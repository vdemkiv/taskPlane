"""Cooperative native workflow policy, never a protected host authority.

The account running the agent can edit this store or fabricate observations.
Checks enforce the Taskplane API contract and observed hooks, not host isolation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys
from typing import Any

from . import primitives, storage, workflow as w, workflow_evidence as evidence

PROFILE = "native_workflow"
MAX_BYTES = 8 * 1024 * 1024
EXCLUDED = {".git", ".taskplane", ".venv", "venv", "node_modules", "__pycache__",
            ".pytest_cache", ".mypy_cache", ".ruff_cache"}
CHOICES = {"approve": "approved", "approved": "approved", "reject": "rejected",
           "rejected": "rejected", "request changes": "changes_requested",
           "changes requested": "changes_requested", "cancel": "cancelled", "cancelled": "cancelled"}


def choice(text: str) -> str | None:
    value = text.strip().lower().rstrip(".! ")
    return CHOICES.get(value) or (CHOICES.get(value.split(":", 1)[0]) if ":" in value else None)


def timestamp(value: Any) -> datetime:
    w.require(isinstance(value, str) and len(value) <= 64, "invalid_evidence", "Decision time is missing.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        w.require(result.tzinfo is not None, "invalid_evidence", "Decision time needs a timezone.")
        return result
    except ValueError:
        raise w.Refusal("invalid_evidence", "Decision time is invalid.") from None


def inventory(workspace: Path) -> dict[str, str]:
    """Bounded source audit, including additions/deletions and symlink identity."""
    result: dict[str, str] = {}
    total = 0
    def failed(error: OSError) -> None:
        raise w.Refusal("state_unavailable", f"Source inventory is unreadable: {error.filename}")
    for parent, dirs, files in os.walk(workspace, followlinks=False, onerror=failed):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        for name in sorted([*files, *(d for d in dirs if (Path(parent)/d).is_symlink())]):
            target = Path(parent)/name
            relative = str(target.relative_to(workspace))
            w.require(len(result) < 20000, "state_unavailable", "Source inventory exceeds 20,000 files.")
            if target.is_symlink():
                result[relative] = "symlink:" + os.readlink(target)
                continue
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                w.require(stat.S_ISREG(info.st_mode), "state_unavailable", "Source inventory requires regular files.")
                total += info.st_size
                w.require(total <= 512 * 1024 * 1024, "state_unavailable", "Source inventory exceeds 512 MiB.")
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                result[relative] = digest.hexdigest()
    return result


class LocalWorkflow:
    """Mixin for the same Controller, with explicitly weaker local provenance."""
    profile = PROFILE

    def bind(self, workspace: Path, root: str) -> None:
        self.workspace, self.root = workspace, root
        key = hashlib.sha256(root.encode()).hexdigest()[:32]
        self.filename = f"workflow-{key}.json"
        self.markername = f"workflow-{key}.initialized.json"

    def control_path(self, workspace: Path, root: str) -> Path:
        w.require(workspace == self.workspace and root == self.root, "state_unavailable", "Local root changed.")
        return storage.runtime_file(str(workspace), self.filename)

    def validate_path(self, workspace: Path, target: Path) -> Path:
        w.require(target == self.control_path(workspace, self.root), "state_unavailable", "Local store path changed.")
        return target

    def marker(self) -> dict[str, Any]:
        return {"schema": "taskplane.local-initialization/v1", "workspace": str(self.workspace),
                "root": self.root, "profile": PROFILE}

    def state_exists(self) -> bool:
        target = self.control_path(self.workspace, self.root)
        marker = storage.runtime_file(str(self.workspace), self.markername)
        w.require(target.exists() == marker.exists(), "state_unavailable",
                  "Local workflow initialization/state is incomplete. Explicit recovery is required.")
        if marker.exists():
            w.require(marker.stat().st_size <= 4096 and json.loads(marker.read_text()) == self.marker(),
                      "state_unavailable", "Local initialization identity is corrupt.")
        return target.exists()

    def initialize(self) -> None:
        if self.state_exists():
            return
        # Marker first: interruption cannot silently create a fresh approval store.
        primitives.atomic_json(storage.runtime_file(str(self.workspace), self.markername),
                               self.marker(), strict_directory_sync=True)
        primitives.atomic_json(self.control_path(self.workspace, self.root), {
            "schema": "taskplane.control/v1", "profile": PROFILE, "workspace": str(self.workspace),
            "root": self.root, "active": None, "runs": {}}, strict_directory_sync=True)

    def verify_start(self, workspace: Path, root: str, request: dict[str, Any]) -> dict[str, Any]:
        scope = request.get("scope")
        w.require(isinstance(scope, dict), "invalid_evidence", "Native workflow start requires an exact --scope JSON file.")
        assert isinstance(scope, dict)
        evidence.valid_scope(workspace, scope)
        reference = request.get("request_reference")
        w.require(isinstance(reference, str) and 0 < len(reference) <= 512, "invalid_evidence",
                  "Identify the actual user request with --request-reference.")
        return {"scope": deepcopy(scope), "entry": request.get("entry", "product"),
                "standalone": request.get("standalone", False), "goal": request.get("goal", ""),
                "request_reference": reference}

    def state_created(self, state: dict[str, Any], request: dict[str, Any]) -> None:
        state.update(profile=PROFILE, source_baseline=inventory(self.workspace), observed_handles={},
                     request_provenance={"reference": request["request_reference"], "assurance": "observed"})

    def decorate(self, state: dict[str, Any]) -> dict[str, Any]:
        return {"coverage": {"structured_hook_paths": "checked when observed", "source_drift": "audited",
                "opaque_commands": "host permissions; effects not contained", "process_census": "unknown",
                "late_stdin": "checked only when observed"},
                "known_live_handles": [h for h,r in state.get("observed_handles", {}).items()
                                       if r["state"] == "running"]}

    def validate_state(self, state: dict[str, Any]) -> None:
        w.require(state.get("profile") == PROFILE and isinstance(state.get("source_baseline"), dict)
                  and isinstance(state.get("observed_handles"), dict), "state_unavailable", "Invalid local workflow state.")
        for handle, record in state["observed_handles"].items():
            w.require(isinstance(handle, str) and isinstance(record, dict)
                      and record.get("state") in {"running", "completed", "failed", "cancelled"}
                      and type(record.get("revision")) is int and isinstance(record.get("visit"), str),
                      "state_unavailable", "Invalid observed process record.")
        for decision in state["decisions"].values():
            w.require(decision.get("assurance") == "observed" and isinstance(decision.get("provenance"), dict),
                      "state_unavailable", "Local decisions require observed provenance, not host authentication.")
        w.require(len(state["source_baseline"]) <= 20000 and all(isinstance(k, str) and isinstance(v, str)
                  for k,v in state["source_baseline"].items()), "state_unavailable", "Invalid source audit baseline.")

    def before_action(self, state: dict[str, Any], action: str) -> None:
        before, after = state["source_baseline"], inventory(self.workspace)
        allowed = set(state["scope"]["paths"][w.current(state)["phase"]])
        changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
        w.require(not changed - allowed, "scope_violation",
                  "Source changed outside this phase scope: " + ", ".join(sorted(changed - allowed)[:10]))

    def can_seal(self, state: dict[str, Any]) -> bool:
        return not self.decorate(state)["known_live_handles"]

    def after_action(self, state: dict[str, Any], action: str) -> None:
        if action == "advance":
            state["source_baseline"] = inventory(self.workspace)

    def verify_decision_context(self, reference: str, expected: dict[str, Any],
                                prior: dict[str, Any]) -> dict[str, Any]:
        w.require(0 < len(reference.encode()) <= 16384, "invalid_evidence", "Observed decision exceeds its bound.")
        try:
            value = json.loads(reference)
        except ValueError:
            raise w.Refusal("invalid_evidence", "Supply an observed decision JSON envelope, not an actor flag.") from None
        w.require(isinstance(value, dict) and value.get("schema") == "taskplane.observed-decision/v1",
                  "invalid_evidence", "Observed decision schema is missing.")
        source = value.get("source")
        w.require(isinstance(source, dict) and source.get("kind") in {"conversation", "native_prompt"}
                  and source.get("conversation") == self.root and source.get("actor") == "user"
                  and source.get("automatic") is False and isinstance(source.get("reference"), str)
                  and 0 < len(source["reference"]) <= 512, "invalid_evidence", "Human response provenance is incomplete.")
        event_id, excerpt, recorder = value.get("event_id"), value.get("excerpt"), value.get("recorder")
        w.require(isinstance(event_id, str) and 0 < len(event_id) <= 512
                  and isinstance(excerpt, str) and 0 < len(excerpt) <= 512
                  and recorder in {"root_orchestrator", "native_prompt_hook"}
                  and value.get("choice") == choice(excerpt) and choice(excerpt) is not None,
                  "invalid_evidence", "An explicit human choice, event and recorder are required.")
        observed = timestamp(source.get("observed_at"))
        expected = prior[event_id]["binding"] if event_id in prior else expected
        w.require(primitives.content_fingerprint(value.get("binding")) == primitives.content_fingerprint(expected),
                  "stale_checkpoint", "Observed decision has a stale or foreign checkpoint binding.")
        if value.get("checkpoint_explicit") is not True:
            presentation = value.get("presentation")
            w.require(isinstance(presentation, dict) and presentation.get("checkpoint") == expected["checkpoint"]
                      and isinstance(presentation.get("reference"), str) and presentation["reference"],
                      "invalid_evidence", "Brief approval needs the presented checkpoint and ordering evidence.")
            w.require(timestamp(presentation.get("at")) < observed, "invalid_evidence", "Response precedes presentation.")
        else:
            w.require(expected["checkpoint"] in excerpt, "invalid_evidence", "Explicit approval must name the checkpoint.")
        return {"event_id": event_id, "human": True, "automatic": False, "choice": value["choice"],
                "binding": deepcopy(expected), "assurance": "observed", "provenance": {
                    "source": {k:source[k] for k in ("kind","reference","conversation","actor","automatic","observed_at")},
                    "recorder": recorder, "excerpt": excerpt,
                    "presentation": {k:value["presentation"][k] for k in ("checkpoint","reference","at")}
                        if value.get("checkpoint_explicit") is not True else None,
                    "checkpoint_explicit": value.get("checkpoint_explicit", False)}}

    def prompt_reference(self, event: dict[str, Any], state: dict[str, Any]) -> str | None:
        # Named hooks may carry an observed envelope. Plain hook-shaped text is
        # not enough to establish what checkpoint was shown before the response.
        value = event.get("taskplane_decision")
        return json.dumps(value) if isinstance(value, dict) else None

    def control_action(self, event: dict[str, Any], state: dict[str, Any]) -> bool:
        args = event.get("tool_input", {})
        command = args.get("cmd", args.get("command", ""))
        if not isinstance(command, str) or any(c in command for c in ";&|<>$`\n\r"):
            return False
        try:
            words = shlex.split(command)
        except ValueError:
            return False
        if (len(words) < 4 or Path(shutil.which(words[0]) or "/nonexistent").resolve() != Path(sys.executable).resolve()
                or (self.workspace/words[1]).resolve() != Path(__file__).with_name("tp.py").resolve()
                or words[2] != "flow" or words[3] not in {"report", "decide", "advance", "finish"}):
            return False
        # An exact control command still goes through the Controller checks.
        if "--workspace" not in words:
            return False
        index = words.index("--workspace") + 1
        return index < len(words) and (self.workspace/words[index]).resolve() == self.workspace

    def guard_command(self, event: dict[str, Any], state: dict[str, Any], paths: list[str]) -> None:
        pass  # Host permissions apply; inventory audits effects before transitions.

    def guard_input(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        args = event.get("tool_input", {})
        handle = str(args.get("session_id", ""))
        record = state["observed_handles"].get(handle)
        w.require(record is not None and record["state"] == "running"
                  and record["visit"] == w.current(state)["id"] and record["revision"] == state["revision"],
                  "scope_violation", "Observed input handle is unknown, terminal or belongs to an old phase grant.")

    def observe_state(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        if event.get("hook_event_name") != "PostToolUse" or self.control_action(event, state):
            return
        tool = event.get("tool_name") or event.get("tool")
        response = event.get("tool_response")
        if tool not in {"exec_command", "Bash", "write_stdin"} or not isinstance(response, dict):
            return  # Unstructured/uncovered process observations remain unknown.
        args = event.get("tool_input", {})
        handle = response.get("session_id", args.get("session_id") if tool == "write_stdin" else None)
        if type(handle) not in {str, int}:
            return
        key = str(handle)
        handles = state["observed_handles"]
        w.require(key in handles or len(handles) < 4096, "state_unavailable", "Observed handle limit reached.")
        previous = handles.get(key)
        terminal = type(response.get("exit_code")) is int
        new_state = ("completed" if response["exit_code"] == 0 else "failed") if terminal else "running"
        if previous:
            w.require(previous["visit"] == w.current(state)["id"] and previous["revision"] == state["revision"]
                      and (previous["state"] == "running" or previous["state"] == new_state),
                      "scope_violation", "Observed handle cannot reopen or cross grants.")
        handles[key] = {"visit": w.current(state)["id"], "revision": state["revision"],
                        "state": new_state}
