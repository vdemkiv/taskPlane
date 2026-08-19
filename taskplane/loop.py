"""The Evaluate-Loop engine — owned by taskplane.

taskplane owns the loop state machine, sequences the DoR/DoD gates, activates
each step's contract (so the PreToolUse hook enforces it), and records every
transition to `.taskplane/trace.jsonl`. The role agents are pluggable step
workers: the engine tells the driver which role to run and under which
contract; the driver runs it and reports the outcome back via `gate`.

State machine (per docs/loop-design.md, answers locked 2026-07-11):
  init → (pm if free-text goal, else optional design or plan)
  pm      → optional design → design_approval (human) → plan
  plan    → plan_approval (human) → execute
  execute → evaluate
  evaluate: pass → next task, or → em when all tasks pass
            unavailable → next task/em with a visible warning, never fix
            fail → fix (if fix_cycles < max) else escalated (human)
  fix     → evaluate
  em      → signoff (human) → retro/graph true-up → done
  escalated → (human) retry | skip | abort

Human gates: design approval (when requested), plan approval, and EM sign-off.
On FAIL: auto-fix up to
max_fix_cycles (default 2), then escalate. Goal input: free-text (→pm) or an
existing spec (→plan).
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import time

import authority as authority_engine
import command_wave
import depgraph
import evaluation_output
import host_capabilities
import kb
import lens as lens_router
import loop_status
import loop_recovery
import retro as retro_engine
import requirements as reqs
import review_retry
import runtime_eval
import review_session as review_session_engine
import review_dor
import storage as runtime_storage
import taskplane_lite as tp
import yield_meter

LOOP_FILE = "loop.json"

# R-0006 row 1: the EVALUATE step routes lenses with the BUILD stage
# profile (route v2: build-profile candidates, R-0001 budget 5-7/cap-8
# inherited verbatim, component assembly from R-0003). ONE constant feeds
# BOTH the evaluate brief's routing and _evaluation_errors' expected-lens
# derivation, so the validator's expectation can never drift from what
# was dispatched. Final EM uses the review profile through the same kernel.
EVALUATE_ROUTE_STAGE = "build"


_review_kernel_binding_key = review_retry.binding_key
review_kernel_binding = review_retry.binding
review_session_authority_gate = review_session_engine.request_authority


def _consolidated_enabled() -> bool:
    return os.environ.get("TASKPLANE_CONSOLIDATED_FLOW", "").strip().lower() \
        in {"1", "true", "yes", "on"}


def _authorization_fields(ws: str, state: dict) -> dict:
    """Build the semantic preimplementation envelope from engine facts."""
    requirement = reqs.get_requirement(ws, state.get("requirement_id")) or {}
    design, _ = _design_contract(ws)
    tasks = state.get("tasks") or []
    scope = sorted({str(path) for task in tasks
                    for path in task.get("scope") or []})
    contracts = {
        str(row.get("id")): str(row.get("relation") or "")
        for row in requirement.get("contracts") or []
        if isinstance(row, dict) and row.get("id")
    }
    for row in (design or {}).get("contracts") or []:
        if isinstance(row, dict) and row.get("id"):
            contracts[str(row["id"])] = {
                "relation": str(row.get("relation") or ""),
                "description": str(row.get("description") or ""),
            }
    plan = [{
        "id": str(task.get("id") or ""),
        "scope": sorted(str(path) for path in task.get("scope") or []),
        "tests": str(task.get("tests") or ""),
        "deps": sorted(str(dep) for dep in task.get("deps") or []),
        "variant": task.get("variant"),
    } for task in tasks]
    return {
        "requirement": str(state.get("requirement_id") or ""),
        "acceptance": list(requirement.get("acceptance") or []),
        "target": {"repository": os.path.realpath(ws),
                   "revision": (state.get("authority_target_revision") or
                                tp.git_head(ws))},
        "scope": scope,
        "contracts": contracts,
        "design": {
            "decision": str((design or {}).get("decision") or ""),
            "depth_policy": ((design or {}).get("graph") or {}).get(
                "depth_policy") or {},
        },
        "plan": {"tasks": plan, "parallel": bool(state.get("parallel"))},
        "dynamic_validation": state.get("dynamic_validation_intent", "declared"),
        "sandbox": state.get("sandbox_authority", "ordinary_scoped_activity"),
        "recovery": {"max_fix_cycles": int(state.get("max_fix_cycles", 2)),
                     "gate_weakening": False},
        "evaluation": "declared tests, routed lenses, collection",
        "artifact_delivery": ["canonical_json", "inline_or_complete_markdown"],
        "execution_bounds": {"parallel": bool(state.get("parallel")),
                             "external_effects": False},
    }


def _product_definition_gate(requirement: dict) -> dict:
    """Product refinement is mechanical; strategic advice is attributable."""
    text = [str(requirement.get("title") or ""),
            *[str(x) for x in requirement.get("acceptance") or []]]
    advice = review_dor.north_star_advice(
        text, explicit=bool(requirement.get("north_star_requested")),
        advice=requirement.get("north_star_advice"))
    evidence = {
        "requirement": requirement.get("title") or requirement.get("id"),
        "acceptance": requirement.get("acceptance"),
        # Pass the facts themselves. Truthy ``checked/items`` envelopes made
        # empty collections look complete and allowed incomplete refinement.
        "contracts": requirement.get("contracts"),
        "dependencies": requirement.get("dependencies"),
        "nfrs": requirement.get("nfrs"),
        "score": requirement.get("score"),
    }
    return {**authority_engine.mechanical_definition_gate("product", evidence),
            "north_star": advice}


def _preview_feedback(state: dict, text: str, *, actor: str,
                      authenticated: bool, kind: str) -> dict:
    return authority_engine.preview_change(
        text, actor=actor, authenticated=authenticated,
        requirement=str(state.get("requirement_id") or ""),
        target={"revision": str(state.get("authority_target_revision") or
                                state.get("baseline") or "")}, kind=kind)


def request_human_decision(state: dict, reason: str, response: object, *,
                           actor: str, thread: str, revision: str,
                           consumed: bool = False, fact: str = "",
                           consequence: str = "") -> dict:
    """Single production boundary for every exceptional human decision."""
    receipt = state.get("authority_receipt") or {}
    return authority_engine.decision_input(
        reason, response, fact=fact, consequence=consequence,
        actor=actor, thread=thread, revision=revision,
        expected_actor=str(receipt.get("actor") or ""),
        expected_thread=str(receipt.get("thread") or ""),
        expected_revision=str(state.get("authority_target_revision") or
                              state.get("baseline") or ""),
        consumed=consumed)


def _trace_effect_seen(ws: str, effect_id: str) -> bool:
    root = os.path.realpath(tp.tp_dir(ws))
    for path in tp.trace_paths(ws):
        fd = None
        try:
            absolute = os.path.abspath(path)
            if os.path.commonpath((root, absolute)) != root:
                continue
            before = os.lstat(absolute)
            if not stat.S_ISREG(before.st_mode):
                continue
            fd = os.open(absolute, os.O_RDONLY |
                         getattr(os, "O_NOFOLLOW", 0))
            after = os.fstat(fd)
            if not stat.S_ISREG(after.st_mode) or \
                    (before.st_dev, before.st_ino) != \
                    (after.st_dev, after.st_ino):
                os.close(fd)
                fd = None
                continue
            with os.fdopen(fd, encoding="utf-8") as handle:
                fd = None
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("authority_effect_id") == effect_id:
                        return True
        except OSError:
            continue
        finally:
            if fd is not None:
                os.close(fd)
    return False


def _open_directory_without_symlinks(path: str) -> int:
    """Open an absolute directory while rejecting every symlink component."""
    directory = os.path.abspath(path)
    drive, tail = os.path.splitdrive(directory)
    root = drive + os.sep if drive else os.sep
    parts = [part for part in tail.split(os.sep) if part]
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    supports_relative_open = (
        directory_flag is not None and nofollow is not None and
        os.open in getattr(os, "supports_dir_fd", set()))
    if supports_relative_open:
        flags = os.O_RDONLY | directory_flag | nofollow
        current_fd = os.open(root, os.O_RDONLY | directory_flag)
        current_path = root
        try:
            for part in parts:
                candidate = os.path.join(current_path, part)
                info = os.lstat(candidate)
                if stat.S_ISLNK(info.st_mode):
                    raise OSError(
                        "authority trace path contains a symlink: " +
                        candidate)
                if not stat.S_ISDIR(info.st_mode):
                    raise OSError(
                        "authority trace path component is not a directory: " +
                        candidate)
                next_fd = None
                try:
                    next_fd = os.open(part, flags, dir_fd=current_fd)
                    opened = os.fstat(next_fd)
                    if not stat.S_ISDIR(opened.st_mode) or (
                            info.st_dev, info.st_ino) != (
                                opened.st_dev, opened.st_ino):
                        raise OSError(
                            "authority trace path component changed while "
                            "opening: " + candidate)
                except Exception:
                    if next_fd is not None:
                        os.close(next_fd)
                    raise
                os.close(current_fd)
                current_fd = next_fd
                current_path = candidate
            return current_fd
        except Exception:
            os.close(current_fd)
            raise

    # Platforms without relative O_NOFOLLOW traversal still fail closed for
    # ordinary accidental substitution by checking every ancestor before the
    # final open. The deployment threat model excludes a hostile same-UID
    # process racing this fallback.
    current_path = root
    final_info = os.lstat(root)
    for part in parts:
        current_path = os.path.join(current_path, part)
        final_info = os.lstat(current_path)
        if stat.S_ISLNK(final_info.st_mode):
            raise OSError(
                "authority trace path contains a symlink: " + current_path)
        if not stat.S_ISDIR(final_info.st_mode):
            raise OSError(
                "authority trace path component is not a directory: " +
                current_path)
    flags = os.O_RDONLY
    if directory_flag is not None:
        flags |= directory_flag
    dir_fd = os.open(directory, flags)
    opened = os.fstat(dir_fd)
    if not stat.S_ISDIR(opened.st_mode) or (
            final_info.st_dev, final_info.st_ino) != (
                opened.st_dev, opened.st_ino):
        os.close(dir_fd)
        raise OSError("authority trace directory changed while opening")
    return dir_fd


def _append_authority_trace(ws: str, event: str, data: dict) -> None:
    """Append one authority trace without following directory/file links."""
    directory = os.path.abspath(tp.tp_dir(ws))
    dir_fd = _open_directory_without_symlinks(directory)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    supports_relative_open = os.open in getattr(os, "supports_dir_fd", set())
    trace_fd = None
    existing = None
    try:
        name = "trace.jsonl"
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if nofollow is not None:
            flags |= nofollow
        if nofollow is None:
            trace_path = os.path.join(directory, name)
            try:
                existing = os.lstat(trace_path)
            except FileNotFoundError:
                existing = None
            if existing is not None and stat.S_ISLNK(existing.st_mode):
                raise OSError(
                    "authority trace file is a symlink: " + trace_path)
        if supports_relative_open:
            trace_fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
        else:
            trace_fd = os.open(os.path.join(directory, name), flags, 0o600)
        current = os.fstat(trace_fd)
        if not stat.S_ISREG(current.st_mode):
            raise OSError("authority trace target is not a regular file")
        if existing is not None and (
                existing.st_dev, existing.st_ino) != (
                    current.st_dev, current.st_ino):
            raise OSError("authority trace file changed while opening")
        record = {"event": event, "ts": time.time(), **data}
        raw = (json.dumps(record, default=str) + "\n").encode("utf-8")
        written = os.write(trace_fd, raw)
        if written != len(raw):
            raise OSError("authority trace append was incomplete")
        os.fsync(trace_fd)
    finally:
        if trace_fd is not None:
            os.close(trace_fd)
        os.close(dir_fd)


def _kb_effect_seen(ws: str, effect_id: str) -> bool:
    try:
        return any((row.get("links") or {}).get("authority_effect") == effect_id
                   for row in kb.list_decisions(ws))
    except (OSError, ValueError, TypeError):
        return False


def _enqueue_authority_effect(state: dict, effect_id: str, *,
                              trace_event: str, trace_data: dict,
                              kb_data: dict | None = None) -> None:
    outbox = state.setdefault("authority_effect_outbox", {})
    outbox.setdefault(effect_id, {
        "schema": "taskplane.authority-effect/v1", "status": "pending",
        "trace": {"delivered": False, "event": trace_event,
                  "data": trace_data},
        "kb": ({"delivered": False, "data": kb_data}
               if kb_data is not None else None),
    })


def reconcile_authority_effects(ws: str) -> dict:
    """Deliver durable authority effects idempotently after state commit."""
    delivered = pending = 0
    with mutate(ws) as state:
        if state is None:
            return {"delivered": 0, "pending": 0}
        for effect_id, row in (state.get("authority_effect_outbox") or {}).items():
            if row.get("status") == "delivered":
                delivered += 1
                continue
            trace_effect = row.get("trace") or {}
            try:
                if not trace_effect.get("delivered"):
                    if not _trace_effect_seen(ws, effect_id):
                        _append_authority_trace(
                            ws, str(trace_effect.get("event") or
                                    "authority_effect"),
                            {**dict(trace_effect.get("data") or {}),
                             "authority_effect_id": effect_id})
                    trace_effect["delivered"] = _trace_effect_seen(ws, effect_id)
                kb_effect = row.get("kb")
                if trace_effect.get("delivered") and kb_effect and not \
                        kb_effect.get("delivered"):
                    if not _kb_effect_seen(ws, effect_id):
                        data = dict(kb_effect.get("data") or {})
                        links = {**dict(data.pop("links", {}) or {}),
                                 "authority_effect": effect_id}
                        kb.record_decision(ws, links=links, **data)
                    kb_effect["delivered"] = _kb_effect_seen(ws, effect_id)
            except Exception as exc:  # effect remains durable for retry
                row["last_error"] = f"{exc.__class__.__name__}: {exc}"
            complete = bool(trace_effect.get("delivered")) and (
                row.get("kb") is None or bool((row.get("kb") or {}).get(
                    "delivered")))
            if complete:
                row["status"] = "delivered"
                row.pop("last_error", None)
                delivered += 1
            else:
                row["status"] = "pending"
                pending += 1
    return {"delivered": delivered, "pending": pending}


def _host_session_envelope(state: dict, event: dict,
                           host_event: object | None) -> dict:
    """Bind trusted-session attribution to the loop's current target."""
    expected_revision = str(state.get("authority_target_revision") or
                            state.get("baseline") or "")
    receipt = state.get("authority_receipt") or {}
    envelope = authority_engine.HostSessionAdapter().observe(
        event, host_event,
        expected_actor=str(receipt.get("actor") or ""),
        expected_thread=str(receipt.get("thread") or ""),
        expected_revision=expected_revision,
        expected_target={"revision": expected_revision})
    if envelope.get("attributed") and not all((
            str(receipt.get("actor") or "").strip(),
            str(receipt.get("thread") or "").strip(), expected_revision)):
        return {**envelope, "attributed": False,
                "reasons": ["current_authority_unbound"]}
    return envelope


def handle_host_input(ws: str, event: dict,
                      host_event: object | None = None) -> dict:
    """Consume one trusted local host/session event.

    The supported deployment is a single trusted Codex/Claude session.  The
    separate adapter observation supplies attribution; labels in the event
    body do not.  Stale and replay checks remain mechanical and atomic.
    """
    if not isinstance(event, dict):
        return {"error": "host event must be a mapping"}
    reconcile_authority_effects(ws)
    kind = str(event.get("type") or "").strip().lower()
    if kind == "preview_feedback":
        with mutate(ws) as state:
            if state is None:
                return {"error": "no active loop"}
            envelope = _host_session_envelope(state, event, host_event)
            if not envelope["attributed"]:
                return {"accepted": False, "reasons": envelope["reasons"]}
            expected_revision = str(state.get("authority_target_revision") or
                                    state.get("baseline") or "")
            if envelope["revision"] != expected_revision:
                return {"accepted": False, "reasons": ["wrong_revision"]}
            event_id = envelope["event_id"]
            consumed = state.setdefault("consumed_host_events", {})
            if event_id in consumed:
                return {"accepted": False, "reasons": ["replayed_event"]}
            change = _preview_feedback(
                state, str(event.get("text") or ""), actor=envelope["actor"],
                authenticated=True,
                kind=str(event.get("change_kind") or "behavioral"))
            if not change["accepted"]:
                return change
            state.setdefault("preview_changes", []).append(change)
            if change["reauthorization_required"]:
                state["reauthorization_required"] = True
            consumed[event_id] = {
                "actor": envelope["actor"], "thread": envelope["thread"],
                "revision": envelope["revision"],
                "target": envelope["target"], "source": envelope["source"],
                "event_ref": envelope["event_ref"],
            }
            _enqueue_authority_effect(
                state, f"preview:{event_id}", trace_event="preview_change",
                trace_data={"actor": change["actor"],
                            "kind": str(event.get("change_kind") or
                                        "behavioral"),
                            "material": change["material"],
                            "change": change["fingerprint"]})
        effects = reconcile_authority_effects(ws)
        return {**change, "effect_delivery": effects}
    if kind == "human_decision":
        with mutate(ws) as state:
            if state is None:
                return {"error": "no active loop"}
            envelope = _host_session_envelope(state, event, host_event)
            if not envelope["attributed"]:
                return {"authorized": False, "human_required": True,
                        "reasons": envelope["reasons"]}
            decision_id = envelope["event_id"]
            consumed = state.setdefault("consumed_host_decisions", {})
            response = event.get("response")
            if isinstance(response, dict):
                response = {**response, "authenticated": True}
            result = request_human_decision(
                state, str(event.get("reason") or "unsafe_or_ambiguous"),
                response, actor=envelope["actor"],
                thread=envelope["thread"], revision=envelope["revision"],
                consumed=decision_id in consumed,
                fact=str(event.get("fact") or ""),
                consequence=str(event.get("consequence") or ""))
            if result["authorized"]:
                consumed[decision_id] = {
                    "actor": envelope["actor"],
                    "thread": envelope["thread"],
                    "revision": envelope["revision"],
                    "target": envelope["target"],
                    "source": envelope["source"],
                    "event_ref": envelope["event_ref"],
                }
            return result
    return {"error": "host event type must be preview_feedback|human_decision"}


def _derive_consolidated_authority(ws: str, state: dict,
                                   stage: str) -> dict | None:
    packet, receipt = (state.get("authority_packet"),
                       state.get("authority_receipt"))
    if not _consolidated_enabled() or not packet or not receipt:
        return None
    return authority_engine.derive(
        packet, receipt, stage=stage,
        current=_authorization_fields(ws, state),
        actor=str(receipt.get("actor") or ""),
        thread=str(receipt.get("thread") or ""))


def authorize_routine_flow(ws: str, flow: str) -> dict:
    """Production entry point used by each host flow to derive authority."""
    normalized = str(flow or "").strip().lower().replace("-", "_")
    if normalized not in authority_engine.ROUTINE_FLOWS:
        return {"error": f"unknown routine flow '{flow}'"}
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    derived = _derive_consolidated_authority(ws, state, normalized)
    if derived is None:
        return {"error": "consolidated authorization is unavailable"}
    tp.trace(ws, "authority_derived", flow=normalized,
             authorized=derived["authorized"],
             receipt=derived.get("receipt_fingerprint"))
    return derived

def _state_dir(ws: str) -> str:
    """Loop coordination state. v1.5.1: state is PER-USER even in team/repo
    knowledge mode — share knowledge, not the state machine. Two teammates'
    concurrent loops in a committed loop.json are guaranteed unmergeable
    conflicts, and flock on a git-round-tripped file serializes nothing
    across machines. The ONE exception is the explicit TASKPLANE_STORE=repo
    env override (Claude Tag): there the sandbox is ephemeral and
    single-writer, so committed state is exactly what lets the next session
    resume the loop."""
    if tp.store_env() == "repo":
        return os.path.join(tp.kb_root(ws), "state")
    ext = os.path.join(tp.external_store_root(ws), "knowledge", "state")
    if os.path.exists(os.path.join(ext, LOOP_FILE)):
        return ext
    legacy = os.path.join(ws, "knowledge", "state")   # unmigrated project
    if os.path.exists(os.path.join(legacy, LOOP_FILE)):
        return legacy
    return ext


def state_dir(ws: str) -> str:
    """THE exported owner of the loop-state location rule (v2.3.0).

    Any module that touches per-user coordination state (loop.json,
    tracks.json — see docs/state-spec.md, 'Loop coordination state is
    per-user') must resolve its directory HERE instead of re-deriving via
    tp.kb_root/store_root: re-derivation is exactly how track state ended up
    in the committed team store on a team plan. TASKPLANE_STORE=repo remains
    the single exception, and this function owns it."""
    return _state_dir(ws)

