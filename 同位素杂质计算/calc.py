# -*- coding: utf-8 -*-
"""
calc - 同位素杂质含量计算引擎（用理论强度作固定系数，逐级扣除）

流程：
  1. 读取 mzML（自动 RT 窗口），质心化谱按 m/z 分箱累加为轮廓谱；
  2. 逐锚点质量校准：对每个档的 M0 / 基峰位置，在宽窗内找真实峰心（修正全局/局部质量偏差）；
  3. 在每个真实峰心 ±MEAS_HALF 窗口内提取强度（积分 与 峰顶 两种方式）；
  4. 用理论包络的「窗口重叠概率」作固定系数，逐级扣除：
       9 档体系本质为二对角：位置 M0(t) 上叠放 本体档 t 的 M0 与档 t+1 的基峰；
       - M0 法：轻→重逐级扣除（triangular）；
       - 基峰法：重→轻逐级扣除（triangular）。5 ppm 下基峰(t) 与 M0(t-1) 位置重合，
         二法量级一致、可互相校验；但链端无对偶档可抵消，扣除不闭合，存在固有偏差，
         并非严格等价。本报告以 M0 法为准，基峰法作一致性校验。
  5. 归一化到总量 100%；同档内成员质量简并，每档合并为 1 行、含量取整档总量；
  6. 返回结构化结果（用于 Excel / 报告）。

不做事实测谱拟合（非 deconvolution）：系数全部来自 theo.py 理论包络。
"""
from __future__ import annotations

import numpy as np

import molmass

import mzml_io as io
import model as M

ELECTRON_MASS = molmass.ELECTRON.mass
PROTON_MASS = 1.00782503223


def _is_natural_iso(iso):
    return str(iso).lower() in ("natural", "自然", "自然分布", "")


def _iso_mass(element, iso):
    el = molmass.ELEMENTS[element]
    if _is_natural_iso(iso):
        return min(i.mass for i in el.isotopes.values())
    return el.isotopes[int(iso)].mass


def _member_m0_mz(member, z):
    """逐组成精确单同位素 m/z（monoisotopic）：天然元素取最轻同位素，标记元素取其指定质量。"""
    m = 0.0
    for e, iso, cnt in zip(member["elements"], member["isos"], member["counts"]):
        m += int(cnt) * _iso_mass(e, iso)
    return (m + z * (PROTON_MASS - ELECTRON_MASS)) / z

SEARCH_HALF = 0.06   # Da，校准时寻找真实峰心的宽搜半宽
MEAS_HALF = 0.04     # Da，测量与重叠矩阵的窗口半宽（足以容纳整峰、且不串入相邻档 0.33/z）


def _overlap_matrix(anchor_kind, z, half=MEAS_HALF):
    """W[i][j] = tier j 全部峰落在 tier i 的 anchor±half 窗口内的概率之和。"""
    W = np.zeros((9, 9))
    for i in range(9):
        center = M.MODEL[z][i]["m0_mz" if anchor_kind == "m0" else "base_mz"]
        lo, hi = center - half, center + half
        for j in range(9):
            W[i, j] = float(sum(pr for mzp, pr in M.MODEL[z][j]["peaks"]
                                 if lo <= mzp <= hi))
    return W


def find_center(mz, inten, approx):
    """在 approx±SEARCH_HALF 内返回最强信号点的 m/z（真实峰心）。"""
    lo, hi = approx - SEARCH_HALF, approx + SEARCH_HALF
    mask = (mz >= lo) & (mz <= hi)
    if not mask.any():
        return approx
    i = int(np.argmax(inten[mask]))
    return float(mz[mask][i])


def calibrate(mz, inten, z):
    """返回 {(tier, anchor): 真实峰心 m/z}。"""
    centers = {}
    for t in range(9):
        centers[(t, "m0")] = find_center(mz, inten, M.MODEL[z][t]["m0_mz"])
        centers[(t, "base")] = find_center(mz, inten, M.MODEL[z][t]["base_mz"])
    return centers


def _tier_gap_da(z):
    """相邻档 M0 间距（Da），用于约束本底环不串入相邻档。"""
    mzs = [M.MODEL[z][t]["m0_mz"] for t in range(9)]
    gaps = [abs(mzs[t] - mzs[t - 1]) for t in range(1, 9)]
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
    for t in range(9):
        for anchor in ("m0", "base"):
            c = centers[(t, anchor)]
            lo, hi = c - MEAS_HALF, c + MEAS_HALF
            mask = (mz >= lo) & (mz <= hi)
            if mask.any():
                base = _local_baseline(mz, inten, c, z)
                nb = int(mask.sum())
                # 背景扣除：积分减 基线×箱数；峰顶减 基线（每箱本底）
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
    """M0 法：位置 M0(t) 含 A[t]·P0[t] + A[t+1]·Pbase[t+1]。
    轻→重 (t=8→0) 逐级扣除。返回 (amps, notes)。"""
    W = _overlap_matrix("m0", z, half)
    amps, notes = {}, {}
    for t in sorted(range(9), key=lambda x: -x):  # 8→0
        I_t = measured_m0[t]
        sub = amps.get(t + 1, 0.0) * W[t, t + 1] if (t + 1) in amps else 0.0
        denom = W[t, t]
        a = (I_t - sub) / denom if denom > 0 else 0.0
        flagged = a < 0
        if flagged:
            a = 0.0
            notes[t] = "扣除后为负，截断为 0（信号低于检出/校准偏差）"
        elif sub:
            notes[t] = f"已扣除更轻档 t{t+1} 的基峰贡献 {sub:.1f}"
        amps[t] = a
    return amps, notes


