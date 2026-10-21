# Model evaluation

taskPlane does not treat a dynamic model like a unit-test function. Scenario
inputs and workflow invariants are deterministic; wording, reasoning paths,
and the number of useful findings are not. A model run therefore passes by
respecting the governed workflow and producing defensible evidence—not by
matching a frozen transcript or reaching an identical output every time.

The evaluator has three layers:

- deterministic tests validate all ten approved skill flows, scenarios,
  evidence synthesis, workflow grading, and the native driver without spending
  model tokens;
- live runtime guidance ships from
  `skills/taskplane/references/runtime-evals.json`. Every stage brief receives
  the applicable controls. Every `tp loop submit pass` automatically invokes
  the same checkpoint exposed by `tp loop guide`: it checks machine-owned
  workflow facts, returns one bounded correction for recoverable drift,
  records recovery, and blocks the same unresolved drift on repetition;
- `scripts/eval_skills.py` runs the nine CLI-native skills with a real Claude
  or Codex model; `tp-tag` is validated live only in Claude Tag/Slack, where its
  human reply gate actually exists.

Runtime controls focus on contract/DoR ordering, graph-before-route, one sealed
review context, selective lens mapping, canonical result collection,
orchestrator-owned gates, human approval, and DoD. Historical model baselines
are telemetry only. They may reveal cost or reliability trends, but cannot be
a release gate and are never loaded by the runtime guide.

The native runner copies the current checkout into each disposable fixture and fingerprints that bundle before and after the run. It initializes personal/private knowledge storage, installs the fixture-local Codex hooks, ignores the user's Codex configuration, and instructs the model to use only that staged skill and CLI. Codex uses a disposable home with only the existing authentication linked in: its host-authored session metadata is the dispatch receipt when a repo hook is silent, and is retained only for the evaluation without adding tasks or plugin state to the user's normal Codex home. This prevents a marketplace install from being mixed with repository code and prevents real native workers from being graded as inline work.

```bash
python3 scripts/eval_skills.py all \
  --host codex \
  --model YOUR_EXPLICIT_CODEX_MODEL_ID \
  --reasoning-effort high \
  --output-root /private/tmp/taskplane-skill-eval
```

Each scenario declares its valid terminal:

- `human_gate` for facade, Go, Design, Build, Engineering, Product, and Tag
  evaluations that deliberately stop for a person;
- `response` for the read-only Help, Status, and North-star skills;
- `completion` only for flows that genuinely complete governed work.

Declared `n/a` controls do not fail eligibility. Missing evidence for an applicable control still fails closed. Model runs are intentionally separate from CI and should be run only after the focused deterministic evaluator checks are green.
