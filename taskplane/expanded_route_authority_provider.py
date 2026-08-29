"""Protected content-addressed authority for exceptional expanded routes.

The orchestrator installs this exact source as an immutable package and runs
that packaged object in a separate process.  Worker code supplies only the
closed route request and an externally signed approval receipt; provider
location, custody, time, RSA verification, issuance, and one-use consumption
remain inside this process and its protected external root.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
import time
from typing import Any, Callable

try:  # pragma: no cover - platform branch
    import fcntl
except ImportError:  # pragma: no cover - Windows branch
    fcntl = None  # type: ignore[assignment]


PROTOCOL_VERSION = "taskplane.expanded-route-authority-provider/v1"
REQUEST_SCHEMA = "taskplane.expanded-lens-route-provider-request/v1"
APPROVAL_PAYLOAD_SCHEMA = "taskplane.expanded-lens-route-approval-payload/v1"
APPROVAL_RECEIPT_SCHEMA = "taskplane.expanded-lens-route-approval-receipt/v1"
ACTION_SCHEMA = "taskplane.expanded-lens-route-action/v1"
CONSUMPTION_SCHEMA = "taskplane.expanded-lens-route-consumption/v2"
LOCATOR_SCHEMA = "taskplane.expanded-route-provider-locator/v1"
MANIFEST_SCHEMA = "taskplane.expanded-route-provider-package/v1"
APPROVER_MANIFEST_SCHEMA = "taskplane.expanded-route-approver-keys/v1"
TRANSACTION_HEAD_SCHEMA = "taskplane.expanded-route-transaction-head/v1"
MAX_APPROVAL_TTL_SECONDS = 60 * 60
MAX_FUTURE_SKEW_SECONDS = 30

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_OBJECT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_ACTION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_LENS_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_TEXT = re.compile(r"[\x21-\x7e]{1,256}\Z")
_REQUEST_FIELDS = frozenset({
    "schema", "workspace", "stage", "target", "context_fingerprint",
    "exact_ordered_lens_ids", "estimated_cost", "policy_version",
    "catalog_version", "action_id",
})
_APPROVAL_PAYLOAD_FIELDS = frozenset({
    "schema", "repository_source_path", "repository_commit", "source_sha256",
    "package_sha256", "provider_protocol_version", "workspace", "stage",
    "target", "context_fingerprint", "exact_ordered_lens_ids",
    "estimated_cost", "policy_version", "catalog_version", "action_id",
    "approved_at", "expiry", "approver_identity", "approver_key_fingerprint",
})
_APPROVAL_FIELDS = frozenset({"schema", "payload", "signature"})
_KEY_FIELDS = frozenset({
    "algorithm", "modulus", "exponent", "key_fingerprint",
    "approver_identity",
})
_MANIFEST_FIELDS = frozenset({
    "schema", "provider_protocol_version", "repository_source_path",
    "repository_commit", "source_sha256", "package_sha256", "package_path",
})
_LOCATOR_FIELDS = frozenset({
    "schema", "provider_protocol_version", "configured_root", "root_device",
    "root_inode", "package_path", "manifest_path", "manifest_sha256",
    "issuer_key_path", "issuer_key_sha256", "approver_manifest_path",
    "approver_manifest_sha256", "transactions_root", "approval_journal_root",
    "repository_source_path", "repository_commit", "source_sha256",
    "package_sha256",
})
_ACTION_FIELDS = frozenset({
    "schema", "key_id", "repository_source_path", "repository_commit",
    "source_sha256", "package_sha256", "provider_protocol_version",
    "workspace", "stage", "target", "context_fingerprint",
    "exact_ordered_lens_ids", "estimated_cost", "policy_version",
    "catalog_version", "action_id", "issued_at", "expiry",
    "approver_identity", "approver_key_fingerprint",
    "approval_receipt_digest", "seal",
})
_CONSUMPTION_FIELDS = frozenset({
    "schema", "provider_protocol_version", "locator_fingerprint", "action",
    "action_fingerprint", "approval_receipt_digest", "consumed_at",
    "recovered", "seal",
})
_HEAD_FIELDS = frozenset({
    "schema", "action_id", "receipt_sha256", "state",
})
_TEST_TOKEN = object()


class ProviderError(RuntimeError):
    """Provider source, custody, approval, or consumption is invalid."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProviderError("schema", "provider value is not canonical JSON") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _bytes_digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _current_uid() -> int:
    return int(getattr(os, "geteuid", os.getuid)())


def _mode(value: os.stat_result) -> int:
    return stat.S_IMODE(value.st_mode)


