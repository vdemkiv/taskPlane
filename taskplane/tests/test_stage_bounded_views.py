"""Bounded stage read models for status, dashboard, Review, and Retro.

These tests deliberately persist real v4 heads and lineage rows, then make
opening either an immutable stage object or an execution tree fatal.  Default
human-facing views therefore have to remain projections over the already
bounded summaries in the run manifest.
"""
from __future__ import annotations

import builtins
import copy
import html
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TASKPLANE = ROOT / "taskplane"
if str(TASKPLANE) not in sys.path:
    sys.path.insert(0, str(TASKPLANE))

import dashboard  # noqa: E402
import loop  # noqa: E402
import loop_status  # noqa: E402
import retro  # noqa: E402
import run_store  # noqa: E402
import stage_entities  # noqa: E402
import storage  # noqa: E402


RUN_ID = "run-bounded-views"
PREDECESSOR_ID = "stage-plan-predecessor"
CURRENT_ID = "stage-build-current"
CHILD_ID = "stage-evaluate-child"
HOSTILE_REASON = '<img src=x onerror="alert(1)">'


def _reference(marker: str, kind: str = "stage-handoff") -> dict[str, object]:
    fingerprint = marker * 64
    return {
        "schema": "taskplane.artifact-reference/v1",
        "kind": kind,
        "fingerprint": fingerprint,
        "digest": fingerprint,
        "bytes": 128,
        "locator": f"artifact://{kind}/{fingerprint}",
        "transport": "artifact-reference",
    }


def _authority() -> dict[str, object]:
    return {
        "schema": "taskplane.stage-authority-binding/v1",
        "run_id": RUN_ID,
        "repository_id": "github.com/example/bounded-views",
        "repository_key": "github.com-example-bounded-views",
        "worktree_id": "bounded-views-worktree",
        "target_revision": "1" * 40,
        "worktree_revision": "2" * 40,
        "requirement_id": "R-0004",
        "requirement_revision": "4",
        "design_revision": "2",
        "design_fingerprint": "c" * 64,
        "actor": "human:test",
        "session_id": "bounded-views-session",
        "authority_revision": 7,
        "authority_fingerprint": "d" * 64,
    }


def _stage(
        stage_id: str, *, stage_kind: str = "build",
        parents: list[str] | None = None,
        predecessors: list[str] | None = None,
        handoff_marker: str = "6", terminal: bool = False,
        reason: str = "No further work is required in this stage.",
        created_at: str = "2026-08-21T12:00:00Z") -> dict[str, object]:
    stage = stage_entities.create_stage(
        run_id=RUN_ID,
        stage_id=stage_id,
        requirement={
            "id": "R-0004", "revision": "4", "fingerprint": "b" * 64,
        },
        design={"revision": "2", "fingerprint": "c" * 64},
        stage_kind=stage_kind,
        parent_stage_ids=parents or [],
        predecessor_stage_ids=predecessors or [],
        input_manifest_ref=_reference(handoff_marker),
        execution_root_id=f"execution-{stage_id}",
        deliverables=[f"deliverable-{stage_id}"],
        selected_artifacts=[],
        budget={"token_limit": 2_000},
        dependencies=[],
        contracts=["contract:delivery-lineage"],
        authority=_authority(),
        created_at=created_at,
    )
    if not terminal:
        return stage
    return stage_entities.terminalize_stage(
        stage,
        outcome="closed",
        actor="human:test",
        terminalized_at="2026-08-21T13:00:00Z",
        reason_code="complete",
        reason=reason,
    )


def _head(store: run_store.RunStore,
          stage: dict[str, object]) -> dict[str, object]:
    return {
        "object": store.put_stage_object(RUN_ID, stage),
        "summary": stage_entities.bounded_stage_summary(stage),
    }


def _lineage(
        child: dict[str, object], *, operation_id: str,
        parent_stage_id: str | None = None,
        predecessor_stage_ids: list[str] | None = None) -> dict[str, object]:
    return stage_entities.validate_lineage(stage_entities._lineage_row(
        parent_stage_id=parent_stage_id,
        child_stage_id=str(child["stage_id"]),
        predecessor_stage_ids=predecessor_stage_ids or [],
        input_manifest_ref=child["input_manifest_ref"],
        operation_id=operation_id,
    ))


