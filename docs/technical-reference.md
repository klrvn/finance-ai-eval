# Eval Judge 技术参考手册

> **读者定位**：三类读者按需阅读。
> 
> - **评测使用者**（要用它给一份金融分析作答打分）→ 读 §1、§7、§8。
> - **容器作者**（要为新任务写一套冻结规范）→ 读 §2、§3、§4、§6，以及 `taskspecs/README.md`。
> - **引擎维护者**（要改评分流程或新增计算器）→ 读 §2、§4、§5、§10。
> 
> **权威性顺序**：本手册解释系统如何工作和为什么这样设计，但不定义规则。
> 冲突时以 `rubrics/constitution.md`（宪法）和 `taskspecs/<task>/`（任务容器）为正本——
> 宪法定义"能做什么、不能做什么"，容器定义"这个任务具体怎么评"，本文只解释"如何做"和"为什么"。
> 已有设计论证文档 `docs/three-layer-architecture.md` 记录了架构决策过程和迁移历史，本文是技术参考，二者互补。

---

## 目录

0. [术语约定](#0-术语约定)
1. [系统概览](#1-系统概览)
2. [三层架构](#2-三层架构)
3. [评分宪法（架构第一层）](#3-评分宪法架构第一层)
4. [两层维度评分模型](#4-两层维度评分模型)
5. [引擎层：按流水线顺序](#5-引擎层按流水线顺序)
6. [任务容器（架构第三层）](#6-任务容器架构第三层)
7. [编排与隔离机制](#7-编排与隔离机制)
8. [入口与调用方式](#8-入口与调用方式)
9. [输出规范](#9-输出规范)
10. [安装、环境与路径约定](#10-安装环境与路径约定)
11. [开发者工作流](#11-开发者工作流)
12. [故障排查](#12-故障排查)
13. [附录](#13-附录)

---

## 0. 术语约定

本文中"层"这个词有两套完全不同的用法，阅读前先区分清楚：

| 术语                  | 含义                      | 适用范围                      |
| ------------------- | ----------------------- | ------------------------- |
| **架构第一层 / Layer 1** | 宪法层——不可变评分原则，所有任务共享     | 整个框架                      |
| **架构第二层 / Layer 2** | 引擎层——任务无关的流程代码          | 整个框架                      |
| **架构第三层 / Layer 3** | 容器层——每个任务的自包含规范文件夹      | 每个任务独立                    |
| **评分第一层（L1）**       | 维度分数的实测基线——检查点通过率或引用核验率 | 仅 `objective_dims`（D1、D2） |
| **评分第二层（L2）**       | 维度分数的扣分锚点——盲评官登记的主观缺陷   | 全部六个维度                    |

**简称对照**：后文统一用"L1 / L2"指评分两层，用"宪法 / 引擎 / 容器"指架构三层，不混用。

其他高频术语：

| 术语                    | 说明                                                     |
| --------------------- | ------------------------------------------------------ |
| **检查点（checkpoint）**   | 任务规范中定义的一个可评测字段，如"YTD 收益率"或"杜邦恒等式校验"                   |
| **GT（ground truth）**  | 基准真值——由确定性脚本计算的正确答案，供检查点评分器做容差比对                       |
| **NA**                | 不可核验——因 GT 缺失或输入不足而无法判定通过/未通过，不扣分                      |
| **CF**                | 关键失败（Critical Failure）——结构性缺陷触发维度或总分封顶                 |
| **盲评（blind judging）** | 评分官只看作品文本和家族扣分锚点，不知道真值、检查点结果和其他作品的分数                   |
| **扣分制**               | 每个维度从 4 分满分起评，只有明确标记了证据的缺陷才扣分                          |
| **Workflow 岛**        | `orchestration/eval-judge.workflow.js`——唯一必须机器强制隔离的执行段 |

---

## 1. 系统概览

### 1.1 它解决什么问题

Eval Judge（评审法官）是一个**金融分析作答的自动评测系统**。给定一个任务（如"做一份杜邦分析""跑一个动量回测""检索四只标的的 YTD 收益率"），它对照**预先冻结的规范**对一份或多份候选作答给出 0-100 的分数，并产出带完整证据链的评分报告。

### 1.2 四个核心设计承诺

1. **确定性数值交给脚本**。检查点的通过/未通过由 Python 脚本做容差比对，不靠 LLM 肉眼判断。
2. **盲评与客观通道隔离**。定性打分的评分官永远看不到基准真值和检查点结果——这一条由代码结构强制保证，不是靠提示词"请假装没看到"。
3. **任务知识只存在于冻结容器**。引擎层代码不知道任何具体任务的信息——所有"这个任务考什么、权重多少、容差多大"都存放在每个任务的独立文件夹里。
4. **扣分制，不可核验不扣分**。每个维度从满分起评，只有证据确凿的缺陷才扣分。无法验证的内容（GT 缺失、网络故障、付费墙）不扣分，从分母剔除。

### 1.3 一篇作品经过的全流程（简化版）

```
作品文本 + 任务容器
       │
       ▼
  ① 提取器：把作品里的数值、引用、工具调用提取成结构化 JSON
       │
       ▼
  ② 基准真值：运行确定性脚本，算出"正确答案"（所有作品共享同一份 GT）
       │
       ▼
  ③ 检查点评分器：脚本逐字段比对 作品值 vs GT，输出 通过/未通过/NA
       │
       ▼
  ④ 引用核验器：网络搜索验证每条引用是否存在、是否支撑主张、是否新鲜
       │
       ▼
  ⑤ 盲评 ×2：两个独立子代理（从未见过 GT 和检查点结果）在六个维度上扣分制打分
       │
       ▼
  ⑥ CF 审计器：检查是否触发关键失败规则（捏造、未引用、只描述不计算、陈旧数据）
       │
       ▼
  ⑦ 汇总器：合并所有通道 → 两层计分 → 加权 → CF 封顶 → 0-100 总分
       │
       ▼
  ⑧ 报告：统一格式的 markdown 报告（总分排名 → 六维度详情 → 检查点明细 → 结论）
```

每一步都**写出一份 JSON 文件到运行目录**，所以任何环节都可以单独重跑而不用重跑整个流程。中间产物的完整清单见 §9.2。

### 1.4 一份产出物长什么样

最终用户拿到的是 `report.md`，固定六个段落：总分与排名表、六维度总览、逐维度详细评分（含扣分证据）、确定性检查点明细、引用与 CF 标记、结论。单份作品就是只有一行的排名表。格式详见 §9.1。

---

## 2. 三层架构

Eval Judge 把"定义规则""执行流程"和"每个任务的具体规范"拆成三层。这张图解释了谁定义什么、谁调用谁：

```
┌─────────────────────────────────────────────────────┐
│  架构第一层  宪法  rubrics/constitution.md            │
│              "所有任务都遵守的不可变规则"              │
│              D1-D6 是什么、扣分制怎么扣、CF 怎么触发   │
├─────────────────────────────────────────────────────┤
│  架构第二层  引擎  skills/eval-*                      │
│              "不知道任何任务具体考什么"                │
│              提取 / 真值 / 评分 / 核验 / 审计 / 汇总   │
│              agents/  orchestrator + blind judge      │
│              orchestration/  Workflow 岛              │
├─────────────────────────────────────────────────────┤
│  架构第三层  容器  taskspecs/<task>/                   │
│              "这个任务考这些，权重这么多，容差这么小"   │
│              spec / checkpoint_schema / gt_recipe     │
│              judge_notes / fixtures / provenance      │
└─────────────────────────────────────────────────────┘
```

### 2.1 各层职责与禁止跨层

**宪法层**（第一层）定义的是"不可变原则"——六维度恰好 D1 到 D6、扣分量尺（轻微 −0.5 / 明显 −1 / 严重 −2）、CF1 到 CF5 的触发条件和封顶效果、盲评必须靠隔离机制而非自律。这些规则**容器不得覆盖**。宪法也列出了容器可以定制的参数（称为"可参数化面"），例如权重、容差、引用策略——容器只能在这些参数范围内定制。

**引擎层**（第二层）包含所有执行代码——提取器、评分器、核验器、汇总器，以及编排这些工具的流程控制代码。这一层的代码**不包含任何任务知识**：它不知道 S3 是债券分析、S9 有 4 道题、D1 权重在 S6 是 30 而在 S9 是 40。它只读容器的规范文件，按契约执行。

**容器层**（第三层）是一个个自包含的文件夹，每个文件夹装着评测一个任务所需的全部规范。**新增任务 = 新增一个容器文件夹**，不改宪法、不改引擎。容器在评测前必须冻结（`status: frozen`），其哈希值会被钉入运行目录——这是反作弊属性，防止"看到学生答案后再调整规范"。

### 2.2 目录地图

```
eval-judge/
├── rubrics/
│   └── constitution.md            # 架构第一层：评分宪法
├── skills/
│   ├── eval-task-specs/           # 规范加载器（告诉各引擎去哪读容器）
│   ├── eval-groundtruth/          # 基准真值构建（Python 计算器 + 调度器）
│   ├── eval-extractor/            # 字段提取（作品 → 结构化 JSON）
│   ├── eval-checkpoint-grader/    # 检查点评分（数值容差比对）
│   ├── eval-citation-verifier/    # 引用核验（网络搜索验证）
│   ├── eval-rubric-judge/         # 盲评量规评分（定性扣分制打分）
│   ├── eval-cf-auditor/           # CF 审计（关键失败规则判定）
│   ├── eval-aggregator/           # 汇总与报告（合并通道 → 分数 → 报告）
│   ├── eval-orchestrator/         # 编排规程（流程控制 + 护栏规则）
│   └── eval-taskspec-lint/        # 容器校验（冻结前的治理关口）
├── agents/
│   ├── eval-judge.md              # 编排器 agent（评审法官 persona）
│   └── eval-rubric-judge.md       # 盲评子代理（隔离评分官 persona）
├── commands/
│   └── eval-judge.md              # /eval-judge 命令（首选入口）
├── orchestration/
│   └── eval-judge.workflow.js     # Workflow 岛（隔离关键核）
├── taskspecs/
│   ├── registry.json              # 任务索引（S1-S11）
│   ├── README.md                  # 容器契约与编写指南
│   ├── S1-dupont-analysis/        # 杜邦分析
│   ├── S2-momentum-backtest/      # 动量回测
│   ├── S3-bond-analytics/         # 债券分析
│   ├── S4-multifactor-attribution/# 多因子归因
│   ├── S5-stress-test/            # 压力测试
│   ├── S6-macro-view/             # 宏观观点
│   ├── S7-fund-screen/            # 基金筛选
│   ├── S8-client-diagnosis/       # 客户诊断
│   ├── S9-data-retrieval-stability/# 数据获取稳定性
│   ├── S10-financial-data-retrieval/# 财务数据获取
│   └── S11-news-data-retrieval/   # 消息面取数
├── docs/
│   ├── three-layer-architecture.md # 架构设计论证
│   └── technical-reference.md      # 本手册
├── README.md
└── CLAUDE.md
```

### 2.3 扩展点

- **新增任务**：在 `taskspecs/` 下新建文件夹，写入规范文件，通过 lint，冻结，然后在 `registry.json` 注册。不需要改任何引擎代码。
- **新增计算器**：在 `skills/eval-groundtruth/scripts/` 下新建 `.py` + `.calculator.yaml` sidecar，然后在对应容器的 `gt_recipe.yaml` 里绑定。计算器必须带 `self_check` 块。
- **新增检查点类型**：这是**引擎发布级**的变更，需要改 `grade_checkpoints.py` 并补测试——不允许逐任务分叉。
- **新增验证器插件**：在容器 `validators/` 下放置，遵循验证器契约，由容器的 `task_validators` 声明。

---

## 3. 评分宪法（架构第一层）

宪法 `rubrics/constitution.md` 定义的是**所有任务共享的不可变规则**，以及容器可以在哪些参数范围内定制。以下是完整内容的结构化摘要，正文以宪法为准。

### 3.1 不可变原则（§0）——容器不得覆盖

- **维度集固定**：恰好 D1-D6，不可增删改名。
- **扣分词表固定**：轻微 −0.5、明显 −1、严重 −2。等级 = `max(0, 4 − Σ扣分)`，允许 0.5 步长。
- **CF 规则固定**：CF1-CF5 的触发条件、所需证据、封顶效果都不可变。CF1（捏造）的人工确认闸门不可绕过。
- **NA 不扣分**：真值缺失、网络故障、付费墙导致的不可核验项，从分母剔除，不当作缺陷扣分。
- **不可验证 ≠ 捏造**：抓不到的引用是 D1 质量问题，不触发 CF1。
- **篇幅中性**：长的不加分，短的不扣分。只有被标记的缺陷才扣分。
- **盲评靠机制**：每轮盲评派发全新子代理，绝对评分（非两两比较），作品之间互相隔离。
- **运算交给脚本**：确定性真值只能由带自检的可执行计算器产出，不由 LLM 估算。

### 3.2 可参数化面（§1）——容器唯一合法的定制边界

这是容器可以调整的参数表。超出此范围的定制会被 `spec-lint` 拦截。

| 参数                        | 允许范围                                             | 校验者                    |
| ------------------------- | ------------------------------------------------ | ---------------------- |
| `rubric_weights`          | 六维度整数权重，合计 100；D6 ≥ 5                            | spec-lint              |
| `objective_dims`          | `{D1, D2}` 的子集                                   | spec-lint              |
| `cf_rules`                | `{CF1..CF5}` 的子集；CF2 未引用率阈值 ∈ [0.15, 0.40]       | spec-lint / cf-auditor |
| `citation_policy`         | `none` / `sample,k` / `per_cell_sample` / `full` | citation-verifier      |
| 检查点类型                     | 只能取自封闭类型表（14 种，见 §6.4）                           | spec-lint / grader     |
| `judge_notes` 扣分锚点        | "情形 → 已有严重度"的映射；禁止出现期望数值答案、改权重、新增严重度             | spec-lint              |
| 计算器 / 验证器插件               | 遵循计算器契约 / 验证器契约                                  | spec-lint + 自测         |
| `d1_objective_weights`    | `{citation, grounding}` 权重覆盖，值 ∈ [0,1] 且和为 1     | spec-lint / aggregator |
| `grounding_group_weights` | 按组前缀给 grounding 检查点非等权                           | spec-lint / aggregator |

**升级规则**：任何在 ≥2 个容器中以插件形式出现的能力，必须上升为共享引擎能力——否则引擎会被任务反向绑定。

### 3.3 扣分制总则

所有作品在每个维度上**默认满分起评**。分数只能因为被明确标记的缺陷而下降：

1. **无标记，不扣分**。说不出具体缺陷就不降级。
2. **不可核验 ≠ 缺陷**。NA 项不扣分，从分母剔除并单独披露。
3. **每笔扣分可追溯**。报告中每个非满分的维度都展开为"扣了多少、为什么扣、证据在哪"的清单。

### 3.4 六个评分维度

| 维度  | 名称                  | 测量什么                                                      |
| --- | ------------------- | --------------------------------------------------------- |
| D1  | 数据完整性与依据（grounding） | 数值是否有引用支撑、来源是否具名可溯源、来源含金量高低、是否编造数据                        |
| D2  | 方法正确性               | 公式/框架/惯例是否用对                                              |
| D3  | 完整性                 | 提示要求的交付物是否都在且实际计算过（不是只描述了没算）                              |
| D4  | 分析质量与洞察             | 推理深度、驱动因素/风险是否正确识别                                        |
| D5  | 可操作性与适配性            | 建议是否具体可决策、是否针对所声明的用户/风险画像                                 |
| D6  | 外部工具链完整度            | 计算是否经代码/工具执行且可查；工具链覆盖是否完整；调用失败后是否处理。**只评工具使用行为，不评数据来源层级** |

### 3.5 扣分量尺

每个维度从 4 分起评。扣分项必须登记严重度与证据：

| 严重度        | 扣分   | 判定标准        |
| ---------- | ---- | ----------- |
| 轻微（minor）  | −0.5 | 不影响结论的小缺陷   |
| 明显（major）  | −1   | 影响该维度可信度的缺陷 |
| 严重（severe） | −2   | 使该维度大面积失效   |

等级语义：4 = 优秀（无扣分项）/ 3 = 良好 / 2 = 合格 / 1 = 较差 / 0 = 不及格。

### 3.6 D1 数据来源含金量分级

这是 D1 特有的评估机制——不看数值对不对（那是检查点的工作），只看数据从什么级别的来源来：

| 级别  | 说明        | 来源举例                                                                    |
| --- | --------- | ----------------------------------------------------------------------- |
| 第一级 | 原始/一手数据   | 公司财报、Wind/Bloomberg API、央行/统计局、交易所官方行情、**内置数据抓取工具**（如 stock_financials） |
| 第二级 | 权威机构二次分析  | 券商研报、基金评级报告、金融终端分析模块输出、专业财经媒体深度分析                                       |
| 第三级 | 无专业编辑流程保障 | 自媒体/公众号、博客论坛、百科条目、搜索引擎摘要、来路不明的网页                                        |

分级判定以**每条量化主张的实际来源**为准，取最高级别；不同数据点可以有不同级别。扣分锚点见宪法原文。

### 3.7 关键失败规则（CF1-CF5）

CF 封顶是扣分制的极端形态——结构性缺陷直接把维度或总分压到上限。

| 规则  | 触发条件             | 所需证据                                    | 效果               |
| --- | ---------------- | --------------------------------------- | ---------------- |
| CF1 | 捏造数据（编造价格/代码/来源） | 确凿证据：不存在的标识符，或说法不同的被引来源；**需人工确认，不自动应用** | 总分 ≤ 30          |
| CF2 | 相当比例的量化主张无引用     | 未引用率超过阈值（默认 0.25）                       | D1 ≤ 1           |
| CF3 | 描述了分析但未产出计算结果    | 无执行轨迹且无产物能复现所宣称的指标                      | D2, D3 ≤ 1       |
| CF4 | 未实际获取便将陈旧数据当当前使用 | 数值与快照不一致/明显早于截止点                        | D1 ≤ 1           |
| CF5 | 确定性检查点超出容差       | 检查点评分器的偏差 > `tol`                       | 该检查点记 0 分（不给部分分） |

注意"不可验证 ≠ 捏造"——一条抓不到的引用（网络故障、付费墙）会降级为 D1 质量问题，绝不触发 CF1。

### 3.8 tier 等级与可核验程度

tier 描述任务有多少东西可以被客观核验，进而决定了评分中 L1 能覆盖多少维度：

- **T1（确定性）**：`objective_dims == [D1, D2]`，有可执行数值计算器，承重数值检查点都有真值路径。例如 S2、S3、S9、S10、S11。
- **T2（半确定性）**：最多一个客观维度，至少有一条可核验路径（计算器或内部一致性检查点）。例如 S1、S4、S5、S7。
- **T3（研判型）**：最多一个客观维度，非客观权重 ≥ 40，以研判和引用质量为主导。例如 S6、S8。

全 tier 通用约束：权重合计 100；`objective_dims ⊆ {D1, D2}`；D6 权重 ≥ 5；数值型检查点若非内部一致性类型，必须有真值路径（禁止"伪确定性"——声称要核对数值却没有 GT 来源）。

---

## 4. 两层维度评分模型

这是全框架最核心也最容易误解的机制。简单说：**每个维度的最终分数 = 一个可选的实测基线 减去 一份主观扣分账本**。前半句叫评分第一层（L1），后半句叫评分第二层（L2）。

### 4.1 为什么要分两层

旧模型存在一个结构性缺陷：D1 既想用检查点通过率衡量"数值对不对"，又想用来源含金量惩罚"数据从低质量来源来"。但旧模型把这两件事分给两个独立阶段（客观维度整体交给客观比例、主观维度交给评分官），结果**来源含金量扣分被客观比例覆盖后实际丢弃了**——一份全部从第三级来源来的数据，只要数值碰巧对上 GT，D1 分数照拿不误。

两层模型的解法：每个维度内部同时跑两条线——L1 测"数值对不对"，L2 扣"来源好不好"。两者做减法合成，不互相覆盖。

另一极的纯扣分制则完全用不上已经费力算好的 GT。两层模型保留了 GT 的价值，只在 GT 覆盖不到的缺陷上让评分官出手。

### 4.2 合成公式

```
维度分数值（fraction）= max(0, L1_frac − L2_points / 4)
加权得分（weighted_points）= fraction × 该维度权重
```

- `L1_frac` 是第一层实测基线，取值范围 0 到 1。对于没有 GT 可测的维度（D4/D5 以及没有客观指标的 D1/D2），`L1_frac` 恒为 1.0——此时公式退化为纯扣分制，与旧行为逐位兼容。
- `L2_points` 是两位盲评官在该维度扣分合计的**均值**（以严重度点数计：0.5 / 1 / 2），除以 4 后从基线中减去。
- `max(0, …)` 保证 fraction 不会扣成负数。
- fraction 保持**连续值**不先四舍五入到整数等级——避免同类作品因 0.01 的差异落到不同等级，也避免 Python 银行家舍入造成的不一致。

等级 `level` 仅用于展示，由 `round(4 × fraction, 2)` 计算，0.5 步长。

### 4.3 第一层：GT 基线（L1）

L1 是可选层——只有维度在 `objective_dims`（当前始终 ⊆ {D1, D2}）中且确实实测出了客观比例时才生效。

#### D1 的 L1 口径

D1 的实测基线由两个成分混合：

```
grounding_objective = citation_weight × verified_fraction + grounding_weight × grounding_pass_fraction
```

- `verified_fraction`：引用核验通道产出的已核验引用占比。
- `grounding_pass_fraction`：标记了 `grounding: true` 的检查点的通过率。
- 默认权重 `{citation: 0.6, grounding: 0.4}`，容器可用 `d1_objective_weights` 覆盖。例如 S9 用 `{citation: 0.3, grounding: 0.7}`——因为数据获取任务中数值准确性比引用格式更重要。
- 如果任务没有引用策略（`citation_policy: none`），grounding 取全部权重（退化回单纯的检查点通过率）。

**grounding 检查点分组加权**：容器可以通过 `grounding_group_weights` 让不同题目的检查点权重不同。例如 S9 的 4 道题各 3 个检查点（base_price / latest_price / answer），配比为 0.4 : 0.4 : 0.2——意味着"基准价取对了"占 40%，"最新价取对了"占 40%，"最终答案算对了"占 20%。四道题之间等权平均。这样一道题的 base_price 答错不会把整道题的分数全扣光。

#### D2 的 L1 口径

D2 的基线取 `methodology_pass_fraction`——标记了 `methodology: true` 的检查点的通过率。如果任务没有标注任何 methodology 检查点，回退到全部检查点的 `pass_fraction`。

**偏差评分模式**：当容器声明了 `objective_scoring_mode: deviation` 时，L1 优先使用连续的偏差分数而非二值（通过/未通过）。偏差模式下的通过率 = 各检查点偏差分数的加权平均，而非简单的"通过数 / 总数"。这种模式适用于需要对数值精度做连续评价的任务（如 S2 动量回测）。

### 4.4 第二层：扣分锚点（L2）

L2 是**强制层**——全部六个维度都适用。它来自两位盲评官各自登记的扣分账本。

扣分账本由 `{issue, severity, points, evidence}` 四元组组成。汇总器取两位评分官扣分合计的**均值**，除以 4，从 L1 基线中减去。

**账本是唯一真相源**：等级由账本反算（`4 − Σpoints`），不采信评分官自报的等级字段。汇总器在推算时做双侧钳制——`points` 为负数（符号写反了）会导致等级被钳到 4，同时触发 `ledger_warnings`。这是账本符号错误和等级膨胀缺陷的特征信号。

扣分锚点有两个出处：

1. **宪法内置**：D1 来源含金量七条锚点（关键数据取自第三级 −1、凭记忆给数 −2、来源缺日期 −0.5……）、D6 工具链四条锚点（无执行轨迹 −1、覆盖不完整 −0.5~−1、调用失败未处理 −0.5、完全无工具 −2）。
2. **容器的 `judge_notes.md`**：声明本任务家族的"什么算第一级来源""什么算关键计算步骤"，评分官据此对号入座。容器只能声明归属，不得改分级框架、不得改权重、不得新增严重度。

### 4.5 两层不重叠（disjointness）——本章最关键的规则

**规则**：在客观维度（D1、D2）上，盲评官的 L2 扣分**只能针对不可数值核验的缺陷**——来源含金量层级、as-of 日期是否标注、复权类型是否声明、口径叙述是否诚实。**数值本身对不对由 L1 检查点负责**，评分官不得因为"某个数算错了"在 D1/D2 再扣一次。

这是为了防止同一事实被扣两次——检查点已经因为某个数值超出容差记了 fail（压低 L1），评分官又来一次"这个数不对"扣 L2，一份错误被罚两次。

**这个规则靠什么保证**：

1. 容器的 `judge_notes.md` 必须按「第一层（检查点，评分官不在此扣分）/ 第二层扣分锚点」分列——由 `spec_lint.py` 校验，标题结构缺失会触发 WARN。
2. 出现 `→ +N` 形式的加分会触发 ERROR（扣分制下不允许加分）。
3. 两层不重叠的**语义正确性**目前靠容器作者和宪法约束——lint 只能查结构，不能真正理解锚点的内容是否与检查点重叠。这是当前框架的执行边界。

### 4.6 边界与退化情形

**空第二层**：S11 是先例——该家族的三级来源锚点在新闻检索任务上缺乏区分度，因此 D1 完全取消 L2，D1 = 纯 L1 基线。Lint 只要求 `judge_notes.md` 中两个标题存在，不要求 L2 非空。

**无 GT / GT 缺失**：NA 从分母剔除，不当缺陷扣分。汇总器输出 `scored_weight`（实际参与计分的权重之和）、`unscored_dims`（未评分维度列表）和 `score_normalized`（归一化分 = `总分 / 有效权重 × 100`）。缺少这一披露会导致"部分维度没评分，但总分看起来像 /100 满分制"的误读。

**权重为 0 的维度**：S9 的 D4/D5 权重为 0（任务不需要分析洞察和可操作性），这两个维度不参与计分，也不进入有效分母。

**评分官分歧（needs_review）**：两位盲评官在任一维度上分歧超过一级 → 该维度标记 `needs_review`。分数仍按公式算，但提交人工裁定。

**CF 封顶的应用顺序**：L1 → L2 加权 → **CF 封顶最后应用**。CF2/CF4 封 D1 上限为等级 1，CF3 封 D2/D3 上限为等级 1，CF1 封总分为 30（需人工确认后才生效）。

### 4.7 与 tier 的耦合

- T1 任务：`objective_dims == [D1, D2]`——两层全开，D1 和 D2 都同时有 L1 和 L2。
- T2/T3 任务：最多一个客观维度——大部分维度只有 L2（L1 恒为 1.0，纯扣分制）。
- 全 tier：非内部一致性检查点的数值型字段必须有真值路径——否则 L1 是假的，silently degrades to 1.0。

### 4.8 迁移状态（注意：影响跨任务分数可比性）

当前两层分层状态：

| 状态                    | 任务                   |
| --------------------- | -------------------- |
| 已两层结构化，lint 0 warning | **S9、S2、S10、S11**    |
| 未迁移（lint WARN）        | S1、S3、S4、S5、S6、S7、S8 |

未迁移的任务，其客观维度的 `judge_notes.md` 还没有按"第一层/第二层"分列，评分官账本可能仍在双罚（L1 已扣过、L2 又扣一次）。**跨任务分数不可直接比较**——同一份作品放到 S9 和放到 S7 里跑，分数基准不同。

S2 有一个例外决策：保留了 6 个指标的 grounding+methodology 双标签（拆开会让 D2 塌到只剩一个检查点），两层只在评分官层面生效。

还有一个已知陷阱：**用旧 judge_notes 跑出来的旧 ledger，不能用新汇总器重算**——旧 ledger 是按旧的（未分层）锚点登记的，新汇总器的减法合成会在 D1/D2 上双罚。要获得真正的两层效果，必须用新 judge_notes 重跑盲评评分官。

### 4.9 一个完整的算例

假设一份 S9 作品：

- D1 权重 40
- citation_policy: sample, d1_objective_weights: {citation: 0.3, grounding: 0.7}
- L1：引用核验 4 条全过 → `verified_fraction = 1.0`。12 个 grounding 检查点中 10 个通过，按分组等权 → `grounding_pass_fraction_weighted = 0.833`。
- `grounding_objective = 0.3 × 1.0 + 0.7 × 0.833 = 0.883`
- L2：评分官 1 扣了"来源缺日期"(minor −0.5)，评分官 2 扣了"关键数据用第二级来源未回溯第一级"(major −1) + "两处基准价未标 as-of"(minor −0.5)。两官 L2 均值 = (0.5 + 1.5) / 2 = 1.0 点。
- `fraction = max(0, 0.883 − 1.0/4) = max(0, 0.883 − 0.25) = 0.633`
- `weighted_points = 0.633 × 40 = 25.32`

如果没有两层模型（旧行为），D1 的含金量扣分会直接被 0.883 的基线覆盖掉，最终 D1 = 0.883 × 40 = 35.32——整整高出 10 分。两层模型让含金量问题真正影响了分数。

---

## 5. 引擎层：按流水线顺序

引擎层包含 10 个技能，对应流水线上的 10 个环节。每个技能是一个"任务无关的引擎"——它读容器规范、执行自己的工作、写出结构化产物，但不知道任何具体任务的知识。

以下按评测流水线顺序介绍每个引擎的核心能力。每个引擎的详细输入输出格式见对应的 SKILL.md。

### 5.1 eval-task-specs —— 规范加载器

**职责**：告诉所有下游引擎去哪里读规范、怎么读。

它本身不"做"任何计算——它是一个索引：按 `task_id` 在 `registry.json` 定位容器路径，然后根据调用方身份告知该读容器中的哪个文件。共享原则（六维度定义、扣分量尺、CF 规则等）统一从 `rubrics/constitution.md` 读取。

**关键约束**：只读 `status: frozen` 的容器。

### 5.2 eval-groundtruth —— 基准真值构建

**职责**：产出所有作品共享的"正确答案"，由确定性 Python 脚本计算——绝不靠 LLM 估算。

**入口**：`scripts/gt_dispatch.py` 是唯一调度器。它读容器的 `gt_recipe.yaml`，按 `kind` 分派三条路径：

**路径 A —— `kind: calculator`**：调用共享计算器脚本（如 `momentum_backtest.py`、`bond_analytics.py`、`data_retrieval_snapshot.py`），计算器写出一份 `groundtruth.json`。调度器随后强制校验 `self_check.passed`——如果计算器自检未通过（或缺失自检块），调度器拒绝继续，不允许在未验证的真值上打分。

当前可用计算器（12 种方案，9 种有脚本 + 3 种无脚本）：

| 方案                      | 脚本                           | 用途                                     |
| ----------------------- | ---------------------------- | -------------------------------------- |
| bond_analytics          | `bond_analytics.py`          | S3 债券定价（需要债券参数 JSON）                   |
| brinson                 | `brinson.py`                 | 共享 Brinson-Fachler 行业归因（S4 可选升级）       |
| linear_shock            | `linear_shock.py`            | S5 因子冲击下的组合盈亏                          |
| momentum_backtest       | `momentum_backtest.py`       | S2 月度动量回测                              |
| fund_metrics            | `fund_metrics.py`            | S7 基金风险指标（收益/波动/夏普/回撤）                 |
| client_metrics          | `linear_shock.py --metrics`  | S8 客户组合指标（集中度、费率、股债比）                  |
| data_retrieval_snapshot | `data_retrieval_snapshot.py` | S9 数据获取（Yahoo Finance live + 东方财富交叉验证） |
| financial_data_snapshot | `financial_data_snapshot.py` | S10 财务数据（内嵌冻结答案键，网络无关）                 |
| news_search_snapshot    | `news_search_snapshot.py`    | S11 消息面取数（内嵌冻结答案键，网络无关）                |

**路径 B —— `kind: internal_consistency`**：不算数值真值，写出空的 `values: {}`。一致性交给评分器的 `reconcile` / `consistency` / `monotonic` 类型检查点完成（如 S1 杜邦恒等式、S4 三腿勾稽）。

**路径 C —— `kind: user_snapshot`**：用户提供快照则原样写入 `values`；否则写 `values: null`，相关检查点记 NA（如 S6 宏观快照）。

**GT 输入缺失硬停规则**：`calculator` 或 `user_snapshot` 方案下，如果容器声明了非 optional 的 `inputs_required` 文件缺失（既不在 `fixtures/` 也不在运行时提供），编排器**必须停止评测并报错**——不能静默把检查点记 NA。静默 NA 让评测"看起来在跑"但客观锚定全空，比报错更危险。

当前已知债务：S1–S8 为 `origin: legacy-v1` 迁移件，A 类任务（S2/S3/S5/S7/S8）的 GT 输入数据尚未打包，在回填前确定性数值检查点将触发硬停。S9/S10/S11 为后续新增，GT 可用。

### 5.3 eval-extractor —— 字段提取

**职责**：把一篇自由格式的候选作品转成结构化 JSON——每个检查点字段标注 `value | MISSING | AMBIGUOUS`，每一条引用登记来源与日期，每一个工具调用登记用途与层级。

**脚本**：`scripts/validators.py` 对原始提取做单位标准化——`%` 转 fraction（3.41% → 0.0341）、`bp` 按字段类型决定是否转 fraction——并标记超出合理范围的值。

**三条铁律**：

1. 只报告"作品呈现了什么"，绝不推断或填补缺失值。
2. 不在此处判断对错（那是评分器的工作）。
3. 不在此处评判质量（那是评分官的工作）。

**输出**：`normalized.json`，含 `extracted`（逐字段）、`citations`（引用清单）、`tool_inventory`（工具调用清单）、`validator_flags`。

### 5.4 eval-checkpoint-grader —— 确定性检查点评分

**职责**：**运行脚本**做逐字段容差比对——绝不靠肉眼判断。这是全框架中唯一产出"这个数对不对"结论的环节。

**脚本**：`scripts/grade_checkpoints.py`。输入 `checkpoint_schema` + `normalized.json` + `groundtruth.json`，输出 `det_results.json`。

**支持的检查点类型**（14 种，完整实现）：

- **标量**：`number`、`pct`、`bp`、`yr`、`ratio`——按容差比对 GT
- **基数**：`count_eq`、`count_min`、`sum_to`、`present`——计数/求和/存在性，无需 GT
- **结构**：`vector`（字典或列表逐元素容差）、`set_match`（标识符集合匹配，tol = 允许漏掉几个）
- **内部一致性**：`reconcile`（自报值与公式求值的差 ≤ tol）、`consistency`（两表达式求值的差 ≤ tol）、`monotonic`（二维网格行列单调性）
- **抽样核验**：`sample_verify`（抽样单元格 vs GT 参考值）

**两种评分模式**：

- **binary**（默认）：每个检查点通过 = 1、失败 = 0。
- **deviation**（偏差模式）：连续衰减——偏差 ≤ tol 时得满分 1.0，超出部分线性衰减至 0。用于需要评价数值精度连续梯度的任务。

**精度对齐（tol=0 时的陷阱）**：当 `tol=0` 时，脚本会自动将学生值 round 到 GT 的小数位数再比对。例如 GT=9.30（2dp）、学生=9.301（3dp）→ 自动 round 到 9.30 → 通过。但这个机制有一个已知缺陷：`repr(728.80)` 是 `'728.8'`（末尾零被丢弃），GT 读取为 1dp → 错误答案 728.84 被 round 到 728.8 → 误判通过。每个 tol=0 的检查点应声明 `decimals` 字段来固定精度，避免此缺陷。**对于 `pct` 类型**：因为 validators.py 已将百分比转为 fraction，`decimals` 应声明在 fraction 精度上（2 位小数百分比 → `decimals: 4`）。

**CF5 强制应用**：超容差 → 该检查点记 0 分，不给部分分。在偏差模式下 CF5 通过连续衰减自然生效。

**输出**：`det_results.json`，含逐检查点状态、`pass_fraction`、`grounding_pass_fraction`、`methodology_pass_fraction`、`cf5_hits`，偏差模式下额外含 `deviation_fraction` 等连续分数。

### 5.5 eval-citation-verifier —— 引用核验

**职责**：通过网络搜索和网页抓取，验证作品中的每条引用是否真实存在、是否支撑所述主张、数据是否新鲜。

**抽样策略**（由容器的 `citation_policy` 声明）：

- `full`：核验每一条引用（如 S6 宏观观点，引用真实性是核心评测维度）
- `sample, k`：核验 k 条引用，优先承重性量化主张上的引用（如 S1、S5、S9）
- `per_cell_sample`：对比表中每只基金至少抽样一条（如 S7）
- `none`：跳过本通道

**判定四态**：

| 判定                     | 含义                           |
| ---------------------- | ---------------------------- |
| `verified`             | 来源存在、支撑主张、数据新鲜（或不需要新鲜度检查）    |
| `unsupported`          | 来源存在，但没找到所述主张                |
| `broken`               | 链接失效或无法定位，但标识符看似合理           |
| `fabricated-candidate` | 确凿的捏造证据——不存在的标识符，或来源说法与被引相矛盾 |

**关键纪律**："无法核验"（broken / 网络故障 / 付费墙）是 D1 质量问题，绝不等于捏造。`fabricated-candidate` 只在有确凿证据时标记——不存在的标的代码、来源的说法与被引矛盾。

**输出**：`citation_audit.json`，含逐条引用判定、`verified_fraction`（已核验占比）、`uncited_claim_ratio`（无引用的量化主张占比）。`verified_fraction` **永远要有值**——即使作品一条引用都没有也要写 `0.0`，不能留空或省略。如果留空，汇总器会回退为只用 grounding 通过率，把引用权重整段丢弃——后果是"答案全对但一个来源都没给"的作品拿到满额 D1，恰好与 D1 的本意相反。实测（S11）表明这会导致维度得分差 30%。

### 5.6 eval-rubric-judge —— 盲评量规评分

**职责**：在**隔离模式**下对一份作品在 D1-D6 六个维度上以扣分制打分。

**隔离是关键**：到盲评这一步，编排主 Agent 的上下文里已经装着真值、检查点结果和引用审计。让同一个上下文去"自我屏蔽"不可靠——因此每一轮盲评应派发给一个**全新的子代理**（`eval-rubric-judge` agent，`tools: Read` 只读），只喂盲评载荷。子代理的上下文里没有客观结果可供泄漏。

**盲评载荷**（评分官唯一可见的内容）：

- `prompt_text`：任务给候选人的逐字提示
- `rubric_weights`：六维度权重（让评分官知道什么维度重要）
- `work_text`：盲评作品全文（去除了系统名称和标签）
- `tool_evidence`：作品自带的工具调用轨迹和提取器的 tool_inventory
- `judge_notes`：容器的家族扣分锚点（不含标准答案数值）

**载荷中绝不出现**：`det_results`、`citation_audit`、`groundtruth.json`、其他作品内容、作者身份。

**评分程序**：

1. 加载宪法和 judge_notes
2. 对每个维度从 4 分起评
3. 逐条登记扣分项 `{issue, severity, points, evidence}`
4. 维度等级 = `max(0, 4 − Σ扣分)`

**返回**：`{levels, deductions, rationale, injection_detected, blind_isolation}`。`blind_isolation` 恒为 `"subagent"`。

**隔离三路径**（按强度降序）：

1. **Workflow 岛**：由 `orchestration/eval-judge.workflow.js` 的代码构造 GT-free 载荷并启动两个盲评子代理——隔离由代码**结构性保证**。
2. **Task 子代理**：通过 `Task(subagent_type: "eval-rubric-judge")` 派发，隔离由**子代理的空白上下文**保证。
3. **自我隔离（self）**：编排 Agent 在同一个上下文里"假装没看到"客观结果——最弱，仅作最后回退。

### 5.7 eval-cf-auditor —— CF 审计

**职责**：从检查点结果、引用审计和执行证据中判定该作品是否触发了 CF1-CF5。

只启用容器的 `cf_rules` 子集中声明的规则。CF5 已由评分器处理，此处不重新推导。

**CF1 的特殊处理**：CF1（捏造）标记以 `confirmed: false, needs_human: true` 发出——附带确凿证据但**不自动封顶**，必须人工确认后才生效。因为对竞品做出错误的捏造判定，是本框架可能犯下的最具破坏性的评分错误。

**CF3 的证据优先级**（对未暴露工具轨迹的竞品尤为重要）：(1) 有执行/工具轨迹 → 不构成 CF3；(2) 无轨迹但有可机检产物能复现所宣称数字 → 不构成 CF3；(3) 两者皆无 → CF3 触发。

**输出**：`cf_flags.json`，含各规则的触发情况、证据、confirmed 状态和 proposed_effect。

### 5.8 eval-aggregator —— 汇总与报告

**职责**：将各通道的产物合并为 0-100 总分和六维度详细拆解。所有运算在 `scripts/aggregate.py` 中完成——运行脚本，不手工算。

**两个模式**：

- **单作品模式**（`--bundle`）：为一份作品产出一份 `scorecard.json`。
- **报告模式**（`--report`）：读入全部 scorecard，产出统一格式 `report.md`。

**评分流程**（`aggregate.py` 内部）：

1. 加载 `det_results.json`、`citation_audit.json`、`judge_1/2.json`、`cf_flags.json`、`taskspec.json`
2. 从 judge 账本推算有效等级（`4 − Σpoints`，[0,4] 钳制）——账本是真相源。钳制命中 = `ledger_warnings`
3. 对每个维度：L1 基线（仅 objective_dims）→ 减去 L2 扣分均值 / 4 → 加权 → 维度得分
4. 对每个维度：两位评分官分歧 >1 级 → `needs_review`
5. 应用 CF 封顶：CF2/CF4 → D1 ≤ 1；CF3 → D2, D3 ≤ 1；CF1 → 总分 ≤ 30（仅 `confirmed: true` 的）
6. 有效分母：`scored_weight` = 实际参与计分的权重之和；`unscored_dims` = 未评分维度；`score_normalized` = 归一化分

**评分卡关键字段**：`score`（总分）、`dimensions`（六维度详细）、`grounding_objective`（D1 实测基线）、`cf_applied`（已应用 CF）、`cf_pending_human`（待人工确认 CF1）、`needs_review_dims`（需复核维度）、`ledger_warnings`（账本校验警告）、`headline`（一行结论）。

报告模式产出的 `report.md` 固定六个段落，详见 §9.1。

### 5.9 eval-taskspec-lint —— 容器校验

**职责**：在容器被冻结之前，校验它是否符合宪法的可参数化面约束和 tier 相干性规则。这是治理关口——容器在此通过，引擎即可机械消费。

**脚本**：`scripts/spec_lint.py`。全量检查项：

| 类别                      | 检查项                                                           | 严重度          |
| ----------------------- | ------------------------------------------------------------- | ------------ |
| 注册表一致性                  | task_id / tier / version 在 registry 和 spec 中一致                | ERROR        |
| 来源标记                    | `provenance.origin` 必须为 `legacy-v1`                           | ERROR        |
| 权重                      | 恰好 D1-D6，整数，合计 100，D6 ≥ 5                                     | ERROR        |
| 客观维度                    | `objective_dims ⊆ {D1, D2}`                                   | ERROR        |
| 客观基础                    | D1 为客观时需有 grounding 检查点或引用策略                                  | WARN         |
| D2 回退                   | D2 为客观时无 methodology 检查点则回退全量通过率                              | WARN         |
| 检查点类型                   | 所有字段类型必须在封闭类型表内                                               | ERROR        |
| GT 路径                   | 数值/结构检查点需有 GT 来源（禁止伪确定性）                                      | ERROR        |
| 计算器绑定                   | calculator sidecar / script 文件存在                              | ERROR        |
| CF 阈值                   | CF2 未引用率阈值在宪法区间 [0.15, 0.40] 内                                | ERROR        |
| d1_objective_weights    | 若声明则 {citation, grounding} 均为数值且和为 1                          | ERROR        |
| grounding_group_weights | 组权重和为 1，全名在 schema 中存在且 grounding:true                        | ERROR / WARN |
| judge_notes 泄漏          | 不得出现"正确答案：N"等标准答案数值                                           | ERROR        |
| judge_notes 两层结构        | 客观维度需有 第一层/第二层 标题（未迁移为 WARN）                                  | WARN         |
| 加分禁止                    | 不得出现 `→ +N` 形式的加分锚点                                           | ERROR        |
| tier 相干性                | T1=dual-objective+calculator, T2=≤1obj+验证路径, T3=≤1obj+研判主导≥40 | ERROR        |
| 类型声明                    | `engine_requirements.checkpoint_types` 覆盖所有使用中的类型             | WARN         |

### 5.10 eval-orchestrator —— 编排规程

**职责**：定义评测的**完整控制流**、组装规则和护栏。它是本手册 §7（编排与隔离机制）的规范正本。各环节的散文程序见 `eval-orchestrator/SKILL.md`，命令入口见 `commands/eval-judge.md`。两者是镜像关系——orchestrator 是完整的规程定义，command 是瘦的调用参数模板。本文 §7 是它们的结构化摘要。

---

## 6. 任务容器（架构第三层）

### 6.1 容器文件清单

每个容器文件夹包含以下文件。`spec.yaml`、`checkpoint_schema.yaml`、`judge_notes.md` 和 `provenance.json` 是所有任务都必需的四件套；`gt_recipe.yaml` 在 T1/T2 任务中需要；其余为可选。

| 文件                       | 必需       | 消费者                               | 内容                                                                                               |
| ------------------------ | -------- | --------------------------------- | ------------------------------------------------------------------------------------------------ |
| `spec.yaml`              | 是        | orchestrator / aggregator / judge | 任务标识、tier、prompt_text（逐字）、rubric_weights、objective_dims、citation_policy、cf_rules、required_inputs |
| `checkpoint_schema.yaml` | 是        | extractor / grader / aggregator   | 逐字段的类型、容差、grounding/methodology 标记                                                               |
| `gt_recipe.yaml`         | T1/T2 需要 | groundtruth 调度器                   | GT 构建方式（calculator / internal_consistency / user_snapshot）、计算器绑定                                 |
| `judge_notes.md`         | 是        | rubric-judge                      | 逐维度"情形 → 严重度"扣分锚点；禁止含标准答案数值                                                                      |
| `provenance.json`        | 是        | 治理                                | 来源标记、版本、容器哈希                                                                                     |
| `extraction_notes.md`    | 可选       | extractor                         | 纯格式/单位约定；不得改 schema                                                                              |
| `fixtures/`              | 若自包含     | groundtruth / validators          | GT 输入数据文件                                                                                        |
| `validators/`            | 可选       | grader / cf-auditor               | 插件验证器（遵循验证器契约）                                                                                   |

### 6.2 关键字段参考

#### spec.yaml 核心字段

| 字段                        | 说明                      | 示例                                                         |
| ------------------------- | ----------------------- | ---------------------------------------------------------- |
| `rubric_weights`          | 六维度整数权重，合计 100          | `{D1: 40, D2: 20, D3: 15, D4: 0, D5: 0, D6: 25}`           |
| `objective_dims`          | 哪些维度有 GT 可测（⊆ {D1, D2}） | `[D1, D2]` 或 `[D1]`                                        |
| `objective_scoring_mode`  | 可选，默认 `binary`          | `binary` 或 `deviation`                                     |
| `citation_policy`         | 引用核验策略                  | `{mode: sample, k: 4}`                                     |
| `cf_rules`                | 启用的 CF 规则子集             | `[CF1, CF4, CF5]`                                          |
| `cf_thresholds`           | CF 阈值覆盖                 | `{CF2_uncited_ratio: 0.25}`                                |
| `d1_objective_weights`    | D1 客观内部权重覆盖             | `{citation: 0.3, grounding: 0.7}`                          |
| `grounding_group_weights` | grounding 检查点分组加权       | `{Q1_: {base_price: 0.4, latest_price: 0.4, answer: 0.2}}` |
| `tier`                    | 可核验程度                   | `T1` / `T2` / `T3`                                         |

#### checkpoint_schema.yaml 字段属性

| 属性                    | 说明                                         |
| --------------------- | ------------------------------------------ |
| `type`                | 14 种封闭类型之一（见 §6.4）                         |
| `tol`                 | 容差值；`tol=0` 为精确匹配                          |
| `rel`                 | `true` 时为相对容差（偏差 / `                       |
| `decimals`            | tol=0 时固定精度（防末尾零被 repr 丢弃）                 |
| `grounding`           | `true` → 计入 D1 的 grounding_pass_fraction   |
| `methodology`         | `true` → 计入 D2 的 methodology_pass_fraction |
| `target`              | `count_eq` / `count_min` / `sum_to` 的目标值   |
| `formula`             | `reconcile` 的表达式                           |
| `lhs` / `rhs`         | `consistency` 的左右表达式                       |
| `row_dir` / `col_dir` | `monotonic` 的增减方向                          |
| `deviation_scoring`   | 偏差模式配置 `{max_dev}`                         |
| `na`                  | 条件性 NA 声明                                  |
| `validator`           | 专用验证器派发                                    |

### 6.3 已注册容器一览

截至当前，11 个任务：

| ID  | 标题                  | 家族                      | tier | GT 方式                          | 权重特征                                |
| --- | ------------------- | ----------------------- | ---- | ------------------------------ | ----------------------------------- |
| S1  | 杜邦分析与同业对比           | equity-fundamentals     | T2   | 内部一致性                          | D1:20 D2:20 D3:15 D4:25 D5:10 D6:10 |
| S2  | 量化策略回测              | quant-backtest          | T1   | momentum_backtest              | 含 D4/D5                             |
| S3  | 固定收益工具分析            | fixed-income-analytics  | T1   | bond_analytics                 | D2 权重最高                             |
| S4  | 多因子业绩归因             | multifactor-attribution | T2   | 内部一致性 (+ 可选 brinson)           | 三腿勾稽                                |
| S5  | 风险压力测试              | risk-scenario           | T2   | linear_shock                   | T2                                  |
| S6  | 多来源宏观观点             | macro-view              | T3   | user_snapshot                  | citation_policy: full               |
| S7  | 基金筛选与对比             | fund-screen             | T2   | fund_metrics                   | 含 sample_verify                     |
| S8  | 客户组合诊断              | portfolio-advisory      | T3   | client_metrics                 | T3 研判主导                             |
| S9  | 数据获取稳定性测试           | data-retrieval          | T1   | data_retrieval_snapshot (live) | D1:40 D2:20 D3:15 D4:0 D5:0 D6:25   |
| S10 | 三家公司三年财务数据          | data-retrieval          | T1   | financial_data_snapshot        | D1:60 D2:12 D3:8 D4:0 D5:0 D6:20    |
| S11 | 金融消息面 Web Search 取数 | news-data-retrieval     | T1   | news_search_snapshot           | T1, 含分组加权                           |

完整字段以 `taskspecs/registry.json` 为准。

### 6.4 检查点类型表

标量 `number` `pct` `bp` `yr` `ratio` · 基数 `count_eq` `count_min` `sum_to` `present` · 结构 `vector` `set_match` · 内部 `reconcile` `consistency` `monotonic` · 抽样 `sample_verify`。

新增类型是**引擎发布级**变更（需改 `grade_checkpoints.py` + 测试），不允许逐任务分叉。

### 6.5 容器生命周期

```
draft → lint 通过 → frozen（冻结后不可再改）
```

- **draft**：容器作者起草阶段，可以随意修改。
- **lint 通过**：`spec_lint.py` 0 错误通过——容器形式上合法。
- **frozen**：容器内容锁定，`container_hash` 写入 `provenance.json`。冻结后如需修改，应升级 `version` 并更新 `provenance.json` 记录变更。

编排器**只评 frozen 容器**。遇到非 frozen 或无容器的 task_id 时，停止并告知用户——不临场自拟规范。容器冻结必须在接收候选作品**之前**完成（反作弊属性）。

---

## 7. 编排与隔离机制

### 7.1 混合式编排：什么在主对话、什么进 Workflow 岛

评测流程的大部分环节在**主对话内联**完成——编排 Agent 直接加载对应的 `eval-*` 技能、运行脚本、读写文件。这样避免了不必要的子代理开销。

唯一需要**机器强制隔离**的核心是逐作品的这段：

```
确定性检查点评分 → 引用核验 + 载荷纯净性校验 → 盲评 ×2 → 落盘 judge_1.json / judge_2.json
```

这一段必须进 Workflow 岛（或由 Task 子代理替代），原因是：**盲评载荷的构造和派发必须在从未见过真值和检查点结果的上下文中完成**。如果编排 Agent 在主对话中组装盲评载荷——它的上下文里已经有 `groundtruth.json` 和 `det_results.json`——隔离就退化为"请假装没看到"的纪律，而非结构性保证。

Workflow 岛的 JavaScript 代码（`orchestration/eval-judge.workflow.js`）解决了这个问题：代码从 `spec.promptText`、`rubricWeights`、`judgeNotes`、`workText` 和 `toolEvidence` 组装盲评载荷——**这些变量中从不含 `gtPath`、`det_results` 或 `citation_audit` 的内容**。隔离是代码保证的，不只是 prose 约定的。

### 7.2 三条隔离路径与强度

| 路径             | 触发条件                                   | 隔离保证                               | 强度            |
| -------------- | -------------------------------------- | ---------------------------------- | ------------- |
| **Workflow 岛** | `/eval-judge` 命令（有 Workflow 工具）        | 代码结构保证——载荷由 JS 代码组装，变量集与 GT 变量集不交叠 | 最强            |
| **Task 子代理**   | `eval-judge` agent 直接调用（无 Workflow 工具） | 子代理保证——全新空白上下文，只接收 GT-free 载荷      | 强             |
| **自我隔离**       | Task 工具也不可用                            | 自律——编排 Agent 在已有 GT 的上下文中"假装不记得"   | 最弱（仅在最后回退时使用） |

**为什么 agent 没有 Workflow 工具**：`agents/eval-judge.md` 的 tools 列表不含 Workflow。这是设计使然——/eval-judge 命令走 Workflow 岛（命令有 Workflow 工具），而 agent 作为子代理被直接调用时走 Task 子代理路径。两条路径的隔离强度等价，只是实现方式不同。

### 7.3 Workflow 岛内部结构

`orchestration/eval-judge.workflow.js` 分三个 phase：

**Phase 1: Grade+Cite**。每个作品并行执行：(a) 运行 `grade_checkpoints.py` 做确定性评分（读取 GT）；(b) 如果 `citationPolicy != none`，执行引用核验和载荷纯净性校验。两个子步骤独立并行。

**Phase 2: Blind Judge**。对每个作品派发两个独立的盲评子代理——它们接收的提示只含 rubric 信息、作品文本和 purity-checked 的工具证据。每个子代理必须返回结构化的 `JUDGE_RESULT`（六维度的 deduction ledger）。超过一级的分歧标记为 `needs_review`。

**Phase 3: Persist**。将两份盲评账本写入 `judge_1.json` / `judge_2.json`，供汇总器消费。

全流程中任何一步失败都会触发 `hardStop`——不产出部分报告。

### 7.4 载荷纯净性校验——防盲评幻觉的闸门

这是编排层三条护栏中最容易被跳过的，也是导致盲评偏差的最常见根因。在盲评载荷派发前**必须**执行：

1. **逐条溯源**：将 `tool_evidence` 中的每条事实性陈述（如"使用了 style_metrics 字典映射""引用表含 6 条公众号文章"）与**当前作品**的 `work_text` 逐一匹配，确定每条陈述都能在 `work_text` 中找到对应的原文。
2. **删除不匹配项**：任何在 `work_text` 中找不到的陈述必须从 `tool_evidence` 中删除——无论它看起来多么合理。删除项记录到 `dropped_claims`。
3. **禁止跨作品复用**：每份作品的载荷独立组装，只参考该作品自己的文本。
4. **校验记录**：写入 `payload_audit.json`，含 `verified_claims`、`dropped_claims` 和 `isolation_confirmed`。

载荷组装错误的两种典型后果：(1) `tool_evidence` 混入相邻作品的特征 → 子代理基于不存在于本作品中的"缺陷"误增扣分；(2) 引导性提示使用了错误作品的特征 → 子代理漏判真实缺陷。两者都会严重扭曲评分。

### 7.5 work_registry.json —— 防映射错位的闸门

在接收作品后、执行任何下游操作前，**必须**先构建 `work_registry.json`：

```json
{
  "task_id": "S7",
  "works": [
    {"blind_label": "Work A", "source_type": "upload", "original_filename": "model1_s7.md", "path": "/abs/path/to/model1_s7.md"},
    {"blind_label": "Work B", "source_type": "upload", "original_filename": "model2_s7.md", "path": "/abs/path/to/model2_s7.md"}
  ],
  "label_order": ["Work A", "Work B"]
}
```

规则：

1. 按用户提交顺序分配标签——标签顺序 = 提交顺序，非文件名字母序。
2. 后续所有操作**必须**通过注册表查询 `blind_label → path`，不得靠"字母序 = Model 编号"这种隐含假设。
3. 注册表一旦写入，本次运行中不可变。

跳过注册表、直接派发盲评是"file_path → blind_label 映射错位"的主要根因——分数被归到错误的作品标签下，且这种错误比载荷纯净性问题更隐蔽（分数与所评文本是匹配的，只是标签归错了）。

### 7.6 needs_review 触发点汇总

以下情况会触发 `needs_review`（需人工复核）：

| 触发条件                                 | 位置                    |
| ------------------------------------ | --------------------- |
| 两位盲评官在同一维度上等级差 > 1                   | Workflow 岛 / 汇总器      |
| ledger 符号错误（负 points）或等级超出 [0,4] 被钳制 | 汇总器 `ledger_warnings` |
| CF1 标记（始终 `needs_human: true`）       | CF 审计器                |
| GT 自检未通过                             | 基准真值构建阶段硬停（不产生分数）     |
| 作品间 `scored_weight` 不一致              | 报告模式自动标注              |

---

## 8. 入口与调用方式

### 8.1 三种入口及其能力差异

| 入口                 | 形式                                        | Workflow 岛      | 盲评隔离        | 适用场景            |
| ------------------ | ----------------------------------------- | --------------- | ----------- | --------------- |
| `/eval-judge` 命令   | 用户输入 ` /eval-judge S9 workA.md workB.md`  | ✅ 完整 Workflow 岛 | 最强（代码结构保证）  | **首选**——标准评测运行  |
| `eval-judge` agent | 在对话中 `Agent(subagent_type: "eval-judge")` | ❌ 无 Workflow 工具 | 强（Task 子代理） | 在其他 agent 中嵌入评测 |
| 直接调用单个 skill       | `Skill("eval-groundtruth")`               | ❌ 无             | 无（仅运行单环节）   | 调试或分步重跑         |

### 8.2 典型调用形式

```
# 评测一份作品
/eval-judge S9 /path/to/candidate_answer.md

# 同时评测两份作品并对比
/eval-judge S4 model_a_answer.md model_b_answer.md

# 评测粘贴的文本（先保存为临时文件）
/eval-judge S3 /tmp/bond_answer.md
```

命令内部自动完成 13 个步骤：解析插件根 → 确认容器 frozen → 解析 Python → 建立 work_registry → 创建运行目录 → 加载规范 → 构建 GT → 逐作品提取 → 启动 Workflow 岛 → CF 审计 → 汇总评分卡 → 生成报告 → 呈现。

---

## 9. 输出规范

### 9.1 report.md 六段结构

无论一份还是多份作品，报告格式完全一致——单份作品就是一张只有一行的排名表：

**① 总分与排名**：排名 × 作品 × 总分表，含已应用 CF、待确认 CF、一行结论。各作品有效分母不一致时附归一化分列。

**② 各维度评分总览**：D1-D6 × 作品的等级矩阵；每格标注来源（两层 / 盲评 / 盲评回退 / 缺失）和封顶标记。`*` 表示两官分歧 >1 级需复核。

**③ 各维度详细评分（D1-D6）**：对每个维度，逐作品列出等级与得分、客观扣分来源（未通过检查点）、两位评分官的逐条扣分记录（扣多少 / 严重度 / 问题 / 证据）、CF 封顶信息、复核标记。每个维度末尾附学生答案原文引用区块。

**④ 确定性检查点明细**：逐检查点 × 逐作品的 通过/未通过（含偏差值）/NA 表。

**⑤ 引用核验与致命缺陷**：逐作品的 grounding 值、已应用 CF、待确认 CF。

**⑥ 结论**：排序（单作品为总分陈述）、前两名分差、决定性维度、不确定性说明（分差 <3 或需复核时标注暂定）。

### 9.2 运行目录布局

```
eval-runs/<task-id>-<UTC-timestamp>/
├── work_registry.json          # 盲评标签 ↔ 文件路径（不可变）
├── taskspec.json                # 合并后的任务规范（供脚本消费）
├── checkpoint_schema.json       # 检查点模式 JSON（YAML 转换产物）
├── groundtruth.json             # 基准真值（所有作品共享）
├── report.md                    # 最终报告
├── Work_A/
│   ├── normalized.raw.json      # 原始提取
│   ├── normalized.json          # 经校验的提取
│   ├── det_results.json         # 检查点评分结果
│   ├── citation_audit.json      # 引用核验结果
│   ├── payload_audit.json       # 载荷纯净性校验记录
│   ├── judge_1.json             # 盲评第一轮
│   ├── judge_2.json             # 盲评第二轮
│   ├── cf_flags.json            # CF 标记
│   └── scorecard.json           # 评分卡
├── Work_B/
│   └── ...
```

每个环节都可以基于前一环节的产物单独重跑——修改量规权重只需重跑汇总器，不需要重跑整个流程。

### 9.3 scorecard.json 关键字段速查

| 字段                                                        | 说明                                                                       |
| --------------------------------------------------------- | ------------------------------------------------------------------------ |
| `score`                                                   | 总分 /100                                                                  |
| `scored_weight`                                           | 实际参与计分的权重之和（≠100 时说明有维度未评分）                                              |
| `score_normalized`                                        | 归一化分 = `score / scored_weight × 100`                                     |
| `unscored_dims`                                           | 未能评分的维度列表                                                                |
| `dimensions.<D>.level`                                    | 0-4 展示等级                                                                 |
| `dimensions.<D>.fraction`                                 | 连续分数比例（加权前的值）                                                            |
| `dimensions.<D>.source`                                   | `layer1+layer2` / `judge_mean` / `judge_mean_fallback` / `judge_missing` |
| `dimensions.<D>.objective_detail.layer1_fraction`         | L1 实测基线（仅客观维度）                                                           |
| `dimensions.<D>.objective_detail.layer2_deduction_points` | L2 扣分均值（仅客观维度）                                                           |
| `dimensions.<D>.judge_1.deductions`                       | 评分官 1 逐条扣分记录                                                             |
| `dimensions.<D>.judge_2.deductions`                       | 评分官 2 逐条扣分记录                                                             |
| `cf_applied`                                              | 已应用的 CF 规则列表                                                             |
| `cf_pending_human`                                        | 待人工确认的 CF1                                                               |
| `needs_review_dims`                                       | 两官分歧 >1 级的维度                                                             |
| `ledger_warnings`                                         | 账本校验警告（负 points / 钳制）                                                    |

---

## 10. 安装、环境与路径约定

### 10.1 安装（本地 marketplace）

```bash
claude plugin marketplace add <本仓库路径>
claude plugin install eval-judge@eval-judge-marketplace
```

安装后暴露：

- 命令 `/eval-judge`（首选入口，有 Workflow 工具 → 完整 Workflow 岛隔离）
- agent `eval-judge`（编排器，无 Workflow 工具 → 盲评走 Task 子代理）
- agent `eval-rubric-judge`（隔离盲评子代理，`tools: Read` only）
- 11 个 `eval-*` 技能

### 10.2 Python 依赖

```bash
pip install QuantLib pandas numpy openpyxl pyyaml
```

- `pyyaml`：硬依赖——`gt_dispatch.py` 和 `spec_lint.py` 读取 YAML 规范文件。
- `QuantLib`：仅 S3 债券计算器使用。
- `openpyxl`：仅 xlsx 格式的 fixture 文件需要。
- 本仓库统一使用 `python`（非 `python3`），面向 Windows 环境。
- `gt_dispatch.py` 通过 `sys.executable` 调用子计算器，保持解释器一致。

### 10.3 $CLAUDE_PLUGIN_ROOT 约定（关键）

插件运行时，**工作目录是用户的当前目录**，但宪法、容器和计算器脚本在**插件目录**中。因此：

1. 先通过 `echo "$CLAUDE_PLUGIN_ROOT"` 获取插件根 → `<ROOT>`。
2. **Read 不展开环境变量**——必须用绝对路径：`Read("<ROOT>/rubrics/constitution.md")`。
3. **Bash 会展开**——脚本调用中直接写变量：`python "${CLAUDE_PLUGIN_ROOT}/skills/..."`。
4. **运行产物**（`eval-runs/`、`run/`）写在用户的 cwd，而非插件目录（插件目录通常只读）。
5. 如果 `$CLAUDE_PLUGIN_ROOT` 为空（直接从仓库 checkout 运行而非插件安装），用仓库根作为 `<ROOT>`。

### 10.4 源目录与插件缓存同步（重要警告）

插件安装在 `C:\Users\<user>\.claude\plugins\cache\eval-judge-marketplace\eval-judge\<version>\`。这是一个**普通文件副本**，不是 git checkout。

**修改源目录（`Documents/eval-judge/`）不会自动同步到插件缓存**——`/eval-judge` 命令加载的是缓存副本。改动后需要手动同步或重新安装。更糟的是，改动可能**双向飘移**——源目录在一个方向上累积了修改，缓存目录在另一个方向上累积了不同的修改。

**安全做法**：

1. 用 `mtime` 逐文件审计源和缓存的差异。
2. 确认同步方向后再复制。
3. 或者统一在源目录开发，完成后重新安装插件。

---

## 11. 开发者工作流

### 11.1 新增任务容器（分步流程）

1. 在 `taskspecs/` 下创建 `<task-id>-<short-name>/` 文件夹。
2. 编写 `spec.yaml`——确定 tier、rubric_weights、objective_dims、citation_policy、cf_rules、prompt_text。
3. 编写 `checkpoint_schema.yaml`——列出所有检查点字段，标注类型、容差、grounding/methodology 标记。
4. 编写 `gt_recipe.yaml`——如果有数值真值，选择或新增计算器。
5. 编写 `judge_notes.md`——D1-D6 段的家族扣分锚点。客观维度按"第一层/第二层"分列。
6. 编写 `provenance.json`（`origin: legacy-v1` + 元信息）。
7. 在 `taskspecs/registry.json` 中注册新条目。
8. 运行 `spec_lint.py --root <ROOT>`，确保 0 ERROR。
9. 如有 GT 计算器，合成一份完美答案，跑通提取→校验→评分→汇总全链路。
10. 冻结容器——把 `status` 改为 `frozen`，运行 `--stamp` 写入 `container_hash`。

### 11.2 新增 GT 计算器

1. 在 `skills/eval-groundtruth/scripts/` 下创建 `<name>.py`。
2. 创建 `<name>.calculator.yaml` sidecar，声明 invocation 模板、inputs_required、self_check 契约。
3. 计算器必须：(a) 写出 `groundtruth.json` 含 `values` + `self_check` + `provenance`；(b) `self_check.passed` 为 true 时才允许继续；(c) cwd 无关——通过 `--root` 或 `__file__` 定位输入文件。
4. 在目标容器的 `gt_recipe.yaml` 中绑定 `calculator.script` 和 `calculator.invocation`。

### 11.3 验证清单

- [ ] `spec_lint.py --root <ROOT>` 全绿（0 ERROR）。
- [ ] GT 计算器 `--root` 参数独立运行（不依赖 cwd）。
- [ ] 合成一份完美答案，跑 `validators.py` → `grade_checkpoints.py` → `aggregate.py`，确认满分。
- [ ] 合成一份有已知错误的答案，确认每个错误都正确归到对应检查点上。
- [ ] 对于含百分比的检查点：GT recipe 中声明了 `percent_fields`，且 validators.py 在环里验证过（不是绕过 validator 直接把 GT 值喂给 grader）。
- [ ] 对于 `tol=0` 的检查点：声明了 `decimals`（防末尾零被 repr 丢弃）。
- [ ] 如果容器是两层结构的：`judge_notes.md` 中客观维度按"第一层/第二层"分列，lint 0 WARN。

### 11.4 修改宪法/引擎的准入门槛

- **宪法修改**：影响所有已冻结容器，需要重新 lint 所有容器并确认兼容性。
- **引擎修改**（技能或脚本）：需要在至少 2 个不同家族的任务上回归测试。新增检查点类型是引擎发布级变更。
- **容器修改**（冻结后）：升级 `version` 号，更新 `provenance.json`，重新 lint，重新 stamp。

---

## 12. 故障排查

按症状查找：

### 12.1 "正确答案被判错了"——百分比刻度不匹配

**症状**：一份作品的数据完全正确（如 ROE = 15.19%），但检查点全部 FAIL，偏差值巨大（如 Δ=15.19）。

**原因**：`validators.py` 将带 `%` 单位的值规范化为 fraction（15.19% → 0.1519），而 GT 计算器输出的是百分数数量级（15.19）。即使答案完全正确，在一个是 0.1519、一个是 15.19 的情况下 tol=0 的比对也必然失败。

**修复**：在容器的 `gt_recipe.yaml` 中声明 `percent_fields: [字段名, ...]`，让 `gt_dispatch.py` 自动将 GT 值除以 100 与 extractor 对齐。**不要**通过把检查点类型从 `pct` 改成 `number` 来绕过——`to_number()` 的转换依据是 `unit_as_written`，不是检查点类型。

**验证**：合成一份带 `%` 单位的答案，完整走 extractor → validator → grader 链路，不要跳过 validator。

### 12.2 "tol=0 但有错误答案通过了"——末尾零陷阱

**症状**：GT = 728.80，学生写了 728.84，检查点显示 PASS。

**原因**：tol=0 的精度对齐逻辑从 `repr(728.80)` 推断 GT 小数位数为 1（因为 `repr` 丢弃末尾零），于是将学生的 728.84 round 到 728.8，比对通过。实际上 GT 是两位小数精度，728.84 应该 FAIL。

**修复**：在检查点 schema 中声明 `decimals: 2`。对于 `pct` 类型，由于 validator 已转为 fraction，声明 fraction 精度：2 位小数百分比 → `decimals: 4`。

### 12.3 "含金量扣分没起作用"——旧模型残留

**症状**：一份作品的数据全部来自第三级来源，但 D1 分数仍然很高。

**可能原因**：

1. 容器的 `judge_notes.md` 还没有按"第一层/第二层"分列（未迁移到两层模型）→ 评分官可能在含金量上扣了分，但旧汇总逻辑中客观比例覆盖了含金量扣分。
2. 旧 ledger 用新汇总器重算 → 双罚（不是"没起作用"，而是扣了两次，但含金量确实被 L1 客观分数"对冲"了）。

**排查**：检查 `spec_lint.py` 输出的该容器 WARN 数；确认是否已迁移到两层结构。

### 12.4 "维度得分超过权重"——账本符号写反

**症状**：某维度 weighted_points > weight，或 scorecard 中有 `ledger_warnings` 关于"负扣分点数"或"钳到 4"。

**原因**：评分官的 deduction ledger 中 `points` 字段写了负数（如 −0.5），导致等级被推到 4 以上，维度得分超过了该维度的满额权重。

**修复**：汇总器会自动钳制（双侧 [0,4] clamp）保证分数不爆炸，但会记录 `ledger_warnings`。根因修复需要评分官重跑，确保 `points` 为正数量级。

### 12.5 "改了容器但 /eval-judge 没生效"——缓存未同步

见 §10.4。源目录的修改需要同步到插件缓存目录或在开发完成后重新安装。

### 12.6 "盲评分数异常偏高/偏低"——载荷纯净性被破坏

**症状**：某作品的盲评分数与预期严重不符，但评分官的 reasoning 中引用了该作品实际不存在的事实。

**原因**：载荷纯净性校验被跳过或执行不完整——`tool_evidence` 中混入了其他作品的信息。

**修复**：检查 `payload_audit.json` 中的 `dropped_claims` 和 `isolation_confirmed`。如果 `isolation_confirmed: false`，重新执行载荷组装和纯净性校验。

### 12.7 "评分归给了错误的标签"——work_registry 映射错位

**症状**：Work A 和 Work B 的评分看内容像是互换了。

**原因**：盲评载荷中的 `work_text` 不是从注册表路径读取的，而是靠"字母序 = 模型编号"的假设手动编排的。

**修复**：必须通过 `work_registry.json` 查询 `blind_label → path → Read(path)`，不要凭隐含假设。

### 12.8 "引用核验全失败"——网络问题

**症状**：几乎所有引用都是 `broken`。

**原因**：WebFetch / WebSearch 的网络连接问题，或目标网站有反爬/付费墙。

**处理**：`broken` 是 D1 质量问题，不影响其他维度评分。报告会在引用章节披露核验失败的引用及其原因。不是 CF1。

### 12.9 "汇总器报错 file not found"——产物的文件名或位置不对

**症状**：`aggregate.py` 找不到 `judge_1.json` 或 `citation_audit.json`。

**原因**：中间产物文件名不匹配——核查 `--bundle` 目录下的文件是否齐全。`citation_audit.json` 仅在 `citation_policy != none` 时产出，但汇总器会尝试加载并 grace 退化为空对象。

---

## 13. 附录

### A. 术语中英对照

| 中文         | English                              |
| ---------- | ------------------------------------ |
| 评审法官       | Eval Judge                           |
| 宪法         | Constitution                         |
| 引擎         | Engine                               |
| 容器         | Container / Task Spec Container      |
| 检查点        | Checkpoint                           |
| 基准真值       | Ground Truth (GT)                    |
| 引用核验       | Citation Verification                |
| 盲评         | Blind Judging / Blind Rubric Scoring |
| 扣分制        | Deduction-based Scoring              |
| 扣分锚点       | Deduction Anchor                     |
| 两层评分       | Two-layer Scoring                    |
| 不重叠规则      | Disjointness Rule                    |
| 关键失败       | Critical Failure (CF)                |
| 载荷纯净性      | Payload Purity                       |
| 作品注册表      | Work Registry                        |
| 汇总器        | Aggregator                           |
| 编排器        | Orchestrator                         |
| 提取器        | Extractor                            |
| 评分官        | Judge / Scorer                       |
| 子代理        | Subagent                             |
| Workflow 岛 | Workflow Island                      |

### B. 关键公式速查

| 公式                                                                            | 位置                               |
| ----------------------------------------------------------------------------- | -------------------------------- |
| `fraction = max(0, L1_frac − L2_points / 4)`                                  | 两层维度计分（aggregate.py §score_work） |
| `weighted_points = fraction × weight`                                         | 维度加权得分                           |
| `grounding_objective = cw × verified_fraction + gw × grounding_pass_fraction` | D1 第一层基线                         |
| `level = min(4, max(0, 4 − Σpoints))`                                         | 扣分账本 → 展示等级                      |
| `score_normalized = total / scored_weight × 100`                              | 有效分母归一化                          |
| `deviation_score = max(0, 1 − (delta − tol) / (max_dev − tol))`               | 偏差评分连续衰减                         |

### C. 脚本 CLI 参数速查

| 脚本                      | 主要参数                                                                                                                          |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `gt_dispatch.py`        | `--container <路径> --out <输出> [--in name=path ...] [--snapshot <JSON>] [--root <根>]`                                           |
| `validators.py`         | `--normalized <raw.json> --schema <schema.json> --out <normalized.json>`                                                      |
| `grade_checkpoints.py`  | `--schema <schema.json> --normalized <norm.json> --groundtruth <gt.json> --out <det.json> [--scoring-mode binary\|deviation]` |
| `aggregate.py`          | `--bundle <目录> --taskspec <taskspec.json> --out <scorecard.json>`                                                             |
| `aggregate.py` (report) | `--report <scorecard1.json> [scorecard2.json ...] --out <report.md>`                                                          |
| `spec_lint.py`          | `--root <repo根> [--stamp]`                                                                                                    |

### D. 文件路径索引

| 路径（相对于仓库根）                             | 内容                |
| -------------------------------------- | ----------------- |
| `rubrics/constitution.md`              | 评分宪法              |
| `skills/eval-*/SKILL.md`               | 各引擎的散文程序          |
| `skills/eval-*/scripts/*.py`           | 各引擎的确定性脚本         |
| `agents/eval-judge.md`                 | 编排器 agent persona |
| `agents/eval-rubric-judge.md`          | 盲评子代理 persona     |
| `commands/eval-judge.md`               | /eval-judge 命令定义  |
| `orchestration/eval-judge.workflow.js` | Workflow 岛脚本      |
| `taskspecs/registry.json`              | 已注册任务索引           |
| `taskspecs/<task>/*`                   | 单任务的冻结容器文件        |
| `docs/three-layer-architecture.md`     | 架构设计论证            |
| `docs/technical-reference.md`          | 本手册               |

### E. 版本与变更记录

| 日期         | 变更                                                                       |
| ---------- | ------------------------------------------------------------------------ |
| 2026-07-29 | 本文档初版——结构化技术参考，涵盖三层架构、两层评分模型、全部引擎与脚本、编排隔离机制                              |
| 2026-07-24 | 两层评分模型实现（constitution.md §计分 重写 + aggregate.py 减法合成 + spec_lint.py 两层校验） |
| 2026-07-23 | 插件化——从 WorkBuddy/CodeBuddy 迁移为 Claude Code 插件                            |

---

> **维护说明**：本手册覆盖 eval-judge v1.3.0（11 个冻结容器 S1-S11）。当有新容器冻结、新计算器加入或宪法修订时，对应更新 §3、§4、§5.2、§6.3 和 §E。
