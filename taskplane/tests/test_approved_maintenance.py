"""Approved checkpoints acknowledge unrelated additions without reopening grants."""
from copy import deepcopy
import json

import pytest

from taskplane import primitives, workflow as w, workflow_local
from taskplane.tests.test_workflow_local import setup, submit, decide, present


def fixture(tmp_path, *, approved=True, reports="dist"):
    if reports == "outputs":
        # The exclusion belongs to the reviewed graph, never the later request.
        (tmp_path / "components.yaml").write_text("exclude:\n  - outputs\n")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/existing.txt").write_text("Existing unrelated artifact\n")
    c, state = setup(tmp_path)
    state = submit(c, state)
    if approved:
        state = decide(c, state)
    target = tmp_path / reports / "release-report.txt"
    target.parent.mkdir(exist_ok=True)
    target.write_text("Actual release observations\n")
    request = {"schema": "taskplane.maintenance-request/v1", "request_reference": "fixture/user-finalize",
               "reason": "Acknowledge the explicitly requested release report and staging additions.",
               "changes": {str(target.relative_to(tmp_path)): {
                   "before": None, "after": primitives.content_fingerprint(target.read_bytes())}}}
    manifest = tmp_path / ".taskplane/maintenance.json"
    manifest.write_text(json.dumps(request))
    return c, state, request, manifest


@pytest.mark.parametrize("reports", ["dist", "outputs"])
def test_approved_additions_preserve_checkpoint_and_need_fresh_dashboard(tmp_path, reports):
    c, state, request, manifest = fixture(tmp_path, reports=reports)
    harness = workflow_local.Harness(tmp_path, c.root)
    assert harness.presentation_valid(state)
    before = deepcopy(state)
    receipt = c.reconcile_maintenance(state["run"], state["revision"], str(manifest.relative_to(tmp_path)))
    current = c.report()
    assert current["revision"] == state["revision"] + 1
    for key in ("scope", "visits", "decisions"):
        assert current[key] == before[key]
    assert current["history"][:-1] == before["history"]
    assert current["history"][-1] == {"maintenance": receipt}
    assert current["source_baseline"] == {**before["source_baseline"],
        **{path: change["after"] for path, change in request["changes"].items()}}
    assert receipt["approvals_changed"] is False and receipt["scope_changed"] is False
    assert not harness.presentation_valid(current)
    with pytest.raises(w.Refusal, match="dashboard handoff"):
        c.apply("advance", state["run"], expected_revision=current["revision"], phase="design")
    with pytest.raises(w.Refusal):
        c.guard({"tool_name": "Write", "tool_input": {"file_path": next(iter(request["changes"]))}}, state["run"])
    present(c, current)
    advanced = c.apply("advance", state["run"], expected_revision=current["revision"], phase="design")
    assert w.current(advanced)["phase"] == "design" and advanced["decisions"] == before["decisions"]


@pytest.mark.parametrize("defect", ["pending", "modified", "deleted", "declared_path", "normative",
    "sealed_source", "new_runtime_source", "new_graph_artifact", "changed_exclusion", "missing_change",
    "hash", "reference", "live_command", "symlink", "inactive"])
def test_approved_maintenance_refuses_unreviewed_effects_without_mutation(tmp_path, defect):
    c, state, request, manifest = fixture(tmp_path, approved=defect != "pending")
    if defect in {"modified", "deleted"}:
        target = tmp_path / "dist/existing.txt"
        before_hash = primitives.content_fingerprint(target.read_bytes())
        if defect == "modified":
            target.write_text("Changed preexisting bytes\n")
        else:
            target.unlink()
        request["changes"]["dist/existing.txt"] = {"before": before_hash,
            "after": primitives.content_fingerprint(target.read_bytes()) if target.exists() else None}
    elif defect in {"declared_path", "normative", "sealed_source", "new_runtime_source", "new_graph_artifact", "changed_exclusion"}:
        name = {"declared_path": "design.json", "normative": "product.json", "sealed_source": "app.py",
                "new_runtime_source": "unreviewed.py", "new_graph_artifact": "docs/new.md",
                "changed_exclusion": "components.yaml"}[defect]
        target = tmp_path / name
        before_hash = primitives.content_fingerprint(target.read_bytes()) if target.exists() else None
        target.parent.mkdir(exist_ok=True)
        target.write_text("Changed source relationship\n")
        request["changes"][name] = {"before": before_hash, "after": primitives.content_fingerprint(target.read_bytes())}
    elif defect == "missing_change":
        (tmp_path / "dist/unlisted.txt").write_text("Unlisted addition\n")
    elif defect == "hash":
        request["changes"]["dist/release-report.txt"]["after"] = "0" * 64
    elif defect == "reference":
        request["request_reference"] = " "
    elif defect == "live_command":
        target = c._path()
        with primitives.file_lock(str(target)):
            db = c._read(target)
            db["runs"][state["run"]]["observed_handles"]["fixture"] = {
                "state": "running", "revision": state["revision"], "visit": w.current(state)["id"]}
            c._write(target, db)
    elif defect == "symlink":
        target = tmp_path / "dist/release-report.txt"
        target.unlink()
        target.symlink_to(tmp_path / "app.py")
    elif defect == "inactive":
        c.apply("retire", state["run"], expected_revision=state["revision"],
                native_reference=json.dumps({"request_reference": "fixture/retire", "reason": "Retire fixture"}))
    manifest.write_text(json.dumps(request))
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        c.reconcile_maintenance(state["run"], state["revision"], str(manifest.relative_to(tmp_path)))
    assert c._path().read_bytes() == before
