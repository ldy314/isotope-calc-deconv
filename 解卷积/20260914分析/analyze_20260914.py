# -*- coding: utf-8 -*-
"""
analyze_20260914.py — 20260914 ID of SPH20291 批次 · 同位素取代率（富集度）计算  v2

方法学（沿用 ADR-0007「质心法」，含 3 处必要修正）
------------------------------------------------
1) 质量轴标定：本变体岛津 QTOF .lcd 的 Centroid Data 内，存储值 x 与 m/z 满足
       m/z = a·x^2 + c
   （TOF 平方关系 + 常数项）。用 6 个已知锚点标定，残差 ±10 ppm。
   v1 只用单项 a·x^2，残差 ±90~220 ppm，该项误差会在取代率里表现为 ~+5% 的假富集，
   必须靠天然对照扣除 —— v2 直接标定掉，使天然对照无需"调零"即落在 0 附近。

2) 窗口：必须**完整覆盖**天然形与全标记形两套同位素包络。
   取 [M0_nat − 0.5u, M0_lab + 8u]，u = 1.0033548/z（一个 13C 的 m/z 间距）。
   v1 用「质心 − 2.2 Da ~ 质心 + 2.2 Da」，左端多收 1.4 Da 纯噪声、右端截断样品包络尾部，
   使结果随电荷态漂移 1.9%。

3) 基线：窗内以质心噪声峰为主（z=4 噪声占总强度 30~47%，z=2 占 70%）。
   先减窗内 p25 作噪声基座，再以 0.5%·max 阈值剔除噪声峰。

4) 天然形零点：质心法对"平均同位素取代率"是严格线性的
   （档位质心线性检验 |偏差| ≤ 0.005%），故用三个 STD 天然对照的平均 δ 作分电荷零点，
   扣除残留的仪器/标定系统偏移。

5) 独立交叉验证：包络逐峰法（同位素序号法）
   —— 直接观察"天然形特征位"与"标记形特征位"的有无与强度比，不依赖质心。

输出：summary.csv / summary.json / <tag>_spec_rt*.csv / report.md
"""
from __future__ import annotations

import csv
import json
import os
import struct

import numpy as np
import olefile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
import sys
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "同位素杂质计算"))

import formula as formula_mod
import theo as theo_mod

DATA = r"D:\code test\chem\同位素计算及解卷积\计算数据\20260914 ID of SPH20291"
OUT = HERE

PROTON = 1.007276466812           # m_H − m_e
U13C = 1.0033548                  # 13C 相对 12C 的中性质量增量
SHIFT_TOTAL = 8.0142              # SPH20291-Isotope1 相对天然形的中性质量增量
N_LABEL = 8                       # 标记原子总数（6×13C + 2×15N）

REF_NAT = "SPH20291"
REF_LAB = "SPH20291-Isotope1"

# 6 个标定锚点：(电荷, 参照物, 实测存储值 x) —— 取自各电荷态 M0 峰位
ANCHORS = [
    (2, REF_NAT, 1764.742), (3, REF_NAT, 1441.284), (4, REF_NAT, 1248.498),
    (2, REF_LAB, 1767.244), (3, REF_LAB, 1443.324), (4, REF_LAB, 1250.266),
]

RT_LO, RT_HI = 261.0, 266.0       # 目标峰 RT 窗口（秒）
Z_LIST = (3, 2, 4)                # 电荷态优先级（信噪比降序）
WIN_LO_U, WIN_HI_U = 0.5, 8.0     # 窗口（以 u 为单位，相对 M0_nat / M0_lab）
BASE_PCT = 25.0                   # 基线分位数
THR_FRAC = 0.005                  # 峰阈值（窗内最大值的比例）

FILES = {
    "STD_002": "STD_002.lcd",
    "STD_003": "STD_003.lcd",
    "STD_005": "STD_005.lcd",
    "ISO1_004": "SYSPH20291-Isotopel-260911_004.lcd",
    "blank": "blank_001.lcd",
}
NAT_REFS = ("STD_002", "STD_003", "STD_005")


