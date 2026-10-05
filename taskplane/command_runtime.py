"""Native handle lifecycle restored from 38c1d8b command_runtime.

Records retain bindings and states, never command/output bodies. Cancellation
revokes input first; only the host's actual process observation can prove exit.
The supplied observer and pre-provisioned store belong to the trusted host.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping, Protocol

from . import primitives, storage, workflow as w

TERMINAL = {"completed", "failed", "cancelled"}
STATES = {"running", "input_required", "cancelling"} | TERMINAL
BINDINGS = ("workspace", "root", "run", "visit", "revision")
RECOVERY_NODES = 4096
RECOVERY_BYTES = 32 * 1024 * 1024


def _retained_references(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Inventory reference-bearing evidence, not graph snapshots or scalar state."""
    from .context import REFERENCE_SCHEMA
    values: list[Any] = list(state.get("task_results", {}).values())
    values.append(state.get("inherited_findings", {}))
    for stage in [*state.get("visits", []), *state.get("history", [])]:
        packet = stage.get("packet") or {}
        values.extend([packet.get("output", {}), packet.get("finding_evidence", {}),
                       packet.get("verification", {})])
    for worker in state.get("workers", {}).values():
        values.extend([worker.get("context_receipt", {}), worker.get("launch_proof_ref", {}),
                       worker.get("launch_proof_refs", [])])
    refs: list[dict[str, Any]] = []
    examined = 0
    while values:
        value = values.pop()
        examined += 1
        w.require(examined <= 20000, "context_overflow", "Recovery evidence inventory exceeded its bound.")
        if isinstance(value, dict):
            if value.get("schema") == REFERENCE_SCHEMA:
                refs.append(value)
                w.require(len(refs) <= 4096, "context_overflow", "Recovery reference inventory exceeded its bound.")
            else:
                values.extend(child for child in value.values() if isinstance(child, (dict, list)))
        elif isinstance(value, list):
            values.extend(child for child in value if isinstance(child, (dict, list)))
    return refs


def _recovery_result(workspace: Path, state: dict[str, Any], reference: str) -> bytes:
    from .context import Store, encode
    from .context_handoff import binding, current_read_binding
    store = Store(workspace)
    store.path(reference)  # Reject invalid selectors before inventory or I/O.
    roots = store.roots(binding(state))

    def exact(refs: list[dict[str, Any]]) -> bytes | None:
        for ref in refs:
            if ref.get("sha256") == reference:
                store.node(ref)  # Check the registered metadata as well as bytes.
                return store._bytes(reference)
        return None

    raw = exact(roots)
    if raw is not None:
        return raw
    # Official compact output registers under the current source-key binding.
    # Recompute source freshness without building a view or its delivery trees.
    # Do not enumerate old read-index files or accept another source generation.
    try:
        current = current_read_binding(workspace, state)
    except w.Refusal:
        current = None  # Retained sealed evidence remains independently readable.
    if current is not None:
        registered = store.roots(current)
        raw = exact(registered)
        if raw is not None:
            return raw
        roots.extend(registered)
    retained = _retained_references(state)
    raw = exact(retained)
    if raw is not None:
        return raw
    # A child can be inspected too, but unrelated evidence cannot consume an
    # unbounded amount of memory or I/O while establishing reachability.
    pending = [(ref, 0) for ref in [*roots, *retained]]
    visited: set[str] = set()
    remaining_bytes = RECOVERY_BYTES
    while pending:
        ref, depth = pending.pop()
        key = ref.get("sha256", "")
        if key in visited:
            continue
        w.require(len(visited) < RECOVERY_NODES and depth <= 32 and remaining_bytes > 0,
                  "context_overflow", "Recovery result reachability exceeded its bound.")
        node = store.node(ref)
        remaining_bytes -= len(encode(node))
        w.require(remaining_bytes >= 0, "context_overflow", "Recovery result bytes exceeded their bound.")
        visited.add(key)
        if key == reference:
            return store._bytes(reference)
        children = store.children(node)
        # Inspect the selected child next; every opened node retains the same
        # depth, object, byte and metadata checks as the rest of the traversal.
        pending.extend((child, depth + 1) for child in
                       sorted(children, key=lambda child: child.get("sha256") == reference))
    raise w.Refusal("scope_violation", "Result is not registered or reachable for this run; use its current report references.")


