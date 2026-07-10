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
    "D6": "数据获取与计算是否经恰当的外部工具链：金融专用API > 通用结构化来源 > 野网页抓取 > 凭记忆；计算是否有执行轨迹",
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


def effective_levels(judge):
    """Deduction ledger is the source of truth: level = 4 - sum(points), floor 0.
    Falls back to a plain stated level for legacy judge files without ledgers."""
    levels = dict(judge.get("levels", {}) or {})
    for d, items in (judge.get("deductions", {}) or {}).items():
        pts = sum((it.get("points") or 0) for it in (items or []))
        levels[d] = max(0.0, 4.0 - pts)
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


def objective_grounding(det, cit, has_policy):
    gpf = det.get("grounding_pass_fraction")
    if has_policy and cit:
        vf = cit.get("verified_fraction")
        if vf is not None and gpf is not None:
            return 0.6 * vf + 0.4 * gpf
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

    if src == "objective" and d == "D1":
        od = info.get("objective_detail", {})
        parts.append(f"客观依据：grounding_objective={od.get('grounding_objective', '-')}, "
                      f"引用核验率={od.get('verified_fraction', '-')}, "
                      f"grounding检查点通过率={od.get('grounding_pass_fraction', '-')}")
        failed = od.get("failed_checkpoints")
        if failed:
            parts.append(f"扣分来源（未通过检查点）：{', '.join(failed)}")
    elif src == "objective" and d == "D2":
        od = info.get("objective_detail", {})
        parts.append(f"客观依据：方法论检查点通过率={od.get('methodology_pass_fraction') if od.get('methodology_pass_fraction') is not None else od.get('pass_fraction', '-')}")
        failed = od.get("failed_checkpoints")
        if failed:
            parts.append(f"扣分来源（未通过检查点）：{', '.join(failed)}")
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

    l1, l2 = effective_levels(j1), effective_levels(j2)
    ded1, ded2 = j1.get("deductions", {}) or {}, j2.get("deductions", {}) or {}
    r1, r2 = j1.get("rationale", {}), j2.get("rationale", {})
    e1, e2 = j1.get("evidence", {}), j2.get("evidence", {})
    grounding_obj = objective_grounding(det, cit, has_policy)
    pass_frac = det.get("pass_fraction")
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

        if d in objective:
            if d == "D1":
                frac = grounding_obj
                entry["objective_detail"] = {
                    "grounding_objective": round(grounding_obj, 4) if grounding_obj is not None else None,
                    "verified_fraction": round(vf, 4) if vf is not None else None,
                    "grounding_pass_fraction": round(gpf, 4) if gpf is not None else None,
                    "failed_checkpoints": failed_grounding,
                }
            elif d == "D2":
                # D2 is methodology: prefer methodology-tagged checkpoints, else fall back to overall.
                frac = meth_frac if meth_frac is not None else pass_frac
                entry["objective_detail"] = {
                    "methodology_pass_fraction": round(meth_frac, 4) if meth_frac is not None else None,
                    "pass_fraction": round(pass_frac, 4) if pass_frac is not None else None,
                    "basis": "methodology_pass_fraction" if meth_frac is not None else "pass_fraction",
                    "failed_checkpoints": failed_methodology if meth_frac is not None else failed_all,
                }
            frac = frac if (frac is not None) else None
            lvl = round(4 * frac) if frac is not None else None

            if lvl is None and d in l1 and d in l2:      # objective metric unavailable -> judges
                lvl = (l1[d] + l2[d]) / 2.0
                frac = lvl / 4.0
                entry["source"] = "judge_mean_fallback"
                entry["needs_review"] = abs(l1[d] - l2[d]) > 1
            else:
                entry["source"] = "objective"
        else:
            a, b = l1.get(d), l2.get(d)
            if a is None or b is None:
                entry["source"] = "judge_missing"
            else:
                lvl = (a + b) / 2.0
                frac = lvl / 4.0
                entry["source"] = "judge_mean"
                entry["needs_review"] = abs(a - b) > 1

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


def report(cards_paths, out):
    """Unified report: identical format for 1..N works.
    Sections: score & ranking table -> per-dim overview -> detailed D1-D6 grading for every
    work (deduction ledgers with evidence) -> checkpoints -> citations & CF -> verdict."""
    cards = [load(p) for p in cards_paths]
    cards.sort(key=lambda c: c.get("score", 0), reverse=True)
    lines = ["# 评测报告", ""]
    task = cards[0].get("task_id", "?")
    lines.append(f"任务：**{task}** · {len(cards)} 份作品 · 扣分制：满分起评，仅对明确标记的缺陷扣分 · "
                 f"基于同一套量规与基准真值绝对评分\n")

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
        name = DIM_NAMES[d]
        row = [f"{d} {name}"]
        for c in cards:
            info = c.get("dimensions", {}).get(d, {})
            lv = info.get("level")
            src = info.get("source", "")
            tag = "*" if info.get("needs_review") else ""
            cap = f" [封顶:{info.get('capped_by')}]" if info.get("capped_by") else ""
            lvl_str = "NA" if lv is None else f"{lv:g}{tag}"
            src_tag = {"objective": "客观", "judge_mean": "盲评", "judge_mean_fallback": "盲评(回退)",
                       "judge_missing": "缺失"}.get(src, src[:4])
            row.append(f"{lvl_str} ({src_tag}){cap}")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("| **总分 /100** | " + " | ".join(f"**{c['score']:g}**" for c in cards) + " |")
    lines.append("\n`*` = 两位评分官分歧超过一级（需复核）。`(客观)` = 由实测数值推导，`(盲评)` = 由盲评扣分账本均值。\n")

    # --- 3. Detailed grading per dimension per work (deduction ledgers) ---
    lines += ["## 各维度详细评分（D1-D6）",
              "",
              "每个维度从 4 分（满分）起评；下列每一笔扣分都对应一条有证据的缺陷记录。", ""]
    for d in DIMS:
        name = DIM_NAMES[d]
        lines.append(f"### {d} — {name}")
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
                    lines.append(f"- 客观依据：grounding_objective={od.get('grounding_objective', '-')}, "
                                  f"引用核验率={od.get('verified_fraction', '-')}, "
                                  f"grounding通过率={od.get('grounding_pass_fraction', '-')}")
                elif d == "D2":
                    basis = od.get('methodology_pass_fraction')
                    lines.append(f"- 客观依据：方法论检查点通过率="
                                  f"{basis if basis is not None else od.get('pass_fraction', '-')}")
                failed = od.get("failed_checkpoints")
                if failed:
                    lines.append(f"- 扣分来源（未通过检查点）：{', '.join(failed)}")
                elif info.get("source") == "objective":
                    lines.append("- 无扣分来源：可核验检查点全部通过")

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
