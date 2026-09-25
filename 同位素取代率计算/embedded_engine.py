# -*- coding: utf-8 -*-
# =============================================================================
# 同位素取代率（富集度）计算引擎 —— 自包含版，供 .xlsm 内嵌调用
#
# 适用数据：岛津 QTOF .lcd（QTFL RawData / Centroid Data 质心谱）
# 方法：质心法（ADR-0007）+ 包络序号法（独立交叉验证）；见「方法说明」表
#
# 输入：xlsm 的「输入」表，单元格约定（与 build_xlsm.py 严格一致）
#   B5  天然形分子式        B6  全标记形分子式      B7  标记原子总数(可空)
#   B8  计算电荷态           B10 数据目录            B11/D11 RT 窗口起/止(秒)
#   B13 质量轴 a            B14 质量轴 c            B15 自动重标定(是/否)
#   B16 天然对照样品名      B19:C38 样品表(名/文件/备注)   B40 Python 解释器
#
# 输出：%TEMP% 下若干 CSV（无 BOM、字段内不含逗号），供 VBA 回填工作表
#   <base>_overview.csv   总览
#   <base>_detail.csv     取代率明细
#   <base>_dist.csv       档分布
#   <base>_calib.csv      标定与诊断
#   <base>_scalars.txt    状态/参数摘要
#
# 关键点（踩坑史，勿改）
#   1) .lcd 存储值 x 与 m/z 满足 m/z = a·x^2 + c。openszraw 文档的 mz=x/1e12 不适用。
#      只用 a·x^2 会留 ±90~220 ppm 残差 → 伪装成 ~+5% 假富集。
#   2) 质心法窗口必须完整覆盖天然形与全标记形两套包络：[M0_nat-0.5u, M0_lab+8u]。
#   3) 窗内以质心噪声峰为主（z=2 占 ~70%）→ 先减 p25 基座，再 0.5%·max 阈值。
#   4) 中间 CSV 的文件名基名用独立变量 fname；基线值用 bl。
#      （历史上 base 一名两用导致 f"{base}_x.csv" 变成 "0.0_x.csv"）
# =============================================================================
from __future__ import annotations

import csv
import math
import os
import re
import struct
import sys
import tempfile
from collections import OrderedDict

import numpy as np

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
U13C = 1.0033548              # 13C 相对 12C 的中性质量增量（一个同位素间距）
PROTON = 1.007276466812       # m(1H) - m(e)
ELECTRON = 0.000548579909

A_DEFAULT = 4.53758732e-04    # 本批岛津 QTOF .lcd 实测（m/z = a·x^2 + c）
C_DEFAULT = -0.44325

BASE_PCT = 25.0               # 质心法基线分位数
THR_FRAC = 0.005              # 峰阈值（窗内最大强度比例）
WIN_LO_U = 0.5                # 窗口左端 = M0_nat - 0.5u
WIN_HI_U = 8.0                # 窗口右端 = M0_lab + 8u
BIN_X = 1.0e-3                # x 轴分箱宽度
ENV_HALF_U = 0.2              # 包络逐峰提取半宽（以 u 为单位）
LABELED_SHIFT = 8             # 天然形 ↔ 全标记形 之间的同位素序号位移（= 标记原子数）

# --- 自动重标定的采纳门槛（2026-09-25 加；此前无校验，锚点同源时会把质量轴算塌）---
MIN_ANCHOR_SPAN = 0.05        # 锚点理论质量的最小跨度（Da）
                              # 若全部锚点来自同一参照形、同一电荷态，理论 y 值全同
                              # → 设计矩阵 [x², 1] 两列不可分辨 → lstsq 退化解
                              # （实测得 a≈1.16e-16、c≈942.475，质量轴塌缩成单点）
MIN_CAL_A = 1.0e-8            # 标定 a 的下限：必须为正且非退化（正常值 ≈4.54e-4）
MAX_CAL_REL = 2.0e-4          # 标定残差上限（200 ppm）：实测正常残差仅 ±10 ppm，
                              # 而锚点误配（错认同位素峰）至少偏 1 个同位素间距
                              # （z=3 → 0.334 Da @942 ≈ 355 ppm；z=4 ≈ 500 ppm）
                              # → 200 ppm 可拦下误配，同时留 20× 正常余量

NAT_REF_DEFAULT = "STD_002,STD_003,STD_005"


# ===========================================================================
# 1. 分子式解析（支持 C134H198N28O35S2 / C128[13C]6H198N26[15N]2O35S2 / D / (CH2)5）
# ===========================================================================
_SPECIAL = {"D": ("H", "2"), "T": ("H", "3")}
_TOKEN = re.compile(
    r"""
    \s*
    (?:
        [\[(](?P<iso_mass>\d+)(?P<iso_el>[A-Z][a-z]?)[\])](?P<iso_n>\d*)
      | (?P<open>\()
      | (?P<close>\))(?P<grp_n>\d*)
      | (?P<el>[A-Z][a-z]?)(?P<n>\d*)
    )
    """,
    re.VERBOSE,
)


