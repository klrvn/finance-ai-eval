---
name: eval-groundtruth
description: >
  计算用于为 AlphaMind 评测任务打分的确定性基准真值参考答案：债券分析（S3，QuantLib）、
  多因子归因任务可复用的单期 Brinson-Fachler 行业腿计算器（S4 可选升级）、线性因子冲击下的组合盈亏（S5 自定义冲击段）、
  月度动量回测（S2）、基金风险指标（S7）、由样本派生的客户指标（S8），
  以及两种无脚本方案——杜邦恒等式内部一致性检查（S1）与宏观快照（S6）。
  当编排器在检查点评分之前需要一份参考包时调用，使用任务的 gt_recipe。基准真值只构建一次，并在所有作品间共享。
---

# 评测基准真值

基准真值必须**比被评分的系统更可靠**，因此它要么由专用 Python 脚本确定性计算，要么由用户提供的外部快照给出——绝不由模型估算。每次运行只构建一次，并在每份作品中复用。

## 任务无关调度（读契约，不认 task_id）

本引擎**不含任务知识**：它读容器的 `gt_recipe.yaml`，按 `kind` 分派——
- `kind: calculator` → 按 `calculator.invocation` 调用**共享计算器库**（`scripts/*.py`，每个带 `*.calculator.yaml` 契约 sidecar），校验其 `self_check`；
- `kind: internal_consistency` → 不算数值，写空 `values`（一致性交给评分器的 reconcile/monotonic/consistency 类型）；
- `kind: user_snapshot` → 用户快照原样写入 `values`，否则写 `values: null` 并让相关检查点记 NA。

新任务复用现有计算器只需在其容器里绑定；确有需要的新计算器进入**库**（附 sidecar + 自检），绝不做逐任务分叉。下表按 recipe（而非 task）列出当前计算器；哪个任务用哪个 recipe 由各容器的 `gt_recipe.yaml` 声明。

每个脚本读取输入（一份样本文件和/或一个参数 JSON），写出一份 `groundtruth.json`，其中包含参考值，外加一个 `provenance`（来源溯源）块（脚本引用、输入哈希、时间戳）与一个 `self_check`（自检）块。**若 `self_check.passed` 为 false，停止并报告——不要基于未经验证的真值打分。**

## 计算方案（recipes）

当前围绕 S1-S8 覆盖 9 种可调度方案：6 种可执行脚本/共享计算器方案（其中 `brinson` 为可复用行业腿计算器），3 种无脚本方案（靠一致性检查或用户提供快照）。

### 有脚本的方案（6 种）

| gt_recipe | 脚本 | 默认任务/用途 | 输入 | 说明 |
|---|---|---|---|---|
| `bond_analytics` | `scripts/bond_analytics.py` | S3 | 债券参数 JSON（票息、日期、结算日、计息惯例，以及一个市场净价 *或* 收益率） | 自包含。OAS 需要曲线+波动率模型，超出范围；脚本将其记为 `null` 并附说明。 |
| `brinson` | `scripts/brinson.py` | 共享行业归因计算器（S4 可选升级） | 行业级 `csv/xlsx`（`sector, w_p, r_p, w_b, r_b`） | 默认不直接代表当前 S4 全任务；当 S4 提供行业级样本时，可客观核验其行业配置/选择腿。自检会断言各项效应与主动收益相吻合。 |
| `linear_shock` | `scripts/linear_shock.py` | S5 | `s5_portfolio.csv/xlsx`（权重 + 因子敏感度）+ 冲击 JSON | 仅自定义冲击段。历史重放需要所提供的情景收益集合。 |
| `momentum_backtest` | `scripts/momentum_backtest.py` | S2 | `prices.csv`（日期 × 标的，复权）、`benchmark.csv`（日期、点位），可选 `membership.csv` | 需要市场数据。若缺失，编排器仅回退到 NAV 复现一致性。 |
| `fund_metrics` | `scripts/fund_metrics.py` | S7 | 各基金 NAV/收益 CSV + 一份 `static.csv`（费率、管理规模 AUM） | 计算收益/波动率/夏普/最大回撤；费率与 AUM 为透传（不可计算）。 |
| `client_metrics` | （使用 `linear_shock.py --metrics`） | S8 | `s8_client.csv/xlsx` | 第一大持仓占比 %、权益占比 %、综合费率、HHI 集中度。 |

