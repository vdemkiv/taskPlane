"""Farm F3/F4/F5/F9: obligations, executable plans and immutable attempts."""
from copy import deepcopy
import hashlib
import json
import subprocess
import sys

import pytest

from taskplane import context_reuse as reuse, workflow as w, workflow_evidence as e
from taskplane.context import Store


def state_for(tmp_path, phase="build"):
    scope = {"criteria": ["AC1"], "paths": {phase: [phase + ".json"] for phase in w.PHASES},
             "verification_inputs": ["app.py", "test_app.py"]}
    scope["paths"]["build"] = ["build.json", "report.md", "history.json", "app.py",
                                "check-1.log", "check-2.log", "check-3.log"]
    state = w.new_state(str(tmp_path), "root", "run-a", scope)
    state["index"] = w.PHASES.index(phase)
    return state


def contract(tmp_path, *, kind="unit", environment="local"):
    state = state_for(tmp_path)
    (tmp_path / "app.py").write_text("value = 1\n")
    (tmp_path / "test_app.py").write_text("assert value == 2\n")
    task = {"id": "BUILD", "phase": "build", "owner": "root", "dependencies": [],
            "paths": list(state["scope"]["paths"]["build"]), "criteria": ["AC1"],
            "read_inputs": ["app.py", "test_app.py"], "verification": "Execute the declared assertion"}
    requirement = {"id": "assertion", "name": "Application assertion", "task": "BUILD",
        "criteria": ["AC1"], "kind": kind, "environment": environment, "required": True,
        "source_inputs": ["app.py"], "test_inputs": ["test_app.py"],
        "command": [sys.executable, "-c", "exec(open('app.py').read()); exec(open('test_app.py').read())"],
        "evidence_outputs": ["check-1.log", "check-2.log", "check-3.log"]}
    plan = {"task_dag": [task], "write_scope": list(task["paths"]), "integration_order": ["BUILD"],
        "build_outputs": [{"kind": kind, "path": path, "task": "BUILD"} for kind, path in
                          [("packet", "build.json"), ("report", "report.md"), ("verification_history", "history.json")]],
        "verification_strategy": {"schema": e.VERIFICATION_STRATEGY, "checks": [requirement]}}
    state["visits"][2].update(decision="approved", packet={"output": plan})
    return state, plan, requirement


def fake_key(workspace, *, paths, tests, criteria, command, tool, contract, **unused):
    # Unit fixture binds real source/test bytes without depending on scanner tests.
    return {"schema": "taskplane.verification-key/v1", "eligible": True,
        "paths": paths, "tests": tests, "criteria": criteria, "command": command,
        "tool": tool, "contract": contract,
        "files": {path: hashlib.sha256((workspace / path).read_bytes()).hexdigest() for path in paths + tests}}


def attempt(tmp_path, monkeypatch, state, requirement, log, **options):
    monkeypatch.setattr(reuse, "key", fake_key)
    provenance = reuse.key(tmp_path, paths=requirement["source_inputs"], tests=requirement["test_inputs"],
        criteria=requirement["criteria"], command=requirement["command"], tool="python", contract="assertion/v1")
    ref = reuse.run_check(tmp_path, provenance, producer="root", history_path="history.json",
        run=state["run"], visit=w.current(state)["id"], check_id=requirement["id"],
        kind=requirement["kind"], environment=requirement["environment"], log_path=log, **options)
    history = json.loads((tmp_path / "history.json").read_text())
    row = history["attempts"][-1]
    assert row["record_ref"] == ref
    output = {"verification_history": "history.json", "build_checks": [
        {"name": requirement["name"], "check_id": row["check_id"], "attempt_id": row["id"],
         "status": row["status"], "evidence": row["evidence"]}]}
    return output, history, ref


def capture(state, output, validation):
    state["visits"][state["index"]].update(decision="approved", packet={
        "phase": "build", "visit": w.current(state)["id"], "output": deepcopy(output),
        "verification": deepcopy(validation)})


def test_typed_plan_declares_future_owned_outputs(tmp_path):
    state, plan, _ = contract(tmp_path)
    assert e.plan_preflight(tmp_path, state, plan, plan["task_dag"])["status"] == "validated"
    assert not (tmp_path / "build.json").exists()


@pytest.mark.parametrize("defect", ["packet", "report", "history", "duplicate-owner", "undeclared-write",
                                    "missing-helper", "undeclared-read", "undeclared-log", "wrong-owner"])
def test_plan_preflight_rejects_incomplete_contract(tmp_path, defect):
    state, plan, check = contract(tmp_path)
    if defect in {"packet", "report", "history"}:
        kind = "verification_history" if defect == "history" else defect
        plan["build_outputs"] = [row for row in plan["build_outputs"] if row["kind"] != kind]
    elif defect == "duplicate-owner":
        plan["task_dag"].append({**deepcopy(plan["task_dag"][0]), "id": "SECOND"})
        plan["integration_order"].append("SECOND")
    elif defect == "undeclared-write":
        plan["task_dag"][0]["paths"].append("unscoped.py")
    elif defect == "missing-helper":
        state["scope"]["verification_inputs"].append("helper.py")
        plan["task_dag"][0]["read_inputs"].append("helper.py")
        check["test_inputs"].append("helper.py")
    elif defect == "undeclared-read":
        plan["task_dag"][0]["read_inputs"].remove("test_app.py")
    elif defect == "undeclared-log":
        check["evidence_outputs"].append("unplanned.log")
    else:
        plan["build_outputs"][0]["task"] = "MISSING"
    with pytest.raises(w.Refusal):
        e.plan_preflight(tmp_path, state, plan, plan["task_dag"])


