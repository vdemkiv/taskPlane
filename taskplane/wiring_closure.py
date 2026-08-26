"""Pure validators for Design acceptance selectors and wiring closure.

The Design Contract is authority for what must be connected.  This module
turns that authored inventory into closed, content-fingerprinted evidence only
after every exact ``test_file.py::selector`` identity resolves beneath the
caller's repository root.  It deliberately imports no loop or review runtime.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Mapping, Sequence
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


ACCEPTANCE_SELECTOR_SCHEMA = "taskplane.acceptance-selector-map/v1"
WIRING_CLOSURE_SCHEMA = "taskplane.wiring-closure/v1"
EXPECTED_CRITERION_COUNT = 12
EXPECTED_EDGE_IDS = tuple(f"W{number:02d}" for number in range(1, 33))
EXPECTED_PRODUCER_COUNT = 18
_FIVE_CLASS_BOUNDARY_PRODUCERS = frozenset(
    {
        "trusted host adapter private channel",
        "generic task capability inventory + protected release consumer",
    }
)
_CANONICAL_PRODUCER_EDGE_IDS = {
    "taskplane/delivery_policy.py": frozenset(
        {"W01", "W02", "W08", "W09", "W11", "W14", "W16", "W27"}
    ),
    "taskplane/review_authority.py": frozenset(
        {"W04", "W05", "W08", "W09", "W11", "W14", "W16", "W27"}
    ),
    "taskplane/producer_observation.py": frozenset(
        {"W06", "W08", "W09", "W11", "W14", "W16", "W27"}
    ),
    "taskplane/wiring_closure.py": frozenset(
        {"W07", "W08", "W12", "W14", "W16", "W27"}
    ),
    "taskplane/release_evidence.py": frozenset(
        {"W10", "W13", "W14", "W15", "W16", "W17", "W25", "W27"}
    ),
    "taskplane/plan_topology.py": frozenset(
        {"W08", "W11", "W14", "W19", "W20", "W22"}
    ),
    "taskplane/brief_projection.py": frozenset(
        {"W08", "W09", "W11", "W14", "W18"}
    ),
    "taskplane/dispatch_telemetry.py": frozenset(
        {"W08", "W11", "W14", "W20", "W21", "W22"}
    ),
    "taskplane/repository.py remote-default resolver": frozenset(
        {"W08", "W11", "W14", "W23"}
    ),
    "taskplane/pickup.py timing events": frozenset(
        {"W08", "W11", "W14", "W24"}
    ),
    "lenses/references/prompt-injection-defense.md": frozenset(
        {"W08", "W11", "W12", "W14", "W16", "W27"}
    ),
    "release version/history surfaces": frozenset(
        {"W10", "W13", "W14", "W16", "W17", "W25", "W27"}
    ),
    "host/plugin capability adapters": frozenset(
        {"W06", "W11", "W14", "W26", "W27"}
    ),
    "design/schemas + design/compatibility.json": frozenset(
        {"W08", "W11", "W14", "W16", "W26", "W27"}
    ),
    "taskplane/delivery_ports.py + injected implementations": frozenset(
        {"W06", "W14", "W16", "W20", "W21", "W23", "W28", "W29"}
    ),
    "native dispatch intent + host spawn observation": frozenset(
        {"W19", "W20", "W21", "W22", "W30"}
    ),
    "trusted host adapter private channel": frozenset(
        {"W04", "W06", "W16", "W28", "W31"}
    ),
    "generic task capability inventory + protected release consumer": frozenset(
        {"W01", "W02", "W14", "W16", "W30", "W32"}
    ),
}

_ACCEPTANCE_FIELDS = frozenset(
    {
        "criterion",
        "design_element",
        "validation",
        "tests",
        "public_entrypoint",
        "controlled_dependencies",
    }
)
_WIRING_FIELDS = frozenset(
    {
        "schema",
        "edge_count",
        "status",
        "edges",
        "producer_closure",
        "release_rule",
    }
)
_EDGE_REQUIRED_FIELDS = frozenset(
    {
        "id",
        "producer",
        "artifact_or_contract",
        "consumer",
        "edge_test",
        "required_status",
    }
)
_EDGE_OPTIONAL_FIELDS = frozenset({"additional_tests", "semantic_obligation"})
_PRODUCER_FIELDS = frozenset({"producer", "consumer_classes", "edge_ids"})


class WiringClosureError(ValueError):
    """A Design selector or producer/consumer edge is not closed."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WiringClosureError(f"{field} is required")
    if value != value.strip():
        raise WiringClosureError(f"{field} must not contain outer whitespace")
    return value


