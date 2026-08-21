"""Cross-host consumers preserve one bounded stage startup contract."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from taskplane import taskplane_lite, views
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
    return HostSurfaceSnapshot.create(
        workflow_id="stage-workflow-r0004",
        run_id="run-r0004",
        target="github.com/example/taskplane@" + "1" * 40,
        revision="1" * 40,
        sequence=1,
        stage="evaluate",
        state="active",
        values={"stage_runtime": dispatch},
        evidence=(f"sha256:{dispatch['startup_sha256']}",),
        safe_actions=("inspect",),
    )


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
