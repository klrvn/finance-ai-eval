#!/usr/bin/env python3
"""Deterministic checkpoint grader. Compares extracted values to ground truth with tolerances.

Inputs:
  --schema        {field: {type, tol, rel, target, grounding, methodology, ...}}  (checkpoint_schema)
  --normalized    normalized.json from the extractor (extracted[field] = {value, status, ...})
  --groundtruth   groundtruth.json ({"values": {...}}) or {} if unavailable
  --out           det_results.json

Scalar types:  bp, yr, pct, ratio, number            (compared to ground truth with tolerance)
Cardinality:   count_eq, count_min, sum_to, present   (no ground truth needed)
Structural:    vector, set_match                       (compared to ground truth structures)
Internal:      reconcile, consistency, monotonic       (self-consistency of the work's own values)
Sampling:      sample_verify                           (sampled cells vs supplied reference metrics)

Structural / internal types were previously punted to NA; they are now fully evaluated so that the
core substance of tasks like S4 (per-sector attribution), S1 (valuation reconciliation + sensitivity
monotonicity), S5 (top-5 loss contributors) and S2 (NAV reproduction) is actually graded.

Multi-convention ground truth (`gt_variants`): a scalar checkpoint may declare several candidate
ground-truth keys, one per legitimate convention, plus a routing field naming which one a work used
(see resolve_gt_variants). This keeps tolerances tight for quantities that have more than one lawful
convention -- S12's CSI300 leg, where total-return and price-index are both valid ETF-leg calibers
and one tolerance covering both would lose all discriminatory power.
"""
import argparse, ast, json, operator, sys

# ---- safe arithmetic evaluator (used by reconcile / consistency) -----------------------------------
_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod}
_UNOPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_CALLS = {"abs": abs, "min": min, "max": max}


def _ev(node, env):
    if isinstance(node, ast.Expression):
        return _ev(node.body, env)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        raise ValueError(f"non-numeric constant {node.value!r}")
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_ev(node.left, env), _ev(node.right, env))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNOPS:
        return _UNOPS[type(node.op)](_ev(node.operand, env))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _CALLS:
        return _CALLS[node.func.id](*[_ev(a, env) for a in node.args])
    if isinstance(node, ast.Name):
        if node.id in env and env[node.id] is not None:
            return float(env[node.id])
        raise KeyError(node.id)
    raise ValueError(f"unsupported expression node {type(node).__name__}")


def safe_eval(expr, env):
    """Evaluate an arithmetic expression over a name->number environment. Raises on missing names."""
    return _ev(ast.parse(str(expr), mode="eval"), env)


def build_env(extracted):
    """Expose every numeric extracted value, plus __sum/__len/__mean for list/dict-of-number fields."""
    env = {}
    for field, e in extracted.items():
        if not isinstance(e, dict) or e.get("status") != "value":
            continue
        v = e.get("value")
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            env[field] = float(v)
        elif isinstance(v, dict):
            nums = [x for x in v.values() if isinstance(x, (int, float)) and not isinstance(x, bool)]
            if nums:
                env[f"{field}__sum"] = float(sum(nums)); env[f"{field}__len"] = float(len(nums))
                env[f"{field}__mean"] = float(sum(nums) / len(nums))
        elif isinstance(v, (list, tuple)):
            nums = [x for x in v if isinstance(x, (int, float)) and not isinstance(x, bool)]
            if nums:
                env[f"{field}__sum"] = float(sum(nums)); env[f"{field}__len"] = float(len(nums))
                env[f"{field}__mean"] = float(sum(nums) / len(nums))
    return env


# ---- element / structural comparators --------------------------------------------------------------
def _within(a, b, tol, rel):
    if a is None or b is None:
        return None, None
    delta = abs(a - b)
    thresh = tol * abs(b) if rel else tol
    return delta <= thresh, delta


