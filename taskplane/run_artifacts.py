"""Private, durable evidence stored beside one canonical run.

This module owns evidence layout and integrity only.  It deliberately has no
run status, transition, retry, or terminal-outcome fields; those remain in the
canonical :mod:`run_store` manifest.  Every stored object repeats the closed
run/stage/candidate/settings/source binding so a portable reference cannot be
silently replayed into another working candidate.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path, PurePath
import re
import stat
import time
import uuid
from typing import Any, BinaryIO, Iterator, Mapping, Sequence, TypeAlias, cast


MANIFEST_SCHEMA = "taskplane.run-artifacts/v1"
MANIFEST_REFERENCE_SCHEMA = "taskplane.run-artifact-manifest-ref/v1"
BINDING_SCHEMA = "taskplane.run-artifact-binding/v1"
CLASS_SCHEMA = "taskplane.run-artifact-class/v1"
ARTIFACT_SCHEMA = "taskplane.run-artifact/v1"
ACTIVITY_SCHEMA = "taskplane.agent-activity-event/v1"
VERIFICATION_SCHEMA = "taskplane.run-artifact-verification/v1"
MANIFEST_NAME = "run-artifacts.json"
MANIFEST_LOCATOR = "artifacts/run-artifacts.json"

ARTIFACT_CLASSES = (
    "dashboard",
    "dependency-graphs",
    "telemetry",
    "agent-activity",
    "validation",
    "cleanup",
    "retro",
)
ACTIVITY_EVENT_TYPES = frozenset({
    "assignment",
    "worker-identity",
    "start",
    "progress",
    "attention",
    "terminal",
    "cancel",
    "interruption",
    "handoff",
    "usage-reference",
    "evidence-reference",
})
_DISTINCT_ACTIVITY_OUTCOMES = {
    "cancel": "cancellation",
    "interruption": "interruption",
    "handoff": "handoff",
}

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}$")
_OBJECT_NAME = re.compile(r"^[0-9]{8}-[0-9a-f]{64}\.json$")
_MAX_CANDIDATE_BYTES = 128 * 1024
_MAX_METADATA_BYTES = 128 * 1024
_MAX_ACTIVITY_BYTES = 256 * 1024
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_MAX_ENTRIES = 20_000
_MAX_ROOT_HYGIENE_ENTRIES = 8
_MAX_ROOT_HYGIENE_BYTES = 256 * 1024
_MAX_ROOT_HYGIENE_ENTRY_BYTES = 16 * 1024

JsonObject: TypeAlias = dict[str, Any]


class RunArtifactError(RuntimeError):
    """A run artifact is unsafe, stale, ambiguous, or unreadable."""


def _is_alias(info: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & reparse)


def _path_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(info.st_dev), int(info.st_ino), int(info.st_mode),
        int(getattr(info, "st_file_attributes", 0)),
    )


def _component_paths(path: Path) -> list[Path]:
    values = [path.absolute()]
    while values[-1].parent != values[-1]:
        values.append(values[-1].parent)
    return list(reversed(values))


def _windows_close_handle(handle: int) -> None:
    import ctypes as ctypes_module
    ctypes = cast(Any, ctypes_module)
    if not ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle)):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_normal_path(value: str) -> str:
    selected = value
    if selected.startswith("\\\\?\\UNC\\"):
        selected = "\\\\" + selected[8:]
    elif selected.startswith("\\\\?\\"):
        selected = selected[4:]
    return os.path.normcase(os.path.abspath(selected))


def _windows_pin_directory(path: Path) -> int:
    """Open a non-reparse directory without delete sharing.

    Holding one such handle for every ancestor prevents a path component
    from being renamed or replaced while a fallback operation is in flight.
    """
    import ctypes as ctypes_module
    ctypes = cast(Any, ctypes_module)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = (
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
    kernel.GetFileAttributesW.restype = ctypes.c_uint32
    kernel.GetFileAttributesW.argtypes = (ctypes.c_wchar_p,)
    kernel.GetFinalPathNameByHandleW.restype = ctypes.c_uint32
    kernel.GetFinalPathNameByHandleW.argtypes = (
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32)
    handle = kernel.CreateFileW(
        str(path), 0x0080, 0x00000003, None, 3,
        0x02000000 | 0x00200000, None)
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = kernel.GetFileAttributesW(str(path))
        if attributes == 0xFFFFFFFF or attributes & 0x400:
            raise RunArtifactError("portable artifact path is a reparse point")
        required = kernel.GetFinalPathNameByHandleW(
            ctypes.c_void_p(handle), None, 0, 0)
        if not required:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(required + 1)
        if not kernel.GetFinalPathNameByHandleW(
                ctypes.c_void_p(handle), buffer, len(buffer), 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if _windows_normal_path(buffer.value) != \
                _windows_normal_path(str(path)):
            raise RunArtifactError(
                "portable artifact directory resolved outside its path")
        return int(handle)
    except Exception:
        _windows_close_handle(int(handle))
        raise


@contextmanager
def _windows_private_security() -> Iterator[tuple[Any, Any]]:
    """Yield SECURITY_ATTRIBUTES with a protected owner/System-only DACL."""
    import ctypes as ctypes_module
    ctypes = cast(Any, ctypes_module)

    class _SecurityAttributes(ctypes_module.Structure):
        _fields_ = [
            ("length", ctypes_module.c_uint32),
            ("security_descriptor", ctypes_module.c_void_p),
            ("inherit_handle", ctypes_module.c_int),
        ]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    descriptor = ctypes.c_void_p()
    # OW is Windows' Owner Rights SID; SY is LocalSystem.  The protected DACL
    # has no inherited or broad user/group access.
    sddl = "D:P(A;;FA;;;OW)(A;;FA;;;SY)"
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        ctypes.c_wchar_p, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32))
    advapi.SetFileSecurityW.argtypes = (
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p)
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes), descriptor, False)
        yield descriptor, attributes
    finally:
        kernel.LocalFree(descriptor)


def _windows_apply_private(path: Path) -> None:
    """Protect one existing path with an owner/System-only DACL."""
    import ctypes as ctypes_module
    ctypes = cast(Any, ctypes_module)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.SetFileSecurityW.argtypes = (
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p)
    with _windows_private_security() as (descriptor, _attributes):
        if not advapi.SetFileSecurityW(
                str(path), 0x00000004 | 0x80000000, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())


def _windows_create_private_directory(path: Path) -> bool:
    """Create a directory with no broad-access window; return False if extant."""
    import ctypes as ctypes_module
    ctypes = cast(Any, ctypes_module)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateDirectoryW.argtypes = (ctypes.c_wchar_p, ctypes.c_void_p)
    with _windows_private_security() as (_descriptor, attributes):
        if kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
            return True
        error = ctypes.get_last_error()
        if error == 183:  # ERROR_ALREADY_EXISTS
            return False
        raise ctypes.WinError(error)


def _make_private(path: Path, *, directory: bool) -> None:
    if os.name == "nt":
        _windows_apply_private(path)
    else:
        os.chmod(path, 0o700 if directory else 0o600,
                 follow_symlinks=False)


class _PortableDirectory:
    """Pinned non-reparse directory for hosts without dir-fd primitives."""

    def __init__(self, path: Path, label: str):
        self.path = path.absolute()
        self._closed = False
        self._identities: list[tuple[Path, tuple[int, int, int, int]]] = []
        self._native_handles: list[int] = []
        try:
            for component in _component_paths(self.path):
                before = os.lstat(component)
                if _is_alias(before) or not stat.S_ISDIR(before.st_mode):
                    raise RunArtifactError(f"{label} contains an alias")
                if os.name == "nt":
                    self._native_handles.append(
                        _windows_pin_directory(component))
                after = os.lstat(component)
                if _path_identity(before) != _path_identity(after):
                    raise RunArtifactError(
                        f"{label} changed while its path was pinned")
                self._identities.append((component, _path_identity(after)))
        except Exception:
            self.close()
            raise

    def recheck(self) -> None:
        if self._closed:
            raise RunArtifactError("portable artifact directory is closed")
        for component, identity in self._identities:
            current = os.lstat(component)
            if (_is_alias(current) or not stat.S_ISDIR(current.st_mode) or
                    _path_identity(current) != identity):
                raise RunArtifactError(
                    "portable artifact directory identity changed")

    def close(self) -> None:
        while self._native_handles:
            handle = self._native_handles.pop()
            try:
                _windows_close_handle(handle)
            except OSError:
                pass
        self._closed = True


DirectoryHandle = int | _PortableDirectory


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RunArtifactError("run artifact value is not canonical JSON") \
            from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _bytes_digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _bounded_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_TEXT.fullmatch(value):
        raise RunArtifactError(f"{label} is invalid")
    return value


def _media_type(value: object) -> str:
    if (not isinstance(value, str) or not value or len(value) > 256 or
            value != value.strip() or any(ord(char) < 32 for char in value)):
        raise RunArtifactError("artifact media type is invalid")
    return value


def _digest_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise RunArtifactError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _private_mode(info: os.stat_result, label: str) -> None:
    if os.name == "nt":
        # The portable backend applies a protected owner/System-only DACL.
        return
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise RunArtifactError(f"{label} is not private")


def _assert_no_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if not os.path.lexists(current):
            continue
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise RunArtifactError("run artifact path is symlinked")


def _dir_fd_primitives_available() -> bool:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    replace_parameters: Mapping[str, inspect.Parameter]
    try:
        replace_parameters = inspect.signature(os.replace).parameters
    except (TypeError, ValueError):
        replace_parameters = {}
    return not (
            any(not hasattr(os, name) for name in required) or
            os.open not in os.supports_dir_fd or \
            not {"src_dir_fd", "dst_dir_fd"}.issubset(replace_parameters) or \
            os.unlink not in os.supports_dir_fd)


def _directory_info(directory: DirectoryHandle) -> os.stat_result:
    if isinstance(directory, _PortableDirectory):
        directory.recheck()
        return os.lstat(directory.path)
    return os.fstat(directory)


def _directory_path(directory: DirectoryHandle) -> Path:
    if not isinstance(directory, _PortableDirectory):
        raise RunArtifactError("directory path is unavailable for fd backend")
    directory.recheck()
    return directory.path


def _close_directory(directory: DirectoryHandle) -> None:
    if isinstance(directory, _PortableDirectory):
        directory.close()
    else:
        os.close(directory)


def _make_directory_private(directory: DirectoryHandle) -> None:
    if isinstance(directory, _PortableDirectory):
        _make_private(directory.path, directory=True)
        directory.recheck()
    else:
        os.fchmod(directory, 0o700)


def _fsync_directory(directory: DirectoryHandle) -> None:
    if isinstance(directory, _PortableDirectory):
        directory.recheck()
        if os.name != "nt":
            descriptor = os.open(directory.path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        # Windows' atomic replacement is durable at the file boundary.  A
        # directory FlushFileBuffers commonly returns ACCESS_DENIED; pinned
        # component handles still protect confinement during the operation.
        directory.recheck()
    else:
        os.fsync(directory)


def _open_directory(path: Path, label: str) -> DirectoryHandle:
    if not _dir_fd_primitives_available():
        return _PortableDirectory(path, label)
    absolute = path.absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(absolute.anchor, flags)
        for component in absolute.parts[1:]:
            next_descriptor = os.open(
                component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        info = os.fstat(descriptor)
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise RunArtifactError(f"{label} is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode):
        os.close(descriptor)
        raise RunArtifactError(f"{label} is not a directory")
    return descriptor


def _ensure_root(path: Path) -> DirectoryHandle:
    absolute = path.absolute()
    if absolute.parent == absolute:
        raise RunArtifactError("run artifact root cannot be a filesystem root")
    if not _dir_fd_primitives_available():
        parent = _open_directory(absolute.parent, "run artifact parent")
        try:
            if not isinstance(parent, _PortableDirectory):
                raise RunArtifactError(
                    "portable run artifact parent is unavailable")
            parent_path = _directory_path(parent)
            candidate = parent_path / absolute.name
            created = (_windows_create_private_directory(candidate)
                       if os.name == "nt" else None)
            try:
                if os.name != "nt":
                    os.mkdir(candidate, 0o700)
                    created = True
            except FileExistsError:
                created = False
            if created is False:
                existing = os.lstat(candidate)
                if _is_alias(existing) or not stat.S_ISDIR(existing.st_mode):
                    raise RunArtifactError(
                        "run artifact root is an alias or non-directory")
            _make_private(candidate, directory=True)
            parent.recheck()
        finally:
            _close_directory(parent)
        descriptor = _open_directory(absolute, "run artifact root")
        _private_mode(_directory_info(descriptor), "run artifact root")
        return descriptor
    _assert_no_symlink_components(absolute.parent)
    parent_fd = _open_directory(absolute.parent, "run artifact parent")
    if not isinstance(parent_fd, int):
        _close_directory(parent_fd)
        raise RunArtifactError("run artifact fd backend is unavailable")
    try:
        try:
            os.mkdir(absolute.name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        try:
            descriptor = os.open(
                absolute.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise RunArtifactError("run artifact root is unavailable") from exc
    finally:
        _close_directory(parent_fd)
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        _close_directory(descriptor)
        raise RunArtifactError("run artifact root is not a directory")
    _make_directory_private(descriptor)
    _private_mode(_directory_info(descriptor), "run artifact root")
    return descriptor


def _open_class(root_fd: DirectoryHandle, artifact_class: str, *,
                create: bool) -> DirectoryHandle:
    if artifact_class not in ARTIFACT_CLASSES:
        raise RunArtifactError("run artifact class is not allowlisted")
    if isinstance(root_fd, _PortableDirectory):
        root_fd.recheck()
        candidate = root_fd.path / artifact_class
        if create:
            created = (_windows_create_private_directory(candidate)
                       if os.name == "nt" else None)
            try:
                if os.name != "nt":
                    os.mkdir(candidate, 0o700)
                    created = True
            except FileExistsError:
                created = False
            if created is False:
                existing = os.lstat(candidate)
                if _is_alias(existing) or not stat.S_ISDIR(existing.st_mode):
                    raise RunArtifactError(
                        "run artifact class is an alias or non-directory")
            _make_private(candidate, directory=True)
        descriptor = _open_directory(
            candidate, f"run artifact class {artifact_class}")
        if create or os.name == "nt":
            _make_directory_private(descriptor)
        _private_mode(
            _directory_info(descriptor), "run artifact class directory")
        root_fd.recheck()
        return descriptor
    if create:
        try:
            os.mkdir(artifact_class, 0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
    try:
        descriptor = os.open(
            artifact_class,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
    except OSError as exc:
        raise RunArtifactError(
            f"run artifact class {artifact_class} is unavailable") from exc
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        _close_directory(descriptor)
        raise RunArtifactError("run artifact class is not a directory")
    if create:
        _make_directory_private(descriptor)
    _private_mode(_directory_info(descriptor),
                  "run artifact class directory")
    return descriptor


def _portable_entry_path(directory: _PortableDirectory, name: str) -> Path:
    if (not isinstance(name, str) or not name or "\0" in name or
            PurePath(name).name != name):
        raise RunArtifactError("portable artifact entry name is invalid")
    directory.recheck()
    return directory.path / name


def _portable_existing_fd(path: Path) -> int:
    if os.name != "nt":
        return os.open(path, os.O_RDONLY)
    return _windows_file_fd(
        path, desired_access=0x80000000, share_mode=0x00000001,
        creation=3, os_flags=os.O_RDONLY)


def _windows_file_fd(path: Path, *, desired_access: int, share_mode: int,
                     creation: int, os_flags: int) -> int:
    """Open one exact non-reparse file and transfer its handle to the CRT."""
    import ctypes as ctypes_module
    import msvcrt as msvcrt_module
    ctypes = cast(Any, ctypes_module)
    msvcrt = cast(Any, msvcrt_module)

    class _FileInformation(ctypes_module.Structure):
        _fields_ = [
            ("attributes", ctypes_module.c_uint32),
            ("creation_low", ctypes_module.c_uint32),
            ("creation_high", ctypes_module.c_uint32),
            ("access_low", ctypes_module.c_uint32),
            ("access_high", ctypes_module.c_uint32),
            ("write_low", ctypes_module.c_uint32),
            ("write_high", ctypes_module.c_uint32),
            ("volume_serial", ctypes_module.c_uint32),
            ("size_high", ctypes_module.c_uint32),
            ("size_low", ctypes_module.c_uint32),
            ("links", ctypes_module.c_uint32),
            ("index_high", ctypes_module.c_uint32),
            ("index_low", ctypes_module.c_uint32),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = (
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
    kernel.GetFileInformationByHandle.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(_FileInformation))
    kernel.GetFinalPathNameByHandleW.restype = ctypes.c_uint32
    kernel.GetFinalPathNameByHandleW.argtypes = (
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32)
    with _windows_private_security() as (_descriptor, attributes):
        handle = kernel.CreateFileW(
            str(path), desired_access, share_mode, ctypes.byref(attributes),
            creation, 0x00200000, None)
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        information = _FileInformation()
        if not kernel.GetFileInformationByHandle(
                ctypes.c_void_p(handle), ctypes.byref(information)):
            raise ctypes.WinError(ctypes.get_last_error())
        if information.attributes & (0x400 | 0x10):
            raise RunArtifactError("portable artifact file is a reparse point")
        required = kernel.GetFinalPathNameByHandleW(
            ctypes.c_void_p(handle), None, 0, 0)
        if not required:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(required + 1)
        if not kernel.GetFinalPathNameByHandleW(
                ctypes.c_void_p(handle), buffer, len(buffer), 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if _windows_normal_path(buffer.value) != \
                _windows_normal_path(str(path)):
            raise RunArtifactError(
                "portable artifact file resolved outside its path")
        return int(msvcrt.open_osfhandle(int(handle), os_flags))
    except Exception:
        _windows_close_handle(int(handle))
        raise


def _read_at(directory_fd: DirectoryHandle, name: str, *, maximum: int,
             label: str) -> bytes:
    before = None
    try:
        if isinstance(directory_fd, _PortableDirectory):
            path = _portable_entry_path(directory_fd, name)
            before = os.lstat(path)
            if _is_alias(before):
                raise RunArtifactError(f"{label} is an alias")
            descriptor = _portable_existing_fd(path)
        else:
            descriptor = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    except OSError as exc:
        raise RunArtifactError(f"{label} is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if before is not None and _path_identity(before) != \
                _path_identity(info):
            raise RunArtifactError(f"{label} changed while opening")
        if isinstance(directory_fd, _PortableDirectory):
            _make_private(path, directory=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_size > maximum):
            raise RunArtifactError(
                f"{label} is not a bounded exact-owned regular file")
        _private_mode(info, label)
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > maximum:
            raise RunArtifactError(f"{label} exceeds its byte bound")
        if isinstance(directory_fd, _PortableDirectory):
            directory_fd.recheck()
            after = os.lstat(path)
            if _path_identity(after) != _path_identity(info) or \
                    _is_alias(after):
                raise RunArtifactError(f"{label} changed while reading")
        return payload
    finally:
        os.close(descriptor)


def _atomic_write_at(directory_fd: DirectoryHandle, name: str,
                     payload: bytes) -> None:
    if not isinstance(payload, bytes) or len(payload) > _MAX_ARTIFACT_BYTES:
        raise RunArtifactError("run artifact payload exceeds its byte bound")
    temporary = f".{name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    descriptor = -1
    try:
        if isinstance(directory_fd, _PortableDirectory):
            temporary_path = _portable_entry_path(directory_fd, temporary)
            destination_path = _portable_entry_path(directory_fd, name)
            descriptor = (_windows_file_fd(
                temporary_path, desired_access=0x40000000,
                share_mode=0x00000001, creation=1, os_flags=os.O_WRONLY)
                if os.name == "nt" else os.open(
                    temporary_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            created = os.lstat(temporary_path)
            opened = os.fstat(descriptor)
            if (_is_alias(created) or not stat.S_ISREG(created.st_mode) or
                    created.st_nlink != 1 or
                    _path_identity(created) != _path_identity(opened)):
                raise RunArtifactError(
                    "portable artifact temporary file is ambiguous")
            _make_private(temporary_path, directory=False)
        else:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            offset += os.write(descriptor, view[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if isinstance(directory_fd, _PortableDirectory):
            directory_fd.recheck()
            if os.path.lexists(destination_path):
                destination = os.lstat(destination_path)
                if (_is_alias(destination) or
                        not stat.S_ISREG(destination.st_mode) or
                        destination.st_nlink != 1):
                    raise RunArtifactError(
                        "portable artifact destination is ambiguous")
            os.replace(temporary_path, destination_path)
            _make_private(destination_path, directory=False)
            replaced = os.lstat(destination_path)
            if (_is_alias(replaced) or not stat.S_ISREG(replaced.st_mode) or
                    replaced.st_nlink != 1 or replaced.st_size != len(payload)):
                raise RunArtifactError(
                    "portable artifact atomic replacement is ambiguous")
            directory_fd.recheck()
        else:
            os.replace(
                temporary, name, src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd)
        _fsync_directory(directory_fd)
    except OSError as exc:
        raise RunArtifactError("atomic run artifact write failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            if isinstance(directory_fd, _PortableDirectory):
                directory_fd.recheck()
                if os.path.lexists(temporary_path):
                    leftover = os.lstat(temporary_path)
                    if _is_alias(leftover) or not stat.S_ISREG(
                            leftover.st_mode) or leftover.st_nlink != 1:
                        raise RunArtifactError(
                            "portable artifact temporary cleanup is ambiguous")
                    os.unlink(temporary_path)
            else:
                os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _lock_handle(root_fd: DirectoryHandle) -> BinaryIO:
    try:
        if isinstance(root_fd, _PortableDirectory):
            path = _portable_entry_path(root_fd, ".run-artifacts.lock")
            if os.path.lexists(path):
                before = os.lstat(path)
                if (_is_alias(before) or not stat.S_ISREG(before.st_mode) or
                        before.st_nlink != 1):
                    raise RunArtifactError("run artifact lock is ambiguous")
            descriptor = (_windows_file_fd(
                path, desired_access=0xC0000000, share_mode=0x00000003,
                creation=4, os_flags=os.O_RDWR)
                if os.name == "nt" else os.open(
                    path, os.O_RDWR | os.O_CREAT, 0o600))
            _make_private(path, directory=False)
            after = os.lstat(path)
            if (_is_alias(after) or not stat.S_ISREG(after.st_mode) or
                    after.st_nlink != 1 or
                    _path_identity(after) !=
                    _path_identity(os.fstat(descriptor))):
                raise RunArtifactError("run artifact lock is ambiguous")
            root_fd.recheck()
        else:
            descriptor = os.open(
                ".run-artifacts.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=root_fd,
            )
    except OSError as exc:
        raise RunArtifactError("run artifact lock is unavailable") from exc
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(descriptor)
        raise RunArtifactError("run artifact lock is ambiguous")
    try:
        _private_mode(info, "run artifact lock")
    except RunArtifactError:
        os.close(descriptor)
        raise
    return os.fdopen(descriptor, "a+b", closefd=True)


def _entry_exists(directory: DirectoryHandle, name: str) -> bool:
    if isinstance(directory, _PortableDirectory):
        path = _portable_entry_path(directory, name)
        if not os.path.lexists(path):
            directory.recheck()
            return False
        info = os.lstat(path)
        if _is_alias(info):
            raise RunArtifactError("portable artifact entry is an alias")
        directory.recheck()
        return True
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        raise RunArtifactError("run artifact entry is an alias")
    return True


def _list_directory(directory: DirectoryHandle) -> list[str]:
    if isinstance(directory, _PortableDirectory):
        directory.recheck()
        values = os.listdir(directory.path)
        directory.recheck()
        return values
    return os.listdir(directory)


def _lock(handle: BinaryIO) -> None:
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except ImportError:  # pragma: no cover - Windows compatibility path
        import msvcrt as msvcrt_module
        msvcrt = cast(Any, msvcrt_module)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)


def _unlock(handle: BinaryIO) -> None:
    try:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except ImportError:  # pragma: no cover - Windows compatibility path
        import msvcrt as msvcrt_module
        msvcrt = cast(Any, msvcrt_module)
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def _manifest_lock(root_fd: DirectoryHandle) -> Iterator[None]:
    with _lock_handle(root_fd) as handle:
        _lock(handle)
        try:
            yield
        finally:
            _unlock(handle)


def create_binding(*, repository_id: str, run_id: str, stage_id: str,
                   stage_instance_id: str, candidate: Mapping[str, object],
                   settings_digest: str,
                   source_fingerprint: str) -> JsonObject:
    """Close the complete identity repeated by every artifact and event."""
    if not isinstance(candidate, Mapping):
        raise RunArtifactError("working candidate must be an object")
    candidate_value = copy.deepcopy(dict(candidate))
    if (not candidate_value or
            len(_canonical(candidate_value)) > _MAX_CANDIDATE_BYTES):
        raise RunArtifactError("working candidate is empty or unbounded")
    _digest_text(candidate_value.get("fingerprint"),
                 "working candidate fingerprint")
    material = {
        "schema": BINDING_SCHEMA,
        "repository_id": _bounded_text(repository_id, "repository id"),
        "run_id": _bounded_text(run_id, "run id"),
        "stage_id": _bounded_text(stage_id, "stage id"),
        "stage_instance_id": _bounded_text(
            stage_instance_id, "stage instance id"),
        "candidate": candidate_value,
        "settings_digest": _digest_text(
            settings_digest, "settings digest"),
        "source_fingerprint": _digest_text(
            source_fingerprint, "source fingerprint"),
    }
    return {**material, "fingerprint": _digest(material)}


def validate_binding(value: object) -> JsonObject:
    if not isinstance(value, Mapping):
        raise RunArtifactError("run artifact binding must be an object")
    expected_fields = {
        "schema", "repository_id", "run_id", "stage_id",
        "stage_instance_id", "candidate", "settings_digest",
        "source_fingerprint", "fingerprint",
    }
    if set(value) != expected_fields or value.get("schema") != BINDING_SCHEMA:
        raise RunArtifactError("run artifact binding shape is invalid")
    rebuilt = create_binding(
        repository_id=cast(str, value.get("repository_id")),
        run_id=cast(str, value.get("run_id")),
        stage_id=cast(str, value.get("stage_id")),
        stage_instance_id=cast(str, value.get("stage_instance_id")),
        candidate=cast(Mapping[str, object], value.get("candidate")),
        settings_digest=cast(str, value.get("settings_digest")),
        source_fingerprint=cast(str, value.get("source_fingerprint")),
    )
    if dict(value) != rebuilt:
        raise RunArtifactError("run artifact binding fingerprint is stale")
    return rebuilt


def manifest_locator_reference() -> JsonObject:
    """Return the only run-state field: an immutable relative locator."""
    return {
        "schema": MANIFEST_REFERENCE_SCHEMA,
        "locator": MANIFEST_LOCATOR,
    }


def validate_manifest_locator_reference(value: object) -> JsonObject:
    expected = manifest_locator_reference()
    if not isinstance(value, Mapping) or dict(value) != expected:
        raise RunArtifactError("run artifact manifest locator is invalid")
    return expected


def _manifest_digest(value: Mapping[str, object]) -> str:
    return _digest({key: copy.deepcopy(item) for key, item in value.items()
                    if key != "manifest_digest"})


def _validate_entry(entry: object, *, artifact_class: str,
                    binding: Mapping[str, object]) -> JsonObject:
    if not isinstance(entry, Mapping):
        raise RunArtifactError("run artifact entry is invalid")
    required = {
        "schema", "class", "sequence", "locator", "media_type", "bytes",
        "sha256", "binding", "metadata", "fingerprint",
    }
    if set(entry) != required or entry.get("schema") not in {
            ARTIFACT_SCHEMA, ACTIVITY_SCHEMA}:
        raise RunArtifactError("run artifact entry shape is invalid")
    if (entry.get("class") != artifact_class or
            entry.get("binding") != dict(binding)):
        raise RunArtifactError("run artifact entry binding is stale")
    sequence = entry.get("sequence")
    if (isinstance(sequence, bool) or not isinstance(sequence, int) or
            sequence < 1):
        raise RunArtifactError("run artifact sequence is invalid")
    expected_name = f"{sequence:08d}-{entry.get('fingerprint')}.json"
    if (not _OBJECT_NAME.fullmatch(expected_name) or
            entry.get("locator") != f"{artifact_class}/{expected_name}"):
        raise RunArtifactError("run artifact locator is invalid")
    _media_type(entry.get("media_type"))
    byte_count = entry.get("bytes")
    if (isinstance(byte_count, bool) or not isinstance(byte_count, int) or
            byte_count < 0 or byte_count > _MAX_ARTIFACT_BYTES):
        raise RunArtifactError("run artifact byte count is invalid")
    _digest_text(entry.get("sha256"), "run artifact digest")
    metadata = entry.get("metadata")
    if not isinstance(metadata, Mapping) or \
            len(_canonical(metadata)) > _MAX_METADATA_BYTES:
        raise RunArtifactError("run artifact metadata is invalid")
    material = {key: copy.deepcopy(item) for key, item in entry.items()
                if key not in {"locator", "fingerprint"}}
    if entry.get("fingerprint") != _digest(material):
        raise RunArtifactError("run artifact fingerprint is stale")
    if ((artifact_class == "agent-activity") !=
            (entry.get("schema") == ACTIVITY_SCHEMA)):
        raise RunArtifactError("agent activity is stored in the wrong class")
    return copy.deepcopy(dict(entry))


def _validate_manifest(value: object) -> JsonObject:
    if not isinstance(value, Mapping):
        raise RunArtifactError("run artifact manifest must be an object")
    expected_fields = {
        "schema", "binding", "classes", "revision", "created_at_ns",
        "manifest_digest",
    }
    if set(value) != expected_fields or value.get("schema") != MANIFEST_SCHEMA:
        raise RunArtifactError("run artifact manifest shape is invalid")
    binding = validate_binding(value.get("binding"))
    revision = value.get("revision")
    if (isinstance(revision, bool) or not isinstance(revision, int) or
            revision < 0):
        raise RunArtifactError("run artifact manifest revision is invalid")
    created = value.get("created_at_ns")
    if isinstance(created, bool) or not isinstance(created, int) or created < 1:
        raise RunArtifactError("run artifact creation time is invalid")
    classes = value.get("classes")
    if not isinstance(classes, Mapping) or set(classes) != set(ARTIFACT_CLASSES):
        raise RunArtifactError("run artifact classes are incomplete")
    total = 0
    for artifact_class in ARTIFACT_CLASSES:
        row = classes.get(artifact_class)
        if (not isinstance(row, Mapping) or set(row) != {
                "schema", "class", "locator", "entries"} or
                row.get("schema") != CLASS_SCHEMA or
                row.get("class") != artifact_class or
                row.get("locator") != artifact_class or
                not isinstance(row.get("entries"), list)):
            raise RunArtifactError("run artifact class manifest is invalid")
        entries = row["entries"]
        total += len(entries)
        if total > _MAX_ENTRIES:
            raise RunArtifactError("run artifact manifest exceeds entry bound")
        checked = [_validate_entry(
            entry, artifact_class=artifact_class, binding=binding)
            for entry in entries]
        if [entry["sequence"] for entry in checked] != \
                list(range(1, len(checked) + 1)):
            raise RunArtifactError("run artifact class sequence is not append-only")
        if len({entry["fingerprint"] for entry in checked}) != len(checked):
            raise RunArtifactError("run artifact class contains duplicate entries")
    if value.get("manifest_digest") != _manifest_digest(value):
        raise RunArtifactError("run artifact manifest is invalid or tampered")
    return copy.deepcopy(dict(value))


def _load_manifest_at(root_fd: DirectoryHandle) -> JsonObject:
    payload = _read_at(
        root_fd, MANIFEST_NAME, maximum=_MAX_MANIFEST_BYTES,
        label="run artifact manifest")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RunArtifactError("run artifact manifest is unavailable") from exc
    return _validate_manifest(value)


def _save_manifest_at(root_fd: DirectoryHandle,
                      manifest: JsonObject) -> JsonObject:
    value = copy.deepcopy(manifest)
    value["manifest_digest"] = _manifest_digest(value)
    payload = _canonical(value) + b"\n"
    if len(payload) > _MAX_MANIFEST_BYTES:
        raise RunArtifactError("run artifact manifest exceeds its byte bound")
    _atomic_write_at(root_fd, MANIFEST_NAME, payload)
    return value


def create_manifest(root: str | os.PathLike[str], *, binding: Mapping[str, object]) \
        -> JsonObject:
    """Create one private manifest and all seven separate class roots."""
    checked_binding = validate_binding(binding)
    root_path = Path(root).absolute()
    root_fd = _ensure_root(root_path)
    try:
        with _manifest_lock(root_fd):
            if _entry_exists(root_fd, MANIFEST_NAME):
                raise RunArtifactError("run artifact manifest already exists")
            classes = {}
            for artifact_class in ARTIFACT_CLASSES:
                class_fd = _open_class(root_fd, artifact_class, create=True)
                _close_directory(class_fd)
                classes[artifact_class] = {
                    "schema": CLASS_SCHEMA,
                    "class": artifact_class,
                    "locator": artifact_class,
                    "entries": [],
                }
            manifest = {
                "schema": MANIFEST_SCHEMA,
                "binding": checked_binding,
                "classes": classes,
                "revision": 0,
                "created_at_ns": time.time_ns(),
            }
            return _save_manifest_at(root_fd, manifest)
    finally:
        _close_directory(root_fd)


def load_manifest(root: str | os.PathLike[str]) -> JsonObject:
    root_fd = _open_directory(Path(root).absolute(), "run artifact root")
    try:
        if os.name == "nt":
            _make_directory_private(root_fd)
        _private_mode(_directory_info(root_fd), "run artifact root")
        return _load_manifest_at(root_fd)
    finally:
        _close_directory(root_fd)


def _payload_bytes(payload: object) -> tuple[bytes, str]:
    if isinstance(payload, bytes):
        value, media_type = payload, "application/octet-stream"
    elif isinstance(payload, str):
        value, media_type = payload.encode("utf-8"), "text/plain; charset=utf-8"
    elif isinstance(payload, Mapping) or isinstance(payload, Sequence):
        if isinstance(payload, (str, bytes, bytearray)):
            raise RunArtifactError("run artifact payload is invalid")
        value, media_type = _canonical(payload) + b"\n", "application/json"
    else:
        raise RunArtifactError("run artifact payload type is unsupported")
    if len(value) > _MAX_ARTIFACT_BYTES:
        raise RunArtifactError("run artifact payload exceeds its byte bound")
    return value, media_type


def _publish(root: Path, artifact_class: str, payload: bytes, *,
             media_type: str, metadata: Mapping[str, object],
             schema: str) -> JsonObject:
    if artifact_class not in ARTIFACT_CLASSES:
        raise RunArtifactError("run artifact class is not allowlisted")
    if schema == ACTIVITY_SCHEMA and artifact_class != "agent-activity":
        raise RunArtifactError("activity event class is invalid")
    if not isinstance(metadata, Mapping) or \
            len(_canonical(metadata)) > _MAX_METADATA_BYTES:
        raise RunArtifactError("run artifact metadata exceeds its byte bound")
    checked_media_type = _media_type(media_type)
    root_fd = _open_directory(root, "run artifact root")
    try:
        if os.name == "nt":
            _make_directory_private(root_fd)
        _private_mode(_directory_info(root_fd), "run artifact root")
        with _manifest_lock(root_fd):
            manifest = _load_manifest_at(root_fd)
            entries = manifest["classes"][artifact_class]["entries"]
            if artifact_class == "telemetry" and \
                    metadata.get("kind") == "root-hygiene":
                rows = [row for row in entries
                        if (row.get("metadata") or {}).get("kind") ==
                        "root-hygiene"]
                matching = [row for row in rows
                            if (row.get("metadata") or {}).get(
                                "receipt_fingerprint") == metadata.get(
                                    "receipt_fingerprint")]
                if len(matching) == 1:
                    if matching[0].get("sha256") != _bytes_digest(payload):
                        raise RunArtifactError(
                            "root hygiene fingerprint is ambiguous")
                    return copy.deepcopy(matching[0])
                if len(matching) > 1:
                    raise RunArtifactError(
                        "root hygiene retention is ambiguous")
                if len(rows) >= _MAX_ROOT_HYGIENE_ENTRIES:
                    raise RunArtifactError(
                        "root hygiene retention exceeds its row bound")
                if sum(int(row.get("bytes") or 0) for row in rows) + \
                        len(payload) > _MAX_ROOT_HYGIENE_BYTES:
                    raise RunArtifactError(
                        "root hygiene retention exceeds its group byte bound")
            if len(entries) >= _MAX_ENTRIES:
                raise RunArtifactError("run artifact class exceeds entry bound")
            sequence = len(entries) + 1
            material = {
                "schema": schema,
                "class": artifact_class,
                "sequence": sequence,
                "media_type": checked_media_type,
                "bytes": len(payload),
                "sha256": _bytes_digest(payload),
                "binding": copy.deepcopy(manifest["binding"]),
                "metadata": copy.deepcopy(dict(metadata)),
            }
            fingerprint = _digest(material)
            name = f"{sequence:08d}-{fingerprint}.json"
            entry = {
                **material,
                "locator": f"{artifact_class}/{name}",
                "fingerprint": fingerprint,
            }
            class_fd = _open_class(root_fd, artifact_class, create=False)
            try:
                if _entry_exists(class_fd, name):
                    raise RunArtifactError(
                        "run artifact sequence target already exists")
                _atomic_write_at(class_fd, name, payload)
            finally:
                _close_directory(class_fd)
            entries.append(entry)
            manifest["revision"] += 1
            _save_manifest_at(root_fd, manifest)
            return copy.deepcopy(entry)
    finally:
        _close_directory(root_fd)


def publish_artifact(root: str | os.PathLike[str], artifact_class: str,
                     payload: object, *,
                     metadata: Mapping[str, object] | None = None,
                     media_type: str | None = None) -> JsonObject:
    """Atomically publish one bounded immutable object into an allowed class."""
    if artifact_class == "agent-activity":
        raise RunArtifactError(
            "agent activity must use the append-only event API")
    value, inferred_media_type = _payload_bytes(payload)
    return _publish(
        Path(root).absolute(), artifact_class, value,
        media_type=media_type or inferred_media_type,
        metadata=dict(metadata or {}), schema=ARTIFACT_SCHEMA)


def publish_root_hygiene(
        root: str | os.PathLike[str], receipt: Mapping[str, object]) -> JsonObject:
    """Retain one canonical root seal under its explicit bounded policy."""
    from taskplane import wave_metrics

    checked = wave_metrics.validate_root_hygiene(receipt)
    value, _ = _payload_bytes(checked)
    if len(value) > _MAX_ROOT_HYGIENE_ENTRY_BYTES:
        raise RunArtifactError("root hygiene receipt exceeds its byte bound")
    binding = load_manifest(root)["binding"]
    if checked["candidate"]["source_sha"] != \
            binding["candidate"].get("revision"):
        raise RunArtifactError("root hygiene receipt belongs to another candidate")
    return publish_artifact(
        root, "telemetry", checked,
        metadata={"kind": "root-hygiene",
                  "receipt_fingerprint": checked["fingerprint"]})


def _activity_metadata(event: Mapping[str, object]) -> JsonObject:
    required = {
        "event_type", "agent_attempt_id", "worker_id", "task_id", "lens",
        "occurred_at_ns", "details", "usage_reference",
        "evidence_references",
    }
    event_type = event.get("event_type")
    if (set(event) != required or not isinstance(event_type, str) or
            event_type not in ACTIVITY_EVENT_TYPES):
        raise RunArtifactError("agent activity event shape is invalid")
    for field in ("agent_attempt_id", "worker_id", "task_id", "lens"):
        _bounded_text(event.get(field), f"agent activity {field}")
    occurred = event.get("occurred_at_ns")
    if isinstance(occurred, bool) or not isinstance(occurred, int) or occurred < 1:
        raise RunArtifactError("agent activity timestamp is invalid")
    details = event.get("details")
    if not isinstance(details, Mapping):
        raise RunArtifactError("agent activity details must be an object")
    usage = event.get("usage_reference")
    if usage is not None and not isinstance(usage, Mapping):
        raise RunArtifactError("agent activity usage reference is invalid")
    evidence = event.get("evidence_references")
    if (not isinstance(evidence, list) or
            any(not isinstance(item, Mapping) for item in evidence)):
        raise RunArtifactError("agent activity evidence references are invalid")
    if event.get("event_type") == "terminal" and not str(
            details.get("outcome") or "").strip():
        raise RunArtifactError("terminal activity needs an outcome")
    expected_outcome = _DISTINCT_ACTIVITY_OUTCOMES.get(event_type)
    if expected_outcome is not None and details.get("outcome") != \
            expected_outcome:
        raise RunArtifactError(
            f"{event.get('event_type')} activity needs outcome "
            f"{expected_outcome}")
    if len(_canonical(event)) > _MAX_ACTIVITY_BYTES:
        raise RunArtifactError("agent activity event exceeds its byte bound")
    return copy.deepcopy(dict(event))


def append_activity(root: str | os.PathLike[str], *, event_type: str,
                    agent_attempt_id: str, worker_id: str, task_id: str,
                    lens: str, details: Mapping[str, object] | None = None,
                    usage_reference: Mapping[str, object] | None = None,
                    evidence_references: Sequence[Mapping[str, object]] = (),
                    occurred_at_ns: int | None = None) -> JsonObject:
    """Append one ordered candidate-bound worker activity event."""
    event = _activity_metadata({
        "event_type": event_type,
        "agent_attempt_id": agent_attempt_id,
        "worker_id": worker_id,
        "task_id": task_id,
        "lens": lens,
        "occurred_at_ns": occurred_at_ns or time.time_ns(),
        "details": copy.deepcopy(dict(details or {})),
        "usage_reference": (None if usage_reference is None else
                            copy.deepcopy(dict(usage_reference))),
        "evidence_references": [copy.deepcopy(dict(item))
                                for item in evidence_references],
    })
    root_path = Path(root).absolute()
    binding = load_manifest(root_path)["binding"]
    payload = _canonical({
        "schema": ACTIVITY_SCHEMA,
        "binding": binding,
        "event": event,
    }) + b"\n"
    return _publish(
        root_path, "agent-activity", payload,
        media_type="application/json", metadata=event,
        schema=ACTIVITY_SCHEMA)


def _read_entry(root_fd: DirectoryHandle,
                entry: Mapping[str, object]) -> bytes:
    locator = PurePath(str(entry.get("locator") or ""))
    if (locator.is_absolute() or len(locator.parts) != 2 or
            locator.parts[0] != entry.get("class") or
            not _OBJECT_NAME.fullmatch(locator.parts[1])):
        raise RunArtifactError("run artifact locator escapes its class")
    class_fd = _open_class(root_fd, str(entry["class"]), create=False)
    try:
        payload = _read_at(
            class_fd, locator.parts[1], maximum=_MAX_ARTIFACT_BYTES,
            label="run artifact object")
    finally:
        _close_directory(class_fd)
    if (len(payload) != entry.get("bytes") or
            _bytes_digest(payload) != entry.get("sha256")):
        raise RunArtifactError("run artifact object digest is stale")
    if entry.get("schema") == ACTIVITY_SCHEMA:
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RunArtifactError("agent activity event is unreadable") from exc
        if (not isinstance(value, Mapping) or
                value.get("schema") != ACTIVITY_SCHEMA or
                value.get("binding") != entry.get("binding") or
                value.get("event") != entry.get("metadata")):
            raise RunArtifactError("agent activity event metadata is stale")
        _activity_metadata(value["event"])
    return payload


def verify_manifest(root: str | os.PathLike[str], *,
                    expected_binding: Mapping[str, object] | None = None) \
        -> JsonObject:
    """Read every indexed object, reject aliases/orphans, and seal a proof."""
    root_path = Path(root).absolute()
    root_fd = _open_directory(root_path, "run artifact root")
    try:
        if os.name == "nt":
            _make_directory_private(root_fd)
        _private_mode(_directory_info(root_fd), "run artifact root")
        with _manifest_lock(root_fd):
            manifest = _load_manifest_at(root_fd)
            if expected_binding is not None and manifest["binding"] != \
                    validate_binding(expected_binding):
                raise RunArtifactError(
                    "run artifact manifest belongs to another binding")
            counts: dict[str, int] = {}
            total_bytes = 0
            for artifact_class in ARTIFACT_CLASSES:
                entries = manifest["classes"][artifact_class]["entries"]
                class_fd = _open_class(root_fd, artifact_class, create=False)
                try:
                    actual = sorted(
                        name for name in _list_directory(class_fd)
                        if name not in {".", ".."})
                finally:
                    _close_directory(class_fd)
                expected = sorted(PurePath(entry["locator"]).name
                                  for entry in entries)
                if actual != expected:
                    raise RunArtifactError(
                        f"run artifact class {artifact_class} has unindexed files")
                for entry in entries:
                    total_bytes += len(_read_entry(root_fd, entry))
                counts[artifact_class] = len(entries)
            material = {
                "schema": VERIFICATION_SCHEMA,
                "binding_fingerprint": manifest["binding"]["fingerprint"],
                "manifest_digest": manifest["manifest_digest"],
                "manifest_revision": manifest["revision"],
                "class_counts": counts,
                "artifact_count": sum(counts.values()),
                "bytes": total_bytes,
                "readable": True,
                "zero_unindexed_files": True,
            }
            return {**material, "fingerprint": _digest(material)}
    finally:
        _close_directory(root_fd)


def durable_reference(root: str | os.PathLike[str]) -> JsonObject:
    """Return cleanup's immutable locator/reference, never lifecycle state."""
    root_path = Path(root).absolute()
    manifest = load_manifest(root_path)
    verification = verify_manifest(root_path)
    material = {
        "schema": MANIFEST_REFERENCE_SCHEMA,
        "root": str(root_path),
        "manifest": str(root_path / MANIFEST_NAME),
        "repository_id": manifest["binding"]["repository_id"],
        "run_id": manifest["binding"]["run_id"],
        "settings_digest": manifest["binding"]["settings_digest"],
        "binding_fingerprint": manifest["binding"]["fingerprint"],
        "verification_fingerprint": verification["fingerprint"],
    }
    return {
        **material,
        "reference_fingerprint": _digest(material),
    }


