# -*- coding: utf-8 -*-
# =============================================================================
# 同位素杂质含量计算引擎（自包含版，供 .xlsm 内嵌调用）
#
# 输入 : 一个 .xlsm 文件，其 "输入" 工作表含：
#          B3 = mzML 文件路径
#          B4 = 样品名称
#          B5 = 计算电荷态 ("3" / "4" / "3,4" / "两者")
#          B6 = 分辨率 ppm（默认 5）
#          B11:B17 (元素/同位素/个数) = 标记化合物本体（全标记）元素表
# 输出 : 与 xlsm 同目录，写出若干 CSV + 一个 scalars.txt，供 VBA 回填工作表：
#          <base>_scalars.txt
#          <base>_overview.csv
#          <base>_z3_tiers.csv / <base>_z3_imp.csv
#          <base>_z4_tiers.csv / <base>_z4_imp.csv
#          <base>_calib.csv
#
# 方法（与现有 calc.py 完全一致，仅改为从输入表动态建模型、结果写 CSV）：
#   - 9 档体系：tier t = 8 个标记原子中 t 个被天然同位素替换（t=0 本体，t=8 全天然）
#   - 每个档用理论同位素包络（theo）算出离散峰；取「窗口重叠概率」作固定系数
#   - 逐锚点宽窗找真实峰心做质量校准，再在 ±MEAS_HALF 窗提强度（积分/峰顶两种方式）
#   - M0 法（轻→重三角扣除）与 基峰法（重→轻三角扣除）在 5ppm 下数学等价
#   - 归一化到总量 100%，每档含量等分给其成员（20 个杂质）
# =============================================================================
from __future__ import annotations

import os
import sys
import csv
import itertools
from collections import defaultdict, OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import molmass

ELECTRON_MASS = molmass.ELECTRON.mass   # 0.000548579909 u
PROTON_MASS = 1.00782503223             # 1H 原子质量

# ---------------------------------------------------------------------------
# is_natural 判断
# ---------------------------------------------------------------------------
def is_natural_iso(iso):
    return str(iso).lower() in ("natural", "自然", "自然分布", "")


# ===========================================================================
# imp —— 同位素取代杂质枚举
# ===========================================================================
SUB_D = {'0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄',
         '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉'}
SUP_D = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
         '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹'}


def _to_sub(n):
    return ''.join(SUB_D[d] for d in str(n))


def _to_sup(n):
    return ''.join(SUP_D[d] for d in str(n))


def iso_symbol(element, mass_number):
    if element == 'H' and mass_number == 2:
        return 'D'
    if element == 'H' and mass_number == 3:
        return 'T'
    return _to_sup(mass_number) + element


def charge_suffix(z):
    if z == 0:
        return ''
    mag = '' if abs(z) == 1 else _to_sup(abs(z))
    return mag + ('⁺' if z > 0 else '⁻')


@dataclass
class ImpCol:
    element: str
    isotope: str
    count: int

    def __post_init__(self):
        self.element = self.element.strip().capitalize()
        self.isotope = str(self.isotope).strip()
        if self.count is None or self.count < 0:
            raise ValueError(f"原子个数必须 >= 0: {self.count}")

    @property
    def is_natural(self):
        return is_natural_iso(self.isotope)


def _canonical_key(cols):
    merged = OrderedDict()
    for elem, iso, cnt in cols:
        if cnt == 0:
            continue
        key = (elem, iso)
        merged[key] = merged.get(key, 0) + cnt
    items = sorted(merged.items(),
                   key=lambda kv: (kv[0][0], 0 if is_natural_iso(kv[0][1]) else 1, kv[0][1]))
    return tuple((e, i, c) for (e, i), c in items)


def formula_name(cols, z=1):
    parts = []
    for elem, iso, cnt in cols:
        if cnt == 0:
            continue
        symbol = elem if is_natural_iso(iso) else iso_symbol(elem, int(iso))
        parts.append(symbol if cnt == 1 else f"{symbol}{_to_sub(cnt)}")
    return ''.join(parts) + charge_suffix(z)