# Non-build steps are read-only with artifact allowances; build/fix use plan scope.
# pm and em are two deliberate personas (split in v0.8.0): tp-product owns
# the requirement; tp-engineering owns the final all-lens review.
STEP_ROLE = {
    "pm": "tp-product",
    "design": "tp-designer",
    "plan": "tp-planner",
    "execute": "tp-executor",
    "evaluate": "tp-evaluator",
    "fix": "tp-fixer",
    "em": "tp-engineering",
}
HUMAN_STEPS = {"design_approval", "plan_approval", "selection",
               "signoff", "escalated",
               "done", "failed"}

COMMAND_WAVE_SCHEMA = command_wave.COMMAND_WAVE_SCHEMA
command_wave_create = command_wave.create
command_wave_resume = command_wave.resume
command_wave_update = command_wave.update

# A task is SETTLED when nothing further is owed on it: it passed, or the
# selection gate closed it (not_selected / reference), or a human skipped it.
# Wave readiness and "are we done?" both reason over this set.
SETTLED = {"passed", "not_selected", "reference", "skipped",
           "done", "external"}
# Statuses that SATISFY a dependency: the work exists (passed here,
# `done` seeded from outside the loop, `external` deferred to an
# external gate by an explicit human decision). `skipped` settles a
# task but does NOT satisfy its dependents (they cascade-skip).
DEP_SATISFIED = {"passed", "done", "external"}

# The canonical governance rail — (step, label). This is the SINGLE source a
# view renders its timeline from; the engine owns the machine, so a dashboard
# must derive its pipeline from here (via display_pipeline) rather than
# re-encode it and drift. is-human comes from HUMAN_STEPS, role from STEP_ROLE.
PIPELINE = [
    ("pm", "PM"), ("design", "Design"),
    ("design_approval", "Approve design"),
    ("plan", "Plan"), ("plan_approval", "Approve"),
    ("execute", "Execute"), ("evaluate", "Evaluate"), ("fix", "Fix"),
    ("em", "EM"), ("signoff", "Sign-off"),
    ("retro", "Retro + graph true-up"), ("done", "Done"),
]
# The A/B selection gate is spliced in before 'em', but only for an A/B loop
# that hasn't selected yet — one place owns that rule (display_pipeline).
SELECTION_STEP = ("selection", "Select")

_NATIVE_TERMINAL_STATES = frozenset(
    {"completed", "complete", "done", "cancelled", "failed", "failure"})


class NativeProgressSession:
    """One presentation-only PiP lifecycle over canonical snapshots."""

    def __init__(self) -> None:
        self.identity = None
        self.last_sequence = -1
        self.last_fingerprint = None
        self.opened = False
        self.closed = False

    def publish(self, snapshot: object) -> dict:
        identity = (snapshot.workflow_id, snapshot.run_id, snapshot.revision)
        if self.identity is not None and identity != self.identity:
            raise ValueError("progress session identity changed")
        if snapshot.sequence < self.last_sequence:
            raise ValueError("progress sequence moved backwards")
        if (snapshot.sequence == self.last_sequence and
                snapshot.fingerprint == self.last_fingerprint):
            return self._result(snapshot, "duplicate")
        if snapshot.sequence == self.last_sequence:
            raise ValueError("progress sequence conflicts with prior snapshot")
        if self.closed:
            raise ValueError("progress session is closed")
        self.identity = identity
        self.last_sequence = snapshot.sequence
        self.last_fingerprint = snapshot.fingerprint
        terminal = snapshot.state.lower() in _NATIVE_TERMINAL_STATES
        persistent = bool(snapshot.values.get("persistent", True))
        if terminal and not self.opened:
            transition = "none"
            self.closed = True
        elif not persistent and not self.opened:
            transition = "none"
        elif not self.opened:
            self.opened = True
            transition = "open"
        elif terminal:
            self.closed = True
            transition = "close"
        else:
            transition = "update"
        return self._result(snapshot, transition)

    def _result(self, snapshot: object, transition: str) -> dict:
        values = snapshot.to_dict()["values"]
        return {
            "schema": "taskplane.host-progress-session/v1",
            "transition": transition, "workflow_id": snapshot.workflow_id,
            "run_id": snapshot.run_id, "revision": snapshot.revision,
            "sequence": snapshot.sequence, "stage": snapshot.stage,
            "state": snapshot.state, "active_work": values.get("active_work"),
            "completed_work": values.get("completed_work"),
            "attention": values.get("attention", []),
            "last_update": values.get("last_update", snapshot.sequence),
            "tokens": values.get("tokens"),
        }


def project_agent_topology(events: list[dict]) -> dict:
    """Fold canonical dispatch events into a stable, phantom-free graph."""
    nodes, order, edges, edge_keys = {}, [], [], set()
    immutable = ("task_id", "slot_id", "role", "scope", "wave")
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        agent_id = str(event.get("agent_id") or "").strip()
        task_id = str(event.get("task_id") or "").strip()
        slot_id = str(event.get("slot_id") or "").strip()
        if not (agent_id and task_id and slot_id):
            continue
        normalized = {
            "agent_id": agent_id, "task_id": task_id, "slot_id": slot_id,
            "role": str(event.get("role") or "unknown"),
            "scope": list(event.get("scope") or []),
            "wave": str(event.get("wave") or ""),
            "state": str(event.get("state") or "unknown"),
            "attention": list(event.get("attention") or []),
            "outcome": event.get("outcome"),
        }
        prior = nodes.get(agent_id)
        if prior:
            if any(prior[key] != normalized[key] for key in immutable):
                raise ValueError(f"agent identity changed: {agent_id}")
            prior.update({key: normalized[key]
                          for key in ("state", "attention", "outcome")})
        else:
            nodes[agent_id] = normalized
            order.append(agent_id)
        for source, relationship in (
                [(str(event.get("retry_of") or ""), "retry")] +
                [(str(item), "dependency")
                 for item in event.get("depends_on") or []]):
            if not source:
                continue
            key = (source, agent_id, relationship)
            if key not in edge_keys:
                edges.append({"from": source, "to": agent_id,
                              "relationship": relationship})
                edge_keys.add(key)
    edges = [row for row in edges
             if row["from"] in nodes and row["to"] in nodes]
    return {"schema": "taskplane.host-agent-topology/v1",
            "nodes": [nodes[key] for key in order], "edges": edges}


def splice_selection(rail: list, state: dict | None) -> list:
    """Insert the A/B 'selection' gate before 'em' when the loop is an A/B
    round that hasn't selected yet. `rail` is any list whose items' [0] is a
    step id (with or without label/flag). Returns a NEW list. This is the ONE
    place the splice rule lives, so render()'s full rail and widget()'s
    collapsed spine can't disagree."""
    if not (state and state.get("ab") and not state.get("selection")):
        return list(rail)
    ids = [r[0] for r in rail]
    i = ids.index("em") if "em" in ids else len(rail)
    sel = (SELECTION_STEP[0], SELECTION_STEP[1], True)
    return list(rail[:i]) + [sel] + list(rail[i:])


def display_pipeline(state: dict | None = None) -> list:
    """The ordered rail a view should render: list of (step, label, is_human).
    Both dashboard.render() and dashboard.widget() derive from the engine
    (this + splice_selection), so the timeline and the human-gate set can't
    drift between the two renderers or from the engine."""
    rows = list(PIPELINE)
    if not (state and state.get("design_required")):
        rows = [row for row in rows
                if row[0] not in ("design", "design_approval")]
    elif state.get("design_only"):
        rows = [row for row in rows
                if row[0] in ("pm", "design", "design_approval", "done")]
    rail = [(s, lbl, s in HUMAN_STEPS) for s, lbl in rows]
    return splice_selection(rail, state)


def _next_unsettled_index(state: dict, after: int):
    """Next task index strictly after `after` whose task is not SETTLED, or
    None when the rest are all settled. Serial advance uses this so a task
    the skip-cascade already settled is never re-executed."""
    tasks = state.get("tasks") or []
    for i in range(after + 1, len(tasks)):
        if tasks[i].get("status") not in SETTLED:
            return i
    return None


def _loop_path(ws: str) -> str:
    return os.path.join(_state_dir(ws), LOOP_FILE)


def _legacy_loop_path(ws: str) -> str:
    return os.path.join(tp.tp_dir(ws), LOOP_FILE)


def _load_raw(ws: str) -> dict | None:
    p = _loop_path(ws)
    if not os.path.exists(p):
        p = _legacy_loop_path(ws)          # pre-spec state, read once
        if not os.path.exists(p):
            return None
    # v2.3.0: a corrupt loop.json fails CLOSED with a typed error naming the
    # file and a remedy (tp.StateError) — never a bare JSONDecodeError
    # traceback, and never a silent default that would mask the corruption.
    return tp.load_json(p, what="loop state file")


def load(ws: str) -> dict | None:
    """Load loop state and flush any crash-surviving authority outbox."""
    state = _load_raw(ws)
    if state is not None and any(
            row.get("status") != "delivered" for row in
            (state.get("authority_effect_outbox") or {}).values()):
        reconcile_authority_effects(ws)
        state = _load_raw(ws)
    return state


def save(ws: str, state: dict) -> None:
    os.makedirs(_state_dir(ws), exist_ok=True)
    # Atomic write (tp.atomic_write_json): parallel wave workers gate
    # concurrently against the shared loop.json — a torn read of a
    # half-written file is a corrupt loop that stalls everyone; a reader only
    # ever sees a complete state. (Lost-update races between concurrent
    # read-modify-write are serialized by `mutate()` below, which holds an
    # exclusive lock across the whole load→change→save.)
    tp.atomic_write_json(_loop_path(ws), state, indent=2)
    legacy = _legacy_loop_path(ws)         # migrate: single source of truth
    if os.path.exists(legacy):
        tp.safe_remove(legacy)


@contextlib.contextmanager
def mutate(ws: str):
    """Serialize a read-modify-write of the shared loop state. Concurrent wave
    workers each do load()→change→save(); without a lock two workers can read
    the same state and the second save clobbers the first's update (a gated
    task silently reverts to running and the loop stalls). An exclusive
    flock held across the whole critical section prevents that. Yields the
    current state dict; persists it on clean exit.

        with loop.mutate(ws) as st:
            task = next(t for t in st['tasks'] if t['id'] == tid)
            task['status'] = 'built'

    v2.3.0: the lock is tp.file_lock — where flock is unavailable or refused
    (Windows, FUSE/NFS/SMB mounts, exactly the hosts this plugin targets) it
    falls back to an atomic mkdir spin-lock, and if even that cannot be
    acquired it raises tp.StateError. Wave serialization is therefore never
    SILENTLY lost the way the old `except OSError: pass` fallback lost it.
    """
    os.makedirs(_state_dir(ws), exist_ok=True)
    with tp.file_lock(_loop_path(ws)):
        st = _load_raw(ws)
        original = (json.loads(json.dumps(st)) if st is not None else None)
        yield st
        if st is not None:
            fence = st.pop("_authority_revision_fence", None)
            if fence:
                before = tp.git_head(ws)
                if before != fence:
                    st.clear()
                    st.update(original or {})
                    st["_revision_fence_failed"] = {
                        "expected": str(fence), "actual": str(before)}
                    return
                save(ws, st)
                after = tp.git_head(ws)
                if after != fence:
                    if original is not None:
                        save(ws, original)
                    st.clear()
                    st.update(original or {})
                    st["_revision_fence_failed"] = {
                        "expected": str(fence), "actual": str(after)}
            else:
                save(ws, st)


TERMINAL_STEPS = ("done", "failed")


def init(ws: str, goal: str, spec_path: str | None = None,
         max_fix_cycles: int = 2, checkpoints=None,
         requirement_id: str | None = None, parallel: bool = False,
         design: bool = False, design_only: bool = False,
         force: bool = False) -> dict:
    checkpoints = list(checkpoints if checkpoints is not None else
                       ["plan", "em"])
    # v2.3.0: init over an IN-FLIGHT loop refuses by default — one mistyped
    # init must not silently reset a governed session's step, tasks,
    # approvals and baseline. `force` discards deliberately, and even then
    # the prior state file is archived (visible, recoverable), never erased.
    existing = load(ws)
    archived_to = None
    if existing and existing.get("step") not in TERMINAL_STEPS:
        if not force:
            return {"error": "an active loop already exists at step="
                             f"'{existing.get('step')}' — refusing to discard "
                             "its progress. Finish or abort it first "
                             "(`loop resolve abort`), or re-run init with "
                             "force to archive the current state and restart.",
                    "refused": True, "step": existing.get("step")}
        src = _loop_path(ws) if os.path.exists(_loop_path(ws)) \
            else _legacy_loop_path(ws)
        archived_to = _loop_path(ws) + time.strftime(
            ".replaced-%Y%m%d-%H%M%S") + f".{os.getpid()}"
        os.makedirs(_state_dir(ws), exist_ok=True)
        os.replace(src, archived_to)
        tp.trace(ws, "loop_init_replaced", prior_step=existing.get("step"),
                 archived_to=archived_to)
    state = {
        "governance_revision": 2,
        # Workers submit evidence; only the driver asks the engine to evaluate
        # a gate.  Older persisted loops omit this flag and remain resumable.
        "submission_required": True,
        "graph_governance": True,
        "goal": goal,
        "parallel": bool(parallel),
        "design_required": bool(design or design_only),
        "design_only": bool(design_only),
        "requirement_id": requirement_id,
        "spec_path": spec_path,
        "max_fix_cycles": int(max_fix_cycles),
        "checkpoints": checkpoints,
        "step": ("design" if spec_path and (design or design_only)
                 else "plan" if spec_path else "pm"),
        "tasks": None,
        "current_task": 0,
        "consumed_host_decisions": {},
        "consumed_host_events": {},
        "authority_effect_outbox": {},
    }
    save(ws, state)
    tp.trace(ws, "loop_init", goal=goal, spec_path=spec_path,
             first_step=state["step"], max_fix_cycles=max_fix_cycles,
             checkpoints=checkpoints, design=bool(design or design_only),
             design_only=bool(design_only))
    out = dict(state)
    if archived_to:
        out["previous_loop_archived"] = archived_to
        out["note"] = f"previous in-flight loop archived to {archived_to}"
    # v2.0.0: point the driver at prior gate snapshots (context cache) -
    # read the published state instead of re-deriving it.
    with contextlib.suppress(Exception):
        art = os.path.join(tp.store_root(ws), "artifacts")
        tracks = sorted(os.listdir(art)) if os.path.isdir(art) else []
        if tracks:
            out["prior_artifacts"] = {
                "path": art, "tracks": tracks,
                "note": "prior gate snapshots (dashboard, plan, "
                        "findings, graph, HEADLINES) - read these "
                        "before re-deriving context"}
    return out


# --------------------------------------------------------------- contracts

def _step_contract(step: str, state: dict, ws: str | None = None) -> dict:
    task = _current_task(state)
    if step == "pm":
        return tp.build_contract(
            f"PM: {state['goal']}", read_only=True,
            write_allow=["specs/**", "docs/**"],
            tools=["Read", "Grep", "Glob", "WebSearch", "Bash", "Write"])
    if step == "design":
        return tp.build_contract(
            f"DESIGN: {state['goal']}", read_only=True,
            write_allow=["design/**"],
            tools=["Read", "Grep", "Glob", "WebSearch", "Bash", "Write"])
    if step == "plan":
        return tp.build_contract(
            f"PLAN: {state['goal']}", read_only=True, write_allow=["plan/**"],
            tools=["Read", "Grep", "Glob", "Bash", "Write"])
    if step in ("execute", "fix"):
        verb = "EXECUTE" if step == "execute" else "FIX"
        return tp.build_contract(
            f"{verb}: {task['id']}", scope=task["scope"],
            test_command=task.get("tests"), plan_minted=True, regression_gate=True,
            tools=["Read", "Grep", "Glob", "Bash", "Write", "Edit",
                   "MultiEdit"])
    if step == "evaluate":
        return tp.build_contract(
            f"EVALUATE: {task['id']}", read_only=True,
            write_allow=runtime_storage.worker_write_allow(ws, ".eval/**"),
            tools=["Read", "Grep", "Glob", "Bash", "Write"])
    if step == "em":
        return tp.build_contract(
            "EM review", read_only=True,
            write_allow=runtime_storage.worker_write_allow(ws, ".em-review/**"),
            tools=["Read", "Grep", "Glob", "Bash", "Write", "Edit"])
    raise ValueError(f"no contract for step {step}")


def _bind_worker_submission(ws: str, state: dict, step: str,
                            contract: dict, task: dict | None) -> dict:
    """Bind worker lifecycle to its exact loop submission, without gating."""
    if not state.get("submission_required") or step not in {
            "execute", "fix", "evaluate", "em"}:
        return contract
    task_name = ((task or {}).get("id") or "engineering-signoff"
                 if step == "em" else (task or {}).get("id"))
    return tp.bind_submission_contract(
        contract, ws, task=str(task_name), stage=step, slot=tp.task_slot(),
        locator={"type": "loop_submission"},
        validation_rule="loop-submission/v1")


def _current_task(state: dict):
    tasks = state.get("tasks")
    if not tasks:
        return None
    i = state.get("current_task", 0)
    return tasks[i] if 0 <= i < len(tasks) else None


def _edge_nudges(ws: str, changed, base: str) -> list:
    """Spot side-effect channels the import scanner cannot see (v2.0.0):
    SQL/migrations, HTTP calls, queue/topic messaging in the diff. Each
    nudge asks the reviewer to record the runtime edge (`tp graph edge`)
    so the NEXT change to that surface has a true blast radius."""
    import re as _re
    import subprocess as _sp
    nudges = []
    try:
        names = " ".join(changed)
        if _re.search(r"\.sql\b|/migrations?/", names):
            nudges.append(
                "diff touches SQL/migrations - schema changes ripple to "
                "every consumer of those tables; record the edge: "
                "tp graph edge <consumer-module> <db-module> --kind data")
        diff = _sp.run(["git", "diff", "-U0", base, "--", *changed[:50]],
                       cwd=ws, capture_output=True, text=True
                       , encoding="utf-8", errors="replace").stdout[:60000]
        added = "\n".join(l for l in diff.splitlines()
                           if l.startswith("+"))
        if _re.search(r"https?://|requests\.|urllib|fetch\(|axios"
                      r"|http\.client|HttpClient", added):
            nudges.append(
                "diff adds HTTP calls - cross-service effects are not "
                "import edges; record them: tp graph edge <this-module> "
                "<called-service> --kind runtime")
        if _re.search(r"publish|subscribe|topic|queue|kafka|sqs|rabbit"
                      r"|emit\(", added, _re.I):
            nudges.append(
                "diff touches messaging (topic/queue) - consumers are "
                "invisible to the import graph; record them: tp graph "
                "edge <consumer> <contract:event-name> --kind consumes; "
                "record the producer with --kind provides. Dependency edges "
                "point from the dependent to the contract so contract changes "
                "impact consumers in the correct direction")
    except (OSError, _sp.SubprocessError, UnicodeDecodeError) as e:
        # Degraded nudging must be VISIBLE, never silent (v2.3.0): the
        # reviewer loses side-effect-channel hints, so say so once.
        import sys as _sys
        print(f"taskplane: edge-nudge scan degraded ({e.__class__.__name__}: "
              f"{e}) — record runtime edges manually via `tp graph edge`",
              file=_sys.stderr)
        try:
            tp.trace(ws, "edge_nudges_failed", error=str(e))
        except Exception:
            pass
    return nudges


def _diff_files(ws: str, base: str) -> list:
    import subprocess

    def run(args):
        return subprocess.run(["git", *args], cwd=ws, capture_output=True,
                              text=True, encoding="utf-8", errors="replace").stdout
    return [f for f in (run(["diff", "--name-only", base])
                        + run(["ls-files", "--others",
                               "--exclude-standard"])).splitlines() if f]


