"""R-0001 AC5/7/27/28: recovery, preparation, and onboarding continuity."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


TASKPLANE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TASKPLANE))

import preflight  # noqa: E402
import recovery  # noqa: E402
import repository  # noqa: E402
import storage  # noqa: E402
from command_runtime import CommandRuntime  # noqa: E402
from command_adapters import CommandAdapter, HostLaunch  # noqa: E402


@pytest.mark.parametrize("attempt", [1, 2, 3])
def test_routine_failures_recover_for_three_bounded_attempts(attempt):
    result = recovery.decide_recovery(
        failure_class="artifact", attempt=attempt,
        fingerprints=[f"failure-{attempt}"], progress=[])
    assert result["status"] == "recover"
    assert result["reason"] == "routine_retry"


def test_recovery_exhaustion_and_authority_or_safety_changes_escalate():
    exhausted = recovery.decide_recovery(
        failure_class="render", attempt=4,
        fingerprints=["a", "b", "c", "d"])
    authority = recovery.decide_recovery(
        failure_class="setup", attempt=1, authority_changed=True)
    unsafe = recovery.decide_recovery(
        failure_class="transient", attempt=1, safe=False)
    replan = recovery.decide_recovery(
        failure_class="metadata", attempt=1, replan_required=True)
    assert exhausted["reason"] == "retry_budget_exhausted"
    assert authority["reason"] == "authority_change"
    assert unsafe["reason"] == "unsafe_recovery"
    assert replan["reason"] == "replan_required"
    assert {row["status"] for row in (exhausted, authority, unsafe, replan)} == {
        "escalate"
    }


def test_measured_convergence_can_continue_past_retry_budget():
    result = recovery.decide_recovery(
        failure_class="collection", attempt=6,
        fingerprints=["f1", "f2", "f3", "f4", "f5", "f6"],
        progress=[1, 2, 3, 4, 5, 6])
    assert result["status"] == "recover"
    assert result["reason"] == "measurable_convergence"


@pytest.mark.parametrize(
    ("fingerprints", "progress", "reason"),
    [
        (["same", "same"], [], "repeated_fingerprint"),
        (["a", "b", "c"], [1, 1, 1], "no_progress"),
        (["a", "b", "a"], [1, 2, 1], "oscillation"),
        (["a", "b", "c"], [3, 2, 1], "worsening"),
    ],
)
def test_non_convergent_recovery_escalates(fingerprints, progress, reason):
    result = recovery.decide_recovery(
        failure_class="evaluator", attempt=3,
        fingerprints=fingerprints, progress=progress)
    assert result == {
        "schema": "taskplane.recovery-decision/v1",
        "status": "escalate",
        "reason": reason,
        "attempt": 3,
        "failure_class": "evaluator",
    }


def test_setup_matrix_repairs_or_prompts_once_and_waits_without_prompt_loops():
    repaired = []
    checks = [
        {"id": "metadata", "classification": "self-repairable"},
        {"id": "storage", "classification": "authority-required"},
        {"id": "managed-hooks", "classification": "host-policy"},
        {"id": "registry", "classification": "external-unavailable"},
    ]
    first = preflight.reconcile_onboarding_checks(
        checks, repair=lambda check: repaired.append(check["id"]) or True)
    second = preflight.reconcile_onboarding_checks(
        checks, repair=lambda check: True,
        prior_prompt_ids=first["prompt_ids"])
    assert repaired == ["metadata"]
    assert first["status"] == "needs_user"
    assert [row["id"] for row in first["actions"]] == ["storage"]
    assert first["prompt_ids"] == ["storage"]
    assert second["actions"] == []
    assert second["prompt_ids"] == ["storage"]
    assert {row["status"] for row in first["checks"]} == {
        "repaired", "needs_authority", "waiting_host_policy",
        "waiting_external",
    }


@pytest.mark.parametrize(
    ("check_id", "classification", "expected_status", "prompt_count"),
    [
        ("workspace", "self-repairable", "repaired", 0),
        ("git", "self-repairable", "repaired", 0),
        ("init", "self-repairable", "repaired", 0),
        ("hook_install", "self-repairable", "repaired", 0),
        ("repository_trust", "authority-required", "needs_authority", 1),
        ("managed_policy", "host-policy", "waiting_host_policy", 0),
        ("loaded_session", "external-unavailable", "waiting_external", 0),
        ("effective_hook_path", "host-policy", "waiting_host_policy", 0),
    ],
)
def test_every_onboarding_setup_check_has_bounded_recovery_and_prompt_counts(
        check_id, classification, expected_status, prompt_count):
    checks = [{"id": check_id, "classification": classification,
               "detail": f"authority for {check_id}"}]
    first = preflight.reconcile_onboarding_checks(
        checks, repair=lambda check: True)
    resumed = preflight.reconcile_onboarding_checks(
        checks, repair=lambda check: True,
        prior_prompt_ids=first["prompt_ids"])
    assert first["checks"][0]["status"] == expected_status
    assert len(first["actions"]) == prompt_count
    assert resumed["actions"] == []
    if classification == "authority-required":
        assert first["actions"][0]["authority"] == f"authority for {check_id}"
        assert resumed["status"] == "waiting"


def test_onboarding_retries_failed_self_repair_after_state_changes():
    attempts = 0

    def repair(check):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("filesystem temporarily read-only")
        return True

    checks = [{"id": "init", "classification": "self-repairable"}]
    blocked = preflight.reconcile_onboarding_checks(checks, repair=repair)
    recovered = preflight.reconcile_onboarding_checks(
        checks, repair=repair, prior_prompt_ids=blocked["prompt_ids"])
    assert blocked["status"] == "waiting"
    assert blocked["checks"][0]["status"] == "repair_failed"
    assert blocked["actions"] == []
    assert recovered["status"] == "ready"
    assert recovered["checks"][0]["status"] == "repaired"
    assert attempts == 2


class _SequenceAcquirer:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def acquire_repository(self, identity, target):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    acquire_pr = acquire_repository


def _acquired(checkout: Path, *, head="a", base="b", merge_base="c"):
    checkout.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    return repository.AcquisitionResult(
        checkout=str(checkout), base_ref="refs/remotes/origin/main",
        base=base * 40, head=head * 40, merge_base=merge_base * 40,
        changed_files=("src/changed.py",),
        metadata={"url": "https://github.com/example/project"})


def _repository_preflight(tmp_path, acquirer):
    return preflight.RepositoryPreflight(
        home=str(tmp_path / "home"),
        tools_provider=lambda: {
            "git": {"present": True},
            "gh": {"present": True, "authenticated": True},
        }, acquirer=acquirer)


def test_repository_acquisition_uses_bounded_automatic_recovery(monkeypatch):
    monkeypatch.setenv("TASKPLANE_CONSOLIDATED_FLOW", "1")
    acquirer = _SequenceAcquirer([
        repository.RepositoryAcquisitionError("network", "temporary one"),
        repository.RepositoryAcquisitionError("network", "temporary two"),
        "ready",
    ])
    result = repository.acquire_with_recovery(
        lambda: acquirer.acquire_repository(None, {}))
    assert result["status"] == "ready"
    assert result["value"] == "ready"
    assert result["attempts"] == 3
    assert acquirer.calls == 3


def test_preflight_production_path_automatically_recovers_repository(
        tmp_path, monkeypatch):
    monkeypatch.setenv("TASKPLANE_CONSOLIDATED_FLOW", "1")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    acquired = repository.AcquisitionResult(
        checkout=str(checkout), base_ref="origin/main", base="b" * 40,
        head="a" * 40, merge_base="c" * 40,
        changed_files=("a.py",), metadata={})
    acquirer = _SequenceAcquirer([
        repository.RepositoryAcquisitionError("network", "temporary one"),
        repository.RepositoryAcquisitionError("network", "temporary two"),
        acquired,
    ])
    engine = preflight.RepositoryPreflight(
        home=str(tmp_path / "home"),
        tools_provider=lambda: {
            "git": {"present": True},
            "gh": {"present": True, "authenticated": True},
        }, acquirer=acquirer)
    result = engine.prepare(
        "https://github.com/example/project.git",
        workspace=str(tmp_path), host={"kind": "codex"}, run_id="recover")
    assert result["status"] == "ready"
    assert acquirer.calls == 3


def test_repository_preparation_local_fixture_pins_and_verifies_head(
        tmp_path):
    checkout = tmp_path / "local"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    (checkout / "tracked.txt").write_text("pinned\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=checkout, check=True)
    subprocess.run([
        "git", "-c", "user.name=Taskplane", "-c",
        "user.email=taskplane@example.invalid", "commit", "-qm", "base",
    ], cwd=checkout, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=checkout, check=True,
        text=True, stdout=subprocess.PIPE, encoding="utf-8",
        errors="replace").stdout.strip()

    result = _repository_preflight(
        tmp_path, _SequenceAcquirer([])).prepare(
            str(checkout), workspace=str(tmp_path), host={"kind": "codex"},
            run_id="local")
    assert result["status"] == "ready"
    assert result["checkout"] == str(checkout.resolve())
    assert result["target"]["ok"] is True
    assert result["target"]["head"] == head
    assert result["target"]["root"] == str(checkout.resolve())
    assert result["target"]["fingerprint"]


def test_repository_preparation_remote_fixture_preserves_pinned_target(
        tmp_path, monkeypatch):
    monkeypatch.setenv("TASKPLANE_CONSOLIDATED_FLOW", "1")
    acquired = _acquired(tmp_path / "remote-checkout")
    acquirer = _SequenceAcquirer([acquired])
    result = _repository_preflight(tmp_path, acquirer).prepare(
        "https://github.com/example/project.git", workspace=str(tmp_path),
        host={"kind": "codex"}, run_id="remote")
    assert result["status"] == "ready"
    assert acquirer.calls == 1
    assert result["target"]["target"]["kind"] == "repository"
    assert result["target"]["head"] == acquired.head
    assert result["target"]["base"] == acquired.base
    assert result["target"]["merge_base"] == acquired.merge_base
    assert result["target"]["base_ref"] == acquired.base_ref
    assert result["target"]["changed_files"] == ["src/changed.py"]
    assert result["target"]["fingerprint"]


def test_repository_preparation_cached_fixture_avoids_remote_reacquisition(
        tmp_path, monkeypatch):
    monkeypatch.setenv("TASKPLANE_CONSOLIDATED_FLOW", "1")
    acquired = _acquired(tmp_path / "cached-checkout")
    acquirer = _SequenceAcquirer([acquired])
    engine = _repository_preflight(tmp_path, acquirer)
    first = engine.prepare(
        "https://github.com/example/project.git", workspace=str(tmp_path),
        host={"kind": "codex"}, run_id="cached")
    cached = engine.prepare(
        "https://github.com/example/project.git", workspace=str(tmp_path),
        host={"kind": "codex"}, run_id="cached")
    assert first["status"] == cached["status"] == "ready"
    assert cached["target"]["fingerprint"] == first["target"]["fingerprint"]
    assert cached["revision"] == first["revision"]
    assert acquirer.calls == 1


@pytest.mark.parametrize("fixture", ["stale", "moved"])
def test_repository_preparation_reacquires_stale_or_moved_checkout(
        tmp_path, monkeypatch, fixture):
    monkeypatch.setenv("TASKPLANE_CONSOLIDATED_FLOW", "1")
    old = _acquired(tmp_path / "old-checkout")
    replacement = _acquired(tmp_path / f"{fixture}-checkout", head="d")
    acquirer = _SequenceAcquirer([old, replacement])
    engine = _repository_preflight(tmp_path, acquirer)
    first = engine.prepare(
        "https://github.com/example/project.git", workspace=str(tmp_path),
        host={"kind": "codex"}, run_id=fixture)
    Path(first["checkout"]).rename(tmp_path / "relocated-outside-cache")
    resumed = engine.prepare(
        "https://github.com/example/project.git", workspace=str(tmp_path),
        host={"kind": "codex"}, run_id=fixture)
    assert resumed["status"] == "ready"
    assert resumed["checkout"] == str((tmp_path / f"{fixture}-checkout"))
    assert resumed["target"]["head"] == "d" * 40
    assert resumed["target"]["fingerprint"] != first["target"]["fingerprint"]
    assert acquirer.calls == 2


def test_command_runtime_persists_recovery_and_detects_repeated_failure(tmp_path):
    runtime = CommandRuntime(str(tmp_path), workspace="workspace",
                             authorization="actor")
    handle = runtime.create(command_fingerprint="command", binding=None)
    first = runtime.record_recovery(
        handle, failure_class="artifact", detail="render unavailable")
    second = runtime.record_recovery(
        handle, failure_class="artifact", detail="render unavailable")
    assert first["status"] == "recover"
    assert second["status"] == "escalate"
    snapshot = runtime.snapshot(handle)
    assert [row["decision"]["reason"] for row in snapshot["recovery"]] == [
        "routine_retry", "repeated_fingerprint"
    ]
    assert "render unavailable" not in json.dumps(snapshot["recovery"])


def test_command_adapter_uses_recovery_policy_before_requesting_input(
        tmp_path, monkeypatch):
    monkeypatch.setenv("TASKPLANE_CONSOLIDATED_FLOW", "1")
    runtime = CommandRuntime(str(tmp_path), workspace="workspace",
                             authorization="actor")

    def unavailable(binding, timeout, interrupted):
        raise OSError("native bridge unavailable")

    adapter = CommandAdapter(
        host="codex", runtime=runtime,
        launcher=lambda command, cwd: HostLaunch(binding={"id": "native"}),
        native_wait=unavailable)
    handle = adapter.launch(["tool"], cwd=str(tmp_path))
    assert adapter.wait_next(handle, consumer="agent") is None
    event = adapter.wait_next(handle, consumer="agent")
    assert event["state"] == "input_required"
    assert event["reason"].endswith("repeated_fingerprint")


@pytest.mark.parametrize(
    ("kind", "status", "reason"),
    [
        ("authentication", "needs_user", "authority_required"),
        ("host-policy", "waiting", "host_policy"),
        ("external-unavailable", "waiting", "external_unavailable"),
    ],
)
def test_repository_genuine_boundaries_are_named_without_retry(kind, status,
                                                                reason):
    calls = 0

    def acquire():
        nonlocal calls
        calls += 1
        raise repository.RepositoryAcquisitionError(kind, "bounded detail")

    result = repository.acquire_with_recovery(acquire)
    assert result["status"] == status
    assert result["reason"] == reason
    assert calls == 1


def _plugin(root: Path, version: str, *, valid=True):
    root.mkdir(parents=True)
    (root / ".codex-plugin").mkdir()
    (root / "taskplane").mkdir()
    (root / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "name": "taskplane" if valid else "other", "version": version,
    }), encoding="utf-8")
    (root / "taskplane" / "tp.py").write_text("# engine\n", encoding="utf-8")


def _worktree(root: Path, *, name="main", launcher_at_root=True):
    worktree = root if name == "main" else root / ".tp-work" / name
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / ".git").write_text(
        f"gitdir: /managed/common/worktrees/{name}\n", encoding="utf-8")
    launcher_root = root if launcher_at_root else worktree
    (launcher_root / ".taskplane").mkdir(parents=True, exist_ok=True)
    launcher = launcher_root / ".taskplane" / "codex-hook.py"
    launcher.write_text("# launcher\n", encoding="utf-8")
    return worktree, launcher


def test_repository_family_continuity_selects_exact_worktree_and_latest_engine(
        tmp_path):
    family = tmp_path / "family"
    _plugin(family / "2.17.7", "2.17.7")
    _plugin(family / "2.17.8", "2.17.8")
    _plugin(family / "2.18.0", "broken", valid=False)
    worktree = tmp_path / "repo" / ".tp-work" / "sibling"
    nested = worktree / "src" / "pkg"
    nested.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: /managed/common/worktrees/sibling\n",
                                    encoding="utf-8")
    (worktree / ".taskplane").mkdir()
    launcher = worktree / ".taskplane" / "codex-hook.py"
    launcher.write_text("# launcher\n", encoding="utf-8")

    result = repository.resolve_worktree_continuity(
        str(nested), plugin_family=str(family))
    assert result["status"] == "ready"
    assert result["worktree"] == str(worktree.resolve())
    assert result["launcher"] == str(launcher.resolve())
    assert result["engine"] == str(
        (family / "2.17.8" / "taskplane" / "tp.py").resolve())


@pytest.mark.parametrize("location", ["root", "nested", "sibling"])
def test_repository_family_continuity_resolves_each_current_worktree_fixture(
        tmp_path, location):
    family = tmp_path / "family"
    _plugin(family / "2.17.8", "2.17.8")
    root, launcher = _worktree(tmp_path / "repo")
    sibling, _ = _worktree(root, name="sibling")
    if location == "root":
        current, expected = root, root
    elif location == "nested":
        current = root / "src" / "nested"
        current.mkdir(parents=True)
        expected = root
    else:
        current = sibling / "src" / "nested"
        current.mkdir(parents=True)
        expected = sibling
    result = repository.resolve_worktree_continuity(
        str(current), plugin_family=str(family))
    assert result["status"] == "ready"
    assert result["worktree"] == str(expected.resolve())
    assert result["launcher"] == str(launcher.resolve())


def test_repository_family_continuity_re_resolves_moved_worktree(tmp_path):
    family = tmp_path / "family"
    _plugin(family / "2.17.8", "2.17.8")
    old_root, _ = _worktree(tmp_path / "old-repo")
    old_result = repository.resolve_worktree_continuity(
        str(old_root), plugin_family=str(family))
    moved_root = tmp_path / "moved-repo"
    old_root.rename(moved_root)
    moved_result = repository.resolve_worktree_continuity(
        str(moved_root), plugin_family=str(family))
    assert old_result["worktree"] == str(old_root.resolve())
    assert moved_result["status"] == "ready"
    assert moved_result["worktree"] == str(moved_root.resolve())
    assert moved_result["launcher"] == str(
        (moved_root / ".taskplane" / "codex-hook.py").resolve())


def test_repository_family_continuity_falls_back_from_stale_engine(tmp_path):
    family = tmp_path / "family"
    _plugin(family / "2.17.7", "2.17.7")
    _plugin(family / "2.17.8", "2.17.8")
    (family / "2.17.8" / "taskplane" / "tp.py").unlink()
    root, _ = _worktree(tmp_path / "repo")
    result = repository.resolve_worktree_continuity(
        str(root), plugin_family=str(family))
    assert result["status"] == "ready"
    assert result["engine"] == str(
        (family / "2.17.7" / "taskplane" / "tp.py").resolve())


def test_repository_family_continuity_new_sibling_operates_without_restart(
        tmp_path):
    family = tmp_path / "family"
    _plugin(family / "2.17.8", "2.17.8")
    root, launcher = _worktree(tmp_path / "repo")
    sibling, _ = _worktree(root, name="created-after-session-start")
    current = sibling / "new" / "nested"
    current.mkdir(parents=True)
    result = repository.resolve_worktree_continuity(
        str(current), plugin_family=str(family))
    assert result == {
        "schema": "taskplane.worktree-continuity/v1",
        "status": "ready", "reason": "continuity_verified",
        "worktree": str(sibling.resolve()),
        "launcher": str(launcher.resolve()),
        "engine": str(
            (family / "2.17.8" / "taskplane" / "tp.py").resolve()),
    }


def test_repository_family_continuity_reports_policy_restricted_engine_family(
        tmp_path, monkeypatch):
    family = tmp_path / "family"
    family.mkdir()
    root, launcher = _worktree(tmp_path / "repo")
    real_listdir = repository.os.listdir

    def policy_listdir(path):
        if os.path.realpath(path) == str(family.resolve()):
            raise PermissionError("managed plugin policy")
        return real_listdir(path)

    monkeypatch.setattr(repository.os, "listdir", policy_listdir)
    result = repository.resolve_worktree_continuity(
        str(root), plugin_family=str(family))
    assert result == {
        "schema": "taskplane.worktree-continuity/v1",
        "status": "waiting_host_policy", "reason": "host_policy",
        "worktree": str(root.resolve()), "launcher": str(launcher.resolve()),
    }


@pytest.mark.parametrize("state", ["unavailable"])
def test_repository_family_continuity_returns_truthful_unavailable_states(
        tmp_path, state):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    family = tmp_path / "family"
    family.mkdir()
    result = repository.resolve_worktree_continuity(
        str(workspace), plugin_family=str(family))
    assert result["status"] in {"waiting_external", "waiting_host_policy"}
    assert result["reason"] in {
        "launcher_unavailable", "engine_unavailable", "host_policy",
    }


def test_storage_repository_family_root_tracks_sibling_worktree(tmp_path):
    root = tmp_path / "repo"
    sibling = root / ".tp-work" / "new"
    (root / ".taskplane").mkdir(parents=True)
    (root / ".taskplane" / "codex-hook.py").write_text("# bridge\n",
                                                        encoding="utf-8")
    sibling.mkdir(parents=True)
    (sibling / ".git").write_text("gitdir: /common/worktrees/new\n",
                                   encoding="utf-8")
    current = sibling / "nested"
    current.mkdir()
    resolved = storage.resolve_repository_family(str(current))
    assert resolved["worktree"] == str(sibling.resolve())
    assert resolved["launcher"] == str(
        (root / ".taskplane" / "codex-hook.py").resolve())
