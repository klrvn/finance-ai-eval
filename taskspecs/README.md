# 任务规范容器（Task Spec Containers）— 第 3 层

本目录是评测框架的**第 3 层（容器层）**。每个任务是一个**自包含的文件夹**，装着该任务被评测所需的全部规范；
引擎层（第 2 层）读取容器来运行，但引擎代码里**不含任何任务知识**。这样新增任务 = 新增一个文件夹，
不改框架。

哲学：**把「任务规范」从「评分流程」里分离出来**。规范是数据（容器），流程是引擎（skills）。

---

## 分层关系

```
第1层 宪法   rubrics/constitution.md            ← 不可变原则：D1-6、扣分制、CF1-5、盲评隔离
第2层 引擎   skills/eval-*                       ← 任务无关的流程引擎，经契约读取容器
第3层 容器   taskspecs/<task>/                  ← 每任务一个文件夹（本目录）
```

> 不变量见 `rubrics/constitution.md` §0，可参数化面见 §1，完整设计见 `docs/three-layer-architecture.md`——此处不复述。已注册容器（当前 S1–S11）以 `taskspecs/registry.json` 为准。

## 容器清单（每个文件、消费者、约束）

| 文件 | 必需 | 消费引擎 | 内容 / 约束 |
|---|---|---|---|
| `spec.yaml` | 是 | orchestrator / aggregator / judge | `task_id`、`version`、`family`、`tier`、`persona`、`prompt_text`（逐字）、`measurable_outputs`、`rubric_weights`、`objective_dims`、`citation_policy`、`cf_rules`(+阈值)、`required_inputs`(+NA 政策)、`task_validators`(+`realized_by`)、`engine_requirements` |
| `checkpoint_schema.yaml` | 是 | extractor / grader / aggregator | 字段类型只能取自引擎的封闭类型表（见 §类型表）；`tol`/`rel`/`target`；`grounding`/`methodology` 标记；可选 `validator:` 派发插件；可选 `na:` 段声明条件性 NA 检查点 |
| `gt_recipe.yaml` | T1/T2 需要 | groundtruth 调度器 | `kind: calculator | internal_consistency | user_snapshot`；calculator 绑定到共享计算器 + 参数；声明 `inputs_required` 与缺失时的 NA 政策 |
| `judge_notes.md` | 是 | rubric-judge（进入盲评载荷） | 逐维度「情形 → 既有严重度」扣分锚点；D1 段声明本家族的第一/二/三级来源界定；D6 段声明本家族的关键计算步骤界定。**禁止出现期望数值答案、权重、新严重度** |
| `extraction_notes.md` | 可选 | extractor | 格式/单位约定、结构化载荷形态；纯散文，不得改 schema |
| `fixtures/` | 若自包含 | groundtruth / validators | 输入数据文件；哈希记入 provenance |
| `validators/` | 可选 | grader / cf-auditor | 插件（遵循验证器契约，自带自测）；每个声明 `feeds:`（喂哪个维度/CF） |
| `provenance.json` | 是 | 治理 | `origin`、作者、方法学说明、引擎版本、评审状态、`container_hash` |

## GT 输入数据规则

### 基本原则

每个任务的容器应当**自包含**——即评测所需的 GT 输入数据应打包在容器内，而不是每次运行时由用户提供。这保证了：

1. **可复现性**：同一份容器对任何作品产出相同的 GT，评分可复现。
2. **公平性**：所有作品对照同一份 GT 打分，不因运行时数据差异而偏差。
3. **可审计性**：GT 输入文件的哈希记入 `provenance.json`，运行时可追溯。

### 三类任务的 GT 需求分类

| 分类 | gt_recipe.kind | 需要 GT 输入数据？ | 典型任务 |
|:---|:---|:---|:---|
| **A 类（确定性）** | `calculator` | ✅ 是——计算器需要的输入文件（CSV/JSON）必须打包在 `fixtures/`；S9 例外，见下注 | S2（prices/benchmark CSV）、S3（bond_params.json）、S5（portfolio/shock）、S7（NAV CSV）、S8（client portfolio）、S9（数据获取，live-fetch + fixtures 回退） |
| **B 类（内部一致性）** | `internal_consistency` | ❌ 否——无需外部数据，恒等式自洽由 reconcile/consistency 检查点完成 | S1（杜邦恒等式）、S4（三腿勾稽） |
| **C 类（研判型）** | `user_snapshot` | ⚠️ 可选——用户提供的宏观快照可打包，也可运行时提供 | S6（宏观快照） |

> **B 类豁免说明**：`internal_consistency` 类型的任务不需要 GT 输入数据（因为不产出数值真值，靠恒等式自洽检查点判定）。
>
> **C 类灵活说明**：`user_snapshot` 类型的任务可以将快照数据打包在容器内（推荐），也可接受运行时提供。但一旦打包，容器内快照即为唯一 GT 来源。
>
> **A 类 live-fetch 例外（S9）**：S9 的 calculator 默认从 Yahoo Finance **实时获取**最新数据构建 GT（`--no-live` 时回退到 `fixtures/groundtruth_snapshot.json`），因此不依赖运行时输入、也不属于"GT 数据需回填"的债务。其 `inputs_required` 中的 snapshot 标记为可选（legacy fallback）。详见 `taskspecs/S9-data-retrieval-stability/gt_recipe.yaml`。

### GT 输入数据打包规则

