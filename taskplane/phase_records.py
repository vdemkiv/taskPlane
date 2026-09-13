"""Phase operation records owned by the run aggregate."""
from __future__ import annotations
from typing import TYPE_CHECKING
import re
from typing import Any
from collections.abc import Callable, Mapping
import copy
import re
from typing import Any
if TYPE_CHECKING or __package__:
    from .primitives import content_fingerprint as _fingerprint
else:
    from primitives import content_fingerprint as _fingerprint

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")

class PhaseRecordError(ValueError):
    """A phase operation or its current routing cannot be verified."""

def phase_records(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate phase receipts held by the existing run manifest owner."""
    rows = manifest.get("phase_records", {})
    if not isinstance(rows, dict) or len(rows) > 10000:
        raise PhaseRecordError("invalid phase receipt index")
    for key, row in rows.items():
        if not isinstance(row, dict) or set(row) != {"schema", "operation_id", "operation",
                "request_fingerprint", "result", "result_fingerprint", "committed_revision"} or \
                row["schema"] != "taskplane.phase-operation-receipt/v1" or row["operation_id"] != key or \
                row["operation"] not in {"phase_routing", "phase_prepare", "phase_collect", "phase_retry", "resource_policy"} or \
                row["result_fingerprint"] != _fingerprint(row["result"]) or \
                not isinstance(row["request_fingerprint"], str) or not _FINGERPRINT.fullmatch(row["request_fingerprint"]) or \
                type(row["committed_revision"]) is not int or not 1 < row["committed_revision"] <= manifest["revision"]:
            raise PhaseRecordError("phase receipt does not verify")
    return copy.deepcopy(rows)


def commit_phase_record(store: Any, run_id: str, *,
        expected_revision: int, operation_id: str, operation: str,
        request_fingerprint: str, result: dict[str, Any],
        validate_authority: Callable[[dict[str, Any]], object]) -> dict[str, Any]:
    """Revision-CAS non-lifecycle data through RunStore's general commit API.

    No stage receipt is forged: lifecycle state and its journal stay owned by
    commit_stage_operation. A losing CAS cannot publish a second owner.
    """
    current = store.load(run_id)
    validate_authority(current)
    rows = phase_records(current)
    prior = rows.get(operation_id)
    if prior is not None:
        if prior["operation"] != operation or prior["request_fingerprint"] != request_fingerprint or prior["result"] != result:
            raise PhaseRecordError("phase operation replay changed")
        return prior
    row = {"schema": "taskplane.phase-operation-receipt/v1", "operation_id": operation_id,
        "operation": operation, "request_fingerprint": request_fingerprint,
        "result": copy.deepcopy(result), "result_fingerprint": _fingerprint(result),
        "committed_revision": expected_revision + 1}
    rows[operation_id] = row
    phase_records({"phase_records": rows, "revision": expected_revision + 1})
    store.commit(run_id, expected_revision=expected_revision, changes={"phase_records": rows})
    return copy.deepcopy(row)


def phase_routing(manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read the single latest atomic routing receipt from the incumbent journal.

    Attempts retain their original preparation receipt. A rollback changes
    admission for new attempts only and never rewrites evidence or identities.
    """
    rows = sorted((row for row in phase_records(manifest).values()
        if row.get("operation") == "phase_routing"), key=lambda row: row["committed_revision"])
    prior = None
    for row in rows:
        result = row.get("result")
        if not isinstance(result, dict) or set(result) != {"schema", "owner", "configuration", "previous"} or \
                result["schema"] != "taskplane.phase-routing/v1" or \
                result["owner"] != "agent-runtime" or \
                row.get("result_fingerprint") != _fingerprint(result) or \
                result["previous"] != (None if prior is None else prior["result_fingerprint"]):
            raise PhaseRecordError("phase routing receipt is invalid")
        prior = row
    return copy.deepcopy(prior)


def change_phase_routing(store: Any, run_id: str, *,
        owner: str, configuration: Mapping[str, Any] | None,
        expected_previous: str | None, expected_revision: int, operation_id: str,
        validate_authority: Callable[[Mapping[str, Any]], None]) -> dict[str, Any]:
    """CAS the sole new-attempt owner, preserving the complete prior journal."""
    if owner != "agent-runtime" or not isinstance(configuration, Mapping):
        raise PhaseRecordError("invalid phase routing selection")
    request = {"schema": "taskplane.phase-routing/v1", "owner": owner,
        "configuration": copy.deepcopy(configuration), "previous": expected_previous}
    # Replays also require current authority; RunStore's idempotent fast path
    # deliberately does not rerun its mutation callback.
    def authorize(current: Any) -> None:
        validate_authority(current)
        prior = phase_routing(current)
        replay = phase_records(current).get(operation_id)
        if replay is None and (None if prior is None else prior["result_fingerprint"]) != expected_previous:
            raise PhaseRecordError("phase routing CAS conflict")
    return commit_phase_record(store, run_id, expected_revision=expected_revision,
        operation_id=operation_id, operation="phase_routing", request_fingerprint=_fingerprint(request),
        result=request, validate_authority=authorize)


def historical_resource_policy(manifest: Mapping[str, Any], run_id: str) -> dict[str, Any] | None:
    """Read the human's run-wide resource decision from the existing journal."""
    row = phase_records(manifest).get("run-resource-limits")
    if row is None:
        return None
    value = row["result"]
    if row["operation"] != "resource_policy" or not isinstance(value, dict) or set(value) != {
            "schema", "run_id", "mode", "actor", "authority_fingerprint", "decided_at"} or \
            value["schema"] != "taskplane.resource-policy/v1" or value["run_id"] != run_id or \
            value["mode"] != "advisory" or not str(value["actor"]).startswith("human:") or \
            not re.fullmatch(r"[a-f0-9]{64}", value["authority_fingerprint"]) or \
            type(value["decided_at"]) is not int or value["decided_at"] < 0 or \
            row["request_fingerprint"] != _fingerprint(value):
        raise ValueError("run resource policy does not verify")
    return {**value, "fingerprint": row["request_fingerprint"]}


def resource_policy(manifest: Mapping[str, Any], run_id: str) -> dict[str, Any] | None:
    """Historical waivers remain auditable but cannot relax execution limits."""
    historical_resource_policy(manifest, run_id)
    return None