def _review_kernel(ws: str, diff_ws: str, *, base: str, step: str,
                   task: dict | None, graph: dict, impact: dict,
                   requirement: dict | None,
                   retry_context: dict | None = None) -> tuple[dict, dict]:
    """One evidence/routing kernel shared by Evaluate and final EM."""
    import hashlib
    import subprocess
    import review
    import review_evidence

    diff_rc, patch = review.canonical_diff_patch(diff_ws, base)
    if diff_rc:
        raise review.ReviewKernelError("canonical diff derivation failed")
    files = [f for f in _diff_files(diff_ws, base)
             if not f.startswith(lens_router.LOOP_OWNED)]
    head = tp.git_head(diff_ws) or ""
    target_material = {"workspace": os.path.realpath(diff_ws), "head": head,
                       "base": base, "step": step,
                       "task": (task or {}).get("id")}
    target = {**target_material, "fingerprint": hashlib.sha256(
        json.dumps(target_material, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")).hexdigest()}
    store = review_evidence.ArtifactStore(diff_ws)
    diff_ref = store.put("diff", {"base": base, "files": files,
                                  "patch": patch})
    stage = "review" if step == "em" else EVALUATE_ROUTE_STAGE
    manifest = review.start_review(
        diff_ws, target=target, graph=graph, impact=impact,
        diff={"files": files,
              "changed_symbols": review.changed_symbols_from_patch(patch),
              "artifact": review._portable_ref(diff_ref)},
        requirement=requirement or {},
        acceptance=(requirement or {}).get("acceptance") or [],
        contracts=(task or {}).get("contracts") or [],
        stage=stage,
        task_type=(task or {}).get("type"), base=base,
        caller_expander=review.bounded_caller_expander(graph),
        routing_content=review.changed_content_from_patch(patch),
        retry_lenses=((retry_context or {}).get("lenses")
                      if step == "evaluate" else None),
        retry_source_run_id=((retry_context or {}).get("source_run_id")
                             if step == "evaluate" else None))
    state = review._load_state(diff_ws, manifest.get("run_id"))
    return manifest, (state.get("routing") or {"lenses": [], "context": {
        "status": manifest.get("status"), "breadth": "routed"}})


# --------------------------------------------------------------- parallel

def _scopes_overlap(a, b) -> bool:
    """Two scopes conflict when one's fixed prefix contains the other's, on
    path-segment boundaries — conflicting tasks are serialized into later
    waves. Segment-aware so sibling dirs (src/a vs src/ab) do NOT collide,
    and empty-prefix globs don't conflict with everything. (The path math
    itself lives in the kernel — tp.scope_stems / tp.seg_prefix.)"""
    sa, sb = tp.scope_stems(a), tp.scope_stems(b)
    return any(tp.seg_prefix(x, y) or tp.seg_prefix(y, x)
               for x in sa for y in sb)


def wave(ws: str) -> dict:
    """The next parallel wave: every task whose dependencies have PASSED
    and whose scope is disjoint from the rest of the wave. Each entry ships
    its own contract + primed lenses + requirement — one governed agent per
    task, each in its own worktree. THE HARNESS IS PER AGENT: a worker's
    hook enforces its own task's contract in its own workspace."""
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    if not state.get("parallel"):
        return {"error": "loop is serial — `loop init --parallel` to enable"}
    if state["step"] != "execute":
        return {"error": f"waves only at execute (current: {state['step']})"}
    tasks = state.get("tasks") or []
    passed = {t["id"] for t in tasks
              if t.get("status") in DEP_SATISFIED}
    ready, held = [], []
    for t in tasks:
        if t.get("status") != "pending":
            continue
        if not set(t.get("deps") or []) <= passed:
            held.append({"task": t["id"],
                         "reason": "waiting on deps: "
                         + ",".join(sorted(set(t.get("deps") or []) - passed))})
            continue
        clash = [c["id"] for c in ready
                 if _scopes_overlap(t.get("scope"), c.get("scope"))
                 # Different A/B variants deliberately overlap in isolated
                 # worktrees; they never merge before human selection.
                 and not (state.get("ab") and t.get("variant")
                          and c.get("variant")
                          and t.get("variant") != c.get("variant"))]
        if clash:
            held.append({"task": t["id"],
                         "reason": f"scope overlaps {clash[0]} — next wave"})
            continue
        ready.append(t)

    entries = []
    for t in ready:
        dispatch = tp.dispatch_fields(
            "step", "tp-executor", t["id"], tp.step_tier("execute", t))
        task_ws = t.get("workspace") or runtime_storage.task_worktree_path(ws, t["id"])
        if not os.path.isdir(task_ws):
            task_ws = ws
        prime = lens_router.prime_scope(t.get("scope"),
                                        task_type=t.get("type"),
                                        workspace=task_ws)
        recalled = kb.retrieve(ws, files=t.get("scope") or [],
                               tags=[t["id"]], limit=3)
        rid = t.get("req") or state.get("requirement_id")
        rec = reqs.get_requirement(ws, rid) if rid else None
        is_variant = bool(state.get("ab") and t.get("variant"))
        entries.append({**dispatch,
            "task": {"id": t["id"], "scope": t.get("scope"),
                     "tests": t.get("tests"), "deps": t.get("deps") or [],
                     "variant": t.get("variant")},
            "worktree": runtime_storage.task_worktree_reference(ws, t["id"]),
            "merge_on_pass": not is_variant,
            "lenses": prime["lenses"],
            "language_references": (prime.get("context") or {}).get(
                "language_references") or [],
            "requirement": rec and {"id": rec["id"], "title": rec["title"],
                                    "acceptance": rec["acceptance"]},
            "design": _design_context(ws, state),
            "knowledge": kb.render_context(recalled),
            "runtime_evals": runtime_eval.guidance("execute"),
        })
    tp.trace(ws, "loop_wave", ready=[t["id"] for t in ready],
             held=[h["task"] for h in held])

    # Deadlock guard: nothing ready, nothing built to evaluate, yet tasks
    # are held — and none of them is held merely on a scope clash (which a
    # later wave clears). If every held task waits on a dep that can NEVER
    # pass (skipped/failed/absent) or on a cycle, the loop cannot self-
    # advance — surface it for the human instead of returning a silent
    # empty wave forever.
    built = any(t.get("status") == "built" for t in tasks)
    if not entries and not built and held:
        by_id = {t["id"]: t for t in tasks}
        stuck = []
        for h in held:
            t = by_id[h["task"]]
            unmet = set(t.get("deps") or []) - passed
            dead = [d for d in unmet
                    if d not in by_id
                    or by_id[d].get("status") in ("skipped", "failed")]
            waiting_on_scope = "scope overlaps" in h["reason"]
            if dead or (unmet and not waiting_on_scope
                        and not any(by_id.get(d, {}).get("status")
                                    in (None, "pending", "running", "built")
                                    for d in unmet)):
                stuck.append({"task": h["task"], "blocked_by": sorted(unmet),
                              "dead_deps": dead})
        if stuck:
            tp.trace(ws, "loop_deadlock", stuck=[s["task"] for s in stuck])
            return {
                "step": "execute", "parallel": True, "wave": [], "held": held,
                "deadlock": stuck,
                "error": "wave deadlock — held tasks depend on tasks that "
                         "can never pass (skipped/failed/missing or a "
                         "dependency cycle). Resolve with `loop resolve "
                         "skip|abort`, or fix plan/tasks.json deps.",
            }

    return {
        "step": "execute", "parallel": True,
        "wave": entries, "held": held,
        "runtime_evals": runtime_eval.guidance("execute"),
        "instruction": (
            "Dispatch ONE governed subagent per wave entry, concurrently. "
            "Per task: (1) `git worktree add <worktree> -b tp/<task>` from "
            "the approved baseline; (2) `tp.py loop claim <task> "
            "--agent-workspace <worktree>` — activates THAT task's contract "
            "in THAT worktree, so the hook confines the agent mechanically; "
            "(3) the subagent builds inside its worktree (TDD, primed "
            "lenses, acceptance criteria); (4) it COMMITS its work in the "
            "worktree (`git add -A && git commit`) and runs `tp.py loop "
            "submit pass|fail --task <id>`. The orchestrator alone runs "
            "the matching `loop gate`. When the wave empties, `loop next` "
            "evaluates each built task; on evaluate PASS merge its branch "
            "(`git merge tp/<task>`) and remove the worktree. "
            "EXCEPTION — entries with merge_on_pass=false are A/B variants: "
            "do NOT merge them; when all variants pass, the loop pauses at "
            "the SELECTION gate and the human picks what ships."),
    } if entries else {
        "step": "execute", "parallel": True, "wave": [], "held": held,
        "runtime_evals": runtime_eval.guidance("execute"),
        "instruction": "no dispatchable tasks — evaluate built tasks via "
                       "`loop next`, or resolve held dependencies.",
    }


def claim(ws: str, task_id: str, agent_ws: str) -> dict:
    """Activate `task_id`'s contract in the worker's own workspace
    (worktree). From here the worker's PreToolUse hook enforces this task's
    scope/tools/commands — the core invariant: every parallel agent runs
    under the harness, individually."""
    # v2.3.0 (scalability): DoR preparation shells out to git in the worker's
    # worktree (slow) — prepare OUTSIDE the global lock, then commit the
    # claim under the lock with a status RE-CHECK.
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    if not state.get("parallel"):
        # A1 (R-0007): a direct claim on a serial loop forms a wave whose
        # submits deadlock (decision 0011) — fail closed BEFORE any
        # contract/DoR work, backstopping wave()'s existing refusal.
        tp.trace(ws, "loop_claim_blocked", task=task_id, reason="serial_mode")
        return {"error": "loop was initialized without --parallel — a wave "
                         "cannot claim; re-init with --parallel or run "
                         "serially via `loop next`"}
    t = next((x for x in state.get("tasks") or [] if x["id"] == task_id),
             None)
    if t is None:
        return {"error": f"no task {task_id}"}
    if t.get("status") not in ("pending", "running"):
        return {"error": f"task {task_id} is {t.get('status')} — "
                         "not claimable"}
    contract = tp.build_contract(
        f"EXECUTE: {t['id']}", scope=t.get("scope"),
        test_command=t.get("tests"), plan_minted=True, regression_gate=True,
        tools=["Read", "Grep", "Glob", "Bash", "Write", "Edit",
               "MultiEdit"])
    agent_ws = os.path.abspath(agent_ws)
    locator_error = runtime_storage.worker_locator_error(ws, agent_ws, task_id)
    if locator_error: return {"error": locator_error, "task": task_id}
    contract = _bind_worker_submission(
        agent_ws, state, "execute", contract, t)
    snapshot = tp.git_head(agent_ws)
    dor_ready, blockers, warnings = tp.dor_check(
        contract, agent_ws, snapshot)
    if not dor_ready:
        tp.trace(ws, "loop_claim_blocked", task=task_id,
                 agent_workspace=agent_ws, dor_blockers=blockers)
        return {"error": "Definition of Ready failed — task was not "
                         "claimed", "task": task_id,
                "dor": {"ready": False, "blockers": blockers,
                        "warnings": warnings}}
    # Repeat claimability under the lock so two claimers cannot both win.
    with mutate(ws) as state:
        if state is None:
            return {"error": "no active loop"}
        t = next((x for x in state.get("tasks") or [] if x["id"] == task_id),
                 None)
        if t is None:
            return {"error": f"no task {task_id}"}
        if t.get("status") not in ("pending", "running"):
            return {"error": f"task {task_id} is {t.get('status')} — "
                             "not claimable"}
        tp.activate(agent_ws, contract, snapshot=snapshot)
        t["status"] = "running"
        t["workspace"] = agent_ws
    tp.trace(ws, "loop_claim", task=task_id, agent_workspace=agent_ws,
             dor_ready=dor_ready)
    return {"claimed": task_id, "workspace": agent_ws,
            "contract": {"scope": contract["coding"]["scope_paths"],
                         "tests": contract["coding"]["dod"]["test_command"]},
            "dor": {"ready": dor_ready, "blockers": blockers,
                    "warnings": warnings}}


# --------------------------------------------------------------- next / gate

def next_action(ws: str, rid: str | None = None) -> dict:
    """Advance to the current step's work: activate its contract and return
    what the driver should run. Human steps pause without activating."""
    state = load(ws)
    if state is None:
        return {"error": "no active loop — run `tp.py loop init` first"}
    # v2.3.0 wiring: attach a requirement BEFORE the design DoR evaluates —
    # the sanctioned mid-loop exit for a loop started without --req. The
    # validator (design_contract.design_attach_requirement) enforces the same
    # completeness the DoR demands; failure blocks, success persists.
    if rid:
        attach_errors: list = []
        with mutate(ws) as st:
            if st is None:
                return {"error": "no active loop — run `tp.py loop init` "
                                 "first"}
            attach_errors = _dc.design_attach_requirement(ws, st, rid)
        if attach_errors:
            return {"error": "requirement attach failed",
                    "blockers": attach_errors}
        state = load(ws)
    step = state["step"]

    if step == "retro":
        return {
            "step": "retro", "paused": False, "action": "loop_retro",
            "runtime_evals": runtime_eval.guidance("retro"),
            "instruction": (
                "Run `tp loop retro` once. It seals the run's learning, "
                "refreshes the dependency graph, and moves the loop to done; "
                "no human approval is required."),
            "status": status(ws),
        }

    if step in HUMAN_STEPS:
        awaiting = {
            "design_approval": "human: review design/design.md and the "
                               "Design Contract, then `loop approve`",
            "plan_approval": ("human: review the consolidated requirement, "
                              "conditional design, plan, scope, validation, "
                              "recovery, and delivery packet, then `loop approve`"
                              if _consolidated_enabled() else
                              "human: review plan/plan.md, then `loop approve`"),
            "selection": "human: A/B gate — compare the variants (rendered "
                         "side by side, criteria + lenses + spend), then "
                         "`loop select <variant|task-id|hybrid>`",
            "signoff": "human: EM sign-off, then `loop approve`",
            "escalated": "human: `loop resolve retry|skip|abort` "
                         "(fix cycles exhausted)",
            "done": "loop complete",
            "failed": "loop aborted",
        }[step]
        out = {"step": step, "paused": True, "awaiting": awaiting,
               "status": status(ws)}
        if step == "selection":
            out["variants"] = [
                {"id": t["id"], "variant": t.get("variant"),
                 "status": t.get("status"), "scope": t.get("scope"),
                 "worktree": runtime_storage.task_worktree_reference(ws, t["id"])}
                for t in (state.get("tasks") or []) if t.get("variant")]
            out["instruction"] = (
                "Present BOTH variants for the human's pick: re-run each "
                "variant's tests (trust but verify), render both UIs side "
                "by side — live screenshots over mocks — with the criteria "
                "scoreboard, lens findings, and per-variant resource spend. "
                "Then WAIT; `loop select` only on their explicit choice.")
        if step == "signoff":
            # Run the MECHANICAL Definition-of-Done here so the human signs off
            # seeing both the EM's read-out AND the scope-diff/lint verdict.
            out["dod"] = _signoff_dod(ws, state)
            # v2.3.0 wiring: accepted design drift and hand-declared edge
            # realizations are VISIBLE at sign-off, not dead-on-pass.
            findings, _errs = _read_json(
                runtime_storage.review_public_path(ws, "findings.json"))
            notices = _dc.design_review_notices(
                (findings or {}).get("meta") or {})
            if notices:
                out["notices"] = notices
        if step == "design_approval":
            design_errors = _design_dod_errors(ws, state)
            out["dod"] = {"passed": not design_errors,
                          "errors": design_errors,
                          "fingerprint": _design_evidence_fingerprint(ws)}
            # v2.3.0 wiring: self-attested lens evidence is surfaced AT the
            # human gate instead of being silently accepted.
            notices = _dc.design_approval_notices(ws)
            if notices:
                out["notices"] = notices
        out["runtime_evals"] = runtime_eval.guidance(step)
        return out

    # Parallel mode: EXECUTE is a wave (dispatch handled by `wave`/`claim`);
    # once workers report built, evaluate them one by one (read-only).
    # v2.3.0: the built→evaluate flip is a read-modify-write of the SHARED
    # loop.json while wave workers gate concurrently — apply it under
    # mutate() to a FRESH read (the same lost-update class H2 closed in
    # gate()), so a worker's just-gated status is never clobbered by saving
    # this function's earlier unlocked snapshot.
    if step == "execute" and state.get("parallel"):
        moved = False
        with mutate(ws) as fresh:
            if fresh is None:
                return {"error": "no active loop — run `tp.py loop init` "
                                 "first"}
            if fresh.get("step") != "execute" or not fresh.get("parallel"):
                moved = True                # advanced under us — re-dispatch
            else:
                built = [i for i, t in enumerate(fresh.get("tasks") or [])
                         if t.get("status") == "built"]
                if built:
                    fresh["current_task"] = built[0]
                    fresh["step"] = "evaluate"
            state = fresh
        if moved:
            return next_action(ws)
        step = state["step"]
        if step == "execute":
            return wave(ws)

    # Defence in depth: a per-task step must have a current task. If the loop
    # ever reaches execute/fix/evaluate with none (e.g. a plan that produced
    # no tasks), return a structured error instead of crashing in
    # _step_contract on task["id"].
    if step in ("execute", "fix", "evaluate") and _current_task(state) is None:
        return {"error": f"loop step '{step}' has no current task — the plan "
                         f"produced no tasks, so the loop should not be here. "
                         f"Re-run the plan step (`loop gate fail`, then "
                         f"re-plan) or start over with `loop init`.",
                "step": step, "status": status(ws)}

    # Per-task steps run in the task's own workspace when one was claimed.
    act_ws = ws
    if step in ("evaluate", "fix") and state.get("parallel"):
        tws = (_current_task(state) or {}).get("workspace")
        act_ws = tws if tws and os.path.isdir(tws) else ws

    if step == "design" and not state.get("design_approved"):
        # H3 (v2.2.1): until the design is human-approved, the graph
        # baseline follows the CURRENT scan — capturing once from a stale
        # graph and then blocking on "rescan" left the stored fingerprint
        # permanently mismatched (the engine's own remedy deadlocked the
        # step). A pre-approval rescan re-baselines, with a trace.
        current_fp = (depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
        if state.get("design_graph_fingerprint") != current_fp:
            # v2.3.0: persist the rebaseline under the state lock on a fresh
            # read — a bare save() here could clobber a concurrent update.
            with mutate(ws) as fresh:
                if fresh is not None and fresh.get("step") == "design" \
                        and fresh.get("design_graph_fingerprint") != current_fp:
                    if fresh.get("design_graph_fingerprint"):
                        tp.trace(
                            ws, "design_rebaseline",
                            old=(fresh["design_graph_fingerprint"] or "")[:12],
                            new=(current_fp or "")[:12])
                    fresh["design_graph_fingerprint"] = current_fp
                if fresh is not None:
                    state = fresh

    capability_vars = {
        "TASKPLANE_MODEL_SELECTION", "TASKPLANE_EFFORT_SELECTION",
        "TASKPLANE_SUPPORTED_MODEL_ALIASES",
        "TASKPLANE_SUPPORTED_EFFORT_VALUES",
        "TASKPLANE_NATIVE_STRUCTURED_OUTPUT",
    }
    capability_snapshot = (
        host_capabilities.dispatch_snapshot_from_environment(
            ws, host=tp.host(), environment=os.environ)
        if step == "evaluate" or any(name in os.environ
                                     for name in capability_vars) else None)

    contract = _step_contract(step, state, act_ws)
    evaluator_contract = None
    if step == "evaluate":
        evaluator_contract = evaluation_output.create_evaluator_contract(
            workspace=act_ws, task=str(_current_task(state)["id"]),
            slot=tp.task_slot(), capability_snapshot=capability_snapshot)
        contract = dict(contract)
        contract["output_contract"] = evaluator_contract
    contract = _bind_worker_submission(
        act_ws, state, step, contract, _current_task(state))
    snapshot = tp.git_head(act_ws)
    # Governance starts before readiness is evaluated.  A failing DoR must
    # still leave the attempted step inside its exact contract; recording
    # readiness first made the audit say work was judged before it was
    # governed and let host actions race the enforcement boundary.
    tp.activate(act_ws, contract, snapshot=snapshot)
    dor_ready, blockers, warnings = tp.dor_check(
        contract, act_ws, snapshot)
    if step == "design":
        design_dor = _design_dor(ws, state)
        blockers.extend(design_dor["blockers"])
        warnings.extend(design_dor["warnings"])
        dor_ready = not blockers
    tp.trace(ws, "loop_step", step=step, role=STEP_ROLE[step],
             task=(_current_task(state) or {}).get("id"),
             dor_ready=dor_ready, dor_blockers=blockers,
             dor_warnings=warnings)
    if not dor_ready:
        return {"error": "Definition of Ready failed — resolve blockers "
                         "before this step can start",
                "step": step, "role": STEP_ROLE[step],
                "dor": {"ready": False, "blockers": blockers,
                        "warnings": warnings},
                "status": status(ws)}

    # The graph is an input to evaluation, not a cache refreshed only after
    # review.  Serial work and the final merged-tree review can safely refresh
    # the shared graph here. Parallel task worktrees are deliberately deferred
    # until their branches merge; publishing one worker's partial graph as the
    # project graph would hide its siblings.
    if step == "em":
        # Make the final graph describe the merged, as-built system BEFORE
        # the engineering reviewer receives it.  Doing this at the EM gate
        # would invalidate the review's graph fingerprint at sign-off.
        try:
            _true_up_graph(ws, state)
        except Exception as exc:
            if state.get("graph_governance"):
                return {"error": f"graph true-up failed before {step}: {exc}",
                        "step": step, "status": status(ws)}
            tp.trace(ws, "graph_refresh_failed", step=step, error=str(exc))
    elif step == "evaluate" and not state.get("parallel"):
        try:
            depgraph.scan(ws)
        except Exception as exc:
            if state.get("graph_governance"):
                return {"error": f"graph refresh failed before {step}: {exc}",
                        "step": step, "status": status(ws)}
            tp.trace(ws, "graph_refresh_failed", step=step, error=str(exc))
    # Inject the handful of prior decisions relevant to this step's work, so
    # the role starts with context instead of re-deriving it (token savings).
    task = _current_task(state)
    # The worker's tree — where a claimed task's change lands. NOT act_ws:
    # that is parallel-gated, so a serial loop would name the PROJECT tree.
    wtree = (task or {}).get("workspace") or ""
    wtree = wtree if os.path.isdir(wtree) else ws
    query_files = (task or {}).get("scope") or []
    query_tags = ([task["id"]] if task else []) + [state["goal"][:24]]
    recalled = kb.retrieve(ws, files=query_files, tags=query_tags, limit=5)
    if recalled:
        tp.trace(ws, "kb_recall", step=step,
                 decisions=[d["id"] for d in recalled])

    # Lens wiring. EXECUTE/FIX: PRIME — the same lenses that will review the
    # change are named before it's built. EVALUATE/EM: ROUTE on the real diff
    # since plan approval, so review effort lands exactly where change did.
    # Normal Evaluate and final EM consume the same selective mapper. The
    # explicit human/calibration `tp lens route --all` surface remains, but
    # delivery never substitutes full-catalog fan-out for uncertainty.
    routing, breadth = None, "routed"
    if step in ("pm", "plan"):
        # Advisory tier: C-level lenses run at STRATEGY level, always-on at
        # the pm/plan steps — never on code.
        routing = lens_router.route([], artifact_type="strategy", catalog=None)
    elif step == "design":
        # The design lens is mandatory at this phase, independent of diff
        # routing. Keep a fallback brief so an in-place minor update remains
        # resumable while the catalog file itself is being upgraded.
        design_req = reqs.get_requirement(ws, state.get("requirement_id"))
        design_scope = (design_req or {}).get("context_files") or []
        design_files = list(design_scope) + \
            lens_router.workspace_language_markers(ws, design_scope)
        routed = lens_router.route(
            design_files,
            task_type="solution-design", only=["solution-design"])
        routing = routed if routed.get("lenses") else {"lenses": [{
            "id": "solution-design", "name": "Solution design",
            "mode": "inline", "tier": "deep",
            "reasons": ["mandatory Design Contract lens"], "checks": [],
            "looks_for": "approach coherence, dependency boundaries, "
                         "trade-offs, failure modes, and verifiable delivery"
        }]}
    elif step in ("execute", "fix"):
        routing = lens_router.prime_scope((task or {}).get("scope"),
                                          task_type=(task or {}).get("type"),
                                          workspace=wtree)
    elif step in ("evaluate", "em"):
        # Deferred until graph quality and complete impact exist below.
        # Mapping before that evidence is the ordering defect R-0005 closes.
        routing = None
    if routing:
        # RECORDING ONLY. Reading the breadth back off the lens LIST cannot
        # work — see lens.LENS_BREADTH_EVENT. `breadth` is the ask, verbatim.
        tp.trace(ws, "lens_route", step=step, requested_breadth=breadth,
                 engine_ran="signals" in (routing.get("context") or {}),
                 lenses=[[x["id"], x["mode"]] for x in routing["lenses"]])

    def heads():                    # lazy: only an emitting branch pays
        return {"head": tp.git_head(ws if step == "em" else wtree),
                "scanned_head": (depgraph.load(ws).get("meta")
                                 or {}).get("scanned_head")}
    # Blast radius from the persistent dependency graph — the reviewer sees
    # what the change can break WITHOUT re-deriving dependencies (no tokens).
    imp = None
    if step in ("evaluate", "em"):
        diff_ws = wtree if step == "evaluate" else ws
        changed = [f for f in _diff_files(
            diff_ws, state.get("baseline") or "HEAD")
            if not f.startswith(lens_router.LOOP_OWNED)]
        if changed or step == "em":
            review_policy = (_aggregate_impact_policy(state.get("tasks") or [])
                             if step == "em" else
                             depgraph.impact_policy(task or {}))
            imp = depgraph.impact(ws, changed, policy=review_policy)
            # Product side of the blast radius: which OTHER requirements'
            # surface this diff touches (their criteria may need re-checking)
            # and which requirements depend on the affected ones.
            prod = depgraph.product_impact(ws, changed)
            own = (task or {}).get("req") or state.get("requirement_id")
            own = depgraph.req_node(own) if own else None
            imp["affected_requirements"] = [
                r for r in prod["affected_requirements"] if r != own]
            imp["dependent_requirements"] = prod["dependent_requirements"]
            nudges = _edge_nudges(diff_ws, changed,
                                  state.get("baseline") or "HEAD")
            if nudges:
                imp["edge_suggestions"] = nudges
            tp.trace(ws, "graph_impact", step=step,
                     touched=imp["touched"],
                     impacted=imp["total_impacted"],
                     affected_reqs=imp["affected_requirements"], **heads())
    elif step in ("execute", "fix") and task:
        # v2.0.0: the BUILDER sees the blast radius BEFORE changing code
        # (previously only the judges at evaluate/em did) - side effects
        # get prevented, not just detected a loop-step later.
        scope = task.get("scope") or []
        if scope and depgraph.load(ws)["modules"]:
            mods = depgraph.scope_modules(ws, scope)
            if mods:
                imp = depgraph.impact(
                    ws, mods, policy=depgraph.impact_policy(task))
                if not imp["touched"]:
                    imp = None
                else:
                    tp.trace(ws, "graph_impact", step=step,
                             touched=imp["touched"],
                             impacted=imp["total_impacted"], **heads())
    elif step == "design":
        design_req = reqs.get_requirement(ws, state.get("requirement_id"))
        design_scope = (design_req or {}).get("context_files") or []
        design_modules = depgraph.scope_modules(ws, design_scope)
        if design_modules and depgraph.load(ws).get("modules"):
            design_policy = {"local_depth": 3,
                             "boundary_mode": "contract-only",
                             "contract_depth": 1, "requirement_depth": 1}
            imp = depgraph.impact(ws, design_modules, policy=design_policy)
            tp.trace(ws, "graph_impact", step=step,
                     touched=imp["touched"],
                     impacted=imp["total_impacted"], **heads())

    # Audit cadence (v3 Phase 1): the em brief advertises audit mode — due
    # every Nth completed review (default 5) or on a release flag — and,
    # when due, carries the recorded routing decision so the gate can check
    # findings against it (router-regression auto-filing).
    audit_info = None
    if step == "em":
        audit_info = _audit_brief(ws, state)
        if audit_info.get("due"):
            tp.trace(ws, "audit_due", reason=audit_info.get("reason"),
                     reviews_completed=audit_info.get("reviews_completed"))

    # Requirement anchoring: this task's R-id (or the loop's) is the spine —
    # its acceptance criteria are the DoD the evaluator holds the work to.
    req_rec = None
    rid = (task or {}).get("req") or state.get("requirement_id")
    if rid:
        req_rec = reqs.get_requirement(ws, rid)

    review_kernel = None
    review_workspace = None
    if step in ("evaluate", "em"):
        diff_ws = wtree if step == "evaluate" and state.get("parallel") else ws
        review_workspace = os.path.realpath(diff_ws)
        base_ref = state.get("baseline") or "HEAD"
        try:
            retry_context = (review_retry.incremental_context(
                ws, diff_ws, task, review_kernel_binding(state, "evaluate", task))
                if step == "evaluate" else None)
            review_kernel, routing = _review_kernel(
                ws, diff_ws, base=base_ref, step=step, task=task,
                graph=depgraph.load(ws), impact=imp or {},
                requirement=req_rec, retry_context=retry_context)
        except Exception as exc:
            review_kernel = {"status": "kernel_unavailable", "slots": [],
                             "reason": f"{exc.__class__.__name__}: {exc}"}
            routing = {"lenses": [], "context": {
                "status": "kernel_unavailable", "breadth": "routed"}}
        binding = {
            "schema": "taskplane.review-kernel-binding/v1",
            "run_id": review_kernel.get("run_id"),
            "workspace": review_workspace,
            "stage": (EVALUATE_ROUTE_STAGE if step == "evaluate" else
                      "review"),
            "status": review_kernel.get("status"),
        }
        with mutate(ws) as fresh:
            if fresh is not None:
                fresh.setdefault("review_kernel_runs", {})[
                    _review_kernel_binding_key(step, task)] = binding
        tp.trace(ws, "lens_route", step=step, requested_breadth="routed",
                 engine_ran="signals" in (routing.get("context") or {}),
                 lenses=[[x["id"], x["mode"]]
                         for x in routing.get("lenses") or []],
                 kernel_status=review_kernel.get("status"))

    dispatch = tp.dispatch_fields(
        "step", STEP_ROLE[step], (task or {}).get("id") or step,
        tp.step_tier(step, task), capability_snapshot=capability_snapshot,
        enforcement_mode=os.environ.get("TASKPLANE_ENFORCE_DISPATCH"))
    if dispatch.get("dispatch_blocked"):
        tp.trace(ws, "dispatch_route_resolved", step=step,
                 task=(task or {}).get("id"),
                 resolution="blocked",
                 reason=dispatch["dispatch_route"].get("reason"))
        return {"error": "strict host dispatch route cannot be honored — "
                         + dispatch["dispatch_route"].get("reason", ""),
                "step": step, **dispatch}
    model_tier, model = dispatch["model_tier"], dispatch["model"]
    reasoning_effort, task_name = (dispatch["reasoning_effort"],
                                   dispatch["task_name"])
    tp.trace(ws, "model_tier", step=step,
             task=(task or {}).get("id"), tier=model_tier, model=model,
             reasoning_effort=reasoning_effort)
    tp.record_expected_dispatch(ws, "step", STEP_ROLE[step], model_tier,
                                model, ref=(task or {}).get("id") or step,
                                task_name=task_name,
                                reasoning_effort=reasoning_effort,
                                role_marker_value=dispatch["role_marker"],
                                dispatch_route=dispatch.get("dispatch_route"))
    if dispatch.get("dispatch_route"):
        tp.trace(ws, "dispatch_route_resolved", step=step,
                 task=(task or {}).get("id"),
                 resolution=dispatch["dispatch_route"].get("resolution"),
                 capability_source=dispatch["dispatch_route"].get(
                     "capability_source"),
                 exact_route_verified=False)
    model_note = None
    if model is None and (model_tier or "standard") != "standard":
        model_note = (f"tier '{model_tier}' resolves to inherit on this "
                      f"host — the planned routing has no effect; set "
                      f"TASKPLANE_MODEL_{str(model_tier).upper()} to "
                      "activate it")
    return {**dispatch,
        **({"model_note": model_note} if model_note else {}),
        **({
            "output_contract": evaluator_contract,
            "output_schema": evaluator_contract["output_schema"],
            "resume_identity": evaluation_output.resume_identity(
                evaluator_contract),
            "max_attempts": evaluator_contract["max_attempts"],
        } if evaluator_contract else {}),
        "step": step,
        "codex_dispatch": ("Use Codex's native subagent task orchestration with "
                           "this exact task_name, role instructions, standalone "
                           "role_marker, model when non-null, and "
                           "reasoning_effort."),
        # cross-host artifact: '/'-shaped out, host-shaped in state
        "task": tp.posix_workspace(task),
        "contract": {"read_only": bool(contract.get("read_only")),
                     "scope": contract["coding"]["scope_paths"],
                     "write_allow": contract.get("write_allow"),
                     "tests": contract["coding"]["dod"]["test_command"]},
        "dor": {"ready": dor_ready, "blockers": blockers,
                "warnings": warnings},
        "knowledge": {"decisions": recalled,
                      # R-0002: accepted decisions whose modules overlap this
                      # task's scope are ALWAYS in force — injected
                      # unconditionally, not relevance-ranked.
                      "governing_decisions": kb.governing(
                          ws, contract["coding"]["scope_paths"]),
                      # R-0004: the as-built inventory — ALWAYS in the brief
                      # when filled, so design work is judged as a delta
                      # against what exists, never in a vacuum.
                      "current_state": kb.current_state(ws),
                      "context": kb.render_context(recalled)},
        "lenses": routing["lenses"] if routing else None,
        "language_references": ((routing.get("context") or {}).get(
            "language_references") if routing else None),
        "review_kernel": review_kernel,
        "runtime_evals": runtime_eval.guidance(step),
        "audit": audit_info,
        "impact": imp and {**imp, "context": depgraph.render_context(imp)},
        "design": _design_context(ws, state),
        "design_graph": ({
            "baseline_fingerprint": state.get("design_graph_fingerprint"),
            "summary": depgraph.summary(ws),
            "policy": depgraph.impact_policy({}),
            "rule": "propose modules and edges in design/contract.json; "
                    "do not mutate the as-built graph during Design"
        } if step == "design" else None),
        "requirement": req_rec and {
            "id": req_rec["id"], "title": req_rec["title"],
            "acceptance": req_rec["acceptance"],
            "open_questions": req_rec["open_questions"],
            # Structured requirement data is authoritative.  The rendered
            # context includes relations such as
            # ``changes:contract:pricing.checkout.total`` for humans; a
            # planner copying that entire display string as a contract id
            # produces an invalid ``changes:contract:...`` plan boundary.
            # Give both hosts the exact id and relation separately so the
            # plan can copy ``contract:...`` without rediscovery or retry.
            "contracts": [
                {"relation": row.get("relation"), "id": row.get("id")}
                for row in (req_rec.get("contracts") or [])
                if isinstance(row, dict) and row.get("id")
            ],
            "depends": list(req_rec.get("depends_on") or []),
            "context_files": list(req_rec.get("context_files") or []),
            "context": reqs.render_context([req_rec])},
        "instruction": _instruction(step, state, act_ws),
    }


guide = runtime_eval.guide_loop


def _instruction(step: str, state: dict, ws: str | None = None) -> str:
    t = _current_task(state)
    evaluator_result, review_root = runtime_storage.instruction_artifact_paths(ws)
    return {
        "pm": "Run tp-product: author specs/spec.md, then call `req new` "
              "exactly once with complete functional, acceptance, "
              "context-file, contract, and NFR fields. Code-bearing scope "
              "must include exact `security` and `architecture` NFR ids. Do "
              "not call status, context, graph, graph impact, req score, req "
              "list/help, loop submit, new, or clear: the PM gate "
              "mechanically scores DoR and links context files to the "
              "planned graph. Return the R-id to the orchestrator.",
        "design": "Run tp-designer (read-only toward product code): inspect "
                  "the requirement, current state, decisions, and baseline "
                  "graph; author design/design.md and design/contract.json "
                  "using schema taskplane.design/v1. Compare alternatives, "
                  "select the HOW, define modules/contracts plus graph DoR/DoD "
                  "and depth policy, map acceptance, handle risks/rollout, "
                  "apply solution-design, and create a visualization only "
                  "when it materially clarifies the choice. Never mutate the "
                  "as-built graph. Return to the orchestrator; it validates "
                  "with `loop gate pass` and then pauses for human approval.",
        "plan": "Run the tp-planner role: derive impact once with `tp graph "
                "impact --files \"comma,separated,paths\" --json`; write "
                "plan/tasks.json (machine) "
                "and plan/plan.md (human) — tasks with scope, tests (ONE "
                "command string, never a list), "
                "criteria, dependencies, contracts, design_edges, and impact "
                "policy. Copy only `requirement.contracts[].id` into task "
                "contracts; the relation is metadata, not part of the id. When "
                "`design.approved` is true, cover its modules, edges, contracts, "
                "depth policy, and acceptance mapping without drift. Return "
                "to the orchestrator; it validates with `loop gate pass`.",
        "execute": f"Run the tp-executor on task {t and t['id']}: build "
                   "under this contract (TDD), honoring the PRIMED lenses "
                   "(see `lenses`) and the requirement's acceptance criteria "
                   "(see `requirement`) plus the approved Design Contract "
                   "when `design.approved` is true. Then `loop submit pass` (or `fail` "
                   "if you couldn't build it); only the orchestrator calls "
                   "`loop gate`.",
        "evaluate": f"Run the tp-evaluator (read-only) on task "
                    f"{t and t['id']}: START with `tp loop evidence --write` — "
                    "one call returns the suite result, the diff, and the exact "
                    "criteria, routed-lens and graph obligations this gate "
                    "demands, judgment slots empty; do NOT rebuild those by "
                    "hand. Then do what the engine cannot: prove each criterion "
                    "against real behavior, apply each ROUTED lens (prompt at "
                    "lenses/<id>.md) — inline ones yourself, one governed "
                    "read-only subagent per subagent-mode lens — and disposition "
                    "graph impact + affected requirements; reject stale Design "
                    f"evidence. Fill the empty slots in {evaluator_result} "
                    "(submitted unchanged, it is refused). Then `loop submit "
                    "pass|fail`; if one bounded model/host attempt is unavailable "
                    "but bound tests are green and no product/lens defect exists, "
                    "record structured evaluation unavailability and `loop submit "
                    "unavailable` instead — it warns without opening FIX. Only "
                    "the orchestrator calls `loop gate`.",
        "fix": f"Run the tp-fixer on task {t and t['id']}: repair the "
               "listed failures + add a regression test. Then `loop submit "
               "pass`; only the orchestrator calls `loop gate`.",
        "em": "Run tp-engineering (read-only): `lenses` is the one complete "
              "26-lens routing decision. Run exact tier=deep slots and at "
              "most one bounded tier=light sweep; tier=n/a is evidence, "
              "never a dispatch. Do not re-derive diff or impact per lens. "
              "Synthesize all verdicts + requirement-vs-implementation into "
              f"{os.path.join(review_root, 'report.md')} AND "
              f"{os.path.join(review_root, 'findings.json')} (including "
              "complete meta.lens_coverage, meta.impact, meta.design "
              "conformance when an approved design exists, tests, and gate "
              "verdict), record the verdict to the knowledge "
              "base, then `loop submit pass`. The orchestrator validates "
              "with `loop gate pass` before presenting human sign-off.",
    }[step]


# Design Contract validation lives in design_contract.py (v2.2.1) — thin
# delegates keep loop's internal API stable for callers and tests.
import design_contract as _dc

_read_json = _dc.read_json
DESIGN_SCHEMA = _dc.DESIGN_SCHEMA
DESIGN_CONTRACT = _dc.DESIGN_CONTRACT
DESIGN_NARRATIVE = _dc.DESIGN_NARRATIVE
_design_path = _dc.design_path
_design_contract = _dc.design_contract
_design_safe_rel = _dc.design_safe_rel
_design_evidence_paths = _dc.design_evidence_paths
_design_evidence_fingerprint = _dc.design_evidence_fingerprint
_design_current_errors = _dc.design_current_errors
_design_dor = _dc.design_dor
_design_dod_errors = _dc.design_dod_errors
_design_plan_errors = _dc.design_plan_errors
_design_review_errors = _dc.design_review_errors


def _design_context(ws: str, state: dict) -> dict | None:
    if not state.get("design_required"):
        return None
    contract, errors = _design_contract(ws)
    approved = bool(state.get("design_fingerprint"))
    stale = _design_current_errors(ws, state) if approved else []
    if stale:
        # M8 (v2.2.1): an approved design whose artifacts changed after
        # approval is NOT served as approved — the same staleness the
        # gates enforce is reported in every brief that carries it.
        approved = False
        errors = list(errors or []) + stale
    return {"approved": approved,
            "stale": bool(stale) or None,
            "fingerprint": state.get("design_fingerprint"),
            "contract": contract, "errors": errors}


def _criteria_for(ws: str, state: dict, task: dict) -> list:
    criteria = list(task.get("criteria") or [])
    rid = task.get("req") or state.get("requirement_id")
    rec = reqs.get_requirement(ws, rid) if rid else None
    if rec and not criteria:
        criteria = list(rec.get("acceptance") or criteria)
    criteria = [str(c).strip() for c in criteria if str(c).strip()]
    if not criteria and str(task.get("tests") or "").strip():
        criteria = [f"test command passes: {task['tests']}"]
    return criteria


def _aggregate_impact_policy(tasks) -> dict:
    return depgraph.aggregate_impact_policy(tasks)


def _plan_dor_errors(ws: str, state: dict, apply: bool = False) -> list:
    """Definition of Ready for implementation, derived from the plan.

    M3 (v2.2.1): a Ready CHECK must not mutate. With apply=False
    (default) this is pure — it inspects and reports. Only the plan
    GATE passes apply=True, which merges requirement contracts into
    tasks, records requirement/contract edges, resolves each task's
    impact policy, and stores the graph DoR verdict on the state."""
    errors = []
    for task in state.get("tasks") or []:
        prefix = f"task {task.get('id', '?')}: "
        if not task.get("scope"):
            errors.append(prefix + "scope is missing")
        errors.extend(prefix + problem for problem in
                      tp.plan_test_command_errors(task.get("tests")))
        if not _criteria_for(ws, state, task):
            errors.append(prefix + "acceptance criteria are missing")
        rid = task.get("req") or state.get("requirement_id")
        rec = reqs.get_requirement(ws, rid) if rid else None
        if rec:
            # Requirements own stable product/contract dependencies; the plan
            # may add contracts but cannot silently erase the requirement's
            # boundaries with an empty or narrower task-level list.
            merged_contracts, seen_contracts = [], set()
            for contract in list(rec.get("contracts") or []) + \
                    list(task.get("contracts") or []):
                cids = depgraph.contract_ids([contract])
                cid = cids[0] if cids else ""
                if cid and cid not in seen_contracts:
                    merged_contracts.append(contract)
                    seen_contracts.add(cid)
            if apply:
                task["contracts"] = merged_contracts
            for dep in rec.get("depends_on") or []:
                if reqs.get_requirement(ws, dep) is None:
                    errors.append(prefix + f"requirement dependency {dep} "
                                  "does not exist")
                elif apply:
                    # Requirements are the source of truth. Reconcile their
                    # product edges before graph Ready instead of depending on
                    # a particular CLI path having populated the derived map.
                    depgraph.link_requirement_dep(ws, rid, dep)
            if apply:
                for contract in rec.get("contracts") or []:
                    cids = depgraph.contract_ids([contract])
                    relation = (contract.get("relation", "changes")
                                if isinstance(contract, dict) else "changes")
                    if cids:
                        depgraph.record_edge(
                            ws, depgraph.req_node(rid), cids[0],
                            kind=relation, confidence="high")
        if apply:
            task["impact_policy"] = depgraph.impact_policy(task)
        if rid and task.get("high_cost"):
            if rec is None:
                errors.append(prefix + f"requirement {rid} does not exist")
            elif rec.get("open_questions"):
                errors.append(prefix + "requirement has unresolved questions: "
                              + "; ".join(rec["open_questions"]))
    graph_dor = depgraph.readiness(ws, state.get("tasks") or [])
    if apply:
        state["graph_dor"] = graph_dor
    errors.extend("graph DoR: " + e for e in graph_dor.get("errors") or [])
    errors.extend(tp.requirement_coverage_errors(state.get("tasks") or [],
        lambda rid: reqs.get_requirement(ws, rid), state.get("requirement_id")))
    errors.extend("design DoR: " + e for e in _design_plan_errors(ws, state))
    return errors


def _task_graph_dod(ws: str, state: dict, task: dict) -> dict:
    """As-built dependency proof for one task.

    Parallel worktrees cannot replace the shared graph before merge, so their
    final graph proof is explicitly deferred to the merged EM review.
    """
    if state.get("parallel"):
        return {"passed": True, "deferred_to_post_merge": True,
                "errors": [], "impact": {}}
    baseline = state.get("baseline") or tp.snapshot_ref(ws)
    changed = [f for f in _diff_files(ws, baseline or "HEAD")
               if not f.startswith(lens_router.LOOP_OWNED)]
    stems = [g.split("*", 1)[0] for g in (task.get("scope") or [])]
    mine = [f for f in changed
            if not stems or any(f.startswith(s) for s in stems if s)]
    planned = ((task.get("blast") or {}).get("modules")
               or depgraph.scope_modules(ws, task.get("scope") or []))
    return depgraph.completion(
        ws, mine, planned_modules=planned,
        policy=task.get("impact_policy") or depgraph.impact_policy(task))


def _task_dod_errors(ws: str, state: dict, task: dict,
                     snapshot: str | None) -> list:
    contract = tp.build_contract(
        f"EXECUTE: {task['id']}", scope=task.get("scope"),
        test_command=task.get("tests"), plan_minted=True, regression_gate=True)
    # Scope regression evidence to this task; loop-owned artifacts self-gate.
    regression_files = [f for f in (tp.changed_files(ws, snapshot) if snapshot else [])
                        if tp.match_any(f, task.get("scope") or [])]
    suite_evidence = {}
    errors = (_design_current_errors(ws, state) + tp.dod_check(
        contract, ws, snapshot, ignore_prefixes=lens_router.LOOP_OWNED,
        regression_files=regression_files,
        suite_evidence=suite_evidence))
    # Preserve the long-standing four-argument patch seam used by race and
    # failure-injection tests. This is transient validation output: gate()
    # copies it into the fresh locked state only after all checks pass.
    if suite_evidence:
        state.setdefault("_validated_suite_evidence", {})[task["id"]] = \
            suite_evidence
    return errors


def _acceptance_evidence_errors(ws: str, state: dict, task: dict,
                                verdict: dict) -> list:
    """Static DoD evidence check, safe to compose with runtime guidance."""
    errors = []
    expected_criteria = _criteria_for(ws, state, task)
    rows = verdict.get("criteria") or []
    if not isinstance(rows, list):
        errors.append("evaluation criteria must be a list")
        rows = []
    by_criterion = {str(r.get("criterion", "")).strip(): r
                    for r in rows if isinstance(r, dict)}
    for criterion in expected_criteria:
        row = by_criterion.get(criterion)
        if not row:
            errors.append(f"acceptance criterion has no evidence: {criterion}")
        elif row.get("status") != "met" or not str(
                row.get("evidence") or "").strip():
            errors.append(
                f"acceptance criterion is not proven met: {criterion}")
    return errors


def _evaluation_errors(ws: str, state: dict, task: dict) -> list:
    """Validate evaluator evidence instead of trusting `gate pass`."""
    path = runtime_storage.evaluation_path(ws)
    verdict, errors = _read_json(path)
    if errors:
        return errors
    errors.extend(_design_current_errors(ws, state))
    import review as _review
    binding = review_kernel_binding(state, "evaluate", task)
    kernel_ws = str((binding or {}).get("workspace") or ws)
    kernel = None
    if binding:
        try:
            kernel = _review._load_state(kernel_ws, binding["run_id"])
        except Exception:
            kernel = None
    if not kernel or kernel.get("stage") != EVALUATE_ROUTE_STAGE:
        # Compatibility for callers that historically submitted and gated an
        # evaluator directly without first asking for `loop next`.  This is
        # still a single mapping: materialize the normal brief once, then the
        # validator below consumes only its persisted decision.
        try:
            action = getattr(next_action, "__wrapped__", next_action)(ws)
            if not action.get("error"):
                state = load(ws) or state
                binding = review_kernel_binding(state, "evaluate", task)
                kernel_ws = str((binding or {}).get("workspace") or ws)
                if binding:
                    kernel = _review._load_state(
                        kernel_ws, binding["run_id"])
        except Exception:
            kernel = None
    if kernel and kernel.get("status") == "ready" and \
            kernel.get("stage") == EVALUATE_ROUTE_STAGE:
        try:
            _review.collect_review(
                kernel_ws, publish=False, run_id=kernel.get("run_id"))
            kernel = _review._load_state(
                kernel_ws, kernel.get("run_id"))
        except Exception as exc:
            errors.append("evaluation leased slot collection failed: "
                          f"{exc.__class__.__name__}: {exc}")
    if not kernel or kernel.get("status") != "complete" or \
            kernel.get("stage") != EVALUATE_ROUTE_STAGE:
        errors.append("evaluation selective review kernel is missing or incomplete")
    if verdict.get("task") != task.get("id"):
        errors.append("evaluation evidence is for task "
                      f"{verdict.get('task')!r}, expected {task.get('id')!r}")
    if verdict.get("verdict") != "pass":
        errors.append("evaluation verdict is not pass")

    errors.extend(_acceptance_evidence_errors(ws, state, task, verdict))

    # Derive the expected lens set with the SAME stage the evaluate brief
    # routed with (EVALUATE_ROUTE_STAGE — single-sourced, R-0006 row 1), so
    # expectation matches dispatch. Route v2 returns EVERY catalog lens for
    # coverage honesty; only the ROUTED ones (deep + light, mode != "none")
    # owe the evaluator a verdict row — n/a lenses carry their negative
    # evidence in the routing itself. On the legacy path no entry has mode
    # "none", so the filter is a no-op there.
    # Consume the one decision created after graph quality; never map again
    # inside the gate on potentially different inputs.
    routing = ((kernel or {}).get("routing") or {"lenses": []})
    expected_lenses = {entry["id"] for entry in routing.get("lenses") or []
                       if entry.get("mode") != "none"}
    canonical_rows = {str(row.get("lens") or ""): row for row in
                      ((kernel or {}).get("lens_results") or [])
                      if isinstance(row, dict)}
    canonical_blocking = _review.blocking_findings_by_lens(
        ((kernel or {}).get("revision") or {}).get("findings") or [])
    for lens_id, count in sorted(canonical_blocking.items()):
        errors.append(
            f"canonical blocking finding prevents Evaluate pass: {lens_id} "
            f"({count})")
    if set(canonical_rows) != expected_lenses:
        missing_slots = sorted(expected_lenses - set(canonical_rows))
        unexpected_slots = sorted(set(canonical_rows) - expected_lenses)
        if missing_slots:
            errors.append("leased slot results omit routed lenses: "
                          + ", ".join(missing_slots))
        if unexpected_slots:
            errors.append("leased slot results contain unexpected lenses: "
                          + ", ".join(unexpected_slots))
    raw_lenses = verdict.get("lenses") or []
    if not isinstance(raw_lenses, list):
        errors.append("evaluation lenses must be a list")
        raw_lenses = []
    lens_rows = {str(r.get("lens", "")): r for r in raw_lenses
                 if isinstance(r, dict)}
    for lens_id in sorted(expected_lenses):
        row = lens_rows.get(lens_id)
        if not row:
            errors.append(f"routed lens has no verdict: {lens_id}")
        else:
            canonical = canonical_rows.get(lens_id)
            if canonical is None:
                errors.append(f"routed lens lacks a leased slot result: {lens_id}")
                continue
            try:
                blocker_count = int(row.get("blockers") or 0)
            except (TypeError, ValueError):
                blocker_count = 1
            if row.get("verdict") != "pass" or blocker_count > 0:
                errors.append(f"routed lens did not pass cleanly: {lens_id}")
            if (row.get("verdict"), blocker_count) != \
                    (canonical.get("verdict"), canonical.get("blockers")):
                errors.append(
                    f"routed lens verdict contradicts leased result: {lens_id}")
    if verdict.get("failures"):
        errors.append("evaluation contains unresolved failures")
    if state.get("graph_governance"):
        graph_dod = _task_graph_dod(ws, state, task)
        errors.extend("graph DoD: " + e for e in graph_dod.get("errors") or [])
        if not graph_dod.get("deferred_to_post_merge"):
            impact = graph_dod.get("impact") or {}
            direct = sorted({e.get("module")
                             for e in (impact.get("impacted") or {}).get(1, [])
                             if e.get("module")
                             and not str(e.get("module")).startswith("req:")})
            prod = depgraph.product_impact(ws,
                                           graph_dod.get("realized_modules") or [])
            own = task.get("req") or state.get("requirement_id")
            own = depgraph.req_node(own) if own else None
            affected = sorted(r for r in prod.get("affected_requirements") or []
                              if r != own)
            needs_graph_evidence = bool(
                direct or affected or graph_dod.get("contract_files")
                or impact.get("unknown") or impact.get("truncated"))
            graph_ev = verdict.get("graph") or {}
            if needs_graph_evidence and not isinstance(verdict.get("graph"), dict):
                errors.append("evaluation is missing graph impact evidence")
                graph_ev = {}
            dispositions = {str(x.get("node")): x for x in
                            (graph_ev.get("dispositions") or [])
                            if isinstance(x, dict)}
            allowed = {"tested", "contract-verified", "unaffected",
                       "follow-up", "requires-replan"}
            for node in direct:
                row = dispositions.get(node)
                if (not row or row.get("status") not in allowed
                        or not str(row.get("evidence") or "").strip()):
                    errors.append(f"graph impact has no evidenced disposition: {node}")
                elif row.get("status") == "requires-replan":
                    errors.append(f"graph impact requires replanning: {node}")
            checked = set(graph_ev.get("requirements_checked") or [])
            for rid in affected:
                if rid not in checked:
                    errors.append("affected requirement was not re-checked: " + rid)
            expected_contracts = set()
            for contract_row in task.get("contracts") or []:
                contract_id = (contract_row.get("id")
                               if isinstance(contract_row, dict)
                               else contract_row)
                if str(contract_id or "").strip():
                    expected_contracts.add(str(contract_id))
            checked_contracts = set(graph_ev.get("contracts_checked") or [])
            for contract in sorted(expected_contracts - checked_contracts):
                errors.append("declared contract was not verified: " + contract)
    return errors


def _evaluation_unavailable_errors(ws: str, state: dict,
                                   task: dict) -> tuple[list, dict]:
    """Admit a pure model/host outage without inventing a product defect."""
    path = runtime_storage.evaluation_path(ws)
    verdict, errors = _read_json(path)
    if errors:
        return errors, {}
    try:
        evaluation_output.validate_evaluator_value(verdict)
    except evaluation_output.OutputValidationError as exc:
        errors.append(f"evaluation output is invalid ({exc.code}): {exc}")
        return errors, verdict
    availability = verdict.get("evaluation") or {}
    if availability.get("status") != "unavailable":
        errors.append("evaluation does not declare structured unavailability")
    if availability.get("reason_code") in (None, "", "none"):
        errors.append("evaluation unavailability has no host reason code")
    if verdict.get("task") != task.get("id"):
        errors.append("evaluation evidence is for task "
                      f"{verdict.get('task')!r}, expected {task.get('id')!r}")
    if verdict.get("verdict") != "fail":
        errors.append("unavailable evaluation must retain verdict 'fail'")
    not_met = [row.get("criterion") for row in verdict.get("criteria") or []
               if isinstance(row, dict) and row.get("status") == "not-met"]
    if not_met:
        errors.append("product acceptance is not met: " + ", ".join(
            str(item) for item in not_met))
    blocking_lenses = [row.get("lens") for row in verdict.get("lenses") or []
                       if isinstance(row, dict) and
                       (row.get("verdict") == "fail" or
                        int(row.get("blockers") or 0) > 0)]
    if blocking_lenses:
        errors.append("product/lens failures cannot be classified as host "
                      "unavailability: " + ", ".join(
                          str(item) for item in blocking_lenses))
    if not (verdict.get("failures") or []):
        errors.append("evaluation unavailability has no bounded failure record")
    suite = ((state.get("_suite_evidence") or {}).get(str(task.get("id")))
             or {})
    if task.get("tests") and not (
            suite.get("schema") == "taskplane.suite-evidence/v1" and
            suite.get("returncode") == 0):
        errors.append("evaluation unavailability requires green mechanical "
                      "suite evidence from the execute/fix gate")
    if state.get("_build_failed") or task.get("_build_failed"):
        errors.append("a failed build is a product failure, not evaluation "
                      "unavailability")
    return errors, verdict


# One canonical severity vocabulary (v2.3.0). Producers disagree — the lens
# brief says high|med|low, the lens catalog's verdict schema says
# blocker|major|minor|question|praise, free-form reviews say critical —
# so every CONSUMER normalizes through this map. Enforcement rule: unknown
# or foreign severities map UP to 'high'; a finding a gate cannot classify
# must BLOCK, never pass or render as medium (fail closed).
SEVERITY_CANONICAL = ("high", "med", "low", "info")
_SEVERITY_MAP = {
    "high": "high", "critical": "high", "blocker": "high", "major": "high",
    "sev1": "high", "p0": "high", "p1": "high",
    "med": "med", "medium": "med", "moderate": "med",
    "low": "low", "minor": "low", "trivial": "low",
    "info": "info", "question": "info", "praise": "info", "note": "info",
    "nit": "info",
}


def normalize_severity(value) -> str:
    """Map any producer's severity onto the canonical enum — UNKNOWN maps UP
    to 'high' so an unclassifiable finding blocks rather than slips through.
    Shared consumption point for the EM gate and the dashboard renderer."""
    return _SEVERITY_MAP.get(str(value or "").strip().lower(), "high")


# Review discipline (v2.3.1). A finding's CLASS decides whether it gates a
# change, orthogonally to how bad it is. This is what stops a whole-tree
# 26-lens sweep (which always yields ~100 observations) from reading as "100
# blockers": only a regression, or a NEW high defect in the change's own diff,
# blocks — pre-existing debt and taste are surfaced but never block the change.
_CLASS_MAP = {
    "regression": "regression", "regressed": "regression",
    "pre-existing": "pre-existing", "preexisting": "pre-existing",
    "pre_existing": "pre-existing", "existing": "pre-existing",
    "debt": "pre-existing", "legacy": "pre-existing",
    "observation": "observation", "taste": "observation",
    "style": "observation", "nit": "observation", "opinion": "observation",
    "suggestion": "observation", "enhancement": "observation",
}


def normalize_finding_class(value) -> str:
    """Canonical finding class, or 'unclassified' when absent/foreign.

    Unlike severity, an unknown class maps to 'unclassified' (NOT up to
    'regression') — taste must never be inflated to a blocker. But an absent
    class does NOT let a high slip through either: `finding_blocks` routes an
    unclassified finding through the severity rule, so you cannot hide a real
    high defect merely by omitting the class."""
    v = str(value or "").strip().lower()
    return _CLASS_MAP.get(v, "unclassified")


def _finding_in_diff(finding: dict, changed_files) -> bool:
    if changed_files is None:
        return True                      # no diff context → cannot exclude
    f = str(finding.get("file") or "").replace("\\", "/")
    return f in {str(c).replace("\\", "/") for c in changed_files}


def finding_blocks(finding: dict, changed_files=None) -> bool:
    """Does this finding block THIS change's gate?

      regression                     -> always blocks
      pre-existing / observation     -> never blocks (surfaced, tracked)
      unclassified + high + in-diff  -> blocks (a new high defect in the
                                        change's own surface — fail closed)
      unclassified + high + no diff  -> blocks (cannot prove it's old)
      anything else                  -> does not block
    """
    cls = normalize_finding_class(finding.get("class"))
    if cls == "regression":
        return True
    if cls in ("pre-existing", "observation"):
        return False
    # unclassified: fall back to the severity rule (danger fails closed)
    if normalize_severity(finding.get("severity")) != "high":
        return False
    return _finding_in_diff(finding, changed_files)


def classify_findings(findings, changed_files=None) -> dict:
    """Split a findings list into the blocker set and the triage buckets, so a
    review headline reads '7 block · 93 to triage' instead of '100 issues'."""
    out = {"blockers": [], "regressions": [], "pre_existing": [],
           "observations": [], "unclassified": []}
    for f in findings or []:
        cls = normalize_finding_class(f.get("class"))
        if cls == "regression":
            out["regressions"].append(f)
        elif cls == "pre-existing":
            out["pre_existing"].append(f)
        elif cls == "observation":
            out["observations"].append(f)
        else:
            out["unclassified"].append(f)
        if finding_blocks(f, changed_files):
            out["blockers"].append(f)
    return out


# Evidence bundle → evidence.py (P2 / R-0012); extracted like audit.py, same
# line ratchet. UNTRUSTED INPUT — _evaluation_errors re-derives every
# obligation itself (evidence.py's docstring: why it stays out of VALIDATOR_SURFACE).
from evidence import EVIDENCE_JUDGMENT_KEYS, evidence  # noqa: E402,F401

# ---------------------------------------------------------------------------
# Audit sweep cadence + router-regression auto-filing (v3 Phase 1, R-0001):
# MOVED VERBATIM to audit.py (R-0006 / D-0004, v3 Phase 2), byte-frozen by
# taskplane/tests/test_audit_extraction.py. The names below are CALLER
# aliases bound once at import — NOT patch seams (t9 / R-0011 E6). Patch the
# MACHINERY at audit.<name> (audit.audit_counter, audit.audit_every, …),
# resolved module-locally inside audit.py: rebinding the loop alias is
# invisible to audit_due. Patch the GATE MATH at loop.<name>
# (finding_blocks, normalize_finding_class, load, _state_dir) — audit.py
# late-binds those via _loop() (audit.py:41-51) every call, so a patched
# loop.finding_blocks does govern the gate. TestPatchSeams pins both halves.
from audit import (  # noqa: E402,F401 — re-exports, not dead imports
    AUDIT_EVERY_DEFAULT,
    AUDIT_FILE,
    _audit_brief,
    _audit_path,
    _is_machinery_warn_row,
    _is_router_regression,
    _release_review_flagged,
    _blocking_claim_errors,
    _router_audit_gate,
    _router_regression_key,
    _routing_decision_from_meta,
    _routing_decision_of,
    _unresolved_high_errors,
    audit_counter,
    audit_due,
    audit_every,
    record_audit_review,
    router_audit,
)


def _coverage_disposition(v) -> str:
    """Legacy coverage values are tier strings ('deep'|'sweep'); v2 values
    (contract:findings-v2) are {verdict, ...} objects. One accessor so the
    tier validation accepts both shapes."""
    if isinstance(v, dict):
        v = v.get("verdict")
    return str(v or "")


def _engineering_review_errors(ws: str, state: dict | None = None) -> list:
    """Require full-catalog lens evidence before the EM gate can pass."""
    path = runtime_storage.review_public_path(ws, "findings.json")
    findings, errors = _read_json(path)
    if errors:
        return errors
    report_path = runtime_storage.review_public_path(ws, "report.md")
    try:
        with open(report_path, encoding="utf-8") as report_file:
            report_text = report_file.read()
        if not report_text.strip():
            errors.append("engineering narrative report is empty")
    except OSError:
        errors.append("engineering narrative report is missing: "
                      + report_path)
    meta = findings.get("meta") or {}
    # Real EM/sign-off gates always pass loop state and therefore require the
    # canonical selective kernel. ``state=None`` is the long-standing pure
    # classification seam used by audit/finding unit tests; keeping it free
    # of repository orchestration lets those tests judge only the rule they
    # name without constructing a fake review transaction.
    if state is not None:
        try:
            import review as _review
            import review_evidence as _review_evidence
            binding = review_kernel_binding(
                state, "em", _current_task(state))
            if not binding:
                raise RuntimeError("loop state has no bound EM review kernel")
            kernel_ws = str(binding.get("workspace") or ws)
            kernel = _review._load_state(kernel_ws, binding["run_id"])
            if kernel.get("status") != "complete" or kernel.get("stage") != "review":
                errors.append("engineering selective review kernel is incomplete")
            else:
                current = _review_evidence._read_current(
                    _review_evidence.ArtifactStore(kernel_ws))
                for key, value in (current or {}).items():
                    if meta.get(key) != value:
                        errors.append("engineering review contradicts canonical "
                                      f"revision identity: {key}")
        except Exception as exc:
            errors.append("engineering canonical revision is missing: "
                          f"{exc.__class__.__name__}: {exc}")
    if state:
        errors.extend(_design_review_errors(ws, state, meta))
    coverage = meta.get("lens_coverage") or {}
    if not isinstance(coverage, dict):
        errors.append("engineering lens coverage must be an object")
        coverage = {}
    catalog = lens_router.load_catalog()
    expected = {entry["id"] for entry in catalog.get("lenses") or []}
    missing = sorted(expected - set(coverage))
    # Legacy tiers ('deep'|'sweep') and v2 verdicts (contract:findings-v2:
    # {verdict: deep|light|n/a|deep (forced), ...}) are both valid coverage.
    valid_tiers = ("deep", "sweep", "light", "n/a", "deep (forced)")
    invalid = sorted(k for k, v in coverage.items()
                     if k in expected
                     and _coverage_disposition(v) not in valid_tiers)
    if missing:
        errors.append("engineering review omitted lenses: " + ", ".join(missing))
    if invalid:
        errors.append("engineering review has invalid lens tiers: "
                      + ", ".join(invalid))
    # EM v3 tightening: a lens skipped as n/a must carry MACHINE-CHECKABLE
    # negative evidence (the v2 dict shape with negative_evidence). A bare
    # string "n/a" asserted the skip without evidence AND slipped past the
    # router-audit backstop (which only diffs dict-shaped decisions) — the
    # one disposition that reduces coverage was the one with no proof.
    bare_na = sorted(
        k for k, v in coverage.items()
        if k in expected and isinstance(v, str) and v.strip().lower() == "n/a")
    if bare_na:
        errors.append(
            "engineering review marks lenses n/a without negative evidence "
            "(use the v2 dict shape {verdict: 'n/a', negative_evidence: "
            "[...]}): " + ", ".join(bare_na))
    else:
        for k, v in sorted(coverage.items()):
            if (k in expected and isinstance(v, dict)
                    and str(v.get("verdict", "")).strip().lower() == "n/a"
                    and not v.get("negative_evidence")):
                errors.append(
                    "engineering review marks lens n/a with EMPTY "
                    "negative_evidence: " + k)
    impact_ev = meta.get("impact")
    if not isinstance(impact_ev, dict):
        errors.append("engineering review is missing dependency impact evidence")
    elif (state or {}).get("graph_governance"):
        required = {"touched", "impacted", "total_impacted", "unknown",
                    "depth_limit", "truncated", "policy", "graph"}
        missing_impact = sorted(required - set(impact_ev))
        if missing_impact:
            errors.append("engineering dependency impact evidence is incomplete: "
                          + ", ".join(missing_impact))
        changed = [f for f in _diff_files(
            ws, (state or {}).get("baseline") or "HEAD")
            if not f.startswith(lens_router.LOOP_OWNED)]
        if changed:
            review_policy = _aggregate_impact_policy(
                (state or {}).get("tasks") or [])
            expected = depgraph.impact(ws, changed, policy=review_policy)
            if not impact_ev.get("touched"):
                errors.append("engineering dependency impact names no touched modules")
            elif not set(expected.get("touched") or []) <= \
                    set(impact_ev.get("touched") or []):
                errors.append("engineering dependency impact does not cover the diff")
            expected_fp = (expected.get("graph") or {}).get("content_fingerprint")
            actual_fp = (impact_ev.get("graph") or {}).get("content_fingerprint")
            if expected_fp and actual_fp != expected_fp:
                errors.append("engineering dependency impact uses a stale graph revision")
            if impact_ev.get("policy") != review_policy:
                errors.append("engineering dependency impact uses the wrong review policy")
    if not meta.get("tests"):
        errors.append("engineering review is missing test evidence")
    gate = meta.get("gate") or {}
    if gate.get("verdict") not in ("pass", "recommend-pass"):
        errors.append("engineering review does not recommend sign-off — "
                      'set meta.gate.verdict to "pass" or "recommend-pass" '
                      "in " + runtime_storage.review_public_path(
                          ws, "findings.json"))
    rows = findings.get("findings") or []
    if not isinstance(rows, list):
        errors.append("engineering findings must be a list")
        rows = []
    # v2.3.0 raw unresolved-high sweep (body in audit.py): unknown
    # severities normalize UP to high and BLOCK. Machinery warn rows are
    # exempt ONLY when re-derived as legitimate this run — the A5 shape
    # alone is a costume any findings author can wear.
    errors.extend(_unresolved_high_errors(meta, rows))
    # R-0013: commentary may not block this gate (body in audit.py).
    errors.extend(_blocking_claim_errors(ws, state, rows))
    # Audit sweep (v3 Phase 1): when the review recorded a routing decision,
    # diff the findings against it — n/a-lens findings are auto-filed as
    # router regressions and block sign-off via the frozen finding_blocks
    # rule (no guardrail change).
    errors.extend(_router_audit_gate(ws, path, findings, meta, rows))
    return errors


def submit(ws: str, outcome: str, note: str = "",
           task_id: str | None = None) -> dict:
    """Worker submission — evidence request, never a state transition.

    Trust boundary (L12, v2.2.1): "orchestrator-only gating" is a PROTOCOL
    guarantee, not a process-isolation one — any process with workspace
    access can call gate(). What holds mechanically is the EVIDENCE: a gate
    only advances when the fingerprinted submission matches the bytes on
    disk, so a worker gating itself still cannot pass unproven work. Gate
    calls are traced for after-the-fact attribution.

    The engine, not the worker, computes the changed paths and fingerprint.
    The orchestrator subsequently calls ``gate``; if anything changed between
    submission and validation, the gate rejects the stale evidence.  Repeating
    the same submission is idempotent, which makes interrupted/resumed drivers
    safe.

    A4 (decision 0018): the record additionally carries ``engine_fingerprint``
    — the identity of the ENGINE BUILD that produced it (tp.engine_fingerprint
    over the validator surface). Purely additive: older gates ignore the
    unknown key, and the evaluate gate uses it to refuse evidence produced by
    a different build than the one validating it.
    """
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    step = state.get("step")
    if step not in ("execute", "fix", "evaluate", "em"):
        return {"error": f"step '{step}' is not a worker submission step — "
                         "run `loop next` to see the current role and "
                         "instruction; submissions happen at execute/fix/"
                         "evaluate/em"}
    if outcome not in ("pass", "fail", "unavailable"):
        return {"error": "submission outcome must be pass, fail, or unavailable"}
    if outcome == "unavailable" and step != "evaluate":
        return {"error": "unavailable is only valid for model evaluation; "
                         "product execution/fix/review still submit pass or fail"}

    task = _current_task(state)
    act_ws = ws
    parallel_execute = step == "execute" and state.get("parallel")
    if not parallel_execute and task_id and \
            task_id != (task or {}).get("id"):
        # H1 (v2.2.1): outside a parallel EXECUTE wave the engine evaluates
        # ONE current task — silently dropping a mismatched --task would
        # record this worker's evidence against a different task.
        return {"error": f"--task {task_id} does not match the current "
                         f"task '{(task or {}).get('id')}' at step "
                         f"'{step}' — a wave worker submits only during "
                         "parallel EXECUTE; otherwise omit --task or "
                         "pass the current task's id"}
    if parallel_execute:
        task = next((x for x in state.get("tasks") or []
                     if x.get("id") == task_id), None)
        if task is None:
            return {"error": "parallel submit needs --task <id> of a wave member"}
        act_ws = task.get("workspace") or ws
    elif step in ("evaluate", "fix") and state.get("parallel"):
        tws = (task or {}).get("workspace")
        act_ws = tws if tws and os.path.isdir(tws) else ws

    runtime_guidance = None
    if outcome == "pass":
        runtime_guidance = runtime_eval.guide_loop(ws, task_id=task_id)
        if runtime_guidance.get("error"):
            return {"error": "runtime eval checkpoint failed: "
                             + runtime_guidance["error"],
                    "submitted": False, "transitioned": False,
                    "runtime_eval": runtime_guidance}
        if runtime_guidance.get("status") != "on_path":
            status = runtime_guidance.get("status")
            evidence_errors = []
            if step == "evaluate":
                verdict, read_errors = _read_json(
                    runtime_storage.evaluation_path(act_ws))
                evidence_errors = (read_errors or
                                   _acceptance_evidence_errors(
                                       act_ws, state, task, verdict))
            return {
                "error": ("runtime eval detected recoverable execution drift; "
                          "apply the supplied correction before pass submission"
                          if status == "correct" else
                          "runtime eval blocked repeated unresolved execution "
                          "drift; submit fail or return to the orchestrator"),
                "submitted": False, "transitioned": False,
                "runtime_eval": runtime_guidance,
                **({"dod": {"passed": False,
                            "errors": evidence_errors}}
                   if evidence_errors else {}),
            }

    snapshot = tp.snapshot_ref(act_ws)
    evidence_paths = runtime_storage.submission_evidence_paths(act_ws, step)
    graph_fingerprint = None
    if state.get("graph_governance") and \
            (step == "em" or step == "evaluate" and not state.get("parallel")):
        graph_fingerprint = (depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
    submission = {
        "step": step,
        "task": (task or {}).get("id"),
        "outcome": outcome,
        "note": note,
        "workspace": act_ws,
        "snapshot": snapshot,
        "fingerprint": tp.workspace_fingerprint(
            act_ws, snapshot, extra_paths=evidence_paths),
        "changed_files": (tp.changed_files(act_ws, snapshot)
                          if snapshot else []),
        "evidence_paths": evidence_paths,
        "graph_fingerprint": graph_fingerprint,
        "engine_fingerprint": tp.engine_fingerprint(),
        # A4 REPAIR (EM, v3 phase 3): engine_fingerprint attests the process
        # RUNNING submit — the same installed plugin the gate uses, so it
        # could never fire. Stamp the engine in the workspace the EVIDENCE
        # came from; None where that workspace carries no engine copy.
        "evidence_engine_fingerprint":
            tp.workspace_engine_fingerprint(act_ws),
        "submitted_at": int(time.time()),
    }
    with mutate(ws) as locked:
        if locked is None:
            return {"error": "no active loop"}
        def _same(existing):
            # engine_fingerprint is part of the identity: a re-submission
            # under a DIFFERENT engine must replace the record, not be
            # deduplicated into it (A4's in-flight remedy).
            return existing and all(
                existing.get(k) == submission.get(k)
                for k in ("step", "task", "outcome", "fingerprint",
                          "engine_fingerprint"))
        if parallel_execute:
            target = next((x for x in locked.get("tasks") or []
                           if x.get("id") == task_id), None)
            if target is None:
                return {"error": f"no task {task_id}"}
            if _same(target.get("_submission")):
                submission = target["_submission"]
            else:
                target["_submission"] = submission
        else:
            if _same(locked.get("_submission")):
                submission = locked["_submission"]
            else:
                locked["_submission"] = submission
    tp.trace(ws, "loop_submit", step=step, task=submission.get("task"),
             outcome=outcome, fingerprint=submission["fingerprint"][:12])
    return {"submitted": True, "transitioned": False,
            **({"runtime_eval": runtime_guidance}
               if runtime_guidance is not None else {}),
            "submission": submission,
            "next": "orchestrator: run loop gate with the submitted outcome"}


def _submission_staleness(ws: str, submission: dict) -> str | None:
    """Recompute the engine-owned attestations for a pending submission."""
    sub_ws = submission.get("workspace") or ws
    current_fp = tp.workspace_fingerprint(
        sub_ws, submission.get("snapshot"),
        extra_paths=submission.get("evidence_paths") or [])
    if current_fp != submission.get("fingerprint"):
        return "workspace or evidence changed after worker submission"
    graph_fp = submission.get("graph_fingerprint")
    if graph_fp:
        current_graph_fp = (depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
        if current_graph_fp != graph_fp:
            return "dependency graph changed after worker submission"
    return None


def gate(ws: str, outcome: str, note: str = "", task_id: str | None = None,
         rid: str | None = None) -> dict:
    """Record the current step's outcome, transition, and clear its contract."""
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    # v2.3.0 wiring: `--req R-xxxx` attaches a requirement to the in-flight
    # loop through the SANCTIONED validator (design_contract.design_attach_
    # requirement) — it validates exactly what the design DoR demands and
    # refuses to swap an anchored requirement; nothing downstream is skipped.
    if rid:
        attach_errors: list = []
        with mutate(ws) as st:
            if st is None:
                return {"error": "no active loop"}
            attach_errors = _dc.design_attach_requirement(ws, st, rid)
        if attach_errors:
            return {"error": "requirement attach failed — the gate was not "
                             "evaluated", "blockers": attach_errors}
        state = load(ws)
    step = state["step"]

    # v2.3.0: validate --task FIRST in a parallel wave. An unknown id used to
    # fall through to "worker evidence was not submitted", telling the driver
    # to submit for a task that does not exist (mirrors H1's submit-side
    # validation).
    if step == "execute" and state.get("parallel"):
        members = [str(x.get("id")) for x in state.get("tasks") or []]
        if not task_id:
            return {"error": "parallel gate needs --task <id> of a wave "
                             "member", "step": step}
        if task_id not in members:
            return {"error": f"unknown task id '{task_id}' — wave members: "
                             + ", ".join(members), "step": step}

    if state.get("submission_required") and step in \
            ("execute", "fix", "evaluate", "em"):
        task_for_submission = (_current_task(state) if step != "execute"
                               or not state.get("parallel") else
                               next((x for x in state.get("tasks") or []
                                     if x.get("id") == task_id), None))
        submission = ((task_for_submission or {}).get("_submission")
                      if step == "execute" and state.get("parallel") else
                      state.get("_submission"))
        if not submission:
            return {"error": "worker evidence was not submitted — the worker "
                             "must run `loop submit pass|fail|unavailable`; only the "
                             "orchestrator may evaluate `loop gate`",
                    "step": step}
        if submission.get("step") != step or submission.get("outcome") != outcome:
            return {"error": "gate request does not match the worker submission",
                    "step": step, "submission": submission}
        stale = _submission_staleness(ws, submission)
        if stale:
            return {"error": stale + " — discard stale evidence and submit again",
                    "step": step}

    # Parallel EXECUTE: a wave worker reports its own task's build outcome.
    # Concurrent workers gate against the SAME loop.json — serialize the whole
    # read-modify-write under an exclusive lock so a second worker's save
    # can't clobber the first's status update (which would revert a gated task
    # to running and stall the wave).
    if step == "execute" and state.get("parallel"):
        wt_precheck = next((x for x in state.get("tasks") or []
                            if x["id"] == task_id), None)
        if wt_precheck is None:
            return {"error": "parallel gate needs --task <id> of a wave "
                             "member"}
        # Fail closed: an uncommitted worktree means the branch carries
        # NOTHING — the merge would be empty and worktree removal would
        # destroy the work. Commit first, then gate.
        wt = wt_precheck.get("workspace")
        if outcome == "pass":
            dod_errors = _task_dod_errors(
                wt or ws, state, wt_precheck, tp.snapshot_ref(wt or ws))
            if dod_errors:
                tp.trace(ws, "loop_gate_blocked", step=step, task=task_id,
                         reason="dod", errors=dod_errors)
                return {"error": "Definition of Done failed — task remains "
                                 "running", "dod": {"passed": False,
                                 "errors": dod_errors}}
        if outcome == "pass" and wt and os.path.isdir(wt) and tp.is_dirty(wt):
            return {"error": f"task {task_id}: uncommitted work in {wt} — "
                             "the tp/<task> branch carries nothing yet. "
                             "`git add -A && git commit` in the worktree, "
                             "then gate again."}
        with mutate(ws) as locked:
            t = next((x for x in (locked.get("tasks") or [])
                      if x["id"] == task_id), None)
            if t is None:
                return {"error": "parallel gate needs --task <id> of a wave "
                                 "member"}
            # v2.3.0: the final staleness re-attest runs INSIDE the lock,
            # immediately before the status commits — no TOCTOU window
            # between the attest and the transition.
            if state.get("submission_required"):
                stale = _submission_staleness(ws, submission)
                if stale:
                    return {"error": stale + " during gate validation — "
                                     "submit the final state again",
                            "step": step}
            tp.clear(t.get("workspace") or ws)
            t["status"] = "built"
            verified_suite = ((state.get("_validated_suite_evidence") or {})
                              .get(t["id"]))
            if verified_suite:
                locked.setdefault("_suite_evidence", {})[t["id"]] = \
                    verified_suite
            t.pop("_submission", None)
            if outcome != "pass":
                t["_build_failed"] = True
            tp.trace(ws, "loop_gate", step=step, task=task_id, outcome=outcome,
                     note=note)
            running = [x["id"] for x in locked["tasks"]
                       if x.get("status") == "running"]
        return {"step": "execute", "task": task_id, "built": True,
                "still_running": running, "status": status(ws)}

    # H4 (v2.2.1): the pm gate was the one fail-open step — it advanced with
    # no spec and no submission. Symmetric minimal DoD: the authored
    # requirement must exist before the loop leaves Define.
    if step == "pm":
        if outcome != "pass":
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note=note or "pm rejected — staying at pm")
            return {"error": "pm gate: outcome was not 'pass' — refine the "
                             "requirement/spec, then gate again",
                    "step": "pm", "status": status(ws)}
        spec_rel = state.get("spec_path") or os.path.join("specs", "spec.md")
        spec_abs = spec_rel if os.path.isabs(spec_rel) \
            else os.path.join(ws, spec_rel)
        has_req = bool(state.get("requirement_id"))
        if not has_req and not (os.path.isfile(spec_abs)
                                and os.path.getsize(spec_abs) > 0):
            tp.trace(ws, "loop_gate_blocked", step=step, reason="dod",
                     errors=["no spec"])
            return {"error": "pm Definition of Done failed — no requirement "
                             "was authored. Write a non-empty specs/spec.md "
                             "(or record a requirement with `tp req new` and "
                             "attach its R-id with `tp loop gate pass --req "
                             "R-XXXX`), then gate again.",
                    "step": "pm",
                    "dod": {"passed": False,
                            "errors": [f"{spec_rel} missing or empty and no "
                                       "requirement_id is attached — the pm "
                                       "step authors the WHAT before the "
                                       "loop advances"]}}
        if has_req:
            rec = reqs.get_requirement(ws, state["requirement_id"])
            product_dor = reqs.product_dor(rec)
            refinement = product_dor["refinement"]
            if not product_dor["passed"]:
                errors = ([f"requirement {state['requirement_id']} is missing"]
                          if not rec else product_dor["errors"])
                tp.trace(ws, "loop_gate_blocked", step=step,
                         reason="requirement_dor", errors=errors)
                return {"error": "pm Definition of Ready failed — refine the "
                                 "same requirement before planning",
                        "step": "pm",
                        "dor": {"passed": False, "errors": errors,
                                "refinement": refinement}}
            # Dependencies and named contracts are recorded by req new. The
            # context-file ownership edge is equally mechanical and belongs
            # at this gate, not in a second model-authored graph command.
            context_files = list(rec.get("context_files") or [])
            if context_files:
                depgraph.link_requirement(
                    ws, rec["id"], context_files,
                    kind="planned", replace=True)
            state["requirement_refinement"] = refinement
            # Product owns refinement, including the optional strategic note.
            # The note is recorded as advisory evidence and cannot create a
            # standalone user gate or override the canonical Product DoR.
            product_evidence = dict(rec)
            product_evidence.setdefault("score", refinement.get("score", 1)
                                        if isinstance(refinement, dict) else 1)
            state["product_definition"] = _product_definition_gate(
                product_evidence)

    # Validate the proposed HOW while its read-only contract is active. The
    # designer cannot self-certify or mutate the as-built graph; a complete
    # contract advances only to the human approval gate.
    if step == "design":
        if outcome != "pass":
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note=note or "design rejected — staying at design")
            return {"error": "design gate: outcome was not 'pass' — revise "
                             "design/design.md and design/contract.json, then "
                             "gate again", "step": "design",
                    "status": status(ws)}
        design_errors = _design_dod_errors(ws, state)
        if design_errors:
            tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="design_dod", errors=design_errors)
            return {"error": "Design Definition of Done failed — revise the "
                             "Design Contract before approval",
                    "step": "design",
                    "dod": {"passed": False, "errors": design_errors}}

    # Validate the implementation-ready plan while its read-only contract is
    # still active. A rejected plan remains governed for the planner's retry.
    if step == "plan":
        _load_tasks(ws, state)
        if outcome != "pass":
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note=note or "plan rejected — staying at plan")
            return {"error": "plan gate: outcome was not 'pass' — the plan "
                             "was rejected. Revise plan/tasks.json (+ "
                             "plan/plan.md) and gate again; the loop stays at "
                             "the plan step.",
                    "step": "plan", "status": status(ws)}
        if not state.get("tasks"):
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note="phantom plan: plan/tasks.json missing or empty")
            return {"error": "plan gate: plan/tasks.json is missing or has "
                             "no tasks — the plan exists only as words. "
                             "Write plan/tasks.json (+ plan/plan.md for the "
                             "human), then gate again."}
        dor_errors = _plan_dor_errors(ws, state, apply=True)
        if dor_errors:
            tp.trace(ws, "loop_gate_blocked", step=step, reason="dor",
                     errors=dor_errors)
            return {"error": "Definition of Ready failed — revise "
                             "plan/tasks.json before approval or execution",
                    "step": "plan",
                    "dor": {"ready": False, "blockers": dor_errors}}
        # B2: ordering at the GATE too — checkpoint-less loops skip approve.
        if (refusal := tp.plan_ordering_refusal(ws, state.get("tasks"),
                                                "gate")):
            return refusal

    task = _current_task(state)
    act_ws = ws
    if step in ("evaluate", "fix") and state.get("parallel"):
        tws = (task or {}).get("workspace")
        act_ws = tws if tws and os.path.isdir(tws) else ws

    unavailable_verdict = None

    # A reported PASS is a request to evaluate the gate. Evidence, not the
    # agent's assertion, determines whether the state machine advances.
    if outcome == "pass" and step in ("execute", "fix"):
        dod_errors = _task_dod_errors(
            act_ws, state, task, tp.snapshot_ref(act_ws))
        if dod_errors:
            tp.trace(ws, "loop_gate_blocked", step=step, reason="dod",
                     errors=dod_errors)
            return {"error": "Definition of Done failed — step did not "
                             "advance", "step": step,
                    "dod": {"passed": False, "errors": dod_errors}}
    if outcome == "pass" and step == "evaluate":
        # A4: the engine that PRODUCED this evidence vs the one about to
        # judge it — a pure pre-check (decision 0018), so equal engines
        # leave the walk below byte-unchanged.
        if (skew := tp.engine_skew_refusal(ws, state.get("_submission"))):
            return skew
        evidence_errors = _evaluation_errors(act_ws, state, task)
        if evidence_errors:
            tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="evaluation_evidence", errors=evidence_errors)
            return {"error": "evaluation evidence failed — step did not "
                             "advance", "step": step,
                    "dod": {"passed": False, "errors": evidence_errors}}
    if outcome == "unavailable" and step == "evaluate":
        if (skew := tp.engine_skew_refusal(ws, state.get("_submission"))):
            return skew
        unavailable_errors, unavailable_verdict = \
            _evaluation_unavailable_errors(act_ws, state, task)
        if unavailable_errors:
            tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="invalid_evaluation_unavailability",
                     errors=unavailable_errors)
            return {"error": "evaluation unavailability was not proven — "
                             "step did not advance", "step": step,
                    "dod": {"passed": False,
                            "errors": unavailable_errors}}
    if outcome == "fail" and step == "evaluate":
        unavailable_errors, _ = _evaluation_unavailable_errors(
            act_ws, state, task)
        if not unavailable_errors:
            return {"error": "evaluation infrastructure is unavailable, not "
                             "a product defect — gate unavailable; no FIX "
                             "cycle was opened", "step": step}
    if outcome == "pass" and step == "em":
        review_errors = _engineering_review_errors(ws, state)
        if review_errors:
            tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="engineering_review", errors=review_errors)
            return {"error": "engineering review is incomplete — sign-off "
                             "is not available", "step": step,
                    "dod": {"passed": False, "errors": review_errors}}

    # H2 (v2.2.1): validation above ran on a snapshot and can take seconds
    # (tests, evidence, graph). Apply the transition under the state LOCK to
    # a FRESH read, so a wave worker's concurrent update to another task is
    # never clobbered by saving this stale snapshot wholesale. Fields the
    # VALIDATION itself computed on the snapshot (loaded plan tasks, graph
    # DoR) are carried over explicitly.
    _validated = state
    with mutate(ws) as state:
        if state is None:
            return {"error": "no active loop"}
        if state.get("step") != step:
            return {"error": f"loop advanced to '{state.get('step')}' while "
                             "this gate was validating — run loop next and "
                             "gate again", "step": state.get("step")}
        # v2.3.0: the final staleness re-attest runs INSIDE the state lock,
        # immediately before the transition commits — the old pre-lock check
        # left a TOCTOU window in which a workspace edit got blessed by a
        # gate whose evidence was attested against different bytes. (The
        # contract is cleared AFTER the locked transition, below, so a
        # refused gate also leaves the workspace governed.)
        if _validated.get("submission_required") and step in \
                ("execute", "fix", "evaluate", "em"):
            stale = _submission_staleness(ws, submission)
            if stale:
                return {"error": stale + " during gate validation — submit "
                                 "the final state again", "step": step}
        if _validated.get("tasks") and not state.get("tasks"):
            state["tasks"] = _validated["tasks"]
        if step == "plan":
            # plan validation recomputed these on the snapshot via
            # _load_tasks: loaded tasks, the ab flag, the round-scoped
            # selection reset, and the graph DoR verdict.
            if _validated.get("tasks"):
                state["tasks"] = _validated["tasks"]
            if "ab" in _validated:
                state["ab"] = _validated["ab"]
            if "parallel" in _validated:
                state["parallel"] = _validated["parallel"]
            if "selection" not in _validated:
                state.pop("selection", None)
            if "graph_dor" in _validated:
                state["graph_dor"] = _validated["graph_dor"]
        elif "design_graph_fingerprint" in _validated and \
                "design_graph_fingerprint" not in state:
            state["design_graph_fingerprint"] = \
                _validated["design_graph_fingerprint"]
        state.pop("_submission", None)
        if step == "pm":
            if "requirement_refinement" in _validated:
                state["requirement_refinement"] = \
                    _validated["requirement_refinement"]
            state["step"] = ("design" if state.get("design_required") else "plan")
        elif step == "design":
            if _consolidated_enabled():
                contract, _ = _design_contract(ws)
                state["design_fingerprint"] = _design_evidence_fingerprint(
                    ws, contract)
                state["design_approved_by"] = "mechanical-definition-gate"
                _record_design_contracts(ws, state, contract)
                state["step"] = ("design_approval" if state.get("design_only")
                                 else "plan")
                tp.trace(ws, "mechanical_gate", gate="design",
                         outcome="pass", human_required=False)
            else:
                state["step"] = "design_approval"
        elif step == "plan":
            # Product↔engineering graph, PLANNED side: link each task's
            # requirement to the modules its scope intends to touch, then
            # annotate the task with its blast radius (engineering) and any
            # OTHER requirements whose surface it overlaps (product). The
            # human approves the plan seeing both; the executor's contract
            # briefing carries them; evaluation compares against them later.
            _annotate_plan_graph(ws, state)
            derivation = _derive_consolidated_authority(ws, state, "execute")
            if derivation and derivation.get("authorized"):
                state["authority_derivations"] = {
                    **(state.get("authority_derivations") or {}),
                    "execute": derivation,
                }
                state["step"] = "execute"
                tp.trace(ws, "mechanical_gate", gate="plan",
                         outcome="pass", human_required=False,
                         authority=derivation.get("fingerprint"))
            else:
                state["step"] = ("plan_approval"
                                 if "plan" in state["checkpoints"]
                                 else "execute")
            state["current_task"] = 0
            if state["step"] == "execute":
                state["baseline"] = tp.git_head(ws)
        elif step == "execute":
            # a build always goes to evaluate; a FAILED build is flagged so
            # evaluate FAILs and routes to fix/escalate — one place owns the fail
            # policy (so the step transition itself is unconditional).
            state["step"] = "evaluate"
            verified_suite = ((_validated.get(
                "_validated_suite_evidence") or {}).get(
                    _current_task(state)["id"]))
            if verified_suite:
                current = _current_task(state)
                state.setdefault("_suite_evidence", {})[current["id"]] = \
                    verified_suite
            if outcome != "pass":
                state["_build_failed"] = True
        elif step == "evaluate":
            t = _current_task(state)
            build_failed = state.pop("_build_failed", False) or \
                t.pop("_build_failed", False)
            if outcome in ("pass", "unavailable") and not build_failed:
                t["status"] = "passed"
                if outcome == "unavailable":
                    availability = dict(
                        (unavailable_verdict or {}).get("evaluation") or {})
                    warning = {
                        "task": t.get("id"),
                        "status": "unavailable",
                        "reason_code": availability.get("reason_code"),
                        "detail": str(availability.get("detail") or "")[:500],
                    }
                    t["evaluation"] = warning
                    warnings = state.setdefault("evaluation_warnings", [])
                    warnings[:] = [row for row in warnings
                                   if row.get("task") != t.get("id")]
                    warnings.append(warning)
                # After the LAST task: A/B loops pause at the human SELECTION
                # gate (variants never merge — one gets picked) — but only
                # ONCE; a post-selection fix cycle goes back to the review.
                after_last = ("selection" if state.get("ab")
                              and not state.get("selection") else "em")
                if state.get("parallel"):
                    # merge is the driver's job (instruction), state just moves on
                    if all(x.get("status") in SETTLED
                           for x in state["tasks"]):
                        state["step"] = after_last
                    else:
                        state["step"] = "execute"   # next wave / next built task
                else:
                    # serial: advance to the next UNSETTLED task, skipping any the
                    # skip-cascade already closed (else a dependency-failed task
                    # gets silently built and shipped).
                    nxt = _next_unsettled_index(state, state["current_task"])
                    if nxt is not None:
                        state["current_task"] = nxt
                        state["step"] = "execute"
                    else:
                        state["step"] = after_last
            else:
                t["fix_cycles"] = t.get("fix_cycles", 0) + 1
                if t["fix_cycles"] <= state["max_fix_cycles"]:
                    state["step"] = "fix"
                else:
                    t["status"] = "failed"
                    state["step"] = "escalated"
        elif step == "fix":
            verified_suite = ((_validated.get(
                "_validated_suite_evidence") or {}).get(
                    _current_task(state)["id"]))
            if verified_suite:
                current = _current_task(state)
                state.setdefault("_suite_evidence", {})[current["id"]] = \
                    verified_suite
            state["step"] = "evaluate"
        elif step == "em":
            # The graph was true-d up before the EM brief, so its fingerprint is
            # part of the evidence being gated rather than a post-review mutation.
            state["step"] = "signoff"
    if step == "em" and outcome == "pass":
        # One more COMPLETED engineering review: advance the audit cadence
        # (every Nth em review runs as a full audit sweep). A cadence-store
        # failure is traced, never allowed to block a validated sign-off.
        try:
            reviews = record_audit_review(ws)
            tp.trace(ws, "audit_review_recorded", reviews=reviews,
                     next_audit_due=audit_due(ws, state))
        except Exception as exc:      # noqa: BLE001
            tp.trace(ws, "audit_counter_failed", error=str(exc))
    # Release the step's contract only AFTER the locked transition committed
    # (v2.3.0): clearing before the lock left the workspace ungoverned during
    # the commit window; a refused gate above leaves it governed for retry.
    tp.clear(act_ws)
    yield_meter.gate_snapshot(ws, step, outcome)   # records, never gates
    tp.trace(ws, "loop_gate", step=step, outcome=outcome, note=note,
             **({"reason": ((unavailable_verdict or {}).get("evaluation")
                             or {}).get("reason_code")}
                if outcome == "unavailable" else {}))
    return {"step": state["step"], "status": status(ws),
            **({"warning": (state.get("evaluation_warnings") or [])[-1]}
               if outcome == "unavailable" else {})}


