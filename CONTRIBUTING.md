# Contributing to taskplane

Thanks for helping. The short version: stdlib-only Python, tests must pass,
generated artifacts must be regenerated, and domain evidence must remain truthful.

## Run the tests

```bash
git clone https://github.com/vdemkiv/taskPlane
cd taskPlane
git config user.email you@example.com && git config user.name you   # gates need commit identity
awk 'sub(/^# test-lock: /, "")' requirements-dev.lock > .requirements-test.lock
python -m pip install --require-hashes --no-deps -r .requirements-test.lock
python -m pip install --require-hashes --no-deps -r requirements-dev.lock
rm .requirements-test.lock
python scripts/ci_local.py --check tests               # isolates before collection/import
python -m unittest taskplane.tests.test_runner_isolation.TestUnittestRunnerIsolation -v
```

CI (`.github/workflows/ci.yml`) runs the authoritative suite on Python 3.12
and compile/help checks on Python 3.10/3.11/3.13. The `unittest` store-isolation
canary is run explicitly before candidate collection by the wrapper. Pytest also
collects its regression assertions. Partial `unittest discover` is not a substitute
for the pytest inventory.

## Ground rules

- **Stdlib only.** The runtime (`taskplane/*.py`) may not gain pip
  dependencies; it must run anywhere the plugin does.
- **Preserve the protected outcome.** Scope, human authority and required
  completion evidence remain enforced. Ordinary source review uses native
  permissions. A control that blocks its own required operation should be
  corrected in its existing owner; do not preserve a deadlock merely because
  a historical test expected it.
- **Tests accompany behavior changes**, including a regression test for every
  bug fix.

## Regenerate, don't hand-edit

Some shipped files are generated; CI fails if they drift from their sources:

```bash
python3 lenses/_generate_catalog.py        # lenses/catalog.json summary check
python3 lenses/_generate_lens_prompts.py   # lenses/<id>.md evaluator prompts
python3 scripts/gen_lens_catalog.py        # docs/lens-catalog.md
```

The README animation uses Pillow only in its development asset toolchain. It
never enters `taskplane/*.py` or the ordinary test profile. The commands below
are intentionally a source build: they install hash-locked universal build
tools, force the reviewed Pillow source artifact, and disable pip's isolated
build resolver. Run them in `bash` (`Git Bash` on Windows); the source build
also needs the native compiler/toolchain for your platform.

```bash
awk 'sub(/^# asset-build-lock: /, "")' requirements-dev.lock > .requirements-asset-build.lock
python -m pip install --require-hashes --no-deps --only-binary=:all: -r .requirements-asset-build.lock
rm .requirements-asset-build.lock
awk 'sub(/^# asset-lock: /, "")' requirements-dev.lock > .requirements-asset.lock
python -m pip install --require-hashes --no-deps --no-binary=Pillow --no-build-isolation -r .requirements-asset.lock
rm .requirements-asset.lock
python3 scripts/render_readme_gif.py
git diff --exit-code -- docs/assets/taskplane-cowork-flow.gif
```

## Reporting problems

Use the issue templates (they encode the triage fields from `SUPPORT.md`).
Security-sensitive reports go to the private contact in `SUPPORT.md`, never a
public issue.

## Release packaging

Every delivery of product fixes or improvements must bump the version before
the marketplace is updated. Use a new version above the latest published version;
never replace a published package with changed product bytes under the same version.
Keep `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, and both the
top-level and plugin-entry versions in `.claude-plugin/marketplace.json` aligned.
Record the changes under that version in `CHANGELOG.md` and run
`python3 taskplane/tp.py version --verify` before packaging.

`python3 scripts/package_openai.py` builds the deterministic OpenAI
marketplace zip into the gitignored `dist/`; CI validates the build and its
reproducibility on every push.

## Keep generated run data out of Git

Commit source, reusable fixtures, maintained documentation/specifications, and
runtime policies. Do not force-add release bundles, `build/`, `dist/`, `exports/`,
`.em-review/`, `waves/`, backlog/analysis/report folders, completed `plan/`
outputs, or generated Design reports. Keep local copies for later analysis;
removing already-committed data from tracking does not require deleting it.
Run evidence belongs in the project's ignored `.taskplane/` store or CI artifacts. Historical
inputs needed by tests belong under `taskplane/tests/fixtures/`, explicitly labeled
as fixtures rather than current delivery evidence. A `.gitignore` rule does not
untrack a file already committed; check `git ls-files -ci --exclude-standard`
before opening a PR.

### Isolation and evidence

Use `scripts/ci_local.py` for product checks. It establishes disposable native
stores, home/config/cache/temp directories and bytecode/plugin isolation before
any candidate test import, collection or child process. Inherited `CODEX_`,
`CLAUDE_` and `TASKPLANE_` variables are removed; the intentional
`TASKPLANE_BROWSER_EXECUTABLE` override is retained. An installed formal runner
may omit that override, so browser availability is discovered in its actual environment.

Direct `python -m pytest` does **not** have the same boundary: the seed conftest
autouse fixture runs after collection. Direct unittest is supported for the exact
stdlib canary above, whose fixture launches isolated children; it is not a general
runner for product tests. Package archives intentionally exclude `taskplane/tests`;
archive exclusions do not establish test isolation.

`--report-json /outside/source/execution.json` retains commands, environment,
stdout/stderr, JUnit, collection and diagnostic phase timestamps. Use external paths for JUnit
and `--package-output` too. Without an explicit package output directory, archives
are disposable verification products. CI uploads complete reports even on failure.
`--host hostless` explicitly audits and executes hostless package cases;
a verified empty partition is reported as such, never counted as an executed test.

The remediation-only entry point resolves its source manifest relative to the
invocation workspace and emits the complete execution record to stdout:

```sh
python work/taskplane-remediation/scripts/ci_local.py --check remediation --remediation-manifest .taskplane/remediation-v5/build/source-manifest.json
```

It neither writes nor overwrites the formal `results.json` or `checks.json`. Missing
scenario bindings, required skips/xfails, dependency failures and incomplete
functional coverage fail the composite. The accepted v5 registry has 30 groups and
18 criteria. Exact legacy performance exclusions remain inventoried; mixed modules
retain their functional tests. No benchmark/capacity job or timing target is required. Named optional platform skips
are retained separately and cannot satisfy required assertion bindings. A passing
local composite does not certify native restart, future monitor wake or Windows CI.


Runner reports use the versioned lossless evidence encoding documented in
[Functional CI and evidence](docs/ci-performance.md#lossless-evidence-transport). Load a saved
JSON report with `scripts.ci_local.decode_evidence` before using the existing
execution/measurement accessors; use `raw_evidence_bytes(record, name)` for exact
process or file bytes. Raw evidence is embedded, verified and deduplicated, including
failed attempts. Temporary output paths are not the sole evidence copy. Optional
platform skips require an exact reviewed test/capability binding and remain unable
to satisfy required scenarios. Actual final-candidate Windows/native-host execution remains a separate integration
requirement. Existing CI timeout caps are operational limits. Full candidate
validation is deferred to Engineering with the proper version installed; source
changes and static checks do not certify those executions.
