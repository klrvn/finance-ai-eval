# Eval Judge — Claude Code plugin guidance

Financial-analysis evaluation framework, packaged as a **Claude Code plugin**. It scores
candidate finance analyses against frozen task specs using deterministic checkpoints + citation
verification + blind rubric scoring, and emits a uniform, evidence-backed scorecard.

## Three-layer architecture (do not collapse the layers)

```
Layer 1  宪法   rubrics/constitution.md      Immutable principles: D1-D6, deduction scoring, CF1-5, blind isolation
Layer 2  引擎   skills/eval-*                Task-agnostic engines; read containers via contracts, contain zero task knowledge
                commands/eval-judge.md       PIPELINE SPEC — single source of truth for step order + dispatch
                agents/eval-judge.md         Dispatcher persona (subagent entry point)
                agents/eval-pipeline.md      Per-work GT-informed pipeline subagent (N in parallel)
                agents/eval-rubric-judge.md  Blind rubric-scoring subagent (Read-only; 2N in parallel)
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

## Orchestration: dispatcher + 3×N parallel subagents

**`commands/eval-judge.md` is the single source of truth for the pipeline** — step order, parallel
structure, and subagent dispatch. Do not restate the pipeline elsewhere; point at that file.

The main conversation is a **pure dispatcher**. It runs only the shared sequential prefix (intake →
run dir → spec → ground truth), then fans out **3 subagents per work, all in parallel**:

- **N × `eval-pipeline`** (`agents/eval-pipeline.md`) — per work: read → extract → validate →
  deterministic checkpoint grading → citation/purity audit. GT-informed; reads `groundtruth.json`.
- **2N × `eval-rubric-judge`** (`agents/eval-rubric-judge.md`, `tools: Read`) — two independent
  blind judges per work, on a GT-free payload.

Then CF audit → scorecard → report run inline. Wall clock is `max(slowest subagent)`, not the sum.

Blind judges need **no output** from the GT-informed pipeline, so they dispatch concurrently with it.

## Blind isolation (the anti-gaming property)

Blind rubric scoring must run in a fresh context that has never seen ground truth / checkpoint
results / citations.

- **Primary:** the dispatcher builds the judge payload from a **field whitelist** — `task_id`,
  `plugin_root`, `prompt_text`, `rubric_weights`, `judge_notes`, `tool_evidence`, `work_text` — and
  dispatches `Task(subagent_type: "eval-rubric-judge", …)` twice per work. That agent has
  `tools: Read` only: no Bash/Glob/Grep, so it cannot discover GT or checkpoint files on disk even
  though they exist there. The whitelist is enforced by `eval-orchestrator` §3.
- **Last resort (Task unavailable):** score inline and mark `blind_isolation: "self"` (weaker).

Never build the judge payload in a context that holds ground truth and then score there — that is the
one thing this architecture exists to prevent. The dispatcher DOES hold ground truth, so the
whitelist in `eval-orchestrator` §3 is the gate: any field not on it is a leak.

**An `eval-pipeline` agent must never dispatch its own judges** — it holds GT by the time it
finishes grading, so judges spawned from it would inherit a contaminated parent. Only the
dispatcher spawns judges.

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
