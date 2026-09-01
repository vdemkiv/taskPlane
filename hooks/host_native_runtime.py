"""Package-owned discovery and recovery for optional host-native surfaces.

The host UI is deliberately a projection of canonical Taskplane snapshots.
This module is shipped with a fixed package-owned declaration. Claude resolves
that declaration through its plugin manifest; Codex uses the fixed bundled
path because its hook manifest schema accepts only hook configuration fields.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TASKPLANE_RUNTIME_ROOT = PLUGIN_ROOT / "taskplane"
for runtime_root in (PLUGIN_ROOT, TASKPLANE_RUNTIME_ROOT):
    if str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))

from taskplane import progress, storage
from taskplane.host_capabilities import (
    HOST_NATIVE_SURFACES,
    Observation,
    RUNTIME_RECEIPT_MAX_AGE_SECONDS,
    SurfaceSelection,
    dispatch_snapshot_from_environment,
    negotiate_host_surfaces,
    progress_surface_projection,
)
from taskplane.host_native import (
    ContradictorySnapshotError,
    HostSurfaceEvent,
    HostSurfaceSnapshot,
    native_dashboard_projection,
    ordered_snapshots,
)


PACKAGE_SCHEMA = "taskplane.host-native-package/v1"
SURFACE_ROLES = (
    "pip", "fanout", "approval", "dashboard", "carousel", "preview",
    "fallback", "artifact", "gate", "visualization", "sandbox", "hosting",
    "browser", "side_panel",
)
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "closed"})
SURFACE_CAPABILITIES = HOST_NATIVE_SURFACES
ACKNOWLEDGEMENT_SCHEMA = "taskplane.host-native-acknowledgement/v1"
PUBLISH_HEAD_SCHEMA = "taskplane.host-native-publish-head/v1"
_HOST_MANIFESTS = {
    "claude": Path(".claude-plugin/plugin.json"),
}
_BUNDLED_CONTRACT = Path("hooks/host-native.json")


def project_progress_surface(
        workspace: str, *, host: str, host_version: str | None = None,
        environment: Mapping[str, str] | None = None,
        now: float | None = None) -> dict:
    """Production output adapter for durable progress and PiP fallback."""
    env = environment if environment is not None else os.environ
    current = float(now if now is not None else time.time())
    capability_snapshot = dispatch_snapshot_from_environment(
        workspace, host=host, environment=env)
    receipt = capability_snapshot.capabilities["pip"]
    try:
        observed_at = float(receipt.observed_at)
        age = current - observed_at
    except (TypeError, ValueError):
        age = RUNTIME_RECEIPT_MAX_AGE_SECONDS + 1.0
    if age < -30.0 or age > RUNTIME_RECEIPT_MAX_AGE_SECONDS:
        receipt = Observation(
            status="stale", source=receipt.source,
            confidence="low", observed_at=receipt.observed_at,
            reason="stale")
    durable = progress.read_workspace_status(workspace, now=current)
    return progress_surface_projection(
        host=host, host_version=host_version or capability_snapshot.host_version,
        pip_observation=receipt, durable_status=durable)


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"host-native declaration must be an object: {path}")
    return value


def discover_host_native_contract(root: Path | str, host: str) -> dict:
    """Resolve the declaration through the manifest consumed by ``host``."""
    root = Path(root).resolve()
    if host == "codex":
        return discover_hook_contract(root)
    try:
        manifest_path = root / _HOST_MANIFESTS[host]
    except KeyError as exc:
        raise ValueError(f"unsupported host: {host}") from exc
    manifest = _load_json(manifest_path)
    relative = manifest.get("hostNative")
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError(f"{host} plugin manifest has no hostNative declaration")
    declaration_path = (manifest_path.parent / relative).resolve()
    if root not in declaration_path.parents:
        raise ValueError("hostNative declaration must remain inside plugin root")
    declaration = _load_json(declaration_path)
    if declaration.get("schema") != PACKAGE_SCHEMA:
        raise ValueError("unsupported host-native package schema")
    return declaration


def discover_hook_contract(root: Path | str) -> dict:
    """Compatibility wrapper for the fixed package-owned declaration."""
    root = Path(root).resolve()
    declaration_path = (root / _BUNDLED_CONTRACT).resolve()
    if root not in declaration_path.parents:
        raise ValueError("bundled host-native declaration escaped plugin root")
    declaration = _load_json(declaration_path)
    if declaration.get("schema") != PACKAGE_SCHEMA:
        raise ValueError("unsupported bundled host-native package schema")
    return declaration


def _identity(snapshot: HostSurfaceSnapshot) -> tuple[str, ...]:
    values = snapshot.values
    return (
        snapshot.workflow_id, snapshot.run_id, snapshot.target,
        snapshot.revision, str(values.get("task_id", "")),
        str(values.get("slot_id", "")),
    )


@dataclass
class HostNativeRecovery:
    """Ordered, restartable recovery for all non-authoritative host views."""

    identity: tuple[str, ...] | None = None
    audit: list[HostSurfaceEvent] = field(default_factory=list)
    projections: dict[str, dict[str, dict]] = field(default_factory=dict)
    rejections: list[dict[str, object]] = field(default_factory=list)
    terminal_sequence: int | None = None
    current_snapshot: HostSurfaceSnapshot | None = None
    _seen: set[tuple[str, str, str, int, str]] = field(default_factory=set)

    @classmethod
    def resume(
        cls,
        snapshots: Iterable[HostSurfaceSnapshot],
        *,
        selections: Mapping[str, SurfaceSelection],
    ) -> "HostNativeRecovery":
        """Rehydrate canonical history after a host process reconnects."""
        recovery = cls()
        recovery.recover(snapshots, host="resume", selections=selections)
        return recovery

    def recover(
        self,
        snapshots: Iterable[HostSurfaceSnapshot],
        *,
        host: str,
        selections: Mapping[str, SurfaceSelection],
    ) -> tuple[HostSurfaceEvent, ...]:
        """Project an unordered delivery batch while sealing one audit order."""
        accepted: list[HostSurfaceEvent] = []
        # Authenticate and disposition the complete batch before projecting
        # any candidate.  In particular, two different snapshots at one
        # sequence are contradictory; delivery order cannot choose a winner.
        for snapshot in ordered_snapshots(snapshots):
            event = self.apply(snapshot, host=host, selections=selections)
            if event is not None:
                accepted.append(event)
        return tuple(accepted)

    def apply(
        self,
        snapshot: HostSurfaceSnapshot,
        *,
        host: str,
        selections: Mapping[str, SurfaceSelection],
    ) -> HostSurfaceEvent | None:
        identity = _identity(snapshot)
        if self.identity is None:
            self.identity = identity
        elif identity != self.identity:
            raise ValueError("host-native update changed canonical identity")

        event = HostSurfaceEvent.from_snapshot(
            snapshot, event_type=snapshot.state)
        key = (event.workflow_id, event.run_id, event.revision,
               event.sequence, event.fingerprint)
        duplicate = key in self._seen
        same_sequence = bool(
            self.audit and event.sequence == self.audit[-1].sequence)
        contradictory = bool(
            same_sequence and
            event.snapshot_fingerprint != self.audit[-1].snapshot_fingerprint)
        stale = bool(self.audit and event.sequence < self.audit[-1].sequence)
        after_terminal = self.terminal_sequence is not None

        # Disposition is complete before any view can observe the candidate.
        # Rejection audit intentionally carries identity, order and fingerprint
        # only: rejected canonical values never become presentation state.
        if duplicate:
            self._reject(event, "duplicate")
            return None
        if contradictory:
            self._reject(event, "contradictory")
            raise ContradictorySnapshotError(
                "contradictory snapshots share one sequence")
        if after_terminal:
            self._reject(event, "terminal_closed")
            return None
        if stale:
            self._reject(event, "stale")
            return None

        self._seen.add(key)
        self.audit.append(event)
        self.current_snapshot = snapshot
        if snapshot.state in TERMINAL_STATES:
            self.terminal_sequence = snapshot.sequence
        self._project(snapshot, host=host, selections=selections)
        return event

    def publish_head(self) -> dict[str, object] | None:
        """Return the portable head derived from accepted canonical truth.

        This is a receipt reference, not another workflow store.  The caller's
        committed snapshot remains the authority and SessionStart obtains it
        only through ``loop_status.refresh_dashboard_snapshot``.
        """
        snapshot = self.current_snapshot
        if snapshot is None:
            return None
        canonical = snapshot.to_dict()
        values = canonical["values"]
        return {
            "schema": PUBLISH_HEAD_SCHEMA,
            "workflow_id": snapshot.workflow_id,
            "run_id": snapshot.run_id,
            "target": snapshot.target,
            "revision": snapshot.revision,
            "sequence": snapshot.sequence,
            "state": snapshot.state,
            "task_id": str(values.get("task_id") or ""),
            "slot_id": str(values.get("slot_id") or ""),
            "evidence": list(snapshot.evidence),
            "gate": values.get("gate", {}),
            "snapshot_fingerprint": snapshot.fingerprint,
            "terminal_closed": self.terminal_sequence is not None,
        }

    def switch_host(
        self,
        host: str,
        *,
        selections: Mapping[str, SurfaceSelection],
    ) -> None:
        """Project only the last accepted snapshot into a newly active host."""
        if self.current_snapshot is None:
            raise ValueError("cannot switch host before an accepted snapshot")
        self._project(self.current_snapshot, host=host, selections=selections)

    def _reject(self, event: HostSurfaceEvent, reason: str) -> None:
        self.rejections.append({
            "workflow_id": event.workflow_id,
            "run_id": event.run_id,
            "revision": event.revision,
            "sequence": event.sequence,
            "event_fingerprint": event.fingerprint,
            "reason": reason,
        })

    def _project(
        self,
        snapshot: HostSurfaceSnapshot,
        *,
        host: str,
        selections: Mapping[str, SurfaceSelection],
    ) -> None:
        host_views = self.projections.setdefault(host, {})
        for role in SURFACE_ROLES:
            capability = {
                "fanout": "visualization",
                "dashboard": "visualization",
                "preview": "side_panel",
                "fallback": "visualization",
                "artifact": "carousel",
                "gate": "approval",
            }.get(role, role)
            selection = selections[capability]
            view = snapshot.project(selection)
            view["surface_role"] = role
            view["host"] = host
            view["audit"] = [event.to_dict() for event in self.audit]
            view["delivery"] = {
                "actions_enabled": selection.selected_surface == "native",
                "safe_actions": (
                    list(snapshot.safe_actions)
                    if selection.selected_surface == "native" else []),
                "limitation": selection.limitation,
            }
            host_views[role] = view

        # Exercise the real dashboard projector for the two supported plugin
        # hosts.  The generic projection above remains the complete accessible
        # fallback and retains the canonical bytes when native UI is absent.
        if host in {"codex", "claude"}:
            native = native_dashboard_projection(snapshot, host=host)
            host_views["dashboard"]["dashboard_projection"] = native
            if selections["visualization"].selected_surface == "native":
                from taskplane import dashboard
                host_views["dashboard"]["native_surface"] = \
                    dashboard.render_native_dashboard_surface(native)


def _portable_source(value: object) -> str:
    """Keep source authority while excluding workstation-local paths."""
    text = str(value or "unknown")[:512]
    path_like = (
        os.path.isabs(text) or
        bool(re.match(r"^[A-Za-z]:[\\/]", text)) or
        text.casefold().startswith("file://") or
        bool(re.search(
            r"(?:^|[:=])(?:/Users/|/home/|/private/|[A-Za-z]:[\\/]|\\\\)",
            text))
    )
    if not path_like:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"redacted-path:sha256:{digest}"


def _expiry(observed_at: str) -> float | None:
    try:
        return float(observed_at) + RUNTIME_RECEIPT_MAX_AGE_SECONDS
    except (TypeError, ValueError):
        return None


def _host_acknowledgement(
        snapshot: HostSurfaceSnapshot, *, host: str,
        selections: Mapping[str, SurfaceSelection],
        publish_head: Mapping[str, object], source_mode: str) -> dict:
    capabilities = {}
    for name, selection in sorted(selections.items()):
        capabilities[name] = {
            "surface": selection.surface,
            "status": selection.status,
            "source": _portable_source(selection.source),
            "freshness": selection.freshness,
            "observed_at": selection.observed_at,
            "expires_at": _expiry(selection.observed_at),
            "selected_surface": selection.selected_surface,
            "limitation": selection.limitation,
            "fallback": selection.fallback,
        }
    acknowledgement = {
        "schema": ACKNOWLEDGEMENT_SCHEMA,
        "host": _portable_source(host),
        "source_mode": _portable_source(source_mode),
        "identity": {
            "workflow_id": snapshot.workflow_id,
            "run_id": snapshot.run_id,
            "target": snapshot.target,
            "revision": snapshot.revision,
            "task_id": str(snapshot.values.get("task_id") or ""),
            "slot_id": str(snapshot.values.get("slot_id") or ""),
        },
        "sequence": snapshot.sequence,
        "snapshot_fingerprint": snapshot.fingerprint,
        "evidence": list(snapshot.evidence),
        "gate": snapshot.to_dict()["values"].get("gate", {}),
        "current_head": dict(publish_head),
        "capabilities": capabilities,
    }
    payload = json.dumps(
        acknowledgement, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")
    acknowledgement["fingerprint"] = hashlib.sha256(payload).hexdigest()
    return acknowledgement


def _committed_snapshot(value: Mapping[str, object]) \
        -> tuple[HostSurfaceSnapshot, HostSurfaceEvent]:
    snapshot_value = value.get("snapshot")
    event_value = value.get("event")
    if isinstance(snapshot_value, HostSurfaceSnapshot):
        snapshot = HostSurfaceSnapshot.from_dict(snapshot_value.to_dict())
    elif isinstance(snapshot_value, Mapping):
        snapshot = HostSurfaceSnapshot.from_dict(snapshot_value)
    else:
        raise ValueError("dashboard refresh has no authenticated snapshot")
    if isinstance(event_value, HostSurfaceEvent):
        event = HostSurfaceEvent.from_dict(event_value.to_dict())
    elif isinstance(event_value, Mapping):
        event = HostSurfaceEvent.from_dict(event_value)
    else:
        raise ValueError("dashboard refresh has no authenticated event")
    if (
        event.workflow_id != snapshot.workflow_id or
        event.run_id != snapshot.run_id or
        event.revision != snapshot.revision or
        event.sequence != snapshot.sequence or
        event.snapshot_fingerprint != snapshot.fingerprint
    ):
        raise ValueError("dashboard event does not bind the supplied snapshot")
    return snapshot, event


def project_committed_dashboard(
        refresh: Mapping[str, object], *, host: str,
        selections: Mapping[str, SurfaceSelection],
        recovery: HostNativeRecovery | None = None) -> dict:
    """Apply one callback-owned snapshot to real native/fallback surfaces."""
    if not isinstance(refresh, Mapping):
        raise TypeError("dashboard refresh result must be a mapping")
    snapshot, committed_event = _committed_snapshot(refresh)
    active = recovery if recovery is not None else HostNativeRecovery()
    accepted = active.apply(snapshot, host=host, selections=selections)
    status = "published"
    if accepted is None:
        current = active.current_snapshot
        if current is not None and current.fingerprint == snapshot.fingerprint:
            # A replay or host handoff projects the exact committed head again
            # without adding an audit event or allocating a sequence.
            active.switch_host(host, selections=selections)
            status = "republished"
        else:
            status = "rejected"
    elif bool(refresh.get("replayed")):
        status = "republished"

    publish_head = active.publish_head()
    if publish_head is None:
        raise ValueError("accepted dashboard projection has no publish head")
    acknowledgement = None
    if status != "rejected":
        acknowledgement = _host_acknowledgement(
            snapshot, host=host, selections=selections,
            publish_head=publish_head,
            source_mode=str(refresh.get("source_mode") or "unknown"))
    return {
        "schema": "taskplane.host-native-publication/v1",
        "status": status,
        "event": committed_event.to_dict(),
        "publish_head": publish_head,
        "acknowledgement": acknowledgement,
        "projections": active.projections.get(host, {}),
        "rejections": list(active.rejections),
        "recovery": active,
    }


def _surface_selections(
        workspace: str, *, host: str, environment: Mapping[str, str]
        ) -> Mapping[str, SurfaceSelection]:
    capability_snapshot = dispatch_snapshot_from_environment(
        workspace, host=host, environment=environment)
    return negotiate_host_surfaces(
        host=host, host_version=capability_snapshot.host_version,
        observations=capability_snapshot.capabilities)


def _phase_graph_projection(workspace: str, state=None, **kwargs) -> dict:
    """Project package-qualified Design/Plan graphs without dashboard imports."""
    from taskplane import plan_topology
    return plan_topology.phase_graph_projection(
        workspace, state, **kwargs)


def _configured_loop_status():
    """Compose the exact package module used by SessionStart recovery."""
    from taskplane import loop_status
    loop_status.configure_phase_graph_projector(_phase_graph_projection)
    return loop_status


def recover_session_dashboard(
        workspace: str, *, host: str,
        environment: Mapping[str, str] | None = None,
        selections: Mapping[str, SurfaceSelection] | None = None,
        recovery: HostNativeRecovery | None = None) -> dict:
    """Republish the callback-owned committed head during SessionStart."""
    loop_status = _configured_loop_status()
    from taskplane.settings import load_settings
    settings = load_settings()
    refresh_policy = settings.dashboard.refresh
    refresh = loop_status.refresh_dashboard_snapshot(
        workspace, event_type=refresh_policy.session_event,
        replay=refresh_policy.replay_on_session_start)
    if isinstance(refresh, Mapping) and refresh.get("status") == "no_active":
        if refresh.get("snapshot") is not None or refresh.get("event") is not None:
            raise ValueError("no-active dashboard replay carried canonical state")
        return {
            "schema": "taskplane.host-native-publication/v1",
            "status": "no_active",
            "event": None,
            "publish_head": None,
            "acknowledgement": None,
            "projections": {},
            "rejections": [],
            "recovery": recovery if recovery is not None
            else HostNativeRecovery(),
        }
    chosen = selections or _surface_selections(
        workspace, host=host,
        environment=environment if environment is not None else os.environ)
    return project_committed_dashboard(
        refresh, host=host, selections=chosen, recovery=recovery)


def _main(argv: list[str]) -> int:
    """Fail closed when a packaged hook cannot resolve its declaration."""
    from taskplane.settings import load_settings
    load_settings()
    if len(argv) != 3 or argv[0] != "check" or argv[1] != "--host":
        raise SystemExit("usage: host_native_runtime.py check --host HOST")
    host = argv[2]
    host_contract = discover_host_native_contract(PLUGIN_ROOT, host)
    hook_contract = discover_hook_contract(PLUGIN_ROOT)
    if host_contract != hook_contract:
        raise ValueError("plugin and hook host-native contracts differ")
    workspace = os.environ.get("TASKPLANE_WORKSPACE") or os.getcwd()
    storage.bind_hook_taskplane_home(workspace, os.environ)
    # Exercise the optional output surface on every adapter start. Missing or
    # stale capability evidence intentionally produces the complete accessible
    # fallback; it never turns native UI into workflow authority.
    project_progress_surface(
        workspace, host=host,
        host_version=os.environ.get("TASKPLANE_HOST_VERSION"))
    # SessionStart replays only the canonical callback-owned head.  Older
    # compatible installations lack the callback; once present, no-active is
    # an ordinary fresh-install outcome while corrupt/ambiguous state arrives
    # as an explicit action-disabled snapshot and is still projected.
    loop_status = _configured_loop_status()
    if callable(getattr(loop_status, "refresh_dashboard_snapshot", None)):
        recover_session_dashboard(workspace, host=host)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
