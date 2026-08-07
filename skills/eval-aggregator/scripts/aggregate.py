#!/usr/bin/env python3
"""Aggregate lane outputs into a per-work score, and build the unified report.

Deduction-based grading: every dimension starts at full marks; points come off only for
explicitly flagged, evidenced issues (failed checkpoints, judge deduction ledger entries,
CF caps). NA/unverifiable items never deduct - they leave the denominator instead.

Score mode:
  python aggregate.py --bundle run/A --taskspec run/taskspec.json --out run/A/scorecard.json
Report mode (unified format, 1..N works; --compare is a legacy alias):
  python aggregate.py --report run/A/scorecard.json [run/B/scorecard.json ...] --out run/report.md

A --bundle dir holds: det_results.json, [citation_audit.json], judge_1.json, judge_2.json, [cf_flags.json].
"""
import argparse, json, os, sys

# Engine version, stamped into every scorecard/report so an archived artifact self-identifies
# which scoring rule produced it. 3.0 = two-layer combination switched from subtractive to the
# arithmetic mean of the two layers (see combine_layers below and rubrics/constitution.md §计分).
AGGREGATOR_VERSION = "3.0"

DIMS = ["D1", "D2", "D3", "D4", "D5", "D6"]

DIM_NAMES = {
    "D1": "数据完整性与依据",
    "D2": "方法论正确性",
    "D3": "完整性",
    "D4": "分析质量与洞察",
    "D5": "可操作性与适用性",
    "D6": "外部工具链完整度",
}

DIM_DESC = {
    "D1": "引用是否存在、来源是否具名、数值是否可溯源",
    "D2": "公式、框架、惯例是否适用于该任务",
    "D3": "提示要求的所有交付物是否实际存在并已计算",
    "D4": "推理深度、驱动因素和风险识别是否正确",
    "D5": "推荐是否具体、可决策、适合所述用户",
    "D6": "计算是否经恰当的外部工具链（代码/工具）执行且有轨迹；工具链覆盖是否完整；调用失败后是否妥善处理。D6 只评工具使用行为，不评数据来源层级",
}

LEVEL_LABELS = {0: "不及格", 1: "较差", 2: "合格", 3: "良好", 4: "优秀"}


def load(path, default=None):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) \
        else (default if default is not None else {})


def lvl_label(lvl):
    """Level label; half-steps floor to the more conservative label (3.5 -> 良好)."""
    if lvl is None:
        return "NA"
    return LEVEL_LABELS.get(min(4, max(0, int(lvl))), "?")


def layer2_coefficient(l2_points):
    """第二层扣分系数 = max(0, (4 - 扣分点) / 4)，落在 [0, 1]。

    `l2_points` is the two judges' averaged deduction total, in points off 4. It is derived
    from levels already clamped to [0, 4] by effective_levels(), so the clamp here is only a
    guard against a caller passing raw ledger sums."""
    return max(0.0, min(1.0, (4.0 - l2_points) / 4.0))


def combine_layers(l1_frac, l2_points):
    """维度分数 = 第一层实测基线 与 第二层扣分系数 的算术平均。

    Averaged, not subtracted. Subtraction stacked the two penalties and zeroed a dimension far
    too easily: a work measured at L1=0.57 with 2.25 points of source-quality deductions landed
    at 0.009, indistinguishable from a work that got nothing right. Averaging is the most
    forgiving of the combination rules considered — a dimension only reaches 0 when BOTH layers
    are at 0 — which is the intent.

    Known trade-off, accepted by design: averaging halves the reach of each layer. A work whose
    Layer-1 measures 0.34 but whose ledger is clean lands at 0.67, and a work with a perfect
    Layer 1 whose sourcing collapses entirely still floors at 0.50. If either half needs more
    bite, the lever is a weighted mean, not a change of shape.

    l1_frac is None means the dimension has no GT base at all (D3/D4/D6, or an objective dim
    whose metric came back missing). Such a dimension is NOT averaged against an implicit 1.0 —
    that would floor every purely-judged dimension at 0.5 and hand a work with a 4-point D4
    ledger half the weight. It falls through to the pure deduction coefficient, bit-identical
    to the pre-3.0 engine.

    `level = 4 * fraction` continues to hold, which the CF cap in cap() relies on."""
    coef = layer2_coefficient(l2_points)
    if l1_frac is None:
        return coef
    return max(0.0, (l1_frac + coef) / 2.0)


def _two_layer_lines(info, od, bullet="- "):
    """Render the two-layer equation as three short lines.

    One idea per line: the measured base, the deduction coefficient, then the product. The
    previous single-line form packed all five numbers into one clause, which the readability
    rule in commands/eval-judge.md explicitly calls out as too dense."""
    l1f = od.get("layer1_fraction")
    l2p = od.get("layer2_deduction_points", 0) or 0
    coef = od.get("layer2_coefficient")
    if coef is None:
        coef = layer2_coefficient(l2p)
    frac_val = info.get("fraction")
    basis = "GT 检查点通过率" if od.get("scoring_mode") != "deviation" else "GT 检查点偏差分"
    return [
        f"{bullet}第一层实测基线 {l1f:g}（{basis}）",
        f"{bullet}第二层扣分系数 {coef:g}（两位评分官平均扣 {l2p:g} 点，满分 4）",
        f"{bullet}维度分数 = 两层取平均 = ({l1f:g} + {coef:g}) / 2 = "
        f"{frac_val if frac_val is not None else '-'}",
    ]


