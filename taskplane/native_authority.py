"""Pure R-0013 Codex-native authority boundary validation.

Taskplane is allowed to describe delivery intent and verify evidence.  Codex
alone owns native scheduling, capacity, admission, agent lifecycle and event
transport.  This module validates that closed Design/Plan responsibility map
and statically checks the production delivery roots without importing or
invoking a host transport.
"""
from __future__ import annotations

import argparse
import ast
from collections import deque
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any


CAPABILITY_INVENTORY_SCHEMA = \
    "taskplane.codex-native-capability-inventory/v1"
NATIVE_AUTHORITY_SCHEMA = "taskplane.native-delivery-authority/v1"
LEAF_READINESS_SCHEMA = "taskplane.acceptance-leaf-readiness/v1"
NATIVE_CAPABILITY_CONTRACT = \
    "contract:design.codex-native-capability-inventory"

REQUIRED_CAPABILITIES = (
    "spawn-and-task-identity",
    "capacity-and-native-admission",
    "parent-child-delegation",
    "message-followup-interrupt",
    "completion-and-attention",
    "event-driven-wait",
    "lifecycle-and-usage-observation",
)

# These values are the closed, approved Design evidence.  They deliberately
# live outside the caller-provided Design object: otherwise a mutated Design
# row could weaken its own authority restrictions or replace a provenance
# claim while still satisfying a merely structural validator.
APPROVED_PINNED_SOURCE_SHA = "27ab9fecad3cf3b477e02678f6fa4d9ec721f54e"

REQUIRED_NATIVE_OWNERS: Mapping[str, str] = {
    "spawn-and-task-identity": (
        "Codex collaboration.spawn_agent and canonical task paths"),
    "capacity-and-native-admission": "Codex dynamic collaboration slots",
    "parent-child-delegation": "Codex native agent tree",
    "message-followup-interrupt": (
        "Codex send_message, followup_task and interrupt_agent"),
    "completion-and-attention": (
        "Codex native lifecycle and final-result events"),
    "event-driven-wait": "Codex collaboration.wait_agent",
    "lifecycle-and-usage-observation": (
        "Codex SubagentStart/SubagentStop and provider usage observations"),
}

REQUIRED_NATIVE_ROLES: Mapping[str, str] = {
    "spawn-and-task-identity": (
        "Emit exact task_name/role_marker/payload/model/effort and bind "
        "observed start to intent."),
    "capacity-and-native-admission": (
        "Classify dependencies/scopes and offer every ready "
        "pairwise-disjoint intent; enforce contract/budget refusal on each "
        "attempted native start."),
    "parent-child-delegation": (
        "Preserve workflow predecessor metadata and one "
        "brief-to-one-native-task binding."),
    "message-followup-interrupt": (
        "Provide bounded correction/escalation policy and preserve partial "
        "evidence."),
    "completion-and-attention": (
        "Bind expected intent to observed completion/attention and validate "
        "exact membership/idempotency."),
    "event-driven-wait": (
        "Declare exact outstanding members and one long-lived event wait; "
        "reissue only after completion/attention."),
    "lifecycle-and-usage-observation": (
        "Bind, aggregate, redact and enforce human budgets before a later "
        "native start."),
}

REQUIRED_FORBIDDEN_BY_CAPABILITY: Mapping[str, tuple[str, ...]] = {
    "spawn-and-task-identity": (
        "spawn runner",
        "agent registry",
        "task rename or alias",
        "synthetic worker identity",
    ),
    "capacity-and-native-admission": (
        "capacity constant",
        "reservation",
        "admission queue",
        "tranche scheduler",
        "overflow queue",
    ),
    "parent-child-delegation": (
        "second agent tree",
        "per-agent stage child",
        "agent attempt hierarchy",
        "execution root",
    ),
    "message-followup-interrupt": (
        "message queue",
        "automatic replacement",
        "cancellation scheduler",
        "waiver",
        "replay",
    ),
    "completion-and-attention": (
        "fabricated completion",
        "inferred worker lifecycle",
        "replacement completion queue",
    ),
    "event-driven-wait": (
        "scheduled polling",
        "model polling",
        "timer wake",
        "event queue",
        "replay scheduler",
    ),
    "lifecycle-and-usage-observation": (
        "worker lifecycle",
        "fabricated usage",
        "null active usage",
        "execution DAG reconstruction",
    ),
}