def _signoff_dod(ws: str, state: dict) -> dict:
    """Mechanical final DoD over aggregate scope, requirements, tests, graph,
    engineering evidence, and committed knowledge. Human sign-off remains."""
    scopes: list = []
    for t in (state.get("tasks") or []):
        scopes.extend(t.get("scope") or [])
    baseline = state.get("baseline")
    errors: list = []
    notices: list = []
    errors.extend("requirement DoD: " + e for e in tp.requirement_coverage_errors(
        state.get("tasks") or [], lambda rid: reqs.get_requirement(ws, rid),
        state.get("requirement_id"), require_passed=True))
    if scopes:
        # Aggregate diff-scope, EXCLUDING loop-owned artifacts: they are
        # authored by governed steps under their own write-allow contracts
        # and human gates, so requiring them inside the union of TASK
        # scopes was a contradiction. Every other engine path (evaluate
        # routing, em review, impact, and — A2 — the per-task DoD) filters
        # lens_router.LOOP_OWNED the same way. Fail-closed stance
        # unchanged: no snapshot still errors, and NON-loop-owned files
        # outside the union still block.
        if not baseline:
            errors.append("diff_scope: cannot verify — no git snapshot "
                          "(commit the workspace before governing)")
        else:
            # plan_minted: the union IS the human-approved plan's scopes
            # (approved wildcard-free literals keep their provenance-gated
            # override); DEFAULT_OUT_OF_SCOPE here is STRICTER than the
            # old synthetic contract, which had no out_of_scope at all.
            coding = {"scope_paths": scopes,
                      "out_of_scope_paths": list(tp.DEFAULT_OUT_OF_SCOPE),
                      "plan_minted": True}
            for f in tp.changed_files(ws, baseline):
                if f.startswith(lens_router.LOOP_OWNED):
                    continue
                v = tp.scope_violation(f, coding)
                if v:
                    errors.append("diff_scope: " + v)
            if errors:
                errors.append(
                    "diff_scope recovery: revert the out-of-scope files or "
                    "widen the owning task's scope via the human gate "
                    "(attributable: trace + KB decision), then re-run")
    if state.get("graph_governance"):
        try:
            depgraph.scan(ws)
        except Exception as exc:
            errors.append(f"graph_dod: final merged-tree scan failed: {exc}")
    for task in state.get("tasks") or []:
        test_command = task.get("tests")
        if not test_command:
            errors.append(f"task {task.get('id', '?')}: test command missing")
            continue
        test_contract = tp.build_contract(
            f"SIGNOFF TEST: {task.get('id', '?')}",
            scope=task.get("scope"), test_command=test_command,
            plan_minted=True, regression_gate=True)
        # Aggregate scope is already checked; run each task's scoped evidence.
        test_contract["coding"]["dod"]["require_clean_scope_diff"] = False
        regression_files = [f for f in (tp.changed_files(ws, baseline)
                                        if baseline else [])
                     if tp.match_any(f, task.get("scope") or [])]
        task_notices: list = []
        errors.extend(f"task {task.get('id', '?')}: {e}" for e in tp.dod_check(
            test_contract, ws, baseline, regression_files=regression_files,
            notices=task_notices))
        notices.extend(f"task {task.get('id', '?')}: {n}"
                       for n in task_notices)
    errors.extend(_engineering_review_errors(ws, state))
    for problem in kb.lint(ws):
        errors.append("kb_lint: " + (problem.get("file", "?")) + " — "
                      + problem.get("problem", ""))
    # D-0008: sign-off is the human's decision point. A `tests_pass` that was
    # CITED rather than executed is a fact about the evidence being signed
    # for, so it travels with the verdict instead of only into the trace.
    return {"passed": not errors, "errors": errors, "notices": notices,
            "scope": scopes, "baseline": baseline}


