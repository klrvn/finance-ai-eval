---
description: Grade one or more candidate financial-analysis works for a frozen task (S1-S12). The main conversation acts as a pure dispatcher — it fans out 3×N subagents in parallel (N pipeline agents + 2N blind judges), then runs CF audit + scorecard + report inline.
argument-hint: <task-id> <work-file> [more-work-files...]
allowed-tools: Bash, Read, Write, Glob, Grep, Task, WebSearch, WebFetch, Skill
---

You are the **Eval Judge orchestrator**. Your job is to DISPATCH, not to compute. You run the
deterministic shared steps inline (ground truth), then fan out all per-work work in parallel.
You never grade or extract a work yourself — you delegate to subagents.

**Two rules that determine whether this run takes 10 minutes or 35:**
1. **Never `Read` a candidate work file.** Shell-copy it (§4) and let subagents read it. Holding
   N works in your context is what made prior runs re-read 5.9M cached tokens.
2. **Emit all 3N `Task` calls in ONE message** (§7). Split across messages = sequential = slow.

## 0. Intake — build the immutable work registry FIRST

Assign blind labels in submission order: `Work A`, `Work B`, … — labels only, no filenames, no
author identity, no platform tags. These labels are all the pipeline subagents and blind judges
ever see. Keep a private `label → path` map for your own reads of the raw files.

Write this registry to disk immediately so downstream steps have an authoritative reference:
```json
{"taskId": "<task-id>", "works": [{"label": "Work A", "path": "<abs-path>"}, ...]}
```
Save as `<runDir>/work_registry.json` after you stamp the run dir in step 2.

## 1. Resolve the plugin root — in ONE Bash call, then stop looking

If the invocation already told you the plugin root, use it and skip this step entirely.

Otherwise resolve it with a **single** Bash call that tries every location at once:

```bash
for p in "$CLAUDE_PLUGIN_ROOT" \
         "$HOME/.claude/plugins/cache/eval-judge-marketplace/eval-judge/1.3.0" \
         "$HOME/Documents/eval-judge" ; do
  [ -f "$p/taskspecs/registry.json" ] && { echo "ROOT=$p"; break; }
done
```

Take the first hit as `<ROOT>`. **Do not** keep probing with `ls`/`find` afterwards — a prior run
burned **2.2 minutes over 16 Bash calls** hunting for this because `$CLAUDE_PLUGIN_ROOT` was empty.
One call, first hit, move on. Only if all candidates miss, ask the user for the path.

## 2. Confirm the container is registered and frozen
Read `<ROOT>/taskspecs/registry.json`. Find the task id, note its container directory and
`status`. **If the task id is absent or `status != "frozen"`, stop** — do not improvise.

## 3. Resolve the Python interpreter
Prefer `python`; if PATH `python`/`python3` are unavailable, use an absolute path with
`QuantLib pandas numpy openpyxl pyyaml` importable. Call it `<PY>`.

## 4. Stamp a run directory, blind-copy the works, convert YAML → JSON

