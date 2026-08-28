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
