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
from taskplane.tests.test_worker_runtime import parent_pair
from taskplane.tests.test_ordinary_checkpoint_reply import ordinary
from taskplane.tests.binding_support import HAS_BINDING_RUNTIME


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


@pytest.mark.parametrize("relocate", [False, True])
def test_authentic_import_can_retire_bind_and_relocate_without_fabricated_marker(lost, relocate):
    from taskplane import workspace_binding
    from taskplane.tests.test_workspace_binding import request
    c, state, original, _, _, _ = lost
    imported = invoke(lost, "apply")
    retired = c.apply("retire", state["run"], expected_revision=state["revision"],
        native_reference=json.dumps({"request_reference": "user/retire-import", "reason": "Retire imported history"}))
    assert retired["retired"] and not retired["decisions"]
    marker = c._path().with_name(c.adapter.markername)
    assert not marker.exists()
    assert Path(imported["original_controller"]["path"]).read_bytes() == original
    if not HAS_BINDING_RUNTIME:
        with pytest.raises(w.Refusal, match="lacks no-follow directory operations"):
            workspace_binding.bind(c.workspace, request(c.workspace))
        assert not marker.exists()
        return
    bound = workspace_binding.bind(c.workspace, request(c.workspace))
    assert recovery.initialization(c.workspace, c.root)["status"] == "committed"
    if relocate:
        destination = c.workspace.with_name("relocated")
        c.workspace.rename(destination)
        fresh = request(destination, payload=b"relocated import probe", reference="host/relocated-import")
        fresh.update(expected_project_id=bound["project_id"], request_reference="user/relocate-import")
        result = workspace_binding.recover(destination, fresh)
        assert result["recovery"]["approvals_transferred"] is False
        archive = Path(result["recovery"]["archive"])
        retained = archive / "recovery-v1" / imported["request_sha256"] / "original-controller.json"
        assert retained.read_bytes() == original
        assert not (archive / marker.name).exists()


@pytest.mark.parametrize("damage", ["active", "ambiguous"])
def test_import_binding_refuses_active_or_ambiguous_initialization(lost, damage):
    from taskplane import workspace_binding
    from taskplane.tests.test_workspace_binding import request
    c, state, _, _, _, _ = lost
    invoke(lost, "apply")
    if damage == "ambiguous":
        c.apply("retire", state["run"], expected_revision=state["revision"],
            native_reference=json.dumps({"request_reference": "user/retire-import", "reason": "Retire imported history"}))
        c._path().with_name(c.adapter.markername).write_text(json.dumps({
            "schema": "taskplane.local-initialization/v1", "workspace": str(c.workspace),
            "root": c.root, "profile": "native_workflow"}))
    reason = "active|ambiguous" if HAS_BINDING_RUNTIME else "lacks no-follow directory operations"
    with pytest.raises(w.Refusal, match=reason):
        workspace_binding.bind(c.workspace, request(c.workspace))


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
    assert observed["state_import"]
    assert invoke(lost, "apply")["status"] == "already_imported"
    with pytest.raises(w.Refusal, match="Initialization"):
        decide(c, observed, approval)
    parent_pair(c)
    accepted = decide(c, observed, approval)
    assert accepted["decisions"][approval["event_id"]]["provenance"]["relay"] == approval["relay"]
    advanced = c.apply("advance", state["run"], expected_revision=accepted["revision"], phase="design")
    assert w.current(advanced)["phase"] == "design" and w.current(advanced)["decision"] == "not_requested"
    assert len(advanced["decisions"]) == 1
    from taskplane.context_handoff import Brief
    brief = Brief(c.workspace, advanced).descriptor()
    assert brief["schema"] == "taskplane.brief/v1"
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
    identity = runtime_identity(full=True)
    for name in ("taskplane/state_import.py", "taskplane/delegated_approval.py"):
        assert identity["member_sha256"][name] == hashlib.sha256((Path(identity["root"]) / name).read_bytes()).hexdigest()


@pytest.mark.parametrize("kind", ["dangling", "different", "parent"])
def test_invalid_import_destination_refuses_before_prepared_receipt(lost, kind):
    c, _, _, _, _, proof = lost
    store = c.workspace / ".taskplane"
    store.mkdir()
    recovery_root = store / "recovery-v1"
    if kind == "parent":
        recovery_root.symlink_to("missing-directory", target_is_directory=True)
    else:
        destination = recovery_root / proof["sha256"] / "original-controller.json"
        destination.parent.mkdir(parents=True)
        if kind == "dangling":
            destination.symlink_to("missing-source")
        else:
            destination.write_bytes(b"unrelated retained data")
    before = tree(c.workspace)
    links = {str(p.relative_to(c.workspace)): str(p.readlink()) for p in c.workspace.rglob("*") if p.is_symlink()}
    with pytest.raises(w.Refusal):
        invoke(lost, "apply")
    assert not recovery.record_path(c.workspace, c.root).exists()
    assert {k: v for k, v in tree(c.workspace).items() if not k.endswith(".lock")} == before
    assert {str(p.relative_to(c.workspace)): str(p.readlink()) for p in c.workspace.rglob("*") if p.is_symlink()} == links
