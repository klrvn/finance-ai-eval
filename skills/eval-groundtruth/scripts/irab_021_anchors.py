#!/usr/bin/env python3
"""S16 招商银行(600036.SH) 2025年报杜邦分解 —— 基准真值(冻结快照, 网络无关).

真值分两类:
  1) 原始财务数据(营业收入/归母净利润/总资产/归母权益, 2023-2025年末, 以及年报披露的
     ROAE/ROAA) —— 真外部真值, 来自招商银行 A 股定期报告, 全部来自 IRAB irab-021 gold facts。
  2) 杜邦三因子(净利率/总资产周转率/权益乘数)与三因子乘积 —— 由第1类原始数据**计算**得出,
     而非硬编码, 使 self_check 真正验证算术。周转率/权益乘数/乘积存在"期末口径"与"平均口径"
     两种合法计量方式, 两条序列都作为独立GT键输出, 容器的 turnover_*/leverage_*/roe_product_*
     检查点通过 gt_variants 按候选作品声明的口径路由到对应序列。净利率两种口径下数值相同,
     不设 gt_variants。

用法:
  python irab_021_anchors.py --out run/groundtruth.json
"""
import argparse
import json
import os
import sys

RECIPE_ID = "irab_021_anchors"
VERSION = "1.0"

# ---------------------------------------------------------------------------
# 冻结原始数据(亿元 / fraction), 来自 IRAB irab-021 gold facts
# ---------------------------------------------------------------------------
REVENUE = {"2025": 3375.32, "2024": 3374.88}
NET_PROFIT = {"2025": 1501.81, "2024": 1483.91}
TOTAL_ASSETS_EOP = {"2023": 110284.83, "2024": 121520.36, "2025": 130705.23}
EQUITY_EOP = {"2023": 10763.70, "2024": 12260.14, "2025": 12728.75}

DISCLOSED_ROAE = {"2025": 0.1344, "2024": 0.1449}
DISCLOSED_ROAA = {"2025": 0.0119, "2024": 0.0128}

# gold 自身给出的四舍五入参考值(±容差内), 用于 self_check 交叉校验计算结果没有算错
GOLD_REFERENCE = {
    "net_margin_2025": 0.4449, "net_margin_2024": 0.4397,
    "turnover_2025_eop": 0.02582, "turnover_2025_avg": 0.02676,
    "turnover_2024_eop": 0.02777, "turnover_2024_avg": 0.02912,
    "leverage_2025_eop": 10.27, "leverage_2025_avg": 10.09,
    "leverage_2024_eop": 9.91, "leverage_2024_avg": 10.07,
    "product_2025_eop": 0.1180, "product_2025_avg": 0.1202,
    "product_2024_eop": 0.1210, "product_2024_avg": 0.1289,
}


def avg(a, b):
    return (a + b) / 2.0


def compute():
    avg_assets = {"2025": avg(TOTAL_ASSETS_EOP["2024"], TOTAL_ASSETS_EOP["2025"]),
                  "2024": avg(TOTAL_ASSETS_EOP["2023"], TOTAL_ASSETS_EOP["2024"])}
    avg_equity = {"2025": avg(EQUITY_EOP["2024"], EQUITY_EOP["2025"]),
                  "2024": avg(EQUITY_EOP["2023"], EQUITY_EOP["2024"])}

    net_margin = {y: NET_PROFIT[y] / REVENUE[y] for y in ("2025", "2024")}

    turnover_eop = {y: REVENUE[y] / TOTAL_ASSETS_EOP[y] for y in ("2025", "2024")}
    turnover_avg = {y: REVENUE[y] / avg_assets[y] for y in ("2025", "2024")}

    leverage_eop = {y: TOTAL_ASSETS_EOP[y] / EQUITY_EOP[y] for y in ("2025", "2024")}
    leverage_avg = {y: avg_assets[y] / avg_equity[y] for y in ("2025", "2024")}

    product_eop = {y: NET_PROFIT[y] / EQUITY_EOP[y] for y in ("2025", "2024")}
    product_avg = {y: NET_PROFIT[y] / avg_equity[y] for y in ("2025", "2024")}

    return {
        "avg_assets": avg_assets, "avg_equity": avg_equity, "net_margin": net_margin,
        "turnover_eop": turnover_eop, "turnover_avg": turnover_avg,
        "leverage_eop": leverage_eop, "leverage_avg": leverage_avg,
        "product_eop": product_eop, "product_avg": product_avg,
    }


