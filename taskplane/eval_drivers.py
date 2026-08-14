"""Bounded native Claude and Codex transports for model-behaviour evals.

The compliance kernel hands both adapters the same canonical bytes.  This
module owns only transport: capability detection, a small secret-free child
environment, deadlines/cancellation, process-tree cleanup and normalized
outcomes.  A transport outcome is evidence about an *attempt*; it is never a
workflow-compliance verdict.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Callable, Mapping

SCHEMA = "taskplane.eval-driver/v2"
STATUSES = ("success", "failed", "capability_unavailable", "timeout",
            "cancelled")

_SAFE_ENV = ("PATH", "PYTHONPATH", "TMPDIR", "TEMP", "TMP", "SystemRoot",
             "COMSPEC", "PATHEXT", "LANG", "LC_ALL", "TZ", "TERM", "HOME",
             "CODEX_HOME", "CLAUDE_CONFIG_DIR", "PLUGIN_ROOT",
             "CLAUDE_PLUGIN_ROOT", "TASKPLANE_HOME",
             "TASKPLANE_ENFORCE_DISPATCH", "GIT_CONFIG_GLOBAL",
             "GIT_CONFIG_NOSYSTEM", "GIT_TERMINAL_PROMPT")
_SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY",
                   "APIKEY", "CREDENTIAL", "PRIVATE_KEY")
_HOOK_EVENTS = frozenset(("hook_screen", "hook_deny", "hook_allow",
                          "dispatch_observed", "screen_dispatch"))
DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024


def canonical_bytes(value) -> bytes:
    """Stable host-neutral JSON, including one terminating newline."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False)
            + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def secret_safe_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """A deliberately small model-process environment.

    Native CLIs authenticate through their existing user installation (for
    example an OS credential store).  API keys and arbitrary ambient values
    are not forwarded into a model-controlled process.
    """
    source = os.environ if source is None else source
    out = {}
    for name in _SAFE_ENV:
        value = source.get(name)
        if value is not None and not any(m in name.upper()
                                         for m in _SECRET_MARKERS):
            out[name] = str(value)
    return out


@dataclass(frozen=True)
class ProcessOutcome:
    status: str
    returncode: int | None
    stdout: bytes = b""
    stderr: bytes = b""
    duration_ms: int = 0
    pid: int | None = None
    terminated: bool = False
    output_truncated: bool = False
    reason: str | None = None


def _read_bounded(stream, limit: int) -> tuple[bytes, bool]:
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return stream.read(limit), size > limit


def _terminate_tree(proc: subprocess.Popen, grace_s: float = .2) -> bool:
    """Terminate the process group created for one host invocation."""
    if proc.poll() is not None:
        return False
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:  # pragma: no cover - exercised on Windows runners
            proc.terminate()
        try:
            proc.wait(timeout=max(0.01, grace_s))
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:  # pragma: no cover
                proc.kill()
            proc.wait(timeout=max(0.01, grace_s))
        return True
    except (OSError, ProcessLookupError, subprocess.SubprocessError):
        return proc.poll() is not None


def run_process(*, host: str, argv: list[str], input_bytes: bytes, cwd: str,
                env: Mapping[str, str] | None, timeout_s: float,
                cancel=None, max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
                poll_s: float = .02) -> ProcessOutcome:
    """Run one host CLI without a shell, with bounded time and output.

    Files rather than pipes prevent a child that writes heavily from
    deadlocking the parent and keep unbounded output out of memory.
    """
    del host  # intentionally absent from the process environment
    started = time.monotonic()
    if cancel is not None and cancel.is_set():
        return ProcessOutcome("cancelled", None, reason="cancelled before start")
    if not argv or not os.path.isabs(argv[0]) and shutil.which(argv[0]) is None:
        return ProcessOutcome("capability_unavailable", None,
                              reason=f"executable unavailable: {argv[0] if argv else '(empty)'}")
    if timeout_s <= 0:
        return ProcessOutcome("timeout", None, reason="deadline expired before start")
    with tempfile.TemporaryFile() as stdin_f, tempfile.TemporaryFile() as out_f, \
            tempfile.TemporaryFile() as err_f:
        stdin_f.write(input_bytes)
        stdin_f.seek(0)
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, env=secret_safe_env(env), stdin=stdin_f,
                stdout=out_f, stderr=err_f, shell=False,
                start_new_session=(os.name == "posix"), close_fds=True)
        except FileNotFoundError:
            return ProcessOutcome("capability_unavailable", None,
                                  reason=f"executable unavailable: {argv[0]}")
        except OSError as exc:
            return ProcessOutcome("failed", None, reason=str(exc))

        deadline = started + timeout_s
        state = None
        terminated = False
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                state = "cancelled"
                terminated = _terminate_tree(proc)
                break
            if time.monotonic() >= deadline:
                state = "timeout"
                terminated = _terminate_tree(proc)
                break
            time.sleep(min(poll_s, max(0.001, deadline - time.monotonic())))
        if proc.poll() is None:
            terminated = _terminate_tree(proc) or terminated
        try:
            returncode = proc.wait(timeout=.5)
        except subprocess.TimeoutExpired:  # defensive: tree cleanup failed
            terminated = _terminate_tree(proc, grace_s=.05) or terminated
            returncode = proc.poll()
        stdout, out_trunc = _read_bounded(out_f, max_output_bytes)
        stderr, err_trunc = _read_bounded(err_f, max_output_bytes)
        if state is None:
            state = "success" if returncode == 0 else "failed"
        return ProcessOutcome(
            state, returncode, stdout, stderr,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            pid=proc.pid, terminated=terminated,
            output_truncated=out_trunc or err_trunc,
            reason=(state if state in ("timeout", "cancelled") else None))