def validate_durable_reference(value: object) -> JsonObject:
    if not isinstance(value, Mapping):
        raise RunArtifactError("durable run artifact reference is invalid")
    required = {
        "schema", "root", "manifest", "repository_id", "run_id",
        "settings_digest", "binding_fingerprint", "reference_fingerprint",
        "verification_fingerprint",
    }
    if set(value) != required or value.get("schema") != \
            MANIFEST_REFERENCE_SCHEMA:
        raise RunArtifactError("durable run artifact reference shape is invalid")
    root = Path(str(value.get("root") or "")).absolute()
    if str(root / MANIFEST_NAME) != value.get("manifest"):
        raise RunArtifactError("durable run artifact manifest locator is stale")
    _bounded_text(value.get("repository_id"), "repository id")
    _bounded_text(value.get("run_id"), "run id")
    _digest_text(value.get("settings_digest"), "settings digest")
    _digest_text(value.get("binding_fingerprint"), "binding fingerprint")
    _digest_text(value.get("verification_fingerprint"),
                 "verification fingerprint")
    material = {key: copy.deepcopy(item) for key, item in value.items()
                if key != "reference_fingerprint"}
    if value.get("reference_fingerprint") != _digest(material):
        raise RunArtifactError("durable run artifact reference is stale")
    return copy.deepcopy(dict(value))


