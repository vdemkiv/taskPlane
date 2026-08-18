"""Host-neutral adapters for durable, event-driven command completion.

Host process identifiers remain in adapter memory and are persisted only as a
digest by :mod:`taskplane.command_runtime`.  The model-facing boundary exposes
opaque Taskplane handles and canonical command events.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Callable, Mapping, Protocol

from taskplane.command_runtime import CommandRuntime, TERMINAL_STATES
from taskplane.review_session import session_transport_binding


SUPPORTED_HOSTS = frozenset({"claude", "codex"})


class Launcher(Protocol):
    def __call__(self, command: object, cwd: str) -> "HostLaunch": ...


class NativeWait(Protocol):
    def __call__(self, binding: Mapping[str, object], timeout: float | None,
                 interrupted: Callable[[], bool] | None) -> Mapping | None: ...


@dataclass(frozen=True)
class HostLaunch:
    """Private host launch result used to bind an opaque runtime handle."""

    binding: Mapping[str, object]


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
                 canceller: Callable[[Mapping[str, object]], None] | None = None):
        if host not in SUPPORTED_HOSTS:
            raise ValueError(f"unsupported command host {host!r}")
        self.host = host
        self.runtime = runtime
        self._launcher = launcher
        self._native_wait = native_wait
        self._canceller = canceller
        self._bindings: dict[str, Mapping[str, object]] = {}

    def launch(self, command: object, *, cwd: str,
               deadline: float | None = None,
               wave_id: str | None = None,
               review_session: Mapping | None = None) -> str:
        launched = self._launcher(command, cwd)
        if not isinstance(launched, HostLaunch) or not launched.binding:
            raise ValueError("host launch must return a non-empty binding")
        handle = self.runtime.create(
            command_fingerprint=_fingerprint_command(command),
            binding=launched.binding, deadline=deadline, wave_id=wave_id,
            review_session=review_session)
        self._bindings[handle] = dict(launched.binding)
        self.runtime.transition(handle, "running")
        return handle

    def launch_review_validation(self, command: object, *, cwd: str,
                                 session: Mapping,
                                 sandbox: Mapping) -> str:
        """Launch only inside a disposable, push-disabled review copy."""
        if sandbox.get("disposable") is not True or \
                sandbox.get("push_disabled") is not True or \
                not str(sandbox.get("sandbox_id") or "").strip():
            raise ValueError(
                "review validation requires a disposable push-disabled sandbox")
        return self.launch(
            command, cwd=cwd,
            review_session=session_transport_binding(session))

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
                native_event = self._native_wait(binding, timeout, interrupted)
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
            if binding is not None:
                self._canceller(binding)
        return self.runtime.cancel(handle)

    def reconnect(self, handle: str,
                  *, binding: Mapping[str, object] | None = None) -> dict:
        effective = binding or self._bindings.get(handle)
        result = self.runtime.reconnect(handle, binding=effective)
        if binding is not None and result.get("state") != "failed":
            self._bindings[handle] = dict(binding)
        return result

    def snapshot(self, handle: str) -> dict:
        return self.runtime.snapshot(handle)
