"""Canonical Build-quality progression and sealed receipt contract.

The module is deliberately effect-free.  Runners execute checks and provide
their evidence; this boundary verifies that the evidence is complete, fresh,
and contiguous for one candidate before Build or CI can claim authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

if __package__:
    from . import failure_routing
else:  # pragma: no cover - direct CLI module loading
    import failure_routing


BUILD_QUALITY_RECEIPT_SCHEMA_ID = "taskplane.build-quality-receipt/v1"
LAYER_EVIDENCE_SCHEMA_ID = "taskplane.build-quality-layer-evidence/v1"
VALIDATION_SCHEMA = "taskplane.ci-validation/v1"
VALIDATION_LAYERS = (
    "static",
    "exact-selector",
    "changed-radius",
    "proportional-suite",
    "authoritative-ci",
)
BUILD_REQUIRED_LAYERS = VALIDATION_LAYERS[:-1]

_DIGEST_FIELDS = ("settings_digest", "runtime_digest", "environment_digest")
_BINDING_FIELDS = frozenset({"candidate", "run_id", "stage_instance", *_DIGEST_FIELDS})
_LAYER_EVIDENCE_FIELDS = frozenset({
    "schema",
    "layer",
    "candidate_fingerprint",
    "input_fingerprint",
    "status",
    "payload",
    "digest",
})


class BuildQualityError(ValueError):
    """Build evidence cannot acquire quality or CI authority."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
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


def _strings(value: object, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise BuildQualityError("string_list", f"{label} must be a string list")
    result: list[str] = []
    for item in value:
        result.append(_text(item, label))
    if len(result) != len(set(result)):
        raise BuildQualityError("string_list", f"{label} contains duplicates")
    return result


def _receipt_payload(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): copy.deepcopy(value)
        for key, value in receipt.items()
        if key != "fingerprint"
    }


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
    return previous


