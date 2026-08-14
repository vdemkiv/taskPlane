# Model evaluation

The evaluator has two layers:

- deterministic tests validate scenarios, evidence synthesis, workflow grading, and the native driver without spending model tokens;
- `scripts/eval_skills.py` runs the nine Codex-visible skills with a real Claude or Codex model.

The native runner copies the current checkout into each disposable fixture and fingerprints that bundle before and after the run. It initializes personal/private knowledge storage, installs the fixture-local Codex hooks, ignores the user's Codex configuration, and instructs the model to use only that staged skill and CLI. This prevents a marketplace install from being mixed with repository code.

```bash
python3 scripts/eval_skills.py all \
  --host codex \
  --model YOUR_EXPLICIT_CODEX_MODEL_ID \
  --reasoning-effort high \
  --output-root /private/tmp/taskplane-skill-eval
```

Each scenario declares its valid terminal:

- `human_gate` for facade, Go, Design, and Build evaluations that deliberately stop for a person;
- `review_complete` for Engineering after canonical ReviewKernel collection;
- `response` for Product and the read-only Help, Status, and North-star skills;
- `completion` only for flows that genuinely complete governed work.

Declared `n/a` controls do not fail eligibility. Missing evidence for an applicable control still fails closed. Model runs are intentionally separate from CI and should be run only after the focused deterministic evaluator checks are green.
