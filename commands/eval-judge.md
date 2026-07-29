---
description: Grade one or more candidate financial-analysis works for a frozen task (S1-S11). The main conversation runs intake / spec / ground-truth / read / extract / CF / scorecard / report directly (loading the eval-* skills), and delegates ONLY the isolation-critical core — grade + citation/purity + blind judge x2 + persist — to the enforced Workflow island.
argument-hint: <task-id> <work-file> [more-work-files...]
allowed-tools: Bash, Read, Write, Glob, Grep, Task, Workflow, WebSearch, WebFetch, Skill
---

You are running the **Eval Judge** pipeline. You do most of the work **inline in this conversation**
(reading, extracting, ground truth, CF, scorecard, report — loading each `eval-*` skill directly),
and you delegate ONLY the **blind-scoring island** (per work: deterministic checkpoint grading →
citation/purity audit → two blind rubric judges → persist the two ledgers) to the enforced
`Workflow` engine, so blind-judge isolation is guaranteed structurally rather than prose-followed.

Why this split: the isolation-critical property is that the blind judges never see ground truth or
checkpoint results. Steps that don't need isolation (read/extract/CF/scorecard/report) run here with
no subagent overhead; the one place isolation must be *machine-guaranteed* — building a GT-free judge
payload and fanning out the two judges — runs inside `orchestration/eval-judge.workflow.js`, whose
code assembles the payload and provably never routes ground truth into it.

Arguments (space-separated): `$ARGUMENTS`
- First token = task id (e.g. `S2`, `S4`, `S9`).
- Remaining tokens = absolute path(s) to candidate work file(s). If none are given, ask the user
  for the work path(s) before proceeding.

Do this in order.

## 1. Resolve the plugin root
Run `echo "$CLAUDE_PLUGIN_ROOT"` in Bash. Call the result `<ROOT>`. If empty (running from a
checkout, not an install), use this repository's root as `<ROOT>`.

## 2. Confirm the container is registered and frozen
`Read("<ROOT>/taskspecs/registry.json")`, find the task id, note its container directory
(`<ROOT>/taskspecs/<task-dir>`) and `status`. **If the task id is absent or `status != "frozen"`,
stop** and tell the user which task ids exist — do not improvise a rubric for an unregistered task.

## 3. Resolve the Python interpreter
The deterministic leaf nodes are Python. Prefer plain `python`; if PATH `python`/`python3` are
unavailable (Windows Store stubs that exit 49), use an absolute interpreter path that has
`QuantLib pandas numpy openpyxl pyyaml` importable. Call it `<PY>`.

## 4. Build the immutable work registry
For each work path, assign a blind label in submission order (`Work A`, `Work B`, …) — labels only,
no filenames or author identity. Finalize this registry now, before launch, so the run stays
non-interactive. Keep a private `label → path` map for your own reads; the blind labels are all the
island ever sees.

