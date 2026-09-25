"""One transaction owner for local workflow policy and protected host adapters."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable, Mapping, cast
import uuid

from . import primitives, storage, workflow as w, workflow_evidence as evidence
from . import host_capabilities, host_native, command_runtime, workflow_local, workflow_approval


class HostAdapter:
    """An in-process trusted host integration, never selected from agent input."""

    name = "unsupported"
    profile = "protected_host"

    def bind(self, workspace: Path, root: str) -> None:
        pass

    def state_exists(self) -> bool:
        return True

    def initialize(self) -> None:
        pass

    def validate_path(self, workspace: Path, target: Path) -> Path:
        return storage.control_file(workspace, target)

    def state_created(self, state: dict[str, Any], request: dict[str, Any]) -> None:
        pass

    def validate_state(self, state: dict[str, Any]) -> None:
        pass

    def before_action(self, state: dict[str, Any], action: str) -> None:
        pass

    def after_action(self, state: dict[str, Any], action: str) -> None:
        pass

    def decorate(self, state: dict[str, Any]) -> dict[str, Any]:
        return {}

    def can_seal(self, state: dict[str, Any]) -> bool:
        return self.quiescent(Path(state["workspace"]), state["root"], state["run"])

    def control_action(self, event: dict[str, Any], state: dict[str, Any]) -> bool:
        return False

    def observe_state(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        self.observe_native(event, state)

    def prompt_reference(self, event: dict[str, Any], state: dict[str, Any]) -> str | None:
        return str(event.get("native_event_id") or event.get("turn_id") or "")

    def capabilities(self) -> dict[str, Any]:
        return {"host": self.name, "protected_store": False, "human_origin": False,
                "tool_containment": False, "process_tracking": False}

    def control_path(self, workspace: Path, root: str) -> Path:
        raise w.Refusal("unsupported_authority", "No trusted host control store is provisioned.")

    def verify_start(self, workspace: Path, root: str, request: dict[str, Any]) -> dict[str, Any]:
        raise w.Refusal("unsupported_authority", "No independent human scope source is configured.")

    def verify_decision(self, native_reference: str, expected: dict[str, Any]) -> dict[str, Any]:
        raise w.Refusal("unsupported_authority", "Native human decision origin is unproven.")

    def verify_decision_context(self, reference: str, expected: dict[str, Any],
                                prior_decisions: dict[str, Any]) -> dict[str, Any]:
        return self.verify_decision(reference, expected)

    def quiescent(self, workspace: Path, root: str, run: str) -> bool:
        return False

    def contained_command(self, event: dict[str, Any], paths: list[str], revision: int) -> bool:
        return False

    def process_revision(self, event: dict[str, Any]) -> int | None:
        return None

    def observations(self, workspace: Path, root: str) -> dict[str, Any]:
        return host_capabilities.inspect_native(self.name, workspace, root)

    def guard_command(self, event: dict[str, Any], state: dict[str, Any], paths: list[str]) -> None:
        w.require(self.contained_command(event, paths, state["revision"]),
                  "scope_violation", "Opaque command has no verified containment for this phase.")

    def guard_input(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        w.require(self.process_revision(event) == state["revision"],
                  "scope_violation", "Interactive process belongs to an expired or unknown phase grant.")

    def observe_native(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        pass


class NativeAdapter(HostAdapter):
    """Integration for an already admitted host owner; never configured by CLI."""

    def __init__(self, session: host_native.NativeSession | None = None,
                 commands: command_runtime.CommandRuntime | None = None):
        self.session = session
        self.commands = commands

    def _session(self) -> host_native.NativeSession:
        w.require(self.session is not None and self.session.binding["host"] == self.name,
                  "unsupported_authority", "No matching native host owner is admitted.")
        assert self.session is not None
        self.session.require_current()
        return self.session

    def capabilities(self) -> dict[str, Any]:
        try:
            self._commands().validate()
            return {"host": self.name, **self._session().capabilities()}
        except (w.Refusal, OSError, ValueError, TypeError):
            return super().capabilities()

    def _commands(self) -> command_runtime.CommandRuntime:
        session = self._session()
        commands = self.commands
        w.require(commands is not None and commands.observer is cast(object, session.owner)
                  and commands.workspace == session.workspace and commands.root == session.binding["root"],
                  "unsupported_authority", "No matching native command owner is admitted.")
        assert commands is not None
        return commands

    def control_path(self, workspace: Path, root: str) -> Path:
        return self._session().control_path(workspace, root)

    def verify_start(self, workspace: Path, root: str, request: dict[str, Any]) -> dict[str, Any]:
        self.control_path(workspace, root)
        return self._session().verify_start(request)

    def verify_decision(self, native_reference: str, expected: dict[str, Any]) -> dict[str, Any]:
        return self._session().verify_decision(native_reference, expected)

    def verify_decision_context(self, reference: str, expected: dict[str, Any],
                                prior_decisions: dict[str, Any]) -> dict[str, Any]:
        return self._session().verify_decision(reference, expected, prior_decisions=prior_decisions)

    def quiescent(self, workspace: Path, root: str, run: str) -> bool:
        self.control_path(workspace, root)
        return self._commands().quiescent(run)

    def _native_command(self, event: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        session = self._session()
        self.control_path(Path(state["workspace"]), state["root"])
        identity = event.get("tool_use_id") or event.get("call_id")
        w.require(isinstance(identity, str) and bool(identity), "scope_violation", "Native call identity is missing.")
        expected_event = primitives.content_fingerprint({"call_id": identity,
            "tool": event.get("tool_name") or event.get("tool"), "input": event.get("tool_input", {})})
        try:
            observed = self._commands().observer.command_for(event)
        except (OSError, ValueError, TypeError, KeyError):
            raise w.Refusal("scope_violation", "Independent native call evidence is unavailable.") from None
        w.require(isinstance(observed, Mapping) and observed.get("event_digest") == expected_event
                  and primitives.content_fingerprint(observed.get("grant")) == primitives.content_fingerprint(command_runtime.grant(state)),
                  "scope_violation", "Native call differs from its current phase/process grant.")
        session.require_current()
        return deepcopy(dict(observed))

    def guard_command(self, event: dict[str, Any], state: dict[str, Any], paths: list[str]) -> None:
        observed = self._native_command(event, state)
        w.require(observed.get("contained") is True and observed.get("paths") == sorted(set(paths)),
                  "scope_violation", "Native command containment does not match the exact phase scope.")

    def guard_input(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        observed = self._native_command(event, state)
        handle = observed.get("handle")
        w.require(isinstance(handle, str) and bool(handle), "scope_violation", "Native input handle is unknown.")
        assert isinstance(handle, str)
        self._commands().reconnect(handle, command_runtime.grant(state))

    def observe_native(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        if event.get("hook_event_name") not in {"PostToolUse", "SubagentStart", "SubagentStop"}:
            return
        observed = self._native_command(event, state)
        handle = observed.get("handle")
        # A completed non-process tool has nothing to register. The native
        # census still has to prove that no unknown execution is outstanding.
        if handle is not None:
            w.require(isinstance(handle, str) and bool(handle), "scope_violation", "Native handle is invalid.")
            self._commands().create(handle, command_runtime.grant(state))


class CodexAdapter(NativeAdapter):
    name = "codex"


class ClaudeAdapter(NativeAdapter):
    name = "claude"


class LocalAdapter(workflow_local.LocalWorkflow, HostAdapter):
    def __init__(self, host: str):
        self.name = host


def installed_adapter(host: str, profile: str = "native_workflow") -> HostAdapter:
    w.require(host in {"codex", "claude"} and profile in {"native_workflow", "protected_host"},
              "unsupported_authority", "Unknown workflow host/profile.")
    if profile == "native_workflow":
        return LocalAdapter(host)
    return ClaudeAdapter() if host == "claude" else CodexAdapter()


class Controller:
    def __init__(self, workspace: Path, root: str, adapter: HostAdapter, *, principal: str | None = None):
        self.workspace = workspace.resolve()
        self.root = root
        self.principal = principal or root
        self.adapter = adapter
        adapter.bind(self.workspace, root)

    def availability(self) -> dict[str, Any]:
        caps = self.adapter.capabilities()
        ready = all(caps.get(k) is True for k in
                    ("protected_store", "human_origin", "tool_containment", "process_tracking"))
        local = self.adapter.profile == "native_workflow"
        return {"profile": self.adapter.profile, "workflow_available": ready or local,
                "authority_verified": ready, "capabilities": caps,
                "native_observations": self.adapter.observations(self.workspace, self.root),
                "status": "available" if ready or local else "capability_blocked",
                "detail": "Workflow gates active; host-wide protection unavailable." if local else
                "Host approval protection and origin are verified." if ready else
                "Host approval protection/origin is unverified. Keep human gates; prepare a trusted integration before activation."}

    def _path(self) -> Path:
        w.require(self.availability()["workflow_available"], "unsupported_authority", self.availability()["detail"])
        try:
            return self.adapter.validate_path(self.workspace, self.adapter.control_path(self.workspace, self.root))
        except (OSError, ValueError) as exc:
            if isinstance(exc, w.Refusal):
                raise
            raise w.Refusal("state_unavailable", str(exc)) from None

    def _read(self, target: Path) -> dict[str, Any]:
        try:
            self.adapter.validate_path(self.workspace, target)
            w.require(self.adapter.state_exists(), "state_unavailable", "Workflow store is not initialized.")
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                w.require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "state_unavailable", "Invalid control file.")
                raw = stream.read(workflow_local.MAX_BYTES + 1)
                w.require(len(raw.encode()) <= workflow_local.MAX_BYTES, "state_unavailable", "Workflow store is oversized.")
                db = json.loads(raw)
            w.require(isinstance(db, dict) and db.get("schema") == "taskplane.control/v1"
                      and db.get("workspace") == str(self.workspace) and db.get("root") == self.root
                      and db.get("profile", "protected_host") == self.adapter.profile
                      and isinstance(db.get("runs"), dict) and "active" in db,
                      "state_unavailable", "Control state identity or schema is invalid.")
            for key, s in db["runs"].items():
                w.require(s["schema"] == "taskplane.workflow/v1" and s["run"] == key
                          and s["root"] == self.root and s["workspace"] == str(self.workspace)
                          and isinstance(s["revision"], int) and isinstance(s["decisions"], dict)
                          and s["visits"] and 0 <= s["index"] < len(s["visits"]),
                          "state_unavailable", "Invalid stored workflow.")
                w.validate_state(s)
                w.require(s.get("profile", "protected_host") == self.adapter.profile,
                          "state_unavailable", "Stored run belongs to another profile.")
                self.adapter.validate_state(s)
            w.require(db["active"] is None or db["active"] in db["runs"],
                      "state_unavailable", "Active workflow binding is missing.")
            from . import workflow_retention
            workflow_retention.validate_index(db)
            return dict(db)
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            if isinstance(exc, w.Refusal):
                raise
            raise w.Refusal("state_unavailable", f"Control state cannot be read: {type(exc).__name__}") from None

    def _write(self, target: Path, db: dict[str, Any]) -> None:
        try:
            self.adapter.validate_path(self.workspace, target)
            from . import workflow_retention
            db = workflow_retention.compact(self.workspace, db)
            raw = (json.dumps(db, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                              allow_nan=False) + "\n").encode("utf-8")
            w.require(len(raw) <= workflow_local.MAX_BYTES,
                      "state_unavailable", "Active workflow data exceeds its 8 MiB size limit after history retention. "
                      "Inspect flow report storage; explicitly retire or replace obsolete work. History was not deleted.")
            # Match the measured encoding while retaining the private-file writer.
            primitives.atomic_json(target, db, sort_keys=True, indent=None, ensure_ascii=True,
                                   trailing_newline=True, strict_directory_sync=True)
        except (OSError, ValueError, primitives.StateError) as exc:
            raise w.Refusal("state_unavailable", f"Control state was not acknowledged: {exc}") from None

    def _initial_tasks(self, request: dict[str, Any], scope: dict[str, Any]) -> list[dict[str, Any]]:
        """Freeze requested data, not a workspace-global source of authority."""
        relative = request.get("tasks")
        if not relative:
            return []
        w.require(isinstance(relative, str), "invalid_evidence", "Initial tasks need a workspace file path.")
        rows = evidence.task_dag(evidence.object_file(self.workspace, relative), scope["criteria"])
        allowed = {path for paths in scope["paths"].values() for path in paths}
        for row in rows:
            phase = row.get("phase")
            w.require(phase is None or phase in w.PHASES, "invalid_evidence", "Initial task has an unknown phase.")
            paths = set(scope["paths"][phase]) if phase else allowed
            w.require(set(row["paths"]) <= paths and set(evidence.task_criteria(row)) <= set(scope["criteria"]),
                      "scope_violation", "Initial task paths and criteria must stay within the requested scope.")
        frozen = [{k: deepcopy(v) for k, v in row.items() if k not in evidence.TASK_OBSERVATIONS} for row in rows]
        from .context import encode
        w.require(len(encode(frozen)) <= 65536, "invalid_evidence", "Initial task snapshot exceeds 64 KiB.")
        return frozen

    def _check_start_tasks(self, state: dict[str, Any], request: dict[str, Any]) -> None:
        if request.get("tasks"):
            requested = self._initial_tasks(request, state["scope"])
            initial = state.get("initial_context_tasks", [])
            # Existing starts also refresh dashboard evidence. With no initial
            # task definitions, scoped display rows cannot replace the empty
            # snapshot; context keeps using the run's scope until submission.
            w.require(not initial or requested == initial,
                      "stale_checkpoint", "Start retry cannot replace the run's initial tasks.")

    def start(self, request: dict[str, Any]) -> dict[str, Any]:
        w.require(self.principal == self.root, "scope_violation", "Workers cannot start or replace workflows.")
        target = self._path()
        with primitives.file_lock(str(target)):
            authorized = None
            if not self.adapter.state_exists():
                w.require(not request.get("replace_run"), "state_unavailable",
                          "No initialized run exists to replace.")
                authorized = self.adapter.verify_start(self.workspace, self.root, request)
                evidence.valid_scope(self.workspace, authorized["scope"])
                self._initial_tasks(request, authorized["scope"])
                # Validate route before creating the durable initialization marker.
                w.new_state(str(self.workspace), self.root, "validate", authorized["scope"],
                            entry=authorized["entry"], standalone=authorized["standalone"])
                self.adapter.initialize()
            db = self._read(target)
            replaced = None
            replace_run = request.get("replace_run")
            if replace_run:
                w.require(self.adapter.profile == "native_workflow", "unsupported_authority",
                          "Run replacement requires a native workflow; protected host recovery is owner-controlled.")
                previous = db["runs"].get(replace_run)
                if previous is None and replace_run in db.get("archives", {}):
                    from . import workflow_retention
                    previous = workflow_retention.read(self.workspace, db, replace_run)
                revision = request.get("expected_revision")
                w.require(previous and type(revision) is int, "stale_checkpoint",
                          "Replacement requires the previous --replace-run and --expected-revision.")
                assert isinstance(revision, int)
                # A retry can recover a lost response, never replace a different active run.
                if previous.get("superseded_by") == db["active"] and db["active"]:
                    active = db["runs"][db["active"]]
                    w.require(previous["revision"] == revision + 1
                              and active.get("request_provenance", {}).get("reference") == request.get("request_reference")
                              and active["scope"] == request.get("scope"),
                              "stale_checkpoint", "Conflicting run replacement retry.")
                    self._check_start_tasks(active, request)
                    return deepcopy(active)
                w.require(db["active"] == replace_run and previous["revision"] == revision,
                          "stale_checkpoint", "The run or revision selected for replacement changed.")
                w.require(self.adapter.can_seal(previous), "scope_violation",
                          "Live tool processes must stop before replacing a run.")
                replaced = deepcopy(previous)
            elif db["active"]:
                active = db["runs"][db["active"]]
                w.require((not request.get("request_reference") or request["request_reference"] ==
                           active.get("request_provenance", {}).get("reference"))
                          and (not request.get("scope") or request["scope"] == active["scope"]),
                          "approval_required", "Another run is active. To start a new scope, explicitly use "
                          "--replace-run and --expected-revision with the new user request reference.")
                self._check_start_tasks(active, request)
                return deepcopy(active)
            authorized = authorized or self.adapter.verify_start(self.workspace, self.root, request)
            evidence.valid_scope(self.workspace, authorized["scope"])
            s = w.new_state(str(self.workspace), self.root, uuid.uuid4().hex, authorized["scope"],
                            entry=authorized["entry"], standalone=authorized["standalone"])
            s.update(goal=str(authorized.get("goal", request.get("goal", ""))),
                     started_at=datetime.now(timezone.utc).isoformat(), profile=self.adapter.profile,
                     initial_context_tasks=self._initial_tasks(request, authorized["scope"]))
            self.adapter.state_created(s, authorized)
            if replaced is not None:
                # Commit preservation, grant revocation and the fresh baseline atomically.
                # Replacement is not acceptance, cancellation or a migration of approvals.
                replaced["superseded_by"] = s["run"]
                replaced["revision"] += 1
                workflow_approval.suspend(replaced, "User requested a new run.")
                replaced["history"].append({"replaced_by": s["run"],
                    "request_reference": authorized["request_reference"], "at": s["started_at"]})
                s["replaces"] = {"run": replaced["run"], "revision": request["expected_revision"]}
                db["runs"][replaced["run"]] = replaced
            db["runs"][s["run"]] = s
            db["active"] = s["run"]
            self._write(target, db)
            return deepcopy(s)

    def report(self, run: str | None = None) -> dict[str, Any]:
        if not self.availability()["workflow_available"]:
            return self.availability()
        target = self._path()
        with primitives.file_lock(str(target)):
            if not self.adapter.state_exists():
                w.require(run is None, "state_unavailable", "Requested run has no local workflow binding.")
                return {**self.availability(), "status": "no_workflow"}
            db = self._read(target)
            key = run or db["active"]
            if key is None:
                from . import workflow_retention
                return {**self.availability(), "status": "no_workflow",
                        "storage": workflow_retention.capacity(db, workflow_local.MAX_BYTES)}
            from . import workflow_retention
            archived = key in db.get("archives", {})
            s = workflow_retention.read(self.workspace, db, key)
            changed = None if archived or s.get("superseded_by") or s.get("retired") else evidence.changed(self.workspace, s)
            if changed:
                revision = s["revision"]
                s = w.invalidate(s, *changed)
                # A read-only projection cannot advertise a revision not yet committed.
                s["revision"] = revision
                s["invalidation_pending"] = True
            return {**s, **self.availability(), **self.adapter.decorate(s), "phase": w.current(s)["phase"],
                    "storage": workflow_retention.capacity(db, workflow_local.MAX_BYTES), "archived": archived,
                    "pending_checkpoint": w.binding(s, w.current(s)["packet"])
                        if not s.get("superseded_by") and not s.get("retired") and w.current(s)["decision"] == "awaiting_human_approval" else None,
                    "status": "retired" if s.get("retired") else "superseded" if s.get("superseded_by") else
                              "accepted" if s["finished"] else w.current(s)["decision"]}

    def context(self, run: str | None = None, *, task: str | None = None,
                 consume: str | None = None, read: str | None = None,
                 page: int = 0, section: str | None = None,
                 read_required: str | None = None) -> dict[str, Any]:
        """Current-binding derived data only; never writes a workflow decision."""
        from .context_handoff import Session
        state = self.report(run)
        w.require(state.get("run") and not state.get("invalidation_pending"),
                  "invalid_context", "Repair the current workflow binding before consuming context.")
        with primitives.file_lock(str(self._path())):
            db = self._read(self._path())
            w.require(db["active"] == state["run"]
                      and db["runs"][state["run"]]["revision"] == state["revision"],
                      "invalid_context", "Context requires the unchanged active run.")
            from . import worker_runtime as workers
            worker = workers.find(state, self.principal) if self.principal != self.root else None
            if self.principal != self.root:
                w.require(worker is not None and task == worker["task_id"], "scope_violation",
                          "Worker context requires its own claimed task.")
                assert worker is not None
                w.require(worker["state"] in {"bootstrapping", "running"}, "scope_violation", "Worker attempt is not live.")
                session = workers.worker_session(self.workspace, state, worker)
            else:
                session = Session(self.workspace, state, task)
            w.require(sum(value is not None for value in (consume, read, read_required)) <= 1,
                      "invalid_context", "Choose consume, read or read-required.")
            w.require(read is not None or (page == 0 and section is None),
                      "invalid_context", "Page and section require a single-reference read.")
            if consume:
                result = session.consume(consume)
            elif read:
                result = session.read(read, page, section)
            elif read_required is not None:
                result = session.read_required(read_required)
            else:
                result = {"schema": "taskplane.context-preparation/v1", "binding": session.binding,
                          **session.descriptor()}
            if worker is not None and result.get("remaining_required") == 0:
                session.validate(result["context_receipt"])
                record = db["runs"][state["run"]]["workers"][worker["grant_id"]]
                record.update(context_receipt=result["context_receipt"], state="running")
                self._write(self._path(), db)
            return result

    def update_tasks(self, run: str, revision: int | None, tasks: str) -> dict[str, Any]:
        w.require(self.principal == self.root, "scope_violation", "Only the root can publish task definitions.")
        target = self._path()
        with primitives.file_lock(str(target)):
            db = self._read(target)
            w.require(db["active"] == run, "stale_checkpoint", "Task publication requires the active run.")
            state = db["runs"][run]
            frozen = evidence.freeze_tasks(self.workspace, state, evidence.object_file(self.workspace, tasks))
            prior = state.get("task_context", {})
            if (prior.get("visit") == w.current(state)["id"] and prior.get("tasks") == frozen
                    and revision in {state["revision"], prior.get("base_revision")}):
                return deepcopy(state)
            w.require(revision == state["revision"], "stale_checkpoint", "Expected task revision changed.")
            w.require(w.current(state)["decision"] in {"not_requested", "changes_requested", "rejected"}
                      and self.adapter.can_seal(state), "scope_violation", "Task updates need an unsealed, quiescent visit.")
            w.require(evidence.changed(self.workspace, state) is None, "stale_checkpoint", "Accepted inputs changed.")
            self.adapter.before_action(state, "update-tasks")
            state["task_context"] = {"visit": w.current(state)["id"], "tasks": frozen, "base_revision": revision}
            state["task_generation"] = state.get("task_generation", 0) + 1
            state["revision"] += 1
            self._write(target, db)
            return deepcopy(state)

    def worker(self, run: str, operation: str, *, revision: int | None = None,
               task: str = "", grant: str = "", request: dict[str, Any] | None = None) -> dict[str, Any]:
        from . import worker_runtime as workers
        w.require(self.adapter.profile == "native_workflow", "unsupported_authority", "Worker adapter is cooperative native_workflow only.")
        target = self._path()
        with primitives.file_lock(str(target)):
            db = self._read(target)
            w.require(db["active"] == run, "stale_checkpoint", "Worker operation needs the active run.")
            state = db["runs"][run]
            request = request or {}
            if operation == "claim":
                row = workers.records(state).get(grant)
                w.require(row and self.principal != self.root and row.get("worker_id") == self.principal,
                          "scope_violation", "Claim requires an observed native identity; prompt or parent alone is insufficient.")
                assert row is not None
                workers.current(state, row)
                w.require(row["state"] in {"bootstrapping", "running"}, "scope_violation", "Worker is not live.")
                return {"grant_id": grant, "task_id": row["task_id"], "context": workers.worker_session(self.workspace, state, row).descriptor()}
            w.require(self.principal == self.root, "scope_violation", "Workers cannot schedule or accept task results.")
            if operation == "status":
                return workers.summary(state, self.workspace)
            w.require(revision == state["revision"], "stale_checkpoint", "Expected worker revision changed.")
            w.require(w.current(state)["decision"] in {"not_requested", "changes_requested", "rejected"}
                      and not state.get("finished") and not state.get("retired"), "approval_required", "Current phase is sealed or inactive.")
            w.require(evidence.changed(self.workspace, state) is None, "stale_checkpoint", "Accepted evidence changed.")
            self.adapter.before_action(state, "worker")
            if operation == "prepare":
                row = workers.prepare(self.workspace, state, task, request)
                result = {"grant": deepcopy(row), "message": workers.dispatch_message(state, row)}
            elif operation == 'capacity':
                limit = workers.capacity(request.get('capacity'))
                state['worker_capacity'] = {**request['capacity'], 'effective_limit': limit,
                                            'observed_at': workers.now()}
                result = workers.summary(state, self.workspace)
            elif operation == "accept-result":
                result = workers.accept_result(self.workspace, state, task, {**request, **({"grant": grant} if grant else {})})
            elif operation == "abandon":
                row = workers.records(state).get(grant)
                w.require(row and row["state"] == "prepared" and row["call_id"] is None,
                          "scope_violation", "Only an unlaunched reservation can be abandoned.")
                assert row is not None
                row.update(state="failed", ended_at=workers.now(), terminal_status="not_launched")
                result = deepcopy(row)
            else:
                raise w.Refusal("invalid_evidence", "Unknown worker operation.")
            state["worker_sequence"] = state.get("worker_sequence", 0) + 1
            self._write(target, db)
            return result

    def apply(self, action: str, run: str, *, expected_revision: int | None = None,
              output: str = "", tasks: str = "", phase: str = "", native_reference: str = "",
              assessment_json: str | None = None) -> dict[str, Any]:
        w.require(self.principal == self.root, "scope_violation", "Workers cannot change workflow control state.")
        w.require(assessment_json is None or action == "auto-decide", "invalid_evidence",
                  "Inline assessment applies only to auto-decide.")
        target = self._path()
        with primitives.file_lock(str(target)):
            db = self._read(target)
            w.require(db["active"] == run or action == "finish" and run in db["runs"],
                      "state_unavailable", "Mutation must address the bound active run.")
            s = db["runs"][run]
            if action == "retire":
                w.require(self.adapter.profile == "native_workflow", "unsupported_authority",
                          "Protected run retirement requires its trusted owner.")
                w.require(expected_revision == s["revision"], "stale_checkpoint", "Expected state revision changed.")
                w.require(self.adapter.can_seal(s), "scope_violation", "Stop live work before retirement.")
                try:
                    request = json.loads(native_reference)
                except (ValueError, TypeError):
                    raise w.Refusal("invalid_evidence", "Retirement needs an actual request reference and reason.") from None
                w.require(isinstance(request, dict) and all(isinstance(request.get(k), str)
                          and 0 < len(request[k].strip()) <= 2048 for k in ("request_reference", "reason")),
                          "invalid_evidence", "Retirement needs an actual request reference and reason.")
                updated = deepcopy(s)
                updated["retired"] = {**request, "at": datetime.now(timezone.utc).isoformat(),
                                      "accepted": False, "assurance": "observed"}
                updated["history"].append({"retired": updated["retired"]})
                updated["revision"] += 1
                workflow_approval.suspend(updated, "User retired this run; no acceptance granted.")
                db["runs"][run], db["active"] = updated, None
                self._write(target, db)
                return deepcopy(updated)
            w.require(not s.get("retired"), "state_unavailable", "Retired workflows have no active grants.")
            w.require(not s.get("superseded_by"), "state_unavailable",
                      "This run was replaced; its evidence is historical and its grants are revoked.")
            policy_request: dict[str, Any] = {}
            if action == "policy":
                w.require(len(native_reference.encode()) <= 32768, "invalid_evidence", "Policy exceeds its size bound.")
                try:
                    policy_request = json.loads(native_reference)
                except ValueError:
                    raise w.Refusal("invalid_evidence", "Supply a policy JSON envelope.") from None
                w.require(isinstance(policy_request, dict), "invalid_evidence", "Policy must be an object.")
            if action == "auto-decide":
                assessment = workflow_approval.read_assessment(self.workspace, output, assessment_json)
                verified = workflow_approval.automatic_decision(s, assessment)
            elif action == "decide":
                packet = w.current(s)["packet"]
                w.require(packet, "approval_required", "No submitted human checkpoint.")
                # The selected adapter validates protected or observed provenance.
                # Raw approval text and actor flags alone never become a decision.
                native = self.adapter.verify_decision_context(native_reference, w.binding(s, packet), s["decisions"])
                # Persist decision metadata, not arbitrary native prompt/transcript content.
                verified = {k: native[k] for k in ("event_id", "human", "automatic", "choice", "binding", "assurance", "provenance") if k in native}
            else:
                verified = {}
            replay = action in ("decide", "auto-decide") and verified.get("event_id") in s["decisions"]
            policy_replay = action == "policy" and policy_request.get("event_id") in s.get("policy_events", {})
            w.require(replay or policy_replay or expected_revision == s["revision"], "stale_checkpoint", "Expected state revision changed.")
            negative = (action == "decide" and self.adapter.profile == "native_workflow"
                        and verified.get("choice") in {"changes_requested", "rejected", "cancelled"})
            # An exactly bound negative response accepts no evidence. Source drift must
            # still block approvals/transitions, but cannot veto the user's correction.
            if negative and replay:
                return w.decide(s, verified)
            drift = None if negative else evidence.changed(self.workspace, s, skip_current=action == "submit")
            if drift:
                db["runs"][run] = w.invalidate(s, *drift)
                self._write(target, db)
                raise w.Refusal("stale_checkpoint", drift[1])
            if not negative:
                self.adapter.before_action(s, action)
            if replay:
                return w.decide(s, verified)
            if action in ("advance", "finish", "submit", "auto-decide"):
                w.require(self.adapter.can_seal(s),
                          "scope_violation", "Live tool processes must stop before sealing or revoking a phase grant.")
            operations: dict[str, Callable[[], dict[str, Any]]] = {
                "submit": lambda: w.submit(s, evidence.seal(self.workspace, s, output, tasks)),
                "decide": lambda: w.decide(s, verified),
                "auto-decide": lambda: w.decide(s, verified),
                "policy": lambda: workflow_approval.authorize(s, policy_request),
                "advance": lambda: w.advance(s, phase),
                "finish": lambda: w.finish(s),
            }
            w.require(action in operations, "invalid_evidence", "Unknown workflow operation.")
            try:
                updated = operations[action]()
            except (TypeError, KeyError, IndexError) as exc:
                raise w.Refusal("invalid_evidence", f"Invalid phase evidence: {type(exc).__name__}") from None
            self.adapter.after_action(updated, action)
            db["runs"][run] = updated
            if updated["finished"] and db["active"] == run:
                db["active"] = None
            self._write(target, db)
            return deepcopy(updated)

    def observe(self, event: dict[str, Any], run: str) -> None:
        target = self._path()
        with primitives.file_lock(str(target)):
            db = self._read(target)
            w.require(db["active"] == run, "state_unavailable", "Observation has no active workflow binding.")
            state = deepcopy(db["runs"][run])
            from . import worker_runtime as workers
            if self.adapter.profile == "native_workflow":
                workers.observe(state, event)
            self.adapter.observe_state(event, state)
            if state != db["runs"][run]:
                db["runs"][run] = state
                self._write(target, db)

    def guard(self, event: dict[str, Any], run: str) -> None:
        state = self.report(run)
        target = self._path()
        with primitives.file_lock(str(target)):
            db = self._read(target)
            w.require(not db["runs"].get(run, {}).get("retired"), "scope_violation", "The retired run has no active grants.")
            w.require(db["active"] == run and db["runs"][run]["revision"] == state["revision"],
                      "stale_checkpoint", "Tool grant changed during admission.")
            candidate = deepcopy(db["runs"][run])
            state.update(candidate)
            self._guard(event, state)
            # Commit mandatory admission before returning permission to the host.
            for key in ("workers", "worker_sequence", "worker_polls"):
                if key in state:
                    candidate[key] = state[key]
            if candidate != db["runs"][run]:
                db["runs"][run] = candidate
                self._write(target, db)

    def _guard(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        from . import worker_runtime as workers
        w.require(state.get("workflow_available"), "unsupported_authority", state.get("detail", "Host guard unavailable."))
        w.require(state.get("visits"), "state_unavailable", "No active workflow grant.")
        w.require(not state.get("superseded_by"), "scope_violation", "The replaced run has no active grants.")
        w.require(not state.get("retired"), "scope_violation", "The retired run has no active grants.")
        w.require(not state.get("finished"), "scope_violation", "The route has ended; its write grants are revoked.")
        stage = w.current(state)
        tool = event.get("tool_name") or event.get("tool")
        args = event.get("tool_input", {})
        w.require(isinstance(args, dict), "scope_violation", "Unrecognized tool arguments.")
        worker = None
        if self.principal != self.root:
            worker = workers.find(state, self.principal)
            w.require(worker is not None, "scope_violation", "Unbound native child has no task grant.")
            assert worker is not None
            # Read/control exceptions never confer root authority on a descendant.
            words = workflow_local.runtime_words(event) if tool in {"Bash", "exec_command"} else []
            native_cli = (len(words) >= 3 and (self.workspace/words[1]).resolve() == Path(__file__).with_name('tp.py').resolve())
            if self.adapter.control_action(event, state) or native_cli:
                w.require(len(words) >= 4 and words[2] == "flow" and words[3] in {"context", "worker"}
                          and (words[3] == "context" or "--operation" in words
                               and words.index("--operation") + 1 < len(words)
                               and words[words.index("--operation") + 1] == "claim"),
                          "scope_violation", "Worker control is limited to claim and task context.")
                return
            workers.current(state, worker)
            w.require(worker["state"] == "running" and worker.get("context_receipt"),
                      "invalid_context", "Worker must consume every required task input before execution.")
            workers.worker_session(self.workspace, state, worker).validate(worker["context_receipt"])
            if tool in workers.MESSAGE:
                parent_name = str(worker.get('canonical_name', '')).rsplit('/', 1)[0]
                w.require(set(args) == {'target', 'message'} and args.get('target') in
                          ({self.root, parent_name} - {''}) and isinstance(args.get('message'), str)
                          and 0 < len(args['message']) <= 32768, 'scope_violation',
                          'Worker messages may only report to their bound parent.')
                return
            w.require(tool not in workers.TOOLS, "scope_violation", "Nested delegation is unsupported.")
        elif self.adapter.profile == "native_workflow" and workers.admit(state, event):
            return
        if self.adapter.profile == "native_workflow" and (
                tool in workflow_local.QUESTION_TOOLS or workflow_local.execution_entry(event)
                or tool == "Skill" and args.get("skill") in {"taskplane:tp-help", "taskplane:tp-status"}):
            return  # Loading a skill or asking the user grants no source write or phase transition.
        if tool in workflow_local.READ_TOOLS:
            value = args.get("file_path") or args.get("path")
            if value:
                w.require(isinstance(value, str), "scope_violation", "Invalid read path.")
                relative = str(Path(value).relative_to(self.workspace)) if Path(value).is_absolute() and Path(value).is_relative_to(self.workspace) else value
                evidence.path(self.workspace, relative)
            return
        if worker is None and tool in ("Bash", "exec_command") and self.adapter.control_action(event, state):
            return
        if worker is None and self.adapter.profile == "native_workflow":
            harness = workflow_local.Harness(self.workspace, self.root)
            if (harness.dashboard_opener(event, state) or harness.recovery_action(event, state)
                    or harness.recovery_setup(event, state)):
                return
            if tool in ("Bash", "exec_command") and workflow_local.readonly_command(event):
                return
            if tool == "write_stdin" and args.get("chars", "") in ("", "\x03"):
                record = state.get("observed_handles", {}).get(str(args.get("session_id", "")), {})
                if record:
                    self.adapter.guard_input(event, state)
                    return  # Drain known work across revisions without sending new code.
            if workflow_local.bootstrap_write(self.workspace, event, state):
                return  # Fresh recovery scope only; never overwrite sealed evidence.
        w.require(not state.get("invalidation_pending"), "stale_checkpoint",
                  "Evidence drift must revoke the prior phase grant before further writes.")
        w.require(stage["decision"] in ("not_requested", "changes_requested", "rejected", "stale"),
                  "approval_required", "Current output is sealed; resolve the human checkpoint before writing.")
        allowed = worker["paths"] if worker else state["scope"]["paths"][stage["phase"]]
        if worker is None:
            busy = {p for row in state.get('workers', {}).values() if row['state'] in workers.LIVE
                    for p in [*row['paths'], *row.get('input_manifest', {}), *row.get('dependency_manifest', {})]}
            allowed = [p for p in allowed if p not in busy]
        if tool in ("Bash", "exec_command"):
            self.adapter.guard_command(event, state, allowed)
        elif tool == "write_stdin":
            if worker is not None:
                record = state.get("observed_handles", {}).get(str(args.get("session_id", "")), {})
                w.require(record.get("worker_id") == self.principal, "scope_violation", "Worker input belongs to another principal.")
            self.adapter.guard_input(event, state)
        elif tool in ("Write", "Edit", "write_file", "edit_file"):
            self._paths([args.get("file_path") or args.get("path")], allowed)
        elif tool == "apply_patch":
            patch = args.get("command", args.get("input", args.get("patch", "")))
            w.require(isinstance(patch, str), "scope_violation", "Invalid structured patch.")
            targets = [line.split(": ", 1)[1] for line in patch.splitlines()
                       if line.startswith(("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: "))]
            w.require(targets, "scope_violation", "Patch targets could not be determined.")
            self._paths(targets, allowed)
        else:
            raise w.Refusal("scope_violation", "This tool has no verified phase-scope guard.")

    def _paths(self, values: list[Any], allowed: list[str]) -> None:
        for value in values:
            w.require(isinstance(value, str) and value, "scope_violation", "A structured write needs a target.")
            relative = str(Path(value).relative_to(self.workspace)) if Path(value).is_absolute() and Path(value).is_relative_to(self.workspace) else value
            evidence.path(self.workspace, relative)
            w.require(relative in allowed, "scope_violation", f"Write is outside current phase scope: {relative}")
