# 四种输入模态全部在范围内

输入层同时支持分子式（含同位素计数）、多肽一字母序列（含内联同位素语法）、SMILES、ChemDraw（CDXML + CDX 二进制），最终都归约为一个基础中性化学式再与同位素替换列表叠加。

## Decision
四种全选。理由：用户原话"最好可以读 ChemDraw"虽用"最好"措辞，但在第二轮 grill 中明确将其与其它三种一同勾选，实际等同于必选。ChemDraw 在论文/专利附图场景几乎不可替代，仅留 SMILES 会迫使用户二次画结构。

## Consequences
- 依赖项：molmass（基础）/ rdkit（SMILES、molfile、CDXML 补氢）/ lxml（CDXML 纯 Python 解析）/ openbabel（CDX 二进制，best-effort）。
- CDX 二进制路径在 OpenBabel 不可用时必须**显式报错并提示安装**，而不是静默失败——这一点在审计汇总（2026-08-06）"假绿教训"中已作为回归教训固化。
- 多肽一字母序列 → 化学式映射需含 ¹³C 内联语法（如 `GFL{13C6}F`），并覆盖 20 种标准氨基酸 + 常见修饰（氧化 M、磷酸化等），细节由后续 ADR 决定。