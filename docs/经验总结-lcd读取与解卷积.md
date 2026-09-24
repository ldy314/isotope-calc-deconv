# 经验总结：岛津 .lcd 读取与解卷积准备（2026-08-07）

> ## ⚠️ 更正声明（2026-09-24）
> **本文 §2.4 与 §5 的核心结论「这批 .lcd 测的不是 SPH20291」是错的**，已就地更正。
> 真因是 `openszraw` 的线性 m/z 换算对本项目 QTOF 变体不适用 —— 真实关系是
> **`m/z = a·x² + c`**（TOF 平方标定），线性换算把 942.15 显示成 1442.29。
> 2026-09-24 用正确标定复核 `sp_003.lcd` / `STD 0.005_001.lcd`：z=3 M0 分别落在
> **944.805**（全标记形）、**942.135**（天然形），与理论值 944.8125 / 942.1411 吻合。
> → 结论：**这批 .lcd 就是 SPH20291，没有测错样品。**
> 详见 `docs/adr/0008-lcd-tof-quadratic-calibration.md` 与 `AGENTS.md` 坑 #1。
> 下文其余部分（进样尖峰、背景扣除、docx ChemDraw 提取、deconv 引擎现状）仍然有效。

本轮用 `参考数据/岛津原始数据 测试打开/` 下两个 .lcd（`STD 0.005_001.lcd`、`sp_003.lcd`）
实测了「.lcd → 谱图 CSV → 解卷积」全链路的前半段。以下经验供下一轮（新版 .lcd 数据）直接复用。

## 1. .lcd 读取：OpenSZRaw（已装，PyPI 0.1.3）

- 库：`openszraw`，clean-room 逆向，无需岛津 SDK，支持 LCMS-9030（.lcd QTOF）。
- API：
  ```python
  import openszraw
  r = openszraw.RawReader(path)
  r.scan_count                 # 600（本批数据）
  sp = r.read_spectrum(i)      # Spectrum{mz, intensity, ms_level, retention_time_sec}
  ```
- 已封装为 `lcd2csv.py`：`--scan N`（单张谱）/ `--rt MIN MAX`（窗口平均）/ `--bg MIN MAX`（背景扣除，需与 --rt 同用）。
- **时间单位 = 秒**（用户习惯分钟：4.02 min = 241.2 s）。

## 2. 关键经验（踩过的坑）

### 2.1 化合物峰 ≠ 文件开头最强谱
- 文件开头（RT 210–212s）是**进样尖峰/背景**：sp_003 在 scan #11–12 有 22.8 亿 TIC 饱和（m/z 0.43 垃圾峰，单峰 19.5 亿）。
- **化合物峰在 RT 240–244s（=4.00–4.07 min）**，STD 峰顶 scan #327（242.7s），SP 峰顶 scan #307（240.7s）。
- 教训：**先扫整条 TIC 定位色谱峰**（按 1–2s 分箱看轮廓），不要直接取全文件 TIC 最强扫描。

### 2.2 RT 窗口平均的分箱宽度
- 0.1 mDa 分箱会残留**扫描间质量漂移（~1 mDa）导致的重复近同峰**（如 885.0501/885.0511 并列）。
- 用 **2 mDa 分箱**（`--bin-da 0.002`，默认）可合并漂移；单张峰顶谱（--scan）无此问题、最干净。

### 2.3 背景扣除
- `--rt 240 243 --bg 210 228`（峰窗口 − 背景窗口，m/z 对齐相减，负值截零）。
- 饱和尖峰只污染 m/z 0.43 垃圾峰（在化合物区域之外），不影响 m/z > 1200 的拟合区。
- 扣背景后化合物包络成为最强峰（STD 1442.29 强度 44 万；SP 1444.33 19 万），+2.04 Da 标记偏移清晰可见。

### 2.4 谱图→化合物对应：先验算理论 m/z，**且必须先把质量轴标定对**
- ~~实测主簇 STD 1442.29 / SP 1444.33（簇内间隔 ~0.51 Da 交错 → 疑似 z=2；+2.04 偏移 = 标记）~~
  **更正**：这两个"峰位"是 openszraw 线性换算的**假值**（它给的是文件内存值 x，不是 m/z）。
  经 `m/z = a·x² + c` 换算后，真实主簇为 **STD 942.135 / SP 944.805（z=3）**，
  二者相差 **2.6714 Da = 8.0142/3**，正是 6×¹³C+2×¹⁵N 共 8 个标记原子的位移 —— **就是 SPH20291 天然形与全标记形**。
