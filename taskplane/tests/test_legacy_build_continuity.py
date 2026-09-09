"""Legacy saved-shape regressions; generated data and temporary stores only."""
import copy
import hashlib
import json
from pathlib import Path
from contextlib import contextmanager
import sys
import subprocess
from types import SimpleNamespace

import pytest

from taskplane import delivery_policy, loop, loop_recovery, run_context, settings

REAL_GIT_HEAD = loop.tp.git_head
REAL_WORKSPACE_FINGERPRINT = loop.tp.workspace_fingerprint
REAL_GET_REQUIREMENT = loop.reqs.get_requirement


@pytest.fixture
def publication_amendment(legacy, monkeypatch, request):
    ws, root, _, packet = legacy
    monkeypatch.setattr(loop.reqs, "get_requirement", REAL_GET_REQUIREMENT)
    criterion = ("FP-AC17 J6: real finalization: On a supported real host, production Build output "
        "provides full accessible evidence to fresh Evaluate, independent Engineering review, "
        "applicable scoped human sign-off, Retro, and the declared Release/Publish/final outcome. "
        "Verification: Missing downstream evidence/authority blocks at its boundary. "
        "Prove PR-based delivery and separately authorized publication; Build-only results, documents, "
        "synthetic terminals, old approval, or skips cannot satisfy the journey.")
    requirement = loop.reqs.record_requirement(ws, "Original requirement", functional=["works"],
        acceptance=["J1 requires genuine native evidence", criterion],
        nfr={"security":"no new trust boundary", "architecture":"local and reversible"},
        contracts=[{"id":"contract:required", "relation":"changes"}])
    state = loop._load_raw(ws)
    original_plan = packet["before_plan"]
    amended_plan = json.loads((root / "plan/tasks.json").read_text())
    for i in (11, 14):
        for target in (state["tasks"][i], original_plan["tasks"][i], amended_plan["tasks"][i]):
            target.update(criteria=[criterion], acceptance_refs=[criterion])
    (root / "design").mkdir()
    design = {"requirement":"R-0001"}
    if getattr(request, "param", None) == "conformance":
        design["graph"] = {"proposed_modules": [], "proposed_edges": [
            {"from":".github/workflows", "to":"contract:taskplane.stage-handoff/v2", "kind":"consumes"},
            {"from":"taskplane", "to":"contract:ordinary", "kind":"provides"}]}
    (root / "design/contract.json").write_text(json.dumps(design))
    (root / "design/design.md").write_text("Original generated Design; publication authorization remains separate.")
    state["design_required"] = True
    state["design_fingerprint"] = loop._design_evidence_fingerprint(ws)
    original_plan["design_fingerprint"] = amended_plan["design_fingerprint"] = state["design_fingerprint"]
    policy = state["review_timing_override"]
    policy = sealed({**{k:v for k,v in policy.items() if k != "fingerprint"},
        "plan_fingerprint":fingerprint(original_plan), "design_fingerprint":state["design_fingerprint"]})
    state["review_timing_override"] = policy
    for task in state["tasks"][2:19]: task["evaluation"]["policy_fingerprint"] = policy["fingerprint"]
    state["delivery_mode_receipt"] = delivery_policy.validate_plan_mode(original_plan,
        plan_fingerprint=fingerprint(original_plan), source_sha="a" * 40)
    loop.save(ws, state)
    (root / "plan/tasks.json").write_text(json.dumps(amended_plan, indent=2) + "\n")
    packet.update(before_state_fingerprint=loop_recovery.legacy_state_fingerprint(state),
        design_fingerprint=state["design_fingerprint"],
        after_plan_sha256=hashlib.sha256((root / "plan/tasks.json").read_bytes()).hexdigest())
    packet["requirement_fingerprint"] = loop_recovery._requirement_fingerprint(requirement)
    assert invoke(legacy)["continued"] is True
    (root / ".gitignore").write_text(".taskplane/\n.eval/\n")
    for command in (["git", "init", "-q"], ["git", "add", "."],
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline"]):
        subprocess.run(command, cwd=ws, check=True, capture_output=True)
    monkeypatch.setattr(loop.tp, "git_head", REAL_GIT_HEAD)
    monkeypatch.setattr(loop.tp, "workspace_fingerprint", REAL_WORKSPACE_FINGERPRINT)
    state = loop._load_raw(ws)
    state.update(step="evaluate", _build_failed=True, submission_required=True)
    state["goal"] = "generated publication-only continuity"
    state["tasks"][19]["failure_routing"] = loop._detected_build_failure_routing(ws,
        state["tasks"][19], {"outcome":"fail", "fingerprint":REAL_WORKSPACE_FINGERPRINT(ws)}, "execute")
    state["review_kernel_runs"] = {"evaluate:T19":{"run_id":"e" * 32, "workspace":ws}}
    loop.save(ws, state)
    initial_contract = loop.tp.build_contract("Evaluate", read_only=True)
    initial_contract["producer_dispatch"] = {"run_id":"e" * 32, "task_id":"T19", "stage":"evaluate"}
    contract = loop.tp.prepare_worker_contract(ws, initial_contract,
        stage="evaluate", task="T19", task_name="tp_step_evaluator_t19_test", role_marker="taskplane-role:tp-evaluator")
    contract = loop.tp.bind_submission_contract(contract, ws, task="T19", stage="evaluate",
        slot=contract["task_slot"], locator={"type":"loop_submission"}, validation_rule="loop-submission/v1")
    loop.tp.activate(ws, contract, snapshot=REAL_GIT_HEAD(ws), task_slot_override=contract["task_slot"])
    event = {"cwd":ws, "session_id":"test-root", "agent_id":"test-child", "task_name":"tp_step_evaluator_t19_test"}
    loop.tp.bind_worker_contract_event(ws, event)
    (root / ".eval").mkdir()
    (root / ".eval/verdict.json").write_text('{"verdict":"fail","historical":true}')
    assert loop.submit.__wrapped__(ws, "fail", note="historical adverse classifier")["submitted"]
    loop.tp.terminalize_worker_contract(ws, event, outcome="failure", submission_status="producer_error")
    approval = root / ".taskplane/publication-approval.md"
    original = root / ".taskplane/original-approval.md"
    approval.write_text("human:test: publication-only post-merge sequencing approved; review and CI mandatory")
    original.write_text("human:test: original approved scope retained")
    artifact = lambda path:{"path":str(path), "sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
    prepared = loop_recovery.prepare_publication_amendment(loop, ws, by="human:test",
        request="Move publication only to separately authorized post-merge gate", approval=artifact(approval),
        original_approval=artifact(original), terminal_slot=contract["task_slot"])
    source = root / ".taskplane/publication-amendment.json"
    source.write_text(json.dumps(prepared))
    return ws, root, prepared, source


def apply_publication(fixture, **kwargs):
    ws, _, packet, source = fixture
    return loop.amend_delivery(ws, source=str(source), by=kwargs.pop("by", "human:test"),
        request=packet["request"], expected_fingerprint=loop_recovery._fingerprint(packet), **kwargs)


@pytest.mark.parametrize("publication_amendment", ["conformance"], indirect=True)
def test_publication_conformance_defers_only_authenticated_pending_edge(publication_amendment, monkeypatch):
    ws, root, _, _ = publication_amendment
    assert apply_publication(publication_amendment).get("amended")
    state = loop._load_raw(ws)
    contract = json.loads((root / "design/contract.json").read_text())
    edges = contract["graph"]["proposed_edges"]
    keys = [loop._dc.edge_key(row) for row in edges]
    meta = {"design": {"fingerprint":state["design_fingerprint"], "verdict":"conformant",
        "modules_checked":[], "edges_checked":keys, "contracts_checked":[], "drift":[],
        "edge_evidence":[{"edge":keys[1], "evidence":"existing implementation", "declared_by":"test"}]}}
    monkeypatch.setattr(loop._dc.depgraph, "load", lambda ws:{"modules":{}, "edges":[edges[1]]})
    before = (root / "design/contract.json").read_bytes()
    assert not loop._design_review_errors(ws, state, meta)
    assert any("pending post-merge" in notice for notice in loop._design_review_notices(ws, state, meta))
    assert (root / "design/contract.json").read_bytes() == before
    unamended = copy.deepcopy(state)
    unamended.pop("legacy_publication_amendment")
    assert any(keys[0] in error for error in loop._design_review_errors(ws, unamended, meta))
    monkeypatch.setattr(loop._dc.depgraph, "load", lambda ws:{"modules":{}, "edges":[]})
    assert any(keys[1] in error for error in loop._design_review_errors(ws, state, meta))
    for field, value in (("phase", "prepared"), ("actor", "human:foreign"), ("revoked", True)):
        damaged = copy.deepcopy(state)
        damaged["legacy_publication_amendment"][field] = value
        assert loop._design_review_errors(ws, damaged, meta)


def test_publication_amendment_preserves_failed_build_and_all_retained_work(publication_amendment):
    ws, root, packet, _ = publication_amendment
    before = loop._load_raw(ws)
    files = {p:p.read_bytes() for p in root.rglob("*") if p.is_file()}
    checked = apply_publication(publication_amendment, check=True)
    assert checked.get("checked"), checked
    assert loop._load_raw(ws) == before
    assert {p:p.read_bytes() for p in files} == files
    applied = apply_publication(publication_amendment)
    assert applied.get("amended"), applied
    after = loop._load_raw(ws)
    assert after["step"] == "evaluate" and after["_build_failed"] is True
    assert after["tasks"] == before["tasks"]
    assert after["legacy_build_continuation"] == before["legacy_build_continuation"]
    assert after["resource_policy"] == before["resource_policy"]
    assert "_submission" not in after and "evaluate:T19" not in after["review_kernel_runs"]
    journal = after["legacy_publication_amendment"]
    assert journal["historical_submission"] == before["_submission"]
    assert journal["historical_review_binding"] == before["review_kernel_runs"]["evaluate:T19"]
    assert loop.reqs.get_requirement(ws, "R-0001") == packet["after_requirement"]
    assert json.loads((root / "plan/tasks.json").read_text())["tasks"] == json.loads(packet["before_plan_text"])["tasks"]
    assert loop_recovery.legacy_continuation(after, ws)
    assert not loop._design_current_errors(ws, after)
    assert loop._design_evidence_fingerprint(ws) != before["design_fingerprint"]
    lookup = lambda rid:loop.reqs.get_requirement(ws, rid)
    assert loop.tp.requirement_coverage_errors(after["tasks"], lookup), "raw historical references unexpectedly accepted"
    projected = loop.reqs.publication_coverage_tasks(after["tasks"], lookup)
    assert not loop.tp.requirement_coverage_errors(projected, lookup)
    current_only = loop.reqs.publication_coverage_tasks(after["tasks"], lookup, require_passed=True)
    assert any("FP-AC17" in error for error in loop.tp.requirement_coverage_errors(current_only, lookup, require_passed=True))
    assert after["tasks"] == before["tasks"]
    assert packet["after_plan_text"].startswith(packet["before_plan_text"].rstrip()[:-1])
    assert apply_publication(publication_amendment).get("replay") is True
    assert loop._load_raw(ws) == after


@pytest.mark.parametrize("damage", ["acceptance", "task-tests", "task-scope", "retained-result", "foreign-run",
    "foreign-source", "actor", "approval-file", "original-approval", "terminal-missing", "terminal-forged",
    "active-worker", "observation", "current-plan", "current-requirement", "design-drift"])
def test_publication_amendment_refuses_unrelated_or_unproven_changes(publication_amendment, damage):
    ws, root, packet, source = publication_amendment
    if damage == "acceptance": packet["after_requirement"]["acceptance"][0] = "J1 waived"
    if damage in {"task-tests", "task-scope"}:
        plan = json.loads(packet["after_plan_text"])
        plan["tasks"][19]["tests" if damage == "task-tests" else "scope"] = "waived"
        packet["after_plan_text"] = json.dumps(plan)
    if damage == "retained-result":
        with loop.mutate(ws) as state: state["tasks"][0]["evaluation"]["verdict"] = "pass"
    if damage == "foreign-run": packet["run_id"] = "foreign"
    if damage == "foreign-source": (root / "foreign.py").write_text("unapproved source")
    if damage == "actor": packet["by"] = "human:other"
    if damage in {"approval-file", "original-approval"}:
        Path(packet["approval" if damage == "approval-file" else "original_approval"]["path"]).write_text("changed")
    if damage.startswith("terminal-"):
        path = Path(loop.tp._worker_terminal_path(ws, packet["terminal_slot"]))
        if damage == "terminal-missing": path.unlink()
        else:
            terminal = json.loads(path.read_text())
            terminal["outcome"] = "success"
            path.write_text(json.dumps(terminal))
    if damage == "active-worker":
        contract = loop.tp.prepare_worker_contract(ws, loop.tp.build_contract("Other", read_only=True),
            stage="evaluate", task="T19", task_name="tp_step_evaluator_other", role_marker="taskplane-role:tp-evaluator")
        loop.tp.activate(ws, contract, snapshot=REAL_GIT_HEAD(ws), task_slot_override=contract["task_slot"])
    if damage == "observation": packet["observation_checkpoint"] = {"unauthenticated":True}
    if damage == "current-plan": (root / "plan/tasks.json").write_text(packet["before_plan_text"] + " ")
    if damage == "current-requirement": loop.reqs.amend_requirement(ws, "R-0001", functional=["unrelated"])
    if damage == "design-drift": (root / "design/design.md").write_text("unrelated Design change")
    source.write_text(json.dumps(packet))
    before = loop._load_raw(ws)
    result = apply_publication(publication_amendment)
    assert result.get("error"), result
    assert loop._load_raw(ws) == before


@pytest.mark.parametrize("boundary", ["requirement", "plan-before", "plan-after", "source-race"])
def test_publication_amendment_interruption_resumes_exact_journal(publication_amendment, monkeypatch, boundary):
    ws, root, packet, _ = publication_amendment
    apply_req = loop.reqs.apply_publication_sequence
    write = loop.tp.atomic_write_bytes
    def interrupted_req(*args, **kwargs):
        if boundary == "requirement": raise OSError("interrupted requirement owner")
        result = apply_req(*args, **kwargs)
        if boundary == "source-race": (root / "unapproved.py").write_text("changed during amendment")
        return result
    def interrupted_plan(path, *args, **kwargs):
        target = path == str(root / "plan/tasks.json")
        if target and boundary == "plan-before": raise OSError("interrupted Plan owner")
        result = write(path, *args, **kwargs)
        if target and boundary == "plan-after": raise OSError("interrupted after durable Plan")
        return result
    monkeypatch.setattr(loop.reqs, "apply_publication_sequence", interrupted_req)
    monkeypatch.setattr(loop.tp, "atomic_write_bytes", interrupted_plan)
    before = loop._load_raw(ws)
    assert apply_publication(publication_amendment).get("error")
    interrupted = loop._load_raw(ws)
    assert interrupted["legacy_publication_amendment"]["phase"] == "prepared"
    assert interrupted["_submission"] == before["_submission"]
    with pytest.raises(ValueError, match="interrupted pickup"):
        loop_recovery.legacy_continuation(interrupted, ws)
    monkeypatch.setattr(loop.reqs, "apply_publication_sequence", apply_req)
    monkeypatch.setattr(loop.tp, "atomic_write_bytes", write)
    if boundary == "source-race":
        assert apply_publication(publication_amendment, check=True).get("error")
        (root / "unapproved.py").unlink()
    assert apply_publication(publication_amendment, check=True).get("checked")
    assert loop._load_raw(ws) == interrupted
    resumed = apply_publication(publication_amendment)
    assert resumed.get("amended"), resumed
    after = loop._load_raw(ws)
    assert after["tasks"] == before["tasks"]
    assert after["_build_failed"] is True and "_submission" not in after
    assert loop.reqs.get_requirement(ws, "R-0001") == packet["after_requirement"]
    assert apply_publication(publication_amendment).get("replay")
    assert loop._load_raw(ws) == after


def test_publication_amendment_next_reaches_current_independent_preparation(publication_amendment, monkeypatch):
    ws, _, packet, _ = publication_amendment
    assert apply_publication(publication_amendment).get("amended")
    state = loop._load_raw(ws)
    design = loop._design_context(ws, state)
    assert design["approved"] and not design["errors"]
    assert design["fingerprint"] == packet["design_fingerprint"]
    assert design["contract"]["requirement"] == packet["requirement_id"]
    monkeypatch.setattr(loop.tp, "dor_check", lambda *_:(True, [], []))
    monkeypatch.setattr(loop.depgraph, "scan", lambda *_:None)
    monkeypatch.setattr(loop.depgraph, "load", lambda *_:{"modules":{}})
    monkeypatch.setattr(loop, "_diff_files", lambda *_:[])
    monkeypatch.setattr(loop, "status", lambda *_:{})
    monkeypatch.setattr(loop, "_run_artifact_root", lambda *_:ws)
    captured = []
    def preparation(*args, **kwargs):
        captured.append(kwargs)
        raise ValueError("test stops at independent ReviewKernel preparation; no attempt or native evidence minted")
    monkeypatch.setattr(loop, "_review_kernel", preparation)
    result = loop.next_action(ws)
    assert "test stops at independent ReviewKernel preparation" in result.get("error", ""), result
    assert captured and captured[0]["requirement"]["acceptance"] == packet["after_requirement"]["acceptance"]
    assert loop._load_raw(ws)["tasks"] == state["tasks"]


@pytest.mark.parametrize("size", [977211, 1553457, 2000001])
def test_delivery_review_artifact_capacity_preserves_full_diff(legacy, monkeypatch, size):
    from taskplane import review
    patch = "x" * size
    limits = []

    def canonical(*args, max_bytes=400_000, **kwargs):
        limits.append(max_bytes)
        return (review.CANONICAL_DIFF_TOO_LARGE, "") if size > max_bytes else (0, patch)

    class Retained(Exception): pass

    def retain(*args, payload, **kwargs):
        assert payload["patch"] == patch
        raise Retained("full artifact retained, never inlined")

    monkeypatch.setattr(loop, "_review_runtime_modules", lambda: (loop.tp, SimpleNamespace(ArtifactStore=lambda _:None), review))
    monkeypatch.setattr(loop, "_diff_files", lambda *_:["taskplane/loop.py"])
    monkeypatch.setattr(review, "canonical_diff_patch", canonical)
    monkeypatch.setattr(loop, "store_retained_review_diff", retain)
    expected = review.ReviewKernelError if size > 2_000_000 else Retained
    with pytest.raises(expected, match="2000000-byte bound" if size > 2_000_000 else "full artifact"):
        loop._review_kernel(legacy[0], legacy[0], base="c" * 40, step="evaluate",
            task={"id":"T19", "scope":["taskplane/loop.py"]}, graph={}, impact={}, requirement={})
    assert limits == [2_000_000]


def test_legacy_failed_build_gate_retires_worker_as_failure(legacy, monkeypatch):
    ws = legacy[0]
    assert invoke(legacy)["continued"] is True
    state = loop._load_raw(ws)
    state["submission_required"] = False
    loop.save(ws, state)
    released = []
    monkeypatch.setattr(loop.tp, "release_worker_contracts_for_gate",
        lambda *args, **kwargs: released.append(kwargs) or [{"released":True}])
    monkeypatch.setattr(loop, "status", lambda *_:{})
    monkeypatch.setattr(loop.yield_meter, "gate_snapshot", lambda *_:None)
    result = loop.gate(ws, "fail", note="genuine incomplete Build")
    assert not result.get("error"), result
    assert released[-1]["outcome"] == "failure"
    assert released[-1]["submission_status"] == "gated:fail"
    after = loop._load_raw(ws)
    assert after["step"] == "evaluate" and after["_build_failed"] is True
    assert after["tasks"][:19] == state["tasks"][:19]


def test_legacy_evaluate_reports_original_kernel_error_without_attempt_identity(legacy, monkeypatch):
    ws = legacy[0]
    assert invoke(legacy)["continued"] is True
    state = loop._load_raw(ws)
    state.update(step="evaluate", goal="original legacy task", _build_failed=True)
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "dor_check", lambda *_:(True, [], []))
    monkeypatch.setattr(loop.depgraph, "scan", lambda *_:None)
    monkeypatch.setattr(loop.depgraph, "load", lambda *_:{"modules":{}})
    monkeypatch.setattr(loop, "_diff_files", lambda *_:[])
    monkeypatch.setattr(loop, "status", lambda *_:{})
    monkeypatch.setattr(loop, "_run_artifact_root", lambda *_:ws)
    monkeypatch.setattr(loop, "_bind_worker_submission", lambda _ws,_state,_step,contract,_task:contract)

    def failed(*args, **kwargs):
        raise ValueError("canonical governed diff exceeds the 2000000-byte bound")

    monkeypatch.setattr(loop, "_review_kernel", failed)
    monkeypatch.setattr(loop, "_prepare_public_evaluate_evidence", lambda *a,**k:pytest.fail("minted evaluator identity"))
    result = loop.next_action(ws)
    assert "2000000-byte bound" in result["error"], result
    assert result["review_kernel"]["status"] == "kernel_unavailable"
    assert "review_kernel_runs" not in loop._load_raw(ws)
    assert loop.tp.worker_contract_for_stage(ws,stage="evaluate",task="T19") is None


def test_current_native_terminal_pipeline_admits_actual_pending_ledger_shape(legacy, monkeypatch):
    from taskplane import dispatch_telemetry, tp as cli
    from taskplane.tests.test_native_session_meter import _write_segment
    ws, root, _, _ = legacy
    assert invoke(legacy)["continued"] is True
    state = loop._load_raw(ws)
    ledger = dispatch_telemetry.new_ledger(run_id=state["run_id"], source_sha="a" * 40,
        design_fingerprint=state["design_fingerprint"], plan_fingerprint="p" * 64, started_at=1)
    binding = loop._native_delivery_dispatch_binding(state, stage="execute", task=state["tasks"][19],
        intent_id="intent-generated-stop", native_task_name="generated-child")
    assert binding["started_at"] == binding["ended_at"] == 0 and binding["events"] == []
    dispatch_telemetry.bind_dispatch(ledger, binding)
    state["dispatch_telemetry"] = ledger
    loop.save(ws, state)
    segment = root / "generated-child.jsonl"
    _write_segment(segment, session_id="generated-child", total=1300, cached=800, output=100, ordinal=3)
    contract = {"budget":{"token_usage_required":True}, "worker_lifecycle": {
        "task":"T19", "expected_task_name":"generated-child", "dispatch_intent_id":"intent-generated-stop"}}
    monkeypatch.setitem(sys.modules, "loop", loop)
    event = {"host":"codex", "task_name":"generated-child", "transcript_path":str(segment)}
    result = cli._seal_terminal_dispatch_telemetry(ws, contract, event, outcome="failure")
    assert result["status"] == "admitted"
    assert result["receipt"]["total_tokens"] == 1300
    assert result["receipt"]["events"][-1]["kind"] == "failed"
    assert result["native_session"]["attributed_usage"]["total_tokens"] == 1300
    assert cli._seal_terminal_dispatch_telemetry(ws, contract, event, outcome="failure")["status"] == "duplicate"
    after = loop._load_raw(ws)
    assert len(after["dispatch_telemetry"]["dispatches"]) == 1
    assert after["tasks"] == state["tasks"]


def test_failed_build_dispatch_classifies_without_acceptance_children(legacy, monkeypatch):
    ws = legacy[0]
    assert invoke(legacy)["continued"] is True
    state = loop._load_raw(ws)
    state.update(step="evaluate", goal="classify original incomplete Build", _build_failed=True)
    task = state["tasks"][19]
    task["evaluation_evidence_edges"] = []
    task["failure_routing"] = loop._detected_build_failure_routing(ws, task,
        {"fingerprint":"b" * 64, "outcome":"fail"}, "execute")
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "dor_check", lambda *_:(True, [], []))
    monkeypatch.setattr(loop.depgraph, "scan", lambda *_:None)
    monkeypatch.setattr(loop.depgraph, "load", lambda *_:{"modules":{}})
    monkeypatch.setattr(loop, "_diff_files", lambda *_:[])
    monkeypatch.setattr(loop, "status", lambda *_:{})
    monkeypatch.setattr(loop, "_run_artifact_root", lambda *_:ws)
    monkeypatch.setattr(loop, "_bind_worker_submission", lambda _ws,_state,_step,contract,_task:contract)
    monkeypatch.setattr(loop, "_review_kernel", lambda *a,**k:({
        "run_id":"genuine-test-attempt", "status":"ready", "slots":[],
        "expected_lenses":[], "zero_lens_evaluation":True}, None))
    monkeypatch.setattr(loop, "_bind_stateless_review_contract_actions", lambda _ws,kernel,**kw:kernel)
    monkeypatch.setattr(loop, "_prepare_public_evaluate_evidence",
        lambda *a,**k:pytest.fail("classification requested acceptance children"))
    monkeypatch.setattr(loop, "_dispatch_public_evaluate_evidence_children",
        lambda *a,**k:pytest.fail("classification dispatched acceptance children"))
    class Ready(Exception): pass
    def instruction(*args):
        raise Ready("classification reached ordinary native dispatch composition")
    monkeypatch.setattr(loop, "_design_context", instruction)
    with pytest.raises(Ready):
        loop.next_action(ws)
    after = loop._load_raw(ws)
    assert after["tasks"] == state["tasks"] and after["_build_failed"] is True
    assert after["review_kernel_runs"]["evaluate:T19"]["run_id"] == "genuine-test-attempt"


