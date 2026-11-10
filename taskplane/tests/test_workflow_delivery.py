"""Public operations and hooks share guarded policy; optional telemetry does not."""
from taskplane.tests.test_workflow_evidence import start_native
import io
import json
from unittest.mock import patch
import pytest
from taskplane import flow, flow_dashboard, workflow as w, workflow_host as h
from taskplane.tests.test_workflow_host import controller, native
from taskplane.tests.test_workflow_evidence import prepare


def cli(c, capsys, *args):
    code = flow.main([*args, "--workspace", str(c.workspace)], governor=c)
    return code, json.loads(capsys.readouterr().out)


def output(c, s):
    phase = w.current(s)["phase"]
    _, out, _ = prepare(c.workspace, phase)
    out.update(run=s["run"], visit=w.current(s)["id"])
    if w.phase_contract(s):
        from taskplane.tests.test_workflow_local import collaborative_output
        collaborative_output(c.workspace, s, out)
    (c.workspace/(phase+".json")).write_text(json.dumps(out))
    return phase+".json"


def test_all_public_phase_boundaries_require_human_decisions(tmp_path, capsys, monkeypatch):
    c, host, _ = controller(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "root")
    assert cli(c, capsys, "start")[0] == 0
    for i, phase in enumerate(w.PHASES):
        s = c.report()
        target = output(c, s)
        code, report = cli(c, capsys, "submit", "--output", target, "--tasks", "tasks.json",
                           "--expected-revision", str(s["revision"]))
        assert code == 0 and report["workflow"]["status"] == "awaiting_human_approval"
        s = c.report()
        before = host.store.read_bytes()
        code, refused = cli(c, capsys, "finish", "--expected-revision", str(s["revision"]))
        assert code == 2 and refused["reason"] == "approval_required"
        assert host.store.read_bytes() == before
        assert cli(c, capsys, "progress", "--phase", phase, "--note", "Work produced")[0] == 0
        assert c.report()["status"] == "awaiting_human_approval"
        key = native(host, s, key="human-"+phase)
        assert cli(c, capsys, "decide", "--native-event", key, "--expected-revision", str(s["revision"]))[0] == 0
        s = c.report()
        if phase != "retro":
            assert cli(c, capsys, "advance", "--phase", w.PHASES[i+1], "--expected-revision", str(s["revision"]))[0] == 0
        else:
            code, report = cli(c, capsys, "finish", "--expected-revision", str(s["revision"]))
            assert code == 0 and report["status"] == "accepted"
    assert len(report["workflow"]["decisions"]) == 7
    page = (c.workspace/".taskplane/dashboard.html").read_text()
    assert "Task decomposition" in page and "Dependency graph" in page


def test_hooks_deny_with_protected_binding_even_after_journal_deleted(tmp_path, capsys):
    c, host, s = controller(tmp_path)
    (c.workspace/flow.JOURNAL).write_text("")
    event = {"hook_event_name": "PreToolUse", "cwd": str(c.workspace), "session_id": "root",
             "tool_name": "Write", "tool_input": {"path": "app.py"}}
    with patch("sys.stdin", io.StringIO(json.dumps(event))):
        assert flow.run_hook(governor=c) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    host.store.unlink()
    with patch("sys.stdin", io.StringIO(json.dumps(event))):
        assert flow.run_hook(governor=c) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_prompt_shape_cannot_forge_a_decision_and_stop_can_wait(tmp_path, capsys):
    c, host, s = controller(tmp_path)
    target = output(c, s)
    s = c.apply("submit", s["run"], expected_revision=s["revision"], output=target, tasks="tasks.json")
    event = {"hook_event_name": "UserPromptSubmit", "cwd": str(c.workspace), "session_id": "root",
             "turn_id": "forged", "prompt": "approved", "actor": "human"}
    before = host.store.read_bytes()
    with patch("sys.stdin", io.StringIO(json.dumps(event))):
        assert flow.run_hook(governor=c) == 2
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    assert host.store.read_bytes() == before
    event["hook_event_name"] = "Stop"
    with patch("sys.stdin", io.StringIO(json.dumps(event))):
        assert flow.run_hook(governor=c) == 0
    assert json.loads(capsys.readouterr().out).get("decision") != "block"


