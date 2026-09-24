# -*- coding: utf-8 -*-
"""
为 SPH20291-Isotope1 计算所有同位素取代杂质（复刻 ExactMass_Impurities_Calc.xlsm 的
“列举并计算”功能：imp.py 枚举 + theo.py 逐杂质算理论同位素峰）。

SPH20291-Isotope1 分子式（同位素修饰版）：
    C natural 128 | C 13 6 | H natural 198 | N natural 26 | N 15 2 | O natural 35 | S natural 2
电荷 z=2（与 SPH20291 的 LC-MS 分析一致；z=1 仅供对照）。
"""
import sys, importlib.util, csv, os, datetime

DIR = r"D:\code test\chem\同位素计算及解卷积"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m          # 注册后再 exec，避免 dataclass 找不到模块
    spec.loader.exec_module(m)
    return m


imp = _load("imp_mod", os.path.join(DIR, "imp.py"))
theo = _load("theo_mod", os.path.join(DIR, "theo.py"))

Z = 2            # 主电荷（与 SPH20291 MS 一致）
TOL = 0.001
MIN_AB = 0.05
TOP_N = 50

# ---- 输入：SPH20291-Isotope1 分子式 ----
RAW = [
    ("C", "natural", 128),
    ("C", "13", 6),
    ("H", "natural", 198),
    ("N", "natural", 26),
    ("N", "15", 2),
    ("O", "natural", 35),
    ("S", "natural", 2),
]
cols = [imp.ElementColumn(e, i, c) for e, i, c in RAW]

print("输入分子式:", imp.formula_name([(e, i, c) for e, i, c in RAW], 0))
print("  修饰列: ¹³C×6, ¹⁵N×2  -> 总修饰原子 = 8")

impurities = imp.enumerate_impurities(cols, z=Z)
print(f"枚举得到杂质数: {len(impurities)} （含全天然形，不含输入本身）")

# ---- 逐杂质计算同位素峰 ----
summary = []
peak_rows = []
for idx, r in enumerate(impurities, 1):
    tcols = [theo.ElementColumn(e, i, c) for e, i, c in zip(r["elements"], r["isos"], r["counts"])]
    peaks = theo.compute_theoretical_spectrum(tcols, z=Z, tol=TOL, top_n=TOP_N, min_ab=MIN_AB)
    mono = peaks[0] if peaks else None
    # z=1 对照 m/z
    peaks1 = theo.compute_theoretical_spectrum(tcols, z=1, tol=TOL, top_n=1, min_ab=0)
    mz1 = peaks1[0].mz if peaks1 else None
    summary.append(dict(
        idx=idx, name=r["name"], mod_total=r["mod_total"],
        replaced_total=r["replaced_total"],
        mono_mass=mono.mass if mono else None,
        mz_z2=mono.mz if mono else None,
        mz_z1=mz1,
        mono_rel=mono.rel_abundance if mono else None,
    ))
    for p in peaks:
        peak_rows.append([
            idx, r["name"], r["mod_total"], r["replaced_total"],
            "|".join(r["elements"]), "|".join(r["isos"]), "|".join(str(c) for c in r["counts"]),
            p.mass, p.mz, round(p.rel_abundance, 4),
        ])

# ---- 输出文件 ----
ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = DIR
sum_csv = os.path.join(out_dir, f"impurity_summary_z{Z}_{ts}.csv")
peak_csv = os.path.join(out_dir, f"impurity_peaks_z{Z}_{ts}.csv")   # 同 Calc 表输出风格
imp_txt = os.path.join(out_dir, f"impurity_list_z{Z}_{ts}.txt")    # 4 行/杂质，供 Excel 回填

with open(sum_csv, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["idx", "formula", "labeled_atoms_remaining", "substituted_to_natural",
                "mono_neutral_mass", f"mz_z{Z}", "mz_z1", "mono_rel_abundance_%"])
    for s in summary:
        w.writerow([s["idx"], s["name"], s["mod_total"], s["replaced_total"],
                    f"{s['mono_mass']:.4f}", f"{s['mz_z2']:.4f}",
                    f"{s['mz_z1']:.4f}" if s["mz_z1"] else "",
                    f"{s['mono_rel']:.4f}"])

with open(peak_csv, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["impurity_idx", "formula", "labeled_atoms_remaining", "substituted_to_natural",
                "elements", "isotopes", "counts", "neutral_mass", f"mz_z{Z}", "rel_abundance_%"])
    for row in peak_rows:
        w.writerow(row)

with open(imp_txt, "w", encoding="utf-8") as f:
    f.write(f"# imp v1 | total {len(impurities)} | z {Z}\n")
    for r in impurities:
        f.write(imp._impurity_block(r) + "\n")

print(f"\n已写出:")
print(f"  杂质汇总  : {os.path.basename(sum_csv)}")
print(f"  逐峰 CSV  : {os.path.basename(peak_csv)}  ({len(peak_rows)} 行 = 杂质×峰)")
print(f"  4行/杂质  : {os.path.basename(imp_txt)}")

print("\n" + "=" * 96)
print(f"{'#':>2}  {'分子式 (z={Z})':<28} {'保留标记':>6} {'替换天然':>7} {'单同位素质量':>14} {'m/z z'+str(Z):>12} {'单峰相对%':>10}")
print("-" * 96)
for s in summary:
    print(f"{s['idx']:>2}  {s['name']:<28} {s['mod_total']:>6} {s['replaced_total']:>7} "
          f"{s['mono_mass']:>14.4f} {s['mz_z2']:>12.4f} {s['mono_rel']:>10.3f}")
