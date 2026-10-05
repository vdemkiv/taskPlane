"""Cooperative native workflow policy, never a protected host authority.

The account running the agent can edit this store or fabricate observations.
Checks enforce the Taskplane API contract and observed hooks, not host isolation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import heapq
import json
import os
import re
from pathlib import Path
import shlex
import stat
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import url2pathname
from typing import Any, cast

from . import primitives, storage, runtime_command, workflow as w, workflow_evidence as evidence

PROFILE = "native_workflow"
MAX_BYTES = 8 * 1024 * 1024
MAX_SOURCE_FILES = 20000
MAX_SOURCE_BYTES = 512 * 1024 * 1024
EXCLUDED = {".git", ".taskplane", ".venv", "venv", "node_modules", "__pycache__",
            ".pytest_cache", ".mypy_cache", ".ruff_cache"}


class DecisionRefusal(w.Refusal):
    """Keep the decision API's refusal reason while identifying its failed contract."""

    def __init__(self, category: str, detail: str):
        self.category = category
        super().__init__("invalid_evidence", f"{category}: {detail}")

    def result(self) -> dict[str, Any]:
        return {**super().result(), "category": self.category}


def _require_decision(condition: object, category: str, detail: str) -> None:
    if not condition:
        raise DecisionRefusal(category, detail)


def choice(text: str) -> str | None:
    from .workflow_approval import conversational_choice
    return conversational_choice(text)


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
            w.require(len(result) < MAX_SOURCE_FILES, "state_unavailable",
                      f"Source inventory exceeds {MAX_SOURCE_FILES:,} files at {relative}. Run flow diagnose --workspace {workspace}.")
            if target.is_symlink():
                result[relative] = "symlink:" + os.readlink(target)
                continue
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                w.require(stat.S_ISREG(info.st_mode), "state_unavailable", "Source inventory requires regular files.")
                total += info.st_size
                w.require(total <= MAX_SOURCE_BYTES, "state_unavailable",
                          f"Source inventory exceeds {MAX_SOURCE_BYTES // (1024 * 1024)} MiB at {relative}. "
                          f"Run flow diagnose --workspace {workspace}; recover in a clean checkout without deleting source.")
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                result[relative] = digest.hexdigest()
    return result


def diagnose(workspace: Path) -> dict[str, Any]:
    """Bounded metadata walk: no source hashing, initialization, deletion or ignores."""
    entries = files = total = 0
    complete = True
    largest: list[tuple[int, str]] = []
    issues: list[str] = []
    pending = [workspace]
    while pending and complete:
        parent = pending.pop()
        try:
            with os.scandir(parent) as children:
                for item in children:
                    if entries >= MAX_SOURCE_FILES:
                        complete = False
                        break
                    entries += 1
                    if item.name in EXCLUDED and item.is_dir(follow_symlinks=False):
                        continue
                    relative = str(Path(item.path).relative_to(workspace))
                    try:
                        info = item.stat(follow_symlinks=False)
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(Path(item.path))
                        elif stat.S_ISREG(info.st_mode):
                            files += 1
                            total += info.st_size
                            heapq.heappush(largest, (info.st_size, relative))
                            if len(largest) > 10:
                                heapq.heappop(largest)
                        elif len(issues) < 10:
                            issues.append(relative + (": symlink (not followed)" if stat.S_ISLNK(info.st_mode) else ": non-regular entry"))
                    except OSError:
                        if len(issues) < 10:
                            issues.append(relative + ": unreadable metadata")
        except OSError:
            if len(issues) < 10:
                issues.append(str(parent) + ": unreadable directory")
    return {"schema": "taskplane.diagnostics/v1", "workspace": str(workspace),
            "limits": {"source_files": MAX_SOURCE_FILES, "source_bytes": MAX_SOURCE_BYTES,
                       "diagnostic_entries": MAX_SOURCE_FILES},
            "inspected_entries": entries, "regular_files": files, "regular_bytes": total,
            "complete": complete and not issues, "partial": not complete or bool(issues),
            "largest_inspected_files": [{"path": path, "bytes": size} for size, path in sorted(largest, reverse=True)],
            "issues": issues, "source_limit_exceeded": True if total > MAX_SOURCE_BYTES else None if not complete or issues else False,
            "recovery": "Inspect the reported paths. Create a native clean HEAD worktree if appropriate; "
                        "uncommitted changes remain in the original checkout. Initialize an exact scope there. "
                        "For an active run use explicit --replace-run/--expected-revision to preserve history. "
                        "Do not delete user files, ignore source, or disable hooks to recover."}


