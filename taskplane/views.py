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

import contextlib
import json
import os
import sys

import taskplane_lite as tp


_REVISION_ID_KEYS = ("target_fingerprint", "context_fingerprint",
                     "findings_fingerprint", "canonical_revision")


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
# the repo, meant to be committed. This function copied `.em-review/
# findings.json`, `.em-review/report.md`, `retro.md` and the rendered
# dashboard (which embeds review prose) into it on EVERY gate transition,
# automatically. PRIVACY.md says the opposite in two places:
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
        root = os.path.join(tp.store_root(ws), "artifacts", slug)
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

        _cp(os.path.join(tp.tp_dir(ws), "dashboard.html"))
        _cp(os.path.join(tp.tp_dir(ws), "retro.md"))
        _cp(os.path.join(ws, "plan", "plan.md"))
        _cp(os.path.join(ws, "plan", "tasks.json"))
        _cp(os.path.join(ws, ".em-review", "findings.json"))
        _cp(os.path.join(ws, ".em-review", "report.md"))
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
        p = os.path.join(tp.tp_dir(ws), "dashboard.html")
        tmp = f"{p}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(doc)
        os.replace(tmp, p)
        out["dashboard"] = {
            # logical pointer, not a path: os.path.join made
            # this '\\' on Windows and the goldens disagreed
            "path": ".taskplane/dashboard.html",
            "render": "refreshed for this transition — show it "
                      "(mcp__visualize__show_widget) before "
                      "proceeding; the dashboard is the interface "
                      "the human governs through"}
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
                step=str((out.get("state") or {}).get("step")
                         or out.get("step") or ""),
                artifact=p, key=".taskplane/dashboard.html")
            if oid:
                out["dashboard"]["obligation"] = oid
                out["dashboard"]["ack"] = (
                    f"after showing it, run: tp ack {oid} — an obligation "
                    "left unacknowledged is recorded as not shown")
    except Exception as exc:
        detail = f"{exc.__class__.__name__}: {exc}"
        out["dashboard"] = {
            "path": ".taskplane/dashboard.html",
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
                "a deliberate human act. It stays in the checkout at "
                ".em-review/ and .taskplane/. Set TASKPLANE_PUBLISH_REVIEW=1 "
                "to publish it deliberately.")
        out["artifacts"] = entry
    return out
