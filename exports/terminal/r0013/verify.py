"""Fail-closed verification for the R-0013 exact-candidate export.

The tracked template deliberately has no candidate SHA: a commit cannot
contain its own object id. After integration commits the candidate, this
module binds the exact HEAD to all eight terminal surfaces and the focused
selector receipts. It prepares evidence only; terminal authority remains an
engine-owned operation after final evaluation.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from taskplane import terminal_truth


TEMPLATE_SCHEMA = "taskplane.r0013-exact-candidate-successor-template/v1"
SUCCESSOR_SCHEMA = "taskplane.r0013-exact-candidate-successor/v1"
TOMBSTONE_SCHEMA = "taskplane.exact-sha-terminal-projection-tombstone/v1"
TERMINAL_SURFACE_SCHEMA = terminal_truth.TERMINAL_PROJECTION_SCHEMA
FULL_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")

SURFACE_IDS = terminal_truth.SURFACE_IDS

PREPARED_EVIDENCE_STATE = {
    "terminal_authority": "not-minted",
    "full_suite": "not-recorded",
    "release": "not-granted",
    "main_mutation": "not-granted",
    "publication": "not-granted",
}

_TEMPLATE_FIELDS = {
    "schema",
    "requirement_id",
    "finding_id",
    "candidate_binding",
    "surface_ids",
    "required_selectors",
    "prepared_evidence_state",
}
_CANDIDATE_FIELDS = {
    "schema",
    "requirement_id",
    "finding_id",
    "status",
    "candidate_sha",
    "template_sha256",
    "surfaces",
    "selectors",
    "evidence_state",
    "fingerprint",
}


class TerminalExportError(ValueError):
    """The candidate export is stale, incomplete, or claims unavailable proof."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _seal(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["fingerprint"] = _digest(result)
    return result