def _record_design_contracts(ws: str, state: dict, contract: dict | None) -> list:
    """The sanctioned mechanical path for DESIGN-introduced contracts into
    the dependency graph (v2.3.0).

    A design may legitimately propose a NEW boundary (e.g.
    contract:order-cancelled-v2) that is not declared on the requirement.
    Only requirement contracts were auto-recorded, the designer is forbidden
    to mutate the graph, and the planner's contract has no Bash tool — so
    graph readiness blocked with 'contracts are not recorded in the
    dependency graph' and no in-band remedy. At the human design-approval
    gate-PASS the engine records each approved design contract as a
    req→contract edge (registering the contract node), recorded + traced.
    Plan DoR is NOT weakened: it still independently verifies every declared
    contract is recorded — this only provides the governed path that records
    them. Returns the recorded contract ids."""
    rid = state.get("requirement_id")
    if not rid:
        return []
    applied = []
    for row in (contract or {}).get("contracts") or []:
        cids = depgraph.contract_ids([row])
        if not cids:
            continue
        relation = (row.get("relation", "changes")
                    if isinstance(row, dict) else "changes")
        depgraph.record_edge(ws, depgraph.req_node(rid), cids[0],
                             kind=relation, confidence="high",
                             note="approved design contract")
        applied.append(cids[0])
    if applied:
        tp.trace(ws, "design_contracts_recorded", gate="design_approval",
                 requirement=rid, contracts=applied)
    return applied