# ---------------------------------------------------------------------------
# .lcd 读取
# ---------------------------------------------------------------------------
def read_qtof_x(path):
    """返回 [(rt_sec, x_array, intensity_array), ...]；x 为文件内存储的质量轴值。"""
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
        x = np.array([struct.unpack_from("<Q", blk, 72 + k * 8)[0] / 1e12 for k in range(N)])
        fmt = {1: "<B", 2: "<H", 4: "<I"}[w]
        it = np.array([struct.unpack_from(fmt, blk, 72 + N * 8 + k * w)[0] for k in range(N)], float)
        out.append((rt, x, it))
    return out


def window_spectrum(scans, lo, hi, bin_x=1.0e-3):
    """累加 RT 窗口内的质心谱并按 x 分箱。"""
    bins = {}
    for rt, x, it in scans:
        if not (lo <= rt <= hi):
            continue
        key = np.round(x / bin_x).astype(np.int64)
        for k, v in zip(key, it):
            bins[k] = bins.get(k, 0.0) + v
    ks = np.array(sorted(bins), dtype=np.int64)
    return ks * bin_x, np.array([bins[k] for k in ks])


# ---------------------------------------------------------------------------
# 理论
# ---------------------------------------------------------------------------
_GEO = {}


def _geo(alias):
    if alias not in _GEO:
        cols, _, _ = formula_mod.resolve(alias)
        _GEO[alias] = cols
    return _GEO[alias]


def mono_mz(alias, z):
    """单同位素 m/z。"""
    mass = 0.0
    for c in _geo(alias):
        if c.is_natural:
            d = theo_mod._element_natural_distribution(c.element)
            mass += sorted(d)[0][0] * c.count
        else:
            mass += theo_mod._single_isotope_distribution(c.element, c.isotope)[0][0] * c.count
    return (mass + z * PROTON) / z


def centroid_mz(alias, z):
    """理论同位素包络的强度加权质心（m/z）。"""
    raw = theo_mod._convolve_columns(_geo(alias), tol=1e-3)
    tot = mom = 0.0
    for mass, prob in raw:
        tot += prob
        mom += prob * (mass / z + PROTON)
    return mom / tot


def calibrate():
    """最小二乘标定 m/z = a·x^2 + c。"""
    xs = np.array([x * x for _, _, x in ANCHORS])
    ys = np.array([mono_mz(al, z) for z, al, _ in ANCHORS])
    A = np.c_[xs, np.ones_like(xs)]
    (a, c), *_ = np.linalg.lstsq(A, ys, rcond=None)
    resid = [(z, al, x, y, a * x * x + c, (a * x * x + c - y) / y * 1e6)
             for (z, al, x), y in zip(ANCHORS, ys)]
    return float(a), float(c), resid


# ---------------------------------------------------------------------------
# 估计量
# ---------------------------------------------------------------------------
def centroid_cal(m, v, lo, hi, base_pct=BASE_PCT, thr=THR_FRAC):
    """扣基座 + 阈值后的强度加权质心。"""
    s = (m >= lo) & (m <= hi)
    if not s.any():
        return None, 0
    mm, vv = m[s], v[s].astype(float)
    b = float(np.percentile(vv, base_pct))
    vv = np.clip(vv - b, 0.0, None)
    if thr > 0 and vv.max() > 0:
        vv = np.where(vv >= thr * vv.max(), vv, 0.0)
    n = int((vv > 0).sum())
    if vv.sum() <= 0:
        return None, n
    return float(np.sum(vv * mm) / vv.sum()), n


