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
import json
import os
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


def _embedded_html(body: str, canonical: bytes) -> bytes:
    encoded = base64.b64encode(canonical).decode("ascii")
    return (
        '<!DOCTYPE html><html lang="en"><meta charset="utf-8"><body>'
        + body
        + '<script type="application/x-taskplane-json-base64" '
          f'data-taskplane-canonical="true">{encoded}</script>'
          '</body></html>').encode("utf-8")


def deliver_dashboard(output_dir: str, model: Mapping, *,
                      inline_threshold: int = LARGE_DASHBOARD_INLINE_BYTES,
                      html_renderer=None) -> dict:
    """Publish a lossless, size-appropriate dashboard artifact set.

    JSON and complete Markdown are required.  Inline is selected only when
    the canonical semantic bytes fit the declared boundary.  HTML is a
    best-effort convenience and cannot change publication or gate state.
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
    json_path = os.path.join(root, "dashboard.json")
    markdown_path = os.path.join(root, "dashboard.md")
    markdown = (
        "# Taskplane dashboard\n\n"
        "Canonical complete dashboard evidence (JSON):\n\n"
        f"{_CANONICAL_START}\n```json\n{canonical_text}\n```\n"
        f"{_CANONICAL_END}\n").encode("utf-8")
    _write_delivery_artifact(json_path, canonical)
    _write_delivery_artifact(markdown_path, markdown)
    artifacts = {"json": _artifact_ref(json_path, canonical),
                 "markdown": _artifact_ref(markdown_path, markdown)}

    inline = None
    mode = "complete-markdown"
    if len(canonical) <= inline_threshold:
        import dashboard as _dashboard
        content = _dashboard.render_lossless_dashboard_inline(canonical_text)
        inline = {"format": "html", "content": content, "complete": True,
                  "semantic_bytes": len(canonical)}
        mode = "inline"

    html_path = os.path.join(root, "dashboard.html")
    if html_renderer is None:
        artifacts["html"] = {"status": "unavailable", "path": html_path,
                             "reason": "optional HTML was not requested"}
    else:
        try:
            body = str(html_renderer(canonical_text))
            html_payload = _embedded_html(body, canonical)
            _write_delivery_artifact(html_path, html_payload)
            artifacts["html"] = _artifact_ref(html_path, html_payload)
        except Exception as exc:
            artifacts["html"] = {
                "status": "unavailable", "path": html_path,
                "reason": f"{exc.__class__.__name__}: {exc}"}

    return {
        "schema": "taskplane.dashboard-delivery/v1", "status": "published",
        "mode": mode, "semantic_bytes": len(canonical),
        "semantic_sha256": hashlib.sha256(canonical).hexdigest(),
        "gate": dict(model.get("gate") or {}), "inline": inline,
        "artifacts": artifacts,
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


def refresh_views(ws: str, out: dict) -> dict:
    """Render the dashboard and publish the gate snapshot; annotate `out`.

    Never raises. Every outcome — rendered, published, withheld, failed —
    is stated in the returned payload.
    """
    global _VIEW_FAILED_WARNED
    try:
        import dashboard as _dash
        frag = _dash.report_widget(ws)
        # The durable artifact is a real report document, not a raw widget
        # fragment.  This uses the same 940px canvas, palette, dark-mode
        # variables and section hierarchy as engineering-review reports, so
        # Codex/Claude/file delivery all render the same bytes reliably.
        doc = _dash.standalone_document(
            [frag], title="taskplane — mission control")
        import storage as _runtime_storage
        p = _runtime_storage.dashboard_path(ws)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = f"{p}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(doc)
        os.replace(tmp, p)
        fragment_path = os.path.splitext(p)[0] + ".fragment.html"
        fragment_tmp = f"{fragment_path}.tmp.{os.getpid()}"
        with open(fragment_tmp, "w", encoding="utf-8", newline="") as f:
            f.write(frag)
        os.replace(fragment_tmp, fragment_path)
        step = _transition_step(out)
        human_gate = step in _HUMAN_DASHBOARD_STEPS
        logical_path = (p if _runtime_storage.load_workspace_locator(ws)
                        else ".taskplane/dashboard.html")
        out["dashboard"] = {
            # logical pointer, not a path: os.path.join made
            # this '\\' on Windows and the goldens disagreed
            "path": logical_path,
            "inline": {"path": fragment_path},
            "render": (
                "human gate — render inline.path with the host widget before "
                "asking for approval or rejection; path is fallback only"
                if human_gate else
                "refreshed for this internal transition — keep using this "
                "path as the progress reference; do not render or acknowledge "
                "it until a human gate or explicit status request")}
        # R-0001: delivery is a production concern, not a test-only helper.
        # The transition payload is the canonical semantic input; the rendered
        # fragment is retained as evidence but never replaces canonical JSON.
        # Very large values bypass inline transport automatically and point to
        # the complete Markdown artifact instead.
        delivery_root = os.path.join(os.path.dirname(p), "dashboard-delivery")
        delivery_model = {
            "schema": "taskplane.dashboard-delivery-model/v1",
            "transition": {key: value for key, value in out.items()
                           if key not in {"dashboard", "artifacts"}},
            "rendered_dashboard": {"fragment": frag},
            "gate": dict((out.get("gate") or
                          ((out.get("status") or {}).get("gate")
                           if isinstance(out.get("status"), dict) else {}) or
                          {})),
        }
        try:
            delivery = deliver_dashboard(
                delivery_root, delivery_model,
                inline_threshold=LARGE_DASHBOARD_INLINE_BYTES,
                html_renderer=lambda _canonical: doc)
            if delivery.get("inline"):
                inline = dict(delivery["inline"])
                inline_path = os.path.join(delivery_root, "dashboard.inline.html")
                _write_delivery_artifact(
                    inline_path, inline.pop("content").encode("utf-8"))
                inline["path"] = inline_path
                delivery["inline"] = inline
            out["dashboard"]["delivery"] = delivery
        except Exception as exc:
            detail = f"{exc.__class__.__name__}: {exc}"
            out["dashboard"]["delivery"] = {
                "schema": "taskplane.dashboard-delivery/v1",
                "status": "unavailable", "gating": False, "reason": detail,
                "action": "retry artifact delivery"}
            with contextlib.suppress(Exception):
                tp.trace(ws, "artifact_delivery_unavailable", error=detail)
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
