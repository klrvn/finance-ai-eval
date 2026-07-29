export const meta = {
  name: 'eval-judge-island',
  description: 'Enforced BLIND-SCORING ISLAND for AlphaMind eval-judge. The main conversation runs intake / spec / ground-truth / read / extract / CF / scorecard / report itself (loading the eval-* skills directly, no subagents). This workflow enforces ONLY the isolation-critical core, per work: deterministic checkpoint grading, citation+purity audit, TWO blind rubric judges on a ground-truth-free payload, and persistence of the two ledgers. Blind-judge isolation is guaranteed STRUCTURALLY — this code never places ground truth / checkpoint results / citation results into the judge payload; the judges run as fresh subagents that see only the rubric payload.',
  whenToUse: 'Invoked by commands/eval-judge.md AFTER the main conversation has built groundtruth.json and each work\'s normalized.json, to run grade -> cite/purity -> blind judge x2 -> persist under engine enforcement. Not a full pipeline — read/extract/CF/scorecard/report live in the main conversation.',
  phases: [
    { title: 'Grade+Cite', detail: 'deterministic checkpoints + citation/purity audit, per work' },
    { title: 'Blind Judge', detail: 'two ground-truth-free blind rubric judges per work' },
    { title: 'Persist', detail: 'commit the two judge ledgers to disk' },
  ],
}

// ============================================================================
// ARGS CONTRACT  (pass as Workflow `args`)
//   {
//     taskId:     'S9',
//     repoRoot:   '<CLAUDE_PLUGIN_ROOT>',    // cwd for the deterministic scripts
//     python:     'python',                   // interpreter (absolute path if PATH lacks one)
//     gtPath:     '<runDir>/groundtruth.json',        // built by the main conversation
//     schemaPath: '<runDir>/checkpoint_schema.json',  // flat checkpoint map, built by the main conversation
//     spec: {                                 // spec fields the main conversation already loaded
//       objectiveScoringMode: 'binary' | 'deviation',
//       citationPolicy:       'none' | 'full' | 'sample',
//       promptText:           '<verbatim candidate prompt>',
//       rubricWeights:        { D1:.., D2:.., ... },
//       judgeNotes:           '<verbatim judge_notes.md, leak-guarded by lint>'
//     },
//     works: [                                // one per blind label; normalized.json ALREADY written by main
//       { label:'Work A', wd:'<runDir>/Work_A', workText:'<full blind work text>', toolEvidence:'<work-own trace, or "">' },
//       ...
//     ]
//   }
//
// ISOLATION CONTRACT (why this is the only enforced island):
//   - The main conversation holds ground truth + checkpoint results in its context, so building the
//     judge payload THERE would reduce isolation to a promise. Here, the judge payload is assembled by
//     CODE from `spec.promptText / rubricWeights / judgeNotes`, this work's `workText`, and the
//     purity-checked `toolEvidence` ONLY. gtPath / det_results / citation_audit are read for grading
//     but are NEVER referenced in the judge prompt. That is the structural guarantee.
//   - grade (W2) reads gtPath; the two blind judges (W4) do not. Same script, disjoint variable sets.
// ============================================================================

const A = args || {}
const taskId = A.taskId
const repoRoot = A.repoRoot
const PY = A.python || 'python'
const gtPath = A.gtPath
const schemaPath = A.schemaPath
const spec = A.spec || {}
const works = Array.isArray(A.works) ? A.works : []

function hardStop(phase, reason, detail) {
  log(`HARD-STOP [${phase}]: ${reason}`)
  return { status: 'hard_stop', phase, reason, detail: detail || null, taskId }
}

// ---- schemas for structured hand-offs (validated at the tool layer) ----
const SCRIPT_RESULT = {
  type: 'object',
  additionalProperties: false,
  required: ['ok', 'exitCode', 'outPath', 'selfCheckPassed', 'summary'],
  properties: {
    ok: { type: 'boolean' },
    exitCode: { type: 'integer' },
    outPath: { type: 'string' },
    selfCheckPassed: { type: 'boolean', description: 'true if the artifact reports self_check.passed OR the step has no self-check' },
    summary: { type: 'string', description: 'one line: key fields parsed back from the artifact, or the stderr tail on failure' },
  },
}
const JUDGE_RESULT = {
  type: 'object', additionalProperties: false,
  required: ['dimensions', 'notes'],
  properties: {
    dimensions: {
      type: 'object',
      description: 'D1..D6 -> deduction ledger',
      additionalProperties: {
        type: 'object', additionalProperties: false,
        required: ['level', 'deductions'],
        properties: {
          level: { type: 'number' },
          deductions: {
            type: 'array',
            items: {
              type: 'object', additionalProperties: false,
              required: ['issue', 'severity', 'points', 'evidence'],
              properties: {
                issue: { type: 'string' },
                severity: { type: 'string', enum: ['minor', 'major', 'severe'] },
                points: { type: 'number' },
                evidence: { type: 'string' },
              },
            },
          },
        },
      },
    },
    notes: { type: 'string' },
  },
}
const OK_ONLY = { type: 'object', additionalProperties: false, required: ['ok'], properties: { ok: { type: 'boolean' } } }

