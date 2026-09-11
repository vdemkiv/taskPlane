"""Project execution storage preserves binding authority and prior runs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import preflight
import run_store
import storage
import taskplane_lite


def _checkout(tmp_path: Path, name: str = "project") -> Path:
    root = tmp_path / name
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def _bind(root: Path, home: Path, run_id: str = "preflight-one"):
    identity = storage.resolve_repository_identity(str(root))
    store = run_store.RunStore(home=str(home))
    store.create(identity, run_id=run_id, checkout=str(root), host={}, target={})
    layout = storage.resolve_layout(identity, home=str(home), run_id=run_id)
    storage.write_workspace_locator(str(root), identity=identity,
                                    layout=layout, run_id=run_id)
    return store, layout


def _files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_default_layout_knowledge_and_run_store_use_requested_project(
        tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv("TASKPLANE_HOME")
    identity = storage.resolve_repository_identity(str(root))
    home = str(root / ".taskplane")
    layout = storage.resolve_layout(identity, run_id="run-local")
    assert layout.home == home
    assert storage.taskplane_home(workspace=str(root)) == home
    assert storage.store_home(str(root)) == home
    assert storage.external_store_root(str(root)) == str(
        root / ".taskplane" / "projects" / identity.key)
    store = run_store.RunStore(workspace=str(root))
    assert store.home == home
    store.create(identity, run_id="run-local", checkout=str(root), host={}, target={})
    assert (root / ".taskplane" / "runs" / "run-local" / "manifest.json").is_file()
    assert not (elsewhere / ".taskplane").exists()


def test_existing_external_binding_is_kept_without_migration(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    home = tmp_path / "explicit-old-home"
    _bind(root, home)
    monkeypatch.delenv("TASKPLANE_HOME")
    before = _files(home)
    assert storage.taskplane_home(workspace=str(root)) == str(home)
    assert storage.store_home(str(root)) == str(home)
    environment = {}
    assert storage.bind_workspace_taskplane_home(str(root), environment) == str(home)
    assert environment == {"TASKPLANE_HOME": str(home)}
    assert _files(home) == before
    assert not (root / ".taskplane").exists()


def test_explicit_custom_homes_remain_available(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    custom = str(tmp_path / "custom")
    monkeypatch.setenv("TASKPLANE_HOME", custom)
    assert storage.taskplane_home(workspace=str(root)) == custom
    assert storage.store_home(str(root)) == custom
    assert storage.taskplane_home(str(tmp_path / "argument"), workspace=str(root)) == str(
        tmp_path / "argument")


def test_project_home_cannot_be_redirected_by_symlink(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".taskplane").symlink_to(outside, target_is_directory=True)
    monkeypatch.delenv("TASKPLANE_HOME")
    with pytest.raises(storage.StorageIdentityError, match="without symlinks"):
        storage.taskplane_home(workspace=str(root))
    with pytest.raises(storage.StorageIdentityError, match="without symlinks"):
        storage.select_project_execution_storage(str(root))
    assert not list(outside.iterdir())


def test_explicit_selection_archives_unused_binding_then_new_run_is_local(
        tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    old_home = tmp_path / "old-home"
    old_store, _ = _bind(root, old_home)
    monkeypatch.delenv("TASKPLANE_HOME")
    old_files = _files(old_home)
    locator_path = Path(storage._locator_path(str(root)))
    old_locator = locator_path.read_bytes()
    environment = {"TASKPLANE_HOME": str(old_home), "UNRELATED": "keep"}
    result = storage.select_project_execution_storage(str(root), environment=environment)
    assert result["status"] == "selected"
    assert result["home"] == str(root / ".taskplane")
    assert environment == {"TASKPLANE_HOME": result["home"], "UNRELATED": "keep"}
    assert storage.load_workspace_locator(str(root)) is None
    assert storage.taskplane_home(workspace=str(root)) == result["home"]
    assert preflight.workspace_readiness(str(root))["status"] == "unbound"
    assert not (root / ".taskplane" / "runs").exists()
    assert locator_path.read_bytes() == old_locator
    assert _files(old_home) == old_files
    archive = Path(result["previous_binding"]["archive_path"])
    preserved = json.loads(archive.read_text())
    assert preserved["locator"] == json.loads(old_locator)
    assert preserved["manifest"] == old_store.inspect("preflight-one")
    assert storage.select_project_execution_storage(str(root))["status"] == "already_selected"
    _bind(root, root / ".taskplane", "new-local-run")
    local = storage.load_workspace_locator(str(root))
    assert local["run_id"] == "new-local-run"
    assert local["home"] == result["home"]
    assert _files(old_home) == old_files
    assert json.loads(archive.read_text()) == preserved


@pytest.mark.parametrize("change", ["contract", "workflow", "stages", "submission", "claim", "loop"])
def test_active_or_used_bindings_refuse_selection_without_side_effects(
        tmp_path, change):
    root = _checkout(tmp_path)
    home = tmp_path / "old-home"
    store, layout = _bind(root, home)
    manifest_path = Path(store._manifest_path("preflight-one"))
    manifest = json.loads(manifest_path.read_text())
    if change == "contract":
        manifest["contract"] = {"status": "active", "task_id": "task-one"}
    elif change == "workflow":
        manifest["workflow"] = {"run_id": "preflight-one"}
    elif change == "stages":
        manifest["stage_heads"] = {"stage-one": {}}
    else:
        name = {"submission": "submission.json", "claim": storage.STAGE_EXECUTION_ROOT_CLAIM,
                "loop": "loop.json"}[change]
        path = Path(layout.state_root) / name
        path.parent.mkdir(parents=True)
        path.write_text("{}")
    manifest_path.write_text(json.dumps(manifest))
    old_files = _files(home)
    old_locator = Path(storage._locator_path(str(root))).read_bytes()
    with pytest.raises(storage.StorageIdentityError, match="execution_storage_migration_required"):
        storage.select_project_execution_storage(str(root))
    assert _files(home) == old_files
    assert Path(storage._locator_path(str(root))).read_bytes() == old_locator
    assert not (root / ".taskplane").exists()


def test_selection_refuses_binding_change_during_archive(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    home = tmp_path / "old-home"
    store, _ = _bind(root, home)
    original = storage._atomic_json

    def write_and_activate(path, value):
        original(path, value)
        if value.get("schema") == "taskplane.execution-storage-archive/v1":
            manifest_path = Path(store._manifest_path("preflight-one"))
            manifest = json.loads(manifest_path.read_text())
            manifest["workflow"] = {"run_id": "preflight-one"}
            manifest_path.write_text(json.dumps(manifest))

    monkeypatch.setattr(storage, "_atomic_json", write_and_activate)
    with pytest.raises(storage.StorageIdentityError, match="execution_storage_migration_required"):
        storage.select_project_execution_storage(str(root))
    assert not Path(storage._selection_path(str(root))).exists()
    assert storage.load_workspace_locator(str(root))["home"] == str(home)
    assert len(list((root / ".taskplane" / "storage-selections").glob("*.json"))) == 1


@pytest.mark.parametrize("changed", ["locator", "manifest", "archive", "marker"])
def test_selected_binding_drift_fails_closed(tmp_path, changed):
    root = _checkout(tmp_path)
    home = tmp_path / "old-home"
    store, _ = _bind(root, home)
    result = storage.select_project_execution_storage(str(root))
    path = {
        "locator": Path(storage._locator_path(str(root))),
        "manifest": Path(store._manifest_path("preflight-one")),
        "archive": Path(result["previous_binding"]["archive_path"]),
        "marker": Path(storage._selection_path(str(root))),
    }[changed]
    value = json.loads(path.read_text())
    if changed == "manifest":
        value["revision"] += 1
    elif changed == "locator":
        value["run_id"] = "foreign-run"
    else:
        value["home"] = str(tmp_path / "redirect")
        if changed == "archive":
            value["manifest"]["revision"] += 1
    path.write_text(json.dumps(value))
    with pytest.raises(storage.StorageIdentityError):
        storage.load_workspace_locator(str(root))


def test_selection_marker_cannot_be_replaced_by_symlink(tmp_path):
    root = _checkout(tmp_path)
    home = tmp_path / "old-home"
    _bind(root, home)
    storage.select_project_execution_storage(str(root))
    path = Path(storage._selection_path(str(root)))
    target = tmp_path / "copied-marker.json"
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(storage.StorageIdentityError, match="unsafe symlink"):
        storage.load_workspace_locator(str(root))


def test_selected_project_refuses_stale_external_environment(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    home = tmp_path / "old-home"
    _bind(root, home)
    monkeypatch.setenv("TASKPLANE_HOME", str(home))
    storage.select_project_execution_storage(str(root))
    with pytest.raises(storage.StorageIdentityError, match="does not match selected"):
        storage.external_store_root(str(root))
    with pytest.raises(storage.StorageIdentityError, match="does not match"):
        storage.bind_workspace_taskplane_home(str(root), os.environ.copy())


def test_prior_history_guard_remains_after_new_local_run_binding(tmp_path):
    root = _checkout(tmp_path)
    old_home = tmp_path / "old-home"
    old_store, _ = _bind(root, old_home)
    selection = storage.select_project_execution_storage(str(root))
    assert selection["prior_history_guard"] is True
    _bind(root, root / ".taskplane", "new-local-run")
    assert storage.load_workspace_locator(str(root))["run_id"] == "new-local-run"
    manifest_path = Path(old_store._manifest_path("preflight-one"))
    old = json.loads(manifest_path.read_text())
    old["workflow"] = {"run_id": "preflight-one"}
    manifest_path.write_text(json.dumps(old))
    before = _files(old_home)
    with pytest.raises(storage.StorageIdentityError, match="execution_storage_migration_required"):
        storage.load_workspace_locator(str(root))
    assert _files(old_home) == before


@pytest.mark.parametrize("history_run", ["unrelated-run", "preflight-one", None])
def test_legacy_singleton_history_is_checked_by_run_identity(tmp_path, history_run):
    root = _checkout(tmp_path)
    home = tmp_path / "old-home"
    _, layout = _bind(root, home)
    history = Path(layout.knowledge_root) / "state" / "loop.json"
    history.parent.mkdir(parents=True)
    history.write_text(json.dumps({"run_id": history_run, "tasks": [{"id": "historical-task"}]}))
    orphan = history.parent / "tracks" / "retained-orphan" / "loop.json"
    orphan.parent.mkdir(parents=True)
    orphan.write_text('{"tasks":[{"id":"archived-orphan"}]}')
    before = _files(home)
    if history_run == "unrelated-run":
        assert storage.select_project_execution_storage(str(root))["status"] == "selected"
        assert storage.load_workspace_locator(str(root)) is None
    else:
        with pytest.raises(storage.StorageIdentityError, match="execution_storage_migration_required"):
            storage.select_project_execution_storage(str(root))
    assert _files(home) == before


def test_ambiguous_active_track_refuses_but_inactive_orphan_is_retained(tmp_path):
    root = _checkout(tmp_path)
    home = tmp_path / "old-home"
    _, layout = _bind(root, home)
    history = Path(layout.knowledge_root) / "state"
    active = history / "tracks" / "active-track" / "loop.json"
    active.parent.mkdir(parents=True)
    active.write_text('{"tasks":[{"id":"unsettled-task"}]}')
    (history / "tracks.json").write_text('{"active":"active-track","tracks":{}}')
    with pytest.raises(storage.StorageIdentityError, match="execution_storage_migration_required"):
        storage.select_project_execution_storage(str(root))
    (history / "tracks.json").write_text('{"active":null,"tracks":{}}')
    before = _files(home)
    assert storage.select_project_execution_storage(str(root))["status"] == "selected"
    assert _files(home) == before


@pytest.mark.parametrize("operation", ["load", "create", "preflight"])
@pytest.mark.parametrize("component", ["runs", "repositories"])
def test_project_run_storage_rejects_redirected_ancestors_before_writing(
        tmp_path, monkeypatch, operation, component):
    root = _checkout(tmp_path)
    home = root / ".taskplane"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("unchanged")
    (home / component).symlink_to(outside, target_is_directory=True)
    monkeypatch.delenv("TASKPLANE_HOME")
    store = run_store.RunStore(workspace=str(root))
    if operation == "load" and component == "repositories":
        # Loading a run does not write repository records; only its own
        # manifest hierarchy needs inspection in this operation.
        return
    with pytest.raises(storage.StorageIdentityError, match="unsafe symlink"):
        if operation == "load":
            store.load("unsafe-run")
        elif operation == "create":
            store.create(storage.resolve_repository_identity(str(root)),
                         run_id="unsafe-run", checkout=str(root), host={}, target={})
        else:
            preflight.RepositoryPreflight(workspace=str(root)).prepare(
                str(root), workspace=str(root), host={}, run_id="unsafe-run")
    assert _files(outside) == {"sentinel": b"unchanged"}


def test_paused_remote_retry_keeps_one_project_home_and_run_identity(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    monkeypatch.delenv("TASKPLANE_HOME")
    engine = preflight.RepositoryPreflight(workspace=str(root),
        tools_provider=lambda: {"git": {"present": False}})
    paused = engine.prepare("https://github.com/example/project.git", workspace=str(root),
                            host={}, run_id="paused-remote")
    initial_home = engine.store.home
    before = engine.store.inspect("paused-remote")
    resumed = engine.resume("paused-remote", action_id=paused["action"]["action_id"],
                            response="retry", approved_by="test")
    assert resumed["status"] == "needs_user"
    assert engine.store.home == initial_home == str(root / ".taskplane")
    assert engine.store.inspect("paused-remote")["revision"] > before["revision"]
    assert len(list((root / ".taskplane").rglob("manifest.json"))) == 1


def test_new_folder_initial_commit_excludes_project_execution_files(tmp_path, monkeypatch):
    root = tmp_path / "new-folder"
    root.mkdir()
    (root / "source.txt").write_text("baseline")
    monkeypatch.delenv("TASKPLANE_HOME")
    engine = preflight.RepositoryPreflight(workspace=str(root),
        tools_provider=lambda: {"git": {"present": True}})
    paused = engine.prepare(str(root), workspace=str(root), host={}, run_id="new-folder-run")
    assert paused["action"]["kind"] == "initialize_or_commit_git"
    assert (root / ".taskplane" / "runs" / "new-folder-run" / "manifest.json").is_file()
    resumed = engine.resume("new-folder-run", action_id=paused["action"]["action_id"],
                            response="initialize", approved_by="test")
    assert resumed["status"] == "ready"
    tracked = subprocess.check_output(["git", "ls-files"], cwd=root, text=True).splitlines()
    assert tracked == ["source.txt"]


def test_explicit_target_preflight_does_not_inspect_unrelated_cwd_binding(tmp_path, monkeypatch):
    caller = _checkout(tmp_path, "caller")
    target = _checkout(tmp_path, "target")
    (caller / ".taskplane").symlink_to(tmp_path / "outside", target_is_directory=True)
    monkeypatch.chdir(caller)
    monkeypatch.delenv("TASKPLANE_HOME")
    engine = preflight.RepositoryPreflight(tools_provider=lambda: {"git": {"present": False}})
    assert engine.prepare(str(target), workspace=str(caller), host={}, run_id="target-run")[
        "status"] == "needs_user"
    assert engine.store.home == str(target / ".taskplane")


def test_stage_paths_allow_only_the_reserved_project_execution_directory(tmp_path):
    root = _checkout(tmp_path)
    home = root / ".taskplane"
    _, layout = _bind(root, home)
    object_path = storage.stage_object_path(str(root), "stage-one", "a" * 64)
    execution = storage.stage_execution_root(str(root), "stage-one", "attempt-one")
    assert object_path.startswith(layout.run_root + os.sep + "stages" + os.sep)
    assert execution.startswith(layout.run_root + os.sep + "stages" + os.sep)
    stages = home / "runs" / "preflight-one" / "stages"
    stages.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(storage.StorageIdentityError, match="unsafe symlink"):
        storage.stage_execution_root(str(root), "stage-one")
    stages.unlink()
    _bind(root, root / "source-subfolder", "unsafe-local-home")
    with pytest.raises(storage.StorageIdentityError, match="inside source checkout"):
        storage.stage_object_path(str(root), "stage-one", "a" * 64)


def test_worker_binding_keeps_primary_home_and_separate_evidence(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=t@example.test",
                    "commit", "--allow-empty", "-qm", "base"], cwd=root, check=True)
    _, layout = _bind(root, root / ".taskplane")
    worker = storage.task_worktree_path(str(root), "task-one")
    subprocess.run(["git", "worktree", "add", "--detach", worker, "HEAD"], cwd=root, check=True,
                   capture_output=True)
    storage.bind_worker_locator(str(root), worker, "task-one")
    monkeypatch.delenv("TASKPLANE_HOME")
    assert storage.taskplane_home(workspace=worker) == layout.home
    child = storage.load_workspace_locator(worker)
    parent = storage.load_workspace_locator(str(root))
    assert child["primary_checkout"] == str(root)
    assert child["paths"]["evidence"] != parent["paths"]["evidence"]
    assert child["paths"]["evidence"].startswith(parent["paths"]["evidence"] + os.sep)


def test_preflight_default_follows_explicit_target_project(tmp_path, monkeypatch):
    caller = _checkout(tmp_path, "caller")
    target = _checkout(tmp_path, "target")
    monkeypatch.delenv("TASKPLANE_HOME")
    monkeypatch.chdir(caller)
    engine = preflight.RepositoryPreflight(tools_provider=lambda: {"git": {"present": False}})
    result = engine.prepare(str(target), workspace=str(caller), host={}, run_id="target-run")
    assert result["status"] == "needs_user"
    assert engine.store.home == str(target / ".taskplane")
    assert (target / ".taskplane" / "runs" / "target-run" / "manifest.json").is_file()
    assert not (caller / ".taskplane").exists()


def test_unbound_native_hooks_share_project_receipt_home_without_creating_run(tmp_path):
    root = _checkout(tmp_path)
    for hook_path in ("native", "bridge"):
        environment = {}
        assert storage.bind_hook_taskplane_home(str(root), environment,
                                               hook_path=hook_path) == str(root / ".taskplane")
        assert environment["TASKPLANE_HOME"] == str(root / ".taskplane")
    assert storage.load_workspace_locator(str(root)) is None
    assert not (root / ".taskplane").exists()


def test_cache_and_private_recovery_follow_workspace_without_environment(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    caller = _checkout(tmp_path, "caller")
    monkeypatch.chdir(caller)
    monkeypatch.delenv("TASKPLANE_HOME")
    monkeypatch.setattr(taskplane_lite, "suite_cache_enabled", lambda: True)
    monkeypatch.setattr(taskplane_lite, "_suite_cache_key", lambda *args: "test-key")
    taskplane_lite.suite_cache_store(str(root), "verified command", {},
                                     returncode=0, tail="passed", duration_s=1)
    assert (root / ".taskplane" / "suite-cache" / "test-key.json").is_file()
    assert taskplane_lite.suite_cache_lookup(str(root), "verified command", {})["returncode"] == 0
    stale = root / ".taskplane-kb" / "meta.json"
    stale.parent.mkdir()
    stale.write_text('{"workspace":"private-path"}')
    preserved = storage._quarantine_shared_store_meta(str(stale), str(root))
    assert Path(preserved).parent == root / ".taskplane" / "privacy-quarantine"
    assert Path(preserved).read_text() == '{"workspace":"private-path"}'
    assert not (caller / ".taskplane").exists()


def test_project_cache_path_supports_package_import_without_flat_module_path(tmp_path):
    root = _checkout(tmp_path)
    repository = str(Path(__file__).resolve().parents[2])
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"TASKPLANE_HOME", "PYTHONPATH"}}
    code = "from taskplane import taskplane_lite; import sys; print(taskplane_lite._suite_cache_path('test-key', sys.argv[1]))"
    result = subprocess.run([sys.executable, "-c", code, str(root)], cwd=repository,
                            env=environment, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == str(root / ".taskplane" / "suite-cache" / "test-key.json")


def test_archived_project_run_allows_successor_without_dropping_prior_guard(tmp_path, monkeypatch):
    root = _checkout(tmp_path)
    old_home = tmp_path / "old-home"
    _bind(root, old_home)
    monkeypatch.delenv("TASKPLANE_HOME")
    selection = storage.select_project_execution_storage(str(root))
    old_files = _files(old_home)
    _, layout = _bind(root, root / ".taskplane", run_id="completed-project-run")
    from taskplane import loop
    archived = loop.archive(str(root), by="human:test")
    assert archived["archived"] and archived["evidence_retained"]
    assert storage.load_workspace_locator(str(root)) is None
    assert storage.taskplane_home(workspace=str(root)) == layout.home
    _bind(root, root / ".taskplane", run_id="successor")
    assert storage.load_workspace_locator(str(root))["run_id"] == "successor"
    assert Path(archived["locator"]).is_file()
    assert _files(old_home) == old_files
    Path(selection["previous_binding"]["archive_path"]).write_text("{}")
    with pytest.raises(storage.StorageIdentityError, match="archive fingerprint mismatch"):
        storage.load_workspace_locator(str(root))