def inspect_recovery(workspace: Path, state: dict[str, Any], kind: str,
                     reference: str, offset: int = 0, limit: int = 32768) -> dict[str, Any]:
    """Read installed contracts or reachable result nodes without publishing state."""
    w.require(type(offset) is int and type(limit) is int and offset >= 0 and 0 < limit <= 32768,
              "invalid_evidence", "Inspection needs a nonnegative byte offset and 1-32768 byte limit.")
    if kind == "contract":
        contracts = {"cli-reference": "docs/cli-reference.md",
                     "shared-flow": "skills/tp-go/references/shared-flow.md"}
        w.require(reference in contracts, "scope_violation", "Choose cli-reference or shared-flow.")
        target = Path(__file__).resolve().parents[1] / contracts[reference]
        w.require(target.resolve() == target and not target.is_symlink(), "scope_violation", "Inspection cannot follow a symlink.")
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            w.require(stat.S_ISREG(info.st_mode) and info.st_size <= 1024 * 1024,
                      "invalid_evidence", "Contract inspection requires a regular file of at most 1 MiB.")
            raw = stream.read(1024 * 1024 + 1)
            w.require(len(raw) <= 1024 * 1024, "invalid_evidence", "Contract grew beyond its read bound.")
    elif kind == "result":
        raw = _recovery_result(workspace, state, reference)
    else:
        raise w.Refusal("invalid_evidence", "Inspection kind must be contract or result.")
    w.require(offset <= len(raw), "invalid_evidence", "Inspection byte offset is beyond the result.")
    selected = raw[offset:offset + limit]
    return {"schema": "taskplane.recovery-read/v1", "kind": kind, "reference": reference,
            "offset": offset, "returned_bytes": len(selected), "total_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "text": selected.decode("utf-8", errors="replace"),
            "next_offset": offset + len(selected) if offset + len(selected) < len(raw) else None,
            "authority": "none", "untrusted_data": True}


