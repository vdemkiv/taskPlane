"""Generic progression for independently selected CI validation.

This effect-free policy does not impose a receipt or CI step on Build/Fix.
"""
from __future__ import annotations

if __package__:
    from . import primitives as _json_primitives
else:
    import primitives as _json_primitives  # type: ignore[no-redef]

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from taskplane.test_strategy import VALIDATION_LAYERS
elif __package__:
    from .test_strategy import VALIDATION_LAYERS
else:  # pragma: no cover - direct CLI module loading
    from test_strategy import VALIDATION_LAYERS


VALIDATION_SCHEMA = "taskplane.ci-validation/v1"


class BuildQualityError(ValueError):
    """An independently selected validation progression is invalid."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def _canonical(value: object) -> bytes:
    try:
        return _json_primitives.canonical_bytes(value, ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise BuildQualityError(
            "portable_json", "build-quality values must be portable JSON"
        ) from exc


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BuildQualityError("mapping", f"{label} must be an object")
    return {str(key): copy.deepcopy(item) for key, item in value.items()}


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise BuildQualityError("text", f"{label} must be non-empty trimmed text")
    return value


def _digest(value: object, label: str) -> str:
    text = _text(value, label)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise BuildQualityError("digest", f"{label} must be a SHA-256 digest")
    return text


def _validate_progression(value: Mapping[str, Any]) -> dict[str, Any]:
    previous = _mapping(value, "validation progression")
    if previous.get("schema") != VALIDATION_SCHEMA:
        raise BuildQualityError(
            "progression_schema", "validation progression schema is unsupported"
        )
    claimed = previous.get("fingerprint")
    actual = _fingerprint({
        key: item for key, item in previous.items() if key != "fingerprint"
    })
    if claimed != actual:
        raise BuildQualityError("stale_progression", "validation evidence is stale")
    completed = previous.get("completed")
    if not isinstance(completed, list) or completed != list(
            VALIDATION_LAYERS[:len(completed)]):
        raise BuildQualityError(
            "progression_order", "validation progression is not contiguous"
        )
    if previous.get("matrix_runs") not in {0, 1}:
        raise BuildQualityError(
            "matrix_runs", "only one authoritative matrix may run for a candidate"
        )
    if previous.get("local_approval") is not None:
        _local_approval(previous["local_approval"], previous.get("candidate_fingerprint"))
    return previous


def _local_approval(value: object, candidate: object) -> dict[str, Any]:
    approval = _mapping(value, "local approval")
    if set(approval) != {"actor", "request", "authority_reference", "candidate_fingerprint"}:
        raise BuildQualityError("local_approval", "local approval requires attributable saved authority")
    for field in ("actor", "request", "authority_reference"):
        _text(approval[field], "local approval " + field)
    if approval["candidate_fingerprint"] != candidate:
        raise BuildQualityError("local_approval", "local approval belongs to another candidate")
    return copy.deepcopy(approval)


def advance_progression(
    candidate_fingerprint: str,
    layer: str,
    *,
    execution: str,
    prior: Mapping[str, Any] | None = None,
    unchanged_green: Mapping[str, Any] | None = None,
    local_approval: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Advance the one validation progression used by Build and CI."""
    candidate = _digest(candidate_fingerprint, "candidate fingerprint")
    if layer not in VALIDATION_LAYERS:
        raise BuildQualityError("layer", f"unknown validation layer {layer!r}")
    if execution not in {"local", "ci"}:
        raise BuildQualityError(
            "execution", "validation execution must be local or ci"
        )
    approval = (_local_approval(local_approval, candidate)
                if local_approval is not None else None)
    if layer == "authoritative-ci" and execution != "ci":
        raise BuildQualityError(
            "authoritative_execution", "the authoritative matrix must run in CI"
        )
    if execution == "local" and layer not in {"static", "exact-selector"} and approval is None:
        raise BuildQualityError(
            "broad_local", "broad local validation is refused by default"
        )

    completed: list[str] = []
    cited: list[dict[str, str]] = []
    matrix_runs = 0
    if prior is not None:
        previous = _validate_progression(prior)
        if previous.get("candidate_fingerprint") != candidate:
            raise BuildQualityError(
                "candidate_mismatch", "validation layers must use one frozen candidate"
            )
        completed = list(previous["completed"])
        cited = copy.deepcopy(list(previous.get("cited_unchanged_green") or []))
        matrix_runs = int(previous.get("matrix_runs") or 0)
        retained_approval = previous.get("local_approval")
        if retained_approval is not None:
            retained_approval = _local_approval(retained_approval, candidate)
            if approval is not None and approval != retained_approval:
                raise BuildQualityError("local_approval", "local approval changed during progression")
            approval = retained_approval
    expected = (
        VALIDATION_LAYERS[len(completed)]
        if len(completed) < len(VALIDATION_LAYERS)
        else None
    )
    if layer != expected:
        raise BuildQualityError(
            "progression_order",
            f"validation must advance to {expected!r}, not {layer!r}",
        )

    mode = "executed"
    if unchanged_green is not None:
        if layer == "authoritative-ci":
            raise BuildQualityError(
                "authoritative_reuse",
                "authoritative CI must execute once for the frozen candidate",
            )
        green = _mapping(unchanged_green, "unchanged green receipt")
        if green.get("layer") != layer:
            raise BuildQualityError(
                "reuse_layer", "unchanged green receipt names the wrong layer"
            )
        if green.get("candidate_fingerprint") != candidate:
            raise BuildQualityError("reuse_stale", "unchanged green receipt is stale")
        receipt = _digest(green.get("receipt"), "unchanged green receipt")
        cited.append({"layer": layer, "receipt": receipt})
        mode = "cited"
    completed.append(layer)
    if layer == "authoritative-ci":
        matrix_runs += 1
    payload = {
        "schema": VALIDATION_SCHEMA,
        "candidate_fingerprint": candidate,
        "completed": completed,
        "cited_unchanged_green": cited,
        "last_layer": {"name": layer, "execution": execution, "mode": mode},
        "authoritative": completed == list(VALIDATION_LAYERS),
        "matrix_runs": matrix_runs,
    }
    if approval is not None:
        payload["local_approval"] = approval
    return {**payload, "fingerprint": _fingerprint(payload)}


__all__ = ["BuildQualityError", "VALIDATION_LAYERS", "VALIDATION_SCHEMA", "advance_progression"]
