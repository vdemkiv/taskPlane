"""Exercise scope selection against real git history and aggregate failure cases."""
import json
import subprocess

import pytest
from scripts.ci_scope import ALWAYS_JOBS, FULL_JOBS, focused_change, select_mode, verify_results


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()
    git("init", "-q")
    git("config", "user.name", "CI fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path/"README.md").write_text("before")
    (tmp_path/"runtime.py").write_text("before")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    return tmp_path, git, base


@pytest.mark.parametrize("path,expected", [("README.md", "focused"), ("skills/x/SKILL.md", "focused"),
    ("runtime.py", "full"), ("docs/helper.py", "full"), (".github/workflows/ci.yml", "full"),
    ("requirements-dev.lock", "full"), ("unknown.md", "full")])
def test_real_diff_is_focused_only_for_allowed_documents(repo, path, expected):
    root, git, base = repo
    target = root/path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("after")
    git("add", ".")
    git("commit", "-qm", "change")
    assert select_mode(root, base, git("rev-parse", "HEAD")) == expected


def test_missing_history_invalid_refs_and_empty_diff_require_full(repo):
    root, _, base = repo
    for ref in ("0"*40, "f"*40, "--output=escape", "", base):
        assert select_mode(root, ref, base) == "full"


@pytest.mark.parametrize("path", [".codex-plugin/plugin.json", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"])
def test_version_changes_do_not_hide_manifest_behavior_changes(path):
    old = {"name": "taskplane", "version": "2.35.7", "source": "."}
    if "marketplace" in path:
        old["plugins"] = [{"name": "taskplane", "version": "2.35.7", "source": "."}]
    new = json.loads(json.dumps(old))
    new["version"] = "2.35.8"
    if "plugins" in new: new["plugins"][0]["version"] = "2.35.8"
    def allowed():
        return focused_change(path, "M", json.dumps(old).encode(), json.dumps(new).encode())
    assert allowed()
    new["source"] = "elsewhere"
    assert not allowed()
    new["source"] = "."
    new["version"] = "bad"
    assert not allowed()
    assert not focused_change(path, "A", b"", json.dumps(old).encode())
    assert not focused_change(path, "M", b"broken", b"{}")
    assert not focused_change("docs/guide.md", "T", b"", b"")


def results(mode):
    value = {name: {"result": "success"} for name in ALWAYS_JOBS | FULL_JOBS}
    value["scope"]["outputs"] = {"mode": mode}
    if mode == "focused":
        for name in FULL_JOBS: value[name]["result"] = "skipped"
    return value


@pytest.mark.parametrize("mode", ["full", "focused"])
def test_aggregate_accepts_exact_success_and_rejects_failures(mode):
    verify_results(results(mode))
    for name in ALWAYS_JOBS | FULL_JOBS:
        for outcome in ("failure", "cancelled", "skipped"):
            value = results(mode)
            if value[name]["result"] == outcome: continue
            value[name]["result"] = outcome
            with pytest.raises(ValueError): verify_results(value)
    value = results(mode)
    del value["suites"]
    with pytest.raises(ValueError): verify_results(value)
    value = results(mode)
    value["scope"]["outputs"] = {}
    with pytest.raises(ValueError): verify_results(value)