def _assert_directory(path: Path, *, create: bool = False) -> os.stat_result:
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    try:
        value = path.lstat()
    except OSError as exc:
        raise ProviderError("custody", f"protected directory is missing: {path.name}") from exc
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
        raise ProviderError("custody", f"protected directory is symlinked or invalid: {path.name}")
    if value.st_uid != _current_uid():
        raise ProviderError("custody", f"protected directory owner is invalid: {path.name}")
    if _mode(value) != 0o700:
        raise ProviderError("custody", f"protected directory mode must be 0700: {path.name}")
    return value


def _open_protected(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProviderError("custody", f"protected object is missing or symlinked: {path.name}") from exc
    value = os.fstat(descriptor)
    if not stat.S_ISREG(value.st_mode) or value.st_uid != _current_uid() or \
            _mode(value) != 0o600:
        os.close(descriptor)
        if value.st_uid != _current_uid():
            reason = "owner is invalid"
        else:
            reason = "mode must be 0600"
        raise ProviderError("custody", f"protected object {reason}: {path.name}")
    return descriptor, value


def _read_protected(path: Path) -> bytes:
    descriptor, _ = _open_protected(path)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _read_json_protected(path: Path, what: str) -> dict[str, Any]:
    try:
        value = json.loads(_read_protected(path).decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ProviderError("custody", f"{what} is unreadable") from exc
    if not isinstance(value, dict):
        raise ProviderError("custody", f"{what} is not an object")
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_temp(path: Path, payload: bytes) -> Path:
    _assert_directory(path.parent)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return temporary


def _write_immutable(path: Path, payload: bytes) -> None:
    if path.exists():
        if _read_protected(path) != payload:
            raise ProviderError("collision", f"immutable provider collision: {path.name}")
        return
    temporary = _write_temp(path, payload)
    try:
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if _read_protected(path) != payload:
                raise ProviderError("collision", f"immutable provider collision: {path.name}")
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_atomic(path: Path, payload: bytes) -> None:
    temporary = _write_temp(path, payload)
    try:
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _json_file(path: Path, fields: frozenset[str], schema: str, what: str) -> dict[str, Any]:
    value = _read_json_protected(path, what)
    if set(value) != fields or value.get("schema") != schema:
        raise ProviderError("custody", f"{what} schema is invalid")
    return value


def _normalize_key(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _KEY_FIELDS or \
            value.get("algorithm") != "rsa-pss-sha256" or \
            value.get("exponent") != 65_537:
        raise ProviderError("rsa", "approved RSA key schema is invalid")
    modulus_text = value.get("modulus")
    if not isinstance(modulus_text, str) or not re.fullmatch(r"[0-9a-f]+", modulus_text):
        raise ProviderError("rsa", "approved RSA modulus is invalid")
    modulus = int(modulus_text, 16)
    if modulus.bit_length() != 3072:
        raise ProviderError("rsa", "approved RSA modulus must be exactly 3072 bits")
    identity = value.get("approver_identity")
    if not isinstance(identity, str) or not _TEXT.fullmatch(identity):
        raise ProviderError("rsa", "approved RSA identity is invalid")
    material = {
        "algorithm": "rsa-pss-sha256", "modulus": modulus_text,
        "exponent": 65_537,
    }
    if value.get("key_fingerprint") != _digest(material):
        raise ProviderError("rsa", "approved RSA key fingerprint is invalid")
    return dict(value)


def install_provider(
    *, source_path: str, repository_source_path: str, repository_commit: str,
    authority_root: str, approver_keys: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Trusted orchestrator installation into a protected external root."""
    source = Path(os.path.abspath(source_path))
    try:
        source_state = source.lstat()
        source_bytes = source.read_bytes()
    except OSError as exc:
        raise ProviderError("source", "provider source is unavailable") from exc
    if stat.S_ISLNK(source_state.st_mode) or not stat.S_ISREG(source_state.st_mode):
        raise ProviderError("source", "provider source must be a regular non-symlink file")
    if not isinstance(repository_source_path, str) or \
            repository_source_path != "taskplane/expanded_route_authority_provider.py":
        raise ProviderError("source", "repository provider source path is invalid")
    if not isinstance(repository_commit, str) or not _GIT_OBJECT.fullmatch(repository_commit):
        raise ProviderError("source", "repository commit must be a full Git object id")
    keys = [_normalize_key(item) for item in approver_keys]
    if not keys or len({row["key_fingerprint"] for row in keys}) != len(keys):
        raise ProviderError("rsa", "approved RSA key manifest is empty or duplicated")

    root = Path(os.path.abspath(authority_root))
    if root.exists() and root.is_symlink():
        raise ProviderError("custody", "authority root cannot be a symlink")
    root_state = _assert_directory(root, create=True)
    if root.resolve() != root:
        raise ProviderError("custody", "authority root was relocated through a symlink")
    source_sha = _bytes_digest(source_bytes)
    package_dir = root / "providers" / "expanded-route" / source_sha
    for directory in (root / "providers", root / "providers" / "expanded-route",
                      package_dir, root / "custody", root / "transactions",
                      root / "approvals", root / "locators"):
        _assert_directory(directory, create=True)
    package_path = package_dir / "expanded_route_authority_provider.py"
    _write_immutable(package_path, source_bytes)
    package_sha = _bytes_digest(_read_protected(package_path))
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "provider_protocol_version": PROTOCOL_VERSION,
        "repository_source_path": repository_source_path,
        "repository_commit": repository_commit,
        "source_sha256": source_sha,
        "package_sha256": package_sha,
        "package_path": str(package_path),
    }
    manifest_path = package_dir / "manifest.json"
    manifest_bytes = _canonical(manifest)
    _write_immutable(manifest_path, manifest_bytes)
    issuer_path = root / "custody" / ".issuer.key"
    issuer_bytes = secrets.token_bytes(32)
    _write_immutable(issuer_path, issuer_bytes)
    approver_path = root / "custody" / "approver-keys.json"
    approver_bytes = _canonical({
        "schema": APPROVER_MANIFEST_SCHEMA,
        "provider_protocol_version": PROTOCOL_VERSION,
        "keys": keys,
    })
    _write_immutable(approver_path, approver_bytes)
    lock_path = root / "custody" / ".authority.lock"
    _write_immutable(lock_path, b"")
    locator = {
        "schema": LOCATOR_SCHEMA,
        "provider_protocol_version": PROTOCOL_VERSION,
        "configured_root": str(root),
        "root_device": int(root_state.st_dev),
        "root_inode": int(root_state.st_ino),
        "package_path": str(package_path),
        "manifest_path": str(manifest_path),
        "manifest_sha256": _bytes_digest(manifest_bytes),
        "issuer_key_path": str(issuer_path),
        "issuer_key_sha256": _bytes_digest(issuer_bytes),
        "approver_manifest_path": str(approver_path),
        "approver_manifest_sha256": _bytes_digest(approver_bytes),
        "transactions_root": str(root / "transactions"),
        "approval_journal_root": str(root / "approvals"),
        "repository_source_path": repository_source_path,
        "repository_commit": repository_commit,
        "source_sha256": source_sha,
        "package_sha256": package_sha,
    }
    locator_bytes = _canonical(locator)
    locator_path = root / "locators" / f"{_bytes_digest(locator_bytes)}.json"
    _write_immutable(locator_path, locator_bytes)
    return {
        **locator,
        "authority_root": str(root),
        "locator_path": str(locator_path),
        "issuer_key_path": str(issuer_path),
    }


def _contained(root: Path, raw: object, *, name: str) -> Path:
    if not isinstance(raw, str) or not os.path.isabs(raw):
        raise ProviderError("custody", f"{name} is not an absolute protected path")
    path = Path(raw)
    try:
        if os.path.commonpath((str(root), str(path))) != str(root):
            raise ProviderError("custody", f"{name} escapes the configured root")
    except ValueError as exc:
        raise ProviderError("custody", f"{name} escapes the configured root") from exc
    return path


def _validate_installation(
    locator_path: str, *, execution_path: str,
) -> dict[str, Any]:
    locator_file = Path(os.path.abspath(locator_path))
    locator_bytes = _read_protected(locator_file)
    try:
        locator = json.loads(locator_bytes.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ProviderError("locator", "provider locator is unreadable") from exc
    if not isinstance(locator, dict) or set(locator) != _LOCATOR_FIELDS or \
            locator.get("schema") != LOCATOR_SCHEMA or \
            locator.get("provider_protocol_version") != PROTOCOL_VERSION:
        raise ProviderError("locator", "provider locator schema or protocol is invalid")
    root = Path(str(locator.get("configured_root") or ""))
    if not root.is_absolute() or root.resolve() != root:
        raise ProviderError("locator", "provider configured root is relocated")
    root_state = _assert_directory(root)
    if int(locator.get("root_device", -1)) != root_state.st_dev or \
            int(locator.get("root_inode", -1)) != root_state.st_ino:
        raise ProviderError("locator", "provider root identity changed or was relocated")
    expected_locator = root / "locators" / f"{_bytes_digest(locator_bytes)}.json"
    if locator_file != expected_locator:
        raise ProviderError("locator", "provider locator was altered or relocated")
    for directory in (
        root / "locators", root / "providers", root / "providers" / "expanded-route",
        root / "custody", root / "transactions", root / "approvals",
    ):
        _assert_directory(directory)
    package_path = _contained(root, locator.get("package_path"), name="package path")
    manifest_path = _contained(root, locator.get("manifest_path"), name="manifest path")
    issuer_path = _contained(root, locator.get("issuer_key_path"), name="issuer key path")
    approver_path = _contained(
        root, locator.get("approver_manifest_path"), name="approver manifest path")
    transaction_root = _contained(
        root, locator.get("transactions_root"), name="transaction root")
    approval_root = _contained(
        root, locator.get("approval_journal_root"), name="approval journal root")
    source_sha = locator.get("source_sha256")
    if not isinstance(source_sha, str) or not _SHA256.fullmatch(source_sha):
        raise ProviderError("source", "provider source digest is malformed")
    canonical_package_dir = root / "providers" / "expanded-route" / source_sha
    canonical_paths = {
        "package path": (
            package_path,
            canonical_package_dir / "expanded_route_authority_provider.py"),
        "manifest path": (manifest_path, canonical_package_dir / "manifest.json"),
        "issuer key path": (issuer_path, root / "custody" / ".issuer.key"),
        "approver manifest path": (
            approver_path, root / "custody" / "approver-keys.json"),
        "transaction root": (transaction_root, root / "transactions"),
        "approval journal root": (approval_root, root / "approvals"),
    }
    for name, (actual, expected) in canonical_paths.items():
        if actual != expected:
            raise ProviderError(
                "custody", f"{name} was relocated from its canonical path")
    _assert_directory(canonical_package_dir)
    execution = Path(os.path.abspath(execution_path))
    if execution != package_path or execution.resolve() != package_path:
        raise ProviderError("source", "provider is not executing the configured package")
    package_bytes = _read_protected(package_path)
    manifest_bytes = _read_protected(manifest_path)
    manifest = _json_file(manifest_path, _MANIFEST_FIELDS, MANIFEST_SCHEMA,
                          "provider package manifest")
    expected_manifest = {
        "schema": MANIFEST_SCHEMA,
        "provider_protocol_version": locator["provider_protocol_version"],
        "repository_source_path": locator["repository_source_path"],
        "repository_commit": locator["repository_commit"],
        "source_sha256": locator["source_sha256"],
        "package_sha256": locator["package_sha256"],
        "package_path": locator["package_path"],
    }
    if _bytes_digest(manifest_bytes) != locator.get("manifest_sha256") or \
            manifest != expected_manifest:
        raise ProviderError("source", "provider package manifest binding changed")
    if manifest.get("provider_protocol_version") != PROTOCOL_VERSION or \
            _bytes_digest(package_bytes) != manifest.get("package_sha256") or \
            manifest.get("source_sha256") != locator.get("source_sha256") or \
            package_path.parent.name != manifest.get("source_sha256"):
        raise ProviderError("source", "provider source, package, or protocol digest changed")
    issuer = _read_protected(issuer_path)
    if len(issuer) != 32 or \
            _bytes_digest(issuer) != locator.get("issuer_key_sha256"):
        raise ProviderError("custody", "provider issuer key is invalid")
    approver_bytes = _read_protected(approver_path)
    if _bytes_digest(approver_bytes) != \
            locator.get("approver_manifest_sha256"):
        raise ProviderError("custody", "approver key manifest digest changed")
    try:
        approvers = json.loads(approver_bytes.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ProviderError(
            "custody", "approver key manifest is unreadable") from exc
    if not isinstance(approvers, dict):
        raise ProviderError("custody", "approver key manifest is not an object")
    if set(approvers) != {"schema", "provider_protocol_version", "keys"} or \
            approvers.get("schema") != APPROVER_MANIFEST_SCHEMA or \
            approvers.get("provider_protocol_version") != PROTOCOL_VERSION or \
            not isinstance(approvers.get("keys"), list):
        raise ProviderError("custody", "approver key manifest is invalid")
    keys = [_normalize_key(item) for item in approvers["keys"]]
    return {
        "locator": locator, "locator_fingerprint": _bytes_digest(locator_bytes),
        "root": root, "package_path": package_path, "issuer": issuer,
        "approvers": {row["key_fingerprint"]: row for row in keys},
        "transactions_root": transaction_root, "approval_root": approval_root,
        "lock_path": root / "custody" / ".authority.lock",
    }


@contextmanager
def _authority_lock(path: Path) -> Iterator[None]:
    descriptor, _ = _open_protected(path)
    try:
        if fcntl is None:  # pragma: no cover - Windows branch
            raise ProviderError("lock", "cross-process authority lock is unavailable")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _mgf1(seed: bytes, length: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
    return out[:length]


def _rsa_pss_sha256_valid(key: Mapping[str, Any], payload: bytes, signature: str) -> bool:
    try:
        raw = base64.b64decode(signature, validate=True)
        modulus = int(str(key["modulus"]), 16)
        exponent = int(key["exponent"])
    except (ValueError, TypeError, KeyError):
        return False
    size = (modulus.bit_length() + 7) // 8
    if modulus.bit_length() != 3072 or exponent != 65_537 or len(raw) != size:
        return False
    signature_number = int.from_bytes(raw, "big")
    if signature_number >= modulus:
        return False
    em_bits = modulus.bit_length() - 1
    em_len = (em_bits + 7) // 8
    encoded = pow(signature_number, exponent, modulus).to_bytes(size, "big")
    if len(encoded) != em_len or encoded[-1] != 0xBC:
        return False
    digest_size = hashlib.sha256().digest_size
    masked = bytearray(encoded[:em_len - digest_size - 1])
    digest = encoded[em_len - digest_size - 1:-1]
    unused = 8 * em_len - em_bits
    if masked[0] & (0xFF << (8 - unused)):
        return False
    mask = _mgf1(digest, len(masked))
    data = bytearray(left ^ right for left, right in zip(masked, mask))
    data[0] &= 0xFF >> unused
    salt_size = 32
    padding_size = em_len - digest_size - salt_size - 2
    if data[:padding_size] != b"\0" * padding_size or \
            data[padding_size] != 1:
        return False
    salt = bytes(data[-salt_size:])
    expected = hashlib.sha256(
        b"\0" * 8 + hashlib.sha256(payload).digest() + salt).digest()
    return hmac.compare_digest(digest, expected)


def _validate_request(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _REQUEST_FIELDS or \
            value.get("schema") != REQUEST_SCHEMA:
        raise ProviderError("request", "expanded route request schema is malformed")
    request = dict(value)
    if request.get("stage") not in {"plan", "evaluate"}:
        raise ProviderError("request", "expanded route request stage is invalid")
    for field in ("workspace", "context_fingerprint"):
        if not isinstance(request.get(field), str) or \
                not _SHA256.fullmatch(request[field]):
            raise ProviderError("request", f"expanded route request {field} is invalid")
    for field in ("target", "policy_version", "catalog_version"):
        if not isinstance(request.get(field), str) or not _TEXT.fullmatch(request[field]):
            raise ProviderError("request", f"expanded route request {field} is invalid")
    lenses = request.get("exact_ordered_lens_ids")
    if not isinstance(lenses, list) or not lenses or len(lenses) > 26 or \
            len(set(lenses)) != len(lenses) or any(
                not isinstance(item, str) or not _LENS_ID.fullmatch(item)
                for item in lenses):
        raise ProviderError("request", "expanded route request lenses are invalid")
    cost = request.get("estimated_cost")
    if isinstance(cost, bool) or not isinstance(cost, int) or not 1 <= cost <= 1_000_000_000:
        raise ProviderError("request", "expanded route request cost is invalid")
    if not isinstance(request.get("action_id"), str) or \
            not _ACTION_ID.fullmatch(request["action_id"]):
        raise ProviderError("request", "expanded route request action id is invalid")
    return request


def _validate_approval(
    value: object, *, request: Mapping[str, Any], state: Mapping[str, Any], now: int,
) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping) or set(value) != _APPROVAL_FIELDS or \
            value.get("schema") != APPROVAL_RECEIPT_SCHEMA or \
            not isinstance(value.get("payload"), Mapping) or \
            not isinstance(value.get("signature"), str):
        raise ProviderError("approval", "expanded route approval receipt is malformed")
    payload = dict(value["payload"])
    if set(payload) != _APPROVAL_PAYLOAD_FIELDS or \
            payload.get("schema") != APPROVAL_PAYLOAD_SCHEMA:
        raise ProviderError("approval", "expanded route approval payload is malformed")
    key = state["approvers"].get(payload.get("approver_key_fingerprint"))
    if key is None or payload.get("approver_identity") != key.get("approver_identity"):
        raise ProviderError("approval", "expanded route approver binding is invalid")
    if not _rsa_pss_sha256_valid(key, _canonical(payload), value["signature"]):
        raise ProviderError("rsa", "expanded route RSA-PSS signature is invalid")
    approved_at = payload.get("approved_at")
    expiry = payload.get("expiry")
    if isinstance(approved_at, bool) or not isinstance(approved_at, int) or \
            isinstance(expiry, bool) or not isinstance(expiry, int):
        raise ProviderError("time", "expanded route approval time is malformed")
    if approved_at > now + MAX_FUTURE_SKEW_SECONDS or expiry <= now or \
            expiry <= approved_at or expiry - approved_at > MAX_APPROVAL_TTL_SECONDS:
        raise ProviderError("time", "expanded route approval expiry or skew is invalid")
    locator = state["locator"]
    provider_expected = {
        "repository_source_path": locator["repository_source_path"],
        "repository_commit": locator["repository_commit"],
        "source_sha256": locator["source_sha256"],
        "package_sha256": locator["package_sha256"],
        "provider_protocol_version": PROTOCOL_VERSION,
    }
    request_expected = {field: request[field] for field in _REQUEST_FIELDS if field != "schema"}
    for field, expected in {**provider_expected, **request_expected}.items():
        if payload.get(field) != expected:
            raise ProviderError("binding", f"expanded route approval {field} binding mismatches")
    return payload, _digest(value)


def _seal(secret: bytes, value: Mapping[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "seal"}
    return hmac.new(secret, _canonical(unsigned), hashlib.sha256).hexdigest()


def _validate_sealed(
    secret: bytes, value: object, fields: frozenset[str], schema: str, what: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields or \
            value.get("schema") != schema or not hmac.compare_digest(
                str(value.get("seal") or ""), _seal(secret, value)):
        raise ProviderError("custody", f"{what} seal or schema is invalid")
    return dict(value)


def _configured_package_path(locator_path: str) -> str:
    """Read only the configured executable identity from protected custody."""
    try:
        value = json.loads(_read_protected(
            Path(os.path.abspath(locator_path))).decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ProviderError("locator", "provider locator is unreadable") from exc
    if not isinstance(value, Mapping) or set(value) != _LOCATOR_FIELDS or \
            value.get("schema") != LOCATOR_SCHEMA or \
            value.get("provider_protocol_version") != PROTOCOL_VERSION or \
            not isinstance(value.get("package_path"), str):
        raise ProviderError("locator", "provider locator is invalid")
    return value["package_path"]


def _authenticate_terminal_receipt(
    locator_path: str,
    request_value: object,
    receipt_value: object,
) -> dict[str, Any]:
    """Authenticate one terminal result inside the orchestrator boundary.

    A shape-valid mapping is never enough.  The receipt must carry both
    provider HMACs, match the protected content-addressed installation, and
    be the exact durable one-use transaction selected by its consumed head.
    """
    execution_path = _configured_package_path(locator_path)
    request = _validate_request(request_value)
    state = _validate_installation(
        locator_path, execution_path=execution_path)
    receipt = _validate_sealed(
        state["issuer"], receipt_value, _CONSUMPTION_FIELDS,
        CONSUMPTION_SCHEMA, "expanded route terminal receipt")
    action = _validate_sealed(
        state["issuer"], receipt.get("action"), _ACTION_FIELDS,
        ACTION_SCHEMA, "expanded route terminal action")
    locator = state["locator"]
    expected_action = {
        "key_id": hashlib.sha256(state["issuer"]).hexdigest(),
        **{field: locator[field] for field in (
            "repository_source_path", "repository_commit", "source_sha256",
            "package_sha256",
        )},
        "provider_protocol_version": PROTOCOL_VERSION,
        **{field: request[field]
           for field in _REQUEST_FIELDS if field != "schema"},
    }
    if any(action.get(field) != expected
           for field, expected in expected_action.items()):
        raise ProviderError(
            "binding", "expanded route terminal action binding mismatches")
    if receipt.get("provider_protocol_version") != PROTOCOL_VERSION or \
            receipt.get("locator_fingerprint") != \
            state["locator_fingerprint"] or \
            receipt.get("action_fingerprint") != _digest(action) or \
            receipt.get("approval_receipt_digest") != \
            action.get("approval_receipt_digest") or \
            not isinstance(receipt.get("recovered"), bool):
        raise ProviderError(
            "binding", "expanded route terminal receipt binding mismatches")
    for field in (
        "approver_key_fingerprint", "approval_receipt_digest",
    ):
        if not isinstance(action.get(field), str) or \
                not _SHA256.fullmatch(action[field]):
            raise ProviderError(
                "binding", f"expanded route terminal action {field} is invalid")
    if not isinstance(action.get("approver_identity"), str) or \
            not _TEXT.fullmatch(action["approver_identity"]):
        raise ProviderError(
            "binding", "expanded route terminal approver identity is invalid")
    issued_at = action.get("issued_at")
    expiry = action.get("expiry")
    consumed_at = receipt.get("consumed_at")
    if any(isinstance(value, bool) or not isinstance(value, int)
           for value in (issued_at, expiry, consumed_at)) or \
            issued_at > consumed_at or consumed_at >= expiry:
        raise ProviderError(
            "time", "expanded route terminal receipt time order is invalid")

    approval_digest = action["approval_receipt_digest"]
    approval_path = state["approval_root"] / f"{approval_digest}.json"
    approval = _read_json_protected(
        approval_path, "expanded route terminal approval")
    if _digest(approval) != approval_digest:
        raise ProviderError(
            "approval", "expanded route terminal approval digest changed")
    _validate_approval(
        approval, request=request, state=state, now=issued_at)

    transaction = state["transactions_root"] / hashlib.sha256(
        request["action_id"].encode("utf-8")).hexdigest()
    head = _json_file(
        transaction / "head.json", _HEAD_FIELDS, TRANSACTION_HEAD_SCHEMA,
        "expanded route terminal transaction head")
    if head.get("state") != "consumed" or \
            head.get("action_id") != request["action_id"]:
        raise ProviderError(
            "consumption", "expanded route terminal transaction is not consumed")
    expected_bytes = _canonical(receipt)
    receipt_name = (
        "recovered-consumption-receipt.json"
        if receipt["recovered"] else "consumption-receipt.json"
    )
    durable_bytes = _read_protected(transaction / receipt_name)
    if durable_bytes != expected_bytes or \
            head.get("receipt_sha256") != _bytes_digest(durable_bytes):
        raise ProviderError(
            "consumption", "expanded route terminal receipt is not durable head")
    return json.loads(expected_bytes.decode("utf-8"))


class _AuthorityEngine:
    def __init__(
        self, locator_path: str, *, execution_path: str,
        clock: Callable[[], int], token: object,
    ) -> None:
        if token is not _TEST_TOKEN:
            raise TypeError("authority engines are provider-produced")
        self._locator_path = locator_path
        self._execution_path = execution_path
        self._clock = clock

    def authorize(
        self, request_value: object, approval_value: object, *,
        fault_at: str | None = None,
    ) -> dict[str, Any]:
        request = _validate_request(request_value)
        initial = _validate_installation(
            self._locator_path, execution_path=self._execution_path)
        with _authority_lock(initial["lock_path"]):
            state = _validate_installation(
                self._locator_path, execution_path=self._execution_path)
            current = int(self._clock())
            approval, approval_digest = _validate_approval(
                approval_value, request=request, state=state, now=current)
            action_hash = hashlib.sha256(
                request["action_id"].encode("utf-8")).hexdigest()
            transaction = state["transactions_root"] / action_hash
            _assert_directory(transaction, create=True)
            for temporary in transaction.glob(".*.tmp"):
                descriptor, _ = _open_protected(temporary)
                os.close(descriptor)
                temporary.unlink()
            head_path = transaction / "head.json"
            if head_path.exists():
                head = _json_file(
                    head_path, _HEAD_FIELDS, TRANSACTION_HEAD_SCHEMA,
                    "expanded route transaction head")
                if head.get("state") == "consumed":
                    raise ProviderError("replay", "expanded route action replay rejected")
                raise ProviderError("custody", "expanded route transaction head is invalid")
            approval_path = state["approval_root"] / f"{approval_digest}.json"
            _write_immutable(approval_path, _canonical(approval_value))
            action_path = transaction / "issued-action.json"
            action_bindings = {
                "key_id": hashlib.sha256(state["issuer"]).hexdigest(),
                **{field: state["locator"][field] for field in (
                    "repository_source_path", "repository_commit", "source_sha256",
                    "package_sha256",
                )},
                "provider_protocol_version": PROTOCOL_VERSION,
                **{field: request[field] for field in _REQUEST_FIELDS if field != "schema"},
                "expiry": approval["expiry"],
                "approver_identity": approval["approver_identity"],
                "approver_key_fingerprint": approval["approver_key_fingerprint"],
                "approval_receipt_digest": approval_digest,
            }
            if action_path.exists():
                action = _validate_sealed(
                    state["issuer"], _read_json_protected(
                        action_path, "expanded route issued action"),
                    _ACTION_FIELDS, ACTION_SCHEMA, "expanded route issued action")
                if any(action.get(field) != expected
                       for field, expected in action_bindings.items()):
                    raise ProviderError(
                        "collision", "issued action binding changed during recovery")
            else:
                action = {
                    "schema": ACTION_SCHEMA,
                    **action_bindings,
                    "issued_at": current,
                }
                action["seal"] = _seal(state["issuer"], action)
                _write_immutable(action_path, _canonical(action))
            if fault_at == "before-consumption-receipt":
                interrupted = _write_temp(transaction / "consumption-receipt.json", b"interrupted")
                interrupted.rename(transaction / ".consumption-receipt.interrupted.tmp")
                raise ProviderError("fault", "injected fault before consumption receipt")
            receipt = {
                "schema": CONSUMPTION_SCHEMA,
                "provider_protocol_version": PROTOCOL_VERSION,
                "locator_fingerprint": state["locator_fingerprint"],
                "action": action,
                "action_fingerprint": _digest(action),
                "approval_receipt_digest": approval_digest,
                "consumed_at": current,
                "recovered": False,
            }
            receipt["seal"] = _seal(state["issuer"], receipt)
            receipt_path = transaction / "consumption-receipt.json"
            if receipt_path.exists():
                stored = _validate_sealed(
                    state["issuer"], _read_json_protected(
                        receipt_path, "expanded route consumption receipt"),
                    _CONSUMPTION_FIELDS, CONSUMPTION_SCHEMA,
                    "expanded route consumption receipt")
                recovered = dict(stored)
                recovered["recovered"] = True
                recovered["seal"] = _seal(state["issuer"], recovered)
                recovery_path = transaction / "recovered-consumption-receipt.json"
                _write_immutable(recovery_path, _canonical(recovered))
                receipt = recovered
                receipt_path = recovery_path
            else:
                _write_immutable(receipt_path, _canonical(receipt))
            if fault_at == "after-consumption-receipt":
                raise ProviderError("fault", "injected fault after consumption receipt")
            head = {
                "schema": TRANSACTION_HEAD_SCHEMA,
                "action_id": request["action_id"],
                "receipt_sha256": _bytes_digest(_read_protected(receipt_path)),
                "state": "consumed",
            }
            _replace_atomic(head_path, _canonical(head))
            return receipt


class AuthorityProvider:
    """Production provider; clock and verification seams are not injectable."""

    def __init__(self, locator_path: str) -> None:
        self._engine = _AuthorityEngine(
            locator_path, execution_path=os.path.abspath(__file__),
            clock=lambda: int(time.time()), token=_TEST_TOKEN)

    def authorize(self, request: object, approval: object) -> dict[str, Any]:
        return self._engine.authorize(request, approval)


def _test_execution_path(locator_path: str) -> str:
    try:
        value = json.loads(Path(locator_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProviderError("locator", "test provider locator is unreadable") from exc
    if not isinstance(value, Mapping) or not isinstance(value.get("package_path"), str):
        raise ProviderError("locator", "test provider locator is invalid")
    return value["package_path"]


def _authorize_for_test(
    locator_path: str, request: object, approval: object, *, now: int,
    fault_at: str | None = None,
) -> dict[str, Any]:
    """Non-exported deterministic fixture; production exposes no clock seam."""
    engine = _AuthorityEngine(
        locator_path, execution_path=_test_execution_path(locator_path),
        clock=lambda: int(now), token=_TEST_TOKEN)
    return engine.authorize(request, approval, fault_at=fault_at)


def _self_check(locator_path: str) -> dict[str, Any]:
    state = _validate_installation(
        locator_path, execution_path=os.path.abspath(__file__))
    return {
        "schema": "taskplane.expanded-route-provider-self-check/v1",
        "protocol_version": PROTOCOL_VERSION,
        "source_sha256": state["locator"]["source_sha256"],
        "package_sha256": state["locator"]["package_sha256"],
        "locator_fingerprint": state["locator_fingerprint"],
    }


def _argument(argv: Sequence[str], name: str) -> str:
    if argv.count(name) != 1:
        raise ProviderError("command", f"provider command requires one {name}")
    index = argv.index(name)
    if index + 1 >= len(argv):
        raise ProviderError("command", f"provider command requires one {name}")
    return argv[index + 1]


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if not arguments or arguments[0] not in {"authorize", "self-check"}:
            raise ProviderError("command", "provider command is unsupported")
        locator = _argument(arguments, "--locator")
        if arguments[0] == "self-check":
            result = _self_check(locator)
        else:
            try:
                envelope = json.loads(sys.stdin.read())
            except ValueError as exc:
                raise ProviderError("request", "provider request envelope is unreadable") from exc
            if not isinstance(envelope, Mapping) or set(envelope) != {"request", "approval"}:
                raise ProviderError("request", "provider request envelope is malformed")
            result = AuthorityProvider(locator).authorize(
                envelope["request"], envelope["approval"])
        sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    except ProviderError as exc:
        sys.stderr.write(json.dumps({
            "schema": "taskplane.expanded-route-provider-error/v1",
            "code": exc.code, "error": exc.detail,
        }, sort_keys=True, separators=(",", ":")) + "\n")
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised as packaged process
    raise SystemExit(main())
