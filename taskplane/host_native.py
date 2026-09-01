"""Canonical, host-neutral records for native workflow surfaces.

The records in this module are presentation inputs, not a second workflow
authority.  They are immutable, content addressed, and retain the complete
canonical value/evidence/action set when a host has to use a fallback.
"""

from __future__ import annotations

import base64
import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import html
import hmac
from html.parser import HTMLParser
import json
import ctypes
import ctypes.util
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence, TYPE_CHECKING

try:
    from . import wave_metrics
except ImportError:  # pragma: no cover - direct module loading
    import wave_metrics  # type: ignore

if TYPE_CHECKING:
    from .host_capabilities import SurfaceSelection
else:
    if __package__:
        from . import host_capabilities as _host_capabilities
    else:  # direct CLI/module execution
        import host_capabilities as _host_capabilities
    SurfaceSelection = _host_capabilities.SurfaceSelection


SNAPSHOT_SCHEMA = "taskplane.host-surface-snapshot/v1"
EVENT_SCHEMA = "taskplane.host-surface-event/v1"
REVISION_ID_KEYS = (
    "target_fingerprint", "context_fingerprint", "findings_fingerprint",
    "canonical_revision",
)


class ContradictorySnapshotError(ValueError):
    """Two snapshots claim different canonical truth at one sequence."""


