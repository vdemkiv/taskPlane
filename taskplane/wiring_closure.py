"""Pure validators for Design acceptance selectors and wiring closure.

The Design Contract is authority for what must be connected.  This module
turns that authored inventory into closed, content-fingerprinted evidence only
after every exact ``test_file.py::selector`` identity resolves beneath the
caller's repository root.  It deliberately imports no loop or review runtime.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import time
from typing import Any


ACCEPTANCE_SELECTOR_SCHEMA = "taskplane.acceptance-selector-map/v1"
WIRING_CLOSURE_SCHEMA = "taskplane.wiring-closure/v1"
CANDIDATE_CHECKOUT_WIRING_SCHEMA = "taskplane.candidate-checkout-wiring/v1"
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


_REGISTERED_CHECKOUT_TOKEN = object()
_CANDIDATE_RECEIPT_TOKEN = object()
_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_CANDIDATE_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "producer",
        "checkout_identity",
        "selector_evidence",
        "edge_evidence",
        "fingerprint",
    }
)
_CHECKOUT_IDENTITY_FIELDS = frozenset(
    {
        "repository_fingerprint",
        "git_common_dir_fingerprint",
        "checkout_realpath_fingerprint",
        "full_head_sha",
        "tree_sha",
        "clean_status",
        "requirement_id",
        "design_fingerprint",
        "plan_fingerprint",
    }
)
_SELECTOR_EVIDENCE_FIELDS = frozenset(
    {
        "tracked_test_path",
        "git_blob_oid",
        "exact_selector",
        "argv",
        "exit_code",
        "stdout_sha256",
        "stderr_sha256",
        "started_at",
        "ended_at",
    }
)
_EDGE_EVIDENCE_FIELDS = frozenset(
    {
        "edge_id",
        "producer_module_symbol",
        "consumer_module_symbol",
        "artifact_or_contract",
        "positive_selector",
        "severed_selector",
        "clean_result_fingerprint",
        "mutation_worktree_identity",
        "one_edge_diff_fingerprint",
        "severed_result_fingerprint",
    }
)
EXPECTED_R0013_PRODUCTION_EDGE_IDS = tuple(
    f"E{number:02d}" for number in range(1, 22)
)
R0013_PRODUCTION_EDGE_BINDINGS = (
    ("E01", "Design native capability inventory", "design_contract.design_dod_errors", "taskplane/tests/test_r0013_native_authority.py::test_complete_native_capability_map_is_required_by_design_and_plan"),
    ("E02", "Design native capability inventory", "design_contract.design_plan_errors", "taskplane/tests/test_r0013_native_authority.py::test_complete_native_capability_map_is_required_by_design_and_plan"),
    ("E03", "loop.select_ready_tasks", "sealed native dispatch intent", "taskplane/tests/test_r0013_native_dispatch.py::test_severed_readiness_dispatch_completion_and_wait_fail_without_fallback"),
    ("E04", "sealed ready set", "build_c.assign_scopes", "taskplane/tests/test_r0013_native_dispatch.py::test_build_c_consumes_one_sealed_ready_set_without_reclassification"),
    ("E05", "native dispatch intent", "Codex screen-dispatch observation", "taskplane/tests/test_r0013_native_budget.py::test_cut_screen_dispatch_to_telemetry_binding_refuses_dispatch"),
    ("E06", "Codex completion or attention", "native event wait", "taskplane/tests/test_r0013_native_dispatch.py::test_one_native_wait_wakes_on_completion_or_attention"),
    ("E07", "lenses/catalog.json", "design_sweep.validate_design_sweep", "taskplane/tests/test_r0013_design_sweep.py::test_exactly_one_quick_result_for_all_26_catalog_lenses"),
    ("E08", "Design native lens results", "Design sweep dispositions", "taskplane/tests/test_r0013_design_sweep.py::test_every_design_result_has_one_disposition"),
    ("E09", "delivery_policy zero-lens authorization", "Build/Fix/Evaluate/EM dispatch", "taskplane/tests/test_r0013_zero_lens.py::test_build_fix_evaluate_and_em_start_zero_taskplane_lens_workers"),
    ("E10", "direct evaluator/EM result", "empty expected-lens gate", "taskplane/tests/test_r0013_zero_lens.py::test_empty_expected_collection_is_valid_success"),
    ("E11", "Requirement seven outcomes", "Plan pair map", "taskplane/tests/test_r0013_wave_ceiling.py::test_exactly_seven_acceptance_outcomes_and_complete_21_pair_map"),
    ("E12", "stage state delta", "brief_projection.project", "taskplane/tests/test_r0013_native_budget.py::test_delta_handoff_is_below_4000_tokens_and_contains_only_required_fields"),
    ("E13", "host provider usage", "dispatch_telemetry active binding", "taskplane/tests/test_r0013_native_budget.py::test_live_hook_dispatch_populates_active_observed_tokens"),
    ("E14", "dispatch telemetry ledger", "next native start budget screen", "taskplane/tests/test_r0013_native_budget.py::test_breach_stops_before_any_next_spawn"),
    ("E15", "Design acceptance map", "candidate checkout selector runner", "taskplane/tests/test_r0013_real_checkout_wiring.py::test_pinned_and_final_checkout_execute_same_named_selector_inventory"),
    ("E16", "registered Git checkout facts", "candidate checkout wiring receipt", "taskplane/tests/test_r0013_real_checkout_wiring.py::test_candidate_receipt_refuses_non_git_temp_or_head_mismatch"),
    ("E17", "candidate checkout wiring receipt", "checkpoint and terminal finalizer", "taskplane/tests/test_r0013_real_checkout_wiring.py::test_cut_design_wiring_validator_from_checkpoint_fails_closed"),
    ("E18", "eight prepared terminal surfaces", "terminal_truth.commit_delivery", "taskplane/tests/test_r0013_terminal_finalization.py::test_finalization_refuses_each_missing_nonterminal_or_mixed_sha_surface"),
    ("E19", "native usage receipt", "terminal bundle", "taskplane/tests/test_r0013_native_budget.py::test_active_usage_contributes_to_all_four_budget_totals"),
    ("E20", "terminal bundle CAS head", "Done/merge/push/release guards", "taskplane/tests/test_r0013_terminal_finalization.py::test_sha_changing_merge_invalidates_finalization"),
    ("E21", "terminal bundle", "exports aggregate projection", "taskplane/tests/test_r0013_terminal_finalization.py::test_finalize_replay_is_idempotent_on_one_sha"),
)
_R0013_EDGE_BINDING_BY_ID = {
    edge_id: (producer, consumer, selector)
    for edge_id, producer, consumer, selector in R0013_PRODUCTION_EDGE_BINDINGS
}
R0013_NAMED_SELECTOR_INVENTORY = tuple(dict.fromkeys(
    selector for _, _, _, selector in R0013_PRODUCTION_EDGE_BINDINGS
))


@dataclass(frozen=True, slots=True)
class RegisteredCheckout:
    """Non-serializable live registration for one exact Git checkout.

    A receipt builder accepts this object, never caller-authored checkout
    facts.  The live root remains private and is not projected into evidence.
    """

    root: Path
    repository_fingerprint: str
    git_common_dir_fingerprint: str
    checkout_realpath_fingerprint: str
    full_head_sha: str
    tree_sha: str
    clean_status: str
    mutation_edge_id: str | None
    mutation_diff_fingerprint: str | None
    _token: object

    def __reduce__(self):  # pragma: no cover - exercised by security callers
        raise TypeError("registered checkout authority is not serializable")


class CandidateCheckoutReceipt(dict):
    """Sealed evidence retaining a live binding to its registered Git CAS."""

    __slots__ = ("_registration", "_token")

    def __init__(
        self,
        value: Mapping[str, Any],
        *,
        registration: RegisteredCheckout,
        token: object,
    ) -> None:
        if token is not _CANDIDATE_RECEIPT_TOKEN:
            raise TypeError("candidate receipts are produced by the live executor")
        super().__init__(value)
        self._registration = registration
        self._token = token

    def __reduce__(self):
        raise TypeError("live candidate checkout receipt is not serializable")


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


def _collect_test_selectors_from_source(
    source: str, *, filename: str
) -> frozenset[str]:
    try:
        tree = ast.parse(source, filename=filename)
    except (UnicodeError, SyntaxError) as exc:
        raise WiringClosureError(
            f"declared test file cannot be collected: {filename}: {exc}"
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


def collect_test_selectors(path: Path) -> frozenset[str]:
    """Collect exact module-function and ``TestClass::method`` selectors."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise WiringClosureError(
            f"declared test file cannot be collected: {path}: {exc}"
        ) from exc
    return _collect_test_selectors_from_source(source, filename=str(path))


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


