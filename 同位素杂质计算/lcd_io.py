# -*- coding: utf-8 -*-
"""
lcd_io - 岛津 Q-TOF .lcd（QTFL Centroid）读取与 m/z 换算

关键结论（2026-09-23 实测，详见 解卷积/20260914分析/report.md §3.1）
------------------------------------------------------------------
本变体岛津 QTOF .lcd 中，`QTFL RawData/Centroid Data` 的存储值 x 与 m/z 满足

        m/z = a·x² + c            （TOF 平方关系 + 常数项）

而 **不是** openszraw 文档所述 m/z = x/1e12。若按线性关系换算，峰位偏差随 m/z
变化（在本项目数据上表现为"主簇落在 1442/1444"的假峰位，真实位置应为
942.15 / 706.85），会完全误导化合物归属判断。

a、c 需由已知锚点最小二乘标定（依赖目标化合物与电荷），故本模块要求调用方
显式传入 tof_calib=(a, c)；可用 calibrate_tof(anchors) 求得，典型残差 ±10 ppm。

接口与 mzml_io 对齐：read_lcd 返回
    {"mz": ..., "intensity": ..., "n_scans": ..., "rt_lo": ..., "rt_hi": ...}
可直接交给 calc.py 的 analyze_profile() 使用。
"""
from __future__ import annotations

import struct
from typing import List, Sequence, Tuple

import numpy as np
import olefile

IDX_STREAM = "QTFL RawData/Centroid Index"
DAT_STREAM = "QTFL RawData/Centroid Data"
HDR = 72          # 扫描头长度
RT_OFF, PAY_OFF, W_OFF = 4, 24, 36   # 头内字段偏移：RT(ms) / payload 字节数 / 强度宽度


def read_scans_x(path: str) -> List[Tuple[float, np.ndarray, np.ndarray]]:
    """解析 QTFL Centroid 流，返回 [(rt_sec, x_array, intensity_array), ...]。

    x 为文件内存储的质量轴值（**未做 TOF 换算**）。扫描头 72 B：
      +4  RT (ms)
      +24 payload 字节数
      +36 强度宽度 w ∈ {1, 2, 4}
    payload = N×u64 质量轴 + N×w 强度，N = payload / (8 + w)。
    """
    ole = olefile.OleFileIO(path)
    try:
        idx = ole.openstream(IDX_STREAM).read()
        dat = ole.openstream(DAT_STREAM).read()
    finally:
        ole.close()
    n = len(idx) // 24
    offs = [struct.unpack_from("<Q", idx, i * 24)[0] for i in range(n)]
    out: List[Tuple[float, np.ndarray, np.ndarray]] = []
    for i in range(n):
        o = offs[i]
        end = offs[i + 1] if i + 1 < n else len(dat)
        blk = dat[o:end]
        if len(blk) < HDR:
            continue
        rt = struct.unpack_from("<I", blk, RT_OFF)[0] / 1000.0
        payload = struct.unpack_from("<I", blk, PAY_OFF)[0]
        w = struct.unpack_from("<I", blk, W_OFF)[0]
        if w not in (1, 2, 4):
            w = 2
        npts = payload // (8 + w)
        if npts <= 0:
            continue
        x = np.array([struct.unpack_from("<Q", blk, HDR + k * 8)[0] / 1e12
                      for k in range(npts)], dtype=np.float64)
        fmt = {1: "<B", 2: "<H", 4: "<I"}[w]
        it = np.array([struct.unpack_from(fmt, blk, HDR + npts * 8 + k * w)[0]
                       for k in range(npts)], dtype=np.float64)
        out.append((rt, x, it))
    return out


def window_profile(scans, rt_lo: float, rt_hi: float, bin_x: float = 1.0e-3
                   ) -> Tuple[np.ndarray, np.ndarray, int]:
    """累加 RT 窗口内的质心谱并按 x 分箱（默认 1e-3，合并扫描间质量抖动）。

    返回 (x_bins, intensity, n_scans)。
    """
    bins: dict = {}
    n = 0
    for rt, x, it in scans:
        if not (rt_lo <= rt <= rt_hi):
            continue
        n += 1
        key = np.round(x / bin_x).astype(np.int64)
        for k, v in zip(key, it):
            bins[k] = bins.get(k, 0.0) + float(v)
    if not bins:
        return np.array([]), np.array([]), 0
    ks = np.array(sorted(bins), dtype=np.int64)
    return ks * bin_x, np.array([bins[k] for k in ks], dtype=np.float64), n


def calibrate_tof(anchors: Sequence[Tuple[float, float]]):
    """最小二乘标定 m/z = a·x² + c。

    anchors: [(x_measured, mz_theory), ...]。
    返回 (a, c, resid_ppm)。
    """
    xs = np.array([x * x for x, _ in anchors], dtype=np.float64)
    ys = np.array([y for _, y in anchors], dtype=np.float64)
    A = np.c_[xs, np.ones_like(xs)]
    (a, c), *_ = np.linalg.lstsq(A, ys, rcond=None)
    resid = [float((a * x * x + c - y) / y * 1e6) for (x, _), y in zip(anchors, ys)]
    return float(a), float(c), resid


def read_lcd(path: str, rt_lo: float, rt_hi: float, tof_calib: Tuple[float, float],
             bin_x: float = 1.0e-3) -> dict:
    """读取 .lcd 指定 RT 窗口并换算为 m/z。

    tof_calib: (a, c)，由 calibrate_tof 标定得到。
    返回 {"mz","intensity","n_scans","rt_lo","rt_hi"}（m/z 升序）。
    """
    a, c = tof_calib
    x, it, n = window_profile(read_scans_x(path), rt_lo, rt_hi, bin_x)
    if len(x) == 0:
        return {"mz": np.array([]), "intensity": np.array([]), "n_scans": 0,
                "rt_lo": rt_lo, "rt_hi": rt_hi}
    mz = a * x * x + c
    o = np.argsort(mz)
    return {"mz": mz[o], "intensity": it[o], "n_scans": n,
            "rt_lo": rt_lo, "rt_hi": rt_hi}