def envelope_peaks(m, v, m0n, u, kmax=24, half_u=0.2):
    """按同位素序号 j 取 M0_nat + j·u 处的峰强度（±half_u·u 内最大值）。
    half_u=0.2 足以覆盖质心抖动，又不误拾相邻同位素峰（间距 = 1u）。"""
    out = []
    for j in range(0, kmax + 1):
        c = m0n + j * u
        s = (m >= c - half_u * u) & (m <= c + half_u * u)
        out.append(float(v[s].max()) if s.any() else 0.0)
    return np.array(out)


def neighbor_analysis(I, j0, floor):
    """
    包络序号法（独立于质心）。
    I[j]  = nat位 j（= M0_nat + j·u）的实测峰强度。
    j0    = 主包络起点序号：0 → 主形为天然形；8 → 主形为全标记形。
    用主形自身测得的包络形状 w 作模板，反推各邻档物种的相对丰度。
    """
    mx = float(I.max())
    if mx <= 0 or j0 + 1 >= len(I):
        return None
    w = I[j0:j0 + 8] / mx
    if w[0] <= 0:
        return None
    ks = [k for k in range(1, 7) if j0 + k < len(I) and I[j0 + k] > 0]
    if not ks:
        return None
    den = sum(w[k] ** 2 for k in ks)
    A0 = sum(w[k] * I[j0 + k] for k in ks) / den
    if A0 <= 0:
        return None
    out = dict(j0=j0, shape=[round(float(t), 4) for t in w[:8]], A0=A0,
               floor=floor, floor_ratio=floor / A0)
    # 轻 d 档（少 d 个标记原子）：其 M0 落在 j0−d
    for d in (1, 2):
        if j0 - d >= 0:
            out[f"f_m{d}"] = float(I[j0 - d] / w[0] / A0)
    # 另一参照形（位移 8 个同位素单位：天然形 ↔ 全标记形）
    jo = 8 if j0 == 0 else 0
    ks2 = [k for k in (1, 2, 3) if jo + k < len(I) and w[k] > 0]
    if ks2:
        Ao = sum(w[k] * I[jo + k] for k in ks2) / sum(w[k] ** 2 for k in ks2)
    else:
        Ao = 0.0
    out["f_other"] = float(Ao / A0)
    out["f_other_ub"] = float(floor / w[0] / A0)     # 未检出时的上限
    # 组成归一 → 平均同位素取代率（8 个标记位点的平均标记比例）
    # 各物种中"仍被标记的位点数"/8
    sites = {"lab": 1.0, "nat": 0.0,
             "m1": (N_LABEL - 1) / N_LABEL, "m2": (N_LABEL - 2) / N_LABEL}
    parts = {"lab" if j0 == 8 else "nat": 1.0}          # 主形
    for d in (1, 2):
        parts[f"m{d}"] = out.get(f"f_m{d}", 0.0)
    # 另一参照形：主形为天然形时它是全标记形（8 位全标记），反之它是天然形（0 位）
    parts["other"] = out["f_other"]
    sites["other"] = 1.0 if j0 == 0 else 0.0
    tot = sum(parts.values())
    avg = sum(parts[k] * sites[k] for k in parts) / tot * 100.0
    out["comp"] = {k: v / tot for k, v in parts.items()}
    out["avg"] = float(avg)
    out["main_frac"] = float((1.0 / tot) * 100.0)
    return out