def _closed_mapping(
    value: object, expected: set[str], *, label: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise TerminalExportError(f"{label} fields are not closed")
    return value


def _full_object_id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not FULL_OBJECT_ID.fullmatch(value):
        raise TerminalExportError(f"{label} must be a full Git object id")
    return value


def _sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise TerminalExportError(f"{label} must be a SHA-256 digest")
    return value


def _validate_template(value: object) -> dict[str, Any]:
    template = _closed_mapping(value, _TEMPLATE_FIELDS, label="successor template")
    if template["schema"] != TEMPLATE_SCHEMA:
        raise TerminalExportError("successor template schema is invalid")
    if template["requirement_id"] != "R-0013" or template["finding_id"] != "H-32":
        raise TerminalExportError("successor template identity is invalid")
    binding = _closed_mapping(
        template["candidate_binding"],
        {
            "source",
            "field",
            "requires_full_object_id",
            "requires_clean_checkout",
            "output_name",
        },
        label="candidate binding",
    )
    if dict(binding) != {
        "source": "git-head-at-materialization",
        "field": "candidate_sha",
        "requires_full_object_id": True,
        "requires_clean_checkout": True,
        "output_name": "<candidate_sha>.json",
    }:
        raise TerminalExportError("candidate binding is not exact-HEAD fail-closed")
    if tuple(template["surface_ids"]) != SURFACE_IDS:
        raise TerminalExportError("successor template must bind all terminal surfaces")
    selectors = template["required_selectors"]
    if not isinstance(selectors, list) or not selectors:
        raise TerminalExportError("required selectors are incomplete")
    if any(
        not isinstance(selector, str)
        or not selector.startswith("taskplane/tests/test_em_h3_terminal_export.py::")
        for selector in selectors
    ) or len(selectors) != len(set(selectors)):
        raise TerminalExportError("required selectors are incomplete or contradictory")
    if template["prepared_evidence_state"] != PREPARED_EVIDENCE_STATE:
        raise TerminalExportError("template invents terminal or external authority")
    return dict(template)


def load_template(path: Path) -> dict[str, Any]:
    """Load the tracked successor template and enforce its closed contract."""
    return _validate_template(json.loads(path.read_text(encoding="utf-8")))


def validate_tombstone(
    value: object, *, expected_template: Mapping[str, Any]
) -> dict[str, Any]:
    """Prove the old exact-SHA filename cannot be consumed as active evidence."""
    template = _validate_template(expected_template)
    tombstone = _closed_mapping(
        value,
        {
            "schema",
            "active",
            "superseded_candidate_sha",
            "reason",
            "successor_template",
            "successor_schema",
            "authority",
        },
        label="terminal tombstone",
    )
    if tombstone["schema"] != TOMBSTONE_SCHEMA or tombstone["active"] is not False:
        raise TerminalExportError("stale terminal projection is still active")
    _full_object_id(tombstone["superseded_candidate_sha"], label="superseded SHA")
    if tombstone["successor_template"] != "successor-template.json":
        raise TerminalExportError("terminal tombstone points to an unknown successor")
    if tombstone["successor_schema"] != SUCCESSOR_SCHEMA:
        raise TerminalExportError("terminal tombstone successor schema is invalid")
    expected_authority = {
        key: template["prepared_evidence_state"][key]
        for key in ("release", "main_mutation", "publication")
    }
    if tombstone["authority"] != expected_authority:
        raise TerminalExportError("terminal tombstone invents external authority")
    return dict(tombstone)


def prepare_candidate_manifest(
    template: Mapping[str, Any],
    *,
    candidate_sha: str,
    surface_documents: Mapping[str, Mapping[str, Any]],
    selector_receipts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind candidate evidence without promoting it to terminal authority."""
    normalized_template = _validate_template(template)
    candidate_sha = _full_object_id(candidate_sha, label="candidate SHA")
    if set(surface_documents) != set(SURFACE_IDS):
        raise TerminalExportError("candidate must include exactly all terminal surfaces")
    surfaces: dict[str, dict[str, str]] = {}
    expected_identity: Mapping[str, Any] | None = None
    for surface_id in SURFACE_IDS:
        document = surface_documents[surface_id]
        if not isinstance(document, Mapping):
            raise TerminalExportError(f"{surface_id} surface must be an object")
        if document.get("schema") != TERMINAL_SURFACE_SCHEMA:
            raise TerminalExportError(f"{surface_id} surface schema is invalid")
        if document.get("surface_id") != surface_id:
            raise TerminalExportError(f"{surface_id} surface id is contradictory")
        identity = document.get("identity")
        if not isinstance(identity, Mapping) or identity.get("full_source_sha") != candidate_sha:
            raise TerminalExportError(f"{surface_id} surface is bound to a stale SHA")
        if expected_identity is None:
            expected_identity = identity
        try:
            terminal_truth.validate_terminal_surface(
                document,
                expected_surface_id=surface_id,
                expected_identity=expected_identity,
            )
        except terminal_truth.TerminalTruthError as exc:
            raise TerminalExportError(
                f"{surface_id} surface is not valid terminal evidence: {exc.detail}"
            ) from exc
        surfaces[surface_id] = {
            "candidate_sha": candidate_sha,
            "sha256": _digest(document),
        }

    required_selectors = normalized_template["required_selectors"]
    if set(selector_receipts) != set(required_selectors):
        raise TerminalExportError("candidate must include exactly all required selectors")
    selectors: list[dict[str, str]] = []
    for selector in required_selectors:
        receipt = _closed_mapping(
            selector_receipts[selector],
            {"candidate_sha", "outcome", "output_sha256"},
            label=f"selector receipt {selector}",
        )
        if receipt["candidate_sha"] != candidate_sha:
            raise TerminalExportError(f"selector {selector} is bound to a stale SHA")
        if receipt["outcome"] != "passed":
            raise TerminalExportError(f"selector {selector} did not pass")
        selectors.append(
            {
                "selector": selector,
                "candidate_sha": candidate_sha,
                "outcome": "passed",
                "output_sha256": _sha256(
                    receipt["output_sha256"], label=f"selector {selector} output"
                ),
            }
        )

    return _seal(
        {
            "schema": SUCCESSOR_SCHEMA,
            "requirement_id": "R-0013",
            "finding_id": "H-32",
            "status": "prepared-not-authoritative",
            "candidate_sha": candidate_sha,
            "template_sha256": _digest(normalized_template),
            "surfaces": surfaces,
            "selectors": selectors,
            "evidence_state": dict(PREPARED_EVIDENCE_STATE),
        }
    )


def verify_candidate_manifest(
    template: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    expected_sha: str,
    surface_documents: Mapping[str, Mapping[str, Any]],
    selector_receipts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Verify a prepared successor and reject every stale or partial binding."""
    expected_sha = _full_object_id(expected_sha, label="expected candidate SHA")
    candidate = _closed_mapping(
        manifest, _CANDIDATE_FIELDS, label="candidate successor"
    )
    if candidate["schema"] != SUCCESSOR_SCHEMA:
        raise TerminalExportError("candidate successor schema is invalid")
    if candidate["requirement_id"] != "R-0013" or candidate["finding_id"] != "H-32":
        raise TerminalExportError("candidate successor identity is invalid")
    if candidate["status"] != "prepared-not-authoritative":
        raise TerminalExportError("candidate successor falsely claims terminal status")
    if candidate["candidate_sha"] != expected_sha:
        raise TerminalExportError("candidate successor is bound to a stale SHA")
    normalized_template = _validate_template(template)
    if candidate["template_sha256"] != _digest(normalized_template):
        raise TerminalExportError("candidate successor template digest mismatch")

    surfaces = candidate["surfaces"]
    if not isinstance(surfaces, Mapping) or set(surfaces) != set(SURFACE_IDS):
        raise TerminalExportError("candidate successor omits a terminal surface")
    for surface_id in SURFACE_IDS:
        binding = _closed_mapping(
            surfaces[surface_id],
            {"candidate_sha", "sha256"},
            label=f"{surface_id} binding",
        )
        if binding["candidate_sha"] != expected_sha:
            raise TerminalExportError(f"{surface_id} binding is stale")
        _sha256(binding["sha256"], label=f"{surface_id} digest")

    selectors = candidate["selectors"]
    if not isinstance(selectors, list) or any(
        not isinstance(row, Mapping) for row in selectors
    ):
        raise TerminalExportError("candidate successor selectors are invalid")
    by_name = {row.get("selector"): row for row in selectors}
    required = normalized_template["required_selectors"]
    if len(selectors) != len(required) or set(by_name) != set(required):
        raise TerminalExportError("candidate successor omits a required selector")
    for selector in required:
        receipt = _closed_mapping(
            by_name[selector],
            {"selector", "candidate_sha", "outcome", "output_sha256"},
            label=f"selector binding {selector}",
        )
        if receipt["candidate_sha"] != expected_sha or receipt["outcome"] != "passed":
            raise TerminalExportError(f"selector {selector} is stale or not passing")
        _sha256(receipt["output_sha256"], label=f"selector {selector} output")

    if candidate["evidence_state"] != PREPARED_EVIDENCE_STATE:
        raise TerminalExportError("candidate successor invents unavailable authority")
    unsigned = {key: value for key, value in candidate.items() if key != "fingerprint"}
    if candidate["fingerprint"] != _digest(unsigned):
        raise TerminalExportError("candidate successor fingerprint mismatch")
    rebuilt = prepare_candidate_manifest(
        normalized_template,
        candidate_sha=expected_sha,
        surface_documents=surface_documents,
        selector_receipts=selector_receipts,
    )
    if dict(candidate) != rebuilt:
        raise TerminalExportError("candidate successor does not match its evidence")
    return dict(candidate)


def repository_head(repository: Path) -> str:
    """Resolve the full current Git candidate for post-commit materialization."""
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        text=True,
        encoding="utf-8",
        errors="strict",
    ).strip()


def clean_repository_head(repository: Path) -> str:
    """Resolve HEAD only when tracked candidate inputs are fully committed."""
    head = repository_head(repository)
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=repository,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    if status:
        raise TerminalExportError("candidate checkout must be clean and committed")
    return head


def prepare_repository_candidate(
    template: Mapping[str, Any],
    *,
    repository: Path,
    surface_documents: Mapping[str, Mapping[str, Any]],
    selector_receipts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Prepare from one exact clean Git HEAD, never caller-selected identity."""
    return prepare_candidate_manifest(
        template,
        candidate_sha=clean_repository_head(repository),
        surface_documents=surface_documents,
        selector_receipts=selector_receipts,
    )