@pytest.mark.parametrize("damage", [None, "missing-detection", "changed-seal", "foreign-task",
    "foreign-candidate", "pass", "missing-attempt", "not-evaluate", "not-failed"])
def test_failed_build_classification_retains_detection_and_refuses_drift(legacy, monkeypatch, damage):
    ws, _, original, _ = legacy
    state = copy.deepcopy(original)
    state.update(step="evaluate", _build_failed=True)
    task = state["tasks"][19]
    task["failure_routing"] = loop._detected_build_failure_routing(ws, task,
        {"fingerprint":"b" * 64, "outcome":"fail"}, "execute")
    historical = copy.deepcopy(task["failure_routing"])
    # Later repairs change the current candidate, never the historical red.
    monkeypatch.setattr(loop.tp, "git_head", lambda _:"c" * 40)
    attempt = "current-attempt"
    if damage == "missing-detection": task.pop("failure_routing")
    if damage == "changed-seal": task["failure_routing"]["fingerprint"] = "f" * 64
    if damage == "foreign-task": task["id"] = "OTHER"
    if damage == "foreign-candidate":
        task["failure_routing"] = loop._detected_build_failure_routing(ws, {"id":"OTHER"},
            {"fingerprint":"b" * 64, "outcome":"fail"}, "execute")
    if damage == "pass":
        task["failure_routing"] = loop._detected_build_failure_routing(ws, task,
            {"fingerprint":"b" * 64, "outcome":"pass"}, "execute")
    if damage == "missing-attempt": attempt = ""
    if damage == "not-evaluate": state["step"] = "execute"
    if damage == "not-failed": state.pop("_build_failed")
    before = copy.deepcopy(state)
    if damage not in {None, "not-failed"}:
        with pytest.raises(ValueError):
            loop._failed_build_classification(ws, state, task, evaluator_attempt_id=attempt)
    else:
        result = loop._failed_build_classification(ws, state, task, evaluator_attempt_id=attempt)
        if damage == "not-failed":
            assert result is None  # Ordinary acceptance preparation remains mandatory.
        else:
            assert result["mode"] == "failure-classification-only"
            assert result["detected_failure"] == historical
            assert result["candidate"] == loop._failure_candidate_identity(ws, task)
            assert result["evaluator_attempt_id"] == attempt
            assert result["acceptance_allowed"] is False
            assert result["failed_submission"] is None
            assert result["full_submission_status"] == "unavailable-in-legacy-detection"
    assert state == before


