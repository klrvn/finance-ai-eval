# Eval Judge — Claude Code plugin guidance

Financial-analysis evaluation framework, packaged as a **Claude Code plugin**. It scores
candidate finance analyses against frozen task specs using deterministic checkpoints + citation
verification + blind rubric scoring, and emits a uniform, evidence-backed scorecard.

## Three-layer architecture (do not collapse the layers)

```
Layer 1  宪法   rubrics/constitution.md      Immutable principles: D1-D6, deduction scoring, CF1-5, blind isolation
Layer 2  容器   taskspecs/<task>/            One self-contained frozen container per task (S1-S8); indexed by taskspecs/registry.json
Layer 3  引擎   skills/eval-*                Task-agnostic engines; read containers via contracts, contain zero task knowledge
                agents/eval-judge.md         Orchestrator persona (subagent)
                agents/eval-rubric-judge.md  Blind rubric-scoring subagent (isolated; dispatched via Task tool)
```

Add a task = add a frozen container. Never edit the constitution or engines to fit one task,
and never improvise a rubric for a task that has no frozen container.

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

## Blind isolation (the anti-gaming property)

Blind rubric scoring must run in a fresh context that has never seen ground truth / checkpoint
results / citations. On Claude Code that means the **`eval-rubric-judge` subagent** (`tools: Read`
only), dispatched twice via `Task(subagent_type: "eval-rubric-judge", …)`. Do **not** score
rubrics inline in the orchestrator's own context — that reduces isolation to `"self"` and is only
a last resort when the Task tool is unavailable.

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
  must be green (0 errors) across S1-S8 before any container is treated as frozen.
- Ground truth must resolve cwd-independently: `gt_dispatch.py` derives its default `--root` from
  `__file__`, and containers/calculators are found relative to that root.
