# Inactive specification package adapters

`spec-phase-definitions.json` is an explicitly selected candidate definition
set for the existing settings registry. It is not loaded by the active
lifecycle. The installed 2.19.0 routing and pinned skill bytes stay unchanged;
HG-E/T15C owns any later selection. The incumbent registry remains the sole
definition and DAG authority for an attempt.

Product emits a requirement candidate. Design consumes that requirement and
emits its Design contract and sealed test strategy. Plan consumes those exact
artifacts and emits a Plan task carrying the existing Design/Plan quality
authority. Build consumes that task plus the preserved Design artifacts.
The later phase declarations retain their stage boundaries; this file does
not claim their adapters or cutover are complete.

The orchestrator passes one sealed package to the shared runtime. Host adapters
use `store_spec_phase_outputs` for declared candidate documents and
`seal_phase_plan_task` for the Plan candidate. Those functions do not evaluate,
gate, dispatch other workers, progress a stage, or apply knowledge updates.
The agent has no lifecycle API and receives no predecessor workspace,
conversation, event log, or mutable store. Knowledge remains a fingerprinted
runtime input; proposals require their declared schema and existing governance.

After actual runtime collection, the loop owner invokes
`produce_phase_handoff` with that exact dispatch and result. The complete v2
writer preserves every collected output and inherited artifact and retains
the predecessor handoff as immutable evidence. Fresh consumers use
`consume_phase_handoff` under separately supplied run, candidate, registry and
authority bindings. Plan and Build use the package-aware quality authority
functions; the legacy filesystem route is retained for existing attempts.

Local tests simulate host authorship and already granted authority. They
exercise the real registry, nonce, runtime collection, package and quality
producers. They do not satisfy J0, J1, J6, native-host completion or the complete
W01-W34 evidence obligations. Substantive evaluation and human gates remain
the orchestrator's responsibility.