### 无脚本的方案（3 种）

这两种方案不产出数值型 `groundtruth.json`，而是定义一致性检查规则或依赖用户提供的外部快照。编排器在遇到这些 recipe 时，不需要运行 Python 脚本，而是按以下说明处理。

| gt_recipe | 任务 | 机制 | 处理方式 |
|---|---|---|---|
| `dupont_internal` | S1 | **内部一致性检查** | 杜邦分析没有单一"正确答案"数值真值（取决于报告期与口径），因此不计算基准真值数值。取而代之的是，检查点评分器对杜邦恒等式与同业覆盖完整性做确定性判定：例如 `ROE == 净利润率 × 总资产周转率 × 权益乘数` 的 `reconcile` 检查，以及公司覆盖数、引用支撑等字段。编排器写出一份 `groundtruth.json`，其中 `values` 为空对象，`recipe` 标记为 `dupont_internal`，`self_check.passed` 设为 true。 |
| `multifactor_internal` | S4 | **内部一致性检查** | 当前 S4 是"沪深300 vs 中证500 的多因子业绩归因"。默认不提供共享 CNE5 风格因子计算器，因此不计算完整数值真值；取而代之的是检查点评分器对 `总收益差 == 行业配置效应 + 风格因子效应 + 个股选择效应` 做 `consistency` 判定，并结合引用与盲评检查方法是否正确。若用户提供行业级样本，可额外调用共享 `brinson` 计算器核验行业腿。编排器写出 `values: {}`、`recipe: multifactor_internal`、`self_check.passed: true`。 |
| `macro_snapshot` | S6 | **用户提供外部快照** | 宏观观点任务的"真值"是一份由用户提供的当前宏观数据快照（CPI、GDP、政策利率、市场隐含概率等）。若用户提供了快照 JSON，编排器将其原样写入 `groundtruth.json` 的 `values` 字段，检查点评分器据此核验学生引用的量化数据点是否与快照一致。若用户未提供快照，编排器写出 `groundtruth.json`，其中 `values` 为 null，`recipe` 标记为 `macro_snapshot`，`self_check.passed` 仍为 true（缺失数据不是错误），但所有 `grounding: true` 的检查点被记为 `NA`。S6 的核心评测依赖引用核验（`citation_policy: full`）而非数值真值——引用是否真实、是否支撑主张、是否新鲜。 |

## 用法

**首选入口：`gt_dispatch.py`（确定性调度，按 `gt_recipe.kind` 路由并强制 self_check 闸门）。** 编排器应调用它，
而不是自行拼接计算器命令——这样"读 recipe→建命令→跑→校验"全程可复现，不依赖临场判断：

脚本与容器都在**插件目录**里，用 `${CLAUDE_PLUGIN_ROOT}` 定位（Bash 会展开）；`--in` 输入样本与 `--out` 产物则相对**用户当前目录**：

```bash
# calculator 方案：命名输入按容器 gt_recipe 的 invocation 模板填充；跑完强制校验 self_check.passed
python "${CLAUDE_PLUGIN_ROOT}/skills/eval-groundtruth/scripts/gt_dispatch.py" --container "${CLAUDE_PLUGIN_ROOT}/taskspecs/S2-momentum-backtest" --in prices=px.csv --in benchmark=b.csv --out run/groundtruth.json
python "${CLAUDE_PLUGIN_ROOT}/skills/eval-groundtruth/scripts/gt_dispatch.py" --container "${CLAUDE_PLUGIN_ROOT}/taskspecs/S7-fund-screen" --in "navs=fundA.csv fundB.csv" --in static=static.csv --out run/groundtruth.json
# S4 若提供行业级样本，可用共享 brinson 计算器核验行业腿：
python "${CLAUDE_PLUGIN_ROOT}/skills/eval-groundtruth/scripts/gt_dispatch.py" --container "${CLAUDE_PLUGIN_ROOT}/taskspecs/S4-multifactor-attribution" --in fixture=s4_sector_leg.csv --out run/groundtruth.json
# internal_consistency（S1/S4）与 user_snapshot（S6）无需外部计算，dispatcher 直接写出正确的 groundtruth.json：
python "${CLAUDE_PLUGIN_ROOT}/skills/eval-groundtruth/scripts/gt_dispatch.py" --container "${CLAUDE_PLUGIN_ROOT}/taskspecs/S1-dupont-analysis" --out run/groundtruth.json
python "${CLAUDE_PLUGIN_ROOT}/skills/eval-groundtruth/scripts/gt_dispatch.py" --container "${CLAUDE_PLUGIN_ROOT}/taskspecs/S4-multifactor-attribution" --out run/groundtruth.json
python "${CLAUDE_PLUGIN_ROOT}/skills/eval-groundtruth/scripts/gt_dispatch.py" --container "${CLAUDE_PLUGIN_ROOT}/taskspecs/S6-macro-view" [--snapshot snap.json] --out run/groundtruth.json
```

