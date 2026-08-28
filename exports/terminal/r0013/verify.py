"""Fail-closed preparation of the R-0013 exact-candidate successor.

The historical projection remains immutable at its original SHA filename.
This module validates the separate tombstone and delegates candidate
composition to ``TerminalCoordinator`` so selector results cannot be authored
or redigested by an export caller. It prepares evidence only: FINAL-I owns
the later terminal-authority transition.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from taskplane import delivery_ports, terminal_truth


TEMPLATE_SCHEMA = terminal_truth.EXACT_CANDIDATE_TEMPLATE_SCHEMA
SUCCESSOR_SCHEMA = terminal_truth.EXACT_CANDIDATE_SUCCESSOR_SCHEMA
TOMBSTONE_SCHEMA = "taskplane.exact-sha-terminal-projection-tombstone/v1"
STALE_SHA = "106af4631ab5b5c041055b9b9b918d78a18ae50b"
ORIGINAL_FILENAME = f"{STALE_SHA}.json"
ORIGINAL_SHA256 = "1e41748672f8d492823824b6e2103ac87484f2687389d80567f231ea4151c459"
TOMBSTONE_FILENAME = f"{STALE_SHA}.tombstone.json"
TOMBSTONE_REASON = (
    "Historical exact-SHA projection retained unchanged; superseded as current "
    "candidate evidence by the R-0002 successor contract."
)
FULL_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")

SURFACE_IDS = terminal_truth.SURFACE_IDS
PREPARED_EVIDENCE_STATE = {
    "terminal_authority": "not-minted",
    "full_suite": "not-recorded",
    "release": "not-granted",
    "main_mutation": "not-granted",
    "publication": "not-granted",
}

_TEMPLATE_FIELDS = {
    "schema", "requirement_id", "finding_id", "candidate_binding",
    "surface_ids", "required_selectors", "prepared_evidence_state",
}
_TOMBSTONE_FIELDS = {
    "schema", "active", "superseded_candidate_sha", "original_filename",
    "original_sha256", "reason", "successor_template", "successor_schema",
}


class TerminalExportError(ValueError):
    """The candidate export is stale, partial, external, or unauthorized."""


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


def _closed_mapping(value: object, expected: set[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise TerminalExportError(f"{label} fields are not closed")
    return value


def _validate_template(value: object) -> dict[str, Any]:
    template = _closed_mapping(value, _TEMPLATE_FIELDS, label="successor template")
    if template["schema"] != TEMPLATE_SCHEMA or \
            template["requirement_id"] != "R-0013" or \
            template["finding_id"] != "H-32":
        raise TerminalExportError("successor template identity is invalid")
    binding = _closed_mapping(
        template["candidate_binding"],
        {
            "source", "field", "requires_full_object_id",
            "requires_clean_checkout", "output_name",
        },
        label="candidate binding",
    )
    if dict(binding) != {
        "source": "trusted-git-head-at-materialization",
        "field": "candidate_sha",
        "requires_full_object_id": True,
        "requires_clean_checkout": True,
        "output_name": "<candidate_sha>.json",
    }:
        raise TerminalExportError("candidate binding is not trusted exact-HEAD")
    if tuple(template["surface_ids"]) != SURFACE_IDS:
        raise TerminalExportError("successor template must bind all terminal surfaces")
    selectors = template["required_selectors"]
    if not isinstance(selectors, list) or not selectors or \
            len(selectors) != len(set(selectors)) or any(
                not isinstance(selector, str)
                or not selector.startswith(
                    "taskplane/tests/test_em_h3_terminal_export.py::"
                )
                for selector in selectors
            ):
        raise TerminalExportError("required selectors are incomplete or contradictory")
    if template["prepared_evidence_state"] != PREPARED_EVIDENCE_STATE:
        raise TerminalExportError("template invents terminal or external authority")
    return dict(template)


def load_template(path: Path) -> dict[str, Any]:
    try:
        return _validate_template(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TerminalExportError("successor template is unreadable") from exc


def validate_tombstone(
    value: object,
    *,
    expected_template: Mapping[str, Any],
    original_path: Path,
    tombstone_path: Path,
) -> dict[str, Any]:
    """Bind a separately named tombstone to unchanged historical bytes."""
    _validate_template(expected_template)
    tombstone = _closed_mapping(value, _TOMBSTONE_FIELDS, label="terminal tombstone")
    expected = {
        "schema": TOMBSTONE_SCHEMA,
        "active": False,
        "superseded_candidate_sha": STALE_SHA,
        "original_filename": ORIGINAL_FILENAME,
        "original_sha256": ORIGINAL_SHA256,
        "reason": TOMBSTONE_REASON,
        "successor_template": "successor-template.json",
        "successor_schema": SUCCESSOR_SCHEMA,
    }
    if dict(tombstone) != expected:
        raise TerminalExportError("terminal tombstone schema or reason is not exact")
    if original_path.name != ORIGINAL_FILENAME or \
            tombstone_path.name != TOMBSTONE_FILENAME or \
            original_path.parent != tombstone_path.parent or \
            original_path == tombstone_path:
        raise TerminalExportError("terminal tombstone is misnamed or replaces history")
    try:
        original = original_path.read_bytes()
    except OSError as exc:
        raise TerminalExportError("historical terminal projection is unavailable") from exc
    if hashlib.sha256(original).hexdigest() != ORIGINAL_SHA256:
        raise TerminalExportError("historical terminal projection bytes changed")
    return dict(tombstone)


def _candidate_evidence_paths(
    repository: Path,
    template_path: Path,
    template: Mapping[str, Any],
) -> tuple[Path, ...]:
    paths = [template_path, Path(__file__).resolve()]
    paths.extend(
        repository / str(selector).split("::", 1)[0]
        for selector in template["required_selectors"]
    )
    return tuple(dict.fromkeys(paths))


def prepare_candidate_manifest(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Reject the obsolete caller-authored candidate/receipt path."""
    del args, kwargs
    raise TerminalExportError(
        "caller-authored selector receipts cannot prepare candidate evidence"
    )


