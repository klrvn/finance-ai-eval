#!/usr/bin/env python3
"""Task-spec container linter.

Validates every frozen container in taskspecs/registry.json against the constitution's lawful
surface (rubrics/constitution.md §1) and the tier-coherence rules (README §tier). This is the
governance gate that lets the engines stay task-agnostic: if a container is well-formed here,
the engines can consume it mechanically.

Usage:
  python spec_lint.py --root <repo-root>            # lint all registered containers
  python spec_lint.py --root <repo-root> --stamp    # also write container_hash into provenance.json

Exit code 0 iff no ERRORs (WARNs are allowed).
"""
import argparse, hashlib, json, os, re, sys

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr); raise

TYPE_REGISTRY = {
    "number", "pct", "bp", "yr", "ratio",
    "count_eq", "count_min", "sum_to", "present",
    "vector", "set_match",
    "reconcile", "consistency", "monotonic",
    "sample_verify",
}
NUMERIC_SCALAR = {"number", "pct", "bp", "yr", "ratio"}
INTERNAL = {"reconcile", "consistency", "monotonic"}
NEEDS_GT = NUMERIC_SCALAR | {"vector", "set_match", "sample_verify"}   # need an external truth path
DIMS = ["D1", "D2", "D3", "D4", "D5", "D6"]
CF2_BAND = (0.15, 0.40)
# judge_notes must not leak quasi-ground-truth into the blind payload. We flag answer-key *assertions*
# (a forbidden term adjacent to an actual value/number), NOT the bare word - otherwise a sentence that
# merely names these terms to prohibit them would false-positive.
JUDGE_LEAK_PATTERNS = [
    re.compile(r"(正确答案|标准答案|期望值|期望数值|真值|answer\s*key)\s*[:：=＝是为]?\s*-?\d"),
    re.compile(r"应\s*(等于|为)\s*-?\d"),
]