class FormulaError(ValueError):
    pass


def is_nat(iso):
    return str(iso).strip().lower() in ("natural", "自然", "自然分布", "")


def parse_formula(s):
    """-> OrderedDict[(element, isotope)] = count，保持首次出现顺序。"""
    if not s or not s.strip():
        raise FormulaError("分子式为空")
    text = s.replace("·", "").replace(" ", "")
    text = re.sub(r"\d*[+-]$", "", text)
    acc = OrderedDict()
    stack = [acc]
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise FormulaError("无法解析的片段：%r（位置 %d）" % (text[pos:pos + 12], pos))
        pos = m.end()
        if m.group("open"):
            stack.append(OrderedDict())
            continue
        if m.group("close"):
            if len(stack) == 1:
                raise FormulaError("括号不匹配：多余的 ')'")
            grp = stack.pop()
            mult = int(m.group("grp_n") or 1)
            tgt = stack[-1]
            for k, v in grp.items():
                tgt[k] = tgt.get(k, 0) + v * mult
            continue
        cur = stack[-1]
        if m.group("iso_el"):
            el = m.group("iso_el").capitalize()
            iso = m.group("iso_mass")
            n = int(m.group("iso_n") or 1)
        else:
            raw = m.group("el")
            n = int(m.group("n") or 1)
            if raw in _SPECIAL:
                el, iso = _SPECIAL[raw]
            else:
                el, iso = raw.capitalize(), "natural"
        if n == 0:
            continue
        cur[(el, iso)] = cur.get((el, iso), 0) + n
    if len(stack) != 1:
        raise FormulaError("括号不匹配：缺少 ')'")
    if not acc:
        raise FormulaError("未解析出任何元素：%r" % s)
    return acc


def cols_of(formula):
    """-> [(element, isotope, count), ...]"""
    return [(e, i, c) for (e, i), c in parse_formula(formula).items()]


def labeled_atom_count(cols):
    return sum(c for e, i, c in cols if not is_nat(i))


# ===========================================================================
# 2. 理论同位素包络（多项式卷积）
# ===========================================================================
_MASS_CACHE = {}


def _molmass_elements():
    import molmass
    return molmass.ELEMENTS


def _elem_nat(element):
    key = ("nat", element)
    if key in _MASS_CACHE:
        return _MASS_CACHE[key]
    el = _molmass_elements()[element]
    d = sorted((el.isotopes[mn].mass, el.isotopes[mn].abundance)
               for mn in el.isotopes)
    tot = sum(p for _, p in d) or 1.0
    out = [(m, p / tot) for m, p in d]
    _MASS_CACHE[key] = out
    return out


def _single_mass(element, iso):
    key = ("one", element, str(iso))
    if key in _MASS_CACHE:
        return _MASS_CACHE[key]
    el = _molmass_elements()[element]
    m = el.isotopes[int(iso)].mass
    _MASS_CACHE[key] = m
    return m


def _mono_mass(element):
    key = ("mono", element)
    if key in _MASS_CACHE:
        return _MASS_CACHE[key]
    m = min(_elem_nat(element))[0]
    _MASS_CACHE[key] = m
    return m


def _col_dist(col):
    el, iso, cnt = col
    cnt = int(cnt)
    if cnt <= 0:
        return [(0.0, 1.0)]
    if is_nat(iso):
        single = _elem_nat(el)
    else:
        single = [(_single_mass(el, iso), 1.0)]
    if len(single) == 1:
        return [(single[0][0] * cnt, 1.0)]
    base_m = min(m for m, _ in single)
    states = {0: 1.0}
    for _ in range(cnt):
        ns = {}
        for off, p in states.items():
            for m, p2 in single:
                k = off + int(round((m - base_m) * 1e6))
                ns[k] = ns.get(k, 0.0) + p * p2
        states = ns
    return [(base_m * cnt + off / 1e6, p) for off, p in sorted(states.items())]


def _merge_tol(peaks, tol):
    if not peaks:
        return []
    peaks = sorted(peaks)
    out = []
    cm, cp = peaks[0]
    for m, p in peaks[1:]:
        t = cp + p
        if t <= 0:
            continue
        if abs(m - cm) <= tol:
            cm = (cm * cp + m * p) / t
            cp = t
        else:
            out.append((cm, cp))
            cm, cp = m, p
    if cp > 0:
        out.append((cm, cp))
    return out


