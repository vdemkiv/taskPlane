import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import review  # noqa: E402


class _OsProxy:
    """Test-local platform seam that never mutates process-wide ``os.name``."""

    def __init__(self, name):
        self.name = name

    def __getattr__(self, attribute):
        return getattr(os, attribute)


def _set_review_platform(monkeypatch, name):
    proxy = _OsProxy(name)
    monkeypatch.setattr(review, "os", proxy)
    return proxy


def _posix_signal_fixture(monkeypatch):
    """Provide POSIX-only signal identities on non-POSIX test hosts."""
    terminate = int(signal.SIGTERM)
    hard_kill = int(getattr(signal, "SIGKILL", 9))
    monkeypatch.setattr(
        review.signal, "SIGKILL", hard_kill, raising=False)
    return {terminate: "SIGTERM", hard_kill: "SIGKILL"}, hard_kill


def _bind_short_validation_kernel(tmp_path, monkeypatch):
    """Keep Windows Git clone fixtures below the legacy path-length bound."""
    base = tmp_path
    if os.name == "nt":
        base = tmp_path.parent / f"k-{tmp_path.name[-8:]}"
    kernel = base / "k"
    monkeypatch.setattr(review, "_kernel_root", lambda _ws: str(kernel))
    return kernel


class _StuckProcess:
    pid = 4312
    returncode = None

    def communicate(self, input=None, timeout=None):
        del input
        raise subprocess.TimeoutExpired(["git", "clone"], timeout)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        del timeout
        self.returncode = -signal.SIGTERM
        return self.returncode


@pytest.mark.parametrize(
    ("remaining", "reason_code", "expected_timeout"),
    [(600.0, "sandbox_process_timeout", 120.0),
     (7.0, "sandbox_preparation_timeout", 7.0)],
)
def test_h34_git_process_uses_process_and_total_deadlines(
        monkeypatch, remaining, reason_code, expected_timeout):
    process = _StuckProcess()
    launched = []
    terminated = []
    monkeypatch.setattr(review.subprocess, "Popen", lambda *args, **kwargs: (
        launched.append((args, kwargs)) or process))
    monkeypatch.setattr(
        review, "_terminate_validation_sandbox_process_tree",
        lambda selected, **_deadlines: terminated.append(selected))
    monkeypatch.setattr(review.time, "monotonic", lambda: 100.0)
    _set_review_platform(monkeypatch, "posix")

    with pytest.raises(review._ValidationSandboxTimeout) as raised:
        review._run_validation_sandbox_git(
            ["git", "clone", "source", "target"], cwd=None,
            deadline=100.0 + remaining, phase="clone")

    assert raised.value.reason_code == reason_code
    assert raised.value.phase == "clone"
    assert launched[0][1]["start_new_session"] is True
    assert process is terminated[0]
    assert raised.value.timeout_seconds == expected_timeout


def test_h34_cancellation_terminates_the_complete_process_tree(monkeypatch):
    class CancelledProcess(_StuckProcess):
        def communicate(self, input=None, timeout=None):
            del input, timeout
            raise KeyboardInterrupt

    process = CancelledProcess()
    terminated = []
    monkeypatch.setattr(
        review.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        review, "_terminate_validation_sandbox_process_tree",
        lambda selected, **_deadlines: terminated.append(selected))
    monkeypatch.setattr(review.time, "monotonic", lambda: 20.0)
    _set_review_platform(monkeypatch, "posix")

    with pytest.raises(KeyboardInterrupt):
        review._run_validation_sandbox_git(
            ["git", "checkout", "--detach", "HEAD"], cwd=".",
            deadline=620.0, phase="checkout")

    assert terminated == [process]