`gt_dispatch` 会：解析容器 recipe → 按 kind 路由 → 对 calculator 用 invocation 模板 + `--in name=path` 生成命令并运行
→ **无论脚本自身退出码如何，一律校验输出的 `self_check.passed`，为假或缺失即非零退出、拒绝在未经验证的真值上打分**。
若某个新计算器需要，其命令模板与命名输入都声明在容器 `gt_recipe.yaml` 与 `*.calculator.yaml` sidecar 里，dispatcher 无需改动。

底层计算器也可直接调用（dispatcher 内部即如此），同样用 `${CLAUDE_PLUGIN_ROOT}` 定位脚本（下方简写 `$R`）：
```bash
R="${CLAUDE_PLUGIN_ROOT}/skills/eval-groundtruth/scripts"
python "$R/bond_analytics.py" --params bond.json --out run/groundtruth.json          # S3
python "$R/brinson.py" --fixture s4_portfolio.csv --out run/groundtruth.json          # S4
python "$R/linear_shock.py" --fixture s5_portfolio.csv --shock shock.json --out run/groundtruth.json  # S5
python "$R/momentum_backtest.py" --prices prices.csv --benchmark benchmark.csv --out run/groundtruth.json  # S2
python "$R/fund_metrics.py" --navs fundA.csv fundB.csv --static static.csv --out run/groundtruth.json  # S7
python "$R/linear_shock.py" --fixture s8_client.csv --metrics --out run/groundtruth.json  # S8
```

无脚本的方案（编排器直接写出 groundtruth.json）：
- **S1**（`dupont_internal`）：写出 `{"values": {}, "recipe": "dupont_internal", "self_check": {"passed": true}}`，一致性检查由检查点评分器的 `reconcile` 等类型自动完成。
- **S4**（`multifactor_internal`）：写出 `{"values": {}, "recipe": "multifactor_internal", "self_check": {"passed": true}}`，核心检查是三腿归因与总收益差的 `consistency` 勾稽；若另附行业级样本，可再调用共享 `brinson` 计算器。
- **S6**（`macro_snapshot`）：若用户提供快照，原样写入 `values`；若未提供，写出 `{"values": null, "recipe": "macro_snapshot", "self_check": {"passed": true}}`，grounding 检查点记为 NA。

依赖项：`PyYAML`（`gt_dispatch` 读取 recipe，必需）、`QuantLib`（S3）、`pandas`、`numpy`、`openpyxl`（xlsx 样本）。使用 `pip install QuantLib pandas numpy openpyxl pyyaml` 安装。

## 缺失市场数据的处理

对于 `momentum_backtest`（S2）、`fund_metrics`（S7）以及 `macro_snapshot`（S6），基准真值需要用户必须提供的数据。当数据缺失时，编排器**不会**捏造它：受影响的检查点被记为 `NA`（不可验证），报告会准确说明需要什么数据才能消解它们。自包含或可直接落盘的方案（`bond_analytics`、共享 `brinson` 行业腿、`linear_shock` 自定义冲击段、`client_metrics`、`dupont_internal`、`multifactor_internal`）始终可按容器规则运行。