def deduce_base(measured_base, z, half=MEAS_HALF):
    """基峰法：位置 base(t)=M0(t-1)，含 A[t-1]·P0[t-1] + A[t]·Pbase[t]。
    重→轻 (t=0→8) 逐级扣除。返回 amps。"""
    W = _overlap_matrix("base", z, half)
    amps, notes = {}, {}
    for t in range(9):  # 0→8 重→轻
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


def distribute_to_impurities(content_pct, z):
    body_m0 = M.MODEL[z][0]["m0_mz"]
    rows = [{
        "tier": 0, "kind": "本体", "name": "SPH20291-Isotope1 (全标记本体)",
        "content_pct": content_pct[0], "n_members": 1, "m0_mz_real": body_m0,
    }]
    for t in range(1, 9):
        members = M.TIERS[t]["members"]
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


def _read_profile(path, target_mz, half_width_mz=0.5, frac=0.1):
    """按扩展名分派谱图读取：.lcd → lcd_io（需先 set_calibration）；其余 → mzml_io。

    两条通道返回结构一致（mz/intensity/n_scans/rt_lo/rt_hi），故上层 calc 无需感知来源。
    """
    if str(path).lower().endswith(".lcd"):
        import lcd_io
        return lcd_io.read_lcd(path, target_mz=target_mz,
                               half_width_mz=half_width_mz, frac=frac)
    return io.read_mzml(path, target_mz=target_mz,
                        half_width_mz=half_width_mz, frac=frac)


def analyze_profile(mz, inten, z, sample_name=None, half=MEAS_HALF,
                    rt_lo=None, rt_hi=None, n_scans=None):
    """从现成的轮廓谱 (mz, intensity) 做 9 档含量分析（不读文件）。

    与 analyze_sample 共用同一内核；供 .lcd（经 TOF 换算）、外部 CSV 等通道复用。
    """
    sample_name = sample_name or "profile"
    mz = np.asarray(mz, dtype=np.float64)
    inten = np.asarray(inten, dtype=np.float64)
    if len(mz) == 0:
        raise RuntimeError(f"{sample_name}: 谱图为空")

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
                "amps_raw": amps,
                "content_pct": content,
                "rows": rows,
                "notes": notes,
                "total_raw": total_raw,
            }

    # 校准残差（真实峰心 - 理论）用于报告
    cal_off = {}
    for t in range(9):
        cal_off[(t, "m0")] = centers[(t, "m0")] - M.MODEL[z][t]["m0_mz"]
        cal_off[(t, "base")] = centers[(t, "base")] - M.MODEL[z][t]["base_mz"]

    return {
        "sample": sample_name, "z": z, "meas_half_da": half,
        "rt_lo": rt_lo, "rt_hi": rt_hi, "n_scans": n_scans,
        "centers": centers, "cal_off": cal_off,
        "measured": meas, "baselines": meas["baseline"], "results": results,
    }


def analyze_sample(path, z, sample_name=None, half=MEAS_HALF):
    """读取谱图文件（.mzML / .lcd）后做 9 档含量分析。"""
    sample_name = sample_name or path
    body_m0 = M.MODEL[z][0]["m0_mz"]
    d = _read_profile(path, body_m0)
    if len(d["mz"]) == 0:
        raise RuntimeError(f"{sample_name}: 未能从谱图文件读取到谱图")
    return analyze_profile(d["mz"], d["intensity"], z, sample_name, half,
                           d["rt_lo"], d["rt_hi"], d["n_scans"])


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "../解卷积/解卷积/再处理2/sp_003/sp_003.mzML"
    z = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    res = analyze_sample(p, z, "sp_003")
    print(f"sample={res['sample']} z={z} RT={res['rt_lo']:.1f}-{res['rt_hi']:.1f}s "
          f"scans={res['n_scans']}")
    print("校准残差(ppm):", {f"t{t}": round(res['cal_off'][(t,'m0')]*1e6/M.MODEL[z][t]['m0_mz'],2) for t in range(9)})
    for key in ("m0_integral", "m0_top", "base_integral", "base_top"):
        r = res["results"][key]
        c = r["content_pct"]
        print(f"\n[{key}] 总量原始={r['total_raw']:.0f}")
        for t in range(9):
            print(f"   tier{t}: {c[t]:.3f}%  (raw {r['amps_raw'][t]:.1f})")
