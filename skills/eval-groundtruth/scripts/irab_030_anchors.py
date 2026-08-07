#!/usr/bin/env python3
"""S17 马来西亚2026年1-2月累计对八大贸易伙伴的贸易差额 —— 基准真值(冻结快照, 网络无关).

真外部真值, 全部来自马来西亚统计局(DOSM)《Malaysia External Trade Statistics Bulletin,
February 2026》的 Jan-Feb 2026 累计列(出口 FOB / 进口 CIF, 单位 RM 百万), 经 IRAB irab-030
gold facts 转录。贸易差额(balance)本身由出口-进口**计算**得出而非硬编码, 使 self_check
真正验证算术。全国合计覆盖马来西亚全部贸易伙伴, 不等于本题点名的8国之和(马来西亚的贸易
伙伴远不止这8个), 因此不设"8国之和==合计"的断言。

用法:
  python irab_030_anchors.py --out run/groundtruth.json
"""
import argparse
import json
import os
import sys

RECIPE_ID = "irab_030_anchors"
VERSION = "1.0"

# 冻结原始数据(RM 百万), 来自 IRAB irab-030 gold facts / DOSM 2026年2月贸易公报
TRADE = {
    "usa":       {"export": 47999, "import": 16627},
    "china":     {"export": 30348, "import": 61374},
    "singapore": {"export": 36147, "import": 25872},
    "india":     {"export": 9100,  "import": 4498},
    "vietnam":   {"export": 9545,  "import": 6389},
    "japan":     {"export": 12997, "import": 10888},
    "thailand":  {"export": 11117, "import": 9450},
    "korea":     {"export": 9094,  "import": 12688},
}
TOTAL = {"export": 277782, "import": 239111}

# gold 自身给出的差额参考值(RM百万), 用于 self_check 交叉校验计算没有算错
GOLD_BALANCE_REFERENCE = {
    "usa": 31372, "china": -31026, "singapore": 10275, "india": 4602,
    "vietnam": 3156, "japan": 2109, "thailand": 1667, "korea": -3594,
}
GOLD_TOTAL_BALANCE_REFERENCE = 38670


def compute_balances():
    return {country: data["export"] - data["import"] for country, data in TRADE.items()}


def run_self_check(balances, total_balance):
    checks, failures = [], []

    def record(ok, desc):
        checks.append({"check": desc, "ok": bool(ok)})
        if not ok:
            failures.append(desc)

    for country, bal in balances.items():
        ref = GOLD_BALANCE_REFERENCE[country]
        record(abs(bal - ref) <= 1, f"{country} 出口-进口计算值({bal}) 与 gold 参考差额({ref}) 一致(±1)")

    record(abs(total_balance - GOLD_TOTAL_BALANCE_REFERENCE) <= 1,
           f"全国合计出口-进口计算值({total_balance}) 与 gold 参考差额({GOLD_TOTAL_BALANCE_REFERENCE}) 一致(±1)")

    # 方向红线: 已知哪些是顺差、哪些是逆差(gold facts 明确标注)
    surplus = {"usa", "singapore", "india", "vietnam", "japan", "thailand"}
    deficit = {"china", "korea"}
    for c in surplus:
        record(balances[c] > 0, f"{c} 应为顺差(balance>0)")
    for c in deficit:
        record(balances[c] < 0, f"{c} 应为逆差(balance<0)")
    record(total_balance > 0, "全国合计应为顺差")

    # 全国合计覆盖全部贸易伙伴, 不等于本题8国之和 —— 断言两者确实不相等,
    # 防止未来误改成"8国之和"而静默变成一个错误但自洽的小数据集
    sum_eight = sum(balances.values())
    record(abs(sum_eight - total_balance) > 100,
           "8国差额之和 与 全国合计差额 应有明显差距(全国合计覆盖8国之外的其他贸易伙伴)")

    return (len(failures) == 0), checks, failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--snapshot")
    a = ap.parse_args()

    balances = compute_balances()
    total_balance = TOTAL["export"] - TOTAL["import"]
    passed, checks, failures = run_self_check(balances, total_balance)

    values = {}
    for country, data in TRADE.items():
        values[f"export_{country}"] = data["export"]
        values[f"import_{country}"] = data["import"]
        values[f"balance_{country}"] = balances[country]
    values["export_total"] = TOTAL["export"]
    values["import_total"] = TOTAL["import"]
    values["balance_total"] = total_balance

    out = {
        "recipe": RECIPE_ID, "calculator_version": VERSION,
        "values": values,
        "provenance": {
            "source": "IRAB irab-030 gold facts (马来西亚统计局 DOSM, Malaysia External Trade Statistics Bulletin, February 2026)",
            "network": False,
            "scale": "RM 百万 (RM million); 出口 FOB, 进口 CIF",
            "period": "2026年1-2月累计",
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
