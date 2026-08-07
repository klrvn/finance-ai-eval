# Three-Layer Grading Architecture

> Status: **Layers 1-3 IMPLEMENTED** (2026-07-09 migration). The constitution, the 8 task-spec
> containers (S1-S8, `origin: legacy-v1`, frozen), the calculator-contract sidecars, the registry,
> and the `eval-taskspec-lint` gate are built and green; the flow engines read from containers.
> 
> Not yet done (follow-up): GT input data backfill for A-class tasks (S2/S3/S5/S7/S8).
> 
> This document specifies the architecture that decouples 任务规范 (task spec) from 评分流程
> (grading flow) so tasks scale beyond S1-S8.
>
> **后续新增（不在本文 2026-07-09 迁移叙述范围内）**：S9（数据获取稳定性，live-fetch GT）已加入
> `taskspecs/registry.json`。本文档下述 "S1-S8"/"8 containers" 均指 2026-07-09 迁移时的参考集，是
> **历史记录**；当前已注册容器的权威清单始终以 `registry.json` 为准（当前 S1–S11）。

---

## 0. Problem statement

The current framework hardcodes 8 tasks: `tasks.md` is a monolithic spec file, ground-truth
scripts are task-named, and `task_validators` are semi-bound to specific task IDs. Adding a
task means editing the framework. Target: a three-layer architecture where

1. **Rubrics layer（宪法层）** — D1-6 + CF1-5 + 扣分制 scoring math: immutable ground principles.
2. **Flow layer（引擎层）** — the existing skills become task-agnostic engines, progressively
   loaded, customizable ONLY through contracted extension points in the container.
3. **Task-spec layer（容器层）** — one self-contained "task spec container" per task,
   hand-authored and frozen before any candidate work is graded.

---

## 1. Layer 1 — Rubrics (the constitution)