def test_future_failed_build_detection_retains_complete_submission(legacy):
    ws, _, original, _ = legacy
    state = copy.deepcopy(original)
    state.update(step="evaluate", _build_failed=True)
    task = state["tasks"][19]
    submission = {"task":"T19", "step":"execute", "outcome":"fail",
        "fingerprint":"b" * 64, "snapshot":"c" * 40, "workspace":ws,
        "note":"incomplete: exact required native evidence unavailable", "submitted_at":123,
        "evidence_paths":[".eval/build.json"], "changed_files":["src/t19.py"],
        "engine_fingerprint":"d" * 64, "evidence_engine_fingerprint":"e" * 64}
    task["failure_routing"] = loop._detected_build_failure_routing(ws, task, submission, "execute")
    result = loop._failed_build_classification(ws, state, task, evaluator_attempt_id="attempt")
    assert result["failed_submission"] == submission
    assert result["full_submission_status"] == "retained"
    submission["note"] = "changed caller object"
    assert result["failed_submission"]["note"] != submission["note"]


@pytest.mark.parametrize("failure_class", ["product", "test", "infrastructure", "environment", "unknown"])
def test_failed_build_classifier_keeps_incumbent_correction_guards(legacy, monkeypatch, failure_class):
    from taskplane import evaluation_output, failure_routing
    ws, root, state, _ = legacy
    assert invoke(legacy)["continued"] is True
    state = loop._load_raw(ws)
    state.update(step="evaluate", _build_failed=True)
    task = state["tasks"][19]
    candidate = loop._failure_candidate_identity(ws, task)
    evidence = {"observation":"bounded independently observed failure"}
    record = {"schema":failure_routing.FAILURE_RECORD_SCHEMA_ID, "id":"actual-failure",
        "source":"independent-evaluator", "stage":"evaluate", "repro":"bounded exact probe",
        "evidence":evidence, "evidence_digest":failure_routing.evidence_digest(evidence),
        "class":failure_class, "reason":"current observed failure", "owner":"existing owner",
        "cluster":"failure-classification", "route":failure_routing.route_for_class(failure_class),
        "candidate":candidate}
    verdict = {"schema":evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
        "task":"T19", "requirement":"R-0001", "verdict":"fail",
        "evaluation":{"status":"complete", "reason_code":"none", "detail":"classified red"},
        "criteria":[{"criterion":"delivery", "status":"cannot-verify", "evidence":"actual-failure"}],
        "graph":{"dispositions":[], "requirements_checked":[], "contracts_checked":[]},
        "failures":[record]}
    path = root / "classifier.json"
    path.write_text(json.dumps(verdict))
    monkeypatch.setattr(loop.runtime_storage, "evaluation_path", lambda _:str(path))
    errors, _, decision = loop._evaluation_failure_routing(ws, state, task)
    assert not errors, errors
    assert decision["product_fix_allowed"] is (failure_class == "product")
    verdict["failures"][0]["candidate"] = {"id":"foreign@" + "a" * 40, "fingerprint":"f" * 64}
    path.write_text(json.dumps(verdict))
    assert loop._evaluation_failure_routing(ws, state, task)[0]
    verdict["failures"][0]["candidate"] = candidate
    verdict["evaluation"] = {"status":"unavailable", "reason_code":"orchestration_unavailable",
        "detail":"honest missing receipt"}
    path.write_text(json.dumps(verdict))
    assert any("failed build" in error for error in loop._evaluation_unavailable_errors(ws, state, task)[0])
    loop.save(ws, state)
    state["submission_required"] = False
    loop.save(ws, state)
    monkeypatch.setattr(loop, "status", lambda *_:{})
    assert "cannot erase" in loop.gate(ws, "pass")["error"]


