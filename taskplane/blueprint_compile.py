"""Read-only workflow binding and deterministic, no-clobber bootstrap packages.

Nothing here creates a run, observes approval, dispatches work, or executes a
workflow command. Callers must obtain mutable Build authority from the harness.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Any, Iterable, NoReturn

from . import blueprint as b, blueprint_catalog as catalog
from . import workflow as w, workflow_evidence as evidence
from .primitives import canonical_bytes, content_fingerprint, git_environment

BINDINGS_SCHEMA = "taskplane.workflow-bindings/v1"
COMPILATION_SCHEMA = "taskplane.workflow-compilation/v1"
BINDING_SCHEMA = "taskplane.workflow-binding/v1"
PREVIEW_SCHEMA = "taskplane.workflow-preview/v1"
TASKS_SCHEMA = "taskplane.tasks/v1"
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
_PACKAGE = re.compile(r"\.taskplane/bootstrap/workflow-[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}\Z")


def _fail(code: str, location: str, message: str) -> NoReturn:
    raise b.BlueprintError(code, location, message,
                           "Correct the binding or restore pinned inputs, then compile a fresh invocation.")


def _path(root: Path, relative: str) -> Path:
    try:
        target = evidence.path(root, relative)
        for parent in target.parents:
            if parent == root:
                break
            if parent.exists() and not parent.is_dir():
                _fail("invalid_path", "/paths/" + relative, "A parent path is not a directory.")
        return target
    except (OSError, w.Refusal) as exc:
        _fail("invalid_path", "/paths/" + relative, str(exc))


def _read(root: Path, relative: str) -> bytes:
    target = _path(root, relative)
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                _fail("invalid_file", "/paths/" + relative, "A regular file is required.")
            raw = stream.read(MAX_MEMBER_BYTES + 1)
        if len(raw) > MAX_MEMBER_BYTES:
            _fail("input_too_large", "/paths/" + relative, "File exceeds the 16 MiB limit.")
        return raw
    except OSError as exc:
        _fail("input_unavailable", "/paths/" + relative, str(exc))


def _git(root: Path, arguments: list[str], *, optional: bool = False) -> bytes | None:
    executable = shutil.which("git")
    if executable is None:
        _fail("git_unavailable", "/inputs", "Git is required to resolve git_ref inputs.")
    try:
        result = subprocess.run([executable, "--no-optional-locks", "--no-replace-objects", "-C", str(root),
                                 *arguments], env=git_environment(Path(executable)),
                                capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail("git_unavailable", "/inputs", str(exc))
    if result.returncode:
        if optional:
            return None
        _fail("invalid_git_ref", "/inputs", result.stderr.decode("utf-8", "replace").strip())
    if len(result.stdout) > MAX_MEMBER_BYTES:
        _fail("input_too_large", "/inputs", "Git result exceeds the 16 MiB limit.")
    return result.stdout


def _commit(root: Path, ref: str) -> str:
    raw = _git(root, ["rev-parse", "--verify", "--end-of-options", ref + "^{commit}"])
    assert raw is not None
    value = raw.decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        _fail("invalid_git_ref", "/inputs", "Git did not return a full commit identity.")
    return value


def _blob(root: Path, commit: str, relative: str) -> bytes | None:
    # ls-tree prevents a symlink or submodule from being mistaken for file bytes.
    row = _git(root, ["ls-tree", "-z", commit, "--", relative])
    if not row:
        return None
    entries = row.rstrip(b"\0").split(b"\0")
    if len(entries) != 1:
        _fail("invalid_git_file", "/inputs/" + relative, "Expected one exact Git file.")
    metadata, name = entries[0].split(b"\t", 1)
    mode, kind, object_id = metadata.split(b" ")
    if name.decode("utf-8") != relative or mode not in (b"100644", b"100755") or kind != b"blob":
        _fail("invalid_git_file", "/inputs/" + relative, "Git input must be a regular file, not a link or directory.")
    return _git(root, ["cat-file", "blob", object_id.decode("ascii")])


def _referenced_inputs(definition: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for task in definition["tasks"]:
        names.add(task["criteria_binding"]["input"])
        for read in task["read_bindings"]:
            if "input" in read:
                names.add(read["input"])
        for output in task["outputs"]:
            names.add(output.get("binding", output.get("directory"))["input"])
    return names


def bind_inputs(workspace: str | os.PathLike[str], definition: dict[str, Any],
                inputs: dict[str, Any] | None = None, *, incomplete: bool = False) -> dict[str, Any]:
    """Resolve literal values and exact source inventory without writing anything.

    ``base_ref`` and ``head_ref`` are conventional declared git_ref parameters.
    A missing read is allowed only when it is deleted in head_ref and exists as a
    regular base_ref blob; its content is preserved in the compilation package.
    All workspace_file_list inputs (including dependency/test lists) are pinned.
    """
    data = b.validate_definition(definition)
    root = Path(workspace).resolve()
    if not root.is_dir():
        _fail("workspace_unavailable", "/workspace", "Selected workspace is not a directory.")
    supplied = {} if inputs is None else inputs
    if not isinstance(supplied, dict) or any(not isinstance(key, str) for key in supplied):
        _fail("invalid_inputs", "/inputs", "Inputs must be an object with declared names.")
    unknown = set(supplied) - set(data["inputs"])
    if unknown:
        _fail("unknown_input", "/inputs/" + sorted(unknown)[0], "Input is not declared.")
    values: dict[str, Any] = {}
    unresolved: list[str] = []
    referenced = _referenced_inputs(data)
    for name, spec in sorted(data["inputs"].items()):
        if name in supplied or "default" in spec:
            values[name] = b.validate_input_value(spec["type"], supplied.get(name, spec.get("default")),
                                                   "/inputs/" + name)
        elif spec["required"] or name in referenced:
            unresolved.append(name)
    if unresolved and not incomplete:
        _fail("missing_input", "/inputs/" + unresolved[0], "Unresolved inputs: " + ", ".join(unresolved))
    refs: dict[str, str] = {}
    for name, value in values.items():
        if data["inputs"][name]["type"] == "git_ref":
            refs[name] = _commit(root, value)
    checkout: str | None = None
    if refs:
        checkout = _commit(root, "HEAD")
        if "head_ref" in refs and refs["head_ref"] != checkout:
            _fail("checkout_mismatch", "/inputs/head_ref", "head_ref must identify the selected checkout HEAD.")
    manifest: dict[str, Any] = {}
    for name, value in values.items():
        kind = data["inputs"][name]["type"]
        if kind == "workspace_relative_directory":
            target = _path(root, value)
            if target.exists() and not target.is_dir():
                _fail("invalid_directory", "/inputs/" + name, "Output prefix is not a directory.")
        if kind not in {"workspace_file_list", "workspace_output_file_list"}:
            continue
        for relative in value:
            target = _path(root, relative)
            if kind == "workspace_output_file_list":
                if target.exists() and not target.is_file():
                    _fail("invalid_file", "/inputs/" + name, "A source output must be a regular file or absent.")
                continue
            if relative in manifest:
                manifest[relative]["inputs"].append(name)
                continue
            head_blob = _blob(root, refs["head_ref"], relative) if "head_ref" in refs else None
            if target.exists():
                raw = _read(root, relative)
                if "head_ref" in refs and head_blob != raw:
                    _fail("source_checkout_mismatch", "/inputs/" + name,
                          "Working bytes differ from head_ref: " + relative)
                row = {"kind": "working", "sha256": content_fingerprint(raw), "bytes": len(raw)}
            else:
                base_blob = _blob(root, refs["base_ref"], relative) if "base_ref" in refs else None
                if "head_ref" not in refs or head_blob is not None or base_blob is None:
                    _fail("missing_read", "/inputs/" + name, "Read file has no immutable deleted-file source: " + relative)
                row = {"kind": "deleted", "sha256": content_fingerprint(base_blob), "bytes": len(base_blob),
                       "base_commit": refs["base_ref"]}
            manifest[relative] = {**row, "inputs": [name]}
    return {"schema": BINDINGS_SCHEMA, "workspace": str(root), "values": values,
            "git_refs": refs, "checkout_head": checkout, "source_manifest": manifest,
            "unresolved_inputs": unresolved, "status": "incomplete" if unresolved else "complete"}


def package_path(workspace: str | os.PathLike[str], out: str | os.PathLike[str] | None,
                 bindings: dict[str, Any]) -> str:
    """Normalize only the finite bootstrap namespace, never a general write grant."""
    root = Path(workspace).resolve()
    if out is None:
        relative = ".taskplane/bootstrap/workflow-" + content_fingerprint(bindings)[0:20]
    else:
        candidate = Path(out)
        if candidate.as_posix() != str(out):
            _fail("invalid_package_path", "/out", "Use a normalized literal bootstrap path.")
        if candidate.is_absolute():
            try:
                candidate = candidate.relative_to(root)
            except ValueError:
                _fail("invalid_package_path", "/out", "Package destination must be in the selected workspace.")
        relative = candidate.as_posix()
        if str(out).endswith("/"):
            _fail("invalid_package_path", "/out", "Use an exact bootstrap directory without a trailing slash.")
    if not _PACKAGE.fullmatch(relative) or (out is not None and ".." in Path(out).parts):
        _fail("invalid_package_path", "/out", "Use .taskplane/bootstrap/workflow-<invocation>.")
    _path(root, relative)
    return relative


def _prefix(data: dict[str, Any], bindings: dict[str, Any]) -> str:
    values = bindings["values"]
    directories = [key for key, spec in data["inputs"].items()
                   if spec["type"] == "workspace_relative_directory" and key in values]
    key = "output_prefix" if "output_prefix" in directories else (sorted(directories)[0] if directories else None)
    if key is None:
        _fail("missing_output_prefix", "/inputs", "Declare and bind a workspace_relative_directory for phase evidence.")
    return str(values[key])


def _collision(paths: Iterable[str]) -> None:
    seen: set[str] = set()
    for relative in paths:
        if any(relative == old or relative.startswith(old + "/") or old.startswith(relative + "/") for old in seen):
            _fail("output_collision", "/outputs", "Overlapping output files: " + relative)
        seen.add(relative)


def _assemble(root: Path, data: dict[str, Any], bindings: dict[str, Any],
              capabilities: dict[str, Any], destination: str) -> dict[str, Any]:
    """Build semantic artifacts. All input data is already strictly validated."""
    phases = list(w.PHASES) if data["route"]["kind"] == "delivery" else [data["route"]["phase"]]
    prefix = _prefix(data, bindings)
    values = bindings["values"]
    criteria: dict[str, str] = {}
    for name, spec in sorted(data["inputs"].items()):
        if spec["type"] == "acceptance_criteria" and name in values:
            for criterion in values[name]:
                if criterion["id"] in criteria:
                    _fail("duplicate_criterion", "/inputs/" + name, "Criterion IDs must be unique across inputs.")
                criteria[criterion["id"]] = criterion["statement"]
    outputs: dict[str, list[str]] = {}
    owners: dict[str, str] = {}
    source_outputs: set[str] = set()
    for task in data["tasks"]:
        for output in task["outputs"]:
            if "binding" in output:
                paths = values[output["binding"]["input"]]
                source_outputs.update(paths)
            else:
                paths = [values[output["directory"]["input"]] + "/" + output["relative_path"]]
            outputs[task["id"] + "/" + output["name"]] = paths
            for relative in paths:
                if relative in owners:
                    _fail("output_collision", "/outputs", "Multiple tasks own " + relative)
                owners[relative] = task["id"]
    if data["route"]["kind"] == "delivery" and not source_outputs:
        _fail("missing_build_scope", "/inputs", "Delivery requires a nonempty exact outer Build output list.")
    phase_files = {phase: {name: f"{prefix}/{phase}/{filename}" for name, filename in (
        ("packet", "packet.json"), ("tasks", "tasks.json"), ("report", "report.md"),
        ("evidence", "evidence.json"))} for phase in phases}
    generated_paths = [path for files in phase_files.values() for path in files.values()]
    _collision([*owners, *generated_paths])
    snapshots = {relative: destination + "/sources/" + row["sha256"] + ".blob"
                 for relative, row in bindings["source_manifest"].items() if row["kind"] == "deleted"}
    rows: list[dict[str, Any]] = []
    for task in data["tasks"]:
        reads: list[str] = []
        for reference in task["read_bindings"]:
            if "input" in reference:
                name = reference["input"]
                if data["inputs"][name]["type"] == "workspace_file_list":
                    reads.extend(snapshots.get(p, p) for p in values[name])
            else:
                artifact = reference["artifact"]
                reads.extend(outputs[artifact["task"] + "/" + artifact["name"]])
        row = {"id": task["id"], "phase": task["phase"],
               "owner": "root" if task["execution"] == "root" else "workflow-" + task["id"],
               "execution": task["execution"], "dependencies": task["depends_on"],
               "paths": sorted(p for output in task["outputs"] for p in outputs[task["id"] + "/" + output["name"]]),
               "read_inputs": sorted(set(reads)), "purpose": task["purpose"],
               "criteria": [c["id"] for c in values[task["criteria_binding"]["input"]]],
               "verification": task["verification"], "capability": task["capability"]}
        for field in ("instructions", "execution_reason", "execution_reference"):
            if field in task:
                row[field] = task[field]
        if "lens" in task:
            row["review_lens"] = task["lens"]
        rows.append(row)
    if not any(row["phase"] == phases[0] for row in rows):
        _fail("missing_phase_tasks", "/tasks", "The entry phase needs a concrete task pattern.")
    for phase in phases:
        key = "workflow-" + phase + "-checkpoint"
        if key in {row["id"] for row in rows}:
            _fail("reserved_task_id", "/tasks", "Compiler checkpoint task ID is reserved: " + key)
        current_rows = [row for row in rows if row["phase"] == phase]
        rows.append({"id": key, "phase": phase, "owner": "root", "execution": "root",
                     "execution_reason": "Root prepares the existing harness checkpoint from accepted task evidence.",
                     "execution_reference": "Workflow compilation phase evidence allocation.",
                     "dependencies": sorted(row["id"] for row in current_rows),
                     "paths": sorted(phase_files[phase].values()),
                     "read_inputs": sorted({p for row in current_rows for p in row["paths"]}),
                     "purpose": "Prepare " + phase + " evidence and request its existing checkpoint decision.",
                     "criteria": list(criteria), "verification": "Validate actual phase evidence through the existing harness; obtain a fresh checkpoint decision."})
    scope: dict[str, Any] = {"goal": data["name"] + ": " + data["description"],
                             "criteria": list(criteria), "paths": {phase: [] for phase in w.PHASES},
                             "verification_inputs": sorted(set(snapshots.get(p, p) for p in bindings["source_manifest"])
                                 | set(owners) | set(generated_paths)),
                             "execution_contract": "native-default/v1"}
    for row in rows:
        scope["paths"][row["phase"]].extend(row["paths"])
    scope["paths"] = {phase: sorted(paths) for phase, paths in scope["paths"].items()}
    publications: dict[str, Any] = {}
    try:
        evidence.valid_scope(root, scope)
        evidence.task_dag({"tasks": rows}, list(criteria))
        evidence.execution_fields(rows, required=True)
        for row in rows:
            evidence.validate_read_inputs(root, scope, row)
        for phase in phases:
            # Keeping all earlier rows preserves explicit cross-phase DAG references.
            selected = [row for row in rows if w.PHASES.index(row["phase"]) <= w.PHASES.index(phase)]
            frozen = list(evidence.task_definitions(selected).values())
            if len(json.dumps(frozen).encode()) > 65536:
                _fail("task_snapshot_too_large", "/tasks", "Compiled task snapshot exceeds 64 KiB; narrow the definition.")
            publications[phase] = {"schema": TASKS_SCHEMA, "tasks": selected}
    except w.Refusal as exc:
        _fail("invalid_harness_contract", "/tasks", str(exc))
    return {"semantic_scope": scope, "task_patterns": publications, "phase_files": phase_files,
            "task_outputs": outputs, "source_outputs": sorted(source_outputs), "snapshots": snapshots,
            "entry_phase": phases[0], "criteria": criteria, "capabilities": capabilities}


def _preview(data: dict[str, Any], bindings: dict[str, Any], capabilities: dict[str, Any],
             plan: dict[str, Any] | None, blockers: list[dict[str, str]]) -> dict[str, Any]:
    return {"schema": PREVIEW_SCHEMA, "status": "blocked" if blockers else (
                "incomplete" if bindings["unresolved_inputs"] else "ready"),
            "runnable": not blockers and not bindings["unresolved_inputs"] and plan is not None,
            "name": data["name"], "version": data["version"], "route": data["route"],
            "unresolved_inputs": bindings["unresolved_inputs"], "blockers": blockers,
            "source_manifest": bindings["source_manifest"], "git_refs": bindings["git_refs"],
            "scope": plan["semantic_scope"] if plan else None,
            "tasks": plan["task_patterns"] if plan else data["tasks"],
            "phase_files": plan["phase_files"] if plan else {},
            "decisions": {"approval_default": "manual", "execution_authorized": False,
                          "capacity": "unknown; observe native readiness at dispatch",
                          "delivery_plan": "Actual Build DAG and typed checks require accepted Plan"},
            "read_coverage": {"declared_inputs": sorted(name for name, spec in data["inputs"].items()
                               if spec["type"] == "workspace_file_list"),
                              "basis": "Declared source, dependency and test lists only; completeness is not inferred from changed filenames.",
                              "graph": "Optional graph suggestions are not included; no graph scan was run."},
            "effects": {"source_writes": plan["source_outputs"] if plan else [],
                        "external_actions": [], "preview_writes": [], "workers_dispatched": 0},
            "definition_digest": b.definition_digest(data),
            "compiler_digest": capabilities["compiler_digest"],
            "capability_digest": capabilities["capability_digest"],
            "runtime_digest": capabilities["runtime"]["digest"]}


def preview(workspace: str | os.PathLike[str], definition: dict[str, Any],
            inputs: dict[str, Any] | None = None, *,
            out: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Inspect available bindings without persistence, run creation or dispatch."""
    data = b.validate_definition(definition)
    root = Path(workspace).resolve()
    bindings = bind_inputs(root, data, inputs, incomplete=True)
    capabilities = catalog.catalog(data["tasks"], requirements=data["runtime_requirements"], route=data["route"])
    blockers = list(capabilities["blockers"])
    plan = None
    if not bindings["unresolved_inputs"]:
        try:
            destination = package_path(root, out, bindings)
            plan = _assemble(root, data, bindings, capabilities, destination)
            _check_outputs(root, plan)
        except b.BlueprintError as exc:
            blockers.append(exc.diagnostic())
    return _preview(data, bindings, capabilities, plan, blockers)


