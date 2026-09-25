# -*- coding: utf-8 -*-
"""auto_calibrate 三道采纳门槛的定向回归（合成谱，已知答案，无需任何数据文件）。

背景：`auto_calibrate` 曾在「锚点同源」时最小二乘退化（a≈1.16e-16）把质量轴算塌 →
取代率全 0（详见项目根 `AGENTS.md` 坑 #10）。本脚本用合成谱验证修复后的三道门槛，
可作回归测试反复运行。

用法：
    python verify_autocal.py          # 全 PASS 退出码 0，任一 FAIL 退出码 1

用例：
    T1  锚点同源（3 个 STD 仅 z=3，理论 y 全同）→ 期望拒绝 + 真回退
    T2  多档锚点（nat/lab × z=2/3/4，初值 +50 ppm）→ 期望采纳且还原真值（<1 ppm）
    T2b 初值偏到峰搜索窗落空（a −12%）→ 期望安全拒绝（不是塌缩成 a→0）
    T3  一个锚点偏移 0.3 Da（残差超限）→ 期望拒绝（残差门槛）
    T4  混合样品（两形强度 1000:400，不足 5×）→ 期望该样品锚点被弃用
"""
from __future__ import annotations

import os
import sys

import numpy as np

# 允许从仓库内直接运行
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import embedded_engine as E  # noqa: E402

# 本项目标准物质对（SPH20291 天然型 / 全标记型）
F_NAT = "C134H198N28O35S2"
F_LAB = "C128[13C]6H198N26[15N]2O35S2"

A_T, C_T = E.A_DEFAULT, E.C_DEFAULT      # 真值（本批岛津 QTOF 实测）
Z = [2, 3, 4]

_fail = []


def check(tag: str, cond: bool, detail: str = "") -> bool:
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", tag, detail))
    if not cond:
        _fail.append(tag)
    return bool(cond)


def x_of(mz: float, a: float = A_T, c: float = C_T) -> float:
    return float(np.sqrt((mz - c) / a))


def mk_sample(pairs):
    """pairs: [(mz, intensity), ...] → (xs, it, nscan)，x 升序。"""
    xs = np.array([x_of(m) for m, _ in pairs])
    it = np.array([float(i) for _, i in pairs])
    o = np.argsort(xs)
    return xs[o], it[o], 1


def report(tag: str, a: float, c: float, resid, note: str) -> None:
    ys = [r[1] for r in resid]
    span = (" y跨度=%.4f" % (max(ys) - min(ys))) if len(ys) >= 2 else ""
    print("  a=%.8e  c=%.5f  n_anchor=%d%s" % (a, c, len(resid), span))
    print("  note=%s" % (note or "（无）"))


