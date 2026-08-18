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
const TERMINAL = new Set(['succeeded', 'failed', 'timed_out', 'cancelled']);
const ATTENTION = new Set([
  'approval_required', 'input_required', 'failed', 'timed_out', 'cancelled',
]);
const PAUSED = new Set(['approval_required', 'input_required']);
const RESUME = {
  authorization_granted: 'approval_required', input_provided: 'input_required',
};

function prepareCommandWave(args, briefs, stage) {
  const members = briefs.map((brief) => String(brief.id));
  if (!members.length || new Set(members).size !== members.length) {
    throw new Error(stage + ' wave membership must be non-empty and unique');
  }
  const supplied = args.command_wave;
  const wave = supplied || {
    schema: COMMAND_WAVE_SCHEMA,
    wave_id: String(args.wave_id || stage + ':' + members.join(',')),
    sealed_members: members,
    members: Object.fromEntries(members.map((member) => [member, 'running'])),
    handles: {},
    launches: 0,
    interrupted: false,
    delivered_attention: [],
    ordinary_completion_deliveries: 0,
    receipts: {},
  };
  if (wave.schema !== COMMAND_WAVE_SCHEMA ||
      JSON.stringify(wave.sealed_members) !== JSON.stringify(members)) {
    throw new Error(stage + ' wave cannot resume with changed membership');
  }
  wave.handles ||= {};
  wave.receipts ||= {};
  wave.delivered_attention ||= [];
  for (const brief of briefs) {
    const member = String(brief.id);
    if (!wave.handles[member]) {
      wave.handles[member] = String(
        brief.resume_identity || stage + ':' + wave.wave_id + ':' + member);
      wave.launches += 1;
    }
  }
  return wave;
}

function updateCommandWave(wave, member, state) {
  member = String(member);
  state = String(state);
  if (!(member in wave.members)) throw new Error('unknown command-wave member');
  const events = [];
  if (state in RESUME) {
    if (wave.members[member] === RESUME[state]) wave.members[member] = 'running';
    return events;
  }
  const attentionKey = member + ':' + state;
  if (ATTENTION.has(state) && !wave.delivered_attention.includes(attentionKey)) {
    wave.delivered_attention.push(attentionKey);
    events.push({ schema: 'taskplane.command-wave-event/v1',
      wave_id: wave.wave_id, member, state, attention: true });
  }
  if (TERMINAL.has(state)) wave.members[member] = state;
  else if (!TERMINAL.has(wave.members[member])) wave.members[member] = state;
  if (!wave.ordinary_completion_deliveries &&
      Object.values(wave.members).every((value) => TERMINAL.has(value))) {
    wave.ordinary_completion_deliveries = 1;
    events.push({ schema: 'taskplane.command-wave-event/v1',
      wave_id: wave.wave_id, state: 'wave_completed', attention: false,
      members: { ...wave.members } });
  }
  return events;
}

function receiptState(receipt) {
  const explicit = receipt && (receipt.command_state || receipt.state);
  return TERMINAL.has(explicit) || ATTENTION.has(explicit) ? explicit : 'succeeded';
}

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
  const commandWave = prepareCommandWave(args, briefs, 'execute');
  const events = [];
  for (const event of args.command_events || []) {
    events.push(...updateCommandWave(commandWave, event.member, event.state));
  }
  // One governed build agent per claimed task brief, fanned out with a
  // barrier — tasks in one wave are independent by plan construction.
  const pending = briefs.filter((b) =>
    !TERMINAL.has(commandWave.members[String(b.id)]) &&
    !PAUSED.has(commandWave.members[String(b.id)]));
  const runs = pending.map((b) => () => (async () => {
    const output_contract = b.output_contract || {};
    try {
      const receipt = await agent(b.prompt, {
        label: 'task:' + b.id,
        phase: 'Build',
        schema: output_contract.output_schema || RECEIPT_SCHEMA,
        outputContract: output_contract,
        resumeKey: commandWave.handles[String(b.id)],
      });
      return { member: String(b.id), receipt, state: receiptState(receipt) };
    } catch (error) {
      return { member: String(b.id), receipt: null, state: 'failed',
        error: String(error && error.message || error) };
    }
  })());
  const results = await parallel(runs);

  phase('Collect');
  // Workers have submitted via the tp CLI under their contracts; the
  // return value hands the driver the schema-validated receipts only —
  // the harness still owns every state transition.
  for (const result of results.filter(Boolean)) {
    if (result.receipt) commandWave.receipts[result.member] = result.receipt;
    events.push(...updateCommandWave(commandWave, result.member, result.state));
  }
  return { receipts: commandWave.sealed_members
      .map((member) => commandWave.receipts[member]).filter(Boolean),
    command_wave: commandWave, command_events: events };
}
