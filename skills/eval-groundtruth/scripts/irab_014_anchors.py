#!/usr/bin/env python3
"""S18 五粮液(000858.SZ) 多空综合分析 —— 基准真值(冻结快照, 网络无关).

真外部真值, 全部来自 IRAB irab-014 gold facts (五粮液2025年报/2026Q1季报官方披露数据,
以及贵州茅台/泸州老窖/白酒行业可比数据), 数据基准约 2026-06-12。

刻意不设点值真值的量(gold 原文本身是区间/多口径, 见 checkpoint_schema.yaml K 组):
批价(挂牌 vs 实际成交两种口径)、渠道库存(区间)、股息率(区间)、回购/集团增持计划(区间)、
账上货币资金(区间)。编一个点值会惩罚"如实标注区间"这一 gold 明确要求的正确行为。

用法:
  python irab_014_anchors.py --out run/groundtruth.json
"""
import argparse
import json
import os
import sys

RECIPE_ID = "irab_014_anchors"
VERSION = "1.0"

VALUES = {
    # --- A: 2025前三季度, 重述前/重述后 ---
    "revenue_2025_9m_pre": 609.45,
    "net_profit_2025_9m_pre": 215.11,
    "revenue_2025_9m_post": 306.38,
    "net_profit_2025_9m_post": 64.75,

    # --- B: 2025年报全年(重述后) ---
    "revenue_2025_full": 405.29,
    "net_profit_2025_full": 89.54,
    "gross_margin_2025": 0.7754,
    "gross_margin_2024": 0.7705,
    "operating_cashflow_2025": 297.06,

    # --- C: 历史对照(旧口径, 未被重述调整) ---
    "revenue_2024_full": 891.75,
    "net_profit_2024_full": 318.53,
    "revenue_2023_full": 832.72,

    # --- D: 2025分季度(重述后) ---
    "q1_2025_revenue": 170.86, "q2_2025_revenue": 64.24, "q3_2025_revenue": 71.28, "q4_2025_revenue": 98.91,
    "q1_2025_np": 44.16, "q2_2025_np": 2.08, "q3_2025_np": 18.51, "q4_2025_np": 24.80,

    # --- E: 2026Q1(重述后) ---
    "revenue_2026q1": 228.38,
    "net_profit_2026q1": 80.63,
    "gross_margin_2026q1": 0.8143,
    "operating_cashflow_2026q1": -25.35,

    # --- F: 合同负债 ---
    "contract_liability_2024_end": 116.90,
    "contract_liability_2025_end": 134.60,
    "contract_liability_2026q1_end": 141.38,

    # --- G: 股东回报(仅两项有单一点值来源) ---
    "dividend_total_fy2025": 200.0,
    "dividend_payout_ratio_fy2025": 2.23,

    # --- H: 估值(截至约2026-06-12) ---
    "market_cap": 3100.0,
    "pb": 2.42,
    "pe_ttm": 143.8,
    "stock_price": 79.92,

    # --- I: 可比公司与行业 ---
    "moutai_revenue_total_2025": 1720.0,
    "moutai_net_profit_2025": 823.0,
    "moutai_gross_margin_2025": 0.913,
    "moutai_pe_ttm": 19.5,
    "moutai_pb": 5.96,
    "moutai_dividend_yield": 0.0375,
    "moutai_payout_ratio": 0.79,
    "luzhou_pe_ttm": 12.6,
    "luzhou_dividend_yield": 0.058,
    "baijiu_industry_revenue_yoy_2025": -0.059,
    "baijiu_industry_netprofit_yoy_2025": -0.0693,
}