**Content** (today's `rubric_and_cf.md`, promoted): D1-6 definitions (D1 includes data-source tier grading — 第一级一手/官方 > 第二级权威机构二次分析 > 第三级非金融机构文章/新闻 — directly affecting D1 score; D6 covers only tool-execution behavior, not source tiers); 扣分制 severity scale
(minor −0.5 / major −1 / severe −2, level = max(0, 4−Σ)); scoring math — the two-layer
combination `fraction = mean(layer-1 measured base, layer-2 deduction coefficient)`, where the
coefficient is `max(0, (4 − Σpoints)/4)`, and dimensions outside `objective_dims` have no
layer-1 base and so score the coefficient alone (averaged since aggregator 3.0; previously
subtractive, which stacked the two penalties and zeroed dimensions far too easily — the accepted
cost of averaging is that each layer's reach is halved) — plus continuous fraction ×
weight, NA excluded from denominator, scored_weight disclosure; CF1-5 definitions, evidence
requirements, cap effects; blind/absolute/isolation principles; D6 deduction anchors
(计算无执行轨迹, 工具链覆盖不完整, 调用失败未处理, 完全无工具使用证据).

**Location**: move `skills/eval-task-specs/references/rubric_and_cf.md` → `rubrics/constitution.md`.
The `eval-task-specs` skill retires; its role is replaced by the container registry.

**Invariants — no container may override**:

- The dimension set (exactly D1-6) and their definitions.
- The severity vocabulary and point values.
- CF rules' evidence requirements and cap effects; the CF1 human-confirmation gate.
- NA-does-not-deduct; unverifiable ≠ fabrication; length earns nothing.
- Blind isolation via fresh subagents; absolute (non-comparative) scoring.

**What containers MAY parameterize** (the entire lawful surface):

- `rubric_weights` (must sum to 100, within tier-dependent bands — see §3.4).
- `objective_dims` (subset, subject to tier coherence rules).
- `cf_rules` (subset of CF1-5) and bounded thresholds (e.g. CF2 uncited-ratio within [0.15, 0.40]).
- `citation_policy` (none / sample,k / per_cell_sample / full).
- Per-dimension **deduction anchors** in `judge_notes.md`: mappings of task-specific
  situations → existing severities. Never new severities, never weight changes,
  never expected numeric answers.

---

## 2. Layer 2 — Flow engines (task-agnostic)

### 2.1 Engine ↔ container contract map

| Engine                 | Reads from container                              | Extension point (only these)                                          |
| ---------------------- | ------------------------------------------------- | --------------------------------------------------------------------- |
| eval-orchestrator      | registry entry, `spec.yaml`                       | none — control flow is fixed                                          |
| eval-extractor         | `checkpoint_schema.yaml`, `extraction_notes.md`   | prose notes only                                                      |
| eval-groundtruth       | `gt_recipe.yaml`, `fixtures/`                     | shared calculator library; task-local script via calculator contract  |
| eval-checkpoint-grader | `checkpoint_schema.yaml`                          | checkpoint-type registry (engine-level); `validator:` plugin dispatch |
| eval-citation-verifier | `citation_policy`                                 | policy parameters only                                                |
| eval-rubric-judge      | `prompt_text`, `rubric_weights`, `judge_notes.md` | judge_notes (bounded format)                                          |
| eval-cf-auditor        | `cf_rules` + thresholds, validator `feeds`        | thresholds within constitution bounds                                 |
| eval-aggregator        | `rubric_weights`, `objective_dims`, schema tags   | none — pure math                                                      |

Engines must contain **zero task IDs** in code. (Today `grade_checkpoints.py` mentions S2/S4
in comments only — acceptable; the `nav_reproduction` validator moves into the S2 container
or the shared validator library.)

### 2.2 Calculator contract (ground truth)

```
python <calculator> --params params.json --fixtures <dir> --out groundtruth.json
```

- Deterministic (seeded if stochastic), versioned, no network unless declared.
- MUST emit `self_check: {passed: bool, checks: [{name, detail}]}`; dispatcher halts the run
  on failure (existing rule, now contractual).
- Declares its parameter surface in a sidecar `calculator.yaml` (name, params schema,
  outputs schema) so spec-lint can validate `gt_recipe` bindings statically.

**Calculator library**: promote the six task-named scripts into parameterized family
calculators — `bond_analytics` (any fixed-coupon bond), `brinson_single_period` (any
sector attribution fixture), `linear_factor_shock`, `periodic_momentum_backtest`,
`table_metrics_verify` (generalizes fund_metrics), `portfolio_diagnostics` (generalizes
client_metrics). New calculators enter the library, not containers, whenever reusable.

### 2.3 Validator plugin contract

```
validate(normalized, groundtruth, fixtures) -> {"result": pass|fail|NA, "evidence": str, "feeds": "D2"|"CF3"|...}
```

- Ships inside the container with its own self-test (runs during container selfcheck).
- Sandboxed execution (no network, fixtures-scoped filesystem).
- `feeds` declares the single dimension/CF the verdict feeds — the aggregator/cf-auditor
  consume it mechanically; a plugin cannot invent new score paths.

### 2.4 Checkpoint-type registry

The grader's closed type vocabulary (today: `number/pct/bp/yr/present/count_eq/count_min/
sum_to/vector/set_match/reconcile/consistency/monotonic/sample_verify`) is the **only**
type surface containers may use. Adding a type is an engine release with tests, never a
per-task fork.

### 2.5 The extension ladder (the customization/compatibility balance)

Order of preference when a task needs something the engines don't do:

1. **Declarative config** — schema fields, weights, policies, calculator params.
2. **Bounded prose slots** — `judge_notes.md`, `extraction_notes.md` (linted formats).
3. **Contracted plugins** — validators, task-local GT scripts (sandboxed, self-tested).
4. **Engine release** — new checkpoint type or shared calculator (benefits every task).

**Promotion rule**: any capability appearing in ≥2 containers at level 3 must be promoted to
level 4 (library/registry). Spec-lint warns when a container plugin duplicates an existing
engine capability. This is the mechanism that keeps engines general while letting individual
tasks be arbitrarily specific.

---

## 3. Layer 3 — Task spec containers

### 3.1 Directory layout

```
taskspecs/
├── registry.json                  # the only place the orchestrator looks up tasks
├── S3-bond-analytics/             # migrated S1-S8 become the reference containers
│   ├── spec.yaml
│   ├── checkpoint_schema.yaml
│   ├── gt_recipe.yaml
│   ├── fixtures/                  # input data (versioned with the container)
│   ├── groundtruth_local/         # optional task-local GT script (calculator contract)
│   ├── validators/                # optional plugin validators (validator contract)
│   ├── judge_notes.md             # blind-judge deduction anchors (bounded format)
│   ├── extraction_notes.md        # optional extractor hints
│   ├── provenance.json            # versions, review status, hash
│   └── selfcheck/                 # lint report, GT self-check output
└── <new-task-id>/
```

### 3.2 Container manifest — every artifact, its consumer, its lint rules

| Artifact                 | Required          | Consumed by                          | Contents / rules                                                                                                                                                                                                                                                                                                                  |
| ------------------------ | ----------------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `spec.yaml`              | yes               | orchestrator, aggregator, judge      | `task_id`, `version`, `family`, `tier` (T1/T2/T3), `persona`, `prompt_text` (逐字), `measurable_outputs`, `rubric_weights`, `objective_dims`, `citation_policy`, `cf_rules` + thresholds, `required_inputs` + NA policy, `engine_requirements` (min engine versions, required checkpoint types, required calculators)               |
| `checkpoint_schema.yaml` | yes               | extractor, grader, aggregator        | fields typed ONLY from the engine's checkpoint-type registry; `tol`/`rel`; `grounding`/`methodology` tags; optional `validator:` dispatch to a plugin                                                                                                                                                                             |
| `gt_recipe.yaml`         | T1/T2             | groundtruth dispatcher               | one of: (a) `calculator:` binding to a shared library calculator + parameter block; (b) `local:` pointing into `groundtruth_local/` (must implement calculator contract incl. self-check); (c) `inputs_only:` declaring user-supplied reference data. T3 may omit                                                                 |
| `fixtures/`              | if self-contained | groundtruth, validators              | data files; hashes recorded in provenance                                                                                                                                                                                                                                                                                         |
| `validators/`            | optional          | grader / cf-auditor                  | plugins per §2.3 contract; each declares `feeds:` (which dim or CF its verdict feeds) and ships a self-test                                                                                                                                                                                                                       |
| `judge_notes.md`         | yes               | rubric-judge (part of blind payload) | per-dimension list of `situation → severity` anchors; D1 section states the expected source-tier expectations for this family (e.g. "bond pricing: tier 1 = exchange/CCDC official quotes, QuantLib"); D6 section states the family's key computation steps. **Lint: no expected numeric results, no weights, no new severities** |
| `extraction_notes.md`    | optional          | extractor                            | format/unit conventions, structured payload shapes. Prose only; cannot alter schema                                                                                                                                                                                                                                               |
| `provenance.json`        | yes               | governance                           | origin, author, methodology notes, engine versions, review status, container content hash                                                                                                                                                                                                                                        |
| `selfcheck/`             | yes               | governance                           | outputs of spec-lint, GT self-check; a container is registrable only when all green                                                                                                                                                                                                                                               |

### 3.3 Registry and lifecycle

`registry.json`: `{task_id, title, family, tier, version, status, container_hash, path}` per entry.

Lifecycle: `draft` → (spec-lint + GT self-check pass) → `review` → human
approval → **`frozen`** → optionally `deprecated`. **The orchestrator grades only `frozen`
containers and pins `container_hash` into every run directory.** Any amendment produces a new
version; scores are comparable only within one container version (report must disclose,
extending the existing scored_weight disclosure pattern).

**Anti-gaming rule (hard)**: a container must reach `frozen` **before** any candidate work for
that task is accepted. Whoever authors a container never sees candidate works. This kills the
circularity where a spec could be fitted to (or against) a particular submission.

### 3.4 Tier coherence rules (enforced by spec-lint)

`tier` states how verifiable the task is; lint enforces internal coherence so a container
can't claim determinism it doesn't have:

> **Superseded twice — this is the design draft, not the live rule.** Migration self-check row 10
> (below) reconciled it against S1-S8; then on 2026-08-06 the T2 `objective_dims` count limit was
> dropped entirely. Live rule: `spec_lint.py` § tier coherence, mirrored in `taskspecs/README.md`
> § tier and constitution §5.

- **T1 确定性**: `gt_recipe` must be executable (a/b) and cover ≥60% of weight-bearing
  checkpoints; `objective_dims ⊇ {D1, D2}` (or D2 alone if `citation_policy: none`).
- **T2 半确定性**: ≥1 objective dim; at least one internal-consistency/reconcile checkpoint;
  unverifiable parts must map to NA policy, not fake numbers.
- **T3 研判型**: `citation_policy ≠ none`; judge-weighted dims (D4/D5) ≥ 40 combined;
  **no `number`-type checkpoints without a GT path** (fake determinism is a lint error).
- All tiers: weights sum to 100; D6 present with weight ≥ 5 (toolchain is always assessable).

---

## 4. Grading flow after the change (delta from today)

Only step 1 of the orchestrator changes materially:

1. **Resolve task**: look up `task_id` in `taskspecs/registry.json`. Require `status: frozen`;
   pin `container_hash` into the run dir. If the task doesn't exist → hard-stop with "author &
   freeze a container first" (design-then-grade in one breath is impossible by construction — a
   human freeze gate sits between authoring and grading).
2. Ground truth: dispatcher executes `gt_recipe` per calculator contract (unchanged behavior,
   now contractual).
3. Per-work loop (extract → checkpoint-grade → citation-verify → blind judge ×2 → CF audit):
   unchanged, except the blind payload adds `judge_notes.md` alongside `tool_evidence`, and
   validators dispatch to container plugins.
4. Aggregate + unified report: unchanged; report gains a header line `container: <id>@<version> (<hash>)`.

---

## 5. Migration path (sequenced, for a later session)

1. Extract constitution: `rubric_and_cf.md` → `rubrics/constitution.md`; update references.
2. Containerize S1-S8 (mechanical split of `tasks.md`; backfill
   provenance as `origin: legacy-v1`).
3. Promote GT scripts → calculator library with `calculator.yaml` sidecars.
4. Build `registry.json` + orchestrator resolution + freeze enforcement.
5. Build spec-lint (`spec_lint.py`) — it encodes §3.2 rules, §3.4 tier coherence, §2.4 type
   registry check, judge_notes format check.

---

## 6. Self-check: blockages & conflicts found, and their resolutions

| #   | Blockage / conflict                                                                                                                    | Resolution baked into the design                                                                                                                                                                                                                           |
| --- | -------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Spec-gaming circularity**: a spec authored after seeing a submission can be fitted for/against it                                    | Freeze-before-work rule; container hash pinned per run; authors never see candidate works                                                                                                                                                                  |
| 2   | **judge_notes as a contamination channel**: notes could carry quasi-GT numbers into the blind payload (leak + error vector)            | Lint bans expected numeric results in judge_notes; format restricted to situation→severity mappings                                                                                                                                                        |
| 3   | **Plugin sprawl re-couples engines to tasks** (the exact disease being cured)                                                          | Extension ladder + promotion rule (≥2 uses → engine library); plugins sandboxed + self-tested; lint flags duplication                                                                                                                                      |
| 4   | **Type-registry gap for a novel checkpoint semantics**                                                                                 | Escape valves in order: compose existing types (consistency/reconcile expressions) → container validator plugin → engine release. Never per-task grader forks                                                                                              |
| 5   | **Version drift breaks comparability**                                                                                                 | Runs pin container version+hash; cross-version comparison flagged in the report (extends existing scored_weight disclosure)                                                                                                                                |
| 6   | **Authored weights are arbitrary/gameable**                                                                                            | Tier-banded weight rules in lint; human freeze gate reviews them                                                                                                                                                                                          |
| 7   | **Tasks with unavailable reference data**                                                                                              | `required_inputs` + NA policy rides the existing NA/denominator machinery unchanged — verified compatible with 扣分制 (NA never deducts)                                                                                                                      |
| 8   | **Too-loose tolerances pass everything silently**                                                                                      | spec-lint enforces that numeric checkpoints must have a truth path (calculator/internal_consistency); tolerances are declared in checkpoint_schema and auditable                                                                                           |
| 9   | **D6 deduction anchors are generic but "what counts as a key computation step" is family-specific**                                    | Constitution keeps the generic anchors (计算无执行轨迹, 工具链覆盖不完整, etc.); container judge_notes declares family-specific key computation steps. D1 source-tier grading (tier 1/2/3) is family-parameterized via D1 anchors in judge_notes                    |

### Migration self-check (2026-07-09) — blockages surfaced while building, and resolutions

| #   | Blockage / conflict surfaced during migration                                                                                                                                                                                                                                                                                                                     | Resolution                                                                                                                                                                                                                                                                                                                                    |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 10  | **Design's tier rules contradicted the reference tasks.** Draft §3.4 said T2 needs an internal-consistency checkpoint (S5 has none), T2 needs a non-empty objective_dims (S7 is `[]`), T3 needs D4+D5≥40 (S6=35) and citations (S8 is `none`). Enforcing them would have failed 4 of 8 legacy containers — the specs are ground truth, so the *rules* were wrong. | Reconciled to rules all 8 satisfy while still catching real errors: T2 = `≤1 objective dim` + *any* verifiability path (calculator / internal-consistency / sample_verify); T3 = `≤1 objective dim` + non-objective weight ≥40. Encoded in `spec_lint.py`; documented in `taskspecs/README.md` and constitution §5. Lint runs green on all 8. |
| 11  | **The judge_notes leak-guard false-positived on its own prohibition text.** A bare-keyword blocklist flagged the sentence "禁止期望数值…" (which *forbids* answer keys) as if it *were* one. Any bare-word list misfires on text that names the words to ban them.                                                                                                      | Guard now matches answer-key *assertions* (forbidden term adjacent to an actual number), not bare words; and data files no longer restate prohibitions (that rule lives in the constitution). Verified: 0 false positives across 8 containers, still catches "正确答案 = 3.41"-style leaks.                                                       |
| 12  | **`objective_dims ⊆ {D1,D2}` is not just convention — it's structural.** `aggregate.py` only computes objective fractions for D1/D2; a D3+ in objective_dims silently falls back to judges.                                                                                                                                                                       | Promoted to a hard lint rule so misconfiguration errors loudly instead of scoring silently-wrong.                                                                                                                                                                                                                                             |
| 13  | **Freeze-before-work vs. "grade an unknown task now".** A user could ask to grade a task with no container, tempting on-the-fly spec authoring — which reintroduces spec-gaming.                                                                                                                                                                                  | Orchestrator + agent now hard-stop on unknown/non-frozen `task_id` with "author & freeze a container first"; explicitly forbid improvising a rubric. Design-then-grade stays impossible in one pass (human freeze gate between).                                                                                                              |

**Verification performed:** `spec_lint.py` green on all 8 (0 errors); a container→engine integration test resolved S3 from the registry, assembled `taskspec.json`, and ran the **real** `grade_checkpoints.py` + `aggregate.py` — CF5 (`ytm`) fired, D6 rendered as 外部工具链完整度, deduction ledgers flowed to the unified report. `gt_dispatch.py` (the deterministic GT dispatcher, now built — resolves the open question below) exercised all three `kind`s: internal_consistency (S1), user_snapshot (S6), and a **real calculator run** (S4 `brinson.py` via CSV, reconciliation self-check passed); its self_check gate halts on `passed:false` and on a missing block. No residual live references to the deleted monolith files.

**Still LLM-driven (not script-verified), by nature:** container resolution, extraction, citation verification, and the two blind judges are agent steps that follow the documented contracts — they are not unit-testable here. The calculators for S2/S3/S5/S7/S8 were not executed (need QuantLib/pandas + real fixtures); only S4's calculator path was run live. Golden pairs remain `pending`.

**Open questions (need owner decisions, do not block the design):**

- Who staffs the human freeze gate, and the SLA for review.
