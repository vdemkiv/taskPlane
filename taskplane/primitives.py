"""Shared JSON identities, atomic persistence, and file locks.

Only standard-library dependencies belong here. Encoding choices are explicit
so callers can retain existing content identities while sharing one writer.
"""
from __future__ import annotations
import contextlib as _contextlib
import copy
import time as _time

import hashlib
import json
import os
import stat
import secrets
import subprocess
import sys
import re
import shlex
from pathlib import Path
import tempfile
from typing import Any, BinaryIO, Iterator
from collections.abc import Mapping


def canonical_bytes(value: object, *, ensure_ascii: bool = False,
                    default: Any = None, trailing_newline: bool = False) -> bytes:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=ensure_ascii, allow_nan=False,
                      default=default).encode("utf-8")
    return raw + b"\n" if trailing_newline else raw


def content_fingerprint(value: object, *, ensure_ascii: bool = False,
                        default: Any = None, trailing_newline: bool = False) -> str:
    raw = value if isinstance(value, bytes) else canonical_bytes(
        value, ensure_ascii=ensure_ascii, default=default,
        trailing_newline=trailing_newline)
    return hashlib.sha256(raw).hexdigest()


def manifest_record(body: Mapping[str, Any], *, fingerprint_field: str = "fingerprint",
                    ensure_ascii: bool = False) -> dict[str, Any]:
    """Build a detached manifest with one canonical self-excluding digest.

    Domain owners validate their own fields, references and authority before
    publishing. This constructor never imports a store or discovers inputs.
    """
    material = copy.deepcopy({key: value for key, value in body.items()
                              if key != fingerprint_field})
    return {**material, fingerprint_field: content_fingerprint(material,
                                                              ensure_ascii=ensure_ascii)}


def atomic_json(path: str | os.PathLike[str], value: object, *,
                indent: int | None = 2, ensure_ascii: bool = True,
                sort_keys: bool = True, trailing_newline: bool = True,
                strict_directory_sync: bool = False) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            json.dump(value, target, sort_keys=sort_keys, indent=indent,
                      separators=(",", ":") if indent is None else None,
                      ensure_ascii=ensure_ascii, allow_nan=False)
            if trailing_newline:
                target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
        try:
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            if strict_directory_sync:
                raise
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def lock_file(handle: BinaryIO) -> None:
    try:
        import fcntl
    except ImportError:  # Windows
        import msvcrt
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_LOCK"), 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def unlock_file(handle: BinaryIO) -> None:
    try:
        import fcntl
    except ImportError:  # Windows
        import msvcrt
        handle.seek(0)
        getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


_LOCK_STALE_S = 120.0

class StateError(RuntimeError):
    """A governance state file is unreadable or unprotectable.

    Raised instead of a bare traceback (fail-closed WITH a remedy) — never
    swallowed into a silent default: masking a corrupt control file is how a
    user's `private` flag or a track registry quietly disappears."""

    def __init__(self, path: str, why: str, remedy: str = ""):
        self.path = path
        msg = f"{why}: {path}"
        if remedy:
            msg += f" — {remedy}"
        super().__init__(msg)


