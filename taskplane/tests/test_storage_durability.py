"""Directory durability requests native write access and fails closed."""
import ctypes
import errno
import json
import os
from pathlib import PurePosixPath, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from taskplane import primitives


@pytest.fixture
def windows_kernel(monkeypatch):
    # Keep the fake available on every OS so CI always covers the Win32
    # permission contract; the native publication test below uses the real OS.
    state = SimpleNamespace(error=0, access=0, handle=0x123456789)

    def create(path, access, share, security, disposition, flags, template):
        state.access = access
        return state.handle

    def flush(handle):
        if not state.access & 0x40000000:  # GENERIC_WRITE is mandatory.
            state.error = 5  # ERROR_ACCESS_DENIED
            return 0
        return 1

    def win_error(code):
        error = OSError(f"fixture WinError {code}")
        error.winerror = code
        return error

    kernel = SimpleNamespace(CreateFileW=Mock(side_effect=create),
                             FlushFileBuffers=Mock(side_effect=flush),
                             CloseHandle=Mock(return_value=1))
    loader = Mock(return_value=kernel)
    monkeypatch.setattr(ctypes, "WinDLL", loader, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state.error, raising=False)
    monkeypatch.setattr(ctypes, "WinError", win_error, raising=False)
    return kernel, state, loader


def test_windows_directory_flush_uses_writable_shared_directory_handle(windows_kernel):
    kernel, state, loader = windows_kernel
    primitives._flush_windows_directory("directory")
    loader.assert_called_once_with("kernel32", use_last_error=True)
    kernel.CreateFileW.assert_called_once_with(
        "directory", 0x40000000, 7, None, 3, 0x02000000, None)
    kernel.FlushFileBuffers.assert_called_once_with(state.handle)
    kernel.CloseHandle.assert_called_once_with(state.handle)
    assert kernel.CreateFileW.restype is ctypes.c_void_p
    assert kernel.FlushFileBuffers.argtypes == (ctypes.c_void_p,)
    assert kernel.CloseHandle.argtypes == (ctypes.c_void_p,)


@pytest.mark.parametrize("operation,code", [
    ("open", 5), ("open", 32),  # access denied, sharing violation
    ("flush", 5), ("flush", 1), ("flush", 50), ("flush", 1117),
    ("close", 6),  # invalid handle
])
def test_windows_directory_flush_never_acknowledges_api_failure(windows_kernel, operation, code):
    kernel, state, _ = windows_kernel
    state.error = code
    if operation == "open":
        kernel.CreateFileW.side_effect = None
        kernel.CreateFileW.return_value = ctypes.c_void_p(-1).value
    elif operation == "flush":
        kernel.FlushFileBuffers.side_effect = None
        kernel.FlushFileBuffers.return_value = 0
    else:
        kernel.CloseHandle.return_value = 0
    with pytest.raises(OSError) as caught:
        primitives._flush_windows_directory("directory")
    assert caught.value.winerror == code
    if operation == "open":
        kernel.FlushFileBuffers.assert_not_called()
        kernel.CloseHandle.assert_not_called()
    else:
        kernel.FlushFileBuffers.assert_called_once_with(state.handle)
        kernel.CloseHandle.assert_called_once_with(state.handle)


@pytest.mark.parametrize("boundary", ["open", "fsync"])
@pytest.mark.parametrize("native_failure", [False, True])
def test_windows_directory_fallback_requires_successful_native_flush(monkeypatch, boundary, native_failure):
    platform = SimpleNamespace(name="nt", O_RDONLY=os.O_RDONLY,
                               open=Mock(return_value=123), fsync=Mock(), close=Mock())
    getattr(platform, boundary).side_effect = PermissionError(errno.EACCES, "directory handle")
    native = Mock(side_effect=OSError("unsupported directory flush") if native_failure else None)
    monkeypatch.setattr(primitives, "os", platform)
    monkeypatch.setattr(primitives, "_flush_windows_directory", native)
    if native_failure:
        with pytest.raises(OSError, match="unsupported directory flush"):
            primitives._fsync_directory("directory")
    else:
        primitives._fsync_directory("directory")
    native.assert_called_once_with("directory")
    if boundary == "open":
        platform.close.assert_not_called()
    else:
        platform.close.assert_called_once_with(123)


