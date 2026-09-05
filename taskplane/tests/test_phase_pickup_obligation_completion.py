"""One Build execution closes all of its remaining sealed obligations."""
from __future__ import annotations

import copy
from pathlib import Path
import subprocess

import pytest

from taskplane import phase_handoff, phase_pickup
from taskplane import tp as cli
from taskplane.tests.test_build_quality import _published_build_checkout
from taskplane.tests.test_stage_non_build_handoffs import _resume_handoff


def _commit(checkout: Path, message: str) -> None:
    subprocess.run(["git", "add", "-f", "exports/pickup"],
                   cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-qm", message],
                   cwd=checkout, check=True)


def _reseal(handoff: dict) -> dict:
    handoff["handoff_id"] = phase_handoff.handoff_identity(handoff)
    handoff["fingerprint"] = phase_handoff.manifest_fingerprint(handoff)
    return phase_handoff.validate_phase_handoff(handoff)


def test_partial_acceptance_overlap_refuses_before_task_selection() -> None:
    handoff = _resume_handoff("plan")
    handoff["obligations"] = [{
        **handoff["obligations"][0], "acceptance": ["A1", "A2"],
    }]
    handoff["tasks"][0]["acceptance"] = ["A1"]
    handoff["producer"] = {"phase": "plan", "outcome": "done"}
    handoff["successor"] = {"phase": "build", "mode": "next-phase"}
    handoff["progress"] = {"completed": ["O1"], "remaining": []}
    handoff["progress_receipts"] = handoff["progress_receipts"][:1]
    handoff["lineage"]["predecessor_receipt_head"] = \
        handoff["progress_receipts"][-1]["fingerprint"]
    # The portable validator accepts this topology: both declarations close
    # over their own proof command, despite the task omitting criterion A2.
    checked = _reseal(handoff)
    before = copy.deepcopy(checked)

    with pytest.raises(phase_pickup.PhasePickupError,
                       match="every acceptance criterion") as caught:
        phase_pickup.select_ready_build_task(checked)

    assert caught.value.code == "scope-widened"
    assert checked == before


def test_legacy_partial_completion_requires_remaining_work_recovery() -> None:
    handoff = _resume_handoff("plan")
    receipt = phase_handoff.create_progress_receipt(
        producer="engine:taskplane.phase-pickup/v1", sequence=1,
        phase="build", obligation_id="O1", task_id="T-001", status="green",
        predecessor_receipt_fingerprint=None,
        checkpoint_receipt_digest="7" * 64,
        integration_receipt_fingerprint="8" * 64)
    handoff["producer"] = {"phase": "build", "outcome": "interrupted"}
    handoff["successor"] = {"phase": "build", "mode": "same-phase-resume"}
    handoff["progress_receipts"] = [receipt]
    handoff["lineage"]["predecessor_receipt_head"] = receipt["fingerprint"]
    checked = _reseal(handoff)
    before = copy.deepcopy(checked)

    with pytest.raises(phase_pickup.PhasePickupError,
                       match="remaining-work recovery") as caught:
        phase_pickup.select_ready_build_task(checked)

    assert caught.value.code == "receipt-lineage"
    assert checked == before
    assert checked["progress"] == {"completed": ["O1"], "remaining": ["O2"]}


def _expanded_checkout(tmp_path: Path, count: int) -> tuple[Path, dict]:
    checkout, original = _published_build_checkout(tmp_path)
    material = {
        key: copy.deepcopy(value) for key, value in original.items()
        if key not in {"schema", "handoff_id", "fingerprint"}
    }
    for ordinal in range(2, count + 1):
        criterion = f"AC{ordinal}"
        material["acceptance"].append({
            **material["acceptance"][0], "id": criterion,
            "ordinal": ordinal, "criterion": f"complete criterion {ordinal}",
        })
        material["obligations"].append({
            **material["obligations"][0], "id": criterion,
            "ordinal": ordinal, "acceptance": [criterion],
        })
        material["progress"]["remaining"].append(criterion)
    material["tasks"][0]["acceptance"] = [
        f"AC{ordinal}" for ordinal in range(1, min(count, 2) + 1)]
    if count == 3:
        material["tasks"].append({
            **material["tasks"][0], "id": "T-002", "ordinal": 2,
            "dependencies": ["T-001"], "acceptance": ["AC3"],
        })
    if count > 1:
        material["source"] = {
            key: subprocess.check_output(
                ["git", "rev-parse", ref], cwd=checkout,
                text=True, encoding="utf-8").strip()
            for key, ref in (("commit", "HEAD"), ("tree", "HEAD^{tree}"))
        }
    handoff = phase_handoff.create_phase_handoff(**material)
    if handoff != original:
        phase_handoff.publish_phase_handoff(checkout, handoff)
        _commit(checkout, "publish multi-obligation Build handoff")
    return checkout, handoff