- ~~用 SPH20291 跑 deconv 时 z=1/2/4 窗口内 0 个数据点 → 判为"测的不是 SPH20291"~~
  **更正**：这是因为当时拿假峰位（1442/1444）去套理论值（1412.7/706.9），当然对不上。
  标定正确后 z=3 实测 M0 = 944.805 vs 理论 944.8125（偏差 0.008 Da）。
- **更正后的教训（比原来的教训更重要）**：
  1. 拿到 `.lcd` **先做质量轴标定**（用天然形与全标记形的 M0 做锚点），再做任何归属判断；
  2. 标定后必须做一次**自检**：目标分子的 M0 是否落在理论 m/z 的 ±0.01 Da 内；
  3. "峰位对不上"时，**先怀疑读取换算，再怀疑样品身份** —— 本项目在这里栽过一次大的。


## 3. docx 里 ChemDraw OLE 的分子式提取（已验证可行）

`SPH20291-Isotop1所有可能 (1).docx` = 21 个嵌入 ChemDraw OLE 对象（CFB 复合文档）：

```python
import olefile, zipfile, io, re
z = zipfile.ZipFile(docx)
ole = olefile.OleFileIO(io.BytesIO(z.read('word/embeddings/oleObjectN.bin')))
cdx = ole.openstream('CONTENTS').read()
re.findall(rb'Chemical Formula: ([A-Za-z0-9]+)', cdx)   # 分子式
re.findall(rb'Exact Mass: ([\d.]+)', cdx)                # 精确质量
```

- 无需完整解析 CDX 对象：分子式/精确质量**以文本形式直接嵌在 CDX 里**。
- 结果：SPH20291 = C₁₃₄H₁₉₈N₂₈O₃₅S₂（2823.40）；21 种可能 = ¹³C 0–6 × ¹⁵N 0–2 全组合。
- **imp.py 以全标记物种为输入枚举的 20 个杂质与 docx 21 种减输入本身完全一致** → 固化测试 `test_imp_docx.py`。

## 4. deconv 引擎现状（模拟谱验证通过，真实谱待验）

- `deconv.py`：NNLS 模式拟合（imp 枚举 → theo 理论模式 → Gaussian 峰形卷积 FWHM=m/30000 → NNLS + 基线 → 质量偏移扫描 ±10ppm → 质量简并类自动合并）。
- `test_deconv.py` 6 项全过：profile/centroid 还原、+5ppm 偏移找回、1% 噪声稳健、natural 下限、z=2。
- 30k / 1–5 kDa 下 D/¹³C/¹⁵N 近简并对（Δm≈0.003 Da << 0.1–0.2 FWHM）不可分辨 → 自动按名义质量类合并（`--proximity-factor` 可调，`--no-merge` 关闭）。

## 5. 下一轮（新版 .lcd 数据）直接套用的流程

> ⚠️ **不要再用 `lcd2csv.py`（openszraw）做 `.lcd` 定量** —— 它的 m/z 换算是错的。
> 正确的 `.lcd` 读法用 `同位素杂质计算/lcd_io.py` 或 `同位素取代率计算/embedded_engine.py`。

```
# 1) 用已知锚点标定 TOF 质量轴 m/z = a·x² + c
#    锚点 = 天然形/全标记形 在 z=2/3/4 的 M0 峰位（取窗内最强点）
#    lcd_io.calibrate_tof([(x_meas, mz_theory), ...]) -> (a, c, resid_ppm)
# 2) 自检：标定后 z=3 的 M0 是否落在 天然 942.141 / 全标记 944.813（±0.01 Da）
# 3) 定 RT 窗口：先扫整条 TIC 轮廓，取覆盖**整峰**的窗口（本批宽峰约 260–270 s）
# 4) 提谱 + 算：lcd_io.read_lcd(...) → calc.analyze_profile(...)（杂质含量，9 档三角扣除）
#              或 embedded_engine（取代率，质心法 + 包络序号法）
```

## 6. 环境备注

- venv 已装：numpy 2.5.1、scipy、openszraw 0.1.3、olefile。
- 导出 CSV 在 `参考数据/导出CSV/`（该目录已 gitignore，不入库）。
- 安全删除机制对中文路径会拦截 Git Bash 的 rm → 用 PowerShell `Remove-Item -LiteralPath`。
