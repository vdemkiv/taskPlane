"""Canonical, provenance-bound Definition-of-Ready evidence for reviews.

The module deliberately owns facts that exist *before* lens routing.  It does
not judge code and it does not let a host adapter silently turn prose into a
pass.  Discovery records every attempted source, classification keeps feature
criteria separate from review directives, and the criterion ledger makes the
remaining human/agent judgment explicit.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Iterable, Mapping


DOR_SCHEMA = "taskplane.review-dor-evidence/v1"
CRITERION_SCHEMA = "taskplane.review-criterion-ledger/v1"

# Eight canonical probes.  Linked specifications and standalone acceptance
# text share one requirements probe because hosts expose them differently.
SOURCE_KINDS = (
    "pr_title",
    "pr_body",
    "pr_comments",
    "commits",
    "changelog",
    "linked_issue",
    "linked_requirements",
    "repository_contracts",
)

_SOURCE_ALIASES = {
    "comment": "pr_comments",
    "comments": "pr_comments",
    "commit": "commits",
    "linked_spec": "linked_requirements",
    "acceptance_text": "linked_requirements",
    "linked_acceptance": "linked_requirements",
    "contracts": "repository_contracts",
}

_CLASSIFICATIONS = frozenset({
    "objective", "acceptance-criterion", "review-directive", "constraint",
    "context",
})
_CRITERION_STATUSES = frozenset({
    "pass", "fail", "unproven", "not-applicable",
})

_DIRECTIVE_PHRASES = re.compile(
    r"\b(review|look for|check for|identify|audit|consider|find issues?)\b",
    re.IGNORECASE,
)
_CONSTRAINT_PHRASES = re.compile(
    r"\b(must not|do not|don't|cannot|can't|read[- ]only|no push|without "
    r"pushing|only in|restricted to|shall not)\b",
    re.IGNORECASE,
)
_ACCEPTANCE_PHRASES = re.compile(
    r"\b(must|should|shall|ensure|supports?|reports?|requires?|prevents?|"
    r"allows?|remains?|preserves?|produces?)\b",
    re.IGNORECASE,
)
_IMPLEMENTATION_START = re.compile(
    r"^(add|extract|update|implement|create|remove|rename|refactor|fix|"
    r"support|preserve|prevent|require|run)\b",
    re.IGNORECASE,
)
_OBJECTIVE_START = re.compile(
    r"^(feat(?:ure)?|refactor|fix|build|implement)\s*:", re.IGNORECASE)

_DIRECTIVE_LENSES = {
    "security": (
        "security", "vulnerab", "authorization", "authentication", "authz",
        "injection", "idor", "secret", "unsafe input", "supply chain",
    ),
    "qa": (
        "bug", "defect", "logic error", "regression", "incorrect", "edge case",
    ),
    "design": (
        "usability", "user experience", "interaction", "confusing", "ux",
    ),
    "scalability": (
        "performance", "at scale", "heavy load", "throughput", "latency",
        "unbounded", "large data",
    ),
    "code-quality": (
        "code quality", "best practice", "maintainability", "readability",
        "complexity", "clean code",
    ),
    "architecture": (
        "architecture", "system design", "design trade-off", "design tradeoff",
        "coupling", "boundary",
    ),
}


def _canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _fingerprint(value) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _kind(value: object) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    return _SOURCE_ALIASES.get(raw, raw)


def _units(text: str) -> list[tuple[str, str]]:
    """Return stable (form, text) units without interpreting markdown as code."""
    out: list[tuple[str, str]] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        bullet = re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)(.+)$", line)
        if bullet:
            out.append(("bullet", bullet.group(1).strip()))
            continue
        # Preserve commit/changelog subjects and headings as one unit, while
        # ordinary prose can carry multiple independently classifiable claims.
        pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z])", line)
        out.extend(("prose", piece.strip().rstrip("."))
                   for piece in pieces if piece.strip())
    return out


def _classification(text: str, *, kind: str, form: str) -> str:
    value = text.strip()
    lower = value.lower()
    if kind == "repository_contracts":
        return "context"
    if _CONSTRAINT_PHRASES.search(value):
        return "constraint"
    if _DIRECTIVE_PHRASES.search(value) and any(
            phrase in lower for phrases in _DIRECTIVE_LENSES.values()
            for phrase in phrases):
        return "review-directive"
    if kind == "pr_title" or _OBJECTIVE_START.search(value):
        return "objective"
    if (_ACCEPTANCE_PHRASES.search(value) or _IMPLEMENTATION_START.search(value)):
        # A requested review remains a directive even if it says "should".
        if _DIRECTIVE_PHRASES.search(value):
            return "review-directive"
        return "acceptance-criterion"
    if kind in {"commits", "changelog", "linked_requirements"} and (
            form == "bullet" or re.search(r"\b(add|added|extract|implement|fix)",
                                          lower)):
        return "acceptance-criterion"
    return "context"


def _source_status(source: Mapping) -> str:
    if not bool(source.get("accessible", True)):
        return "inaccessible"
    if not bool(source.get("fresh", True)):
        return "stale"
    if source.get("contradictions"):
        return "contradictory"
    return "available"


def _source_record(source: Mapping, index: int) -> dict:
    kind = _kind(source.get("kind"))
    identity = str(source.get("identity") or f"{kind}:{index}").strip()
    revision = str(source.get("revision") or "unknown").strip()
    content = str(source.get("content") or "")
    provenance = {
        "kind": kind,
        "identity": identity,
        "revision": revision,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
    return {
        "kind": kind,
        "identity": identity,
        "revision": revision,
        "status": _source_status(source),
        "provenance_ref": "dor-source:" + _fingerprint(provenance),
        "contradictions": sorted({str(row) for row in
                                  source.get("contradictions") or []}),
        "content": content,
        "material_ambiguity": bool(source.get("material_ambiguity")),
    }


def _requested_lenses(directives: Iterable[dict]) -> dict[str, list[str]]:
    routed: dict[str, list[str]] = {}
    for item in directives:
        text = str(item.get("text") or "").lower()
        for lens, phrases in _DIRECTIVE_LENSES.items():
            if any(phrase in text for phrase in phrases):
                routed.setdefault(lens, []).append(str(item["id"]))
    return {lens: sorted(set(ids)) for lens, ids in sorted(routed.items())}


def _has_lens_signal(text: str) -> bool:
    lower = str(text or "").lower()
    return any(phrase in lower for phrases in _DIRECTIVE_LENSES.values()
               for phrase in phrases)


def discover(sources: Iterable[Mapping], *, target_revision: str = "") -> dict:
    """Probe all canonical sources and return pre-routing DoR evidence.

    Callers pass observations, including unsuccessful observations.  A source
    omitted by the host is still emitted as ``missing`` so absence cannot be
    mistaken for a completed check.
    """
    records = [_source_record(source, index)
               for index, source in enumerate(sources or (), 1)]
    checks: dict[str, dict] = {}
    for kind in SOURCE_KINDS:
        matches = [row for row in records if row["kind"] == kind]
        if not matches:
            checks[kind] = {
                "status": "missing", "identity": "", "revision": "",
                "provenance_ref": "", "contradictions": [],
            }
            continue
        # Keep every observation in ``sources`` and make the summary choose
        # the most actionable state deterministically.
        priority = {"contradictory": 0, "inaccessible": 1, "stale": 2,
                    "available": 3}
        chosen = sorted(matches, key=lambda row: (
            priority[row["status"]], row["identity"], row["revision"]))[0]
        checks[kind] = {key: chosen[key] for key in (
            "status", "identity", "revision", "provenance_ref",
            "contradictions")}

    items: list[dict] = []
    clarification_candidates: list[dict] = []
    for source in records:
        if source["status"] != "available":
            continue
        # Review requests are commonly written as an introductory sentence
        # followed by terse category bullets.  Carry only that local semantic
        # context; a random security-related comment without a review request
        # remains context/acceptance rather than silently expanding routing.
        directive_scope = bool(_DIRECTIVE_PHRASES.search(source["content"]))
        for ordinal, (form, text) in enumerate(_units(source["content"]), 1):
            classification = _classification(
                text, kind=source["kind"], form=form)
            if (classification == "context" and directive_scope and
                    _has_lens_signal(text)):
                classification = "review-directive"
            seed = {"source": source["provenance_ref"], "ordinal": ordinal,
                    "text": text, "classification": classification}
            item = {
                "id": "dor-item-" + _fingerprint(seed)[:16],
                "text": text,
                "classification": classification,
                "form": form,
                "source_ref": source["provenance_ref"],
                "source_identity": source["identity"],
                "source_revision": source["revision"],
            }
            items.append(item)
            if source["material_ambiguity"]:
                clarification_candidates.append({
                    "item_id": item["id"],
                    "question": "Clarify the requirement because it changes "
                                "routing, validation, or verdict interpretation.",
                    "reason": "material_review_ambiguity",
                })

    # One consolidated clarification is the hard review bound.
    clarifications = clarification_candidates[:1]
    criteria = [{"id": row["id"], "text": row["text"],
                 "source_ref": row["source_ref"],
                 "source_identity": row["source_identity"],
                 "source_revision": row["source_revision"]}
                for row in items
                if row["classification"] == "acceptance-criterion"]
    directives = [row for row in items
                  if row["classification"] == "review-directive"]
    requested = _requested_lenses(directives)
    combined = " ".join(row["text"].lower() for row in items)
    executable = bool(re.search(
        r"\b(run|execute)\b.{0,32}\b(test|tests|build|lint|check|validation)\b|"
        r"\b(dynamic|runtime) validation\b", combined))
    blockers = []
    if clarifications:
        blockers.append("material_ambiguity")
    if any(row["status"] in {"contradictory", "stale"}
           for row in checks.values()):
        blockers.append("dor_source_conflict_or_staleness")
    if not criteria:
        blockers.append("acceptance_criteria_missing")

    payload = {
        "schema": DOR_SCHEMA,
        "target_revision": str(target_revision or ""),
        "sources": [{key: value for key, value in row.items()
                     if key != "content"} for row in records],
        "source_checks": checks,
        "items": items,
        "criteria": criteria,
        "review_directives": directives,
        "requested_lenses": requested,
        "executable_validation_requested": executable,
        "clarifications": clarifications,
        "clarification_count": len(clarifications),
        "blockers": blockers,
        "approvable": not blockers,
    }
    payload["fingerprint"] = _fingerprint(payload)
    return payload


def criterion_result(criterion: Mapping, status: str, rationale: str,
                     evidence_ref: str, verification_method: str,
                     responsible: str) -> dict:
    """Create one strict criterion judgment without inventing evidence."""
    normalized = str(status or "").strip().lower().replace("_", "-")
    if normalized not in _CRITERION_STATUSES:
        raise ValueError("criterion status must be pass, fail, unproven, or "
                         "not-applicable")
    text = str(criterion.get("text") or criterion.get("criterion") or "").strip()
    if not text:
        raise ValueError("criterion text is required")
    row = {
        "id": str(criterion.get("id") or "criterion-" +
                  _fingerprint(text)[:16]),
        "criterion": text,
        "status": normalized,
        "rationale": str(rationale or "").strip(),
        "evidence_ref": str(evidence_ref or "").strip(),
        "verification_method": str(verification_method or "").strip(),
        "responsible": str(responsible or "").strip(),
    }
    for key in ("source_ref", "source_identity", "source_revision"):
        if str(criterion.get(key) or "").strip():
            row[key] = str(criterion[key]).strip()
    return row


def criterion_ledger(results: Iterable[Mapping], *, revision: str) -> dict:
    """Bind criterion judgments to a revision and compute approval safety."""
    rows: list[dict] = []
    blockers: list[str] = []
    for value in results or ():
        row = criterion_result(
            value, str(value.get("status") or ""),
            str(value.get("rationale") or ""),
            str(value.get("evidence_ref") or ""),
            str(value.get("verification_method") or ""),
            str(value.get("responsible") or ""))
        row["revision"] = str(revision or "")
        if not row["revision"]:
            raise ValueError("criterion ledger revision is required")
        missing_common = not row["verification_method"] or not row["responsible"]
        if row["status"] == "fail":
            blockers.append("criterion_failed")
        elif row["status"] == "unproven":
            blockers.append("criterion_unproven")
        elif row["status"] == "not-applicable" and (
                not row["rationale"] or not row["evidence_ref"]):
            blockers.append("unjustified_not_applicable")
        elif row["status"] == "pass" and (
                not row["rationale"] or not row["evidence_ref"]):
            blockers.append("criterion_evidence_missing")
        if missing_common:
            blockers.append("criterion_verification_owner_missing")
        rows.append(row)
    if not rows:
        blockers.append("criteria_missing")
    blockers = sorted(set(blockers))
    payload = {
        "schema": CRITERION_SCHEMA,
        "revision": str(revision or ""),
        "criteria": rows,
        "blockers": blockers,
        "approvable": not blockers,
    }
    payload["fingerprint"] = _fingerprint(payload)
    return payload