@pytest.mark.parametrize(
    ("platform_name", "shared_budget", "operation_budget", "expected_actions",
     "expected_waits"),
    [("posix", 10.0, 4.0, ["SIGTERM", "SIGKILL"], [1.0, 0.5]),
     ("nt", 3.0, 4.0, ["job-terminate", "job-close"], [1.0])],
)
def test_h34_cleanup_shares_the_aggregate_deadline_and_fails_closed(
        monkeypatch, platform_name, shared_budget, operation_budget,
        expected_actions, expected_waits):
    class Clock:
        def __init__(self):
            self.now = 100.0

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    clock = Clock()

    class UnreapableProcess(_StuckProcess):
        def __init__(self):
            self.returncode = None
            self.communicate_timeout = None
            self.wait_timeouts = []
            self.actions = []

        def communicate(self, input=None, timeout=None):
            del input
            self.communicate_timeout = timeout
            clock.advance(timeout - 1.5)
            raise subprocess.TimeoutExpired(["git"], timeout)

        def wait(self, timeout=None):
            self.wait_timeouts.append(timeout)
            clock.advance(timeout)
            raise subprocess.TimeoutExpired(["git"], timeout)

        def terminate(self):
            self.actions.append("terminate")

        def kill(self):
            self.actions.append("kill")

    process = UnreapableProcess()

    class Job:
        def terminate(self):
            process.actions.append("job-terminate")

        def close(self):
            process.actions.append("job-close")

    _set_review_platform(monkeypatch, platform_name)
    monkeypatch.setattr(
        review.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        review, "_VALIDATION_SANDBOX_PROCESS_TIMEOUT_SECONDS",
        operation_budget)
    if platform_name == "posix":
        signal_names, _hard_kill = _posix_signal_fixture(monkeypatch)
        monkeypatch.setattr(
            review.os, "killpg",
            lambda _pid, sig: process.actions.append(signal_names[int(sig)]),
            raising=False)
    else:
        def create_job(selected):
            assert selected is process
            process.actions.append("assign-job")
            return Job()

        monkeypatch.setattr(
            review, "_create_windows_validation_sandbox_job",
            create_job)
        monkeypatch.setattr(
            review, "_resume_windows_validation_sandbox_process",
            lambda selected: process.actions.append("resume"))
    monkeypatch.setattr(review.time, "monotonic", clock)

    with pytest.raises(review._ValidationSandboxTimeout) as raised:
        review._run_validation_sandbox_git(
            ["git", "clone", "source", "target"], cwd=None,
            deadline=100.0 + shared_budget, phase="clone")

    assert raised.value.reason_code == "sandbox_process_reap_timeout"
    assert raised.value.phase == "clone"
    assert raised.value.timeout_seconds == pytest.approx(1.5)
    assert process.communicate_timeout == min(shared_budget, operation_budget)
    assert process.wait_timeouts == pytest.approx(expected_waits)
    assert process.actions == (
        (["assign-job", "resume"] if platform_name == "nt" else []) +
        expected_actions)
    assert clock.now - 100.0 <= min(shared_budget, operation_budget)


def test_h34_windows_launch_owns_and_closes_a_kill_on_close_job(monkeypatch):
    class Process:
        pid = 8841
        returncode = None

        def communicate(self, input=None, timeout=None):
            del input, timeout
            self.returncode = 0
            return b"ready", b""

        def poll(self):
            return self.returncode

    process = Process()
    launched = []
    actions = []

    class Job:
        def terminate(self):
            actions.append("terminate")

        def close(self):
            actions.append("close")

    _set_review_platform(monkeypatch, "nt")
    monkeypatch.setattr(
        review.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200,
        raising=False)
    monkeypatch.setattr(
        review.subprocess, "Popen", lambda *args, **kwargs: (
            launched.append((args, kwargs)) or process))
    def create_job(selected):
        assert selected is process
        actions.append("assign-job")
        return Job()

    monkeypatch.setattr(
        review, "_create_windows_validation_sandbox_job", create_job)
    monkeypatch.setattr(
        review, "_resume_windows_validation_sandbox_process",
        lambda selected: actions.append("resume"))

    result = review._run_validation_sandbox_git(
        ["git", "status"], cwd=None,
        deadline=review.time.monotonic() + 10.0, phase="status")

    assert result.returncode == 0
    assert launched[0][1]["start_new_session"] is False
    assert launched[0][1]["creationflags"] == (
        0x200 | review._WINDOWS_CREATE_SUSPENDED)
    assert actions == ["assign-job", "resume", "close"]
    assert process._taskplane_validation_job is None


def test_h34_windows_job_assignment_failure_aborts_suspended_child(
        monkeypatch):
    class Process:
        pid = 8842
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 1

        def wait(self, timeout=None):
            del timeout
            return self.returncode

    process = Process()
    launched = []
    resumed = []
    _set_review_platform(monkeypatch, "nt")
    monkeypatch.setattr(
        review.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200,
        raising=False)
    monkeypatch.setattr(
        review.subprocess, "Popen", lambda *args, **kwargs: (
            launched.append((args, kwargs)) or process))
    monkeypatch.setattr(
        review, "_create_windows_validation_sandbox_job",
        lambda selected: (_ for _ in ()).throw(OSError("job refused")))
    monkeypatch.setattr(
        review, "_resume_windows_validation_sandbox_process",
        lambda selected: resumed.append(selected))

    with pytest.raises(OSError, match="job refused"):
        review._run_validation_sandbox_git(
            ["git", "status"], cwd=None,
            deadline=review.time.monotonic() + 10.0, phase="status")

    assert launched[0][1]["creationflags"] & \
        review._WINDOWS_CREATE_SUSPENDED
    assert process.returncode == 1
    assert resumed == []


