from __future__ import annotations

import inspect
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from taskplane import dashboard
from taskplane import text_runtime
from taskplane.host_native import HostSurfaceSnapshot


def _snapshot(*, statuses=None, actions=("Retry evidence", "Inspect")):
    statuses = statuses or {}
    values = {
        name: {
            "status": statuses.get(name, "ready"),
            "provenance": f"audit:{name}",
            "summary": f"Canonical {name} evidence",
            "items": [],
        }
        for name in dashboard.HOST_DASHBOARD_COMPONENTS
    }
    return HostSurfaceSnapshot.create(
        workflow_id="wf", run_id="run", target="repo", revision="abc123",
        sequence=7, stage="review", state="awaiting_approval", values=values,
        evidence=("sha256:evidence",), safe_actions=actions,
    )


def _projection(*, statuses=None, actions=("Retry evidence", "Inspect")):
    return dashboard.native_dashboard_projection(
        _snapshot(statuses=statuses, actions=actions), host="codex")


def _render_standalone(tmp_path, monkeypatch, goal):
    monkeypatch.setattr(dashboard, "_read_trace_all",
                        lambda _ws, stats=None: [])
    monkeypatch.setattr(dashboard, "_load_loop", lambda _ws: {
        "step": "execute", "goal": goal, "tasks": [], "parallel": False,
    })
    monkeypatch.setattr(dashboard, "_counts", lambda _ws: {
        "decisions": 0, "requirements": 0, "debt": 0,
        "modules": 0, "edges": 0,
    })
    monkeypatch.setattr(dashboard.tp, "load_active", lambda _ws: None)
    monkeypatch.setattr(dashboard, "_render_pipeline",
                        lambda _state, _step: "<span>static state</span>")
    monkeypatch.setattr(dashboard, "_bounded_stage_view", lambda _ws: {})
    monkeypatch.setattr(dashboard, "render_stage_lineage", lambda _view: "")
    destination = tmp_path / "dashboard.html"
    dashboard.render(str(tmp_path), str(destination), locale="en")
    return destination.read_text(encoding="utf-8")


def test_m01_reduced_motion_disables_infinite_status_pulse(tmp_path, monkeypatch):
    goal = "x" * 79 + "👨‍👩‍👧‍👦" + "tail"
    markup = _render_standalone(tmp_path, monkeypatch, goal)
    assert "@media (prefers-reduced-motion: reduce)" in markup
    reduced = markup.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert "animation:none!important" in reduced
    assert 'id="tp-motion-control"' in markup
    assert 'aria-pressed="false"' in markup
    assert "classList.toggle('tp-motion-paused')" in markup
    assert "animation-play-state:paused!important" in markup
    # Motion is never the only status cue: the indicator is decorative and
    # the pipeline retains explicit text when all animation is disabled.
    assert '<span class="live" aria-hidden="true"></span>' in markup
    assert "static state" in markup


def test_m05_component_visual_state_matches_semantic_state(monkeypatch):
    names = dashboard.HOST_DASHBOARD_COMPONENTS
    statuses = {
        names[0]: "complete",
        names[1]: "pending",
        names[2]: "degraded",
        names[3]: "failed",
    }

    def verify(markup):
        assert 'data-state="success" role="status"><span aria-hidden="true">&#10003;' in markup
        assert 'data-state="pending" role="status"><span aria-hidden="true">&#8230;' in markup
        assert 'data-state="warning" role="status"><span aria-hidden="true">&#9888;' in markup
        assert 'data-state="failure" role="alert"><span aria-hidden="true">&#10007;' in markup
        assert "Component state: failed" in markup
        assert "Recovery action: Retry evidence" in markup
        for token in ("--tp-success", "--tp-pending", "--tp-warning",
                      "--tp-failure"):
            assert token in markup

    verify(dashboard.render_native_dashboard_surface(
        _projection(statuses=statuses)))
    # Adversarial sensitivity: restoring the old all-success classifier must
    # invalidate this exact production-path proof.
    monkeypatch.setattr(dashboard, "_dashboard_component_state",
                        lambda _status: ("success", "✓"))
    with pytest.raises(AssertionError):
        verify(dashboard.render_native_dashboard_surface(
            _projection(statuses=statuses)))


def test_m05_diagnostic_actions_are_not_mislabelled_as_recovery(monkeypatch):
    failed = {dashboard.HOST_DASHBOARD_COMPONENTS[0]: "failed"}

    def verify_no_false_recovery(markup):
        assert 'data-state="failure" role="alert"' in markup
        assert "Recovery action:" not in markup
        assert 'class="tp-recovery"' not in markup

    projection = _projection(
        statuses=failed, actions=("Inspect diagnostics", "Export evidence"))
    verify_no_false_recovery(
        dashboard.render_native_dashboard_surface(projection))
    assert dashboard._dashboard_recovery_action(
        ("Inspect", "request-changes with evidence"), "failure") \
        == "request-changes with evidence"
    assert dashboard._dashboard_recovery_action(
        ("Open full details", "Open recovery controls"), "failure") \
        == "Open recovery controls"

    # Mutation proof: the former arbitrary-first-action fallback must make
    # the negative assertion fail, rather than silently blessing Inspect.
    monkeypatch.setattr(
        dashboard, "_dashboard_recovery_action",
        lambda actions, state: str(actions[0]) if state == "failure" else None)
    with pytest.raises(AssertionError):
        verify_no_false_recovery(
            dashboard.render_native_dashboard_surface(projection))


