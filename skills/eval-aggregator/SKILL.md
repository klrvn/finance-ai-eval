---
name: eval-aggregator
description: >
  以扣分制将一份作品的各项检查点结果、引用审计、两次评分官扣分账本以及 CF 标记，汇总为一个 0-100 分的总分，
  并给出六维度详细拆解——每个维度含等级、权重、得分、来源、逐条扣分记录（扣多少/为什么/证据）、
  客观指标明细、CF 封顶信息与人类可读摘要。之后以报告模式在全部评分卡（1 份或多份）上再调用一次，
  产出统一格式评测报告：总分与排名表 + D1-D6 逐作品详细评分。这是唯一能同时看到所有通道的阶段。
---

# 评测汇总器

将各通道的输出转化为分数，再转化为统一格式的评测报告。所有运算都在 `scripts/aggregate.py` 中完成；请运行脚本，而非手工计算。

**扣分制**：每个维度满分起评，只有明确标记的缺陷才扣分。客观维度的每一笔扣分是一个有名有姓的未通过检查点；主观维度的每一笔扣分是评分官账本里一条带证据的 `{issue, severity, points, evidence}` 记录。NA（不可核验）不扣分，从分母剔除并披露。

## 单作品评分

对于每个维度，汇总器收集以下信息：
- **两层合成**：每个维度的 `fraction`(0-1) 由两层取**算术平均**得出，展示用等级为 `level = round(4*fraction, 2)`。
  - `fraction = (第一层基线 + 第二层扣分系数) / 2`
  - **第一层基线** = 该维度实测出的客观比例，仅对 `objective_dims`（⊆ {D1,D2}）中的维度存在。
  - **第二层扣分系数** = `max(0, (4 − 两位评分官平均扣分点) / 4)`。单个评分官的等级为
    `level_j = max(0, 4 − Σpoints_j)`，取自 `judge_N.json` 的 `deductions` 账本；无账本的旧版评审文件回退到其 `levels` 字段。
  - **第一层缺席的维度不参与平均**（D3/D4/D6，或客观指标缺失时）：`fraction` 就是纯扣分系数，与两层模型之前逐位一致。切勿与隐含的 1.0 取平均——那会让纯盲评维度保底 0.5。
  - `D1` 第一层基线 `= grounding_objective`，其中 `grounding_objective` =
    当存在引用策略且容器覆盖 citation > 0 时为 `cw*verified_fraction + gw*grounding_pass_fraction`，缺省 `{citation:0.0, grounding:1.0}` 时为 `grounding_pass_fraction`。
  - `D2` 第一层基线 `= methodology_pass_fraction`（若任务未标 `methodology` 则回退到 `pass_fraction`）。
  - 实现见 `scripts/aggregate.py` 的 `combine_layers()`；规则与设计理由见 `rubrics/constitution.md` §计分。
- **扣分来源**：客观维度的 `objective_detail.failed_checkpoints` 列出具体未通过的检查点名（依据 taskspec 的 `checkpoint_schema` 字段标记归入 grounding/methodology）；主观维度的 `judge_N.deductions` 保存评分官的逐条扣分记录。
- **权重与得分**：`weighted_points = fraction * weight`（**连续**，不先量化到 0-4 再折算——消除量化悬崖与银行家舍入的不一致）。权重来自任务的 `rubric_weights`。
- **有效分母披露**：若某维度既无客观指标又无评分官等级（`level` 为 None），它既不默认满分、也不被扣分——其权重从总分中缺失。汇总器输出 `scored_weight`（实际参与计分的权重之和）、`unscored_dims` 与 `score_normalized`（`总分/scored_weight*100`），使部分评分的运行不会被误读为满 /100。
- **评分官账本**：两位评分官各自的等级、扣分记录、理由文本和证据引用（来自 `judge_1.json` / `judge_2.json`）。
- **客观指标明细**：D1 含 `grounding_objective`、`verified_fraction`、`grounding_pass_fraction`、`failed_checkpoints`；D2 含 `methodology_pass_fraction`/`pass_fraction`、`basis`、`failed_checkpoints`。
- **CF 封顶**：若被 CF 规则封顶，记录 `capped_by`（规则名）和 `capped_from`（封顶前原始等级）。
- **人类可读摘要**：自动生成一段中文摘要，串联上述所有信息。

