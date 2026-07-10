# golden/ — 容器自测件（S6，状态：pending 待回填）

冻结级容器应包含一对 golden 作品用于回归与「检查点真能抓错」的证明：

- `golden_clean.md` —— 合规合成作品，应通过全部可核验检查点。
- `golden_defect.md` —— **种入已知错误**的作品。本任务的示例缺陷：捏造一条引用（不存在的来源）；未引用主张占比超阈值。
- `expected_results.json` —— 两件各检查点的期望 通过/未通过。

`origin: legacy-v1` 暂以 `golden: pending` 宽限（lint 警告不报错），因为验证 golden 需运行完整真值/评分管线。
回填：手写两件 → 跑 `eval-taskspec-lint --golden` → 落 `expected_results.json`。
`origin: kb`（未来子代理生成）必须 golden 全绿方可冻结。
