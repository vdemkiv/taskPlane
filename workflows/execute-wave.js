// taskplane execute-wave — the parallel EXECUTE stage as a Claude Dynamic
// Workflow (journaled, resumable: a stopped run re-uses completed agents'
// cached results instead of re-running them).
//
// args IS the stage dispatch payload emitted by tp.py (contract:
// wave-workflow): { briefs: [brief...] }, one brief per claimed wave task
// { id, worktree, prompt, ... }. Each brief's prompt is used VERBATIM —
// the same text the Task-dispatch path uses, including the per-task
// export TASKPLANE_TASK=<slot> activation and the claim/submit/CLEAR
// protocol — so the two paths behave identically by construction. The
// workflow is transport only: builders still claim into .tp-work/<task>
// worktrees via tp loop claim, the PreToolUse contract screen fires
// inside workflow agents unchanged, and workers submit evidence WITHOUT
// ever advancing loop state — every human decision happens at
// conversation level AFTER this run returns, never inside it. The schema
// below is the belt on top: a violation retries instead of returning an
// invalid receipt shape.
//
// Deterministic by design: no clock, no randomness, no dynamic loading.

export const meta = {
  name: 'execute-wave',
  description: 'Run the claimed EXECUTE wave as a resumable workflow',
  phases: [{ title: 'Build' }, { title: 'Collect' }],
};

const COMMAND_WAVE_SCHEMA = 'taskplane.command-wave/v1';

// contract:wave-workflow — the schema-pinned submission receipt per
// build agent: receipts[{task, outcome, note}].
const RECEIPT_SCHEMA = {
  '$schema': 'https://json-schema.org/draft/2020-12/schema',
  '$id': 'taskplane.execute-receipt/v1',
  type: 'object',
  additionalProperties: false,
  required: ['task', 'outcome', 'note'],
  properties: {
    task: { type: 'string' },
    outcome: { type: 'string' },
    note: { type: 'string' },
  },
};

export default async function executeWave({ args, agent, parallel, phase }) {
  phase('Build');
  const briefs = args.briefs || [];
  const commandWave = args.command_wave;
  if (commandWave && commandWave.schema !== COMMAND_WAVE_SCHEMA) {
    throw new Error('execute wave has an unsupported command-wave contract');
  }
  // One governed build agent per claimed task brief, fanned out with a
  // barrier — tasks in one wave are independent by plan construction.
  const runs = briefs.map((b) => () => {
    const output_contract = b.output_contract || {};
    return agent(b.prompt, {
      label: 'task:' + b.id,
      phase: 'Build',
      schema: output_contract.output_schema || RECEIPT_SCHEMA,
      outputContract: output_contract,
    });
  });
  const results = await parallel(runs);

  phase('Collect');
  // Workers have submitted via the tp CLI under their contracts; the
  // return value hands the driver the schema-validated receipts only —
  // the harness still owns every state transition.
  return {
    receipts: results.filter(Boolean),
    command_wave: commandWave || null,
  };
}
