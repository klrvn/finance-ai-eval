---
name: eval-orchestrator
description: >
  针对单个任务（S1-S8）对一份或多份候选作品（来自 AlphaMind 或竞品的输出）运行 AlphaMind 金融分析评测。
  当用户说出诸如 "evaluate S3"、"grade this Dupont analysis"、"score these two backtests and compare them"、"run the eval on task S6"，
  或提供了任务 id 加一份或多份待评作品时，使用本技能。它协调提取器、基准真值、检查点评分器、
  引用核验器、量规评分器、CF 审计器与汇总器各技能，保持评分的盲评与逐作品隔离，
  并在给出多份作品时产出逐作品评分卡以及跨作品对比。
---

# 评测编排器

你是 AlphaMind 评测框架的运行控制器。你**不**凭直觉亲自给任何东西打分——你编排各专项技能、执行完整性规则，并组装结果。所有运算都委托给脚本；所有定性判断都通过 Task 工具派发给隔离的 `eval-rubric-judge` 子代理完成（见其盲评程序）。你的工作是控制流、隔离与组装。

## 0. 接收任务

从用户的消息中确认：

1. **`task_id`**——S1-S8 之一。若不明确，请询问是哪个任务（不要猜测）。
2. **作品**——一份或多份待评候选输出。每份可以是粘贴的文本、附件，或一条路径。按给出顺序为每份分配一个不透明的盲评标签：`Work A`、`Work B`、`Work C`……
3. **基准真值输入**——任务所需的任何样本文件或参考数据（见下表 GT 表）。若某任务需要实时市场基准真值而用户未提供，现在就记录；这些检查点将被记为 `NA`（不可验证），而非未通过。
4. **盲评**——剥离或忽略任何系统身份标识（"这是 AlphaMind 的回答"）。绝不要将系统身份传给评分官子代理。在下游所有环节只用盲评标签指代作品。

在**用户当前目录**下创建一个运行目录：`./eval-runs/<task_id>-<UTC-timestamp>/`，并将每个中间产物都以 JSON 写入其中，使整个运行可审计、可复评。（产物写用户目录，参考数据读插件目录——见下步。）

## 1. 加载任务规范

**先解析插件根**（Claude Code 插件下工作目录是用户项目，参考数据在插件目录）：按 `eval-task-specs` 加载器第 0 步，用 Bash `echo "$CLAUDE_PLUGIN_ROOT"` 取得绝对根路径 `<ROOT>`，此后所有宪法/容器一律用 `<ROOT>/…` 绝对路径 Read（Read 工具不展开环境变量）。

经 `eval-task-specs` 加载器，在 `<ROOT>/taskspecs/registry.json` 按 `task_id` 定位容器，**确认 `status == "frozen"`**，并把 `container_hash`+`version` 钉进运行目录。从容器读取：`spec.yaml`（`tier`、`prompt_text`、`rubric_weights`、`objective_dims`、`citation_policy`、`cf_rules`+阈值、`required_inputs`、`task_validators`、`engine_requirements`）、`checkpoint_schema.yaml`、`gt_recipe.yaml`、`judge_notes.md`；共享原则读 `<ROOT>/rubrics/constitution.md`。将这些整合写入 `run/taskspec.json`（供下游脚本消费，形态与旧版兼容）。

**未知任务**：若 `task_id` 不在 registry 或容器非 `frozen`，停止并说明「该任务无冻结容器」。当前容器由人手工编写（S1-S8 即迁移件）；未来由 `eval-task-designer` 子代理生成——**设计与评分不可一气呵成**（中间隔着人工冻结闸门，这是反作弊属性）。作者子代理的位置见 `taskspecs/README.md`。

## 2. 建立基准真值（一次——在所有作品间共享）

基准真值只构建**一次**，并在每份作品中复用，因此所有作品都对照同一份参考打分。**路由完全来自容器的 `gt_recipe.yaml`**（不再按 task_id 硬编码）——引擎读 `kind` 与 `calculator` 绑定即可，任务知识留在容器里：

