"""Deterministic risk-first engineering-review progression.

This module owns the additive ``contract:review-risk-progression`` surface:
risk-scaled attributable deep floors, a single bounded light sweep, and
evidence-bound promotion or rejection of sweep concerns.  It is deliberately
pure and synchronous; persistence and dispatch remain ReviewKernel owners.
"""

from __future__ import annotations

import copy
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

_DOC_AMBIGUITY = re.compile(
    r"\b(tbd|to be determined|ambiguous|"
    r"(?:impact|ownership|behaviou?r|contract|scope)\s+(?:is\s+)?unknown|"
    r"unclear\s+(?:impact|ownership|behaviou?r|contract|scope))\b",
    re.I,
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


def document_evidence_uncertainty(files, content_by_file=None) -> str | None:
    """Return an explicit reason only for evidenced ambiguity or corruption.

    Missing content or module mapping is not evidence of risk and therefore
    stays on the normal single-lens documentation route.  Corrupt payloads and
    explicit uncertainty markers are review evidence, so callers can widen to
    the four risk floors without treating every unmapped document as risky.
    """
    content_by_file = content_by_file or {}
    for raw in sorted({str(f).replace("\\", "/") for f in files or []}):
        if not _is_document(raw) or raw not in content_by_file:
            continue
        value = content_by_file[raw]
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                return f"corrupt document evidence: {raw} is not valid UTF-8"
        if not isinstance(value, str):
            return f"corrupt document evidence: {raw} is not text"
        if "\x00" in value:
            return f"corrupt document evidence: {raw} contains NUL"
        if "\ufffd" in value:
            return f"corrupt document evidence: {raw} contains invalid UTF-8"
        match = _DOC_AMBIGUITY.search(value[:64 * 1024])
        if match:
            return (
                f"ambiguous document evidence: {raw} contains explicit "
                f"uncertainty marker {match.group(0).lower()!r}"
            )
    return None


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


def _quick_only(review_policy: dict | None) -> bool:
    return isinstance(review_policy, dict) and \
        review_policy.get("depth") == "quick-only"


def apply_depth_policy(routing: dict, review_policy: dict | None) -> dict:
    """Apply a requirement-bound depth ceiling after every routing override.

    Quick-only is a ceiling, not a second applicability mapper.  Existing
    deep/light decisions keep their evidence and all applicable lenses are
    batched into the one cheap sweep; n/a dispositions remain n/a.  Copying
    prevents the requirement policy from mutating a caller's cached route.
    """
    routed = copy.deepcopy(routing or {})
    context = routed.setdefault("context", {})
    if not _quick_only(review_policy):
        if isinstance(review_policy, dict):
            context["review_depth_policy"] = copy.deepcopy(review_policy)
        return routed

    active = []
    for row in routed.get("lenses") or []:
        # Applicability is the union of both routing signals.  Older routes
        # use tier="sweep" without a verdict, while retained/caller-provided
        # decisions can temporarily disagree (for example tier="deep" with
        # verdict="n/a").  Under quick-only, any applicable signal must be
        # retained in the quick sweep and no deep signal may survive.
        verdict = str(row.get("verdict") or "").strip().lower()
        tier = str(row.get("tier") or "").strip().lower()
        signals = {"deep" if value == "deep (forced)" else value
                   for value in (verdict, tier)}
        applicable = bool(signals & {"deep", "light", "sweep"})
        if not applicable:
            # Keep the coverage-honesty row but make its inactivity
            # canonical.  Gate validation uses mode != "none" as the exact
            # set that owes a leased result.
            row["tier"] = row["verdict"] = "n/a"
            row["mode"] = "none"
            continue
        if "deep" in signals:
            row["initial_verdict"] = row.get("initial_verdict") or "deep"
            evidence = row.setdefault("evidence", [])
            reason = "requirement-bound quick-only depth ceiling"
            if reason not in evidence:
                evidence.append(reason)
            reasons = row.setdefault("reasons", [])
            if reason not in reasons:
                reasons.append(reason)
        automatic = bool(context.get("automatic_review"))
        row["tier"] = row["verdict"] = (
            "sweep" if automatic else "light")
        row["mode"] = "subagent" if automatic else "inline"
        active.append(str(row.get("id") or ""))

    active = sorted(value for value in active if value)
    progression = context.setdefault("review_progression", {})
    # Stage=review routes persist concrete deep slot identities in this
    # canonical field.  Clear them along with the row dispositions so no
    # model-facing or rendered output can advertise a pre-policy deep wave.
    progression["deep_slots"] = []
    progression.pop("deep_lenses", None)
    progression["sweep_lenses"] = active
    progression["sweep_count"] = (
        len(active) if context.get("automatic_review") else
        (1 if active else 0))
    context["review_depth_policy"] = copy.deepcopy(review_policy)
    return routed


def initial_wave(routing: dict, *, sweep_limit: int = DEFAULT_SWEEP_LIMIT) -> dict:
    """Project routing into independently leased workers for one sweep set."""
    if not isinstance(sweep_limit, int) or sweep_limit < 0:
        raise ValueError("sweep_limit must be a non-negative integer")
    rows = {str(row["id"]): row for row in routing.get("lenses") or []}
    context = routing.get("context") or {}
    policy = context.get("review_depth_policy")
    if context.get("automatic_review"):
        ordered = list((context.get("review_progression") or {}).get(
            "sweep_lenses") or [])
        selected = [lens_id for lens_id in ordered
                    if lens_id in rows and rows[lens_id].get("tier") in {
                        "sweep", "light"}]
        if not 4 <= len(selected) <= 5 or "architecture" not in selected:
            raise ValueError(
                "automatic review requires 4–5 sweep workers including architecture")
        dispatch_set = {
            "schema": "taskplane.dispatch-set/v1",
            "id": "automatic-review-sweep", "concurrent": True,
            "member_count": len(selected),
        }
        wait_policy = {
            "schema": "taskplane.wait-policy/v1",
            "outstanding_set": dispatch_set["id"],
            "outstanding_count": len(selected), "mode": "event",
            "timeout_seconds": 1800, "minimum_timeout_seconds": 300,
            "reissue_after": ["completion", "attention"],
            "scheduled_polling": False,
        }
        return {
            "schema": "taskplane.review-progression/v1",
            "deep": [],
            "sweep": [
                {"slot": f"lens-{lens_id}", "lens": lens_id,
                 "tier": "sweep", "dispatch_set": copy.deepcopy(dispatch_set)}
                for lens_id in selected
            ],
            "sweep_count": len(selected),
            "deferred_light": [],
            "dispatch_set": dispatch_set,
            "wait_policy": wait_policy,
            "review_depth_policy": copy.deepcopy(policy),
        }
    if _quick_only(policy):
        light_ids = sorted(
            lens_id for lens_id, row in rows.items()
            if row.get("tier") in {
                "deep", "deep (forced)", "light", "sweep"})
        return {
            "schema": "taskplane.review-progression/v1",
            "deep": [],
            "sweep": (
                {"slot": "lens-sweep", "tier": "light",
                 "lenses": light_ids}
                if light_ids else None
            ),
            "sweep_count": 1 if light_ids else 0,
            "deferred_light": [],
            "review_depth_policy": copy.deepcopy(policy),
        }
    risk = routing.get("context", {}).get("review_risk") or {}
    required = tuple(risk.get("required_deep_lenses") or ())
    if not required:
        required = tuple(
            lens_id for lens_id, row in rows.items()
            if row.get("review_required_deep")
        ) or MANDATORY_DEEP_FLOORS
    missing = sorted(set(required) - set(rows))
    if missing:
        raise ValueError("routing missing mandatory review floor(s): " + ", ".join(missing))
    deep_ids = sorted(
        lens_id for lens_id, row in rows.items()
        if row.get("tier") == "deep" or lens_id in required
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


def resolve_sweep_concerns(concerns, *, already_promoted=(),
                           review_policy: dict | None = None) -> dict:
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
    corrections = []
    rejections = []
    for raw in concerns or []:
        row = dict(raw) if isinstance(raw, dict) else {}
        concern_id = str(row.get("id") or "").strip()
        severity = str(row.get("severity") or "").strip().lower()
        lens_id = str(row.get("lens") or "").strip()
        evidence_ref = str(row.get("evidence_ref") or "").strip()
        rationale = str(row.get("rationale") or "").strip()
        trigger = str(row.get("trigger") or "").strip()
        finding_class = str(row.get("class") or "").strip().lower()
        fingerprint = _concern_fingerprint(row)
        reason = None
        if fingerprint in seen or (concern_id and concern_id in seen):
            reason = "duplicate"
        elif severity not in PROMOTION_SEVERITIES and not (
                _quick_only(review_policy) and finding_class == "regression"):
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
        if _quick_only(review_policy):
            corrections.append({
                "concern_id": concern_id,
                "lens": lens_id,
                "slot": "lens-sweep",
                "tier": "light",
                "severity": severity,
                "class": finding_class,
                "evidence_ref": evidence_ref,
                "rationale": rationale,
                "trigger": trigger,
                "fingerprint": fingerprint,
                "action": "return-same-task-for-correction",
                "deep_dispatch": False,
            })
            continue
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
        "corrections": corrections,
        "rejections": rejections,
        "outcome": ("correction_required" if corrections else "continue"),
        **({"review_depth_policy": copy.deepcopy(review_policy)}
           if isinstance(review_policy, dict) else {}),
    }
