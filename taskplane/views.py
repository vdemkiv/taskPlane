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
try:
    from . import host_native
except (ImportError, ValueError):
    from taskplane import host_native


# Compatibility alias; host_native owns the canonical identity schema beside
# its validator and every presentation facade consumes that single value.
_REVISION_ID_KEYS = host_native.REVISION_ID_KEYS

# A transition always refreshes the durable dashboard, but only a HUMAN gate
# needs the model to stop, surface it, and acknowledge delivery.  Issuing and
# acknowledging a fresh render obligation at every internal PM/Plan/Evaluate
# hop turned progress reporting into repeated CLI work and repeatedly sent the
# same HTML through model context.  Keep one open, deterministic obligation
# through the internal transitions; the gate payload carries the one ack.
_HUMAN_DASHBOARD_STEPS = frozenset({
    "design_approval", "plan_approval", "selection", "signoff", "escalated",
})

LARGE_DASHBOARD_INLINE_BYTES = host_native.LARGE_DASHBOARD_INLINE_BYTES
_NO_EXPECTED_HEAD = host_native._NO_EXPECTED_HEAD
canonical_dashboard_bytes = host_native.canonical_dashboard_bytes
dashboard_freshness_state = host_native.dashboard_freshness_state
dashboard_publication_receipt_fingerprint = (
    host_native.dashboard_publication_receipt_fingerprint)
validate_dashboard_publication_receipt = (
    host_native.validate_dashboard_publication_receipt)
decode_dashboard_artifact = host_native.decode_dashboard_artifact
_write_delivery_artifact = host_native._write_delivery_artifact


def deliver_dashboard(output_dir: str, model: Mapping, *,
                      inline_threshold: int = LARGE_DASHBOARD_INLINE_BYTES,
                      html_renderer=None,
                      html_stylesheet: "str | None" = None,
                      host_acknowledgement: "Mapping | None" = None,
                      expected_head=_NO_EXPECTED_HEAD) -> dict:
    """Compatibility facade over the acyclic host delivery implementation."""
    import dashboard as _dashboard
    return host_native.deliver_dashboard(
        output_dir, model, inline_threshold=inline_threshold,
        inline_renderer=_dashboard.render_lossless_dashboard_inline,
        html_renderer=html_renderer,
        html_stylesheet=html_stylesheet,
        host_acknowledgement=host_acknowledgement,
        expected_head=expected_head)
def _transition_step(out: dict) -> str:
    state = out.get("status") or out.get("state") or {}
    return str((state.get("step") if isinstance(state, dict) else None)
               or out.get("step") or "")


canonical_revision_identity = host_native.canonical_revision_identity
canonical_report_projection = host_native.canonical_report_projection


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
        import dashboard as _dash

        def presentation(_canonical: str) -> str:
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
            html_stylesheet=_dash.dashboard_document_style(),
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
        if html_ref["status"] == "available":
            # WS-F: the engine can render, write and point at the artifact,
            # but cannot see whether it reached a human. Record that demand
            # only after the exact HTML artifact exists; issuing it for a
            # stale or missing file would create fictional delivery debt.
            with contextlib.suppress(Exception):
                import obligations
                oid = obligations.issue(
                    ws, "render_dashboard",
                    detail="show the refreshed dashboard inline",
                    step="loop", artifact=p, key=logical_path)
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
