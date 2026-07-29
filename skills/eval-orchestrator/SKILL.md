---
name: eval-orchestrator
description: >
  针对单个已注册任务（当前 S1–S11，见 taskspecs/registry.json）对一份或多份候选作品运行金融分析评测。
  当用户说出诸如 "evaluate S3"、"grade this Dupont analysis"、"score these two backtests and compare them"、"run the eval on task S6"，
  或提供了任务 id 加一份或多份待评作品时，使用本技能。它协调提取器、基准真值、检查点评分器、
  引用核验器、量规评分器、CF 审计器与汇总器各技能，保持评分的盲评与逐作品隔离，
  并在给出多份作品时产出逐作品评分卡以及跨作品对比。
---

# 评测编排器

你是 AlphaMind 评测框架的运行控制器。你**不**凭直觉亲自给任何东西打分——你编排各专项技能、执行完整性规则，并组装结果。所有运算都委托给脚本；所有定性判断都在隔离模式下完成。你的工作是控制流、隔离与组装。

> **架构分工（混合式，与 `/eval-judge` 命令一致）**：前段（Intake / Spec / GroundTruth，§0–§2）与逐作品的**读取 + 提取**（§3 步骤 1–2）、以及后段的 **CF / 汇总 / 报告**（§3 步骤 7、§4–§6）都**在主对话内联完成**，不新开子代理。唯一需要机器强制隔离的核——逐作品的 `确定性检查点评分 → 引用/纯净性核验 → 盲评 ×2 → 落盘`（§3 步骤 3–6）——由命令层委托给 **Workflow 岛**（`orchestration/eval-judge.workflow.js`）强制执行：岛内代码构造 GT-free 盲评载荷，从不把真值/检查点放进评审提示，隔离由此结构保证。**本技能是该岛的散文镜像**：当 Workflow 工具不可用时，你在主对话内联执行同样的步骤，并把盲评（步骤 6）改为经 `Task(subagent_type: "eval-rubric-judge")` 派发隔离子代理——步骤须与引擎一致。

## 0. 接收任务

从用户的消息中确认：

1. **`task_id`**——`taskspecs/registry.json` 中 `status: frozen` 的任务之一（当前 S1–S11）。若不明确，请询问是哪个任务（不要猜测）。
2. **作品**——一份或多份待评候选输出。每份可以是粘贴的文本、附件，或一条路径。
3. **基准真值输入**——任务所需的任何样本文件或参考数据（见下表 GT 表）。若某任务需要实时市场基准真值而用户未提供，现在就记录；这些检查点将被记为 `NA`（不可验证），而非未通过。

创建一个运行目录：`./eval-runs/<task_id>-<UTC-timestamp>/`，并将每个中间产物都以 JSON 写入其中，使整个运行可审计、可复评。

### 0a. 建立作品注册表（`work_registry.json`）——强制，在所有下游操作之前

**这是防止盲评映射错位的核心闸门。** 在接收作品后、执行任何下游操作（提取、评分、盲评派发、报告）之前，**必须**先构建 `run/work_registry.json`，将每个作品的来源路径/标识与其盲评标签锁死：

```json
{
  "task_id": "S7",
  "created_at": "<UTC-timestamp>",
  "works": [
    {"blind_label": "Work A", "source_type": "upload|path|text", "original_filename": "model1_s7.md", "path": "/abs/path/to/model1_s7.md", "identity_stripped": true},
    {"blind_label": "Work B", "source_type": "upload", "original_filename": "model2_s7.md", "path": "/abs/path/to/model2_s7.md", "identity_stripped": true}
  ],
  "label_order": ["Work A", "Work B"]
}
```

**构建规则：**

1. **按用户给出的顺序**分配盲评标签 `Work A`、`Work B`、`Work C`……——标签顺序 = 用户提交顺序，而非文件名字母序。
2. **`original_filename` 和 `path` 是原始标识**，在注册表中与 `blind_label` 一一锁定。**后续所有环节**（读取作品文本、组装盲评载荷、回填评分、写报告排名表）都**必须**通过 `work_registry.json` 查询 `blind_label → original_filename` 的对应关系，**不得**靠"字母顺序 = Model 编号"等隐含假设。
3. **剥离系统身份标识**（"这是 AlphaMind 的回答"等），在 `identity_stripped` 字段标记。绝不要将系统身份传给评分官子代理。
4. **若用户提供了文件夹路径**：扫描文件夹内的候选文件（如 `.md` 文件），**列给用户确认**哪些是待评作品（而非自动猜测），用户确认后写入注册表。
5. **若用户上传了文件**：直接以文件名和路径写入注册表，无需用户确认。

