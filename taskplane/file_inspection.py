"""Bounded file inspection through the existing CLI, without running source code.

This is a data operation, not a general command adapter. The hook admits only
the exact isolated installed-engine invocation returned by ``tool_input``.
Ordinary terminal commands, custom interpreters and caller code stay denied.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
from typing import Any


OPERATIONS = {"read": "Read", "list": "Glob", "search": "Grep"}
MAX_BYTES = 256 * 1024
MAX_LINES = 400
MAX_SCAN_BYTES = 16 * 1024 * 1024


def decode(encoded: str) -> dict[str, Any]:
    if not isinstance(encoded, str) or len(encoded) > 16384 or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", encoded):
        raise ValueError("inspection request must be bounded URL-safe base64 JSON")
    try:
        value = json.loads(base64.b64decode(encoded, altchars=b"-_", validate=True))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid inspection request") from exc
    if not isinstance(value, dict) or set(value) - {"operation", "path", "start", "limit", "pattern"}:
        raise ValueError("unknown inspection request fields")
    if value.get("operation") not in OPERATIONS or not isinstance(value.get("path"), str) or not value["path"] or "\0" in value["path"]:
        raise ValueError("inspection requires an operation and a file or directory path")
    for field, default, maximum in (("start", 1, 1000000), ("limit", 200, MAX_LINES)):
        number = value.get(field, default)
        if type(number) is not int or not 1 <= number <= maximum:
            raise ValueError(f"inspection {field} must be between 1 and {maximum}")
        value[field] = number
    pattern = value.get("pattern")
    if value["operation"] == "search":
        if not isinstance(pattern, str) or not 1 <= len(pattern) <= 1024:
            raise ValueError("search requires a bounded literal pattern")
    elif pattern is not None:
        raise ValueError("pattern is only supported by search")
    return value


def encode(request: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(request, separators=(",", ":")).encode()).decode()
    decode(encoded)
    return encoded


def launch_supported() -> bool:
    # sh in noninteractive POSIX mode does not source ENV/BASH_ENV; refuse
    # loader and exported-function injection too. Python -I -S ignores user
    # paths, startup customizations and site packages. Absolute paths avoid PATH.
    return (os.name == "posix" and os.path.isfile("/bin/sh") and
            not any(key.startswith(("LD_", "DYLD_", "BASH_FUNC_")) or
                    key in {"ENV", "BASH_ENV", "SHELLOPTS", "BASHOPTS"}
                    for key in os.environ))


def tool_input(engine: str, workspace: str, request: dict[str, Any]) -> dict[str, Any]:
    return {"cmd": shlex.join(["exec", os.path.realpath(sys.executable), "-I", "-S", "-B",
                               os.path.realpath(engine), "inspect", encode(request)]),
            "shell": "/bin/sh", "login": False, "workdir": os.path.realpath(workspace)}


def invocation(tool_name: str, arguments: dict[str, Any], engine: str, workspace: str) -> dict[str, Any] | None:
    """Recognize one exact data operation; never infer permission from argv claims."""
    if tool_name not in {"exec_command", "functions.exec_command"} or not launch_supported():
        return None
    if (arguments.get("shell") != "/bin/sh" or arguments.get("login") is not False or
            arguments.get("tty", False) is not False or
            arguments.get("sandbox_permissions", "use_default") != "use_default" or
            set(arguments) - {"cmd", "shell", "login", "workdir", "tty", "sandbox_permissions",
                              "max_output_tokens", "yield_time_ms"}):
        return None
    if os.path.realpath(str(arguments.get("workdir") or os.getcwd())) != os.path.realpath(workspace):
        return None
    command = arguments.get("cmd")
    if not isinstance(command, str) or len(command) > 20000:
        return None
    try:
        argv = shlex.split(command)
        prefix = ["exec", os.path.realpath(sys.executable), "-I", "-S", "-B", os.path.realpath(engine), "inspect"]
        if len(argv) != 8 or argv[:7] != prefix or command != shlex.join(argv):
            return None
        # The installed engine is trusted code, never a reviewed checkout's
        # substitute script or a symlink switched after the hook check.
        if os.path.abspath(engine) != os.path.realpath(engine) or os.path.islink(engine):
            return None
        if os.path.commonpath((os.path.realpath(engine), os.path.realpath(workspace))) == os.path.realpath(workspace):
            return None
        return decode(argv[-1])
    except (ValueError, OSError):
        return None


def inspect(request: dict[str, Any], workspace: str) -> dict[str, Any]:
    """Read regular files or list a directory; never import or execute its contents."""
    path = Path(request["path"])
    if not path.is_absolute():
        path = Path(workspace) / path
    operation = request["operation"]
    result: dict[str, Any] = {"operation": operation, "path": str(path), "truncated": False}
    if operation == "list":
        rows: list[dict[str, Any]] = []
        with os.scandir(path) as entries:
            for index, entry in enumerate(entries, 1):
                if index < request["start"]:
                    continue
                if len(rows) >= request["limit"]:
                    result["truncated"] = True
                    break
                rows.append({"name": entry.name, "directory": entry.is_dir(follow_symlinks=False),
                             "symlink": entry.is_symlink()})
        result["entries"] = rows
        return result
    # O_NONBLOCK avoids blocking on a FIFO before fstat can reject it.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("inspection reads regular files only")
        rows = []
        consumed = output_bytes = number = 0
        while line := stream.readline(MAX_BYTES + 1):
            number += 1
            consumed += len(line)
            if len(line) > MAX_BYTES or consumed > MAX_SCAN_BYTES:
                result["truncated"] = True
                break
            if number < request["start"]:
                continue
            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if operation == "search" and request["pattern"] not in text:
                continue
            output_bytes += len(line)
            if len(rows) >= request["limit"] or output_bytes > MAX_BYTES:
                result["truncated"] = True
                break
            rows.append({"line": number, "text": text})
        result["lines"] = rows
    return result


def main(argv: list[str]) -> int:
    """No Git, run discovery, subprocesses, settings imports or state writes.

    Contract and budget admission belongs to the existing PreToolUse hook.
    Even outside that hook this closed operation can only inspect file data.
    """
    try:
        if len(argv) != 1 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
            raise ValueError("inspection requires the isolated -I -S -B engine invocation and one request")
        print(json.dumps(inspect(decode(argv[0]), os.getcwd()), ensure_ascii=True))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 2
