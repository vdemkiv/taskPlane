"""Host-neutral adapters for durable, event-driven command completion.

Host process identifiers remain in adapter memory and are persisted only as a
digest by :mod:`taskplane.command_runtime`.  The model-facing boundary exposes
opaque Taskplane handles and canonical command events.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, Mapping, Protocol

try:
    import resource as _resource
except ImportError:  # pragma: no cover - Windows fail-closed path
    _resource = None

from taskplane.command_runtime import CommandRuntime, TERMINAL_STATES
from taskplane.review_session import (
    ReviewSessionError,
    sandbox_transport_binding,
    session_transport_binding,
)


SUPPORTED_HOSTS = frozenset({"claude", "codex"})

# Git aliases and external helpers make a denylist unsafe here: an apparently
# harmless, unknown subcommand can resolve to ``push`` (or arbitrary shell
# code) before the sandbox's disabled origin is consulted.  Validation only
# needs repository inspection, so admit named built-ins whose behavior is
# read-only and reject Git's global-option/configuration command layer.
_REVIEW_READ_ONLY_GIT_COMMANDS = frozenset({
    "branch", "cat-file", "check-attr", "check-ignore", "diff",
    "diff-files", "diff-index", "diff-tree", "for-each-ref", "grep",
    "log", "ls-files", "ls-tree", "merge-base", "name-rev", "rev-list",
    "rev-parse", "show", "show-ref", "status", "symbolic-ref",
})


class Launcher(Protocol):
    def __call__(self, command: object, cwd: str) -> "HostLaunch": ...


class IsolationLauncher(Protocol):
    """Trusted host boundary that confines a complete descendant process tree."""

    def __call__(self, command: object, cwd: str,
                 policy: Mapping[str, object]) -> "HostLaunch": ...


class NativeWait(Protocol):
    def __call__(self, binding: Mapping[str, object], timeout: float | None,
                 interrupted: Callable[[], bool] | None) -> Mapping | None: ...


@dataclass(frozen=True)
class HostLaunch:
    """Private host launch result used to bind an opaque runtime handle."""

    binding: Mapping[str, object]
    isolation: Mapping[str, object] | None = None


_SURFACE_ENV = {
    "side_panel": "TASKPLANE_SIDE_PANEL_COMMAND",
    "browser": "TASKPLANE_BROWSER_COMMAND",
    "hosting": "TASKPLANE_HOSTING_COMMAND",
}

_STARTUP_TIMEOUT_SECONDS = 0.15
_TEARDOWN_GRACE_SECONDS = 0.5
_PREVIEW_PROCESSES: dict[str, list[object]] = {}
_PREVIEW_PROCESS_LOCK = threading.Lock()


def _pid_start_identity(pid: int) -> str:
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        fields = proc_stat.read_text(encoding="utf-8").split()
        if len(fields) > 21:
            return f"linux-proc:{fields[21]}"
    if sys.platform == "darwin":
        library = ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
        libproc = ctypes.CDLL(library, use_errno=True)
        buffer = ctypes.create_string_buffer(256)
        size = int(libproc.proc_pidinfo(
            int(pid), 3, 0, ctypes.byref(buffer), ctypes.sizeof(buffer)))
        # proc_bsdinfo's immutable pbi_start_tvsec/usec occupy bytes
        # 120..135. Hashing the full struct would include mutable status and
        # create false PID-reuse alarms during ordinary lifecycle changes.
        if size >= 136:
            return "darwin-start:" + buffer.raw[120:136].hex()
    raise OSError("preview process start identity is unavailable")


def _process_identity(process: object, *, role: str,
                      generation: int = 1) -> dict:
    pid = int(process.pid)
    pgid = int(os.getpgid(pid))
    started = _pid_start_identity(pid)
    return {"schema": "taskplane.preview-process-ownership/v1",
            "pid": pid, "pgid": pgid, "started": started,
            "role": role, "generation": int(generation)}


def detached_process_binding(process: object, *, token: str) -> dict:
    """Create a reconnectable, PID-reuse-resistant local worker binding."""
    pid = int(process.pid)
    return {
        "schema": "taskplane.detached-command-binding/v1",
        "pid": pid,
        "pgid": int(os.getpgid(pid)),
        "started": _pid_start_identity(pid),
        "token": str(token),
    }


def detached_process_groups_supported() -> bool:
    """Whether this host can safely own and signal detached process groups."""
    return (os.name != "nt" and hasattr(os, "getpgid") and
            hasattr(os, "killpg") and hasattr(os, "setsid"))


def detached_process_is_live(binding: Mapping[str, object]) -> bool:
    """Return whether a durable local-worker binding still owns its process."""
    try:
        if (set(binding) != {"schema", "pid", "pgid", "started", "token"} or
                binding.get("schema") !=
                "taskplane.detached-command-binding/v1" or
                not str(binding.get("token") or "")):
            return False
        pid, pgid = int(binding["pid"]), int(binding["pgid"])
        if pid <= 0 or pgid <= 0 or os.getpgid(pid) != pgid:
            return False
        return _pid_start_identity(pid) == binding.get("started")
    except (KeyError, TypeError, ValueError, OSError,
            subprocess.SubprocessError):
        return False


def cancel_detached_process(binding: Mapping[str, object]) -> None:
    """Cancel only the still-live process group named by a durable binding."""
    if not detached_process_is_live(binding):
        raise OSError("detached command ownership no longer matches")
    pgid = int(binding["pgid"])
    os.killpg(pgid, signal.SIGTERM)


def _register_preview_process(preview_id: str, process: object, *,
                              role: str = "preview-command") -> dict:
    ownership = _process_identity(process, role=role)
    with _PREVIEW_PROCESS_LOCK:
        _PREVIEW_PROCESSES.setdefault(preview_id, []).append(process)
    return ownership


def _ownership_status(row: Mapping[str, object]) -> str:
    """Return live, absent, or unverifiable without signalling anything."""
    try:
        if row.get("schema") != "taskplane.preview-process-ownership/v1" or \
                str(row.get("role")) not in {"preview-command", "host-surface"} or \
                int(row.get("generation", 0)) <= 0:
            return "unverifiable"
        pid, pgid = int(row["pid"]), int(row["pgid"])
        if os.getpgid(pid) != pgid:
            return "unverifiable"
        started = _pid_start_identity(pid)
        return "live" if started and started == row.get("started") else "unverifiable"
    except ProcessLookupError:
        return "absent"
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        return "unverifiable"


def teardown_preview_processes(preview_id: str,
                               ownership: object = None) -> bool:
    """Rehydrate ownership and terminate only identity-verified process groups."""
    if not isinstance(ownership, list) or not ownership:
        return False
    with _PREVIEW_PROCESS_LOCK:
        processes = _PREVIEW_PROCESSES.pop(preview_id, [])
    cached = {int(process.pid): process for process in processes}
    for row in ownership:
        if not isinstance(row, Mapping):
            return False
        status = _ownership_status(row)
        if status == "absent":
            continue
        if status != "live":
            return False
        pid, pgid = int(row["pid"]), int(row["pgid"])
        process = cached.get(pid)
        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            return False
        if process is not None:
            try:
                process.wait(timeout=_TEARDOWN_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        else:
            deadline = time.monotonic() + _TEARDOWN_GRACE_SECONDS
            while (_ownership_status(row) == "live" and
                   time.monotonic() < deadline):
                time.sleep(0.01)
        if _ownership_status(row) == "live":
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                return False
            deadline = time.monotonic() + _TEARDOWN_GRACE_SECONDS
            while _ownership_status(row) == "live" and time.monotonic() < deadline:
                time.sleep(0.01)
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=0)
            except (subprocess.TimeoutExpired, OSError):
                pass
        if _ownership_status(row) != "absent":
            return False
    return True


def _require_live_startup(process, *, label: str) -> None:
    """Prove a child survived its bounded startup window before receipting it."""
    try:
        returncode = process.wait(timeout=_STARTUP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return
    output = b""
    try:
        captured = process.communicate(timeout=0)[0]
        output = bytes(captured or b"")
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    detail = output[-512:].decode("utf-8", errors="replace").strip()
    if "sandbox_apply" in detail:
        raise OSError(f"{label} could not apply isolation: {detail}")
    suffix = f": {detail}" if detail else ""
    raise OSError(f"{label} exited during startup ({returncode}){suffix}")


def native_surface_transport(surface: str, sandbox: str,
                             preview: Mapping[str, object]) -> Mapping[str, object]:
    """Invoke the configured host-native surface bridge without a shell."""
    env_name = _SURFACE_ENV.get(surface)
    configured = os.environ.get(env_name or "", "").strip()
    if not configured:
        raise OSError(f"native {surface} transport is unavailable")
    argv = shlex.split(configured)
    if not argv:
        raise OSError(f"native {surface} transport is invalid")
    process = subprocess.Popen(
        [*argv, "--workspace", sandbox, "--preview-id",
         str(preview["preview_id"])], cwd=sandbox,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    _require_live_startup(process, label=f"native {surface} transport")
    ownership = _register_preview_process(
        str(preview["preview_id"]), process, role="host-surface")
    return {"schema": "taskplane.host-preview-surface/v1",
            "surface": surface, "binding": f"pid:{process.pid}",
            "process_ownership": ownership}


def os_preview_isolation_launcher(command: object, cwd: str,
                                  policy: Mapping[str, object]) -> HostLaunch:
    """Launch a complete descendant tree under an OS-enforced preview policy."""
    if not isinstance(command, (list, tuple)) or not command:
        raise ValueError("preview isolation requires direct argv")
    root = Path(cwd).resolve()
    if (policy.get("network"), policy.get("scope"), policy.get("push"),
            policy.get("filesystem"), policy.get("source"),
            policy.get("remotes")) != (
                "deny", "complete-process-tree", "deny", "sandbox-only",
                "immutable", "disabled"):
        raise ValueError("preview isolation policy is incomplete")
    # Remote disabling is physical, not a promise in a receipt.
    if (root / ".git").exists():
        raise ValueError("preview sandbox contains repository remotes")
    if sys.platform != "darwin" or not os.path.isfile("/usr/bin/sandbox-exec"):
        raise OSError("complete preview process-tree isolation is unavailable")
    limits = dict(policy.get("limits") or {})
    if _resource is None or not hasattr(_resource, "RLIMIT_CPU") or not hasattr(
            _resource, "RLIMIT_AS"):
        raise OSError("preview CPU/memory enforcement is unavailable")
    try:
        cpu_limit = int(limits["cpu_seconds"])
        memory_limit = int(limits["memory_bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("preview CPU/memory limits are incomplete") from exc
    if cpu_limit <= 0 or memory_limit <= 0:
        raise ValueError("preview CPU/memory limits are invalid")

    def apply_resource_limits():
        _resource.setrlimit(_resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
        _resource.setrlimit(_resource.RLIMIT_AS, (memory_limit, memory_limit))
    escaped = str(root).replace("\\", "\\\\").replace('"', '\\"')
    profile = " ".join((("(version 1)"), "(deny default)",
                        '(import "system.sb")', "(allow process*)",
                        "(allow file-read*)",
                        f'(allow file-write* (subpath "{escaped}"))',
                        "(deny network*)"))
    process = subprocess.Popen(
        ["/usr/bin/sandbox-exec", "-p", profile, "--", *command], cwd=str(root),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True,
        preexec_fn=apply_resource_limits)
    # The receipt below describes observed enforcement, not policy intent.
    # Seatbelt failures and commands that immediately die are rejected before
    # the caller can persist a running handle or open a host surface.
    _require_live_startup(process, label="preview sandbox")
    preview_id = str(policy.get("preview_id") or "")
    if not preview_id:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        raise ValueError("preview isolation policy lacks preview identity")
    ownership = _register_preview_process(
        preview_id, process, role="preview-command")
    fingerprint = hashlib.sha256(json.dumps(
        dict(policy), sort_keys=True, separators=(",", ":"))
        .encode("utf-8")).hexdigest()
    return HostLaunch(binding={"pid": process.pid, "process_group": process.pid},
                      isolation={
                          "schema": "taskplane.preview-isolation-receipt/v1",
                          "network": "denied", "scope": "complete-process-tree",
                          "push": "denied", "filesystem": "sandbox-only",
                          "source": "immutable", "remotes": "disabled",
                          "cpu": "rlimit-enforced",
                          "memory": "rlimit-enforced",
                          "process_ownership": ownership,
                          "mechanism": "macos-seatbelt",
                          "policy_fingerprint": fingerprint})


_STATUS_MAP = {
    "completed": "succeeded",
    "complete": "succeeded",
    "success": "succeeded",
    "succeeded": "succeeded",
    "error": "failed",
    "failure": "failed",
    "failed": "failed",
    "timeout": "timed_out",
    "timed_out": "timed_out",
    "cancel": "cancelled",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "approval": "approval_required",
    "approval_needed": "approval_required",
    "approval_required": "approval_required",
    "authorization_required": "approval_required",
    "input": "input_required",
    "input_needed": "input_required",
    "input_required": "input_required",
    "milestone": "milestone",
}


def _fingerprint_command(command: object) -> str:
    encoded = json.dumps(command, sort_keys=True, separators=(",", ":"),
                         default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_state(event: Mapping) -> str:
    raw = event.get("state", event.get("status", event.get("type", "")))
    state = _STATUS_MAP.get(str(raw).strip().lower())
    if state is None:
        raise ValueError(f"unsupported host command state {raw!r}")
    return state


class CommandAdapter:
    """Normalize one host onto ``contract:runtime:host-command-adapter``.

    ``wait_next`` performs either one native host wait or one runtime-owned
    blocking wait.  It never asks the model to poll.  Delivery is receipted in
    the runtime before the canonical event is returned.
    """

    def __init__(self, *, host: str, runtime: CommandRuntime,
                 launcher: Launcher, native_wait: NativeWait | None = None,
                 canceller: Callable[[Mapping[str, object]], None] | None = None,
                 review_isolation_launcher: IsolationLauncher | None = None):
        if host not in SUPPORTED_HOSTS:
            raise ValueError(f"unsupported command host {host!r}")
        self.host = host
        self.runtime = runtime
        self._launcher = launcher
        self._native_wait = native_wait
        self._canceller = canceller
        self._review_isolation_launcher = review_isolation_launcher
        self._bindings: dict[str, Mapping[str, object]] = {}
        self._preview_ownership: dict[str, dict] = {}

    def launch(self, command: object, *, cwd: str,
               deadline: float | None = None,
               wave_id: str | None = None,
               identity: Mapping | None = None,
               review_session: Mapping | None = None,
               review_sandbox: Mapping | None = None) -> str:
        launched = self._launcher(command, cwd)
        if not isinstance(launched, HostLaunch) or not launched.binding:
            raise ValueError("host launch must return a non-empty binding")
        handle = self.runtime.create(
            command_fingerprint=_fingerprint_command(command),
            binding=launched.binding, deadline=deadline, wave_id=wave_id,
            identity=identity,
            review_session=review_session, review_sandbox=review_sandbox)
        self._bindings[handle] = dict(launched.binding)
        self.runtime.transition(handle, "running")
        return handle

    def preview_process_ownership(self, handle: str) -> dict:
        """Return verified ownership for durable preview binding."""
        row = dict(self._preview_ownership.get(handle) or {})
        if row.get("schema") != "taskplane.preview-process-ownership/v1":
            raise ValueError("preview process ownership is unavailable")
        return row

    def launch_review_validation(self, command: object, *, cwd: str,
                                 session: Mapping,
                                 sandbox: Mapping) -> str:
        """Launch only inside a disposable, push-disabled review copy."""
        try:
            sandbox_binding, workdir = sandbox_transport_binding(
                sandbox, cwd=cwd)
        except ReviewSessionError as exc:
            raise ValueError(str(exc)) from exc
        self._validate_push_disabled_command(command)
        if self._review_isolation_launcher is None:
            raise ValueError(
                "push-disabled review validation requires verified process "
                "and network isolation")
        policy = {
            "schema": "taskplane.review-isolation-policy/v1",
            "network": "deny",
            "scope": "complete-process-tree",
        }
        policy_fingerprint = hashlib.sha256(
            json.dumps(policy, sort_keys=True, separators=(",", ":"))
            .encode("utf-8")).hexdigest()
        launched = self._review_isolation_launcher(command, workdir, policy)
        if not isinstance(launched, HostLaunch) or not launched.binding:
            raise ValueError("isolated host launch must return a non-empty binding")
        isolation = dict(launched.isolation or {})
        if isolation.get("schema") != "taskplane.review-isolation-receipt/v1" or \
                isolation.get("network") != "denied" or \
                isolation.get("scope") != "complete-process-tree" or \
                isolation.get("policy_fingerprint") != policy_fingerprint or \
                not str(isolation.get("mechanism") or "").strip():
            raise ValueError(
                "push-disabled review validation requires a verified "
                "process-tree isolation receipt")
        sandbox_binding = dict(sandbox_binding)
        sandbox_binding["isolation_fingerprint"] = hashlib.sha256(
            json.dumps(isolation, sort_keys=True, separators=(",", ":"),
                       default=str).encode("utf-8")).hexdigest()
        handle = self.runtime.create(
            command_fingerprint=_fingerprint_command(command),
            binding=launched.binding,
            review_session=session_transport_binding(session),
            review_sandbox=sandbox_binding)
        self._bindings[handle] = dict(launched.binding)
        self.runtime.transition(handle, "running")
        return handle

    def launch_preview(self, command: object, *, cwd: str,
                       preview: Mapping) -> str:
        """Launch a registered preview using the verified isolation boundary.

        Preview commands share review validation's direct-argv/no-push rules,
        but additionally carry the preview pin in every durable event.
        """
        if self._review_isolation_launcher is None:
            raise ValueError("preview requires verified process-tree isolation")
        if preview.get("schema") != "taskplane.host-preview/v1" or \
                preview.get("state") not in {"registered", "open"} or \
                preview.get("push_disabled") is not True or \
                (preview.get("network") or {}).get("mode") != "deny":
            raise ValueError("preview registration is not executable")
        self._validate_push_disabled_command(command)
        workdir = os.path.realpath(cwd)
        workdir_fingerprint = hashlib.sha256(
            workdir.encode("utf-8")).hexdigest()
        if workdir_fingerprint != preview.get("sandbox_id"):
            raise ValueError("preview command cwd escapes registered sandbox")
        policy = {
            "schema": "taskplane.preview-isolation-policy/v1",
            "network": "deny", "scope": "complete-process-tree",
            "push": "deny", "filesystem": "sandbox-only",
            "source": "immutable", "remotes": "disabled",
            "sandbox_id": preview.get("sandbox_id"),
            "preview_id": preview.get("preview_id"),
            "limits": dict(preview.get("limits") or {}),
        }
        policy_fingerprint = hashlib.sha256(
            json.dumps(policy, sort_keys=True, separators=(",", ":"))
            .encode("utf-8")).hexdigest()
        launched = self._review_isolation_launcher(command, workdir, policy)
        if not isinstance(launched, HostLaunch) or not launched.binding:
            raise ValueError("isolated preview launch returned no binding")
        isolation = dict(launched.isolation or {})
        if (isolation.get("schema") not in {
                "taskplane.preview-isolation-receipt/v1",
                "taskplane.review-isolation-receipt/v1"} or
                isolation.get("network") != "denied" or
                isolation.get("scope") != "complete-process-tree" or
                isolation.get("push") != "denied" or
                isolation.get("filesystem") != "sandbox-only" or
                isolation.get("source") != "immutable" or
                isolation.get("remotes") != "disabled" or
                isolation.get("cpu") != "rlimit-enforced" or
                isolation.get("memory") != "rlimit-enforced" or
                isolation.get("policy_fingerprint") != policy_fingerprint or
                not str(isolation.get("mechanism") or "").strip()):
            raise ValueError("preview isolation receipt is invalid")
        handle = self.runtime.create(
            command_fingerprint=_fingerprint_command(command),
            binding=launched.binding, deadline=preview.get("deadline"),
            preview=preview)
        self._bindings[handle] = dict(launched.binding)
        self._preview_ownership[handle] = dict(
            isolation.get("process_ownership") or {})
        self.runtime.transition(handle, "running")
        return handle

    @staticmethod
    def _validate_push_disabled_command(command: object) -> None:
        """Reject argv forms that bypass a disabled remote or pre-push hook."""
        if not isinstance(command, (list, tuple)) or not command or any(
                not isinstance(item, (str, bytes)) for item in command):
            raise ValueError(
                "push-disabled review validation requires direct argv")
        argv = [item.decode() if isinstance(item, bytes) else item
                for item in command]
        executable = os.path.basename(argv[0]).lower()
        if executable in {"sh", "bash", "zsh", "dash", "fish", "cmd",
                          "cmd.exe", "powershell", "pwsh"}:
            raise ValueError(
                "push-disabled review validation forbids shell wrappers")
        if executable == "env":
            index = 1
            while index < len(argv) and ("=" in argv[index] or
                                          argv[index].startswith("-")):
                index += 1
            argv = argv[index:]
            executable = os.path.basename(argv[0]).lower() if argv else ""
        if executable in {"git", "git.exe"}:
            # The subcommand must be the first argument.  In particular,
            # reject ``-c alias.x=push x`` and config/env indirections rather
            # than attempting to interpret Git's extensible command grammar.
            subcommand = argv[1].lower() if len(argv) > 1 else ""
            if subcommand not in _REVIEW_READ_ONLY_GIT_COMMANDS:
                raise ValueError(
                    "push-disabled review validation permits only explicit "
                    "read-only git commands")

    def wait_review_event(self, handle: str, *, consumer: str,
                          interrupted: Callable[[], bool] | None = None,
                          timeout: float | None = None) -> dict | None:
        """Return a canonical event plus explicitly non-authoritative host data."""
        event = self.wait_next(
            handle, consumer=consumer, interrupted=interrupted,
            timeout=timeout)
        if event is None:
            return None
        canonical = {key: event.get(key) for key in (
            "schema", "revision", "state", "reason", "exit_code",
            "output_delta", "artifact", "review_session")}
        return {
            "schema": "taskplane.review-host-event/v1",
            "transport": {"host": self.host},
            "event": canonical,
        }

    def notify(self, handle: str, event: Mapping) -> dict:
        """Ingest one native notification into the canonical lifecycle."""
        state = _canonical_state(event)
        output = event.get("output", event.get("output_delta"))
        if output:
            self.runtime.append_output(handle, str(output))
        reason = event.get("reason", event.get("message"))
        exit_code = event.get("exit_code", event.get("returncode"))
        if exit_code is not None:
            try:
                exit_code = int(exit_code)
            except (TypeError, ValueError) as exc:
                raise ValueError("host exit code must be an integer") from exc
        return self.runtime.transition(
            handle, state, exit_code=exit_code,
            reason=str(reason) if reason is not None else None)

    def _receive(self, handle: str, consumer: str,
                 candidate: dict | None) -> dict | None:
        if candidate is None:
            return None
        return self.runtime.receive(
            handle, consumer=consumer,
            delivery_key=candidate["delivery_key"])

    def wait_next(self, handle: str, *, consumer: str,
                  interrupted: Callable[[], bool] | None = None,
                  timeout: float | None = None) -> dict | None:
        if self._native_wait is not None:
            binding = self._bindings.get(handle)
            if binding is None:
                self.runtime.reconnect(handle, binding=None)
            else:
                try:
                    native_event = self._native_wait(
                        binding, timeout, interrupted)
                except OSError as exc:
                    consolidated = os.environ.get(
                        "TASKPLANE_CONSOLIDATED_FLOW", "").strip().lower() in {
                            "1", "true", "yes", "on"}
                    if not consolidated:
                        raise
                    decision = self.runtime.record_recovery(
                        handle, failure_class="transient",
                        detail=f"{exc.__class__.__name__}: {exc}")
                    if decision["status"] == "recover":
                        return None
                    self.runtime.transition(
                        handle, "input_required",
                        reason=("automatic command recovery stopped: "
                                f"{decision['reason']}"))
                    return self._receive(
                        handle, consumer,
                        self.runtime.pending(handle, consumer=consumer))
                if native_event is not None:
                    self.notify(handle, native_event)
            return self._receive(
                handle, consumer,
                self.runtime.pending(handle, consumer=consumer))
        candidate = self.runtime.wait_next(
            handle, consumer=consumer, interrupted=interrupted,
            timeout=timeout)
        return self._receive(handle, consumer, candidate)

    def cancel(self, handle: str) -> dict:
        snapshot = self.runtime.snapshot(handle)
        if snapshot["state"] not in TERMINAL_STATES and self._canceller:
            binding = self._bindings.get(handle)
            ownership_already_lost = (
                snapshot["state"] == "input_required" and
                snapshot.get("reason") == "detached_worker_ownership_lost")
            if binding is not None and not ownership_already_lost:
                binding_digest = hashlib.sha256(json.dumps(
                    binding, sort_keys=True, separators=(",", ":"),
                    default=str).encode("utf-8")).hexdigest()
                if binding_digest != snapshot.get("binding_digest"):
                    raise OSError(
                        "detached command control binding does not match "
                        "immutable runtime ownership")
                if (binding.get("schema") ==
                        "taskplane.detached-command-binding/v1" and
                        not detached_process_is_live(binding)):
                    raise OSError(
                        "detached command process ownership no longer matches")
                self._canceller(binding)
        return self.runtime.cancel(handle)

    def reconnect(self, handle: str,
                  *, binding: Mapping[str, object] | None = None,
                  ownership_check: Callable[[Mapping[str, object]], bool] |
                  None = None) -> dict:
        effective = binding or self._bindings.get(handle)
        result = self.runtime.reconnect(
            handle, binding=effective, ownership_check=ownership_check)
        if binding is not None and result.get("state") != "failed":
            self._bindings[handle] = dict(binding)
        return result

    def restore_binding(self, handle: str,
                        binding: Mapping[str, object]) -> None:
        """Restore host-private binding loaded by a trusted composition root."""
        if not binding:
            raise ValueError("restored command binding must be non-empty")
        self._bindings[str(handle)] = dict(binding)

    def snapshot(self, handle: str) -> dict:
        return self.runtime.snapshot(handle)
