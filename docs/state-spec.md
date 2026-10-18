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
| `flow-events.jsonl`, `knowledge/graph.json`, dashboard and usage snapshots | Optional observations and generated views; not phase authority |

The root key identifies the owning conversation/store, not a freely supplied
worker principal. The controller owns ordered Product → Design → Plan → Build →
Evaluate → Engineering → Retro visits and seven separate human decisions. A
storage-generation counter is compare-and-swap transaction state; changing it
does not make unrelated task inputs stale. Worker attempts retain actual native
identity, relevant dependencies and observed completion/cancellation facts.

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
