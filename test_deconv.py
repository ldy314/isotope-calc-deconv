"""test_deconv.py - deconv 主引擎测试（模拟谱 → NNLS 还原已知比例）

覆盖场景（对应 ADR-0007 与 LCMS-9030 实际工况）：
  1. profile 模式：已知权重混合谱 → 还原相对含量
  2. centroid 模式：峰列表 → 还原相对含量
  3. 质量偏移：谱图 +5 ppm 校准误差 → 偏移扫描找回 + 权重仍正确
  4. 近简并：D 与 ¹³C 组合（Δm≈0.003 Da）在 30k 下不可分辨 → 整体 NNLS 仍分辨
  5. natural 下限规则：所有杂质 C natural ≥ 输入值
"""

import os
import tempfile

import numpy as np

import imp
import theo
import deconv


# ---------------------------------------------------------------------------
# 模拟谱生成器（与 deconv 同一套理论模式 + 峰形卷积）
# ---------------------------------------------------------------------------

def simulate_profile(columns, z, weights, resolution=deconv.RESOLUTION_DEFAULT,
                     noise=0.0, seed=0):
    """按已知权重混合各物种模式（含主成分/输入分子本身），生成 profile 谱 (grid, model)。
    权重顺序：第 0 个 = 主成分（输入分子），其后 = enumerate_impurities 的杂质。"""
    impurities = imp.enumerate_impurities(columns, z=z)
    target_dict = {'elements': [c.element for c in columns],
                   'isos': [c.isotope for c in columns],
                   'counts': [c.count for c in columns]}
    target_stick = deconv._species_stick(target_dict, z, 0.001, 500)
    sticks = [target_stick] + [deconv._species_stick(r, z, 0.001, 500) for r in impurities]
    assert len(weights) == len(sticks), f"权重数 {len(weights)} != 物种数 {len(sticks)}"
    lo, hi = deconv.auto_window(sticks, resolution)
    min_m = min(m for st in sticks for m, _ in st)
    sigma = min_m / resolution / deconv.GAUSS_K
    grid_step = sigma / 4.0  # 每个峰 ~10 采样点
    grid = np.arange(lo, hi, grid_step)
    model = np.zeros_like(grid, dtype=float)
    for w, st in zip(weights, sticks):
        model += w * deconv._convolve_gaussian(st, grid, resolution)
    if noise > 0:
        rng = np.random.default_rng(seed)
        model = model + rng.normal(0, noise * float(model.max()), len(grid))
    return grid, model


def profile_to_centroid(grid, model):
    """从 profile 取局部极大 → centroid 峰列表"""
    mzs, ints = [], []
    for i in range(1, len(grid) - 1):
        if model[i] > model[i - 1] and model[i] >= model[i + 1]:
            mzs.append(grid[i])
            ints.append(model[i])
    return np.array(mzs), np.array(ints)


def write_csv(mz, intensity):
    fd, path = tempfile.mkstemp(suffix='.csv')
    os.close(fd)
    with open(path, 'w') as f:
        f.write('m/z,intensity\n')
        for m, i in zip(mz, intensity):
            f.write(f'{m:.6f},{i:.8e}\n')  # 科学计数保留小强度（模拟真实导出）
    return path


# 测试分子：C natural 20 + H natural 30 + D×3 + ¹³C×2 + N natural 4 + O natural 6，z=1
# → 11 个杂质（D k=0..3 × ¹³C k=0..2，排除输入本身）
TEST_COLS = [
    imp.ElementColumn('C', 'natural', 20),
    imp.ElementColumn('H', 'natural', 30),
    imp.ElementColumn('H', '2', 3),
    imp.ElementColumn('C', '13', 2),
    imp.ElementColumn('N', 'natural', 4),
    imp.ElementColumn('O', 'natural', 6),
]
# 已知权重（总和=1）：全天然 0.5，单 D 0.15，双 D 0.10，三 D 0.05，
# 单 ¹³C 0.10，双 ¹³C 0.05，D1+¹³C1 0.03，D2+¹³C1 0.02 → 其余 0
TEST_WEIGHTS = [0.0, 0.50, 0.15, 0.10, 0.05, 0.10, 0.05, 0.03, 0.02, 0.0, 0.0, 0.0]


def expected_rel(weights):
    tot = sum(weights)
    return [100.0 * w / tot for w in weights]


def assert_recovered(result, expected_weights, tol=1.0):
    """按质量简并类断言：每组（可能合并多个物种）的相对含量 = 组内期望之和。"""
    exp = expected_rel(expected_weights)
    errs = []
    for g in result['groups']:
        members = g['members']  # 1-based rank
        exp_sum = sum(exp[m - 1] for m in members)
        e = abs(g['relative_content'] - exp_sum)
        if e > tol:
            names = ', '.join(result['species'][m - 1]['name'] for m in members)
            errs.append(f"组{g['group']} [{names}]: got {g['relative_content']:.3f} vs exp {exp_sum:.3f}")
    assert not errs, "相对含量还原超差:\n" + '\n'.join(errs) + \
        f"\noffset={result['mass_offset_ppm']:+.1f} ppm, RSS={result['residual_rss']:.2e}"


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

