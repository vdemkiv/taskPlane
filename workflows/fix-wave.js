// taskplane fix-wave — the FIX stage as a Claude Dynamic Workflow
// (journaled, resumable: a stopped run re-uses completed agents' cached
// results instead of re-running them).
//
// args IS the stage dispatch payload emitted by tp.py (contract:
// wave-workflow): { verdicts: [brief...] }, one brief per FAILED task
// { id, worktree, prompt, ... } with the evaluator's repro notes carried
// inside the prompt. Each brief's prompt is used VERBATIM — the same
// text the Task-dispatch path uses, including the per-task
// export TASKPLANE_TASK=<slot> activation and the claim/submit/CLEAR
// protocol — so the two paths behave identically by construction. The
// workflow is transport only: fixers work under the same contracts, the
// PreToolUse screen fires inside workflow agents unchanged, and workers
// submit evidence WITHOUT ever advancing loop state — every human
// decision happens at conversation level AFTER this run returns, never
// inside it. The schema below is the belt on top: a violation retries
// instead of returning an invalid receipt shape.
//
// Deterministic by design: no clock, no randomness, no dynamic loading.

export const meta = {
  name: 'fix-wave',
  description: 'Run the FIX wave for failed tasks as a resumable workflow',
  phases: [{ title: 'Fix' }, { title: 'Collect' }],
};

// contract:wave-workflow — the schema-pinned submission receipt per fix
// agent: receipts[{task, outcome, note}] (byte-identical to
// execute-wave.js's pin: one shape, zero drift).
const RECEIPT_SCHEMA = {
  '$schema': 'https://json-schema.org/draft/2020-12/schema',
  '$id': 'taskplane.fix-receipt/v1',
  type: 'object',
  additionalProperties: false,
  required: ['task', 'outcome', 'note'],
  properties: {
    task: { type: 'string' },
    outcome: { type: 'string' },
    note: { type: 'string' },
  },
};
const SETTINGS_DIGEST = /^[0-9a-f]{64}$/;

function requireSettings(args) {
  const digest = args && args.settings_digest;
  if (typeof digest !== 'string' || !SETTINGS_DIGEST.test(digest)) {
    throw new Error('fix workflow lacks canonical settings digest');
  }
  return { digest };
}

export default async function fixWave({ args, agent, parallel, phase }) {
  const settings = requireSettings(args);
  phase('Fix');
  const verdicts = args.verdicts || [];
  // One governed fix agent per failed-task verdict brief, fanned out
  // with a barrier — fixes stay inside their own task scopes, so they
  // are independent by plan construction.
  const runs = verdicts.map((v) => () => {
    const output_contract = v.output_contract || {};
    return agent(v.prompt, {
      label: 'fix:' + v.id,
      phase: 'Fix',
      schema: output_contract.output_schema || RECEIPT_SCHEMA,
      outputContract: output_contract,
    });
  });
  const results = await parallel(runs);

  phase('Collect');
  // Fixers have submitted via the tp CLI under their contracts; the
  // return value hands the driver the schema-validated receipts only —
  // the harness still owns every state transition.
  return {
    receipts: results.filter(Boolean),
    settings_digest: settings.digest,
  };
}
