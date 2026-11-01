"""Conservative verification reuse with source, runtime and command provenance.

These records preserve observations, including failures. They never carry gates.
"""
from __future__ import annotations

import codecs
import hashlib
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, BinaryIO

from . import depgraph, primitives, workflow as w, workflow_evidence as evidence
from .context import Store, digest, encode

MAX_OUTPUT_BYTES = 1024 * 1024

ENVIRONMENT = ("PATH", "LANG", "LC_ALL", "TZ", "PYTHONHASHSEED", "PYTHONPATH", "NODE_ENV", "CI")


def key(workspace: Path, *, paths: list[str], tests: list[str], criteria: list[str],
        command: list[str], tool: str, contract: str,
        environment: dict[str, str] | None = None) -> dict[str, Any]:
    """Unknown graph coverage is a cache miss, never a guessed dependency boundary."""
    graph = depgraph.load(str(workspace))
    complete = bool(graph) and depgraph.source_inputs_current(str(workspace), graph)
    quality = depgraph.scan_quality(graph) if graph else {"degraded": True}
    meta = graph.get("meta", {})
    complete = (complete and not quality.get("degraded") and quality.get("mode") == "components"
                and meta.get("source_coverage", {}).get("complete") is True
                and meta.get("module_confidence") != "low")
    modules = graph.get("modules", {})
    def module(path: str) -> str | None:
        matches = [name for name in modules if name == "." or path == name or path.startswith(name + "/")]
        return max(matches, key=len) if matches else None
    selected = {module(path) for path in paths + tests}
    complete = complete and None not in selected and bool(paths and tests and criteria and command and tool and contract)
    closure = set(selected) - {None}
    while True:
        expanded = closure | {edge.get(side) for edge in graph.get("edges", [])
                               for side in ("from", "to")
                               if edge.get("from") in closure or edge.get("to") in closure}
        if expanded == closure:
            break
        closure = expanded
    # A complete workspace scan is not complete runtime dependency coverage.
    # External nodes name packages, but do not bind their installed contents;
    # even an unchanged PYTHONPATH can point at a changed implementation.
    unverified_dependencies = sorted(str(name) for name in closure
                                    if name not in modules
                                    or modules[name].get("kind") == "external")
    complete = complete and not unverified_dependencies
    files = (set(paths + tests) | {path for path in graph.get("files", {}) if module(path) in closure}
             | set(graph.get("context_files", {})))
    fingerprints: dict[str, str] = {}
    for path in sorted(files):
        target = evidence.path(workspace, path)
        fingerprints[path] = hashlib.sha256(evidence.read(workspace, path)).hexdigest() if target.is_file() else "missing"
    executable = Path(command[0]) if command else Path("/nonexistent")
    complete = complete and executable.is_absolute() and executable.is_file()
    executable_hash = hashlib.sha256(executable.read_bytes()).hexdigest() if executable.is_file() else "unknown"
    # Only hashes of explicitly allowlisted nonsecret settings enter persisted data.
    env = environment if environment is not None else dict(os.environ)
    body = {"schema": "taskplane.verification-key/v1", "eligible": bool(complete),
            "paths": sorted(paths), "tests": sorted(tests),
            "files": fingerprints, "modules": sorted(str(x) for x in closure),
            "criteria": sorted(criteria), "command": command, "tool": tool, "contract": contract,
            "runtime": {"executable": str(executable), "sha256": executable_hash,
                        "python": sys.version, "platform": platform.platform(),
                        "taskplane": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in sorted(Path(__file__).parent.glob("*.py"))}},
            "environment": digest({name: env.get(name) for name in ENVIRONMENT}),
            "unverified_dependencies": unverified_dependencies,
            "coverage": "complete" if complete else "unknown"}
    return {**body, "digest": digest(body)}


def record(store: Store, provenance: dict[str, Any], *, status: str, producer: str,
           result: dict[str, Any], findings: list[dict[str, Any]] | None = None,
           verification: dict[str, Any] | None = None, log_ref: dict[str, Any] | None = None,
           log_path: str | None = None) -> dict[str, Any]:
    w.require(status in {"pass", "fail", "unknown"} and bool(producer),
              "invalid_context", "Verification needs an observed status and producer.")
    w.require(status != "pass" or result.get("returncode") == 0,
              "invalid_context", "Passing verification requires a successful observed command.")
    body: dict[str, Any] = {"schema": "taskplane.verification-record/v1", "key": provenance,
            "status": status, "producer": producer, "result": result,
            "findings": findings or [], "authority": "none"}
    if verification is not None:
        body.update(verification=verification, log_ref=log_ref, log_path=log_path)
    return store.put("verification-record", body)