@_contextlib.contextmanager
def file_lock(path: str, *, timeout: float = 10.0) -> Iterator[None]:
    """Advisory exclusive lock on <path>.lock — NEVER silently lock-free.

    Primary: fcntl.flock. Where flock is unavailable or refused (Windows,
    some FUSE/network mounts — exactly the hosts this plugin targets), fall
    back to an atomic mkdir spin-lock with staleness recovery instead of
    proceeding unlocked. If even that cannot be acquired within `timeout`,
    raise StateError: failing closed beats corrupting shared state."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lock_path = path + ".lock"
    # ACQUISITION failures fall through to the mkdir lock; an exception
    # raised by the CALLER'S BODY must propagate unchanged (v2.3.0 — the
    # earlier shape caught the body's OSError too, then yielded a second
    # time from the fallback path).
    lf = None
    try:
        lf = open(lock_path, "a+b")
        lock_file(lf)
    except (ImportError, OSError):
        if lf is not None:
            lf.close()
        lf = None     # fall through to the mkdir lock — not to "no lock"
    if lf is not None:
        try:
            yield
        finally:
            try:
                unlock_file(lf)
            finally:
                lf.close()
        return
    lockdir = path + ".lockdir"
    deadline = _time.monotonic() + max(0.1, timeout)
    while True:
        try:
            os.mkdir(lockdir)
            break
        except FileExistsError:
            try:  # steal a lock left behind by a dead process
                if _time.time() - os.stat(lockdir).st_mtime > _LOCK_STALE_S:
                    os.rmdir(lockdir)
                    continue
            except OSError:
                pass
            if _time.monotonic() >= deadline:
                raise StateError(
                    lockdir, "could not acquire state lock",
                    "another process holds it; if it is dead, remove the "
                    "lockdir") from None
            _time.sleep(0.05)
        except OSError as e:
            raise StateError(lockdir, f"lock unavailable ({e})") from None
    try:
        yield
    finally:
        try:
            os.rmdir(lockdir)
        except OSError:
            pass


def git_environment(git_path: Path) -> dict[str, str]:
    """Build a closed environment; never inherit Git routing/config state."""
    path_parts = [str(git_path.parent)]
    if os.name != "nt":
        path_parts.extend(["/usr/bin", "/bin"])
    return {
        "PATH": os.pathsep.join(dict.fromkeys(path_parts)),
        "HOME": os.devnull,
        "USERPROFILE": os.devnull,
        "XDG_CONFIG_HOME": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
        "LANG": "C",
    }


def bounded_text(value: object, limit: int) -> str:
    """Bound valid UTF-8 text without splitting its last character."""
    return str(value or "").encode("utf-8", errors="replace")[:limit].decode("utf-8", errors="ignore")


def store_identity(store: object, namespace: str) -> tuple[object, ...]:
    identity = tuple(getattr(store, field, None) for field in (
        "caller_root", "repository_fingerprint", "run_namespace"))
    if all(value is not None for value in identity):
        return (namespace, *(str(value) for value in identity))
    return (namespace + "-object", id(store))


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def fingerprint_text(value: object, field: str, *, optional: bool = False,
                     error: type[ValueError] = ValueError) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise error(f"{field} is required")
    value = value.strip()
    if not is_sha256(value):
        raise error(f"{field} must be a 64-character lowercase SHA-256 fingerprint")
    return value


def append_instrument(path: str, record: dict[str, Any]) -> None:
    """Append optional telemetry; an instrument cannot hold a delivery gate."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        record.setdefault("ts", _time.time())
        with file_lock(path), open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, default=str, sort_keys=True) + "\n")
    except Exception:
        pass


def atomic_write_json(path: str, data: Any, *, indent: int=1, sort_keys: bool=False, private: bool=False) -> None:
    """Keep enforcement's exact encoding through the shared durable writer."""
    _durable_makedirs(os.path.dirname(path) or '.')
    atomic_json(path, data, indent=indent, sort_keys=sort_keys, trailing_newline=False, strict_directory_sync=True)

