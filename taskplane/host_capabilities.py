"""Native discovery restored from 38c1d8b; observations never grant authority.

Executable/profile matching and plugin-family selection retain the historical
algorithms. Host records are supplied by their owner, never found through a
workspace launcher. No function here authenticates a human or executes a tool.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
from typing import Any, Mapping, Sequence

from .primitives import content_fingerprint

CAPABILITIES = ("protected_store", "human_origin", "tool_containment", "process_tracking")
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:\+codex\.[0-9A-Za-z.-]+)?$")


def codex_readonly_runtime(workspace: str) -> str | None:
    if os.name != "posix" or not os.environ.get("CODEX_THREAD_ID"):
        return None
    if any(k.startswith(("LD_", "DYLD_", "BASH_FUNC_")) or k in
           {"ENV", "BASH_ENV", "SHELLOPTS", "BASHOPTS"} for k in os.environ):
        return None
    value = shutil.which("codex")
    if not value:
        return None
    executable = Path(value).resolve()
    if executable.is_relative_to(Path(workspace).resolve()) or not executable.is_file():
        return None
    return str(executable)


def codex_readonly_command(argv: list[str], workspace: str) -> dict[str, Any]:
    """Describe the fixed native profile; the host still decides execution."""
    executable = codex_readonly_runtime(workspace)
    if not executable or not argv or any(not isinstance(v, str) or not v or "\0" in v for v in argv):
        raise ValueError("Native read-only execution is unavailable or argv is invalid")
    request: dict[str, Any] = {
        "cmd": shlex.join([executable, "sandbox", "--include-managed-config", "-P", ":read-only", "--", *argv]),
        "shell": "/bin/sh", "login": False, "workdir": str(Path(workspace).resolve()),
    }
    if os.environ.get("CODEX_SANDBOX") == "seatbelt":
        request.update(sandbox_permissions="require_escalated",
                       justification="Allow the native host to launch its read-only sandbox?")
    return request


def pending_codex_tool_call(name: str, records: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Recognize exactly one pending call in a complete host-supplied record set.

    This is a parser, not proof of record origin. Truncated/restarted transcripts
    must be reconciled by the host before being passed here. JS is never evaluated.
    """
    pending: dict[str, Mapping[str, Any]] = {}
    seen: set[str] = set()
    for row in records:
        if not isinstance(row, Mapping):
            return None
        if row.get("type") != "response_item":
            continue
        value = row.get("payload")
        if not isinstance(value, Mapping):
            return None
        kind, call = value.get("type"), value.get("call_id")
        if kind not in {"custom_tool_call", "function_call", "custom_tool_call_output", "function_call_output"}:
            continue
        if not isinstance(call, str) or not call:
            return None
        if kind in {"custom_tool_call", "function_call"}:
            if call in seen:
                return None
            seen.add(call)
            pending[call] = value
        else:
            if call not in pending:
                return None
            del pending[call]
    if len(pending) != 1:
        return None
    value = next(iter(pending.values()))
    raw = value.get("input", value.get("arguments"))
    if value.get("name") in {"exec", "functions.exec"} and isinstance(raw, str):
        match = re.fullmatch(r"text\(await tools\." + re.escape(name) + r"\((\{.*\})\)\);", raw.strip(), re.DOTALL)
        raw = match[1] if match else None
    elif value.get("name") not in {name, "functions." + name}:
        return None
    try:
        args = json.loads(raw) if isinstance(raw, str) else None
    except (ValueError, TypeError):
        return None
    return args if isinstance(args, dict) else None


def is_codex_readonly_invocation(tool: str, payload: Mapping[str, Any], workspace: str,
                                *, pending: Mapping[str, Any] | None = None) -> bool:
    executable = codex_readonly_runtime(workspace)
    if not executable or tool not in {"exec_command", "functions.exec_command", "Bash"}:
        return False
    if tool == "Bash":
        if set(payload) != {"command"} or pending is None or payload.get("command") != pending.get("cmd"):
            return False
        return is_codex_readonly_invocation("exec_command", pending, workspace)
    if set(payload) - {"cmd", "shell", "login", "workdir", "max_output_tokens", "yield_time_ms",
                       "sandbox_permissions", "justification", "tty"}:
        return False
    if payload.get("shell") != "/bin/sh" or payload.get("login") is not False:
        return False
    cwd = payload.get("workdir")
    if not isinstance(cwd, str) or Path(cwd).resolve() != Path(workspace).resolve():
        return False
    command = payload.get("cmd")
    if not isinstance(command, str) or "\0" in command:
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    return len(argv) > 6 and argv[:6] == [executable, "sandbox", "--include-managed-config", "-P", ":read-only", "--"] and shlex.join(argv) == command


def valid_plugin_root(root: str, family: str, host: str = "codex") -> tuple[tuple[int, ...], str, str] | None:
    """Select only regular files contained in the explicitly supplied family."""
    candidate, base = Path(root).absolute(), Path(family).absolute()
    if host not in {"codex", "claude"} or candidate.is_symlink() or not candidate.is_relative_to(base):
        return None
    engine = candidate / "taskplane/tp.py"
    manifest = candidate / (".codex-plugin" if host == "codex" else ".claude-plugin") / "plugin.json"
    for p in (engine, manifest):
        if not p.is_file() or any(q.is_symlink() for q in (p, *p.parents)):
            return None
        if not p.resolve().is_relative_to(base.resolve()):
            return None
    try:
        value = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return None
    version = value.get("version") if isinstance(value, dict) else None
    match = _VERSION.fullmatch(str(version or ""))
    if not match or value.get("name") != "taskplane" or candidate != base and candidate.name != version:
        return None
    return tuple(int(v) for v in match.groups()), str(version), str(engine.resolve())


