# State import and release

State recovery is a provenance and durability operation. Inventory the exact source files, hashes and modes, distinguish original records from post-loss backups, and retain missing files as unknown. An inspection copy or `ready_to_import` report has no authority to continue a workflow.

Use supported import/recovery entry points only with the exact reviewed source, target workspace, runtime and request. Preserve authentic prepared recovery receipts. Temporary data must be flushed before publication; newly created directory ancestry must be durably published. A retry may complete the same authentic transaction, but cannot synthesize a missing receipt, overwrite a different target, follow a symlink or claim unsupported directory synchronization.

Before release, verify the final candidate and both host archives. `scripts/package_plugin.py` selects required members and records their hashes; wrappers select the OpenAI or Claude format. Every selected member must exist as a regular file, stay inside the declared source envelope and match the tested bytes. A commit label alone does not establish source identity. Preserve all dirty-source and historical lineage differences.

The five required package documents are bootstrap prerequisites: this file, `claude-interactive-recovery.md`, `host-compatibility-watch.md`, `test-candidate.md`, and the root `CHANGELOG.md`. Native release verification reads them after bootstrap. A bootstrap archive enables later feature work; final producer changes require another package and final-source verification.

Installation is a separate concrete action. Present the exact archive/member digests, destination, supported reload procedure, existing-runtime backup and compatible rollback before installing. At genuine quiescence use the supported context-upgrade preview/commit, bound to accepted checkpoints, selection, package and verification evidence. Do not copy controller snapshots, reset failed attempts or bypass unavailable host authority. Native identity and automatic hooks must match the actually loaded package after reload.

A rollback must preserve current decisions, attempts and source history. An older runtime incapable of the active contract must refuse mutation. Publication, merge and release distribution require their applicable explicit authorization. Local package or fault-injection checks do not prove installation, real power-loss recovery, Windows execution or native lifecycle success.
