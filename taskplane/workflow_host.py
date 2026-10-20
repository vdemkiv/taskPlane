"""One transaction owner for local workflow policy and protected host adapters."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import re
import time
from typing import Any, Callable, Iterator, Mapping, cast
import uuid

from . import primitives, storage, workflow as w, workflow_evidence as evidence
from . import host_capabilities, host_native, command_runtime, workflow_local, workflow_approval
from . import workspace_binding
from .context_delivery import observed, operation

MAINTENANCE_MAX_PATHS = 1024
MAINTENANCE_MAX_BYTES = 256 * 1024


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

    def can_seal(self, state: dict[str, Any]) -> bool:
        from . import worker_runtime as workers
        # Direct continuation/diagnostic callers stay read-only. Mutation owners
        # separately persist refreshed evidence before relying on quiescence.
        return super().can_seal(state) and workers.launch_denials_verified(state)


def installed_adapter(host: str, profile: str = "native_workflow") -> HostAdapter:
    w.require(host in {"codex", "claude"} and profile in {"native_workflow", "protected_host"},
              "unsupported_authority", "Unknown workflow host/profile.")
    if profile == "native_workflow":
        return LocalAdapter(host)
    return ClaudeAdapter() if host == "claude" else CodexAdapter()


class Controller:
    def __init__(self, workspace: Path, root: str, adapter: HostAdapter, *, principal: str | None = None):
        self.workspace = (workspace_binding.resolve_workspace(workspace)
                          if adapter.profile == "native_workflow" else workspace.resolve())
        self.root = root
        self.principal = principal or root
        self.adapter = adapter
        adapter.bind(self.workspace, root)

    @contextmanager
    def _locked(self, target: Path) -> Iterator[None]:
        """Serialize cross-root claims before each controller transaction."""
        with ExitStack() as stack:
            if self.adapter.profile == 'native_workflow':
                shared = storage.runtime_file(str(self.workspace), 'workspace-claims')
                stack.enter_context(primitives.file_lock(str(shared)))
            stack.enter_context(primitives.file_lock(str(target)))
            yield

    def availability(self, *, observations: bool = True) -> dict[str, Any]:
        caps = self.adapter.capabilities()
        ready = all(caps.get(k) is True for k in
                    ("protected_store", "human_origin", "tool_containment", "process_tracking"))
        local = self.adapter.profile == "native_workflow"
        return {"profile": self.adapter.profile, "workflow_available": ready or local,
                "authority_verified": ready, "capabilities": caps,
                "native_observations": self.adapter.observations(self.workspace, self.root) if observations else {
                    'status': 'not_requested', 'authority_verified': False,
                    'detail': 'Display-only native discovery is available in the full diagnostic report.'},
                "status": "available" if ready or local else "capability_blocked",
                "detail": "Workflow gates active; host-wide protection unavailable." if local else
                "Host approval protection and origin are verified." if ready else
                "Host approval protection/origin is unverified. Keep human gates; prepare a trusted integration before activation."}

    def _path(self) -> Path:
        if self.adapter.profile == "native_workflow":
            workspace_binding.ensure(self.workspace, worker=self.principal != self.root)
        availability = self.availability(observations=False)
        w.require(availability["workflow_available"], "unsupported_authority", availability["detail"])
        try:
            return self.adapter.validate_path(self.workspace, self.adapter.control_path(self.workspace, self.root))
        except (OSError, ValueError) as exc:
            if isinstance(exc, w.Refusal):
                raise
            raise w.Refusal("state_unavailable", str(exc)) from None

    @observed('controller-storage')
    def _read(self, target: Path) -> dict[str, Any]:
        try:
            self.adapter.validate_path(self.workspace, target)
            w.require(self.adapter.state_exists(), "state_unavailable", "Workflow store is not initialized.")
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                w.require(stat.S_ISREG(operation('source_stat', os.fstat, stream.fileno()).st_mode), "state_unavailable", "Invalid control file.")
                raw = operation('source_read', stream.read, workflow_local.MAX_BYTES + 1,
                                category='source_fingerprints')
                w.require(len(raw.encode()) <= workflow_local.MAX_BYTES, "state_unavailable", "Workflow store is oversized.")
                db = operation('source_parse', json.loads, raw)
            return self._validate_database(db)
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            if isinstance(exc, w.Refusal):
                raise
            raise w.Refusal("state_unavailable", f"Control state cannot be read: {type(exc).__name__}") from None

    def _validate_database(self, db: Any) -> dict[str, Any]:
        """Validate identical identities for normal reads and explicit recovery."""
        try:
            w.require(isinstance(db, dict), 'state_unavailable', 'Control state must be an object.')
            storage.storage_generation(db)
            w.require(isinstance(db, dict) and db.get("schema") == "taskplane.control/v1"
                      and db.get("workspace") == str(self.workspace) and db.get("root") == self.root
                      and db.get("profile", "protected_host") == self.adapter.profile
                      and isinstance(db.get("runs"), dict) and "active" in db,
                      "state_unavailable", "Control state identity or schema is invalid.")
            for key, s in db["runs"].items():
                w.require(s["schema"] in {"taskplane.workflow/v1", "taskplane.workflow/v2"} and s["run"] == key
                          and s["root"] == self.root and s["workspace"] == str(self.workspace)
                          and isinstance(s["revision"], int) and isinstance(s["decisions"], dict)
                          and s["visits"] and 0 <= s["index"] < len(s["visits"]),
                          "state_unavailable", "Invalid stored workflow.")
                w.validate_state(s)
                w.require(s.get("profile", "protected_host") == self.adapter.profile,
                          "state_unavailable", "Stored run belongs to another profile.")
                self.adapter.validate_state(s)
                if self.adapter.profile == "native_workflow" and db["active"] == key:
                    workspace_binding.ensure(self.workspace, worker=self.principal != self.root,
                                             expected=s.get("workspace_contract"))
            w.require(db["active"] is None or db["active"] in db["runs"],
                      "state_unavailable", "Active workflow binding is missing.")
            from .workflow_write_guard import invocation_transport
            admissions = db.get("admissions", {})
            w.require(isinstance(admissions, dict) and len(admissions) <= 512,
                      "state_unavailable", "Invalid admission inventory.")
            for admission in admissions.values():
                w.require(isinstance(admission, dict), "state_unavailable", "Invalid admission record.")
                if "invocation" in admission:
                    if admission['invocation'].get('schema') == 'taskplane.claude-startup-invocation/v1':
                        from .workflow_write_guard import validate_historical_invocation
                        validate_historical_invocation(admission['invocation'])
                    else:
                        invocation_transport(admission['invocation']).validate_record(admission["invocation"])
                    bound = admission["invocation"]["binding"]
                    w.require(all(bound[key] == admission.get(other) for key, other in
                              (("root", "root"), ("actor", "principal"), ("workspace", "workspace"),
                               ("call_id", "call_id"), ("run", "run"))),
                              "state_unavailable", "Invocation differs from its admission identity.")
            from . import workflow_retention
            workflow_retention.validate_index(db)
            return dict(db)
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            if isinstance(exc, w.Refusal):
                raise
            raise w.Refusal("state_unavailable", f"Control state cannot be read: {type(exc).__name__}") from None

    def recover_initialization(self, source: str, expected_sha256: str, run: str,
                               revision: int | None, reference: str) -> dict[str, Any]:
        """Restore exact same-binding bytes from an explicitly selected numbered copy."""
        w.require(self.adapter.profile == "native_workflow" and self.principal == self.root,
                  "unsupported_authority", "Initialization recovery is a native root operation.")
        w.require(isinstance(reference, str) and 0 < len(reference.strip()) <= 512,
                  "invalid_evidence", "Recovery requires the actual user request reference.")
        target = self._path()
        w.require(re.fullmatch(re.escape(target.stem) + r" [1-9][0-9]*\.json", source) is not None
                  and re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is not None,
                  "invalid_evidence", "Select an exact adjacent numbered copy and its SHA-256.")
        w.require(type(revision) is int and revision >= 0 and bool(run),
                  "invalid_evidence", "Recovery needs the expected active run and revision.")

        def read_regular(path: Path, limit: int) -> bytes:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                w.require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "state_unavailable", "Recovery input must be regular.")
                raw = stream.read(limit + 1)
            w.require(len(raw) <= limit, "state_unavailable", "Recovery input exceeds its size bound.")
            return raw

        with self._locked(target):
            marker = storage.runtime_file(str(self.workspace), target.stem + ".initialized.json")
            expected_marker = {"schema": "taskplane.local-initialization/v1", "workspace": str(self.workspace),
                               "root": self.root, "profile": "native_workflow"}
            w.require(marker.exists() and json.loads(read_regular(marker, 4096)) == expected_marker,
                      "state_unavailable", "Recovery requires the existing matching initialization marker.")
            candidate = storage.runtime_file(str(self.workspace), source)
            raw = read_regular(candidate, workflow_local.MAX_BYTES)
            w.require(primitives.content_fingerprint(raw) == expected_sha256,
                      "stale_checkpoint", "Recovery copy checksum changed.")
            db = self._validate_database(json.loads(raw))
            w.require(db["active"] == run and db["runs"][run]["revision"] == revision,
                      "stale_checkpoint", "Recovery copy has a different active run or revision.")
            result = {"schema": "taskplane.initialization-recovery/v1", "workspace": str(self.workspace),
                      "root": self.root, "run": run, "revision": revision, "source": source,
                      "sha256": expected_sha256, "request_reference": reference.strip(),
                      "state_bytes_preserved": True, "approvals_changed": False}
            return workspace_binding.restore_initialization(target, raw, result,
                                                            limit=workflow_local.MAX_BYTES)


    @observed('controller-storage')
    def _write(self, target: Path, db: dict[str, Any]) -> None:
        try:
            self.adapter.validate_path(self.workspace, target)
            from . import workflow_retention
            expected_generation = storage.storage_generation(db)
            compacted = workflow_retention.compact(self.workspace, db)
            raw = storage.encode_controller(compacted)
            w.require(len(raw) <= workflow_local.MAX_BYTES,
                      "state_unavailable", "Active workflow data exceeds its 8 MiB size limit after history retention. "
                      "Inspect flow report storage; explicitly retire or replace obsolete work. History was not deleted.")
            generation = operation('source_write', storage.commit_controller, target, compacted,
                                   expected_generation=expected_generation, locked=True,
                                   limit=workflow_local.MAX_BYTES)
            # A caller can commit an operation reservation and its output in the
            # same locked transaction. Carry the returned generation forward.
            db['storage_generation'] = generation
        except storage.StorageConflict as exc:
            raise w.Refusal('stale_checkpoint', str(exc)) from None
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
            paths = set(scope["paths"].get(phase, [])) if phase else allowed
            w.require(set(row["paths"]) <= paths and set(evidence.task_criteria(row)) <= set(scope["criteria"]),
                      "scope_violation", "Initial task paths and criteria must stay within the requested scope.")
            evidence.validate_read_inputs(self.workspace, scope, row)
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
        contract = (workspace_binding.ensure(self.workspace)
                    if self.adapter.profile == "native_workflow" else None)
        target = self._path()
        with self._locked(target):
            authorized = None
            initialization = None
            if not (cast(workflow_local.LocalWorkflow, self.adapter).state_exists(allow_pending=True)
                    if self.adapter.profile == "native_workflow"
                    else self.adapter.state_exists()):
                w.require(not request.get("replace_run"), "state_unavailable",
                          "No initialized run exists to replace.")
                authorized = self.adapter.verify_start(self.workspace, self.root, request)
                evidence.valid_scope(self.workspace, authorized["scope"])
                self._initial_tasks(request, authorized["scope"])
                # Validate route before creating the durable initialization marker.
                w.new_state(str(self.workspace), self.root, "validate", authorized["scope"],
                            entry=authorized["entry"], standalone=authorized["standalone"])
                if self.adapter.profile == 'native_workflow':
                    initialization = workflow_local.initialize_workspace(
                        self.workspace, self.root, self.adapter.name, authorized['scope'])
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
                              and request.get("scope") in [active.get("requested_scope", active["scope"]), active["scope"]],
                              "stale_checkpoint", "Conflicting run replacement retry.")
                    self._check_start_tasks(active, request)
                    return self._started_result(active)
                w.require(db["active"] == replace_run and previous["revision"] == revision,
                          "stale_checkpoint", "The run or revision selected for replacement changed.")
                self._refresh_denials_locked(db, previous)
                w.require(self.adapter.can_seal(previous), "scope_violation",
                          "Live tool processes must stop before replacing a run.")
                replaced = deepcopy(previous)
            elif db["active"]:
                active = db["runs"][db["active"]]
                w.require((not request.get("request_reference") or request["request_reference"] ==
                           active.get("request_provenance", {}).get("reference"))
                          and (not request.get("scope") or request["scope"] in
                               [active.get("requested_scope", active["scope"]), active["scope"]]),
                          "approval_required", "Another run is active. To start a new scope, explicitly use "
                          "--replace-run and --expected-revision with the new user request reference.")
                self._check_start_tasks(active, request)
                return self._started_result(active)
            authorized = authorized or self.adapter.verify_start(self.workspace, self.root, request)
            evidence.valid_scope(self.workspace, authorized["scope"])
            if self.adapter.profile == 'native_workflow' and initialization is None:
                initialization = workflow_local.initialize_workspace(
                    self.workspace, self.root, self.adapter.name, authorized['scope'])
            s = w.new_state(str(self.workspace), self.root, uuid.uuid4().hex, authorized["scope"],
                            entry=authorized["entry"], standalone=authorized["standalone"])
            s.update(requested_scope=deepcopy(authorized["scope"]),
                     goal=str(authorized.get("goal", request.get("goal", ""))),
                     started_at=datetime.now(timezone.utc).isoformat(), profile=self.adapter.profile,
                     initial_context_tasks=self._initial_tasks(request, authorized["scope"]))
            self.adapter.state_created(s, authorized)
            if initialization is not None:
                s['native_initialization'] = initialization
                from .workflow_collision import require_claims
                require_claims(self.workspace, s)
            if contract:
                s["workspace_contract"] = {k: contract[k] for k in ("project_id", "digest")}
            if replaced is not None:
                s["inherited_findings"] = evidence.carry_findings(replaced)
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
            return self._started_result(s)

    def _started_result(self, state: dict[str, Any]) -> dict[str, Any]:
        """Bind setup observations under the store lock after the run is durable."""
        result = deepcopy(state)
        if self.adapter.profile == "native_workflow":
            try:
                workflow_local.Harness(self.workspace, self.root).bind(state)
            except (OSError, ValueError, TypeError, KeyError, primitives.StateError):
                # Initial handles are already in the committed run. Keep the
                # original setup observations for a binding retry, and report
                # the committed result so callers can close the old interval.
                result["start_observation_errors"] = ["Run started; harness binding observation unavailable."]
        return result

    def _refresh_denials_locked(self, db: dict[str, Any], state: dict[str, Any]) -> None:
        from . import worker_runtime as workers
        if self.adapter.profile != 'native_workflow' or not any(
                workers.is_launch_denial(row) for row in state.get('workers', {}).values()):
            return
        before = deepcopy(state)
        workers.refresh_launch_denials(state)
        if state != before:
            # Persist uncertainty and sticky contradictions even if the caller
            # subsequently refuses scheduling, ownership release or sealing.
            self._write(self._path(), db)

    def can_seal(self, run: str) -> bool:
        """Mutable observation boundary used by Stop; no phase authority granted."""
        target = self._path()
        with self._locked(target):
            db = self._read(target)
            w.require(db['active'] == run, 'state_unavailable', 'Quiescence requires the active run.')
            state = db['runs'][run]
            self._refresh_denials_locked(db, state)
            return self.adapter.can_seal(state)

    def _recover_launch_denied(self, db: dict[str, Any], state: dict[str, Any], grant: str,
                               request: dict[str, Any]) -> dict[str, Any]:
        from . import claude_worker_observations as claude, worker_runtime as workers
        from .context import Store, digest
        from .context_handoff import binding
        row = workers.records(state).get(grant)
        w.require(self.adapter.name == 'claude' and row and row.get('host') == 'claude',
                  'scope_violation', 'Launch-denial recovery requires the exact Claude attempt.')
        assert row is not None
        w.require(set(request) == {'request_reference', 'call_id', 'expected_call_sha256', 'expected_result_sha256'}
                  and isinstance(request.get('request_reference'), str)
                  and 0 < len(request['request_reference'].strip()) <= 512
                  and request.get('call_id') == row.get('call_id') and bool(row.get('call_id'))
                  and all(isinstance(request.get(key), str) and re.fullmatch(r'[0-9a-f]{64}', request[key])
                          for key in ('expected_call_sha256', 'expected_result_sha256')),
                  'invalid_evidence', 'Recovery requires the original call, raw span hashes and actual request reference.')
        w.require(row.get('binding') == binding(state) and row.get('workspace') == str(self.workspace)
                  and row.get('task_digest') == digest(workers.task(state, row['task_id']))
                  and row.get('workspace_contract') == state.get('workspace_contract'),
                  'stale_checkpoint', 'Recovery attempt binding or frozen task changed.')
        w.require(not any(handle['state'] == 'running' for handle in state.get('observed_handles', {}).values()),
                  'scope_violation', 'Known live commands prevent launch-denial recovery.')
        store = Store(self.workspace)
        frozen = store.resolve(row['snapshot'])
        w.require(frozen.get('binding') == row['binding'] and frozen.get('task') == row['task_id']
                  and row.get('input_manifest') == evidence.manifest(self.workspace, list(row['input_manifest']))
                  and row.get('dependency_results') == workers.dependency_results(state,
                      workers.task(state, row['task_id'])['dependencies']),
                  'stale_checkpoint', 'Original frozen worker inputs changed.')
        admissions = [admission for admission in db.get('admissions', {}).values()
                      if admission.get('run') == state['run'] and admission.get('call_id') == row['call_id']]
        w.require(len(admissions) == 1, 'invalid_evidence', 'Original launch admission is missing or ambiguous.')
        admission = admissions[0]
        w.require(all(admission.get(key) == value for key, value in {
            'root': self.root, 'principal': self.root, 'workspace': str(self.workspace), 'host': 'claude',
            'binding': row['binding'], 'input_digest': row.get('dispatch_digest'), 'authority': 'phase_grant',
            'automatic': True}.items()) and admission.get('tool') in {'Agent', 'Task'}
            and row.get('transcript_source') == state.get('claude_transcript_source'),
            'invalid_evidence', 'Original admitted launch differs from its frozen binding.')
        replay = row.get('denial_recovery')
        if replay:
            w.require(replay['request'] == request, 'invalid_evidence', 'Conflicting denial recovery replay.')
            self._refresh_denials_locked(db, state)
            w.require(row['state'] == 'failed' and not row.get('identity_conflict')
                      and row.get('denial_freshness', {}).get('status') == 'launch_denied',
                      'invalid_evidence', 'Recovered denial no longer verifies.')
            return deepcopy(row)
        w.require(row['state'] == 'unknown' and row.get('identity_conflict') is True
                  and not row.get('revoked_at') and not row.get('ended_at') and not row.get('terminal_status')
                  and not row.get('identity_observation') and not workers.denial_has_work(state, row),
                  'scope_violation', 'Recovery requires an unbound legacy denial with no child or work evidence.')
        history = row.get('reconciliation_events', {})
        w.require(isinstance(history, dict) and 0 < len(history) < 128 and not row.get('reconciliation_overflow')
                  and all(item.get('kind') == 'identity_observation'
                          and item.get('value', {}).get('status') in {'conflict', 'not_yet_available', 'unsupported'}
                          and (item['value']['status'] != 'conflict'
                               or item['value'].get('reason') == claude.LEGACY_LAUNCH_CONFLICT)
                          for item in history.values())
                  and any(item['value']['status'] == 'conflict' for item in history.values()),
                  'invalid_evidence', 'Legacy history is ambiguous or contains an independent contradiction.')
        cursor = state.get('claude_transcript_cursor')
        w.require(isinstance(cursor, dict) and claude._valid_seal(cursor, 'taskplane.claude-transcript-cursor/v1')
                  and not cursor.get('conflict'), 'invalid_evidence', 'Original legacy cursor is missing or conflicted.')
        assert isinstance(cursor, dict)
        entry_key = digest(claude._binding(self.root, row))
        progress = row.get('denial_recovery_progress')
        if progress:
            w.require(progress['request'] == request, 'invalid_evidence', 'Conflicting private recovery request.')
            original = store.resolve(progress['before_ref'])
            changing = {'denial_recovery_progress', 'identity_freshness', 'reconciliation_events'}
            w.require(original['cursor']['entries'].get(entry_key) == cursor['entries'].get(entry_key)
                      and original['cursor']['source_sha256'] == cursor['source_sha256']
                      and original['admission'] == admission
                      and {key: value for key, value in original['row'].items() if key not in changing}
                          == {key: value for key, value in row.items() if key not in changing},
                      'invalid_evidence', 'Legacy recovery inputs changed while scanning.')
        else:
            progress = {'request': deepcopy(request), 'before_ref': store.put('claude-denial-recovery-before',
                {'row': deepcopy(row), 'cursor': deepcopy(cursor), 'admission': deepcopy(admission)})}
        answer = claude.reobserve_legacy_denial(self.root, row, source=state['claude_transcript_source'],
            cursor=progress.get('cursor', cursor), expected_call_sha256=request['expected_call_sha256'],
            expected_result_sha256=request['expected_result_sha256'])
        if answer['status'] != 'launch_denied':
            if answer['status'] == 'not_yet_available':
                row['denial_recovery_progress'] = {**progress, 'cursor': answer['cursor']}
                self._write(self._path(), db)
            raise w.Refusal('invalid_evidence', 'Legacy launch denial did not verify: ' + answer['reason'])
        try:
            admitted = datetime.fromisoformat(admission['admitted_at'].replace('Z', '+00:00'))
            prepared = datetime.fromisoformat(row['prepared_at'].replace('Z', '+00:00'))
            denied = datetime.fromisoformat(answer['denied_at'].replace('Z', '+00:00'))
            valid_time = prepared <= admitted <= denied
        except (KeyError, TypeError, ValueError):
            valid_time = False
        w.require(valid_time, 'invalid_evidence', 'Original admission timestamp disagrees with native denial.')
        candidate = deepcopy(row)
        candidate.pop('denial_recovery_progress', None)
        candidate['identity_conflict'] = False
        candidate['state'] = 'launch_pending'
        w.require(workers.record_launch_denial(state, candidate, answer),
                  'invalid_evidence', 'Reobserved denial does not satisfy the runtime failure contract.')
        candidate['denial_recovery'] = {'request': deepcopy(request), 'before_ref': progress['before_ref'],
            'proof_ref': candidate['denial_proof_ref'], 'recovered_at': workers.now(), 'authority': 'none'}
        workers._audit(candidate, 'legacy_launch_denial_recovered', candidate['denial_recovery'])
        state['workers'][grant] = candidate
        committed = deepcopy(answer['cursor'])
        for key, entry in cursor['entries'].items():
            if key != entry_key and committed['entries'].get(key) != entry:
                # Preserve observations made by intervening hooks. Registering
                # additional candidates needs a full scan on their next read.
                committed['entries'][key] = deepcopy(entry)
                committed['offset'] = 0
        state['claude_transcript_cursor'] = claude._sealed(committed)
        return deepcopy(candidate)

    def report(self, run: str | None = None, *, diagnostics: bool = True) -> dict[str, Any]:
        """Read current authority; hooks omit expensive presentation-only diagnostics.

        Evidence drift and database validation always run. Worker scheduling/context
        reconstruction and storage inventory belong to explicit status requests, not
        every tool's admission path. Required worker checks still run in _guard.
        """
        availability = self.availability(observations=diagnostics)
        if not availability["workflow_available"]:
            return availability
        target = self._path()
        with self._locked(target):
            if not self.adapter.state_exists():
                w.require(run is None, "state_unavailable", "Requested run has no local workflow binding.")
                return {**availability, "status": "no_workflow"}
            db = self._read(target)
            key = run or db["active"]
            if key is None:
                from . import workflow_retention
                return {**availability, "status": "no_workflow",
                        **({"storage": workflow_retention.capacity(db, workflow_local.MAX_BYTES, self.workspace)}
                           if diagnostics else {})}
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
                s["evidence_drift"] = {"visit": changed[0], "reason": changed[1]}
            details = {**self.adapter.decorate(s),
                       "storage": workflow_retention.capacity(db, workflow_local.MAX_BYTES, self.workspace)} if diagnostics else {}
            if diagnostics and self.adapter.profile == "native_workflow":
                from . import state_import
                recovered = state_import.record(self.workspace, self.root)
                if recovered:
                    details["state_import"] = recovered
            return {**s, **availability, **details, "phase": w.current(s)["phase"], "archived": archived,
                    "pending_checkpoint": w.binding(s, w.current(s)["packet"])
                        if not s.get("superseded_by") and not s.get("retired") and w.current(s)["decision"] == "awaiting_human_approval" else None,
                    "status": "retired" if s.get("retired") else "superseded" if s.get("superseded_by") else
                              "accepted" if s["finished"] else w.current(s)["decision"]}

    def inspect(self, run: str, kind: str, reference: str, *, offset: int = 0,
                limit: int = 32768) -> dict[str, Any]:
        w.require(self.principal == self.root, "scope_violation", "Recovery inspection belongs to the root.")
        from . import workflow_retention
        target = self._path()
        with self._locked(target):
            state = workflow_retention.read(self.workspace, self._read(target), run)
            return command_runtime.inspect_recovery(self.workspace, state, kind, reference, offset, limit)

    def prevalidate(self, run: str, *, revision: int | None, output: str, tasks: str) -> dict[str, Any]:
        w.require(self.principal == self.root, "scope_violation", "Prevalidation belongs to the root.")
        target = self._path()
        with self._locked(target):
            db = self._read(target)
            w.require(db["active"] == run, "state_unavailable", "Prevalidation needs the active run.")
            state = db["runs"][run]
            w.require(state["revision"] == revision, "stale_checkpoint", "Expected state revision changed.")
            self._refresh_denials_locked(db, state)
            drift = evidence.changed(self.workspace, state, skip_current=True)
            w.require(not drift, "stale_checkpoint", str(drift))
            self.adapter.before_action(state, "submit")
            w.require(self.adapter.can_seal(state), "scope_violation", "Live work prevents prevalidation.")
            packet = evidence.prevalidate(self.workspace, state, output, tasks)
            return {"valid": True, "authority": "none", "run": run, "revision": revision,
                    "phase": packet["phase"], "visit": packet["visit"], "manifest": packet["manifest"]}

    def context(self, run: str | None = None, *, task: str | None = None,
                 read: str | None = None,
                 page: int = 0, section: str | None = None) -> dict[str, Any]:
        """Current-binding derived data only; never writes a workflow decision."""
        snapshot = self.report(run, diagnostics=False)
        w.require(snapshot.get("run") and not snapshot.get("invalidation_pending"),
                  "invalid_context", "Repair the current workflow binding before consuming context.")
        target = self._path()
        with self._locked(target):
            db = self._read(target)
            selected = snapshot["run"]
            w.require(selected and db["active"] == selected
                      and db["runs"][selected]["revision"] == snapshot["revision"],
                      "invalid_context", "Context requires the unchanged active run.")
            return self._context_locked(db, selected, task=task, read=read, page=page, section=section)

    def _context_locked(self, db: dict[str, Any], run: str, *, task: str | None = None,
                        read: str | None = None, page: int = 0,
                        section: str | None = None) -> dict[str, Any]:
        from .context_handoff import Brief
        from . import worker_runtime as workers
        state = db["runs"][run]
        worker = workers.find(state, self.principal) if self.principal != self.root else None
        if self.principal != self.root:
            w.require(worker and worker.get("claimed_at") and task == worker["task_id"],
                      "scope_violation", "Worker context requires its observed own task identity.")
            assert worker is not None
            workers.current(state, worker)
            session = workers.worker_session(self.workspace, state, worker)
        else:
            session = Brief(self.workspace, state, task)
        return session.read(read, page, section) if read else session.descriptor()

    def update_tasks(self, run: str, revision: int | None, tasks: str) -> dict[str, Any]:
        w.require(self.principal == self.root, "scope_violation", "Only the root can publish task definitions.")
        target = self._path()
        with self._locked(target):
            db = self._read(target)
            w.require(db["active"] == run, "stale_checkpoint", "Task publication requires the active run.")
            state = db["runs"][run]
            self._refresh_denials_locked(db, state)
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
            previous = deepcopy(state)
            state["task_context"] = {"visit": w.current(state)["id"], "tasks": frozen, "base_revision": revision}
            if self.adapter.profile == 'native_workflow':
                from .workflow_collision import require_claims
                require_claims(self.workspace, state, previous)
            state["task_generation"] = state.get("task_generation", 0) + 1
            state["revision"] += 1
            self._write(target, db)
            return deepcopy(state)

    def revise(self, run: str, mode: str, request: dict[str, Any], *,
               preview_digest: str | None = None, note: str | None = None) -> dict[str, Any]:
        """Durable same-run revision intent; returning to Product grants no approval."""
        from .context import Store, digest
        from .workflow_write_guard import revision_preview
        from . import worker_runtime as workers
        w.require(self.principal == self.root, 'scope_violation', 'Only the original root can revise requirements.')
        w.require(self.adapter.profile == 'native_workflow' and self.adapter.name in {'codex', 'claude'},
                  'unsupported_authority', 'Requirements revision needs a supported cooperative native adapter.')
        w.require(mode in {'preview', 'begin', 'resume', 'abort'} and isinstance(request, dict)
                  and isinstance(request.get('request_id'), str), 'invalid_evidence', 'Invalid revision operation.')
        target = self._path()
        with self._locked(target):
            db = self._read(target)
            w.require(db['active'] == run and run in db['runs'], 'stale_checkpoint', 'Revision needs the exact active run.')
            state = db['runs'][run]
            prior = state.get('requirements_revisions', {}).get(request['request_id'])
            if prior:
                w.require(prior['request_digest'] == digest(request), 'invalid_evidence', 'Conflicting revision request ID.')
                w.require(preview_digest in (None, prior['preview']['preview_digest']),
                          'stale_checkpoint', 'Revision preview differs from the committed intent.')
                w.require(mode != 'abort' or prior['status'] != 'completed', 'approval_required',
                          'The revision already completed review; changing it requires a new explicit revision.')
                if (mode in {'preview', 'begin'} or prior['status'] == 'completed'
                        or mode == 'resume' and prior['status'] == 'review' and prior['opened']):
                    return deepcopy(prior['result'])
            else:
                w.require(mode in {'preview', 'begin'}, 'invalid_evidence', 'Begin a revision before resuming or aborting it.')
            w.require(evidence.changed(self.workspace, state) is None, 'stale_checkpoint',
                      'Accepted evidence changed; revision cannot bless that drift.')
            self.adapter.before_action(state, 'revise')
            updated = deepcopy(state)
            if prior is None:
                proposal = revision_preview(self.workspace, state, request)
                if mode == 'preview':
                    return proposal
                w.require(preview_digest == proposal['preview_digest'], 'stale_checkpoint',
                          'Revision source, closure or preview changed before begin.')
                basis = Store(self.workspace).put('requirements-revision-basis', state)
                event: dict[str, Any] = {'schema': 'taskplane.requirements-revision-event/v1',
                    'request_digest': digest(request), 'request': deepcopy(request), 'preview': proposal,
                    'basis': basis, 'status': 'pending', 'history': [], 'opened': False}
                updated.setdefault('requirements_revisions', {})[request['request_id']] = event
                updated['active_requirements_revision'] = request['request_id']
                updated['dispatch_paused'] = True
                for key in proposal['affected_grants']:
                    row = updated['workers'][key]
                    if row['state'] in workers.LIVE:
                        row['stop_requested'] = {'revision_id': request['request_id'],
                                                 'request_reference': request['request_reference']}
                        row['cancel_requested'] = True
                        row['control_epoch'] = row.get('control_epoch', 0) + 1
                workflow_approval.suspend(updated, 'Requirements revision requires fresh human phase acceptance.')
            else:
                event = updated['requirements_revisions'][request['request_id']]
            if mode == 'abort':
                w.require(isinstance(note, str) and 0 < len(note.strip()) <= 2048,
                          'invalid_evidence', 'Abort needs its actual reason.')
                if event['status'] == 'aborted':
                    w.require(event.get('abort_reason') == note, 'invalid_evidence', 'Conflicting abort retry.')
                    return deepcopy(event['result'])
                event.update(status='aborted', abort_reason=note)
                updated['dispatch_paused'] = True
            else:
                if event['status'] == 'aborted':
                    # Resume continues the same review. It never revives stopped
                    # worker authority or silently restores old requirements.
                    event['history'].append({'resumed_after_abort': event['abort_reason']})
                    event['status'] = 'review' if event['opened'] else 'pending'
                live = [key for key, row in workers.records(updated).items() if row['state'] in workers.LIVE]
                handles = [key for key, row in updated.get('observed_handles', {}).items() if row['state'] == 'running']
                awaiting_results = [key for key, row in workers.records(updated).items()
                    if row['state'] == 'result_pending' and not row.get('result_rejection')
                    and row['task_id'] not in event['preview']['tasks']]
                if not event['opened'] and workers.joined(updated) and not handles and not awaiting_results:
                    # Unaffected attempts were allowed to finish in their original
                    # Build. Only genuine quiescence opens the new review route.
                    # Recheck the original requested closure against current
                    # source evidence; waiting workers may have changed edges.
                    basis = Store(self.workspace).resolve(event['basis'])
                    opening = revision_preview(self.workspace, basis, event['request'])
                    closure_fields = ('criteria', 'affected_criteria', 'tasks', 'files', 'modules',
                                      'checks', 'scope', 'requirements')
                    w.require(all(opening[key] == event['preview'][key] for key in closure_fields),
                              'stale_checkpoint', 'The source dependency closure changed while the revision waited. '
                              'The existing intent remains fenced; a newly reviewed revision is required.')
                    w.require(opening.get('artifact_manifest', {}) == event['preview'].get('artifact_manifest', {}),
                              'stale_checkpoint', 'Declared evidence artifacts changed while the revision waited. '
                              'The existing intent remains fenced; a newly reviewed revision is required.')
                    event['opening_preview'] = opening
                    event['completion_basis'] = Store(self.workspace).put('requirements-revision-basis', updated)
                    event['retained_results'] = sorted(key for key in updated.get('task_results', {})
                        if key not in event['preview']['tasks'])
                    for old in updated['visits']:
                        old['superseded'] = True
                    new_visits = [w.visit(phase) for phase in w.PHASES]
                    for visit in new_visits:
                        visit['requirements_revision'] = request['request_id']
                    updated['index'] = len(updated['visits'])
                    updated['visits'].extend(new_visits)
                    updated['scope'] = deepcopy(event['preview']['scope'])
                    updated['revision'] += 1
                    updated['requirements_epoch'] = updated.get('requirements_epoch', 0) + 1
                    epochs = updated.setdefault('criterion_epochs', {})
                    for criterion in event['preview']['criteria']:
                        epochs[criterion] = epochs.get(criterion, 0) + 1
                    task_epochs = updated.setdefault('task_epochs', {})
                    for task_id in event['preview']['tasks']:
                        task_epochs[task_id] = task_epochs.get(task_id, 0) + 1
                    updated.pop('task_context', None)
                    updated.pop('parent_hook_readiness', None)
                    event.update(status='review', opened=True)
            updated['storage_generation'] = updated.get('storage_generation', 0) + 1
            event['result'] = {'schema': 'taskplane.requirements-revision-result/v1',
                'run': run, 'request_id': request['request_id'], 'status': event['status'],
                'revision': updated['revision'], 'storage_generation': updated['storage_generation'],
                'phase': w.current(updated)['phase'], 'approvals_created': 0,
                'dispatch_paused': updated['dispatch_paused'],
                'unresolved_grants': sorted(key for key, row in workers.records(updated).items() if row['state'] in workers.LIVE),
                'awaiting_result_acceptance': sorted(key for key, row in workers.records(updated).items()
                    if row['state'] == 'result_pending' and not row.get('result_rejection')
                    and row['task_id'] not in event['preview']['tasks']) if not event['opened'] else [],
                'affected_tasks': event['preview']['tasks'],
                'retained_results': event.get('retained_results', event['preview']['retained_results']),
                'retained_result_status': 'Historical contributions; current fingerprints must validate before reuse.',
                'next_action': 'Accept fresh Product, Design and Plan checkpoints before new implementation.'
                    if event['status'] == 'review' else 'Observe genuine termination and accept or reject pending unaffected results, then resume this exact revision.'
                    if event['status'] == 'pending' else 'Resume this review explicitly; stopped grants remain fenced.'}
            event['history'].append({'operation': mode, 'storage_generation': updated['storage_generation']})
            from .workflow_collision import require_claims
            require_claims(self.workspace, updated, state)
            db['runs'][run] = updated
            self._write(target, db)
            return deepcopy(event['result'])



    def _claim_locked(self, db: dict[str, Any], run: str, grant: str) -> dict[str, Any]:
        from . import worker_runtime as workers
        state = db["runs"][run]
        row = workers.records(state).get(grant)
        w.require(row and self.principal != self.root and row.get("worker_id") == self.principal,
                  "scope_violation", "Claim requires an observed native identity; prompt or parent alone is insufficient.")
        assert row is not None
        workers.current(state, row)
        workers.require_execution(row)
        w.require(row["state"] in {"bootstrapping", "running"} and not row.get("identity_conflict")
                  and not row.get("startup_handbacks"),
                  "scope_violation", "Worker is not live or has conflicting identity.")
        row.setdefault("claimed_at", workers.now())
        row["state"] = "running"
        result = {"grant_id": grant, "task_id": row["task_id"],
                  "context": workers.worker_session(self.workspace, state, row).descriptor()}
        self._write(self._path(), db)
        return result



    def worker(self, run: str, operation: str, *, revision: int | None = None,
               task: str = "", grant: str = "", request: dict[str, Any] | None = None) -> dict[str, Any]:
        from . import worker_runtime as workers
        w.require(self.adapter.profile == "native_workflow", "unsupported_authority", "Worker adapter is cooperative native_workflow only.")
        required_host = workers.RECOVERY_HOSTS.get(operation)
        if required_host and required_host != self.adapter.name:
            raise w.Refusal('unsupported_authority',
                f'{operation} is unavailable for {self.adapter.name}; it requires {required_host} native evidence. '
                'Inspect flow worker --operation status before offering recovery. '
                + workers.recovery_capabilities(self.adapter.name)['next_action'])
        target = self._path()
        with self._locked(target):
            db = self._read(target)
            w.require(db["active"] == run, "stale_checkpoint", "Worker operation needs the active run.")
            state = db["runs"][run]
            request = request or {}
            row: dict[str, Any] | None
            owner = workers.RECOVERY_HOSTS.get(operation)
            w.require(owner is None or owner == self.adapter.name, 'unsupported_authority',
                      f'{operation} requires the {owner} native adapter; this host cannot supply that authority.')
            if operation == "claim":
                return self._claim_locked(db, run, grant)
            w.require(self.principal == self.root, "scope_violation", "Workers cannot schedule or accept task results.")
            if operation == "status":
                prior_budgets = deepcopy(state.get('retry_budgets'))
                workers.refresh_result_budgets(self.workspace, state)
                if state.get('retry_budgets') != prior_budgets:
                    self._write(target, db)
                return {**workers.summary(state, self.workspace),
                        'recovery': workers.recovery_capabilities(self.adapter.name)}
            w.require(type(revision) is int and revision == state["revision"], "stale_checkpoint", "Expected worker revision changed.")
            w.require(w.current(state)["decision"] in {"not_requested", "changes_requested", "rejected"}
                      and not state.get("finished") and not state.get("retired"), "approval_required", "Current phase is sealed or inactive.")
            w.require(evidence.changed(self.workspace, state) is None, "stale_checkpoint", "Accepted evidence changed.")
            self._refresh_denials_locked(db, state)
            # Verified revocation accepts no source changes. Drift must continue
            # to block new work, but cannot prevent retiring a missed launch.
            if operation != 'recover-unbound':
                self.adapter.before_action(state, "worker")
            if operation == "prepare":
                try:
                    row = workers.prepare(self.workspace, state, task, request, host=self.adapter.name)
                except workers.BudgetRefusal:
                    self._write(target, db)
                    raise
                result: dict[str, Any] = {"grant": deepcopy(row), "message": workers.dispatch_message(state, row)}
            elif operation == 'recover-launch-denied':
                replay = bool(workers.records(state).get(grant, {}).get('denial_recovery'))
                try:
                    result = self._recover_launch_denied(db, state, grant, request)
                except w.Refusal:
                    raise
                except (KeyError, TypeError, ValueError, IndexError):
                    raise w.Refusal('invalid_evidence', 'Malformed retained launch-denial recovery evidence.') from None
                if replay:
                    return result
            elif operation == 'capacity':
                limit = workers.capacity(request.get('capacity'), host=self.adapter.name)
                state['worker_capacity'] = {**request['capacity'], 'effective_limit': limit,
                                            'observed_at': workers.now(),
                                            'binding': {'run': state['run'], 'visit': w.current(state)['id']}}
                result = workers.summary(state, self.workspace)
            elif operation == "accept-result":
                result = workers.accept_result(self.workspace, state, task, {**request, **({"grant": grant} if grant else {})})
            elif operation == 'reject-result':
                row = workers.records(state).get(grant)
                w.require(row and row['state'] == 'result_pending' and (not task or row['task_id'] == task)
                          and row.get('terminal_status') in {'completed', 'idle'}, 'invalid_evidence',
                          'Only a genuinely joined pending native result may be rejected.')
                assert row is not None
                workers.current(state, row)
                w.require(set(request) == {'request_reference', 'reason'}
                          and all(isinstance(request.get(k), str) and 0 < len(request[k].strip()) <= bound
                                  for k, bound in [('request_reference', 512), ('reason', 2048)]),
                          'invalid_evidence', 'Result rejection needs the exact request reference and review reason.')
                w.require(not any(h.get('state') == 'running' and h.get('worker_id') == row.get('worker_id')
                                  for h in state.get('observed_handles', {}).values()),
                          'scope_violation', 'Worker commands remain live.')
                prior = row.get('result_rejection')
                w.require(prior is None or prior == request, 'invalid_evidence', 'Conflicting result rejection retry.')
                retained = workers.retain_rejection(self.workspace, state, row, request)
                row['result_rejection'] = deepcopy(request)
                result = {**(retained or {}), 'status': 'rejected', 'grant': grant, 'task': row['task_id'],
                          'terminal_status': row['terminal_status'], 'reason': request['reason'], 'accepted': False}
                if prior:
                    return result
            elif operation == 'reconcile-handback':
                row = workers.records(state).get(grant)
                failed_startup = bool(row and row.get('state') == 'failed'
                    and row.get('terminal_status') == 'failed'
                    and row.get('handback', {}).get('status') == 'startup_failed'
                    and row.get('startup_handbacks'))
                w.require(row and row.get('host') == 'claude' and row.get('worker_id')
                          and (row.get('state') in {'running', 'bootstrapping', 'result_pending', 'accepted'}
                               or failed_startup)
                          and (not task or task == row.get('task_id')) and not request,
                          'invalid_evidence', 'Select an exact owned Claude handback grant; no supplied proof or overrides are accepted.')
                assert row is not None
                workers.current(state, row)
                if row['state'] != 'accepted':
                    fresh = workers.observe_claude_launches(state, [row], {})
                    failed_startup = bool(row.get('state') == 'failed' and row.get('terminal_status') == 'failed'
                        and row.get('handback', {}).get('status') == 'startup_failed' and row.get('startup_handbacks'))
                    w.require(fresh[0]['status'] == 'matched'
                              and (row['state'] == 'result_pending' and row.get('handback', {}).get('status') == 'delivered'
                                   or failed_startup)
                              and fresh[0].get('completion') == row['handback'].get('notification')
                              and not row.get('identity_conflict') and not row.get('completion_conflict'),
                              'worker_readiness', 'Recorded handback cannot yet be joined. Preserve the attempt; '
                              'native launch, original report, acknowledgment, stop and delivered completion must all agree.')
                result = {'status': 'reconciled', 'grant': grant, 'task': row['task_id'],
                          'state': row['state'], 'result_accepted': row['state'] == 'accepted',
                          'next_action': 'accept-result with independent output checks' if row['state'] == 'result_pending'
                                         else 'Failed startup retained; prepare a fresh attempt through normal retry validation.' if failed_startup
                                         else 'Existing accepted result preserved'}
            elif operation == "abandon":
                row = workers.records(state).get(grant)
                w.require(row and row["state"] == "prepared" and row["call_id"] is None,
                          "scope_violation", "Only an unlaunched reservation can be abandoned.")
                assert row is not None
                row.update(state="failed", ended_at=workers.now(), terminal_status="not_launched")
                result = deepcopy(row)
            elif operation == 'recover-unbound':
                row = workers.records(state).get(grant)
                w.require(self.adapter.name == 'codex' and row and row.get('state') == 'prepared'
                          and all(row.get(k) is None for k in
                                  ('call_id', 'worker_id', 'claimed_at', 'context_receipt', 'dispatch_digest', 'revoked_at'))
                          and not row.get('events') and not row.get('canonical_name')
                          and not any(r.get('grant') == grant for r in state.get('task_results', {}).values()),
                          'scope_violation', 'Recovery needs a prepared Codex grant with no admitted launch, claim or result.')
                assert row is not None
                workers.current(state, row)
                reference = request.get('request_reference')
                launch_call, terminal_call = request.get('launch_call_id'), request.get('terminal_call_id')
                w.require(isinstance(reference, str) and 0 < len(reference.strip()) <= 512
                          and isinstance(launch_call, str) and isinstance(terminal_call, str),
                          'invalid_evidence', 'Preserve the actual user request and native launch and terminal references.')
                assert isinstance(reference, str) and isinstance(launch_call, str) and isinstance(terminal_call, str)
                w.require(set(request) <= {'request_reference', 'launch_call_id', 'terminal_call_id', 'restart'}
                          and type(request.get('restart', False)) is bool, 'invalid_evidence',
                          'Recovery accepts references and explicit restart only; cursors are controller-owned.')
                w.require(not any(r['state'] == 'running' for r in state.get('observed_handles', {}).values()),
                          'scope_violation', 'Known live commands must stop before unbound-worker recovery.')
                continuation = None if request.get('restart') else row.get('recovery_cursor')
                proof = host_capabilities.unbound_worker_observation(self.root, row, launch_call, terminal_call,
                                                                     cursor=continuation)
                if proof.get('status') == 'incomplete':
                    row['recovery_cursor'] = proof['cursor']
                    row['recovery_progress'] = {'request_reference': reference.strip(),
                                                'operation': proof['operation'], 'at': workers.now()}
                    self._write(target, db)
                    return {k: v for k, v in proof.items() if k != 'cursor'} | {
                        'grant': grant, 'state': row['state'], 'next_action': 'Repeat recover-unbound with the same references.'}
                # Preserve missing admission fields. Revocation cannot manufacture a successful dispatch.
                host_capabilities.revalidate_recovery_snapshots(proof['scan']['snapshots'])
                row.update(state='failed', terminal_status='unadmitted_launch_revoked', revoked_at=workers.now(),
                           recovery={**proof, 'request_reference': reference.strip()})
                row['events']['recovery/' + terminal_call] = 'unadmitted_launch_revoked'
                row.pop('recovery_cursor', None)
                result = deepcopy(row)
            elif operation == 'recover-unavailable':
                row = workers.records(state).get(grant)
                reference, call = request.get('request_reference'), request.get('call_id')
                w.require(self.adapter.profile == 'native_workflow' and self.adapter.name == 'codex'
                          and row and row.get('state') == 'cancel_requested' and row.get('worker_id'),
                          'scope_violation', 'Recovery needs a current native worker with an observed interruption request.')
                assert row is not None
                workers.current(state, row)
                w.require(isinstance(reference, str) and 0 < len(reference.strip()) <= 512
                          and isinstance(call, str), 'invalid_evidence', 'Preserve the actual user recovery request and native call ID.')
                assert isinstance(reference, str) and isinstance(call, str)
                w.require(not any(r['state'] == 'running' for r in state.get('observed_handles', {}).values()),
                          'scope_violation', 'Known live commands must stop before unavailable-worker recovery.')
                from .host_capabilities import unavailable_worker_observation
                proof = unavailable_worker_observation(self.root, row, call)
                # Revoke an unavailable grant, never claim task success or process exit.
                row.update(state='failed', terminal_status='unavailable', revoked_at=workers.now(),
                           recovery={**proof, 'request_reference':reference.strip()})
                row['events']['recovery/'+call] = 'unavailable'
                result = deepcopy(row)
            else:
                raise w.Refusal("invalid_evidence", "Unknown worker operation.")
            state["worker_sequence"] = state.get("worker_sequence", 0) + 1
            self._write(target, db)
            return result

    def _retain_repair_context(self, previous: dict[str, Any], updated: dict[str, Any]) -> None:
        """Pin once at repair entry; current references avoid historical replay."""
        from .context import Store
        store = Store(self.workspace)
        basis = store.put('scoped-repair-basis', previous)
        for task_id, result in updated.get('task_results', {}).items():
            updated.setdefault('retained_task_authority', {}).setdefault(task_id,
                {'reference': basis, 'result_digest': primitives.content_fingerprint(result)})
        updated['history'][-1]['repair_basis'] = basis
        snapshot = evidence._verification_packet(previous).get('packet', {}).get('verification')
        verification = store.put('repair-verification-basis', snapshot) if snapshot else None
        updated.setdefault('request_provenance', {})['repair'] = {
            'scope': deepcopy(updated['repair_scope']), 'verification_basis': verification,
            'retained_results': deepcopy(updated.get('retained_task_authority', {})),
            'rule': 'Retry only missing, failed or invalidated tasks and their affected checks; retain all unaffected results, evidence and approvals, without restarting earlier phases.'}

    def apply(self, action: str, run: str, *, expected_revision: int | None = None,
              output: str = "", tasks: str = "", phase: str = "", native_reference: str = "",
              assessment_json: str | None = None) -> dict[str, Any]:
        w.require(self.principal == self.root, "scope_violation", "Workers cannot change workflow control state.")
        w.require(assessment_json is None or action == "auto-decide", "invalid_evidence",
                  "Inline assessment applies only to auto-decide.")
        target = self._path()
        with self._locked(target):
            db = self._read(target)
            w.require(db["active"] == run or action == 'finish' and run in db['runs']
                      or action == 'repair' and db['active'] is None and run in db['runs'],
                      "state_unavailable", "Mutation must address the bound active run.")
            s = db["runs"][run]
            revision_event = s.get('requirements_revisions', {}).get(s.get('active_requirements_revision'), {})
            w.require(action == 'retire' or revision_event.get('status') not in {'pending', 'aborted'},
                      'approval_required', 'Requirements revision is pending or aborted; resume it before phase mutations.')
            if action != 'retire':
                w.require(not s.get("retired"), "state_unavailable", "Retired workflows have no active grants.")
                w.require(not s.get("superseded_by"), "state_unavailable",
                          "This run was replaced; its evidence is historical and its grants are revoked.")
            if action != 'decide':
                self._refresh_denials_locked(db, s)
            if action == 'repair':
                try:
                    request = json.loads(native_reference)
                except (TypeError, ValueError):
                    raise w.Refusal('invalid_evidence', 'Repair needs a decision JSON object and affected task IDs.') from None
                w.require(isinstance(request, dict) and isinstance(request.get('decision'), dict),
                          'invalid_evidence', 'Repair needs the actual human decision and affected task IDs.')
                previous = s.get('repair_scope', {}).get('direct_request')
                if (previous and request['decision'].get('event_id') == previous['decision']['event_id']):
                    verified = self.adapter.verify_decision_context(json.dumps(request['decision']),
                        previous['decision']['binding'], {previous['decision']['event_id']: previous['decision']})
                    w.require(verified == previous['decision'] and request.get('tasks') == previous['tasks']
                              and expected_revision == verified['binding']['revision'],
                              'stale_checkpoint', 'Conflicting repair retry; inspect the existing repair without restarting it.')
                    self.adapter.before_action(s, 'repair')
                    return deepcopy(s)
                w.require(request['decision'].get('event_id') not in s['decisions'],
                          'decision_binding', 'An earlier phase decision cannot authorize a new repair.')
                packet = w.current(s).get('packet')
                w.require(packet and expected_revision == s['revision'] and self.adapter.can_seal(s),
                          'stale_checkpoint', 'Repair needs the current quiescent checkpoint.')
                assert packet is not None
                verified_repair = self.adapter.verify_decision_context(json.dumps(request['decision']),
                    w.binding(s, packet), s['decisions'])
                w.require(verified_repair.get('binding') == w.binding(s, packet),
                          'decision_binding', 'Repair needs a decision bound to the current checkpoint.')
                updated = w.repair(s, request.get('tasks'), verified_repair)
                updated['repair_scope']['direct_request'] = {
                    'decision': deepcopy(verified_repair), 'tasks': deepcopy(request['tasks'])}
                self.adapter.before_action(s, 'repair')
                self._retain_repair_context(s, updated)
                if self.adapter.profile == 'native_workflow':
                    from .workflow_collision import require_claims
                    require_claims(self.workspace, updated, s)
                db['runs'][run], db['active'] = updated, run
                self._write(target, db)
                return deepcopy(updated)
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
            if replay and self.adapter.profile == "native_workflow" and verified.get("human") is True:
                w.require(expected_revision == verified["binding"]["revision"], "stale_checkpoint",
                          "A decision replay must repeat the original decision revision.")
            policy_replay = action == "policy" and policy_request.get("event_id") in s.get("policy_events", {})
            w.require(replay or policy_replay or expected_revision == s["revision"], "stale_checkpoint", "Expected state revision changed.")
            if s.get("cancellation"):
                w.require(replay, "approval_required", "This run was cancelled; an explicit new run is required.")
                return w.decide(s, verified)  # Exact retries retain cancellation and write nothing.
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
                if action == 'decide':
                    self._refresh_denials_locked(db, s)
                self.adapter.before_action(s, action)
            if replay:
                return w.decide(s, verified)
            if action in ("advance", "finish", "submit", "auto-decide"):
                w.require(self.adapter.can_seal(s),
                          "scope_violation", "Live tool processes must stop before sealing or revoking a phase grant.")
            if action == 'advance' and phase == 'build' and self.adapter.profile == 'native_workflow':
                from .worker_runtime import parent_readiness
                w.require(parent_readiness(s)['status'] == 'ready', 'worker_readiness',
                          'Build needs an admitted automatic pre/post hook pair for the current session and phase. '
                          'Restore the configured hooks, run an admitted read, then retry; approvals are preserved.')
            first_checkpoint = not any(visit['decision'] == 'approved' for visit in s['visits'][:s['index']])
            if (self.adapter.profile == 'native_workflow' and first_checkpoint and
                    (action == 'submit' or action in {'decide', 'auto-decide'}
                     and verified.get('choice') == 'approved')):
                from .worker_runtime import parent_readiness
                w.require(parent_readiness(s)['status'] == 'ready', 'worker_readiness',
                          'Initialization needs a completed automatic admitted pre/post hook pair '
                          'for this native session, workspace, runtime and phase. Complete an admitted '
                          'read in the selected native workspace, then retry only this checkpoint; '
                          'existing work and approvals are preserved.')
            operations: dict[str, Callable[[], dict[str, Any]]] = {
                "submit": lambda: w.submit(s, evidence.seal(self.workspace, s, output, tasks)),
                "decide": lambda: w.decide(s, verified),
                "auto-decide": lambda: w.decide(s, verified),
                "policy": lambda: workflow_approval.authorize(s, policy_request),
                "advance": lambda: w.advance(s, phase, output_path=output or None),
                "finish": lambda: w.finish(s),
            }
            w.require(action in operations, "invalid_evidence", "Unknown workflow operation.")
            try:
                updated = operations[action]()
            except (TypeError, KeyError, IndexError) as exc:
                raise w.Refusal("invalid_evidence", f"Invalid phase evidence: {type(exc).__name__}") from None
            if updated.get('repair_scope', {}).get('build_visit') != s.get('repair_scope', {}).get('build_visit'):
                self._retain_repair_context(s, updated)
            if self.adapter.profile == 'native_workflow':
                from .workflow_collision import require_claims
                require_claims(self.workspace, updated, s)
            if action == "advance":
                prior_paths = set(s["scope"]["paths"].get(phase, []))
                from .workflow_collision import sealed_paths
                protected = sealed_paths(self.workspace, s) if self.adapter.profile == "native_workflow" else {}
                for relative in (set(updated["scope"]["paths"].get(phase, [])) - prior_paths) | set(w.current(updated).get("artifact_paths", [])):
                    target_path = evidence.path(self.workspace, relative)
                    w.require(not target_path.exists() and relative not in protected, "scope_violation",
                              "Progressive phase artifacts need fresh safe paths; existing or sealed output cannot be overwritten.")
            self.adapter.after_action(updated, action)
            db["runs"][run] = updated
            if updated["finished"] and db["active"] == run:
                db["active"] = None
            self._write(target, db)
            return deepcopy(updated)

    def _invocation_binding(self, db: dict[str, Any], admission: dict[str, Any],
                            command: Any) -> dict[str, Any]:
        from . import worker_runtime as workers
        from .context_handoff import binding
        run = admission["run"]
        w.require(db["active"] == run and admission.get("state") == "admitted"
                  and admission.get("automatic") is True and admission.get("authority") == "phase_grant",
                  "stale_checkpoint", "Invocation admission is no longer current and pending.")
        state = db["runs"][run]
        w.require(admission["binding"] == binding(state) and not state.get("invalidation_pending")
                  and not any(state.get(k) for k in ("retired", "finished", "superseded_by", "cancellation")),
                  "stale_checkpoint", "Invocation belongs to an inactive workflow binding.")
        w.require(evidence.changed(self.workspace, state) is None, "stale_checkpoint", "Invocation evidence changed.")
        runtime = host_capabilities.runtime_identity()
        w.require(admission.get("runtime") == runtime, "stale_checkpoint", "Invocation runtime changed.")
        actor = admission["principal"]
        result = {"kind": "root-context", "workspace": str(self.workspace), "root": self.root,
                  "actor": actor, "call_id": admission["call_id"], "run": run,
                  "visit": w.current(state)["id"], "revision": state["revision"],
                  "binding_sha256": primitives.content_fingerprint(binding(state)),
                  "workspace_contract_sha256": primitives.content_fingerprint(state.get("workspace_contract")),
                  "runtime_sha256": primitives.content_fingerprint(runtime), "task_id": command.task_id,
                  "active": True, "automatic": True,
                  **{k: None for k in ("grant_id", "attempt", "task_generation", "launch_call_id",
                     "launch_evidence_sha256", "task_sha256", "inputs_sha256", "claimed")}}
        if actor == self.root:
            w.require(command.action == "context" and admission.get("root_observed") is True,
                      "unsupported_authority", "Root context requires an automatic observed root actor.")
            return result
        matches = [row for row in workers.records(state).values()
                   if row.get("worker_id") == actor and row.get("host") == "claude"
                   and row.get("binding") == binding(state) and row.get("state") in {"bootstrapping", "running"}
                   and not row.get("revoked_at") and not row.get("identity_conflict")
                   and row.get("identity_observation", {}).get("status") == "matched"]
        w.require(len(matches) == 1, "scope_violation", "Invocation needs one exact live observed Claude attempt.")
        row = matches[0]
        workers.current(state, row)
        if command.action == "worker":
            workers.require_execution(row)
        w.require(row.get("expected_runtime") == runtime, "stale_checkpoint", "Worker runtime changed.")
        w.require(row["input_manifest"] == evidence.manifest(self.workspace, list(row["input_manifest"])),
                  "stale_checkpoint", "Worker invocation inputs changed.")
        result.update(kind="worker-claim" if command.action == "worker" else "worker-context",
                      task_id=row["task_id"], grant_id=row["grant_id"], attempt=row["attempt"],
                      task_generation=state.get("task_generation", 0), launch_call_id=row["call_id"],
                      launch_evidence_sha256=row["identity_observation"]["evidence_sha256"],
                      task_sha256=row["task_digest"], inputs_sha256=primitives.content_fingerprint(row["input_manifest"]),
                      claimed=bool(row.get("claimed_at")))
        return result


    def _issue_invocation(self, db: dict[str, Any], event: dict[str, Any],
                          admission: dict[str, Any] | None) -> None:
        if (self.adapter.profile != "native_workflow" or self.adapter.name != "claude"
                or event.get("hook_event_name") != "PreToolUse"
                or (event.get("tool_name") or event.get("tool")) != "Bash"):
            return
        words = workflow_local.runtime_words(event)
        if len(words) < 4 or words[2] != "flow" or words[3] not in {"worker", "context"}:
            return
        if words[3] == "worker" and self.principal == self.root:
            return
        from . import claude_worker_invocation as invocation
        transport: Any = invocation
        args = event.get("tool_input", {})
        w.require(set(args) <= {"command", "description", "timeout", "run_in_background"}
                  and ("description" not in args or isinstance(args["description"], str))
                  and ("timeout" not in args or type(args["timeout"]) is int and 0 < args["timeout"] <= 600000)
                  and ("run_in_background" not in args or args["run_in_background"] is False),
                  "scope_violation", "Unsupported foreground Claude Bash input schema.")
        w.require(admission is not None and event.get("taskplane_automatic_hook") is True,
                  "unsupported_authority", "Claude invocation requires an automatic call admission.")
        assert admission is not None
        command = transport.parse_command(args.get("command"))
        if self.principal == self.root:
            w.require(event.get("taskplane_claude_root_source") is True,
                      "unsupported_authority", "Root context requires fresh automatic root transcript provenance.")
            admission["root_observed"] = True
        record, rewritten = transport.issue_record(command, args,
            self._invocation_binding(db, admission, command), existing=admission.get("invocation"))
        admission["invocation"] = record
        event["taskplane_updated_input"] = rewritten

    @classmethod
    def invoke_claude(cls, argv: list[str]) -> dict[str, Any]:
        """Resolve a locator before any environment-selected controller; consume under one lock."""
        from . import claude_worker_invocation as invocation
        decode_reference = invocation.decode_reference
        transport: Any = invocation
        command, reference = transport.parse_cli(argv)
        root = decode_reference(reference)
        controller = cls(Path(command.workspace), root, installed_adapter("claude"))
        target = controller._path()
        with controller._locked(target):
            db = controller._read(target)  # Existing validated store only; never initialize.
            matches = [row for row in db.get("admissions", {}).values()
                       if row.get("invocation", {}).get("reference") == reference]
            w.require(len(matches) == 1, "unsupported_authority", "Invocation reference is unknown or foreign.")
            admission = matches[0]
            w.require(admission["invocation"]["state"] == "pending", "scope_violation",
                      "Invocation reference has already been consumed.")
            controller.principal = admission["principal"]
            if controller.principal != controller.root and db["active"] == admission["run"]:
                from . import worker_runtime as workers
                state = db["runs"][admission["run"]]
                grant = admission["invocation"]["binding"]["grant_id"]
                attempt = state.get("workers", {}).get(grant)
                w.require(attempt is not None, "scope_violation", "Invocation attempt is missing.")
                fresh = workers.observe_claude_launches(state, [attempt], {})
                controller._write(target, db)
                w.require(fresh[0]["status"] == "matched", "scope_violation", "Invocation launch proof is not freshly available.")
            current = controller._invocation_binding(db, admission, command)
            def persist(record: dict[str, Any]) -> None:
                admission["invocation"] = record
                controller._write(target, db)
            def dispatch() -> dict[str, Any]:
                if command.action == "worker":
                    return controller._claim_locked(db, command.run, command.grant_id or "")
                options = dict(zip(command.argv[4::2], command.argv[5::2]))
                return controller._context_locked(db, command.run, task=command.task_id,
                    read=options.get("--read"),
                    page=int(options.get("--page", "0")), section=options.get("--section"))
            return cast(dict[str, Any], transport.consume_record(admission["invocation"], command, reference, current,
                                                                 persist=persist, dispatch=dispatch))

    @staticmethod
    def _claude_question_matches(row: dict[str, Any], event: dict[str, Any]) -> bool:
        """Allow only the observed answer enrichment of an unchanged question.

        The digest still covers every originally admitted input field. Answers
        are data, never checkpoint authority; unsupported annotations refuse.
        """
        args = event.get('tool_input')
        if (row.get('host') != 'claude' or event.get('host') != 'claude'
                or row.get('tool') != 'AskUserQuestion' or not isinstance(args, dict)
                or not isinstance(args.get('answers'), dict) or not args['answers']
                or args.get('annotations', {}) != {}):
            return False
        original = {key: value for key, value in args.items() if key not in {'answers', 'annotations'}}
        questions = original.get('questions')
        if (not isinstance(questions, list) or not 1 <= len(questions) <= 4
                or not all(isinstance(question, dict) and isinstance(question.get('question'), str)
                           for question in questions)):
            return False
        names = {question['question'] for question in questions}
        return (len(names) == len(questions) and set(args['answers']) <= names
                and all(isinstance(value, str) and len(value.encode('utf-8')) <= 32768
                        for value in args['answers'].values())
                and primitives.content_fingerprint(original) == row.get('input_digest'))

    @staticmethod
    def _post_matches(row: dict[str, Any], identity: dict[str, Any], event: dict[str, Any]) -> bool:
        from .workflow_write_guard import invocation_transport
        return (all(row.get(k) == v for k, v in identity.items() if k != "input_digest")
                and (invocation_transport(row['invocation']).post_input_matches(row["invocation"], event.get("tool_input", {}))
                     if row.get("invocation") else row.get("input_digest") == identity["input_digest"]
                     or Controller._claude_question_matches(row, event)))

    def claude_actor(self, actor: str, event: dict[str, Any]) -> str:
        """Resolve an actor only through exact admitted native launch evidence."""
        from . import worker_runtime as workers
        from .context_handoff import binding
        target = self._path()
        with self._locked(target):
            db = self._read(target)
            # A late post-hook may close its admitted observation after replacement;
            # it receives no execution grant from the historical worker identity.
            if event.get('hook_event_name') == 'PostToolUse':
                call = event.get('tool_use_id') or event.get('call_id')
                call_identity = self._call(event)
                matches = [row for row in db.get('admissions', {}).values()
                           if call_identity and row.get('principal') == actor and row.get('call_id') == call
                           and row.get('tool') == (event.get('tool_name') or event.get('tool'))
                           and self._post_matches(row, {**call_identity[1], "principal": actor}, event)]
                if len(matches) == 1:
                    return actor
            w.require(db["active"], "scope_violation", "Claude child has no active parent run.")
            state = db["runs"][db["active"]]
            before = deepcopy(state)
            candidates = [row for row in workers.records(state).values()
                          if row.get("host") == "claude" and row.get("call_id")
                          and row.get("worker_id") in {None, actor}]
            w.require(len(candidates) <= 64, "scope_violation", "Claude actor correlation exceeds its bound.")
            workers.observe_claude_launches(state, candidates, event)
            matches = [row for row in candidates if row.get("worker_id") == actor
                       and row.get("identity_observation", {}).get("status") == "matched"
                       and row.get("identity_freshness", {}).get("status") == "matched"
                       and not row.get("identity_conflict") and not row.get("revoked_at")
                       and row.get('binding') == binding(state)
                       and row.get('state') in {'bootstrapping', 'running'}]
            if state != before:
                self._write(target, db)
            w.require(len(matches) == 1, "scope_violation", "Claude child actor needs exact structured native launch proof.")
            workers.current(state, matches[0])
            return actor

    def observe(self, event: dict[str, Any], run: str | None) -> str | None:
        target = self._path()
        with self._locked(target):
            if run is None:
                # Startup and observation use store -> harness order. Recheck the
                # binding here: a pre-run report may predate start's transfer.
                w.require(self.adapter.profile == "native_workflow" and self.principal == self.root,
                          "scope_violation", "Setup observation requires the native root.")
                if not cast(workflow_local.LocalWorkflow, self.adapter).state_exists(allow_pending=True):
                    workflow_local.Harness(self.workspace, self.root).observe_setup(event)
                    return None
            db = self._read(target)
            if run is None:
                run = db["active"]
                if run is None:
                    workflow_local.Harness(self.workspace, self.root).observe_setup(event)
                    return None
            w.require(db["active"] == run, "state_unavailable", "Observation has no active workflow binding.")
            state = deepcopy(db["runs"][run])
            from . import worker_runtime as workers
            if self.adapter.profile == "native_workflow":
                workers.observe(state, event)
            self.adapter.observe_state(event, state)
            if (self.adapter.profile == 'native_workflow' and self.adapter.name == 'claude'
                    and self.principal == self.root and event.get('taskplane_automatic_hook') is True
                    and event.get('hook_event_name') in {'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop'}
                    and w.current(state)['decision'] == 'awaiting_human_approval'
                    and not event.get('taskplane_decision') and not event.get('taskplane_policy')):
                from . import native_approval
                harness = workflow_local.Harness(self.workspace, self.root)
                captured = native_approval.capture(state, host=self.adapter.name,
                    presentation=harness.read().get('presentation') or {}, event=event)
                state['native_approval_capture'] = {key: value for key, value in captured.items() if key != 'decision'}
                if captured['status'] == 'observed':
                    # The verifier independently reloads the pinned source.
                    # Commit only observation metadata before that reread;
                    # no approval is stored until all checks below pass.
                    db['runs'][run] = deepcopy(state)
                    self._write(target, db)
                    packet = w.current(state)['packet']
                    verified = self.adapter.verify_decision_context(json.dumps(captured['decision']),
                        w.binding(state, packet), state['decisions'])
                    if verified.get('choice') not in {'changes_requested', 'rejected', 'cancelled'}:
                        w.require(evidence.changed(self.workspace, state) is None, 'stale_checkpoint',
                                  'Native approval cannot accept changed checkpoint evidence.')
                        self.adapter.before_action(state, 'decide')
                    state = w.decide(state, verified)
                    self.adapter.after_action(state, 'decide')
            if state != db["runs"][run]:
                db["runs"][run] = state
                self._write(target, db)
            return run

    def guard(self, event: dict[str, Any], run: str) -> None:
        from . import worker_runtime as workers
        state = self.report(run, diagnostics=False)
        target = self._path()
        with self._locked(target):
            db = self._read(target)
            w.require(not db["runs"].get(run, {}).get("retired"), "scope_violation", "The retired run has no active grants.")
            w.require(db["active"] == run and db["runs"][run]["revision"] == state["revision"],
                      "stale_checkpoint", "Tool grant changed during admission.")
            self._refresh_denials_locked(db, db['runs'][run])
            candidate = deepcopy(db["runs"][run])
            state.update(candidate)
            try:
                self._guard(event, state)
            except workers.BudgetRefusal:
                if state.get('retry_budgets') != db['runs'][run].get('retry_budgets'):
                    db['runs'][run]['retry_budgets'] = deepcopy(state['retry_budgets'])
                    self._write(target, db)
                raise
            except w.Refusal:
                # A native result before admission is an escaped-launch hazard,
                # not permission to dispatch or proof that no child exists.
                # Retain only that diagnostic barrier, never other guard work.
                changed = False
                retained = deepcopy(db['runs'][run])
                for grant, row in state.get('workers', {}).items():
                    prior = retained.get('workers', {}).get(grant)
                    if (prior and prior.get('state') == 'prepared' and prior.get('call_id') is None
                            and row.get('state') == 'unknown' and row.get('preflight_blocker')):
                        prior.update(state='unknown', preflight_blocker=deepcopy(row['preflight_blocker']))
                        changed = True
                if changed:
                    db['runs'][run] = retained
                    self._write(target, db)
                raise
            # Commit mandatory admission before returning permission to the host.
            for key in ("workers", "worker_sequence", "worker_polls", "worker_controls", "native_automation_dispatch",
                        "retry_budgets", "result_ledgers"):
                if key in state:
                    candidate[key] = state[key]
            admission = self._admission(db, event, candidate, "phase_grant")
            self._issue_invocation(db, event, admission)
            if admission:
                event["taskplane_admission"] = deepcopy(admission)
            changed = candidate != db["runs"][run]
            if changed:
                db["runs"][run] = candidate
            if admission or changed:
                self._write(target, db)

    def _call(self, event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        call = event.get("tool_use_id") or event.get("call_id")
        if not isinstance(call, str) or not call or len(call) > 512:
            return None
        identity = {"root": self.root, "principal": self.principal, "call_id": call,
                    "workspace": str(self.workspace)}
        key = primitives.content_fingerprint(identity)
        return key, {**identity, "tool": event.get("tool_name") or event.get("tool"),
                     "input_digest": primitives.content_fingerprint(event.get("tool_input", {}))}

    def _admission(self, db: dict[str, Any], event: dict[str, Any], state: dict[str, Any],
                   authority: str) -> dict[str, Any] | None:
        call = self._call(event)
        if call is None:
            return None
        from .context_handoff import binding
        key, identity = call
        rows = db.setdefault("admissions", {})
        if key in rows:
            old = rows[key]
            w.require(all(old.get(k) == v for k, v in identity.items()) and old.get("run") == state["run"],
                      "scope_violation", "Conflicting tool call admission.")
            return cast(dict[str, Any], old)
        if len(rows) >= 512:
            # Expiry removes invocation authority, never invents successful completion.
            utc_ns, monotonic_ns = time.time_ns(), time.monotonic_ns()
            for pending in rows.values():
                inv = pending.get("invocation", {})
                expires = inv.get("expires", {})
                if (pending.get("state") == "admitted" and inv.get("state") == "pending"
                        and utc_ns >= expires.get("utc_ns", 2 ** 63)
                        and monotonic_ns >= expires.get("monotonic_ns", 2 ** 63)):
                    pending.update(state="expired", completed_at=datetime.now(timezone.utc).isoformat())
            completed = sorted((k for k, v in rows.items() if v.get("state") in {"completed", "expired"}),
                               key=lambda k: rows[k]["completed_at"])
            w.require(completed, "state_unavailable", "Pending tool call admission capacity exhausted.")
            del rows[completed[0]]
        row = {**identity, "run": state["run"], "binding": binding(state), "authority": authority,
               "host": self.adapter.name,
               "state": "admitted", "admitted_at": datetime.now(timezone.utc).isoformat(),
               "automatic": event.get("taskplane_automatic_hook") is True,
               "runtime": deepcopy(event.get("taskplane_runtime_identity"))}
        rows[key] = row
        return row

    def followup_admission(self, event: dict[str, Any], run: str) -> dict[str, Any] | None:
        w.require(self.principal == self.root, "scope_violation", "Only root follow-up observations are supported.")
        target = self._path()
        with self._locked(target):
            db = self._read(target)
            from . import workflow_retention
            state = workflow_retention.read(self.workspace, db, run)
            w.require(db["active"] is None and (state.get("finished") or state.get("retired")),
                      "scope_violation", "Follow-up observations require a closed run and no active grant.")
            if state.get('finished') and not state.get('retired'):
                from . import native_tool_guard
                tool = event.get('tool_name') or event.get('tool')
                args = event.get('tool_input', {})
                observational = (tool in workflow_local.READ_TOOLS | workflow_local.QUESTION_TOOLS
                    or native_tool_guard.read(str(tool), self.adapter.name)
                    or workflow_local.readonly_command(event)
                    or self.adapter.control_action(event, state)
                    or workflow_local.Harness(self.workspace, self.root).dashboard_opener(event, state))
                if tool == 'write_stdin' and isinstance(args, dict) and args.get('chars', '') in ('', '\x03'):
                    self.adapter.guard_input(event, state)
                    observational = True
                w.require(observational, 'approval_required',
                          'Completed Taskplane work has no mutation grant. Enter flow repair for the affected tasks '
                          'or start the requested new flow; retained results and approvals stay intact.')
            row = self._admission(db, event, state, "observation_only")
            if row:
                self._write(target, db)
                event["taskplane_admission"] = deepcopy(row)
            return deepcopy(row)

    def complete_admission(self, event: dict[str, Any]) -> dict[str, Any] | None:
        call = self._call(event)
        if call is None:
            return None
        # A resumable first start has setup handles but no admission store yet.
        # Validate its pending transaction without mistaking it for corruption.
        if not (cast(workflow_local.LocalWorkflow, self.adapter).state_exists(allow_pending=True)
                if self.adapter.profile == "native_workflow" else self.adapter.state_exists()):
            return None
        key, identity = call
        target = self._path()
        with self._locked(target):
            db = self._read(target)
            row = db.get("admissions", {}).get(key)
            if row is None:
                return None
            w.require(self._post_matches(row, identity, event),
                      "scope_violation", "Completion conflicts with admitted tool call.")
            if row.get("invocation"):
                w.require(row.get("automatic") is True and event.get("taskplane_automatic_hook") is True
                          and row.get("runtime") == event.get("taskplane_runtime_identity")
                          == host_capabilities.runtime_identity(),
                          "stale_checkpoint", "Invocation post hook runtime or provenance changed.")
                row["post_input_representation"] = ("original" if identity["input_digest"]
                    == row["invocation"]["original_input_sha256"] else "rewritten")
            from . import worker_runtime as workers
            observed_state = db["runs"].get(row["run"])
            if observed_state is not None:
                workers.observe_handback(observed_state, row, event)
            if row["state"] == "completed":
                return cast(dict[str, Any], deepcopy(row))
            row.update(state="completed", completed_at=datetime.now(timezone.utc).isoformat())
            from .context_handoff import binding
            state = db["runs"].get(row["run"])
            if (state and row.get('automatic') is True and event.get('taskplane_automatic_hook') is True
                    and row.get('runtime') == event.get('taskplane_runtime_identity')
                    == host_capabilities.runtime_identity()
                    and row.get('binding') == binding(state)):
                proof = {'run': state['run'], 'visit': w.current(state)['id'],
                         'root': state['root'], 'workspace': str(self.workspace),
                         'automatic': True, 'matched_call': row['call_id'], 'runtime': row['runtime']}
                if state.get('native_initialization') is not None:
                    proof['initialization'] = primitives.content_fingerprint(state['native_initialization'])
                if row['principal'] == state['root']:
                    state['parent_hook_readiness'] = proof
                else:
                    child = workers.find(state, row['principal'])
                    if child:
                        child['hook_readiness'] = proof
            self._write(target, db)
            return cast(dict[str, Any], deepcopy(row))



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
        if tool in workers.TOOLS:
            native_host = 'claude' if tool in workers.CLAUDE_TOOLS else 'codex'
            alternative = ('Claude Agent/Task with observed lifecycle tools' if self.adapter.name == 'claude'
                           else 'Codex collaboration tools with observed lifecycle evidence')
            w.require(self.adapter.profile == 'native_workflow' and self.adapter.name == native_host,
                      'unsupported_authority', f'{tool} requires the {native_host.capitalize()} native workflow adapter. '
                      f'Use {alternative} when exposed; otherwise keep required native work blocked. '
                      'No worker admission or approval was granted.')
        if tool in {'mcp__codex_app__open_in_codex', 'mcp__codex_app.open_in_codex'}:
            w.require(self.adapter.name == 'codex', 'unsupported_authority',
                      'Codex dashboard opening is unavailable in this host. Link the current native dashboard '
                      'and record flow present --presentation linked, or blocked with an accessible artifact '
                      'fallback. Only observed rendering supports verified; opening never grants approval.')
        if self.adapter.profile == "native_workflow" and tool in {"Bash", "exec_command"}:
            words = workflow_local.runtime_words(event)
            from . import runtime_command
            try:
                cwd = runtime_command.validate_workdir(words, event, self.workspace)
            except ValueError as exc:
                raise w.Refusal("scope_violation", str(exc)) from None
            mismatch = runtime_command.collision(words, cwd)
            w.require(not mismatch, "scope_violation", mismatch or "")
            if runtime_command.installed(words, cwd):
                try:
                    selected = runtime_command.workspace_selector(words[2:])
                    if selected is not None:
                        w.require(runtime_command.resolve_selection(selected, event, self.workspace) == self.workspace
                                  or self.principal == self.root and
                                  workflow_local.Harness(self.workspace, self.root).recovery_setup(event, state),
                                  "scope_violation", "Runtime command selects another workspace.")
                except ValueError as exc:
                    raise w.Refusal("scope_violation", str(exc)) from None
        worker = None
        if self.principal != self.root:
            worker = workers.find(state, self.principal)
            w.require(worker is not None, "scope_violation", "Unbound native child has no task grant.")
            assert worker is not None
            # Read/control exceptions never confer root authority on a descendant.
            words = workflow_local.runtime_words(event) if tool in {"Bash", "exec_command"} else []
            native_cli = (len(words) >= 3 and (self.workspace/words[1]).resolve() == Path(__file__).with_name('tp.py').resolve())
            if self.adapter.control_action(event, state) or native_cli:
                operation = (words[words.index('--operation') + 1]
                             if '--operation' in words and words.index('--operation') + 1 < len(words) else None)
                w.require(len(words) >= 4 and words[2] == "flow" and words[3] in {"context", "worker"}
                          and (words[3] == "context" or operation == 'claim'),
                          "scope_violation", "Worker control is limited to its observed claim and task context.")
                return
            if tool == "write_stdin" and args.get("chars", "") in ("", "\x03"):
                record = state.get("observed_handles", {}).get(str(args.get("session_id", "")), {})
                if record.get("control"):
                    workers.current(state, worker)
                    w.require(worker["state"] in workers.LIVE, "scope_violation", "Worker control grant has ended.")
                    w.require(record.get("worker_id") == self.principal, "scope_violation",
                              "Worker input belongs to another principal.")
                    self.adapter.guard_input(event, state)
                    return  # Known claim/context output can be drained without reviving the attempt.
            workers.current(state, worker)
            if tool in workers.HANDBACK:
                workers.admit_handback(state, worker, event)
                return
            workers.require_execution(worker)
            w.require(worker["state"] == "running" and worker.get("claimed_at"),
                      "scope_violation", "Worker needs its exact observed live identity.")
            if tool in workers.DISCOVERY:
                workers.admit_discovery(args)
                return  # Discovery grants neither sibling control nor root authority.
            if tool == 'SendMessage':
                raise w.Refusal('unsupported_authority', 'Claude child SendMessage is unavailable in this adapter. '
                                'Use the native final handback or supported startup-failure handback; '
                                'the root can inspect observed status. Nested delegation and sibling control remain unsupported.')
            if tool in workers.MESSAGE:
                parent_name = str(worker.get('canonical_name', '')).rsplit('/', 1)[0]
                w.require(set(args) == {'target', 'message'} and args.get('target') in
                          ({self.root, parent_name} - {''}) and isinstance(args.get('message'), str)
                          and 0 < len(args['message']) <= 32768, 'scope_violation',
                          'Worker messages may only report to their bound parent.')
                return
            w.require(tool not in workers.TOOLS, "scope_violation", "Nested delegation is unsupported.")
        elif tool in workers.HANDBACK:
            raise w.Refusal("scope_violation", "SubagentHandback belongs only to a current bound Claude child.")
        elif self.adapter.profile == "native_workflow" and tool in workers.SPAWN | workers.FOLLOW:
            from .workflow_write_guard import require_runtime
            require_runtime(state)
            if workers.admit(state, event):
                return
        elif self.adapter.profile == "native_workflow" and workers.admit(state, event):
            return
        if self.adapter.profile == "native_workflow" and (
                tool in workflow_local.QUESTION_TOOLS or workflow_local.execution_entry(event)
                or tool == "Skill" and args.get("skill") in {"taskplane:tp-help", "taskplane:tp-status"}):
            return  # Loading a skill or asking the user grants no source write or phase transition.
        if tool in workflow_local.READ_TOOLS:
            return  # Ordinary host access controls apply to reads and searches.
        from . import native_tool_guard
        if self.adapter.profile == 'native_workflow' and native_tool_guard.read(
                str(tool), self.adapter.name):
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
                    w.require(not record.get("control") or record.get("worker_id") in (None, self.root),
                              "scope_violation", "Control input belongs to another principal.")
                    self.adapter.guard_input(event, state)
                    return  # Drain known work across revisions without sending new code.
            revision_event = state.get('requirements_revisions', {}).get(state.get('active_requirements_revision'), {})
            w.require(revision_event.get('status') not in {'pending', 'aborted'}, 'scope_violation',
                      'Requirements revision has fenced root writes; inspect, stop or resume through supported controls.')
            from .workflow_write_guard import require_runtime
            require_runtime(state)
            if workflow_local.bootstrap_write(self.workspace, event, state):
                return  # Fresh recovery scope only; never overwrite sealed evidence.
        w.require(not state.get("invalidation_pending"), "stale_checkpoint",
                  "Evidence drift must revoke the prior phase grant before further writes.")
        w.require(stage["decision"] in ("not_requested", "changes_requested", "rejected", "stale"),
                  "approval_required", "Current output is sealed; resolve the human checkpoint before writing.")
        if self.adapter.profile == 'native_workflow' and tool == native_tool_guard.AUTOMATION:
            self.adapter.before_action(state, 'native-automation')
            if native_tool_guard.automation(event, self.adapter.name, state, root=worker is None):
                return
        allowed = worker["paths"] if worker else w.phase_write_paths(state)
        if worker is None:
            definitions = evidence.context_tasks(state)
            if stage['phase'] == 'build':
                definitions = w.accepted_plan(state)['packet']['output']['task_dag']
            assigned_elsewhere = {path for task in definitions
                if task.get('phase', stage['phase']) == stage['phase'] and task.get('owner') != self.root
                for path in task['paths']}
            allowed = [path for path in allowed if path not in assigned_elsewhere]
        if self.adapter.profile == 'native_workflow' and stage['phase'] == 'build':
            readiness = workers.parent_readiness(state) if worker is None else workers.readiness(worker)
            w.require(readiness['status'] == 'ready', 'worker_readiness',
                      'Preventive hook admission is missing for this session and phase. '
                      'Restore the configured pre/post hooks, run an admitted read, and retry only the pending action. '
                      'Completed work and approvals are preserved.')
        if self.adapter.profile == 'native_workflow':
            from .workflow_collision import sealed_paths
            protected = sealed_paths(self.workspace, state)
            from . import runtime_command
            for target in runtime_command.mutation_targets(event):
                location = Path(target)
                absolute = location if location.is_absolute() else runtime_command.execution_directory(event, self.workspace) / location
                relative = workspace_binding.relative_path(self.workspace, str(absolute))
                owner = protected.get(relative)
                kind = 'owns this working path' if owner and owner.get('kind') == 'working' else 'has sealed this path'
                w.require(not owner, 'scope_violation',
                          f'Another active run {kind}: {relative}; preserve its work and use a distinct path or owner-authorized release. Owner: {owner}')
            allowed = [path for path in allowed if path not in protected]
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
            relative = (workspace_binding.relative_path(self.workspace, value)
                        if self.adapter.profile == "native_workflow" else
                        str(Path(value).relative_to(self.workspace)) if Path(value).is_absolute() and Path(value).is_relative_to(self.workspace) else value)
            evidence.path(self.workspace, relative)
            w.require(relative in allowed, "scope_violation", f"Write is outside current phase scope: {relative}")