## 5. Stamp a run directory and pre-build deterministic inputs (one Bash step)
Choose `<runDir> = <user-cwd>/eval-runs/<task-id>-<UTC-timestamp>` (timestamp from Bash
`date -u +%Y%m%dT%H%M%SZ`). Artifacts go under the user's cwd (the plugin dir is read-only). Then, in
one Bash step, create the run dirs and convert the container's YAML specs to the JSON the scripts
consume. Create exactly one `Work_<X>` dir per blind label (spaces → underscores, matching the
island's `w.label.replace(/\s+/g,'_')`):
```bash
RUN="<runDir>"; C="<ROOT>/taskspecs/<task-dir>"
mkdir -p "$RUN" "$RUN/Work_A" "$RUN/Work_B"   # one per blind label
"<PY>" -c "import yaml,json; d=yaml.safe_load(open(r'$C/checkpoint_schema.yaml',encoding='utf-8')); json.dump(d.get('fields',d) if isinstance(d,dict) else d, open(r'$RUN/checkpoint_schema.json','w',encoding='utf-8'), ensure_ascii=False)"
"<PY>" -c "import yaml,json; s=yaml.safe_load(open(r'$C/spec.yaml',encoding='utf-8')); c=yaml.safe_load(open(r'$C/checkpoint_schema.yaml',encoding='utf-8')); s['checkpoint_schema']=(c.get('fields',c) if isinstance(c,dict) else c); json.dump(s, open(r'$RUN/taskspec.json','w',encoding='utf-8'), ensure_ascii=False)"
```
If either conversion fails, **stop and surface the error — do not launch** (the container YAML is
malformed). This produces `<runDir>/checkpoint_schema.json` and `<runDir>/taskspec.json`.

## 6. Load the frozen spec (inline — `eval-task-specs` skill)
Invoke the `eval-task-specs` skill (or Read the container directly) to read the container's
`spec.yaml`, `checkpoint_schema.yaml`, `gt_recipe.yaml`, `judge_notes.md`, and shared
`<ROOT>/rubrics/constitution.md`. Extract and keep in memory:
- `gtKind` = `gt_recipe.kind`
- `objectiveScoringMode` = `spec.objective_scoring_mode` (default `binary`)
- `citationPolicy` = `spec.citation_policy`
- `cfRules` = `spec.cf_rules`
- `requiredInputs` (name, optional, availableInFixtures) from `spec.required_inputs` + `fixtures/`
- `promptText` = the verbatim candidate prompt
- `rubricWeights` = `spec.rubric_weights`
- `judgeNotes` = the **verbatim** contents of `judge_notes.md`

## 7. Build ground truth once (inline — `eval-groundtruth` skill)
GT is built **once** and shared across works. **GT-missing hard-stop:** if `gtKind` is `calculator`
or `user_snapshot`, first check every non-`optional` `requiredInputs` entry is either packaged in
`fixtures/` or supplied at runtime; if any is missing, **stop and report it verbatim — do not fake a
score.** Otherwise run the dispatcher and enforce `self_check.passed`:
```bash
"<PY>" "<ROOT>/skills/eval-groundtruth/scripts/gt_dispatch.py" --container "<ROOT>/taskspecs/<task-dir>" [--in name=path ...] [--snapshot <path>] --out "<runDir>/groundtruth.json" --root "<ROOT>"
```
If the script fails or `self_check.passed` is false/absent, **stop — refuse to grade on unverified
ground truth.** (`internal_consistency` tasks still produce an empty GT pack and proceed.)

## 8. Per work: read + extract (inline — `eval-extractor` skill)
For each blind label, using your private `label → path` map:
1. `Read(path)` → the work's full text (`workText`) and any of its OWN tool-call trace
   (`toolEvidence`, empty if none). Never mix works; never reuse another work's text.
2. Invoke the `eval-extractor` skill to extract every checkpoint field defined in
   `<runDir>/checkpoint_schema.json` into value | MISSING | AMBIGUOUS with evidence + citations +
   tool_inventory. Never infer or compute a missing value. Write `<runDir>/Work_<X>/normalized.raw.json`,
   then run the validator:
   ```bash
   "<PY>" "<ROOT>/skills/eval-extractor/scripts/validators.py" --normalized "<runDir>/Work_<X>/normalized.raw.json" --schema "<runDir>/checkpoint_schema.json" --out "<runDir>/Work_<X>/normalized.json"
   ```
   Confirm `normalized.json` was written before continuing.

## 9. Launch the enforced blind-scoring island (`Workflow`)
Call the `Workflow` tool with `scriptPath: "<ROOT>/orchestration/eval-judge.workflow.js"` and `args`:
```json
{
  "taskId": "<task-id>",
  "repoRoot": "<ROOT>",
  "python": "<PY>",
  "gtPath": "<runDir>/groundtruth.json",
  "schemaPath": "<runDir>/checkpoint_schema.json",
  "spec": {
    "objectiveScoringMode": "<binary|deviation>",
    "citationPolicy": "<none|full|sample>",
    "promptText": "<verbatim candidate prompt>",
    "rubricWeights": { "D1": 40, "D2": 20, "D3": 15, "D4": 0, "D5": 0, "D6": 25 },
    "judgeNotes": "<verbatim judge_notes.md>"
  },
  "works": [
    { "label": "Work A", "wd": "<runDir>/Work_A", "workText": "<full blind work text>", "toolEvidence": "<work-own trace or empty>" }
  ]
}
```
The island runs, per work: `grade_checkpoints.py` → citation/purity audit (if `citationPolicy != none`)
→ two blind rubric judges on a **ground-truth-free** payload → persist `judge_1.json` / `judge_2.json`.
It writes `det_results.json`, `citation_audit.json`, `payload_audit.json`, `judge_1.json`,
`judge_2.json` into each `Work_<X>/`, and returns per-work `{ label, needsReview }`. Do **not** pass
`groundtruth.json`'s contents or any checkpoint result inside `spec` or `works` — only the fields
above. If any work fails, the island hard-stops; surface it, do not emit a partial report.

**Fallback if `Workflow` is unavailable:** dispatch the two blind judges via
`Task(subagent_type: "eval-rubric-judge", …)` — twice, each a fresh subagent given ONLY the GT-free
payload — and run `grade_checkpoints.py` + the citation/purity audit inline. This preserves blind
isolation via the isolated subagent; mark `blind_isolation: "subagent"`. Only if the Task tool is
also unavailable, score inline and mark `blind_isolation: "self"` (weaker, last resort).

## 10. Per work: CF audit (inline — `eval-cf-auditor` skill)
After the island returns, for each work invoke the `eval-cf-auditor` skill with `cfRules`, reading
`<runDir>/Work_<X>/det_results.json` and `citation_audit.json` (if present). Write
`<runDir>/Work_<X>/cf_flags.json`. **CF1 (fabrication) is PROPOSED with hard evidence and left for
human confirmation — do NOT apply the score cap yourself.** CF5 is already handled by the grader.

## 11. Per work: scorecard (inline — `eval-aggregator`)
For each work:
```bash
"<PY>" "<ROOT>/skills/eval-aggregator/scripts/aggregate.py" --bundle "<runDir>/Work_<X>" --taskspec "<runDir>/taskspec.json" --out "<runDir>/Work_<X>/scorecard.json"
```

## 12. Report (inline — `eval-aggregator`)
Once every work has a scorecard:
```bash
"<PY>" "<ROOT>/skills/eval-aggregator/scripts/aggregate.py" --report <all Work_<X>/scorecard.json paths> --out "<runDir>/report.md"
```

## 13. Present
Read `<runDir>/report.md` and present the scorecard: total /100, per-dimension D1-D6 levels,
applied/pending CF flags, and the one-line verdict per work. Flag any dimension where the two blind
judges diverged by more than one level (the island's `needsReview`). Point the user to `<runDir>` for
full artifacts.
