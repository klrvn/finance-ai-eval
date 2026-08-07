#!/usr/bin/env python3
"""Two-layer combination test — proof that a dimension with a GT base scores the arithmetic
mean of the two layers, that a dimension without one still scores the pure deduction ledger,
and that the accepted dilution trade-off is exactly what the constitution says it is.

    python skills/eval-aggregator/tests/combine_layers_test.py

Exit code 0 iff every case matches.

Why this file exists: aggregator 3.0 changed `fraction = max(0, L1 - L2pts/4)` to
`fraction = (L1 + (4 - L2pts)/4) / 2`. The easy way to get this wrong in a later refactor is to
average the no-GT-base dimensions against an implicit 1.0, which would floor every purely-judged
dimension at 0.5. That case is pinned first. See rubrics/constitution.md §计分.
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "skills", "eval-aggregator", "scripts"))
import aggregate as AGG

EPS = 1e-9
bad = 0


def check(label, got, want, eps=EPS):
    global bad
    ok = (got is None and want is None) or (got is not None and want is not None and abs(got - want) <= eps)
    print(("ok   " if ok else "XX   ") + f"{label}: got {got!r}, want {want!r}")
    if not ok:
        bad += 1


def assert_true(label, cond):
    global bad
    print(("ok   " if cond else "XX   ") + label)
    if not cond:
        bad += 1


print("--- 1. 无第一层基线的维度不参与平均，仍是纯扣分制 ----------------------------")
# The load-bearing case. D3/D4/D6 must stay bit-identical to the pre-3.0 engine: averaging them
# against an implicit 1.0 would floor a 4-point-deducted D4 at 0.5 instead of 0.
for pts in [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4]:
    check(f"combine_layers(None, {pts})", AGG.combine_layers(None, pts), (4.0 - pts) / 4.0)
check("D4 扣满 4 分仍为 0（不保底 0.5）", AGG.combine_layers(None, 4), 0.0)
check("D4 扣 11 分（S14 alphamind 实测）仍为 0", AGG.combine_layers(None, 11), 0.0)

print("\n--- 2. 有第一层基线的维度取两层算术平均 --------------------------------------")
for l1, pts in [(0.0, 0), (0.34, 0), (0.5714, 2.25), (1.0, 4), (1.0, 0), (0.5, 2)]:
    coef = max(0.0, (4.0 - pts) / 4.0)
    check(f"combine_layers({l1}, {pts})", AGG.combine_layers(l1, pts), (l1 + coef) / 2.0)

print("\n--- 3. 只有两层同时为 0 才归零（本次改动的目标）------------------------------")
check("两层同时为 0 -> 0", AGG.combine_layers(0.0, 4), 0.0)
check("第一层 0、第二层不扣分 -> 0.5", AGG.combine_layers(0.0, 0), 0.5)
check("第一层满分、第二层扣满 -> 0.5", AGG.combine_layers(1.0, 4), 0.5)
assert_true("凡有一层非零，分数即非零",
            all(AGG.combine_layers(l1, p) > 0 for l1, p in [(0.01, 4), (0.0, 3.9), (0.3415, 3.0)]))

print("\n--- 4. 已知代价：两层力度各减半（宪法表中明列，非缺陷）-----------------------")
check("第一层 0.34 + 干净账本 -> 抬高到 0.67", AGG.combine_layers(0.34, 0), 0.67)
check("第一层 1.00 + 账本崩盘 -> 保底 0.50", AGG.combine_layers(1.0, 4), 0.50)

print("\n--- 5. 单调性：固定一层，另一层变差则分数不升 --------------------------------")
mono = True
for l1 in [0.2, 0.5714, 1.0]:
    seq = [AGG.combine_layers(l1, p) for p in [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4]]
    mono &= all(a >= b - EPS for a, b in zip(seq, seq[1:]))
for pts in [0, 1, 2, 3]:
    seq = [AGG.combine_layers(x, pts) for x in [0.0, 0.25, 0.5, 0.75, 1.0]]
    mono &= all(a <= b + EPS for a, b in zip(seq, seq[1:]))
assert_true("两个方向都单调", mono)

print("\n--- 6. S14 实测回归钉（constitution 表中的四个情形）--------------------------")
check("claude   D1  L1=0.5714 扣2.25", AGG.combine_layers(0.5714, 2.25), 0.5045, eps=1e-4)
check("alphamind D1 L1=0.3415 扣3.00", AGG.combine_layers(0.3415, 3.00), 0.2958, eps=1e-4)
check("codex    D1  L1=0.5952 扣0.75", AGG.combine_layers(0.5952, 0.75), 0.7038, eps=1e-4)
check("claude   D2  L1=1.0000 扣1.75", AGG.combine_layers(1.0000, 1.75), 0.7813, eps=1e-4)

print("\n--- 7. 系数落在 [0,1]，且 level = 4 x fraction 成立（CF 封顶的前提）----------")
assert_true("layer2_coefficient 恒在 [0,1]",
            all(0.0 <= AGG.layer2_coefficient(p) <= 1.0 for p in [-2, 0, 1.7, 4, 9]))
assert_true("fraction 恒在 [0,1]",
            all(0.0 <= AGG.combine_layers(l1, p) <= 1.0
                for l1 in [None, 0.0, 0.4, 1.0] for p in [-1, 0, 2.5, 4, 9]))
lvl_ok = all(abs(round(4.0 * AGG.combine_layers(l1, p), 2) - 4.0 * AGG.combine_layers(l1, p)) <= 0.005
             for l1 in [None, 0.0, 0.4, 1.0] for p in [0, 1.25, 2.5, 4])
assert_true("level = 4 x fraction 在所有分支成立（舍入误差 <= 0.005）", lvl_ok)

print("\n--- 8. 引擎版本已标注 --------------------------------------------------------")
assert_true(f"AGGREGATOR_VERSION = {AGG.AGGREGATOR_VERSION!r} >= 3.0",
            tuple(int(x) for x in AGG.AGGREGATOR_VERSION.split(".")) >= (3, 0))

print()
if bad:
    print(f"FAILED: {bad} 项不符")
    raise SystemExit(1)
print("PASSED: 两层取平均合成全部符合预期")