def test_generated_input_requires_producer_order(tmp_path):
    state, plan, check = contract(tmp_path)
    state["scope"]["paths"]["build"].append("generated.py")
    plan["write_scope"].append("generated.py")
    check["test_inputs"].append("generated.py")
    plan["task_dag"][0]["read_inputs"].append("generated.py")
    producer = {"id": "GENERATE", "phase": "build", "paths": ["generated.py"], "owner": "generator",
                "criteria": ["AC1"], "dependencies": [], "verification": "Check generated fixture"}
    plan["task_dag"].append(producer)
    plan["integration_order"] = ["GENERATE", "BUILD"]
    with pytest.raises(w.Refusal, match="producer dependency"):
        e.plan_preflight(tmp_path, state, plan, plan["task_dag"])
    plan["task_dag"][0]["dependencies"] = ["GENERATE"]
    assert e.plan_preflight(tmp_path, state, plan, plan["task_dag"])["status"] == "validated"


def test_native_new_plan_requires_typed_contract_but_legacy_remains_readable(tmp_path):
    state = state_for(tmp_path, "plan")
    assert e.plan_preflight(tmp_path, state, {}, []) == {"status": "legacy_untyped"}
    state["scope"]["execution_contract"] = "native-default/v1"
    with pytest.raises(w.Refusal):
        e.plan_preflight(tmp_path, state, {}, [])


def test_build_requires_exact_planned_packet_and_report(tmp_path):
    state, _, _ = contract(tmp_path)
    for relative in ("build.json", "report.md", "history.json"):
        (tmp_path / relative).write_text("{}")
    with pytest.raises(w.Refusal, match="exact planned packet"):
        e.validate_build_outputs(tmp_path, state, {}, "other.json")
    with pytest.raises(w.Refusal, match="planned report"):
        e.validate_build_outputs(tmp_path, state, {}, "build.json")
    e.validate_build_outputs(tmp_path, state, {"artifacts": [
        {"kind": "report", "path": "report.md", "tasks": ["BUILD"]}]}, "build.json")


def test_fail_then_repair_pass_retains_both_attempts(tmp_path, monkeypatch):
    state, _, check = contract(tmp_path)
    out, first, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    validation = e.validate_verification(tmp_path, state, out)
    capture(state, out, validation)
    assert e.effective_checks(state)[0]["status"] == "fail"
    (tmp_path / "app.py").write_text("value = 2\n")
    out, second, _ = attempt(tmp_path, monkeypatch, state, check, "check-2.log")
    validation = e.validate_verification(tmp_path, state, out)
    capture(state, out, validation)
    assert second["attempts"][:1] == first["attempts"]
    assert [row["status"] for row in second["attempts"]] == ["fail", "pass"]
    assert e.effective_checks(state)[0]["status"] == "pass"
    assert len(e.carry_findings(state)["verification_histories"][0]["attempts"]) == 2


def test_pass_then_fail_cannot_select_old_pass(tmp_path, monkeypatch):
    state, _, check = contract(tmp_path)
    (tmp_path / "app.py").write_text("value = 2\n")
    passed, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    (tmp_path / "app.py").write_text("value = 1\n")
    failed, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-2.log")
    with pytest.raises(w.Refusal, match="latest attempt"):
        e.validate_verification(tmp_path, state, passed)
    capture(state, failed, e.validate_verification(tmp_path, state, failed))
    assert e.effective_checks(state)[0]["status"] == "fail"


@pytest.mark.parametrize("phase", ["engineering", "retro"])
def test_later_phase_policy_lookup_uses_current_build_checks(tmp_path, phase):
    state, _, check = contract(tmp_path)
    capture(state, {"build_checks": [{"check_id": check["id"], "status": "pass"}]},
            {"effective_checks": [{"check_id": check["id"], "status": "pass"}]})
    build = state["visits"][state["index"]]
    state["index"] = w.PHASES.index(phase)
    output = {"phase": phase}
    state["visits"][state["index"]].update(decision="awaiting_human_approval", packet={
        "output": output, "verification": {"status": "legacy_untyped", "files": []}})
    assert e.effective_checks(state, output)[0]["status"] == "pass"
    build["decision"] = "stale"
    assert e.effective_checks(state, output)[0]["status"] == "unknown"


