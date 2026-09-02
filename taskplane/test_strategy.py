"""Closed Design-to-Build test-strategy contracts.

The contract is deliberately data-first.  Design records exact acceptance
selectors and producer edges; Build can then reject stale, incomplete, or
metadata-only evidence before attempting a correction.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping


SCHEMA = "taskplane.test-strategy/v1"
VALIDATION_LAYERS = (
    "static",
    "exact-selector",
    "changed-radius",
    "proportional-suite",
    "authoritative-ci",
)
FAILURE_CLASSES = ("product", "test", "infrastructure", "environment")
EVIDENCE_FAILURE_CLASSES = FAILURE_CLASSES + ("mixed", "unknown")
REJECTED_BEHAVIORAL_EVIDENCE = (
    "ceremonial", "source", "ast", "prose-shape", "byte-only",
)
CORRECTION_FIELDS = ("class", "reason", "owner", "cluster")
FINGERPRINT_INPUTS = (
    "source",
    "tests",
    "settings",
    "inventory",
    "selector",
    "radius",
    "shard-plan",
    "runner",
    "environment",
)

_NODE_ID = re.compile(r"^[^\s:]+\.py::[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*$")


class StrategyContractError(ValueError):
    """The strategy cannot authorize correction or terminal validation."""


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _producer_payload(producer: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in producer.items()
        if key != "fingerprint_sha256"
    }


def _contract_payload(strategy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in strategy.items()
        if key != "contract_fingerprint_sha256"
    }


def seal_strategy(strategy: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with producer and whole-contract freshness seals."""

    sealed = copy.deepcopy(dict(strategy))
    producers = sealed.get("producers", [])
    if isinstance(producers, list):
        for producer in producers:
            if isinstance(producer, dict):
                producer["fingerprint_sha256"] = _fingerprint(
                    _producer_payload(producer)
                )
    sealed["contract_fingerprint_sha256"] = _fingerprint(
        _contract_payload(sealed)
    )
    return sealed


def _require_exact_selector(selector: Any, context: str) -> str:
    if not isinstance(selector, str) or not _NODE_ID.fullmatch(selector):
        raise StrategyContractError(
            f"{context} must be an exact pytest node id (file.py::test_selector)"
        )
    return selector


