# -*- coding: utf-8 -*-
"""
model - 同位素杂质计算的理论模型

构建 SPH20291-Isotope1（分析物本体 = 全标记）与 8 个「替换档」(tier) 的代表性组成，
并对每个 tier 在 z=2/3/4 下用 theo.py 计算理论同位素包络，保存离散峰列表
[(m/z, 归一化绝对概率), ...]。

关键修正：标记化合物（含 ¹³C 与 ¹⁵N）的 M+1 会拆成两个相邻峰
（¹²C→¹³C 贡献 +1.00335、¹⁴N→¹⁵N 贡献 +0.99703），故「偏移 k」必须用
「落在某锚点 5 ppm 窗口内的全部峰的概率之和」表示，不能按排序序号索引。

档定义：tier t = 8 个标记原子中有 t 个被天然同位素替换（t=0 为本体，t=8 为全天然形）。
同档内多个杂质质量简并（Δm/z < 0.0021 @z=3），包络近乎一致，取成员按偏移平均。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

import numpy as np

PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

import imp as imp_mod
import theo as theo_mod

# SPH20291-Isotope1 本体（全标记）输入列
LABELED_COLS = [
    ("C", "natural", 128), ("C", "13", 6),
    ("H", "natural", 198),
    ("N", "natural", 26), ("N", "15", 2),
    ("O", "natural", 35), ("S", "natural", 2),
]

THEO_TOP_N = 60
THEO_MIN_AB = 1e-5


def _to_theo_cols(elements, isos, counts):
    return [theo_mod.ElementColumn(e, i, int(c))
            for e, i, c in zip(elements, isos, counts)]


def build_tiers():
    """返回 {tier(0..8): {...}} 与 20 杂质清单。"""
    imp_cols = [imp_mod.ElementColumn(e, i, c) for e, i, c in LABELED_COLS]
    impurities = imp_mod.enumerate_impurities(imp_cols, z=0)

    tiers: Dict[int, dict] = {}
    tiers[0] = {
        "t": 0,
        "name": "本体(Isotope1, 全标记)",
        "elems": [e for e, _, _ in LABELED_COLS],
        "isos": [i for _, i, _ in LABELED_COLS],
        "counts": [c for _, _, c in LABELED_COLS],
        "members": [],
    }
    by_tier = defaultdict(list)
    for r in impurities:
        by_tier[r["replaced_total"]].append(r)
    for t in range(1, 9):
        members = by_tier.get(t, [])
        rep = members[0]
        tiers[t] = {
            "t": t,
            "name": f"替换{t}档",
            "elems": list(rep["elements"]),
            "isos": list(rep["isos"]),
            "counts": list(rep["counts"]),
            "members": members,
        }
    return tiers, impurities


def _peaks_for_cols(cols, z):
    """返回该组成的离散峰列表 [(mz, norm_abs_prob), ...]，按质量升序。
    norm_abs_prob 已归一化到该组成全部（保留）峰概率之和 = 1。"""
    peaks = theo_mod.compute_theoretical_spectrum(
        cols, z=z, tol=0.001, min_ab=THEO_MIN_AB, top_n=THEO_TOP_N)
    peaks = sorted(peaks, key=lambda p: p.mass)
    total = sum(p.abundance for p in peaks) or 1.0
    return [(p.mz, p.abundance / total) for p in peaks]


def tier_peaks(tier, z):
    """对一档计算代表包络：成员峰列表按 (mz, prob) 平均；锚点 m/z 取成员均值。
    返回 dict: {m0_mz, base_mz, peaks:[(mz,prob),...]}。"""
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
        # 基峰 = 概率最大的单峰
        bases.append(max(pk, key=lambda x: x[1])[0])

    # 平均：以 m/z 网格对齐（成员间 m/z 差 < 0.01，足够接近，直接按索引平均）
    n = max(len(pk) for pk in all_peaks)
    avg = []
    for i in range(n):
        mz_sum = 0.0
        pr_sum = 0.0
        cnt = 0
        for pk in all_peaks:
            if i < len(pk):
                mz_sum += pk[i][0]
                pr_sum += pk[i][1]
                cnt += 1
        if cnt:
            avg.append((mz_sum / cnt, pr_sum / cnt))
    avg.sort(key=lambda x: x[0])
    return {
        "m0_mz": float(np.mean(m0s)),
        "base_mz": float(np.mean(bases)),
        "peaks": avg,
    }


def build_model():
    tiers, impurities = build_tiers()
    model = {}
    for z in (2, 3, 4):
        model[z] = {t: tier_peaks(tiers[t], z) for t in range(0, 9)}
    return model, tiers, impurities


MODEL, TIERS, IMPURITIES = build_model()


def window_overlap(tier_peaks: List[Tuple[float, float]], center: float, ppm: float) -> float:
    """tier 的全部峰中，落在 center±ppm 窗口内的归一化概率之和。"""
    hw = center * ppm / 1e6 / 2.0
    lo, hi = center - hw, center + hw
    return float(sum(pr for mz, pr in tier_peaks if lo <= mz <= hi))


def m0_coeff(tier: int, z: int, ppm: float = 5.0) -> float:
    """tier 自身 M0 峰在 5ppm 窗口内的概率（≈P0）。"""
    e = MODEL[z][tier]
    return window_overlap(e["peaks"], e["m0_mz"], ppm)


def base_coeff(tier: int, z: int, ppm: float = 5.0) -> float:
    """tier 自身基峰在 5ppm 窗口内的概率。"""
    e = MODEL[z][tier]
    return window_overlap(e["peaks"], e["base_mz"], ppm)


if __name__ == "__main__":
    for z in (3, 4):
        print(f"\n===== z={z} =====")
        for t in range(0, 9):
            e = MODEL[z][t]
            print(f"  tier {t:>1} {TIERS[t]['name']:<16} M0={e['m0_mz']:.4f} "
                  f"base={e['base_mz']:.4f} P0(in5ppm)={m0_coeff(t,z):.4f} "
                  f"Pbase(in5ppm)={base_coeff(t,z):.4f} members={len(TIERS[t]['members'])}")
    print(f"\n20 杂质总数: {len(IMPURITIES)}")
