#!/usr/bin/env python3
"""Ground-truth calculator for S11 (news-data-retrieval / 金融消息面 Web Search) — v1.1 Snapshot.

S11 asks 12 scoreable news-retrieval questions across three 题目 (AI 新闻 / 一起并购交易 /
霍尔木兹海峡时间线). Every answer is a definite fact with a frozen answer key — there is nothing
to compute, only to pack, so this is a snapshot-to-GT calculator in the same shape as S10's
financial_data_snapshot.py: embedded constant -> flat checkpoint values -> structural self_check.

--- The 12-point scheme and how it is encoded -------------------------------------------------
题目一 3 分 + 题目二 3 分 + 题目三 6 分 = 12. Three points are compound (several values must be
right together) and one carries a threshold partial-credit rule, none of which a single binary
checkpoint expresses. They are encoded as follows and re-assembled into exactly 12 equally
weighted points by `grounding_group_weights` in spec.yaml:

  Q1_2 (Kimi K3 pricing, 3 prices; 全对=1 / 对两个=0.5 / 其余=0)  -- the only partial-credit point
      Q1_2_kimi_k3_pricing_all3  vector,    tol=0  -> passes only when all three prices are right
      Q1_2_kimi_k3_pricing_set   set_match, tol=1  -> passes when at least two of three are right
      weights 0.5 / 0.5  ->  1.0 / 0.5 / 0.0 exactly

  Q2_3 (交易金额 + 持股比例; 第三题全对才给分)
      Q2_3_deal_terms            vector, tol=0  -> all-or-nothing over both members

  Q3_4 (伊斯兰堡会谈的两日日期; 两日都对才给分, 无部分分)
      Q3_4_islamabad_talks_window vector, tol=0 -> all-or-nothing over both days

--- Date encoding ------------------------------------------------------------------------------
Dates are YYYYMMDD integers (2026年7月14日 -> 20260714) so the numeric grader can compare them.
THREE questions accept either of two adjacent dates (多来源口径差异, see the task's 口径说明 and
the 可接受答案 column). Those are encoded as a HALF-INTEGER midpoint with tol=0.5, which passes
for exactly the two accepted days and nothing else:

      题目三 第2点  20260407.5 ± 0.5  -> accepts 20260407 or 20260408
      题目三 第5点  20260617.5 ± 0.5  -> accepts 20260617 or 20260618
      题目三 第6点  20260707.5 ± 0.5  -> accepts 20260707 or 20260708

NOTE: this arithmetic only works for two adjacent days INSIDE one month (YYYYMMDD is not a linear
day scale across month boundaries). All three tolerant pairs satisfy that; the self-check enforces
it. If the answer key is ever revised to a cross-month pair, switch that checkpoint to a day-ordinal
encoding instead.

Usage:
  python news_search_snapshot.py --out run/groundtruth.json [--snapshot fixtures/groundtruth_snapshot.json]

Exit code 0 iff a self-check-passed groundtruth.json was written.
"""
import argparse
import datetime
import json
import math
import os
import sys

CALCULATOR_ID = "news_search_snapshot"
CALCULATOR_VERSION = "1.1"
DATA_CUTOFF = "2026-07-27"      # 题目规定的数据核验时点
N_POINTS = 12                   # 题目一 3 + 题目二 3 + 题目三 6

