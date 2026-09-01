// taskplane review-wave — the routed lens review wave as a Claude Dynamic
// Workflow (journaled, resumable: a stopped run re-uses completed lenses'
// cached agent results instead of re-running them).
//
// args IS the canonical ReviewKernel manifest: { slots: [brief...] }.
// Each brief's prompt is used VERBATIM — the same
// text the Task-dispatch path uses, including the per-task slot activation
// (`export TASKPLANE_TASK=lens-<id>`) and the CLEAR_ALWAYS finally-block —
// so the two paths produce byte-equivalent artifacts by construction. The
// agents themselves write `.em-review/lens-<id>/findings.json` under their
// read-only contracts (hooks still fire inside workflow agents); the schema
// below is the belt on top: a violation retries instead of returning an
// invalid findings shape.
//
// Deterministic by design: no clock, no randomness, no dynamic loading.
// Host parity is deliberate too: workflow completion is transport telemetry,
// never review truth and never human consent. Canonical DoR, criteria, slots,
// findings, validation, provenance, revision, and gate state stay in the
// ReviewKernel. The same revision is later projected to lossless JSON/MD/HTML
// and <=14 KB inline pages on Claude and Codex. This workflow must not merge,
// summarize, render, or infer a user-declined state from an absent host event.

export const meta = {
  name: 'review-wave',
  description: 'Run the routed lens review wave as a resumable workflow',
  phases: [{ title: 'Lenses' }, { title: 'Merge' }],
};
const SETTINGS_DIGEST = /^[0-9a-f]{64}$/;

function requireSettings(args) {
  const digest = args && args.settings_digest;
  if (typeof digest !== 'string' || !SETTINGS_DIGEST.test(digest)) {
    throw new Error('review workflow lacks canonical settings digest');
  }
  return { digest };
}

function requireSlots(args) {
  if (!args || !Array.isArray(args.slots)) {
    throw new Error('review workflow lacks canonical slots');
  }
  return args.slots.map((slot, index) => {
    if (!slot || typeof slot !== 'object' ||
        typeof slot.slot_id !== 'string' || !slot.slot_id ||
        typeof slot.prompt !== 'string' || !slot.prompt ||
        !slot.result_schema || typeof slot.result_schema !== 'object' ||
        typeof slot.resume_identity !== 'string' || !slot.resume_identity ||
        typeof slot.result_path !== 'string' || !slot.result_path ||
        !slot.lease || typeof slot.lease !== 'object' ||
        !Number.isInteger(slot.max_attempts) || slot.max_attempts < 1 ||
        typeof slot.task_name !== 'string' || !slot.task_name ||
        typeof slot.agent !== 'string' || !slot.agent ||
        typeof slot.role_marker !== 'string' || !slot.role_marker ||
        (slot.model !== null &&
          (typeof slot.model !== 'string' || !slot.model)) ||
        typeof slot.model_tier !== 'string' || !slot.model_tier ||
        typeof slot.reasoning_effort !== 'string' || !slot.reasoning_effort) {
      throw new Error('review workflow slot ' + index +
        ' lacks its governed execution contract');
    }
    return slot;
  });
}

export default async function reviewWave({ args, agent, parallel, phase }) {
  const settings = requireSettings(args);
  const slots = requireSlots(args);
  phase('Lenses');
  const lensRuns = slots.map((b) => () =>
    agent(b.prompt, {
      label: 'lens:' + b.slot_id,
      phase: 'Lenses',
      schema: b.result_schema,
      resumeKey: b.resume_identity,
      resultPath: b.result_path,
      lease: b.lease,
      maxAttempts: b.max_attempts,
      taskName: b.task_name,
      agent: b.agent,
      roleMarker: b.role_marker,
      model: b.model,
      modelTier: b.model_tier,
      reasoningEffort: b.reasoning_effort,
    }));
  const results = await parallel(lensRuns);

  phase('Merge');
  // Canonical collection consumes the sealed, validated result files. Host
  // receipts are attribution telemetry; the workflow never remaps findings.
  return { receipts: results.filter(Boolean),
    settings_digest: settings.digest };
}