def _git_workspace(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    source = path / "app.py"
    source.write_text("def answer():\n    return 42\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-qm", "fixture"],
        cwd=path, check=True,
    )
    return path


def _loop_state(*, step: str = "signoff") -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "goal": "Exercise bounded delivery views",
        "step": step,
        "tasks": [{
            "id": "t05", "scope": ["taskplane/**"],
            "status": "passed", "fix_cycles": 0,
        }],
        "current_task": 0,
        "max_fix_cycles": 2,
        "checkpoints": [],
    }


def _seed_v4(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
        foreground: str | None = CURRENT_ID,
        include_child: bool = True,
        sibling_active: bool = False,
        lineage_children: int = 0,
        step: str = "signoff",
        hostile_reason: str = HOSTILE_REASON,
        ) -> tuple[Path, run_store.RunStore, dict[str, object]]:
    workspace = _git_workspace(tmp_path / "checkout")
    identity = storage.identity_from_remote(
        "https://github.com/example/bounded-views.git",
        workspace=str(workspace),
    )
    store = run_store.RunStore(home=str(tmp_path / "home"))
    initial = store.create(
        identity,
        run_id=RUN_ID,
        checkout=str(workspace),
        host={"kind": "test"},
        target={"branch": "main"},
    )

    predecessor = _stage(
        PREDECESSOR_ID,
        stage_kind="plan",
        terminal=True,
        handoff_marker="4",
        reason=hostile_reason,
    )
    current = _stage(
        CURRENT_ID,
        stage_kind="build",
        predecessors=[PREDECESSOR_ID],
        handoff_marker="6",
        created_at="2026-08-21T13:00:01Z",
    )
    stages = [predecessor, current]
    lineage = [_lineage(
        current,
        operation_id="seed-successor",
        predecessor_stage_ids=[PREDECESSOR_ID],
    )]
    if include_child:
        child = _stage(
            CHILD_ID,
            stage_kind="evaluate",
            parents=[CURRENT_ID],
            handoff_marker="7",
            created_at="2026-08-21T13:00:02Z",
        )
        stages.append(child)
        lineage.append(_lineage(
            child,
            operation_id="seed-child",
            parent_stage_id=CURRENT_ID,
        ))
    if sibling_active:
        stages.append(_stage(
            "stage-build-sibling",
            handoff_marker="8",
            created_at="2026-08-21T13:00:03Z",
        ))
    for index in range(lineage_children):
        child_id = f"stage-history-child-{index:04d}"
        history_child = _stage(
            child_id,
            stage_kind="evaluate",
            parents=[CURRENT_ID],
            handoff_marker="9",
            terminal=True,
            created_at=f"2026-08-21T13:{index % 60:02d}:10Z",
        )
        stages.append(history_child)
        lineage.append(_lineage(
            history_child,
            operation_id=f"seed-history-{index:04d}",
            parent_stage_id=CURRENT_ID,
        ))

    heads = {str(stage["stage_id"]): _head(store, stage) for stage in stages}
    projection = stage_entities.active_stage_projection(
        heads, foreground_stage_id=foreground)

    def seed(_current: dict[str, object]) -> dict[str, object]:
        return {
            "changes": {
                "stage_heads": heads,
                "lineage": lineage,
                "active_stage_projection": projection,
            },
            "receipt": {
                "operation": "seed_bounded_views",
                "stage_ids": sorted(heads),
                "result": {"fixture": "bounded-views"},
            },
        }

    store.commit_stage_operation(
        RUN_ID,
        expected_revision=int(initial["revision"]),
        operation_id="seed-bounded-views",
        request_fingerprint="a" * 64,
        mutate=seed,
        validate_authority=lambda _current: None,
    )
    locator = {
        "schema": "taskplane.workspace/v1",
        "run_id": RUN_ID,
        "repo_id": identity.repo_id,
        "repository_key": identity.key,
        "checkout": str(workspace.resolve()),
        "primary_checkout": str(workspace.resolve()),
        "home": store.home,
        "paths": copy.deepcopy(initial["paths"]),
    }
    monkeypatch.setattr(
        storage, "load_workspace_locator",
        lambda checkout: copy.deepcopy(locator)
        if os.path.realpath(checkout) == os.path.realpath(workspace) else None,
    )
    loop.save(str(workspace), _loop_state(step=step))
    return workspace, store, store.load(RUN_ID)