def _check_outputs(root: Path, plan: dict[str, Any]) -> None:
    mutable = set(plan["source_outputs"])
    for paths in plan["semantic_scope"]["paths"].values():
        for relative in paths:
            target = _path(root, relative)
            if target.exists() and relative not in mutable:
                _fail("output_exists", "/outputs/" + relative, "Invocation output already exists; select a fresh output prefix.")
    for relative in plan["semantic_scope"]["verification_inputs"]:
        _path(root, relative)


def _binding(data: dict[str, Any], capabilities: dict[str, Any], destination: str,
             digest: str) -> dict[str, Any]:
    return {"schema": BINDING_SCHEMA, "package_path": destination, "package_digest": digest,
            "definition_id": data["id"], "definition_version": data["version"],
            "definition_digest": b.definition_digest(data),
            "compiler_digest": capabilities["compiler_digest"],
            "capability_digest": capabilities["capability_digest"],
            "runtime_digest": capabilities["runtime"]["digest"]}


def build_package(workspace: str | os.PathLike[str], definition: dict[str, Any],
                  inputs: dict[str, Any] | None = None,
                  out: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Construct a complete package in memory; ``members`` maps names to bytes."""
    data = b.validate_definition(definition)
    root = Path(workspace).resolve()
    bindings = bind_inputs(root, data, inputs)
    capabilities = catalog.catalog(data["tasks"], requirements=data["runtime_requirements"], route=data["route"])
    if not capabilities["compatible"]:
        first = capabilities["blockers"][0]
        _fail("runtime_incompatible", first["location"], first["code"] + ": " + first["message"])
    destination = package_path(root, out, bindings)
    plan = _assemble(root, data, bindings, capabilities, destination)
    _check_outputs(root, plan)
    view = _preview(data, bindings, capabilities, plan, [])
    members: dict[str, bytes] = {"definition.json": canonical_bytes(data),
                               "bindings.json": canonical_bytes(bindings),
                               "capabilities.json": canonical_bytes(capabilities),
                               "tasks.json": canonical_bytes(plan["task_patterns"][plan["entry_phase"]]),
                               "preview.json": canonical_bytes(view)}
    for relative, snapshot in plan["snapshots"].items():
        row = bindings["source_manifest"][relative]
        raw = _blob(root, row["base_commit"], relative)
        if raw is None or content_fingerprint(raw) != row["sha256"]:
            _fail("source_drift", "/inputs/" + relative, "Deleted-file blob changed while compiling.")
        members[snapshot[len(destination) + 1:]] = raw
    members["preview.md"] = render_preview(view).encode("utf-8")
    core = {"schema": COMPILATION_SCHEMA, "package_path": destination,
            "definition_digest": b.definition_digest(data), "binding_digest": content_fingerprint(bindings),
            "compiler_digest": capabilities["compiler_digest"], "capability_digest": capabilities["capability_digest"],
            "runtime_digest": capabilities["runtime"]["digest"], "compatible": True,
            **{key: plan[key] for key in ("semantic_scope", "task_patterns", "phase_files", "task_outputs", "source_outputs", "snapshots", "entry_phase", "criteria")},
            "members": {name: content_fingerprint(raw) for name, raw in sorted(members.items())}}
    digest = content_fingerprint(core)
    binding = _binding(data, capabilities, destination, digest)
    scope = {**plan["semantic_scope"], "workflow_binding": binding}
    members["scope.json"] = canonical_bytes(scope)
    members["compilation.json"] = canonical_bytes({**core, "digest": digest})
    if sum(len(raw) for raw in members.values()) > MAX_PACKAGE_BYTES:
        _fail("package_too_large", "/out", "Compiled package exceeds the 32 MiB limit.")
    # Re-read source inputs after constructing the package to catch concurrent edits.
    _verify_sources(root, bindings, set(), allow_head_change=False)
    return {"status": "compiled", "package_path": destination, "package_digest": digest,
            "workflow_binding": binding, "scope": scope, "compilation": {**core, "digest": digest},
            "members": members, "preview": view,
            "start_arguments": ["flow", "start", "--workspace", str(root), "--scope", destination + "/scope.json",
                                "--tasks", destination + "/tasks.json", *(["--standalone", "--phase", plan["entry_phase"]]
                                if data["route"]["kind"] == "standalone" else [])]}


def render_preview(view: dict[str, Any]) -> str:
    """Readable plain Markdown; definition strings are data, never commands."""
    lines = ["# Workflow preview", "", str(view["name"]), "", "Status: " + view["status"],
             "Execution authorized: no. Native capacity: unknown.", ""]
    if view["unresolved_inputs"]:
        lines += ["Unresolved inputs: " + ", ".join(view["unresolved_inputs"]), ""]
    for blocker in view["blockers"]:
        lines.append(blocker["code"] + ": " + blocker["message"])
    if view["scope"]:
        for phase, paths in view["scope"]["paths"].items():
            if paths:
                lines += ["", phase + " writes:", *["- " + path for path in paths]]
    lines += ["", "Declared reads:", *["- " + path for path in view["source_manifest"]], "",
              view["read_coverage"]["basis"], view["read_coverage"]["graph"],
              "Manual checkpoints remain required. No workers or external actions were executed."]
    return "\n".join(lines) + "\n"


def compile_package(workspace: str | os.PathLike[str], definition: dict[str, Any],
                    inputs: dict[str, Any] | None = None,
                    out: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Publish only a finite validated package; compilation.json is written last.

    A pre-existing partial package is never repaired or overwritten automatically.
    It remains available for inspection and requires a fresh namespace.
    """
    package = build_package(workspace, definition, inputs, out)
    root = Path(workspace).resolve()
    destination = package["package_path"]
    target = _path(root, destination)
    members = package["members"]
    if target.exists():
        inspected = verify_package(root, package["workflow_binding"])
        if inspected["package_digest"] != package["package_digest"]:
            _fail("package_conflict", "/out", "Existing package has different content.")
        return {key: value for key, value in {**package, "status": "unchanged"}.items() if key != "members"}
    try:
        _path(root, ".taskplane").mkdir(exist_ok=True)
        _path(root, ".taskplane/bootstrap").mkdir(exist_ok=True)
        _path(root, destination).mkdir()  # exclusive directory creation claims this namespace
        for name in [*sorted(set(members) - {"compilation.json"}), "compilation.json"]:
            relative = destination + "/" + name
            file = _path(root, relative)
            if file.parent != target:
                file.parent.mkdir(exist_ok=True)
            file = _path(root, relative)
            fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
            with os.fdopen(fd, "wb") as stream:
                stream.write(members[name])
                stream.flush()
                os.fsync(stream.fileno())
    except (OSError, w.Refusal) as exc:
        _fail("publication_failed", "/out", "No existing bytes were replaced; inspect any partial package: " + str(exc))
    verify_package(root, package["workflow_binding"])
    return {key: value for key, value in {**package, "status": "created"}.items() if key != "members"}


def _object(root: Path, relative: str) -> dict[str, Any]:
    raw = _read(root, relative)
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail("package_invalid", "/package", "Duplicate key in package JSON.")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        if not isinstance(value, dict) or canonical_bytes(value) != raw:
            _fail("package_invalid", "/package", "Package JSON must be a canonical object.")
        return dict(value)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, b.BlueprintError):
            raise
        _fail("package_invalid", "/package", str(exc))