def resolve_plugin_engine(family: str, host: str = "codex") -> str | None:
    base = Path(family).expanduser().absolute()
    try:
        candidates = [r for p in [base, *base.iterdir()] if (r := valid_plugin_root(str(p), str(base), host))]
    except OSError:
        return None
    if not candidates:
        return None
    latest = max(r[0] for r in candidates)
    candidates = [r for r in candidates if r[0] == latest]
    if len(candidates) > 1:
        stamps = [re.fullmatch(r"\d+\.\d+\.\d+\+codex\.(\d{14})", r[1]) for r in candidates]
        if not all(stamps):
            return None
        newest = max(m[1] for m in stamps if m)
        candidates = [r for r, m in zip(candidates, stamps) if m and m[1] == newest]
    return candidates[0][2] if len(candidates) == 1 else None


def runtime_hook_observations(records: Sequence[Mapping[str, Any]], *, host: str,
                              session: str, workspace: str) -> dict[str, Any]:
    """Project exact-context event metadata; receipt presence never means trust."""
    names = {"SessionStart", "PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop", "Stop", "UserPromptSubmit"}
    context = {"host": host, "session": session, "workspace": str(Path(workspace).resolve())}
    observed = sorted({str(r.get("event")) for r in records
                       if all(r.get(k) == v for k, v in context.items()) and r.get("event") in names})
    return {"schema": "taskplane.native-hook-observations/v1", "context_fingerprint": content_fingerprint(context),
            "events_seen": observed, "authority_verified": False,
            "detail": "Matching hook observations do not authenticate human decisions or complete tool coverage."}


def inspect_native(host: str, workspace: Path, session: str) -> dict[str, Any]:
    """Bounded, read-only diagnostics from the executing plugin; no cache search."""
    plugin = Path(__file__).resolve().parents[1]
    selected = valid_plugin_root(str(plugin), str(plugin), host)
    executable = codex_readonly_runtime(str(workspace)) if host == "codex" else None
    return {"schema": "taskplane.native-readiness/v1", "host": host,
            "context_fingerprint": content_fingerprint({"host": host, "workspace": str(workspace.resolve()), "session": session}),
            "plugin_version": selected[1] if selected else None,
            "plugin_files_valid": selected is not None,
            "plugin_outside_workspace": not plugin.is_relative_to(workspace.resolve()),
            "session_present": bool(session and session != "unknown"),
            "readonly_executable_found": executable is not None,
            "runtime_identity": runtime_identity(),
            "authority_verified": False,
            "detail": "Discovery is observational. No native issuer, protected store or complete process/tool boundary is certified."}


def runtime_identity() -> dict[str, Any]:
    """Identity of this executing runtime; a manifest alone does not prove hook loading."""
    import hashlib
    root = Path(__file__).resolve().parents[1]
    members = {}
    for name in ('taskplane/tp.py', 'taskplane/flow.py', 'taskplane/workflow_host.py',
                 'taskplane/workflow_local.py', 'taskplane/worker_runtime.py',
                 'taskplane/context_handoff.py', 'hooks/hooks.json'):
        target = root/name
        members[name] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
    return {'root': str(root), 'member_sha256': members,
            'basis': 'executing Python module path and file bytes; observation, not host attestation'}


def worker_identities(parent: str, *, canonical_name: str | None = None,
                      task_name: str | None = None, since: str) -> list[dict[str, str]]:
    """Match returned native names to actual IDs using bounded native lineage.

    No prompt/env-supplied root override and no latest-child guess. Ambiguous
    records yield no binding. This remains observed local metadata, not attestation.
    """
    from datetime import datetime
    import stat
    from . import native_session_meter as meter
    home = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))
    found: dict[str, dict[str, str]] = {}
    cutoff = datetime.fromisoformat(since.replace('Z', '+00:00'))
    count = 0
    for folder in ('sessions', 'archived_sessions'):
        for path in (home/folder).glob('**/*.jsonl'):
            count += 1
            if count > 20000:
                return []
            try:
                if any(p.is_symlink() for p in (path, *path.parents)):
                    continue
                with path.open('rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        continue
                    meta, _ = meter._session_metadata(stream.readline(meter.MAX_METADATA_BYTES))
                name = meta.get('agent_path')
                born = datetime.fromisoformat(meta['started_at'].replace('Z', '+00:00'))
                if (meta.get('parent_session_id') == parent and born >= cutoff and name
                        and (name == canonical_name if canonical_name else name.rsplit('/', 1)[-1] == task_name)):
                    value = dict(worker_id=meta['session_id'], canonical_name=name, parent=parent,
                                 started_at=meta['started_at'])
                    if value['worker_id'] in found and found[value['worker_id']] != value:
                        return []
                    found[value['worker_id']] = value
            except (OSError, ValueError, TypeError):
                continue
    return list(found.values()) if len(found) == 1 else []
