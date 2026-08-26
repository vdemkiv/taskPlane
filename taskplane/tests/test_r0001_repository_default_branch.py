"""R-0001 hosted repository default-branch preparation contract."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from delivery_ports import GitResult, SubprocessGitRunner  # noqa: E402
import preflight  # noqa: E402
import repository  # noqa: E402


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=cwd, text=True, stderr=subprocess.STDOUT
    ).strip()


def _local_bare_remote(tmp_path: Path, *, default_branch: str) -> tuple[Path, str]:
    source = tmp_path / "source"
    remote = tmp_path / "origin.git"
    source.mkdir()
    _git("init", "-q", cwd=source)
    _git("config", "user.name", "R-0001", cwd=source)
    _git("config", "user.email", "r0001@example.invalid", cwd=source)
    (source / "README.md").write_text("verified default\n", encoding="utf-8")
    _git("add", "README.md", cwd=source)
    _git("commit", "-qm", "default", cwd=source)
    _git("branch", "-M", default_branch, cwd=source)
    sha = _git("rev-parse", "HEAD", cwd=source)
    _git("init", "-q", "--bare", str(remote))
    _git("remote", "add", "origin", str(remote), cwd=source)
    _git("push", "-q", "-u", "origin", default_branch, cwd=source)
    _git("symbolic-ref", "HEAD", f"refs/heads/{default_branch}", cwd=remote)
    return remote, sha


class _NoBareHeadRunner:
    """Real Git runner that rejects the historical severed edge."""

    def __init__(self) -> None:
        self.delegate = SubprocessGitRunner()
        self.calls: list[tuple[str, ...]] = []

    def run(self, args, *, cwd=None) -> GitResult:
        call = tuple(args)
        self.calls.append(call)
        if len(call) >= 4 and call[-2:] == ("rev-parse", "HEAD"):
            raise AssertionError("bare HEAD was dereferenced")
        return self.delegate.run(args, cwd=cwd)


class _InterceptRunner(_NoBareHeadRunner):
    def __init__(self, *, advertisement: str | None = None,
                 fail_fetch: bool = False) -> None:
        super().__init__()
        self.advertisement = advertisement
        self.fail_fetch = fail_fetch

    def run(self, args, *, cwd=None) -> GitResult:
        call = tuple(args)
        if "fetch" in call and self.fail_fetch:
            self.calls.append(call)
            self.fail_fetch = False
            return GitResult(1, "", "temporary failure")
        if call[-4:] == ("ls-remote", "--symref", "origin", "HEAD") and \
                self.advertisement is not None:
            self.calls.append(call)
            return GitResult(0, self.advertisement, "")
        return super().run(args, cwd=cwd)


def _request(remote: Path) -> dict:
    return {
        "schema": "taskplane.repository-preparation-request/v1",
        "operation_id": "prepare-default-1",
        "run_id": "run-default-1",
        "target": {
            "kind": "repository",
            "repository_id": "fixture/example/project",
            "remote": str(remote),
            "requested_ref": None,
        },
        "workspace_locator_fingerprint": "a" * 64,
        "attempt": 1,
        "predecessor_result_fingerprint": None,
    }


def test_non_master_default_branch_survives_severed_bare_head(tmp_path):
    remote, expected_sha = _local_bare_remote(
        tmp_path, default_branch="trunk")
    mirror = tmp_path / "mirror.git"
    worktrees = tmp_path / "worktrees"
    _git("init", "-q", "--bare", str(mirror))
    _git("remote", "add", "origin", str(remote), cwd=mirror)
    _git("symbolic-ref", "HEAD", "refs/heads/master", cwd=mirror)
    runner = _NoBareHeadRunner()

    result = repository.prepare(
        _request(remote), mirror_path=str(mirror),
        worktree_root=str(worktrees), git_runner=runner)

    assert result["status"] == "ready"
    assert result["remote_default_branch"] == "trunk"
    assert result["remote_default_ref"] == "refs/remotes/origin/trunk"
    assert result["resolved_sha"] == expected_sha
    assert _git("rev-parse", "HEAD", cwd=Path(result["checkout"])) == expected_sha
    assert _git("symbolic-ref", "HEAD", cwd=mirror) == \
        "refs/remotes/origin/trunk"
    assert not any(call[-2:] == ("rev-parse", "HEAD") for call in runner.calls)


def test_hosted_repository_resolves_fetched_remote_default_before_head(tmp_path):
    remote, expected_sha = _local_bare_remote(tmp_path, default_branch="main")
    mirror = tmp_path / "mirror.git"
    _git("init", "-q", "--bare", str(mirror))
    _git("remote", "add", "origin", str(remote), cwd=mirror)
    runner = _NoBareHeadRunner()

    result = repository.prepare(
        _request(remote), mirror_path=str(mirror),
        worktree_root=str(tmp_path / "worktrees"), git_runner=runner)

    assert result["status"] == "ready"
    assert result["resolved_sha"] == expected_sha
    commands = [" ".join(call) for call in runner.calls]
    fetch_index = next(i for i, call in enumerate(commands)
                       if " fetch " in f" {call} ")
    advertise_index = next(i for i, call in enumerate(commands)
                           if "ls-remote --symref origin HEAD" in call)
    verify_index = next(i for i, call in enumerate(commands)
                        if "show-ref --verify --hash" in call)
    bind_index = next(i for i, call in enumerate(commands)
                      if "symbolic-ref HEAD refs/remotes/origin/main" in call)
    resolve_index = next(i for i, call in enumerate(commands)
                         if "rev-parse --verify "
                         "refs/remotes/origin/main^{commit}" in call)
    checkout_index = next(i for i, call in enumerate(commands)
                          if "worktree add --detach" in call)
    assert fetch_index < advertise_index < verify_index < bind_index \
        < resolve_index < checkout_index


@pytest.mark.parametrize(
    ("advertisement", "reason_code"),
    [
        ("", "remote_default_missing"),
        (
            "ref: refs/heads/main\tHEAD\n"
            "ref: refs/heads/trunk\tHEAD\n"
            f"{'1' * 40}\tHEAD\n",
            "remote_default_ambiguous",
        ),
        ("ref: malformed\tHEAD\n" + f"{'1' * 40}\tHEAD\n",
         "remote_default_ambiguous"),
        ("ref: refs/heads/ghost\tHEAD\n" + f"{'1' * 40}\tHEAD\n",
         "default_ref_unfetched"),
    ],
)
def test_missing_or_ambiguous_remote_default_branch_fails_closed(
        tmp_path, advertisement, reason_code):
    remote, _ = _local_bare_remote(tmp_path, default_branch="main")
    mirror = tmp_path / "mirror.git"
    _git("init", "-q", "--bare", str(mirror))
    _git("remote", "add", "origin", str(remote), cwd=mirror)
    runner = _InterceptRunner(advertisement=advertisement)

    result = repository.prepare(
        _request(remote), mirror_path=str(mirror),
        worktree_root=str(tmp_path / "worktrees"), git_runner=runner)

    assert result["status"] == "refused"
    assert result["reason_code"] == reason_code
    assert result["retryability"] == "change_request"
    assert result["checkout"] is None
    assert not any("symbolic-ref HEAD refs/remotes" in " ".join(call)
                   for call in runner.calls)


def test_repository_preparation_request_and_result_are_closed(tmp_path):
    remote, _ = _local_bare_remote(tmp_path, default_branch="main")
    bad_request = {**_request(remote), "unexpected": True}
    runner = _NoBareHeadRunner()

    refused = repository.prepare(
        bad_request, mirror_path=str(tmp_path / "mirror.git"),
        worktree_root=str(tmp_path / "worktrees"), git_runner=runner)

    assert refused["reason_code"] == "invalid_request"
    assert runner.calls == []
    assert set(refused) == repository.REPOSITORY_PREPARATION_RESULT_FIELDS
    with pytest.raises(repository.RepositoryAcquisitionError):
        repository.validate_repository_preparation_result(
            {**refused, "unexpected": True})


def test_repository_refusal_identity_is_stable_and_retryability_exact(tmp_path):
    remote, _ = _local_bare_remote(tmp_path, default_branch="main")
    mirror = tmp_path / "mirror.git"
    _git("init", "-q", "--bare", str(mirror))
    _git("remote", "add", "origin", str(remote), cwd=mirror)

    first = repository.prepare(
        _request(remote), mirror_path=str(mirror),
        worktree_root=str(tmp_path / "worktrees-a"),
        git_runner=_InterceptRunner(advertisement=""))
    second = repository.prepare(
        _request(remote), mirror_path=str(mirror),
        worktree_root=str(tmp_path / "worktrees-b"),
        git_runner=_InterceptRunner(advertisement=""))

    assert first["refusal_identity"] == second["refusal_identity"]
    assert first["retryability"] == "change_request"
    assert first["fingerprint"] == second["fingerprint"]


def test_repository_retry_is_idempotent_and_predecessor_bound(tmp_path):
    remote, expected_sha = _local_bare_remote(tmp_path, default_branch="main")
    mirror = tmp_path / "mirror.git"
    _git("init", "-q", "--bare", str(mirror))
    _git("remote", "add", "origin", str(remote), cwd=mirror)
    first_runner = _InterceptRunner(fail_fetch=True)
    first = repository.prepare(
        _request(remote), mirror_path=str(mirror),
        worktree_root=str(tmp_path / "worktrees"), git_runner=first_runner)
    assert first["status"] == "waiting"
    assert first["retryability"] == "retry_after_external"

    retry = {
        **_request(remote),
        "attempt": 2,
        "predecessor_result_fingerprint": first["fingerprint"],
    }
    second_runner = _NoBareHeadRunner()
    ready = repository.prepare(
        retry, mirror_path=str(mirror),
        worktree_root=str(tmp_path / "worktrees"), git_runner=second_runner,
        prior_result=first)
    assert ready["status"] == "ready"
    assert ready["resolved_sha"] == expected_sha

    replay_runner = _NoBareHeadRunner()
    replay = repository.prepare(
        retry, mirror_path=str(mirror),
        worktree_root=str(tmp_path / "worktrees"), git_runner=replay_runner,
        prior_result=ready)
    assert replay == ready
    assert replay_runner.calls == []

    bad_retry = {**retry, "predecessor_result_fingerprint": "f" * 64}
    invalid = repository.prepare(
        bad_retry, mirror_path=str(mirror),
        worktree_root=str(tmp_path / "worktrees"), git_runner=_NoBareHeadRunner(),
        prior_result=first)
    assert invalid["status"] == "refused"
    assert invalid["reason_code"] == "invalid_request"


def test_repository_manager_uses_verified_preparation_receipt(tmp_path):
    remote, expected_sha = _local_bare_remote(tmp_path, default_branch="trunk")
    identity = repository.storage.RepositoryIdentity(
        repo_id="fixture/example/project", kind="hosted", host="fixture",
        owner="example", name="project", remote=str(remote))

    class _LocalManager(repository.RepositoryManager):
        @staticmethod
        def _remote_url(_identity):
            return str(remote)

    runner = _NoBareHeadRunner()
    manager = _LocalManager(home=str(tmp_path / "home"), git_runner=runner)
    layout = repository.storage.resolve_layout(
        identity, home=str(tmp_path / "home"), run_id="acquisition")
    manager._ensure_mirror(identity, layout)
    _git("symbolic-ref", "HEAD", "refs/heads/master",
         cwd=Path(layout.mirror_path))

    with mock.patch.object(manager, "_ensure_mirror", return_value=None):
        acquired = manager.acquire_repository(
            identity, {"kind": "repository", "spec": str(remote)},
            run_id="managed-default")

    receipt = acquired.metadata["repository_preparation"]
    assert receipt["status"] == "ready"
    assert receipt["remote_default_branch"] == "trunk"
    assert acquired.head == expected_sha
    assert acquired.checkout == receipt["checkout"]


def test_preflight_binds_hosted_preparation_to_the_resumable_run(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git("init", "-q", cwd=checkout)
    calls = []

    class _BoundManager(repository.RepositoryManager):
        def acquire_repository(self, identity, target, **kwargs):
            calls.append(kwargs)
            return repository.AcquisitionResult(
                checkout=str(checkout),
                base_ref="refs/remotes/origin/main", base="b" * 40,
                head="b" * 40, merge_base="b" * 40, changed_files=(),
                metadata={"repository_preparation": {
                    "schema": "taskplane.repository-preparation/v1",
                    "status": "ready",
                }})

    engine = preflight.RepositoryPreflight(
        home=str(tmp_path / "home"),
        tools_provider=lambda: {
            "git": {"present": True},
            "gh": {"present": True, "authenticated": True},
        },
        acquirer=_BoundManager(home=str(tmp_path / "home")))

    result = engine.prepare(
        "https://github.com/Example/Project.git",
        workspace=str(tmp_path), host={"kind": "codex"},
        run_id="run-preparation-binding")

    assert result["status"] == "ready"
    assert calls == [{"run_id": "run-preparation-binding"}]
    assert result["target"]["metadata"]["repository_preparation"]["status"] \
        == "ready"
