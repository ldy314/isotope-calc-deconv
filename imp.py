# -*- coding: utf-8 -*-
"""
imp - 同位素取代杂质枚举（impurities）

给定一个同位素修饰的分子式（元素表：元素/同位素种类/原子个数），
枚举该修饰分子与天然化合物之间的所有同位素取代杂质（含天然形式，不含输入本身）。

例：C2D3（C natural 2 + H 2 3）→ 输出
    C2H3   (3 个 D 全部替换回 H，即天然形式)
    C2HD2  (1 个 D 替换为 H)
    C2H2D  (2 个 D 替换为 H)

规则：
  - "修饰列" = 同位素种类不是 natural 的列（如 H 质量数 2 = 氘 D）
  - 每个修饰列 (E, iso, n)：把其中 k 个修饰原子替换回该元素天然同位素，k = 1..n
    （k=n 为全天然形式；k=0 为输入本身，不输出）
  - "natural 列"（如 C natural 2）原样保留在每个杂质中，不参与枚举
    （即天然同位素如 ¹³C 的杂质忽略，不列举）
  - 多个修饰列时做笛卡尔积，排除所有 k=0（= 输入本身）的组合

输出格式（与输入一致的三行形式 + 分子式平文名称）：
  - 名称行：如 "C2H3"
  - 元素行 / 同位素行 / 个数行

用法：
    python imp.py --elements C natural 2 H 2 3 [--out FILE]
"""

from __future__ import annotations

import argparse
import itertools
import sys
from collections import OrderedDict
from dataclasses import dataclass
from typing import List, Tuple


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class ElementColumn:
    """与 theo.py 一致的一列输入：元素 + 同位素选择 + 原子个数"""
    element: str
    isotope: str
    count: int

    def __post_init__(self):
        self.element = self.element.strip().capitalize()
        self.isotope = self.isotope.strip()
        if self.count is None or self.count < 0:
            raise ValueError(f"原子个数必须 >= 0: {self.count}")

    @property
    def is_natural(self) -> bool:
        return self.isotope.lower() in ("natural", "自然", "自然分布", "")


def is_natural_iso(iso: str) -> bool:
    return iso.lower() in ("natural", "自然", "自然分布", "")


# ---------------------------------------------------------------------------
# 核素符号与分子式名称（Unicode 上/下标）
# ---------------------------------------------------------------------------
# 常见同位素专用符号：H-2 → D（氘）、H-3 → T（氚）
SPECIAL_SYMBOLS = {
    ('H', 2): 'D',
    ('H', 3): 'T',
}

SUB_D = {'0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄',
         '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉'}
SUP_D = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
         '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹'}


def _to_sub(n: int) -> str:
    return ''.join(SUB_D[d] for d in str(n))


def _to_sup(n: int) -> str:
    return ''.join(SUP_D[d] for d in str(n))


def iso_symbol(element: str, mass_number: int) -> str:
    """修饰同位素符号：H2 → D、H3 → T，其他 → ¹³C 样式（质量数上标）"""
    if (element, mass_number) in SPECIAL_SYMBOLS:
        return SPECIAL_SYMBOLS[(element, mass_number)]
    return _to_sup(mass_number) + element


def charge_suffix(z: int) -> str:
    """电荷上标：z=1 → ⁺，z=2 → ²⁺，z=-1 → ⁻，z=-2 → ²⁻，z=0 → ''"""
    if z == 0:
        return ''
    mag = '' if abs(z) == 1 else _to_sup(abs(z))
    return mag + ('⁺' if z > 0 else '⁻')


# ---------------------------------------------------------------------------
# 核心算法
# ---------------------------------------------------------------------------

def _split_mod_column(col: ElementColumn, k: int) -> List[Tuple[str, str, int]]:
    """把修饰列 (E, iso, n) 拆成两个子列：天然 k 个 + 修饰 (n-k) 个。
    k=0 → 只有修饰列（= 输入原样）；k=n → 只有天然列（= 全天然形式）。"""
    out = []
    if k > 0:
        out.append((col.element, 'natural', k))
    remain = col.count - k
    if remain > 0:
        out.append((col.element, col.isotope, remain))
    return out