| `gt_recipe.kind` | 处理 | 输入缺失时（容器 `na_policy` 声明） |
|---|---|---|
| `calculator` | 按 `calculator.invocation` 调用共享计算器（bond_analytics/brinson/linear_shock/momentum_backtest/fund_metrics）；校验 `self_check.passed` | 受影响检查点记 `NA`（如 S2 无行情→仅跑 NAV 复现；S7 无权威 NAV→单元格 NA） |
| `internal_consistency` | 不算数值真值，写出 `{values:{}, self_check:{passed:true}}`；一致性由 `reconcile`/`monotonic` 检查点完成（如 S1 杜邦恒等式、S4 三腿勾稽） | 外部数据检查 → `NA`；内部一致性仍运行 |
| `user_snapshot` | 用户提供快照则原样写入 `values`；否则写 `{values:null}` | 数据点检查 → `NA`；引用与论点仍全评（如 S6） |

调用 `eval-groundtruth` 技能的确定性入口 `python "${CLAUDE_PLUGIN_ROOT}/skills/eval-groundtruth/scripts/gt_dispatch.py" --container "${CLAUDE_PLUGIN_ROOT}/taskspecs/<容器>" [--in name=path ...] [--snapshot ...] --out run/groundtruth.json`（脚本与容器在插件目录用 `${CLAUDE_PLUGIN_ROOT}` 定位，`--in`/`--out` 在用户当前目录）：它按 `kind` 路由、用容器 recipe 的 invocation 模板生成计算器命令、并**强制校验 `self_check.passed`（为假或缺失即停止）**——绝不基于未经验证的真值打分。三种 kind（calculator/internal_consistency/user_snapshot）它都直接处理，编排器无需自行拼命令。（各任务 recipe 见其容器；例：S3=bond_analytics、S4=multifactor_internal〔可选行业腿 brinson〕、S5=linear_shock、S8=client_metrics=linear_shock --metrics。）

## 3. 逐作品循环（每份作品独立评分）

对每份作品，在隔离状态下处理（一份作品的结果绝不可影响另一份的评分）：

1. **提取**——携带作品 + `checkpoint_schema` 调用 `eval-extractor`。产出 `run/<label>/normalized.json`（每个字段 → value | MISSING | AMBIGUOUS + 置信度，外加引用清单）。提取绝不推断缺失值。
2. **检查点评分**——携带 `normalized.json`、`checkpoint_schema` 与 `groundtruth.json` 调用 `eval-checkpoint-grader`。产出 `run/<label>/det_results.json`（逐字段 通过/未通过/NA + 偏差；强制 CF5：错误的数字记零分，不给部分分）。
3. **引用核验**——若 `citation_policy != none`，调用 `eval-citation-verifier`（尽早启动；它依赖网络）。产出 `run/<label>/citation_audit.json`。`unverifiable`（无法核验）是质量问题，**不是**捏造；`fabricated-candidate` 需要确凿证据。
4. **评审 ×2（盲评，经子代理强制隔离，扣分制）**——盲评的有效性取决于评分官**没有**看过真值/检查点/引用。由于此刻主 Agent 的上下文已包含这些客观结果，"自我屏蔽"只是纪律而非机制。因此**必须把每一轮盲评通过 Task 工具派发给 `eval-rubric-judge` 子代理**（`Task(subagent_type: "eval-rubric-judge", prompt: <盲评载荷 JSON>)`），只向它传入盲评载荷，其上下文里不存在任何客观结果可供泄漏：
   ```json
   {"task_id":"S4","plugin_root":"<第 1 步 echo \"$CLAUDE_PLUGIN_ROOT\" 得到的绝对插件根路径>",
    "prompt_text":"<容器 spec.yaml 的逐字提示>","rubric_weights":{...},"work_text":"<盲评作品，去除系统名与标签>",
    "tool_evidence":"<作品自带的工具/执行轨迹与提取器的 tool_inventory（如有）——供 D6 评分；只含作品自身信息，不含真值/检查点/引用核验结果>",
    "judge_notes":"<容器 taskspecs/<task>/judge_notes.md 的内容——本任务家族的『情形→严重度』扣分锚点，不含标准答案数值>"}
   ```
   `plugin_root` 让子代理能用绝对路径 Read 宪法 `rubrics/constitution.md`（子代理的工作目录不是插件目录，且 Read 不展开环境变量）。子代理按扣分制打分：每维度从 4 分起评，逐条登记 `{issue, severity, points, evidence}` 扣分项，无标记不扣分，返回 `judge_N.json` 形态并置 `blind_isolation:"subagent"`。派发两个独立子代理（两次 Task 调用）得到 `judge_1.json` 与 `judge_2.json`。**仅当 Task 工具不可用时**，才退回到主 Agent 自我隔离，并在报告中标注 `blind_isolation: "self"`（较弱）而非 `"subagent"`。为增强评审间独立性，两轮应尽量制造差异：不同子代理会话、可变温度，或（最强）一轮走本地模型、另一轮经外部提供方。确切盲评程序见 `eval-rubric-judge` **子代理**（`agents/eval-rubric-judge.md`）。产出 `run/<label>/judge_1.json` 与 `judge_2.json`。
