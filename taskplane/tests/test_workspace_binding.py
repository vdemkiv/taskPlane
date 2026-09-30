"""Topology fixtures prove cooperative checks, not live Cowork or host attestation."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import shlex
import sys

import pytest

from taskplane import flow, primitives, workflow as w, workspace_binding as b
from taskplane.tests.binding_support import HAS_BINDING_RUNTIME, requires_binding_runtime


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith("TASKPLANE_"):
            monkeypatch.delenv(key)


def request(root, *, policy="any", host=None, payload=b"fresh host nonce", reference="host/probe-1"):
    root = root.resolve()
    (root / ".project-probe").write_bytes(payload)
    return {"schema": b.REQUEST_SCHEMA, "surface": "cowork", "host_root": str(host or root),
            "execution_root": str(root), "policy": policy,
            "execution": {"location": "local", "reference": "runtime/execution-1"},
            "worker": {"location": "local", "reference": "runtime/worker-1"},
            "probe": {"path": ".project-probe", "sha256": hashlib.sha256(payload).hexdigest(),
                      "host_reference": reference}}


def local(monkeypatch, *, worker=False):
    monkeypatch.setenv("TASKPLANE_EXECUTION_LOCATION", "local")
    monkeypatch.setenv("TASKPLANE_EXECUTION_REFERENCE", "runtime/current-execution")
    if worker:
        monkeypatch.setenv("TASKPLANE_WORKER_LOCATION", "local")
        monkeypatch.setenv("TASKPLANE_WORKER_REFERENCE", "runtime/current-worker")


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def controller(root, *, active=False, worker=None, handle=None, corrupt=False):
    owner = "original-session"
    key = hashlib.sha256(owner.encode()).hexdigest()[:32]
    scope = {"criteria": ["A"], "paths": {p: [] for p in w.PHASES}, "verification_inputs": []}
    state = w.new_state(str(root), owner, "old-run", scope)
    state.update(profile="native_workflow", source_baseline={}, observed_handles={})
    if not active:
        state["retired"] = {"request_reference": "user/retire-1", "reason": "User retired fixture",
                            "at": "2026-09-25T00:00:00Z", "accepted": False, "assurance": "observed"}
    if worker:
        state["workers"] = {"grant": {"grant_id": "grant", "state": worker, "run": "old-run",
                                     "root": owner, "attempt": 1, "paths": []}}
    if handle:
        state["observed_handles"]["process"] = {"state": handle, "revision": 0, "visit": state["visits"][0]["id"]}
    db = {"schema": "taskplane.control/v1", "profile": "native_workflow", "workspace": str(root),
          "root": owner, "active": "old-run" if active else None, "runs": {"old-run": state}}
    store = root / ".taskplane"
    store.mkdir(exist_ok=True)
    path = store / f"workflow-{key}.json"
    path.write_text("{corrupt" if corrupt else json.dumps(db, indent=3))
    (store / f"workflow-{key}.initialized.json").write_text(json.dumps({
        "schema": "taskplane.local-initialization/v1", "workspace": str(root),
        "root": owner, "profile": "native_workflow"}))
    (store / "historical-evidence.txt").write_bytes(b"unaltered history\n\x00bytes")
    return db, path


def relocation(tmp_path, **state_options):
    original = tmp_path / "original"
    original.mkdir()
    binding = b.bind(original, request(original))
    controller(original, **state_options)
    prior_bytes = tree(original / ".taskplane")
    destination = tmp_path / "remounted"
    original.rename(destination)
    fresh = request(destination, payload=b"new host nonce", reference="host/probe-2")
    fresh.update(expected_project_id=binding["project_id"], request_reference="user/recover-1")
    return destination, binding, fresh, prior_bytes


def test_legacy_read_only_and_explicit_selection_requires_binding(tmp_path, monkeypatch):
    assert b.load(tmp_path) is None
    assert b.ensure(tmp_path) is None
    assert b.describe(tmp_path)["status"] == "unbound"
    assert not (tmp_path / ".taskplane").exists()
    for variable, value in [("TASKPLANE_SURFACE", "cowork"), ("TASKPLANE_WORKSPACE_POLICY", "any"),
                            ("TASKPLANE_WORKSPACE", str(tmp_path))]:
        with monkeypatch.context() as env:
            env.setenv(variable, value)
            with pytest.raises(w.Refusal, match="binding"):
                b.resolve_workspace(None)
            assert not (tmp_path / ".taskplane").exists()


@pytest.mark.parametrize("existing_state", [False, True])
def test_unbound_legacy_reads_do_not_require_descriptor_support(tmp_path, monkeypatch, existing_state):
    if existing_state:
        (tmp_path / ".taskplane").mkdir()
        (tmp_path / ".taskplane" / "history.txt").write_text("existing local history")
    before = tree(tmp_path)
    monkeypatch.setattr(b.os, "supports_dir_fd", set())
    assert b.load(tmp_path) is None
    assert b.ensure(tmp_path) is None
    assert b.resolve_workspace(tmp_path) == tmp_path.resolve()
    assert b.describe(tmp_path)["status"] == "unbound"
    assert b.relative_path(tmp_path, str(tmp_path / "src" / "feature.py")) == "src/feature.py"
    assert b.relative_path(tmp_path, "src/feature.py") == "src/feature.py"
    assert tree(tmp_path) == before


def test_unbound_symlink_state_refuses_without_descriptor_support(tmp_path, monkeypatch):
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / ".taskplane").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(b.os, "supports_dir_fd", set())
    with pytest.raises(w.Refusal, match="symlink"):
        b.load(project)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("operation", [b.bind, b.recover])
def test_binding_administration_requires_descriptor_support(tmp_path, monkeypatch, operation):
    req = request(tmp_path)
    before = tree(tmp_path)
    monkeypatch.setattr(b.os, "supports_dir_fd", set())
    with pytest.raises(w.Refusal, match="no-follow directory operations"):
        operation(tmp_path, req)
    assert tree(tmp_path) == before


@pytest.mark.parametrize("operation", ["bind", "recover", "inspect"])
@pytest.mark.parametrize("capability", [
    pytest.param("native", marks=pytest.mark.skipif(HAS_BINDING_RUNTIME,
                 reason="Native unsupported-runtime check runs where primitives are absent")),
    "dir_fd", "O_DIRECTORY", "O_NOFOLLOW",
])
def test_unsupported_binding_cli_is_structured_and_preserves_files(
        tmp_path, monkeypatch, capsys, operation, capability):
    req = request(tmp_path)
    if operation == "recover":
        req.update(expected_project_id="0" * 32, request_reference="user/recover")
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(req))
    monkeypatch.setenv("TASKPLANE_SURFACE", "cowork")
    if capability == "dir_fd":
        monkeypatch.setattr(b.os, "supports_dir_fd", set())
    elif capability != "native":
        monkeypatch.delattr(b.os, capability, raising=False)
    before = tree(tmp_path)
    args = [operation, "--workspace", str(tmp_path)]
    if operation != "inspect":
        args += ["--request", str(request_path)]
    assert b.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked" and result["reason"] == "workspace_binding"
    assert "no-follow directory operations" in result["detail"]
    assert tree(tmp_path) == before
    assert not (tmp_path / ".taskplane").exists()


@pytest.mark.parametrize("authority", ["binding", "pending", "selected"])
def test_selected_or_existing_authority_cannot_use_legacy_fallback(tmp_path, monkeypatch, authority):
    if authority == "binding":
        # Even an unvalidated persisted binding selects the strict path. Creating
        # it directly keeps this refusal test runnable without binding support.
        (tmp_path / ".taskplane").mkdir()
        (tmp_path / ".taskplane" / b.BINDING_FILE).write_text("{}")
    elif authority == "pending":
        (tmp_path / b.PENDING_FILE).write_text("{}")
    else:
        monkeypatch.setenv("TASKPLANE_SURFACE", "cowork")
    before = tree(tmp_path)
    monkeypatch.setattr(b.os, "supports_dir_fd", set())
    with pytest.raises(w.Refusal, match="no-follow directory operations"):
        b.ensure(tmp_path)
    assert tree(tmp_path) == before


@pytest.mark.parametrize("path, required", [
    ("/home/claude", True), ("/home/claude/project", True), ("/home/claude-other", False),
])
def test_claude_session_path_requires_binding_without_matching_neighbors(path, required):
    assert b._required(Path(path)) is required


@requires_binding_runtime
def test_bind_is_idempotent_and_frozen_contract_is_checked(tmp_path):
    req = request(tmp_path)
    bound = b.bind(tmp_path, req)
    before = tree(tmp_path)
    assert b.bind(tmp_path, req) == bound
    assert b.ensure(tmp_path, expected=bound) == bound
    assert b.ensure(tmp_path, expected=bound["digest"]) == bound
    with pytest.raises(w.Refusal, match="frozen"):
        b.ensure(tmp_path, expected={"project_id": bound["project_id"], "digest": "stale"})
    changed = deepcopy(req)
    changed["policy"] = "local"
    with pytest.raises(w.Refusal):
        b.bind(tmp_path, changed)
    assert tree(tmp_path) == before


@pytest.mark.parametrize("change", [
    lambda r: r.pop("probe"),
    lambda r: r.update(schema="unknown"),
    lambda r: r.update(policy=[]),
    lambda r: r.update(surface={}),
    lambda r: r.update(project_id="invented"),
    lambda r: r["probe"].update(sha256="0" * 64),
    lambda r: r["probe"].update(host_reference=""),
    lambda r: r["probe"].update(host_reference=r["execution"]["reference"]),
    lambda r: r["probe"].update(path="../probe"),
    lambda r: r["execution"].update(reference=""),
    lambda r: r["worker"].update(location=[]),
    lambda r: r.update(execution_root="/different-project"),
])
@requires_binding_runtime
def test_malformed_evidence_refuses_without_state(tmp_path, change):
    req = request(tmp_path)
    change(req)
    before = tree(tmp_path)
    with pytest.raises(w.Refusal):
        b.bind(tmp_path, req)
    assert tree(tmp_path) == before
    assert not (tmp_path / ".taskplane").exists()


@pytest.mark.parametrize("kind", ["symlink", "parent-symlink", "directory", "fifo", "oversize"])
@requires_binding_runtime
def test_probe_reads_are_bounded_ordinary_no_symlink(tmp_path, kind):
    req = request(tmp_path)
    path = tmp_path / ".project-probe"
    path.unlink()
    if kind == "symlink":
        target = tmp_path / "real"
        target.write_bytes(b"fresh host nonce")
        path.symlink_to(target)
    elif kind == "parent-symlink":
        directory = tmp_path / "real"
        directory.mkdir()
        (directory / "probe").write_bytes(b"fresh host nonce")
        (tmp_path / "alias").symlink_to(directory, target_is_directory=True)
        req["probe"]["path"] = "alias/probe"
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(b"x" * (b.REQUEST_BYTES + 1))
    with pytest.raises(w.Refusal):
        b.bind(tmp_path, req)
    assert not (tmp_path / ".taskplane").exists()


@requires_binding_runtime
def test_store_and_workspace_symlinks_are_rejected(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    req = request(project)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".taskplane").symlink_to(outside, target_is_directory=True)
    with pytest.raises(w.Refusal):
        b.bind(project, req)
    assert list(outside.iterdir()) == []
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    with pytest.raises(w.Refusal):
        b.load(alias)


@pytest.mark.parametrize("policy", ["local", "darwin-local"])
@requires_binding_runtime
def test_required_execution_observation_not_inferred_from_binding(tmp_path, monkeypatch, policy):
    req = request(tmp_path, policy=policy)
    monkeypatch.setattr(b.platform, "system", lambda: "Darwin")
    with pytest.raises(w.Refusal, match="current local execution"):
        b.bind(tmp_path, req)
    assert not (tmp_path / ".taskplane").exists()
    local(monkeypatch)
    bound = b.bind(tmp_path, req)
    assert b.ensure(tmp_path) == bound
    monkeypatch.delenv("TASKPLANE_EXECUTION_REFERENCE")
    with pytest.raises(w.Refusal, match="REFERENCE"):
        b.ensure(tmp_path)


@requires_binding_runtime
def test_darwin_local_checks_actual_platform(tmp_path, monkeypatch):
    local(monkeypatch)
    monkeypatch.setattr(b.platform, "system", lambda: "Linux")
    with pytest.raises(w.Refusal, match="executing platform"):
        b.bind(tmp_path, request(tmp_path, policy="darwin-local"))
    assert not (tmp_path / ".taskplane").exists()


@requires_binding_runtime
def test_worker_location_requires_independent_runtime_observation(tmp_path, monkeypatch):
    local(monkeypatch)
    bound = b.bind(tmp_path, request(tmp_path, policy="local"))
    assert b.ensure(tmp_path) == bound
    for location in (None, "unknown", "remote"):
        with monkeypatch.context() as env:
            if location:
                env.setenv("TASKPLANE_WORKER_LOCATION", location)
                env.setenv("TASKPLANE_WORKER_REFERENCE", "worker/new")
            with pytest.raises(w.Refusal):
                b.ensure(tmp_path, worker=True)
    local(monkeypatch, worker=True)
    assert b.ensure(tmp_path, worker=True) == bound


@requires_binding_runtime
def test_any_policy_allows_explicit_remote_but_refuses_conflict(tmp_path, monkeypatch):
    req = request(tmp_path)
    req["execution"]["location"] = "remote"
    monkeypatch.setenv("TASKPLANE_EXECUTION_LOCATION", "remote")
    monkeypatch.setenv("TASKPLANE_EXECUTION_REFERENCE", "runtime/remote")
    b.bind(tmp_path, req)
    assert b.ensure(tmp_path)
    monkeypatch.setenv("TASKPLANE_EXECUTION_LOCATION", "local")
    with pytest.raises(w.Refusal, match="conflicts"):
        b.ensure(tmp_path)


@requires_binding_runtime
def test_policy_environment_cannot_weaken_binding(tmp_path, monkeypatch):
    local(monkeypatch)
    b.bind(tmp_path, request(tmp_path, policy="local"))
    monkeypatch.setenv("TASKPLANE_WORKSPACE_POLICY", "any")
    monkeypatch.delenv("TASKPLANE_EXECUTION_LOCATION")
    with pytest.raises(w.Refusal, match="current local"):
        b.ensure(tmp_path)


@requires_binding_runtime
def test_split_cwd_aliases_one_root_and_no_scratch_store(tmp_path, monkeypatch):
    project, scratch = tmp_path / "project", tmp_path / "session"
    project.mkdir()
    scratch.mkdir()
    host = tmp_path / "native-namespace" / "project"
    b.bind(project, request(project, host=host))
    monkeypatch.setenv("TASKPLANE_WORKSPACE", str(project))
    monkeypatch.chdir(scratch)
    assert b.resolve_workspace(None, {"cwd": str(scratch), "surface": "cowork"}) == project
    assert b.resolve_workspace(host) == project
    assert b.relative_path(project, str(host / "source.py")) == "source.py"
    assert b.relative_path(project, str(project / "source.py")) == "source.py"
    with pytest.raises(w.Refusal, match="conflicts"):
        b.resolve_workspace(scratch)
    assert not (scratch / ".taskplane").exists()


@pytest.fixture
def simulated_windows_paths(monkeypatch):
    """Windows parsing only: root/binding checks and filesystem queries are mocked."""
    class SimulatedWindowsPath(PureWindowsPath):
        def is_symlink(self):
            return False

        def resolve(self):
            return self

    root = SimulatedWindowsPath("C:/project")
    monkeypatch.setattr(b, "Path", SimulatedWindowsPath)
    monkeypatch.setattr(b, "_root", lambda workspace: root)
    monkeypatch.setattr(b, "ensure", lambda workspace: None)
    return root


@pytest.mark.parametrize("target, expected", [
    (r"C:\project\product.json", "product.json"),
    (r"C:\project\src\feature.py", "src/feature.py"),
    ("C:/project/src/feature.py", "src/feature.py"),
    (r"c:\PROJECT\src\feature.py", "src/feature.py"),
    (r"src\feature.py", "src/feature.py"),
    (r".\src/feature.py", "src/feature.py"),
    ("src/feature.py", "src/feature.py"),
])
def test_simulated_windows_legacy_targets_normalize_to_posix(simulated_windows_paths, target, expected):
    assert b.relative_path(simulated_windows_paths, target) == expected


def test_simulated_windows_legacy_unc_workspace_keeps_exact_share(simulated_windows_paths, monkeypatch):
    root = type(simulated_windows_paths)(r"\\server\share\project")
    monkeypatch.setattr(b, "_root", lambda workspace: root)
    assert b.relative_path(root, r"\\server\share\project\src\feature.py") == "src/feature.py"
    assert b.relative_path(root, r"src\feature.py") == "src/feature.py"
    for target in (r"\\server\other\project\src\feature.py",
                   r"\\server\share\project-neighbor\feature.py"):
        with pytest.raises(w.Refusal, match="outside"):
            b.relative_path(root, target)


@pytest.mark.parametrize("target", [
    r"..\outside.py", r"src\..\outside.py", "src/../outside.py",
    r"C:\project\src\..\outside.py", r"C:\project-neighbor\file.py",
    r"D:\project\file.py", r"C:src\file.py", r"D:src\file.py", "C:src/file.py", "C:",
    r"\src\file.py", "/src/file.py", r"\\server\share\file.py",
    r"\\?\C:\project\file.py", "C:/project", ".",
])
def test_simulated_windows_legacy_targets_refuse_ambiguous_or_foreign_paths(simulated_windows_paths, target):
    with pytest.raises(w.Refusal):
        b.relative_path(simulated_windows_paths, target)


@pytest.mark.skipif(os.name == "nt", reason="POSIX backslashes remain literal and refused")
@pytest.mark.parametrize("bound", [False, True])
def test_posix_targets_keep_backslash_refusal(tmp_path, bound):
    if bound:
        b.bind(tmp_path, request(tmp_path))
    with pytest.raises(w.Refusal, match="backslashes"):
        b.relative_path(tmp_path, r"src\feature.py")


@pytest.mark.parametrize("bound", [False, pytest.param(True, marks=requires_binding_runtime)])
@pytest.mark.parametrize("directory", [False, True])
def test_native_targets_refuse_symlink_components(tmp_path, bound, directory):
    project = tmp_path / "project"
    project.mkdir()
    if bound:
        b.bind(project, request(project))
    outside = tmp_path / "outside"
    if directory:
        outside.mkdir()
    else:
        outside.write_text("outside scope")
    link = project / "link"
    link.symlink_to(outside, target_is_directory=directory)
    target = link / "new.py" if directory else link
    with pytest.raises(w.Refusal, match="symlink"):
        b.relative_path(project, str(target))


@pytest.mark.parametrize("target", ["../neighbor.txt", "sub/../neighbor.txt", "a\\b", "/other/project/a", "."])
@requires_binding_runtime
def test_relative_targets_refuse_escape(tmp_path, target):
    b.bind(tmp_path, request(tmp_path))
    with pytest.raises(w.Refusal):
        b.relative_path(tmp_path, target)


@requires_binding_runtime
def test_alias_neighbor_symlink_and_nested_alias_refused(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    host = tmp_path / "host-project"
    b.bind(project, request(project, host=host))
    with pytest.raises(w.Refusal):
        b.relative_path(project, str(tmp_path / "host-project-neighbor" / "a"))
    (project / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(w.Refusal, match="symlink"):
        b.relative_path(project, str(host / "link" / "a"))
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(w.Refusal, match="nested"):
        b.bind(other, request(other, host=other / "nested"))


@requires_binding_runtime
def test_probe_drift_and_binding_tamper_refuse(tmp_path):
    b.bind(tmp_path, request(tmp_path))
    (tmp_path / ".project-probe").write_bytes(b"changed")
    with pytest.raises(w.Refusal, match="digest"):
        b.ensure(tmp_path)
    path = tmp_path / ".taskplane" / b.BINDING_FILE
    value = json.loads(path.read_text())
    value["host_root"] = "/invented"
    path.write_text(json.dumps(value))
    with pytest.raises(w.Refusal, match="digest"):
        b.load(tmp_path)


@requires_binding_runtime
def test_active_legacy_store_cannot_be_bound(tmp_path):
    req = request(tmp_path)
    controller(tmp_path, active=True)
    before = tree(tmp_path)
    with pytest.raises(w.Refusal, match="active"):
        b.bind(tmp_path, req)
    assert tree(tmp_path) == before


@requires_binding_runtime
def test_inactive_relocation_archives_exact_bytes_no_approval_transfer(tmp_path):
    destination, original, fresh, prior_bytes = relocation(tmp_path)
    assert b.describe(destination)["status"] == "blocked"
    assert b.load(destination, allow_relocated=True) == original
    with pytest.raises(w.Refusal, match="relocated"):
        b.ensure(destination)
    result = b.recover(destination, fresh)
    assert result["status"] == "recovered"
    archive = Path(result["recovery"]["archive"])
    assert tree(archive) == prior_bytes
    assert result["recovery"]["files"] == {p: hashlib.sha256(raw).hexdigest() for p, raw in prior_bytes.items()}
    assert result["recovery"]["approvals_transferred"] is False
    assert result["binding"]["project_id"] == original["project_id"]
    assert result["binding"]["digest"] != original["digest"]
    assert set(tree(destination / ".taskplane")) == {b.BINDING_FILE, "workspace-recovery.json"}
    assert b.ensure(destination) == result["binding"]
    with pytest.raises(w.Refusal, match="frozen"):
        b.ensure(destination, expected=original)


def restored_relocation(tmp_path, *, approved=False, archived=False, historical_live=False):
    from taskplane.context import Store
    from taskplane.tests.test_workflow_local import displaced_database
    original = tmp_path / "original"
    original.mkdir()
    bound = b.bind(original, request(original))
    c, state, target, candidate, raw, checksum = displaced_database(original, approved=approved)
    if historical_live:
        db = json.loads(raw)
        historical = db["runs"][state["run"]]
        historical["observed_handles"] = {"process": {"state": "running", "revision": state["revision"],
                                                      "visit": w.current(state)["id"]}}
        historical["workers"] = {"grant": {"grant_id": "grant", "task_id": "historical-task", "state": "unknown",
                                            "run": state["run"], "root": c.root, "attempt": 1, "paths": []}}
        raw = json.dumps(db).encode()
        checksum = hashlib.sha256(raw).hexdigest()
        candidate.write_bytes(raw)
    restored = c.recover_initialization(candidate.name, checksum, state["run"], state["revision"],
                                        "conversation/restore-before-relocation")
    assert restored["status"] == "restored"
    assert target.read_bytes() == candidate.read_bytes() == raw
    if historical_live:
        # Fixture observations have ended before retiring the current controller.
        current = c._read(target)
        current["runs"][state["run"]]["observed_handles"]["process"]["state"] = "completed"
        current["runs"][state["run"]]["workers"]["grant"]["state"] = "failed"
        c._write(target, current)
    c.apply("retire", state["run"], expected_revision=state["revision"],
            native_reference=json.dumps({"request_reference": "conversation/retire-before-relocation",
                                         "reason": "Relocate this retired fixture"}))
    if archived:
        db = json.loads(target.read_text())
        db["archives"] = {state["run"]: Store(original).put("workflow-history", db["runs"].pop(state["run"]))}
        target.write_text(json.dumps(db))
    receipt = target.with_name(target.stem + ".recovery-" + checksum + ".json")
    destination = tmp_path / "relocated"
    original.rename(destination)
    fresh = request(destination, payload=b"relocated nonce", reference="host/relocated-probe")
    fresh.update(expected_project_id=bound["project_id"], request_reference="conversation/relocate")
    return destination, bound, fresh, target.name, candidate.name, receipt.name


@pytest.mark.parametrize("archived, historical_live", [(False, False), (True, False), (False, True)])
@requires_binding_runtime
def test_restored_retired_controller_relocates_exact_history_without_approvals(tmp_path, archived, historical_live):
    destination, bound, fresh, target, candidate, receipt = restored_relocation(
        tmp_path, approved=True, archived=archived, historical_live=historical_live)
    before = tree(destination / ".taskplane")
    historical = json.loads(before[candidate])
    run = historical["active"]
    assert historical["runs"][run]["decisions"]
    assert not historical["runs"][run].get("retired")
    if historical_live:
        assert historical["runs"][run]["observed_handles"]["process"]["state"] == "running"
        assert historical["runs"][run]["workers"]["grant"]["state"] == "unknown"
    assert json.loads(before[target])["active"] is None
    assert json.loads(before[receipt])["status"] == "restored"
    result = b.recover(destination, fresh)
    assert tree(Path(result["recovery"]["archive"])) == before
    assert result["recovery"]["files"] == {p: hashlib.sha256(raw).hexdigest() for p, raw in before.items()}
    assert result["recovery"]["approvals_transferred"] is False
    assert result["binding"]["project_id"] == bound["project_id"]
    assert result["binding"]["digest"] != bound["digest"]
    assert set(tree(destination / ".taskplane")) == {b.BINDING_FILE, "workspace-recovery.json"}
    assert b.ensure(destination) == result["binding"]
    with pytest.raises(w.Refusal, match="frozen"):
        b.ensure(destination, expected=bound)


@pytest.mark.parametrize("bad", [
    "missing-copy", "missing-receipt", "missing-controller", "missing-marker", "foreign-filenames",
    "copy-symlink", "receipt-symlink", "extra-copy", "unknown-name", "zero-number", "leading-zero",
    "copy-checksum", "copy-json", "copy-list", "copy-schema", "copy-workspace", "copy-root",
    "copy-active", "copy-run", "copy-revision", "copy-future", "copy-contract", "copy-worker",
    "copy-handle", "copy-context", "copy-baseline", "copy-archives", "copy-decision",
    "receipt-json", "receipt-list", "receipt-schema", "receipt-workspace", "receipt-root",
    "receipt-source", "receipt-traversal", "receipt-run", "receipt-revision", "receipt-boolean",
    "receipt-sha256", "receipt-reference", "receipt-prepared", "receipt-preserved", "receipt-approvals",
    "receipt-extra", "receipt-missing", "missing-current-run", "current-active",
])
@requires_binding_runtime
def test_invalid_restoration_history_refuses_without_mutation(tmp_path, bad):
    destination, _, fresh, target_name, copy_name, receipt_name = restored_relocation(tmp_path)
    store = destination / ".taskplane"
    target, candidate, receipt = (store / name for name in (target_name, copy_name, receipt_name))
    audit = json.loads(receipt.read_text())
    if bad.startswith("missing-") and bad != "missing-current-run":
        {"missing-copy": candidate, "missing-receipt": receipt, "missing-controller": target,
         "missing-marker": target.with_name(target.stem + ".initialized.json")}[bad].unlink()
    elif bad == "foreign-filenames":
        for path in (candidate, receipt):
            path.rename(store / path.name.replace(target.stem, "workflow-" + "0" * 32))
    elif bad in {"copy-symlink", "receipt-symlink"}:
        path = candidate if bad == "copy-symlink" else receipt
        other = destination / (bad + ".json")
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
    elif bad in {"extra-copy", "unknown-name", "zero-number", "leading-zero"}:
        suffix = {"extra-copy": " 3", "unknown-name": ".unrecognized", "zero-number": " 0",
                  "leading-zero": " 02"}[bad]
        candidate.with_name(target.stem + suffix + ".json").write_bytes(candidate.read_bytes())
    elif bad == "copy-checksum":
        candidate.write_bytes(candidate.read_bytes() + b" ")
    elif bad.startswith("copy-"):
        db = json.loads(candidate.read_text())
        state = db["runs"][db["active"]]
        if bad in {"copy-schema", "copy-workspace", "copy-root"}:
            db[bad.removeprefix("copy-")] = "foreign"
        elif bad == "copy-active": db["active"] = None
        elif bad == "copy-run": state["run"] = "foreign"
        elif bad == "copy-revision": state["revision"] = True
        elif bad == "copy-future":
            state["revision"] += 10
            audit["revision"] = state["revision"]
        elif bad == "copy-contract": state["workspace_contract"] = {"digest": "foreign"}
        elif bad == "copy-worker": state["workers"] = {"invalid": {}}
        elif bad == "copy-handle": state["observed_handles"] = {"invalid": {}}
        elif bad == "copy-context": state["context_contract"] = "unknown"
        elif bad == "copy-baseline": state["source_baseline"] = []
        elif bad == "copy-archives": db["archives"] = {"other": {}}
        elif bad == "copy-decision": state["decisions"] = {"invalid": None}
        raw = b"{" if bad == "copy-json" else b"[]" if bad == "copy-list" else json.dumps(db).encode()
        candidate.write_bytes(raw)
        # Keep the receipt/checksum aligned so malformed database validation is exercised.
        audit["sha256"] = hashlib.sha256(raw).hexdigest()
        receipt.unlink()
        receipt = store / (target.stem + ".recovery-" + audit["sha256"] + ".json")
        receipt.write_text(json.dumps(audit))
    elif bad.startswith("receipt-"):
        changes = {"receipt-schema": ("schema", "unknown"), "receipt-workspace": ("workspace", "foreign"),
            "receipt-root": ("root", "foreign"), "receipt-source": ("source", target.name),
            "receipt-traversal": ("source", "../" + candidate.name), "receipt-run": ("run", "foreign"),
            "receipt-revision": ("revision", audit["revision"] + 1), "receipt-boolean": ("revision", True),
            "receipt-sha256": ("sha256", "0" * 64), "receipt-reference": ("request_reference", " "),
            "receipt-prepared": ("status", "prepared"), "receipt-preserved": ("state_bytes_preserved", False),
            "receipt-approvals": ("approvals_changed", True), "receipt-extra": ("extra", True)}
        if bad in changes:
            key, value = changes[bad]
            audit[key] = value
        elif bad == "receipt-missing": audit.pop("source")
        receipt.write_text("{" if bad == "receipt-json" else "[]" if bad == "receipt-list" else json.dumps(audit))
    else:
        db = json.loads(target.read_text())
        if bad == "missing-current-run": db["runs"] = {}
        else: db["active"] = audit["run"]
        target.write_text(json.dumps(db))
    before = tree(destination)
    with pytest.raises(w.Refusal):
        b.recover(destination, fresh)
    assert tree(destination) == before
    assert not list(destination.glob(".taskplane-recovery-*"))
    assert not (destination / b.PENDING_FILE).exists()


@pytest.mark.parametrize("options", [{"active": True}, {"worker": "running"}, {"worker": "unknown"},
                                     {"handle": "running"}, {"corrupt": True}])
@requires_binding_runtime
def test_active_or_corrupt_recovery_is_read_only(tmp_path, options):
    destination, _, fresh, prior = relocation(tmp_path, **options)
    before = tree(destination)
    with pytest.raises(w.Refusal):
        b.recover(destination, fresh)
    assert tree(destination) == before
    assert tree(destination / ".taskplane") == prior
    assert not list(destination.glob(".taskplane-recovery-*"))
    assert not (destination / b.PENDING_FILE).exists()


@pytest.mark.parametrize("mutation", ["project", "stale-bytes", "stale-reference", "missing-provenance"])
@requires_binding_runtime
def test_recovery_requires_correct_identity_fresh_probe_and_provenance(tmp_path, mutation):
    destination, original, fresh, _ = relocation(tmp_path)
    if mutation == "project":
        fresh["expected_project_id"] = "0" * 32
    elif mutation == "stale-bytes":
        (destination / ".project-probe").write_bytes(b"fresh host nonce")
        fresh["probe"]["sha256"] = original["probe"]["sha256"]
    elif mutation == "stale-reference":
        fresh["probe"]["host_reference"] = original["probe"]["host_reference"]
    else:
        fresh["request_reference"] = ""
    before = tree(destination)
    with pytest.raises(w.Refusal):
        b.recover(destination, fresh)
    assert tree(destination) == before


@requires_binding_runtime
def test_copied_identity_refuses_even_with_fresh_claim(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    bound = b.bind(original, request(original))
    copied = tmp_path / "copy"
    shutil.copytree(original, copied)
    fresh = request(copied, payload=b"fresh copy proof", reference="host/copy")
    fresh.update(expected_project_id=bound["project_id"], request_reference="user/copy")
    before = tree(copied)
    with pytest.raises(w.Refusal, match="copied"):
        b.recover(copied, fresh)
    assert tree(copied) == before


@requires_binding_runtime
def test_same_path_copy_detected_by_directory_identity(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    b.bind(root, request(root))
    original = tmp_path / "saved"
    root.rename(original)
    shutil.copytree(original, root)
    with pytest.raises(w.Refusal, match="copied"):
        b.ensure(root)


@requires_binding_runtime
def test_recovery_rejects_special_history_and_incomplete_marker(tmp_path):
    destination, _, fresh, _ = relocation(tmp_path)
    (destination / ".taskplane" / "untrusted-link").symlink_to(destination / ".project-probe")
    with pytest.raises(w.Refusal, match="symlink"):
        b.recover(destination, fresh)
    assert not (destination / b.PENDING_FILE).exists()
    (destination / ".taskplane" / "untrusted-link").unlink()
    next((destination / ".taskplane").glob("*.initialized.json")).unlink()
    with pytest.raises(w.Refusal, match="incomplete"):
        b.recover(destination, fresh)


@requires_binding_runtime
def test_interrupted_recovery_remains_fail_closed_and_original_bytes_survive(tmp_path, monkeypatch):
    destination, _, fresh, prior_bytes = relocation(tmp_path)
    rename = b.os.rename
    def fail_second(source, target, **kwargs):
        if str(source).startswith(".taskplane-rebind-"):
            raise OSError("simulated interrupted second rename")
        return rename(source, target, **kwargs)
    monkeypatch.setattr(b.os, "rename", fail_second)
    with pytest.raises(w.Refusal, match="interrupted"):
        b.recover(destination, fresh)
    archive = next(destination.glob(".taskplane-recovery-*"))
    assert tree(archive) == prior_bytes
    assert (destination / b.PENDING_FILE).exists()
    with pytest.raises(w.Refusal, match="interrupted"):
        b.ensure(destination)


@requires_binding_runtime
def test_cli_request_and_readonly_inspection(tmp_path, capsys):
    req = request(tmp_path)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(req))
    assert b.main(["bind", "--workspace", str(tmp_path), "--request", str(path)]) == 0
    bound = json.loads(capsys.readouterr().out)
    assert bound["schema"] == b.BINDING_SCHEMA
    before = tree(tmp_path)
    assert b.main(["inspect", "--workspace", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "bound"
    assert result["host_attestation"] is False
    assert result["live_cowork_verified"] is False
    assert tree(tmp_path) == before


@requires_binding_runtime
def test_digest_matches_canonical_record(tmp_path):
    bound = b.bind(tmp_path, request(tmp_path))
    assert bound["digest"] == primitives.content_fingerprint({k: v for k, v in bound.items() if k != "digest"})


@requires_binding_runtime
def test_recovery_preserves_and_validates_archived_context_history(tmp_path):
    from taskplane.context import Store
    original = tmp_path / "original"
    original.mkdir()
    bound = b.bind(original, request(original))
    db, path = controller(original)
    db["archives"] = {"old-run": Store(original).put("workflow-history", db["runs"].pop("old-run"))}
    path.write_text(json.dumps(db))
    before = tree(original / ".taskplane")
    destination = tmp_path / "remounted"
    original.rename(destination)
    fresh = request(destination, payload=b"new host nonce", reference="host/new")
    fresh.update(expected_project_id=bound["project_id"], request_reference="user/remount")
    result = b.recover(destination, fresh)
    assert tree(Path(result["recovery"]["archive"])) == before


@pytest.mark.parametrize("field,value", [("retired", True), ("superseded_by", True),
                                         ("source_baseline", []), ("workers", [])])
@requires_binding_runtime
def test_corrupt_terminal_fields_do_not_authorize_recovery(tmp_path, field, value):
    destination, _, fresh, _ = relocation(tmp_path)
    path = next(p for p in (destination / ".taskplane").glob("workflow-*.json") if "initialized" not in p.name)
    db = json.loads(path.read_text())
    db["runs"]["old-run"][field] = value
    path.write_text(json.dumps(db))
    before = tree(destination)
    with pytest.raises(w.Refusal):
        b.recover(destination, fresh)
    assert tree(destination) == before


@requires_binding_runtime
def test_recovery_inventory_has_hard_bounds(tmp_path, monkeypatch):
    destination, _, fresh, _ = relocation(tmp_path)
    monkeypatch.setattr(b, "MAX_FILES", 2)
    before = tree(destination)
    with pytest.raises(w.Refusal, match="bound"):
        b.recover(destination, fresh)
    assert tree(destination) == before


# Claude-shaped onboarding journeys; these do not certify live Cowork.
ROOT = Path(__file__).resolve().parents[2]
PROMPT = '/taskplane run full engineering review for farm viewer repo with top 8 best matching lenses.'


@pytest.fixture
def onboarding_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith(('TASKPLANE_', 'CODEX_', 'CLAUDE_')):
            monkeypatch.delenv(key)


def invoke(tmp_path, monkeypatch, capsys, name, **extra):
    event = {'hook_event_name': name, 'session_id': 'cowork-root', 'cwd': str(tmp_path), **extra}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(event)))
    code = flow.run_hook()
    return code, json.loads(capsys.readouterr().out)


@requires_binding_runtime
@pytest.mark.parametrize('selected', [False, True])
@pytest.mark.usefixtures("onboarding_environment")
def test_prompt_reaches_model_with_setup_guidance(tmp_path, monkeypatch, capsys, selected):
    monkeypatch.setenv('TASKPLANE_SURFACE', 'cowork')
    if selected:
        monkeypatch.setenv('TASKPLANE_WORKSPACE', str(tmp_path))
    before = tree(tmp_path)
    code, result = invoke(tmp_path, monkeypatch, capsys, 'UserPromptSubmit', prompt=PROMPT)
    assert code == 0
    assert 'decision' not in result
    context = result['hookSpecificOutput']['additionalContext']
    assert 'original request' in context and 'lens count' in context
    assert 'selected' in context and 'binding' in context
    assert tree(tmp_path) == before


@requires_binding_runtime
@pytest.mark.usefixtures("onboarding_environment")
def test_report_missing_binding_is_actionable_and_readonly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('TASKPLANE_SURFACE', 'cowork')
    assert flow.main(['report', '--workspace', str(tmp_path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['onboarding']['state'] == 'binding_required'
    assert result['onboarding']['history'] == 'unknown'
    assert 'original request' in result['next_action']
    assert not (tmp_path / '.taskplane').exists()


@requires_binding_runtime
@pytest.mark.parametrize('event_name', ['PreToolUse', 'PostToolUse'])
@pytest.mark.parametrize('tool,args', [
    ('Skill', {'skill': 'taskplane'}),
    ('Skill', {'skill': 'taskplane:taskplane'}),
    ('Skill', {'skill': 'taskplane:tp-engineering'}),
    ('Skill', {'skill': 'taskplane:tp-status'}),
    ('Read', {'file_path': 'README.md'}),
    ('AskUserQuestion', {'question': 'Select the project folder'}),
    ('Bash', {'command': 'pwd'}),
    ('Bash', {'command': 'rg --files'}),
    ('Bash', {'command': 'shasum -a 256 -- /selected/README.md'}),
    ('Bash', {'command': 'sha256sum -- /selected/README.md'}),
    ('Bash', {'command': shlex.join([sys.executable, str(ROOT / 'taskplane/tp.py'), 'flow', 'report'])}),
])
@pytest.mark.usefixtures("onboarding_environment")
def test_selected_unbound_setup_reads_reachable(tmp_path, monkeypatch, capsys, tool, args, event_name):
    monkeypatch.setenv('TASKPLANE_WORKSPACE', str(tmp_path))
    before = tree(tmp_path)
    code, result = invoke(tmp_path, monkeypatch, capsys, event_name, tool_name=tool, tool_input=args)
    assert code == 0
    assert result['hookSpecificOutput'].get('permissionDecision') is None
    assert 'setup is pending' in result['hookSpecificOutput']['additionalContext']
    assert tree(tmp_path) == before


@requires_binding_runtime
@pytest.mark.parametrize('tool,args,child', [
    ('Write', {'file_path': 'app.py', 'content': 'change'}, {}),
    ('Bash', {'command': 'touch app.py'}, {}),
    ('Bash', {'command': 'pwd; touch app.py'}, {}),
    ('Bash', {'command': 'shasum -a 256 -- /selected/README.md; touch app.py'}, {}),
    ('Bash', {'command': 'sha256sum --check /selected/checks'}, {}),
    ('Bash', {'command': 'python3 /tmp/forged/tp.py flow report'}, {}),
    ('Read', {'file_path': 'README.md'}, {'parent_session_id': 'parent'}),
    ('Skill', {'skill': 'taskplane:tp-engineering'}, {'subagent_id': 'child'}),
])
@pytest.mark.usefixtures("onboarding_environment")
def test_setup_does_not_admit_writes_or_children(tmp_path, monkeypatch, capsys, tool, args, child):
    monkeypatch.setenv('TASKPLANE_WORKSPACE', str(tmp_path))
    code, result = invoke(tmp_path, monkeypatch, capsys, 'PreToolUse', tool_name=tool, tool_input=args, **child)
    assert code == 0 and result['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert not (tmp_path / '.taskplane').exists()


@requires_binding_runtime
@pytest.mark.usefixtures("onboarding_environment")
def test_invalid_binding_is_not_first_run_onboarding(tmp_path, monkeypatch, capsys):
    b.bind(tmp_path, request(tmp_path))
    target = tmp_path / '.taskplane' / b.BINDING_FILE
    target.write_text('{corrupt')
    monkeypatch.setenv('TASKPLANE_WORKSPACE', str(tmp_path))
    before = tree(tmp_path)
    code, result = invoke(tmp_path, monkeypatch, capsys, 'UserPromptSubmit', prompt=PROMPT)
    assert code == 2 and result['decision'] == 'block'
    assert tree(tmp_path) == before


@requires_binding_runtime
@pytest.mark.usefixtures("onboarding_environment")
def test_frozen_missing_binding_is_not_onboarding(tmp_path):
    with pytest.raises(w.Refusal) as error:
        b.ensure(tmp_path, expected={'project_id': 'lost', 'digest': 'lost'})
    assert not isinstance(error.value, b.MissingBinding)


@requires_binding_runtime
@pytest.mark.parametrize('payload', ['{', '[]', '"value"', ' ' * (b.REQUEST_BYTES + 1)],
                         ids=['malformed', 'list', 'scalar', 'oversize'])
@pytest.mark.usefixtures("onboarding_environment")
def test_inline_request_rejects_invalid_data_without_state(tmp_path, capsys, payload):
    assert b.main(['bind', '--workspace', str(tmp_path), '--request-json', payload]) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'workspace_binding'
    assert not (tmp_path / '.taskplane').exists()


@requires_binding_runtime
@pytest.mark.usefixtures("onboarding_environment")
def test_selected_folder_inline_bind_then_standalone_engineering(tmp_path, monkeypatch, capsys):
    from taskplane.tests.test_native_workflow_cli import cli
    monkeypatch.setenv('TASKPLANE_WORKSPACE', str(tmp_path))
    req = request(tmp_path)
    argv = [sys.executable, str(ROOT / 'taskplane/tp.py'), 'workspace', 'bind',
            '--workspace', str(tmp_path), '--request-json', json.dumps(req)]
    command = shlex.join(argv)
    for child in ({'parent_session_id': 'parent'}, {'agent_id': 'child'}, {'subagent_id': 'child'}):
        assert not flow._workspace_admin({'tool_name': 'Bash', 'tool_input': {'command': command}, **child})
    code, result = invoke(tmp_path, monkeypatch, capsys, 'PreToolUse', tool_name='Bash',
                          tool_input={'command': command})
    assert code == 0 and result.get('hookSpecificOutput', {}).get('permissionDecision') != 'deny'
    assert b.main(argv[3:]) == 0
    assert json.loads(capsys.readouterr().out)['schema'] == b.BINDING_SCHEMA
    env = {'TASKPLANE_WORKSPACE': str(tmp_path), 'TASKPLANE_SURFACE': 'cowork'}
    cli(tmp_path, 'claude', 'activate', '--phase', 'tp-engineering', '--request-reference', PROMPT, environment=env)
    bootstrap = tmp_path / '.taskplane/bootstrap'
    bootstrap.mkdir()
    scope = {'criteria': ['eight-lens-review'], 'paths': {p: [] for p in w.PHASES}, 'verification_inputs': []}
    (bootstrap / 'scope.json').write_text(json.dumps(scope))
    state = cli(tmp_path, 'claude', 'start', '--standalone', '--phase', 'engineering',
                '--scope', '.taskplane/bootstrap/scope.json', '--request-reference', PROMPT, environment=env)['workflow']
    assert [v['phase'] for v in state['visits']] == ['engineering']
    assert state['request_provenance']['reference'] == PROMPT
    assert state['scope']['criteria'] == ['eight-lens-review']
    assert state['workspace_contract']['project_id'] == b.load(tmp_path)['project_id']


@requires_binding_runtime
@pytest.mark.usefixtures("onboarding_environment")
def test_unrelated_cowork_prompt_does_not_select_taskplane(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('TASKPLANE_SURFACE', 'cowork')
    code, result = invoke(tmp_path, monkeypatch, capsys, 'UserPromptSubmit', prompt='Write a short poem')
    assert code == 0 and result == {}
    assert not (tmp_path / '.taskplane').exists()


@pytest.mark.usefixtures("onboarding_environment")
def test_cowork_router_skill_selects_initialization():
    from taskplane.workflow_local import execution_entry
    assert execution_entry({'tool_name': 'Skill', 'tool_input': {'skill': 'taskplane'}}) == 'taskplane'


@requires_binding_runtime
@pytest.mark.parametrize('tool', ['Read', 'Skill', 'Bash'])
@pytest.mark.parametrize('event_name', ['PreToolUse', 'PostToolUse'])
@pytest.mark.usefixtures("onboarding_environment")
def test_discovered_child_cannot_use_onboarding_exemptions(tmp_path, monkeypatch, capsys, tool, event_name):
    from taskplane.tests.test_native_session_meter import _write_segment
    home = tmp_path / 'native-home'
    logs = home / 'sessions'
    logs.mkdir(parents=True)
    _write_segment(logs / 'child-session.jsonl', session_id='child-session', total=None, parent='parent-session')
    project = tmp_path / 'project'
    project.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setenv('CODEX_THREAD_ID', 'child-session')
    monkeypatch.setenv('TASKPLANE_WORKSPACE', str(project))
    args = {'Read': {'file_path': 'README.md'},
            'Skill': {'skill': 'taskplane:tp-engineering'},
            'Bash': {'command': shlex.join([sys.executable, str(ROOT / 'taskplane/tp.py'),
                                           'workspace', 'inspect', '--workspace', str(project)])}}[tool]
    before = tree(tmp_path)
    code, result = invoke(project, monkeypatch, capsys, event_name, thread_id='child-session',
                          tool_name=tool, tool_input=args)
    if event_name == 'PreToolUse':
        assert code == 0 and result['hookSpecificOutput']['permissionDecision'] == 'deny'
    else:
        assert code == 2 and result['decision'] == 'block'
    assert tree(tmp_path) == before


@requires_binding_runtime
@pytest.mark.parametrize('history', ['active', 'inactive', 'corrupt', 'marker-only', 'archived'])
@pytest.mark.parametrize('selection', ['workspace', 'surface', 'event'])
@pytest.mark.usefixtures("onboarding_environment")
def test_lost_binding_history_is_not_first_run_through_entry_points(tmp_path, monkeypatch, capsys, history, selection):
    bound = b.bind(tmp_path, request(tmp_path))
    db, path = controller(tmp_path, active=history == 'active')
    db['runs']['old-run']['workspace_contract'] = {k: bound[k] for k in ('project_id', 'digest')}
    if history == 'archived':
        from taskplane.context import Store
        db['archives'] = {'old-run': Store(tmp_path).put('workflow-history', db['runs'].pop('old-run'))}
    path.write_text('{corrupt' if history == 'corrupt' else json.dumps(db))
    if history == 'marker-only':
        path.unlink()
    (tmp_path / '.taskplane' / b.BINDING_FILE).unlink()
    extra = {}
    if selection == 'workspace':
        monkeypatch.setenv('TASKPLANE_WORKSPACE', str(tmp_path))
    elif selection == 'surface':
        monkeypatch.setenv('TASKPLANE_SURFACE', 'cowork')
    else:
        extra['surface'] = 'cowork'
    before = tree(tmp_path)
    code, result = invoke(tmp_path, monkeypatch, capsys, 'UserPromptSubmit', prompt=PROMPT, **extra)
    assert code == 2 and result['decision'] == 'block'
    assert 'preserve existing state' in result['reason']
    assert 'setup is pending' not in result['reason']
    assert tree(tmp_path) == before
    # CLI selection has no native event, so supply its corresponding surface signal.
    if selection == 'event':
        monkeypatch.setenv('TASKPLANE_SURFACE', 'cowork')
    assert flow.main(['report', '--workspace', str(tmp_path)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report['reason'] == 'workspace_binding'
    assert 'onboarding' not in report
    assert 'restore the original binding' in report['detail']
    assert tree(tmp_path) == before


@requires_binding_runtime
@pytest.mark.usefixtures("onboarding_environment")
def test_missing_binding_discovery_is_bounded_and_readonly(tmp_path, monkeypatch, capsys):
    store = tmp_path / '.taskplane'
    store.mkdir()
    (store / 'unrelated-a').write_text('a')
    (store / 'unrelated-b').write_text('b')
    monkeypatch.setattr(b, 'MAX_FILES', 1)
    monkeypatch.setenv('TASKPLANE_SURFACE', 'cowork')
    before = tree(tmp_path)
    assert flow.main(['report', '--workspace', str(tmp_path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert 'entry bound' in result['detail'] and 'onboarding' not in result
    assert tree(tmp_path) == before


@requires_binding_runtime
@pytest.mark.usefixtures("onboarding_environment")
def test_existing_unbound_local_history_retains_legacy_resolution(tmp_path):
    controller(tmp_path, active=True)
    before = tree(tmp_path)
    assert b.resolve_workspace(tmp_path) == tmp_path.resolve()
    assert tree(tmp_path) == before