def _canonical_key(cols: List[Tuple[str, str, int]]) -> tuple:
    """规范化签名：按 (元素, 是否natural, 同位素) 排序分组合并，用于识别
    '分子式一致但排列顺序不一致'的重复，并做去重。"""
    from collections import OrderedDict
    merged: "OrderedDict[tuple, int]" = OrderedDict()
    for elem, iso, cnt in cols:
        if cnt == 0:
            continue
        key = (elem, iso)
        merged[key] = merged.get(key, 0) + cnt
    items = sorted(merged.items(),
                   key=lambda kv: (kv[0][0], 0 if is_natural_iso(kv[0][1]) else 1, kv[0][1]))
    return tuple((e, i, c) for (e, i), c in items)


def enumerate_impurities(columns: List[ElementColumn], z: int = 1) -> List[dict]:
    """枚举所有同位素取代杂质。

    返回列表，每项：
      {
        'name': 分子式名称（含电荷，如 C₂H₃⁺ / C₂HD₂⁺）,
        'elements': [元素...],
        'isos':     [同位素...]（'natural' 或质量数字符串）,
        'counts':   [原子个数...],
        'mod_total': 保留的修饰原子总数,
        'replaced_total': 被替换回天然的修饰原子总数,
      }

    排序规则：
      - 全天然（mod_total=0）排第一
      - 其余按 replaced_total（替换修饰原子总数）升序：替换 1 个的在前 → 接近输入在后
      - 同替换数按分子式名称升序
    去重：分子式一致（含排列顺序不同）的杂质合并为一个（规范化签名去重）。

    关键规则：
      - 同元素多列（如 C natural 128 + ¹³C 3）的 natural 部分在输出中**合并为一列**
        （全天然杂质 = C natural 131，而非 C natural 128 + C natural 3）
      - **每个元素 natural 计数 = 输入 natural 值 + 替换回的数量，永不小于输入值**
        （如 C natural ≥ 2、N natural ≥ 12、O natural ≥ 5；适用所有元素）
      - 修饰列 (E, iso, n)：替换回天然 k 个，k = 1..n（k=n=全天然；k=0=输入本身，排除）
      - 输出列按元素分组：每元素先 natural 列（聚合后），再该元素各修饰列剩余部分
    """
    if not columns:
        raise ValueError("元素表为空")

    # natural 列按元素聚合计数（同元素多列合并）
    nat_counts: OrderedDict[str, int] = OrderedDict()
    mod_cols: List[ElementColumn] = []
    for c in columns:
        if c.count <= 0:
            continue
        if c.is_natural:
            nat_counts[c.element] = nat_counts.get(c.element, 0) + c.count
        else:
            mod_cols.append(c)

    if not mod_cols:
        # 无修饰同位素：没有可枚举的杂质
        return []

    total_mod_atoms = sum(c.count for c in mod_cols)

    # 元素输出顺序：输入中所有列（natural+修饰）首次出现的顺序
    elem_order: List[str] = []
    for c in columns:
        if c.count > 0 and c.element not in elem_order:
            elem_order.append(c.element)

    # 每个修饰列 k 的取值范围：0..n（k=0=该列保持输入状态；全 0 = 输入本身，排除）
    k_ranges = [range(0, c.count + 1) for c in mod_cols]
    results = []
    seen_keys = set()

    for k_comb in itertools.product(*k_ranges):
        if all(k == 0 for k in k_comb):
            continue  # 排除输入本身（所有修饰列都不替换）
        # 1) 计算每元素聚合后的 natural 计数 = 输入 natural 总数 + 各修饰列替换回的 k
        nat_total = dict(nat_counts)
        mod_remain: List[Tuple[str, str, int]] = []
        for mc, k in zip(mod_cols, k_comb):
            nat_total[mc.element] = nat_total.get(mc.element, 0) + k
            remain = mc.count - k
            if remain > 0:
                mod_remain.append((mc.element, mc.isotope, remain))

        # 2) 按元素分组组装输出列：每元素 [natural 聚合列] + [该元素修饰剩余列]
        cols: List[Tuple[str, str, int]] = []
        for elem in elem_order:
            n = nat_total.get(elem, 0)
            if n > 0:
                cols.append((elem, 'natural', n))
            for el2, iso2, cnt2 in mod_remain:
                if el2 == elem:
                    cols.append((el2, iso2, cnt2))

        # 防御性去重：规范化签名（分子式一致但排列顺序不同 → 合并）
        canon = _canonical_key(cols)
        if canon in seen_keys:
            continue
        seen_keys.add(canon)

        mod_total = sum(cnt for el, iso, cnt in cols if not is_natural_iso(iso))
        replaced_total = total_mod_atoms - mod_total
        results.append({
            'name': formula_name(cols, z),
            'elements': [c[0] for c in cols],
            'isos': [c[1] for c in cols],
            'counts': [c[2] for c in cols],
            'mod_total': mod_total,
            'replaced_total': replaced_total,
        })

    # 排序：全天然第一；其余按替换修饰原子总数升序（替换1个在前→接近输入在后），同替换数按名称
    results.sort(key=lambda r: (0 if r['mod_total'] == 0 else 1, r['replaced_total'], r['name']))
    return results


