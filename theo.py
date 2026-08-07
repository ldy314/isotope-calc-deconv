# -*- coding: utf-8 -*-
"""
theo - 理论同位素分布计算子命令（最小可运行版本）

输入：元素表 (element, isotope_or_natural, count)
输出：丰度最高的前 20 个同位素峰（不足 20 显示全部），按质量升序
      每峰：中性精确质量 | m/z（带电荷）| 相对丰度%（最强峰=100%）

数据源：molmass.ELEMENTS（NIST 同位素质量与天然丰度）
算法：多项式卷积展开（按 0.001 Da 网格分箱合并近简并峰）

用法：
    python theo.py --elements C natural 120 H natural 198 N natural 28 O natural 35 \
                   --z 1 --tol 0.001
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Tuple

import molmass

ELECTRON_MASS = molmass.ELECTRON.mass  # 0.000548579909 u


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class ElementColumn:
    """Excel 中的一列输入：元素 + 同位素选择 + 原子个数"""
    element: str          # 元素符号，如 'C'
    isotope: str          # 'natural' 或质量数，如 '13'
    count: int            # 原子个数

    def __post_init__(self):
        self.element = self.element.strip().capitalize()
        self.isotope = self.isotope.strip()
        if self.count is None or self.count < 0:
            raise ValueError(f"原子个数必须 >= 0: {self.count}")


@dataclass
class IsotopePeak:
    """一个合并后的同位素峰"""
    mass: float           # 中性精确质量（合并组丰度加权平均）
    abundance: float      # 绝对概率（0~1）
    rel_abundance: float  # 相对丰度%（最强峰=100）
    mz: float             # 带电荷 m/z

    def __lt__(self, other: "IsotopePeak"):
        return self.mass < other.mass


# ---------------------------------------------------------------------------
# 核心算法：多项式卷积展开
# ---------------------------------------------------------------------------

def _element_natural_distribution(element: str) -> List[Tuple[float, float]]:
    """取元素的天然同位素分布 [(mass, abundance), ...]，丰度归一化到总和=1"""
    try:
        el = molmass.ELEMENTS[element]
    except KeyError:
        raise ValueError(f"不支持的（或 molmass 未收录的）元素: {element!r}")
    dist = []
    for mass_number in el.isotopes:
        iso = el.isotopes[mass_number]
        dist.append((iso.mass, iso.abundance))
    total = sum(p for _, p in dist)
    return [(m, p / total) for m, p in dist]


def _single_isotope_distribution(element: str, mass_number: str) -> List[Tuple[float, float]]:
    """取指定同位素的单点分布 [(mass, 1.0)]"""
    try:
        el = molmass.ELEMENTS[element]
        iso = el.isotopes[int(mass_number)]
    except (KeyError, ValueError):
        raise ValueError(f"元素 {element!r} 没有质量数 {mass_number!r} 的同位素")
    return [(iso.mass, 1.0)]


def _column_distribution(col: ElementColumn) -> List[Tuple[float, float]]:
    """展开一列的分布：单原子分布卷积 count 次"""
    if col.count == 0:
        return [(0.0, 1.0)]  # 零原子：质量 0，概率 1（中性元素）
    if col.isotope.lower() in ("natural", "自然", "自然分布", ""):
        single = _element_natural_distribution(col.element)
    else:
        single = _single_isotope_distribution(col.element, col.isotope)
    if len(single) == 1:
        return [(single[0][0] * col.count, 1.0)]  # 纯同位素：固定质量，无分布
    # 多项式卷积展开：DP 按 (质量偏移, 概率) 累积
    # 用质量偏移（相对该列最轻同位素质量×count）以避免大数漂移
    base = min(m for m, _ in single) * col.count
    states = {0: 1.0}  # offset -> probability
    for _ in range(col.count):
        new_states = defaultdict(float)
        for off, prob in states.items():
            for m, p in single:
                new_states[off + round((m - base / col.count) * 1e6)] += prob * p
        states = dict(new_states)
    return [(base + off / 1e6, prob) for off, prob in states.items()]


def _merge_by_tolerance(peaks: List[Tuple[float, float]], tol: float) -> List[Tuple[float, float]]:
    """按质量排序后聚类合并：与当前组重心质量差 ≤ tol 则并入，否则开新组。

    返回 [(丰度加权平均质量, 合并概率), ...]
    """
    if not peaks:
        return []
    peaks = sorted(peaks)
    merged: List[Tuple[float, float]] = []
    cur_mass, cur_prob = peaks[0]
    for m, p in peaks[1:]:
        total = cur_prob + p
        if total == 0:
            continue
        if abs(m - cur_mass) <= tol:
            # 并入当前组：更新丰度加权平均质量
            cur_mass = (cur_mass * cur_prob + m * p) / total
            cur_prob = total
        else:
            merged.append((cur_mass, cur_prob))
            cur_mass, cur_prob = m, p
    if cur_prob != 0:
        merged.append((cur_mass, cur_prob))
    return merged


def _convolve_columns(columns: List[ElementColumn], tol: float = 0.001, min_prob: float = 1e-16) -> List[Tuple[float, float]]:
    """逐列卷积所有元素列，每列卷积后按 tol 容差聚类合并近简并峰，并剪除概率 < min_prob 的峰。"""
    dist = [(0.0, 1.0)]
    for col in columns:
        col_dist = _column_distribution(col)
        if len(col_dist) == 1 and col_dist[0][1] == 1.0:
            # 纯同位素列：整体平移质量，不改变分布形状
            dist = [(m1 + col_dist[0][0], p1) for m1, p1 in dist]
            continue
        # 卷积 dist × col_dist
        raw = []
        for m1, p1 in dist:
            for m2, p2 in col_dist:
                p = p1 * p2
                if p >= min_prob:
                    raw.append((m1 + m2, p))
        dist = _merge_by_tolerance(raw, tol)
    return dist


def compute_theoretical_spectrum(
    columns: List[ElementColumn],
    z: int = 1,
    tol: float = 0.001,
    top_n: int = 50,
    min_ab: float = 0.05,
) -> List[IsotopePeak]:
    """计算前 top_n 个同位素峰，按质量升序返回。

    z=0 表示中性分子：m/z 列输出中性质量 M（不除电荷、不加电子质量修正）。
    min_ab: 相对丰度阈值（%，最强峰=100）。相对丰度低于该值的峰不显示（默认 0.05%）。
    """
    if not columns:
        raise ValueError("元素表为空")
    if tol <= 0:
        raise ValueError("质量精度必须 > 0")

    dist = _convolve_columns(columns, tol=tol)
    peaks = []
    for mass, prob in dist:
        if z == 0:
            mz = mass  # 中性分子：m/z = M
        else:
            mz = (mass - z * ELECTRON_MASS) / z
        peaks.append(IsotopePeak(
            mass=mass,
            abundance=prob,
            rel_abundance=0.0,  # 后面归一化
            mz=mz,
        ))
    if not peaks:
        return []
    max_ab = max(p.abundance for p in peaks)
    for p in peaks:
        p.rel_abundance = p.abundance / max_ab * 100.0
    # 过滤相对丰度低于阈值 min_ab% 的峰
    peaks = [p for p in peaks if p.rel_abundance >= min_ab]
    # 取丰度最高的前 top_n
    peaks.sort(key=lambda p: p.abundance, reverse=True)
    peaks = peaks[:top_n]
    # 按质量升序
    peaks.sort()
    return peaks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="theo",
        description="理论同位素分布计算：元素表 → 前50同位素峰（中性质量/m/z/相对丰度）",
    )
    parser.add_argument(
        "--elements", nargs="+", required=True, metavar="E",
        help="元素表：三元素一组 (元素, 同位素, 个数)。同位素用 'natural' 或质量数（如 '13'）。"
             "例：C natural 120 H natural 198 N natural 28 O natural 35 C 13 6",
    )
    parser.add_argument("--z", type=int, default=1, help="电荷数（默认 1）")
    parser.add_argument("--tol", type=float, default=0.001, help="质量精度（峰合并容差 Da，默认 0.001）")
    parser.add_argument("--min-ab", type=float, default=0.05, help="相对丰度阈值（%%，最强峰=100，低于此值不显示，默认 0.05）")
    parser.add_argument("--top", type=int, default=50, help="输出峰数（默认 50）")
    parser.add_argument("--json", action="store_true", help="JSON 输出（供 Excel 回填）")
    parser.add_argument("--out", metavar="FILE", default=None,
                        help="结果文件路径：成功时写 JSON，失败时写同目录 theo_err.txt"
                             "（供 VBA 直接调用，无需 shell 重定向）")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    # Windows 下 stdout 重定向默认 GBK；强制 UTF-8 输出，保证 VBA 读取一致
    if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    if len(args.elements) % 3 != 0:
        _emit_error(args, "错误：--elements 必须按 (元素, 同位素, 个数) 三元组提供")
        return 1
    columns = []
    for i in range(0, len(args.elements), 3):
        el, iso, cnt = args.elements[i], args.elements[i + 1], args.elements[i + 2]
        try:
            columns.append(ElementColumn(el, iso, int(cnt)))
        except ValueError as e:
            _emit_error(args, f"错误：{e}")
            return 1
    try:
        peaks = compute_theoretical_spectrum(columns, z=args.z, tol=args.tol, top_n=args.top, min_ab=args.min_ab)
    except ValueError as e:
        _emit_error(args, f"错误：{e}")
        return 1

    if args.out:
        # --out 模式：成功写 JSON 到指定文件；异常已由 _emit_error 写 theo_err.txt
        _write_json(args, peaks)
        return 0
    if args.json:
        print(_json_text(args, peaks))
        return 0

    print(f"{'#':>3} {'中性精确质量 (u)':>18} {'m/z':>14} {'相对丰度%':>12}")
    print("-" * 52)
    for i, p in enumerate(peaks):
        print(f"{i + 1:>3} {p.mass:>18.6f} {p.mz:>14.6f} {p.rel_abundance:>11.4f}")
    return 0


def _json_text(args, peaks) -> str:
    """生成每峰一行的 JSON 文本（VBA 逐行解析：每行含 rank/mass/mz/abundance）"""
    lines = ['{"z": %d, "tol": %s, "min_ab": %s, "peaks": [' % (args.z, repr(args.tol), repr(args.min_ab))]
    for i, p in enumerate(peaks):
        comma = ',' if i < len(peaks) - 1 else ''
        lines.append(
            '{"rank": %d, "mass": %.9f, "mz": %.9f, "abundance": %.9f}%s'
            % (i + 1, p.mass, p.mz, p.rel_abundance, comma)
        )
    lines.append(']}')
    return '\n'.join(lines)


def _write_json(args, peaks) -> None:
    """把 JSON 写入 --out 指定的文件（UTF-8）"""
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(_json_text(args, peaks))


def _emit_error(args, message: str) -> None:
    """输出错误：--out 模式写入 <out 同目录>/theo_err.txt，否则打印到 stderr"""
    if args.out:
        import os
        err_path = os.path.join(os.path.dirname(os.path.abspath(args.out)), 'theo_err.txt')
        try:
            with open(err_path, 'w', encoding='utf-8') as f:
                f.write(message)
            return
        except OSError:
            pass
    print(message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
