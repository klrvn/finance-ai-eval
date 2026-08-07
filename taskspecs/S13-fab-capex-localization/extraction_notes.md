# S13 提取提示（capex-allocation · 存储扩产国产设备受益测算）

本文件只固定书写形式，不改变 `checkpoint_schema.yaml`。所有示例均使用与本题无关的占位数值，
避免把任何锚点值泄漏进提取器上下文。

## 单位归一（最重要）

| 字段类 | 提取形态 |
|---|---|
| 产能 `*_wspm` | 以**万片/月**为单位的裸数值（作品写「3 万片/月」→ 记 `3`；写「30000 片/月」→ 记 `3` 并在 evidence 保留原文） |
| 单价 `*_unit_price` | 以**亿元/万片月产能**为单位的裸数值（写「约 60 亿元/万片」→ 记 `60`） |
| 金额（设备总额 / 环节额 / 国产额 / 公司增量 / 营收 / 募资额） | 以**亿元**为单位的裸数值 |
| 占比与国产化率 `*_weight` / `ymtc_phase3_localization` / `naura_kingsemi_stake` | 小数，或**数值 + `unit_as_written: "%"`** |

> **占比与国产化率的两种写法都可以**，因为这些字段的类型是 `number` / `ratio` / `sum_to`，
> `validators.py` 会把带 `%` 单位的值转成小数（`22` + `unit_as_written: "%"` → `0.22`）。
> 但**必须二选一，不能混**：写成字符串 `"22%"` 却把 `unit_as_written` 留空，验证器的
> 首数字正则会取出 `22`、而没有单位可依据故不做除法，于是占比被**放大 100 倍**（0.22 → 22）。
> 要么记 `0.22` 且不写单位，要么记 `22` 且 `unit_as_written: "%"`。

若作品以美元报价（3D NAND 的单价常见美元口径，如「5.4 亿美元/万片」），**不要自行套用汇率
换算**——汇率不是确定性换算。按原值提取并在 `unit_as_written` 记 `"亿美元"`，在 evidence
保留原文。该情形下相关的 reconcile 检查点会因量纲不一致而失败，属真实缺陷（题面已要求
人民币美元口径须换算后方可相加）。

## 字典型字段的形态

以下三个字段必须提取为**对象**，不要压成标量或数组——评分器由 `build_env` 对其派生 `__sum`：

```json
"dram_step_domestic":  {"刻蚀": 11.3, "薄膜沉积": 11.1, "清洗": 6.3, "光刻": 0.39, "...": 0.0},
"nand_step_domestic":  {"刻蚀": 12.6, "薄膜沉积": 8.7,  "...": 0.0},
"company_increments":  {"北方华创": 21.4, "中微公司": 9.8, "...": 0.0}
```

键名按作品原文书写（环节名/公司名），键序无关，值为**亿元裸数值**。
非数值的成员（如某公司标注「待查」）请省略该键，并在 evidence 中记录。

## 逐字段要点

- `dram_step_weights_sum` / `nand_step_weights_sum`（`sum_to`）：提取**作品自己给出的占比合计
  单元格**的值。题面已要求这是一个校验活公式，作品理应有这一格。若作品没有任何合计格，
  记 `MISSING`——**不要自己把各环节占比加起来填进去**。提取器只报告呈现了什么。
- `dram_equipment_total` / `nand_equipment_total` / `dram_etch_amount` / `nand_etch_amount` /
  `dram_domestic_total` / `nand_domestic_total`（`reconcile`）：提取作品**自报**的那个值。
  其公式由评分器用其他字段求值比对，**你不要自己算**。
- `company_vs_step_tie_out`、`litho_domestic_near_zero`、`domestic_share_band`、
  `etch_depo_share_dram`、`etch_depo_share_nand`（`consistency`）：这五个字段**不需要提取值**，
  记 `MISSING` 即可。评分器只对它们的 `lhs`/`rhs` 表达式求值，从不读字段本身。
  真正要保证被提取到的是它们引用的那些依赖字段。
- `dram_litho_domestic` / `nand_litho_domestic`：光刻环节的**国产**金额。作品若明确写「光刻
  国产受益为零/近零」，记 `0`（不是 `MISSING`）——这是一个有意给出的结果，不是缺失。
  作品若整张表没有光刻行，记 `MISSING`。
- `ymtc_phase3_localization`：长存武汉三期新产线的国产设备采购占比。若作品同时给了多个口径
  的国产化率，取其**用于三期/新平台分摊的那一个**，并在 evidence 中记录它是哪一处。
  若作品全程只用一个不区分平台的国产化率，就提取那一个。
- `*_revenue_2025`：各设备厂商 2025 年**全年营业收入**，亿元。作品给的是季度或 TTM 时记
  `AMBIGUOUS` 并在 evidence 说明口径。
- `naura_kingsemi_stake`：北方华创持有芯源微的**股权比例**。作品若只给了持股金额或股数而无
  比例，记 `MISSING`。
- `workbook_formula_layer_present`：作品是否暴露了可检查的公式层。`.xlsx` 作品经
  `tools/workbook_dump.py` 展开后，公式列非空即为 `true`；文本作品需逐格给出计算式才算。
  只给数字表格、或只写「见附件 model.xlsx」而不展开，记 `false`。
- `dram_nand_structure_differentiated`：两厂是否各有一套环节占比向量。两列数值完全相同
  （或第二列直接引用第一列）记 `false`。
- `assumption_cells_count`：可调假设格的**类别数**（单价 / 环节占比 / 国产化率 / 公司份额），
  不是单元格个数。
- `process_steps_covered` / `companies_covered`：整数计数。
- `sensitivity_monotonic`：二维数组 `[[...], [...], ...]`，行沿第一个假设轴递增、列沿第二个
  假设轴递增。作品无敏感性表时记 `MISSING`。

## 【硬约束：以下格式漂移会让一个本来正确的答案被判未通过】

1. 占比/国产化率写成字符串 `"22%"` 且 `unit_as_written` 留空 → 值被放大 100 倍（0.22 → 22）。
   **注意它不是转 NA，而是判未通过**——值仍是数值，评分器照常求值，只是结果荒谬。
   已实测：一份本来正确的作品会因此吃到 **6 个 methodology 检查点误判未通过**
   （两个 `*_step_weights_sum`、两个 `*_etch_amount`、两个 `etch_depo_share_*`），
   D2 第一层基线从满分掉到约 0.54。
   诊断特征很好认：`*_etch_amount` 的 Δ 达到四位数（如 3742），
   `etch_depo_share_*` 的 Δ 达到两位数（如 42.5）。看到这种量级就是量纲漂移，不是真错误。
2. 字典型字段被压成标量（只提取了合计数）→ `__sum` 无法派生，三个 reconcile 与
   `company_vs_step_tie_out` 全部转 NA，D2 第一层基线的分母被掏空。
3. 产能与单价的量纲不配套（一个用「片/月」一个用「亿元/万片」）→
   `dram_equipment_total` / `nand_equipment_total` 以 10000 倍的偏差失败。
4. 把 `MISSING` 与 `0` 混用：光刻国产受益明确为零应记 `0`；记成 `MISSING` 会使
   `litho_domestic_near_zero` 因 KeyError 转 NA，那道最关键的红线检查就白设了。
5. 依赖字段漏提取：`consistency` 的 `lhs`/`rhs` 中任一名字缺失即整条检查点转 NA
   （不扣分，但也不产生信号）。C 组的 15 个依赖字段务必逐个扫过。