def _verify_sources(root: Path, bindings: dict[str, Any], mutable: set[str], *,
                    allow_head_change: bool) -> None:
    for name, commit in bindings["git_refs"].items():
        if name == "head_ref" and allow_head_change:
            continue
        if _commit(root, bindings["values"][name]) != commit:
            _fail("git_ref_drift", "/inputs/" + name, "A selected Git ref changed after binding.")
    if bindings["checkout_head"] and not allow_head_change and _commit(root, "HEAD") != bindings["checkout_head"]:
        _fail("checkout_drift", "/inputs/head_ref", "Checkout HEAD changed after binding.")
    for relative, row in bindings["source_manifest"].items():
        target = _path(root, relative)  # even mutable outputs may never become symlinks
        if relative in mutable:
            if target.exists() and not target.is_file():
                _fail("source_drift", "/inputs/" + relative, "Mutable Build input is not a regular file.")
            continue
        if row["kind"] == "deleted":
            raw = _blob(root, row["base_commit"], relative)
            if target.exists() or raw is None or content_fingerprint(raw) != row["sha256"]:
                _fail("source_drift", "/inputs/" + relative, "Deleted-file evidence changed.")
        elif not target.exists() or content_fingerprint(_read(root, relative)) != row["sha256"]:
            _fail("source_drift", "/inputs/" + relative, "Bound source/dependency/test bytes changed.")