@pytest.fixture
def classifier_terminal(legacy, monkeypatch):
    from taskplane import evaluation_output, failure_routing, tp as cli
    ws, root, _, _ = legacy
    assert invoke(legacy)["continued"] is True
    (root / ".gitignore").write_text(".taskplane/\n.eval/\n")
    for command in (["git", "init", "-q"], ["git", "add", "."],
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
             "commit", "-qm", "generated legacy fixture baseline"]):
        subprocess.run(command, cwd=ws, check=True, capture_output=True)
    monkeypatch.setattr(loop.tp, "git_head", REAL_GIT_HEAD)
    monkeypatch.setattr(loop.tp, "workspace_fingerprint", REAL_WORKSPACE_FINGERPRINT)
    head = REAL_GIT_HEAD(ws)
    # Rendering is unrelated to producer authority; retain the public owners.
    dashboard = sys.modules[loop.submit.__module__]
    import views
    monkeypatch.setattr(dashboard, "refresh_dashboard_snapshot", lambda *a,**k:{})
    monkeypatch.setattr(dashboard, "_publication_problem", lambda *a:None)
    monkeypatch.setattr(views, "refresh_views", lambda *a:None)
    state = loop._load_raw(ws)
    state.update(step="evaluate", _build_failed=True, submission_required=True,
        goal="generated legacy classification", max_fix_cycles=3)
    task = state["tasks"][19]
    loop.stamp_plan_delivery_mode(state, json.loads((root / "plan/tasks.json").read_text()),
        plan_fingerprint=fingerprint(json.loads((root / "plan/tasks.json").read_text())), source_sha=head)
    state["review_kernel_runs"] = {"evaluate:T19":{"run_id":"e" * 32,
        "workspace":ws, "stage":"evaluate", "status":"ready"}}
    candidate = loop._failure_candidate_identity(ws, task)
    evidence = {"observation":"independent environment failure"}
    record = {"schema":failure_routing.FAILURE_RECORD_SCHEMA_ID, "id":"observed-failure",
        "source":"native-evaluator", "stage":"evaluate", "repro":"bounded current probe",
        "evidence":evidence, "evidence_digest":failure_routing.evidence_digest(evidence),
        "class":"environment", "reason":"required native service unavailable", "owner":"host",
        "cluster":"classification", "route":"environment-recovery", "candidate":candidate}
    verdict = {"schema":evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID, "task":"T19",
        "requirement":"R-0001", "verdict":"fail", "evaluation":{"status":"complete",
        "reason_code":"none", "detail":"bounded independent classification"},
        "criteria":[{"criterion":"delivery", "status":"cannot-verify", "evidence":"observed-failure"}],
        "graph":{"dispositions":[], "requirements_checked":[], "contracts_checked":[]}, "failures":[record]}
    (root / ".eval").mkdir()
    (root / ".eval/verdict.json").write_text(json.dumps(verdict))
    dispatch = {"run_id":"e" * 32, "task_id":"T19", "stage":"evaluate",
        "producer":"tp-evaluator", "task_name":"tp_step_evaluator_t19_test",
        "role_marker":"taskplane-role:tp-evaluator", "model":None, "reasoning_effort":"medium"}
    contract = loop.tp.build_contract("Evaluate", read_only=True, write_allow=[".eval/**"])
    dispatch["fingerprint"] = loop.producer_observation_policy.content_fingerprint(dispatch)
    contract.update(producer_dispatch=dispatch, output_contract={"stage":"evaluate",
        "task":"T19", "producer":"tp-evaluator", "result_path":".eval/verdict.json",
        "output_schema_id":evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID})
    contract = loop.tp.prepare_worker_contract(ws, contract, stage="evaluate", task="T19",
        task_name=dispatch["task_name"], role_marker=dispatch["role_marker"])
    contract = loop.tp.bind_submission_contract(contract, ws, task="T19", stage="evaluate",
        slot=contract["task_slot"], locator={"type":"loop_submission"}, validation_rule="loop-submission/v1")
    loop.tp.activate(ws, contract, snapshot=head, task_slot_override=contract["task_slot"])
    event = {"cwd":ws, "hook_event_name":"SubagentStart", "session_id":"test-parent",
        "agent_id":"test-child", "task_name":dispatch["task_name"], "agent_type":dispatch["task_name"],
        "turn_id":"test-turn"}
    loop.tp.bind_worker_contract_event(ws, event)
    loop.save(ws, state)
    assert loop.submit(ws, "fail", note="independent classifier complete")["submitted"]
    event["hook_event_name"] = "SubagentStop"
    claim = loop.tp.claim_hook_event(ws, "subagent-stop", event, hook_path="native")
    event["_taskplane_hook_claim_id"] = claim["claim_id"]
    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "native")
    monkeypatch.setattr(cli, "_subagent_event", lambda:dict(event))
    monkeypatch.setitem(sys.modules, "loop", loop)
    monkeypatch.setattr(loop, "status", lambda *_:{})
    monkeypatch.setattr(loop.yield_meter, "gate_snapshot", lambda *_:None)
    return ws, root, contract, cli


def test_native_failed_submission_stop_and_gate_use_same_consumed_observation(classifier_terminal, capsys):
    ws, root, contract, cli = classifier_terminal
    before = loop._load_raw(ws)
    assert "producer_observation" not in before["_submission"]
    decision = loop.tp.stop_submission_decision(ws, contract,
        observed_slot=contract["task_slot"], loop_state=before)
    assert decision["valid"], decision
    assert cli.cmd_subagent_stop(SimpleNamespace()) == 0, capsys.readouterr().out
    submitted = loop._load_raw(ws)["_submission"]
    assert submitted.get("producer_observation"), "native Stop observation was never attached to fail submission"
    assert submitted["producer_worker_slot"] == contract["task_slot"]
    assert not Path(loop.tp.active_contract_path(ws, contract["task_slot"])).exists()
    result = loop.gate(ws, "fail")
    assert result.get("step") == "escalated", result
    after = loop._load_raw(ws)
    assert after["tasks"][:19] == before["tasks"][:19]
    assert after["tasks"][19]["failure_routing"]["next"] == "environment-recovery"


def record_classifier_stop(fixture, **changed):
    """Generated host claim through the real observer, never a consumer receipt."""
    ws, _, contract, cli = fixture
    state = loop._load_raw(ws)
    active = loop.tp.load_json(loop.tp.active_contract_path(ws, contract["task_slot"]))
    material = loop.producer_output_identity(ws, state, state["tasks"][19], "evaluate",
        active_contract=active)
    material.update(changed)
    event = cli._subagent_event()
    return loop.producer_observation_policy.record_codex_subagent_stop(event=event,
        hook_claim_id=event["_taskplane_hook_claim_id"], **material)


@pytest.mark.parametrize("damage", ["missing", "foreign-source", "foreign-slot", "foreign-task", "stale-output"])
def test_failed_observation_refuses_independent_missing_foreign_stale(classifier_terminal, damage):
    ws, root, contract, _ = classifier_terminal
    if damage != "missing":
        record_classifier_stop(classifier_terminal,
            **({"source_sha":"f" * 40} if damage == "foreign-source" else {}))
    if damage == "stale-output":
        with (root / ".eval/verdict.json").open("a") as stream:
            stream.write("\n ")
    if damage == "foreign-task":
        with loop.mutate(ws) as state:
            state["_submission"]["task"] = "OTHER"
    before = loop._load_raw(ws)
    with pytest.raises(ValueError):
        loop.collect_failed_submission_observation(ws,
            slot="task_foreign" if damage == "foreign-slot" else contract["task_slot"])
    assert loop._load_raw(ws) == before
    assert "producer_observation" not in before["_submission"]


def test_failed_observation_byte_race_and_interrupted_consumption_replay(classifier_terminal, monkeypatch):
    ws, root, contract, _ = classifier_terminal
    receipt = record_classifier_stop(classifier_terminal)
    output = root / ".eval/verdict.json"
    original = output.read_bytes()
    consume = loop.producer_observation_policy.consume_matching_observation
    def raced(**material):
        result = consume(**material)
        output.write_bytes(original + b"\n")
        return result
    monkeypatch.setattr(loop.producer_observation_policy, "consume_matching_observation", raced)
    before = loop._load_raw(ws)
    with pytest.raises(ValueError, match="changed during"):
        loop.collect_failed_submission_observation(ws, slot=contract["task_slot"])
    assert loop._load_raw(ws) == before
    output.write_bytes(original)
    monkeypatch.setattr(loop.producer_observation_policy, "consume_matching_observation", consume)
    assert loop.collect_failed_submission_observation(ws, slot=contract["task_slot"]) == receipt
    once = loop._load_raw(ws)
    assert loop.collect_failed_submission_observation(ws, slot=contract["task_slot"]) == receipt
    assert loop._load_raw(ws) == once


@pytest.mark.parametrize("damage", ["missing-terminal", "missing-quarantine", "signature", "foreign-owner",
    "foreign-task", "foreign-slot", "stale-output", "missing-observation"])