Create ONE `Work_X` dir per blind label — exactly N of them, no more (do not pre-create dirs for
works that don't exist).

**Blind-copy each work to a neutral filename.** This is what keeps the judges blind while keeping
YOUR context small: the copy happens in the shell, so the work text never enters your context, and
the resulting path contains only the blind label — no `model1_s12.md`, no platform name, no ordering
hint. Both the pipeline agents and the judges read this copy, never the original.

```bash
RUN="<user-cwd>/eval-runs/<task-id>-<UTC-timestamp>"
C="<ROOT>/taskspecs/<task-dir>"
mkdir -p "$RUN"
# one line per work, in blind-label order (from work_registry.json):
mkdir -p "$RUN/Work_A" && cp "<abs-path-of-work-A>" "$RUN/Work_A/work.md"
mkdir -p "$RUN/Work_B" && cp "<abs-path-of-work-B>" "$RUN/Work_B/work.md"
# ... one pair per work, N total
"<PY>" -c "import yaml,json; d=yaml.safe_load(open(r'$C/checkpoint_schema.yaml',encoding='utf-8')); json.dump(d.get('fields',d) if isinstance(d,dict) else d, open(r'$RUN/checkpoint_schema.json','w',encoding='utf-8'), ensure_ascii=False)"
"<PY>" -c "import yaml,json; s=yaml.safe_load(open(r'$C/spec.yaml',encoding='utf-8')); c=yaml.safe_load(open(r'$C/checkpoint_schema.yaml',encoding='utf-8')); s['checkpoint_schema']=(c.get('fields',c) if isinstance(c,dict) else c); json.dump(s, open(r'$RUN/taskspec.json','w',encoding='utf-8'), ensure_ascii=False)"
```

Write `work_registry.json` to `$RUN/work_registry.json` — it holds the `blind_label → original
path` mapping that YOU use for the final report. Never pass it to a subagent.

**Do NOT `Read` the work files yourself.** The pipeline agents and judges read
`$RUN/Work_X/work.md` directly. Keeping all N works out of your context is what makes this fast —
in a prior 4-work run, holding them resident cost 5.9M cache-read tokens across 83 turns.

### 4b. Workbook submissions (`.xlsx` / `.xlsm`) — dump the formula layer

Only relevant for containers whose deliverable is a spreadsheet (currently S13). Skip entirely
when every submitted work is already text.

A blind judge has `tools: Read` and cannot open a workbook. If the container ships
`tools/workbook_dump.py`, convert each spreadsheet work to text **in the shell** (so it still
never enters your context) and keep the original alongside for the pipeline agent:

```bash
# per work whose source file is .xlsx/.xlsm — instead of the plain `cp` above:
mkdir -p "$RUN/Work_A" && cp "<abs-path-of-work-A>" "$RUN/Work_A/work.xlsx"
"<PY>" "$C/tools/workbook_dump.py" --in "$RUN/Work_A/work.xlsx" --out "$RUN/Work_A/work.md"
```

- The judge still receives **only** `work_file: $RUN/Work_X/work.md` — the dump. It shows every
  cell as `单元格 | 公式 | 值`, which is what lets it assess a live-formula model at all.
- The `eval-pipeline` agent has Bash and reads **both**: `work.md` for prose and `work.xlsx` via
  openpyxl for exact cell extraction.
- The dump self-reports two conditions the judge must not misread: a workbook with **no formulas
  at all**, and a workbook whose formulas were **never computed** (openpyxl-written files carry no
  cached values, so the value column is legitimately blank). Do not paraphrase or strip those
  banners.
- Text works are copied to `work.md` unchanged, exactly as in §4.

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

## 7. DISPATCH — all 3N subagents in ONE message

> ### ⛔ THE ONE RULE THAT MAKES THIS FAST
> **Emit all 3N `Task` calls as 3N tool-use blocks in a SINGLE assistant message.**
> Tool calls in one message run concurrently; tool calls split across messages run
> sequentially. There is no other mechanism for parallelism here.
>
> For N=4 that is **one message containing exactly 12 `Task` calls**. Do not send a
> pipeline call, look at its result, then send the next. Do not send the N pipelines in
> one message and the 2N judges in another — that serializes the two halves and costs
> you roughly half the speedup.
>
> Measured cost of getting this wrong: in three prior 4-work runs the judges went out in
> batches of 4+4 and 1+7, adding 4.3 and 6.1 minutes of dead air to runs that took 31-36
> minutes total.

**Before you dispatch**, count: N works → **N** `eval-pipeline` + **2N** `eval-rubric-judge`
= **3N** calls. Write that number down. After the message, verify you emitted exactly that many.

**Judges do not depend on pipelines.** A judge needs nothing the pipeline produces — no ground
truth, no checkpoint results, no citation audit. That independence is precisely why all 3N can
go out at once. Never make a judge wait for a pipeline.

### The single dispatch message

Build one message with, for each work X in `Work_A`…`Work_<N>`, these three calls:

**① `eval-pipeline` — GT-informed (1 per work)**
```
Task(
  subagent_type: "eval-pipeline",
  description: "Pipeline for Work X",
  prompt: "
    workPath: <runDir>/Work_X/work.md
    blindLabel: Work X
    workDir: <runDir>/Work_X
    pluginRoot: <ROOT>
    python: <PY>
    schemaPath: <runDir>/checkpoint_schema.json
    gtPath: <runDir>/groundtruth.json
    scoringMode: <binary|deviation>
    citationPolicy: <none|full|sample>
  "
)
```
Returns `{label, ok, extractionOk, gradingOk, citationOk, summary}` and writes
`normalized.json`, `det_results.json`, `citation_audit.json`, `payload_audit.json`
into `<runDir>/Work_X/`.

**② and ③ `eval-rubric-judge` — blind, Read-only (2 per work)**
```
Task(
  subagent_type: "eval-rubric-judge",
  description: "Blind judge 1 for Work X",
  prompt: "
    task_id: <task-id>
    plugin_root: <ROOT>
    work_file: <runDir>/Work_X/work.md
    out_path:  <runDir>/Work_X/judge_1.json
    prompt_text: <verbatim promptText>
    rubric_weights: <JSON of rubricWeights>
    judge_notes: <verbatim judgeNotes>

    Read work_file — that is the entire work you are scoring. Its own tool-chain /
    execution record is inside that same file; treat that as your tool_evidence for D6.
    There is no separate evidence input and nothing else to wait for.

    Deduction scoring: D1-D6 start at 4; register {issue, severity, points, evidence};
    minor -0.5 / major -1 / severe -2; level = max(0, 4-sum).

    WRITE your ledger JSON yourself to out_path with the Write tool, then reply with
    ONE line confirming it (e.g. 'wrote judge_1.json — D1..D6 = 3.5/3/4/3/2/3').
    Do NOT return the ledger as text for the orchestrator to re-write.
  "
)
```
Judge 2 is identical with `out_path: <runDir>/Work_X/judge_2.json` and
`(Independent second pass — reason from scratch.)` appended.

### Isolation rules for the judge prompt — non-negotiable

- **Whitelist.** Only `task_id`, `plugin_root`, `work_file`, `prompt_text`, `rubric_weights`,
  `judge_notes`. Anything else is a leak. (Full whitelist rationale: `eval-orchestrator` §3.)
- **Never** include `groundtruth.json` contents, `det_results.json`, `citation_audit.json`, any
  reference value, any checkpoint outcome, or any other work's text.
- **Pass `work_file`, never the original path.** `<runDir>/Work_X/work.md` carries only the blind
  label. Handing over `model1_s12.md` would leak identity and submission order — the exact thing
  blind scoring exists to prevent, and forbidden by `agents/eval-rubric-judge.md`.
- The judge has `tools: Read` only — no Bash/Glob/Grep — so it cannot discover GT or checkpoint
  files on disk even though they sit in the same run directory.

### 7b. Verify — do NOT re-write the ledgers

Each judge writes its own `judge_N.json` (that's why it gets `out_path`). Your job is only to
confirm all 2N files exist, in **one** Bash call:

```bash
RUN="<runDir>"; for w in Work_A Work_B Work_C Work_D; do
  for j in 1 2; do
    f="$RUN/$w/judge_$j.json"
    [ -s "$f" ] && python -c "import json,sys; json.load(open(sys.argv[1],encoding='utf-8'))" "$f" \
      && echo "ok   $w/judge_$j.json" || echo "MISS $w/judge_$j.json"
  done
done
```

> ⛔ **Never hand-write a Python script to persist ledgers, and never re-emit ledger JSON
> yourself.** A prior run did exactly that — 35.6 KB of generated Python across
> `_write_judges_A.py` / `_write_judges_BCD.py` / `_cf_audit.py` — which cost **~6 minutes of
> pure token emission** (one 23.5 KB write alone took 3.9 min) and made this phase 45% of total
> runtime. The judges already wrote the files. Read them if you need the levels; don't rebuild them.

Then read the 2N files to compute `needsReview`: any dimension where the two judges for the same
work differ by more than one level.

If a single subagent fails, **retry just that one** — every other artifact is already on disk.
Only if `Task` is entirely unavailable, run the steps inline and mark
`blind_isolation: "self"` (weaker).

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

### Readability requirement — 语句通顺易读，句子信息密度不要过大

`aggregate.py` can only concatenate fixed template sentences, so it cannot meet this on its own.
**You must**, both in what you present here and in any narrative rewrite of `report.md`:

- **One idea per sentence.** Never pack objective baseline + deduction points + weight + weighted
  score + verdict into a single clause. Lead with the conclusion, then give the evidence; split
  into several sentences or a short list where needed.
- **Rewrite the high-density template lines**, e.g. the verdict sentence that chains margin +
  deciding dimension + provisional-ranking rationale together. (The two-layer scoring lines and
  the overview-table legend are already emitted one-idea-per-line by `aggregate.py` — leave them.)
- **Do not repeat zero-weight dimensions per work** — collapse D4/D5 into a single note when their
  weight is 0.
- **Keep one language throughout**; do not mix Chinese and English judge rationales.

> **Hard constraint: numbers, levels, tables and checkpoint results are hard data. Reword freely —
> never alter a value, a ranking, or a conclusion.** Readability edits apply to narrative prose only.
> `report.md` as emitted by `aggregate.py` is the reproducible evidence artifact; if you rewrite it,
> keep the script-generated tables intact.

### Checkpoint table — always in full

**Whenever the task has deterministic checkpoints, always show the full checkpoint × work table.**
Never omit it, sample it, list only the failures, or replace it with a prose summary — in the
report *or* in what you present here.

- **Rows = every checkpoint** (the union of checkpoint IDs across all works). Include passes and
  `NA`s, not just failures; do not collapse rows because they are all `通过`.
- **Columns = every work.** No work is dropped for scoring low, ranking last, or making the table
  wide.
- Failures carry their deviation `(Δ=…)`; keep `NA` rows and the footnote that `NA` does not
  deduct and is removed from the denominator.
- `aggregate.py` already builds the table to this rule. Your job is to **preserve it verbatim** —
  the readability pass above must not touch it, only the prose around it.
- The only permitted substitute is `_本任务无确定性检查点。_`, and only when the task genuinely
  has none.