def hook_proof(trace_rows) -> dict:
    """A proof is an observed enforcement-boundary event, never prose."""
    for row in trace_rows or ():
        if not isinstance(row, dict):
            continue
        event = row.get("event")
        if event == "subagent_start" and \
                row.get("source") == "codex_session_store" and \
                row.get("host_observed") is True:
            return {"proved": True, "event": event, "host": "codex",
                    "ts": row.get("ts"),
                    "source": "codex_session_store"}
        if event in _HOOK_EVENTS:
            return {"proved": True, "event": event, "host": row.get("host"),
                    "ts": row.get("ts")}
    return {"proved": False, "event": None, "host": None, "ts": None}


def normalized_result(result: Mapping) -> dict:
    """Host-neutral outcome used by the Claude/Codex parity assertion."""
    stdout = str(result.get("stdout") or "").encode("utf-8", "replace")
    stderr = str(result.get("stderr") or "").encode("utf-8", "replace")
    return {
        "schema": "taskplane.eval-driver-result/v2",
        "status": result.get("status"), "attempted": result.get("attempted"),
        "returncode": result.get("returncode"),
        "canonical_input_sha256": result.get("canonical_input_sha256"),
        "canonical_input_bytes": result.get("canonical_input_bytes"),
        "stdout_sha256": digest(stdout), "stdout_bytes": len(stdout),
        "stderr_sha256": digest(stderr), "stderr_bytes": len(stderr),
        "output_truncated": bool(result.get("output_truncated")),
    }


class NativeAdapter:
    host = ""

    def __init__(self, *, executable: str, runner: Callable | None = None):
        self.executable = executable
        self.runner = runner or run_process

    def argv(self, cwd: str) -> list[str]:  # pragma: no cover - abstract seam
        raise NotImplementedError

    def run(self, manifest, *, cwd: str, timeout_s: float = 900,
            cancel=None, env=None) -> dict:
        body = manifest if isinstance(manifest, bytes) else canonical_bytes(manifest)
        resolved = (self.executable if os.path.isabs(self.executable)
                    else shutil.which(self.executable))
        if self.runner is run_process and not resolved:
            outcome = ProcessOutcome(
                "capability_unavailable", None,
                reason=f"{self.host} CLI {self.executable!r} is unavailable")
        else:
            argv = self.argv(cwd)
            if resolved:
                argv[0] = resolved
            try:
                outcome = self.runner(host=self.host, argv=argv,
                                      input_bytes=body, cwd=cwd,
                                      env=env or os.environ,
                                      timeout_s=timeout_s, cancel=cancel)
            except Exception as exc:
                outcome = ProcessOutcome("failed", None,
                                         reason=f"transport failed: {exc}")
        result = asdict(outcome)
        result.update({
            "schema": SCHEMA, "host": self.host,
            "attempted": outcome.pid is not None,
            "canonical_input_sha256": digest(body),
            "canonical_input_bytes": len(body),
            "transport": "stdin-json",
        })
        # Raw bytes do not belong in run.json; expose decoded bounded output
        # only to the recorder seam, which may store it as a separate artifact.
        result["stdout"] = outcome.stdout.decode("utf-8", "replace")
        result["stderr"] = outcome.stderr.decode("utf-8", "replace")
        return result


class ClaudeAdapter(NativeAdapter):
    host = "claude"

    def __init__(self, *, executable="claude", runner=None):
        super().__init__(executable=executable, runner=runner)

    def argv(self, cwd: str) -> list[str]:
        del cwd
        return [self.executable, "--print", "--output-format", "stream-json",
                "--verbose", "--no-session-persistence"]


class CodexAdapter(NativeAdapter):
    host = "codex"

    def __init__(self, *, executable="codex", runner=None):
        super().__init__(executable=executable, runner=runner)

    def argv(self, cwd: str) -> list[str]:
        return [self.executable, "exec", "--json", "--cd", cwd, "-"]


def adapter(host: str, **kw) -> NativeAdapter:
    if host == "claude":
        return ClaudeAdapter(**kw)
    if host == "codex":
        return CodexAdapter(**kw)
    raise ValueError(f"unsupported evaluation host {host!r}")
