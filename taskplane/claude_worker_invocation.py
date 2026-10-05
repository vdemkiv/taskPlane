"""Bounded cooperative Claude hook-to-CLI references, not host authentication.

The controller owns actor observation, current-attempt checks, admission capacity,
safe storage and its existing file lock. Under that lock it calls ``issue_record``
after guarding an automatic pending Bash call, stores the returned record inside
that admission, durably saves, and only then emits the rewritten input. Duplicate
pre-hooks must pass that admission's existing record, even if it is consumed.

Before controller selection, ``parse_cli(sys.argv)`` and ``decode_reference`` give
an untrusted locator. The caller must load the *existing* validated controller
database; absence must never initialize a store or fall back to inherited identity.
Under the same controller lock it finds exactly one matching pending automatic
admission, reconstructs its current binding and calls ``consume_record``. Its
``persist`` callback saves the consumed record durably, and ``dispatch`` invokes
the already-locked claim/context implementation. Neither callback reacquires the
lock. A failed save never dispatches; a crash or failed dispatch burns the token.

Bindings come from guarded controller state, never command/environment assertions.
``active`` means the exact admission AND run/visit/attempt are still current,
pending, unrevoked, nonterminal and unconflicted. ``claimed`` requires claimed_at.
The three SHA fields for binding/workspace/runtime use content_fingerprint; worker
task/input/proof digests use the already-validated controller evidence digests.
Root context needs a separately observed root actor; missing child IDs do not prove
root provenance. Fixture tests of this module do not certify host updatedInput.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import ctypes
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import secrets
import shlex
import stat
import sys
import time
from typing import Any, Callable, Mapping, Sequence, TypeVar

from . import primitives, workflow as w

SCHEMA = "taskplane.claude-invocation/v1"
TTL_NS = 120 * 1_000_000_000
MAX_COMMAND_BYTES = 16 * 1024
MAX_INPUT_BYTES = 32 * 1024
MAX_REFERENCE_BYTES = 340
MAX_ADMISSIONS = 512
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}\Z")
_UNSAFE = re.compile(r"[\x00-\x1f\x7f;&|<>`$(){}\[\]*?~\\#]")
_WORKER_FIELDS = {"grant_id", "attempt", "task_generation", "launch_call_id",
                  "launch_evidence_sha256", "task_sha256", "inputs_sha256", "claimed"}
BINDING_FIELDS = {"kind", "workspace", "root", "actor", "call_id", "run", "visit",
                  "revision", "binding_sha256", "workspace_contract_sha256", "runtime_sha256",
                  "task_id", "active", "automatic"} | _WORKER_FIELDS
_RECORD_FIELDS = {"schema", "reference", "binding", "argv", "argv_sha256",
                  "original_input_sha256", "rewritten_input_sha256",
                  "original_command_sha256", "rewritten_command_sha256",
                  "issued", "expires", "state", "consumed", "fingerprint"}
T = TypeVar("T")


def _require(condition: object, detail: str, reason: str = "scope_violation") -> None:
    w.require(condition, reason, detail)


def _text(value: object, limit: int = 200) -> bool:
    try:
        return (isinstance(value, str) and bool(value) and len(value.encode("utf-8")) <= limit
                and not any(ord(c) < 32 or ord(c) == 127 for c in value))
    except UnicodeError:
        return False


def _sha(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _path(value: object) -> bool:
    return (_text(value, 4096) and isinstance(value, str) and value.startswith("/")
            and not value.startswith("//") and os.path.normpath(value) == value)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _runtime(python: str | None, script: str | None) -> tuple[str, str]:
    _require(os.name == "posix" and sys.platform in {"darwin", "linux"},
             "Claude invocation transport is unavailable on this platform.", "unsupported_authority")
    return (str(Path(python or sys.executable).resolve()),
            str(Path(script).resolve() if script else Path(__file__).with_name("tp.py").resolve()))


@dataclass(frozen=True)
class InvocationCommand:
    """A validated command; argv is canonical and includes the interpreter."""

    command: str
    argv: tuple[str, ...]
    action: str
    workspace: str
    run: str
    task_id: str | None
    grant_id: str | None

    @property
    def canonical(self) -> str:
        return shlex.join(self.argv)


def _parse_words(words: Sequence[str], command: str, runtime: tuple[str, str]) -> InvocationCommand:
    _require(isinstance(words, (list, tuple)) and 4 <= len(words) <= 22
             and all(_text(word, MAX_COMMAND_BYTES) for word in words),
             "Invalid invocation argument vector.")
    _require(all(not _UNSAFE.search(word) for word in words), "Shell syntax is not supported.")
    _require(len(shlex.join(words).encode("utf-8")) <= MAX_COMMAND_BYTES,
             "Canonical invocation exceeds its byte bound.")
    _require(tuple(words[:2]) == runtime, "Invocation must use the exact installed Python and tp.py.")
    _require(words[2] == "flow" and words[3] in {"worker", "context"},
             "Only worker claim and context invocation transport is supported.")
    action = words[3]
    allowed = ({"--operation", "--run", "--grant", "--workspace"} if action == "worker" else
               {"--workspace", "--run", "--task", "--consume", "--read-required", "--drain",
                "--read", "--page", "--section"})
    rest = words[4:]
    _require(len(rest) % 2 == 0, "Every invocation option requires one value.")
    values: dict[str, str] = {}
    for key, value in zip(rest[::2], rest[1::2]):
        _require(key in allowed and key not in values and not value.startswith("-"),
                 "Unknown, abbreviated, duplicate or malformed invocation option.")
        values[key] = value
    _require({"--workspace", "--run"} <= values.keys()
             and _path(values["--workspace"]) and _ID.fullmatch(values["--run"]),
             "An exact run and normalized absolute workspace are required.")
    for key in ("--grant", "--task"):
        _require(key not in values or _ID.fullmatch(values[key]), "Invalid task or grant identifier.")
    order: tuple[str, ...]
    if action == "worker":
        _require(set(values) == allowed and values["--operation"] == "claim",
                 "Only the exact worker claim operation is supported.")
        order = ("--operation", "--run", "--grant", "--workspace")
    else:
        selectors = set(values) & {"--consume", "--read-required", "--drain", "--read"}
        _require(len(selectors) <= 1 and all(_sha(values[k]) for k in selectors),
                 "Context requires at most one exact SHA-256 selector.")
        _require(not ({"--page", "--section"} & values.keys()) or "--read" in values,
                 "Context page and section require --read.")
        _require("--page" not in values or re.fullmatch(r"0|[1-9][0-9]{0,8}", values["--page"]),
                 "Context page must be a bounded canonical nonnegative integer.")
        _require("--section" not in values or _text(values["--section"], 256),
                 "Context section is invalid.")
        order = ("--workspace", "--run", "--task", "--consume", "--read-required", "--drain",
                 "--read", "--page", "--section")
    canonical = (*runtime, "flow", action, *(item for key in order if key in values
                                            for item in (key, values[key])))
    return InvocationCommand(command, canonical, action, values["--workspace"], values["--run"],
                             values.get("--task"), values.get("--grant"))


def parse_command(command: str, *, python: str | None = None,
                  script: str | None = None) -> InvocationCommand:
    """Parse a single foreground POSIX command without evaluating shell text.

    Optional runtime paths are trusted integration/test configuration, never CLI
    values. Defaults always identify the executing installed helper. Deliberately
    narrow literal grammar refuses shell metacharacters even inside quotes.
    """
    _require(_text(command, MAX_COMMAND_BYTES) and not _UNSAFE.search(command),
             "Invocation command contains unsupported shell syntax or exceeds its bound.")
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        raise w.Refusal("scope_violation", "Invocation command has invalid quoting.") from None
    return _parse_words(words, command, _runtime(python, script))


def parse_cli(argv: Sequence[str], *, python: str | None = None,
              script: str | None = None) -> tuple[InvocationCommand, str]:
    """Parse sys.argv including tp.py, requiring one final reference pair.

    Interpreter identity comes from the executing process, not sys.argv or PATH.
    Require exactly the rewritten canonical argv; reordering it is a mismatch.
    """
    runtime = _runtime(python, script)
    _require(isinstance(argv, (list, tuple)) and 5 <= len(argv) <= 23
             and all(_text(x, MAX_COMMAND_BYTES) for x in argv)
             and argv[-2] == "--invocation-ref" and argv.count("--invocation-ref") == 1,
             "A single final automatic invocation reference is required.", "unsupported_authority")
    reference = argv[-1]
    decode_reference(reference)
    words = (runtime[0], *argv[:-2])
    parsed = _parse_words(words, shlex.join(words), runtime)
    _require(parsed.argv == words, "Invocation argv differs from the canonical hook rewrite.")
    return parsed, reference


def decode_reference(reference: str) -> str:
    """Decode only a bounded canonical root locator; this establishes no identity."""
    _require(_text(reference, MAX_REFERENCE_BYTES), "Invalid invocation locator.", "unsupported_authority")
    parts = reference.split(".")
    _require(len(parts) == 3 and parts[0] == "v1" and _sha(parts[2])
             and re.fullmatch(r"[A-Za-z0-9_-]{1,267}", parts[1]),
             "Invalid invocation locator.", "unsupported_authority")
    try:
        raw = base64.b64decode(parts[1] + "=" * (-len(parts[1]) % 4), altchars=b"-_", validate=True)
        root = raw.decode("utf-8")
    except (ValueError, UnicodeError):
        raise w.Refusal("unsupported_authority", "Invalid invocation root encoding.") from None
    _require(_text(root) and base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") == parts[1],
             "Invocation root encoding is not canonical.", "unsupported_authority")
    return root


def _reference(root: str) -> str:
    encoded = base64.urlsafe_b64encode(root.encode("utf-8")).decode("ascii").rstrip("=")
    return "v1." + encoded + "." + secrets.token_hex(32)


def runtime_digest() -> str:
    """The same installed runtime identity used by automatic hook readiness."""
    from . import host_capabilities
    try:
        return primitives.content_fingerprint(host_capabilities.runtime_identity())
    except (OSError, ValueError, TypeError):
        raise w.Refusal("state_unavailable", "Installed runtime identity is unavailable.") from None


def _boot_identity() -> str:
    """Fixed OS adapters; no environment or caller-supplied boot identity."""
    if sys.platform == "linux":
        descriptor = os.open("/proc/sys/kernel/random/boot_id", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError("Boot identity must be regular")
            value = stream.read(65).decode("ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value):
            raise OSError("Boot identity is invalid")
        return "linux:" + value
    if sys.platform == "darwin":
        class Timeval(ctypes.Structure):
            _fields_ = [("sec", ctypes.c_long), ("usec", ctypes.c_int)]
        value = Timeval()
        size = ctypes.c_size_t(ctypes.sizeof(value))
        query = ctypes.CDLL(None, use_errno=True).sysctlbyname
        query.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                          ctypes.c_void_p, ctypes.c_size_t]
        query.restype = ctypes.c_int
        if query(b"kern.boottime", ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
            raise OSError(ctypes.get_errno(), "Boot time is unavailable")
        if size.value != ctypes.sizeof(value) or value.sec <= 0 or not 0 <= value.usec < 1_000_000:
            raise OSError("Boot time is invalid")
        return f"darwin:{value.sec}:{value.usec}"
    raise OSError("Boot identity is unavailable on this platform")


def _clock() -> dict[str, Any]:
    try:
        return {"utc_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(), "boot_id": _boot_identity()}
    except (OSError, ValueError, AttributeError, UnicodeError):
        raise w.Refusal("state_unavailable", "OS boot identity or invocation clock is unavailable.") from None


def _valid_clock(value: object) -> bool:
    return (isinstance(value, dict) and set(value) == {"utc_ns", "monotonic_ns", "boot_id"}
            and all(type(value[k]) is int and 0 < value[k] < 2 ** 63 for k in ("utc_ns", "monotonic_ns"))
            and _text(value["boot_id"], 128))


def _validate_binding(binding: object) -> None:
    _require(isinstance(binding, dict) and set(binding) == BINDING_FIELDS,
             "Invocation binding schema is invalid.", "state_unavailable")
    assert isinstance(binding, dict)
    _require(isinstance(binding["kind"], str)
             and binding["kind"] in {"worker-claim", "worker-context", "root-context"}
             and _path(binding["workspace"])
             and all(_text(binding[k]) for k in ("root", "actor", "run", "visit"))
             and _text(binding["call_id"], 512)
             and type(binding["revision"]) is int and binding["revision"] >= 0
             and all(_sha(binding[k]) for k in ("binding_sha256", "workspace_contract_sha256", "runtime_sha256"))
             and binding["active"] is True and binding["automatic"] is True,
             "Invocation binding is incomplete, inactive or not automatic.", "stale_checkpoint")
    _require(binding["task_id"] is None or (_text(binding["task_id"]) and _ID.fullmatch(binding["task_id"])),
             "Invocation task is invalid.")
    if binding["kind"] == "root-context":
        _require(binding["actor"] == binding["root"] and all(binding[k] is None for k in _WORKER_FIELDS),
                 "Root context cannot carry worker authority.")
    else:
        _require(binding["actor"] != binding["root"] and binding["task_id"] is not None
                 and _text(binding["grant_id"]) and _ID.fullmatch(binding["grant_id"])
                 and _text(binding["launch_call_id"], 512)
                 and type(binding["attempt"]) is int and binding["attempt"] >= 1
                 and type(binding["task_generation"]) is int and binding["task_generation"] >= 0
                 and all(_sha(binding[k]) for k in ("launch_evidence_sha256", "task_sha256", "inputs_sha256"))
                 and type(binding["claimed"]) is bool,
                 "Worker invocation needs an exact observed actor, task, attempt and launch proof.")
        _require(binding["kind"] != "worker-context" or binding["claimed"] is True,
                 "Worker context requires the current claimed task.")


def _bind_command(command: InvocationCommand, binding: dict[str, Any]) -> None:
    _validate_binding(binding)
    _require(command.workspace == binding["workspace"] and command.run == binding["run"],
             "Invocation command belongs to another workspace or run.")
    if binding["kind"] == "worker-claim":
        _require(command.action == "worker" and command.grant_id == binding["grant_id"],
                 "Claim command differs from the observed worker grant.")
    else:
        _require(command.action == "context" and command.task_id == binding["task_id"],
                 "Context command differs from the observed root or worker task.")


def rewrite_input(command: InvocationCommand, tool_input: Mapping[str, Any], reference: str) -> dict[str, Any]:
    """Preserve host-validated Bash fields, changing only command.

    The controller's versioned host adapter validates the accepted Bash field
    schema. This helper additionally rejects background execution and oversized
    or non-JSON input. Arbitrary metadata never contributes identity.
    """
    decode_reference(reference)
    _require(isinstance(tool_input, dict) and tool_input.get("command") == command.command
             and ("run_in_background" not in tool_input or tool_input["run_in_background"] is False),
             "Bash input must match the exact foreground command.")
    assert isinstance(tool_input, dict)
    try:
        raw = primitives.canonical_bytes(tool_input)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise w.Refusal("scope_violation", "Bash input is not bounded JSON.") from None
    _require(len(raw) <= MAX_INPUT_BYTES, "Bash input exceeds its bound.")
    result = deepcopy(tool_input)
    result["command"] = shlex.join((*command.argv, "--invocation-ref", reference))
    _require(len(primitives.canonical_bytes(result)) <= MAX_INPUT_BYTES,
             "Rewritten Bash input exceeds its bound.")
    return result


def _seal(record: dict[str, Any]) -> dict[str, Any]:
    return primitives.manifest_record(record)


def issue_record(command: InvocationCommand, tool_input: Mapping[str, Any], binding: dict[str, Any], *,
                 existing: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prepare one record under the caller's controller lock; caller must save it.

    Re-delivery of the same pre-hook reuses only its still-pending reference.
    Capacity/eviction policy belongs to the controller (at most 512 admissions).
    No helper here creates a separate store, lock, controller or global actor.
    """
    _require(isinstance(command, InvocationCommand) and command == parse_command(command.command),
             "Invocation command was not parsed against the executing runtime.")
    _bind_command(command, binding)
    _require(binding["runtime_sha256"] == runtime_digest(),
             "Executing runtime fingerprint differs from the automatic hook.", "stale_checkpoint")
    if existing is not None:
        validate_pending(existing, command, existing.get("reference", ""), binding)
        rewritten = rewrite_input(command, tool_input, existing["reference"])
        _require(existing["original_input_sha256"] == primitives.content_fingerprint(tool_input)
                 and existing["original_command_sha256"] == _digest(command.command)
                 and existing["rewritten_input_sha256"] == primitives.content_fingerprint(rewritten),
                 "Conflicting duplicate automatic invocation admission.")
        return deepcopy(existing), rewritten
    reference = _reference(binding["root"])
    rewritten = rewrite_input(command, tool_input, reference)
    issued = _clock()
    _require(_valid_clock(issued), "Invocation clock is invalid.", "state_unavailable")
    record = _seal({"schema": SCHEMA, "reference": reference, "binding": deepcopy(binding),
                    "argv": list(command.argv), "argv_sha256": primitives.content_fingerprint(list(command.argv)),
                    "original_input_sha256": primitives.content_fingerprint(tool_input),
                    "rewritten_input_sha256": primitives.content_fingerprint(rewritten),
                    "original_command_sha256": _digest(command.command),
                    "rewritten_command_sha256": _digest(rewritten["command"]),
                    "issued": issued,
                    "expires": {"utc_ns": issued["utc_ns"] + TTL_NS,
                                "monotonic_ns": issued["monotonic_ns"] + TTL_NS,
                                "boot_id": issued["boot_id"]},
                    "state": "pending", "consumed": None})
    validate_record(record)
    return record, rewritten


