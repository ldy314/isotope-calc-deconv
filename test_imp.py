# -*- coding: utf-8 -*-
"""imp 杂质枚举引擎的单元测试"""
import sys
sys.path.insert(0, r'D:\code test\chem\同位素计算及解卷积')
import imp

def test_c2d3():
    """C2D3（C natural 2 + H 2 3）→ 3 个杂质（k=0..3 排除输入本身）
    排序：全天然第一，其余按替换总数升序（与用户例子 C2H3→C2HD2→C2H2D 一致）：
    C₂H₃⁺(全天然,替换3) → C₂HD₂⁺(替换1个D,剩2D) → C₂H₂D⁺(替换2个D,剩1D)"""
    cols = [
        imp.ElementColumn('C', 'natural', 2),
        imp.ElementColumn('H', '2', 3),
    ]
    out = imp.enumerate_impurities(cols, z=1)
    names = [r['name'] for r in out]
    assert names == ['C₂H₃⁺', 'C₂HD₂⁺', 'C₂H₂D⁺'], f"实际: {names}"
    assert [r['replaced_total'] for r in out] == [3, 1, 2], f"替换数: {[r['replaced_total'] for r in out]}"
    print(f"[OK] C2D3 → {names}")

def test_c2d3_z0():
    """z=0 时分子式不带电荷"""
    cols = [
        imp.ElementColumn('C', 'natural', 2),
        imp.ElementColumn('H', '2', 3),
    ]
    out = imp.enumerate_impurities(cols, z=0)
    assert [r['name'] for r in out] == ['C₂H₃', 'C₂HD₂', 'C₂H₂D'], f"实际: {[r['name'] for r in out]}"
    print("[OK] z=0 → 无电荷后缀")

def test_charge_suffix():
    """电荷上标规则"""
    assert imp.charge_suffix(0) == ''
    assert imp.charge_suffix(1) == '⁺'
    assert imp.charge_suffix(2) == '²⁺'
    assert imp.charge_suffix(-1) == '⁻'
    assert imp.charge_suffix(-3) == '³⁻'
    print("[OK] 电荷上标 ⁺/²⁺/⁻/³⁻")

def test_no_modification():
    """全部 natural：无杂质"""
    cols = [imp.ElementColumn('C', 'natural', 2), imp.ElementColumn('H', 'natural', 3)]
    assert imp.enumerate_impurities(cols) == []
    print("[OK] 无修饰 → 空")

def test_single_atom():
    """单一修饰原子 H2×1 → k=0(输入,排除), k=1(全天然) → 只有 H⁺"""
    out = imp.enumerate_impurities([imp.ElementColumn('H', '2', 1)], z=1)
    assert [r['name'] for r in out] == ['H⁺']
    assert out[0]['counts'] == [1]
    print("[OK] H2×1 → [H⁺]")

def test_multi_mod():
    """多修饰列：C natural 1 + H2×2 + N15×2
    k 范围：H∈{0,1,2}, N∈{0,1,2}，排除全0(输入)，共 3×3-1=8 个"""
    cols = [
        imp.ElementColumn('C', 'natural', 1),
        imp.ElementColumn('H', '2', 2),
        imp.ElementColumn('N', '15', 2),
    ]
    out = imp.enumerate_impurities(cols, z=1)
    names = [r['name'] for r in out]
    # 全天然（H k=2, N k=2）：CH₂N₂⁺ 排第一
    assert names[0] == 'CH₂N₂⁺', f"全天然应第一: {names[0]}"
    assert len(out) == 8, f"应 8 个，实际 {len(out)}"
    # 替换1个的排最前（replaced_total=1）：CHDN²⁺? 检查无重复
    assert len(set(names)) == len(names), f"存在重复: {names}"
    print(f"[OK] 多修饰 → 8 个，全天然 {names[0]}，无重复")

def test_zero_count_mod_ignored():
    """修饰列 count=0 应忽略"""
    cols = [
        imp.ElementColumn('C', 'natural', 1),
        imp.ElementColumn('H', '2', 0),
        imp.ElementColumn('N', '15', 1),
    ]
    out = imp.enumerate_impurities(cols, z=1)
    assert [r['name'] for r in out] == ['CN⁺'], f"实际: {[r['name'] for r in out]}"
    print("[OK] count=0 修饰列忽略")

