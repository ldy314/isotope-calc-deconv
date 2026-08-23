# -*- coding: utf-8 -*-
"""从 CDXML 解析出的分子中识别多肽主链残基序列、修饰与同位素标记位置。"""
import sys
from collections import Counter
from rdkit import Chem
from rdkit.Chem import Draw
from rdkit.Chem.Draw import rdMolDraw2D
from cdxml_to_rdkit import load, build

# 侧链重原子组成 -> 常见氨基酸（侧链 = 去掉 N/CA/C/O 主链后挂在 CA 上的部分）
STD = {
    "": "Gly", "C1": "Ala", "C3": "Val", "C4": "Leu 或 Ile", "C1S1": "Cys",
    "C3S1": "Met", "C1O1": "Ser", "C2O1": "Thr", "C2O2": "Asp", "C3O2": "Glu",
    "C2N1O1": "Asn", "C3N1O1": "Gln", "C4N1": "Lys", "C4N3": "Arg",
    "C4N2": "His", "C7": "Phe", "C7O1": "Tyr", "C9N1": "Trp", "C3S1?": "Pen",
}


def sc_key(mol, ids):
    c = Counter(mol.GetAtomWithIdx(i).GetSymbol() for i in ids)
    return "".join(f"{e}{c[e]}" for e in ["C", "N", "O", "S"] if c[e])


def find_residues(mol):
    """返回 [(ca, n, c, o, sidechain_ids)]，按 N->C 排好序"""
    amideC, amideN = set(), set()
    for b in mol.GetBonds():
        a1, a2 = b.GetBeginAtom(), b.GetEndAtom()
        for c, n in ((a1, a2), (a2, a1)):
            if c.GetSymbol() == "C" and n.GetSymbol() == "N" and b.GetBondType() == Chem.BondType.SINGLE:
                if any(nb.GetSymbol() == "O" and mol.GetBondBetweenAtoms(c.GetIdx(), nb.GetIdx()).GetBondType() == Chem.BondType.DOUBLE
                       for nb in c.GetNeighbors()):
                    amideC.add(c.GetIdx()); amideN.add(n.GetIdx())

    def carbonylO(ci):
        a = mol.GetAtomWithIdx(ci)
        for nb in a.GetNeighbors():
            if nb.GetSymbol() == "O" and mol.GetBondBetweenAtoms(ci, nb.GetIdx()).GetBondType() == Chem.BondType.DOUBLE:
                return nb.GetIdx()
        return None

    res = []
    for a in mol.GetAtoms():
        if a.GetSymbol() != "C" or a.GetIdx() in amideC:
            continue
        ns = [nb.GetIdx() for nb in a.GetNeighbors() if nb.GetIdx() in amideN]
        cs = [nb.GetIdx() for nb in a.GetNeighbors() if nb.GetIdx() in amideC]
        if ns and cs:
            res.append((a.GetIdx(), ns[0], cs[0], carbonylO(cs[0])))

    ca_of_n = {r[1]: r[0] for r in res}
    c_of_ca = {r[0]: r[2] for r in res}
    # 后继：本残基 C(=O) 连到的 N，若该 N 是别的残基的主链 N
    nxt, prv = {}, {}
    for ca, n, c, o in res:
        for nb in mol.GetAtomWithIdx(c).GetNeighbors():
            if nb.GetSymbol() == "N" and nb.GetIdx() in ca_of_n and ca_of_n[nb.GetIdx()] != ca:
                nxt[ca] = ca_of_n[nb.GetIdx()]; prv[ca_of_n[nb.GetIdx()]] = ca
    starts = [r[0] for r in res if r[0] not in prv]
    order, seen = [], set()
    for s in starts:
        cur = s
        while cur is not None and cur not in seen:
            order.append(cur); seen.add(cur); cur = nxt.get(cur)
    for r in res:                      # 兜底：环状/未串上的
        if r[0] not in seen:
            order.append(r[0]); seen.add(r[0])

    info = {r[0]: r for r in res}
    out = []
    ca_set = {r[0] for r in res}
    for ca in order:
        _, n, c, o = info[ca]
        core = {ca, n, c, o}
        sc, stack = set(), [nb.GetIdx() for nb in mol.GetAtomWithIdx(ca).GetNeighbors() if nb.GetIdx() not in core]
        while stack:
            x = stack.pop()
            if x in sc or x in core or x in ca_set:
                continue
            sc.add(x)
            for nb in mol.GetAtomWithIdx(x).GetNeighbors():
                j = nb.GetIdx()
                if j not in core and j not in sc and j not in ca_set:
                    stack.append(j)
        out.append(dict(ca=ca, n=n, c=c, o=o, sc=sc))
    return out


def describe(mol, r, idx):
    ca = r["ca"]
    key = sc_key(mol, r["sc"])
    name = STD.get(key, "")
    a = mol.GetAtomWithIdx(ca)
    quaternary = a.GetTotalNumHs() == 0
    # 主链 N 上的取代（N-甲基/N-乙酰）
    nat = mol.GetAtomWithIdx(r["n"])
    nsub = [nb.GetIdx() for nb in nat.GetNeighbors() if nb.GetIdx() not in (ca,)]
    iso_sc = [mol.GetAtomWithIdx(i).GetIsotope() for i in r["sc"] | {ca, r["n"], r["c"]} if mol.GetAtomWithIdx(i).GetIsotope()]
    return dict(i=idx, ca=ca, key=key or "(无侧链)", guess=name,
                nH=a.GetTotalNumHs(), quaternary=quaternary,
                sc_size=len(r["sc"]), iso=len(iso_sc))


if __name__ == "__main__":
    path = sys.argv[1]
    root = load(path); page = root.find("page")
    items = [("A_labeled(带标记)", fr) for fr in page.findall("fragment")]
    for g in page.findall("group"):
        items += [("B_natural(天然)", fr) for fr in g.findall("fragment")]

    for tag, fr in items:
        mol = build(fr)
        res = find_residues(mol)
        print(f"\n{'='*80}\n{tag}   主链残基数 = {len(res)}\n{'='*80}")
        print(f"{'#':>3} {'侧链组成':<10} {'推定':<12} {'CA-H':>4} {'侧链重原子':>10} {'标记原子':>8}")
        print("-" * 80)
        for i, r in enumerate(res, 1):
            d = describe(mol, r, i)
            flag = f"  <== {d['iso']}个" if d["iso"] else ""
            print(f"{i:>3} {d['key']:<10} {d['guess'] or '非天然':<12} {d['nH']:>4} {d['sc_size']:>10} {d['iso']:>8}{flag}")
        # 二硫键
        ss = [(b.GetBeginAtomIdx(), b.GetEndAtomIdx()) for b in mol.GetBonds()
              if b.GetBeginAtom().GetSymbol() == "S" and b.GetEndAtom().GetSymbol() == "S"]
        print(f"  二硫键 S-S: {len(ss)} 个 -> {ss}")
        if tag.startswith("A"):
            iso_at = [(a.GetIdx(), a.GetIsotope(), a.GetSymbol()) for a in mol.GetAtoms() if a.GetIsotope()]
            print(f"  同位素标记原子: {iso_at}")
            # 渲染：高亮标记原子
            hl = [i for i, _, _ in iso_at]
            d = rdMolDraw2D.MolDraw2DCairo(2200, 1200)
            mm = Chem.Mol(mol)
            rdMolDraw2D.PrepareAndDrawMolecule(d, mm, highlightAtoms=hl)
            d.FinishDrawing()
            open("A_labeled_highlight.png", "wb").write(d.GetDrawingText())
            print("  已渲染高亮图: A_labeled_highlight.png")
