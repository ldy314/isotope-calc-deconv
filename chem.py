#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
chem.py — 同位素计算 / 杂质枚举 / 解卷积 统一命令行入口

不再需要打开 Excel：所有功能一行命令搞定，分子式直接写字符串，或用化合物别名。

用法速查
--------
    # 枚举同位素取代杂质 + 算理论峰（= Calc 表「列举并计算」按钮）
    chem.py imp SPH20291-Isotope1
    chem.py imp "C128[13C]6H198N26[15N]2O35S2" --z 2

    # 理论同位素谱（单个分子式）
    chem.py theo SPH20291 --z 2 --top-n 10

    # 解卷积 / 富集度（质心法 + NNLS）
    chem.py deconv SPH20291-Isotope1 --spectrum 解卷积/xxx/spec.csv

    # 岛津 .lcd 导出为 CSV（参数完全透传给 lcd2csv.py，含 --help）
    chem.py lcd --lcd data.lcd --rt 300 360 --out spec.csv   # RT 窗口单位为秒
    chem.py lcd --lcd data.lcd --list                        # 先列出所有扫描

    # 岛津 .jdx（JCAMP-DX 质心谱）导出为 CSV（参数完全透传给 jdx2csv.py）
    chem.py jdx --jdx data.jdx --out spec.csv
    chem.py jdx --jdx data.jdx --list                        # 仅打印文件头元数据
    # deconv 可直接吃 .jdx（内部自动先转 CSV）：
    chem.py deconv SPH20291-Isotope1 --spectrum data.jdx --z 4

    # 化合物库
    chem.py ls
    chem.py add MyPep "C50H80N12O15" --z 2 --note "测试肽"
    chem.py parse "C128[13C]6H198N26[15N]2O35S2"

