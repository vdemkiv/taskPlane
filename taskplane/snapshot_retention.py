"""Advisory snapshot ownership, permanent pins and bounded transient collection.

Only pairs exclusively created by the managed generation can be collected.
Lock order is controller/context/journal -> snapshot; this module never acquires
those outer locks or calls their APIs. Legacy catalog bytes are read-only.
Legacy local hosts can publish observations with ordinary atomic file operations;
collection requires descriptor-relative no-follow operations on every host.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from itertools import repeat
import json
import os
from pathlib import Path
import re
import stat
import sqlite3
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname
import time
from typing import Any, Callable, Iterator
import uuid

from . import primitives

CATALOG = "snapshot-catalog.json"
SCHEMA = "taskplane.snapshot-catalog/v1"
MAX_PAIRS = 32
MAX_BYTES = 64 * 1024 * 1024
MAX_SCAN_ENTRIES = 20000
MAX_SCAN_DEPTH = 32
MAX_SCAN_SECONDS = 1.0
MAX_REFERENCE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
LEASE_SECONDS = 60.0
MANAGED = "snapshot-managed-v1.sqlite3"
MARKER = "snapshot-managed-v1.json"
MAX_CANDIDATES = 64
MAX_VERIFY_BYTES = 2 * MAX_FILE_BYTES
MAX_SQL_STEPS = 200000
MAX_PIN_REASONS = 32
PAIR = re.compile(r"snapshot-[a-f0-9]{16}-[a-f0-9]{64}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(workspace: str | Path) -> Path:
    from .workspace_binding import validate_workspace_root
    return validate_workspace_root(workspace).resolve() / ".taskplane"


def supports_collection() -> bool:
    return (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
            and os.open in os.supports_dir_fd)


def _ordinary(path: Path, *, directory: bool = False) -> os.stat_result:
    info = path.lstat()
    reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if reparse or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("Snapshot path must be an ordinary " + ("directory" if directory else "file"))
    return info


def _path_identity(path: Path) -> tuple[tuple[int, int], ...]:
    return tuple((info.st_dev, info.st_ino) for part in (*reversed(path.parents), path)
                 for info in [_ordinary(part, directory=True)])


@contextmanager
def _directory(path: Path, *, strict: bool = False) -> Iterator[int | Path]:
    """Anchor POSIX I/O; retain the legacy local publication path on Windows.

    Path checks detect ordinary links and directory changes, but are not a
    substitute for descriptor-relative deletion or strict workspace binding.
    """
    if not supports_collection():
        if strict:
            raise ValueError("Snapshot collection requires no-follow directory operations")
        identity = _path_identity(path)
        yield path
        if _path_identity(path) != identity:
            raise ValueError("Snapshot directory changed during observation")
        return
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read(fd: int | Path, name: str, limit: int = MAX_FILE_BYTES) -> bytes:
    expected = None
    if isinstance(fd, Path):
        _path_identity(fd)
        expected = _ordinary(fd / name)
        child = os.open(fd / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    else:
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(child, "rb") as stream:
        before = os.fstat(stream.fileno())
        if expected and (expected.st_dev, expected.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Snapshot input changed while opening")
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("Snapshot input is not a bounded ordinary file")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(raw) > limit or (before.st_size, before.st_mtime_ns, before.st_ino) != (
                after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError("Snapshot input changed while reading")
        return raw


def _write(fd: int | Path, name: str, raw: bytes, *, exclusive: bool = False) -> None:
    if isinstance(fd, Path):
        identity = _path_identity(fd)
        target = fd / name
        try:
            _ordinary(target)
        except FileNotFoundError:
            pass
        temporary_path = fd / (".snapshot-write-" + uuid.uuid4().hex)
        try:
            with temporary_path.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if _path_identity(fd) != identity:
                raise ValueError("Snapshot directory changed during publication")
            if exclusive:
                os.link(temporary_path, target)
            else:
                os.replace(temporary_path, target)
            primitives._fsync_directory(str(fd))
        finally:
            temporary_path.unlink(missing_ok=True)
        return
    temporary = ".snapshot-write-" + uuid.uuid4().hex
    child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(child, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        else:
            try:
                mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISREG(mode):
                    raise ValueError("Snapshot output is not an ordinary file")
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            # Replacement consumes this name; exclusive linking leaves it to clean up.
            pass


@contextmanager
def _locked(root: Path, *, strict: bool = False) -> Iterator[int | Path]:
    with _directory(root, strict=strict) as fd:
        if isinstance(fd, Path):
            try:
                _ordinary(root / (CATALOG + ".lock"))
            except FileNotFoundError:
                pass
            with primitives.file_lock(str(root / CATALOG)):
                yield fd
            return
        child = os.open(CATALOG + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                        0o600, dir_fd=fd)
        with os.fdopen(child, "a+b") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Snapshot lock must be ordinary")
            primitives.lock_file(stream)
            try:
                yield fd
            finally:
                primitives.unlock_file(stream)


def _catalog(fd: int | Path, root: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read(fd, CATALOG))
    except FileNotFoundError:
        return {"schema": SCHEMA, "workspace": str(root.parent), "pairs": {}, "selected": {}, "sequence": 0}
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or value.get("workspace") != str(root.parent) or not isinstance(value.get("pairs"), dict)
            or not isinstance(value.get("selected"), dict) or type(value.get("sequence")) is not int):
        raise ValueError("Snapshot catalog identity is invalid; preserve existing files")
    for name, item in value["pairs"].items():
        if (not PAIR.fullmatch(name) or not isinstance(item, dict) or item.get("workspace") != str(root.parent)
                or not isinstance(item.get("pins"), list) or type(item.get("order")) is not int
                or not isinstance(item.get("run"), str)
                or not name.startswith("snapshot-" + hashlib.sha256(item["run"].encode()).hexdigest()[:16] + "-")
                or not isinstance(item.get("files"), dict)
                or set(item["files"]) != {name + ".html", name + ".json"}):
            raise ValueError("Snapshot catalog ownership is invalid")
        for metadata in item["files"].values():
            if (not isinstance(metadata, dict) or not re.fullmatch(r"[a-f0-9]{64}", str(metadata.get("sha256")))
                    or type(metadata.get("bytes")) is not int or metadata["bytes"] < 0):
                raise ValueError("Snapshot catalog file identity is invalid")
    return value


def content_key(model: dict[str, Any]) -> str:
    """Ignore only transport attempts; preserve actual counter/sample times.

    Storage inventory is sampled independently and is displayed in mutable views.
    It must not cause a feedback loop where each pair creates the next pair.
    """
    value = deepcopy(model)
    snapshot = value.get("snapshot", {})
    for key in ("generated_at", "captured_at", "measurement_attempted_at", "digest", "presentation_target"):
        snapshot.pop(key, None)
    value.get("usage_measurement", {}).pop("attempted_at", None)
    value.get("phase_usage", {}).pop("measurement_attempted_at", None)
    value.get("workflow", {}).get("storage", {}).pop("store", None)
    value.pop("snapshot_retention", None)
    return primitives.content_fingerprint(value)


def _verified(fd: int | Path, item: dict[str, Any], *, absent: bool = False) -> bool:
    for name, expected in item["files"].items():
        try:
            raw = _read(fd, name)
        except FileNotFoundError:
            if absent:
                continue
            return False
        except (OSError, ValueError):
            return False
        if len(raw) != expected["bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
            return False
        if name.endswith(".json"):
            try:
                model = json.loads(raw)
                snapshot = model["snapshot"]
                if (any(snapshot.get(key) != item.get(key) for key in ("workspace", "root", "run"))
                        or model.get("run") != item.get("run")
                        or not name.endswith("-" + snapshot["digest"] + ".json")):
                    return False
            except (ValueError, KeyError, TypeError, AttributeError):
                return False
    return True


def _category(relative: str) -> str:
    parts = Path(relative).parts
    name = parts[-1]
    if PAIR.fullmatch(Path(name).stem):
        return "snapshots"
    if name.startswith("dashboard") or name.endswith(".selection.json"):
        return "dashboard_copies"
    if "context-v1" in parts:
        return "context"
    if name.startswith(("workflow-", "local-harness-")):
        return "controllers"
    if name.endswith(".jsonl") or name.startswith("usage-closure-"):
        return "journals"
    if "knowledge" in parts or name.startswith("graph"):
        return "graphs"
    return "other"


def _scan(root: Path, *, references: bool = False, max_entries: int = MAX_SCAN_ENTRIES,
          max_depth: int = MAX_SCAN_DEPTH, max_seconds: float = MAX_SCAN_SECONDS) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {"bytes": 0, "files": 0, "entries": 0, "categories": {},
                             "complete": True, "errors": [], "references": set(),
                             "permanent_references": set(), "signatures": []}
    consumed = 0

    def error(detail: str) -> None:
        result["complete"] = False
        if len(result["errors"]) < 16:
            result["errors"].append(detail)

    def visit(fd: int | Path, prefix: str, depth: int) -> None:
        nonlocal consumed
        if depth > max_depth:
            error("depth_limit")
            return
        before = _ordinary(fd, directory=True) if isinstance(fd, Path) else os.fstat(fd)
        try:
            with os.scandir(fd) as entries:
                for entry in entries:
                    if result["entries"] >= max_entries or time.monotonic() - started >= max_seconds:
                        error("entry_or_time_limit")
                        break
                    relative = prefix + entry.name
                    result["entries"] += 1
                    info = entry.stat(follow_symlinks=False)
                    if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                        error("link_or_special_file:" + relative)
                    elif stat.S_ISDIR(info.st_mode) and isinstance(fd, Path):
                        with _directory(fd / entry.name) as child_path:
                            visit(child_path, relative + "/", depth + 1)
                    elif stat.S_ISDIR(info.st_mode):
                        assert isinstance(fd, int)
                        child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        try:
                            if os.fstat(child).st_ino != info.st_ino:
                                error("directory_changed")
                            else:
                                visit(child, relative + "/", depth + 1)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode):
                        result["bytes"] += info.st_size
                        result["files"] += 1
                        category = result["categories"].setdefault(_category(relative), {"bytes": 0, "files": 0})
                        category["bytes"] += info.st_size
                        category["files"] += 1
                        # Catalog and transient renderings are not durable
                        # reference roots. Selection/controller/context/journal
                        # files are. Immutable snapshot models are scanned too.
                        textual = entry.name.endswith((".json", ".jsonl", ".md", ".txt", ".log", ".html"))
                        owned_html = entry.name.endswith(".html") and bool(PAIR.fullmatch(Path(entry.name).stem))
                        if references and entry.name != CATALOG and textual and not owned_html:
                            if consumed + info.st_size > MAX_REFERENCE_BYTES:
                                error("reference_byte_limit")
                                continue
                            raw = _read(fd, entry.name, MAX_REFERENCE_BYTES - consumed)
                            consumed += len(raw)
                            result["signatures"].append((relative, hashlib.sha256(raw).hexdigest()))
                            try:
                                if entry.name.endswith(".jsonl"):
                                    for line in raw.splitlines():
                                        if line.strip():
                                            json.loads(line)
                                elif entry.name.endswith(".json"):
                                    json.loads(raw)
                                # Snapshot references can occur inside an encoded
                                # report or HTML field, not just a known schema.
                                text = raw.decode("utf-8")
                                found = PAIR.findall(text)
                                result["references"].update(found)
                                if not entry.name.endswith(".selection.json") and not entry.name.startswith("dashboard"):
                                    result["permanent_references"].update(found)
                            except (ValueError, UnicodeError):
                                error("unreadable_reference:" + relative)
                    else:
                        error("link_or_special_file:" + relative)
        except (OSError, ValueError) as exc:
            error(type(exc).__name__ + ":" + prefix)
        after = _ordinary(fd, directory=True) if isinstance(fd, Path) else os.fstat(fd)
        if before.st_mtime_ns != after.st_mtime_ns:
            error("directory_changed:" + prefix)

    try:
        with _directory(root) as fd:
            visit(fd, "", 0)
    except FileNotFoundError:
        result["exists"] = False
    except (OSError, ValueError) as exc:
        error(type(exc).__name__ + ":store")
    result["scanned_at"] = _now()
    result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    result["fingerprint"] = primitives.content_fingerprint(sorted(result.pop("signatures")))
    return result


def inventory(workspace: str | Path, *, max_entries: int = MAX_SCAN_ENTRIES,
              max_depth: int = MAX_SCAN_DEPTH, max_seconds: float = MAX_SCAN_SECONDS) -> dict[str, Any]:
    """Read-only logical path-byte inventory; partial scans are lower bounds."""
    root = _root(workspace)
    value = _scan(root, max_entries=max_entries, max_depth=max_depth, max_seconds=max_seconds)
    value.pop("references")
    value.pop("permanent_references")
    value.pop("fingerprint")
    value.update(schema="taskplane.store-inventory/v1", basis="logical path bytes; hardlinks count per path",
                 status="complete" if value["complete"] else "partial", lower_bound=not value["complete"],
                 policy={"transient_pairs": MAX_PAIRS, "transient_bytes": MAX_BYTES})
    value["snapshots"] = {"status": "unavailable"}
    try:
        with _directory(root) as fd, _managed(fd, root, readonly=True) as db:
            rows = db.execute("SELECT * FROM pairs ORDER BY seq LIMIT ?", (max_entries + 1,)).fetchall()
            totals = {"pinned_bytes": 0, "transient_bytes": 0, "protected_bytes": 0, "managed_pairs": 0}
            for row in rows[:max_entries]:
                item = _item(row, root)
                size = sum(v["bytes"] for v in item["files"].values())
                totals["managed_pairs"] += 1
                totals["pinned_bytes" if row["pinned"] else "transient_bytes"] += size
                if row["pinned"] or row["lease"] > time.time() or db.execute(
                        "SELECT 1 FROM protections WHERE name=? LIMIT 1", (row["name"],)).fetchone():
                    totals["protected_bytes"] += size
            complete = value["complete"] and len(rows) <= max_entries
            legacy_bytes = max(0, value["categories"].get("snapshots", {}).get("bytes", 0)
                               - totals["pinned_bytes"] - totals["transient_bytes"]) if complete else None
            value["snapshots"] = {"status": "catalogued" if complete else "partial", **totals,
                "legacy_bytes": legacy_bytes, "legacy_preserved": True, "managed_lower_bound": len(rows) > max_entries}
    except (OSError, ValueError, TypeError, sqlite3.Error):
        value["snapshots"] = {"status": "unknown", "legacy_preserved": True,
                              "reason": "managed generation unavailable; legacy totals unknown"}
    return value


@contextmanager
def _managed(fd: int | Path, root: Path, *, create: bool = False, readonly: bool = False) -> Iterator[sqlite3.Connection]:
    """Authoritative generation: loss/corruption is never repaired into ownership."""
    identity = _path_identity(root)
    try:
        marker = json.loads(_read(fd, MARKER, 4096))
    except FileNotFoundError:
        if not create:
            raise ValueError("Managed snapshot generation is absent") from None
        if (root / MANAGED).exists():
            raise ValueError("Managed snapshot marker is missing; preserve files")
        # An interrupted initialization remains unavailable, never adopted.
        _write(fd, MARKER, primitives.canonical_bytes({"workspace": str(root.parent),
            "generation": uuid.uuid4().hex}), exclusive=True)
        marker = json.loads(_read(fd, MARKER, 4096))
        fresh = True
    else:
        fresh = False
    if (not isinstance(marker, dict) or marker.get("workspace") != str(root.parent)
            or not re.fullmatch(r"[a-f0-9]{32}", str(marker.get("generation")))):
        raise ValueError("Managed snapshot marker is invalid")
    for name in (MANAGED, MANAGED + "-journal", MANAGED + "-wal", MANAGED + "-shm"):
        try:
            _ordinary(root / name)
        except FileNotFoundError:
            if name == MANAGED and not fresh:
                raise ValueError("Managed snapshot database is missing; preserve files") from None
    if fresh:
        child = os.open(root / MANAGED, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(child)
    db = sqlite3.connect((root / MANAGED).as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    steps = 0
    def progress() -> int:
        nonlocal steps
        steps += 1
        return int(steps > MAX_SQL_STEPS)
    db.set_progress_handler(progress, 1)
    try:
        if not readonly:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
        if fresh:
            db.executescript("""
                CREATE TABLE identity (generation TEXT, workspace TEXT);
                CREATE TABLE pairs (seq INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
                    content_key TEXT, item TEXT, pinned INTEGER NOT NULL DEFAULT 0,
                    lease REAL NOT NULL DEFAULT 0, deleting INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX content_lookup ON pairs(content_key);
                CREATE INDEX pending_delete ON pairs(deleting, seq);
                CREATE TABLE protections (kind TEXT, owner TEXT, name TEXT,
                    PRIMARY KEY(kind, owner));
                CREATE INDEX protected_name ON protections(name);
                CREATE TABLE sweep (id INTEGER PRIMARY KEY, cursor INTEGER, highwater INTEGER,
                    kept_count INTEGER, kept_bytes INTEGER, max_pairs INTEGER, max_bytes INTEGER);
            """)
            db.execute("INSERT INTO identity VALUES (?, ?)", (marker["generation"], str(root.parent)))
            db.commit()
        row = db.execute("SELECT generation,workspace FROM identity LIMIT 2").fetchall()
        if len(row) != 1 or tuple(row[0]) != (marker["generation"], str(root.parent)):
            raise ValueError("Managed snapshot database identity changed")
        if _path_identity(root) != identity:
            raise ValueError("Managed snapshot directory changed")
        yield db
    finally:
        db.close()


def _item(row: sqlite3.Row, root: Path) -> dict[str, Any]:
    if len(row["item"]) > 65536:
        raise ValueError("Managed snapshot ownership record is oversized")
    item = json.loads(row["item"])
    name = row["name"]
    if (not isinstance(item, dict) or not isinstance(name, str) or not PAIR.fullmatch(name) or item.get("workspace") != str(root.parent)
            or not isinstance(item.get("pins"), list) or len(item["pins"]) > MAX_PIN_REASONS
            or not all(isinstance(reason, str) and 0 < len(reason) <= 1024 for reason in item["pins"])
            or not isinstance(item.get("run"), str)
            or not name.startswith("snapshot-" + hashlib.sha256(item["run"].encode()).hexdigest()[:16] + "-")
            or not isinstance(item.get("files"), dict)
            or set(item["files"]) != {name + ".html", name + ".json"}):
        raise ValueError("Managed snapshot ownership is invalid")
    for metadata in item["files"].values():
        if (not isinstance(metadata, dict) or type(metadata.get("bytes")) is not int or not 0 <= metadata["bytes"] <= MAX_FILE_BYTES
                or not re.fullmatch(r"[a-f0-9]{64}", str(metadata.get("sha256")))):
            raise ValueError("Managed snapshot member identity is invalid")
    item["deleting"] = bool(row["deleting"])
    return item


def classify_snapshot_references(workspace: str | Path, value: Any, *, format: str = "structured") -> set[str]:
    """Bounded value traversal; preserve candidate paths until identity is checked."""
    root = _root(workspace)
    if format not in {"structured", "json", "text"}:
        raise ValueError("Unknown snapshot reference format")
    if isinstance(value, bytes):
        if len(value) > MAX_REFERENCE_BYTES:
            raise ValueError("Snapshot reference byte limit")
        try:
            value = value.decode("utf-8")
        except UnicodeError:
            if (format == "text" and b".taskplane/snapshot-" not in value
                    and not re.search(rb"snapshot-[a-f0-9]{16}-[a-f0-9]{64}\.(?:html|json)", value)):
                return set()  # Opaque binary attachment, outside the text-reference contract.
            raise ValueError("Undecodable candidate snapshot references") from None
    if isinstance(value, str):
        if len(value) > MAX_REFERENCE_BYTES or len(value.encode("utf-8")) > MAX_REFERENCE_BYTES:
            raise ValueError("Snapshot reference byte limit")
    if format == "json":
        try:
            value = json.loads(value)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError("Malformed declared JSON snapshot references") from exc
    found: set[str] = set()
    used = 0
    reference_fields = {"snapshot", "artifact", "path", "href", "src", "evidence"}
    # Bare native tokens remain supported, but a directory is never discarded.
    token = re.compile(r"(?<![A-Za-z0-9_-])(snapshot-[a-f0-9]{16}-[a-f0-9]{64}\.(?:html|json))(?![A-Za-z0-9_-]|\.[A-Za-z0-9])")

    def members(body: dict[str, Any]) -> Iterator[tuple[Any, str | None]]:
        for key, child in body.items():
            if not isinstance(key, str):
                raise ValueError("Snapshot reference keys must be strings")
            yield key, None  # Inventory keys count towards the budget, never pin.
            yield child, key

    def candidates(text: str) -> Iterator[tuple[str, bool]]:
        whole = text.strip().rstrip('.')
        match = token.search(whole)
        # Preserve spaces in whole absolute/local paths and file URIs. Prose is
        # handled below so its lead-in never becomes part of a relative path.
        if (match and match.end() == len(whole)
                and not any(c in whole for c in '\n<>"')
                and (Path(whole).is_absolute() or whole.startswith(('file:', '.taskplane/', '.taskplane\\', '../', '..\\')))):
            yield whole, False
            return
        links = list(re.finditer(r'''(?:href|src)\s*=\s*(?:"([^"]+)"|'([^']+)'|([^\s"'<>`]+))|\]\(\s*<?([^\n()]+?)>?\s*\)''', text))
        for link in links:
            yield next(group for group in link.groups() if group is not None).strip(), True
        link_index = 0
        for word in re.finditer(r'''[^\s"'<>()[\]{},;`]+''', text):
            while link_index < len(links) and links[link_index].end() <= word.start():
                link_index += 1
            if link_index == len(links) or word.start() < links[link_index].start():
                yield word.group().rstrip('.!?'), False

    def native_path(candidate: str, declared: bool) -> bool:
        original = candidate
        if re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", candidate) and not candidate.startswith('file:'):
            raise ValueError(f"Foreign snapshot reference: {original[:512]}")
        if candidate.startswith('file:'):
            parsed = urlsplit(candidate)
            if parsed.netloc not in {'', 'localhost'} or parsed.query or parsed.fragment:
                raise ValueError(f"Foreign snapshot reference: {original[:512]}")
            # Escapes were decoded once; preserve literal percent characters.
            candidate = url2pathname(parsed.path.replace('%', '%25'))
        target = Path(candidate)
        if target.name == candidate:
            return True
        if not target.is_absolute():
            target = root.parent / target
        if target.parent == root and '..' not in target.parts:
            return True
        if declared or '.taskplane' in target.parts or '..' in target.parts:
            raise ValueError(f"Foreign or traversing snapshot reference: {original[:512]}")
        return False  # Ordinary backup paths in untyped data retain their identity.

    # One iterator per ancestor, rather than one stack entry per sibling.
    # Each visited value/key costs at least one unit; text costs its UTF-8
    # length. This lower bound on JSON bytes bounds work without rejecting
    # supported byte-sized payloads merely because they contain many nodes.
    stack: list[tuple[Iterator[tuple[Any, str | None]], int | None]] = [(iter([(value, "")]), None)]
    active: set[int] = set()
    while stack:
        try:
            body, field = next(stack[-1][0])
        except StopIteration:
            _, identity = stack.pop()
            if identity is not None:
                active.remove(identity)
            continue
        if len(stack) - 1 > MAX_SCAN_DEPTH:
            raise ValueError("Snapshot reference traversal limit")
        remaining = MAX_REFERENCE_BYTES - used
        if isinstance(body, str):
            if len(body) > remaining:
                raise ValueError("Snapshot reference byte limit")
            used += max(1, len(body.encode("utf-8")))
        else:
            used += 1
        if used > MAX_REFERENCE_BYTES:
            raise ValueError("Snapshot reference byte limit")
        if field is None:
            continue
        if isinstance(body, (dict, list, tuple)):
            identity = id(body)
            if identity in active:
                raise ValueError("Cyclic snapshot references")
            active.add(identity)
            children = members(body) if isinstance(body, dict) else zip(body, repeat(field))
            stack.append((children, identity))
        elif not isinstance(body, (str, int, float, bool, type(None))):
            raise ValueError("Unsupported snapshot reference value")
        elif isinstance(body, str):
            # Complete encoded containers obey the same key/value contract.
            # Their text and parsed children both consume the existing budget.
            parsed = None
            # Decode only to recognize a container; ordinary candidates retain
            # their original path/URI escaping semantics below.
            for container_text in (body, unquote(body)):
                if not container_text.lstrip().startswith(('{', '[')):
                    continue
                try:
                    parsed = json.loads(container_text)
                except RecursionError as exc:
                    raise ValueError("Snapshot reference traversal limit") from exc
                except ValueError:
                    pass  # Ordinary text; declared JSON was validated above.
                else:
                    break
            if isinstance(parsed, (dict, list)):
                stack.append((iter([(parsed, field)]), None))
                continue
            for candidate, linked in candidates(body):
                # Extract URI boundaries before decoding escaped spaces.
                # Already-local paths may contain literal percent sequences.
                if Path(candidate).parent != root:
                    candidate = unquote(candidate)
                matches = list(token.finditer(candidate))
                if field in reference_fields or linked or format == 'text':
                    for native in re.findall(r"\.taskplane[/\\](snapshot-[^\s\"'<>()[\]{},;]+)", candidate):
                        if not re.fullmatch(PAIR.pattern + r"\.(?:html|json)", native.rstrip('.')):
                            raise ValueError(f"Malformed native snapshot reference: {candidate[:512]}")
                    # Ordinary fixture basenames remain outside the native namespace.
                    if re.match(r"snapshot-[a-fA-F0-9]{16}-", candidate) and not matches:
                        raise ValueError(f"Malformed native snapshot reference: {candidate[:512]}")
                for match in matches:
                    if native_path(candidate, field in reference_fields or linked):
                        found.add(Path(match.group(1)).stem)
    return found


def _pin_names(fd: int | Path, root: Path, names: set[str], reason: str) -> dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1024:
        raise ValueError("Snapshot pin requires a bounded reason")
    if not names:
        return {"status": "registered", "pairs": 0}
    verified_bytes = 0
    with _managed(fd, root, create=True) as db:
        for name in sorted(names):
            row = db.execute("SELECT * FROM pairs WHERE name=?", (name,)).fetchone()
            if row:
                item = _item(row, root)
                verified_bytes += sum(f["bytes"] for f in item["files"].values())
                if verified_bytes > MAX_VERIFY_BYTES:
                    raise ValueError("Snapshot reference verification budget")
                if row["deleting"] or not _verified(fd, item):
                    raise ValueError(f"Snapshot pin cannot verify immutable pair: {str(root / name)[:512]}")
                if reason not in item["pins"] and len(item["pins"]) < MAX_PIN_REASONS:
                    item["pins"].append(reason)
                db.execute("UPDATE pairs SET pinned=1,item=? WHERE name=?", (json.dumps(item), name))
            else:
                # Legacy bytes can be referenced, but never enter deletion ownership.
                for suffix in (".html", ".json"):
                    try:
                        raw = _read(fd, name + suffix, min(MAX_FILE_BYTES, MAX_VERIFY_BYTES - verified_bytes))
                    except (OSError, ValueError) as exc:
                        raise ValueError(f"Snapshot pin cannot read {str(root / (name + suffix))[:512]}: {str(exc)[:512]}") from exc
                    verified_bytes += len(raw)
        db.commit()
    return {"status": "registered", "pairs": len(names)}


def register_references(workspace: str | Path, value: Any, reason: str, *, format: str = "structured") -> dict[str, Any]:
    names = classify_snapshot_references(workspace, value, format=format)
    if not names:
        return {"status": "registered", "pairs": 0}
    root = _root(workspace)
    with _locked(root) as fd:
        return _pin_names(fd, root, names, reason)


def _collect(fd: int | Path, root: Path, db: sqlite3.Connection, *,
             max_pairs: int, max_bytes: int, now: float) -> dict[str, Any]:
    result: dict[str, Any] = {"at": _now(), "status": "partial", "removed_pairs": 0,
        "removed_bytes": 0, "candidates": 0, "verified_bytes": 0, "legacy_preserved": True}
    if isinstance(fd, Path):
        return {**result, "status": "skipped", "reason": "Snapshot collection requires no-follow directory operations"}
    sweep = db.execute("SELECT * FROM sweep WHERE id=1").fetchone()
    if sweep is None or (sweep["max_pairs"], sweep["max_bytes"]) != (max_pairs, max_bytes):
        high = db.execute("SELECT coalesce(max(seq),0) FROM pairs").fetchone()[0]
        db.execute("INSERT OR REPLACE INTO sweep VALUES (1,?,?,?,?,?,?)", (high+1, high, 0, 0, max_pairs, max_bytes))
        db.commit()
        sweep = db.execute("SELECT * FROM sweep WHERE id=1").fetchone()
    cursor, high, kept, size = (sweep[k] for k in ("cursor", "highwater", "kept_count", "kept_bytes"))
    for _ in range(MAX_CANDIDATES):
        # Interrupted deletion must complete before advancing a sweep.
        row = db.execute("SELECT * FROM pairs WHERE deleting=1 ORDER BY seq LIMIT 1").fetchone()
        if row is None:
            row = db.execute("SELECT * FROM pairs WHERE seq<? AND seq<=? ORDER BY seq DESC LIMIT 1", (cursor, high)).fetchone()
        if row is None:
            db.execute("DELETE FROM sweep WHERE id=1")
            db.commit()
            result["status"] = "complete"
            break
        item = _item(row, root)
        amount = sum(f["bytes"] for f in item["files"].values())
        protected = row["pinned"] or row["lease"] > now or db.execute(
            "SELECT 1 FROM protections WHERE name=? LIMIT 1", (row["name"],)).fetchone()
        remove = bool(row["deleting"] or not protected and (kept >= max_pairs or size + amount > max_bytes))
        if remove:
            if result["verified_bytes"] + amount > MAX_VERIFY_BYTES:
                result["reason"] = "verification_budget"
                break
            result["verified_bytes"] += amount
            if not _verified(fd, item, absent=bool(row["deleting"])):
                result.update(status="skipped", reason="managed pair identity mismatch")
                break
            db.execute("UPDATE pairs SET deleting=1 WHERE name=?", (row["name"],))
            db.commit()
            for filename in item["files"]:
                try:
                    os.unlink(filename, dir_fd=fd)
                except FileNotFoundError:
                    pass
            os.fsync(fd)
            db.execute("DELETE FROM pairs WHERE name=?", (row["name"],))
            result["removed_pairs"] += 1
            result["removed_bytes"] += amount
        elif not protected:
            kept += 1
            size += amount
        cursor = min(cursor, row["seq"])
        result["candidates"] += 1
        db.execute("UPDATE sweep SET cursor=?,kept_count=?,kept_bytes=? WHERE id=1", (cursor, kept, size))
        db.commit()
    result.update(cursor=cursor, highwater=high, retained_pairs=kept, retained_bytes=size)
    return result


def collect(workspace: str | Path, *, max_pairs: int = MAX_PAIRS, max_bytes: int = MAX_BYTES,
            now: float | None = None) -> dict[str, Any]:
    root = _root(workspace)
    try:
        with _locked(root, strict=True) as fd, _managed(fd, root) as db:
            return _collect(fd, root, db, max_pairs=max(0, max_pairs), max_bytes=max(0, max_bytes),
                            now=time.time() if now is None else now)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        return {"status": "skipped", "reason": str(exc)[:200], "removed_pairs": 0, "legacy_preserved": True}


def pin(workspace: str | Path, artifact: str | Path, reason: str) -> dict[str, Any]:
    root = _root(workspace)
    target = Path(artifact).absolute()
    if target.parent != root or not PAIR.fullmatch(target.stem) or target.suffix not in {".html", ".json"}:
        raise ValueError("Only a direct immutable native snapshot can be pinned")
    with _locked(root) as fd:
        _pin_names(fd, root, {target.stem}, reason)
    return {"status": "pinned", "artifact": str(target)}


def final_snapshot_links(workspace: Any, root_id: Any, run: Any,
                         observations: list[Any]) -> list[dict[str, str]]:
    """Resolve a bounded frozen index without trusting journal URLs or labels.

    Every link names a catalog-verified direct child of the captured workspace.
    Reading cannot update the catalog, selection, or immutable snapshot bytes.
    """
    links: list[dict[str, str]] = []
    if not all(isinstance(value, str) and value for value in (workspace, root_id, run)):
        return links
    try:
        root = _root(workspace)
        if str(root.parent) != workspace:
            return links
        with _directory(root) as fd:
            seen: set[str] = set()
            for row in observations[-32:]:
                if (not isinstance(row, dict) or row.get("run") != run
                        or row.get("session") != root_id or row.get("kind") != "final_snapshot"
                        or not isinstance(row.get("artifact"), str)):
                    continue
                target = Path(row["artifact"])
                if (target.parent != root or target.suffix != ".html"
                        or not PAIR.fullmatch(target.stem) or target.name in seen):
                    continue
                try:
                    with _managed(fd, root, readonly=True) as db:
                        managed_row = db.execute("SELECT * FROM pairs WHERE name=?", (target.stem,)).fetchone()
                        item = _item(managed_row, root) if managed_row else None
                except ValueError:
                    if (root / MARKER).exists():
                        raise
                    item = None
                if item is None:
                    item = _catalog(fd, root)["pairs"].get(target.stem)
                if (not item or item.get("deleting") or item.get("root") != root_id
                        or item.get("run") != run or not _verified(fd, item)):
                    continue
                raw = _read(fd, target.with_suffix(".json").name)
                if hashlib.sha256(raw).hexdigest() != item["files"][target.with_suffix(".json").name]["sha256"]:
                    continue
                saved = json.loads(raw)
                snapshot = saved.get("snapshot", {})
                boundary = saved.get("final_observation", {})
                if (not isinstance(boundary, dict) or not snapshot.get("historical")
                        or not saved.get("historical") or boundary.get("id") != row.get("closure_id")
                        or not re.fullmatch(r"[a-f0-9]{64}", str(boundary.get("id")))
                        or snapshot.get("final_observation") != boundary.get("id")
                        or boundary.get("authority") != "observation_only"
                        or boundary.get("transition") not in {"finish", "replacement", "interval_closed"}
                        or not isinstance(boundary.get("cutoff"), str)):
                    continue
                seen.add(target.name)
                links.append({"href": target.as_uri(), "cutoff": boundary["cutoff"],
                              "label": "At finish" if boundary["transition"] == "finish" else "Interval closed"})
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
        # Broken optional evidence must not prevent the remaining dashboard.
        return []
    return links


def publish_pair(workspace: str | Path, model: dict[str, Any], document: str, *,
                 target: Path | None = None, select: bool = False, detached: bool = False,
                 older: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
                 pin_reason: str | None = None) -> Path:
    """Commit immutable bytes before selection and collection; reuse exact bytes."""
    root = _root(workspace)
    if pin_reason is not None and (not isinstance(pin_reason, str) or not pin_reason.strip() or len(pin_reason) > 1024):
        raise ValueError("Snapshot pin requires a bounded reason")
    root.mkdir(exist_ok=True, mode=0o700)
    target = (target or root / "dashboard.html").absolute()
    if target.suffix != ".html" or target.is_symlink():
        raise ValueError("Dashboard target must be an ordinary HTML path")
    snapshot = model["snapshot"]
    if snapshot.get("workspace") != str(root.parent) or snapshot.get("run") != model.get("run"):
        raise ValueError("Snapshot identity disagrees with selected workspace/run")
    key = content_key(model)
    prefix = "snapshot-" + hashlib.sha256(str(model["run"]).encode()).hexdigest()[:16] + "-" + snapshot["digest"]
    if not PAIR.fullmatch(prefix):
        raise ValueError("Snapshot digest is invalid")
    names = classify_snapshot_references(workspace, model) - {prefix}
    with _locked(root) as fd:
        _pin_names(fd, root, names, "snapshot-reference")
        with _managed(fd, root, create=True) as db:
            row = db.execute("SELECT * FROM pairs WHERE content_key=? AND deleting=0 LIMIT 1", (key,)).fetchone()
            item = _item(row, root) if row else None
            if item and not _verified(fd, item):
                raise ValueError("Managed snapshot must retain ordinary immutable bytes")
            if item:
                prefix = row["name"]
                document_raw = _read(fd, prefix + ".html")
                saved_model = json.loads(_read(fd, prefix + ".json"))
                saved_snapshot = saved_model["snapshot"]
                model.clear()
                model.update(saved_model)
            else:
                if db.execute("SELECT 1 FROM pairs WHERE name=?", (prefix,)).fetchone():
                    raise ValueError("Snapshot publication cannot reuse a deleting or conflicting pair")
                document_raw = document.encode("utf-8")
                saved_snapshot = snapshot
                raw_model = primitives.canonical_bytes(model, ensure_ascii=True, trailing_newline=True)
                files = {prefix + ".html": document_raw, prefix + ".json": raw_model}
                if any(len(raw) > MAX_FILE_BYTES for raw in files.values()):
                    raise ValueError("Snapshot member exceeds admitted file size")
                owned = True
                for name, raw in files.items():
                    try:
                        _write(fd, name, raw, exclusive=True)
                    except FileExistsError:
                        if _read(fd, name) != raw:
                            raise ValueError("Immutable snapshot name has different bytes")
                        owned = False
                if owned:
                    item = {"workspace": str(root.parent), "root": snapshot.get("root"), "run": model["run"],
                            "key": key, "created_at": _now(), "pins": [],
                            "files": {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                                      for name, raw in files.items()}}
                    db.execute("INSERT INTO pairs(name,content_key,item) VALUES (?,?,?)", (prefix, key, json.dumps(item)))
            active = not any(model.get("workflow", {}).get(k) for k in ("finished", "superseded_by", "retired"))
            owner = json.dumps([snapshot.get("root"), model["run"]])
            db.execute("DELETE FROM protections WHERE kind='active' AND owner=?", (owner,))
            if item:
                if pin_reason and pin_reason not in item["pins"] and len(item["pins"]) < MAX_PIN_REASONS:
                    item["pins"].append(pin_reason)
                db.execute("UPDATE pairs SET item=?,pinned=?,lease=? WHERE name=?",
                           (json.dumps(item), bool(item["pins"]), time.time() + LEASE_SECONDS, prefix))
            if item and active:
                db.execute("INSERT OR REPLACE INTO protections VALUES ('active',?,?)", (owner, prefix))
            db.commit()
            immutable = root / (prefix + ".html")
            selection_name = target.with_suffix(".selection.json").name
            selected = False
            if not detached:
                with _directory(target.parent) as target_fd:
                    try:
                        previous = json.loads(_read(target_fd, selection_name))
                    except FileNotFoundError:
                        previous = {}
                    may_select = (not previous or previous.get("run") == model["run"] or select)
                    if may_select and not (previous.get("run") == model["run"] and older and older(previous, snapshot)):
                        # Retain old protection until both visible writes succeed.
                        db.execute("INSERT OR REPLACE INTO protections VALUES ('pending',?,?)", (json.dumps([str(target), prefix]), prefix))
                        db.commit()
                        _write(target_fd, target.name, document_raw)
                        selection = {"workspace": str(root.parent), "root": saved_snapshot.get("root"), "run": model["run"],
                                     "revision": saved_snapshot.get("revision", -1), "digest": saved_snapshot["digest"],
                                     "captured_at": snapshot.get("captured_at"), "generated_at": saved_snapshot.get("generated_at"),
                                     "observation_count": saved_snapshot.get("observation_count"), "snapshot": str(immutable),
                                     "refresh_attempted_at": _now(),
                                     "presentation": "generated; opening and visible verification are host observations"}
                        _write(target_fd, selection_name, primitives.canonical_bytes(selection, trailing_newline=True))
                        db.execute("INSERT OR REPLACE INTO protections VALUES ('selected',?,?)", (str(target), prefix))
                        db.execute("DELETE FROM protections WHERE kind='pending' AND owner=?", (json.dumps([str(target), prefix]),))
                        selected = True
            if selected or pin_reason and not detached:
                db.execute("DELETE FROM protections WHERE kind='active' AND owner=?", (owner,))
            db.commit()
            _collect(fd, root, db, max_pairs=MAX_PAIRS, max_bytes=MAX_BYTES, now=time.time())
            return target if selected else immutable
