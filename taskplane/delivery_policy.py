"""Closed delivery-mode and zero-lens execution receipt contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

if __package__:
    from .delivery_ports import content_fingerprint
else:  # pragma: no cover - exercised by isolated legacy-import subprocess
    from delivery_ports import content_fingerprint


DELIVERY_MODE_RECEIPT_SCHEMA = "taskplane.delivery-mode-receipt/v1"
EMPTY_LENS_COLLECTION_SCHEMA = "taskplane.empty-lens-collection/v1"
EXECUTION_ZERO_LENS_AUTHORIZATION_SCHEMA = \
    "taskplane.execution-zero-lens-authorization/v1"
EXECUTION_STAGE_ORIGIN_SCHEMA = "taskplane.execution-stage-origin/v1"
STAGE_LENS_EXECUTION_RECEIPT_SCHEMA = \
    "taskplane.stage-lens-execution-receipt/v1"
DELIVERY_MODES = frozenset({"build", "review", "design"})
AUTOMATIC_LENS_MODES = frozenset({"design"})
# ``EXECUTION_STAGES`` is retained for the v1 zero-lens authorization reader.
# New delivery decisions use the closed routed/zero split below so Evaluate is
# never accidentally treated as an editing-time zero-lens stage.
EXECUTION_STAGES = frozenset({"build", "fix", "evaluate", "em"})
ROUTED_LENS_STAGES = frozenset({"product", "design", "plan", "evaluate"})
ZERO_LENS_STAGES = frozenset({"build", "fix", "em"})
DELIVERY_STAGES = ROUTED_LENS_STAGES | ZERO_LENS_STAGES
TERMINAL_OUTCOMES = frozenset({
    "passed", "failed", "cancelled", "interrupted", "handed_off",
})


class DeliveryPolicyError(ValueError):
    """A Plan or collection value violates the closed delivery policy."""


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeliveryPolicyError(f"{field} is required")
    return value


def _fingerprint(value: Any, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    text = _required_text(value, field)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise DeliveryPolicyError(f"{field} must be a lowercase SHA-256 fingerprint")
    return text


def _source_sha(value: Any) -> str:
    text = _required_text(value, "source_sha")
    if len(text) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise DeliveryPolicyError("source_sha must be an exact lowercase Git SHA")
    return text


def _lens_ids(value: Any, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DeliveryPolicyError(f"{field} must be a collection")
    lenses = tuple(_required_text(item, field) for item in value)
    if len(lenses) != len(set(lenses)):
        raise DeliveryPolicyError(f"{field} contains duplicate lens ids")
    return lenses


def _seal(projection: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(projection)
    sealed["fingerprint"] = content_fingerprint(sealed)
    return sealed


def _execution_stage(value: Any, field: str = "stage") -> str:
    text = _required_text(value, field).strip().lower()
    compact = "".join(character for character in text if character.isalnum())
    if compact == "executiontimeem":
        return "em"
    if compact not in EXECUTION_STAGES:
        raise DeliveryPolicyError(
            f"{field} must be build, fix, evaluate, or em"
        )
    return compact


def _delivery_stage(value: Any, field: str = "stage") -> str:
    text = _required_text(value, field).strip().lower()
    compact = "".join(character for character in text if character.isalnum())
    if compact == "executiontimeem":
        compact = "em"
    if compact not in DELIVERY_STAGES:
        raise DeliveryPolicyError(
            f"{field} must be product, design, plan, build, fix, evaluate, "
            "or em"
        )
    return compact


def _observation_rows(value: Any, field: str) -> tuple[dict[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DeliveryPolicyError(f"{field} must be a collection")
    rows = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise DeliveryPolicyError(f"{field}[{index}] must be a mapping")
        rows.append(dict(row))
    return tuple(rows)


def _normalized_event(row: Mapping[str, Any], field: str) -> str:
    value = next((row.get(key) for key in (
        "hook_event_name", "event_name", "event", "lifecycle", "status"
    ) if row.get(key) is not None), None)
    text = _required_text(value, field)
    return "".join(character for character in text.lower()
                   if character.isalnum())


def _row_stage(row: Mapping[str, Any], field: str) -> str:
    value = next((row.get(key) for key in (
        "stage", "loop_stage", "taskplane_stage"
    ) if row.get(key) is not None), None)
    text = _required_text(value, field).strip().lower()
    compact = "".join(character for character in text if character.isalnum())
    return "em" if compact == "executiontimeem" else compact


def _role_identities(row: Mapping[str, Any]) -> tuple[str, ...]:
    values = []
    for key in (
        "role_marker", "role", "agent_type", "subagent_type", "task_name"
    ):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip().lower())
    return tuple(values)


def _is_taskplane_lens_role(identities: Sequence[str]) -> bool:
    exact = {
        "lens", "tp-lens", "taskplane:tp-lens",
        "taskplane-role:lens", "taskplane-role:tp-lens",
    }
    return any(
        value in exact or value.startswith("tp-lens-") or
        value.startswith("taskplane:tp-lens-") or
        value == "tp_lens" or value.startswith("tp_lens_") or
        value == "taskplane:tp_lens" or
        value.startswith("taskplane:tp_lens_")
        for value in identities
    )


def _lifecycle_kind_and_outcome(
    row: Mapping[str, Any], field: str
) -> tuple[str, str | None]:
    event = _normalized_event(row, field)
    if event in {"subagentstart", "start", "started", "active", "running"}:
        return "start", None

    outcomes = {
        "subagentstop": "passed",
        "stop": "passed",
        "stopped": "passed",
        "complete": "passed",
        "completed": "passed",
        "success": "passed",
        "succeeded": "passed",
        "pass": "passed",
        "passed": "passed",
        "subagentfailed": "failed",
        "failure": "failed",
        "fail": "failed",
        "failed": "failed",
        "error": "failed",
        "errored": "failed",
        "subagentcancelled": "cancelled",
        "subagentcanceled": "cancelled",
        "cancel": "cancelled",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "subagentinterrupted": "interrupted",
        "interrupt": "interrupted",
        "interrupted": "interrupted",
        "subagenthandedoff": "handed_off",
        "handoff": "handed_off",
        "handedoff": "handed_off",
        "transferred": "handed_off",
    }
    outcome = outcomes.get(event)
    if outcome is None:
        raise DeliveryPolicyError(f"{field} has no recognized lifecycle state")

    # Native hosts may use a generic stop hook with a more precise terminal
    # result beside it. Preserve that result instead of misreporting success.
    if event in {"subagentstop", "stop", "stopped"}:
        explicit = next((row.get(key) for key in (
            "terminal_outcome", "outcome", "result", "status"
        ) if row.get(key) is not None), None)
        if isinstance(explicit, str) and explicit.strip():
            explicit_key = "".join(
                character for character in explicit.lower()
                if character.isalnum()
            )
            explicit_outcome = outcomes.get(explicit_key)
            if explicit_outcome is not None:
                outcome = explicit_outcome
    return "terminal", outcome


def _origin_identity(
    row: Mapping[str, Any], field: str
) -> tuple[str, str, str, str]:
    """Return the closed native origin shared by trace and session ledger."""
    run_id = _required_text(row.get("run_id"), f"{field}.run_id")
    session_id = next((row.get(key) for key in (
        "session_id", "host_session_id", "thread_id"
    ) if row.get(key) is not None), None)
    session_id = _required_text(session_id, f"{field}.session_id")
    task_name = _required_text(row.get("task_name"), f"{field}.task_name")
    agent_id = _required_text(row.get("agent_id"), f"{field}.agent_id")
    return run_id, session_id, task_name, agent_id


def create_execution_stage_origin_receipt(
    *,
    stage: str,
    run_id: str,
    session_id: str,
    task_name: str,
    agent_id: str,
    dispatch_identity_fingerprint: str,
) -> dict[str, Any]:
    """Seal the upstream dispatch identity expected in host observations."""
    return _seal({
        "schema": EXECUTION_STAGE_ORIGIN_SCHEMA,
        "contract": "contract:delivery.execution-zero-lens",
        "stage": _delivery_stage(stage),
        "run_id": _required_text(run_id, "run_id"),
        "session_id": _required_text(session_id, "session_id"),
        "task_name": _required_text(task_name, "task_name"),
        "agent_id": _required_text(agent_id, "agent_id"),
        "dispatch_identity_fingerprint": _fingerprint(
            dispatch_identity_fingerprint, "dispatch_identity_fingerprint"
        ),
    })


def validate_execution_stage_origin_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a closed origin authority supplied by native dispatch."""
    if not isinstance(receipt, Mapping):
        raise DeliveryPolicyError(
            "sealed expected execution-stage origin receipt is required"
        )
    fields = {
        "schema", "contract", "stage", "run_id", "session_id",
        "task_name", "agent_id", "dispatch_identity_fingerprint",
        "fingerprint",
    }
    if set(receipt) != fields:
        raise DeliveryPolicyError(
            "execution-stage origin receipt fields are not closed"
        )
    if receipt.get("schema") != EXECUTION_STAGE_ORIGIN_SCHEMA:
        raise DeliveryPolicyError("execution-stage origin receipt schema is invalid")
    if receipt.get("contract") != "contract:delivery.execution-zero-lens":
        raise DeliveryPolicyError(
            "execution-stage origin receipt contract is invalid"
        )
    normalized = create_execution_stage_origin_receipt(
        stage=receipt.get("stage"),
        run_id=receipt.get("run_id"),
        session_id=receipt.get("session_id"),
        task_name=receipt.get("task_name"),
        agent_id=receipt.get("agent_id"),
        dispatch_identity_fingerprint=receipt.get(
            "dispatch_identity_fingerprint"
        ),
    )
    if receipt.get("fingerprint") != normalized["fingerprint"]:
        raise DeliveryPolicyError(
            "execution-stage origin receipt fingerprint mismatch"
        )
    return normalized


