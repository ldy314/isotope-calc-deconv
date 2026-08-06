# Excel 计算引擎 = Python 后端（molmass 卷积核心）

ExactMass_Calculator.xlsx 的前20同位素峰计算不再内嵌公式/宏，改由独立 Python 脚本（molmass 库）完成后回填 Excel；卷积核心与 CLI 的 theo 子命令复用同一套代码。

## Considered Options
- **VBA 宏（文件自包含）** — 拷贝即用、无环境依赖，但卷积代码与 Python CLI 形成两套独立实现，数值细节（近简并峰、截断策略）容易分叉。
- **纯公式展开** — 大分子（如 C134H198O35N28）公式爆炸，不可行。
- **Python 后端（选定）** — Excel 只做输入界面与结果回填，计算统一走 molmass，与 theo / deconv / quantify 共享同一核心模块。

## Decision
选 Python 后端，且 **Excel 直接调用 theo 子命令本身**（非独立模块）。理由：用户明确后续要解卷积与定量（deconv / quantify），若 Excel 与 CLI 各自算一遍理论分布，两套结果必然在边界情形（峰合并、截断、归一化）分叉，无法互相对照验证。统一核心后，Excel 理论分布 = theo 子命令输出 = deconv 物种网格的生成依据，三者同源。theo 即唯一实现（API），Excel 按钮是它的一个调用方。

## Consequences
- Excel 需另存为 .xlsm（含触发按钮的少量 VBA），按钮调用 `python isodist.py` 或等价 CLI，经临时文件（JSON/CSV）交换输入输出。
- 依赖本机 Python + molmass；缺环境时 Excel 按钮报错并提示（同 ADR-0003 的"显式报错"纪律）。
- 文件不再完全自包含；拷贝给同事前需说明环境要求。