def envelope(cols, tol=1e-3, min_prob=1e-16):
    """-> [(neutral_mass, probability)]，概率已归一化。"""
    dist = [(0.0, 1.0)]
    for col in cols:
        cd = _col_dist(col)
        if len(cd) == 1 and cd[0][1] == 1.0:
            dist = [(m + cd[0][0], p) for m, p in dist]
            continue
        raw = []
        for m1, p1 in dist:
            for m2, p2 in cd:
                p = p1 * p2
                if p >= min_prob:
                    raw.append((m1 + m2, p))
        dist = _merge_tol(raw, tol)
    tot = sum(p for _, p in dist) or 1.0
    return [(m, p / tot) for m, p in dist]


def peaks_mz(cols, z):
    """理论同位素峰 [(m/z, 相对丰度 0..1)]，按 m/z 升序。"""
    dist = envelope(cols)
    out = []
    for m, p in dist:
        mz = (m + z * (PROTON - ELECTRON)) / z if z else m
        out.append((mz, p))
    out.sort()
    return out


def centroid_mz(cols, z):
    """理论包络的强度加权质心 (m/z)。"""
    pk = peaks_mz(cols, z)
    tot = sum(p for _, p in pk)
    return sum(p * mz for mz, p in pk) / tot


def mono_mz(cols, z):
    """单同位素 (m/z)。"""
    m = 0.0
    for e, i, c in cols:
        m += int(c) * (_mono_mass(e) if is_nat(i) else _single_mass(e, i))
    return (m + z * (PROTON - ELECTRON)) / z


def apex_mz(cols, z):
    """基峰 (m/z)：包络中相对丰度最高者。"""
    pk = peaks_mz(cols, z)
    return max(pk, key=lambda t: t[1])[0]


# ===========================================================================
# 3. .lcd（岛津 QTOF）读取
# ===========================================================================
def read_qtof(path):
    """-> [(rt_sec, x_array, intensity_array)]；x 为文件内存储的质量轴值（未换算）。"""
    import olefile
    ole = olefile.OleFileIO(path)
    try:
        idx = ole.openstream("QTFL RawData/Centroid Index").read()
        dat = ole.openstream("QTFL RawData/Centroid Data").read()
    finally:
        ole.close()
    n = len(idx) // 24
    offs = [struct.unpack_from("<Q", idx, i * 24)[0] for i in range(n)]
    out = []
    for i in range(n):
        o = offs[i]
        end = offs[i + 1] if i + 1 < n else len(dat)
        blk = dat[o:end]
        if len(blk) < 72:
            continue
        rt = struct.unpack_from("<I", blk, 4)[0] / 1000.0
        pay = struct.unpack_from("<I", blk, 24)[0]
        w = struct.unpack_from("<I", blk, 36)[0]
        if w not in (1, 2, 4):
            w = 2
        N = pay // (8 + w)
        if N <= 0:
            continue
        x = np.array([struct.unpack_from("<Q", blk, 72 + k * 8)[0] / 1e12
                      for k in range(N)], dtype=np.float64)
        fmt = {1: "<B", 2: "<H", 4: "<I"}[w]
        it = np.array([struct.unpack_from(fmt, blk, 72 + N * 8 + k * w)[0]
                       for k in range(N)], dtype=np.float64)
        out.append((rt, x, it))
    return out


def window_spectrum(scans, rt_lo, rt_hi, bin_x=BIN_X):
    """把 RT 窗口内的质心谱按 x 分箱累加。-> (xs, intensities)"""
    bins = {}
    nscan = 0
    for rt, x, it in scans:
        if not (rt_lo <= rt <= rt_hi):
            continue
        nscan += 1
        key = np.round(x / bin_x).astype(np.int64)
        for k, v in zip(key, it):
            bins[int(k)] = bins.get(int(k), 0.0) + float(v)
    if not bins:
        return np.array([]), np.array([]), 0
    ks = np.array(sorted(bins), dtype=np.int64)
    return ks * bin_x, np.array([bins[int(k)] for k in ks], dtype=np.float64), nscan


# ===========================================================================
# 4. 质量轴标定  m/z = a·x^2 + c
# ===========================================================================
def _x_from_mz(mz, a, c):
    v = (mz - c) / a
    return math.sqrt(v) if v > 0 else 0.0


def _apex_x(xs, it, xc, dmz, a):
    """在 x ∈ xc ± dx（dx 对应 ±dmz Da）内找最强峰，返回其 x。"""
    if xc <= 0 or a <= 0:
        return None
    dx = dmz / (2.0 * a * xc)
    s = (xs >= xc - dx) & (xs <= xc + dx)
    if not s.any():
        return None
    sub_x, sub_i = xs[s], it[s]
    return float(sub_x[int(np.argmax(sub_i))])