def test_usage_failure_does_not_erase_a_valid_governance_decision(tmp_path, capsys, monkeypatch):
    c, host, s = controller(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "root")
    cli(c, capsys, "start")
    target = output(c, s)
    s = c.apply("submit", s["run"], expected_revision=s["revision"], output=target, tasks="tasks.json")
    key = native(host, s)
    with patch.object(flow.flow_usage, "reconcile", side_effect=OSError("unavailable")):
        code, r = cli(c, capsys, "decide", "--native-event", key, "--expected-revision", str(s["revision"]))
    assert code == 0 and r["workflow"]["status"] == "approved"
    assert r["evidence_errors"]


@pytest.mark.parametrize("phase", w.ENTRY_PHASES)
def test_standalone_public_entry_has_shared_artifacts_and_no_fake_predecessors(tmp_path, capsys, phase):
    c, _, _ = controller(tmp_path, {"entry": phase, "standalone": True})
    code, r = cli(c, capsys, "start", "--phase", phase, "--standalone")
    assert code == 0
    assert [v["phase"] for v in r["workflow"]["visits"]] == [phase]
    assert r["tasks"] == []  # Start does not invent an execution plan.
    _, _, tasks = prepare(c.workspace, phase)
    code, r = cli(c, capsys, "attach", "--tasks", "tasks.json", "--update-context",
                  "--run", r["run"], "--expected-revision", str(r["workflow"]["revision"]))
    assert code == 0 and {t["id"] for t in r["tasks"]} == {t["id"] for t in tasks["tasks"]}
    assert (c.workspace/".taskplane/knowledge/graph.json").exists()
    assert (c.workspace/".taskplane/dashboard.html").exists()


def test_unverified_production_controller_refuses_legacy_progress_and_finish(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "root")
    flow.append(tmp_path, {"kind": "start", "run": "legacy", "session": "root", "phase": "product"})
    for action in (["progress", "--phase", "build"], ["finish"], ["start"], ["decide", "--native-event", "approved"]):
        assert flow.main([*action, "--workspace", str(tmp_path), "--profile", "protected_host"]) == 2
        assert json.loads(capsys.readouterr().out)["reason"] in {"unsupported_authority", "approval_required"}
    assert not any(r["kind"] == "finish" for r in flow.read_events(tmp_path))
    assert not h.installed_adapter("codex").capabilities()["human_origin"]


@pytest.mark.parametrize("choice", ["approved", "changes_requested", "rejected", "cancelled"])
def test_dashboard_uses_protected_decision_even_without_journal(tmp_path, choice):
    c, host, s = controller(tmp_path)
    target = output(c, s)
    s = c.apply("submit", s["run"], expected_revision=s["revision"], output=target, tasks="tasks.json")
    page = flow_dashboard.render(str(c.workspace), flow.report(c.workspace, s["run"], governor=c))
    assert "Work: Produced" in page and "Evidence: Validated" in page
    assert "Human decision: Awaiting human approval" in page
    key = native(host, s, choice=choice)
    s = c.apply("decide", s["run"], expected_revision=s["revision"], native_reference=key)
    page = flow_dashboard.render(str(c.workspace), flow.report(c.workspace, s["run"], governor=c))
    assert "Human decision: " + choice.replace("_", " ").capitalize() in page
    assert 'class="stage recorded"' in page if choice == "approved" else 'class="stage recorded"' not in page
    (c.workspace/target).write_text("{}")
    page = flow_dashboard.render(str(c.workspace), flow.report(c.workspace, s["run"], governor=c))
    assert "Human decision: Stale" in page and "Evidence: Stale" in page
    assert 'class="stage recorded"' not in page


def test_legacy_finish_is_never_displayed_as_acceptance(tmp_path):
    flow.append(tmp_path, {"kind": "start", "run": "legacy", "session": "root", "phase": "product"})
    flow.append(tmp_path, {"kind": "finish", "run": "legacy", "session": "root", "phase": "retro", "note": "done"})
    page = flow_dashboard.render(str(tmp_path), flow.report(tmp_path, "legacy"))
    assert "Legacy observations are unverified" in page
    assert 'class="stage recorded"' not in page