def enumerate_impurities(columns, z=1):
    if not columns:
        raise ValueError("元素表为空")
    nat_counts = OrderedDict()
    mod_cols = []
    for c in columns:
        if c.count <= 0:
            continue
        if c.is_natural:
            nat_counts[c.element] = nat_counts.get(c.element, 0) + c.count
        else:
            mod_cols.append(c)
    if not mod_cols:
        return []
    total_mod_atoms = sum(c.count for c in mod_cols)
    elem_order = []
    for c in columns:
        if c.count > 0 and c.element not in elem_order:
            elem_order.append(c.element)
    k_ranges = [range(0, c.count + 1) for c in mod_cols]
    results = []
    seen_keys = set()
    for k_comb in itertools.product(*k_ranges):
        if all(k == 0 for k in k_comb):
            continue
        nat_total = dict(nat_counts)
        mod_remain = []
        for mc, k in zip(mod_cols, k_comb):
            nat_total[mc.element] = nat_total.get(mc.element, 0) + k
            remain = mc.count - k
            if remain > 0:
                mod_remain.append((mc.element, mc.isotope, remain))
        cols = []
        for elem in elem_order:
            n = nat_total.get(elem, 0)
            if n > 0:
                cols.append((elem, 'natural', n))
            for el2, iso2, cnt2 in mod_remain:
                if el2 == elem:
                    cols.append((el2, iso2, cnt2))
        canon = _canonical_key(cols)
        if canon in seen_keys:
            continue
        seen_keys.add(canon)
        mod_total = sum(cnt for el, iso, cnt in cols if not is_natural_iso(iso))
        replaced_total = total_mod_atoms - mod_total
        results.append({
            'name': formula_name(cols, z),
            'elements': [c[0] for c in cols],
            'isos': [c[1] for c in cols],
            'counts': [c[2] for c in cols],
            'mod_total': mod_total,
            'replaced_total': replaced_total,
        })
    results.sort(key=lambda r: (0 if r['mod_total'] == 0 else 1, r['replaced_total'], r['name']))
    return results


# ===========================================================================
# theo —— 理论同位素分布（多项式卷积）
# ===========================================================================
@dataclass
class TheoCol:
    element: str
    isotope: str
    count: int

    def __post_init__(self):
        self.element = self.element.strip().capitalize()
        self.isotope = str(self.isotope).strip()
        if self.count is None or self.count < 0:
            raise ValueError(f"原子个数必须 >= 0: {self.count}")

    @property
    def is_natural(self):
        return is_natural_iso(self.isotope)


@dataclass
class IsotopePeak:
    mass: float
    abundance: float
    rel_abundance: float
    mz: float

    def __lt__(self, other):
        return self.mass < other.mass


def _element_natural_distribution(element):
    el = molmass.ELEMENTS[element]
    dist = []
    for mass_number in el.isotopes:
        iso = el.isotopes[mass_number]
        dist.append((iso.mass, iso.abundance))
    total = sum(p for _, p in dist)
    return [(m, p / total) for m, p in dist]


def _single_isotope_distribution(element, mass_number):
    el = molmass.ELEMENTS[element]
    iso = el.isotopes[int(mass_number)]
    return [(iso.mass, 1.0)]


def _column_distribution(col):
    if col.count == 0:
        return [(0.0, 1.0)]
    if col.is_natural:
        single = _element_natural_distribution(col.element)
    else:
        single = _single_isotope_distribution(col.element, col.isotope)
    if len(single) == 1:
        return [(single[0][0] * col.count, 1.0)]
    base = min(m for m, _ in single) * col.count
    states = {0: 1.0}
    for _ in range(col.count):
        new_states = defaultdict(float)
        for off, prob in states.items():
            for m, p in single:
                new_states[off + round((m - base / col.count) * 1e6)] += prob * p
        states = dict(new_states)
    return [(base + off / 1e6, prob) for off, prob in states.items()]


def _merge_by_tolerance(peaks, tol):
    if not peaks:
        return []
    peaks = sorted(peaks)
    merged = []
    cur_mass, cur_prob = peaks[0]
    for m, p in peaks[1:]:
        total = cur_prob + p
        if total == 0:
            continue
        if abs(m - cur_mass) <= tol:
            cur_mass = (cur_mass * cur_prob + m * p) / total
            cur_prob = total
        else:
            merged.append((cur_mass, cur_prob))
            cur_mass, cur_prob = m, p
    if cur_prob != 0:
        merged.append((cur_mass, cur_prob))
    return merged


def _convolve_columns(columns, tol=0.001, min_prob=1e-16):
    dist = [(0.0, 1.0)]
    for col in columns:
        col_dist = _column_distribution(col)
        if len(col_dist) == 1 and col_dist[0][1] == 1.0:
            dist = [(m1 + col_dist[0][0], p1) for m1, p1 in dist]
            continue
        raw = []
        for m1, p1 in dist:
            for m2, p2 in col_dist:
                p = p1 * p2
                if p >= min_prob:
                    raw.append((m1 + m2, p))
        dist = _merge_by_tolerance(raw, tol)
    return dist


