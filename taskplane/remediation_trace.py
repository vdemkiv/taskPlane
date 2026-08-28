"""Exact-candidate, receipt-backed remediation evidence and closure gates.

Build and Evaluate authority is represented by closed, content-addressed
receipts. The canonical trace is derived from those receipts; callers never
supply outcome words, finding metadata, selectors, or free-form role labels.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Mapping, Sequence


H1_FINDING_IDS = (
    "H-03", "H-04", "H-05", "H-06", "H-07", "H-08", "H-14",
    "H-15", "H-19", "H-22", "H-26", "H-30", "H-34",
)
H1_TRACE_SCHEMA = "taskplane.remediation-h1-trace/v2"
H1_RESULT_SCHEMA = "taskplane.remediation-finding-result/v2"
BUILD_RECEIPT_SCHEMA = "taskplane.remediation-build-receipt/v1"
EVALUATE_RECEIPT_SCHEMA = "taskplane.remediation-evaluate-receipt/v1"
IDENTITY_SCHEMA = "taskplane.remediation-agent-identity/v1"
GIT_EVIDENCE_SCHEMA = "taskplane.trusted-git-evidence/v1"
SELECTOR_EXECUTION_SCHEMA = "taskplane.selector-execution/v1"
PRICED_DEBT_SCHEMA = "taskplane.remediation-priced-debt/v1"
PRICED_DEBT_TRACE_SCHEMA = "taskplane.remediation-priced-debt-trace/v1"
_SHA = re.compile(r"[0-9a-f]{40,64}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")
_DEBT_ID = re.compile(r"D-[0-9]{4,}\Z")
_MAX_RECEIPT_BYTES = 1024 * 1024
_DEBT_COST_COMPONENTS = (
    "backfill", "migration", "compatibility", "operator_reteaching", "other",
)
_PRICED_DEBT_IDS = ("D-1301", "D-1302", "D-1303")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_PRICED_DEBT_SPEC = _REPOSITORY_ROOT / "specs" / "spec.md"
_DEBT_AUTHORITY_START = "<!-- taskplane:priced-debt-authority:v1:start -->"
_DEBT_AUTHORITY_END = "<!-- taskplane:priced-debt-authority:v1:end -->"
_VAGUE_TRIGGERS = {"none", "n/a", "na", "tbd", "unknown", "someday", "later"}


class RemediationTraceError(ValueError):
    """A remediation receipt set is incomplete, stale, or self-attested."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _debt_text(value: object, label: str, *, limit: int = 2048) -> str:
    if (not isinstance(value, str) or value != value.strip() or not value or
            len(value) > limit or any(ord(character) < 32 for character in value)):
        raise RemediationTraceError(f"priced debt {label} is invalid")
    return value


def _reentry_trigger(value: object) -> dict:
    """Require an observable signal, its threshold, and the resulting action."""
    if not isinstance(value, Mapping) or set(value) != {
            "signal", "threshold", "action"}:
        raise RemediationTraceError(
            "priced debt re-entry trigger must define signal, threshold, and action")
    signal = _debt_text(value.get("signal"), "re-entry signal", limit=256)
    threshold = _debt_text(value.get("threshold"), "re-entry threshold")
    action = _debt_text(value.get("action"), "re-entry action")
    if (not _IDENTIFIER.fullmatch(signal) or
            signal.casefold() in _VAGUE_TRIGGERS or
            threshold.casefold() in _VAGUE_TRIGGERS or
            action.casefold() in _VAGUE_TRIGGERS):
        raise RemediationTraceError("priced debt re-entry trigger is not actionable")
    return {"signal": signal, "threshold": threshold, "action": action}


def _repository_reference(value: object) -> str:
    """Resolve one repository-relative path plus explicit retained anchor."""
    reference = _debt_text(value, "reference", limit=512)
    path_text, separator, anchor = reference.partition("#")
    if (separator != "#" or not path_text or not anchor or
            path_text.startswith("/") or ".." in Path(path_text).parts or
            not _IDENTIFIER.fullmatch(anchor)):
        raise RemediationTraceError("priced debt provenance reference is invalid")
    try:
        path = (_REPOSITORY_ROOT / path_text).resolve(strict=True)
        path.relative_to(_REPOSITORY_ROOT)
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RemediationTraceError(
            "priced debt provenance path does not resolve in the repository") from exc
    explicit_anchor = re.compile(
        rf"<(?:a|span)\s+(?:id|name)=[\"']{re.escape(anchor)}[\"'][^>]*>",
        re.IGNORECASE,
    )
    if explicit_anchor.search(source) is None:
        raise RemediationTraceError(
            "priced debt provenance anchor does not resolve in the repository")
    return reference


def _priced_cost(value: object, label: str) -> dict:
    """Validate one comparable, explicit debt repayment estimate."""
    if not isinstance(value, Mapping):
        raise RemediationTraceError(f"priced debt {label} cost is missing")
    fields = {"unit", *_DEBT_COST_COMPONENTS, "total", "basis"}
    if set(value) != fields:
        raise RemediationTraceError(f"priced debt {label} cost fields are invalid")
    components: dict[str, int] = {}
    for field in _DEBT_COST_COMPONENTS:
        amount = value.get(field)
        if (isinstance(amount, bool) or not isinstance(amount, int) or
                amount < 0 or amount > 1_000_000):
            raise RemediationTraceError(
                f"priced debt {label} {field} cost is invalid")
        components[field] = amount
    total = value.get("total")
    if (isinstance(total, bool) or not isinstance(total, int) or
            total <= 0 or total != sum(components.values())):
        raise RemediationTraceError(
            f"priced debt {label} total does not match its components")
    return {
        "unit": _debt_text(value.get("unit"), f"{label} cost unit", limit=64),
        **components,
        "total": total,
        "basis": _debt_text(value.get("basis"), f"{label} cost basis"),
    }


def priced_debt_record(*, debt_id: str, deferred_item: str, owner: str,
                       reentry_trigger: Mapping, follow_up: str,
                       now_cost: Mapping, later_cost: Mapping,
                       references: Sequence[str]) -> dict:
    """Mint one content-addressed debt record with comparable repayment cost.

    The estimate unit is deliberately caller-owned: relative work units,
    person-days, or another reviewed unit are valid, but the now/later records
    must use the same unit and itemized cost shape.
    """
    if not isinstance(debt_id, str) or not _DEBT_ID.fullmatch(debt_id):
        raise RemediationTraceError("priced debt id is invalid")
    item = _debt_text(deferred_item, "deferred item", limit=256)
    if not _IDENTIFIER.fullmatch(item):
        raise RemediationTraceError("priced debt deferred item id is invalid")
    owner_value = _debt_text(owner, "owner", limit=256)
    trigger = _reentry_trigger(reentry_trigger)
    follow_up_value = _debt_text(follow_up, "follow-up")
    if (not isinstance(references, Sequence) or
            isinstance(references, (str, bytes))):
        raise RemediationTraceError("priced debt references are invalid")
    reference_values = [_repository_reference(reference) for reference in references]
    if not reference_values or len(reference_values) != len(set(reference_values)):
        raise RemediationTraceError(
            "priced debt references must be non-empty and unique")
    current = _priced_cost(now_cost, "now")
    deferred = _priced_cost(later_cost, "later")
    if current["unit"] != deferred["unit"]:
        raise RemediationTraceError(
            "priced debt now and later estimates use different units")
    material = {
        "schema": PRICED_DEBT_SCHEMA,
        "debt_id": debt_id,
        "status": "open",
        "deferred_item": item,
        "owner": owner_value,
        "reentry_trigger": trigger,
        "follow_up": follow_up_value,
        "now_cost": current,
        "later_cost": deferred,
        "references": reference_values,
    }
    return {**material, "content_fingerprint": _digest(material)}