def test_public_standalone_repair_cannot_skip_prerequisite_approvals(tmp_path, capsys):
    c, host, s = controller(tmp_path, {"entry": "engineering", "standalone": True})
    target = output(c, s)
    proposal = json.loads((c.workspace/target).read_text())
    proposal["route_change"] = {"kind": "repair"}
    (c.workspace/target).write_text(json.dumps(proposal))
    assert cli(c, capsys, "submit", "--output", target, "--tasks", "tasks.json",
               "--expected-revision", str(s["revision"]))[0] == 0
    s = c.report()
    key = native(host, s)
    before = host.store.read_bytes()
    code, refused = cli(c, capsys, "decide", "--native-event", key, "--expected-revision", str(s["revision"]))
    assert code == 2 and refused["reason"] == "approval_required"
    assert host.store.read_bytes() == before
    assert cli(c, capsys, "advance", "--phase", "build", "--expected-revision", str(s["revision"]))[0] == 2
    assert host.store.read_bytes() == before


@pytest.mark.parametrize("change", ["map", "task", "observation"])
def test_public_build_is_bound_to_accepted_plan_tasks(tmp_path, capsys, change):
    c, host, s = controller(tmp_path)
    for phase, next_phase in zip(w.PHASES[:3], w.PHASES[1:4]):
        target = output(c, s)
        s = c.apply("submit", s["run"], expected_revision=s["revision"], output=target, tasks="tasks.json")
        key = native(host, s, key="plan-lineage-"+phase)
        s = c.apply("decide", s["run"], expected_revision=s["revision"], native_reference=key)
        s = c.apply("advance", s["run"], expected_revision=s["revision"], phase=next_phase)
    target = output(c, s)
    out = json.loads((c.workspace/target).read_text())
    tasks = json.loads((c.workspace/"tasks.json").read_text())
    if change in ("map", "task"):
        out["task_acceptance_map"] = {"AC1": ["NEW-TASK"]}
        if change == "task":
            tasks["tasks"][0]["id"] = "NEW-TASK"
    else:
        tasks["tasks"][0].update(status="complete", completed_at="2026-09-16T12:00:00Z")
    (c.workspace/target).write_text(json.dumps(out))
    (c.workspace/"tasks.json").write_text(json.dumps(tasks))
    before = host.store.read_bytes()
    code, report = cli(c, capsys, "submit", "--output", target, "--tasks", "tasks.json",
                       "--expected-revision", str(s["revision"]))
    if change == "observation":
        assert code == 0 and report["workflow"]["status"] == "awaiting_human_approval"
    else:
        assert code == 2 and report["reason"] == "invalid_evidence"
        assert host.store.read_bytes() == before


@pytest.mark.parametrize("route", ["delivery", "repair"])
def test_extended_routes_accept_changed_build_without_losing_history(tmp_path, route):
    request = {"entry": "engineering", "standalone": True} if route == "delivery" else {}
    c, host, s = controller(tmp_path, request)
    original = s["run"]
    if route == "repair":
        for phase in w.PHASES[:5]:
            target = output(c, s)
            s = c.apply("submit", original, expected_revision=s["revision"], output=target, tasks="tasks.json")
            key = native(host, s, key="initial-"+phase)
            s = c.apply("decide", original, expected_revision=s["revision"], native_reference=key)
            s = c.apply("advance", original, expected_revision=s["revision"], phase=w.PHASES[w.PHASES.index(phase)+1])
    target = output(c, s)
    proposal = json.loads((c.workspace/target).read_text())
    proposal["route_change"] = {"kind": route, **({"scope": host.scope} if route == "delivery" else {})}
    (c.workspace/target).write_text(json.dumps(proposal))
    s = c.apply("submit", original, expected_revision=s["revision"], output=target, tasks="tasks.json")
    key = native(host, s, key="route-human")
    s = c.apply("decide", original, expected_revision=s["revision"], native_reference=key)
    phases = w.PHASES if route == "delivery" else w.PHASES[3:]
    for phase in phases:
        s = c.apply("advance", original, expected_revision=s["revision"], phase=phase)
        target = output(c, s)
        if phase == "build":
            (c.workspace/"app.py").write_text("value = 2 # accepted Build scope\n")
            from taskplane import depgraph
            depgraph.scan(str(c.workspace), decompose=True, strict=True)
        s = c.apply("submit", original, expected_revision=s["revision"], output=target, tasks="tasks.json")
        key = native(host, s, key="new-"+phase)
        s = c.apply("decide", original, expected_revision=s["revision"], native_reference=key)
    s = c.apply("finish", original, expected_revision=s["revision"])
    assert s["finished"] and s["run"] == original and s["history"]
    assert s["decisions"]["route-human"]["choice"] == "approved"
    assert (c.workspace/"app.py").read_text().startswith("value = 2")
    page = flow_dashboard.render(str(c.workspace), flow.report(c.workspace, original, governor=c))
    assert "Superseded history" in page
    assert page.count('class="stage recorded"') == 7


