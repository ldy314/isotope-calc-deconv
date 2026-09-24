# chem.py — 同位素计算 / 杂质枚举 / 解卷积 命令行

**不再需要打开 Excel。** 所有功能一行命令完成，分子式直接写字符串或用化合物别名。

Excel 那两个 xlsm 仍然可用（`ExactMass_Impurities.xlsm` 杂质枚举、`ExactMass_Impurities_Deconv.xlsm` 解卷积），底层调的是同一批引擎，结果完全一致。

---

## 0. 准备

Python 解释器（隔离 venv，已装好 `molmass` / `numpy` / `scipy` / `rdkit`）：

```
C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe
```

直接调用：

```bash
cd "D:/code test/chem/同位素计算及解卷积"
C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe chem.py ls
```

**建议设个别名**，之后就能直接敲 `chem ...`。把下面这行写进 `~/.bashrc`（Git Bash），新开终端生效——注意 alias 必须预先写进配置文件，在同一条命令里现定义现用是不生效的：

```bash
alias chem='C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe "D:/code test/chem/同位素计算及解卷积/chem.py"'
```

PowerShell 版（写进 `$PROFILE`）：

```powershell
function chem { & "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" "D:\code test\chem\同位素计算及解卷积\chem.py" @args }
```

本文档后续示例均以 `chem` 别名书写。

---

## 1. 分子式语法

| 写法 | 含义 |
|---|---|
| `C134H198N28O35S2` | 纯天然 |
| `C128[13C]6H198N26[15N]2O35S2` | 方括号指定同位素 |
| `C128(13C)6...` | 圆括号等价 |
| `D10` / `T2` | D=²H（氘）、T=³H（氚） |
| `(CH2)5OH` | 支持分组与倍数（可嵌套） |
| `C` | 省略个数 = 1 |

规则：同位素质量数写在括号内、元素符号之前；同一 (元素,同位素) 重复出现会自动合并计数；解析顺序即首次出现顺序，与 Excel 逐列输入一致。

查看解析结果 / 转成 `--elements` 形式：

```bash
chem parse "C128[13C]6H198N26[15N]2O35S2"
```

---

## 2. 化合物别名库

存在 `compounds.json`，避免每次敲长分子式。

```bash
chem ls                                              # 列出全部
chem add MyPep "C50H80N12O15" --z 2 --note "测试肽"   # 新增/更新
chem rm MyPep                                        # 删除
```

已内置：

| 别名 | 分子式 | z | 说明 |
|---|---|---|---|
| `SPH20291` | C₁₃₄H₁₉₈N₂₈O₃₅S₂ | 2 | 天然版，单同位素 2823.4016 |
| `SPH20291-Isotope1` | C₁₂₈¹³C₆H₁₉₈N₂₆¹⁵N₂O₃₅S₂ | 2 | SILAC Lys8 标记版，单同位素 2831.4158 |

别名不区分大小写；带 `z` 的别名会作为默认电荷，`--z` 可覆盖。

---

## 3. 四个核心命令

### 3.1 `theo` — 理论同位素谱

```bash
chem theo SPH20291 --top-n 10
chem theo "C6H12O6" --z 1 --out glucose.csv
```

常用参数：`--z` 电荷、`--top-n` 峰数、`--min-ab` 丰度阈值%（默认 0.05）、`--tol` 峰合并容差 Da（默认 0.001）、`--out` 导出 CSV。

### 3.2 `imp` — 枚举同位素取代杂质 + 逐杂质理论峰

等价于 Excel 里「列举并计算」按钮。**仅适用于带同位素标记的分子**（纯天然分子无杂质可枚举，请用 `theo`）。

```bash
chem imp SPH20291-Isotope1
chem imp "C128[13C]6H198N26[15N]2O35S2" --z 2 --out-dir out --prefix run1
chem imp SPH20291-Isotope1 --no-files          # 只看屏幕输出
```

枚举规则：每个修饰列把 k 个标记原子换回天然（k=1..n），多列做笛卡尔积，排除输入本身。
例：¹³C×6 + ¹⁵N×2 → (6+1)×(2+1) − 1 = **20** 个杂质（含全天然形）。

输出三个文件（默认脚本目录，可用 `--out-dir` 改）：

- `*_summary_z2_<时间戳>.csv` — 每杂质一行（分子式/保留标记/替换天然/单同位素质量/m-z/单峰相对%）
- `*_peaks_z2_<时间戳>.csv` — 逐峰明细（杂质数 × `--top-n` 行）
- `*_list_z2_<时间戳>.txt` — 4 行/杂质，可回贴 Excel 输入区

### 3.3 `deconv` — 解卷积 / 同位素富集度