def test_failed_gate_requires_exact_signed_retired_producer(classifier_terminal, capsys, damage):
    ws, root, contract, cli = classifier_terminal
    assert cli.cmd_subagent_stop(SimpleNamespace()) == 0, capsys.readouterr().out
    slot = contract["task_slot"]
    terminal = Path(loop.tp._worker_terminal_path(ws, slot))
    receipt = json.loads(terminal.read_text())
    archive = root / ".taskplane/quarantine/contracts" / f"{slot}-{receipt['receipt_id'].split('-')[-1]}.json"
    if damage == "missing-terminal": terminal.unlink()
    if damage == "missing-quarantine": archive.unlink()
    if damage == "signature":
        receipt["signature"] = "0" * 64
        terminal.write_text(json.dumps(receipt))
    if damage in {"foreign-owner", "foreign-task"}:
        released = json.loads(archive.read_text())
        if damage == "foreign-owner": released["worker_lifecycle"]["owner"]["agent_id"] = "foreign"
        else: released["worker_lifecycle"]["task"] = "OTHER"
        archive.write_text(json.dumps(released))
    if damage == "stale-output":
        with (root / ".eval/verdict.json").open("a") as stream:
            stream.write("\n ")
    if damage in {"foreign-slot", "missing-observation"}:
        with loop.mutate(ws) as state:
            if damage == "foreign-slot": state["_submission"]["producer_worker_slot"] = "task_foreign"
            else: state["_submission"].pop("producer_observation")
    before = loop._load_raw(ws)
    result = loop.gate(ws, "fail")
    assert result.get("error"), result
    after = loop._load_raw(ws)
    assert after["step"] == "evaluate" and after["_submission"] == before["_submission"]
    assert after["tasks"] == before["tasks"]


def test_failed_gate_cleanup_preserves_existing_adverse_terminal_receipt(cancellation):
    legacy, path, _ = cancellation
    ws = legacy[0]
    contract = json.loads(path.read_text())
    # Simulate an already-recorded historical wrong outcome. A future gate
    # correction must preserve it, not rewrite adverse signed evidence.
    receipt = loop.tp.record_worker_terminal(ws, contract["task_id"], event=None,
        outcome="success", submission_status="gated", authority="loop-gate")
    result = loop.tp.release_worker_contracts_for_gate(ws, stage="execute", task="T19",
        outcome="failure", submission_status="gated:fail")
    archive = json.loads(Path(result[0]["quarantine"]).read_text())
    assert archive["worker_lifecycle"]["terminal"] == receipt


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def sealed(value):
    return dict(value, fingerprint=fingerprint(value))


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    ws = str(tmp_path / "repo")
    root = tmp_path / "repo"
    (root / "plan").mkdir(parents=True)
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TASKPLANE_TASK_ID", raising=False)
    monkeypatch.setattr(loop.tp, "task_slot", lambda: None)
    monkeypatch.setattr(loop.tp, "git_head", lambda _: "a" * 40)
    monkeypatch.setattr(loop.tp, "workspace_fingerprint", lambda _: "b" * 64)
    config = settings.load_settings(environment={})
    snapshot = config.to_dict()
    snapshot.pop("phase_definitions")
    digest = settings.settings_digest(snapshot)
    requirement = {"id": "R-0001", "title": "Original requirement", "contracts": [
        {"id": "contract:required", "relation": "changes"}]}
    monkeypatch.setattr(loop.reqs, "get_requirement", lambda *_: copy.deepcopy(requirement))
    declared = [{"id": f"T{i:02}", "req": "R-0001", "scope": [f"src/t{i}.py"],
                 "tests": f"pytest tests/test_t{i}.py", "deps": [], "type": "code", "contracts": ["contract:local"]}
                for i in range(23)]
    plan = {"requirement": "R-0001", "design_fingerprint": "d" * 64,
            "delivery_mode": "build", "automatic_lenses": [],
            "plan_authority": "original human approval", "tasks": declared}
    policy = sealed({"schema": "taskplane.run-review-timing/v1", "run_id": "legacy-run",
        "design_fingerprint": "d" * 64, "plan_fingerprint": fingerprint(plan),
        "by": "human:test", "instruction": "Review Build at Engineering",
        "mode": "build-tests-then-em-lenses"})
    tasks = copy.deepcopy(declared)
    for i, task in enumerate(tasks):
        task["contracts"] = copy.deepcopy(requirement["contracts"]) + task["contracts"]
        task.update(status="passed" if i < 19 else "pending", fix_cycles=0)
        if 2 <= i < 19:
            task["evaluation"] = {"task": task["id"], "status": "deferred",
                "verdict": "non-judged", "reason_code": "human-deferred-to-em",
                "policy_fingerprint": policy["fingerprint"], "build_candidate": "c" * 40,
                "suite_key": "e" * 64}
        elif i < 2:
            task.update(evaluation={"task": task["id"], "status": "unavailable",
                "verdict": "non-judged", "outage_identity": {"fingerprint": "f" * 64}},
                human_resolution={"decision": "pass", "actor": "human:test",
                    "outage_fingerprint": "f" * 64}, reanchor_authority={"fingerprint": "e" * 64},
                target_commit="c" * 40)
    state = {"run_id": "legacy-run", "requirement_id": "R-0001", "step": "execute",
        "current_task": 19, "tasks": tasks, "baseline": "c" * 40,
        "design_fingerprint": "d" * 64, "settings_digest": digest,
        "run_artifact_binding": {"run_id": "legacy-run", "settings_digest": digest},
        "review_timing_override": policy, "review_timing_override_history": [],
        "deferred_review_tasks": [t["id"] for t in tasks[2:19]], "checkpoints": ["plan", "em"],
        "dispatch_telemetry": {"total_tokens": 500000, "unknown": None},
        "delivery_mode_receipt": delivery_policy.validate_plan_mode(plan,
            plan_fingerprint=fingerprint(plan), source_sha="a" * 40)}
    loop.save(ws, state)
    amended = copy.deepcopy(plan)
    amended["tasks"][19]["scope"].append("taskplane/run_context.py")
    after_bytes = (json.dumps(amended, indent=2) + "\n").encode()
    (root / "plan/tasks.json").write_bytes(after_bytes)
    request = {"schema": "taskplane.legacy-build-amendment/v1", "run_id": "legacy-run",
        "requirement_id": "R-0001", "task_id": "T19", "baseline": "c" * 40,
        "design_fingerprint": "d" * 64, "settings_digest": digest,
        "before_state_fingerprint": loop_recovery.legacy_state_fingerprint(state), "candidate": "a" * 40,
        "source_fingerprint": "b" * 64, "before_plan": plan,
        "after_plan_sha256": hashlib.sha256(after_bytes).hexdigest(),
        "settings_snapshot": snapshot, "resource_limits": "advisory", "observation_checkpoint": None,
        "requirement_fingerprint": loop_recovery._requirement_fingerprint(requirement)}
    return ws, root, state, request


def invoke(legacy, request=None, **kwargs):
    ws, root, _, original = legacy
    supplied = request or original
    path = root / "amendment.json"
    path.write_text(json.dumps(supplied))
    return loop.continue_build(ws, source=str(path), by=kwargs.get("by", "human:test"),
        request=kwargs.get("instruction", "Append the exact repair scope; limits are advisory"),
        expected_fingerprint=kwargs.get("expected_fingerprint", fingerprint(supplied)), check=kwargs.get("check", False),
        observation_authority=kwargs.get("observation_authority"))


