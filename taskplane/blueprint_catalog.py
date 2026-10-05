"""Explicit Workflow Builder capabilities and the identity of this loaded runtime.

This registry describes available work. It never observes worker capacity, grants
authority, imports a user-selected module, or initializes a workflow store.
"""
from __future__ import annotations

from copy import deepcopy
import importlib
import inspect
from pathlib import Path
import platform
import sys
from types import CodeType, ModuleType
from typing import Any

from . import workflow as w, workflow_evidence as evidence
from .primitives import content_fingerprint

CATALOG_SCHEMA = "taskplane.workflow-capabilities/v1"
IDENTITY_SCHEMA = "taskplane.workflow-runtime/v1"
BASE_CONTRACTS = (
    "native-default/v1", "bounded/v2", "taskplane.task-definitions/v1",
    "taskplane.phase-output/v1",
)
DELIVERY_CONTRACTS = (
    "taskplane.verification-strategy/v1", "taskplane.verification-history/v1",
)
KNOWN_CONTRACTS = BASE_CONTRACTS + DELIVERY_CONTRACTS
_PHASES = ("product", "design", "plan", "build", "evaluate", "engineering", "retro")
INPUT_TYPES = (
    "text", "git_ref", "workspace_file_list", "workspace_output_file_list",
    "workspace_relative_directory", "acceptance_criteria",
)
ARTIFACT_KINDS = (
    "report", "phase-output", "supporting", "verification", "requirements", "design", "plan",
)
LENSES = (
    "accessibility", "architecture", "backend", "code-quality", "cost-finops", "data-safety",
    "dba", "design", "devops", "frontend", "i18n", "integrability", "mobile",
    "privacy-compliance", "product", "project-management", "qa", "scalability", "security",
    "services-selection", "solution-design", "sre", "tech-writer", "testability",
    "time-to-market", "tradeoffs",
)
# These are fixed transitive prompt assets, not paths discovered in user prose.
COMMON_ASSETS = (
    "skills/tp-go/references/shared-flow.md", "skills/tp-go/references/codex-native-dispatch.md",
    "docs/cli-reference.md",
)
LENS_REFERENCES: dict[str, tuple[str, ...]] = {
    "security": ("security-methodology.md", "prompt-injection-defense.md"),
    "code-quality": ("python-code-quality.md", "typescript-code-quality.md",
                     "go-code-quality.md", "SOURCES.md"),
    "data-safety": ("migration-scripts.md",),
    "dba": ("database-selection.md", "migration-scripts.md"),
    "design": ("ui-audit.md",),
}
_ROLES = (
    ("taskplane.product.analysis", ("product",), "tp-product", "artifact-write"),
    ("taskplane.design.analysis", ("design",), "tp-designer", "artifact-write"),
    ("taskplane.plan.decomposition", ("plan",), "tp-planner", "artifact-write"),
    ("taskplane.build.execution", ("build",), "tp-executor", "scoped-source-write"),
    ("taskplane.evaluate.verification", ("evaluate",), "tp-evaluator", "artifact-write"),
    ("taskplane.review.lens", ("engineering",), "tp-lens", "artifact-write"),
    ("taskplane.phase.synthesis", w.PHASES, "tp-orchestrator", "artifact-write"),
)
_CAPABILITIES: dict[str, dict[str, Any]] = {
    name: {
        "id": name, "version": "1.0.0", "phases": list(phases),
        "input_types": [v for v in INPUT_TYPES if v != "workspace_relative_directory"],
        "output_types": [*ARTIFACT_KINDS, *(["source"] if effect == "scoped-source-write" else [])],
        "effect_class": effect, "execution": "root" if role == "tp-orchestrator" else "native_required",
        "role_path": "agents/" + role + ".md", "lenses": list(LENSES) if role == "tp-lens" else [],
        "prompt_assets": list(COMMON_ASSETS),
    }
    for name, phases, role, effect in _ROLES
}
RUNTIME_MODULES = (
    "workflow", "workflow_evidence", "workflow_approval", "workflow_host", "workflow_local",
    "worker_runtime", "context", "context_handoff", "context_delivery", "context_reuse",
    "context_views", "host_capabilities", "host_native", "workspace_binding", "flow",
    "primitives", "runtime_command", "command_runtime",
    "claude_worker_observations", "flow_dashboard",
)
COMPILER_MODULES = ("blueprint", "blueprint_catalog", "blueprint_compile")


