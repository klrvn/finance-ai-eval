---
name: eval-task-specs
description: >
  任务规范与评分宪法的**加载器**。告诉各引擎去哪里、怎样读取评测所需的权威规范：
  第 1 层宪法在 rubrics/constitution.md（D1-D6、扣分制、CF1-5、盲评隔离），
  第 3 层任务容器在 taskspecs/<task>/（每任务一个文件夹：spec/checkpoint_schema/gt_recipe/judge_notes/…）。
  在对任何任务评测、打分、提取或评判前加载本技能，以确保逐字提示、检查点模式、容差、权重、
  引用策略与真值方案都取自冻结容器，而非任何硬编码副本。
---

# 任务规范加载器（Task Spec Loader）

本技能是一个**参考加载器**，不是一个动作。它把三层架构的读取入口固定下来：

```
第1层 宪法   rubrics/constitution.md          ← 不可变原则（所有任务共享）
第2层 引擎   skills/eval-*                     ← 本技能被这些引擎调用
第3层 容器   taskspecs/registry.json          ← 任务索引（只列 frozen 容器）
             taskspecs/<task>/                ← 单任务的自包含规范文件夹
```

> 迁移说明：本技能原为单体 `references/tasks.md` + `references/rubric_and_cf.md` 的参考库。现已拆分为
> **宪法**（`rubrics/constitution.md`）+ **逐任务容器**（`taskspecs/`）。旧的两份单体文件已删除，以免双源漂移。

## 如何加载一个任务

1. 在 `taskspecs/registry.json` 中按 `task_id` 找到条目；确认 `status == "frozen"`，记下 `path` 与 `version`。
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

3. 六维度定义、扣分制量尺、CF1-5、D1 数据来源含金量分级、D6 工具链扣分锚点等**共享原则**一律读 `rubrics/constitution.md`。

## 容器契约与校验

容器应包含哪些文件、每个文件的约束、tier 相干性规则：见 `taskspecs/README.md`。
容器是否合规由 `eval-taskspec-lint` 校验；编排器**只评 `frozen` 且 lint 通过**的容器。

## 容器从哪来

已注册容器（当前 S1–S11，见 `taskspecs/registry.json`）均由人**手工编写**（S1–S8 为 `origin: legacy-v1` 迁移件）：判定 tier → 起草 → 自检（含本 lint）→ 提交人工冻结；容器契约见 `taskspecs/README.md`。
