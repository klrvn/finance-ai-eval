# Eval Judge · 评审法官

对金融分析 AI 的输出做全流程、可复现、抗操纵的评测：确定性检查点 + 引用核验 + 盲评量规打分，产出带证据的评分卡与统一格式报告。

## 三层架构（任务规范与评分流程解耦，可扩展到新任务）

```
第1层 宪法    rubrics/constitution.md      不可变原则
第2层 引擎    skills/eval-*                 任务无关的流程引擎，经契约读取容器
第3层 容器    taskspecs/<task>/             每任务一个自包含冻结文件夹，索引 taskspecs/registry.json
```

- **新增任务 = 新增一个冻结容器文件夹**，不改引擎、不改宪法。当前已注册容器见 `taskspecs/registry.json`（S1–S11）。
- 完整设计与不变量见 `docs/three-layer-architecture.md` 与 `rubrics/constitution.md` §0（不变量）/§1（可参数化面）。
- **反作弊**：编排器只评 `status: frozen` 容器，把 `container_hash` 钉进运行目录；容器须在接收候选作品前冻结，编写者永不接触候选作品。

## 类型

Agent 型（单个 AI 专家）。

## 功能

评审法官对一份或多份候选作品运行**绝对评分**（每份对照同一套量规与基准真值打分，非两两比较）。采用**派发器 + 并行子代理**编排：主对话只做共享前段与派发，然后对 N 份作品并行扇出 `3×N` 个子代理。流水线的权威定义见 `commands/eval-judge.md`；编排层三条护栏见 `skills/eval-orchestrator/SKILL.md`。

**共享前段（顺序，主对话）**
1. **Intake** — 解析冻结容器、锁定作品盲评注册表 `work_registry.json`。
2. **Spec** — 加载容器 spec/schema/gt_recipe/judge_notes 与宪法。
3. **GroundTruth** — 按容器 `gt_recipe` 构建基准真值（带自检、一次算全作品共享；A 类缺输入则硬停）。

**并行扇出（`3×N` 个子代理同时运行）**
4. **N × `eval-pipeline`** — 每份作品一个：读取 → 提取 → 校验 → 确定性检查点评分 → 引用核验 + 载荷纯净性校验。
5. **2N × `eval-rubric-judge`** — 每份作品两个独立盲评官（`tools: Read`，GT-free 载荷）。盲评不依赖流水线的任何产物，因此与之**并发**派发。

**后段（顺序，主对话）**
6. **CF / 汇总 / Report** — 逐作品 CF 审计 → 评分卡 → 全部评分卡汇总为统一格式报告。

墙钟时间为 `max(最慢子代理)` 而非各作品之和。

**核心属性**：扣分制、运算交给脚本、盲评与逐作品隔离、"不可验证≠捏造"、篇幅不加分、每阶段落盘 JSON 可审计可重跑。以上原则的完整定义见 `rubrics/constitution.md`。

**评分维度**（D1-D6，权重按任务设定合计 100）：D1 数据完整性与依据 · D2 方法正确性 · D3 完整性 · D4 分析质量与洞察 · D5 可操作性与适配性 · D6 外部工具链完整度。维度定义、扣分量尺、CF1-5、D1 数据来源含金量分级均以 `rubrics/constitution.md` 为准。

## 使用示例

- `请评测任务 S3（固定收益工具分析）的这份作答：<粘贴作品文本>`
- `对任务 S4（多因子业绩归因，指数对比）的这两份输出打分并对比：<作品A> / <作品B>`
- `评测 S6（宏观观点），citation_policy 为 full，请重点核验每条引用的真实性：<作品文本>`

## 运行环境

确定性脚本依赖：`pip install QuantLib pandas numpy openpyxl pyyaml`。仓库统一使用 `python`（非 `python3`，见 `CLAUDE.md`）。所有脚本以 UTF-8 读写 JSON，可在中文（gbk）Windows 环境下正确处理中文内容。

## 安装（Claude Code 插件）

以本地 marketplace 安装：

```bash
claude plugin marketplace add <本仓库路径>
claude plugin install eval-judge@eval-judge-marketplace
```

安装后暴露命令 `/eval-judge`、agent `eval-judge`（编排器）+ `eval-rubric-judge`（隔离盲评子代理），以及若干 `eval-*` 技能。插件运行时用 `$CLAUDE_PLUGIN_ROOT` 解析宪法/容器/脚本的绝对路径（见 `CLAUDE.md`）。
