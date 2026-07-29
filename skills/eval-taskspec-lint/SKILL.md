---
name: eval-taskspec-lint
description: >
  校验任务规范容器（taskspecs/<task>/）是否合乎宪法可参数化面与 tier 相干性规则：权重合计 100、
  objective_dims ⊆ {D1,D2}、检查点类型在封闭类型表内、数值型检查点都有真值路径（禁止伪确定性）、
  gt_recipe 计算器绑定可解析、judge_notes 不泄漏标准答案、GT 输入数据打包检查。是让引擎层保持任务无关的
  治理关口——容器在此通过，引擎即可机械消费。任何容器进入 `frozen` 前都必须先过此 lint。
---

# 任务规范容器 Linter

这是第 3 层（容器）与第 2 层（引擎）之间的**治理关口**。容器是数据、引擎是无任务知识的机器；本 lint 保证
每个容器都落在宪法（`rubrics/constitution.md` §1 可参数化面）与 tier 相干性规则（`taskspecs/README.md`）之内，
引擎才能安全地机械消费它。

## 校验项

**通用（全 tier）**
- `rubric_weights` 覆盖 D1-D6、整数、合计 100；D6 权重 ≥ 5。
- `objective_dims ⊆ {D1, D2}`（汇总器只对 D1/D2 做客观锚定）。
- 每个检查点 `type` ∈ 封闭类型表（见 registry `checkpoint_type_registry`）。
- **无伪确定性**：任何数值/结构型检查点（number/pct/bp/yr/ratio/vector/set_match/sample_verify）都必须有真值路径
  （`gt_recipe.kind` 为 `calculator` 或 `user_snapshot`）；`reconcile/consistency/monotonic` 为内部一致性类型，豁免。
- `calculator` 方案的 `sidecar` 与 `script` 路径可解析。
- `cf_thresholds` 在宪法界限内（如 CF2 未引用率 ∈ [0.15, 0.40]）。
- `judge_notes.md` 不含"标准答案值"泄漏（关键词紧邻数值的断言）——保护盲评载荷不被污染。
- registry 与 spec 的 `task_id/tier/version` 一致；`provenance.origin ∈ {legacy-v1, kb}`。

**tier 相干性**（规则从早期 8 个参考容器 S1–S8 反推并保证其全过；设计初稿更严版本与 S5/S6/S7/S8 冲突已放宽。新容器如 S9 亦须满足这些规则）
- **T1**：`objective_dims == [D1,D2]` 且 `gt kind == calculator`。
- **T2**：`len(objective_dims) ≤ 1` 且有可核验路径（calculator / 内部一致性检查点 / sample_verify）。
- **T3**：`len(objective_dims) ≤ 1` 且非客观权重（`100 − Σobj权重`）≥ 40。

**GT 输入数据打包检查**
- `gt_recipe.kind == calculator` 的容器**必须**在 `fixtures/` 下打包 `inputs_required` 中非 `optional` 的输入文件。缺失 → **ERROR**（A 类任务无 GT 数据则确定性检查点无法运行，评测将硬停）。
- `gt_recipe.kind == user_snapshot` 的容器**应当**在 `fixtures/` 下打包快照文件；若 `inputs_required` 声明允许运行时提供（`na_policy` 含"运行时"），则缺失 → **WARN**（C 类任务可接受运行时提供，但推荐打包）。
- `gt_recipe.kind == internal_consistency` 的容器无需 GT 输入数据（豁免）。
- 文件哈希应记入 `provenance.json`（若已回填）。

## 用法

```bash
# 校验全部已注册容器
python scripts/spec_lint.py --root <repo-root>
# 校验并把 container_hash 盖进各 provenance.json
python scripts/spec_lint.py --root <repo-root> --stamp
```

退出码 0 当且仅当无 ERROR（WARN 允许）。任何容器进入 `frozen` 前必须 lint 通过；这是编排器只评 frozen 容器这一
反作弊属性的前置保证。