def _git_text(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise WiringClosureError(
            f"registered checkout Git observation failed: {detail or args[0]}"
        )
    return result.stdout.strip()


def _fingerprint_text(value: Any, field: str) -> str:
    text = _text(value, field)
    if not _FINGERPRINT.fullmatch(text):
        raise WiringClosureError(f"{field} must be a SHA-256 fingerprint")
    return text


def _object_id(value: Any, field: str) -> str:
    text = _text(value, field)
    if not _OBJECT_ID.fullmatch(text):
        raise WiringClosureError(f"{field} must be a full Git object id")
    return text


def register_candidate_checkout(
    checkout_root: str | Path,
    *,
    repository_fingerprint: str,
    expected_head_sha: str,
    require_clean: bool = True,
) -> RegisteredCheckout:
    """Observe and register a non-temporary Git checkout at one exact SHA."""
    root = Path(checkout_root).resolve()
    try:
        root.relative_to(Path(tempfile.gettempdir()).resolve())
    except ValueError:
        pass
    else:
        raise WiringClosureError(
            "candidate checkout cannot be an arbitrary temporary substitute"
        )
    if not root.is_dir():
        raise WiringClosureError("candidate checkout root is not a directory")
    top = Path(_git_text(root, "rev-parse", "--show-toplevel")).resolve()
    if top != root:
        raise WiringClosureError(
            "candidate checkout root must be the registered Git toplevel"
        )
    head = _object_id(_git_text(root, "rev-parse", "HEAD"), "Git HEAD")
    expected = _object_id(expected_head_sha, "expected_head_sha")
    if head != expected:
        raise WiringClosureError("candidate checkout HEAD does not match expected SHA")
    tree_sha = _object_id(
        _git_text(root, "rev-parse", "HEAD^{tree}"), "candidate tree SHA"
    )
    common_dir_raw = _git_text(root, "rev-parse", "--git-common-dir")
    common_dir = Path(common_dir_raw)
    if not common_dir.is_absolute():
        common_dir = root / common_dir
    common_dir = common_dir.resolve()
    status = _git_text(root, "status", "--porcelain=v1", "--untracked-files=all")
    clean_status = "clean" if not status else "dirty"
    if require_clean and clean_status != "clean":
        raise WiringClosureError("candidate checkout must be clean at exact HEAD")
    return RegisteredCheckout(
        root=root,
        repository_fingerprint=_fingerprint_text(
            repository_fingerprint, "repository_fingerprint"
        ),
        git_common_dir_fingerprint=hashlib.sha256(
            str(common_dir).encode("utf-8")
        ).hexdigest(),
        checkout_realpath_fingerprint=hashlib.sha256(
            str(root).encode("utf-8")
        ).hexdigest(),
        full_head_sha=head,
        tree_sha=tree_sha,
        clean_status=clean_status,
        mutation_edge_id=None,
        mutation_diff_fingerprint=None,
        _token=_REGISTERED_CHECKOUT_TOKEN,
    )


def register_edge_mutation_checkout(
    checkout_root: str | Path,
    *,
    clean_registration: RegisteredCheckout,
    edge_id: str,
) -> RegisteredCheckout:
    """Bind one dirty sibling worktree to one declared Design edge mutation."""
    if not isinstance(clean_registration, RegisteredCheckout) or \
            clean_registration._token is not _REGISTERED_CHECKOUT_TOKEN or \
            clean_registration.clean_status != "clean":
        raise WiringClosureError("clean registered checkout authority is required")
    if edge_id not in _R0013_EDGE_BINDING_BY_ID:
        raise WiringClosureError("mutation edge id is not a Design E01-E21 edge")
    root = Path(checkout_root).resolve()
    if root == clean_registration.root:
        raise WiringClosureError("edge mutation must use a sibling Git worktree")
    observed = register_candidate_checkout(
        root,
        repository_fingerprint=clean_registration.repository_fingerprint,
        expected_head_sha=clean_registration.full_head_sha,
        require_clean=False,
    )
    if observed.git_common_dir_fingerprint != \
            clean_registration.git_common_dir_fingerprint:
        raise WiringClosureError("edge mutation worktree is foreign")
    status = _git_text(root, "status", "--porcelain=v1", "--untracked-files=all")
    if not status or any(line.startswith("?? ") for line in status.splitlines()):
        raise WiringClosureError(
            "edge mutation must be a tracked one-edge diff, not generated files"
        )
    diff = _git_text(root, "diff", "--binary", "--", ".")
    if not diff:
        raise WiringClosureError("edge mutation worktree has no tracked diff")
    return RegisteredCheckout(
        root=observed.root,
        repository_fingerprint=observed.repository_fingerprint,
        git_common_dir_fingerprint=observed.git_common_dir_fingerprint,
        checkout_realpath_fingerprint=observed.checkout_realpath_fingerprint,
        full_head_sha=observed.full_head_sha,
        tree_sha=observed.tree_sha,
        clean_status="one-edge-mutation",
        mutation_edge_id=edge_id,
        mutation_diff_fingerprint=hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        _token=_REGISTERED_CHECKOUT_TOKEN,
    )


def _revalidate_registered_checkout(
    registration: RegisteredCheckout,
) -> RegisteredCheckout:
    """Re-observe the exact Git CAS instead of trusting captured identity fields."""
    if not isinstance(registration, RegisteredCheckout) or \
            registration._token is not _REGISTERED_CHECKOUT_TOKEN or \
            registration.clean_status != "clean" or \
            registration.mutation_edge_id is not None or \
            registration.mutation_diff_fingerprint is not None:
        raise WiringClosureError("live clean registered checkout authority is required")
    root = registration.root.resolve()
    if Path(_git_text(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise WiringClosureError("registered checkout Git toplevel changed")
    head = _object_id(_git_text(root, "rev-parse", "HEAD"), "Git HEAD")
    tree = _object_id(_git_text(root, "rev-parse", "HEAD^{tree}"), "Git tree")
    common_raw = Path(_git_text(root, "rev-parse", "--git-common-dir"))
    common = (common_raw if common_raw.is_absolute() else root / common_raw).resolve()
    status = _git_text(root, "status", "--porcelain=v1", "--untracked-files=all")
    observations = (
        head == registration.full_head_sha,
        tree == registration.tree_sha,
        hashlib.sha256(str(root).encode("utf-8")).hexdigest()
        == registration.checkout_realpath_fingerprint,
        hashlib.sha256(str(common).encode("utf-8")).hexdigest()
        == registration.git_common_dir_fingerprint,
        status == "",
    )
    if not all(observations):
        raise WiringClosureError(
            "registered candidate checkout CAS is stale, dirty, or contradictory"
        )
    return registration


def _revalidate_edge_mutation_checkout(
    registration: RegisteredCheckout,
    *,
    clean_registration: RegisteredCheckout,
    edge_id: str,
) -> RegisteredCheckout:
    """Re-observe one registered mutation immediately before its selector."""
    if not isinstance(registration, RegisteredCheckout) or \
            registration._token is not _REGISTERED_CHECKOUT_TOKEN or \
            registration.clean_status != "one-edge-mutation" or \
            registration.mutation_edge_id != edge_id or \
            registration.mutation_diff_fingerprint is None:
        raise WiringClosureError(
            f"live registered one-edge mutation is required for {edge_id}"
        )
    root = registration.root.resolve()
    top = Path(_git_text(root, "rev-parse", "--show-toplevel")).resolve()
    head = _object_id(_git_text(root, "rev-parse", "HEAD"), "mutation Git HEAD")
    tree = _object_id(
        _git_text(root, "rev-parse", "HEAD^{tree}"), "mutation Git tree"
    )
    common_raw = Path(_git_text(root, "rev-parse", "--git-common-dir"))
    common = (common_raw if common_raw.is_absolute() else root / common_raw).resolve()
    status = _git_text(root, "status", "--porcelain=v1", "--untracked-files=all")
    diff = _git_text(root, "diff", "--binary", "--", ".")
    current_diff_fingerprint = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    observations = (
        root != clean_registration.root,
        top == root,
        head == registration.full_head_sha == clean_registration.full_head_sha,
        tree == registration.tree_sha == clean_registration.tree_sha,
        hashlib.sha256(str(root).encode("utf-8")).hexdigest()
        == registration.checkout_realpath_fingerprint,
        hashlib.sha256(str(common).encode("utf-8")).hexdigest()
        == registration.git_common_dir_fingerprint
        == clean_registration.git_common_dir_fingerprint,
        registration.repository_fingerprint
        == clean_registration.repository_fingerprint,
        bool(status),
        not any(line.startswith("?? ") for line in status.splitlines()),
        bool(diff),
        current_diff_fingerprint == registration.mutation_diff_fingerprint,
    )
    if not all(observations):
        raise WiringClosureError(
            f"mutation checkout changed after registration for {edge_id}"
        )
    return registration


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WiringClosureError(f"{field} must be finite")
    result = float(value)
    if not math.isfinite(result):
        raise WiringClosureError(f"{field} must be finite")
    return result


def _normalize_selector_evidence(
    rows: Any,
    *,
    registration: RegisteredCheckout | None = None,
) -> list[dict[str, Any]]:
    values = _items(rows, "selector_evidence")
    normalized: list[dict[str, Any]] = []
    selectors: set[str] = set()
    observed_blobs: dict[str, str] = {}
    observed_symbols: dict[str, frozenset[str]] = {}
    for index, row in enumerate(values, 1):
        if not isinstance(row, Mapping) or set(row) != _SELECTOR_EVIDENCE_FIELDS:
            raise WiringClosureError(
                f"selector evidence row {index} fields are not closed"
            )
        path, selector_symbol = _selector_identity(row.get("exact_selector"))
        tracked_path = _text(row.get("tracked_test_path"), "tracked_test_path")
        if path != tracked_path:
            raise WiringClosureError("selector evidence test path is contradictory")
        blob = _object_id(row.get("git_blob_oid"), "git_blob_oid")
        selector = _text(row.get("exact_selector"), "exact_selector")
        if selector in selectors:
            raise WiringClosureError("selector evidence contains duplicates")
        selectors.add(selector)
        argv = _strings(row.get("argv"), "selector argv")
        if selector not in argv:
            raise WiringClosureError("selector argv does not execute exact selector")
        if row.get("exit_code") != 0:
            raise WiringClosureError("clean candidate selector did not pass")
        started = _number(row.get("started_at"), "selector started_at")
        ended = _number(row.get("ended_at"), "selector ended_at")
        if ended < started:
            raise WiringClosureError("selector evidence time ordering is invalid")
        if registration is not None:
            if tracked_path not in observed_blobs:
                observed_blobs[tracked_path] = _object_id(
                    _git_text(
                        registration.root, "rev-parse", f"HEAD:{tracked_path}"
                    ),
                    "tracked selector blob",
                )
                observed_symbols[tracked_path] = _collect_test_selectors_from_source(
                    _git_text(
                        registration.root, "show", f"HEAD:{tracked_path}"
                    ),
                    filename=f"HEAD:{tracked_path}",
                )
            if observed_blobs[tracked_path] != blob:
                raise WiringClosureError("selector Git blob does not match candidate HEAD")
            if selector_symbol not in observed_symbols[tracked_path]:
                raise WiringClosureError(
                    "selector symbol does not exist in the registered HEAD blob"
                )
        normalized.append(
            {
                "tracked_test_path": tracked_path,
                "git_blob_oid": blob,
                "exact_selector": selector,
                "argv": argv,
                "exit_code": 0,
                "stdout_sha256": _fingerprint_text(
                    row.get("stdout_sha256"), "stdout_sha256"
                ),
                "stderr_sha256": _fingerprint_text(
                    row.get("stderr_sha256"), "stderr_sha256"
                ),
                "started_at": started,
                "ended_at": ended,
            }
        )
    return normalized


def _normalize_edge_evidence(
    rows: Any, *, checkout_identity: Mapping[str, Any]
) -> list[dict[str, Any]]:
    values = _items(rows, "edge_evidence")
    ids = [str(row.get("edge_id") or "") if isinstance(row, Mapping) else ""
           for row in values]
    if tuple(ids) != EXPECTED_R0013_PRODUCTION_EDGE_IDS:
        raise WiringClosureError("edge evidence must be exactly E01-E21 in order")
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(values, 1):
        if not isinstance(row, Mapping) or set(row) != _EDGE_EVIDENCE_FIELDS:
            raise WiringClosureError(
                f"edge evidence row {index} fields are not closed"
            )
        positive = _text(row.get("positive_selector"), "positive_selector")
        severed = _text(row.get("severed_selector"), "severed_selector")
        _selector_identity(positive)
        if severed != positive:
            raise WiringClosureError(
                "severed edge must execute the same exact production selector"
            )
        expected_producer, expected_consumer, expected_selector = \
            _R0013_EDGE_BINDING_BY_ID[ids[index - 1]]
        producer = _text(
            row.get("producer_module_symbol"), "producer_module_symbol"
        )
        consumer = _text(
            row.get("consumer_module_symbol"), "consumer_module_symbol"
        )
        if (producer, consumer, positive) != (
            expected_producer, expected_consumer, expected_selector
        ):
            raise WiringClosureError(
                f"edge evidence binding mismatch for {ids[index - 1]}"
            )
        mutation = row.get("mutation_worktree_identity")
        required_mutation = {
            "repository_fingerprint",
            "git_common_dir_fingerprint",
            "full_head_sha",
            "clean_status",
        }
        if not isinstance(mutation, Mapping) or set(mutation) != required_mutation:
            raise WiringClosureError("mutation worktree identity fields are not closed")
        if (
            mutation.get("repository_fingerprint")
            != checkout_identity.get("repository_fingerprint")
            or mutation.get("git_common_dir_fingerprint")
            != checkout_identity.get("git_common_dir_fingerprint")
            or mutation.get("full_head_sha") != checkout_identity.get("full_head_sha")
            or mutation.get("clean_status") != "one-edge-mutation"
        ):
            raise WiringClosureError(
                "mutation worktree is foreign, wrong-SHA, or not one-edge scoped"
            )
        clean_fingerprint = _fingerprint_text(
            row.get("clean_result_fingerprint"), "clean_result_fingerprint"
        )
        severed_fingerprint = _fingerprint_text(
            row.get("severed_result_fingerprint"), "severed_result_fingerprint"
        )
        if clean_fingerprint == severed_fingerprint:
            raise WiringClosureError(
                "clean and severed selector results must be observably distinct"
            )
        normalized.append(
            {
                "edge_id": ids[index - 1],
                "producer_module_symbol": producer,
                "consumer_module_symbol": consumer,
                "artifact_or_contract": _text(
                    row.get("artifact_or_contract"), "artifact_or_contract"
                ),
                "positive_selector": positive,
                "severed_selector": severed,
                "clean_result_fingerprint": clean_fingerprint,
                "mutation_worktree_identity": dict(mutation),
                "one_edge_diff_fingerprint": _fingerprint_text(
                    row.get("one_edge_diff_fingerprint"),
                    "one_edge_diff_fingerprint",
                ),
                "severed_result_fingerprint": severed_fingerprint,
            }
        )
    return normalized


def create_candidate_checkout_receipt(
    registration: RegisteredCheckout,
    *,
    requirement_id: str,
    design_fingerprint: str,
    plan_fingerprint: str,
    selector_evidence: Sequence[Mapping[str, Any]],
    edge_evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Refuse legacy caller-authored evidence; use execute_candidate_checkout."""
    del registration, requirement_id, design_fingerprint, plan_fingerprint
    del selector_evidence, edge_evidence
    raise WiringClosureError(
        "caller-authored selector/edge evidence cannot mint candidate authority"
    )


def validate_candidate_checkout_receipt(
    receipt: Mapping[str, Any],
    *,
    expected_repository_fingerprint: str | None = None,
    expected_head_sha: str | None = None,
    expected_requirement_id: str | None = None,
) -> dict[str, Any]:
    """Validate a complete, non-opaque candidate wiring receipt."""
    if not isinstance(receipt, CandidateCheckoutReceipt) or \
            receipt._token is not _CANDIDATE_RECEIPT_TOKEN:
        raise WiringClosureError(
            "live registered candidate checkout receipt is required"
        )
    registration = _revalidate_registered_checkout(receipt._registration)
    if not isinstance(receipt, Mapping) or set(receipt) != _CANDIDATE_RECEIPT_FIELDS:
        raise WiringClosureError("candidate checkout wiring receipt fields are not closed")
    if receipt.get("schema") != CANDIDATE_CHECKOUT_WIRING_SCHEMA:
        raise WiringClosureError("candidate checkout wiring receipt schema is invalid")
    if receipt.get("status") != "closed" or receipt.get("producer") != \
            "taskplane.wiring-closure-native-runner/v1":
        raise WiringClosureError("candidate checkout wiring receipt is not closed")
    identity = receipt.get("checkout_identity")
    if not isinstance(identity, Mapping) or set(identity) != _CHECKOUT_IDENTITY_FIELDS:
        raise WiringClosureError("candidate checkout identity fields are not closed")
    normalized_identity = {
        "repository_fingerprint": _fingerprint_text(
            identity.get("repository_fingerprint"), "repository_fingerprint"
        ),
        "git_common_dir_fingerprint": _fingerprint_text(
            identity.get("git_common_dir_fingerprint"),
            "git_common_dir_fingerprint",
        ),
        "checkout_realpath_fingerprint": _fingerprint_text(
            identity.get("checkout_realpath_fingerprint"),
            "checkout_realpath_fingerprint",
        ),
        "full_head_sha": _object_id(identity.get("full_head_sha"), "full_head_sha"),
        "tree_sha": _object_id(identity.get("tree_sha"), "tree_sha"),
        "clean_status": _text(identity.get("clean_status"), "clean_status"),
        "requirement_id": _text(identity.get("requirement_id"), "requirement_id"),
        "design_fingerprint": _fingerprint_text(
            identity.get("design_fingerprint"), "design_fingerprint"
        ),
        "plan_fingerprint": _fingerprint_text(
            identity.get("plan_fingerprint"), "plan_fingerprint"
        ),
    }
    if normalized_identity["clean_status"] != "clean":
        raise WiringClosureError("candidate checkout wiring receipt is not clean")
    if expected_repository_fingerprint is not None and \
            normalized_identity["repository_fingerprint"] != \
            _fingerprint_text(
                expected_repository_fingerprint, "expected_repository_fingerprint"
            ):
        raise WiringClosureError("candidate checkout wiring receipt is foreign")
    if expected_head_sha is not None and normalized_identity["full_head_sha"] != \
            _object_id(expected_head_sha, "expected_head_sha"):
        raise WiringClosureError("candidate checkout wiring receipt is wrong-SHA")
    if expected_requirement_id is not None and \
            normalized_identity["requirement_id"] != \
            _text(expected_requirement_id, "expected_requirement_id"):
        raise WiringClosureError("candidate checkout wiring requirement is foreign")
    selectors = _normalize_selector_evidence(
        receipt.get("selector_evidence"), registration=registration
    )
    if tuple(row["exact_selector"] for row in selectors) != \
            R0013_NAMED_SELECTOR_INVENTORY:
        raise WiringClosureError(
            "selector evidence does not match the exact Design selector inventory"
        )
    edges = _normalize_edge_evidence(
        receipt.get("edge_evidence"), checkout_identity=normalized_identity
    )
    normalized = {
        "schema": CANDIDATE_CHECKOUT_WIRING_SCHEMA,
        "status": "closed",
        "producer": "taskplane.wiring-closure-native-runner/v1",
        "checkout_identity": normalized_identity,
        "selector_evidence": selectors,
        "edge_evidence": edges,
    }
    expected_fingerprint = hashlib.sha256(_canonical_bytes(normalized)).hexdigest()
    if receipt.get("fingerprint") != expected_fingerprint:
        raise WiringClosureError("candidate checkout wiring fingerprint mismatch")
    normalized["fingerprint"] = expected_fingerprint
    return CandidateCheckoutReceipt(
        normalized,
        registration=registration,
        token=_CANDIDATE_RECEIPT_TOKEN,
    )


def _execution_result(
    result: Any,
    *,
    selector: str,
    argv: Sequence[str],
    started_at: float,
    ended_at: float,
) -> dict[str, Any]:
    """Normalize one real command result without trusting caller fingerprints."""
    if isinstance(result, Mapping):
        exit_code = result.get("returncode", result.get("exit_code"))
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
    else:
        exit_code = getattr(result, "returncode", None)
        stdout = getattr(result, "stdout", "")
        stderr = getattr(result, "stderr", "")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise WiringClosureError("selector runner returned no integer exit code")
    if isinstance(stdout, bytes):
        stdout_bytes = stdout
    else:
        stdout_bytes = str(stdout or "").encode("utf-8", errors="replace")
    if isinstance(stderr, bytes):
        stderr_bytes = stderr
    else:
        stderr_bytes = str(stderr or "").encode("utf-8", errors="replace")
    normalized = {
        "selector": selector,
        "argv": list(argv),
        "exit_code": exit_code,
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "started_at": _number(started_at, "selector started_at"),
        "ended_at": _number(ended_at, "selector ended_at"),
    }
    normalized["result_fingerprint"] = hashlib.sha256(
        _canonical_bytes(normalized)
    ).hexdigest()
    return normalized


def execute_candidate_checkout(
    registration: RegisteredCheckout,
    *,
    requirement_id: str,
    design_fingerprint: str,
    plan_fingerprint: str,
    mutation_checkouts: Mapping[str, RegisteredCheckout],
    selector_inventory: Sequence[str] = R0013_NAMED_SELECTOR_INVENTORY,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Execute the exact Design inventory in a clean and 21 severed checkouts.

    The clean selector must pass and each one-edge sibling mutation must make
    that edge's *same* production selector fail.  Only engine-observed Git
    blobs and command bytes are admitted to the resulting wiring receipt.
    """
    if not isinstance(registration, RegisteredCheckout) or \
            registration._token is not _REGISTERED_CHECKOUT_TOKEN or \
            registration.clean_status != "clean":
        raise WiringClosureError("live clean registered checkout authority is required")
    inventory = tuple(_text(item, "selector_inventory") for item in selector_inventory)
    if inventory != R0013_NAMED_SELECTOR_INVENTORY:
        raise WiringClosureError("selector inventory is not the exact Design inventory")
    if not isinstance(mutation_checkouts, Mapping) or \
            tuple(mutation_checkouts) != EXPECTED_R0013_PRODUCTION_EDGE_IDS:
        raise WiringClosureError("mutation checkout set must be exactly E01-E21 in order")

    def default_runner(root: Path, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            list(argv), cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
            env=environment,
        )

    execute = default_runner
    clean_results: dict[str, dict[str, Any]] = {}
    selector_evidence: list[dict[str, Any]] = []
    for selector in inventory:
        _revalidate_registered_checkout(registration)
        tracked_path, _ = _selector_identity(selector)
        tracked = _git_text(
            registration.root, "ls-files", "--error-unmatch", "--", tracked_path
        )
        if tracked != tracked_path:
            raise WiringClosureError("selector test is not tracked at candidate HEAD")
        blob = _object_id(
            _git_text(registration.root, "rev-parse", f"HEAD:{tracked_path}"),
            "tracked selector blob",
        )
        argv = (
            "python3", "-m", "pytest", "-q", "-p", "no:cacheprovider",
            selector,
        )
        started = float(clock())
        result = execute(registration.root, argv)
        ended = float(clock())
        observed = _execution_result(
            result, selector=selector, argv=argv,
            started_at=started, ended_at=ended,
        )
        _revalidate_registered_checkout(registration)
        if observed["exit_code"] != 0:
            raise WiringClosureError(f"clean candidate selector did not pass: {selector}")
        clean_results[selector] = observed
        selector_evidence.append(
            {
                "tracked_test_path": tracked_path,
                "git_blob_oid": blob,
                "exact_selector": selector,
                "argv": list(argv),
                "exit_code": 0,
                "stdout_sha256": observed["stdout_sha256"],
                "stderr_sha256": observed["stderr_sha256"],
                "started_at": observed["started_at"],
                "ended_at": observed["ended_at"],
            }
        )

    edge_evidence: list[dict[str, Any]] = []
    for edge_id, producer, consumer, selector in R0013_PRODUCTION_EDGE_BINDINGS:
        mutation = mutation_checkouts[edge_id]
        if not isinstance(mutation, RegisteredCheckout) or \
                mutation._token is not _REGISTERED_CHECKOUT_TOKEN or \
                mutation.clean_status != "one-edge-mutation" or \
                mutation.mutation_edge_id != edge_id or \
                mutation.repository_fingerprint != registration.repository_fingerprint or \
                mutation.git_common_dir_fingerprint != registration.git_common_dir_fingerprint or \
                mutation.full_head_sha != registration.full_head_sha or \
                mutation.root == registration.root or \
                mutation.mutation_diff_fingerprint is None:
            raise WiringClosureError(
                f"mutation checkout is not a live same-SHA sibling for {edge_id}"
            )
        mutation = _revalidate_edge_mutation_checkout(
            mutation,
            clean_registration=registration,
            edge_id=edge_id,
        )
        argv = (
            "python3", "-m", "pytest", "-q", "-p", "no:cacheprovider",
            selector,
        )
        started = float(clock())
        result = execute(mutation.root, argv)
        ended = float(clock())
        severed = _execution_result(
            result, selector=selector, argv=argv,
            started_at=started, ended_at=ended,
        )
        _revalidate_edge_mutation_checkout(
            mutation,
            clean_registration=registration,
            edge_id=edge_id,
        )
        if severed["exit_code"] == 0:
            raise WiringClosureError(
                f"severing {edge_id} did not break its exact production selector"
            )
        clean = clean_results[selector]
        if clean["result_fingerprint"] == severed["result_fingerprint"]:
            raise WiringClosureError(
                f"clean and severed results are indistinguishable for {edge_id}"
            )
        edge_evidence.append(
            {
                "edge_id": edge_id,
                "producer_module_symbol": producer,
                "consumer_module_symbol": consumer,
                "artifact_or_contract": "contract:delivery.exact-sha-terminal-truth",
                "positive_selector": selector,
                "severed_selector": selector,
                "clean_result_fingerprint": clean["result_fingerprint"],
                "mutation_worktree_identity": {
                    "repository_fingerprint": mutation.repository_fingerprint,
                    "git_common_dir_fingerprint": mutation.git_common_dir_fingerprint,
                    "full_head_sha": mutation.full_head_sha,
                    "clean_status": mutation.clean_status,
                },
                "one_edge_diff_fingerprint": mutation.mutation_diff_fingerprint,
                "severed_result_fingerprint": severed["result_fingerprint"],
            }
        )
    # Mint inline: there is no callable raw-evidence builder that can attach
    # live authority without completing every observation above.
    _revalidate_registered_checkout(registration)
    identity = {
        "repository_fingerprint": registration.repository_fingerprint,
        "git_common_dir_fingerprint": registration.git_common_dir_fingerprint,
        "checkout_realpath_fingerprint": registration.checkout_realpath_fingerprint,
        "full_head_sha": registration.full_head_sha,
        "tree_sha": registration.tree_sha,
        "clean_status": registration.clean_status,
        "requirement_id": _text(requirement_id, "requirement_id"),
        "design_fingerprint": _fingerprint_text(
            design_fingerprint, "design_fingerprint"
        ),
        "plan_fingerprint": _fingerprint_text(
            plan_fingerprint, "plan_fingerprint"
        ),
    }
    selectors = _normalize_selector_evidence(
        selector_evidence, registration=registration
    )
    if tuple(row["exact_selector"] for row in selectors) != \
            R0013_NAMED_SELECTOR_INVENTORY:
        raise WiringClosureError(
            "selector evidence must execute the exact Design selector inventory"
        )
    edges = _normalize_edge_evidence(
        edge_evidence, checkout_identity=identity
    )
    sealed = _seal(
        {
            "schema": CANDIDATE_CHECKOUT_WIRING_SCHEMA,
            "status": "closed",
            "producer": "taskplane.wiring-closure-native-runner/v1",
            "checkout_identity": identity,
            "selector_evidence": selectors,
            "edge_evidence": edges,
        }
    )
    return CandidateCheckoutReceipt(
        sealed,
        registration=registration,
        token=_CANDIDATE_RECEIPT_TOKEN,
    )