def test_evaluate_policy_lookup_preserves_its_own_unknown_snapshot(tmp_path):
    state, _, check = contract(tmp_path)
    capture(state, {"build_checks": [{"check_id": check["id"], "status": "pass"}]},
            {"effective_checks": [{"check_id": check["id"], "status": "pass"}]})
    state["index"] = w.PHASES.index("evaluate")
    output = {"criterion_results": {"AC1": {"status": "unknown"}}}
    state["visits"][state["index"]].update(decision="awaiting_human_approval", packet={
        "output": output, "verification": {"effective_checks": [
            {"check_id": check["id"], "status": "unknown"}]}})
    assert e.effective_checks(state, output)[0]["status"] == "unknown"


@pytest.mark.parametrize("tamper", ["remove", "reorder", "status", "foreign", "log"])
def test_captured_attempts_and_logs_cannot_be_rewritten(tmp_path, monkeypatch, tamper):
    state, _, check = contract(tmp_path)
    out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    capture(state, out, e.validate_verification(tmp_path, state, out))
    out, history, _ = attempt(tmp_path, monkeypatch, state, check, "check-2.log")
    if tamper == "remove":
        history["attempts"].pop(0)
    elif tamper == "reorder":
        history["attempts"].reverse()
    elif tamper == "status":
        history["attempts"][0]["status"] = "pass"
    elif tamper == "foreign":
        history["run"] = "another-run"
    else:
        (tmp_path / "check-1.log").write_text("The failed attempt is now described as passed")
    (tmp_path / "history.json").write_text(json.dumps(history))
    with pytest.raises(w.Refusal):
        e.validate_verification(tmp_path, state, out)


def test_missing_historical_log_uses_pinned_body_and_stale_pass_is_unknown(tmp_path, monkeypatch):
    state, _, check = contract(tmp_path)
    out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    (tmp_path / "app.py").write_text("value = 2\n")
    out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-2.log")
    (tmp_path / "check-1.log").unlink()
    assert e.validate_verification(tmp_path, state, out)["effective_checks"][0]["status"] == "pass"
    (tmp_path / "app.py").write_text("value = 3\n")
    assert e.validate_verification(tmp_path, state, out)["effective_checks"][0]["status"] == "unknown"


@pytest.mark.parametrize("failure", ["timeout", "launch"])
def test_runtime_failures_append_unknown_attempt(tmp_path, monkeypatch, failure):
    state, _, check = contract(tmp_path)
    def broken(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args[0], 1, output=b"partial output")
        raise FileNotFoundError(2, "missing executable")
    monkeypatch.setattr(reuse.subprocess, "run", broken)
    out, history, ref = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    assert history["attempts"][0]["status"] == "unknown"
    result = Store(tmp_path).resolve(ref)["result"]
    assert result["reason"] == ("timeout" if failure == "timeout" else "launch_error")
    assert e.validate_verification(tmp_path, state, out)["effective_checks"][0]["status"] == "unknown"


def test_input_drift_is_recorded_without_running_command(tmp_path, monkeypatch):
    state, _, check = contract(tmp_path)
    monkeypatch.setattr(reuse, "key", fake_key)
    provenance = fake_key(tmp_path, paths=check["source_inputs"], tests=check["test_inputs"],
        criteria=check["criteria"], command=check["command"], tool="python", contract="assertion/v1")
    (tmp_path / "app.py").write_text("value = 3\n")
    monkeypatch.setattr(reuse.subprocess, "run", lambda *a, **k: pytest.fail("stale command must not run"))
    ref = reuse.run_check(tmp_path, provenance, producer="root", history_path="history.json",
        run=state["run"], visit=w.current(state)["id"], check_id=check["id"], kind="unit", environment="local",
        log_path="check-1.log")
    assert Store(tmp_path).resolve(ref)["status"] == "unknown"
    assert len(json.loads((tmp_path / "history.json").read_text())["attempts"]) == 1


@pytest.mark.parametrize("kind,environment", [("browser", "local"), ("integration", "deployed")])
def test_stronger_coverage_requires_actual_environment_details(tmp_path, monkeypatch, kind, environment):
    state, _, check = contract(tmp_path, kind=kind, environment=environment)
    (tmp_path / "app.py").write_text("value = 2\n")
    out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    with pytest.raises(w.Refusal, match="coverage needs"):
        e.validate_verification(tmp_path, state, out)
    details = {"engine": "Chromium", "version": "fixture", "interactions": ["save", "reload"]} if kind == "browser" else {
        "target": "fixture-service", "service_result": "observed response"}
    out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-2.log", coverage_details=details)
    assert e.validate_verification(tmp_path, state, out)["effective_checks"][0]["status"] == "pass"


def test_fixture_static_cannot_satisfy_browser_local(tmp_path, monkeypatch):
    state, _, check = contract(tmp_path)
    out, history, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    check.update(kind="browser", environment="local")
    with pytest.raises(w.Refusal, match="kind/environment"):
        e.validate_verification(tmp_path, state, out)