def _items(value: Any, field: str, *, allow_empty: bool = False) -> list[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(
        value, Sequence
    ):
        raise WiringClosureError(f"{field} must be a collection")
    items = list(value)
    if not allow_empty and not items:
        raise WiringClosureError(f"{field} must not be empty")
    return items


def _strings(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    values = [
        _text(item, field)
        for item in _items(value, field, allow_empty=allow_empty)
    ]
    if len(values) != len(set(values)):
        raise WiringClosureError(f"{field} contains duplicates")
    return values


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _seal(projection: Mapping[str, Any]) -> dict[str, Any]:
    sealed = dict(projection)
    sealed["fingerprint"] = hashlib.sha256(_canonical_bytes(sealed)).hexdigest()
    return sealed


def _selector_identity(value: Any) -> tuple[str, str]:
    identity = _text(value, "test selector")
    parts = identity.split("::")
    if len(parts) not in {2, 3} or any(not part for part in parts):
        raise WiringClosureError(
            f"test selector must be exact file.py::selector: {identity}"
        )
    relative = parts[0]
    pure = PurePosixPath(relative)
    if (
        "\\" in relative
        or pure.is_absolute()
        or pure.suffix != ".py"
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise WiringClosureError(
            "test selector path must be a safe repository-relative .py "
            f"file: {identity}"
        )
    selector_parts = parts[1:]
    if any(not part.isidentifier() for part in selector_parts):
        raise WiringClosureError(
            f"test selector is not an exact Python identity: {identity}"
        )
    if len(selector_parts) == 1 and not selector_parts[0].startswith("test_"):
        raise WiringClosureError(
            f"test selector must name a test function: {identity}"
        )
    if len(selector_parts) == 2 and (
        not selector_parts[0].startswith("Test")
        or not selector_parts[1].startswith("test_")
    ):
        raise WiringClosureError(
            f"test selector must name TestClass::test_method: {identity}"
        )
    return pure.as_posix(), "::".join(selector_parts)


def collect_test_selectors(path: Path) -> frozenset[str]:
    """Collect exact module-function and ``TestClass::method`` selectors."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise WiringClosureError(
            f"declared test file cannot be collected: {path}: {exc}"
        ) from exc
    selectors: set[str] = set()
    for node in tree.body:
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test_"):
            selectors.add(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for member in node.body:
                if isinstance(
                    member, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and member.name.startswith("test_"):
                    selectors.add(f"{node.name}::{member.name}")
    return frozenset(selectors)


def _caller_root(value: str | Path) -> Path:
    root = Path(value).resolve()
    if not root.is_dir():
        raise WiringClosureError(f"caller_root is not a directory: {root}")
    return root


def _resolve_selectors(
    identities: Iterable[str],
    *,
    caller_root: str | Path,
    selector_collector: Callable[[Path], Iterable[str]],
) -> list[str]:
    root = _caller_root(caller_root)
    cache: dict[str, frozenset[str]] = {}
    resolved: list[str] = []
    for identity in identities:
        relative, selector = _selector_identity(identity)
        candidate = root.joinpath(*PurePosixPath(relative).parts)
        try:
            path = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise WiringClosureError(
                f"declared test file is missing: {relative} (for {identity})"
            ) from exc
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise WiringClosureError(
                f"declared test file escapes caller_root: {relative}"
            ) from exc
        if not path.is_file():
            raise WiringClosureError(f"declared test file is not regular: {relative}")
        if relative not in cache:
            try:
                collected = frozenset(
                    _text(item, "collected selector")
                    for item in selector_collector(path)
                )
            except WiringClosureError:
                raise
            except Exception as exc:
                raise WiringClosureError(
                    f"declared test file cannot be collected: {relative}: {exc}"
                ) from exc
            cache[relative] = collected
        if selector not in cache[relative] and identity not in cache[relative]:
            raise WiringClosureError(f"exact selector is missing: {identity}")
        resolved.append(identity)
    return sorted(set(resolved))


def acceptance_test_map(
    contract: Mapping[str, Any],
) -> dict[str, list[str]] | None:
    """Return the Design AC-to-selector map when one is declared.

    Older Design Contracts may omit ``acceptance_map``.  Once present, the
    map is closed: every criterion owns a non-empty, duplicate-free list of
    exact test identities.  This pure projection is shared by Design and the
    BUILD-C checkpoint adapter so the adapter never imports Design runtime.
    """
    if not isinstance(contract, Mapping) or "acceptance_map" not in contract:
        return None
    rows = contract.get("acceptance_map")
    if not isinstance(rows, list):
        raise WiringClosureError("acceptance_map must be a list")
    result: dict[str, list[str]] = {}
    for index, row in enumerate(rows, 1):
        criterion = str(row.get("criterion") or "").strip() \
            if isinstance(row, Mapping) else ""
        if not criterion:
            raise WiringClosureError(
                f"acceptance row {index} criterion is missing")
        if criterion in result:
            raise WiringClosureError(
                f"acceptance criterion is duplicated: {criterion}")
        tests = row.get("tests")
        if (not isinstance(tests, Sequence)
                or isinstance(tests, (str, bytes)) or not tests):
            raise WiringClosureError(
                f"acceptance criterion has no exact tests: {criterion}")
        identities = []
        for value in tests:
            _selector_identity(value)
            identities.append(str(value))
        if len(identities) != len(set(identities)):
            raise WiringClosureError(
                f"acceptance criterion has duplicate tests: {criterion}")
        result[criterion] = identities
    return result


def checkpoint_acceptance_tests(
    caller_root: str | Path,
    contract: Mapping[str, Any],
    ac_ids: Sequence[str],
) -> dict[str, list[str]] | None:
    """Resolve Design-declared tests for one checkpoint before execution."""
    mapping = acceptance_test_map(contract)
    if mapping is None:
        return None
    criteria = list(mapping)
    selected: list[str] = []
    for ac_id in ac_ids:
        criterion = ac_id if ac_id in mapping else None
        if criterion is None:
            ordinal = re.fullmatch(
                r"AC-?0*([1-9][0-9]*)", ac_id, flags=re.IGNORECASE)
            index = int(ordinal.group(1)) - 1 if ordinal else -1
            if 0 <= index < len(criteria):
                criterion = criteria[index]
        if criterion is None:
            raise WiringClosureError(
                f"checkpoint acceptance criterion is not declared: {ac_id}")
        if criterion in selected:
            raise WiringClosureError(
                "checkpoint acceptance criterion resolves more than once: "
                f"{criterion}")
        selected.append(criterion)

    identities = [
        identity
        for criterion in selected
        for identity in mapping[criterion]
    ]
    selectors = _resolve_selectors(
        identities,
        caller_root=caller_root,
        selector_collector=collect_test_selectors,
    )
    files = sorted({_selector_identity(identity)[0]
                    for identity in identities})
    return {
        "criteria": selected,
        "files": files,
        "selectors": selectors,
    }


def validate_acceptance_map(
    acceptance_map: Sequence[Mapping[str, Any]],
    *,
    caller_root: str | Path,
    selector_collector: Callable[[Path], Iterable[str]] = collect_test_selectors,
) -> dict[str, Any]:
    """Close all 12 acceptance rows only after every selector resolves."""
    rows = _items(acceptance_map, "acceptance_map")
    if len(rows) != EXPECTED_CRITERION_COUNT:
        raise WiringClosureError(
            f"acceptance_map must contain exactly {EXPECTED_CRITERION_COUNT} criteria"
        )
    normalized: list[dict[str, Any]] = []
    criteria: set[str] = set()
    identities: list[str] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, Mapping) or set(row) != _ACCEPTANCE_FIELDS:
            raise WiringClosureError(f"acceptance row {index} fields are not closed")
        criterion = _text(
            row.get("criterion"), f"acceptance row {index} criterion"
        )
        if criterion in criteria:
            raise WiringClosureError(
                f"acceptance criterion is duplicated: {criterion}"
            )
        criteria.add(criterion)
        tests = _strings(row.get("tests"), f"acceptance row {index} tests")
        identities.extend(tests)
        normalized.append(
            {
                "criterion": criterion,
                "design_element": _text(
                    row.get("design_element"),
                    f"acceptance row {index} design_element",
                ),
                "validation": _text(
                    row.get("validation"), f"acceptance row {index} validation"
                ),
                "tests": tests,
                "public_entrypoint": _text(
                    row.get("public_entrypoint"),
                    f"acceptance row {index} public_entrypoint",
                ),
                "controlled_dependencies": _strings(
                    row.get("controlled_dependencies"),
                    f"acceptance row {index} controlled_dependencies",
                ),
            }
        )
    selectors = _resolve_selectors(
        identities,
        caller_root=caller_root,
        selector_collector=selector_collector,
    )
    return _seal(
        {
            "schema": ACCEPTANCE_SELECTOR_SCHEMA,
            "status": "closed",
            "criterion_count": len(normalized),
            "acceptance_map": normalized,
            "selectors": selectors,
        }
    )


def validate_producer_edges(
    wiring: Mapping[str, Any],
    *,
    caller_root: str | Path,
    selector_collector: Callable[[Path], Iterable[str]] = collect_test_selectors,
) -> dict[str, Any]:
    """Close the W01-W32 ledger and every declared producer consumer map."""
    if not isinstance(wiring, Mapping) or set(wiring) != _WIRING_FIELDS:
        raise WiringClosureError("wiring closure fields are not closed")
    if wiring.get("schema") != WIRING_CLOSURE_SCHEMA:
        raise WiringClosureError("wiring closure schema is invalid")
    if wiring.get("status") not in {"designed", "closed"}:
        raise WiringClosureError(
            "wiring closure status must be designed or closed"
        )
    if wiring.get("status") == "closed":
        raise WiringClosureError(
            "W31 requires a genuine external host receipt before closure"
        )

    raw_edges = _items(wiring.get("edges"), "wiring edges")
    edge_ids = [
        str(edge.get("id") or "") if isinstance(edge, Mapping) else ""
        for edge in raw_edges
    ]
    if tuple(edge_ids) != EXPECTED_EDGE_IDS:
        missing = [
            edge_id for edge_id in EXPECTED_EDGE_IDS
            if edge_id not in edge_ids
        ]
        extra = [
            edge_id for edge_id in edge_ids
            if edge_id not in EXPECTED_EDGE_IDS
        ]
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(extra))
        raise WiringClosureError(
            "wiring edge ids must be exactly W01-W32"
            + (": " + "; ".join(detail) if detail else "")
        )
    if wiring.get("edge_count") != len(EXPECTED_EDGE_IDS):
        raise WiringClosureError("edge_count must be exactly 32")

    normalized_edges: list[dict[str, Any]] = []
    identities: list[str] = []
    for edge in raw_edges:
        edge_id = str(edge.get("id"))
        fields = set(edge)
        if not _EDGE_REQUIRED_FIELDS.issubset(fields) or not fields.issubset(
            _EDGE_REQUIRED_FIELDS | _EDGE_OPTIONAL_FIELDS
        ):
            raise WiringClosureError(
                f"wiring edge {edge_id} fields are not closed"
            )
        if edge.get("required_status") != "closed":
            raise WiringClosureError(
                f"wiring edge {edge_id} must require closed status"
            )
        edge_test = _text(
            edge.get("edge_test"), f"wiring edge {edge_id} edge_test"
        )
        additional = _strings(
            edge.get("additional_tests") or [],
            f"wiring edge {edge_id} additional_tests",
            allow_empty=True,
        )
        identities.extend([edge_test, *additional])
        normalized = {
            "id": edge_id,
            "producer": _text(
                edge.get("producer"), f"wiring edge {edge_id} producer"
            ),
            "artifact_or_contract": _text(
                edge.get("artifact_or_contract"),
                f"wiring edge {edge_id} artifact_or_contract",
            ),
            "consumer": _text(
                edge.get("consumer"), f"wiring edge {edge_id} consumer"
            ),
            "edge_test": edge_test,
            "required_status": "closed",
        }
        if "additional_tests" in edge:
            normalized["additional_tests"] = additional
        if "semantic_obligation" in edge:
            normalized["semantic_obligation"] = _text(
                edge.get("semantic_obligation"),
                f"wiring edge {edge_id} semantic_obligation",
            )
        normalized_edges.append(normalized)

    raw_producers = _items(wiring.get("producer_closure"), "producer_closure")
    if len(raw_producers) != EXPECTED_PRODUCER_COUNT:
        raise WiringClosureError(
            "producer_closure must contain exactly "
            f"{EXPECTED_PRODUCER_COUNT} producers"
        )
    producers: set[str] = set()
    producer_edge_ids: dict[str, frozenset[str]] = {}
    normalized_producers: list[dict[str, Any]] = []
    for index, row in enumerate(raw_producers, 1):
        if not isinstance(row, Mapping) or set(row) != _PRODUCER_FIELDS:
            raise WiringClosureError(
                f"producer closure row {index} fields are not closed"
            )
        producer = _text(
            row.get("producer"), f"producer closure row {index} producer"
        )
        if producer in producers:
            raise WiringClosureError(f"producer closure is duplicated: {producer}")
        producers.add(producer)
        classes = _strings(
            row.get("consumer_classes"),
            f"producer {producer} consumer_classes",
        )
        expected_classes = (
            5 if producer in _FIVE_CLASS_BOUNDARY_PRODUCERS else 7
        )
        if len(classes) != expected_classes:
            raise WiringClosureError(
                f"producer {producer} must name exactly {expected_classes} "
                "closed consumer classes"
            )
        producer_edges = _strings(
            row.get("edge_ids"), f"producer {producer} edge_ids"
        )
        unknown = [
            edge_id for edge_id in producer_edges
            if edge_id not in EXPECTED_EDGE_IDS
        ]
        if unknown:
            raise WiringClosureError(
                f"producer {producer} names unknown edges: {', '.join(unknown)}"
            )
        producer_edge_ids[producer] = frozenset(producer_edges)
        normalized_producers.append(
            {
                "producer": producer,
                "consumer_classes": classes,
                "edge_ids": producer_edges,
            }
        )
    canonical_producers = set(_CANONICAL_PRODUCER_EDGE_IDS)
    if producers != canonical_producers:
        raise WiringClosureError(
            "producer edge binding identities mismatch: "
            f"missing={sorted(canonical_producers - producers)}; "
            f"unexpected={sorted(producers - canonical_producers)}"
        )
    canonical_edge_producers = {
        edge_id: frozenset(
            producer
            for producer, producer_edges in _CANONICAL_PRODUCER_EDGE_IDS.items()
            if edge_id in producer_edges
        )
        for edge_id in EXPECTED_EDGE_IDS
    }
    actual_edge_producers = {
        edge_id: frozenset(
            producer
            for producer, producer_edges in producer_edge_ids.items()
            if edge_id in producer_edges
        )
        for edge_id in EXPECTED_EDGE_IDS
    }
    for edge_id in EXPECTED_EDGE_IDS:
        expected = canonical_edge_producers[edge_id]
        actual = actual_edge_producers[edge_id]
        if actual != expected:
            raise WiringClosureError(
                f"producer edge binding mismatch for {edge_id}: "
                f"expected={sorted(expected)}; actual={sorted(actual)}"
            )
    selectors = _resolve_selectors(
        identities,
        caller_root=caller_root,
        selector_collector=selector_collector,
    )
    return _seal(
        {
            "schema": WIRING_CLOSURE_SCHEMA,
            "status": "designed",
            "edge_count": len(normalized_edges),
            "producer_count": len(normalized_producers),
            "edges": normalized_edges,
            "producer_closure": normalized_producers,
            "selectors": selectors,
            "release_rule": _text(wiring.get("release_rule"), "release_rule"),
        }
    )
