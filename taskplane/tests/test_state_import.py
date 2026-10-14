"""Archive-loss recovery through real local adapters; no live-host certification."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import sys

import pytest

from taskplane import flow, state_import as recovery, workflow as w, workflow_host as h, workflow_local as local
from taskplane.tests.test_workflow_evidence import prepare
from taskplane.tests.test_workflow_local import submit, decide, present
from taskplane.tests.test_ordinary_checkpoint_reply import ordinary


def pin(path, raw):
    path.write_bytes(raw)
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}


@pytest.fixture(params=["codex", "claude"])
def lost(tmp_path, request):
    root = tmp_path / "workspace"
    root.mkdir()
    source, _, _ = prepare(root)
    c = h.Controller(root, "root", h.installed_adapter(request.param))
    state = c.start({"scope": source["scope"], "request_reference": "fixture/original-user"})
    state = submit(c, state)
    approval = ordinary(state)
    backup = tmp_path / "preserved"
    backup.mkdir()
    original = c._path().read_bytes()
    packet = w.current(state)["packet"]
    request_data = {"schema": recovery.REQUEST, "host": request.param, "binding": w.binding(state, packet),
        "request_reference": "fixture/user-recover-original-run", "controller": pin(backup / "controller.json", original),
        "approval": pin(backup / "approval.json", json.dumps(approval).encode()), "artifacts": {}}
    for i, relative in enumerate(packet["manifest"]):
        request_data["artifacts"][relative] = pin(backup / str(i), (root / relative).read_bytes())
    # Simulate a host archive omitting ignored runtime data. The backup is outside
    # the checkout; no original initialization marker or context store survives.
    shutil.rmtree(root / ".taskplane")
    for relative in packet["manifest"]:
        (root / relative).unlink(missing_ok=True)
    request_file = backup / "request.json"
    proof = pin(request_file, json.dumps(request_data).encode())
    return c, state, original, approval, request_data, proof


def invoke(lost, mode="inspect"):
    c, _, _, _, _, proof = lost
    return recovery.recover(c, proof["path"], proof["sha256"], mode=mode)


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_archive_loss_import_preserves_checkpoint_and_requires_separate_approval(lost):
    c, state, original, approval, _, _ = lost
    before = tree(c.workspace)
    preview = invoke(lost)
    assert preview["status"] == "ready_to_import" and preview["approval_recorded"] is False
    assert tree(c.workspace) == before
    result = invoke(lost, "apply")
    assert result["status"] == "imported" and result["missing_history"]
    assert Path(result["original_controller"]["path"]).read_bytes() == original
    assert not c._path().with_name(c.adapter.markername).exists()
    observed = c.report()
    assert observed["revision"] == state["revision"] and observed["run"] == state["run"]
    assert observed["visits"] == state["visits"] and observed["decisions"] == {}
    assert not observed.get("parent_hook_readiness")
    assert not local.Harness(c.workspace, c.root).presentation_valid(observed)
    assert observed["state_import"]["historical_context_refs_unverified"]
    assert invoke(lost, "apply")["status"] == "already_imported"
    accepted = decide(c, observed, approval)
    assert accepted["decisions"][approval["event_id"]]["provenance"]["relay"] == approval["relay"]
    with pytest.raises(w.Refusal, match="dashboard handoff"):
        c.apply("advance", state["run"], expected_revision=accepted["revision"], phase="design")
    present(c, accepted)  # A newly generated linked fixture, never a restored old observation.
    advanced = c.apply("advance", state["run"], expected_revision=accepted["revision"], phase="design")
    assert w.current(advanced)["phase"] == "design" and w.current(advanced)["decision"] == "not_requested"
    assert len(advanced["decisions"]) == 1
    from taskplane.context_handoff import Session, consume_required
    receipt, returned = consume_required(Session(c.workspace, advanced))
    assert returned and receipt["consumed_inputs"]
    assert Path(result["original_controller"]["path"]).read_bytes() == original


@pytest.mark.parametrize("bad", ["checksum", "host", "root", "revision", "approval", "missing_artifact", "artifact_hash",
                                      "source_drift", "existing", "child", "protected", "symlink", "unresolved", "workers", "boolean_revision"])
def test_invalid_import_does_not_write_or_replace_evidence(lost, bad):
    c, _, _, _, request, proof = lost
    request = deepcopy(request)
    if bad == "checksum":
        proof["sha256"] = "0" * 64
    elif bad == "host":
        request["host"] = "other"
    elif bad in {"root", "revision"}:
        request["binding"][bad] = "other"
    elif bad == "boolean_revision":
        request["binding"]["revision"] = True
    elif bad == "approval":
        request["approval"] = pin(Path(proof["path"]).with_name("bad-approval.json"), b'{}')
    elif bad == "missing_artifact":
        request["artifacts"].pop(next(iter(request["artifacts"])))
    elif bad == "artifact_hash":
        request["artifacts"][next(iter(request["artifacts"]))]["sha256"] = "0" * 64
    elif bad == "source_drift":
        (c.workspace / "app.py").write_text("unexpected source change")
    elif bad == "existing":
        c._path().parent.mkdir(exist_ok=True)
        c._path().write_bytes(b"unrelated current state")
    elif bad == "child":
        c.principal = "child"
    elif bad == "protected":
        c.adapter = h.installed_adapter(c.adapter.name, "protected_host")
    elif bad == "symlink":
        link = Path(proof["path"]).with_name("indirect")
        link.symlink_to(request["controller"]["path"])
        request["controller"]["path"] = str(link)
    else:
        db = json.loads(Path(request["controller"]["path"]).read_bytes())
        if bad == "unresolved":
            db.setdefault("admissions", {})["unknown-call"] = {"state": "admitted", "host": c.adapter.name}
        else:
            db["runs"][db["active"]]["workers"] = {"unresolved": {"state": "unknown"}}
        request["controller"] = pin(Path(proof["path"]).with_name("changed-controller.json"), json.dumps(db).encode())
    if bad != "checksum":
        proof.update(pin(Path(proof["path"]), json.dumps(request).encode()))
    before = tree(c.workspace)
    with pytest.raises((w.Refusal, OSError, ValueError)):
        invoke(lost, "apply")
    # The lock is an implementation synchronization file, never workflow state.
    after = {k: v for k, v in tree(c.workspace).items() if not k.endswith(".lock")}
    assert after == {k: v for k, v in before.items() if not k.endswith(".lock")}


def test_interrupted_import_blocks_new_start_and_retries_exact_request(lost, monkeypatch):
    c, _, _, _, _, _ = lost
    original = recovery._publish
    count = 0
    def interrupt(path, raw):
        nonlocal count
        count += 1
        if count == 3:
            raise OSError("fixture interrupted publication")
        original(path, raw)
    monkeypatch.setattr(recovery, "_publish", interrupt)
    with pytest.raises(OSError, match="interrupted"):
        invoke(lost, "apply")
    with pytest.raises(w.Refusal, match="import is incomplete"):
        c.report()
    with pytest.raises(w.Refusal):
        c.start({})
    monkeypatch.setattr(recovery, "_publish", original)
    assert invoke(lost, "apply")["status"] == "imported"


def test_cli_and_host_hook_keep_missing_state_recovery_reachable(lost, capsys, monkeypatch):
    c, _, _, _, _, proof = lost
    monkeypatch.setattr(flow, "observed_parent", lambda *a: None)
    args = ["recover", "--workspace", str(c.workspace), "--recovery-request", proof["path"],
            "--expected-sha256", proof["sha256"], "--recovery-mode", "inspect"]
    cmd = shlex.join([sys.executable, str(Path(flow.__file__).with_name("tp.py")), "flow", *args])
    event = {"hook_event_name": "PreToolUse", "cwd": str(c.workspace), "session_id": c.root,
             "tool_name": "Bash" if c.adapter.name == "claude" else "exec_command",
             "tool_input": {"command" if c.adapter.name == "claude" else "cmd": cmd}}
    local.Harness(c.workspace, c.root).select("taskplane", "fixture/recovery", c.report())
    flow.hook(event, governor=c)
    assert flow.main(args, governor=c) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready_to_import"
    assert not c._path().exists()
    assert flow.main([*args[:-1], "apply"], governor=c) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "imported"


@pytest.mark.parametrize("target", ["original", "receipt"])
def test_tampered_recovery_provenance_blocks_normal_access(lost, target):
    c, _, _, _, _, _ = lost
    result = invoke(lost, "apply")
    path = (Path(result["original_controller"]["path"]) if target == "original"
            else recovery.record_path(c.workspace, c.root))
    path.write_bytes(b'{}')
    before = c._path().read_bytes()
    with pytest.raises(w.Refusal):
        c.report()
    assert c._path().read_bytes() == before


def test_racing_file_is_never_overwritten_or_retried_as_success(lost, monkeypatch):
    c, _, _, _, _, _ = lost
    original = recovery._publish
    changed = None
    def race(path, raw):
        nonlocal changed
        if changed is None:
            changed = path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'concurrent author')
        original(path, raw)
    monkeypatch.setattr(recovery, "_publish", race)
    with pytest.raises(w.Refusal):
        invoke(lost, "apply")
    assert changed.read_bytes() == b'concurrent author'
    monkeypatch.setattr(recovery, "_publish", original)
    with pytest.raises(w.Refusal):
        invoke(lost, "apply")
    assert changed.read_bytes() == b'concurrent author'


def test_executing_runtime_fingerprints_cover_recovery_and_consent():
    from taskplane.host_capabilities import runtime_identity
    identity = runtime_identity()
    for name in ("taskplane/state_import.py", "taskplane/delegated_approval.py"):
        assert identity["member_sha256"][name] == hashlib.sha256((Path(identity["root"]) / name).read_bytes()).hexdigest()
