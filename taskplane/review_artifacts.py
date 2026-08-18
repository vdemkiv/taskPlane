"""Atomic, lossless publication of governed review revisions.

The immutable review model is the authority.  JSON, Markdown, and HTML are
three projections of the same sanitized value; each projection embeds a
machine-readable copy and is parsed back before the manifest is committed.
The manifest is the only publication pointer, so a renderer/write failure may
leave harmless content-addressed objects but can never advertise a partial
artifact set.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping


ARTIFACT_MODEL_SCHEMA = "taskplane.review-artifact-model/v1"
ARTIFACT_SET_SCHEMA = "taskplane.review-artifact-set/v1"
_REQUIRED_SECTIONS = (
    "dor", "criteria", "slots", "findings", "validation", "collection",
    "provenance", "gate",
)
_FORMATS = ("json", "markdown", "html")
_EXTENSIONS = {"json": "json", "markdown": "md", "html": "html"}
_MARKDOWN_BEGIN = "<!-- taskplane-semantic-model:begin -->"
_MARKDOWN_END = "<!-- taskplane-semantic-model:end -->"
_HTML_PATTERN = re.compile(
    rb'<script id="taskplane-semantic-model" type="application/json">'
    rb'(.*?)</script>', re.DOTALL)
_SAFE_COMPONENT = re.compile(r"^[0-9a-f]{64}\.(?:json|md|html)$")

_BEARER = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{6,}")
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|token|password|passwd|secret)"
    r"\s*[:=]\s*)[^\s,;]+")
_PERSONAL_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:/Users/[^/\s]+|/home/[^/\s]+|"
    r"[A-Za-z]:\\Users\\[^\\\s]+)(?:[/\\][^\s,;:]+)*")


class ArtifactPublicationError(ValueError):
    """Artifact input or destination violates the publication contract."""


def _canonical_bytes(value) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False) + "\n").encode(
                               "utf-8")
    except (TypeError, ValueError) as exc:
        raise ArtifactPublicationError(
            "review artifact model must be canonical JSON") from exc


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _redact_text(value: str) -> tuple[str, int]:
    count = 0

    def replace(pattern, text, replacement):
        nonlocal count
        text, matches = pattern.subn(replacement, text)
        count += matches
        return text

    value = replace(_BEARER, value, r"\1[REDACTED]")
    value = replace(_ASSIGNMENT_SECRET, value, r"\1[REDACTED]")
    value = replace(_PERSONAL_PATH, value, "[REDACTED]")
    return value, count


def _sanitize(value) -> tuple[object, int]:
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value, 0
    if isinstance(value, list):
        out, count = [], 0
        for item in value:
            clean, item_count = _sanitize(item)
            out.append(clean)
            count += item_count
        return out, count
    if isinstance(value, dict):
        out, count = {}, 0
        for key, item in value.items():
            if not isinstance(key, str):
                raise ArtifactPublicationError(
                    "review artifact model keys must be strings")
            clean, item_count = _sanitize(item)
            out[key] = clean
            count += item_count
        return out, count
    raise ArtifactPublicationError(
        "review artifact model contains a non-JSON value")


def _validate_model(model: object) -> dict:
    if not isinstance(model, Mapping):
        raise ArtifactPublicationError("review artifact model must be an object")
    row = copy.deepcopy(dict(model))
    if row.get("schema") != ARTIFACT_MODEL_SCHEMA:
        raise ArtifactPublicationError("review artifact model schema is invalid")
    revision = row.get("revision")
    if not isinstance(revision, dict):
        raise ArtifactPublicationError("review artifact model misses revision")
    disposition = revision.get("disposition")
    if disposition not in ("provisional", "canonical"):
        raise ArtifactPublicationError("revision disposition is invalid")
    status = revision.get("status")
    if (disposition == "provisional" and status == "complete") or \
            (disposition == "canonical" and status != "complete"):
        raise ArtifactPublicationError(
            "revision status contradicts its disposition")
    for section in _REQUIRED_SECTIONS:
        if section not in row:
            raise ArtifactPublicationError(
                "review artifact model misses " + section)
    for section in ("criteria", "slots", "findings"):
        if not isinstance(row[section], list):
            raise ArtifactPublicationError(section + " must be a list")
    for section in ("dor", "validation", "collection", "provenance", "gate"):
        if not isinstance(row[section], dict):
            raise ArtifactPublicationError(section + " must be an object")
    approval = bool(row["gate"].get("approval_enabled"))
    if disposition == "provisional" and approval:
        raise ArtifactPublicationError(
            "provisional revision cannot enable approval")
    # Prove JSON compatibility at the trust boundary before rendering.
    _canonical_bytes(row)
    return row


def sanitize_model(model: Mapping) -> dict:
    """Validate and policy-redact one model shared by every projection."""
    validated = _validate_model(model)
    sanitized, redaction_count = _sanitize(validated)
    # Redaction cannot change the structural contract, but validate again so a
    # future policy rule cannot accidentally synthesize an invalid model.
    sanitized = _validate_model(sanitized)
    return {"model": sanitized, "redaction_count": redaction_count}


def _render_markdown(model: dict, semantic: bytes) -> bytes:
    revision = model["revision"]
    lines = [
        f"# Review {revision.get('id', '')}", "",
        f"- Disposition: {revision['disposition']}",
        f"- Status: {revision['status']}",
        f"- Findings: {len(model['findings'])}", "",
    ]
    headings = (
        ("Definition of Ready", "dor"),
        ("Acceptance criteria", "criteria"),
        ("Slots and lenses", "slots"),
        ("Findings", "findings"),
        ("Dynamic validation", "validation"),
        ("Collection", "collection"),
        ("Provenance", "provenance"),
        ("Gate", "gate"),
    )
    for title, key in headings:
        lines.extend((f"## {title}", "", "```json",
                      json.dumps(model[key], indent=2, ensure_ascii=False,
                                 sort_keys=True, allow_nan=False),
                      "```", ""))
    lines.extend((_MARKDOWN_BEGIN, "```json",
                  semantic.decode("utf-8").rstrip("\n"), "```",
                  _MARKDOWN_END, ""))
    return "\n".join(lines).encode("utf-8")


def _render_html(model: dict, semantic: bytes) -> bytes:
    # Escape '<' inside the script element so untrusted strings cannot close
    # it.  JSON decoding restores the exact original characters.
    embedded = semantic.decode("utf-8").rstrip("\n").replace("<", "\\u003c")
    revision = model["revision"]
    sections = []
    for title, key in (
            ("Definition of Ready", "dor"),
            ("Acceptance criteria", "criteria"),
            ("Slots and lenses", "slots"),
            ("Findings", "findings"),
            ("Dynamic validation", "validation"),
            ("Collection", "collection"),
            ("Provenance", "provenance"),
            ("Gate", "gate")):
        pretty = json.dumps(model[key], indent=2, ensure_ascii=False,
                            sort_keys=True, allow_nan=False)
        sections.append(
            f'<section><h2>{html.escape(title)}</h2><pre>{html.escape(pretty)}</pre>'
            "</section>")
    body = "".join(sections)
    document = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>Review {html.escape(str(revision.get('id') or ''))}</title>"
        "<style>body{font:16px system-ui;max-width:84rem;margin:auto;padding:2rem}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:1px "
        "solid #888;padding-block:1rem}</style></head><body>"
        f"<h1>Review {html.escape(str(revision.get('id') or ''))}</h1>"
        f"<p>{html.escape(str(revision['disposition']))} · "
        f"{len(model['findings'])} findings</p>{body}"
        f'<script id="taskplane-semantic-model" type="application/json">'
        f"{embedded}</script></body></html>"
    )
    return document.encode("utf-8")


def _render_all(model: dict) -> dict[str, bytes]:
    semantic = _canonical_bytes(model)
    return {
        "json": semantic,
        "markdown": _render_markdown(model, semantic),
        "html": _render_html(model, semantic),
    }


def parse_artifact(kind: str, data: bytes) -> dict:
    """Parse the authoritative semantic model from any artifact format."""
    if kind not in _FORMATS or not isinstance(data, bytes):
        raise ArtifactPublicationError("unknown artifact format")
    try:
        if kind == "json":
            payload = data
        elif kind == "markdown":
            text = data.decode("utf-8")
            begin = text.index(_MARKDOWN_BEGIN) + len(_MARKDOWN_BEGIN)
            end = text.index(_MARKDOWN_END, begin)
            block = text[begin:end].strip()
            if not block.startswith("```json\n") or not block.endswith("\n```"):
                raise ValueError("invalid Markdown semantic block")
            payload = block[len("```json\n"):-len("\n```")].encode("utf-8")
        else:
            match = _HTML_PATTERN.search(data)
            if match is None:
                raise ValueError("missing HTML semantic model")
            payload = match.group(1)
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ArtifactPublicationError(
            f"{kind} artifact semantic payload is invalid") from exc
    return _validate_model(decoded)


def _assert_safe_root(root: str) -> str:
    raw_root = os.fspath(root) if root is not None else ""
    if not raw_root or not os.path.isabs(raw_root):
        raise ArtifactPublicationError(
            "artifact output root must be an explicit absolute path")
    root = os.path.abspath(raw_root)
    _assert_no_symlink_components(root, "artifact output root")
    if os.path.lexists(root) and not os.path.isdir(root):
        raise ArtifactPublicationError("artifact output root is not a directory")
    os.makedirs(root, mode=0o700, exist_ok=True)
    # Recheck after creation.  This also catches a pre-existing symlink in any
    # ancestor, rather than checking only the nearest existing component.
    _assert_no_symlink_components(root, "artifact output root")
    if not os.path.isdir(root):
        raise ArtifactPublicationError("artifact output root is not a directory")
    return root


def _assert_no_symlink_components(path: str, label: str) -> None:
    """Reject a symlink in every existing component of an absolute path."""
    current = os.path.sep
    for component in os.path.abspath(path).split(os.path.sep)[1:]:
        current = os.path.join(current, component)
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ArtifactPublicationError(f"{label} uses a symlink")


def _assert_confined(root: str, path: str, label: str, *,
                     existing_file: bool = False) -> None:
    """Prove a publisher-controlled path is below the sealed output root."""
    candidate = os.path.abspath(path)
    try:
        confined = os.path.commonpath((root, candidate)) == root
    except ValueError:
        confined = False
    if not confined or candidate == root:
        raise ArtifactPublicationError(f"{label} escapes artifact output root")
    _assert_no_symlink_components(candidate, label)
    if os.path.lexists(candidate):
        mode = os.lstat(candidate).st_mode
        if existing_file and not stat.S_ISREG(mode):
            raise ArtifactPublicationError(f"{label} is not a regular file")


def _atomic_write(path: str, data: bytes) -> None:
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=".publish-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Persist the directory entry where the platform supports it.
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _put_object(root: str, kind: str, data: bytes) -> dict:
    digest = _digest(data)
    name = f"{digest}.{_EXTENSIONS[kind]}"
    if not _SAFE_COMPONENT.fullmatch(name):
        raise ArtifactPublicationError("unsafe artifact object name")
    directory = os.path.join(root, "objects")
    _assert_confined(root, directory, "artifact object directory")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    _assert_confined(root, directory, "artifact object directory")
    if not os.path.isdir(directory):
        raise ArtifactPublicationError(
            "artifact object directory is not a directory")
    path = os.path.join(directory, name)
    _assert_confined(root, path, "artifact object path", existing_file=True)
    if os.path.lexists(path):
        with open(path, "rb") as stream:
            if stream.read() != data:
                raise ArtifactPublicationError(
                    "content-addressed artifact collision")
    else:
        _atomic_write(path, data)
    return {
        "format": kind, "digest": digest, "bytes": len(data),
        "relative_path": "objects/" + name,
    }


def _failure(reason: str, model: object) -> dict:
    findings = model.get("findings") if isinstance(model, Mapping) else None
    return {
        "schema": "taskplane.review-artifact-publication/v1",
        "status": "unavailable", "completed": False,
        "approval_enabled": False,
        "finding_count": len(findings) if isinstance(findings, list) else None,
        "reason": str(reason), "action": "retry artifact publication",
    }


def publish_revision_artifacts(root: str, model: Mapping, *,
                               fault: str | None = None) -> dict:
    """Atomically advertise JSON/Markdown/HTML for one immutable revision.

    A returned ``unavailable`` value is intentionally non-successful and keeps
    the finding count when known.  The previous manifest, if any, remains the
    stable publication pointer until the whole replacement verifies.
    """
    output_root = _assert_safe_root(root)
    try:
        sanitized = sanitize_model(model)
        clean = sanitized["model"]
        rendered = _render_all(clean)
        parsed = {kind: parse_artifact(kind, data)
                  for kind, data in rendered.items()}
        if any(value != clean for value in parsed.values()):
            raise ArtifactPublicationError(
                "artifact formats are not semantically equivalent")
        references = {kind: _put_object(output_root, kind, rendered[kind])
                      for kind in _FORMATS}
        if fault == "before-manifest":
            raise ArtifactPublicationError(
                "injected artifact failure before manifest commit")
        if fault is not None:
            raise ArtifactPublicationError("unknown artifact failure injection")
        semantic = _canonical_bytes(clean)
        revision = clean["revision"]
        core = {
            "schema": ARTIFACT_SET_SCHEMA,
            "status": "published",
            "revision": copy.deepcopy(revision),
            "semantic_fingerprint": _digest(semantic),
            "semantic_bytes": len(semantic),
            "finding_count": len(clean["findings"]),
            "redaction_count": sanitized["redaction_count"],
            "semantic_equality": True,
            "artifacts": references,
        }
        manifest = dict(core, fingerprint=_digest(_canonical_bytes(core)))
        manifest_bytes = _canonical_bytes(manifest)
        manifest_path = os.path.join(output_root, "artifact-set.json")
        _assert_confined(output_root, manifest_path, "artifact manifest",
                         existing_file=True)
        _atomic_write(manifest_path, manifest_bytes)
        return {
            "schema": "taskplane.review-artifact-publication/v1",
            "status": "published",
            "completed": revision["disposition"] == "canonical",
            "approval_enabled": bool(clean["gate"].get("approval_enabled")),
            "finding_count": len(clean["findings"]),
            "semantic_bytes": len(semantic),
            "manifest_fingerprint": manifest["fingerprint"],
            "manifest_path": "artifact-set.json",
            "artifacts": copy.deepcopy(references),
        }
    except (ArtifactPublicationError, OSError) as exc:
        return _failure(str(exc), model)