def _annotate_plan_graph(ws: str, state: dict) -> None:
    """Plan-gate graph work: planned req→module links + per-task blast."""
    # Batch by requirement first: link_requirement(replace=True) refreshes a
    # requirement's whole edge set of one kind, so calling it once per task
    # would let a second task sharing the requirement WIPE the first's edges.
    planned = {}
    for t in state.get("tasks") or []:
        rid = t.get("req") or state.get("requirement_id")
        scope = t.get("scope") or []
        if rid and scope:
            planned.setdefault(rid, []).extend(scope)
    for rid, scopes in planned.items():
        depgraph.link_requirement(ws, rid, scopes, kind="planned")

    for t in state.get("tasks") or []:
        rid = t.get("req") or state.get("requirement_id")
        scope = t.get("scope") or []
        if not scope:
            continue
        mods = depgraph.scope_modules(ws, scope)
        imp = depgraph.impact(
            ws, mods, policy=t.get("impact_policy")
            or depgraph.impact_policy(t)) if \
            depgraph.load(ws)["modules"] else None
        prod = depgraph.product_impact(ws, mods)
        own = depgraph.req_node(rid) if rid else None
        shared = [r for r in prod["affected_requirements"] if r != own]
        t["blast"] = {
            "modules": mods,
            "impacted": imp["total_impacted"] if imp else 0,
            "unknown": imp["unknown"] if imp else mods,
            "truncated": bool(imp and imp.get("truncated")),
            "policy": t.get("impact_policy") or depgraph.impact_policy(t),
            "shared_with": shared,
            "dependent_requirements": prod["dependent_requirements"],
        }
        if shared:
            tp.trace(ws, "graph_shared_surface", task=t["id"],
                     requirement=rid, shared_with=shared)


