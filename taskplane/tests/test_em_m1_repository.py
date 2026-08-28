from __future__ import annotations

import subprocess

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

