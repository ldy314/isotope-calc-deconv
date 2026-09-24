#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
formula.py — 分子式字符串解析 + 化合物别名库

目的：免去每次手敲 `--elements C natural 128 C 13 6 H natural 198 ...` 三元组。

支持的分子式语法
----------------
    C134H198N28O35S2                    纯天然
    C128[13C]6H198N26[15N]2O35S2        方括号指定同位素
    C128(13C)6H198N26(15N)2O35S2        圆括号等价
    D10                                 D = ²H（氘），T = ³H（氚）
    C6H12O6 · 括号分组 (CH2)5           支持嵌套分组与倍数

规则
----
* 元素符号：1 个大写字母 + 可选 1 个小写字母（C, Cl, Na, Fe...）
* 同位素：必须写在方/圆括号内且质量数在前，如 [13C]、(15N)
* 省略个数视为 1（C = C1）
* 同一 (元素, 同位素) 多次出现会自动合并计数
* 解析结果保持"首次出现顺序"，与 Excel 表逐列输入顺序一致

对外接口
--------
    parse_formula(s)        -> List[ElementColumn]
    columns_to_formula(cols)-> str            （反向，规范化输出）
    resolve(name_or_formula)-> (cols, z, note) 别名优先，回退当作分子式
    load_compounds() / save_compound()        化合物库读写
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

# 复用本目录 imp.py 的 ElementColumn，保证与 theo/imp/deconv 完全兼容
# （注意：不能直接 `import imp` —— 那是已废弃的标准库同名模块）

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_local(name: str):
    """加载同目录模块，并注册进 sys.modules（dataclass 需要）。"""
    if name in sys.modules and getattr(sys.modules[name], "__file__", "").startswith(_HERE):
        return sys.modules[name]
    path = os.path.join(_HERE, f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # 关键：dataclass 解析注解时要能找到模块
    spec.loader.exec_module(mod)
    return mod


imp = _load_local("imp")
ElementColumn = imp.ElementColumn

COMPOUNDS_PATH = os.path.join(_HERE, "compounds.json")

# 特殊同位素符号 → (元素, 质量数)
_SPECIAL = {"D": ("H", "2"), "T": ("H", "3")}

# 一个 token：括号同位素 | 分组 | 普通元素
_TOKEN = re.compile(
    r"""
    \s*
    (?:
        [\[(](?P<iso_mass>\d+)(?P<iso_el>[A-Z][a-z]?)[\])](?P<iso_n>\d*)   # [13C]6
      | (?P<open>\()                                                        # 分组开始
      | (?P<close>\))(?P<grp_n>\d*)                                         # 分组结束 + 倍数
      | (?P<el>[A-Z][a-z]?)(?P<n>\d*)                                       # C128 / Cl2 / D10
    )
    """,
    re.VERBOSE,
)


class FormulaError(ValueError):
    """分子式语法错误。"""


def parse_formula(s: str) -> List[ElementColumn]:
    """把分子式字符串解析为 ElementColumn 列表。

    >>> [ (c.element, c.isotope, c.count) for c in parse_formula("C2[13C]1H6") ]
    [('C', 'natural', 2), ('C', '13', 1), ('H', 'natural', 6)]
    """
    if not s or not s.strip():
        raise FormulaError("分子式为空")

    text = s.replace("·", "").replace(" ", "")
    # 去掉末尾电荷标注（如 2+ / +），电荷由 --z 单独指定
    text = re.sub(r"\d*[+-]$", "", text)

    # (元素, 同位素) -> 计数；用 dict 保序
    acc: Dict[Tuple[str, str], int] = {}
    stack: List[Dict[Tuple[str, str], int]] = [acc]

    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise FormulaError(f"无法解析的片段：{text[pos:pos + 12]!r}（位置 {pos}）")
        pos = m.end()
        cur = stack[-1]

        if m.group("open"):
            stack.append({})
            continue

        if m.group("close"):
            if len(stack) == 1:
                raise FormulaError("括号不匹配：多余的 ')'")
            grp = stack.pop()
            mult = int(m.group("grp_n") or 1)
            tgt = stack[-1]
            for k, v in grp.items():
                tgt[k] = tgt.get(k, 0) + v * mult
            continue

        if m.group("iso_el"):
            el = m.group("iso_el").capitalize()
            iso = m.group("iso_mass")
            n = int(m.group("iso_n") or 1)
        else:
            raw = m.group("el")
            n = int(m.group("n") or 1)
            if raw in _SPECIAL:                     # D / T
                el, iso = _SPECIAL[raw]
            else:
                el, iso = raw.capitalize(), "natural"

        if n == 0:
            continue
        key = (el, iso)
        cur[key] = cur.get(key, 0) + n

    if len(stack) != 1:
        raise FormulaError("括号不匹配：缺少 ')'")

    if not acc:
        raise FormulaError(f"未解析出任何元素：{s!r}")

    return [ElementColumn(el, iso, cnt) for (el, iso), cnt in acc.items()]


def columns_to_formula(cols: List[ElementColumn]) -> str:
    """反向：ElementColumn 列表 → 规范分子式字符串（ASCII，可回灌）。"""
    parts = []
    for c in cols:
        if c.count == 0:
            continue
        head = c.element if imp.is_natural_iso(c.isotope) else f"[{c.isotope}{c.element}]"
        parts.append(head + (str(c.count) if c.count != 1 else ""))
    return "".join(parts)


def pretty_formula(cols: List[ElementColumn], z: int = 0) -> str:
    """Unicode 上/下标的漂亮分子式（复用 imp.formula_name）。"""
    triples = [(c.element, c.isotope, c.count) for c in cols]
    return imp.formula_name(triples, z)


# ---------------------------------------------------------------------------
# 化合物别名库
# ---------------------------------------------------------------------------

def load_compounds() -> Dict[str, dict]:
    if not os.path.exists(COMPOUNDS_PATH):
        return {}
    with open(COMPOUNDS_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_compound(name: str, formula: str, z: Optional[int] = None,
                  note: str = "") -> Dict[str, dict]:
    """新增/更新一个化合物别名（会先校验分式可解析）。"""
    parse_formula(formula)                       # 校验
    db = load_compounds()
    entry = {"formula": formula}
    if z is not None:
        entry["z"] = z
    if note:
        entry["note"] = note
    db[name] = entry
    with open(COMPOUNDS_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    return db


def delete_compound(name: str) -> bool:
    db = load_compounds()
    if name not in db:
        return False
    db.pop(name)
    with open(COMPOUNDS_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    return True


def resolve(token: str) -> Tuple[List[ElementColumn], Optional[int], str]:
    """把「别名 或 分子式」统一解析。

    返回 (columns, default_z 或 None, note)
    别名匹配不区分大小写；未命中则当作分子式解析。
    """
    db = load_compounds()
    if token in db:
        e = db[token]
        return parse_formula(e["formula"]), e.get("z"), e.get("note", "")
    low = {k.lower(): k for k in db}
    if token.lower() in low:
        e = db[low[token.lower()]]
        return parse_formula(e["formula"]), e.get("z"), e.get("note", "")
    return parse_formula(token), None, ""


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="分子式解析自测")
    ap.add_argument("formula", nargs="?", default="C128[13C]6H198N26[15N]2O35S2")
    a = ap.parse_args()
    cols, z, note = resolve(a.formula)
    print("输入      :", a.formula)
    print("解析结果  :", [(c.element, c.isotope, c.count) for c in cols])
    print("规范化    :", columns_to_formula(cols))
    print("漂亮显示  :", pretty_formula(cols, z or 0))
    print("默认电荷  :", z)
    if note:
        print("备注      :", note)
