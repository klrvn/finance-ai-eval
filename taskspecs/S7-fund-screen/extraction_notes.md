# S7 提取提示（fund-screen）

- `metric_cells_verified` 提取为抽样单元格列表 `[{"fund":"...","metric":"sharpe","value":1.2}, ...]`，
  覆盖每支基金至少一条（配合 per_cell_sample 引用策略）。
- `table_columns_complete`：八列（3Y/5Y 年化收益、年化波动、夏普、最大回撤、费率、AUM、持仓重叠）
  在全部 5 行都出现即记 present；数值正确性由 metric_cells_verified 处理。
- 费率/AUM 为不可计算项（透传），提取其书写值即可。
