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


def test_h34_process_tree_termination_escalates_to_group_kill(monkeypatch):
    class Process:
        pid = 991
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            if timeout is not None:
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


def test_h34_prepare_timeout_is_cleaned_persisted_and_retryable(
        tmp_path, monkeypatch):
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
            phase=phase, reason_code="sandbox_process_timeout",
            timeout_seconds=120.0)

    monkeypatch.setattr(review, "_run_validation_sandbox_git", run_git)

    with pytest.raises(review.ReviewKernelError, match="timed out.*clone"):
        review.prepare_review_validation_sandbox(
            str(ws), run_id=state["run_id"])

    persisted = review._load_state(str(ws), state["run_id"])
    detail = persisted["review_execution"]["dynamic_validation"]["detail"]
    assert persisted["review_execution"]["dynamic_validation"]["status"] == "failed"
    assert detail["reason_code"] == "sandbox_process_timeout"
    assert detail["phase"] == "clone"
    root = Path(review._kernel_root(str(ws))) / "validation-sandbox"
    assert not list(root.glob(".prepare-*"))
    assert calls == ["resolve-head", "clone"]


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
