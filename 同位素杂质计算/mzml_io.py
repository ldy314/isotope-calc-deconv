# -*- coding: utf-8 -*-
"""
mzml_io - 岛津 Shimadzu LCMS-9030 mzML 读取器（profile 谱）

功能：
  - 流式解析 mzML（profile spectrum，64-bit m/z + 32-bit intensity，无压缩）；
  - 在指定 RT 窗口内累加所有扫描，得到一条 (m/z, intensity) 轮廓谱；
  - 自动检测目标 m/z 附近的 RT 出峰窗口；
  - 提供「窗口积分」与「峰顶高度」两种从轮廓谱提取强度的方式。

依赖：numpy，标准库。
"""
from __future__ import annotations

import base64
import struct
import xml.etree.ElementTree as ET
import zlib
from typing import Optional, Tuple

import numpy as np

NS = "http://psi.hupo.org/ms/mzml"
M = {"m": NS}


def _decode_binary(b64_text: str, precision: int, compressed: bool) -> np.ndarray:
    """解码 binaryDataArray 的 base64 文本为 float 数组。

    precision: 64 (MS:1000523) 或 32 (MS:1000521)
    compressed: 是否 zlib (MS:1000574)
    返回 float64 数组。
    """
    raw = base64.b64decode(b64_text)
    if compressed:
        raw = zlib.decompress(raw)
    size = 8 if precision == 64 else 4
    n = len(raw) // size
    # mzML binary data 约定为小端（little-endian）；岛津 LCMS-9030 实测亦为小端
    fmt = "<" + ("d" if precision == 64 else "f") * n
    vals = struct.unpack(fmt, raw)
    return np.array(vals, dtype=np.float64)


def _iter_spectra(path):
    """流式产出 (rt_seconds, mz_np, int_np) 三元组（仅含 m/z 与 intensity 数组的谱）。"""
    for ev, el in ET.iterparse(path, events=("end",)):
        if el.tag.split("}")[-1] != "spectrum":
            continue
        rt = None
        mz = None
        inten = None
        for c in el.iter():
            t = c.tag.split("}")[-1]
            if t == "cvParam":
                a = c.get("accession")
                if a == "MS:1000016":  # scan start time
                    v = float(c.get("value"))
                    unit = (c.get("unitName") or "").lower()
                    # 实测为 second；若个别文件标记 minute 且数值异常大则换算
                    if "min" in unit and v > 1000:  # 分钟不会 >1000
                        v *= 60.0
                    rt = v
            elif t == "binaryDataArray":
                kind = None
                prec = 64
                comp = False
                b64 = None
                for cv in c.findall("m:cvParam", M):
                    a = cv.get("accession")
                    if a == "MS:1000514":
                        kind = "mz"
                    elif a == "MS:1000515":
                        kind = "int"
                    elif a == "MS:1000523":
                        prec = 64
                    elif a == "MS:1000521":
                        prec = 32
                    elif a == "MS:1000574":
                        comp = True
                    # MS:1000576 = no compression -> comp=False（默认）
                bin_el = c.find("m:binary", M)
                if bin_el is not None and bin_el.text:
                    b64 = bin_el.text.strip()
                if kind and b64:
                    arr = _decode_binary(b64, prec, comp)
                    if kind == "mz":
                        mz = arr
                    else:
                        inten = arr
        if rt is not None and mz is not None and inten is not None:
            yield rt, mz, inten
        el.clear()


def detect_rt_window(path: str, target_mz: float, half_width_mz: float = 0.5,
                     frac: float = 0.1) -> Tuple[float, float, np.ndarray, np.ndarray]:
    """自动检测目标 m/z 附近有信号的 RT 窗口。

    做法：流式扫描，计算每扫描在 [target_mz-half, target_mz+half] 内的总强度；
    取超过最大强度 frac 比例的连续 RT 段作为出峰窗口。
    返回 (rt_lo, rt_hi, rt_array, band_intensity_array)。
    """
    rts = []
    bands = []
    for rt, mz, inten in _iter_spectra(path):
        lo = target_mz - half_width_mz
        hi = target_mz + half_width_mz
        mask = (mz >= lo) & (mz <= hi)
        bands.append(float(inten[mask].sum()) if mask.any() else 0.0)
        rts.append(rt)
    rts = np.array(rts, dtype=np.float64)
    bands = np.array(bands, dtype=np.float64)
    if bands.max() <= 0:
        return (float(rts.min()), float(rts.max()), rts, bands)
    thr = bands.max() * frac
    idx = np.where(bands >= thr)[0]
    if len(idx) == 0:
        return (float(rts.min()), float(rts.max()), rts, bands)
    rt_lo = float(rts[idx.min()])
    rt_hi = float(rts[idx.max()])
    return rt_lo, rt_hi, rts, bands