def compute_theoretical_spectrum(columns, z=1, tol=0.001, top_n=50, min_ab=0.05):
    if not columns:
        raise ValueError("元素表为空")
    if tol <= 0:
        raise ValueError("质量精度必须 > 0")
    dist = _convolve_columns(columns, tol=tol)
    peaks = []
    for mass, prob in dist:
        if z == 0:
            mz = mass
        else:
            mz = (mass + z * (PROTON_MASS - ELECTRON_MASS)) / z
        peaks.append(IsotopePeak(mass=mass, abundance=prob, rel_abundance=0.0, mz=mz))
    if not peaks:
        return []
    max_ab = max(p.abundance for p in peaks)
    for p in peaks:
        p.rel_abundance = p.abundance / max_ab * 100.0
    peaks = [p for p in peaks if p.rel_abundance >= min_ab]
    peaks.sort(key=lambda p: p.abundance, reverse=True)
    peaks = peaks[:top_n]
    peaks.sort()
    return peaks


# ===========================================================================
# mzml_io —— 岛津 Shimadzu mzML 读取器（质心化谱按 m/z 分箱累加）
# ===========================================================================
import base64 as _base64
import struct as _struct
import xml.etree.ElementTree as ET
import zlib as _zlib


def _decode_binary(b64_text, precision, compressed):
    raw = _base64.b64decode(b64_text)
    if compressed:
        raw = _zlib.decompress(raw)
    size = 8 if precision == 64 else 4
    n = len(raw) // size
    fmt = "<" + ("d" if precision == 64 else "f") * n
    return np.array(_struct.unpack(fmt, raw), dtype=np.float64)


def _iter_spectra(path):
    M = {"m": "http://psi.hupo.org/ms/mzml"}
    for ev, el in ET.iterparse(path, events=("end",)):
        if el.tag.split("}")[-1] != "spectrum":
            continue
        rt = None
        mz = None
        inten = None
        for c in el.iter():
            t = c.tag.split("}")[-1]
            if t == "cvParam":
                a = c.get("accession")
                if a == "MS:1000016":
                    v = float(c.get("value"))
                    unit = (c.get("unitName") or "").lower()
                    if "min" in unit and v > 1000:
                        v *= 60.0
                    rt = v
            elif t == "binaryDataArray":
                kind = None
                prec = 64
                comp = False
                b64 = None
                for cv in c.findall("m:cvParam", M):
                    a = cv.get("accession")
                    if a == "MS:1000514":
                        kind = "mz"
                    elif a == "MS:1000515":
                        kind = "int"
                    elif a == "MS:1000523":
                        prec = 64
                    elif a == "MS:1000521":
                        prec = 32
                    elif a == "MS:1000574":
                        comp = True
                bin_el = c.find("m:binary", M)
                if bin_el is not None and bin_el.text:
                    b64 = bin_el.text.strip()
                if kind and b64:
                    arr = _decode_binary(b64, prec, comp)
                    if kind == "mz":
                        mz = arr
                    else:
                        inten = arr
        if rt is not None and mz is not None and inten is not None:
            yield rt, mz, inten
        el.clear()


def detect_rt_window(path, target_mz, half_width_mz=0.5, frac=0.1):
    rts = []
    bands = []
    for rt, mz, inten in _iter_spectra(path):
        lo = target_mz - half_width_mz
        hi = target_mz + half_width_mz
        mask = (mz >= lo) & (mz <= hi)
        bands.append(float(inten[mask].sum()) if mask.any() else 0.0)
        rts.append(rt)
    rts = np.array(rts, dtype=np.float64)
    bands = np.array(bands, dtype=np.float64)
    if bands.max() <= 0:
        return (float(rts.min()), float(rts.max()), rts, bands)
    thr = bands.max() * frac
    idx = np.where(bands >= thr)[0]
    if len(idx) == 0:
        return (float(rts.min()), float(rts.max()), rts, bands)
    return float(rts[idx.min()]), float(rts[idx.max()]), rts, bands


def read_mzml(path, rt_lo=None, rt_hi=None, target_mz=None, half_width_mz=0.5,
              frac=0.1, bin_width=0.001):
    if rt_lo is None or rt_hi is None:
        if target_mz is None:
            raise ValueError("未提供 RT 窗口且未提供 target_mz")
        rt_lo, rt_hi, _, _ = detect_rt_window(path, target_mz, half_width_mz, frac)
    acc = {}
    inv = 1.0 / bin_width
    n = 0
    for rt, mz, inten in _iter_spectra(path):
        if rt < rt_lo or rt > rt_hi:
            continue
        for x, y in zip(mz, inten):
            yi = float(y)
            if yi <= 0:
                continue
            key = round(float(x) * inv) * bin_width
            acc[key] = acc.get(key, 0.0) + yi
        n += 1
    if not acc:
        return {"mz": np.array([]), "intensity": np.array([]), "n_scans": 0,
                "rt_lo": rt_lo, "rt_hi": rt_hi}
    mz_grid = np.array(sorted(acc.keys()), dtype=np.float64)
    int_sum = np.array([acc[k] for k in mz_grid], dtype=np.float64)
    return {"mz": mz_grid, "intensity": int_sum, "n_scans": n,
            "rt_lo": rt_lo, "rt_hi": rt_hi}


