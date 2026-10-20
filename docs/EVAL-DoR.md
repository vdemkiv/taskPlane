# Live evaluation readiness

The former paid-model evaluator and its scenario runner were removed. This page does not certify a replacement runner as READY.

Before an authorized live check:

- Run the maintained deterministic and quality inventory described in [Contributing](../CONTRIBUTING.md).
- Build the final candidate and retain its JSON provenance sidecar; identify the exact source commit and archive hash.
- Use an authenticated intended host and a disposable project. Keep the candidate separate from the user's active installation unless installation is explicitly authorized.
- State the requested scenario, approval mode, expected terminal and required evidence. Default manual mode needs each real human decision; automatic mode needs explicit scoped policy authorization and stop phases.
- Observe actual hooks, identities and process completion where required. Missing platform, host or channel coverage remains unknown.

Run the currently shipped skills and `flow` controls. Do not invoke removed `eval_skills.py`, `runtime-evals.json`, `flow.json` scenarios or `tp loop` commands. A manual checkpoint is a valid scenario terminal when it is the declared target; it does not complete seven-phase delivery. Retain the evidence and limitations with the candidate. See [model evaluation](model-evaluation.md).
