from __future__ import annotations

from html.parser import HTMLParser
import re
import shutil
import subprocess

import pytest

from taskplane.dashboard import (
    HOST_DASHBOARD_COMPONENTS,
    carousel_pages,
    native_dashboard_projection,
    render_canonical_dashboard_snapshot,
    render_native_dashboard_surface,
)
from taskplane.host_native import (
    HostSurfaceSnapshot,
    refresh_dashboard_snapshot,
    select_dashboard_source,
)
from taskplane import wave_metrics


def _snapshot(items=()):
    values = {
        name: {"status": "ready", "items": list(items),
               "provenance": f"audit:{name}"}
        for name in HOST_DASHBOARD_COMPONENTS
    }
    return HostSurfaceSnapshot.create(
        workflow_id="wf", run_id="run", target="repo", revision="abc",
        sequence=4, stage="review", state="awaiting_approval", values=values,
        evidence=("sha256:evidence",), safe_actions=("inspect", "approve"),
    )


def _items(n):
    return [{"id": f"F-{i:03d}", "title": f"Finding {i}",
             "kind": "odd" if i % 2 else "even"} for i in range(n)]


def _semantic(projection):
    return {k: v for k, v in projection.items() if k != "presentation"}










def test_managed_v4_routes_to_the_selected_active_stage():
    manifest = {
        "schema": "taskplane.run/v4", "run_id": "managed-v4",
        "revision": 8,
        "active_stage_projection": {
            "active_stage_ids": ["design-1"],
            "foreground_stage_id": "design-1",
        },
        "stage_heads": {
            "design-1": {"summary": {"stage_kind": "design"}},
        },
    }

    source = select_dashboard_source(
        "/managed-workspace",
        locator_loader=lambda _workspace: {"run_id": "managed-v4"},
        manifest_loader=lambda _workspace, _locator: manifest,

        manifest_validator=lambda candidate: candidate,
        error_formatter=str,
    )

    assert source["mode"] == "v4"
    assert source["status"] == "ready"
    assert source["target"] == "design-1"
    assert source["state"]["stage_view"]["current_stage"] == {
        "stage_kind": "design",
    }


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"),
                                         float("-inf")])
def test_managed_v4_non_finite_json_values_are_non_actionable(non_finite):
    manifest = {
        "schema": "taskplane.run/v4", "run_id": "managed-v4",
        "revision": 8,
        "active_stage_projection": {
            "active_stage_ids": ["design-1"],
            "foreground_stage_id": "design-1",
        },
        "stage_heads": {
            "design-1": {"summary": {"stage_kind": "design"}},
        },
        "telemetry": {"tokens": non_finite},
    }

    source = select_dashboard_source(
        "/managed-workspace",
        locator_loader=lambda _workspace: {"run_id": "managed-v4"},
        manifest_loader=lambda _workspace, _locator: manifest,

        manifest_validator=lambda candidate: candidate,
        error_formatter=lambda exc: f"{exc.__class__.__name__}: {exc}",
    )

    assert source["mode"] == "v4"
    assert source["status"] == "corrupt"
    assert source["state"] is None
    assert source["target"] == "active-stage"
    assert source["source_fingerprint"]
    assert "not JSON compliant" in " ".join(source["evidence"])


@pytest.mark.parametrize(("case", "malformed", "mode", "target"), [
    ("locator-load-error", None, "managed", "run"),
    ("invalid-locator", float("nan"), "managed", "run"),
    ("manifest-load-error", None, "managed", "run"),
    ("root-manifest", float("nan"), "managed", "run"),
    ("root-manifest", float("inf"), "managed", "run"),
    ("root-manifest", float("-inf"), "managed", "run"),
    ("unsupported-manifest", {"tokens": float("nan")}, "managed", "run"),
    ("unsupported-manifest", {"tokens": object()}, "managed", "run"),
    ("v4-manifest", {"tokens": float("-inf")}, "v4", "active-stage"),
])
def test_every_corrupt_dashboard_source_path_has_a_safe_fingerprint(
        case, malformed, mode, target):
    locator = {"run_id": "managed-run"}
    manifest = {
        "schema": "taskplane.run/v4", "run_id": "managed-run",
        "active_stage_projection": {
            "active_stage_ids": [], "foreground_stage_id": None,
        },
        "stage_heads": {},
    }

    def load_locator(_workspace):
        if case == "locator-load-error":
            raise ValueError("locator unavailable")
        if case == "invalid-locator":
            return {"run_id": malformed}
        return locator

    def load_manifest(_workspace, _locator):
        if case == "manifest-load-error":
            raise ValueError("manifest unavailable")
        if case == "root-manifest":
            return malformed
        if case == "unsupported-manifest":
            return {
                "schema": "taskplane.run/v99", "run_id": "managed-run",
                "telemetry": malformed,
            }
        if case == "v4-manifest":
            return {**manifest, "telemetry": malformed}
        return manifest

    source = select_dashboard_source(
        "/managed-workspace", locator_loader=load_locator,
        manifest_loader=load_manifest,
        manifest_validator=lambda candidate: candidate,
        error_formatter=lambda exc: f"{exc.__class__.__name__}: {exc}",
    )

    assert source["mode"] == mode
    assert source["status"] == "corrupt"
    assert source["run_id"]
    assert source["target"] == target
    assert source["state"] is None
    assert isinstance(source["source_fingerprint"], str)
    assert len(source["source_fingerprint"]) == 64
    assert source["evidence"]
    assert all(isinstance(item, str) and item for item in source["evidence"])