原始得分 = 六维度 `weighted_points` 之和。然后应用来自 `cf_flags.json` 的 CF 封顶
（仅已确认标记；需要人工签核的 CF1 仅作呈报，在获得批准前不应用）：
`CF1 -> min(total,30)`、`CF2/CF4 -> D1 等级 <= 1`、`CF3 -> D2、D3 等级 <= 1`（封顶后重新计算维度得分与总分）。

### 输出 `scorecard.json`（节选）

```json
{
  "work_label": "A",
  "task_id": "S3",
  "score": 77.08,
  "scored_weight": 100,
  "score_normalized": 77.08,
  "unscored_dims": [],
  "dimensions": {
    "D2": {
      "name": "方法论正确性",
      "level": 3, "fraction": 0.6667, "source": "layer1+layer2",
      "weight": 35, "weighted_points": 23.33,
      "needs_review": false, "capped_by": null, "capped_from": null,
      "judge_1": {"level": 3.5, "rationale": "复利口径有小瑕疵",
                   "deductions": [{"issue": "复利基准未声明即切换", "severity": "minor",
                                    "points": 0.5, "evidence": "\"按年复利……（后文改用连续复利）\""}],
                   "evidence": null},
      "judge_2": {"level": 4, "rationale": "未发现可扣分问题", "deductions": [], "evidence": null},
      "objective_detail": {"methodology_pass_fraction": 0.6667, "pass_fraction": 0.75,
                            "basis": "methodology_pass_fraction", "failed_checkpoints": ["ytm"]},
      "summary": "[D2] 方法论正确性（权重 35）| 等级 3（良好），得分 23.3/35 | 客观依据：方法论检查点通过率=0.6667 | 扣分来源（未通过检查点）：ytm"
    },
    "D6": {
      "name": "外部工具链完整度",
      "level": 3, "source": "judge_mean", "weight": 20, "weighted_points": 15.0,
      "judge_1": {"level": 3, "rationale": "计算无执行轨迹",
                   "deductions": [{"issue": "计算无执行轨迹", "severity": "major", "points": 1,
                                    "evidence": "全文无代码/工具调用记录"}]},
      "judge_2": {"level": 3, "rationale": "工具链覆盖不完整",
                   "deductions": [{"issue": "工具链覆盖不完整", "severity": "major", "points": 1,
                                    "evidence": "部分关键步骤无工具支撑"}]},
      "summary": "..."
    },
    "D1": {"...": "..."}, "D3": {"...": "..."}, "D4": {"...": "..."}, "D5": {"...": "..."}
  },
  "dimension_summaries": [{"dim": "D1", "...": "..."}],
  "pass_fraction": 0.75,
  "grounding_objective": 0.6667,
  "na_checkpoints": ["oas"],
  "cf_applied": [],
  "cf_pending_human": [],
  "needs_review_dims": [],
  "headline": "检查点未通过：ytm 超出容差。",
  "bundle_dir": "run/A"
}
```

### 维度字段说明

| 字段 | 说明 |
|------|------|
| `name` | 维度中文名（D6 = 外部工具链完整度，只评计算执行轨迹与工具链覆盖度，不评数据来源层级） |
| `description` | 维度评判内容简述 |
| `level` | 0-4 等级，允许 0.5 步长（可能被 CF 封顶） |
| `source` | 等级来源：`layer1+layer2`（第一层实测基线与第二层扣分系数取平均）、`judge_mean`（无第一层基线，纯扣分账本）、`judge_mean_fallback`（本应有客观指标但缺失，回退盲评）、`judge_missing`（盲评缺失）。`objective` 为两层模型之前的旧值，引擎已不再产出，仅在读取历史评分卡时可能遇到 |
| `weight` | 该维度在总分中的权重（来自 `rubric_weights`） |
| `weighted_points` | `fraction × weight`，封顶后重新计算 |
| `needs_review` | 两位评分官分歧超过一级时为 true |
| `capped_by` / `capped_from` | CF 封顶规则名 / 封顶前原始等级 |
| `judge_1` / `judge_2` | 两位评分官各自的 `{level, rationale, deductions, evidence}` |
| `objective_detail` | 客观维度的底层指标与 `failed_checkpoints` 扣分来源 |
| `summary` | 自动生成的人类可读摘要 |

`dimension_summaries` 是 `dimensions` 的精简列表版本，用于快速扫描六个维度的核心结论。

## 统一报告模式（1 份或多份作品，格式一致）

