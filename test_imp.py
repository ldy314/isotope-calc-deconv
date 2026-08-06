# -*- coding: utf-8 -*-
"""imp 杂质枚举引擎的单元测试"""
import sys
sys.path.insert(0, r'D:\code test\chem\同位素计算及解卷积')
import imp

def test_c2d3():
    """C2D3（C natural 2 + H 2 3）→ 3 个杂质：C2H3, C2H2D, C2HD2"""
    cols = [
        imp.ElementColumn('C', 'natural', 2),
        imp.ElementColumn('H', '2', 3),
    ]
    out = imp.enumerate_impurities(cols)
    names = [r['name'] for r in out]
    assert names == ['C2H3', 'C2H2D', 'C2HD2'], f"实际: {names}"
    # 检查第一个杂质（天然形式）：C2H3
    r0 = out[0]
    assert r0['elements'] == ['C', 'H'] and r0['isos'] == ['natural', 'natural'] and r0['counts'] == [2, 3]
    # 检查第三个杂质：C2HD2 = C natural 2 + H natural 1 + H 2 2
    r2 = out[2]
    assert r2['elements'] == ['C', 'H', 'H'] and r2['isos'] == ['natural', 'natural', '2'] and r2['counts'] == [2, 1, 2]
    print(f"[OK] C2D3 → {names}")

def test_no_modification():
    """全部 natural：无杂质"""
    cols = [imp.ElementColumn('C', 'natural', 2), imp.ElementColumn('H', 'natural', 3)]
    assert imp.enumerate_impurities(cols) == []
    print("[OK] 无修饰 → 空")

def test_single_atom():
    """单一修饰原子 H2×1 → 只有天然形式 H"""
    out = imp.enumerate_impurities([imp.ElementColumn('H', '2', 1)])
    assert [r['name'] for r in out] == ['H']
    assert out[0]['counts'] == [1]
    print("[OK] H2×1 → [H]")

def test_multi_mod():
    """多修饰列笛卡尔积：C natural 1 + H2×2 + N15×1
    H2 列 k=1..2，N15 列 k=1..1 → 2 组合（排除输入本身）"""
    cols = [
        imp.ElementColumn('C', 'natural', 1),
        imp.ElementColumn('H', '2', 2),
        imp.ElementColumn('N', '15', 1),
    ]
    out = imp.enumerate_impurities(cols)
    names = [r['name'] for r in out]
    # 组合：H k=1 + N k=1 → CHDN；H k=2 + N k=1 → CH2N
    assert names == ['CH2N', 'CHDN'], f"实际: {names}"
    print(f"[OK] 多修饰 → {names}")

def test_zero_count_mod_ignored():
    """修饰列 count=0 应忽略：C natural 1 + H2×0 + N15×1 → 只有 N 参与 → CN"""
    cols = [
        imp.ElementColumn('C', 'natural', 1),
        imp.ElementColumn('H', '2', 0),
        imp.ElementColumn('N', '15', 1),
    ]
    out = imp.enumerate_impurities(cols)
    assert [r['name'] for r in out] == ['CN'], f"实际: {[r['name'] for r in out]}"
    print("[OK] count=0 修饰列忽略")

def test_d_and_t_symbols():
    """H2 → D、H3 → T 专用符号；13C → 13C"""
    assert imp.iso_symbol('H', 2) == 'D'
    assert imp.iso_symbol('H', 3) == 'T'
    assert imp.iso_symbol('C', 13) == '13C'
    print("[OK] 核素符号 D/T/13C")

if __name__ == '__main__':
    test_c2d3()
    test_no_modification()
    test_single_atom()
    test_multi_mod()
    test_zero_count_mod_ignored()
    test_d_and_t_symbols()
    print("\n全部测试通过 ✔")