def read_mzml(path: str, rt_lo: Optional[float] = None, rt_hi: Optional[float] = None,
              target_mz: Optional[float] = None, half_width_mz: float = 0.5,
              frac: float = 0.1, bin_width: float = 0.001):
    """读取 mzML，在 RT 窗口 [rt_lo, rt_hi] 内累加所有扫描。

    注意：岛津该文件实为**质心化（centroided）**谱——每扫描是稀疏峰列表且峰位不固定，
    故不能按索引对齐相加。这里把所有峰按 m/z 分箱（默认 0.001 Da）累加，得到一条
    可用于窗口提取的轮廓谱。

    若 rt_lo/rt_hi 为 None，则先用 detect_rt_window（围绕 target_mz）自动确定窗口。
    返回 dict:
        mz        : 累加后的 m/z 网格 (np.ndarray, 升序)
        intensity : 累加后的强度 (np.ndarray)
        n_scans   : 累加的扫描数
        rt_lo, rt_hi : 实际使用的 RT 窗口
    """
    if rt_lo is None or rt_hi is None:
        if target_mz is None:
            raise ValueError("未提供 RT 窗口且未提供 target_mz，无法自动检测")
        rt_lo, rt_hi, _, _ = detect_rt_window(path, target_mz, half_width_mz, frac)

    acc = {}  # 分箱后的 m/z -> 强度累加
    inv = 1.0 / bin_width
    n = 0
    for rt, mz, inten in _iter_spectra(path):
        if rt < rt_lo or rt > rt_hi:
            continue
        for x, y in zip(mz, inten):
            yi = float(y)
            if yi <= 0:
                continue
            key = round(float(x) * inv) * bin_width
            acc[key] = acc.get(key, 0.0) + yi
        n += 1
    if not acc:
        return {"mz": np.array([]), "intensity": np.array([]), "n_scans": 0,
                "rt_lo": rt_lo, "rt_hi": rt_hi}
    mz_grid = np.array(sorted(acc.keys()), dtype=np.float64)
    int_sum = np.array([acc[k] for k in mz_grid], dtype=np.float64)
    return {"mz": mz_grid, "intensity": int_sum, "n_scans": n,
            "rt_lo": rt_lo, "rt_hi": rt_hi}


def extract(mz: np.ndarray, intensity: np.ndarray, center: float, ppm: float,
            mode: str = "integral") -> dict:
    """在轮廓谱中围绕 center（±ppm/2 窗口）提取强度。

    mode:
      'integral' -> 窗口内强度求和
      'top'      -> 窗口内最大强度，并返回其 m/z（用于质量校准）
    返回 dict：{value, peak_mz (仅 top), hw, lo, hi}
    """
    hw = center * ppm / 1e6 / 2.0  # 半窗宽
    lo = center - hw
    hi = center + hw
    mask = (mz >= lo) & (mz <= hi)
    if not mask.any():
        if mode == "top":
            return {"value": 0.0, "peak_mz": center, "hw": hw, "lo": lo, "hi": hi}
        return {"value": 0.0, "hw": hw, "lo": lo, "hi": hi}
    if mode == "top":
        sub_mz = mz[mask]
        sub_int = intensity[mask]
        i = int(np.argmax(sub_int))
        return {"value": float(sub_int[i]), "peak_mz": float(sub_mz[i]),
                "hw": hw, "lo": lo, "hi": hi}
    return {"value": float(intensity[mask].sum()), "hw": hw, "lo": lo, "hi": hi}


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else None
    if not p:
        print("usage: python mzml_io.py <file.mzML>")
        raise SystemExit(1)
    d = read_mzml(p, target_mz=944.8125, half_width_mz=0.5, frac=0.1)
    print(f"RT window: {d['rt_lo']:.2f}-{d['rt_hi']:.2f}s  scans={d['n_scans']}")
    print(f"grid points: {len(d['mz'])}  total intensity: {d['intensity'].sum():.0f}")
    r = extract(d["mz"], d["intensity"], 944.8125, 5.0, "top")
    print(f"@944.8125 top: {r['value']:.0f} at {r['peak_mz']:.4f}")