def test_h34_cleanup_without_reap_time_fails_closed_without_overrun(
        monkeypatch):
    class Clock:
        def __init__(self):
            self.now = 20.0

        def __call__(self):
            return self.now

    clock = Clock()

    class ExhaustedProcess(_StuckProcess):
        def __init__(self):
            self.returncode = None
            self.actions = []
            self.wait_timeouts = []

        def communicate(self, input=None, timeout=None):
            del input
            clock.now += timeout
            raise subprocess.TimeoutExpired(["git"], timeout)

        def wait(self, timeout=None):
            self.wait_timeouts.append(timeout)
            raise AssertionError("cleanup waited after its deadline")

    process = ExhaustedProcess()
    signal_names, _hard_kill = _posix_signal_fixture(monkeypatch)
    _set_review_platform(monkeypatch, "posix")
    monkeypatch.setattr(
        review.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        review.os, "killpg",
        lambda _pid, sig: process.actions.append(signal_names[int(sig)]),
        raising=False)
    monkeypatch.setattr(review.time, "monotonic", clock)

    with pytest.raises(review._ValidationSandboxTimeout) as raised:
        review._run_validation_sandbox_git(
            ["git", "clone", "source", "target"], cwd=None,
            deadline=22.0, phase="clone")

    assert raised.value.reason_code == "sandbox_process_reap_timeout"
    assert raised.value.timeout_seconds == 0.0
    assert process.wait_timeouts == []
    assert process.actions == ["SIGTERM", "SIGKILL"]
    assert clock.now == 22.0


def test_h34_process_tree_termination_escalates_to_group_kill(monkeypatch):
    _signal_names, hard_kill = _posix_signal_fixture(monkeypatch)

    class Process:
        pid = 991
        returncode = None
        wait_calls = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired(["git"], timeout)
            self.returncode = -hard_kill

    sent = []
    _set_review_platform(monkeypatch, "posix")
    monkeypatch.setattr(
        review.os, "killpg", lambda pid, sig: sent.append((pid, sig)),
        raising=False)

    deadline = review.time.monotonic() + 10.0
    review._terminate_validation_sandbox_process_tree(
        Process(), shared_deadline=deadline, operation_deadline=deadline)

    assert sent == [(991, signal.SIGTERM), (991, hard_kill)]


def _git_review_state(ws: Path) -> tuple[dict, str]:
    subprocess.run(["git", "init"], cwd=ws, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (ws / "service.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "service.py"], cwd=ws, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-m", "fixture"], cwd=ws, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ws, check=True,
        stdout=subprocess.PIPE, text=True, encoding="utf-8").stdout.strip()
    run_id = "a" * 32
    selected = review.review_execution_preflight(
        selection="dynamic", run_id=run_id, decided_by="human")
    state = {
        "run_id": run_id, "status": "ready", "stage": "review",
        "target": {"head": head, "fingerprint": "target-1"},
        "review_execution": selected,
        "manifest": {"schema": "taskplane.review-manifest/v2"},
    }
    review._save_state(str(ws), state)
    return state, head


@pytest.mark.parametrize(
    ("reason_code", "timeout_seconds"),
    [("sandbox_process_timeout", 120.0),
     ("sandbox_process_reap_timeout", 1.0),
     ("sandbox_process_reap_timeout", 0.0)],
)
def test_h34_prepare_timeout_is_cleaned_persisted_and_retryable(
        tmp_path, monkeypatch, reason_code, timeout_seconds):
    ws = tmp_path / "repo"
    ws.mkdir()
    state, head = _git_review_state(ws)
    calls = []

    def run_git(argv, *, cwd, deadline, phase, input=None, text=False):
        del cwd, deadline, input
        calls.append(phase)
        if phase == "resolve-head":
            return subprocess.CompletedProcess(argv, 0, head + "\n", "")
        raise review._ValidationSandboxTimeout(
            phase=phase, reason_code=reason_code,
            timeout_seconds=timeout_seconds)

    monkeypatch.setattr(review, "_run_validation_sandbox_git", run_git)

    with pytest.raises(review.ReviewKernelError, match="timed out.*clone"):
        review.prepare_review_validation_sandbox(
            str(ws), run_id=state["run_id"])

    persisted = review._load_state(str(ws), state["run_id"])
    detail = persisted["review_execution"]["dynamic_validation"]["detail"]
    assert persisted["review_execution"]["dynamic_validation"]["status"] == "failed"
    assert detail["reason_code"] == reason_code
    assert detail["phase"] == "clone"
    assert detail["timeout_seconds"] == timeout_seconds
    root = Path(review._kernel_root(str(ws))) / "validation-sandbox"
    assert not list(root.glob(".prepare-*"))
    assert calls == ["resolve-head", "clone"]


