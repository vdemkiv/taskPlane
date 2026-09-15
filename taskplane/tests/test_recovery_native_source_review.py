"""Native source-review recovery: source facts without delivery authority."""
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import subprocess
import types

import pytest

import depgraph
import review
import run_context
import taskplane_lite as lite
import tp as cli


@pytest.fixture
def source(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(("TASKPLANE_", "CLAUDE_", "CODEX_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "store"))
    ws = tmp_path / "source"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "source.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c",
        "user.email=fixture@example.invalid", "commit", "-qm", "source"], cwd=ws, check=True)
    return ws


def invoke(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(list(args))
    return rc, out.getvalue(), err.getvalue()


def test_source_review_needs_no_delivery_setup_or_execution_authority(source, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("ordinary source review invoked delivery machinery")
    monkeypatch.setattr(cli, "_enforcement_check", forbidden)
    monkeypatch.setattr(run_context, "bind", forbidden)
    monkeypatch.setattr(depgraph, "scan", forbidden)
    monkeypatch.setattr(review, "start_review", forbidden)
    monkeypatch.setattr(lite, "activate", forbidden)
    rc, out, err = invoke("review", "start", "--scope", "repository",
                          "--max-tokens", "1000", "--workspace", str(source))
    assert rc == 0, (out, err)
    manifest = json.loads(out)
    assert manifest["status"] == "ready" and len(out.encode()) < 2048
    assert manifest["budget"] == {"mode": "advisory", "max_tokens": 1000, "max_actions": None}
    assert not lite.load_active(str(source))
    assert not (source / ".gitignore").exists()
    selected = review.review_evidence_runtime.ArtifactStore(str(source)).read(manifest["source"])
    assert selected["files"] == ["source.py"]
    assert selected["scope"] == "repository" and selected["patch"] == ""


def test_review_does_not_replace_or_clear_existing_contract(source):
    contract = lite.build_contract("Product ownership", read_only=True, scope=["docs/**"],
                                   write_allow=["docs/**"])
    lite.activate(str(source), contract, snapshot=lite.git_head(str(source)))
    path = Path(lite.active_contract_path(str(source)))
    before = path.read_bytes()
    rc, out, err = invoke("review", "start", "--scope", "repository", "--workspace", str(source))
    assert rc == 0, (out, err)
    assert path.read_bytes() == before


def test_diff_review_is_scoped_and_excludes_generated_untracked_evidence(source):
    (source / "source.py").write_text("VALUE = 2\n")
    (source / "other.py").write_text("OTHER = 3\n")
    (source / ".taskplane").mkdir(exist_ok=True)
    (source / ".taskplane" / "generated.json").write_text("{}")
    assert review.canonical_diff_files(str(source), "HEAD") == ["other.py", "source.py"]
    rc, out, err = invoke("review", "start", "--paths", "source.py", "--workspace", str(source))
    assert rc == 0, (out, err)
    selected = review.review_evidence_runtime.ArtifactStore(str(source)).read(json.loads(out)["source"])
    assert selected["files"] == ["source.py"]
    assert "+VALUE = 2" in selected["patch"] and "OTHER" not in selected["patch"]


def test_repository_scope_refuses_dirty_tracked_snapshot(source):
    (source / "source.py").write_text("VALUE = 2\n")
    rc, out, err = invoke("review", "start", "--scope", "repository", "--workspace", str(source))
    assert rc == 1 and "differs from the checkout" in out, (out, err)
    assert not lite.load_active(str(source))


@pytest.mark.parametrize("args", [("--scope", "repository", "--base", "HEAD"),
                                  ("--scope", "repository", "--max-actions", "0"),
                                  ("--scope", "repository", "--max-tokens", "-1")])
def test_invalid_source_options_do_not_create_authority(source, args):
    rc, out, err = invoke("review", "start", *args, "--workspace", str(source))
    assert rc == 1, (out, err)
    assert not lite.load_active(str(source))


def test_empty_diff_has_actionable_repository_option(source):
    rc, out, err = invoke("review", "start", "--workspace", str(source))
    assert rc == 1 and "--scope repository" in out, (out, err)


@pytest.mark.parametrize("active_delivery", [False, True])
def test_session_context_sweeps_only_for_existing_delivery(source, monkeypatch, active_delivery):
    if active_delivery:
        contract = lite.build_contract("explicit delivery")
        lite.activate(str(source), contract, snapshot=lite.git_head(str(source)))
    calls = []
    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "native")
    monkeypatch.setattr(lite, "sweep_completed_worker_contracts",
                        lambda workspace, loop_state: calls.append(workspace) or [])
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("{}"))
    out = io.StringIO()
    with redirect_stdout(out):
        rc = cli.cmd_context(types.SimpleNamespace(workspace=str(source)))
    assert rc == 0
    assert calls == ([str(source)] if active_delivery else [])
    if not active_delivery:
        assert not out.getvalue()


def test_hook_context_without_delivery_emits_no_setup_prompt(source, monkeypatch):
    monkeypatch.setenv("TASKPLANE_HOOK_PATH", "native")
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps({
        "session_id": "native-source-review", "cwd": str(source), "hook_event_name": "SessionStart"})))
    rc, out, err = invoke("context", "--workspace", str(source))
    assert rc == 0 and not out, (out, err)


def test_direct_context_without_delivery_remains_informative(source):
    rc, out, err = invoke("context", "--workspace", str(source))
    assert rc == 0 and "No active Taskplane delivery" in out, (out, err)
    assert "onboard" not in out


def test_pending_native_lookup_can_request_a_smaller_transcript_window(tmp_path):
    path = tmp_path / "session.jsonl"
    first, last = json.dumps({"id": "older"}), json.dumps({"id": "pending"})
    path.write_text(first + "\n" + last + "\n")
    assert review._host_review_records(str(path), len(last.encode()) + 2) == [{"id": "pending"}]
    with pytest.raises(ValueError, match="byte limit"):
        review._host_review_records(str(path), 0)
