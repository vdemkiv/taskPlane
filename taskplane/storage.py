"""Workspace-local storage for graph data and delivery observations."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess


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


def dependency_graph_visual_path(workspace: str) -> str:
    return os.path.join(tp_dir(workspace), "graph.html")


def _git_value(workspace: str, *args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=workspace, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    return result.stdout.strip() if result.returncode == 0 else None