@pytest.mark.parametrize("platform_name", ["posix", "nt"])
def test_directory_fsync_io_error_is_not_retried_or_suppressed(monkeypatch, platform_name):
    failure = OSError(errno.EIO, "directory I/O failure")
    platform = SimpleNamespace(name=platform_name, O_RDONLY=os.O_RDONLY,
                               open=Mock(return_value=123), fsync=Mock(side_effect=failure), close=Mock())
    native = Mock()
    monkeypatch.setattr(primitives, "os", platform)
    monkeypatch.setattr(primitives, "_flush_windows_directory", native)
    with pytest.raises(OSError) as caught:
        primitives._fsync_directory("directory")
    assert caught.value is failure
    native.assert_not_called()
    platform.close.assert_called_once_with(123)


def test_native_directory_durability_for_nested_creation_and_replacement(tmp_path):
    # This is deliberately not mocked or skipped on Windows: the real native
    # API must acknowledge directory creation and publication on that runner.
    target = tmp_path / "new" / "nested" / "state.json"
    primitives.atomic_write_bytes(str(target), b'{"revision":1}')
    assert target.read_bytes() == b'{"revision":1}'
    primitives.atomic_write_json(str(target), {"revision": 2})
    assert json.loads(target.read_bytes()) == {"revision": 2}
    primitives.atomic_json(target, {"revision": 3}, strict_directory_sync=True)
    assert json.loads(target.read_bytes()) == {"revision": 3}
    primitives._durable_makedirs(str(target.parent.resolve()), sync_existing=True)
    assert [entry.name for entry in target.parent.iterdir()] == ["state.json"]


@pytest.mark.parametrize("path_type,target", [
    (PureWindowsPath, "D:\\state\\nested"),
    (PureWindowsPath, "\\\\server\\share\\state\\nested"),
    (PureWindowsPath, "\\\\?\\D:\\state\\nested"),
    (PureWindowsPath, "\\\\?\\UNC\\server\\share\\state\\nested"),
    (PurePosixPath, "/state/nested"),
])
def test_retry_directory_ancestry_validates_and_flushes_anchors_without_creating_them(
        monkeypatch, path_type, target):
    target = path_type(target)
    anchor = path_type(target.anchor)
    present = {anchor, target.parent}
    events = []

    def mkdir(path, *, mode):
        events.append(("mkdir", str(path)))
        if path == anchor:
            raise PermissionError(errno.EACCES, "cannot create filesystem anchor")
        if path in present:
            raise FileExistsError(errno.EEXIST, "already exists")
        assert path.parent in present and mode == 0o700
        present.add(path)

    def identity(path):
        events.append(("identity", str(path)))
        assert path_type(path) in present
        return (1, 1)

    monkeypatch.setattr(primitives, "Path", path_type)
    monkeypatch.setattr(primitives, "os", SimpleNamespace(
        path=SimpleNamespace(abspath=lambda path: path), mkdir=mkdir))
    monkeypatch.setattr(primitives, "_durable_directory_identity", identity)
    monkeypatch.setattr(primitives, "_fsync_directory", lambda path: events.append(("sync", path)))
    primitives._durable_makedirs(str(target), mode=0o700, sync_existing=True)
    assert ("mkdir", str(anchor)) not in events
    assert events[:4] == [("identity", str(anchor)), ("sync", str(anchor)),
                          ("sync", str(anchor)), ("identity", str(anchor))]
    assert events[-5:] == [("mkdir", str(target)), ("identity", str(target)),
                           ("sync", str(target)), ("sync", str(target.parent)),
                           ("identity", str(target))]


@pytest.mark.parametrize("boundary", ["child_creation", "anchor_flush"])
def test_retry_directory_ancestry_preserves_access_and_durability_errors(monkeypatch, boundary):
    target = PureWindowsPath("D:\\state")
    failure = PermissionError(errno.EACCES, "fixture access denied")
    mkdir = Mock(side_effect=failure if boundary == "child_creation" else None)
    flush = Mock(side_effect=failure if boundary == "anchor_flush" else None)
    monkeypatch.setattr(primitives, "Path", PureWindowsPath)
    monkeypatch.setattr(primitives, "os", SimpleNamespace(
        path=SimpleNamespace(abspath=lambda path: path), mkdir=mkdir))
    monkeypatch.setattr(primitives, "_durable_directory_identity", lambda path: (1, 1))
    monkeypatch.setattr(primitives, "_fsync_directory", flush)
    with pytest.raises(PermissionError) as caught:
        primitives._durable_makedirs(str(target), sync_existing=True)
    assert caught.value is failure
    if boundary == "child_creation":
        mkdir.assert_called_once_with(target, mode=0o777)
    else:
        mkdir.assert_not_called()
        flush.assert_called_once_with(str(target.parent))