def auto_calibrate(spectra, z_list, apex_theo, a0, a_c_default_c):
    """
    用实测基峰重新标定 m/z = a·x^2 + c。
    apex_theo[(which, z)] = 该参照形在该电荷态的基峰理论 m/z（which ∈ {'nat','lab'}）
    对每个样品：比较其"天然形位置"与"全标记形位置"的峰强度，只采用明显占优的一方
    （天然对照样品自然给 nat 锚点，全标记样品给 lab 锚点，混合样品被弃用）。
    -> (a, c, anchors, note)

    三道采纳门槛（任一不过 → 返回 anchors=[] 与原因 note，调用方须沿用输入表 a/c）：
      ① 锚点理论质量跨度 ≥ MIN_ANCHOR_SPAN —— 防「锚点同源」导致的退化拟合
      ② a > MIN_CAL_A 且 a/c 有限 —— 防质量轴塌缩
      ③ 残差 ≤ MAX_CAL_REL —— 防锚点误配
    ⚠ 历史事故：无门槛时，窄 RT 窗下只有 z=3 找到 3 个同源锚点（理论值全同），
      lstsq 退化得 a≈1.16e-16、c≈942.475 → 质量轴塌缩成单点 → 全部取代率算成 0。
    """
    a, c = a0, a_c_default_c
    anchors, note = [], ""
    for _ in range(4):
        pts = []
        for tag, (xs, it, _ns) in spectra.items():
            if len(xs) == 0:
                continue
            for z in z_list:
                got = {}
                for which in ("nat", "lab"):
                    d = apex_theo.get((which, z))
                    if d is None:
                        continue
                    xc = _x_from_mz(d, a, c)
                    xo = _apex_x(xs, it, xc, 0.5, a)
                    if xo is None:
                        continue
                    s = np.abs(xs - xo) <= 1e-9
                    got[which] = (xo, float(it[s].max()) if s.any() else 0.0)
                if len(got) < 2:
                    continue
                hi = max(got, key=lambda k: got[k][1])
                lo = "lab" if hi == "nat" else "nat"
                if got[hi][1] >= 5.0 * max(got[lo][1], 1.0):
                    pts.append((got[hi][0], apex_theo[(hi, z)]))
        if len(pts) < 3:
            note = "锚点不足（%d 个），沿用输入表 a/c" % len(pts)
            break
        xs2 = np.array([p[0] ** 2 for p in pts])
        ys = np.array([p[1] for p in pts])

        # 校验①：理论锚点质量必须跨 ≥ MIN_ANCHOR_SPAN Da。
        # 若锚点全部同源（同一参照形 + 同一电荷态），y 值全同 → 设计矩阵 [x², 1]
        # 两列不可分辨 → lstsq 给出退化解（a→0 且 c→该定值），质量轴塌缩。
        span = float(ys.max() - ys.min())
        if span < MIN_ANCHOR_SPAN:
            note = ("锚点理论质量无跨度（%.4f–%.4f，仅 %.5f Da < %.2f Da）：锚点同源，"
                    "x² 与常数项不可分辨，最小二乘退化 → 沿用输入表 a/c"
                    % (ys.min(), ys.max(), span, MIN_ANCHOR_SPAN))
            break

        A = np.c_[xs2, np.ones_like(xs2)]
        sol, *_ = np.linalg.lstsq(A, ys, rcond=None)
        a_new, c_new = float(sol[0]), float(sol[1])
        # 外点剔除（残差 > 1000 ppm）
        pred = a_new * xs2 + c_new
        keep = np.abs(pred - ys) / ys < 1e-3
        if keep.sum() >= 3 and keep.sum() < len(pts):
            xs2f, ysf = xs2[keep], ys[keep]
            Af = np.c_[xs2f, np.ones_like(xs2f)]
            sol, *_ = np.linalg.lstsq(Af, ysf, rcond=None)
            a_new, c_new = float(sol[0]), float(sol[1])
        else:
            xs2f, ysf = xs2, ys          # 未触发剔除：拟合即用全集

        # 校验②：a 必须为正、有限、非退化（正常值 ≈ 4.54e-4，本机 QTOF）
        if not (math.isfinite(a_new) and math.isfinite(c_new) and a_new > MIN_CAL_A):
            note = ("拟合退化（a=%.6e，c=%.5f；要求 a>%.1e 且有限）：沿用输入表 a/c"
                    % (a_new, c_new, MIN_CAL_A))
            break

        # 校验③：残差按「实际参与拟合的锚点」评估，须在 MAX_CAL_REL 内
        rel = np.abs(a_new * xs2f + c_new - ysf) / ysf
        if float(rel.max()) > MAX_CAL_REL:
            note = ("拟合残差过大（最大 %.0f ppm > %.0f ppm，用 %d 个锚点）：沿用输入表 a/c"
                    % (float(rel.max()) * 1e6, MAX_CAL_REL * 1e6, len(ysf)))
            break

        # 通过全部门槛 → 登记锚点（收敛也要登记，否则诊断误报 n_anchor=0）
        anchors = pts
        if abs(a_new - a) <= 1e-12 and abs(c_new - c) <= 1e-9:
            a, c = a_new, c_new
            break
        a, c = a_new, c_new
    resid = []
    for x, y in anchors:
        yf = a * x * x + c
        resid.append((x, y, yf, (yf - y) / y * 1e6))
    return a, c, resid, note


