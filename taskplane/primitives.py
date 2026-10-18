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
import re
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
            _fsync_directory(str(destination.parent))
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
def file_lock(path: str) -> Iterator[None]:
    """Hold the native exclusive lock, or refuse before entering the body.

    Native locks release when the owning handle/process exits. Directory ages
    cannot establish owner death and must never be used to steal a live lock.
    """
    lock_path = path + ".lock"
    lf = None
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_APPEND
                             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
        lf = os.fdopen(descriptor, "a+b")
        if not stat.S_ISREG(os.fstat(lf.fileno()).st_mode):
            raise OSError("state lock must be a regular file")
        lock_file(lf)
    except (ImportError, OSError) as exc:
        if lf is not None:
            lf.close()
        raise StateError(lock_path, f"native state lock unavailable ({exc})",
                         "use a filesystem with supported native file locking") from exc
    try:
        yield
    finally:
        try:
            unlock_file(lf)
        finally:
            lf.close()


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


def append_instrument(path: str, record: dict[str, Any], *, strict: bool = False) -> None:
    """Append telemetry; explicit user actions can require a successful write."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        record.setdefault("ts", _time.time())
        with file_lock(path), open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, default=str, sort_keys=True) + "\n")
    except Exception:
        if strict:
            raise


def atomic_write_json(path: str, data: Any, *, indent: int=1, sort_keys: bool=False, private: bool=False) -> None:
    """Keep enforcement's exact encoding through the shared durable writer."""
    _durable_makedirs(os.path.dirname(path) or '.')
    atomic_json(path, data, indent=indent, sort_keys=sort_keys, trailing_newline=False, strict_directory_sync=True)

def atomic_write_bytes(path: str, data: bytes, *, private: bool = False) -> None:
    """Durably replace one file with exact caller-owned bytes.

    This is the byte-preserving counterpart of :func:`atomic_write_json` for
    recovery paths that must restore the exact prior artifact representation,
    rather than merely an equivalent decoded JSON value. Private publications
    create their temporary file with mode 0600 before writing any contents.
    """
    if not isinstance(data, bytes):
        raise TypeError('atomic_write_bytes requires bytes')
    directory = os.path.dirname(path) or '.'
    _durable_makedirs(directory)
    temporary = os.path.join(directory, f'.{os.path.basename(path)}.tmp.{os.getpid()}.{secrets.token_hex(8)}')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                             0o600 if private else 0o666)
        with os.fdopen(descriptor, 'wb') as stream:
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

def _durable_makedirs(path: str, *, mode: int = 0o777, sync_existing: bool = False) -> None:
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
    if sync_existing:
        # Recovery cannot distinguish a durable pre-existing directory from one
        # left by interruption immediately after mkdir. Revalidate and flush the
        # complete ancestry on exact retry, including each owning parent.
        chain = list(reversed((Path(target), *Path(target).parents)))
        for entry in chain:
            # A filesystem anchor already exists independently of this chain.
            # Creating a Windows drive root or UNC share reports access denied,
            # not FileExistsError. Still validate and flush the anchor below.
            if entry != entry.parent:
                try:
                    os.mkdir(entry, mode=mode)
                except FileExistsError:
                    pass
            identity = _durable_directory_identity(str(entry))
            _fsync_directory(str(entry))
            _fsync_directory(str(entry.parent))
            if _durable_directory_identity(str(entry)) != identity:
                raise StateError(str(entry), 'durable directory identity changed during fsync')
        return
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
            os.mkdir(directory, mode=mode)
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
    kernel.FlushFileBuffers.restype = ctypes.c_int
    kernel.FlushFileBuffers.argtypes = (ctypes.c_void_p,)
    kernel.CloseHandle.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    # FlushFileBuffers requires GENERIC_WRITE, even for a directory opened
    # with FILE_FLAG_BACKUP_SEMANTICS. A read-only handle always fails with
    # ERROR_ACCESS_DENIED before the filesystem can perform the flush.
    # https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers
    generic_write = 0x40000000
    share_read_write_delete = 0x00000007
    open_existing = 3
    backup_semantics = 0x02000000
    handle = kernel.CreateFileW(str(path), generic_write, share_read_write_delete,
                                None, open_existing, backup_semantics, None)
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
    try:
        if not kernel.FlushFileBuffers(handle):
            error = getattr(ctypes, "get_last_error")()
            # Access denied is not successful persistence. Some Windows/filesystem
            # combinations cannot flush directory handles; strict callers must
            # report unsupported durability instead of acknowledging a commit.
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
    if shell or isinstance(cmd, (str, bytes)):
        raise ValueError('Taskplane subprocesses require argv and cannot execute a shell')
    return subprocess.run(cmd, cwd=cwd, shell=False, capture_output=True, text=True, timeout=timeout, env=env, encoding='utf-8', errors='replace')
def _ensure_self_ignored(d: str) -> None:
    """The runtime dir ignores itself — a worker's `git add -A` must never
    commit local observations, and merges must never collide on them."""
    gi = os.path.join(d, '.gitignore')
    if any(p.is_symlink() for p in (Path(gi), *Path(gi).parents)):
        raise ValueError('Runtime ignore file cannot follow symlinks')
    if not os.path.isdir(d):
        return
    body = ''
    try:
        fd = os.open(gi, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, encoding='utf-8') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise ValueError('Runtime ignore file must be regular')
            body = f.read()
    except FileNotFoundError:
        body = ''
    if '*' not in body.splitlines():
        atomic_write_bytes(gi, b'*\n')
