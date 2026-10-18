# CI coverage and proportionate verification

Use `python3 scripts/ci_local.py --help` for the actual candidate's suite/options.
The local runner groups regression, native/platform, package, quality, browser and
capacity evidence. Focused development checks precede one combined affected
verification; repeat only after changed inputs, failure or an uncovered case.

Record exact argv, environment/capability limits, collected/executed tests and
actual pass/fail/unknown. Preserve failed attempts and logs. A zero exit with no
required tests or unsupported runtime capability is not success. Skips require a
real optional capability explanation and must not hide required failures.

Remove retired node IDs and helper/fixture/import references with removed code.
Update scenario registries to actual behavior instead of adding exclusions.
Collection and caller audits are one-time retirement evidence, not new permanent
absence tests. Keep useful negative path/identity/durability coverage.

Package checks build reproducible Codex/Claude archives, validate their selected
regular members and execute extracted candidate entrypoints in disposable stores.
Build-time archive/member hashes establish tested package identity at this
meaningful boundary; ordinary workflow operations do not rehash every member.
A package build does not install, reload or publish.

Local Linux/macOS/Windows fixtures, actual platform CI, rendered-browser checks
and live installed-host journeys are separate observations. Missing scanners,
browser, sandbox capability or native host remain unknown when required, with a
precise reason. Do not infer speed/token savings from removed byte accounting or
claim a benchmark/platform run that was not observed.

Ordinary JSON reports reuse the final execution's candidate fingerprint and its
coverage description. They do not traverse `.git`, `.taskplane`, `dist`, outputs
or unrelated workspace documents. Explicit archive/isolation and historical
audit operations retain their own integrity checks.

An optional skip is identified by its reviewed test node, typed parameters and
actual platform capability evidence. The explanation remains in the report but
its punctuation and English wording are not an eligibility key. Unknown test or
capability bindings, missing native errors and required-scenario skips still fail.