def compare_scalar(ex, truth, tol, rel, ftype, decimals=None):
    if ex is None or truth is None:
        return None, None
    if ftype == "bp":
        delta = abs(ex - truth) * 10000.0
        return delta <= tol, delta               # tol in bp
    # --- precision harmonization (tol=0 exact-match regime) ---
    # Rule: when tol=0 (exact match required), if the student value carries more
    # decimal places than the ground truth, round the student value down to the
    # GT's decimal precision before comparing.  E.g. GT=9.30 (2dp), student=9.301
    # (3dp) -> round student to 9.30 -> PASS.  This prevents penalizing students
    # who report higher-precision data that rounds to the correct 2-dp checkpoint
    # value.  Only applies when tol==0; with tol>0 the tolerance already absorbs
    # sub-precision differences.
    #
    # Precision is taken from the checkpoint's declared `decimals` when present,
    # and only otherwise inferred from repr(truth).  Inference is unsound for GT
    # values with a trailing zero: repr(728.80) is '728.8', so the GT reads as
    # 1dp and a wrong answer of 728.84 would be rounded to 728.8 and PASS —
    # a silent ±0.05 tolerance under a declared tol=0.  Declaring `decimals: 2`
    # pins the intended precision and restores true exact matching.
    if tol == 0 and isinstance(truth, float):
        if decimals is not None:
            _gt_decimals = int(decimals)
        else:
            _gt_str = repr(truth)
            if "." in _gt_str and "e" not in _gt_str:
                _gt_decimals = len(_gt_str.split(".")[1])
            else:
                _gt_decimals = 0
        if _gt_decimals > 0 and isinstance(ex, float):
            _ex_str = repr(ex)
            if "." in _ex_str and "e" not in _ex_str:
                _ex_decimals = len(_ex_str.split(".")[1])
            else:
                _ex_decimals = 0
            if _ex_decimals > _gt_decimals:
                ex = round(ex, _gt_decimals)
    return _within(ex, truth, tol, rel)


def compare_vector(ex, truth, tol, rel):
    """ex/truth may be {key:num} or [num]. Returns (ok, max_delta, note)."""
    if isinstance(truth, dict):
        if not isinstance(ex, dict):
            return False, None, "expected keyed object {sector: value}"
        # Normalize keys (strip + upper) so trivial "tech" vs "Tech" differences don't spuriously fail.
        exn = {_key(k): v for k, v in ex.items()}
        trn = {_key(k): v for k, v in truth.items()}
        missing = [k for k in trn if k not in exn]
        if missing:
            return False, None, f"missing keys: {sorted(missing)[:5]}"
        worst = 0.0; ok = True
        for k in set(trn) | set(exn):
            good, d = _within(_num(exn.get(k)), _num(trn.get(k)), tol, rel)
            if good is None or not good:
                ok = False
            if d is not None:
                worst = max(worst, d)
        return ok, round(worst, 8), None
    if isinstance(truth, list):
        if not isinstance(ex, list) or len(ex) != len(truth):
            return False, None, f"length mismatch (got {len(ex) if isinstance(ex, list) else 'n/a'}, want {len(truth)})"
        worst = 0.0; ok = True
        for a, b in zip(ex, truth):
            good, d = _within(_num(a), _num(b), tol, rel)
            if good is None or not good:
                ok = False
            if d is not None:
                worst = max(worst, d)
        return ok, round(worst, 8), None
    return None, None, "unsupported ground-truth shape"


def compare_set(ex, truth, tol):
    """Identity match: pass if the work missed at most `tol` of the true members.

    `tol` counts *misses* (true members absent from the work), not symmetric difference, so a single
    substitution (4 of 5 correct) counts as 1, matching the intent of e.g. top-5 `tol: 1`.
    """
    if not isinstance(ex, (list, tuple, set)):
        return False, None, "expected a list of identifiers"
    se, st = set(map(_key, ex)), set(map(_key, truth))
    missing = st - se
    extra = se - st
    ok = len(missing) <= (tol or 0)
    note = None if ok else f"missed {sorted(missing)}" + (f", extra {sorted(extra)}" if extra else "")
    return ok, len(missing), note