# ===========================================================================
# 5. 估计量
# ===========================================================================
def centroid_cal(m, v, lo, hi, base_pct=BASE_PCT, thr=THR_FRAC):
    """扣基座 + 阈值后的强度加权质心。-> (质心 or None, 用峰数)"""
    s = (m >= lo) & (m <= hi)
    if not s.any():
        return None, 0
    mm = m[s]
    vv = v[s].astype(float)
    bl = float(np.percentile(vv, base_pct))
    vv = np.clip(vv - bl, 0.0, None)
    if thr > 0 and vv.max() > 0:
        vv = np.where(vv >= thr * vv.max(), vv, 0.0)
    n = int((vv > 0).sum())
    if vv.sum() <= 0:
        return None, n
    return float(np.sum(vv * mm) / vv.sum()), n


def envelope_peaks(m, v, m0n, u, kmax=24, half_u=ENV_HALF_U):
    """按同位素序号 j 取 m = m0n + j·u 处的峰强度（±half_u·u 内最大值）。"""
    out = []
    for j in range(0, kmax + 1):
        cc = m0n + j * u
        s = (m >= cc - half_u * u) & (m <= cc + half_u * u)
        out.append(float(v[s].max()) if s.any() else 0.0)
    return np.array(out)


def neighbor_analysis(I, j0, floor, n_label=LABELED_SHIFT):
    """
    包络序号法（独立于质心）。
    I[j] = 同位素序号 j（= M0_nat + j·u）处的实测峰强度；
    j0   = 主包络起点序号（0 → 主形为天然形；n_label → 主形为全标记形）。
    -> dict(comp, avg, main_frac, j0, ...)
    """
    mx = float(I.max())
    if mx <= 0 or j0 + 1 >= len(I):
        return None
    w = I[j0:j0 + n_label] / mx
    if w[0] <= 0:
        return None
    ks = [k for k in range(1, 7) if j0 + k < len(I) and I[j0 + k] > 0 and k < len(w)]
    if not ks:
        return None
    den = sum(w[k] ** 2 for k in ks)
    A0 = sum(w[k] * I[j0 + k] for k in ks) / den
    if A0 <= 0:
        return None
    out = {"j0": int(j0), "shape": [round(float(t), 4) for t in w],
           "A0": A0, "floor": float(floor),
           "floor_ratio": float(floor / A0) if A0 > 0 else 1.0}
    for d in (1, 2):
        if j0 - d >= 0:
            out["f_m%d" % d] = float(I[j0 - d] / w[0] / A0)
    jo = n_label if j0 == 0 else 0
    ks2 = [k for k in (1, 2, 3) if jo + k < len(I) and w[k] > 0]
    Ao = (sum(w[k] * I[jo + k] for k in ks2) / sum(w[k] ** 2 for k in ks2)) if ks2 else 0.0
    out["f_other"] = float(Ao / A0)
    out["f_other_ub"] = float(floor / w[0] / A0) if w[0] > 0 else 1.0
    parts = {"main": 1.0}
    for d in (1, 2):
        parts["m%d" % d] = out.get("f_m%d" % d, 0.0)
    parts["other"] = out["f_other"]
    sites = {"main": (1.0 if j0 == n_label else 0.0),
             "m1": (n_label - 1) / n_label, "m2": (n_label - 2) / n_label,
             "other": (0.0 if j0 == n_label else 1.0)}
    tot = sum(parts.values())
    if tot <= 0:
        return None
    out["comp"] = OrderedDict((k, v / tot) for k, v in parts.items())
    out["avg"] = float(sum(parts[k] * sites[k] for k in parts) / tot * 100.0)
    out["main_frac"] = float(100.0 / tot)
    out["main_kind"] = "全标记形" if j0 == n_label else "天然形"
    out["sites_mean"] = float(sum(parts[k] * sites[k] for k in parts) / tot)
    return out


# ===========================================================================
# 6. 输入读取 / CSV 输出
# ===========================================================================
def _txt(ws, cell, default=""):
    v = ws[cell].value
    return default if v is None else str(v).strip()


def _num(ws, cell, default=0.0):
    v = ws[cell].value
    if v is None or str(v).strip() == "":
        return default
    try:
        return float(str(v).strip())
    except Exception:
        return default