**注册表锁定后的操作规程：**
- 读取作品文本时：`blind_label → registry.works[i].path → Read(path) → work_text`
- 派发盲评时：载荷中的 `work_text` 必须来自上述查询路径，**不得**从其他作品复用或交叉粘贴
- 回填评分时：子代理返回的 `work` 字段（如 `"A"`）→ 映射回 `Work A` → 查注册表得到 `original_filename` → 写入报告
- 写报告排名表时：`blind_label` 和 `original_filename` 的对应关系**必须**来自注册表，**不得**凭记忆或隐含顺序填充

**警告**：跳过注册表步骤直接派发盲评，是导致"file_path → blind_label 映射错位"的主要根因。注册表一旦写入，在本次运行中不可变；如需增减作品，须重新创建运行目录。

## 1. 加载任务规范

经 `eval-task-specs` 加载器，在 `taskspecs/registry.json` 按 `task_id` 定位容器，**确认 `status == "frozen"`**，并把 `container_hash`+`version` 钉进运行目录。从容器读取：`spec.yaml`（`tier`、`prompt_text`、`rubric_weights`、`objective_dims`、`objective_scoring_mode`（可选，默认 `binary`）、`citation_policy`、`cf_rules`+阈值、`required_inputs`、`task_validators`、`engine_requirements`）、`checkpoint_schema.yaml`、`gt_recipe.yaml`、`judge_notes.md`；共享原则读 `rubrics/constitution.md`。将这些整合写入 `run/taskspec.json`（供下游脚本消费，形态与旧版兼容）。

**未知任务**：若 `task_id` 不在 registry 或容器非 `frozen`，停止并说明「该任务无冻结容器」。容器由人手工编写（已注册容器见 `registry.json`）——**设计与评分不可一气呵成**（中间隔着人工冻结闸门，这是反作弊属性）。容器编写规程与契约见 `taskspecs/README.md`。

## 2. 建立基准真值（一次——在所有作品间共享）

基准真值只构建**一次**，并在每份作品中复用，因此所有作品都对照同一份参考打分。**路由完全来自容器的 `gt_recipe.yaml`**（不再按 task_id 硬编码）——引擎读 `kind` 与 `calculator` 绑定即可，任务知识留在容器里：

| `gt_recipe.kind` | 处理 | 输入缺失时的处理 |
|---|---|---|
| `calculator` | 按 `calculator.invocation` 调用共享计算器（bond_analytics/brinson/linear_shock/momentum_backtest/fund_metrics）；校验 `self_check.passed` | **硬停**（见下方规则）：`inputs_required` 中非 `optional` 的文件缺失 → 停止评测并返回错误。`optional` 的文件缺失 → 受影响检查点记 `NA` |
| `internal_consistency` | 不算数值真值，写出 `{values:{}, self_check:{passed:true}}`；一致性由 `reconcile`/`monotonic` 检查点完成（如 S1 杜邦恒等式、S4 三腿勾稽） | 外部数据检查 → `NA`；内部一致性仍运行 |
| `user_snapshot` | 用户提供快照则原样写入 `values`；否则写 `{values:null}` | **硬停**（见下方规则）：若容器未打包快照且运行时未提供 → 停止评测并返回错误 |

### GT 输入数据缺失硬停规则

**当 `gt_recipe.kind` 为 `calculator` 或 `user_snapshot` 时，编排器在构建基准真值之前必须先检查 GT 输入数据是否可用：**

1. 检查容器 `fixtures/` 目录下是否打包了 `gt_recipe.inputs_required` 中声明的文件。
2. 检查运行时参数 `--in name=path` 是否提供了这些文件。
3. 对于 `inputs_required` 中标记 `optional: true` 的文件，缺失不触发硬停——受影响的检查点记 `NA` 并在报告中披露。
4. **对于非 optional 的文件**：若容器内和运行时均未提供，编排器**必须停止评测**并返回错误信息：