def verify_durable_reference(value: object) -> JsonObject:
    """Authenticate a stored cleanup reference against current durable bytes."""
    reference = validate_durable_reference(value)
    root = Path(reference["root"])
    manifest = load_manifest(root)
    binding = manifest["binding"]
    if (binding["repository_id"] != reference["repository_id"] or
            binding["run_id"] != reference["run_id"] or
            binding["settings_digest"] != reference["settings_digest"] or
            binding["fingerprint"] != reference["binding_fingerprint"]):
        raise RunArtifactError("durable run artifact reference is foreign")
    return verify_manifest(root, expected_binding=binding)


__all__ = [
    "ACTIVITY_EVENT_TYPES",
    "ARTIFACT_CLASSES",
    "MANIFEST_LOCATOR",
    "MANIFEST_NAME",
    "MANIFEST_REFERENCE_SCHEMA",
    "MANIFEST_SCHEMA",
    "RunArtifactError",
    "append_activity",
    "create_binding",
    "create_manifest",
    "durable_reference",
    "load_manifest",
    "manifest_locator_reference",
    "publish_artifact",
    "publish_root_hygiene",
    "validate_binding",
    "validate_durable_reference",
    "validate_manifest_locator_reference",
    "verify_durable_reference",
    "verify_manifest",
]