def extract(mz, intensity, center, ppm, mode="integral"):
    hw = center * ppm / 1e6 / 2.0
    lo = center - hw
    hi = center + hw
    mask = (mz >= lo) & (mz <= hi)
    if not mask.any():
        if mode == "top":
            return {"value": 0.0, "peak_mz": center, "hw": hw, "lo": lo, "hi": hi}
        return {"value": 0.0, "hw": hw, "lo": lo, "hi": hi}
    if mode == "top":
        sub_mz = mz[mask]
        sub_int = intensity[mask]
        i = int(np.argmax(sub_int))
        return {"value": float(sub_int[i]), "peak_mz": float(sub_mz[i]),
                "hw": hw, "lo": lo, "hi": hi}
    return {"value": float(intensity[mask].sum()), "hw": hw, "lo": lo, "hi": hi}


# ===========================================================================
# model —— 9 档理论包络（动态，从输入元素表构建）
# ===========================================================================
THEO_TOP_N = 60
THEO_MIN_AB = 1e-5

# 模块级模型（由 init_model 填充）
MODEL = {}
TIERS = {}
IMPURITIES = []


def _to_theo_cols(elements, isos, counts):
    return [TheoCol(e, i, int(c)) for e, i, c in zip(elements, isos, counts)]


def build_tiers(labeled_cols):
    imp_cols = [ImpCol(e, i, int(c)) for e, i, c in labeled_cols]
    impurities = enumerate_impurities(imp_cols, z=0)
    tiers = {}
    tiers[0] = {
        "t": 0, "name": "本体(全标记)",
        "elems": [e for e, _, _ in labeled_cols],
        "isos": [i for _, i, _ in labeled_cols],
        "counts": [c for _, _, c in labeled_cols],
        "members": [],
    }
    by_tier = defaultdict(list)
    for r in impurities:
        by_tier[r["replaced_total"]].append(r)
    n_tiers = max(by_tier.keys()) if by_tier else 0
    for t in range(1, n_tiers + 1):
        members = by_tier.get(t, [])
        rep = members[0]
        tiers[t] = {
            "t": t, "name": f"替换{t}档",
            "elems": list(rep["elements"]), "isos": list(rep["isos"]),
            "counts": list(rep["counts"]), "members": members,
        }
    return tiers, impurities


def _peaks_for_cols(cols, z):
    peaks = compute_theoretical_spectrum(cols, z=z, tol=0.001,
                                         min_ab=THEO_MIN_AB, top_n=THEO_TOP_N)
    peaks = sorted(peaks, key=lambda p: p.mass)
    total = sum(p.abundance for p in peaks) or 1.0
    return [(p.mz, p.abundance / total) for p in peaks]


def tier_peaks(tier, z):
    if tier["members"]:
        cols_list = [_to_theo_cols(m["elements"], m["isos"], m["counts"])
                     for m in tier["members"]]
    else:
        cols_list = [_to_theo_cols(tier["elems"], tier["isos"], tier["counts"])]
    all_peaks = []
    m0s, bases = [], []
    for cols in cols_list:
        pk = _peaks_for_cols(cols, z)
        all_peaks.append(pk)
        m0s.append(pk[0][0])
        bases.append(max(pk, key=lambda x: x[1])[0])
    n = max(len(pk) for pk in all_peaks)
    avg = []
    for i in range(n):
        mz_sum, pr_sum, cnt = 0.0, 0.0, 0
        for pk in all_peaks:
            if i < len(pk):
                mz_sum += pk[i][0]
                pr_sum += pk[i][1]
                cnt += 1
        if cnt:
            avg.append((mz_sum / cnt, pr_sum / cnt))
    avg.sort(key=lambda x: x[0])
    return {"m0_mz": float(np.mean(m0s)), "base_mz": float(np.mean(bases)), "peaks": avg}


def init_model(labeled_cols, z_list):
    global MODEL, TIERS, IMPURITIES
    TIERS, IMPURITIES = build_tiers(labeled_cols)
    MODEL = {}
    for z in z_list:
        MODEL[z] = {t: tier_peaks(TIERS[t], z) for t in range(0, len(TIERS))}
    return MODEL, TIERS, IMPURITIES


def window_overlap(tier_peaks, center, ppm):
    hw = center * ppm / 1e6 / 2.0
    lo, hi = center - hw, center + hw
    return float(sum(pr for mz, pr in tier_peaks if lo <= mz <= hi))


