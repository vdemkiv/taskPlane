"""R-0007: bounded, observational worker executions-per-turn telemetry."""
import pytest

import dashboard
import runtime_eval
import spend


ROWS = [
    {"run": "r1", "task": "t1", "role": "tp-executor", "turn": "1",
     "executions": 2, "verdicts": 0, "decision": "pass"},
    {"run": "r1", "task": "t1", "role": "tp-evaluator", "turn": "2",
     "executions": 4, "verdicts": 3, "decision": "pass"},
]


def test_replay_reports_target_and_guardian_verdicts_without_gating():
    row = runtime_eval.worker_efficiency_projection(ROWS)
    assert row["median_executions_per_turn"] == 3
    assert row["guardian_verdict_count"] == 3
    assert row["rollout_target"]["met"] is True
    assert row["affects_correctness_or_review_gates"] is False
    html = dashboard.render_worker_efficiency(row)
    assert "observational" in html and "never changes" in html


def test_arguments_or_transcript_fields_are_rejected():
    leaked = dict(ROWS[0], command_args=["secret"])
    with pytest.raises(ValueError, match="only bounded counters"):
        spend.worker_efficiency([leaked])

