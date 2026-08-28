"""Focused adversarial evidence for H3 privacy and retention closure."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

import loop
import review_evidence
import taskplane_lite as tp
from taskplane.command_runtime import (
    MAX_DURABLE_OUTPUT,
    CommandRuntime,
)


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=workspace, check=True,
                   capture_output=True, text=True)


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
    assert "diagnostic" in artifact

    durable_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.rglob("*")
        if path.suffix in {".json", ".jsonl", ".log"})
    for private in (email, private_path, secret):
        assert private not in durable_text
    assert "[REDACTED_EMAIL]" in durable_text
    assert "[REDACTED_PATH]" in durable_text
    assert "[REDACTED]" in durable_text


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
    for index in range(loop.REVIEW_RAW_DIFF_MAX_ARTIFACTS + 5):
        restarted.put("diff", loop._retained_review_diff_payload(
            base="base-b", files=[f"file-{index}.py"],
            patch=f"+ private row {index}", now=100.0))
    bounded = loop.enforce_review_diff_retention(
        str(workspace), store=restarted, now=101.0)
    assert bounded["retained"] == loop.REVIEW_RAW_DIFF_MAX_ARTIFACTS
    assert len(restarted.references("diff")) == \
        loop.REVIEW_RAW_DIFF_MAX_ARTIFACTS


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
    assert rows[0]["prompt"]["minimized"] is True
    assert rows[0]["nested"]["email"].startswith("anon:")
    assert any(key.startswith("field:") for key in rows[0]["nested"])
    assert rows[0]["task_id"] == "H3-C"
    for private in (actor, "alice.private@example.com", private_path,
                    secret):
        assert private not in raw
    assert "[REDACTED_PATH]" in raw
    assert "[REDACTED_SECRET]" in raw


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
