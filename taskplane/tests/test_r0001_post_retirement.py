"""T21 post-retirement regression using the incumbent migration owners.

The declared suite also reruns every T16B wiring pair and early policy case.
These local production checks use simulated authority and are not native
journey, final sign-off, or publication evidence.
"""
from pathlib import Path
import json

import pytest

from taskplane import loop, review_evidence, stage_migration, track
from taskplane.tests.test_r0001_legacy_retirement import (
    RUN_ID, legacy_workspace, _migrate, _terminalize,
)


@pytest.mark.parametrize("mode", ["disabled", "enabled"], ids=["rollback", "enabled"])
def test_post_retirement_refusal_preserves_evidence_and_next_action(
        legacy_workspace, monkeypatch, mode, request, record_property):
    workspace, store, _ = legacy_workspace
    receipt = _migrate(legacy_workspace)
    _terminalize(legacy_workspace, receipt)
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", mode)
    before = store.load(RUN_ID)
    legacy_bytes = Path(loop._loop_path(workspace)).read_bytes()
    original = store.read_stage_object(RUN_ID, receipt["result"]["head"]["object"])
    original_bytes = review_evidence.canonical_bytes(original)

    refusal = track.new(workspace, "retired", "must not resume the retired writer")
    assert refusal == {"error": (
        "legacy track writes are read-only after verified stage migration; "
        "use stage commands")}
    assert track.list_(workspace)["active"] is None
    assert stage_migration.migration_projection(workspace)["receipt"] == receipt
    retained = stage_migration.read_compatible_contract(original_bytes)
    assert retained.source_bytes == original_bytes
    assert retained.payload == original
    assert retained.progression_authority is False
    assert store.read_stage_object(RUN_ID, receipt["result"]["head"]["object"]) == original
    assert store.load(RUN_ID) == before
    assert Path(loop._loop_path(workspace)).read_bytes() == legacy_bytes
    record_property("post_retirement_case", json.dumps({
        "selector": request.node.nodeid, "case_id": mode,
        "outcome": "refused", "reason": refusal["error"],
        "next_action": "use stage commands", "effects": [],
        "evidence_reference": receipt["result"]["head"]["object"]["fingerprint"],
        "rollback_readable": True,
        "evidence_mode": "local-production-with-simulated-host-and-authority",
    }, sort_keys=True))
