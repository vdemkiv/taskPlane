"""R-0003 t08: canonical governance survives eligible tree removal."""
from __future__ import annotations

import os

import dashboard
import loop
import review_evidence
import runtime_eval
import storage
import worktree_cleanup as cleanup
from tests.test_worktree_cleanup import _fixture, _released


def test_canonical_review_evidence_resolves_after_worker_removal(tmp_path):
    primary, worker, receipt, _layout = _fixture(tmp_path)
    store = review_evidence.ArtifactStore(worker)
    reference = store.put("evaluation", {
        "schema": "test.evaluation/v1", "verdict": "pass"})
    retention = review_evidence.retained_cleanup_evidence(
        str(primary), worker, [reference], lifecycle_released=True)
    assert retention["status"] == "released"
    assert retention["evidence_needed"] is False

    removed = cleanup.cleanup(receipt, lifecycle=_released(
        evidence_needed=retention["evidence_needed"]))
    assert removed["outcome"] == "removed"
    assert not os.path.exists(worker)

    reopened = review_evidence.ArtifactStore.from_reference(
        str(primary), reference)
    assert reopened.verify(reference) is True
    assert reopened.read(reference)["verdict"] == "pass"


def test_evidence_needed_retains_tree_until_owner_releases(tmp_path):
    primary, worker, receipt, _layout = _fixture(tmp_path)
    store = review_evidence.ArtifactStore(worker)
    reference = store.put("evaluation", {"verdict": "pass"})
    held = review_evidence.retained_cleanup_evidence(
        str(primary), worker, [reference], lifecycle_released=False)
    assert held["status"] == "evidence-needed"

    preserved = cleanup.cleanup(
        receipt, lifecycle=_released(evidence_needed=held["evidence_needed"]))
    assert preserved["outcome"] == "preserved"
    assert os.path.isdir(worker)

    released = review_evidence.retained_cleanup_evidence(
        str(primary), worker, [reference], lifecycle_released=True)
    removed = cleanup.cleanup(
        receipt, lifecycle=_released(evidence_needed=released["evidence_needed"]))
    assert removed["outcome"] == "removed"


def test_cleanup_projection_and_dashboard_use_canonical_outcome_after_removal(
        tmp_path, monkeypatch):
    primary, _worker, receipt, _layout = _fixture(tmp_path)
    record = cleanup.cleanup(receipt, lifecycle=_released())
    projection = runtime_eval.worktree_cleanup_projection({"task-1": record})
    assert projection["counts"]["removed"] == 1
    assert projection["headline"] is False

    attention = dict(record, outcome="preserved",
                     reason="evidence still required")
    monkeypatch.setattr(dashboard, "_load_loop", lambda _ws: {
        "step": "em", "goal": "finish", "tasks": [],
        "worktree_cleanups": {"task-1": attention}})
    html = dashboard.widget(str(primary))
    assert "worktree cleanup needs attention" in html
    assert "evidence still required" in html


def test_retained_artifacts_are_outside_removed_worker_and_inside_run_home(
        tmp_path):
    primary, worker, _receipt, layout = _fixture(tmp_path)
    reference = review_evidence.ArtifactStore(worker).put(
        "evaluation", {"verdict": "pass"})
    path = os.path.realpath(reference["path"])
    assert os.path.commonpath((os.path.realpath(worker), path)) != \
        os.path.realpath(worker)
    assert os.path.commonpath((os.path.realpath(layout.home), path)) == \
        os.path.realpath(layout.home)
    assert storage.load_workspace_locator(str(primary))["run_id"] == "run-1"


def test_automatic_merge_persists_receipt_before_cleanup_and_replay_recovers(
        tmp_path, monkeypatch):
    primary, worker, _receipt, _layout = _fixture(tmp_path)
    verdict = storage.evaluation_path(worker)
    os.makedirs(os.path.dirname(verdict), exist_ok=True)
    with open(verdict, "w", encoding="utf-8") as handle:
        handle.write('{"evaluation":{"status":"pass"}}\n')
    task = {"id": "task-1", "status": "passed", "workspace": worker,
            "scope": ["task.txt"]}
    loop.save(str(primary), {"step": "em", "goal": "g", "parallel": True,
                             "tasks": [task], "current_task": 0})
    original = cleanup.cleanup

    def crash_after_receipt(*_args, **_kwargs):
        state = loop.load(str(primary))
        assert state["task_merges"]["task-1"]["schema"] == \
            "taskplane.task-merge/v1"
        raise RuntimeError("simulated crash after receipt")

    monkeypatch.setattr(cleanup, "cleanup", crash_after_receipt)
    first = loop._automatic_merge_cleanup(str(primary), task)
    assert first["status"] == "preserved"
    assert os.path.isdir(worker)
    assert "task-1" in loop.load(str(primary))["task_merges"]

    monkeypatch.setattr(cleanup, "cleanup", original)
    replay = loop.cleanup_replay(str(primary))
    assert replay["attempted"] == 1
    assert replay["outcomes"][0]["outcome"] == "removed"
    assert not os.path.exists(worker)


def test_pre_receipt_failure_and_disabled_mode_preserve_tree(
        tmp_path, monkeypatch):
    primary, worker, _receipt, _layout = _fixture(tmp_path)
    verdict = storage.evaluation_path(worker)
    os.makedirs(os.path.dirname(verdict), exist_ok=True)
    with open(verdict, "w", encoding="utf-8") as handle:
        handle.write("{}\n")
    task = {"id": "task-1", "status": "passed", "workspace": worker}
    loop.save(str(primary), {"step": "em", "goal": "g", "parallel": True,
                             "tasks": [task], "current_task": 0})
    import repository
    monkeypatch.setattr(
        repository.RepositoryManager, "merge_registered_task",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("simulated pre-receipt crash")))
    result = loop._automatic_merge_cleanup(str(primary), task)
    assert result["status"] == "preserved"
    assert "task_merges" not in loop.load(str(primary))
    assert os.path.isdir(worker)

    monkeypatch.setenv("TASKPLANE_AUTO_WORKTREE_CLEANUP", "off")
    assert loop._automatic_merge_cleanup(str(primary), task) is None
    assert os.path.isdir(worker)