def run_self_check(v):
    checks, failures = [], []

    def record(ok, desc):
        checks.append({"check": desc, "ok": bool(ok)})
        if not ok:
            failures.append(desc)

    record(len(v) == len(VALUES), "GT 值数量与声明一致")
    record(all(isinstance(x, (int, float)) for x in v.values()), "全部 GT 值为数值且有限")

    # --- 重述前后必须不同(否则说明重述这件事本身没被建模) ---
    record(v["revenue_2025_9m_pre"] != v["revenue_2025_9m_post"], "重述前后前三季营业收入应不同")
    record(v["net_profit_2025_9m_pre"] != v["net_profit_2025_9m_post"], "重述前后前三季归母净利润应不同")
    record(v["revenue_2025_9m_pre"] > v["revenue_2025_9m_post"], "重述前营收应高于重述后(发货确认更靠前)")

    # --- 季度闭合: 四个季度之和应等于全年(重述后口径), 验证自己转录的数字没有算错 ---
    q_rev_sum = v["q1_2025_revenue"] + v["q2_2025_revenue"] + v["q3_2025_revenue"] + v["q4_2025_revenue"]
    record(abs(q_rev_sum - v["revenue_2025_full"]) < 0.05,
           f"四季度营业收入之和({q_rev_sum:.2f}) 与全年({v['revenue_2025_full']}) 一致(±0.05亿)")
    q_np_sum = v["q1_2025_np"] + v["q2_2025_np"] + v["q3_2025_np"] + v["q4_2025_np"]
    record(abs(q_np_sum - v["net_profit_2025_full"]) < 0.1,
           f"四季度归母净利润之和({q_np_sum:.2f}) 与全年({v['net_profit_2025_full']}) 一致(±0.1亿)")

    # --- Q4 陷阱红线: Q4 不应等于"重述前前三季"减"重述后全年"这种跨口径混算 ---
    wrong_q4 = v["revenue_2025_9m_pre"] - v["revenue_2025_full"]  # 会得到一个荒谬的负值量级
    record(abs(v["q4_2025_revenue"] - wrong_q4) > 50,
           "真实Q4营业收入应明显偏离'重述前前三季-重述后全年'这一跨口径混算得出的错误值")

    # --- 同比方向: 2026Q1 应为高增, 全年重述后应大幅低于重述前叙事(但仍为正) ---
    record(v["revenue_2026q1"] > 0 and v["net_profit_2026q1"] > 0, "2026Q1营收/净利润均为正")
    record(v["net_profit_2025_full"] > 0, "2025年重述后归母净利润仍为正(非亏损)")

    # --- 合理区间 ---
    record(0.5 < v["gross_margin_2025"] < 1.0, "毛利率落在合理区间")
    record(v["pe_ttm"] > 50, "PE(TTM) 应明显偏高(重述后净利分母被压低导致失真高读数)")
    record(v["moutai_pe_ttm"] < 30, "茅台PE(TTM) 应明显低于五粮液的失真高读数, 反映五粮液PE(TTM)不可直接横向比较")

    return (len(failures) == 0), checks, failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--snapshot")
    a = ap.parse_args()

    passed, checks, failures = run_self_check(VALUES)

    out = {
        "recipe": RECIPE_ID, "calculator_version": VERSION,
        "values": VALUES,
        "provenance": {
            "source": "IRAB irab-014 gold facts (五粮液2025年报/《前期会计差错更正公告》/2026Q1季报; 贵州茅台/泸州老窖同期披露; 白酒行业统计)",
            "network": False,
            "scale": "亿元 for absolute financials; fraction for pct fields; PE/PB as plain ratios",
            "data_cutoff": "约2026-06-12(估值数据基准); 财务数据基准为2026-04-30披露的FY2025年报与随后的2026Q1季报",
            "coverage_gaps": {
                "batch_price": "挂牌口径(约840元) vs 实际成交口径(约770-800元)——两种合法口径均为区间, 不设点值真值",
                "channel_inventory": "约1.5-2个月, 区间值, 不设点值真值",
                "dividend_yield": "约5.3%-5.8%, 区间值, 不设点值真值(分红总额/分红率已作为G组独立锚点设点值)",
                "buyback_and_group_stake": "回购约80-100亿元, 集团拟增持约30-50亿元, 账上货币资金约1200-1243亿元, 均为区间值",
            },
        },
        "self_check": {"passed": passed, "n_checks": len(checks), "checks": checks, "failures": failures},
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps({"recipe": RECIPE_ID, "n_values": len(VALUES), "self_check_passed": passed,
                       "failures": failures}, ensure_ascii=False, indent=2))
    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
