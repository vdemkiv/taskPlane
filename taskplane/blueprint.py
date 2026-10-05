"""Strict, data-only Workflow Builder v1 definitions and versioned publication.

Parsing and validation are pure. Saving creates only one exact project-local
version; none of these operations initializes a run or grants execution authority.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, NoReturn

from . import blueprint_catalog as catalog, workflow as w, workflow_evidence as evidence
from .primitives import canonical_bytes, content_fingerprint

SCHEMA = "taskplane.workflow-blueprint/v1"
MAX_DEFINITION_BYTES = 256 * 1024
MAX_TASKS = 128
MAX_INPUTS = 128
MAX_CRITERIA = 256
ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
_ROOT_FIELDS = {
    "schema", "id", "version", "name", "description", "route", "trigger", "inputs", "policy",
    "tasks", "acceptance_scenarios", "runtime_requirements",
}
_TASK_REQUIRED = {
    "id", "phase", "capability", "purpose", "execution", "depends_on", "read_bindings",
    "outputs", "criteria_binding", "verification",
}
_TASK_OPTIONAL = {"lens", "instructions", "execution_reason", "execution_reference"}


class BlueprintError(ValueError):
    """A stable diagnostic suitable for human or JSON CLI output."""

    def __init__(self, code: str, location: str, message: str,
                 remedy: str = "Correct the indicated definition field and validate again."):
        self.code = code
        self.location = location
        self.message = message
        self.remedy = remedy
        super().__init__(f"{location or '/'}: {message}")

    def diagnostic(self) -> dict[str, str]:
        return {"code": self.code, "location": self.location,
                "message": self.message, "remedy": self.remedy}

    def result(self) -> dict[str, Any]:
        return {"status": "invalid", "diagnostics": [self.diagnostic()]}


def _fail(code: str, location: str, message: str,
          remedy: str = "Correct the indicated definition field and validate again.") -> NoReturn:
    raise BlueprintError(code, location, message, remedy)


def _pointer(base: str, key: str | int) -> str:
    return base + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _object(value: Any, location: str, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        _fail("invalid_type", location, "Expected an object with string keys.")
    unknown = set(value) - required - (optional or set())
    if unknown:
        key = sorted(unknown)[0]
        _fail("unknown_field", _pointer(location, key), "Unknown field: " + key)
    missing = required - set(value)
    if missing:
        key = sorted(missing)[0]
        _fail("missing_field", _pointer(location, key), "Required field is missing.")
    return dict(value)


def _text(value: Any, location: str, limit: int = 4096) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or "\0" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value)):
        _fail("invalid_type", location, f"Expected nonempty text of at most {limit} characters.")
    return value


def _name(value: Any, location: str, *, stable: bool = False) -> str:
    text = _text(value, location, 64)
    if not (ID if stable else NAME).fullmatch(text):
        _fail("invalid_identifier", location, "Use a lowercase identifier beginning with a letter.")
    return text


def _list(value: Any, location: str, *, minimum: int = 0, maximum: int = 1024) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _fail("invalid_type", location, f"Expected an array with {minimum} to {maximum} items.")
    return list(value)


def _unique(values: list[str], location: str) -> None:
    if len(values) != len(set(values)):
        _fail("duplicate_value", location, "Values must be unique.")


def relative_path(value: Any, location: str = "", *, output: bool = False) -> str:
    """Validate a literal portable path; filesystem/symlink checks occur at binding."""
    text = _text(value, location, 1024)
    parts = text.split("/")
    if (PurePosixPath(text).is_absolute() or "\\" in text or ":" in text
            or any(c in text for c in "*?[]${}`") or any(ord(c) < 32 for c in text)
            or any(p in {"", ".", "..", ".git", ".codex", ".agents", ".taskplane"} for p in parts)):
        _fail("invalid_path", location, "Use an exact relative path outside protected metadata; expressions and globs are unsupported.")
    # The shared validator remains authoritative for workspace path containment.
    # No filesystem is consulted by this static syntax validation.
    return text


def validate_input_value(input_type: str, value: Any, location: str = "") -> Any:
    """Validate and normalize one declared value; resolve existence/Git in binding."""
    if input_type == "text":
        return _text(value, location, 16384)
    if input_type == "git_ref":
        value = _text(value, location, 256)
        if value.startswith("-") or any(c.isspace() or ord(c) < 32 for c in value) or any(c in value for c in "${}`\\"):
            _fail("invalid_git_ref", location, "Use a literal Git ref or commit; expressions and options are unsupported.")
        return value
    if input_type in {"workspace_file_list", "workspace_output_file_list"}:
        values = [relative_path(v, _pointer(location, i), output=input_type == "workspace_output_file_list")
                  for i, v in enumerate(_list(value, location))]
        _unique(values, location)
        return sorted(values)
    if input_type == "workspace_relative_directory":
        return relative_path(value, location, output=True)
    if input_type == "acceptance_criteria":
        rows = _list(value, location, minimum=1, maximum=MAX_CRITERIA)
        identifiers: list[str] = []
        result: list[dict[str, str]] = []
        for i, row in enumerate(rows):
            loc = _pointer(location, i)
            criterion = _object(row, loc, {"id", "statement"})
            key = _text(criterion["id"], loc + "/id", 128)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", key):
                _fail("invalid_identifier", loc + "/id", "Criterion IDs must be literal identifiers.")
            identifiers.append(key)
            result.append({"id": key, "statement": _text(criterion["statement"], loc + "/statement")})
        _unique(identifiers, location)
        return result
    _fail("unsupported_input_type", location, "Unknown input type: " + str(input_type))


class _Pairs(list[tuple[str, Any]]):
    pass


def _json_objects(value: Any, location: str = "") -> Any:
    if isinstance(value, _Pairs):
        result: dict[str, Any] = {}
        for key, child in value:
            loc = _pointer(location, key)
            if key in result:
                _fail("duplicate_key", loc, "Duplicate JSON object key.")
            result[key] = _json_objects(child, loc)
        return result
    if isinstance(value, list):
        return [_json_objects(child, _pointer(location, i)) for i, child in enumerate(value)]
    return value


def _constant(value: str) -> NoReturn:
    _fail("invalid_json", "", "Non-finite JSON number is forbidden: " + value)


def parse_definition(raw: bytes | str) -> dict[str, Any]:
    if not isinstance(raw, (bytes, str)):
        _fail("invalid_type", "", "Definition must be UTF-8 JSON bytes or text.")
    try:
        body = raw.encode("utf-8") if isinstance(raw, str) else raw
        if len(body) > MAX_DEFINITION_BYTES:
            _fail("definition_too_large", "", "Definition exceeds the 256 KiB limit.")
        data = json.loads(body.decode("utf-8"), object_pairs_hook=_Pairs, parse_constant=_constant)
        return validate_definition(_json_objects(data))
    except BlueprintError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        _fail("invalid_json", "", "Invalid UTF-8 JSON: " + str(exc))


def _read_file(path: Path) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                _fail("invalid_file", "", "Definition must be a regular file.")
            return stream.read(MAX_DEFINITION_BYTES + 1)
    except OSError as exc:
        _fail("definition_unavailable", "", str(exc))


def load_definition(path: str | os.PathLike[str]) -> dict[str, Any]:
    return parse_definition(_read_file(Path(path)))


def _binding_shape(value: Any, location: str, *, artifact: bool = True) -> None:
    allowed = {"input", "artifact"} if artifact else {"input"}
    binding = _object(value, location, set(), allowed)
    if len(binding) != 1:
        _fail("invalid_binding", location, "Specify exactly one typed input or artifact binding.")
    if "artifact" in binding:
        _object(binding["artifact"], location + "/artifact", {"task", "name"})


def _structure(data: Any) -> dict[str, Any]:
    """Reject closed-schema errors everywhere before looking up capabilities."""
    data = _object(data, "", _ROOT_FIELDS)
    if data["schema"] != SCHEMA:
        _fail("unsupported_schema", "/schema", "Expected " + SCHEMA)
    route = _object(data["route"], "/route", {"kind"}, {"phase"})
    if route["kind"] == "delivery":
        _object(route, "/route", {"kind"})
    _object(data["trigger"], "/trigger", {"kind"})
    _object(data["policy"], "/policy", {"approval_default", "source_writes", "external_actions", "on_failure"})
    inputs = data["inputs"]
    if not isinstance(inputs, dict) or not 1 <= len(inputs) <= MAX_INPUTS:
        _fail("invalid_type", "/inputs", f"Declare between 1 and {MAX_INPUTS} named inputs.")
    for name, spec in inputs.items():
        _name(name, _pointer("/inputs", name))
        _object(spec, _pointer("/inputs", name), {"type", "required"}, {"default"})
        if spec["type"] == "acceptance_criteria" and "default" in spec:
            for i, row in enumerate(_list(spec["default"], _pointer("/inputs", name) + "/default")):
                _object(row, _pointer("/inputs", name) + "/default/" + str(i), {"id", "statement"})
    for i, row in enumerate(_list(data["tasks"], "/tasks", minimum=1, maximum=MAX_TASKS)):
        loc = "/tasks/" + str(i)
        _object(row, loc, _TASK_REQUIRED, _TASK_OPTIONAL)
        _binding_shape(row["criteria_binding"], loc + "/criteria_binding", artifact=False)
        for j, binding in enumerate(_list(row["read_bindings"], loc + "/read_bindings")):
            _binding_shape(binding, loc + "/read_bindings/" + str(j))
        for j, output in enumerate(_list(row["outputs"], loc + "/outputs", minimum=1)):
            outloc = loc + "/outputs/" + str(j)
            _object(output, outloc, {"name", "kind"}, {"directory", "relative_path", "binding"})
            if "binding" in output:
                _object(output, outloc, {"name", "kind", "binding"})
                _binding_shape(output["binding"], outloc + "/binding", artifact=False)
            else:
                _object(output, outloc, {"name", "kind", "directory", "relative_path"})
                _binding_shape(output["directory"], outloc + "/directory", artifact=False)
    return dict(data)


def _input_binding(binding: dict[str, Any], inputs: dict[str, Any], location: str,
                   allowed_types: set[str]) -> str:
    key = _name(binding["input"], location + "/input")
    if key not in inputs:
        _fail("unknown_input", location + "/input", "Input is not declared: " + key)
    if inputs[key]["type"] not in allowed_types:
        _fail("binding_type_mismatch", location, "Input type is not valid in this binding.")
    return key


def validate_definition(value: Any) -> dict[str, Any]:
    """Validate the closed v1 contract and return detached canonicalizable data."""
    try:
        raw = canonical_bytes(value)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        _fail("invalid_json", "", str(exc))
    if len(raw) > MAX_DEFINITION_BYTES:
        _fail("definition_too_large", "", "Definition exceeds the 256 KiB limit.")
    data = deepcopy(_structure(value))
    _name(data["id"], "/id", stable=True)
    version = _text(data["version"], "/version", 128)
    if not SEMVER.fullmatch(version):
        _fail("invalid_version", "/version", "Use a semantic version such as 0.1.0.")
    _text(data["name"], "/name", 160)
    _text(data["description"], "/description")
    route = data["route"]
    phases: tuple[str, ...]
    if route["kind"] == "standalone":
        if route.get("phase") not in w.ENTRY_PHASES:
            _fail("unsupported_route", "/route/phase", "Standalone routes are product, design or engineering.")
        phases = (route["phase"],)
    elif route["kind"] == "delivery":
        phases = w.PHASES
    else:
        _fail("unsupported_route", "/route/kind", "Choose delivery or standalone.")
    if data["trigger"] != {"kind": "manual"}:
        _fail("unsupported_trigger", "/trigger", "Only manual invocation is supported.")
    policy = data["policy"]
    if (policy["approval_default"] != "manual" or policy["external_actions"] != []
            or policy["on_failure"] != "stop_and_report" or type(policy["source_writes"]) is not bool
            or policy["source_writes"] and route["kind"] != "delivery"):
        _fail("unsupported_policy", "/policy", "Require manual checkpoints, no external actions, stop_and_report, and source writes only for delivery.")
    inputs = data["inputs"]
    criteria_inputs: set[str] = set()
    for name, spec in inputs.items():
        loc = _pointer("/inputs", name)
        if spec["type"] not in catalog.INPUT_TYPES:
            _fail("unsupported_input_type", loc + "/type", "Unknown input type.")
        if type(spec["required"]) is not bool:
            _fail("invalid_type", loc + "/required", "required must be a boolean.")
        if "default" in spec:
            spec["default"] = validate_input_value(spec["type"], spec["default"], loc + "/default")
        if spec["type"] == "acceptance_criteria":
            criteria_inputs.add(name)
            if not spec["required"] and "default" not in spec:
                _fail("missing_criteria", loc, "Criteria must be required or have a nonempty default.")
    if not criteria_inputs:
        _fail("missing_criteria", "/inputs", "Declare an acceptance_criteria input.")
    covered: set[str] = set()
    index: dict[str, dict[str, Any]] = {}
    locations: dict[str, str] = {}
    owners: dict[tuple[str, str], str] = {}
    lenses: set[str] = set()
    for i, row in enumerate(data["tasks"]):
        loc = "/tasks/" + str(i)
        key = _name(row["id"], loc + "/id", stable=True)
        if key in index:
            _fail("duplicate_task", loc + "/id", "Task IDs must be unique.")
        index[key], locations[key] = row, loc
        if row["phase"] not in phases:
            _fail("unsupported_phase", loc + "/phase", "Task phase is outside the selected route.")
        _text(row["purpose"], loc + "/purpose", 512)
        _text(row["verification"], loc + "/verification")
        if "instructions" in row:
            _text(row["instructions"], loc + "/instructions", 16384)
        capability_id = _text(row["capability"], loc + "/capability", 128)
        capability = catalog.capability_contract(capability_id)
        if capability is None:
            _fail("unsupported_capability", loc + "/capability", "Capability is not registered: " + capability_id)
        if row["phase"] not in capability["phases"]:
            _fail("capability_phase_mismatch", loc + "/phase", "Capability does not support this phase.")
        if row["execution"] != capability["execution"]:
            _fail("execution_mismatch", loc + "/execution", "Use the capability's required execution: " + capability["execution"])
        if row["execution"] == "root":
            for field in ("execution_reason", "execution_reference"):
                _text(row.get(field), loc + "/" + field)
        elif "execution_reason" in row or "execution_reference" in row:
            _fail("execution_mismatch", loc, "Native tasks cannot carry a root execution exception.")
        if capability["lenses"]:
            lens = _name(row.get("lens"), loc + "/lens", stable=True)
            if lens not in capability["lenses"]:
                _fail("unsupported_lens", loc + "/lens", "Lens is not registered.")
            if lens in lenses:
                _fail("duplicate_lens", loc + "/lens", "Each independent review lens needs one task.")
            lenses.add(lens)
        elif "lens" in row:
            _fail("unsupported_lens", loc + "/lens", "This capability does not accept a lens.")
        dependencies = [_name(dep, loc + "/depends_on/" + str(j), stable=True)
                        for j, dep in enumerate(_list(row["depends_on"], loc + "/depends_on", maximum=MAX_TASKS))]
        _unique(dependencies, loc + "/depends_on")
        row["depends_on"] = sorted(dependencies)
        covered.add(_input_binding(row["criteria_binding"], inputs, loc + "/criteria_binding", {"acceptance_criteria"}))
        names: list[str] = []
        for j, output in enumerate(row["outputs"]):
            outloc = loc + "/outputs/" + str(j)
            names.append(_name(output["name"], outloc + "/name"))
            if output["kind"] not in capability["output_types"]:
                _fail("unsupported_output", outloc + "/kind", "Output kind is not supported by this capability.")
            if "binding" in output:
                binding = _input_binding(output["binding"], inputs, outloc + "/binding", {"workspace_output_file_list"})
                if row["phase"] != "build" or not policy["source_writes"] or output["kind"] != "source":
                    _fail("source_write_forbidden", outloc, "Exact source output lists require a Build capability and delivery source_writes policy.")
                owner_key = (binding, "")
            else:
                if output["kind"] == "source":
                    _fail("source_write_forbidden", outloc, "Source writes must bind an exact output-file list.")
                binding = _input_binding(output["directory"], inputs, outloc + "/directory", {"workspace_relative_directory"})
                relative = relative_path(output["relative_path"], outloc + "/relative_path", output=True)
                owner_key = (binding, relative)
            if owner_key in owners:
                _fail("output_collision", outloc, "Output is already owned by task " + owners[owner_key])
            owners[owner_key] = key
        _unique(names, loc + "/outputs")
        for j, binding in enumerate(row["read_bindings"]):
            readloc = loc + "/read_bindings/" + str(j)
            if "input" in binding:
                _input_binding(binding, inputs, readloc, set(capability["input_types"]) - {"workspace_output_file_list"})
            else:
                _name(binding["artifact"]["task"], readloc + "/artifact/task", stable=True)
                _name(binding["artifact"]["name"], readloc + "/artifact/name")
    if criteria_inputs - covered:
        _fail("uncovered_criteria", "/tasks", "No task covers criteria inputs: " + ", ".join(sorted(criteria_inputs - covered)))
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(key: str) -> None:
        if key in visiting:
            _fail("cyclic_dependency", locations[key] + "/depends_on", "Task dependencies form a cycle.")
        if key in visited:
            return
        visiting.add(key)
        row = index[key]
        for dependency in row["depends_on"]:
            if dependency not in index:
                _fail("unknown_dependency", locations[key] + "/depends_on", "Missing task: " + dependency)
            if w.PHASES.index(index[dependency]["phase"]) > w.PHASES.index(row["phase"]):
                _fail("forward_phase_dependency", locations[key] + "/depends_on", "A dependency cannot come from a later phase.")
            walk(dependency)
        visiting.remove(key)
        visited.add(key)

    for key, row in index.items():
        walk(key)
        if row["execution"] == "root" and row["phase"] == "engineering":
            reviewers = {task_id for task_id, task in index.items()
                         if task["capability"] == "taskplane.review.lens"}
            if not reviewers or not reviewers <= set(row["depends_on"]):
                _fail("native_review_required", locations[key] + "/depends_on",
                      "Engineering synthesis requires dependencies on all declared native lens reviewers.")
        for j, binding in enumerate(row["read_bindings"]):
            if "artifact" not in binding:
                continue
            artifact = binding["artifact"]
            loc = locations[key] + "/read_bindings/" + str(j)
            producer = index.get(artifact["task"])
            if producer is None or artifact["task"] not in row["depends_on"]:
                _fail("missing_producer_dependency", loc, "Artifact reads require a direct dependency on their producer.")
            if artifact["name"] not in {output["name"] for output in producer["outputs"]}:
                _fail("unknown_artifact", loc, "Producer does not declare this named output.")
    data["acceptance_scenarios"] = [_text(v, "/acceptance_scenarios/" + str(i))
        for i, v in enumerate(_list(data["acceptance_scenarios"], "/acceptance_scenarios", minimum=1, maximum=128))]
    requirements = [_text(v, "/runtime_requirements/" + str(i), 128)
        for i, v in enumerate(_list(data["runtime_requirements"], "/runtime_requirements", minimum=1, maximum=32))]
    _unique(requirements, "/runtime_requirements")
    if set(requirements) - set(catalog.KNOWN_CONTRACTS):
        _fail("unsupported_contract", "/runtime_requirements", "Unknown runtime contract.")
    missing = set(catalog.required_contracts(route)) - set(requirements)
    if missing:
        _fail("missing_contract", "/runtime_requirements", "Required contracts are missing: " + ", ".join(sorted(missing)))
    data["runtime_requirements"] = sorted(requirements)
    return data


def canonical_definition(definition: dict[str, Any]) -> bytes:
    return canonical_bytes(validate_definition(definition))


def definition_digest(definition: dict[str, Any]) -> str:
    return content_fingerprint(canonical_definition(definition))


def save_definition(workspace: str | os.PathLike[str], definition: dict[str, Any],
                    out: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Create workflows/<id>.<version>.workflow.json without replacing any bytes."""
    data = validate_definition(definition)
    raw = canonical_bytes(data)
    relative = f"workflows/{data['id']}.{data['version']}.workflow.json"
    root = Path(workspace).resolve()
    requested = Path(out) if out is not None else Path(relative)
    if requested.is_absolute():
        try:
            requested = requested.relative_to(root)
        except ValueError:
            _fail("invalid_save_path", "/out", "Published versions must be inside the selected workspace.")
    if requested.as_posix() != relative or ".." in requested.parts:
        _fail("invalid_save_path", "/out", "Save this version to " + relative)
    try:
        target = evidence.path(root, relative)
        target.parent.mkdir(exist_ok=True)
        target = evidence.path(root, relative)
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        except FileExistsError:
            if _read_file(target) != raw:
                _fail("published_version_conflict", "/version", "This version already exists with different bytes.",
                      "Choose a new semantic version; published definitions cannot be overwritten.")
            return {"status": "unchanged", "path": relative, "digest": content_fingerprint(raw),
                    "id": data["id"], "version": data["version"]}
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            # A failed write is never reported as a published version. No prior
            # version can be removed: O_EXCL created this target for this call.
            target.unlink(missing_ok=True)
            raise
        return {"status": "created", "path": relative, "digest": content_fingerprint(raw),
                "id": data["id"], "version": data["version"]}
    except BlueprintError:
        raise
    except (OSError, w.Refusal) as exc:
        _fail("save_unavailable", "/out", str(exc))