def _mutable_paths(root: Path, core: dict[str, Any], state: dict[str, Any] | None,
                   explicit: Iterable[str]) -> set[str]:
    mutable = set(explicit)
    if state is not None:
        if Path(state.get("workspace", "")).resolve() != root:
            _fail("state_mismatch", "/state/workspace", "State belongs to another workspace.")
        # Accepted Plan is the authority for mutable source, including later phases.
        try:
            plan = w.accepted_plan(state)
        except w.Refusal:
            plan = None
        allowed = set(plan["packet"]["output"]["write_scope"]) if plan else set()
        if mutable and not mutable <= allowed:
            _fail("mutable_scope_violation", "/state", "Mutable paths exceed the accepted Plan.")
        mutable = allowed
    if mutable and (not core["source_outputs"] or not mutable <= set(core["semantic_scope"]["paths"]["build"])):
        _fail("mutable_scope_violation", "/state", "Mutable paths exceed the pinned outer Build scope.")
    for relative in mutable:
        _path(root, relative)
    return mutable


def verify_package(workspace: str | os.PathLike[str], binding: dict[str, Any], *,
                   scope: dict[str, Any] | None = None, state: dict[str, Any] | None = None,
                   authorized_mutable_paths: Iterable[str] = (),
                   for_start: bool = False) -> dict[str, Any]:
    """Verify pinned package/identity/inputs without writes or lifecycle operations.

    ``binding`` is scope.workflow_binding. Callers supplying mutable paths assert
    that the existing harness authorized them; they never come from a definition.
    With ``state``, only its accepted Plan permits mutation. Package, runtime and
    semantic scope are always immutable, regardless of mutable source authority.
    Fresh-run admission passes ``for_start=True`` to refuse reused output files;
    continuation leaves it false because its own evidence already exists.
    """
    root = Path(workspace).resolve()
    if not isinstance(binding, dict) or binding.get("schema") != BINDING_SCHEMA:
        _fail("invalid_workflow_binding", "/workflow_binding", "Unsupported workflow binding.")
    try:
        destination = package_path(root, binding["package_path"], {})
        record = _object(root, destination + "/compilation.json")
        core = {key: value for key, value in record.items() if key != "digest"}
        digest = content_fingerprint(core)
        if (record.get("schema") != COMPILATION_SCHEMA or record.get("digest") != digest
                or binding.get("package_digest") != digest or core.get("package_path") != destination):
            _fail("package_integrity", "/workflow_binding/package_digest", "Compilation identity differs from its pinned binding.")
        expected_members = set(core["members"]) | {"scope.json", "compilation.json"}
        actual_members: set[str] = set()
        total = 0
        for target in _path(root, destination).rglob("*"):
            relative = target.relative_to(root).as_posix()
            _path(root, relative)
            if target.is_dir():
                if target.relative_to(root / destination).as_posix() != "sources":
                    _fail("package_integrity", "/package", "Unexpected package directory.")
                continue
            name = target.relative_to(root / destination).as_posix()
            actual_members.add(name)
            raw = _read(root, relative)
            total += len(raw)
            if name in core["members"] and content_fingerprint(raw) != core["members"][name]:
                _fail("package_integrity", "/package/" + name, "Pinned package member changed.")
        if actual_members != expected_members or total > MAX_PACKAGE_BYTES:
            _fail("package_integrity", "/package", "Package is incomplete, has extra members or exceeds its size limit.")
        data = b.parse_definition(_read(root, destination + "/definition.json"))
        bindings = _object(root, destination + "/bindings.json")
        capabilities = _object(root, destination + "/capabilities.json")
        if bindings.get("workspace") != str(root) or bindings.get("status") != "complete":
            _fail("binding_mismatch", "/bindings", "Package is incomplete or belongs to a different workspace.")
        expected_binding = _binding(data, capabilities, destination, digest)
        if binding != expected_binding:
            _fail("binding_mismatch", "/workflow_binding", "Pinned definition/runtime/capability metadata differs.")
        pinned_scope = {**core["semantic_scope"], "workflow_binding": expected_binding}
        if _object(root, destination + "/scope.json") != pinned_scope:
            _fail("scope_drift", "/scope", "Pinned scope envelope changed.")
        current_scope = state["scope"] if state is not None else scope
        if scope is not None and state is not None and scope != state["scope"]:
            _fail("scope_drift", "/scope", "Supplied scope and state disagree.")
        if current_scope is not None and current_scope != pinned_scope:
            _fail("scope_drift", "/scope", "Run semantic scope or workflow binding changed.")
        observed = catalog.catalog(data["tasks"], requirements=data["runtime_requirements"], route=data["route"])
        if not observed["compatible"]:
            first = observed["blockers"][0]
            _fail("runtime_incompatible", first["location"], first["code"] + ": " + first["message"])
        if observed != capabilities:
            _fail("runtime_drift", "/capabilities", "Loaded runtime, compiler or selected capability assets changed.")
        plan = _assemble(root, data, bindings, capabilities, destination)
        for key in ("semantic_scope", "task_patterns", "phase_files", "task_outputs", "source_outputs", "snapshots", "entry_phase", "criteria"):
            if core[key] != plan[key]:
                _fail("package_integrity", "/compilation/" + key, "Compiled semantics differ from pinned definition and bindings.")
        if (core["definition_digest"] != b.definition_digest(data)
                or core["binding_digest"] != content_fingerprint(bindings)
                or core["compiler_digest"] != capabilities["compiler_digest"]
                or core["capability_digest"] != capabilities["capability_digest"]
                or core["runtime_digest"] != capabilities["runtime"]["digest"] or core["compatible"] is not True):
            _fail("package_integrity", "/compilation", "Compilation provenance differs from members.")
        if _object(root, destination + "/tasks.json") != plan["task_patterns"][plan["entry_phase"]]:
            _fail("package_integrity", "/tasks", "Entry task publication changed.")
        if for_start:
            _check_outputs(root, plan)
        mutable = _mutable_paths(root, core, state, authorized_mutable_paths)
        _verify_sources(root, bindings, mutable, allow_head_change=bool(mutable))
        return {"status": "valid", "package_path": destination, "package_digest": digest,
                "workflow_binding": expected_binding, "definition": data, "bindings": bindings,
                "compilation": record, "scope": pinned_scope, "mutable_paths": sorted(mutable)}
    except b.BlueprintError:
        raise
    except (KeyError, TypeError, ValueError, OSError, AttributeError) as exc:
        _fail("package_invalid", "/package", "Invalid or incomplete package: " + str(exc))