def check_monotonic(grid, row_dir, col_dir):
    """grid[r][c]; rows ascend one axis, cols the other. dir in {asc, desc}."""
    # A malformed / non-grid value is a delivery failure (the work owed a clean 2D sensitivity table),
    # not an un-verifiable NA. Ragged rows (unequal lengths) likewise fail.
    if not isinstance(grid, list) or not grid or not all(isinstance(r, list) for r in grid):
        return False, "expected a 2D grid (list of lists)"
    if len({len(r) for r in grid}) != 1:
        return False, "ragged grid (rows have unequal lengths)"
    def mono(seq, d):
        seq = [_num(x) for x in seq]
        if any(x is None for x in seq):
            return False
        return all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1)) if d == "asc" \
            else all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))
    rows_ok = all(mono(r, col_dir) for r in grid)
    cols = list(zip(*grid))
    cols_ok = all(mono(list(c), row_dir) for c in cols)
    return (rows_ok and cols_ok), None if (rows_ok and cols_ok) else \
        f"monotonicity violated (rows_{col_dir}={rows_ok}, cols_{row_dir}={cols_ok})"


def _num(x):
    if x is None or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _key(x):
    return str(x).strip().upper()


def gt_lookup(gt, field):
    return (gt.get("values") or {}).get(field)


def resolve_gt_variants(schema, extracted, gt):
    """Decide, per routing field, which ground-truth variant a work was written against.

    A checkpoint opts in by declaring:

        gt_variants:
          field: <routing_field>            # another checkpoint holding the variant name
          map:   {<variant>: <gt_key>, ...} # candidate ground-truth keys

    This exists for quantities with more than one *legitimate* convention, where grading a work
    against the convention it did not use would be a false negative. S12's CSI300 leg is the
    motivating case: total-return and price-index are both lawful ETF-leg calibers, so a single
    tolerance wide enough for both would lose all discriminatory power. Routing lets each work be
    measured against its own caliber at a tight tolerance.

    Resolution order per routing field:
      1. ``declared``   -- the routing field was extracted as a recognized variant name.
      2. ``inferred``   -- no usable declaration: choose the variant whose ground-truth series
                           minimizes TOTAL absolute deviation across every checkpoint sharing the
                           routing field. Deliberately resolved once for the whole group rather
                           than per checkpoint: per-checkpoint inference would let a work be graded
                           against total-return in one year and price-index in the next, scoring
                           better than either convention alone.
      3. ``unresolved`` -- nothing numeric to infer from; callers mark those checkpoints NA.

    Inference decides only *what to measure against*. Whether failing to declare a convention is
    itself a defect is a rubric question, left to the blind judges' deduction anchors.

    Returns {routing_field: {"choice": <variant|None>, "basis": <str>, ...}}.
    """
    groups = {}
    for field, meta in schema.items():
        if not isinstance(meta, dict):
            continue
        gv = meta.get("gt_variants")
        if isinstance(gv, dict) and gv.get("field") and isinstance(gv.get("map"), dict) and gv["map"]:
            groups.setdefault(gv["field"], []).append((field, gv["map"]))

    resolution = {}
    for routing_field, members in groups.items():
        variants = sorted({v for _, vmap in members for v in vmap})

        entry = extracted.get(routing_field) or {}
        declared = entry.get("value") if isinstance(entry, dict) and entry.get("status") == "value" else None
        if isinstance(declared, str) and declared in variants:
            resolution[routing_field] = {"choice": declared, "basis": "declared", "variants": variants}
            continue

        # No usable declaration -> infer from the numbers, group-wide.
        totals = {v: 0.0 for v in variants}
        counts = {v: 0 for v in variants}
        for field, vmap in members:
            e = extracted.get(field) or {}
            if not isinstance(e, dict) or e.get("status") != "value":
                continue
            val = _num(e.get("value"))
            if val is None:
                continue
            for v in variants:
                truth = _num(gt_lookup(gt, vmap.get(v)))
                if truth is None:
                    continue
                totals[v] += abs(val - truth)
                counts[v] += 1

        comparable = [v for v in variants if counts[v]]
        if not comparable:
            resolution[routing_field] = {
                "choice": None, "basis": "unresolved", "variants": variants,
                "note": "no declared variant and no comparable extracted values",
                "declared_value": declared,
            }
            continue
        best = min(comparable, key=lambda v: (totals[v], v))
        resolution[routing_field] = {
            "choice": best, "basis": "inferred", "variants": variants,
            "declared_value": declared,
            "total_abs_deviation": {v: round(totals[v], 8) for v in comparable},
            "n_compared": counts[best],
        }
    return resolution