def effective_levels(judge, sink=None):
    """Deduction ledger is the source of truth: level = 4 - sum(points), clamped to [0, 4].
    Falls back to a plain stated level for legacy judge files without ledgers.

    The upper clamp guards against ledger sign bugs (a negative `points` value would otherwise
    push the level above 4 and inflate the dimension score past 100% of its weight). Deduction
    `points` are a positive magnitude by convention (see the lint `points >= 0` rule).

    `sink`, if given, is a list that collects human-readable validation warnings: individual
    negative points (which violate the positive-magnitude convention) and any case where the
    raw level fell outside [0, 4] and had to be clamped (the sign-bug signature). Clamping keeps
    the score sound; the warning surfaces the underlying ledger defect for review/lint."""
    levels = dict(judge.get("levels", {}) or {})
    for d, items in (judge.get("deductions", {}) or {}).items():
        raw_pts = [(it.get("points") or 0) for it in (items or [])]
        if sink is not None:
            for it, p in zip((items or []), raw_pts):
                if p < 0:
                    sink.append(f"{d}: 负扣分点数 {p:g}（『{it.get('issue', '?')}』）——扣分应为正数量级，已按下限处理")
        pts = sum(raw_pts)
        raw_level = 4.0 - pts
        clamped = min(4.0, max(0.0, raw_level))
        if sink is not None and raw_level > 4.0 + 1e-9:
            sink.append(f"{d}: 扣分合计为负使原始等级达 {raw_level:.2f}（>4），已钳到 4——疑似账本符号错误")
        levels[d] = clamped
    return levels


def fmt_deductions(items):
    """Compact one-line rendering of a judge's deduction ledger for a dimension."""
    if not items:
        return "无扣分项"
    return "；".join(f"−{(it.get('points') or 0):g} {it.get('issue', '?')}" for it in items)


def fmt_judge_detail(j):
    """Ledger if present; otherwise the rationale (legacy judge files have no ledger,
    so '无扣分项' would misread as full marks)."""
    if j.get("deductions"):
        return "扣分：" + fmt_deductions(j.get("deductions"))
    lvl = j.get("level")
    if lvl is not None and float(lvl) >= 4:
        return "扣分：无扣分项"
    return j.get("rationale") or "无理由"


def _grouped_grounding_fraction(det, schema, ggw):
    """Compute a weighted grounding pass fraction using grounding_group_weights config.

    ggw: {prefix: {subfield: weight}} e.g. {"Q1_": {"base_price": 0.4, "latest_price": 0.4, "answer": 0.2}}
    Each checkpoint's contribution = its weight within its group. Groups are averaged equally
    (each group's weighted pass-rate contributes 1/n_groups), so no group dominates.
    Checkpoints not covered by any group fall back to equal weight among themselves.
    Returns None if no grounding checkpoints were graded.
    """
    cps = det.get("checkpoints", {}) or {}
    grounding_fields = [f for f, m in (schema or {}).items() if isinstance(m, dict) and m.get("grounding")]
    if not grounding_fields:
        return None
    # Build the set of grounding checkpoints that were actually graded (not NA)
    graded = [f for f in grounding_fields if f in cps and cps[f].get("result") in ("pass", "fail")]
    if not graded:
        return None
    # Partition into groups per ggw config
    grouped = {}      # prefix -> {full_key: weight}
    covered = set()
    for prefix, sub in (ggw or {}).items():
        grouped[prefix] = {prefix + sf: float(w) for sf, w in (sub or {}).items()}
        covered.update(grouped[prefix].keys())
    # Ungrouped grounding checkpoints (not covered by any group)
    ungrouped = [f for f in grounding_fields if f not in covered and f in graded]

    group_rates = []  # each group's weighted pass-rate
    for prefix, kw in grouped.items():
        # only count this group's checkpoints that were actually graded
        present_graded = {f: w for f, w in kw.items() if f in graded}
        if not present_graded:
            continue  # group not represented in this work -> skip, don't count as 0
        wsum = sum(present_graded.values())
        if wsum <= 0:
            continue
        passed_w = sum(w for f, w in present_graded.items() if cps[f].get("result") == "pass")
        group_rates.append(passed_w / wsum)
    # ungrouped: equal-weight pass rate
    if ungrouped:
        passed_u = sum(1 for f in ungrouped if cps[f].get("result") == "pass")
        group_rates.append(passed_u / len(ungrouped))
    if not group_rates:
        return None
    return sum(group_rates) / len(group_rates)


