# Contributing to taskplane

Thanks for helping. The short version: stdlib-only Python, tests must pass,
generated artifacts must be regenerated, and no change may weaken a guardrail.

## Run the tests

```bash
git clone https://github.com/vdemkiv/taskPlane
cd taskPlane
git config user.email you@example.com && git config user.name you   # gates need commit identity
awk 'sub(/^# test-lock: /, "")' requirements-dev.lock > .requirements-test.lock
python -m pip install --require-hashes --no-deps -r .requirements-test.lock
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
- **Never weaken a guardrail.** Gates, contracts, scope screening, and
  evidence checks fail closed. A change that makes an interaction simpler by
  removing or softening an enforcement path will not be accepted.
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
never enters `taskplane/*.py` or the ordinary test profile. Install the exact
reviewed source artifact and verify deterministic regeneration with:

```bash
awk 'sub(/^# asset-lock: /, "")' requirements-dev.lock > .requirements-asset.lock
python -m pip install --require-hashes --no-deps -r .requirements-asset.lock
rm .requirements-asset.lock
python3 scripts/render_readme_gif.py
git diff --exit-code -- docs/assets/taskplane-cowork-flow.gif
```

## Reporting problems

Use the issue templates (they encode the triage fields from `SUPPORT.md`).
Security-sensitive reports go to the private contact in `SUPPORT.md`, never a
public issue.

## Release packaging

`python3 scripts/package_openai.py` builds the deterministic OpenAI
marketplace zip into the gitignored `dist/`; CI validates the build and its
reproducibility on every push.
