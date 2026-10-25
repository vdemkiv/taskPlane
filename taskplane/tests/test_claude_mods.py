"""Controller fixtures establish cooperative observation, not live host proof."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
import uuid

import pytest

from taskplane import claude_mods as mods, flow, primitives, workflow as w, workflow_host as host
from taskplane.tests.test_workflow_local import setup, submit, decision


def fixture(tmp_path):
    original, state = setup(tmp_path)
    original.adapter.name = "claude"
    state = submit(original, state)
    controller = mods.selected_adapter(original)
    request = {"schema": mods.REQUEST, "operation": "prepare", "intent_id": uuid.uuid4().hex,
               "binding": w.binding(state, w.current(state)["packet"]),
               "choice": "approved", "comment": ""}
    return controller, state, request


def record_request(prepared):
    intent = prepared["intent"]
    return {"schema": mods.REQUEST, "operation": "record_ui", "intent_id": intent["intent_id"],
            "payload_digest": intent["payload_digest"], "control": {"plugin": "taskplane", "component": "checkpoint",
            "element": "confirm-accept" if intent["choice"] == "approved" else "confirm-changes",
            "request_id": "pane-1", "surface": "terminal", "session": intent["binding"]["root"]}}


def receipt_request(prepared):
    return {"schema": mods.REQUEST, "operation": "receipt", "intent_id": prepared["intent"]["intent_id"]}


@pytest.mark.parametrize("choice", ["approved", "changes_requested"])
@pytest.mark.parametrize("comment", ["", "  line one\n\n第二行 🐈 é\t  "])
def test_both_explicit_decisions_preserve_frozen_content_and_ordinary_reload(tmp_path, choice, comment):
    c, state, request = fixture(tmp_path)
    request.update(choice=choice, comment=comment)
    before = c._path().read_bytes()
    prepared = mods.decide(c, request)
    assert prepared["status"] == "prepared" and prepared["receipt"] is None
    assert c._path().read_bytes() == before
    assert mods.decide(c, request) == prepared
    assert prepared["presentation"]["text"].endswith("Comment:\n" + comment)
    recorded = mods.decide(c, record_request(prepared))
    assert recorded["status"] == "recorded"
    result = recorded["receipt"]["decision"]
    assert result["choice"] == choice and result["binding"] == request["binding"]
    assert result["provenance"]["comment"] == comment
    assert result["provenance"]["source"]["kind"] == "mods_ui"
    assert result["provenance"]["source"]["assurance"] == "observed_ui"
    assert result["provenance"]["excerpt"] == prepared["presentation"]["text"]
    ordinary = host.Controller(c.workspace, c.root, host.LocalAdapter("claude"))
    assert ordinary.report()["decisions"][result["event_id"]] == result
    assert ordinary.report()["revision"] == state["revision"] + 1
    assert mods.decide(c, receipt_request(prepared))["receipt"]["decision"] == result


def test_eight_concurrent_clicks_commit_one_event_and_one_revision(tmp_path):
    c, state, request = fixture(tmp_path)
    prepared = mods.decide(c, request)
    callback = record_request(prepared)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: mods.decide(c, callback), range(8)))
    assert all(result["status"] == "recorded" for result in results)
    after = c.report()
    assert len(after["decisions"]) == 1 and after["revision"] == state["revision"] + 1
    assert len({json.dumps(r["receipt"]["decision"], sort_keys=True) for r in results}) == 1


@pytest.mark.parametrize("field", sorted(mods.BINDING_KEYS))
def test_each_binding_field_is_required_and_never_retargets(tmp_path, field):
    c, _, request = fixture(tmp_path)
    before = c._path().read_bytes()
    request["binding"][field] = request["binding"][field] + 1 if field == "revision" else "foreign"
    with pytest.raises(w.Refusal): mods.decide(c, request)
    assert c._path().read_bytes() == before
    assert not mods.ledger_path(c.workspace, c.root).exists()


@pytest.mark.parametrize("field", ["comment", "choice", "binding"])
def test_attempt_id_cannot_be_reused_for_a_different_payload(tmp_path, field):
    c, _, request = fixture(tmp_path)
    mods.decide(c, request)
    if field == "binding": request["binding"]["revision"] += 1
    else: request[field] = "changes_requested" if field == "choice" else "changed"
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match="conflicts"): mods.decide(c, request)
    assert c._path().read_bytes() == before


@pytest.mark.parametrize("field", ["plugin", "component", "element", "request_id", "surface", "session", "payload_digest"])
def test_altered_callback_or_display_digest_is_refused(tmp_path, field):
    c, _, request = fixture(tmp_path)
    prepared = mods.decide(c, request)
    callback = record_request(prepared)
    if field == "payload_digest": callback[field] = "altered"
    else: callback["control"][field] = "" if field == "request_id" else "foreign"
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal): mods.decide(c, callback)
    assert c._path().read_bytes() == before
    assert mods.decide(c, receipt_request(prepared))["status"] == "not_recorded"


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_crash_on_either_side_of_controller_commit_reconciles(tmp_path, monkeypatch, boundary):
    c, state, request = fixture(tmp_path)
    prepared = mods.decide(c, request)
    apply = c.apply
    def crash(*args, **kwargs):
        if boundary == "after": apply(*args, **kwargs)
        raise OSError("fixture lost response")
    monkeypatch.setattr(c, "apply", crash)
    with pytest.raises(OSError): mods.decide(c, record_request(prepared))
    monkeypatch.setattr(c, "apply", apply)
    reconciled = mods.decide(c, receipt_request(prepared))
    assert reconciled["status"] == ("recorded" if boundary == "after" else "not_recorded")
    assert mods.decide(c, record_request(prepared))["status"] == "recorded"
    assert c.report()["revision"] == state["revision"] + 1
    assert len(c.report()["decisions"]) == 1


def test_later_phase_receipt_never_replays_or_retargets(tmp_path, monkeypatch):
    c, _, request = fixture(tmp_path)
    prepared = mods.decide(c, request)
    result = mods.decide(c, record_request(prepared))
    state = c.report()
    c.apply("advance", state["run"], expected_revision=state["revision"], phase="design")
    monkeypatch.setattr(c, "apply", lambda *a, **kw: pytest.fail("receipt must not replay"))
    assert mods.decide(c, receipt_request(prepared))["receipt"]["decision"] == result["receipt"]["decision"]
    assert mods.decide(c, record_request(prepared))["status"] == "recorded"


def test_proof_tampering_refuses_before_controller_mutation(tmp_path, monkeypatch):
    c, _, request = fixture(tmp_path)
    prepared = mods.decide(c, request)
    apply = c.apply
    monkeypatch.setattr(c, "apply", lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture crash")))
    with pytest.raises(OSError): mods.decide(c, record_request(prepared))
    monkeypatch.setattr(c, "apply", apply)
    path = mods.ledger_path(c.workspace, c.root)
    ledger = json.loads(path.read_text())
    ledger["intents"][request["intent_id"]]["observation"]["assurance"] = "authenticated"
    path.write_text(json.dumps(ledger))
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal): mods.decide(c, record_request(prepared))
    assert c._path().read_bytes() == before


@pytest.mark.parametrize("when", ["prepare", "record_ui"])
def test_ledger_write_failure_keeps_checkpoint_unresolved(tmp_path, monkeypatch, when):
    c, _, request = fixture(tmp_path)
    if when == "record_ui": request = record_request(mods.decide(c, request))
    before = c._path().read_bytes()
    monkeypatch.setattr(mods, "_write", lambda *a: (_ for _ in ()).throw(OSError("fixture disk full")))
    with pytest.raises(OSError): mods.decide(c, request)
    assert c._path().read_bytes() == before


def test_oversize_does_not_truncate_or_create_intent(tmp_path):
    c, _, request = fixture(tmp_path)
    request["comment"] = "🐈" * 4000
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal): mods.decide(c, request)
    assert request["comment"] == "🐈" * 4000
    assert c._path().read_bytes() == before
    assert not mods.ledger_path(c.workspace, c.root).exists()


@pytest.mark.parametrize("defect", ["unknown", "duplicate", "nan", "partial_binding", "bad_id", "wrong_schema", "extra_control"])
def test_strict_request_parser(defect):
    request = {"schema": mods.REQUEST, "operation": "receipt", "intent_id": "f" * 32}
    if defect == "unknown": request["owner"] = "root"
    if defect == "nan": request["intent_id"] = float("nan")
    if defect == "bad_id": request["intent_id"] = "../escape"
    if defect == "wrong_schema": request["schema"] = "v2"
    if defect == "partial_binding": request.update(operation="prepare", binding={}, choice="approved", comment="")
    if defect == "extra_control": request["control"] = {}
    raw = json.dumps(request)
    if defect == "duplicate": raw = raw[:-1] + ',"operation":"prepare"}'
    with pytest.raises(w.Refusal): mods.read_request(raw)


@pytest.mark.parametrize("mode", ["preview", "foreign", "codex", "protected"])
def test_host_owner_and_preview_constraints(tmp_path, mode):
    c, _, request = fixture(tmp_path)
    before = c._path().read_bytes()
    path = c._path()
    if mode == "foreign": c.principal = "foreign"
    if mode == "codex": c.adapter.name = "codex"
    if mode == "protected": c.adapter.profile = "protected_host"
    with pytest.raises(w.Refusal): mods.decide(c, request, preview=mode == "preview")
    assert path.read_bytes() == before


def test_existing_conversation_envelope_delegates_unchanged(tmp_path):
    c, state, _ = fixture(tmp_path)
    value = decision(state)
    ordinary = host.Controller(c.workspace, c.root, host.LocalAdapter("claude"))
    # recorded_at is observation time, so compare the stable proof separately.
    expected = ordinary.adapter.verify_decision_context(json.dumps(value), value["binding"], {})
    actual = c.adapter.verify_decision_context(json.dumps(value), value["binding"], {})
    expected["provenance"].pop("recorded_at"); actual["provenance"].pop("recorded_at")
    assert actual == expected


def test_snapshot_is_full_authority_and_has_independent_unknown_usage(tmp_path):
    c, state, request = fixture(tmp_path)
    before = c._path().read_bytes()
    snapshot = mods.snapshot(c.workspace, state["run"], c, generation=7)
    assert snapshot["schema"] == mods.SNAPSHOT and snapshot["identity"] == request["binding"]
    assert snapshot["selection"]["generation"] == 7
    assert snapshot["decision"] == "awaiting_human_approval"
    assert snapshot["evidence"]["status"] == "validated" and snapshot["context_usage"] is None
    assert snapshot["capabilities"]["direct_decision"] is True
    assert c._path().read_bytes() == before
    preview = mods.snapshot(c.workspace, state["run"], c, preview=True)
    assert preview["capabilities"]["preview"] and not preview["capabilities"]["direct_decision"]


def test_failed_optional_telemetry_does_not_erase_authority(tmp_path, monkeypatch):
    c, state, _ = fixture(tmp_path)
    monkeypatch.setattr(flow, "report", lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture telemetry")))
    snapshot = mods.snapshot(c.workspace, state["run"], c)
    assert snapshot["status"] == "ready" and snapshot["identity"]["run"] == state["run"]
    assert snapshot["run_usage"]["tokens"] is None and snapshot["context_usage"] is None
    assert snapshot["run_usage"]["measurement"]["status"] == "unavailable"


def test_missing_selection_is_distinct_from_no_run(tmp_path):
    c = host.Controller(tmp_path, "empty", host.LocalAdapter("claude"))
    assert mods.snapshot(tmp_path, None, c)["status"] == "no_run"
    assert mods.snapshot(tmp_path, "missing", c)["status"] == "selection_unavailable"


def test_flow_cli_exposes_uncompacted_snapshot_and_refuses_preview(tmp_path, capsys):
    c, state, request = fixture(tmp_path)
    assert flow.main(["report", "--mods", "--workspace", str(tmp_path), "--run", state["run"], "--mods-generation", "9"], governor=c, compact=True) == 0
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["schema"] == mods.SNAPSHOT and snapshot["selection"]["generation"] == 9
    assert flow.main(["decide", "--workspace", str(tmp_path), "--mods-preview", "--mods-request-json", json.dumps(request)], governor=c, compact=True) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "unsupported_authority"
    assert not c.report()["decisions"]


@pytest.mark.parametrize("choice", ["approved", "changes_requested"])
def test_evidence_drift_between_prepare_and_callback(tmp_path, choice):
    c, state, request = fixture(tmp_path)
    request["choice"] = choice
    prepared = mods.decide(c, request)
    output = tmp_path / (w.current(state)["phase"] + ".json")
    output.write_text(output.read_text() + "\n")
    projection = mods.snapshot(c.workspace, state["run"], c)
    assert projection["revision"] == state["revision"]
    assert projection["evidence"]["status"] == "stale"
    assert not projection["capabilities"]["direct_decision"]
    assert projection["capabilities"]["choices"]["approved"]["direct_decision"] is False
    assert projection["capabilities"]["choices"]["changes_requested"]["direct_decision"] is True
    if choice == "approved":
        with pytest.raises(w.Refusal): mods.decide(c, record_request(prepared))
        assert not c.report()["decisions"]
    else:
        result = mods.decide(c, record_request(prepared))
        assert result["status"] == "recorded"
        assert result["receipt"]["decision"]["choice"] == "changes_requested"


@pytest.mark.parametrize("defect", ["symlink", "directory", "size", "encoding", "schema", "intent", "observation"])
def test_bounded_regular_ledger_refuses_corruption_without_deciding(tmp_path, defect):
    c, _, request = fixture(tmp_path)
    prepared = mods.decide(c, request)
    path = mods.ledger_path(c.workspace, c.root)
    if defect == "symlink":
        other = tmp_path / "ledger-copy.json"; other.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(other)
    elif defect == "directory": path.unlink(); path.mkdir()
    elif defect == "size": path.write_bytes(b" " * (mods.MAX_BYTES + 1))
    elif defect == "encoding": path.write_bytes(b"\xff")
    else:
        ledger = json.loads(path.read_text())
        if defect == "schema": ledger["schema"] = "unknown"
        elif defect == "intent": ledger["intents"][request["intent_id"]]["intent"]["comment"] = "changed"
        else: ledger["intents"][request["intent_id"]]["observation"] = {}
        path.write_text(json.dumps(ledger))
    before = c._path().read_bytes()
    with pytest.raises((w.Refusal, ValueError, OSError)): mods.decide(c, record_request(prepared))
    assert c._path().read_bytes() == before


def test_exact_encoded_ledger_limit_keeps_existing_attempt(tmp_path, monkeypatch):
    c, _, request = fixture(tmp_path)
    request["comment"] = "你好" * 200
    prepared = mods.decide(c, request)
    path = mods.ledger_path(c.workspace, c.root)
    saved = path.read_bytes()
    monkeypatch.setattr(mods, "MAX_BYTES", len(saved) + 10)
    with pytest.raises(w.Refusal, match="full"): mods.decide(c, record_request(prepared))
    assert path.read_bytes() == saved
    assert mods.decide(c, receipt_request(prepared))["status"] == "not_recorded"


def test_installed_permission_denial_remains_denial(tmp_path, monkeypatch):
    c, _, request = fixture(tmp_path)
    before = c._path().read_bytes()
    def denied(*args): raise w.Refusal("scope_violation", "fixture host/runtime denial")
    monkeypatch.setattr(c.adapter, "before_action", denied)
    with pytest.raises(w.Refusal, match="denial"): mods.decide(c, request)
    assert c._path().read_bytes() == before


def test_prepare_negative_after_drift_preserves_exact_comment(tmp_path):
    c, state, request = fixture(tmp_path)
    target = tmp_path / (w.current(state)["phase"] + ".json")
    target.write_text(target.read_text() + "\n")
    request.update(choice="changes_requested", comment="  Fix café 🛠️\n\nKeep newline.\n")
    prepared = mods.decide(c, request)
    result = mods.decide(c, record_request(prepared))
    assert result["receipt"]["decision"]["provenance"]["comment"] == request["comment"]


@pytest.mark.parametrize("case", ["fresh", "stale", "drift", "missing", "decided", "finished", "retired", "superseded_by", "archived"])
def test_snapshot_and_preparation_share_pure_state_eligibility(tmp_path, monkeypatch, case):
    c, state, request = fixture(tmp_path)
    state = c.report(state["run"])
    stage = state["visits"][state["index"]]
    if case == "stale": stage["decision"] = "stale"
    elif case == "drift": state["invalidation_pending"] = True
    elif case == "missing": stage["packet"] = None
    elif case == "decided": stage["decision"] = "approved"
    elif case != "fresh": state[case] = True
    calls = []
    monkeypatch.setattr(c, "report", lambda *_: calls.append("report") or state)
    monkeypatch.setattr(flow, "report", lambda *a, **kw: {"workflow": state})
    monkeypatch.setattr(c.adapter, "before_action", lambda *a: calls.append("permission"))
    projected = mods.snapshot(c.workspace, state["run"], c)
    assert calls == ["report"]  # No additional report or source audit per choice.
    for choice in ("approved", "changes_requested"):
        allowed = case == "fresh" or case in {"stale", "drift"} and choice == "changes_requested"
        assert projected["capabilities"]["choices"][choice]["intent"] is allowed
        if allowed:
            mods._eligible(c, request["binding"], choice)
        else:
            with pytest.raises(w.Refusal): mods._eligible(c, request["binding"], choice)


@pytest.mark.parametrize("case", ["preview", "foreign", "unsupported"])
def test_snapshot_refuses_both_choices_outside_owning_host(tmp_path, monkeypatch, case):
    c, state, _ = fixture(tmp_path)
    state = c.report(state["run"])
    if case == "foreign": c.principal = "other"
    if case == "unsupported": c.adapter.name = "codex"
    monkeypatch.setattr(c, "report", lambda *_: state)
    monkeypatch.setattr(flow, "report", lambda *a, **kw: {"workflow": state})
    result = mods.snapshot(c.workspace, state["run"], c, preview=case == "preview")
    assert all(not item["direct_decision"] for item in result["capabilities"]["choices"].values())


def test_snapshot_advice_does_not_bypass_later_permission_denial(tmp_path, monkeypatch):
    c, state, request = fixture(tmp_path)
    monkeypatch.setattr(flow, "report", lambda *a, **kw: {"workflow": c.report()})
    def denied(*args): raise w.Refusal("scope_violation", "fixture host denial")
    monkeypatch.setattr(c.adapter, "before_action", denied)
    assert mods.snapshot(c.workspace, state["run"], c)["capabilities"]["choices"]["approved"]["intent"]
    with pytest.raises(w.Refusal, match="host denial"): mods.decide(c, request)
    assert not mods.ledger_path(c.workspace, c.root).exists()


def test_snapshot_preserves_partial_journal_and_zero_tokens(tmp_path, monkeypatch):
    c, state, _ = fixture(tmp_path)
    usage = {"tokens": {"total_tokens": 0}, "token_coverage": {"measured_sessions": 2,
        "unmeasured_sessions": 0, "partial_sessions": 0, "journal_status": "partial",
        "journal_detail_status": "partial"}, "usage_measurement": {"status": "partial"}}
    monkeypatch.setattr(flow, "report", lambda *a, **kw: {"workflow": c.report(), **usage})
    result = mods.snapshot(c.workspace, state["run"], c)
    assert result["run_usage"] == {"tokens": usage["tokens"], "coverage": usage["token_coverage"],
                                   "measurement": usage["usage_measurement"]}


def test_cli_run_cannot_override_attempt_binding(tmp_path):
    c, _, request = fixture(tmp_path)
    prepared = mods.decide(c, request)
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal, match="CLI run"): mods.decide(c, record_request(prepared), run_id="foreign")
    assert c._path().read_bytes() == before


def test_broken_authority_is_not_a_confirmed_missing_selection(tmp_path):
    c, state, _ = fixture(tmp_path)
    c._path().write_text("broken")
    with pytest.raises(w.Refusal): mods.snapshot(c.workspace, state["run"], c)
    with pytest.raises(w.Refusal): mods.snapshot(c.workspace, "missing", c)


@pytest.mark.parametrize("tokens", [None, 0, {"input": 3, "output": None}])
def test_run_usage_preserves_missing_zero_and_partial_without_context_inference(tmp_path, monkeypatch, tokens):
    c, state, _ = fixture(tmp_path)
    authority = c.report()
    monkeypatch.setattr(flow, "report", lambda *a, **kw: {"workflow": authority, "tokens": tokens,
        "token_coverage": {"status": "partial"}, "usage_measurement": {"measured_at": "fixture-time"}})
    view = mods.snapshot(c.workspace, state["run"], c)
    assert view["run_usage"]["tokens"] == tokens
    assert view["run_usage"]["measurement"]["measured_at"] == "fixture-time"
    assert view["context_usage"] is None


def test_full_findings_have_captured_sources_and_recorded_metadata_only(tmp_path, monkeypatch):
    from taskplane.context import Store
    c, state, _ = fixture(tmp_path)
    state = c.report()
    source = Store(tmp_path).put("finding-evidence", {"path": "fixture.txt", "encoding": "utf-8", "text": "captured source"})
    packet = w.current(state)["packet"]
    packet["output"]["finding_references"] = [
        {"id": "F1", "title": "Recorded title", "source": "app.py:3", "evidence": "fixture.txt",
         "related_tasks": ["TASK-1"], "lenses": ["security"], "reviewer": "reviewer-1", "method": "static"},
        {"id": "F2", "title": "No source", "source": "app.py:4"}]
    packet["finding_evidence"] = {"fixture.txt": source}
    monkeypatch.setattr(flow, "report", lambda *a, **kw: {"workflow": state})
    view = mods.snapshot(c.workspace, state["run"], c)
    assert view["unresolved_count"] == 2 and view["evidence"]["status"] == "validated"
    first, second = view["findings"]
    assert first["title"] == "Recorded title" and first["source"] == "app.py:3"
    assert first["reviewer"] == "reviewer-1" and first["method"] == "static"
    assert first["related_tasks"] == ["TASK-1"] and first["lenses"] == ["security"]
    assert first["sources"][0]["text"] == "captured source" and not first["historical"]
    assert second["evidence_status"] == "unavailable" and "reviewer" not in second and "method" not in second
    assert first["disposition"] == "open" and first["verification"] == "unverified"
    Store(tmp_path).path(source["sha256"]).unlink()
    assert mods.snapshot(c.workspace, state["run"], c)["findings"][0]["sources"][0]["status"] == "unavailable"


@pytest.mark.parametrize("value", [[], {}, None, 4])
def test_non_string_operation_is_a_structured_refusal(value):
    request = {"schema": mods.REQUEST, "operation": value, "intent_id": "f" * 32}
    with pytest.raises(w.Refusal): mods.read_request(json.dumps(request))


def test_replacement_snapshot_count_and_browser_share_original_identity(tmp_path, monkeypatch):
    from taskplane import workflow_evidence as evidence
    from taskplane.context import Store
    c, state, _ = fixture(tmp_path)
    packet = w.current(state)["packet"]
    packet["output"]["findings"] = [{"id": f"F{i}", "title": f"Original {i}"} for i in range(11)]
    inherited = evidence.carry_findings(state)
    old_run, old_visit = state["run"], w.current(state)["id"]
    state = deepcopy(state)
    state["run"] = "replacement"
    state["visits"][state["index"]]["id"] = "replacement-visit"
    packet = w.current(state)["packet"]
    packet["visit"] = "replacement-visit"
    state["inherited_findings"] = inherited
    source = Store(tmp_path).put("finding-evidence", {
        "path": "review.md", "encoding": "utf-8", "text": "Current captured review"})
    packet["finding_evidence"] = {"review.md": source}
    packet["output"]["findings"] = [{"id": f"F{i}", "title": f"Rechecked {i}",
        "source": f"app.py:{i + 1}", "evidence": "review.md"} for i in range(11)]
    monkeypatch.setattr(c, "report", lambda *a, **kw: {**state, "workflow_available": True})
    monkeypatch.setattr(flow, "report", lambda *a, **kw: {"workflow": state})
    before = deepcopy(state)
    view = mods.snapshot(c.workspace, state["run"], c)
    assert view["unresolved_count"] == len(view["findings"]) == 11
    for row in view["findings"]:
        assert row["origin_run"] == old_run and row["origin"]["visit"] == old_visit
        assert row["historical"] and row["title"] == "Rechecked " + row["id"][1:]
        assert row["sources"][0]["text"] == "Current captured review"
        assert "reviewer" not in row and "method" not in row
    assert state == before


def test_adapter_dispatch_preserves_existing_verifier_input(tmp_path, monkeypatch):
    c, _, _ = fixture(tmp_path)
    supplied = []
    def original(self, reference, expected, prior):
        supplied.append(reference)
        return {"delegated": True}
    monkeypatch.setattr(host.LocalAdapter, "verify_decision_context", original)
    for reference in ['not-json', '{"schema":"other","schema":"taskplane.observed-decision/v1"}', '[]']:
        assert c.adapter.verify_decision_context(reference, {}, {}) == {"delegated": True}
    assert len(supplied) == 3


def test_source_drift_after_saved_observation_is_checked_by_controller(tmp_path, monkeypatch):
    c, state, request = fixture(tmp_path)
    prepared = mods.decide(c, request)
    apply = c.apply
    def mutate_then_apply(*args, **kwargs):
        output = tmp_path / (w.current(state)["phase"] + ".json")
        output.write_text(output.read_text() + "\n")
        return apply(*args, **kwargs)
    monkeypatch.setattr(c, "apply", mutate_then_apply)
    with pytest.raises(w.Refusal): mods.decide(c, record_request(prepared))
    assert not c.report()["decisions"]
    assert mods.decide(c, receipt_request(prepared))["status"] == "not_recorded"


def test_mod_cli_preserves_bound_worker_context_restriction(tmp_path, monkeypatch, capsys):
    from taskplane import worker_runtime
    c, state, _ = fixture(tmp_path)
    c.principal = "worker"
    monkeypatch.setattr(worker_runtime, "find", lambda state, actor: {"task_id": "TASK"} if actor == "worker" else None)
    before = c._path().read_bytes()
    assert flow.main(["report", "--mods", "--workspace", str(tmp_path), "--run", state["run"]], governor=c) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "scope_violation"
    assert c._path().read_bytes() == before


def test_foreign_reader_preserves_2317_session_restriction(tmp_path, capsys):
    c, state, _ = fixture(tmp_path)
    c.principal = "reader"
    assert flow.main(["report", "--mods", "--workspace", str(tmp_path), "--run", state["run"]], governor=c) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "scope_violation"


def test_default_mod_selection_uses_controller_run_even_when_journal_lags(tmp_path, monkeypatch):
    c, state, _ = fixture(tmp_path)
    original = flow.report
    selected = []
    def report(workspace, run_id=None, **kwargs):
        selected.append(run_id)
        return original(workspace, run_id, **kwargs)
    monkeypatch.setattr(flow, 'report', report)
    view = mods.snapshot(c.workspace, None, c)
    assert selected == [state['run']]
    assert view['selection']['run'] == state['run']
    monkeypatch.setattr(flow, 'report', lambda *a, **kw: {'workflow': {**state, 'run': 'older-journal-run'}})
    with pytest.raises(w.Refusal, match='unavailable'):
        mods.snapshot(c.workspace, None, c)