// ---- helper: run a deterministic python leaf node inside a thin shell-runner subagent ----
// The script sandbox has no shell/fs, so a subagent runs the exact command. Determinism comes
// from the python script, not the agent; the agent only executes and reports back.
function runScript(label, phase, cmd, outPath, hasSelfCheck, outIsJson = true) {
  const prompt = [
    `You are a non-interactive shell runner. From the directory:`,
    `  ${repoRoot}`,
    `run EXACTLY this command, unmodified:`,
    ``,
    `  ${cmd}`,
    ``,
    `Rules:`,
    `- Do not edit, reinterpret, or "fix" the command. Run it once.`,
    `- Capture the process exit code.`,
    outIsJson
      ? `- If the expected output file exists, read it and parse it as JSON:\n    ${outPath}`
      : `- Confirm the expected output file exists and is non-empty. It is NOT JSON — do NOT parse it as JSON:\n    ${outPath}`,
    hasSelfCheck
      ? `- Set selfCheckPassed to the artifact's self_check.passed boolean (false if absent).`
      : `- This step has no self-check; set selfCheckPassed to true when exitCode is 0, false otherwise.`,
    outIsJson
      ? `- ok = (exitCode === 0 AND the output file parsed as valid JSON).`
      : `- ok = (exitCode === 0 AND the output file exists and is non-empty).`,
    `- summary = one line of the key fields parsed back (or the first heading for a text file), or the last line of stderr on failure.`,
    `Return the structured object only. Never fabricate a success you did not observe.`,
  ].join('\n')
  return agent(prompt, { label, phase, schema: SCRIPT_RESULT })
}

// ---- helper: reshape a JUDGE_RESULT into the judge_N.json shape aggregate.py expects ----
function toJudgeFile(j) {
  const levels = {}
  const deductions = {}
  for (const d of Object.keys(j.dimensions || {})) {
    levels[d] = j.dimensions[d].level
    deductions[d] = j.dimensions[d].deductions || []
  }
  return { levels, deductions, rationale: {}, evidence: {}, notes: j.notes || '' }
}

// ---- helper: persist BOTH blind ledgers in ONE writer subagent (sandbox has no fs) ----
function writeJudges(label, phase, wd, j1, j2) {
  const prompt = [
    `Write TWO UTF-8 JSON files. Create parent dirs if needed; overwrite if present.`,
    `Each file's content must be EXACTLY the given JSON, byte for byte — do not reformat,`,
    `re-key, pretty-print differently, or add anything.`,
    ``,
    `FILE 1 path: ${wd}/judge_1.json`,
    `<<<JSON1`,
    JSON.stringify(toJudgeFile(j1)),
    `JSON1`,
    ``,
    `FILE 2 path: ${wd}/judge_2.json`,
    `<<<JSON2`,
    JSON.stringify(toJudgeFile(j2)),
    `JSON2`,
    ``,
    `Then re-read BOTH files and confirm each parses as JSON. Return ok=true only if both do.`,
  ].join('\n')
  return agent(prompt, { label, phase, schema: OK_ONLY })
}

// ============================================================================
// INTAKE — validate the island args (registry / GT / normalized already produced by main)
// ============================================================================
phase('Grade+Cite')
if (!taskId || !repoRoot || !gtPath || !schemaPath) {
  return hardStop('Intake', 'missing required args (taskId/repoRoot/gtPath/schemaPath)')
}
if (works.length === 0) {
  return hardStop('Intake', 'no works passed to the island')
}
log(`Island for task ${taskId}: ${works.length} work(s) — ${works.map((w) => w.label).join(', ')}`)

