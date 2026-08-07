#!/usr/bin/env python3
"""S11 point-scheme conformance test — re-runnable proof that the container reproduces
the 判分规则 in S11_GT.md exactly.

Drives the REAL engine chain (gt_dispatch -> validators.py -> grade_checkpoints.py ->
aggregate._grouped_grounding_fraction) over hand-built extractions. Kept in the container
because this point scheme has already been revised once (13 -> 12 points) and every future
revision of the answer key needs the same proof re-run.

    python taskspecs/S11-news-data-retrieval/tests/point_scheme_test.py

Exit code 0 iff every case matches. Nothing here is part of the container contract
(container_hash covers spec / checkpoint_schema / gt_recipe / judge_notes only), so adding or
editing this file cannot change the frozen hash.

Encoded rules (S11_GT.md, revision read 2026-07-28):
  题目一 3 分 + 题目二 3 分 + 题目三 6 分 = 12
  题目一第2题 : 三项全对 1 / 恰对两项 0.5 / 一项或以下 0   <- the only partial-credit point
  题目二第3题 : 全对才给分，否则 0
  题目三第4点 : 两日全对才给分（无容错、无部分分）
  题目三第2/5/6点 : 接受相邻两日；第1/3/4点 无容错
"""
import copy, json, os, subprocess, sys, tempfile

CONTAINER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(CONTAINER))          # repo / plugin root
sys.path.insert(0, os.path.join(ROOT, "skills", "eval-aggregator", "scripts"))
import yaml
import aggregate as AGG

PY = sys.executable
N_POINTS = 12


