# deconv 用自研 NNLS 模式拟合，IsoCorrectoR 仅作交叉验证参照

2026-08-07 grill-with-docs 会话结晶。核心问题：给定多同位素修饰化合物的高分辨质谱图，如何计算 ExactMass_Impurities_Calc 枚举出的每个杂质的相对含量？候选：IsoCorrectoR（R 包）/ IsoCor（Python）/ 自研 NNLS 模式拟合。

## Decision

**主路线：自研 NNLS（非负最小二乘）模式拟合**（Python，实现 ADR-0004 的 deconv 子命令）：

```
实测高分辨谱(m/z-强度 CSV)
   + imp.py 枚举杂质
   + theo.py 生成每个杂质的理论同位素模式（基模式）
   ──→ scipy NNLS 拟合：谱 = Σ 权重ᵢ × 模式ᵢ
   ──→ 权重归一化到总和 100% = 各杂质相对含量
```

**参照路线：IsoCorrectoR（R 包）**——仅用于单示踪场景的交叉验证，不作为核心引擎。理由（grill 证据）：
- 用户输入是**原始谱（m/z-强度对）**，而 IsoCorrectoR 的 MeasurementFile 要求**已积分的同位素分布分数**（峰面积），需先做峰识别+积分预处理
- 真实场景**示踪元素数不固定**（可能 D+¹³C+¹⁵N 三个及以上），IsoCorrectoR UltraHighRes 模式最多支持 2 个示踪元素
- 本机**无 R 环境**，需另装 R + IsoCorrectoR（R 装 D:\R，包装 D:\IsoCorrectoR）

## 关键边界（grill 逐分支确认）

| 分支 | 决定 |
|---|---|
| 数据形态 | **岛津 LCMS-9030（Q-TOF，分辨率 ~30,000 FWHM）**导出谱，CSV 两列（m/z, intensity），**profile 与 centroid 两种导出都可能，deconv 需同时支持**；用户稍后提供真实示例文件 |
| 分子量范围 | **1–5 kDa（多肽）**：m/z 1000–5000 处峰宽 ~0.03–0.17 Da，¹³C/¹⁵N 近简并对（Δm≈0.006 Da）**不可分辨** → 峰形卷积为必需 |
| 标记模型 | 示踪元素数不固定，方案必须通用（任意 N 维替换） |
| 无修饰化合物 | 仅用于**事后验证**（对照全天然杂质占比合理性），不参与拟合、不作校准标样 |
| 算法路径 | 两者都做：自研 NNLS 为主，IsoCorrectoR 交叉验证为辅 |
| 输出集成 | 新建独立 xlsm：**ExactMass_Deconv.xlsm**（输入区与 Impurities 一致 + 谱图路径 + 参数，输出相对含量） |
| 开发验证 | 先用 theo.py 合成"已知比例混合"模拟谱测试 NNLS 还原度，真实 CSV 到了再做真实验证 |

## Consequences

- deconv 子命令新增依赖：numpy / scipy（NNLS 用 `scipy.optimize.nnls` 或 `lsq_linear`）
- **仪器峰形建模（30k Q-TOF，1–5 kDa 分子）**：¹³C/¹⁵N 近简并对（Δm≈0.006 Da）不可分辨，理论模式须先按 Gaussian（FWHM≈m/30000）卷积再参与 NNLS；对 centroid 峰列表则直接以峰强度拟合（基模式取各峰位置理论强度）；拟合宜支持整体质量偏移（ppm 级）优化以吸收质量校准误差
- **profile / centroid 双模式**：自动识别（m/z 步长密集=profile，稀疏=centroid）或显式参数切换
- **IsoCorrectoR 交叉验证范围收窄**：30k 下大分子近简并峰不可分辨，UltraHighRes 模式（为可分辨 ¹³C/¹⁵N 设计）对大分子不适用，只能用普通模式（质量简并合并）
- 新增文件：`deconv.py`、`build_deconv_excel.py`、`deconv_macro.bas`、`test_deconv.py`
- R 安装到 `D:\R`，IsoCorrectoR 库装到 `D:\IsoCorrectoR`（xls 输出在 Windows 有 bug，用 csv）
- 谱图 CSV 规格：(m/z, intensity) 两列，可含表头行；拟合窗口 = 全部杂质 50 峰质量范围 + 边距
- 无修饰样品谱图用于验证：deconv 结果中"全天然"杂质占比应与预期一致
