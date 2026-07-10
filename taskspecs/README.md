# 任务规范容器（Task Spec Containers）— 第 2 层

本目录是评测框架的**第 2 层（容器层）**。每个任务是一个**自包含的文件夹**，装着该任务被评测所需的全部规范；
流程层引擎（第 3 层）读取容器来运行，但引擎代码里**不含任何任务知识**。这样新增任务 = 新增一个文件夹，
不改框架。

哲学：**把「任务规范」从「评分流程」里分离出来**。规范是数据（容器），流程是引擎（skills）。

---

## 分层关系

```
第1层 宪法   rubrics/constitution.md            ← 不可变原则：D1-6、扣分制、CF1-5、盲评隔离
第2层 容器   taskspecs/<task>/                  ← 每任务一个文件夹（本目录）
第3层 引擎   skills/eval-*                       ← 任务无关的流程引擎，经契约读取容器
作者   eval-task-designer（RAG 子代理，尚未构建）← 未来自动生成容器；见下「作者槽位」
```

## 容器清单（每个文件、消费者、约束）

| 文件 | 必需 | 消费引擎 | 内容 / 约束 |
|---|---|---|---|
| `spec.yaml` | 是 | orchestrator / aggregator / judge | `task_id`、`version`、`family`、`tier`、`persona`、`prompt_text`（逐字）、`measurable_outputs`、`rubric_weights`、`objective_dims`、`citation_policy`、`cf_rules`(+阈值)、`required_inputs`(+NA 政策)、`task_validators`(+`realized_by`)、`engine_requirements` |
| `checkpoint_schema.yaml` | 是 | extractor / grader / aggregator | 字段类型只能取自引擎的封闭类型表（见 §类型表）；`tol`/`rel`/`target`；`grounding`/`methodology` 标记；可选 `validator:` 派发插件；可选 `na:` 段声明条件性 NA 检查点 |
| `gt_recipe.yaml` | T1/T2 需要 | groundtruth 调度器 | `kind: calculator | internal_consistency | user_snapshot`；calculator 绑定到共享计算器 + 参数；声明 `inputs_required` 与缺失时的 NA 政策 |
| `judge_notes.md` | 是 | rubric-judge（进入盲评载荷） | 逐维度「情形 → 既有严重度」扣分锚点；D6 段声明本家族的 A/B 层来源期望。**禁止出现期望数值答案、权重、新严重度** |
| `extraction_notes.md` | 可选 | extractor | 格式/单位约定、结构化载荷形态；纯散文，不得改 schema |
| `fixtures/` | 若自包含 | groundtruth / validators | 输入数据文件；哈希记入 provenance |
| `validators/` | 可选 | grader / cf-auditor | 插件（遵循验证器契约，自带自测）；每个声明 `feeds:`（喂哪个维度/CF） |
| `golden/` | 见下 | 作者 dry-run / 回归 CI | golden 干净件 + 缺陷件 + `expected_results.json`；缺陷件证明检查点真能抓错（防容差过松） |
| `provenance.json` | 是 | 治理 | `origin`、KB 卡片引用 + 版本、方法学选择的检索证据、作者、引擎版本、评审状态、`container_hash` |

## 生命周期与冻结闸门

`draft` → （spec-lint + 真值自检 + golden dry-run 全绿）→ `review` → 人工批准 → **`frozen`** → 可选 `deprecated`。

- **编排器只评 `frozen` 容器**，并把 `container_hash` 钉进每次运行目录。
- 任何修改产生**新版本**；分数只在同一容器版本内可比（报告披露）。
- **反作弊硬规则**：容器必须在接收任何该任务的候选作品**之前**冻结。作者（子代理）永不接触候选作品。这杜绝了「规范被按某份作品定制」的循环。

`golden/` 要求：`origin: legacy-v1`（S1-S8 迁移件）暂以 `golden: pending` 宽限，lint 出**警告**不报错；
`origin: kb`（未来由作者子代理生成）**必须** golden 全绿方可冻结。

## tier 相干性约束（由 `eval-taskspec-lint` 强制；与宪法 §5 一致）

全 tier：权重整数合计 100；`objective_dims ⊆ {D1,D2}`；D6 权重 ≥ 5；每个非内部一致性的数值型检查点都要有真值路径（禁止伪确定性）。

- **T1｜确定性**：`objective_dims == [D1,D2]`；`gt_recipe.kind == calculator` 且为数值计算器；承重数值检查点均有真值。（S2/S3/S4）
- **T2｜半确定性**：`len(objective_dims) ≤ 1`；至少一条可核验路径——可执行计算器 **或** ≥1 个 `reconcile/consistency/monotonic` 检查点 **或** ≥1 个 `sample_verify`。（S1/S5/S7）
- **T3｜研判型**：`len(objective_dims) ≤ 1`；非客观权重（`100 − Σ objective_dims 权重`）≥ 40，研判/引用主导。（S6/S8）

> 这三条 tier 规则是从 S1-S8 反推、并保证 8 个参考容器全部通过的。设计初稿更严的版本（如「T2 必须有内部一致性检查点」「T3 必须有引用」「T3 D4+D5≥40」）与 S5/S7/S6/S8 冲突，已在迁移自检中放宽（见 `docs/two-layer-architecture.md` §7）。

## 类型表（checkpoint_schema 只能用这些类型）

标量 `number` `pct` `bp` `yr` `ratio` · 基数 `count_eq` `count_min` `sum_to` `present` · 结构 `vector` `set_match` · 内部 `reconcile` `consistency` `monotonic` · 抽样 `sample_verify`。
新增类型是**引擎发布**（带测试），绝不做逐任务分叉。定义见 `skills/eval-checkpoint-grader/scripts/grade_checkpoints.py`。

## 作者槽位：`eval-task-designer`（RAG 子代理，尚未构建）

未来由一个 **RAG 知识库支撑的子代理** 自动生成容器：检索方法学卡片 → 判定 tier → 起草容器（每个方法学选择都引用卡片来源）→ 自检（spec-lint + 真值自检 + golden dry-run）→ 提交人工冻结。它**永不**自算真值、**永不**看候选作品、**永不**改宪法或引擎。

**当前（迁移后）**：容器由人**手工**编写（S1-S8 即手工迁移件，作为未来子代理的 few-shot 范例）。作者子代理设计见 `docs/two-layer-architecture.md` §3，届时插入本槽位即可，流程层与容器契约无需改动。

编排器在遇到未知 `task_id` 时的行为：查 `registry.json`；若不存在，提示「该任务无容器——需先编写并冻结容器」（当前手工；未来一键调用 `eval-task-designer`）。**设计与评分不可一气呵成**（冻结闸门在中间），这是反作弊属性，不是限制。
