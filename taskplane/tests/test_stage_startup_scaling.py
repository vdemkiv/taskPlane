"""Successor startup cost is independent of predecessor runtime history."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys

import pytest


TASKPLANE_DIR = Path(__file__).resolve().parents[1]
if str(TASKPLANE_DIR) not in sys.path:
    sys.path.insert(0, str(TASKPLANE_DIR))

import runtime_eval  # noqa: E402
import stage_entities  # noqa: E402
import stage_handoff  # noqa: E402
import taskplane_lite  # noqa: E402


_HOSTILE_MARKERS = (
    "hostile-agent-private-state",
    "hostile-conversation-private-state",
    "hostile-log-private-state",
    "hostile-transcript-private-state",
    "hostile-lease-private-state",
    "hostile-runtime-private-state",
    "hostile-secret-private-state",
)


def _reference(kind: str, marker: str, size: int) -> dict[str, object]:
    fingerprint = marker * 64
    return {
        "schema": "taskplane.artifact-reference/v1",
        "kind": kind,
        "fingerprint": fingerprint,
        "digest": fingerprint,
        "bytes": size,
        "locator": f"artifact://{kind}/{fingerprint}",
        "transport": "artifact-reference",
    }


def _authority() -> dict[str, object]:
    return {
        "schema": "taskplane.stage-authority-binding/v1",
        "run_id": "run-r0004-scaling",
        "repository_id": "github.com/example/taskplane",
        "repository_key": "github.com-example-taskplane",
        "worktree_id": "t05-scaling-worktree",
        "target_revision": "1" * 40,
        "worktree_revision": "2" * 40,
        "requirement_id": "R-0004",
        "requirement_revision": "4",
        "design_revision": "2",
        "design_fingerprint": "c" * 64,
        "actor": "human:vdemkiv",
        "session_id": "codex-thread-scaling",
        "authority_revision": 7,
        "authority_fingerprint": "d" * 64,
    }


def _dispatch_material() -> tuple[
        dict[str, object], dict[str, object], dict[str, object]]:
    selected = [
        _reference("design", "f", 256),
        _reference("source", "9", 1024),
    ]
    handoff: dict[str, object] = {
        "schema": "taskplane.stage-handoff/v1",
        "producer": {"stage_id": "stage-build-001", "outcome": "done"},
        "requirement": {
            "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
        },
        "design": {"revision": "2", "fingerprint": "c" * 64},
        "target": None,
        "commit": None,
        "contracts": {
            "provided": ["contract:stage-artifact-handoff"],
            "consumed": [],
            "changed": [],
        },
        "deliverables": ["build-commit"],
        "evidence_references": [_reference("test-evidence", "e", 128)],
        "selected_artifacts": copy.deepcopy(selected),
        "exclusions": sorted(stage_handoff.REQUIRED_EXCLUSIONS),
        "authorization": {
            "actor": "human:vdemkiv",
            "session_id": "codex-thread-scaling",
            "authorized_at": "2026-08-21T14:00:00Z",
            "operation_id": "handoff-build-evaluate",
            "authority_record": {
                "schema": "taskplane.authority-record-reference/v1",
                "authority_schema":
                    "taskplane.consolidated-authorization/v1",
                "revision": 7,
                "fingerprint": "d" * 64,
            },
            "nonconsumable_reuse": None,
        },
    }
    handoff["fingerprint"] = stage_handoff.manifest_fingerprint(handoff)
    manifest_fingerprint = str(handoff["fingerprint"])
    stage = stage_entities.create_stage(
        run_id="run-r0004-scaling",
        stage_id="stage-evaluate-scaling",
        requirement={
            "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
        },
        design={"revision": "2", "fingerprint": "c" * 64},
        stage_kind="evaluate",
        parent_stage_ids=[],
        predecessor_stage_ids=["stage-build-001"],
        input_manifest_ref={
            "schema": "taskplane.artifact-reference/v1",
            "kind": "stage-handoff",
            "fingerprint": manifest_fingerprint,
            "digest": manifest_fingerprint,
            "bytes": len(taskplane_lite.canonical_json_bytes(handoff)),
            "locator": f"artifact://stage-handoff/{manifest_fingerprint}",
            "transport": "artifact-reference",
        },
        execution_root_id="execution-stage-evaluate-scaling",
        deliverables=["evaluation-verdict"],
        selected_artifacts=selected,
        budget={"token_limit": 8_000, "attempt_limit": 3},
        dependencies=["t03-isolated-stage-dispatch-and-cli"],
        contracts=["contract:stage-artifact-handoff"],
        authority=_authority(),
        created_at="2026-08-21T14:05:00Z",
    )
    payload = taskplane_lite.canonical_json_bytes(stage) + b"\n"
    result = {
        "head": {
            "object": {
                "schema": "taskplane.stage-object-ref/v1",
                "stage_id": stage["stage_id"],
                "fingerprint": stage["fingerprint"],
                "digest": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "locator": (
                    f"stages/objects/{stage['stage_id']}/"
                    f"{stage['fingerprint']}.json"),
            },
            "summary": stage_entities.bounded_stage_summary(stage),
        },
    }
    receipt: dict[str, object] = {
        "schema": "taskplane.stage-operation-receipt/v1",
        "operation_id": "start-stage-scaling",
        "request_fingerprint": "a" * 64,
        "operation": "start_stage",
        "stage_ids": [stage["stage_id"]],
        "committed_revision": 10,
        "result": result,
        "result_fingerprint": hashlib.sha256(
            taskplane_lite.canonical_json_bytes(result)).hexdigest(),
    }
    return stage, handoff, receipt


class _PredecessorFixture:
    """Keep hostile predecessor runtime beside, never inside, dispatch data."""

    def __init__(self, irrelevant_event_count: int) -> None:
        hostile_event = {
            "agents": _HOSTILE_MARKERS[0],
            "conversations": _HOSTILE_MARKERS[1],
            "logs": _HOSTILE_MARKERS[2],
            "transcripts": _HOSTILE_MARKERS[3],
            "leases": _HOSTILE_MARKERS[4],
            "runtime": _HOSTILE_MARKERS[5],
            "secrets": _HOSTILE_MARKERS[6],
        }
        # Shared immutable test values make the 100,000-event case cheap while
        # retaining the exact history cardinality the scaling contract names.
        self.irrelevant_events = [hostile_event] * irrelevant_event_count
        self.stage, self.handoff, self.receipt = _dispatch_material()
        self.calls = {
            "dispatch": 0,
            "handoff_reads": 0,
            "selected_reference_reads": 0,
            "predecessor_root_opens": 0,
        }

    def dispatch(self) -> dict[str, object]:
        self.calls["dispatch"] += 1
        self.calls["handoff_reads"] += 1
        selected = self.stage["selected_artifacts"]
        assert isinstance(selected, list)
        self.calls["selected_reference_reads"] += len(selected)
        return taskplane_lite.stage_runtime_dispatch(
            self.stage, self.receipt, self.handoff, selected)


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key for child in value.values() for key in _nested_keys(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in _nested_keys(child)}
    return set()


def _measure(fixture: _PredecessorFixture,
             serializer_calls: list[int]) -> dict[str, object]:
    before = serializer_calls[0]
    dispatch = fixture.dispatch()
    serialized = taskplane_lite.stage_startup_bytes(dispatch)
    projection = runtime_eval.stage_startup_projection(dispatch)
    return {
        "dispatch": dispatch,
        "serialized": serialized,
        "projection": projection,
        "serializer_calls": serializer_calls[0] - before,
        "fixture_calls": copy.deepcopy(fixture.calls),
    }


def test_successor_startup_is_constant_for_ten_or_one_hundred_thousand_events(
        monkeypatch: pytest.MonkeyPatch) -> None:
    small = _PredecessorFixture(10)
    large = _PredecessorFixture(100_000)
    assert len(small.irrelevant_events) == 10
    assert len(large.irrelevant_events) == 100_000

    original_canonical = taskplane_lite.canonical_json_bytes
    serializer_calls = [0]

    def counted_canonical(value: object) -> bytes:
        serializer_calls[0] += 1
        return original_canonical(value)

    monkeypatch.setattr(
        taskplane_lite, "canonical_json_bytes", counted_canonical)
    small_measurement = _measure(small, serializer_calls)
    large_measurement = _measure(large, serializer_calls)

    small_dispatch = small_measurement["dispatch"]
    large_dispatch = large_measurement["dispatch"]
    assert isinstance(small_dispatch, dict)
    assert isinstance(large_dispatch, dict)
    small_projection = small_measurement["projection"]
    large_projection = large_measurement["projection"]
    assert isinstance(small_projection, dict)
    assert isinstance(large_projection, dict)

    assert small_measurement["serialized"] == large_measurement["serialized"]
    assert small_dispatch["startup_sha256"] == \
        large_dispatch["startup_sha256"]
    assert small_projection == large_projection
    assert small_projection == {
        "schema": "taskplane.stage-startup-projection/v1",
        "startup_sha256": small_dispatch["startup_sha256"],
        "manifest_bytes": small.stage["input_manifest_ref"]["bytes"],
        "startup_bytes": small_dispatch["telemetry"]["startup_bytes"],
        "startup_token_estimate":
            small_dispatch["telemetry"]["startup_tokens"],
        "selected_ref_count": 2,
        "selected_ref_bytes": 1280,
        "predecessor_root_opens": 0,
    }
    assert small_measurement["serializer_calls"] == \
        large_measurement["serializer_calls"]
    assert small_measurement["fixture_calls"] == \
        large_measurement["fixture_calls"] == {
            "dispatch": 1,
            "handoff_reads": 1,
            "selected_reference_reads": 2,
            "predecessor_root_opens": 0,
        }

    manifest_bytes = len(original_canonical(small.handoff))
    all_references = (small.handoff["evidence_references"] +
                      small.handoff["selected_artifacts"])
    assert manifest_bytes == small_projection["manifest_bytes"] \
        <= stage_handoff.MAX_MANIFEST_BYTES
    assert len(all_references) <= stage_handoff.MAX_ARTIFACT_REFERENCES
    assert small_dispatch["telemetry"]["predecessor_root_opens"] == 0
    assert large_dispatch["telemetry"]["predecessor_root_opens"] == 0

    startup = small_dispatch["startup"]
    assert not ({
        "agents", "conversations", "logs", "transcripts", "leases",
        "runtime", "secrets",
    } & _nested_keys(startup))
    startup_bytes = small_measurement["serialized"]
    assert isinstance(startup_bytes, bytes)
    for marker in _HOSTILE_MARKERS:
        assert marker.encode("utf-8") not in startup_bytes


def test_runtime_projection_uses_verified_startup_bytes_without_mutation(
        monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _PredecessorFixture(10)
    dispatch = fixture.dispatch()
    before = copy.deepcopy(dispatch)
    original_startup_bytes = taskplane_lite.stage_startup_bytes
    original_canonical = taskplane_lite.canonical_json_bytes
    calls = {"stage_startup_bytes": 0, "canonical_json_bytes": 0}

    def counted_startup_bytes(value: dict[str, object]) -> bytes:
        calls["stage_startup_bytes"] += 1
        return original_startup_bytes(value)

    def counted_canonical(value: object) -> bytes:
        calls["canonical_json_bytes"] += 1
        return original_canonical(value)

    monkeypatch.setattr(
        taskplane_lite, "stage_startup_bytes", counted_startup_bytes)
    monkeypatch.setattr(
        taskplane_lite, "canonical_json_bytes", counted_canonical)

    projection = runtime_eval.stage_startup_projection(dispatch)

    assert calls == {"stage_startup_bytes": 1, "canonical_json_bytes": 2}
    assert dispatch == before
    assert dispatch["telemetry"] == before["telemetry"]
    assert projection["startup_sha256"] == dispatch["startup_sha256"]


@pytest.mark.parametrize("tamper", [
    "malformed-telemetry",
    "boolean-byte-count",
    "mismatched-startup-bytes",
    "mismatched-selected-ref-bytes",
])
def test_runtime_projection_rejects_malformed_or_mismatched_telemetry(
        tamper: str) -> None:
    dispatch = _PredecessorFixture(10).dispatch()
    if tamper == "malformed-telemetry":
        dispatch["telemetry"] = []
    elif tamper == "boolean-byte-count":
        dispatch["telemetry"]["startup_bytes"] = True
    elif tamper == "mismatched-startup-bytes":
        dispatch["telemetry"]["startup_bytes"] += 1
    else:
        dispatch["telemetry"]["selected_ref_bytes"] += 1

    with pytest.raises(taskplane_lite.StageDispatchError,
                       match="stage startup telemetry mismatch"):
        runtime_eval.stage_startup_projection(dispatch)
