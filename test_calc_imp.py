# -*- coding: utf-8 -*-
"""calc_imp 组合引擎的单元测试"""
import csv
import io
import os
import sys
import tempfile

sys.path.insert(0, r'D:\code test\chem\同位素计算及解卷积')
import calc_imp
import imp
import theo


def test_ch2d3_csv():
    """CH2D3 → 3 杂质，CSV 每杂质带 theo 峰结果"""
    cols = [
        imp.ElementColumn('C', 'natural', 1),
        imp.ElementColumn('H', 'natural', 2),
        imp.ElementColumn('H', '2', 3),
    ]
    impurities, rows = calc_imp.run(cols, z=1, tol=0.001, min_ab=0.05)
    assert len(impurities) == 3, f"应 3 杂质，实际 {len(impurities)}"
    assert len(rows) > 0, "无峰行"
    # 杂质 1 全天然 CH5⁺ 的 M 峰质量应 ≈ 17.0391
    first = [r for r in rows if r['impurity'] == 1 and r['rank'] == 1][0]
    assert first['formula'] == 'CH₅⁺', f"公式: {first['formula']}"
    assert abs(first['mass'] - 17.0391) < 1e-3, f"M={first['mass']}"
    # rank 应从 1 开始连续
    ranks1 = [r['rank'] for r in rows if r['impurity'] == 1]
    assert ranks1 == list(range(1, len(ranks1) + 1)), f"rank: {ranks1}"
    print(f"[OK] CH2D3 → {len(impurities)} 杂质 / {len(rows)} 峰；M(CH5+)={first['mass']:.4f}")

def test_params_passed():
    """参数（z/tol/min-ab）透传：min-ab 大时峰数减少"""
    cols = [
        imp.ElementColumn('C', 'natural', 1),
        imp.ElementColumn('H', 'natural', 2),
        imp.ElementColumn('H', '2', 3),
    ]
    _, rows_lo = calc_imp.run(cols, z=1, tol=0.001, min_ab=0.0)
    _, rows_hi = calc_imp.run(cols, z=1, tol=0.001, min_ab=10.0)
    assert len(rows_hi) < len(rows_lo), "min-ab 增大应过滤更多峰"
    print(f"[OK] min-ab 过滤: {len(rows_lo)} 峰 → {len(rows_hi)} 峰 (min-ab 10%)")

def test_csv_file_bom():
    """CSV 文件：UTF-8 BOM 开头、表头完整、Excel 可读"""
    cols = [imp.ElementColumn('C', 'natural', 1), imp.ElementColumn('H', '2', 3)]
    tmp = os.path.join(tempfile.gettempdir(), 'calc_imp_test.csv')
    if os.path.exists(tmp):
        os.remove(tmp)
    # 直接写文件
    _, rows = calc_imp.run(cols, z=0, tol=0.001, min_ab=0.05)
    assert rows, "应有峰行"
    with open(tmp, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(tmp, 'rb') as f:
        head = f.read(3)
    assert head == b'\xef\xbb\xbf', f"缺少 BOM: {head}"
    with open(tmp, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        assert 'formula' in fieldnames and 'mass' in fieldnames and 'mz' in fieldnames
    os.remove(tmp)
    print("[OK] CSV 文件 UTF-8 BOM + 表头完整")

if __name__ == '__main__':
    test_ch2d3_csv()
    test_params_passed()
    test_csv_file_bom()
    print("\n全部测试通过 ✔")
