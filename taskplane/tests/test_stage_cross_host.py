"""Cross-host consumers preserve one bounded stage startup contract."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from taskplane import (
    loop,
    requirements,
    review_evidence,
    run_store,
    stage_entities,
    stage_handoff,
    storage,
    taskplane_lite,
    views,
)
from taskplane.host_capabilities import Observation, negotiate_host_surfaces
from taskplane.host_native import HostSurfaceEvent, HostSurfaceSnapshot
from tests.root_session_fixture import open_delivery_root
from taskplane.tests.test_stage_dispatch import _receipt, _stage_and_handoff


STAGE_SURFACE = ("stage_runtime",)
HOST_CASES = (
    ("codex", "supported", "native"),
    ("claude", "supported", "native"),
    ("slack-capable", "supported", "native"),
    ("managed", "unsupported", "accessible_bounded"),
    ("legacy", "unknown", "accessible_bounded"),
)
STARTUP_FIELDS = {
    "schema",
    "stage_id",
    "authority",
    "input_manifest_bytes",
    "input_handoff",
    "selected_artifacts",
    "budget",
    "execution_claim",
    "attempt_id",
    "declared_scope",
}
FORBIDDEN_RUNTIME_FIELDS = {
    "agents",
    "actor",
    "conversations",
    "event_logs",
    "tool_transcripts",
    "leases",
    "runtime_state",
    "session_id",
    "predecessor_roots",
}


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key for child in value.values() for key in _all_keys(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in _all_keys(child)}
    return set()


def _dispatch() -> tuple[dict[str, object], dict[str, object]]:
    stage, handoff = _stage_and_handoff()
    dispatch = taskplane_lite.stage_runtime_dispatch(
        stage,
        _receipt(stage),
        handoff,
        stage["selected_artifacts"],
        declared_scope={
            "scope_paths": ["taskplane/tests/test_stage_cross_host.py"],
            "out_of_scope_paths": ["taskplane/track.py"],
        },
    )
    return dispatch, stage


def _snapshot(dispatch: dict[str, object]) -> HostSurfaceSnapshot:
    startup = dispatch.get("startup")
    authority = (startup.get("authority")
                 if isinstance(startup, dict) else None)
    run_id = (str(authority.get("run_id"))
              if isinstance(authority, dict) and authority.get("run_id")
              else "run-r0004")
    revision = (str(authority.get("target_revision"))
                if isinstance(authority, dict) and
                authority.get("target_revision") else "1" * 40)
    return HostSurfaceSnapshot.create(
        workflow_id="stage-workflow-r0004",
        run_id=run_id,
        target="github.com/example/taskplane@" + revision,
        revision=revision,
        sequence=1,
        stage="evaluate",
        state="active",
        values={"stage_runtime": dispatch},
        evidence=(f"sha256:{dispatch['startup_sha256']}",),
        safe_actions=("inspect",),
    )


def _host_runtime_module():
    path = Path(__file__).resolve().parents[2] / "hooks" / \
        "host_native_runtime.py"
    spec = importlib.util.spec_from_file_location(
        "stage_cross_host_native_runtime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=workspace, text=True, capture_output=True,
        encoding="utf-8", errors="replace", check=True)
    return result.stdout.strip()


def _real_pristine_run(
        tmp_path: Path,
        ) -> tuple[Path, run_store.RunStore, dict[str, object]]:
    """Create only public run infrastructure, never stage/handoff objects."""
    workspace = tmp_path / "pristine-loop-workspace"
    workspace.mkdir()
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test")
    (workspace / ".gitignore").write_text(
        ".taskplane/\n", encoding="utf-8")
    (workspace / "README.md").write_text(
        "pristine stage journey\n", encoding="utf-8")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-qm", "base")
    _git(
        workspace, "remote", "add", "origin",
        "https://github.com/example/taskplane-pristine.git")
    revision = _git(workspace, "rev-parse", "HEAD")
    run_id = "run-cross-host-pristine"
    identity = storage.resolve_repository_identity(str(workspace))
    # The whole-run settings/artifact control plane and stage transport share
    # one canonical TASKPLANE_HOME.  A fixture-local second store is now a
    # deliberately severed authority edge, not an isolated test shortcut.
    store = run_store.RunStore()
    initial = store.create(
        identity,
        run_id=run_id,
        checkout=str(workspace),
        host={"kind": "codex", "session_id": "pristine-session"},
        target={"kind": "workspace", "revision": revision},
    )
    layout = storage.resolve_layout(
        identity, home=store.home, run_id=run_id)
    storage.write_workspace_locator(
        str(workspace), identity=identity, layout=layout, run_id=run_id)
    return workspace, store, initial


def _record_bootstrap_requirement(
        workspace: Path, *, ordinal: int = 1) -> dict[str, object]:
    records = [
        requirements.record_requirement(
            str(workspace), f"stage bootstrap requirement {index}",
            functional=["bounded stage dispatch starts without caller JSON"],
            acceptance=["the exact run owns one immutable root stage"],
        )
        for index in range(1, ordinal + 1)
    ]
    return records[-1]


def _real_loop_stage(
        tmp_path: Path, *, stage_kind: str = "product",
        stage_id: str = "stage-product-cross-host",
        ) -> tuple[Path, run_store.RunStore, dict[str, object]]:
    workspace = tmp_path / "loop-workspace"
    workspace.mkdir()
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test")
    (workspace / ".gitignore").write_text(
        ".taskplane/\n", encoding="utf-8")
    (workspace / "README.md").write_text("stage journey\n", encoding="utf-8")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-qm", "base")
    _git(
        workspace, "remote", "add", "origin",
        "https://github.com/example/taskplane.git")
    revision = _git(workspace, "rev-parse", "HEAD")

    run_id = "run-cross-host-loop"
    identity = storage.resolve_repository_identity(str(workspace))
    # Keep the stage aggregate in the same canonical store used by the
    # whole-run artifact/settings loader.  Tests that need a foreign store
    # construct one explicitly at the corruption/authority boundary.
    store = run_store.RunStore()
    initial = store.create(
        identity,
        run_id=run_id,
        checkout=str(workspace),
        host={"kind": "codex", "session_id": "cross-host-session"},
        target={"kind": "workspace", "revision": revision},
    )
    layout = storage.resolve_layout(
        identity, home=store.home, run_id=run_id)
    storage.write_workspace_locator(
        str(workspace), identity=identity, layout=layout, run_id=run_id)

    authority = {
        "schema": "taskplane.stage-authority-binding/v1",
        "run_id": run_id,
        "repository_id": identity.repo_id,
        "repository_key": identity.key,
        "worktree_id": "cross-host-worktree",
        "target_revision": revision,
        "worktree_revision": revision,
        "requirement_id": "R-0004",
        "requirement_revision": "4",
        "design_revision": "2",
        "design_fingerprint": "c" * 64,
        "actor": "human:vdemkiv",
        "session_id": "cross-host-session",
        "authority_revision": 7,
        "authority_fingerprint": "d" * 64,
    }
    artifact_store = review_evidence.ArtifactStore(str(workspace))
    evidence = review_evidence.portable_artifact_reference(
        artifact_store,
        artifact_store.put("test-evidence", {"status": "passed"}),
    )
    selected = review_evidence.portable_artifact_reference(
        artifact_store,
        artifact_store.put("source", {"revision": revision}),
    )
    handoff = stage_handoff.create_manifest(
        artifact_store,
        producer_stage_id="stage-input-001",
        producer_outcome="done",
        requirement={
            "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
        },
        design={"revision": "2", "fingerprint": "c" * 64},
        target=None,
        commit=None,
        contracts={
            "provided": ["contract:stage-artifact-handoff"],
            "consumed": [],
            "changed": [],
        },
        deliverables=["cross-host-input"],
        evidence_references=[evidence],
        selected_artifacts=[selected],
        exclusions=sorted(stage_handoff.REQUIRED_EXCLUSIONS),
        authorization={
            "actor": authority["actor"],
            "session_id": authority["session_id"],
            "authorized_at": "2026-08-21T14:00:00Z",
            "operation_id": "authorize-cross-host-input",
            "authority_record": {
                "schema": "taskplane.authority-record-reference/v1",
                "authority_schema": "taskplane.consolidated-authorization/v1",
                "revision": authority["authority_revision"],
                "fingerprint": authority["authority_fingerprint"],
            },
            "nonconsumable_reuse": None,
        },
    )
    handoff_reference = review_evidence.portable_artifact_reference(
        artifact_store,
        stage_handoff.store_manifest(artifact_store, handoff),
    )
    stage = stage_entities.create_stage(
        run_id=run_id,
        stage_id=stage_id,
        requirement=handoff["requirement"],
        design=handoff["design"],
        stage_kind=stage_kind,
        parent_stage_ids=[],
        predecessor_stage_ids=[],
        input_manifest_ref=handoff_reference,
        execution_root_id=f"execution-{stage_id}",
        deliverables=[f"{stage_kind}-decision"],
        selected_artifacts=[selected],
        budget={"token_limit": 4_000, "attempt_limit": 2},
        dependencies=[],
        contracts=["contract:stage-artifact-handoff"],
        authority=authority,
        created_at="2026-08-21T14:05:00Z",
    )
    assert initial["revision"] == 1
    return workspace, store, stage


def _parallel_loop_wave(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        ) -> tuple[Path, run_store.RunStore, dict[str, object], bytes]:
    workspace, store, initial = _real_pristine_run(tmp_path)
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    requirement = _record_bootstrap_requirement(workspace)
    criterion = "the task completes without disturbing its sibling stage root"
    tasks = [
        {
            "id": "t-left", "scope": ["left/**"], "tests": "true",
            "deps": [], "status": "pending", "merge_on_pass": False,
            "req": str(requirement["id"]), "criteria": [criterion],
        },
        {
            "id": "t-right", "scope": ["right/**"], "tests": "true",
            "deps": [], "status": "pending", "merge_on_pass": False,
            "req": str(requirement["id"]), "criteria": [criterion],
        },
    ]
    plan_dir = workspace / "plan"
    plan_dir.mkdir(exist_ok=True)
    (plan_dir / "plan.md").write_text(
        "# Parallel delivery plan\n\nBuild the two independent tasks.\n",
        encoding="utf-8",
    )
    (plan_dir / "tasks.json").write_text(
        json.dumps({"tasks": tasks}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    state = loop.init(
        str(workspace), "dispatch two independent stage roots",
        spec_path="specs/spec.md",
        requirement_id=str(requirement["id"]), parallel=True,
        by="human:vdemkiv")
    assert "error" not in state, state
    bootstrapped = loop.next_action.__wrapped__(str(workspace))
    assert "error" not in bootstrapped, bootstrapped
    monkeypatch.setattr(
        loop, "_load_tasks",
        lambda _ws, current: current.update({"tasks": copy.deepcopy(tasks)}))
    monkeypatch.setattr(loop, "_plan_dor_errors", lambda *_a, **_k: [])
    monkeypatch.setattr(
        loop.tp, "plan_task_id_refusal", lambda *_a, **_k: None)
    advanced = loop.gate.__wrapped__(str(workspace), "pass")
    assert "error" not in advanced, advanced
    assert advanced["step"] == "plan_approval"
    approved = loop.approve.__wrapped__(
        str(workspace), force=True, by="human:vdemkiv")
    assert "error" not in approved, approved
    assert approved["step"] == "execute"
    state = loop.load(str(workspace))
    state["submission_required"] = False
    loop.save(str(workspace), state)
    authority = open_delivery_root(str(workspace))
    manifest = store.load(str(initial["run_id"]))
    parent_id = manifest["active_stage_projection"]["foreground_stage_id"]
    parent = store.read_stage_object(
        str(initial["run_id"]), manifest["stage_heads"][parent_id]["object"])
    assert parent["stage_kind"] == "build"
    return workspace, store, parent, authority


def _write_reanchorable_pass(workspace: str | Path, task: dict) -> None:
    verdict_path = Path(loop.runtime_storage.evaluation_path(str(workspace)))
    verdict_path.parent.mkdir(parents=True, exist_ok=True)
    verdict_path.write_text(json.dumps({
        "schema": loop.evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
        "task": task["id"],
        "requirement": task["req"],
        "verdict": "pass",
        "criteria": [{
            "criterion": criterion,
            "status": "met",
            "evidence": "verified by the stage integration fixture",
        } for criterion in task["criteria"]],
        "failures": [],
    }, sort_keys=True) + "\n", encoding="utf-8")


def test_cross_host_surfaces_preserve_one_canonical_bounded_startup(
    tmp_path: Path,
) -> None:
    expected, stage = _dispatch()
    expected_bytes = taskplane_lite.stage_startup_bytes(expected)
    expected_snapshot = _snapshot(expected).to_dict()
    startup = expected["startup"]

    assert isinstance(startup, dict)
    assert set(startup) == STARTUP_FIELDS
    assert startup["schema"] == "taskplane.stage-startup/v1"
    authority_reference = {
        "schema": "taskplane.stage-authority-reference/v1",
        "fingerprint": hashlib.sha256(
            taskplane_lite.canonical_json_bytes(stage["authority"])
        ).hexdigest(),
    }
    assert startup["authority"] == authority_reference
    assert startup["input_handoff"]["authorization"] == authority_reference
    assert startup["input_handoff"]["schema"] == \
        "taskplane.stage-handoff-dispatch/v1"
    assert startup["budget"] == stage["budget"]
    assert not (FORBIDDEN_RUNTIME_FIELDS & _all_keys(startup))
    for reference in startup["selected_artifacts"]:
        assert reference["schema"] == "taskplane.artifact-reference/v1"
        assert reference["transport"] == "artifact-reference"
        assert reference["fingerprint"] == reference["digest"]
        assert str(reference["locator"]).startswith("artifact://")

    fingerprints: set[str] = set()
    startup_hashes: set[str] = set()
    handoff_fingerprints: set[str] = set()
    for host, capability_status, expected_surface in HOST_CASES:
        # JSON transport simulates a host boundary and prevents shared Python
        # object identity from making this parity check pass accidentally.
        transported = json.loads(json.dumps(expected))
        assert taskplane_lite.stage_startup_bytes(transported) == expected_bytes

        snapshot = _snapshot(transported)
        selection = negotiate_host_surfaces(
            host=host,
            host_version="2.17.13-compatible",
            observations={
                "stage_runtime": Observation(
                    status=capability_status,
                    source=f"fixture:{host}",
                    confidence="high",
                ),
            },
            surfaces=STAGE_SURFACE,
        )["stage_runtime"]
        projection = snapshot.project(selection)

        assert projection["canonical"] == expected_snapshot
        assert projection["presentation"]["kind"] == expected_surface
        assert projection["presentation"]["safe_actions"] == ["inspect"]
        canonical_runtime = projection["canonical"]["values"]["stage_runtime"]
        assert canonical_runtime == expected

        # The complete Markdown fallback and machine JSON decode to the same
        # canonical model. Inline native presentation carries those bytes too.
        model = {
            "schema": "taskplane.stage-runtime-view/v1",
            "stage_runtime": canonical_runtime,
        }
        delivery = views.deliver_dashboard(
            str(tmp_path / host), model,
            inline_threshold=taskplane_lite.MAX_STAGE_STARTUP_BYTES,
        )
        machine = Path(delivery["artifacts"]["json"]["path"]).read_bytes()
        text_first = Path(
            delivery["artifacts"]["markdown"]["path"]
        ).read_bytes()
        inline = delivery["inline"]
        assert inline is not None
        assert views.decode_dashboard_artifact("json", machine) == model
        assert views.decode_dashboard_artifact("markdown", text_first) == model
        assert views.decode_dashboard_artifact(
            "inline", inline["content"].encode("utf-8")
        ) == model

        fingerprints.add(snapshot.fingerprint)
        startup_hashes.add(str(canonical_runtime["startup_sha256"]))
        handoff_fingerprints.add(str(
            canonical_runtime["startup"]["input_handoff"]["fingerprint"]
        ))

    assert len(fingerprints) == 1
    assert len(startup_hashes) == 1
    assert len(handoff_fingerprints) == 1


def test_dashboard_handoff_republishes_same_snapshot_without_portable_host_paths(
    tmp_path: Path,
) -> None:
    runtime = _host_runtime_module()
    dispatch, _stage = _dispatch()
    snapshot = _snapshot(dispatch)
    committed = {
        "schema": "taskplane.dashboard-snapshot-refresh/v1",
        "snapshot": snapshot.to_dict(),
        "event": HostSurfaceEvent.from_snapshot(
            snapshot, event_type="handoff").to_dict(),
        "replayed": False,
        "source_mode": "v4",
    }
    recovery = runtime.HostNativeRecovery()
    presentations = []
    for host in ("codex", "claude"):
        selections = negotiate_host_surfaces(
            host=host, host_version="test", observations={
                name: Observation(
                    status="supported", source=str(tmp_path / host / name),
                    confidence="high", observed_at="100.0")
                for name in runtime.SURFACE_CAPABILITIES
            })
        presentations.append(runtime.project_committed_dashboard(
            committed, host=host, selections=selections, recovery=recovery))

    codex, claude = presentations
    assert codex["publish_head"] == claude["publish_head"]
    assert codex["publish_head"]["sequence"] == snapshot.sequence
    assert [event.sequence for event in recovery.audit] == [snapshot.sequence]
    assert codex["projections"]["dashboard"]["canonical"] == \
        claude["projections"]["dashboard"]["canonical"] == snapshot.to_dict()
    for result in presentations:
        acknowledgement = result["acknowledgement"]
        assert acknowledgement["identity"]["workflow_id"] == \
            snapshot.workflow_id
        assert acknowledgement["evidence"] == list(snapshot.evidence)
        assert acknowledgement["gate"] == snapshot.to_dict()["values"].get(
            "gate", {})

    portable = json.dumps(
        [codex["acknowledgement"], claude["acknowledgement"]],
        sort_keys=True)
    assert str(tmp_path) not in portable
    assert "host_path" not in portable
    assert "workspace_path" not in portable


def test_real_loop_journey_emits_one_bounded_dispatch_on_every_host(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, store, stage = _real_loop_stage(tmp_path)
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "cross-host-session")
    requirement = _record_bootstrap_requirement(workspace, ordinal=4)
    assert requirement["id"] == "R-0004"
    initialized = loop.init(
        str(workspace), "exercise a real host stage",
        requirement_id="R-0004", by="human:vdemkiv")
    assert "error" not in initialized, initialized

    started = loop.stage_command(str(workspace), "start", {
        "schema": "taskplane.stage-command/v1",
        "stage": stage,
        "expected_revision": 1,
        "operation_id": "start-cross-host-product",
        "expected_predecessor_fingerprints": {},
        "foreground": True,
        "authority": stage["authority"],
        "declared_scope": {
            "scope_paths": ["specs/spec.md"],
            "out_of_scope_paths": ["taskplane/loop.py"],
        },
    })
    assert "error" not in started, started
    dispatch = started["dispatch"]
    assert dispatch["schema"] == "taskplane.stage-dispatch/v1"
    assert dispatch["startup"]["stage_id"] == stage["stage_id"]
    assert dispatch["telemetry"]["predecessor_root_opens"] == 0

    history = loop.stage_history(str(workspace), str(stage["run_id"]), limit=1)
    assert history["schema"] == "taskplane.stage-history-page/v1"
    assert [row["stage_id"] for row in history["stages"]] == [
        stage["stage_id"]]
    assert history["stages"][0]["state"] == "active"
    assert history["next_cursor"] is None

    current = store.load(str(stage["run_id"]))
    resumed = loop.stage_command(str(workspace), "resume", {
        "schema": "taskplane.stage-command/v1",
        "run_id": stage["run_id"],
        "stage_id": stage["stage_id"],
        "expected_head_fingerprint": stage["fingerprint"],
        "expected_revision": current["revision"],
        "operation_id": "resume-cross-host-product",
        "attempt_id": "attempt-cross-host-002",
        "authority": stage["authority"],
        "declared_scope": {
            "scope_paths": ["specs/spec.md"],
            "out_of_scope_paths": ["taskplane/loop.py"],
        },
    })
    assert "error" not in resumed, resumed
    assert resumed["dispatch"]["startup"]["attempt_id"] == \
        "attempt-cross-host-002"
    assert resumed["dispatch"]["telemetry"]["predecessor_root_opens"] == 0

    expected_snapshot = _snapshot(dispatch).to_dict()
    fingerprints = set()
    for host, capability_status, expected_surface in HOST_CASES:
        transported = json.loads(json.dumps(dispatch))
        selection = negotiate_host_surfaces(
            host=host,
            host_version="2.17.13-compatible",
            observations={
                "stage_runtime": Observation(
                    status=capability_status,
                    source=f"real-loop:{host}",
                    confidence="high",
                ),
            },
            surfaces=STAGE_SURFACE,
        )["stage_runtime"]
        snapshot = _snapshot(transported)
        projected = snapshot.project(selection)
        assert projected["canonical"] == expected_snapshot
        assert projected["canonical"]["values"]["stage_runtime"] == dispatch
        assert projected["presentation"]["kind"] == expected_surface
        fingerprints.add(snapshot.fingerprint)

    assert len(fingerprints) == 1


def test_parallel_wave_preserves_native_intent_and_wait_identity_on_hosts(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, store, parent, authority = _parallel_loop_wave(
        tmp_path, monkeypatch)
    manifest_before = copy.deepcopy(store.load(str(parent["run_id"])))

    emitted = loop.wave(
        str(workspace), root_observation_authority=authority)

    assert "error" not in emitted, emitted
    assert [entry["task"]["id"] for entry in emitted["wave"]] == [
        "t-left", "t-right"]
    identities: dict[str, tuple[str, str]] = {}
    for entry in emitted["wave"]:
        task_id = str(entry["task"]["id"])
        intent = entry["dispatch_intent"]
        identities[task_id] = (
            str(intent["intent_id"]), str(entry["task_name"]))
        payload = {
            "dispatch_intent": intent,
            "wait_policy": entry["wait_policy"],
            "wait_invocation": emitted["wait_invocation"],
        }
        for host, _capability_status, _expected_surface in HOST_CASES:
            transported = json.loads(json.dumps(payload))
            assert transported == payload, host

    assert len({intent_id for intent_id, _name in identities.values()}) == 2
    assert len({name for _intent_id, name in identities.values()}) == 2
    assert emitted["wait_invocation"]["outstanding_members"] == [
        "t-left", "t-right"]
    encoded = json.dumps(emitted, sort_keys=True).lower()
    assert "stage_runtime_dispatch" not in encoded
    assert "execution_root" not in encoded
    assert not (loop.load(str(workspace)) or {}).get("_stage_bindings")
    assert store.load(str(parent["run_id"])) == manifest_before


def test_parallel_wave_ignores_historical_split_persistence_adapter(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, store, parent, authority = _parallel_loop_wave(
        tmp_path, monkeypatch)
    manifest_before = copy.deepcopy(store.load(str(parent["run_id"])))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("native delivery invoked stage split persistence")

    monkeypatch.setattr(
        loop, "_persist_stage_loop_wave_bindings", forbidden)
    monkeypatch.setattr(loop, "_stage_loop_wave_dispatches", forbidden)
    first = loop.wave(
        str(workspace), root_observation_authority=authority)
    second = loop.wave(
        str(workspace), root_observation_authority=authority)

    for emitted in (first, second):
        assert "error" not in emitted, emitted
        assert [entry["task"]["id"] for entry in emitted["wave"]] == [
            "t-left", "t-right"]
        assert emitted["wait_invocation"]["outstanding_members"] == [
            "t-left", "t-right"]
        assert emitted["held"] == []
    assert [entry["dispatch_intent"]["intent_id"]
            for entry in first["wave"]] == [
                entry["dispatch_intent"]["intent_id"]
                for entry in second["wave"]]
    assert not (loop.load(str(workspace)) or {}).get("_stage_bindings")
    assert store.load(str(parent["run_id"])) == manifest_before


def test_pristine_new_run_wave_refuses_legacy_or_implicit_root_dispatch(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, store, initial = _real_pristine_run(tmp_path)
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    requirement = _record_bootstrap_requirement(workspace)
    state = loop.init(
        str(workspace), "bootstrap a parallel build wave", parallel=True,
        requirement_id=str(requirement["id"]), by="human:vdemkiv")
    state.update({
        "step": "execute",
        "tasks": [
            {
                "id": "t-left", "scope": ["left/**"], "tests": "true",
                "deps": [], "status": "pending",
            },
            {
                "id": "t-right", "scope": ["right/**"], "tests": "true",
                "deps": [], "status": "pending",
            },
        ],
    })
    loop.save(str(workspace), state)
    state_path = Path(loop._loop_path(str(workspace)))
    before_state = state_path.read_bytes()
    before_run = copy.deepcopy(store.load(str(initial["run_id"])))

    refused = loop.wave(str(workspace))

    assert "error" in refused
    assert "stage-native" in refused["error"]
    assert "first `loop next`" in refused["error"]
    assert "wave" not in refused or refused["wave"] == []
    assert store.load(str(initial["run_id"])) == before_run
    assert state_path.read_bytes() == before_state


def test_native_wave_never_resumes_historical_stage_children(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, store, parent, authority = _parallel_loop_wave(
        tmp_path, monkeypatch)
    manifest_before = copy.deepcopy(store.load(str(parent["run_id"])))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("native wave attempted a stage child resume")

    monkeypatch.setattr(loop, "_stage_loop_dispatch", forbidden)
    monkeypatch.setattr(loop, "_stage_loop_wave_dispatches", forbidden)
    emitted = loop.wave(
        str(workspace), root_observation_authority=authority)

    assert "error" not in emitted, emitted
    assert [entry["task"]["id"] for entry in emitted["wave"]] == [
        "t-left", "t-right"]
    assert emitted["wait_invocation"]["outstanding_members"] == [
        "t-left", "t-right"]
    assert all(entry["dispatch_intent"]["intent_id"]
               for entry in emitted["wave"])
    assert not (loop.load(str(workspace)) or {}).get("_stage_bindings")
    assert store.load(str(parent["run_id"])) == manifest_before


def test_interim_parallel_evaluate_advances_without_stage_tree_mutation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, store, parent, authority = _parallel_loop_wave(
        tmp_path, monkeypatch)
    manifest_before = copy.deepcopy(store.load(str(parent["run_id"])))
    emitted = loop.wave(
        str(workspace), root_observation_authority=authority)
    assert "error" not in emitted, emitted
    assert not (loop.load(str(workspace)) or {}).get("_stage_bindings")
    monkeypatch.setattr(loop, "_task_dod_errors", lambda *_a, **_k: [])
    monkeypatch.setattr(
        loop.runtime_storage, "refresh_task_worktree_tip",
        lambda *_a, **_k: {"branch_tip": _git(workspace, "rev-parse", "HEAD")})

    built = loop.gate.__wrapped__(
        str(workspace), "pass", task_id="t-left")

    assert "error" not in built, built
    assert "stage_transition" not in built
    evaluating = loop.load(str(workspace))
    assert not evaluating.get("_stage_bindings")
    evaluating["step"] = "evaluate"
    evaluating["current_task"] = 0
    loop.save(str(workspace), evaluating)
    task = evaluating["tasks"][0]
    evaluation_workspace = task.get("workspace") or str(workspace)
    _write_reanchorable_pass(evaluation_workspace, task)
    monkeypatch.setattr(loop, "_evaluation_errors", lambda *_a, **_k: [])
    monkeypatch.setattr(
        loop.tp, "engine_skew_refusal", lambda *_a, **_k: None)

    evaluated = loop.gate.__wrapped__(str(workspace), "pass")

    assert "error" not in evaluated, evaluated
    assert "stage_transition" not in evaluated
    final_state = loop.load(str(workspace))
    assert final_state["step"] == "execute"
    assert final_state["tasks"][0]["status"] == "passed"
    assert final_state["tasks"][1]["status"] == "pending"
    assert not final_state.get("_stage_bindings")
    assert store.load(str(parent["run_id"])) == manifest_before


@pytest.mark.parametrize("field", sorted(FORBIDDEN_RUNTIME_FIELDS))
def test_host_adapter_cannot_add_predecessor_runtime_context(field: str) -> None:
    dispatch, _stage = _dispatch()
    hostile = copy.deepcopy(dispatch)
    hostile["startup"][field] = {"private": True}

    with pytest.raises(taskplane_lite.StageDispatchError):
        taskplane_lite.stage_startup_bytes(hostile)


@pytest.mark.parametrize(
    "host_environment",
    [
        {"CODEX_HOME": "/host/codex"},
        {"CLAUDE_SESSION_ID": "claude-session"},
        {"TASKPLANE_SLACK_CAPABLE": "1"},
        {"TASKPLANE_MANAGED_HOOK_POLICY": "supported"},
        {"TASKPLANE_STORE": "repo"},
    ],
)
def test_host_identity_never_silently_enables_stage_native_mutation(
    host_environment: dict[str, str],
) -> None:
    assert taskplane_lite.stage_native_mode(host_environment) == "disabled"
    assert taskplane_lite.stage_native_enabled(host_environment) is False

    explicitly_enabled = dict(host_environment)
    explicitly_enabled[taskplane_lite.STAGE_NATIVE_ENV] = "new-run"
    assert taskplane_lite.stage_native_mode(explicitly_enabled) == "new-run"
    assert taskplane_lite.stage_native_enabled(explicitly_enabled) is True