# --- Embedded authoritative answer key (from the S11 ground-truth doc) --------------------------
# Flat {checkpoint: value}. Shapes are dictated by the checkpoint types in checkpoint_schema.yaml.
ANSWER_KEY = {
    # ---- 题目一 AI 新闻 (3 分) ----
    # Q1.1 Anthropic 最新款大模型 (set_match, tol=0; 归一化为不含厂商前缀的型号名)
    "Q1_1_latest_anthropic_model": ["Opus 5"],
    # Q1.2 Kimi K3 官方 API 价格, USD / 1M token (vector, tol=0 —— 三项全对才通过)
    "Q1_2_kimi_k3_pricing_all3": {
        "input_cache_hit": 0.30,
        "input_cache_miss": 3.00,
        "output": 15.00,
    },
    # Q1.2 同样三个价格的集合形式 (set_match, tol=1 —— 至少答对两项即通过)
    # 成员为两位小数字符串, 以避开浮点 repr 歧义 (compare_set 用 str() 做身份比较)。
    "Q1_2_kimi_k3_pricing_set": ["0.30", "3.00", "15.00"],
    # Q1.3 WAIC 已举办届数 (number, tol=0)
    "Q1_3_waic_editions": 9,

    # ---- 题目二 黑芝麻智能收购亿智电子 (3 分) ----
    # Q2.1 正式发布完成股份收购公告的日期 (YYYYMMDD)
    "Q2_1_acquisition_completion_date": 20260714,
    # Q2.2 2026-2028 三年累计含税营业收入承诺下限, 单位 亿元 (number, tol=0)
    "Q2_2_revenue_commitment_3y": 12.00,
    # Q2.3 交易金额 (亿元) + 最终持股比例 (百分数数值) —— vector, tol=0, 全对才给分
    "Q2_3_deal_terms": {
        "deal_amount_yi_cny": 4.78,
        "stake_pct": 60,
    },

    # ---- 题目三 霍尔木兹海峡时间线 (6 分) ----
    # 第1点 下令执行 Operation Epic Fury —— 无容错
    "Q3_1_epic_fury_order_date": 20260228,
    # 第2点 接受两周临时停火安排 —— 4月7日 / 4月8日 均可 -> 半整数中点 + tol 0.5
    "Q3_2_two_week_ceasefire_date": 20260407.5,
    # 第3点 伊朗宣布有条件重新开放海峡航运 —— 无容错
    "Q3_3_strait_reopen_announced_date": 20260417,
    # 第4点 美伊伊斯兰堡会谈的两日日期 —— 无容错, 两日都对才给分 (vector, 无部分分)
    "Q3_4_islamabad_talks_window": {
        "talks_start": 20260411,
        "talks_end": 20260412,
    },
    # 第5点 美伊两国总统签署谅解备忘录 —— 6月17日 / 6月18日 均可 -> 半整数中点 + tol 0.5
    "Q3_5_mou_signed_date": 20260617.5,
    # 第6点 正式宣布休战安排已经结束 —— 7月7日 / 7月8日 均可 -> 半整数中点 + tol 0.5
    "Q3_6_truce_declared_over_date": 20260707.5,
}

# Per-checkpoint metadata carried into groundtruth.provenance for the audit trail.
FIELD_META = {
    "Q1_1_latest_anthropic_model": "题目一-1 Anthropic 最新款大模型 (set_match)",
    "Q1_2_kimi_k3_pricing_all3": "题目一-2 Kimi K3 API 价格三项全对 (vector, USD/1M token)",
    "Q1_2_kimi_k3_pricing_set": "题目一-2 Kimi K3 API 价格至少两项正确 (set_match, tol=1)",
    "Q1_3_waic_editions": "题目一-3 WAIC 已举办届数",
    "Q2_1_acquisition_completion_date": "题目二-1 完成股份收购公告日 (YYYYMMDD)",
    "Q2_2_revenue_commitment_3y": "题目二-2 2026-2028 三年累计含税营收承诺下限 (亿元)",
    "Q2_3_deal_terms": "题目二-3 交易金额 (亿元) + 最终持股比例 (%), 全对才给分 (vector)",
    "Q3_1_epic_fury_order_date": "题目三-1 下令执行 Operation Epic Fury (YYYYMMDD, 无容错)",
    "Q3_2_two_week_ceasefire_date": "题目三-2 接受两周临时停火安排 (YYYYMMDD, 双日容错)",
    "Q3_3_strait_reopen_announced_date": "题目三-3 伊朗宣布有条件重新开放海峡航运 (YYYYMMDD, 无容错)",
    "Q3_4_islamabad_talks_window": "题目三-4 美伊伊斯兰堡会谈两日日期 (vector, 两日全对才给分, 无容错)",
    "Q3_5_mou_signed_date": "题目三-5 美伊总统签署谅解备忘录 (YYYYMMDD, 双日容错)",
    "Q3_6_truce_declared_over_date": "题目三-6 宣布休战安排结束 (YYYYMMDD, 双日容错)",
}