def capability_contract(capability_id: str) -> dict[str, Any] | None:
    """Return detached static metadata; unavailable IDs are never registered."""
    row = _CAPABILITIES.get(capability_id)
    return deepcopy(row) if row is not None else None


def required_contracts(route: dict[str, Any] | None = None) -> list[str]:
    return sorted([*BASE_CONTRACTS, *(DELIVERY_CONTRACTS if route and route.get("kind") == "delivery" else ())])


def _diagnostic(code: str, location: str, message: str) -> dict[str, str]:
    return {"code": code, "location": location, "message": message,
            "remedy": "Use a compatible loaded Taskplane package and revalidate the definition."}


def _shape(function: Any, parameters: tuple[str, ...]) -> bool:
    try:
        return callable(function) and set(parameters) <= set(inspect.signature(function).parameters)
    except (ValueError, TypeError):
        return False


def _contracts(modules: dict[str, ModuleType]) -> dict[str, bool]:
    worker = modules.get("worker_runtime")
    context = modules.get("context_handoff")
    ev = modules.get("workflow_evidence")
    workflow = modules.get("workflow")
    output_fields = getattr(workflow, "OUTPUT_FIELDS", None)
    native = (getattr(workflow, "PHASES", None) == _PHASES
              and _shape(getattr(workflow, "validate_scope", None), ("scope",))
              and all(_shape(getattr(worker, name, None), args) for name, args in (
                  ("prepare", ("workspace", "state", "task_id", "request")),
                  ("accept_result", ("workspace", "state", "task_id", "request")),
                  ("bind_worker", ("state", "row", "identity")),
                  ("parent_readiness", ("state",)), ("readiness", ("row",)),
              )))
    # Exercise pure validation too: mere names or a version string are insufficient.
    if native:
        scope = {"criteria": ["C1"], "paths": {p: [] for p in _PHASES},
                 "execution_contract": "native-default/v1"}
        try:
            assert workflow is not None
            workflow.validate_scope(scope)
        except (AttributeError, TypeError, ValueError):
            native = False
        if native:
            try:
                assert workflow is not None
                workflow.validate_scope({**scope, "execution_contract": "unknown/v1"})
                native = False
            except w.Refusal as exc:
                native = exc.reason == "invalid_evidence"
            except (AttributeError, TypeError, ValueError):
                native = False
    return {
        "native-default/v1": native,
        "bounded/v2": (getattr(context, "SEMANTIC_CONTRACT", None) == "bounded/v2"
                       and _shape(getattr(context, "consume_required", None), ("session",))
                       and _shape(getattr(getattr(context, "Session", None), "__init__", None),
                                  ("workspace", "state"))),
        "taskplane.task-definitions/v1": (
            getattr(ev, "TASK_DEFINITIONS", None) == "taskplane.task-definitions/v1"
            and _shape(getattr(ev, "freeze_tasks", None), ("root", "state", "data"))
            and _shape(getattr(ev, "execution_fields", None), ("rows", "required"))),
        "taskplane.phase-output/v1": (
            isinstance(output_fields, dict) and set(output_fields) == set(_PHASES)
            and isinstance(output_fields["plan"], (list, tuple))
            and isinstance(output_fields["build"], (list, tuple))
            and {"task_dag", "verification_strategy"} <= set(output_fields["plan"])
            and {"build_checks", "known_gaps"} <= set(output_fields["build"])
            and _shape(getattr(ev, "prevalidate", None), ("root", "state", "output_path", "tasks_path"))
            and _shape(getattr(ev, "seal", None), ("root", "state", "output_path", "tasks_path"))),
        "taskplane.verification-strategy/v1": (
            getattr(ev, "VERIFICATION_STRATEGY", None) == "taskplane.verification-strategy/v1"
            and _shape(getattr(ev, "plan_preflight", None), ("root", "state", "output"))
            and _shape(getattr(ev, "typed_plan", None), ("output",))),
        "taskplane.verification-history/v1": (
            getattr(ev, "VERIFICATION_HISTORY", None) == "taskplane.verification-history/v1"
            and _shape(getattr(ev, "validate_verification", None), ("root", "state", "output"))),
    }