@pytest.fixture
def cancellation(legacy, monkeypatch):
    ws, root, state, _ = legacy
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "test-controller")
    contract = loop.tp.prepare_worker_contract(ws, {"task_id": "task_b2355132",
        "submission_contract": {"required": True}}, stage="execute", task="T19",
        task_name="tp_step_executor_t19_attempt_5_0e8d96b7", role_marker="taskplane-role:tp-executor")
    contract["worker_lifecycle"].update(dispatch_intent_id="intent-test", dispatch_intent_run_id=state["run_id"])
    path = Path(loop.tp.active_contract_path(ws, contract["task_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract))
    # A historically real spawn with no lifecycle hooks is NOT an unlaunched
    # attempt: its unknown ledger usage must survive administrative retirement.
    state["dispatch_telemetry"]["workers"] = {"intent-test": {
        "task": "T19", "started_at": 0, "ended_at": 0, "usage": None}}
    loop.save(ws, state)
    packet = {"schema": "taskplane.legacy-worker-cancellation/v1", "run_id": state["run_id"],
        "task_id": "T19", "slot": contract["task_id"],
        "contract_fingerprint": loop_recovery._fingerprint(contract),
        "expected_worker": contract["worker_lifecycle"]["expected_task_name"],
        "before_state_fingerprint": loop_recovery.legacy_state_fingerprint(state),
        "candidate": "a" * 40, "source_fingerprint": "b" * 64,
        "plan_sha256": hashlib.sha256((root / "plan/tasks.json").read_bytes()).hexdigest(),
        "observation_checkpoint": None, "host_attestation": {"status": "unavailable",
            "session_id": "test-controller", "evidence": "Exact native interrupt returned not_found; historical spawn exists"}}
    return legacy, path, packet


def cancel(cancellation, **kwargs):
    legacy, _, packet = cancellation
    ws, root, _, _ = legacy
    source = root / "cancellation.json"
    source.write_text(json.dumps(packet))
    return loop.cancel_worker(ws, source=str(source), by=kwargs.pop("by", "human:test"),
        request=kwargs.pop("request", "Proceed with administrative cancellation, retaining unknown evidence"),
        expected_fingerprint=loop_recovery._fingerprint(packet), **kwargs)


def test_legacy_cancellation_preserves_real_launch_uncertainty_and_results(cancellation):
    legacy, path, _ = cancellation
    ws, root, before, _ = legacy
    raw = path.read_bytes()
    plan = (root / "plan/tasks.json").read_bytes()
    assert cancel(cancellation, check=True)["read_only"] is True
    assert path.read_bytes() == raw and loop._load_raw(ws) == before
    result = cancel(cancellation)
    assert result.get("cancelled") is True, result
    assert result["dispatch_allowed"] is False
    after = loop._load_raw(ws)
    receipt = after.pop("legacy_worker_cancellation")
    assert after == before and receipt["cleanup"] == "completed"
    assert receipt["evidence_status"] == "host-terminal-missing; usage-unchanged"
    assert not path.exists() and (root / "plan/tasks.json").read_bytes() == plan
    archive = json.loads(Path(result["release"]["quarantine"]).read_text())
    assert archive["worker_lifecycle"]["terminal"]["outcome"] == "cancellation"
    assert archive["worker_lifecycle"]["terminal"]["authority"] == "orphan-recovery"
    assert archive["worker_lifecycle"]["terminal"]["owner"] is None
    saved = loop._load_raw(ws)
    assert cancel(cancellation)["replay"] is True
    assert loop._load_raw(ws) == saved


def test_legacy_cancellation_public_cli_check_does_not_flush_outbox(cancellation, monkeypatch, capsys):
    from taskplane import tp as cli
    legacy, path, packet = cancellation
    ws, root, before, _ = legacy
    source = root / "cancellation.json"
    source.write_text(json.dumps(packet))
    monkeypatch.setitem(sys.modules, "loop", loop)
    monkeypatch.setattr(loop, "load", lambda *_: pytest.fail("cancellation flushed authority outbox"))
    args = SimpleNamespace(cmd="loop", fn=cli.cmd_loop, loop_action="cancel-worker", workspace=ws,
        amendment_from=str(source), by="human:test", request="Proceed with administrative cancellation",
        fingerprint=loop_recovery._fingerprint(packet), check=True)
    assert cli._invoke_run_command(args, ws) == 0
    assert json.loads(capsys.readouterr().out)["read_only"] is True
    assert loop._load_raw(ws) == before and path.exists()
    args.check = False
    assert cli._invoke_run_command(args, ws) == 0
    assert json.loads(capsys.readouterr().out)["cancelled"] is True


@pytest.mark.parametrize("field", ["by", "request", "host_attestation", "run_id", "task_id", "slot",
    "contract_fingerprint", "release"])
def test_legacy_cancellation_replay_refuses_changed_journal(cancellation, field):
    assert cancel(cancellation)["cancelled"] is True
    ws = cancellation[0][0]
    state = loop._load_raw(ws)
    state["legacy_worker_cancellation"][field] = "forged-audit-value"
    loop.save(ws, state)
    assert cancel(cancellation).get("error")
    assert loop._load_raw(ws) == state


@pytest.mark.parametrize("damage", ["owner", "run", "task", "slot", "contract", "worker", "effects",
    "actor", "request", "attestation", "session", "plan", "policy", "source", "stage"])
def test_legacy_cancellation_refuses_foreign_or_unsafe_request(cancellation, damage):
    legacy, path, packet = cancellation
    ws, root, state, _ = legacy
    kwargs = {}
    if damage == "owner":
        value = json.loads(path.read_text())
        value["worker_lifecycle"]["owner"] = {"agent_id": "live"}
        path.write_text(json.dumps(value))
        packet["contract_fingerprint"] = loop_recovery._fingerprint(value)
    if damage == "run": packet["run_id"] = "foreign"
    if damage == "task": packet["task_id"] = "T20"
    if damage == "slot": packet["slot"] = "task_foreign"
    if damage == "contract": packet["contract_fingerprint"] = "f" * 64
    if damage == "worker": packet["expected_worker"] = "foreign"
    if damage == "effects": state["attempt_lease"] = {"effects": "uncertain"}
    if damage == "actor": kwargs["by"] = "human:foreign"
    if damage == "request": kwargs["request"] = ""
    if damage == "attestation": packet["host_attestation"]["status"] = "completed"
    if damage == "session": packet["host_attestation"]["session_id"] = "foreign"
    if damage == "plan": (root / "plan/tasks.json").write_text("{}")
    if damage == "policy": state["review_timing_override"]["by"] = "human:foreign"
    if damage == "source": packet["source_fingerprint"] = "f" * 64
    if damage == "stage": state["_stage_run_binding"] = {"run_id": "stage"}
    loop.save(ws, state)
    packet["before_state_fingerprint"] = loop_recovery.legacy_state_fingerprint(state)
    raw = path.read_bytes()
    assert cancel(cancellation, **kwargs).get("error")
    assert loop._load_raw(ws) == state and path.read_bytes() == raw


@pytest.mark.parametrize("boundary", ["terminal", "terminal-file", "release", "after-release"])
def test_legacy_cancellation_interrupted_cleanup_replays_exactly(cancellation, monkeypatch, boundary):
    legacy, path, _ = cancellation
    ws = legacy[0]
    name = "record_worker_terminal" if boundary == "terminal" else "release_worker_contract"
    original = getattr(loop.tp, name)
    write = loop.tp.atomic_write_json

    def interrupted(*args, **kwargs):
        if boundary == "after-release": original(*args, **kwargs)
        raise OSError("simulated interruption")

    monkeypatch.setattr(loop.tp, name, interrupted)
    if boundary == "terminal-file":
        monkeypatch.setattr(loop.tp, name, original)

        def interrupted_write(target, *args, **kwargs):
            if str(target) == str(path): raise OSError("simulated active lifecycle write failure")
            return write(target, *args, **kwargs)

        monkeypatch.setattr(loop.tp, "atomic_write_json", interrupted_write)
    assert cancel(cancellation).get("error")
    assert loop._load_raw(ws)["legacy_worker_cancellation"]["cleanup"] == "pending"
    assert cancel(cancellation, request="different approval").get("error")
    monkeypatch.setattr(loop.tp, name, original)
    monkeypatch.setattr(loop.tp, "atomic_write_json", write)
    assert cancel(cancellation)["cancelled"] is True
    saved = loop._load_raw(ws)
    assert cancel(cancellation)["replay"] is True and loop._load_raw(ws) == saved


def test_actual_legacy_shape_continues_without_reanchoring_deferred_passes(legacy):
    ws, _, before, _ = legacy
    result = invoke(legacy)
    assert not result.get("error"), result
    assert result["continued"] is True and result["independent_review_passed"] is False
    after = loop._load_raw(ws)
    assert after["tasks"][:19] == before["tasks"][:19]
    assert after["tasks"][19]["status"] == "pending"
    for key in ("run_id", "requirement_id", "baseline", "design_fingerprint", "current_task",
                "dispatch_telemetry", "deferred_review_tasks", "checkpoints", "run_artifact_binding"):
        assert after[key] == before[key]
    assert not after.get("_stage_run_binding") and not after.get("_stage_native_root_authority")
    assert invoke(legacy)["replay"] is True
    assert loop._load_raw(ws) == after
    assert run_context.selected(after) is False
    with run_context.bind(ws, after):
        recovered = settings.load_settings(environment={"TASKPLANE_MODEL_DEEP": "changed"})
        assert recovered.digest == before["settings_digest"]
        assert recovered.to_dict() == after["settings_snapshot"]
        assert "phase_definitions" not in recovered.to_dict()
    assert run_context.resource_limits_advisory(ws) is True


@pytest.mark.parametrize("damage", ["scope", "tests", "completed-declaration", "plan-metadata", "plan-bytes"])
def test_altered_plan_refuses_without_changing_saved_work(legacy, damage):
    ws, root, before, packet = legacy
    plan = json.loads((root / "plan/tasks.json").read_text())
    if damage == "scope": plan["tasks"][19]["scope"] = ["unrelated.py"]
    if damage == "tests": plan["tasks"][19]["tests"] = "true"
    if damage == "completed-declaration": plan["tasks"][1]["scope"].append("unrelated.py")
    if damage == "plan-metadata": plan["design_fingerprint"] = "e" * 64
    raw = json.dumps(plan).encode()
    (root / "plan/tasks.json").write_bytes(raw)
    if damage != "plan-bytes": packet["after_plan_sha256"] = hashlib.sha256(raw).hexdigest()
    assert invoke(legacy).get("error")
    assert loop._load_raw(ws) == before


@pytest.mark.parametrize("damage", ["forged-verdict", "forged-authority", "stale-policy", "missing-suite",
    "foreign-run", "foreign-binding", "settings", "foreign-actor", "worker", "source", "candidate", "request", "packet",
    "extra-contract", "foreign-requirement"])
def test_foreign_stale_or_forged_inputs_fail_closed(legacy, monkeypatch, damage):
    ws, _, before, packet = legacy
    state = copy.deepcopy(before)
    kwargs = {}
    if damage == "forged-verdict": state["tasks"][2]["evaluation"]["verdict"] = "pass"
    if damage == "forged-authority": state["tasks"][2]["reanchor_authority"] = {"fingerprint": "f" * 64}
    if damage == "stale-policy": state["tasks"][2]["evaluation"]["policy_fingerprint"] = "f" * 64
    if damage == "missing-suite": state["tasks"][2]["evaluation"].pop("suite_key")
    if damage == "foreign-run": packet["run_id"] = "another-run"
    if damage == "foreign-binding": state["run_artifact_binding"]["run_id"] = "another-run"
    if damage == "settings": packet["settings_snapshot"]["limits"]["budgets"]["max_actions"] += 1
    if damage == "foreign-actor": kwargs["by"] = "human:another"
    if damage == "worker": monkeypatch.setattr(loop.tp, "task_slot", lambda: "worker-slot")
    if damage == "source": packet["source_fingerprint"] = "f" * 64
    if damage == "candidate": packet["candidate"] = "f" * 40
    if damage == "request": kwargs["instruction"] = ""
    if damage == "packet": kwargs["expected_fingerprint"] = "f" * 64
    if damage == "extra-contract": state["tasks"][19]["contracts"].append("contract:foreign")
    if damage == "foreign-requirement": packet["requirement_fingerprint"] = "f" * 64
    # Even a freshly fingerprinted malformed proposal cannot mint pass evidence.
    loop.save(ws, state)
    packet["before_state_fingerprint"] = loop_recovery.legacy_state_fingerprint(state)
    assert invoke(legacy, **kwargs).get("error")
    assert loop._load_raw(ws) == state


@pytest.mark.parametrize("damage", ["result", "settings", "resource", "run", "revocation", "plan"])
def test_fresh_pickup_and_policy_guard_refuse_post_approval_drift(legacy, damage):
    ws, root, _, _ = legacy
    assert not invoke(legacy).get("error")
    state = loop._load_raw(ws)
    if damage == "result": state["tasks"][2]["evaluation"]["verdict"] = "pass"
    if damage == "settings": state["settings_snapshot"]["limits"]["budgets"]["max_actions"] += 1
    if damage == "resource": state["resource_policy"]["actor"] = "human:another"
    if damage == "run": state["run_id"] = "foreign"
    if damage == "revocation": state["legacy_build_continuation"]["revoked"] = True
    if damage == "plan": (root / "plan/tasks.json").write_text("{}")
    loop.save(ws, state)
    with pytest.raises(ValueError):
        with run_context.bind(ws, state):
            pytest.fail("admitted changed authority")
    with pytest.raises(ValueError):
        run_context.resource_limits_advisory(ws)
    assert invoke(legacy).get("error")
    assert loop._load_raw(ws) == state


def test_check_and_interrupted_plan_to_state_commit_are_recoverable(legacy, monkeypatch):
    ws, root, before, _ = legacy
    plan_bytes = (root / "plan/tasks.json").read_bytes()
    result = invoke(legacy, check=True)
    assert result["checked"] is True and result["continued"] is False
    assert loop._load_raw(ws) == before
    with pytest.raises(ValueError, match="continue-build"):
        with run_context.bind(ws, before):
            pytest.fail("an externally prepared Plan became authority")
    original_save = loop.save
    with monkeypatch.context() as patch:
        patch.setattr(loop, "save", lambda *_: (_ for _ in ()).throw(OSError("interrupted atomic commit")))
        assert "interrupted atomic commit" in invoke(legacy)["error"]
    assert loop._load_raw(ws) == before
    assert (root / "plan/tasks.json").read_bytes() == plan_bytes
    result = invoke(legacy)
    assert result["continued"] is True and result["replay"] is False
    committed = loop._load_raw(ws)
    with monkeypatch.context() as patch:
        patch.setattr(loop, "save", lambda *_: pytest.fail("replay wrote loop state"))
        assert invoke(legacy)["replay"] is True
    assert loop._load_raw(ws) == committed
    assert loop.save == original_save
    assert invoke(legacy, instruction="different approval").get("error")


def test_concurrent_scope_change_is_not_lost(legacy, monkeypatch):
    ws, _, before, _ = legacy
    original = loop.mutate
    @contextmanager
    def race(workspace):
        changed = copy.deepcopy(before)
        changed["tasks"][0]["scope"].append("unrelated.py")
        loop.save(workspace, changed)
        with original(workspace) as state:
            yield state
    monkeypatch.setattr(loop, "mutate", race)
    assert "concurrently" in invoke(legacy)["error"]
    assert loop._load_raw(ws)["tasks"][0]["scope"][-1] == "unrelated.py"


def _add_meter(legacy):
    from taskplane import dispatch_telemetry, native_session_meter
    from taskplane.tests.test_native_session_meter import _write_segment
    ws, root, state, packet = legacy
    authority = b"generated-test-authority-only"
    segment = root / "root.jsonl"
    def observation(sequence, total):
        _write_segment(segment, session_id="root", total=total, output=1, ordinal=sequence * 3)
        return native_session_meter.seal_root_observation(native_session_meter.read_snapshot(str(segment)),
            sequence=sequence, session_role="root", status_receipt_fingerprint="e" * 64,
            terminal_reason=None, authority=authority)
    meter = native_session_meter.fold_root_observations([observation(1, 100)], authority=authority)
    ledger = dispatch_telemetry.new_ledger(run_id=state["run_id"], source_sha="a" * 40,
        design_fingerprint=state["design_fingerprint"], plan_fingerprint=fingerprint(packet["before_plan"]), started_at=1)
    dispatch_telemetry.configure_root_admission(ledger,
        root_session_settings=packet["settings_snapshot"]["workflow"]["root_session"], settings_digest=state["settings_digest"])
    dispatch_telemetry.record_root_meter(ledger, meter, observation_authority=authority)
    state["dispatch_telemetry"] = ledger
    state["root_hygiene"] = {"status": "open", "meter": meter, "policy": "original",
        "observation_authority_fingerprint": hashlib.sha256(authority).hexdigest()}
    loop.save(ws, state)
    packet["before_state_fingerprint"] = loop_recovery.legacy_state_fingerprint(state)
    packet["observation_checkpoint"] = loop_recovery.legacy_observation_checkpoint(state)
    def advance():
        current = loop._load_raw(ws)
        newer = native_session_meter.fold_root_observations([observation(2, 200)], authority=authority,
            prior=current["root_hygiene"]["meter"]["watermark"])
        dispatch_telemetry.record_root_meter(current["dispatch_telemetry"], newer, observation_authority=authority)
        current["root_hygiene"]["meter"] = newer
        loop.save(ws, current)
        return current
    return authority, advance


def test_authenticated_meter_can_advance_between_check_and_commit(legacy, monkeypatch):
    ws, _, _, packet = legacy
    authority, advance = _add_meter(legacy)
    assert invoke(legacy, check=True, observation_authority=authority)["checked"] is True
    newer = advance()
    assert loop_recovery.legacy_state_fingerprint(newer) == packet["before_state_fingerprint"]
    result = invoke(legacy, observation_authority=authority)
    assert not result.get("error"), result
    after = loop._load_raw(ws)
    assert after["root_hygiene"]["meter"] == newer["root_hygiene"]["meter"]
    assert after["dispatch_telemetry"] == newer["dispatch_telemetry"]
    assert "authenticator" not in json.dumps(packet)


@pytest.mark.parametrize("damage", ["policy", "ledger-policy", "worker-binding", "forged-meter", "authority", "backwards"])
def test_meter_exemption_cannot_hide_authority_or_usage_drift(legacy, damage):
    ws, _, _, packet = legacy
    authority, _ = _add_meter(legacy)
    state = loop._load_raw(ws)
    if damage == "policy": state["root_hygiene"]["policy"] = "forged"
    if damage == "ledger-policy": state["dispatch_telemetry"]["root_admission"]["policy"]["root_budget_tokens"] += 1
    if damage == "worker-binding": state["dispatch_telemetry"]["bindings"].append({"forged": True})
    if damage == "forged-meter": state["root_hygiene"]["meter"]["watermark"]["authenticator"] = "f" * 64
    if damage == "authority": authority = b"foreign-test-authority"
    if damage == "backwards": packet["observation_checkpoint"]["sequence"] += 1
    loop.save(ws, state)
    assert invoke(legacy, observation_authority=authority).get("error")
    assert loop._load_raw(ws) == state


def test_original_review_timing_advances_only_verified_build_and_keeps_em_due(legacy):
    ws, _, _, _ = legacy
    assert invoke(legacy)["continued"] is True
    state = loop._load_raw(ws)
    state["step"] = "evaluate"
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match="passing Build tests"):
        loop._advance_build_with_review_deferred(ws, state)
    assert state == before
    state["_suite_evidence"] = {"T19": {"returncode": 0,
        "command": state["tasks"][19]["tests"], "key": "a" * 64}}
    assert loop._advance_build_with_review_deferred(ws, state) is True
    assert state["step"] == "execute" and state["current_task"] == 20
    result = state["tasks"][19]
    assert result["status"] == "passed"
    assert result["evaluation"]["verdict"] == "non-judged"
    assert result["evaluation"]["status"] == "deferred"
    assert "target_commit" not in result and "reanchor_authority" not in result
    assert "T19" in state["deferred_review_tasks"]
    assert loop_recovery.legacy_review_policy(state) is not None
    assert state["baseline"] == before["baseline"]
    assert state["legacy_build_continuation"]["independent_review_passed"] is False


def test_failed_or_dispatched_build_cannot_be_deferred(legacy):
    ws, _, _, _ = legacy
    invoke(legacy)
    state = loop._load_raw(ws)
    state.update(step="evaluate", _build_failed=True)
    before = copy.deepcopy(state)
    with pytest.raises(ValueError):
        loop._advance_build_with_review_deferred(ws, state)
    assert state == before


def _ready_human_defer_review(legacy):
    ws, _, _, _ = legacy
    assert invoke(legacy)["continued"] is True
    state = loop._load_raw(ws)
    state["step"] = "evaluate"
    state["tasks"][19].update(status="running", evaluation={"status": "failed", "verdict": "unknown"},
                              failure_routing={"next": "hold", "historical": True})
    loop.save(ws, state)
    return state


# This saved-shape fixture has no renderable dashboard artifact store. Exercise
# the public resolver/run_context and mutation owners without that UI decorator.
resolve_defer_review = loop.resolve.__wrapped__


@pytest.fixture
def review_baseline_ready(legacy, monkeypatch):
    ws, root, _, _ = legacy
    _ready_human_defer_review(legacy)
    for args in (["init", "-q"], ["config", "user.email", "test@example.test"],
                 ["config", "user.name", "Test"]):
        subprocess.run(["git", *args], cwd=ws, check=True)
    (root / "candidate.txt").write_text("accepted\n")
    subprocess.run(["git", "add", "candidate.txt"], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "accepted Build"], cwd=ws, check=True)
    monkeypatch.setattr(loop.tp, "git_head", REAL_GIT_HEAD)
    accepted = REAL_GIT_HEAD(ws)
    for index in range(19, 23):
        state = loop._load_raw(ws)
        state["step"] = "evaluate"
        state["tasks"][index]["status"] = "running"
        loop.save(ws, state)
        result = resolve_defer_review(ws, "defer-review", by="human:test", run_id="legacy-run",
            task_id=state["tasks"][index]["id"], reason="Accept Build; review remains non-judged")
        assert result.get("resolved"), result
    (root / "candidate.txt").write_text("current delta\n")
    subprocess.run(["git", "add", "candidate.txt"], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "remaining implementation"], cwd=ws, check=True)
    return ws, root, accepted


