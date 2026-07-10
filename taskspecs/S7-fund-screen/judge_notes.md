# S7 盲评扣分锚点（fund-screen）

> 每维度 4 分起评，逐条登记 `{issue, severity, points, evidence}`。扣分锚点只列「情形 → 严重度」映射。
> 注：objective_dims 为空 —— D1/D2 也由盲评评定（因无权威 NAV 时单元格 NA）。

## D1 数据完整性与依据
- 表格数值无来源/截止日期 → major
- 单一 as-of 日期缺失（各列时点不一）→ minor

## D2 方法正确性
- 3 年/5 年年化口径混淆或与列名不符 → major
- 夏普比率无风险利率口径不明 → minor

## D3 完整性
- 候选不是恰好 5 支 → major
- 八列有任一列在任一行缺失 → minor（每格）
- 缺持仓重叠说明 → minor

## D4 分析质量与洞察
- 排序无理由、纯按单一指标 → major
- 未讨论重叠/风格集中带来的分散不足 → minor

## D5 可操作性与适配性（suitability_consistency 关注点）
- 排序与"中等风险、长期"画像矛盾（如把高波动主题基金排第一且无风险提示）→ major（矛盾严重可 severe）
- 推荐无一行明确适配理由 → minor

## D6 外部工具链完整度（本家族 A 层界定）
- A 层 = 基金评级/数据终端、基金公司官网披露、Wind/Morningstar 接口
- 指标取自野网页抓取/聚合站转载而非权威披露 → major
- 指标凭记忆、无检索轨迹 → severe
