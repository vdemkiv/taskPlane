// taskplane review-wave — the routed lens review wave as a Claude Dynamic
// Workflow (journaled, resumable: a stopped run re-uses completed lenses'
// cached agent results instead of re-running them).
//
// args IS the dispatch payload emitted by `tp lens dispatch --emit workflow`
// (contract:lens-brief): { deep: [brief...], sweep: brief|null,
// routing_decision: {...} }. Each brief's prompt is used VERBATIM — the same
// text the Task-dispatch path uses, including the per-task slot activation
// (`export TASKPLANE_TASK=lens-<id>`) and the CLEAR_ALWAYS finally-block —
// so the two paths produce byte-equivalent artifacts by construction. The
// agents themselves write `.em-review/lens-<id>/findings.json` under their
// read-only contracts (hooks still fire inside workflow agents); the schema
// below is the belt on top: a violation retries instead of returning an
// invalid findings shape.
//
// Deterministic by design: no clock, no randomness, no dynamic loading.

export const meta = {
  name: 'review-wave',
  description: 'Run the routed lens review wave as a resumable workflow',
  phases: [{ title: 'Lenses' }, { title: 'Merge' }],
};

// contract:findings-v2 — the schema-pinned findings shape per lens agent.
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

export default async function reviewWave({ args, agent, parallel, phase }) {
  phase('Lenses');
  const deep = args.deep || [];
  // One governed read-only agent per DEEP brief, fanned out with a barrier.
  const lensRuns = deep.map((b) => () =>
    agent(b.prompt, {
      label: 'lens:' + b.id,
      phase: 'Lenses',
      schema: FINDINGS_SCHEMA,
    }));
  // The SWEEP brief (light lenses batched) rides in the same barrier.
  if (args.sweep) {
    lensRuns.push(() =>
      agent(args.sweep.prompt, {
        label: 'lens:sweep',
        phase: 'Lenses',
        schema: FINDINGS_SCHEMA,
      }));
  }
  const results = await parallel(lensRuns);

  phase('Merge');
  // Artifacts already sit in .em-review/lens-<id>/findings.json (written by
  // the agents under their contracts, identical to the Task-dispatch path);
  // the merged return value hands the driver the schema-validated results
  // plus the full routing decision for the coverage map.
  return {
    per_lens: results.filter(Boolean),
    routing_decision: args.routing_decision,
  };
}
