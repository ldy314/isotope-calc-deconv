# -*- coding: utf-8 -*-
"""
deconv_excel.py - Excel「解卷积」按钮调用的 Python 引擎

读取分子式(同位素编码) + 电荷 z + 实测谱 CSV，输出 JSON：
  1) 质心法（稳健默认，对包络重叠不敏感）
       - 天然形式 / 全标记形式 的理论质心 (强度加权平均 m/z)
       - 实测谱质心 (可选 m/z 窗口隔离电荷簇)
       - 平均标记原子数、平均富集度%
  2) NNLS 逐杂质相对含量（大分子可能病态，附诊断）
       - 相对含量合计偏离 100% 即判定病态不可信

用法（VBA 通过 Shell 调用）：
    python deconv_excel.py --elements C natural 128 C 13 6 H natural 198 \
        N natural 26 N 15 2 O natural 35 S natural 2 \
        --z 2 --spectrum spec.csv [--lo 1410 --hi 1420] [--nnls true] --out result.json

质心法原理：
    对每个可标记位点，被标记与未被标记两种状态在 m/z 上贡献固定偏移；
    混合物的强度加权质心在「全天然(0 标记)」与「全标记(全部位点标记)」之间线性插值。
    因此  label_fraction = (m_meas - m_nat) / (m_lab - m_nat)
          avg_labels     = label_fraction * 可标记原子总数
          enrichment_%   = label_fraction * 100
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import List, Tuple

import numpy as np

import theo
import imp
import deconv


def _centroid_of_columns(columns: List[imp.ElementColumn], z: int, tol: float) -> float:
    """计算该分子式理论同位素分布(全模式)的强度加权质心 m/z。"""
    raw = theo._convolve_columns(columns, tol=tol)  # [(中性质量, 概率)]
    if not raw:
        return None
    tot = 0.0
    mom = 0.0
    for mass, prob in raw:
        if z == 0:
            mz = mass
        else:
            mz = (mass + z * (theo.PROTON_MASS - theo.ELECTRON_MASS)) / z
        tot += prob
        mom += prob * mz
    return float(mom / tot) if tot > 0 else None


def _to_columns(triples: List[Tuple[str, str, str]]) -> List[imp.ElementColumn]:
    return [imp.ElementColumn(e, iso, int(c)) for e, iso, c in triples]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Excel 解卷积引擎：质心法富集度 + NNLS 逐杂质（带病态诊断）"
    )
    ap.add_argument("--elements", nargs="+", required=True,
                    help="元素表：三元素一组 (元素, 同位素, 个数)。例：C natural 128 C 13 6")
    ap.add_argument("--z", type=int, default=1, help="电荷数（0=中性）")
    ap.add_argument("--spectrum", required=True, help="实测谱 CSV（m/z, intensity 两列）")
    ap.add_argument("--lo", type=float, default=None, help="拟合/质心窗口下限 m/z（隔离电荷簇）")
    ap.add_argument("--hi", type=float, default=None, help="拟合/质心窗口上限 m/z")
    ap.add_argument("--resolution", type=float, default=deconv.RESOLUTION_DEFAULT,
                    help=f"仪器分辨率 FWHM（默认 {deconv.RESOLUTION_DEFAULT}）")
    ap.add_argument("--tol", type=float, default=0.001, help="质量合并容差 Da（默认 0.001）")
    ap.add_argument("--nnls", default="true", help="是否运行 NNLS 逐杂质（true/false）")
    ap.add_argument("--out", default=None, help="结果 JSON 输出路径")
    args = ap.parse_args(argv)

    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    result: dict = {}
    try:
        if len(args.elements) % 3 != 0:
            raise ValueError("--elements 必须按 (元素, 同位素, 个数) 三元组提供")
        triples = [(args.elements[i], args.elements[i + 1], args.elements[i + 2])
                   for i in range(0, len(args.elements), 3)]
        columns = _to_columns(triples)

        # 可标记原子总数 = 所有非 natural 列的原子数之和
        total_mod = sum(c.count for c in columns if not c.is_natural)
        result["total_mod_atoms"] = total_mod
        # 总碳数：用于 NNLS 病态判据（碳数越多，同位素包络越重叠 → 越不可分）
        total_carbons = sum(c.count for c in columns if c.element == "C")
        result["total_carbons"] = total_carbons

        # 天然形式：所有修饰列替换为 natural
        nat_triples = [(c.element, "natural", c.count) for c in columns]
        # 全标记形式：输入原样（修饰列保持标记同位素）
        lab_triples = triples

        m_nat = _centroid_of_columns(_to_columns(nat_triples), args.z, args.tol)
        m_lab = _centroid_of_columns(_to_columns(lab_triples), args.z, args.tol)
        result["m_nat"] = m_nat
        result["m_lab"] = m_lab

        # 实测谱
        smz, sint, mode = deconv.load_spectrum(args.spectrum)
        result["spectrum_mode"] = mode
        if args.lo is not None or args.hi is not None:
            lo = args.lo if args.lo is not None else float(smz.min())
            hi = args.hi if args.hi is not None else float(smz.max())
        else:
            # 自动窗口：未指定时围绕理论天然/全标记质心 ±2 Da，
            # 隔离对应电荷簇（实测谱常含多电荷态，整谱加权质心会被拉偏）
            if m_nat is not None and m_lab is not None:
                lo = min(m_nat, m_lab) - 2.0
                hi = max(m_nat, m_lab) + 2.0
            else:
                lo, hi = float(smz.min()), float(smz.max())
        mm = (smz >= lo) & (smz <= hi)
        if mm.any() and sint[mm].sum() > 0:
            m_meas = float(np.sum(sint[mm] * smz[mm]) / np.sum(sint[mm]))
        else:
            m_meas = None
        result["m_meas"] = m_meas
        result["window"] = [
            float(smz[mm].min()) if mm.any() else None,
            float(smz[mm].max()) if mm.any() else None,
        ]

        # 质心法富集度
        if total_mod > 0 and None not in (m_nat, m_lab, m_meas):
            denom = m_lab - m_nat
            f = (m_meas - m_nat) / denom if abs(denom) > 1e-9 else None
            result["label_fraction"] = f
            result["avg_labels"] = (f * total_mod) if f is not None else None
            result["enrichment_pct"] = (f * 100.0) if f is not None else None
        else:
            result["label_fraction"] = None
            result["avg_labels"] = None
            result["enrichment_pct"] = None
            if total_mod == 0:
                result["note"] = "无修饰同位素（无标记位点），无法计算富集度；NNLS 也无杂质可解。"

        # NNLS 逐杂质（可选）
        do_nnls = str(args.nnls).lower() in ("1", "true", "yes", "y")
        result["nnls_requested"] = do_nnls
        if do_nnls and total_mod > 0:
            try:
                dec = deconv.deconvolve(
                    columns, args.spectrum, z=args.z, tol=args.tol,
                    resolution=args.resolution, mode="auto",
                    with_baseline=True, merge_degenerate=True,
                )
                species = [
                    {
                        "rank": s["rank"], "name": s["name"],
                        "relative_content": s["relative_content"],
                        "merged": s["merged"], "is_target": s["is_target"],
                    }
                    for s in dec["species"]
                ]
                rel_sum = float(np.sum([s["relative_content"] for s in species]))
                ill = abs(rel_sum - 100.0) > 15.0
                # 大碳数分子（>50 C）同位素包络高度重叠，NNLS 非唯一；
                # 即使相对含量合计≈100% 也可能是不稳定解（合成数据已证明可行>200%），
                # 此时以质心法为准。判据：合计偏差小 且 碳数<=50 才视为可靠。
                reliable = (not ill) and (total_carbons <= 50)
                result["nnls"] = {
                    "species": species,
                    "relative_content_sum": rel_sum,
                    "mass_offset_ppm": dec["mass_offset_ppm"],
                    "residual_rss": dec["residual_rss"],
                    "n_species": dec["n_species"],
                    "n_groups": dec["n_groups"],
                    "ill_conditioned": ill,
                    "reliable": reliable,
                    "unreliable_reason": (
                        None if reliable else
                        ("相对含量合计偏离 100%（病态非唯一解）" if ill
                         else f"碳数={total_carbons} 过多，同位素包络重叠，NNLS 不可靠（以质心法为准）")
                    ),
                }
            except Exception as e:
                result["nnls"] = {"error": str(e)}

        # 人类可读摘要
        lines = []
        lines.append(f"质心法（稳健）：实测质心={m_meas}; 天然质心={m_nat}; 全标记质心={m_lab}")
        if result.get("enrichment_pct") is not None:
            lines.append(f"平均富集度 ≈ {result['enrichment_pct']:.2f}%  "
                         f"(平均标记原子数 ≈ {result['avg_labels']:.3f} / {total_mod} 可标记位点)")
        else:
            lines.append("无修饰同位素或窗口无效，无法计算富集度。")
        if result.get("nnls") and "relative_content_sum" in result["nnls"]:
            nn = result["nnls"]
            s = nn["relative_content_sum"]
            # 判据必须与 nnls.reliable 一致：合计偏离 100%（病态）或碳数过多都不可信。
            # 否则 stdout 会与 Excel 回填的 nnls_reliable / JSON 的 reliable 自相矛盾。
            if nn.get("reliable"):
                flag = "【可用】"
            elif nn.get("ill_conditioned"):
                flag = "【病态·不可信】"
            else:
                flag = "【不可靠】"
            lines.append(f"NNLS 相对含量合计 = {s:.1f}% {flag}")
            if not nn.get("reliable") and nn.get("unreliable_reason"):
                lines.append(f"  原因：{nn['unreliable_reason']}")
        print("\n".join(lines))

    except Exception as e:
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        print("错误：" + str(e), file=sys.stderr)

    if args.out:
        try:
            base = os.path.splitext(args.out)[0]
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=1)
            # VBA 友好：标量 key=value（避免 VBA 解析 JSON）
            with open(base + "_scalars.txt", "w", encoding="utf-8") as f:
                def w(k, v):
                    f.write(f"{k}={v}\n")
                w("m_meas", "NA" if result.get("m_meas") is None else repr(result["m_meas"]))
                w("m_nat", "NA" if result.get("m_nat") is None else repr(result["m_nat"]))
                w("m_lab", "NA" if result.get("m_lab") is None else repr(result["m_lab"]))
                w("enrichment_pct", "NA" if result.get("enrichment_pct") is None else repr(result["enrichment_pct"]))
                w("avg_labels", "NA" if result.get("avg_labels") is None else repr(result["avg_labels"]))
                w("label_fraction", "NA" if result.get("label_fraction") is None else repr(result["label_fraction"]))
                w("total_mod_atoms", result.get("total_mod_atoms", "NA"))
                w("total_carbons", result.get("total_carbons", "NA"))
                w("spectrum_mode", result.get("spectrum_mode", "NA"))
                nn = result.get("nnls")
                if isinstance(nn, dict) and "relative_content_sum" in nn:
                    w("nnls_sum", repr(nn["relative_content_sum"]))
                    w("nnls_reliable", "True" if nn.get("reliable") else "False")
                    w("nnls_reason", (nn.get("unreliable_reason") or "NA"))
                    w("nnls_ill", "True" if nn.get("ill_conditioned") else "False")
                else:
                    w("nnls_sum", "NA")
                    w("nnls_reliable", "NA")
                    w("nnls_reason", "NA")
                if "error" in result:
                    w("error", result["error"])
            # VBA 友好：NNLS 逐杂质表 CSV
            with open(base + "_table.csv", "w", encoding="utf-8", newline="") as f:
                f.write("rank,name,relative_content,merged,is_target\n")
                if isinstance(nn, dict) and "species" in nn:
                    for s in nn["species"]:
                        f.write(f'{s["rank"]},{s["name"]},{s["relative_content"]:.4f},'
                                f'{int(s["merged"])},{int(s["is_target"])}\n')
        except Exception as e:
            err_path = os.path.join(
                os.path.dirname(os.path.abspath(args.out)) or ".", "deconv_err.txt"
            )
            with open(err_path, "w", encoding="utf-8") as f:
                f.write("错误：" + str(e) + "\n" + traceback.format_exc())
    else:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
