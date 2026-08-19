"""Deterministic risk-first engineering-review progression.

This module owns the additive ``contract:review-risk-progression`` surface:
the four non-negotiable deep floors, a single bounded light sweep, and
evidence-bound promotion or rejection of sweep concerns.  It is deliberately
pure and synchronous; persistence and dispatch remain ReviewKernel owners.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath


MANDATORY_DEEP_FLOORS = (
    "architecture",
    "code-quality",
    "security",
    "qa",
)
PROMOTION_SEVERITIES = frozenset({"blocker", "critical", "high", "major"})
DEFAULT_SWEEP_LIMIT = 8

_DOC_SUFFIXES = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})
_DOC_RULES = (
    ("integrability", re.compile(r"\b(api|endpoint|contract|schema|version(?:ing)?)\b", re.I)),
    ("security", re.compile(r"\b(auth(?:n|z|entication|orization)?|oauth|token|secret|security|permission)\b", re.I)),
    ("sre", re.compile(r"\b(runbook|incident|alert|rollback|recovery|on-call|slo)\b", re.I)),
    ("privacy-compliance", re.compile(r"\b(personal data|privacy|retention|deletion|consent)\b", re.I)),
    ("accessibility", re.compile(r"\b(accessibility|wcag|screen reader|keyboard)\b", re.I)),
    ("product", re.compile(r"\b(user guide|user-facing|journey|workflow)\b", re.I)),
)

_DOMAIN_MARKERS = {
    "security": re.compile(r"\b(auth(?:n|z|entication|orization)?|oauth|permission|secret|token|vulnerab|exploit)\b", re.I),
    # Use ownership-specific phrases here, not generic review vocabulary such
    # as "fixture" or "regression" that can legitimately describe evidence
    # for any lens.
    "qa": re.compile(r"\b(coverage|missing test|test suite|untested)\b", re.I),
    "dba": re.compile(r"\b(database|query|index|schema|sql|table)\b", re.I),
    "sre": re.compile(r"\b(slo|incident|alert|retry|timeout|recovery|on-call)\b", re.I),
    "integrability": re.compile(r"\b(api|contract|protocol|version|endpoint)\b", re.I),
}


def catalog_lens_ids() -> set[str]:
    """Return the current catalog ids without creating an import cycle."""
    import lens

    return {str(row["id"]) for row in lens.load_catalog()["lenses"]}


def _is_document(path: str) -> bool:
    p = PurePosixPath(str(path).replace("\\", "/"))
    return p.suffix.lower() in _DOC_SUFFIXES or p.name.lower() in {
        "readme", "changelog", "changes", "release-notes"
    } or p.name.lower().startswith(("readme.", "changelog."))


def document_lens_signals(files, content_by_file=None) -> dict[str, list[str]]:
    """Return the smallest explained lens set supported by document evidence.

    Missing code-module mapping is intentionally irrelevant here.  Malformed,
    absent, or ambiguous document content retains only the documentation lens;
    it never becomes either no review or an all-catalog fail-open.
    """
    content_by_file = content_by_file or {}
    out: dict[str, list[str]] = {}
    for raw in sorted({str(f).replace("\\", "/") for f in files or []}):
        if not _is_document(raw):
            continue
        out.setdefault("tech-writer", []).append(f"documentation surface: {raw}")
        value = content_by_file.get(raw, "")
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        if not isinstance(value, str) or "\x00" in value:
            continue
        bounded = value[:64 * 1024]
        path_text = raw.replace("-", " ").replace("_", " ")
        evidence_text = f"{path_text}\n{bounded}"
        for lens_id, pattern in _DOC_RULES:
            match = pattern.search(evidence_text)
            if match:
                out.setdefault(lens_id, []).append(
                    f"document evidence: {raw} contains {match.group(0).lower()}"
                )
    return {lens_id: sorted(set(reasons)) for lens_id, reasons in sorted(out.items())}


def apply_document_signals(verdict_map: dict, files, content_by_file=None) -> dict:
    """Promote evidence-backed document lenses to at least light, in place."""
    for lens_id, reasons in document_lens_signals(files, content_by_file).items():
        entry = verdict_map.get(lens_id)
        if entry is None:
            continue
        if entry.get("verdict") == "n/a":
            entry["verdict"] = "light"
        for reason in reasons:
            if reason not in entry["evidence"]:
                entry["evidence"].append(reason)
    return verdict_map


def initial_wave(routing: dict, *, sweep_limit: int = DEFAULT_SWEEP_LIMIT) -> dict:
    """Project a routing decision into deep slots plus at most one sweep."""
    if not isinstance(sweep_limit, int) or sweep_limit < 0:
        raise ValueError("sweep_limit must be a non-negative integer")
    rows = {str(row["id"]): row for row in routing.get("lenses") or []}
    missing = sorted(set(MANDATORY_DEEP_FLOORS) - set(rows))
    if missing:
        raise ValueError("routing missing mandatory review floor(s): " + ", ".join(missing))
    deep_ids = sorted(
        lens_id for lens_id, row in rows.items()
        if row.get("tier") == "deep" or lens_id in MANDATORY_DEEP_FLOORS
    )
    all_light = sorted(
        (lens_id for lens_id, row in rows.items()
         if row.get("tier") == "light" and lens_id not in deep_ids),
        key=lambda lens_id: (-float(rows[lens_id].get("score") or 0), lens_id),
    )
    light_ids = all_light[:sweep_limit]
    return {
        "schema": "taskplane.review-progression/v1",
        "deep": [
            {"slot": f"lens-{lens_id}", "lens": lens_id, "tier": "deep"}
            for lens_id in deep_ids
        ],
        "sweep": (
            {"slot": "lens-sweep", "tier": "light", "lenses": light_ids}
            if light_ids else None
        ),
        "sweep_count": 1 if light_ids else 0,
        "deferred_light": all_light[sweep_limit:],
    }


def _concern_fingerprint(row: dict) -> str:
    canonical = {
        key: str(row.get(key, "")).strip()
        for key in ("id", "severity", "lens", "evidence_ref", "rationale", "trigger")
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _in_charter(lens_id: str, rationale: str, trigger: str) -> bool:
    text = f"{trigger} {rationale}"
    claimed = {domain for domain, marker in _DOMAIN_MARKERS.items() if marker.search(text)}
    return not claimed or lens_id in claimed


def resolve_sweep_concerns(concerns, *, already_promoted=()) -> dict:
    """Normalize sweep concerns into deterministic promotions/rejections.

    Every high/major concern receives one outcome.  Promotion requires a
    catalog lens, evidence reference, rationale, trigger, and in-charter
    signal.  Exact replays are idempotently rejected as duplicates.
    """
    catalog = catalog_lens_ids()
    seen = {str(value) for value in already_promoted}
    promoted_lenses = {
        value.removeprefix("lens-") for value in seen
        if value.removeprefix("lens-") in catalog
    }
    promotions = []
    rejections = []
    for raw in concerns or []:
        row = dict(raw) if isinstance(raw, dict) else {}
        concern_id = str(row.get("id") or "").strip()
        severity = str(row.get("severity") or "").strip().lower()
        lens_id = str(row.get("lens") or "").strip()
        evidence_ref = str(row.get("evidence_ref") or "").strip()
        rationale = str(row.get("rationale") or "").strip()
        trigger = str(row.get("trigger") or "").strip()
        fingerprint = _concern_fingerprint(row)
        reason = None
        if fingerprint in seen or (concern_id and concern_id in seen):
            reason = "duplicate"
        elif severity not in PROMOTION_SEVERITIES:
            reason = "below-promotion-threshold"
        elif lens_id not in catalog:
            reason = "invalid-lens"
        elif not evidence_ref or not rationale or not trigger:
            reason = "missing-evidence"
        elif not _in_charter(lens_id, rationale, trigger):
            reason = "out-of-charter"
        elif lens_id in promoted_lenses:
            reason = "already-covered"
        if reason:
            rejections.append({
                "concern_id": concern_id,
                "lens": lens_id,
                "severity": severity,
                "reason": reason,
                "fingerprint": fingerprint,
            })
            continue
        seen.update({fingerprint, concern_id})
        promoted_lenses.add(lens_id)
        promotions.append({
            "concern_id": concern_id,
            "lens": lens_id,
            "slot": f"lens-{lens_id}",
            "tier": "deep",
            "severity": severity,
            "evidence_ref": evidence_ref,
            "rationale": rationale,
            "trigger": trigger,
            "fingerprint": fingerprint,
        })
    return {
        "schema": "taskplane.review-promotions/v1",
        "promotions": promotions,
        "rejections": rejections,
    }
