// taskplane evaluate-wave — the EVALUATE stage as a Claude Dynamic
// Workflow (journaled, resumable: a stopped run re-uses completed agents'
// cached results instead of re-running them).
//
// args IS the stage dispatch payload emitted by tp.py (contract:
// wave-workflow): { briefs: [brief...] }, one READ-ONLY evaluation brief
// per built task { id, worktree, prompt, ... }. Each brief's prompt is
// used VERBATIM — the same text the Task-dispatch path uses, including
// the per-task export TASKPLANE_TASK=<slot> activation and the
// claim/submit/CLEAR protocol — so the two paths behave identically by
// construction. Evaluators write their evidence under their read-only
// contracts (hooks still fire inside workflow agents) and NEVER advance
// loop state — every human decision happens at conversation level AFTER
// this run returns, never inside it. The schema below is the belt on
// top: a violation retries instead of returning an invalid findings
// shape.
//
// Deterministic by design: no clock, no randomness, no dynamic loading.

export const meta = {
  name: 'evaluate-wave',
  description: 'Run the read-only EVALUATE wave as a resumable workflow',
  phases: [{ title: 'Evaluate' }, { title: 'Collect' }],
};

// contract:findings-v2 — the schema-pinned findings shape per evaluation
// agent (byte-identical to review-wave.js's pin: one shape, zero drift).
const FINDINGS_SCHEMA = {
  type: 'object',
  required: ['lens', 'findings'],
  properties: {
    lens: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'class', 'file', 'line', 'title',
                   'scenario', 'fix'],
        properties: {
          severity: { type: 'string' },
          class: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'number' },
          title: { type: 'string' },
          scenario: { type: 'string' },
          fix: { type: 'string' },
        },
      },
    },
    clean: { type: 'array', items: { type: 'string' } },
  },
};

export default async function evaluateWave({ args, agent, parallel, phase }) {
  phase('Evaluate');
  const briefs = args.briefs || [];
  // One governed read-only evaluator per built-task brief, fanned out
  // with a barrier — evaluations are independent by construction.
  const runs = briefs.map((b) => () =>
    agent(b.prompt, {
      label: 'eval:' + b.id,
      phase: 'Evaluate',
      schema: FINDINGS_SCHEMA,
    }));
  const results = await parallel(runs);

  phase('Collect');
  // Evidence already sits on disk, written by the evaluators under their
  // read-only contracts; the return value hands the driver the
  // schema-validated verdicts only — the harness still owns every state
  // transition.
  return {
    verdicts: results.filter(Boolean),
  };
}
