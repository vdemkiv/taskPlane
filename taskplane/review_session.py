"""Canonical consent and validation evidence for governed PR reviews.

Hosts transport this state; they do not interpret it.  The module is pure
domain behavior so Claude, Codex, the CLI, and tests all exercise one consent
and authority-change policy.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from typing import Iterable, Mapping


SESSION_SCHEMA = "taskplane.review-session/v1"
CONSENT_SCHEMA = "taskplane.review-consent/v1"
AUTHORITY_REQUEST_SCHEMA = "taskplane.review-authority-request/v1"
VALIDATION_EVIDENCE_SCHEMA = "taskplane.review-validation-evidence/v1"

MATERIAL_AUTHORITY_TRIGGERS = frozenset({
    "target_or_scope_changed",
    "destructive_or_external_action",
    "permission_or_credential_escalation",
    "unsafe_operation",
    "irreconcilable_requirement_ambiguity",
    "final_disposition",
})

_FAILURES = {
    "host_limitation": (
        "unavailable", "Use another supported host capability or retry later."),
    "dynamic_check_unavailable": (
        "unavailable", "Record the unavailable check and continue non-approvable."),
    "invalid_reference": (
        "incomplete", "Restore or regenerate the pinned evidence reference."),
    "renderer_failure": (
        "incomplete", "Retry the renderer; retained review evidence is unchanged."),
    "artifact_write_failure": (
        "incomplete", "Retry the artifact transaction from the retained revision."),
    "unrepaired_slot": (
        "incomplete", "Retry only the affected producer or mark it unavailable."),
}

_IMPLICIT_REVIEW_ACTIONS = frozenset({
    "collection", "mechanical_repair", "affected_retry",
    "artifact_publication",
})


class ReviewSessionError(ValueError):
    """A host or caller attempted an invalid review-session transition."""


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_target(value: object) -> dict:
    if not isinstance(value, Mapping):
        raise ReviewSessionError("review target must be a mapping")
    fingerprint = str(value.get("fingerprint") or "").strip().lower()
    revision = str(value.get("revision") or value.get("head") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or not revision:
        raise ReviewSessionError(
            "review target requires a SHA-256 fingerprint and revision")
    return {"fingerprint": fingerprint, "revision": revision}


def _validate_actions(values: object) -> list[dict]:
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes)):
        raise ReviewSessionError("available review actions must be a sequence")
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, Mapping):
            raise ReviewSessionError("review action must be a mapping")
        action = str(value.get("id") or "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", action):
            raise ReviewSessionError("review action id is invalid")
        if action in seen:
            raise ReviewSessionError("review action ids must be unique")
        seen.add(action)
        result.append({
            "id": action,
            "non_destructive": value.get("non_destructive") is True,
        })
    if not result:
        raise ReviewSessionError("review session requires available actions")
    return result


def create_session(*, run_id: str, target: Mapping,
                   available_actions: Iterable[Mapping]) -> dict:
    """Create one host-neutral review authority envelope."""
    run_id = str(run_id or "").strip()
    if not run_id:
        raise ReviewSessionError("review run id is required")
    actions = _validate_actions(available_actions)
    return {
        "schema": SESSION_SCHEMA,
        "run_id": run_id,
        "target": _validate_target(target),
        "available_actions": actions,
        "status": "awaiting_consent",
        "completed": False,
        "passed": False,
        "consent": None,
        "authority_requests": [],
        "findings": [],
        "failure": None,
        "metrics": {"approval_requests": 0},
    }


def _mode_from_response(response: object) -> str:
    text = " ".join(re.findall(r"[a-z0-9]+", str(response or "").lower()))
    if not text:
        raise ReviewSessionError("review consent response is empty")
    words = set(text.split())
    if text in {"static", "static only"} or (
            "static" in words and "dynamic" not in words):
        return "static"
    render = bool(words & {"render", "inline", "dashboard", "visualise",
                           "visualize", "widget", "show"})
    dynamic = bool(words & {"dynamic", "test", "tests", "build", "runtime",
                            "execute", "validation"})
    complete = bool(words & {"full", "complete", "everything", "all",
                             "proceed", "approved", "finish"})
    if text.replace(" ", "-") == "dynamic-render" or render and (
            dynamic or complete):
        return "dynamic-render"
    if dynamic or complete:
        return "dynamic"
    raise ReviewSessionError(
        "review consent does not identify static or dynamic validation")


def record_consent(session: Mapping, *, response: object, actor: str) -> dict:
    """Record one complete-review consent without requiring a magic phrase."""
    current = copy.deepcopy(dict(session))
    if current.get("schema") != SESSION_SCHEMA or \
            current.get("status") != "awaiting_consent":
        raise ReviewSessionError("review session is not awaiting consent")
    actor = str(actor or "").strip()
    if not actor:
        raise ReviewSessionError("consent actor is required")
    mode = _mode_from_response(response)
    known = [row["id"] for row in current["available_actions"]
             if row.get("non_destructive") is True]
    if mode == "static":
        actions = [action for action in known
                   if action in _IMPLICIT_REVIEW_ACTIONS]
    elif mode == "dynamic":
        actions = [action for action in known if action != "inline_render"]
    else:
        actions = known
    material = {
        "run_id": current["run_id"], "target": current["target"],
        "mode": mode, "actions": sorted(actions), "actor": actor,
    }
    current["consent"] = {
        "schema": CONSENT_SCHEMA,
        "mode": mode,
        "actions": actions,
        "actor": actor,
        "scope_fingerprint": _canonical_digest(material),
    }
    current["status"] = "active"
    current["metrics"]["approval_requests"] = 1
    return current


def request_authority(session: dict, *, action: str, fact: str,
                      trigger: str | None = None,
                      authority: str | None = None) -> dict | None:
    """Return a gate only for a material authority change.

    Routine actions already inside the immutable consent return ``None``.
    Unknown routine actions are programming errors, not excuses to ask the
    human for an undifferentiated approval.
    """
    if session.get("schema") != SESSION_SCHEMA or not session.get("consent"):
        raise ReviewSessionError("review consent is not configured")
    action = str(action or "").strip()
    if not trigger and action in session["consent"].get("actions", []):
        return None
    if trigger not in MATERIAL_AUTHORITY_TRIGGERS:
        raise ReviewSessionError(
            "a new approval requires a material authority trigger")
    fact = str(fact or "").strip()
    authority = str(authority or "").strip()
    if not fact or not authority:
        raise ReviewSessionError(
            "authority request must name the new fact and authority needed")
    request = {
        "schema": AUTHORITY_REQUEST_SCHEMA,
        "trigger": trigger,
        "fact": fact,
        "authority": authority,
        "action": action,
    }
    session.setdefault("authority_requests", []).append(request)
    session.setdefault("metrics", {}).setdefault("approval_requests", 0)
    session["metrics"]["approval_requests"] += 1
    return request


def apply_failure(session: Mapping, *, kind: str, detail: object) -> dict:
    """Project a stable actionable non-success without losing findings."""
    if kind not in _FAILURES:
        raise ReviewSessionError("unknown review-session failure")
    current = copy.deepcopy(dict(session))
    status, action = _FAILURES[kind]
    current.update({"status": status, "completed": False, "passed": False})
    current["failure"] = {
        "schema": "taskplane.review-session-failure/v1",
        "code": kind,
        "detail": str(detail or "")[:1000],
        "action": action,
    }
    return current


def session_transport_binding(session: Mapping) -> dict:
    """Return only canonical identities safe to cross a host boundary."""
    if session.get("schema") != SESSION_SCHEMA or not session.get("consent"):
        raise ReviewSessionError("review session binding requires consent")
    return {
        "schema": "taskplane.review-session-binding/v1",
        "run_id": str(session["run_id"]),
        "target_fingerprint": str(session["target"]["fingerprint"]),
        "consent_fingerprint": str(
            session["consent"]["scope_fingerprint"]),
    }


def sandbox_transport_binding(sandbox: Mapping, *, cwd: str) -> tuple[dict, str]:
    """Bind a validation launch to the real path of its disposable copy.

    Boolean ``push_disabled`` claims are not authority.  The adapter records a
    non-reversible root fingerprint in durable state and launches only from a
    real directory contained by that root.  Resolving both paths also closes
    symlink escapes.
    """
    sandbox = dict(sandbox or {})
    if sandbox.get("disposable") is not True or \
            sandbox.get("push_disabled") is not True or \
            not str(sandbox.get("sandbox_id") or "").strip():
        raise ReviewSessionError(
            "review validation requires a disposable push-disabled sandbox")
    root_value = str(sandbox.get("path") or sandbox.get("root") or "").strip()
    if not root_value:
        raise ReviewSessionError("review validation requires a sandbox root")
    root = os.path.realpath(root_value)
    workdir = os.path.realpath(str(cwd or ""))
    try:
        contained = os.path.commonpath((root, workdir)) == root
    except ValueError:
        contained = False
    if not os.path.isdir(root) or not os.path.isdir(workdir) or not contained:
        raise ReviewSessionError("review validation cwd escapes its sandbox")
    material = {
        "sandbox_id": str(sandbox["sandbox_id"]),
        "root": root,
        "push_disabled": True,
    }
    return ({
        "schema": "taskplane.review-sandbox-binding/v1",
        "sandbox_id": material["sandbox_id"],
        "root_fingerprint": _canonical_digest(material),
        "push_disabled": True,
    }, workdir)


def validation_evidence(*, submitted: Mapping, sandbox: Mapping) -> dict:
    """Validate and distinguish submitted-PR and sandbox outcomes."""
    submitted = dict(submitted or {})
    sandbox = dict(sandbox or {})
    head_unchanged = bool(submitted.get("head_before")) and \
        submitted.get("head_before") == submitted.get("head_after")
    remote_before = submitted.get("remote_before")
    remote_after = submitted.get("remote_after")
    if remote_before in (None, "") or remote_after in (None, ""):
        raise ReviewSessionError(
            "submitted remote observations are required")
    remote_unchanged = remote_before == remote_after
    if not head_unchanged or not remote_unchanged:
        raise ReviewSessionError(
            "submitted checkout and remote must remain unchanged")
    if sandbox.get("disposable") is not True or \
            sandbox.get("push_disabled") is not True:
        raise ReviewSessionError(
            "dynamic validation requires a disposable push-disabled sandbox")
    try:
        push_attempts = int(sandbox.get("push_attempts", -1))
    except (TypeError, ValueError) as exc:
        raise ReviewSessionError("sandbox push attempts are invalid") from exc
    if push_attempts != 0:
        raise ReviewSessionError("dynamic validation must not attempt a push")
    delta_ref = str(sandbox.get("delta_ref") or "").strip()
    if not delta_ref:
        raise ReviewSessionError("sandbox delta evidence reference is required")
    return {
        "schema": VALIDATION_EVIDENCE_SCHEMA,
        "submitted_pr": {
            "revision": submitted["head_before"],
            "unchanged": True,
            "outcome": str(submitted.get("outcome") or "unavailable"),
        },
        "remote": {"unchanged": True},
        "sandbox": {
            "disposable": True,
            "push_disabled": True,
            "delta_ref": delta_ref,
            "outcome": str(sandbox.get("outcome") or "unavailable"),
        },
        "push_attempts": 0,
    }
