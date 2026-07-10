# S3 盲评扣分锚点（fixed-income-analytics）

> 每维度 4 分起评，逐条登记 `{issue, severity, points, evidence}`。扣分锚点只列「情形 → 严重度」映射。
> D1/D2 为客观维度（检查点通过率锚定）；盲评账本仍独立记录。

## D2 方法正确性
- 计息天数惯例声明与实际计算不一致（convention_consistency 失败）→ major
- 复利方式（连续/复利）声明与计算口径不符 → major
- modified vs Macaulay duration 关系错误（未除以 1+y/f）→ major
- DV01 量纲/符号错误 → minor~major

## D3 完整性
- 8 个必报数值量缺一 → major（每项）
- 可赎回债券未报 OAS 或未声明模型假设 → minor（OAS 本身记 NA，但"是否声明"仍看）

## D4 分析质量与洞察
- 只给数字、无对久期/凸性的风险含义解读 → minor

## D5 可操作性与适配性
- 无「该券在利率情景下的持有/规避」落地判断 → minor

## D6 外部工具链完整度（本家族 A 层界定）
- A 层 = QuantLib/专业定价库/终端；实际执行的定价代码（有轨迹）
- 定价指标凭记忆或手算、无定价库/代码执行轨迹 → severe
- 用了通用电子表格但无专业定价库，关键惯例处理易错 → minor~major（视错误）
- 债券静态参数取自野网页而非披露文件/终端 → minor
