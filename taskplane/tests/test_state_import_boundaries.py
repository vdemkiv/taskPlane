"""Recovery durability boundaries using synthetic local-host stores only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from taskplane import primitives, state_import as recovery, workflow as w
from taskplane import workflow_host as h, workflow_local as local
from taskplane.context_handoff import Session, consume_required
from taskplane.tests.test_ordinary_checkpoint_reply import ordinary
from taskplane.tests.test_state_import import invoke, lost, pin, tree
from taskplane.tests.test_workflow_evidence import prepare
from taskplane.tests.test_workflow_local import decide, present, submit


@pytest.mark.parametrize("member", ["request.json", "approval-observation.json"])
@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_import_cannot_outlive_retained_request_or_approval(lost, member, damage):
    c, _, _, _, _, _ = lost
    result = invoke(lost, "apply")
    retained = Path(result["original_controller"]["path"]).with_name(member)
    if damage == "missing":
        retained.unlink()
    else:
        retained.write_bytes(b"{}")
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        c.report()
    assert c._path().read_bytes() == before


def test_committed_import_retains_provenance_without_external_backup(lost):
    c, state, original, approval, request, proof = lost
    expected_request = Path(proof["path"]).read_bytes()
    expected_approval = Path(request["approval"]["path"]).read_bytes()
    result = invoke(lost, "apply")
    retained = Path(result["original_controller"]["path"]).parent
    assert (retained / "original-controller.json").read_bytes() == original
    assert (retained / "request.json").read_bytes() == expected_request
    assert (retained / "approval-observation.json").read_bytes() == expected_approval
    shutil.rmtree(Path(proof["path"]).parent)
    restored = c.report()
    assert restored["run"] == state["run"] and restored["decisions"] == {}
    accepted = decide(c, restored, approval)
    assert accepted["decisions"][approval["event_id"]]["provenance"]["relay"] == approval["relay"]
    assert (retained / "request.json").read_bytes() == expected_request
    assert (retained / "approval-observation.json").read_bytes() == expected_approval


def test_incomplete_receipt_binding_reports_recoverable_refusal(lost):
    c, _, _, _, _, _ = lost
    invoke(lost, "apply")
    receipt = recovery.record_path(c.workspace, c.root)
    value = json.loads(receipt.read_bytes())
    # A syntactically valid receipt with a valid checksum still needs a complete
    # identity; malformed persisted metadata must not escape as a traceback.
    value["binding"].pop("run")
    value.pop("digest")
    value["digest"] = primitives.content_fingerprint(value)
    receipt.write_text(json.dumps(value))
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        c.report()
    assert c._path().read_bytes() == before


@pytest.mark.parametrize("boundary", ["harness", "commit"])
def test_late_interruption_retries_without_accepting_or_replacing_run(lost, monkeypatch, boundary):
    c, state, original, _, _, _ = lost
    publish_harness = local.Harness.update
    publish_receipt = recovery._save_receipt

    def fail_harness(self, **changes):
        if "state_import" in changes:
            raise OSError("fixture stopped before harness binding")
        return publish_harness(self, **changes)

    def fail_commit(path, value):
        if value["status"] == "committed":
            raise OSError("fixture stopped before receipt commit")
        return publish_receipt(path, value)

    if boundary == "harness":
        monkeypatch.setattr(local.Harness, "update", fail_harness)
    else:
        monkeypatch.setattr(recovery, "_save_receipt", fail_commit)
    with pytest.raises(OSError, match="fixture stopped"):
        invoke(lost, "apply")
    controller_bytes = c._path().read_bytes()
    with pytest.raises(w.Refusal, match="incomplete"):
        c.report()
    monkeypatch.setattr(local.Harness, "update", publish_harness)
    monkeypatch.setattr(recovery, "_save_receipt", publish_receipt)
    result = invoke(lost, "apply")
    assert result["status"] == "imported"
    assert c._path().read_bytes() == controller_bytes
    assert Path(result["original_controller"]["path"]).read_bytes() == original
    restored = c.report()
    assert restored["run"] == state["run"] and restored["revision"] == state["revision"]
    assert restored["visits"] == state["visits"] and restored["decisions"] == {}
    assert not local.Harness(c.workspace, c.root).presentation_valid(restored)


def test_interrupted_import_cannot_switch_request_on_retry(lost, monkeypatch):
    c, _, _, _, request, proof = lost
    publish = recovery._publish

    def interrupt(path, raw):
        raise OSError("fixture stopped after prepared receipt")

    monkeypatch.setattr(recovery, "_publish", interrupt)
    with pytest.raises(OSError, match="fixture stopped"):
        invoke(lost, "apply")
    monkeypatch.setattr(recovery, "_publish", publish)
    changed = deepcopy(request)
    changed["request_reference"] = "fixture/different-recovery-request"
    alternate = pin(Path(proof["path"]).with_name("different-request.json"), json.dumps(changed).encode())
    before = tree(c.workspace)
    with pytest.raises(w.Refusal, match="different import"):
        recovery.recover(c, alternate["path"], alternate["sha256"], mode="apply")
    assert tree(c.workspace) == before
    assert invoke(lost, "apply")["status"] == "imported"


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_complete_ignored_store_backup_restores_original_context_and_identity(tmp_path, host):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".gitignore").write_text(".taskplane/\n")
    source, _, _ = prepare(root)
    c = h.Controller(root, "root", h.installed_adapter(host))
    state = submit(c, c.start({"scope": source["scope"], "request_reference": "fixture/original-user"}))
    receipt, _ = consume_required(Session(root, state))
    marker = c._path().with_name(c.adapter.markername)
    assert marker.exists() and (root / ".taskplane/context-v1").is_dir()
    before = tree(root)
    backup = tmp_path / "complete-backup"
    shutil.copytree(root, backup)
    assert tree(backup) == before
    shutil.rmtree(root / ".taskplane")
    assert c.report()["status"] == "no_workflow"
    shutil.copytree(backup, root, dirs_exist_ok=True)
    assert tree(root) == before
    restored = c.report()
    assert restored["run"] == state["run"] and restored["revision"] == state["revision"]
    assert restored["visits"] == state["visits"] and restored["decisions"] == state["decisions"]
    Session(root, restored).validate(receipt)
    assert marker.read_bytes() == (backup / marker.relative_to(root)).read_bytes()
    assert not recovery.record_path(root, c.root).exists()


def test_recovered_product_approval_does_not_accept_submitted_design(lost):
    c, _, _, approval, _, _ = lost
    result = invoke(lost, "apply")
    restored = c.report()
    product = deepcopy(w.current(restored))
    restored = decide(c, restored, approval)
    present(c, restored)
    design = c.apply("advance", restored["run"], expected_revision=restored["revision"], phase="design")
    receipt, _ = consume_required(Session(c.workspace, design))
    Session(c.workspace, design).validate(receipt)
    submitted = submit(c, design)
    assert w.current(submitted)["decision"] == "awaiting_human_approval"
    assert submitted["visits"][0]["packet"] == product["packet"]
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        decide(c, submitted, approval)
    with pytest.raises(w.Refusal):
        c.apply("advance", submitted["run"], expected_revision=submitted["revision"], phase="plan")
    assert c._path().read_bytes() == before
    assert set(c.report()["decisions"]) == {approval["event_id"]}
    assert result["approval_recorded"] is False


def test_oversized_receipt_refuses_before_durable_publication(lost):
    c, _, original, _, request, proof = lost
    db = json.loads(original)
    state = db["runs"][db["active"]]
    packet = w.current(state)["packet"]
    backup = Path(proof["path"]).parent
    request = deepcopy(request)
    # A valid bounded request can contain many long, individually valid paths.
    # Their repeated inclusion in the receipt must not create unreadable state.
    for index in range(240):
        relative = ".taskplane/product/" + str(index) + "-" + "x" * 225 + ".txt"
        raw = b"Synthetic retained supporting evidence"
        packet["manifest"][relative] = hashlib.sha256(raw).hexdigest()
        request["artifacts"][relative] = pin(backup / ("evidence-" + str(index)), raw)
    request["controller"] = pin(backup / "large-controller.json", json.dumps(db).encode())
    request["binding"] = w.binding(state, packet)
    request["approval"] = pin(backup / "large-approval.json", json.dumps(ordinary(state)).encode())
    proof.update(pin(Path(proof["path"]), json.dumps(request).encode()))
    assert Path(proof["path"]).stat().st_size < 262144
    before = tree(c.workspace)
    for mode in ("inspect", "apply"):
        with pytest.raises(w.Refusal, match="[Rr]eceipt.*(?:size|bound|large)"):
            invoke(lost, mode)
        assert {p: raw for p, raw in tree(c.workspace).items() if not p.endswith(".lock")} == before


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_normalized_task_artifact_gets_precise_unsupported_shape_before_writes(tmp_path, host):
    root = tmp_path / "workspace"
    root.mkdir()
    source, _, _ = prepare(root)
    c = h.Controller(root, "root", h.installed_adapter(host))
    state = c.start({"scope": source["scope"], "request_reference": "fixture/original-user"})
    _, output, _ = prepare(root)
    output.update(run=state["run"], visit=w.current(state)["id"])
    output["artifacts"] = [{"path": "tasks.json", "kind": "task-dag", "schema": "fixture/tasks",
                            "phase": "product", "visit": w.current(state)["id"],
                            "criteria": ["AC1"], "tasks": ["T1"]}]
    (root / "product.json").write_text(json.dumps(output))
    state = c.apply("submit", state["run"], expected_revision=state["revision"],
                    output="product.json", tasks="tasks.json")
    packet = w.current(state)["packet"]
    assert packet["manifest"]["tasks.json"]["schema"] == "taskplane.task-definitions/v1"
    backup = tmp_path / "preserved"
    backup.mkdir()
    request = {"schema": recovery.REQUEST, "host": host, "binding": w.binding(state, packet),
               "request_reference": "fixture/recover-original-run",
               "controller": pin(backup / "controller.json", c._path().read_bytes()),
               "approval": pin(backup / "approval.json", json.dumps(ordinary(state)).encode()),
               "artifacts": {}}
    for index, relative in enumerate(packet["manifest"]):
        request["artifacts"][relative] = pin(backup / str(index), (root / relative).read_bytes())
    proof = pin(backup / "request.json", json.dumps(request).encode())
    shutil.rmtree(root / ".taskplane")
    for relative in packet["manifest"]:
        (root / relative).unlink(missing_ok=True)
    before = tree(root)
    for mode in ("inspect", "apply"):
        with pytest.raises(w.Refusal, match="[Nn]ormalized"):
            recovery.recover(c, proof["path"], proof["sha256"], mode=mode)
        assert {p: raw for p, raw in tree(root).items() if not p.endswith(".lock")} == before