// ============================================================================
// PER-WORK ISLAND (parallel across works; ordered within each work)
//   grade (det) ∥ cite/purity  ->  blind judge x2 (GT-free)  ->  persist
// ============================================================================
async function processWork(w) {
  const wd = w.wd
  const detPath = `${wd}/det_results.json`
  const mode = spec.objectiveScoringMode === 'deviation' ? '--scoring-mode deviation' : ''
  const gradeCmd = `${PY} skills/eval-checkpoint-grader/scripts/grade_checkpoints.py --schema ${schemaPath} --normalized ${wd}/normalized.json --groundtruth ${gtPath} --out ${detPath} ${mode}`.replace(/\s+/g, ' ').trim()

  // W2 grade (deterministic, reads GT) and W3 cite/purity (web, reads normalized) are independent
  // -> run together. NEITHER output is placed in the judge payload below.
  const citePrompt = [
    `Follow skills/eval-citation-verifier/SKILL.md for blind label ${w.label}.`,
    `Verify the citations extracted in ${wd}/normalized.json against the web.`,
    `Write ${wd}/citation_audit.json (verified / unsupported / broken / fabricated-candidate).`,
    `Remember: unverifiable != fabricated. Then run the payload purity check: every factual`,
    `claim in the work's tool_evidence must trace to THIS work's text; drop unmatched claims;`,
    `write ${wd}/payload_audit.json with verified_claims / dropped_claims / isolation_confirmed.`,
    `Return the purity-checked tool_evidence as your text output.`,
  ].join('\n')
  const [det, citeOut] = await parallel([
    () => runScript(`grade:${w.label}`, 'Grade+Cite', gradeCmd, detPath, false),
    () => (spec.citationPolicy && spec.citationPolicy !== 'none')
      ? agent(citePrompt, { label: `cite:${w.label}`, phase: 'Grade+Cite' })
      : (w.toolEvidence || ''),
  ])
  if (!det || !det.ok) throw new Error(`grade failed for ${w.label}: ${det && det.summary}`)
  const toolEvidence = (typeof citeOut === 'string' && citeOut.trim()) ? citeOut : (w.toolEvidence || '')

  // W4 BLIND JUDGE x2 — fresh subagents, GT-FREE payload. Isolation is STRUCTURAL: this array
  //    never contains gtPath / det_results / citation_audit — only rubric, work text, purity-checked evidence.
  const judgePayload = [
    `You are an independent blind rubric judge. Follow skills/eval-rubric-judge/SKILL.md.`,
    `You are scoring ONE work you have never seen, with NO access to ground truth or checkpoint`,
    `results — score only what the rubric asks, from the work itself.`,
    `task_id: ${taskId}`,
    `plugin_root: ${repoRoot}`,
    `prompt given to the candidate:`,
    spec.promptText || '',
    `rubric_weights: ${JSON.stringify(spec.rubricWeights || {})}`,
    `judge_notes:`,
    spec.judgeNotes || '',
    `tool_evidence (this work's own trace, purity-checked):`,
    toolEvidence || '(none)',
    `Deduction scoring: each of D1..D6 starts at 4; register {issue,severity,points,evidence};`,
    `minor -0.5 / major -1 / severe -2; no flag = no deduction; level = max(0, 4 - sum).`,
    `--- WORK TEXT ---`,
    w.workText || '',
  ].join('\n')
  const [j1, j2] = await parallel([
    () => agent(judgePayload, { label: `judge1:${w.label}`, phase: 'Blind Judge', schema: JUDGE_RESULT }),
    () => agent(judgePayload + '\n(Independent second pass — reason from scratch.)', { label: `judge2:${w.label}`, phase: 'Blind Judge', schema: JUDGE_RESULT }),
  ])
  if (!j1 || !j2) throw new Error(`blind judging failed for ${w.label}`)
  // divergence gate: >1 level apart on any dimension => needs_review
  let needsReview = false
  for (const d of Object.keys(j1.dimensions)) {
    const a1 = j1.dimensions[d] && j1.dimensions[d].level
    const a2 = j2.dimensions[d] && j2.dimensions[d].level
    if (typeof a1 === 'number' && typeof a2 === 'number' && Math.abs(a1 - a2) > 1) needsReview = true
  }

  // W5 persist both ledgers (one writer, two files) where aggregate.py --bundle expects them.
  const pw = await writeJudges(`persist:${w.label}`, 'Persist', wd, j1, j2)
  if (!pw || !pw.ok) throw new Error(`could not persist judge ledgers for ${w.label}`)

  return { label: w.label, needsReview, detPath, judge1Path: `${wd}/judge_1.json`, judge2Path: `${wd}/judge_2.json` }
}

const results = await parallel(works.map((w) => () => processWork(w)))
const done = results.filter(Boolean)
if (done.length !== works.length) {
  return hardStop('Island', `only ${done.length}/${works.length} works completed the blind-scoring island`, JSON.stringify(results.map((r, i) => (r ? r.label : `${works[i].label}:FAILED`))))
}

const flagged = done.filter((r) => r.needsReview).map((r) => r.label)
log(`Blind-scoring island done for ${done.length} work(s)${flagged.length ? ` (needs_review: ${flagged.join(', ')})` : ''}`)
return {
  status: 'ok',
  taskId,
  works: done.map((r) => ({ label: r.label, needsReview: r.needsReview, detPath: r.detPath, judge1Path: r.judge1Path, judge2Path: r.judge2Path })),
}