def _validate_priced_debt_record(value: object) -> dict:
    if not isinstance(value, Mapping):
        raise RemediationTraceError("priced debt record is missing")
    fields = {
        "schema", "debt_id", "status", "deferred_item", "owner",
        "reentry_trigger", "follow_up", "now_cost", "later_cost",
        "references", "content_fingerprint",
    }
    if set(value) != fields or value.get("schema") != PRICED_DEBT_SCHEMA or \
            value.get("status") != "open":
        raise RemediationTraceError("priced debt record fields are invalid")
    rebuilt = priced_debt_record(
        debt_id=str(value.get("debt_id") or ""),
        deferred_item=str(value.get("deferred_item") or ""),
        owner=str(value.get("owner") or ""),
        reentry_trigger=value.get("reentry_trigger") if isinstance(
            value.get("reentry_trigger"), Mapping) else {},
        follow_up=str(value.get("follow_up") or ""),
        now_cost=value.get("now_cost") if isinstance(
            value.get("now_cost"), Mapping) else {},
        later_cost=value.get("later_cost") if isinstance(
            value.get("later_cost"), Mapping) else {},
        references=value.get("references") if isinstance(
            value.get("references"), list) else (),
    )
    if dict(value) != rebuilt:
        raise RemediationTraceError("priced debt record was tampered")
    return rebuilt


def _parse_priced_debt_authority(source: str) -> list[dict]:
    """Parse the one hidden machine block and its visible Out-of-scope links."""
    if (source.count(_DEBT_AUTHORITY_START) != 1 or
            source.count(_DEBT_AUTHORITY_END) != 1):
        raise RemediationTraceError("priced debt specification authority is missing")
    start = source.index(_DEBT_AUTHORITY_START) + len(_DEBT_AUTHORITY_START)
    end = source.index(_DEBT_AUTHORITY_END, start)
    try:
        rows = json.loads(source[start:end].strip())
    except ValueError as exc:
        raise RemediationTraceError(
            "priced debt specification authority is invalid JSON") from exc
    if not isinstance(rows, list):
        raise RemediationTraceError("priced debt specification inventory is invalid")
    try:
        records = [priced_debt_record(**row) for row in rows
                   if isinstance(row, Mapping)]
    except TypeError as exc:
        raise RemediationTraceError(
            "priced debt specification record fields are invalid") from exc
    if len(records) != len(rows):
        raise RemediationTraceError("priced debt specification record is invalid")
    records.sort(key=lambda row: row["debt_id"])
    if tuple(row["debt_id"] for row in records) != _PRICED_DEBT_IDS or \
            len({row["deferred_item"] for row in records}) != len(records):
        raise RemediationTraceError(
            "priced debt specification omits or replays deferred work")
    try:
        out_of_scope = source.split("## Out of scope", 1)[1].split(
            "## Functional requirements", 1)[0]
    except IndexError as exc:
        raise RemediationTraceError(
            "priced debt Out-of-scope authority is missing") from exc
    for record in records:
        debt_anchor = f"debt-{record['debt_id'].lower()}"
        item_anchor = f"deferred-{record['deferred_item'].lower()}"
        if (f"[{record['debt_id']}](#{debt_anchor})" not in out_of_scope or
                f'<a id="{item_anchor}"></a>' not in out_of_scope or
                f"specs/spec.md#{item_anchor}" not in record["references"] or
                f"specs/spec.md#{debt_anchor}" not in record["references"]):
            raise RemediationTraceError(
                "every deferred item must link its priced debt in Out of scope")
    return records


def priced_debt_authority() -> dict:
    """Load priced debt only from the installed repository Product authority."""
    try:
        raw = _PRICED_DEBT_SPEC.read_bytes()
        source = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RemediationTraceError(
            "priced debt repository specification is unavailable") from exc
    return {
        "path": "specs/spec.md",
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "records": _parse_priced_debt_authority(source),
    }


def build_priced_debt_trace(*, records: Sequence[Mapping]) -> dict:
    """Join records against fixed repository Product authority, never a caller map."""
    authority = priced_debt_authority()
    if (not isinstance(records, Sequence) or isinstance(records, (str, bytes))):
        raise RemediationTraceError("priced debt records must be a sequence")
    validated = [_validate_priced_debt_record(value) for value in records]
    by_item = {record["deferred_item"]: record for record in validated}
    if len(by_item) != len(validated):
        raise RemediationTraceError("priced debt records replay a deferred item")
    expected = authority["records"]
    if set(by_item) != {row["deferred_item"] for row in expected}:
        raise RemediationTraceError(
            "every deferred item needs exactly one priced debt record")
    ordered = [by_item[row["deferred_item"]] for row in expected]
    for supplied, authoritative in zip(ordered, expected):
        if supplied != authoritative:
            raise RemediationTraceError(
                "priced debt record differs from repository Product authority")
    material = {
        "schema": PRICED_DEBT_TRACE_SCHEMA,
        "authority": {
            "path": authority["path"],
            "content_sha256": authority["content_sha256"],
        },
        "required_debt_ids": list(_PRICED_DEBT_IDS),
        "record_count": len(ordered),
        "records_fingerprint": _digest(ordered),
        "records": ordered,
    }
    return {**material, "trace_fingerprint": _digest(material)}


def verify_priced_debt_trace(trace: Mapping) -> dict:
    """Rebuild using current repository Product authority, never caller input."""
    if not isinstance(trace, Mapping) or set(trace) != {
            "schema", "authority", "required_debt_ids",
            "record_count", "records_fingerprint", "records",
            "trace_fingerprint"} or trace.get("schema") != PRICED_DEBT_TRACE_SCHEMA:
        raise RemediationTraceError("priced debt trace fields are invalid")
    records = trace.get("records")
    if not isinstance(records, list):
        raise RemediationTraceError("priced debt trace records are missing")
    rebuilt = build_priced_debt_trace(records=records)
    if dict(trace) != rebuilt:
        raise RemediationTraceError("priced debt trace was tampered or replayed")
    return rebuilt


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _trusted_git_path() -> Path:
    """Resolve Git without consulting caller-controlled PATH or aliases."""
    candidates = ([Path(os.environ.get("SystemRoot", r"C:\\Windows")) /
                   "System32" / "git.exe"] if os.name == "nt" else [
                       Path("/usr/bin/git"), Path("/bin/git")])
    for candidate in candidates:
        try:
            metadata = candidate.lstat()
            resolved = candidate.resolve(strict=True)
            resolved_metadata = resolved.stat()
        except OSError:
            continue
        if (stat.S_ISREG(resolved_metadata.st_mode) and
                os.access(resolved, os.X_OK) and
                not stat.S_ISLNK(metadata.st_mode) and
                not (resolved_metadata.st_mode &
                     (stat.S_IWGRP | stat.S_IWOTH)) and
                (os.name == "nt" or resolved_metadata.st_uid == 0)):
            return resolved
    raise RemediationTraceError(
        "a root-owned, non-writable trusted Git executable is unavailable")


def _git_identity() -> dict:
    path = _trusted_git_path()
    metadata = path.stat()
    material = {
        "schema": GIT_EVIDENCE_SCHEMA,
        "executable_path": str(path),
        "executable_sha256": _file_sha256(path),
        "executable_size": metadata.st_size,
        "executable_mode": stat.S_IMODE(metadata.st_mode),
    }
    return {**material, "identity_fingerprint": _digest(material)}


def _git_environment(git_path: Path) -> dict[str, str]:
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


def _workspace_path(workspace: str) -> Path:
    path = Path(workspace).resolve(strict=True)
    if not path.is_dir():
        raise RemediationTraceError("remediation workspace is invalid")
    return path


