"""The legacy loop opts into bounded v4 stage dispatch explicitly."""
from __future__ import annotations

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


@pytest.mark.parametrize("stage", ["projection", "seed", "capability", "start_seal",
    "observation_seal", "open", "advance", "final_snapshot",
    "start_seal_capability", "start_seal_binding", "start_seal_seed", "start_seal_unexpected",
    "start_seal_role", "start_seal_resumed", "start_seal_session_binding",
    "start_seal_host", "start_seal_path", "start_seal_session_missing", "start_seal_time_missing", "start_seal_all_guards"])
def test_root_hook_failure_trace_retains_safe_stage_and_code_only(tmp_path, monkeypatch, stage):
    """Failure injection into existing hook/audit owners; no native evidence."""
    import loop as hook_loop
    import root_seed
    import host_native
    import native_session_meter
    from taskplane import tp as cli
    from taskplane.tests.test_native_root_session import _prepared, _write_root
    case = stage
    if case.startswith("start_seal_"):
        stage = "start_seal"
    ws = str(tmp_path)
    _prepared(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "root-session")
    monkeypatch.setenv("TASKPLANE_NATIVE_HOOKS_LOADED", "supported")
    monkeypatch.setenv("TASKPLANE_MANAGED_HOOK_POLICY", "supported")
    transcript = tmp_path / "root-counter.jsonl"
    _write_root(transcript, total=40_000, sequence=1,
        resumed=case == "start_seal_resumed",
        session_id="foreign-session" if case == "start_seal_session_binding" else "root-session")
    if case == "start_seal_role":
        rows = [json.loads(line) for line in transcript.read_text().splitlines()]
        rows[0]["payload"]["parent_thread_id"] = "test-parent"
        transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    event = {"cwd":ws, "session_id":"root-session", "turn_id":"turn-root",
        "transcript_path":str(transcript)}
    if stage == "advance":
        cli._observe_active_loop_orchestrator(ws, event)
        assert hook_loop.load(ws)["root_hygiene"]["status"] == "open"
        _write_root(transcript, total=40_100, sequence=2)
    owner, name = {
        "projection": (cli, "_bounded_transcript_projection"),
        "seed": (root_seed, "load_root_seed"),
        "capability": (cli.host_caps, "root_session_capability"),
        "start_seal": (host_native, "start_root_session"),
        "observation_seal": (native_session_meter, "seal_root_observation"),
        "open": (hook_loop, "open_delivery_wave"),
        "advance": (hook_loop, "record_delivery_root_observation"),
        "final_snapshot": (hook_loop, "record_native_orchestrator_snapshot"),
    }[stage]
    sentinel = "sensitive-exception-sentinel /private/transcript?secret=do-not-retain"
    messages = {
        "start_seal_capability": ("host root-session capability is unsupported", "root_capability_unsupported"),
        "start_seal_binding": ("host root-session start binding does not match the prepared seed", "root_seed_binding_mismatch"),
        "start_seal_seed": ("root seed schema is unsupported", "root_seed_invalid"),
        "start_seal_unexpected": (sentinel, "RootSessionReceiptError"),
    }
    fresh_failures = {"start_seal_role":"root_role_not_root",
        "start_seal_resumed":"root_session_resumed",
        "start_seal_session_binding":"root_session_binding_mismatch"}
    guard_changes = {
        "start_seal_host": ({"host":"claude"}, ["root_host_not_codex"]),
        "start_seal_path": ({"effective_path":"unavailable"}, ["root_path_unavailable"]),
        "start_seal_session_missing": ({"session_fingerprint":None},
            ["root_fresh_start", "root_session_binding_mismatch", "root_session_missing"]),
        "start_seal_time_missing": ({"observed_at":""}, ["root_time_missing"]),
        "start_seal_all_guards": ({"host":"claude", "effective_path":"unavailable",
            "session_fingerprint":None, "observed_at":""}, ["root_fresh_start",
            "root_session_binding_mismatch", "root_host_not_codex", "root_path_unavailable",
            "root_session_missing", "root_time_missing"]),
    }
    host_snapshot = cli._host_capability_snapshot
    if case in guard_changes:
        def changed_host(*args, **kwargs):
            return replace(host_snapshot(*args, **kwargs), **guard_changes[case][0])
        monkeypatch.setattr(cli, "_host_capability_snapshot", changed_host)
    capability = cli.host_caps.root_session_capability
    def with_missing(*args, **kwargs):
        value = capability(*args, **kwargs)
        return {**value, "missing":["root_fresh_start", sentinel, "root_turn_mapping"]}
    if case == "start_seal_capability":
        monkeypatch.setattr(cli.host_caps, "root_session_capability", with_missing)
    def fail(*args, **kwargs):
        if case in messages:
            raise host_native.RootSessionReceiptError(messages[case][0])
        raise RuntimeError(sentinel)
    if case not in fresh_failures and case not in guard_changes:
        monkeypatch.setattr(owner, name, fail)
    cli._observe_active_loop_orchestrator(ws, event)
    text = (Path(cli.tp.tp_dir(ws)) / "trace.jsonl").read_text()
    records = [json.loads(line) for line in text.splitlines()]
    failures = [row for row in records if row["event"] == "native_orchestrator_meter_unavailable"]
    assert len(failures) == 1
    assert failures[0]["stage"] == ("identity" if case == "start_seal_session_binding" else stage)
    assert failures[0]["failure_code"] == ("ValueError" if case == "start_seal_session_binding"
        else "root_capability_unsupported" if case in fresh_failures or case in guard_changes
        else messages[case][1] if case in messages else "RuntimeError")
    if case == "start_seal_capability":
        assert failures[0]["tags"] == ["root_fresh_start", "root_turn_mapping"]
    if case in fresh_failures and case != "start_seal_session_binding":
        assert failures[0]["tags"] == ["root_fresh_start", fresh_failures[case]]
    if case in guard_changes:
        assert failures[0]["tags"] == guard_changes[case][1]
    assert sentinel not in text
    assert str(transcript) not in json.dumps(failures)


@pytest.mark.parametrize("case", ["advisory", "strict", "resumed", "foreign", "missing", "zero",
    "missing_identity", "conflicting_identity", "foreign_receipt", "foreign_workspace",
    "no_codex_environment", "conflicting_provider"])
def test_root_hook_respects_saved_advisory_without_reducing_cumulative_usage(tmp_path, monkeypatch, case):
    """Production policy/open owners; generated host metadata, not native proof."""
    from taskplane import tp as cli
    from taskplane.tests.test_r0001_j1_native import _supporting_pristine_phase_run
    from taskplane.tests.test_native_root_session import _write_root

    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    action = loop.next_action(ws)
    assert not action.get("error"), action
    policy = None
    if case != "strict":
        decision = loop.resolve(ws, "limits-advisory", by="human:simulated")
        assert not decision.get("error"), decision
        policy = decision["resource_policy"]
    state = loop.load(ws)
    # Preparation input only: no Build result or phase acceptance is invented.
    state["baseline"] = loop.tp.git_head(ws)
    loop.save(ws, state)
    loop.prepare_delivery_root(ws, seed_ref="waves/test/root-seed.json", wave_id="test",
        prepared_at="2026-09-08T22:25:37Z", operation_id="prepare-root-policy",
        design={"path":"design/contract.json", "fingerprint":"b" * 64},
        plan={"path":"plan/tasks.json", "fingerprint":"c" * 64},
        pickups=[{"id":"test", "write_scopes":["README.md"],
            "disjointness_receipt_fingerprint":"d" * 64}],
        outstanding_human_gates=[], predecessor_terminal_projection={"status":"none"})
    prepared = copy.deepcopy(loop.load(ws)["root_hygiene"])
    manifest = store.load(run_id)
    transcript = Path(ws) / "root-counter.jsonl"
    _write_root(transcript, total=0 if case == "zero" else 7_981_454, sequence=1,
        resumed=case == "resumed", session_id="foreign-root" if case == "foreign" else "root-session")
    if case == "missing":
        transcript.write_text("{}\n")
    monkeypatch.setenv("CODEX_THREAD_ID", "stale-inherited-session")
    monkeypatch.delenv("TASKPLANE_NATIVE_HOOKS_LOADED", raising=False)
    monkeypatch.delenv("TASKPLANE_MANAGED_HOOK_POLICY", raising=False)
    event = {"cwd":ws, "session_id":"root-session", "turn_id":"turn-root",
        "transcript_path":str(transcript), "tool_name":"Read", "tool_input":{}}
    # The actual hook admission owner records the event session, independently
    # of the ambient process session inherited by the hook subprocess.
    receipt_event = dict(event)
    if case == "foreign_receipt":
        receipt_event["session_id"] = "foreign-receipt-session"
    if case == "foreign_workspace":
        receipt_event["cwd"] = str(tmp_path / "foreign-workspace")
    cli.host_caps.record_runtime_hook_receipt(cli.tp.store_home(),
        hook_path="bridge" if case == "foreign_workspace" else "native", event=receipt_event)
    if case == "missing_identity":
        event.pop("session_id")
    if case == "conflicting_identity":
        event["thread_id"] = "conflicting-session"
    if case == "conflicting_provider":
        event["provider"] = "claude"
    if case == "no_codex_environment":
        for variable in ("CODEX_HOME", "CODEX_THREAD_ID", "CLAUDE_SESSION_ID"):
            monkeypatch.delenv(variable, raising=False)
        assert cli._host_capability_snapshot(ws).host == "claude"
        assert cli._host_capability_snapshot(ws).session_fingerprint is None
    else:
        assert cli._host_capability_snapshot(ws).session_fingerprint == hashlib.sha256(
            b"stale-inherited-session").hexdigest()  # Ordinary CLI default is unchanged.
    import loop as hook_loop
    open_wave = hook_loop.open_delivery_wave
    open_errors = []
    def observed_open(*args, **kwargs):
        try:
            return open_wave(*args, **kwargs)
        except ValueError as exc:
            open_errors.append(str(exc))
            raise
    monkeypatch.setattr(hook_loop, "open_delivery_wave", observed_open)
    cli._observe_active_loop_orchestrator(ws, event)
    opened = loop.load(ws)["root_hygiene"]
    if case not in {"advisory", "no_codex_environment"}:
        assert opened == prepared
        if case == "strict":
            assert open_errors == ["first observed input exceeds seed budget"]
        return
    assert opened["status"] == "open", open_errors
    assert opened["meter"]["first_observed_input_tokens"] == 7_981_453
    assert opened["meter"]["usage"]["total_tokens"] == 7_981_454
    assert opened["conformance"] == "overridden"
    assert opened["canary_eligible"] is False
    assert opened["override"]["by"] == policy["actor"]
    assert policy["fingerprint"] in opened["override"]["reason"]
    assert opened["override"]["failed_checks"] == ["first observed input exceeds seed budget"]
    assert opened["prepare_receipt"] == prepared["prepare_receipt"]
    cli._observe_active_loop_orchestrator(ws, event)
    assert loop.load(ws)["root_hygiene"] == opened
    assert store.load(run_id) == manifest


def test_stage_native_first_worker_identity_is_run_scoped_and_replay_stable(tmp_path, monkeypatch):
    """Real init/next producers; simulated host metadata, no native J1 claim."""
    from taskplane import requirements, review_evidence, run_store, stage_migration, storage
    from taskplane.tests.test_r0001_phase_agents_spec import _registry

    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
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
        initialized = loop.init(ws, "Same Product goal", requirement_id=requirement["id"], by="human:simulated")
        assert not initialized.get("error"), initialized
        authority = initialized["_stage_native_root_authority"]
        artifacts = review_evidence.ArtifactStore(ws)
        def authorize(current):
            assert current["run_id"] == run_id
            assert loop._stage_native_init_authority(ws, requirement["id"], "human:simulated") == authority
        stage_migration.change_phase_routing(store, run_id, owner="agent-runtime",
            configuration={"definition_source":"agents/spec-phase-definitions.json",
                "definition_set_fingerprint":_registry().definition_set_fingerprint,
                "knowledge_reference":artifacts.put("phase-knowledge", {"facts":[]}),
                "candidate_fingerprint":review_evidence.content_fingerprint({"revision":authority["target_revision"]}),
                "target_revision":authority["target_revision"], "host_kind":"simulated",
                "host_version":"supporting-test", "output_paths":{"product":{"requirement":"specs/requirement.json"}}},
            expected_previous=None, expected_revision=store.load(run_id)["revision"],
            operation_id="select-product-runtime", validate_authority=authorize)
        first = loop.next_action(ws)
        assert not first.get("error"), first
        names.append(first["task_name"])
        state = loop.load(ws)
        manifest = store.load(run_id)
        slot = Path(loop.tp.active_contract_path(ws, first["contract_bootstrap"]["task_slot"]))
        contract_bytes = slot.read_bytes()
        repeated = loop.next_action(ws)
        assert repeated["phase_runtime"]["reference"] == first["phase_runtime"]["reference"]
        assert repeated["phase_runtime"]["operation_id"] == first["phase_runtime"]["operation_id"]
        assert "task_name" not in repeated  # Pickup is observation, not another launch.
        assert loop.load(ws)["worker_dispatch_sequences"] == state["worker_dispatch_sequences"] == {"pm:pm":1}
        assert slot.read_bytes() == contract_bytes
        assert json.loads(contract_bytes)["worker_lifecycle"]["expected_task_name"] == first["task_name"]
        assert store.load(run_id) == manifest
        assert (Path(ws) / "README.md").read_bytes() == source
    assert names[0] != names[1], "fresh runs reused the same host-global first Product name"


def _initialize_real_new_run(tmp_path, monkeypatch, *, stage_kind="product",
                             stage_id=None,
                             goal="exercise the real stage loop",
                             parallel=False):
    from taskplane import requirements as reqs
    from taskplane.tests.test_stage_cross_host import (
        _real_loop_stage, _record_bootstrap_requirement)

    tmp_path.mkdir(parents=True, exist_ok=True)
    stage_id = stage_id or f"stage-{stage_kind}-loop-root"
    workspace, store, stage = _real_loop_stage(
        tmp_path, stage_kind=stage_kind, stage_id=stage_id)
    requirement = _record_bootstrap_requirement(workspace, ordinal=4)
    requirement = reqs.amend_requirement(
        str(workspace), requirement["id"],
        nfr={
            "reliability": "stage transitions fail closed without evidence",
            "security": "stage output stays inside governed storage",
            "architecture": "the immutable stage aggregate owns transitions",
        })
    assert requirement["id"] == stage["requirement"]["id"] == "R-0004"
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "cross-host-session")
    initialized = loop.init(
        str(workspace), goal, requirement_id=requirement["id"],
        parallel=parallel, by=stage["authority"]["actor"])
    assert "error" not in initialized, initialized
    return str(workspace), store, stage, initialized


@pytest.fixture
def collected_product_handoff(tmp_path, monkeypatch):
    """Actual producers/collector on a small Git target; host events simulated."""
    from taskplane import review_evidence, stage_migration
    from taskplane.tests import test_stage_cross_host as cross_host
    from taskplane.tests.test_r0001_j1_native import _supporting_pristine_phase_run
    from taskplane.tests.test_r0001_phase_cutover import _emit_host_hook, _host_event
    git = cross_host._git
    def with_python_input(workspace, *args):
        if args == ("add", "."):
            (workspace / "app.py").write_text("def greet(name):\n    return f'Hello {name}'\n")
        return git(workspace, *args)
    monkeypatch.setattr(cross_host, "_git", with_python_input)
    route = stage_migration.change_phase_routing
    def with_design_destination(*args, **kwargs):
        configuration = copy.deepcopy(kwargs["configuration"])
        configuration["output_paths"]["design"] = {
            "design": "design/contract.json", "test-strategy": "design/test-strategy.json"}
        configuration["output_paths"]["plan"] = {"plan-task":"plan/tasks.json"}
        return route(*args, **{**kwargs, "configuration": configuration})
    monkeypatch.setattr(stage_migration, "change_phase_routing", with_design_destination)
    record = cross_host._record_bootstrap_requirement
    def with_context(workspace):
        requirement = record(workspace)
        requirement = loop.reqs.amend_requirement(str(workspace), requirement["id"],
            context_files=["app.py"], nfr={"security":"retain exact authority",
                "architecture":"reuse existing owners", "reliability":"preserve accepted evidence"})
        loop.depgraph.link_requirement(str(workspace), requirement["id"], ["app.py"], kind="planned", replace=True)
        return requirement
    monkeypatch.setattr(cross_host, "_record_bootstrap_requirement", with_context)
    ws, store, run_id, requirement = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    requested = loop.next_action(ws)
    assert not requested.get("error"), requested
    assert _emit_host_hook(ws, requested, "SubagentStart", monkeypatch) == 0
    path = Path(ws) / requested["phase_runtime"]["outputs"]["requirement"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema":"taskplane.requirement/v1", "id":requirement["id"],
        "title":requirement["title"], "acceptance_criteria":requirement["acceptance"]}))
    stop = _host_event(ws, requested, "SubagentStop")
    slot = requested["contract_bootstrap"]["task_slot"]
    contract = loop.tp.load_json(loop.tp.active_contract_path(ws, slot))
    collected = loop.observe_phase_runtime_hook(ws, contract, stop)
    assert collected["status"] == "collected", collected
    loop.tp.terminalize_worker_contract(ws, stop, outcome="success", submission_status="not_required")
    completion = loop.next_action(ws)["phase_runtime"]["completion"]
    assert completion is not None
    artifacts = review_evidence.ArtifactStore(ws)
    material = artifacts.read(completion["preparation"])
    return ws, store, run_id, completion, material, artifacts