def _constant_identity(value: Any) -> Any:
    if isinstance(value, CodeType):
        return _code_identity(value)
    if isinstance(value, (tuple, frozenset)):
        values = [_constant_identity(item) for item in value]
        return {type(value).__name__: sorted(values, key=content_fingerprint)
                if isinstance(value, frozenset) else values}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    return {type(value).__name__: repr(value)}


def _code_identity(code: CodeType) -> dict[str, Any]:
    # marshal encodes reference/interning flags that can change while a function
    # executes. Pin stable public bytecode fields instead of that process state.
    return {"code": code.co_code.hex(), "constants": [_constant_identity(v) for v in code.co_consts],
            "names": code.co_names, "varnames": code.co_varnames, "freevars": code.co_freevars,
            "cellvars": code.co_cellvars, "flags": code.co_flags, "argcount": code.co_argcount,
            "posonlyargcount": code.co_posonlyargcount, "kwonlyargcount": code.co_kwonlyargcount,
            "exceptiontable": getattr(code, "co_exceptiontable", b"").hex()}


def _code_digest(module: ModuleType) -> str:
    """Pin executed Python bodies as well as their current source-file bytes."""
    bodies: dict[str, str] = {}
    for name, value in sorted(vars(module).items()):
        if getattr(value, "__module__", None) != module.__name__:
            continue
        if inspect.isfunction(value):
            bodies[name] = content_fingerprint(_code_identity(value.__code__))
        elif inspect.isclass(value):
            for member, method in sorted(vars(value).items()):
                if isinstance(method, (staticmethod, classmethod)):
                    method = method.__func__
                if inspect.isfunction(method):
                    bodies[name + "." + member] = content_fingerprint(_code_identity(method.__code__))
    return content_fingerprint(bodies)


