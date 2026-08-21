"""Executable rollout and rollback contract for stage-native v4 runs.

The rollout is deliberately one-way: shadow work is read-only, only a new-run
canary may promote a pristine v3 run, and rollback pauses mutations without
collapsing or deleting an already-migrated run.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from taskplane import loop, run_store, stage_migration, storage, taskplane_lite


RUN_ID = "run-stage-rollout"
NOW = "2026-08-21T16:00:00Z"
ROOT = Path(__file__).resolve().parents[2]
ROLLOUT_ABORT_SIGNALS = [
    "predecessor_root_open",
    "ambiguous_active_projection",
    "terminal_reopen_attempt",
    "handoff_integrity_failure",
    "authority_mismatch",
    "startup_bound_exceeded",
    "migration_conservation_mismatch",
    "r0003_cleanup_proof_failure",
]
ROLLOUT_POLICY = {
    "canary": {
        "mode": "new-run",
        "exact_run_count": 1,
        "named_owner_required": True,
        "owner_source": "stage_authority.actor",
    },
    "observation": {
        "minimum_hours": 24,
        "retro_required": True,
    },
    "abort": {
        "threshold": 1,
        "signals": ROLLOUT_ABORT_SIGNALS,
    },
    "rollback": {
        "maximum_minutes": 15,
        "action": "disable-v4-mutations-retain-v4-read-only",
    },
}


def _store(tmp_path: Path) -> tuple[run_store.RunStore, dict[str, object]]:
    identity = storage.identity_from_remote(
        "https://github.com/example/project.git")
    store = run_store.RunStore(home=str(tmp_path / "home"))
    manifest = store.create(
        identity,
        run_id=RUN_ID,
        checkout=str(tmp_path / "checkout"),
        host={"kind": "codex", "session_id": "thread-rollout"},
        target={"kind": "workspace", "revision": "1" * 40},
    )
    return store, manifest


def _authority() -> dict[str, object]:
    return {
        "schema": "taskplane.stage-authority-binding/v1",
        "run_id": RUN_ID,
        "repository_id": "github.com/example/project",
        "repository_key": "github.com-example-project",
        "worktree_id": "legacy-workspace",
        "target_revision": "1" * 40,
        "worktree_revision": "1" * 40,
        "requirement_id": "R-0004",
        "requirement_revision": "4",
        "design_revision": "2",
        "design_fingerprint": "c" * 64,
        "actor": "human:vdemkiv",
        "session_id": "codex-thread-rollout",
        "authority_revision": 7,
        "authority_fingerprint": "d" * 64,
    }


def _legacy_sources(step: str = "execute") -> dict[str, bytes]:
    loop_state = {
        "governance_revision": 2,
        "goal": "Retain the legacy delivery record",
        "requirement_id": "R-0004",
        "step": step,
        "current_task": 0,
        "tasks": [
            {"id": "t01", "status": "passed", "commit": "a" * 40,
             "reviews": [{"lens": "qa", "verdict": "pass"}],
             "evidence": [{"kind": "suite", "fingerprint": "e" * 64}]},
            {"id": "t02", "status": "running", "deps": ["t01"]},
        ],
        "decisions": [{"id": "D-1", "decision": "approved"}],
        "audit_history": [{"event": "gate", "actor": "human:vdemkiv"}],
    }
    return {
        "loop.json": (json.dumps(loop_state, indent=2) + "\n").encode(),
        "tracks.json": b'{"active":"main","tracks":{}}\n',
        "requirements/R-0004.json": b'{"id":"R-0004"}\n',
    }


def _migrate(
        workspace: Path, store: run_store.RunStore, initial: dict[str, object],
        *, step: str = "execute") -> dict[str, object]:
    return stage_migration.migrate_singleton(
        str(workspace), store=store, run_id=RUN_ID,
        expected_revision=int(initial["revision"]),
        operation_id=f"migrate-rollout-{step}",
        authority=_authority(),
        requirement={
            "id": "R-0004", "revision": "4", "fingerprint": "b" * 64},
        design={"revision": "2", "fingerprint": "c" * 64},
        contracts=["contract:stage-entity-lifecycle"],
        created_at=NOW,
        legacy_sources=_legacy_sources(step),
        authority_validator=lambda _expected, _current: None,
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_tp_go_flow_pins_the_complete_canary_and_rollback_policy() -> None:
    flow = json.loads(
        (ROOT / "skills/tp-go/flow.json").read_text(encoding="utf-8"))
    policy = flow["rollout_policy"]

    assert policy == ROLLOUT_POLICY
    assert type(policy["canary"]["exact_run_count"]) is int
    assert policy["canary"]["exact_run_count"] == 1
    assert policy["canary"]["named_owner_required"] is True
    assert policy["canary"]["owner_source"] == "stage_authority.actor"
    assert policy["observation"] == {
        "minimum_hours": 24, "retro_required": True}
    assert policy["abort"]["threshold"] == 1
    assert policy["abort"]["signals"] == ROLLOUT_ABORT_SIGNALS
    assert len(policy["abort"]["signals"]) == len(
        set(policy["abort"]["signals"]))
    assert policy["rollback"]["maximum_minutes"] <= 15


def test_tp_go_guidance_requires_owner_window_retro_abort_and_slo() -> None:
    guidance = (ROOT / "skills/tp-go/SKILL.md").read_text(encoding="utf-8")

    for required in (
        "exactly one `new-run` canary",
        "named, accountable\nowner is exactly the human `stage_authority.actor` recorded for that run",
        "24-hour observation window",
        "completed both a 24-hour observation window and its Retro",
        "Every abort signal has threshold `1`",
        "within at most 15 minutes",
        "retaining v4 read access",
    ):
        assert required in guidance


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "disabled"),
        ("", "disabled"),
        ("true", "disabled"),
        ("1", "disabled"),
        ("NEW-RUN", "new-run"),
        ("new-run", "new-run"),
        ("enabled", "enabled"),
    ],
)
def test_stage_rollout_requires_an_exact_explicit_mode(
        raw: str | None, expected: str) -> None:
    environment = {} if raw is None else {"TASKPLANE_STAGE_NATIVE": raw}

    assert taskplane_lite.stage_native_mode(environment) == expected
    assert taskplane_lite.stage_native_enabled(environment) is \
        (expected != "disabled")


def test_normal_new_run_init_can_reach_the_public_stage_start(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Importing the public-journey fixture locally keeps this rollout module
    # independent from cross-host collection while exercising the same real
    # RunStore/lifecycle boundary used by host adapters.
    from taskplane.tests.test_stage_cross_host import (
        _real_loop_stage, _record_bootstrap_requirement)

    workspace, store, stage = _real_loop_stage(tmp_path)
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "cross-host-session")
    requirement = _record_bootstrap_requirement(workspace, ordinal=4)
    assert requirement["id"] == "R-0004"

    initialized = loop.init(
        str(workspace), "exercise the stage-native canary",
        requirement_id="R-0004", by="human:vdemkiv")
    started = loop.stage_command(str(workspace), "start", {
        "schema": "taskplane.stage-command/v1",
        "stage": stage,
        "expected_revision": 1,
        "operation_id": "start-reachable-new-run-canary",
        "expected_predecessor_fingerprints": {},
        "foreground": True,
        "authority": stage["authority"],
        "declared_scope": {
            "scope_paths": ["specs/spec.md"],
            "out_of_scope_paths": ["taskplane/loop.py"],
        },
    })

    assert "error" not in initialized, initialized
    assert "error" not in started, started
    assert started["command"] == "start"
    assert started["dispatch"]["startup"]["stage_id"] == stage["stage_id"]
    assert store.load(str(stage["run_id"]))["schema"] == "taskplane.run/v4"


def test_new_run_next_bootstraps_and_replays_one_stage_root_without_artifacts(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement)

    workspace, store, initial = _real_pristine_run(tmp_path)
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    requirement = _record_bootstrap_requirement(workspace)
    initialized = loop.init(
        str(workspace), "start from one bounded root",
        requirement_id=str(requirement["id"]), by="human:vdemkiv")
    first = loop.next_action.__wrapped__(str(workspace))
    committed = store.load(str(initial["run_id"]))
    second = loop.next_action.__wrapped__(str(workspace))

    assert "error" not in initialized, initialized
    assert "error" not in first, first
    assert "error" not in second, second
    first_stage_id = first["stage_runtime_dispatch"]["startup"]["stage_id"]
    assert second["stage_runtime_dispatch"]["startup"]["stage_id"] == \
        first_stage_id
    assert committed["schema"] == "taskplane.run/v4"
    assert set(committed["stage_heads"]) == {first_stage_id}
    assert committed["stage_heads"][first_stage_id]["summary"][
        "stage_kind"] == "product"
    assert first_stage_id in json.dumps(
        loop.load(str(workspace))["_stage_run_binding"], sort_keys=True)
    assert len([
        receipt for receipt in committed["stage_operations"].values()
        if receipt.get("operation") == "start_stage"
    ]) == 1


def test_new_run_init_force_refuses_existing_singleton_without_archiving(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement)

    workspace, store, initial = _real_pristine_run(tmp_path)
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    requirement = _record_bootstrap_requirement(workspace)
    initialized = loop.init(
        str(workspace), "retain the attributable singleton",
        requirement_id=str(requirement["id"]), by="human:vdemkiv")
    assert "error" not in initialized, initialized
    state_path = Path(loop._loop_path(str(workspace)))
    state_before = state_path.read_bytes()
    workspace_before = _tree_bytes(workspace / ".taskplane")
    store_before = _tree_bytes(
        Path(store.home) / "runs" / str(initial["run_id"]))

    refused = loop.init(
        str(workspace), "do not replace governed history",
        requirement_id=str(requirement["id"]), force=True,
        by="human:vdemkiv")

    assert refused["refused"] is True
    assert "refuses existing singleton/history" in refused["error"]
    assert "fresh governed run" in refused["error"]
    assert state_path.read_bytes() == state_before
    assert _tree_bytes(workspace / ".taskplane") == workspace_before
    assert _tree_bytes(
        Path(store.home) / "runs" / str(initial["run_id"])) == store_before
    assert list(state_path.parent.glob(state_path.name + ".replaced-*")) == []


@pytest.mark.parametrize("mode", ["new-run", "disabled"])
def test_bound_new_run_refuses_a_cloned_store_with_the_same_run_identity(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement)

    workspace, store, initial = _real_pristine_run(tmp_path)
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    requirement = _record_bootstrap_requirement(workspace)
    initialized = loop.init(
        str(workspace), "bind one exact store",
        requirement_id=str(requirement["id"]), by="human:vdemkiv")
    bootstrapped = loop.next_action.__wrapped__(str(workspace))
    assert "error" not in initialized, initialized
    assert "error" not in bootstrapped, bootstrapped

    clone_home = tmp_path / "cloned-home"
    shutil.copytree(store.home, clone_home)
    identity = storage.resolve_repository_identity(str(workspace))
    layout = storage.resolve_layout(
        identity, home=str(clone_home), run_id=str(initial["run_id"]))
    storage.write_workspace_locator(
        str(workspace), identity=identity, layout=layout,
        run_id=str(initial["run_id"]))
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, mode)
    state_path = Path(loop._loop_path(str(workspace)))
    state_before = state_path.read_bytes()
    original_before = _tree_bytes(
        Path(store.home) / "runs" / str(initial["run_id"]))
    clone_before = _tree_bytes(
        clone_home / "runs" / str(initial["run_id"]))

    refused = loop.resolve(str(workspace), "abort")

    assert "stage-native bound run store identity changed" in refused["error"]
    assert refused["stage_native"] == "read-only"
    assert state_path.read_bytes() == state_before
    assert _tree_bytes(
        Path(store.home) / "runs" / str(initial["run_id"])) == original_before
    assert _tree_bytes(
        clone_home / "runs" / str(initial["run_id"])) == clone_before


@pytest.mark.parametrize("mode", ["new-run", "disabled"])
def test_bootstrapped_new_run_refuses_public_mutation_without_run_binding(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement)

    workspace, store, initial = _real_pristine_run(tmp_path)
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    requirement = _record_bootstrap_requirement(workspace)
    initialized = loop.init(
        str(workspace), "require the persisted run binding",
        requirement_id=str(requirement["id"]), by="human:vdemkiv")
    bootstrapped = loop.next_action.__wrapped__(str(workspace))
    assert "error" not in initialized, initialized
    assert "error" not in bootstrapped, bootstrapped
    state = loop.load(str(workspace))
    state.pop("_stage_run_binding")
    loop.save(str(workspace), state)
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, mode)
    state_path = Path(loop._loop_path(str(workspace)))
    state_before = state_path.read_bytes()
    store_before = _tree_bytes(
        Path(store.home) / "runs" / str(initial["run_id"]))

    refused = loop.resolve(str(workspace), "abort")

    assert "stage-native migrated run binding is missing" in refused["error"]
    assert refused["stage_native"] == "read-only"
    assert state_path.read_bytes() == state_before
    assert _tree_bytes(
        Path(store.home) / "runs" / str(initial["run_id"])) == store_before


def test_structurally_pristine_singleton_without_new_run_marker_is_refused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _real_loop_stage, _record_bootstrap_requirement)

    workspace, _store_value, stage = _real_loop_stage(tmp_path)
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "cross-host-session")
    requirement = _record_bootstrap_requirement(workspace, ordinal=4)
    assert requirement["id"] == "R-0004"
    state = loop.init(
        str(workspace), "do not infer canary authority",
        requirement_id="R-0004", by="human:vdemkiv")
    state.pop("_stage_native_new_run_pristine", None)
    state.pop("_stage_native_root_authority", None)
    loop.save(str(workspace), state)

    refused = loop.stage_command(str(workspace), "start", {
        "schema": "taskplane.stage-command/v1",
        "stage": stage,
        "expected_revision": 1,
        "operation_id": "refuse-unmarked-structural-singleton",
        "expected_predecessor_fingerprints": {},
        "foreground": True,
        "authority": stage["authority"],
    })

    assert refused["enabled"] is False
    assert "cannot promote an existing singleton" in refused["error"]
    assert _store_value.load(str(stage["run_id"]))["schema"] == \
        "taskplane.run/v3"


def test_shadow_migration_compares_conservation_without_switching_readers(
        tmp_path: Path) -> None:
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    store, initial = _store(tmp_path)
    sources = _legacy_sources()
    before = _tree_bytes(Path(store.home) / "runs" / RUN_ID)

    shadow = stage_migration.retain_legacy_sources(sources)

    assert stage_migration.verify_retained_sources(shadow, sources)
    assert shadow["conservation"]["tasks"]["count"] == 2
    assert shadow["conservation"]["reviews"]["count"] == 1
    assert shadow["conservation"]["evidence"]["count"] == 1
    assert stage_migration.migration_projection(
        str(workspace), store=store, run_id=RUN_ID) is None
    assert store.load(RUN_ID) == initial
    assert _tree_bytes(Path(store.home) / "runs" / RUN_ID) == before


@pytest.mark.parametrize(
    ("mode", "singleton", "migration_bound", "expected_substring"),
    [
        ("disabled", False, False, "stage-native mutation is disabled"),
        ("new-run", False, False, "new-run"),
        ("new-run", True, False, "cannot promote an existing singleton"),
        ("new-run", False, True, "migration-bound"),
        ("enabled", False, False, "requires TASKPLANE_STAGE_NATIVE=new-run"),
    ],
)
def test_new_run_canary_never_promotes_an_existing_singleton(
        monkeypatch: pytest.MonkeyPatch, mode: str,
        singleton: bool, migration_bound: bool,
        expected_substring: str) -> None:
    state = {"step": "execute"} if singleton else None
    monkeypatch.setattr(loop, "load", lambda _workspace: state)
    manifest: dict[str, object] = {
        "schema": "taskplane.run/v3", "run_id": RUN_ID}
    if migration_bound:
        manifest["migration_receipt"] = {"fingerprint": "f" * 64}

    blocker = loop._stage_mutation_blocker(mode, manifest, "/workspace")
    assert expected_substring in str(blocker)


def test_verified_migration_is_the_reader_cutover_boundary(
        tmp_path: Path) -> None:
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    store, initial = _store(tmp_path)

    assert stage_migration.migration_projection(
        str(workspace), store=store, run_id=RUN_ID) is None

    receipt = _migrate(workspace, store, initial)
    projection = stage_migration.migration_projection(
        str(workspace), store=store, run_id=RUN_ID)

    assert receipt["result"]["classification"] == "stage"
    assert projection is not None
    assert projection["receipt"] == receipt
    assert projection["foreground_stage_id"] == receipt["stage_ids"][0]
    assert projection["stages"][receipt["stage_ids"][0]]["state"] == \
        "active"


def test_rollback_pauses_mutation_but_preserves_migrated_history_and_bytes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    store, initial = _store(tmp_path)
    receipt = _migrate(workspace, store, initial)
    stage_id = receipt["stage_ids"][0]
    run_root = Path(store.home) / "runs" / RUN_ID
    before = _tree_bytes(run_root)
    monkeypatch.setattr(loop, "_stage_store", lambda _ws, _run_id: store)
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)

    history = loop.stage_command(
        str(workspace), "history", {"run_id": RUN_ID, "limit": 100})
    refused = loop.stage_command(
        str(workspace), "resume", {"run_id": RUN_ID, "stage_id": stage_id})

    assert [row["stage_id"] for row in history["stages"]] == [stage_id]
    assert history["stages"][0]["state"] == "active"
    assert refused == {
        "schema": "taskplane.stage-command-result/v1",
        "command": "resume",
        "run_id": RUN_ID,
        "enabled": False,
        "legacy": False,
        "error": "stage-native mutation is disabled",
    }
    assert _tree_bytes(run_root) == before
    assert loop._stage_mutation_blocker(
        "enabled", store.load(RUN_ID), str(workspace)) is None


@pytest.mark.parametrize(
    ("command", "payload"),
    [
        ("start", {"stage": {"run_id": RUN_ID}}),
        ("reuse", {"stage": {"run_id": RUN_ID}}),
        ("resume", {"run_id": RUN_ID}),
        ("terminalize", {"run_id": RUN_ID}),
        ("terminalize-and-start", {"stage": {"run_id": RUN_ID}}),
        ("split", {"run_id": RUN_ID}),
    ],
)
def test_disabled_migrated_v4_refuses_every_public_stage_mutation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str,
        payload: dict[str, object]) -> None:
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    store, initial = _store(tmp_path)
    _migrate(workspace, store, initial)
    run_root = Path(store.home) / "runs" / RUN_ID
    before = _tree_bytes(run_root)
    monkeypatch.setattr(loop, "_stage_store", lambda _ws, _run_id: store)
    monkeypatch.delenv(taskplane_lite.STAGE_NATIVE_ENV, raising=False)

    refused = loop.stage_command(str(workspace), command, payload)

    assert refused["command"] == command
    assert refused["run_id"] == RUN_ID
    assert refused["enabled"] is False
    assert refused["legacy"] is False
    assert "stage-native mutation is disabled" in refused["error"]
    assert _tree_bytes(run_root) == before


def test_disabled_migrated_v4_refuses_singleton_resolution_without_writes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    store, initial = _store(tmp_path)
    identity = storage.identity_from_remote(
        "https://github.com/example/project.git")
    layout = storage.resolve_layout(
        identity, home=store.home, run_id=RUN_ID)
    storage.write_workspace_locator(
        str(workspace), identity=identity, layout=layout, run_id=RUN_ID)
    state = {
        "governance_revision": 2,
        "goal": "retain the migrated stage history",
        "step": "escalated",
        "parallel": False,
        "max_fix_cycles": 2,
        "checkpoints": [],
        "current_task": 0,
        "tasks": [{"id": "t01", "status": "failed", "fix_cycles": 2}],
    }
    loop.save(str(workspace), state)
    _migrate(workspace, store, initial, step="escalated")
    run_root = Path(store.home) / "runs" / RUN_ID
    state_path = Path(loop._loop_path(str(workspace)))
    before_run = _tree_bytes(run_root)
    before_state = state_path.read_bytes()
    monkeypatch.delenv(taskplane_lite.STAGE_NATIVE_ENV, raising=False)

    refused = loop.resolve(str(workspace), "abort")

    assert "stage-native mutation is disabled" in refused["error"]
    assert refused["stage_native"] == "read-only"
    assert state_path.read_bytes() == before_state
    assert _tree_bytes(run_root) == before_run


@pytest.mark.parametrize("corruption", ["locator", "manifest"])
def test_disabled_rollback_refuses_mutation_when_stage_storage_is_corrupt(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        corruption: str) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement)

    workspace, store, initial = _real_pristine_run(tmp_path)
    monkeypatch.setenv("TASKPLANE_HOME", store.home)
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    requirement = _record_bootstrap_requirement(workspace)
    state = loop.init(
        str(workspace), "retain rollback authority",
        requirement_id=str(requirement["id"]), by="human:vdemkiv")
    dispatched = loop.next_action.__wrapped__(str(workspace))
    assert "error" not in dispatched, dispatched
    state.update({
        "step": "escalated",
        "tasks": [{"id": "t01", "status": "failed", "fix_cycles": 2}],
        "current_task": 0,
    })
    loop.save(str(workspace), state)
    state_path = Path(loop._loop_path(str(workspace)))
    before_state = state_path.read_bytes()
    if corruption == "locator":
        corrupted_path = Path(storage._locator_path(str(workspace)))
    else:
        corrupted_path = Path(store.home) / "runs" / str(
            initial["run_id"]) / "manifest.json"
    corrupted_path.write_text("{not-valid-json\n", encoding="utf-8")
    before_corrupt_bytes = corrupted_path.read_bytes()
    monkeypatch.delenv(taskplane_lite.STAGE_NATIVE_ENV, raising=False)

    refused = loop.resolve(str(workspace), "abort")

    assert "error" in refused
    assert "stage-native" in refused["error"]
    assert refused["stage_native"] == "read-only"
    assert state_path.read_bytes() == before_state
    assert corrupted_path.read_bytes() == before_corrupt_bytes


@pytest.mark.parametrize("mode", ["disabled", "enabled"])
def test_migrated_new_run_refuses_mutation_after_locator_is_missing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    from taskplane.tests.test_stage_cross_host import (
        _real_pristine_run, _record_bootstrap_requirement)

    workspace, store, initial = _real_pristine_run(tmp_path)
    monkeypatch.setenv("TASKPLANE_HOME", store.home)
    monkeypatch.setenv("TASKPLANE_STORE", "repo")
    monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "new-run")
    monkeypatch.setenv("TASKPLANE_SESSION_ID", "pristine-session")
    requirement = _record_bootstrap_requirement(workspace)
    initialized = loop.init(
        str(workspace), "retain lost locator authority",
        requirement_id=str(requirement["id"]), by="human:vdemkiv")
    dispatched = loop.next_action.__wrapped__(str(workspace))
    assert "error" not in initialized, initialized
    assert "error" not in dispatched, dispatched
    assert store.load(str(initial["run_id"]))["schema"] == \
        "taskplane.run/v4"
    state = loop.load(str(workspace))
    state.update({
        "step": "escalated",
        "tasks": [{"id": "t01", "status": "failed", "fix_cycles": 2}],
        "current_task": 0,
    })
    loop.save(str(workspace), state)
    state_path = Path(loop._loop_path(str(workspace)))
    before_state = state_path.read_bytes()
    locator_path = Path(storage._locator_path(str(workspace)))
    locator_path.unlink()
    if mode == "disabled":
        monkeypatch.delenv(taskplane_lite.STAGE_NATIVE_ENV, raising=False)
    else:
        monkeypatch.setenv(taskplane_lite.STAGE_NATIVE_ENV, "enabled")

    refused = loop.resolve(str(workspace), "abort")

    assert "error" in refused
    assert "locator" in refused["error"] or "read-only" in refused["error"]
    assert refused["stage_native"] == "read-only"
    assert state_path.read_bytes() == before_state
    assert not locator_path.exists()


def test_rollback_never_guesses_an_ambiguous_legacy_outcome(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    store, initial = _store(tmp_path)
    receipt = _migrate(workspace, store, initial, step="mystery-state")
    run_root = Path(store.home) / "runs" / RUN_ID
    before = _tree_bytes(run_root)
    monkeypatch.setattr(loop, "_stage_store", lambda _ws, _run_id: store)
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)

    projection = stage_migration.migration_projection(
        str(workspace), store=store, run_id=RUN_ID)
    history = loop.stage_command(
        str(workspace), "history", {"run_id": RUN_ID, "limit": 100})

    assert receipt["stage_ids"] == []
    assert receipt["result"]["classification"] == "legacy-unknown"
    assert receipt["result"]["unknown_reason"] == \
        "unrecognized_loop_step:mystery-state"
    assert projection is not None
    assert projection["stages"] == {}
    assert history["stages"] == []
    assert _tree_bytes(run_root) == before
