from __future__ import annotations

import subprocess

import preflight
import pytest
import repository


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def wall_time(self) -> float:
        return 1_800_000_000.0 + self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class _DefaultManager(repository.RepositoryManager):
    def __init__(self, *, home: str, acquired: repository.AcquisitionResult):
        super().__init__(home=home)
        self.acquired = acquired
        self.fetch_calls: list[list[str]] = []

    def _run(self, argv, *, cwd=None, timeout=600):
        del cwd, timeout
        self.fetch_calls.append(list(argv))
        if len(self.fetch_calls) == 1:
            raise repository.RepositoryAcquisitionError(
                "network", "RPC failed; HTTP 400 default preflight")
        return "fetched"

    def _acquire_repository_once(self, identity, target, **kwargs):
        del identity, target, kwargs
        self._fetch(["git", "fetch", "origin", "main"])
        return self.acquired


def test_m19_one_retry_owner_applies_bounded_backoff_and_deadline(
        monkeypatch, tmp_path):
    clock = _Clock()
    manager = repository.RepositoryManager(home=str(tmp_path))
    calls: list[list[str]] = []

    def run(argv, *, cwd=None, timeout=600):
        del cwd, timeout
        calls.append(list(argv))
        if len(calls) == 1:
            raise repository.RepositoryAcquisitionError(
                "network", "RPC failed; HTTP 400 curl 22")
        return "fetched"

    monkeypatch.setattr(manager, "_run", run)
    result = repository.acquire_with_recovery(
        lambda: manager._fetch(["git", "fetch", "origin", "main"]),
        deadline_seconds=10, base_backoff_seconds=2,
        max_backoff_seconds=4, monotonic=clock.monotonic,
        wall_time=clock.wall_time, sleep=clock.sleep,
        random_value=lambda: 0.5)

    assert result["status"] == "ready"
    assert result["attempts"] == 2
    assert len(calls) == 2
    assert calls[0] == ["git", "fetch", "origin", "main"]
    assert calls[1][:3] == ["git", "-c", "http.version=HTTP/1.1"]
    assert clock.sleeps == [1.0]
    assert result["attempt_telemetry"] == [
        {
            "attempt": 1,
            "started_after_seconds": 0.0,
            "duration_seconds": 0.0,
            "status": "failed",
            "failure_class": "network",
            "detail_fingerprint": result["attempt_telemetry"][0][
                "detail_fingerprint"],
            "backoff_seconds": 1.0,
            "backoff_source": "exponential_jitter",
            "retry_after_seconds": None,
        },
        {
            "attempt": 2,
            "started_after_seconds": 1.0,
            "duration_seconds": 0.0,
            "status": "ready",
        },
    ]

    timeout_clock = _Clock()
    observed_timeouts: list[float] = []

    def expire(argv, **kwargs):
        timeout = float(kwargs["timeout"])
        observed_timeouts.append(timeout)
        timeout_clock.value += timeout
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(repository.subprocess, "run", expire)
    timed = repository.acquire_with_recovery(
        lambda: repository.RepositoryManager(home=str(tmp_path))._run(
            ["git", "fetch", "origin"]),
        deadline_seconds=5, monotonic=timeout_clock.monotonic,
        wall_time=timeout_clock.wall_time, sleep=timeout_clock.sleep,
        random_value=lambda: 0.5)

    assert timed["status"] == "waiting"
    assert timed["reason"] == "acquisition_deadline"
    assert timed["attempts"] == 1
    assert observed_timeouts == [5.0]
    assert timeout_clock.sleeps == []