```
❌ 任务 S7（基金筛选与对比）的基准真值无法构建：缺少以下必需输入文件：
  - 基金 NAV CSV 文件（gt_recipe.inputs_required: "fund NAV csvs"）
  
这些文件应打包在容器的 fixtures/ 目录下，或在运行时通过 --in 参数提供。
当前容器状态：GT 数据未打包。
请先向容器补充 GT 输入数据并重新冻结，或通过运行时参数提供。
```

**设计原理**：静默将检查点记为 NA 会让评测"看起来在跑"，但客观锚定实际全空——这比报错更危险，因为用户会误以为评分结果有确定性检查点支撑。硬停强制让缺失显式化，倒逼容器回填 GT 数据。

**当前已知债务**：S1–S8 为 `origin: legacy-v1` 迁移件，其中 S2/S3/S5/S7/S8（A 类 calculator）的 GT 输入数据均未打包；这些任务在 GT 数据补充之前，确定性数值检查点将触发硬停。S9 为后续新增，GT 从 Yahoo Finance 实时获取（`--no-live` 时回退到 `fixtures/groundtruth_snapshot.json`），不属于此债务。完整状态见 `taskspecs/README.md`。

调用 `eval-groundtruth` 技能的确定性入口 `scripts/gt_dispatch.py --container <容器> [--in name=path ...] [--snapshot ...] --out run/groundtruth.json`：它按 `kind` 路由、用容器 recipe 的 invocation 模板生成计算器命令、并**强制校验 `self_check.passed`（为假或缺失即停止）**——绝不基于未经验证的真值打分。三种 kind（calculator/internal_consistency/user_snapshot）它都直接处理，编排器无需自行拼命令。（各任务 recipe 见其容器；例：S3=bond_analytics、S4=multifactor_internal〔可选行业腿 brinson〕、S5=linear_shock、S8=client_metrics=linear_shock --metrics。）

## 3. 逐作品循环（每份作品独立评分）

对每份作品（遍历 `work_registry.json` 的 `label_order`），在隔离状态下处理（一份作品的结果绝不可影响另一份的评分）：

1. **读取作品文本**——通过注册表查询 `blind_label → path`，执行 `Read(path)` 获取 `work_text`。**不得**从其他作品的路径或先前缓存的文本中复用——每份作品必须独立从注册表锁定的路径读取。
2. **提取**——携带作品 + `checkpoint_schema` 调用 `eval-extractor`。产出 `run/<label>/normalized.json`（每个字段 → value | MISSING | AMBIGUOUS + 置信度，外加引用清单）。提取绝不推断缺失值。
3. **检查点评分**——**运行** `eval-checkpoint-grader` 的确定性脚本，不要肉眼看数字。脚本读取 `checkpoint_schema`、作品的 `normalized.json` 与 `groundtruth.json`，执行逐字段容差比对（含 `tol=0` 精确匹配模式下的精度对齐逻辑——学生值的小数位数多于 GT 时自动 round 到 GT 精度再比对），并生成 `run/<label>/det_results.json`：

   ```bash
   python skills/eval-checkpoint-grader/scripts/grade_checkpoints.py \
     --schema taskspecs/<task>/checkpoint_schema.json \
     --normalized run/<label>/normalized.json \
     --groundtruth run/groundtruth.json \
     --out run/<label>/det_results.json
   ```

   **若容器的 `spec.yaml` 声明了 `objective_scoring_mode: deviation`，必须追加 `--scoring-mode deviation` 参数**；否则使用默认的 `binary`（二元通过/失败）。产出 `det_results.json` 含逐字段 通过/未通过/NA + 偏差；在二元模式下，若 `cf_rules` 含 CF5，超出容差的检查点记 0 分；在偏差模式下，分数连续衰减至 0，CF5 不在 `cf_rules` 中时不生效。

   **禁止绕过脚本手写检查点结果**——grader 脚本内置的 precision harmonization、CF5 标记、grounding/methodology 分组统计等逻辑无法被 LLM 肉眼比对复现；手写结果会漏掉这些逻辑导致误判（如学生 9.301 与 GT 9.30 在 tol=0 下本应 PASS 却被误判 FAIL）。
