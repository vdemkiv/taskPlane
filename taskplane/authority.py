"""Consolidated, attributable authority for governed delivery.

The packet is deliberately a pure value.  Hosts may present it differently,
but a stage derives authority only from the approved semantic envelope and an
authenticated actor/thread-bound receipt.  Fingerprint drift is evidence
staleness; it is never, by itself, a request for more human authority.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence


PACKET_SCHEMA = "taskplane.consolidated-authorization/v1"
RECEIPT_SCHEMA = "taskplane.authorization-receipt/v1"
DERIVATION_SCHEMA = "taskplane.authorization-derivation/v1"

REQUIRED_FIELDS = (
    "requirement", "acceptance", "target", "scope", "contracts", "design",
    "plan", "dynamic_validation", "sandbox", "recovery", "evaluation",
    "artifact_delivery", "execution_bounds",
)

HUMAN_BOUNDARIES = frozenset({
    "initial_authorization", "ab_selection", "recovery_exhausted",
    "replan", "material_scope", "major_authority_change", "destructive",
    "irreversible", "external_system", "credential", "external_publication",
    "spend", "acceptance_changed", "gate_weakening", "final_signoff",
    "unsafe_or_ambiguous",
})

_MATERIAL_FIELDS = (
    "requirement", "acceptance", "target", "scope", "contracts",
    "dynamic_validation", "sandbox", "recovery", "evaluation",
    "artifact_delivery", "execution_bounds",
)
_REASON_BY_FIELD = {
    "requirement": "requirement_changed",
    "acceptance": "acceptance_changed",
    "target": "target_changed",
    "scope": "scope_changed",
    "contracts": "contract_meaning_changed",
    "dynamic_validation": "validation_authority_changed",
    "sandbox": "sandbox_authority_changed",
    "recovery": "recovery_policy_changed",
    "evaluation": "evaluation_authority_changed",
    "artifact_delivery": "delivery_authority_changed",
    "execution_bounds": "authority_changed",
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _plain(value: object) -> object:
    """Return a JSON value and reject host objects at the trust boundary."""
    try:
        return json.loads(_canonical_bytes(value).decode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError("authorization values must be JSON serializable") from exc


def _without_format_metadata(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _without_format_metadata(item)
            for key, item in value.items()
            if str(key) not in {
                "format_fingerprint", "content_fingerprint", "rendered_sha256",
                "generated_at", "updated_at",
            }
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_without_format_metadata(item) for item in value]
    return value


def create_packet(fields: Mapping) -> dict:
    """Seal the complete preimplementation authority envelope."""
    if not isinstance(fields, Mapping):
        raise ValueError("authorization packet must be a mapping")
    missing = [field for field in REQUIRED_FIELDS if field not in fields]
    if missing:
        raise ValueError("authorization packet missing: " + ", ".join(missing))
    payload = {
        "schema": PACKET_SCHEMA,
        "authority": {field: _plain(fields[field]) for field in REQUIRED_FIELDS},
    }
    payload["fingerprint"] = _fingerprint(payload)
    return payload


def approve(packet: Mapping, *, actor: str, thread: str,
            authenticated: bool) -> dict:
    """Issue one immutable receipt; missing identity never implies consent."""
    _validate_packet(packet)
    actor_value, thread_value = str(actor or "").strip(), str(thread or "").strip()
    if not authenticated or not actor_value or not thread_value:
        raise ValueError("authenticated actor and thread are required")
    payload = {
        "schema": RECEIPT_SCHEMA,
        "packet_fingerprint": packet["fingerprint"],
        "actor": actor_value,
        "thread": thread_value,
        "authenticated": True,
        "decision": "approve",
    }
    payload["fingerprint"] = _fingerprint(payload)
    return payload


def _validate_packet(packet: Mapping) -> None:
    if not isinstance(packet, Mapping) or packet.get("schema") != PACKET_SCHEMA:
        raise ValueError("unsupported authorization packet")
    expected = _fingerprint({key: value for key, value in packet.items()
                             if key != "fingerprint"})
    if packet.get("fingerprint") != expected:
        raise ValueError("authorization packet fingerprint mismatch")


def _receipt_reasons(packet: Mapping, receipt: Mapping, *, actor: str,
                     thread: str) -> list[str]:
    reasons: list[str] = []
    expected = _fingerprint({key: value for key, value in receipt.items()
                             if key != "fingerprint"})
    if receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("fingerprint") != expected:
        reasons.append("invalid_receipt")
    if receipt.get("packet_fingerprint") != packet.get("fingerprint"):
        reasons.append("stale_or_replayed_receipt")
    if not receipt.get("authenticated"):
        reasons.append("unauthenticated")
    if str(receipt.get("actor") or "") != str(actor or ""):
        reasons.append("wrong_actor")
    if str(receipt.get("thread") or "") != str(thread or ""):
        reasons.append("wrong_thread")
    if receipt.get("decision") != "approve":
        reasons.append("not_approved")
    return sorted(set(reasons))


def classify_evolution(approved: Mapping, current: Mapping) -> dict:
    """Classify semantic evolution without promoting byte drift to authority."""
    missing = [field for field in REQUIRED_FIELDS if field not in current]
    if missing:
        return {"classification": "unsafe-or-ambiguous",
                "reasons": ["current_authority_incomplete"]}
    reasons = [
        _REASON_BY_FIELD[field]
        for field in _MATERIAL_FIELDS
        if _without_format_metadata(approved.get(field)) !=
        _without_format_metadata(current.get(field))
    ]
    if reasons:
        return {"classification": "material-contract",
                "reasons": sorted(set(reasons))}
    approved_how = {key: _without_format_metadata(approved.get(key))
                    for key in ("design", "plan")}
    current_how = {key: _without_format_metadata(current.get(key))
                   for key in ("design", "plan")}
    if approved_how != current_how:
        return {"classification": "non-material", "reasons": []}
    if _plain(approved) != _plain(current):
        return {"classification": "byte-only", "reasons": []}
    return {"classification": "unchanged", "reasons": []}


def derive(packet: Mapping, receipt: Mapping, *, stage: str, current: Mapping,
           actor: str, thread: str) -> dict:
    """Derive stage authority, failing closed on identity or semantic drift."""
    _validate_packet(packet)
    identity_reasons = _receipt_reasons(
        packet, receipt, actor=actor, thread=thread)
    evolution = classify_evolution(packet["authority"], current)
    reasons = sorted(set(identity_reasons + evolution["reasons"]))
    authorized = not identity_reasons and evolution["classification"] in {
        "unchanged", "byte-only", "non-material",
    }
    payload = {
        "schema": DERIVATION_SCHEMA,
        "stage": str(stage or "").strip(),
        "packet_fingerprint": packet["fingerprint"],
        "receipt_fingerprint": receipt.get("fingerprint"),
        "evolution": evolution["classification"],
        "authorized": authorized,
        "reasons": reasons,
    }
    payload["fingerprint"] = _fingerprint(payload)
    return payload


def human_boundary(reason: str, *, fact: str = "",
                   consequence: str = "") -> dict:
    """Return the closed human-attention decision for a new fact."""
    normalized = str(reason or "").strip().lower().replace("-", "_")
    if normalized not in HUMAN_BOUNDARIES:
        return {"human_required": False, "reason": normalized}
    return {
        "human_required": True,
        "reason": normalized,
        "new_fact": str(fact or "").strip(),
        "consequence": str(consequence or "").strip(),
        "authority_requested": normalized,
    }
