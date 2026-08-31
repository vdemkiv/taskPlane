"""Presentation: rendering the dashboard and publishing gate snapshots.

D-0011. This lived inside `loop.py`, run from a decorator wrapped around
every state transition, with the whole thing under `except Exception: pass`.
Two defects in one place.

The layering one is what loop.py's own comment had been recording as debt
since v2.3.0: "rendering/publishing belongs in the CLI/driver layer". An
engine that renders its own view cannot be reasoned about, tested, or
replaced without dragging a renderer along, and `dashboard` importing `loop`
at module top meant every import here had to be smuggled inside a function
body to avoid closing a cycle.

The behavioural one is worse. "Fail-open" was implemented as a bare
`except: pass`, which is not fail-open — it is fail-SILENT. The transition
payload TELLS the human the dashboard was "refreshed for this transition";
when rendering threw, that key was simply absent. No error, no trace, no
warning: a transition that looked perfectly healthy while the artifact the
human was told to govern through was stale or missing. That is the exact
shape of the most-repeated complaint against this product — "no inline
dashboard visualisation, no report, nothing" — and by construction it left
nothing behind to diagnose it with.

Rendering still cannot break a transition. It now REPORTS instead of
vanishing: the failure goes into the payload, into the trace, and once per
process onto stderr.

The cycle is genuinely broken, not hidden: nothing here imports `loop` at
module scope, so `dashboard` -> `loop` -> `views` closes nothing. One
function needs the track's goal to name the snapshot directory and takes it
from `loop.load` at call time; `publish_report(ws, state=...)` lets a caller
that already holds the state skip even that.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
from html.parser import HTMLParser
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping

import taskplane_lite as tp


_REVISION_ID_KEYS = ("target_fingerprint", "context_fingerprint",
                     "findings_fingerprint", "canonical_revision")

# A transition always refreshes the durable dashboard, but only a HUMAN gate
# needs the model to stop, surface it, and acknowledge delivery.  Issuing and
# acknowledging a fresh render obligation at every internal PM/Plan/Evaluate
# hop turned progress reporting into repeated CLI work and repeatedly sent the
# same HTML through model context.  Keep one open, deterministic obligation
# through the internal transitions; the gate payload carries the one ack.
_HUMAN_DASHBOARD_STEPS = frozenset({
    "design_approval", "plan_approval", "selection", "signoff", "escalated",
})

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


def canonical_dashboard_bytes(model: Mapping) -> bytes:
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


def _artifact_ref(path: str, payload: bytes) -> dict:
    return {"status": "available", "path": path, "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest()}


def _fingerprint_value(value: Mapping) -> str:
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

    def handle_starttag(self, tag: str, attrs) -> None:
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

    def closed(match: re.Match) -> str:
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


def _dashboard_freshness_controller(rendered_head: Mapping,
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
        'root.dataset.dashboardFreshnessReason=reason||"";mutations().forEach('
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


def _embedded_html(body: str, canonical: bytes, *, rendered_head: Mapping,
                   actions_enabled: bool) -> bytes:
    fragment_shape = _html_shape(body)
    if fragment_shape.doctypes or any(fragment_shape.tags.values()):
        raise ValueError(
            "HTML renderer must return a fragment, not a document boundary")
    if not actions_enabled:
        body = _disable_unverified_actions(body)
    encoded = base64.b64encode(canonical).decode("ascii")
    document = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Taskplane dashboard</title></head><body '
        'data-dashboard-delivery-root="true" data-dashboard-freshness="'
        + ("fresh" if actions_enabled else "unverified") + '">'
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


def _normalize_delivery_head(value: Mapping) -> dict:
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
        rendered_head: Mapping, current_head: "Mapping | None", *,
        page_url: str, bridge_available: bool, fetch_available: bool,
        previously_stale: bool = False,
        previous_rendered_sequence: "int | None" = None) -> dict:
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


def dashboard_publication_receipt_fingerprint(receipt: Mapping) -> str:
    """Authenticate a receipt after removing only its self fingerprint."""
    payload = dict(receipt)
    payload.pop("fingerprint", None)
    return _fingerprint_value(payload)


def _lowercase_digest(value, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(
        character in "0123456789abcdef" for character in value)


def _snapshot_receipt(model: Mapping, canonical_sha256: str) -> dict:
    identity = model.get("identity") if isinstance(
        model.get("identity"), Mapping) else {}
    values = model.get("values") if isinstance(
        model.get("values"), Mapping) else model
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


def _candidate_receipt(snapshot: Mapping) -> dict:
    candidate = {
        "source_sha": snapshot.get("candidate_sha"),
        "snapshot_fingerprint": snapshot.get("fingerprint"),
        "canonical_sha256": snapshot.get("canonical_sha256"),
    }
    candidate["fingerprint"] = _fingerprint_value(candidate)
    return candidate


def validate_dashboard_publication_receipt(
        receipt: Mapping, *, current_head: Mapping,
        expected_source_sha: str) -> dict:
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


def _rendered_head(model: Mapping, snapshot: Mapping) -> dict:
    identity = model.get("identity") if isinstance(
        model.get("identity"), Mapping) else {}
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


def _graph_fingerprints(model: Mapping) -> dict:
    source = model.get("values") if isinstance(
        model.get("values"), Mapping) else model
    return {key: _fingerprint_value(source[key]) for key in
            _DASHBOARD_GRAPH_KEYS if isinstance(source.get(key), Mapping)}


def _host_acknowledgement_receipt(
        acknowledgement: "Mapping | None", rendered_head: Mapping) -> dict:
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
    identity = value.get("identity") if isinstance(
        value.get("identity"), Mapping) else {}
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


def _load_current_head(path: str) -> "dict | None":
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


def _commit_current_head(root: str, head: Mapping, *, expected_head) -> dict:
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
            if same_identity and current.get("sequence", -1) > head["sequence"]:
                raise ValueError("dashboard current pointer refuses stale sequence")
            if (same_identity and current.get("sequence") == head["sequence"]
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


def deliver_dashboard(output_dir: str, model: Mapping, *,
                      inline_threshold: int = LARGE_DASHBOARD_INLINE_BYTES,
                      html_renderer=None,
                      host_acknowledgement: "Mapping | None" = None,
                      expected_head=_NO_EXPECTED_HEAD) -> dict:
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
        import dashboard as _dashboard
        content = _dashboard.render_lossless_dashboard_inline(canonical_text)
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
                actions_enabled=actions_enabled)
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
    if not isinstance(gate_source, Mapping) and isinstance(
            model.get("values"), Mapping):
        gate_source = model["values"].get("gate")
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


def decode_dashboard_artifact(kind: str, payload: bytes) -> dict:
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


def _transition_step(out: dict) -> str:
    state = out.get("status") or out.get("state") or {}
    return str((state.get("step") if isinstance(state, dict) else None)
               or out.get("step") or "")


def canonical_revision_identity(value: dict) -> dict:
    """Validate and normalize the tuple shared by every review projection."""
    source = value.get("identity") if isinstance(value, dict) \
        and isinstance(value.get("identity"), dict) else value
    source = source if isinstance(source, dict) else {}
    if any(source.get(key) in (None, "") for key in _REVISION_ID_KEYS):
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


def canonical_report_projection(report: str, identity: dict) -> dict:
    """A report projection that cannot drop or rename canonical identity."""
    return {"schema": "taskplane.review-projection/v1", "kind": "report",
            "identity": canonical_revision_identity(identity),
            "body": str(report or "")}


def terminal_public_report_surface(identity: dict, report: dict) -> dict:
    """Prepare the public-report member of the exact-SHA terminal bundle."""
    try:
        from taskplane import terminal_truth
    except ImportError:  # direct executable/import compatibility
        import terminal_truth
    return terminal_truth.prepare_terminal_surface(
        "public_report", identity, dict(report)
    )


def projection_identity_problem(projection: dict,
                                expected: dict) -> "str | None":
    """Why a projection is stale/contradictory, or None."""
    try:
        have = canonical_revision_identity(projection)
        want = canonical_revision_identity(expected)
    except ValueError as exc:
        return str(exc)
    if have != want:
        return "projection identity is stale or contradictory"
    return None


# ---- shared progress artifacts (v2.0.0) -------------------------------------
# Every gate transition snapshots its decision artifacts into the ACTIVE
# store (team plan: in-repo .taskplane-kb/; personal: the external store).
# Doubles as a context cache. Fail-open: publishing never breaks the loop.
#
# D-0013. On a TEAM plan the active store is `<repo>/.taskplane-kb/` — in
# the repo, meant to be committed. This function used to copy model-authored
# review prose into it on EVERY gate transition automatically.
#
#   "[.taskplane/, .em-review/] stay local to the checkout and git-ignored
#    on BOTH plans ... Only knowledge is ever shared on a team plan"
#   "publishing is a deliberate human act, and the lint gate is a backstop"
#
# Both were false. Review findings are model-authored free text — precisely
# the class `tp kb lint` exists to keep out of a committed store — and no
# human act was involved: a gate transition published them.
#
# The rule now: an IN-REPO store receives only artifacts that are already
# the repo's own committed material or derived structured data. Model-
# authored prose requires an explicit opt-in, and what was withheld is
# NAMED in the return value rather than silently dropped. An EXTERNAL
# (personal) store is unchanged — it is outside the repo and private, which
# is the situation PRIVACY.md actually describes.
_MODEL_AUTHORED = ("findings.json", "report.md", "retro.md",
                   "dashboard.html")


def publish_prose_opt_in() -> bool:
    """TASKPLANE_PUBLISH_REVIEW=1 — the deliberate human act PRIVACY.md
    requires before model-authored review text lands in a committed store."""
    return os.environ.get("TASKPLANE_PUBLISH_REVIEW", "").strip().lower() in (
        "1", "true", "yes", "on")


def _publish_artifacts(ws: str) -> "str | None":
    """The store dir written, or None. `publish_report` returns the same
    plus what an in-repo store withheld."""
    return (publish_report(ws) or {}).get("root")


def publish_report(ws: str, state: dict | None = None) -> "dict | None":
    """Snapshot this track's decision artifacts into the active store.

    Returns {"root": <dir>, "withheld": [names]} or None if nothing could be
    written. `state` is the loop state; omitted, it is read at call time.
    """
    import re as _re
    import shutil as _sh
    import time as _time
    try:
        if state is None:
            import loop as _loop          # runtime only — see module docstring
            state = _loop.load(ws) or {}
        slug = _re.sub(r"[^a-z0-9]+", "-",
                       str(state.get("goal") or "track").lower()
                       ).strip("-")[:60] or "track"
        import storage as _runtime_storage
        managed_root = _runtime_storage.managed_path(
            ws, "artifacts", "snapshots", slug)
        root = (managed_root if managed_root and
                tp.get_mode(ws)["store"] != "repo" else
                os.path.join(tp.store_root(ws), "artifacts", slug))
        os.makedirs(root, exist_ok=True)
        # D-0013: is this store INSIDE the repo (committed with the work)?
        in_repo = tp.get_mode(ws)["store"] == "repo"
        withheld: list = []

        def _cp(src):
            if not os.path.isfile(src):
                return
            name = os.path.basename(src)
            if (in_repo and name in _MODEL_AUTHORED
                    and not publish_prose_opt_in()):
                withheld.append(name)
                return
            _sh.copyfile(src, os.path.join(root, name))

        _cp(_runtime_storage.dashboard_path(ws))
        _cp(os.path.join(tp.tp_dir(ws), "retro.md"))
        _cp(os.path.join(ws, "plan", "plan.md"))
        _cp(os.path.join(ws, "plan", "tasks.json"))
        _locator = _runtime_storage.load_workspace_locator(ws)
        _review_root = (os.path.join(
            _locator["paths"]["artifacts"], "public") if _locator
            else os.path.join(ws, ".em-review"))
        _cp(os.path.join(_review_root, "findings.json"))
        _cp(os.path.join(_review_root, "report.md"))
        if withheld:
            tp.trace(ws, "artifacts_withheld", store="repo",
                     files=sorted(set(withheld)),
                     reason="model-authored review text is not auto-published "
                            "into a committed store (PRIVACY.md); set "
                            "TASKPLANE_PUBLISH_REVIEW=1 to publish it")

        with contextlib.suppress(Exception):
            import depgraph
            g = depgraph.load(ws)
            if g and g.get("modules"):
                # v2.3.0 (scalability): re-copy the graph snapshot only when
                # its content fingerprint changed — dumping megabytes into a
                # committed store on EVERY transition was pure churn.
                gp = os.path.join(root, "graph.json")
                new_fp = (g.get("meta") or {}).get("content_fingerprint")
                old_fp = None
                if new_fp and os.path.exists(gp):
                    try:
                        with open(gp, encoding="utf-8") as f:
                            old_fp = (json.load(f).get("meta") or {}).get(
                                "content_fingerprint")
                    except (OSError, ValueError):
                        old_fp = None
                if not new_fp or old_fp != new_fp:
                    with open(gp, "w", encoding="utf-8", newline="") as f:
                        json.dump(g, f, indent=1)
        with contextlib.suppress(Exception):
            # Late import: dashboard.py imports loop at module top. This
            # module does not import loop at all, so the cycle is gone —
            # the lateness here is only to keep a broken renderer from
            # taking the publish path down with it.
            import dashboard as _dash
            line = _dash.headline_loop(ws)
            if line:
                p = os.path.join(root, "HEADLINES.md")
                prev = ""
                size = 0
                if os.path.exists(p):
                    # v2.3.0 (scalability): read only the TAIL to find the
                    # last line — HEADLINES.md is append-forever, and a full
                    # read per gate made cumulative reads quadratic.
                    with open(p, "rb") as f:
                        f.seek(0, os.SEEK_END)
                        size = f.tell()
                        f.seek(max(0, size - 8192))
                        tail = f.read().decode("utf-8", "replace")
                    tail_lines = tail.rstrip().splitlines()
                    prev = tail_lines[-1] if tail_lines else ""
                if not prev.endswith(line):        # skip consecutive repeats
                    with open(p, "a", encoding="utf-8") as f:
                        if not prev:
                            f.write(f"# {state.get('goal', 'track')} — "
                                    "progress log\n\n")
                        stamp = _time.strftime("%Y-%m-%d %H:%M UTC",
                                               _time.gmtime())
                        f.write(f"- {stamp} · {line}\n")
                    # Cap the log: keep the header + the last 500 entries.
                    # Amortized — the full-file pass runs only past 256 KiB.
                    if size > 262144:
                        with open(p, encoding="utf-8") as f:
                            all_lines = f.read().splitlines()
                        head = [l for l in all_lines[:2]
                                if l.startswith("#") or not l.strip()]
                        body = [l for l in all_lines[len(head):] if l.strip()]
                        if len(body) > 500:
                            tmp = f"{p}.tmp.{os.getpid()}"
                            with open(tmp, "w", encoding="utf-8", newline="") as f:
                                f.write("\n".join(head + body[-500:]) + "\n")
                            os.replace(tmp, p)
        return {"root": root, "withheld": withheld}
    except Exception:
        return None


# Fail-open: a dashboard problem must never break the loop itself — but it
# must never do so SILENTLY. See the module docstring.
_VIEW_FAILED_WARNED = False


def _delivery_model(out: Mapping) -> dict:
    publication = out.get("dashboard_snapshot")
    snapshot = publication.get("snapshot") if isinstance(
        publication, Mapping) else None
    if isinstance(snapshot, Mapping):
        # This is the one frozen HostSurfaceSnapshot assembled by loop_status.
        # No presentation value or transition reread enters canonical bytes.
        return dict(snapshot)
    return {
        "schema": "taskplane.dashboard-delivery-model/v1",
        "transition": {key: value for key, value in out.items()
                       if key not in {"dashboard", "artifacts"}},
        "gate": dict((out.get("gate") or
                      ((out.get("status") or {}).get("gate")
                       if isinstance(out.get("status"), dict) else {}) or {})),
    }


def _delivery_host_acknowledgement(out: Mapping) -> "Mapping | None":
    for key in ("host_publication", "host_native_publication",
                "dashboard_host_publication"):
        publication = out.get(key)
        if isinstance(publication, Mapping) and isinstance(
                publication.get("acknowledgement"), Mapping):
            return publication["acknowledgement"]
    return None


def refresh_views(ws: str, out: dict) -> dict:
    """Render the dashboard and publish the gate snapshot; annotate `out`.

    Never raises. Every outcome — rendered, published, withheld, failed —
    is stated in the returned payload.
    """
    global _VIEW_FAILED_WARNED
    try:
        import storage as _runtime_storage
        p = _runtime_storage.dashboard_path(ws)
        step = _transition_step(out)
        human_gate = step in _HUMAN_DASHBOARD_STEPS
        logical_path = (p if _runtime_storage.load_workspace_locator(ws)
                        else ".taskplane/dashboard.html")
        fragment_path = os.path.splitext(p)[0] + ".fragment.html"
        delivery_root = os.path.join(os.path.dirname(p), "dashboard-delivery")
        rendered: dict = {}

        def presentation(_canonical: str) -> str:
            import dashboard as _dash
            fragment = _dash.report_widget(ws)
            canonical_model = json.loads(_canonical)
            canonical_values = canonical_model.get("values") \
                if isinstance(canonical_model.get("values"), Mapping) else {}
            fragment += _dash.render_wave_metrics_projection(
                canonical_values.get("wave_metrics"))
            rendered["fragment"] = fragment
            return fragment

        # Canonical JSON/Markdown and the product outcome are committed by
        # deliver_dashboard even when presentation() raises.  The renderer is
        # a true output port: it receives canonical bytes and cannot feed
        # semantic values back into the delivery model.
        delivery = deliver_dashboard(
            delivery_root, _delivery_model(out),
            inline_threshold=LARGE_DASHBOARD_INLINE_BYTES,
            html_renderer=presentation,
            host_acknowledgement=_delivery_host_acknowledgement(out))
        if delivery.get("inline"):
            inline = dict(delivery["inline"])
            inline_path = os.path.join(delivery_root, "dashboard.inline.html")
            _write_delivery_artifact(
                inline_path, inline.pop("content").encode("utf-8"))
            inline["path"] = inline_path
            delivery["inline"] = inline

        out["dashboard"] = {"path": logical_path, "delivery": delivery}
        html_ref = delivery["artifacts"]["html"]
        if html_ref["status"] == "available":
            with open(html_ref["path"], "rb") as stream:
                _write_delivery_artifact(p, stream.read())
            fragment = rendered.get("fragment")
            if isinstance(fragment, str):
                _write_delivery_artifact(
                    fragment_path, fragment.encode("utf-8"))
                out["dashboard"]["inline"] = {"path": fragment_path}
            out["dashboard"]["render"] = (
                "human gate — render inline.path with the host widget before "
                "asking for approval or rejection; path is fallback only"
                if human_gate else
                "refreshed for this internal transition — keep using this "
                "path as the progress reference; do not render or acknowledge "
                "it until a human gate or explicit status request")
        else:
            detail = str(html_ref.get("reason") or
                         "HTML presentation is unavailable")
            out["dashboard"].update({
                "error": detail,
                "render": "NOT refreshed — this transition's dashboard is "
                          "STALE or missing. Canonical JSON/Markdown remain "
                          "published; do not present the HTML as current."})
            with contextlib.suppress(Exception):
                tp.trace(ws, "dashboard_render_failed", error=detail)
            if not _VIEW_FAILED_WARNED:
                _VIEW_FAILED_WARNED = True
                print(f"taskplane: WARNING — dashboard render failed "
                      f"({detail}); canonical delivery remains available, "
                      "but the inline view is stale until repaired.",
                      file=sys.stderr)
        # WS-F: the engine can render, write and point at the artifact, and
        # has no way to see whether it reached a human — which is exactly how
        # "no inline dashboard, no report, nothing" kept happening against a
        # green engine. Record the demand so the SILENCE is countable.
        # Best-effort and non-blocking by contract: a workspace with no
        # ledger, or a failed write, changes nothing about this transition.
        with contextlib.suppress(Exception):
            import obligations
            oid = obligations.issue(
                ws, "render_dashboard",
                detail="show the refreshed dashboard inline",
                # This is one durable dashboard obligation for the whole
                # delivery loop. The current step still controls whether the
                # payload asks for delivery, but must not mint a new debt at
                # every transition.
                step="loop",
                artifact=p, key=logical_path)
            if oid:
                out["dashboard"]["obligation"] = oid
                if human_gate:
                    out["dashboard"]["ack"] = (
                        f"after delivering it, run once: tp ack {oid} "
                        f"--delivered {logical_path}")
    except Exception as exc:
        detail = f"{exc.__class__.__name__}: {exc}"
        with contextlib.suppress(Exception):
            import storage as _runtime_storage
            logical_path = _runtime_storage.dashboard_path(ws)
        out["dashboard"] = {
            "path": locals().get("logical_path", ".taskplane/dashboard.html"),
            "error": detail,
            "render": "NOT refreshed — this transition's dashboard is STALE "
                      "or missing. Do not present it as current; say so and "
                      "repair the renderer."}
        with contextlib.suppress(Exception):
            tp.trace(ws, "dashboard_render_failed", error=detail)
        if not _VIEW_FAILED_WARNED:
            _VIEW_FAILED_WARNED = True
            print(f"taskplane: WARNING — dashboard render failed ({detail}); "
                  "the inline view is stale for this workspace until it is "
                  "repaired.", file=sys.stderr)
    try:
        rep = publish_report(ws)
    except Exception as exc:                      # pragma: no cover - defensive
        rep = None
        with contextlib.suppress(Exception):
            tp.trace(ws, "artifacts_publish_failed",
                     error=f"{exc.__class__.__name__}: {exc}")
    if rep and rep.get("root"):
        note = ("gate-state snapshot (dashboard, plan, findings, graph, "
                "HEADLINES.md) — on a team store commit it with the work so "
                "the org sees progress; future sessions read it instead of "
                "re-deriving (token cache)")
        entry = {"path": tp.to_posix(rep["root"]), "note": note}
        if rep.get("withheld"):
            # D-0013: name what was NOT published. A snapshot that silently
            # omits the review is worse than one that omits it loudly.
            entry["withheld"] = sorted(set(rep["withheld"]))
            entry["withheld_reason"] = (
                "model-authored review text is not auto-published into a "
                "COMMITTED (in-repo) store — PRIVACY.md states publishing is "
                "a deliberate human act. It stays in the private run store "
                "(or legacy local review paths). Set TASKPLANE_PUBLISH_REVIEW=1 "
                "to publish it deliberately.")
        out["artifacts"] = entry
    return out