def test_managed_manifest_identity_mismatch_refuses_without_state_fallback():
    source = select_dashboard_source(
        "/managed-workspace",
        locator_loader=lambda _workspace: {"run_id": "locator-run"},
        manifest_loader=lambda _workspace, _locator: {
            "schema": "taskplane.run/v4", "run_id": "foreign-run",
        },

        manifest_validator=lambda _manifest: pytest.fail(
            "identity mismatch must be refused before v4 validation"),
        error_formatter=str,
    )

    assert source["status"] == "corrupt"
    assert source["state"] is None
    assert source["run_id"] == "locator-run"






def test_refreshed_dashboard_carries_the_canonical_root_hygiene_seal():
    root = {
        "status": "open", "conformance": "pass", "canary_eligible": True,
        "override": None, "host": {"adapter": "codex", "runtime": "native"},
        "session_pseudonym": "1" * 64, "seed_fingerprint": "2" * 64,
        "host_start_fingerprint": "3" * 64,
        "meter": {
            "turns": 2, "first_observed_input_tokens": 40_000,
            "peak_context_tokens": 45_000, "context_rent_tokens": 25_000,
            "resumed": False,
            "usage": {"total_tokens": 100_000,
                      "cached_input_tokens": 50_000},
        },
    }
    seal = wave_metrics.finalize_root_hygiene_canary(
        root, candidate_sha="a" * 40, worker_tokens=300_000)
    state = {
        "step": "retro", "run_id": "root-hygiene-dashboard",
        "requirement_id": "R-ROOT-HYGIENE", "baseline": "a" * 40,
        "root_hygiene_receipt": seal, "tasks": [],
    }
    committed = {}

    def commit_snapshot(_workspace, snapshot):
        committed["current"] = snapshot.to_dict()
        return {"current": committed["current"], "replayed": False}

    publication = refresh_dashboard_snapshot(
        "/managed-workspace", event_type="terminal", committed_at=1,
        settings_digest="settings",
        source_loader=lambda _workspace: {
            "status": "ready", "mode": "v4",
            "run_id": "root-hygiene-dashboard", "target": "retro",
            "revision": "a" * 40, "state": state,
            "evidence": ["terminal-root-seal"],
        },
        graph_projector=lambda _workspace, **_kwargs: {},
        metrics_projector=lambda value, **_kwargs: value,
        publication_loader=lambda _workspace: None,
        snapshot_committer=commit_snapshot,
        event_committer=lambda _workspace, _event: None,
        error_formatter=str,
    )

    assert publication["snapshot"]["values"]["root_hygiene_receipt"] == seal
    rendered = render_canonical_dashboard_snapshot(publication["snapshot"])
    assert seal["fingerprint"] in rendered
    assert all(str(total) in rendered for total in seal["totals"].values())


class _SurfaceDOM(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.nodes = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.nodes.append((tag, attrs, tuple(self.stack)))
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs), tuple(self.stack)))

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack:
                if self.stack.pop() == tag:
                    break

    def handle_data(self, data):
        self.text.append(data)

    def matching(self, tag, **attrs):
        return [(node_attrs, parents) for node_tag, node_attrs, parents
                in self.nodes if node_tag == tag and all(
                    node_attrs.get(key) == value for key, value in attrs.items())]


def _dom(markup):
    parser = _SurfaceDOM()
    parser.feed(markup)
    return parser