def test_collected_product_gate_preserves_signed_handoff_after_graph_refresh(collected_product_handoff, monkeypatch):
    import types
    ws, store, run_id, completion, material, artifacts = collected_product_handoff
    original = {key:artifacts.read(completion[key]) for key in ("preparation", "runtime_result", "runtime_receipt")}
    impact = artifacts.read(material["impact_reference"])
    # Only the producer's metadata clock advances; the public gate repeats the
    # same requirement edge update between its two existing verifier calls.
    monkeypatch.setattr(loop.depgraph, "time", types.SimpleNamespace(time=lambda: impact["graph"]["updated_at"] + 10))
    gated = loop.gate(ws, "pass")
    assert not gated.get("error"), gated
    assert gated["step"] == "design"
    assert {key:artifacts.read(completion[key]) for key in original} == original
    from taskplane import stage_migration
    retained = stage_migration.phase_records(store.load(run_id))
    assert any(row["operation"] == "phase_collect" and row["result"] == completion for row in retained.values())


def test_collected_product_uses_selected_successor_before_legacy_design_policy(collected_product_handoff):
    ws, _, _, completion, _, artifacts = collected_product_handoff
    assert loop.load(ws)["design_required"] is False
    gated = loop.gate(ws, "pass")
    assert not gated.get("error"), gated
    assert gated["step"] == "design"
    assert loop.load(ws)["design_required"] is True
    requested = loop.next_action(ws)
    assert not requested.get("error"), requested
    assert requested["role"] == "tp-design"
    assert requested["phase_runtime"]["status"] == "pending"
    material = artifacts.read(requested["phase_runtime"]["reference"])
    instructions = Path(requested["role_instructions"])
    assert instructions.is_file(), instructions
    assert instructions == Path(loop.__file__).resolve().parents[1] / "skills/tp-design/SKILL.md"
    assert hashlib.sha256(instructions.read_bytes()).hexdigest() == material["bindings"]["skill_content_fingerprint"]
    assert requested["role_marker"] == "taskplane-role:tp-design"
    assert material["envelope"]["task_name"] == requested["task_name"]
    assert artifacts.read(material["predecessor"]) == artifacts.read(completion["handoff"])
    assert [row["artifact_class"] for row in material["package"]] == ["requirement"]
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
def collected_zero_lens_design(collected_product_handoff, monkeypatch):
    """Real phase owners with authored test candidates and simulated host events."""
    from taskplane.tests.test_r0001_phase_cutover import _host_event
    from taskplane.tests.test_r0001_phase_agents_spec import _strategy, SELECTOR
    ws, store, run_id, _, _, artifacts = collected_product_handoff
    assert loop.gate(ws, "pass")["step"] == "design"
    action = loop.next_action(ws)
    assert not action.get("error"), action
    start = _host_event(ws, action, "SubagentStart")
    loop.tp.bind_worker_contract_event(ws, start)
    slot = action["contract_bootstrap"]["task_slot"]
    worker = loop.tp.load_json(loop.tp.active_contract_path(ws, slot))
    assert loop.observe_phase_runtime_hook(ws, worker, start)["status"] == "pending"
    state = loop.load(ws)
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
        "graph": {"baseline_fingerprint": state["design_graph_fingerprint"],
            "proposed_modules": source_modules, "proposed_edges": [],
            "depth_policy": {"local_depth": 1, "boundary_mode": "contract-only", "contract_depth": 1, "requirement_depth": 1},
            "dor": [{"check": "source exists", "evidence": "app.py"}],
            "dod": [{"check": "greeting preserved", "evidence": "acceptance test"}]},
        "acceptance_map": [{"criterion": criterion, "design_element": "app.greet", "validation": "regression",
            "tests": [SELECTOR]}
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
        "produced_by": action["task_name"], "self_attested": True,
        "content_fingerprint": loop._dc.design_content_fingerprint(ws, contract)}]
    (folder / "contract.json").write_text(json.dumps(contract))
    (folder / "test-strategy.json").write_text(json.dumps(_strategy()))
    assert loop._base_design_dod_errors(ws, state) == []
    assert loop._design_control_plane_errors(ws, state) == []
    slot = action["contract_bootstrap"]["task_slot"]
    worker = loop.tp.load_json(loop.tp.active_contract_path(ws, slot))
    stop = _host_event(ws, action, "SubagentStop")
    collected = loop.observe_phase_runtime_hook(ws, worker, stop)
    assert collected["status"] == "collected", collected
    loop.tp.terminalize_worker_contract(ws, stop, outcome="success", submission_status="not_required")
    completion = loop.next_action(ws)["phase_runtime"]["completion"]
    assert artifacts.read(completion["runtime_result"])["status"] == "accepted"
    assert "design_team_plan" not in loop.load(ws)
    return ws, store, run_id, artifacts, completion


@pytest.mark.parametrize("consolidated", [False, True], ids=["human-checkpoint", "consolidated"])
def test_stateless_design_gate_uses_collected_runtime_not_legacy_team(collected_zero_lens_design, monkeypatch, consolidated):
    ws, _, _, artifacts, completion = collected_zero_lens_design
    monkeypatch.setenv("TASKPLANE_CONSOLIDATED_FLOW", "1" if consolidated else "0")
    original = {key: artifacts.read(completion[key]) for key in
                ("preparation", "runtime_result", "runtime_receipt", "handoff")}
    stage = loop._phase_bridge_context(ws, loop.load(ws))["stage"]
    result = loop.gate(ws, "pass")
    assert not result.get("error"), result.get("dod", result)
    if consolidated:
        assert result["step"] == "plan"
    else:
        assert result["step"] == "design_approval"
        assert loop._phase_bridge_context(ws, loop.load(ws))["stage"] == stage
        approved = loop.approve(ws, by="human:fixture — approve greeting design")
        assert not approved.get("error"), approved.get("dod", approved)
        assert approved["step"] == "plan"
    assert {key: artifacts.read(completion[key]) for key in original} == original


def test_legacy_design_still_requires_team_despite_untrusted_phase_flags(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "disabled")
    errors = loop._design_dod_errors(str(tmp_path), {
        "step": "design", "phase_runtime": {"status": "collected"},
        "phase_definition": {"phase_id": "design"}})
    assert "Design lens team plan is missing" in errors


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


def test_flat_script_handoff_uses_canonical_runtime_artifacts(tmp_path, monkeypatch):
    import importlib
    import sys
    from taskplane.tests import test_r0001_phase_agents_spec as spec
    monkeypatch.syspath_prepend(str(Path(loop.__file__).parent))
    monkeypatch.delitem(sys.modules, "loop", raising=False)
    flat = importlib.import_module("loop")
    assert flat is not loop and flat.__package__ == ""
    store, registry, state, _, plan, _ = spec._journey(tmp_path)
    package, _ = spec._consume(store, registry, state, plan)
    restored = flat.consume_phase_handoff(store, plan, registry=registry, phase_id="build",
        expected_authority_revision=package.authority_revision,
        expected_authority_fingerprint=package.authority_fingerprint,
        expected_run_id=package.run_id, expected_candidate_fingerprint=package.candidate_fingerprint)
    assert [row.projection() for row in restored.artifacts] == [row.projection() for row in package.artifacts]
    assert restored.artifacts == package.artifacts


@pytest.mark.parametrize("case", ["one-owner", "missing-cross-task-contract", "ambiguous-owner"])
def test_dependency_plan_uses_actual_scoped_ownership(tmp_path, case):
    from taskplane import plan_topology
    for folder, text in (("provider", "VALUE = 1\n"),
            ("consumer", "from provider import value\n"), ("unrelated", "VALUE = 2\n")):
        destination = tmp_path / folder / "value.py"
        destination.parent.mkdir()
        destination.write_text(text)
    for arguments in (("init",), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "source")):
        subprocess.run(["git", *arguments], cwd=tmp_path, check=True, capture_output=True)
    tasks = [{"id":"T1", "scope":["provider/value.py", "consumer/value.py"], "modules":["*"], "deps":[]}]
    if case == "missing-cross-task-contract":
        tasks[0]["scope"] = ["provider/value.py"]
        tasks.append({"id":"T2", "scope":["consumer/value.py"], "deps":["T1"]})
    if case == "ambiguous-owner":
        tasks.append({"id":"T2", "scope":["consumer/value.py"], "deps":["T1"]})
    binding = {key:"a" * 64 for key in ("run_id", "candidate_fingerprint", "requirement_fingerprint", "design_fingerprint", "plan_fingerprint")}
    if case != "one-owner":
        with pytest.raises(ValueError, match="missing seam contract" if case == "missing-cross-task-contract" else "ambiguous Plan"):
            plan_topology.produce_dependency_plan(str(tmp_path), binding=binding, seam_contracts=[], plan={"tasks":tasks})
        return
    result = plan_topology.produce_dependency_plan(str(tmp_path), binding=binding, seam_contracts=[], plan={"tasks":tasks})
    assert result["decomposition"]["tasks"] == [{"id":"T1", "nodes":["consumer", "provider"], "deps":[]}]
    assert result["seam-manifest"]["seams"] == []
    assert result["source-coverage"]["complete"] is True


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


