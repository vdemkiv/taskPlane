"""Advisory snapshot ownership, permanent pins and bounded transient collection.

Only pairs created by this catalog can be collected. No controller APIs are
called, and a caller must not hold a controller lock while invoking this module.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
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
PAIR = re.compile(r"snapshot-[a-f0-9]{16}-[a-f0-9]{64}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(workspace: str | Path) -> Path:
    from .workspace_binding import validate_workspace_root
    return validate_workspace_root(workspace).resolve() / ".taskplane"


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    """Pin all path components; never follow a link during later mutation."""
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise ValueError("Snapshot mutation requires no-follow directory operations")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read(fd: int, name: str, limit: int = MAX_FILE_BYTES) -> bytes:
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(child, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("Snapshot input is not a bounded ordinary file")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(raw) > limit or (before.st_size, before.st_mtime_ns, before.st_ino) != (
                after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError("Snapshot input changed while reading")
        return raw


def _write(fd: int, name: str, raw: bytes, *, exclusive: bool = False) -> None:
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
def _locked(root: Path) -> Iterator[int]:
    with _directory(root) as fd:
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


def _catalog(fd: int, root: Path) -> dict[str, Any]:
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


def _save(fd: int, value: dict[str, Any]) -> None:
    _write(fd, CATALOG, primitives.canonical_bytes(value, trailing_newline=True))


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


def _verified(fd: int, item: dict[str, Any], *, absent: bool = False) -> bool:
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

    def visit(fd: int, prefix: str, depth: int) -> None:
        nonlocal consumed
        if depth > max_depth:
            error("depth_limit")
            return
        before = os.fstat(fd)
        try:
            with os.scandir(fd) as entries:
                for entry in entries:
                    if result["entries"] >= max_entries or time.monotonic() - started >= max_seconds:
                        error("entry_or_time_limit")
                        break
                    relative = prefix + entry.name
                    result["entries"] += 1
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
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
                                if "snapshot-" in text and not PAIR.search(text):
                                    error("unknown_snapshot_reference")
                            except (ValueError, UnicodeError):
                                error("unreadable_reference:" + relative)
                    else:
                        error("link_or_special_file:" + relative)
        except (OSError, ValueError) as exc:
            error(type(exc).__name__ + ":" + prefix)
        after = os.fstat(fd)
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
        with _directory(root) as fd:
            catalog = _catalog(fd, root)
            pairs = catalog["pairs"]
            totals = {"pinned_bytes": 0, "transient_bytes": 0, "protected_bytes": 0, "managed_pairs": len(pairs)}
            protected = set(catalog["selected"].values())
            for name, item in pairs.items():
                size = sum(v["bytes"] for v in item["files"].values())
                totals["pinned_bytes" if item["pins"] else "transient_bytes"] += size
                if item["pins"] or name in protected or item.get("lease_until", 0) > time.time() or item.get("active_latest"):
                    totals["protected_bytes"] += size
            total = value["categories"].get("snapshots", {}).get("bytes", 0)
            totals["legacy_bytes"] = max(0, total - totals["pinned_bytes"] - totals["transient_bytes"])
            value["snapshots"] = {"status": "catalogued", **totals,
                                  "last_collection": catalog.get("last_collection")}
    except FileNotFoundError:
        value["snapshots"] = {"status": "legacy", "legacy_bytes": value["categories"].get("snapshots", {}).get("bytes", 0)}
    except (OSError, ValueError, TypeError):
        value["snapshots"] = {"status": "unknown", "reason": "catalog unavailable; no collection"}
    return value


def _collect(fd: int, root: Path, catalog: dict[str, Any], before: dict[str, Any],
             *, max_pairs: int, max_bytes: int, now: float) -> dict[str, Any]:
    current = _scan(root, references=True)
    result: dict[str, Any] = {"at": _now(), "status": "skipped", "removed_pairs": 0, "removed_bytes": 0}
    if not before["complete"] or not current["complete"] or before["fingerprint"] != current["fingerprint"]:
        result["reason"] = "reference inventory incomplete or changed"
        return result
    pairs = catalog["pairs"]
    referenced = current["references"]
    # Unknown references are never converted into evidence of absence.
    for name in referenced - set(pairs):
        try:
            for suffix in (".html", ".json"):
                info = os.stat(name + suffix, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("referenced pair is not ordinary")
        except (OSError, ValueError):
            result["reason"] = "unknown snapshot reference"
            return result
    protected = referenced | set(catalog["selected"].values())
    for name in current["permanent_references"] & set(pairs):
        if "durable-reference" not in pairs[name]["pins"]:
            pairs[name]["pins"].append("durable-reference")
    _save(fd, catalog)
    protected.update(name for name, item in pairs.items() if item["pins"] or item.get("active_latest")
                     or item.get("lease_until", 0) > now)
    eligible = [(name, item) for name, item in pairs.items() if name not in protected]
    eligible.sort(key=lambda row: row[1]["order"])
    size = sum(sum(f["bytes"] for f in item["files"].values()) for _, item in eligible)
    count = len(eligible)
    for name, item in eligible:
        if count <= max_pairs and size <= max_bytes and not item.get("deleting"):
            continue
        if not _verified(fd, item, absent=bool(item.get("deleting"))):
            result.update(reason="managed pair identity mismatch", status="partial")
            break
        # Persist intent first. Interrupted pair removal remains known garbage;
        # no subsequent publisher or pin may reuse it as immutable evidence.
        item["deleting"] = True
        _save(fd, catalog)
        for filename in item["files"]:
            try:
                os.unlink(filename, dir_fd=fd)
            except FileNotFoundError:
                pass
        os.fsync(fd)
        amount = sum(f["bytes"] for f in item["files"].values())
        size -= amount
        count -= 1
        result["removed_pairs"] += 1
        result["removed_bytes"] += amount
        del pairs[name]
        _save(fd, catalog)
    if result["status"] == "skipped":
        result["status"] = "complete"
    result.update(eligible_pairs=count, eligible_bytes=size, protected_pairs=len(protected & set(pairs)))
    return result


def collect(workspace: str | Path, *, max_pairs: int = MAX_PAIRS, max_bytes: int = MAX_BYTES,
            now: float | None = None) -> dict[str, Any]:
    root = _root(workspace)
    before = _scan(root, references=True)
    try:
        with _locked(root) as fd:
            catalog = _catalog(fd, root)
            result = _collect(fd, root, catalog, before, max_pairs=max(0, max_pairs),
                              max_bytes=max(0, max_bytes), now=time.time() if now is None else now)
            catalog["last_collection"] = result
            _save(fd, catalog)
            return result
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "skipped", "reason": str(exc)[:200], "removed_pairs": 0}


def pin(workspace: str | Path, artifact: str | Path, reason: str) -> dict[str, Any]:
    """Pin before a durable reference is stored. Legacy pairs remain untouched."""
    root = _root(workspace)
    target = Path(artifact).absolute()
    if target.parent != root or not PAIR.fullmatch(target.stem) or target.suffix not in {".html", ".json"}:
        raise ValueError("Only a direct immutable native snapshot can be pinned")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1024:
        raise ValueError("Snapshot pin requires a bounded reason")
    with _locked(root) as fd:
        catalog = _catalog(fd, root)
        item = catalog["pairs"].get(target.stem)
        if item is None:
            for suffix in (".html", ".json"):
                _read(fd, target.stem + suffix)
            return {"status": "legacy_preserved", "artifact": str(target)}
        if item.get("deleting") or not _verified(fd, item):
            raise ValueError("Snapshot pin cannot verify immutable pair")
        if reason not in item["pins"]:
            item["pins"].append(reason)
            _save(fd, catalog)
        return {"status": "pinned", "artifact": str(target), "reasons": list(item["pins"])}


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
            catalog = _catalog(fd, root)
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
                item = catalog["pairs"].get(target.stem)
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
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Broken optional evidence must not prevent the remaining dashboard.
        return []
    return links


def publish_pair(workspace: str | Path, model: dict[str, Any], document: str, *,
                 target: Path | None = None, select: bool = False, detached: bool = False,
                 older: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
                 pin_reason: str | None = None) -> Path:
    """Commit immutable bytes before selection and collection; reuse exact bytes."""
    root = _root(workspace)
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
    with _locked(root) as fd:
        catalog = _catalog(fd, root)
        item = next((entry for entry in catalog["pairs"].values()
                     if entry.get("key") == key and not entry.get("deleting") and _verified(fd, entry)), None)
        if item:
            prefix = next(name for name, entry in catalog["pairs"].items() if entry is item)
            document_raw = _read(fd, prefix + ".html")
            saved_model = json.loads(_read(fd, prefix + ".json"))
            saved_snapshot = saved_model["snapshot"]
            # The returned report must identify the reused bytes as well.
            # `snapshot` still holds this refresh attempt for ordering below.
            model.clear()
            model.update(saved_model)
        else:
            document_raw = document.encode("utf-8")
            saved_snapshot = snapshot
            raw_model = primitives.canonical_bytes(model, ensure_ascii=True, trailing_newline=True)
            files = {prefix + ".html": document_raw, prefix + ".json": raw_model}
            owned = True
            for name, raw in files.items():
                try:
                    _write(fd, name, raw, exclusive=True)
                except FileExistsError:
                    if _read(fd, name) != raw:
                        raise ValueError("Immutable snapshot name has different bytes")
                    # Never adopt a legacy file into deletion ownership.
                    owned = owned and prefix in catalog["pairs"]
            if owned:
                catalog["sequence"] += 1
                item = {"workspace": str(root.parent), "root": snapshot.get("root"), "run": model["run"],
                        "key": key, "order": catalog["sequence"], "created_at": _now(), "pins": [],
                        "files": {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                                  for name, raw in files.items()}}
                catalog["pairs"][prefix] = item
        if item:
            item["lease_until"] = time.time() + LEASE_SECONDS
            if pin_reason and pin_reason not in item["pins"]:
                item["pins"].append(pin_reason)
            active = not any(model.get("workflow", {}).get(k) for k in ("finished", "superseded_by", "retired"))
            for entry in catalog["pairs"].values():
                if (entry.get("root"), entry.get("run")) == (snapshot.get("root"), model["run"]):
                    entry["active_latest"] = False
            item["active_latest"] = bool(active and detached)
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
                    # Catalog commit precedes every durable selection. Failure
                    # before this point permits no collection at all.
                    catalog["selected"][str(target)] = prefix
                    _save(fd, catalog)
                    _write(target_fd, target.name, document_raw)
                    selection = {"workspace": str(root.parent), "root": saved_snapshot.get("root"), "run": model["run"],
                                 "revision": saved_snapshot.get("revision", -1), "digest": saved_snapshot["digest"],
                                 "captured_at": snapshot.get("captured_at"), "generated_at": saved_snapshot.get("generated_at"),
                                 "observation_count": saved_snapshot.get("observation_count"), "snapshot": str(immutable),
                                 "refresh_attempted_at": _now(),
                                 "presentation": "generated; opening and visible verification are host observations"}
                    _write(target_fd, selection_name, primitives.canonical_bytes(selection, trailing_newline=True))
                    selected = True
        if item and not selected and not pin_reason:
            item["active_latest"] = bool(active)
        _save(fd, catalog)
        # Publisher and collector use one lock; controller/reference files are
        # read without acquiring their lock. Recheck the reference fingerprint.
        before = _scan(root, references=True)
        result = _collect(fd, root, catalog, before, max_pairs=MAX_PAIRS, max_bytes=MAX_BYTES, now=time.time())
        catalog["last_collection"] = result
        _save(fd, catalog)
        return target if selected else immutable
