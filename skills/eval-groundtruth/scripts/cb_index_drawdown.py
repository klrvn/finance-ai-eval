#!/usr/bin/env python3
"""S14 中证转债指数(000832)2022-2024 阶段性回撤复盘的基准真值(冻结快照, 网络无关).

四轮回撤的关键点位/日期/溢价率/基准对照数值, 全部来自 IRAB irab-011 gold facts
(多源一致: 中证指数公司官方数据 + 华创/兴业/华安/东吴/Wind 测算), 本计算器只做只读展开
与算术自检, 不做任何模型估算.

刻度约定:
  - 日期一律编码为 YYYYMM 整数(年*100+月), 与本容器"月份级即可算 present"的要求对齐——
    避免 YYYYMMDD 整数差跨月不等于自然日差的算术陷阱.
  - 百分比量(跌幅/溢价率/基准收益率)一律以 fraction 输出(-12.2% -> -0.122), 与提取器
    validators.py 的规范化结果同刻度. gt_recipe.yaml 因此不得声明 percent_fields.
  - 交易日数以整数天数输出, 不做刻度转换.

**范围之外**: 溢价率收缩幅度(pp)与"点位-溢价率互算"一致性不在本计算器输出范围——
它们由容器的 reconcile/consistency 检查点在评分时直接对候选作品自己的已报数值求值,
不需要独立的真值键(这两项本身就是"内部自洽性", 不是"外部事实").

用法:
  python cb_index_drawdown.py --out run/groundtruth.json
  python cb_index_drawdown.py --out run/groundtruth.json --snapshot fixtures/groundtruth_snapshot.json
"""
import argparse
import json
import math
import os
import sys

RECIPE_ID = "cb_index_drawdown"
VERSION = "1.0"

# ---------------------------------------------------------------------------
# 冻结答案键 —— 逐轮关键点位(收盘口径), 来自 IRAB irab-011 gold facts
# ---------------------------------------------------------------------------

ROUNDS = {
    "R1": {
        "peak_date": 202201, "peak_price": 437.69,
        "trough_date": 202204, "trough_price": 384.40,
        "max_drawdown_pct": -0.122,
        "benchmark_return_pct": -0.231,          # 沪深300 同期 2022-01-04 -> 04-26
        "premium_before_pct": 0.28,               # 拟合百元口径, 2022年初
        "premium_after_pct": 0.18,                # 拟合百元口径, 2022年4月底
        "recov_from_high": 119,                   # 距2/15低点约113-125个交易日(取中值), 回到约28%
        "recov_from_low": 77,                     # 距4/26低点约77个交易日, 回到约28%
    },
    "R2": {
        # W 型双腿: 峰值 -> leg1 低点 -> 反弹 -> leg2 低点(=全轮收盘口径低点)
        "peak_date": 202208, "peak_price": 430.68,
        "leg1_low_date": 202210, "leg1_low_price": 397.24,
        "rebound_date": 202211, "rebound_price": 408.81,
        "trough_date": 202212, "trough_price": 388.46,
        "max_drawdown_pct": -0.098,                # 全轮口径(峰 08-17 -> 最终谷 12-23)
        "leg1_benchmark_return_pct": -0.168,       # 沪深300 2022-08-17 -> 10-31
        "leg2_benchmark_return_pct": 0.091,        # 沪深300 2022-10-31 -> 12-23 (正股涨、转债跌)
        "premium_before_pct": 0.254,               # 拟合口径(华创), leg2 起点(11月中)
        "premium_after_pct": 0.213,                # 拟合口径(华创), leg2 终点(12月)
        "recovery_days_valuation": 93,             # 距12/23低点约64-123个交易日(取中值), 回到约25%+
    },
    "R3": {
        "peak_date": 202308, "peak_price": 414.36,
        "trough_date": 202402, "trough_price": 370.86,
        "max_drawdown_pct": -0.105,
        "benchmark_return_pct": -0.204,            # 沪深300 同期 2023-08-04 -> 2024-02-05
        "premium_after_pct": 0.183,                # 拟合口径, 2024-02-05 低点
        "recovery_days_valuation": 64,             # 18.3% -> 26.19%(华安口径), 2024-02-05 -> 05-20
        "recovery_days_point": 202,                # 点位收复414.36前高, 至2024-12-10
        # 注意: gold facts 未给出 2023-08 起点的拟合溢价率精确值, 故本轮不设
        # premium_before_pct/premium_compression 的点值真值(见 provenance.coverage_gaps),
        # 避免用未经来源支撑的数字充当真值.
    },
    "R4": {
        "peak_date": 202405, "peak_price": 404.05,
        "trough_date": 202409, "trough_price": 363.31,
        "max_drawdown_pct": -0.101,
        "benchmark_return_pct": -0.141,            # 沪深300 同期 2024-05-20 -> 09-18
        "premium_before_pct": 0.2628,               # 拟合口径, 2024-06-28
        "premium_after_pct": 0.154,                # 拟合口径, 2024-07-31
        "recovery_days_point": 31,                  # 点位收复404.05前高, 至2024-11-07
        # 注意: 估值修复在2025年1月末仍未完全回到中位(>76个交易日未完成), 是一个开放区间
        # 而非确定的完成天数, 故不设 recovery_days_valuation 点值真值 —— 该"背离"事实
        # 由检查点 R4_valuation_recovery_incomplete_noted(present) 与 D4 判官锚点承载.
    },
}

