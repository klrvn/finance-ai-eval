---
name: eval-checkpoint-grader
description: >
  以逐字段容差将一份作品提取出的数值与基准真值做确定性比较，并为每个检查点输出 通过/未通过/不适用（NA）
  以及偏差值。强制应用 CF5（错误的数字记为零分，不给部分分），并在基准真值缺失时将检查点标记为 NA。
  请在提取与基准真值构建之后，针对每份作品调用。这是代码逻辑，而非主观判断——绝不要凭直觉给检查点打分。
---

# 评测检查点评分器

运行比较器，不要肉眼看数字。脚本读取任务的 `checkpoint_schema`、作品的 `normalized.json` 与 `groundtruth.json`，并生成 `det_results.json`。

## 语义规则

- **容差**：默认绝对容差；当模式字段设置 `rel: true` 时为相对容差（偏差值与 `tol * |ground_truth|` 比较）。`bp` 类型的字段以基点为单位比较。
- **MISSING**：必答检查点上提取到的值缺失 -> `fail`（缺失），而非 NA。
- **NA**：该字段的基准真值缺失（例如未提供实时市场数据），或结构化检查所需的可执行契约不完整（`reconcile` 缺 `formula`、`consistency` 缺 `lhs`/`rhs`）。NA 检查点不计入通过率，单独报告，并说明需要什么数据才能消解。
- **CF5**：任何确定性检查点（标量**或**结构化）超出容差 -> 该检查点记 0 分并进入 `cf5_hits`。不存在"差不多"部分分；一个看似合理的错误数字也按未通过处理。

## 检查点类型（均已在脚本中实现，不再落 NA）

| 类别 | 类型 | 语义 |
|---|---|---|
| 标量 | `bp`/`yr`/`pct`/`ratio`/`number` | 与基准真值按容差比较（`bp` 以基点、`rel:true` 以相对容差） |
| 基数 | `count_eq`/`count_min`/`sum_to`/`present` | 计数/求和/存在性，无需基准真值 |
| 结构 | `vector` | 提取的 `{键:数值}` 字典或列表 vs 基准真值同构，逐元素容差；键序无关 |
| 结构 | `set_match` | 提取的标识符列表；`tol`=至多漏掉的真实成员数（漏 1 = tol 1） |
| 内部 | `reconcile` | `\|自报值 − eval(formula, 其他提取字段)\| <= tol` |
| 内部 | `consistency` | `\|eval(lhs) − eval(rhs)\| <= tol`；可用 `<field>__sum/__mean/__len` 规约向量字段。需重执行的（S2 NAV 复现）由 `validator:` 专用验证器处理 |
| 内部 | `monotonic` | 二维网格按 `row_dir`/`col_dir`（asc/desc）校验单调 |
| 抽样 | `sample_verify` | 抽样单元格 `[{fund,metric,value}]` vs `groundtruth.values.funds` |

`formula`/`lhs`/`rhs` 由一个**受限算术求值器**（仅 `+ - * / ** %`、一元正负、`abs/min/max`，变量绑定到提取的数值）执行，不使用 `eval`，无任意代码执行风险。

## 输出（`det_results.json`）

```json
{"work_label":"A","task_id":"S3",
 "checkpoints":{"ytm":{"result":"pass","delta":0.4,"unit":"bp"},
                "modified_duration":{"result":"fail","delta":0.31,"unit":"yr","cf5":true},
                "allocation_by_sector":{"result":"fail","delta":0.9944,"cf5":true},
                "upside_downside_pct":{"result":"fail","claimed":0.5,"computed":0.2,"cf5":true}, ...},
 "pass_fraction": 0.72,                     // 针对非 NA、可比较的全部检查点
 "grounding_pass_fraction": 0.80,           // 仅针对 grounding:true 的检查点 -> 喂 D1
 "methodology_pass_fraction": 0.86,         // 仅针对 methodology:true 的检查点 -> 喂 D2
 "na_checkpoints": ["oas"],
 "cf5_hits": ["modified_duration","allocation_by_sector","upside_downside_pct"]}
```

## 用法

```bash
python scripts/grade_checkpoints.py --schema <task_schema.json> \
  --normalized run/A/normalized.json --groundtruth run/groundtruth.json --out run/A/det_results.json
```

`grounding_pass_fraction` 喂 D1（grounding 那一半），`methodology_pass_fraction` 喂 D2；两者缺省时 D2 回退到 `pass_fraction`。汇总器按**连续**比例计分（`比例 × 权重`），不再把比例量化到 0-4 等级后再折算，从而消除量化悬崖。评分器绝不会看到量规或评分官——严格隔离。