分子式语法见 formula.py（支持 [13C]6 / (15N)2 / D10 / (CH2)5 分组）。
"""

from __future__ import annotations

import argparse
import csv
import datetime
import importlib.util
import os
import sys
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str):
    """加载同目录模块并注册 sys.modules（dataclass 依赖）。"""
    if name in sys.modules and getattr(sys.modules[name], "__file__", "").startswith(HERE):
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


imp = _load("imp")
theo = _load("theo")
formula = _load("formula")


# ---------------------------------------------------------------------------
# 公共：解析「别名 或 分子式」
# ---------------------------------------------------------------------------

def _resolve(token: str, z_arg):
    cols, z_default, note = formula.resolve(token)
    z = z_arg if z_arg is not None else (z_default if z_default is not None else 1)
    return cols, z, note


def _print_input(token: str, cols, z: int, note: str):
    print(f"输入      : {token}")
    print(f"分子式    : {formula.pretty_formula(cols, z)}")
    print(f"规范式    : {formula.columns_to_formula(cols)}   (z={z})")
    mods = [(c.element, c.isotope, c.count) for c in cols if not imp.is_natural_iso(c.isotope)]
    if mods:
        s = ", ".join(f"{imp.iso_symbol(e, int(i))}×{n}" for e, i, n in mods)
        print(f"修饰同位素: {s}   总标记原子 = {sum(n for _, _, n in mods)}")
    else:
        print("修饰同位素: 无（纯天然）")
    if note:
        print(f"备注      : {note}")


# ---------------------------------------------------------------------------
# 子命令：imp —— 枚举杂质 + 逐杂质理论峰
# ---------------------------------------------------------------------------

def cmd_imp(args) -> int:
    cols, z, note = _resolve(args.formula, args.z)
    _print_input(args.formula, cols, z, note)

    impurities = imp.enumerate_impurities(cols, z=z)
    if not impurities:
        print("\n无修饰同位素 → 无同位素取代杂质可枚举。"
              "\n（该功能针对同位素标记物；纯天然分子请用 `chem.py theo`）")
        return 1
    print(f"\n枚举杂质数: {len(impurities)} （含全天然形，不含输入本身）")

    summary, peak_rows = [], []
    for idx, r in enumerate(impurities, 1):
        tcols = [theo.ElementColumn(e, i, c)
                 for e, i, c in zip(r["elements"], r["isos"], r["counts"])]
        peaks = theo.compute_theoretical_spectrum(
            tcols, z=z, tol=args.tol, top_n=args.top_n, min_ab=args.min_ab)
        mono = peaks[0] if peaks else None
        p1 = theo.compute_theoretical_spectrum(tcols, z=1, tol=args.tol, top_n=1, min_ab=0)
        summary.append(dict(
            idx=idx, name=r["name"], mod=r["mod_total"], rep=r["replaced_total"],
            mass=mono.mass if mono else None, mz=mono.mz if mono else None,
            mz1=p1[0].mz if p1 else None,
            rel=mono.rel_abundance if mono else None))
        for p in peaks:
            peak_rows.append([idx, r["name"], r["mod_total"], r["replaced_total"],
                              "|".join(r["elements"]), "|".join(r["isos"]),
                              "|".join(str(c) for c in r["counts"]),
                              f"{p.mass:.6f}", f"{p.mz:.6f}", round(p.rel_abundance, 4)])

    # 控制台表格
    w = 30
    print("\n" + "=" * 96)
    print(f"{'#':>3}  {'分子式':<{w}} {'保留':>4} {'替换':>4} {'单同位素质量':>14} "
          f"{'m/z z' + str(z):>12} {'单峰rel%':>9}")
    print("-" * 96)
    for s in summary:
        print(f"{s['idx']:>3}  {s['name']:<{w}} {s['mod']:>4} {s['rep']:>4} "
              f"{s['mass']:>14.4f} {s['mz']:>12.4f} {s['rel']:>9.3f}")
    print("=" * 96)

    if args.no_files:
        return 0

    out_dir = args.out_dir or HERE
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = args.prefix or "impurity"
    sum_csv = os.path.join(out_dir, f"{tag}_summary_z{z}_{ts}.csv")
    peak_csv = os.path.join(out_dir, f"{tag}_peaks_z{z}_{ts}.csv")
    imp_txt = os.path.join(out_dir, f"{tag}_list_z{z}_{ts}.txt")

    with open(sum_csv, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["idx", "formula", "labeled_atoms_remaining", "substituted_to_natural",
                     "mono_neutral_mass", f"mz_z{z}", "mz_z1", "mono_rel_abundance_%"])
        for s in summary:
            wr.writerow([s["idx"], s["name"], s["mod"], s["rep"], f"{s['mass']:.4f}",
                         f"{s['mz']:.4f}", f"{s['mz1']:.4f}" if s["mz1"] else "",
                         f"{s['rel']:.4f}"])

    with open(peak_csv, "w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["impurity_idx", "formula", "labeled_atoms_remaining",
                     "substituted_to_natural", "elements", "isotopes", "counts",
                     "neutral_mass", f"mz_z{z}", "rel_abundance_%"])
        wr.writerows(peak_rows)

    with open(imp_txt, "w", encoding="utf-8") as f:
        f.write(f"# imp v1 | total {len(impurities)} | z {z}\n")
        for r in impurities:
            f.write(imp._impurity_block(r) + "\n")

    print(f"\n已写出 ({out_dir}):")
    print(f"  汇总    : {os.path.basename(sum_csv)}   ({len(summary)} 行)")
    print(f"  逐峰    : {os.path.basename(peak_csv)}   ({len(peak_rows)} 行)")
    print(f"  4行清单 : {os.path.basename(imp_txt)}")
    return 0


# ---------------------------------------------------------------------------
# 子命令：theo —— 单分子式理论同位素谱
# ---------------------------------------------------------------------------

def cmd_theo(args) -> int:
    cols, z, note = _resolve(args.formula, args.z)
    _print_input(args.formula, cols, z, note)

    tcols = [theo.ElementColumn(c.element, c.isotope, c.count) for c in cols]
    peaks = theo.compute_theoretical_spectrum(
        tcols, z=z, tol=args.tol, top_n=args.top_n, min_ab=args.min_ab)
    if not peaks:
        print("未得到任何峰（检查 --min-ab 阈值）")
        return 1

    print(f"\n共 {len(peaks)} 个峰（tol={args.tol} Da, min-ab={args.min_ab}%）")
    print("=" * 62)
    print(f"{'#':>3} {'中性精确质量':>16} {'m/z (z=' + str(z) + ')':>16} {'相对丰度%':>12}")
    print("-" * 62)
    for i, p in enumerate(peaks, 1):
        bar = "#" * max(0, int(p.rel_abundance / 4))
        print(f"{i:>3} {p.mass:>16.5f} {p.mz:>16.5f} {p.rel_abundance:>12.4f}  {bar}")
    print("=" * 62)
    print(f"单同位素质量 (最轻峰): {peaks[0].mass:.5f}   m/z = {peaks[0].mz:.5f}")

    if args.out:
        with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["idx", "neutral_mass", f"mz_z{z}", "rel_abundance_%"])
            for i, p in enumerate(peaks, 1):
                wr.writerow([i, f"{p.mass:.6f}", f"{p.mz:.6f}", f"{p.rel_abundance:.4f}"])
        print(f"已写出: {args.out}")
    return 0


# ---------------------------------------------------------------------------
# 子命令：deconv —— 转发到已验证的 deconv_excel 引擎
# ---------------------------------------------------------------------------

def cmd_deconv(args) -> int:
    cols, z, note = _resolve(args.formula, args.z)
    _print_input(args.formula, cols, z, note)

    # .jdx 直接吃：内部先转 CSV（与 deconv.load_spectrum 兼容）
    spec = args.spectrum
    if spec.lower().endswith(".jdx"):
        j2c = _load("jdx2csv")
        spec = j2c.convert(spec)
        print(f"[jdx→csv] 已转换: {spec}")

    dx = _load("deconv_excel")
    argv: List[str] = ["--elements"]
    for c in cols:
        argv += [c.element, c.isotope, str(c.count)]
    argv += ["--z", str(z), "--spectrum", spec]
    if args.lo is not None:
        argv += ["--lo", str(args.lo)]
    if args.hi is not None:
        argv += ["--hi", str(args.hi)]
    if args.resolution is not None:
        argv += ["--resolution", str(args.resolution)]
    argv += ["--tol", str(args.tol), "--nnls", "true" if args.nnls else "false"]

    # 引擎在无 --out 时会把整个 JSON 打到 stdout（那是给 VBA 读的）。
    # CLI 下默认落盘，保持终端只显示人类可读摘要；--json 可强制打印。
    out = None
    if not args.json:
        out = args.out or os.path.join(os.getcwd(), "deconv_result.json")
        argv += ["--out", out]

    print()
    rc = dx.main(argv) or 0

    if out:
        table = os.path.splitext(out)[0] + "_table.csv"
        if os.path.exists(table):
            with open(table, encoding="utf-8") as f:
                rows = list(csv.reader(f))
            if len(rows) > 1:
                print(f"\nNNLS 逐杂质相对含量（前 {min(15, len(rows) - 1)} / {len(rows) - 1}）")
                print("-" * 76)
                print(f"{'#':>3}  {'分子式':<34} {'相对含量%':>10}  {'简并':>4} {'主成分':>6}")
                for r in rows[1:16]:
                    star = "★" if r[4] == "1" else ""
                    print(f"{r[0]:>3}  {r[1]:<34} {float(r[2]):>10.4f}  "
                          f"{'是' if r[3] == '1' else '':>4} {star:>6}")
                print("-" * 76)
        print(f"\n结果文件:")
        print(f"  JSON  : {out}")
        print(f"  标量  : {os.path.splitext(out)[0]}_scalars.txt")
        if os.path.exists(table):
            print(f"  NNLS表: {table}")
    return rc


# ---------------------------------------------------------------------------
# 子命令：lcd —— 转发到 lcd2csv
# ---------------------------------------------------------------------------

def cmd_lcd(args) -> int:
    l2c = _load("lcd2csv")
    return l2c.main(args.rest) or 0


def cmd_jdx(args) -> int:
    j2c = _load("jdx2csv")
    return j2c.main(args.rest) or 0


# ---------------------------------------------------------------------------
# 子命令：化合物库 / 分子式工具
# ---------------------------------------------------------------------------

def cmd_ls(args) -> int:
    db = formula.load_compounds()
    if not db:
        print("化合物库为空。用 `chem.py add <名称> <分子式>` 添加。")
        return 0
    print(f"化合物库 ({formula.COMPOUNDS_PATH})  共 {len(db)} 条\n")
    for name, e in db.items():
        cols = formula.parse_formula(e["formula"])
        print(f"  {name}")
        print(f"      分子式: {formula.pretty_formula(cols, e.get('z', 0))}")
        print(f"      ASCII : {e['formula']}   z={e.get('z', '-')}")
        if e.get("note"):
            print(f"      备注  : {e['note']}")
        print()
    return 0


def cmd_add(args) -> int:
    formula.save_compound(args.name, args.formula, args.z, args.note or "")
    cols = formula.parse_formula(args.formula)
    print(f"已保存: {args.name}")
    print(f"  {formula.pretty_formula(cols, args.z or 0)}   z={args.z or '-'}")
    return 0


def cmd_rm(args) -> int:
    ok = formula.delete_compound(args.name)
    print(f"已删除: {args.name}" if ok else f"未找到: {args.name}")
    return 0 if ok else 1


def cmd_parse(args) -> int:
    cols, z, note = _resolve(args.formula, args.z)
    _print_input(args.formula, cols, z, note)
    print(f"\n逐列 (供 Excel / --elements 使用):")
    for c in cols:
        print(f"  {c.element:<3} {c.isotope:<8} {c.count}")
    print("\n--elements 形式:")
    print("  " + " ".join(f"{c.element} {c.isotope} {c.count}" for c in cols))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chem.py",
        description="同位素计算 / 杂质枚举 / 解卷积 —— 统一命令行（无需 Excel）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="分子式可写别名（见 `chem.py ls`）或字符串，如 C128[13C]6H198N26[15N]2O35S2")
    sub = p.add_subparsers(dest="cmd", metavar="<命令>")

    def common(sp):
        sp.add_argument("formula", help="化合物别名 或 分子式字符串")
        sp.add_argument("--z", type=int, default=None, help="电荷数（默认取别名设定，否则 1）")
        sp.add_argument("--tol", type=float, default=0.001, help="峰合并容差 Da（默认 0.001）")

    sp = sub.add_parser("imp", help="枚举同位素取代杂质 + 逐杂质理论峰")
    common(sp)
    sp.add_argument("--min-ab", type=float, default=0.05, help="丰度阈值%%（默认 0.05）")
    sp.add_argument("--top-n", type=int, default=50, help="每杂质保留峰数（默认 50）")
    sp.add_argument("--out-dir", default=None, help="输出目录（默认脚本所在目录）")
    sp.add_argument("--prefix", default=None, help="输出文件名前缀（默认 impurity）")
    sp.add_argument("--no-files", action="store_true", help="只打印，不写文件")
    sp.set_defaults(func=cmd_imp)

    sp = sub.add_parser("theo", help="计算单个分子式的理论同位素谱")
    common(sp)
    sp.add_argument("--min-ab", type=float, default=0.05, help="丰度阈值%%（默认 0.05）")
    sp.add_argument("--top-n", type=int, default=20, help="保留峰数（默认 20）")
    sp.add_argument("--out", default=None, help="导出 CSV 路径")
    sp.set_defaults(func=cmd_theo)

    sp = sub.add_parser("deconv", help="解卷积 / 同位素富集度（质心法 + NNLS）")
    common(sp)
    sp.add_argument("--spectrum", required=True, help="实测谱 CSV/JDX 路径")
    sp.add_argument("--lo", type=float, default=None, help="拟合窗口下限 m/z（默认自动）")
    sp.add_argument("--hi", type=float, default=None, help="拟合窗口上限 m/z（默认自动）")
    sp.add_argument("--resolution", type=float, default=None, help="分辨率 FWHM（默认 30000）")
    sp.add_argument("--nnls", dest="nnls", action="store_true", default=True,
                    help="运行 NNLS 逐杂质拟合（默认开）")
    sp.add_argument("--no-nnls", dest="nnls", action="store_false", help="只跑质心法")
    sp.add_argument("--out", default=None,
                    help="结果 JSON 路径（默认 ./deconv_result.json）")
    sp.add_argument("--json", action="store_true",
                    help="把完整 JSON 打到 stdout（供管道/程序调用）")
    sp.set_defaults(func=cmd_deconv)

    # add_help=False：让 -h/--help 也一并透传给 lcd2csv，而不是被本层截获
    sp = sub.add_parser("lcd", help="岛津 .lcd/.jdx 导出 CSV（参数透传 lcd2csv.py）",
                        add_help=False)
    sp.add_argument("rest", nargs=argparse.REMAINDER, help="传给 lcd2csv.py 的参数")
    sp.set_defaults(func=cmd_lcd)

    sp = sub.add_parser("jdx", help="岛津 .jdx 导出 CSV（参数透传 jdx2csv.py）",
                        add_help=False)
    sp.add_argument("rest", nargs=argparse.REMAINDER, help="传给 jdx2csv.py 的参数")
    sp.set_defaults(func=cmd_jdx)

    sp = sub.add_parser("ls", help="列出化合物库")
    sp.set_defaults(func=cmd_ls)

    sp = sub.add_parser("add", help="添加/更新化合物别名")
    sp.add_argument("name")
    sp.add_argument("formula")
    sp.add_argument("--z", type=int, default=None)
    sp.add_argument("--note", default=None)
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("rm", help="删除化合物别名")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_rm)

    sp = sub.add_parser("parse", help="解析分子式，输出逐列/--elements 形式")
    common(sp)
    sp.set_defaults(func=cmd_parse)

    return p


def main(argv=None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    # `lcd` 全量透传：argparse 的 REMAINDER 对前导 `-` 参数不可靠
    # （`chem.py lcd --help` 会被顶层解析器截获报错），故在入口直接分流。
    if raw and raw[0] == "lcd":
        return _load("lcd2csv").main(raw[1:]) or 0

    if raw and raw[0] == "jdx":
        return _load("jdx2csv").main(raw[1:]) or 0

    p = build_parser()
    args = p.parse_args(raw)
    if not getattr(args, "cmd", None):
        p.print_help()
        return 0
    try:
        return args.func(args)
    except formula.FormulaError as e:
        print(f"[分子式错误] {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"[文件不存在] {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[错误] {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