def runtime_identity(requirements: list[str] | None = None, *,
                     route: dict[str, Any] | None = None) -> dict[str, Any]:
    """Inspect only this package's loaded modules, without loading another runtime.

    Importing a sibling here is read-only. Missing compiler/harness modules and
    incoherent module roots are named blockers, never a compatibility fallback.
    """
    root = Path(__file__).resolve().parents[1]
    modules: dict[str, ModuleType] = {}
    members: dict[str, Any] = {}
    blockers: list[dict[str, str]] = []
    for name in sorted((*RUNTIME_MODULES, *COMPILER_MODULES)):
        relative = "taskplane/" + name + ".py"
        try:
            module = importlib.import_module("." + name, __package__)
            loaded = Path(module.__file__ or "").resolve()
            expected = evidence.path(root, relative)
            if loaded != expected or not expected.is_file():
                raise ValueError("Loaded module does not belong to this runtime root")
            modules[name] = module
            members[relative] = {"path": str(loaded), "sha256": content_fingerprint(evidence.read(root, relative)),
                                 "loaded_code_sha256": _code_digest(module)}
        except (ImportError, OSError, ValueError) as exc:
            blockers.append(_diagnostic("runtime_module_unavailable", "/runtime/" + name, str(exc)))
    contracts = _contracts(modules)
    native_members: dict[str, str | None] = {}
    native_module = modules.get("host_capabilities")
    try:
        if native_module is None:
            raise ValueError("Native runtime identity is unavailable")
        native = native_module.runtime_identity()
        if native["root"] != str(root):
            raise ValueError("Native runtime identity has a different root")
        for relative in sorted(native["member_sha256"]):
            try:
                # Supplement, do not replace, the native admission-path identity.
                native_members[relative] = content_fingerprint(evidence.read(root, relative))
                if native_members[relative] != native["member_sha256"][relative]:
                    raise ValueError("Native runtime member changed while reading identity")
            except (OSError, ValueError) as exc:
                native_members[relative] = None
                blockers.append(_diagnostic("runtime_asset_unavailable", "/runtime/assets/" + relative, str(exc)))
    except (AttributeError, KeyError, TypeError, ValueError, OSError) as exc:
        blockers.append(_diagnostic("native_identity_unavailable", "/runtime/native", str(exc)))
    requested = sorted(set(required_contracts(route)) | set(requirements or []))
    for contract in requested:
        if not contracts.get(contract, False):
            blockers.append(_diagnostic("unsupported_contract", "/runtime_requirements", contract))
    executable = Path(sys.executable).resolve()
    try:
        interpreter_hash = content_fingerprint(executable.read_bytes())
    except OSError as exc:
        interpreter_hash = None
        blockers.append(_diagnostic("interpreter_unavailable", "/runtime/interpreter", str(exc)))
    body: dict[str, Any] = {
        "schema": IDENTITY_SCHEMA, "runtime_root": str(root),
        "interpreter": {"path": str(executable), "sha256": interpreter_hash,
                        "implementation": platform.python_implementation(), "version": platform.python_version()},
        "contracts": sorted(k for k, supported in contracts.items() if supported),
        "required_contracts": requested, "modules": members, "native_member_sha256": native_members,
        "compatible": not blockers, "blockers": blockers,
        "basis": "Loaded module paths, Python bodies and file bytes; observation, not host attestation.",
    }
    body["digest"] = content_fingerprint(body)
    return body


def catalog(tasks: list[dict[str, Any]] | None = None, *,
            requirements: list[str] | None = None,
            route: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return deterministic selected capability/assets and loaded compatibility.

    With no task selection, report every registered capability and lens. A task
    selection pins only the capabilities/lenses that the definition can invoke.
    """
    root = Path(__file__).resolve().parents[1]
    selected = {(t["capability"], t.get("lens")) for t in tasks} if tasks is not None else {
        (name, lens) for name, row in _CAPABILITIES.items() for lens in (row["lenses"] or [None])
    }
    rows: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    for capability_id, lens in sorted(selected, key=lambda pair: (pair[0], pair[1] or "")):
        row = capability_contract(capability_id)
        if row is None or (lens is not None and lens not in row["lenses"]) or (row["lenses"] and lens is None):
            blockers.append(_diagnostic("unsupported_capability", "/tasks", str((capability_id, lens))))
            continue
        assets = [row["role_path"], *row["prompt_assets"]]
        if lens:
            row["lens"] = lens
            row["lens_path"] = "lenses/" + lens + ".md"
            assets += [row["lens_path"], *["lenses/references/" + p for p in LENS_REFERENCES.get(lens, ())]]
        hashes: dict[str, str | None] = {}
        for relative in sorted(set(assets)):
            try:
                hashes[relative] = content_fingerprint(evidence.read(root, relative))
            except (OSError, ValueError) as exc:
                hashes[relative] = None
                blockers.append(_diagnostic("capability_asset_unavailable", "/assets/" + relative, str(exc)))
        row["assets"] = hashes
        rows.append(row)
    runtime = runtime_identity(requirements, route=route)
    body: dict[str, Any] = {
        "schema": CATALOG_SCHEMA, "capabilities": rows, "runtime": runtime,
        "capability_digest": content_fingerprint(rows),
        "compiler_digest": content_fingerprint({k: v for k, v in runtime["modules"].items()
                                                if Path(k).stem in COMPILER_MODULES}),
        "compatible": not blockers and runtime["compatible"],
        "blockers": [*blockers, *runtime["blockers"]],
    }
    body["digest"] = content_fingerprint(body)
    return body
