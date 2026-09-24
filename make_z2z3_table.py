# -*- coding: utf-8 -*-
"""把 SPH20291-Isotope1 的 z=2 / z=3 杂质汇总合并为并排对照表。

产出：
  杂质列表/SPH20291-Isotope1/impurity_z2_z3_对照.csv
  杂质列表/SPH20291-Isotope1/杂质列表_z2_z3.md

电荷模型：[M+zH]^z+ 质子加合，m/z = (M + z*(m_H - m_e)) / z
         m_H = 1.00782503223 u（¹H 原子质量），m_e = 0.000548579909 u
         每个电荷净加 1.007276452 u
"""
import csv
import glob
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OD = os.path.join(HERE, "杂质列表", "SPH20291-Isotope1")

PROTON = 1.00782503223 - 0.000548579909  # 1.007276452

ALIAS = "SPH20291-Isotope1"
FORMULA = "C128[13C]6H198N26[15N]2O35S2"
M_PARENT = 2831.4158  # 输入本体单同位素中性质量


def newest(pattern):
    files = sorted(glob.glob(os.path.join(OD, pattern)))
    if not files:
        raise SystemExit(f"找不到 {pattern}，请先运行 chem.py imp")
    return files[-1]


def read_summary(path):
    rows = {}
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows[int(r["idx"])] = r
    return rows


def strip_charge(s):
    return s.replace("²⁺", "").replace("³⁺", "")


def main():
    z2 = read_summary(newest("impurity_summary_z2_*.csv"))
    z3 = read_summary(newest("impurity_summary_z3_*.csv"))
    assert set(z2) == set(z3), "z2/z3 杂质集合不一致"

    parent2 = (M_PARENT + 2 * PROTON) / 2
    parent3 = (M_PARENT + 3 * PROTON) / 3

    out_csv = os.path.join(OD, "impurity_z2_z3_对照.csv")
    with io.open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "idx", "formula_neutral", "labeled_remaining", "substituted",
            "mono_neutral_mass", "mz_z2", "mz_z3",
            "delta_mz_z2_vs_parent", "delta_mz_z3_vs_parent",
            "mono_rel_abundance_%",
        ])
        for i in sorted(z2):
            a, b = z2[i], z3[i]
            m2, m3 = float(a["mz_z2"]), float(b["mz_z3"])
            w.writerow([
                i, strip_charge(a["formula"]),
                a["labeled_atoms_remaining"], a["substituted_to_natural"],
                a["mono_neutral_mass"], f"{m2:.4f}", f"{m3:.4f}",
                f"{m2 - parent2:+.4f}", f"{m3 - parent3:+.4f}",
                a["mono_rel_abundance_%"],
            ])

    L = []
    L.append(f"# {ALIAS} 同位素杂质列表（z=2 / z=3）\n")
    L.append(f"- 输入分子式：`{FORMULA}`")
    L.append("- 标记位点：第 5 位 Lys(Ac) = U-¹³C₆,¹⁵N₂-Lys（SILAC Lys8），总标记原子 **8**")
    L.append(f"- 杂质定义：8 个标记位点被天然同位素替换的全部组合，"
             f"数量 = (6+1)×(2+1) − 1 = **20**（含全天然形，不含输入本体）")
    L.append("- **电荷模型：[M+zH]ᶻ⁺ 质子加合**（电荷由加氢带来）\n"
             "  `m/z = (M + z·(m_H − m_e)) / z`，"
             "m_H = 1.00782503223 u，m_e = 0.000548579909 u，"
             "每电荷净加 **1.0072765 u**")
    L.append(f"- 输入本体 M = {M_PARENT:.4f} u → "
             f"[M+2H]²⁺ = **{parent2:.4f}**，[M+3H]³⁺ = **{parent3:.4f}**\n")

    L.append("| # | 分子式（中性） | 保留 | 替换 | 单同位素中性质量 | "
             "[M+2H]²⁺ m/z | Δ vs 本体 | [M+3H]³⁺ m/z | Δ vs 本体 | 单峰 rel% |")
    L.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for i in sorted(z2):
        a, b = z2[i], z3[i]
        m2, m3 = float(a["mz_z2"]), float(b["mz_z3"])
        L.append(
            f"| {i} | {strip_charge(a['formula'])} | "
            f"{a['labeled_atoms_remaining']} | {a['substituted_to_natural']} | "
            f"{float(a['mono_neutral_mass']):.4f} | "
            f"{m2:.4f} | {m2 - parent2:+.4f} | "
            f"{m3:.4f} | {m3 - parent3:+.4f} | "
            f"{float(a['mono_rel_abundance_%']):.2f} |"
        )

    L.append("\n## 说明\n")
    L.append("- **保留 / 替换**：仍为标记同位素的原子数 / 被换回天然的原子数，两者之和恒为 8。")
    L.append("- **排序**：全天然形（替换 8）排第 1，其余按替换数升序 —— 越靠后越接近输入本体。")
    L.append("- **单同位素中性质量与单峰 rel% 不随电荷态变化**（两个电荷态完全一致），"
             "只有 m/z 变，这是正确的：电荷态只改变 m/z 标尺，不改变分子本身。")
    L.append("- **Δ vs 本体** 呈 1/z 的整数倍分立（z=2 为 0.5 的倍数，z=3 为 0.3333 的倍数）——"
             "这正是「少一个标记原子 ≈ 少 1 Da 中性质量」在 m/z 标尺上的体现。")
    L.append("- **质量分辨率要求**：同一「替换数」内的不同组合（¹³C 换回 vs ¹⁵N 换回）"
             "中性质量仅差约 0.0063 Da，在 z=2 下 m/z 仅差 **0.0032**、z=3 下差 **0.0021**，"
             "需要 R ≈ **45 万** 才能基线分开。常规 QTOF（R≈30000，对应可分辨 0.047/0.031）"
             "下它们**必然合并成一个峰** —— 实测只能看到按替换数分档的 **8 组**包络"
             "（替换 1~8，外加本体），而非 20 个独立峰。")
    L.append("- **加合质子的同位素贡献未计入卷积**：加合的 z 个 H 按纯质子（固定质量）处理。"
             "若严格按天然氢计，会额外贡献 M+1 约 0.023%（z=2）/ 0.035%（z=3），"
             "低于默认丰度阈值 0.05%，可忽略。")
    L.append("\n## 产物文件\n")
    L.append("| 文件 | 内容 |")
    L.append("|---|---|")
    L.append("| `impurity_summary_z2_*.csv` / `_z3_*.csv` | 每杂质一行的汇总（20 行） |")
    L.append("| `impurity_peaks_z2_*.csv` / `_z3_*.csv` | 逐杂质逐峰完整同位素谱（1000 行） |")
    L.append("| `impurity_list_z2_*.txt` / `_z3_*.txt` | 4 行清单格式（可回灌引擎） |")
    L.append("| `impurity_z2_z3_对照.csv` | z=2/z=3 并排对照（本表的 CSV 版） |")

    out_md = os.path.join(OD, "杂质列表_z2_z3.md")
    with io.open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    print("已写出:")
    print(" ", out_csv)
    print(" ", out_md)
    print()
    print(f"本体 [M+2H]2+ = {parent2:.4f}   [M+3H]3+ = {parent3:.4f}")


if __name__ == "__main__":
    main()
