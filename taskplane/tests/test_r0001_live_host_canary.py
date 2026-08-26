"""Optional live Codex producer-event canary.

The deterministic fixture suite is release-independent.  A live host must
explicitly provide its immutable JSONL event capture; absence is unavailable,
never success and never a request for manual outage resolution.
"""

from __future__ import annotations

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
    text = path.read_text(encoding="utf-8")
    assert '"stage":"evaluate"' in text
    assert '"stage":"em"' in text