# ---------------------------------------------------------------------------
def main():
    a, c, resid = calibrate()
    print("=" * 100)
    print(f"[标定] m/z = {a:.8e}·x^2 + ({c:+.5f})   （6 锚点最小二乘）")
    print(f"  {'z':>2} {'参照':<20} {'锚点 x':>10} {'理论 M0':>10} {'拟合':>10} {'残差 ppm':>10}")
    for z, al, x, y, yf, r in resid:
        print(f"  {z:>2} {al:<20} {x:>10.3f} {y:>10.4f} {yf:>10.4f} {r:>10.1f}")

    TH = {}
    for z in Z_LIST:
        mn, ml = centroid_mz(REF_NAT, z), centroid_mz(REF_LAB, z)
        TH[z] = dict(m_nat=mn, m_lab=ml, m0n=mono_mz(REF_NAT, z), m0l=mono_mz(REF_LAB, z),
                     u=U13C / z, delta=ml - mn)
        print(f"[理论] z={z}: 质心 nat={mn:.4f} lab={ml:.4f} Δ={ml-mn:.4f}  "
              f"M0 nat={TH[z]['m0n']:.4f} lab={TH[z]['m0l']:.4f}  u={TH[z]['u']:.4f}")

    res = {}
    for tag, fn in FILES.items():
        scans = read_qtof_x(os.path.join(DATA, fn))
        x, itn = window_spectrum(scans, RT_LO, RT_HI)
        if len(x) == 0:
            res[tag] = dict(file=fn, per_z={}, empty=True)
            print(f"\n[{tag}] {fn}  RT {RT_LO}-{RT_HI}s 无峰")
            continue
        m = a * x * x + c
        o = np.argsort(m)
        m, itn = m[o], itn[o]
        np.savetxt(os.path.join(OUT, f"{tag}_spec_rt{RT_LO:.0f}-{RT_HI:.0f}.csv"),
                   np.c_[m, itn], delimiter=",", header="m/z,intensity",
                   comments="", fmt="%.6f,%.6e")
        res[tag] = dict(file=fn, per_z={}, empty=False)
        print(f"\n[{tag}] {fn}  RT {RT_LO}-{RT_HI}s  峰数={len(m)}  m/z {m.min():.2f}-{m.max():.2f}")
        for z in Z_LIST:
            d = TH[z]
            lo, hi = d["m0n"] - WIN_LO_U * d["u"], d["m0l"] + WIN_HI_U * d["u"]
            m_meas, nb = centroid_cal(m, itn, lo, hi)
            I = envelope_peaks(m, itn, d["m0n"], d["u"])
            sel = (m >= lo) & (m <= hi)
            pos = itn[sel][itn[sel] > 0]
            floor = float(pos.min()) if pos.size else 0.0
            mx = I.max() if len(I) else 0.0
            j0 = int(np.argmax(I >= 0.05 * mx)) if mx > 0 else 0
            env = neighbor_analysis(I, j0, floor)
            res[tag]["per_z"][z] = dict(
                m_meas=m_meas, m_nat=d["m_nat"], m_lab=d["m_lab"],
                window=[lo, hi], n_used=nb, env_I=I.tolist(), env=env,
            )
            if m_meas is None:
                mm_s, raw_s = "None", "—"
            else:
                mm_s = f"{m_meas:.4f}"
                raw_s = f"{(m_meas - d['m_nat']) / d['delta'] * 100:+.2f}%"
            print(f"  z={z} 窗[{lo:.3f},{hi:.3f}] 用峰 {nb} 个  m_meas={mm_s}  "
                  f"m_nat={d['m_nat']:.4f} m_lab={d['m_lab']:.4f}  原始取代率={raw_s}")
            if env is not None:
                comp = " ".join(f"{k}={v*100:.2f}%" for k, v in env["comp"].items() if v > 1e-6)
                print(f"        [包络序号法] j0={j0}  组成: {comp}")
                print(f"        平均取代率={env['avg']:.2f}%  主形比例={env['main_frac']:.2f}%  "
                      f"该窗最小可测峰={floor:.1f}（相对主形 {env['floor_ratio']*100:.3f}%）")

    # 天然对照零点
    offset, off_sd = {}, {}
    for z in Z_LIST:
        v_ = [res[t]["per_z"][z]["m_meas"] - res[t]["per_z"][z]["m_nat"]
              for t in NAT_REFS
              if res[t]["per_z"].get(z, {}).get("m_meas") is not None]
        offset[z] = float(np.mean(v_)) if v_ else 0.0
        off_sd[z] = float(np.std(v_)) if v_ else 0.0
        print(f"\n[零点] z={z}: δ = {offset[z]:+.4f} Da（3 个 STD 天然对照，sd={off_sd[z]:.4f} Da）")

    for t in res:
        for z in Z_LIST:
            e = res[t]["per_z"].get(z)
            if e is None:
                continue
            if e["m_meas"] is None:
                e["enrich"] = e["enrich_raw"] = None
            else:
                d = TH[z]
                e["enrich_raw"] = (e["m_meas"] - d["m_nat"]) / d["delta"] * 100.0
                e["enrich"] = (e["m_meas"] - offset[z] - d["m_nat"]) / d["delta"] * 100.0

    # ---------------- 输出 ----------------
    with open(os.path.join(OUT, "summary.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sample", "file", "z", "m_meas", "m_nat_theory", "m_lab_theory",
                    "offset_Da", "取代率_质心法_%", "原始值_未扣零点_%",
                    "包络序号法_平均取代率_%", "包络序号法_主形归属",
                    "包络序号法_主形比例_%", "包络序号法_轻一档物种_%",
                    "窗口_lo", "窗口_hi", "用峰数"])
        for t in res:
            for z in Z_LIST:
                e = res[t]["per_z"].get(z)
                if e is None:
                    w.writerow([t, res[t]["file"], z] + [""] * 13)
                    continue
                env = e["env"]
                w.writerow([
                    t, res[t]["file"], z,
                    "" if e["m_meas"] is None else f"{e['m_meas']:.4f}",
                    f"{e['m_nat']:.4f}", f"{e['m_lab']:.4f}", f"{offset[z]:+.4f}",
                    "" if e["enrich"] is None else f"{e['enrich']:.3f}",
                    "" if e["enrich_raw"] is None else f"{e['enrich_raw']:.3f}",
                    "" if env is None else f"{env['avg']:.3f}",
                    "" if env is None else ("天然形" if env["j0"] == 0 else "全标记形"),
                    "" if env is None else f"{env['main_frac']:.3f}",
                    "" if env is None else f"{env.get('f_m1', 0.0) * 100:.3f}",
                    f"{e['window'][0]:.3f}", f"{e['window'][1]:.3f}", e["n_used"]])

    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(dict(
            method="质心法(centroid) + 包络序号法(envelope index)",
            calibration=dict(a=a, c=c, model="m/z = a*x^2 + c",
                             anchors=[[z, al, x] for z, al, x in ANCHORS],
                             resid_ppm=[round(r, 1) for *_, r in resid]),
            rt_window=[RT_LO, RT_HI], window_u=[WIN_LO_U, WIN_HI_U],
            base_pct=BASE_PCT, thr_frac=THR_FRAC,
            offset={str(z): offset[z] for z in Z_LIST},
            offset_sd={str(z): off_sd[z] for z in Z_LIST},
            results={t: dict(file=res[t]["file"],
                             per_z={str(z): res[t]["per_z"].get(z) for z in Z_LIST})
                     for t in res}), f, ensure_ascii=False, indent=1)

    print("\n" + "=" * 100)
    print("汇总：同位素取代率（%）")
    print(f"  {'样品':<10} " + " ".join(f"{'z=' + str(z):>22}" for z in Z_LIST))
    print(f"  {'':<10} " + " ".join(f"{'质心法':>10}{'包络序号法':>12}" for z in Z_LIST))
    for t in res:
        cells = []
        for z in Z_LIST:
            e = res[t]["per_z"].get(z)
            if e is None or e.get("enrich") is None:
                cells.append(f"{'—':>10}{'—':>12}")
            else:
                env = e["env"]
                cells.append(f"{e['enrich']:>10.2f}" +
                             (f"{env['avg']:>12.2f}" if env else f"{'—':>12}"))
        print(f"  {t:<10} " + " ".join(cells))
    print(f"\n产物目录: {OUT}")


if __name__ == "__main__":
    main()
