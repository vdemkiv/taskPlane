"""T10 incumbent rollback and uncertain-effect conservation rehearsal.

Runtime selection remains inactive until T15C. These local checks exercise
the existing rollback controls, not a fabricated cutover or native terminal.
"""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from taskplane import loop, loop_recovery
from taskplane.tests.test_stage_rollout import _store, _migrate, _tree_bytes, RUN_ID
from taskplane.tests.test_r0001_lease_retry import _owner, _dispatch


@pytest.mark.parametrize("case", ["start", "reuse", "resume", "terminalize",
    "terminalize-and-start", "split", "stop-new-dispatch", "uncertain-no-relaunch"], ids=str)
def test_foundation_rollback_rehearsal(tmp_path, monkeypatch, request, record_property, case):
    if case not in {"stop-new-dispatch", "uncertain-no-relaunch"}:
        workspace = tmp_path / "checkout"
        workspace.mkdir()
        store, initial = _store(tmp_path)
        migrated = _migrate(workspace, store, initial)
        stage_id = migrated["stage_ids"][0]
        run_root = Path(store.home) / "runs" / RUN_ID
        before = _tree_bytes(run_root)
        # Injection selects the actual local store; no producer output is
        # patched. The history and refusal are emitted by stage_command.
        monkeypatch.setattr(loop, "_stage_store", lambda _ws, _run_id: store)
        monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "disabled")
        history = loop.stage_command(str(workspace), "history", {"run_id": RUN_ID, "limit": 100})
        assert [row["stage_id"] for row in history["stages"]] == [stage_id]
        payload = ({"stage": {"run_id": RUN_ID}}
            if case in {"start", "reuse", "terminalize-and-start"}
            else {"run_id": RUN_ID})
        refused = loop.stage_command(str(workspace), case, payload)
        assert refused["enabled"] is False
        assert "stage-native mutation is disabled" in refused["error"]
        assert _tree_bytes(run_root) == before
        evidence = migrated
    else:
        owner, lease, runtime, dispatch, calls, _, _ = _owner(tmp_path, monkeypatch)
        admitted = owner.admit(lease, expected_fence=0)
        if case == "uncertain-no-relaunch":
            owner.execute(admitted, nonce_action=_dispatch(runtime, dispatch),
                action=lambda bound: calls.append("original-effect"))
        owner.cancel(admitted)
        with pytest.raises(loop_recovery.LeaseRefusal):
            owner.execute(admitted, nonce_action=_dispatch(runtime, dispatch),
                action=lambda bound: calls.append("forbidden-new-effect"))
        if case == "uncertain-no-relaunch":
            replacement = replace(lease, lease_id="lease-2", attempt_id="attempt-2",
                operation_id="op-2", fencing_token=2)
            with pytest.raises(loop_recovery.LeaseRefusal) as refusal:
                owner.admit(replacement, expected_fence=1)
            assert refusal.value.result["continuation"] == "observe_wait_reconcile"
            assert calls == ["original-effect"]
        else:
            assert calls == []
        evidence = lease.lease_id
    record_property("foundation_rollback", json.dumps({
        "selector": request.node.nodeid, "case_id": case, "collected": True,
        "executed": True, "outcome": "preserved-and-held",
        "evidence_reference": evidence,
        "evidence_mode": "local-incumbent-controls-with-simulated-authority",
        "native_terminal_claimed": False, "expansion_authorized": False,
    }, sort_keys=True))
