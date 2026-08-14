# Definition of Ready — model-behavior evaluation

This is the entry check for a paid/live-model validation run. Deterministic
evaluation is part of CI; live Claude/Codex runs are deliberately out of band
so ordinary changes do not spend model tokens.

## Current status

**READY when the host prerequisites below are present.** The repository layer
is complete: scenarios, flow fingerprints, fixture isolation, native adapters,
host-observed command/dispatch evidence, scoring, and baseline eligibility are
implemented and covered by focused deterministic tests.

The ten shipped skill flows are covered as follows:

| Skill | Deterministic flow/scenario | Live native model lane |
| --- | --- | --- |
| `taskplane` | yes | Claude + Codex |
| `tp-go` | yes | Claude + Codex |
| `tp-build` | yes | Claude + Codex |
| `tp-design` | yes | Claude + Codex |
| `tp-engineering` | yes | Claude + Codex |
| `tp-product` | yes | Claude + Codex |
| `tp-help` | yes | Claude + Codex |
| `tp-status` | yes | Claude + Codex |
| `tp-northstar` | yes | Claude + Codex |
| `tp-tag` | yes | Claude Tag/Slack field run only |

`tp-tag` is not graded by pretending a CLI Codex run is Slack. Its approved
`flow.json` and scenario are checked deterministically; the live lane must run
inside Claude Tag with a real thread reply at the approval gate.

## Host prerequisites

- an explicit model id (never inferred from ambient user configuration);
- an authenticated host (`CODEX_HOME/auth.json` for Codex, Claude CLI auth for
  Claude);
- a disposable output root outside the repository;
- enough time for one governed flow; and
- for baseline eligibility, active lifecycle hooks and out-of-band mode.

No GitHub token or ambient global Git configuration reaches the fixture. The
runner stages and fingerprints the exact checkout-local plugin bundle before
the model starts and refuses a baseline if the model changes that bundle.

## Run

```bash
python3 scripts/eval_skills.py all \
  --host codex \
  --model YOUR_EXPLICIT_CODEX_MODEL_ID \
  --reasoning-effort high \
  --output-root /private/tmp/taskplane-skill-eval
```

Run Claude with `--host claude`. The command covers the nine CLI-native skills.
Validate `tp-tag` separately in Slack using `skills/tp-tag/flow.json` and stop
at the first human reply gate.

## Eligibility rules

- `human_gate` is the correct terminal for facade, Go, Build, Design,
  Engineering, Product, and Tag scenarios. Stopping for approval is success,
  not an incomplete run.
- `response` is correct for Help, Status, and North-star.
- A missing hook/dispatch receipt, changed staged bundle, missing required
  evidence, or an applicable control with no evidence fails closed.
- Declared `n/a` controls remain visible with a reason and do not fail a run.
- Baselines compare item-level outcomes; the aggregate score is reported but
  never allowed to hide a regressed control.

## Efficiency rule

Run deterministic checks first. Then run each live scenario once per release
candidate, record the result, and investigate only failing control clusters.
Do not repeatedly run the paid matrix while repairing static documentation or
fixture drift.
