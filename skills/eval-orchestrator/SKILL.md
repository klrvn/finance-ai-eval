---
name: eval-orchestrator
description: >
  评测编排层的**三条护栏**（作品注册表 / GT 输入缺失硬停 / 载荷纯净性校验）与数据契约。
  流水线的步骤顺序与派发方式**不在本技能**——以 `commands/eval-judge.md` 为唯一权威。
  当你需要这三条护栏的完整规程、或需要查数据契约中某个产物的形态时，加载本技能。
---

# 评测编排护栏

> **权威边界（重要）**：流水线的**步骤顺序、并行结构、子代理派发方式**以
> `commands/eval-judge.md` 为**唯一权威**——那里定义了主对话作为纯派发器、并行扇出
> `3×N` 个子代理（N 个 `eval-pipeline` + 2N 个 `eval-rubric-judge`）的完整流程。
> **本技能不重述流水线**，只承载三条**编排层特有护栏**的完整规程，以及数据契约清单。
> 评分原则（D1-D6、扣分制、CF1-5、盲评隔离原则）以 `rubrics/constitution.md` 为准。

三条护栏：

| 护栏 | 防的是什么 | 章节 |
|---|---|---|
| 作品注册表 `work_registry.json` | 盲评标签与作品路径映射错位 | §1 |
| GT 输入缺失硬停 | 客观锚定全空的"空跑" | §2 |
| 载荷纯净性校验 | 跨作品污染导致的盲评幻觉 | §3 |

---

## §1. 作品注册表（`work_registry.json`）——强制，在所有下游操作之前

**这是防止盲评映射错位的核心闸门。** 在接收作品后、执行任何下游操作（提取、评分、盲评派发、报告）之前，**必须**先构建 `work_registry.json`，将每个作品的来源路径/标识与其盲评标签锁死：

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

---

## §2. GT 输入数据缺失硬停规则

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

---

## §3. 载荷纯净性校验程序（在盲评派发前，强制执行）

盲评载荷中的 `tool_evidence` 与任何引导性提示**必须仅含当前作品自身的特征**。跨作品复用描述段落是导致盲评幻觉的主要根因。组装完每个作品的盲评载荷后，强制执行以下自检：

a. **逐条溯源**：将 `tool_evidence` 中的每条事实性陈述（如"使用了 style_metrics 字典映射""3 只基金共享 31.50% 波动率""引用表含 6 条公众号文章""rf=1.35%""使用了 westock-data 内置工具"等）与当前作品的 `work_text` 逐一匹配。每条陈述都必须能在 `work_text` 中找到对应的原文片段。

b. **删除不匹配项**：任何在 `work_text` 中找不到对应的事实性陈述，**必须从 `tool_evidence` 中删除**——无论它看起来多么"合理"或"可能是对的"。宁可省略，不可臆造。删除时记录 `dropped_claims` 列表（含被删陈述及删除原因），写入 `<runDir>/Work_X/payload_audit.json`。

c. **禁止跨作品复用**：`tool_evidence` 和引导性提示**不得从其他作品的载荷中复制粘贴**。每份作品的载荷必须独立组装，且组装时只参考该作品的 `work_text` 和 `normalized.json` 中的 `tool_inventory` 字段。

d. **引导性提示（如有）同样校验**：若在载荷中包含任何引导性提示（如"Pay attention to..."），这些提示中的每条事实性描述也必须与当前 `work_text` 一致。不匹配的提示必须删除。

e. **校验记录**：将校验结果写入 `<runDir>/Work_X/payload_audit.json`，包含 `verified_claims`（已验证一致的陈述）、`dropped_claims`（已删除的不匹配陈述）和 `isolation_confirmed`（布尔值，确认载荷仅含当前作品特征）。

**警告**：载荷组装错误会导致两种对称的盲评幻觉——(1) `tool_evidence` 混入相邻作品的特征 → 子代理基于不存在于本作品的"缺陷"误增扣分；(2) 引导性提示使用了错误作品的特征 → 子代理误以为本作品方法论严谨而漏判真实缺陷。两者都会严重扭曲评分。纯净性校验是防止这两类错误的强制闸门。

### 盲评载荷的字段白名单

派发给 `eval-rubric-judge` 的载荷**只允许**这些字段：

```json
{"task_id":"S4","plugin_root":"<ROOT>","prompt_text":"<容器 spec.yaml 的逐字提示>","rubric_weights":{...},
 "work_text":"<经 work_registry.json 查询 blind_label→path→Read(path) 获取的作品文本，去除系统名与标签>",
 "tool_evidence":"<经纯净性校验后的作品自身工具/执行轨迹>",
 "judge_notes":"<容器 judge_notes.md——扣分锚点，不含标准答案数值>"}
```

**绝不允许**出现在载荷中：`groundtruth.json` 的任何内容、`det_results.json`、`citation_audit.json`、任何参考数值、任何其他作品的文本、作品的文件名或平台身份。

---

## 数据契约（规范形态见各被引技能）

`taskspec.json` · `work_registry.json` · `groundtruth.json` · `normalized.json` · `det_results.json` · `citation_audit.json` · `payload_audit.json` · `judge_N.json` · `cf_flags.json` · `scorecard.json` · `report.md`。每个阶段各写一份；每个阶段都可基于前一阶段的产物重跑，因此调整量规只会触发重新汇总，而不会重跑整个流程。

## 护栏小结

- **GT 输入数据缺失硬停（防空跑）**：非 optional 输入缺失时**必须停止并报错**（§2），而非静默记 NA。静默 NA 会让评测"看起来在跑"但客观锚定全空——这比报错更危险。
- **作品注册表（防映射错位）**：任何下游操作之前**必须**先建 `work_registry.json`（§1），锁死 `path → blind_label`。
- **载荷纯净性（防幻觉）**：盲评载荷必须仅含当前作品自身特征并经校验（§3）。不经校验的载荷**不得**派发。
- 上述三条为**编排层特有护栏**。步骤顺序见 `commands/eval-judge.md`；其余评分原则均以 `rubrics/constitution.md` 为准，此处不复述。
