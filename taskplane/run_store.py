"""Atomic, revision-checked owner for one taskPlane run."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
import time
import uuid

import storage
import taskplane_lite as tp


class RunStoreError(RuntimeError):
    pass


class RunStoreBusy(RunStoreError):
    pass


class RevisionConflict(RunStoreError):
    pass


def _atomic_write_json(path: str, value: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    try:
        with open(temporary, "w", encoding="utf-8", newline="") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


@contextmanager
def _lock(path: str):
    try:
        with tp.file_lock(path, timeout=10.0):
            yield
    except tp.StateError as exc:
        raise RunStoreBusy(f"run manifest lock is unavailable: {exc}") \
            from None


def _merge(current: dict, changes: dict) -> dict:
    merged = copy.deepcopy(current)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


class RunStore:
    """Persist canonical run identity, state, and artifact ownership."""

    def __init__(self, *, home: str | None = None):
        self.home = storage.taskplane_home(home)

    def _manifest_path(self, run_id: str) -> str:
        return os.path.join(self.home, "runs", str(run_id), "manifest.json")

    def _journal_path(self, run_id: str) -> str:
        return os.path.join(self.home, "runs", str(run_id), "journal.jsonl")

    def _append_journal(self, run_id: str, event: dict) -> None:
        path = self._journal_path(run_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(event, sort_keys=True,
                                    separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def create(self, identity: storage.RepositoryIdentity, *, run_id: str,
               checkout: str, host: dict, target: dict) -> dict:
        layout = storage.resolve_layout(identity, home=self.home,
                                        run_id=run_id)
        path = self._manifest_path(run_id)
        os.makedirs(layout.run_root, exist_ok=True)
        with _lock(path):
            if os.path.exists(path):
                raise RunStoreError(f"run already exists: {run_id}")
            repository = identity.to_dict()
            repository["checkout"] = os.path.realpath(checkout)
            manifest = {
                "schema": "taskplane.run/v3",
                "run_id": str(run_id),
                "revision": 1,
                "status": "preflight",
                "repository": repository,
                "target": copy.deepcopy(target),
                "host": copy.deepcopy(host),
                "preflight": {"status": "pending", "completed_steps": [],
                              "pending_action": None},
                "contract": {"status": "inactive", "task_id": None},
                "paths": {
                    "state": layout.state_root,
                    "graph": layout.graph_root,
                    "evidence": layout.evidence_root,
                    "lenses": layout.lens_root,
                    "artifacts": layout.artifact_root,
                },
            }
            _atomic_write_json(path, manifest)
            _atomic_write_json(layout.repository_record, {
                "schema": "taskplane.repository/v1",
                "repository": identity.to_dict(),
                "repository_key": identity.key,
            })
            self._append_journal(run_id, {
                "event": "run_created", "revision": 1,
                "status": "preflight", "at": int(time.time())})
            return manifest

    def load(self, run_id: str) -> dict:
        path = self._manifest_path(run_id)
        try:
            with open(path, encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RunStoreError(f"run manifest is unavailable: {run_id}") \
                from exc
        if not isinstance(value, dict) or value.get("schema") != \
                "taskplane.run/v3":
            raise RunStoreError(f"run manifest is invalid: {run_id}")
        return value

    def commit(self, run_id: str, *, expected_revision: int,
               changes: dict) -> dict:
        path = self._manifest_path(run_id)
        with _lock(path):
            current = self.load(run_id)
            actual = int(current.get("revision") or 0)
            if actual != int(expected_revision):
                raise RevisionConflict(
                    f"run {run_id} revision is {actual}, expected "
                    f"{expected_revision}")
            updated = _merge(current, changes)
            updated["revision"] = actual + 1
            _atomic_write_json(path, updated)
            self._append_journal(run_id, {
                "event": "run_committed", "revision": updated["revision"],
                "status": updated.get("status"), "at": int(time.time())})
            return updated

    def register_checkout(self, identity: storage.RepositoryIdentity, *,
                          checkout: str, source: str) -> dict:
        """Register a non-authoritative checkout alias without moving it."""
        layout = storage.resolve_layout(
            identity, home=self.home, run_id="checkout-registration")
        path = layout.repository_record
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _lock(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    record = json.load(handle)
            except FileNotFoundError:
                record = {
                    "schema": "taskplane.repository/v1",
                    "repository": identity.to_dict(),
                    "repository_key": identity.key, "checkouts": []}
            except (OSError, ValueError) as exc:
                raise RunStoreError(
                    f"repository record is unavailable: {path}") from exc
            if record.get("schema") != "taskplane.repository/v1" or \
                    (record.get("repository") or {}).get("repo_id") != \
                    identity.repo_id:
                raise RunStoreError("repository record identity mismatch")
            rows = list(record.get("checkouts") or [])
            row = {"path": os.path.realpath(checkout),
                   "source": str(source)}
            if row not in rows:
                rows.append(row)
            record["checkouts"] = sorted(rows, key=lambda item: (
                str(item.get("path")), str(item.get("source"))))
            _atomic_write_json(path, record)
            return record

    def reference_command(self, run_id: str, *, expected_revision: int,
                          handle: str,
                          wave_id: str | None = None) -> dict:
        """Revision-check and retain opaque command/wave references.

        The run manifest never stores argv, environment, output, host process
        identifiers, or authorization material. Those remain owned by the
        command runtime's bound record.
        """
        current = self.load(run_id)
        commands = copy.deepcopy(current.get("commands") or {
            "handles": [], "waves": [],
        })
        handles = list(commands.get("handles") or [])
        if str(handle) not in handles:
            handles.append(str(handle))
        waves = list(commands.get("waves") or [])
        if wave_id is not None and str(wave_id) not in waves:
            waves.append(str(wave_id))
        return self.commit(run_id, expected_revision=expected_revision,
                           changes={"commands": {
                               "handles": handles, "waves": waves,
                           }})