def read_inputs(xlsm_path):
    import openpyxl
    wb = openpyxl.load_workbook(xlsm_path, data_only=True)
    if "输入" not in wb.sheetnames:
        raise ValueError("工作簿缺少「输入」表")
    ws = wb["输入"]
    inp = {}
    inp["f_nat"] = _txt(ws, "B5")
    inp["f_lab"] = _txt(ws, "B6")
    inp["n_label_in"] = _num(ws, "B7", 0.0)
    inp["z_sel"] = _txt(ws, "B8", "3")
    inp["data_dir"] = _txt(ws, "B10")
    inp["rt_lo"] = _num(ws, "B11", 0.0)
    inp["rt_hi"] = _num(ws, "D11", 0.0)
    inp["a"] = _num(ws, "B13", A_DEFAULT)
    inp["c"] = _num(ws, "B14", C_DEFAULT)
    inp["auto_cal"] = _txt(ws, "B15", "是").lower() not in ("否", "no", "n", "false", "0")
    refs = _txt(ws, "B16", NAT_REF_DEFAULT).replace("，", ",")
    inp["nat_refs"] = [s.strip() for s in refs.split(",") if s.strip()]
    inp["py"] = _txt(ws, "B40", "")
    samples = []
    for r in range(19, 39):
        fn = ws.cell(r, 2).value
        if fn is None or str(fn).strip() == "":
            continue
        nm = ws.cell(r, 1).value
        note = ws.cell(r, 3).value
        samples.append({"name": str(nm or "").strip() or os.path.splitext(str(fn))[0],
                        "file": str(fn).strip(),
                        "note": str(note or "").strip()})
    inp["samples"] = samples
    zs = []
    for tok in inp["z_sel"].replace("，", ",").split(","):
        tok = tok.strip()
        if tok.isdigit():
            zs.append(int(tok))
        elif tok.lower() in ("both", "两者", "all"):
            zs = [3, 2, 4]
            break
    inp["z_list"] = zs or [3]
    if not inp["f_nat"] or not inp["f_lab"]:
        raise ValueError("输入表 B5/B6 需填天然形与全标记形分子式")
    if not samples:
        raise ValueError("输入表样品表（A19:C38）为空")
    return inp


def _cmd(x):
    """CSV 单元格清理：去掉逗号（VBA 用 Split(',') 解析）。"""
    s = "" if x is None else str(x)
    return s.replace(",", " /")


def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([_cmd(h) for h in header])
        for row in rows:
            w.writerow([_cmd(c) for c in row])


