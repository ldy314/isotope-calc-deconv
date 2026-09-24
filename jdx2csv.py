#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
jdx2csv.py — 岛津 LabSolutions 导出的 JCAMP-DX (.jdx) 质谱谱图 → (m/z, intensity) CSV

与 lcd2csv.py 平行：解卷积引擎 deconv.load_spectrum 只读 CSV，本工具把 .jdx
（质心 / 轮廓谱）转成同一格式，于是 `chem.py deconv --spectrum xxx.jdx` 能直接吃，
全程无需 Excel。

实现要点
--------
- 解析 JCAMP ##XYDATA=(XY..XY) / ##XYPOINTS= 数据块；多数据块会被顺序拼接成一张谱
  （本数据集为单张求和/质心谱，天然只有一块）。
- 每个数据行按空白切分为若干 token，token 形如 "x,y"（逗号分隔或小数点）。
- 仅保留正强度点，按 m/z 升序输出；输出 CSV 两列 `m/z,intensity`
  （首行表头会被 deconv.load_spectrum 当作非数值行跳过，安全）。
- 假设 .jdx 已存真实 m/z 与强度（LabSolutions 质心谱通常无 XFACTOR 缩放）；
  若遇到 (X++(Y..Y)) 等需 FIRSTX/LASTX 插值的格式，会给出明确报错而非静默错解。

用法
----
    python jdx2csv.py --jdx data.jdx --out data.csv
    python jdx2csv.py --jdx data.jdx            # 默认写到同目录同名 .csv
    python jdx2csv.py --jdx data.jdx --list      # 打印文件头元数据
"""
from __future__ import annotations

import argparse
import os
import sys


def _read_meta(lines) -> dict:
    """提取 ##KEY=VALUE 头信息（到 ##END 为止）。"""
    meta = {}
    for ln in lines:
        s = ln.strip()
        if not s.startswith("##"):
            continue
        if s.startswith("##END"):
            break
        if "=" in s:
            k, v = s[2:].split("=", 1)
            meta[k.strip()] = v.strip()
    return meta


def _iter_points(lines):
    """从 JCAMP 行流中收集所有数据点，yield (mz, intensity)。"""
    capture = False
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("##"):
            if s.startswith("##XYDATA") or s.startswith("##XYPOINTS"):
                capture = True
            else:
                capture = False
            continue
        if not capture:
            continue
        for tok in s.split():
            if "," not in tok:
                # (XY..XY) 每个 token 应含逗号；无逗号说明非预期格式
                continue
            a, b = tok.split(",", 1)
            try:
                x = float(a)
                y = float(b)
            except ValueError:
                continue
            if y > 0:
                yield x, y


def convert(jdx_path: str, out_csv: str | None = None) -> str:
    """把 .jdx 转成 CSV，返回输出 CSV 路径。"""
    if out_csv is None:
        base = os.path.splitext(jdx_path)[0]
        out_csv = base + ".csv"
    with open(jdx_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    pts = sorted(_iter_points(lines), key=lambda t: t[0])
    if len(pts) < 2:
        raise ValueError(f"未在 {jdx_path} 中解析到足够的 XY 数据点（{len(pts)} 个）")

    os.makedirs(os.path.dirname(os.path.abspath(out_csv)), exist_ok=True)
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        f.write("m/z,intensity\n")
        for x, y in pts:
            f.write(f"{x:.6f},{y:.6f}\n")
    return out_csv


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="jdx2csv",
        description="岛津 .jdx 质谱谱图 → (m/z,intensity) CSV（供 deconv 解卷积）",
    )
    p.add_argument("--jdx", required=True, metavar="FILE", help="岛津 .jdx 文件路径")
    p.add_argument("--out", metavar="CSV", default=None,
                   help="输出 CSV 路径（默认写到同目录同名 .csv）")
    p.add_argument("--list", action="store_true", help="只打印文件头元数据，不导出")
    return p.parse_args(argv)


def main(argv=None) -> int:
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if not os.path.exists(args.jdx):
        print(f"[错误] 文件不存在: {args.jdx}", file=sys.stderr)
        return 2
    with open(args.jdx, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    meta = _read_meta(lines)
    if args.list:
        print(f"文件: {args.jdx}")
        for k in ("TITLE", "DATE", "TIME", "IONIZATION_MODE", "SCAN_NUMBER",
                  "RETENTION_TIME", "NPOINTS", "XUNITS", "DATA_TYPE"):
            if k in meta:
                print(f"  {k}: {meta[k]}")
        n = sum(1 for _ in _iter_points(lines))
        print(f"  解析数据点: {n}")
        return 0
    out = convert(args.jdx, args.out)
    n = sum(1 for _ in _iter_points(lines))
    print(f"已写出: {out}   ({n} 点)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
