#!/usr/bin/env python3
"""S12 Put-Writing 回测的基准真值（冻结快照，网络无关）。

输出两类值，来源与可核验程度不同 —— 这个区分是本计算器的核心设计：

  1) 中债-综合财富(总值)逐年回报 6 个值 —— **真外部真值**，D1 第一层的锚点。
     来自中债估值中心/媒体披露（2021、2024 为披露值）与公开基准推算（其余四年）。
     财富口径由任务 prompt 钉死，无合法替代口径。

  1b) 沪深300 逐年回报，**两种口径各 6 个值**（csi300tr_* 全收益 / csi300pr_* 价格指数）
     —— 同样是真外部真值。与债底仓不同，这里两种口径都是合法选择，所以两条序列都输出；
     容器的 csi300_<y> 检查点通过 gt_variants 按作品自己的口径选中其一比对，
     另一条不参与该作品评分。两条序列逐年最小间距 1.68pp 远大于容差 0.005，
     故不存在「一个数同时通过两种口径」的歧义（由 self_check 断言守护）。

  2) benchmark_annual_return —— 对照组年化收益率。**计算**得出而非硬编码：
     年度组合回报 = 0.95×债 + 0.05×沪深300全收益，逐年连乘后开六次方根。
     计算而非硬编码有两个好处：self_check 真正在验证算术，且任何分量修改自动传播。

**策略组的五项指标故意不在此输出。** 历史期权权利金没有公开的权威逐月序列，且回测结果
取决于 IV 取值、保证金口径、换仓滑点等定价假设——属**模型依赖**而非数据缺失，补数据也
解决不了。给它们编一个点值充当真值，会惩罚「诚实标注为假设估算并给出区间」这一正确行为，
同时放行伪精确的答案。策略组指标由容器的内部类型检查点（reconcile/consistency/present）承载。

刻度约定：所有百分比量以 **fraction** 输出（5.09% -> 0.0509），与提取器 validators.py
的规范化结果同刻度。因此 gt_recipe.yaml **不得**声明 percent_fields ——
声明会导致 gt_dispatch.apply_percent_canon 再除一次 100，7 个 pct 检查点全部失败。

用法:
  python put_write_snapshot.py --out run/groundtruth.json
  python put_write_snapshot.py --out run/groundtruth.json --snapshot fixtures/groundtruth_snapshot.json
"""
import argparse
import json
import math
import os
import sys

RECIPE_ID = "put_write_snapshot"
VERSION = "1.0"

YEARS = ["2020", "2021", "2022", "2023", "2024", "2025"]
W_BOND = 0.95
W_SLEEVE = 0.05

# ---------------------------------------------------------------------------
# 冻结答案键（fraction 刻度）
# ---------------------------------------------------------------------------
# 中债-综合财富(总值)指数 CBA00101.CS 逐年回报。
#   2021 (+5.09%) 与 2024 (+7.61%) 为中债官方/媒体披露值 -> 容器容差 0.002
#   其余四年为公开基准/久期推算 -> 容器容差 0.005
BOND_WEALTH_YEARLY = {
    "2020": 0.0300,
    "2021": 0.0509,
    "2022": 0.0330,
    "2023": 0.0480,
    "2024": 0.0761,
    "2025": 0.0170,
}

# 沪深300 逐年回报 —— 两种合法口径**各自**都是外部真值。
# 容器的 csi300_2020..2025 检查点通过 gt_variants 路由：按作品声明（或数值推断）的口径
# 选中其中一条序列比对，另一条不参与该作品的评分。因此不再需要「一个容差同时容纳两者」，
# 也就不必像 v1.0 那样放弃沪深300 逐年检查点。
#
# 两条序列逐年最小间距 1.68pp（2021），远大于检查点容差 0.005（0.5pp），
# 故同一个数不可能同时通过两种口径 —— 这是 gt_variants 数值推断可靠的前提，
# 由下方 self_check 的 series_disjoint 断言守护。

# 全收益 H00300.CSI（含分红再投资，贴近 ETF 实际持有口径）。同时用于推导对照组年化。
CSI300_TOTAL_RETURN_YEARLY = {
    "2020": 0.2989,
    "2021": -0.0352,
    "2022": -0.1984,
    "2023": -0.0914,
    "2024": 0.1824,
    "2025": 0.2098,
}

# 价格指数 000300.SH（不含分红再投资）。合法但非最优的 ETF 腿口径选择 ——
# 口径优劣由 D1 第二层锚点承载，数值准确性由本序列在第一层度量。
CSI300_PRICE_YEARLY = {
    "2020": 0.2721,
    "2021": -0.0520,
    "2022": -0.2163,
    "2023": -0.1138,
    "2024": 0.1468,
    "2025": 0.1766,
}