@pytest.mark.parametrize("preparation_failure", [False, True], ids=["clean", "issued-before-output-refusal"])
def test_public_plan_reconcile_validates_current_json_and_gates_without_old_stop(collected_zero_lens_design, tmp_path, monkeypatch, capsys, record_property, preparation_failure):
    """Actual public owners, simulated provider metadata; no native J1 claim."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import sys
    import runpy
    from taskplane import design_host_transport
    from taskplane.tests.test_r0001_phase_cutover import _emit_host_hook
    from taskplane.tests.test_r0001_phase_agents_spec import SELECTOR, _strategy
    ws, store, run_id, artifacts, design_completion = collected_zero_lens_design
    original_design = artifacts.read(design_completion["runtime_receipt"])
    monkeypatch.setenv("TASKPLANE_CONSOLIDATED_FLOW", "0")
    assert loop.gate(ws, "pass")["step"] == "design_approval"
    assert loop.approve(ws, by="human:fixture — approve greeting Design")["step"] == "plan"
    # The incident run has an attributable advisory usage policy. Unknown
    # provider token usage stays unknown; this is not a synthetic zero count.
    policy = loop.resolve(ws, "limits-advisory", by=loop.load(ws)["_stage_native_root_authority"]["actor"])
    assert not policy.get("error"), policy
    state = loop.load(ws)
    requirement = loop.reqs.get_requirement(ws, state["requirement_id"])
    plan = {"requirement":requirement["id"], "delivery_mode":"build", "tasks":[{"id":"T1", "task":"Preserve greeting",
        "scope":["app.py"], "modules":["app"], "deps":[], "contracts":[],
        "criteria":requirement["acceptance"], "acceptance_refs":requirement["acceptance"],
        "tests":"python3 -m pytest -q " + SELECTOR,
        "test_contract":{"changed_producers":["app.py"]},
        "test_strategy_authority":{"schema":"taskplane.plan-test-strategy-reference/v1", "path":"design/test-strategy.json",
            "strategy_fingerprint":_strategy()["contract_fingerprint_sha256"], "criterion_ids":["AC-T11"],
            "changed_producer_ids":["spec-package"]}}]}
    # Existing candidate files are inputs, not accepted outputs. Preparation
    # observes the destination before the simulated worker reserializes it.
    destination = Path(ws) / "plan/tasks.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(plan))
    (destination.parent / "plan.md").write_text("# Plan\nPreserve the greeting with its existing test.\n")
    loop.depgraph.scan(ws, decompose=True)
    action = loop.next_action(ws)
    assert not action.get("error"), action
    child = "11111111-1111-4111-8111-111111111111"
    assert _emit_host_hook(ws, action, "SubagentStart", monkeypatch, agent_id=child) == 0
    material = artifacts.read(action["phase_runtime"]["reference"])
    nonce = design_host_transport.phase_nonce_source(loop.tp, ws, run_id, existing_only=True)
    issued = nonce.recover(material["nonce_bindings"])
    start = nonce._read_phase_hook(issued, material["nonce_bindings"], "start")
    assert nonce.terminal_hooks(issued, material["nonce_bindings"]) is None
    home = tmp_path / "provider"
    day = datetime.fromtimestamp(start["observed_at"], timezone.utc).strftime("%Y/%m/%d")
    path = home / "sessions" / day / f"rollout-current-{child}.jsonl"
    path.parent.mkdir(parents=True)
    owner = start["owner"]
    agent_path = "/root/" + owner["task_name"]
    metadata = {"type":"session_meta", "payload":{"id":child, "session_id":owner["session_id"],
        "parent_thread_id":owner["session_id"], "cwd":ws, "agent_path":agent_path,
        "source":{"subagent":{"thread_spawn":{"parent_thread_id":owner["session_id"], "agent_path":agent_path}}}}}
    complete = {"type":"event_msg", "payload":{"type":"task_complete", "turn_id":start["turn_id"],
        "started_at":start["observed_at"]-2, "completed_at":loop.time.time(), "duration_ms":2000}}
    path.write_text(json.dumps(metadata) + "\n" + json.dumps(complete) + "\n")
    monkeypatch.setenv("CODEX_HOME", str(home))
    destination.write_text(json.dumps(plan, sort_keys=True, indent=4) + "\n")
    def no_host_effect(*args, **kwargs): pytest.fail("current validation must not dispatch or replay Stop")
    observe_phase_hook = design_host_transport.observe_phase_hook
    monkeypatch.setattr(design_host_transport, "observe_phase_hook", no_host_effect)
    # Execute the real script entry, retaining its flat ``import loop``.
    # Aliasing that module to the package hides cross-module dataclass bugs.
    monkeypatch.syspath_prepend(str(Path(loop.__file__).parent))
    monkeypatch.delitem(sys.modules, "loop", raising=False)
    capsys.readouterr()
    args = SimpleNamespace(workspace=ws, loop_action="resolve", decision="reconcile", phase_operation=action["phase_runtime"]["operation_id"])
    script = str(Path(loop.__file__).with_name("tp.py"))
    monkeypatch.setattr(sys, "argv", [script, "loop", "--workspace", ws, "resolve", "reconcile",
        "--phase-operation", args.phase_operation])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(script, run_name="__main__")
    return_code = exited.value.code
    assert sys.modules["loop"] is not loop and sys.modules["loop"].__package__ == ""
    recovered = json.loads(capsys.readouterr().out)
    original_impact = artifacts.read(material["impact_reference"])
    _, current_impact = loop._phase_bridge_freshness(ws, material["signing_scope"])
    def changed_keys(old, new, prefix=""):
        if isinstance(old, dict) and isinstance(new, dict):
            return [item for key in old.keys() | new.keys()
                for item in changed_keys(old.get(key), new.get(key), prefix + "." + key)]
        return [prefix] if old != new else []
    if return_code:
        pytest.fail(str(recovered.get("error")) + "\n" + "\n".join(sorted(changed_keys(original_impact, current_impact))))
    assert recovered.get("validation") == "current-semantic", {key:value for key,value in recovered.items()
        if key not in {"dashboard", "dashboard_snapshot", "artifacts", "dispatch_audit", "enforcement"}}
    assert recovered["completion_source"] == "codex-task-complete"
    assert not recovered["worker_released"]
    assert loop.resolve(ws, "reconcile", phase_operation=args.phase_operation)["replay"] is True
    assert nonce.terminal_hooks(issued, material["nonce_bindings"]) is None
    collected_plan_stage = loop._phase_bridge_context(ws, loop.load(ws))["stage"]
    result = loop.gate(ws, "pass")
    assert not result.get("error"), result.get("dor", result)
    assert result["step"] == "plan_approval"
    assert loop._phase_bridge_context(ws, loop.load(ws))["stage"] == collected_plan_stage
    approved = loop.approve(ws, by="human:fixture — approve the scoped greeting Plan")
    assert not approved.get("error"), approved.get("dor", approved)
    assert approved["step"] == "execute"
    assert loop._phase_bridge_context(ws, loop.load(ws))["stage"]["stage_kind"] == "build"
    from taskplane import plan_topology
    assert loop.load(ws)["delivery_mode_receipt"]["plan_fingerprint"] == plan_topology.canonical_plan_fingerprint(plan)
    assert artifacts.read(design_completion["runtime_receipt"]) == original_design
    assert json.loads(destination.read_text()) == plan  # No harness field insertion.
    import stage_migration
    assert len([row for row in stage_migration.phase_records(store.load(run_id)).values()
        if row["operation"] == "phase_collect" and row["operation_id"] == args.phase_operation + "-complete"]) == 1
    # Continue through the actual root hook and direct-script Build command.
    # Neither the final root telemetry owner nor Build preparation is mocked.
    onboarding = Path(ws) / ".codex/hooks.json"
    onboarding.parent.mkdir(exist_ok=True)
    onboarding.write_text('{"hooks":{"SessionStart":[]}}\n')
    original_onboarding = onboarding.read_bytes()
    from taskplane import tp as cli
    from taskplane.tests.test_native_root_session import _write_root
    root_transcript = tmp_path / "current-root-counter.jsonl"
    _write_root(root_transcript, total=40_000, sequence=1, session_id=owner["session_id"])
    root_event = {"cwd":ws, "session_id":owner["session_id"], "turn_id":"root-build-turn",
        "transcript_path":str(root_transcript)}
    cli.host_caps.record_runtime_hook_receipt(cli.tp.store_home(), hook_path="native", event=root_event)
    cli._observe_active_loop_orchestrator(ws, root_event)
    assert loop.load(ws)["root_hygiene"]["status"] == "open"
    root_trace = [json.loads(line) for line in (Path(cli.tp.tp_dir(ws)) / "trace.jsonl").read_text().splitlines()]
    root_failures = [{key:row.get(key) for key in ("stage", "failure_code")}
        for row in root_trace if row.get("event") == "native_orchestrator_meter_unavailable"]
    record_property("unmocked_root_hook_failures", json.dumps(root_failures))
    abandoned = {}
    if preparation_failure:
        from taskplane import producer_observation
        original_issue = producer_observation.AttemptNonceSource.issue
        def historical_output_refusal(source, bindings):
            issued = original_issue(source, bindings)
            abandoned.update(source=source, bindings=dict(bindings), receipt=dict(issued.receipt))
            raise ValueError("phase output paths do not match declared outputs")
        # Historical 10cc ordering: real nonce issuance preceded output-map
        # validation. Public activation failure owns the actual cancellation.
        with monkeypatch.context() as patch:
            patch.setattr(producer_observation.AttemptNonceSource, "issue", historical_output_refusal)
            patch.setattr(sys, "argv", [script, "loop", "--workspace", ws, "next"])
            capsys.readouterr()
            with pytest.raises(SystemExit):
                runpy.run_path(script, run_name="__main__")
            failed = json.loads(capsys.readouterr().out)
        assert "phase output paths do not match declared outputs" in failed["error"]
        assert not failed.get("recovery_errors")
        assert abandoned["source"].effect_state(abandoned["bindings"]) == "issued"
        assert abandoned["bindings"]["operation_id"] == "phase-attempt-" + loop._phase_bridge_context(ws, loop.load(ws))["stage"]["fingerprint"][:32]
        assert abandoned["bindings"]["operation_id"] not in stage_migration.phase_records(store.load(run_id))
        assert not loop.load(ws).get("attempt_lease")
        cancelled_before = loop.tp.dispatch_intent_census(ws, run_id)["cancelled_intent_ids"]
        assert abandoned["bindings"]["attempt_id"] in cancelled_before
    monkeypatch.setattr(sys, "argv", [script, "loop", "--workspace", ws, "next"])
    capsys.readouterr()
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(script, run_name="__main__")
    build = json.loads(capsys.readouterr().out)
    assert exited.value.code == 0, build.get("error")
    assert build["execution_mode"] == "stateless-phase"
    assert build["phase_definition"]["id"] == "build"
    assert build["phase_runtime"]["status"] == "pending"
    assert build["contract_bootstrap"]["task_slot"]
    assert build["phase_runtime"]["outputs"] == {}
    quality = build["build_quality"]
    assert quality["binding_at_dispatch"] == loop._build_quality_binding(
        ws, loop.load(ws), loop.load(ws)["tasks"][0], "execute")
    assert quality["strategy_authority"] == loop.load(ws)["tasks"][0]["test_strategy_authority_receipt"]
    assert quality["task"] == loop.load(ws)["tasks"][0]["id"]
    assert "build_quality.begin_receipt" in build["instruction"]
    assert "loop build-quality" in build["instruction"]
    assert "Before loop submit pass" in build["instruction"]
    assert {row["artifact_class"]:row["owner"] for row in build["phase_outputs"]} == {
        "stage":"runtime", "realized-conformance":"runtime"}
    build_material = artifacts.read(build["phase_runtime"]["reference"])
    assert build_material["signing_scope"] == ["app.py"]
    assert build_material["domain"]["lease"]["effect_scope"] == ["workspace:app.py"]
    if preparation_failure:
        assert build_material["bindings"]["operation_id"] != abandoned["bindings"]["operation_id"]
        assert abandoned["source"].recover(abandoned["bindings"]).receipt == abandoned["receipt"]
        assert abandoned["source"].effect_state(abandoned["bindings"]) == "issued"
        assert loop.tp.dispatch_intent_census(ws, run_id)["cancelled_intent_ids"] == cancelled_before
    pending = loop.next_action(ws)
    assert pending["phase_runtime"]["reference"] == build["phase_runtime"]["reference"]
    assert "task_name" not in pending
    monkeypatch.setattr(design_host_transport, "observe_phase_hook", observe_phase_hook)
    assert _emit_host_hook(ws, build, "SubagentStart", monkeypatch, agent_id="simulated-build-worker") == 0
    target = Path(ws) / "app.py"
    target.write_text(target.read_text() + "\n# Scoped worker implementation.\n")
    slot = build["contract_bootstrap"]["task_slot"]
    with monkeypatch.context() as patch:
        patch.setenv("TASKPLANE_TASK", slot)
        submitted = loop.submit(ws, "pass", "Scoped simulated producer completed")
    assert submitted.get("submitted") is True, submitted.get("error")
    submission = copy.deepcopy(loop.load(ws)["_submission"])
    # Actual-shaped native Stop has no internal effect-lease payload. The
    # existing hook must durably preserve it even when collection is interrupted.
    if preparation_failure:
        with monkeypatch.context() as patch:
            def interrupted_collection(*args, **kwargs):
                raise ValueError("historical collection interruption")
            patch.setattr(loop, "_collect_phase_attempt", interrupted_collection)
            assert _emit_host_hook(ws, build, "SubagentStop", monkeypatch, agent_id="simulated-build-worker") == 2
    else:
        assert _emit_host_hook(ws, build, "SubagentStop", monkeypatch, agent_id="simulated-build-worker") == 0
    source = design_host_transport.phase_nonce_source(loop.tp, ws, run_id, existing_only=True)
    issued = source.recover(build_material["nonce_bindings"])
    hooks = source.phase_hooks(issued, build_material["nonce_bindings"])
    assert hooks[1]["lease_terminal"] is None
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", [script, "loop", "--workspace", ws, "resolve", "reconcile",
        "--phase-operation", build["phase_runtime"]["operation_id"]])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(script, run_name="__main__")
    collected = json.loads(capsys.readouterr().out)
    assert exited.value.code == 0, collected.get("error")
    assert collected["status"] == "collected"
    completion = collected["receipt"]["result"]
    assert artifacts.read(completion["runtime_receipt"])["payload"]["status"] == "accepted"
    assert source.phase_hooks(issued, build_material["nonce_bindings"]) == hooks
    assert loop.load(ws)["_submission"] == submission
    assert onboarding.read_bytes() == original_onboarding
    assert loop.load(ws)["attempt_lease"]["released"] is True
    assert not Path(loop.tp.active_contract_path(ws, slot)).exists()
    with monkeypatch.context() as patch:
        patch.setenv("TASKPLANE_TASK", slot)
        with pytest.raises(loop.tp.StateError):
            loop.tp.load_active(ws)  # Existing missing-slot refusal closes writes.
    archived = loop.tp.released_worker_contract(ws, slot)
    assert archived["worker_lifecycle"]["terminal"]["submission_status"] == "phase-terminal:" + hooks[1]["claim"]
    assert archived["phase_runtime"] == build["phase_runtime"]
    signed = artifacts.read(completion["runtime_receipt"])
    current_material = artifacts.read(completion["validation"]["signing_material"])
    assert current_material["original_preparation"] == build["phase_runtime"]["reference"]
    assert signed["freshness"] == current_material["freshness"]
    before_replay = store.load(run_id)
    assert loop.resolve(ws, "reconcile", phase_operation=build["phase_runtime"]["operation_id"])["replay"] is True
    assert store.load(run_id) == before_replay


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
    context = {"stage":{"stage_kind":"build", "fingerprint":"f" * 64},
        "run_id":original["run_id"], "store":SimpleNamespace(load=lambda _: {})}
    monkeypatch.setattr(loop, "_phase_bridge_authorize", lambda *args: None)
    monkeypatch.setattr(loop, "load", lambda _: {"attempt_lease":{"status":"active"}} if case == "effect-lease" else {})
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


@pytest.fixture
def historical_product_plan_correction(collected_product_handoff, monkeypatch):
    from taskplane import stage_entities
    ws, store, run_id, completion, _, artifacts = collected_product_handoff
    context = loop._phase_bridge_context
    def historical_optional_design(*args, **kwargs):
        value = context(*args, **kwargs)
        if value and value["stage"]["stage_kind"] == "product":
            # Reproduce the old optional-Design successor choice only. The
            # admitted registry and signed Product artifacts remain original.
            value = {**value, "definition": {**value["definition"],
                "edge_conditions": [{"condition": "accepted", "successor": "plan"}]}}
        return value
    with monkeypatch.context() as patch:
        patch.setattr(loop, "_phase_bridge_context", historical_optional_design)
        assert loop.gate(ws, "pass")["step"] == "plan"
    refused = loop.next_action(ws)
    assert "not a declared predecessor" in refused["error"]
    current = store.load(run_id)
    plan = context(ws, loop.load(ws))["stage"]
    product = loop._indexed_stage(store, current, run_id, plan["predecessor_stage_ids"][0])
    closed = loop.stage_command(ws, "terminalize", {
        "schema": "taskplane.stage-command/v1", "run_id": run_id,
        "stage_id": plan["stage_id"], "expected_head_fingerprint": plan["fingerprint"],
        "expected_revision": current["revision"], "operation_id": "close-incorrect-plan",
        "outcome": "closed", "actor": plan["authority"]["actor"],
        "terminalized_at": plan["created_at"], "reason_code": "incorrect_successor",
        "reason": "Historical optional-Design routing skipped required predecessor",
        "authority": plan["authority"]})
    assert not closed.get("error"), closed
    design = stage_entities.create_stage(run_id=run_id, stage_id="correct-design",
        requirement=product["requirement"], design=product["design"], stage_kind="design",
        parent_stage_ids=[], predecessor_stage_ids=[product["stage_id"]],
        input_manifest_ref=plan["input_manifest_ref"], execution_root_id="execution-correct-design",
        deliverables=["design-contract"], budget=product["budget"], dependencies=[],
        contracts=product["contracts"], authority=product["authority"], created_at=plan["created_at"],
        selected_artifacts=artifacts.read(completion["handoff"])["selected_artifacts"])
    request = {"schema": "taskplane.stage-command/v1", "stage": design,
        "expected_revision": store.load(run_id)["revision"], "operation_id": "start-correct-design",
        "expected_predecessor_fingerprints": {product["stage_id"]: product["fingerprint"]},
        "foreground": True, "authority": design["authority"]}
    return ws, store, run_id, completion, artifacts, request


@pytest.mark.parametrize("interrupted", [False, True], ids=["normal", "after-stage-commit"])
def test_public_stage_start_projects_corrected_design_and_replays(historical_product_plan_correction, monkeypatch, interrupted):
    ws, store, run_id, completion, artifacts, request = historical_product_plan_correction
    original = {key: artifacts.read(completion[key]) for key in
                ("preparation", "runtime_result", "runtime_receipt", "handoff")}
    if interrupted:
        def missed_projection(*args, **kwargs):
            raise OSError("interrupted singleton projection")
        with monkeypatch.context() as patch:
            patch.setattr(loop, "save", missed_projection)
            refused = loop.stage_command(ws, "start", request)
        assert "interrupted singleton projection" in refused["error"]
        assert loop.load(ws)["step"] == "plan"
        committed = store.load(run_id)
        assert "correct-design" in committed["stage_heads"]
    started = loop.stage_command(ws, "start", request)
    assert not started.get("error"), started
    if interrupted:
        assert store.load(run_id) == committed
    assert loop.load(ws)["step"] == "design"
    assert loop.load(ws)["design_required"] is True
    before = store.load(run_id)
    assert loop.stage_command(ws, "start", request) == started
    assert store.load(run_id) == before
    requested = loop.next_action(ws)
    assert not requested.get("error"), requested
    assert requested["role"] == "tp-design"
    assert requested["phase_runtime"]["status"] == "pending"
    assert {key: artifacts.read(completion[key]) for key in original} == original


@pytest.mark.parametrize("case", ["background", "stale-revision", "foreign-authority", "worker", "stale-foreground"])
def test_public_stage_start_projection_refuses_unrelated_changes(historical_product_plan_correction, monkeypatch, case):
    ws, store, run_id, _, _, request = historical_product_plan_correction
    before = copy.deepcopy(loop.load(ws))
    if case == "background":
        request["foreground"] = False
    elif case == "stale-revision":
        request["expected_revision"] -= 1
    elif case == "foreign-authority":
        request["authority"] = {**request["authority"], "actor": "human:foreign"}
    elif case == "worker":
        monkeypatch.setattr(loop.tp, "_active_worker_contracts", lambda ws: [("foreign-slot", {})])
    else:
        project = loop._project_bound_stage_start
        def close_before_projection(ws, store, lifecycle, stage, **kwargs):
            if kwargs.get("receipt") is None:
                return project(ws, store, lifecycle, stage, **kwargs)
            closed = loop.stage_command(ws, "terminalize", {
                "schema": "taskplane.stage-command/v1", "run_id": run_id,
                "stage_id": stage["stage_id"], "expected_head_fingerprint": stage["fingerprint"],
                "expected_revision": store.load(run_id)["revision"], "operation_id": "close-before-projection",
                "outcome": "closed", "actor": stage["authority"]["actor"],
                "terminalized_at": stage["created_at"], "reason_code": "superseded",
                "reason": "Current foreground changed before singleton projection",
                "authority": stage["authority"]})
            assert not closed.get("error"), closed
            return project(ws, store, lifecycle, stage, **kwargs)
        monkeypatch.setattr(loop, "_project_bound_stage_start", close_before_projection)
    manifest_before = store.load(run_id)
    result = loop.stage_command(ws, "start", request)
    if case == "background":
        assert not result.get("error"), result
    else:
        assert result.get("error"), result
    if case in {"stale-revision", "foreign-authority", "worker"}:
        assert store.load(run_id) == manifest_before
    assert loop.load(ws) == before


@pytest.mark.parametrize("changed", ["source", "graph-content", "graph-quality", "impact", "policy", "binding", "recorded-impact", "signature"])
def test_product_handoff_freshness_still_rejects_content_changes(collected_product_handoff, monkeypatch, changed):
    ws, _, _, completion, material, artifacts = collected_product_handoff
    signed = artifacts.read(completion["runtime_receipt"])
    original_material = copy.deepcopy(material)
    if changed == "source":
        (Path(ws) / "README.md").write_text("Changed implementation source\n")
        subprocess.run(["git", "add", "README.md"], cwd=ws, check=True)
        subprocess.run(["git", "commit", "-qm", "changed source"], cwd=ws, check=True)
    elif changed in {"graph-content", "graph-quality", "impact", "policy"}:
        producer = loop.depgraph.impact
        def changed_impact(*args, **kwargs):
            value = copy.deepcopy(producer(*args, **kwargs))
            if changed == "graph-content": value["graph"]["content_fingerprint"] = "f" * 64
            elif changed == "graph-quality": value["graph"].setdefault("graph_scan_quality", {})["degraded"] = True
            elif changed == "impact": value["total_impacted"] += 1
            else: value["policy"]["local_depth"] += 1
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


def _start_real_stage_loop(tmp_path, monkeypatch, *, stage_kind="product",
                           stage_id=None, goal="exercise the real stage loop"):
    workspace, store, stage, _initialized = _initialize_real_new_run(
        tmp_path, monkeypatch, stage_kind=stage_kind, stage_id=stage_id,
        goal=goal)
    started = loop.stage_command(str(workspace), "start", {
        "schema": "taskplane.stage-command/v1",
        "stage": stage,
        "expected_revision": 1,
        "operation_id": f"start-{stage['stage_id']}",
        "expected_predecessor_fingerprints": {},
        "foreground": True,
        "authority": stage["authority"],
        "declared_scope": {
            "scope_paths": ["README.md"],
            "out_of_scope_paths": ["taskplane/loop.py"],
        },
    })
    assert "error" not in started, started
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "enabled")
    return str(workspace), store, stage, started


def _successor_handoff(ws, store, receipt):
    successor_head = receipt["result"]["successor_head"]
    successor_id = successor_head["summary"]["stage_id"]
    manifest = store.load(successor_head["summary"]["run_id"])
    successor = store.read_stage_object(
        manifest["run_id"], manifest["stage_heads"][successor_id]["object"])
    _entities, lifecycle = loop._stage_lifecycle(
        ws, store, manifest, successor["authority"])
    handoff = loop._verified_stage_handoff(
        lifecycle, store, manifest, successor)
    return successor, handoff


def _handoff_payload_text(ws, handoff):
    from taskplane import review_evidence

    artifact_store = review_evidence.ArtifactStore(ws)
    payloads = [artifact_store.read(reference) for reference in
                handoff["selected_artifacts"] +
                handoff["evidence_references"]]
    return json.dumps(payloads, sort_keys=True)


def test_disabled_loop_stage_context_does_not_open_a_locator(
        monkeypatch) -> None:
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "disabled")
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator",
        lambda _ws: (_ for _ in ()).throw(AssertionError("locator opened")))

    assert loop._stage_loop_context("/repo") is None


def test_unmigrated_loop_stage_context_is_a_read_only_noop(monkeypatch) -> None:
    manifest = {"schema": "taskplane.run/v3", "run_id": "legacy", "revision": 9}
    before = copy.deepcopy(manifest)

    class Store:
        def load(self, _run_id):
            return manifest

    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "enabled")
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator",
        lambda _ws: {"run_id": "legacy", "home": "/run-store"})
    monkeypatch.setattr(loop, "_stage_store", lambda _ws, _run_id: Store())

    assert loop._stage_loop_context("/repo") is None
    assert manifest == before


def test_disabled_migrated_loop_refuses_mutation_without_state_change(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state_root = tmp_path / "state-root"
    monkeypatch.setattr(
        loop.tp, "external_store_root", lambda _ws: str(state_root))
    loop.init(ws, "preserve the migrated run")
    specs = tmp_path / "repo" / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text("migrated run\n", encoding="utf-8")
    before = copy.deepcopy(loop.load(ws))
    manifest = {
        "schema": "taskplane.run/v4", "run_id": "run-r0004",
        "revision": 4, "stage_heads": {}, "stage_operations": {},
        "active_stage_projection": {
            "active_stage_ids": [], "foreground_stage_id": None,
        },
    }

    class Store:
        @staticmethod
        def load(run_id):
            assert run_id == "run-r0004"
            return copy.deepcopy(manifest)

    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator",
        lambda _ws: {"run_id": "run-r0004", "home": "/run-store"})
    monkeypatch.setattr(loop, "_stage_store", lambda _ws, _run_id: Store())

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "error" in refused
    assert "disabled" in refused["error"]
    assert loop.load(ws) == before


@pytest.mark.parametrize("corruption", ["locator", "store"])
def test_corrupt_stage_locator_or_store_refuses_without_singleton_mutation(
        tmp_path, monkeypatch, corruption) -> None:
    ws = _workspace(tmp_path)
    state_root = tmp_path / "corrupt-state-root"
    monkeypatch.setattr(
        loop.tp, "external_store_root", lambda _ws: str(state_root))
    loop.init(ws, "preserve singleton on stage metadata corruption")
    specs = Path(ws) / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(
        "# Product\n\nDo not mutate through corrupt stage state.\n",
        encoding="utf-8")
    before = copy.deepcopy(loop.load(ws))
    state_path = Path(loop._loop_path(ws))
    before_bytes = state_path.read_bytes()
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)

    if corruption == "locator":
        monkeypatch.setattr(
            loop.runtime_storage, "load_workspace_locator",
            lambda _ws: (_ for _ in ()).throw(
                RuntimeError("corrupt stage locator")))
    else:
        monkeypatch.setattr(
            loop.runtime_storage, "load_workspace_locator",
            lambda _ws: {"run_id": "run-corrupt", "home": "/run-store"})

        class CorruptStore:
            @staticmethod
            def load(_run_id):
                raise RuntimeError("corrupt stage manifest")

        monkeypatch.setattr(
            loop, "_stage_store", lambda *_args: CorruptStore())

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "error" in refused
    assert "stage" in refused["error"].lower()
    assert "corrupt" in refused["error"].lower() or \
        "unavailable" in refused["error"].lower()
    assert loop.load(ws) == before
    assert state_path.read_bytes() == before_bytes


def test_new_run_can_start_after_normal_loop_init_and_replay_once(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, first = _start_real_stage_loop(
        tmp_path / "explicit-replay", monkeypatch,
        stage_id="stage-product-explicit-replay")
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    request = {
        "schema": "taskplane.stage-command/v1", "stage": stage,
        "expected_revision": 1,
        "operation_id": "start-stage-product-explicit-replay",
        "expected_predecessor_fingerprints": {}, "foreground": True,
        "authority": stage["authority"],
        "declared_scope": {
            "scope_paths": ["README.md"],
            "out_of_scope_paths": ["taskplane/loop.py"],
        },
    }
    second = loop.stage_command(ws, "start", request)

    assert second == first
    manifest = store.load(stage["run_id"])
    assert list(manifest["stage_operations"]).count(
        "start-stage-product-explicit-replay") == 1


def test_pristine_new_run_next_bootstraps_once_before_public_dispatch(
        tmp_path, monkeypatch) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement)

    worker_clock = iter(range(1_800_000_000, 1_800_001_000))
    monkeypatch.setattr(
        taskplane_lite._time, "time", lambda: next(worker_clock))
    observed = []
    real_dispatch = loop._stage_dispatch

    def observe_committed_root(store, lifecycle, receipt, stage, **kwargs):
        manifest = store.load(stage["run_id"])
        summary = manifest["stage_heads"][stage["stage_id"]]["summary"]
        assert manifest["schema"] == "taskplane.run/v4"
        assert summary["state"] == "active"
        assert manifest["stage_operations"][receipt["operation_id"]] == receipt
        observed.append(stage["stage_id"])
        return real_dispatch(
            store, lifecycle, receipt, stage, **kwargs)

    monkeypatch.setattr(loop, "_stage_dispatch", observe_committed_root)
    pristine = tmp_path / "pristine"
    pristine.mkdir()
    workspace, store, initial = _real_pristine_run(pristine)
    requirement = _record_bootstrap_requirement(workspace)
    ws = str(workspace)
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    initialized = loop.init(
        ws, "bootstrap Product without caller stage JSON", parallel=True,
        requirement_id=requirement["id"], by="human:vdemkiv")
    assert "error" not in initialized, initialized
    specs = workspace / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(
        "# Product\n\nBootstrap Product before any stage dispatch.\n",
        encoding="utf-8")
    refused_wave = loop.wave(ws)

    assert "first `loop next`" in refused_wave["error"]
    assert store.load(initial["run_id"])["schema"] == "taskplane.run/v3"
    assert initialized["_stage_native_new_run_pristine"] is True
    assert initialized["_stage_native_root_authority"]["actor"] == \
        "human:vdemkiv"

    first = loop.next_action.__wrapped__(ws)

    assert "error" not in first, first
    dispatch = first["stage_runtime_dispatch"]
    root_id = dispatch["startup"]["stage_id"]
    assert observed == [root_id]
    manifest = store.load(initial["run_id"])
    assert manifest["schema"] == "taskplane.run/v4"
    assert manifest["stage_heads"][root_id]["summary"]["state"] == "active"
    assert loop.load(ws)["_stage_run_binding"]["root_stage_id"] == root_id

    second = loop.next_action.__wrapped__(ws)

    assert "error" not in second, second
    assert second["stage_runtime_dispatch"]["startup"]["stage_id"] == root_id
    assert store.load(initial["run_id"])["active_stage_projection"][
        "active_stage_ids"] == [root_id]


def test_cold_plan_next_ignores_unselected_foreign_repo_inputs_and_replays_root(tmp_path, monkeypatch):
    from taskplane.tests.test_stage_cross_host import _real_pristine_run, _record_bootstrap_requirement
    workspace, store, initial = _real_pristine_run(tmp_path)
    foreign = {
        "design/contract.json":{"requirement":"R-0002", "summary":"foreign database migration"},
        "plan/tasks.json":{"requirement":"R-0002", "tasks":[{"id":"foreign", "scope":["foreign.py"],
            "tests":"foreign suite"}], "plan_route":{"selected":["architecture", "security", "testability", "cost-finops"]}},
    }
    for relative, value in foreign.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    subprocess.run(["git", "add", "design/contract.json", "plan/tasks.json"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "unrelated checked-in predecessor artifacts"], cwd=workspace, check=True)
    preserved = {relative:(workspace / relative).read_bytes() for relative in foreign}
    requirement = _record_bootstrap_requirement(workspace)
    requirement = loop.reqs.amend_requirement(str(workspace), requirement["id"], nfr={
        "security":"local source only", "architecture":"reuse existing owners"})
    assert loop.reqs.product_dor(requirement)["passed"]
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    (workspace / "current-spec.md").write_text("Current bounded requirement, not the checked-in predecessor Plan.")
    initialized = loop.init(str(workspace), "current bounded Plan", spec_path="current-spec.md",
        requirement_id=requirement["id"], by="human:vdemkiv")
    assert initialized["step"] == "plan", initialized
    # Stop at the mapper boundary after real root/handoff admission. No native
    # worker or fake applicability/acceptance receipt is needed for this test.
    observed = []
    def mapper(*args, **kwargs):
        observed.append(kwargs)
        raise ValueError("test stopped at current Plan mapper boundary")
    monkeypatch.setattr(loop, "_focused_stage_route", mapper)
    for _ in range(2):
        result = loop.next_action.__wrapped__(str(workspace))
        assert "current Plan mapper boundary" in result.get("error", ""), result
    assert len(observed) == 2
    for row in observed:
        assert row["stage"] == "plan"
        assert row["mandatory_lenses"] == ["architecture", "project-management", "testability"]
        assert row["maximum_lenses"] == 4
        assert row["evidence"]["approved_design"] == {}
        assert row["evidence"]["task_scopes"] == row["evidence"]["selectors"] == {}
        assert row["evidence"]["approved_product"]["id"] == requirement["id"]
        assert "foreign" not in json.dumps(row["evidence"])
    manifest = store.load(initial["run_id"])
    assert len(manifest["stage_heads"]) == 1
    context = loop._stage_loop_context(str(workspace), loop.load(str(workspace)))
    handoff = loop._verified_stage_handoff(context["lifecycle"], store, manifest, context["stage"])
    assert handoff["design"] is None and handoff["requirement"]["id"] == requirement["id"]
    assert {relative:(workspace / relative).read_bytes() for relative in foreign} == preserved


@pytest.fixture
def native_plan_lens_action(tmp_path, monkeypatch):
    from taskplane.tests.test_stage_cross_host import _real_pristine_run, _record_bootstrap_requirement
    workspace, store, initial = _real_pristine_run(tmp_path)
    requirement = _record_bootstrap_requirement(workspace)
    loop.reqs.amend_requirement(str(workspace), requirement["id"], nfr={
        "security":"review local trust boundaries", "architecture":"reuse current owners"})
    (workspace / "current-spec.md").write_text("Plan the current local change with independent quick reviews.")
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    # Simulated applicability provider; production policy still chooses its
    # canonical mandatory set and any fourth risk. No host completion here.
    monkeypatch.setattr(loop.lens_router, "route", lambda *a, **k:{"lenses":[], "context":{"status":"ready"}})
    initialized = loop.init(str(workspace), "current Plan with native quick lenses", spec_path="current-spec.md",
        requirement_id=requirement["id"], by="human:vdemkiv")
    assert initialized["step"] == "plan", initialized
    action = loop.next_action.__wrapped__(str(workspace))
    assert not action.get("error"), action
    return str(workspace), workspace, store, initial, action


def test_public_plan_action_emits_exact_native_lens_team_and_replays_once(native_plan_lens_action):
    ws, _, store, initial, action = native_plan_lens_action
    assert "plan_lens_dispatches" in action, "selected Plan lenses have no native contracts or dispatch briefs"
    workers = action["plan_lens_dispatches"]
    plan = action["plan_team_plan"]
    assert len(workers) in {3, 4}
    assert {row["lens"] for row in workers} == set(action["focused_route"]["dispatchable_selected"])
    assert len({row["task_name"] for row in workers}) == len({row["task_slot"] for row in workers}) == len(workers)
    for worker in workers:
        assert worker["output"] == f"plan/lenses/{worker['lens']}.json"
        assert worker["dispatch_intent"]["schema"] == "taskplane.plan-lens-dispatch-intent/v1"
        contract = loop.tp.load_json(loop.tp.active_contract_path(ws, worker["task_slot"]))
        assert contract["worker_lifecycle"]["stage"] == "plan-lens"
        assert contract["worker_lifecycle"]["expected_task_name"] == worker["task_name"]
        assert contract["write_allow"] == [worker["output"]]
        assert "plan_host_authority" in contract["worker_lifecycle"]
    before_queue = loop.tp._load_queue_strict(loop.tp._dispatch_path(ws, "expected_dispatch.json"))
    repeated = loop.next_action.__wrapped__(ws)
    assert repeated["plan_team_plan"] == plan
    lens_queue = lambda rows: [row for row in rows if row.get("kind") == "plan-lens"]
    assert lens_queue(loop.tp._load_queue_strict(loop.tp._dispatch_path(ws, "expected_dispatch.json"))) == lens_queue(before_queue)
    assert len(store.load(initial["run_id"])["stage_heads"]) == 1
    assert "phase_runtime" not in action


def _finish_plan_lenses(ws, workspace, action, *, usage="unavailable"):
    """Simulate only native dispatch/start/Stop; use real lifecycle owners."""
    from taskplane.tests.test_r0002_cross_host_journey import _digest
    plan = action["plan_team_plan"]
    events = []
    for index, worker in enumerate(plan["workers"]):
        expected = loop.tp.peek_expectation(ws, worker["task_name"], strict=True)
        loop.tp.record_design_dispatch_assignment_activity(ws, expected)
        loop.record_native_dispatch_observation(ws, expected=expected,
            native_task_name=worker["task_name"], observed_at=100 + index)
        assert loop.tp.commit_dispatch_verification(ws, worker["task_name"], worker["model"],
            expected, True, worker["reasoning_effort"], strict=True)
        event = {"cwd":ws, "session_id":"pristine-session", "agent_id":f"plan-child-{index}",
            "agent_type":worker["task_name"], "task_name":worker["task_name"], "turn_id":f"turn-{index}"}
        bound = loop.tp.bind_worker_contract_event(ws, event)
        loop.tp.record_design_worker_start_activity(ws, bound, event)
        material = {"schema":"taskplane.plan-lens-result/v1", "lens":worker["lens"],
            "worker_identity":worker["task_name"], "team_plan_fingerprint":plan["fingerprint"],
            "candidate_fingerprint":plan["candidate_fingerprint"], "outcome":"pass", "findings":[]}
        path = workspace / worker["output"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**material, "fingerprint":_digest(material)}))
        if usage == "measured":
            loop.record_observed_dispatch_usage(ws, task_id=worker["lens"],
                native_task_name=worker["task_name"], source_fingerprint="a" * 64,
                normalized_usage={"schema":loop.spend.USAGE_SCHEMA, "available":True,
                    "cached_input_tokens":60, "uncached_input_tokens":40, "output_tokens":10,
                    "raw_total_tokens":110, "reasoning_tokens":5})
            sealed = loop.finalize_observed_dispatch_usage(ws, task_id=worker["lens"],
                native_task_name=worker["task_name"], ended_at=110 + index, outcome="success")
            event["usage_reference"] = {"schema":"taskplane.native-dispatch-usage-reference/v1",
                "dispatch_receipt":sealed["receipt"]}
        elif usage != "missing":
            sealed = loop.finalize_observed_dispatch_usage(ws, task_id=worker["lens"],
                native_task_name=worker["task_name"], ended_at=110 + index, outcome="success",
                usage_unavailable=True, unavailable_reason="simulated host has no counter provider")
            assert sealed["status"] == "unavailable"
            assert sealed["binding"]["usage"] is None
        terminal = loop.tp.terminalize_worker_contract(ws, {**event, "outcome":"success"},
            outcome="success", submission_status="not_required")
        assert terminal
        events.append(event)
    return events


@pytest.mark.parametrize("started", [False, True])
def test_session_start_preserves_current_plan_lens_slots(native_plan_lens_action, started):
    ws, _, _, _, action = native_plan_lens_action
    worker = action["plan_lens_dispatches"][0]
    if started:
        loop.tp.bind_worker_contract_event(ws, {"cwd":ws, "session_id":"pristine-session",
            "agent_id":"current-plan-child", "agent_type":worker["task_name"],
            "task_name":worker["task_name"], "turn_id":"turn-plan"})
    paths = [Path(loop.tp.active_contract_path(ws, row["task_slot"])) for row in action["plan_lens_dispatches"]]
    before = {str(path):path.read_bytes() for path in paths}
    assert loop.tp.sweep_completed_worker_contracts(ws, loop_state=loop.load(ws)) == []
    assert {str(path):path.read_bytes() for path in paths} == before
    assert loop.resume(ws)["step"] == "plan"


@pytest.mark.parametrize("stage", ["design-lens", "unrecognized-worker"])
@pytest.mark.parametrize("started", [False, True])
def test_session_start_does_not_guess_lens_or_unknown_stage_completion(tmp_path, stage, started):
    from taskplane.tests.test_worker_contract_lifecycle import _active_worker, _event
    contract = _active_worker(tmp_path, stage=stage, task="architecture")
    if started:
        loop.tp.bind_worker_contract_event(str(tmp_path), _event(tmp_path), now=11)
    path = Path(loop.tp.active_contract_path(str(tmp_path), contract["task_slot"]))
    before = path.read_bytes()
    assert loop.tp.sweep_completed_worker_contracts(str(tmp_path), loop_state={"step":"design", "tasks":[]}) == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("receipt_kind", ["missing", "tampered", "native"])
def test_session_start_terminal_flag_requires_authenticated_receipt(tmp_path, receipt_kind):
    from taskplane.tests.test_worker_contract_lifecycle import _active_worker, _event
    ws = str(tmp_path)
    contract = _active_worker(tmp_path, stage="plan-lens", task="architecture")
    slot = contract["task_slot"]
    loop.tp.bind_worker_contract_event(ws, _event(tmp_path), now=11)
    if receipt_kind != "missing":
        loop.tp.record_worker_terminal(ws, slot, event=_event(tmp_path), outcome="failure",
            submission_status="not_required", now=12)
    path = Path(loop.tp.active_contract_path(ws, slot))
    current = json.loads(path.read_text())
    current["worker_lifecycle"]["status"] = "terminal"
    if receipt_kind == "tampered":
        current["worker_lifecycle"]["terminal"]["signature"] = "tampered"
    path.write_text(json.dumps(current))
    before = path.read_bytes()
    if receipt_kind == "native":
        released = loop.tp.sweep_completed_worker_contracts(ws, loop_state={"step":"plan"})
        assert len(released) == 1
        assert released[0]["outcome"] == "failure"
        assert loop.tp.released_worker_contract(ws, slot)["worker_lifecycle"]["terminal"]["authority"] == "host-lifecycle"
    else:
        with pytest.raises(loop.tp.StateError):
            loop.tp.sweep_completed_worker_contracts(ws, loop_state={"step":"plan"})
        assert path.read_bytes() == before


def _write_reviewed_plan(ws, workspace):
    requirement = loop.reqs.get_requirement(ws, loop.load(ws)["requirement_id"])
    tasks = {"tasks":[{"id":"t01", "scope":["README.md"], "tests":"python3 -c 'assert True'",
        "deps":[], "new_modules":["(root)"], "criteria":requirement["acceptance"], "acceptance_refs":requirement["acceptance"],
        "req":requirement["id"], "status":"pending"}]}
    (workspace / "plan" / "tasks.json").write_text(json.dumps(tasks))
    (workspace / "plan" / "plan.md").write_text("# Plan\n\nImplement the current bounded requirement.\n")


@pytest.mark.parametrize("usage", ["unavailable", "measured"])
def test_native_plan_results_collect_then_public_gate_with_unknown_usage(native_plan_lens_action, usage):
    ws, workspace, _, _, action = native_plan_lens_action
    refused = loop.gate.__wrapped__(ws, "pass")
    assert refused["error"] == "Plan lens collection is incomplete"
    _finish_plan_lenses(ws, workspace, action, usage=usage)
    assert loop._design_team_errors(ws, loop.load(ws), stage="plan") == []
    _write_reviewed_plan(ws, workspace)
    gated = loop.gate.__wrapped__(ws, "pass")
    assert not gated.get("error"), gated
    assert gated["step"] == "plan_approval"
    bindings = loop.load(ws)["dispatch_telemetry"]["bindings"]
    assert all((row["usage"] is None) == (usage == "unavailable") for row in bindings)


def test_plan_collection_does_not_mix_same_lens_from_design_team(native_plan_lens_action):
    from taskplane.tests.test_r0002_cross_host_journey import _worker, _plan, _complete_worker
    ws, workspace, _, _, action = native_plan_lens_action
    _finish_plan_lenses(ws, workspace, action)
    plan = action["plan_team_plan"]
    # Both teams use the incumbent whole-run artifact owner. This transport
    # fixture does not claim a Design stage approval or native acceptance.
    worker = _worker(plan["selected"][0], run_id=plan["run_id"], stage_id=plan["stage_instance_id"],
        candidate=plan["candidate_fingerprint"], settings=plan["settings_digest"])
    design = _plan([worker], run_id=plan["run_id"], stage_id=plan["stage_instance_id"],
        candidate=plan["candidate_fingerprint"], settings=plan["settings_digest"])
    state = loop.load(ws)
    authority = loop.tp.register_design_lens_dispatch_plan(ws, design,
        artifact_root=loop._run_artifact_root(ws, state), artifact_binding=state["run_artifact_binding"])
    _complete_worker(workspace, design, authority, worker, index=100)
    collected = loop.tp.validate_design_lens_dispatch_completion(ws, plan, plan["host_authority"])
    assert collected["valid"], collected
    # New Design bytes still invalidate the original Plan source binding.
    assert loop._design_team_errors(ws, loop.load(ws), stage="plan")


def test_plan_team_registration_interrupted_replay_preserves_bound_owner(native_plan_lens_action):
    ws, _, _, _, action = native_plan_lens_action
    worker = action["plan_lens_dispatches"][0]
    event = {"cwd":ws, "session_id":"pristine-session", "agent_id":"already-started-child",
        "agent_type":worker["task_name"], "task_name":worker["task_name"], "turn_id":"started-turn"}
    loop.tp.bind_worker_contract_event(ws, event)
    slot_path = Path(loop.tp.active_contract_path(ws, worker["task_slot"]))
    before = slot_path.read_bytes()
    with loop.mutate(ws) as state:
        state.pop("plan_team_plan")  # Simulate interruption before the loop's team write.
    repeated = loop.next_action.__wrapped__(ws)
    assert repeated.get("plan_team_plan") == action["plan_team_plan"], repeated
    assert slot_path.read_bytes() == before


@pytest.mark.parametrize("corruption", ["missing", "foreign", "stale", "duplicate", "route", "usage", "changes-required", "result-bytes", "requirement"])
def test_native_plan_collection_refuses_independent_corruption(native_plan_lens_action, corruption):
    from taskplane.tests.test_r0002_cross_host_journey import _digest
    ws, workspace, _, _, action = native_plan_lens_action
    _finish_plan_lenses(ws, workspace, action, usage="missing" if corruption == "usage" else "unavailable")
    worker = action["plan_lens_dispatches"][0]
    path = workspace / worker["output"]
    if corruption == "missing":
        path.unlink()
    elif corruption in {"foreign", "changes-required", "result-bytes"}:
        result = json.loads(path.read_text())
        if corruption == "result-bytes":
            result["findings"] = [{"summary":"Changed after actual terminal"}]
        else:
            result["worker_identity" if corruption == "foreign" else "outcome"] = "foreign-worker" if corruption == "foreign" else "changes-required"
        result["fingerprint"] = _digest({key:value for key,value in result.items() if key != "fingerprint"})
        path.write_text(json.dumps(result))
    elif corruption == "stale":
        (workspace / "README.md").write_text("Unreviewed changed source\n")
    elif corruption == "requirement":
        loop.reqs.amend_requirement(ws, loop.load(ws)["requirement_id"], nfr={"security":"new unreviewed boundary"})
    elif corruption in {"duplicate", "route"}:
        with loop.mutate(ws) as state:
            plan = state["plan_team_plan"]
            if corruption == "duplicate":
                plan["workers"].append(copy.deepcopy(plan["workers"][0]))
            else:
                plan["route_fingerprint"] = "f" * 64
            plan["fingerprint"] = _digest({key:value for key,value in plan.items() if key not in {"fingerprint", "host_authority"}})
    refused = loop.gate.__wrapped__(ws, "pass")
    assert refused.get("error") == "Plan lens collection is incomplete", refused
    assert loop.load(ws)["step"] == "plan"


@pytest.fixture
def current_focused_plan_inputs(tmp_path, monkeypatch):
    from taskplane import review_evidence
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "disabled")
    ws = _workspace(tmp_path)
    requirement = loop.reqs.record_requirement(ws, "Current input", functional=["bounded scope"], acceptance=["current evidence"])
    root = Path(ws)
    design = {"requirement":requirement["id"], "summary":"Current selected solution"}
    plan = {"requirement":requirement["id"], "tasks":[{"id":"t1", "req":requirement["id"],
        "scope":["README.md"], "tests":"true", "acceptance_refs":["current evidence"]}],
        "plan_route":{"selected":["architecture", "project-management", "testability", "security"]}}
    for relative, value in (("design/contract.json", design), ("plan/tasks.json", plan)):
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(value))
    (root / "design/design.md").write_text("Current Design narrative")
    state = {"step":"plan", "requirement_id":requirement["id"], "design_required":True,
        "design_fingerprint":loop._design_evidence_fingerprint(ws),
        "plan_fingerprint":review_evidence.content_fingerprint(plan)}
    return ws, root, state, design, plan


def test_current_bound_plan_selected_choices_do_not_replace_mandatory_settings(current_focused_plan_inputs):
    ws, _, state, design, plan = current_focused_plan_inputs
    evidence, mandatory = loop._focused_stage_evidence(ws, state, "plan")
    assert mandatory is None
    assert evidence["approved_design"]["summary"] == design["summary"]
    assert evidence["plan_route"] == plan["plan_route"]
    assert evidence["selectors"] == {"t1":"true"}
    assert evidence["task_to_ac_coverage"] == {"t1":["current evidence"]}
    catalog = {row["id"] for row in loop.lens_router.load_catalog()["lenses"]}
    policy = load_settings(environment={}).lenses.policy_for("plan", catalog_ids=catalog)
    assert list(policy.mandatory) == ["architecture", "project-management", "testability"]
    assert policy.max_count == 4


@pytest.mark.parametrize("kind", ["design", "plan"])
@pytest.mark.parametrize("damage", ["missing", "corrupt", "stale", "foreign", "symlink"])
def test_bound_plan_inputs_refuse_bad_provenance(current_focused_plan_inputs, kind, damage):
    from taskplane import review_evidence
    ws, root, state, _, _ = current_focused_plan_inputs
    path = root / ("design/contract.json" if kind == "design" else "plan/tasks.json")
    if damage == "missing": path.unlink()
    elif damage == "corrupt": path.write_text("{not JSON")
    elif damage == "symlink":
        other = root / "aliased.json"
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
    else:
        value = json.loads(path.read_text())
        value["requirement" if damage == "foreign" else "summary"] = "R-9999" if damage == "foreign" else "changed"
        path.write_text(json.dumps(value))
        if damage == "foreign":
            # A matching content hash must not excuse a foreign requirement.
            state[kind + "_fingerprint"] = (loop._design_evidence_fingerprint(ws, value) if kind == "design"
                else review_evidence.content_fingerprint(value))
    before = _content_inventory(root)
    with pytest.raises((ValueError, OSError)):
        loop._focused_stage_evidence(ws, state, "plan")
    assert _content_inventory(root) == before


@pytest.mark.parametrize("damage", [None, "selected-missing", "selected-corrupt", "requirement-stale", "foreign-design"])
def test_plan_consumes_only_verified_current_design_handoff(tmp_path, monkeypatch, damage):
    from taskplane.tests.test_stage_cross_host import _real_pristine_run, _record_bootstrap_requirement
    workspace, store, _ = _real_pristine_run(tmp_path)
    ws = str(workspace)
    requirement = _record_bootstrap_requirement(workspace)
    (workspace / "current-spec.md").write_text("Current Design input")
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    state = loop.init(ws, "current Design into Plan", spec_path="current-spec.md", design=True,
        requirement_id=requirement["id"], by="human:vdemkiv")
    assert state["step"] == "design", state
    loop._stage_bootstrap_pristine_root(ws, state)
    state = loop.load(ws)
    design_dir = workspace / "design"
    design_dir.mkdir()
    design = {"requirement":"R-9999" if damage == "foreign-design" else requirement["id"],
        "summary":"Selected immutable Design"}
    (design_dir / "contract.json").write_text(json.dumps(design))
    (design_dir / "design.md").write_text("Selected narrative")
    # Exercise real output/handoff owners, not a native Design acceptance claim.
    completion = loop._stage_loop_gate_completion(ws, state, step="design", outcome="pass")
    receipt = loop._stage_loop_transition(ws, state, from_step="design", to_step="plan", completion=completion)
    _, handoff = _successor_handoff(ws, store, receipt)
    state["step"] = "plan"
    loop.save(ws, state)
    context = loop._stage_loop_context(ws, state)
    if damage in {"selected-missing", "selected-corrupt"}:
        reference = handoff["selected_artifacts"][0]
        path = Path(context["lifecycle"]._artifact_store()._path(reference["kind"], reference["fingerprint"]))
        if damage == "selected-missing": path.unlink()
        else: path.write_text("corrupt retained selected artifact")
    elif damage == "requirement-stale":
        loop.reqs.amend_requirement(ws, requirement["id"], functional=["unrelated changed requirement"])
    # The source filename is not selection authority. Its unrelated later bytes
    # must neither substitute for nor corrupt the immutable handoff's content.
    (design_dir / "contract.json").write_text('{"requirement":"R-8888","summary":"unselected repository content"}')
    source_before = _content_inventory(design_dir)
    if damage:
        with pytest.raises((ValueError, OSError)):
            loop._focused_stage_evidence(ws, state, "plan")
    else:
        evidence, mandatory = loop._focused_stage_evidence(ws, state, "plan")
        assert evidence["approved_design"]["summary"] == "Selected immutable Design"
        assert evidence["task_scopes"] == {} and mandatory is None
    assert _content_inventory(design_dir) == source_before


def test_new_run_refuses_unmarked_existing_singleton(
        tmp_path, monkeypatch) -> None:
    from taskplane.tests.test_stage_cross_host import _real_loop_stage

    unmarked_root = tmp_path / "unmarked"
    unmarked_root.mkdir()
    unmarked_ws, unmarked_store, unmarked_stage = _real_loop_stage(
        unmarked_root, stage_kind="product",
        stage_id="stage-product-unmarked-root")
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    loop.init(str(unmarked_ws), "pre-existing singleton")
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")

    refused = loop.stage_command(str(unmarked_ws), "start", {
        "schema": "taskplane.stage-command/v1", "stage": unmarked_stage,
        "expected_revision": 1, "operation_id": "start-too-late",
        "expected_predecessor_fingerprints": {}, "foreground": True,
        "authority": unmarked_stage["authority"],
    })

    assert refused["enabled"] is False
    assert "existing singleton" in refused["error"] or \
        "pristine" in refused["error"]
    assert unmarked_store.load(unmarked_stage["run_id"])["schema"] == \
        "taskplane.run/v3"


def test_dispatch_uses_one_deterministic_verified_resume_attempt(
        monkeypatch) -> None:
    calls = []
    stage = {
        "run_id": "run-r0004", "stage_id": "stage-build-001",
        "fingerprint": "f" * 64, "stage_kind": "build",
        "selected_artifacts": [],
    }

    class Lifecycle:
        def resume_stage(self, run_id, **kwargs):
            calls.append((run_id, kwargs))
            return {"operation": "resume_stage", "result": {
                "attempt_id": kwargs["attempt_id"]}}

    context = {
        "store": object(), "manifest": {"revision": 7},
        "lifecycle": Lifecycle(), "stage": stage,
    }
    monkeypatch.setattr(
        loop, "_stage_loop_context", lambda _ws, *_a, **_k: context)
    monkeypatch.setattr(
        loop, "_stage_dispatch",
        lambda _store, _lifecycle, receipt, current, **kwargs: {
            "schema": "taskplane.stage-dispatch/v1",
            "receipt": receipt["operation"],
            "stage": current["stage_id"],
            "attempt": kwargs["attempt_id"],
            "scope": kwargs["declared_scope"],
        })
    state = {"step": "execute"}
    scope = {"scope_paths": ["taskplane/loop.py"],
             "out_of_scope_paths": []}

    first = loop._stage_loop_dispatch(
        "/repo", state, slot="t06-loop", declared_scope=scope)
    second = loop._stage_loop_dispatch(
        "/repo", state, slot="t06-loop", declared_scope=scope)

    assert first == second
    assert first["schema"] == "taskplane.stage-dispatch/v1"
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][1]["operation_id"].startswith("loop-dispatch-")
    assert calls[0][1]["attempt_id"].startswith("attempt-")


def test_same_stage_kind_transition_does_not_mutate_stage_store(
        monkeypatch) -> None:
    monkeypatch.setattr(
        loop, "_stage_loop_context",
        lambda _ws: (_ for _ in ()).throw(AssertionError("store opened")))

    assert loop._stage_loop_transition(
        "/repo", {"step": "design_approval"},
        from_step="design", to_step="design_approval") is None


def test_terminal_transition_replays_the_existing_operation(monkeypatch) -> None:
    class StageEntities:
        @staticmethod
        def request_fingerprint(_value):
            return "a" * 64

    receipt = {"schema": "taskplane.stage-operation-receipt/v1",
               "operation_id": "loop-transition-" + "a" * 32}
    context = {
        "run_id": "run-r0004", "store": object(), "stage": None,
        "lifecycle": None, "stage_entities": StageEntities,
        "manifest": {"stage_operations": {
            receipt["operation_id"]: receipt}},
    }
    monkeypatch.setattr(
        loop, "_stage_loop_context", lambda _ws, *_a, **_k: context)
    monkeypatch.setattr(loop.tp, "verify_stage_receipt", lambda value: value)

    assert loop._stage_loop_transition(
        "/repo", {"step": "done"},
        from_step="retro", to_step="done") is receipt


def test_terminal_transition_refuses_predecessor_input_as_completion_evidence(
        monkeypatch) -> None:
    stage = {
        "run_id": "run-r0004", "stage_id": "stage-retro-001",
        "fingerprint": "f" * 64, "stage_kind": "retro",
        "deliverables": ["retrospective"],
        "authority": {"actor": "human:owner"},
        "created_at": "2026-08-21T20:00:00Z",
    }

    class StageEntities:
        @staticmethod
        def request_fingerprint(_value):
            return "a" * 64

    class Lifecycle:
        @staticmethod
        def terminalize(*_args, **_kwargs):
            raise AssertionError("predecessor input evidence was reused")

    context = {
        "run_id": stage["run_id"], "store": object(), "stage": stage,
        "lifecycle": Lifecycle(), "stage_entities": StageEntities,
        "manifest": {"revision": 7, "stage_operations": {}},
    }
    predecessor_handoff = {
        "evidence_references": [{
            "schema": "taskplane.artifact-reference/v1",
            "kind": "input-evidence", "fingerprint": "e" * 64,
        }],
        "authorization": {"authorized_at": "2026-08-21T19:00:00Z"},
    }
    monkeypatch.setattr(
        loop, "_stage_loop_context", lambda _ws, *_a, **_k: context)
    monkeypatch.setattr(
        loop, "_verified_stage_handoff", lambda *_a: predecessor_handoff)

    with pytest.raises(ValueError, match="completion"):
        loop._stage_loop_transition(
            "/repo", {"step": "done"},
            from_step="retro", to_step="done")


def test_selection_stage_failure_rolls_back_the_legacy_choice(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "choose one bounded variant", parallel=True)
    state.update({
        "step": "selection", "ab": True,
        "baseline": loop.tp.git_head(ws),
        "tasks": [
            {"id": "variant-a", "variant": "A", "scope": ["a/**"],
             "status": "passed"},
            {"id": "variant-b", "variant": "B", "scope": ["b/**"],
             "status": "passed"},
        ],
    })
    loop.save(ws, state)
    before = copy.deepcopy(loop.load(ws))
    monkeypatch.setattr(loop, "reconcile_authority_effects", lambda _ws: {})
    monkeypatch.setattr(loop, "status", lambda _ws: {})
    monkeypatch.setattr(
        loop.authority_engine, "build_selection",
        lambda *_a, **_k: {"authorized": True, "reasons": []})

    def refuse(*_args, **kwargs):
        assert kwargs == {"from_step": "selection", "to_step": "em"}
        raise RuntimeError("selection stage refused")

    monkeypatch.setattr(loop, "_stage_loop_transition", refuse)

    result = loop.select(ws, "A")

    assert "stage-native" in result["error"]
    assert loop.load(ws) == before


@pytest.mark.parametrize(
    ("decision", "to_step"), [("retry", "fix"), ("abort", "failed")])
def test_resolve_stage_failure_rolls_back_retry_or_abort(
        tmp_path, monkeypatch, decision, to_step) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "resolve one bounded escalation")
    state.update({
        "step": "escalated", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "failed",
            "fix_cycles": 3,
        }],
    })
    loop.save(ws, state)
    before = copy.deepcopy(loop.load(ws))
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "status", lambda _ws: {})

    def refuse(*_args, **kwargs):
        assert kwargs == {"from_step": "escalated", "to_step": to_step}
        raise RuntimeError("recovery stage refused")

    monkeypatch.setattr(loop, "_stage_loop_transition", refuse)

    result = loop.resolve(ws, decision)

    assert "stage-native" in result["error"]
    assert loop.load(ws) == before


def test_replan_stage_failure_preserves_the_frozen_plan(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "replan one bounded task")
    state.update({
        "step": "execute", "baseline": loop.tp.git_head(ws),
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "running",
        }],
    })
    loop.save(ws, state)
    before = copy.deepcopy(loop.load(ws))
    monkeypatch.setattr(loop.tp, "clear", lambda _ws: None)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop.kb, "record_decision", lambda *_a, **_k: {})

    def refuse(*_args, **kwargs):
        assert kwargs == {"from_step": "execute", "to_step": "plan"}
        raise RuntimeError("replan stage refused")

    monkeypatch.setattr(loop, "_stage_loop_transition", refuse)

    result = loop.replan(ws, by="human:owner", reason="invalid task graph")

    assert "stage-native" in result["error"]
    assert loop.load(ws) == before


def test_retro_attaches_one_idempotent_terminal_receipt(monkeypatch) -> None:
    receipt = {"operation": "terminalize", "operation_id": "retro-done"}
    monkeypatch.setattr(
        loop.retro_engine, "run", lambda *_args, **_kwargs: {"goal": "done"})
    monkeypatch.setattr(loop, "load", lambda _ws: {"step": "done"})
    calls = []

    def transition(*_args, **kwargs):
        calls.append(kwargs)
        return receipt

    monkeypatch.setattr(loop, "_stage_loop_transition", transition)

    first = loop.retro("/repo")
    second = loop.retro("/repo")

    assert first["stage_transition"] == receipt
    assert second == first
    assert calls == [
        {"from_step": "retro", "to_step": "done"},
        {"from_step": "retro", "to_step": "done"},
    ]


def test_next_action_attaches_stage_runtime_dispatch(tmp_path, monkeypatch) \
        -> None:
    ws = _workspace(tmp_path)
    loop.init(ws, "define the bounded handoff")
    marker = {"schema": "taskplane.stage-dispatch/v1", "startup": {}}
    monkeypatch.setattr(loop, "_stage_loop_dispatch", lambda *_a, **_k: marker)

    result = loop.next_action.__wrapped__(ws)

    assert result["step"] == "pm"
    assert result["stage_runtime_dispatch"] is marker
    assert Path(result["role_instructions"]).name == "tp-product.md"


def test_wave_emits_native_intent_without_stage_runtime_dispatch(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "build the bounded handoff", spec_path="spec.md",
                      parallel=True)
    state.update({
        "step": "execute",
        "tasks": [{"id": "t01", "scope": ["README.md"],
                   "tests": "true", "deps": [], "status": "pending"}],
    })
    loop.save(ws, state)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("native delivery must not use StageLifecycle")

    monkeypatch.setattr(loop, "_stage_loop_dispatch", forbidden)
    monkeypatch.setattr(loop, "_stage_loop_wave_dispatches", forbidden)

    authority = open_delivery_root(ws)
    result = loop.wave(ws, root_observation_authority=authority)

    assert len(result["wave"]) == 1
    assert result["wave"][0]["dispatch_intent"]["intent_id"]
    assert "stage_runtime_dispatch" not in result["wave"][0]
    assert result["wait_invocation"]["outstanding_members"] == ["t01"]
    assert not (loop.load(ws) or {}).get("_stage_bindings")


def test_parallel_wave_emits_one_native_set_without_execution_roots(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "dispatch two independent roots", parallel=True)
    state.update({
        "step": "execute", "current_task": 0,
        "tasks": [
            {"id": "t01", "scope": ["a/**"], "tests": "true",
             "deps": [], "status": "pending"},
            {"id": "t02", "scope": ["b/**"], "tests": "true",
             "deps": [], "status": "pending"},
        ],
    })
    loop.save(ws, state)

    monkeypatch.setattr(
        loop, "_stage_loop_wave_dispatches",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("Taskplane child-stage scheduler was invoked")))

    authority = open_delivery_root(ws)
    result = loop.wave(ws, root_observation_authority=authority)

    assert [entry["task"]["id"] for entry in result["wave"]] == [
        "t01", "t02"]
    assert len({entry["dispatch_intent"]["intent_id"]
                for entry in result["wave"]}) == 2
    assert result["wait_invocation"]["outstanding_members"] == [
        "t01", "t02"]
    assert result["held"] == []
    encoded = json.dumps(result, sort_keys=True).lower()
    assert "stage_runtime_dispatch" not in encoded
    assert "execution_root" not in encoded
    assert "tranche" not in encoded
    assert not (loop.load(ws) or {}).get("_stage_bindings")


def test_parallel_wave_honors_configured_build_concurrency(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    state = loop.init(ws, "dispatch two independent roots", parallel=True)
    state.update({
        "step": "execute", "current_task": 0,
        "tasks": [
            {"id": "t01", "scope": ["a/**"], "tests": "true",
             "deps": [], "status": "pending"},
            {"id": "t02", "scope": ["b/**"], "tests": "true",
             "deps": [], "status": "pending"},
        ],
    })
    loop.save(ws, state)
    effective = load_settings(environment={})
    capped = replace(
        effective, build=replace(effective.build, concurrency=1))
    monkeypatch.setattr(
        loop.operational_settings, "load_settings", lambda **_kwargs: capped)

    authority = open_delivery_root(ws)
    result = loop.wave(ws, root_observation_authority=authority)

    assert [entry["task"]["id"] for entry in result["wave"]] == ["t01"]
    assert result["held"] == [{
        "task": "t02",
        "reason": "configured build concurrency cap — next wave",
        "shared_owner": "settings",
    }]


def test_real_wave_recovers_task_bindings_after_post_split_crash(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "split-recovery", monkeypatch, stage_kind="build",
        stage_id="stage-build-split-parent")
    state = loop.load(ws)
    ready = [
        {"id": "t01", "scope": ["a/**"], "tests": "true",
         "deps": [], "status": "pending"},
        {"id": "t02", "scope": ["b/**"], "tests": "true",
         "deps": [], "status": "pending"},
    ]
    state.update({
        "step": "execute", "parallel": True, "current_task": 0,
        "tasks": ready,
    })
    loop.save(ws, state)
    real_mutate = loop.mutate

    @contextlib.contextmanager
    def crash_before_binding_commit(_workspace):
        raise RuntimeError("crash after split before singleton binding")
        yield

    monkeypatch.setattr(loop, "mutate", crash_before_binding_commit)
    with pytest.raises(RuntimeError, match="after split"):
        loop._stage_loop_wave_dispatches(ws, state, ready)
    split = store.load(stage["run_id"])
    assert len(split["active_stage_projection"]["active_stage_ids"]) == 2
    assert "_stage_bindings" not in loop.load(ws)

    monkeypatch.setattr(loop, "mutate", real_mutate)
    recovered = loop._stage_loop_wave_dispatches(
        ws, loop.load(ws), ready)

    assert set(recovered) == {"t01", "t02"}
    bindings = loop.load(ws)["_stage_bindings"]
    child_ids = {bindings[task_id]["build"] for task_id in recovered}
    assert len(child_ids) == 2
    assert child_ids == set(
        store.load(stage["run_id"])["active_stage_projection"][
            "active_stage_ids"])
    assert {
        dispatch["startup"]["stage_id"] for dispatch in recovered.values()
    } == child_ids


def test_gate_rolls_back_on_stage_failure_then_returns_transition_receipt(
        tmp_path, monkeypatch) -> None:
    ws = _workspace(tmp_path)
    loop.init(ws, "define the bounded handoff")
    specs = tmp_path / "repo" / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text("bounded handoff\n", encoding="utf-8")
    monkeypatch.setattr(
        loop, "_stage_loop_transition",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("stage refused")))

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "stage-native loop transition failed closed" in refused["error"]
    assert loop.load(ws)["step"] == "pm"

    receipt = {"operation": "terminalize_and_start",
               "operation_id": "pm-to-plan"}
    monkeypatch.setattr(
        loop, "_stage_loop_transition", lambda *_a, **_k: receipt)
    accepted = loop.gate.__wrapped__(ws, "pass")

    assert accepted["stage_transition"] is receipt
    assert loop.load(ws)["step"] == "plan"


def test_real_product_gate_and_plan_approval_seal_exact_outputs(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "gate-approve", monkeypatch,
        stage_id="stage-product-gate-root")
    spec_path = "specs/spec.md"
    spec = "# Product\n\nShip the bounded stage journey.\n"
    specs = Path(ws) / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(spec, encoding="utf-8")

    gated = loop.gate.__wrapped__(ws, "pass")

    assert "error" not in gated, gated
    assert gated["step"] == "plan"
    assert gated["stage_transition"]["operation"] == \
        "terminalize_and_start"
    _plan_stage, product_handoff = _successor_handoff(
        ws, store, gated["stage_transition"])
    product_payloads = _handoff_payload_text(ws, product_handoff)
    assert spec_path in product_payloads
    assert hashlib.sha256(spec.encode()).hexdigest() in product_payloads

    plan_dir = Path(ws) / "plan"
    plan_dir.mkdir()
    plan_text = "# Plan\n\nImplement the bounded handoff.\n"
    tasks_value = {"tasks": [{
        "id": "t01", "scope": ["README.md"], "tests": "true",
        "deps": [], "status": "pending",
    }]}
    (plan_dir / "plan.md").write_text(plan_text, encoding="utf-8")
    (plan_dir / "tasks.json").write_text(
        json.dumps(tasks_value, sort_keys=True) + "\n", encoding="utf-8")
    state = loop.load(ws)
    state.update({
        "step": "plan_approval", "tasks": tasks_value["tasks"],
        "graph_dor": {"ready": True, "blockers": []},
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop, "_design_current_errors", lambda *_a: [])
    monkeypatch.setattr(loop, "_refinement_report", lambda *_a: [])
    monkeypatch.setattr(loop.tp, "plan_task_id_refusal", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "_consolidated_enabled", lambda: False)
    monkeypatch.setattr(loop.kb, "record_decision", lambda *_a, **_k: {})

    approved = loop.approve(ws, by="human:owner")

    assert "error" not in approved, approved
    assert approved["step"] == "execute"
    assert approved["stage_transition"]["operation"] == \
        "terminalize_and_start"
    build_stage, plan_handoff = _successor_handoff(
        ws, store, approved["stage_transition"])
    plan_payloads = _handoff_payload_text(ws, plan_handoff)
    assert build_stage["stage_kind"] == "build"
    for path, content in (
            ("plan/plan.md", plan_text),
            ("plan/tasks.json", json.dumps(tasks_value, sort_keys=True) + "\n")):
        assert path in plan_payloads
        assert hashlib.sha256(content.encode()).hexdigest() in plan_payloads


@pytest.mark.parametrize(
    ("stage_kind", "from_step", "to_step", "outputs"), [
        ("design", "design", "plan", {
            "design/design.md": "# Design\n\nA bounded immutable stage.\n",
            "design/contract.json": "{\"schema\":\"test-design\"}\n",
        }),
    ])
def test_real_stage_completion_seals_design_outputs(
        tmp_path, monkeypatch, stage_kind, from_step, to_step, outputs) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / stage_kind, monkeypatch, stage_kind=stage_kind,
        stage_id=f"stage-{stage_kind}-output-root")
    workspace = Path(ws)
    for relative, content in outputs.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    state = loop.load(ws)
    state["step"] = from_step
    loop.save(ws, state)

    completion = loop._stage_loop_gate_completion(
        ws, state, step=from_step, outcome="pass")
    receipt = loop._stage_loop_transition(
        ws, state, from_step=from_step, to_step=to_step,
        completion=completion)

    assert receipt["operation"] == "terminalize_and_start"
    successor, handoff = _successor_handoff(ws, store, receipt)
    assert successor["stage_kind"] == "plan"
    payloads = _handoff_payload_text(ws, handoff)
    for relative, content in outputs.items():
        assert relative in payloads
        assert hashlib.sha256(content.encode()).hexdigest() in payloads


def test_real_build_completion_propagates_exact_target_commit_and_outputs(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "build-output", monkeypatch, stage_kind="build",
        stage_id="stage-build-output-root")
    workspace = Path(ws)
    build_text = "stage journey\nreal build output\n"
    (workspace / "README.md").write_text(build_text, encoding="utf-8")
    target_commit = loop.tp.git_head(ws)
    state = loop.load(ws)
    state.update({
        "step": "execute", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "tests": "true",
            "deps": [], "status": "running", "target_commit": target_commit,
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(
        loop.runtime_eval, "guide_loop",
        lambda *_a, **_k: {"status": "on_path", "recovered": False})
    submitted = loop.submit(ws, "pass")
    assert "error" not in submitted, submitted
    state = loop.load(ws)

    completion = loop._stage_loop_gate_completion(
        ws, state, step="execute", outcome="pass",
        submission=submitted["submission"])
    receipt = loop._stage_loop_transition(
        ws, state, from_step="execute", to_step="evaluate",
        completion=completion)

    assert receipt["operation"] == "terminalize_and_start"
    successor, handoff = _successor_handoff(ws, store, receipt)
    assert successor["stage_kind"] == "evaluate"
    assert handoff["target"] is not None
    assert handoff["commit"] is not None
    assert handoff["commit"]["sha"] == target_commit
    assert handoff["commit"]["target_fingerprint"] == \
        handoff["target"]["fingerprint"]
    payloads = _handoff_payload_text(ws, handoff)
    assert "README.md" in payloads
    assert hashlib.sha256(build_text.encode()).hexdigest() in payloads
    assert stage["authority"]["target_revision"] == target_commit


def test_retro_retries_sealed_report_after_real_terminalization_failure(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "retro-retry", monkeypatch, stage_kind="retro",
        stage_id="stage-retro-retry-root")
    state = loop.load(ws)
    state["step"] = "retro"
    loop.save(ws, state)
    report = {
        "goal": state["goal"],
        "graph_true_up": {"changed": False},
        "evaluator_summary": loop.retro_engine.evaluator_summary([]),
    }
    computations = []

    def sealed_retro(workspace, *, load_state, mutate_state, **_kwargs):
        current = load_state(workspace)
        if (current.get("retro") or {}).get("status") == "complete":
            return report
        computations.append("computed")
        with mutate_state(workspace) as locked:
            locked["retro"] = {
                "id": "retro-001", "status": "complete", "report": report,
            }
            locked["step"] = "done"
        return report

    real_lifecycle_factory = loop._stage_lifecycle
    terminal_attempts = []

    class FailOnceLifecycle:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def terminalize(self, *args, **kwargs):
            terminal_attempts.append(kwargs["operation_id"])
            if len(terminal_attempts) == 1:
                raise RuntimeError("simulated terminal store interruption")
            return self._wrapped.terminalize(*args, **kwargs)

    def lifecycle_factory(*args, **kwargs):
        entities, lifecycle = real_lifecycle_factory(*args, **kwargs)
        return entities, FailOnceLifecycle(lifecycle)

    monkeypatch.setattr(loop.retro_engine, "run", sealed_retro)
    monkeypatch.setattr(loop, "_stage_lifecycle", lifecycle_factory)

    refused = loop.retro(ws)
    prepared = loop.load(ws)

    assert "stage-native" in refused["error"]
    assert prepared["step"] == "retro"
    assert prepared["_retro_terminal_step"] == "done"
    assert prepared["retro"]["status"] == "complete"
    assert prepared["retro"]["report"] == report
    assert store.load(stage["run_id"])["active_stage_projection"][
        "active_stage_ids"] == [stage["stage_id"]]

    completed = loop.retro(ws)

    assert "error" not in completed, completed
    assert completed["stage_transition"]["operation"] == "terminalize"
    assert loop.load(ws)["step"] == "done"
    assert "_retro_terminal_step" not in loop.load(ws)
    assert computations == ["computed"]
    assert len(terminal_attempts) == 2
    assert terminal_attempts[0] == terminal_attempts[1]
    assert store.load(stage["run_id"])["active_stage_projection"][
        "active_stage_ids"] == []


@pytest.mark.parametrize("mode", ["enabled", "disabled"])
def test_bound_v4_missing_locator_refuses_mutation_byte_identically(
        tmp_path, monkeypatch, mode) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / mode, monkeypatch,
        stage_id=f"stage-product-bound-{mode}")
    specs = Path(ws) / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(
        "# Product\n\nPreserve a bound v4 singleton.\n",
        encoding="utf-8")
    state = loop.load(ws)
    assert state["_stage_run_binding"]["run_schema"] == \
        "taskplane.run/v4"
    state_path = Path(loop._loop_path(ws))
    before = state_path.read_bytes()

    state_root = state_path.parents[2]
    monkeypatch.setattr(
        loop.tp, "external_store_root", lambda _ws: str(state_root))
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", mode)
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator", lambda _ws: None)

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "error" in refused
    assert "locator" in refused["error"].lower()
    assert state_path.read_bytes() == before


def test_selection_transition_seals_the_declared_control_output(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "selection-output", monkeypatch, stage_kind="evaluate",
        stage_id="stage-evaluate-selection-root")
    revision = loop.tp.git_head(ws)
    state = loop.load(ws)
    state.update({
        "step": "selection", "ab": True, "baseline": revision,
        "tasks": [
            {"id": "variant-a", "variant": "A", "scope": ["a/**"],
             "status": "passed"},
            {"id": "variant-b", "variant": "B", "scope": ["b/**"],
             "status": "passed"},
        ],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop, "reconcile_authority_effects", lambda _ws: {})
    monkeypatch.setattr(loop, "status", lambda _ws: {})
    monkeypatch.setattr(
        loop.authority_engine, "build_selection",
        lambda *_a, **_k: {"authorized": True, "reasons": []})

    selected = loop.select(ws, "A", note="ship the bounded winner")

    assert "error" not in selected, selected
    assert selected["stage_transition"]["operation"] == \
        "terminalize_and_start"
    successor, handoff = _successor_handoff(
        ws, store, selected["stage_transition"])
    assert successor["stage_kind"] == "engineering"
    payloads = _handoff_payload_text(ws, handoff)
    assert "taskplane.loop-selection-result/v1" in payloads
    assert "variant-a" in payloads
    assert "ship the bounded winner" in payloads


def test_resolve_transition_seals_the_declared_control_output(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "resolve-output", monkeypatch,
        stage_kind="engineering", stage_id="stage-engineering-resolve-root")
    state = loop.load(ws)
    evidence = {"selector": "public acceptance journey", "returncode": 1}
    product_failure = loop.failure_routing.route_failure_records([{
        "schema": "taskplane.failure-record/v1",
        "id": "failure-t01-product",
        "source": "evaluate",
        "stage": "evaluate",
        "repro": "public acceptance journey still fails",
        "evidence": evidence,
        "evidence_digest": loop.failure_routing.evidence_digest(evidence),
        "class": "product",
        "reason": "the current product behavior violates acceptance",
        "owner": "task:t01",
        "cluster": "acceptance",
        "route": "fix",
        "candidate": {"id": "t01@candidate", "fingerprint": "b" * 64},
    }])
    state.update({
        "step": "escalated", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "failed",
            "fix_cycles": 3, "failure_routing": product_failure,
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "status", lambda _ws: {})

    resolved = loop.resolve(ws, "retry")

    assert "error" not in resolved, resolved
    assert resolved["stage_transition"]["operation"] == \
        "terminalize_and_start"
    successor, handoff = _successor_handoff(
        ws, store, resolved["stage_transition"])
    assert successor["stage_kind"] == "build"
    payloads = _handoff_payload_text(ws, handoff)
    assert "taskplane.loop-resolution-result/v1" in payloads
    assert '"decision": "retry"' in payloads
    assert '"resulting_step": "fix"' in payloads


def test_unclassified_escalated_retry_cannot_enter_product_fix(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "resolve-unclassified", monkeypatch,
        stage_kind="engineering",
        stage_id="stage-engineering-resolve-unclassified")
    state = loop.load(ws)
    state.update({
        "step": "escalated", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "failed",
            "fix_cycles": 3,
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "status", lambda _ws: {})

    resolved = loop.resolve(ws, "retry")

    assert "error" not in resolved, resolved
    successor, handoff = _successor_handoff(
        ws, store, resolved["stage_transition"])
    assert successor["stage_kind"] == "evaluate"
    payloads = _handoff_payload_text(ws, handoff)
    assert '"resulting_step": "evaluate"' in payloads


def test_replan_transition_seals_the_declared_control_output(
        tmp_path, monkeypatch) -> None:
    ws, store, _stage, _started = _start_real_stage_loop(
        tmp_path / "replan-output", monkeypatch, stage_kind="build",
        stage_id="stage-build-replan-root")
    state = loop.load(ws)
    state.update({
        "step": "execute", "baseline": loop.tp.git_head(ws),
        "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "running",
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "clear", lambda _ws: None)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop.kb, "record_decision", lambda *_a, **_k: {})

    replanned = loop.replan(
        ws, by="human:owner", reason="replace the invalid task graph")

    assert "error" not in replanned, replanned
    manifest = store.load("run-cross-host-loop")
    receipts = [receipt for receipt in manifest["stage_operations"].values()
                if receipt.get("operation") == "terminalize_and_start"]
    assert len(receipts) == 1
    successor, handoff = _successor_handoff(ws, store, receipts[0])
    assert successor["stage_kind"] == "plan"
    payloads = _handoff_payload_text(ws, handoff)
    assert "taskplane.loop-replan-result/v1" in payloads
    assert "replace the invalid task graph" in payloads
    assert '"to_step": "plan"' in payloads


@pytest.mark.parametrize(
    "source_case", ["absolute", "outside", "intermediate-symlink"])
def test_stage_output_sealing_rejects_untrusted_sources_without_capture(
        tmp_path, monkeypatch, source_case) -> None:
    from taskplane import review_evidence

    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / source_case, monkeypatch, stage_kind="engineering",
        stage_id=f"stage-engineering-hostile-{source_case}")
    workspace = Path(ws)
    if source_case == "absolute":
        source = str(workspace / "README.md")
    elif source_case == "outside":
        outside = workspace.parent / "outside.txt"
        outside.write_text("outside stage output\n", encoding="utf-8")
        source = "../outside.txt"
    else:
        outside = workspace.parent / "linked-output"
        outside.mkdir()
        (outside / "secret.txt").write_text(
            "symlinked stage output\n", encoding="utf-8")
        (workspace / "linked").symlink_to(outside, target_is_directory=True)
        source = "linked/secret.txt"

    state = loop.load(ws)
    state["step"] = "em"
    loop.save(ws, state)
    artifact_store = review_evidence.ArtifactStore(ws)
    before = artifact_store.references("stage-output")
    manifest_before = copy.deepcopy(store.load(stage["run_id"]))
    completion = loop._stage_loop_decision_completion(
        ws, schema="taskplane.loop-test-decision/v1",
        step="em", outcome="approved", result={"approved": True})
    completion["_stage_output"]["sources"] = [{
        "path": source, "required": True,
    }]

    with pytest.raises(ValueError):
        loop._stage_loop_transition(
            ws, state, from_step="em", to_step="retro",
            completion=completion)

    assert artifact_store.references("stage-output") == before
    assert store.load(stage["run_id"]) == manifest_before


def test_replan_transition_identity_includes_each_predecessor_head(
        tmp_path, monkeypatch) -> None:
    ws, _store, _stage, _started = _start_real_stage_loop(
        tmp_path / "replan-heads", monkeypatch, stage_kind="build",
        stage_id="stage-build-replan-head-one")
    state = loop.load(ws)
    state.update({
        "step": "execute", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "tests": "true",
            "deps": [], "status": "running",
        }],
    })
    loop.save(ws, state)
    replan_completion = loop._stage_loop_decision_completion(
        ws, schema="taskplane.loop-replan-result/v1",
        step="execute", outcome="replan",
        result={"from_step": "execute", "to_step": "plan",
                "by": "human:owner", "reason": "replace invalid graph"})

    first = loop._stage_loop_transition(
        ws, state, from_step="execute", to_step="plan",
        completion=replan_completion, force=True)
    state.update({"step": "plan", "tasks": None, "current_task": 0})
    loop.save(ws, state)

    plan_dir = Path(ws) / "plan"
    plan_dir.mkdir()
    (plan_dir / "plan.md").write_text(
        "# Plan\n\nTry the corrected graph.\n", encoding="utf-8")
    tasks = [{
        "id": "t01", "scope": ["README.md"], "tests": "true",
        "deps": [], "status": "running",
    }]
    (plan_dir / "tasks.json").write_text(
        json.dumps({"tasks": tasks}, sort_keys=True) + "\n",
        encoding="utf-8")
    state.update({"step": "plan", "tasks": tasks, "current_task": 0})
    loop.save(ws, state)
    plan_completion = loop._stage_loop_gate_completion(
        ws, state, step="plan", outcome="pass")
    advanced = loop._stage_loop_transition(
        ws, state, from_step="plan", to_step="execute",
        completion=plan_completion)
    state["step"] = "execute"
    loop.save(ws, state)

    second = loop._stage_loop_transition(
        ws, state, from_step="execute", to_step="plan",
        completion=replan_completion, force=True)

    assert first["operation_id"] != second["operation_id"]
    assert first["result"]["predecessor_head"]["summary"]["stage_id"] == \
        "stage-build-replan-head-one"
    assert second["result"]["predecessor_head"]["summary"]["stage_id"] == \
        advanced["result"]["successor_head"]["summary"]["stage_id"]


def test_partial_wave_split_retry_reuses_children_and_resumes_only_missing(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "partial-split", monkeypatch, stage_kind="build",
        stage_id="stage-build-partial-split-parent")
    ready = [
        {"id": "t01", "scope": ["a/**"], "tests": "true",
         "deps": [], "status": "pending"},
        {"id": "t02", "scope": ["b/**"], "tests": "true",
         "deps": [], "status": "pending"},
    ]
    state = loop.load(ws)
    state.update({
        "step": "execute", "parallel": True, "current_task": 0,
        "tasks": ready,
    })
    loop.save(ws, state)
    real_dispatch = loop._stage_loop_dispatch
    attempted = []
    first_dispatch = {}

    def interrupt_second_dispatch(*args, **kwargs):
        attempted.append(str(kwargs.get("stage_id") or ""))
        if len(attempted) == 2:
            raise RuntimeError("crash after the first child resume")
        result = real_dispatch(*args, **kwargs)
        first_dispatch["runtime"] = copy.deepcopy(result)
        return result

    monkeypatch.setattr(
        loop, "_stage_loop_dispatch", interrupt_second_dispatch)
    with pytest.raises(RuntimeError, match="first child resume"):
        loop._stage_loop_wave_dispatches(ws, state, ready)

    after_crash = store.load(stage["run_id"])
    bindings = copy.deepcopy(loop.load(ws)["_stage_bindings"])
    child_ids = {bindings[task_id]["build"] for task_id in ("t01", "t02")}
    roots = {
        child_id: store.read_stage_object(
            stage["run_id"], after_crash["stage_heads"][child_id]["object"]
        )["execution_root_id"]
        for child_id in child_ids
    }
    split_operations = {
        operation_id for operation_id, receipt in
        after_crash["stage_operations"].items()
        if receipt.get("operation") == "split_stage"
    }
    resumed_before_retry = {
        operation_id for operation_id, receipt in
        after_crash["stage_operations"].items()
        if receipt.get("operation") == "resume_stage"
    }
    assert len(split_operations) == 1
    assert len(resumed_before_retry) == 1

    monkeypatch.setattr(loop, "_stage_loop_dispatch", real_dispatch)
    retried = loop._stage_loop_wave_dispatches(
        ws, loop.load(ws), ready)

    assert set(retried) == {"t01", "t02"}
    assert retried["t01"] == first_dispatch["runtime"]
    final = store.load(stage["run_id"])
    assert loop.load(ws)["_stage_bindings"] == bindings
    assert set(final["active_stage_projection"]["active_stage_ids"]) == \
        child_ids
    assert {
        child_id: store.read_stage_object(
            stage["run_id"], final["stage_heads"][child_id]["object"]
        )["execution_root_id"]
        for child_id in child_ids
    } == roots
    assert {
        operation_id for operation_id, receipt in
        final["stage_operations"].items()
        if receipt.get("operation") == "split_stage"
    } == split_operations
    resumed_after_retry = {
        operation_id for operation_id, receipt in
        final["stage_operations"].items()
        if receipt.get("operation") == "resume_stage"
    }
    assert resumed_before_retry < resumed_after_retry
    assert len(resumed_after_retry) == 2


def test_new_run_init_rejects_spaced_actor_without_state_or_run_mutation(
        tmp_path, monkeypatch) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement)

    root = tmp_path / "spaced-actor"
    root.mkdir()
    workspace, store, initial = _real_pristine_run(root)
    requirement = _record_bootstrap_requirement(workspace)
    state_path = Path(loop._loop_path(str(workspace)))
    before_run = copy.deepcopy(store.load(initial["run_id"]))
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")

    refused = loop.init(
        str(workspace), "reject ambiguous actor authority",
        requirement_id=requirement["id"], by="Volodymyr Demkiv")

    assert refused["refused"] is True
    assert "actor" in refused["error"]
    assert "match" in refused["error"]
    assert not state_path.exists()
    assert store.load(initial["run_id"]) == before_run


@pytest.mark.parametrize(
    ("step", "force"), [("pm", True), ("done", False), ("done", True)])
def test_new_run_init_refuses_any_existing_singleton_even_force_or_terminal(
        tmp_path, monkeypatch, step, force) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _record_bootstrap_requirement)

    root = tmp_path / f"existing-{step}-{force}"
    root.mkdir()
    workspace = Path(_workspace(root))
    requirement = _record_bootstrap_requirement(workspace)
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    existing = loop.init(str(workspace), "preserve prior singleton history")
    assert "error" not in existing, existing
    if step == "done":
        existing["step"] = "done"
        loop.save(str(workspace), existing)
    state_path = Path(loop._loop_path(str(workspace)))
    before_state = state_path.read_bytes()
    before_files = sorted(path.name for path in state_path.parent.iterdir())
    store_root = Path(os.environ["TASKPLANE_HOME"])
    before_store = _content_inventory(store_root)
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")

    refused = loop.init(
        str(workspace), "never replace existing singleton history",
        requirement_id=requirement["id"], by="human:vdemkiv", force=force)

    assert refused["refused"] is True
    assert "existing singleton" in refused["error"]
    assert state_path.read_bytes() == before_state
    assert sorted(path.name for path in state_path.parent.iterdir()) == \
        before_files
    assert _content_inventory(store_root) == before_store


@pytest.mark.parametrize("mode", ["enabled", "disabled"])
def test_bound_v4_refuses_same_id_stale_cloned_store_byte_identically(
        tmp_path, monkeypatch, mode) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / f"stale-{mode}", monkeypatch,
        stage_id=f"stage-product-stale-store-{mode}")
    state_path = Path(loop._loop_path(ws))
    state_root = state_path.parents[2]
    before_state = state_path.read_bytes()
    before_original = copy.deepcopy(store.load(stage["run_id"]))
    original_locator = loop.runtime_storage.load_workspace_locator(ws)
    alternate_home = tmp_path / f"alternate-home-{mode}"
    source_run = Path(store.home) / "runs" / stage["run_id"]
    cloned_run = alternate_home / "runs" / stage["run_id"]
    cloned_run.parent.mkdir(parents=True)
    shutil.copytree(source_run, cloned_run)
    cloned_manifest_path = cloned_run / "manifest.json"
    before_clone = cloned_manifest_path.read_bytes()
    identity = loop.runtime_storage.resolve_repository_identity(ws)
    layout = loop.runtime_storage.resolve_layout(
        identity, home=str(alternate_home), run_id=stage["run_id"])
    stale_locator = {
        **copy.deepcopy(original_locator), "home": layout.home,
        "paths": {
            "state": layout.state_root, "graph": layout.graph_root,
            "evidence": layout.evidence_root, "lenses": layout.lens_root,
            "artifacts": layout.artifact_root,
        },
    }
    monkeypatch.setattr(
        loop.tp, "external_store_root", lambda _ws: str(state_root))
    monkeypatch.setattr(
        loop.runtime_storage, "load_workspace_locator",
        lambda _ws: copy.deepcopy(stale_locator))
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", mode)

    refused = loop.gate.__wrapped__(ws, "pass")

    assert "store identity changed" in refused["error"]
    assert state_path.read_bytes() == before_state
    assert store.load(stage["run_id"]) == before_original
    assert cloned_manifest_path.read_bytes() == before_clone


def test_stage_output_rejects_caller_controlled_source_workspace(
        tmp_path, monkeypatch) -> None:
    from taskplane import review_evidence

    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "caller-source", monkeypatch, stage_kind="engineering",
        stage_id="stage-engineering-caller-source")
    state = loop.load(ws)
    state["step"] = "em"
    loop.save(ws, state)
    alternate = tmp_path / "caller-controlled-source"
    alternate.mkdir()
    (alternate / "result.txt").write_text(
        "caller-selected bytes\n", encoding="utf-8")
    completion = loop._stage_loop_decision_completion(
        ws, schema="taskplane.loop-test-decision/v1",
        step="em", outcome="approved", result={"approved": True})
    completion["_stage_output"].update({
        "source_workspace": str(alternate),
        "sources": [{"path": "result.txt", "required": True}],
    })
    artifact_store = review_evidence.ArtifactStore(ws)
    before_artifacts = artifact_store.references("stage-output")
    before_run = copy.deepcopy(store.load(stage["run_id"]))
    opened = []
    monkeypatch.setattr(
        loop, "_before_stage_output_component_open",
        lambda *_args: opened.append(_args))

    with pytest.raises(ValueError, match="workspace"):
        loop._stage_loop_transition(
            ws, state, from_step="em", to_step="retro",
            completion=completion)

    assert opened == []
    assert artifact_store.references("stage-output") == before_artifacts
    assert store.load(stage["run_id"]) == before_run


def test_stage_output_intermediate_symlink_swap_hook_fails_closed(
        tmp_path, monkeypatch) -> None:
    from taskplane import review_evidence

    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "symlink-swap", monkeypatch, stage_kind="engineering",
        stage_id="stage-engineering-symlink-swap")
    workspace = Path(ws)
    safe = workspace / "safe"
    safe.mkdir()
    (safe / "result.txt").write_text(
        "validated output\n", encoding="utf-8")
    outside = tmp_path / "swap-target"
    outside.mkdir()
    (outside / "result.txt").write_text(
        "substituted output\n", encoding="utf-8")
    state = loop.load(ws)
    state["step"] = "em"
    loop.save(ws, state)
    completion = loop._stage_loop_decision_completion(
        ws, schema="taskplane.loop-test-decision/v1",
        step="em", outcome="approved", result={"approved": True})
    completion["_stage_output"]["sources"] = [{
        "path": "safe/result.txt", "required": True,
    }]
    artifact_store = review_evidence.ArtifactStore(ws)
    before_artifacts = artifact_store.references("stage-output")
    before_run = copy.deepcopy(store.load(stage["run_id"]))
    swaps = []

    def swap_intermediate(root, relative_path, component_index):
        if relative_path == "safe/result.txt" and component_index == 0 and \
                not swaps:
            swaps.append((root, relative_path, component_index))
            safe.rename(workspace / "safe-validated")
            safe.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(
        loop, "_before_stage_output_component_open", swap_intermediate)

    with pytest.raises((ValueError, OSError)):
        loop._stage_loop_transition(
            ws, state, from_step="em", to_step="retro",
            completion=completion)

    assert swaps == [(ws, "safe/result.txt", 0)]
    assert artifact_store.references("stage-output") == before_artifacts
    assert store.load(stage["run_id"]) == before_run


def test_same_kind_resolve_seals_decision_through_new_engineering_head(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "same-kind-resolve", monkeypatch,
        stage_kind="engineering", stage_id="stage-engineering-resolve-skip")
    state = loop.load(ws)
    state.update({
        "step": "escalated", "current_task": 0,
        "tasks": [{
            "id": "t01", "scope": ["README.md"], "status": "failed",
            "fix_cycles": 3,
        }],
    })
    loop.save(ws, state)
    monkeypatch.setattr(loop.tp, "trace", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "status", lambda _ws: {})

    resolved = loop.resolve(ws, "skip")

    assert "error" not in resolved, resolved
    assert resolved["step"] == "em"
    receipt = resolved["stage_transition"]
    assert receipt["operation"] == "terminalize_and_start"
    successor, handoff = _successor_handoff(ws, store, receipt)
    assert successor["stage_kind"] == "engineering"
    assert successor["stage_id"] != stage["stage_id"]
    payloads = _handoff_payload_text(ws, handoff)
    assert "taskplane.loop-resolution-result/v1" in payloads
    assert '"decision": "skip"' in payloads
    assert '"resulting_step": "em"' in payloads


def test_transition_reconciles_receipt_after_stage_commit_before_singleton(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "transition-crash", monkeypatch,
        stage_id="stage-product-transition-crash")
    specs = Path(ws) / "specs"
    specs.mkdir()
    (specs / "spec.md").write_text(
        "# Product\n\nReconcile the committed Product transition.\n",
        encoding="utf-8")
    state_path = Path(loop._loop_path(ws))
    before_state = state_path.read_bytes()
    real_save = loop.save
    failures = []

    def fail_first_plan_singleton(workspace, value):
        if value.get("step") == "plan" and not failures:
            failures.append("post-stage/pre-singleton")
            raise RuntimeError("crash before singleton transition commit")
        return real_save(workspace, value)

    monkeypatch.setattr(loop, "save", fail_first_plan_singleton)
    with pytest.raises(RuntimeError, match="before singleton"):
        loop.gate.__wrapped__(ws, "pass")

    assert state_path.read_bytes() == before_state
    after_stage_commit = store.load(stage["run_id"])
    prior = [
        receipt for receipt in after_stage_commit["stage_operations"].values()
        if receipt.get("operation") == "terminalize_and_start"
    ]
    assert len(prior) == 1
    monkeypatch.setattr(loop, "save", real_save)

    retried = loop.gate.__wrapped__(ws, "pass")

    assert "error" not in retried, retried
    assert retried["step"] == "plan"
    assert retried["stage_transition"]["operation_id"] == \
        prior[0]["operation_id"]
    final = store.load(stage["run_id"])
    assert len([
        receipt for receipt in final["stage_operations"].values()
        if receipt.get("operation") == "terminalize_and_start"
    ]) == 1


def test_native_wave_ignores_historical_stage_replay_state(
        tmp_path, monkeypatch) -> None:
    ws, store, stage, _started = _start_real_stage_loop(
        tmp_path / "wave-pre-output", monkeypatch, stage_kind="build",
        stage_id="stage-build-wave-pre-output")
    tasks = [
        {"id": "t01", "scope": ["a/**"], "tests": "true",
         "deps": [], "status": "pending"},
        {"id": "t02", "scope": ["b/**"], "tests": "true",
         "deps": [], "status": "pending"},
    ]
    state = loop.load(ws)
    state.update({
        "step": "execute", "parallel": True, "current_task": 0,
        "tasks": tasks,
    })
    loop.save(ws, state)
    historical_manifest = copy.deepcopy(store.load(stage["run_id"]))
    monkeypatch.setattr(
        loop, "_stage_loop_wave_dispatches",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("historical StageLifecycle replay was invoked")))

    authority = open_delivery_root(ws)
    first = loop.wave(ws, root_observation_authority=authority)
    second = loop.wave(ws, root_observation_authority=authority)

    for emitted in (first, second):
        assert "error" not in emitted, emitted
        assert [entry["task"]["id"] for entry in emitted["wave"]] == [
            "t01", "t02"]
        assert emitted["wait_invocation"]["outstanding_members"] == [
            "t01", "t02"]
        assert emitted["held"] == []
        encoded = json.dumps(emitted, sort_keys=True).lower()
        assert "stage_runtime_dispatch" not in encoded
        assert "execution_root" not in encoded
        assert "replay" not in encoded
        assert "tranche" not in encoded
    assert [entry["dispatch_intent"]["intent_id"]
            for entry in first["wave"]] == [
                entry["dispatch_intent"]["intent_id"]
                for entry in second["wave"]]
    assert not (loop.load(ws) or {}).get("_stage_bindings")
    assert store.load(stage["run_id"]) == historical_manifest