def _expected_origin_terminal_outcome(
    rows: Sequence[Mapping[str, Any]],
    *,
    stage: str,
    expected_origin: tuple[str, str, str, str],
    field: str,
) -> str:
    starts = 0
    outcomes: set[str] = set()
    for index, row in enumerate(rows):
        row_stage = _row_stage(row, f"{field}[{index}].stage")
        if row_stage != stage:
            continue
        origin = _origin_identity(row, f"{field}[{index}]")
        if origin != expected_origin:
            continue
        lifecycle, outcome = _lifecycle_kind_and_outcome(
            row, f"{field}[{index}].event"
        )
        if lifecycle == "start":
            starts += 1
        elif outcome is not None:
            outcomes.add(outcome)
    if starts < 1 or len(outcomes) != 1:
        raise DeliveryPolicyError(
            f"{field} requires complete, origin-bound start/terminal "
            f"evidence for {stage}"
        )
    return next(iter(outcomes))


def _lens_worker_starts(
    rows: Sequence[Mapping[str, Any]], *, stage: str, field: str
) -> frozenset[tuple[str, str, str, str]]:
    starts: set[tuple[str, str, str, str]] = set()
    for index, row in enumerate(rows):
        if _row_stage(row, f"{field}[{index}].stage") != stage:
            continue
        identities = _role_identities(row)
        if not identities or not _is_taskplane_lens_role(identities):
            continue
        lifecycle, _outcome = _lifecycle_kind_and_outcome(
            row, f"{field}[{index}].event"
        )
        if lifecycle == "start":
            starts.add(_origin_identity(row, f"{field}[{index}]"))
    return frozenset(starts)