def test_committed_actions_survive_journal_failure_and_decision_replay(tmp_path, capsys):
    c, host, initial = controller(tmp_path)
    for i, phase in enumerate(w.PHASES):
        s = c.report()
        target = output(c, s)
        with patch.object(flow, 'append', side_effect=OSError('observation unavailable')):
            code, result = cli(c, capsys, 'submit', '--output', target, '--tasks', 'tasks.json',
                               '--expected-revision', str(s['revision']))
        assert code == 0 and result['workflow']['status'] == 'awaiting_human_approval'
        assert any('action committed' in message for message in result['evidence_errors'])
        s = c.report()
        key = native(host, s, key='journal-'+phase)
        with patch.object(flow, 'append', side_effect=OSError('observation unavailable')):
            for _ in range(2):
                code, result = cli(c, capsys, 'decide', '--native-event', key,
                                   '--expected-revision', str(s['revision']))
                assert code == 0 and result['workflow']['status'] == 'approved'
                assert len(result['workflow']['decisions']) == i+1
                assert result['evidence_errors']
        s = c.report()
        action = ['finish'] if phase == 'retro' else ['advance', '--phase', w.PHASES[i+1]]
        with patch.object(flow, 'append', side_effect=OSError('observation unavailable')):
            code, result = cli(c, capsys, *action, '--expected-revision', str(s['revision']))
        assert code == 0 and result['evidence_errors']
        assert result['workflow']['status'] == ('accepted' if phase == 'retro' else 'not_requested')
    assert c.report(initial['run'])['status'] == 'accepted'
    assert len(c.report(initial['run'])['decisions']) == 7


def test_authoritative_store_failure_still_refuses_without_saved_decision(tmp_path, capsys):
    c, host, s = controller(tmp_path)
    target = output(c, s)
    s = c.apply('submit', s['run'], expected_revision=s['revision'], output=target, tasks='tasks.json')
    key = native(host, s)
    before = host.store.read_bytes()
    with patch.object(c, '_write', side_effect=OSError('authoritative store unavailable')):
        code, result = cli(c, capsys, 'decide', '--native-event', key, '--expected-revision', str(s['revision']))
    assert code == 2 and result['status'] == 'blocked' and result['reason'] == 'state_unavailable'
    assert host.store.read_bytes() == before
    assert c.report()['status'] == 'awaiting_human_approval'
    assert not c.report()['decisions']


@pytest.mark.parametrize('command', ['screen', 'context', 'session-verify', None, 'unknown'])
@pytest.mark.parametrize('payload', ['[]', 'null', 'false', '17', '"text"', '{broken'])
def test_malformed_hook_input_returns_named_refusal_without_mutation(tmp_path, capsys, command, payload):
    c, host, _ = controller(tmp_path)
    before = host.store.read_bytes()
    with patch('sys.stdin', io.StringIO(payload)):
        code = flow.run_hook(command=command, governor=c)
    result = json.loads(capsys.readouterr().out)
    if command == 'screen':
        assert code == 0 and result['hookSpecificOutput']['permissionDecision'] == 'deny'
        assert result['hookSpecificOutput']['hookEventName'] == 'PreToolUse'
    elif command == 'session-verify':
        assert code == 0 and result['continue'] is False and result['stopReason'] and 'decision' not in result
    else:
        assert code == 2 and result['decision'] == 'block'
    assert host.store.read_bytes() == before


def test_named_screen_command_keeps_its_contract_when_payload_names_stop(tmp_path, capsys):
    c, host, _ = controller(tmp_path)
    before = host.store.read_bytes()
    payload = {'hook_event_name':'Stop', 'cwd':str(c.workspace), 'session_id':'root',
               'tool_name':'Write', 'tool_input':{'path':'app.py'}}
    with patch('sys.stdin', io.StringIO(json.dumps(payload))):
        assert flow.run_hook(command='screen', governor=c) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert host.store.read_bytes() == before


