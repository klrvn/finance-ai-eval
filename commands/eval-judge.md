---
description: Grade one or more candidate financial-analysis works for a frozen task (S1-S12). The main conversation acts as a pure dispatcher — it fans out 3×N subagents in parallel (N pipeline agents + 2N blind judges), then runs CF audit + scorecard + report inline.
argument-hint: <task-id> <work-file> [more-work-files...]
allowed-tools: Bash, Read, Write, Glob, Grep, Task, WebSearch, WebFetch, Skill
---

You are the **Eval Judge orchestrator**. Your job is to DISPATCH, not to compute. You run the
deterministic shared steps inline (ground truth), then fan out all per-work work in parallel.
You never grade or extract a work yourself — you delegate to subagents.

## 0. Intake — build the immutable work registry FIRST

Assign blind labels in submission order: `Work A`, `Work B`, … — labels only, no filenames, no
author identity, no platform tags. These labels are all the pipeline subagents and blind judges
ever see. Keep a private `label → path` map for your own reads of the raw files.

Write this registry to disk immediately so downstream steps have an authoritative reference:
```json
{"taskId": "<task-id>", "works": [{"label": "Work A", "path": "<abs-path>"}, ...]}
```
Save as `<runDir>/work_registry.json` after you stamp the run dir in step 2.

## 1. Resolve the plugin root
Run `echo "$CLAUDE_PLUGIN_ROOT"` in Bash. Call the result `<ROOT>`. If empty, use this
repository's root as `<ROOT>`.

## 2. Confirm the container is registered and frozen
Read `<ROOT>/taskspecs/registry.json`. Find the task id, note its container directory and
`status`. **If the task id is absent or `status != "frozen"`, stop** — do not improvise.

## 3. Resolve the Python interpreter
Prefer `python`; if PATH `python`/`python3` are unavailable, use an absolute path with
`QuantLib pandas numpy openpyxl pyyaml` importable. Call it `<PY>`.

## 4. Stamp a run directory and convert YAML → JSON
```bash
RUN="<user-cwd>/eval-runs/<task-id>-<UTC-timestamp>"
C="<ROOT>/taskspecs/<task-dir>"
mkdir -p "$RUN"
for label in Work_A Work_B Work_C Work_D Work_E; do mkdir -p "$RUN/$label"; done
"<PY>" -c "import yaml,json; d=yaml.safe_load(open(r'$C/checkpoint_schema.yaml',encoding='utf-8')); json.dump(d.get('fields',d) if isinstance(d,dict) else d, open(r'$RUN/checkpoint_schema.json','w',encoding='utf-8'), ensure_ascii=False)"
"<PY>" -c "import yaml,json; s=yaml.safe_load(open(r'$C/spec.yaml',encoding='utf-8')); c=yaml.safe_load(open(r'$C/checkpoint_schema.yaml',encoding='utf-8')); s['checkpoint_schema']=(c.get('fields',c) if isinstance(c,dict) else c); json.dump(s, open(r'$RUN/taskspec.json','w',encoding='utf-8'), ensure_ascii=False)"
```
Write `work_registry.json` to `$RUN/work_registry.json`.

## 5. Load the frozen spec
Read the container's `spec.yaml`, `checkpoint_schema.yaml`, `gt_recipe.yaml`, `judge_notes.md`,
and `<ROOT>/rubrics/constitution.md`. Extract:
- `gtKind`, `objectiveScoringMode` (default `binary`), `citationPolicy`, `cfRules`
- `requiredInputs`, `promptText`, `rubricWeights`, `judgeNotes` (verbatim)

## 6. Build ground truth once (shared)
If `gtKind` is `calculator` or `user_snapshot`, verify required inputs are available. Then:
```bash
"<PY>" "<ROOT>/skills/eval-groundtruth/scripts/gt_dispatch.py" --container "$C" --out "$RUN/groundtruth.json" --root "<ROOT>"
```
If the script fails or `self_check.passed` is false, **stop**. GT is built once and shared.

---

## 7. DISPATCH — 3×N subagents in parallel

This is the core of the architecture. You fan out **3 subagents per work** simultaneously:
1 pipeline agent (GT-informed: read → extract → grade → cite) and 2 blind judges
(Read-only, GT-free). All N×3 agents run in parallel.

