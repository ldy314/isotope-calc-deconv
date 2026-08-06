# -*- coding: utf-8 -*-
"""从 molmass 提取 19 种元素的全部稳定同位素 → 生成长表数据（供 Excel IsotopeData sheet 使用）"""
import sys, json
import molmass

ELEMENTS = ['H', 'C', 'N', 'O', 'F', 'Si', 'P', 'S', 'Cl', 'Br', 'I',
            'Na', 'Mg', 'Al', 'K', 'Ca', 'Fe', 'Cu', 'Zn']

rows = []
for sym in ELEMENTS:
    el = molmass.ELEMENTS[sym]
    for mass_number in el.isotopes:
        iso = el.isotopes[mass_number]
        rows.append({
            'element': sym,
            'mass_number': mass_number,
            'mass': iso.mass,
            'abundance': iso.abundance,
        })

print(f"共 {len(rows)} 条同位素记录")
for r in rows:
    print(f"{r['element']:>2} {r['mass_number']:>3}  {r['mass']:.9f}  {r['abundance']:.6f}")

with open(r'D:\code test\chem\同位素计算及解卷积\isotope_data.json', 'w', encoding='utf-8') as f:
    json.dump(rows, f, ensure_ascii=False, indent=2)
print("\n已保存 isotope_data.json")
