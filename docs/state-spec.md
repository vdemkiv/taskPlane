# State ownership and backup

Current runtime state is workspace-local under `.taskplane/`. It is not the old
plan-aware `~/.taskplane/projects`, external run tree or shared `.taskplane-kb`
loop. Those earlier layouts are historical, not current recovery instructions.

| Owner/path | Meaning |
| --- | --- |
| `workspace-binding.json` | Explicit selected-project identity and execution policy when binding is required |
| `workflow-<root-key>.json` | Controller index: run/visit/task identities, decisions, attempts, accepted references and history |
| `workflow-<root-key>.initialized.json` and initialization transaction | Authentic ordinary store initialization provenance |
| Verified committed import record and `recovery-v1/` originals | Alternative authentic import initialization provenance; never a fabricated ordinary marker |
| Accepted result files and immutable objects/logs | Concrete phase/check history and relevant evidence references |
| Durable brief/references | Restart context, current work and missing inputs; no attention/delivery receipt |
| `native-parent-<actor-key>.json` | Run-bound host lifecycle parent observations used when native metadata is not yet available; operational identity, never journal data |
| `live-telemetry-<run-key>.json` | Advisory operational aggregates and counter baselines; separate from gates and analysis records |
| `run-record-<key>.json` | Optional summaries written only after a run ends; no gate authority or live consumer |
| `flow-events.jsonl` | Legacy historical analysis input, never an active-flow dependency |
| `knowledge/graph.json`, dashboard and usage snapshots | Reusable source context and generated views; dashboard publication occurs at flow milestones, not ordinary hooks |
| `parent_hook_readiness` / worker `hook_readiness` | Admitted automatic pre/post observation for the current session/run/phase/runtime; not a host-containment certificate |
| `repair_scope` | Existing approved task closure, affected checks and fresh result namespace; unaffected results and decisions remain retained |

The root key identifies the owning conversation/store, not a freely supplied
worker principal. The controller owns ordered Product → Design → Plan → Build →
Evaluate → Engineering → Retro visits and seven separate human decisions. A
retry budget uses the controller's run start time and owned worker identities
with native interval evidence. Optional telemetry baselines and historical
journals cannot supply required budget usage; missing native evidence stays unknown.
A storage-generation counter is compare-and-swap transaction state; changing it
does not make unrelated task inputs stale. Worker attempts retain actual native
identity, relevant dependencies and observed completion/cancellation facts.

Required typed evidence blocks acceptance when missing, failed or unknown and
reports the unmet check IDs. Substantive equivalent wording is accepted. An
explicit scoped repair can re-enter Build after acceptance or completion, using
fresh result paths and retained immutable evidence. Retry only missing, failed or
invalidated tasks and their affected checks; retain all unaffected results,
evidence and approvals, without restarting earlier phases.

The supported Codex/Claude hook paths are the enforcement scope. Their failing
checks are defects; stronger containment when a host never invokes hooks remains
a separate existing limitation. Candidate source changes do not alter the live
installation. Disabling the active harness to bypass a refusal is prohibited.

## Durability and recovery

Controller writes use atomic publication, native file locking and generation
checks. Native lock acquisition failure raises before the critical section; no
mkdir/mtime fallback evicts a supposedly stale owner. Directory synchronization
errors remain errors. Local fixtures do not certify network filesystems or real
power-loss behavior.

Back up the complete quiescent `.taskplane/` tree, accepted external authored
artifacts/logs referenced by it, exact workspace/owner identity and compatible
runtime/package. A controller JSON alone is insufficient. Do not edit pointers,
copy a stale controller over newer history or delete unknown in-flight ownership.
Keep original bytes and provenance when diagnosing recovery.

Supported same-root recovery/import validates its exact request, original
controller, source approval observation and retained originals. Inactive binding
or relocation accepts exactly one authentic ordinary initialization or verified
committed import provenance. All runs must be inactive/retired; active, pending,
missing, ambiguous or tampered history refuses. Preserve the archive; never
synthesize a missing marker or carry old approval authority into a fresh store.
See [state import](state-import-and-release.md).

New candidate runs use the compact contract. Old runs are readable for history,
diagnostics and archival; active old-contract mutation requires the original
compatible runtime. An update or new run never transfers approvals, native
identity, grants or verification claims.