**单份与多份作品使用完全相同的报告格式**——单份作品就是一张只有一行的排名表。由于每份作品都是**绝对地**依据同一套量规和同一份基准真值评分的，排序依然公平。
`scripts/aggregate.py --report` 生成 `report.md`，固定包含以下章节：

1. **总分与排名**——排名 × 作品 × 总分表，含已应用/待确认 CF 与一行结论（有效分母不一致时附归一化分列）
2. **各维度评分总览**——逐维度 × 逐作品的等级表，维度名附带权重（如 `D1 数据完整性与依据（权重40）`），标注来源（客观/盲评）和封顶标记；权重为 0 的维度不在此表显示
3. **各维度详细评分**——对每个权重 > 0 的维度，逐作品列出：等级与得分、客观扣分来源（未通过检查点）、两位评分官的逐条扣分记录（扣多少/严重度/问题/证据）、CF 封顶信息、复核标记；每个维度末尾附**学生答案原文引用**区块，逐条列出该维度扣分点对应的学生答案原文片段（客观维度取自 `normalized.json` 的 `extracted[field].evidence`，主观维度取自盲评扣分账本的 `evidence` 字段），方便用户快速定位答案中的错误位置。权重为 0 的维度折叠为一行（`_本任务该维度权重为 0，不计分。_`），不逐作品展开
4. **确定性检查点明细**——逐检查点 × 逐作品的 通过/未通过（含偏差值）/NA 表
5. **引用核验与致命缺陷**——逐作品的 grounding 值、已应用 CF、待确认 CF
6. **结论**——排序（单份作品为总分陈述）、前两名分差、决定性维度、不确定性说明（当分差 <3 分或决定性维度需复核时标注为暂定）

### 行文要求（适用于报告全文）

除上述章节结构外，报告还须满足一条行文要求：**语句通顺易读，句子信息密度不要过大**。

`aggregate.py` 只能拼接固定模板句，无法自行调节句子密度，因此这一层由生成报告的 Agent 在脚本产物之上完成。具体要求：

- **一句话只讲一件事。** 不要把「客观基线 + 扣分点数 + 权重 + 折算得分 + 结论」压进同一个长句；先给结论，再给依据，必要时拆成多句或短列表。
- **改写模板中的高密度句式。** 例如结论段里把分差、决定性维度与暂定理由串在一起的长句，在面向人阅读的版本中应拆开重写。（两层计分的三行说明与总览表图例已由 `aggregate.py` 逐条拆开输出，无需再改。）
- **零权重维度不逐作品重复展开**（如 D4/D5 权重为 0 时，合并为一句说明即可）。
- **评语语言统一**，不要中英文混排。

> **硬约束：数字、等级、表格与检查点结果是硬数据，只可改写措辞，不可改动任何数值、排名或结论。** 可读性改写只作用于叙述性文字。

### 检查点表完整性要求

**只要本次评测存在确定性检查点，报告就必须包含完整的「确定性检查点明细」表——不得省略、不得抽样、不得只列未通过项、不得用文字概述代替表格。**

- **行 = 全部检查点。** 取所有作品检查点 ID 的并集逐行列出；通过、未通过、NA 一律照列，不因「全部通过」而合并或跳过。
- **列 = 全部作品。** 参与本次评测的每一份作品都必须占一列，不因分数低、排名靠后或表格过宽而省略。
- 未通过项附偏差值 `(Δ=…)`；`NA` 需保留，并在表下注明「不扣分，已从分母剔除」。
- 该表由 `aggregate.py` 自动按上述规则生成（行取并集、列取全部作品），属硬数据。**可读性改写时整表原样保留**，只允许调整表格前后的说明文字。
- 仅当任务确实不含任何确定性检查点时，才以 `_本任务无确定性检查点。_` 代替该表。

## 用法

```bash
# 对单份作品评分
python scripts/aggregate.py --bundle run/A --taskspec run/taskspec.json --out run/A/scorecard.json
# 统一报告（单份或多份均用此模式；--compare 为兼容别名）
python scripts/aggregate.py --report run/A/scorecard.json [run/B/scorecard.json ...] --out run/report.md
```

一个 `--bundle` 目录应包含 `det_results.json`、`citation_audit.json`（如有）、`judge_1.json`、`judge_2.json` 与 `cf_flags.json`。报告模式下，汇总器还会从每个 bundle 目录读取 `normalized.json`（提取器产出）以获取各检查点字段的学生答案原文片段（`evidence`），用于在维度详细评分中展示"学生答案原文引用"区块。