def validate_record(record: object) -> None:
    """Strict persistent schema validation; historical records need not be live."""
    _require(isinstance(record, dict) and set(record) == _RECORD_FIELDS,
             "Invocation record schema is invalid.", "state_unavailable")
    assert isinstance(record, dict)
    _require(record["schema"] == SCHEMA and isinstance(record["state"], str)
             and record["state"] in {"pending", "consumed"}
             and all(_sha(record[k]) for k in _RECORD_FIELDS if k.endswith("sha256") or k == "fingerprint"),
             "Invocation record fields are invalid.", "state_unavailable")
    _require(record["fingerprint"] == _seal(record)["fingerprint"],
             "Invocation record fingerprint is corrupt.", "state_unavailable")
    binding = record["binding"]
    _validate_binding(binding)
    _require(decode_reference(record["reference"]) == binding["root"],
             "Invocation locator belongs to another root.")
    argv = record["argv"]
    _require(isinstance(argv, list) and 4 <= len(argv) <= 22
             and all(_text(x, MAX_COMMAND_BYTES) for x in argv),
             "Stored invocation argv is invalid.", "state_unavailable")
    # Historical validation does not depend on today's installed runtime path.
    _require(_path(argv[0]) and _path(argv[1]), "Stored launcher paths are invalid.", "state_unavailable")
    command = _parse_words(argv, shlex.join(argv), (argv[0], argv[1]))
    _bind_command(command, binding)
    _require(list(command.argv) == argv and primitives.content_fingerprint(argv) == record["argv_sha256"]
             and _digest(shlex.join((*argv, "--invocation-ref", record["reference"])))
             == record["rewritten_command_sha256"], "Stored invocation command differs from its digest.", "state_unavailable")
    issued, expires, consumed = record["issued"], record["expires"], record["consumed"]
    _require(_valid_clock(issued) and _valid_clock(expires)
             and issued["boot_id"] == expires["boot_id"]
             and all(expires[k] - issued[k] == TTL_NS for k in ("utc_ns", "monotonic_ns")),
             "Invocation lifetime is invalid.", "state_unavailable")
    _require((record["state"] == "pending" and consumed is None)
             or (record["state"] == "consumed" and _valid_clock(consumed)
                 and consumed["boot_id"] == issued["boot_id"]
                 and all(issued[k] <= consumed[k] < expires[k] for k in ("utc_ns", "monotonic_ns"))),
             "Invocation consumption state is invalid.", "state_unavailable")


