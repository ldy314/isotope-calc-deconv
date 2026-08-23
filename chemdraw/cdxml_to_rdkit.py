# -*- coding: utf-8 -*-
"""CDXML -> RDKit：生成 SMILES、分子式、精确质量，并渲染 PNG。"""
import re, sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from rdkit import Chem
from rdkit.Chem import Draw, AllChem, Descriptors
from rdkit.Chem.Draw import rdMolDraw2D
from rdkit.Chem.rdMolDescriptors import CalcMolFormula
from rdkit.Geometry import Point3D

Z2SYM = {1: "H", 6: "C", 7: "N", 8: "O", 15: "P", 16: "S"}


def load(path):
    raw = open(path, "rb").read()
    txt = raw.decode("utf-8", errors="replace")
    txt = re.sub(r"<\?xml[^>]*\?>", '<?xml version="1.0"?>', txt, count=1)
    txt = re.sub(r"<!DOCTYPE[^>]*>", "", txt, count=1)
    return ET.fromstring(txt)


WEDGE = {"WedgeBegin": Chem.BondDir.BEGINWEDGE, "WedgedHashBegin": Chem.BondDir.BEGINDASH,
         "Wedge": Chem.BondDir.BEGINWEDGE, "Hash": Chem.BondDir.BEGINDASH,
         "WedgeEnd": Chem.BondDir.BEGINWEDGE, "WedgedHashEnd": Chem.BondDir.BEGINDASH}


def build(frag, keep_isotope=True):
    m = Chem.RWMol()
    idx = {}
    coords = []
    for n in frag.findall("n"):
        sym = Z2SYM.get(int(n.get("Element", "6")), "C")
        a = Chem.Atom(sym)
        chg = int(n.get("Charge", "0") or 0)
        a.SetFormalCharge(chg)
        iso = n.get("Isotope")
        if iso and keep_isotope:
            a.SetIsotope(int(iso))
        nh = n.get("NumHydrogens")
        if nh is not None:
            a.SetNoImplicit(True)
            a.SetNumExplicitHs(int(nh))
        idx[n.get("id")] = m.AddAtom(a)
        p = (n.get("p") or "0 0").split()
        coords.append((float(p[0]), -float(p[1])))
    BT = {1: Chem.BondType.SINGLE, 2: Chem.BondType.DOUBLE, 3: Chem.BondType.TRIPLE}
    wedges = []
    for b in frag.findall("b"):
        B, E = idx.get(b.get("B")), idx.get(b.get("E"))
        if B is None or E is None:
            continue
        o = {"1": 1, "2": 2, "3": 3}.get(b.get("Order", "1"), 1)
        if m.GetBondBetweenAtoms(B, E) is None:
            m.AddBond(B, E, BT[o])
            d = b.get("Display")
            if d in WEDGE:
                wedges.append((B, E, WEDGE[d]))
    mol = m.GetMol()
    conf = Chem.Conformer(mol.GetNumAtoms())
    for i, (x, y) in enumerate(coords):
        conf.SetAtomPosition(i, Point3D(x / 30.0, y / 30.0, 0.0))
    mol.AddConformer(conf)
    Chem.SanitizeMol(mol)
    for B, E, d in wedges:
        bd = mol.GetBondBetweenAtoms(B, E)
        if bd:
            if bd.GetBeginAtomIdx() != B:
                bd.SetBeginAtomIdx(B); bd.SetEndAtomIdx(E)
            bd.SetBondDir(d)
    Chem.AssignChiralTypesFromBondDirs(mol)
    Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
    return mol


def report(mol, title, png=None):
    print(f"\n{'='*78}\n{title}\n{'='*78}")
    print("  分子式        :", CalcMolFormula(mol))
    print("  精确质量(单同位素):", f"{Descriptors.ExactMolWt(mol):.4f}")
    print("  平均分子量    :", f"{Descriptors.MolWt(mol):.2f}")
    print("  重原子/环数   :", mol.GetNumHeavyAtoms(), "/", Chem.GetSSSR(mol))
    nst = sum(1 for a in mol.GetAtoms() if a.GetChiralTag() != Chem.ChiralType.CHI_UNSPECIFIED)
    print("  已定义手性中心:", nst)
    smi = Chem.MolToSmiles(mol)
    print("  SMILES        :", smi)
    if png:
        d = rdMolDraw2D.MolDraw2DCairo(2000, 1100)
        d.drawOptions().addStereoAnnotation = False
        mm = Chem.Mol(mol)
        rdMolDraw2D.PrepareAndDrawMolecule(d, mm)
        d.FinishDrawing()
        open(png, "wb").write(d.GetDrawingText())
        print("  结构图        :", png)
    return smi


if __name__ == "__main__":
    path = sys.argv[1]
    root = load(path)
    page = root.find("page")
    items = [("A_labeled", fr) for fr in page.findall("fragment")]
    for g in page.findall("group"):
        items += [("B_natural", fr) for fr in g.findall("fragment")]
    smis = {}
    for tag, fr in items:
        mol = build(fr)
        smis[tag] = report(mol, f"{tag}  (fragment id={fr.get('id')})", png=f"{tag}.png")
        if tag == "A_labeled":
            m0 = build(fr, keep_isotope=False)
            smis["A_stripped"] = Chem.MolToSmiles(m0)
    print(f"\n{'='*78}\n骨架一致性检验（把 A 的同位素抹掉后与 B 比较）\n{'='*78}")
    same = smis.get("A_stripped") == smis.get("B_natural")
    print("  A(去同位素) == B ?", same)
    if not same:
        print("  A':", smis.get("A_stripped")[:200])
        print("  B :", smis.get("B_natural")[:200])
