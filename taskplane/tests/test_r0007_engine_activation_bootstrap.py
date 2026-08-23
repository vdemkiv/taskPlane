"""R-0007: installed metadata is separate from current hook enforcement."""
from datetime import datetime, timedelta, timezone

import enforcement


def _decision(status="unproven"):
    row = {
        "schema": enforcement.SCHEMA,
        "repository_fingerprint": "a" * 64,
        "workspace_fingerprint": "b" * 64,
        "session_fingerprint": None, "run_id": "run", "revision": "head",
        "host": "codex", "mode": "warn", "status": status,
        "receipt_evidence": {"effective_path": "configured_only",
                             "capabilities": {}},
        "meter_evidence": {"governed": False, "hook_seen": False,
                           "warning": None},
        "reasons": ["no current receipt"], "advisory": None,
        "observed_at": "2026-08-23T00:00:00Z",
    }
    row["evidence_id"] = "enf-" + enforcement._digest(
        enforcement._decision_id_payload(row))
    return row


def test_unproven_hook_blocks_terminal_signoff():
    assert enforcement.final_signoff_allowed(_decision()) is False


def test_scoped_expiring_exception_is_attributable_and_current():
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    row = enforcement.acknowledge_exception(
        _decision(), actor="human:test", reason="host bridge unavailable",
        scope="final-signoff", expires_at=expiry,
        accepted_limitations=["screen enforcement is unproven"])
    assert enforcement.validate_decision(row)["advisory"]["exception"][
        "actor"] == "human:test"
    assert enforcement.final_signoff_allowed(row) is True