def sh(*argv):
    r = subprocess.run([PY, *argv], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print(r.stdout, r.stderr, sep="\n")
        raise SystemExit(f"FAILED: {' '.join(argv)}")
    return r


spec = yaml.safe_load(open(os.path.join(CONTAINER, "spec.yaml"), encoding="utf-8"))
schema = yaml.safe_load(open(os.path.join(CONTAINER, "checkpoint_schema.yaml"), encoding="utf-8"))["fields"]
recipe = yaml.safe_load(open(os.path.join(CONTAINER, "gt_recipe.yaml"), encoding="utf-8"))
ggw = spec["grounding_group_weights"]

WORK = tempfile.mkdtemp(prefix="s11_test_")
json.dump(schema, open(f"{WORK}/schema.json", "w", encoding="utf-8"), ensure_ascii=False)
taskspec = {"task_id": "S11", "rubric_weights": spec["rubric_weights"],
            "objective_dims": spec["objective_dims"], "citation_policy": spec["citation_policy"],
            "d1_objective_weights": spec.get("d1_objective_weights", {"citation": 0.0, "grounding": 1.0}),
            "grounding_group_weights": ggw, "checkpoint_schema": schema}
json.dump(taskspec, open(f"{WORK}/taskspec.json", "w", encoding="utf-8"), ensure_ascii=False)

# --- structural invariants ----------------------------------------------------------------------
grounding = {f for f, m in schema.items() if m.get("grounding")}
covered = {p + sf for p, sub in ggw.items() for sf in sub}
produced = set(recipe["calculator"]["produces"])
assert len(ggw) == N_POINTS, f"expected {N_POINTS} point groups, got {len(ggw)}"
assert covered == grounding, f"group coverage != grounding checkpoints: {covered ^ grounding}"
assert produced == grounding, f"gt_recipe.produces != grounding checkpoints: {produced ^ grounding}"
for prefix, sub in ggw.items():
    assert abs(sum(sub.values()) - 1.0) < 1e-9, f"group {prefix} weights do not sum to 1"
print(f"[ok] {len(ggw)} point groups, weights sum to 1 each, coverage == grounding checkpoints "
      f"({len(grounding)}) == gt_recipe.produces")

# --- ground truth, straight from the container's own recipe --------------------------------------
sh(os.path.join(ROOT, "skills/eval-groundtruth/scripts/gt_dispatch.py"),
   "--container", CONTAINER, "--out", f"{WORK}/groundtruth.json", "--root", ROOT)
gt = json.load(open(f"{WORK}/groundtruth.json", encoding="utf-8"))
assert gt["self_check"]["passed"], gt["self_check"]
assert set(gt["values"]) == grounding
print(f"[ok] gt_dispatch self_check passed, {len(gt['values'])} values == grounding checkpoints")


def f(value, unit=None):
    return {"value": value, "status": "value", "unit_as_written": unit,
            "confidence": "high", "evidence": "conformance-test"}


class MISSING:
    pass


PERFECT = {
    "Q1_1_latest_anthropic_model": f(["Opus 5"]),
    "Q1_2_kimi_k3_pricing_all3": f({"input_cache_hit": 0.30, "input_cache_miss": 3.00, "output": 15.00}),
    "Q1_2_kimi_k3_pricing_set": f(["0.30", "3.00", "15.00"]),
    "Q1_3_waic_editions": f("9"),
    "Q2_1_acquisition_completion_date": f(20260714),
    "Q2_2_revenue_commitment_3y": f("12", "亿元"),
    "Q2_3_deal_terms": f({"deal_amount_yi_cny": 4.78, "stake_pct": 60}),
    "Q3_1_epic_fury_order_date": f(20260228),
    "Q3_2_two_week_ceasefire_date": f(20260407),
    "Q3_3_strait_reopen_announced_date": f(20260417),
    "Q3_4_islamabad_talks_window": f({"talks_start": 20260411, "talks_end": 20260412}),
    "Q3_5_mou_signed_date": f(20260617),
    "Q3_6_truce_declared_over_date": f(20260707),
    "caliber_dates_with_year": f("全部日期均写明 2026 年"),
    "caliber_event_date_basis": f("以事件实际发生日为准；多来源差异处已说明采用口径"),
    "caliber_precise_official_values": f("采用官方定价页/公告原文精确值"),
    "caliber_asof_cutoff": f("数据核验时点 2026-07-27"),
    "data_source_per_question": f(3),
}


def variant(**edits):
    v = copy.deepcopy(PERFECT)
    for k, val in edits.items():
        v[k] = {"value": None, "status": "MISSING", "evidence": "conformance-test"} \
            if val is MISSING else f(val)
    return v


def run(extracted):
    json.dump({"work_label": "T", "task_id": "S11", "extracted": extracted, "validator_flags": []},
              open(f"{WORK}/raw.json", "w", encoding="utf-8"), ensure_ascii=False)
    sh(os.path.join(ROOT, "skills/eval-extractor/scripts/validators.py"),
       "--normalized", f"{WORK}/raw.json", "--schema", f"{WORK}/schema.json",
       "--out", f"{WORK}/normalized.json")
    sh(os.path.join(ROOT, "skills/eval-checkpoint-grader/scripts/grade_checkpoints.py"),
       "--schema", f"{WORK}/schema.json", "--normalized", f"{WORK}/normalized.json",
       "--groundtruth", f"{WORK}/groundtruth.json", "--out", f"{WORK}/det_results.json")
    det = json.load(open(f"{WORK}/det_results.json", encoding="utf-8"))
    return (det,
            AGG._grouped_grounding_fraction(det, schema, ggw),
            [k for k, v in det["checkpoints"].items() if v.get("result") == "fail"])


CASES = [
    ("perfect", PERFECT, 12.0),
    ("tolerant dates: the later accepted day (2/5/6)",
     variant(Q3_2_two_week_ceasefire_date=20260408, Q3_5_mou_signed_date=20260618,
             Q3_6_truce_declared_over_date=20260708), 12.0),
    ("题目一第2题: 2 of 3 prices -> 0.5",
     variant(Q1_2_kimi_k3_pricing_all3={"input_cache_hit": 0.30, "input_cache_miss": 3.00, "output": 12.00},
             Q1_2_kimi_k3_pricing_set=["0.30", "3.00", "12.00"]), 11.5),
    ("题目一第2题: 1 of 3 prices -> 0",
     variant(Q1_2_kimi_k3_pricing_all3={"input_cache_hit": 0.30, "input_cache_miss": 2.00, "output": 12.00},
             Q1_2_kimi_k3_pricing_set=["0.30", "2.00", "12.00"]), 11.0),
    ("题目二第3题: one member wrong -> 0 (全对才给分)",
     variant(Q2_3_deal_terms={"deal_amount_yi_cny": 4.78, "stake_pct": 51}), 11.0),
    ("题目三第4点: only the start day -> 0 (no partial credit)",
     variant(Q3_4_islamabad_talks_window={"talks_start": 20260411}), 11.0),
    ("题目三第4点: end day off by one -> 0",
     variant(Q3_4_islamabad_talks_window={"talks_start": 20260411, "talks_end": 20260413}), 11.0),
    ("题目三第2点: one day outside the accepted pair -> 0",
     variant(Q3_2_two_week_ceasefire_date=20260409), 11.0),
    ("题目三第5点: one day outside the accepted pair -> 0",
     variant(Q3_5_mou_signed_date=20260619), 11.0),
    ("题目三第6点: one day outside the accepted pair -> 0",
     variant(Q3_6_truce_declared_over_date=20260709), 11.0),
    ("题目三第1点 (无容错): off by one -> 0", variant(Q3_1_epic_fury_order_date=20260227), 11.0),
    ("题目三第3点 (无容错): off by one -> 0", variant(Q3_3_strait_reopen_announced_date=20260418), 11.0),
    ("题目一第1题: wrong model -> 0", variant(Q1_1_latest_anthropic_model=["Opus 4.5"]), 11.0),
    ("题目二第2题: 亿元 unit slip -> 0", variant(Q2_2_revenue_commitment_3y="1200000000"), 11.0),
]

print(f"\n{'case':52s} {'pts/12':>7s}  failed")
print("-" * 118)
bad = 0
for label, ex, want in CASES:
    det, grouped, failed = run(ex)
    got = round(grouped * N_POINTS, 6)
    ok = abs(got - want) < 1e-9
    bad += (not ok)
    print(f"{'ok ' if ok else 'XX '}{label:49s} {got:>7.4g}  {', '.join(failed) or '-'}")
    if not ok:
        print(f"      expected {want}, got {got}")
    if det["methodology_pass_fraction"] != 1.0:
        print(f"      !! methodology_pass_fraction {det['methodology_pass_fraction']} != 1.0"); bad += 1

# --- a perfect work with a clean citation lane and empty ledgers must score 100 -------------------
run(PERFECT)
json.dump({"verified_fraction": 1.0}, open(f"{WORK}/citation_audit.json", "w"))
for j in ("judge_1", "judge_2"):
    json.dump({"levels": {d: 4 for d in AGG.DIMS}, "deductions": {d: [] for d in AGG.DIMS},
               "rationale": {}, "evidence": {}}, open(f"{WORK}/{j}.json", "w", encoding="utf-8"))
AGG.score_work(WORK, taskspec, f"{WORK}/scorecard.json")
card = json.load(open(f"{WORK}/scorecard.json", encoding="utf-8"))
if card["score"] != 100.0:
    print(f"XX perfect work scored {card['score']}, expected 100"); bad += 1

print("\n" + ("ALL CASES PASS" if not bad else f"{bad} FAILURE(S)"))
sys.exit(1 if bad else 0)
