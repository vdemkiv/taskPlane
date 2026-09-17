"""Native session binding restored from 38c1d8b authority/host_native.

The boundary object belongs to the native host, outside worker authority. There
is deliberately no CLI, environment, workspace-file or global registration path
for it. Installed profiles have no such owner and stay unverified. These checks
validate bindings; they cannot establish the truth of an injected test boundary.
"""
from __future__ import annotations

from copy import deepcopy
import ctypes
import ctypes.util
from pathlib import Path
import sys
from typing import Any, Mapping, Protocol

from . import host_capabilities as caps, storage, primitives, workflow as w


class NativeBoundary(Protocol):
    """Host-owned operations; worker data must never implement this interface."""

    def identity(self) -> Mapping[str, Any]: ...
    def capabilities(self) -> Mapping[str, Any]: ...
    def control_path(self) -> Path: ...
    def read_event(self, reference: str) -> Mapping[str, Any]: ...
    def read_scope(self, reference: str) -> Mapping[str, Any]: ...


def process_start_identity(pid: int) -> str:
    """OS process-start identity, never a claim of ownership or termination."""
    if type(pid) is not int or pid <= 0:
        raise OSError("Process identity is invalid")
    if sys.platform.startswith("linux"):
        # comm may contain spaces or ')'; field 22 is 19 fields after comm.
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text.rpartition(") ")[2].split()
        if len(fields) > 19 and fields[19].isdigit():
            return "linux-proc:" + fields[19]
    if sys.platform == "darwin":
        libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib", use_errno=True)
        buffer = ctypes.create_string_buffer(256)
        size = int(libproc.proc_pidinfo(pid, 3, 0, ctypes.byref(buffer), ctypes.sizeof(buffer)))
        if size >= 136 and any(buffer.raw[120:136]):
            return "darwin-start:" + buffer.raw[120:136].hex()
    raise OSError("Process start identity is unavailable")


class NativeSession:
    def __init__(self, owner: NativeBoundary, *, host: str, version: str,
                 workspace: Path, root: str):
        w.require(host in {"codex", "claude"} and bool(version) and bool(root),
                  "unsupported_authority", "A complete native session binding is required.")
        self.owner = owner
        self.workspace = workspace.resolve()
        self.binding = {"host": host, "version": version, "workspace": str(self.workspace), "root": root}

    def require_current(self) -> None:
        try:
            valid = dict(self.owner.identity()) == self.binding
            available = self.owner.capabilities()
            valid = valid and all(available.get(k) is True for k in caps.CAPABILITIES)
        except (OSError, ValueError, TypeError, AttributeError):
            valid = False
        w.require(valid, "unsupported_authority", "Native session or host protection is missing, changed or revoked.")

    def capabilities(self) -> dict[str, Any]:
        try:
            self.require_current()
            storage.control_file(self.workspace, self.owner.control_path())
        except (w.Refusal, OSError, ValueError, TypeError):
            return {k: False for k in caps.CAPABILITIES}
        return {k: True for k in caps.CAPABILITIES}

    def control_path(self, workspace: Path, root: str) -> Path:
        self.require_current()
        w.require(workspace.resolve() == self.workspace and root == self.binding["root"],
                  "unsupported_authority", "Native control request belongs to another session.")
        return storage.control_file(self.workspace, self.owner.control_path())

    def _reference(self, reference: str) -> None:
        self.require_current()
        w.require(isinstance(reference, str) and 0 < len(reference) <= 2048 and "\0" not in reference,
                  "unsupported_authority", "An opaque native event reference is required.")

    def verify_decision(self, reference: str, expected: dict[str, Any], *,
                        prior_decisions: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._reference(reference)
        try:
            value = self.owner.read_event(reference)
        except (OSError, KeyError, TypeError, ValueError):
            raise w.Refusal("unsupported_authority", "Native decision reference is unavailable.") from None
        w.require(isinstance(value, Mapping) and value.get("reference") == reference
                  and value.get("session") == self.binding and value.get("origin") == "human"
                  and value.get("automatic") is False and value.get("resolved") is True,
                  "unsupported_authority", "Native event is not an independently resolved human decision.")
        event_id, choice = value.get("event_id"), value.get("choice")
        w.require(isinstance(event_id, str) and bool(event_id)
                  and choice in {"approved", "changes_requested", "rejected", "cancelled"},
                  "unsupported_authority", "Native event has no explicit human decision identity.")
        assert isinstance(event_id, str)
        w.require(expected.get("workspace") == str(self.workspace) and expected.get("root") == self.binding["root"],
                  "stale_checkpoint", "Native decision belongs to a different root.")
        candidate = {"event_id": event_id, "human": True, "automatic": False,
                     "choice": choice, "binding": deepcopy(value.get("binding"))}
        if prior_decisions is not None and event_id in prior_decisions:
            w.require(primitives.content_fingerprint(candidate) == primitives.content_fingerprint(prior_decisions[event_id]),
                      "stale_checkpoint", "Conflicting native event replay.")
        else:
            w.require(primitives.content_fingerprint(value.get("binding")) == primitives.content_fingerprint(expected),
                      "stale_checkpoint", "Native decision belongs to a different checkpoint.")
        self.require_current()  # Revocation during the native read cannot grant approval.
        return candidate

    def verify_start(self, request: dict[str, Any]) -> dict[str, Any]:
        reference = request.get("native_reference", "")
        self._reference(reference)
        try:
            value = self.owner.read_scope(reference)
        except (OSError, KeyError, TypeError, ValueError):
            raise w.Refusal("unsupported_authority", "Native scope reference is unavailable.") from None
        w.require(isinstance(value, Mapping) and value.get("reference") == reference
                  and value.get("session") == self.binding and value.get("origin") == "human"
                  and value.get("automatic") is False and value.get("resolved") is True,
                  "unsupported_authority", "Native start scope has no independently resolved human origin.")
        result = value.get("authorization")
        w.require(isinstance(result, dict) and result.get("entry") == request.get("entry", "product")
                  and type(result.get("standalone")) is bool
                  and result["standalone"] == request.get("standalone", False),
                  "scope_violation", "Requested entry differs from the native human scope.")
        self.require_current()
        assert isinstance(result, dict)
        return deepcopy(result)
