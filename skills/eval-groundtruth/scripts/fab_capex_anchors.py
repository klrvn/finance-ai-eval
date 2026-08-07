#!/usr/bin/env python3
"""Ground-truth calculator for S13 (capex-allocation) — v1.0 Snapshot.

S13 asks the candidate to build a live-formula workbook allocating the equipment capex of a
CXMT (DRAM) + YMTC (3D NAND) capacity expansion down to individual domestic equipment vendors.
The model's *inputs* are ranges, not facts — the per-10k-WSPM equipment price spans 2.8x across
two legitimate calibers, and the process-step weights / localization rates / vendor shares are
all published as bands. So there is deliberately **no ground truth for any model output**; the
output layer is carried entirely by internal-consistency checkpoints in the container.

What IS frozen and checkable is the *anchor* layer — 13 definite facts as of 2026-06-13 that a
correct answer must retrieve:

  · 9 vendor 2025 revenues (亿元) — the denominator of the required 相对弹性 column
  · CXMT's IPO planned raise and its prospectus equipment-purchase-and-installation line (亿元)
  · NAURA's stake in KINGSEMI (fraction) — the basis for de-duplicating the company-level rollup
  · YMTC Wuhan phase-3 domestic equipment procurement share (fraction, band midpoint) — the
    freshness probe that separates the current caliber from the stale 15-30% industry average

Like S10/S11/S12 this is a network-free, self-checked snapshot-to-GT calculator: the answer key
is an embedded constant, and an optional --snapshot override lets an operator substitute an
updated key (same flat shape) without editing this script.

单位 (units, fixed by the container):
  *_revenue_2025               亿元 (CNY), 2 decimals
  cxmt_planned_raise           亿元 (CNY)
  cxmt_equip_capex_prospectus  亿元 (CNY), 2 decimals
  naura_kingsemi_stake         fraction (0.1787 == 17.87%)
  ymtc_phase3_localization     fraction (band midpoint)

Scale note: the two fraction fields are emitted on the **fraction** scale, matching the
extractor's validators.py ("17.87%" -> 0.1787). gt_recipe.yaml must therefore NOT declare
percent_fields — doing so would divide them by 100 a second time and fail both checkpoints.

Usage:
  python fab_capex_anchors.py --out run/groundtruth.json [--snapshot fixtures/groundtruth_snapshot.json]

Exit code 0 iff a self-check-passed groundtruth.json was written.
"""
import argparse
import json
import math
import os
import sys

CALCULATOR_ID = "fab_capex_anchors"
CALCULATOR_VERSION = "1.0"
DATA_CUTOFF = "2026-06-13"

# --- Frozen answer key (as of DATA_CUTOFF) --------------------------------------------------
# 国产半导体设备厂商 2025 年营业收入，亿元。用作「订单增量 ÷ 2025 营收」相对弹性的分母基数。
# 万业企业刻意缺席：其 2025 营收无可信结构化数据，正确行为是留空/标待查，故容器不设检查点。
VENDOR_REVENUE_2025 = {
    "naura":    393.53,   # 北方华创 002371
    "amec":     123.85,   # 中微公司 688012
    "acmr":      67.86,   # 盛美上海 688082
    "piotech":   65.19,   # 拓荆科技 688072
    "mattson":   50.76,   # 屹唐股份
    "hwatsing":  46.48,   # 华海清科 688120
    "jingce":    33.48,   # 精测电子 300567
    "skyverse":  20.53,   # 中科飞测 688361
    "kingsemi":  19.48,   # 芯源微 688037（已由北方华创控股，见 naura_kingsemi_stake）
}

# 长鑫科技 IPO：2026-06-12 证监会同意科创板首发注册。
# 募投三项的**拟以募集资金投入额**分列，三项相加恰等于拟募资额 295 亿。
# 项目总投资 345 亿是另一个口径（含自筹部分），必然高于募集资金投入额——这是 A 股招股书的
# 标准结构。gold 把「合计总投资 345 亿」写在三项分列之后，容易被误读为三项之和；
# 本计算器的 self_check 用「三项之和 == 拟募资额」把这个区分钉死。
CXMT_USE_OF_PROCEEDS = {
    "dram_tech_upgrade":       130.0,   # DRAM 技术升级
    "line_tech_renovation":     75.0,   # 产线技术升级改造
    "forward_looking_rnd":      90.0,   # 前瞻技术研发
}
CXMT_PLANNED_RAISE = 295.0              # 拟募资额 == 上述三项之和
CXMT_PROJECT_TOTAL_INVESTMENT = 345.0   # 募投项目总投资（含自筹），≠ 拟募资额
CXMT_EQUIP_CAPEX_PROSPECTUS = 220.66    # 招股书披露：募投项目设备购置及安装费合计

# 北方华创以两笔合计 31.35 亿元持有芯源微 3596.47 万股、占总股本 17.87%，成为控股股东。
NAURA_KINGSEMI_STAKE = 0.1787

