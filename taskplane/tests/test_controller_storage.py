"""Controller publication uses its measured bytes and keeps authority private."""
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from taskplane import storage, workflow_local, workflow_retention
from taskplane.context import Store, encode
from taskplane.tests.test_workflow_local import displaced_database
from taskplane.tests.test_workflow_retention import controller


def test_flat_graph_storage_import_in_fresh_interpreter():
    runtime = Path(storage.__file__).parent
    result = subprocess.run([sys.executable, "-c", "import storage, depgraph; assert storage.primitives.file_lock"],
                            cwd=runtime, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", ["ascii", "\u00e9\u6f22\U0001f600" * 30])
def test_capacity_matches_exact_persisted_bytes_at_limit(tmp_path, value):
    target = tmp_path / "controller.json"
    proposal = {"active": "run", "runs": {"run": {"revision": 7,
                "decisions": {"approval": {"accepted": True, "evidence": value}}}}}
    before = deepcopy(proposal)
    persisted = {**proposal, "storage_generation": 1}
    expected = storage.encode_controller(persisted)
    assert len(expected) < len(json.dumps(persisted, sort_keys=True, indent=2).encode())
    measured = workflow_retention.capacity(persisted, len(expected))
    assert measured["bytes"] == len(expected) and measured["remaining_bytes"] == 0
    assert storage.commit_controller(target, proposal, expected_generation=0, limit=len(expected)) == 1
    assert target.read_bytes() == expected
    assert json.loads(target.read_bytes()) == persisted and proposal == before
    assert workflow_retention.capacity(json.loads(target.read_bytes()), len(expected))["bytes"] == target.stat().st_size


def test_oversized_publication_refuses_without_changing_authority(tmp_path):
    target = tmp_path / "controller.json"
    original = b'{ "storage_generation": 1, "runs": {"r": {"accepted": true}} }\n'
    target.write_bytes(original)
    proposal = json.loads(original)
    proposal["observation"] = "\U0001f600" * 100
    before = deepcopy(proposal)
    limit = len(storage.encode_controller({**proposal, "storage_generation": 2})) - 1
    with pytest.raises(ValueError, match="Proposed controller exceeds"):
        storage.commit_controller(target, proposal, expected_generation=1, limit=limit)
    assert target.read_bytes() == original and proposal == before
    assert not list(tmp_path.glob(".controller.json.tmp.*"))


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("boundary", ["success", "before_replace", "after_replace"])
def test_controller_publication_preserves_bytes_and_requests_private_creation(tmp_path, monkeypatch, existing, boundary):
    target = tmp_path / "controller.json"
    original = b'{"storage_generation":0,"accepted":true}\n'
    if existing:
        target.write_bytes(original)
        target.chmod(0o644)
    replace = os.replace
    open_file = os.open
    created = []
    proposal = {"storage_generation": 0, "accepted": True, "observation": "new"}
    expected = storage.encode_controller({**proposal, "storage_generation": 1})

    def observed_open(path, flags, mode=0o777, **kwargs):
        if Path(path).name.startswith(".controller.json.tmp."):
            assert flags & os.O_CREAT and flags & os.O_EXCL
            assert mode == 0o600
            created.append(path)
        return open_file(path, flags, mode, **kwargs)

    def observed_replace(source, destination):
        assert source in created
        # Windows reports synthesized mode bits, not its ACL. Only POSIX
        # mode bits establish owner-only access here; the requested creation
        # mode and publication/failure boundaries are checked on every OS.
        if os.name == "posix":
            assert stat.S_IMODE(Path(source).stat().st_mode) == 0o600
        assert Path(source).read_bytes() == expected
        if boundary == "before_replace":
            raise OSError("fixture before publication")
        replace(source, destination)
        if boundary == "after_replace":
            raise OSError("fixture after publication")

    monkeypatch.setattr(os, "replace", observed_replace)
    monkeypatch.setattr(os, "open", observed_open)
    previous_umask = os.umask(0o022)
    try:
        if boundary == "success":
            assert storage.commit_controller(target, proposal, expected_generation=0) == 1
        else:
            with pytest.raises(OSError, match="fixture"):
                storage.commit_controller(target, proposal, expected_generation=0)
    finally:
        os.umask(previous_umask)
    if boundary == "before_replace":
        assert target.exists() == existing
        if existing:
            assert target.read_bytes() == original
            if os.name == "posix":
                assert stat.S_IMODE(target.stat().st_mode) == 0o644
    else:
        assert target.read_bytes() == expected
        if os.name == "posix":
            assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert len(created) == 1
    assert proposal["storage_generation"] == 0
    assert not list(tmp_path.glob(".controller.json.tmp.*"))


def test_unicode_persisted_bytes_trigger_retention(tmp_path, monkeypatch):
    old = {"retired": True, "decisions": {"accepted": "\U0001f600" * 1000}}
    db = {"profile": "native_workflow", "active": "live", "runs": {"live": {}, "old": old}}
    before = deepcopy(db)
    watermark = (len(encode(db)) + len(storage.encode_controller(db))) // 2
    assert len(encode(db)) < watermark < len(storage.encode_controller(db))
    monkeypatch.setattr(workflow_retention, "WATERMARK", watermark)
    compacted = workflow_retention.compact(tmp_path, db)
    assert db == before and set(compacted["runs"]) == {"live"}
    assert Store(tmp_path).resolve(compacted["archives"]["old"]) == old


def test_public_transition_fits_compact_capacity_without_losing_history(tmp_path, monkeypatch):
    c, state = controller(tmp_path)
    db = c._read(c._path())
    compact_size = len(storage.encode_controller(db))
    legacy_size = len(json.dumps(db, indent=2, sort_keys=True).encode()) + 1
    limit = (compact_size + legacy_size) // 2
    assert compact_size < limit < legacy_size
    monkeypatch.setattr(workflow_local, "MAX_BYTES", limit)
    result = c.apply("retire", state["run"], expected_revision=state["revision"],
                     native_reference=json.dumps({"request_reference": "fixture/user-retire", "reason": "Complete fixture"}))
    saved = json.loads(c._path().read_bytes())
    assert result["decisions"] == state["decisions"] and result["visits"] == state["visits"]
    assert c._path().read_bytes() == storage.encode_controller(saved)
    assert workflow_retention.capacity(saved, limit)["bytes"] == c._path().stat().st_size <= limit


@pytest.mark.skipif(os.open not in os.supports_dir_fd or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"),
                    reason="Restoration requires no-follow directory descriptors.")
def test_public_recovery_requires_receipt_and_preserves_original_provenance(tmp_path):
    from taskplane import workflow as w
    c, state, target, source, raw, checksum = displaced_database(tmp_path, approved=True)
    target.write_bytes(raw)
    with pytest.raises(w.Refusal, match="authentic prepared"):
        c.recover_initialization(source.name, checksum, state["run"], state["revision"], "fixture/original")
    assert target.read_bytes() == source.read_bytes() == raw
    target.unlink()
    result = c.recover_initialization(source.name, checksum, state["run"], state["revision"], "fixture/original")
    receipt = target.with_name(target.stem + ".recovery-" + checksum + ".json")
    original_receipt = receipt.read_bytes()
    retry = c.recover_initialization(source.name, checksum, state["run"], state["revision"], "fixture/later-retry")
    assert result["status"] == "restored" and retry["status"] == "already_restored"
    assert retry["request_reference"] == "fixture/original" and receipt.read_bytes() == original_receipt
    assert target.read_bytes() == source.read_bytes() == raw
    assert c.report()["decisions"] == state["decisions"]
