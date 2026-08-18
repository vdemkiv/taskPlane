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

export default async function reviewWave({ args, agent, parallel, phase }) {
  phase('Lenses');
  const slots = args.slots || [];
  const lensRuns = slots.map((b) => () =>
    agent(b.prompt, {
      label: 'lens:' + b.slot_id,
      phase: 'Lenses',
      schema: b.result_schema,
      resumeKey: b.resume_identity,
      resultPath: b.result_path,
      lease: b.lease,
      maxAttempts: b.max_attempts || 2,
    }));
  const results = await parallel(lensRuns);

  phase('Merge');
  // Canonical collection consumes the sealed, validated result files. Host
  // receipts are attribution telemetry; the workflow never remaps findings.
  return { receipts: results.filter(Boolean) };
}