REQUIRED_EVIDENCE_SOURCES: Mapping[str, tuple[str, ...]] = {
    "spawn-and-task-identity": (
        "skills/tp-go/references/codex-native-dispatch.md:12-24",
        ".codex/hooks.json:73-99",
        "taskplane/loop.py:4390-4426",
    ),
    "capacity-and-native-admission": (
        "skills/tp-go/references/codex-native-dispatch.md:25-28",
        "taskplane/plan_topology.py:1-7,141-221",
    ),
    "parent-child-delegation": (
        "skills/tp-go/references/codex-native-dispatch.md:12-24",
        "taskplane/stage_entities.py:1085-1175,1619-1718",
    ),
    "message-followup-interrupt": (
        "skills/tp-go/references/codex-native-dispatch.md:29-42",
    ),
    "completion-and-attention": (
        "skills/tp-go/references/codex-native-dispatch.md:25-42",
        "taskplane/command_runtime.py",
        ".codex/hooks.json:73-99",
    ),
    "event-driven-wait": (
        "skills/tp-go/references/codex-native-dispatch.md:25-33",
        "taskplane/build_c.py:124-170",
    ),
    "lifecycle-and-usage-observation": (
        ".codex/hooks.json:73-99",
        "taskplane/dispatch_telemetry.py:1-54,92-199",
        "taskplane/progress.py:209-353",
    ),
}

# These broad terms cover the authority classes called out by AC1 even when a
# malicious row deletes the more specific phrase from its mutable forbidden
# list.  The complete per-capability union below catches the remaining closed
# Design restrictions.
_CLOSED_AUTHORITY_CLASSES = (
    "scheduler",
    "capacity",
    "reservation",
    "admission",
    "replay",
    "lease",
    "worker lifecycle",
    "execution dag",
)

REQUIRED_ALLOWED_ROOTS = (
    "plan_topology.classify_plan",
    "loop.select_ready_tasks",
    "build_c.assign_scopes",
    "loop.native_dispatch_intent",
    "tp.cmd_screen_dispatch",
    "loop.record_native_dispatch_observation",
    "loop.activate_contract",
    "checkpoint run/gate adapters",
    "repository worktree preparation and feature integration",
)

REQUIRED_FORBIDDEN_AUTHORITIES = (
    "_stage_loop_wave_dispatches",
    "StageLifecycle.split_stage",
    "StageLifecycle.resume_stage",
    "storage.claim_stage_execution_root_for_run",
    "stage_runtime_dispatch",
    "_stage_bindings",
    "scheduler",
    "capacity model",
    "reservation",
    "admission queue",
    "lease concurrency",
    "worker lifecycle",
    "replay queue",
    "execution DAG",
)

DELIVERY_ROOTS: Mapping[str, tuple[str, ...]] = {
    "taskplane/loop.py": ("wave",),
    "taskplane/build_c.py": ("assign_scopes",),
    "taskplane/tp.py": ("cmd_screen_dispatch",),
}

_REQUIRED_REACHABLE: Mapping[tuple[str, str], tuple[tuple[str, ...], ...]] = {
    ("taskplane/loop.py", "wave"): (
        ("select_ready_tasks",),
        ("_native_dispatch_intent", "native_dispatch_intent"),
    ),
    ("taskplane/build_c.py", "assign_scopes"): (("executable_topology",),),
}

_FORBIDDEN_CALL_IDENTITIES = frozenset({
    "_stage_loop_wave_dispatches",
    "split_stage",
    "resume_stage",
    "claim_stage_execution_root",
    "claim_stage_execution_root_for_run",
    "stage_runtime_dispatch",
    "scheduler",
    "schedule_workers",
    "reserve",
    "reservation",
    "admit",
    "admission",
    "replay",
    "lease_concurrency",
    "worker_lifecycle",
    "execution_dag",
})
_FORBIDDEN_STATE_KEYS = frozenset({
    "_stage_bindings", "stage_runtime_dispatch",
})
_SHA = re.compile(r"[0-9a-f]{40,64}\Z")
_IDENTIFIER = re.compile(r"[^a-z0-9]+")


