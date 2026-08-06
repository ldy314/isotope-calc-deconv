# CLI 拆为三个子命令（theo / deconv / quantify）

CLI 表面拆为三个互斥职责的子命令：theo（理论同位素分布计算）/ deconv（解卷积）/ quantify（基于已知标样的多组分定量）。

## Decision
拆三部分。理由（用户原话，第三轮 grill）：
- "根据分子式计算同位素分布（参考 ExactMass_Calculator.xlsx）" — 对应 theo。
- "根据质谱图解卷积" — 对应 deconv。
- "根据已有的几个不同同位素取代的化合物在质谱图中计算不同程度同位素取代的占比" — 对应 quantify。

三者在数据流上独立：theo 不读谱图、deconv 读一张谱图 + 自动生成的物种网格、quantify 读一张谱图 + 用户提供的标样库。

## Consequences
- 三个子命令可共享同一组核心模块（formula / isodist / spectrum_io / deconv / report），但不强制。
- 第四个子命令（如 `simulate` 合成谱图生成器）暂不纳入范围；如未来需要，新建 ADR 评估。
- theo 与 ExactMass_Calculator.xlsx 的关系：theo 是其 Python 等价实现，行为需可对照（差异作为测试基准，详见 ADR 后续如需）。