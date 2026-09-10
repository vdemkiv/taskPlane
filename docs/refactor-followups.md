# Harness refactor follow-ups

For PR #22 on 2026-09-10, the user decided: “Defer structural targets; keep functional CI blocking.” This changes the integration gate, not the measured results or the original targets. No upgrade or migration work is required; local marketplace testing can start fresh.

Measured at commit `146104cc089872394bfa9db648635d06efd07577`:

| Follow-up | Measurement | Original target |
| --- | --- | --- |
| Runtime size | 126,729 physical lines | ≤120,000 |
| Test-suite size | 72,899 physical lines in 230 test files | ≤70,000 lines and ≤200 files |
| Plan-topology test attribution | 0.268 test/source line ratio | 0.3–2.0 or a substantive recorded reason |
| Import-cycle debt | One component with 27 members and 70 internal edges; new cyclic members compared with policy | Remain within the recorded topology |

CI continues to generate the size and import-cycle reports, including their violations. They are non-blocking until these follow-ups are resolved and strict CI enforcement is restored. The default import-cycle command and `--check` remain strict; `--report-only` permits known structural debt but still rejects invalid policy and scan errors. Size measurement also retains its explicit `--check` mode. The policy inventory is not rebased to hide the new cycles.

Functional tests, browser behavior, platform/import compatibility, source/settings checks, lint, type checks, deterministic packages, and release provenance remain blocking. Do not delete meaningful tests or compress source merely to reach the size targets. Tests verify behavior; they do not define product requirements.

Separate validation gap: the earlier whole-suite EM review was incomplete because the current read-only contract refuses the command tools available in this Codex task. Passing CI or building a fresh marketplace package does not establish a completed live EM review. Current-version EM tool access remains follow-up work for fresh testing.