def test_evaluate_pass_requires_all_declared_mandatory_checks(tmp_path, monkeypatch):
    state, plan, check = contract(tmp_path)
    (tmp_path / "app.py").write_text("value = 2\n")
    out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    plan["verification_strategy"]["checks"].append({**deepcopy(check), "id": "required-browser", "kind": "browser"})
    validation = e.validate_verification(tmp_path, state, out)
    capture(state, out, validation)
    state["index"] = 4
    evaluated = {"criterion_results": {"AC1": {"status": "pass"}}}
    with pytest.raises(w.Refusal, match="mandatory check"):
        e.validate_verification(tmp_path, state, evaluated)
    evaluated["criterion_results"]["AC1"]["status"] = "unknown"
    assert e.validate_verification(tmp_path, state, evaluated)["effective_checks"][-1]["status"] == "unknown"


def test_independent_check_cannot_use_root_as_reviewer(tmp_path, monkeypatch):
    from taskplane import worker_runtime
    state, _, check = contract(tmp_path, kind="independent")
    (tmp_path / "app.py").write_text("value = 2\n")
    state["task_results"] = {"BUILD": {"worker_id": "root", "grant": "claimed"}}
    monkeypatch.setattr(worker_runtime, "native_result_valid", lambda *a: True)
    monkeypatch.setattr(worker_runtime, "result_valid", lambda *a: True)
    out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log",
                        coverage_details={"reviewer": "root", "grant": "claimed"})
    with pytest.raises(w.Refusal, match="actual distinct reviewer"):
        e.validate_verification(tmp_path, state, out)


def original_finding(tmp_path):
    state = state_for(tmp_path, "product")
    body = "Actual principal grants and denied users.json/config.json writes remain unverified."
    (tmp_path / "risk.md").write_text(body)
    ref = Store(tmp_path).put("finding-evidence", {"path": "risk.md", "encoding": "utf-8", "text": body})
    declaration = {"id": "SEC-2", "owner": "infrastructure", "required_evidence": ["actual grants", "write denials"],
                   "criterion": "ORIGINAL-SECURITY", "evidence": "risk.md", "disposition": "deferred", "reason": "External owner"}
    state["visits"][0].update(decision="approved", packet={"phase": "product", "visit": w.current(state)["id"],
        "output": {"finding_references": [declaration]}, "finding_evidence": {"risk.md": ref}})
    return state


def test_replacements_preserve_original_obligations_and_immutable_context(tmp_path, monkeypatch):
    from taskplane import context_handoff
    original = original_finding(tmp_path)
    inherited = e.carry_findings(original)
    second = state_for(tmp_path, "product")
    second.update(run="run-b", inherited_findings=inherited)
    third = state_for(tmp_path, "product")
    third.update(run="run-c", inherited_findings=e.carry_findings(second))
    (tmp_path / "risk.md").write_text("Narrow report: all current checks pass")
    rows = e.finding_register(third)
    assert rows[0]["id"] == "SEC-2" and rows[0]["origin_run"] == "run-a"
    assert rows[0]["disposition"] == "deferred" and rows[0]["owner"] == "infrastructure"
    assert rows[0]["required_evidence"] == ["actual grants", "write denials"]
    monkeypatch.setattr(context_handoff.depgraph, "load", lambda _: {})
    items, _, _, _ = context_handoff.inputs(tmp_path, third, None)
    assert any(item["kind"] == "finding" and item.get("required", True) for item in items)
    immutable = next(item["body"] for item in items if item["kind"] == "finding-evidence")
    assert "remain unverified" in immutable["text"]
    assert not any(key in inherited for key in ("decisions", "approval_policy", "grants", "context_receipt"))


def test_omitted_renamed_pending_and_stale_resolution_cannot_hide_finding(tmp_path):
    state = original_finding(tmp_path)
    stage = state["visits"][3]
    state["index"] = 3
    update = {"origin_run": state["run"], "id": "SEC-2", "disposition": "resolved",
              "verification": "runtime_verified", "owner": "infrastructure", "reason": "Claimed pass", "evidence": "risk.md"}
    stage.update(decision="awaiting_human_approval", packet={"phase": "build", "visit": stage["id"],
        "output": {"finding_updates": [update]}})
    assert e.finding_register(state)[0]["disposition"] == "deferred"
    stage["decision"] = "stale"
    assert e.finding_register(state)[0]["disposition"] == "deferred"
    update["id"] = "RENAMED"
    with pytest.raises(w.Refusal, match="unknown, renamed"):
        e.validate_finding_updates(tmp_path, state, {"finding_updates": [update]})
    assert e.outcome_summary(state)["unresolved_findings"][0]["id"] == "SEC-2"


def test_unknown_legacy_finding_metadata_stays_visible(tmp_path):
    state = state_for(tmp_path, "product")
    state["visits"][0].update(packet={"output": {"finding_references": [{"id": "OLD"}]}})
    row = e.finding_register(state)[0]
    assert row["owner"] == "unknown" and row["metadata"] == "legacy_untyped"
    assert row["verification"] == "unverified" and row["disposition"] == "open"


