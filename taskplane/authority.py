"""Consolidated, attributable authority for governed delivery.

The packet is deliberately a pure value.  Hosts may present it differently,
but a stage derives authority only from the approved semantic envelope and an
authenticated actor/thread-bound receipt.  Fingerprint drift is evidence
staleness; it is never, by itself, a request for more human authority.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence


PACKET_SCHEMA = "taskplane.consolidated-authorization/v1"
RECEIPT_SCHEMA = "taskplane.authorization-receipt/v1"
DERIVATION_SCHEMA = "taskplane.authorization-derivation/v1"
DECISION_SCHEMA = "taskplane.human-decision/v1"
CHANGE_SCHEMA = "taskplane.attributable-change/v1"
HOST_INPUT_RECEIPT_SCHEMA = "taskplane.host-input-receipt/v1"

ROUTINE_FLOWS = (
    "facade", "delivery", "product", "design", "build", "engineering",
    "status", "help", "north_star", "tag_slack",
)

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


def _host_event_payload(event: Mapping) -> dict:
    """Canonical non-authority event body bound by a trusted host receipt."""
    allowed = ("type", "text", "change_kind", "reason", "response", "fact",
               "consequence")
    payload = {key: _plain(event[key]) for key in allowed if key in event}
    response = payload.get("response")
    if isinstance(response, dict):
        response.pop("authenticated", None)
    return payload


_HOST_RECEIPT_RSA_N = int(
    "2755070703043514567837450338864977453983335217863375313393347508867998"
    "8653606130385459860851556870701927819635677964252617671428447820199573"
    "5891145625961267505292290879320152248587133816351974857532220613567616"
    "4442576590974819685291497203942545700267055896278182985594923471809590"
    "1348806834409386751189663841627023762916628072701707507494690688456557"
    "6020485684414216971381181957259990629280647142016116688515243136515996"
    "9619352225257173199336520417967382269653847072571466571530171225163005"
    "2133153064061413150938446572227240517779658235163179907488568403155154"
    "731860395743276319730653002263913812036871800261560339749")
_HOST_RECEIPT_RSA_E = 65537
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _host_receipt_signature_valid(receipt: Mapping) -> bool:
    """Verify a host-issued RSA/SHA-256 receipt; no signing API exists here."""
    try:
        signature = bytes.fromhex(str(receipt.get("signature") or ""))
    except ValueError:
        return False
    size = (_HOST_RECEIPT_RSA_N.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    signature_value = int.from_bytes(signature, "big")
    if signature_value >= _HOST_RECEIPT_RSA_N:
        return False
    value = pow(signature_value, _HOST_RECEIPT_RSA_E,
                _HOST_RECEIPT_RSA_N).to_bytes(size, "big")
    unsigned = {key: receipt[key] for key in receipt if key != "signature"}
    digest = hashlib.sha256(_canonical_bytes(unsigned)).digest()
    tail = _SHA256_DIGEST_INFO + digest
    expected = b"\x00\x01" + b"\xff" * (size - len(tail) - 3) + b"\x00" + tail
    return hmac.compare_digest(value, expected)


class HostInputVerifier:
    """Consume-only verifier for receipts signed by the privileged host."""

    def verify(self, event: Mapping, receipt: object) -> dict:
        reasons = []
        if not isinstance(receipt, Mapping) or \
                receipt.get("schema") != HOST_INPUT_RECEIPT_SCHEMA:
            return {"authenticated": False,
                    "reasons": ["host_receipt_required"]}
        if not _host_receipt_signature_valid(receipt):
            reasons.append("host_receipt_unauthenticated")
        if receipt.get("event_fingerprint") != _fingerprint(
                _host_event_payload(event)):
            reasons.append("host_event_mismatch")
        for field in ("actor", "thread", "revision", "event_id"):
            if not str(receipt.get(field) or "").strip():
                reasons.append(f"host_receipt_missing_{field}")
        if receipt.get("authenticated") is not True:
            reasons.append("host_receipt_unauthenticated")
        return {
            "authenticated": not reasons, "reasons": sorted(set(reasons)),
            "actor": str(receipt.get("actor") or ""),
            "thread": str(receipt.get("thread") or ""),
            "revision": str(receipt.get("revision") or ""),
            "event_id": str(receipt.get("event_id") or ""),
        }


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


def mechanical_definition_gate(stage: str, evidence: Mapping) -> dict:
    """Return the named mechanical outcome for Product, Design, or Plan.

    A missing fact blocks the stage; it never gets converted into a request
    for ceremonial human approval.
    """
    normalized = str(stage or "").strip().lower()
    required = {
        "product": ("requirement", "acceptance", "contracts",
                    "dependencies", "nfrs", "score"),
        "design": ("contracts", "graph", "acceptance_mapping", "lenses"),
        "plan": ("contracts", "graph", "acceptance_mapping", "tasks"),
    }
    if normalized not in required:
        raise ValueError("unsupported mechanical definition gate")
    missing = [name for name in required[normalized]
               if not evidence.get(name)]
    return {
        "stage": normalized,
        "passed": not missing,
        "human_required": False,
        "blocker": None if not missing else
        f"{normalized}_evidence_incomplete:" + ",".join(missing),
    }


def routine_flow_trace(packet: Mapping, receipt: Mapping, *, current: Mapping,
                       actor: str, thread: str) -> dict:
    """Prove the same receipt derives unchanged routine work for every flow."""
    stages = {}
    for flow in ROUTINE_FLOWS:
        stages[flow] = derive(packet, receipt, stage=flow, current=current,
                              actor=actor, thread=thread)
    return {
        "receipt": receipt.get("fingerprint"),
        "stages": stages,
        "authorized": all(row["authorized"] for row in stages.values()),
    }


def decision_input(reason: str, response: object, *, fact: str,
                   consequence: str, actor: str, thread: str,
                   revision: str, expected_actor: str,
                   expected_thread: str, expected_revision: str,
                   consumed: bool = False) -> dict:
    """Validate a human-owned decision; absence and prose never authorize.

    Hosts must pass a structured response with an explicit decision. This
    makes timeout, free-form ambiguity, stale revisions and replay ordinary
    fail-closed states rather than inferred consent.
    """
    boundary = human_boundary(reason, fact=fact, consequence=consequence)
    reasons = []
    if not boundary["human_required"]:
        return {"schema": DECISION_SCHEMA, "authorized": True,
                "human_required": False, "reasons": []}
    if not isinstance(response, Mapping):
        reasons.append("missing_or_ambiguous_response")
        response = {}
    if response.get("decision") != "approve":
        reasons.append("not_approved")
    if not response.get("authenticated"):
        reasons.append("unauthenticated")
    if not actor or actor != expected_actor:
        reasons.append("wrong_actor")
    if not thread or thread != expected_thread:
        reasons.append("wrong_thread")
    if not revision or revision != expected_revision:
        reasons.append("wrong_revision")
    if consumed:
        reasons.append("replayed_decision")
    return {
        "schema": DECISION_SCHEMA,
        **boundary,
        "authorized": not reasons,
        "reasons": sorted(set(reasons)),
        "actor": actor,
        "thread": thread,
        "revision": revision,
    }


def build_selection(variants: Sequence[Mapping], *, selected: str | None,
                    revision: str, expected_revision: str) -> dict:
    """Conserve the sole ordinary mid-build gate for explicit A/B work."""
    rows = [dict(row) for row in variants]
    if len(rows) <= 1:
        return {"human_required": False, "authorized": True,
                "selected": rows[0].get("id") if rows else None}
    ids = {str(value) for row in rows
           for value in (row.get("id"), row.get("variant")) if value}
    reasons = []
    if revision != expected_revision:
        reasons.append("stale_selection")
    if not selected or selected not in ids:
        reasons.append("invalid_selection")
    return {"human_required": True, "reason": "ab_selection",
            "authorized": not reasons, "selected": selected,
            "reasons": reasons}


def preview_change(text: str, *, actor: str, authenticated: bool,
                   requirement: str, target: Mapping, kind: str) -> dict:
    """Record preview feedback as an attributable scoped change request."""
    normalized = str(kind or "").strip().lower().replace("-", "_")
    material = normalized in {"acceptance", "scope", "authority"}
    reasons = []
    if not authenticated or not str(actor or "").strip():
        reasons.append("unauthenticated")
    if not str(text or "").strip():
        reasons.append("feedback_missing")
    payload = {
        "schema": CHANGE_SCHEMA, "actor": str(actor or "").strip(),
        "requirement": str(requirement or "").strip(),
        "target": _plain(target), "kind": normalized,
        "feedback": str(text or "").strip(), "material": material,
        "reauthorization_required": material, "accepted": not reasons,
        "reasons": reasons,
    }
    payload["fingerprint"] = _fingerprint(payload)
    return payload
