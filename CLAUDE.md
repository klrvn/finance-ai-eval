# Eval Judge — Claude Code plugin guidance

Financial-analysis evaluation framework, packaged as a **Claude Code plugin**. It scores
candidate finance analyses against frozen task specs using deterministic checkpoints + citation
verification + blind rubric scoring, and emits a uniform, evidence-backed scorecard.

## Three-layer architecture (do not collapse the layers)

```
Layer 1  宪法   rubrics/constitution.md      Immutable principles: D1-D6, deduction scoring, CF1-5, blind isolation
Layer 2  引擎   skills/eval-*                Task-agnostic engines; read containers via contracts, contain zero task knowledge
                agents/eval-judge.md         Orchestrator persona (subagent)
                agents/eval-rubric-judge.md  Blind rubric-scoring subagent (isolated; dispatched via Task tool)
Layer 3  容器   taskspecs/<task>/            One self-contained frozen container per task; indexed by taskspecs/registry.json (currently S1-S11)
```

Add a task = add a frozen container. Never edit the constitution or engines to fit one task,
and never improvise a rubric for a task that has no frozen container. Immutable principles and
the lawful parameterization surface live in `rubrics/constitution.md` (§0 / §1) — do not restate
them here; the design rationale is in `docs/three-layer-architecture.md`.

## Plugin-root path convention (critical)

When this runs as an installed plugin, the **working directory is the user's project**, but the
constitution, containers, and calculator scripts live in the **plugin directory**. So:

- Resolve the plugin root once per run via Bash: `echo "$CLAUDE_PLUGIN_ROOT"` → `<ROOT>`.
- **Read reference data by absolute path**: `Read("<ROOT>/rubrics/constitution.md")`,
  `Read("<ROOT>/taskspecs/registry.json")`, etc. The Read tool does **not** expand `$CLAUDE_PLUGIN_ROOT`.
- **Invoke scripts** with the env var (Bash expands it):
  `python "${CLAUDE_PLUGIN_ROOT}/skills/eval-groundtruth/scripts/gt_dispatch.py" --container "${CLAUDE_PLUGIN_ROOT}/taskspecs/S3-bond-analytics" --out run/groundtruth.json`.
- When dispatching the blind judge, pass `plugin_root: "<ROOT>"` in the payload so the judge
  subagent can Read the constitution by absolute path.
- **Run artifacts** (`./eval-runs/…`, `run/…`) are written relative to the **user's cwd**, not the
  plugin dir (the plugin dir is typically read-only). This is intended.
- If `$CLAUDE_PLUGIN_ROOT` is empty (running from a checkout rather than an install), use this
  repository root as `<ROOT>`.

## Hybrid orchestration (main conversation + enforced island)

`/eval-judge` runs most steps **inline in the main conversation** (intake, spec load, ground truth,
per-work read + extract, CF audit, scorecard, report — each loads an `eval-*` skill directly, no
subagent). It delegates ONLY the isolation-critical core — per work: deterministic checkpoint
grading → citation/purity audit → **two blind rubric judges** → persist the two ledgers — to the
enforced `Workflow` island `orchestration/eval-judge.workflow.js`. The island's code assembles a
ground-truth-free judge payload and provably never routes ground truth / checkpoint results into it,
so blind isolation is guaranteed structurally, not by prose discipline. Steps that don't need
isolation stay inline to avoid subagent overhead.

## Blind isolation (the anti-gaming property)

Blind rubric scoring must run in a fresh context that has never seen ground truth / checkpoint
results / citations.

- **Primary (via `/eval-judge` command):** the Workflow island builds the GT-free payload in code
  and fans out two fresh judge subagents — isolation is structural.
- **Fallback (Workflow unavailable, or running via the `eval-judge` subagent, which has no Workflow
  tool):** dispatch the **`eval-rubric-judge` subagent** (`tools: Read` only) twice via
  `Task(subagent_type: "eval-rubric-judge", …)`, passing only the purity-checked payload.
- **Last resort (Task also unavailable):** score inline and mark `blind_isolation: "self"` (weaker).

Never build the judge payload in a context that holds ground truth and then score there — that is the
one thing this architecture exists to prevent.

## Python prerequisites

The deterministic calculators/graders are Python. Ensure these are importable on the PATH `python`:

```bash
pip install QuantLib pandas numpy openpyxl pyyaml
```

`pyyaml` is a hard dependency of `gt_dispatch.py` (reads `gt_recipe.yaml`). `QuantLib` is used by
the S3 bond calculator; `openpyxl` is only needed for `.xlsx` fixtures.

- Use `python` (not `python3`) in invocations — this package targets Windows where `python3` may be absent.
  `gt_dispatch.py` shells sub-calculators via `sys.executable`, so the interpreter stays consistent.

## Verifying changes

- `python "${CLAUDE_PLUGIN_ROOT}/skills/eval-taskspec-lint/scripts/spec_lint.py" --root "${CLAUDE_PLUGIN_ROOT}"`
  must be green (0 errors) across every registered container before any is treated as frozen.
- Ground truth must resolve cwd-independently: `gt_dispatch.py` derives its default `--root` from
  `__file__`, and containers/calculators are found relative to that root.