# ===========================================================================
# calc —— 逐级扣除引擎
# ===========================================================================
SEARCH_HALF = 0.06   # Da，校准搜峰半宽
MEAS_HALF = 0.04     # Da，测量/重叠矩阵窗口半宽


def _overlap_matrix(anchor_kind, z, half=MEAS_HALF):
    W = np.zeros((9, 9))
    for i in range(9):
        center = MODEL[z][i]["m0_mz" if anchor_kind == "m0" else "base_mz"]
        lo, hi = center - half, center + half
        for j in range(9):
            W[i, j] = float(sum(pr for mzp, pr in MODEL[z][j]["peaks"]
                                 if lo <= mzp <= hi))
    return W


NTIERS = 9


def find_center(mz, inten, approx):
    lo, hi = approx - SEARCH_HALF, approx + SEARCH_HALF
    mask = (mz >= lo) & (mz <= hi)
    if not mask.any():
        return approx
    i = int(np.argmax(inten[mask]))
    return float(mz[mask][i])


def calibrate(mz, inten, z):
    centers = {}
    for t in range(NTIERS):
        centers[(t, "m0")] = find_center(mz, inten, MODEL[z][t]["m0_mz"])
        centers[(t, "base")] = find_center(mz, inten, MODEL[z][t]["base_mz"])
    return centers


def _tier_gap_da(z):
    """相邻档 M0 间距（Da），用于约束本底环不串入相邻档。"""
    mzs = [MODEL[z][t]["m0_mz"] for t in range(NTIERS)]
    gaps = [abs(mzs[t] - mzs[t - 1]) for t in range(1, NTIERS)]
    return min(gaps) if gaps else 1.0


def _local_baseline(mz, inten, center, z):
    """峰附近平缓基线估计（自积分背景扣除用）。

    取测量窗外侧环 [center+MEAS_HALF+pad, center+gap/2-δ] 两侧的中位强度作基线。
    gap = 相邻档间距，保证环内不含相邻档峰（二者相距 ~0.33/z Da）。
    质心化谱的环内常无数据点 → 返回 0（即无需扣本底）。
    """
    gap = _tier_gap_da(z)
    inner = MEAS_HALF + 0.01
    outer = min(gap / 2.0 - 0.005, inner + 0.12)
    if outer <= inner:
        return 0.0
    mask = ((mz >= center - outer) & (mz <= center - inner)) | \
           ((mz >= center + inner) & (mz <= center + outer))
    if not mask.any():
        return 0.0
    return float(np.median(inten[mask]))


def extract_measured(mz, inten, z, centers):
    out = {"m0": {"integral": {}, "top": {}}, "base": {"integral": {}, "top": {}}}
    baselines = {}
    for t in range(NTIERS):
        for anchor in ("m0", "base"):
            c = centers[(t, anchor)]
            lo, hi = c - MEAS_HALF, c + MEAS_HALF
            mask = (mz >= lo) & (mz <= hi)
            if mask.any():
                base = _local_baseline(mz, inten, c, z)
                nb = int(mask.sum())
                integ = max(0.0, float(inten[mask].sum()) - base * nb)
                i = int(np.argmax(inten[mask]))
                top = max(0.0, float(inten[mask][i]) - base)
            else:
                integ, top, base = 0.0, 0.0, 0.0
            out[anchor]["integral"][t] = integ
            out[anchor]["top"][t] = top
            baselines[(t, anchor)] = base
    out["baseline"] = baselines
    return out


def deduce_m0(measured_m0, z, half=MEAS_HALF):
    W = _overlap_matrix("m0", z, half)
    amps, notes = {}, {}
    for t in sorted(range(NTIERS), key=lambda x: -x):
        I_t = measured_m0[t]
        sub = amps.get(t + 1, 0.0) * W[t, t + 1] if (t + 1) in amps else 0.0
        denom = W[t, t]
        a = (I_t - sub) / denom if denom > 0 else 0.0
        if a < 0:
            a = 0.0
            notes[t] = "扣除后为负，截断为 0"
        elif sub:
            notes[t] = f"已扣除更轻档 t{t+1} 基峰贡献 {sub:.1f}"
        amps[t] = a
    return amps, notes


def deduce_base(measured_base, z, half=MEAS_HALF):
    W = _overlap_matrix("base", z, half)
    amps, notes = {}, {}
    for t in range(NTIERS):
        I_t = measured_base[t]
        sub = amps.get(t - 1, 0.0) * W[t, t - 1] if (t - 1) in amps else 0.0
        denom = W[t, t]
        a = (I_t - sub) / denom if denom > 0 else 0.0
        if a < 0:
            a = 0.0
            notes[t] = "扣除后为负，截断为 0"
        amps[t] = a
    return amps, notes