def _contrast(foreground, background):
    def luminance(color):
        channels = [int(color[index:index + 2], 16) / 255
                    for index in (1, 3, 5)]
        channels = [channel / 12.92 if channel <= .04045
                    else ((channel + .055) / 1.055) ** 2.4
                    for channel in channels]
        return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + .05) / (low + .05)


def test_codex_and_claude_project_equal_semantics_for_all_components():
    snapshot = _snapshot(_items(3))
    codex = native_dashboard_projection(snapshot, host="codex")
    claude = native_dashboard_projection(snapshot, host="claude")

    assert _semantic(codex) == _semantic(claude)
    assert [row["id"] for row in codex["components"]] == list(
        HOST_DASHBOARD_COMPONENTS)
    assert codex["safe_actions"] == ["inspect", "approve"]
    assert codex["evidence"] == ["sha256:evidence"]
    assert codex["presentation"]["style"] != claude["presentation"]["style"]


@pytest.mark.parametrize("count", [0, 1, 3, 8, 9, 120])
def test_carousel_is_deterministic_bounded_and_lossless(count):
    items = _items(count)
    first = carousel_pages(items)
    second = carousel_pages(items)
    assert first == second
    assert [item["id"] for p in first["pages"] for item in p["items"]] == [
        item["id"] for item in items]
    assert first["total_items"] == count
    assert all(3 <= len(p["items"]) <= 8 for p in first["pages"]) if count >= 3 else True
    assert [p["position"] for p in first["pages"]] == list(
        range(1, len(first["pages"]) + 1))
    assert all(p["total_pages"] == len(first["pages"])
               for p in first["pages"])


def test_carousel_rebalances_final_page_and_preserves_filter_navigation():
    pages = carousel_pages(_items(18), filters={"kind": "odd"}, current=2)
    assert [len(p["items"]) for p in pages["pages"]] == [6, 3]
    assert pages["current"] == 2
    assert pages["filters"] == {"kind": "odd"}
    assert pages["navigation"] == {"previous": 1, "next": None}
    assert all(item["kind"] == "odd" for page in pages["pages"]
               for item in page["items"])


def test_duplicate_or_missing_stable_identity_is_rejected():
    with pytest.raises(ValueError, match="stable unique id"):
        carousel_pages([{"id": "same"}, {"id": "same"}, {"title": "x"}])


@pytest.mark.parametrize("host", ["codex", "claude"])
@pytest.mark.parametrize("viewport,layout", [(320, "single-column"),
                                               (1440, "responsive-grid")])
def test_rendered_inline_surface_is_bounded_and_retains_composer(host, viewport,
                                                                 layout):
    projection = native_dashboard_projection(_snapshot(), host=host)
    markup = render_native_dashboard_surface(projection, viewport_px=viewport)
    dom = _dom(markup)

    root = dom.matching("div", **{"data-host": host})[0][0]
    assert root["data-layout"] == layout
    assert root["data-viewport-width"] == str(viewport)
    assert len(dom.matching("main", **{"aria-label": "Taskplane workflow dashboard"})) == 1
    cards = dom.matching("section")
    assert len(cards) == len(HOST_DASHBOARD_COMPONENTS)
    assert all(attrs.get("data-purpose") and attrs.get("aria-labelledby")
               and parents[-1] == "main" for attrs, parents in cards)
    composer = dom.matching("form", **{"aria-label": "Conversation composer"})
    assert len(composer) == 1 and "main" not in composer[0][1]
    assert "overflow:auto" not in markup and "overflow:scroll" not in markup
    assert "grid-template-columns:1fr" in markup


def test_rendered_actions_use_fullscreen_detail_without_deep_navigation():
    snapshot = HostSurfaceSnapshot.create(
        workflow_id="wf", run_id="run", target="repo", revision="abc",
        sequence=1, stage="review", state="waiting", values={},
        safe_actions=("approve", "decline", "inspect", "export"),
    )
    markup = render_native_dashboard_surface(
        native_dashboard_projection(snapshot, host="codex"))
    dom = _dom(markup)
    inline_buttons = [(attrs, parents) for attrs, parents in dom.matching("button")
                      if "nav" in parents]
    assert len(inline_buttons) == 2
    assert any(attrs.get("aria-controls") == "tp-fullscreen-detail"
               for attrs, _ in inline_buttons)
    dialog = dom.matching("dialog", id="tp-fullscreen-detail")
    assert len(dialog) == 1
    assert "position:fixed;inset:0;width:100vw;height:100vh" in markup
    assert dom.matching("button", **{"data-detail-close": "true"})
    assert "showModal" in markup and "addEventListener" in markup
    assert not dom.matching("a")


