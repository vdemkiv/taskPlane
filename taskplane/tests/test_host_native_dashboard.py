from __future__ import annotations

import pytest

from taskplane.dashboard import (
    HOST_DASHBOARD_COMPONENTS,
    carousel_pages,
    native_dashboard_projection,
)
from taskplane.host_native import HostSurfaceSnapshot


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


def test_inline_contract_is_conversational_responsive_and_accessible():
    projected = native_dashboard_projection(_snapshot(), host="codex")
    ui = projected["presentation"]
    assert ui["card"] == {
        "single_purpose": True, "max_primary_actions": 2,
        "nested_scroll": False, "deep_navigation": False,
        "rich_detail_surface": "fullscreen", "composer_retained": True,
    }
    assert ui["responsive"]["min_viewport_px"] == 320
    assert ui["accessibility"] == {
        "semantic_labels": True, "alt_text": True,
        "keyboard_navigation": True, "visible_focus": True,
        "text_scale_percent": 200, "reduced_motion": True,
        "status_not_color_only": True, "contrast": "WCAG-AA",
        "fonts": "system", "tokens": "host-system",
        "themes": ["light", "dark"],
    }


def test_more_than_two_actions_moves_extras_to_fullscreen_detail():
    snapshot = HostSurfaceSnapshot.create(
        workflow_id="wf", run_id="run", target="repo", revision="abc",
        sequence=1, stage="review", state="waiting", values={},
        safe_actions=("approve", "decline", "inspect", "export"),
    )
    projection = native_dashboard_projection(snapshot, host="claude")
    assert projection["presentation"]["primary_actions"] == ["approve", "decline"]
    assert projection["presentation"]["detail_actions"] == ["inspect", "export"]