class ProcessObserver(Protocol):
    def command_for(self, event: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def read_process(self, handle: str) -> Mapping[str, Any]: ...
    def process_census(self, root: str, run: str) -> Mapping[str, Any]: ...
    def cancel_process(self, handle: str, binding: Mapping[str, Any]) -> None: ...


def grant(state: Mapping[str, Any]) -> dict[str, Any]:
    return {"workspace": state["workspace"], "root": state["root"], "run": state["run"],
            "visit": w.current(dict(state))["id"], "revision": state["revision"]}


class CommandRuntime:
    def __init__(self, path: Path, *, workspace: Path, root: str, observer: ProcessObserver):
        self.workspace, self.root, self.observer = workspace.resolve(), root, observer
        self.path = storage.control_file(self.workspace, path)

    def validate(self) -> None:
        with primitives.file_lock(str(self.path)):
            self._read()

    def _read(self) -> dict[str, Any]:
        storage.control_file(self.workspace, self.path)
        fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            w.require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "state_unavailable", "Command store is not regular.")
            raw = stream.read(2 * 1024 * 1024 + 1)
        w.require(len(raw) <= 2 * 1024 * 1024, "state_unavailable", "Command store exceeds its bound.")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise w.Refusal("state_unavailable", "Command store is corrupt; it cannot be reset.") from None
        w.require(isinstance(value, dict) and value.get("schema") == "taskplane.native-commands/v1"
                  and value.get("workspace") == str(self.workspace) and value.get("root") == self.root
                  and isinstance(value.get("handles"), dict) and len(value["handles"]) <= 4096,
                  "state_unavailable", "Command store identity or schema is invalid.")
        assert isinstance(value, dict)
        for handle, record in value["handles"].items():
            w.require(isinstance(handle, str) and isinstance(record, dict)
                      and record.get("state") in STATES and type(record.get("revision")) is int
                      and record["revision"] >= 0 and type(record.get("revoked")) is bool
                      and isinstance(record.get("process_start"), str) and bool(record["process_start"])
                      and type(record.get("tree_quiescent")) is bool,
                      "state_unavailable", "Invalid command lifecycle record.")
            self._binding(record.get("binding"))
        return value

    def _write(self, value: dict[str, Any]) -> None:
        storage.control_file(self.workspace, self.path)
        primitives.atomic_json(self.path, value, strict_directory_sync=True)

    def _binding(self, value: Any) -> None:
        w.require(isinstance(value, dict) and set(value) == set(BINDINGS)
                  and value.get("workspace") == str(self.workspace) and value.get("root") == self.root
                  and all(isinstance(value.get(k), str) and value[k] for k in ("run", "visit"))
                  and type(value.get("revision")) is int and value["revision"] >= 0,
                  "scope_violation", "Native process grant is missing or belongs to another root.")

    def _observe(self, handle: str, binding: dict[str, Any]) -> dict[str, Any]:
        self._binding(binding)
        try:
            value = self.observer.read_process(handle)
        except (OSError, ValueError, KeyError, TypeError):
            raise w.Refusal("scope_violation", "Actual native process ownership is unavailable.") from None
        w.require(isinstance(value, Mapping) and value.get("handle") == handle
                  and value.get("binding") == binding and value.get("state") in STATES
                  and isinstance(value.get("process_start"), str) and bool(value["process_start"])
                  and type(value.get("tree_quiescent")) is bool,
                  "scope_violation", "Native process observation is incomplete or has a different grant.")
        self._binding(value.get("binding"))
        return {k: deepcopy(value[k]) for k in ("binding", "state", "process_start", "tree_quiescent")}

    def _refresh(self, record: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
        w.require(record["binding"] == observed["binding"] and record["process_start"] == observed["process_start"],
                  "scope_violation", "Native handle was reused or its process identity changed.")
        if record["state"] in TERMINAL:
            w.require(record["state"] == observed["state"], "scope_violation", "A terminal command cannot reopen.")
        result = {**record, **observed}
        if result != record:
            result["revision"] += 1
        return result

    def create(self, handle: str, binding: dict[str, Any]) -> dict[str, Any]:
        w.require(isinstance(handle, str) and 0 < len(handle) <= 512 and "\0" not in handle,
                  "scope_violation", "Native handle is invalid.")
        with primitives.file_lock(str(self.path)):
            db = self._read(); observed = self._observe(handle, binding)
            previous = db["handles"].get(handle)
            if previous is None:
                w.require(len(db["handles"]) < 4096, "state_unavailable", "Native handle store is full.")
                record = {**observed, "revision": 0, "revoked": False}
            else:
                record = self._refresh(previous, observed)
            db["handles"][handle] = record
            self._write(db)
            return deepcopy(record)

    def snapshot(self, handle: str) -> dict[str, Any]:
        with primitives.file_lock(str(self.path)):
            db = self._read()
            w.require(handle in db["handles"], "scope_violation", "Native handle is unknown.")
            record = db["handles"][handle]
            updated = self._refresh(record, self._observe(handle, record["binding"]))
            if updated != record:
                db["handles"][handle] = updated; self._write(db)
            return deepcopy(updated)

    def reconnect(self, handle: str, binding: dict[str, Any]) -> dict[str, Any]:
        record = self.snapshot(handle)
        w.require(record["binding"] == binding and not record["revoked"] and record["state"] == "running",
                  "scope_violation", "Input cannot reuse an expired, interrupted or foreign process grant.")
        return record

    def cancel(self, handle: str, *, expected_revision: int) -> dict[str, Any]:
        with primitives.file_lock(str(self.path)):
            db = self._read()
            w.require(handle in db["handles"], "scope_violation", "Native handle is unknown.")
            record = db["handles"][handle]
            w.require(type(expected_revision) is int and record["revision"] == expected_revision,
                      "stale_checkpoint", "Command revision changed before cancellation.")
            observed = self._observe(handle, record["binding"])
            updated = self._refresh(record, observed)
            if updated["state"] not in TERMINAL:
                updated.update(state="cancelling", tree_quiescent=False)
            updated.update(revoked=True, revision=record["revision"] + 1)
            db["handles"][handle] = updated
            self._write(db)  # Revoke stdin before requesting an external effect.
        if observed["state"] not in TERMINAL:
            self.observer.cancel_process(handle, deepcopy(record["binding"]))
        return self.snapshot(handle)  # Do not manufacture a terminal result.

    def quiescent(self, run: str) -> bool:
        try:
            census = self.observer.process_census(self.root, run)
            if (census.get("complete") is not True or census.get("root") != self.root
                    or census.get("run") != run or census.get("active_handles") != []):
                return False
            with primitives.file_lock(str(self.path)):
                handles = [h for h, r in self._read()["handles"].items() if r["binding"]["run"] == run]
            for handle in handles:
                record = self.snapshot(handle)
                if record["state"] not in TERMINAL or record["tree_quiescent"] is not True:
                    return False
            return True
        except (w.Refusal, OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