def lookup(store: Store, reference: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    prior = store.resolve(reference)
    w.require(isinstance(prior, dict) and prior.get("schema") == "taskplane.verification-record/v1",
              "invalid_context", "Invalid verification record.")
    eligible = (current.get("eligible") is True and prior.get("key") == current
                and prior.get("status") == "pass" and prior.get("result", {}).get("returncode") == 0)
    return {"status": "reused" if eligible else "miss", "prior_status": prior.get("status"),
            "producer": prior.get("producer"), "findings": prior.get("findings", []),
            "evidence_ref": reference, "key_digest": current.get("digest"), "approval": "not_transferred"}


def _bounded_command(workspace: Path, command: list[str], timeout: float,
                     output: BinaryIO | None = None) -> dict[str, Any]:
    """Drain both pipes into one byte budget and stop the process tree on limits."""
    process = subprocess.Popen(command, cwd=workspace, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=os.name == "posix",
        env={name: os.environ[name] for name in ENVIRONMENT if name in os.environ})
    assert process.stdout is not None and process.stderr is not None
    streams = (process.stdout, process.stderr)
    buffers = [bytearray(), bytearray()]
    decoders = [codecs.getincrementaldecoder("utf-8")(errors="replace") for _ in streams]
    lock = threading.Lock()
    wake = threading.Event()
    stopping = threading.Event()
    cleanup_errors: list[str] = []
    finished: set[int] = set()
    captured = 0
    reason: str | None = None

    def drain(index: int) -> None:
        nonlocal captured, reason
        try:
            while not stopping.is_set():
                chunk = os.read(streams[index].fileno(), 8192)
                if not chunk:
                    break
                with lock:
                    remaining = MAX_OUTPUT_BYTES - captured
                    accepted = chunk[:remaining]
                    if output is None:
                        buffers[index].extend(accepted)
                    else:
                        output.write(decoders[index].decode(accepted).encode("utf-8"))
                        output.flush()
                    captured += min(len(chunk), remaining)
                    if len(chunk) > remaining:
                        reason = "output_limit"
                        break
        except OSError:
            with lock:
                reason = reason or "output_io_error"
        finally:
            streams[index].close()
            with lock:
                if output is not None:
                    try:
                        output.write(decoders[index].decode(b"", final=True).encode("utf-8"))
                        output.flush()
                    except OSError:
                        reason = reason or "output_io_error"
                finished.add(index)
            wake.set()

    readers = [threading.Thread(target=drain, args=(index,), daemon=True) for index in range(2)]
    deadline = time.monotonic() + timeout
    try:
        for reader in readers:
            reader.start()
        while True:
            with lock:
                if reason is not None or (len(finished) == 2 and process.poll() is not None):
                    break
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    reason = "timeout"
                    break
            wake.wait(min(remaining_time, .05))
            wake.clear()
    finally:
        # A child may have exited while descendants still own its pipes. Kill
        # the group even then; waiting only for the direct child leaks workers.
        if reason is not None or len(finished) != 2 or process.poll() is None:
            stopping.set()
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                try:
                    cleanup = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
                    if cleanup.returncode:
                        cleanup_errors.append("tree_termination_failed")
                except (OSError, subprocess.TimeoutExpired):
                    cleanup_errors.append("tree_termination_failed")
                if process.poll() is None:
                    process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cleanup_errors.append("process_wait_timeout")
        for index, reader in enumerate(readers):
            if reader.ident is not None:
                reader.join(timeout=1)
                if reader.is_alive():
                    cleanup_errors.append("descendant_pipe_open")
            else:
                streams[index].close()
        if cleanup_errors:
            reason = reason or "cleanup_incomplete"
    result: dict[str, Any] = {"returncode": process.returncode,
        "stdout": buffers[0].decode("utf-8", errors="replace"),
        "stderr": buffers[1].decode("utf-8", errors="replace")}
    if output is not None:
        result = {"returncode": process.returncode, "captured_output_bytes": captured,
                  "output_limit_bytes": MAX_OUTPUT_BYTES}
    if cleanup_errors:
        result["cleanup_errors"] = cleanup_errors
    if reason is not None:
        result.update(reason=reason, incomplete=True, output_limit_bytes=MAX_OUTPUT_BYTES,
                      captured_output_bytes=captured)
        if reason == "timeout":
            result["timeout"] = timeout
    return result


def run_check(workspace: Path, provenance: dict[str, Any], *, producer: str,
              timeout: float = 120, history_path: str | None = None,
              run: str | None = None, visit: str | None = None, check_id: str | None = None,
              kind: str | None = None, environment: str | None = None,
              log_path: str | None = None, coverage_details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute once, preserving fail/unknown attempts before returning their ref.

    Typed callers explicitly bind run/visit and owned history/log paths. There is
    no implicit active-run lookup. Legacy callers retain the reference-only API.
    """
    typed = history_path is not None
    if typed:
        w.require(all(isinstance(value, str) and value for value in (run, visit, check_id, log_path))
                  and kind in evidence.CHECK_KINDS and environment in evidence.CHECK_ENVIRONMENTS,
                  "invalid_context", "Typed checks need run, visit, check ID, kind, environment and log path.")
        assert history_path is not None and log_path is not None
        evidence.path(workspace, history_path)
        w.require(history_path != log_path and not evidence.path(workspace, log_path).exists(),
                  "invalid_context", "Each attempt needs a new distinct declared log path.")
    else:
        w.require(all(value is None for value in (run, visit, check_id, kind, environment, log_path)),
                  "invalid_context", "Typed check metadata requires an explicit history path.")

    def current() -> dict[str, Any]:
        return key(workspace, paths=provenance["paths"], tests=provenance["tests"],
                   criteria=provenance["criteria"], command=provenance["command"],
                   tool=provenance["tool"], contract=provenance["contract"])
    status = "unknown"
    result: dict[str, Any] = {"returncode": None}
    if not typed:
        result.update(stdout="", stderr="")
    stream: BinaryIO | None = None
    if typed:
        assert log_path is not None
        log_target = evidence.path(workspace, log_path)
        log_target.parent.mkdir(parents=True, exist_ok=True)
        # Reserve this attempt before execution; never overwrite prior evidence.
        fd = os.open(log_target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        stream = os.fdopen(fd, "wb")
    try:
        try:
            if current() != provenance:
                if not typed:
                    raise w.Refusal("invalid_context", "Verification inputs changed before execution.")
                result["reason"] = "input_drift_before_execution"
            else:
                result.update(_bounded_command(workspace, provenance["command"], timeout, output=stream))
                if not result.get("incomplete"):
                    status = "pass" if result["returncode"] == 0 else "fail"
                if current() != provenance:
                    status = "unknown"
                    result.setdefault("reason", "input_drift_during_execution")
        except OSError as exc:
            result.update(reason="launch_error", error_type=type(exc).__name__, errno=exc.errno)
            status = "unknown"
        except w.Refusal:
            if not typed:
                raise
            result["reason"] = "input_fingerprint_unavailable"
            status = "unknown"
        if coverage_details is not None:
            result["coverage_details"] = coverage_details
    finally:
        if stream is not None:
            try:
                stream.write(b"\n" + encode(result) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            finally:
                stream.close()
    store = Store(workspace)
    if not typed:
        return record(store, provenance, status=status, producer=producer, result=result)
    assert history_path is not None and log_path is not None
    target = evidence.path(workspace, history_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with primitives.file_lock(str(target)):
        history = evidence.object_file(workspace, history_path) if target.exists() else {
            "schema": evidence.VERIFICATION_HISTORY, "run": run, "visit": visit,
            "coverage": "declared_producer", "attempts": []}
        w.require(history.get("schema") == evidence.VERIFICATION_HISTORY and history.get("run") == run
                  and history.get("coverage") == "declared_producer" and isinstance(history.get("attempts"), list),
                  "invalid_context", "Cannot append to a foreign verification history.")
        attempts = history["attempts"]
        w.require(all(isinstance(row, dict) and row.get("sequence") == number
                      for number, row in enumerate(attempts, 1)),
                  "invalid_context", "Existing verification history sequence is corrupt.")
        binding = {"id": uuid.uuid4().hex, "check_id": check_id, "run": run, "visit": visit,
                   "kind": kind, "environment": environment}
        raw = evidence.path(workspace, log_path).read_bytes()
        log_ref = store.put("verification-log", {"path": log_path, "encoding": "utf-8",
                            "text": raw.decode(), "sha256": hashlib.sha256(raw).hexdigest()})
        ref = record(store, provenance, status=status, producer=producer, result=result,
                     verification=binding, log_ref=log_ref, log_path=log_path)
        attempts.append({**binding, "sequence": len(attempts) + 1, "status": status,
                         "record_ref": ref, "log_ref": log_ref, "evidence": log_path})
        history["visit"] = visit
        primitives.atomic_json(target, history)
        return ref


def inherited(workspace: Path, reference: dict[str, Any]) -> dict[str, Any]:
    store = Store(workspace)
    prior = store.resolve(reference)
    saved = prior["key"]
    current = key(workspace, paths=saved["paths"], tests=saved["tests"],
                  criteria=saved["criteria"], command=saved["command"],
                  tool=saved["tool"], contract=saved["contract"])
    return lookup(store, reference, current)
