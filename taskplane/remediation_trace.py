"""Exact-candidate remediation evidence and closure gates.

The trace is intentionally content-addressed and repository-derived.  Callers
may report an outcome and identities, but cannot choose the finding metadata,
selector, production boundary, source bytes, or candidate revision that the
validator binds into a closure row.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Mapping, Sequence


H1_FINDING_IDS = (
    "H-03", "H-04", "H-05", "H-06", "H-07", "H-08", "H-14",
    "H-15", "H-19", "H-22", "H-26", "H-30", "H-34",
)
H1_TRACE_SCHEMA = "taskplane.remediation-h1-trace/v1"
H1_RESULT_SCHEMA = "taskplane.remediation-finding-result/v1"
_SHA = re.compile(r"[0-9a-f]{40,64}\Z")


class RemediationTraceError(ValueError):
    """A remediation result set is incomplete, stale, or self-attested."""


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()


def _git(workspace: str, *args: str, binary: bool = False):
    result = subprocess.run(
        ["git", *args], cwd=workspace, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        **({} if binary else {
            "text": True, "encoding": "utf-8", "errors": "replace",
        }))
    if result.returncode != 0:
        raise RemediationTraceError(
            "remediation trace could not resolve exact Git evidence")
    return result.stdout


def _exact_head(workspace: str) -> str:
    head = str(_git(workspace, "rev-parse", "HEAD")).strip()
    if not _SHA.fullmatch(head):
        raise RemediationTraceError("candidate SHA is invalid")
    return head


def _clean_candidate(workspace: str, candidate_sha: str) -> None:
    if candidate_sha != _exact_head(workspace):
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


def finding_result(workspace: str, *, candidate_sha: str,
                   finding_id: str, status: str, selector_status: str,
                   builder_identity: str, evaluator_identity: str) -> dict:
    """Create one repository-derived row from an externally observed result."""
    workspace = str(Path(workspace).resolve())
    _clean_candidate(workspace, candidate_sha)
    finding = _finding_map(workspace, candidate_sha).get(finding_id)
    if finding is None or finding_id not in H1_FINDING_IDS:
        raise RemediationTraceError("finding is outside the H1 closure set")
    if status != "closed" or selector_status != "passed":
        raise RemediationTraceError(f"{finding_id} is not independently green")
    if (not str(builder_identity).strip() or
            not str(evaluator_identity).strip() or
            builder_identity == evaluator_identity):
        raise RemediationTraceError(
            f"{finding_id} requires distinct Build and Evaluate identities")
    source_path = _path_from_source(finding.get("source"))
    selector = str(finding.get("evidence") or "")
    selector_path = _selector_path(selector)
    source_bytes = _blob(workspace, candidate_sha, source_path)
    selector_bytes = _blob(workspace, candidate_sha, selector_path)
    material = {
        "schema": H1_RESULT_SCHEMA,
        "finding_id": finding_id,
        "severity": "high",
        "task_id": finding.get("task"),
        "owner": finding.get("owner"),
        "candidate_sha": candidate_sha,
        "status": status,
        "selector": selector,
        "selector_status": selector_status,
        "builder_identity": builder_identity,
        "evaluator_identity": evaluator_identity,
        "production_boundary": {
            "contracts": list(finding.get("boundaries") or []),
            "source": str(finding.get("source") or ""),
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "selector_sha256": hashlib.sha256(selector_bytes).hexdigest(),
        },
    }
    return {**material, "content_fingerprint": _digest(material)}


def build_h1_trace(workspace: str, *, candidate_sha: str,
                   results: Sequence[Mapping]) -> dict:
    """Validate and join exactly one independently green row per H1 id."""
    workspace = str(Path(workspace).resolve())
    _clean_candidate(workspace, candidate_sha)
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
        raise RemediationTraceError("H1 results must be a sequence")
    rows = [dict(row) for row in results if isinstance(row, Mapping)]
    if len(rows) != len(results):
        raise RemediationTraceError("H1 result row is invalid")
    ids = [row.get("finding_id") for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(H1_FINDING_IDS):
        raise RemediationTraceError(
            "H1 trace requires exactly one result for every H1 finding")
    canonical = []
    for finding_id in H1_FINDING_IDS:
        supplied = next(row for row in rows
                        if row.get("finding_id") == finding_id)
        expected = finding_result(
            workspace, candidate_sha=candidate_sha,
            finding_id=finding_id, status=str(supplied.get("status") or ""),
            selector_status=str(supplied.get("selector_status") or ""),
            builder_identity=str(supplied.get("builder_identity") or ""),
            evaluator_identity=str(supplied.get("evaluator_identity") or ""))
        if supplied != expected:
            raise RemediationTraceError(
                f"{finding_id} result differs from exact repository evidence")
        canonical.append(expected)
    material = {
        "schema": H1_TRACE_SCHEMA,
        "candidate_sha": candidate_sha,
        "required_finding_ids": list(H1_FINDING_IDS),
        "result_count": len(canonical),
        "results_fingerprint": _digest(canonical),
        "results": canonical,
    }
    return {**material, "trace_fingerprint": _digest(material)}


def verify_h1_trace(workspace: str, trace: Mapping) -> dict:
    """Re-derive an H1 trace and reject missing, stale, or forged material."""
    if not isinstance(trace, Mapping) or trace.get("schema") != H1_TRACE_SCHEMA:
        raise RemediationTraceError("H1 trace schema is invalid")
    candidate_sha = str(trace.get("candidate_sha") or "")
    rebuilt = build_h1_trace(
        workspace, candidate_sha=candidate_sha,
        results=trace.get("results") if isinstance(trace.get("results"), list)
        else [])
    if dict(trace) != rebuilt:
        raise RemediationTraceError("H1 trace fingerprint or inventory is mixed")
    return rebuilt
