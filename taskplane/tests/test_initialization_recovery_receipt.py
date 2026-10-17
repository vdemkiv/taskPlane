"""Real disposable-file recovery/CAS behavior, without native-host authority claims."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading

import pytest

from taskplane import primitives, storage, workspace_binding as binding, workflow as w

requires_binding_runtime = pytest.mark.skipif(
    os.open not in os.supports_dir_fd or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"),
    reason="Restoration requires no-follow directory descriptors; unsupported platforms fail closed.")


@pytest.fixture
def restoration(tmp_path):
    workspace = tmp_path.resolve()
    parent = workspace / ".taskplane"
    parent.mkdir()
    owner, run = "fixture-root", "fixture-run"
    target = parent / ("workflow-" + hashlib.sha256(owner.encode()).hexdigest()[:32] + ".json")
    source = target.with_name(target.stem + " 1.json")
    database = {"schema": "taskplane.control/v1", "workspace": str(workspace), "root": owner,
                "profile": "native_workflow", "active": run,
                "runs": {run: {"revision": 7, "decisions": {"approval": {"decision": "accepted"}}}}}
    raw = (json.dumps(database, indent=3) + "\n").encode()
    source.write_bytes(raw)
    target.with_name(target.stem + ".initialized.json").write_text(json.dumps({
        "schema": "taskplane.local-initialization/v1", "workspace": str(workspace),
        "root": owner, "profile": "native_workflow"}))
    expected = {"schema": "taskplane.initialization-recovery/v1", "workspace": str(workspace),
                "root": owner, "run": run, "revision": 7, "source": source.name,
                "sha256": hashlib.sha256(raw).hexdigest(), "request_reference": "fixture/user-recovery",
                "state_bytes_preserved": True, "approvals_changed": False}
    record = target.with_name(target.stem + ".recovery-" + expected["sha256"] + ".json")
    return target, source, raw, expected, record


def restore(fixture):
    target, _, raw, expected, _ = fixture
    with primitives.file_lock(str(target)):
        return binding.restore_initialization(target, raw, expected)


def authority_bytes(parent):
    return {path.name: path.read_bytes() for path in parent.iterdir()
            if path.is_file() and not path.name.endswith(".lock")}


@requires_binding_runtime
def test_exact_restoration_and_completed_retry_preserve_original_bytes(restoration):
    target, source, raw, expected, record = restoration
    request = deepcopy(expected)
    assert restore(restoration) == {**expected, "status": "restored"}
    before = authority_bytes(target.parent)
    assert restore(restoration) == {**expected, "status": "already_restored"}
    assert authority_bytes(target.parent) == before
    assert source.read_bytes() == target.read_bytes() == raw
    assert json.loads(record.read_bytes()) == {**expected, "status": "restored"}
    assert expected == request


@requires_binding_runtime
def test_completed_retry_retains_original_request_provenance(restoration):
    target, _, raw, expected, record = restoration
    restore(restoration)
    before = authority_bytes(target.parent)
    with primitives.file_lock(str(target)):
        result = binding.restore_initialization(target, raw, {**expected, "request_reference": "fixture/later-retry"})
    assert result["status"] == "already_restored"
    assert result["request_reference"] == expected["request_reference"]
    assert authority_bytes(target.parent) == before


@requires_binding_runtime
def test_matching_target_cannot_manufacture_recovery_provenance(restoration):
    target, _, raw, _, record = restoration
    target.write_bytes(raw)
    before = authority_bytes(target.parent)
    with pytest.raises(w.Refusal, match="authentic prepared"):
        restore(restoration)
    assert authority_bytes(target.parent) == before and not record.exists()


@pytest.mark.parametrize("boundary", ["before_prepared", "prepared", "before_temporary_fsync", "after_temporary_fsync",
                                      "before_target_link", "target_link", "target_sync", "after_target_sync",
                                      "completion_write", "completion_sync", "after_completion_sync", "after_completion"])
@requires_binding_runtime
def test_interrupted_initial_restore_finishes_same_receipt(restoration, monkeypatch, boundary):
    target, source, raw, expected, record = restoration
    write, link, replace, fsync = binding._write, os.link, os.replace, os.fsync
    publish = binding._publish_restored_file
    state = {"publishing": False, "linked": False, "completed": False}

    def active_publish(path, data):
        state["publishing"] = True
        try:
            return publish(path, data)
        finally:
            state["publishing"] = False

    def interrupted_write(path, value, **kwargs):
        if value.get("status") == "prepared" and boundary == "before_prepared":
            raise OSError("fixture interruption before prepared receipt")
        if value.get("status") == "restored" and boundary == "completion_write":
            raise OSError("fixture interruption before receipt completion")
        write(path, value, **kwargs)
        if value.get("status") == "prepared" and boundary == "prepared":
            raise OSError("fixture interruption after prepared receipt")
        if value.get("status") == "restored" and boundary == "after_completion":
            raise OSError("fixture interruption after receipt completion")

    def interrupted_link(src, dst, **kwargs):
        if dst == target.name and boundary == "before_target_link":
            raise OSError("fixture interruption before target link")
        result = link(src, dst, **kwargs)
        if dst == target.name:
            state["linked"] = True
            if boundary == "target_link":
                raise OSError("fixture interruption after target link")
        return result

    def interrupted_replace(src, dst, **kwargs):
        result = replace(src, dst, **kwargs)
        if dst == record.name:
            state["completed"] = True
        return result

    def interrupted_sync(fd):
        temporary = state["publishing"] and not state["linked"]
        if ((boundary == "before_temporary_fsync" and temporary)
                or (boundary == "target_sync" and state["linked"])
                or (boundary == "completion_sync" and state["completed"])):
            raise OSError("fixture interruption before directory fsync")
        result = fsync(fd)
        if ((boundary == "after_temporary_fsync" and temporary)
                or (boundary == "after_target_sync" and state["linked"])
                or (boundary == "after_completion_sync" and state["completed"])):
            raise OSError("fixture interruption after fsync")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(binding, "_write", interrupted_write)
        patch.setattr(binding, "_publish_restored_file", active_publish)
        patch.setattr(os, "link", interrupted_link)
        patch.setattr(os, "replace", interrupted_replace)
        patch.setattr(os, "fsync", interrupted_sync)
        with pytest.raises((OSError, w.Refusal), match="(?:fixture interruption|Directory unavailable)"):
            restore(restoration)
    if boundary == "before_prepared":
        assert not record.exists() and not target.exists()
    else:
        retained = json.loads(record.read_bytes())
        assert {key: value for key, value in retained.items() if key != "status"} == expected
        assert retained["status"] == ("restored" if boundary in {"completion_sync", "after_completion_sync", "after_completion"} else "prepared")
    assert source.read_bytes() == raw
    assert restore(restoration)["status"] in {"restored", "already_restored"}
    assert target.read_bytes() == source.read_bytes() == raw
    assert json.loads(record.read_bytes()) == {**expected, "status": "restored"}
    settled = authority_bytes(target.parent)
    restore(restoration)
    assert authority_bytes(target.parent) == settled


@pytest.mark.parametrize("defect", ["target", "source", "run", "revision", "reference", "digest", "extra", "status", "symlink"])
@requires_binding_runtime
def test_prepared_retry_rejects_changed_scope_without_writes(restoration, defect):
    target, source, raw, expected, record = restoration
    target.write_bytes(raw)
    receipt = {**expected, "status": "prepared"}
    if defect == "target":
        target.write_bytes(raw + b" ")
    elif defect == "source":
        source.write_bytes(raw + b" ")
    elif defect == "run":
        receipt["run"] = "foreign-run"
    elif defect == "revision":
        receipt["revision"] = True
    elif defect == "reference":
        receipt["request_reference"] = "fixture/other-request"
    elif defect == "digest":
        receipt["sha256"] = "0" * 64
    elif defect == "extra":
        receipt["extra"] = "unrecognized"
    elif defect == "status":
        receipt["status"] = "already_restored"
    elif defect == "symlink":
        target.unlink()
        target.symlink_to(source.name)
    record.write_text(json.dumps(receipt))
    before = authority_bytes(target.parent)
    with pytest.raises(w.Refusal):
        restore(restoration)
    assert authority_bytes(target.parent) == before


@requires_binding_runtime
def test_restored_receipt_with_missing_target_is_not_replayed(restoration):
    target, _, _, _, _ = restoration
    restore(restoration)
    target.unlink()
    before = authority_bytes(target.parent)
    with pytest.raises(w.Refusal, match="no canonical controller"):
        restore(restoration)
    assert authority_bytes(target.parent) == before and not target.exists()


@requires_binding_runtime
def test_unsupported_directory_sync_never_acknowledges_restore(restoration, monkeypatch):
    target, _, _, _, record = restoration
    before = authority_bytes(target.parent)
    def unsupported(path):
        raise OSError("fixture filesystem does not support directory fsync")
    monkeypatch.setattr(primitives, "_fsync_directory", unsupported)
    with pytest.raises(OSError, match="does not support"):
        restore(restoration)
    assert authority_bytes(target.parent) == before and not record.exists()


def test_missing_no_follow_primitive_refuses_without_authority_writes(restoration, monkeypatch):
    target, _, _, _, record = restoration
    before = authority_bytes(target.parent)
    monkeypatch.setattr(os, "supports_dir_fd", set())
    with pytest.raises(w.Refusal, match="no-follow directory operations"):
        restore(restoration)
    assert authority_bytes(target.parent) == before and not record.exists()


def test_oversized_recovery_receipt_refuses_before_publication(restoration):
    target, _, raw, expected, record = restoration
    before = authority_bytes(target.parent)
    with primitives.file_lock(str(target)):
        with pytest.raises(w.Refusal, match="receipt exceeds"):
            binding.restore_initialization(target, raw, {**expected, "run": "r" * binding.REQUEST_BYTES})
    assert authority_bytes(target.parent) == before and not record.exists()


def test_storage_generation_legacy_read_and_noop_do_not_rewrite(tmp_path):
    target = tmp_path / "controller.json"
    raw = b'{ "runs": {"r": {"revision": 7}}, "active": "r" }\n'
    target.write_bytes(raw)
    proposal = json.loads(raw)
    assert storage.storage_generation(proposal) == 0
    assert storage.commit_controller(target, proposal, expected_generation=0) == 0
    assert target.read_bytes() == raw and "storage_generation" not in proposal
    proposal["runs"]["r"]["observation"] = "new"
    saved = deepcopy(proposal)
    assert storage.commit_controller(target, proposal, expected_generation=0) == 1
    assert proposal == saved
    persisted = json.loads(target.read_bytes())
    assert persisted["storage_generation"] == 1 and persisted["runs"]["r"]["revision"] == 7


@pytest.mark.parametrize("value", [True, False, -1, "0", None, 1.0])
def test_invalid_generation_preserves_snapshot_and_authority(tmp_path, value):
    target = tmp_path / "controller.json"
    raw = b'{"storage_generation": 2, "accepted": true}\n'
    target.write_bytes(raw)
    proposal = {"storage_generation": value, "accepted": False}
    before = deepcopy(proposal)
    with pytest.raises(ValueError):
        storage.commit_controller(target, proposal, expected_generation=2)
    with pytest.raises(ValueError):
        storage.commit_controller(target, {"storage_generation": 2}, expected_generation=value)
    assert proposal == before and target.read_bytes() == raw
    target.write_text(json.dumps(proposal))
    invalid = target.read_bytes()
    with pytest.raises(ValueError):
        storage.commit_controller(target, {"storage_generation": 2}, expected_generation=2)
    assert target.read_bytes() == invalid


def test_concurrent_stale_writer_cannot_erase_accepted_result(tmp_path):
    target = tmp_path / "controller.json"
    initial = {"runs": {"r": {"revision": 7, "result": "pending"}}}
    target.write_text(json.dumps(initial))
    accepted = deepcopy(initial)
    accepted["runs"]["r"]["result"] = "accepted"
    stale = deepcopy(initial)
    stale["runs"]["r"]["observation"] = "late startup"
    read_barrier, accepted_commit = threading.Barrier(2), threading.Event()

    def writer(proposal, first):
        snapshot = deepcopy(proposal)
        read_barrier.wait(timeout=10)
        if not first:
            assert accepted_commit.wait(timeout=10)
        try:
            return storage.commit_controller(target, proposal, expected_generation=0)
        finally:
            assert proposal == snapshot
            if first:
                accepted_commit.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(writer, accepted, True)
        loser = executor.submit(writer, stale, False)
        assert winner.result(timeout=15) == 1
        with pytest.raises(storage.StorageConflict) as caught:
            loser.result(timeout=15)
    assert (caught.value.expected, caught.value.actual) == (0, 1)
    current = json.loads(target.read_bytes())
    assert current["runs"]["r"] == {"revision": 7, "result": "accepted"}
    assert current["storage_generation"] == 1


@pytest.mark.parametrize("boundary", ["before_replace", "after_replace"])
def test_failed_controller_publication_keeps_caller_and_complete_authority(tmp_path, monkeypatch, boundary):
    target = tmp_path / "controller.json"
    original = b'{"storage_generation": 4, "accepted": true}\n'
    target.write_bytes(original)
    proposal = {"storage_generation": 4, "accepted": True, "observation": "new"}
    before = deepcopy(proposal)
    replace = os.replace
    def interrupted(src, dst):
        if boundary == "after_replace":
            replace(src, dst)
        raise OSError("fixture publication interrupted")
    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError, match="fixture publication"):
        storage.commit_controller(target, proposal, expected_generation=4)
    assert proposal == before
    if boundary == "before_replace":
        assert target.read_bytes() == original
    else:
        assert json.loads(target.read_bytes()) == {**proposal, "storage_generation": 5}
        with pytest.raises(storage.StorageConflict):
            storage.commit_controller(target, proposal, expected_generation=4)