def test_owner_change_retains_history_and_original_evidence_obligations(tmp_path):
    state = original_finding(tmp_path)
    state["index"] = 3
    update = {"origin_run": state["run"], "id": "SEC-2", "disposition": "deferred",
              "owner": "security-operations", "reason": "Ownership handed to the deployment operator"}
    assert e.validate_finding_updates(tmp_path, state, {"finding_updates": [update]}) == []
    stage = state["visits"][3]
    stage.update(decision="approved", packet={"phase": "build", "visit": stage["id"],
                                             "output": {"finding_updates": [update]}})
    row = e.finding_register(state)[0]
    assert row["owner"] == "security-operations"
    assert row["ownership_history"][0]["from"] == "infrastructure"
    bad = {**update, "required_evidence": ["actual grants"]}
    with pytest.raises(w.Refusal, match="cannot be removed"):
        e.validate_finding_updates(tmp_path, state, {"finding_updates": [bad]})


def accept_finding_update(state, update, *, index=3):
    """Keep the same decision digest/revision used by actual invalidation replay."""
    state["index"] = index
    packet = {"phase": state["visits"][index]["phase"], "visit": state["visits"][index]["id"],
              "output": {"finding_updates": [update]}}
    state = w.submit(state, packet)
    state["visits"][index]["decision"] = "approved"
    state["decisions"][str(state["revision"])] = {"choice": "approved", "binding": {
        "manifest_digest": e.content_fingerprint(packet), "revision": state["revision"]}}
    return state


def test_resubmission_replays_owner_and_added_obligations_after_product_declaration(tmp_path):
    state = original_finding(tmp_path)
    declaration = state["visits"][0]["packet"]["output"]["finding_references"][0]
    declaration["required_evidence"] = ["actual grants"]
    update = {"origin_run": state["run"], "id": "SEC-2", "owner": "security-operations",
              "disposition": "deferred", "required_evidence": ["actual grants", "write denials"],
              "reason": "Operator accepts the denied-write verification obligation"}
    state = accept_finding_update(state, update)
    state = w.invalidate(state, state["visits"][3]["id"], "source changed")
    state = w.submit(state, {"phase": "build", "visit": state["visits"][3]["id"], "output": {}})
    row = e.finding_register(state)[0]
    assert row["owner"] == "security-operations"
    assert row["required_evidence"] == ["actual grants", "write denials"]
    assert row["disposition"] == "deferred"
    assert row["ownership_history"] == [{"from": "infrastructure", "to": "security-operations",
        "visit": state["visits"][3]["id"], "reason": update["reason"]}]
    successor = state_for(tmp_path)
    successor.update(run="replacement", inherited_findings=e.carry_findings(state))
    assert e.finding_register(successor)[0] == row


def test_accepted_update_order_deduplicates_packets_and_revisits(tmp_path):
    state = original_finding(tmp_path)
    update = {"origin_run": state["run"], "id": "SEC-2", "disposition": "deferred",
              "owner": "security-operations", "reason": "First handoff"}
    state = accept_finding_update(state, update)
    state = accept_finding_update(state, {**update, "owner": "operations-review", "reason": "Second handoff"}, index=4)
    state = w.invalidate(state, state["visits"][3]["id"], "repeat Build after Evaluate")
    state = accept_finding_update(state, {**update, "owner": "security-operations", "reason": "Final handoff"})
    # Duplicate retained current packet, as well as a legacy approved snapshot.
    state["history"].append(deepcopy(state["visits"][3]))
    state["visits"][4]["superseded"] = True
    row = e.finding_register(state)[0]
    assert row["owner"] == "security-operations"
    assert [(entry["from"], entry["to"]) for entry in row["ownership_history"]] == [
        ("infrastructure", "security-operations"), ("security-operations", "operations-review"),
        ("operations-review", "security-operations")]
    assert len(row["update_history"]) == 3
    assert e.finding_register(state)[0] == row


def test_stale_accepted_resolution_reopens_but_retains_handoff_and_evidence(tmp_path):
    state = original_finding(tmp_path)
    update = {"origin_run": state["run"], "id": "SEC-2", "disposition": "resolved",
              "verification": "runtime_verified", "owner": "security-operations", "evidence": "risk.md",
              "required_evidence": ["actual grants", "write denials", "list/export"], "reason": "Historical claim"}
    state = accept_finding_update(state, update)
    state = w.invalidate(state, state["visits"][3]["id"], "changed inputs")
    state = w.submit(state, {"phase": "build", "visit": state["visits"][3]["id"], "output": {}})
    row = e.finding_register(state)[0]
    assert row["disposition"] == "open" and row["verification"] == "unverified"
    assert row["owner"] == "security-operations" and "list/export" in row["required_evidence"]
    assert row["update_history"][0]["update"]["disposition"] == "resolved"
    carried = e.carry_findings(state)["findings"][0]
    assert carried == row


def runtime_finding(tmp_path, *, kind="integration", environment="local"):
    state, plan, check = contract(tmp_path, kind=kind, environment=environment)
    obligation = {"obligation": "execute application assertion", "check_id": check["id"],
                  "kind": kind, "environment": environment, "criteria": ["AC1"]}
    declaration = {"id": "ASSERTION", "owner": "test-owner", "criterion": "AC1",
                   "required_evidence": [obligation]}
    state["visits"][0].update(decision="approved", packet={"phase": "product",
        "visit": state["visits"][0]["id"], "output": {"finding_references": [declaration]}})
    update = {"origin_run": state["run"], "id": "ASSERTION", "disposition": "resolved",
              "verification": "runtime_verified", "reason": "Current execution", "evidence": "check-1.log"}
    return state, plan, check, declaration, update