# gt_variants 数值推断赖以区分两条序列的最小逐年间距（self_check 用）
CSI300_MIN_SERIES_GAP = 0.01

# 已知市场方向（self_check 用）：沪深300 连续三年下跌（2021 小幅、2022 大幅、2023 中幅），
# 2020 与 2024-2025 为正。价格指数与全收益口径的负回报年份集合一致。
CSI300_NEGATIVE_YEARS = {"2021", "2022", "2023"}

# 合理区间（self_check 用）
BOND_BAND = (0.005, 0.10)
BENCHMARK_ANNUAL_BAND = (0.03, 0.06)


def control_group_metrics(bond, sleeve):
    """年度再平衡的 95/5 组合：年度回报为加权和，再逐年连乘。

    年度再平衡意味着每年初权重都被重置回 95/5，因此该年的组合回报就是
    两条腿回报的加权和（无权重漂移）。跨年用几何连乘。
    """
    growth = 1.0
    yearly = {}
    for y in YEARS:
        r = W_BOND * bond[y] + W_SLEEVE * sleeve[y]
        yearly[y] = round(r, 8)
        growth *= (1.0 + r)
    cumulative = growth - 1.0
    annualised = growth ** (1.0 / len(YEARS)) - 1.0
    return yearly, cumulative, annualised


def run_self_check(bond, sleeve, yearly, cumulative, annualised):
    """结构 + 数值合理性 + 算术闭环自检。返回 (passed, checks, failures)。"""
    checks, failures = [], []

    def record(ok, desc):
        checks.append({"check": desc, "ok": bool(ok)})
        if not ok:
            failures.append(desc)

    # --- 结构完整性 ---
    record(set(bond) == set(YEARS), f"债券逐年回报覆盖 {len(YEARS)} 个年度")
    record(set(sleeve) == set(YEARS), f"沪深300 逐年回报覆盖 {len(YEARS)} 个年度")
    record(all(isinstance(v, (int, float)) and math.isfinite(v) for v in bond.values()),
           "债券逐年回报全部为有限数值")
    record(all(isinstance(v, (int, float)) and math.isfinite(v) for v in sleeve.values()),
           "沪深300 逐年回报全部为有限数值")

    # --- 数值合理性 ---
    record(all(v > 0 for v in bond.values()),
           "债券财富指数逐年回报全部为正（该指数 2020-2025 每年均为正回报）")
    record(all(BOND_BAND[0] <= v <= BOND_BAND[1] for v in bond.values()),
           f"债券逐年回报落在合理区间 {BOND_BAND}")
    neg = {y for y, v in sleeve.items() if v < 0}
    record(neg == CSI300_NEGATIVE_YEARS,
           f"沪深300 负回报年份与已知市场方向一致（应为 {sorted(CSI300_NEGATIVE_YEARS)}）")

    # --- gt_variants 前提：两条沪深300 序列逐年必须充分分离 ---
    # 容器 csi300_* 检查点容差 0.005；若某年两口径间距小于 CSI300_MIN_SERIES_GAP，
    # 同一个数就可能同时通过两种口径，数值推断随之失效。此断言守护该前提。
    gaps = {y: abs(sleeve[y] - CSI300_PRICE_YEARLY[y]) for y in YEARS if y in CSI300_PRICE_YEARLY}
    tight = {y: round(g, 6) for y, g in gaps.items() if g < CSI300_MIN_SERIES_GAP}
    record(not tight,
           f"沪深300 两口径逐年间距均 >= {CSI300_MIN_SERIES_GAP}"
           f"（gt_variants 数值推断的前提）{f'；过近年份 {tight}' if tight else ''}")

    # --- 权重 ---
    record(abs((W_BOND + W_SLEEVE) - 1.0) < 1e-12, "组合权重合计为 1")

    # --- 算术闭环：由累计反推年化，与直接算出的年化必须一致 ---
    reconstructed = (1.0 + cumulative) ** (1.0 / len(YEARS)) - 1.0
    record(abs(reconstructed - annualised) < 1e-12,
           "对照组累计与年化互推闭合（(1+累计)^(1/6)-1 == 年化）")

    # --- 算术闭环：逐年连乘还原累计 ---
    growth = 1.0
    for y in YEARS:
        growth *= (1.0 + yearly[y])
    record(abs((growth - 1.0) - cumulative) < 1e-10,
           "对照组逐年回报连乘还原累计收益率")

    # --- 结果合理性 ---
    record(BENCHMARK_ANNUAL_BAND[0] <= annualised <= BENCHMARK_ANNUAL_BAND[1],
           f"对照组年化落在合理区间 {BENCHMARK_ANNUAL_BAND}（95% 低波债券主导）")

    return (len(failures) == 0), checks, failures