# 长江存储武汉三期新产线国产设备采购占比「首次超过 50%」(2026-04)，显著高于国内晶圆厂
# 平均 15-30%。这是一个阈值型事实，故以带中值 + 容差编码而非点值。
YMTC_PHASE3_LOCALIZATION_MID = 0.55
# 容器 checkpoint_schema.yaml 为 ymtc_phase3_localization 声明的绝对容差。此处复制一份
# 仅为让 self_check 能守护「通过带必须把陈旧口径排除在外」这一设计前提——见 compute_self_check。
YMTC_PHASE3_LOCALIZATION_TOL = 0.15
# 陈旧口径的上界：国内晶圆厂平均 15-30%。通过带下沿必须严格高于它，否则这个检查点就
# 不再是时效性探针，而只是一道谁都能过的送分题。
STALE_LOCALIZATION_CEILING = 0.30


def build_values(vendor_rev, cxmt_raise, cxmt_equip, naura_stake, ymtc_mid):
    """Flatten the answer key into the flat {checkpoint: value} map the grader consumes."""
    values = {f"{k}_revenue_2025": float(v) for k, v in vendor_rev.items()}
    values["cxmt_planned_raise"] = float(cxmt_raise)
    values["cxmt_equip_capex_prospectus"] = float(cxmt_equip)
    values["naura_kingsemi_stake"] = float(naura_stake)
    values["ymtc_phase3_localization"] = float(ymtc_mid)
    return values


EXPECTED_KEYS = sorted(
    [f"{k}_revenue_2025" for k in VENDOR_REVENUE_2025]
    + ["cxmt_planned_raise", "cxmt_equip_capex_prospectus",
       "naura_kingsemi_stake", "ymtc_phase3_localization"]
)