def _current_clock(record: dict[str, Any]) -> dict[str, Any]:
    now = _clock()
    _require(_valid_clock(now) and now["boot_id"] == record["issued"]["boot_id"]
             and all(record["issued"][k] <= now[k] < record["expires"][k]
                     for k in ("utc_ns", "monotonic_ns")),
             "Invocation expired, clocks regressed or boot identity changed.", "stale_checkpoint")
    return now


def _validate_pending(record: dict[str, Any], command: InvocationCommand, reference: str,
                      current_binding: dict[str, Any]) -> dict[str, Any]:
    validate_record(record)
    _require(isinstance(command, InvocationCommand) and command == parse_command(command.command),
             "Invocation command was not parsed against the executing runtime.")
    _bind_command(command, current_binding)
    _require(record["reference"] == reference and record["binding"] == current_binding
             and list(command.argv) == record["argv"],
             "Invocation reference, argv or current identity binding differs.")
    _require(record["state"] == "pending", "Invocation reference has already been consumed.")
    _require(tuple(command.argv[:2]) == _runtime(None, None)
             and current_binding["runtime_sha256"] == runtime_digest(),
             "Invocation runtime has changed.", "stale_checkpoint")
    return _current_clock(record)


def validate_pending(record: dict[str, Any], command: InvocationCommand, reference: str,
                     current_binding: dict[str, Any]) -> None:
    """Revalidate current controller-derived identity and both clocks under lock."""
    _validate_pending(record, command, reference, current_binding)


