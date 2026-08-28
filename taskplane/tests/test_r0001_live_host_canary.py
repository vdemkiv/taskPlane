"""Optional genuine-host Codex producer-event companion canary.

The mandatory hermetic production-path proof lives in
``test_em_m1_proof_paths.py``.  This separate canary accepts only an explicit
immutable capture from a genuine Codex host; absence remains unavailable and
must never be represented as live evidence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def test_codex_evaluator_and_em_producer_observations_live():
    capture = os.environ.get("TASKPLANE_CODEX_PRODUCER_EVENT_FILE")
    if not capture:
        pytest.skip("live Codex producer-event capture unavailable on this host")
    path = Path(capture)
    if not path.is_file():
        pytest.skip("declared live Codex producer-event capture is unavailable")
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    observed = [
        row
        for row in rows
        if row.get("schema") == "taskplane.host-producer-event/v1"
        and row.get("host") == "codex"
        and row.get("stage") in {"evaluate", "em"}
    ]
    assert len(observed) == 2
    assert {row["stage"] for row in observed} == {"evaluate", "em"}
    for row in observed:
        assert row["event_id"]
        assert row["host_session_id"]
        assert row["host_turn_id"]
        assert row["output_sha256"]
        assert row["output_contract_fingerprint"]
