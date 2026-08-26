# R-0001 terminal full-matrix verification

Verdict: **FAIL** at exact source SHA `48dba00b8816bfcbea05ba2587f9e3679d7fe7ca`.

Execution override, recorded verbatim: “Human explicitly authorized replacing the interrupted serial full-suite run with parallel disjoint file shards and isolated reruns”. The interrupted serial run reached visible 2% progress, produced no JUnit receipt, and contributes no result count. No tests were rerun during evidence coordination.

## Coverage and results

All 225 `taskplane/tests/test_*.py` files are covered. The deterministic ten-bin partition used stable descending file size with lexical glob order resolving equal-size ties, followed by index modulo ten. Nine original bins were retained; bin 05 was replaced by the disjoint 05a–05d partitions, and all 32 unique `test_evidence_bundle.py` nodes were covered separately.

The 22 primary receipts contain 5,460 attempts including reruns and 4,526 unique final node results: 4,375 passed, 144 failed, 7 skipped, and 0 errors. Eight isolated receipts contain 1,012 attempts and 834 unique final results: 691 passed, 143 failed, and 0 errors. Those isolated reruns reproduce 143 of the 144 primary failures.

| Product failure family | Primary | Isolated confirmations |
| --- | ---: | ---: |
| CI import path and encoding | 9 | 8 |
| Pickup revision attestation | 46 | 46 |
| Scheduler empty legacy waves and telemetry recovery | 12 | 12 |
| Design transitions and authority binding | 7 | 7 |
| Workflow and Codex compatibility | 19 | 19 |
| Cycle and seal drift | 44 | 44 |
| Token ceiling | 1 | 1 |
| Runtime-eval submit behavior | 6 | 6 |
| **Total** | **144** | **143** |

The only primary failure not included in an isolated rerun is `taskplane/tests/test_eval_recorder.py::TestTheFixtureIsInvisibleToTheRepositorysOwnTooling::test_the_corpus_run_names_the_fixture_skipped_and_exits_zero`.

## Closure disposition

The exact-SHA graph snapshot is nondegraded (45 modules, 156 edges) with content fingerprint `eaca9178edd8f981562d204ca4a569e0577b2889c084a27489a9d1271c590990` and quality fingerprint `9fbefee76a17ab4f8c6e3ecc917a31a6b7e0f1b7958e2811259361a46cb5cf47`. Because the terminal matrix is red, the graph terminal seal, W01–W32 terminal closure, and release-green disposition are withheld. W31 live-host producer proof remains unavailable/deferred. Zero lenses were run.

Machine-readable details are in `verification.json`; every retained JUnit receipt and SHA-256 digest is recorded in `junit-inventory.json`. Event `ee97599da72a2ba39d8131cc40be8dbae008f8b7d5541b7df2951d647f3a7808` records this fail result.