def normalize_amounts(amps):
    tot = sum(amps.values())
    if tot <= 0:
        return {t: 0.0 for t in amps}, 0.0
    return {t: amps[t] / tot * 100.0 for t in amps}, tot


def _iso_mass(element, iso):
    """天然同位素取最轻同位素质量（monoisotopic），指定同位素取其精确质量。"""
    el = molmass.ELEMENTS[element]
    if is_natural_iso(iso):
        return min(i.mass for i in el.isotopes.values())
    return el.isotopes[int(iso)].mass


def _member_m0_mz(member, z):
    """逐组成精确单同位素 m/z：天然元素取最轻同位素，标记元素取其指定质量。"""
    m = 0.0
    for e, iso, cnt in zip(member["elements"], member["isos"], member["counts"]):
        m += int(cnt) * _iso_mass(e, iso)
    return (m + z * (PROTON_MASS - ELECTRON_MASS)) / z


def distribute_to_impurities(content_pct, z):
    body_m0 = MODEL[z][0]["m0_mz"]
    rows = [{
        "tier": 0, "kind": "本体", "name": "SPH20291-Isotope1 (全标记本体)",
        "content_pct": content_pct[0], "n_members": 1, "m0_mz_real": body_m0,
    }]
    for t in range(1, NTIERS):
        members = TIERS[t]["members"]
        n = len(members) or 1
        if n == 1:
            m = members[0]
            rows.append({
                "tier": t, "kind": "杂质", "name": m["name"],
                "content_pct": content_pct[t], "n_members": 1,
                "m0_mz_real": _member_m0_mz(m, z),
            })
        else:
            # 同档内 ¹³C/¹⁵N 替换组合在 5 ppm 下质量简并（Δm/z < 0.0021 @z=3），
            # 实验同一峰、无法分辨 → 合并为 1 行，列出所有分子式与各自真实单同位素 m/z，
            # 含量取整档总量（不再按成员均分）。
            names = [m["name"] for m in members]
            reals = [_member_m0_mz(m, z) for m in members]
            rows.append({
                "tier": t, "kind": "杂质",
                "name": " / ".join(names) + f"（未分辨，{n} 组合）",
                "content_pct": content_pct[t], "n_members": n,
                "m0_mz_real": reals,
            })
    return rows


def _tier_desc(t):
    if t == 0:
        return "本体：保留全部 8 个标记原子（6×¹³C + 2×¹⁵N）"
    if t == 8:
        return "天然形：8 个标记原子全部被天然同位素替换"
    return f"{t} 个标记原子被天然同位素（¹²C/¹⁴N）替换"


def _tier_division_note():
    return [
        "【档的划分说明】分析物 SPH20291-Isotope1 全标记本体含 8 个稳定同位素标记原子（6×¹³C + 2×¹⁵N）。",
        "  tier t = 其中 t 个标记原子被天然同位素（¹²C / ¹⁴N）替换：t=0 本体(全标记)，t=8 即天然形 SPH20291，共 9 档、20 个杂质。",
        "  每档成员数见「成员数」列（t=1..8）；同档内 ¹³C 与 ¹⁵N 替换组合在 5 ppm 下质量简并（Δm/z < 0.0021 @z=3），实验同一峰、无法分辨，",
        "  故在杂质明细中合并为 1 行（列出全部分子式与各自真实单同位素 m/z，含量取整档总量）；见各档「类型 / 分子式 / 真实 m/z」。",
        "  9 档沿「天然替换数」构成链：位置 M0(t) 叠放 本体档 t 的 M0 与 档 t+1 的基峰（相距 ~0.33/z Da ≈ 353 ppm，远在窗口外）。",
        "  逐级三角扣除时，计算某档含量已扣掉相邻更轻档(t+1)基峰的串入，故已扣除其它杂质的影响；非相邻档不串入。",
        "  同档内所有不可分辨组合（如 tier-1 的 ¹³C/¹⁵N 换回、tier-2..7 的多个 ¹³C/¹⁵N 换回组合）在杂质明细中均合并为 1 行，",
        "  列出全部分子式与各自真实单同位素 m/z（组合间仅差 ~2.2 ppm×|Δn|，实验同一峰），含量取整档总量（共 9 行，每档 1 行）。",
    ]