5. **CF 审计**——携带 `det_results`、`citation_audit` 以及任何执行/复现证据调用 `eval-cf-auditor`。产出 `run/<label>/cf_flags.json`。`CF1`（捏造）标记会*附带证据被提议*，并在对分数封顶之前呈交用户确认。

## 4. 汇总每份作品

携带每份作品的 `det_results`、`citation_audit`、两份评审文件、`cf_flags` 与 taskspec，针对每份作品调用 `eval-aggregator`。产出 `run/<label>/scorecard.json`——一个 0-100 分的总分，含六维度拆解：客观维度锚定到实测数值（`failed_checkpoints` 即扣分来源），主观维度取两位评分官扣分账本推导等级的均值（若分歧超过一级则带 `needs_review` 标记），并应用 CF 封顶。

## 5. 统一报告（无论一份还是多份作品，格式一致）

在**全部**评分卡上（哪怕只有一份）以报告模式调用 `eval-aggregator`（`--report`）。产出 `run/report.md`，固定章节：① 总分与排名表（含 CF 与一行结论）；② 各维度评分总览（D1-D6 × 作品）；③ 各维度详细评分——逐作品的扣分明细（扣多少/严重度/问题/证据）、客观扣分来源、CF 封顶；④ 确定性检查点明细；⑤ 引用核验与致命缺陷；⑥ 结论（排序、分差、不确定性说明）。由于评分是**绝对的**（每份作品都对照同一套量规与基准真值打分，绝非两两比较），即便作品是独立评分的，排序依然公平。

## 6. 呈现

将 `run/report.md` 呈现给用户（这就是统一的最终报告），外加：任何需要确认的 CF1 候选，以及任何 `NA` 检查点及其所需消解数据。指引用户到运行目录获取完整产物。

## 数据契约（规范形态见各被引技能）

`taskspec.json` · `groundtruth.json` · `normalized.json` · `det_results.json` · `citation_audit.json` · `judge_N.json` · `cf_flags.json` · `scorecard.json` · `report.md`。每个阶段各写一份；每个阶段都可基于前一阶段的产物重跑，因此调整量规只会触发重新汇总，而不会重跑整个流程。

## 护栏

- 保持评审盲评、作品隔离。这是核心的有效性属性——不要走捷径。
- **扣分制**：满分起评，只有明确标记、附证据的缺陷才扣分；不可核验（NA）不扣分，从分母剔除并披露。
- 确定性问题交给脚本，而非你自己的运算。
- `不可验证 ≠ 捏造`。只有已确认的 `CF1` 才会将分数封顶为 30。
- 不要透露或推断某份作品由哪个系统产出。
- 本框架评的是分析性*输出*；它不提供投资建议，其分数也不是交易建议。