def _git(workspace: str, *args: str, binary: bool = False):
    root = _workspace_path(workspace)
    evidence = _git_identity()
    executable = Path(evidence["executable_path"])
    command = [
        str(executable),
        "-c", f"core.worktree={root}",
        "-c", "core.fsmonitor=false",
        "-c", f"core.attributesFile={os.devnull}",
        "-c", f"core.excludesFile={os.devnull}",
        "-c", "diff.external=",
        *args,
    ]
    result = subprocess.run(
        command, cwd=str(root), env=_git_environment(executable),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
        **({} if binary else {
            "text": True, "encoding": "utf-8", "errors": "replace",
        }))
    if result.returncode != 0:
        raise RemediationTraceError(
            "remediation trace could not resolve exact Git evidence")
    return result.stdout


def _exact_head(workspace: str) -> str:
    root = _workspace_path(workspace)
    top = str(_git(str(root), "rev-parse", "--show-toplevel")).strip()
    if Path(top).resolve() != root:
        raise RemediationTraceError("Git evidence belongs to another checkout")
    head = str(_git(str(root), "rev-parse", "HEAD")).strip()
    if not _SHA.fullmatch(head):
        raise RemediationTraceError("candidate SHA is invalid")
    return head


def _clean_candidate(workspace: str, candidate_sha: str) -> None:
    if not _SHA.fullmatch(str(candidate_sha)) or \
            candidate_sha != _exact_head(workspace):
        raise RemediationTraceError(
            "all remediation evidence must bind the exact current HEAD")
    dirty = str(_git(
        workspace, "status", "--porcelain=v1", "--untracked-files=all"
    )).strip()
    if dirty:
        raise RemediationTraceError(
            "exact-candidate remediation evidence requires a clean tree")


def _blob(workspace: str, candidate_sha: str, path: str) -> bytes:
    raw = _git(workspace, "show", f"{candidate_sha}:{path}", binary=True)
    if not isinstance(raw, bytes):  # pragma: no cover - defensive typing
        raise RemediationTraceError("exact-candidate blob is unavailable")
    return raw


def _finding_map(workspace: str, candidate_sha: str) -> dict[str, dict]:
    try:
        contract = json.loads(_blob(
            workspace, candidate_sha, "design/contract.json").decode("utf-8"))
    except (UnicodeDecodeError, ValueError, KeyError) as exc:
        raise RemediationTraceError(
            "Design finding map is invalid at candidate SHA") from exc
    rows = contract.get("finding_map") if isinstance(contract, Mapping) else None
    if not isinstance(rows, list):
        raise RemediationTraceError("Design finding map is unavailable")
    mapped: dict[str, dict] = {}
    for row in rows:
        finding_id = row.get("id") if isinstance(row, Mapping) else None
        if not isinstance(finding_id, str) or finding_id in mapped:
            raise RemediationTraceError("Design finding ids are invalid")
        mapped[finding_id] = dict(row)
    if not set(H1_FINDING_IDS).issubset(mapped):
        raise RemediationTraceError("Design finding map omits an H1 finding")
    return mapped


def _path_from_source(value: object) -> str:
    source = str(value or "")
    path = source.rsplit(":", 1)[0]
    if not path or path.startswith("/") or ".." in Path(path).parts:
        raise RemediationTraceError("finding source path is invalid")
    return path


def _selector_path(value: object) -> str:
    selector = str(value or "")
    path, separator, test_name = selector.partition("::")
    if (separator != "::" or not path or not test_name or
            path.startswith("/") or ".." in Path(path).parts):
        raise RemediationTraceError("finding selector is invalid")
    return path


def agent_identity(*, role: str, agent_id: str, task_name: str,
                   task_id: str, session_id: str) -> dict:
    """Create the closed identity object consumed by remediation producers."""
    if role not in {"build", "evaluate"}:
        raise RemediationTraceError("remediation identity role is invalid")
    values = (agent_id, task_name, task_id, session_id)
    if any(not isinstance(value, str) or not _IDENTIFIER.fullmatch(value)
           for value in values):
        raise RemediationTraceError("remediation identity fields are invalid")
    material = {
        "schema": IDENTITY_SCHEMA,
        "role": role,
        "agent_id": agent_id,
        "task_name": task_name,
        "task_id": task_id,
        "session_id": session_id,
    }
    return {**material, "identity_fingerprint": _digest(material)}


def _validate_identity(value: object, *, role: str, task_id: str) -> dict:
    if not isinstance(value, Mapping):
        raise RemediationTraceError("remediation identity is missing")
    expected_fields = {
        "schema", "role", "agent_id", "task_name", "task_id",
        "session_id", "identity_fingerprint",
    }
    if set(value) != expected_fields:
        raise RemediationTraceError("remediation identity fields are invalid")
    expected = agent_identity(
        role=str(value.get("role") or ""),
        agent_id=str(value.get("agent_id") or ""),
        task_name=str(value.get("task_name") or ""),
        task_id=str(value.get("task_id") or ""),
        session_id=str(value.get("session_id") or ""),
    )
    if (dict(value) != expected or value.get("schema") != IDENTITY_SCHEMA or
            value.get("role") != role or value.get("task_id") != task_id):
        raise RemediationTraceError("remediation identity binding is invalid")
    return expected


def _production_boundary(workspace: str, candidate_sha: str,
                         finding: Mapping) -> dict:
    source_path = _path_from_source(finding.get("source"))
    selector = str(finding.get("evidence") or "")
    selector_path = _selector_path(selector)
    source_bytes = _blob(workspace, candidate_sha, source_path)
    selector_bytes = _blob(workspace, candidate_sha, selector_path)
    tree = str(_git(workspace, "rev-parse", f"{candidate_sha}^{{tree}}")).strip()
    if not _SHA.fullmatch(tree):
        raise RemediationTraceError("candidate tree identity is invalid")
    return {
        "contracts": list(finding.get("boundaries") or []),
        "source": str(finding.get("source") or ""),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "selector": selector,
        "selector_sha256": hashlib.sha256(selector_bytes).hexdigest(),
        "candidate_tree": tree,
    }


def _receipt(material: Mapping, fingerprint_field: str) -> dict:
    return {**material, fingerprint_field: _digest(material)}


def _write_receipt(directory: str | Path, receipt: Mapping,
                   *, kind: str, fingerprint_field: str) -> str:
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise RemediationTraceError("remediation receipt directory is invalid")
    fingerprint = str(receipt.get(fingerprint_field) or "")
    if not _DIGEST.fullmatch(fingerprint):
        raise RemediationTraceError("remediation receipt fingerprint is invalid")
    path = root / f"{kind}-{fingerprint}.json"
    payload = _canonical_bytes(dict(receipt)) + b"\n"
    if path.exists():
        try:
            if path.is_symlink() or path.read_bytes() != payload:
                raise RemediationTraceError(
                    "remediation receipt collision was refused")
        except OSError as exc:
            raise RemediationTraceError(
                "remediation receipt collision could not be checked") from exc
        return str(path)
    temporary = root / f".{path.name}.{os.getpid()}.tmp"
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise RemediationTraceError(
            "remediation receipt could not be persisted") from exc
    return str(path)


def _read_receipt(path: str | Path, *, kind: str,
                  fingerprint_field: str) -> dict:
    receipt_path = Path(path)
    try:
        metadata = receipt_path.lstat()
        if (stat.S_ISLNK(metadata.st_mode) or
                not stat.S_ISREG(metadata.st_mode) or
                metadata.st_size > _MAX_RECEIPT_BYTES):
            raise RemediationTraceError("remediation receipt path is invalid")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(receipt_path, flags)
        with os.fdopen(descriptor, "rb") as source:
            raw = source.read(_MAX_RECEIPT_BYTES + 1)
    except OSError as exc:
        raise RemediationTraceError("remediation receipt is unavailable") from exc
    if len(raw) > _MAX_RECEIPT_BYTES:
        raise RemediationTraceError("remediation receipt is oversized")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RemediationTraceError("remediation receipt JSON is invalid") from exc
    if (not isinstance(value, dict) or
            raw != _canonical_bytes(value) + b"\n"):
        raise RemediationTraceError("remediation receipt encoding is invalid")
    fingerprint = str(value.get(fingerprint_field) or "")
    if (not _DIGEST.fullmatch(fingerprint) or
            receipt_path.name != f"{kind}-{fingerprint}.json"):
        raise RemediationTraceError(
            "remediation receipt is not content-addressed")
    return value


