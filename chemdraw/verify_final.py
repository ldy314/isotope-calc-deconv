# -*- coding: utf-8 -*-
"""最终核验：甲基色氨酸位置、二酸链长、PEG 单元数、末端修饰、与 MS 数据的对应。"""
import sys
from rdkit import Chem
from rdkit.Chem import Descriptors
from cdxml_to_rdkit import load, build

path = sys.argv[1]
root = load(path); page = root.find("page")
molA = build(page.find("fragment"))                       # 带标记
molB = build(page.find("group").find("fragment"))         # 天然

print("=" * 88)
print("1) 甲基吲哚（残基4）取代位置 —— 在完整分子上用 SMARTS 判定")
print("=" * 88)
pats = {
    "2-Me-Trp": "[CH3]c1[nH]c2ccccc2c1C",
    "4-Me-Trp": "[CH3]c1cccc2[nH]cc(C)c12",
    "5-Me-Trp": "[CH3]c1ccc2[nH]cc(C)c2c1",
    "6-Me-Trp": "[CH3]c1ccc2c(c1)[nH]cc2C",
    "7-Me-Trp": "[CH3]c1cccc2c1[nH]cc2C",
}
for name, sma in pats.items():
    q = Chem.MolFromSmarts(sma)
    hit = molB.HasSubstructMatch(q) if q else None
    print(f"   {name:<10}: {'✔ 命中' if hit else '—'}")

print("\n" + "=" * 88)
print("2) 脂肪二酸链长 & 侧链连接子单元")
print("=" * 88)
for n in range(14, 23):
    sma = "OC(=O)" + "C" * (n - 2) + "C(=O)N"
    q = Chem.MolFromSmarts(sma)
    if q and molB.HasSubstructMatch(q):
        print(f"   HOOC-(CH2)n-CO-NH  链总碳数 = {n}  ✔  (即 C{n} 二元酸)")
print("   AEEA 单元 [-C(=O)CH2-O-CH2CH2-O-CH2CH2-NH-] 出现次数 :",
      len(molB.GetSubstructMatches(Chem.MolFromSmarts("C(=O)COCCOCCN"))))
print("   1,2,3-三氮唑环 数量 :", len(molB.GetSubstructMatches(Chem.MolFromSmarts("c1cnnn1"))))
print("   γ-Glu (侧链羧基成酰胺、α-COOH 游离) :",
      len(molB.GetSubstructMatches(Chem.MolFromSmarts("NC(C(=O)O)CCC(=O)N"))))
print("   二硫键 S-S :", len(molB.GetSubstructMatches(Chem.MolFromSmarts("[SX2][SX2]"))))
print("   偕二甲基-半胱氨酸(青霉胺 Pen) :",
      len(molB.GetSubstructMatches(Chem.MolFromSmarts("[CH3]C([CH3])([SX2])C"))))
print("   N-乙酰基 (CH3-CO-NH-) 总数 :",
      len(molB.GetSubstructMatches(Chem.MolFromSmarts("[CH3]C(=O)[NX3]"))))
print("   一级酰胺 C(=O)NH2 总数 :",
      len(molB.GetSubstructMatches(Chem.MolFromSmarts("C(=O)[NH2]"))))
print("   游离羧酸 COOH 总数 :",
      len(molB.GetSubstructMatches(Chem.MolFromSmarts("C(=O)[OH]"))))
print("   伯胺 -CH2NH2 :", len(molB.GetSubstructMatches(Chem.MolFromSmarts("[CH2][NH2]"))))

print("\n" + "=" * 88)
print("3) 质量学核对（与之前 MS 解卷积工作对齐）")
print("=" * 88)
PROTON = 1.007276466
mA, mB = Descriptors.ExactMolWt(molA), Descriptors.ExactMolWt(molB)
avgA, avgB = Descriptors.MolWt(molA), Descriptors.MolWt(molB)
print(f"   天然  SPH20291        单同位素 M = {mB:.4f}   平均 MW = {avgB:.2f}")
print(f"   标记  SPH20291-Iso1   单同位素 M = {mA:.4f}   平均 MW = {avgA:.2f}")
print(f"   质量差 Δ = {mA-mB:.4f}  (理论 6×¹³C + 2×¹⁵N = "
      f"{6*(13.0033548-12.0)+2*(15.0001089-14.0030740):.4f})")
for z in (1, 2, 3):
    print(f"   z={z}: 天然 [M+{z}H]{z}+ 单同位素 m/z = {(mB+z*PROTON)/z:9.4f} | "
          f"平均(质心近似) = {(avgB+z*1.00794)/z:9.4f} || "
          f"标记 单同位素 = {(mA+z*PROTON)/z:9.4f}")

print("\n" + "=" * 88)
print("4) 两结构骨架一致性")
print("=" * 88)
m0 = build(page.find("fragment"), keep_isotope=False)
print("   A 抹去同位素后的 canonical SMILES == B ? ",
      Chem.MolToSmiles(m0) == Chem.MolToSmiles(molB))
print("   两者原子数/键数一致 ? ",
      molA.GetNumAtoms() == molB.GetNumAtoms() and molA.GetNumBonds() == molB.GetNumBonds())