class NativeAuthorityError(ValueError):
    """The R-0013 native authority boundary is incomplete or duplicated."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalized(value: object) -> str:
    return _IDENTIFIER.sub(" ", _text(value).lower()).strip()


def _contains_phrase(value: object, phrase: object) -> bool:
    normalized_value = _normalized(value)
    normalized_phrase = _normalized(phrase)
    if not normalized_value or not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {normalized_value} "


def _closed_forbidden_authority(value: object) -> str | None:
    candidates = {
        *(_normalized(row) for row in _CLOSED_AUTHORITY_CLASSES),
        *(
            _normalized(row)
            for rows in REQUIRED_FORBIDDEN_BY_CAPABILITY.values()
            for row in rows
        ),
    }
    matches = [
        phrase for phrase in candidates
        if phrase and _contains_phrase(value, phrase)
    ]
    if not matches:
        return None
    # Prefer the most specific closed phrase in diagnostics.
    return max(matches, key=lambda row: (len(row.split()), len(row), row))


def _closed_text_list(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise NativeAuthorityError(f"{label} must be a non-empty list")
    rows = [_text(row) for row in value]
    if any(not row for row in rows) or len(set(rows)) != len(rows):
        raise NativeAuthorityError(
            f"{label} must contain unique non-empty identities")
    return rows


def _validate_inventory(design: Mapping[str, object]) -> dict[str, Any]:
    inventory = design.get("native_capability_inventory")
    if not isinstance(inventory, Mapping):
        raise NativeAuthorityError(
            "Design native capability inventory is missing")
    if inventory.get("schema") != CAPABILITY_INVENTORY_SCHEMA:
        raise NativeAuthorityError(
            "Design native capability inventory schema is invalid")
    source_sha = _text(inventory.get("pinned_source_sha"))
    if not _SHA.fullmatch(source_sha) or \
            source_sha != APPROVED_PINNED_SOURCE_SHA:
        raise NativeAuthorityError(
            "Design native capability inventory differs from the approved "
            "exact source SHA")

    rows = inventory.get("rows")
    if not isinstance(rows, list):
        raise NativeAuthorityError(
            "Design native capability rows are missing")
    identities = [
        _text(row.get("capability")) if isinstance(row, Mapping) else ""
        for row in rows
    ]
    if tuple(identities) != REQUIRED_CAPABILITIES:
        raise NativeAuthorityError(
            "Design requires exactly the seven ordered Codex-native "
            "capability identities")

    for index, row in enumerate(rows):
        capability = identities[index]
        if not isinstance(row, Mapping):
            raise NativeAuthorityError(
                f"native capability {capability} is not an object")
        native_owner = _text(row.get("native_owner"))
        taskplane_role = _text(row.get("taskplane_role"))
        embedded_authority = _closed_forbidden_authority(native_owner)
        if embedded_authority is not None:
            raise NativeAuthorityError(
                f"native capability {capability} native owner embeds "
                f"forbidden Taskplane authority: {embedded_authority}")
        if native_owner != REQUIRED_NATIVE_OWNERS[capability]:
            raise NativeAuthorityError(
                f"native capability {capability} differs from the closed "
                "Codex native owner")
        if not taskplane_role:
            raise NativeAuthorityError(
                f"native capability {capability} has no bounded Taskplane role")
        duplicate = _closed_forbidden_authority(taskplane_role)
        if duplicate is not None:
            raise NativeAuthorityError(
                f"native capability {capability} gives Taskplane forbidden "
                f"duplicate authority: {duplicate}")
        if taskplane_role != REQUIRED_NATIVE_ROLES[capability]:
            raise NativeAuthorityError(
                f"native capability {capability} differs from the closed "
                "bounded Taskplane role")
        forbidden = _closed_text_list(
            row.get("forbidden_taskplane_authority"),
            label=f"native capability {capability} forbidden authority",
        )
        if tuple(forbidden) != REQUIRED_FORBIDDEN_BY_CAPABILITY[capability]:
            raise NativeAuthorityError(
                f"native capability {capability} forbidden authority differs "
                "from the closed canonical policy")
        sources = _closed_text_list(
            row.get("sources"),
            label=f"native capability {capability} evidence sources",
        )
        if tuple(sources) != REQUIRED_EVIDENCE_SOURCES[capability]:
            raise NativeAuthorityError(
                f"native capability {capability} evidence sources differ "
                "from the exact approved inventory")

    if not _text(inventory.get("completeness_rule")) or \
            not _text(inventory.get("host_gap_rule")):
        raise NativeAuthorityError(
            "native capability completeness and host-gap rules are required")
    host_gap_rule = _normalized(inventory.get("host_gap_rule"))
    if "human design blocker" not in host_gap_rule or \
            "never authorizes taskplane" not in host_gap_rule:
        raise NativeAuthorityError(
            "a host capability gap must block Design without granting "
            "Taskplane authority")
    return dict(inventory)


def _validate_boundary(design: Mapping[str, object]) -> dict[str, Any]:
    boundary = design.get("authority_boundary")
    if not isinstance(boundary, Mapping):
        raise NativeAuthorityError("Design native authority boundary is missing")
    if boundary.get("schema") != NATIVE_AUTHORITY_SCHEMA:
        raise NativeAuthorityError("Design native authority schema is invalid")
    allowed = _closed_text_list(
        boundary.get("allowed_taskplane_roots"),
        label="allowed Taskplane governance roots",
    )
    if tuple(allowed) != REQUIRED_ALLOWED_ROOTS:
        raise NativeAuthorityError(
            "allowed Taskplane governance roots differ from the closed "
            "Design responsibility map")
    forbidden = _closed_text_list(
        boundary.get("forbidden_from_native_dispatch_roots"),
        label="forbidden native-dispatch authority",
    )
    if tuple(forbidden) != REQUIRED_FORBIDDEN_AUTHORITIES:
        raise NativeAuthorityError(
            "forbidden native-dispatch authority differs from the closed "
            "Design boundary")
    for name in (
        "stage_journal_disposition", "static_rule", "behavioral_rule",
    ):
        if not _text(boundary.get(name)):
            raise NativeAuthorityError(
                f"native authority boundary is missing {name}")
    return dict(boundary)


def _ac1_design_criterion(design: Mapping[str, object]) -> str:
    acceptance = design.get("acceptance_map")
    if not isinstance(acceptance, list):
        raise NativeAuthorityError("Design acceptance map is missing")
    matches = [
        _text(row.get("criterion"))
        for row in acceptance if isinstance(row, Mapping)
        and _text(row.get("criterion")).startswith("AC1:")
    ]
    if len(matches) != 1:
        raise NativeAuthorityError(
            "Design must contain exactly one AC1 acceptance mapping")
    tests = next(
        row.get("tests") for row in acceptance
        if isinstance(row, Mapping) and
        _text(row.get("criterion")) == matches[0]
    )
    required_selector = (
        "taskplane/tests/test_r0013_native_authority.py::"
        "test_complete_native_capability_map_is_required_by_design_and_plan"
    )
    if not isinstance(tests, list) or required_selector not in tests:
        raise NativeAuthorityError(
            "Design AC1 mapping lacks the exact native inventory selector")
    return matches[0]


def _validate_plan(
        design: Mapping[str, object], plan: Mapping[str, object]) -> dict:
    if _text(plan.get("requirement")) != _text(design.get("requirement")):
        raise NativeAuthorityError(
            "Plan requirement does not match the Design authority")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise NativeAuthorityError("Plan tasks are missing")
    task_ids = [
        _text(row.get("id")) if isinstance(row, Mapping) else ""
        for row in tasks
    ]
    if any(not value for value in task_ids) or \
            len(set(task_ids)) != len(task_ids):
        raise NativeAuthorityError("Plan task identities are missing or duplicate")

    criterion = _ac1_design_criterion(design)
    owner_scope = {
        "taskplane/native_authority.py",
        "taskplane/tests/test_r0013_native_authority.py",
    }
    required_selector = (
        "taskplane/tests/test_r0013_native_authority.py::"
        "test_complete_native_capability_map_is_required_by_design_and_plan"
    )
    owners = [
        row for row in tasks if isinstance(row, Mapping)
        and set(row.get("scope") or []) == owner_scope
    ]
    if len(owners) != 1:
        raise NativeAuthorityError(
            "Plan must have one exclusive native-authority leaf owner")
    owner = owners[0]
    if list(owner.get("deps") or []) or owner.get("type") != "architecture":
        raise NativeAuthorityError(
            "native-authority owner must be a dependency-free architecture leaf")
    if list(owner.get("criteria") or []) != [criterion]:
        raise NativeAuthorityError(
            "native-authority owner does not preserve the exact AC1 criterion")
    if list(owner.get("contracts") or []) != [NATIVE_CAPABILITY_CONTRACT]:
        raise NativeAuthorityError(
            "native-authority owner does not exclusively bind the native "
            "capability contract")
    if _text(owner.get("tests")) != f"python3 -m pytest -q {required_selector}":
        raise NativeAuthorityError(
            "native-authority owner lacks the exact leaf selector")

    contract_rows = design.get("contracts")
    if not isinstance(contract_rows, list) or not any(
            isinstance(row, Mapping) and
            row.get("id") == NATIVE_CAPABILITY_CONTRACT and
            row.get("relation") == "provides"
            for row in contract_rows):
        raise NativeAuthorityError(
            "Design does not provide the native capability contract")
    contract_tasks = [
        _text(row.get("id")) for row in tasks if isinstance(row, Mapping)
        and NATIVE_CAPABILITY_CONTRACT in list(row.get("contracts") or [])
    ]
    if not contract_tasks:
        raise NativeAuthorityError(
            "Plan does not consume the native capability contract")

    edge_union = {
        _text(edge) for row in tasks if isinstance(row, Mapping)
        for edge in list(row.get("design_edges") or [])
    }
    required_edges = {
        "design->contract:design.codex-native-capability-inventory:provides",
        "contract:design.codex-native-capability-inventory->"
        "taskplane/native_authority.py:validated-by",
        "taskplane/native_authority.py->plan:blocks",
        "taskplane/native_authority.py->taskplane:consumed-by",
    }
    missing_edges = sorted(required_edges - edge_union)
    if missing_edges:
        raise NativeAuthorityError(
            "Plan is missing native-authority Design edges: " +
            ", ".join(missing_edges))
    if not any(
            "taskplane/native_authority.py" in list(
                row.get("new_modules") or [])
            for row in tasks if isinstance(row, Mapping)):
        raise NativeAuthorityError(
            "Plan module inventory omits taskplane/native_authority.py")
    return dict(owner)


def validate_design_and_plan(
        design: Mapping[str, object], plan: Mapping[str, object]) -> dict:
    """Validate and fingerprint the closed AC1 Design/Plan responsibility map.

    The function is deliberately pure: callers provide already-read canonical
    artifacts, and failure raises :class:`NativeAuthorityError` before Build.
    """
    if not isinstance(design, Mapping) or not isinstance(plan, Mapping):
        raise NativeAuthorityError("Design and Plan must be objects")
    inventory = _validate_inventory(design)
    boundary = _validate_boundary(design)
    owner = _validate_plan(design, plan)
    material = {
        "requirement": _text(design.get("requirement")),
        "contract": NATIVE_CAPABILITY_CONTRACT,
        "capabilities": list(REQUIRED_CAPABILITIES),
        "inventory": inventory,
        "authority_boundary": boundary,
        "plan_owner": owner,
    }
    return {
        "schema": LEAF_READINESS_SCHEMA,
        "status": "ready",
        "outcome": "AC1",
        "contract": NATIVE_CAPABILITY_CONTRACT,
        "capability_count": len(REQUIRED_CAPABILITIES),
        "fingerprint": _fingerprint(material),
    }


def _attribute_name(node: ast.AST) -> str:
    parts: list[str] = []
    cursor: ast.AST | None = node
    while isinstance(cursor, ast.Attribute):
        parts.append(cursor.attr)
        cursor = cursor.value
    if isinstance(cursor, ast.Name):
        parts.append(cursor.id)
    return ".".join(reversed(parts))


def _call_identity(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return _attribute_name(node.func)
    return "<dynamic>"


def _function_calls(node: ast.AST) -> tuple[str, ...]:
    return tuple(
        _call_identity(child) for child in ast.walk(node)
        if isinstance(child, ast.Call)
    )


def _forbidden_call(identity: str) -> bool:
    terminal = _normalized(identity.split(".")[-1]).replace(" ", "_")
    return terminal in _FORBIDDEN_CALL_IDENTITIES


def _source_text(
        source_root: str | Path, rel: str,
        overrides: Mapping[str, str]) -> str:
    if rel in overrides:
        value = overrides[rel]
        if not isinstance(value, str):
            raise NativeAuthorityError(
                f"delivery-root source override {rel} is not text")
        return value
    path = Path(source_root).resolve() / rel
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NativeAuthorityError(
            f"delivery-root source is unavailable: {rel}: {exc}") from exc


def _parse_delivery_file(
        source_root: str | Path, rel: str,
        overrides: Mapping[str, str]) -> tuple[
            dict[str, ast.FunctionDef | ast.AsyncFunctionDef], dict[str, tuple[str, ...]]]:
    text = _source_text(source_root, rel, overrides)
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        raise NativeAuthorityError(
            f"delivery-root source does not parse: {rel}: {exc}") from exc
    functions = {
        node.name: node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    calls = {name: _function_calls(node) for name, node in functions.items()}
    return functions, calls


def _forbidden_state_keys(node: ast.AST) -> set[str]:
    return {
        child.value for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
        and child.value in _FORBIDDEN_STATE_KEYS
    }


def _reachable_file_functions(
        rel: str, roots: Sequence[str],
        functions: Mapping[str, ast.AST],
        calls: Mapping[str, tuple[str, ...]]) -> tuple[set[str], set[str]]:
    missing = [name for name in roots if name not in functions]
    if missing:
        raise NativeAuthorityError(
            f"delivery root {rel} is missing: {', '.join(missing)}")
    reachable: set[str] = set()
    observed_calls: set[str] = set()
    queue = deque(roots)
    while queue:
        name = queue.popleft()
        if name in reachable:
            continue
        reachable.add(name)
        for identity in calls.get(name, ()):
            observed_calls.add(identity)
            terminal = identity.split(".")[-1]
            if terminal in functions and terminal not in reachable:
                queue.append(terminal)
    return reachable, observed_calls


def validate_delivery_roots(
        source_root: str | Path, *,
        roots: Mapping[str, Sequence[str]] = DELIVERY_ROOTS,
        source_overrides: Mapping[str, str] | None = None) -> dict:
    """Refuse forbidden authority reachable from native delivery roots.

    ``source_overrides`` exists for mutation proofs.  It is source text only;
    the validator never imports the candidate modules or performs worktree,
    state, dispatch, wait or host lifecycle operations.
    """
    overrides = source_overrides or {}
    if not isinstance(roots, Mapping) or not roots:
        raise NativeAuthorityError("native delivery roots are missing")
    parsed: dict[str, tuple[dict[str, ast.AST], dict[str, tuple[str, ...]]]] = {}
    root_rows: list[dict[str, object]] = []
    violations: list[str] = []
    for rel, names in roots.items():
        rel = _text(rel).replace("\\", "/")
        if rel not in DELIVERY_ROOTS or tuple(names) != DELIVERY_ROOTS[rel]:
            raise NativeAuthorityError(
                "native delivery roots differ from the closed authority map")
        functions, calls = _parse_delivery_file(
            source_root, rel, overrides)
        parsed[rel] = (functions, calls)
        reachable, observed_calls = _reachable_file_functions(
            rel, names, functions, calls)
        for function_name in sorted(reachable):
            for identity in calls.get(function_name, ()):
                if _forbidden_call(identity):
                    violations.append(
                        f"{rel}:{function_name}->{identity}")
            for key in sorted(_forbidden_state_keys(functions[function_name])):
                violations.append(f"{rel}:{function_name}->state[{key}]")
        for root_name in names:
            for alternatives in _REQUIRED_REACHABLE.get((rel, root_name), ()):
                if not any(
                        candidate in reachable or any(
                            call.split(".")[-1] == candidate
                            for call in observed_calls)
                        for candidate in alternatives):
                    violations.append(
                        f"{rel}:{root_name}->missing-required-native-edge["
                        + "|".join(alternatives) + "]")
        root_rows.append({
            "path": rel,
            "roots": list(names),
            "reachable": sorted(reachable),
            "calls": sorted(observed_calls),
        })
    if violations:
        raise NativeAuthorityError(
            "forbidden or severed native delivery authority edges: " +
            "; ".join(sorted(set(violations))))
    material = {"roots": root_rows, "forbidden": []}
    return {
        "schema": NATIVE_AUTHORITY_SCHEMA,
        "status": "ready",
        "roots": root_rows,
        "forbidden_edge_count": 0,
        "fingerprint": _fingerprint(material),
    }


PRODUCTION_DESIGN_GATE_SCHEMA = "taskplane.production-design-gate/v1"
RETAINED_R0013_AUTHORITY_REVISION = \
    "00cd4f2c8183e57b6eae3f0cb6b0c580e00fe085"
RETAINED_R0013_SOURCE_THREAD = \
    "01a03619-63a9-7743-90f9-1b6b1945b6ac"
RETAINED_R0013_DESIGN_TURN = \
    "01a0409b-8903-78f3-a096-3fbd794d6ab3"
RETAINED_R0013_AUDIT_SHA256 = \
    "467edb61e99beac0432fbda6fc0e5028c6703304ab72b86029ff58f843d60ef7"
_RETAINED_R0013_AUDIT_DEFAULT = (
    "/Users/vdemkiv/.codex/sessions/2026/08/24/"
    "rollout-2026-08-24T19-27-08-"
    "01a03619-63a9-7743-90f9-1b6b1945b6ac.jsonl"
)


def _repository_json(source_root: str | Path, relative: str) -> dict:
    root = Path(source_root).resolve()
    path = root / relative
    try:
        if path.is_symlink() or not path.is_file() or \
                root not in path.resolve().parents or \
                path.stat().st_size > 8_000_000:
            raise NativeAuthorityError(
                f"production Design artifact is not a confined regular file: "
                f"{relative}")
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except NativeAuthorityError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise NativeAuthorityError(
            f"production Design artifact is unavailable: {relative}") from exc
    if not isinstance(value, dict):
        raise NativeAuthorityError(
            f"production Design artifact must contain an object: {relative}")
    return value


def validate_production_design_gate(
        source_root: str | Path, *,
        sweep_evidence: Mapping[str, object] | None = None,
        authority_revision: str | None = None) -> dict:
    """Execute the authoritative R-0013 Design/Plan gate from repository bytes.

    This executable composition root is intentionally fail closed.  It binds
    the closed native responsibility map, scans the actual delivery roots and,
    when the approved Design declares its one broad sweep, requires the live
    retained-audit validator before returning a Build-ready receipt.
    """
    root = Path(source_root).resolve()
    if authority_revision is None:
        design = _repository_json(root, "design/contract.json")
        plan = _repository_json(root, "plan/tasks.json")
    else:
        try:
            if __package__:
                from .design_sweep import retained_repository_bytes
            else:  # pragma: no cover - legacy direct execution
                from design_sweep import retained_repository_bytes
            design = json.loads(retained_repository_bytes(
                root, "design/contract.json", maximum=8_000_000,
                revision=authority_revision).decode("utf-8"))
            plan = json.loads(retained_repository_bytes(
                root, "plan/tasks.json", maximum=8_000_000,
                revision=authority_revision).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise NativeAuthorityError(
                "retained production Design authority is invalid") from exc
        if not isinstance(design, dict) or not isinstance(plan, dict):
            raise NativeAuthorityError(
                "retained production Design authority must contain objects")
    authority = validate_design_and_plan(design, plan)
    roots = validate_delivery_roots(root)
    sweep = None
    sweep_declared = isinstance(design.get("design_sweep"), Mapping)
    if _text(design.get("requirement")) == "R-0013" and not sweep_declared:
        raise NativeAuthorityError(
            "production R-0013 Design gate requires the approved sweep")
    if sweep_declared:
        if not isinstance(sweep_evidence, Mapping):
            raise NativeAuthorityError(
                "production Design gate requires retained sweep evidence")
        try:
            if __package__:
                from .design_sweep import (
                    DesignSweepError, validate_retained_design_sweep,
                )
            else:  # pragma: no cover - legacy direct execution
                from design_sweep import (  # type: ignore[import-not-found]
                    DesignSweepError, validate_retained_design_sweep,
                )
            sweep_kwargs = {"evidence": sweep_evidence}
            if authority_revision is not None:
                sweep_kwargs["revision"] = authority_revision
            sweep = validate_retained_design_sweep(root, **sweep_kwargs)
        except DesignSweepError as exc:
            raise NativeAuthorityError(
                f"production Design sweep is invalid: {exc}") from exc
    material = {"authority": authority, "delivery_roots": roots,
                "design_sweep": sweep,
                "authority_revision": authority_revision}
    return {
        "schema": PRODUCTION_DESIGN_GATE_SCHEMA,
        "status": "ready",
        **material,
        "fingerprint": _fingerprint(material),
    }


def retained_r0013_sweep_evidence(
        audit_path: str | Path | None = None) -> dict[str, str]:
    """Return the closed locator/identity for the already-approved sweep."""
    configured = audit_path or os.environ.get("TASKPLANE_R0013_CODEX_AUDIT") \
        or _RETAINED_R0013_AUDIT_DEFAULT
    return {
        "codex_audit_path": str(configured),
        "source_thread_id": RETAINED_R0013_SOURCE_THREAD,
        "design_turn_id": RETAINED_R0013_DESIGN_TURN,
        "expected_source_log_sha256": RETAINED_R0013_AUDIT_SHA256,
    }


def validate_retained_r0013_authority(
        source_root: str | Path, *, audit_path: str | Path | None = None) -> dict:
    """Validate immutable R-0013 Design authority against current live roots.

    R-0002 replaced the working Design/Plan files after R-0013 completed.  A
    current-file conditional would therefore make the earlier production
    authority disappear.  This composition instead resolves the exact
    approved ancestor blobs and the exact retained native audit, while still
    scanning the current checkout's delivery roots for forbidden authority.
    """
    return validate_production_design_gate(
        source_root,
        sweep_evidence=retained_r0013_sweep_evidence(audit_path),
        authority_revision=RETAINED_R0013_AUTHORITY_REVISION)


def retained_r0013_authority_applies(source_root: str | Path) -> bool:
    """Detect the immutable R-0013 lineage without consulting current JSON."""
    try:
        if __package__:
            from .design_sweep import repository_contains_revision
        else:  # pragma: no cover - legacy direct execution
            from design_sweep import repository_contains_revision
        return repository_contains_revision(
            source_root, RETAINED_R0013_AUTHORITY_REVISION)
    except Exception as exc:
        raise NativeAuthorityError(
            "retained R-0013 provenance could not be inspected") from exc


def main(argv: Sequence[str] | None = None) -> int:
    """Run the installed production Design gate and emit its sealed receipt."""
    parser = argparse.ArgumentParser(
        prog="python -m taskplane.native_authority")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--sweep-evidence")
    parser.add_argument("--retained-r0013", action="store_true")
    parser.add_argument("--audit-path")
    args = parser.parse_args(argv)
    if args.retained_r0013:
        receipt = validate_retained_r0013_authority(
            args.source_root, audit_path=args.audit_path)
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    sweep_evidence = None
    if args.sweep_evidence:
        sweep_evidence = _repository_json(
            Path(args.sweep_evidence).resolve().parent,
            Path(args.sweep_evidence).name)
    receipt = validate_production_design_gate(
        args.source_root, sweep_evidence=sweep_evidence)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


__all__ = [
    "CAPABILITY_INVENTORY_SCHEMA",
    "DELIVERY_ROOTS",
    "LEAF_READINESS_SCHEMA",
    "NATIVE_AUTHORITY_SCHEMA",
    "NATIVE_CAPABILITY_CONTRACT",
    "PRODUCTION_DESIGN_GATE_SCHEMA",
    "RETAINED_R0013_AUTHORITY_REVISION",
    "NativeAuthorityError",
    "REQUIRED_CAPABILITIES",
    "validate_delivery_roots",
    "validate_design_and_plan",
    "validate_production_design_gate",
    "retained_r0013_authority_applies",
    "validate_retained_r0013_authority",
]


if __name__ == "__main__":  # pragma: no cover - exercised as an installed CLI
    raise SystemExit(main())
