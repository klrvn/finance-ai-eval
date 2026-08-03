#!/usr/bin/env python3
"""Ground-truth calculator for S10 (financial-data-retrieval) — v1.1 Snapshot.

S10 asks for 3 fixed, definite financial figures (归母净利润 / 加权净资产收益率 / 存货期末余额)
for three companies across their most recent three fiscal years. That window is PER COMPANY:
ZTE / POPMART report on the calendar year (2023FY / 2024FY / 2025FY), while NVIDIA's fiscal year
ends in late January of the following calendar year, so as of the 2026-07-20 data cutoff its
latest three reported fiscal years are FY2024 / FY2025 / FY2026.
Unlike S9 (live market data), S10's ground truth is a FROZEN answer key sourced from company
filings — there is nothing to fetch. This calculator therefore packs the authoritative answer
key as an embedded constant, flattens it into the 27 checkpoint values the grader consumes, and
emits a structural self_check (all 27 present, numeric, finite; ROE within a plausible band).

This mirrors S9's legacy v1.0 snapshot mode: a deterministic, self-checked, network-free
snapshot-to-GT calculator. An optional --snapshot override lets an operator substitute an updated
answer key (same nested shape) without editing this script.

口径 (calibers, fixed by the task):
  net_profit : 归属母公司股东的净利润 (归母净利润), 单位 亿 (CNY for ZTE/POPMART, USD for NVDA)
  roe        : 加权净资产收益率, 百分比 (e.g. 15.19 = 15.19%)
  inventory  : 资产负债表 存货/Inventories 期末余额, 单位 亿
Rounding (fixed by the task): net_profit / inventory -> 2 decimals; roe -> 2 decimals of percent.

Companies (with their frozen three-fiscal-year window):
  ZTE      中兴通讯 000063.SZ  (A股/深交所, CNY, 亿元;   2023FY / 2024FY / 2025FY, 自然年)
  POPMART  泡泡玛特 09992.HK   (港股/港交所, CNY, 亿元;   2023FY / 2024FY / 2025FY, 自然年)
  NVDA     英伟达   NVDA.O      (美股/纳斯达克, USD, 亿美元; FY2024 / FY2025 / FY2026, 财年1月末结束)

Usage:
  python financial_data_snapshot.py --out run/groundtruth.json [--snapshot fixtures/groundtruth_snapshot.json]

Exit code 0 iff a self-check-passed groundtruth.json was written.
"""
import argparse
import json
import math
import os
import sys

CALCULATOR_ID = "financial_data_snapshot"
CALCULATOR_VERSION = "1.1"
DATA_CUTOFF = "2026-07-20"

METRICS = ["net_profit", "roe", "inventory"]

# v1.1: the three-fiscal-year window is PER COMPANY, not global. ZTE / POPMART report on the
# calendar year, so their latest three fiscal years are 2023/2024/2025. NVIDIA's fiscal year ends
# in late January of the FOLLOWING calendar year (FY2026 ended 2026-01-25), so as of the
# 2026-07-20 cutoff its latest three reported fiscal years are FY2024/FY2025/FY2026 — NOT
# FY2023/FY2024/FY2025 (v1.0 shifted NVIDIA's window one year too early).
# These years are frozen together with the checkpoint field names ({COMPANY}_{FY}_{METRIC}), so a
# --snapshot override must use the same years; if it does not, the values land nowhere and the
# self-check fails rather than silently grading against absent ground truth.
FISCAL_YEARS = {
    "ZTE": ["2023", "2024", "2025"],
    "POPMART": ["2023", "2024", "2025"],
    "NVDA": ["2024", "2025", "2026"],
}

# --- Embedded authoritative answer key (from S10 ground truth, company filings) -------------
# Values are ALREADY rounded per the task rounding policy. Structure:
#   ANSWER_KEY[company] = {
#       "identifier": <ticker>, "listing": ..., "currency": ..., "unit": ...,
#       "years": { "<FY>": {"net_profit": .., "roe": .., "inventory": ..}, ... } }
ANSWER_KEY = {
    "ZTE": {
        "name": "中兴通讯",
        "identifier": "000063.SZ",
        "listing": "A股/深交所",
        "currency": "CNY",
        "unit": "亿元",
        "fiscal_year_end": "自然年 (12-31)",
        "years": {
            "2023": {"net_profit": 93.26, "roe": 15.19, "inventory": 411.31},
            "2024": {"net_profit": 84.25, "roe": 11.97, "inventory": 412.58},
            "2025": {"net_profit": 56.18, "roe": 7.58, "inventory": 470.17},
        },
    },
    "POPMART": {
        "name": "泡泡玛特",
        "identifier": "09992.HK",
        "listing": "港股/港交所",
        "currency": "CNY",
        "unit": "亿元",
        "fiscal_year_end": "自然年 (12-31)",
        "years": {
            "2023": {"net_profit": 10.82, "roe": 14.69, "inventory": 9.05},
            "2024": {"net_profit": 31.25, "roe": 33.87, "inventory": 15.25},
            "2025": {"net_profit": 127.76, "roe": 77.52, "inventory": 54.73},
        },
    },
    "NVDA": {
        "name": "英伟达",
        "identifier": "NVDA.O",
        "listing": "美股/纳斯达克",
        "currency": "USD",
        "unit": "亿美元",
        "fiscal_year_end": "财年1月末结束 (FY2026 结束于 2026-01)",
        "years": {
            "2024": {"net_profit": 297.60, "roe": 91.46, "inventory": 52.82},
            "2025": {"net_profit": 728.80, "roe": 119.18, "inventory": 100.80},
            "2026": {"net_profit": 1200.67, "roe": 101.49, "inventory": 214.03},
        },
    },
}