def _require_nonempty_strings(value: Any, context: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise StrategyContractError(f"{context} must be a non-empty string list")
    if len(value) != len(set(value)):
        raise StrategyContractError(f"{context} contains duplicates")
    return value


def _collect_exact_selectors(workspace: str | Path, selectors: list[str]) -> None:
    """Require pytest itself to collect every declared exact selector."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", *selectors],
            cwd=str(workspace), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", timeout=30,
            check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StrategyContractError(
            "impacted test selector collection is unavailable") from exc
    if result.returncode != 0:
        raise StrategyContractError(
            "impacted test selector does not exist or cannot be collected")


def validate_strategy(strategy: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a defensive copy of a sealed strategy.

    Validation is structural as well as cryptographic: resealing an invalid
    graph cannot make a missing consumer, severed edge, or cross-slice fixture
    authoritative.
    """

    if not isinstance(strategy, Mapping):
        raise StrategyContractError("strategy must be an object")
    if strategy.get("schema") != SCHEMA:
        raise StrategyContractError(f"strategy schema must be {SCHEMA}")

    criteria = strategy.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        raise StrategyContractError("acceptance criteria must not be empty")
    criterion_ids: list[str] = []
    seen_selectors: set[str] = set()
    for criterion in criteria:
        if not isinstance(criterion, Mapping):
            raise StrategyContractError("each acceptance criterion must be an object")
        criterion_id = criterion.get("id")
        if not isinstance(criterion_id, str) or not criterion_id.strip():
            raise StrategyContractError("each acceptance criterion needs an id")
        criterion_ids.append(criterion_id)
        selectors = _require_nonempty_strings(
            criterion.get("selectors"), f"criterion {criterion_id} selectors"
        )
        for selector in selectors:
            _require_exact_selector(selector, f"criterion {criterion_id} selector")
            if selector in seen_selectors:
                raise StrategyContractError(
                    f"selector {selector} is assigned to more than one criterion"
                )
            seen_selectors.add(selector)
    if len(criterion_ids) != len(set(criterion_ids)):
        raise StrategyContractError("acceptance criterion ids must be unique")

    producers = strategy.get("producers")
    if not isinstance(producers, list) or not producers:
        raise StrategyContractError("changed producers must not be empty")
    producer_ids: list[str] = []
    for producer in producers:
        if not isinstance(producer, Mapping):
            raise StrategyContractError("each changed producer must be an object")
        producer_id = producer.get("id")
        if not isinstance(producer_id, str) or not producer_id.strip():
            raise StrategyContractError("each changed producer needs an id")
        producer_ids.append(producer_id)
        for field in ("path", "slice"):
            if not isinstance(producer.get(field), str) or not producer[field].strip():
                raise StrategyContractError(f"producer {producer_id} needs {field}")
        consumers = _require_nonempty_strings(
            producer.get("consumers"), f"producer {producer_id} consumers"
        )
        _require_nonempty_strings(
            producer.get("freshness_inputs"),
            f"producer {producer_id} freshness inputs",
        )
        expected = _fingerprint(_producer_payload(producer))
        if producer.get("fingerprint_sha256") != expected:
            raise StrategyContractError(
                f"producer {producer_id} has a stale fingerprint"
            )

        severed_edges = producer.get("severed_edges")
        if not isinstance(severed_edges, list) or not severed_edges:
            raise StrategyContractError(
                f"producer {producer_id} needs a deliberately severed edge"
            )
        for edge in severed_edges:
            if not isinstance(edge, Mapping):
                raise StrategyContractError(
                    f"producer {producer_id} severed edge must be an object"
                )
            consumer = edge.get("consumer")
            if consumer not in consumers:
                raise StrategyContractError(
                    f"producer {producer_id} severed edge names missing consumer {consumer!r}"
                )
            mutation = edge.get("mutation")
            if not isinstance(mutation, str) or not mutation.strip():
                raise StrategyContractError(
                    f"producer {producer_id} severed edge needs an executable mutation"
                )
            _require_exact_selector(
                edge.get("selector"), f"producer {producer_id} severed edge selector"
            )

        fixtures = producer.get("interface_fixtures", [])
        interface_kind = producer.get("interface_kind")
        if interface_kind is None:
            # Compatibility for sealed v1 strategies: a declared fixture is
            # an explicit serialized boundary, while no fixture is in-process.
            interface_kind = "serialized" if fixtures else "in-process"
        if interface_kind not in {"in-process", "serialized", "external"}:
            raise StrategyContractError(
                f"producer {producer_id} has an unknown interface kind"
            )
        if not isinstance(fixtures, list):
            raise StrategyContractError(
                f"producer {producer_id} interface fixtures must be a list"
            )
        if interface_kind in {"serialized", "external"} and not fixtures:
            raise StrategyContractError(
                f"producer {producer_id} must name interface fixtures"
            )
        if interface_kind == "in-process" and fixtures:
            raise StrategyContractError(
                f"producer {producer_id} in-process interface must use a real consumer journey"
            )
        for fixture in fixtures:
            if not isinstance(fixture, Mapping) or not isinstance(
                fixture.get("path"), str
            ):
                raise StrategyContractError(
                    f"producer {producer_id} has an invalid interface fixture"
                )
            if fixture.get("slice") != producer.get("slice"):
                raise StrategyContractError(
                    f"producer {producer_id} interface fixture must stay in the same slice"
                )
    if len(producer_ids) != len(set(producer_ids)):
        raise StrategyContractError("changed producer ids must be unique")

    failure_policy = strategy.get("failure_policy", {})
    if failure_policy.get("classes") != list(FAILURE_CLASSES):
        raise StrategyContractError(
            "failure classes must be exactly product, test, infrastructure, environment"
        )
    if failure_policy.get("correction_requires") != list(CORRECTION_FIELDS):
        raise StrategyContractError(
            "failure classification must require class, reason, owner, and cluster"
        )

    validation = strategy.get("validation", {})
    if validation.get("layers") != list(VALIDATION_LAYERS):
        raise StrategyContractError("validation layers must follow the approved progression")
    if validation.get("fingerprint_inputs") != list(FINGERPRINT_INPUTS):
        raise StrategyContractError("validation freshness fingerprint is incomplete")
    if validation.get("reuse_unchanged_green") != "cite":
        raise StrategyContractError("unchanged green layers must be cited")
    if validation.get("broad_local_default") != "refuse":
        raise StrategyContractError("broad local execution must refuse by default")
    if validation.get("authoritative_matrix_runs") != 1:
        raise StrategyContractError("exactly one authoritative CI matrix is allowed")

    expected_contract = _fingerprint(_contract_payload(strategy))
    if strategy.get("contract_fingerprint_sha256") != expected_contract:
        raise StrategyContractError("test strategy has a stale contract fingerprint")
    return copy.deepcopy(dict(strategy))


def current_value_obligations(
        impact_manifest: Mapping[str, Any], *,
        workspace: str | Path | None = None) -> dict:
    """Validate the test-design work an Evaluate attempt must discharge.

    These are behavioral obligations, not proof.  The child producer must
    later return direct evidence for each row; copying this inventory is not
    enough to pass evidence admission.
    """
    if not isinstance(impact_manifest, Mapping):
        raise StrategyContractError("impact manifest must be an object")
    tests = impact_manifest.get("tests")
    if not isinstance(tests, list) or not tests:
        raise StrategyContractError("impacted tests must not be empty")
    test_files = impact_manifest.get("test_files")
    if not isinstance(test_files, list) or not test_files:
        raise StrategyContractError("impacted test files must not be empty")
    normalized_tests = {str(path).replace("\\", "/") for path in test_files}
    selectors = []
    for row in tests:
        if not isinstance(row, Mapping):
            raise StrategyContractError("impacted test must be an object")
        selector = _require_exact_selector(
            row.get("selector"), "impacted test selector"
        )
        contract = row.get("contract")
        if not isinstance(contract, str) or not contract.strip():
            raise StrategyContractError(
                f"impacted test {selector} needs a current contract"
            )
        selector_file = selector.split("::", 1)[0]
        if selector_file not in normalized_tests:
            raise StrategyContractError(
                f"impacted selector {selector} is outside impacted test files")
        selectors.append(selector)
    if len(selectors) != len(set(selectors)):
        raise StrategyContractError("impacted test selectors contain duplicates")

    edges = impact_manifest.get("producer_consumer_edges")
    if not isinstance(edges, list) or not edges:
        raise StrategyContractError(
            "impacted producer-consumer edges must not be empty"
        )
    edge_keys = []
    for row in edges:
        if not isinstance(row, Mapping):
            raise StrategyContractError("producer-consumer edge must be an object")
        producer = row.get("producer")
        consumer = row.get("consumer")
        if not isinstance(producer, str) or not producer.strip() or \
                not isinstance(consumer, str) or not consumer.strip():
            raise StrategyContractError(
                "producer-consumer edge needs producer and consumer"
            )
        selector = _require_exact_selector(
            row.get("selector"), "producer-consumer selector"
        )
        _require_nonempty_strings(
            row.get("freshness_inputs"),
            f"producer-consumer edge {producer}->{consumer} freshness inputs",
        )
        severed = row.get("severed_edge")
        if not isinstance(severed, Mapping):
            raise StrategyContractError(
                f"producer-consumer edge {producer}->{consumer} needs a severed edge"
            )
        if not isinstance(severed.get("mutation"), str) or not \
                severed["mutation"].strip():
            raise StrategyContractError("severed edge mutation must be non-empty")
        severed_selector = _require_exact_selector(
            severed.get("selector"), "severed edge selector"
        )
        selectors.extend((selector, severed_selector))
        edge_keys.append((producer, consumer, selector))
    if len(edge_keys) != len(set(edge_keys)):
        raise StrategyContractError("producer-consumer edges contain duplicates")
    if workspace is not None:
        _collect_exact_selectors(workspace, list(dict.fromkeys(selectors)))

    interfaces = copy.deepcopy(impact_manifest.get("changed_interfaces", []))
    if not isinstance(interfaces, list):
        raise StrategyContractError("changed interfaces must be a list")
    interface_keys = []
    for row in interfaces:
        if not isinstance(row, Mapping):
            raise StrategyContractError("changed interface must be an object")
        producer = row.get("producer")
        kind = row.get("kind")
        slice_id = row.get("slice")
        if not isinstance(producer, str) or not producer.strip() or \
                kind not in {"in-process", "serialized", "external"} or \
                not isinstance(slice_id, str) or not slice_id.strip():
            raise StrategyContractError("changed interface identity is incomplete")
        fixture = row.get("fixture")
        if kind in {"serialized", "external"}:
            if not isinstance(fixture, Mapping) or \
                    not isinstance(fixture.get("path"), str) or \
                    not fixture["path"].strip():
                raise StrategyContractError(
                    f"changed {kind} interface needs a same-slice fixture"
                )
            if fixture.get("slice") != slice_id:
                raise StrategyContractError(
                    "changed interface fixture must stay in the same slice"
                )
            fixture_path = str(fixture["path"]).replace("\\", "/")
            if fixture_path not in normalized_tests:
                raise StrategyContractError(
                    "changed interface fixture must be an impacted test file")
            if workspace is not None:
                relative = Path(fixture_path)
                target = Path(workspace) / relative
                if relative.is_absolute() or ".." in relative.parts or \
                        not target.is_file() or target.is_symlink():
                    raise StrategyContractError(
                        "changed interface fixture must be an existing safe file")
                try:
                    fixture["content_sha256"] = hashlib.sha256(
                        target.read_bytes()).hexdigest()
                except OSError as exc:
                    raise StrategyContractError(
                        "changed interface fixture content is unavailable") from exc
        elif fixture is not None:
            raise StrategyContractError(
                "in-process interface must use a real consumer journey"
            )
        interface_keys.append((producer, kind, slice_id))
    if len(interface_keys) != len(set(interface_keys)):
        raise StrategyContractError("changed interfaces contain duplicates")

    failures = impact_manifest.get("failures", [])
    if not isinstance(failures, list):
        raise StrategyContractError("failures must be a list")
    failure_ids = []
    for row in failures:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str) \
                or not row["id"].strip():
            raise StrategyContractError("failure observation needs an id")
        classification = row.get("classification")
        if classification not in EVIDENCE_FAILURE_CLASSES:
            raise StrategyContractError("failure classification is invalid")
        if row.get("classified_before_repair") is not True:
            raise StrategyContractError(
                "failure must be classified before repair"
            )
        failure_ids.append(row["id"])
    if len(failure_ids) != len(set(failure_ids)):
        raise StrategyContractError("failure observations contain duplicates")

    rejected = impact_manifest.get("rejected_evidence_kinds")
    if rejected != list(REJECTED_BEHAVIORAL_EVIDENCE):
        raise StrategyContractError(
            "rejected behavioral evidence must be exactly ceremonial, source, "
            "ast, prose-shape, and byte-only"
        )
    return {
        "tests": copy.deepcopy(tests),
        "producer_consumer_edges": copy.deepcopy(edges),
        "changed_interfaces": interfaces,
        "failures": copy.deepcopy(failures),
        "rejected_evidence_kinds": list(REJECTED_BEHAVIORAL_EVIDENCE),
    }