@pytest.mark.parametrize("obligation_count", [1, 2, 3])
def test_one_build_execution_preserves_every_completion_through_export(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        obligation_count: int) -> None:
    checkout, handoff = _expanded_checkout(tmp_path, obligation_count)
    assignment = phase_pickup.prepare(str(checkout), handoff)
    task = assignment["task"]
    source = checkout / "taskplane" / "phase_handoff.py"
    source.write_text(source.read_text(encoding="utf-8") +
                      "\n# one Build execution covers its sealed criteria\n",
                      encoding="utf-8")
    subprocess.run(["git", "add", "taskplane/phase_handoff.py"],
                   cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "implement the first Build task"],
                   cwd=checkout, check=True)
    executions = []
    run_build_c = phase_pickup.build_c.run_phase_pickup

    def observe_build_c(*args, **kwargs):
        executions.append(args[1]["id"])
        return run_build_c(*args, **kwargs)

    monkeypatch.setattr(phase_pickup.build_c, "run_phase_pickup", observe_build_c)
    result = phase_pickup.submit_committed(
        str(checkout), handoff, task_id=task["id"])

    receipts = result.get("progress_receipts", [result["progress_receipt"]])
    assert executions == ["T-001"]
    assert [row["obligation_id"] for row in receipts] == task["acceptance"]
    predecessor = handoff["lineage"]["predecessor_receipt_head"]
    for sequence, receipt in enumerate(
            receipts, len(handoff["progress_receipts"]) + 1):
        assert receipt["sequence"] == sequence
        assert receipt["predecessor_receipt_fingerprint"] == predecessor
        assert receipt["task_id"] == task["id"]
        assert receipt["checkpoint_receipt_digest"] == \
            result["checkpoint_receipt_digest"]
        assert receipt["integration_receipt_fingerprint"] == \
            result["integration_receipt_fingerprint"]
        assert receipt["fingerprint"] == phase_handoff.receipt_fingerprint(receipt)
        predecessor = receipt["fingerprint"]
    assert result["progress_receipt"] == receipts[-1]
    assert result["lineage"]["receipt_head"] == predecessor
    assert not {"lease", "contract_bootstrap", "assignment", "authoring_result"} \
        & result.keys()
    if obligation_count == 1:
        assert "progress_receipts" not in result

    exported = cli._phase_publish_build_result(str(checkout), handoff, result)
    _commit(checkout, "publish Build completion receipts")
    successor = phase_handoff.load_phase_handoff(
        checkout, exported["next_handoff"]["path"])
    assert successor["progress"]["completed"] == task["acceptance"]
    assert successor["progress_receipts"][:len(handoff["progress_receipts"])] \
        == handoff["progress_receipts"]
    assert successor["progress_receipts"][
        len(handoff["progress_receipts"]):len(handoff["progress_receipts"]) +
        len(receipts)] == receipts
    if obligation_count == 3:
        assert successor["progress"]["remaining"] == ["AC3"]
        assert successor["successor"] == {
            "phase": "build", "mode": "same-phase-resume"}
        resumed = phase_pickup.prepare(str(checkout), successor)
        assert resumed["task"]["id"] == "T-002"
        assert resumed["task"]["acceptance"] == ["AC3"]
    else:
        assert successor["progress"]["remaining"] == []
        assert successor["successor"] == {
            "phase": "terminal", "mode": "terminal-evidence"}