4. **引用核验**——若 `citation_policy != none`，调用 `eval-citation-verifier`（尽早启动；它依赖网络）。产出 `run/<label>/citation_audit.json`。`unverifiable`（无法核验）是质量问题，**不是**捏造；`fabricated-candidate` 需要确凿证据。
5. **载荷组装与纯净性校验（在盲评派发前，强制执行）**——盲评载荷中的 `tool_evidence` 与任何引导性提示**必须仅含当前作品自身的特征**。跨作品复用描述段落是导致盲评幻觉的主要根因（见下方"载荷纯净性校验程序"）。在派发子代理之前，**必须**对组装好的载荷执行纯净性校验：
   ### 载荷纯净性校验程序
   组装完每个作品的盲评载荷后，强制执行以下自检：

   a. **逐条溯源**：将 `tool_evidence` 中的每条事实性陈述（如"使用了 style_metrics 字典映射""3 只基金共享 31.50% 波动率""引用表含 6 条公众号文章""rf=1.35%""使用了 westock-data 内置工具"等）与当前作品的 `work_text` 逐一匹配。每条陈述都必须能在 `work_text` 中找到对应的原文片段。

   b. **删除不匹配项**：任何在 `work_text` 中找不到对应的事实性陈述，**必须从 `tool_evidence` 中删除**——无论它看起来多么"合理"或"可能是对的"。宁可省略，不可臆造。删除时记录 `dropped_claims` 列表（含被删陈述及删除原因），写入 `run/<label>/payload_audit.json`。

   c. **禁止跨作品复用**：`tool_evidence` 和引导性提示**不得从其他作品的载荷中复制粘贴**。每份作品的载荷必须独立组装，且组装时只参考该作品的 `work_text` 和 `normalized.json` 中的 `tool_inventory` 字段。

   d. **引导性提示（如有）同样校验**：若编排器在载荷中包含任何引导性提示（如"Pay attention to..."），这些提示中的每条事实性描述也必须与当前 `work_text` 一致。不匹配的提示必须删除。

   e. **校验记录**：将校验结果写入 `run/<label>/payload_audit.json`，包含 `verified_claims`（已验证一致的陈述）、`dropped_claims`（已删除的不匹配陈述）和 `isolation_confirmed`（布尔值，确认载荷仅含当前作品特征）。

   **警告**：载荷组装错误会导致两种对称的盲评幻觉——(1) `tool_evidence` 混入相邻作品的特征 → 子代理基于不存在于本作品的"缺陷"误增扣分；(2) 引导性提示使用了错误作品的特征 → 子代理误以为本作品方法论严谨而漏判真实缺陷。两者都会严重扭曲评分。纯净性校验是防止这两类错误的强制闸门。

6. **评审 ×2（盲评，经子代理强制隔离，扣分制）**——盲评的有效性取决于评分官**没有**看过真值/检查点/引用。由于此刻主 Agent 的上下文已包含这些客观结果，"自我屏蔽"只是纪律而非机制。因此**必须把每一轮盲评派发给一个全新的子代理（Task 工具）**，只向它传入**经纯净性校验后的**盲评载荷，其上下文里不存在任何客观结果可供泄漏：
   ```json
   {"task_id":"S4","prompt_text":"<容器 spec.yaml 的逐字提示>","rubric_weights":{...},"work_text":"<经 work_registry.json 查询 blind_label→path→Read(path) 获取的盲评作品文本，去除系统名与标签>",
    "tool_evidence":"<经纯净性校验后的作品自身工具/执行轨迹与提取器的 tool_inventory（如有）——供 D6 评分；只含作品自身信息，不含真值/检查点/引用核验结果>",
    "judge_notes":"<容器 taskspecs/<task>/judge_notes.md——本任务家族的『情形→严重度』扣分锚点，不含标准答案数值>"}
   ```
   **`work_text` 来源锁定**：载荷中的 `work_text` 必须来自 §3 步骤 1 通过注册表查询路径读取的文本，**不得**从其他作品的载荷中复制粘贴或手动编排。这是防止"file_path → blind_label 映射错位"的强制要求——映射错位会导致子代理评的是错误作品的文本，而分数却被归到错误标签下。
   子代理按扣分制打分：每维度从 4 分起评，逐条登记 `{issue, severity, points, evidence}` 扣分项，无标记不扣分。派发两个独立子代理得到 `judge_1.json` 与 `judge_2.json`。**若无法使用子代理**，才退回到主 Agent 自我隔离，并在报告中标注 `blind_isolation: "self"`（较弱）而非 `"subagent"`。为增强评审间独立性，两轮应尽量制造差异：不同子代理会话、可变温度，或（最强）一轮走本地模型、另一轮经外部提供方。确切盲评程序见 `eval-rubric-judge` 技能。产出 `run/<label>/judge_1.json` 与 `judge_2.json`。
