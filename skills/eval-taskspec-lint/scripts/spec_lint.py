#!/usr/bin/env python3
"""Task-spec container linter.

Validates every frozen container in taskspecs/registry.json against the constitution's lawful
surface (rubrics/constitution.md §1) and the tier-coherence rules (README §tier). This is the
governance gate that lets the flow engines stay task-agnostic: if a container is well-formed here,
the engines can consume it mechanically.

Usage:
  python spec_lint.py --root <repo-root>            # lint all registered containers
  python spec_lint.py --root <repo-root> --stamp    # also write container_hash into provenance.json

Exit code 0 iff no ERRORs (WARNs are allowed, e.g. legacy golden: pending).
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
    if str(prov.get("origin")) not in ("legacy-v1", "kb"):
        r.err(f"provenance.origin must be legacy-v1|kb, got {prov.get('origin')!r}")

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

    # --- cf thresholds within constitution bands ---
    thr = spec.get("cf_thresholds", {}) or {}
    if "CF2_uncited_ratio" in thr and not (CF2_BAND[0] <= thr["CF2_uncited_ratio"] <= CF2_BAND[1]):
        r.err(f"CF2_uncited_ratio {thr['CF2_uncited_ratio']} outside band {CF2_BAND}")

    # --- engine_requirements type coverage ---
    er = spec.get("engine_requirements", {}) or {}
    declared = set(er.get("checkpoint_types", []) or [])
    used = {m.get("type") for m in fields.values() if isinstance(m, dict)}
    missing_decl = used - declared
    if missing_decl:
        r.warn(f"engine_requirements.checkpoint_types missing used types {sorted(missing_decl)}")

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
        if len(obj) > 1:
            r.err(f"T2 requires <=1 objective dim, got {obj}")
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

    # --- golden gate ---
    golden = prov.get("golden")
    if prov.get("origin") == "kb" and golden != "green":
        r.err("kb-authored container must have golden: green before freeze")
    elif golden == "pending":
        r.warn("golden: pending (legacy grandfathered; backfill golden pair before hardening)")

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