def test_m19_default_preflight_cannot_bypass_or_nest_retry_owner(
        monkeypatch, tmp_path):
    monkeypatch.delenv("TASKPLANE_CONSOLIDATED_FLOW", raising=False)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    acquired = repository.AcquisitionResult(
        checkout=str(checkout), base_ref="origin/main", base="a" * 40,
        head="a" * 40, merge_base="a" * 40, changed_files=(),
        metadata={"url": "https://github.com/example/project"})
    clock = _Clock()
    manager = _DefaultManager(
        home=str(tmp_path / "home"), acquired=acquired)
    original_recovery = repository.acquire_with_recovery
    owner_calls = 0

    def deterministic_owner(acquire, **kwargs):
        nonlocal owner_calls
        owner_calls += 1
        return original_recovery(
            acquire, deadline_seconds=10, base_backoff_seconds=2,
            max_backoff_seconds=4, monotonic=clock.monotonic,
            wall_time=clock.wall_time, sleep=clock.sleep,
            random_value=lambda: 0.5, **kwargs)

    monkeypatch.setattr(
        repository, "acquire_with_recovery", deterministic_owner)
    engine = preflight.RepositoryPreflight(
        home=str(tmp_path / "home"),
        tools_provider=lambda: {
            "git": {"present": True},
            "gh": {"present": False, "authenticated": False},
        }, acquirer=manager)
    result = engine.prepare(
        "https://github.com/example/project",
        workspace=str(tmp_path / "workspace"), host={"kind": "codex"},
        run_id="m19-default")

    assert result["status"] == "ready"
    assert owner_calls == 1
    assert clock.sleeps == [1.0]
    assert len(manager.fetch_calls) == 2
    assert manager.fetch_calls[0] == ["git", "fetch", "origin", "main"]
    assert manager.fetch_calls[1][:3] == [
        "git", "-c", "http.version=HTTP/1.1"]
    retry = result["target"]["metadata"]["repository_retry"]
    assert [row["status"] for row in retry["attempts"]] == [
        "failed", "ready"]

    # An explicit outer owner must suppress the manager's default owner, so a
    # future consolidated caller cannot recreate nested retry loops.
    outer = repository.acquire_with_recovery(
        lambda: manager.acquire_repository(None, {}))
    assert outer["status"] == "ready"
    assert owner_calls == 2
    assert len(manager.fetch_calls) == 3


def test_m19_direct_fetch_has_no_independent_retry(monkeypatch, tmp_path):
    manager = repository.RepositoryManager(home=str(tmp_path))
    calls: list[list[str]] = []

    def fail(argv, *, cwd=None, timeout=600):
        del cwd, timeout
        calls.append(list(argv))
        raise repository.RepositoryAcquisitionError(
            "network", "RPC failed; HTTP 400 direct fetch")

    monkeypatch.setattr(manager, "_run", fail)
    with pytest.raises(repository.RepositoryAcquisitionError):
        manager._fetch(["git", "fetch", "origin", "main"])
    assert calls == [["git", "fetch", "origin", "main"]]


def test_m19_retry_after_is_honored_and_cannot_escape_deadline():
    clock = _Clock()
    attempts = 0

    def throttled():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise repository.RepositoryAcquisitionError(
                "network", "provider throttled", retry_after=3)
        return "ready"

    result = repository.acquire_with_recovery(
        throttled, deadline_seconds=10, base_backoff_seconds=2,
        max_backoff_seconds=4, monotonic=clock.monotonic,
        wall_time=clock.wall_time, sleep=clock.sleep,
        random_value=lambda: 0.5)
    assert result["status"] == "ready"
    assert clock.sleeps == [3.0]
    assert result["attempt_telemetry"][0]["backoff_source"] == "retry_after"
    assert result["attempt_telemetry"][0]["retry_after_seconds"] == 3.0

    blocked_clock = _Clock()
    blocked_calls = 0

    def retry_after_deadline():
        nonlocal blocked_calls
        blocked_calls += 1
        raise repository.RepositoryAcquisitionError(
            "network", "Retry-After: 30")

    blocked = repository.acquire_with_recovery(
        retry_after_deadline, deadline_seconds=5,
        monotonic=blocked_clock.monotonic,
        wall_time=blocked_clock.wall_time, sleep=blocked_clock.sleep,
        random_value=lambda: 0.5)
    assert blocked["status"] == "waiting"
    assert blocked["reason"] == "acquisition_deadline"
    assert blocked_calls == 1
    assert blocked_clock.sleeps == []


def test_m19_success_persists_per_attempt_telemetry():
    clock = _Clock()
    acquired = repository.AcquisitionResult(
        checkout="/managed/checkout", base_ref="origin/main",
        base="a" * 40, head="a" * 40, merge_base="a" * 40,
        changed_files=(), metadata={"url": "https://example.invalid/repo"})
    result = repository.acquire_with_recovery(
        lambda: acquired, monotonic=clock.monotonic,
        wall_time=clock.wall_time, sleep=clock.sleep,
        random_value=lambda: 0.5)
    telemetry = result["value"].metadata["repository_retry"]
    assert telemetry["schema"] == "taskplane.repository-retry-telemetry/v1"
    assert telemetry["attempts"][0]["status"] == "ready"
