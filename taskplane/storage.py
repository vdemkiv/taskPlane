"""Workspace-local storage for graph data and delivery observations."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import stat
import json
from contextlib import nullcontext
from copy import deepcopy
from typing import Any, Mapping

if __package__:
    from . import primitives as _package_primitives
    primitives = _package_primitives
else:
    import primitives as _flat_primitives
    primitives = _flat_primitives


class StorageConflict(RuntimeError):
    """A stale proposed controller cannot replace a newer generation."""

    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(f"Controller storage generation changed: expected {expected}, found {actual}")


def storage_generation(database: Mapping[str, Any]) -> int:
    """Read legacy state as generation zero without modifying its bytes/value."""
    value = database.get("storage_generation", 0)
    if type(value) is not int or value < 0:
        raise ValueError("Controller storage_generation must be a nonnegative integer")
    return value


def encode_controller(database: Mapping[str, Any]) -> bytes:
    """Use one complete persisted representation for capacity and publication.

    Readers still accept legacy indented JSON. Compact whitespace removes no
    domain records and does not change evidence stored inside the controller.
    """
    return (json.dumps(database, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def commit_controller(path: Path, database: Mapping[str, Any], *, expected_generation: int,
                      locked: bool = False, limit: int = 16 * 1024 * 1024) -> int:
    """CAS a complete controller, keeping storage generation separate from revision.

    Callers validate the proposed domain transition first. ``locked=True`` is
    only for an in-process caller already holding ``file_lock(str(path))`` from
    its current read through this commit; it is never a request-level option.
    Otherwise this function acquires that exact lock itself. Every authoritative
    writer must use this API, including observations and retained rejections.

    The supplied mapping is never changed. The returned committed generation
    must be used for a subsequent commit. An unchanged proposal does not rewrite
    legacy bytes or advance any generation. After an I/O error, reload: atomic
    publication may have succeeded before directory sync failed.
    """
    if type(expected_generation) is not int or expected_generation < 0:
        raise ValueError("Expected storage generation must be a nonnegative integer")
    if type(limit) is not int or limit <= 0:
        raise ValueError("Controller byte limit must be a positive integer")
    proposed = deepcopy(dict(database))
    if storage_generation(proposed) != expected_generation:
        raise ValueError("Proposed controller does not carry the expected storage generation")
    # Validate even an unchanged proposal. JSON's exact scalar representation
    # avoids treating a changed boolean/integer field as a no-op via Python ==.
    original_encoding = encode_controller(proposed)
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Controller storage cannot follow symlinks")
    with nullcontext() if locked else primitives.file_lock(str(path)):
        current = None
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError:
            pass
        else:
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Controller storage must be a regular file")
                raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("Controller exceeds its byte limit")
            current = json.loads(raw)
            if not isinstance(current, dict):
                raise ValueError("Controller must be an object")
        actual = storage_generation(current) if current is not None else 0
        if actual != expected_generation:
            raise StorageConflict(expected_generation, actual)
        if current is not None and encode_controller(current) == original_encoding:
            return actual
        proposed["storage_generation"] = actual + 1
        encoded = encode_controller(proposed)
        if len(encoded) > limit:
            raise ValueError("Proposed controller exceeds its byte limit")
        primitives.atomic_write_bytes(str(path), encoded, private=True)
        return actual + 1


def tp_dir(workspace: str) -> str:
    from taskplane.workspace_binding import validate_workspace_root
    root = validate_workspace_root(workspace).resolve()
    target = root / ".taskplane"
    if target.is_symlink():
        raise ValueError("Taskplane storage must remain inside the workspace")
    return str(target)


def kb_root(workspace: str) -> str:
    root = Path(tp_dir(workspace))
    target = root / "knowledge"
    if target.is_symlink():
        raise ValueError("Graph storage must remain inside the workspace")
    return str(target)


def runtime_file(workspace: str, name: str) -> Path:
    """Resolve a direct runtime file without following workspace-owned links."""
    if Path(name).name != name or name in {"", ".", ".."}:
        raise ValueError("Runtime files must have a plain filename")
    target = Path(tp_dir(workspace)) / name
    try:
        mode = target.lstat().st_mode
    except FileNotFoundError:
        return target
    if not stat.S_ISREG(mode):
        raise ValueError("Taskplane runtime target must be a regular file")
    return target


def dependency_graph_visual_path(workspace: str) -> str:
    return os.path.join(tp_dir(workspace), "graph.html")


def control_file(workspace: Path, supplied: Path) -> Path:
    """Validate an adapter-provisioned file; a path alone is not authority."""
    root = workspace.resolve()
    target = supplied.absolute()
    if ".." in target.parts or target.is_relative_to(root):
        raise ValueError("Authoritative control state cannot live in the workspace")
    for entry in (target, *target.parents):
        if entry.is_symlink():
            raise ValueError("Control state cannot follow symlinks")
    if not target.is_file():
        raise ValueError("Host-provisioned control state is missing or not a regular file")
    if target.resolve().is_relative_to(root):
        raise ValueError("Authoritative control state cannot resolve into the workspace")
    return target


def _git_value(workspace: str, *args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=workspace, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    return result.stdout.strip() if result.returncode == 0 else None