@pytest.mark.parametrize("kind", ["static", "independent"])
def test_static_or_generic_independent_pass_cannot_resolve_runtime_finding(tmp_path, kind):
    state, _, check, _, update = runtime_finding(tmp_path, kind=kind)
    (tmp_path / "check-1.log").write_text("Review prose says the runtime obligation passed")
    # Even a typed pass with immutable-reference-shaped metadata is insufficient:
    # the kind does not execute the finding's runtime behavior.
    verification = {"status": "validated", "effective_checks": [{**check, "check_id": check["id"],
        "status": "pass", "id": "attempt", "record_ref": {"sha256": "record"},
        "log_ref": {"sha256": "log"}, "evidence": "check-1.log"}]}
    with pytest.raises(w.Refusal, match="runtime checks matching"):
        e.validate_finding_updates(tmp_path, state, {"finding_updates": [update]}, verification=verification)


@pytest.mark.parametrize("defect", ["missing", "unvalidated", "stale", "fail", "unknown", "fixture", "local", "criterion", "obligation", "prose"])
def test_runtime_resolution_rejects_missing_stale_failed_or_incompatible_proof(tmp_path, monkeypatch, defect):
    state, _, check, declaration, update = runtime_finding(tmp_path)
    (tmp_path / "app.py").write_text("value = 2\n")
    if defect == "fail":
        (tmp_path / "app.py").write_text("value = 1\n")
    if defect == "unknown":
        monkeypatch.setattr(reuse.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("unavailable")))
    if defect == "fixture":
        check["environment"] = "fixture"
    out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    if defect == "stale":
        (tmp_path / "app.py").write_text("value = 3\n")
    if defect in {"fixture", "local"}:
        declaration["required_evidence"][0]["environment"] = "deployed"
    if defect == "criterion":
        declaration["criterion"] = "ORIGINAL-CRITERION"
        declaration["required_evidence"][0]["criteria"] = ["ORIGINAL-CRITERION"]
    if defect == "obligation":
        declaration["required_evidence"].append({**declaration["required_evidence"][0],
            "obligation": "denied configuration write", "check_id": "write-denials"})
    if defect == "prose":
        (tmp_path / "risk.md").write_text("The runtime passed")
        update["evidence"] = "risk.md"
    validation = e.validate_verification(tmp_path, state, out)
    if defect == "missing":
        validation["effective_checks"] = []
    if defect == "unvalidated":
        validation["status"] = "legacy_untyped"
    with pytest.raises(w.Refusal, match="runtime checks matching"):
        e.validate_finding_updates(tmp_path, state, {**out, "finding_updates": [update]}, verification=validation)


def test_legacy_obligation_requires_accepted_typed_mapping_before_closure(tmp_path, monkeypatch):
    state, _, check, declaration, update = runtime_finding(tmp_path)
    typed = deepcopy(declaration["required_evidence"][0])
    declaration["required_evidence"] = [typed["obligation"]]
    (tmp_path / "app.py").write_text("value = 2\n")
    out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    validation = e.validate_verification(tmp_path, state, out)
    for proposed in (update, {**update, "evidence_requirements": [typed]}):
        with pytest.raises(w.Refusal, match="runtime checks matching"):
            e.validate_finding_updates(tmp_path, state, {**out, "finding_updates": [proposed]}, verification=validation)
    mapping = {"origin_run": state["run"], "id": "ASSERTION", "evidence_requirements": [typed],
               "reason": "Accept exact local assertion coverage", "disposition": "open"}
    state = accept_finding_update(state, mapping, index=1)
    state["index"] = 3
    assert e.validate_finding_updates(tmp_path, state, {**out, "finding_updates": [update]}, verification=validation) == ["check-1.log"]
    with pytest.raises(w.Refusal, match="cannot be removed"):
        e.validate_finding_updates(tmp_path, state, {"finding_updates": [{**mapping, "evidence_requirements": []}]})


@pytest.mark.parametrize("addition", ["prose", "typed", "mapping"])
def test_resolution_cannot_add_unaccepted_obligations_or_mappings(tmp_path, monkeypatch, addition):
    state, _, check, declaration, update = runtime_finding(tmp_path)
    (tmp_path / "app.py").write_text("value = 2\n")
    out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    validation = e.validate_verification(tmp_path, state, out)
    typed = deepcopy(declaration["required_evidence"][0])
    if addition == "mapping":
        update["evidence_requirements"] = [typed]
    else:
        update["required_evidence"] = [typed, "write-denials" if addition == "prose" else {
            **typed, "obligation": "write-denials", "check_id": "write-denials"}]
    with pytest.raises(w.Refusal, match="accept additions first"):
        e.validate_finding_updates(tmp_path, state, {**out, "finding_updates": [update]}, verification=validation)