# 2024 年信用事件标志性事实(用于 set_match 检查点 R4_credit_event_facts)
CREDIT_EVENTS = ["LINGNAN_DEFAULT_20240814", "GUANGHUI_DELIST_20240828", "SOUTE_FIRSTDEFAULT_20240517"]

# ---------------------------------------------------------------------------
# 合理区间(self_check 用), 均来自 gold facts 的"全程运行区间"锚
# ---------------------------------------------------------------------------
PRICE_BAND = (350.0, 445.0)
DRAWDOWN_BAND = (-0.15, -0.07)          # 四轮收盘口径最大回撤均落在 -7% 至 -13%
PREMIUM_BAND = (0.10, 0.30)             # 拟合百元口径运行区间约 15%-28%(留边)
DATE_MIN, DATE_MAX = 202201, 202412


def _drawdown(peak, trough):
    return trough / peak - 1.0


def run_self_check(rounds):
    checks, failures = [], []

    def record(ok, desc):
        checks.append({"check": desc, "ok": bool(ok)})
        if not ok:
            failures.append(desc)

    for rid, r in rounds.items():
        # --- 结构合理性 ---
        for key in ("peak_price", "trough_price"):
            v = r.get(key)
            if v is not None:
                record(PRICE_BAND[0] <= v <= PRICE_BAND[1], f"{rid}.{key} 落在合理区间 {PRICE_BAND}")
        for key in ("peak_date", "trough_date", "leg1_low_date", "rebound_date"):
            v = r.get(key)
            if v is not None:
                record(DATE_MIN <= v <= DATE_MAX, f"{rid}.{key} 落在窗口 [{DATE_MIN},{DATE_MAX}]")

        # --- 日期时序 ---
        if rid == "R2":
            record(r["peak_date"] <= r["leg1_low_date"] <= r["rebound_date"] <= r["trough_date"],
                   f"{rid} 四个日期按 峰值<=leg1低点<=反弹<=leg2低点 时序排列")
        else:
            record(r["peak_date"] <= r["trough_date"], f"{rid} 峰值日期 <= 低点日期")

        # --- 跌幅算术自检: 若已声明 max_drawdown_pct, 必须与峰谷价格互算一致 ---
        declared_dd = r.get("max_drawdown_pct")
        if declared_dd is not None:
            computed_dd = _drawdown(r["peak_price"], r["trough_price"])
            record(abs(computed_dd - declared_dd) < 5e-4,
                   f"{rid} 声明的 max_drawdown_pct({declared_dd}) 与峰谷价格互算值({round(computed_dd, 6)}) 一致")
            record(DRAWDOWN_BAND[0] <= declared_dd <= DRAWDOWN_BAND[1],
                   f"{rid} max_drawdown_pct 落在四轮共有的合理区间 {DRAWDOWN_BAND}")

        # --- 溢价率合理区间 ---
        for key in ("premium_before_pct", "premium_after_pct"):
            v = r.get(key)
            if v is not None:
                record(PREMIUM_BAND[0] <= v <= PREMIUM_BAND[1],
                       f"{rid}.{key} 落在拟合百元口径合理区间 {PREMIUM_BAND}")

    # --- R1 两个修复口径变体的相对顺序: 从更晚的低点(4/26)出发理应耗时更短 ---
    r1 = rounds["R1"]
    record(r1["recov_from_low"] < r1["recov_from_high"],
           "R1 recov_from_low(距4/26低点) < recov_from_high(距2/15低点), 因起点更晚、终点相同")

    # --- R3 估值修复(64td) 快于点位修复(202td) —— 估值压得极低 + 权益反转时的典型模式 ---
    r3 = rounds["R3"]
    record(r3["recovery_days_valuation"] < r3["recovery_days_point"],
           "R3 估值修复天数(64) < 点位修复天数(202)")

    # --- 已知市场方向: R1/R3/R4 基准同期下跌, R2 leg1 下跌/leg2 上涨(正股涨转债跌的脱钩腿) ---
    record(rounds["R1"]["benchmark_return_pct"] < 0, "R1 基准(沪深300)同期为负收益")
    record(rounds["R2"]["leg1_benchmark_return_pct"] < 0, "R2 leg1 基准同期为负收益")
    record(rounds["R2"]["leg2_benchmark_return_pct"] > 0, "R2 leg2 基准同期为正收益(与转债走势脱钩)")
    record(rounds["R3"]["benchmark_return_pct"] < 0, "R3 基准同期为负收益")
    record(rounds["R4"]["benchmark_return_pct"] < 0, "R4 基准同期为负收益")

    record(len(CREDIT_EVENTS) == 3, "2024 信用事件标志性事实登记 3 条")

    return (len(failures) == 0), checks, failures


