"""Canonical structural enforcement decisions for governed Taskplane work.

The host adapter owns observations; this module owns the one normalized
``live|unproven|advisory`` decision consumed by every command and projection.
It is deliberately synchronous, network-free, and independent of model text.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Mapping

import host_capabilities as host_caps
import storage


SCHEMA = "taskplane.enforcement-status/v1"
ADVISORY_SCHEMA = "taskplane.enforcement-advisory/v1"
STATUSES = frozenset(("live", "unproven", "advisory"))
MODES = frozenset(("strict", "warn", "off"))
_RELEVANT_CAPABILITIES = (
    "native_plugin_hooks_loaded",
    "repository_bridge_loaded",
    "repository_trust",
    "managed_policy_permission",
    "stable_event_identity",
)


class EnforcementError(ValueError):
    """The supplied structural evidence cannot form a trusted decision."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _timestamp(value: str | None = None) -> str:
    if value is not None:
        text = str(value).strip()
        if not text:
            raise EnforcementError("observed_at must not be empty")
        return text
    return datetime.fromtimestamp(time.time(), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _workspace_fingerprint(workspace: str) -> str:
    root = os.path.normcase(os.path.realpath(os.path.abspath(workspace)))
    return hashlib.sha256(root.encode("utf-8", errors="replace")).hexdigest()


def _repository_fingerprint(workspace: str) -> str:
    identity = storage.resolve_repository_identity(workspace)
    return hashlib.sha256(identity.repo_id.encode("utf-8")).hexdigest()


def _receipt_evidence(
        snapshot: host_caps.HostCapabilitySnapshot) -> dict[str, Any]:
    capabilities: dict[str, Any] = {}
    for name in _RELEVANT_CAPABILITIES:
        row = snapshot.capabilities.get(name)
        if isinstance(row, host_caps.Observation):
            value = row.to_dict()
            # The decision time is carried once at the top level. Rebuilding
            # an otherwise identical snapshot must not mint a new evidence id.
            value.pop("observed_at", None)
            capabilities[name] = value
    return {
        "effective_path": snapshot.effective_path,
        "capabilities": capabilities,
    }


def _meter_evidence(value: Mapping[str, Any] | None) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "governed": bool(row.get("governed")),
        "hook_seen": bool(row.get("hook_seen")),
        "warning": (str(row.get("warning"))[:1024]
                    if row.get("warning") else None),
    }


def _decision_id_payload(decision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in decision.items()
        if key not in {"evidence_id", "observed_at"}
    }


def enforcement_status(
        workspace: str, *, snapshot: host_caps.HostCapabilitySnapshot,
        liveness: Mapping[str, Any] | None = None,
        run_id: str | None = None, revision: str | int | None = None,
        mode: str = "strict", observed_at: str | None = None) -> dict[str, Any]:
    """Return the sole structural enforcement decision for one exact view.

    A live capability snapshot proves entry without an extra probe. Once a
    governed contract has aged into an explicit liveness warning, that meter
    evidence overrides the session receipt and closes the gate fail-closed.
    """
    if not isinstance(snapshot, host_caps.HostCapabilitySnapshot):
        raise TypeError("snapshot must be a HostCapabilitySnapshot")
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in MODES:
        raise EnforcementError("mode must be strict, warn, or off")
    workspace_fp = _workspace_fingerprint(workspace)
    if snapshot.workspace_fingerprint != workspace_fp:
        raise EnforcementError("host evidence belongs to another workspace")
    meter = _meter_evidence(liveness)
    receipt = _receipt_evidence(snapshot)
    structural_live = snapshot.effective_path in {
        "native_effective", "bridge_effective"}
    if meter["warning"]:
        status = "unproven"
        reasons = ["active contract has no current screen activity"]
    elif structural_live or meter["hook_seen"]:
        status = "live"
        reasons = ([f"host path {snapshot.effective_path} is structurally live"]
                   if structural_live else
                   ["active contract meter observed the screen"])
    else:
        status = "unproven"
        reasons = ["no session-compatible hook receipt or screen activity"]
    decision: dict[str, Any] = {
        "schema": SCHEMA,
        "repository_fingerprint": _repository_fingerprint(workspace),
        "workspace_fingerprint": workspace_fp,
        "session_fingerprint": snapshot.session_fingerprint,
        "run_id": str(run_id) if run_id is not None else None,
        "revision": revision,
        "host": snapshot.host,
        "mode": normalized_mode,
        "status": status,
        "receipt_evidence": receipt,
        "meter_evidence": meter,
        "reasons": reasons,
        "advisory": None,
        "observed_at": _timestamp(observed_at),
    }
    decision["evidence_id"] = "enf-" + _digest(
        _decision_id_payload(decision))
    return decision


def acknowledge_advisory(
        decision: Mapping[str, Any], *, actor: str,
        acknowledged_at: str | None = None) -> dict[str, Any]:
    """Return an attributable advisory decision derived from exact evidence."""
    if not isinstance(decision, Mapping) or decision.get("schema") != SCHEMA:
        raise EnforcementError("advisory requires an enforcement decision")
    if decision.get("status") not in {"live", "unproven"}:
        raise EnforcementError("advisory decision is already acknowledged")
    identity = str(actor or "").strip()
    if not identity:
        raise EnforcementError("advisory mode requires an attributable actor")
    when = _timestamp(acknowledged_at)
    advisory = {
        "schema": ADVISORY_SCHEMA,
        "actor": identity[:256],
        "acknowledged_at": when,
        "source_evidence_id": decision.get("evidence_id"),
    }
    advisory["decision_id"] = "adv-" + _digest(advisory)
    updated = copy.deepcopy(dict(decision))
    updated["status"] = "advisory"
    updated["advisory"] = advisory
    updated["reasons"] = list(updated.get("reasons") or []) + [
        f"advisory enforcement acknowledged by {advisory['actor']}"]
    updated["observed_at"] = when
    updated["evidence_id"] = "enf-" + _digest(
        _decision_id_payload(updated))
    return updated


def validate_decision(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and detach a decision before it crosses persistence APIs."""
    if not isinstance(value, Mapping) or value.get("schema") != SCHEMA:
        raise EnforcementError("invalid enforcement decision schema")
    if value.get("status") not in STATUSES:
        raise EnforcementError("invalid enforcement decision status")
    expected = "enf-" + _digest(_decision_id_payload(value))
    if value.get("evidence_id") != expected:
        raise EnforcementError("enforcement evidence identity mismatch")
    advisory = value.get("advisory")
    if value.get("status") == "advisory":
        if (not isinstance(advisory, Mapping)
                or advisory.get("schema") != ADVISORY_SCHEMA
                or not str(advisory.get("actor") or "").strip()
                or not str(advisory.get("decision_id") or "").startswith(
                    "adv-")):
            raise EnforcementError("advisory decision is not attributable")
        advisory_payload = dict(advisory)
        advisory_id = advisory_payload.pop("decision_id", None)
        if advisory_id != "adv-" + _digest(advisory_payload):
            raise EnforcementError("advisory decision identity mismatch")
    elif advisory is not None:
        raise EnforcementError("non-advisory decision carries advisory data")
    return copy.deepcopy(dict(value))