def test_h34_blocked_untracked_copy_is_killed_cleaned_and_persisted(
        tmp_path, monkeypatch):
    _bind_short_validation_kernel(tmp_path, monkeypatch)
    ws = tmp_path / "repo"
    ws.mkdir()
    state, _head = _git_review_state(ws)
    (ws / "untracked.bin").write_bytes(b"payload")

    class BlockedCopyProcess(_StuckProcess):
        pid = 5521

        def __init__(self):
            self.returncode = None
            self.timeout = None

        def communicate(self, input=None, timeout=None):
            del input
            self.timeout = timeout
            raise subprocess.TimeoutExpired([sys.executable, "-c"], timeout)

    process = BlockedCopyProcess()
    original_popen = review.subprocess.Popen
    launched = []
    sent = []

    def popen(argv, *args, **kwargs):
        if argv and argv[0] == sys.executable and not launched:
            launched.append((argv, kwargs))
            Path(argv[-1]).write_bytes(b"partial")
            return process
        return original_popen(argv, *args, **kwargs)

    monkeypatch.setattr(review.subprocess, "Popen", popen)
    _set_review_platform(monkeypatch, "posix")
    monkeypatch.setattr(
        review.shutil, "copy2",
        lambda *_args, **_kwargs: pytest.fail(
            "untracked copy ran synchronously in the preparation process"))
    monkeypatch.setattr(
        review.os, "killpg", lambda pid, sig: sent.append((pid, sig)),
        raising=False)

    with pytest.raises(review.ReviewKernelError, match="timed out.*copy-untracked"):
        review.prepare_review_validation_sandbox(
            str(ws), run_id=state["run_id"])

    persisted = review._load_state(str(ws), state["run_id"])
    detail = persisted["review_execution"]["dynamic_validation"]["detail"]
    assert detail["reason_code"] == "sandbox_process_timeout"
    assert detail["phase"] == "copy-untracked"
    assert 0 < process.timeout <= 120
    assert process.returncode is not None
    assert sent == [(process.pid, signal.SIGTERM)]
    assert launched[0][1]["start_new_session"] is True
    root = Path(review._kernel_root(str(ws))) / "validation-sandbox"
    assert not list(root.glob(".prepare-*"))

    sandbox = review.prepare_review_validation_sandbox(
        str(ws), run_id=state["run_id"])
    assert (Path(sandbox["path"]) / "untracked.bin").read_bytes() == b"payload"
    assert not list(root.glob(".prepare-*"))


def test_h34_sandbox_prepare_has_process_and_total_deadlines(
        tmp_path, monkeypatch):
    _bind_short_validation_kernel(tmp_path, monkeypatch)
    ws = tmp_path / "repo"
    ws.mkdir()
    state, _head = _git_review_state(ws)
    phases = []
    real_runner = review._run_validation_sandbox_git

    def observed_runner(argv, *, cwd, deadline, phase, input=None, text=False):
        phases.append((phase, deadline - review.time.monotonic()))
        return real_runner(
            argv, cwd=cwd, deadline=deadline, phase=phase,
            input=input, text=text)

    monkeypatch.setattr(review, "_run_validation_sandbox_git", observed_runner)

    sandbox = review.prepare_review_validation_sandbox(
        str(ws), run_id=state["run_id"])

    assert Path(sandbox["path"]).is_dir()
    assert sandbox["push_disabled"] is True
    assert [phase for phase, _remaining in phases] == [
        "resolve-head", "clone", "checkout", "diff", "list-untracked",
        "disable-push", "configure-hooks",
    ]
    assert all(0 < remaining <= 600 for _phase, remaining in phases)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows jobs")
def test_h34_windows_timeout_kills_child_and_grandchild(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    pid_file = tmp_path / "tree.pids"
    parent_script = (
        "import os, pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(30)'], "
        "creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)\n"
        f"pathlib.Path({str(pid_file)!r}).write_text("
        "f'{os.getpid()} {child.pid}', encoding='ascii')\n"
        "time.sleep(30)\n"
    )
    monkeypatch.setattr(
        review, "_VALIDATION_SANDBOX_PROCESS_TIMEOUT_SECONDS", 1.5)

    with pytest.raises(review._ValidationSandboxTimeout):
        review._run_validation_sandbox_git(
            [sys.executable, "-c", parent_script], cwd=None,
            deadline=review.time.monotonic() + 10.0, phase="windows-tree")

    parent_pid, child_pid = (
        int(value) for value in pid_file.read_text(encoding="ascii").split())
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    def is_running(pid):
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(handle)

    stop = review.time.monotonic() + 5.0
    while any(is_running(pid) for pid in (parent_pid, child_pid)) and \
            review.time.monotonic() < stop:
        review.time.sleep(0.05)
    assert not is_running(parent_pid)
    assert not is_running(child_pid)