def objective_grounding(det, cit, has_policy, taskspec=None, schema=None):
    """Compute D1's objective fraction.

    In deviation scoring mode (det.scoring_mode == 'deviation'), prefer the continuous
    grounding_deviation_fraction over the binary grounding_pass_fraction.

    taskspec may carry:
      - d1_objective_weights: {citation: x, grounding: y} overriding default 0.0/1.0
      - grounding_group_weights: {prefix: {subfield: weight}} to compute a weighted
        grounding pass fraction (instead of the grader's equal-weight gpf)
    """
    cw = 0.0; gw = 1.0
    if taskspec and isinstance(taskspec.get("d1_objective_weights"), dict):
        d = taskspec["d1_objective_weights"]
        try:
            cw = float(d.get("citation", 0.0)); gw = float(d.get("grounding", 1.0))
        except (TypeError, ValueError):
            cw, gw = 0.0, 1.0
    ggw = (taskspec or {}).get("grounding_group_weights") if isinstance(taskspec, dict) else None
    schema_obj = schema or (taskspec or {}).get("checkpoint_schema") or {}
    # Deviation mode: use continuous fractions if available
    if det.get("scoring_mode") == "deviation":
        gdf = det.get("grounding_deviation_fraction")
        if gdf is not None:
            # prefer weighted grouped fraction if configured, else fall back to gdf
            weighted = _grouped_grounding_fraction(det, schema_obj, ggw) if ggw else None
            gpf_eff = weighted if weighted is not None else gdf
            if has_policy and cit:
                vf = cit.get("verified_fraction")
                if vf is not None:
                    return cw * vf + gw * gpf_eff
            return gpf_eff
    # Binary mode (default)
    gpf = det.get("grounding_pass_fraction")
    # Override with weighted grouped fraction if the container configured one
    if ggw:
        weighted = _grouped_grounding_fraction(det, schema_obj, ggw)
        if weighted is not None:
            gpf = weighted
    if has_policy and cit:
        vf = cit.get("verified_fraction")
        if vf is not None and gpf is not None:
            return cw * vf + gw * gpf
        if vf is not None:
            return vf
    if gpf is not None:
        return gpf
    return det.get("pass_fraction")


def fmt_lvl(lvl):
    if lvl is None:
        return "NA"
    return f"{lvl:g}"


def build_dim_summary(d, dims, det, cit, cf_applied_list):
    """Build a human-readable summary for a single dimension."""
    info = dims[d]
    name = DIM_NAMES[d]
    lvl = info.get("level")
    src = info.get("source", "")
    weight = info.get("weight", 0)
    pts = info.get("weighted_points", 0)

    parts = []
    parts.append(f"[{d}] {name}（权重 {weight}）")

    if lvl is not None:
        parts.append(f"等级 {fmt_lvl(lvl)}（{lvl_label(lvl)}），得分 {pts:.1f}/{weight}")
    else:
        parts.append(f"等级 NA（不可验证），得分 0/{weight}")

    capped = info.get("capped_by")
    if capped:
        orig = info.get("capped_from")
        parts.append(f"⚠ 被 {capped} 封顶（原等级 {fmt_lvl(orig)}）")

    if info.get("needs_review"):
        parts.append("⚠ 两位评分官分歧超过一级，需人工复核")

    # "objective" is the legacy source tag; the engine has emitted "layer1+layer2" since the
    # two-layer model landed. Accept both so this console summary does not silently go blank.
    is_measured = src in ("objective", "layer1+layer2")

    if is_measured and d == "D1":
        od = info.get("objective_detail", {})
        if det.get("scoring_mode") == "deviation":
            parts.append(f"客观依据：偏差评分模式 grounding_deviation={od.get('grounding_objective', '-')}, "
                         f"grounding偏差分={det.get('grounding_deviation_fraction', '-')}")
        else:
            parts.append(f"客观依据：grounding_objective={od.get('grounding_objective', '-')}, "
                         f"引用核验率={od.get('verified_fraction', '-')}, "
                         f"grounding检查点通过率={od.get('grounding_pass_fraction', '-')}")
        failed = od.get("failed_checkpoints")
        if failed:
            parts.append(f"扣分来源（未通过检查点）：{', '.join(failed)}")
        if src == "layer1+layer2" and od.get("layer1_fraction") is not None:
            parts += _two_layer_lines(info, od, bullet="")
    elif is_measured and d == "D2":
        od = info.get("objective_detail", {})
        if det.get("scoring_mode") == "deviation":
            basis_val = od.get("methodology_deviation_fraction", od.get('methodology_pass_fraction'))
            parts.append(f"客观依据：偏差评分模式 methodology偏差分={basis_val if basis_val is not None else '-'}")
        else:
            basis = od.get('methodology_pass_fraction')
            parts.append(f"客观依据：方法论检查点通过率="
                         f"{basis if basis is not None else od.get('pass_fraction', '-')}")
        failed = od.get("failed_checkpoints")
        if failed:
            parts.append(f"扣分来源（未通过检查点）：{', '.join(failed)}")
        if src == "layer1+layer2" and od.get("layer1_fraction") is not None:
            parts += _two_layer_lines(info, od, bullet="")
    elif "judge" in src:
        j1 = info.get("judge_1", {})
        j2 = info.get("judge_2", {})
        if j1.get("rationale") or j2.get("rationale") or j1.get("deductions") or j2.get("deductions"):
            parts.append(f"评分官1：等级{fmt_lvl(j1.get('level'))}，{fmt_judge_detail(j1)}")
            parts.append(f"评分官2：等级{fmt_lvl(j2.get('level'))}，{fmt_judge_detail(j2)}")
        ev = j1.get("evidence") or j2.get("evidence")
        if ev:
            parts.append(f"证据引用：\"{ev}\"")

    return " | ".join(parts)


