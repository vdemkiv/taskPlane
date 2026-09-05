"""Current-attempt orchestration using explicitly synthetic native lifecycle events."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from taskplane import phase_admission, phase_dispatch, phase_handoff, phase_review_host
from taskplane import taskplane_lite as kernel
from taskplane.tests.test_stateless_phase_pickup import _authority_chain, _git, _published_checkout


def _fixture(tmp_path, phase="design", *, visual=False):
    checkout, handoff = _published_checkout(tmp_path, phase)
    rid = handoff["requirement"]["id"]
    requirement = {"id": rid, "acceptance": [row["criterion"] for row in handoff["acceptance"]],
        "contracts": handoff["contracts"], "depends_on": [], "open_questions": [],
        "context_files": ["taskplane/phase_handoff.py"]}
    graph = {"modules": {"taskplane": {}}, "edges": [], "meta": {"content_fingerprint": "a" * 64}}
    design = {"requirement": rid, "summary": "A small modular recovery boundary.",
              "selected_approach": "Share the existing checks"}
    if visual:
        design["visualization"] = {"required": True, "path": "design/visual.html"}
    plan = {"requirement": rid, "tasks": [{"id": "T-001", "req": rid,
        "scope": ["taskplane/phase_handoff.py"], "tests": "pytest exact_selector",
        "criteria": requirement["acceptance"], "deps": []}]}
    artifact = design if phase == "design" else plan
    records = {"exports/pickup/requirement-input.json": requirement,
               "exports/pickup/graph-input.json": graph,
               "design/contract.json": design}
    records[phase_handoff.phase_output_paths(phase)[0]] = artifact
    for path, record in records.items():
        (checkout / path).parent.mkdir(parents=True, exist_ok=True)
        (checkout / path).write_text(json.dumps(record), encoding="utf-8")
    (checkout / phase_handoff.phase_output_paths(phase)[1]).write_text(
        "# Synthetic authored candidate fixture\n", encoding="utf-8")
    if visual:
        (checkout / "design/visual.html").write_text("<p>Fixture visual</p>", encoding="utf-8")
    _git(checkout, "add", "-f", "--", *records, phase_handoff.phase_output_paths(phase)[1],
         *(["design/visual.html"] if visual else []))
    _git(checkout, "commit", "-qm", "prepare explicit focused review fixture inputs")
    refs = [phase_handoff.create_repository_artifact_reference(checkout, path, kind=kind)
            for kind, path in (("requirement", "exports/pickup/requirement-input.json"),
                               ("graph", "exports/pickup/graph-input.json"))]
    if phase == "plan":
        refs.append(phase_handoff.create_repository_artifact_reference(
            checkout, "design/contract.json", kind="design"))
    material = {key: copy.deepcopy(value) for key, value in handoff.items()
                if key not in {"schema", "handoff_id", "fingerprint"}}
    material["source"] = {"commit": _git(checkout, "rev-parse", "HEAD"),
                          "tree": _git(checkout, "rev-parse", "HEAD^{tree}")}
    material["requirement"] = {"id": rid, "fingerprint": refs[0]["digest"], "artifact": refs[0]}
    gates = [("initial-authorization", refs[0]["digest"])]
    if phase == "plan":
        material["design"] = {"fingerprint": refs[2]["digest"], "artifact": refs[2]}
        gates.append(("design-approval", refs[2]["digest"]))
    material["selected_artifacts"] = sorted(refs, key=lambda row: (row["kind"], row["digest"]))
    material["authority_receipts"] = _authority_chain(handoff["repository"]["id"],
        material["source"]["commit"], material["source"]["tree"], gates)
    handoff = phase_handoff.create_phase_handoff(**material)
    phase_handoff.publish_phase_handoff(checkout, handoff)
    _git(checkout, "add", "-f", "exports/pickup")
    _git(checkout, "commit", "-qm", "seal focused review fixture input")
    owner = phase_dispatch.worker_brief(handoff,
        kernel.stateless_phase_startup(handoff, attempt_id="attempt-review-host-fixture"))
    references = phase_dispatch.output_references(str(checkout), phase)
    paths = phase_handoff.phase_output_paths(phase)[:2] + (["design/visual.html"] if visual else [])
    content = {reference["kind"]: (checkout / path).read_bytes()
               for path, reference in zip(paths, references)}
    return str(checkout), handoff, owner, artifact, references, content


def _finish(fixture, dispatched, *, outcome="pass", omit_fingerprint=False):
    """Synthetic start/stop fixtures exercise signing, never real host behavior."""
    workspace = fixture[0]
    for index, brief in enumerate(dispatched["dispatches"]):
        event = {"session_id": "fixture-session", "agent_id": f"fixture-child-{index}",
                 "task_name": brief["task_name"]}
        kernel.bind_worker_contract_event(workspace, event)
        result = {**brief["result_template"], "outcome": outcome, "findings": [],
                  "evidence": "Synthetic result protocol fixture, not native review evidence."}
        if not omit_fingerprint:
            result["fingerprint"] = phase_handoff.canonical_fingerprint(result)
        path = Path(workspace) / brief["result_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result), encoding="utf-8")
        kernel.terminalize_worker_contract(workspace, event, outcome="success", submission_status="valid")


@pytest.mark.parametrize("phase", ["design", "plan"])
def test_current_attempt_preparation_is_idempotent_without_predecessor_reads(tmp_path, monkeypatch, phase):
    fixture = _fixture(tmp_path, phase)
    def predecessor(*_args, **_kwargs):
        pytest.fail("review host read predecessor loop")
    monkeypatch.setattr(phase_review_host.phase_review.loop, "load", predecessor)
    monkeypatch.setattr(phase_review_host.phase_review.loop, "mutate", predecessor)
    first = phase_review_host.prepare(*fixture)
    before = {slot: kernel.load_json(kernel.active_contract_path(fixture[0], slot))
              for slot in kernel.list_task_slots(fixture[0])}
    second = phase_review_host.prepare(*fixture)
    assert first == second and first["status"] == "ready"
    assert all(row["dispatch_allowed"] for row in first["dispatches"])
    after = {slot: kernel.load_json(kernel.active_contract_path(fixture[0], slot)) for slot in before}
    assert before == after
    queue = kernel.load_json(str(Path(kernel.tp_dir(fixture[0])) / "expected_dispatch.json"))
    assert len(queue) == len(first["dispatches"])
    assert all(row["intent_id"] for row in queue)


def test_active_native_child_is_not_replaced_or_redispatched(tmp_path):
    fixture = _fixture(tmp_path)
    first = phase_review_host.prepare(*fixture)
    for index, brief in enumerate(first["dispatches"]):
        kernel.bind_worker_contract_event(fixture[0], {
            "session_id": "fixture-session", "agent_id": f"fixture-active-{index}",
            "task_name": brief["task_name"]})
    second = phase_review_host.prepare(*fixture)
    assert second["status"] == "working"
    assert all(row["activation"] == "active" and not row["dispatch_allowed"]
               for row in second["dispatches"])
    with pytest.raises(ValueError, match="waiting for every selected"):
        phase_review_host.collect(*fixture)


@pytest.mark.parametrize("phase,outcome", [("design", "pass"), ("plan", "changes-required")])
def test_exact_signed_children_collect_once_and_preserve_judgment(tmp_path, phase, outcome):
    fixture = _fixture(tmp_path, phase)
    dispatched = phase_review_host.prepare(*fixture)
    with pytest.raises(ValueError, match="waiting for every selected"):
        phase_review_host.collect(*fixture)
    _finish(fixture, dispatched, outcome=outcome)
    collection = phase_review_host.collect(*fixture)
    assert collection["status"] == outcome
    assert len(collection["route"]["dispositions"]) == 26
    assert len(collection["results"]) == len(dispatched["dispatches"])
    assert collection["human_approval"] is False
    replay = phase_review_host.prepare(*fixture)
    assert replay["status"] == "completed" and replay["dispatches"] == []
    assert phase_review_host.collect(*fixture) == collection


@pytest.mark.parametrize("case", ["changed-result", "unsigned-terminal", "missing-result"])
def test_collection_refuses_post_stop_changes_and_missing_proof(tmp_path, case):
    fixture = _fixture(tmp_path)
    dispatched = phase_review_host.prepare(*fixture)
    _finish(fixture, dispatched)
    brief = dispatched["dispatches"][0]
    output = Path(fixture[0]) / brief["result_path"]
    if case == "changed-result":
        output.write_text(output.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    elif case == "missing-result":
        output.unlink()
    else:
        path = Path(kernel._worker_terminal_path(fixture[0], brief["task_slot"]))
        value = json.loads(path.read_text(encoding="utf-8"))
        value["signature"] = "f" * 64
        path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises((ValueError, kernel.StateError)):
        phase_review_host.collect(*fixture)


@pytest.mark.parametrize("case", ["cached-route", "widened-contract", "candidate", "symlink"])
def test_preparation_refuses_changed_current_attempt_without_overwriting_slots(tmp_path, case):
    fixture = _fixture(tmp_path)
    dispatched = phase_review_host.prepare(*fixture)
    brief = dispatched["dispatches"][0]
    slot_path = Path(kernel.active_contract_path(fixture[0], brief["task_slot"]))
    cache_path = Path(fixture[0]) / ".taskplane/phase-reviews/attempt-review-host-fixture.json"
    altered = list(fixture)
    if case == "cached-route":
        value = json.loads(cache_path.read_text(encoding="utf-8"))
        value["plan"]["selected"] = []
        cache_path.write_text(json.dumps(value), encoding="utf-8")
    elif case == "widened-contract":
        value = json.loads(slot_path.read_text(encoding="utf-8"))
        value["write_allow"].append("taskplane/**")
        slot_path.write_text(json.dumps(value), encoding="utf-8")
    elif case == "candidate":
        altered[5] = {**fixture[5], "design-narrative": b"changed candidate"}
    else:
        foreign = tmp_path / "foreign-cache.json"
        cache_path.rename(foreign)
        cache_path.symlink_to(foreign)
    before = slot_path.read_bytes()
    with pytest.raises(ValueError):
        phase_review_host.prepare(*altered)
    assert slot_path.read_bytes() == before


def test_nonfirst_child_hook_resolution_rebuilds_actual_selected_candidate(tmp_path):
    fixture = _fixture(tmp_path)
    dispatched = phase_review_host.prepare(*fixture)
    brief = dispatched["dispatches"][1]
    contract = kernel.load_json(kernel.active_contract_path(fixture[0], brief["task_slot"]))
    canonical = phase_review_host.validate_dispatch(fixture[0], fixture[1], contract)
    assert canonical["task_name"] == brief["task_name"]
    queue = kernel.load_json(str(Path(kernel.tp_dir(fixture[0])) / "expected_dispatch.json"))
    expected = next(row for row in queue if row["task_name"] == brief["task_name"])
    resolved = phase_admission.resolve_expected(fixture[0], expected, native_task_name=brief["task_name"])
    assert resolved["brief"] == canonical


def test_required_visual_is_bound_and_survives_exact_child_collection(tmp_path):
    fixture = _fixture(tmp_path, visual=True)
    dispatched = phase_review_host.prepare(*fixture)
    assert all(row["candidate_artifacts"][-1]["kind"] == "design-visual"
               for row in dispatched["dispatches"])
    _finish(fixture, dispatched)
    assert phase_review_host.collect(*fixture)["status"] == "pass"


@pytest.mark.parametrize("outcome", ["pass", "changes-required"])
def test_terminal_observed_hashless_lens_results_are_sealed_only_in_memory(tmp_path, outcome):
    fixture = _fixture(tmp_path)
    dispatched = phase_review_host.prepare(*fixture)
    _finish(fixture, dispatched, outcome=outcome, omit_fingerprint=True)
    before = {row["lens"]: (Path(fixture[0]) / row["result_path"]).read_bytes()
              for row in dispatched["dispatches"]}
    collection = phase_review_host.collect(*fixture)
    assert collection["status"] == outcome and collection["human_approval"] is False
    for row in collection["results"]:
        raw = before[row["lens"]]
        original = json.loads(raw)
        assert "fingerprint" not in original
        assert row["result"] == {**original, "fingerprint": phase_handoff.canonical_fingerprint(original)}
        brief = next(brief for brief in dispatched["dispatches"] if brief["lens"] == row["lens"])
        assert (Path(fixture[0]) / brief["result_path"]).read_bytes() == raw
