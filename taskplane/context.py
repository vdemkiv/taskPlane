"""Immutable, digest-verified context data. This store conveys no authority."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from . import primitives, workflow as w, workflow_evidence as evidence

OBJECT_LIMIT = 8 * 1024 * 1024
PAGE_LIMIT = 16 * 1024
LEAF_LIMIT = 6 * 1024
PAGE_ITEMS = 64
REFERENCE_SCHEMA = "taskplane.context-reference/v1"


def encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


def signed(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "digest": digest(value)}


class Store:
    """Fixed-root immutable object tree; large values share independently hashed nodes."""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.root = evidence.path(self.workspace, ".taskplane/context-v1")

    def path(self, key: str) -> Path:
        w.require(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{64}", key),
                  "invalid_context", "Context IDs must be SHA-256 digests.")
        return evidence.path(self.workspace, f".taskplane/context-v1/objects/{key}.json")

    def _object(self, kind: str, data: Any, source_key: str, form: str) -> dict[str, Any]:
        value = {"schema": "taskplane.context-object/v1", "kind": kind,
                 "source_key": source_key, "form": form, "data": data}
        raw = encode(value)
        w.require(len(raw) <= OBJECT_LIMIT, "context_overflow", "Canonical context node is oversized.")
        key = hashlib.sha256(raw).hexdigest()
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with primitives.file_lock(str(target)):
            if target.exists():
                w.require(self._bytes(key) == raw, "invalid_context", "Immutable context was altered.")
            else:
                # Exclusive create avoids replacing a concurrent immutable object.
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
        return {"schema": REFERENCE_SCHEMA, "kind": kind, "sha256": key,
                "bytes": len(raw), "source_key": source_key}

    def put(self, kind: str, value: Any, source_key: str = "") -> dict[str, Any]:
        w.require(isinstance(kind, str) and 0 < len(kind) <= 128
                  and isinstance(source_key, str) and len(source_key) <= 256,
                  "invalid_context", "Invalid context object metadata.")
        raw = encode(value)
        source_key = source_key or hashlib.sha256(raw).hexdigest()
        if len(raw) <= LEAF_LIMIT:
            return self._object(kind, value, source_key, "value")
        if isinstance(value, (dict, list)) and len(value) > PAGE_ITEMS:
            # Pack small entries into real pages. One object per tiny element
            # makes inherited evidence require thousands of separate reads.
            chunks: list[Any] = []
            chunk: Any = {} if isinstance(value, dict) else []
            members = sorted(value.items()) if isinstance(value, dict) else enumerate(value)
            for key, body in members:
                candidate = {**chunk, key: body} if isinstance(value, dict) else [*chunk, body]
                if chunk and (len(chunk) >= PAGE_ITEMS or len(encode(candidate)) > LEAF_LIMIT):
                    chunks.append(chunk)
                    chunk = {} if isinstance(value, dict) else []
                if isinstance(value, dict):
                    chunk[key] = body
                else:
                    chunk.append(body)
            if chunk:
                chunks.append(chunk)
            refs = [self.put(kind, part) for part in chunks]
            return self._index(kind, refs, source_key, "dict-chunks" if isinstance(value, dict) else "list-chunks")
        # Small independently addressed bodies deduplicate unchanged inputs across phases.
        entries: list[Any]
        if isinstance(value, dict):
            entries = [[key, self.put(kind, body)] for key, body in sorted(value.items())]
            form = "dict"
        elif isinstance(value, list):
            entries = [self.put(kind, body) for body in value]
            form = "list"
        elif isinstance(value, str):
            entries = [self.put(kind, value[i:i + 1024]) for i in range(0, len(value), 1024)]
            form = "text"
        else:
            raise w.Refusal("context_overflow", "Scalar context cannot fit its canonical node.")
        return self._index(kind, entries, source_key, form)

    def _index(self, kind: str, entries: list[Any], source_key: str, form: str) -> dict[str, Any]:
        # Index nodes themselves are trees, so no large collection creates an oversized index.
        if len(encode(entries)) > LEAF_LIMIT:
            groups = [self._object(kind, entries[i:i + 8], source_key, "entries")
                      for i in range(0, len(entries), 8)]
            while len(encode(groups)) > LEAF_LIMIT:
                groups = [self._object(kind, groups[i:i + 8], source_key, "groups")
                          for i in range(0, len(groups), 8)]
            return self._object(kind, groups, source_key, form + "-groups")
        return self._object(kind, entries, source_key, form)

    def _bytes(self, key: str) -> bytes:
        target = self.path(key)
        try:
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                w.require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode),
                          "invalid_context", "Context must be a regular file.")
                raw = stream.read(OBJECT_LIMIT + 1)
            w.require(len(raw) <= OBJECT_LIMIT and hashlib.sha256(raw).hexdigest() == key,
                      "invalid_context", "Context size or digest mismatch.")
            return raw
        except OSError as exc:
            raise w.Refusal("invalid_context", "Context object unavailable: " + key) from exc

    def node(self, ref: str | dict[str, Any]) -> dict[str, Any]:
        key = ref if isinstance(ref, str) else ref.get("sha256", "")
        raw = self._bytes(key)
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise w.Refusal("invalid_context", "Context is not valid JSON.") from exc
        w.require(isinstance(value, dict) and value.get("schema") == "taskplane.context-object/v1"
                  and value.get("form") in {"value", "dict", "list", "text", "entries", "groups",
                                            "dict-groups", "list-groups", "text-groups",
                                            "dict-chunks", "list-chunks", "dict-chunks-groups", "list-chunks-groups"},
                  "invalid_context", "Invalid canonical context schema.")
        if isinstance(ref, dict):
            w.require(ref == {"schema": REFERENCE_SCHEMA, "kind": value["kind"],
                              "sha256": key, "bytes": len(raw), "source_key": value["source_key"]},
                      "invalid_context", "Context reference metadata differs from its object.")
        return dict(value)

    def children(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        if node["form"] == "value":
            return []
        if node["form"] == "dict":
            return [entry[1] for entry in node["data"]]
        if node["form"] == "entries":
            return [entry[1] if isinstance(entry, list) else entry for entry in node["data"]]
        return list(node["data"])

    def resolve(self, ref: str | dict[str, Any]) -> Any:
        budget = [20000, 128 * 1024 * 1024]
        def read(item: str | dict[str, Any], depth: int = 0) -> Any:
            w.require(depth <= 32 and budget[0] > 0 and budget[1] > 0,
                      "context_overflow", "Context expansion exceeds its finite read budget.")
            node = self.node(item)
            budget[0] -= 1
            budget[1] -= len(encode(node))
            form, data = node["form"], node["data"]
            if form == "value":
                return data
            if form == "entries":
                return data
            if form == "groups" or form.endswith("-groups"):
                entries = [entry for group in data for entry in read(group, depth + 1)]
                if form == "groups":
                    return entries
                data, form = entries, form.removesuffix("-groups")
            if form == "dict":
                return {key: read(value, depth + 1) for key, value in data}
            values = [read(value, depth + 1) for value in data]
            if form == "dict-chunks":
                return {key: value for chunk in values for key, value in chunk.items()}
            if form == "list-chunks":
                return [item for chunk in values for item in chunk]
            return "".join(values) if form == "text" else values
        return read(ref)

    def descendants(self, ref: dict[str, Any]) -> set[str]:
        seen: set[str] = set()
        pending = [ref]
        while pending:
            current = pending.pop()
            self.node(current)  # Validate metadata even when another edge reused the same digest.
            if current["sha256"] in seen:
                continue
            w.require(len(seen) < 20000, "context_overflow", "Context reference tree is too large.")
            seen.add(current["sha256"])
            pending.extend(self.children(self.node(current)))
        return seen

    def page(self, key: str, page: int = 0, section: str | None = None) -> dict[str, Any]:
        node = self.node(key)
        w.require(type(page) is int and page >= 0, "invalid_context", "Invalid page cursor.")
        data = node["data"]
        if section is not None:
            w.require(node["form"] in {"dict", "value"}, "invalid_context",
                      "Read an index child before selecting a section.")
            source = dict(data) if node["form"] == "dict" else data
            w.require(isinstance(source, dict) and section in source,
                      "invalid_context", "Unknown context section.")
            data = {section: source[section]}
        # Canonical node bodies normally fit one page; bounded indexed collections
        # still retain an explicit cursor and exact count.
        entries = data if isinstance(data, list) else [data]
        pages = [entries[i:i + PAGE_ITEMS] for i in range(0, len(entries), PAGE_ITEMS)] or [[]]
        w.require(page < len(pages), "invalid_context", "Page cursor is out of range.")
        result = {"schema": "taskplane.context-page/v1", "sha256": key,
                  "kind": node["kind"], "form": node["form"], "section": section,
                  "page": page, "pages": len(pages), "total": len(entries),
                  "data": pages[page] if isinstance(data, list) else data,
                  "next_page": page + 1 if page + 1 < len(pages) else None,
                  "untrusted_data": True}
        w.require(len(encode(result)) <= PAGE_LIMIT, "context_overflow", "Context page exceeds its budget.")
        return result

    def register(self, binding: dict[str, Any], refs: list[dict[str, Any]]) -> None:
        """Index data returned by official commands for this exact current binding."""
        target = evidence.path(self.workspace, f".taskplane/context-v1/reads/{digest(binding)}.json")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with primitives.file_lock(str(target)):
            previous = self.roots(binding)
            indexed = {ref["sha256"]: ref for ref in previous + refs}
            w.require(len(indexed) <= 4096, "context_overflow", "Current context read index is full.")
            primitives.atomic_json(target, {"binding": binding, "roots": list(indexed.values())})

    def roots(self, binding: dict[str, Any]) -> list[dict[str, Any]]:
        relative = f".taskplane/context-v1/reads/{digest(binding)}.json"
        target = evidence.path(self.workspace, relative)
        if not target.exists():
            return []
        w.require(target.stat().st_size <= OBJECT_LIMIT, "context_overflow", "Context read index is oversized.")
        value = evidence.object_file(self.workspace, relative)
        w.require(value.get("binding") == binding and isinstance(value.get("roots"), list),
                  "invalid_context", "Foreign context read index.")
        return list(value["roots"])