def main() -> int:
    cols_nat, cols_lab = E.cols_of(F_NAT), E.cols_of(F_LAB)
    apex = {}
    for z in Z:
        apex[("nat", z)] = E.apex_mz(cols_nat, z)
        apex[("lab", z)] = E.apex_mz(cols_lab, z)

    print("理论基峰 m/z（%s / %s）" % (F_NAT, F_LAB))
    for z in Z:
        print("  z=%d  nat=%.4f  lab=%.4f" % (z, apex[("nat", z)], apex[("lab", z)]))
    print()

    # ------------------------------------------------------------ T1 同源
    print("=" * 88)
    print("T1 锚点同源（3 个 STD，仅 z=3，理论 y 全同）→ 期望：拒绝 + 真回退")
    spec1 = {}
    for nm in ("STD_002", "STD_003", "STD_005"):
        spec1[nm] = mk_sample([(apex[("nat", 3)], 1000), (apex[("lab", 3)], 10)])
    a1, c1, r1, n1 = E.auto_calibrate(spec1, [3], apex, A_T, C_T)
    report("T1", a1, c1, r1, n1)
    check("拒绝（n_anchor=0）", len(r1) == 0, "got %d" % len(r1))
    check("真回退（a/c 不变）", a1 == A_T and c1 == C_T)
    check("给出原因", "无跨度" in n1 or "退化" in n1, n1[:60])

    # ------------------------------------------------------------ T2 有效
    print("=" * 88)
    print("T2 多档锚点（nat/lab 样品 z=2/3/4，y 有跨度）→ 期望：采纳且还原真值")
    spec2 = {
        "NAT": mk_sample([(apex[("nat", z)], 1000) for z in Z]
                         + [(apex[("lab", z)], 10) for z in Z]),
        "LAB": mk_sample([(apex[("lab", z)], 1000) for z in Z]
                         + [(apex[("nat", z)], 10) for z in Z]),
    }
    # 初值取「输入表典型偏差」：自动标定是精修而非盲搜，a/c 须准到让 ±0.5 Da 峰窗命中
    a0, c0 = A_T * 1.00005, C_T + 0.005
    a2, c2, r2, n2 = E.auto_calibrate(spec2, Z, apex, a0, c0)
    report("T2", a2, c2, r2, n2)
    ys2 = [r[1] for r in r2]
    check("采纳（n_anchor>=3）", len(r2) >= 3, "got %d" % len(r2))
    check("a 还原 <1 ppm", abs(a2 - A_T) / A_T * 1e6 < 1.0,
          "%.3f ppm" % (abs(a2 - A_T) / A_T * 1e6))
    check("c 还原 <1 mDa", abs(c2 - C_T) < 1e-3, "%.5f Da" % abs(c2 - C_T))
    check("y 跨度 > %.2f Da" % E.MIN_ANCHOR_SPAN,
          bool(ys2) and (max(ys2) - min(ys2)) > E.MIN_ANCHOR_SPAN,
          "%.4f" % ((max(ys2) - min(ys2)) if ys2 else float("nan")))

    # ------------------------------------------------------------ T2b 边界
    print("=" * 88)
    print("T2b 初值偏到峰窗落空（a=4.0e-4，−12%）→ 期望：安全拒绝（不是塌缩）")
    a2b, c2b, r2b, n2b = E.auto_calibrate(spec2, Z, apex, 4.0e-4, -1.0)
    report("T2b", a2b, c2b, r2b, n2b)
    check("拒绝（n_anchor=0）", len(r2b) == 0, "got %d" % len(r2b))
    check("真回退（a/c 不变，非 a→0）", a2b == 4.0e-4 and c2b == -1.0)

    # ------------------------------------------------------------ T3 误配
    print("=" * 88)
    print("T3 一个锚点偏移 0.3 Da（仍在 ±0.5 搜索窗内，残差 > %.0f ppm）"
          "→ 期望：拒绝（残差门槛）" % (E.MAX_CAL_REL * 1e6))
    spec3 = {
        "NAT": mk_sample([(apex[("nat", 2)], 1000),
                          (apex[("nat", 3)], 1000),
                          (apex[("nat", 4)] + 0.3, 1000),   # z=4 锚点故意偏移
                          (apex[("lab", 2)], 10), (apex[("lab", 3)], 10),
                          (apex[("lab", 4)], 10)]),
        "LAB": mk_sample([(apex[("lab", z)], 1000) for z in Z]
                         + [(apex[("nat", z)], 10) for z in Z]),
    }
    a3, c3, r3, n3 = E.auto_calibrate(spec3, Z, apex, A_T * 1.00002, C_T)
    report("T3", a3, c3, r3, n3)
    check("拒绝（n_anchor=0）", len(r3) == 0, "got %d" % len(r3))
    check("真回退（a/c 不变）", a3 == A_T * 1.00002 and c3 == C_T)
    check("给出原因（残差）", "残差" in n3, n3[:70])

    # ------------------------------------------------------------ T4 混合
    print("=" * 88)
    print("T4 混合样品（两形强度 1000:400，不足 5×）→ 期望：该样品锚点被弃用")
    spec4 = {
        "NAT": mk_sample([(apex[("nat", z)], 1000) for z in Z]
                         + [(apex[("lab", z)], 10) for z in Z]),
        "MIX": mk_sample([(apex[("nat", z)], 1000) for z in Z]
                         + [(apex[("lab", z)], 400) for z in Z]),   # 400 > 1000/5
    }
    a4, c4, r4, n4 = E.auto_calibrate(spec4, Z, apex, A_T, C_T)
    report("T4", a4, c4, r4, n4)
    check("仅 NAT 样品贡献锚点（<= 3）", len(r4) <= 3, "got %d" % len(r4))
    check("采纳且 a 正确", len(r4) >= 3 and abs(a4 - A_T) / A_T * 1e6 < 1.0)

    print("=" * 88)
    if _fail:
        print("结果：FAIL（%d 项）→ %s" % (len(_fail), " / ".join(_fail)))
        return 1
    print("结果：全部 PASS（门槛行为符合预期）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