def _has_lens_worker_observation(
    rows: Sequence[Mapping[str, Any]], *, stage: str, field: str
) -> bool:
    for index, row in enumerate(rows):
        if _row_stage(row, f"{field}[{index}].stage") != stage:
            continue
        identities = _role_identities(row)
        if identities and _is_taskplane_lens_role(identities):
            _lifecycle_kind_and_outcome(row, f"{field}[{index}].event")
            return True
    return False


def validate_stage_lens_execution(
    *,
    stage: str,
    native_trace: Sequence[Mapping[str, Any]],
    session_ledger: Sequence[Mapping[str, Any]],
    expected_origin_receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate the routed/zero-lens boundary against native lifecycle data.

    Product, Design, Plan, and Evaluate may contain focused lens starts.
    Build, Fix, and EM fail closed on any such start. Every stage attempt must
    carry matching terminal evidence for success, failure, cancellation,
    interruption, or handoff in both native sources.
    """
    normalized_stage = _delivery_stage(stage)
    expected = validate_execution_stage_origin_receipt(expected_origin_receipt)
    if expected["stage"] != normalized_stage:
        raise DeliveryPolicyError(
            "expected execution-stage origin does not match current stage"
        )
    trusted_origin = (
        expected["run_id"], expected["session_id"],
        expected["task_name"], expected["agent_id"],
    )
    trace_rows = _observation_rows(native_trace, "native_trace")
    ledger_rows = _observation_rows(session_ledger, "session_ledger")
    trace_outcome = _expected_origin_terminal_outcome(
        trace_rows, stage=normalized_stage, expected_origin=trusted_origin,
        field="native_trace",
    )
    ledger_outcome = _expected_origin_terminal_outcome(
        ledger_rows, stage=normalized_stage, expected_origin=trusted_origin,
        field="session_ledger",
    )
    if trace_outcome != ledger_outcome:
        raise DeliveryPolicyError(
            "native_trace and session_ledger terminal outcomes do not match"
        )

    trace_lenses = _lens_worker_starts(
        trace_rows, stage=normalized_stage, field="native_trace"
    )
    ledger_lenses = _lens_worker_starts(
        ledger_rows, stage=normalized_stage, field="session_ledger"
    )
    if trace_lenses != ledger_lenses:
        raise DeliveryPolicyError(
            "native_trace and session_ledger lens worker starts do not match"
        )
    zero_stage_lens_observation = (
        normalized_stage in ZERO_LENS_STAGES and (
            _has_lens_worker_observation(
                trace_rows, stage=normalized_stage, field="native_trace"
            ) or _has_lens_worker_observation(
                ledger_rows, stage=normalized_stage, field="session_ledger"
            )
        )
    )
    if zero_stage_lens_observation:
        raise DeliveryPolicyError(
            "Taskplane lens worker start is forbidden; lens worker "
            f"observation found in {normalized_stage}"
        )

    return _seal({
        "schema": STAGE_LENS_EXECUTION_RECEIPT_SCHEMA,
        "contract": "contract:delivery.stage-lens-execution",
        "stage": normalized_stage,
        "lens_execution_policy": (
            "focused" if normalized_stage in ROUTED_LENS_STAGES else "none"
        ),
        "terminal_outcome": trace_outcome,
        "expected_origin_fingerprint": expected["fingerprint"],
        "lens_worker_start_count": len(trace_lenses),
        "native_trace_fingerprint": content_fingerprint(list(trace_rows)),
        "session_ledger_fingerprint": content_fingerprint(list(ledger_rows)),
        "status": "observed",
    })


def validate_stage_lens_execution_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a closed routed/zero-lens lifecycle receipt."""
    if not isinstance(receipt, Mapping):
        raise DeliveryPolicyError("stage lens execution receipt must be a mapping")
    fields = {
        "schema", "contract", "stage", "lens_execution_policy",
        "terminal_outcome", "expected_origin_fingerprint",
        "lens_worker_start_count", "native_trace_fingerprint",
        "session_ledger_fingerprint", "status", "fingerprint",
    }
    if set(receipt) != fields:
        raise DeliveryPolicyError(
            "stage lens execution receipt fields are not closed"
        )
    if receipt.get("schema") != STAGE_LENS_EXECUTION_RECEIPT_SCHEMA:
        raise DeliveryPolicyError("stage lens execution receipt schema is invalid")
    if receipt.get("contract") != "contract:delivery.stage-lens-execution":
        raise DeliveryPolicyError("stage lens execution receipt contract is invalid")
    stage = _delivery_stage(receipt.get("stage"))
    policy = receipt.get("lens_execution_policy")
    expected_policy = "focused" if stage in ROUTED_LENS_STAGES else "none"
    if policy != expected_policy:
        raise DeliveryPolicyError("stage lens execution policy is invalid")
    if receipt.get("terminal_outcome") not in TERMINAL_OUTCOMES:
        raise DeliveryPolicyError("stage terminal outcome is invalid")
    _fingerprint(
        receipt.get("expected_origin_fingerprint"),
        "expected_origin_fingerprint",
    )
    worker_count = receipt.get("lens_worker_start_count")
    if isinstance(worker_count, bool) or not isinstance(worker_count, int) or \
            worker_count < 0:
        raise DeliveryPolicyError("lens worker start count must be non-negative")
    if stage in ZERO_LENS_STAGES and worker_count != 0:
        raise DeliveryPolicyError(
            f"stage lens execution receipt contains lens starts for {stage}"
        )
    _fingerprint(
        receipt.get("native_trace_fingerprint"), "native_trace_fingerprint"
    )
    _fingerprint(
        receipt.get("session_ledger_fingerprint"),
        "session_ledger_fingerprint",
    )
    if receipt.get("status") != "observed":
        raise DeliveryPolicyError("stage lens execution status must be observed")
    projection = {key: receipt[key] for key in fields - {"fingerprint"}}
    if receipt.get("fingerprint") != content_fingerprint(projection):
        raise DeliveryPolicyError(
            "stage lens execution receipt fingerprint mismatch"
        )
    return dict(receipt)


def _complete_stage_origins(
    rows: Sequence[Mapping[str, Any]], *, stage: str, field: str
) -> frozenset[tuple[str, str, str, str]]:
    """Require matching start/terminal observations for the current stage."""
    start_events = {"subagentstart", "start", "started", "active", "running"}
    terminal_events = {
        "subagentstop", "stop", "stopped", "complete", "completed",
        "success", "succeeded", "pass", "passed",
    }
    starts: set[tuple[str, str, str, str]] = set()
    terminals: set[tuple[str, str, str, str]] = set()
    current_rows = 0
    for index, row in enumerate(rows):
        row_stage = _row_stage(row, f"{field}[{index}].stage")
        if row_stage != stage:
            continue
        current_rows += 1
        event = _normalized_event(row, f"{field}[{index}].event")
        origin = _origin_identity(row, f"{field}[{index}]")
        if event in start_events:
            starts.add(origin)
        elif event in terminal_events:
            terminals.add(origin)
        else:
            raise DeliveryPolicyError(
                f"{field}[{index}] has no recognized lifecycle state"
            )
    if not current_rows:
        raise DeliveryPolicyError(
            f"{field} has no current-stage evidence for {stage}"
        )
    if not starts or starts != terminals:
        raise DeliveryPolicyError(
            f"{field} requires complete, origin-bound start/terminal "
            f"evidence for {stage}"
        )
    return frozenset(starts)


def _refuse_execution_lens_starts(
    rows: Sequence[Mapping[str, Any]], field: str
) -> None:
    """Reject any observed Taskplane lens start in an execution stage."""
    for index, row in enumerate(rows):
        event = _normalized_event(row, f"{field}[{index}].event")
        if event not in {
            "subagentstart", "start", "started", "active", "running",
            "subagentstop", "stop", "stopped", "complete", "completed",
        }:
            continue
        stage = _row_stage(row, f"{field}[{index}].stage")
        identities = _role_identities(row)
        if not identities:
            raise DeliveryPolicyError(
                f"{field}[{index}] start role identity is required"
            )
        if stage in EXECUTION_STAGES and _is_taskplane_lens_role(identities):
            raise DeliveryPolicyError(
                f"Taskplane lens worker start is forbidden in {stage}"
            )


def authorize_execution_stage(
    *,
    stage: str,
    delivery_mode_receipt: Mapping[str, Any],
    expected_lenses: Sequence[str],
    native_trace: Sequence[Mapping[str, Any]],
    session_ledger: Sequence[Mapping[str, Any]],
    lens_worker_factory: Callable[[str], Any],
    expected_origin_receipt: Mapping[str, Any] | None = None,
    outage_fallback: bool = False,
) -> dict[str, Any]:
    """Seal zero-lens authority before execution-stage gate success.

    The function only validates host observations and policy.  It deliberately
    never calls ``lens_worker_factory`` and therefore cannot acquire worker or
    host lifecycle authority.
    """
    normalized_stage = _execution_stage(stage)
    receipt = validate_delivery_mode_receipt(delivery_mode_receipt)
    expected_origin = validate_execution_stage_origin_receipt(
        expected_origin_receipt
    )
    if expected_origin["stage"] != normalized_stage:
        raise DeliveryPolicyError(
            "expected execution-stage origin does not match current stage"
        )
    expected = _lens_ids(expected_lenses, "expected_lenses")
    if receipt["mode"] != "build" or receipt["automatic_lenses"] != []:
        raise DeliveryPolicyError(
            "execution stage requires build mode with automatic_lenses=[]"
        )
    if expected:
        raise DeliveryPolicyError(
            "execution stage requires expected_lenses=[]"
        )
    if not isinstance(outage_fallback, bool):
        raise DeliveryPolicyError("outage_fallback must be boolean")
    if outage_fallback:
        raise DeliveryPolicyError(
            "zero-lens execution forbids outage fallback"
        )
    if not callable(lens_worker_factory):
        raise DeliveryPolicyError("lens_worker_factory must be callable")

    trace_rows = _observation_rows(native_trace, "native_trace")
    ledger_rows = _observation_rows(session_ledger, "session_ledger")
    _refuse_execution_lens_starts(trace_rows, "native_trace")
    _refuse_execution_lens_starts(ledger_rows, "session_ledger")
    trace_origins = _complete_stage_origins(
        trace_rows, stage=normalized_stage, field="native_trace"
    )
    ledger_origins = _complete_stage_origins(
        ledger_rows, stage=normalized_stage, field="session_ledger"
    )
    if trace_origins != ledger_origins:
        raise DeliveryPolicyError(
            "native_trace and session_ledger current-stage origins do not match"
        )
    trusted_origin = (
        expected_origin["run_id"], expected_origin["session_id"],
        expected_origin["task_name"], expected_origin["agent_id"],
    )
    if trace_origins != frozenset({trusted_origin}):
        raise DeliveryPolicyError(
            "current-stage observations do not match sealed expected origin"
        )

    return _seal({
        "schema": EXECUTION_ZERO_LENS_AUTHORIZATION_SCHEMA,
        "contract": "contract:delivery.execution-zero-lens",
        "stage": normalized_stage,
        "delivery_mode_receipt_fingerprint": receipt["fingerprint"],
        "expected_origin_fingerprint": expected_origin["fingerprint"],
        "expected_lenses": [],
        "automatic_lens_workers": [],
        "automatic_lens_worker_count": 0,
        "native_trace_fingerprint": content_fingerprint(list(trace_rows)),
        "session_ledger_fingerprint": content_fingerprint(list(ledger_rows)),
        "status": "authorized",
    })


def validate_execution_stage_authorization(
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the closed authorization consumed by shared integration."""
    if not isinstance(authorization, Mapping):
        raise DeliveryPolicyError(
            "execution zero-lens authorization must be a mapping"
        )
    fields = {
        "schema", "contract", "stage",
        "delivery_mode_receipt_fingerprint", "expected_origin_fingerprint",
        "expected_lenses",
        "automatic_lens_workers", "automatic_lens_worker_count",
        "native_trace_fingerprint", "session_ledger_fingerprint", "status",
        "fingerprint",
    }
    if set(authorization) != fields:
        raise DeliveryPolicyError(
            "execution zero-lens authorization fields are not closed"
        )
    if authorization.get("schema") != \
            EXECUTION_ZERO_LENS_AUTHORIZATION_SCHEMA:
        raise DeliveryPolicyError(
            "execution zero-lens authorization schema is invalid"
        )
    if authorization.get("contract") != \
            "contract:delivery.execution-zero-lens":
        raise DeliveryPolicyError(
            "execution zero-lens authorization contract is invalid"
        )
    _execution_stage(authorization.get("stage"))
    _fingerprint(
        authorization.get("delivery_mode_receipt_fingerprint"),
        "delivery_mode_receipt_fingerprint",
    )
    _fingerprint(
        authorization.get("expected_origin_fingerprint"),
        "expected_origin_fingerprint",
    )
    if _lens_ids(authorization.get("expected_lenses"), "expected_lenses"):
        raise DeliveryPolicyError(
            "execution zero-lens authorization contains expected lenses"
        )
    if _lens_ids(
        authorization.get("automatic_lens_workers"),
        "automatic_lens_workers",
    ):
        raise DeliveryPolicyError(
            "execution zero-lens authorization contains lens workers"
        )
    if authorization.get("automatic_lens_worker_count") != 0:
        raise DeliveryPolicyError(
            "execution zero-lens authorization worker count must be zero"
        )
    _fingerprint(
        authorization.get("native_trace_fingerprint"),
        "native_trace_fingerprint",
    )
    _fingerprint(
        authorization.get("session_ledger_fingerprint"),
        "session_ledger_fingerprint",
    )
    if authorization.get("status") != "authorized":
        raise DeliveryPolicyError(
            "execution zero-lens authorization status must be authorized"
        )
    projection = {key: authorization[key] for key in fields - {"fingerprint"}}
    if authorization.get("fingerprint") != content_fingerprint(projection):
        raise DeliveryPolicyError(
            "execution zero-lens authorization fingerprint mismatch"
        )
    return dict(authorization)


def validate_plan_mode(
    plan: Mapping[str, Any],
    *,
    plan_fingerprint: str,
    source_sha: str,
    predecessor_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Normalize a Plan declaration into a closed delivery-mode receipt."""
    if not isinstance(plan, Mapping):
        raise DeliveryPolicyError("Plan must be a mapping")
    mode = plan.get("delivery_mode")
    if mode not in DELIVERY_MODES:
        raise DeliveryPolicyError("delivery mode must be build, review, or design")
    lenses = _lens_ids(plan.get("automatic_lenses"), "automatic_lenses")
    if mode not in AUTOMATIC_LENS_MODES and lenses:
        raise DeliveryPolicyError(f"{mode} delivery mode forbids automatic lenses")
    return _seal(
        {
            "schema": DELIVERY_MODE_RECEIPT_SCHEMA,
            "requirement": _required_text(plan.get("requirement"), "requirement"),
            "plan_fingerprint": _fingerprint(plan_fingerprint, "plan_fingerprint"),
            "mode": mode,
            "automatic_lenses": list(lenses),
            "plan_authority": _required_text(
                plan.get("plan_authority"), "plan_authority"
            ),
            "source_sha": _source_sha(source_sha),
            "predecessor_fingerprint": _fingerprint(
                predecessor_fingerprint, "predecessor_fingerprint", optional=True
            ),
        }
    )


def validate_delivery_mode_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact v1 receipt shape and its content fingerprint."""
    if not isinstance(receipt, Mapping):
        raise DeliveryPolicyError("delivery-mode receipt must be a mapping")
    fields = {
        "schema",
        "requirement",
        "plan_fingerprint",
        "mode",
        "automatic_lenses",
        "plan_authority",
        "source_sha",
        "predecessor_fingerprint",
        "fingerprint",
    }
    if set(receipt) != fields:
        raise DeliveryPolicyError("delivery-mode receipt fields are not closed")
    if receipt.get("schema") != DELIVERY_MODE_RECEIPT_SCHEMA:
        raise DeliveryPolicyError("delivery-mode receipt schema is invalid")
    normalized = validate_plan_mode(
        {
            "requirement": receipt.get("requirement"),
            "delivery_mode": receipt.get("mode"),
            "automatic_lenses": receipt.get("automatic_lenses"),
            "plan_authority": receipt.get("plan_authority"),
        },
        plan_fingerprint=receipt.get("plan_fingerprint"),
        source_sha=receipt.get("source_sha"),
        predecessor_fingerprint=receipt.get("predecessor_fingerprint"),
    )
    if receipt.get("fingerprint") != normalized["fingerprint"]:
        raise DeliveryPolicyError("delivery-mode receipt fingerprint mismatch")
    return normalized


def automatic_lens_workers_for_dispatch(
    receipt: Mapping[str, Any], lens_worker_factory: Callable[[str], Any]
) -> tuple[Any, ...]:
    """Validate policy before constructing any automatic lens worker."""
    normalized = validate_delivery_mode_receipt(receipt)
    lenses = tuple(normalized["automatic_lenses"])
    if normalized["mode"] == "build" and lenses:
        raise DeliveryPolicyError("build delivery mode forbids automatic lenses")
    if lenses and not callable(lens_worker_factory):
        raise DeliveryPolicyError("lens_worker_factory must be callable")
    return tuple(lens_worker_factory(lens) for lens in lenses)


def create_empty_lens_collection_receipt(
    *,
    run_id: str,
    task_id: str,
    stage: str,
    expected_lenses: Sequence[str],
    collected_lenses: Sequence[str],
    result: Mapping[str, Any],
    result_validator: Callable[[dict[str, Any]], Any],
    producer_observation_fingerprint: str,
    predecessor_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Record normal success for a validated result with no expected lenses."""
    expected = _lens_ids(expected_lenses, "expected_lenses")
    collected = _lens_ids(collected_lenses, "collected_lenses")
    if expected or collected:
        raise DeliveryPolicyError(
            "empty collection requires empty expected and collected lenses"
        )
    if not isinstance(result, Mapping):
        raise DeliveryPolicyError("result must be a mapping")
    if not callable(result_validator):
        raise DeliveryPolicyError("result_validator must be callable")
    validation_input = dict(result)
    validated_result = result_validator(validation_input)
    if validated_result is None:
        validated_result = validation_input
    if not isinstance(validated_result, Mapping):
        raise DeliveryPolicyError("result validator must return a mapping or None")
    return _seal(
        {
            "schema": EMPTY_LENS_COLLECTION_SCHEMA,
            "run_id": _required_text(run_id, "run_id"),
            "task_id": _required_text(task_id, "task_id"),
            "stage": _required_text(stage, "stage"),
            "expected_lenses": [],
            "collected_lenses": [],
            "result_fingerprint": content_fingerprint(dict(validated_result)),
            "producer_observation_fingerprint": _fingerprint(
                producer_observation_fingerprint,
                "producer_observation_fingerprint",
            ),
            "status": "complete",
            "predecessor_fingerprint": _fingerprint(
                predecessor_fingerprint, "predecessor_fingerprint", optional=True
            ),
        }
    )


def validate_empty_lens_collection_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a closed successful empty-collection receipt."""
    if not isinstance(receipt, Mapping):
        raise DeliveryPolicyError("empty-collection receipt must be a mapping")
    fields = {
        "schema",
        "run_id",
        "task_id",
        "stage",
        "expected_lenses",
        "collected_lenses",
        "result_fingerprint",
        "producer_observation_fingerprint",
        "status",
        "predecessor_fingerprint",
        "fingerprint",
    }
    if set(receipt) != fields:
        raise DeliveryPolicyError("empty-collection receipt fields are not closed")
    if receipt.get("schema") != EMPTY_LENS_COLLECTION_SCHEMA:
        raise DeliveryPolicyError("empty-collection receipt schema is invalid")
    _required_text(receipt.get("run_id"), "run_id")
    _required_text(receipt.get("task_id"), "task_id")
    _required_text(receipt.get("stage"), "stage")
    if _lens_ids(receipt.get("expected_lenses"), "expected_lenses") or _lens_ids(
        receipt.get("collected_lenses"), "collected_lenses"
    ):
        raise DeliveryPolicyError("empty-collection receipt contains lenses")
    _fingerprint(receipt.get("result_fingerprint"), "result_fingerprint")
    _fingerprint(
        receipt.get("producer_observation_fingerprint"),
        "producer_observation_fingerprint",
    )
    _fingerprint(
        receipt.get("predecessor_fingerprint"),
        "predecessor_fingerprint",
        optional=True,
    )
    if receipt.get("status") != "complete":
        raise DeliveryPolicyError("empty-collection receipt status must be complete")
    projection = {key: receipt[key] for key in fields - {"fingerprint"}}
    if receipt.get("fingerprint") != content_fingerprint(projection):
        raise DeliveryPolicyError("empty-collection receipt fingerprint mismatch")
    return dict(receipt)