def produce_outputs(xlsm_path, inp):
    fname = os.path.splitext(os.path.basename(xlsm_path))[0]   # 文件名基名（勿与其它变量复用）
    folder = tempfile.gettempdir()
    data_dir = inp["data_dir"]
    z_list = inp["z_list"]

    cols_nat = cols_of(inp["f_nat"])
    cols_lab = cols_of(inp["f_lab"])
    n_label = int(inp["n_label_in"]) or labeled_atom_count(cols_lab)

    # 理论量
    th = {}
    apex_theo = {}
    for z in z_list:
        th[z] = {
            "m_nat": centroid_mz(cols_nat, z), "m_lab": centroid_mz(cols_lab, z),
            "m0n": mono_mz(cols_nat, z), "m0l": mono_mz(cols_lab, z),
            "apex_nat": apex_mz(cols_nat, z), "apex_lab": apex_mz(cols_lab, z),
            "u": U13C / z,
        }
        th[z]["delta"] = th[z]["m_lab"] - th[z]["m_nat"]
        apex_theo[("nat", z)] = th[z]["apex_nat"]
        apex_theo[("lab", z)] = th[z]["apex_lab"]

    # 读谱（x 轴）
    spectra = OrderedDict()
    read_note = {}
    for s in inp["samples"]:
        p = s["file"]
        if not os.path.isabs(p):
            p = os.path.join(data_dir, p)
        if not os.path.exists(p):
            spectra[s["name"]] = (np.array([]), np.array([]), 0)
            read_note[s["name"]] = "文件不存在"
            continue
        try:
            scans = read_qtof(p)
            xs, it, ns = window_spectrum(scans, inp["rt_lo"], inp["rt_hi"])
            spectra[s["name"]] = (xs, it, ns)
        except Exception as e:
            spectra[s["name"]] = (np.array([]), np.array([]), 0)
            read_note[s["name"]] = "读取失败：%s" % str(e)[:80]

    # 标定（自动重标定须过 auto_calibrate 的三道门槛；任一不过 → 真回退到输入表 a/c）
    a, c = inp["a"], inp["c"]
    anchors, cal_note = [], ""
    if inp["auto_cal"]:
        a2, c2, anchors, cal_note = auto_calibrate(spectra, z_list, apex_theo, a, c)
        ok = (len(anchors) >= 3 and math.isfinite(a2) and math.isfinite(c2)
              and a2 > MIN_CAL_A)
        if ok:
            a, c = a2, c2
        else:
            anchors = []      # 明确置空：未过门槛的标定一律不得采纳（防质量轴塌缩）
            cal_note = cal_note or "自动标定未过门槛，沿用输入表 a/c"

    # 逐样品 × 电荷态
    detail_rows, dist_rows, overview, cal_rows = [], [], [], []
    per = OrderedDict()
    for s in inp["samples"]:
        tag = s["name"]
        xs, it, ns = spectra.get(tag, (np.array([]), np.array([]), 0))
        per[tag] = {"file": s["file"], "note": s["note"], "nscan": ns, "per_z": {}}
        if len(xs) == 0:
            for z in z_list:
                overview.append([tag, s["file"], z, "", "", "", "", "无峰/未读到", s["note"]])
                detail_rows.append([tag, s["file"], z] + [""] * 12)
                dist_rows.append([tag, z] + [""] * 8)
            continue
        m = a * xs * xs + c
        o = np.argsort(m)
        m, itn = m[o], it[o]
        for z in z_list:
            d = th[z]
            lo = d["m0n"] - WIN_LO_U * d["u"]
            hi = d["m0l"] + WIN_HI_U * d["u"]
            m_meas, nb = centroid_cal(m, itn, lo, hi)
            I = envelope_peaks(m, itn, d["m0n"], d["u"])
            sel = (m >= lo) & (m <= hi)
            pos = itn[sel][itn[sel] > 0]
            floor = float(pos.min()) if pos.size else 0.0
            mx = float(I.max()) if len(I) else 0.0
            j0 = int(np.argmax(I >= 0.05 * mx)) if mx > 0 else 0
            env = neighbor_analysis(I, j0, floor, n_label) if mx > 0 else None
            per[tag]["per_z"][z] = dict(m_meas=m_meas, lo=lo, hi=hi, n_used=nb,
                                        env=env, npeak=len(m))
            if m_meas is None:
                raw = None
            else:
                raw = (m_meas - d["m_nat"]) / d["delta"] * 100.0
            per[tag]["per_z"][z]["enrich_raw"] = raw
            detail_rows.append([
                tag, s["file"], z,
                "" if m_meas is None else "%.4f" % m_meas,
                "%.4f" % d["m_nat"], "%.4f" % d["m_lab"], "%.4f" % d["delta"],
                "%.3f" % lo, "%.3f" % hi, nb, len(m),
                "" if raw is None else "%.3f" % raw,
                "", "", ""])
            dist_rows.append([
                tag, z, ("" if env is None else env["j0"]),
                "" if env is None else env["main_kind"],
                "" if env is None else "%.3f" % (env["comp"].get("main", 0) * 100),
                "" if env is None else "%.3f" % (env["comp"].get("m1", 0) * 100),
                "" if env is None else "%.3f" % (env["comp"].get("m2", 0) * 100),
                "" if env is None else "%.3f" % (env["comp"].get("other", 0) * 100),
                "" if env is None else "%.3f" % env["avg"],
                "" if env is None else "%.3f" % (env["floor_ratio"] * 100)])

    # 零点（天然对照）
    offset, off_sd = {}, {}
    for z in z_list:
        v = []
        for tag in inp["nat_refs"]:
            e = per.get(tag, {}).get("per_z", {}).get(z)
            if e and e.get("m_meas") is not None:
                v.append(e["m_meas"] - th[z]["m_nat"])
        offset[z] = float(np.mean(v)) if v else 0.0
        off_sd[z] = float(np.std(v)) if v else 0.0

    # 校准后取代率 + 总览
    for s in inp["samples"]:
        tag = s["name"]
        for z in z_list:
            e = per[tag]["per_z"].get(z)
            if e is None:
                continue
            off = offset[z]
            if e["m_meas"] is None:
                enr = None
            else:
                enr = (e["m_meas"] - off - th[z]["m_nat"]) / th[z]["delta"] * 100.0
            e["enrich"] = enr
            env = e["env"]
            judge = "—"
            if enr is not None and env is not None:
                if enr >= 90:
                    judge = "全标记形（标记成功）"
                elif enr <= 5:
                    judge = "天然形（未标记）"
                else:
                    judge = "混合"
            overview.append([
                tag, s["file"], z,
                "" if enr is None else "%.2f" % enr,
                "" if env is None else "%.2f" % env["avg"],
                "" if env is None else env["main_kind"],
                "" if env is None else "%.2f" % env["main_frac"],
                judge,
                ("对照" if tag in inp["nat_refs"] else s["note"])])

    # 把零点 δ 与校准后取代率填回明细表（按 (样品, 电荷态) 反查，避免行序错位）
    ridx = {}
    for i, r in enumerate(detail_rows):
        ridx[(r[0], r[2])] = i
    for _tag, _pz in per.items():
        for _z, _e in _pz["per_z"].items():
            i = ridx.get((_tag, _z))
            if i is None:
                continue
            detail_rows[i][12] = "%.4f" % offset[_z]
            detail_rows[i][13] = "" if _e.get("enrich") is None else "%.3f" % _e["enrich"]

    # 标定诊断
    for x, y, yf, r in anchors:
        cal_rows.append(["锚点", "", "x=%.3f" % x, "%.4f" % y, "%.4f" % yf,
                         "%.4f" % (yf - y), "%.1f" % r, "基峰最小二乘"])
    for z in z_list:
        d = th[z]
        cal_rows.append(["理论", z, "天然形质心", "%.4f" % d["m_nat"], "", "", "", ""])
        cal_rows.append(["理论", z, "全标记形质心", "%.4f" % d["m_lab"], "", "",
                         "", "Δ=%.4f" % d["delta"]])
        cal_rows.append(["理论", z, "天然形 M0", "%.4f" % d["m0n"], "", "", "", ""])
        cal_rows.append(["理论", z, "全标记形 M0", "%.4f" % d["m0l"], "", "", "", ""])
        v = [per[t]["per_z"][z]["m_meas"] - d["m_nat"] for t in inp["nat_refs"]
             if per.get(t, {}).get("per_z", {}).get(z, {}).get("m_meas") is not None]
        cal_rows.append(["零点", z, "天然对照 δ(Da)", "%.4f" % offset[z], "", "",
                         "%.1f" % (off_sd[z] / d["delta"] * 100), "sd=%.4f Da  n=%d"
                         % (off_sd[z], len(v))])
    for s in inp["samples"]:
        tag = s["name"]
        for z in z_list:
            e = per.get(tag, {}).get("per_z", {}).get(z)
            if e is None:
                continue
            cal_rows.append(["窗口", z, tag,
                             "%.3f" % e["lo"], "%.3f" % e["hi"], "",
                             "", "窗内峰 %d / 用峰 %d" % (e["npeak"], e["n_used"])])
    sc = {"status": "OK", "a": "%.8e" % a, "c": "%.5f" % c,
          "auto_cal": "1" if inp["auto_cal"] else "0",
          "n_anchor": str(len(anchors)), "n_label": str(n_label),
          "z_list": ",".join(str(z) for z in z_list),
          "rt": "%.0f-%.0f" % (inp["rt_lo"], inp["rt_hi"]),
          "note": cal_note or "标定正常"}

    _write_csv(os.path.join(folder, "%s_overview.csv" % fname),
               ["样品", "文件", "电荷态", "质心法取代率%", "包络序号法平均取代率%",
                "包络法主形归属", "包络法主形比例%", "判定", "备注"], overview)
    _write_csv(os.path.join(folder, "%s_detail.csv" % fname),
               ["样品", "文件", "电荷态", "实测质心 m/z", "天然形质心(理论)",
                "全标记形质心(理论)", "Δ理论", "窗口lo", "窗口hi", "窗内峰数",
                "窗内总峰数", "原始取代率%", "零点δ(Da)", "校准后取代率%", "备注"],
               detail_rows)
    _write_csv(os.path.join(folder, "%s_dist.csv" % fname),
               ["样品", "电荷态", "主形起点j0", "包络法主形归属", "主形%",
                "轻一档(少1标记)%", "轻二档(少2标记)%", "另一形%",
                "平均取代率%", "最小可测峰/主形%"], dist_rows)
    _write_csv(os.path.join(folder, "%s_calib.csv" % fname),
               ["类别", "电荷态", "项目", "理论值", "观测值", "残差Da",
                "残差ppm", "备注"], cal_rows)
    with open(os.path.join(folder, "%s_scalars.txt" % fname), "w", encoding="utf-8") as f:
        for k, v in sc.items():
            f.write("%s=%s\n" % (k, v))
    return sc


# ===========================================================================
# 7. 入口
# ===========================================================================
def main(argv=None):
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = argv if argv is not None else sys.argv[1:]
    if any(x.startswith("--xlsm") for x in args):
        xlsm = args[args.index("--xlsm") + 1]
        inp = read_inputs(xlsm)
        sc = produce_outputs(xlsm, inp)
        print("DONE", sc)
        return 0
    print("usage: python embedded_engine.py --xlsm FILE.xlsm")
    print("       （只读「输入」表，结果写 %%TEMP%%\\<基名>_*.csv）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