def test_matching_deployed_requirement_accepts_typed_deployed_evidence(tmp_path, monkeypatch):
    # This is a local contract fixture, not evidence of an actual deployment.
    state, _, check, _, update = runtime_finding(tmp_path, environment="deployed")
    (tmp_path / "app.py").write_text("value = 2\n")
    out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log", coverage_details={
        "target": "test-fixture-service", "service_result": "test-fixture-success"})
    validation = e.validate_verification(tmp_path, state, out)
    assert e.validate_finding_updates(tmp_path, state, {**out, "finding_updates": [update]}, verification=validation) == ["check-1.log"]


def test_typed_obligation_requires_every_accepted_additional_mapping(tmp_path, monkeypatch):
    state, _, check, declaration, update = runtime_finding(tmp_path)
    original = deepcopy(declaration["required_evidence"][0])
    deployed = {**original, "check_id": "deployed-assertion", "environment": "deployed"}
    mapping = {"origin_run": state["run"], "id": "ASSERTION", "disposition": "open",
               "evidence_requirements": [deployed], "reason": "Also require deployed execution"}
    assert e.validate_finding_updates(tmp_path, state, {"finding_updates": [mapping]}) == []
    state = accept_finding_update(state, mapping, index=1)
    state["index"] = 3
    (tmp_path / "app.py").write_text("value = 2\n")
    local_out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    local_validation = e.validate_verification(tmp_path, state, local_out)
    # The accepted additional obligation has no declared execution yet. The
    # original local proof cannot close it merely because that is the only check.
    with pytest.raises(w.Refusal, match="runtime checks matching"):
        e.validate_finding_updates(tmp_path, state, {**local_out, "finding_updates": [update]},
                                   verification=local_validation)
    with pytest.raises(w.Refusal, match="cannot be removed"):
        e.validate_finding_updates(tmp_path, state, {"finding_updates": [
            {**mapping, "required_evidence": [deployed]}]})

    # Supply the additional planned check and actual immutable attempt through
    # the same local contract fixture; this does not certify a live deployment.
    deployed_check = {**check, "id": deployed["check_id"], "name": "Deployed assertion",
                      "environment": "deployed", "evidence_outputs": ["check-2.log"]}
    state["visits"][2]["packet"]["output"]["verification_strategy"]["checks"].append(deployed_check)
    deployed_out, _, _ = attempt(tmp_path, monkeypatch, state, deployed_check, "check-2.log",
        coverage_details={"target": "test-fixture-service", "service_result": "test-fixture-success"})
    out = {**local_out, "build_checks": local_out["build_checks"] + deployed_out["build_checks"]}
    validation = e.validate_verification(tmp_path, state, out)
    for evidence in ("check-1.log", "check-2.log"):
        with pytest.raises(w.Refusal, match="runtime checks matching"):
            e.validate_finding_updates(tmp_path, state, {**out, "finding_updates": [
                {**update, "evidence": evidence}]}, verification=validation)
    out["finding_updates"] = [{**update, "evidence": ["check-1.log", "check-2.log"]}]
    assert e.validate_finding_updates(tmp_path, state, out, verification=validation) == ["check-1.log", "check-2.log"]
    capture(state, out, validation)
    row = e.finding_register(state)[0]
    assert row["disposition"] == "resolved"
    assert row["required_evidence"] == [original] and row["evidence_requirements"] == [deployed]


@pytest.mark.parametrize("defect", ["orphan", "not-an-object", "check", "kind", "environment", "criteria"])
def test_legacy_obligation_refuses_orphan_or_malformed_mapping_additions(tmp_path, defect):
    state, _, _, declaration, _ = runtime_finding(tmp_path)
    typed = deepcopy(declaration["required_evidence"][0])
    declaration["required_evidence"] = [typed["obligation"]]
    mapping = {"origin_run": state["run"], "id": "ASSERTION", "disposition": "open",
               "evidence_requirements": [typed], "reason": "Accept original local coverage"}
    assert e.validate_finding_updates(tmp_path, state, {"finding_updates": [mapping]}) == []
    state = accept_finding_update(state, mapping, index=1)
    state["index"] = 3
    bad = {"orphan": {**typed, "obligation": "nonexistent obligation"}, "not-an-object": None,
           "check": {**typed, "check_id": ""}, "kind": {**typed, "kind": "static"},
           "environment": {**typed, "environment": []}, "criteria": {**typed, "criteria": ["OTHER"]}}[defect]
    proposed = {**mapping, "evidence_requirements": [typed, bad]}
    with pytest.raises(w.Refusal, match="mapping.*obligation"):
        e.validate_finding_updates(tmp_path, state, {"finding_updates": [proposed]})