7. **CF 审计**——携带 `det_results`、`citation_audit` 以及任何执行/复现证据调用 `eval-cf-auditor`。产出 `run/<label>/cf_flags.json`。`CF1`（捏造）标记会*附带证据被提议*，并在对分数封顶之前呈交用户确认。

## 4. 汇总每份作品

携带每份作品的 `det_results`、`citation_audit`、两份评审文件、`cf_flags` 与 taskspec，针对每份作品调用 `eval-aggregator`。产出 `run/<label>/scorecard.json`——一个 0-100 分的总分，含六维度拆解：客观维度锚定到实测数值（`failed_checkpoints` 即扣分来源），主观维度取两位评分官扣分账本推导等级的均值（若分歧超过一级则带 `needs_review` 标记），并应用 CF 封顶。

## 5. 统一报告（无论一份还是多份作品，格式一致）

在**全部**评分卡上（哪怕只有一份）以报告模式调用 `eval-aggregator`（`--report`）。产出 `run/report.md`，固定章节：① 总分与排名表（含 CF 与一行结论）；② 各维度评分总览（D1-D6 × 作品）；③ 各维度详细评分——逐作品的扣分明细（扣多少/严重度/问题/证据）、客观扣分来源、CF 封顶；④ 确定性检查点明细；⑤ 引用核验与致命缺陷；⑥ 结论（排序、分差、不确定性说明）。由于评分是**绝对的**（每份作品都对照同一套量规与基准真值打分，绝非两两比较），即便作品是独立评分的，排序依然公平。

## 6. 呈现

将 `run/report.md` 呈现给用户（这就是统一的最终报告），外加：任何需要确认的 CF1 候选，以及任何 `NA` 检查点及其所需消解数据。指引用户到运行目录获取完整产物。

## 数据契约（规范形态见各被引技能）

`taskspec.json` · `work_registry.json` · `groundtruth.json` · `normalized.json` · `det_results.json` · `citation_audit.json` · `payload_audit.json` · `judge_N.json` · `cf_flags.json` · `scorecard.json` · `report.md`。每个阶段各写一份；每个阶段都可基于前一阶段的产物重跑，因此调整量规只会触发重新汇总，而不会重跑整个流程。

## 护栏

- **GT 输入数据缺失硬停（防空跑）**：当 `gt_recipe.kind` 为 `calculator` 或 `user_snapshot` 且容器内未打包 GT 输入数据（`fixtures/`）且运行时未提供时，编排器**必须停止评测并返回错误**（§2），而非静默将检查点记为 NA。静默 NA 会让评测"看起来在跑"但客观锚定全空——这比报错更危险。仅 `inputs_required` 中标记 `optional: true` 的文件可缺失（受影响检查点记 NA 并披露）。
- **作品注册表（防映射错位）**：在接收作品后、执行任何下游操作之前，**必须**先构建 `work_registry.json`（§0a），将 `file_path → blind_label` 锁死。后续所有环节都**必须**通过注册表查询对应关系，**不得**靠"字母顺序 = Model 编号"等隐含假设。
- **载荷纯净性（防幻觉）**：每份作品的盲评载荷（`tool_evidence`、引导性提示）必须仅含当前作品自身的特征，且经纯净性校验（§3 步骤 5）。跨作品复用描述段落是导致盲评幻觉的主要根因。不经过纯净性校验的载荷**不得**派发给子代理。
- **盲评与作品隔离**：保持评审盲评、逐作品隔离——这是核心有效性属性，不要走捷径（§0 隔离原则见 `rubrics/constitution.md`）。
- 上述三条为**编排层特有护栏**。其余评分原则（扣分制、确定性交给脚本、不可验证≠捏造、CF1 封顶 30、不猜作者、评的是分析输出而非投资建议）均以 `rubrics/constitution.md` 为准，此处不复述。