def test_revision_bound_design_feedback_and_plan_handoff(tmp_path):
    import base64
    import hashlib
    from taskplane import workflow_local, workflow_evidence as evidence
    from taskplane.context import Store
    from taskplane.primitives import content_fingerprint
    from taskplane.tests.test_workflow_host import collaborative_repair_submit
    from taskplane.tests.test_workflow_local import (
        collaborative_setup, collaborative_output, decide, decision, refine, refinement)

    c, state, product = collaborative_setup(tmp_path)
    state = collaborative_repair_submit(c, state, product)
    state = decide(c, state, decision(state, event="accept-product"))
    state = c.apply("advance", state["run"], expected_revision=state["revision"], phase="design")
    _, design, _ = prepare(tmp_path, "design")
    design.update(run=state["run"], visit=w.current(state)["id"])
    collaborative_output(tmp_path, state, design)
    (tmp_path/"design.json").write_text(json.dumps(design))
    state = refine(c, state, refinement(state, "draft", {"output": "design.json"}, human=False))
    head = w.current(state)["phase_review"]["ref"]
    store = Store(tmp_path)
    first = store.resolve(head)["artifacts"][0]
    request = refinement(state, "feedback", {})
    request["excerpt"] = "Please label the stored decision with its revision."
    feedback = {"id": "revision-label", "criteria": ["AC1"], "disposition": "open",
                "target": {"id": first["id"], "revision": 1, "digest": first["sha256"]},
                "source": workflow_local.refinement_source(state, request)}
    request["payload"] = feedback
    state = refine(c, state, request)

    original = (tmp_path/"design.md").read_bytes()
    updated = original.replace(b"persist decision", b"persist decision and revision")
    (tmp_path/"design.md").write_bytes(updated)
    artifact = design["phase_review"]["artifacts"][0]
    artifact.update(revision=2, sha256=hashlib.sha256(updated).hexdigest())
    design["phase_review"].update(revision=2, feedback=[{**feedback, "disposition": "addressed",
        "resulting_revision": 2, "response": "Labelled the revision stored with each decision.",
        "evidence": [{"path": artifact["path"], "sha256": artifact["sha256"]}]}])
    state = collaborative_repair_submit(c, state, design)
    state = decide(c, state, decision(state, event="accept-design"))
    packet = w.current(state)["packet"]
    plan = c.apply("advance", state["run"], expected_revision=state["revision"], phase="plan")
    assert evidence.accepted_review_inputs(plan)[-1] == {
        "checkpoint": packet["checkpoint"], "digest": content_fingerprint(packet)}
    from taskplane.context_handoff import Session, consume_required
    session = Session(tmp_path, plan)
    receipt, _ = consume_required(session)
    assert receipt["consumed_inputs"]
    received = {item["id"]: session.store.resolve(item["ref"]) for item in session.required}
    predecessor = received["accepted/" + packet["visit"]]
    assert predecessor["phase_review"]["revision"] == 2
    assert predecessor["system_design"] == design["system_design"]
    assert received["artifact/design.md"]["text"].encode() == updated
    second = packet["review_artifacts"][0]
    for pinned, expected in [(first, original), (second, updated)]:
        assert base64.b64decode(store.resolve(pinned["body_ref"])["data"]) == expected
        assert "Reviewer" in pinned["fallback"]
    assert packet["output"]["phase_review"]["feedback"][0]["target"]["digest"] == first["sha256"]
    (tmp_path/"design.md").write_bytes(original)
    assert evidence.changed(tmp_path, plan)[0] == packet["visit"]