@pytest.mark.parametrize("coverage", [{"kind": "browser"}, {"environment": "deployed"}])
def test_mapping_cannot_assign_incompatible_coverage_to_the_same_check(tmp_path, coverage):
    state, _, _, declaration, _ = runtime_finding(tmp_path)
    mapping = {**declaration["required_evidence"][0], **coverage}
    update = {"origin_run": state["run"], "id": "ASSERTION", "disposition": "open",
              "evidence_requirements": [mapping]}
    with pytest.raises(w.Refusal, match="same check.*kind and environment"):
        e.validate_finding_updates(tmp_path, state, {"finding_updates": [update]})


@pytest.mark.parametrize("case", ["typed-deployed", "legacy-orphan", "typed-malformed"])
@pytest.mark.parametrize("projection", ["outcome", "handoff"])
def test_historical_unproven_mapping_stays_unresolved_in_projections(tmp_path, monkeypatch, case, projection):
    from taskplane import context_handoff
    state, _, check, declaration, update = runtime_finding(tmp_path)
    typed = deepcopy(declaration["required_evidence"][0])
    mappings = [{**typed, "check_id": "deployed-assertion", "environment": "deployed"}]
    if case == "legacy-orphan":
        declaration["required_evidence"] = [typed["obligation"]]
        mappings = [typed, {**typed, "obligation": "nonexistent obligation"}]
    elif case == "typed-malformed":
        mappings = [None]
    # Replay a historical acceptance without calling the new admission check.
    mapping = {"origin_run": state["run"], "id": "ASSERTION", "disposition": "open",
               "evidence_requirements": mappings, "reason": "Historically accepted mapping"}
    state = accept_finding_update(state, mapping, index=1)
    state["index"] = 3
    (tmp_path / "app.py").write_text("value = 2\n")
    out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    out["finding_updates"] = [update]
    validation = e.validate_verification(tmp_path, state, out)
    capture(state, out, validation)
    if projection == "outcome":
        rows = e.outcome_summary(state)["unresolved_findings"]
    else:
        for stage in state["visits"]:
            if stage.get("packet"):
                stage["packet"].update(checkpoint="fixture-checkpoint", manifest={})
        monkeypatch.setattr(context_handoff.depgraph, "load", lambda _: {})
        items, _, _, _ = context_handoff.inputs(tmp_path, state, None)
        rows = [item["body"] for item in items if item["kind"] == "finding" and item.get("required", True)]
    assert len(rows) == 1
    assert rows[0]["id"] == "ASSERTION" and rows[0]["disposition"] == "open"
    assert rows[0]["evidence_requirements"] == mappings
    assert rows[0]["verification"] == "unverified"
    with pytest.raises(w.Refusal, match="runtime checks matching"):
        e.validate_finding_updates(tmp_path, state, out, verification=validation)


def test_current_typed_runtime_closes_and_stale_or_replaced_evidence_reopens(tmp_path, monkeypatch):
    state, _, check, _, update = runtime_finding(tmp_path)
    (tmp_path / "app.py").write_text("value = 2\n")
    out, _, _ = attempt(tmp_path, monkeypatch, state, check, "check-1.log")
    out["finding_updates"] = [update]
    validation = e.validate_verification(tmp_path, state, out)
    assert e.validate_finding_updates(tmp_path, state, out, verification=validation) == ["check-1.log"]
    capture(state, out, validation)
    assert e.finding_register(state)[0]["disposition"] == "resolved"
    inherited = e.carry_findings(state)
    replacement = state_for(tmp_path)
    replacement.update(run="replacement", inherited_findings=inherited)
    row = e.finding_register(replacement)[0]
    assert row["disposition"] == "open" and row["verification"] == "unverified"
    assert row["update_history"][0]["update"]["disposition"] == "resolved"
    state["visits"][3]["decision"] = "stale"
    with pytest.raises(w.Refusal, match="runtime checks matching"):
        e.validate_finding_updates(tmp_path, state, out)
    assert e.finding_register(state)[0]["disposition"] == "open"


def test_prevalidation_matches_seal_without_uuid_store_or_state_mutation(tmp_path, monkeypatch):
    # Existing full fixture supplies a real strict graph and bounded receipt.
    from taskplane.tests.test_workflow_evidence import prepare
    from taskplane.context_handoff import Session, consume_required
    state, output, _ = prepare(tmp_path)
    state["context_contract"] = "bounded/v2"
    output["finding_references"] = []
    output["context_receipt"], _ = consume_required(Session(tmp_path, state))
    (tmp_path / "product.json").write_text(json.dumps(output))
    before_state = deepcopy(state)
    before_files = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    real_uuid = e.uuid.uuid4
    with monkeypatch.context() as patch:
        patch.setattr(e.uuid, "uuid4", lambda: pytest.fail("preview allocated a UUID"))
        patch.setattr(Store, "_object", lambda *a, **k: pytest.fail("preview wrote an object"))
        patch.setattr(Store, "register", lambda *a, **k: pytest.fail("preview registered context"))
        preview = e.prevalidate(tmp_path, state, "product.json", "tasks.json")
    assert state == before_state and "checkpoint" not in preview
    assert before_files == {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert real_uuid
    packet = e.seal(tmp_path, state, "product.json", "tasks.json")
    assert {key: value for key, value in packet.items() if key not in {"checkpoint", "finding_evidence"}} == preview