def consume_record(record: dict[str, Any], command: InvocationCommand, reference: str,
                   current_binding: dict[str, Any], *, persist: Callable[[dict[str, Any]], None],
                   dispatch: Callable[[], T]) -> T:
    """Consume durably before dispatch, entirely under the caller's controller lock.

    ``persist`` must replace this admission's invocation and durably save the same
    controller database, propagating every write error. The local object stays
    burned even if saving raises (a partially committed write is possible).
    ``dispatch`` must not run on save failure or be retried with this reference.
    """
    _require(callable(persist) and callable(dispatch), "Consumption needs durable persistence and locked dispatch.")
    now = _validate_pending(record, command, reference, current_binding)
    consumed = _seal({**record, "state": "consumed", "consumed": now})
    record.clear()
    record.update(consumed)
    try:
        persist(deepcopy(consumed))
    except (OSError, primitives.StateError):
        raise w.Refusal("state_unavailable", "Invocation consumption could not be durably saved.") from None
    # A slow durable write must not dispatch beyond the admitted lifetime.
    _current_clock(record)
    return dispatch()


def post_input_matches(record: dict[str, Any], tool_input: Mapping[str, Any]) -> bool:
    """Only the admitted original or rewritten input can complete this call.

    The controller separately verifies actor, call, binding, runtime, automatic
    provenance and post-hook status. This accepts late post input but never
    issues, consumes, resurrects or grants readiness on its own.
    """
    validate_record(record)
    try:
        raw = primitives.canonical_bytes(tool_input)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return False
    return (isinstance(tool_input, dict) and len(raw) <= MAX_INPUT_BYTES
            and primitives.content_fingerprint(tool_input)
            in {record["original_input_sha256"], record["rewritten_input_sha256"]})
