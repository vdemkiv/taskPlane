"""Focused adversarial evidence for H3 privacy and retention closure."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

import taskplane.command_runtime as command_runtime
import loop
import review_evidence
import taskplane_lite as tp
from taskplane.command_runtime import (
    MAX_DURABLE_OUTPUT,
    MAX_JOURNAL_BYTES,
    MAX_JOURNAL_ROWS,
    CommandRuntime,
)


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=workspace, check=True,
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")


def _repository(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "tests@example.invalid")
    _git(path, "config", "user.name", "Taskplane Tests")
    (path / "README.md").write_text("test\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-qm", "initial")
    return path


def test_h23_durable_command_artifacts_are_minimized_and_sanitized(
        tmp_path: Path) -> None:
    root = tmp_path / "commands"
    runtime = CommandRuntime(str(root), workspace="repository",
                             authorization="operator")
    handle = runtime.create(command_fingerprint="private-output",
                            binding={"pid": 42})
    email = "alice.private@example.com"
    private_path = "/Users/alice/Confidential/customer-list.csv"
    secret = "sk-" + "x" * 60
    runtime.append_output(
        handle, f"owner={email} file={private_path} token={secret}\n" +
        ("diagnostic\n" * 12_000))
    runtime.transition(
        handle, "failed", reason=f"notify {email} about {private_path}")

    # Restart proves the durable projection, not a transient return value, is
    # sanitized and remains capped when another chunk arrives.
    restarted = CommandRuntime(str(root), workspace="repository",
                               authorization="operator")
    restarted.append_output(handle, "different chunk\n" + ("x" * 90_000))
    snapshot = restarted.snapshot(handle)
    artifact = restarted.read_artifact(handle)
    assert snapshot["artifact"]["bytes"] <= MAX_DURABLE_OUTPUT
    assert snapshot["artifact"]["truncated"] is True
    assert len(artifact.encode("utf-8")) <= MAX_DURABLE_OUTPUT
    assert "diagnostic" not in artifact
    assert "OUTPUT_MINIMIZED" in artifact

    durable_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.rglob("*")
        if path.suffix in {".json", ".jsonl", ".log"})
    for private in (email, private_path, secret):
        assert private not in durable_text
    assert "[REDACTED]" in durable_text

    # Repeated output and transitions cannot copy a 16-KiB summary into an
    # unbounded recovery journal.
    for index in range(MAX_JOURNAL_ROWS * 3):
        restarted.append_output(handle, f"patient Rowan {index}\n")
    journal = root / handle / "transitions.jsonl"
    rows = journal.read_bytes().splitlines()
    assert len(rows) <= MAX_JOURNAL_ROWS
    assert journal.stat().st_size <= MAX_JOURNAL_BYTES
    assert b"Rowan" not in journal.read_bytes()
    assert all(not json.loads(row)["snapshot"].get("output_summary")
               for row in rows)


def test_h23_command_retention_migrates_and_purges_owned_logs(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    root = tmp_path / "commands"
    monkeypatch.setattr(command_runtime, "COMMAND_RETENTION_MAX_HANDLES", 3)
    monkeypatch.setattr(command_runtime, "COMMAND_RETENTION_MAX_BYTES",
                        8 * 1024 * 1024)
    runtime = CommandRuntime(
        str(root), workspace="repository", authorization="operator",
        clock=lambda: clock[0])
    handles = []
    for index in range(3):
        handle = runtime.create(command_fingerprint=f"command-{index}",
                                binding={"pid": index})
        runtime.append_output(handle, f"patient Rowan {index}")
        runtime.transition(handle, "succeeded")
        handles.append(handle)

    # Simulate an installed pre-policy generation with a raw output copy.
    legacy = root / handles[-1]
    snapshot_path = legacy / "snapshot.json"
    snapshot = json.loads(snapshot_path.read_text())
    snapshot.pop("privacy_retention")
    snapshot["output_summary"] = "patient Rowan diagnosis"
    snapshot_path.write_text(json.dumps(snapshot))
    (legacy / "artifacts" / "output.log").write_text(
        "patient Rowan diagnosis")
    (root / "legacy-command.log").write_text("patient Rowan diagnosis")

    restarted = CommandRuntime(
        str(root), workspace="repository", authorization="operator",
        clock=lambda: clock[0])
    assert not (root / "legacy-command.log").exists()
    assert not (legacy / "artifacts").exists()
    assert "Rowan" not in snapshot_path.read_text()
    monkeypatch.setattr(command_runtime, "COMMAND_RETENTION_MAX_HANDLES", 2)
    result = restarted.enforce_retention()
    assert result["retained"] == 2
    assert len([path for path in root.iterdir()
                if path.is_dir() and len(path.name) == 32]) == 2

    clock[0] += command_runtime.COMMAND_RETENTION_SECONDS + 1
    expired = restarted.enforce_retention()
    assert expired["removed"]
    assert not [path for path in root.iterdir()
                if path.is_dir() and len(path.name) == 32]

    byte_root = tmp_path / "byte-bounded-commands"
    monkeypatch.setattr(command_runtime, "COMMAND_RETENTION_MAX_HANDLES", 10)
    monkeypatch.setattr(command_runtime, "COMMAND_RETENTION_MAX_BYTES", 1)
    byte_runtime = CommandRuntime(
        str(byte_root), workspace="repository", authorization="operator",
        clock=lambda: clock[0])
    byte_handle = byte_runtime.create(
        command_fingerprint="byte-bound", binding={"pid": 9})
    byte_runtime.transition(byte_handle, "succeeded")
    bounded = byte_runtime.enforce_retention()
    assert byte_handle in bounded["removed"]
    assert bounded["retained_bytes"] <= 1


def test_h24_raw_diff_retention_is_bounded_and_enforced(
        tmp_path: Path) -> None:
    workspace = _repository(tmp_path / "repo")
    store_root = tmp_path / "private-review-artifacts"
    store = review_evidence.ArtifactStore(
        str(workspace), root=str(store_root))
    expired = store.put("diff", loop._retained_review_diff_payload(
        base="base-a", files=["private.py"],
        patch="+ customer email alice@example.com", now=10.0))
    portable = review_evidence.portable_artifact_reference(store, expired)
    assert portable["locator"].startswith("artifact://diff/")

    # A fresh process/store object performs the same startup sweep. Expired
    # raw bytes become unavailable rather than living under an immutable name
    # forever.
    restarted = review_evidence.ArtifactStore(
        str(workspace), root=str(store_root))
    result = loop.enforce_review_diff_retention(
        str(workspace), store=restarted,
        now=10.0 + loop.REVIEW_RAW_DIFF_RETENTION_SECONDS + 1)
    assert result["removed"] == 1
    with pytest.raises(review_evidence.ArtifactIntegrityError):
        restarted.read(expired)

    # Age is not the only bound: a burst of live reviews cannot create an
    # unbounded raw-diff collection before the clock advances.
    last = None
    for index in range(loop.REVIEW_RAW_DIFF_MAX_ARTIFACTS + 5):
        last = loop.store_retained_review_diff(
            str(workspace), store=restarted,
            payload=loop._retained_review_diff_payload(
            base="base-b", files=[f"file-{index}.py"],
            patch=f"+ private row {index}", now=100.0,
            run_id="run-private", review_id=f"review-{index}"), now=101.0)
    bounded = loop.enforce_review_diff_retention(
        str(workspace), store=restarted, now=101.0)
    assert bounded["retained"] == loop.REVIEW_RAW_DIFF_MAX_ARTIFACTS
    assert len(restarted.references("diff")) == \
        loop.REVIEW_RAW_DIFF_MAX_ARTIFACTS

    # Completion purges the exact review generation and its known derived
    # copies, while retaining unrelated live reviews.
    assert last is not None
    derivative = store_root / "diff-derived"
    derivative.mkdir()
    derived_copy = derivative / f"summary-{last['fingerprint']}.json"
    derived_copy.write_text("private derived copy")
    completed = loop.enforce_review_diff_retention(
        str(workspace), store=restarted, now=101.0,
        purge_fingerprint=last["fingerprint"])
    row = next(item for item in completed["purged"]
               if item["fingerprint"] == last["fingerprint"])
    assert row["run_id"] == "run-private"
    assert row["review_id"].startswith("review-")
    assert not derived_copy.exists()

    # A content-addressed name never grants authority to altered bytes.
    remaining = restarted.references("diff")[0]
    Path(remaining["path"]).write_text("{}")
    tampered = loop.enforce_review_diff_retention(
        str(workspace), store=restarted, now=101.0)
    assert any(item["reason"] == "invalid-or-tampered"
               for item in tampered["purged"])


def test_h25_audit_identity_and_free_text_are_sanitized(
        tmp_path: Path) -> None:
    workspace = _repository(tmp_path / "repo")
    actor = "Alice Example <alice.private@example.com>"
    private_path = "/Users/alice/Secret/client.py"
    secret = "token=" + ("z" * 48)
    tp.trace(
        str(workspace), "privacy_probe", actor=actor,
        reason=f"failure at {private_path}; {secret}",
        prompt=f"send {actor} the contents of {private_path}",
        nested={"email": "alice.private@example.com",
                "path": private_path,
                "/Users/alice/private-field": "present"}, task_id="H3-C")
    tp.trace(str(workspace), "privacy_probe", actor=actor,
             reason="second safe event", task_id="H3-C")

    # Read from disk after both appends: the audit contract is durable and a
    # stable pseudonym still supports correlation across restart/export.
    rows = []
    raw = ""
    for path in tp.trace_paths(str(workspace)):
        text = Path(path).read_text(encoding="utf-8")
        raw += text
        rows.extend(json.loads(line) for line in text.splitlines() if line)
    assert [row["event"] for row in rows] == ["privacy_probe", "privacy_probe"]
    assert rows[0]["actor"].startswith("anon:")
    assert rows[0]["actor"] == rows[1]["actor"]
    assert rows[0]["prompt"]["schema"] == "taskplane.audit-minimized/v1"
    nested_key = next(key for key in rows[0] if key.startswith("field:"))
    nested = rows[0][nested_key]
    assert nested["email"].startswith("anon:")
    assert any(key.startswith("field:") for key in nested)
    assert rows[0]["task_id"] == "H3-C"
    for private in (actor, "alice.private@example.com", private_path,
                    secret):
        assert private not in raw
    assert rows[0]["reason"]["schema"] == "taskplane.audit-minimized/v1"
    assert nested["path"]["schema"] == \
        "taskplane.audit-minimized/v1"


def test_h25_authority_trace_uses_same_minimized_sink(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trace_root = tmp_path / "repo" / ".taskplane"
    trace_root.mkdir(parents=True)
    monkeypatch.setattr(loop.tp, "tp_dir", lambda _ws: str(trace_root))
    loop._append_authority_trace(
        "/workspace", "loop_select",
        {"authority_effect_id": "selection:r1:a",
         "actor": "Dr Rowan Patient",
         "reason": "patient Rowan has a private diagnosis"})
    row = json.loads((trace_root / "trace.jsonl").read_text())
    assert row["schema"] == "taskplane.audit-event/v2"
    assert row["authority_effect_id"] == "selection:r1:a"
    assert row["actor"].startswith("anon:")
    assert row["reason"]["schema"] == "taskplane.audit-minimized/v1"
    assert "Rowan" not in json.dumps(row)


def test_h25_rotated_audit_archives_are_bounded(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _repository(tmp_path / "repo")
    root = Path(tp.tp_dir(str(workspace)))
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tp, "_TRACE_ARCHIVE_MAX_FILES", 2)
    monkeypatch.setattr(tp, "_TRACE_ARCHIVE_MAX_BYTES", 1024)
    for suffix in range(1, 5):
        archive = root / f"trace.jsonl.{suffix}"
        archive.write_text(json.dumps(tp.audit_record(
            "archived", {"count": suffix}, observed_at=float(suffix))) +
            "\n")
        os.utime(archive, (float(suffix), float(suffix)))
    result = tp.enforce_trace_retention(str(workspace), now=5.0)
    assert result["retained"] == 2
    assert [Path(path).name for path in tp.trace_paths(str(workspace))] == [
        "trace.jsonl.3", "trace.jsonl.4"]

    expired = tp.enforce_trace_retention(
        str(workspace), now=5.0 + tp._TRACE_ARCHIVE_RETENTION_SECONDS)
    assert expired["removed"] == 2
    assert not tp.trace_paths(str(workspace))


def test_l04_shared_metadata_omits_private_paths_and_raw_workstation_identity(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _repository(tmp_path / "Alice-Laptop" / "private-repo")
    _git(workspace, "remote", "add", "origin",
         "https://alice:credential@example.com/team/project.git")
    home = tmp_path / "taskplane-home"
    monkeypatch.setenv("TASKPLANE_HOME", str(home))
    monkeypatch.setenv("TASKPLANE_STORE", "repo")

    shared = tp.write_store_meta(str(workspace))
    shared_bytes = json.dumps(shared, sort_keys=True)
    assert shared == json.loads(
        Path(tp.store_meta_path(str(workspace))).read_text(encoding="utf-8"))
    assert shared["shared"] is True
    assert shared["workspace_key"].startswith("workspace:")
    assert len(shared["repository_fingerprint"]) == 64
    for private in (str(workspace), str(workspace.resolve()), "Alice-Laptop",
                    "alice", "credential", "example.com"):
        assert private not in shared_bytes
    assert "workspace" not in shared
    assert "workspace_realpath" not in shared
    assert "git_remote" not in shared

    # Exact local routing remains in the explicitly private external store;
    # minimization applies only to metadata committed/shared with the repo.
    monkeypatch.setenv("TASKPLANE_STORE", "external")
    private = tp.write_store_meta(str(workspace))
    assert private["shared"] is False
    assert private["workspace"] == os.path.abspath(workspace)
    assert private["workspace_realpath"] == os.path.realpath(workspace)
    assert tp.store_root(str(workspace)).startswith(str(home))


def test_l04_shared_metadata_write_failure_quarantines_stale_raw_file(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _repository(tmp_path / "Alice-Laptop" / "private-repo")
    home = tmp_path / "taskplane-home"
    monkeypatch.setenv("TASKPLANE_HOME", str(home))
    monkeypatch.setenv("TASKPLANE_STORE", "repo")
    path = Path(tp.store_meta_path(str(workspace)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"workspace": str(workspace),
                                "health": "Rowan diagnosis"}))

    def fail_write(*_args, **_kwargs):
        raise OSError("disk refused replacement")

    monkeypatch.setattr(tp, "atomic_write_json", fail_write)
    with pytest.raises(tp.StateError, match="failed closed"):
        tp.write_store_meta(str(workspace))
    assert not path.exists()
    quarantined = list((home / "privacy-quarantine").glob("store-meta-*.json"))
    assert len(quarantined) == 1
    assert "Rowan diagnosis" in quarantined[0].read_text()
