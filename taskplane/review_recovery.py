"""Equivalence-bounded recovery for governed review slot results.

Recovery is deliberately narrower than validation.  It may fill canonical
schema/provenance values from a sealed lease and replace a free-form
declaration label with the identity of an equivalent declaration from the
sealed DoR authority.  It never edits a finding claim, checked evidence,
verdict, target, producer, or slot.  Anything outside that allowlist is sent
back only to the affected producer.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable, Mapping


RESULT_SCHEMA = "taskplane.lens-slot-output/v2"
AUDIT_SCHEMA = "taskplane.review-repair-audit/v1"
RETRY_SCHEMA = "taskplane.review-retry-plan/v1"
REPAIR_RULE = "lease-derived-metadata/v1"
MAX_RETRY_ATTEMPTS = 2

_LEASE_FIELDS = (
    "lease_fingerprint", "slot_id", "lens_ids", "target_fingerprint",
    "context_fingerprint", "view_fingerprint", "canonical_revision",
)
_OPTIONAL_LEASE_FIELDS = (
    "reference_manifest_fingerprint", "routing_fingerprint", "producer",
)
_REPAIRABLE_TOP_LEVEL = frozenset({
    "schema", "authored_by", *_LEASE_FIELDS, *_OPTIONAL_LEASE_FIELDS,
})


class RepairRejected(ValueError):
    """The proposed repair cannot be proven mechanically equivalent."""


def _canonical_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode(
                           "utf-8")


def _fingerprint(value) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _summary_equivalence_projection(result: Mapping) -> dict:
    """Project producer substance while excluding derivable summaries."""
    projected = copy.deepcopy(dict(result))
    rows = projected.get("lens_results")
    if not isinstance(rows, list):
        raise RepairRejected("slot result lens_results must be a list")
    for row in rows:
        if not isinstance(row, dict):
            raise RepairRejected("slot result lens verdict is invalid")
        row.pop("verdict", None)
        row.pop("blockers", None)
    return projected


def _declaration_words(value: object) -> tuple[str, ...]:
    """Normalize punctuation/formatting without guessing semantic aliases."""
    return tuple(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _declaration_index(declarations: Iterable[Mapping]) -> dict:
    by_id, by_words = {}, {}
    for raw in declarations or ():
        if not isinstance(raw, Mapping):
            raise RepairRejected("declaration authority contains a non-object")
        identity = str(raw.get("id") or "").strip()
        text = str(raw.get("text") or "").strip()
        source = str(raw.get("source_fingerprint") or "").strip()
        if not identity or not text or not source:
            raise RepairRejected(
                "declaration authority requires id, text, and source fingerprint")
        if identity in by_id and dict(raw) != by_id[identity]:
            raise RepairRejected("declaration identity is ambiguous: " + identity)
        by_id[identity] = dict(raw)
        aliases = raw.get("aliases") or []
        if isinstance(aliases, (str, bytes)) or not isinstance(aliases, list):
            raise RepairRejected("declaration aliases must be a list")
        candidates = [text, *aliases]
        for candidate in candidates:
            words = _declaration_words(candidate)
            if not words:
                continue
            existing = by_words.get(words)
            if existing is not None and existing["id"] != identity:
                raise RepairRejected("declaration text is ambiguous")
            by_words[words] = dict(raw)
    return {"by_id": by_id, "by_words": by_words}


def _resolve_declaration(value: object, authority: dict) -> dict:
    identity = str(value or "").strip()
    if not identity:
        raise RepairRejected("declaration identity is missing")
    if identity in authority["by_id"]:
        return authority["by_id"][identity]
    declaration = authority["by_words"].get(_declaration_words(identity))
    if declaration is None:
        raise RepairRejected("free-form declaration identity is unverifiable")
    return declaration


def _equivalence_projection(result: Mapping, authority: dict) -> dict:
    """Project only review substance, canonicalizing declaration labels."""
    projected = copy.deepcopy(dict(result))
    for key in _REPAIRABLE_TOP_LEVEL:
        projected.pop(key, None)
    findings = projected.get("findings")
    if not isinstance(findings, list):
        raise RepairRejected("findings must be a list")
    for finding in findings:
        if not isinstance(finding, dict):
            raise RepairRejected("finding must be an object")
        if "declares" not in finding:
            continue
        declaration = _resolve_declaration(finding.get("declares"), authority)
        finding["declares"] = declaration["id"]
    return projected


def _expected_metadata(lease: Mapping) -> dict:
    if not isinstance(lease, Mapping) or lease.get("schema") != \
            "taskplane.slot-lease/v1":
        raise RepairRejected("sealed slot lease is invalid")
    expected = {"schema": RESULT_SCHEMA, "authored_by": "lens-slot"}
    for field in _LEASE_FIELDS:
        if field not in lease:
            raise RepairRejected("sealed lease is missing " + field)
        expected[field] = copy.deepcopy(lease[field])
    return expected


def _audit(*, before: Mapping, after: Mapping, changes: list[dict],
           authority: dict, actor: str, status: str) -> dict:
    before_projection = _equivalence_projection(before, authority)
    after_projection = _equivalence_projection(after, authority)
    before_equivalence = _fingerprint(before_projection)
    after_equivalence = _fingerprint(after_projection)
    if before_equivalence != after_equivalence:
        raise RepairRejected("repair changes review substance")
    before_bytes, after_bytes = _canonical_bytes(before), _canonical_bytes(after)
    return {
        "schema": AUDIT_SCHEMA,
        "status": status,
        "rule": REPAIR_RULE,
        "actor": actor,
        "slot_id": str(after.get("slot_id") or before.get("slot_id") or ""),
        "before_fingerprint": hashlib.sha256(before_bytes).hexdigest(),
        "after_fingerprint": hashlib.sha256(after_bytes).hexdigest(),
        "before_bytes": len(before_bytes),
        "after_bytes": len(after_bytes),
        "equivalence_fingerprint_before": before_equivalence,
        "equivalence_fingerprint_after": after_equivalence,
        "changes": changes,
    }


def repair_slot_result(result: Mapping, lease: Mapping, *, declarations,
                       actor: str = "review-kernel",
                       expected_result: Mapping | None = None) -> dict:
    """Return an audited equivalent result or raise :class:`RepairRejected`.

    ``expected_result`` is an optional previously observed value.  When
    supplied it protects against a caller presenting changed findings or
    evidence as a metadata repair.  The sealed lease is always authoritative
    for identity fields; conflicting values are rejected rather than fixed.
    """
    if not isinstance(result, Mapping):
        raise RepairRejected("slot result must be an object")
    if not str(actor or "").strip():
        raise RepairRejected("repair actor is required")
    authority = _declaration_index(declarations)
    expected_metadata = _expected_metadata(lease)
    before = copy.deepcopy(dict(result))

    if expected_result is not None:
        if not isinstance(expected_result, Mapping):
            raise RepairRejected("expected slot result must be an object")
        observed_projection = _equivalence_projection(before, authority)
        expected_projection = _equivalence_projection(
            expected_result, authority)
        if observed_projection != expected_projection:
            raise RepairRejected("findings or evidence differ from observed result")

    after = copy.deepcopy(before)
    changes: list[dict] = []
    # These identities belong to canonical lease/result envelopes rather than
    # the strict producer output schema.  When a transport supplies one, it
    # must already agree with the lease; recovery never inserts or changes it.
    for field in _OPTIONAL_LEASE_FIELDS:
        if field in after and after.get(field) != lease.get(field):
            raise RepairRejected(field + " conflicts with sealed lease authority")
    for field, value in expected_metadata.items():
        if field in after:
            if after[field] != value:
                raise RepairRejected(field + " conflicts with sealed lease authority")
            continue
        after[field] = copy.deepcopy(value)
        changes.append({
            "path": field, "before": None, "after": copy.deepcopy(value),
            "derived_from": "sealed-lease",
        })

    findings = after.get("findings")
    if not isinstance(findings, list):
        raise RepairRejected("findings must be a list")
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise RepairRejected("finding must be an object")
        if "declares" not in finding:
            continue
        declaration = _resolve_declaration(finding["declares"], authority)
        canonical = declaration["id"]
        if finding["declares"] == canonical:
            continue
        previous = finding["declares"]
        finding["declares"] = canonical
        changes.append({
            "path": f"findings[{index}].declares",
            "before": previous,
            "after": canonical,
            "derived_from": (
                f"declaration:{canonical}@"
                f"{declaration['source_fingerprint']}")
        })

    audit = _audit(
        before=before, after=after, changes=changes, authority=authority,
        actor=str(actor).strip(), status="repaired" if changes else "unchanged")
    return {
        "schema": "taskplane.review-repair-result/v1",
        "status": "repaired" if changes else "unchanged",
        "producer_rerun_required": False,
        "affected_slot_ids": [],
        "result": after,
        "audit": audit,
    }


def attempt_slot_repair(result: Mapping, lease: Mapping, *, declarations,
                        actor: str = "review-kernel",
                        expected_result: Mapping | None = None) -> dict:
    """Non-throwing recovery boundary used by the retry scheduler."""
    slot_id = str(lease.get("slot_id") or "") if isinstance(lease, Mapping) \
        else ""
    try:
        return repair_slot_result(
            result, lease, declarations=declarations, actor=actor,
            expected_result=expected_result)
    except RepairRejected as exc:
        reason = str(exc)
        return {
            "schema": "taskplane.review-repair-result/v1",
            "status": "rejected",
            "producer_rerun_required": True,
            "affected_slot_ids": [slot_id] if slot_id else [],
            "result": None,
            "audit": {
                "schema": AUDIT_SCHEMA,
                "status": "rejected",
                "rule": REPAIR_RULE,
                "actor": str(actor or "review-kernel"),
                "slot_id": slot_id,
                "reason": reason,
                "before_fingerprint": (
                    _fingerprint(result) if isinstance(result, Mapping) else None),
                "after_fingerprint": None,
                "changes": [],
            },
        }


def plan_affected_retries(leases: Iterable[Mapping], *, valid_results: Mapping,
                          failures: Iterable[Mapping], attempts: Mapping,
                          max_attempts: int = MAX_RETRY_ATTEMPTS) -> dict:
    """Create a deterministic manifest that calls only failed producers."""
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or \
            max_attempts < 1:
        raise RepairRejected("retry limit must be a positive integer")
    lease_by_slot = {}
    for raw in leases or ():
        if not isinstance(raw, Mapping):
            raise RepairRejected("retry lease must be an object")
        slot_id = str(raw.get("slot_id") or "").strip()
        if not slot_id or slot_id in lease_by_slot:
            raise RepairRejected("retry leases require unique slot identities")
        _expected_metadata(raw)
        lease_by_slot[slot_id] = copy.deepcopy(dict(raw))
    valid = copy.deepcopy(dict(valid_results or {}))
    unknown_valid = set(valid) - set(lease_by_slot)
    if unknown_valid:
        raise RepairRejected("valid result cites unknown slot")
    failed = {}
    for raw in failures or ():
        if not isinstance(raw, Mapping):
            raise RepairRejected("retry failure must be an object")
        slot_id = str(raw.get("slot_id") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if slot_id not in lease_by_slot or not reason:
            raise RepairRejected("retry failure requires a known slot and reason")
        if slot_id in valid:
            raise RepairRejected("valid slot cannot be scheduled for retry")
        prior = failed.get(slot_id)
        if prior is not None and prior != reason:
            raise RepairRejected("retry failure is ambiguous for slot: " + slot_id)
        failed[slot_id] = reason

    producer_calls, exhausted = [], []
    for slot_id in sorted(failed):
        attempt = attempts.get(slot_id, 0) if isinstance(attempts, Mapping) else 0
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
            raise RepairRejected("retry attempt is invalid for slot: " + slot_id)
        if attempt >= max_attempts:
            exhausted.append(slot_id)
            continue
        producer_calls.append({
            "slot_id": slot_id,
            "lease_fingerprint": lease_by_slot[slot_id]["lease_fingerprint"],
            "attempt": attempt + 1,
            "reason": failed[slot_id],
        })
    affected = sorted(failed)
    status = "unavailable" if exhausted else "retry" if producer_calls else \
        "complete"
    material = {
        "affected_slot_ids": affected,
        "producer_calls": producer_calls,
        "reused_results": valid,
        "exhausted_slot_ids": exhausted,
    }
    return {
        "schema": RETRY_SCHEMA,
        "status": status,
        **material,
        "reused_result_count": len(valid),
        "audit": {
            "schema": "taskplane.review-retry-audit/v1",
            "affected_slot_ids": affected,
            "producer_call_count": len(producer_calls),
            "reused_result_count": len(valid),
            "exhausted_slot_ids": exhausted,
            "plan_fingerprint": _fingerprint(material),
        },
    }


def merge_findings_once(results: Iterable[Mapping]) -> list[dict]:
    """Merge retry output without replaying a result or duplicating a row."""
    seen_results, seen_findings, merged = {}, set(), []
    for result in results or ():
        if not isinstance(result, Mapping):
            raise RepairRejected("result must be an object")
        result_fp = str(result.get("result_fingerprint") or "").strip()
        if not result_fp:
            raise RepairRejected("result fingerprint is required")
        findings = result.get("findings")
        if not isinstance(findings, list):
            raise RepairRejected("result findings must be a list")
        material_fp = _fingerprint(dict(result))
        if result_fp in seen_results:
            if seen_results[result_fp] != material_fp:
                raise RepairRejected(
                    "result fingerprint was replayed with different content")
            continue
        seen_results[result_fp] = material_fp
        for finding in findings:
            if not isinstance(finding, Mapping):
                raise RepairRejected("finding must be an object")
            fingerprint = _fingerprint(finding)
            if fingerprint in seen_findings:
                continue
            seen_findings.add(fingerprint)
            merged.append(copy.deepcopy(dict(finding)))
    return merged


def recover_summary_or_plan_retry(
        result: Mapping, lease: Mapping, *, blocking_by_lens: Mapping,
        attempts: Mapping, valid_results: Mapping | None = None,
        leases: Iterable[Mapping] | None = None,
        actor: str = "review-kernel") -> dict:
    """Repair a purely derived summary, else rerun only its leased slot.

    ``blocking_by_lens`` is computed by the canonical collector *after*
    finding validation/adjudication.  This function does not reinterpret a
    finding.  It may only copy those authoritative counts into the producer's
    redundant summary fields.  Any malformed evidence, identity mismatch, or
    other ambiguity takes the bounded affected-slot retry path.
    """
    slot_id = str(lease.get("slot_id") or "") if isinstance(lease, Mapping) \
        else ""
    try:
        if not isinstance(result, Mapping):
            raise RepairRejected("slot result must be an object")
        expected = _expected_metadata(lease)
        before = copy.deepcopy(dict(result))
        for field, value in expected.items():
            if before.get(field) != value:
                raise RepairRejected(field + " conflicts with sealed lease authority")
        rows = before.get("lens_results")
        if not isinstance(rows, list):
            raise RepairRejected("slot result lens_results must be a list")
        by_lens = {}
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise RepairRejected("slot result lens verdict is invalid")
            lens_id = str(row.get("lens") or "").strip()
            if lens_id in by_lens or lens_id not in lease.get("lens_ids", []):
                raise RepairRejected("slot result lens verdict is invalid")
            if row.get("verdict") not in {"pass", "fail"} or \
                    isinstance(row.get("blockers"), bool) or not isinstance(
                        row.get("blockers"), int) or row["blockers"] < 0 or \
                    not isinstance(row.get("checked_evidence"), list):
                raise RepairRejected("slot result lens verdict is invalid")
            by_lens[lens_id] = index
        if set(by_lens) != set(lease.get("lens_ids") or []):
            raise RepairRejected("slot result does not cover its leased lenses")
        unknown = set(blocking_by_lens or {}) - set(by_lens)
        if unknown:
            raise RepairRejected("blocking summary cites an unleased lens")

        after = copy.deepcopy(before)
        changes = []
        for lens_id in lease["lens_ids"]:
            count = (blocking_by_lens or {}).get(lens_id, 0)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise RepairRejected("canonical blocker count is invalid")
            index = by_lens[lens_id]
            expected_verdict = "fail" if count else "pass"
            for field, value in (("blockers", count),
                                 ("verdict", expected_verdict)):
                prior = after["lens_results"][index][field]
                if prior == value:
                    continue
                after["lens_results"][index][field] = value
                changes.append({
                    "path": f"lens_results[{index}].{field}",
                    "before": prior, "after": value,
                    "derived_from": "canonical-blocking-findings",
                })
        import review_evidence
        review_evidence.assert_summary_only_repair(before, after)
        equivalence_before = _fingerprint(
            _summary_equivalence_projection(before))
        equivalence_after = _fingerprint(
            _summary_equivalence_projection(after))
        if equivalence_before != equivalence_after:
            raise RepairRejected("summary repair changes review substance")
        material = {
            "slot_id": slot_id,
            "before_fingerprint": _fingerprint(before),
            "after_fingerprint": _fingerprint(after),
            "equivalence_fingerprint_before": equivalence_before,
            "equivalence_fingerprint_after": equivalence_after,
            "changes": changes,
        }
        return {
            "schema": "taskplane.review-summary-recovery/v1",
            "status": "repaired" if changes else "unchanged",
            "producer_rerun_required": False,
            "affected_slot_ids": [],
            "result": after,
            "audit": {
                "schema": "taskplane.review-summary-repair-audit/v1",
                "actor": str(actor or "review-kernel"), **material,
                "repair_fingerprint": _fingerprint(material),
            },
        }
    except (RepairRejected, ValueError) as exc:
        retry_leases = list(leases) if leases is not None else [lease]
        plan = plan_affected_retries(
            retry_leases, valid_results=valid_results or {},
            failures=[{"slot_id": slot_id, "reason": str(exc)}],
            attempts=attempts)
        return {
            "schema": "taskplane.review-summary-recovery/v1",
            "status": plan["status"],
            "producer_rerun_required": plan["status"] == "retry",
            "affected_slot_ids": plan["affected_slot_ids"],
            "result": None,
            "retry_plan": plan,
            "audit": {
                "schema": "taskplane.review-summary-repair-audit/v1",
                "actor": str(actor or "review-kernel"), "slot_id": slot_id,
                "reason": str(exc), "changes": [],
            },
        }