@pytest.mark.parametrize("damage", ["none", "run", "actor", "task", "reason", "incomplete", "scope",
    "nonancestor", "submission", "worker"])
def test_review_baseline_selects_only_accepted_instance_and_preserves_history(review_baseline_ready, monkeypatch, damage):
    ws, root, accepted = review_baseline_ready
    args = dict(by="human:test", run_id="legacy-run", task_id="T19", reason="Use latest accepted T19 for EM delta")
    state = loop._load_raw(ws)
    if damage == "run": args["run_id"] = "foreign"
    elif damage == "actor": args["by"] = "human:foreign"
    elif damage == "task": args["task_id"] = "T00"
    elif damage == "reason": args["reason"] = ""
    elif damage == "incomplete": state["tasks"][-1]["status"] = "pending"
    elif damage == "scope": state["tasks"][-1]["scope"].append("foreign.py")
    elif damage == "nonancestor":
        state["tasks"][19]["human_resolution"]["build_candidate"] = "f" * 40
        state["tasks"][19]["evaluation"]["build_candidate"] = "f" * 40
    elif damage == "submission": state["_submission"] = {"outcome": "pass"}
    elif damage == "worker": monkeypatch.setattr(loop.tp, "_active_worker_contracts", lambda _: [("worker", {})])
    loop.save(ws, state)
    result = resolve_defer_review(ws, "review-baseline", **args)
    if damage != "none":
        assert result.get("error"), result
        assert loop._load_raw(ws) == state
        return
    assert result.get("resolved"), result
    after = loop._load_raw(ws)
    assert after["tasks"] == state["tasks"] and after["baseline"] == state["baseline"]
    assert after["deferred_review_tasks"] == state["deferred_review_tasks"]
    assert loop._review_baseline(ws, after, "em") == accepted
    assert loop._review_baseline(ws, after, "evaluate") == state["baseline"]
    assert "candidate.txt" in loop._diff_files(ws, loop._review_baseline(ws, after, "em"))
    assert resolve_defer_review(ws, "review-baseline", **args)["replay"] is True
    assert loop._load_raw(ws) == after
    assert resolve_defer_review(ws, "review-baseline", **{**args, "reason": "changed"}).get("error")
    after["tasks"][-1]["scope"].append("foreign.py")
    with pytest.raises(ValueError):
        loop._review_baseline(ws, after, "em")