def _validate_git_evidence(value: object) -> dict:
    expected = _git_identity()
    if not isinstance(value, Mapping) or dict(value) != expected:
        raise RemediationTraceError("trusted Git executable identity changed")
    return expected


def produce_build_receipt(workspace: str, receipt_directory: str | Path, *,
                          candidate_sha: str, finding_id: str,
                          producer_identity: Mapping) -> str:
    """Persist one exact-candidate Build receipt for a contract-owned finding."""
    workspace = str(_workspace_path(workspace))
    _clean_candidate(workspace, candidate_sha)
    finding = _finding_map(workspace, candidate_sha).get(finding_id)
    if finding is None or finding_id not in H1_FINDING_IDS:
        raise RemediationTraceError("finding is outside the H1 closure set")
    task_id = str(finding.get("task") or "")
    identity = _validate_identity(
        producer_identity, role="build", task_id=task_id)
    material = {
        "schema": BUILD_RECEIPT_SCHEMA,
        "phase": "build",
        "outcome": "candidate-produced",
        "candidate_sha": candidate_sha,
        "finding_id": finding_id,
        "task_id": task_id,
        "producer_identity": identity,
        "git_evidence": _git_identity(),
        "production_boundary": _production_boundary(
            workspace, candidate_sha, finding),
    }
    receipt = _receipt(material, "receipt_fingerprint")
    return _write_receipt(
        receipt_directory, receipt, kind="build",
        fingerprint_field="receipt_fingerprint")


def _validate_build_receipt(workspace: str, value: object, *,
                            candidate_sha: str) -> dict:
    if not isinstance(value, Mapping):
        raise RemediationTraceError("Build receipt is missing")
    expected_fields = {
        "schema", "phase", "outcome", "candidate_sha", "finding_id",
        "task_id", "producer_identity", "git_evidence",
        "production_boundary", "receipt_fingerprint",
    }
    if set(value) != expected_fields:
        raise RemediationTraceError("Build receipt fields are invalid")
    finding_id = str(value.get("finding_id") or "")
    finding = _finding_map(workspace, candidate_sha).get(finding_id)
    if finding is None or finding_id not in H1_FINDING_IDS:
        raise RemediationTraceError("Build receipt finding is invalid")
    task_id = str(finding.get("task") or "")
    identity = _validate_identity(
        value.get("producer_identity"), role="build", task_id=task_id)
    git_evidence = _validate_git_evidence(value.get("git_evidence"))
    material = {
        "schema": BUILD_RECEIPT_SCHEMA,
        "phase": "build",
        "outcome": "candidate-produced",
        "candidate_sha": candidate_sha,
        "finding_id": finding_id,
        "task_id": task_id,
        "producer_identity": identity,
        "git_evidence": git_evidence,
        "production_boundary": _production_boundary(
            workspace, candidate_sha, finding),
    }
    expected = _receipt(material, "receipt_fingerprint")
    if dict(value) != expected:
        raise RemediationTraceError(
            "Build receipt differs from exact repository evidence")
    return expected


def _selector_environment() -> dict[str, str]:
    python = Path(sys.executable).resolve()
    path_parts = [str(python.parent)]
    if os.name != "nt":
        path_parts.extend(["/usr/bin", "/bin"])
    return {
        "PATH": os.pathsep.join(dict.fromkeys(path_parts)),
        "HOME": os.devnull,
        "USERPROFILE": os.devnull,
        "XDG_CONFIG_HOME": os.devnull,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "LC_ALL": "C",
        "LANG": "C",
    }


