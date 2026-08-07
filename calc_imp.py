# -*- coding: utf-8 -*-
"""
calc_imp - 同位素取代杂质计算（枚举 + 逐杂质理论同位素峰 → CSV）

组合 imp.py（枚举杂质）与 theo.py（ExactMass_Calculator_v2 的计算引擎）：
  1. 解析元素表（与 ExactMass_Impurities 输入格式一致）
  2. 枚举所有同位素取代杂质（含全天然，不含输入本身）
  3. 对每个杂质，用 theo.compute_theoretical_spectrum 计算其同位素分布（前 50 峰）
  4. 输出 CSV：每行 = 一个杂质的一个峰

CSV 列：
  impurity    杂质序号（如 1/2/3...）
  formula     杂质分子式（含电荷，如 C₂H₃⁺）
  elements    元素（| 分隔）
  isos        同位素（| 分隔，natural 或质量数）
  counts      原子个数（| 分隔）
  rank        峰序号
  mass        中性精确质量
  mz          带电荷 m/z
  abundance   相对丰度 %

参数（与 v2 一致，可在 Excel 中设置）：
  --z     电荷数（默认 1）
  --tol   质量精度 / 峰合并容差 Da（默认 0.001）
  --min-ab 丰度阈值 %（默认 0.05）

用法：
    python calc_imp.py --elements C natural 1 H natural 2 H 2 3 \
                       --z 1 --tol 0.001 --min-ab 0.05 \
                       --out impurity_peaks.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from typing import List

import imp
import theo


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="calc_imp",
        description="同位素取代杂质计算：枚举杂质 + 逐杂质理论同位素峰 → CSV",
    )
    parser.add_argument(
        "--elements", nargs="+", required=True, metavar="E",
        help="元素表：三元素一组 (元素, 同位素, 个数)。同位素用 'natural' 或质量数（如 '2'=氘）。"
             "例：C natural 1 H natural 2 H 2 3",
    )
    parser.add_argument("--z", type=int, default=1, help="电荷数（默认 1）")
    parser.add_argument("--tol", type=float, default=0.001, help="质量精度（峰合并容差 Da，默认 0.001）")
    parser.add_argument("--min-ab", type=float, default=0.05, help="丰度阈值 %（默认 0.05）")
    parser.add_argument("--out", metavar="FILE", required=True, help="CSV 输出路径")
    parser.add_argument("--imp-out", metavar="FILE", default=None,
                        help="杂质列举协议文件（每杂质4行，供 Excel 回填输出区；缺省不写）")
    parser.add_argument("--json", action="store_true", help="同时在 stdout 打印汇总 JSON")
    return parser.parse_args(argv)


def run(columns, z, tol, min_ab):
    """返回 (impurities, rows)；rows 为 CSV 行列表"""
    impurities = imp.enumerate_impurities(columns, z=z)
    rows = []
    for idx, r in enumerate(impurities, 1):
        # 把杂质的三行格式转为 theo 元素表
        theo_cols: List[theo.ElementColumn] = [
            theo.ElementColumn(e, iso, c)
            for e, iso, c in zip(r['elements'], r['isos'], r['counts'])
        ]
        peaks = theo.compute_theoretical_spectrum(
            theo_cols, z=z, tol=tol, min_ab=min_ab
        )
        for rank, pk in enumerate(peaks, 1):
            rows.append({
                'impurity': idx,
                'formula': r['name'],
                'elements': '|'.join(r['elements']),
                'isos': '|'.join(r['isos']),
                'counts': '|'.join(str(c) for c in r['counts']),
                'rank': rank,
                'mass': pk.mass,
                'mz': pk.mz,
                'abundance': pk.rel_abundance,
            })
    return impurities, rows


def main(argv=None):
    args = parse_args(argv)
    if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    if len(args.elements) % 3 != 0:
        print("错误：--elements 必须按 (元素, 同位素, 个数) 三元组提供", file=sys.stderr)
        return 1

    columns = []
    try:
        for i in range(0, len(args.elements), 3):
            el, iso, cnt = args.elements[i], args.elements[i + 1], args.elements[i + 2]
            columns.append(imp.ElementColumn(el, iso, int(cnt)))
    except ValueError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1

    try:
        impurities, rows = run(columns, args.z, args.tol, args.min_ab)
    except ValueError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1

    # 写 CSV（UTF-8 with BOM，Excel 可直接打开中文）
    with open(args.out, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'impurity', 'formula', 'elements', 'isos', 'counts',
            'rank', 'mass', 'mz', 'abundance',
        ])
        writer.writeheader()
        writer.writerows(rows)

    # 写杂质列举协议文件（与 imp.py --out 同格式，供 Excel 回填输出区）
    if args.imp_out:
        with open(args.imp_out, 'w', encoding='utf-8', newline='\n') as f:
            f.write(f"# imp v1 | total {len(impurities)} | z {args.z}\n")
            for r in impurities:
                f.write(imp._impurity_block(r) + '\n')
            if not impurities:
                f.write("# none\n")

    if args.json:
        import json
        summary = {
            'z': args.z, 'tol': args.tol, 'min_ab': args.min_ab,
            'impurities': len(impurities), 'peaks': len(rows),
            'out': args.out,
        }
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"完成：{len(impurities)} 个杂质，共 {len(rows)} 个峰 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
