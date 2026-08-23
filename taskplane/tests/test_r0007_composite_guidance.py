"""R-0007: all live worker payloads carry the same efficiency contract."""
from pathlib import Path

import loop


ROOT = Path(__file__).resolve().parents[2]
IDS = {"composite-command", "per-step-marker", "verdict-sized-output"}


def test_live_worker_guidance_is_versioned_and_complete():
    for step in ("execute", "evaluate", "fix"):
        guidance = loop.worker_guidance(step)
        assert guidance["schema"] == "taskplane.worker-guidance/v1"
        assert {row["id"] for row in guidance["requirements"]} == IDS
    assert loop.worker_guidance("plan") is None


def test_all_three_role_files_repeat_the_same_machine_markers():
    for name in ("tp-executor.md", "tp-evaluator.md", "tp-fixer.md"):
        body = (ROOT / "agents" / name).read_text()
        assert "taskplane.worker-guidance/v1" in body
        assert all(marker in body for marker in IDS)

