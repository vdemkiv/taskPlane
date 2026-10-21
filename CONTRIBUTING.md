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
python -m pytest taskplane/tests -q                    # run from the repo ROOT (conftest imports the taskplane package)
python -m unittest taskplane.tests.test_runner_isolation.TestUnittestRunnerIsolation -v
```

CI (`.github/workflows/ci.yml`) runs the authoritative suite on Python 3.12,
focused compatibility boundaries on Python 3.10/3.11, and one `unittest`
store-isolation canary. Pytest owns the test suite; CI does not execute it a
second time through partial `unittest discover` collection.

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