def analyze_sample(path, z, sample_name=None, half=MEAS_HALF):
    sample_name = sample_name or path
    body_m0 = MODEL[z][0]["m0_mz"]
    d = read_mzml(path, target_mz=body_m0, half_width_mz=0.5, frac=0.1)
    mz, inten = d["mz"], d["intensity"]
    if len(mz) == 0:
        raise RuntimeError(f"{sample_name}: 未能从 mzML 读取到谱图")
    centers = calibrate(mz, inten, z)
    meas = extract_measured(mz, inten, z, centers)
    results = {}
    for anchor in ("m0", "base"):
        for mode in ("integral", "top"):
            if anchor == "m0":
                amps, notes = deduce_m0(meas["m0"][mode], z, half)
            else:
                amps, notes = deduce_base(meas["base"][mode], z, half)
            content, total_raw = normalize_amounts(amps)
            rows = distribute_to_impurities(content, z)
            results[f"{anchor}_{mode}"] = {
                "amps_raw": amps, "content_pct": content, "rows": rows,
                "notes": notes, "total_raw": total_raw,
            }
    cal_off = {}
    for t in range(NTIERS):
        cal_off[(t, "m0")] = centers[(t, "m0")] - MODEL[z][t]["m0_mz"]
        cal_off[(t, "base")] = centers[(t, "base")] - MODEL[z][t]["base_mz"]
    return {
        "sample": sample_name, "z": z, "meas_half_da": half,
        "rt_lo": d["rt_lo"], "rt_hi": d["rt_hi"], "n_scans": d["n_scans"],
        "centers": centers, "cal_off": cal_off,
        "measured": meas, "baselines": meas["baseline"], "results": results,
    }


# ===========================================================================
# 读输入表 / 写 CSV 输出
# ===========================================================================
def read_inputs(xlsm_path):
    import openpyxl
    wb = openpyxl.load_workbook(xlsm_path, data_only=True)
    ws = wb["输入"]
    mzml = ws["B3"].value
    sample = ws["B4"].value or (os.path.basename(str(mzml)) if mzml else "")
    zsel = str(ws["B5"].value or "3,4")
    ppm = float(ws["B6"].value or 5.0)
    labeled = []
    for r in range(11, 18):
        e = ws.cell(r, 1).value
        i = ws.cell(r, 2).value
        c = ws.cell(r, 3).value
        if e is None or c is None:
            continue
        labeled.append((str(e), str(i), int(c)))
    zs = []
    for tok in zsel.replace("，", ",").split(","):
        tok = tok.strip()
        if tok in ("3", "4"):
            zs.append(int(tok))
        elif tok.lower() in ("both", "两者", "all"):
            zs = [3, 4]
            break
    if not zs:
        zs = [3]
    if not labeled:
        raise ValueError("输入表元素表为空（B11:B17）")
    return {"mzml": mzml, "sample": sample, "z_list": zs, "ppm": ppm, "labeled": labeled}


def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow(row)


def produce_outputs(xlsm_path, inp):
    init_model(inp["labeled"], inp["z_list"])
    base = os.path.splitext(os.path.basename(xlsm_path))[0]
    import tempfile
    folder = tempfile.gettempdir()   # 中间 CSV 写临时目录，保持工作目录干净
    sc = {}   # scalars
    sc["status"] = "OK"
    sc["sample"] = inp["sample"]
    sc["z_list"] = ",".join(str(z) for z in inp["z_list"])
    sc["ppm"] = f"{inp['ppm']:.3f}"

    overview_rows = []
    cal_rows_all = []
    for z in inp["z_list"]:
        res = analyze_sample(inp["mzml"], z, inp["sample"])
        for key in ("m0_integral", "m0_top", "base_integral", "base_top"):
            c = res["results"][key]["content_pct"]
            body = c[0]
            t1 = c[1] if 1 in c else 0.0
            rest = sum(c[t] for t in range(2, NTIERS))
            overview_rows.append([z, key, round(body, 1), round(t1, 1), round(rest, 1)])
        # 档汇总（4 法并列 + 档的划分说明列）
        tiers_rows = []
        for t in range(NTIERS):
            nm = TIERS[t]["name"]
            vals = []
            for key in ("m0_integral", "m0_top", "base_integral", "base_top"):
                vals.append(round(res["results"][key]["content_pct"][t], 1))
            tiers_rows.append([t, nm] + vals + [_tier_desc(t)])
        _write_csv(os.path.join(folder, f"{base}_z{z}_tiers.csv"),
                   ["tier", "名称", "本体(M0积分)%", "M0峰顶%", "基峰积分%", "基峰峰顶%", "档的划分说明"],
                   tiers_rows)
        # 杂质明细（以 M0·积分 为报告值；含逐组成真实 m/z / 实测 M0 m/z）
        imp_rows = []
        for row in res["results"]["m0_integral"]["rows"]:
            t = row["tier"]
            m0_real = row["m0_mz_real"]
            if isinstance(m0_real, (list, tuple)):
                m0_th_str = " / ".join(f"{x:.4f}" for x in m0_real)
            else:
                m0_th_str = f"{m0_real:.4f}"
            m0_meas = res["centers"][(t, "m0")]
            imp_rows.append([t, row["kind"], row["name"], m0_th_str,
                             round(m0_meas, 4), round(row["content_pct"], 1),
                             row["n_members"]])
        _write_csv(os.path.join(folder, f"{base}_z{z}_imp.csv"),
                   ["tier", "类型", "名称", "理论M0 m/z", "实测M0 m/z", "含量%", "该档成员数"],
                   imp_rows)
        # 校准与实测（汇总到一张表，跨 z；含每箱本底，用于核对背景扣除）
        for t in range(NTIERS):
            for anchor in ("m0", "base"):
                theo_mz = MODEL[z][t]["m0_mz" if anchor == "m0" else "base_mz"]
                meas_mz = res["centers"][(t, anchor)]
                off = meas_mz - theo_mz
                off_ppm = off / theo_mz * 1e6 if theo_mz else 0.0
                bl = res["baselines"][(t, anchor)]
                cal_rows_all.append([z, t, anchor, round(theo_mz, 6),
                                     round(meas_mz, 6), round(off, 6),
                                     round(off_ppm, 3), round(bl, 2)])

    _write_csv(os.path.join(folder, f"{base}_calib.csv"),
               ["电荷态", "档", "锚点", "理论m/z", "实测m/z", "偏移Da", "偏移ppm", "本底/每箱"],
               cal_rows_all)
    _write_csv(os.path.join(folder, f"{base}_tiernote.csv"),
               ["说明"], [[line] for line in _tier_division_note()])
    _write_csv(os.path.join(folder, f"{base}_overview.csv"),
               ["电荷态", "方法", "本体%", "tier1%", "tier2-8%"], overview_rows)

    with open(os.path.join(folder, f"{base}_scalars.txt"), "w", encoding="utf-8") as f:
        for k, v in sc.items():
            f.write(f"{k}={v}\n")
    return sc


