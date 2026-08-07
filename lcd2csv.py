"""lcd2csv.py - 岛津 LabSolutions .lcd → 谱图 CSV（m/z, intensity）

用 OpenSZRaw（开源 clean-room 逆向，无需岛津 SDK）读取 .lcd 文件：
  - 支持 LCMS-9030（QTOF，centroid）与 IT-TOF（profile）
  - 三种取谱方式：
      --scan N         导出第 N 张谱（0-based）
      --rt MIN MAX     平均 [MIN, MAX] 秒窗口内所有 MS1 谱（自动合并 m/z）
      （默认）          自动选 TIC 最强的一张 MS1 谱
  - 输出 CSV 两列（m/z, intensity），直接供 deconv.py 使用

用法示例：
  lcd2csv.py --lcd sample.lcd --list                    # 列出所有扫描
  lcd2csv.py --lcd sample.lcd --rt 5.2 5.6 --out spec.csv
  lcd2csv.py --lcd sample.lcd --scan 42 --out spec.csv
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Tuple

import numpy as np

import openszraw


def list_scans(path: str) -> List[Tuple[int, float, int, int]]:
    """返回 [(scan_idx, rt_sec, ms_level, n_peaks), ...]"""
    reader = openszraw.RawReader(path)
    rows = []
    for i in range(reader.scan_count):
        sp = reader.read_spectrum(i)
        rows.append((i, float(sp.retention_time_sec), int(sp.ms_level), len(sp.mz)))
    return rows


def export_scan(path: str, scan_idx: int) -> Tuple[np.ndarray, np.ndarray]:
    reader = openszraw.RawReader(path)
    sp = reader.read_spectrum(scan_idx)
    return np.asarray(sp.mz, dtype=float), np.asarray(sp.intensity, dtype=float)


def export_rt_average(path: str, rt_min: float, rt_max: float, ms_level: int = 1
                      ) -> Tuple[np.ndarray, np.ndarray]:
    """平均 RT 窗口内 MS1 谱：m/z 按 0.1 mDa 分箱求和，返回 (mz, intensity)"""
    reader = openszraw.RawReader(path)
    bins: dict = {}  # m/z → intensity
    n_used = 0
    for i in range(reader.scan_count):
        sp = reader.read_spectrum(i)
        if int(sp.ms_level) != ms_level:
            continue
        rt = float(sp.retention_time_sec)
        if rt < rt_min or rt > rt_max:
            continue
        n_used += 1
        for m, it in zip(sp.mz, sp.intensity):
            key = round(float(m), 4)  # 0.1 mDa 分箱
            bins[key] = bins.get(key, 0.0) + float(it)
    if n_used == 0:
        raise ValueError(f"RT 窗口 [{rt_min}, {rt_max}] 内无 MS{ms_level} 谱")
    mz = np.array(sorted(bins.keys()), dtype=float)
    intensity = np.array([bins[m] for m in mz], dtype=float)
    return mz, intensity


def export_max_tic(path: str, ms_level: int = 1) -> Tuple[np.ndarray, np.ndarray, int]:
    """选 TIC 最强的 MS1 谱"""
    reader = openszraw.RawReader(path)
    best_idx, best_tic = -1, -1.0
    for i in range(reader.scan_count):
        sp = reader.read_spectrum(i)
        if int(sp.ms_level) != ms_level:
            continue
        tic = float(np.sum(sp.intensity))
        if tic > best_tic:
            best_tic, best_idx = tic, i
    if best_idx < 0:
        raise ValueError(f"无 MS{ms_level} 谱")
    return export_scan(path, best_idx) + (best_idx,)


def write_csv(mz: np.ndarray, intensity: np.ndarray, out: str) -> None:
    with open(out, 'w', newline='') as f:
        f.write('m/z,intensity\n')
        for m, i in zip(mz, intensity):
            f.write(f'{m:.6f},{i:.8e}\n')


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog='lcd2csv',
        description='岛津 .lcd → 谱图 CSV（m/z, intensity），供 deconv.py 解卷积',
    )
    p.add_argument('--lcd', required=True, metavar='FILE', help='岛津 .lcd 文件路径')
    p.add_argument('--list', action='store_true', help='列出所有扫描（不导出）')
    p.add_argument('--scan', type=int, default=None, metavar='N',
                   help='导出第 N 张谱（0-based）')
    p.add_argument('--rt', nargs=2, type=float, default=None, metavar=('MIN', 'MAX'),
                   help='平均 RT 窗口 [MIN, MAX] 秒内的 MS1 谱')
    p.add_argument('--ms-level', type=int, default=1, help='MS 级（默认 1，仅 --rt/默认模式用）')
    p.add_argument('--out', metavar='CSV', default=None, help='输出 CSV 路径（默认打印到 stdout）')
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.list:
            rows = list_scans(args.lcd)
            print(f"{'#':>5}  {'RT(s)':>10}  {'MS':>3}  {'峰数':>8}")
            for idx, rt, ms, n in rows:
                print(f"{idx:>5}  {rt:>10.2f}  {ms:>3}  {n:>8}")
            return 0

        if args.scan is not None:
            mz, intensity = export_scan(args.lcd, args.scan)
            desc = f"scan #{args.scan}"
        elif args.rt is not None:
            mz, intensity = export_rt_average(args.lcd, args.rt[0], args.rt[1], args.ms_level)
            desc = f"RT [{args.rt[0]}-{args.rt[1]}]s 平均 MS{args.ms_level}"
        else:
            mz, intensity, idx = export_max_tic(args.lcd, args.ms_level)
            desc = f"TIC 最强 scan #{idx} (MS{args.ms_level})"

        if args.out:
            write_csv(mz, intensity, args.out)
            print(f"已导出 {desc}：{len(mz)} 点 → {args.out}")
        else:
            print(f"# {desc}（{len(mz)} 点）")
            for m, i in zip(mz, intensity):
                print(f'{m:.6f}\t{i:.8e}')
        return 0
    except Exception as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
