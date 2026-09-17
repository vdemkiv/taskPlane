"""Workspace-local storage for graph data and delivery observations."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import stat


def tp_dir(workspace: str) -> str:
    root = Path(workspace).resolve()
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
