"""Current phase runtime: isolated startup, collection and transitions."""
from __future__ import annotations

from taskplane import phase_records

import copy
import contextlib
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from taskplane import loop, taskplane_lite
from taskplane.tests.phase_fixture import phase_pending

SELECTOR = "test_app.py::test_greeting"

def _strategy():
    from taskplane import test_strategy
    from taskplane.tests.test_r0001_phase_agents_spec import _strategy as source
    value = source()
    value["acceptance_criteria"] = [{"id": "AC-T11", "selectors": [SELECTOR]}]
    value["producers"] = [{"id": "spec-package", "path": "app.py", "slice": "greeting",
        "consumers": ["test_app.py"], "severed_edges": [{"consumer": "test_app.py",
        "mutation": "remove greeting", "selector": "test_app.py::test_missing_greeting"}],
        "interface_kind": "in-process", "interface_fixtures": [],
        "freshness_inputs": ["candidate", "definition", "package"]}]
    value.pop("contract_fingerprint_sha256", None)
    return test_strategy.seal_strategy(value)
from taskplane.settings import load_settings
from tests.root_session_fixture import open_delivery_root


def _content_inventory(root: Path) -> dict[str, str]:
    """Bind a no-mutation assertion to names and semantic file content."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(
            path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


@pytest.mark.parametrize("claimed,prefix", [
    (False, ""), (True, ""), (True, "PYTHONDONTWRITEBYTECODE=1 "),
], ids=["ordinary-plain", "claimed-plain", "claimed-env-prefixed"])
def test_gate_suite_uses_checkout_with_approved_environment_prefix(tmp_path, claimed, prefix):
    workspace = tmp_path / "checkout"
    package = workspace / "taskplane"
    package.mkdir(parents=True)
    (package / "probe.py").write_text("VALUE = 'checkout'\n")
    # Like the actual checkout, taskplane has no __init__.py. An installed
    # regular package must not select the orchestrator's unrelated source.
    foreign = tmp_path / "installed"
    (foreign / "taskplane").mkdir(parents=True)
    (foreign / "taskplane" / "__init__.py").write_text("")
    (foreign / "taskplane" / "probe.py").write_text("VALUE = 'foreign'\n")
    (workspace / "test_checkout.py").write_text(
        "import os, unittest\nfrom taskplane.probe import VALUE\n"
        "class Check(unittest.TestCase):\n"
        "    def test_source(self): self.assertEqual(VALUE, 'checkout')\n" +
        ("    def test_environment(self): self.assertEqual(os.environ['PYTHONDONTWRITEBYTECODE'], '1')\n"
         if prefix else ""))
    env = dict(os.environ, PYTHONPATH=str(foreign))
    original_env = dict(env)
    cache_owner = loop.tp.suite_cache_lookup
    command = prefix + "python3 -m unittest discover -s . -p test_checkout.py"
    with loop._claimed_execute_suite_binding() if claimed else contextlib.nullcontext():
        assert loop.tp.suite_cache_lookup is cache_owner
        result = loop.tp.run_suite_command(str(workspace), command, env=env, timeout=15)
    assert result.returncode == 0, result.stderr
    assert env == original_env


def _workspace(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "README.md").write_text("stage loop\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
    subprocess.run([
        "git", "-c", "user.name=Taskplane", "-c",
        "user.email=taskplane@example.invalid", "commit", "-qm", "base",
    ], cwd=workspace, check=True)
    return str(workspace)


@pytest.mark.parametrize("damage", [None, "plan", "baseline", "scope", "history", "new-tasks"])
def test_unstarted_replan_preserves_only_exact_archived_build_scope(tmp_path, monkeypatch, damage):
    """Real commit ancestry/scope; delivery authorization is a simulated fixture."""
    from taskplane import stage_loop

    ws = Path(_workspace(tmp_path))
    baseline = loop.tp.git_head(str(ws))
    (ws / "README.md").write_text("completed build\n")
    subprocess.run(["git", "add", "README.md"], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.name=Taskplane", "-c",
        "user.email=taskplane@example.invalid", "commit", "-qm", "build"], cwd=ws, check=True)
    current = loop.tp.git_head(str(ws))
    (ws / "plan").mkdir()
    plan = ws / "plan/tasks.json"
    plan.write_text('{"tasks": []}\n')
    state = {"step": "plan", "tasks": None,
        "plan_fingerprint": hashlib.sha256(plan.read_bytes()).hexdigest(),
        "replan_history": [{"from_step": "evaluate", "baseline": baseline,
            "tasks": [{"id": "T-01", "scope": ["README.md"]}]}]}
    if damage == "plan":
        plan.write_text("changed plan\n")
    elif damage == "baseline":
        state["replan_history"][-1]["baseline"] = current
    elif damage == "scope":
        state["replan_history"][-1]["tasks"][0]["scope"] = ["other.py"]
    elif damage == "history":
        state["replan_history"] = []
    elif damage == "new-tasks":
        state["tasks"] = [{"id": "replacement", "scope": ["README.md"]}]
    monkeypatch.setattr(loop, "_validated_delivery_mode", lambda value: {"mode": "build"})
    assert stage_loop.authorized_run_revision(
        loop, str(ws), {"workflow": state}, baseline, current) is (damage is None)


@pytest.mark.parametrize("outcome,damage", [
    ("closed", None), ("discarded", None),
    ("discarded", "missing"), ("discarded", "authority"), ("closed", "outcome")])
def test_v2_dispatch_reuses_closed_inputs_only_with_exact_authorization(tmp_path, outcome, damage):
    """Actual runtime collection and serializer; host observations are simulated."""
    from taskplane import review_evidence, stage_handoff
    from taskplane.tests.test_r0001_agent_runtime import _setup
    from taskplane.tests.test_stage_entities import _authority, _stage

    runtime, dispatch, _ = _setup(tmp_path)
    result = runtime.run(dispatch)
    assert result["status"] == "accepted"
    store = review_evidence.ArtifactStore(str(tmp_path / "artifacts"))
    stage = _stage(run_id="run-1", authority=_authority(run_id="run-1"))
    stage["authority"]["authority_fingerprint"] = result["authority_fingerprint"]
    stage["authority"]["authority_revision"] = 1
    stage["predecessor_stage_ids"] = ["stage-recovery"]
    handoff = stage_handoff.create_v2_manifest(store, phase_result=result,
        produced_artifacts=[{"artifact_class":"stage", "artifact_schema_version":"taskplane.stage/v1",
            "reference": ref} for ref in result["collected_output_references"]],
        inherited_artifacts=[], producer_stage_id="stage-recovery", producer_outcome=outcome,
        requirement=stage["requirement"], design=stage["design"], target=None, commit=None,
        contracts={"provided":[], "consumed":[], "changed":[]}, deliverables=["retained-input"],
        evidence_references=result["collected_output_references"],
        exclusions=sorted(stage_handoff.REQUIRED_EXCLUSIONS),
        authorization={"actor":stage["authority"]["actor"], "session_id":stage["authority"]["session_id"],
            "authorized_at":"2026-09-11T13:00:00Z", "operation_id":"authorized-recovery",
            "authority_record":{"schema":"taskplane.authority-record-reference/v1",
                "authority_schema":"taskplane.consolidated-authorization/v1", "revision":1,
                "fingerprint":result["authority_fingerprint"]}}, allow_nonconsumable_reuse=True)
    if damage == "missing":
        handoff["authorization"]["nonconsumable_reuse"] = None
    elif damage == "authority":
        handoff["authorization"]["nonconsumable_reuse"]["authority_fingerprint"] = "0" * 64
    elif damage == "outcome":
        handoff["authorization"]["nonconsumable_reuse"]["producer_outcome"] = "discarded"
    handoff["fingerprint"] = stage_handoff.manifest_fingerprint(handoff)
    stage["input_manifest_ref"] = review_evidence.portable_artifact_reference(
        store, stage_handoff.store_v2_manifest(store, handoff)) if not damage else {}
    stage["selected_artifacts"] = handoff["selected_artifacts"]
    if damage:
        with pytest.raises(ValueError, match="v2 result binding"):
            stage_handoff._verified_handoff_for_dispatch(stage, handoff, handoff["selected_artifacts"])
    else:
        assert stage_handoff._verified_handoff_for_dispatch(
            stage, handoff, handoff["selected_artifacts"]) == handoff


def _build_collection_signing_fixture(tmp_path, *, advisory, original_admission=True):
    """Real runtime result, artifact custody and signer; simulated host/time inputs."""
    from types import SimpleNamespace
    from taskplane import design_host_transport, phase_harness, review_evidence
    from taskplane.tests.test_r0001_agent_runtime import _setup

    runtime, dispatch, calls = _setup(tmp_path)
    result = runtime.run(dispatch)
    assert result["status"] == "accepted", result
    assert calls == ["launch", "observe"]
    ws = str(tmp_path / "artifacts")
    artifacts = review_evidence.ArtifactStore(ws)
    impact = {"policy": {"depth": 1}, "nodes": ["app.py"]}
    original_freshness = {"candidate_sha": "a" * 40, "source_tree": "b" * 40,
        "impact_manifest_fingerprint": review_evidence.content_fingerprint(impact)}
    original = {"bindings": dict(dispatch.bindings), "signing_scope": ["app.py"],
        "freshness": original_freshness, "impact_reference": artifacts.put("phase-impact", impact)}
    original_ref = artifacts.put("phase-preparation", original)
    current_freshness = {**original_freshness, "candidate_sha": "c" * 40, "source_tree": "d" * 40}
    material = {**original, "freshness": current_freshness, "original_preparation": original_ref}
    manifest = {"revision": 2, "phase_records": {}}
    if advisory:
        decision = {"schema": "taskplane.resource-policy/v1", "run_id": "run-1",
            "mode": "advisory", "actor": "human:simulated", "authority_fingerprint": "f" * 64,
            "decided_at": 100}
        fingerprint = review_evidence.content_fingerprint(decision)
        manifest["phase_records"]["run-resource-limits"] = {
            "schema": "taskplane.phase-operation-receipt/v1", "operation_id": "run-resource-limits",
            "operation": "resource_policy", "request_fingerprint": fingerprint,
            "result": decision, "result_fingerprint": fingerprint, "committed_revision": 2}
    authority = None
    if original_admission:
        authority = design_host_transport.runtime_receipt_authority(loop.tp, ws,
            bindings=dispatch.bindings, freshness=original_freshness, now=100,
            admit=True, authorize=lambda: True)
    # The original admission uses preparation time. Advisory runs may defer
    # admission entirely until collection, after the duration limit.
    runtime.clock.advance(201)
    ports = SimpleNamespace(tp=loop.tp, phase_harness=phase_harness,
        design_host_transport=design_host_transport, SystemClock=lambda: runtime.clock,
        _phase_bridge_freshness=lambda workspace, scopes: (dict(current_freshness), impact),
        _stage_store=lambda workspace, run_id: SimpleNamespace(load=lambda requested: manifest),
        stage_loop=SimpleNamespace(authorized_run_revision=lambda *args: True))
    path = Path(loop.tp.tp_dir(ws)) / "runtime-receipt-authority.json"
    return SimpleNamespace(ws=ws, runtime=runtime, result=result, artifacts=artifacts,
        original=original, material=material, manifest=manifest, ports=ports, path=path,
        authority=authority, before=json.loads(path.read_text()) if path.exists() else None,
        before_bytes=path.read_bytes() if path.exists() else None)


@pytest.mark.parametrize("advisory,original_admission", [(False, True), (True, True), (True, False)],
    ids=["strict", "human-advisory", "human-advisory-before-preparation"])
def test_build_collection_signing_honors_explicit_advisory_policy(tmp_path, advisory, original_admission):
    from taskplane import design_host_transport, phase_harness, review_evidence

    fixture = _build_collection_signing_fixture(tmp_path, advisory=advisory,
        original_admission=original_admission)
    checked = []
    def authorize():
        checked.append("current-host-authority")
        return True
    if not advisory:
        with pytest.raises(design_host_transport.NativeEntryError, match="signing admission is stale"):
            phase_harness._phase_bridge_signing(fixture.ports, fixture.ws, fixture.material,
                admit=True, authorize=authorize)
        assert fixture.path.read_bytes() == fixture.before_bytes
        assert checked
        return
    signer = phase_harness._phase_bridge_signing(fixture.ports, fixture.ws, fixture.material,
        admit=True, authorize=authorize)
    signed = signer.sign(fixture.result, store=fixture.artifacts)
    assert signer.verify(signed, store=fixture.artifacts)["payload"] == fixture.result
    assert signer.expires_at == 301 + 86400
    assert signer.bindings == fixture.original["bindings"]
    assert checked
    after = json.loads(fixture.path.read_text())
    original_operation = fixture.original["bindings"]["operation_id"]
    if original_admission:
        assert after["admissions"][original_operation] == fixture.before["admissions"][original_operation]
        assert after["keys"][fixture.authority.key_id] == fixture.before["keys"][fixture.authority.key_id]
    policy = phase_records.resource_policy(fixture.manifest, "run-1")
    collection = review_evidence.content_fingerprint({"kind": "approved-build-output",
        "preparation": fixture.material["original_preparation"], "freshness": fixture.material["freshness"],
        "scope": fixture.material["signing_scope"], "resource_policy": policy["fingerprint"]})
    expected_operations = {original_operation + "-collection-" + collection}
    if original_admission:
        expected_operations.add(original_operation)
    assert set(after["admissions"]) == expected_operations
    saved = fixture.path.read_bytes()
    fixture.runtime.clock.advance(7)
    retry = phase_harness._phase_bridge_signing(fixture.ports, fixture.ws, fixture.material,
        admit=True, authorize=authorize)
    assert retry.key_id == signer.key_id
    assert retry.expires_at == signer.expires_at
    retry.verify(signed, store=fixture.artifacts)
    assert fixture.path.read_bytes() == saved
    fixture.runtime.clock.advance(86400)
    expired = phase_harness._phase_bridge_signing(fixture.ports, fixture.ws, fixture.material,
        admit=True, authorize=authorize)
    with pytest.raises(design_host_transport.NativeEntryError, match="key is disabled, not yet valid or stale"):
        expired.sign(fixture.result, store=fixture.artifacts)
    assert fixture.path.read_bytes() == saved  # A retry cannot renew its signing window.


@pytest.mark.parametrize("case", ["original-bindings", "original-freshness", "current-source",
    "current-authority", "revoked", "invalid-policy"])
def test_build_collection_signing_rejects_changed_authority(tmp_path, case):
    from taskplane import design_host_transport, phase_harness, review_evidence

    fixture = _build_collection_signing_fixture(tmp_path, advisory=True)
    if case in {"original-bindings", "original-freshness"}:
        original = copy.deepcopy(fixture.original)
        if case == "original-bindings":
            original["bindings"]["authority_fingerprint"] = "e" * 64
        else:
            original["freshness"]["candidate_sha"] = "e" * 40
        fixture.material = {**original, "freshness": fixture.material["freshness"],
            "original_preparation": fixture.artifacts.put("phase-preparation", original)}
        message = "original runtime signing authority changed"
    elif case == "current-source":
        fixture.material["freshness"] = {**fixture.material["freshness"], "candidate_sha": "e" * 40}
        message = "current validation source changed"
    elif case == "current-authority":
        message = "host authority refused admission"
    elif case == "revoked":
        design_host_transport.disable_runtime_receipt_authority(loop.tp, fixture.ws,
            bindings=fixture.original["bindings"], freshness=fixture.original["freshness"],
            now=101, authorize=lambda: True, status="revoked", changed_at=101)
        message = "original runtime signing authority changed or is disabled"
    else:
        row = fixture.manifest["phase_records"]["run-resource-limits"]
        row["result"]["actor"] = "worker:foreign"
        row["request_fingerprint"] = row["result_fingerprint"] = review_evidence.content_fingerprint(row["result"])
        message = "run resource policy does not verify"
    before = fixture.path.read_bytes()
    with pytest.raises(ValueError, match=message):
        phase_harness._phase_bridge_signing(fixture.ports, fixture.ws, fixture.material,
            admit=True, authorize=lambda: case != "current-authority")
    assert fixture.path.read_bytes() == before


def test_stage_native_first_worker_identity_is_run_scoped_and_replay_stable(tmp_path, monkeypatch):
    """Real init/next producers; simulated host metadata, no native J1 claim."""
    from taskplane import requirements, review_evidence, run_store, storage
    from taskplane.tests.test_r0001_phase_agents_spec import _registry

    monkeypatch.setenv("TASKPLANE_SESSION_ID", "same-host-session")
    names = []
    for ordinal in (1, 2):
        root = tmp_path / str(ordinal)
        root.mkdir()
        ws = _workspace(root)
        source = (Path(ws) / "README.md").read_bytes()
        store = run_store.RunStore()
        identity = storage.resolve_repository_identity(ws)
        initial = store.create(identity, run_id=f"run-product-{ordinal}", checkout=ws,
            host={"kind":"codex", "session_id":"same-host-session"},
            target={"kind":"workspace", "revision":loop.tp.git_head(ws)})
        run_id = initial["run_id"]
        storage.write_workspace_locator(ws, identity=identity,
            layout=storage.resolve_layout(identity, home=store.home, run_id=run_id), run_id=run_id)
        requirement = requirements.record_requirement(ws, "Same Product goal",
            functional=["preserve the native attempt identity"],
            acceptance=["two runs cannot collide in one retained host task tree"])
        unrelated = {"design/contract.json": "UNRELATED_DESIGN_SENTINEL",
                     "knowledge/context.md": "STALE_CONTEXT_SENTINEL"}
        for relative, content in unrelated.items():
            path = Path(ws) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        initialized = loop.init(ws, "Same Product goal", requirement_id=requirement["id"], by="human:simulated")
        assert not initialized.get("error"), initialized
        route = phase_records.phase_routing(store.load(run_id))
        assert route["result"]["owner"] == "agent-runtime"
        assert store.load(run_id)["schema"] == "taskplane.run/v4"
        # Projection bytes are not an execution input, even when corrupt.
        projection = Path(loop._loop_path(ws))
        projection.parent.mkdir(parents=True, exist_ok=True)
        projection.write_text("{corrupt dashboard projection")
        dispatched = loop.next_action(ws)
        assert set(dispatched) == {"schema", "stage_runtime_dispatch", "obligations"}, dispatched
        envelope = dispatched["stage_runtime_dispatch"]
        loop.tp.stage_startup_bytes(envelope)
        from taskplane import phase_harness
        inputs = phase_harness.read_input(loop, ws, envelope)
        assert inputs["requirement"]["id"] == requirement["id"]
        assert "design" not in inputs and "knowledge" not in inputs
        for relative, content in unrelated.items():
            assert content not in json.dumps(inputs)
            assert (Path(ws) / relative).read_text() == content
        first = dispatched["obligations"]
        names.append(first["task_name"])
        state = loop.load(ws)
        manifest = store.load(run_id)
        slot = Path(loop.tp.active_contract_path(ws, first["contract_bootstrap"]["task_slot"]))
        contract_bytes = slot.read_bytes()
        repeated = loop.next_action(ws)
        assert repeated["obligations"]["phase_operation"] == first["phase_operation"]
        assert "task_name" not in repeated  # Pickup is observation, not another launch.
        assert loop.load(ws)["worker_dispatch_sequences"] == state["worker_dispatch_sequences"] == {"pm:pm":1}
        assert slot.read_bytes() == contract_bytes
        assert json.loads(contract_bytes)["worker_lifecycle"]["expected_task_name"] == first["task_name"]
        assert store.load(run_id) == manifest
        assert (Path(ws) / "README.md").read_bytes() == source
    assert names[0] != names[1], "fresh runs reused the same host-global first Product name"


@pytest.fixture
def parallel_phase_mode(request):
    return getattr(request, "param", False)


@pytest.fixture
def collected_product_handoff(tmp_path, monkeypatch, parallel_phase_mode, git_ws):
    """Actual producers/collector on a small Git target; host events simulated."""
    from taskplane import review_evidence
    from taskplane.tests import test_stage_cross_host as cross_host
    from taskplane.tests.phase_fixture import _supporting_pristine_phase_run
    from taskplane.tests.phase_fixture import _emit_host_hook, _host_event
    git = cross_host._git
    if parallel_phase_mode:
        import sys
        from taskplane import test_strategy
        original_strategy = _strategy
        def parallel_strategy():
            value = original_strategy()
            producer = copy.deepcopy(value["producers"][0])
            producer.update(id="helper-package", path="helper/__init__.py")
            producer["consumers"] = ["helper/test_helper.py"]
            producer["severed_edges"][0]["consumer"] = "helper/test_helper.py"
            producer["severed_edges"][0]["selector"] = "helper/test_helper.py::test_missing_greeting"
            value["producers"].append(producer)
            value["acceptance_criteria"][0]["selectors"].append("helper/test_helper.py::test_greeting")
            value.pop("contract_fingerprint_sha256", None)
            return test_strategy.seal_strategy(value)
        monkeypatch.setattr(sys.modules[__name__], "_strategy", parallel_strategy)
    def with_python_input(workspace, *args):
        if args == ("add", "."):
            ignored = workspace / ".gitignore"
            ignored.write_text((ignored.read_text() if ignored.exists() else "") +
                "\n__pycache__/\n.pytest_cache/\n.mypy_cache/\n")
            (workspace / "app.py").write_text('def greet(name: str) -> str:\n    return f"Hello {name}"\n')
            (workspace / "test_app.py").write_text(
                "import app\nimport pytest\ndef test_greeting():\n    assert app.greet('World') == 'Hello World'\n"
                "def test_missing_greeting(monkeypatch):\n    monkeypatch.delattr(app, 'greet')\n    with pytest.raises(AttributeError):\n        app.greet('World')\n")
            if parallel_phase_mode:
                (workspace / "helper").mkdir(exist_ok=True)
                (workspace / "helper/__init__.py").write_text((workspace / "app.py").read_text())
                (workspace / "helper/test_helper.py").write_text(
                    (workspace / "test_app.py").read_text().replace("app", "helper"))
        return git(workspace, *args)
    monkeypatch.setattr(cross_host, "_git", with_python_input)
    record = cross_host._record_bootstrap_requirement
    def with_context(workspace):
        requirement = record(workspace)
        requirement = loop.reqs.amend_requirement(str(workspace), requirement["id"],
            context_files=["app.py", "helper/__init__.py"] if parallel_phase_mode else ["app.py"], nfr={"security":"retain exact authority",
                "architecture":"reuse existing owners", "reliability":"preserve accepted evidence"})
        loop.depgraph.link_requirement(str(workspace), requirement["id"], ["app.py"], kind="planned", replace=True)
        return requirement
    monkeypatch.setattr(cross_host, "_record_bootstrap_requirement", with_context)
    ws, store, run_id, requirement = _supporting_pristine_phase_run(
        tmp_path, monkeypatch, parallel=parallel_phase_mode, git_factory=git_ws)
    requested = loop.next_action(ws)
    assert not requested.get("error"), requested
    assert _emit_host_hook(ws, requested, "SubagentStart", monkeypatch) == 0
    path = Path(ws) / "specs/requirement.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema":"taskplane.requirement/v1", "id":requirement["id"],
        "title":requirement["title"], "acceptance_criteria":requirement["acceptance"]}))
    slot = requested["obligations"]["contract_bootstrap"]["task_slot"]
    assert _emit_host_hook(ws, requested, "SubagentStop", monkeypatch) == 0
    assert loop.tp.load_json(loop.tp.active_contract_path(ws, slot), default=None) is None
    assert loop.tp.released_worker_contract(ws, slot)["worker_lifecycle"]["owner"]
    completion = phase_pending(ws)["completion"]
    assert completion is not None
    artifacts = review_evidence.ArtifactStore(ws)
    material = artifacts.read(completion["preparation"])
    return ws, store, run_id, completion, material, artifacts


def test_collected_product_gate_preserves_signed_handoff_after_graph_refresh(collected_product_handoff, monkeypatch):
    import types
    ws, store, run_id, completion, material, artifacts = collected_product_handoff
    original = {key:artifacts.read(completion[key]) for key in ("preparation", "runtime_result", "runtime_receipt")}
    impact = artifacts.read(material["impact_reference"])
    # A later graph update cannot rewrite the phase's consumed snapshot.
    modules = loop.depgraph.scope_modules(ws, ["app.py"])
    loop.depgraph.record_edge(ws, modules[0], "contract:greeting", kind="provides", confidence="high")
    monkeypatch.setattr(loop.depgraph, "time", types.SimpleNamespace(time=lambda: impact["graph"]["updated_at"] + 10))
    gated = loop.gate(ws, "pass")
    assert not gated.get("error"), gated
    assert gated["step"] == "design"
    assert {key:artifacts.read(completion[key]) for key in original} == original
    retained = phase_records.phase_records(store.load(run_id))
    assert any(row["operation"] == "phase_collect" and row["result"] == completion for row in retained.values())


def test_collected_product_uses_selected_successor_before_legacy_design_policy(collected_product_handoff, monkeypatch):
    ws, _, _, completion, _, artifacts = collected_product_handoff
    assert loop.load(ws)["design_required"] is True
    import views
    with monkeypatch.context() as broken:
        broken.setattr(views, "refresh_views", lambda *_args: (_ for _ in ()).throw(OSError("renderer unavailable")))
        gated = loop.gate(ws, "pass")
        assert gated["dashboard_refresh"]["status"] == "blocked"
        refused = loop.next_action(ws)
        assert "publication replay is required" in refused["error"]
        assert loop.load(ws)["step"] == "design"
    assert not gated.get("error"), gated
    assert gated["step"] == "design"
    assert loop.load(ws)["design_required"] is True
    render = views.refresh_views
    with monkeypatch.context() as broken:
        def fail_new_dispatch(workspace, value):
            if value["dashboard_snapshot"]["snapshot"]["values"]["event_type"] == "next_action":
                raise OSError("dispatch presentation unavailable")
            return render(workspace, value)
        broken.setattr(views, "refresh_views", fail_new_dispatch)
        withheld = loop.next_action(ws)
        assert withheld["obligations"]["dispatch_allowed"] is False
        assert withheld["obligations"]["dashboard_refresh"]["replay_required"] is True
    sequence = copy.deepcopy(loop.load(ws)["worker_dispatch_sequences"])
    requested = _cli(ws, "next")
    assert requested["obligations"]["dispatch_allowed"] is True
    assert requested["stage_runtime_dispatch"] == withheld["stage_runtime_dispatch"]
    assert loop.load(ws)["worker_dispatch_sequences"] == sequence
    assert not requested.get("error"), requested
    assert requested["obligations"]["role"] == "tp-design"
    assert requested["obligations"]["dashboard_replay"]["status"] == "replayed"
    assert phase_pending(ws)["status"] == "pending"
    material = artifacts.read(phase_pending(ws)["reference"])
    inputs = loop.phase_harness.read_input(loop, ws, requested["stage_runtime_dispatch"])
    instructions = Path(loop.__file__).resolve().parents[1] / inputs["phase_definition"]["skill_ref"]
    assert instructions.is_file(), instructions
    assert instructions == Path(loop.__file__).resolve().parents[1] / "skills/tp-design/SKILL.md"
    assert hashlib.sha256(instructions.read_bytes()).hexdigest() == material["bindings"]["skill_content_fingerprint"]
    assert requested["obligations"]["role_marker"] == "taskplane-role:tp-design"
    assert material["envelope"]["task_name"] == requested["obligations"]["task_name"]
    assert artifacts.read(material["predecessor"]) == artifacts.read(completion["handoff"])
    assert [row["artifact_class"] for row in material["package"]] == ["requirement", "lens-evidence"]
    refused = loop.gate(ws, "pass")
    assert "matching terminal and collected output" in refused["error"]
    assert loop.load(ws)["step"] == "design"


@pytest.mark.parametrize("phase", ["product", "design", "plan", "build", "evaluate", "engineering", "retro"])
def test_selected_phase_instruction_sources_match_admitted_skill_bytes(phase):
    from taskplane.tests.test_r0001_phase_agents_spec import _registry
    registry, _ = loop._phase_bridge_registry({
        "definition_source": "agents/spec-phase-definitions.json",
        "definition_set_fingerprint": _registry().definition_set_fingerprint})
    definition = registry.admit(phase, ()).to_dict()
    instructions = Path(loop.__file__).resolve().parents[1] / definition["skill_ref"]
    assert instructions.is_file()
    assert hashlib.sha256(instructions.read_bytes()).hexdigest() == definition["skill_content_fingerprint"]


@pytest.fixture
def readable_design_input(collected_product_handoff, monkeypatch):
    from taskplane.tests.phase_fixture import _emit_host_hook
    ws, store, run_id, _, _, artifacts = collected_product_handoff
    assert loop.gate(ws, "pass")["step"] == "design"
    action = loop.next_action(ws)
    obligations = action["obligations"]
    expected = loop.tp.peek_expectation(ws, obligations["task_name"])
    assert expected["agent"] == obligations["role"] == "tp-design"
    assert loop.tp.native_worker_role_matches(ws, expected, obligations["task_name"])
    assert _emit_host_hook(ws, action, "SubagentStart", monkeypatch) == 0
    monkeypatch.setenv("TASKPLANE_TASK", obligations["contract_bootstrap"]["task_slot"])
    inputs = loop.phase_harness.read_input(loop, ws, action["stage_runtime_dispatch"])
    return ws, store, run_id, artifacts, action, inputs


def test_worker_reads_selected_graph_and_complete_lens_evidence(readable_design_input):
    ws, store, run_id, artifacts, action, inputs = readable_design_input
    before = store.load(run_id)
    def read(*references):
        return loop.phase_harness.read_artifact(loop, ws, {
            "stage_runtime_dispatch": action["stage_runtime_dispatch"],
            "references": list(references)})
    baseline = read(inputs["graph_baseline"])
    assert baseline["content"] == artifacts.read(inputs["graph_baseline"])
    assert baseline["projection"] == "exact"
    lens_ref = next(row["reference"] for row in inputs["artifacts"]
                    if row["artifact_class"] == "lens-evidence")
    packet = read(lens_ref)["content"]
    for entry in packet["entries"]:
        collection = read(lens_ref, entry["collection"])
        assert collection["content"] == artifacts.read(entry["collection"])
        assert collection["content"]["results"]
        plan = read(lens_ref, entry["plan"])
        original = artifacts.read(entry["plan"])
        assert plan["projection"] == "lens-plan-evidence"
        assert plan["content"]["decision"] == original["decision"]
        assert len(plan["content"]["decision"]) == 26
        assert set(plan["content"]) == {"schema", "phase", "binding", "decision"}
        with pytest.raises(ValueError, match="not selected"):
            read(entry["collection"])
    assert store.load(run_id) == before


@pytest.mark.parametrize("damage", ["unselected", "digest", "path", "authority", "length"])
def test_worker_artifact_reader_refuses_foreign_or_altered_input(readable_design_input, damage):
    from taskplane import review_evidence
    ws, store, run_id, artifacts, action, inputs = readable_design_input
    request = {"stage_runtime_dispatch": copy.deepcopy(action["stage_runtime_dispatch"]),
               "references": [copy.deepcopy(inputs["graph_baseline"])]}
    if damage == "unselected":
        request["references"] = [review_evidence.portable_artifact_reference(
            artifacts, artifacts.put("graph-baseline", {"foreign": True}))]
    elif damage == "digest":
        request["references"][0]["digest"] = "0" * 64
    elif damage == "path":
        request["references"][0]["path"] = "/outside/graph.json"
    elif damage == "authority":
        request["stage_runtime_dispatch"]["startup"]["authority"]["authority_fingerprint"] = "0" * 64
    else:
        request["references"] *= 3
    before = store.load(run_id)
    with pytest.raises(ValueError):
        loop.phase_harness.read_artifact(loop, ws, request)
    assert store.load(run_id) == before


def test_worker_artifact_reader_cli_and_corrupt_bytes(readable_design_input, monkeypatch, capsys):
    import io
    from types import SimpleNamespace
    from taskplane import tp as cli
    ws, _, _, artifacts, action, inputs = readable_design_input
    ref = inputs["graph_baseline"]
    request = {"stage_runtime_dispatch": action["stage_runtime_dispatch"], "references": [ref]}
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(request)))
    args = SimpleNamespace(stage_action="read-artifact", workspace=ws, request="-")
    capsys.readouterr()
    assert cli.cmd_stage(args) == 0
    assert json.loads(capsys.readouterr().out)["content"] == artifacts.read(ref)
    Path(artifacts._validated_path(ref)).write_text("{}")
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(request)))
    assert cli.cmd_stage(args) == 1
    assert "digest mismatch" in capsys.readouterr().out


def test_documented_test_strategy_example_is_accepted():
    from taskplane import stage_artifacts
    path = Path(loop.__file__).resolve().parents[1] / "skills/tp-design/references/test-strategy.md"
    example = path.read_text().split("```json\n", 1)[1].split("\n```", 1)[0]
    strategy = json.loads(example)
    stage_artifacts.validate("test-strategy", strategy)
    # The acceptance-map owner has stricter selector rules than the strategy
    # shape validator. Exercise the documented example through both owners.
    design = {
        "schema": "taskplane.design/v1", "requirement": "R-0001",
        "acceptance_map": [{"criterion": row["id"], "tests": row["selectors"]}
                           for row in strategy["acceptance_criteria"]],
        "test_strategy": strategy,
    }
    assert loop.validate_spec_phase_artifact(design) == design


@pytest.mark.parametrize("damage", ["class-name", "strategy-fingerprint"])
def test_design_draft_validation_precedes_review_leases(readable_design_input, damage):
    ws, store, run_id, artifacts, action, inputs = readable_design_input
    strategy = _strategy()
    design = {
        "schema": "taskplane.design/v1", "requirement": inputs["requirement"]["id"],
        "acceptance_map": [{"criterion": row["id"], "tests": row["selectors"]}
                           for row in strategy["acceptance_criteria"]],
        "test_strategy": {"path": "design/test-strategy.json"},
        "test_strategy_reference": {
            "schema": "taskplane.design-test-strategy-reference/v1",
            "path": "design/test-strategy.json",
            "strategy_fingerprint": strategy["contract_fingerprint_sha256"],
        },
    }
    folder = Path(ws) / "design"
    folder.mkdir(exist_ok=True)
    candidate = copy.deepcopy(design)
    invalid_strategy = copy.deepcopy(strategy)
    if damage == "class-name":
        candidate["acceptance_map"][0]["tests"] = [
            "test_feature.py::FeatureTests::test_behavior"]
        message = "TestClass::test_method"
    else:
        invalid_strategy["contract_fingerprint_sha256"] = "0" * 64
        message = "fingerprint"
    (folder / "contract.json").write_text(json.dumps(candidate))
    (folder / "test-strategy.json").write_text(json.dumps(invalid_strategy))
    before = artifacts.references("lens-plan")
    contracts = list(Path(loop.tp.active_contract_path(ws, "placeholder")).parent.glob("*.json"))
    with pytest.raises(ValueError, match=message):
        loop.phase_harness.collect_lenses(loop, ws, action["stage_runtime_dispatch"], prepare=True)
    assert artifacts.references("lens-plan") == before
    assert list(Path(loop.tp.active_contract_path(ws, "placeholder")).parent.glob("*.json")) == contracts
    assert loop.load(ws)["step"] == "design"
    (folder / "contract.json").write_text(json.dumps(design))
    (folder / "test-strategy.json").write_text(json.dumps(strategy))
    prepared = loop.phase_harness.collect_lenses(loop, ws, action["stage_runtime_dispatch"], prepare=True)
    assert prepared["dispatch"]


@pytest.fixture
def collected_lens_design(collected_product_handoff, monkeypatch):
    """Real phase owners with authored test candidates and simulated host events."""
    from taskplane.tests.phase_fixture import _emit_host_hook
    ws, store, run_id, _, _, artifacts = collected_product_handoff
    assert loop.gate(ws, "pass")["step"] == "design"
    action = loop.next_action(ws)
    assert not action.get("error"), action
    assert _emit_host_hook(ws, action, "SubagentStart", monkeypatch) == 0
    state = loop.load(ws)
    inputs = loop.phase_harness.read_input(loop, ws, action["stage_runtime_dispatch"])
    baseline = artifacts.read(inputs["graph_baseline"])
    assert baseline["graph_fingerprint"] == state["design_graph_fingerprint"]
    requirement = loop.reqs.get_requirement(ws, state["requirement_id"])
    folder = Path(ws) / "design"
    source_modules = loop.depgraph.scope_modules(ws, ["app.py"])
    folder.mkdir(exist_ok=True)
    (folder / "design.md").write_text("# Greeting design\nKeep the existing greeting boundary.\n")
    contract = {
        "schema": "taskplane.design/v1", "requirement": requirement["id"],
        "title": "Greeting design", "summary": "Keep the greeting function", "decision": "Keep the existing module",
        "current_state": {"summary": "A single greeting function", "sources": ["app.py"]},
        "alternatives": [{"id": choice, "name": choice, "description": choice,
            "tradeoffs": {"gains": ["simple"], "costs": ["limited scope"], "revisit_when": "requirements grow"}}
            for choice in ("existing", "new-module")], "selected_approach": "existing",
        "modules": {"existing": source_modules, "new": []}, "contracts": [],
        "graph": {"baseline_fingerprint": baseline["graph_fingerprint"],
            "proposed_modules": source_modules, "proposed_edges": [],
            "depth_policy": {"local_depth": 1, "boundary_mode": "contract-only", "contract_depth": 1, "requirement_depth": 1},
            "dor": [{"check": "source exists", "evidence": "app.py"}],
            "dod": [{"check": "greeting preserved", "evidence": "acceptance test"}]},
        "acceptance_map": [{"criterion": criterion, "design_element": "app.greet", "validation": "regression",
            "tests": _strategy()["acceptance_criteria"][0]["selectors"]}
            for criterion in requirement["acceptance"]],
        "risks": [{"risk": "regression", "mitigation": "test", "owner": "engineering"}],
        "failure_modes": [{"mode": "wrong greeting", "detection": "test", "recovery": "correct source"}],
        "observability": {"signals": ["test result"], "alerts_none_rationale": "local pure function"},
        "rollout": {"strategy": "reviewed change", "rollback": "revert change"},
        "visualization": {"required": False, "reason": "single function"},
        "test_strategy": {"path": "design/test-strategy.json"}, "open_questions": [],
        "lens_evidence": [], "test_strategy_reference":{
            "schema":"taskplane.design-test-strategy-reference/v1", "path":"design/test-strategy.json",
            "strategy_fingerprint":_strategy()["contract_fingerprint_sha256"]}}
    contract["lens_evidence"] = [{"lens": "solution-design", "verdict": "pass", "blockers": 0,
        "evidence": "Explicit test-candidate self-assessment; no independent lens execution",
        "produced_by": action["obligations"]["task_name"], "self_attested": True,
        "content_fingerprint": loop._dc.design_content_fingerprint(ws, contract)}]
    (folder / "contract.json").write_text(json.dumps(contract))
    (folder / "test-strategy.json").write_text(json.dumps(_strategy()))
    assert loop._base_design_dod_errors(ws, state) == []
    assert loop._design_control_plane_errors(ws, state) == []
    assert loop.submit(ws, "pass").get("submitted") is True
    slot = action["obligations"]["contract_bootstrap"]["task_slot"]
    assert _emit_host_hook(ws, action, "SubagentStop", monkeypatch) == 0
    assert loop.tp.load_json(loop.tp.active_contract_path(ws, slot), default=None) is None
    assert loop.tp.released_worker_contract(ws, slot)["worker_lifecycle"]["owner"]
    completion = phase_pending(ws)["completion"]
    assert artifacts.read(completion["runtime_result"])["status"] == "accepted"
    assert "design_team_plan" not in loop.load(ws)
    return ws, store, run_id, artifacts, completion


def test_stateless_design_gate_uses_collected_runtime_not_legacy_team(collected_lens_design, monkeypatch):
    ws, _, _, artifacts, completion = collected_lens_design
    original = {key: artifacts.read(completion[key]) for key in
                ("preparation", "runtime_result", "runtime_receipt", "handoff")}
    stage = loop._phase_bridge_context(ws, loop.load(ws))["stage"]
    result = loop.gate(ws, "pass")
    assert not result.get("error"), result.get("dod", result)
    assert result["step"] == "design_approval"
    assert loop._phase_bridge_context(ws, loop.load(ws))["stage"] == stage
    approved = loop.approve(ws, by="human:fixture — approve greeting design")
    assert not approved.get("error"), approved.get("dod", approved)
    assert approved["step"] == "plan"
    assert {key: artifacts.read(completion[key]) for key in original} == original


@pytest.mark.parametrize("reason", ["missing collected output", "foreign receipt", "stale source"])
def test_design_phase_dod_propagates_existing_verifier_refusal(tmp_path, monkeypatch, reason):
    # Focused join test; real signature/provenance severances have their own
    # production-owner regression below this boundary.
    monkeypatch.setattr(loop, "_phase_bridge_context", lambda *_: {"stage": {"stage_kind": "design"}})
    def refuse(*_):
        raise ValueError(reason)
    monkeypatch.setattr(loop, "_phase_bridge_gate_check", refuse)
    assert f"Design phase evidence refused: {reason}" in loop._design_dod_errors(str(tmp_path), {"step": "design_approval"})


def test_design_phase_dod_rejects_different_current_phase(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "_phase_bridge_context", lambda *_: {"stage": {"stage_kind": "product"}})
    def cannot_accept_other_phase(*_):
        pytest.fail("foreign phase must refuse before consuming its receipt")
    monkeypatch.setattr(loop, "_phase_bridge_gate_check", cannot_accept_other_phase)
    assert "Design phase evidence refused: current phase is not Design" in loop._design_dod_errors(str(tmp_path), {"step": "design"})


def test_phase_plan_consumes_top_level_design_strategy_and_distinct_approval_domain(tmp_path, monkeypatch):
    """Authored candidates through existing producers; simulated host/approval."""
    from taskplane.tests import test_r0001_phase_agents_spec as spec
    original = spec._run
    def authored_design(*args, **kwargs):
        values = list(args)
        if values[3] == "design":
            authored = copy.deepcopy(values[4])
            design = authored["design"]
            design["test_strategy_reference"] = design["test_strategy"].pop("authority")
            source = tmp_path / "source"
            (source / "design").mkdir(exist_ok=True)
            (source / "design/contract.json").write_text(json.dumps(design))
            (source / "design/test-strategy.json").write_text(json.dumps(authored["test-strategy"]))
            kwargs["state"]["design_fingerprint"] = loop._dc.design_evidence_fingerprint(str(source), design)
            assert kwargs["state"]["design_fingerprint"] != spec.review_evidence.content_fingerprint(design)
            values[4] = authored
        return original(*values, **kwargs)
    monkeypatch.setattr(spec, "_run", authored_design)
    store, registry, state, _, plan, _ = spec._journey(tmp_path)
    package, _ = spec._consume(store, registry, state, plan)
    assert package.read("plan-task")["task"]["test_strategy_authority_receipt"]["design_fingerprint"] == state["design_fingerprint"]




@pytest.mark.parametrize("case", ["one-owner", "root-docs", "future-existing-file", "missing-cross-task-contract", "ambiguous-owner", "undeclared-addition", "approved-seam", "undeclared-seam"])
def test_dependency_plan_uses_actual_scoped_ownership(tmp_path, case):
    from taskplane import plan_topology
    tmp_path = tmp_path / "repo"
    tmp_path.mkdir()
    for folder, text in (("provider", "VALUE = 1\n"),
            ("consumer", "from provider import value\n"), ("unrelated", "VALUE = 2\n")):
        destination = tmp_path / folder / "value.py"
        destination.parent.mkdir()
        destination.write_text(text)
    root_docs = ["README.md", "CHANGELOG.md", "CONTRIBUTING.md", "PRIVACY.md"]
    if case == "root-docs":
        for name in root_docs:
            (tmp_path / name).write_text("Existing documentation\n")
    for arguments in (("init",), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "source")):
        subprocess.run(["git", *arguments], cwd=tmp_path, check=True, capture_output=True)
    tasks = [{"id":"T1", "scope":["provider/value.py", "consumer/value.py"], "modules":["*"], "deps":[]}]
    if case == "root-docs":
        tasks[0]["scope"].extend(root_docs)
    if case == "future-existing-file":
        tasks[0]["scope"].append("unrelated/future.py")
    if case == "missing-cross-task-contract":
        tasks[0]["scope"] = ["provider/value.py"]
        tasks.append({"id":"T2", "scope":["consumer/value.py"], "deps":["T1"]})
    if case == "ambiguous-owner":
        tasks.append({"id":"T2", "scope":["consumer/value.py"], "deps":["T1"]})
    binding = {key:"a" * 64 for key in ("run_id", "candidate_fingerprint", "requirement_fingerprint", "design_fingerprint", "plan_fingerprint")}
    if case in {"missing-cross-task-contract", "ambiguous-owner"}:
        with pytest.raises(ValueError, match="missing seam contract" if case == "missing-cross-task-contract" else "ambiguous Plan"):
            plan_topology.produce_dependency_plan(str(tmp_path), binding=binding, seam_contracts=[], plan={"tasks":tasks})
        return
    if "addition" in case:
        tasks[0]["scope"].append("added/new.py")
    seams = []
    if case.endswith("-seam"):
        tasks.append({"id":"T2", "scope":["added/new.py"], "deps":["T1"],
            "new_modules":["added"], "design_edges":["added->provider:imports"] if case == "approved-seam" else []})
        seams = [{"producer":"provider", "consumer":"added", "kind":"imports",
            "producer_symbol":"provider.value", "consumer_symbol":"added.new", "schema_version":"1",
            "cardinality":"one", "positive":"test_wiring.py::test_connected", "severed":"test_wiring.py::test_severed"}]
        if case == "undeclared-seam":
            with pytest.raises(ValueError, match="not an approved Design edge"):
                plan_topology.produce_dependency_plan(str(tmp_path), binding=binding, seam_contracts=seams, plan={"tasks":tasks})
            return
    result = plan_topology.produce_dependency_plan(str(tmp_path), binding=binding, seam_contracts=seams, plan={"tasks":tasks})
    expected_nodes = ["consumer", "provider", "unrelated"] if case == "future-existing-file" else ["consumer", "provider"]
    assert result["decomposition"]["tasks"] == [{"id":"T1", "nodes":expected_nodes, "deps":[]}]
    assert len(result["seam-manifest"]["seams"]) == len(seams)
    assert result["source-coverage"]["complete"] is True
    if case == "root-docs":
        from taskplane import wiring_closure
        ws = str(tmp_path)
        assert loop.depgraph.readiness(ws, tasks)["passed"]
        assert "(root)" not in loop.depgraph.load(ws)["modules"]
        assert loop.depgraph.modules_for_scope(root_docs) == []
        assert loop.depgraph.scope_modules(ws, root_docs) == []
        assert loop.depgraph.impact(ws, root_docs)["touched"] == []
        assert loop.depgraph.completion(ws, root_docs, planned_modules=expected_nodes)["passed"]
        (tmp_path / "README.md").write_text("Updated documentation\n")
        realized = plan_topology.dependency_plan_projection(loop.depgraph.scan(ws, decompose=True), {"tasks":tasks})
        assert wiring_closure.realized_seam_conformance(result["seam-manifest"], realized)["status"] == "conformant"
        coding = {"scope_paths": tasks[0]["scope"], "dod": {"require_clean_scope_diff": True}}
        assert taskplane_lite.dod_check({"coding": coding}, ws, taskplane_lite.git_head(ws)) == []
        (tmp_path / "OUTSIDE.md").write_text("An undeclared file is still a scope violation\n")
        errors = taskplane_lite.dod_check({"coding": coding}, ws, taskplane_lite.git_head(ws))
        assert any("diff_scope:" in error and "OUTSIDE.md" in error for error in errors)
        # A source file that happens to contain '.md' in its name remains code.
        tasks[0]["scope"].append("README.md.py")
        readiness = loop.depgraph.readiness(ws, tasks)
        assert not readiness["passed"]
        assert any("(root)" in error for error in readiness["errors"])
        assert loop.depgraph.impact(ws, ["README.md.py"])["unknown"] == ["(root)"]
        assert not loop.depgraph.completion(ws, ["README.md.py"], planned_modules=expected_nodes)["passed"]
        (tmp_path / "README.md.py").write_text("VALUE = 3\n")
        realized = plan_topology.dependency_plan_projection(loop.depgraph.scan(ws, decompose=True), {"tasks":tasks})
        with pytest.raises(ValueError, match="unexpected source nodes"):
            wiring_closure.realized_seam_conformance(result["seam-manifest"], realized)
    if case == "future-existing-file":
        from taskplane import wiring_closure
        assert not (tmp_path / "unrelated/future.py").exists()
        (tmp_path / "unrelated/future.py").write_text("VALUE = 3\n")
        realized = plan_topology.dependency_plan_projection(
            loop.depgraph.scan(str(tmp_path), decompose=True), {"tasks":tasks})
        assert "unrelated/future.py" in {path for row in realized["components"] for path in row["files"]}
        assert wiring_closure.realized_seam_conformance(
            result["seam-manifest"], realized)["status"] == "conformant"
    if "addition" in case or seams:
        from taskplane import wiring_closure
        (tmp_path / "added").mkdir()
        (tmp_path / "added/new.py").write_text("from provider import value\n" if seams else "VALUE = 3\n")
        realized = plan_topology.dependency_plan_projection(
            loop.depgraph.scan(str(tmp_path), decompose=True), {"tasks":tasks})
        if case == "undeclared-addition":
            with pytest.raises(ValueError, match="unexpected source nodes"):
                wiring_closure.realized_seam_conformance(result["seam-manifest"], realized)
        else:
            assert wiring_closure.realized_seam_conformance(
                result["seam-manifest"], realized)["status"] == "conformant"


@pytest.mark.parametrize("depends", [False, True], ids=["independent", "predecessor"])
def test_incremental_graph_conformance_requires_current_work_and_merged_completion(tmp_path, depends):
    from taskplane import plan_topology, wiring_closure
    ws = _workspace(tmp_path)
    (Path(ws) / "app.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid",
        "commit", "-qm", "source"], cwd=ws, check=True)
    plan = {"tasks": [
        {"id":"T1", "scope":["app.py", "alpha/**"], "new_modules":["alpha"], "deps":[]},
        {"id":"T2", "scope":["beta/**"], "new_modules":["beta"], "deps":["T1"] if depends else []}]}
    binding = {key:"a" * 64 for key in ("run_id", "candidate_fingerprint", "requirement_fingerprint", "design_fingerprint", "plan_fingerprint")}
    produced = plan_topology.produce_dependency_plan(ws, binding=binding, seam_contracts=[], plan=plan)
    manifest = produced["seam-manifest"]
    topology = plan_topology.expected_dependency_topology(produced["decomposition"], plan, [])
    scopes = {task: plan_topology.task_conformance_scope(topology, task) for task in ("T1", "T2")}
    def realized():
        return plan_topology.dependency_plan_projection(loop.depgraph.scan(ws, decompose=True), plan)
    def add(module):
        (Path(ws) / module).mkdir()
        (Path(ws) / module / "new.py").write_text("VALUE = 2\n")
    add("beta")
    if depends:
        with pytest.raises(ValueError, match="missing or unexpected source nodes"):
            wiring_closure.realized_seam_conformance(manifest, realized(), task_scope=scopes["T2"])
    else:
        assert wiring_closure.realized_seam_conformance(manifest, realized(), task_scope=scopes["T2"])["status"] == "conformant"
    with pytest.raises(ValueError, match="missing or unexpected source nodes"):
        wiring_closure.realized_seam_conformance(manifest, realized())
    add("alpha")
    for scope in scopes.values():
        assert wiring_closure.realized_seam_conformance(manifest, realized(), task_scope=scope)["status"] == "conformant"
    final = wiring_closure.realized_seam_conformance(manifest, realized())
    assert final["status"] == "conformant" and "task_scope" not in final
    assert final["manifest_fingerprint"] == manifest["fingerprint"]
    # A removed current-task node still fails; partial does not mean optional.
    shutil.rmtree(Path(ws) / "beta")
    with pytest.raises(ValueError, match="missing or unexpected source nodes"):
        wiring_closure.realized_seam_conformance(manifest, realized(), task_scope=scopes["T2"])


@pytest.mark.parametrize("phase, paths", [("build", {"stage":"worker-stage.json"}),
    ("build", None), ("plan", None)])
def test_phase_output_mapping_refuses_before_nonce_or_worker_effects(monkeypatch, phase, paths):
    """Negative preflight only; no authority or output is supplied by this stub."""
    from types import SimpleNamespace
    from taskplane.tests.test_r0001_phase_agents_spec import _registry
    context = {"configuration":{"output_paths":{phase:paths}},
        "stage":{"stage_kind":phase, "authority":{}},
        "definition":_registry().admit(phase, ()).to_dict(),
        "run_id":"preflight-only", "store":SimpleNamespace(load=lambda run_id: {})}
    monkeypatch.setattr(loop, "_phase_bridge_context", lambda *args: context)
    monkeypatch.setattr(loop, "_phase_bridge_authorize", lambda *args: None)
    with pytest.raises(ValueError, match="phase output paths do not match declared outputs"):
        loop._phase_bridge_prepare("unused", {}, {}, {})


@pytest.mark.parametrize("case", ["uncanceled", "different-reason", "foreign-run", "foreign-candidate",
    "dispatch-uncertain", "effect-lease", "disabled-key", "unknown-hook"])
def test_unprepared_build_replacement_refuses_activity_or_cancellation_conflict(tmp_path, monkeypatch, case):
    """Negative composition checks using actual nonce/cancellation owners."""
    from types import SimpleNamespace
    from taskplane.tests.test_r0001_agent_runtime import _setup
    runtime, dispatch, _ = _setup(tmp_path)
    ws = _workspace(tmp_path)
    original = dict(dispatch.nonce_bindings)
    binding = dict(original, attempt_id="new-intent", deadline=300.0)
    context = {"stage":{"stage_id":"build-stage", "stage_kind":"build", "fingerprint":"f" * 64},
        "run_id":original["run_id"], "store":SimpleNamespace(load=lambda _: {})}
    monkeypatch.setattr(loop, "_phase_bridge_authorize", lambda *args: None)
    monkeypatch.setattr(loop, "load", lambda _: {"attempt_leases":{"build-stage":{"status":"active"}}} if case == "effect-lease" else {})
    loop.tp.record_expected_dispatch(ws, "step", "tp-executor", "standard", None,
        task_name="old-worker", intent_id=original["attempt_id"],
        intent_run_id="foreign" if case == "foreign-run" else original["run_id"])
    if case != "uncanceled":
        loop.tp.cancel_expected_dispatch(ws, original["attempt_id"],
            reason="unrelated" if case == "different-reason" else "worker-contract-activation-failed")
    if case == "foreign-candidate":
        binding["candidate_fingerprint"] = "b" * 64
    elif case == "dispatch-uncertain":
        runtime.nonce.reserve_dispatch(dispatch.issued, original)
    elif case == "disabled-key":
        runtime.nonce.disable_key()
    elif case == "unknown-hook":
        # Malformed presence must refuse; it is deliberately NOT a host receipt.
        runtime.nonce._hook_path(dispatch.issued, "start").write_text("not host evidence")
    before = runtime.nonce._path.read_bytes()
    with pytest.raises(ValueError, match="prior preparation|nonce key is disabled"):
        loop._phase_bridge_preparation_operation(ws, context, runtime.nonce, binding)
    assert runtime.nonce._path.read_bytes() == before


@pytest.mark.parametrize("changed", ["source", "policy", "binding", "recorded-impact", "signature"])
def test_product_handoff_freshness_still_rejects_content_changes(collected_product_handoff, monkeypatch, changed):
    ws, _, _, completion, material, artifacts = collected_product_handoff
    signed = artifacts.read(completion["runtime_receipt"])
    original_material = copy.deepcopy(material)
    if changed == "source":
        (Path(ws) / "README.md").write_text("Changed implementation source\n")
        subprocess.run(["git", "add", "README.md"], cwd=ws, check=True)
        subprocess.run(["git", "commit", "-qm", "changed source"], cwd=ws, check=True)
    elif changed == "policy":
        producer = loop.depgraph.impact
        def changed_impact(*args, **kwargs):
            value = copy.deepcopy(producer(*args, **kwargs))
            value["policy"]["local_depth"] += 1
            return value
        monkeypatch.setattr(loop.depgraph, "impact", changed_impact)
    elif changed == "binding":
        material["bindings"]["candidate_fingerprint"] = "f" * 64
    elif changed == "recorded-impact":
        altered = artifacts.read(material["impact_reference"])
        altered["total_impacted"] += 1
        material["impact_reference"] = artifacts.put("phase-impact", altered)
    else:
        signed["signature"] = "corrupt"
    with pytest.raises(ValueError):
        loop._phase_bridge_signing(ws, material).verify(signed, store=artifacts)
    assert artifacts.read(completion["preparation"]) == original_material




def _cli(ws, *arguments):
    import contextlib
    import io
    from taskplane import tp as cli
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli.main(["loop", "--workspace", ws, *arguments])
    value = json.loads(output.getvalue())
    assert code == 0, json.dumps(value, indent=2)
    surface = value.get("obligations", value)
    assert "dashboard_refresh" not in surface, surface.get("dashboard_refresh")
    if arguments[0] in {"gate", "approve", "retro"}:
        snapshot = surface["dashboard_snapshot"]["snapshot"]
        assert snapshot["values"]["loop"]["goal"] == loop.load(ws)["goal"]
        assert snapshot["values"]["loop"]["requirement_id"] == loop.load(ws)["requirement_id"]
        assert "phase_graph_error" not in snapshot["values"]
        assert snapshot["values"].get("design_graph"), snapshot["values"]
        if loop.load(ws).get("tasks"):
            assert snapshot["values"]["plan_task_dag"]["task_total"] == len(loop.load(ws)["tasks"])
        if snapshot["stage"] in {"design_approval", "plan_approval", "signoff"}:
            assert snapshot["safe_actions"] == ["approve", "reject"]
        assert surface["dashboard"]["delivery"]["status"] == "published"
    return value


@pytest.mark.parametrize("invalid_collection", [False, True], ids=["accepted", "invalid-conformance"])
def test_public_plan_build_collects_scoped_commit(collected_lens_design, monkeypatch, invalid_collection):
    """Public entry points with real local effects and simulated host events."""
    from taskplane.tests.phase_fixture import _emit_host_hook
    ws, store, run_id, artifacts, _ = collected_lens_design
    assert _cli(ws, "gate", "pass")["step"] == "design_approval"
    assert _cli(ws, "approve", "--by", "human:fixture")["step"] == "plan"
    action = _cli(ws, "next")
    assert set(action) == {"schema", "stage_runtime_dispatch", "obligations"}
    assert action["obligations"]["dispatch_allowed"] is True
    assert _emit_host_hook(ws, action, "SubagentStart", monkeypatch) == 0
    requirement = loop.reqs.get_requirement(ws, loop.load(ws)["requirement_id"])
    plan = {"requirement": requirement["id"], "delivery_mode": "build", "tasks": [{
        "id": "T1", "task": "Preserve greeting", "scope": ["app.py"], "modules": ["app"],
        "deps": [], "contracts": [], "criteria": requirement["acceptance"],
        "acceptance_refs": requirement["acceptance"], "tests": "python3 -m pytest -q " + SELECTOR,
        "test_contract": {"changed_producers": ["app.py"]},
        "test_strategy_authority": {"schema": "taskplane.plan-test-strategy-reference/v1",
            "path": "design/test-strategy.json", "strategy_fingerprint": _strategy()["contract_fingerprint_sha256"],
            "criterion_ids": ["AC-T11"], "changed_producer_ids": ["spec-package"]}}]}
    folder = Path(ws) / "plan"
    folder.mkdir(exist_ok=True)
    (folder / "tasks.json").write_text(json.dumps(plan))
    (folder / "plan.md").write_text("Preserve the greeting in app.py; verify test_app.py.\n")
    assert _emit_host_hook(ws, action, "SubagentStop", monkeypatch) == 0
    assert phase_pending(ws)["status"] == "collected"
    assert _cli(ws, "gate", "pass")["step"] == "plan_approval"
    assert _cli(ws, "approve", "--by", "human:fixture")["step"] == "execute"
    authority = open_delivery_root(ws)
    # The CLI reads the same host-owned observation key on its own path.
    build = loop.next_action(ws, root_observation_authority=authority)
    assert build["obligations"].get("dispatch_allowed") is True, (build, loop.load(ws)["dispatch_telemetry"])
    assert _emit_host_hook(ws, build, "SubagentStart", monkeypatch) == 0
    target = Path(ws) / "app.py"
    target.write_text(target.read_text().replace('f"Hello {name}"', '"Hello " + name'))
    subprocess.run(["git", "add", "app.py"], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "scoped greeting change"], cwd=ws, check=True)
    slot = build["obligations"]["contract_bootstrap"]["task_slot"]
    with monkeypatch.context() as worker:
        worker.setenv("TASKPLANE_TASK", slot)
        assert _cli(ws, "submit", "pass")["submitted"] is True
    if invalid_collection:
        from taskplane import phase_amendment
        prior_state = loop.load(ws)
        prior_context = loop._phase_bridge_context(ws, prior_state)
        stage_id = prior_context["stage"]["stage_id"]
        prior_records = phase_records.phase_records(store.load(run_id))
        code = target.read_bytes()
        def invalid_output(*args, **kwargs):
            # Exact native terminal/lease reconciliation must precede even a
            # rejected producer artifact. This is failure injection, never a
            # claimed Build acceptance or fabricated conformance receipt.
            lease = loop.load(ws)["attempt_leases"][stage_id]
            assert lease["released"] is True and lease["terminal_identity"]
            assert set(lease["effects"].values()) == {"observed"}
            raise ValueError("realized seam conformance: missing or unexpected source nodes")
        with monkeypatch.context() as broken_candidate:
            broken_candidate.setattr(loop, "seal_phase_build_conformance", invalid_output)
            assert _emit_host_hook(ws, build, "SubagentStop", monkeypatch) == 2
        released = loop.tp.released_worker_contract(ws, slot)
        assert released["worker_lifecycle"]["terminal"]["authority"] == "phase-observation"
        assert phase_pending(ws)["completion"] is None
        with pytest.raises(ValueError, match="matching terminal and collected output"):
            loop._phase_bridge_gate_check(ws, loop.load(ws))
        assert phase_records.phase_records(store.load(run_id)) == prior_records
        assert loop.load(ws)["step"] == "execute"
        proposal = phase_amendment.candidate(loop, ws, "design", requirement["id"])
        actor = prior_context["stage"]["authority"]["actor"]
        amended = phase_amendment.amend(loop, ws, phase="design", by=actor,
            reason="Reaffirm unchanged Design to correct Plan ownership for an already-approved path",
            requirement_id=requirement["id"], expected_stage_fingerprint=proposal["stage_fingerprint"],
            requirement_fingerprint=proposal["requirement_fingerprint"],
            candidate_fingerprint=proposal["candidate_fingerprint"], worker_stopped=True)
        assert not amended.get("error"), amended
        assert amended["step"] == "design_approval"
        decision = phase_amendment.current(loop, ws, loop.load(ws))
        previous = artifacts.read(decision["previous_workflow"])
        assert previous["tasks"] == prior_state["tasks"]
        assert previous["attempt_leases"][stage_id]["released"] is True
        retired = loop._indexed_stage(store, store.load(run_id), run_id, stage_id)
        assert retired["outcome"] == "closed" and retired["terminal"]["completed_deliverables"] == []
        assert phase_records.phase_records(store.load(run_id)) == prior_records
        assert target.read_bytes() == code
        assert loop.approve(ws, by=actor)["step"] == "plan"
        replacement = loop._phase_bridge_context(ws, loop.load(ws))
        package = loop.phase_harness.input_package(loop, replacement)
        assert package.read("design")["requirement"] == requirement["id"]
        assert package.read("test-strategy") == _strategy()
        assert loop.load(ws)["tasks"] == []
        return
    assert _emit_host_hook(ws, build, "SubagentStop", monkeypatch) == 0
    assert phase_pending(ws)["status"] == "collected"
    assert _cli(ws, "gate", "pass")["step"] == "evaluate"
    # A simulated human decision uses the real run receipt owner. The actual
    # delivery kernel must carry measured capacity into action and evidence.
    from taskplane import review_evidence
    current = store.load(run_id)
    decision = {"schema": "taskplane.resource-policy/v1", "run_id": run_id,
        "mode": "advisory", "actor": loop.load(ws)["_stage_native_root_authority"]["actor"],
        "authority_fingerprint": "f" * 64, "decided_at": 100}
    phase_records.commit_phase_record(store, run_id, expected_revision=current["revision"],
        operation_id="run-resource-limits", operation="resource_policy",
        request_fingerprint=review_evidence.content_fingerprint(decision), result=decision,
        validate_authority=lambda manifest: None)
    review = loop._review_runtime_modules()[2]
    monkeypatch.setattr(review, "DEFAULT_MAX_DIFF_BYTES", 1)
    captures = []
    actual_kernel = loop._review_kernel
    def observed_kernel(*args, **kwargs):
        manifest, routing = actual_kernel(*args, **kwargs)
        captures.append(manifest)
        return manifest, routing
    monkeypatch.setattr(loop, "_review_kernel", observed_kernel)
    evaluate = loop.next_action(ws, root_observation_authority=authority)
    assert evaluate["obligations"].get("dispatch_allowed") is True, evaluate
    assert captures and captures[0]["diff_capacity"]["previous_max_diff_bytes"] == 1
    assert captures[0]["diff_capacity"]["additional_cost"] == "unknown"
    review_state = review._load_state(ws, captures[0]["run_id"])
    envelope = review_evidence._load_complete_envelope(
        review_evidence.ArtifactStore(ws), review_state["envelope"])
    assert envelope["diff"]["capacity"] == captures[0]["diff_capacity"]
    assert loop._phase_bridge_context(ws, loop.load(ws))["stage"]["stage_kind"] == "evaluate"

    verdict = _complete_evaluation(ws, monkeypatch, evaluate, run_id, requirement, "T1")
    assert _cli(ws, "gate", "pass")["step"] == "em"
    _complete_engineering(ws, monkeypatch, authority, artifacts, verdict, ["T1"])


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_rejected_evaluate_stop_releases_only_authenticated_child(
        collected_lens_design, monkeypatch, outcome):
    """Real phase/slot owners with explicitly simulated native test events."""
    import sys
    from taskplane import run_artifacts
    from taskplane.tests.phase_fixture import _emit_host_hook

    class BoundaryChecked(Exception):
        pass

    def reject_children(ws, patch, evaluate, run_id, requirement, task_id):
        import loop as hook_loop
        children = evaluate["obligations"]["children"]
        assert len(children) == 2
        for child in children:
            assert _emit_host_hook(ws, child, "SubagentStart", patch) == 0
        state = loop.load(ws)
        route = state["evaluate_child_evidence"]
        records = phase_records.phase_records(collected_lens_design[1].load(run_id))
        validation = copy.deepcopy(run_artifacts.load_manifest(
            route["artifact_root"])["classes"]["validation"])
        collection_calls = []
        collect = hook_loop.complete_observed_evaluate_evidence_child

        def observe_collection(*args, **kwargs):
            collection_calls.append(args[1].get("last_assistant_message"))
            return collect(*args, **kwargs)

        patch.setattr(hook_loop, "complete_observed_evaluate_evidence_child", observe_collection)
        slots = [child["obligations"]["contract_bootstrap"]["task_slot"] for child in children]
        first = children[0]
        before_slots = {slot: Path(taskplane_lite.active_contract_path(ws, slot)).read_bytes()
                        for slot in slots}
        # Matching task_name alone never authorizes a foreign child or an
        # event whose native transcript cannot be authenticated.
        assert _emit_host_hook(ws, first, "SubagentStop", patch,
            agent_id="foreign-child", last_assistant_message="{}") == 2
        assert _emit_host_hook(ws, first, "SubagentStop", patch,
            agent_transcript_path=str(Path(ws).parent / "absent-native-transcript.jsonl"),
            last_assistant_message="{}") == 2
        assert collection_calls == []
        assert {slot: Path(taskplane_lite.active_contract_path(ws, slot)).read_bytes()
                for slot in slots} == before_slots

        # Missing JSON and a real substantive-schema rejection must both
        # release their own stopped slot without accepting child evidence.
        for index, (child, raw) in enumerate(zip(children, (None, '{"schema":"blocked"}'))):
            assert _emit_host_hook(ws, child, "SubagentStop", patch,
                last_assistant_message=raw, outcome=outcome) == 2
            slot = slots[index]
            assert not Path(taskplane_lite.active_contract_path(ws, slot)).exists()
            released = taskplane_lite.released_worker_contract(ws, slot)
            receipt = released["worker_lifecycle"]["terminal"]
            assert receipt["authority"] == "host-lifecycle"
            assert receipt["submission_status"] == "evidence-rejected"
            assert receipt["outcome"] == outcome
            assert receipt["owner"] == released["worker_lifecycle"]["owner"]
            if index == 0:
                assert Path(taskplane_lite.active_contract_path(ws, slots[1])).read_bytes() == before_slots[slots[1]]
        assert collection_calls == ([None, '{"schema":"blocked"}'] if outcome == "success" else [])
        assert run_artifacts.load_manifest(route["artifact_root"])["classes"]["validation"] == validation
        assert phase_records.phase_records(collected_lens_design[1].load(run_id)) == records
        assert loop.load(ws)["step"] == "evaluate"
        assert phase_pending(ws)["completion"] is None
        with pytest.raises(ValueError, match="matching terminal and collected output"):
            loop._phase_bridge_gate_check(ws, loop.load(ws))
        raise BoundaryChecked

    monkeypatch.setattr(sys.modules[__name__], "_complete_evaluation", reject_children)
    with pytest.raises(BoundaryChecked):
        test_public_plan_build_collects_scoped_commit(
            collected_lens_design, monkeypatch, False)


def _complete_evaluation(ws, monkeypatch, evaluate, run_id, requirement, task_id):
    from taskplane.tests.phase_fixture import _emit_host_hook
    from taskplane import evaluation_output, governed_commands, storage
    from taskplane.tests.phase_fixture import authored_evidence_results
    children = evaluate["obligations"]["children"]
    assert len(children) == 2
    assignments = []
    for child in children:
        assert set(child) == {"schema", "stage_runtime_dispatch", "obligations"}
        assert _emit_host_hook(ws, child, "SubagentStart", monkeypatch) == 0
        with monkeypatch.context() as worker:
            worker.setenv("TASKPLANE_TASK", child["obligations"]["contract_bootstrap"]["task_slot"])
            inputs = loop.phase_harness.read_input(loop, ws, child["stage_runtime_dispatch"])
            assignments.append(inputs["assignment"])
            with pytest.raises(ValueError, match="another phase"):
                loop.phase_harness.read_input(loop, ws, evaluate["stage_runtime_dispatch"])
    def execute(assignment, argv, label):
        index = assignments.index(assignment)
        slot = children[index]["obligations"]["contract_bootstrap"]["task_slot"]
        authorization = "fixture:" + task_id + ":" + label
        with monkeypatch.context() as worker:
            worker.setenv("TASKPLANE_TASK", slot)
            with pytest.raises(governed_commands.GovernedCommandError, match="exact evidence assignment"):
                governed_commands.execute(ws, "launch", {
                    "authorization": authorization, "argv": argv + ["unapproved.py"], "run_id": run_id,
                    "task_id": task_id, "assignment_binding": assignment["binding"]})
            launched = governed_commands.execute(ws, "launch", {
                "authorization": authorization, "argv": argv, "run_id": run_id,
                "task_id": task_id, "assignment_binding": assignment["binding"]})
            completed = governed_commands.execute(ws, "wait", {
                "authorization": authorization, "handle": launched["handle"],
                "consumer": "evaluate:" + assignment["producer_kind"], "timeout": 30})
            assert completed["event"]["state"] == "succeeded", completed
        return {"authorization": authorization, "handle": launched["handle"]}
    results = authored_evidence_results(assignments, execute)
    for child, assignment in zip(children, assignments):
        assert _emit_host_hook(ws, child, "SubagentStop", monkeypatch,
            last_assistant_message=json.dumps(results[assignment["producer_kind"]])) == 0
    assert _emit_host_hook(ws, evaluate, "SubagentStart", monkeypatch) == 0
    verdict = {"schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
        "task": task_id, "requirement": requirement["id"], "verdict": "pass",
        "criteria": [{"criterion": criterion, "status": "met",
            "evidence": "The exact approved greeting selector passed on the Build commit."}
            for criterion in requirement["acceptance"]],
        "evaluation": {"status": "complete", "reason_code": "none", "detail": "Both evidence producers completed."},
        "graph": {"dispositions": [], "requirements_checked": [requirement["id"]], "contracts_checked": []},
        "failures": []}
    verdict = evaluation_output.attach_child_evidence(verdict, run_id=run_id,
        evaluator_attempt_id=assignments[0]["binding"]["evaluator_attempt_id"], expected_binding=assignments[0]["binding"])
    inputs = loop.phase_harness.read_input(loop, ws, evaluate["stage_runtime_dispatch"])
    judgment_path = next(row["path"] for row in inputs["outputs"] if row["artifact_class"] == "judgment")
    for path in (Path(ws) / judgment_path, Path(storage.evaluation_path(ws))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(verdict))
    with monkeypatch.context() as worker:
        worker.setenv("TASKPLANE_TASK", evaluate["obligations"]["contract_bootstrap"]["task_slot"])
        assert _cli(ws, "submit", "pass")["submitted"] is True
    assert _emit_host_hook(ws, evaluate, "SubagentStop", monkeypatch) == 0
    assert phase_pending(ws)["status"] == "collected"
    return verdict


def _complete_engineering(ws, monkeypatch, authority, artifacts, verdict, task_ids):
    from taskplane import storage
    from taskplane.tests.phase_fixture import _emit_host_hook
    em = loop.next_action(ws, root_observation_authority=authority)
    assert set(em) == {"schema", "stage_runtime_dispatch", "obligations"}
    assert em["obligations"].get("dispatch_allowed") is True, em
    assert not em["obligations"].get("children")
    assert _emit_host_hook(ws, em, "SubagentStart", monkeypatch) == 0
    inputs = loop.phase_harness.read_input(loop, ws, em["stage_runtime_dispatch"])
    conformance = artifacts.read(inputs["graph_conformance"])
    assert conformance["status"] == "conformant"
    assert "task_scope" not in conformance
    accepted = inputs["accepted_evaluations"]
    assert [row["task_id"] for row in accepted] == task_ids
    assert all(row["candidate_sha"] for row in accepted)
    expected_lens_plans = {entry["plan"]["fingerprint"] for task in accepted
        for entry in artifacts.read(task["lens_evidence"])["entries"]}
    from taskplane import review, review_evidence
    state = loop.load(ws)
    kernel = loop.review_kernel_binding(state, "em", loop._current_task(state))
    kernel_state = review._load_state(ws, kernel["run_id"])
    envelope = review_evidence._load_complete_envelope(artifacts, kernel_state["envelope"])
    design = json.loads((Path(ws) / "design/contract.json").read_text())
    graph = design["graph"]
    meta = {**review_evidence._read_current(artifacts), "lens_coverage": {},
        "impact": envelope["impact"], "tests": {task_id: SELECTOR for task_id in task_ids},
        "graph": verdict["graph"],
        "design": {"fingerprint": state["design_fingerprint"], "verdict": "conformant",
            "modules_checked": graph["proposed_modules"], "edges_checked": [],
            "contracts_checked": [], "drift": []}, "gate": {"verdict": "recommend-pass"}}
    for name, value in (("findings.json", json.dumps({"meta": meta, "findings": []})),
            ("report.md", "The scoped greeting change preserves the approved behavior. "
                "Both independent evidence producers completed against the Build commit.\n")):
        path = Path(storage.review_public_path(ws, name))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    em_verdict = {key: value for key, value in verdict.items() if key != "child_evidence"}
    em_verdict.update(task="engineering-signoff", accepted_evaluations=accepted)
    output = next(row["path"] for row in inputs["outputs"] if row["artifact_class"] == "judgment")
    path = Path(ws) / output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(em_verdict))
    with monkeypatch.context() as worker:
        worker.setenv("TASKPLANE_TASK", em["obligations"]["contract_bootstrap"]["task_slot"])
        assert _cli(ws, "submit", "pass")["submitted"] is True
    assert _emit_host_hook(ws, em, "SubagentStop", monkeypatch) == 0
    assert phase_pending(ws)["status"] == "collected"
    result = artifacts.read(phase_pending(ws)["completion"]["runtime_result"])
    packet_ref = next(ref for ref in result["collected_output_references"] if ref["kind"] == "lens-evidence")
    packet = artifacts.read(packet_ref)
    assert expected_lens_plans < {entry["plan"]["fingerprint"] for entry in packet["entries"]}
    assert all(artifacts.read(entry["collection"])["status"] == "complete" for entry in packet["entries"])
    _finish_em_gate_and_retro(ws, monkeypatch, authority)


def _finish_em_gate_and_retro(ws, monkeypatch, authority):
    assert _cli(ws, "gate", "pass")["step"] == "signoff"
    assert _cli(ws, "approve", "--by", "human:fixture")["step"] == "retro"
    _run_retro(ws, monkeypatch, authority)


def _run_retro(ws, monkeypatch, authority):
    from taskplane.tests.phase_fixture import _emit_host_hook
    retro = loop.next_action(ws, root_observation_authority=authority)
    assert set(retro) == {"schema", "stage_runtime_dispatch", "obligations"}
    assert retro["obligations"].get("dispatch_allowed") is True, retro
    assert _emit_host_hook(ws, retro, "SubagentStart", monkeypatch) == 0
    inputs = loop.phase_harness.read_input(loop, ws, retro["stage_runtime_dispatch"])
    stage = loop._phase_bridge_context(ws, loop.load(ws))["stage"]
    output = next(row["path"] for row in inputs["outputs"] if row["artifact_class"] == "stage")
    path = Path(ws) / output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stage))
    assert _emit_host_hook(ws, retro, "SubagentStop", monkeypatch) == 0
    assert phase_pending(ws)["status"] == "collected"
    report = _cli(ws, "retro")
    final = loop.load(ws)
    assert final["retro"]["status"] == "complete", report
    assert final["step"] == "done"
    assert report["timing"]["elapsed_seconds"] > 0
    assert {row["phase"] for row in report["timing"]["stages"]} == {"product", "design", "plan", "build", "evaluate", "engineering", "retro"}
    assert all(row["completed_at"] for row in report["timing"]["stages"])
    assert final["completed_at"] >= final["started_at"] > 0
    assert final["terminal_artifacts"]
    assert loop.tp._active_worker_contracts(ws) == []


def test_parallel_task_phase_commits_preserve_sibling_state(tmp_path):
    """Concurrent task transactions use separate views of one real aggregate."""
    from concurrent.futures import ThreadPoolExecutor
    from taskplane import gates, run_store, storage
    ws = _workspace(tmp_path)
    identity = storage.resolve_repository_identity(ws)
    store = run_store.RunStore()
    run = store.create(identity, run_id="parallel-phase-views", checkout=ws,
        host={"kind": "codex"}, target={"kind": "workspace", "revision": loop.tp.git_head(ws)})
    storage.write_workspace_locator(ws, identity=identity,
        layout=storage.resolve_layout(identity, home=store.home, run_id=run["run_id"]), run_id=run["run_id"])
    store.save_workflow(run["run_id"], {"run_id": run["run_id"], "parallel": True,
        "step": "execute", "current_task": 0, "tasks": [
            {"id": "T1", "status": "built", "phase_step": "evaluate", "evaluate_child_evidence": {"owner": "T1"}},
            {"id": "T2", "status": "running", "phase_step": "execute", "_submission": {"owner": "T2"}}]})
    workspaces = {}
    for task_id in ("T1", "T2"):
        worker = storage.task_worktree_path(ws, task_id)
        Path(worker).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "worktree", "add", "-b", "codex/" + task_id, worker], cwd=ws,
            check=True, capture_output=True)
        storage.bind_worker_locator(ws, worker, task_id)
        workspaces[task_id] = worker
    assert loop.load(workspaces["T1"])["step"] == "evaluate"
    assert loop.load(workspaces["T2"])["step"] == "execute"
    assert "_submission" not in loop.load(workspaces["T1"])
    assert "evaluate_child_evidence" not in loop.load(workspaces["T2"])
    with pytest.raises(ValueError, match="sibling"):
        loop.stage_loop.task_phase_state(loop, workspaces["T1"], loop._load_raw(ws), "T2")
    def finish(task_id):
        with gates._gate_state(loop, workspaces[task_id], task_id) as state:
            task = loop._current_task(state)
            assert task["id"] == task_id
            task["status"] = "passed" if task_id == "T1" else "built"
            state["step"] = "execute" if task_id == "T1" else "evaluate"
            state.pop("_submission", None)
            state.pop("evaluate_child_evidence", None)
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(finish, ("T1", "T2")))
    final = loop._load_raw(ws)
    assert final["current_task"] == 0 and final["step"] == "execute"
    assert "_submission" not in final and "evaluate_child_evidence" not in final
    assert loop.load(workspaces["T1"])["step"] == "done"
    assert loop.load(workspaces["T2"])["step"] == "evaluate"
    assert all("_submission" not in task and "evaluate_child_evidence" not in task for task in final["tasks"])


@pytest.mark.parametrize("parallel_phase_mode", [True], indirect=True)
def test_parallel_phases_join_em_and_retro(collected_lens_design, monkeypatch):
    from taskplane.tests.phase_fixture import _emit_host_hook
    ws, _, run_id, artifacts, _ = collected_lens_design
    assert _cli(ws, "gate", "pass")["step"] == "design_approval"
    assert _cli(ws, "approve", "--by", "human:fixture")["step"] == "plan"
    plan_action = _cli(ws, "next")
    assert _emit_host_hook(ws, plan_action, "SubagentStart", monkeypatch) == 0
    requirement = loop.reqs.get_requirement(ws, loop.load(ws)["requirement_id"])
    task = {"id": "T1", "task": "Preserve greeting", "scope": ["app.py"], "modules": ["app"],
        "deps": [], "contracts": [], "criteria": requirement["acceptance"],
        "acceptance_refs": requirement["acceptance"],
        "tests": "python3 -m pytest -q " + " ".join(_strategy()["acceptance_criteria"][0]["selectors"]),
        "test_contract": {"changed_producers": ["app.py"]},
        "test_strategy_authority": {"schema": "taskplane.plan-test-strategy-reference/v1",
            "path": "design/test-strategy.json", "strategy_fingerprint": _strategy()["contract_fingerprint_sha256"],
            "criterion_ids": ["AC-T11"], "changed_producer_ids": ["spec-package"]}}
    second = copy.deepcopy(task)
    second.update(id="T2", task="Preserve helper greeting", scope=["helper/__init__.py"], modules=["helper"])
    second["test_contract"]["changed_producers"] = ["helper/__init__.py"]
    second["test_strategy_authority"]["changed_producer_ids"] = ["helper-package"]
    folder = Path(ws) / "plan"
    folder.mkdir(exist_ok=True)
    (folder / "tasks.json").write_text(json.dumps({"requirement": requirement["id"],
        "delivery_mode": "build", "tasks": [task, second]}))
    (folder / "plan.md").write_text("Preserve the greeting and document it in separate workspaces.\n")
    assert _emit_host_hook(ws, plan_action, "SubagentStop", monkeypatch) == 0
    assert _cli(ws, "gate", "pass")["step"] == "plan_approval"
    assert _cli(ws, "approve", "--by", "human:fixture")["step"] == "execute"
    authority = open_delivery_root(ws)
    wave = loop.next_action(ws, root_observation_authority=authority)["obligations"]
    assert len(wave.get("wave", [])) == 2, wave
    actions = wave["wave"]
    workers = {}
    for action in actions:
        environment = action["obligations"]["contract_bootstrap"]["environment"]
        worker_ws = environment["PWD"]
        task_id = loop.runtime_storage.load_workspace_locator(worker_ws)["task_id"]
        workers[task_id] = (worker_ws, action)
        assert _emit_host_hook(worker_ws, action, "SubagentStart", monkeypatch) == 0
    assert workers["T1"][0] != workers["T2"][0] != ws
    worker_ws, action = workers["T1"]
    target = Path(worker_ws) / "app.py"
    target.write_text(target.read_text().replace('f"Hello {name}"', '"Hello " + name'))
    subprocess.run(["git", "add", "app.py"], cwd=worker_ws, check=True)
    subprocess.run(["git", "commit", "-qm", "scoped parallel greeting"], cwd=worker_ws, check=True)
    with monkeypatch.context() as worker:
        worker.setenv("TASKPLANE_TASK", action["obligations"]["contract_bootstrap"]["task_slot"])
        assert _cli(worker_ws, "submit", "pass")["submitted"] is True
    assert _emit_host_hook(worker_ws, action, "SubagentStop", monkeypatch) == 0
    evaluate = _assert_parallel_continuation(ws, worker_ws, workers["T2"][0], authority)
    _complete_evaluation(worker_ws, monkeypatch, evaluate, run_id, requirement, "T1")
    gated = loop.gate(ws, "pass", task_id="T1")
    assert not gated.get("error"), gated
    assert loop.load(ws)["step"] == "execute"
    assert loop.load(workers["T2"][0])["step"] == "execute"
    second_ws, second_action = workers["T2"]
    target = Path(second_ws) / "helper/__init__.py"
    target.write_text(target.read_text().replace('f"Hello {name}"', '"Hello " + name'))
    subprocess.run(["git", "add", "helper/__init__.py"], cwd=second_ws, check=True)
    subprocess.run(["git", "commit", "-qm", "scoped helper greeting"], cwd=second_ws, check=True)
    with monkeypatch.context() as worker:
        worker.setenv("TASKPLANE_TASK", second_action["obligations"]["contract_bootstrap"]["task_slot"])
        assert _cli(second_ws, "submit", "pass")["submitted"] is True
    assert _emit_host_hook(second_ws, second_action, "SubagentStop", monkeypatch) == 0
    gated = loop.gate(ws, "pass", task_id="T2")
    assert not gated.get("error"), gated
    continuation = loop.next_action(ws, root_observation_authority=authority)["obligations"]
    assert len(continuation.get("wave", [])) == 1, continuation
    verdict = _complete_evaluation(second_ws, monkeypatch, continuation["wave"][0], run_id, requirement, "T2")
    gated = loop.gate(ws, "pass", task_id="T2")
    assert not gated.get("error"), gated
    assert loop.load(ws)["step"] == "em", gated
    assert all(task["status"] == "passed" for task in loop.load(ws)["tasks"])
    assert '"Hello " + name' in (Path(ws) / "app.py").read_text()
    assert '"Hello " + name' in (Path(ws) / "helper/__init__.py").read_text()
    _complete_engineering(ws, monkeypatch, authority, artifacts, verdict, ["T1", "T2"])


def _assert_parallel_continuation(ws, worker_ws, second_ws, authority):
    gated = loop.gate(worker_ws, "pass", task_id="T1")
    assert not gated.get("error"), gated
    assert loop.load(worker_ws)["step"] == "evaluate"
    assert loop.load(second_ws)["step"] == "execute"
    continuation = loop.next_action(ws, root_observation_authority=authority)["obligations"]
    assert len(continuation.get("wave", [])) == 1, continuation
    evaluate = continuation["wave"][0]
    assert evaluate["obligations"]["role"] == "tp-evaluate"
    assert len(evaluate["obligations"]["children"]) == 2
    assert len(loop.tp._active_worker_contracts(second_ws)) == 1
    return evaluate


def test_missing_signoff_seal_never_recomputes_workspace_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "_compute_signoff_dod", lambda *_: pytest.fail("unsealed sign-off read workspace"))
    result = loop._signoff_dod(str(tmp_path), {})
    assert result["passed"] is False
    assert result["errors"] == ["sign-off requires immutable integration evidence"]


def test_task_criteria_never_borrow_other_requirement_authority(tmp_path, monkeypatch):
    monkeypatch.setattr(loop.reqs, "get_requirement", lambda *_: pytest.fail("read ambient requirement"))
    assert loop._criteria_for(str(tmp_path), {"requirement_id": "R-old"}, {"tests": "true"}) == []
    assert loop._criteria_for(str(tmp_path), {}, {"criteria": [" own task "]}) == ["own task"]


def test_phase_gate_refuses_v4_workflow_without_current_phase(tmp_path):
    from taskplane.tests.phase_fixture import save_component_workflow
    ws = _workspace(tmp_path)
    state = {"goal": "missing phase must not use inline gate", "step": "pm"}
    save_component_workflow(ws, state)
    before = loop.load(ws)
    refused = loop.gate(ws, "pass")
    assert "current agent-runtime phase" in refused["error"]
    assert loop.load(ws) == before
