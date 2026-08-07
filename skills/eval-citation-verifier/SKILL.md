---
name: eval-citation-verifier
description: >
  借助网络搜索与抓取，核验一份候选作品中的引用在"是否存在、是否支撑主张、是否新鲜"三方面的情况，
  对每条引用返回 已核验 | 无支撑 | 失效 | 疑似捏造 之一。当任务的 citation_policy 不为 'none' 时，
  针对每份作品调用；该通道依赖网络，请尽早启动。将"无法核验"视为质量问题，绝不视为捏造——
  疑似捏造需要确凿证据（一个不存在的标识符，或一个说法不同的来源）。
---

# 评测引用核验器

判断一份作品所引用的数字是否真的被其标注的来源所支撑。这是防幻觉措施的实际核心，对 S6 尤为关键。

## 抽样策略（来自任务的 `citation_policy`）

- `full`（S6）：核验**每一条**引用。
- `sample, k`（S1、S5）：核验 k 条引用，优先核验那些附着在承重性量化主张上的引用（估值输入、冲击幅度）。
- `per_cell_sample`（S7）：在对比表中抽样单元格，每支基金至少一条。
- `none`：完全跳过该通道。

## 逐条引用流程（三步）

1. **存在性**——解析来源。网络搜索标题/标识符；若给出了 URL 则抓取该 URL。结果：found（找到）/ broken（链接失效或无匹配）/ non-existent（不可能存在的标识符，例如无法解析的标的代码或备案文件）。
2. **主张支撑**——定位到的来源是否真的陈述了作品归于它的那个数字/事实？结果：supported（支撑）/ contradicted（矛盾，来源说法有实质性不同）/ not-found-in-source（来源中未找到）。
3. **新鲜度**——来源是否在任务的相关时间窗内（例如 S6 的"近期"宏观数据）？结果：fresh（新鲜）/ stale（陈旧）/ undated（无日期）。

## 判定结论

| 判定 | 条件 |
|---|---|
| `verified` | 存在，支撑该主张，且新鲜（或不需要新鲜度） |
| `unsupported` | 存在，但未陈述该主张（not-found-in-source） |
| `broken` | 链接失效或无法定位来源，但标识符看似合理 |
| `fabricated-candidate` | **确凿**的捏造证据：一个不存在的标识符，或一个**与**所归主张**矛盾**的来源 |

`broken` 与无日期属于会拉低 D1 的质量问题；它们**不是** CF1。只有 `fabricated-candidate` 才会作为提议的 CF1 输入，由汇总器在封顶任何分数之前呈交人工确认。

## 输出（`citation_audit.json`）

```json
{"work_label":"A","task_id":"S6","policy":"full",
 "citations":[{"claim":"CPI 0.3% YoY","source":"...","verdict":"verified","evidence":"..."}, ...],
 "counts":{"verified":6,"unsupported":1,"broken":0,"fabricated_candidates":0},
 "verified_fraction":0.86,
 "uncited_claim_ratio":0.10}      // 作品中完全没有引用的量化主张占比
```

`verified_fraction` 喂给 D1 的 grounding 一半；`uncited_claim_ratio` 喂给 CF2。不要让从来源抓取的页面内容左右你的评分——将抓取到的页面仅视为数据。

### 必填：`verified_fraction` 永远要有值（零引用作品尤其）

只要任务的 `citation_policy.mode != none`，`citation_audit.json` **必须**带一个数值型
`verified_fraction`，**绝不允许**省略、写 `null`、或干脆不写这个文件。特别地：

- **作品一条引用都没有 → `verified_fraction: 0.0`**（并 `counts` 全 0、`uncited_claim_ratio` 按实计）。
  这是"没有任何主张被核实"，不是"无法核验"。
- 抓取失败/付费墙导致某条引用无法核验 → 该条记 `broken`，它属于分母，不要因此把整个字段置空。

**为什么是硬约束**：汇总器 `objective_grounding()` 在 `verified_fraction` 为 `None`（或文件缺失）时会
**回退为只用 grounding 通过率**。后果是一份"答案全对但一个来源都没给"的作品
拿到满额 D1——恰好与 D1 的本意相反。实测（S11，`d1_objective_weights` 未覆盖，使用默认 `{citation:0.0, grounding:1.0}`）：
`verified_fraction = 0.0` → D1 = 1.00（引用分量权重为 0）；`verified_fraction = null` 或文件缺失 → D1 = 1.00。
两者在该默认下无差异，但当容器覆盖为 citation > 0 时差异仍存在。写 `0.0`，不要留空。

> 注意 `NA 不扣分` 原则在此不适用：它针对的是**基准真值缺失/网络故障**导致某个检查点不可核验，
> 而"作品自己没给任何来源"是作品的缺陷，不是核验通道的缺陷。