def test_profile_recovery():
    """profile 模式：近简并物种（D/¹³C，Δm 0.003 Da < 分辨极限）自动合并为名义质量类，
    类合计还原（与 centroid 同一合并规则，物理一致）"""
    grid, model = simulate_profile(TEST_COLS, z=1, weights=TEST_WEIGHTS)
    path = write_csv(grid, model)
    result = deconv.deconvolve(TEST_COLS, path, z=1, tol=0.001, resolution=deconv.RESOLUTION_DEFAULT)
    assert result['mode'] == 'profile', f"模式识别错误: {result['mode']}"
    assert result['n_groups'] < result['n_species'], \
        f"profile 应合并近简并: {result['n_groups']} 组 / {result['n_species']} 物种"
    assert_recovered(result, TEST_WEIGHTS, tol=1.0)
    os.remove(path)
    print(f"[OK] profile 还原 {result['n_groups']} 组（{result['n_species']} 物种中 "
          f"{sum(1 for s in result['species'] if s['merged'])} 个自动合并），offset={result['mass_offset_ppm']:+.1f} ppm")


def test_centroid_recovery():
    """centroid 模式：稀疏峰列表无法分辨近简并对 → 自动合并，组合计还原"""
    grid, model = simulate_profile(TEST_COLS, z=1, weights=TEST_WEIGHTS)
    mzs, ints = profile_to_centroid(grid, model)
    path = write_csv(mzs, ints)
    result = deconv.deconvolve(TEST_COLS, path, z=1, tol=0.001, resolution=deconv.RESOLUTION_DEFAULT)
    assert result['mode'] == 'centroid', f"模式识别错误: {result['mode']}"
    assert result['n_groups'] < result['n_species'], \
        f"centroid 应发生合并: {result['n_groups']} 组 / {result['n_species']} 物种"
    assert_recovered(result, TEST_WEIGHTS, tol=1.0)  # 名义质量类误差 ~0.9%
    merged_names = [f"{s['name']}" for s in result['species'] if s['merged']]
    print(f"[OK] centroid 还原 {result['n_groups']} 组（{len(merged_names)} 个物种自动合并），{len(mzs)} 峰")
    os.remove(path)


def test_mass_offset_recovery():
    """质量偏移：谱图整体 +5 ppm → 扫描找回 offset，权重不变"""
    grid, model = simulate_profile(TEST_COLS, z=1, weights=TEST_WEIGHTS)
    shifted_mz = grid * (1.0 + 5e-6)
    path = write_csv(shifted_mz, model)
    result = deconv.deconvolve(TEST_COLS, path, z=1, tol=0.001, resolution=deconv.RESOLUTION_DEFAULT)
    assert abs(result['mass_offset_ppm'] - 5.0) <= 1.0, \
        f"offset 找回失败: {result['mass_offset_ppm']:+.2f} ppm（期望 ≈ +5）"
    assert_recovered(result, TEST_WEIGHTS, tol=0.5)
    os.remove(path)
    print(f"[OK] 质量偏移找回 {result['mass_offset_ppm']:+.1f} ppm，权重正确")


def test_noise_robustness():
    """加 1% 噪声：权重仍能还原（放宽容差）"""
    grid, model = simulate_profile(TEST_COLS, z=1, weights=TEST_WEIGHTS, noise=0.01)
    path = write_csv(grid, model)
    result = deconv.deconvolve(TEST_COLS, path, z=1, tol=0.001, resolution=deconv.RESOLUTION_DEFAULT)
    assert_recovered(result, TEST_WEIGHTS, tol=2.0)
    os.remove(path)
    print(f"[OK] 1% 噪声下还原（RSS={result['residual_rss']:.2e}）")


def test_natural_floor_rule():
    """natural 下限：所有杂质 C natural ≥ 20、H natural ≥ 30、N ≥ 4、O ≥ 6"""
    impurities = imp.enumerate_impurities(TEST_COLS, z=1)
    for r in impurities:
        cn = sum(c for e, iso, c in zip(r['elements'], r['isos'], r['counts'])
                 if e == 'C' and imp.is_natural_iso(iso))
        hn = sum(c for e, iso, c in zip(r['elements'], r['isos'], r['counts'])
                 if e == 'H' and imp.is_natural_iso(iso))
        assert cn >= 20, f"{r['name']}: C natural={cn} < 20"
        assert hn >= 30, f"{r['name']}: H natural={hn} < 30"
    print(f"[OK] natural 下限规则（{len(impurities)} 杂质全满足）")


def test_z2_recovery():
    """z=2：带双电荷的 m/z 拟合仍能还原"""
    grid, model = simulate_profile(TEST_COLS, z=2, weights=TEST_WEIGHTS)
    path = write_csv(grid, model)
    result = deconv.deconvolve(TEST_COLS, path, z=2, tol=0.001, resolution=deconv.RESOLUTION_DEFAULT)
    assert_recovered(result, TEST_WEIGHTS, tol=0.5)
    os.remove(path)
    print(f"[OK] z=2 还原 {result['n_species']} 杂质")


if __name__ == '__main__':
    test_profile_recovery()
    test_centroid_recovery()
    test_mass_offset_recovery()
    test_noise_robustness()
    test_natural_floor_rule()
    test_z2_recovery()
    print("\n全部测试通过 ✔")
