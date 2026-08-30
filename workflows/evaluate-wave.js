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
    handles: {}, launches: 0, interrupted: false,
    delivered_attention: [], ordinary_completion_deliveries: 0, receipts: {},
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

export default async function evaluateWave({ args, agent, parallel, phase }) {
  phase('Evaluate');
  const briefs = args.briefs || [];
  const commandWave = prepareCommandWave(args, briefs, 'evaluate');
  const events = [];
  for (const event of args.command_events || []) {
    events.push(...updateCommandWave(commandWave, event.member, event.state));
  }
  // One governed read-only evaluator per built-task brief, fanned out
  // with a barrier — evaluations are independent by construction.
  const pending = briefs.filter((b) =>
    !TERMINAL.has(commandWave.members[String(b.id)]) &&
    !PAUSED.has(commandWave.members[String(b.id)]));
  const runs = pending.map((b) => () => (async () => {
    const output_contract = b.output_contract || {};
    const output_schema = output_contract.output_schema;
    if (!output_schema || typeof output_schema !== 'object' ||
        output_schema['$id'] !== 'taskplane.evaluator-output/v2' ||
        output_schema.additionalProperties !== false) {
      throw new Error('evaluate brief lacks the canonical evaluator schema');
    }
    const resume_identity = b.resume_identity;
    const max_attempts = b.max_attempts;
    if (typeof resume_identity !== 'string' || !resume_identity ||
        !Number.isInteger(max_attempts) || max_attempts < 1 ||
        max_attempts !== output_contract.max_attempts) {
      throw new Error('evaluate brief lacks canonical retry identity');
    }
    try {
      const receipt = await agent(b.prompt, {
        label: 'eval:' + b.id, phase: 'Evaluate', schema: output_schema,
        outputContract: output_contract,
        resumeKey: commandWave.handles[String(b.id)] || resume_identity,
        maxAttempts: max_attempts,
      });
      return { member: String(b.id), receipt, state: receiptState(receipt) };
    } catch (error) {
      return { member: String(b.id), receipt: null, state: 'failed',
        error: String(error && error.message || error) };
    }
  })());
  const results = await parallel(runs);

  phase('Collect');
  // Evidence already sits on disk, written by the evaluators under their
  // read-only contracts; the return value hands the driver the
  // schema-validated verdicts only — the harness still owns every state
  // transition.
  for (const result of results.filter(Boolean)) {
    if (result.receipt) commandWave.receipts[result.member] = result.receipt;
    events.push(...updateCommandWave(commandWave, result.member, result.state));
  }
  return { receipts: commandWave.sealed_members
      .map((member) => commandWave.receipts[member]).filter(Boolean),
    command_wave: commandWave, command_events: events };
}