def test_d_and_t_symbols():
    """H2 → D、H3 → T 专用符号；13C → ¹³C 上标"""
    assert imp.iso_symbol('H', 2) == 'D'
    assert imp.iso_symbol('H', 3) == 'T'
    assert imp.iso_symbol('C', 13) == '¹³C'
    print("[OK] 核素符号 D/T/¹³C")

def test_same_element_natural_merge():
    """同元素多列 natural 合并 + natural 永不小于输入值：
    C natural 2 + ¹³C 3 + N natural 26 + ¹⁵N 2 → 全天然 C₁₃₁H₁₉₈N₂₈O₃₅S₂
    且所有杂质 C natural ≥ 2、N natural ≥ 26"""
    cols = [
        imp.ElementColumn('C', 'natural', 2),
        imp.ElementColumn('C', '13', 3),
        imp.ElementColumn('H', 'natural', 198),
        imp.ElementColumn('N', 'natural', 26),
        imp.ElementColumn('N', '15', 2),
        imp.ElementColumn('O', 'natural', 35),
        imp.ElementColumn('S', 'natural', 2),
    ]
    out = imp.enumerate_impurities(cols, z=0)
    assert out[0]['name'] == 'C₅H₁₉₈N₂₈O₃₅S₂', f"全天然应为合并形式，实际: {out[0]['name']}"
    # 所有杂质 C natural ≥ 2、N natural ≥ 26
    for r in out:
        c_nat = sum(c for e, i, c in zip(r['elements'], r['isos'], r['counts']) if e == 'C' and i == 'natural')
        n_nat = sum(c for e, i, c in zip(r['elements'], r['isos'], r['counts']) if e == 'N' and i == 'natural')
        assert c_nat >= 2, f"C natural {c_nat} < 2: {r['name']}"
        assert n_nat >= 26, f"N natural {n_nat} < 26: {r['name']}"
    # 组合数 = (3+1)(2+1) - 1 = 11
    assert len(out) == 11, f"应 11 个，实际 {len(out)}"
    print(f"[OK] 同元素合并+natural下限 → 全天然 {out[0]['name']}，共 {len(out)} 个")

def test_ch2d3_natural_fixed():
    """用户核心规则：输入 CH2D3（C natural 1 + H natural 2 + D 3）
    输出应只含 CH3D2、CH4D、CH5（H natural 只增不减，从 3→4→5）
    不允许出现 CHD4、CD5（H natural 从 2 减少到 1、0）"""
    cols = [
        imp.ElementColumn('C', 'natural', 1),
        imp.ElementColumn('H', 'natural', 2),
        imp.ElementColumn('H', '2', 3),
    ]
    out = imp.enumerate_impurities(cols, z=0)
    names = [r['name'] for r in out]
    # 内容集合必须恰好是 {CH3D2, CH4D, CH5}（无电荷）
    expected = {'CH₃D₂', 'CH₄D', 'CH₅'}
    assert set(names) == expected, f"实际: {names}"
    # 所有杂质 H natural ≥ 输入值 2（natural 数字不变/只增）
    for r in out:
        h_nat = sum(c for e, i, c in zip(r['elements'], r['isos'], r['counts'])
                    if e == 'H' and i == 'natural')
        assert h_nat >= 2, f"H natural {h_nat} < 2: {r['name']}"
    # 显式排除 CHD4、CD5
    for bad in ('CHD₄', 'CD₅'):
        assert bad not in names, f"不应出现 {bad}: {names}"
    print(f"[OK] CH2D3 → {sorted(names)}，H natural ≥2，无 CHD4/CD5")

def test_dedup():
    """去重：构造排列顺序不同但分子式相同的组合（人为构造 cols 乱序），
    验证 _canonical_key 归一化一致"""
    a = [('C', 'natural', 2), ('C', '13', 1), ('H', 'natural', 3)]
    b = [('H', 'natural', 3), ('C', '13', 1), ('C', 'natural', 2)]
    assert imp._canonical_key(a) == imp._canonical_key(b), "规范化签名应一致"
    print("[OK] 排列顺序不同的相同分子式 → 规范化签名一致")

if __name__ == '__main__':
    test_c2d3()
    test_c2d3_z0()
    test_charge_suffix()
    test_no_modification()
    test_single_atom()
    test_multi_mod()
    test_zero_count_mod_ignored()
    test_d_and_t_symbols()
    test_same_element_natural_merge()
    test_ch2d3_natural_fixed()
    test_dedup()
    print("\n全部测试通过 ✔")