# Structural contract per checkpoint, used by the self-check.
DATE_FIELDS = [
    "Q2_1_acquisition_completion_date",
    "Q3_1_epic_fury_order_date",
    "Q3_2_two_week_ceasefire_date",
    "Q3_3_strait_reopen_announced_date",
    "Q3_5_mou_signed_date",
    "Q3_6_truce_declared_over_date",
]
TOLERANT_DATE_FIELDS = {
    "Q3_2_two_week_ceasefire_date",
    "Q3_5_mou_signed_date",
    "Q3_6_truce_declared_over_date",
}
PLAIN_NUMBER_FIELDS = ["Q1_3_waic_editions", "Q2_2_revenue_commitment_3y"]
VECTOR_FIELDS = {
    "Q1_2_kimi_k3_pricing_all3": ["input_cache_hit", "input_cache_miss", "output"],
    "Q2_3_deal_terms": ["deal_amount_yi_cny", "stake_pct"],
    "Q3_4_islamabad_talks_window": ["talks_start", "talks_end"],
}
# Vector fields whose members are themselves YYYYMMDD dates that must be consecutive days.
DATE_WINDOW_FIELDS = {"Q3_4_islamabad_talks_window": ("talks_start", "talks_end")}
SET_FIELDS = ["Q1_1_latest_anthropic_model", "Q1_2_kimi_k3_pricing_set"]

EXPECTED_FIELDS = list(ANSWER_KEY.keys())
# 数据核验时点之前的 2026 年内事件 —— 日期合理性区间
DATE_LO, DATE_HI = 20260101, 20261231


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _valid_yyyymmdd(n):
    """True iff the integer n is a real calendar date in YYYYMMDD form."""
    try:
        y, md = divmod(int(n), 10000)
        m, d = divmod(md, 100)
        datetime.date(y, m, d)
        return True
    except (ValueError, TypeError):
        return False


def _to_date(n):
    y, md = divmod(int(n), 10000)
    m, d = divmod(md, 100)
    return datetime.date(y, m, d)