def compute_self_check(values, use_of_proceeds):
    """Structural + semantic self-check. A failure halts the whole evaluation run."""
    issues = []
    n_verified = 0

    # 1. Every expected checkpoint present, numeric, finite.
    for k in EXPECTED_KEYS:
        v = values.get(k)
        if v is None:
            issues.append(f"missing value: {k}")
        elif isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            issues.append(f"non-finite or non-numeric value: {k}={v!r}")
        else:
            n_verified += 1

    # 2. Every vendor revenue strictly positive.
    for k in VENDOR_REVENUE_2025:
        v = values.get(f"{k}_revenue_2025")
        if isinstance(v, (int, float)) and not v > 0:
            issues.append(f"vendor revenue must be positive: {k}={v}")

    # 3. NAURA is the largest domestic equipment vendor by revenue — a structural fact the
    #    allocation relies on (它横跨刻蚀/薄膜/清洗/热处理/离子注入 并并表芯源微，应为受益最大一家).
    #    If an override key ever breaks this ordering, the container's D4 anchor stops making sense.
    others = [values.get(f"{k}_revenue_2025") for k in VENDOR_REVENUE_2025 if k != "naura"]
    naura = values.get("naura_revenue_2025")
    if isinstance(naura, (int, float)) and others and naura <= max(o for o in others if isinstance(o, (int, float))):
        issues.append(f"naura_revenue_2025={naura} is not the largest vendor revenue")

    # 4. The two fraction-scale fields really are fractions, not percent magnitudes.
    #    This is the guard against the percent_fields double-division trap in both directions.
    for k in ("naura_kingsemi_stake", "ymtc_phase3_localization"):
        v = values.get(k)
        if isinstance(v, (int, float)) and not (0.0 < float(v) < 1.0):
            issues.append(f"{k}={v} is not on the fraction scale (expected 0 < v < 1)")

    # 5. Use-of-proceeds arithmetic, COMPUTED rather than hardcoded: the three funded projects
    #    must sum to exactly the planned raise. This pins the caliber distinction that the gold
    #    text makes easy to misread — 295 亿 is 拟募资额 == Σ募集资金投入, while 345 亿 is
    #    项目总投资 (含自筹) and is a different quantity. A candidate that conflates the two,
    #    or a future snapshot override that breaks the identity, fails loudly here.
    total_funded = sum(float(x) for x in use_of_proceeds.values())
    raise_amt = values.get("cxmt_planned_raise")
    if isinstance(raise_amt, (int, float)) and abs(raise_amt - total_funded) > 0.01:
        issues.append(f"cxmt_planned_raise={raise_amt} != Σ use-of-proceeds {total_funded} "
                      f"(拟募资额应等于募集资金投入三项之和)")

    # 6. 项目总投资 must sit strictly above the funded amount (the gap is the self-funded part),
    #    and the prospectus equipment line must sit inside the project envelope.
    if not CXMT_PROJECT_TOTAL_INVESTMENT > total_funded:
        issues.append(f"project total investment {CXMT_PROJECT_TOTAL_INVESTMENT} should exceed "
                      f"the funded amount {total_funded}")
    equip = values.get("cxmt_equip_capex_prospectus")
    if isinstance(equip, (int, float)) and not (0 < equip < CXMT_PROJECT_TOTAL_INVESTMENT):
        issues.append(f"cxmt_equip_capex_prospectus={equip} outside the project envelope "
                      f"(0, {CXMT_PROJECT_TOTAL_INVESTMENT})")

    # 7. **Design premise guard.** The freshness probe only discriminates if its pass band
    #    excludes the stale industry-average caliber. Any future widening of the tolerance or
    #    lowering of the midpoint that lets 15-30% through must fail the self-check loudly
    #    rather than silently turn the checkpoint into a freebie.
    band_low = float(YMTC_PHASE3_LOCALIZATION_MID) - float(YMTC_PHASE3_LOCALIZATION_TOL)
    if not band_low > STALE_LOCALIZATION_CEILING:
        issues.append(
            f"ymtc_phase3_localization pass band starts at {band_low:.4f}, which does not exclude "
            f"the stale caliber ceiling {STALE_LOCALIZATION_CEILING} — the freshness probe would "
            f"lose all discriminatory power"
        )

    return {
        "passed": not issues,
        "n_expected": len(EXPECTED_KEYS),
        "n_verified": n_verified,
        "checks": issues,
        "assertions_run": [
            "all 13 checkpoint values present, numeric, finite",
            "all 9 vendor revenues strictly positive",
            "naura is the largest vendor revenue",
            "both ratio fields on the fraction scale (0,1)",
            "Σ use-of-proceeds computed == planned raise (295 亿拟募资 vs 345 亿项目总投资 的口径区分)",
            "project total investment exceeds the funded amount",
            "prospectus equipment line inside the project envelope",
            f"freshness band low ({band_low:.2f}) excludes the stale caliber ceiling "
            f"({STALE_LOCALIZATION_CEILING})",
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--snapshot", help="optional override answer key (flat {checkpoint: value}, "
                                       "or wrapped as {values: {...}})")
    a = ap.parse_args()

    source = "embedded"
    values = build_values(VENDOR_REVENUE_2025, CXMT_PLANNED_RAISE, CXMT_EQUIP_CAPEX_PROSPECTUS,
                          NAURA_KINGSEMI_STAKE, YMTC_PHASE3_LOCALIZATION_MID)
    use_of_proceeds = dict(CXMT_USE_OF_PROCEEDS)

    if a.snapshot:
        with open(a.snapshot, encoding="utf-8") as f:
            snap = json.load(f)
        override = snap.get("values", snap)
        if not isinstance(override, dict):
            raise SystemExit("fab_capex_anchors: --snapshot must hold a flat {checkpoint: value} map")
        values.update({k: v for k, v in override.items() if k in EXPECTED_KEYS})
        if isinstance(snap.get("cxmt_use_of_proceeds"), dict):
            use_of_proceeds = snap["cxmt_use_of_proceeds"]
        source = os.path.basename(a.snapshot)
        print(f"Using snapshot override: {source}", flush=True)

    # Amounts to 2 decimals; fractions to 4 (17.87% -> 0.1787 needs 4).
    for k, v in list(values.items()):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            values[k] = round(float(v), 4 if k in ("naura_kingsemi_stake", "ymtc_phase3_localization") else 2)

    sc = compute_self_check(values, use_of_proceeds)

    gt = {
        "task_id": "S13",
        "values": values,
        "recipe": CALCULATOR_ID,
        "self_check": sc,
        "provenance": {
            "calculator": f"{CALCULATOR_ID}.py v{CALCULATOR_VERSION} (snapshot)",
            "source": source,
            "data_cutoff": DATA_CUTOFF,
            "network": False,
            "scale": "amounts in 亿元 (CNY); naura_kingsemi_stake and ymtc_phase3_localization "
                     "on the fraction scale — gt_recipe must NOT declare percent_fields",
            "side_outputs": {
                "cxmt_use_of_proceeds_total": round(sum(float(x) for x in use_of_proceeds.values()), 2),
                "cxmt_use_of_proceeds": use_of_proceeds,
                "cxmt_project_total_investment": CXMT_PROJECT_TOTAL_INVESTMENT,
                "caliber_note": "295 亿 = 拟募资额 = Σ募集资金投入；345 亿 = 募投项目总投资（含自筹）。"
                                "二者不可互换，混标属 D2 第二层的口径混用缺陷。",
            },
            "not_ground_truth": (
                "模型的真正输入与全部输出均无真值：单万片设备单价（两套口径相差 2.8 倍）、"
                "各环节价值量占比、各环节国产化率、各公司国产内部份额，以及由它们算出的一切金额。"
                "这些由容器的内部一致性检查点承载，不设点值 GT——否则会惩罚「标区间、标口径、"
                "标假设」这一 gold 明确要求的正确行为。"
            ),
        },
    }

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(gt, f, ensure_ascii=False, indent=2)

    if sc["passed"]:
        print("=== S13 GT Construction Complete ===", flush=True)
        print(f"Self-check passed ({sc['n_verified']}/{sc['n_expected']} values verified). "
              f"Source: {source}", flush=True)
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