def test_m08_dashboard_uses_locale_catalog_with_deterministic_fallback(tmp_path):
    catalog = text_runtime.load_catalog("ar-EG")
    assert catalog.requested_locale == "ar-EG"
    assert catalog.resolved_locales == ("ar", "en")
    markup = dashboard.render_native_dashboard_surface(
        _projection(), locale="ar-EG")
    assert 'lang="ar-EG" dir="auto"' in markup
    assert 'aria-label="لوحة سير عمل Taskplane"' in markup
    assert "تفاصيل لوحة المعلومات" in markup
    assert "إجراءات لوحة المعلومات" in markup
    assert "محرر المحادثة" in markup
    # Regional Arabic inherits untranslated evidence keys from explicit en.
    assert "Canonical findings evidence" in markup

    locale_dir = tmp_path / "locales"
    locale_dir.mkdir()
    (locale_dir / "en.json").write_text(
        json.dumps({"messages": {"probe": "English fallback"}}),
        encoding="utf-8")
    (locale_dir / "xx.json").write_text("{broken", encoding="utf-8")
    fallback = text_runtime.load_catalog("xx-ZZ", catalog_dir=locale_dir)
    assert fallback.format("probe") == "English fallback"
    assert fallback.resolved_locales == ("en",)
    assert fallback.errors and fallback.errors[0].startswith("xx:")


def test_m09_plural_rules_support_locale_categories_beyond_one_other():
    template = (
        "{n, plural, zero {zero} one {one} two {two} few {few} "
        "many {many} other {other}}"
    )
    expected = {
        "ar": {0: "zero", 1: "one", 2: "two", 3: "few", 11: "many",
               100: "other"},
        "pl": {1: "one", 2: "few", 5: "many", 1.5: "other"},
        "ru": {1: "one", 3: "few", 11: "many", 1.5: "other"},
        "lt": {1: "one", 2: "few", 11: "other", "1.0": "one",
               1.5: "many", 2.75: "many"},
        "cy": {0: "zero", 1: "one", 2: "two", 3: "few", 6: "many",
               4: "other"},
    }
    for locale, cases in expected.items():
        for count, category in cases.items():
            assert text_runtime.format_message(
                template, locale=locale, values={"n": count}) == category
    assert dashboard._msg("n_findings", locale="ar-EG", n=2) == "2 نتيجتان"


def test_l03_visible_truncation_is_grapheme_safe_and_accessible_text_is_full(
        tmp_path, monkeypatch):
    jamo_one = "각"
    jamo_two = "나"
    clusters = text_runtime.grapheme_clusters(
        "e\u0301👨‍👩‍👧‍👦क्ष🇺🇦" + jamo_one + jamo_two)
    assert clusters == (
        "e\u0301", "👨‍👩‍👧‍👦", "क्ष", "🇺🇦", jamo_one, jamo_two)
    # GB6–GB8 also compose prebuilt LV/LVT syllables with trailing Jamo.
    assert text_runtime.grapheme_clusters("각나") == ("각", "나")
    hangul_bounded = text_runtime.truncate_graphemes(
        "x" * 259 + jamo_one + "tail", 260)
    assert hangul_bounded.visible.endswith(jamo_one + "…")
    assert hangul_bounded.full.endswith(jamo_one + "tail")

    family = "👨‍👩‍👧‍👦"
    full_scenario = "x" * 259 + family + " after"
    finding = {
        "severity": "med", "domain": "i18n", "title": "cluster",
        "scenario": full_scenario, "fix": "e\u0301" * 221,
    }
    markup = dashboard._compact_card(finding)
    assert family + "…" in markup
    assert f'<span class="sr">{full_scenario}</span>' in markup
    assert "👨…" not in markup and "👨‍…" not in markup

    goal = "x" * 79 + family + "tail"
    standalone = _render_standalone(tmp_path, monkeypatch, goal)
    assert family + "…" in standalone
    assert f'<span class="sr">{goal}</span>' in standalone
    # Guard the other approved display boundary against a return to slicing
    # escaped/code-point text in the recent-decision production renderer.
    assert "_visible_text(d.get(\"title\"" in inspect.getsource(
        dashboard._context_panel)


def test_emitted_host_controller_remains_valid_javascript():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    markup = dashboard.render_native_dashboard_surface(_projection())
    controller = markup.split("<script>", 1)[1].split("</script>", 1)[0]
    completed = subprocess.run(
        [node, "-e", "new Function(process.argv[1])", controller],
        text=True, encoding="utf-8", errors="replace", capture_output=True,
        check=False)
    assert completed.returncode == 0, completed.stderr or completed.stdout
