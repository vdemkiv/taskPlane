"""Cross-host consumers preserve one bounded stage startup contract."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess

import pytest

from taskplane import (
    loop,
    review_evidence,
    run_store,
    stage_entities,
    stage_handoff,
    storage,
    taskplane_lite,
    views,
)
from taskplane.host_capabilities import Observation, negotiate_host_surfaces
from taskplane.host_native import HostSurfaceSnapshot
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
    "input_handoff",
    "selected_artifacts",
    "budget",
    "execution_claim",
    "attempt_id",
    "declared_scope",
}
FORBIDDEN_RUNTIME_FIELDS = {
    "agents",
    "conversations",
    "event_logs",
    "tool_transcripts",
    "leases",
    "runtime_state",
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


def _git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=workspace, text=True, capture_output=True,
        check=True)
    return result.stdout.strip()


def _real_loop_stage(
        tmp_path: Path,
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
    store = run_store.RunStore(home=str(tmp_path / "home"))
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
        stage_id="stage-product-cross-host",
        requirement=handoff["requirement"],
        design=handoff["design"],
        stage_kind="product",
        parent_stage_ids=[],
        predecessor_stage_ids=[],
        input_manifest_ref=handoff_reference,
        execution_root_id="execution-stage-product-cross-host",
        deliverables=["product-decision"],
        selected_artifacts=[selected],
        budget={"token_limit": 4_000, "attempt_limit": 2},
        dependencies=[],
        contracts=["contract:stage-artifact-handoff"],
        authority=authority,
        created_at="2026-08-21T14:05:00Z",
    )
    assert initial["revision"] == 1
    return workspace, store, stage


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
    assert startup["authority"] == stage["authority"]
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


def test_real_loop_journey_emits_one_bounded_dispatch_on_every_host(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, store, stage = _real_loop_stage(tmp_path)
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "new-run")

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
