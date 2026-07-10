# golden/ — 容器自测件（S4 · 多因子归因，状态：pending 待回填）

冻结级容器应含一对 golden 作品用于回归与「检查点真能抓错」的证明：

- `golden_clean.md` —— 合规合成作品，三效应齐全、之和勾稽总收益差、CNE5 因子有暴露与贡献。
- `golden_defect.md` —— **种入已知错误**。本任务示例缺陷：三效应之和不等于总收益差（decomposition_reconciles 失败）；或缺风格因子腿（cne5_factors_covered < 2）。
- `expected_results.json` —— 两件各检查点的期望 通过/未通过。

`origin: legacy-v1` 暂以 `golden: pending` 宽限（lint 警告不报错）。回填：手写两件 → 跑 `eval-taskspec-lint --golden` → 落 `expected_results.json`。
