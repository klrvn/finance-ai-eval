#!/usr/bin/env python3
"""S13 container self-test: format-drift survival + red-line capture.

Runs the REAL pipeline path (validators.py -> grade_checkpoints.py) on synthetic works, because
both of this container's biggest risks are silent:

  A. Format drift — a percentage written as the bare string "21%" with no unit_as_written slips
     through the validator unconverted, build_env skips the non-numeric value, and every cascade
     formula that references it turns NA. The report then reads "un-verifiable" rather than
     "broken", and D2's Layer-1 denominator is quietly hollowed out.
  B. Red-line capture — the three deterministic red lines (shared weight vector, lithography
     given real domestic benefit, company rollup not tying out to the step rollup) must fail
     EXACTLY their own checkpoints and nothing else. Collateral failures would make the report
     unreadable and over-penalize.

Usage:  python cascade_capture_test.py            (from anywhere; paths resolve off __file__)
Exit code 0 iff every assertion holds.
"""
import copy
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CONTAINER = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(CONTAINER))
VALIDATORS = os.path.join(ROOT, "skills", "eval-extractor", "scripts", "validators.py")
GRADER = os.path.join(ROOT, "skills", "eval-checkpoint-grader", "scripts", "grade_checkpoints.py")
CALCULATOR = os.path.join(ROOT, "skills", "eval-groundtruth", "scripts", "fab_capex_anchors.py")

# --- a well-formed answer, on the gold's own basis vectors --------------------------------------
DRAM_W = {"litho": 0.22, "etch": 0.21, "depo": 0.22, "metro": 0.12, "clean": 0.07,
          "cmp": 0.05, "implant": 0.04, "track": 0.04, "thermal": 0.03}          # Σ = 1.00
NAND_W = {"litho": 0.13, "etch": 0.35, "depo": 0.26, "metro": 0.08, "clean": 0.07,
          "cmp": 0.06, "implant": 0.02, "track": 0.02, "thermal": 0.01}          # Σ = 1.00
LOCALIZATION = {"litho": 0.01, "etch": 0.30, "depo": 0.28, "metro": 0.10, "clean": 0.50,
                "cmp": 0.35, "implant": 0.05, "track": 0.08, "thermal": 0.35}

DRAM_PRICE, NAND_PRICE, WSPM = 60.0, 40.0, 3.0
VENDOR_REV = {"naura": 393.53, "amec": 123.85, "acmr": 67.86, "piotech": 65.19,
              "mattson": 50.76, "hwatsing": 46.48, "jingce": 33.48,
              "skyverse": 20.53, "kingsemi": 19.48}


def v(value, unit=None):
    e = {"value": value, "status": "value", "evidence": "synthetic", "confidence": 1.0}
    if unit:
        e["unit_as_written"] = unit
    return e


def step_domestic(total, weights):
    return {k: round(total * w * LOCALIZATION[k], 4) for k, w in weights.items()}