def test_plan_handoff_concurrent_edit_uses_approved_view(tmp_path):
    import base64
    import hashlib
    from copy import deepcopy
    from taskplane import context_handoff as handoff
    from taskplane.context import Store
    from taskplane.tests.test_workflow_evidence import DESIGN_DIAGRAM, png_container
    from taskplane.tests.test_workflow_host import collaborative_repair_submit
    from taskplane.tests.test_workflow_local import collaborative_output, decide, decision

    initial, product, _ = prepare(tmp_path)
    initial["scope"]["paths"]["product"].append("design.md")
    initial["scope"]["paths"]["design"].append("design.png")
    approved = {"design.md": DESIGN_DIAGRAM.encode(), "design.png": png_container()}
    for path, raw in approved.items():
        (tmp_path/path).write_bytes(raw)
    c = h.Controller(tmp_path, "root", h.installed_adapter("codex"))
    state = start_native(c, {"scope": initial["scope"], "request_reference": "fixture/diagram-handoff"},
                    phase_contract=w.PHASE_CONTRACT)
    product.update(run=state["run"], visit=w.current(state)["id"])
    product["artifacts"] = [{"path": "design.md", "kind": "supporting", "schema": "markdown",
                             "phase": "product", "visit": product["visit"], "criteria": ["AC1"], "tasks": []}]
    state = collaborative_repair_submit(c, state, collaborative_output(tmp_path, state, product))
    state = decide(c, state, decision(state, event="accept-product"))
    state = c.apply("advance", state["run"], expected_revision=state["revision"], phase="design")
    _, design, _ = prepare(tmp_path, "design")
    design.update(run=state["run"], visit=w.current(state)["id"])
    collaborative_output(tmp_path, state, design)
    artifacts = design["phase_review"]["artifacts"]
    artifacts.append({**artifacts[0], "id": "image", "path": "design.png", "media_type": "image/png",
                      "sha256": hashlib.sha256(approved["design.png"]).hexdigest()})
    design["system_design"]["views"].append({"id": "picture", "criteria": ["AC1"],
                                             "artifact": "image", "format": "png"})
    state = collaborative_repair_submit(c, state, design)
    state = decide(c, state, decision(state, event="accept-design"))
    plan = c.apply("advance", state["run"], expected_revision=state["revision"], phase="plan")
    store = Store(tmp_path)
    assemble = handoff.inputs

    def edit_after_freshness_check(workspace, state, task):
        try:
            for path in approved:
                (workspace/path).write_bytes(b"UNREVIEWED DIAGRAM\n")
            return assemble(workspace, state, task)
        finally:
            for path, raw in approved.items():
                (workspace/path).write_bytes(raw)

    def read_views():
        view = store.resolve(c.context(plan["run"])["view_ref"])
        rows = store.resolve(view["references"]["details"])
        return {path: c.context(plan["run"], read=row["ref"]["sha256"])["page"]["data"]
                for path in approved for row in rows if row["id"] == "artifact/" + path}

    expected = {"design.md": {"path": "design.md", "encoding": "utf-8", "text": DESIGN_DIAGRAM},
                "design.png": {"path": "design.png", "encoding": "base64",
                               "text": base64.b64encode(approved["design.png"]).decode("ascii")}}
    assert read_views() == expected
    with patch.object(handoff, "inputs", edit_after_freshness_check):
        assert read_views() == expected
    required = {row["id"] for row in handoff.Session(tmp_path, plan).required}
    assert {"artifact/design.md", "artifact/design.png"} <= required

    packet = plan["visits"][1]["packet"]
    pins = packet["review_artifacts"]
    pin = pins[0]
    body = store.resolve(pin["body_ref"])
    for broken in ([], [dict(pin, body_ref=None)], [dict(pin, body_ref=pins[1]["body_ref"])],
                   [dict(pin, body_ref={**pin["body_ref"], "sha256": "0"*64})],
                   [dict(pin, body_ref=store.put("phase-review-artifact", {**body, "data": "?"}))]):
        with patch.dict(packet, review_artifacts=broken + pins[1:]), pytest.raises(w.Refusal) as refused:
            assemble(tmp_path, plan, None)
        assert refused.value.reason == "invalid_context"

    # Creation contracts predating the index did not require a pinned view.
    for contract in ("collaborative/v1", None):
        legacy = deepcopy(plan)
        legacy_packet = legacy["visits"][1]["packet"]
        legacy_packet.pop("review_artifacts")
        legacy_packet["output"].pop("system_design")
        if contract:
            legacy["run_contract"]["phase_contract"] = contract
            legacy["phase_contract"] = legacy_packet["phase_contract"] = contract
            for visit in legacy["visits"]:
                visit["phase_contract"] = contract
        else:
            legacy.pop("phase_contract")
            legacy.pop("run_contract")
            legacy_packet.pop("phase_contract")
            for visit in legacy["visits"]:
                visit.pop("phase_contract")
        items, *_ = assemble(tmp_path, legacy, None)
        assert {item["body"]["path"]: item["body"] for item in items
                if item["id"] in {"artifact/design.md", "artifact/design.png"}} == expected

    (tmp_path/"design.md").write_text("Persistent workspace drift")
    with pytest.raises(w.Refusal) as refused:
        c.context(plan["run"])
    assert refused.value.reason == "invalid_context" and c.report(plan["run"])["invalidation_pending"]
