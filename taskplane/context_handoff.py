"""Durable current-work brief. Returned data never proves attention or approval."""
from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path
import shlex
from typing import Any

from . import workflow as w, workflow_evidence as evidence
from .context import Store, digest

BRIEF_CONTRACT = "brief/v1"


def binding(state: dict[str, Any]) -> dict[str, Any]:
    # Storage/telemetry revisions do not change task authority. Per-task
    # definitions, criterion epochs and dependency results carry freshness.
    return {**{key: state[key] for key in ("workspace", "root", "run")},
            "visit": w.current(state)["id"],
            "scope_digest": digest(state["scope"])}


def context_action(workspace: Path, run: str, task: str | None = None, *,
                   operation: str | None = None, key: str | None = None) -> str:
    args = ["flow", "context", "--workspace", str(workspace.resolve()), "--run", run]
    if task:
        args += ["--task", task]
    if operation == "read" and key:
        args += ["--read", key]
    return shlex.join(args)


def text_body(raw: bytes) -> dict[str, Any]:
    try:
        return {"encoding": "utf-8", "text": raw.decode("utf-8")}
    except UnicodeError:
        return {"encoding": "base64", "text": base64.b64encode(raw).decode("ascii")}


def source_read(workspace: Path, relative: str) -> bytes:
    return evidence.read(workspace, relative)


def input_status(workspace: Path, relative: str) -> dict[str, Any]:
    target = evidence.path(workspace, relative)
    if not target.exists():
        return {"path": relative, "status": "unavailable", "reason": "Input has not been produced."}
    import hashlib
    raw = source_read(workspace, relative)
    return {"path": relative, "status": "available", "sha256": hashlib.sha256(raw).hexdigest()}


def current_read_binding(workspace: Path, state: dict[str, Any]) -> dict[str, Any]:
    return binding(state)


class Brief:
    """A fresh consumer follows ordinary file/object references without receipts.

    Immutable bodies provide corruption checking and recovery; they do not
    establish that a model read, understood or accepted the supplied material.
    """
    def __init__(self, workspace: Path, state: dict[str, Any], task: str | None = None, *,
                 consumer: dict[str, Any] | None = None, snapshot: dict[str, Any] | None = None,
                 persist: bool = True) -> None:
        self.workspace, self.state, self.task = workspace.resolve(), state, task
        self.binding = binding(state)
        self.store = Store(self.workspace)
        phase = w.current(state)["phase"]
        tasks = evidence.context_tasks(state)
        definition = next((row for row in tasks if row["id"] == task), None) if task else None
        w.require(task is None or definition is not None and definition.get("phase", phase) == phase,
                  "invalid_context", "Task is not assigned to this phase.")
        accepted = [{"phase": stage["phase"], "visit": stage["id"],
                     "checkpoint": stage["packet"]["checkpoint"],
                     "output": deepcopy(stage["packet"]["output"])}
                    for stage in state["visits"][:state["index"]]
                    if stage.get("packet") and stage["decision"] == "approved" and not stage["superseded"]]
        declarations = evidence.read_inputs(state, definition) if definition else state["scope"].get("verification_inputs", [])
        self.items = [{"id": "source/" + relative, "kind": "source-input",
                       "body": {"path": relative, **input_status(self.workspace, relative)}}
                      for relative in declarations]
        self.body = {"schema": "taskplane.brief/v1", "binding": self.binding,
                     "phase": phase, "goal": state.get("goal", ""),
                     "request": deepcopy(state.get("request_provenance", {})),
                     "runtime": deepcopy(state.get("runtime_identity")),
                     "criteria": evidence.task_criteria(definition) if definition else state["scope"]["criteria"],
                     "task": deepcopy(definition), "accepted_results": accepted,
                     "prerequisite_results": {key: deepcopy(state.get("task_results", {}).get(key))
                                              for key in (definition.get("dependencies", []) if definition else [])},
                     "inputs": [item["body"] for item in self.items],
                     "write_scope": list(definition["paths"] if definition else w.phase_write_paths(state)),
                     "findings": evidence.finding_register(state),
                     "consumer": deepcopy(consumer), "context_is_authority": False}
        self.frozen = {"binding": deepcopy(self.binding), "task": task, "brief": deepcopy(self.body)}
        if snapshot is not None:
            w.require(snapshot.get("binding") == self.binding and snapshot.get("task") == task,
                      "invalid_context", "Frozen brief belongs to another visit or scope.")
            old = snapshot.get("brief", {})
            w.require(old.get("task") == self.body["task"] and old.get("inputs") == self.body["inputs"]
                      and old.get("prerequisite_results") == self.body["prerequisite_results"],
                      "invalid_context", "Relevant task inputs changed since preparation.")
        self.reference = self.store.put("work-brief", self.body) if persist else self.store.reference("work-brief", self.body)
        if persist:
            self.store.register(self.binding, [self.reference])

    def descriptor(self) -> dict[str, Any]:
        return {"schema": "taskplane.brief/v1", "binding": self.binding,
                "brief": deepcopy(self.body), "reference": self.reference,
                "unknown_inputs": [row for row in self.body["inputs"] if row["status"] != "available"]}

    def read(self, key: str, page: int = 0, section: str | None = None) -> dict[str, Any]:
        # Content addressing is integrity, not access control. These workspace
        # artifacts remain subject to ordinary host filesystem permissions.
        return {"schema": "taskplane.brief-read/v1", "page": self.store.page(key, page, section),
                "context_is_authority": False}