def _execute_selector(workspace: str, selector: str) -> dict:
    python = Path(sys.executable).resolve(strict=True)
    argv = [
        str(python), "-P", "-m", "pytest", "-q", "-p",
        "no:cacheprovider", selector,
    ]
    result = subprocess.run(
        argv, cwd=str(_workspace_path(workspace)),
        env=_selector_environment(), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    output = result.stdout or b""
    if result.returncode != 0:
        raise RemediationTraceError(
            f"selector execution failed with exit code {result.returncode}")
    runner = {
        "executable_path": str(python),
        "executable_sha256": _file_sha256(python),
    }
    return {
        "schema": SELECTOR_EXECUTION_SCHEMA,
        "argv": argv,
        "exit_code": result.returncode,
        "outcome": "passed",
        "output_bytes": len(output),
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "runner": runner,
    }


def produce_evaluate_receipt(workspace: str, receipt_directory: str | Path,
                             *, build_receipt_path: str | Path,
                             evaluator_identity: Mapping) -> str:
    """Run the contract selector and persist its independent Evaluate receipt."""
    workspace = str(_workspace_path(workspace))
    supplied_build = _read_receipt(
        build_receipt_path, kind="build",
        fingerprint_field="receipt_fingerprint")
    candidate_sha = str(supplied_build.get("candidate_sha") or "")
    _clean_candidate(workspace, candidate_sha)
    build_receipt = _validate_build_receipt(
        workspace, supplied_build, candidate_sha=candidate_sha)
    task_id = str(build_receipt["task_id"])
    identity = _validate_identity(
        evaluator_identity, role="evaluate", task_id=task_id)
    producer = build_receipt["producer_identity"]
    if (identity["identity_fingerprint"] ==
            producer["identity_fingerprint"] or
            identity["agent_id"] == producer["agent_id"] or
            identity["task_name"] == producer["task_name"]):
        raise RemediationTraceError(
            "Build and Evaluate producer identities are not independent")
    selector = str(build_receipt["production_boundary"]["selector"])
    execution = _execute_selector(workspace, selector)
    _clean_candidate(workspace, candidate_sha)
    material = {
        "schema": EVALUATE_RECEIPT_SCHEMA,
        "phase": "evaluate",
        "outcome": "passed",
        "candidate_sha": candidate_sha,
        "finding_id": build_receipt["finding_id"],
        "task_id": task_id,
        "build_receipt": build_receipt,
        "build_receipt_fingerprint": build_receipt["receipt_fingerprint"],
        "evaluator_identity": identity,
        "selector_execution": execution,
    }
    receipt = _receipt(material, "receipt_fingerprint")
    return _write_receipt(
        receipt_directory, receipt, kind="evaluate",
        fingerprint_field="receipt_fingerprint")


def _validate_selector_execution(value: object, *, selector: str) -> dict:
    if not isinstance(value, Mapping):
        raise RemediationTraceError("selector execution receipt is missing")
    fields = {
        "schema", "argv", "exit_code", "outcome", "output_bytes",
        "output_sha256", "runner",
    }
    if set(value) != fields:
        raise RemediationTraceError("selector execution fields are invalid")
    python = Path(sys.executable).resolve(strict=True)
    runner = value.get("runner")
    expected_runner = {
        "executable_path": str(python),
        "executable_sha256": _file_sha256(python),
    }
    expected_argv = [
        str(python), "-P", "-m", "pytest", "-q", "-p",
        "no:cacheprovider", selector,
    ]
    if (value.get("schema") != SELECTOR_EXECUTION_SCHEMA or
            value.get("argv") != expected_argv or
            value.get("exit_code") != 0 or value.get("outcome") != "passed" or
            not isinstance(value.get("output_bytes"), int) or
            value.get("output_bytes") < 0 or
            not _DIGEST.fullmatch(str(value.get("output_sha256") or "")) or
            runner != expected_runner):
        raise RemediationTraceError("selector execution result is invalid")
    return dict(value)


def _validate_evaluate_receipt(workspace: str, value: object, *,
                               candidate_sha: str) -> dict:
    if not isinstance(value, Mapping):
        raise RemediationTraceError("Evaluate receipt is missing")
    fields = {
        "schema", "phase", "outcome", "candidate_sha", "finding_id",
        "task_id", "build_receipt", "build_receipt_fingerprint",
        "evaluator_identity", "selector_execution", "receipt_fingerprint",
    }
    if set(value) != fields:
        raise RemediationTraceError("Evaluate receipt fields are invalid")
    build_receipt = _validate_build_receipt(
        workspace, value.get("build_receipt"), candidate_sha=candidate_sha)
    if value.get("build_receipt_fingerprint") != \
            build_receipt["receipt_fingerprint"]:
        raise RemediationTraceError("Evaluate receipt replays another Build")
    task_id = str(build_receipt["task_id"])
    identity = _validate_identity(
        value.get("evaluator_identity"), role="evaluate", task_id=task_id)
    producer = build_receipt["producer_identity"]
    if (identity["identity_fingerprint"] ==
            producer["identity_fingerprint"] or
            identity["agent_id"] == producer["agent_id"] or
            identity["task_name"] == producer["task_name"]):
        raise RemediationTraceError(
            "Build and Evaluate producer identities are not independent")
    execution = _validate_selector_execution(
        value.get("selector_execution"),
        selector=str(build_receipt["production_boundary"]["selector"]))
    material = {
        "schema": EVALUATE_RECEIPT_SCHEMA,
        "phase": "evaluate",
        "outcome": "passed",
        "candidate_sha": candidate_sha,
        "finding_id": build_receipt["finding_id"],
        "task_id": task_id,
        "build_receipt": build_receipt,
        "build_receipt_fingerprint": build_receipt["receipt_fingerprint"],
        "evaluator_identity": identity,
        "selector_execution": execution,
    }
    expected = _receipt(material, "receipt_fingerprint")
    if dict(value) != expected:
        raise RemediationTraceError(
            "Evaluate receipt differs from exact repository evidence")
    return expected


def _result_from_receipt(receipt: Mapping) -> dict:
    build = receipt["build_receipt"]
    producer = build["producer_identity"]
    evaluator = receipt["evaluator_identity"]
    material = {
        "schema": H1_RESULT_SCHEMA,
        "finding_id": receipt["finding_id"],
        "severity": "high",
        "task_id": receipt["task_id"],
        "candidate_sha": receipt["candidate_sha"],
        "outcome": "closed",
        "selector": build["production_boundary"]["selector"],
        "selector_execution": receipt["selector_execution"],
        "producer_identity": producer,
        "evaluator_identity": evaluator,
        "independent": True,
        "build_receipt_fingerprint": build["receipt_fingerprint"],
        "evaluate_receipt_fingerprint": receipt["receipt_fingerprint"],
        "production_boundary": build["production_boundary"],
    }
    return {**material, "content_fingerprint": _digest(material)}


def build_h1_trace(workspace: str, *, candidate_sha: str,
                   evaluate_receipt_paths: Sequence[str | Path]) -> dict:
    """Join exactly one consumed, independently green receipt per H1 id."""
    workspace = str(_workspace_path(workspace))
    _clean_candidate(workspace, candidate_sha)
    if (not isinstance(evaluate_receipt_paths, Sequence) or
            isinstance(evaluate_receipt_paths, (str, bytes))):
        raise RemediationTraceError("H1 Evaluate receipts must be a sequence")
    receipts = []
    paths = []
    for path in evaluate_receipt_paths:
        if not isinstance(path, (str, Path)):
            raise RemediationTraceError("H1 Evaluate receipt path is invalid")
        supplied = _read_receipt(
            path, kind="evaluate", fingerprint_field="receipt_fingerprint")
        receipts.append(_validate_evaluate_receipt(
            workspace, supplied, candidate_sha=candidate_sha))
        paths.append(str(Path(path).resolve()))
    ids = [receipt["finding_id"] for receipt in receipts]
    fingerprints = [receipt["receipt_fingerprint"] for receipt in receipts]
    if (len(receipts) != len(H1_FINDING_IDS) or
            len(ids) != len(set(ids)) or
            set(ids) != set(H1_FINDING_IDS) or
            len(fingerprints) != len(set(fingerprints)) or
            len(paths) != len(set(paths))):
        raise RemediationTraceError(
            "H1 trace requires one unique receipt for every H1 finding")
    ordered_receipts = [
        next(row for row in receipts if row["finding_id"] == finding_id)
        for finding_id in H1_FINDING_IDS
    ]
    results = [_result_from_receipt(receipt)
               for receipt in ordered_receipts]
    material = {
        "schema": H1_TRACE_SCHEMA,
        "candidate_sha": candidate_sha,
        "git_evidence": _git_identity(),
        "required_finding_ids": list(H1_FINDING_IDS),
        "receipt_count": len(ordered_receipts),
        "receipts_fingerprint": _digest(ordered_receipts),
        "evaluate_receipts": ordered_receipts,
        "result_count": len(results),
        "results_fingerprint": _digest(results),
        "results": results,
    }
    return {**material, "trace_fingerprint": _digest(material)}


def verify_h1_trace(workspace: str, trace: Mapping) -> dict:
    """Re-derive a trace from embedded receipts and reject forged material."""
    if not isinstance(trace, Mapping) or trace.get("schema") != H1_TRACE_SCHEMA:
        raise RemediationTraceError("H1 trace schema is invalid")
    candidate_sha = str(trace.get("candidate_sha") or "")
    workspace = str(_workspace_path(workspace))
    _clean_candidate(workspace, candidate_sha)
    supplied_receipts = trace.get("evaluate_receipts")
    if not isinstance(supplied_receipts, list):
        raise RemediationTraceError("H1 trace receipts are missing")
    receipts = [_validate_evaluate_receipt(
        workspace, value, candidate_sha=candidate_sha)
        for value in supplied_receipts]
    ids = [receipt["finding_id"] for receipt in receipts]
    fingerprints = [receipt["receipt_fingerprint"] for receipt in receipts]
    if (len(receipts) != len(H1_FINDING_IDS) or ids != list(H1_FINDING_IDS) or
            len(fingerprints) != len(set(fingerprints))):
        raise RemediationTraceError(
            "H1 trace receipt inventory is incomplete or replayed")
    results = [_result_from_receipt(receipt) for receipt in receipts]
    material = {
        "schema": H1_TRACE_SCHEMA,
        "candidate_sha": candidate_sha,
        "git_evidence": _validate_git_evidence(trace.get("git_evidence")),
        "required_finding_ids": list(H1_FINDING_IDS),
        "receipt_count": len(receipts),
        "receipts_fingerprint": _digest(receipts),
        "evaluate_receipts": receipts,
        "result_count": len(results),
        "results_fingerprint": _digest(results),
        "results": results,
    }
    rebuilt = {**material, "trace_fingerprint": _digest(material)}
    if dict(trace) != rebuilt:
        raise RemediationTraceError("H1 trace fingerprint or inventory is mixed")
    return rebuilt


# R-0002 final-integration authority is deliberately repository-contained.
# The review source itself was ignored audit output, so FINAL-I retains its
# exact bytes as an ID-joined snapshot and pins that snapshot here.  A caller
# cannot replace findings, commits, exception records, or outcome words.
FINAL_INVENTORY_SCHEMA = "taskplane.r0002-remediation-inventory/v1"
FINAL_INTEGRATION_SCHEMA = "taskplane.r0002-final-integration-evidence/v1"
FINAL_AUTHORITY_SCHEMA = "taskplane.r0002-final-integration-authority/v1"
FINAL_FINDINGS_SNAPSHOT_SCHEMA = "taskplane.r0002-findings-snapshot/v1"
_FINAL_AUTHORITY_PATH = \
    ".em-review/remediation/final-integration/authority.json"
_FINAL_AUTHORITY_SHA256 = \
    "1b6ee6ab37ad7553d2d5ab1b5830a4e982978aafc69c3f13dd6f3ec163d5b7f4"
_FINAL_FINDINGS_SNAPSHOT_PATH = \
    ".em-review/remediation/final-integration/findings-snapshot.json"
_FINAL_FINDINGS_SNAPSHOT_SHA256 = \
    "7f68603d889fc932a7f022c4df4b53e48317ce71fbc3608f4d27704d5a2f30ab"
_FINAL_CANONICAL_FINDINGS_SHA256 = \
    "74745ab55c2d0313c9c4271697f2ee024a3e3966ea46f4323a18c9b26f5f6041"
_FINAL_HIGH_RESULTS_PATH = ".em-review/remediation/high-gate/results.json"
_FINAL_HIGH_RESULTS_SHA256 = \
    "a00787374c9c543e7951c5b64e28f414f3c4ed1913a6adfea4eb941c65142628"
_FINAL_COUNTS = {"total": 72, "high": 34, "medium": 28, "low": 10}
_FINAL_EXCEPTION_FINDINGS = {
    "H1-I-selector-receipt-authority": (
        "H-03", "H-04", "H-05", "H-06", "H-07", "H-08", "H-14",
        "H-15", "H-19", "H-22", "H-26", "H-30", "H-34",
    ),
    "H3-C-retention-gaps": ("H-23", "H-25"),
}
_FINAL_JOIN_SELECTORS = {
    "M1-I": (
        "be3425eaaee27279febf8937d05dbefc686fea34",
        "taskplane/tests/test_em_m1_integration.py::"
        "test_ac6_engineering_foundations_close",
    ),
    "M2-I": (
        "7b3473789caf01d3115301c2308f25c460741fb5",
        "taskplane/tests/test_em_m2_integration.py::"
        "test_ac7_user_facing_truth_closes",
    ),
}


def _json_blob(workspace: str, candidate_sha: str, path: str,
               label: str) -> dict:
    try:
        value = json.loads(_blob(workspace, candidate_sha, path).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, KeyError) as exc:
        raise RemediationTraceError(f"{label} JSON is invalid") from exc
    if not isinstance(value, dict):
        raise RemediationTraceError(f"{label} must be a JSON object")
    return value


def _pinned_blob(workspace: str, candidate_sha: str, path: str,
                 expected_sha256: str, label: str) -> bytes:
    value = _blob(workspace, candidate_sha, path)
    if hashlib.sha256(value).hexdigest() != expected_sha256:
        raise RemediationTraceError(f"{label} differs from retained authority")
    return value


def _expected_finding_ids() -> tuple[str, ...]:
    return tuple(
        [f"H-{number:02d}" for number in range(1, 35)] +
        [f"M-{number:02d}" for number in range(1, 29)] +
        [f"L-{number:02d}" for number in range(1, 11)]
    )


def _final_authority(workspace: str, candidate_sha: str) -> dict:
    raw = _pinned_blob(
        workspace, candidate_sha, _FINAL_AUTHORITY_PATH,
        _FINAL_AUTHORITY_SHA256, "final integration authority")
    try:
        authority = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RemediationTraceError("final integration authority is invalid") from exc
    if (not isinstance(authority, dict) or
            authority.get("schema") != FINAL_AUTHORITY_SCHEMA or
            authority.get("requirement_id") != "R-0002" or
            authority.get("inventory") != _FINAL_COUNTS):
        raise RemediationTraceError("final integration authority is incompatible")
    status = authority.get("status_contract") or {}
    if status != {
        "integration_disposition": "ready-for-independent-final-evaluation",
        "strict_ac5_status": "not-satisfied",
        "strict_ac8_status": "pending-independent-final-evaluation",
        "independently_green_high": 19,
        "attributed_non_independent_exceptions": 15,
        "integrated_medium_low_pending_final_evaluation": 38,
    }:
        raise RemediationTraceError("final integration status was relabelled")
    return authority


def _final_sources(workspace: str, candidate_sha: str) -> tuple[dict, dict, dict]:
    authority = _final_authority(workspace, candidate_sha)
    snapshot_raw = _pinned_blob(
        workspace, candidate_sha, _FINAL_FINDINGS_SNAPSHOT_PATH,
        _FINAL_FINDINGS_SNAPSHOT_SHA256, "retained findings snapshot")
    try:
        snapshot = json.loads(snapshot_raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RemediationTraceError("retained findings snapshot is invalid") from exc
    design = _json_blob(
        workspace, candidate_sha, "design/contract.json", "Design contract")
    plan = _json_blob(workspace, candidate_sha, "plan/tasks.json", "Plan")
    if (not isinstance(snapshot, dict) or
            snapshot.get("schema") != FINAL_FINDINGS_SNAPSHOT_SCHEMA or
            snapshot.get("canonical_source") != ".em-review/findings.json" or
            snapshot.get("canonical_source_sha256") !=
            _FINAL_CANONICAL_FINDINGS_SHA256 or
            snapshot.get("counts") != _FINAL_COUNTS):
        raise RemediationTraceError("retained findings source identity is invalid")
    source = authority.get("source_review") or {}
    if (source.get("canonical_sha256") != _FINAL_CANONICAL_FINDINGS_SHA256 or
            source.get("retained_snapshot_path") !=
            _FINAL_FINDINGS_SNAPSHOT_PATH or
            source.get("retained_snapshot_sha256") !=
            _FINAL_FINDINGS_SNAPSHOT_SHA256):
        raise RemediationTraceError("review source authority is inconsistent")
    return snapshot, design, plan


def _scope_contains(scope: object, selector_path: str) -> bool:
    if not isinstance(scope, list):
        return False
    for entry in scope:
        if entry == selector_path:
            return True
        if (isinstance(entry, str) and entry.endswith("/**") and
                selector_path.startswith(entry[:-3])):
            return True
    return False


def build_final_inventory(workspace: str, *, candidate_sha: str) -> dict:
    """Build the exact 72-row Design/Plan/review inventory at clean HEAD."""
    workspace = str(_workspace_path(workspace))
    _clean_candidate(workspace, candidate_sha)
    snapshot, design, plan = _final_sources(workspace, candidate_sha)
    design_rows = design.get("finding_map")
    plan_rows = plan.get("tasks")
    review_rows = snapshot.get("rows")
    if not all(isinstance(value, list)
               for value in (design_rows, plan_rows, review_rows)):
        raise RemediationTraceError("final finding inventories are unavailable")
    expected_ids = _expected_finding_ids()
    if len(design_rows) != 72 or len(review_rows) != 72:
        raise RemediationTraceError("final finding inventory is not exactly 72 rows")
    tasks: dict[str, dict] = {}
    owned: dict[str, str] = {}
    for task in plan_rows:
        if not isinstance(task, Mapping) or not isinstance(task.get("id"), str):
            raise RemediationTraceError("Plan task inventory is invalid")
        task_id = str(task["id"])
        if task_id in tasks:
            raise RemediationTraceError("Plan task ids are duplicated")
        tasks[task_id] = dict(task)
        findings = task.get("findings")
        if not isinstance(findings, list):
            raise RemediationTraceError("Plan task findings are invalid")
        for finding_id in findings:
            if not isinstance(finding_id, str) or finding_id in owned:
                raise RemediationTraceError("Plan finding ownership is duplicated")
            owned[finding_id] = task_id
    design_map: dict[str, dict] = {}
    for row in design_rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
            raise RemediationTraceError("Design finding row is invalid")
        finding_id = str(row["id"])
        if finding_id in design_map:
            raise RemediationTraceError("Design finding ids are duplicated")
        design_map[finding_id] = dict(row)
    review_map: dict[str, dict] = {}
    review_fields = {
        "id", "severity", "review_severity", "class", "source", "title",
        "scenario", "fix", "lens", "domain", "status",
    }
    for row in review_rows:
        if (not isinstance(row, Mapping) or set(row) != review_fields or
                not isinstance(row.get("id"), str)):
            raise RemediationTraceError("retained review row is invalid")
        finding_id = str(row["id"])
        if finding_id in review_map:
            raise RemediationTraceError("retained review ids are duplicated")
        review_map[finding_id] = dict(row)
    if (tuple(design_map) != expected_ids or tuple(review_map) != expected_ids or
            set(owned) != set(expected_ids)):
        raise RemediationTraceError(
            "Design, Plan, and review finding ids do not match exactly")
    severity_counts = {"high": 0, "medium": 0, "low": 0}
    rows: list[dict] = []
    for finding_id in expected_ids:
        design_row = design_map[finding_id]
        review_row = review_map[finding_id]
        task_id = owned[finding_id]
        task = tasks.get(task_id)
        if task is None or design_row.get("task") != task_id:
            raise RemediationTraceError(f"{finding_id} task ownership is inconsistent")
        severity = design_row.get("severity")
        review_severity = review_row.get("review_severity")
        if (severity not in severity_counts or
                review_severity != ("med" if severity == "medium" else severity)):
            raise RemediationTraceError(f"{finding_id} severity is inconsistent")
        severity_counts[str(severity)] += 1
        if (review_row.get("severity") != severity or
                review_row.get("source") != design_row.get("source") or
                review_row.get("title") != design_row.get("title") or
                review_row.get("lens") != design_row.get("lens") or
                review_row.get("status") != "open"):
            raise RemediationTraceError(f"{finding_id} review mapping is inconsistent")
        required_text = ("owner", "dependency_class", "evidence")
        if any(not isinstance(design_row.get(field), str) or
               not design_row.get(field) for field in required_text):
            raise RemediationTraceError(f"{finding_id} trace metadata is incomplete")
        boundaries = design_row.get("boundaries")
        depends_on = design_row.get("depends_on")
        if (not isinstance(boundaries, list) or not boundaries or
                not all(isinstance(value, str) and value for value in boundaries) or
                not isinstance(depends_on, list) or
                not all(isinstance(value, str) and value for value in depends_on)):
            raise RemediationTraceError(f"{finding_id} dependency trace is invalid")
        task_waves = str(task.get("wave") or "").split("+")
        if (design_row.get("owner") != task.get("owner") or
                design_row.get("wave") not in task_waves or
                depends_on != task.get("deps") or
                not set(boundaries).issubset(set(task.get("contracts") or []))):
            raise RemediationTraceError(f"{finding_id} Design/Plan boundary differs")
        selector = str(design_row["evidence"])
        selector_path, separator, selector_name = selector.partition("::")
        if (separator != "::" or not selector_path or not selector_name or
                not _scope_contains(task.get("scope"), selector_path)):
            raise RemediationTraceError(f"{finding_id} evidence is outside task scope")
        low_companion = design_row.get("low_companion")
        if severity == "low":
            if (not isinstance(low_companion, Mapping) or
                    low_companion.get("wave") != design_row.get("wave") or
                    low_companion.get("mode") not in {
                        "shared-owner", "pairwise-disjoint"} or
                    not isinstance(low_companion.get("with"), list) or
                    not low_companion.get("with")):
                raise RemediationTraceError(
                    f"{finding_id} low companion declaration is invalid")
            for companion_id in low_companion["with"]:
                companion = design_map.get(companion_id)
                if companion is None or companion.get("wave") != design_row.get("wave"):
                    raise RemediationTraceError(
                        f"{finding_id} low companion is outside its wave")
                if (low_companion["mode"] == "shared-owner" and
                        companion.get("owner") != design_row.get("owner")):
                    raise RemediationTraceError(
                        f"{finding_id} shared-owner companion differs")
        elif low_companion is not None:
            raise RemediationTraceError(
                f"{finding_id} non-low row declares a low companion")
        row = {
            "id": finding_id,
            "severity": severity,
            "source": design_row["source"],
            "title": design_row["title"],
            "owner": design_row["owner"],
            "boundaries": list(boundaries),
            "wave": design_row["wave"],
            "task": task_id,
            "dependency_class": design_row["dependency_class"],
            "depends_on": list(depends_on),
            "evidence": selector,
            "review_row_fingerprint": _digest(review_row),
        }
        if severity == "low":
            row["low_companion"] = dict(low_companion)
        rows.append(row)
    if {"total": len(rows), **severity_counts} != _FINAL_COUNTS:
        raise RemediationTraceError("final finding severity counts are inconsistent")
    material = {
        "schema": FINAL_INVENTORY_SCHEMA,
        "candidate_sha": candidate_sha,
        "git_evidence": _git_identity(),
        "canonical_review": {
            "path": ".em-review/findings.json",
            "sha256": _FINAL_CANONICAL_FINDINGS_SHA256,
            "retained_snapshot_path": _FINAL_FINDINGS_SNAPSHOT_PATH,
            "retained_snapshot_sha256": _FINAL_FINDINGS_SNAPSHOT_SHA256,
        },
        "design_sha256": hashlib.sha256(_blob(
            workspace, candidate_sha, "design/contract.json")).hexdigest(),
        "plan_sha256": hashlib.sha256(_blob(
            workspace, candidate_sha, "plan/tasks.json")).hexdigest(),
        "counts": dict(_FINAL_COUNTS),
        "rows": rows,
    }
    return {**material, "inventory_fingerprint": _digest(material)}


def verify_final_inventory(workspace: str, inventory: Mapping) -> dict:
    """Rebuild the inventory and reject a removed, relabelled, or mixed row."""
    if (not isinstance(inventory, Mapping) or
            inventory.get("schema") != FINAL_INVENTORY_SCHEMA):
        raise RemediationTraceError("final inventory schema is invalid")
    candidate_sha = str(inventory.get("candidate_sha") or "")
    expected = build_final_inventory(workspace, candidate_sha=candidate_sha)
    if dict(inventory) != expected:
        raise RemediationTraceError("final inventory differs from exact authority")
    return expected


def _require_ancestor(workspace: str, ancestor: str, candidate_sha: str,
                      label: str) -> None:
    if not _SHA.fullmatch(ancestor):
        raise RemediationTraceError(f"{label} commit identity is invalid")
    try:
        _git(workspace, "cat-file", "-e", f"{ancestor}^{{commit}}")
        _git(workspace, "merge-base", "--is-ancestor", ancestor, candidate_sha)
    except RemediationTraceError as exc:
        raise RemediationTraceError(
            f"{label} commit is not exact-candidate ancestry") from exc


def _validate_high_gate(workspace: str, candidate_sha: str,
                        authority: Mapping) -> dict:
    raw = _pinned_blob(
        workspace, candidate_sha, _FINAL_HIGH_RESULTS_PATH,
        _FINAL_HIGH_RESULTS_SHA256, "high-gate results")
    try:
        high = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RemediationTraceError("high-gate results are invalid") from exc
    counts = high.get("counts") if isinstance(high, Mapping) else None
    if (high.get("schema") != "taskplane.r0002-high-gate-disposition/v1" or
            high.get("strict_ac5_status") != "not-satisfied" or
            counts != {
                "required_high": 34, "listed_high": 34,
                "independently_green": 19, "accepted_exception": 15,
                "missing": 0, "suppressed": 0, "downgraded": 0,
                "self_attested_green": 0,
            }):
        raise RemediationTraceError("high-gate truth was weakened or relabelled")
    results = high.get("results")
    exceptions = high.get("exception_records")
    if not isinstance(results, list) or not isinstance(exceptions, list):
        raise RemediationTraceError("high-gate result inventory is missing")
    if [row.get("finding_id") for row in results] != [
            f"H-{number:02d}" for number in range(1, 35)]:
        raise RemediationTraceError("high-gate finding ids are not exact")
    expected_exception_ids = {
        finding_id for values in _FINAL_EXCEPTION_FINDINGS.values()
        for finding_id in values
    }
    actual_exception_ids = {
        row.get("finding_id") for row in results
        if row.get("status") == "accepted-exception" and
        row.get("independent") is False
    }
    independently_green = {
        row.get("finding_id") for row in results
        if row.get("status") == "independently-green" and
        row.get("independent") is True
    }
    if (actual_exception_ids != expected_exception_ids or
            len(independently_green) != 19 or
            actual_exception_ids & independently_green or
            len(actual_exception_ids | independently_green) != 34):
        raise RemediationTraceError("high-gate dispositions are inconsistent")
    authority_exceptions = authority.get("exceptions")
    if exceptions != authority_exceptions:
        raise RemediationTraceError("high-gate exception authority differs")
    for record in exceptions:
        affected = tuple(record.get("affected_findings") or [])
        if (affected != _FINAL_EXCEPTION_FINDINGS.get(record.get("id")) or
                record.get("independently_green") is not False):
            raise RemediationTraceError("high-gate exception was changed")
        _pinned_blob(
            workspace, candidate_sha, str(record["path"]),
            str(record["sha256"]), f"{record['id']} exception record")
    return dict(high)


def build_final_integration_evidence(workspace: str, *,
                                     candidate_sha: str) -> dict:
    """Join high, M1, and M2 truth without claiming the later FINAL-EVAL."""
    workspace = str(_workspace_path(workspace))
    _clean_candidate(workspace, candidate_sha)
    authority = _final_authority(workspace, candidate_sha)
    inventory = build_final_inventory(workspace, candidate_sha=candidate_sha)
    high = _validate_high_gate(workspace, candidate_sha, authority)
    plan = _json_blob(workspace, candidate_sha, "plan/tasks.json", "Plan")
    tasks = {row["id"]: row for row in plan.get("tasks", [])
             if isinstance(row, Mapping) and isinstance(row.get("id"), str)}
    task_commits = authority.get("task_commits")
    if not isinstance(task_commits, Mapping):
        raise RemediationTraceError("final task commit inventory is unavailable")
    expected_tasks = {
        str(row["task"]) for row in inventory["rows"]
    } | {"H1-I", "H2-I", "H3-I", "HG-EVAL", "M1-I", "M2-I"}
    if set(task_commits) != expected_tasks:
        raise RemediationTraceError("final task/leaf ancestry inventory differs")
    for task_id in sorted(expected_tasks):
        _require_ancestor(
            workspace, str(task_commits[task_id]), candidate_sha, task_id)
        task = tasks.get(task_id)
        if task is None:
            raise RemediationTraceError(f"{task_id} is absent from the Plan")
        for dependency in task.get("deps") or []:
            if dependency in task_commits:
                _require_ancestor(
                    workspace, str(task_commits[dependency]),
                    str(task_commits[task_id]), f"{dependency}->{task_id}")
    if (authority.get("high_gate", {}).get("commit") !=
            task_commits.get("HG-EVAL") or
            authority.get("high_gate", {}).get("results_sha256") !=
            _FINAL_HIGH_RESULTS_SHA256):
        raise RemediationTraceError("high-gate ancestry authority differs")
    focused = []
    for record in authority.get("focused_integration") or []:
        task_id = record.get("task_id")
        expected = _FINAL_JOIN_SELECTORS.get(task_id)
        if expected != (record.get("commit"), record.get("selector")):
            raise RemediationTraceError("focused integration selector differs")
        selector_path = str(record["selector"]).partition("::")[0]
        candidate_blob = _blob(workspace, candidate_sha, selector_path)
        integration_blob = _blob(
            workspace, str(record["commit"]), selector_path)
        if candidate_blob != integration_blob:
            raise RemediationTraceError(
                f"{task_id} focused evidence changed after integration")
        focused.append({
            **record,
            "path_sha256": hashlib.sha256(candidate_blob).hexdigest(),
            "candidate_sha": candidate_sha,
        })
    if {row["task_id"] for row in focused} != set(_FINAL_JOIN_SELECTORS):
        raise RemediationTraceError("M1/M2 focused integration is incomplete")
    high_by_id = {row["finding_id"]: row for row in high["results"]}
    dispositions = []
    for row in inventory["rows"]:
        finding_id = row["id"]
        if row["severity"] == "high":
            high_row = high_by_id[finding_id]
            disposition = {
                "finding_id": finding_id,
                "status": high_row["status"],
                "independent": high_row["independent"],
                "evidence_join": "HG-EVAL",
            }
            if "exception_id" in high_row:
                disposition["exception_id"] = high_row["exception_id"]
        else:
            if row["wave"] == "M1":
                evidence_join = "M1-I"
            elif row["wave"] == "M2":
                evidence_join = "M2-I"
            else:
                evidence_join = "HG-EVAL"
            disposition = {
                "finding_id": finding_id,
                "status": "focused-integration-green-awaiting-final-evaluation",
                "independent": False,
                "evidence_join": evidence_join,
            }
        dispositions.append(disposition)
    input_paths = (
        _FINAL_AUTHORITY_PATH, _FINAL_FINDINGS_SNAPSHOT_PATH,
        _FINAL_HIGH_RESULTS_PATH, "design/contract.json", "plan/tasks.json",
        "design/backlog/r0002-h1i-selector-receipt-authority.md",
        "design/backlog/r0002-h3c-retention-exceptions.md",
        "taskplane/remediation_trace.py",
        "taskplane/tests/test_em_m1_integration.py",
        "taskplane/tests/test_em_m2_integration.py",
        "taskplane/tests/test_em_remediation_integration.py",
    )
    exact_inputs = [{
        "path": path,
        "sha256": hashlib.sha256(_blob(
            workspace, candidate_sha, path)).hexdigest(),
    } for path in input_paths]
    tree = str(_git(
        workspace, "rev-parse", f"{candidate_sha}^{{tree}}")).strip()
    material = {
        "schema": FINAL_INTEGRATION_SCHEMA,
        "requirement_id": "R-0002",
        "candidate_sha": candidate_sha,
        "candidate_tree": tree,
        "git_evidence": _git_identity(),
        "disposition": "ready-for-independent-final-evaluation",
        "strict_ac5_status": "not-satisfied",
        "strict_ac8_status": "pending-independent-final-evaluation",
        "counts": {
            "total_trace_rows": 72,
            "high": 34,
            "medium": 28,
            "low": 10,
            "independently_green_high": 19,
            "attributed_non_independent_exceptions": 15,
            "focused_integrated_awaiting_final_evaluation": 38,
        },
        "inventory_fingerprint": inventory["inventory_fingerprint"],
        "task_commits": dict(task_commits),
        "high_gate": {
            "commit": task_commits["HG-EVAL"],
            "candidate_sha": high["candidate_sha"],
            "results_sha256": _FINAL_HIGH_RESULTS_SHA256,
            "strict_ac5_status": "not-satisfied",
        },
        "exceptions": list(authority["exceptions"]),
        "focused_integration": focused,
        "finding_dispositions": dispositions,
        "final_evaluation": dict(authority["final_evaluation"]),
        "exact_candidate_inputs": exact_inputs,
    }
    return {**material, "evidence_fingerprint": _digest(material)}


def verify_final_integration_evidence(workspace: str,
                                      evidence: Mapping) -> dict:
    """Reject mutated outcome, ancestry, exception, or exact-candidate data."""
    if (not isinstance(evidence, Mapping) or
            evidence.get("schema") != FINAL_INTEGRATION_SCHEMA):
        raise RemediationTraceError("final integration evidence schema is invalid")
    candidate_sha = str(evidence.get("candidate_sha") or "")
    expected = build_final_integration_evidence(
        workspace, candidate_sha=candidate_sha)
    if dict(evidence) != expected:
        raise RemediationTraceError(
            "final integration evidence differs from exact candidate truth")
    return expected
