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

export default async function evaluateWave({ args, agent, parallel, phase }) {
  phase('Evaluate');
  const briefs = args.briefs || [];
  // One governed read-only evaluator per built-task brief, fanned out
  // with a barrier — evaluations are independent by construction.
  const runs = briefs.map((b) => () => {
    const output_contract = b.output_contract || {};
    const output_schema = b.output_schema || output_contract.output_schema;
    if (!output_schema || typeof output_schema !== 'object' ||
        output_schema['$id'] !== 'taskplane.evaluator-output/v1' ||
        output_schema.additionalProperties !== false) {
      throw new Error('evaluate brief lacks the canonical evaluator schema');
    }
    const resume_identity = b.resume_identity;
    const max_attempts = b.max_attempts || output_contract.max_attempts || 2;
    return agent(b.prompt, {
      label: 'eval:' + b.id,
      phase: 'Evaluate',
      schema: output_schema,
      outputContract: output_contract,
      resumeKey: resume_identity,
      maxAttempts: max_attempts,
    });
  });
  const results = await parallel(runs);

  phase('Collect');
  // Evidence already sits on disk, written by the evaluators under their
  // read-only contracts; the return value hands the driver the
  // schema-validated verdicts only — the harness still owns every state
  // transition.
  return { receipts: results.filter(Boolean) };
}
