"""Closed, typed phase output contracts, shared by collection and consumption.

These validators inspect artifact bytes only. Repository freshness, signatures,
producer receipts and human decisions remain separate admission checks.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from . import failure_routing
else:
    import failure_routing

import copy
import hashlib
from collections.abc import Mapping
from typing import Any

if TYPE_CHECKING or __package__:
    from .primitives import content_fingerprint, canonical_bytes
else:
    from primitives import content_fingerprint, canonical_bytes  # type: ignore[no-redef]


SCHEMAS = {
    "requirement": "taskplane.requirement/v1",
    "design": "taskplane.design/v1",
    "test-strategy": "taskplane.test-strategy/v1",
    "plan-task": "taskplane.plan-task/v1",
    "source-coverage": "taskplane.source-touchpoint-coverage/v1",
    "decomposition": "taskplane.dependency-decomposition/v1",
    "seam-manifest": "taskplane.cross-task-seam-manifest/v1",
    "realized-conformance": "taskplane.realized-seam-conformance/v1",
    "stage": "taskplane.stage/v1",
    "judgment": "taskplane.evaluator-output/v2",
    "lens-evidence": "taskplane.lens-evidence/v1",
}


def _object(value: Any, required: Any, optional: Any = None, *, label: Any) -> Any:
    optional = optional or {}
    if not isinstance(value, Mapping) or set(value) - required.keys() - optional.keys():
        raise ValueError(f"{label} has unknown fields or is not an object")
    for name, kind in {**required, **optional}.items():
        if name not in value:
            if name in required:
                raise ValueError(f"{label} requires {name}")
            continue
        item = value[name]
        if not isinstance(item, kind) or (isinstance(item, bool) and kind is int):
            raise ValueError(f"{label}.{name} has the wrong type")
        if kind is str and not item.strip():
            raise ValueError(f"{label}.{name} must not be empty")
    return value


def _strings(value: Any, label: Any, *, nonempty: Any = False) -> None:
    if (
        not isinstance(value, list)
        or (nonempty and not value)
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(f"{label} must be a list of nonempty strings")


def _seal(value: Any) -> None:
    if value["fingerprint"] != content_fingerprint(
        {k: v for k, v in value.items() if k != "fingerprint"}
    ):
        raise ValueError("phase artifact fingerprint is stale")


def requirement(value: Any) -> None:
    _object(
        value,
        {"schema": str, "id": str, "acceptance_criteria": list},
        {
            "title": str,
            "summary": str,
            "functional": list,
            "non_functional": dict,
            "contracts": list,
            "depends_on": list,
            "scope": list,
            "out_of_scope": list,
        },
        label="requirement",
    )
    _strings(value["acceptance_criteria"], "acceptance_criteria", nonempty=True)


def design(value: Any) -> None:
    _object(
        value,
        {"schema": str, "requirement": str, "acceptance_map": list, "test_strategy": dict},
        {
            "title": str,
            "summary": str,
            "decision": str,
            "current_state": dict,
            "alternatives": list,
            "selected_approach": str,
            "modules": dict,
            "contracts": list,
            "graph": dict,
            "risks": list,
            "failure_modes": list,
            "observability": dict,
            "rollout": dict,
            "visualization": dict,
            "design_counts": dict,
            "journeys": list,
            "producers": list,
            "seam_contracts": list,
            "acceptance_pair_map": dict,
            "requirement_fingerprint": str,
            "fingerprint": str,
            "quality_authority": dict,
            "open_questions": list,
            "lens_evidence": list,
            "test_strategy_reference": dict,
        },
        label="design",
    )
    if not value["acceptance_map"]:
        raise ValueError("Design requires acceptance mappings")
    for row in value["acceptance_map"]:
        _object(
            row,
            {"criterion": str},
            {
                "criterion_id": str,
                "design_element": str,
                "validation": str,
                "tests": list,
                "selectors": list,
                "producer": str,
                "consumers": list,
                "edges": list,
            },
            label="acceptance mapping",
        )
        _strings(row.get("tests", row.get("selectors")), "acceptance selectors", nonempty=True)
    strategy = value["test_strategy"]
    if strategy.get("schema") == SCHEMAS["test-strategy"]:
        test_strategy(strategy)
    else:
        if "test_strategy_reference" in value:
            _object(strategy, {}, {"path": str}, label="Design test strategy")
            selected = value["test_strategy_reference"]
        else:
            _object(strategy, {"authority": dict}, label="Design test strategy")
            selected = strategy["authority"]
        reference = _object(
            selected,
            {"schema": str, "path": str, "strategy_fingerprint": str},
            label="test strategy reference",
        )
        if reference["schema"] != "taskplane.design-test-strategy-reference/v1":
            raise ValueError("unsupported Design test strategy reference")


def test_strategy(value: Any) -> None:
    from taskplane.test_strategy import validate_strategy

    _object(
        value,
        {
            "schema": str,
            "acceptance_criteria": list,
            "producers": list,
            "failure_policy": dict,
            "validation": dict,
            "contract_fingerprint_sha256": str,
        },
        label="test strategy",
    )
    validate_strategy(value)


def plan_task(value: Any) -> None:
    _object(
        value,
        {"schema": str},
        {
            "task": dict,
            "plan": dict,
            "dependency_outputs": dict,
            "traceability": dict,
            "owners": dict,
            "acceptance": list,
        },
        label="plan task",
    )
    if ("task" in value) == ("plan" in value):
        raise ValueError("Plan needs exactly one task or plan")
    tasks = value["plan"].get("tasks") if "plan" in value else [value["task"]]
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Plan requires tasks")
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str) or not task["id"]:
            raise ValueError("Plan task needs an identity")
        if not isinstance(task.get("tests"), str) or not task["tests"].strip():
            raise ValueError("Plan task needs one test command")
        _strings(task.get("scope"), "task scope", nonempty=True)
        if not isinstance(task.get("test_strategy_authority_receipt"), dict):
            raise ValueError("Plan task requires sealed Design quality authority")


def source_coverage(value: Any) -> None:
    _object(
        value,
        {
            "schema": str,
            "producer": str,
            "source_tree": str,
            "status": str,
            "complete": bool,
            "limits": dict,
            "kinds": list,
            "touchpoints": dict,
            "stopping_conditions": list,
            "fingerprint": str,
        },
        label="source coverage",
    )
    _seal(value)
    if (
        value["status"] != "complete"
        or value["complete"] is not True
        or value["stopping_conditions"]
    ):
        raise ValueError("partial source coverage cannot enter a phase package")


def decomposition(value: Any) -> None:
    _object(
        value,
        {
            "schema": str,
            "source_tree": str,
            "coverage_fingerprint": str,
            "components": list,
            "edges": list,
            "tasks": list,
            "fingerprint": str,
        },
        label="decomposition",
    )
    for row in value["components"]:
        _object(
            row,
            {"id": str, "module": str, "files": list, "symbols": list, "fingerprint": str},
            label="component",
        )
    for row in value["edges"]:
        _object(row, {"producer": str, "consumer": str, "kind": str}, label="edge")
    for row in value["tasks"]:
        _object(row, {"id": str, "nodes": list, "deps": list}, label="decomposed task")
        _strings(row["nodes"], "task nodes", nonempty=True)
        _strings(row["deps"], "task dependencies")
    _seal(value)


def seam_manifest(value: Any) -> None:
    _object(
        value,
        {
            "schema": str,
            "binding": dict,
            "decomposition_fingerprint": str,
            "nodes": list,
            "seams": list,
            "fingerprint": str,
        },
        label="seam manifest",
    )
    _strings(value["nodes"], "seam nodes")
    for row in value["seams"]:
        _object(
            row,
            {
                **{
                    key: str
                    for key in (
                        "id",
                        "producer",
                        "consumer",
                        "kind",
                        "schema_version",
                        "cardinality",
                        "producer_symbol",
                        "consumer_symbol",
                        "positive",
                        "severed",
                        "producer_owner",
                        "consumer_owner",
                    )
                },
                "producer_files": list,
                "consumer_files": list,
                "producer_order": int,
                "consumer_order": int,
            },
            label="seam",
        )
    _seal(value)


def realized_conformance(value: Any) -> None:
    _object(
        value,
        {
            "schema": str,
            "status": str,
            "binding": dict,
            "manifest_fingerprint": str,
            "realized_decomposition_fingerprint": str,
            "realized_source_tree": str,
            "missing": list,
            "unexpected": list,
            "fingerprint": str,
        },
        {"task_scope": dict},
        label="realized conformance",
    )
    if "task_scope" in value:
        scope = _object(
            value["task_scope"],
            {"task_id": str, "required_tasks": list, "required_nodes": list},
            label="conformance task scope",
        )
        _strings(scope["required_tasks"], "conformance tasks", nonempty=True)
        _strings(scope["required_nodes"], "conformance nodes")
        if scope["task_id"] not in scope["required_tasks"]:
            raise ValueError("conformance scope omits its task")
    if value["status"] != "conformant" or value["missing"] or value["unexpected"]:
        raise ValueError("realized seams do not conform to Plan")
    _seal(value)


def stage(value: Any) -> None:
    _object(
        value,
        {
            **{
                key: str
                for key in (
                    "schema",
                    "run_id",
                    "stage_id",
                    "stage_kind",
                    "execution_root_id",
                    "state",
                    "created_at",
                    "fingerprint",
                )
            },
            **{
                key: list
                for key in (
                    "parent_stage_ids",
                    "predecessor_stage_ids",
                    "deliverables",
                    "selected_artifacts",
                    "dependencies",
                    "contracts",
                )
            },
            "requirement": dict,
            "design": (dict, type(None)),
            "input_manifest_ref": dict,
            "budget": dict,
            "authority": dict,
            "outcome": (str, type(None)),
            "default_consumable": bool,
            "terminal": (dict, type(None)),
            "aggregate_revision": int,
        },
        label="stage",
    )
    _seal(value)


def judgment(value: Any) -> None:
    _validate(dict(value), evaluator_output_schema())
    evaluation = value.get("evaluation")
    if (
        not isinstance(evaluation, dict)
        or evaluation.get("status") != "complete"
        or evaluation.get("reason_code") != "none"
    ):
        raise ValueError("phase judgment requires a completed evaluation")
    aggregate = value.get("accepted_evaluations")
    if value["task"] == "engineering-signoff":
        if not isinstance(aggregate, list) or not aggregate or "child_evidence" in value:
            raise ValueError("Engineering judgment requires its accepted task evaluations")
        if len({row["task_id"] for row in aggregate}) != len(aggregate):
            raise ValueError("Engineering task evaluations must be unique")
    elif aggregate is not None or (
        value["verdict"] == "pass" and not isinstance(value.get("child_evidence"), dict)
    ):
        raise ValueError("Evaluate judgment requires its own child evidence")
    if value["verdict"] == "pass" and value["failures"]:
        raise ValueError("passing judgment must not carry failures")
    if value["failures"]:
        failure_routing.validate_failure_records(value["failures"])


def lens_evidence(value: Any) -> None:
    _object(
        value,
        {"schema": str, "entries": list, "fingerprint": str},
        {"human_amendments": list},
        label="lens evidence",
    )
    human = value.get("human_amendments", [])
    if any(
        not isinstance(row, dict)
        or row.get("kind") != "phase-amendment-decision"
        or not row.get("fingerprint")
        for row in human
    ):
        raise ValueError("human amendment evidence needs exact decision references")
    identities = []
    for entry in value["entries"]:
        _object(
            entry,
            {"plan": dict, "collection": dict, "validations": list, "status": str},
            label="lens collection",
        )
        if entry["status"] != "complete":
            raise ValueError("only complete lens collections can enter a phase handoff")
        identities.append(entry["plan"].get("fingerprint"))
    if (
        (not identities and not human)
        or None in identities
        or len(identities) != len(set(identities))
    ):
        raise ValueError("lens evidence needs distinct saved phase plans")
    _seal(value)


VALIDATORS = {
    "lens-evidence": lens_evidence,
    "requirement": requirement,
    "design": design,
    "test-strategy": test_strategy,
    "plan-task": plan_task,
    "source-coverage": source_coverage,
    "decomposition": decomposition,
    "seam-manifest": seam_manifest,
    "realized-conformance": realized_conformance,
    "stage": stage,
    "judgment": judgment,
}


def validate(artifact_class: str, value: object) -> dict[str, Any]:
    if (
        artifact_class not in SCHEMAS
        or not isinstance(value, Mapping)
        or value.get("schema") != SCHEMAS[artifact_class]
    ):
        raise ValueError("phase artifact class and schema differ")
    VALIDATORS[artifact_class](value)
    return copy.deepcopy(dict(value))


def read(store: Any, reference: dict[str, Any], artifact_class: str) -> dict[str, Any]:
    return validate(artifact_class, store.read(reference))


EVALUATOR_OUTPUT_SCHEMA_ID = "taskplane.evaluator-output/v2"


class OutputValidationError(ValueError):
    """Model output cannot enter a governed record or gate."""

    def __init__(self: Any, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _schema_object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": required,
        "additionalProperties": False,
        "properties": properties,
    }


def evaluator_output_schema() -> dict[str, Any]:
    string = {"type": "string"}
    evaluation = _schema_object(
        {
            "status": {"enum": ["complete", "unavailable"]},
            "reason_code": {
                "enum": [
                    "none",
                    "host_unavailable",
                    "agent_timeout",
                    "transport_unavailable",
                    "producer_receipt_unavailable",
                    "orchestration_unavailable",
                ]
            },
            "detail": string,
        },
        ["status", "reason_code", "detail"],
    )
    criterion = _schema_object(
        {
            "criterion": string,
            "status": {"enum": ["met", "not-met", "cannot-verify"]},
            "evidence": string,
        },
        ["criterion", "status", "evidence"],
    )
    disposition = _schema_object(
        {
            "node": string,
            "status": string,
            "evidence": string,
        },
        ["node", "status", "evidence"],
    )
    graph = _schema_object(
        {
            "dispositions": {"type": "array", "items": disposition},
            "requirements_checked": {"type": "array", "items": string},
            "contracts_checked": {"type": "array", "items": string},
        },
        ["dispositions", "requirements_checked", "contracts_checked"],
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": EVALUATOR_OUTPUT_SCHEMA_ID,
        **_schema_object(
            {
                "schema": {"const": EVALUATOR_OUTPUT_SCHEMA_ID},
                "task": string,
                "requirement": {"type": "string"},
                "verdict": {"enum": ["pass", "fail"]},
                # Optional for byte compatibility with completed v1 records. It
                # is mandatory at the loop boundary for ``unavailable``.
                "evaluation": evaluation,
                "criteria": {"type": "array", "items": criterion},
                "graph": graph,
                # Historical records stay readable through read_evaluator_value;
                # a current pass must carry durable child evidence at admission.
                "child_evidence": {"type": "object"},
                "accepted_evaluations": {
                    "type": "array",
                    "items": _schema_object(
                        {
                            "task_id": string,
                            "candidate_sha": string,
                            **{
                                key: _schema_object(
                                    {
                                        "schema": {"const": "taskplane.artifact-reference/v1"},
                                        "kind": string,
                                        "fingerprint": string,
                                        "digest": string,
                                        "bytes": {"type": "integer"},
                                        "locator": string,
                                        "transport": {"const": "artifact-reference"},
                                    },
                                    [
                                        "schema",
                                        "kind",
                                        "fingerprint",
                                        "digest",
                                        "bytes",
                                        "locator",
                                        "transport",
                                    ],
                                )
                                for key in (
                                    "judgment",
                                    "handoff",
                                    "runtime_receipt",
                                    "lens_evidence",
                                )
                            },
                        },
                        [
                            "task_id",
                            "candidate_sha",
                            "judgment",
                            "handoff",
                            "runtime_receipt",
                            "lens_evidence",
                        ],
                    ),
                },
                "failures": {
                    "type": "array",
                    "items": failure_routing.failure_record_schema(),
                },
            },
            ["schema", "task", "requirement", "verdict", "criteria", "graph", "failures"],
        ),
    }


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return False


def _validate(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    if "const" in schema and value != schema["const"]:
        raise OutputValidationError("const_mismatch", f"{path} has wrong value")
    if "enum" in schema and value not in schema["enum"]:
        raise OutputValidationError("enum_mismatch", f"{path} is not allowed")
    expected = schema.get("type")
    if expected and not _type_ok(value, expected):
        raise OutputValidationError("type_mismatch", f"{path} has wrong type")
    if expected == "object":
        properties = schema.get("properties") or {}
        for field in schema.get("required") or []:
            if field not in value:
                raise OutputValidationError("missing_field", f"{path} is missing {field}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise OutputValidationError("extra_field", f"{path} contains {sorted(extra)[0]}")
        for field, child in properties.items():
            if field in value:
                _validate(value[field], child, f"{path}.{field}")
    elif expected == "array":
        child = schema.get("items")
        if isinstance(child, dict):
            for index, item in enumerate(value):
                _validate(item, child, f"{path}[{index}]")
    if "minimum" in schema and value < schema["minimum"]:
        raise OutputValidationError("minimum", f"{path} is below minimum")


ASSIGNMENT_SCHEMA = "taskplane.evaluate-child-assignment/v2"


LIFECYCLE_SCHEMA = "taskplane.evaluate-child-lifecycle/v1"


LANGUAGE_RESULT_SCHEMA = "taskplane.evaluate-language-code-quality/v2"


TEST_DESIGN_RESULT_SCHEMA = "taskplane.evaluate-test-design/v2"


LANGUAGE_PRODUCER = "language-code-quality"


TEST_DESIGN_PRODUCER = "test-design"


PRODUCER_KINDS = (LANGUAGE_PRODUCER, TEST_DESIGN_PRODUCER)


LIFECYCLE_KINDS = ("assignment", "start", "activity", "result", "terminal")


FORBIDDEN_AUTHORITIES = (
    "verdict",
    "gate",
    "dispatch",
    "mutation",
    "delivery-classification",
    "repair",
)


BINDING_FIELDS = (
    "task_id",
    "requirement_id",
    "candidate_sha",
    "source_tree",
    "design_fingerprint",
    "plan_fingerprint",
    "settings_digest",
    "evaluator_attempt_id",
    "impact_manifest_fingerprint",
)


EVENT_TYPES = ("assignment", "start", "progress", "evidence-reference", "terminal")


RESULT_SCHEMAS = {
    LANGUAGE_PRODUCER: LANGUAGE_RESULT_SCHEMA,
    TEST_DESIGN_PRODUCER: TEST_DESIGN_RESULT_SCHEMA,
}


class EvidenceContractError(ValueError):
    """Evidence cannot authorize an evaluator decision."""


def _canonical(value: object) -> bytes:
    try:
        return canonical_bytes(value, ensure_ascii=True) + b"\n"
    except (TypeError, ValueError) as exc:
        raise EvidenceContractError(f"evidence is not canonical JSON: {exc}") from None


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: object, label: str, minimum: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise EvidenceContractError(f"{label} must be substantive")
    return value


def _assignment_digest(value: Mapping[str, Any]) -> str:
    return _digest(
        {key: copy.deepcopy(item) for key, item in value.items() if key != "assignment_digest"}
    )


def _reuse_key(
    kind: str, binding: Mapping[str, Any], obligations: Mapping[str, Any], ledger_fingerprint: str
) -> str:
    stable = {
        key: copy.deepcopy(item) for key, item in binding.items() if key != "evaluator_attempt_id"
    }
    return _digest(
        {
            "producer_kind": kind,
            "binding": stable,
            "ledger_binding_fingerprint": ledger_fingerprint,
            "obligations": copy.deepcopy(dict(obligations)),
            "lifecycle": [LIFECYCLE_SCHEMA, *LIFECYCLE_KINDS, *EVENT_TYPES],
            "result_schema": RESULT_SCHEMAS[kind],
        }
    )


def _validate_assignment(value: object) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or value.get("schema") != ASSIGNMENT_SCHEMA
        or value.get("producer_kind") not in PRODUCER_KINDS
        or value.get("capabilities") != {name: False for name in FORBIDDEN_AUTHORITIES}
    ):
        raise EvidenceContractError("child assignment is invalid")
    row = copy.deepcopy(dict(value))
    binding = row.get("binding")
    if not isinstance(binding, Mapping) or set(binding) != set(BINDING_FIELDS):
        raise EvidenceContractError("child assignment binding is incomplete")
    obligations = (
        {
            "implementation_files": row.get("implementation_files"),
            "language_obligations": row.get("language_obligations"),
        }
        if row["producer_kind"] == LANGUAGE_PRODUCER
        else {"test_obligations": row.get("test_obligations")}
    )
    expected = _reuse_key(
        row["producer_kind"],
        binding,
        obligations,
        _text(row.get("ledger_binding_fingerprint"), "ledger"),
    )
    if row.get("reuse_key_digest") != expected or row.get(
        "assignment_digest"
    ) != _assignment_digest(row):
        raise EvidenceContractError("child assignment or reuse key is stale")
    return row