def build_perfect(weight_unit=None, weight_as_string=False):
    """A correct answer. weight_unit/weight_as_string let us probe extractor format drift."""
    dram_total = DRAM_PRICE * WSPM          # 180
    nand_total = NAND_PRICE * WSPM          # 120
    dram_dom = step_domestic(dram_total, DRAM_W)
    nand_dom = step_domestic(nand_total, NAND_W)
    dram_dom_sum = round(sum(dram_dom.values()), 4)
    nand_dom_sum = round(sum(nand_dom.values()), 4)
    company_total = round(dram_dom_sum + nand_dom_sum, 4)

    def w(x):
        """Emit a weight in one of three shapes to probe the validator contract."""
        if weight_as_string:
            return v(f"{x * 100:g}%")                    # "21%" with NO unit -> the trap
        if weight_unit == "%":
            return v(x * 100, "%")                       # 21 + unit '%'      -> converted
        return v(x)                                      # 0.21               -> passthrough

    ex = {
        # --- C 组 依赖字段 ---
        "dram_unit_price": v(DRAM_PRICE), "dram_wspm": v(WSPM),
        "nand_unit_price": v(NAND_PRICE), "nand_wspm": v(WSPM),
        "dram_etch_weight": w(DRAM_W["etch"]), "dram_depo_weight": w(DRAM_W["depo"]),
        "dram_litho_weight": w(DRAM_W["litho"]),
        "nand_etch_weight": w(NAND_W["etch"]), "nand_depo_weight": w(NAND_W["depo"]),
        "nand_litho_weight": w(NAND_W["litho"]),
        "dram_litho_domestic": v(dram_dom["litho"]), "nand_litho_domestic": v(nand_dom["litho"]),
        "dram_step_domestic": v(dram_dom), "nand_step_domestic": v(nand_dom),
        "company_increments": v({"北方华创": company_total * 0.31, "中微公司": company_total * 0.19,
                                 "拓荆科技": company_total * 0.16, "盛美上海": company_total * 0.10,
                                 "华海清科": company_total * 0.08, "中科飞测": company_total * 0.05,
                                 "屹唐股份": company_total * 0.04, "精测电子": company_total * 0.03,
                                 "芯源微": company_total * 0.04}),
        # --- B 组 D2 第一层 ---
        "dram_step_weights_sum": w(sum(DRAM_W.values())),
        "nand_step_weights_sum": w(sum(NAND_W.values())),
        "dram_equipment_total": v(dram_total), "nand_equipment_total": v(nand_total),
        "dram_etch_amount": v(round(dram_total * DRAM_W["etch"], 4)),
        "nand_etch_amount": v(round(nand_total * NAND_W["etch"], 4)),
        "dram_domestic_total": v(dram_dom_sum), "nand_domestic_total": v(nand_dom_sum),
        # --- A 组 D1 第一层 ---
        "cxmt_planned_raise": v(295.0), "cxmt_equip_capex_prospectus": v(220.66),
        "naura_kingsemi_stake": v(17.87, "%"), "ymtc_phase3_localization": v(52, "%"),
        # --- D 组 交付物 ---
        "workbook_formula_layer_present": v(True), "dram_block_present": v(True),
        "nand_block_present": v(True), "dram_nand_structure_differentiated": v(True),
        "assumption_cells_count": v(4), "process_steps_covered": v(9), "companies_covered": v(9),
        "elasticity_column_present": v(True), "caliber_source_table_present": v(True),
        "sensitivity_monotonic": v([[10.0, 20.0, 30.0], [20.0, 40.0, 60.0], [30.0, 60.0, 90.0]]),
    }
    for k, rev in VENDOR_REV.items():
        ex[f"{k}_revenue_2025"] = v(rev)
    return {"work_label": "synthetic", "task_id": "S13", "extracted": ex,
            "citations": [], "tool_inventory": [], "validator_flags": []}


def build_redline():
    """Violates exactly three red lines, each isolated so nothing else should break."""
    work = build_perfect()
    ex = work["extracted"]

    # 红线5: NAND reuses DRAM's weight vector wholesale.
    for step in ("etch", "depo", "litho"):
        ex[f"nand_{step}_weight"] = v(DRAM_W[step])
    # keep nand_etch_amount consistent with the (wrong) weight so only the band check fires
    ex["nand_etch_amount"] = v(round(NAND_PRICE * WSPM * DRAM_W["etch"], 4))

    # 红线2: lithography handed a real domestic order book.
    dram_dom = dict(ex["dram_step_domestic"]["value"])
    dram_dom["litho"] = 8.2
    ex["dram_step_domestic"] = v(dram_dom)
    ex["dram_litho_domestic"] = v(8.2)
    ex["dram_domestic_total"] = v(round(sum(dram_dom.values()), 4))   # stays self-consistent

    # 红线1: company rollup inflated 12% above the step rollup.
    new_total = ex["dram_domestic_total"]["value"] + ex["nand_domestic_total"]["value"]
    ex["company_increments"] = v({k: val * new_total * 1.12 / sum(ex["company_increments"]["value"].values())
                                  for k, val in ex["company_increments"]["value"].items()})
    return work