def score_work(bundle, taskspec, out):
    det = load(os.path.join(bundle, "det_results.json"))
    cit = load(os.path.join(bundle, "citation_audit.json"), {})
    j1 = load(os.path.join(bundle, "judge_1.json"), {})
    j2 = load(os.path.join(bundle, "judge_2.json"), {})
    cf = load(os.path.join(bundle, "cf_flags.json"), {"flags": []})
    weights = taskspec["rubric_weights"]
    objective = set(taskspec.get("objective_dims", []))
    has_policy = taskspec.get("citation_policy", {}).get("mode", "none") != "none"
    schema = taskspec.get("checkpoint_schema", {}) or {}

    w1, w2 = [], []
    l1 = effective_levels(j1, sink=w1)
    l2 = effective_levels(j2, sink=w2)
    ledger_warnings = [f"judge_1 {m}" for m in w1] + [f"judge_2 {m}" for m in w2]
    ded1, ded2 = j1.get("deductions", {}) or {}, j2.get("deductions", {}) or {}
    r1, r2 = j1.get("rationale", {}), j2.get("rationale", {})
    e1, e2 = j1.get("evidence", {}), j2.get("evidence", {})
    grounding_obj = objective_grounding(det, cit, has_policy, taskspec=taskspec, schema=schema)
    pass_frac = det.get("pass_fraction")
    # In deviation mode, prefer continuous fractions over binary ones
    if det.get("scoring_mode") == "deviation":
        meth_frac = det.get("methodology_deviation_fraction")
        if meth_frac is None:
            meth_frac = det.get("methodology_pass_fraction")
        if meth_frac is None:
            meth_frac = det.get("deviation_fraction")
    else:
        meth_frac = det.get("methodology_pass_fraction")
    gpf = det.get("grounding_pass_fraction")
    vf = cit.get("verified_fraction") if cit else None

    # Named deduction sources for the objective dims: which checkpoints actually failed.
    failed_all = [f for f, rec in det.get("checkpoints", {}).items() if rec.get("result") == "fail"]
    failed_grounding = [f for f in failed_all if schema.get(f, {}).get("grounding")]
    failed_methodology = [f for f in failed_all if schema.get(f, {}).get("methodology")]

    dims = {}
    for d in DIMS:
        w = weights.get(d, 0)
        lvl = None       # 0-4 display level (rounded from fraction for objective dims)
        frac = None      # continuous 0-1 score share; weighted_points = frac * weight
        entry = {
            "name": DIM_NAMES[d],
            "description": DIM_DESC[d],
            "weight": w,
            "level": None,
            "fraction": None,
            "source": "",
            "weighted_points": 0.0,
            "needs_review": False,
            "capped_by": None,
            "capped_from": None,
            "judge_1": {"level": l1.get(d), "rationale": r1.get(d), "evidence": e1.get(d),
                        "deductions": ded1.get(d) or []},
            "judge_2": {"level": l2.get(d), "rationale": r2.get(d), "evidence": e2.get(d),
                        "deductions": ded2.get(d) or []},
            "objective_detail": None,
            "summary": "",
        }

        # ---- Two-layer scoring ----------------------------------------------------------------
        # Layer 2 (扣分锚点, compulsory): the two judges' non-measurable deductions for this dim,
        # as points off 4, averaged. Derived from effective levels so both ledger and legacy
        # judge files work. Layer 1 (optional GT base): the measured fraction for objective dims,
        # None otherwise. Combine multiplicatively via combine_layers():
        #     dim_fraction = mean(L1_frac, max(0, (4 - L2_points)/4))   when a L1 base exists
        #     dim_fraction = max(0, (4 - L2_points)/4)                  when it does not
        # The three branches below therefore differ only in their `source` label and in whether a
        # Layer-1 base exists — the arithmetic is one function, not three copies.
        both_j = (d in l1) and (d in l2)
        if both_j:
            l2_points = ((4.0 - l1[d]) + (4.0 - l2[d])) / 2.0
            judge_divergence = abs(l1[d] - l2[d]) > 1
        else:
            l2_points = 0.0
            judge_divergence = False

        l1_frac = None   # Layer-1 measured base (None = no GT base for this dim)
        if d in objective:
            if d == "D1":
                l1_frac = grounding_obj
                entry["objective_detail"] = {
                    "grounding_objective": round(grounding_obj, 4) if grounding_obj is not None else None,
                    "verified_fraction": round(vf, 4) if vf is not None else None,
                    "grounding_pass_fraction": round(gpf, 4) if gpf is not None else None,
                    "grounding_deviation_fraction": round(det.get("grounding_deviation_fraction"), 4) if det.get("grounding_deviation_fraction") is not None else None,
                    "scoring_mode": det.get("scoring_mode", "binary"),
                    "failed_checkpoints": failed_grounding,
                    "layer1_fraction": round(l1_frac, 4) if l1_frac is not None else None,
                    "layer2_deduction_points": round(l2_points, 3),
                    "layer2_coefficient": round(layer2_coefficient(l2_points), 4),
                }
            elif d == "D2":
                # D2 is methodology: prefer methodology-tagged checkpoints, else fall back to overall.
                l1_frac = meth_frac if meth_frac is not None else pass_frac
                entry["objective_detail"] = {
                    "methodology_pass_fraction": round(meth_frac, 4) if meth_frac is not None else None,
                    "methodology_deviation_fraction": round(det.get("methodology_deviation_fraction"), 4) if det.get("methodology_deviation_fraction") is not None else None,
                    "pass_fraction": round(pass_frac, 4) if pass_frac is not None else None,
                    "basis": "methodology_deviation_fraction" if (det.get("scoring_mode") == "deviation" and det.get("methodology_deviation_fraction") is not None) else ("methodology_pass_fraction" if meth_frac is not None else "pass_fraction"),
                    "scoring_mode": det.get("scoring_mode", "binary"),
                    "failed_checkpoints": failed_methodology if meth_frac is not None else failed_all,
                    "layer1_fraction": round(l1_frac, 4) if l1_frac is not None else None,
                    "layer2_deduction_points": round(l2_points, 3),
                    "layer2_coefficient": round(layer2_coefficient(l2_points), 4),
                }

        if d in objective and l1_frac is not None:
            # Layer 1 (measured base) scaled by Layer 2 (non-measurable 扣分锚点).
            frac = combine_layers(l1_frac, l2_points)
            entry["source"] = "layer1+layer2"
            entry["needs_review"] = judge_divergence
        elif d in objective and both_j:
            # Objective metric unavailable -> fall back to the judges' Layer-2 ledger alone.
            frac = combine_layers(None, l2_points)
            entry["source"] = "judge_mean_fallback"
            entry["needs_review"] = judge_divergence
        elif both_j:
            # Non-objective dim: Layer 2 only (L1_frac implicitly 1.0).
            frac = combine_layers(None, l2_points)
            entry["source"] = "judge_mean"
            entry["needs_review"] = judge_divergence
        else:
            entry["source"] = "judge_missing"

        if frac is not None:
            lvl = round(4.0 * frac, 2)

        entry["level"] = lvl
        entry["fraction"] = round(frac, 4) if frac is not None else None
        if frac is not None:
            entry["weighted_points"] = round(frac * w, 2)
        dims[d] = entry

    # CF caps (confirmed flags only)
    cf_applied, cf_pending = [], []

    def cap(dim, maxlvl, rule):
        if dims[dim]["level"] is not None:
            dims[dim]["capped_from"] = dims[dim]["level"]
            dims[dim]["level"] = min(dims[dim]["level"], maxlvl)
            cur_frac = dims[dim]["fraction"] if dims[dim]["fraction"] is not None else dims[dim]["level"] / 4.0
            capped_frac = min(cur_frac, maxlvl / 4.0)
            dims[dim]["fraction"] = round(capped_frac, 4)
            dims[dim]["capped_by"] = rule
            dims[dim]["weighted_points"] = round(capped_frac * dims[dim]["weight"], 2)

    cap_total_30 = False
    for f in cf.get("flags", []):
        rule = f.get("rule")
        if rule == "CF1":
            if f.get("confirmed"):
                cap_total_30 = True
                cf_applied.append(f)
            else:
                cf_pending.append(f)
            continue
        if not f.get("confirmed"):
            continue
        if rule in ("CF2", "CF4"):
            cap("D1", 1, rule)
            cf_applied.append(f)
        elif rule == "CF3":
            cap("D2", 1, rule)
            cap("D3", 1, rule)
            cf_applied.append(f)

    total = sum(dims[d]["weighted_points"] for d in DIMS if dims[d]["level"] is not None)
    if cap_total_30:
        total = min(total, 30)

    # Denominator disclosure: dims that could not be scored (no objective metric AND no judges) silently
    # drop their weight. Report the effective denominator and a renormalized score so a partial run is not
    # mistaken for a full /100.
    scored_weight = sum(dims[d]["weight"] for d in DIMS if dims[d]["level"] is not None)
    unscored_dims = [d for d in DIMS if dims[d]["level"] is None]
    score_normalized = round(total / scored_weight * 100, 2) if scored_weight else None
    if cap_total_30 and score_normalized is not None:
        score_normalized = min(score_normalized, 30)

    # Build per-dimension summaries
    cf_rules_applied = [f["rule"] for f in cf_applied]
    for d in DIMS:
        dims[d]["summary"] = build_dim_summary(d, dims, det, cit, cf_rules_applied)

    dimension_summaries = []
    for d in DIMS:
        info = dims[d]
        dimension_summaries.append({
            "dim": d,
            "name": DIM_NAMES[d],
            "level": info["level"],
            "level_label": lvl_label(info["level"]),
            "weight": info["weight"],
            "weighted_points": info["weighted_points"],
            "source": info["source"],
            "capped_by": info["capped_by"],
            "needs_review": info["needs_review"],
            "summary": info["summary"],
        })

    # headline heuristic
    cf5 = det.get("cf5_hits", [])
    if cap_total_30:
        headline = "因确认的捏造标记（CF1）封顶为 30 分。"
    elif cf_applied:
        headline = "因 grounding/执行问题封顶：" + ", ".join(f["rule"] for f in cf_applied)
    elif cf5:
        worst = max(((k, v.get("delta", 0)) for k, v in det.get("checkpoints", {}).items()
                     if v.get("result") == "fail" and v.get("delta") is not None),
                    key=lambda kv: kv[1], default=(cf5[0], None))
        headline = f"检查点未通过：{worst[0]} 超出容差。"
    else:
        best = max((d for d in DIMS if dims[d]["level"] is not None),
                   key=lambda d: dims[d]["level"], default=None)
        headline = f"干净运行；{DIM_NAMES.get(best, best)} 表现最强。" if best else "已评分。"

    card = {
        "work_label": det.get("work_label") or os.path.basename(bundle),
        "task_id": taskspec.get("task_id") or det.get("task_id"),
        "aggregator_version": AGGREGATOR_VERSION,
        "score": round(total, 2),
        "scored_weight": scored_weight,
        "score_normalized": score_normalized,
        "unscored_dims": unscored_dims,
        "dimensions": dims,
        "dimension_summaries": dimension_summaries,
        "pass_fraction": pass_frac,
        "methodology_pass_fraction": meth_frac,
        "grounding_objective": round(grounding_obj, 4) if grounding_obj is not None else None,
        "na_checkpoints": det.get("na_checkpoints", []),
        "cf_applied": [f["rule"] for f in cf_applied],
        "cf_pending_human": cf_pending,
        "needs_review_dims": [d for d in DIMS if dims[d]["needs_review"]],
        "ledger_warnings": ledger_warnings,
        "headline": headline,
        "bundle_dir": bundle,
    }
    json.dump(card, open(out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    # Console: print dimension summaries for quick scan
    denom = f"（有效分母 {scored_weight}/100，归一化 {score_normalized}）" if scored_weight != 100 else ""
    print(f"=== {card['work_label']} | {card['task_id']} | 总分 {card['score']}/100 {denom}===")
    for s in dimension_summaries:
        print(f"  {s['summary']}")
    if unscored_dims:
        print(f"  ⚠ 未能评分的维度（已从分母剔除）：{unscored_dims}")
    if cf_pending:
        print(f"  ⚠ 待人工确认：{[f['rule'] for f in cf_pending]}")
    for m in ledger_warnings:
        print(f"  ⚠ 账本校验：{m}", file=sys.stderr)


def _load_work_excerpts(cards):
    """Load normalized.json from each card's bundle dir and build a per-work lookup of
    field evidence excerpts (the verbatim span from the student answer that the extractor
    captured for each checkpoint field).

    Returns: {work_label: {checkpoint_field: evidence_text, ...}, ...}
    Also returns: {work_label: {checkpoint_field: full_extracted_field_meta, ...}} for fallback.
    """
    excerpts = {}
    for c in cards:
        wl = c["work_label"]
        norm = load(os.path.join(c.get("bundle_dir", ""), "normalized.json"), {})
        field_ev = {}
        for field, meta in (norm.get("extracted", {}) or {}).items():
            if isinstance(meta, dict) and meta.get("evidence"):
                field_ev[field] = meta["evidence"]
        excerpts[wl] = field_ev
    return excerpts


def _dim_weight(cards, d):
    """Return the configured weight for dimension d (same across all works), or 0 if unscored."""
    for c in cards:
        w = (c.get("dimensions") or {}).get(d, {}).get("weight")
        if w is not None:
            return w
    return 0


def report(cards_paths, out):
    """Unified report: identical format for 1..N works.
    Sections: score & ranking table -> per-dim overview -> detailed D1-D6 grading for every
    work (deduction ledgers with evidence + student answer excerpts) -> checkpoints -> citations & CF -> verdict."""
    cards = [load(p) for p in cards_paths]
    cards.sort(key=lambda c: c.get("score", 0), reverse=True)
    lines = ["# 评测报告", ""]
    task = cards[0].get("task_id", "?")
    engine_v = next((c.get("aggregator_version") for c in cards if c.get("aggregator_version")), None)
    lines.append(f"任务：**{task}** · {len(cards)} 份作品 · 扣分制：满分起评，仅对明确标记的缺陷扣分 · "
                 f"基于同一套量规与基准真值绝对评分")
    lines.append("")
    lines.append(f"计分引擎：aggregator {engine_v or '未标注（3.0 之前）'}"
                 f"（维度分数 = 第一层实测基线 与 第二层扣分系数 取平均）")
    lines.append("")

    # --- 1. Score & ranking table ---
    lines += ["## 总分与排名", ""]
    show_norm = any(c.get("scored_weight", 100) != 100 for c in cards)
    hdr = "| 排名 | 作品 | 总分 /100 |" + (" 有效权重 | 归一化分 |" if show_norm else "") + " 已应用 CF | 待确认 CF | 一行结论 |"
    sep = "|---|---|---|" + ("---|---|" if show_norm else "") + "---|---|---|"
    lines += [hdr, sep]
    for i, c in enumerate(cards, 1):
        norm_cols = (f" {c.get('scored_weight', 100)} | {c.get('score_normalized', '-')} |" if show_norm else "")
        lines.append(f"| {i} | {c['work_label']} | **{c.get('score', 0):g}** |" + norm_cols +
                     f" {', '.join(c.get('cf_applied', [])) or '-'} |"
                     f" {', '.join(f['rule'] for f in c.get('cf_pending_human', [])) or '-'} |"
                     f" {c.get('headline', '')} |")
    lines.append("")

    # --- 2. Per-dimension overview ---
    hdr = "| 维度 | " + " | ".join(c["work_label"] for c in cards) + " |"
    sep = "|" + "---|" * (len(cards) + 1)
    lines += ["## 各维度评分总览", "", hdr, sep]
    for d in DIMS:
        dw = _dim_weight(cards, d)
        if dw == 0:
            continue
        name = DIM_NAMES[d]
        row = [f"{d} {name}（权重{dw}）"]
        for c in cards:
            info = c.get("dimensions", {}).get(d, {})
            lv = info.get("level")
            src = info.get("source", "")
            tag = "*" if info.get("needs_review") else ""
            cap = f" [封顶:{info.get('capped_by')}]" if info.get("capped_by") else ""
            lvl_str = "NA" if lv is None else f"{lv:g}{tag}"
            src_tag = {"objective": "客观", "layer1+layer2": "两层", "judge_mean": "盲评",
                       "judge_mean_fallback": "盲评(回退)", "judge_missing": "缺失"}.get(src, src[:4])
            row.append(f"{lvl_str} ({src_tag}){cap}")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("| **总分 /100** | " + " | ".join(f"**{c['score']:g}**" for c in cards) + " |")
    lines.append("")
    lines.append("表内标记的含义：")
    lines.append("- `*` = 两位评分官分歧超过一级，需人工复核。")
    lines.append("- `(两层)` = 第一层实测基线 与 第二层扣分系数 的**算术平均**。")
    lines.append("- `(盲评)` = 该维度无 GT 基线，分数为纯第二层扣分账本。")
    lines.append("- `(盲评(回退))` = 该维度本应有客观指标，但指标缺失，退回盲评。")
    lines.append("")

    # --- 3. Detailed grading per dimension per work (deduction ledgers + answer excerpts) ---
    work_excerpts = _load_work_excerpts(cards)
    lines += ["## 各维度详细评分",
              "",
              "每个维度从 4 分（满分）起评；下列每一笔扣分都对应一条有证据的缺陷记录。",
              "每个维度末尾附学生答案原文引用，方便快速定位答案中的对应位置。",
              "权重为 0 的维度已折叠为一行（不计入总分）。", ""]
    for d in DIMS:
        name = DIM_NAMES[d]
        dw = _dim_weight(cards, d)
        if dw == 0:
            lines.append(f"### {d} — {name}（权重 0，不计入总分）")
            lines.append("")
            lines.append("_本任务该维度权重为 0，不计分。_")
            lines.append("")
            continue
        lines.append(f"### {d} — {name}（权重{dw}）")
        lines.append(f"_{DIM_DESC[d]}_")
        lines.append("")
        for c in cards:
            info = c.get("dimensions", {}).get(d, {})
            lv = info.get("level")
            w = info.get("weight", 0)
            pts = info.get("weighted_points", 0)
            lvl_str = "NA" if lv is None else f"{lv:g}"
            lines.append(f"**{c['work_label']}**：等级 {lvl_str}（{lvl_label(lv)}），得分 {pts:.1f}/{w}")

            capped = info.get("capped_by")
            if capped:
                orig = info.get("capped_from")
                orig_str = "NA" if orig is None else f"{orig:g}"
                lines.append(f"- ⚠ 被 {capped} 封顶（原等级 {orig_str}）")

            if info.get("needs_review"):
                lines.append(f"- ⚠ 两位评分官分歧超过一级，需人工复核")

            od = info.get("objective_detail")
            if od:
                if d == "D1":
                    if od.get("scoring_mode") == "deviation":
                        lines.append(f"- 客观依据：偏差评分模式 grounding_deviation={od.get('grounding_objective', '-')}, "
                                      f"grounding偏差分={od.get('grounding_deviation_fraction', '-')}")
                    else:
                        lines.append(f"- 客观依据：grounding_objective={od.get('grounding_objective', '-')}, "
                                      f"引用核验率={od.get('verified_fraction', '-')}, "
                                      f"grounding通过率={od.get('grounding_pass_fraction', '-')}")
                elif d == "D2":
                    if od.get("scoring_mode") == "deviation":
                        basis_val = od.get('methodology_deviation_fraction', od.get('methodology_pass_fraction'))
                        lines.append(f"- 客观依据：偏差评分模式 methodology偏差分="
                                      f"{basis_val if basis_val is not None else od.get('pass_fraction', '-')}")
                    else:
                        basis = od.get('methodology_pass_fraction')
                        lines.append(f"- 客观依据：方法论检查点通过率="
                                      f"{basis if basis is not None else od.get('pass_fraction', '-')}")
                failed = od.get("failed_checkpoints")
                if failed:
                    lines.append(f"- 扣分来源（未通过检查点）：{', '.join(failed)}")
                elif info.get("source") in ("objective", "layer1+layer2"):
                    lines.append("- 无扣分来源：可核验检查点全部通过")
                if info.get("source") == "layer1+layer2" and od.get("layer1_fraction") is not None:
                    lines += _two_layer_lines(info, od)

            for jkey, jname in (("judge_1", "评分官1"), ("judge_2", "评分官2")):
                j = info.get(jkey, {})
                if j.get("level") is None and not j.get("deductions") and not j.get("rationale"):
                    continue
                jl = fmt_lvl(j.get("level"))
                rat = j.get("rationale") or ""
                lines.append(f"- {jname}（等级{jl}）{('：' + rat) if rat else ''}")
                for it in (j.get("deductions") or []):
                    ev = f"（证据：{it.get('evidence')}）" if it.get("evidence") else ""
                    lines.append(f"  - 扣 −{(it.get('points') or 0):g}"
                                 f"［{it.get('severity', '?')}］{it.get('issue', '?')}{ev}")
                if j.get("level") is not None and not (j.get("deductions")):
                    if float(j["level"]) >= 4:
                        lines.append("  - 无扣分项（保持满分）")
            ev = (info.get("judge_1", {}) or {}).get("evidence") or (info.get("judge_2", {}) or {}).get("evidence")
            if ev:
                lines.append(f"- 证据引用：\"{ev}\"")

            # --- Student answer excerpts: verbatim quotes from the work for this dimension ---
            excerpts = []
            wl = c["work_label"]
            # Objective dims: excerpts from failed checkpoint fields
            if od:
                failed = od.get("failed_checkpoints") or []
                for field in failed:
                    ex_text = work_excerpts.get(wl, {}).get(field)
                    if ex_text:
                        excerpts.append({"label": f"检查点 {field}（未通过）", "quote": ex_text})
            # Subjective dims: excerpts from judge deduction evidence
            for jkey in ("judge_1", "judge_2"):
                j = info.get(jkey, {}) or {}
                for it in (j.get("deductions") or []):
                    ev_text = it.get("evidence")
                    if ev_text:
                        issue = it.get("issue", "?")
                        # Deduplicate: same evidence text may appear in both judges
                        if not any(e["quote"] == ev_text for e in excerpts):
                            excerpts.append({"label": f"盲评扣分：{issue}", "quote": ev_text})
            if excerpts:
                lines.append("")
                lines.append(f"> **学生答案原文引用（{c['work_label']} · {d}）**")
                for ex in excerpts:
                    quote = ex["quote"]
                    lines.append(f">")
                    lines.append(f"> `{ex['label']}`：")
                    lines.append(f"> {quote}")
            lines.append("")

    # --- 4. Deterministic checkpoints ---
    lines += ["## 确定性检查点明细", ""]
    det_by = {}
    for c in cards:
        det = load(os.path.join(c.get("bundle_dir", ""), "det_results.json"), {})
        det_by[c["work_label"]] = det.get("checkpoints", {})
    fields = sorted({f for cp in det_by.values() for f in cp})
    if fields:
        lines += ["| 检查点 | " + " | ".join(det_by.keys()) + " |", "|" + "---|" * (len(det_by) + 1)]
        for f in fields:
            row = [f]
            for wl in det_by:
                r = det_by[wl].get(f, {}).get("result", "-")
                delta = det_by[wl].get(f, {}).get("delta")
                delta_str = f" (Δ={delta:g})" if delta is not None and r == "fail" else ""
                row.append({"pass": "通过", "fail": "未通过" + delta_str, "NA": "NA"}.get(r, r))
            lines.append("| " + " | ".join(row) + " |")
        lines.append("\n`NA` = 因基准真值/输入缺失无法核验——不扣分，已从分母剔除。")
    else:
        lines.append("_本任务无确定性检查点。_")
    lines.append("")

    # --- 5. Grounding & CF ---
    lines += ["## 引用核验与致命缺陷", ""]
    lines += ["| 作品 | grounding | 已应用 CF | 待确认 CF |", "|---|---|---|---|"]
    for c in cards:
        lines.append(f"| {c['work_label']} | {c.get('grounding_objective', '-')} | "
                      f"{', '.join(c.get('cf_applied', [])) or '-'} | "
                      f"{', '.join(f['rule'] for f in c.get('cf_pending_human', [])) or '-'} |")
    lines.append("")

    # --- 6. Verdict ---
    lines += ["## 结论", ""]
    top, second = cards[0], (cards[1] if len(cards) > 1 else None)
    if second is None:
        lines.append(f"**{top['work_label']}** 总分 **{top.get('score', 0):g}/100**。{top.get('headline', '')}")
        if top.get("needs_review_dims"):
            lines.append(f"\n⚠ 需人工复核的维度：{', '.join(top['needs_review_dims'])}")
    else:
        margin = top["score"] - second["score"]
        provisional = bool(margin < 3 or top.get("needs_review_dims"))
        verdict_word = "暂定排序" if provisional else "排序"
        lines.append(f"**{verdict_word}：** " + " > ".join(f"{c['work_label']} ({c['score']:g})" for c in cards))
        # If works were scored over different effective denominators (some dims unscorable), a raw-score
        # ranking is not apples-to-apples; disclose and show the renormalized ranking alongside.
        if len({c.get("scored_weight", 100) for c in cards}) > 1:
            norm_rank = sorted(cards, key=lambda c: (c.get("score_normalized") or 0), reverse=True)
            lines.append(f"\n⚠ 各作品有效分母不一致（{ {c['work_label']: c.get('scored_weight', 100) for c in cards} }）"
                         f"，原始分不可直接比较。按归一化分排序："
                         + " > ".join(f"{c['work_label']} ({c.get('score_normalized')})" for c in norm_rank))
        decider = max(DIMS, key=lambda d: abs((top["dimensions"].get(d, {}).get("level") or 0) -
                                              (second["dimensions"].get(d, {}).get("level") or 0)))
        lines.append(f"\n前两名差距 {margin:.1f} 分；最大维度差距在 {decider}（{DIM_NAMES[decider]}）。" +
                     ("因差距较小或决定性维度需复核，此排序为暂定，待人工裁定。" if provisional else
                      "差距在决定性维度上足够清晰。"))
    open(out, "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines[:15]))
    print(f"... 完整评测报告已写入 {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle")
    ap.add_argument("--taskspec")
    ap.add_argument("--report", nargs="+", help="scorecard.json paths (1..N) -> unified report")
    ap.add_argument("--compare", nargs="+", help="legacy alias for --report")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.report or a.compare:
        report(a.report or a.compare, a.out)
    else:
        if not (a.bundle and a.taskspec):
            raise SystemExit("score mode needs --bundle and --taskspec")
        score_work(a.bundle, json.load(open(a.taskspec, encoding="utf-8")), a.out)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # correct console output on zh/gbk Windows
    except Exception:
        pass
    main()
