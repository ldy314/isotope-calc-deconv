# -*- coding: utf-8 -*-
"""theo 核心的单元测试"""
import sys
sys.path.insert(0, r'D:\code test\chem\同位素计算及解卷积')
import theo

def approx(a, b, tol=1e-4):
    return abs(a - b) <= tol

def test_caffeine_monoisotopic():
    """咖啡因 C8H10N4O2 单同位素质量应约 194.0804"""
    cols = [
        theo.ElementColumn('C', 'natural', 8),
        theo.ElementColumn('H', 'natural', 10),
        theo.ElementColumn('N', 'natural', 4),
        theo.ElementColumn('O', 'natural', 2),
    ]
    peaks = theo.compute_theoretical_spectrum(cols, z=1, tol=0.001)
    assert peaks, "no peaks"
    assert approx(peaks[0].mass, 194.0804, 1e-3), f"M={peaks[0].mass}"
    print(f"[OK] 咖啡因 M = {peaks[0].mass:.6f} (期望 ~194.0804)")

def test_peptide_monoisotopic():
    """多肽 C134H198N28O35S1 单同位素质量 ≈ 2791.4295"""
    cols = [
        theo.ElementColumn('C', 'natural', 134),
        theo.ElementColumn('H', 'natural', 198),
        theo.ElementColumn('N', 'natural', 28),
        theo.ElementColumn('O', 'natural', 35),
        theo.ElementColumn('S', 'natural', 1),
    ]
    peaks = theo.compute_theoretical_spectrum(cols, z=1, tol=0.001)
    assert approx(peaks[0].mass, 2791.4295, 1e-3), f"M={peaks[0].mass}"
    print(f"[OK] 多肽 M = {peaks[0].mass:.6f} (期望 ~2791.4295)")

def test_partial_label_13c6():
    """部分标记：C自然128 + ¹³C6 → 质量应偏移 ~6.02 Da"""
    cols = [
        theo.ElementColumn('C', 'natural', 128),
        theo.ElementColumn('C', '13', 6),
        theo.ElementColumn('H', 'natural', 198),
        theo.ElementColumn('N', 'natural', 28),
        theo.ElementColumn('O', 'natural', 35),
        theo.ElementColumn('S', 'natural', 1),
    ]
    peaks = theo.compute_theoretical_spectrum(cols, z=1, tol=0.001)
    expected = 2791.4295 + 6 * 1.003354835
    assert approx(peaks[0].mass, expected, 5e-3), f"M={peaks[0].mass} 期望 {expected}"
    print(f"[OK] ¹³C6 标记 M = {peaks[0].mass:.6f} (期望 ~{expected:.4f})")

def test_charge_z2():
    """z=2 时 m/z = (M - 2*me)/2"""
    cols = [theo.ElementColumn('C', 'natural', 8), theo.ElementColumn('H', 'natural', 10),
            theo.ElementColumn('N', 'natural', 4), theo.ElementColumn('O', 'natural', 2)]
    peaks = theo.compute_theoretical_spectrum(cols, z=2, tol=0.001)
    expected_mz = (194.080376 - 2 * theo.ELECTRON_MASS) / 2
    assert approx(peaks[0].mz, expected_mz, 1e-4), f"m/z={peaks[0].mz} 期望 {expected_mz}"
    print(f"[OK] z=2 m/z = {peaks[0].mz:.6f} (期望 ~{expected_mz:.6f})")

def test_merge_tolerance():
    """容差合并：¹³C(+1.00335) 与 ¹⁷O(+1.00422) 差 0.00087 ≤ 0.001 应合并；
    ¹⁵N(+0.99703) 与 ¹³C 差 0.0063 > 0.001 应分开"""
    cols = [theo.ElementColumn('C', 'natural', 8), theo.ElementColumn('H', 'natural', 10),
            theo.ElementColumn('N', 'natural', 4), theo.ElementColumn('O', 'natural', 2)]
    peaks = theo.compute_theoretical_spectrum(cols, z=1, tol=0.001)
    # M+1 区域应出现多个分离峰；丰度最高的 M+1 峰是 ¹³C+¹⁷O 合并峰（~8.7%）
    m1 = [p for p in peaks if 194.5 < p.mass < 196.0]
    strongest = max(m1, key=lambda p: p.rel_abundance)
    assert strongest.rel_abundance > 8.5, f"M+1 最强峰丰度异常: {strongest.rel_abundance}"
    # ¹⁵N 单独成峰（与 ¹³C 差 0.0063，不合并）
    n15 = [p for p in peaks if abs(p.mass - 195.0774) < 0.001]
    assert n15, f"¹⁵N 峰缺失"
    print(f"[OK] M+1 拆分：¹⁵N={n15[0].rel_abundance:.3f}% | ¹³C+¹⁷O合并={strongest.rel_abundance:.3f}% (>8.5%)")

def test_less_than_20_peaks():
    """纯同位素（如 ¹²C 全替换）时峰数不足20，应显示已有全部"""
    cols = [
        theo.ElementColumn('C', '12', 10),
        theo.ElementColumn('H', '1', 20),
        theo.ElementColumn('O', '16', 5),
    ]
    peaks = theo.compute_theoretical_spectrum(cols, z=1, tol=0.001)
    assert len(peaks) == 1, f"纯同位素应有1峰，实际 {len(peaks)}"
    print(f"[OK] 纯同位素 ¹²C₁₀¹H₂₀¹⁶O₅ → 单峰 {peaks[0].mass:.4f}")

def test_negative_charge():
    """负电荷 z=-1：m/z = (M - z·me)/z = -(M + me)（质谱惯例负值）"""
    cols = [theo.ElementColumn('C', 'natural', 8), theo.ElementColumn('H', 'natural', 10),
            theo.ElementColumn('N', 'natural', 4), theo.ElementColumn('O', 'natural', 2)]
    peaks = theo.compute_theoretical_spectrum(cols, z=-1, tol=0.001)
    expected = -(194.080376 + theo.ELECTRON_MASS)
    assert approx(peaks[0].mz, expected, 1e-4), f"m/z={peaks[0].mz} 期望 {expected}"
    print(f"[OK] z=-1 m/z = {peaks[0].mz:.6f} (期望 ~{expected:.6f})")

def test_neutral_z0():
    """z=0 中性分子：m/z = M（不除电荷、不加电子质量修正）"""
    cols = [theo.ElementColumn('C', 'natural', 8), theo.ElementColumn('H', 'natural', 10),
            theo.ElementColumn('N', 'natural', 4), theo.ElementColumn('O', 'natural', 2)]
    peaks = theo.compute_theoretical_spectrum(cols, z=0, tol=0.001)
    assert approx(peaks[0].mz, peaks[0].mass, 1e-9), f"z=0 时 m/z 应等于 M: {peaks[0].mz} vs {peaks[0].mass}"
    assert approx(peaks[0].mass, 194.0804, 1e-3)
    print(f"[OK] z=0 中性分子 m/z = M = {peaks[0].mz:.6f}")

if __name__ == '__main__':
    test_caffeine_monoisotopic()
    test_peptide_monoisotopic()
    test_partial_label_13c6()
    test_charge_z2()
    test_merge_tolerance()
    test_less_than_20_peaks()
    test_negative_charge()
    test_neutral_z0()
    print("\n全部测试通过 ✔")
