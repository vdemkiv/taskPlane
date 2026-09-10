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














@pytest.mark.parametrize("field", sorted(FORBIDDEN_RUNTIME_FIELDS))
def test_host_adapter_cannot_add_predecessor_runtime_context(field: str) -> None:
    dispatch, _stage = _dispatch()
    hostile = copy.deepcopy(dispatch)
    hostile["startup"][field] = {"private": True}

    with pytest.raises(taskplane_lite.StageDispatchError):
        taskplane_lite.stage_startup_bytes(hostile)




def _git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=workspace, text=True, capture_output=True,
        encoding="utf-8", errors="replace", check=True)
    return result.stdout.strip()


def _real_pristine_run(
        tmp_path: Path, *, git_factory=None,
        ) -> tuple[Path, run_store.RunStore, dict[str, object]]:
    """Create only public run infrastructure, never stage/handoff objects."""
    workspace = tmp_path / "pristine-loop-workspace"
    if git_factory is None:
        workspace.mkdir()
        _git(workspace, "init", "-q", "-b", "main")
    else:
        git_factory(workspace)
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
