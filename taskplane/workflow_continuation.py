"""Observed, read-only continuation through the original native Claude session.

A new conversation can inspect a precise resume request. It cannot adopt another
root, copy approvals/grants, or mutate the source store. Verification succeeds only
after Claude has resumed the original session in the original workspace.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any

from . import primitives, workflow as w, workflow_evidence as evidence

SCHEMA = "taskplane.resume-request/v1"
RESULT = "taskplane.resume-result/v1"


def binding(state: dict[str, Any]) -> dict[str, Any]:
    return {**{key: state[key] for key in ("workspace", "root", "run", "revision")},
            "visit": w.current(state)["id"], "scope_digest": primitives.content_fingerprint(state["scope"])}


def read_request(raw: str) -> dict[str, Any]:
    w.require(isinstance(raw, str) and 0 < len(raw.encode()) <= 16384,
              "invalid_evidence", "Resume requires a bounded observed request envelope.")
    try:
        value = json.loads(raw)
    except ValueError:
        raise w.Refusal("invalid_evidence", "Resume request must be a JSON object.") from None
    w.require(isinstance(value, dict) and value.get("schema") == SCHEMA,
              "invalid_evidence", "Resume request schema is missing.")
    return dict(value)


def native_root(session: str, workspace: Path, *, selected: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read native root lineage with bounded no-follow reads; never set identity.

    Environment selection alone is insufficient. Both the exact native transcript
    and, for the old run, its automatically selected immutable source must agree.
    These are cooperative local observations, not protected host attestation.
    """
    from . import claude_flow_usage, claude_worker_observations as native
    w.require(native.supported_reader(), "unsupported_authority", "Native resume lineage reads are unavailable.")
    target = claude_flow_usage.transcript(session, {})
    w.require(target is not None, "resume_identity", "Native session transcript is unavailable; resume the original session.")
    try:
        path = native._source_path(session, str(target))
        fd, project = native._open_source(path)
        with os.fdopen(fd, "rb", buffering=0) as stream:
            info = native._regular(stream.fileno())
            budget = native.ReadBudget(max_bytes=256 * 1024, max_records=256)
            if selected is not None:
                w.require(native._valid_seal(selected, "taskplane.claude-transcript-source/v1")
                          and selected.get("parent") == session and selected.get("path") == str(path)
                          and selected.get("project_identity") == project
                          and selected.get("identity") == native._identity(info)
                          and info.st_size >= selected.get("selected_size", 0),
                          "resume_identity", "The source run's native session was changed or replaced.")
                row = native._verify(stream.fileno(), selected["session_reference"], budget)
                rows = [(row, selected["session_reference"])] if row else []
            else:
                rows = []
                def collect(row: dict[str, Any], ref: dict[str, Any]) -> bool:
                    if "sessionId" in row:
                        rows.append((row, ref))
                        return False
                    return True
                native._scan(stream.fileno(), path, 0, info.st_size, budget, collect)
            w.require(bool(rows), "resume_identity", "Root lineage is unavailable within the native read bound.")
            row, ref = rows[0]
            w.require(row.get("sessionId") == session and row.get("isSidechain") is False
                      and not any(row.get(key) for key in ("agentId", "parentSessionId", "parent_session_id"))
                      and isinstance(row.get("cwd"), str) and Path(row["cwd"]).resolve() == workspace,
                      "resume_identity", "Continuation requires the exact independent native root and workspace.")
            return {"session": session, "reference": deepcopy(ref), "assurance": "observed"}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, w.Refusal):
            raise
        raise w.Refusal("resume_identity", "Native resume lineage is unsafe, foreign or unreadable.") from None


def guidance(state: dict[str, Any]) -> dict[str, Any]:
    return {"schema": RESULT, "status": "resume_required", "binding": binding(state),
            "detail": "This run belongs to its original native session. A new conversation cannot adopt its ownership.",
            "next_action": "Use flow resume --resume-mode inspect --run RUN --expected-revision N --resume-json REQUEST in this workspace with the actual human continuation request.",
            "approvals_changed": False, "ownership_changed": False}