def _deviation_score(delta, tol, max_dev):
    """Continuous deviation-based score: 1.0 at-or-below tol, linear decay to 0 at max_dev.

    Returns a float in [0.0, 1.0].  If max_dev <= tol, falls back to binary (1.0 if delta<=tol else 0.0).
    """
    if delta <= tol:
        return 1.0
    if max_dev is None or max_dev <= tol:
        return 0.0
    score = 1.0 - (delta - tol) / (max_dev - tol)
    return max(0.0, min(1.0, score))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", required=True)
    ap.add_argument("--normalized", required=True)
    ap.add_argument("--groundtruth", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scoring-mode", default="binary", choices=["binary", "deviation"],
                    help="binary=pass/fail (default); deviation=continuous deviation-based scoring")
    a = ap.parse_args()
    schema = json.load(open(a.schema, encoding="utf-8"))
    norm = json.load(open(a.normalized, encoding="utf-8"))
    gt = json.load(open(a.groundtruth, encoding="utf-8")) if a.groundtruth else {}
    ex = norm.get("extracted") or norm.get("fields") or {}
    env = build_env(ex)
    # Resolve multi-convention ground-truth routing once, group-wide, before any grading.
    # No-op for schemas that never declare gt_variants.
    variant_resolution = resolve_gt_variants(schema, ex, gt)
    scoring_mode = a.scoring_mode

    checks, na, cf5 = {}, [], []
    graded = passed = g_graded = g_passed = m_graded = m_passed = 0
    # Deviation-mode accumulators: sum of per-checkpoint deviation scores (for weighted-average fraction)
    dev_sum = g_dev_sum = m_dev_sum = 0.0
    dev_graded = g_dev_graded = m_dev_graded = 0

    def record(field, ok, rec, grounding, methodology, deterministic=True, delta=None, tol=None, max_dev=None):
        nonlocal graded, passed, g_graded, g_passed, m_graded, m_passed
        nonlocal dev_sum, g_dev_sum, m_dev_sum, dev_graded, g_dev_graded, m_dev_graded
        checks[field] = rec
        if ok is None:                       # NA
            na.append(field); return
        graded += 1; passed += int(bool(ok))
        if grounding:
            g_graded += 1; g_passed += int(bool(ok))
        if methodology:
            m_graded += 1; m_passed += int(bool(ok))
        # Deviation-mode scoring: accumulate continuous score for numeric scalar checkpoints
        if scoring_mode == "deviation" and deterministic and delta is not None and tol is not None:
            dscore = _deviation_score(delta, tol, max_dev)
            rec["deviation_score"] = round(dscore, 6)
            dev_sum += dscore; dev_graded += 1
            if grounding:
                g_dev_sum += dscore; g_dev_graded += 1
            if methodology:
                m_dev_sum += dscore; m_dev_graded += 1
        if deterministic and not ok:
            rec.setdefault("cf5", True); cf5.append(field)

    for field, meta in schema.items():
        ftype = meta.get("type", "number")
        tol = float(meta.get("tol", 0) or 0)
        rel = bool(meta.get("rel", False))
        grounding = bool(meta.get("grounding", False))
        methodology = bool(meta.get("methodology", False))
        e = ex.get(field, {})
        status = e.get("status", "MISSING")
        val = e.get("value")

        # --- presence / cardinality (no ground truth) ---
        if ftype == "present":
            ok = status == "value" or bool(val)
            record(field, ok, {"result": "pass" if ok else "fail"}, grounding, methodology, deterministic=False)
            continue
        if ftype in ("count_eq", "count_min", "sum_to"):
            if status != "value" or val is None:
                record(field, False, {"result": "fail", "note": "missing"}, grounding, methodology, deterministic=False)
                continue
            target = meta.get("target")
            if ftype == "count_eq":
                ok = abs(float(val) - float(target)) < 1e-9
            elif ftype == "count_min":
                ok = float(val) >= float(target)
            else:
                ok = abs(float(val) - float(target)) <= tol
            record(field, ok, {"result": "pass" if ok else "fail", "value": val, "target": target},
                   grounding, methodology, deterministic=(ftype == "sum_to"))
            continue

        # --- structural: vector / set_match (need ground-truth structures) ---
        if ftype == "vector":
            truth = gt_lookup(gt, field)
            if truth is None:
                record(field, None, {"result": "NA", "note": "no ground-truth vector supplied"}, grounding, methodology)
                continue
            if status != "value" or val is None:
                record(field, False, {"result": "fail", "note": "missing extracted vector"}, grounding, methodology)
                continue
            ok, worst, note = compare_vector(val, truth, tol, rel)
            rec = {"result": "pass" if ok else "fail", "delta": worst, "note": note}
            record(field, ok, rec, grounding, methodology)
            continue
        if ftype == "set_match":
            truth = gt_lookup(gt, field)
            if truth is None:
                record(field, None, {"result": "NA", "note": "no ground-truth set supplied"}, grounding, methodology)
                continue
            if status != "value" or val is None:
                record(field, False, {"result": "fail", "note": "missing extracted set"}, grounding, methodology)
                continue
            ok, n_missing, note = compare_set(val, truth, tol)
            record(field, ok, {"result": "pass" if ok else "fail", "missing_count": n_missing, "note": note},
                   grounding, methodology)
            continue

        # --- internal consistency: reconcile / consistency / monotonic (self-checks, no GT) ---
        if ftype == "reconcile":
            if "formula" not in meta:
                record(field, None, {"result": "NA", "note": "reconcile spec missing 'formula'"}, grounding, methodology)
                continue
            if status != "value" or val is None:
                record(field, False, {"result": "fail", "note": "missing claimed value"}, grounding, methodology)
                continue
            try:
                computed = safe_eval(meta["formula"], env)
            except (KeyError, ZeroDivisionError, ValueError) as err:
                record(field, None, {"result": "NA", "note": f"cannot evaluate formula: {err}"}, grounding, methodology)
                continue
            ok, delta = _within(float(val), computed, tol, rel)
            record(field, ok, {"result": "pass" if ok else "fail", "delta": round(delta, 8),
                               "claimed": float(val), "computed": round(computed, 8)}, grounding, methodology)
            continue
        if ftype == "consistency":
            if "lhs" not in meta or "rhs" not in meta:
                record(field, None, {"result": "NA",
                       "note": "consistency needs 'lhs'/'rhs' expressions (or a dedicated re-execution validator, e.g. S2 NAV reproduction)"},
                       grounding, methodology)
                continue
            try:
                lhs = safe_eval(meta["lhs"], env); rhs = safe_eval(meta["rhs"], env)
            except (KeyError, ZeroDivisionError, ValueError) as err:
                record(field, None, {"result": "NA", "note": f"cannot evaluate: {err}"}, grounding, methodology)
                continue
            ok, delta = _within(lhs, rhs, tol, rel)
            record(field, ok, {"result": "pass" if ok else "fail", "delta": round(delta, 8),
                               "lhs": round(lhs, 8), "rhs": round(rhs, 8)}, grounding, methodology)
            continue
        if ftype == "monotonic":
            if status != "value" or val is None:
                record(field, False, {"result": "fail", "note": "missing sensitivity grid"}, grounding, methodology)
                continue
            ok, note = check_monotonic(val, meta.get("row_dir", "desc"), meta.get("col_dir", "asc"))
            record(field, ok, {"result": "pass" if ok else "fail", "note": note}, grounding, methodology)
            continue
        if ftype == "sample_verify":
            truth = gt_lookup(gt, field) or (gt.get("values") or {}).get("funds")
            cells = val if isinstance(val, list) else None
            if truth is None or cells is None:
                record(field, None, {"result": "NA", "note": "no reference metrics / no sampled cells"},
                       grounding, methodology)
                continue
            bad = []
            for c in cells:                                   # each: {fund, metric, value}
                ref = ((truth.get(c.get("fund")) or {}).get(c.get("metric"))) if isinstance(truth, dict) else None
                good, d = _within(_num(c.get("value")), _num(ref), tol, rel)
                if good is None or not good:
                    bad.append({"fund": c.get("fund"), "metric": c.get("metric"), "delta": d})
            ok = len(bad) == 0
            record(field, ok, {"result": "pass" if ok else "fail", "mismatches": bad[:10],
                               "n_cells": len(cells)}, grounding, methodology)
            continue

        # --- scalar numeric vs ground truth ---
        # gt_variants: this checkpoint may have several candidate GT keys, one per legitimate
        # convention. Pick the one matching the convention this work was written against.
        gt_key, variant_note = field, None
        gv = meta.get("gt_variants")
        if isinstance(gv, dict) and isinstance(gv.get("map"), dict) and gv["map"]:
            res = variant_resolution.get(gv.get("field")) or {}
            choice = res.get("choice")
            if not choice:
                record(field, None, {"result": "NA",
                                     "note": f"gt_variants unresolved via {gv.get('field')!r}: "
                                             f"{res.get('note', 'no variant determined')}"},
                       grounding, methodology)
                continue
            gt_key = gv["map"].get(choice)
            variant_note = {"variant": choice, "basis": res.get("basis"), "gt_key": gt_key,
                            "routed_by": gv.get("field")}
            if gt_key is None:
                record(field, None, {"result": "NA",
                                     "note": f"gt_variants map has no entry for variant {choice!r}",
                                     "gt_variant": variant_note},
                       grounding, methodology)
                continue
        truth = gt_lookup(gt, gt_key)
        if truth is None:
            record(field, None, {"result": "NA", "note": "no ground truth",
                                 **({"gt_variant": variant_note} if variant_note else {})},
                   grounding, methodology)
            continue
        if status != "value" or val is None:
            record(field, False, {"result": "fail", "note": "missing extracted value",
                                  **({"gt_variant": variant_note} if variant_note else {})},
                   grounding, methodology)
            continue
        ok, delta = compare_scalar(float(val), float(truth), tol, rel, ftype, meta.get("decimals"))
        max_dev = None
        ds_cfg = meta.get("deviation_scoring") if isinstance(meta, dict) else None
        if ds_cfg and isinstance(ds_cfg, dict):
            max_dev = ds_cfg.get("max_dev")
        record(field, ok, {"result": "pass" if ok else "fail",
                           "delta": round(delta, 8) if delta is not None else None,
                           "unit": "bp" if ftype == "bp" else "",
                           "extracted": val, "ground_truth": truth,
                           **({"gt_variant": variant_note} if variant_note else {})},
               grounding, methodology,
               delta=delta, tol=tol, max_dev=max_dev)

    out = {
        "work_label": norm.get("work_label"),
        "task_id": norm.get("task_id"),
        "scoring_mode": scoring_mode,
        "checkpoints": checks,
        "pass_fraction": round(passed / graded, 4) if graded else None,
        "grounding_pass_fraction": round(g_passed / g_graded, 4) if g_graded else None,
        "methodology_pass_fraction": round(m_passed / m_graded, 4) if m_graded else None,
        "na_checkpoints": na,
        "cf5_hits": cf5,
    }
    # Audit trail: which convention each routed group was graded against, and how that was decided.
    if variant_resolution:
        out["gt_variant_resolution"] = variant_resolution
    # In deviation mode, also output continuous deviation-based fractions
    if scoring_mode == "deviation":
        out["deviation_fraction"] = round(dev_sum / dev_graded, 4) if dev_graded else None
        out["grounding_deviation_fraction"] = round(g_dev_sum / g_dev_graded, 4) if g_dev_graded else None
        out["methodology_deviation_fraction"] = round(m_dev_sum / m_dev_graded, 4) if m_dev_graded else None
    json.dump(out, open(a.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print_keys = ("pass_fraction", "grounding_pass_fraction",
                  "methodology_pass_fraction", "na_checkpoints", "cf5_hits")
    if scoring_mode == "deviation":
        print_keys += ("deviation_fraction", "grounding_deviation_fraction",
                        "methodology_deviation_fraction")
    print(json.dumps({k: out[k] for k in print_keys if k in out},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # correct console output on zh/gbk Windows
    except Exception:
        pass
    main()
