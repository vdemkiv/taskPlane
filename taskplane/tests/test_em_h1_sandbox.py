import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import review  # noqa: E402


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
        lambda selected: terminated.append(selected))
    monkeypatch.setattr(review.time, "monotonic", lambda: 100.0)

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
        lambda selected: terminated.append(selected))
    monkeypatch.setattr(review.time, "monotonic", lambda: 20.0)

    with pytest.raises(KeyboardInterrupt):
        review._run_validation_sandbox_git(
            ["git", "checkout", "--detach", "HEAD"], cwd=".",
            deadline=620.0, phase="checkout")

    assert terminated == [process]


@pytest.mark.parametrize(
    ("platform_name", "expected_actions"),
    [("posix", ["SIGTERM", "SIGKILL"]),
     ("nt", ["terminate", "kill"])],
)
def test_h34_kill_recovery_wait_is_bounded_and_fails_closed(
        monkeypatch, platform_name, expected_actions):
    class UnreapableProcess(_StuckProcess):
        def __init__(self):
            self.returncode = None
            self.wait_timeouts = []
            self.actions = []

        def wait(self, timeout=None):
            self.wait_timeouts.append(timeout)
            raise subprocess.TimeoutExpired(["git"], timeout)

        def terminate(self):
            self.actions.append("terminate")

        def kill(self):
            self.actions.append("kill")

    process = UnreapableProcess()
    monkeypatch.setattr(review.os, "name", platform_name)
    monkeypatch.setattr(
        review.subprocess, "Popen", lambda *args, **kwargs: process)
    if platform_name == "posix":
        monkeypatch.setattr(
            review.os, "killpg",
            lambda _pid, sig: process.actions.append(signal.Signals(sig).name))
    monkeypatch.setattr(review.time, "monotonic", lambda: 100.0)

    with pytest.raises(review._ValidationSandboxTimeout) as raised:
        review._run_validation_sandbox_git(
            ["git", "clone", "source", "target"], cwd=None,
            deadline=700.0, phase="clone")

    assert raised.value.reason_code == "sandbox_process_reap_timeout"
    assert raised.value.phase == "clone"
    assert process.wait_timeouts == [1.0, 1.0]
    assert all(timeout is not None for timeout in process.wait_timeouts)
    assert process.actions == expected_actions


def test_h34_process_tree_termination_escalates_to_group_kill(monkeypatch):
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
            self.returncode = -signal.SIGKILL

    sent = []
    monkeypatch.setattr(review.os, "killpg", lambda pid, sig: sent.append((pid, sig)))

    review._terminate_validation_sandbox_process_tree(Process())

    assert sent == [(991, signal.SIGTERM), (991, signal.SIGKILL)]


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
     ("sandbox_process_reap_timeout", 1.0)],
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
    root = Path(review._kernel_root(str(ws))) / "validation-sandbox"
    assert not list(root.glob(".prepare-*"))
    assert calls == ["resolve-head", "clone"]


def test_h34_blocked_untracked_copy_is_killed_cleaned_and_persisted(
        tmp_path, monkeypatch):
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
    monkeypatch.setattr(
        review.shutil, "copy2",
        lambda *_args, **_kwargs: pytest.fail(
            "untracked copy ran synchronously in the preparation process"))
    monkeypatch.setattr(
        review.os, "killpg", lambda pid, sig: sent.append((pid, sig)))

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