def run(raw, schema_path, gt_path, tag, tmp):
    rawp = os.path.join(tmp, f"{tag}.raw.json")
    normp = os.path.join(tmp, f"{tag}.norm.json")
    detp = os.path.join(tmp, f"{tag}.det.json")
    json.dump(raw, open(rawp, "w", encoding="utf-8"), ensure_ascii=False)
    subprocess.run([sys.executable, VALIDATORS, "--normalized", rawp, "--schema", schema_path,
                    "--out", normp], check=True, capture_output=True)
    subprocess.run([sys.executable, GRADER, "--schema", schema_path, "--normalized", normp,
                    "--groundtruth", gt_path, "--out", detp], check=True, capture_output=True)
    return json.load(open(normp, encoding="utf-8")), json.load(open(detp, encoding="utf-8"))


def outcome(det, kind):
    return {f for f, r in det["checkpoints"].items() if r.get("result") == kind}


def main():
    tmp = tempfile.mkdtemp(prefix="s13_test_")
    schema_doc = yaml.safe_load(open(os.path.join(CONTAINER, "checkpoint_schema.yaml"), encoding="utf-8"))
    schema = schema_doc.get("fields", schema_doc)
    schema_path = os.path.join(tmp, "schema.json")
    json.dump(schema, open(schema_path, "w", encoding="utf-8"), ensure_ascii=False)

    gt_path = os.path.join(tmp, "gt.json")
    subprocess.run([sys.executable, CALCULATOR, "--out", gt_path], check=True, capture_output=True)

    meth = {f for f, m in schema.items() if m.get("methodology")}
    grnd = {f for f, m in schema.items() if m.get("grounding")}
    failures = []

    def check(cond, msg):
        print(("  OK   " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    # ---- 1. A correct answer passes every tagged checkpoint, in all lawful weight formats -----
    print("\n[1] 格式漂移探针 —— 三种写法的占比")
    for tag, kwargs, should_survive in (
            ("decimal",      dict(),                      True),
            ("pct_unit",     dict(weight_unit="%"),       True),
            ("string_no_unit", dict(weight_as_string=True), False)):
        norm, det = run(build_perfect(**kwargs), schema_path, gt_path, tag, tmp)
        got = norm["extracted"]["dram_etch_weight"]["value"]
        m_fail, m_na = outcome(det, "fail") & meth, set(det["na_checkpoints"]) & meth
        if should_survive:
            check(abs(float(got) - 0.21) < 1e-9,
                  f"{tag}: dram_etch_weight 归一到 0.21（实得 {got!r}）")
            check(not m_fail and not m_na,
                  f"{tag}: 13 个 methodology 检查点全部判定且通过（fail={sorted(m_fail)} na={sorted(m_na)}）")
        else:
            # NOT the NA failure mode one might expect: validators.py's first-number regex pulls
            # 21 out of "21%" and, with no unit to key on, returns it unconverted. The value stays
            # numeric — just 100x too large — so build_env picks it up and the cascade checkpoints
            # FAIL loudly rather than turning NA. Two-to-four-orders-of-magnitude deltas are the
            # diagnostic fingerprint.
            check(isinstance(got, (int, float)) and abs(float(got) - 21.0) < 1e-9,
                  f"{tag}: 静默放大 100 倍，成为 {got!r} 而非 0.21")
            check(not m_na,
                  f"{tag}: 不是转 NA（NA 数 {len(m_na)}）—— 这一点与直觉相反，故写进 provenance")
            check(len(m_fail) >= 6,
                  f"{tag}: {len(m_fail)} 个 methodology 检查点误判为未通过：{sorted(m_fail)}")
            big = [f for f in m_fail if (det["checkpoints"][f].get("delta") or 0) > 10]
            check(bool(big),
                  f"{tag}: 存在两位数以上的异常 Δ（{big}）—— 报告可据此一眼认出是量纲漂移而非真错误")

    # ---- 2. The clean run's Layer-1 fractions ------------------------------------------------
    print("\n[2] 干净作品的两层基线")
    _, det = run(build_perfect(), schema_path, gt_path, "clean", tmp)
    check(det["methodology_pass_fraction"] == 1.0,
          f"methodology_pass_fraction = {det['methodology_pass_fraction']}（应为满分基线）")
    check(det["grounding_pass_fraction"] == 1.0,
          f"grounding_pass_fraction = {det['grounding_pass_fraction']}（应为满分基线）")
    check(set(det["na_checkpoints"]) & (meth | grnd) == set(),
          "无任何 grounding/methodology 检查点转 NA")
    dep_na = set(det["na_checkpoints"]) - meth - grnd
    check(len(dep_na) == 12,
          f"C 组 12 个依赖字段如期判 NA（实得 {len(dep_na)}）—— 不进分母、不扣分")

    # ---- 3. Red-line capture: exactly the three intended checkpoints fail ---------------------
    print("\n[3] 红线捕获（共用占比向量 / 光刻非零受益 / 勾稽不平）")
    _, det = run(build_redline(), schema_path, gt_path, "redline", tmp)
    failed = outcome(det, "fail")
    expected = {"etch_depo_share_nand", "litho_domestic_near_zero", "company_vs_step_tie_out"}
    check(expected <= failed, f"三条红线全部命中：{sorted(expected & failed)}")
    collateral = failed - expected
    check(not collateral, f"无误伤（多余失败：{sorted(collateral) or '无'}）")
    check(not (set(det["na_checkpoints"]) & meth), "无 methodology 检查点转 NA")
    check(det["methodology_pass_fraction"] == round(10 / 13, 4),
          f"methodology_pass_fraction = {det['methodology_pass_fraction']}（13 项中 10 项通过）")

    # ---- 4. The disjoint-band premise that makes red line 5 deterministic ---------------------
    print("\n[4] 互斥双带前提（红线5 之所以能被确定性捕获）")
    d, n = schema["etch_depo_share_dram"], schema["etch_depo_share_nand"]
    d_hi = float(d["rhs"]) + d["tol"]
    n_lo = float(n["rhs"]) - n["tol"]
    check(d_hi < n_lo,
          f"DRAM 带上沿 {d_hi:.3f} < NAND 带下沿 {n_lo:.3f} —— 单一向量不可能同时落进两条带")

    # ---- 5. Brief integrity: handout == prompt_text, and neither leaks the answer key --------
    print("\n[5] 题面完整性（候选人拿到的 == 评分官拿到的；且不泄漏答案键）")
    spec = yaml.safe_load(open(os.path.join(CONTAINER, "spec.yaml"), encoding="utf-8"))
    pt = spec["prompt_text"].rstrip()
    handout = open(os.path.join(CONTAINER, "task_description.md"), encoding="utf-8").read()
    body = "\n".join(l for l in handout.splitlines() if not l.startswith("<!--")).strip()
    check(body == pt,
          "task_description.md 正文与 spec.yaml 的 prompt_text 逐字相同"
          "（否则评分官会对着一份候选人没见过的题面打分）")

    gt_vals = json.load(open(gt_path, encoding="utf-8"))["values"]
    bands = [str(schema[f].get("rhs") or schema[f].get("target")) for f in
             ("etch_depo_share_dram", "etch_depo_share_nand", "domestic_share_band")]
    leaked = [str(x) for x in list(gt_vals.values()) + bands if str(x) in pt]
    check(not leaked, f"题面未出现任何真值或判定带（泄漏项：{leaked or '无'}）")

    scoring_words = [w for w in ("红线", "扣分", "检查点", "容差", "封顶", "权重",
                                 "CF1", "CF2", "CF3", "CF4", "CF5") if w in pt]
    check(not scoring_words, f"题面未出现评分机制词汇（命中：{scoring_words or '无'}）")

    print("\n" + "=" * 62)
    if failures:
        print(f"{len(failures)} 项断言失败")
        sys.exit(1)
    print("全部断言通过")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