def load_snapshot(path):
    """可选 override：同样的嵌套结构。缺失的键回落到内嵌冻结值。"""
    with open(path, encoding="utf-8") as f:
        snap = json.load(f)
    bond = dict(BOND_WEALTH_YEARLY)
    sleeve = dict(CSI300_TOTAL_RETURN_YEARLY)
    bond.update((snap.get("bond_wealth_yearly") or {}))
    sleeve.update((snap.get("csi300_total_return_yearly") or {}))
    return bond, sleeve


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="where to write groundtruth.json")
    ap.add_argument("--snapshot", help="(optional) override answer key JSON, same nested shape")
    a = ap.parse_args()

    if a.snapshot:
        bond, sleeve = load_snapshot(a.snapshot)
        source = f"snapshot override: {a.snapshot}"
    else:
        bond, sleeve = dict(BOND_WEALTH_YEARLY), dict(CSI300_TOTAL_RETURN_YEARLY)
        source = "embedded frozen answer key"

    yearly, cumulative, annualised = control_group_metrics(bond, sleeve)
    passed, checks, failures = run_self_check(bond, sleeve, yearly, cumulative, annualised)

    # 用价格指数口径推出的对照组年化：benchmark_annual_return 的容差仍需同时覆盖两者
    # （该检查点未做 gt_variants 路由，见 gt_recipe.yaml 的 design_note）
    _, price_cum, price_annual = control_group_metrics(bond, CSI300_PRICE_YEARLY)

    values = {f"bond_{y}": round(bond[y], 6) for y in YEARS}
    values["benchmark_annual_return"] = round(annualised, 6)
    # 沪深300 两种口径各自作为 GT 键。容器的 csi300_<y> 检查点经 gt_variants 选中其一比对。
    # tr 取自 sleeve（而非模块常量），使 --snapshot override 能传播到检查点真值。
    for y in YEARS:
        values[f"csi300tr_{y}"] = round(sleeve[y], 6)
        values[f"csi300pr_{y}"] = round(CSI300_PRICE_YEARLY[y], 6)

    out = {
        "recipe": RECIPE_ID,
        "calculator_version": VERSION,
        "values": values,
        "side_outputs": {
            "benchmark_cum_return": round(cumulative, 6),
            "benchmark_yearly": yearly,
        },
        "provenance": {
            "source": source,
            "network": False,
            "scale": "fraction (5.09% -> 0.0509); gt_recipe MUST NOT declare percent_fields",
            "weights": {"bond": W_BOND, "sleeve": W_SLEEVE},
            "rebalancing": "annual — weights reset to 95/5 each year, so the yearly portfolio "
                           "return is the weighted sum of the two legs; chain-linked across years",
            "inputs": {
                "bond_wealth_yearly": bond,
                "csi300_total_return_yearly": sleeve,
            },
            "caliber_note": "沪深300 两种口径各自作为 GT 键输出（csi300tr_* / csi300pr_*）："
                            "容器的 csi300_<y> 检查点经 gt_variants 按作品声明（或数值推断）的口径"
                            "选中其一比对，另一条不参与该作品评分。对照组年化仍取全收益口径推导，"
                            "其容差（0.0015）依旧需同时覆盖两种口径。"
                            "口径**选择**的优劣由 D1 第二层锚点承载，口径内**数值**准确性由第一层度量。",
            "price_caliber_reference": {
                "csi300_price_yearly": CSI300_PRICE_YEARLY,
                "benchmark_annual_return": round(price_annual, 6),
                "benchmark_cum_return": round(price_cum, 6),
                "annual_gap_vs_total_return": round(abs(annualised - price_annual), 6),
                "cum_gap_vs_total_return": round(abs(cumulative - price_cum), 6),
            },
            "strategy_metrics_note": "策略组五项指标不在本计算器输出范围内：历史期权权利金无公开"
                                     "权威逐月序列，结果取决于定价假设（模型依赖，非数据缺失）。"
                                     "由容器的 reconcile/consistency/present 检查点承载。",
        },
        "self_check": {
            "passed": passed,
            "n_checks": len(checks),
            "checks": checks,
            "failures": failures,
        },
    }

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "recipe": RECIPE_ID,
        "n_values": len(values),
        "benchmark_annual_return": values["benchmark_annual_return"],
        "benchmark_cum_return": out["side_outputs"]["benchmark_cum_return"],
        "self_check_passed": passed,
        "failures": failures,
    }, ensure_ascii=False, indent=2))

    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # 中文(gbk) Windows 控制台
    except Exception:
        pass
    main()