def _true_up_graph(ws: str, state: dict) -> None:
    """Pre-EM graph work: realize requirements, then scan the final tree."""
    changed = [f for f in _diff_files(ws, state.get("baseline") or "HEAD")
               if not f.startswith(lens_router.LOOP_OWNED)]
    if not changed:
        depgraph.scan(ws)
        tp.trace(ws, "graph_true_up", files=0)
        return
    # Batch by requirement (see _annotate_plan_graph) so multiple tasks
    # sharing one requirement accumulate their realized surface instead of
    # the last task's replace=True wiping the earlier ones'.
    realized = {}
    for t in state.get("tasks") or []:
        rid = t.get("req") or state.get("requirement_id")
        if not rid:
            continue
        stems = [g.split("*", 1)[0] for g in (t.get("scope") or [])]
        mine = [f for f in changed
                if any(f.startswith(s) for s in stems if s)]
        realized.setdefault(rid, []).extend(mine)
    for rid, files in realized.items():
        depgraph.link_requirement(ws, rid, files or changed, kind="realizes")
    # Scan after recording the realized edges so the graph fingerprint covers
    # both the final code tree and requirement-to-implementation truth.
    depgraph.scan(ws)
    tp.trace(ws, "graph_true_up", files=len(changed))


def _refinement_report(ws: str, state: dict) -> list:
    """Score each task's anchored requirement at the plan gate — the
    forecast shows BEFORE a build starts (requirements-at-the-core)."""
    out = []
    for t in state.get("tasks") or []:
        rid = t.get("req") or state.get("requirement_id")
        if not rid:
            continue
        rec = reqs.get_requirement(ws, rid)
        if rec is None:
            out.append({"task": t["id"], "requirement": rid,
                        "error": "requirement not found in the KB"})
            continue
        g = reqs.gate(rec, high_cost=bool(t.get("high_cost")),
                      changed_files=t.get("scope"), task_type=t.get("type"))
        mode = reqs.suggest_mode(g["score"], len(t.get("scope") or []))
        out.append({"task": t["id"], "requirement": rid, "gate": g,
                    "mode_suggestion": mode})
        tp.trace(ws, "refinement_gate", task=t["id"], requirement=rid,
                 score=g["score"], blocking=g["blocking"],
                 mode=mode["mode"])
    return out


