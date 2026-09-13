"""Session isolation regressions. Hook events here are explicitly simulated."""
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest
import host_capabilities as caps
import storage
import taskplane_lite as lite
import tp as cli


def invoke(monkeypatch, *args, event=None):
    out, err = io.StringIO(), io.StringIO()
    with monkeypatch.context() as call, redirect_stdout(out), redirect_stderr(err):
        if event is not None:
            call.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
            call.setenv("TASKPLANE_HOOK_PATH", "native")
        rc = cli.main(list(args))
    return rc, out.getvalue(), err.getvalue()


def hook(workspace, session, *, call="call", tool="Write", path="source.py"):
    return {"hook_event_name": "PreToolUse", "cwd": str(workspace),
            "session_id": session, "turn_id": "turn", "tool_use_id": call,
            "tool_name": tool, "tool_input": {"file_path": str(path)}}


@pytest.fixture
def repos(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKPLANE_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    parent = tmp_path / "parent"
    parent.mkdir()
    subprocess.run(["git", "init", "-q", str(parent)], check=True)
    (parent / "source.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "."], cwd=parent, check=True)
    for text in ("base", "change"):
        if text == "change":
            (parent / "source.py").write_text("value = 2\n")
        subprocess.run(["git", "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "commit", "-qam", text],
                       cwd=parent, check=True)
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(parent), str(checkout)], check=True)
    assert cli._install_codex_hooks(str(parent))["ok"]
    monkeypatch.chdir(parent)
    return parent, checkout


def activate(workspace, name):
    contract = lite.build_contract(name, read_only=True, max_actions=100)
    contract["budget"].pop("max_tokens", None)
    lite.activate(str(workspace), contract, snapshot=lite.git_head(str(workspace)))
    return Path(lite.active_contract_path(str(workspace))), contract


def test_native_hook_proves_only_its_session_across_fresh_checkout(repos, monkeypatch):
    parent, checkout = repos
    monkeypatch.setenv("CODEX_THREAD_ID", "session-a")
    rc, output, err = invoke(monkeypatch, "screen", event=hook(parent, "session-a"))
    assert rc == 0 and not output, (output, err)
    parent_home = storage.project_taskplane_home(str(parent))
    checkout_home = storage.project_taskplane_home(str(checkout))
    assert parent_home != checkout_home
    assert not Path(checkout_home).exists()
    snapshot = cli._host_capability_snapshot(str(checkout))
    assert snapshot.effective_path == "native_effective"
    monkeypatch.setenv("CODEX_THREAD_ID", "session-b")
    assert cli._host_capability_snapshot(str(checkout)).effective_path != "native_effective"
    monkeypatch.delenv("CODEX_THREAD_ID")
    assert cli._host_capability_snapshot(str(checkout)).effective_path != "native_effective"


def test_bridge_never_transfers_trust_through_native_cache(repos, tmp_path):
    parent, checkout = repos
    local, shared = str(tmp_path / "local"), str(tmp_path / "host")
    caps.record_runtime_hook_receipt(local, native_home=shared, hook_path="bridge",
                                    event=hook(parent, "session-a"))
    assert not Path(shared).exists()
    assert caps.runtime_hook_observations(str(checkout), native_home=shared,
        session_id="session-a", workspace=str(checkout)) == {}


def test_managed_locators_and_run_artifacts_are_owned_by_one_session(repos, monkeypatch):
    parent, _ = repos
    from run_store import RunStore
    identity = storage.resolve_repository_identity(str(parent))
    saved = []
    for session in ("session-a", "session-b"):
        monkeypatch.setenv("CODEX_THREAD_ID", session)
        assert storage.load_workspace_locator(str(parent)) is None
        store = RunStore(workspace=str(parent))
        run_id = "run-" + session
        store.create(identity, run_id=run_id, checkout=str(parent),
                     host={"kind": "codex", "session_id": session},
                     target={"kind": "workspace", "revision": lite.git_head(str(parent))})
        layout = storage.resolve_layout(identity, home=store.home, run_id=run_id)
        path = Path(storage.write_workspace_locator(str(parent), identity=identity,
                    layout=layout, run_id=run_id))
        saved.append((path, path.read_bytes(), store.home))
    assert saved[0][0] != saved[1][0] and saved[0][2] != saved[1][2]
    assert saved[0][0].read_bytes() == saved[0][1]
    monkeypatch.setenv("CODEX_THREAD_ID", "session-a")
    assert storage.load_workspace_locator(str(parent))["run_id"] == "run-session-a"
    monkeypatch.delenv("CODEX_THREAD_ID")
    assert storage.load_workspace_locator(str(parent)) is None


def test_other_session_cannot_read_meter_clear_or_block_owner(repos, monkeypatch):
    parent, _ = repos
    monkeypatch.setenv("CODEX_THREAD_ID", "session-a")
    owned, contract = activate(parent, "owner-a")
    before = owned.read_bytes()
    _, output, err = invoke(monkeypatch, "screen", event=hook(parent, "session-b"))
    assert not output, (output, err)
    assert owned.read_bytes() == before
    monkeypatch.setenv("CODEX_THREAD_ID", "session-b")
    assert lite.load_active(str(parent)) is None
    foreign, _ = activate(parent, "owner-b")
    assert foreign != owned
    lite.clear(str(parent))
    assert owned.read_bytes() == before
    monkeypatch.setenv("CODEX_THREAD_ID", "session-a")
    _, output, err = invoke(monkeypatch, "screen", event=hook(parent, "session-a"))
    assert json.loads(output)["decision"] == "block", (output, err)
    assert lite.load_active(str(parent))["task_id"] == contract["task_id"]


def test_corrupt_legacy_and_other_session_contracts_do_not_block_new_session(repos, monkeypatch):
    parent, _ = repos
    legacy = parent / ".taskplane" / "active_contract.json"
    legacy.parent.mkdir(exist_ok=True)
    legacy.write_text("corrupt legacy contract")
    monkeypatch.setenv("CODEX_THREAD_ID", "session-a")
    own = Path(lite.active_contract_path(str(parent)))
    own.parent.mkdir(parents=True)
    own.write_text("corrupt session contract")
    _, output, err = invoke(monkeypatch, "screen", event=hook(parent, "session-b"))
    assert not output, (output, err)
    _, output, err = invoke(monkeypatch, "screen", event=hook(parent, "session-a"))
    assert json.loads(output)["decision"] == "block", (output, err)


def test_session_routes_review_hooks_to_checkout_and_detaches_after_clear(repos, monkeypatch):
    parent, checkout = repos
    monkeypatch.setenv("CODEX_THREAD_ID", "session-a")
    _, contract = activate(checkout, "isolated review")
    storage.bind_review_session(str(checkout), contract["task_id"])
    _, output, err = invoke(monkeypatch, "screen", event=hook(
        parent, "session-a", path=checkout / "source.py"))
    assert json.loads(output)["decision"] == "block", (output, err)
    _, output, err = invoke(monkeypatch, "screen", event=hook(
        parent, "session-b", path=checkout / "source.py"))
    assert not output, (output, err)
    lite.clear(str(checkout))
    assert storage.review_session_workspace() is None
    _, output, err = invoke(monkeypatch, "screen", event=hook(
        parent, "session-a", call="after-clear", path=checkout / "source.py"))
    assert not output, (output, err)


def test_routing_cannot_reinterpret_a_relative_write_as_checkout_permission(repos, monkeypatch):
    parent, checkout = repos
    monkeypatch.setenv("CODEX_THREAD_ID", "session-a")
    contract = lite.build_contract("review", read_only=True,
                                  write_allow=[str(checkout / "allowed.txt")])
    contract["budget"].pop("max_tokens", None)
    lite.activate(str(checkout), contract, snapshot=lite.git_head(str(checkout)))
    storage.bind_review_session(str(checkout), contract["task_id"])
    _, output, err = invoke(monkeypatch, "screen", event=hook(
        parent, "session-a", path="allowed.txt"))
    assert json.loads(output)["decision"] == "block", (output, err)
    _, output, err = invoke(monkeypatch, "screen", event=hook(
        parent, "session-a", call="absolute-allowed", path=checkout / "allowed.txt"))
    assert not output, (output, err)


def test_real_review_start_keeps_two_sessions_runs_and_committed_scope_separate(repos, monkeypatch):
    parent, checkout = repos
    records = []
    for session in ("session-a", "session-b"):
        monkeypatch.setenv("CODEX_THREAD_ID", session)
        monkeypatch.delenv("TASKPLANE_HOME", raising=False)
        rc, output, err = invoke(monkeypatch, "screen", event=hook(parent, session))
        assert rc == 0 and not output, (output, err)
        # No readiness stub: the real hook above is the only fixture evidence.
        rc, output, err = invoke(monkeypatch, "review", "start", "HEAD", "--base",
                                 "HEAD^", "--workspace", str(checkout))
        assert rc == 2, (output, err)
        opened = json.loads(output)
        assert opened["status"] == "needs_user"
        assert opened["contract"]["status"] == "active"
        assert opened["preflight"]["status"] == "ready"
        records.append((Path(lite.active_contract_path(str(checkout))),
                        storage.load_workspace_locator(str(checkout)), opened))
    assert records[0][0] != records[1][0]
    assert records[0][0].exists() and records[1][0].exists()
    assert records[0][2]["run_id"] != records[1][2]["run_id"]
    # Local reviews use independent session control, without a managed locator.
    assert records[0][1] is None and records[1][1] is None
    assert subprocess.check_output(["git", "diff", "HEAD", "--"], cwd=checkout, text=True) == ""