def _forbid_deep_stage_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args, **_kwargs):
        raise AssertionError(
            "a bounded default view opened stage detail or an execution tree")

    monkeypatch.setattr(run_store.RunStore, "read_stage_object", fail)
    monkeypatch.setattr(storage, "stage_execution_root_for_run", fail)
    monkeypatch.setattr(storage, "stage_execution_root", fail)


def test_foreground_view_projects_current_predecessor_handoff_and_child(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, _store, _manifest = _seed_v4(tmp_path, monkeypatch)
    _forbid_deep_stage_reads(monkeypatch)

    view = loop_status.bounded_stage_view(str(workspace))

    assert view["schema"] == "taskplane.bounded-stage-view/v1"
    assert view["status"] == "v4"
    assert view["available"] is True
    assert view["run_id"] == RUN_ID
    assert view["current_stage"]["stage_id"] == CURRENT_ID
    assert view["current_stage"]["state"] == "active"
    assert [(row["stage_id"], row["outcome"])
            for row in view["predecessor_stages"]] == [
                (PREDECESSOR_ID, "closed")]
    assert view["handoff_fingerprint"] == "6" * 64
    assert view["child_stage_ids"] == [CHILD_ID]
    assert {row["stage_id"] for row in view["history"]} >= {
        PREDECESSOR_ID, CURRENT_ID, CHILD_ID,
    }
    assert any(
        row["child_stage_id"] == CURRENT_ID
        and row["predecessor_stage_ids"] == [PREDECESSOR_ID]
        for row in view["lineage"]
    )
    assert any(
        row["parent_stage_id"] == CURRENT_ID
        and row["child_stage_id"] == CHILD_ID
        for row in view["lineage"]
    )


def test_sole_active_stage_is_selected_without_a_foreground_guess(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, _store, _manifest = _seed_v4(
        tmp_path, monkeypatch, foreground=None, include_child=False)
    _forbid_deep_stage_reads(monkeypatch)

    view = loop_status.bounded_stage_view(str(workspace))

    assert view["status"] == "v4"
    assert view["available"] is True
    assert view["current_stage"]["stage_id"] == CURRENT_ID


def test_multiple_active_stages_without_foreground_are_visible_but_ambiguous(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, _store, _manifest = _seed_v4(
        tmp_path,
        monkeypatch,
        foreground=None,
        include_child=False,
        sibling_active=True,
    )
    _forbid_deep_stage_reads(monkeypatch)

    first = loop_status.bounded_stage_view(str(workspace))
    second = loop_status.bounded_stage_view(str(workspace))

    assert first == second
    assert first["status"] == "ambiguous"
    assert first["available"] is False
    assert first["current_stage"] is None
    assert first["handoff_fingerprint"] is None
    assert any(word in first["error"].lower()
               for word in ("multiple", "several", "ambiguous"))


def test_history_and_lineage_are_deterministic_and_capped_at_one_page(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, _store, _manifest = _seed_v4(
        tmp_path,
        monkeypatch,
        include_child=False,
        lineage_children=105,
    )
    _forbid_deep_stage_reads(monkeypatch)

    first = loop_status.bounded_stage_view(str(workspace), limit=100)
    second = loop_status.bounded_stage_view(str(workspace), limit=100)

    assert first == second
    assert first["limits"]["history"] == 100
    assert first["limits"]["lineage"] == 100
    assert len(first["history"]) == 100
    assert 0 < len(first["lineage"]) <= 100
    assert first["history"] == sorted(
        first["history"], key=lambda row: row["stage_id"])
    assert first["lineage"] == sorted(
        first["lineage"],
        key=lambda row: (
            row["child_stage_id"], row["parent_stage_id"] or "",
            row["fingerprint"],
        ),
    )


def test_status_and_user_summary_share_the_same_bounded_v4_contract(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, _store, _manifest = _seed_v4(tmp_path, monkeypatch)
    _forbid_deep_stage_reads(monkeypatch)

    expected = loop_status.bounded_stage_view(str(workspace))
    machine = loop_status.status(str(workspace))
    human = loop_status.user_summary(
        str(workspace), host="codex", now=1_800_000_000)

    assert machine["stage_view"] == expected
    assert human["stage_view"] == expected
    assert machine["step"] == "signoff"
    assert human["state"] == "signoff"
    assert human["action_required"] is True


def test_legacy_status_remains_compatible_and_stage_view_is_only_additive(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _git_workspace(tmp_path / "legacy-checkout")
    loop.save(str(workspace), _loop_state(step="execute"))
    monkeypatch.setattr(storage, "load_workspace_locator", lambda _ws: None)

    view = loop_status.bounded_stage_view(str(workspace))
    machine = loop_status.status(str(workspace))
    human = loop_status.user_summary(
        str(workspace), host="codex", now=1_800_000_000)

    assert view["status"] == "legacy"
    assert view["available"] is False
    assert view["current_stage"] is None
    assert view["history"] == []
    assert view["lineage"] == []
    assert machine["step"] == "execute"
    assert machine["tasks"] == [{
        "id": "t05", "status": "passed", "fix_cycles": 0,
    }]
    assert machine["current_task"] == 0
    assert "stage_view" not in machine
    assert human["state"] == "execute"
    assert human["progress"] == {"settled": 1, "total": 1}
    assert "stage_view" not in human


def test_corrupt_v4_is_visible_and_fails_closed_without_opening_objects(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, store, manifest = _seed_v4(tmp_path, monkeypatch)
    manifest["active_stage_projection"]["fingerprint"] = "0" * 64
    run_store._atomic_write_json(store._manifest_path(RUN_ID), manifest)
    _forbid_deep_stage_reads(monkeypatch)

    view = loop_status.bounded_stage_view(str(workspace))
    machine = loop_status.status(str(workspace))
    human = loop_status.user_summary(str(workspace), host="codex")

    assert view["status"] == "corrupt"
    assert view["available"] is False
    assert view["current_stage"] is None
    assert view["predecessor_stages"] == []
    assert view["handoff_fingerprint"] is None
    assert view["history"] == []
    assert view["lineage"] == []
    assert view["error"]
    assert machine["stage_view"] == view
    assert human["stage_view"] == view


def test_dashboard_renders_text_first_lineage_and_escapes_hostile_summary_text(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, _store, _manifest = _seed_v4(tmp_path, monkeypatch)
    _forbid_deep_stage_reads(monkeypatch)

    view = loop_status.bounded_stage_view(str(workspace))
    rendered = dashboard.render_stage_lineage(view)

    assert "stage &amp; lineage" in rendered.lower()
    assert CURRENT_ID in rendered
    assert PREDECESSOR_ID in rendered
    assert CHILD_ID in rendered
    assert "closed" in rendered
    assert "6" * 64 in rendered
    assert HOSTILE_REASON not in rendered
    assert html.escape(HOSTILE_REASON, quote=False) in rendered


def test_retro_v4_aggregates_summaries_without_predecessor_trace_roots(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace, store, _manifest = _seed_v4(
        tmp_path,
        monkeypatch,
        include_child=False,
        step="retro",
        hostile_reason="Predecessor summary is sufficient for Retro.",
    )
    predecessor_root = Path(storage.stage_execution_root_for_run(
        store.home, RUN_ID, PREDECESSOR_ID))
    predecessor_root.mkdir(parents=True, exist_ok=True)
    predecessor_trace = predecessor_root / "trace.jsonl"
    predecessor_trace.write_text(
        '{"event":"hook_deny","reason":"must never be read"}\n',
        encoding="utf-8",
    )
    real_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        try:
            candidate = os.path.realpath(os.fspath(file))
        except TypeError:
            candidate = ""
        if candidate == os.path.realpath(predecessor_trace):
            raise AssertionError("Retro opened a predecessor trace root")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(retro, "open", guarded_open, raising=False)
    _forbid_deep_stage_reads(monkeypatch)

    report = retro.run(
        str(workspace),
        load_state=loop.load,
        mutate_state=loop.mutate,
        loop_path=loop._loop_path(str(workspace)),
        normalize_severity=loop.normalize_severity,
    )

    assert "error" not in report
    assert report["stage_view"]["current_stage"]["stage_id"] == CURRENT_ID
    assert report["stage_view"]["predecessor_stages"][0]["outcome"] == \
        "closed"
    assert report["stage_metrics"]["terminal"] >= 1
    assert report["stage_metrics"]["outcomes"]["closed"] >= 1
    assert report["trace_scope"]["source"] == "bounded-stage-view"