1. **A 类任务**：计算器所需的所有 `inputs_required` 文件**必须**打包在容器的 `fixtures/` 目录下。文件清单和哈希记入 `provenance.json`。
2. **B 类任务**：无需 `fixtures/` 中的 GT 输入文件。`gt_recipe.groundtruth_output.values` 为空 `{}` 是正常的。
3. **C 类任务**：用户快照**应当**打包在 `fixtures/` 下；若任务设计允许运行时提供，必须在 `gt_recipe.inputs_required` 中声明 `na_policy` 且编排器应优先使用容器内打包的快照。

### 运行时 GT 缺失的硬停规则

**当编排器遇到一个 `frozen` 容器，且该容器的 `gt_recipe.kind` 为 `calculator` 或 `user_snapshot`，但容器内未打包 GT 输入数据且运行时也未提供时——编排器必须停止评测并返回错误**，而非静默将检查点记为 NA。

错误信息应包含：
1. 缺失的输入文件列表（来自 `gt_recipe.inputs_required`）。
2. 这些文件应放在容器的哪个目录（`fixtures/`）。
3. 用户可选择的补救方式（向容器补充数据并重新冻结，或在运行时通过参数提供）。

**例外**：`gt_recipe.inputs_required` 中标记了 `optional: true` 的输入可缺失，不触发硬停（如 S5 的 `scenario_returns_2022` 缺失只导致 `historical_replay_pnl` 记 NA）。

### 当前已注册容器的 GT 数据状态（完整清单以 `registry.json` 为准）

| 任务 | 类别 | GT 输入数据 | 状态 |
|:---|:---|:---|:---|
| S1 杜邦 | B | ❌ 不需要 | GT 豁免 |
| S2 回测 | A | ❌ 未打包（prices/benchmark CSV 缺失） | GT 数据需回填 |
| S3 债券 | A | ❌ 未打包（bond_params.json 缺失） | GT 数据需回填 |
| S4 归因 | B | ❌ 不需要 | GT 豁免 |
| S5 压测 | A | ❌ 未打包（portfolio/shock 缺失） | GT 数据需回填 |
| S6 宏观 | C | ❌ 未打包（macro_snapshot 缺失，设计允许运行时提供） | GT 数据（可选）需回填 |
| S7 基金 | A | ❌ 未打包（NAV CSV 缺失） | GT 数据需回填 |
| S8 组合 | A | ❌ 未打包（client portfolio 缺失） | GT 数据需回填 |
| S9 数据获取 | A（live-fetch） | ✅ live-fetch（Yahoo）+ `fixtures/groundtruth_snapshot.json` 回退 | GT 可用（不属回填债务） |

> **债务说明**：S1–S8 为 `origin: legacy-v1` 迁移件，A 类任务（S2/S3/S5/S7/S8）的 GT 输入数据尚未打包。这是已知技术债务——容器规范结构完整，但 GT 数据尚未回填。回填后应将 `provenance.json` 中标注 `gt_data: packed`。S9 为后续新增的 live-fetch 任务，GT 实时构建、不在此债务内。

## tier 相干性约束（由 `eval-taskspec-lint` 强制；与宪法 §5 一致）

全 tier：权重整数合计 100；`objective_dims ⊆ {D1,D2}`；D6 权重 ≥ 5；每个非内部一致性的数值型检查点都要有真值路径（禁止伪确定性）。

- **T1｜确定性**：`objective_dims == [D1,D2]`；`gt_recipe.kind == calculator` 且为数值计算器；承重数值检查点均有真值。（S2/S3/S4）
- **T2｜半确定性**：`len(objective_dims) ≤ 1`；至少一条可核验路径——可执行计算器 **或** ≥1 个 `reconcile/consistency/monotonic` 检查点 **或** ≥1 个 `sample_verify`。（S1/S5/S7）
- **T3｜研判型**：`len(objective_dims) ≤ 1`；非客观权重（`100 − Σ objective_dims 权重`）≥ 40，研判/引用主导。（S6/S8）

> 这三条 tier 规则是从 S1-S8 反推、并保证 8 个参考容器全部通过的。设计初稿更严的版本（如「T2 必须有内部一致性检查点」「T3 必须有引用」「T3 D4+D5≥40」）与 S5/S7/S6/S8 冲突，已在迁移自检中放宽（见 `docs/three-layer-architecture.md` §7）。

## 类型表（checkpoint_schema 只能用这些类型）

标量 `number` `pct` `bp` `yr` `ratio` · 基数 `count_eq` `count_min` `sum_to` `present` · 结构 `vector` `set_match` · 内部 `reconcile` `consistency` `monotonic` · 抽样 `sample_verify`。
新增类型是**引擎发布**（带测试），绝不做逐任务分叉。定义见 `skills/eval-checkpoint-grader/scripts/grade_checkpoints.py`。

## 容器如何产生（手工编写 + 冻结闸门）

容器由人**手工**编写（已注册容器见 `registry.json`；S1–S8 为 `legacy-v1` 迁移件，S9 及之后为新增件）。编写者**永不**自算真值、**永不**看候选作品、**永不**改宪法或引擎；起草后经自检（spec-lint + 真值自检）并提交人工冻结。

编排器在遇到未知 `task_id` 时的行为：查 `registry.json`；若不存在，提示「该任务无容器——需先编写并冻结容器」。**设计与评分不可一气呵成**（冻结闸门在中间），这是反作弊属性，不是限制。