def resume(controller: Any, *, actor: str, run: str, revision: int | None,
           mode: str, request: dict[str, Any], parent: str | None = None) -> dict[str, Any]:
    from .workflow_local import timestamp
    w.require(controller.adapter.profile == "native_workflow" and controller.adapter.name == "claude",
              "unsupported_authority", "This continuation contract uses Claude's native same-session resume.")
    w.require(not parent and actor == controller.principal, "scope_violation",
              "A native child cannot inspect or verify root continuation.")
    w.require(mode in {"inspect", "verify"}, "invalid_evidence", "Choose resume inspection or verification.")
    target = controller._path()
    with primitives.file_lock(str(target)):
        db = controller._read(target)
        w.require(db.get("active") == run and run in db["runs"], "resume_binding",
                  "Resume must address the exact active source run.")
        state = db["runs"][run]
        w.require(not state.get("finished") and not state.get("superseded_by") and not state.get("retired"),
                  "resume_binding", "Ended or replaced runs remain historical; they cannot be resumed.")
        w.require(type(revision) is int and revision == state["revision"]
                  and primitives.content_fingerprint(request.get("binding")) == primitives.content_fingerprint(binding(state)),
                  "resume_binding", "Resume request has a stale or foreign run, revision, visit or scope.")
        w.require(request.get("schema") == SCHEMA, "invalid_evidence", "Resume request schema is missing.")
        source, excerpt = request.get("source"), request.get("excerpt")
        w.require(isinstance(source, dict) and source.get("kind") in {"conversation", "native_prompt"}
                  and source.get("actor") == "user" and source.get("automatic") is False
                  and isinstance(source.get("reference"), str) and 0 < len(source["reference"]) <= 512
                  and isinstance(source.get("conversation"), str) and source["conversation"]
                  and request.get("recorder") == "root_orchestrator"
                  and isinstance(excerpt, str) and 0 < len(excerpt) <= 4096,
                  "resume_provenance", "Preserve the actual human continuation request and conversation source.")
        assert isinstance(source, dict) and isinstance(excerpt, str)
        observed = timestamp(source.get("observed_at"))
        w.require(timestamp(state["started_at"]) <= observed <= datetime.now(timezone.utc),
                  "resume_provenance", "Continuation request must follow this run and cannot be in the future.")
        # The request must identify this run. No generic assent, generated plan,
        # conditional request or quoted example silently changes sessions.
        normalized = excerpt.strip().casefold()
        match = re.fullmatch(r'(?:please\s+)?(?:continue|resume)\s+(?:run\s+)?([0-9a-f]{8,32})'
                             r'(?:\s+at\s+(?:the\s+)?(product|design|plan|build|evaluate|engineering|retro)'
                             r'(?:\s+phase)?)?[.!]?', normalized)
        w.require(match is not None and run.startswith(match[1])
                  and (not match[2] or match[2] == w.current(state)["phase"]),
                  "resume_intent", "Continuation intent is unclear or names another run/phase. Preserve the actual explicit request.")
        w.require(source["conversation"] == actor if mode == "inspect" else actor == state["root"],
                  "resume_identity", "Inspection belongs to the requesting conversation; verification requires the original root session.")
        w.require(not any(row.get("worker_id") in {actor, source["conversation"]}
                          for row in state.get("workers", {}).values()),
                  "scope_violation", "Workers cannot request root continuation.")
        w.require(controller.adapter.can_seal(state), "resume_live_work",
                  "Stop or join known live workers and command handles in the original session before resuming.")
        if mode == "inspect" and actor != state["root"]:
            w.require(not any(row.get("run") == run and row.get("state") == "admitted"
                              for row in db.get("admissions", {}).values()), "resume_live_work",
                      "The source run still has a pending native call. Finish it in its original session.")
        w.require(not evidence.changed(controller.workspace, state), "resume_binding",
                  "Source evidence changed; resolve stale evidence in the original session before continuation.")
        try:
            controller.adapter.before_action(state, "resume")
        except w.Refusal as exc:
            raise w.Refusal("resume_binding", "Resume source/workflow validation failed: " + exc.detail) from None
        selected = state.get("claude_transcript_source")
        w.require(isinstance(selected, dict), "resume_identity",
                  "The source run lacks an automatically observed native root. Reopen the original session and inspect again.")
        original = native_root(state["root"], controller.workspace, selected=selected)
        requester = native_root(source["conversation"], controller.workspace)
        current = original if actor == state["root"] else native_root(actor, controller.workspace)
        result = {**guidance(state), "status": "resumed" if mode == "verify" else "resume_required",
                  "mode": mode, "request": deepcopy(request), "request_sha256": primitives.content_fingerprint(request),
                  "identity": {"original": original, "requester": requester, "current": current},
                  "native_resume": {"cwd": str(controller.workspace), "argv": ["claude", "--resume", state["root"]]},
                  "grants_changed": False, "state_changed": False,
                  "coverage": {"process_census": "unknown; known pending calls, workers and handles checked",
                               "identity": "observed native lineage; not host attestation"}}
        result["next_action"] = ("Consume fresh flow context for this same run, then continue its current phase and existing checkpoint requirements."
                    if mode == "verify" else
                    "Exit any original session process, run native_resume.argv from native_resume.cwd, then repeat flow resume with --resume-mode verify and this unchanged request. Do not use --fork-session.")
        return result