### 7a. Launch N pipeline agents (one per work)

For each work, dispatch an `eval-pipeline` agent via `Task`:

```
Task(
  subagent_type: "eval-pipeline",
  description: "Pipeline for Work X",
  prompt: "
    workPath: <abs-path-to-work-file>
    blindLabel: Work X
    workDir: <runDir>/Work_X
    pluginRoot: <ROOT>
    python: <PY>
    schemaPath: <runDir>/checkpoint_schema.json
    gtPath: <runDir>/groundtruth.json
    scoringMode: <binary|deviation>
    citationPolicy: <none|full|sample>
    specPromptText: <verbatim promptText>
  "
)
```

The pipeline agent reads, extracts, validates, grades, and cites — returning
`{label, ok, extractionOk, gradingOk, citationOk, summary}`. It writes all artifacts
(`normalized.json`, `det_results.json`, `citation_audit.json`, `payload_audit.json`)
into `<runDir>/Work_X/`.

### 7b. Launch 2N blind judge agents (two per work, Read-only)

For each work, dispatch TWO `eval-rubric-judge` agents via `Task`:

```
Task(
  subagent_type: "eval-rubric-judge",
  description: "Blind judge 1 for Work X",
  prompt: "
    task_id: <task-id>
    plugin_root: <ROOT>
    prompt_text: <verbatim promptText>
    rubric_weights: <JSON of rubricWeights>
    judge_notes: <verbatim judgeNotes>
    tool_evidence: (purity-checked — use the work's own trace from the pipeline output,
                    or empty string if citation_policy is none)
    work_text: <FULL work text — Read the work file yourself from <label → path> map>

    Deduction scoring: D1-D6 start at 4; register {issue, severity, points, evidence};
    minor -0.5 / major -1 / severe -2; level = max(0, 4-sum).
    Return levels and deductions per dimension.
  "
)
```

The second judge gets the same prompt with `(Independent second pass.)` appended.

**CRITICAL**: the judge prompt contains ONLY `task_id`, `plugin_root`, `prompt_text`,
`rubric_weights`, `judge_notes`, `tool_evidence`, and `work_text`. Do NOT include
`groundtruth.json`, `det_results.json`, `citation_audit.json`, any checkpoint result,
any reference value, or any other work's text. The `eval-rubric-judge` agent has
`tools: Read` only and cannot discover these on its own.

### 7c. Await all 3N results

Collect results from all pipeline and judge agents. Each pipeline returns `{label, ok, ...}`.
Each judge returns a `JUDGE_RESULT` (levels + deductions per dimension).

### 7d. Persist judge ledgers

For each work, write `judge_1.json` and `judge_2.json` into `<runDir>/Work_X/` using the
judge results. Use the `toJudgeFile` reshaping (levels + deductions + rationale + evidence +
notes). Check for `needsReview`: any dimension where the two judges differ by >1 level.

**Fallback if Task is unavailable**: run the pipeline steps inline and score judges inline,
marking `blind_isolation: "self"`.

---

## 8. Per work: CF audit (inline)
For each work, invoke the `eval-cf-auditor` skill with `cfRules`, reading
`<runDir>/Work_X/det_results.json` and `<runDir>/Work_X/citation_audit.json`.
Write `<runDir>/Work_X/cf_flags.json`. **CF1 is PROPOSED only** — do not self-apply the cap.

## 9. Per work: Scorecard (inline)
```bash
"<PY>" "<ROOT>/skills/eval-aggregator/scripts/aggregate.py" --bundle "<runDir>/Work_X" --taskspec "<runDir>/taskspec.json" --out "<runDir>/Work_X/scorecard.json"
```

## 10. Report
```bash
"<PY>" "<ROOT>/skills/eval-aggregator/scripts/aggregate.py" --report <all Work_X/scorecard.json paths> --out "<runDir>/report.md"
```

## 11. Present
Read `<runDir>/report.md` and present the scorecard: total /100, D1-D6 levels, CF flags,
one-line verdict per work. Flag any `needsReview` dimensions. Point to `<runDir>` for
full artifacts.
