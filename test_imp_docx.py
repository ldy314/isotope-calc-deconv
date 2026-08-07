"""验证 imp.py 枚举 vs SPH20291 ChemDraw 参考文档（21 物种减输入本身 = 20 杂质）

参考数据：`参考数据/SPH20291-Isotop1所有可能 (1).docx`（嵌入 21 个 ChemDraw OLE 结构，
分子式文本经 olefile+CONTENTS 提取）。输入=全标记物种 C₁₂₈¹³C₆H₁₉₈N₂₆¹⁵N₂O₃₅S₂，
imp.py 应枚举出其余 20 种（¹³C k=0..6 × ¹⁵N j=0..2 全组合去输入本身）。
"""

import imp

# docx 提取的 21 种（含输入本身），平文分子式
DOCX_21 = [
    'C134H198N28O35S2', 'C13313CH198N28O35S2', 'C13213C2H198N28O35S2',
    'C13113C3H198N28O35S2', 'C13013C4H198N28O35S2', 'C12913C5H198N28O35S2',
    'C12813C6H198N28O35S2',
    'C134H198N2715NO35S2', 'C13313CH198N2715NO35S2', 'C13213C2H198N2715NO35S2',
    'C13113C3H198N2715NO35S2', 'C13013C4H198N2715NO35S2', 'C12913C5H198N2715NO35S2',
    'C12813C6H198N2715NO35S2',
    'C134H198N2615N2O35S2', 'C13313CH198N2615N2O35S2', 'C13213C2H198N2615N2O35S2',
    'C13113C3H198N2615N2O35S2', 'C13013C4H198N2615N2O35S2', 'C12913C5H198N2615N2O35S2',
    'C12813C6H198N2615N2O35S2',
]
INPUT = 'C12813C6H198N2615N2O35S2'  # 输入本身（全标记），imp 不输出


def to_plain(n):
    """imp 的 Unicode 名称 → 平文：下标数字=个数、上标=质量数、去电荷"""
    sub = {'₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
           '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9'}
    sup = {'⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
           '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9'}
    n = n.replace('⁺', '').replace('⁻', '')
    for a, b in sub.items():
        n = n.replace(a, b)
    for a, b in sup.items():
        n = n.replace(a, b)
    return n


def test_docx_reference():
    cols = [
        imp.ElementColumn('C', 'natural', 128),
        imp.ElementColumn('C', '13', 6),
        imp.ElementColumn('H', 'natural', 198),
        imp.ElementColumn('N', 'natural', 26),
        imp.ElementColumn('N', '15', 2),
        imp.ElementColumn('O', 'natural', 35),
        imp.ElementColumn('S', 'natural', 2),
    ]
    out = imp.enumerate_impurities(cols, z=0)
    got = sorted(to_plain(r['name']) for r in out)
    expected = sorted(d for d in DOCX_21 if d != INPUT)
    assert len(out) == 20, f"杂质数应为 20: {len(out)}"
    assert got == expected, (
        f"枚举与 ChemDraw 参考不一致\n"
        f"imp 有而 docx 无: {set(got) - set(expected)}\n"
        f"docx 有而 imp 无: {set(expected) - set(got)}"
    )
    print(f"[OK] imp.py 枚举 20 杂质与 SPH20291 ChemDraw 参考完全一致")


if __name__ == '__main__':
    test_docx_reference()
    print("\n全部测试通过 ✔")
