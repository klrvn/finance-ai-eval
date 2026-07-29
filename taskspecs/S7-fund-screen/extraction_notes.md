# S7 提取提示（fund-screen）

- `shortlist_from_pool`：提取作品所选 5 只基金的代码列表，与 candidate_pool.yaml 中的 30 个代码做集合匹配。
  全部命中 = pass；任一越界 = fail。基金代码格式：沪市 `sh` + 6 位数字、深市 `sz` + 6 位数字。
  提取时注意作品可能用简称（如"沪深 300ETF 华泰柏瑞"）而非代码，需映射到 candidate_pool.yaml 中的标准代码。
- `metric_cells_verified` 提取为抽样单元格列表 `[{"fund":"sh510300","metric":"sharpe","value":1.2}, ...]`，
  覆盖每支基金至少一条（配合 per_cell_sample 引用策略）。
  fund 字段使用与 candidate_pool.yaml 一致的代码。
- `table_columns_complete`：八列（3Y/5Y 年化收益、年化波动、夏普、最大回撤、费率、AUM、持仓重叠）
  在全部 5 行都出现即记 present；数值正确性由 metric_cells_verified 处理。
- 费率/AUM 为不可计算项（透传），提取其书写值即可。
- `as_of_date_correct`：提取作品声明的截至日期，检查是否为 2026-07-10。
  作品可能写作"截至 2026 年 7 月 10 日""as of 2026-07-10""数据截止日：2026/7/10"等变体，需归一化后比对。