def prepare_repository_candidate(
    *,
    template_path: Path,
    repository: Path,
    surface_documents: Mapping[str, Mapping[str, Any]],
    coordinator: terminal_truth.TerminalCoordinator,
    git_inspector: delivery_ports.TrustedGitInspector | None = None,
) -> terminal_truth.ExactCandidateExportReceipt:
    """Prepare through trusted Git and the real terminal composition consumer."""
    template = load_template(template_path)
    inspector = git_inspector or delivery_ports.TrustedGitInspector()
    try:
        snapshot = inspector.snapshot(
            repository,
            evidence_paths=_candidate_evidence_paths(
                Path(repository).resolve(), template_path, template
            ),
        )
        receipt = coordinator.compose_exact_candidate_export(
            snapshot=snapshot,
            template=template,
            surface_documents=surface_documents,
        )
        inspector.assert_unchanged(snapshot)
    except (delivery_ports.DeliveryPortError, terminal_truth.TerminalTruthError) as exc:
        raise TerminalExportError(str(exc)) from exc
    return receipt


def verify_candidate_manifest(
    *,
    template_path: Path,
    manifest: Mapping[str, Any],
    expected_sha: str,
) -> dict[str, Any]:
    """Revalidate one live, immutable, coordinator-produced successor."""
    if not FULL_OBJECT_ID.fullmatch(str(expected_sha)):
        raise TerminalExportError("expected candidate SHA must be a full object id")
    template = load_template(template_path)
    if not isinstance(manifest, terminal_truth.ExactCandidateExportReceipt):
        raise TerminalExportError("live exact-candidate receipt is required")
    try:
        return manifest._coordinator.validate_exact_candidate_export(
            manifest,
            expected_sha=expected_sha,
            expected_template_sha256=_digest(template),
        )
    except terminal_truth.TerminalTruthError as exc:
        raise TerminalExportError(str(exc)) from exc


def clean_repository_head(repository: Path) -> str:
    """Resolve a trusted exact HEAD including untracked-file cleanliness."""
    try:
        return delivery_ports.TrustedGitInspector().snapshot(repository).head_sha
    except delivery_ports.DeliveryPortError as exc:
        raise TerminalExportError(str(exc)) from exc


repository_head = clean_repository_head
