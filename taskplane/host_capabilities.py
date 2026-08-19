"""Source-attributed host capabilities for taskPlane's host adapters.

Configuration proves installation or configuration only.  Runtime choices
that protect governance require a separate host-observed receipt for loading,
trust, and managed-policy permission.  Unknown or contradictory evidence is
kept explicit so callers can fall back or fail closed without guessing.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


SCHEMA = "taskplane.host-capabilities/v1"
STATUSES = frozenset(("supported", "unsupported", "partial", "unknown",
                      "stale", "contradictory", "changed"))
CONFIDENCES = frozenset(("high", "medium", "low"))
MAX_REASON_BYTES = 512
RUNTIME_RECEIPT_SCHEMA = "taskplane.host-hook-receipt/v1"
RUNTIME_RECEIPT_MAX_AGE_SECONDS = 300.0


def _bounded(value: object, limit: int = MAX_REASON_BYTES) -> str:
    raw = str(value or "").encode("utf-8", errors="replace")[:limit]
    while raw:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw[:-1]
    return ""


def _immutable_value(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_immutable_value(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((str(k), _immutable_value(v))
                            for k, v in value.items()))
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        if all(isinstance(item, tuple) and len(item) == 2 for item in value):
            return {str(k): _json_value(v) for k, v in value}
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class Observation:
    """One bounded fact and the authority that supplied it."""

    status: str
    source: str
    confidence: str = "medium"
    reason: str = ""
    observed_at: str = ""
    value: Any = None

    def __post_init__(self) -> None:
        status = self.status if self.status in STATUSES else "contradictory"
        confidence = (self.confidence if self.confidence in CONFIDENCES
                      else "low")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "source", _bounded(self.source, 128)
                           or "unknown")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "reason", _bounded(self.reason))
        object.__setattr__(self, "observed_at",
                           _bounded(self.observed_at, 64))
        object.__setattr__(self, "value", _immutable_value(self.value))

    def to_dict(self) -> dict[str, Any]:
        row = {
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "observed_at": self.observed_at,
            "reason": self.reason,
        }
        if self.value is not None:
            row["value"] = _json_value(self.value)
        return row


@dataclass(frozen=True)
class HostCapabilitySnapshot:
    """Immutable capability authority for one host session and workspace."""

    host: str
    host_version: str | None
    workspace_fingerprint: str
    session_fingerprint: str | None
    observed_at: str
    capabilities: Mapping[str, Observation]
    effective_path: str
    fingerprint: str
    schema: str = SCHEMA

    def capability(self, name: str) -> Observation:
        return self.capabilities[name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "host": self.host,
            "host_version": self.host_version,
            "workspace_fingerprint": self.workspace_fingerprint,
            "session_fingerprint": self.session_fingerprint,
            "observed_at": self.observed_at,
            "effective_path": self.effective_path,
            "fingerprint": self.fingerprint,
            "capabilities": {
                name: row.to_dict()
                for name, row in sorted(self.capabilities.items())
            },
        }


_ENV_OBSERVATIONS = {
    "TASKPLANE_NATIVE_HOOKS_LOADED": "native_plugin_hooks_loaded",
    "TASKPLANE_BRIDGE_HOOKS_LOADED": "repository_bridge_loaded",
    "TASKPLANE_REPOSITORY_TRUST": "repository_trust",
    "TASKPLANE_MANAGED_HOOK_POLICY": "managed_policy_permission",
    "TASKPLANE_WORKFLOWS_AVAILABLE": "workflow_availability",
    "TASKPLANE_NATIVE_STRUCTURED_OUTPUT": "native_structured_output",
    "TASKPLANE_MODEL_SELECTION": "model_selection",
    "TASKPLANE_EFFORT_SELECTION": "effort_selection",
    "TASKPLANE_STABLE_HOOK_EVENT_ID": "stable_event_identity",
    "TASKPLANE_NATIVE_PIP": "pip",
    "TASKPLANE_NATIVE_VISUALIZATION": "visualization",
    "TASKPLANE_NATIVE_CAROUSEL": "carousel",
    "TASKPLANE_NATIVE_APPROVAL": "approval",
    "TASKPLANE_NATIVE_SANDBOX": "sandbox",
    "TASKPLANE_NATIVE_HOSTING": "hosting",
    "TASKPLANE_NATIVE_BROWSER": "browser",
    "TASKPLANE_NATIVE_SIDE_PANEL": "side_panel",
}

_STATUS_ALIASES = {
    "1": "supported", "true": "supported", "yes": "supported",
    "supported": "supported", "allowed": "supported",
    "trusted": "supported", "loaded": "supported",
    "0": "unsupported", "false": "unsupported", "no": "unsupported",
    "unsupported": "unsupported", "denied": "unsupported",
    "untrusted": "unsupported", "not_loaded": "unsupported",
    "unknown": "unknown", "partial": "partial", "stale": "stale",
    "contradictory": "contradictory", "changed": "changed",
    "changed-mid-run": "changed", "changed_mid_run": "changed",
}


HOST_NATIVE_SURFACES = (
    "pip", "visualization", "carousel", "approval", "sandbox", "hosting",
    "browser", "side_panel",
)
SURFACE_SELECTION_SCHEMA = "taskplane.host-surface-selection/v1"


@dataclass(frozen=True)
class SurfaceSelection:
    """Auditable selection for exactly one independently optional surface."""

    surface: str
    host: str
    host_version: str | None
    status: str
    source: str
    confidence: str
    freshness: str
    selected_surface: str
    limitation: str | None
    fallback: str | None
    observed_at: str
    reason: str
    schema: str = SURFACE_SELECTION_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "surface": self.surface,
            "host": self.host,
            "host_version": self.host_version,
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "freshness": self.freshness,
            "selected_surface": self.selected_surface,
            "limitation": self.limitation,
            "fallback": self.fallback,
            "observed_at": self.observed_at,
            "reason": self.reason,
        }


def negotiate_host_surfaces(
        *, host: str, host_version: str | None,
        observations: Mapping[str, Observation], observed_at: str = "",
        surfaces: tuple[str, ...] = HOST_NATIVE_SURFACES,
        fallback: str = "accessible_bounded") -> Mapping[str, SurfaceSelection]:
    """Select native functionality only from fresh, explicit support.

    Each capability is independent. Missing or malformed evidence is unknown;
    partial, stale, contradictory, or changed evidence fails closed for that
    surface only. Canonical workflow state is intentionally absent here.
    """
    selections: dict[str, SurfaceSelection] = {}
    for name in surfaces:
        row = observations.get(name)
        if not isinstance(row, Observation):
            row = _unknown(name, observed_at)
        status = row.status
        native = status == "supported"
        freshness = (
            "fresh" if status in {"supported", "unsupported"} else status
        )
        selections[name] = SurfaceSelection(
            surface=name,
            host=_bounded(host, 32) or "unknown",
            host_version=_bounded(host_version, 64) or None,
            status=status,
            source=row.source,
            confidence=row.confidence,
            freshness=freshness,
            selected_surface="native" if native else "fallback",
            limitation=None if native else status,
            fallback=None if native else fallback,
            observed_at=row.observed_at or _bounded(observed_at, 64),
            reason=row.reason,
        )
    return MappingProxyType(selections)


def negotiate_snapshot_surfaces(
        snapshot: HostCapabilitySnapshot,
        *, surfaces: tuple[str, ...] = HOST_NATIVE_SURFACES,
        fallback: str = "accessible_bounded") -> Mapping[str, SurfaceSelection]:
    """Negotiate directly from a sealed host capability snapshot."""
    return negotiate_host_surfaces(
        host=snapshot.host,
        host_version=snapshot.host_version,
        observations=snapshot.capabilities,
        observed_at=snapshot.observed_at,
        surfaces=surfaces,
        fallback=fallback,
    )


def progress_surface_projection(
        *, host: str, host_version: str | None,
        pip_observation: Observation, durable_status: Mapping[str, Any]) -> dict:
    """Project one durable status snapshot to PiP or its bounded fallback.

    Capability evidence selects presentation only.  The canonical progress
    identity and values always come from the already-persisted status snapshot,
    and the projection is explicitly non-gating.
    """
    if not isinstance(pip_observation, Observation):
        raise TypeError("pip_observation must be an Observation")
    if not isinstance(durable_status, Mapping):
        raise TypeError("durable_status must be a mapping")
    status = str(durable_status.get("status") or "unavailable")
    identity = durable_status.get("identity")
    active = durable_status.get("active")
    if status == "available" and (not isinstance(identity, Mapping)
                                  or not isinstance(active, Mapping)):
        raise ValueError("available durable status requires identity and active")
    selection = negotiate_host_surfaces(
        host=host, host_version=host_version,
        observations={"pip": pip_observation},
        observed_at=pip_observation.observed_at, surfaces=("pip",))["pip"]
    native = selection.selected_surface == "native" and status == "available"
    return {
        "schema": "taskplane.progress-surface/v1",
        "host": _bounded(host, 32) or "unknown",
        "host_version": _bounded(host_version, 64) or None,
        "selected_surface": "native-pip" if native else "accessible-bounded",
        "capability": selection.to_dict(),
        "identity": dict(identity) if isinstance(identity, Mapping) else None,
        "active": dict(active) if isinstance(active, Mapping) else None,
        "state": durable_status.get("state"),
        "tokens": durable_status.get("tokens"),
        "focus_elapsed_seconds": durable_status.get("focus_elapsed_seconds"),
        "eta": durable_status.get("eta"),
        "updated_at": durable_status.get("updated_at"),
        "status": status,
        "limitation": (None if native else
                       durable_status.get("reason") or selection.limitation),
        "gating": False,
    }


def observations_from_environment(
        environment: Mapping[str, str] | None = None) -> dict[str, Observation]:
    """Decode explicit host receipts without inferring them from files.

    The adapter that launches taskPlane owns these variables.  Missing values
    remain unknown; invalid values are contradictory rather than truthy.
    """
    env = environment if environment is not None else os.environ
    result: dict[str, Observation] = {}
    shared_reason = env.get("TASKPLANE_HOST_RECEIPT_REASON", "")
    for variable, capability in _ENV_OBSERVATIONS.items():
        if variable not in env:
            continue
        raw = str(env.get(variable, "")).strip().lower()
        status = _STATUS_ALIASES.get(raw, "contradictory")
        reason = shared_reason or (
            f"{variable} reported {raw!r}" if raw else
            f"{variable} was empty")
        result[capability] = Observation(
            status=status, source=f"host-receipt:environment:{variable}",
            confidence="high", reason=reason)
    return result


def _receipt_dir(home: str) -> str:
    return os.path.join(os.path.abspath(home), "host-receipts")


def _receipt_path(home: str, hook_path: str) -> str:
    return os.path.join(_receipt_dir(home), f"{hook_path}.json")


def _fingerprint_text(value: object) -> str | None:
    text = str(value or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None


def record_runtime_hook_receipt(
        home: str, *, hook_path: str, event: Mapping[str, Any],
        observed_at: float | None = None) -> dict[str, Any]:
    """Persist proof that a configured hook actually executed.

    Hook execution is the runtime receipt onboarding needs. It is global to
    the Codex task, not to the repository currently being reviewed: the hook
    receives the tool/event cwd and can govern a prepared checkout without
    forcing the user to open another task there. Only fingerprints and
    bounded event metadata are retained; no prompt or tool input is stored.
    """
    path_name = str(hook_path or "").strip().lower()
    if path_name not in {"native", "bridge"}:
        raise ValueError("hook_path must be native or bridge")
    if not isinstance(event, Mapping):
        raise TypeError("hook event must be a mapping")
    session = (event.get("session_id") or event.get("thread_id")
               or event.get("conversation_id") or
               os.environ.get("CODEX_THREAD_ID") or
               os.environ.get("CLAUDE_SESSION_ID"))
    event_values = [str(event.get(key) or "") for key in (
        "session_id", "thread_id", "turn_id", "tool_use_id",
        "hook_event_name", "tool_name", "agent_id")]
    event_identity = "\0".join(event_values)
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else ""
    receipt = {
        "schema": RUNTIME_RECEIPT_SCHEMA,
        "hook_path": path_name,
        "observed_at": float(observed_at if observed_at is not None
                             else time.time()),
        "session_fingerprint": _fingerprint_text(session),
        "event_fingerprint": (_fingerprint_text(event_identity)
                              if any(event_values) else None),
        "workspace_fingerprint": _fingerprint_text(
            os.path.normcase(os.path.realpath(cwd))) if cwd else None,
        "event_name": _bounded(event.get("hook_event_name"), 64),
    }
    directory = _receipt_dir(home)
    os.makedirs(directory, exist_ok=True)
    target = _receipt_path(home, path_name)
    # A session-bound receipt remains valid for that task. Avoid an fsync on
    # every tool call once this hook path has proved it executed.
    if receipt["session_fingerprint"]:
        try:
            with open(target, encoding="utf-8") as handle:
                prior = json.load(handle)
            if isinstance(prior, dict) and prior.get("schema") == \
                    RUNTIME_RECEIPT_SCHEMA and prior.get("hook_path") == \
                    path_name and prior.get("session_fingerprint") == \
                    receipt["session_fingerprint"]:
                return prior
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", delete=False,
                dir=directory, prefix=f".{path_name}-receipt-",
                suffix=".tmp") as handle:
            temporary = handle.name
            json.dump(receipt, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return receipt


def runtime_hook_observations(
        home: str, *, session_id: str | None = None,
        workspace: str | None = None,
        now: float | None = None) -> dict[str, Observation]:
    """Return fresh, session-compatible observations from hook execution."""
    current = float(now if now is not None else time.time())
    expected_session = _fingerprint_text(session_id)
    expected_workspace = (_fingerprint_text(
        os.path.normcase(os.path.realpath(workspace))) if workspace else None)
    receipts: dict[str, dict[str, Any]] = {}
    for hook_path in ("native", "bridge"):
        try:
            with open(_receipt_path(home, hook_path), encoding="utf-8") as f:
                row = json.load(f)
            if not isinstance(row, dict) or row.get("schema") != \
                    RUNTIME_RECEIPT_SCHEMA or row.get("hook_path") != hook_path:
                continue
            age = current - float(row.get("observed_at"))
            if age < -30.0:
                continue
            observed_session = row.get("session_fingerprint")
            if expected_session and observed_session and \
                    observed_session != expected_session:
                continue
            if hook_path == "bridge" and expected_workspace and \
                    row.get("workspace_fingerprint") != expected_workspace:
                continue
            # A session identity is the durable boundary. Time expiry is only
            # needed for hosts that do not expose one.
            if not observed_session and age > RUNTIME_RECEIPT_MAX_AGE_SECONDS:
                continue
            receipts[hook_path] = row
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue

    observations: dict[str, Observation] = {}
    for hook_path, capability in (
            ("native", "native_plugin_hooks_loaded"),
            ("bridge", "repository_bridge_loaded")):
        if hook_path in receipts:
            observations[capability] = Observation(
                status="supported", source=f"runtime-hook:{hook_path}",
                confidence="high", reason=(
                    f"{hook_path} hook executed in this active host task"))
    if receipts:
        observations["managed_policy_permission"] = Observation(
            status="supported", source="runtime-hook:execution",
            confidence="high",
            reason="host policy permitted the hook command to execute")
    if "bridge" in receipts:
        observations["repository_trust"] = Observation(
            status="supported", source="runtime-hook:bridge",
            confidence="high",
            reason="repository bridge executed for the active host task")
    if len(receipts) == 2:
        native_event = receipts["native"].get("event_fingerprint")
        bridge_event = receipts["bridge"].get("event_fingerprint")
        if native_event and native_event == bridge_event:
            observations["stable_event_identity"] = Observation(
                status="supported", source="runtime-hook:exactly-once-claim",
                confidence="high",
                reason="native and bridge observed the same hook event")
    return observations


def _list_observation(environment: Mapping[str, str], variable: str,
                      capability: str) -> Observation | None:
    if variable not in environment:
        return None
    raw = str(environment.get(variable) or "").strip()
    try:
        value = json.loads(raw) if raw.startswith("[") else [
            item.strip() for item in raw.split(",") if item.strip()]
    except (TypeError, ValueError):
        value = None
    valid = (isinstance(value, list) and bool(value)
             and all(isinstance(item, str) and item.strip() for item in value))
    return Observation(
        status="supported" if valid else "contradictory",
        source=f"host-receipt:environment:{variable}", confidence="high",
        reason=(f"{capability.replace('_', ' ')} reported by host"
                if valid else f"{variable} is malformed"), value=value)


def dispatch_snapshot_from_environment(
        ws: str, *, host: str,
        environment: Mapping[str, str] | None = None) -> HostCapabilitySnapshot:
    """Build the dispatch subset from explicit adapter-owned receipts."""
    env = environment if environment is not None else os.environ
    rows = observations_from_environment(env)
    for variable, capability in (
            ("TASKPLANE_SUPPORTED_MODEL_ALIASES", "supported_model_aliases"),
            ("TASKPLANE_SUPPORTED_EFFORT_VALUES", "supported_effort_values")):
        row = _list_observation(env, variable, capability)
        if row is not None:
            rows[capability] = row
    return probe_snapshot(
        ws, host=host, install_context=str(
            env.get("TASKPLANE_INSTALL_CONTEXT") or "personal"),
        native_installed=None, bridge_configured=None, observations=rows,
        host_version=env.get("TASKPLANE_HOST_VERSION"),
        session_id=env.get("CODEX_THREAD_ID") or env.get("CLAUDE_SESSION_ID"),
        now=str(env.get("TASKPLANE_HOST_RECEIPT_AT") or ""))


def _unknown(name: str, now: str) -> Observation:
    return Observation(
        status="unknown", source="no-host-receipt", confidence="low",
        reason=f"no host-observed {name.replace('_', ' ')} receipt",
        observed_at=now)


def _configured(value: bool | None, source: str, subject: str,
                now: str) -> Observation:
    if value is None:
        return _unknown(subject, now)
    return Observation(
        status="supported" if value else "unsupported", source=source,
        confidence="high",
        reason=f"{subject} is {'present' if value else 'absent'}",
        observed_at=now)


def _with_time(row: Observation, now: str) -> Observation:
    return Observation(status=row.status, source=row.source,
                       confidence=row.confidence, reason=row.reason,
                       observed_at=row.observed_at or now, value=row.value)


def _contradict_loaded(row: Observation, installed: Observation,
                       name: str, now: str) -> Observation:
    if row.status == "supported" and installed.status == "unsupported":
        return Observation(
            status="contradictory", source=row.source,
            confidence="high", observed_at=row.observed_at or now,
            reason=f"{name} is reported loaded but is not installed/configured")
    return row


def probe_snapshot(
        ws: str, *, host: str, install_context: str,
        native_installed: bool | None, bridge_configured: bool | None,
        observations: Mapping[str, Observation] | None = None,
        host_version: str | None = None, session_id: str | None = None,
        now: str = "") -> HostCapabilitySnapshot:
    """Build one snapshot from local configuration plus explicit receipts."""
    observed_at = _bounded(now, 64)
    supplied = dict(observations or {})
    rows: dict[str, Observation] = {
        "native_plugin_hooks_installed": _configured(
            native_installed, "local-config:plugin-hooks-manifest",
            "native plugin hook manifest", observed_at),
        "repository_bridge_configured": _configured(
            bridge_configured, "local-config:workspace-hook-bridge",
            "repository hook bridge configuration", observed_at),
    }
    names = (
        "native_plugin_hooks_loaded", "repository_bridge_loaded",
        "repository_trust", "managed_policy_permission",
        "workflow_availability", "native_structured_output",
        "model_selection", "supported_model_aliases", "effort_selection",
        "supported_effort_values", "stable_event_identity",
        *HOST_NATIVE_SURFACES,
    )
    for name in names:
        row = supplied.get(name)
        rows[name] = _with_time(row, observed_at) if isinstance(
            row, Observation) else _unknown(name, observed_at)

    # A personal install has no organization-managed hook restriction.  This
    # says only that managed policy is not the blocker; it says nothing about
    # repository trust or whether a configured hook loaded.
    if (install_context == "personal"
            and rows["managed_policy_permission"].status == "unknown"):
        rows["managed_policy_permission"] = Observation(
            status="supported", source="install-context:personal",
            confidence="medium", observed_at=observed_at,
            reason="personal install has no detected organization hook policy")

    rows["native_plugin_hooks_loaded"] = _contradict_loaded(
        rows["native_plugin_hooks_loaded"],
        rows["native_plugin_hooks_installed"], "native plugin hooks",
        observed_at)
    rows["repository_bridge_loaded"] = _contradict_loaded(
        rows["repository_bridge_loaded"],
        rows["repository_bridge_configured"], "repository bridge",
        observed_at)

    policy = rows["managed_policy_permission"].status
    native = (rows["native_plugin_hooks_installed"].status == "supported"
              and rows["native_plugin_hooks_loaded"].status == "supported"
              and policy == "supported")
    bridge = (rows["repository_bridge_configured"].status == "supported"
              and rows["repository_bridge_loaded"].status == "supported"
              and rows["repository_trust"].status == "supported"
              and policy == "supported")
    if native and bridge:
        # Exactly-once claiming lands in the next slice.  Until a stable event
        # identity is proved, two loaded paths are a transition, not ready.
        effective_path = (
            "native_effective"
            if rows["stable_event_identity"].status == "supported"
            else "transitioning")
    elif native:
        effective_path = "native_effective"
    elif bridge:
        effective_path = "bridge_effective"
    elif any(row.status == "contradictory" for row in rows.values()):
        effective_path = "blocked"
    elif policy == "unsupported":
        effective_path = "blocked"
    elif (rows["repository_bridge_configured"].status == "supported"
          and rows["repository_trust"].status == "unsupported"):
        effective_path = "blocked"
    elif (rows["native_plugin_hooks_installed"].status == "supported"
          or rows["repository_bridge_configured"].status == "supported"):
        effective_path = "transitioning"
    else:
        effective_path = "blocked"

    workspace_fp = hashlib.sha256(os.path.normcase(os.path.abspath(
        ws)).encode("utf-8", errors="replace")).hexdigest()
    session_fp = (hashlib.sha256(session_id.encode("utf-8")).hexdigest()
                  if session_id else None)
    payload = {
        "schema": SCHEMA, "host": _bounded(host, 32) or "unknown",
        "host_version": _bounded(host_version, 64) or None,
        "workspace_fingerprint": workspace_fp,
        "session_fingerprint": session_fp,
        "observed_at": observed_at,
        "effective_path": effective_path,
        "capabilities": {name: row.to_dict()
                         for name, row in sorted(rows.items())},
    }
    fingerprint = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return HostCapabilitySnapshot(
        host=payload["host"], host_version=payload["host_version"],
        workspace_fingerprint=workspace_fp,
        session_fingerprint=session_fp, observed_at=observed_at,
        capabilities=MappingProxyType(dict(rows)),
        effective_path=effective_path, fingerprint=fingerprint)


def _combined_status(rows: list[Observation]) -> str:
    statuses = {row.status for row in rows}
    if "contradictory" in statuses:
        return "contradictory"
    if "supported" in statuses:
        return "supported"
    if statuses == {"unsupported"}:
        return "unsupported"
    return "unknown"


def onboarding_projection(snapshot: HostCapabilitySnapshot) -> dict[str, Any]:
    """Project the five independent onboarding facts and one safe action."""
    caps = snapshot.capabilities
    installed = [caps["native_plugin_hooks_installed"],
                 caps["repository_bridge_configured"]]
    loaded = [caps["native_plugin_hooks_loaded"],
              caps["repository_bridge_loaded"]]
    path = snapshot.effective_path
    ready = path in ("native_effective", "bridge_effective")
    policy = caps["managed_policy_permission"]
    trust = caps["repository_trust"]
    if policy.status in ("unsupported", "contradictory"):
        action = "contact_administrator"
        reason = "organization hook policy needs administrator action"
    elif (caps["repository_bridge_configured"].status == "supported"
          and trust.status in ("unsupported", "contradictory")):
        action = "review_repository_trust"
        reason = "repository trust does not permit the configured bridge"
    elif path == "transitioning":
        action = "start_new_session"
        reason = "configuration exists but this session has no effective-path receipt"
    elif path == "blocked":
        action = "install_or_enable_hooks"
        reason = "no policy-permitted loaded hook path is proved"
    else:
        action = "ready"
        reason = f"{path} is observed for this session"
    effective_status = ("supported" if ready else
                        "contradictory" if any(
                            row.status == "contradictory" for row in caps.values())
                        else "unsupported" if path == "blocked" else "unknown")
    return {
        "schema": snapshot.schema,
        "fingerprint": snapshot.fingerprint,
        "install": {
            "status": _combined_status(installed),
            "native": installed[0].to_dict(),
            "bridge": installed[1].to_dict(),
        },
        "trust": trust.to_dict(),
        "managed_policy": policy.to_dict(),
        "loaded_session": {
            "status": _combined_status(loaded),
            "native": loaded[0].to_dict(),
            "bridge": loaded[1].to_dict(),
        },
        "effective_path": {
            "status": effective_status,
            "value": path,
            "source": "derived:host-capability-snapshot",
            "confidence": "high" if ready else "low",
            "observed_at": snapshot.observed_at,
            "reason": reason,
        },
        "ready": ready,
        "next_action": action,
    }


_DISPATCH_MODES = frozenset(("default", "warn", "strict"))


def _supported_values(snapshot: HostCapabilitySnapshot, name: str) -> tuple[
        set[str] | None, str]:
    """Return an exact host-owned allowlist, never a model-authored guess."""
    row = snapshot.capabilities.get(name)
    if row is None:
        return None, f"{name} evidence is absent"
    value = _json_value(row.value)
    if row.status != "supported":
        return None, f"{name} is {row.status} ({row.source})"
    if not isinstance(value, list) or not value or not all(
            isinstance(item, str) and item.strip() for item in value):
        subject = ("model alias" if name == "supported_model_aliases"
                   else "effort value" if name == "supported_effort_values"
                   else name.replace("_", " "))
        return None, f"{subject} evidence is corrupt"
    return {item.strip() for item in value}, ""


def _provider_for_model(model: str) -> str | None:
    value = model.strip().lower()
    if (value.startswith("claude-") or "sonnet" in value
            or "opus" in value or "haiku" in value):
        return "claude"
    if (value.startswith("gpt-") or value.startswith("o3")
            or value.startswith("o4") or "codex" in value):
        return "codex"
    return None


def resolve_dispatch_route(
        snapshot: HostCapabilitySnapshot, *, tier: str,
        requested_model: str | None, requested_effort: str | None,
        mode: str = "default",
        observed: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve a tier into host-safe child arguments and an auditable state.

    A planned exact route is not marked verified until an enforcement-boundary
    receipt reports the same values. Unknown, unsupported, contradictory, or
    malformed capabilities therefore become an explicit inherit fallback (or
    a pre-dispatch block in strict mode), never unsupported tool arguments.
    """
    selected_mode = mode if mode in _DISPATCH_MODES else "default"
    host_name = "claude" if snapshot.host.startswith("claude") else snapshot.host
    planned_model = (str(requested_model).strip()
                     if requested_model not in (None, "", "inherit") else None)
    planned_effort = (str(requested_effort).strip()
                      if requested_effort not in (None, "", "inherit") else None)
    reasons: list[str] = []
    effective_model: str | None = None
    effective_effort: str | None = None

    model_control = snapshot.capabilities.get("model_selection")
    model_aliases, alias_reason = _supported_values(
        snapshot, "supported_model_aliases")
    if planned_model is not None:
        provider = _provider_for_model(planned_model)
        if provider is not None and provider != host_name:
            reasons.append(
                f"model {planned_model!r} is a foreign provider id for {snapshot.host}")
        elif model_control is None or model_control.status != "supported":
            status = model_control.status if model_control else "unknown"
            reasons.append(f"model selection is {status}")
        elif model_aliases is None:
            reasons.append(alias_reason)
        elif planned_model not in model_aliases:
            reasons.append(f"model {planned_model!r} is not in the host alias receipt")
        else:
            effective_model = planned_model

    effort_control = snapshot.capabilities.get("effort_selection")
    effort_values, effort_reason = _supported_values(
        snapshot, "supported_effort_values")
    if planned_effort is not None:
        if effort_control is None or effort_control.status != "supported":
            status = effort_control.status if effort_control else "unknown"
            reasons.append(f"reasoning effort selection is {status}")
        elif effort_values is None:
            reasons.append(effort_reason)
        elif planned_effort not in effort_values:
            reasons.append(
                f"reasoning effort {planned_effort!r} is not in the host receipt")
        else:
            effective_effort = planned_effort

    explicit_unhonored = (
        planned_model is not None and effective_model is None
        or planned_effort is not None and effective_effort is None)
    blocked = selected_mode == "strict" and explicit_unhonored
    if blocked:
        effective_model = effective_effort = None
        resolution = "blocked"
    elif explicit_unhonored:
        resolution = "unsupported_fallback"
    elif planned_model is None and planned_effort is None:
        resolution = "inherit"
    else:
        resolution = "exact"

    passed_arguments: dict[str, str] = {}
    if not blocked and effective_model is not None:
        passed_arguments["model"] = effective_model
    if not blocked and effective_effort is not None:
        passed_arguments["reasoning_effort"] = effective_effort

    receipt = observed if isinstance(observed, Mapping) else {}
    observed_model = receipt.get("model")
    observed_effort = receipt.get("reasoning_effort")
    exact_verified = bool(
        resolution == "exact" and receipt.get("host_observed") is True
        and observed_model == effective_model
        and observed_effort == effective_effort)
    sources = sorted({row.source for name, row in snapshot.capabilities.items()
                      if name in {"model_selection", "supported_model_aliases",
                                  "effort_selection", "supported_effort_values"}})
    return {
        "schema": "taskplane.dispatch-route/v1",
        "host": snapshot.host,
        "host_capability_fingerprint": snapshot.fingerprint,
        "capability_source": sources,
        "tier": tier,
        "mode": selected_mode,
        "planned_model": planned_model,
        "planned_effort": planned_effort,
        "effective_model": effective_model,
        "effective_effort": effective_effort,
        "observed_model": observed_model,
        "observed_effort": observed_effort,
        "resolution": resolution,
        "block_before_dispatch": blocked,
        "exact_route_verified": exact_verified,
        "passed_arguments": passed_arguments,
        "reason": "; ".join(reasons) if reasons else (
            "host receipt matches the exact planned route" if exact_verified
            else "exact route planned; host receipt is still required"
            if resolution == "exact" else "session model and effort inherited"),
    }
