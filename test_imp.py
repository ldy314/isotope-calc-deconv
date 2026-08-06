# -*- coding: utf-8 -*-
"""imp 杂质枚举引擎的单元测试"""
import sys
sys.path.insert(0, r'D:\code test\chem\同位素计算及解卷积')
import imp

def test_c2d3():
    """C2D3（C natural 2 + H 2 3）→ 3 个杂质：C₂H₃⁺, C₂H₂D⁺, C₂HD₂⁺"""
    cols = [
        imp.ElementColumn('C', 'natural', 2),
        imp.ElementColumn('H', '2', 3),
    ]
    out = imp.enumerate_impurities(cols, z=1)
    names = [r['name'] for r in out]
    assert names == ['C₂H₃⁺', 'C₂H₂D⁺', 'C₂HD₂⁺'], f"实际: {names}"
    # 检查第一个杂质（天然形式）：C₂H₃⁺
    r0 = out[0]
    assert r0['elements'] == ['C', 'H'] and r0['isos'] == ['natural', 'natural'] and r0['counts'] == [2, 3]
    # 检查第三个杂质：C₂HD₂⁺ = C natural 2 + H natural 1 + H 2 2
    r2 = out[2]
    assert r2['elements'] == ['C', 'H', 'H'] and r2['isos'] == ['natural', 'natural', '2'] and r2['counts'] == [2, 1, 2]
    print(f"[OK] C2D3 → {names}")

def test_c2d3_z0():
    """z=0 时分子式不带电荷：C₂H₃"""
    cols = [
        imp.ElementColumn('C', 'natural', 2),
        imp.ElementColumn('H', '2', 3),
    ]
    out = imp.enumerate_impurities(cols, z=0)
    assert [r['name'] for r in out] == ['C₂H₃', 'C₂H₂D', 'C₂HD₂'], f"实际: {[r['name'] for r in out]}"
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
    """单一修饰原子 H2×1 → 只有天然形式 H"""
    out = imp.enumerate_impurities([imp.ElementColumn('H', '2', 1)], z=1)
    assert [r['name'] for r in out] == ['H⁺']
    assert out[0]['counts'] == [1]
    print("[OK] H2×1 → [H⁺]")

def test_multi_mod():
    """多修饰列笛卡尔积：C natural 1 + H2×2 + N15×2
    H 保留 d=0,1；N 保留 d=0,1 → 2×2=4 组合（d 全=n 的输入本身被排除）"""
    cols = [
        imp.ElementColumn('C', 'natural', 1),
        imp.ElementColumn('H', '2', 2),
        imp.ElementColumn('N', '15', 2),
    ]
    out = imp.enumerate_impurities(cols, z=1)
    names = [r['name'] for r in out]
    # 4 个组合（输入本身 CH₂D₂¹⁵N₂⁺ 排除）：
    #   H k=2(全天然) + N k=2(全天然) → CH₂N₂⁺
    #   H k=2 + N k=1(天然1+15N1)     → CH₂N¹⁵N⁺
    #   H k=1(天然1+D1) + N k=2       → CHDN₂⁺
    #   H k=1 + N k=1                 → CHDN¹⁵N⁺
    expected = {'CH₂N₂⁺', 'CH₂N¹⁵N⁺', 'CHDN₂⁺', 'CHDN¹⁵N⁺'}
    assert set(names) == expected, f"实际: {names}"
    assert len(names) == 4
    print(f"[OK] 多修饰 → {sorted(names)}")

def test_zero_count_mod_ignored():
    """修饰列 count=0 应忽略：C natural 1 + H2×0 + N15×1 → 只有 N 参与 → C¹⁵N⁺? 
    注意：N15×1 时 k 只能=1（全替换=天然），故结果为 CN⁺"""
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

if __name__ == '__main__':
    test_c2d3()
    test_c2d3_z0()
    test_charge_suffix()
    test_no_modification()
    test_single_atom()
    test_multi_mod()
    test_zero_count_mod_ignored()
    test_d_and_t_symbols()
    print("\n全部测试通过 ✔")
