"""Closed Design-to-Build test-strategy contracts.

The contract is deliberately data-first.  Design records exact acceptance
selectors and producer edges; Build can then reject stale, incomplete, or
metadata-only evidence before attempting a correction.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Mapping

if __package__:
    from .build_quality import VALIDATION_LAYERS
else:  # pragma: no cover - direct CLI module loading
    from build_quality import VALIDATION_LAYERS


SCHEMA = "taskplane.test-strategy/v1"
FAILURE_CLASSES = ("product", "test", "infrastructure", "environment")
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
