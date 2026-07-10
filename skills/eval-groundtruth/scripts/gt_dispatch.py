#!/usr/bin/env python3
"""Deterministic ground-truth dispatcher.

Reads a task-spec container's gt_recipe.yaml and produces run/groundtruth.json by routing on
`kind` - so the invocation is built by contract, not improvised:

  kind: calculator          -> render the invocation template with named inputs, run the shared
                               calculator, then ENFORCE self_check.passed (halt if false/missing).
  kind: internal_consistency-> write {values:{}, recipe, self_check:{passed:true}} (no external truth;
                               reconcile/monotonic/consistency checkpoints do the work, e.g. S1 Dupont, S4 multifactor).
  kind: user_snapshot       -> if --snapshot given, write {values:<snapshot>, ...}; else {values:null}.

Usage:
  python gt_dispatch.py --container taskspecs/S4-multifactor-attribution --in fixture=port.csv --out run/groundtruth.json
  python gt_dispatch.py --container taskspecs/S2-momentum-backtest --in prices=px.csv --in benchmark=b.csv --out run/gt.json
  python gt_dispatch.py --container taskspecs/S1-dupont-analysis --out run/gt.json
  python gt_dispatch.py --container taskspecs/S6-macro-view --snapshot snap.json --out run/gt.json

The container path is resolved relative to --root (default: three levels up from this script, i.e. repo root).
Exit code 0 iff a valid, self-check-passed groundtruth.json was written.
"""
import argparse, json, os, re, subprocess, sys

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr); raise

# inputs that are inherently multi-valued (space-separated -> multiple argv tokens)
LIST_INPUTS = {"navs"}


def render_argv(template, mapping):
    """Turn an invocation template into an argv list, deterministically.

    - `[ ... ]` wraps an optional segment: kept (unwrapped) iff every {name} inside is supplied,
      dropped otherwise.
    - `{name}` is replaced by mapping[name]; a name in LIST_INPUTS (or a value with spaces) expands
      to multiple tokens. Non-placeholder tokens (fixed flags/values) pass through verbatim.
    Windows paths are safe: we tokenize the template on whitespace and substitute per-token, so we
    never run a backslash path through shlex.
    """
    def opt(m):
        inner = m.group(1)
        names = re.findall(r"\{(\w+)\}", inner)
        return inner if all(n in mapping for n in names) else ""
    template = re.sub(r"\[([^\]]*)\]", opt, template)

    argv, missing = [], []
    for tok in template.split():
        m = re.fullmatch(r"\{(\w+)\}", tok)
        if not m:
            argv.append(tok); continue
        key = m.group(1)
        if key not in mapping:
            missing.append(key); continue
        val = str(mapping[key])
        if key in LIST_INPUTS or " " in val:
            argv.extend(val.split())
        else:
            argv.append(val)
    if missing:
        raise SystemExit(f"gt_dispatch: missing required input(s) {missing} for invocation template")
    return argv


def gate_self_check(gt_path):
    """The uniform contract gate: a run may proceed only on a self-checked truth."""
    if not os.path.exists(gt_path):
        raise SystemExit(f"gt_dispatch: calculator wrote no output at {gt_path}")
    try:
        gt = json.load(open(gt_path, encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"gt_dispatch: groundtruth is not valid JSON: {e}")
    sc = gt.get("self_check")
    if not isinstance(sc, dict) or "passed" not in sc:
        raise SystemExit("gt_dispatch: calculator output lacks a self_check{passed} block (contract violation)")
    if not sc.get("passed"):
        raise SystemExit(f"gt_dispatch: self_check FAILED -> refusing to grade on unverified truth. detail={sc}")
    return gt


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", required=True, help="path to a task-spec container dir")
    ap.add_argument("--out", required=True, help="where to write groundtruth.json")
    ap.add_argument("--in", dest="inputs", action="append", default=[], metavar="name=path",
                    help="named input for the calculator invocation template (repeatable)")
    ap.add_argument("--snapshot", help="user snapshot JSON (kind: user_snapshot)")
    ap.add_argument("--root", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
                    help="repo root for resolving calculator scripts (default: repo root)")
    a = ap.parse_args()

    cdir = a.container if os.path.isabs(a.container) else os.path.join(a.root, a.container)
    recipe = yaml.safe_load(open(os.path.join(cdir, "gt_recipe.yaml"), encoding="utf-8"))
    kind = recipe.get("kind")
    rid = recipe.get("recipe_id", "unknown")

    if kind == "internal_consistency":
        out = (recipe.get("groundtruth_output") or {})
        gt = {"values": out.get("values", {}), "recipe": rid, "self_check": out.get("self_check", {"passed": True})}
        write_json(a.out, gt)
        print(f"gt_dispatch: {rid} (internal_consistency) -> empty values; consistency checks do the work.")

    elif kind == "user_snapshot":
        if a.snapshot:
            snap = json.load(open(a.snapshot, encoding="utf-8"))
            gt = {"values": snap, "recipe": rid, "self_check": {"passed": True}}
            print(f"gt_dispatch: {rid} (user_snapshot) -> wrote provided snapshot values.")
        else:
            gt = {"values": None, "recipe": rid, "self_check": {"passed": True}}
            print(f"gt_dispatch: {rid} (user_snapshot) -> no snapshot; values=null, grounding checkpoints -> NA.")
        write_json(a.out, gt)

    elif kind == "calculator":
        cal = recipe.get("calculator", {})
        script = cal.get("script")
        if not script:
            raise SystemExit("gt_dispatch: calculator recipe missing 'script'")
        script_abs = script if os.path.isabs(script) else os.path.join(a.root, script)
        if not os.path.exists(script_abs):
            raise SystemExit(f"gt_dispatch: calculator script not found: {script_abs}")
        mapping = {"out": a.out}
        for pair in a.inputs:
            if "=" not in pair:
                raise SystemExit(f"gt_dispatch: --in expects name=path, got {pair!r}")
            k, v = pair.split("=", 1)
            mapping[k.strip()] = v.strip()
        argv = [sys.executable, script_abs] + render_argv(cal["invocation"], mapping)
        print(f"gt_dispatch: {rid} (calculator) -> {' '.join(argv[1:])}")
        proc = subprocess.run(argv)   # calculator writes --out itself
        # Enforce the gate regardless of the script's own exit convention.
        gate_self_check(a.out)
        if proc.returncode != 0:
            print(f"gt_dispatch: note - calculator exited {proc.returncode} but self_check passed; proceeding.")
        print(f"gt_dispatch: {rid} self_check passed.")

    else:
        raise SystemExit(f"gt_dispatch: unknown gt_recipe kind {kind!r} (expected calculator|internal_consistency|user_snapshot)")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
