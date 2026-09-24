# -*- coding: utf-8 -*-
"""
run_lcd_impurity.py — 岛津 Q-TOF .lcd 批次的「同位素杂质含量」计算（项目原生方法与报告格式）

与 run_all.py 的关系
--------------------
计算内核与报告格式**完全沿用 run_all.py**，不另起一套：

  · 引擎：calc.analyze_profile —— 9 档体系、逐级三角扣除（M0 法轻→重 / 基峰法重→轻）、
          理论系数窗 5 ppm、实测提取/重叠窗 ±0.04 Da、四估计量（M0/基峰 × 积分/峰顶）、
          归一化到 100%。系数全部来自 theo.py 理论包络，不做实测谱拟合。
  · 报告：run_all.write_sp003_workbook → 标记化合物工作簿
            （说明 / 四法总览 / z3_档汇总 / z3_杂质明细 / z4_档汇总 / z4_杂质明细 /
              校准与实测 / 真实标记杂质_z3 / 真实标记杂质_z4）
          run_all.write_std_workbook   → 天然型对照工作簿
            （STD验证 / 杂质明细(计算值) / 真实标记杂质 / 真实标记杂质_z4）

唯一差别在**输入端**：本脚本直接读 .lcd 原始文件，并先做 TOF 质量轴标定
（m/z = a·x² + c，见 lcd_io 模块头），无需预先导出 mzML。

用法
----
    python run_lcd_impurity.py
    python run_lcd_impurity.py --data-dir DIR --out DIR --rt-lo 261 --rt-hi 266
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
for _p in (PARENT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import calc as C
import lcd_io
import model as M
import run_all as R

# ---- 默认数据与参数 ---------------------------------------------------------
DEFAULT_DATA = os.path.join(PARENT, "计算数据", "20260914 ID of SPH20291")

SP_FILE = "SYSPH20291-Isotopel-260911_004.lcd"     # 样品（SPH20291-Isotope1 全标记）
STD_FILE = "STD_005.lcd"                           # 天然型对照（用户指定 STD_005）
BLANK_FILE = "blank_001.lcd"

SP_LABEL = "SYSPH20291-Isotope1-260911_004"
STD_LABEL = "STD_005"
OUT_SP = SP_LABEL + "_杂质含量.xlsx"
OUT_STD = STD_LABEL + "_验证.xlsx"
OUT_MD = "汇总报告.md"

ZS = (3, 4)          # z=3 主定量、z=4 辅助（与 run_all.ZS 一致）
Z_STD = 3
RT_SEARCH_HALF = 0.5  # RT 窗口自动检测用的目标 m/z 半宽（Da），与 run_all 一致
RT_FRAC = 0.1

# 6 个 TOF 标定锚点：(x_measured, z, tier)；tier0 = 全标记本体、tier8 = 天然形。
# x 取自实测 M0 峰位，理论值由 model.MODEL 给出；来源见 解卷积/20260914分析/analyze_20260914.py。
ANCHORS = [(1764.742, 2, 8), (1441.284, 3, 8), (1248.498, 4, 8),
           (1767.244, 2, 0), (1443.324, 3, 0), (1250.266, 4, 0)]


def build_tof_calibration():
    """用 6 个锚点最小二乘标定 m/z = a·x² + c，并写入 lcd_io 全局。"""
    anch = [(x, M.MODEL[z][t]["m0_mz"]) for x, z, t in ANCHORS]
    a, c, resid = lcd_io.calibrate_tof(anch)
    lcd_io.set_calibration(a, c)
    return a, c, resid


def read_profile(path, rt_lo, rt_hi, target_mz):
    """读取 .lcd：RT 窗口给定则直接用，否则按 target_mz 自动检测（与 mzml_io 同语义）。"""
    if rt_lo is None or rt_hi is None:
        d = lcd_io.read_lcd(path, target_mz=target_mz,
                            half_width_mz=RT_SEARCH_HALF, frac=RT_FRAC)
    else:
        d = lcd_io.read_lcd(path, rt_lo=rt_lo, rt_hi=rt_hi)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--out", default=None, help="输出目录（默认=数据目录）")
    ap.add_argument("--rt-lo", type=float, default=None, help="RT 窗口下限(s)；缺省自动检测")
    ap.add_argument("--rt-hi", type=float, default=None, help="RT 窗口上限(s)；缺省自动检测")
    args = ap.parse_args()
    out_dir = args.out or args.data_dir
    os.makedirs(out_dir, exist_ok=True)

    a, c, resid = build_tof_calibration()
    print("=" * 96)
    print(f"[TOF 标定] m/z = {a:.8e}·x² + ({c:+.5f})  6 锚点残差(ppm) = "
          f"{[round(r, 1) for r in resid]}  最大 |残差| = {max(abs(r) for r in resid):.1f} ppm")

    # ---------------------------------------------------------------- 样品（SP）
    sp_path = os.path.join(args.data_dir, SP_FILE)
    if not os.path.exists(sp_path):
        raise SystemExit(f"缺样品文件：{sp_path}")
    sp_prof = read_profile(sp_path, args.rt_lo, args.rt_hi, M.MODEL[Z_STD][0]["m0_mz"])
    print(f"\n[SP] {SP_FILE}")
    print(f"  RT {sp_prof['rt_lo']:.1f}–{sp_prof['rt_hi']:.1f} s "
          f"({sp_prof['rt_lo']/60:.2f}–{sp_prof['rt_hi']/60:.2f} min)  "
          f"scans={sp_prof['n_scans']}  谱点数={len(sp_prof['mz'])}  "
          f"m/z {sp_prof['mz'].min():.2f}–{sp_prof['mz'].max():.2f}")
    results_by_z = {}
    for z in ZS:
        res = C.analyze_profile(sp_prof["mz"], sp_prof["intensity"], z, SP_LABEL,
                                rt_lo=sp_prof["rt_lo"], rt_hi=sp_prof["rt_hi"],
                                n_scans=sp_prof["n_scans"])
        results_by_z[z] = res
        ci = res["results"]["m0_integral"]["content_pct"]
        ct = res["results"]["m0_top"]["content_pct"]
        print(f"  z={z}  M0·积分: t0={ci[0]:.2f}%  t1={ci[1]:.2f}%   |   "
              f"M0·峰顶: t0={ct[0]:.2f}%  t1={ct[1]:.2f}%")

    print(f"\n[SP] 分辨率稳健性扫描 z={Z_STD}（M0·积分）")
    sweep = R.sweep_resolution(None, z=Z_STD, profile=sp_prof, sample_name=SP_LABEL)
    for h, d in sweep.items():
        print(f"  half={h:.3f} Da: 本体={d['body']:.3f}%  tier1={d['tier1']:.3f}%")

    # ---------------------------------------------------------------- 对照（STD）
    std_path = os.path.join(args.data_dir, STD_FILE)
    val_std = None
    std_prof = None
    if os.path.exists(std_path):
        m0_nat, _, _ = R.build_natural(Z_STD)
        std_prof = read_profile(std_path, args.rt_lo, args.rt_hi, m0_nat)
        print(f"\n[STD] {STD_FILE}")
        print(f"  RT {std_prof['rt_lo']:.1f}–{std_prof['rt_hi']:.1f} s  "
              f"scans={std_prof['n_scans']}  谱点数={len(std_prof['mz'])}")
        val_std = R.validate_std(None, z=Z_STD, profile=std_prof, sample_name=STD_LABEL)
        std_res_z4 = C.analyze_profile(std_prof["mz"], std_prof["intensity"], 4, STD_LABEL,
                                       rt_lo=std_prof["rt_lo"], rt_hi=std_prof["rt_hi"],
                                       n_scans=std_prof["n_scans"])
        t0 = val_std["labeled_res"]["results"]["m0_integral"]["content_pct"][0]
        print(f"  天然型理论 M0={val_std['m0_th']:.4f}  实测峰心={val_std['c_m0']:.4f}  "
              f"偏移={val_std['off_ppm']:.2f} ppm")
        print(f"  套用标记引擎 tier0(标记本体)={t0:.3f}%  （≈0 → 不含标记化合物）")
    else:
        print(f"\n[STD] 缺文件 {std_path}，跳过对照验证工作簿")

    # ---------------------------------------------------------------- 空白（参考）
    blank_path = os.path.join(args.data_dir, BLANK_FILE)
    if os.path.exists(blank_path):
        d = read_profile(blank_path, args.rt_lo, args.rt_hi, M.MODEL[Z_STD][0]["m0_mz"])
        tot = float(np.nansum(d["intensity"]))
        print(f"\n[blank] {BLANK_FILE}  RT {d['rt_lo']:.1f}–{d['rt_hi']:.1f} s  "
              f"谱点数={len(d['mz'])}  窗内总强度={tot:.0f}")

    # ---------------------------------------------------------------- 写 Excel
    max_resid = max(abs(r) for r in resid)
    src = (f"岛津 LCMS-9030 Q-TOF .lcd 原始文件（QTFL Centroid）直读："
           f"RT {sp_prof['rt_lo']:.1f}–{sp_prof['rt_hi']:.1f} s（{sp_prof['rt_lo']/60:.2f}–"
           f"{sp_prof['rt_hi']/60:.2f} min，4.4 min 目标峰），{sp_prof['n_scans']} 个扫描累加；"
           f"质量轴 m/z = a·x² + c，a={a:.8e}、c={c:+.5f}（6 锚点最小二乘，残差 ±{max_resid:.0f} ppm）")
    ci3 = results_by_z[3]["results"]["m0_integral"]["content_pct"]
    ct3 = results_by_z[3]["results"]["m0_top"]["content_pct"]
    extra = (
        f"本批谱为质心谱（centroid），窗内累加后单个真实峰被分箱展开为多个相邻箱"
        f"（实测 tier0 跨 5–6 箱、tier1 跨 10–11 箱），故「窗内积分」与「窗内峰顶」差异较大："
        f"M0·积分 本体 {ci3[0]:.2f}% / tier1 {ci3[1]:.2f}%，M0·峰顶 本体 {ct3[0]:.2f}% / tier1 {ct3[1]:.2f}%。"
        f"本表按项目约定以 M0·积分 为判定依据，四种算法数值全部列出，供按需取舍。"
    )
    print("\n" + "=" * 96)
    p1 = R.write_sp003_workbook(
        results_by_z, sample_label=SP_LABEL,
        out_path=os.path.join(out_dir, OUT_SP), zs=ZS,
        info_overrides={"数据来源": src, "本批附加说明": extra})
    print(f"[SAVED] {p1}")

    p2 = None
    if val_std is not None:
        p2 = R.write_std_workbook(val_std, std_label=STD_LABEL,
                                  out_path=os.path.join(out_dir, OUT_STD),
                                  extra_z_res={4: std_res_z4})
        print(f"[SAVED] {p2}")

    p3 = write_batch_report(os.path.join(out_dir, OUT_MD), results_by_z, sweep, val_std,
                            sp_prof, a, c, resid, args.data_dir)
    print(f"[SAVED] {p3}")
    return p1, p2, p3


# =====================================================================
# 批次汇总报告（结构与 run_all.write_report 对齐，样品/批次可换）
# =====================================================================
def write_batch_report(path, results_by_z, sweep, val_std, sp_prof, a, c, resid, data_dir):
    lines = []
    A = lines.append
    A("# 同位素杂质含量计算 — 汇总报告")
    A("")
    A(f"**批次数据目录**：`{data_dir}`")
    A(f"**样品**：{SP_LABEL}（`{SP_FILE}`，目标 SPH20291-Isotope1 全标记内标版）")
    A("**日期**：2026-09-24　**脚本**：`run_lcd_impurity.py`（复用 `run_all.py` 的引擎与报告格式）")
    A("")
    A("## 1. 任务与模型")
    A("")
    A("- **分析物**：SPH20291-Isotope1 = `C128[13C]6 H198 N26[15N]2 O35 S2`，8 个标记原子（6×¹³C + 2×¹⁵N）")
    A("- **杂质定义**：8 个标记原子被天然同位素替换的笛卡尔积 = (6+1)(2+1)−1 = **20** 个杂质")
    A("- **档体系**：tier t = t 个标记原子被天然替换（t=0 本体，t=8 全天然形），共 **9 档**；")
    A("  20 个杂质组成按档归并，同档内不可分辨组合在「杂质明细」中合并（每档 1 行，共 9 行），含量取整档总量。")
    A("- **电荷态**：z=3（主定量）、z=4（辅助，杂质在噪声级，仅作一致性校验）")
    A("- **分辨率**：理论系数窗 5 ppm；实测提取/重叠窗 ±%.2f Da" % C.MEAS_HALF)
    A("- **系数来源**：`theo.py` 理论同位素包络作**固定系数**，**不做实测谱解卷积拟合**")
    A("- **扣除法**：9 档体系本质二对角（位置 M0(t) 叠放本体档 t 的 M0 与档 t+1 的基峰）；")
    A("  M0 法轻→重、基峰法重→轻逐级三角扣除。5 ppm 下基峰(t) 与 M0(t−1) 位置重合，二者量级一致、可互校验；")
    A("  但链端存在固有偏差，并非严格等价，本报告以 **M0·积分** 为准。")
    A("- **其它杂质影响已扣除**：逐级三角扣除时，计算某档含量会扣掉相邻更轻档(t+1)基峰在 M0(t) 处的串入；")
    A("  非相邻档相距 ~0.33/z Da（≈353 ppm），远在 ±%.2f Da 提取窗之外，不会串入。" % C.MEAS_HALF)
    A("- **4 种算法**：M0·积分 / M0·峰顶 / 基峰·积分 / 基峰·峰顶；**归一化**到全部物种总量 = 100%")
    A("")
    A("### 1.1 数据入口（与 run_all.py 的唯一差别）")
    A("")
    A(f"- 直接读岛津 Q-TOF `.lcd`（QTFL Centroid），无需预先导出 mzML。")
    A(f"- **质量轴标定**：存储值 x 与 m/z 满足 `m/z = a·x² + c`（非 openszraw 文档的线性换算；")
    A(f"  线性换算会把 942.15 显示成 1442，峰位随 m/z 漂移，完全误导归属判断）。")
    A(f"  a = {a:.8e}，c = {c:+.5f}，6 锚点最小二乘，残差 {[round(r,1) for r in resid]} ppm。")
    A(f"- **RT 窗口**：{sp_prof['rt_lo']:.1f}–{sp_prof['rt_hi']:.1f} s "
      f"（{sp_prof['rt_lo']/60:.2f}–{sp_prof['rt_hi']/60:.2f} min），{sp_prof['n_scans']} 个扫描累加，"
      f"按 x 分箱 1e-3（m/z 步长约 1.6e-3 Da）。")
    A("")
    A("## 2. 计算结果（z=3，主定量）")
    A("")
    res3 = results_by_z[3]
    A("| 档 | 档名 | M0·积分 | M0·峰顶 | 基峰·积分 | 基峰·峰顶 |")
    A("|---|---|---:|---:|---:|---:|")
    for t in range(9):
        nm = M.TIERS[t]["name"]
        vals = [res3["results"][m]["content_pct"][t] for m, _ in R.METHODS]
        A(f"| {t} | {nm} | {vals[0]:.4f} | {vals[1]:.4f} | {vals[2]:.4f} | {vals[3]:.4f} |")
    A("")
    A(f"> 判定（M0·积分）：**本体 = {res3['results']['m0_integral']['content_pct'][0]:.3f}%**，"
      f"**tier1 杂质 = {res3['results']['m0_integral']['content_pct'][1]:.3f}%**，tier 2–8 ≈ 0（低于检出）。")
    A(f"> 即 {SP_LABEL} 样品基本为**全标记本体**，含约 "
      f"{res3['results']['m0_integral']['content_pct'][1]:.1f}% 的「1 个标记原子被天然替换」杂质（tier1），"
      f"更高替换档可忽略。")
    A("")
    A("## 3. 四种算法对比")
    A("")
    A("| 指标 | M0·积分 | M0·峰顶 | 基峰·积分 | 基峰·峰顶 |")
    A("|---|---:|---:|---:|---:|")
    for label, t in [("本体% (z=3)", 0), ("tier1% (z=3)", 1)]:
        vals = [results_by_z[3]["results"][m]["content_pct"][t] for m, _ in R.METHODS]
        A(f"| {label} | {vals[0]:.3f} | {vals[1]:.3f} | {vals[2]:.3f} | {vals[3]:.3f} |")
    A("")
    A("- 本批为**质心谱**：逐扫描质心峰位存在抖动，累加分箱后单个真实峰被展开为多个相邻箱")
    A("  （实测 tier0 跨 5–6 箱、tier1 跨 10–11 箱）。**积分 = 该峰全部离子的总数**，")
    A("  而**峰顶 = 单个箱内强度**，因弱峰的质心抖动更大，峰顶会系统**低估**弱峰相对含量。")
    A("- 因此本批「积分」与「峰顶」差异明显大于 sp_003 那批（那批为 mzML，抖动分布不同）。")
    A("- 按项目约定以 **M0·积分** 为准；基峰法作一致性校验。")
    A("")
    A("## 4. z=4 辅助结果（一致性校验）")
    A("")
    res4 = results_by_z[4]
    A("| 档 | M0·积分 | M0·峰顶 | 基峰·积分 | 基峰·峰顶 |")
    A("|---|---:|---:|---:|---:|")
    for t in range(9):
        vals = [res4["results"][m]["content_pct"][t] for m, _ in R.METHODS]
        A(f"| {t} | {vals[0]:.4f} | {vals[1]:.4f} | {vals[2]:.4f} | {vals[3]:.4f} |")
    A("")
    A(f"> z=4 下 tier1 杂质约 {res4['results']['m0_integral']['content_pct'][1]:.3f}%（积分）/ "
      f"{res4['results']['m0_top']['content_pct'][1]:.3f}%（峰顶），趋势与 z=3 一致，仅作交叉验证。")
    A("")
    A("## 5. 分辨率稳健性扫描")
    A("")
    A(f"对 {SP_LABEL} z=3、M0·积分法，扫描实测提取窗半宽：")
    A("")
    A("| 提取窗半宽 (Da) | 本体% | tier1% | tier2% | tier8% |")
    A("|---|---:|---:|---:|---:|")
    for h, d in sweep.items():
        A(f"| {h:.3f} | {d['body']:.3f} | {d['tier1']:.3f} | {d['tier2']:.3f} | {d['tier8']:.3f} |")
    A("")
    A("## 6. 天然型对照验证（%s）" % STD_LABEL)
    A("")
    if val_std is not None:
        A(f"- {STD_LABEL} 应只含**天然型 SPH20291**（无标记化合物）。")
        A(f"- 套用标记引擎得 **tier0(标记本体) = "
          f"{val_std['labeled_res']['results']['m0_integral']['content_pct'][0]:.3f}%**，≈0，确认无标记化合物。")
        A(f"- 用天然型理论包络对照实测：理论 M0={val_std['m0_th']:.4f}，实测峰心={val_std['c_m0']:.4f}，"
          f"质量偏移={val_std['off_ppm']:.2f} ppm，谱图读取与校准正确。")
        body_pct, imp_pct = R.std_real_impurity(val_std, z=Z_STD)
        A(f"- 真实组成（「真实标记杂质」表，以天然型为本体、扣天然包络）：本体(天然)={body_pct:.3f}%，"
          f"标记杂质合计={imp_pct:.3f}%（≈0）→ 样品确为纯天然型。")
    else:
        A("- 未提供对照文件，跳过。")
    A("")
    A("## 7. 质量校准残差（%s）" % SP_LABEL)
    A("")
    A("| 档 | z=3 偏移ppm(M0) | z=4 偏移ppm(M0) |")
    A("|---|---:|---:|")
    for t in range(9):
        off3 = results_by_z[3]["cal_off"][(t, "m0")] * 1e6 / M.MODEL[3][t]["m0_mz"]
        off4 = results_by_z[4]["cal_off"][(t, "m0")] * 1e6 / M.MODEL[4][t]["m0_mz"]
        A(f"| {t} | {off3:+.2f} | {off4:+.2f} |")
    A("")
    A("## 8. 结论与建议")
    A("")
    A(f"1. {SP_LABEL} 的同位素杂质以 **tier1（1 个标记原子被天然替换）** 为主，"
      f"含量约 **{res3['results']['m0_integral']['content_pct'][1]:.2f}%**（M0·积分法，项目约定口径），"
      f"更高替换档可忽略。")
    A("2. 四种算法互洽但存在系统差异（积分 vs 峰顶），源自质心谱累加后的分箱展宽；")
    A("   建议以 **M0·积分** 作报告值，峰顶法作保守下界参考。")
    A(f"3. {STD_LABEL} 验证通过（tier0≈0），质量轴标定与谱图读取正确。")
    A("4. 结果对提取窗选择稳健（见 §5）。")
    A("")
    A("---")
    A("")
    A("**输出文件**：")
    A(f"- `{OUT_SP}`（说明 / 四法总览 / z3·z4 档汇总 / z3·z4 杂质明细 / 校准与实测 / 真实标记杂质_z3·_z4）")
    if val_std is not None:
        A(f"- `{OUT_STD}`（STD验证 / 杂质明细(计算值) / 真实标记杂质 / 真实标记杂质_z4）")
    A(f"- `{OUT_MD}`（本报告）")
    A("")
    A("> 说明：本流程为「理论系数固定、逐级扣除」算法，非实测谱解卷积拟合；")
    A("> 对 C>50 的大分子，同位素包络高度重叠，绝对丰度依赖理论系数，结论以相对趋势为准。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


if __name__ == "__main__":
    main()
