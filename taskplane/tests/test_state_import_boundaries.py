"""Recovery durability boundaries using synthetic local-host stores only."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

import pytest

from taskplane import primitives, state_import as recovery, workflow as w
from taskplane import workflow_host as h, workflow_local as local
from taskplane.context_handoff import Session, consume_required
from taskplane.tests.test_ordinary_checkpoint_reply import ordinary
from taskplane.tests.test_state_import import invoke, lost, pin, tree
from taskplane.tests.test_workflow_evidence import prepare
from taskplane.tests.test_workflow_local import decide, present, submit


@pytest.mark.parametrize("boundary", ["mkdir", "child_fsync", "parent_fsync", "write", "file_fsync", "link", "receipt"])
def test_import_durability_interruptions_retry_without_false_commit(lost, monkeypatch, boundary):
    c, state, original, _, _, _ = lost
    publish, save = recovery._publish, recovery._save_receipt
    mkdir, sync_directory = os.mkdir, primitives._fsync_directory
    fdopen, fsync, link = os.fdopen, os.fsync, os.link
    progress = {"active": False, "new_directory": None}

    def active_publish(path, raw):
        progress["active"] = True
        try:
            return publish(path, raw)
        finally:
            progress["active"] = False

    def interrupted_mkdir(path, *args, **kwargs):
        result = mkdir(path, *args, **kwargs)
        if progress["active"]:
            progress["new_directory"] = str(Path(path))
            if boundary == "mkdir":
                raise OSError("fixture stopped after mkdir")
        return result

    def interrupted_directory(path):
        new = progress["new_directory"]
        if new and ((boundary == "child_fsync" and str(path) == new)
                    or (boundary == "parent_fsync" and str(path) == str(Path(new).parent))):
            raise OSError("fixture stopped before directory fsync")
        return sync_directory(path)

    class InterruptedWriter:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            self.stream.__enter__()
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def write(self, raw):
            self.stream.write(raw)
            raise OSError("fixture stopped during file write")

    def interrupted_open(fd, mode, *args, **kwargs):
        stream = fdopen(fd, mode, *args, **kwargs)
        return InterruptedWriter(stream) if boundary == "write" and mode == "wb" and progress["active"] else stream

    def interrupted_sync(fd):
        if boundary == "file_fsync" and progress["active"] and stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("fixture stopped before file fsync")
        return fsync(fd)

    def interrupted_link(src, dst, *args, **kwargs):
        result = link(src, dst, *args, **kwargs)
        if boundary == "link" and progress["active"]:
            raise OSError("fixture stopped after publication link")
        return result

    def interrupted_receipt(path, value):
        if boundary == "receipt" and value["status"] == "committed":
            raise OSError("fixture stopped before committed receipt")
        return save(path, value)

    with monkeypatch.context() as patch:
        for obj, name, value in [(recovery, "_publish", active_publish), (recovery, "_save_receipt", interrupted_receipt),
                                 (os, "mkdir", interrupted_mkdir), (primitives, "_fsync_directory", interrupted_directory),
                                 (os, "fdopen", interrupted_open), (os, "fsync", interrupted_sync), (os, "link", interrupted_link)]:
            patch.setattr(obj, name, value)
        with pytest.raises(OSError, match="fixture stopped"):
            invoke(lost, "apply")
    receipt_path = recovery.record_path(c.workspace, c.root)
    pending = json.loads(receipt_path.read_bytes())
    assert pending["status"] == "prepared" and pending["approval_recorded"] is False
    with pytest.raises(w.Refusal, match="incomplete"):
        c.report()
    result = invoke(lost, "apply")
    assert result["status"] == "imported" and result["observed_at"] == pending["observed_at"]
    assert Path(result["original_controller"]["path"]).read_bytes() == original
    restored = c.report()
    assert restored["run"] == state["run"] and restored["revision"] == state["revision"]
    assert restored["decisions"] == {}
    settled = tree(c.workspace)
    assert invoke(lost, "apply")["status"] == "already_imported"
    assert tree(c.workspace) == settled


def test_new_import_ancestry_flushes_child_and_owning_parent_before_commit(lost, monkeypatch):
    c, _, _, _, _, _ = lost
    mkdir, sync, save = os.mkdir, primitives._fsync_directory, recovery._save_receipt
    events = []
    def observe_mkdir(path, *args, **kwargs):
        result = mkdir(path, *args, **kwargs)
        if Path(path).is_relative_to(c.workspace):
            events.append(("mkdir", str(path)))
        return result
    def observe_sync(path):
        result = sync(path)
        events.append(("sync", str(path)))
        return result
    def observe_receipt(path, value):
        if value["status"] == "committed":
            events.append(("commit", str(path)))
        return save(path, value)
    monkeypatch.setattr(os, "mkdir", observe_mkdir)
    monkeypatch.setattr(primitives, "_fsync_directory", observe_sync)
    monkeypatch.setattr(recovery, "_save_receipt", observe_receipt)
    assert invoke(lost, "apply")["status"] == "imported"
    committed = next(i for i, (kind, _) in enumerate(events) if kind == "commit")
    created = [(i, path) for i, (kind, path) in enumerate(events) if kind == "mkdir"]
    assert any("recovery-v1" in path for _, path in created)
    for index, path in created:
        child = events.index(("sync", path), index + 1, committed)
        parent = events.index(("sync", str(Path(path).parent)), child + 1, committed)
        assert index < child < parent < committed


def test_unsupported_import_directory_sync_cannot_commit(lost, monkeypatch):
    c, _, _, _, _, _ = lost
    before = tree(c.workspace)
    def unsupported(path):
        raise OSError("fixture filesystem cannot persist directory entries")
    monkeypatch.setattr(primitives, "_fsync_directory", unsupported)
    with pytest.raises(OSError, match="cannot persist directory"):
        invoke(lost, "apply")
    assert not recovery.record_path(c.workspace, c.root).exists()
    assert {key: value for key, value in tree(c.workspace).items() if not key.endswith(".lock")} == before


def test_existing_import_file_requires_successful_retry_sync(tmp_path, monkeypatch):
    target = tmp_path.resolve() / "existing" / "nested" / "retained.json"
    target.parent.mkdir(parents=True)
    raw = b"retained exact recovery bytes\n"
    target.write_bytes(raw)
    def unsupported(path):
        raise OSError("fixture retry directory fsync failed")
    with monkeypatch.context() as patch:
        patch.setattr(primitives, "_fsync_directory", unsupported)
        with pytest.raises(OSError, match="retry directory fsync failed"):
            recovery._publish(target, raw)
    assert target.read_bytes() == raw
    recovery._publish(target, raw)
    assert target.read_bytes() == raw


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
