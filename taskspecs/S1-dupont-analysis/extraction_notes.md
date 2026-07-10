# S1 提取提示（equity-fundamentals · 杜邦分析）

- `moutai_net_margin` / `moutai_asset_turnover` / `moutai_equity_multiplier` / `moutai_roe` 均提取为**贵州茅台**的标量值；
  单位归一：净利润率与 ROE 记为小数（"52%" → 0.52），周转率记为次数，权益乘数记为倍数。
  `roe_dupont_reconcile` 由评分器对这四个字段求值，提取器不要自算。
- 同业三家的杜邦拆解用于 `peer_dupont_present`（present）与盲评比较；不必逐字段建独立检查点，
  但要在 evidence 中记录其三因子是否齐备。
- `companies_covered` 统计给出完整杜邦拆解的公司数（应为 4）。
- `cited_financials_count` 统计注明来源与报告期的财务原始项（营收/净利/总资产/净资产等）。