def test_details_controls_activate_modal_and_restore_keyboard_focus():
    """Execute the emitted controller against a minimal browser event model."""
    snapshot = HostSurfaceSnapshot.create(
        workflow_id="wf", run_id="run", target="repo", revision="abc",
        sequence=1, stage="review", state="waiting", values={},
        safe_actions=("approve", "decline", "inspect"),
    )
    markup = render_native_dashboard_surface(
        native_dashboard_projection(snapshot, host="codex"))
    controller = re.search(r"<script>(.*?)</script>", markup, re.DOTALL)
    assert controller, "rendered detail surface must include its controller"
    node = shutil.which("node")
    assert node, "Node.js is required to exercise the emitted host controller"
    harness = r'''
class Target {
  constructor() { this.listeners = {}; this.attributes = {}; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  emit(name, event = {}) { this.listeners[name](event); }
  focus() { document.activeElement = this; }
  setAttribute(name, value) { this.attributes[name] = value; if (name === "open") this.open = true; }
  removeAttribute(name) { delete this.attributes[name]; if (name === "open") this.open = false; }
}
const trigger = new Target();
const closer = new Target();
const dialog = new Target();
dialog.open = false;
dialog.showModal = function() { this.open = true; };
dialog.close = function() { this.open = false; };
const root = {querySelector(selector) {
  if (selector === "[data-detail-trigger]") return trigger;
  if (selector === "#tp-fullscreen-detail") return dialog;
  if (selector === "[data-detail-close]") return closer;
}};
global.document = {activeElement: trigger, currentScript: {closest() { return root; }}};
''' + controller.group(1) + r'''
trigger.emit("click");
if (!dialog.open || document.activeElement !== closer) process.exit(2);
let prevented = false;
dialog.emit("cancel", {preventDefault() { prevented = true; }});
if (dialog.open || !prevented || document.activeElement !== trigger) process.exit(3);
trigger.emit("click");
closer.emit("click");
if (dialog.open || document.activeElement !== trigger) process.exit(4);
'''
    completed = subprocess.run(
        [node, "-e", harness], text=True, encoding="utf-8",
        errors="replace", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_more_than_two_actions_moves_extras_to_fullscreen_detail():
    snapshot = HostSurfaceSnapshot.create(
        workflow_id="wf", run_id="run", target="repo", revision="abc",
        sequence=1, stage="review", state="waiting", values={},
        safe_actions=("approve", "decline", "inspect", "export"),
    )
    projection = native_dashboard_projection(snapshot, host="claude")
    assert projection["presentation"]["primary_actions"] == ["approve", "decline"]
    assert projection["presentation"]["detail_actions"] == ["inspect", "export"]


@pytest.mark.parametrize("host", ["codex", "claude"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_rendered_surface_accessibility_is_measured(host, theme):
    markup = render_native_dashboard_surface(
        native_dashboard_projection(_snapshot(), host=host), viewport_px=320,
        theme=theme, text_scale_percent=200, reduced_motion=True)
    dom = _dom(markup)
    root = dom.matching("div", **{"data-host": host})[0][0]
    assert root["data-theme"] == theme
    assert root["data-reduced-motion"] == "true"
    assert "font-size:200%" in root["style"]
    assert "transition:none" in root["style"]
    assert "@media(prefers-reduced-motion:reduce)" in markup
    assert "focus-visible" in markup and "outline:3px solid var(--tp-focus)" in markup

    labelled_regions = dom.matching("main") + dom.matching("nav") \
        + dom.matching("form") + dom.matching("dialog")
    assert all(attrs.get("aria-label") or attrs.get("aria-labelledby")
               for attrs, _ in labelled_regions)
    assert all(attrs.get("alt") for attrs, _ in dom.matching("img"))
    assert dom.matching("label", **{"for": "tp-message"})
    assert "&#10003;" in markup and "ready" in markup

    tokens = dict(re.findall(r"--tp-([a-z]+):(#[0-9a-f]{6})", root["style"]))
    assert _contrast(tokens["text"], tokens["background"]) >= 4.5
    assert _contrast(tokens["muted"], tokens["background"]) >= 4.5
    assert _contrast(tokens["focus"], tokens["background"]) >= 3.0

    focusable = [(tag, attrs) for tag, attrs, _ in dom.nodes
                 if tag in {"button", "textarea"}]
    assert focusable and all(attrs.get("tabindex") != "-1" for _, attrs in focusable)