COMPANIES = list(ANSWER_KEY.keys())

# ROE plausibility band (percent) for the structural self-check.
ROE_MIN, ROE_MAX = -200.0, 200.0


def flatten(answer_key):
    """Turn the nested answer key into the flat {COMPANY_FY_METRIC: value} checkpoint map."""
    values = {}
    meta = {}
    for company in COMPANIES:
        entry = answer_key[company]
        meta[company] = {
            "name": entry.get("name"),
            "identifier": entry.get("identifier"),
            "listing": entry.get("listing"),
            "currency": entry.get("currency"),
            "unit": entry.get("unit"),
            "fiscal_year_end": entry.get("fiscal_year_end"),
            "fiscal_years": list(FISCAL_YEARS[company]),
        }
        years = entry.get("years", {})
        for fy in FISCAL_YEARS[company]:
            row = years.get(fy, {})
            for metric in METRICS:
                key = f"{company}_{fy}_{metric}"
                values[key] = row.get(metric)
    return values, meta


def compute_self_check(values):
    """Structural GT self-check: every one of the 27 values present, numeric, finite;
    ROE within a plausible band. No cross-derivation exists for these independent filing
    figures, so the self-check validates completeness and sanity rather than arithmetic."""
    issues = []
    expected = 0
    present_ok = 0
    for company in COMPANIES:
        for fy in FISCAL_YEARS[company]:
            for metric in METRICS:
                expected += 1
                key = f"{company}_{fy}_{metric}"
                v = values.get(key)
                if v is None:
                    issues.append(f"{key}: MISSING")
                    continue
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    issues.append(f"{key}: not numeric ({v!r})")
                    continue
                if not math.isfinite(float(v)):
                    issues.append(f"{key}: not finite ({v!r})")
                    continue
                if metric == "roe" and not (ROE_MIN <= float(v) <= ROE_MAX):
                    issues.append(f"{key}: roe {v} outside plausible band [{ROE_MIN},{ROE_MAX}]")
                    continue
                present_ok += 1
    return {
        "passed": len(issues) == 0,
        "checks": issues,
        "n_verified": present_ok,
        "n_expected": expected,
    }


def main():
    ap = argparse.ArgumentParser(description="S10 financial-data-retrieval ground-truth calculator (v1.0 snapshot)")
    ap.add_argument("--out", required=True, help="output groundtruth.json path")
    ap.add_argument("--snapshot", default=None,
                    help="(optional) override answer key JSON (same nested shape as the embedded ANSWER_KEY)")
    a = ap.parse_args()

    source = "embedded"
    answer_key = ANSWER_KEY
    if a.snapshot:
        with open(a.snapshot, encoding="utf-8") as f:
            snap = json.load(f)
        # Accept either the full {companies:{...}} wrapper or a bare company map.
        answer_key = snap.get("companies", snap)
        source = os.path.basename(a.snapshot)
        print(f"Using snapshot override: {source}", flush=True)

    values, meta = flatten(answer_key)

    # Round every numeric value to 2 decimals (idempotent for the embedded key; guards overrides).
    for k, v in list(values.items()):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            values[k] = round(float(v), 2)

    sc = compute_self_check(values)

    gt = {
        "task_id": "S10",
        "values": values,
        "recipe": CALCULATOR_ID,
        "self_check": sc,
        "provenance": {
            "calculator": f"{CALCULATOR_ID}.py v{CALCULATOR_VERSION} (snapshot)",
            "source": source,
            "data_cutoff": DATA_CUTOFF,
            "companies": meta,
            "calibers": {
                "net_profit": "归属母公司股东的净利润 (归母净利润)",
                "roe": "加权净资产收益率 (percent)",
                "inventory": "资产负债表 存货/Inventories 期末余额",
            },
            "rounding": "net_profit/inventory -> 2 decimals; roe -> 2 decimals of percent",
        },
    }

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(gt, f, ensure_ascii=False, indent=2)

    if sc["passed"]:
        print(f"=== S10 GT Construction Complete ===", flush=True)
        print(f"Self-check passed ({sc['n_verified']}/{sc['n_expected']} values verified). Source: {source}", flush=True)
    else:
        print("Self-check FAILED!", file=sys.stderr, flush=True)
        for issue in sc["checks"]:
            print(f"  - {issue}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
