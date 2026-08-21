"""Stage dispatch consumes only bounded stage-native runtime results."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from taskplane import stage_entities, stage_handoff, taskplane_lite
from taskplane import tp as cli


def _reference(kind: str, marker: str, size: int = 128) -> dict[str, object]:
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
        "run_id": "run-r0004",
        "repository_id": "github.com/example/taskplane",
        "repository_key": "github.com-example-taskplane",
        "worktree_id": "t03-worktree",
        "target_revision": "1" * 40,
        "worktree_revision": "2" * 40,
        "requirement_id": "R-0004",
        "requirement_revision": "4",
        "design_revision": "2",
        "design_fingerprint": "c" * 64,
        "actor": "human:vdemkiv",
        "session_id": "codex-thread-1",
        "authority_revision": 7,
        "authority_fingerprint": "d" * 64,
    }


def _handoff(selected: list[dict[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {
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
            "consumed": [], "changed": [],
        },
        "deliverables": ["build-commit"],
        "evidence_references": [_reference("test-evidence", "e")],
        "selected_artifacts": copy.deepcopy(selected),
        "exclusions": sorted(stage_handoff.REQUIRED_EXCLUSIONS),
        "authorization": {
            "actor": "human:vdemkiv",
            "session_id": "codex-thread-1",
            "authorized_at": "2026-08-21T14:00:00Z",
            "operation_id": "handoff-build-evaluate",
            "authority_record": {
                "schema": "taskplane.authority-record-reference/v1",
                "authority_schema": "taskplane.consolidated-authorization/v1",
                "revision": 7,
                "fingerprint": "d" * 64,
            },
            "nonconsumable_reuse": None,
        },
    }
    value["fingerprint"] = stage_handoff.manifest_fingerprint(value)
    return value


def _stage_and_handoff() -> tuple[dict[str, object], dict[str, object]]:
    selected = [
        _reference("design", "f", 256),
        _reference("source", "9", 1024),
    ]
    handoff = _handoff(selected)
    manifest_fingerprint = str(handoff["fingerprint"])
    stage = stage_entities.create_stage(
        run_id="run-r0004",
        stage_id="stage-evaluate-001",
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
        execution_root_id="execution-stage-evaluate-001",
        deliverables=["evaluation-verdict"],
        selected_artifacts=selected,
        budget={"token_limit": 8_000, "attempt_limit": 3},
        dependencies=["t03-isolated-stage-dispatch-and-cli"],
        contracts=["contract:stage-artifact-handoff"],
        authority=_authority(),
        created_at="2026-08-21T14:05:00Z",
    )
    return stage, handoff


def _root_stage_and_handoff() -> tuple[dict[str, object], dict[str, object]]:
    _successor, handoff = _stage_and_handoff()
    manifest_fingerprint = str(handoff["fingerprint"])
    stage = stage_entities.create_stage(
        run_id="run-r0004",
        stage_id="stage-product-root-001",
        requirement={
            "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
        },
        design={"revision": "2", "fingerprint": "c" * 64},
        stage_kind="product",
        parent_stage_ids=[],
        predecessor_stage_ids=[],
        input_manifest_ref={
            "schema": "taskplane.artifact-reference/v1",
            "kind": "stage-handoff",
            "fingerprint": manifest_fingerprint,
            "digest": manifest_fingerprint,
            "bytes": len(taskplane_lite.canonical_json_bytes(handoff)),
            "locator": f"artifact://stage-handoff/{manifest_fingerprint}",
            "transport": "artifact-reference",
        },
        execution_root_id="execution-stage-product-root-001",
        deliverables=["product-decision"],
        selected_artifacts=handoff["selected_artifacts"],
        budget={"token_limit": 8_000, "attempt_limit": 3},
        dependencies=[],
        contracts=["contract:stage-artifact-handoff"],
        authority=_authority(),
        created_at="2026-08-21T14:05:00Z",
    )
    return stage, handoff


def _receipt(stage: dict[str, object], *, operation: str = "start_stage",
             attempt_id: str | None = None) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema": "taskplane.stage-operation-receipt/v1",
        "operation_id": f"{operation}-001",
        "request_fingerprint": "a" * 64,
        "operation": operation,
        "stage_ids": [stage["stage_id"]],
        "committed_revision": 10,
    }
    if operation == "start_stage":
        result = {"head": _committed_head(stage)}
    elif operation == "terminalize_and_start":
        receipt["stage_ids"] = sorted([
            "stage-predecessor-001", str(stage["stage_id"]),
        ])
        result = {"successor_head": _committed_head(stage)}
    elif operation == "split_stage":
        receipt["stage_ids"] = sorted([
            "stage-parent-001", str(stage["stage_id"]),
        ])
        result = {"child_heads": {
            str(stage["stage_id"]): _committed_head(stage),
        }}
    else:
        assert operation == "resume_stage"
        assert attempt_id is not None
        result = {
            "stage_id": stage["stage_id"],
            "attempt_id": attempt_id,
            "execution_root_id": stage["execution_root_id"],
            "claim": {
                "schema": "taskplane.stage-execution-attempt-claim/v1",
                "run_id": stage["run_id"],
                "stage_id": stage["stage_id"],
                "execution_root_id": stage["execution_root_id"],
                "attempt_id": attempt_id,
            },
            "stage_fingerprint": stage["fingerprint"],
        }
    receipt["result"] = result
    _refresh_receipt_result(receipt)
    return receipt


def _committed_head(stage: dict[str, object]) -> dict[str, object]:
    payload = taskplane_lite.canonical_json_bytes(stage) + b"\n"
    stage_id = str(stage["stage_id"])
    fingerprint = str(stage["fingerprint"])
    return {
        "object": {
            "schema": "taskplane.stage-object-ref/v1",
            "stage_id": stage_id,
            "fingerprint": fingerprint,
            "digest": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "locator": f"stages/objects/{stage_id}/{fingerprint}.json",
        },
        "summary": stage_entities.bounded_stage_summary(stage),
    }


def _refresh_receipt_result(receipt: dict[str, object]) -> None:
    receipt["result_fingerprint"] = hashlib.sha256(
        taskplane_lite.canonical_json_bytes(receipt["result"])).hexdigest()


def _dispatch(stage: dict[str, object], handoff: dict[str, object],
              receipt: dict[str, object]) -> dict[str, object]:
    attempt_id = None
    if receipt["operation"] == "resume_stage":
        result = receipt.get("result")
        if isinstance(result, dict) and "attempt_id" in result:
            attempt_id = str(result["attempt_id"])
    return taskplane_lite.stage_runtime_dispatch(
        stage, receipt, handoff, stage["selected_artifacts"],
        attempt_id=attempt_id)


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            nested for child in value.values() for nested in _all_keys(child)
        }
    if isinstance(value, list):
        return {nested for child in value for nested in _all_keys(child)}
    return set()


def test_stage_runtime_dispatch_is_deterministic_selected_only_and_bounded() \
        -> None:
    stage, handoff = _stage_and_handoff()
    receipt = _receipt(stage)
    scope = {
        "scope_paths": ["taskplane/tp.py"],
        "out_of_scope_paths": ["taskplane/track.py"],
    }

    first = taskplane_lite.stage_runtime_dispatch(
        stage, receipt, handoff, stage["selected_artifacts"],
        declared_scope=scope)
    second = taskplane_lite.stage_runtime_dispatch(
        copy.deepcopy(stage), copy.deepcopy(receipt), copy.deepcopy(handoff),
        copy.deepcopy(stage["selected_artifacts"]), declared_scope=scope)

    assert first == second
    assert taskplane_lite.stage_startup_bytes(first) == \
        taskplane_lite.stage_startup_bytes(second)
    assert first["startup_sha256"] == hashlib.sha256(
        taskplane_lite.stage_startup_bytes(first)).hexdigest()
    authority_reference = first["startup"]["authority"]
    assert authority_reference == {
        "schema": "taskplane.stage-authority-reference/v1",
        "fingerprint": hashlib.sha256(taskplane_lite.canonical_json_bytes(
            stage["authority"])).hexdigest(),
    }
    projected_handoff = first["startup"]["input_handoff"]
    assert projected_handoff["schema"] == \
        "taskplane.stage-handoff-dispatch/v1"
    assert projected_handoff["source_fingerprint"] == handoff["fingerprint"]
    assert projected_handoff["authorization"] == authority_reference
    projection_payload = copy.deepcopy(projected_handoff)
    projection_fingerprint = projection_payload.pop("fingerprint")
    assert projection_fingerprint == hashlib.sha256(
        taskplane_lite.canonical_json_bytes(projection_payload)).hexdigest()
    assert first["startup"]["selected_artifacts"] == \
        stage["selected_artifacts"]
    assert first["telemetry"] == {
        "startup_bytes": len(taskplane_lite.stage_startup_bytes(first)),
        "startup_tokens": (
            len(taskplane_lite.stage_startup_bytes(first)) + 3) // 4,
        "selected_ref_count": 2,
        "selected_ref_bytes": 1280,
        "predecessor_root_opens": 0,
    }
    assert first["telemetry"]["startup_bytes"] <= \
        taskplane_lite.MAX_STAGE_STARTUP_BYTES
    assert not ({
        "agents", "conversations", "events", "tool_transcripts", "leases",
        "meters", "active_contract", "runtime_environment", "workspace",
        "path", "root", "actor", "session_id",
    } & _all_keys(first["startup"]))
    startup_bytes = taskplane_lite.stage_startup_bytes(first)
    assert b"human:vdemkiv" not in startup_bytes
    assert b"codex-thread-1" not in startup_bytes
    # Privacy projection is non-mutating: attributable proof stays available
    # to the local verification boundary and its persisted inputs.
    assert stage["authority"]["actor"] == "human:vdemkiv"
    assert stage["authority"]["session_id"] == "codex-thread-1"
    assert handoff["authorization"]["actor"] == "human:vdemkiv"
    assert handoff["authorization"]["session_id"] == "codex-thread-1"


@pytest.mark.parametrize("tamper", ["authority", "handoff-authority",
                                     "handoff-fingerprint"])
def test_stage_startup_bytes_rejects_tampered_privacy_projection(
        tamper: str) -> None:
    stage, handoff = _stage_and_handoff()
    dispatch = _dispatch(stage, handoff, _receipt(stage))
    if tamper == "authority":
        dispatch["startup"]["authority"]["fingerprint"] = "0" * 64
    elif tamper == "handoff-authority":
        dispatch["startup"]["input_handoff"]["authorization"][
            "fingerprint"] = "0" * 64
    else:
        dispatch["startup"]["input_handoff"]["fingerprint"] = "0" * 64
    serialized = taskplane_lite.canonical_json_bytes(dispatch["startup"])
    dispatch["startup_sha256"] = hashlib.sha256(serialized).hexdigest()
    dispatch["telemetry"]["startup_bytes"] = len(serialized)
    dispatch["telemetry"]["startup_tokens"] = (len(serialized) + 3) // 4

    with pytest.raises(taskplane_lite.StageDispatchError):
        taskplane_lite.stage_startup_bytes(dispatch)


@pytest.mark.parametrize("tamper", ["receipt-stage", "receipt-fingerprint",
                                     "handoff-context", "selected-mismatch"])
def test_stage_runtime_dispatch_rejects_unverified_or_hostile_context(
        tamper: str) -> None:
    stage, handoff = _stage_and_handoff()
    receipt = _receipt(stage)
    selected = copy.deepcopy(stage["selected_artifacts"])
    if tamper == "receipt-stage":
        receipt["stage_ids"] = ["stage-other-001"]
    elif tamper == "receipt-fingerprint":
        receipt["request_fingerprint"] = "not-a-fingerprint"
    elif tamper == "handoff-context":
        handoff["predecessor_conversations"] = ["hidden"]
    else:
        selected.pop()

    with pytest.raises(taskplane_lite.StageDispatchError):
        taskplane_lite.stage_runtime_dispatch(
            stage, receipt, handoff, selected)


@pytest.mark.parametrize("operation", [
    "start_stage", "terminalize_and_start", "split_stage", "resume_stage",
])
def test_dispatchable_operations_require_a_bounded_result(
        operation: str) -> None:
    stage, handoff = _stage_and_handoff()
    receipt = _receipt(
        stage, operation=operation,
        attempt_id="attempt-001" if operation == "resume_stage" else None)
    receipt.pop("result")
    receipt.pop("result_fingerprint")

    with pytest.raises(taskplane_lite.StageDispatchError,
                       match="no bounded result"):
        _dispatch(stage, handoff, receipt)


def _operation_head(result: dict[str, object], operation: str,
                    stage_id: str) -> tuple[dict[str, object], str]:
    if operation == "start_stage":
        return result, "head"
    if operation == "terminalize_and_start":
        return result, "successor_head"
    child_heads = result["child_heads"]
    assert isinstance(child_heads, dict)
    return child_heads, stage_id


@pytest.mark.parametrize("operation", [
    "start_stage", "terminalize_and_start", "split_stage",
])
@pytest.mark.parametrize("tamper", ["missing", "malformed", "wrong"])
def test_dispatch_rejects_missing_malformed_or_wrong_operation_head(
        operation: str, tamper: str) -> None:
    stage, handoff = _stage_and_handoff()
    receipt = _receipt(stage, operation=operation)
    result = receipt["result"]
    assert isinstance(result, dict)
    container, key = _operation_head(result, operation, str(stage["stage_id"]))
    if tamper == "missing":
        container.pop(key)
    elif tamper == "malformed":
        container[key] = {"object": "not-a-reference", "summary": {}}
    else:
        wrong = copy.deepcopy(_committed_head(stage))
        wrong["object"]["fingerprint"] = "0" * 64
        container[key] = wrong
    _refresh_receipt_result(receipt)

    with pytest.raises(taskplane_lite.StageDispatchError,
                       match="committed head does not match stage"):
        _dispatch(stage, handoff, receipt)


@pytest.mark.parametrize("operation", [
    "start_stage", "terminalize_and_start", "split_stage",
])
def test_dispatch_rejects_same_id_head_for_an_altered_aggregate(
        operation: str) -> None:
    stage, handoff = _stage_and_handoff()
    receipt = _receipt(stage, operation=operation)
    altered = copy.deepcopy(stage)
    altered["budget"]["token_limit"] = 8_001
    altered["fingerprint"] = stage_entities.stage_fingerprint(altered)
    altered = stage_entities.validate_stage(altered)
    result = receipt["result"]
    assert isinstance(result, dict)
    container, key = _operation_head(result, operation, str(stage["stage_id"]))
    container[key] = _committed_head(altered)
    _refresh_receipt_result(receipt)

    with pytest.raises(taskplane_lite.StageDispatchError,
                       match="committed head does not match stage"):
        _dispatch(stage, handoff, receipt)


@pytest.mark.parametrize("mismatch", [
    "actor", "session_id", "authority_revision", "authority_fingerprint",
])
def test_dispatch_binds_handoff_authorization_to_stage_authority(
        mismatch: str) -> None:
    stage, handoff = _stage_and_handoff()
    authorization = handoff["authorization"]
    assert isinstance(authorization, dict)
    authority_record = authorization["authority_record"]
    assert isinstance(authority_record, dict)
    if mismatch == "actor":
        authorization["actor"] = "human:other"
    elif mismatch == "session_id":
        authorization["session_id"] = "codex-thread-other"
    elif mismatch == "authority_revision":
        authority_record["revision"] = 8
    else:
        authority_record["fingerprint"] = "e" * 64
    handoff["fingerprint"] = stage_handoff.manifest_fingerprint(handoff)
    manifest_fingerprint = str(handoff["fingerprint"])
    input_reference = stage["input_manifest_ref"]
    assert isinstance(input_reference, dict)
    input_reference.update({
        "fingerprint": manifest_fingerprint,
        "digest": manifest_fingerprint,
        "bytes": len(taskplane_lite.canonical_json_bytes(handoff)),
        "locator": f"artifact://stage-handoff/{manifest_fingerprint}",
    })
    stage["fingerprint"] = stage_entities.stage_fingerprint(stage)
    stage = stage_entities.validate_stage(stage)
    receipt = _receipt(stage)

    with pytest.raises(taskplane_lite.StageDispatchError,
                       match="does not match stage authority"):
        _dispatch(stage, handoff, receipt)


def test_resume_gets_a_fresh_attempt_claim_under_the_same_stage_root() -> None:
    stage, handoff = _stage_and_handoff()
    first = taskplane_lite.stage_runtime_dispatch(
        stage, _receipt(stage, operation="resume_stage",
                        attempt_id="attempt-001"),
        handoff, stage["selected_artifacts"], attempt_id="attempt-001")
    second = taskplane_lite.stage_runtime_dispatch(
        stage, _receipt(stage, operation="resume_stage",
                        attempt_id="attempt-002"),
        handoff, stage["selected_artifacts"], attempt_id="attempt-002")

    first_claim = first["startup"]["execution_claim"]
    second_claim = second["startup"]["execution_claim"]
    assert first_claim["attempt_id"] != second_claim["attempt_id"]
    assert first_claim["execution_root_id"] == \
        second_claim["execution_root_id"] == stage["execution_root_id"]
    assert first["startup"]["stage_id"] == second["startup"]["stage_id"]
    assert first["startup"]["input_handoff"] == \
        second["startup"]["input_handoff"]
    assert first["startup_sha256"] != second["startup_sha256"]


def test_terminal_stage_cannot_be_dispatched_or_resumed() -> None:
    stage, handoff = _stage_and_handoff()
    terminal = stage_entities.terminalize_stage(
        stage, outcome="closed", actor="human:vdemkiv",
        terminalized_at="2026-08-21T15:00:00Z",
        reason_code="complete", reason="No further evaluation is required.")

    with pytest.raises(taskplane_lite.StageDispatchError,
                       match="only an active stage"):
        taskplane_lite.stage_runtime_dispatch(
            terminal,
            _receipt(terminal, operation="resume_stage",
                     attempt_id="attempt-terminal"),
            handoff, terminal["selected_artifacts"],
            attempt_id="attempt-terminal")


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, "disabled"),
        ({"TASKPLANE_STAGE_NATIVE": "true"}, "disabled"),
        ({"TASKPLANE_STAGE_NATIVE": "new-run"}, "new-run"),
        ({"TASKPLANE_STAGE_NATIVE": "enabled"}, "enabled"),
    ],
)
def test_stage_native_rollout_mode_is_explicit_and_fail_closed(
        environment: dict[str, str], expected: str) -> None:
    assert taskplane_lite.stage_native_mode(environment) == expected
    assert taskplane_lite.stage_native_enabled(environment) is \
        (expected != "disabled")


def test_loop_dispatch_consumes_the_verified_receipt_at_runtime_boundary() \
        -> None:
    import loop

    stage, handoff = _stage_and_handoff()
    receipt = _receipt(stage)

    class Store:
        def load(self, run_id: str) -> dict[str, object]:
            assert run_id == stage["run_id"]
            return {
                "stage_heads": {
                    "stage-build-001": {"object": {"fingerprint": "e" * 64}},
                },
            }

        def read_stage_object(self, run_id: str,
                              _reference: dict[str, object]) \
                -> dict[str, object]:
            assert run_id == stage["run_id"]
            return {"stage_id": "stage-build-001", "outcome": "done"}

    class Lifecycle:
        def _read_handoff(self, reference: dict[str, object], *, producer,
                          consumer) -> dict[str, object]:
            assert reference == stage["input_manifest_ref"]
            assert producer["stage_id"] == "stage-build-001"
            assert consumer == stage
            return handoff

    dispatch = loop._stage_dispatch(Store(), Lifecycle(), receipt, stage)

    assert dispatch == taskplane_lite.stage_runtime_dispatch(
        stage, receipt, handoff, stage["selected_artifacts"])
    assert dispatch["telemetry"]["predecessor_root_opens"] == 0


def test_loop_stage_command_refuses_disabled_legacy_mutation_but_reads_history(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import loop
    import run_store

    class Store:
        def load(self, run_id: str) -> dict[str, object]:
            assert run_id == "legacy-run"
            return {"schema": "taskplane.run/v3", "run_id": run_id}

    monkeypatch.setattr(run_store, "RunStore", Store)
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)

    refused = loop.stage_command(
        "/repo", "start", {"stage": {"run_id": "legacy-run"}})
    history = loop.stage_command(
        "/repo", "history", {"run_id": "legacy-run", "limit": 10})

    assert refused == {
        "schema": "taskplane.stage-command-result/v1",
        "command": "start",
        "run_id": "legacy-run",
        "enabled": False,
        "legacy": True,
        "error": "stage-native mutation is disabled",
    }
    assert history == {
        "schema": "taskplane.stage-history-page/v1",
        "run_id": "legacy-run",
        "legacy": True,
        "stages": [],
        "lineage": [],
        "next_cursor": None,
    }


def test_loop_stage_command_allows_an_explicit_new_run_canary(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import loop
    import run_store

    stage, handoff_value = _root_stage_and_handoff()
    receipt = _receipt(stage)

    class Store:
        def load(self, run_id: str) -> dict[str, object]:
            assert run_id == stage["run_id"]
            return {"schema": "taskplane.run/v3", "run_id": run_id}

    class Lifecycle:
        def _read_handoff(self, *_args, **_kwargs) -> dict[str, object]:
            return handoff_value

        def start_stage(self, candidate: dict[str, object], **kwargs) \
                -> dict[str, object]:
            assert candidate == stage
            assert kwargs["expected_revision"] == 3
            assert kwargs["operation_id"] == "start-canary-001"
            assert kwargs["expected_predecessor_fingerprints"] == {}
            return receipt

    dispatch = {"schema": "taskplane.stage-dispatch/v1", "bounded": True}
    monkeypatch.setattr(run_store, "RunStore", Store)
    monkeypatch.setattr(
        loop, "_stage_lifecycle",
        lambda *_args: (stage_entities, Lifecycle()))
    monkeypatch.setattr(
        loop, "_verified_stage_handoff",
        lambda *_args: handoff_value)
    monkeypatch.setattr(loop, "_stage_dispatch", lambda *_args, **_kwargs: dispatch)
    monkeypatch.setattr(loop, "load", lambda _workspace: None)
    monkeypatch.setenv("TASKPLANE_STAGE_NATIVE", "new-run")

    result = loop.stage_command("/repo", "start", {
        "schema": "taskplane.stage-command/v1",
        "stage": stage,
        "expected_revision": 3,
        "operation_id": "start-canary-001",
        "expected_predecessor_fingerprints": {},
        "authority": stage["authority"],
    })

    assert result == {
        "schema": "taskplane.stage-command-result/v1",
        "command": "start",
        "run_id": stage["run_id"],
        "receipt": receipt,
        "dispatch": dispatch,
    }


def test_cli_preserves_verified_receipt_and_bounded_startup_telemetry(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import loop

    request = {
        "schema": "taskplane.stage-command/v1",
        "run_id": "run-r0004",
        "operation_id": "start-evaluate-001",
        "expected_revision": 9,
        "receipt": {
            "schema": "taskplane.stage-operation-receipt/v1",
            "operation_id": "handoff-build-evaluate",
            "request_fingerprint": "a" * 64,
        },
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    result = {
        "schema": "taskplane.stage-command-result/v1",
        "command": "start",
        "receipt": request["receipt"],
        "dispatch": {
            "stage_id": "stage-evaluate-001",
            "attempt_id": "attempt-001",
            "execution_root_id": "execution-stage-evaluate-001",
            "startup": {
                "schema": "taskplane.stage-startup/v1",
                "manifest_bytes": 4096,
                "selected_artifact_count": 3,
                "selected_artifact_bytes": 12000,
                "startup_bytes": 4608,
                "startup_tokens": 1152,
                "predecessor_execution_tree_opens": 0,
            },
        },
    }
    monkeypatch.setattr(
        loop, "stage_command", lambda *_args: result, raising=False)

    assert cli.main([
        "stage", "--workspace", str(tmp_path), "start",
        "--request", str(request_path),
    ]) == 0

    emitted = json.loads(capsys.readouterr().out)
    assert emitted == result
    assert emitted["receipt"] == request["receipt"]
    assert emitted["dispatch"]["startup"] == result["dispatch"]["startup"]
    assert not ({
        "agents", "conversations", "events", "tool_transcripts", "leases",
        "meters", "active_contract", "runtime_environment",
    } & set(emitted["dispatch"]))


def test_loop_next_and_wave_task_emission_remain_byte_identical(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """Adding `tp stage` must not wrap or reshape legacy task emission."""
    import loop

    payloads = {
        "next": {"step": "evaluate", "brief": "unchanged"},
        "wave": {"step": "execute", "wave": [{"id": "t03"}]},
    }
    monkeypatch.setattr(cli, "_enforcement_check", lambda *_a, **_k: (None, None))
    monkeypatch.setattr(loop, "load", lambda _ws: None)
    monkeypatch.setattr(loop, "next_action", lambda _ws, rid=None: payloads["next"])
    monkeypatch.setattr(loop, "wave", lambda _ws: payloads["wave"])
    monkeypatch.setattr(cli, "_record_parallel_expectations", lambda *_a: None)

    assert cli.main([
        "loop", "--workspace", str(tmp_path), "next", "--emit", "task",
    ]) == 0
    next_bytes = capsys.readouterr().out
    assert next_bytes == json.dumps(payloads["next"], indent=2) + "\n"

    assert cli.main([
        "loop", "--workspace", str(tmp_path), "wave", "--emit", "task",
    ]) == 0
    wave_bytes = capsys.readouterr().out
    assert wave_bytes == json.dumps(payloads["wave"], indent=2) + "\n"