def formula_name(cols: List[Tuple[str, str, int]], z: int = 1) -> str:
    """生成分子式名称（Unicode 下标/上标，计数为 1 时省略数字）。
    天然列 → 元素符号（如 C / H）；修饰列 → 核素符号（D / ¹³C）。
    末尾附加电荷上标（z=0 不加）。"""
    parts = []
    for elem, iso, cnt in cols:
        if cnt == 0:
            continue
        if is_natural_iso(iso):
            symbol = elem
        else:
            symbol = iso_symbol(elem, int(iso))
        parts.append(symbol if cnt == 1 else f"{symbol}{_to_sub(cnt)}")
    return ''.join(parts) + charge_suffix(z)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="imp",
        description="同位素取代杂质枚举：修饰分子式 → 与天然化合物之间的所有取代杂质",
    )
    parser.add_argument(
        "--elements", nargs="+", required=True, metavar="E",
        help="元素表：三元素一组 (元素, 同位素, 个数)。同位素用 'natural' 或质量数（如 '2'=氘）。"
             "例：C natural 2 H 2 3",
    )
    parser.add_argument("--z", type=int, default=1, help="电荷数（默认 1，0=中性不加电荷）")
    parser.add_argument("--out", metavar="FILE", default=None,
                        help="结果文件：成功写文本协议（每杂质4行），失败写同目录 imp_err.txt")
    return parser.parse_args(argv)


def _impurity_block(r: dict) -> str:
    """单个杂质的 4 行文本块（供 --out 文件与 VBA 解析）：
       # <名称>
       <元素以|分隔>
       <同位素以|分隔>
       <个数以|分隔>"""
    return '\n'.join([
        f"# {r['name']}",
        '|'.join(r['elements']),
        '|'.join(r['isos']),
        '|'.join(str(c) for c in r['counts']),
    ])


def main(argv=None):
    args = parse_args(argv)
    if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    if len(args.elements) % 3 != 0:
        _emit_error(args, "错误：--elements 必须按 (元素, 同位素, 个数) 三元组提供")
        return 1

    columns = []
    for i in range(0, len(args.elements), 3):
        el, iso, cnt = args.elements[i], args.elements[i + 1], args.elements[i + 2]
        try:
            columns.append(ElementColumn(el, iso, int(cnt)))
        except ValueError as e:
            _emit_error(args, f"错误：{e}")
            return 1

    try:
        impurities = enumerate_impurities(columns, z=args.z)
    except ValueError as e:
        _emit_error(args, f"错误：{e}")
        return 1

    if args.out:
        with open(args.out, 'w', encoding='utf-8', newline='\n') as f:
            f.write(f"# imp v1 | total {len(impurities)} | z {args.z}\n")
            for r in impurities:
                f.write(_impurity_block(r) + '\n')
            if not impurities:
                f.write("# none\n")
        return 0

    # stdout 人类可读
    print(f"共 {len(impurities)} 个同位素取代杂质：\n")
    for i, r in enumerate(impurities, 1):
        print(f"[{i}] {r['name']}")
        print("    元素:  " + ' | '.join(r['elements']))
        print("    同位素: " + ' | '.join(r['isos']))
        print("    个数:  " + ' | '.join(str(c) for c in r['counts']))
        print()
    return 0


def _emit_error(args, message: str) -> None:
    if args.out:
        import os
        err_path = os.path.join(os.path.dirname(os.path.abspath(args.out)), 'imp_err.txt')
        try:
            with open(err_path, 'w', encoding='utf-8') as f:
                f.write(message)
            return
        except OSError:
            pass
    print(message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