def load_snapshot(path):
    """可选 override: 同样的 {rounds: {...}, credit_events: [...]} 结构, 缺失的键回落到内嵌冻结值."""
    with open(path, encoding="utf-8") as f:
        snap = json.load(f)
    rounds = {rid: dict(r) for rid, r in ROUNDS.items()}
    for rid, override in (snap.get("rounds") or {}).items():
        rounds.setdefault(rid, {}).update(override)
    credit_events = snap.get("credit_events") or list(CREDIT_EVENTS)
    return rounds, credit_events


def flatten(rounds, credit_events):
    values = {}
    for rid, r in rounds.items():
        for key, v in r.items():
            values[f"{rid}_{key}"] = v
    values["R4_credit_event_facts"] = list(credit_events)
    return values


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="where to write groundtruth.json")
    ap.add_argument("--snapshot", help="(optional) override answer key JSON, same nested shape")
    a = ap.parse_args()

    if a.snapshot:
        rounds, credit_events = load_snapshot(a.snapshot)
        source = f"snapshot override: {a.snapshot}"
    else:
        rounds, credit_events = {rid: dict(r) for rid, r in ROUNDS.items()}, list(CREDIT_EVENTS)
        source = "embedded frozen answer key (from IRAB irab-011 gold facts)"

    passed, checks, failures = run_self_check(rounds)
    values = flatten(rounds, credit_events)

    out = {
        "recipe": RECIPE_ID,
        "calculator_version": VERSION,
        "values": values,
        "provenance": {
            "source": source,
            "network": False,
            "scale": "fraction for pct fields (-12.2% -> -0.122); dates as YYYYMM integers; "
                     "gt_recipe MUST NOT declare percent_fields",
            "rounds": rounds,
            "credit_events": credit_events,
            "coverage_gaps": {
                "R3_premium_before_pct": "gold facts do not state a precise 2023-08 fitted-caliber "
                                          "premium level; only the trough (18.3%, 2024-02-05) and the "
                                          "post-trough recovery target (26.19%, 2024-05-20) are sourced. "
                                          "No point GT is fabricated for the missing value.",
                "R4_recovery_days_valuation": "estimation-caliber recovery for R4 is explicitly still "
                                               "incomplete as of end-Jan 2025 (>76 trading days, open-ended) "
                                               "per gold facts, not a finite completed duration -- no point "
                                               "GT is set; the divergence itself is checkpointed via "
                                               "R4_valuation_recovery_incomplete_noted.",
            },
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
