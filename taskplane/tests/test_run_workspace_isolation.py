"""New-run Git isolation; all repositories and host observations are fixtures."""
import subprocess
import json
import os
from pathlib import Path

import pytest

from taskplane import workflow as w, workflow_host as h


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repository(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Taskplane fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / ".gitignore").write_text(".taskplane/\n")
    (repo / "app.py").write_text("value = 1\n")
    git(repo, "add", ".gitignore", "app.py")
    git(repo, "commit", "-m", "fixture baseline")
    git(repo, "remote", "add", "origin", str(repo))
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    work = tmp_path / "work"
    git(repo, "worktree", "add", "--detach", str(work), "HEAD")
    return repo, work


def request():
    return {"request_reference": "fixture/isolated-start", "scope": {
        "criteria": ["AC"], "paths": {p: [p + ".json"] for p in w.PHASES},
        "verification_inputs": ["app.py"]}}


def test_dirty_linked_worktree_refused_before_initialization(tmp_path):
    _, work = repository(tmp_path)
    (work / "app.py").write_text("unrelated dirty work\n")
    c = h.Controller(work, "root", h.installed_adapter("codex"))
    with pytest.raises(w.Refusal, match="(?i)(clean|dirty)"):
        c.start(request())
    assert (work / "app.py").read_text() == "unrelated dirty work\n"
    assert not c._path().exists()


def test_primary_shared_checkout_refused_before_initialization(tmp_path):
    repo, _ = repository(tmp_path)
    c = h.Controller(repo, "root", h.installed_adapter("codex"))
    with pytest.raises(w.Refusal, match="(?i)(dedicated|linked|worktree)"):
        c.start(request())
    assert not c._path().exists()


def native_fixture(work, host, root="root", *, native_workspace=None):
    """Literal isolated metadata fixtures, never live initialization evidence."""
    selected = str(native_workspace or work)
    if host == "codex":
        target = Path(os.environ["CODEX_HOME"]) / "sessions" / (root + ".jsonl")
        row = {"type": "session_meta", "payload": {"id": root, "cwd": selected, "source": "cli"}}
    else:
        target = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects/fixture" / (root + ".jsonl")
        row = {"sessionId": root, "cwd": selected, "isSidechain": False}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(row) + "\n")


@pytest.mark.parametrize("host", ["codex", "claude"])
@pytest.mark.parametrize("global_crlf", [False, True])
def test_clean_native_worktree_and_dirty_idempotent_resume(tmp_path, monkeypatch, host, global_crlf):
    if global_crlf:
        config = tmp_path / 'global.gitconfig'
        config.write_text('[core]\n autocrlf = true\n')
        monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
    _, work = repository(tmp_path)
    assert git(work, 'status', '--porcelain') == ''
    native_fixture(work, host)
    c = h.Controller(work, "root", h.installed_adapter(host))
    if host == "claude":
        from taskplane.claude_worker_observations import supported_reader
        if not supported_reader():
            with pytest.raises(w.Refusal, match="unavailable"):
                c.start(request())
            assert not c._path().exists()
            return
    state = c.start(request())
    identity = state["native_initialization"]
    assert identity["clean_at_start"] and identity["base_revision"] == git(work, "rev-parse", "HEAD")
    (work / "app.py").write_text("authorized work remains\n")
    resumed = c.start(request())
    assert resumed["run"] == state["run"] and resumed["revision"] == state["revision"]
    assert resumed["native_initialization"] == identity
    assert (work / "app.py").read_text() == "authorized work remains\n"
    native_fixture(work, host, root='another-root')
    another = h.Controller(work, 'another-root', h.installed_adapter(host))
    with pytest.raises(w.Refusal, match='dirty'):
        another.start(request())


@pytest.mark.parametrize("host", ["codex", "claude"])
@pytest.mark.parametrize("defect", ["native-missing", "native-foreign", "base", "repository"])
def test_new_start_refuses_unverified_destination(tmp_path, host, defect):
    repo, work = repository(tmp_path)
    if defect != "native-missing":
        native_fixture(work, host, native_workspace=repo if defect == "native-foreign" else work)
    wanted = request()
    if defect == "repository":
        wanted["scope"]["repository"] = "https://example.invalid/foreign.git"
    if defect == "base":
        wanted["scope"]["base_revision"] = git(work, "rev-parse", "HEAD")
        git(work, "commit", "--allow-empty", "-m", "different base")
    c = h.Controller(work, "root", h.installed_adapter(host))
    with pytest.raises(w.Refusal):
        c.start(wanted)
    assert not c._path().exists()