```bash
chem deconv SPH20291-Isotope1 --spectrum "解卷积/SPH20291_MS+_deconv/spec_msplus_from_jdx.csv"
chem deconv SPH20291-Isotope1 --spectrum spec.csv --lo 1410 --hi 1422 --no-nnls
chem deconv SPH20291-Isotope1 --spectrum spec.csv --json    # 完整 JSON 打到 stdout
chem deconv SPH20291-Isotope1 --spectrum data.jdx --z 4     # 直接吃 .jdx（内部自动转 CSV）
```

参数：`--spectrum` 实测谱（必填，接受 CSV **或 `.jdx`**；传 `.jdx` 时内部先用 `jdx2csv` 自动转 CSV）、`--lo/--hi` 拟合窗口（默认按理论质心自动 ±2 Da 隔离电荷簇）、`--resolution` FWHM（默认 30000）、`--no-nnls` 只跑质心法、`--out` JSON 路径（默认 `./deconv_result.json`）。

产出 `deconv_result.json` + `_scalars.txt`（key=value）+ `_table.csv`（NNLS 逐杂质）。

**两套算法，看哪个可信：**

- **质心法（稳健，优先用）** — 强度加权质心在「全天然」与「全标记」理论质心间线性插值：
  `富集度 = (m_meas − m_nat) / (m_lab − m_nat)`。对包络重叠不敏感。
- **NNLS 逐杂质** — 只在**碳数 ≤ 50 且相对含量合计接近 100%** 时可信。大分子（如 SPH20291 的 134 个碳）同位素包络高度重叠，NNLS 解非唯一，程序会标注**【不可靠】**并给出原因，此时以质心法为准。

### 3.4 `lcd` — 岛津 .lcd 导出 CSV

参数（含 `--help`）完全透传给 `lcd2csv.py`：

```bash
chem lcd --lcd data.lcd --list                       # 先列出所有扫描
chem lcd --lcd data.lcd --rt 300 360 --out spec.csv  # RT 窗口平均，单位为秒
chem lcd --lcd data.lcd --rt 300 360 --bg 400 460 --out spec.csv   # 扣背景
```

### 3.5 `jdx` — 岛津 .jdx（JCAMP-DX 质心谱）导出 CSV

与 `lcd` 平行：把 LabSolutions 导出的 `.jdx` 质心谱转成 `(m/z,intensity)` CSV，供 `deconv` 直接吃。参数（含 `--help`）完全透传给 `jdx2csv.py`。

```bash
chem jdx --jdx data.jdx --list          # 仅打印文件头元数据（TITLE/RT/NPOINTS…）
chem jdx --jdx data.jdx --out spec.csv  # 转 CSV（默认写到同目录同名 .csv）
```

> 若只是想对 `.jdx` 做富集度分析，不必手动先转——`chem deconv ... --spectrum data.jdx` 会自动调用它。

---

## 4. 典型工作流

```bash
# 1) 从原始数据导出谱图
chem lcd --lcd SPH20291.lcd --rt 300 360 --out spec.csv

# 2) 看理论谱长什么样
chem theo SPH20291-Isotope1 --top-n 15

# 3) 枚举所有同位素杂质（拿理论 m/z 清单）
chem imp SPH20291-Isotope1

# 4) 对实测谱做富集度分析
chem deconv SPH20291-Isotope1 --spectrum spec.csv
```

---

## 5. 文件说明

| 文件 | 作用 |
|---|---|
| `chem.py` | **统一 CLI 入口**（本文档主角） |
| `formula.py` | 分子式解析 + 化合物库读写 |
| `compounds.json` | 化合物别名库 |
| `theo.py` | 理论同位素谱引擎（多项式卷积） |
| `imp.py` | 杂质枚举引擎 |
| `deconv.py` | 解卷积核心（NNLS + 高斯卷积） |
| `deconv_excel.py` | 解卷积外壳（质心法 + 病态诊断），CLI 与 Excel 共用 |
| `lcd2csv.py` | 岛津 .lcd 读取导出 |
| `jdx2csv.py` | 岛津 .jdx（JCAMP-DX 质心谱）读取导出 |
| `analyze_remaining.py` | 批量解卷积「再处理2」其余 3 文件夹，产出 summary/report |
| `chemdraw/parse_cdxml.py` 等 | ChemDraw .cdxml 结构解析（另一套工具） |

Excel 版仍在：`ExactMass_Impurities.xlsm`（杂质枚举）、`ExactMass_Impurities_Deconv.xlsm`（解卷积）。

---

## 6. 已知注意事项

- **Windows 路径**：Git Bash 里给 `.exe` 传 `/d/...` 会被转成 `c:\d\...`，请先 `cd` 到目录再用相对路径，或用 `D:/...` 正斜杠形式。
- **`imp` 对纯天然分子无意义** —— 会提示改用 `theo`。
- **NNLS 对大分子不可信** —— 见 3.3，认准输出里的【不可靠】标记。
- 输出 CSV 均为 `utf-8-sig`（带 BOM），Excel 直接双击打开不乱码。
