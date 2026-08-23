# -*- coding: utf-8 -*-
"""逐残基细节确认：N端/C端修饰、甲基位置、侧链片段 SMILES。"""
import sys
from rdkit import Chem
from cdxml_to_rdkit import load, build
from peptide_seq import find_residues, sc_key


def frag_smiles(mol, ids, anchor=None):
    """取出指定原子集合的子结构 SMILES"""
    ids = sorted(set(ids))
    if not ids:
        return "(空)"
    amap = {}
    sub = Chem.RWMol()
    for i in ids:
        a = Chem.Atom(mol.GetAtomWithIdx(i).GetSymbol())
        a.SetIsotope(mol.GetAtomWithIdx(i).GetIsotope())
        a.SetFormalCharge(mol.GetAtomWithIdx(i).GetFormalCharge())
        a.SetIsAromatic(mol.GetAtomWithIdx(i).GetIsAromatic())
        amap[i] = sub.AddAtom(a)
    for b in mol.GetBonds():
        i, j = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
        if i in amap and j in amap:
            sub.AddBond(amap[i], amap[j], b.GetBondType())
    m = sub.GetMol()
    try:
        Chem.SanitizeMol(m)
    except Exception:
        Chem.SanitizeMol(m, Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_KEKULIZE, catchErrors=True)
    return Chem.MolToSmiles(m)


path = sys.argv[1]
root = load(path); page = root.find("page")
fr = page.find("group").find("fragment")          # 天然版更好读
mol = build(fr)
res = find_residues(mol)

print("=" * 90)
print("逐残基细节（天然版 SPH20291，N 端 -> C 端）")
print("=" * 90)
for i, r in enumerate(res, 1):
    ca, n, c, o = r["ca"], r["n"], r["c"], r["o"]
    nat = mol.GetAtomWithIdx(n)
    # 主链 N 的取代基（除 CA 和前一残基羰基外）
    nsubs = []
    for nb in nat.GetNeighbors():
        if nb.GetIdx() == ca:
            continue
        nsubs.append((nb.GetIdx(), nb.GetSymbol(), nb.GetTotalNumHs()))
    cat = mol.GetAtomWithIdx(c)
    csubs = [(nb.GetIdx(), nb.GetSymbol()) for nb in cat.GetNeighbors() if nb.GetIdx() not in (ca, o)]
    smi = frag_smiles(mol, r["sc"]) if len(r["sc"]) < 25 else f"(大侧链 {len(r['sc'])} 原子，见下)"
    print(f"\n[{i:>2}] CA={ca}  CA上H={mol.GetAtomWithIdx(ca).GetTotalNumHs()}  侧链={sc_key(mol, r['sc']) or '无'}")
    print(f"     主链N({n}) H数={nat.GetTotalNumHs()}  N上其他取代={nsubs}")
    print(f"     主链C({c})=O({o}) 另一取代={csubs}")
    print(f"     侧链SMILES: {smi}")

print("\n" + "=" * 90)
print("残基 9（季碳、大侧链）拆解")
print("=" * 90)
r9 = res[8]
print("  侧链 SMILES:", frag_smiles(mol, r9["sc"]))

print("\n" + "=" * 90)
print("甲基色氨酸（残基4）取代位置判定")
print("=" * 90)
r4 = res[3]
sub = frag_smiles(mol, r4["sc"])
print("  侧链:", sub)
for pat, label in [("Cc1cccc2c1[nH]cc2", "7-甲基吲哚"), ("Cc1ccc2[nH]ccc2c1", "6-甲基"),
                   ("Cc1cc2cc[nH]c2cc1", "5-甲基"), ("Cc1cccc2[nH]ccc12", "4-甲基")]:
    q = Chem.MolFromSmiles(pat)
    if q:
        m = Chem.MolFromSmiles(sub)
        print(f"    匹配 {label:<10}: {m.HasSubstructMatch(q) if m else 'n/a'}")

print("\n" + "=" * 90)
print("末端确认")
print("=" * 90)
first, last = res[0], res[-1]
nat = mol.GetAtomWithIdx(first["n"])
env = {first["n"], first["ca"]}
for nb in nat.GetNeighbors():
    env.add(nb.GetIdx())
    for nb2 in nb.GetNeighbors():
        env.add(nb2.GetIdx())
print("  N端环境 SMILES:", frag_smiles(mol, env))
cat = mol.GetAtomWithIdx(last["c"])
env2 = {last["ca"], last["c"], last["o"], last["n"]}
for nb in cat.GetNeighbors():
    env2.add(nb.GetIdx())
for nb in mol.GetAtomWithIdx(last["n"]).GetNeighbors():
    env2.add(nb.GetIdx())
print("  C端环境 SMILES:", frag_smiles(mol, env2))