def approve(ws: str, force: bool = False, by: str = None) -> dict:
    """Pass a human checkpoint (plan-approval or EM sign-off).

    `by` (v1.4.0, built for Claude Tag threads): WHO approved and where —
    e.g. "Dana R. — 'approved' in #platform-eng thread". Recorded into the
    trace event and the KB decision, so a gate pass is attributable to a
    human even in environments with no hook enforcement. In an unattended
    or Tag session, an approve WITHOUT `by` is exactly the self-approval
    the adherence experiment flags — drivers must pass the human's words."""
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    step = state["step"]
    refinement = None
    attestation_warning = None
    gate_notices: list = []
    if not str(by or "").strip() and step in ("plan_approval", "signoff"):
        # L5 (v2.2.1): symmetric attestation. Design approval hard-requires
        # --by; these two gates stay compatible but an anonymous pass is
        # RECORDED as unattributed and warned — not silently equivalent.
        by = "(unattributed)"
        attestation_warning = (f"{step} passed without --by — record WHO "
                               "approved (name + where) for an attributable "
                               "gate trail")
        tp.trace(ws, "loop_approve_unattributed", gate=step)
    if step == "design_approval":
        if not str(by or "").strip():
            return {"error": "design approval needs --by with the human's "
                             "identity/context; the designer cannot self-approve"}
        design_errors = _design_dod_errors(ws, state)
        if design_errors:
            tp.trace(ws, "loop_approve_blocked", gate="design",
                     reason="dod", errors=design_errors, by=by)
            return {"error": "Design Definition of Done failed — approval "
                             "cannot be recorded", "step": step,
                    "dod": {"passed": False, "errors": design_errors}}
        contract, _ = _design_contract(ws)
        state["design_fingerprint"] = _design_evidence_fingerprint(ws, contract)
        state["design_approved_by"] = by
        state["step"] = "done" if state.get("design_only") else "plan"
        tp.trace(ws, "loop_approve", gate="design", by=by,
                 fingerprint=state["design_fingerprint"][:12])
        # v2.3.0 wiring: notices (e.g. self-attested lens evidence) surface
        # in the approval response AND in the recorded approval decision.
        gate_notices = _dc.design_approval_notices(ws, contract)
        # v2.3.0: the sanctioned mechanical path for design-introduced
        # contracts into the graph — recorded + traced at the human
        # gate-pass (see _record_design_contracts). Plan DoR is unchanged.
        _record_design_contracts(ws, state, contract)
        modules = ((contract or {}).get("graph") or {}).get(
            "proposed_modules") or []
        kb.record_decision(
            ws, f"Design approved: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}\nApproved by: {by}\n"
                    f"Fingerprint: {state['design_fingerprint']}"
                    + ("".join("\nNotice: " + n for n in gate_notices)),
            decision=(contract or {}).get("decision", "Design approved."),
            tags=["design-approval", "solution-design"],
            context_files=list(modules),
            links={"loop": "design", "modules": list(modules)})
    elif step == "plan_approval":
        current_errors = _design_current_errors(ws, state)
        if current_errors:
            return {"error": "approved design is stale — plan approval is "
                             "blocked", "step": step,
                    "dor": {"ready": False, "blockers": current_errors}}
        # Refinement gate (advisory; hard only for high-cost tasks).
        refinement = _refinement_report(ws, state)
        blocked = [r for r in refinement if r.get("gate", {}).get("blocking")]
        if blocked and not force:
            return {"error": "refinement gate BLOCKED — a high-cost task's "
                             "requirement is under the threshold. Refine it "
                             "(close the gaps) or `loop approve --force`.",
                    "refinement": refinement}
        # B2 (R-0008): mechanical brief-shape-before-golden-regen ordering.
        if (refusal := tp.plan_ordering_refusal(ws, state.get("tasks"),
                                                "approve", by=by)):
            return refusal
        if _consolidated_enabled():
            state["authority_target_revision"] = tp.git_head(ws)
            fields = _authorization_fields(ws, state)
            packet = authority_engine.create_packet(fields)
            actor = str(by or "").strip()
            receipt = authority_engine.approve(
                packet, actor=actor,
                thread="loop:" + packet["fingerprint"][:20],
                authenticated=bool(actor))
            state["authority_packet"] = packet
            state["authority_receipt"] = receipt
            state["authority_derivations"] = {
                "execute": authority_engine.derive(
                    packet, receipt, stage="execute", current=fields,
                    actor=receipt["actor"], thread=receipt["thread"])
            }
            tp.trace(ws, "authority_packet", actor=receipt["actor"],
                     packet=packet["fingerprint"],
                     receipt=receipt["fingerprint"])
        # Baseline for later diff-routing at EVALUATE/EM.
        state["baseline"] = tp.git_head(ws)
        state["step"] = "execute"
        state["current_task"] = 0
        tp.trace(ws, "loop_approve", gate="plan", by=by)
        # High-signal decision → the knowledge base.
        scope = sorted({g for t in (state.get("tasks") or [])
                        for g in t.get("scope", [])})
        kb.record_decision(
            ws, f"Plan approved: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}"
                    + (f"\nApproved by: {by}" if by else ""),
            decision=f"Approved a {len(state.get('tasks') or [])}-task plan.",
            tags=["plan-approval"], context_files=scope,
            links={"loop": "plan"})
    elif step == "signoff":
        dod = _signoff_dod(ws, state)
        if not dod["passed"]:
            tp.trace(ws, "loop_approve_blocked", gate="em_signoff",
                     reason="dod", errors=dod["errors"], by=by)
            return {"error": "Definition of Done failed — sign-off cannot "
                             "complete until the evidence is repaired",
                    "step": "signoff", "dod": dod}
        state["step"] = "retro"
        tp.trace(ws, "loop_approve", gate="em_signoff", final="retro", by=by)
        # v2.3.0 wiring: the sign-off payload carries the review's design
        # notices (accepted drift, declared edge realizations) when present.
        findings, _errs = _read_json(
            runtime_storage.review_public_path(ws, "findings.json"))
        gate_notices = _dc.design_review_notices(
            (findings or {}).get("meta") or {})
        scope = sorted({g for t in (state.get("tasks") or [])
                        for g in t.get("scope", [])})
        kb.record_decision(
            ws, f"Accepted: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}"
                    + (f"\nApproved by: {by}" if by else "")
                    + ("".join("\nNotice: " + n for n in gate_notices)),
            decision="EM review passed and the human signed off — shipped.",
            tags=["accepted", "em-signoff"], context_files=scope,
            links={"loop": "signoff"})
    elif step == "selection":
        return {"error": "the selection gate needs a CHOICE, not a plain "
                         "approve — `loop select <variant|task-id|hybrid>`"}
    else:
        return {"error": f"nothing to approve at step '{step}'"}
    # Commit under the lock with a compare-and-swap on the entry step (v2.3.1):
    # approve() runs seconds of unlocked validation (signoff DoD runs every
    # task's tests, refinement, kb writes); an unlocked save could clobber a
    # concurrent gate() transition (the lost-update class the H2 fix closed in
    # gate()). If the on-disk step advanced while we worked, abort instead.
    with mutate(ws) as locked:
        if locked.get("step") != step:
            return {"error": "the loop advanced concurrently during this "
                             f"approval (was '{step}', now "
                             f"'{locked.get('step')}') — re-run `loop next`",
                    "step": locked.get("step")}
        locked.clear()
        locked.update(state)
    out = {"step": state["step"], "status": status(ws)}
    if refinement:
        out["refinement"] = refinement
    if attestation_warning:
        out["warning"] = attestation_warning
    if gate_notices:
        out["notices"] = gate_notices
    if state["step"] == "retro":
        out["instruction"] = (
            "Human sign-off is recorded. Run `tp loop retro` once to record "
            "the lessons, true up the dependency graph, and close the loop.")
    return out


def select(ws: str, choice: str, note: str = "") -> dict:
    """The A/B selection gate — the human's pick of what ships. Accepts a
    variant letter, a task id, or 'hybrid'. This gate REPLACES the merge
    step variants never have: a winner goes to the engineering review; a
    hybrid goes back to plan for the graft (both variants kept as
    reference). Recorded to the KB — the WHY outlives the losing branch."""
    reconcile_authority_effects(ws)
    with mutate(ws) as state:
        if state is None:
            return {"error": "no active loop"}
        if state["step"] != "selection":
            return {"error": f"selection only at the selection gate "
                             f"(current: {state['step']})"}
        tasks = state.get("tasks") or []
        variants = [t for t in tasks if t.get("variant")] or tasks
        expected_revision = str(state.get("authority_target_revision") or
                                state.get("baseline") or "")
        # Revision validation and state mutation share one lock. A checkout
        # change can no longer land between validation and persistence.
        current_revision = tp.git_head(ws)
        boundary = authority_engine.build_selection(
            variants, selected=choice.strip(), revision=current_revision,
            expected_revision=expected_revision)
        if not boundary["authorized"] and \
                "stale_selection" in boundary["reasons"]:
            return {"error": "A/B selection is stale — the checkout revision "
                             "changed after the selection gate opened; refresh "
                             "the variants before choosing",
                    "expected_revision": expected_revision,
                    "actual_revision": current_revision,
                    "variants": [{"id": t["id"],
                                  "variant": t.get("variant")}
                                 for t in variants]}
        if not boundary["authorized"] and \
                "invalid_selection" in boundary["reasons"] and \
                choice.strip().lower() not in {
                    "hybrid", "neither", "none", "reject", "reject-both"}:
            return {"error": f"no variant matches '{choice}' — use a task "
                             "id, a variant letter, or 'hybrid'",
                    "variants": [{"id": t["id"],
                                  "variant": t.get("variant")}
                                 for t in variants]}
        state["_authority_revision_fence"] = current_revision
        if choice.strip().lower() == "hybrid":
            state["selection"] = {"choice": "hybrid", "note": note,
                                  "revision": current_revision}
            for t in variants:
                t["status"] = "reference"
            state["step"] = "plan"
            instruction = (
                "Hybrid selected: write a NEW plan/tasks.json with the graft "
                "task(s) — name the base variant's branch and what to graft "
                "from the other — then `loop gate pass`. Plan approval and "
                "the build/evaluate cycle apply as usual; both variant "
                "branches stay as reference until the retro.")
        elif choice.strip().lower() in (
                "neither", "none", "reject", "reject-both"):
        # Neither variant ships — the A/B round is abandoned. Both variants
        # become not_selected (kept as reference branches) and the loop goes
        # back to PLAN for a fresh approach, so the human who picks "neither"
        # has a real transition instead of parking at the selection gate.
            state["selection"] = {"choice": "neither", "note": note,
                                  "revision": current_revision}
            for t in variants:
                t["status"] = "not_selected"
            state["step"] = "plan"
            instruction = (
                "Neither variant selected: both are set aside (branches kept "
                "as reference). Write a NEW plan/tasks.json taking a "
                "different approach — what did both variants get wrong? — "
                "then `loop gate pass`. Plan approval and the build/evaluate "
                "cycle apply as usual.")
        else:
            c = choice.strip()
            win = next((t for t in variants
                        if t["id"] == c or
                        str(t.get("variant", "")).lower() == c.lower()), None)
            if win is None:
                return {"error": f"no variant matches '{choice}' — use a task "
                                 "id, a variant letter, or 'hybrid'",
                        "variants": [{"id": t["id"],
                                      "variant": t.get("variant")}
                                     for t in variants]}
            state["selection"] = {"choice": win["id"],
                                  "variant": win.get("variant"), "note": note,
                                  "revision": current_revision}
            win["selected"] = True
            win["status"] = "passed"
            for t in variants:
                if t is not win:
                    t["status"] = "not_selected"
            state["step"] = "em"
            instruction = (
                f"Winner: {win['id']}. Merge its branch "
                f"(`git merge tp/{win['id']}`), keep the losing branch as "
                "reference until the retro, clear the variant worktree "
                "contracts, then run the engineering review of the merged "
                "result (the complete selective routing decision).")
        selection = dict(state["selection"])
        goal = str(state.get("goal") or "")
        context_files = sorted({g for t in variants
                                for g in t.get("scope", [])})
        effect_id = f"selection:{current_revision}:{selection['choice']}"
        _enqueue_authority_effect(
            state, effect_id, trace_event="loop_select",
            trace_data={"choice": selection["choice"], "note": note},
            kb_data={
                "title": f"A/B selection: {selection['choice']} — {goal[:48]}",
                "context": (f"Goal: {goal}; variants: "
                            + ", ".join(t["id"] for t in variants)),
                "decision": (note or
                             f"Human selected {selection['choice']} at the "
                             "selection gate."),
                "tags": ["ab-selection"], "context_files": context_files,
                "links": {"loop": "selection"},
            })
    state.pop("_authority_revision_fence", None)  # fake/test mutate adapters
    fence_failure = state.pop("_revision_fence_failed", None)
    if fence_failure:
        return {
            "error": "A/B selection is stale — the checkout revision changed "
                     "during the locked selection commit; no authority "
                     "transition was persisted",
            "expected_revision": fence_failure["expected"],
            "actual_revision": fence_failure["actual"],
        }
    effects = reconcile_authority_effects(ws)
    return {"step": state["step"], "selection": selection,
            "instruction": instruction, "status": status(ws),
            "effect_delivery": effects}


def _cascade_skip(state: dict, root_id: str) -> list:
    """Skip every task that (transitively) depends on root_id — they can
    never reach passed, so leaving them pending would deadlock the wave.
    Returns the ids that were cascaded."""
    tasks = state.get("tasks") or []
    dead = {root_id}
    cascaded = []
    changed = True
    while changed:
        changed = False
        for t in tasks:
            if t.get("status") in SETTLED:
                continue
            if set(t.get("deps") or []) & dead:
                t["status"] = "skipped"
                dead.add(t["id"])
                cascaded.append(t["id"])
                changed = True
    return cascaded


def resolve(ws: str, decision: str) -> dict:
    """Human decision when a task escalated (fix cycles exhausted)."""
    state = load(ws)
    if state is None or state["step"] != "escalated":
        return {"error": "nothing escalated to resolve"}
    t = _current_task(state)
    if decision == "retry":
        t["fix_cycles"] = 0
        t["status"] = "running"
        state["step"] = "fix"
    elif decision == "skip":
        t["status"] = "skipped"
        # Cascade: a task that depended (transitively) on the skipped one
        # can never satisfy deps⊆passed — skip it too, so it doesn't hold
        # the wave forever (the deadlock). Record which were cascaded.
        cascaded = _cascade_skip(state, t["id"])
        if cascaded:
            tp.trace(ws, "loop_skip_cascade", root=t["id"], skipped=cascaded)
        if state.get("parallel"):
            # settled-aware: advance only when every task is settled
            if all(x.get("status") in SETTLED for x in state["tasks"]):
                state["step"] = ("selection" if state.get("ab")
                                 and not state.get("selection") else "em")
            else:
                state["step"] = "execute"
        else:
            # serial: skip past any task the cascade just settled, so the
            # next execute is a task that still has work owed.
            nxt = _next_unsettled_index(state, state["current_task"])
            if nxt is not None:
                state["current_task"] = nxt
                state["step"] = "execute"
            else:
                state["step"] = "em"
    elif decision == "defer":
        # Human parks the task on an external gate: it settles AND satisfies
        # its dependents (the work will exist, just not via this loop) — the
        # clean form of what previously required hand-editing loop.json.
        t["status"] = "external"
        if state.get("parallel"):
            if all(x.get("status") in SETTLED for x in state["tasks"]):
                state["step"] = ("selection" if state.get("ab")
                                 and not state.get("selection") else "em")
            else:
                state["step"] = "execute"
        else:
            nxt = _next_unsettled_index(state, state["current_task"])
            if nxt is not None:
                state["current_task"] = nxt
                state["step"] = "execute"
            else:
                state["step"] = "em"
    elif decision == "abort":
        state["step"] = "failed"
    else:
        return {"error": "decision must be retry|skip|defer|abort"}
    tp.trace(ws, "loop_resolve", decision=decision, task=t.get("id"))
    with mutate(ws) as locked:                       # v2.3.1: locked commit
        if locked.get("step") != "escalated":
            return {"error": "the loop advanced concurrently during resolve "
                             f"(now '{locked.get('step')}') — re-run",
                    "step": locked.get("step")}
        locked.clear()
        locked.update(state)
    return {"step": state["step"], "status": status(ws)}
def replan(ws: str, by: str, reason: str) -> dict:
    return loop_recovery.replan(ws, by=by, reason=reason, load_state=load, mutate_state=mutate, clear_contract=tp.clear, trace=tp.trace, record_decision=kb.record_decision)
def retro(ws: str) -> dict:
    return retro_engine.run(ws, load_state=load, mutate_state=mutate,
        loop_path=_loop_path(ws), normalize_severity=normalize_severity)
_load_tasks = loop_status.load_tasks
status = loop_status.status
user_summary = loop_status.user_summary
_publish_artifacts = loop_status.publish_artifacts
_with_dashboard = loop_status.with_dashboard
gate = _with_dashboard(gate)
submit = _with_dashboard(submit)
next_action = _with_dashboard(next_action)
approve = _with_dashboard(approve)
retro = _with_dashboard(retro)