def run_self_check(c):
    checks, failures = [], []

    def record(ok, desc):
        checks.append({"check": desc, "ok": bool(ok)})
        if not ok:
            failures.append(desc)

    # --- 算术闭环: 期末/平均口径下三因子乘积必须严格等于 净利润/(对应)权益 (数学恒等式) ---
    for y in ("2025", "2024"):
        recomputed = c["net_margin"][y] * c["turnover_eop"][y] * c["leverage_eop"][y]
        record(abs(recomputed - c["product_eop"][y]) < 1e-9,
               f"{y}年期末口径: 净利率×周转率×权益乘数 严格等于 归母净利润/归母权益 (机器精度)")
        recomputed_avg = c["net_margin"][y] * c["turnover_avg"][y] * c["leverage_avg"][y]
        record(abs(recomputed_avg - c["product_avg"][y]) < 1e-9,
               f"{y}年平均口径: 净利率×周转率×权益乘数 严格等于 归母净利润/平均归母权益 (机器精度)")

    # --- 与 gold 原文四舍五入参考值交叉校验(±容差), 防止本计算器自身抄错原始数据 ---
    tol_pct, tol_ratio = 0.0006, 0.02
    pairs = [
        ("net_margin_2025", c["net_margin"]["2025"], tol_pct),
        ("net_margin_2024", c["net_margin"]["2024"], tol_pct),
        ("turnover_2025_eop", c["turnover_eop"]["2025"], tol_pct),
        ("turnover_2025_avg", c["turnover_avg"]["2025"], tol_pct),
        ("turnover_2024_eop", c["turnover_eop"]["2024"], tol_pct),
        ("turnover_2024_avg", c["turnover_avg"]["2024"], tol_pct),
        ("leverage_2025_eop", c["leverage_eop"]["2025"], tol_ratio),
        ("leverage_2025_avg", c["leverage_avg"]["2025"], tol_ratio),
        ("leverage_2024_eop", c["leverage_eop"]["2024"], tol_ratio),
        ("leverage_2024_avg", c["leverage_avg"]["2024"], tol_ratio),
        ("product_2025_eop", c["product_eop"]["2025"], tol_pct),
        ("product_2025_avg", c["product_avg"]["2025"], tol_pct),
        ("product_2024_eop", c["product_eop"]["2024"], tol_pct),
        ("product_2024_avg", c["product_avg"]["2024"], tol_pct),
    ]
    for key, val, tol in pairs:
        ref = GOLD_REFERENCE[key]
        record(abs(val - ref) <= tol, f"{key} 计算值({val:.5f}) 与 gold 参考值({ref}) 一致(±{tol})")

    # --- ROAA 可由 净利润/平均总资产 精确复算, 验证总资产与净利为同一口径体系 ---
    record(abs(NET_PROFIT["2025"] / c["avg_assets"]["2025"] - DISCLOSED_ROAA["2025"]) < 0.0005,
           "ROAA 可由 归母净利润/平均总资产 精确复算, 与披露值一致")

    return (len(failures) == 0), checks, failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--snapshot")
    a = ap.parse_args()

    c = compute()
    passed, checks, failures = run_self_check(c)

    values = {
        "revenue_2025": REVENUE["2025"], "net_profit_2025": NET_PROFIT["2025"],
        "total_assets_2025_eop": TOTAL_ASSETS_EOP["2025"], "equity_2025_eop": EQUITY_EOP["2025"],
        "revenue_2024": REVENUE["2024"], "net_profit_2024": NET_PROFIT["2024"],
        "total_assets_2024_eop": TOTAL_ASSETS_EOP["2024"], "equity_2024_eop": EQUITY_EOP["2024"],
        "total_assets_2023_eop": TOTAL_ASSETS_EOP["2023"], "equity_2023_eop": EQUITY_EOP["2023"],
        "disclosed_roae_2025": DISCLOSED_ROAE["2025"], "disclosed_roae_2024": DISCLOSED_ROAE["2024"],
        "disclosed_roaa_2025": DISCLOSED_ROAA["2025"], "disclosed_roaa_2024": DISCLOSED_ROAA["2024"],
        "net_margin_2025": round(c["net_margin"]["2025"], 6),
        "net_margin_2024": round(c["net_margin"]["2024"], 6),
        "turnover_2025_eop": round(c["turnover_eop"]["2025"], 6),
        "turnover_2025_avg": round(c["turnover_avg"]["2025"], 6),
        "turnover_2024_eop": round(c["turnover_eop"]["2024"], 6),
        "turnover_2024_avg": round(c["turnover_avg"]["2024"], 6),
        "leverage_2025_eop": round(c["leverage_eop"]["2025"], 4),
        "leverage_2025_avg": round(c["leverage_avg"]["2025"], 4),
        "leverage_2024_eop": round(c["leverage_eop"]["2024"], 4),
        "leverage_2024_avg": round(c["leverage_avg"]["2024"], 4),
        "product_2025_eop": round(c["product_eop"]["2025"], 6),
        "product_2025_avg": round(c["product_avg"]["2025"], 6),
        "product_2024_eop": round(c["product_eop"]["2024"], 6),
        "product_2024_avg": round(c["product_avg"]["2024"], 6),
    }

    out = {
        "recipe": RECIPE_ID, "calculator_version": VERSION,
        "values": values,
        "provenance": {
            "source": "IRAB irab-021 gold facts (招商银行 2025/2024 年 A 股年度报告)",
            "network": False,
            "scale": "亿元 for raw financials; fraction for pct fields; leverage as a plain ratio",
            "avg_assets": c["avg_assets"], "avg_equity": c["avg_equity"],
        },
        "self_check": {"passed": passed, "n_checks": len(checks), "checks": checks, "failures": failures},
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps({"recipe": RECIPE_ID, "n_values": len(values), "self_check_passed": passed,
                       "failures": failures}, ensure_ascii=False, indent=2))
    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