def advance_progression(
    candidate_fingerprint: str,
    layer: str,
    *,
    execution: str,
    prior: Mapping[str, Any] | None = None,
    unchanged_green: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Advance the one validation progression used by Build and CI."""
    candidate = _digest(candidate_fingerprint, "candidate fingerprint")
    if layer not in VALIDATION_LAYERS:
        raise BuildQualityError("layer", f"unknown validation layer {layer!r}")
    if execution not in {"local", "ci"}:
        raise BuildQualityError(
            "execution", "validation execution must be local or ci"
        )
    if execution == "local" and layer not in {"static", "exact-selector"}:
        raise BuildQualityError(
            "broad_local", "broad local validation is refused by default"
        )
    if layer == "authoritative-ci" and execution != "ci":
        raise BuildQualityError(
            "authoritative_execution", "the authoritative matrix must run in CI"
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
    return {**payload, "fingerprint": _fingerprint(payload)}


def _strategy_module() -> Any:
    if __package__:
        from . import test_strategy
    else:  # pragma: no cover - direct CLI module loading
        import test_strategy
    return test_strategy


def _binding(value: object) -> dict[str, Any]:
    raw = _mapping(value, "build-quality binding")
    if set(raw) != _BINDING_FIELDS:
        raise BuildQualityError(
            "binding_shape",
            "binding must contain candidate, run, stage, settings, runtime, and environment",
        )
    try:
        candidate = failure_routing.validate_candidate_identity(raw["candidate"])
    except failure_routing.FailureRoutingError as exc:
        raise BuildQualityError("candidate", str(exc)) from None
    return {
        "candidate": candidate,
        "run_id": _text(raw["run_id"], "run id"),
        "stage_instance": _text(raw["stage_instance"], "stage instance"),
        **{name: _digest(raw[name], name) for name in _DIGEST_FIELDS},
    }


def _selected_strategy(
    strategy: Mapping[str, Any],
    criterion_ids: list[str],
    producer_ids: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    validated = _strategy_module().validate_strategy(strategy)
    criteria = {row["id"]: row for row in validated["acceptance_criteria"]}
    missing_criteria = sorted(set(criterion_ids) - set(criteria))
    if missing_criteria:
        raise BuildQualityError(
            "criterion", f"unknown acceptance criteria {missing_criteria}"
        )
    selectors: list[str] = []
    for criterion_id in criterion_ids:
        selectors.extend(criteria[criterion_id]["selectors"])
    if len(selectors) != len(set(selectors)):
        raise BuildQualityError(
            "selector_overlap", "selected acceptance selectors overlap"
        )

    producer_map = {row["id"]: row for row in validated["producers"]}
    missing_producers = sorted(set(producer_ids) - set(producer_map))
    if missing_producers:
        raise BuildQualityError(
            "producer", f"unknown changed producers {missing_producers}"
        )
    return selectors, [copy.deepcopy(producer_map[name]) for name in producer_ids]


def _input_fingerprint(receipt: Mapping[str, Any], layer: str) -> str:
    return _fingerprint({
        "binding": receipt["binding"],
        "strategy_fingerprint": receipt["strategy_fingerprint"],
        "criterion_ids": receipt["criterion_ids"],
        "selectors": receipt["selectors"],
        "changed_producer_ids": receipt["changed_producer_ids"],
        "changed_paths": receipt["changed_paths"],
        "layer": layer,
    })


def begin_receipt(
    strategy: Mapping[str, Any],
    *,
    binding: Mapping[str, Any],
    criterion_ids: list[str],
    changed_producer_ids: list[str],
    changed_paths: list[str],
) -> dict[str, Any]:
    """Open a candidate-bound quality receipt from a sealed strategy."""
    validated = _strategy_module().validate_strategy(strategy)
    criteria = _strings(criterion_ids, "criterion ids")
    producers = _strings(changed_producer_ids, "changed producer ids")
    paths = _strings(changed_paths, "changed paths")
    selectors, selected_producers = _selected_strategy(
        validated, criteria, producers
    )
    payload = {
        "schema": BUILD_QUALITY_RECEIPT_SCHEMA_ID,
        "binding": _binding(binding),
        "strategy_fingerprint": validated["contract_fingerprint_sha256"],
        "criterion_ids": criteria,
        "selectors": selectors,
        "changed_producer_ids": producers,
        "changed_producers": selected_producers,
        "changed_paths": paths,
        "progression": None,
        "layers": [],
        "build_complete": False,
        "authoritative": False,
    }
    return {**payload, "fingerprint": _fingerprint(payload)}


def _expected_consumer_edges(receipt: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {"producer": producer["id"], "consumer": consumer}
        for producer in receipt["changed_producers"]
        for consumer in producer["consumers"]
    ]


def _expected_severed_edges(receipt: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for producer in receipt["changed_producers"]:
        for edge in producer["severed_edges"]:
            result.append({
                "producer": producer["id"],
                "consumer": edge["consumer"],
                "mutation": edge["mutation"],
                "selector": edge["selector"],
                "baseline_passed": True,
                "severed_failed": True,
                "restored_passed": True,
            })
    return result


def _interface_kind(producer: Mapping[str, Any]) -> str:
    value = producer.get("interface_kind")
    if value is None:
        return "serialized" if producer.get("interface_fixtures") else "in-process"
    return str(value)


def _expected_fixture_cochange(receipt: Mapping[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for producer in receipt["changed_producers"]:
        if _interface_kind(producer) not in {"serialized", "external"}:
            continue
        for fixture in producer.get("interface_fixtures") or []:
            result.append({"producer": producer["id"], "path": fixture["path"]})
    return result


def _same_items(actual: object, expected: list[Any], label: str) -> None:
    if actual != expected:
        raise BuildQualityError(
            "evidence_scope", f"{label} must match the declared Build scope exactly"
        )


def _validate_layer_payload(
    receipt: Mapping[str, Any], layer: str, payload_value: object
) -> None:
    payload = _mapping(payload_value, f"{layer} evidence payload")
    if layer == "static":
        if set(payload) != {"compile_import", "focused_static"}:
            raise BuildQualityError(
                "static_evidence",
                "static evidence must include compile/import and focused static checks",
            )
        python_paths = [
            path for path in receipt["changed_paths"] if path.endswith(".py")
        ]
        compile_check = _mapping(payload["compile_import"], "compile/import evidence")
        static_check = _mapping(payload["focused_static"], "focused static evidence")
        _same_items(compile_check.get("paths"), python_paths, "compile/import paths")
        _same_items(static_check.get("paths"), python_paths, "focused static paths")
        if compile_check.get("passed") is not True or static_check.get("passed") is not True:
            raise BuildQualityError("static_red", "compile/import and static checks must pass")
        _strings(static_check.get("checks"), "focused static checks")
        return

    if layer == "exact-selector":
        if set(payload) != {"collection", "execution"}:
            raise BuildQualityError(
                "selector_evidence",
                "selector evidence must include collection and execution",
            )
        collection = _mapping(payload["collection"], "selector collection")
        execution = _mapping(payload["execution"], "selector execution")
        selectors = receipt["selectors"]
        _same_items(collection.get("requested"), selectors, "requested selectors")
        _same_items(collection.get("collected"), selectors, "collected selectors")
        _same_items(execution.get("executed"), selectors, "executed selectors")
        _same_items(execution.get("passed"), selectors, "passing selectors")
        _same_items(execution.get("failed"), [], "failed selectors")
        if collection.get("passed") is not True:
            raise BuildQualityError("selector_red", "exact selector collection must pass")
        return

    if layer == "changed-radius":
        if set(payload) != {"consumer_radius", "severed_edges", "fixture_cochange"}:
            raise BuildQualityError(
                "radius_evidence",
                "changed-radius evidence must cover consumers, severed edges, and fixtures",
            )
        _same_items(
            payload["consumer_radius"],
            _expected_consumer_edges(receipt),
            "changed consumer radius",
        )
        _same_items(
            payload["severed_edges"],
            _expected_severed_edges(receipt),
            "semantic severed-edge proofs",
        )
        fixtures = _expected_fixture_cochange(receipt)
        _same_items(payload["fixture_cochange"], fixtures, "fixture co-change")
        changed_paths = set(receipt["changed_paths"])
        missing = sorted(
            row["path"] for row in fixtures if row["path"] not in changed_paths
        )
        if missing:
            raise BuildQualityError(
                "fixture_cochange",
                f"serialized or external interface fixtures did not co-change: {missing}",
            )
        return

    if layer == "proportional-suite":
        if set(payload) != {"scope", "passed"}:
            raise BuildQualityError(
                "suite_evidence", "proportional suite evidence is incomplete"
            )
        _strings(payload["scope"], "proportional suite scope")
        if payload["passed"] is not True:
            raise BuildQualityError("suite_red", "proportional suite must pass")
        return

    if set(payload) != {"matrix_runs", "passed"} or \
            payload.get("matrix_runs") != 1 or payload.get("passed") is not True:
        raise BuildQualityError(
            "authoritative_evidence",
            "authoritative CI evidence must prove one passing matrix",
        )


def seal_layer_evidence(
    receipt: Mapping[str, Any],
    layer: str,
    payload: Mapping[str, Any],
    *,
    status: str = "passed",
) -> dict[str, Any]:
    """Seal exact runner evidence for later semantic verification."""
    if layer not in VALIDATION_LAYERS:
        raise BuildQualityError("layer", f"unknown validation layer {layer!r}")
    if status not in {"passed", "cited"}:
        raise BuildQualityError("evidence_status", "layer status must be passed or cited")
    candidate = _binding(receipt.get("binding"))["candidate"]["fingerprint"]
    body = {
        "schema": LAYER_EVIDENCE_SCHEMA_ID,
        "layer": layer,
        "candidate_fingerprint": candidate,
        "input_fingerprint": _input_fingerprint(receipt, layer),
        "status": status,
        "payload": copy.deepcopy(dict(payload)),
    }
    return {**body, "digest": _fingerprint(body)}


def _validated_layer_evidence(
    receipt: Mapping[str, Any], layer: str, value: Mapping[str, Any]
) -> dict[str, Any]:
    evidence = _mapping(value, "layer evidence")
    if set(evidence) != _LAYER_EVIDENCE_FIELDS or \
            evidence.get("schema") != LAYER_EVIDENCE_SCHEMA_ID:
        raise BuildQualityError("evidence_shape", "layer evidence shape is unsupported")
    claimed = evidence.get("digest")
    actual = _fingerprint({
        key: item for key, item in evidence.items() if key != "digest"
    })
    if claimed != actual:
        raise BuildQualityError("evidence_digest", "layer evidence digest is stale")
    if evidence.get("layer") != layer:
        raise BuildQualityError("evidence_layer", "layer evidence names the wrong layer")
    candidate = receipt["binding"]["candidate"]["fingerprint"]
    if evidence.get("candidate_fingerprint") != candidate or \
            evidence.get("input_fingerprint") != _input_fingerprint(receipt, layer):
        raise BuildQualityError(
            "evidence_stale", "layer evidence does not bind the current Build inputs"
        )
    status = evidence.get("status")
    if status == "cited":
        payload = _mapping(evidence.get("payload"), "cited evidence")
        if set(payload) != {"prior_receipt_digest"}:
            raise BuildQualityError(
                "reuse_evidence", "cited green must name one prior receipt digest"
            )
        _digest(payload["prior_receipt_digest"], "prior receipt digest")
    elif status == "passed":
        _validate_layer_payload(receipt, layer, evidence.get("payload"))
    else:
        raise BuildQualityError("evidence_status", "layer evidence is not green")
    return evidence


def validate_receipt(
    strategy: Mapping[str, Any], receipt: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate receipt integrity and its exact sealed strategy binding."""
    value = _mapping(receipt, "build-quality receipt")
    if value.get("schema") != BUILD_QUALITY_RECEIPT_SCHEMA_ID:
        raise BuildQualityError("receipt_schema", "build-quality schema is unsupported")
    if value.get("fingerprint") != _fingerprint(_receipt_payload(value)):
        raise BuildQualityError("receipt_stale", "build-quality receipt is stale")
    validated_strategy = _strategy_module().validate_strategy(strategy)
    if value.get("strategy_fingerprint") != validated_strategy[
            "contract_fingerprint_sha256"]:
        raise BuildQualityError("strategy_stale", "build-quality strategy is stale")
    value["binding"] = _binding(value.get("binding"))
    criteria = _strings(value.get("criterion_ids"), "criterion ids")
    producer_ids = _strings(value.get("changed_producer_ids"), "producer ids")
    paths = _strings(value.get("changed_paths"), "changed paths")
    selectors, producers = _selected_strategy(
        validated_strategy, criteria, producer_ids
    )
    if value.get("selectors") != selectors or value.get("changed_producers") != producers:
        raise BuildQualityError(
            "strategy_scope", "receipt scope does not match the sealed strategy"
        )
    value["changed_paths"] = paths
    progression_value = value.get("progression")
    layers = value.get("layers")
    if not isinstance(layers, list):
        raise BuildQualityError("layers", "receipt layers must be a list")
    completed: list[str] = []
    progression: dict[str, Any] | None = None
    if progression_value is not None:
        progression = _validate_progression(
            _mapping(progression_value, "validation progression")
        )
        if progression.get("candidate_fingerprint") != value[
                "binding"]["candidate"]["fingerprint"]:
            raise BuildQualityError(
                "candidate_mismatch", "receipt progression uses another candidate"
            )
        completed = list(progression["completed"])
    if len(layers) != len(completed):
        raise BuildQualityError("layers", "receipt layers and progression disagree")
    for index, layer in enumerate(completed):
        _validated_layer_evidence(value, layer, layers[index])
    expected_build_complete = completed[:len(BUILD_REQUIRED_LAYERS)] == list(
        BUILD_REQUIRED_LAYERS
    )
    expected_authoritative = completed == list(VALIDATION_LAYERS)
    if value.get("build_complete") is not expected_build_complete or \
            value.get("authoritative") is not expected_authoritative:
        raise BuildQualityError("receipt_state", "receipt state overclaims its evidence")
    return copy.deepcopy(value)


def advance_validation(
    strategy: Mapping[str, Any],
    receipt: Mapping[str, Any],
    layer: str,
    evidence: Mapping[str, Any],
    *,
    execution: str,
) -> dict[str, Any]:
    """Verify one layer and advance the canonical receipt progression."""
    current = validate_receipt(strategy, receipt)
    checked = _validated_layer_evidence(current, layer, evidence)
    unchanged_green = None
    if checked["status"] == "cited":
        unchanged_green = {
            "layer": layer,
            "candidate_fingerprint": current["binding"]["candidate"]["fingerprint"],
            "receipt": checked["digest"],
        }
    progression = advance_progression(
        current["binding"]["candidate"]["fingerprint"],
        layer,
        execution=execution,
        prior=current.get("progression"),
        unchanged_green=unchanged_green,
    )
    current["progression"] = progression
    current["layers"].append(checked)
    current["build_complete"] = progression["completed"][:len(
        BUILD_REQUIRED_LAYERS
    )] == list(BUILD_REQUIRED_LAYERS)
    current["authoritative"] = progression["authoritative"]
    current["fingerprint"] = _fingerprint(_receipt_payload(current))
    return validate_receipt(strategy, current)


def admit_build_quality(
    strategy: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    expected_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Admit only a complete Build receipt for the expected active binding."""
    checked = validate_receipt(strategy, receipt)
    if not checked["build_complete"]:
        raise BuildQualityError(
            "build_incomplete", "Build quality has not completed every required layer"
        )
    if expected_binding is not None and checked["binding"] != _binding(expected_binding):
        raise BuildQualityError(
            "binding_mismatch", "Build quality belongs to another active stage"
        )
    return checked


__all__ = [
    "BUILD_QUALITY_RECEIPT_SCHEMA_ID",
    "BUILD_REQUIRED_LAYERS",
    "BuildQualityError",
    "VALIDATION_LAYERS",
    "VALIDATION_SCHEMA",
    "admit_build_quality",
    "advance_progression",
    "advance_validation",
    "begin_receipt",
    "seal_layer_evidence",
    "validate_receipt",
]
