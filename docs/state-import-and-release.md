# State recovery and release procedure

## Release through the existing infrastructure

Use one release branch based on current `main`, an ordinary aligned version in
both host manifests and marketplace entries, and a draft PR to `main`. Consolidate
related unpublished fixes before selecting the final review commit. Never publish
different bytes under an existing version or use a local candidate suffix instead
of CI.

The existing `CI` workflow owns Linux/Windows portability and core/native shards,
both extracted-host archive journeys, quality/package/provenance, dashboard browser
conformance, security scans, interpreter imports, and the two aggregate gates.
`Storage capacity stress` is the separate three-job release validation workflow.
Record each run's exact head SHA, attempt, result and artifact provenance. Local
focused tests do not establish these jobs passed. Changes after review or failed
checks require validation of the new commit. Use the standard `plugins-<sha>`
Actions artifact and its package sidecars. PR CI may use a merge ref: verify the
tree and source-member hashes against the reviewed head. Final release/main CI
must identify the actual published source. Merging, publishing and host activation
remain separate authorized actions.

## Preserve state before approved installation or restart

A managed-worktree archive preserves Git data but can omit ignored files. The
`.taskplane/` store is ignored. **Do not archive a task or worktree as a reload
mechanism.** Source recovery is not workflow-state recovery.

Before any approved lifecycle change:

1. Stop at a quiescent boundary and record the actual root, workspace, run,
   revision and checkpoint. Reconcile known native workers and command handles.
2. Make a complete backup outside the workspace, including hidden/ignored files,
   the whole `.taskplane/` store, original approval observations, controller,
   initialization metadata, context objects/ledgers, harness, journals and sealed
   artifacts. A manifest of selected Product files is insufficient. Preserve Git
   identity and any required external Git metadata separately.
3. Verify every archived file and symlink against an inventory, retain hashes and
   permissions, and test restoring the archive into a separate inspection folder.
   Do not execute the copied workflow or change its root identity. Treat omissions,
   changed files during backup, special files and failed reads as blockers.
4. Preserve installed package hashes and configuration/enablement observations.
   Install only the authorized standard release artifact through the host's
   supported plugin management. Do not patch caches or hook files in place.
5. Use the host's supported application/session restart that preserves the durable
   task and workspace. Verify availability for that host/version; if unavailable,
   stop with that exact limitation. A catalog rescan does not prove hook replacement.
   Do not use archive/delete or another daemon.
6. Verify restored files and the original root/run/checkpoint and decisions. Check
   installed version, source-member hashes, launcher and interpreter, then a genuinely
   automatic admitted pre/post hook pair from the new runtime with the current
   binding. Runtime fingerprints include consent and state-import modules.
   Configuration, CLI success, synthetic hook events and version strings alone are
   insufficient. Report actual native smoke results separately for Codex and Claude.

## Supported import when original initialization metadata was lost

`flow recover --recovery-request` is bounded to one original native root/workspace, one active
unapproved first Product checkpoint, no workers or unresolved admissions, unchanged
verified source, and no protected-host or explicit relocated-workspace contract.
It does not silently discard other runs, policies or archives. More complex loss
needs its own recovery design.

Prepare a `taskplane.state-import-request/v1` JSON object containing exactly:

- `host`: `codex` or `claude`, matching the actual owning adapter.
- `binding`: the full original checkpoint binding from the preserved controller.
- `controller`: `{ "path": "/absolute/original-controller.json", "sha256": "..." }`.
- `artifacts`: every original sealed manifest path mapped to an absolute backup
  path and SHA-256 in the same shape. No extra writes are admitted.
- `approval`: the absolute path and SHA-256 of the complete existing delegated
  checkpoint observation. Its standard provenance/semantic verifier must pass.
- `request_reference`: the actual instruction authorizing recovery.

Hash the complete request and inspect it through the **installed matching release**:

```sh
python3 /absolute/plugin/taskplane/tp.py flow recover --workspace /original/project \
  --recovery-request /absolute/request.json --expected-sha256 REQUEST_SHA256 \
  --recovery-mode inspect
```

Inspection returns the finite write set, original checkpoint, missing history,
unverified historical references and continuation requirements. It performs no
import or phase decision. After separately authorized recovery activation, the
same command with `--recovery-mode apply` publishes verified artifacts and a new
recovery receipt. Conflicting files, symlinks and foreign roots are refused. A
partial transaction blocks fresh initialization; repeat only the identical request.

The original controller and approval observation are retained verbatim under
`.taskplane/recovery-v1/<request hash>/`. The active controller preserves its run,
revision, visits, admissions and decisions; only old parent-hook readiness is
removed. `state-import-<root key>.json` records new recovery provenance, **not** a
fabricated historical initialization marker. Original initialization metadata,
presentation, context deliveries, unretained objects and journal remain labelled
missing. Root-supplied observations and checksums are cooperative evidence, not
independent account authentication or host attestation.

After import, observe fresh automatic hooks, regenerate and actually present the
same Product checkpoint, and record the existing approval with `flow decide`.
Import itself does not accept Product. Advance to Design using the resulting
revision, generate and fully consume fresh required context, and preserve the new
Design approval boundary. Missing historical receipts are never synthesized or
treated as fresh context reads. No new Product wording or approval is needed.

## Stabilization acceptance coverage

| Case | Required evidence |
| --- | --- |
| Host archive omits ignored state | Original checkpoint survives in external verified backups; loss is reported and no replacement run is created. |
| Complete backup/restore | Every member, hidden/ignored state, seal and initialization file round-trips unchanged; actual native host archive behavior remains a separate smoke check. |
| Lost marker/context import | Both local adapters preserve original bytes, expose missing history, clear old readiness, and require separate approval and presentation. |
| Interrupted publication | Prepared receipt prevents initialization; exact retry succeeds without duplicate decisions or replacing conflicting data. |
| Unsafe input | Wrong host/root/revision/hash, changed source, missing/conflicting artifacts, symlinks, child callers and unresolved work refuse without changing evidence. |
| Restart/cache behavior | Installed hashes and a fresh actual automatic hook pair match the same runtime and original task/run; fresh Claude and Codex observations reported separately. |

`test_state_import.py` exercises loss/import and negative boundaries through both
local adapters, including continuation to fresh Design context. These tests do not
certify a real host archive, restart, cache refresh or full live workflow.
