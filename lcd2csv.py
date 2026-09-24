"""lcd2csv.py - 岛津 LabSolutions .lcd → 谱图 CSV（m/z, intensity）

⚠️⚠️ 重要告警（2026-09-24）：本脚本的质量轴换算是**错的**，不要用于定量或化合物归属。 ⚠️⚠️
    本脚本依赖 openszraw 的线性换算 mz = u64/1e12，但本项目 QTOF 变体的 .lcd
    （QTFL RawData/Centroid Data）真实关系是 TOF 平方式：

            m/z = a·x² + c        （a、c 需用已知锚点最小二乘标定）

    线性换算会得到随 m/z 漂移的假峰位（实测把真实 942.15 显示成 1442），
    曾据此误判"这批数据不含 SPH20291"。
    → 需要正确的 .lcd 读取，改用：
        * 同位素杂质计算/lcd_io.py            （set_calibration(a,c) + read_lcd）
        * 同位素取代率计算/embedded_engine.py （自包含版）
      详见 docs/adr/0008-lcd-tof-quadratic-calibration.md、AGENTS.md 坑 #1。
    本脚本保留仅用于"看大致扫描结构/TIC"。

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


def _rt_average_bins(path: str, rt_min: float, rt_max: float, ms_level: int = 1,
                     bin_da: float = 0.002) -> Tuple[dict, int]:
    """平均 RT 窗口内 MS 谱 → {m/z分箱: 强度}。

    bin_da：m/z 分箱宽度（默认 2 mDa，可合并扫描间 ~1 mDa 的质量漂移重复峰）。
    返回 (bins, 使用的谱数)。
    """
    reader = openszraw.RawReader(path)
    bins: dict = {}
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
            key = round(float(m) / bin_da) * bin_da
            bins[key] = bins.get(key, 0.0) + float(it)
    if n_used == 0:
        raise ValueError(f"RT 窗口 [{rt_min}, {rt_max}] 内无 MS{ms_level} 谱")
    return bins, n_used


def export_rt_average(path: str, rt_min: float, rt_max: float, ms_level: int = 1
                      ) -> Tuple[np.ndarray, np.ndarray]:
    """平均 RT 窗口内 MS1 谱：m/z 按 2 mDa 分箱求和，返回 (mz, intensity)"""
    bins, _ = _rt_average_bins(path, rt_min, rt_max, ms_level)
    mz = np.array(sorted(bins.keys()), dtype=float)
    intensity = np.array([bins[m] for m in mz], dtype=float)
    return mz, intensity


# ---------------------------------------------------------------------------
# 源数据形态识别：profile（轮廓谱，连续采样）vs centroid（中心化峰列表）
# ---------------------------------------------------------------------------
# 判据（与 deconv.detect_mode 一致）：
#   1) 采样密度 > 50 点/Da → profile
#   2) 中位相邻步长 < 0.01 Da → profile（对强度过滤后点数缩水的谱仍稳健）
#   否则 → centroid
# 实证：LCMS-9030 centroid 每扫描 10-100 点、中位间隙 0.2-2.2 Da；
#        profile 每峰 10-20 点、步长 ~0.003-0.005 Da。

def detect_mode(mz: np.ndarray) -> str:
    if len(mz) < 3:
        return 'centroid'
    span = float(mz[-1] - mz[0])
    if span > 0 and len(mz) / span > 50.0:
        return 'profile'
    steps = np.diff(np.sort(mz))
    steps = steps[steps > 0]
    if len(steps):
        if float(np.median(steps)) < 0.01:
            return 'profile'
    return 'centroid'


def source_mode(path: str, rt_min: float = None, rt_max: float = None,
                ms_level: int = 1) -> str:
    """识别 .lcd 源数据的形态（profile/centroid）。

    给 RT 窗口则取窗口内 TIC 最强的一张 MS1 原始谱判断；否则用扫描 0。
    """
    reader = openszraw.RawReader(path)
    best_idx, best_tic = 0, -1.0
    for i in range(reader.scan_count):
        sp = reader.read_spectrum(i)
        if int(sp.ms_level) != ms_level:
            continue
        rt = float(sp.retention_time_sec)
        if rt_min is not None and (rt < rt_min or rt > rt_max):
            continue
        tic = float(np.sum(sp.intensity))
        if tic > best_tic:
            best_tic, best_idx = tic, i
    sp = reader.read_spectrum(best_idx)
    return detect_mode(np.asarray(sp.mz, dtype=float))


def export_rt_subtract(path: str, rt_min: float, rt_max: float,
                       bg_min: float, bg_max: float, ms_level: int = 1,
                       bin_da: float = 0.002) -> Tuple[np.ndarray, np.ndarray, int, int]:
    """峰窗口平均谱 − 背景窗口平均谱（m/z 分箱对齐，负值截零）。

    返回 (mz, intensity, 峰窗口谱数, 背景窗口谱数)。
    """
    sample, n_s = _rt_average_bins(path, rt_min, rt_max, ms_level, bin_da)
    bg, n_b = _rt_average_bins(path, bg_min, bg_max, ms_level, bin_da)
    out = {}
    for m, s in sample.items():
        v = s - bg.get(m, 0.0)
        if v > 0:
            out[m] = v
    mz = np.array(sorted(out.keys()), dtype=float)
    intensity = np.array([out[m] for m in mz], dtype=float)
    return mz, intensity, n_s, n_b


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
    p.add_argument('--bg', nargs=2, type=float, default=None, metavar=('MIN', 'MAX'),
                   help='背景扣除：RT [MIN, MAX] 秒窗口平均谱，从 --rt 峰窗口谱中扣除'
                        '（需与 --rt 同用；分钟×60 转秒）')
    p.add_argument('--bin-da', type=float, default=0.002,
                   help='RT 平均/背景扣除的 m/z 分箱宽度 Da（默认 0.002，合并扫描间质量漂移）')
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
            mode = detect_mode(mz)
        elif args.rt is not None:
            if args.bg is not None:
                mz, intensity, n_s, n_b = export_rt_subtract(
                    args.lcd, args.rt[0], args.rt[1], args.bg[0], args.bg[1],
                    args.ms_level, args.bin_da)
                desc = (f"RT [{args.rt[0]}-{args.rt[1]}]s 平均 MS{args.ms_level}"
                        f"（{n_s} 谱）− 背景 RT [{args.bg[0]}-{args.bg[1]}]s（{n_b} 谱）")
            else:
                mz, intensity = export_rt_average(args.lcd, args.rt[0], args.rt[1], args.ms_level)
                desc = f"RT [{args.rt[0]}-{args.rt[1]}]s 平均 MS{args.ms_level}"
            mode = source_mode(args.lcd, args.rt[0], args.rt[1], args.ms_level)
        else:
            mz, intensity, idx = export_max_tic(args.lcd, args.ms_level)
            desc = f"TIC 最强 scan #{idx} (MS{args.ms_level})"
            mode = detect_mode(mz)

        tag = {'profile': '轮廓谱', 'centroid': '中心化'}.get(mode, mode)

        if args.out:
            write_csv(mz, intensity, args.out)
            print(f"已导出 {desc}：{len(mz)} 点 → {args.out}")
            print(f"源数据形态：{tag}（{mode}）")
        else:
            print(f"# {desc}（{len(mz)} 点，{tag}）")
            for m, i in zip(mz, intensity):
                print(f'{m:.6f}\t{i:.8e}')
        return 0
    except Exception as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