def compute_self_check(values):
    """Structural GT self-check: every checkpoint present with the shape its type requires,
    dates real and inside the task window, tolerant dates encoded as half-integer midpoints
    whose two neighbours are both real same-month days, and two-day windows genuinely
    consecutive."""
    issues = []
    verified = 0

    for f in EXPECTED_FIELDS:
        if f not in values or values[f] is None:
            issues.append(f"{f}: MISSING")

    for f in DATE_FIELDS:
        v = values.get(f)
        if v is None:
            continue
        if not _is_num(v):
            issues.append(f"{f}: date not numeric ({v!r})"); continue
        v = float(v)
        if not (DATE_LO <= v <= DATE_HI):
            issues.append(f"{f}: date {v:g} outside task window [{DATE_LO},{DATE_HI}]"); continue
        if f in TOLERANT_DATE_FIELDS:
            # must be an exact half-integer midpoint between two same-month calendar days
            if abs(v - (math.floor(v) + 0.5)) > 1e-9:
                issues.append(f"{f}: tolerant date {v:g} is not a half-integer midpoint"); continue
            lo, hi = int(math.floor(v)), int(math.floor(v)) + 1
            if not (_valid_yyyymmdd(lo) and _valid_yyyymmdd(hi)):
                issues.append(f"{f}: midpoint {v:g} does not bracket two real dates ({lo}, {hi})"); continue
            if (lo // 100) != (hi // 100):
                issues.append(f"{f}: midpoint {v:g} straddles a month boundary — "
                              f"YYYYMMDD ±0.5 is not a valid two-day window there"); continue
        else:
            if not float(v).is_integer() or not _valid_yyyymmdd(v):
                issues.append(f"{f}: {v:g} is not a real YYYYMMDD calendar date"); continue
        verified += 1

    for f in PLAIN_NUMBER_FIELDS:
        v = values.get(f)
        if v is None:
            continue
        if not _is_num(v):
            issues.append(f"{f}: not numeric ({v!r})"); continue
        if float(v) <= 0:
            issues.append(f"{f}: expected a positive magnitude, got {v!r}"); continue
        verified += 1

    for f, keys in VECTOR_FIELDS.items():
        v = values.get(f)
        if v is None:
            continue
        if not isinstance(v, dict):
            issues.append(f"{f}: expected an object keyed {keys}, got {type(v).__name__}"); continue
        if sorted(v.keys()) != sorted(keys):
            issues.append(f"{f}: keys {sorted(v.keys())} != required {sorted(keys)}"); continue
        bad = [k for k, x in v.items() if not _is_num(x)]
        if bad:
            issues.append(f"{f}: non-numeric members {bad}"); continue
        if f in DATE_WINDOW_FIELDS:
            ks, ke = DATE_WINDOW_FIELDS[f]
            s, e = v[ks], v[ke]
            if not (_valid_yyyymmdd(s) and _valid_yyyymmdd(e)):
                issues.append(f"{f}: window bounds {s}/{e} are not real YYYYMMDD dates"); continue
            if not (DATE_LO <= float(s) <= DATE_HI and DATE_LO <= float(e) <= DATE_HI):
                issues.append(f"{f}: window {s}-{e} outside task window"); continue
            if (_to_date(e) - _to_date(s)).days != 1:
                issues.append(f"{f}: {s}-{e} is not a two-consecutive-day window"); continue
        verified += 1

    for f in SET_FIELDS:
        v = values.get(f)
        if v is None:
            continue
        if not isinstance(v, list) or not v:
            issues.append(f"{f}: expected a non-empty list, got {v!r}"); continue
        if any(not isinstance(x, str) or not x.strip() for x in v):
            issues.append(f"{f}: set members must be non-empty strings, got {v!r}"); continue
        if len({x.strip().upper() for x in v}) != len(v):
            issues.append(f"{f}: set members are not distinct after normalization ({v!r})"); continue
        verified += 1

    # The only split point (Q1_2) needs both legs, or the 12-point weighting breaks.
    pair = ("Q1_2_kimi_k3_pricing_all3", "Q1_2_kimi_k3_pricing_set")
    if any(values.get(p) is None for p in pair):
        issues.append(f"split-question pair {pair} incomplete — the 12-point weighting would break")
    # The all3 vector and the 2-of-3 set must describe the same three prices.
    allv, sets = values.get("Q1_2_kimi_k3_pricing_all3"), values.get("Q1_2_kimi_k3_pricing_set")
    if isinstance(allv, dict) and isinstance(sets, list):
        as_str = {f"{float(x):.2f}" for x in allv.values() if _is_num(x)}
        if as_str != {str(s).strip() for s in sets}:
            issues.append(f"Q1_2 legs disagree: vector {sorted(as_str)} vs set {sorted(sets)}")

    return {
        "passed": len(issues) == 0,
        "checks": issues,
        "n_verified": verified,
        "n_expected": len(EXPECTED_FIELDS),
        "n_points": N_POINTS,
    }


def main():
    ap = argparse.ArgumentParser(
        description="S11 news-data-retrieval ground-truth calculator (v1.1 snapshot)")
    ap.add_argument("--out", required=True, help="output groundtruth.json path")
    ap.add_argument("--snapshot", default=None,
                    help="(optional) override answer key JSON (flat {checkpoint: value}, "
                         "or wrapped as {\"values\": {...}})")
    a = ap.parse_args()

    source = "embedded"
    answer_key = dict(ANSWER_KEY)
    if a.snapshot:
        with open(a.snapshot, encoding="utf-8") as f:
            snap = json.load(f)
        answer_key = snap.get("values", snap)
        source = os.path.basename(a.snapshot)
        print(f"Using snapshot override: {source}", flush=True)

    values = {f: answer_key.get(f) for f in EXPECTED_FIELDS}
    # Carry over any extra keys an override supplies, so a newer key is not silently dropped.
    for k, v in answer_key.items():
        values.setdefault(k, v)

    sc = compute_self_check(values)

    gt = {
        "task_id": "S11",
        "values": values,
        "recipe": CALCULATOR_ID,
        "self_check": sc,
        "provenance": {
            "calculator": f"{CALCULATOR_ID}.py v{CALCULATOR_VERSION} (snapshot)",
            "source": source,
            "data_cutoff": DATA_CUTOFF,
            "fields": FIELD_META,
            "encoding": {
                "dates": "YYYYMMDD integer (2026年7月14日 -> 20260714)",
                "tolerant_dates": "half-integer midpoint + tol 0.5 == the two accepted adjacent days "
                                  "(题目三 第2/第5/第6 点)",
                "two_day_window": "题目三 第4点 is a vector of two consecutive YYYYMMDD days, "
                                  "tol=0 -> both days required, no partial credit",
                "prices": "USD per 1M token",
                "amounts": "亿元 (CNY)",
                "stake_pct": "percent magnitude (60 == 60%), NOT a fraction — vector members are "
                             "not percent-canonicalized by the extractor validator",
            },
            "point_scheme": f"{N_POINTS} 分：题目一 3 + 题目二 3 + 题目三 6；由 "
                            f"spec.grounding_group_weights 把 {len(EXPECTED_FIELDS)} 个检查点"
                            f"重新组装为 {N_POINTS} 个等权分点",
        },
    }

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(gt, f, ensure_ascii=False, indent=2)

    if sc["passed"]:
        print("=== S11 GT Construction Complete ===", flush=True)
        print(f"Self-check passed ({sc['n_verified']}/{sc['n_expected']} checkpoints verified, "
              f"{N_POINTS}-point scheme). Source: {source}", flush=True)
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