def loady(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def loadj(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Report:
    def __init__(self, tid):
        self.tid = tid; self.errors = []; self.warns = []
    def err(self, m): self.errors.append(m)
    def warn(self, m): self.warns.append(m)


def schema_fields(schema):
    """checkpoint_schema.yaml may nest under 'fields:'; conditional_na is exempt from GT-path rules."""
    if isinstance(schema, dict) and "fields" in schema:
        return schema["fields"] or {}
    return schema or {}


def lint_container(root, entry, r):
    path = os.path.join(root, entry["path"])
    if not os.path.isdir(path):
        r.err(f"container dir missing: {entry['path']}"); return None
    # --- load files ---
    try:
        spec = loady(os.path.join(path, "spec.yaml"))
        schema_doc = loady(os.path.join(path, "checkpoint_schema.yaml"))
        gt = loady(os.path.join(path, "gt_recipe.yaml")) if os.path.exists(os.path.join(path, "gt_recipe.yaml")) else None
        prov = loadj(os.path.join(path, "provenance.json"))
    except Exception as e:
        r.err(f"failed to load container files: {e}"); return None
    fields = schema_fields(schema_doc)

    # --- registry <-> spec agreement ---
    for k in ("task_id", "tier", "version"):
        if str(spec.get(k)) != str(entry.get(k)):
            r.err(f"registry/{k}={entry.get(k)!r} != spec.{k}={spec.get(k)!r}")
    if str(prov.get("origin")) not in ("legacy-v1",):
        r.err(f"provenance.origin must be legacy-v1, got {prov.get('origin')!r}")

    # --- weights ---
    w = spec.get("rubric_weights", {}) or {}
    if set(w) != set(DIMS):
        r.err(f"rubric_weights must cover exactly D1-D6, got {sorted(w)}")
    if not all(isinstance(v, int) for v in w.values()):
        r.err(f"rubric_weights must be integers, got {w}")
    if sum(w.values()) != 100:
        r.err(f"rubric_weights sum {sum(w.values())} != 100")
    if w.get("D6", 0) < 5:
        r.err(f"D6 weight {w.get('D6')} < 5 (toolchain is always assessable)")

    # --- objective_dims ---
    obj = spec.get("objective_dims", []) or []
    if not set(obj).issubset({"D1", "D2"}):
        r.err(f"objective_dims must be subset of {{D1,D2}}, got {obj}")
    # D1 objective needs a grounding basis (grounding checkpoint or citation policy)
    has_grounding_cp = any(m.get("grounding") for m in fields.values() if isinstance(m, dict))
    cite_mode = (spec.get("citation_policy") or {}).get("mode", "none")
    if "D1" in obj and not has_grounding_cp and cite_mode == "none":
        r.warn("D1 is objective but no grounding checkpoint and citation_policy=none -> D1 has no objective basis")
    has_meth_cp = any(m.get("methodology") for m in fields.values() if isinstance(m, dict))
    if "D2" in obj and not has_meth_cp:
        r.warn("D2 is objective but no methodology-tagged checkpoint -> falls back to overall pass_fraction")

    # --- checkpoint types ---
    for fname, meta in fields.items():
        if not isinstance(meta, dict):
            r.err(f"checkpoint {fname}: malformed (expected mapping)"); continue
        t = meta.get("type")
        if t not in TYPE_REGISTRY:
            r.err(f"checkpoint {fname}: type {t!r} not in type registry")

    # --- ground-truth path: numeric/structural checkpoints must have a truth source ---
    gt_kind = (gt or {}).get("kind") or spec.get("engine_requirements", {}).get("gt_kind")
    provides_values = gt_kind in ("calculator", "user_snapshot")
    for fname, meta in fields.items():
        if isinstance(meta, dict) and meta.get("type") in NEEDS_GT and not provides_values:
            r.err(f"checkpoint {fname} (type {meta['type']}) needs a ground-truth path but gt kind is {gt_kind!r} "
                  f"(fake determinism)")

    # --- calculator binding resolves ---
    if gt_kind == "calculator":
        cal = (gt or {}).get("calculator", {})
        sidecar = cal.get("sidecar")
        if not sidecar:
            r.warn("calculator recipe without a sidecar reference")
        elif not os.path.exists(os.path.join(root, sidecar)):
            r.err(f"calculator sidecar not found: {sidecar}")
        script = cal.get("script")
        if script and not os.path.exists(os.path.join(root, script)):
            r.err(f"calculator script not found: {script}")

    # --- gt_variants: multi-convention ground-truth routing ---
    # A checkpoint whose quantity has several *legitimate* conventions may route to one GT key per
    # convention (see resolve_gt_variants in grade_checkpoints.py). Misconfiguration here is silent
    # at runtime -- a typo'd GT key just yields NA, which reads as "un-verifiable" rather than
    # "broken container" -- so validate the wiring statically.
    produced = set((gt or {}).get("calculator", {}).get("produces") or [])
    sidecar_outputs = set()
    if gt_kind == "calculator":
        sc = (gt or {}).get("calculator", {}).get("sidecar")
        if sc and os.path.exists(os.path.join(root, sc)):
            try:
                sidecar_outputs = set(loady(os.path.join(root, sc)).get("outputs") or [])
            except Exception as e:
                r.warn(f"could not read calculator sidecar outputs: {e}")
    known_gt_keys = produced | sidecar_outputs

    for fname, meta in fields.items():
        if not isinstance(meta, dict) or "gt_variants" not in meta:
            continue
        gv = meta["gt_variants"]
        if not isinstance(gv, dict):
            r.err(f"checkpoint {fname}: gt_variants must be a mapping, got {type(gv).__name__}"); continue
        if meta.get("type") not in NUMERIC_SCALAR:
            r.err(f"checkpoint {fname}: gt_variants is only supported on numeric scalar types "
                  f"{sorted(NUMERIC_SCALAR)}, got {meta.get('type')!r}")
        routing = gv.get("field")
        if not routing:
            r.err(f"checkpoint {fname}: gt_variants missing 'field' (the routing checkpoint)")
        elif routing not in fields:
            r.err(f"checkpoint {fname}: gt_variants.field {routing!r} is not a checkpoint in this schema")
        elif fields[routing].get("grounding") or fields[routing].get("methodology"):
            # The routing field decides *what* to measure against; scoring it too would make one
            # disclosure gap cost twice (once here, once via the judge anchor for the same gap).
            r.warn(f"checkpoint {fname}: gt_variants routing field {routing!r} is itself tagged "
                   f"grounding/methodology -- the convention choice would be scored twice")
        vmap = gv.get("map")
        if not isinstance(vmap, dict) or not vmap:
            r.err(f"checkpoint {fname}: gt_variants.map must be a non-empty {{variant: gt_key}} mapping")
            continue
        if len(vmap) < 2:
            r.warn(f"checkpoint {fname}: gt_variants.map has a single variant -- routing is pointless")
        for variant, gt_key in vmap.items():
            if not isinstance(gt_key, str) or not gt_key:
                r.err(f"checkpoint {fname}: gt_variants.map[{variant!r}] must be a ground-truth key string")
            elif known_gt_keys and gt_key not in known_gt_keys:
                r.err(f"checkpoint {fname}: gt_variants.map[{variant!r}] -> {gt_key!r} is not produced "
                      f"by the calculator (would silently grade as NA)")

    # --- cf thresholds within constitution bands ---
    thr = spec.get("cf_thresholds", {}) or {}
    if "CF2_uncited_ratio" in thr and not (CF2_BAND[0] <= thr["CF2_uncited_ratio"] <= CF2_BAND[1]):
        r.err(f"CF2_uncited_ratio {thr['CF2_uncited_ratio']} outside band {CF2_BAND}")

    # --- CF2 needs an active citation policy (it reads uncited_claim_ratio from the audit) ---
    if "CF2" in (spec.get("cf_rules") or []) and cite_mode == "none":
        r.err("cf_rules includes CF2 but citation_policy.mode == none -> CF2 can never fire "
              "(no citation audit is produced)")

    # --- engine_requirements type coverage ---
    er = spec.get("engine_requirements", {}) or {}
    declared = set(er.get("checkpoint_types", []) or [])
    used = {m.get("type") for m in fields.values() if isinstance(m, dict)}
    missing_decl = used - declared
    if missing_decl:
        r.warn(f"engine_requirements.checkpoint_types missing used types {sorted(missing_decl)}")

    # --- d1_objective_weights (optional override of default 0.0/1.0) ---
    d1w = spec.get("d1_objective_weights")
    if d1w is not None:
        if not isinstance(d1w, dict) or set(d1w.keys()) != {"citation", "grounding"}:
            r.err(f"d1_objective_weights must have keys {{citation, grounding}}, got {list(d1w.keys()) if isinstance(d1w, dict) else d1w}")
        else:
            try:
                c = float(d1w["citation"]); g = float(d1w["grounding"])
            except (TypeError, ValueError):
                r.err(f"d1_objective_weights values must be numeric, got {d1w}")
            else:
                if not (0.0 <= c <= 1.0 and 0.0 <= g <= 1.0):
                    r.err(f"d1_objective_weights values must be in [0,1], got {d1w}")
                if abs((c + g) - 1.0) > 1e-6:
                    r.err(f"d1_objective_weights citation+grounding must sum to 1, got {c+g}")

    # --- grounding_group_weights (optional per-prefix weighting of grounding checkpoints) ---
    ggw = spec.get("grounding_group_weights")
    if ggw is not None:
        if not isinstance(ggw, dict) or not ggw:
            r.err(f"grounding_group_weights must be a non-empty mapping, got {ggw}")
        else:
            grounding_fields = [f for f, m in fields.items() if isinstance(m, dict) and m.get("grounding")]
            for prefix, sub in ggw.items():
                if not isinstance(sub, dict) or not sub:
                    r.err(f"grounding_group_weights[{prefix!r}] must be a non-empty mapping {{subfield: weight}}")
                    continue
                total = 0.0
                for subfield, w in sub.items():
                    try:
                        wf = float(w)
                    except (TypeError, ValueError):
                        r.err(f"grounding_group_weights[{prefix!r}][{subfield!r}] not numeric: {w}")
                        continue
                    if not (0.0 <= wf <= 1.0):
                        r.err(f"grounding_group_weights[{prefix!r}][{subfield!r}] must be in [0,1], got {wf}")
                    total += wf
                    full_key = prefix + subfield
                    if full_key not in fields:
                        r.warn(f"grounding_group_weights references {full_key!r} which is not in checkpoint_schema")
                    elif not (fields[full_key].get("grounding") if isinstance(fields[full_key], dict) else False):
                        r.err(f"grounding_group_weights[{full_key!r}] is not a grounding:true checkpoint")
                if abs(total - 1.0) > 1e-6:
                    r.err(f"grounding_group_weights[{prefix!r}] weights must sum to 1, got {total}")
            # warn if some grounding checkpoints are not covered by any group
            covered = set()
            for prefix, sub in ggw.items():
                for subfield in (sub if isinstance(sub, dict) else {}):
                    covered.add(prefix + subfield)
            uncovered = [f for f in grounding_fields if f not in covered]
            if uncovered and len(ggw) > 0:
                # Only warn if the groups were meant to be exhaustive; if there's a catch-all like "" it's fine.
                r.warn(f"grounding checkpoints not covered by grounding_group_weights: {uncovered}")

    # --- judge_notes format guard ---
    jn_path = os.path.join(path, "judge_notes.md")
    if not os.path.exists(jn_path):
        r.err("judge_notes.md missing (required, part of blind payload)")
    else:
        jn = open(jn_path, encoding="utf-8").read()
        for pat in JUDGE_LEAK_PATTERNS:
            m = pat.search(jn)
            if m:
                r.err(f"judge_notes.md leaks an answer-key value: ...{m.group(0)!r}...")

        # --- two-layer disjointness (objective dims) --------------------------------------------
        # Split judge_notes into per-dimension sections by "## D<n>" headers, then require every
        # objective dim to declare the 第一层 / 第二层 split. Rationale: under two-layer scoring the
        # aggregator subtracts judge (Layer-2) deductions from the measured (Layer-1) base, so an
        # objective dim whose judge anchors still cover measurable defects would double-count. WARN
        # (not ERROR) so frozen containers not yet migrated to two-layer still lint clean; promote
        # to ERROR once all objective-dim containers are migrated.
        sections, cur = {}, None
        for line in jn.splitlines():
            hm = re.match(r"^#{1,4}\s*(D[1-6])\b", line)
            if hm:
                cur = hm.group(1); sections[cur] = []
            elif cur is not None:
                sections[cur].append(line)
        for d in obj:
            body = "\n".join(sections.get(d, []))
            if not body.strip():
                r.warn(f"judge_notes.md: objective dim {d} has no section (cannot verify two-layer disjointness)")
            elif ("第一层" not in body) or ("第二层" not in body):
                r.warn(f"judge_notes.md: objective dim {d} not structured into 第一层/第二层 — two-layer "
                       f"disjointness un-declared; judge deductions may double-count Layer-1 measurable defects")

        # --- no additive scoring: an anchor must never map to a positive-signed bonus -----------
        for m in re.finditer(r"[→=]\s*[＋+]\s*\d", jn):
            r.err(f"judge_notes.md declares an additive bonus (illegal — deductions only): ...{m.group(0)!r}...")

    # --- tier coherence ---
    tier = spec.get("tier")
    has_internal_cp = any(isinstance(m, dict) and m.get("type") in INTERNAL for m in fields.values())
    has_sample = any(isinstance(m, dict) and m.get("type") == "sample_verify" for m in fields.values())
    if tier == "T1":
        if set(obj) != {"D1", "D2"}:
            r.err(f"T1 requires objective_dims == [D1,D2], got {obj}")
        if gt_kind != "calculator":
            r.err(f"T1 requires an executable calculator, gt kind is {gt_kind!r}")
    elif tier == "T2":
        # No cap on objective_dims: a T2 container may anchor D1, D2, both, or neither.
        # Tier states truth-path strength (T1 demands an executable calculator), not how many
        # dimensions are anchored — the two are independent axes, and a Layer-1 fraction from an
        # internal-consistency checkpoint is as well-formed as one from a calculator. The global
        # `objective_dims ⊆ {D1,D2}` rule still bounds the set.
        if not (gt_kind == "calculator" or has_internal_cp or has_sample):
            r.err("T2 requires a verifiability path (calculator OR internal-consistency OR sample_verify)")
    elif tier == "T3":
        if len(obj) > 1:
            r.err(f"T3 requires <=1 objective dim, got {obj}")
        non_obj_weight = 100 - sum(w.get(d, 0) for d in obj)
        if non_obj_weight < 40:
            r.err(f"T3 requires non-objective weight >=40 (judgment-dominant), got {non_obj_weight}")
    else:
        r.err(f"unknown tier {tier!r} (expected T1|T2|T3)")

    return {"spec": spec, "schema_doc": schema_doc, "gt": gt, "prov": prov, "path": path}


def container_hash(bundle):
    """Stable hash over the container's contract files (excludes provenance.container_hash itself)."""
    h = hashlib.sha256()
    for name in ("spec.yaml", "checkpoint_schema.yaml", "gt_recipe.yaml", "judge_notes.md"):
        p = os.path.join(bundle["path"], name)
        if os.path.exists(p):
            h.update(name.encode())
            h.update(open(p, "rb").read())
    return h.hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="repo root (contains taskspecs/registry.json)")
    ap.add_argument("--stamp", action="store_true", help="write container_hash into each provenance.json")
    a = ap.parse_args()
    reg = loadj(os.path.join(a.root, "taskspecs", "registry.json"))
    # constitution + calculator library exist
    if not os.path.exists(os.path.join(a.root, reg["constitution"])):
        print(f"FATAL: constitution missing at {reg['constitution']}"); sys.exit(2)

    total_err = 0
    for entry in reg["tasks"]:
        r = Report(entry["task_id"])
        bundle = lint_container(a.root, entry, r)
        if bundle and a.stamp:
            digest = container_hash(bundle)
            bundle["prov"]["container_hash"] = digest
            with open(os.path.join(bundle["path"], "provenance.json"), "w", encoding="utf-8") as f:
                json.dump(bundle["prov"], f, ensure_ascii=False, indent=2); f.write("\n")
        status = "FAIL" if r.errors else ("WARN" if r.warns else "PASS")
        hashinfo = f" hash={container_hash(bundle)}" if bundle else ""
        print(f"[{status}] {entry['task_id']} {entry['path']}{hashinfo}")
        for m in r.errors:
            print(f"    ERROR: {m}")
        for m in r.warns:
            print(f"    warn : {m}")
        total_err += len(r.errors)

    print(f"\n{'='*60}\n{len(reg['tasks'])} containers linted; {total_err} error(s).")
    sys.exit(1 if total_err else 0)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
