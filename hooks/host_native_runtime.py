"""Package-owned discovery and recovery for optional host-native surfaces.

The host UI is deliberately a projection of canonical Taskplane snapshots.
This module is shipped through both plugin manifests and the hook manifest so
reconnect and host-switch behavior is shared instead of being reimplemented by
tests or individual host adapters.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from taskplane.host_capabilities import SurfaceSelection
from taskplane.host_native import HostSurfaceEvent, HostSurfaceSnapshot


PACKAGE_SCHEMA = "taskplane.host-native-package/v1"
SURFACE_ROLES = (
    "pip", "fanout", "approval", "dashboard", "carousel", "preview",
    "fallback", "artifact", "gate",
)
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "closed"})
_HOST_MANIFESTS = {
    "codex": Path(".codex-plugin/plugin.json"),
    "claude": Path(".claude-plugin/plugin.json"),
}


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"host-native declaration must be an object: {path}")
    return value


def discover_host_native_contract(root: Path | str, host: str) -> dict:
    """Resolve the declaration through the manifest consumed by ``host``."""
    root = Path(root).resolve()
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
    """Resolve the same declaration through Claude's runtime hook manifest."""
    root = Path(root).resolve()
    manifest_path = root / "hooks/hooks.json"
    manifest = _load_json(manifest_path)
    relative = manifest.get("hostNative")
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("hook manifest has no hostNative declaration")
    declaration_path = (manifest_path.parent / relative).resolve()
    if declaration_path.parent != manifest_path.parent.resolve():
        raise ValueError("hook declaration must remain inside hooks")
    declaration = _load_json(declaration_path)
    if declaration.get("schema") != PACKAGE_SCHEMA:
        raise ValueError("unsupported hook host-native package schema")
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
        for snapshot in sorted(snapshots, key=lambda item: item.sequence):
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
        stale = bool(self.audit and event.sequence <= self.audit[-1].sequence)
        after_terminal = self.terminal_sequence is not None

        # Disposition is complete before any view can observe the candidate.
        # Rejection audit intentionally carries identity, order and fingerprint
        # only: rejected canonical values never become presentation state.
        if duplicate:
            self._reject(event, "duplicate")
            return None
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
            host_views[role] = view


def _main(argv: list[str]) -> int:
    """Fail closed when a packaged hook cannot resolve its declaration."""
    if len(argv) != 3 or argv[0] != "check" or argv[1] != "--host":
        raise SystemExit("usage: host_native_runtime.py check --host HOST")
    host = argv[2]
    host_contract = discover_host_native_contract(PLUGIN_ROOT, host)
    hook_contract = discover_hook_contract(PLUGIN_ROOT)
    if host_contract != hook_contract:
        raise ValueError("plugin and hook host-native contracts differ")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