def test_human_defer_review_preserves_history_without_current_test_or_review_pass(legacy):
    ws, root, _, _ = legacy
    before = _ready_human_defer_review(legacy)
    args = dict(by="human:test", run_id="legacy-run", task_id="T19", reason="Accept completed Build; review at EM")
    result = resolve_defer_review(ws, "defer-review", **args)
    assert not result.get("error"), result
    after = loop._load_raw(ws)
    task = after["tasks"][19]
    assert after["step"] == "execute" and after["current_task"] == 20
    assert task["status"] == "passed"
    assert task["evaluation"]["status"] == "deferred"
    assert task["evaluation"]["verdict"] == "non-judged"
    assert task["evaluation"]["reason_code"] == "human-accepted-build-review-deferred"
    assert task["human_resolution"]["previous_evaluation"] == before["tasks"][19]["evaluation"]
    assert task["failure_routing"] == before["tasks"][19]["failure_routing"]
    assert not task.get("target_commit") and not task.get("reanchor_authority")
    assert "suite_key" not in task["evaluation"] and "_suite_evidence" not in after
    assert after["deferred_review_tasks"].count("T19") == 1
    assert after["tasks"][:19] == before["tasks"][:19]
    assert after["baseline"] == before["baseline"]
    loop_recovery._legacy_results(after, json.loads((root / "plan/tasks.json").read_text()),
                                  loop.reqs.get_requirement(ws, "R-0001"))
    assert resolve_defer_review(ws, "defer-review", **args)["replay"] is True
    assert loop._load_raw(ws) == after
    assert resolve_defer_review(ws, "defer-review", **{**args, "reason": "changed"}).get("error")
    assert loop._load_raw(ws) == after


@pytest.mark.parametrize("damage", ["actor", "reason", "run", "task", "policy", "not-legacy", "failed", "submission", "evaluator", "worker"])
def test_human_defer_review_refuses_unapproved_or_active_state(legacy, monkeypatch, damage):
    ws, _, _, _ = legacy
    state = _ready_human_defer_review(legacy)
    args = dict(by="human:test", run_id="legacy-run", task_id="T19", reason="Accept Build; EM review remains")
    if damage == "actor": args["by"] = ""
    elif damage == "reason": args["reason"] = ""
    elif damage == "run": args["run_id"] = "foreign-run"
    elif damage == "task": args["task_id"] = "T20"
    elif damage == "policy": state["review_timing_override"]["instruction"] = "forged"
    elif damage == "not-legacy": state.pop("legacy_build_continuation")
    elif damage == "failed": state["_build_failed"] = True
    elif damage == "submission": state["_submission"] = {"outcome": "pass"}
    elif damage == "evaluator": state["evaluate_child_evidence"] = {"pending": True}
    else: monkeypatch.setattr(loop.tp, "_active_worker_contracts", lambda _: [("active", {})])
    loop.save(ws, state)
    assert resolve_defer_review(ws, "defer-review", **args).get("error")
    assert loop._load_raw(ws) == state


def test_public_cli_check_and_apply_use_same_explicit_command_without_outbox_flush(legacy, monkeypatch, capsys):
    from taskplane import tp as cli
    ws, root, before, packet = legacy
    path = root / "amendment.json"
    path.write_text(json.dumps(packet))
    monkeypatch.setitem(sys.modules, "loop", loop)
    monkeypatch.setattr(loop, "load", lambda *_: pytest.fail("read-only check flushed the authority outbox"))
    args = SimpleNamespace(cmd="loop", fn=cli.cmd_loop, loop_action="continue-build", workspace=ws,
        amendment_from=str(path), by="human:test", request="Append the exact repair scope; limits are advisory",
        fingerprint=fingerprint(packet), check=True)
    assert cli._invoke_run_command(args, ws) == 0
    assert json.loads(capsys.readouterr().out)["checked"] is True
    assert loop._load_raw(ws) == before
    args.check = False
    assert cli._invoke_run_command(args, ws) == 0
    assert json.loads(capsys.readouterr().out)["continued"] is True
    committed = loop._load_raw(ws)
    assert cli._invoke_run_command(args, ws) == 0
    assert json.loads(capsys.readouterr().out)["replay"] is True
    assert loop._load_raw(ws) == committed


def test_authenticated_meter_arriving_at_state_lock_is_preserved(legacy, monkeypatch):
    ws, _, _, _ = legacy
    authority, advance = _add_meter(legacy)
    original = loop.mutate
    latest = []
    @contextmanager
    def race(workspace):
        latest.append(advance())
        with original(workspace) as state:
            yield state
    monkeypatch.setattr(loop, "mutate", race)
    result = invoke(legacy, observation_authority=authority)
    assert not result.get("error"), result
    assert loop._load_raw(ws)["dispatch_telemetry"] == latest[0]["dispatch_telemetry"]
    assert loop._load_raw(ws)["root_hygiene"]["meter"] == latest[0]["root_hygiene"]["meter"]


def test_authenticated_context_rent_average_can_fall_while_cumulative_usage_grows(legacy):
    from taskplane import dispatch_telemetry, native_session_meter
    from taskplane.tests.test_native_session_meter import _write_segment
    ws, root, state, packet = legacy
    authority = b"generated-context-rent-test-authority"
    segment = root / "root.jsonl"
    observations = []
    for sequence in range(1, 53):
        if sequence <= 50:
            inputs = 862271 + (9943503 - 862271) * (sequence - 1) // 49
            cached = 812271 + (9563136 - 812271) * (sequence - 1) // 49
            outputs = 1000 + (41537 - 1000) * (sequence - 1) // 49
            reasoning = 500 + (20647 - 500) * (sequence - 1) // 49
        else:
            inputs = 9943503 + (10130316 - 9943503) * (sequence - 50) // 2
            cached = 9563136 + (9744384 - 9563136) * (sequence - 50) // 2
            outputs = 41537 + (43369 - 41537) * (sequence - 50) // 2
            reasoning = 20647 + (20894 - 20647) * (sequence - 50) // 2
        _write_segment(segment, session_id="root", total=inputs + outputs,
            cached=cached, output=outputs, ordinal=sequence * 3)
        rows = segment.read_text().splitlines()
        counter = json.loads(rows[-1])
        counter["payload"]["info"]["total_token_usage"]["reasoning_output_tokens"] = reasoning
        rows[-1] = json.dumps(counter)
        segment.write_text("\n".join(rows) + "\n")
        observations.append(native_session_meter.seal_root_observation(
            native_session_meter.read_snapshot(str(segment)), sequence=sequence, session_role="root",
            status_receipt_fingerprint="e" * 64, terminal_reason=None, authority=authority))
    before_meter = native_session_meter.fold_root_observations(observations[:50], authority=authority)
    after_meter = native_session_meter.fold_root_observations(observations[50:], authority=authority,
        prior=before_meter["watermark"])
    assert before_meter["turns"] == 50 and after_meter["turns"] == 52
    assert before_meter["context_rent_tokens"] == 191262.72
    assert after_meter["context_rent_tokens"] == 187392.0
    assert before_meter["peak_context_tokens"] == after_meter["peak_context_tokens"] == 862271
    assert all(after_meter["usage"][key] > value for key, value in before_meter["usage"].items())
    ledger = dispatch_telemetry.new_ledger(run_id=state["run_id"], source_sha="a" * 40,
        design_fingerprint=state["design_fingerprint"], plan_fingerprint=fingerprint(packet["before_plan"]), started_at=1)
    dispatch_telemetry.configure_root_admission(ledger,
        root_session_settings=packet["settings_snapshot"]["workflow"]["root_session"], settings_digest=state["settings_digest"])
    dispatch_telemetry.record_root_meter(ledger, before_meter, observation_authority=authority)
    state["dispatch_telemetry"] = ledger
    state["root_hygiene"] = {"status": "open", "meter": before_meter, "policy": "original",
        "observation_authority_fingerprint": hashlib.sha256(authority).hexdigest()}
    packet["before_state_fingerprint"] = loop_recovery.legacy_state_fingerprint(state)
    packet["observation_checkpoint"] = loop_recovery.legacy_observation_checkpoint(state)
    dispatch_telemetry.record_root_meter(ledger, after_meter, observation_authority=authority)
    state["root_hygiene"]["meter"] = after_meter
    loop.save(ws, state)
    result = invoke(legacy, observation_authority=authority)
    assert not result.get("error"), result
    assert result["continued"] is True
    assert loop._load_raw(ws)["root_hygiene"]["meter"] == after_meter
    assert loop._load_raw(ws)["dispatch_telemetry"] == ledger