# ===========================================================================
# 入口
# ===========================================================================
DEFAULT_COLS = [("C", "natural", 128), ("C", "13", 6), ("H", "natural", 198),
                ("N", "natural", 26), ("N", "15", 2), ("O", "natural", 35),
                ("S", "natural", 2)]


def main(argv=None):
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = argv if argv is not None else sys.argv[1:]
    # 主模式：从 xlsm 读输入
    if any(a.startswith("--xlsm") for a in args):
        xlsm = args[args.index("--xlsm") + 1]
        inp = read_inputs(xlsm)
        sc = produce_outputs(xlsm, inp)
        print("DONE", sc)
        return 0
    # 独立测试模式：--mzml PATH --z 3 [--ppm 5]
    mzml = None
    z = 3
    ppm = 5.0
    for i, a in enumerate(args):
        if a == "--mzml":
            mzml = args[i + 1]
        elif a == "--z":
            z = int(args[i + 1])
        elif a == "--ppm":
            ppm = float(args[i + 1])
    if not mzml:
        print("usage: python embedded_engine.py --xlsm FILE  (或 --mzml PATH --z 3)")
        return 1
    init_model(DEFAULT_COLS, [z])
    res = analyze_sample(mzml, z, "test")
    print(f"sample z={z} RT={res['rt_lo']:.1f}-{res['rt_hi']:.1f}s scans={res['n_scans']}")
    print("校准残差(ppm):",
          {f"t{t}": round(res["cal_off"][(t, "m0")] * 1e6 / MODEL[z][t]["m0_mz"], 2)
           for t in range(NTIERS)})
    for key in ("m0_integral", "m0_top", "base_integral", "base_top"):
        c = res["results"][key]["content_pct"]
        print(f"[{key}] 本体={c[0]:.3f}%  tier1={c[1]:.3f}%  rest={sum(c[t] for t in range(2, NTIERS)):.3f}%")
    # 写测试 CSV 到 cwd
    folder = os.getcwd()
    base = "test"
    sc = {}
    overview_rows = []
    for key in ("m0_integral", "m0_top", "base_integral", "base_top"):
        c = res["results"][key]["content_pct"]
        overview_rows.append([z, key, round(c[0], 1), round(c[1], 1),
                              round(sum(c[t] for t in range(2, NTIERS)), 1)])
    _write_csv(os.path.join(folder, f"{base}_z{z}_tiers.csv"),
               ["tier", "名称", "本体(M0积分)%", "M0峰顶%", "基峰积分%", "基峰峰顶%"],
               [[t, TIERS[t]["name"]] + [round(res["results"][k]["content_pct"][t], 1)
                for k in ("m0_integral", "m0_top", "base_integral", "base_top")]
                for t in range(NTIERS)])
    _write_csv(os.path.join(folder, f"{base}_overview.csv"),
               ["电荷态", "方法", "本体%", "tier1%", "tier2-8%"], overview_rows)
    print("test CSVs written to", folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
