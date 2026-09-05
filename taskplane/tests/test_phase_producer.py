"""Native lifecycle observes phase output bytes; it does not judge their meaning."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from taskplane import phase_handoff, taskplane_lite as kernel


def _worker(tmp_path, phase="design", *, lens=None):
    from taskplane import phase_producer

    name = "tp_step_" + phase + "_fixture_deadbeef"
    template = {
        "schema": "taskplane.phase-worker-result/v1", "phase": phase,
        "worker_id": phase + "-owner", "attempt_id": "phase-fixture",
        "handoff_fingerprint": "a" * 64, "subject_fingerprint": "b" * 64,
    }
    brief = {"protocol": "repository-phase", "phase": phase,
             "task_name": name, "task_slot": name, "result_template": template,
             "producer_contract": {key: value for key, value in template.items()
                                   if key not in {"schema", "worker_id"}},
             "output_paths": phase_handoff.phase_output_paths(phase)}
    if lens:
        output = f"{phase}/lenses/{lens}.json"
        brief.update({"protocol": "repository-phase-review", "lens": lens,
                      "output": output, "result_path": output, "output_paths": [output],
                      "result_template": {"schema": "taskplane.phase-lens-result/v1",
                          "phase": phase, "lens": lens, "worker_identity": name,
                          "team_plan_fingerprint": "d" * 64, "candidate_fingerprint": "e" * 64}})
        brief["producer_contract"]["result_path"] = output
    contract = kernel.build_contract(
        "PHASE " + phase, read_only=True,
        write_allow=[*brief["output_paths"], *(["design/visual.html"] if phase == "design" else [])])
    contract.update({"task_id": name, "phase_dispatch": copy.deepcopy(brief),
                     "phase_handoff_fingerprint": "a" * 64})
    contract = phase_producer.bind_output_submission(str(tmp_path), contract, brief)
    contract = kernel.prepare_worker_contract(
        str(tmp_path), contract, stage="phase-" + phase + ("-review" if lens else ""),
        task="c" * 64 + (":phase-fixture:" + lens if lens else ""),
        task_name=name, role_marker="taskplane-role:tp-" + (
            "designer" if phase == "design" else "planner"), now=10)
    kernel.activate(str(tmp_path), contract, task_slot_override=name)
    event = {"hook_event_name": "SubagentStart", "cwd": str(tmp_path),
             "session_id": "fixture-session", "agent_id": "fixture-child",
             "task_name": name, "agent_type": name}
    kernel.bind_worker_contract_event(str(tmp_path), event, now=11)
    return kernel.load_active_for_event(str(tmp_path), event), event, brief


def _write(tmp_path, phase, *, visual=False):
    primary, narrative = phase_handoff.phase_output_paths(phase)[:2]
    payload = {"fixture": "synthetic output, not a substantive phase judgment"}
    if visual:
        payload["visualization"] = {"required": True, "path": "design/visual.html"}
    (tmp_path / phase).mkdir(exist_ok=True)
    (tmp_path / primary).write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / narrative).write_text("# Fixture narrative\n", encoding="utf-8")
    if visual:
        (tmp_path / "design/visual.html").write_text("<p>Fixture visual</p>", encoding="utf-8")


def _references(tmp_path, phase, *, visual=False):
    paths = [(path, kind, media) for path, kind, media in (
        (phase_handoff.phase_output_paths(phase)[0], phase, "application/json"),
        (phase_handoff.phase_output_paths(phase)[1], phase + "-narrative", "text/markdown"))]
    if visual:
        paths.append(("design/visual.html", "design-visual", "text/html"))
    refs = []
    for path, kind, media in paths:
        raw = (tmp_path / path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        refs.append({"schema": phase_handoff.ARTIFACT_REFERENCE_SCHEMA,
                     "kind": kind, "digest": digest, "bytes": len(raw),
                     "media_type": media, "destination": phase_handoff.artifact_destination(digest),
                     "locator": "repo-artifact://sha256/" + digest})
    return refs


@pytest.mark.parametrize("phase", ["design", "plan"])
def test_stop_requires_phase_files_without_orchestrator_result_deadlock(tmp_path, phase):
    contract, _, brief = _worker(tmp_path, phase)
    missing = kernel.stop_submission_decision(
        str(tmp_path), contract, observed_slot=brief["task_slot"])
    assert missing["required"] and missing["block"]
    assert missing["status"] == "missing"
    _write(tmp_path, phase)
    checked = kernel.stop_submission_decision(
        str(tmp_path), contract, observed_slot=brief["task_slot"])
    assert checked["valid"] and not checked["block"]
    assert not (tmp_path / phase / "result.json").exists()


@pytest.mark.parametrize("phase, visual", [("design", False), ("design", True), ("plan", False)])
def test_signed_terminal_binds_exact_observed_files_before_collection(tmp_path, phase, visual):
    from taskplane import phase_producer

    contract, event, brief = _worker(tmp_path, phase)
    _write(tmp_path, phase, visual=visual)
    refs = _references(tmp_path, phase, visual=visual)
    released = kernel.terminalize_worker_contract(
        str(tmp_path), {**event, "hook_event_name": "SubagentStop"},
        outcome="success", submission_status="valid", now=12)
    receipt = released["terminal_receipt"]
    assert receipt["owner"]["agent_id"] == "fixture-child"
    assert receipt["phase_output"]["status"] == "observed"
    phase_producer.verify_output_observation(receipt, brief, refs)
    archived = json.loads(Path(released["quarantine"]).read_text(encoding="utf-8"))
    action = archived["worker_lifecycle"]["release_action"]
    kernel._verify_worker_terminal_receipt(str(tmp_path), brief["task_slot"], receipt, archived, action)
    tampered = copy.deepcopy(receipt)
    tampered["phase_output"]["artifacts"][0]["digest"] = "d" * 64
    with pytest.raises(kernel.StateError, match="signature"):
        kernel._verify_worker_terminal_receipt(
            str(tmp_path), brief["task_slot"], tampered, archived, action)
    for index in range(len(refs)):
        stale = copy.deepcopy(refs)
        stale[index]["digest"] = "e" * 64
        stale[index]["destination"] = phase_handoff.artifact_destination("e" * 64)
        stale[index]["locator"] = "repo-artifact://sha256/" + "e" * 64
        with pytest.raises(ValueError, match="observed output"):
            phase_producer.verify_output_observation(receipt, brief, stale)
    foreign = copy.deepcopy(brief)
    foreign["result_template"]["attempt_id"] = "foreign-attempt"
    with pytest.raises(ValueError, match="observed output"):
        phase_producer.verify_output_observation(receipt, foreign, refs)
    assert not Path(kernel.active_contract_path(str(tmp_path), contract["task_slot"])).exists()


@pytest.mark.parametrize("problem", ["empty", "malformed", "symlink", "missing-visual", "foreign-visual"])
def test_stop_refuses_unusable_or_unsafe_phase_output(tmp_path, problem):
    contract, _, brief = _worker(tmp_path)
    _write(tmp_path, "design", visual="visual" in problem)
    primary = tmp_path / "design/contract.json"
    if problem == "empty":
        (tmp_path / "design/design.md").write_text(" ", encoding="utf-8")
    elif problem == "malformed":
        primary.write_text("{", encoding="utf-8")
    elif problem == "symlink":
        primary.rename(tmp_path / "foreign.json")
        primary.symlink_to(tmp_path / "foreign.json")
    elif problem == "missing-visual":
        (tmp_path / "design/visual.html").unlink()
    elif problem == "foreign-visual":
        payload = json.loads(primary.read_text(encoding="utf-8"))
        payload["visualization"]["path"] = "design/other.html"
        primary.write_text(json.dumps(payload), encoding="utf-8")
    result = kernel.stop_submission_decision(str(tmp_path), contract,
                                             observed_slot=brief["task_slot"])
    assert result["required"] and result["block"] and not result["valid"]


@pytest.mark.parametrize("outcome", ["success", "failure", "cancellation", "interruption", "handoff"])
def test_missing_phase_output_cannot_become_success_but_terminal_cleanup_survives(tmp_path, outcome):
    from taskplane import phase_producer

    _, event, brief = _worker(tmp_path)
    released = kernel.terminalize_worker_contract(
        str(tmp_path), event, outcome=outcome, submission_status="valid", now=12)
    receipt = released["terminal_receipt"]
    assert released["released"] is True
    assert receipt["outcome"] == ("failure" if outcome == "success" else outcome)
    assert receipt["phase_output"]["status"] == "missing"
    with pytest.raises(ValueError, match="observed output"):
        phase_producer.verify_output_observation(receipt, brief, [])


def test_foreign_native_owner_cannot_observe_output_or_mint_terminal_receipt(tmp_path):
    contract, event, _ = _worker(tmp_path)
    _write(tmp_path, "design")
    with pytest.raises(kernel.StateError, match="owner"):
        kernel.record_worker_terminal(str(tmp_path), contract["task_slot"],
            event={**event, "agent_id": "another-child"}, outcome="success",
            submission_status="valid", now=12)
    assert not Path(kernel._worker_terminal_path(str(tmp_path), contract["task_slot"])).exists()


@pytest.mark.parametrize("phase", ["design", "plan"])
def test_phase_lens_reuses_stop_and_signed_terminal_for_one_exact_output(tmp_path, phase):
    from taskplane import phase_producer

    contract, event, brief = _worker(tmp_path, phase, lens="security")
    assert kernel.stop_submission_decision(str(tmp_path), contract)["block"]
    output = tmp_path / brief["result_path"]
    output.parent.mkdir(parents=True)
    result = {**brief["result_template"], "outcome": "pass", "findings": []}
    result["fingerprint"] = phase_handoff.canonical_fingerprint(result)
    output.write_text(json.dumps(result), encoding="utf-8")
    assert kernel.stop_submission_decision(str(tmp_path), contract)["valid"]
    assert not (tmp_path / phase_handoff.phase_output_paths(phase)[0]).exists()
    receipt = kernel.terminalize_worker_contract(str(tmp_path), event, outcome="success",
        submission_status="valid", now=12)["terminal_receipt"]
    raw = output.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    refs = [{"schema": phase_handoff.ARTIFACT_REFERENCE_SCHEMA,
             "kind": phase + "-lens-security", "media_type": "application/json",
             "digest": digest, "bytes": len(raw),
             "destination": phase_handoff.artifact_destination(digest),
             "locator": "repo-artifact://sha256/" + digest}]
    phase_producer.verify_output_observation(receipt, brief, refs)
    assert len(receipt["phase_output"]["artifacts"]) == 1
    for field in ("team_plan_fingerprint", "candidate_fingerprint"):
        foreign = copy.deepcopy(brief)
        foreign["result_template"][field] = "f" * 64
        with pytest.raises(ValueError, match="observed output"):
            phase_producer.verify_output_observation(receipt, foreign, refs)
    foreign = copy.deepcopy(brief)
    foreign["producer_contract"]["attempt_id"] = "another-attempt"
    with pytest.raises(ValueError, match="observed output"):
        phase_producer.verify_output_observation(receipt, foreign, refs)


def test_phase_receipt_extension_is_closed_and_legacy_receipts_stay_compatible(tmp_path):
    from taskplane import phase_producer
    from taskplane.tests.test_worker_contract_lifecycle import _active_worker, _event

    contract = _active_worker(tmp_path)
    event = _event(tmp_path)
    kernel.bind_worker_contract_event(str(tmp_path), event, now=11)
    receipt = kernel.record_worker_terminal(str(tmp_path), contract["task_slot"], event=event,
        outcome="success", submission_status="not_required", now=12)
    assert set(receipt) == kernel._WORKER_TERMINAL_FIELDS
    action = contract["worker_lifecycle"]["release_action"]
    kernel._verify_worker_terminal_receipt(
        str(tmp_path), contract["task_slot"], receipt, contract, action)
    receipt["phase_output"] = {"schema": phase_producer.OBSERVATION_SCHEMA}
    authority = kernel._worker_contract_authority(str(tmp_path), create=False)
    receipt["signature"] = kernel._worker_signature(authority["secret"], receipt)
    with pytest.raises(kernel.StateError, match="foreign phase output"):
        kernel._verify_worker_terminal_receipt(
            str(tmp_path), contract["task_slot"], receipt, contract, action)


def test_legacy_phase_receipt_without_hashes_cannot_retroactively_prove_current_bytes(tmp_path):
    from taskplane import phase_producer

    contract, event, brief = _worker(tmp_path)
    _write(tmp_path, "design")
    receipt = kernel.record_worker_terminal(str(tmp_path), contract["task_slot"], event=event,
        outcome="success", submission_status="valid", now=12)
    action = contract["worker_lifecycle"]["release_action"]
    authority = kernel._worker_contract_authority(str(tmp_path), create=False)
    malformed = copy.deepcopy(receipt)
    malformed["phase_output"]["extra"] = "not an admitted field"
    malformed["signature"] = kernel._worker_signature(authority["secret"], malformed)
    with pytest.raises(kernel.StateError, match="phase output is malformed"):
        kernel._verify_worker_terminal_receipt(
            str(tmp_path), contract["task_slot"], malformed, contract, action)
    receipt.pop("phase_output")
    receipt["signature"] = kernel._worker_signature(authority["secret"], receipt)
    kernel._verify_worker_terminal_receipt(
        str(tmp_path), contract["task_slot"], receipt, contract, action)
    with pytest.raises(ValueError, match="observed output"):
        phase_producer.verify_output_observation(receipt, brief, _references(tmp_path, "design"))
