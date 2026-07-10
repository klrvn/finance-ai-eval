# Eval Judge · 评审法官

对金融分析 AI 的输出做全流程、可复现、抗操纵的评测：确定性检查点 + 引用核验 + 盲评量规打分，产出带证据的评分卡与统一格式报告。

## 三层架构（任务规范与评分流程解耦，可扩展到新任务）

```
第1层 宪法    rubrics/constitution.md      不可变原则：D1-D6、扣分制、CF1-5、盲评隔离
第2层 容器    taskspecs/<task>/            每任务一个自包含文件夹（spec/checkpoint_schema/gt_recipe/judge_notes/…）
                                          索引 taskspecs/registry.json；由 eval-taskspec-lint 校验、人工冻结
第3层 引擎    skills/eval-*                任务无关的流程引擎，经契约读取容器
```

- **新增任务 = 新增一个容器文件夹**，不改引擎、不改宪法。S1-S8 已迁移为参考容器（`origin: legacy-v1`）。
- **作者槽位**：未来由 RAG 知识库支撑的 `eval-task-designer` 子代理自动生成容器（检索方法学卡片→判定 tier→起草→自检→人工冻结）。**该子代理尚未构建**，其在流程中的位置见 `taskspecs/README.md` 与 `docs/two-layer-architecture.md`；当前容器为手工编写。
- **反作弊**：编排器只评 `frozen` 容器，并把 `container_hash` 钉进运行目录；容器须在接收候选作品前冻结，作者永不接触候选作品。

## 类型

Agent 型（单个 AI 专家）

## 功能

评审法官编排一条七阶段流水线，对一份或多份候选作品（AlphaMind 或竞品的输出）绝对评分：

0. **解析任务** —— 在 `registry.json` 按 `task_id` 定位**冻结容器**，钉入 `container_hash`；从容器读 spec/schema/gt_recipe/judge_notes，从宪法读评分原则。
1. **提取** —— 把自由文本作品转成结构化字段（含向量/网格/单元格等结构化载荷）+ 工具链清单 `tool_inventory`（喂 D6），只记录"呈现了什么"，不计算、不评判。
2. **基准真值** —— 按容器 `gt_recipe` 分派到共享计算器库（债券 QuantLib、Brinson、线性冲击、动量回测、基金指标、客户指标），每次运行只算一次并全作品共享；带自检，自检不过则停机。
3. **检查点评分** —— 提取值 vs 真值按逐字段容差比对，含标量、向量、集合、内部勾稽、单调性、抽样核验等类型；错误数字记 0（CF5），不给部分分。
4. **引用核验** —— 联网核验引用的存在性/支撑性/新鲜度（S6 逐条核验，为抗幻觉探针）。
5. **盲评量规 ×2** —— 派发给全新子代理，只喂盲评载荷，按 D1-D6 六维度**扣分制**绝对打分：每维度从满分 4 起评，逐条登记带证据的扣分项，无标记不扣分；两轮衡量评审间一致性。
6. **CF 审计** —— 汇总 CF1 捏造 / CF2 未引用 / CF3 只描述不计算 / CF4 陈旧数据；CF1 需人工确认才封顶。
7. **汇总与统一报告** —— 客观维度连续计分（未通过检查点即扣分来源）、主观维度取盲评扣分账本均值、应用 CF 封顶，产出 0-100 分评分卡；无论一份还是多份作品，最终产出**同一格式**的评测报告：总分与排名表 + D1-D6 逐维度逐作品详细评分（含扣分明细与证据）+ 检查点/引用/CF 明细 + 结论。

**核心属性**：扣分制（满分起评、凭证据扣分、不可核验不扣分）；运算交给脚本、判断在隔离下进行；盲评与逐作品隔离；"不可验证 ≠ 捏造"；篇幅不加分；每阶段落盘 JSON，运行可审计、可从任一阶段重跑。

评分维度：D1 数据完整性与依据 · D2 方法正确性 · D3 完整性 · D4 分析质量与洞察 · D5 可操作性与适配性 · D6 外部工具链完整度（数据获取与计算的工具链层级：金融专用 API > 通用结构化来源 > 野网页抓取 > 凭记忆；权重按任务设定，合计 100）。

## 使用示例

- `请评测任务 S3（固定收益工具分析）的这份作答：<粘贴作品文本>`
- `对任务 S4（多因子业绩归因，指数对比）的这两份输出打分并对比：<作品A> / <作品B>`
- `评测 S6（宏观观点），citation_policy 为 full，请重点核验每条引用的真实性：<作品文本>`

## 运行环境

本包以 **Claude Code 插件**形式运行（`.claude-plugin/plugin.json` 为清单，`agents/` 与 `skills/` 自动发现）。

确定性脚本依赖 Python：`pip install QuantLib pandas numpy openpyxl pyyaml`（`pyyaml` 为 `gt_dispatch.py` 必需；`QuantLib` 用于 S3 债券计算；`openpyxl` 仅在 `.xlsx` 样本时需要）。所有脚本以 UTF-8 读写 JSON，可在中文（gbk）Windows 环境下正确处理中文内容；调用脚本请用 `python`（非 `python3`）。

**插件根路径约定**：安装后工作目录是用户项目，而宪法/容器/脚本在插件目录。先 `echo "$CLAUDE_PLUGIN_ROOT"` 取得根路径，参考数据用绝对路径 `Read`，脚本用 `${CLAUDE_PLUGIN_ROOT}/…` 调用，运行产物写在用户当前目录（`./eval-runs/…`）。详见 `CLAUDE.md`。

## 安装

在 Claude Code 中把本仓库作为插件加载（`/plugin`）。开发/自用可直接指向本地目录：

```
# 本地目录（本仓库根，含 .claude-plugin/plugin.json）
/plugin marketplace add /path/to/eval-judge      # 若已建 marketplace.json
/plugin install eval-judge
```

或直接把 `agents/` 与 `skills/` 放进某项目的 `.claude/` 下由 Claude Code 自动发现。安装后，说
「评测任务 S3 的这份作答：<粘贴文本>」即会触发 `eval-orchestrator` 技能，并由 `eval-judge` /
`eval-rubric-judge` 子代理完成盲评。
