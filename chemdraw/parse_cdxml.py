# -*- coding: utf-8 -*-
"""
解析 ChemDraw CDXML，识别其中的分子、同位素标记，并对多肽做残基/序列识别。
用法: python parse_cdxml.py "SPH20291 and SPH20291-Isotope1.cdxml"
"""
import re, sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque

Z2SYM = {1: "H", 6: "C", 7: "N", 8: "O", 15: "P", 16: "S"}
VAL = {"C": 4, "N": 3, "O": 2, "S": 2, "P": 3, "H": 1}


def load(path):
    """CDXML 常含非法字节（ChemDraw 注释乱码），需容错解析。"""
    raw = open(path, "rb").read()
    txt = raw.decode("utf-8", errors="replace")
    txt = re.sub(r"<\?xml[^>]*\?>", '<?xml version="1.0"?>', txt, count=1)
    txt = re.sub(r"<!DOCTYPE[^>]*>", "", txt, count=1)
    return ET.fromstring(txt)


class Mol:
    def __init__(self, frag):
        self.id = frag.get("id")
        self.atoms = {}
        for n in frag.findall("n"):
            self.atoms[n.get("id")] = dict(
                sym=Z2SYM.get(int(n.get("Element", "6")), "?"),
                iso=n.get("Isotope"),
                chg=int(n.get("Charge", "0") or 0),
                nh=n.get("NumHydrogens"),
            )
        self.adj = defaultdict(list)
        self.bonds = []
        for b in frag.findall("b"):
            B, E = b.get("B"), b.get("E")
            o = b.get("Order", "1")
            o = {"1": 1, "2": 2, "3": 3}.get(o, 1)
            self.bonds.append((B, E, o))
            self.adj[B].append((E, o))
            self.adj[E].append((B, o))

    def sym(self, a):
        return self.atoms[a]["sym"]

    def nbrs(self, a):
        return [x for x, _ in self.adj[a]]

    def order_sum(self, a):
        return sum(o for _, o in self.adj[a])

    def implicit_h(self, a):
        at = self.atoms[a]
        if at["nh"] is not None:
            try:
                return int(at["nh"])
            except ValueError:
                pass
        v = VAL.get(at["sym"])
        if v is None:
            return 0
        adj_chg = at["chg"] * (1 if at["sym"] == "N" else -1)
        return max(0, int(round(v + adj_chg - self.order_sum(a))))

    def formula(self):
        cnt = Counter()
        H = 0
        for a, at in self.atoms.items():
            key = (at["iso"] or "") + at["sym"]
            cnt[key] += 1
            H += self.implicit_h(a)
        cnt["H"] = H
        return cnt

    def rings(self):
        return len(self.bonds) - len(self.atoms) + self.n_components()

    def n_components(self):
        seen, n = set(), 0
        for a in self.atoms:
            if a in seen:
                continue
            n += 1
            q = deque([a]); seen.add(a)
            while q:
                x = q.popleft()
                for y in self.nbrs(x):
                    if y not in seen:
                        seen.add(y); q.append(y)
        return n

    # ---------- 肽识别 ----------
    def carbonyl_o(self, c):
        """返回 C 上双键 O 的 id（无则 None）"""
        for x, o in self.adj[c]:
            if o == 2 and self.sym(x) == "O":
                return x
        return None

    def perceive_peptide(self):
        A = self.atoms
        # 酰胺碳：C(=O)-N
        amideC = set()
        for a in A:
            if self.sym(a) != "C":
                continue
            if self.carbonyl_o(a) and any(self.sym(x) == "N" for x, o in self.adj[a] if o == 1):
                amideC.add(a)
        # 候选 Cα：同时连着「酰胺氮」和「酰胺碳」的 sp3 碳
        amideN = {x for c in amideC for x, o in self.adj[c] if o == 1 and self.sym(x) == "N"}
        CA = []
        for a in A:
            if self.sym(a) != "C" or self.order_sum(a) > 4:
                continue
            if self.carbonyl_o(a):
                continue
            ns = [x for x in self.nbrs(a) if x in amideN]
            cs = [x for x in self.nbrs(a) if x in amideC]
            if ns and cs:
                CA.append((a, ns, cs))
        return amideC, amideN, CA


SIDECHAIN = {
    # 侧链(不含主链 N/CA/C/O)重原子组成 -> 氨基酸
    "": "Gly",
    "C1": "Ala",
    "C3": "Val",
    "C4": "Leu/Ile",
    "C1S1": "Cys",
    "C3S1": "Met",
    "C1O1": "Ser",
    "C2O1": "Thr",
    "C2O2": "Asp",
    "C3O2": "Glu",
    "C2N1O1": "Asn",
    "C3N1O1": "Gln",
    "C4N1": "Lys",
    "C4N3": "Arg",
    "C4N2": "His",
    "C7": "Phe",
    "C7O1": "Tyr",
    "C9N1": "Trp",
}


def residue_name(mol, sidechain_atoms):
    cnt = Counter(mol.sym(a) for a in sidechain_atoms)
    key = "".join(f"{e}{cnt[e]}" for e in ["C", "N", "O", "S"] if cnt[e])
    return SIDECHAIN.get(key, f"?[{key or 'none'}]"), key


def analyze(mol, title):
    print(f"\n{'='*78}\n{title}\n{'='*78}")
    f = mol.formula()
    order = ["C", "13C", "H", "N", "15N", "O", "S"]
    parts = [f"{k}{f[k]}" for k in order if f.get(k)]
    print(f"  分子式(独立核算): {' '.join(parts)}")
    print(f"  重原子 {len(mol.atoms)} | 键 {len(mol.bonds)} | 连通分量 {mol.n_components()} | 环数 {mol.rings()}")
    iso = [(a, mol.atoms[a]) for a in mol.atoms if mol.atoms[a]["iso"]]
    if iso:
        print(f"  同位素标记: {dict(Counter(x[1]['iso'] + x[1]['sym'] for x in iso))}")

    amideC, amideN, CA = mol.perceive_peptide()
    print(f"  酰胺键(C(=O)-N) 数: {len(amideC)} | 候选 Cα: {len(CA)}")

    # 建残基
    residues = []
    ca_set = {a for a, _, _ in CA}
    for a, ns, cs in CA:
        n = ns[0]
        c = cs[0]
        o = mol.carbonyl_o(c)
        core = {n, a, c, o}
        # 侧链 = 从 Cα 出发、不经过主链 N/C 的连通块
        sc = set()
        q = deque(x for x in mol.nbrs(a) if x not in core)
        while q:
            x = q.popleft()
            if x in sc or x in core:
                continue
            sc.add(x)
            for y in mol.nbrs(x):
                if y not in core and y not in sc and y not in ca_set:
                    q.append(y)
        residues.append(dict(ca=a, n=n, c=c, o=o, sc=sc))
    return residues, amideC, amideN


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "SPH20291 and SPH20291-Isotope1.cdxml"
    root = load(path)
    page = root.find("page")
    frags = []
    for fr in page.findall("fragment"):
        frags.append(("顶层 fragment", fr))
    for g in page.findall("group"):
        for fr in g.findall("fragment"):
            frags.append(("group 内 fragment", fr))
    for tag, fr in frags:
        mol = Mol(fr)
        analyze(mol, f"{tag}  id={fr.get('id')}")