def git_identity(workspace: Path) -> dict[str, str] | None:
    """Read identity only; never create a checkout or copy a phase grant."""
    try:
        result = subprocess.run(['git', '-C', str(workspace), 'rev-parse', '--path-format=absolute',
                                 '--git-common-dir', '--show-toplevel', 'HEAD'],
                                capture_output=True, text=True, timeout=5,
                                env={k: v for k, v in os.environ.items()
                                     if k not in {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR'}})
        parts = result.stdout.splitlines()
        if result.returncode or len(parts) != 3 or not re.fullmatch(r'[a-f0-9]{40,64}', parts[2]):
            return None
        return {'common': str(Path(parts[0]).resolve()), 'head': parts[2],
                'subpath': str(workspace.relative_to(Path(parts[1]).resolve()))}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


WORKFLOW_OPTIONS = {
    "catalog": ({"--workspace"}, set()),
    "validate": ({"--workspace", "--definition"}, set()),
    "preview": ({"--workspace", "--definition"}, {"--inputs"}),
    "save": ({"--workspace", "--definition", "--out"}, set()),
    "compile": ({"--workspace", "--definition", "--inputs", "--out"}, set()),
    "check": ({"--workspace", "--run"}, set()),
}


def workflow_options(arguments: list[str]) -> tuple[str, dict[str, str]]:
    """One closed argument grammar shared by the data CLI and native guards."""
    w.require(bool(arguments) and arguments[0] in WORKFLOW_OPTIONS,
              "invalid_arguments", "Choose workflow catalog, validate, preview, save, compile or check.")
    action = arguments[0]
    required, optional = WORKFLOW_OPTIONS[action]
    values: dict[str, str] = {}
    index = 1
    while index < len(arguments):
        option, separator, value = arguments[index].partition("=")
        w.require(option in required | optional and option not in values, "invalid_arguments",
                  "Unknown, abbreviated or duplicate workflow option: " + option)
        if not separator:
            index += 1
            value = arguments[index] if index < len(arguments) else ""
        w.require(bool(value) and not value.startswith("--") and "\0" not in value,
                  "invalid_arguments", "Expected one value for " + option)
        values[option] = value
        index += 1
    w.require(required <= set(values), "invalid_arguments",
              "Missing workflow options: " + ", ".join(sorted(required - set(values))))
    return action, values


def verify_workflow(workspace: Path, state: dict[str, Any]) -> dict[str, Any] | None:
    """Validate optional pinned provenance at use, never when loading diagnostics."""
    scope = state.get("scope", {})
    if "workflow_binding" not in scope:
        return None
    from . import blueprint, blueprint_compile
    try:
        plans = [v for v in state["visits"][:state["index"] + 1]
                 if v["phase"] == "plan" and not v.get("superseded")]
        plan = plans[-1] if plans and plans[-1].get("decision") == "approved" else None
        if any(v["phase"] == "plan" for v in state["visits"][:state["index"]]):
            w.require(plan is not None, "approval_required", "Continuation requires its current accepted Plan.")
        mutable: list[str] = []
        if plan:
            w.validate_state(state)
            w.require(not state.get("invalidation_pending") and evidence.changed(workspace, state) is None,
                      "stale_checkpoint", "Accepted Plan evidence is stale.")
            w.require(plan.get("approved_scope_digest") == primitives.content_fingerprint(scope),
                      "scope_drift", "Current scope does not match the accepted Plan.")
            expected_binding = {**{key: state[key] for key in ("workspace", "root", "run")},
                                "visit": plan["id"], "checkpoint": plan["packet"]["checkpoint"],
                                "manifest_digest": primitives.content_fingerprint(plan["packet"]),
                                "scope_digest": plan.get("checkpoint_scope_digest")}
            w.require(any(d.get("choice") == "approved" and all(d.get("binding", {}).get(k) == v
                          for k, v in expected_binding.items()) for d in state["decisions"].values()),
                      "stale_checkpoint", "Accepted Plan decision belongs to another binding.")
            mutable = plan["packet"]["output"]["write_scope"]
            w.require(scope["paths"]["build"] == mutable, "scope_drift",
                      "Build paths differ from the accepted Plan.")
        # The compiler independently authenticates the complete pinned package
        # before its outer scope is used to admit a narrowed Plan projection.
        checked = blueprint_compile.verify_package(workspace, scope["workflow_binding"],
                                                    authorized_mutable_paths=mutable)
        expected = deepcopy(checked["scope"])
        if plan:
            expected["paths"]["build"] = mutable
        w.require(scope == expected, "scope_drift", "Run scope differs from the pinned workflow and accepted Plan.")
        w.require(Path(state["workspace"]).resolve() == workspace.resolve(),
                  "state_mismatch", "Workflow state belongs to another workspace.")
        if checked["definition"]["route"]["kind"] == "standalone":
            expected_tasks = checked["compilation"]["task_patterns"][checked["compilation"]["entry_phase"]]["tasks"]
            w.require(evidence.task_definitions(evidence.context_tasks(state)) == evidence.task_definitions(expected_tasks),
                      "binding_mismatch", "Current standalone tasks differ from the pinned workflow.")
        return checked
    except blueprint.BlueprintError as exc:
        raise w.Refusal(exc.code, str(exc) + " " + exc.remedy) from None


def workflow_status(workspace: Path, state: dict[str, Any]) -> dict[str, Any] | None:
    binding = state.get("scope", {}).get("workflow_binding")
    if "workflow_binding" not in state.get("scope", {}):
        return None
    result = {"run": state.get("run"), "binding": deepcopy(binding),
              "status": "unknown", "unresolved_inputs": None}
    try:
        checked = verify_workflow(workspace, state)
        assert checked is not None
        result.update(status="valid", name=checked["definition"]["name"],
                      unresolved_inputs=checked["bindings"]["unresolved_inputs"])
    except w.Refusal as exc:
        result.update(status="blocked", diagnostics=[{
            "code": exc.reason, "location": "/workflow_binding", "message": str(exc),
            "remedy": "Restore pinned inputs or explicitly recover; do not replace the active package."}])
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

    def empty_database(self) -> dict[str, Any]:
        return {"schema": "taskplane.control/v1", "profile": PROFILE, "workspace": str(self.workspace),
                "root": self.root, "active": None, "runs": {}}

    def initialization_transaction(self) -> dict[str, Any]:
        return {**self.marker(),
                "schema": "taskplane.local-initialization-transaction/v1",
                "database": self.filename, "empty_sha256": primitives.content_fingerprint(self.empty_database()),
                "status": "pending"}

    def _initialization(self) -> tuple[Path, Path, Path, dict[str, Any] | None]:
        target = self.control_path(self.workspace, self.root)
        marker = storage.runtime_file(str(self.workspace), self.markername)
        transaction = storage.runtime_file(str(self.workspace), "initialization-" + target.stem.removeprefix("workflow-") + ".json")
        data = None
        if transaction.exists():
            w.require(transaction.stat().st_size <= 4096, "state_unavailable", "Initialization transaction is oversized.")
            data = json.loads(transaction.read_text())
            expected = self.initialization_transaction()
            w.require(data == expected or data == {**expected, "status": "committed"},
                      "state_unavailable", "Initialization transaction identity is corrupt.")
        if marker.exists():
            w.require(marker.stat().st_size <= 4096 and json.loads(marker.read_text()) == self.marker(),
                      "state_unavailable", "Local initialization identity is corrupt.")
        if data and data["status"] == "pending":
            # Pending evidence is only valid for this exact never-used empty store.
            if target.exists():
                w.require(marker.exists() and target.stat().st_size <= 4096
                          and json.loads(target.read_text()) == self.empty_database(),
                          "state_unavailable", "Pending initialization does not match an empty never-used store.")
        else:
            w.require(target.exists() == marker.exists() and (not data or target.exists()), "state_unavailable",
                      "Local workflow initialization/state is incomplete. Explicit recovery is required. "
                      "Run flow diagnose to identify the missing file; "
                      "flow recover can restore an explicitly selected, matching numbered database copy.")
        return target, marker, transaction, data

    def pending_initialization(self) -> bool:
        return (self._initialization()[3] or {}).get("status") == "pending"

    def state_exists(self, *, allow_pending: bool = False) -> bool:
        target, _, _, transaction = self._initialization()
        if transaction and transaction["status"] == "pending":
            w.require(allow_pending, "state_unavailable",
                      "First initialization is pending. Retry the exact validated flow start request; "
                      "read-only status does not repair state.")
            return False
        return target.exists()

    def initialize(self) -> None:
        if self.state_exists(allow_pending=True):
            return
        target, marker, transaction, pending = self._initialization()
        if pending is None:
            # Proof precedes both marker and database publication. Keep it after commit
            # so a missing established database can never be mistaken for first use.
            primitives.atomic_json(transaction, self.initialization_transaction(), strict_directory_sync=True)
        if not marker.exists():
            primitives.atomic_json(marker, self.marker(), strict_directory_sync=True)
        if not target.exists():
            primitives.atomic_json(target, self.empty_database(), strict_directory_sync=True)
        self._initialization()  # Validate exact empty bytes before closing recovery.
        primitives.atomic_json(transaction, {**self.initialization_transaction(), "status": "committed"},
                               strict_directory_sync=True)

    def verify_start(self, workspace: Path, root: str, request: dict[str, Any]) -> dict[str, Any]:
        scope = request.get("scope")
        w.require(isinstance(scope, dict), "invalid_evidence", "Native workflow start requires an exact --scope JSON file.")
        assert isinstance(scope, dict)
        evidence.valid_scope(workspace, scope)
        if "workflow_binding" in scope:
            from . import blueprint, blueprint_compile
            try:
                checked = blueprint_compile.verify_package(workspace, scope["workflow_binding"],
                                                            scope=scope, for_start=True)
            except blueprint.BlueprintError as exc:
                raise w.Refusal(exc.code, str(exc) + " " + exc.remedy) from None
            route = checked["definition"]["route"]
            w.require(request.get("entry", "product") == checked["compilation"]["entry_phase"]
                      and request.get("standalone", False) == (route["kind"] == "standalone"),
                      "binding_mismatch", "Start route differs from the compiled workflow.")
            w.require(isinstance(request.get("tasks"), str), "binding_mismatch",
                      "A compiled workflow requires its pinned entry tasks.")
            tasks = evidence.object_file(workspace, request["tasks"])
            w.require(tasks == checked["compilation"]["task_patterns"][checked["compilation"]["entry_phase"]],
                      "binding_mismatch", "Start tasks differ from the compiled workflow.")
        reference = request.get("request_reference")
        w.require(isinstance(reference, str) and 0 < len(reference) <= 512, "invalid_evidence",
                  "Identify the actual user request with --request-reference.")
        return {"scope": deepcopy(scope), "entry": request.get("entry", "product"),
                "standalone": request.get("standalone", False), "goal": request.get("goal", ""),
                "request_reference": reference}

    def state_created(self, state: dict[str, Any], request: dict[str, Any]) -> None:
        from .context_handoff import SEMANTIC_CONTRACT
        state.update(profile=PROFILE, source_baseline=inventory(self.workspace),
                     observed_handles=Harness(self.workspace, self.root).initial_handles(state),
                     request_provenance={"reference": request["request_reference"], "assurance": "observed"},
                     context_contract=SEMANTIC_CONTRACT)

    def decorate(self, state: dict[str, Any]) -> dict[str, Any]:
        from . import worker_runtime
        return {"coverage": {"structured_hook_paths": "checked when observed", "source_drift": "audited",
                "opaque_commands": "host permissions; effects not contained", "process_census": "unknown",
                "late_stdin": "checked only when observed",
                 "delegation": "scoped native attempts; observed identities, joins and root result acceptance"},
                "worker_status": worker_runtime.summary(state, self.workspace),
                "workflow_provenance": workflow_status(self.workspace, state),
                "known_live_handles": [h for h,r in state.get("observed_handles", {}).items()
                                       if r["state"] == "running"]}

    def validate_state(self, state: dict[str, Any]) -> None:
        from . import worker_runtime
        worker_runtime.validate(state)
        from .context_handoff import CONTRACT, SEMANTIC_CONTRACT
        w.require(state.get("context_contract", CONTRACT) in {CONTRACT, SEMANTIC_CONTRACT},
                  "state_unavailable", "Unsupported context contract.")
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
        verify_workflow(self.workspace, state)
        before, after = state["source_baseline"], inventory(self.workspace)
        allowed = set(state["scope"]["paths"][w.current(state)["phase"]])
        changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
        w.require(not changed - allowed, "scope_violation",
                  "Source changed outside this phase scope: " + ", ".join(sorted(changed - allowed)[:10]))
        if not state.get("finished") and (action == "auto-decide" or (action in {"advance", "finish"}
                                       and w.current(state)["decision"] == "approved")):
            w.require(Harness(self.workspace, self.root).presentation_valid(state), "approval_required",
                      "Current checkpoint needs a native dashboard handoff before continuation. "
                      "Regenerate/link the dashboard and record flow present with its actual outcome.")

    def can_seal(self, state: dict[str, Any]) -> bool:
        from . import worker_runtime
        return (not any(record["state"] == "running" for record in state.get("observed_handles", {}).values())
                and worker_runtime.joined(state))

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
        _require_decision(isinstance(source, dict) and source.get("kind") in {"conversation", "native_prompt"}
                  and source.get("conversation") == self.root and source.get("actor") == "user"
                  and source.get("automatic") is False and isinstance(source.get("reference"), str)
                  and 0 < len(source["reference"]) <= 512, "decision_provenance", "Human response provenance is incomplete.")
        event_id, excerpt, recorder = value.get("event_id"), value.get("excerpt"), value.get("recorder")
        _require_decision(isinstance(event_id, str) and 0 < len(event_id) <= 512
                  and isinstance(excerpt, str) and 0 < len(excerpt) <= 4096
                  and recorder in {"root_orchestrator", "native_prompt_hook"},
                  "decision_provenance", "Preserve the complete bounded human excerpt, event reference and recorder.")
        interpreted = choice(excerpt)
        _require_decision(interpreted is not None and value.get("choice") == interpreted,
                  "decision_grammar", "The response's decision is unclear or differs from its recorded choice. Clarify the intended decision; no exact wording is required.")
        observed = timestamp(source.get("observed_at"))
        if event_id in prior:
            old_source = prior[event_id].get("provenance", {}).get("source", {})
            w.require(all(source.get(k) == v for k, v in old_source.items()),
                      "stale_checkpoint", "Conflicting native event replay.")
        recorded_at = (prior[event_id].get("provenance", {}).get("recorded_at")
                       if event_id in prior else None) or datetime.now(timezone.utc).isoformat()
        expected = prior[event_id]["binding"] if event_id in prior else expected
        _require_decision(primitives.content_fingerprint(value.get("binding")) == primitives.content_fingerprint(expected),
                  "decision_binding", "Observed decision has a stale or foreign checkpoint binding.")
        named_checkpoints = re.findall(r"\b[0-9a-f]{32}\b", excerpt.casefold())
        _require_decision(all(identifier == expected["checkpoint"] for identifier in named_checkpoints),
                  "decision_binding", "The response names a different checkpoint.")
        from .workflow_approval import decision_phase
        named_phase = decision_phase(excerpt)
        supplied = value.get("binding") or {}
        db = evidence.object_file(self.workspace, ".taskplane/" + self.filename)
        run = db.get("runs", {}).get(supplied.get("run"), {})
        stage: dict[str, Any] = next((v for v in run.get("visits", []) if v["id"] == supplied.get("visit")), {})
        legacy_replay = event_id in prior and not prior[event_id].get("provenance", {}).get("recorded_at")
        if not legacy_replay:
            _require_decision(timestamp(run.get("started_at")) <= timestamp(stage.get("submitted_at")) <= observed
                      <= timestamp(recorded_at), "decision_chronology",
                      "Decision must follow run and checkpoint submission and cannot be in the future.")
        if named_phase:
            # Resolve the named visit under the Controller's existing store lock.
            # A correct binding cannot turn 'Build approved' into Product consent.
            _require_decision(stage.get("phase") == named_phase, "decision_binding",
                      "The response names a different phase. Clarify which checkpoint the user intends to accept.")
        if value.get("checkpoint_explicit") is not True:
            presentation = value.get("presentation")
            _require_decision(isinstance(presentation, dict) and presentation.get("checkpoint") == expected["checkpoint"]
                      and isinstance(presentation.get("reference"), str) and presentation["reference"],
                      "decision_binding", "Brief approval needs the presented checkpoint and ordering evidence.")
            _require_decision(timestamp(presentation.get("at")) < observed, "decision_chronology", "Response precedes presentation.")
            if not legacy_replay:
                _require_decision(timestamp(stage.get("submitted_at")) <= timestamp(presentation.get("at")),
                          "decision_chronology", "Presentation precedes checkpoint submission.")
        else:
            _require_decision(expected["checkpoint"] in excerpt, "decision_binding", "Explicit approval must name the checkpoint.")
        result = {"event_id": event_id, "human": True, "automatic": False, "choice": value["choice"],
                "binding": deepcopy(expected), "assurance": "observed", "provenance": {
                    "source": {k:source[k] for k in ("kind","reference","conversation","actor","automatic","observed_at")},
                    "recorder": recorder, "excerpt": excerpt,
                    "presentation": {k:value["presentation"][k] for k in ("checkpoint","reference","at")}
                        if value.get("checkpoint_explicit") is not True else None,
                    "checkpoint_explicit": value.get("checkpoint_explicit", False)}}
        if not legacy_replay:
            result["provenance"].update(recorded_at=recorded_at, chronology="verified/v1")
        if event_id in prior:
            w.require(result == prior[event_id], "stale_checkpoint", "Conflicting native event replay.")
        return result

    def prompt_reference(self, event: dict[str, Any], state: dict[str, Any]) -> str | None:
        # Named hooks may carry an observed envelope. Plain hook-shaped text is
        # not enough to establish what checkpoint was shown before the response.
        value = event.get("taskplane_decision")
        if isinstance(value, dict):
            source = value.get("source", {})
            for field, expected in (("timestamp", source.get("observed_at")), ("message_id", source.get("reference"))):
                w.require(field not in event or event[field] == expected, "invalid_evidence",
                          "Observed approval differs from supplied native prompt metadata.")
        return json.dumps(value) if isinstance(value, dict) else None

    def control_action(self, event: dict[str, Any], state: dict[str, Any]) -> bool:

        def selected_workspace(value: str | None) -> bool:
            try:
                return runtime_command.resolve_selection(value, event, self.workspace) == self.workspace
            except (w.Refusal, OSError, ValueError):
                return False

        words = runtime_words(event)
        try:
            cwd = runtime_command.validate_workdir(words, event, self.workspace)
        except ValueError:
            return False
        if not runtime_command.installed(words, cwd):
            return False
        if words[2] == "dashboard":
            # A sealed checkpoint can refresh its native view, never an arbitrary output.
            options = words[3:]
            if len(options) % 2 or len(set(options[::2])) != len(options[::2]):
                return False
            values = dict(zip(options[::2], options[1::2]))
            return (set(values) <= {"--workspace", "--run", "--out"}
                    and selected_workspace(values.get("--workspace"))
                    and values.get("--run", state["run"]) == state["run"]
                    and ((cwd / values["--out"]).absolute() if "--out" in values else
                         self.workspace / ".taskplane/dashboard.html")
                        == self.workspace/".taskplane/dashboard.html")
        if runtime_command.diagnostic(words) or words[2:] == ["workflow", "--help"]:
            return True
        if words[2] == "workflow":
            action, _ = workflow_command(self.workspace, event, state)
            return action in {"catalog", "validate", "preview", "check"}
        if len(words) < 4 or words[2] != "flow" or words[3] not in {"start", "report", "diagnose", "recover", "resume", "reconcile-maintenance", "inspect", "prevalidate", "context", "worker", "attach", "decide", "advance", "finish", "retire", "policy", "auto-decide", "activate", "deactivate", "present", "wait"}:
            return False
        # An exact control command still goes through the Controller checks.
        try:
            return selected_workspace(runtime_command.workspace_selector(words[4:]))
        except ValueError:
            return False

    def guard_command(self, event: dict[str, Any], state: dict[str, Any], paths: list[str]) -> None:
        words = runtime_words(event)
        if len(words) >= 3 and words[2] == "workflow":
            workflow_command(self.workspace, event, state, allowed=paths)
        verify_workflow(self.workspace, state)
        # Host permissions apply; inventory audits opaque command effects.

    def guard_input(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        args = event.get("tool_input", {})
        handle = str(args.get("session_id", ""))
        record = state["observed_handles"].get(handle)
        safe_input = args.get("chars", "") in ("", "\x03")
        w.require(record is not None and record["state"] == "running"
                  and record["visit"] == w.current(state)["id"]
                  and record["revision"] <= state["revision"]
                  and (not record.get("control") or
                       (event.get("thread_id") or event.get("session_id") or record.get("worker_id") or self.root)
                       == (record.get("worker_id") or self.root))
                  and (safe_input or not record.get("control") and record["revision"] == state["revision"]),
                  "scope_violation", "Observed input handle is unknown, terminal or belongs to an old phase grant.")

    def observe_state(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        if event.get("hook_event_name") != "PostToolUse":
            return
        tool = event.get("tool_name") or event.get("tool")
        response = event.get("tool_response")
        if tool not in {"exec_command", "Bash", "write_stdin"} or not isinstance(response, dict):
            return  # Unstructured/uncovered process observations remain unknown.
        args = event.get("tool_input", {})
        handle = response.get("session_id", args.get("session_id") if tool == "write_stdin" else None)
        if type(handle) not in {str, int}:
            return
        if tool == "write_stdin":
            w.require(str(handle) == str(args.get("session_id")), "scope_violation", "Poll returned another handle.")
        key = str(handle)
        handles = state["observed_handles"]
        w.require(key in handles or len(handles) < 4096, "state_unavailable", "Observed handle limit reached.")
        previous = handles.get(key)
        terminal = type(response.get("exit_code")) is int
        new_state = ("completed" if response["exit_code"] == 0 else "failed") if terminal else "running"
        if previous:
            w.require(previous["visit"] == w.current(state)["id"] and previous["revision"] <= state["revision"]
                      and (previous["state"] == "running" or previous["state"] == new_state),
                      "scope_violation", "Observed handle cannot reopen or cross grants.")
        handles[key] = {"visit": previous["visit"] if previous else w.current(state)["id"],
                        "revision": previous["revision"] if previous else state["revision"],
                        "state": new_state,
                        "worker_id": previous.get("worker_id") if previous else (
                            (event.get("thread_id") or event.get("session_id"))
                            if (event.get("thread_id") or event.get("session_id")) != state["root"] else None),
                        "read_only": previous.get("read_only", False) if previous else readonly_command(event),
                        "control": previous.get("control", False) if previous else self.control_action(event, state)}


EXECUTION_ENTRIES = {'taskplane', 'tp-go', 'tp-tag', 'tp-build', 'tp-product', 'tp-workflow',
                     'tp-design', 'tp-engineering', 'tp-northstar'}
READ_TOOLS = {'Read', 'read_file', 'list_files', 'search_files', 'Grep', 'Glob'}
QUESTION_TOOLS = {'AskUserQuestion', 'request_user_input', 'request_user_input_async'}


def command_words(event: dict[str, Any]) -> list[str]:
    args = event.get('tool_input', {})
    command = args.get('cmd', args.get('command', '')) if isinstance(args, dict) else ''
    if not isinstance(command, str):
        return []
    quote, escaped = '', False
    for char in command:
        if quote == "'":
            if char == "'":
                quote = ''
            continue  # Single-quoted JSON/notes are literal shell data.
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
        elif char in '$`\n\r':
            return []  # No expansions/substitutions or unquoted command separators.
        elif char == '"':
            quote = '' if quote == '"' else '"'
        elif char == "'" and not quote:
            quote = "'"
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>()')
        lexer.whitespace_split = True
        lexer.commenters = ''
        words = list(lexer)
        # Punctuation inside an ordinary quoted note is data, not a shell operator.
        return [] if any(re.fullmatch(r'[;&|<>()]+', word) for word in words) else words
    except ValueError:
        return []


def runtime_words(event: dict[str, Any]) -> list[str]:
    words = command_words(event)
    # Declared Windows hooks use this exact launcher selector. The launcher and
    # a PATH-selected Python can resolve to different installed interpreters.
    if os.name == 'nt' and words[:2] == ['py', '-3']:
        return [sys.executable, *words[2:]]
    return words


def workflow_command(workspace: Path, event: dict[str, Any], state: dict[str, Any], *,
                     allowed: list[str] | None = None) -> tuple[str, dict[str, str]]:
    """Admit only this installed data command and its finite, current write set."""
    words = runtime_words(event)
    cwd = runtime_command.validate_workdir(words, event, workspace)
    w.require(runtime_command.installed(words, cwd) and words[2] == "workflow",
              "scope_violation", "Use the exact loaded workflow runtime and interpreter.")
    action, options = workflow_options(words[3:])
    w.require(runtime_command.resolve_selection(options["--workspace"], event, workspace) == workspace,
              "scope_violation", "Workflow command selects another workspace.")
    if action in {"catalog", "validate", "preview", "check"}:
        return action, options
    if state.get("visits") and not state.get("finished"):
        w.require(not state.get("invalidation_pending") and w.current(state)["decision"] in
                  {"not_requested", "changes_requested", "rejected", "stale"},
                  "approval_required", "Current output is sealed; resolve the checkpoint before publishing workflow data.")
    from . import blueprint, blueprint_compile
    try:
        definition_path = workspace / options["--definition"]
        data = blueprint.load_definition(definition_path)
        if action == "save":
            target = f"workflows/{data['id']}.{data['version']}.workflow.json"
            requested = Path(options["--out"])
            if requested.is_absolute():
                requested = requested.relative_to(workspace)
            w.require(requested.as_posix() == target and ".." not in requested.parts,
                      "scope_violation", "Save must select the exact versioned definition path.")
            evidence.path(workspace, target)
            paths = allowed if allowed is not None else (state.get("scope", {}).get("paths", {}).get(
                w.current(state)["phase"], []) if state.get("visits") else [])
            w.require(target in paths, "scope_violation", "Save requires the exact current authoring path.")
        else:
            destination = blueprint_compile.package_path(workspace, options["--out"], {})
            sealed = {p for stage in state.get("visits", []) if stage.get("packet")
                      for key in ("manifest", "source_manifest") for p in stage["packet"][key]}
            current = state.get("scope", {}).get("workflow_binding", {})
            w.require(current.get("package_path") != destination
                      and not any(p.startswith(destination + "/") for p in sealed),
                      "scope_violation", "An active or sealed workflow package cannot be republished.")
    except (blueprint.BlueprintError, OSError, ValueError) as exc:
        if isinstance(exc, w.Refusal):
            raise
        raise w.Refusal("invalid_workflow_command", str(exc)) from None
    return action, options


def workflow_diagnostic_event(workspace: Path, event: dict[str, Any], state: dict[str, Any]) -> bool:
    """Recovery reads and lifecycle controls remain usable with stale provenance."""
    tool = event.get("tool_name") or event.get("tool")
    from . import worker_runtime
    if tool in worker_runtime.STATUS | worker_runtime.WAIT | worker_runtime.INTERRUPT:
        return True  # The existing worker adapter still checks target and operation.
    if tool in READ_TOOLS | QUESTION_TOOLS or readonly_command(event):
        return True
    if Harness(workspace, state["root"]).dashboard_opener(event, state):
        return True
    if tool == "write_stdin" and event.get("tool_input", {}).get("chars", "") in ("", "\x03"):
        return True
    words = runtime_words(event)
    cwd = runtime_command.validate_workdir(words, event, workspace)
    if not runtime_command.installed(words, cwd):
        return False
    if runtime_command.diagnostic(words) or words[2] == "dashboard" or words[2:] == ["workflow", "--help"]:
        return True
    if words[2] == "workflow":
        return workflow_command(workspace, event, state)[0] in {"catalog", "validate", "preview", "check"}
    if len(words) > 3 and words[2:4] == ["flow", "worker"]:
        options = words[4:]
        return any(word == '--operation=' + operation or word == '--operation'
                   and index + 1 < len(options) and options[index + 1] == operation
                   for index, word in enumerate(options) for operation in ("status", "recover-unbound"))
    return len(words) > 3 and words[2] == "flow" and words[3] in {
        "report", "diagnose", "inspect", "recover", "resume", "reconcile-maintenance", "retire", "start", "wait", "present", "decide"}


def readonly_command(event: dict[str, Any]) -> bool:
    """Small, non-executing diagnostic grammar; no shell operators or rg helpers."""
    words = command_words(event)
    if words == ['pwd']:
        return True
    if words and words[0] == 'ls':
        return all(not word.startswith('-') or word in {'-l', '-a', '-la', '-al', '-d', '-ld', '-1'} for word in words[1:])
    if words in (['date'], ['date', '-u'], ['date', '-Iseconds']):
        return True
    if words and words[0] == 'cat':
        return len(words) > 1 and all(not value.startswith('-') for value in words[1:])
    if not words or words[0] != 'rg':
        return False
    flags = {'-n', '--line-number', '-l', '--files-with-matches', '--files', '--hidden',
             '-i', '--ignore-case', '-F', '--fixed-strings', '-S', '--smart-case',
             '--no-heading', '--no-messages', '--count', '-c', '--no-ignore', '--no-ignore-vcs'}
    values = {'-g', '--glob', '-t', '--type', '-m', '--max-count', '-e', '--regexp',
              '-A', '--after-context', '-B', '--before-context', '-C', '--context'}
    index = 1
    while index < len(words):
        word = words[index]
        if word == '--':
            return True  # Following arguments cannot select an executable helper.
        if word in values:
            index += 1
            if index >= len(words):
                return False
        elif word.startswith('-') and word not in flags:
            return False
        index += 1
    return len(words) > 1


def bootstrap_write(workspace: Path, event: dict[str, Any], state: dict[str, Any]) -> bool:
    """Allow a fresh recovery proposal without editing any previous sealed file."""
    tool = event.get('tool_name') or event.get('tool')
    args = event.get('tool_input', {})
    targets = []
    if tool in {'Write', 'Edit', 'write_file', 'edit_file'}:
        targets = [args.get('file_path') or args.get('path')]
    elif tool == 'apply_patch':
        patch = args.get('command', args.get('input', args.get('patch', '')))
        if isinstance(patch, str):
            targets = [line.split(': ', 1)[1] for line in patch.splitlines()
                       if line.startswith(('*** Add File: ', '*** Update File: ', '*** Delete File: ', '*** Move to: '))]
    if not targets:
        return False
    sealed = {p for stage in state.get('visits', []) if stage.get('packet')
              for field in ('manifest', 'source_manifest') for p in stage['packet'][field]}
    # A past packet in this same run remains historical evidence too.
    sealed.update(p for entry in state.get('history', []) if entry.get('packet')
                  for field in ('manifest', 'source_manifest') for p in entry['packet'][field])
    for value in targets:
        if not isinstance(value, str):
            return False
        path = Path(value)
        if path.is_absolute():
            if not path.is_relative_to(workspace):
                return False
            path = path.relative_to(workspace)
        if '..' in path.parts or not path.as_posix().startswith('.taskplane/bootstrap/') or path.as_posix() in sealed:
            return False
        evidence.path(workspace, path.as_posix())
    return True


def execution_entry(event: dict[str, Any], *, allow_skill_read: bool = True) -> str | None:
    """Recognize explicit execution selection, never arbitrary mentions or approval."""
    args = event.get('tool_input', {})
    if not isinstance(args, dict):
        return None
    tool = event.get('tool_name') or event.get('tool')
    if tool == 'Skill':
        name = args.get('skill', '')
        if name in {'taskplane:tp-workflow', 'tp-workflow'}:
            intent = args.get('args', '')
            return 'tp-workflow' if isinstance(intent, str) and re.match(
                r'^(?:create|edit|author)\b', intent.strip(), re.I) else None
        if name == 'taskplane':
            return 'taskplane'  # Cowork can expose the router without a plugin namespace.
        if isinstance(name, str) and name.startswith('taskplane:') and name[10:] in EXECUTION_ENTRIES:
            return name[10:]
    # Codex can load skills through a native read instead of a Skill event.
    paths = [args.get('file_path') or args.get('path')] if tool in READ_TOOLS else []
    words = command_words(event) if tool in {'Bash', 'exec_command'} else []
    if words and words[0] == 'cat':
        paths += words[1:]
    for value in paths if allow_skill_read else []:
        if isinstance(value, str):
            path = Path(value)
            for entry in EXECUTION_ENTRIES:
                if entry == 'tp-workflow':
                    continue  # The skill resolves inspect/run/author intent before activation.
                if path.is_absolute() and path.resolve() == Path(__file__).resolve().parents[1]/'skills'/entry/'SKILL.md':
                    return entry
    if event.get('hook_event_name') != 'UserPromptSubmit':
        return None
    prompt = event.get('prompt', '')
    if not isinstance(prompt, str):
        return None
    # Only a direct prefix is an activation signal. Quoted examples and questions
    # are not interpreted as execution instructions. No text here grants consent.
    text = prompt.strip().casefold()
    match = re.match(r'^(?:\$|/)(?:taskplane:)?(tp-[a-z]+|taskplane)\b', text)
    if match:
        if match[1] == 'tp-workflow':
            return 'tp-workflow' if re.match(r'^\s+(?:create|edit|author)\b', text[match.end():]) else None
        return match[1] if match[1] in EXECUTION_ENTRIES else None
    # The menu and README also use bare directives. Route by their leading
    # action, so a Build request mentioning design/review keeps its full route.
    bare = re.match(r'^(?:please\s+)?taskplane\s+(build|implement|design|review|audit|product)\b', text)
    if bare:
        return {'design': 'tp-design', 'review': 'tp-engineering',
                'audit': 'tp-engineering', 'product': 'tp-product'}.get(bare[1], 'taskplane')
    tagged = re.match(r'^\[@taskplane\]\(plugin://taskplane[^)]*\)', text)
    direct = re.match(r'^(?:please\s+)?(?:use|run|invoke|start|resume)\s+(?:the\s+)?taskplane\b', text)
    if not (tagged or direct):
        return None
    prefix = tagged or direct
    assert prefix is not None
    tail = text[prefix.end():].strip(' :,-')
    if re.match(r'^(?:to\s+)?(?:help|status|explain|show\s+(?:the\s+)?status)\b', tail):
        return None
    leading = re.match(r'^(?:to\s+)?(build|implement|design|review|audit|product)\b', tail)
    if leading:
        return {'design': 'tp-design', 'review': 'tp-engineering',
                'audit': 'tp-engineering', 'product': 'tp-product'}.get(leading[1], 'taskplane')
    if re.search(r'\b(review|audit)\b', tail):
        return 'tp-engineering'
    if re.search(r'\bdesign\b', tail):
        return 'tp-design'
    if re.search(r'\b(product|requirements|specification)\b', tail):
        return 'tp-product'
    return 'taskplane'


class Harness:
    """Observed session engagement; separate from phase grants and host authority."""

    def __init__(self, workspace: Path, root: str):
        self.workspace, self.root = workspace.resolve(), root
        key = hashlib.sha256(root.encode()).hexdigest()[:32]
        self.path = storage.runtime_file(str(self.workspace), f'harness-{key}.json')

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        w.require(self.path.stat().st_size <= 16384, 'state_unavailable', 'Harness record exceeds its size bound.')
        data = json.loads(self.path.read_text())
        w.require(isinstance(data, dict) and data.get('schema') == 'taskplane.harness/v1'
                  and data.get('workspace') == str(self.workspace) and data.get('root') == self.root,
                  'state_unavailable', 'Harness identity is corrupt; restore this session record before continuing.')
        return cast(dict[str, Any], data)

    def update(self, **values: Any) -> dict[str, Any]:
        with primitives.file_lock(str(self.path)):
            return self._update_locked(**values)

    def _update_locked(self, **values: Any) -> dict[str, Any]:
        """Publish while the caller holds the non-reentrant harness lock."""
        data = self.read() or {'schema': 'taskplane.harness/v1', 'workspace': str(self.workspace), 'root': self.root}
        data.update(values)
        # Match atomic_json's encoding, indentation and trailing newline.
        w.require(len(json.dumps(data, sort_keys=True, indent=2, allow_nan=False).encode('utf-8')) + 1 <= 16384,
                  'invalid_evidence', 'Harness update exceeds its size bound.')
        primitives.atomic_json(self.path, data, strict_directory_sync=True)
        return data

    def setup_handles(self) -> dict[str, Any]:
        data = self.read()
        handles = data.get('setup_handles', {})
        w.require(isinstance(handles, dict) and len(handles) <= 64, 'state_unavailable', 'Invalid setup handles.')
        for key, record in handles.items():
            w.require(isinstance(key, str) and 0 < len(key) <= 256 and isinstance(record, dict)
                      and record.get('state') in {'running', 'completed', 'failed'}
                      and record.get('root') == self.root and record.get('run') is None,
                      'state_unavailable', 'Invalid setup process identity.')
        return handles if data.get('run') is None else {}

    def initial_handles(self, state: dict[str, Any]) -> dict[str, Any]:
        return {key: {'state': record['state'], 'visit': w.current(state)['id'],
                      'revision': state['revision'], 'worker_id': None, 'read_only': False, 'control': True}
                for key, record in self.setup_handles().items()}

    def guard_setup_input(self, event: dict[str, Any]) -> None:
        args = event.get('tool_input', {})
        record = self.setup_handles().get(str(args.get('session_id', '')))
        actor = event.get('thread_id') or event.get('session_id') or self.root
        w.require(actor == self.root and not event.get('parent_session_id')
                  and record is not None and record['state'] == 'running'
                  and args.get('chars', '') in ('', '\x03'),
                  'scope_violation', 'Setup input requires an observed running handle and empty polling or Ctrl-C.')

    def observe_setup(self, event: dict[str, Any]) -> None:
        with primitives.file_lock(str(self.path)):
            self._observe_setup_locked(event)

    def _observe_setup_locked(self, event: dict[str, Any]) -> None:
        if event.get('hook_event_name') != 'PostToolUse':
            return
        tool = event.get('tool_name') or event.get('tool')
        args, response = event.get('tool_input', {}), event.get('tool_response')
        if not isinstance(args, dict) or not isinstance(response, dict):
            return
        actor = event.get('thread_id') or event.get('session_id') or self.root
        if actor != self.root or event.get('parent_session_id'):
            return
        if self.read().get('run') is not None:
            return  # A transferred handle must be observed through its active store.
        handles = self.setup_handles()
        if tool == 'write_stdin':
            self.guard_setup_input(event)
        elif (tool not in {'Bash', 'exec_command'}
              or not runtime_command.installed(runtime_words(event),
                                               runtime_command.execution_directory(event, self.workspace))
              or not self.bootstrap_command(event)):
            return
        handle = response.get('session_id', args.get('session_id') if tool == 'write_stdin' else None)
        if type(handle) not in {str, int}:
            return
        if tool == 'write_stdin':
            w.require(str(handle) == str(args.get('session_id')), 'scope_violation', 'Setup poll returned another handle.')
        key = str(handle)
        w.require(0 < len(key) <= 256 and (key in handles or len(handles) < 64),
                  'state_unavailable', 'Setup handle limit reached.')
        terminal = type(response.get('exit_code')) is int
        status = ('completed' if response['exit_code'] == 0 else 'failed') if terminal else 'running'
        previous = handles.get(key)
        w.require(not previous or previous['state'] == 'running' or previous['state'] == status,
                  'scope_violation', 'A terminal setup handle cannot reopen.')
        handles[key] = {'state': status, 'root': self.root, 'run': None}
        self._update_locked(setup_handles=handles)

    def select(self, entry: str, reference: str, state: dict[str, Any]) -> None:
        w.require(entry in EXECUTION_ENTRIES or entry in w.PHASES, 'invalid_evidence', 'Choose a Taskplane execution entry.')
        w.require(isinstance(reference, str) and 0 < len(reference) <= 512, 'invalid_evidence', 'Identify the execution request.')
        previous = self.read()
        active = state.get('run') if state.get('visits') and not state.get('finished') else None
        values: dict[str, Any] = {'entry': entry, 'request_reference': reference, 'selected': True, 'waiting': None}
        if previous.get('run') != active:
            values.update(run=active, presentation=None)
        self.update(**values)

    def bind(self, state: dict[str, Any]) -> None:
        previous = self.read()
        if previous.get('run') != state['run'] or not previous.get('selected'):
            self.update(selected=True, entry=previous.get('entry', w.current(state)['phase']),
                        run=state['run'], waiting=None, presentation=None, setup_handles={})

    def deactivate(self, state: dict[str, Any], reference: str, reason: str) -> None:
        """Clear only uninitialized engagement; never alter a workflow grant."""
        w.require(state.get('profile') == 'native_workflow', 'unsupported_authority',
                  'Protected workflow engagement requires its trusted owner.')
        w.require(not state.get('run') and not state.get('visits'), 'approval_required',
                  'Cannot deactivate an active workflow; finish or explicitly retire it first.')
        w.require(isinstance(reference, str) and bool(reference.strip()) and len(reference) <= 512
                  and isinstance(reason, str) and bool(reason.strip()) and len(reason) <= 2048,
                  'invalid_evidence', 'Deactivation needs an actual request reference and reason.')
        self.update(selected=False, waiting=None, presentation=None,
                    deactivation={'request_reference': reference, 'reason': reason,
                                  'assurance': 'observed; no approval or workflow mutation'})

    def binding(self, state: dict[str, Any]) -> dict[str, Any]:
        return {'run': state.get('run'), 'visit': w.current(state)['id'] if state.get('visits') else None,
                'revision': state.get('revision')}

    def wait(self, state: dict[str, Any], reason: str) -> None:
        w.require(self.read().get('selected'), 'state_unavailable', 'No Taskplane execution selected.')
        w.require(isinstance(reason, str) and 0 < len(reason.strip()) <= 2048, 'invalid_evidence', 'Describe the missing user input.')
        self.update(waiting={'binding': self.binding(state), 'reason': reason.strip()})

    def readiness(self, state: dict[str, Any]) -> dict[str, Any]:
        data = self.read()
        active = bool(state.get('visits') and not state.get('finished'))
        return {'status': 'active' if active else 'initialization_required' if data.get('selected') and not state.get('finished') else 'inactive',
                'hook_observed': bool(data.get('hook_observed')), 'entry': data.get('entry'),
                'binding': self.binding(state), 'presentation': data.get('presentation'),
                'assurance': 'observed; not host authentication'}

    def guidance(self, state: dict[str, Any]) -> str:
        if not state.get('visits'):
            entry = self.read().get('entry', 'taskplane')
            phase = {'tp-engineering': 'engineering', 'tp-northstar': 'engineering',
                     'tp-design': 'design', 'tp-workflow': 'design', 'tp-product': 'product'}.get(entry)
            route = f'--standalone --phase {phase}' if phase else 'the requested route (Product for full delivery)'
            return ('Taskplane selected; initialization required before implementation or completing the review. '
                    'Prepare exact scope/evidence under .taskplane/bootstrap/, then run the installed tp.py flow start with '
                    f'{route}, --scope and --request-reference. Read/search, loading skills, asking the user and exact '
                    'Taskplane setup commands remain available. Standalone review does not require seven delivery phases.')
        stage = w.current(state)
        return (f'Taskplane harness active: run {state["run"]}, {stage["phase"]} visit {stage["id"]}, revision {state["revision"]}. '
                'Use flow context to obtain and consume the current handoff; read every required input before phase submission. '
                'Context receipts prove returned data, never approval or model attention. '
                f'Use the native dashboard at {self.workspace / ".taskplane/dashboard.html"}. '
                'After submitting phase evidence, provide the exact dashboard link/open result and record flow present. '
                'A queued open is not verified display. If user input is needed, record flow wait --note with the actual reason.')

    def recovery_action(self, event: dict[str, Any], state: dict[str, Any]) -> bool:
        tool = event.get('tool_name') or event.get('tool')
        args = event.get('tool_input', {})
        if not isinstance(args, dict):
            return False
        if tool in {'mcp__codex_app__uninstall_plugin', 'mcp__codex_app.uninstall_plugin'}:
            return (set(args) == {'plugin'} and isinstance(args['plugin'], str)
                    and args['plugin'].casefold() in {'taskplane', 'taskplane@openai-curated-remote'})
        identity = event.get('tool_use_id') or event.get('call_id')
        if not isinstance(identity, str) or not 0 < len(identity) <= 512:
            return False
        if tool in {'mcp__codex_app__get_worktree_creation_status', 'mcp__codex_app.get_worktree_creation_status'}:
            pending = self.read().get('recovery_pending') or {}
            if (not pending.get('operation_id') or set(args) != {'operationId'}
                    or args['operationId'] != pending['operation_id']
                    or pending.get('binding') != self.binding(state)
                    or not pending.get('source') or git_identity(self.workspace) != pending['source']):
                return False
            self.update(recovery_pending={**pending, 'poll': {
                'call_id': identity, 'input_digest': primitives.content_fingerprint(args)}})
            return True
        if tool not in {'mcp__codex_app__create_worktree', 'mcp__codex_app.create_worktree'}:
            return False
        if set(args) - {'name', 'ref', 'allowAsync'} or args.get('ref', 'HEAD') != 'HEAD':
            return False
        # The current API defaults to the remote default branch, not HEAD.
        # Retain legacy immediate calls; their destination identity is checked too.
        if 'allowAsync' in args and (args['allowAsync'] is not True or args.get('ref') != 'HEAD'):
            return False
        name = args.get('name')
        if name is not None and (not isinstance(name, str) or len(name) > 64
                or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name)
                or re.fullmatch(r'(?:[a-f0-9]{4,}|con|prn|aux|nul|com[1-9]|lpt[1-9])', name)):
            return False
        source = git_identity(self.workspace)
        if source is None:
            return False
        self.update(recovery_pending={'call_id': identity, 'input_digest': primitives.content_fingerprint(args),
                                      'binding': self.binding(state), 'source': source,
                                      'allow_async': args.get('allowAsync') is True}, recovery_workspace=None)
        return True

    def observe_recovery(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        tool = event.get('tool_name') or event.get('tool')
        polling = tool in {'mcp__codex_app__get_worktree_creation_status', 'mcp__codex_app.get_worktree_creation_status'}
        if not polling and tool not in {'mcp__codex_app__create_worktree', 'mcp__codex_app.create_worktree'}:
            return
        pending = self.read().get('recovery_pending') or {}
        expected = pending.get('poll', {}) if polling else pending
        if (not pending or not expected or expected.get('call_id') != (event.get('tool_use_id') or event.get('call_id'))
                or expected.get('input_digest') != primitives.content_fingerprint(event.get('tool_input', {}))
                or not polling and pending.get('operation_id')):
            return
        if (pending.get('binding') != self.binding(state) or not pending.get('source')
                or git_identity(self.workspace) != pending['source']):
            self.update(recovery_pending=None, recovery_workspace=None)
            return
        response = event.get('tool_response')
        if not isinstance(response, dict) or response.get('isError'):
            self.update(recovery_pending=None)
            return
        payload = response.get('structuredContent', response)
        # Actual Codex result: one JSON text block with worktreeWorkspaceRoot.
        if 'content' in response:
            blocks = response['content']
            if (not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict)
                    or blocks[0].get('type') != 'text' or not isinstance(blocks[0].get('text'), str)
                    or len(blocks[0]['text'].encode()) > 16384):
                self.update(recovery_pending=None)
                return
            try:
                payload = json.loads(blocks[0]['text'])
            except ValueError:
                payload = None
        if not isinstance(payload, dict):
            self.update(recovery_pending=None, recovery_workspace=None)
            return
        if polling:
            if (payload.get('operationId') != pending.get('operation_id')
                    or event.get('tool_input', {}).get('operationId') != pending.get('operation_id')):
                self.update(recovery_pending=None, recovery_workspace=None)
                return
            status = payload.get('status')
            if isinstance(status, str) and status in {'preparing', 'creating', 'registering'}:
                self.update(recovery_pending={k: v for k, v in pending.items() if k != 'poll'})
                return
            completed = status == 'completed' and payload.get('type') in (None, 'created')
        else:
            operation = payload.get('operationId')
            if (pending.get('allow_async') and payload.get('type') == 'pending'
                    and isinstance(operation, str) and 0 < len(operation.strip()) <= 512):
                self.update(recovery_pending={**pending, 'operation_id': operation})
                return
            completed = (payload.get('type') == 'created' and not operation
                         and payload.get('status') in (None, 'completed'))
        destination = None
        if completed:
            workspace, git_root = payload.get('worktreeWorkspaceRoot'), payload.get('worktreeGitRoot')
            if (isinstance(workspace, str) and isinstance(git_root, str)
                    and len(workspace) <= 4096 and len(git_root) <= 4096):
                target, checkout = Path(workspace), Path(git_root)
                if (target.is_absolute() and checkout.is_absolute() and target.is_dir()
                        and target == target.resolve() and checkout == checkout.resolve()
                        and target != self.workspace and target.is_relative_to(checkout)
                        and str(target.relative_to(checkout)) == pending['source']['subpath']
                        and git_identity(target) == pending['source']
                        and git_identity(self.workspace) == pending['source']):
                    destination = {'workspace': str(target), 'binding': pending['binding'],
                                   'source': pending['source'], 'assurance': 'observed; not host authentication'}
        self.update(recovery_pending=None, recovery_workspace=destination)

    def recovery_setup(self, event: dict[str, Any], state: dict[str, Any]) -> bool:
        recovery = self.read().get('recovery_workspace') or {}
        if not recovery or recovery.get('binding') != self.binding(state):
            return False
        target = Path(recovery['workspace'])
        if git_identity(target) != recovery.get('source') or git_identity(self.workspace) != recovery.get('source'):
            return False
        words = runtime_words(event)
        if words:
            try:
                cwd = runtime_command.validate_workdir(words, event, self.workspace)
                if (len(words) < 6 or not runtime_command.installed(words, cwd)
                        or words[2] != 'flow' or words[3] not in {'activate', 'start', 'report', 'diagnose', 'wait'}):
                    return False
                value = runtime_command.workspace_selector(words[4:])
                return value is not None and (cwd / value).resolve() == target
            except ValueError:
                return False
        # A fresh scope proposal is needed before initializing that exact checkout.
        # Once initialized, only its own Controller may authorize writes there.
        marker = target/'.taskplane'/('workflow-' + hashlib.sha256(self.root.encode()).hexdigest()[:32] + '.json')
        if marker.exists():
            return False
        args = event.get('tool_input', {})
        if not isinstance(args, dict):
            return False
        values = [args.get('file_path') or args.get('path')]
        if (event.get('tool_name') or event.get('tool')) == 'apply_patch':
            patch = args.get('command', args.get('input', args.get('patch', '')))
            values = [line.split(': ', 1)[1] for line in patch.splitlines()
                      if line.startswith(('*** Add File: ', '*** Update File: ', '*** Delete File: ', '*** Move to: '))] if isinstance(patch, str) else []
        return bool(values) and all(isinstance(v, str) and Path(v).is_absolute() and Path(v).is_relative_to(target)
                                    for v in values) and bootstrap_write(target, event, {})

    def bootstrap_command(self, event: dict[str, Any]) -> bool:
        words = runtime_words(event)
        if readonly_command(event):
            return True
        try:
            cwd = runtime_command.validate_workdir(words, event, self.workspace)
        except ValueError:
            return False
        if not runtime_command.installed(words, cwd):
            return False
        if runtime_command.diagnostic(words) or words[2:] == ["workflow", "--help"]:
            return True
        if words[2] == 'workflow':
            workflow_command(self.workspace, event, {})
            return True
        try:
            value = runtime_command.workspace_selector(words[3:])
            if value is None or runtime_command.resolve_selection(value, event, self.workspace) != self.workspace:
                return False
        except (w.Refusal, OSError, ValueError):
            return False
        return ((words[2] == 'flow' and len(words) > 3 and words[3] in {'activate', 'deactivate', 'start', 'report', 'diagnose', 'resume', 'wait'})
                or words[2] == 'graph' and 'scan' in words[3:]
                or words[2:4] == ['review', 'start'])

    def guard_bootstrap(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        tool = event.get('tool_name') or event.get('tool')
        args = event.get('tool_input', {})
        w.require(isinstance(args, dict), 'scope_violation', self.guidance(state))
        try:
            cwd = runtime_command.validate_workdir(runtime_words(event), event, self.workspace)
        except ValueError as exc:
            raise w.Refusal("scope_violation", str(exc)) from None
        mismatch = runtime_command.collision(runtime_words(event), cwd)
        w.require(not mismatch, 'scope_violation', mismatch or '')
        if tool == 'write_stdin':
            self.guard_setup_input(event)
            return
        if self.recovery_action(event, state) or self.recovery_setup(event, state):
            return
        if tool in READ_TOOLS | QUESTION_TOOLS or execution_entry(event):
            return
        if tool == 'Skill' and args.get('skill') in {'taskplane:tp-help', 'taskplane:tp-status'}:
            return
        if tool in {'Bash', 'exec_command'} and self.bootstrap_command(event):
            return
        targets = []
        if tool in {'Write', 'Edit', 'write_file', 'edit_file'}:
            targets = [args.get('file_path') or args.get('path')]
        elif tool == 'apply_patch':
            patch = args.get('command', args.get('input', args.get('patch', '')))
            if isinstance(patch, str):
                targets = [line.split(': ', 1)[1] for line in patch.splitlines()
                           if line.startswith(('*** Add File: ', '*** Update File: ', '*** Delete File: ', '*** Move to: '))]
        for value in targets:
            w.require(isinstance(value, str), 'scope_violation', self.guidance(state))
            path = Path(value)
            if path.is_absolute() and path.is_relative_to(self.workspace):
                path = path.relative_to(self.workspace)
            value = path.as_posix()
            w.require(value.startswith('.taskplane/bootstrap/'), 'scope_violation', self.guidance(state))
            evidence.path(self.workspace, value)
        w.require(bool(targets), 'scope_violation', self.guidance(state))

    def checkpoint(self, state: dict[str, Any]) -> dict[str, Any] | None:
        if not state.get('visits') or not w.current(state).get('packet'):
            return None
        stage = w.current(state)
        checkpoint_binding = w.binding(state, stage['packet'])
        if (stage.get('phase') == 'plan' and stage.get('decision') == 'approved'
                and stage.get('approved_scope_digest') == primitives.content_fingerprint(state['scope'])
                and stage.get('checkpoint_scope_digest')):
            checkpoint_binding['scope_digest'] = stage['checkpoint_scope_digest']
        return {**{k: v for k, v in checkpoint_binding.items() if k != 'revision'},
                'packet_revision': stage.get('packet_revision')}

    def native_snapshot(self, state: dict[str, Any], artifact: str, *, current: bool = True) -> tuple[Path, dict[str, Any]]:
        """Resolve only this run's native projection, never an arbitrary opener target."""
        target = evidence.path(self.workspace, artifact)
        w.require(target.parent == self.workspace/'.taskplane' and
                  (target.name == 'dashboard.html' or re.fullmatch(r'snapshot-[a-f0-9-]+\.html', target.name)),
                  'invalid_evidence', 'Use the native dashboard or its immutable snapshot.')
        if target.name == 'dashboard.html':
            selection = json.loads(target.with_suffix('.selection.json').read_text())
            immutable = Path(selection['snapshot'])
            w.require(immutable.is_relative_to(self.workspace/'.taskplane'), 'invalid_evidence', 'Foreign dashboard snapshot.')
            immutable = evidence.path(self.workspace, str(immutable.relative_to(self.workspace)))
            w.require(target.read_bytes() == immutable.read_bytes(), 'stale_checkpoint', 'Dashboard bytes differ from selected snapshot.')
        else:
            immutable = target
        model_path = evidence.path(self.workspace, str(immutable.with_suffix('.json').relative_to(self.workspace)))
        model = json.loads(model_path.read_text())
        snapshot = model.get('snapshot', {})
        expected = self.binding(state)
        if not current:
            expected.pop('revision')
        w.require(all(snapshot.get(k) == v for k,v in expected.items()) and snapshot.get('workspace') == str(self.workspace)
                  and snapshot.get('root') == self.root and not snapshot.get('historical'),
                  'stale_checkpoint', 'Regenerate the native dashboard for the current run, visit and revision.')
        w.require(immutable.parent == self.workspace/'.taskplane' and
                  self.checkpoint(model.get('workflow', {})) == self.checkpoint(state),
                  'stale_checkpoint', 'Dashboard checkpoint or scope differs from the current output.')
        return immutable, model

    def present(self, state: dict[str, Any], artifact: str, outcome: str, note: str) -> None:
        w.require(state.get('visits'), 'state_unavailable', 'Initialize the harness before dashboard handoff.')
        w.require(outcome in {'linked', 'verified', 'blocked'} and 0 < len(note.strip()) <= 2048,
                  'invalid_evidence', 'Record the actual link/open outcome and its evidence or limitation.')
        immutable, _ = self.native_snapshot(state, artifact)
        from .snapshot_retention import pin
        pin(self.workspace, immutable, 'harness-presentation/' + state['run'])
        self.update(presentation={'binding': self.binding(state), 'checkpoint': self.checkpoint(state),
                                  'artifact': str(immutable), 'outcome': outcome, 'note': note.strip(),
                                  'assurance': 'observed', 'digest': hashlib.sha256(immutable.read_bytes()).hexdigest(),
                                  'model_digest': hashlib.sha256(immutable.with_suffix('.json').read_bytes()).hexdigest()}, waiting=None)

    def presentation_valid(self, state: dict[str, Any]) -> bool:
        try:
            presentation = self.read().get('presentation') or {}
            checkpoint = self.checkpoint(state)
            if not checkpoint or presentation.get('checkpoint') != checkpoint or state.get('invalidation_pending'):
                return False
            if presentation.get('outcome') not in {'linked', 'verified', 'blocked'}:
                return False
            binding = presentation.get('binding', {})
            expected = self.binding(state)
            if binding != expected:
                # Only the approval of this exact, previously shown packet may
                # increment the revision without another presentation.
                if (w.current(state)['decision'] != 'approved'
                        or binding != {**expected, 'revision': state['revision'] - 1}
                        or not any(d.get('choice') == 'approved' and
                            all(d.get('binding', {}).get(k) == v for k, v in binding.items()) and
                            d.get('binding', {}).get('checkpoint') == checkpoint['checkpoint']
                            for d in state['decisions'].values())):
                    return False
            artifact = str(Path(presentation['artifact']).relative_to(self.workspace))
            immutable, model = self.native_snapshot(state, artifact, current=False)
            return (model['snapshot'].get('revision') == binding['revision']
                    and hashlib.sha256(immutable.read_bytes()).hexdigest() == presentation.get('digest')
                    and hashlib.sha256(immutable.with_suffix('.json').read_bytes()).hexdigest() == presentation.get('model_digest'))
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def dashboard_opener(self, event: dict[str, Any], state: dict[str, Any]) -> bool:
        if (event.get('tool_name') or event.get('tool')) not in {
                'mcp__codex_app__open_in_codex', 'mcp__codex_app.open_in_codex'}:
            return False
        args = event.get('tool_input', {})
        if (not isinstance(args, dict) or set(args) - {'target', 'placement', 'threadId'}
                or args.get('threadId', self.root) != self.root):
            return False
        target = args.get('target', {})
        if not isinstance(target, dict):
            return False
        if target.get('type') == 'file' and set(target) <= {'type', 'path'}:
            value = target.get('path')
        elif target.get('type') == 'browser' and set(target) == {'type', 'url'}:
            url = target.get('url')
            if not isinstance(url, str):
                return False
            try:
                parsed = urlsplit(url)
                value = url2pathname(parsed.path)
                canonical = Path(value).as_uri()
            except ValueError:
                return False
            if (parsed.scheme != 'file' or parsed.netloc or parsed.query or parsed.fragment
                    or canonical != url):
                return False
        else:
            return False
        if not isinstance(value, str) or not Path(value).is_absolute():
            return False
        try:
            self.native_snapshot(state, str(Path(value).relative_to(self.workspace)))
        except (OSError, ValueError, KeyError, TypeError):
            return False
        return True

    def stop(self, event: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        data = self.read()
        if not data.get('selected') or state.get('finished') or state.get('status') == 'cancelled':
            return {}
        waiting = data.get('waiting') or {}
        if waiting.get('binding') == self.binding(state):
            return {'systemMessage': 'Taskplane waiting for user input: ' + waiting['reason']}
        if not state.get('visits'):
            reason = self.guidance(state)
        elif state.get('invalidation_pending'):
            reason = 'Taskplane evidence is stale; resolve the current phase before reporting completion.'
        elif w.current(state)['decision'] in {'not_requested', 'changes_requested', 'rejected', 'stale'}:
            reason = 'Taskplane phase evidence is not submitted. Submit the review/current phase and present its native dashboard before completing.'
        elif not self.presentation_valid(state):
            reason = 'Taskplane dashboard handoff is missing for this checkpoint. Link/open the current native dashboard and record flow present with the actual outcome.'
        else:
            return {}
        # One corrective continuation, never a forced loop while the user waits.
        return {'systemMessage': reason} if event.get('stop_hook_active') else {'decision': 'block', 'reason': reason}
