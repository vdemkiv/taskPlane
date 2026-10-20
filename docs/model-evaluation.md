# Model evaluation

The former `scripts/eval_skills.py`, `runtime-evals.json` and `tp loop` evaluator were removed. Historical results are evidence about their original source only; there is no current paid-model matrix or READY certification from that runner.

Use `python3 scripts/ci_local.py --check tests` for the maintained deterministic inventory. Native workflow, approval, ownership, recovery and package fixtures exercise the current controller. Use `python3 scripts/ci_local.py --check quality` for static and package identity checks. Follow [Contributing](../CONTRIBUTING.md) for complete dependency installation and isolation.

For live acceptance, exercise the current seven-phase flow in a disposable project with the exact candidate installed in the intended host. Record candidate/member hashes, host version, actual hook observations, manual or explicitly opted-in automatic mode, checkpoint decisions, worker identities and terminal results, and actual checks. Preserve failures and unknowns. Fixture success does not certify installed Codex, Claude, Windows or channel behavior.

See [live evaluation readiness](EVAL-DoR.md). No obsolete command or historical baseline grants release readiness.
