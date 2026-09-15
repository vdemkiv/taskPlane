"""Small current-runtime fixtures; host events are explicitly simulated."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from taskplane import loop, phase_records, requirements, review_evidence, taskplane_lite
def _supporting_pristine_phase_run(tmp_path, monkeypatch, *, contracts=None, parallel=False, git_factory=None,
                                 phase_tokens_unlimited=False):
    """Public producers with simulated source/authority; no stage or host event seed."""
    from taskplane.tests.test_r0001_phase_agents_spec import _registry
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement,
    )

    workspace, store, initial = _real_pristine_run(tmp_path, git_factory=git_factory)
    ws = str(workspace)
    requirement = (
        _record_bootstrap_requirement(workspace) if contracts is None else
        requirements.record_requirement(
            ws, "supporting R-0001 contract identity compatibility",
            functional=["preserve exact source contract identities and relations"],
            acceptance=["normal initialization prepares the first phase"],
            contracts=contracts,
        )
    )
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    initialized = loop.init(
        ws, "supporting first-dispatch regression", requirement_id=requirement["id"],
        by="human:simulated", parallel=parallel,
        phase_tokens_unlimited=phase_tokens_unlimited,
    )
    assert "error" not in initialized, initialized
    current = store.load(initial["run_id"])
    assert current["schema"] == "taskplane.run/v4"
    assert current["stage_heads"]
    assert phase_records.phase_routing(current)["result"]["owner"] == "agent-runtime"
    return ws, store, initial["run_id"], requirement

def _host_event(ws, requested, kind):
    """Explicit simulated native boundary, never native proof."""
    requested = requested.get("obligations", requested)
    name = requested["task_name"]
    from taskplane.tests.test_native_terminal_telemetry import _worker_event
    event = _worker_event(ws, requested, label=name)
    # Provider metadata is outside the target: recording host counters must
    # not change the submitted source candidate.
    event["agent_transcript_path"] = str(Path(ws).parent / ".codex-native" /
        "sessions" / datetime.now(timezone.utc).strftime("%Y/%m/%d") /
        Path(event["agent_transcript_path"]).name)
    event.update({"hook_event_name": kind, "session_id": "simulated-session",
        "turn_id": "simulated-turn", "outcome": "success", "usage": {"total_tokens": 100}})
    identity = taskplane_lite.hook_event_identity(ws,
        "subagent-start" if kind == "SubagentStart" else "subagent-stop", event)
    event["_taskplane_hook_claim_id"] = hashlib.sha256(identity.encode()).hexdigest()
    return event


def author_lens_results(ws, requested):
    """Simulated lens authorship; shared production ingestion remains real."""
    from taskplane import phase_harness
    if "stage_runtime_dispatch" not in requested:
        return
    # Evidence producers share the phase's role but have a different sealed input.
    artifacts = review_evidence.ArtifactStore(ws)
    raw_input = artifacts.read(requested["stage_runtime_dispatch"]["startup"]["phase_input"])
    if raw_input.get("schema") != "taskplane.phase-input/v1":
        return
    inputs = phase_harness.read_input(loop, ws, requested["stage_runtime_dispatch"])
    if not any(row["artifact_class"] == "lens-evidence" for row in inputs["phase_definition"]["produces"]):
        return
    dispatched = phase_harness.collect_lenses(loop, ws, requested["stage_runtime_dispatch"], prepare=True)
    write_lens_results(artifacts, dispatched["plan"])


def write_lens_results(artifacts, plan_ref, *, findings=()):
    """One candidate fixture for all lenses; no host provenance claim."""
    for slot in artifacts.read(plan_ref)["slots"]:
        lease, brief = artifacts.read(slot["lease"]), artifacts.read(slot["brief"])
        result = {key: value for key, value in lease.items() if key != "schema"}
        result.update(schema="taskplane.lens-slot-output/v2", authored_by="lens-slot",
            findings=[dict(row, lens=slot["lens_ids"][0]) for row in findings],
            lens_results=[{"lens": lid, "verdict": "pass", "blockers": 0,
                "checked_evidence": [{"file": "README.md", "line": 1,
                    "claim": "Simulated fixture checks the sealed phase candidate"}]}
                for lid in slot["lens_ids"]])
        if brief.get("language_references"):
            result["references_applied"] = brief["language_references"]
        path = Path(artifacts.workspace) / slot["result_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result))


def _emit_host_hook(ws, requested, kind, monkeypatch, **overrides):
    from taskplane import tp as cli
    from taskplane.tests.test_native_terminal_telemetry import _write_codex_transcript
    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "native")
    # A native hook is a host event, not this builder's governed CLI process.
    monkeypatch.delenv("TASKPLANE_TASK", raising=False)
    if kind == "SubagentStop" and not overrides.pop("omit_lens_results", False):
        author_lens_results(ws, requested)
    event = _host_event(ws, requested, kind)
    event.update(overrides)
    transcript = Path(event["agent_transcript_path"])
    if "agent_transcript_path" not in overrides:
        monkeypatch.setenv("CODEX_HOME", str(Path(ws).parent / ".codex-native"))
        _write_codex_transcript(transcript, label=event["task_name"],
            input_tokens=10 if kind == "SubagentStart" else 100,
            cached_tokens=0, output_tokens=0 if kind == "SubagentStart" else 10,
            event=event)
    identity = taskplane_lite.hook_event_identity(ws,
        "subagent-start" if kind == "SubagentStart" else "subagent-stop", event)
    event["_taskplane_hook_claim_id"] = hashlib.sha256(identity.encode()).hexdigest()
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    function = cli.cmd_subagent_start if kind == "SubagentStart" else cli.cmd_subagent_stop
    return function(None)


def _authored_requirement(ws, stage):
    # Simulated producer authors only its own declared raw candidate. The
    # production hook must create all artifacts, receipts and handoffs.
    path = Path(ws) / "specs/requirement.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"schema": "taskplane.requirement/v1",
        "id": stage["requirement"]["id"], "acceptance_criteria": ["real bridge collection"]}))


def _normal_phase_workspace(tmp_path, monkeypatch, *, stage_kind="product"):
    assert stage_kind == "product", "current runs start at Product"
    ws, store, run_id, _ = _supporting_pristine_phase_run(tmp_path, monkeypatch)
    context = loop._stage_loop_context(ws, loop.load(ws))
    stage = context["stage"]
    route = phase_records.phase_routing(store.load(run_id))
    authorize = lambda fresh: loop._phase_bridge_authorize(ws, context, fresh)
    return ws, store, stage, review_evidence.ArtifactStore(ws), route, authorize


def phase_pending(ws):
    """Inspect durable phase evidence separately from the public dispatch."""
    return loop._phase_bridge_pending(ws, loop.load(ws))["phase_runtime"]


def authored_evidence_results(assignments: list[dict], execute) -> dict[str, dict]:
    from taskplane import evaluate_child_evidence as evidence
    language = next(row for row in assignments
                    if row["producer_kind"] == evidence.LANGUAGE_PRODUCER)
    design = next(row for row in assignments
                  if row["producer_kind"] == evidence.TEST_DESIGN_PRODUCER)
    quality = {
        "schema": evidence.LANGUAGE_RESULT_SCHEMA,
        "producer_kind": evidence.LANGUAGE_PRODUCER,
        "reuse_key_digest": language["reuse_key_digest"],
        "language_coverage": [{
            "language": item["language"], "reference_id": item["reference"]["path"],
            "reference_sha256": item["reference"]["content_sha256"],
            "toolchain_fingerprint": item["toolchain_fingerprint"],
            "inspected_files": item["implementation_files"],
            "command_receipts": [
                execute(language, command["argv"],
                               "quality:" + command["id"])
                for command in item["required_commands"]], "findings": [],
        } for item in language["language_obligations"]],
    }
    obligations = design["test_obligations"]
    test_design = {
        "schema": evidence.TEST_DESIGN_RESULT_SCHEMA,
        "producer_kind": evidence.TEST_DESIGN_PRODUCER,
        "reuse_key_digest": design["reuse_key_digest"],
        "current_value": [{
            **test, "classification": "protects-current-contract",
            "execution": execute(
                design, ["python3", "-m", "pytest", "-q", test["selector"]],
                "current:" + test["selector"]),
        } for test in obligations["tests"]],
        "producer_consumers": [{
            "producer": edge["producer"], "consumer": edge["consumer"],
            "selector": edge["selector"],
            "execution": execute(
                design, ["python3", "-m", "pytest", "-q", edge["selector"]],
                "edge:" + edge["producer"] + ":" + edge["consumer"]),
            "severed_edge_execution": execute(
                design, ["python3", "-m", "pytest", "-q",
                         edge["severed_edge"]["selector"]],
                "severed:" + edge["producer"] + ":" + edge["consumer"]),
        } for edge in obligations["producer_consumer_edges"]],
        "same_slice_fixtures": [{
            "producer": row["producer"], "path": row["fixture"]["path"],
            "slice": row["slice"],
        } for row in obligations["changed_interfaces"]],
        "failure_classifications": [{
            "id": row["id"], "classification": row["classification"],
            "reason": "candidate behavior contradicted the current contract",
            "owner": "product-code", "cluster": "evidence-admission",
        } for row in obligations["failures"]],
    }
    return {evidence.LANGUAGE_PRODUCER: quality,
            evidence.TEST_DESIGN_PRODUCER: test_design}


def save_component_workflow(workspace, state):
    """Seed explicit v4 storage for a component test, never phase/host proof."""
    from taskplane import run_store, storage
    state.setdefault("max_fix_cycles", 2)
    state.setdefault("checkpoints", ["plan", "em"])
    state.setdefault("current_task", 0)
    state.setdefault("tasks", [])
    locator = storage.load_workspace_locator(workspace)
    if locator is None:
        import subprocess
        workspace = str(Path(workspace).resolve())
        # This helper is explicitly for isolated component fixtures.
        assert workspace != str(Path(__file__).resolve().parents[2])
        if not (Path(workspace) / ".git").exists():
            subprocess.run(["git", "init", "-q", workspace], check=True)
        run_id = state.setdefault("run_id", "component-run")
        identity = storage.resolve_repository_identity(workspace)
        store = run_store.RunStore()
        store.create(identity, run_id=run_id, checkout=workspace,
            host={"kind": "simulated", "session_id": "component-session"},
            target={"kind": "workspace"})
        storage.write_workspace_locator(workspace, identity=identity,
            layout=storage.resolve_layout(identity, home=store.home, run_id=run_id),
            run_id=run_id)
    else:
        state.setdefault("run_id", locator["run_id"])
        assert state["run_id"] == locator["run_id"], "component fixture changed run"
        store = run_store.RunStore(home=locator["home"])
        if not Path(store._manifest_path(locator["run_id"])).exists():
            store.create(storage.resolve_repository_identity(workspace), run_id=locator["run_id"],
                checkout=workspace, host={"kind": "simulated", "session_id": "component-session"},
                target={"kind": "workspace"})
    loop.save(workspace, state)
