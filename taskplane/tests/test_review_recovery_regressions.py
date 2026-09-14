"""Review scope and native-start projections, without model calls."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import subprocess

import pytest

from taskplane import review_evidence, tp as cli
from taskplane.tests.test_review_refusals import _run
from taskplane.tests.test_review_preflight import review, _host_execution_receipt


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "snapshot-review-session")
    cli.tp.record_entry_tools(["Read", "Grep", "Glob", "Write"])
    ws = tmp_path / "source"
    ws.mkdir()
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ws, text=True).strip()
    git("init", "-q")
    (ws / "service.py").write_text("def value():\n    return 1\n")
    (ws / "café.py").write_text("VALUE = 2\n")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-qm", "source snapshot")
    (ws / "untracked.py").write_text("not part of the selected commit\n")
    return ws, git("rev-parse", "HEAD")


def test_clean_repository_reaches_native_reads_with_explicit_source_scope(snapshot):
    ws, head = snapshot
    rc, output, err = _run("review", "start", "--scope", "repository", "--workspace", str(ws))
    ready = json.loads(output)
    assert rc == 0 and ready["status"] == "ready", (output, err)
    source = review_evidence.ArtifactStore(str(ws)).read(ready["source"])
    assert source["scope"] == "repository" and source["revision"] == head
    assert source["files"] == ["café.py", "service.py"]
    assert all((ws / path).read_text() for path in source["files"])
    assert not cli.tp.load_active(str(ws))
    assert not Path(review._kernel_root(str(ws))).exists()
    assert len(output.encode()) < 2048


def test_empty_diff_is_not_silently_reviewed_as_a_repository(snapshot):
    ws, _ = snapshot
    (ws / "untracked.py").unlink()
    rc, output, err = _run("review", "start", "--workspace", str(ws))
    assert rc == 1 and "review scope is empty" in output, (output, err)
    assert not Path(cli.tp.active_contract_path(str(ws))).exists()


@pytest.mark.parametrize("options", [["--base", "HEAD"], ["owner/repo#1"],
    ["https://github.com/owner/repo"]])
def test_repository_scope_refuses_incompatible_preflight_options(snapshot, options):
    ws, _ = snapshot
    rc, output, err = _run("review", "start", *options, "--scope", "repository",
        "--workspace", str(ws))
    assert rc == 1 and json.loads(output)["reason_code"] == "invalid_review_scope", (output, err)


def test_repository_snapshot_refuses_dirty_tracked_content(snapshot):
    ws, head = snapshot
    assert review.canonical_repository_files(str(ws), head) == ["café.py", "service.py"]
    (ws / "service.py").write_text("changed = True\n")
    with pytest.raises(review.ReviewKernelError, match="differs from the checkout"):
        review.canonical_repository_files(str(ws), head)


def test_native_starts_are_counted_once_even_when_concurrent():
    from taskplane.tests.test_review_routing import TestSelectiveReviewKernel as _ReviewFixture

    fixture = _ReviewFixture()
    fixture.setUp()
    try:
        opened = fixture._start()
        state = review._load_state(fixture.ws, opened["run_id"])
        assert state["counters"]["prepared_agent_count"] == len(state["slots"])
        assert state["counters"]["dispatched_agent_count"] == 0
        def start(item):
            index, slot = item
            contract = {**slot["producer_contract"], "task_id": f"fixture-{index}"}
            return review.register_slot_producer(fixture.ws, contract=contract,
                task_slot=contract["task_slot"], event={"session_id": "fixture-parent",
                    "agent_id": f"fixture-child-{index}", "hook_event_name": "SubagentStart"})
        slots = list(enumerate(state["slots"]))
        start(slots[0])
        assert review._load_state(fixture.ws, opened["run_id"])["counters"]["dispatched_agent_count"] == 1
        with ThreadPoolExecutor(max_workers=len(slots)) as pool:
            assert all(pool.map(start, slots))
        current = review._load_state(fixture.ws, opened["run_id"])
        assert current["counters"]["dispatched_agent_count"] == len(slots)
        assert current["manifest"]["counters"]["dispatched_agent_count"] == len(slots)
        assert current["slot_conservation"]["collected"]["count"] == 0
    finally:
        fixture.tearDown()


@pytest.mark.parametrize("damage", ["sandbox", "receipt", "action", "binding"])
def test_sandbox_projection_rejects_mismatched_execution_evidence(damage):
    execution = review.review_execution_preflight(run_id="run-1", selection="dynamic")
    action = execution["dynamic_validation"]["action_id"]
    execution = review.record_review_execution_evidence(execution, kind="dynamic_validation",
        status="executed", approval_receipt=_host_execution_receipt(action_id=action),
        sandbox={"schema": "taskplane.review-validation-sandbox/v1", "run_id": "run-1",
            "source_head": "a" * 40, "source_fingerprint": "b" * 64,
            "sandbox_id": "sandbox-1", "disposable": True, "push_disabled": True})
    assert review.production_validation_projection(execution)["status"] == "executed"
    altered = copy.deepcopy(execution)
    dynamic = altered["dynamic_validation"]
    if damage == "sandbox": dynamic["sandbox"]["sandbox_id"] = "other"
    if damage == "receipt": dynamic["evidence_receipt"]["run_id"] = "other"
    if damage == "action": dynamic["evidence_receipt"]["action_digest"] = "0" * 64
    if damage == "binding": dynamic["sandbox_binding"]["receipt_id"] = "other"
    assert review.production_validation_projection(altered)["status"] == "unverified"


def test_render_recovery_uses_its_own_receipt_without_validation_sandbox():
    execution = review.review_execution_preflight(run_id="run-1", selection="dynamic-render")
    execution = review.record_review_execution_evidence(execution,
        kind="functionality_render", status="failed", detail={"summary": "render unavailable"})
    action = execution["functionality_render"]["action_id"]
    execution = review.record_review_execution_evidence(execution, kind="functionality_render",
        status="executed", approval_receipt=_host_execution_receipt(
            action_id=action, kind="functionality_render"))
    render = execution["functionality_render"]
    assert render["status"] == "executed" and "sandbox_binding" not in render
    assert execution["dynamic_validation"]["status"] == "selected"
    assert [row["status"] for row in render["attempts"]] == ["failed", "executed"]
