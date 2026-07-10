---
name: eval-task-specs
description: >
  任务规范与评分宪法的**加载器**。告诉各引擎去哪里、怎样读取评测所需的权威规范：
  第 1 层宪法在 rubrics/constitution.md（D1-D6、扣分制、CF1-5、盲评隔离），
  第 2 层任务容器在 taskspecs/<task>/（每任务一个文件夹：spec/checkpoint_schema/gt_recipe/judge_notes/…）。
  在对任何任务评测、打分、提取或评判前加载本技能，以确保逐字提示、检查点模式、容差、权重、
  引用策略与真值方案都取自冻结容器，而非任何硬编码副本。
---

# 任务规范加载器（Task Spec Loader）

本技能是一个**参考加载器**，不是一个动作。它把三层架构的读取入口固定下来：

```
第1层 宪法   rubrics/constitution.md          ← 不可变原则（所有任务共享）
第2层 容器   taskspecs/registry.json          ← 任务索引（只列 frozen 容器）
             taskspecs/<task>/                ← 单任务的自包含规范文件夹
第3层 引擎   skills/eval-*                     ← 本技能被这些引擎调用
```

> 迁移说明：本技能原为单体 `references/tasks.md` + `references/rubric_and_cf.md` 的参考库。现已拆分为
> **宪法**（`rubrics/constitution.md`）+ **逐任务容器**（`taskspecs/`）。旧的两份单体文件已删除，以免双源漂移。

## 第 0 步：解析插件根目录（Claude Code 必做）

本包作为 **Claude Code 插件**运行时，工作目录是**用户的项目目录**，而宪法与容器等参考数据装在插件目录里（通常 `~/.claude/plugins/…`）。因此所有 `rubrics/…`、`taskspecs/…` 都不是相对当前目录的路径。**Read 工具不展开环境变量**，所以先用 Bash 解析一次插件根，再用得到的**绝对路径** Read：

```bash
echo "$CLAUDE_PLUGIN_ROOT"     # 打印插件根绝对路径；记为 <ROOT>
```

- 此后一律用 `Read("<ROOT>/rubrics/constitution.md")`、`Read("<ROOT>/taskspecs/registry.json")`、`Read("<ROOT>/taskspecs/<task>/spec.yaml")` 等绝对路径读取参考数据。
- 脚本调用同样用 `${CLAUDE_PLUGIN_ROOT}`（Bash 会展开），例如 `python "${CLAUDE_PLUGIN_ROOT}/skills/eval-groundtruth/scripts/gt_dispatch.py" …`。
- 派发盲评子代理时，把 `<ROOT>` 作为 `plugin_root` 放进盲评载荷，子代理才能用绝对路径读取宪法。
- 运行产物（`run/…`、`eval-runs/…`）仍写在**当前目录**（用户项目），不要写进插件目录（通常只读）。
- 若 `$CLAUDE_PLUGIN_ROOT` 为空（非插件安装、或直接放入某项目 `.claude/`），则以本包仓库根为 `<ROOT>`。

## 如何加载一个任务

1. 在 `<ROOT>/taskspecs/registry.json` 中按 `task_id` 找到条目；确认 `status == "frozen"`，记下 `path` 与 `version`（`path` 为相对插件根的路径，读取时拼成 `<ROOT>/<path>`）。
2. 从该容器 `path` 读取所需文件（**只读你需要的那份**）：

| 你是哪个引擎 | 读取 |
|---|---|
| orchestrator | `spec.yaml`（tier/prompt/weights/policies/inputs/validators/engine_requirements） |
| extractor | `checkpoint_schema.yaml`（`fields:`）、`extraction_notes.md`（可选） |
| groundtruth | `gt_recipe.yaml`（+ 计算器 sidecar） |
| checkpoint-grader | `checkpoint_schema.yaml` 的类型与容差 |
| rubric-judge | `spec.yaml` 的 `prompt_text`/`rubric_weights` + `judge_notes.md`（进入盲评载荷） |
| cf-auditor | `spec.yaml` 的 `cf_rules` + `cf_thresholds` |
| aggregator | `spec.yaml` 的 `rubric_weights`/`objective_dims` + `checkpoint_schema` 标记 |

3. 六维度定义、扣分制量尺、CF1-5、D6 工具链层级等**共享原则**一律读 `<ROOT>/rubrics/constitution.md`。

## 容器契约与校验

容器应包含哪些文件、每个文件的约束、tier 相干性规则：见 `taskspecs/README.md`。
容器是否合规由 `eval-taskspec-lint` 校验；编排器**只评 `frozen` 且 lint 通过**的容器。

## 容器从哪来（作者槽位）

当前 S1-S8 为**手工迁移件**（`origin: legacy-v1`），并作为未来自动作者的 few-shot 范例。
未来由 RAG 支撑的 `eval-task-designer` 子代理生成新容器——检索方法学卡片→判定 tier→起草→自检（含本 lint）→提交人工冻结。
该子代理**尚未构建**；其在流程中的位置见 `taskspecs/README.md` 与 `docs/two-layer-architecture.md` §3。届时插入槽位即可，
容器契约与引擎无需改动。