def atomic_write_bytes(path: str, data: bytes) -> None:
    """Durably replace one file with exact caller-owned bytes.

    This is the byte-preserving counterpart of :func:`atomic_write_json` for
    recovery paths that must restore the exact prior artifact representation,
    rather than merely an equivalent decoded JSON value.
    """
    if not isinstance(data, bytes):
        raise TypeError('atomic_write_bytes requires bytes')
    directory = os.path.dirname(path) or '.'
    _durable_makedirs(directory)
    temporary = os.path.join(directory, f'.{os.path.basename(path)}.tmp.{os.getpid()}.{secrets.token_hex(8)}')
    try:
        with open(temporary, 'xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(directory)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass

def _durable_makedirs(path: str) -> None:
    """Create a directory chain without acknowledging volatile ancestors.

    ``os.makedirs`` makes the complete chain but provides no point at which a
    caller can persist each newly linked directory.  Governance state may be
    the first write beneath a fresh run/store hierarchy, so create each
    missing component separately.  The child is flushed first, then the
    parent that owns its name.  Any failure propagates before the state file
    is opened; a partially created (but unacknowledged) empty chain is safe to
    retry.
    """
    target = os.path.abspath(path)
    missing = []
    cursor = target
    while not os.path.lexists(cursor):
        missing.append(cursor)
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent
    _durable_directory_identity(cursor)
    for directory in reversed(missing):
        parent = os.path.dirname(directory) or '.'
        try:
            os.mkdir(directory)
        except FileExistsError:
            pass
        identity = _durable_directory_identity(directory)
        _fsync_directory(directory)
        _fsync_directory(parent)
        if _durable_directory_identity(directory) != identity:
            raise StateError(directory, 'durable directory identity changed during fsync')

def _durable_directory_identity(path: str) -> tuple[int, int]:
    """Return a stable non-symlink directory identity or fail closed."""
    try:
        value = os.lstat(path)
    except OSError as exc:
        raise StateError(path, f'durable directory is unavailable ({exc})') from None
    if stat.S_ISLNK(value.st_mode):
        raise StateError(path, 'durable directory anchor is a symlink')
    if not stat.S_ISDIR(value.st_mode):
        raise StateError(path, 'durable directory anchor is not a directory')
    return (int(value.st_dev), int(value.st_ino))

def _flush_windows_directory(path: str) -> None:
    """Flush one directory through the native backup-semantics handle."""
    import ctypes
    kernel = getattr(ctypes, "WinDLL")('kernel32', use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
    kernel.FlushFileBuffers.argtypes = (ctypes.c_void_p,)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.CreateFileW(str(path), 2147483648, 7, None, 3, 33554432, None)
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
    try:
        if not kernel.FlushFileBuffers(handle):
            error = getattr(ctypes, "get_last_error")()
            if error != 5:
                raise getattr(ctypes, "WinError")(error)
    finally:
        if not kernel.CloseHandle(handle):
            raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())

def _fsync_directory(path: str) -> None:
    """Persist a directory entry update before its caller acknowledges it."""
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        if os.name != 'nt':
            raise
        _flush_windows_directory(path)
        return
    try:
        try:
            os.fsync(fd)
        except PermissionError:
            if os.name != 'nt':
                raise
            _flush_windows_directory(path)
    finally:
        os.close(fd)
_LOAD_RAISE = object()

def load_json(path: str, default: Any=_LOAD_RAISE, *, what: str='state file') -> Any:
    """Read a JSON governance file with a strict corruption contract.

    Missing file  -> `default` when given, else StateError (fail closed).
    Corrupt file  -> ALWAYS StateError naming the path and a remedy — a
                     corrupt control file must never be silently replaced by
                     a default (that is fail-open data loss)."""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        if default is not _LOAD_RAISE:
            return default
        raise StateError(path, f'missing {what}', 're-run the command that creates it') from None
    except ValueError as e:
        raise StateError(path, f'corrupt {what} ({e})', 'inspect/restore it (git checkout or delete after review); taskplane will not guess its contents') from None
    except OSError as e:
        raise StateError(path, f'unreadable {what} ({e})') from None

def _run(cmd: Any, cwd: Any, shell: Any=False, timeout: Any=600, env: Any=None) -> Any:
    return subprocess.run(cmd, cwd=cwd, shell=shell, capture_output=True, text=True, timeout=timeout, env=env, encoding='utf-8', errors='replace')
_CHECKOUT_PYTHON_TRAMPOLINE = 'import os,sys;root=os.path.realpath(sys.argv[1]);sys.path.insert(0,os.path.realpath(sys.argv[2]));import primitives as _tp;_tp._checkout_bound_main(root,sys.argv[3:])'

def _python_program(value: Any) -> bool:
    try:
        program = os.path.basename(os.fspath(value)).lower()
    except TypeError:
        return False
    return bool(re.fullmatch('python(?:\\d+(?:\\.\\d+)?)?(?:\\.exe)?', program))

def _checkout_bound_python_args(workspace: str, args: Any) -> list[Any]:
    return [sys.executable, '-c', _CHECKOUT_PYTHON_TRAMPOLINE, os.path.realpath(workspace), os.path.dirname(os.path.realpath(__file__)), *list(args)]

def _checkout_bound_main(workspace: str, args: Any) -> None:
    """Execute Python argv with a checkout namespace, transitively.

    Tests and regression probes legitimately start nested Python/pytest
    processes. They must inherit the same checkout boundary instead of
    falling back to an unrelated editable install from system site-packages.
    Intercepting only explicit Python argv keeps ordinary subprocesses and
    shell commands byte-for-byte unchanged.
    """
    import importlib.machinery
    import runpy
    import types
    root = os.path.realpath(workspace)
    python_args = list(args or ())
    package_path = os.path.join(root, 'taskplane')
    package = types.ModuleType('taskplane')
    package.__package__ = 'taskplane'
    package.__path__ = [package_path]
    package.__spec__ = importlib.machinery.ModuleSpec('taskplane', loader=None, is_package=True)
    package.__spec__.submodule_search_locations = package.__path__
    sys.modules['taskplane'] = package
    sys.path[:] = [p for p in sys.path if p not in (root, package_path)]
    sys.path[:0] = [root, package_path]
    original_popen = subprocess.Popen

    def checkout_popen(command: Any, *popen_args: Any, **popen_kwargs: Any) -> Any:
        if isinstance(command, (list, tuple)) and command and _python_program(command[0]):
            command = _checkout_bound_python_args(root, command[1:])
        return original_popen(command, *popen_args, **popen_kwargs)
    setattr(subprocess, "Popen", checkout_popen)
    if not python_args:
        raise SystemExit('checkout-bound Python command is empty')
    if python_args[0] == '-m' and len(python_args) >= 2:
        module = python_args[1]
        sys.argv = [module, *python_args[2:]]
        runpy.run_module(module, run_name='__main__', alter_sys=True)
    elif python_args[0] == '-c' and len(python_args) >= 2:
        sys.argv = ['-c', *python_args[2:]]
        exec(compile(python_args[1], '<string>', 'exec'), {'__name__': '__main__'})
    else:
        script = python_args[0]
        if not os.path.isabs(script):
            script = os.path.join(root, script)
        sys.argv = [script, *python_args[1:]]
        runpy.run_path(script, run_name='__main__')

def _checkout_bound_python_argv(workspace: str, command: str) -> list[Any] | None:
    """Translate one plain Python suite command to the current interpreter.

    The checkout intentionally has no ``taskplane/__init__.py``. A globally
    installed regular package would therefore beat the checkout namespace.
    The bootstrap pins that namespace in-process without PATH aliases,
    PYTHONPATH shims, or a machine-specific interpreter name.
    """
    try:
        lexer = shlex.shlex(str(command or ''), posix=True, punctuation_chars='|&;<>')
        lexer.whitespace_split = True
        lexer.commenters = ''
        tokens = list(lexer)
    except ValueError:
        return None
    if not tokens or any((token and set(token) <= set('|&;<>') for token in tokens)):
        return None
    if not _python_program(tokens[0]):
        return None
    if len(tokens) < 2:
        return None
    return _checkout_bound_python_args(workspace, tokens[1:])

def run_suite_command(workspace: str, command: Any, *, env: Any=None, timeout: int=600) -> Any:
    """Run a declared suite portably while retaining its original identity."""
    if isinstance(command, (list, tuple)):
        argv = _checkout_bound_python_args(workspace, command[1:]) if command and _python_program(command[0]) else list(command)
        return _run(argv, cwd=workspace, shell=False, timeout=timeout, env=env)
    bound = _checkout_bound_python_argv(workspace, command)
    if bound is not None:
        return _run(bound, cwd=workspace, shell=False, timeout=timeout, env=env)
    return _run(command, cwd=workspace, shell=True, timeout=timeout, env=env)

def _ensure_self_ignored(d: str) -> None:
    """The runtime dir ignores itself — a worker's `git add -A` must never
    commit contracts/traces, and merges must never collide on them."""
    gi = os.path.join(d, '.gitignore')
    if not os.path.isdir(d):
        return
    body = ''
    try:
        with open(gi, encoding='utf-8') as f:
            body = f.read()
    except OSError:
        body = ''
    if '*' not in body.splitlines():
        try:
            with open(gi, 'w', encoding='utf-8', newline='') as f:
                f.write('*\n')
        except OSError:
            pass
