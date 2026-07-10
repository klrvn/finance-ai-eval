# golden/ — 容器自测件（S1 · 杜邦分析，状态：pending 待回填）

冻结级容器应含一对 golden 作品用于回归与「检查点真能抓错」的证明：

- `golden_clean.md` —— 合规合成作品，杜邦恒等式自洽、四公司齐全、有差异分析与竞争力判断。
- `golden_defect.md` —— **种入已知错误**。本任务示例缺陷：茅台 ROE 与三因子乘积不勾稽（roe_dupont_reconcile 失败）；或漏掉一家同业（companies_covered ≠ 4）。
- `expected_results.json` —— 两件各检查点的期望 通过/未通过。

`origin: legacy-v1` 暂以 `golden: pending` 宽限（lint 警告不报错）。回填：手写两件 → 跑 `eval-taskspec-lint --golden` → 落 `expected_results.json`。