def process_start_identity(pid: int) -> str:
    """Return an immutable OS start identity for PID-reuse protection."""
    proc_stat = Path(f"/proc/{int(pid)}/stat")
    if proc_stat.is_file():
        fields = proc_stat.read_text(encoding="utf-8").split()
        if len(fields) > 21:
            return f"linux-proc:{fields[21]}"
    if sys.platform == "darwin":
        library = ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
        libproc = ctypes.CDLL(library, use_errno=True)
        buffer = ctypes.create_string_buffer(256)
        size = int(libproc.proc_pidinfo(
            int(pid), 3, 0, ctypes.byref(buffer), ctypes.sizeof(buffer)))
        if size >= 136:
            return "darwin-start:" + buffer.raw[120:136].hex()
    raise OSError("process start identity is unavailable")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item)
                                 for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class HostSurfaceSnapshot:
    """One immutable semantic view shared by every host presentation."""

    workflow_id: str
    run_id: str
    target: str
    revision: str
    sequence: int
    stage: str
    state: str
    values: Mapping[str, Any]
    evidence: tuple[str, ...]
    safe_actions: tuple[str, ...]
    fingerprint: str
    schema: str = SNAPSHOT_SCHEMA

    @classmethod
    def create(
        cls,
        *,
        workflow_id: str,
        run_id: str,
        target: str,
        revision: str,
        sequence: int,
        stage: str,
        state: str,
        values: Mapping[str, Any],
        evidence: Sequence[str] = (),
        safe_actions: Sequence[str] = (),
    ) -> "HostSurfaceSnapshot":
        if not all(str(item).strip() for item in
                   (workflow_id, run_id, target, revision, stage, state)):
            raise ValueError("canonical snapshot identity fields are required")
        if (isinstance(sequence, bool) or not isinstance(sequence, int)
                or sequence < 0):
            raise ValueError("sequence must be a non-negative integer")
        frozen_values = _freeze(values)
        frozen_evidence = tuple(str(item) for item in evidence)
        frozen_actions = tuple(str(item) for item in safe_actions)
        payload = {
            "schema": SNAPSHOT_SCHEMA,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "target": target,
            "revision": revision,
            "sequence": sequence,
            "stage": stage,
            "state": state,
            "values": _plain(frozen_values),
            "evidence": list(frozen_evidence),
            "safe_actions": list(frozen_actions),
        }
        return cls(
            workflow_id=workflow_id,
            run_id=run_id,
            target=target,
            revision=revision,
            sequence=sequence,
            stage=stage,
            state=state,
            values=frozen_values,
            evidence=frozen_evidence,
            safe_actions=frozen_actions,
            fingerprint=_fingerprint(payload),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HostSurfaceSnapshot":
        """Rehydrate and authenticate persisted v1 canonical bytes."""
        if value.get("schema") != SNAPSHOT_SCHEMA:
            raise ValueError("unsupported host-surface snapshot schema")
        expected_fields = {
            "schema", "workflow_id", "run_id", "target", "revision",
            "sequence", "stage", "state", "values", "evidence",
            "safe_actions", "fingerprint",
        }
        if set(value) != expected_fields:
            raise ValueError(
                "host-surface snapshot fields are incomplete or unknown"
            )
        fingerprint = value.get("fingerprint")
        if not isinstance(fingerprint, str):
            raise ValueError("host-surface snapshot fingerprint is required")
        try:
            snapshot = cls.create(
                workflow_id=value["workflow_id"],
                run_id=value["run_id"],
                target=value["target"],
                revision=value["revision"],
                sequence=value["sequence"],
                stage=value["stage"],
                state=value["state"],
                values=value["values"],
                evidence=value["evidence"],
                safe_actions=value["safe_actions"],
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("invalid host-surface snapshot") from exc
        if not hmac.compare_digest(snapshot.fingerprint, fingerprint):
            raise ValueError("host-surface snapshot fingerprint mismatch")
        return snapshot

    @property
    def generated_at(self) -> str | None:
        """Return the committed event time, absent only on historical v1 data."""
        value = self.values.get("generated_at")
        return value if isinstance(value, str) else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "target": self.target,
            "revision": self.revision,
            "sequence": self.sequence,
            "stage": self.stage,
            "state": self.state,
            "values": _plain(self.values),
            "evidence": list(self.evidence),
            "safe_actions": list(self.safe_actions),
            "fingerprint": self.fingerprint,
        }

    def project(self, selection: SurfaceSelection) -> dict[str, Any]:
        """Pair canonical truth with a negotiated, non-authoritative view."""
        presentation = selection.to_dict()
        presentation["kind"] = (
            selection.selected_surface if selection.selected_surface == "native"
            else selection.fallback
        )
        presentation["reason"] = (
            "available" if selection.selected_surface == "native"
            else "unavailable"
        )
        # Unavailable host functionality must never be reported as a choice.
        presentation["user_declined"] = False
        presentation["safe_actions"] = list(self.safe_actions)
        return {"canonical": self.to_dict(), "presentation": presentation}


@dataclass(frozen=True)
class HostSurfaceEvent:
    """Ordered content-addressed notification referencing a snapshot."""

    workflow_id: str
    run_id: str
    revision: str
    sequence: int
    event_type: str
    snapshot_fingerprint: str
    fingerprint: str
    schema: str = EVENT_SCHEMA

    @classmethod
    def from_snapshot(
        cls, snapshot: HostSurfaceSnapshot, *, event_type: str
    ) -> "HostSurfaceEvent":
        if not str(event_type).strip():
            raise ValueError("event_type is required")
        payload = {
            "schema": EVENT_SCHEMA,
            "workflow_id": snapshot.workflow_id,
            "run_id": snapshot.run_id,
            "revision": snapshot.revision,
            "sequence": snapshot.sequence,
            "event_type": event_type,
            "snapshot_fingerprint": snapshot.fingerprint,
        }
        return cls(
            workflow_id=snapshot.workflow_id,
            run_id=snapshot.run_id,
            revision=snapshot.revision,
            sequence=snapshot.sequence,
            event_type=event_type,
            snapshot_fingerprint=snapshot.fingerprint,
            fingerprint=_fingerprint(payload),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HostSurfaceEvent":
        """Rehydrate and authenticate a persisted v1 event reference."""
        if value.get("schema") != EVENT_SCHEMA:
            raise ValueError("unsupported host-surface event schema")
        expected_fields = {
            "schema", "workflow_id", "run_id", "revision", "sequence",
            "event_type", "snapshot_fingerprint", "fingerprint",
        }
        if set(value) != expected_fields:
            raise ValueError(
                "host-surface event fields are incomplete or unknown"
            )
        fingerprint = value.get("fingerprint")
        if not isinstance(fingerprint, str):
            raise ValueError("host-surface event fingerprint is required")
        payload = {key: value[key] for key in expected_fields - {"fingerprint"}}
        if not hmac.compare_digest(_fingerprint(payload), fingerprint):
            raise ValueError("host-surface event fingerprint mismatch")
        try:
            sequence = value["sequence"]
            if (isinstance(sequence, bool) or not isinstance(sequence, int)
                    or sequence < 0):
                raise ValueError("sequence must be a non-negative integer")
            if not all(str(value[key]).strip() for key in (
                    "workflow_id", "run_id", "revision", "event_type",
                    "snapshot_fingerprint")):
                raise ValueError("canonical event fields are required")
            return cls(
                workflow_id=value["workflow_id"],
                run_id=value["run_id"],
                revision=value["revision"],
                sequence=sequence,
                event_type=value["event_type"],
                snapshot_fingerprint=value["snapshot_fingerprint"],
                fingerprint=fingerprint,
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("invalid host-surface event") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "revision": self.revision,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "fingerprint": self.fingerprint,
        }


def ordered_snapshots(
    snapshots: Iterable[HostSurfaceSnapshot],
) -> tuple[HostSurfaceSnapshot, ...]:
    """Return one deterministic v1 history or reject contradictory order.

    Exact duplicates are idempotent. A sequence may never name two different
    fingerprints for the same stable identity; consumers must reject that
    contradiction before projecting either candidate.
    """
    by_sequence: dict[int, HostSurfaceSnapshot] = {}
    identity: tuple[str, str, str, str] | None = None
    for snapshot in snapshots:
        # Public dataclass construction cannot bypass persisted-byte integrity.
        authenticated = HostSurfaceSnapshot.from_dict(snapshot.to_dict())
        candidate_identity = (
            authenticated.workflow_id,
            authenticated.run_id,
            authenticated.target,
            authenticated.revision,
        )
        if identity is None:
            identity = candidate_identity
        elif candidate_identity != identity:
            raise ValueError("host-surface snapshot identity changed")
        previous = by_sequence.get(authenticated.sequence)
        if (previous is not None
                and previous.fingerprint != authenticated.fingerprint):
            raise ContradictorySnapshotError(
                "contradictory snapshots share one sequence"
            )
        by_sequence.setdefault(authenticated.sequence, authenticated)
    return tuple(by_sequence[key] for key in sorted(by_sequence))

LARGE_DASHBOARD_INLINE_BYTES = 64 * 1024
_CANONICAL_START = "<!-- taskplane-canonical-json:start -->"
_CANONICAL_END = "<!-- taskplane-canonical-json:end -->"
_DASHBOARD_GRAPH_KEYS = (
    "design_graph", "plan_task_dag", "plan_waves", "module_impact",
)
_DASHBOARD_HEAD_IDENTITY_KEYS = (
    "workflow_id", "run_id", "target", "revision",
)
_NO_EXPECTED_HEAD = object()


def canonical_dashboard_bytes(model: Mapping[str, Any]) -> bytes:
    """Canonical JSON is the machine authority for every delivery surface."""
    if not isinstance(model, Mapping):
        raise TypeError("dashboard model must be a mapping")
    return json.dumps(
        dict(model), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")


def _write_delivery_artifact(path: str, payload: bytes) -> None:
    target = os.path.abspath(path)
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    if os.path.lexists(target) and os.path.islink(target):
        raise ValueError("dashboard artifact path must not be a symlink")
    fd, temporary = tempfile.mkstemp(prefix=".dashboard-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _artifact_ref(path: str, payload: bytes) -> dict[str, Any]:
    return {"status": "available", "path": path, "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest()}


def _fingerprint_value(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_dashboard_bytes(value)).hexdigest()


class _HtmlShape(HTMLParser):
    """Count document boundaries without treating script text as markup."""

    def __init__(self) -> None:
        super().__init__()
        self.doctypes = 0
        self.tags = {"html": 0, "head": 0, "body": 0}

    def handle_decl(self, decl: str) -> None:
        if decl.casefold().strip() == "doctype html":
            self.doctypes += 1

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag in self.tags:
            self.tags[tag] += 1


def _html_shape(document: str) -> _HtmlShape:
    parser = _HtmlShape()
    parser.feed(document)
    return parser


def _disable_unverified_actions(fragment: str) -> str:
    """Make mutation/approval controls inert before any script executes."""
    action = re.compile(
        r"<button\b(?=[^>]*\bdata-dashboard-action(?:\s|=|>))[^>]*>",
        re.IGNORECASE)

    def closed(match: re.Match[str]) -> str:
        tag = match.group(0)
        inspection = re.search(
            r"\bdata-action-kind\s*=\s*(['\"])inspection\1", tag,
            re.IGNORECASE)
        named = re.search(
            r"\bdata-dashboard-action\s*=\s*(['\"])([^'\"]+)\1", tag,
            re.IGNORECASE)
        if inspection or (named and named.group(2).casefold() in {
                "inspect", "view", "details", "export"}):
            return tag
        if re.search(r"\bdisabled(?:\s|=|>)", tag, re.IGNORECASE):
            return tag
        return tag[:-1] + ' disabled aria-disabled="true">'

    return action.sub(closed, fragment)


def _dashboard_freshness_controller(rendered_head: Mapping[str, Any],
                                    *, actions_enabled: bool) -> str:
    encoded_head = base64.b64encode(canonical_dashboard_bytes(
        rendered_head)).decode("ascii")
    initial = "fresh" if actions_enabled else "unverified"
    # The old document never enables itself from a newer head.  It navigates
    # to the content-addressed generation, whose own controller must then
    # prove an exact head match.  file:// never attempts network fetch.
    return (
        '<script>(function(){'
        'var root=document.body,rendered=JSON.parse(atob("' + encoded_head + '"));'
        'var wasStale=false;'
        'function mutations(){return Array.from(document.querySelectorAll('
        '"[data-dashboard-action]"))'
        '.filter(function(item){var kind=(item.getAttribute('
        '"data-action-kind")||item.getAttribute("data-dashboard-action")||"")'
        '.toLowerCase();return !["inspect","view","details","export",'
        '"inspection"].includes(kind);});}'
        'function state(name,reason,enabled){root.dataset.dashboardFreshness=name;'
        'root.dataset.dashboardFreshnessReason=reason||"";var notice='
        'document.getElementById("tp-dashboard-freshness-status");if(notice){'
        'notice.dataset.status=name;notice.textContent="Dashboard "+name+": "+'
        '(reason||"status unavailable");}mutations().forEach('
        'function(item){item.disabled=!enabled;item.setAttribute("aria-disabled",'
        'enabled?"false":"true");});}'
        'function sameIdentity(head){return ["workflow_id","run_id","target",'
        '"revision"].every(function(key){return String(head[key]||"")==='
        'String(rendered[key]||"");});}'
        'function apply(head){if(!head||!sameIdentity(head)){wasStale=true;'
        'state("stale","dashboard head identity is missing or changed",false);'
        'return false;}var next=Number(head.sequence),here=Number(rendered.sequence);'
        'if(next>here){wasStale=true;state("stale",'
        '"durable dashboard head is newer than this page",false);'
        'if(head.html_href&&window.location&&typeof window.location.replace==="function")'
        '{window.location.replace(head.html_href);}return false;}'
        'if(next!==here||head.snapshot_fingerprint!==rendered.snapshot_fingerprint)'
        '{wasStale=true;state("stale","dashboard head is contradictory",false);'
        'return false;}if(wasStale){state("stale",'
        '"a stale document requires a newer rendered snapshot",false);return false;}'
        'state("fresh","exact durable head verified",true);return true;}'
        'window.taskplaneDashboardApplyHead=apply;'
        'state("' + initial + '","' +
        ('embedded host acknowledgement verified' if actions_enabled else
         'dashboard head has not been verified') + '",' +
        ("true" if actions_enabled else "false") + ');'
        'var bridge=window.openai&&typeof window.openai.getDashboardHead==="function";'
        'if(bridge){Promise.resolve(window.openai.getDashboardHead()).then(apply,'
        'function(){state("unverified","trusted head bridge failed",false);});}'
        'else if(window.location&&window.location.protocol!=="file:"&&'
        'typeof window.fetch==="function"){window.fetch("../../current.json",'
        '{cache:"no-store",credentials:"same-origin"}).then(function(response){'
        'if(!response.ok)throw new Error("head unavailable");return response.json()'
        '.then(function(head){if(head.html_href&&typeof URL==="function")'
        '{head.html_href=new URL(head.html_href,response.url).href;}return head;});})'
        '.then(apply,function(){state("unverified",'
        '"durable dashboard head could not be fetched",false);});}'
        'else{state("unverified",window.location&&window.location.protocol==="file:"?'
        '"file dashboard has no trusted head bridge; network refresh is not attempted":'
        '"dashboard head transport is unavailable",false);}'
        '})();</script>')


def _embedded_html(body: str, canonical: bytes, *,
                   rendered_head: Mapping[str, Any],
                   actions_enabled: bool,
                   stylesheet: str | None = None) -> bytes:
    fragment_shape = _html_shape(body)
    if fragment_shape.doctypes or any(fragment_shape.tags.values()):
        raise ValueError(
            "HTML renderer must return a fragment, not a document boundary")
    if not actions_enabled:
        body = _disable_unverified_actions(body)
    css = str(stylesheet or "")
    if "</style" in css.casefold():
        raise ValueError("dashboard stylesheet must not close its style element")
    style = f"<style>{css}</style>" if css else ""
    encoded = base64.b64encode(canonical).decode("ascii")
    document = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Taskplane dashboard</title>' + style + '</head><body '
        'data-dashboard-delivery-root="true" data-dashboard-freshness="'
        + ("fresh" if actions_enabled else "unverified") + '">'
        + '<div id="tp-dashboard-freshness-status" role="status" '
          'aria-live="polite" data-status="'
        + ("fresh" if actions_enabled else "unverified") + '">Dashboard '
        + ("fresh: embedded host acknowledgement verified" if actions_enabled
           else "unverified: dashboard head has not been verified") + '</div>'
        + body
        + '<script type="application/x-taskplane-json-base64" '
          f'data-taskplane-canonical="true">{encoded}</script>'
        + _dashboard_freshness_controller(
            rendered_head, actions_enabled=actions_enabled)
        + '</body></html>')
    shape = _html_shape(document)
    if shape.doctypes != 1 or shape.tags != {
            "html": 1, "head": 1, "body": 1}:
        raise ValueError("dashboard delivery must contain exactly one document")
    return document.encode("utf-8")


def _normalize_delivery_head(
        value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("dashboard head must be a mapping")
    try:
        sequence = int(value["sequence"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("dashboard head sequence is required") from exc
    if sequence < 0:
        raise ValueError("dashboard head sequence is invalid")
    head = {key: str(value.get(key) or "")
            for key in _DASHBOARD_HEAD_IDENTITY_KEYS}
    if not all(head.values()):
        raise ValueError("dashboard head identity is incomplete")
    fingerprint = str(value.get("snapshot_fingerprint") or
                      value.get("fingerprint") or "")
    if not fingerprint:
        raise ValueError("dashboard head fingerprint is required")
    return {**head, "sequence": sequence,
            "snapshot_fingerprint": fingerprint}


def dashboard_freshness_state(
        rendered_head: Mapping[str, Any],
        current_head: Mapping[str, Any] | None, *,
        page_url: str, bridge_available: bool, fetch_available: bool,
        previously_stale: bool = False,
        previous_rendered_sequence: int | None = None) -> dict[str, Any]:
    """Return the fail-closed action state for one open dashboard page."""
    rendered = _normalize_delivery_head(rendered_head)
    base = {"rendered_sequence": rendered["sequence"]}
    is_file = str(page_url).casefold().startswith("file:")
    if is_file and not bridge_available:
        return {
            "status": "unverified", "actions_enabled": False,
            "reason": "file dashboard has no trusted head bridge; network "
                      "refresh is not attempted", **base,
        }
    if not bridge_available and not fetch_available:
        return {
            "status": "unverified", "actions_enabled": False,
            "reason": "dashboard head transport is unavailable", **base,
        }
    if current_head is None:
        return {
            "status": "unverified", "actions_enabled": False,
            "reason": "durable dashboard head is unavailable", **base,
        }
    try:
        current = _normalize_delivery_head(current_head)
    except ValueError as exc:
        return {"status": "unverified", "actions_enabled": False,
                "reason": str(exc), **base}
    result_base = {**base, "current_sequence": current["sequence"]}
    if any(rendered[key] != current[key]
           for key in _DASHBOARD_HEAD_IDENTITY_KEYS):
        return {"status": "stale", "actions_enabled": False,
                "reason": "dashboard head identity changed", **result_base}
    if current["sequence"] > rendered["sequence"]:
        return {"status": "stale", "actions_enabled": False,
                "reason": "durable dashboard head is newer than this page",
                **result_base}
    if (current["sequence"] != rendered["sequence"] or
            current["snapshot_fingerprint"] !=
            rendered["snapshot_fingerprint"]):
        return {"status": "stale", "actions_enabled": False,
                "reason": "dashboard head is stale or contradictory",
                **result_base}
    if previously_stale and (previous_rendered_sequence is None or
                             rendered["sequence"] <=
                             previous_rendered_sequence):
        return {"status": "stale", "actions_enabled": False,
                "reason": "a stale page requires a newer rendered snapshot",
                **result_base}
    return {"status": "fresh", "actions_enabled": True,
            "reason": "exact durable head verified", **result_base}


def dashboard_publication_receipt_fingerprint(
        receipt: Mapping[str, Any]) -> str:
    """Authenticate a receipt after removing only its self fingerprint."""
    payload = dict(receipt)
    payload.pop("fingerprint", None)
    return _fingerprint_value(payload)


def _lowercase_digest(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(
        character in "0123456789abcdef" for character in value)


def _snapshot_receipt(
        model: Mapping[str, Any], canonical_sha256: str) -> dict[str, Any]:
    identity_value = model.get("identity")
    identity: Mapping[str, Any] = (
        identity_value if isinstance(identity_value, Mapping) else {})
    values_value = model.get("values")
    values: Mapping[str, Any] = (
        values_value if isinstance(values_value, Mapping) else model)
    sequence = model.get("sequence", identity.get("sequence", 0))
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        sequence = 0
    revision = model.get("revision", identity.get("revision", ""))
    return {
        "fingerprint": str(model.get("fingerprint") or canonical_sha256),
        "sequence": sequence,
        "revision": str(revision or ""),
        "generated_at": values.get("generated_at"),
        "canonical_sha256": canonical_sha256,
        "candidate_sha": values.get("candidate_sha"),
    }


def _candidate_receipt(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    candidate = {
        "source_sha": snapshot.get("candidate_sha"),
        "snapshot_fingerprint": snapshot.get("fingerprint"),
        "canonical_sha256": snapshot.get("canonical_sha256"),
    }
    candidate["fingerprint"] = _fingerprint_value(candidate)
    return candidate


def validate_dashboard_publication_receipt(
        receipt: Mapping[str, Any], *, current_head: Mapping[str, Any],
        expected_source_sha: str) -> dict[str, Any]:
    """Return release evidence only for the exact durable dashboard head."""
    receipt_fields = {
        "schema", "snapshot", "candidate", "graphs", "dom_freshness",
        "host_acknowledgement", "generation", "bindings", "fingerprint",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != receipt_fields or \
            receipt.get("schema") != \
            "taskplane.dashboard-publication-receipt/v1" or \
            receipt.get("fingerprint") != \
            dashboard_publication_receipt_fingerprint(receipt):
        raise ValueError("dashboard publication receipt is invalid")
    if not _lowercase_digest(expected_source_sha, 40):
        raise ValueError("dashboard expected source SHA is invalid")
    snapshot = receipt.get("snapshot")
    if not isinstance(snapshot, Mapping) or set(snapshot) != {
            "fingerprint", "sequence", "revision", "generated_at",
            "canonical_sha256", "candidate_sha"} or \
            not _lowercase_digest(snapshot.get("fingerprint"), 64) or \
            not _lowercase_digest(snapshot.get("canonical_sha256"), 64) or \
            snapshot.get("candidate_sha") != expected_source_sha:
        raise ValueError("dashboard snapshot names another candidate")
    candidate = receipt.get("candidate")
    if not isinstance(candidate, Mapping) or set(candidate) != {
            "source_sha", "snapshot_fingerprint", "canonical_sha256",
            "fingerprint"} or \
            candidate.get("source_sha") != expected_source_sha or \
            candidate.get("snapshot_fingerprint") != snapshot["fingerprint"] or \
            candidate.get("canonical_sha256") != snapshot["canonical_sha256"] or \
            candidate.get("fingerprint") != _fingerprint_value({
                key: candidate[key] for key in candidate
                if key != "fingerprint"}):
        raise ValueError("dashboard candidate identity is invalid")
    graphs = receipt.get("graphs")
    if not isinstance(graphs, Mapping) or any(
            not _lowercase_digest(value, 64) for value in graphs.values()):
        raise ValueError("dashboard graph bindings are invalid")
    dom = receipt.get("dom_freshness")
    if not isinstance(dom, Mapping) or set(dom) != {
            "status", "html_document_count", "canonical_sha256",
            "actions_enabled", "fingerprint"} or \
            dom.get("status") != "verified" or \
            dom.get("html_document_count") != 1 or \
            dom.get("canonical_sha256") != snapshot["canonical_sha256"] or \
            dom.get("fingerprint") != _fingerprint_value({
                key: dom[key] for key in dom if key != "fingerprint"}):
        raise ValueError("dashboard DOM freshness is invalid")
    generation = receipt.get("generation")
    host = receipt.get("host_acknowledgement")
    if not isinstance(generation, Mapping) or set(generation) != {
            "id", "artifacts", "complete"} or \
            generation.get("complete") is not True or \
            not isinstance(generation.get("artifacts"), Mapping) or \
            generation.get("id") != _fingerprint_value({
                "artifacts": generation["artifacts"],
                "host_acknowledgement": (
                    host.get("fingerprint") if isinstance(host, Mapping)
                    else None),
            }):
        raise ValueError("dashboard generation identity is invalid")
    bindings = receipt.get("bindings")
    if bindings != {
            "snapshot": snapshot["fingerprint"],
            "candidate": candidate["fingerprint"],
            "graphs": dict(graphs),
            "dom_freshness": dom["fingerprint"],
            "host_acknowledgement": (
                host.get("fingerprint") if isinstance(host, Mapping)
                else None)}:
        raise ValueError("dashboard receipt bindings are severed")
    head_fields = {
        "schema", *_DASHBOARD_HEAD_IDENTITY_KEYS, "sequence",
        "snapshot_fingerprint", "candidate_sha", "generation_id",
        "receipt_fingerprint", "html_href",
    }
    if not isinstance(current_head, Mapping) or set(current_head) != \
            head_fields or current_head.get("schema") != \
            "taskplane.dashboard-current/v1" or \
            current_head.get("sequence") != snapshot["sequence"] or \
            current_head.get("snapshot_fingerprint") != \
            snapshot["fingerprint"] or \
            current_head.get("candidate_sha") != expected_source_sha or \
            current_head.get("generation_id") != generation["id"] or \
            current_head.get("receipt_fingerprint") != receipt["fingerprint"]:
        raise ValueError("dashboard durable head is stale or contradictory")
    return {
        "digest": receipt["fingerprint"],
        "source_sha": candidate["source_sha"],
        "status": "published",
        "fresh": True,
    }


def _rendered_head(
        model: Mapping[str, Any],
        snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    identity_value = model.get("identity")
    identity: Mapping[str, Any] = (
        identity_value if isinstance(identity_value, Mapping) else {})
    return {
        "workflow_id": str(model.get("workflow_id") or
                           identity.get("workflow_id") or "dashboard"),
        "run_id": str(model.get("run_id") or identity.get("run_id") or
                      "standalone"),
        "target": str(model.get("target") or identity.get("target") or
                      "dashboard"),
        "revision": str(model.get("revision") or identity.get("revision") or
                        snapshot["canonical_sha256"]),
        "sequence": snapshot["sequence"],
        "snapshot_fingerprint": snapshot["fingerprint"],
    }


def _graph_fingerprints(model: Mapping[str, Any]) -> dict[str, Any]:
    source_value = model.get("values")
    source: Mapping[str, Any] = (
        source_value if isinstance(source_value, Mapping) else model)
    return {key: _fingerprint_value(source[key]) for key in
            _DASHBOARD_GRAPH_KEYS if isinstance(source.get(key), Mapping)}


def _host_acknowledgement_receipt(
        acknowledgement: Mapping[str, Any] | None,
        rendered_head: Mapping[str, Any],
) -> dict[str, Any]:
    if acknowledgement is None:
        limitation = {
            "status": "static-limitation",
            "snapshot_fingerprint": rendered_head["snapshot_fingerprint"],
            "reason": "no exact host acknowledgement supplied",
        }
        limitation["fingerprint"] = _fingerprint_value(limitation)
        return limitation
    if not isinstance(acknowledgement, Mapping):
        raise TypeError("host_acknowledgement must be a mapping")
    value = dict(acknowledgement)
    supplied = value.pop("fingerprint", None)
    computed = _fingerprint_value(value)
    reasons = []
    if value.get("schema") != "taskplane.host-native-acknowledgement/v1":
        reasons.append("host acknowledgement schema mismatch")
    if not isinstance(supplied, str) or supplied != computed:
        reasons.append("host acknowledgement fingerprint mismatch")
    if value.get("snapshot_fingerprint") != \
            rendered_head["snapshot_fingerprint"]:
        reasons.append("host acknowledgement names another snapshot")
    if value.get("sequence") != rendered_head["sequence"]:
        reasons.append("host acknowledgement names another sequence")
    identity_value = value.get("identity")
    identity: Mapping[str, Any] = (
        identity_value if isinstance(identity_value, Mapping) else {})
    for key in _DASHBOARD_HEAD_IDENTITY_KEYS:
        if key not in identity:
            reasons.append(f"host acknowledgement {key} is missing")
        elif str(identity[key]) != str(rendered_head[key]):
            reasons.append(f"host acknowledgement {key} mismatch")
    if reasons:
        rejected = {
            "status": "rejected",
            "snapshot_fingerprint": rendered_head["snapshot_fingerprint"],
            "reason": "; ".join(reasons),
        }
        rejected["fingerprint"] = _fingerprint_value(rejected)
        return rejected
    return {
        "status": "acknowledged",
        "snapshot_fingerprint": rendered_head["snapshot_fingerprint"],
        "fingerprint": supplied,
    }


def _fsync_directory(path: str) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_current_head(path: str) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    if os.path.islink(path):
        raise ValueError("dashboard current pointer must not be a symlink")
    with open(path, encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or value.get("schema") != \
            "taskplane.dashboard-current/v1":
        raise ValueError("dashboard current pointer is invalid")
    return value


def dashboard_current_head(root: str) -> dict[str, Any] | None:
    """Read the durable head a publisher will compare in its CAS.

    The returned receipt fingerprint is an observation, not authority.  A
    caller passes it back as ``expected_head`` and the commit rechecks it
    while holding the current-pointer lock.
    """
    return _load_current_head(os.path.join(os.path.abspath(root),
                                           "current.json"))


def _commit_current_head(
        root: str, head: Mapping[str, Any], *, expected_head: object,
) -> dict[str, Any]:
    path = os.path.join(root, "current.json")
    lock_path = os.path.join(root, ".current.lock")
    try:
        lock = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError("dashboard current-pointer CAS is busy") from exc
    try:
        with os.fdopen(lock, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
            stream.flush()
            os.fsync(stream.fileno())
        current = _load_current_head(path)
        if expected_head is not _NO_EXPECTED_HEAD:
            observed = None if current is None else current.get(
                "receipt_fingerprint")
            if observed != expected_head:
                raise ValueError("dashboard current-pointer expected head changed")
        if current is not None:
            same_identity = all(current.get(key) == head.get(key)
                                for key in _DASHBOARD_HEAD_IDENTITY_KEYS)
            # Dashboard snapshot sequence is the publication epoch.  It is
            # monotonic across runs, so a delayed prior-run writer can never
            # replace the current run merely by changing identity fields.
            if current.get("sequence", -1) > head["sequence"]:
                raise ValueError("dashboard current pointer refuses stale sequence")
            if (current.get("sequence") == head["sequence"]
                    and current.get("snapshot_fingerprint") !=
                    head["snapshot_fingerprint"]):
                raise ValueError(
                    "dashboard current pointer refuses contradictory snapshot")
            if current.get("receipt_fingerprint") == head[
                    "receipt_fingerprint"]:
                return current
        _write_delivery_artifact(
            path, canonical_dashboard_bytes(dict(head)))
        _fsync_directory(root)
        return dict(head)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(lock_path)


def deliver_dashboard(output_dir: str, model: Mapping[str, Any], *,
                      inline_threshold: int = LARGE_DASHBOARD_INLINE_BYTES,
                      inline_renderer: Callable[[str], object] | None = None,
                      html_renderer: Callable[[str], object] | None = None,
                      html_stylesheet: str | None = None,
                      host_acknowledgement: Mapping[str, Any] | None = None,
                      expected_head: object = _NO_EXPECTED_HEAD,
                      ) -> dict[str, Any]:
    """Publish one canonical snapshot through disjoint delivery projections.

    Canonical bytes are encoded once. JSON, complete Markdown, optional HTML,
    graph bindings, the host acknowledgement, and DOM freshness join in a
    content-addressed generation before the expected-head current-pointer CAS.
    Presentation failure cannot change the canonical delivery outcome.
    """
    if isinstance(inline_threshold, bool) or not isinstance(inline_threshold, int) \
            or inline_threshold < 1:
        raise ValueError("inline_threshold must be a positive byte count")
    canonical = canonical_dashboard_bytes(model)
    canonical_text = canonical.decode("utf-8")
    root = os.path.abspath(output_dir)
    if os.path.lexists(root) and os.path.islink(root):
        raise ValueError("dashboard output directory must not be a symlink")
    os.makedirs(root, exist_ok=True)
    markdown = (
        "# Taskplane dashboard\n\n"
        "Canonical complete dashboard evidence (JSON):\n\n"
        f"{_CANONICAL_START}\n```json\n{canonical_text}\n```\n"
        f"{_CANONICAL_END}\n").encode("utf-8")
    inline = None
    mode = "complete-markdown"
    if len(canonical) <= inline_threshold:
        if not callable(inline_renderer):
            raise ValueError("dashboard inline renderer is required")
        content = inline_renderer(canonical_text)
        inline = {"format": "html", "content": content, "complete": True,
                  "semantic_bytes": len(canonical)}
        mode = "inline"

    canonical_sha256 = hashlib.sha256(canonical).hexdigest()
    snapshot_receipt = _snapshot_receipt(model, canonical_sha256)
    candidate_receipt = _candidate_receipt(snapshot_receipt)
    rendered_head = _rendered_head(model, snapshot_receipt)
    host_receipt = _host_acknowledgement_receipt(
        host_acknowledgement, rendered_head)
    actions_enabled = host_receipt["status"] == "acknowledged"
    html_payload = None
    html_error = None
    structural_error = False
    if html_renderer is None:
        html_error = "optional HTML was not requested"
    else:
        try:
            body = str(html_renderer(canonical_text))
            html_payload = _embedded_html(
                body, canonical, rendered_head=rendered_head,
                actions_enabled=actions_enabled,
                stylesheet=html_stylesheet)
        except Exception as exc:
            html_error = f"{exc.__class__.__name__}: {exc}"
            structural_error = isinstance(exc, ValueError) and \
                ("document" in str(exc) or "fragment" in str(exc))

    artifact_hashes = {
        "json": hashlib.sha256(canonical).hexdigest(),
        "markdown": hashlib.sha256(markdown).hexdigest(),
        "html": hashlib.sha256(html_payload).hexdigest()
        if html_payload is not None else None,
    }
    generation_id = _fingerprint_value({
        "artifacts": artifact_hashes,
        "host_acknowledgement": host_receipt["fingerprint"],
    })
    generation_root = os.path.join(root, "generations", generation_id)
    os.makedirs(generation_root, exist_ok=True)
    json_path = os.path.join(generation_root, "dashboard.json")
    markdown_path = os.path.join(generation_root, "dashboard.md")
    html_path = os.path.join(generation_root, "dashboard.html")
    _write_delivery_artifact(json_path, canonical)
    _write_delivery_artifact(markdown_path, markdown)
    artifacts = {"json": _artifact_ref(json_path, canonical),
                 "markdown": _artifact_ref(markdown_path, markdown)}
    if html_payload is not None:
        _write_delivery_artifact(html_path, html_payload)
        artifacts["html"] = _artifact_ref(html_path, html_payload)
    else:
        artifacts["html"] = {
            "status": "unavailable", "path": html_path,
            "reason": str(html_error)}

    dom_freshness = {
        "status": "verified" if html_payload is not None else "unavailable",
        "html_document_count": 1 if html_payload is not None else 0,
        "canonical_sha256": canonical_sha256,
        "actions_enabled": bool(html_payload is not None and actions_enabled),
    }
    dom_freshness["fingerprint"] = _fingerprint_value(dom_freshness)
    graphs = _graph_fingerprints(model)
    receipt = {
        "schema": "taskplane.dashboard-publication-receipt/v1",
        "snapshot": snapshot_receipt,
        "candidate": candidate_receipt,
        "graphs": graphs,
        "dom_freshness": dom_freshness,
        "host_acknowledgement": host_receipt,
        "generation": {
            "id": generation_id, "artifacts": artifact_hashes,
            "complete": not structural_error,
        },
        "bindings": {
            "snapshot": snapshot_receipt["fingerprint"],
            "candidate": candidate_receipt["fingerprint"],
            "graphs": graphs,
            "dom_freshness": dom_freshness["fingerprint"],
            "host_acknowledgement": host_receipt["fingerprint"],
        },
    }
    receipt["fingerprint"] = dashboard_publication_receipt_fingerprint(receipt)
    receipt_path = os.path.join(generation_root, "publication-receipt.json")
    receipt_bytes = canonical_dashboard_bytes(receipt)
    _write_delivery_artifact(receipt_path, receipt_bytes)
    _fsync_directory(generation_root)
    _fsync_directory(os.path.dirname(generation_root))

    current_head = None
    status = "rejected" if structural_error else "published"
    if not structural_error:
        current_head = _commit_current_head(root, {
            "schema": "taskplane.dashboard-current/v1",
            **{key: rendered_head[key]
               for key in _DASHBOARD_HEAD_IDENTITY_KEYS},
            "sequence": rendered_head["sequence"],
            "snapshot_fingerprint": rendered_head["snapshot_fingerprint"],
            "candidate_sha": candidate_receipt["source_sha"],
            "generation_id": generation_id,
            "receipt_fingerprint": receipt["fingerprint"],
            "html_href": (
                f"generations/{generation_id}/dashboard.html"
                if html_payload is not None else None),
        }, expected_head=expected_head)

    gate_source = model.get("gate")
    values_source = model.get("values")
    if not isinstance(gate_source, Mapping) and isinstance(
            values_source, Mapping):
        gate_source = values_source.get("gate")
    return {
        "schema": "taskplane.dashboard-delivery/v1", "status": status,
        "mode": mode, "semantic_bytes": len(canonical),
        "semantic_sha256": canonical_sha256,
        "gate": dict(gate_source or {}), "inline": inline,
        "artifacts": artifacts,
        "publication_receipt": receipt,
        "publication_receipt_artifact": _artifact_ref(
            receipt_path, receipt_bytes),
        "current_head": current_head,
    }


def decode_dashboard_artifact(
        kind: str, payload: bytes) -> dict[str, Any]:
    """Decode a delivery surface for semantic-equivalence verification."""
    text = payload.decode("utf-8")
    if kind == "json":
        value = json.loads(text)
    elif kind == "markdown":
        start = text.index(_CANONICAL_START) + len(_CANONICAL_START)
        end = text.rindex(_CANONICAL_END)
        fenced = text[start:end].strip()
        if not fenced.startswith("```json\n") or not fenced.endswith("\n```"):
            raise ValueError("invalid complete Markdown dashboard artifact")
        value = json.loads(fenced[len("```json\n"):-len("\n```")])
    elif kind in {"html", "inline"}:
        marker = 'data-taskplane-canonical="true">'
        start = text.index(marker) + len(marker)
        end = text.index("</script>", start)
        value = json.loads(base64.b64decode(text[start:end]).decode("utf-8"))
    else:
        raise ValueError(f"unsupported dashboard artifact kind: {kind}")
    if not isinstance(value, dict):
        raise ValueError("dashboard artifact must decode to an object")
    return value
DASHBOARD_PUBLICATION_SCHEMA = "taskplane.dashboard-publication/v1"
_DASHBOARD_SURFACES = ("native", "json", "markdown", "html")


def _canonical_fingerprint(value: object) -> str:
    material = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _generated_at(value: float | str | None) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    timestamp = time.time() if value is None else float(value)
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace(
        "+00:00", "Z")


def _v4_dashboard_source(
        manifest: dict[str, Any], run_id: str, *,
        manifest_validator: Callable[..., Any],
        error_formatter: Callable[[Exception], str],
) -> dict[str, Any]:
    try:
        if manifest.get("schema") != "taskplane.run/v4" or \
                manifest.get("run_id") != run_id:
            raise ValueError(
                "run manifest identity/schema is not taskplane.run/v4")
        manifest_validator(manifest)
        projection = manifest["active_stage_projection"]
        active_ids = list(projection["active_stage_ids"])
        foreground = projection["foreground_stage_id"]
        if foreground is None and len(active_ids) > 1:
            return {
                "mode": "v4", "status": "ambiguous", "run_id": run_id,
                "revision": str(manifest.get("revision") or "unknown"),
                "target": "active-stage", "state": None,
                "source_fingerprint": _canonical_fingerprint(manifest),
                "evidence": [
                    "active stage projection has several active stages and "
                    "no foreground stage"],
            }
        stage_id = foreground or (active_ids[0] if active_ids else None)
        heads = manifest["stage_heads"]
        summary = copy.deepcopy(heads[stage_id]["summary"]) \
            if stage_id is not None else None
        state = {
            "step": ((summary or {}).get("stage_kind") or
                     ("done" if not active_ids else "unknown")),
            "stage_view": {
                "schema": "taskplane.bounded-stage-view/v1",
                "mode": "v4", "status": "v4", "available": True,
                "run_id": run_id, "revision": manifest.get("revision"),
                "current_stage": summary,
                "active_stage_ids": active_ids,
            },
        }
        return {
            "mode": "v4", "status": "ready", "run_id": run_id,
            "revision": str(manifest.get("revision") or "unknown"),
            "target": str(stage_id or "run"), "state": state,
            "source_fingerprint": _canonical_fingerprint(manifest),
            "evidence": [
                "run-manifest:" + _canonical_fingerprint(manifest)],
        }
    except Exception as exc:
        return {
            "mode": "v4", "status": "corrupt", "run_id": run_id,
            "revision": str(manifest.get("revision") or "unknown"),
            "target": "active-stage", "state": None,
            "source_fingerprint": _canonical_fingerprint(manifest),
            "evidence": [error_formatter(exc)],
        }


def select_dashboard_source(
        ws: str, *, locator_loader: Callable[..., Any],
        legacy_loader: Callable[..., Any],
        manifest_loader: Callable[..., Any],
        manifest_validator: Callable[..., Any],
        error_formatter: Callable[[Exception], str],
) -> dict[str, Any]:
    """Select legacy or v4 once, then perform exactly one state read."""
    try:
        locator = locator_loader(ws)
    except Exception as exc:
        return {
            "mode": "v4", "status": "corrupt", "run_id": "unknown-v4",
            "revision": "unknown", "target": "active-stage", "state": None,
            "source_fingerprint": _canonical_fingerprint(
                {"locator_error": error_formatter(exc)}),
            "evidence": [error_formatter(exc)],
        }
    if isinstance(locator, dict):
        run_id = str(locator.get("run_id") or "unknown-v4")
        try:
            manifest = manifest_loader(ws, locator)
        except Exception as exc:
            return {
                "mode": "v4", "status": "corrupt", "run_id": run_id,
                "revision": "unknown", "target": "active-stage",
                "state": None,
                "source_fingerprint": _canonical_fingerprint(
                    {"run_id": run_id, "error": error_formatter(exc)}),
                "evidence": [error_formatter(exc)],
            }
        return _v4_dashboard_source(
            manifest, run_id, manifest_validator=manifest_validator,
            error_formatter=error_formatter)
    state = legacy_loader(ws)
    if state is None:
        return {"mode": "none", "status": "no_active", "state": None,
                "evidence": []}
    fingerprint = _canonical_fingerprint(state)
    task = None
    tasks = state.get("tasks") or []
    index = state.get("current_task")
    if isinstance(index, int) and 0 <= index < len(tasks):
        task = tasks[index]
    run_id = str(state.get("run_id") or "legacy-" + _canonical_fingerprint({
        "goal": state.get("goal"), "baseline": state.get("baseline"),
    })[:24])
    return {
        "mode": "legacy", "status": "ready", "run_id": run_id,
        "revision": str(state.get("baseline") or fingerprint),
        "target": str((task or {}).get("id") or state.get("step") or "run"),
        "state": state, "source_fingerprint": fingerprint,
        "evidence": ["loop-state:" + fingerprint],
    }


def _bounded_loop_values(
        state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    tasks = [
        {key: row.get(key) for key in
         ("id", "status", "fix_cycles", "variant") if row.get(key) is not None}
        for row in (state.get("tasks") or []) if isinstance(row, dict)
    ]
    return {
        "goal": state.get("goal"), "step": state.get("step"),
        "requirement_id": state.get("requirement_id"),
        "current_task": state.get("current_task"), "tasks": tasks,
        **({"stage_view": copy.deepcopy(state["stage_view"])}
           if isinstance(state.get("stage_view"), dict) else {}),
    }


def _phase_graph_values(
        ws: str, state: dict[str, Any] | None, *,
        projector: Callable[..., Any],
        error_formatter: Callable[[Exception], str],
) -> dict[str, Any]:
    """Consume the projection slice when present; never invent graph truth."""
    try:
        project = projector
        if not callable(project):
            return {"phase_graph_error":
                    "phase graph projector is not configured"}
        values = project(ws, state=state, require_bound=True)
        return {key: copy.deepcopy(values[key]) for key in (
            "design_graph", "plan_task_dag", "plan_waves", "module_impact")
            if key in values}
    except Exception as exc:
        return {"phase_graph_error": error_formatter(exc)}


def _wave_metrics_values(
        state: dict[str, Any] | None, *,
        metrics_projector: Callable[..., Any],
        error_formatter: Callable[[Exception], str],
) -> dict[str, Any]:
    """Project the sealed receipt already present in the one selected state."""
    if not isinstance(state, dict) or state.get("wave_metrics_receipt") is None:
        reason = "sealed terminal wave metrics receipt is unavailable"
        unavailable = ((state or {}).get("wave_metrics_unavailable")
                       if isinstance(state, dict) else None)
        if isinstance(unavailable, dict) and unavailable.get("reason"):
            reason = str(unavailable["reason"])
        return {"wave_metrics": wave_metrics.unavailable_consumer_projection(
            consumer="dashboard", reason=reason)}
    try:
        return {"wave_metrics": metrics_projector(
            state["wave_metrics_receipt"], consumer="dashboard")}
    except Exception as exc:
        return {"wave_metrics": {
            "schema": "taskplane.wave-metrics-projection/v1",
            "consumer": "dashboard", "status": "unavailable",
            "error": error_formatter(exc),
        }}


def _authority_receipt_binding(state: Mapping[str, Any] | None) -> str | None:
    """Return one non-secret authority receipt identity, when available."""
    if not isinstance(state, Mapping):
        return None
    receipt = state.get("authority_receipt")
    if isinstance(receipt, Mapping):
        claimed = receipt.get("fingerprint") or receipt.get("receipt_id")
        if isinstance(claimed, str) and claimed.strip():
            return claimed.strip()
        return _canonical_fingerprint(receipt)
    if isinstance(receipt, str) and receipt.strip():
        return receipt.strip()
    return None


def _dashboard_candidate_values(
        state: Mapping[str, Any] | None) -> dict[str, Any]:
    """Name a migrated legacy run's observed candidate without hiding baseline."""
    if not isinstance(state, Mapping):
        return {}
    baseline = state.get("baseline")
    if not isinstance(baseline, str) or not re.fullmatch(
            r"[0-9a-f]{40}", baseline):
        return {}
    values: dict[str, Any] = {
        "candidate_sha": baseline,
        "baseline_sha": baseline,
    }
    try:
        if __package__:
            from . import run_artifacts
        else:  # pragma: no cover - direct module loading
            import run_artifacts  # type: ignore
        binding = run_artifacts.validate_binding(
            state.get("run_artifact_binding"))
    except Exception:
        return values
    candidate = binding.get("candidate")
    if not isinstance(candidate, Mapping) or candidate.get("schema") != \
            "taskplane.legacy-terminal-observation/v1":
        return values
    observation = {key: candidate.get(key) for key in (
        "schema", "run_id", "baseline", "observed_revision",
        "workspace_fingerprint", "source_state_fingerprint",
        "tasks_fingerprint", "execution_status",
    )}
    observed = observation["observed_revision"]
    if candidate.get("fingerprint") != _canonical_fingerprint(observation) or \
            observation["run_id"] != state.get("run_id") or \
            observation["baseline"] != baseline or \
            observation["execution_status"] != "unproven" or \
            not isinstance(observed, str) or \
            not re.fullmatch(r"[0-9a-f]{40}", observed):
        return values
    return {
        **values,
        "candidate_sha": observed,
        "observed_revision": observed,
        "candidate_execution_status": "unproven",
    }


def _next_dashboard_sequence(
        ws: str, source: dict[str, Any], *,
        publication_loader: Callable[..., Any],
) -> int:
    prior = publication_loader(ws)
    if prior is None:
        return 1
    current = HostSurfaceSnapshot.from_dict(prior["current"])
    # This is a publication epoch, not an identity-local counter.  Keeping it
    # global makes delayed data from a prior run mechanically older.
    return current.sequence + 1


def _publication(
        snapshot: HostSurfaceSnapshot | None,
        event: HostSurfaceEvent | None, *, source_mode: str,
        replayed: bool, status: str,
) -> dict[str, Any]:
    fingerprint = snapshot.fingerprint if snapshot is not None else None
    return {
        "schema": DASHBOARD_PUBLICATION_SCHEMA, "status": status,
        "snapshot": snapshot.to_dict() if snapshot is not None else None,
        "event": event.to_dict() if event is not None else None,
        "replayed": bool(replayed), "source_mode": source_mode,
        "surfaces": ({name: fingerprint for name in _DASHBOARD_SURFACES}
                     if fingerprint is not None else {}),
    }


def refresh_dashboard_snapshot(
        ws: str, *, event_type: str, outcome: str | None = None,
        committed_at: float | str | None = None, replay: bool = False,
        settings_digest: str, source_loader: Callable[..., Any],
        graph_projector: Callable[..., Any],
        metrics_projector: Callable[..., Any],
        publication_loader: Callable[..., Any],
        snapshot_committer: Callable[..., Any],
        event_committer: Callable[..., Any],
        error_formatter: Callable[[Exception], str],
) -> dict[str, Any]:
    """Freeze or idempotently replay the sole canonical dashboard snapshot."""
    if not str(event_type or "").strip():
        raise ValueError("dashboard event_type is required")
    source = source_loader(ws)
    if source["status"] == "no_active":
        return {"schema": DASHBOARD_PUBLICATION_SCHEMA,
                "status": "no_active", "snapshot": None, "event": None,
                "replayed": False, "source_mode": "none", "surfaces": {}}
    source_fingerprint = str(source.get("source_fingerprint") or
                             _canonical_fingerprint(source))
    source["source_fingerprint"] = source_fingerprint
    prior = publication_loader(ws)
    if replay and prior is not None:
        current = HostSurfaceSnapshot.from_dict(prior["current"])
        if current.values.get("source_fingerprint") == source.get(
                "source_fingerprint") and current.values.get(
                    "settings_digest") == settings_digest:
            event = HostSurfaceEvent.from_snapshot(
                current, event_type=str(event_type))
            event_committer(ws, event)
            return _publication(
                current, event, source_mode=str(source["mode"]),
                replayed=True, status=str(source["status"]))
    evidence = tuple(str(item) for item in source.get("evidence") or [])
    healthy = source.get("status") == "ready"
    state = source.get("state") if isinstance(source.get("state"), dict) \
        else None
    stage = str((state or {}).get("step") or source.get("status") or "unknown")
    metrics_values = _wave_metrics_values(
        state, metrics_projector=metrics_projector,
        error_formatter=error_formatter)
    phase_values = _phase_graph_values(
        ws, state, projector=graph_projector,
        error_formatter=error_formatter)
    publication_epoch = _next_dashboard_sequence(
        ws, source, publication_loader=publication_loader)
    graph_components = {
        key: phase_values[key] for key in _DASHBOARD_GRAPH_KEYS
        if isinstance(phase_values.get(key), Mapping)
    }
    graph_receipt = (_canonical_fingerprint(graph_components)
                     if graph_components else None)
    provenance = {
        "schema": "taskplane.dashboard-provenance/v1",
        "run_id": str(source["run_id"]),
        "requirement_id": str((state or {}).get("requirement_id") or
                              "unavailable"),
        "stage": stage,
        "revision": str(source.get("revision") or source_fingerprint),
        "settings_digest": settings_digest,
        "authority_receipt": _authority_receipt_binding(state),
        "graph_receipt": graph_receipt,
        "publication_epoch": publication_epoch,
    }
    candidate_value = _dashboard_candidate_values(state)
    values = {
        "generated_at": _generated_at(committed_at),
        "settings_digest": settings_digest,
        "source_mode": source["mode"],
        "source_status": source["status"],
        "source_fingerprint": source_fingerprint,
        "event_type": str(event_type), "outcome": outcome,
        **candidate_value,
        "loop": _bounded_loop_values(state),
        **phase_values,
        "provenance": provenance,
        **metrics_values,
    }
    safe_actions: tuple[str, ...] = ()
    metrics_signoff_ready = \
        ((metrics_values.get("wave_metrics") or {}).get("signoff") or {}).get(
            "ready") is True
    if healthy and stage in {"design_approval", "plan_approval"}:
        safe_actions = ("approve", "reject")
    elif healthy and stage == "signoff" and metrics_signoff_ready:
        safe_actions = ("approve", "reject")
    elif healthy and stage == "escalated":
        safe_actions = ("retry", "skip", "defer", "abort")
    revision = str(source.get("revision") or source_fingerprint)
    snapshot = HostSurfaceSnapshot.create(
        workflow_id="taskplane-loop", run_id=str(source["run_id"]),
        target=str(source["target"]), revision=revision,
        sequence=publication_epoch, stage=stage,
        state=stage if healthy else str(source["status"]), values=values,
        evidence=evidence, safe_actions=safe_actions)
    committed = snapshot_committer(ws, snapshot)
    frozen = HostSurfaceSnapshot.from_dict(committed["current"])
    event = HostSurfaceEvent.from_snapshot(
        frozen, event_type=str(event_type))
    event_committer(ws, event)
    return _publication(
        frozen, event, source_mode=str(source["mode"]),
        replayed=bool(committed.get("replayed")),
        status=str(source["status"]))
HOST_DASHBOARD_COMPONENTS = (
    "provenance", "workflow", "dor", "dependency_impact", "design_graph",
    "plan_task_dag", "plan_waves", "module_impact", "agents", "lenses",
    "criteria", "findings", "validation", "artifacts", "wave_metrics",
    "gate", "loop",
)


def _dashboard_plain(value: Any) -> Any:
    """Return JSON-shaped presentation data without mutating canonical data."""
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(key): _dashboard_plain(item)
                for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_dashboard_plain(item) for item in value]
    return value


def carousel_pages(
        items: Iterable[Any], *, filters: Mapping[str, Any] | None = None,
        current: int = 1, page_size: int = 8,
) -> dict[str, Any]:
    """Create deterministic, lossless carousel pages of three to eight items.

    Zero and one item remain concise native cards.  Larger collections use a
    stable input order; a one/two-item tail is rebalanced into the preceding
    page so every carousel page stays within the host UI guideline.  Filtering
    is explicit equality matching and therefore serializable and replayable.
    """
    if isinstance(page_size, bool) or not isinstance(page_size, int) \
            or not 3 <= page_size <= 8:
        raise ValueError("page_size must be between 3 and 8")
    filters = dict(filters or {})
    selected: list[dict[str, Any]] = []
    identities: set[str] = set()
    for raw in items:
        row = _dashboard_plain(raw)
        identity = row.get("id") if isinstance(row, dict) else None
        if not isinstance(identity, str) or not identity.strip() \
                or identity in identities:
            raise ValueError("every carousel item requires a stable unique id")
        identities.add(identity)
        if all(row.get(key) == value for key, value in filters.items()):
            selected.append(row)

    chunks = [selected[i:i + page_size]
              for i in range(0, len(selected), page_size)]
    if len(chunks) > 1 and len(chunks[-1]) < 3:
        needed = 3 - len(chunks[-1])
        chunks[-1][0:0] = chunks[-2][-needed:]
        del chunks[-2][-needed:]
    total_pages = len(chunks)
    active = min(max(int(current or 1), 1), max(total_pages, 1))
    pages = [{
        "id": f"page-{index}",
        "position": index,
        "total_pages": total_pages,
        "items": chunk,
        "item_ids": [item["id"] for item in chunk],
    } for index, chunk in enumerate(chunks, 1)]
    return {
        "schema": "taskplane.host-carousel/v1",
        "total_items": len(selected),
        "total_pages": total_pages,
        "current": active,
        "filters": filters,
        "navigation": {
            "previous": active - 1 if total_pages and active > 1 else None,
            "next": active + 1 if active < total_pages else None,
        },
        "pages": pages,
    }


def native_dashboard_projection(
        snapshot: HostSurfaceSnapshot, *, host: str,
        filters: Mapping[str, Any] | None = None, current: int = 1,
) -> dict[str, Any]:
    """Project one canonical snapshot into an accessible host-native model.

    The host-specific section contains styling and interaction affordances
    only.  It cannot change semantic values, evidence, provenance, ordering,
    actions, or gate state, keeping Claude and Codex projections comparable.
    """
    if host not in {"codex", "claude"}:
        raise ValueError("host must be codex or claude")
    canonical = snapshot.to_dict()
    values = canonical["values"]
    components: list[dict[str, Any]] = []
    for order, name in enumerate(HOST_DASHBOARD_COMPONENTS):
        value = _dashboard_plain(values.get(name, {}))
        row = {"id": name, "order": order, "value": value}
        if isinstance(value, dict) and isinstance(value.get("items"), list):
            row["collection"] = carousel_pages(
                value["items"], filters=filters, current=current)
        components.append(row)

    actions = list(canonical["safe_actions"])
    return {
        "schema": "taskplane.host-native-dashboard/v1",
        "identity": {key: canonical[key] for key in (
            "workflow_id", "run_id", "target", "revision", "sequence")},
        "stage": canonical["stage"],
        "state": canonical["state"],
        "fingerprint": canonical["fingerprint"],
        "components": components,
        "evidence": list(canonical["evidence"]),
        "safe_actions": actions,
        "presentation": {
            "host": host,
            "style": "openai-system" if host == "codex" else "claude-system",
            "primary_actions": actions[:2],
            "detail_actions": actions[2:],
            "card": {
                "single_purpose": True,
                "max_primary_actions": 2,
                "nested_scroll": False,
                "deep_navigation": False,
                "rich_detail_surface": "fullscreen",
                "composer_retained": True,
            },
            "responsive": {"min_viewport_px": 320, "layout": "fluid"},
            "accessibility": {
                "semantic_labels": True,
                "alt_text": True,
                "keyboard_navigation": True,
                "visible_focus": True,
                "text_scale_percent": 200,
                "reduced_motion": True,
                "status_not_color_only": True,
                "contrast": "WCAG-AA",
                "fonts": "system",
                "tokens": "host-system",
                "themes": ["light", "dark"],
            },
        },
    }
def canonical_revision_identity(value: Any) -> dict[str, Any]:
    """Validate and normalize the tuple shared by every review projection."""
    source = value.get("identity") if isinstance(value, dict) \
        and isinstance(value.get("identity"), dict) else value
    source = source if isinstance(source, dict) else {}
    if any(source.get(key) in (None, "") for key in REVISION_ID_KEYS):
        raise ValueError("complete canonical revision identity is required")
    try:
        revision = int(source["canonical_revision"])
    except (TypeError, ValueError):
        raise ValueError("canonical revision identity has invalid revision")
    if revision < 1:
        raise ValueError("canonical revision identity has invalid revision")
    return {
        "target_fingerprint": str(source["target_fingerprint"]),
        "context_fingerprint": str(source["context_fingerprint"]),
        "findings_fingerprint": str(source["findings_fingerprint"]),
        "canonical_revision": revision,
    }


def canonical_report_projection(
        report: str, identity: dict[str, Any],
) -> dict[str, Any]:
    """A report projection that cannot drop or rename canonical identity."""
    return {"schema": "taskplane.review-projection/v1", "kind": "report",
            "identity": canonical_revision_identity(identity),
            "body": str(report or "")}

def _dashboard_escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def render_wave_metrics_projection(projection: Mapping[str, Any] | None) -> str:
    """Render only the supplied sealed dashboard projection; perform no reads."""
    if not isinstance(projection, Mapping) or projection.get("schema") != \
            "taskplane.wave-metrics-projection/v1" or \
            projection.get("consumer") != "dashboard":
        return ""
    receipt = str(projection.get("receipt_fingerprint") or "")
    metrics = projection.get("metrics")
    if not receipt or not isinstance(metrics, Mapping):
        return ""
    rows: list[str] = []
    for name, metric in metrics.items():
        if not isinstance(metric, Mapping):
            continue
        rows.append(
            f'<li data-wave-metric="{_dashboard_escape(name)}"><code>{_dashboard_escape(name)}</code> · '
            f'actual {_dashboard_escape(metric.get("actual"))} {_dashboard_escape(metric.get("unit"))} · '
            f'baseline {_dashboard_escape(metric.get("baseline"))} · target '
            f'{_dashboard_escape(metric.get("target"))}</li>')
    signoff_value = projection.get("signoff")
    signoff: Mapping[str, Any] = (
        signoff_value if isinstance(signoff_value, Mapping) else {})
    return (
        '<section class="tp-sec" id="tp-wave-metrics" '
        f'data-wave-metrics-receipt="{_dashboard_escape(receipt)}">'
        '<p class="tp-kicker">sealed delivery-wave metrics</p>'
        f'<p class="tp-lede">receipt <code>{_dashboard_escape(receipt)}</code> · sign-off '
        f'{"ready" if signoff.get("ready") is True else "blocked"}</p><ol>'
        + "".join(rows) + "</ol></section>")
