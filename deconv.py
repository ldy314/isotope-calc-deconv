"""deconv.py - 质谱图解卷积：实测谱 → 各同位素取代杂质的相对含量（ADR-0007）

数据流：
  实测高分辨谱 CSV (m/z, intensity)   [profile 或 centroid，自动识别]
  + imp.py 枚举杂质
  + theo.py 生成每个杂质的理论同位素模式（基模式，全模式 min_ab=0）
  + Gaussian 峰形卷积（FWHM ≈ m/RESOLUTION，吸收仪器峰形；30k Q-TOF 下
    ¹³C/¹⁵N 近简并对不可分辨 → 靠整体 NNLS 拟合分辨）
  + scipy NNLS 拟合：谱 ≈ Σ 权重ᵢ × 模式ᵢ (+ 基线列)
  + 质量偏移扫描（ppm 级，吸收质量校准误差）
  → 权重归一化到总和 100% = 各杂质相对含量（组分语义，区别于相对丰度的最强峰=100%）

仪器默认：岛津 LCMS-9030（Q-TOF，分辨率 ~30000 FWHM），目标 1-5 kDa 多肽。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np

import imp
import theo

RESOLUTION_DEFAULT = 30000          # LCMS-9030 Q-TOF 名义分辨率
GAUSS_K = 2.354820045              # FWHM → sigma 换算系数（2√(2ln2)）
PROFILE_DENSITY_THRESHOLD = 50.0   # 点/Da，超过视为 profile（密集连续谱）


# ---------------------------------------------------------------------------
# 谱图读取
# ---------------------------------------------------------------------------

def load_spectrum(path: str) -> Tuple[np.ndarray, np.ndarray, str]:
    """读 CSV (m/z, intensity)。支持表头、注释行、制表符/逗号分隔。

    返回 (mz, intensity, mode)；mode 由采样密度自动识别：
      profile：密集连续谱（> 50 点/Da）
      centroid：稀疏峰列表（峰 + 强度）
    """
    mz_list: List[float] = []
    int_list: List[float] = []
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(('#', '//', ';')):
                continue
            parts = line.replace(',', '\t').split('\t')
            parts = [p for p in parts if p != '']
            if len(parts) < 2:
                continue
            try:
                m = float(parts[0])
                i = float(parts[1])
            except ValueError:
                continue  # 跳过表头行
            if i > 0:
                mz_list.append(m)
                int_list.append(i)
    if len(mz_list) < 3:
        raise ValueError(f"谱图有效点数不足: {len(mz_list)}（需 CSV 两列: m/z, intensity）")
    mz = np.asarray(mz_list, dtype=float)
    intensity = np.asarray(int_list, dtype=float)
    order = np.argsort(mz)
    mz, intensity = mz[order], intensity[order]
    mode = detect_mode(mz)
    return mz, intensity, mode


def detect_mode(mz: np.ndarray) -> str:
    """按采样密度与步长区分 profile / centroid。

    判据：
      1) 点密度 > 50 点/Da → profile
      2) 中位相邻步长 < 0.01 Da → profile（对强度过滤后点数缩水的谱仍稳健）
      否则 → centroid
    """
    if len(mz) < 3:
        return 'centroid'
    span = float(mz[-1] - mz[0])
    if span > 0 and len(mz) / span > PROFILE_DENSITY_THRESHOLD:
        return 'profile'
    steps = np.diff(mz)
    steps = steps[steps > 0]
    if len(steps):
        median_step = float(np.median(steps))
        if median_step < 0.01:
            return 'profile'
    return 'centroid'


# ---------------------------------------------------------------------------
# 基模式构建（理论模式 + 仪器峰形卷积）
# ---------------------------------------------------------------------------

def _species_stick(impurity: dict, z: int, tol: float, top_n: int) -> List[Tuple[float, float]]:
    """一个杂质的理论同位素模式（棒状）：[(m/z, 绝对概率), ...]

    用 theo 引擎全模式（min_ab=0），再取丰度前 top_n，按质量升序。
    """
    theo_cols = [
        theo.ElementColumn(e, iso, c)
        for e, iso, c in zip(impurity['elements'], impurity['isos'], impurity['counts'])
    ]
    peaks = theo.compute_theoretical_spectrum(
        theo_cols, z=z, tol=tol, top_n=top_n, min_ab=0.0,
    )
    return [(p.mz, p.abundance) for p in peaks]


def _convolve_gaussian(stick: List[Tuple[float, float]], at_mz: np.ndarray,
                       resolution: float) -> np.ndarray:
    """把棒状模式按仪器峰形（Gaussian，FWHM = m/resolution）卷积，在 at_mz 处采样。"""
    out = np.zeros(len(at_mz), dtype=float)
    for m, ab in stick:
        sigma = m / resolution / GAUSS_K
        if sigma <= 0:
            out += ab * (at_mz == m)
            continue
        out += ab * np.exp(-0.5 * ((at_mz - m) / sigma) ** 2)
    return out


def build_design_matrix(fit_mz: np.ndarray, sticks: List[List[Tuple[float, float]]],
                        resolution: float) -> np.ndarray:
    """NNLS 设计矩阵 A：(n_fit × n_species)。

    每列 = 该杂质模式卷积后在 fit_mz 处的取值，**列归一化到总和=1**
    （使 NNLS 权重直接 = 相对摩尔量/相对含量，不随物种峰数/丰度缩放）。
    """
    n = len(fit_mz)
    n_species = len(sticks)
    A = np.zeros((n, n_species), dtype=float)
    for j, stick in enumerate(sticks):
        col = _convolve_gaussian(stick, fit_mz, resolution)
        s = float(col.sum())
        if s > 0:
            A[:, j] = col / s
    return A


def auto_window(sticks: List[List[Tuple[float, float]]],
                resolution: float, margin_extra: float = 0.5) -> Tuple[float, float]:
    """拟合窗口 = 全部杂质理论峰质量范围 + 边距（±max(0.5, 10σ) Da）"""
    lo, hi = float('inf'), float('-inf')
    for stick in sticks:
        for m, _ in stick:
            lo = min(lo, m)
            hi = max(hi, m)
    if lo == float('inf'):
        raise ValueError("杂质模式为空")
    sigma = hi / resolution / GAUSS_K
    margin = max(margin_extra, 10.0 * sigma)
    return lo - margin, hi + margin


# ---------------------------------------------------------------------------
# NNLS 拟合 + 质量偏移扫描
# ---------------------------------------------------------------------------

def fit_once(fit_mz: np.ndarray, y: np.ndarray, sticks: List[List[Tuple[float, float]]],
             resolution: float, with_baseline: bool = True
             ) -> Tuple[np.ndarray, float, np.ndarray, Optional[float]]:
    """单次 NNLS。返回 (物种权重, 残差平方和, 设计矩阵A, 基线权重或None)。"""
    A = build_design_matrix(fit_mz, sticks, resolution)
    if with_baseline:
        A = np.hstack([A, np.ones((len(fit_mz), 1))])
    from scipy.optimize import nnls
    x, rnorm = nnls(A, y)
    n_species = len(sticks)
    w = x[:n_species]
    baseline = float(x[n_species]) if with_baseline else None
    return w, float(rnorm ** 2), A, baseline


# ---------------------------------------------------------------------------
# 质量简并类合并（不可分辨物种自动合并，CONTEXT.md 术语）
# ---------------------------------------------------------------------------

PROXIMITY_FACTOR_DEFAULT = 1.0  # 单同位素 m/z 间隔 > factor×FWHM 才分新类


def _monoisotopic_mz(stick: List[Tuple[float, float]]) -> float:
    """物种的单同位素 m/z（棒状模式最轻峰）"""
    return min(m for m, _ in stick)


def merge_by_proximity(mono: List[float], da: float) -> List[List[int]]:
    """按单同位素 m/z 邻近合并（名义质量类，链式聚类）。

    mono 升序排列后，与上一成员间隔 > da 才开新类。

    物理依据（LCMS-9030 30k / 1-5 kDa）：D/¹³C/¹⁵N 近简并对
    （Δm≈0.003 Da << 峰宽 0.015-0.17 Da，仅 0.1-0.2 FWHM）低于分辨极限
    （Rayleigh 需 ~1 FWHM），无噪声时 NNLS 的"分开"是数值巧合，
    真实噪声下不可靠 → 必须合并为质量简并类。
    """
    n = len(mono)
    order = sorted(range(n), key=lambda i: mono[i])
    groups: List[List[int]] = []
    cur = [order[0]]
    for i in order[1:]:
        if mono[i] - mono[cur[-1]] > da:
            groups.append(cur)
            cur = [i]
        else:
            cur.append(i)
    groups.append(cur)
    return groups


def build_merged_matrix(A_all: np.ndarray, groups: List[List[int]]) -> np.ndarray:
    """由全物种设计矩阵 A_all 生成合并后的设计矩阵（每组=成员列之和，再列归一化）。"""
    A = np.zeros((A_all.shape[0], len(groups)), dtype=float)
    for g, members in enumerate(groups):
        col = A_all[:, members].sum(axis=1)
        s = float(col.sum())
        if s > 0:
            A[:, g] = col / s
    return A


def _window_mask(mz: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return (mz >= lo) & (mz <= hi)


def deconvolve(columns: List[imp.ElementColumn], spectrum_path: str,
               z: int = 1, tol: float = 0.001, top_n: int = 500,
               resolution: float = RESOLUTION_DEFAULT,
               mode: str = 'auto', ppm_range: float = 10.0, ppm_step: float = 1.0,
               with_baseline: bool = True, merge_degenerate: bool = True,
               proximity_factor: float = PROXIMITY_FACTOR_DEFAULT) -> dict:
    """完整解卷积主流程。返回结果 dict（含物种/质量简并类相对含量、元信息）。"""
    # 1) 谱图
    mz, intensity, auto_mode = load_spectrum(spectrum_path)
    if mode == 'auto':
        mode = auto_mode

    # 2) 枚举杂质 + 主成分（输入分子本身），基模式（棒状）
    impurities = imp.enumerate_impurities(columns, z=z)
    if not impurities:
        raise ValueError("无修饰同位素，无杂质可解卷积")
    # 主成分（输入分子）也作为基函数参与 NNLS 拟合：否则全标记峰簇
    # （m/z 最高，如 SPH20291 6¹³C+2¹⁵N 的 [M+2H]²⁺≈1416.7）无法被任何
    # 杂质模式解释，解卷积会把其强度错误分摊到邻近物种。
    input_spec = {
        'name': imp.formula_name([(c.element, c.isotope, c.count) for c in columns], z),
        'elements': [c.element for c in columns],
        'isos': [c.isotope for c in columns],
        'counts': [c.count for c in columns],
    }
    all_specs = [input_spec] + impurities
    sticks = [_species_stick(r, z, tol, top_n) for r in all_specs]

    # 3) 拟合窗口裁剪
    lo, hi = auto_window(sticks, resolution)
    mask = _window_mask(mz, lo, hi)
    fit_mz = mz[mask]
    y = intensity[mask]
    if len(fit_mz) < 10:
        raise ValueError(f"拟合窗口内点数不足: {len(fit_mz)}（窗口 {lo:.2f}-{hi:.2f} Da）")

    # 4) 质量偏移扫描：谱图 m/z 视为 mz_true*(1+ppm*1e-6)，在 mz_true 处评估基模式
    offsets = np.arange(-ppm_range, ppm_range + 1e-9, ppm_step)
    best = None  # (rss, ppm, w)
    for ppm in offsets:
        shift = 1.0 + float(ppm) * 1e-6
        w, rss, A, _ = fit_once(fit_mz / shift, y, sticks, resolution, with_baseline)
        if best is None or rss < best[0]:
            best = (rss, float(ppm), w)
    rss, best_ppm, _ = best
    fit_true = fit_mz / (1.0 + best_ppm * 1e-6)

    # 5) 质量简并类合并：按单同位素 m/z 邻近合并（名义质量类）。
    #    近简并对（D/¹³C/¹⁵N，Δm≈0.003 Da）在 30k / 1-5 kDa 下低于分辨极限
    #    （仅 0.1-0.2 FWHM），无论 profile/centroid 都不可靠 → 统一合并。
    #    profile 密集数据仍可用 --proximity-factor 调小来尝试细分。
    A_all = build_design_matrix(fit_true, sticks, resolution)
    if merge_degenerate:
        mono = [_monoisotopic_mz(s) for s in sticks]
        med = float(np.median(mono))
        da = proximity_factor * med / resolution
        groups = merge_by_proximity(mono, da)
    else:
        groups = [[i] for i in range(len(sticks))]
    A_merged = build_merged_matrix(A_all, groups)
    if with_baseline:
        A_fit = np.hstack([A_merged, np.ones((len(fit_true), 1))])
    else:
        A_fit = A_merged
    from scipy.optimize import nnls
    x, rnorm = nnls(A_fit, y)
    group_w = x[:len(groups)]
    baseline = float(x[len(groups)]) if with_baseline else None
    rss = float(rnorm ** 2)

    # 6) 归一化到相对含量（总和=100%，组分语义）
    total = float(group_w.sum())
    group_rel = np.zeros_like(group_w) if total <= 0 else 100.0 * group_w / total

    # 7) 组装结果（物种级展开 + 组级汇总）
    group_of = {}
    for g, members in enumerate(groups):
        for m in members:
            group_of[m] = g
    species = []
    for j, r in enumerate(all_specs):
        g = group_of[j]
        species.append({
            'rank': j + 1,
            'name': r['name'],
            'elements': r['elements'],
            'isos': r['isos'],
            'counts': r['counts'],
            'group': g,
            'merged': len(groups[g]) > 1,
            'is_target': j == 0,
            'relative_content': float(group_rel[g]),
            'weight': float(group_w[g]),
        })
    groups_out = []
    for g, members in enumerate(groups):
        groups_out.append({
            'group': g,
            'members': [m + 1 for m in members],          # 物种 rank（1-based）
            'n_members': len(members),
            'relative_content': float(group_rel[g]),
            'weight': float(group_w[g]),
        })
    result = {
        'spectrum': spectrum_path,
        'mode': mode,
        'resolution': resolution,
        'z': z,
        'tol': tol,
        'top_n': top_n,
        'mass_offset_ppm': best_ppm,
        'baseline_weight': baseline,
        'residual_rss': rss,
        'n_species': len(species),
        'target_rank': 1,
        'n_groups': len(groups),
        'window': [lo, hi],
        'species': species,
        'groups': groups_out,
    }
    return result


def _format_table(result: dict) -> str:
    lines = [f"解卷积结果（mode={result['mode']}, res={result['resolution']}, "
             f"offset={result['mass_offset_ppm']:+.1f} ppm, "
             f"RSS={result['residual_rss']:.3e}, 物种={result['n_species']}, "
             f"质量简并类={result['n_groups']}）"]
    lines.append(f"{'#':>3}  {'杂质':<18} {'相对含量%':>10} {'权重':>10}  备注")
    for s in result['species']:
        if s['merged']:
            members = [result['species'][m - 1]['name']
                       for m in result['groups'][s['group']]['members'] if m != s['rank']]
            note = '与 ' + '、'.join(members) + ' 简并'
        else:
            note = ""
        if s.get('is_target'):
            note = ('[主成分]' + ('' if not note else '；' + note))
        lines.append(f"{s['rank']:>3}  {s['name']:<18} {s['relative_content']:>10.3f} {s['weight']:>10.5f}  {note}")
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_elements(tokens: List[str]) -> List[imp.ElementColumn]:
    if len(tokens) % 3 != 0:
        raise ValueError("元素表需三元素一组 (元素, 同位素, 个数)")
    cols = []
    for i in range(0, len(tokens), 3):
        cols.append(imp.ElementColumn(tokens[i], tokens[i + 1], int(tokens[i + 2])))
    return cols


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog='deconv',
        description='质谱图解卷积：实测谱 → 各同位素取代杂质的相对含量（NNLS 模式拟合）',
    )
    p.add_argument('--elements', nargs='+', required=True, metavar='E',
                   help='元素表：三元素一组 (元素, 同位素, 个数)。例：C natural 1 H natural 2 H 2 3')
    p.add_argument('--spectrum', required=True, metavar='CSV', help='实测谱 CSV（m/z, intensity 两列）')
    p.add_argument('--z', type=int, default=1, help='电荷数（默认 1；0=中性）')
    p.add_argument('--tol', type=float, default=0.001, help='质量精度（峰合并容差 Da，默认 0.001）')
    p.add_argument('--top', type=int, default=500, help='每个杂质基模式最大峰数（默认 500，全模式）')
    p.add_argument('--resolution', type=float, default=RESOLUTION_DEFAULT,
                   help=f'仪器分辨率 FWHM（默认 {RESOLUTION_DEFAULT}，LCMS-9030）')
    p.add_argument('--mode', choices=['auto', 'profile', 'centroid'], default='auto',
                   help='谱图模式（默认 auto 按采样密度识别）')
    p.add_argument('--ppm', type=float, default=10.0, help='质量偏移扫描范围 ±ppm（默认 10）')
    p.add_argument('--ppm-step', type=float, default=1.0, help='质量偏移扫描步长 ppm（默认 1）')
    p.add_argument('--no-baseline', action='store_true', help='不加常数基线列（默认加）')
    p.add_argument('--no-merge', action='store_true',
                   help='不合并质量简并类（默认自动合并不可分辨物种）')
    p.add_argument('--proximity-factor', type=float, default=PROXIMITY_FACTOR_DEFAULT,
                   help='质量简并合并因子：单同位素 m/z 间隔 > factor×FWHM 才分新类'
                        '（默认 1.0；调小如 0.3 可尝试细分近简并，但噪声下不可靠）')
    p.add_argument('--out', metavar='JSON', default=None, help='结果 JSON 输出路径')
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        columns = _parse_elements(args.elements)
        result = deconvolve(
            columns, args.spectrum,
            z=args.z, tol=args.tol, top_n=args.top,
            resolution=args.resolution, mode=args.mode,
            ppm_range=args.ppm, ppm_step=args.ppm_step,
            with_baseline=not args.no_baseline,
            merge_degenerate=not args.no_merge,
            proximity_factor=args.proximity_factor,
        )
    except Exception as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1

    print(_format_table(result))
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
