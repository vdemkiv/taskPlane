"""Bounded, delta-shaped projection for the ``loop next`` host surface.

This owner deliberately does not dispatch work.  It converts the incumbent
loop action into the small public payload consumed by the CLI/host adapters.
The transition adapters are responsible for supplying binding usage and for
honouring a projected human-scope-review stop before they dispatch.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

from taskplane import delivery_policy


DELTA_SCHEMA = "taskplane.loop-next-delta/v1"
REFUSAL_SCHEMA = "taskplane.loop-next-delta-refusal/v1"
REFERENCE_SCHEMA = "taskplane.content-reference/v1"
MEASUREMENT_SCHEMA = "taskplane.brief-measurement/v1"
STAGE_DELTA_SCHEMA = "taskplane.stage-delta-handoff/v1"

# The projection uses a deliberately conservative upper bound: one UTF-8 byte
# counts as one token.  Passing this bound therefore cannot hide a larger
# model-token payload, and requires no host-specific tokenizer dependency.
MAX_TOKEN_UPPER_BOUND = 4_000

STAGE_DELTA_FIELDS = frozenset({
    "schema", "source_sha", "requirement_id", "active_contracts",
    "acceptance_outcomes", "new_evidence", "unresolved_decisions",
    "outstanding_native_set", "observed_usage",
    "predecessor_fingerprint", "fingerprint",
})
_HANDOFF_USAGE_FIELDS = frozenset({
    "elapsed_seconds", "unique_sessions", "total_tokens",
    "uncached_input_tokens",
})
_HANDOFF_REFERENCE_FIELDS = frozenset({
    "schema", "kind", "fingerprint", "bytes",
})

# These are the binding R-0001 ceilings.  This module only projects their
# consequence.  Dispatch admission remains owned by delivery_policy and
# dispatch_telemetry; loop/build adapters must refuse before invoking a host.
WAVE_BUDGET_CEILINGS = {
    "elapsed_seconds": 28_800,
    "sessions": 60,
    "total_tokens": 150_000_000,
    "uncached_input_tokens": 25_000_000,
}

_ACTION_FIELDS = (
    "step",
    "role",
    "task",
    "instruction",
    "contract",
    "dor",
    "paused",
    "awaiting",
    "error",
    "action",
    "wait_policy",
    "wait_invocation",
    "codex_dispatch",
    "parallel",
)

_WAVE_ACTION_FIELDS = (
    "role", "role_marker", "model", "model_tier", "reasoning_effort",
    "task_name", "task", "worktree", "merge_on_pass", "wait_policy",
)

# Values on these well-known surfaces are already durable loop/run facts.  A
# first projection references them directly; later projections retain a
# reference only while their canonical bytes remain unchanged.
_REFERENCE_FIELDS = frozenset(
    {
        "audit",
        "delivery_dispatch",
        "design",
        "design_graph",
        "dispatch_intent",
        "impact",
        "knowledge",
        "lenses",
        "requirement",
        "review_kernel",
        "runtime_evals",
        "stage_runtime_dispatch",
    }
)


class BriefProjectionError(delivery_policy.DeliveryPolicyError):
    """The loop action cannot be projected without weakening its contract."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BriefProjectionError(
            "loop next projection must be canonical-JSON serializable"
        ) from exc


def canonical_text(value: Any) -> str:
    """Return the exact UTF-8 canonical text used by references and bounds."""
    return _canonical_bytes(value).decode("utf-8")


def _closed_string_list(
        value: Any, label: str, *, pattern: str, max_length: int) -> list[str]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise BriefProjectionError(f"{label} must be a list")
    result = []
    for item in value:
        identity = str(item or "").strip()
        if len(identity) > max_length or not re.fullmatch(pattern, identity):
            raise BriefProjectionError(
                f"{label} must contain only bounded typed identities")
        result.append(identity)
    if not result or \
            len(set(result)) != len(result):
        raise BriefProjectionError(f"{label} must contain unique identities")
    return result


def _handoff_reference(
        value: Any, label: str, *, expected_kind: str) -> dict[str, Any]:
    """Validate one body-free, closed, content-addressed handoff reference."""
    if not isinstance(value, Mapping) or set(value) != \
            _HANDOFF_REFERENCE_FIELDS:
        raise BriefProjectionError(
            f"{label} must be a closed content-addressed reference")
    if value.get("schema") != REFERENCE_SCHEMA or \
            value.get("kind") != expected_kind:
        raise BriefProjectionError(
            f"{label} must be a typed {expected_kind} reference")
    fingerprint = str(value.get("fingerprint") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise BriefProjectionError(
            f"{label}.fingerprint must be one SHA-256 fingerprint")
    size = value.get("bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise BriefProjectionError(f"{label}.bytes must be non-negative")
    return {
        "schema": REFERENCE_SCHEMA,
        "kind": expected_kind,
        "fingerprint": fingerprint,
        "bytes": size,
    }


def _handoff_reference_list(
        value: Any, label: str, *, expected_kind: str) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise BriefProjectionError(f"{label} must be a list of references")
    result = [
        _handoff_reference(
            member, f"{label}[{index}]", expected_kind=expected_kind)
        for index, member in enumerate(value)
    ]
    fingerprints = [row["fingerprint"] for row in result]
    if len(fingerprints) != len(set(fingerprints)):
        raise BriefProjectionError(
            f"{label} cannot repeat a content fingerprint")
    return result


def _handoff_usage(value: Mapping[str, Any]) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        raise BriefProjectionError("observed_usage must be a mapping")
    material = dict(value)
    if "sessions" in material and "unique_sessions" not in material:
        material["unique_sessions"] = material.pop("sessions")
    if set(material) != _HANDOFF_USAGE_FIELDS:
        raise BriefProjectionError(
            "observed_usage requires exactly elapsed_seconds, unique_sessions, "
            "total_tokens, and uncached_input_tokens")
    normalized: dict[str, int | float] = {}
    for field in sorted(_HANDOFF_USAGE_FIELDS):
        observed = material[field]
        if isinstance(observed, bool) or not isinstance(observed, (int, float)):
            raise BriefProjectionError(f"observed_usage.{field} must be numeric")
        if isinstance(observed, float) and not math.isfinite(observed):
            raise BriefProjectionError(f"observed_usage.{field} must be finite")
        if observed < 0:
            raise BriefProjectionError(
                f"observed_usage.{field} cannot be negative")
        normalized[field] = observed
    return normalized


def stage_delta_handoff(
        *, source_sha: str, requirement_id: str,
        active_contracts: list[str] | tuple[str, ...],
        acceptance_outcomes: list[str] | tuple[str, ...],
        new_evidence: Any, unresolved_decisions: Any,
        outstanding_native_set: Any, observed_usage: Mapping[str, Any],
        predecessor_fingerprint: str | None) -> dict[str, Any]:
    """Build one closed stage-to-stage delta under the 4,000-token ceiling.

    The output contains exactly the Design-owned fields.  Variable stage
    material is representable only as closed content-addressed references, so
    transcripts, prompts, messages, model outputs, source, diffs, secrets, and
    personal-content bodies cannot enter the handoff under alternate keys.
    """
    sha = str(source_sha or "").strip().lower()
    requirement = str(requirement_id or "").strip().upper()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
        raise BriefProjectionError("source_sha must be one exact full Git SHA")
    if not re.fullmatch(r"R-[0-9]{4,12}", requirement):
        raise BriefProjectionError("requirement_id must be one typed requirement id")
    predecessor = None if predecessor_fingerprint is None else \
        str(predecessor_fingerprint).strip().lower()
    if predecessor is not None and not re.fullmatch(r"[0-9a-f]{64}", predecessor):
        raise BriefProjectionError(
            "predecessor_fingerprint must be one SHA-256 fingerprint")
    material = {
        "schema": STAGE_DELTA_SCHEMA,
        "source_sha": sha,
        "requirement_id": requirement,
        "active_contracts": _closed_string_list(
            active_contracts, "active_contracts",
            pattern=r"contract:[A-Za-z0-9][A-Za-z0-9_.:-]*",
            max_length=160),
        "acceptance_outcomes": _closed_string_list(
            acceptance_outcomes, "acceptance_outcomes",
            pattern=r"AC[0-9]+(?:[._:-][A-Za-z0-9]+)*",
            max_length=64),
        "new_evidence": _handoff_reference_list(
            new_evidence, "new_evidence", expected_kind="evidence"),
        "unresolved_decisions": _handoff_reference_list(
            unresolved_decisions, "unresolved_decisions",
            expected_kind="decision"),
        "outstanding_native_set": (
            None if outstanding_native_set is None else
            _handoff_reference(
                outstanding_native_set, "outstanding_native_set",
                expected_kind="native-set")
        ),
        "observed_usage": _handoff_usage(observed_usage),
        "predecessor_fingerprint": predecessor,
    }
    # Canonical serialization validates the closed typed material before the
    # fingerprint is issued.  No prompt, message, model output, transcript, or
    # other inline body has a representable field in this schema.
    material["fingerprint"] = hashlib.sha256(_canonical_bytes(material)).hexdigest()
    if set(material) != STAGE_DELTA_FIELDS:
        raise BriefProjectionError("stage delta shape is not closed")
    if len(_canonical_bytes(material)) >= MAX_TOKEN_UPPER_BOUND:
        raise BriefProjectionError(
            "stage delta handoff must be strictly below 4000 tokens")
    return material


def validate_stage_delta_handoff(value: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild and byte-compare one untrusted stage delta."""
    if not isinstance(value, Mapping) or set(value) != STAGE_DELTA_FIELDS:
        raise BriefProjectionError("stage delta requires exactly its closed fields")
    rebuilt = stage_delta_handoff(
        source_sha=value.get("source_sha"),
        requirement_id=value.get("requirement_id"),
        active_contracts=value.get("active_contracts"),
        acceptance_outcomes=value.get("acceptance_outcomes"),
        new_evidence=value.get("new_evidence"),
        unresolved_decisions=value.get("unresolved_decisions"),
        outstanding_native_set=value.get("outstanding_native_set"),
        observed_usage=value.get("observed_usage"),
        predecessor_fingerprint=value.get("predecessor_fingerprint"),
    )
    if rebuilt != dict(value):
        raise BriefProjectionError("stage delta fingerprint or content mismatched")
    return rebuilt


def measure(value: Mapping[str, Any]) -> dict[str, Any]:
    """Measure a projection body, excluding its self-describing measurement.

    ``token_upper_bound`` intentionally equals the UTF-8 byte count.  It is a
    deterministic conservative ceiling rather than an invented provider token
    count; the host may record an exact tokenizer count separately.
    """
    if not isinstance(value, Mapping):
        raise BriefProjectionError("brief projection must be a mapping")
    body = dict(value)
    body.pop("measurement", None)
    raw = _canonical_bytes(body)
    return {
        "schema": MEASUREMENT_SCHEMA,
        "utf8_bytes": len(raw),
        "token_upper_bound": len(raw),
        "limit": MAX_TOKEN_UPPER_BOUND,
        "strictly_under_limit": len(raw) < MAX_TOKEN_UPPER_BOUND,
    }


def _finish(body: Mapping[str, Any]) -> dict[str, Any]:
    projected = dict(body)
    projected["measurement"] = measure(projected)
    return projected


def _reference(
    kind: str,
    value: Any,
    *,
    artifact: str | None = None,
    field: str | None = None,
) -> dict[str, Any]:
    raw = _canonical_bytes(value)
    result = {
        "schema": REFERENCE_SCHEMA,
        "kind": kind,
        "fingerprint": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }
    if artifact is not None:
        if not isinstance(artifact, str) or not artifact.strip():
            raise BriefProjectionError("reference artifact must be a path")
        result["artifact"] = artifact
        if field is not None:
            result["field"] = str(field)
    return result


def _usage_projection(wave_usage: Mapping[str, Any] | None) -> dict[str, Any]:
    if wave_usage is None:
        return {
            "status": "not_supplied",
            "dispatch_allowed": True,
            "triggered": [],
            "ceilings": dict(WAVE_BUDGET_CEILINGS),
        }
    if not isinstance(wave_usage, Mapping) or set(wave_usage) != set(
        WAVE_BUDGET_CEILINGS
    ):
        raise BriefProjectionError(
            "wave_usage is binding and must contain exactly elapsed_seconds, "
            "sessions, total_tokens, and uncached_input_tokens"
        )
    triggered = []
    normalized: dict[str, int | float] = {}
    for field, ceiling in WAVE_BUDGET_CEILINGS.items():
        observed = wave_usage[field]
        if isinstance(observed, bool) or not isinstance(observed, (int, float)):
            raise BriefProjectionError(f"wave_usage.{field} must be numeric")
        if isinstance(observed, float) and not math.isfinite(observed):
            raise BriefProjectionError(f"wave_usage.{field} must be finite")
        if observed < 0:
            raise BriefProjectionError(f"wave_usage.{field} cannot be negative")
        normalized[field] = observed
        if observed >= ceiling:
            triggered.append(
                {"field": field, "observed": observed, "ceiling": ceiling}
            )
    return {
        "status": "human_scope_review" if triggered else "within_budget",
        "dispatch_allowed": not triggered,
        "usage": normalized,
        "triggered": triggered,
        "ceilings": dict(WAVE_BUDGET_CEILINGS),
    }


def _split_delta(
    current: Mapping[str, Any], previous: Mapping[str, Any] | None,
    *, reference_artifact: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    current_action = {
        key: current[key] for key in _ACTION_FIELDS if key in current
    }
    new_evidence: dict[str, Any] = {}
    unchanged_refs: dict[str, Any] = {}
    wave = current.get("wave")
    if isinstance(wave, list):
        members = []
        for index, raw_entry in enumerate(wave):
            if not isinstance(raw_entry, Mapping):
                raise BriefProjectionError("loop wave entry must be a mapping")
            entry = {key: raw_entry[key] for key in _WAVE_ACTION_FIELDS
                     if key in raw_entry}
            intent = raw_entry.get("dispatch_intent")
            if isinstance(intent, Mapping):
                entry["dispatch_intent"] = {
                    key: intent[key] for key in (
                        "schema", "intent_id", "kind", "intended_consumer",
                        "wait_policy") if key in intent
                }
            members.append(entry)
            task = raw_entry.get("task")
            task_id = str(task.get("id") or index) \
                if isinstance(task, Mapping) else str(index)
            reference = _reference(
                f"loop-next/wave/{task_id}", raw_entry,
                artifact=reference_artifact, field="wave",
            )
            reference["member"] = task_id
            unchanged_refs[f"wave:{task_id}"] = reference
        current_action["wave"] = members
    for key, value in current.items():
        if key in current_action or key == "wave":
            continue
        is_unchanged = previous is not None and key in previous and previous[key] == value
        if (previous is None and key in _REFERENCE_FIELDS) or is_unchanged:
            unchanged_refs[key] = _reference(
                f"loop-next/{key}", value,
                artifact=reference_artifact, field=key,
            )
        else:
            new_evidence[key] = value
    return current_action, new_evidence, unchanged_refs


def project(
    action: Mapping[str, Any],
    *,
    previous: Mapping[str, Any] | None = None,
    wave_usage: Mapping[str, Any] | None = None,
    reference_artifact: str | None = None,
) -> dict[str, Any]:
    """Project one real loop action into a bounded host-consumable delta.

    The action and optional predecessor are never mutated.  At a binding
    budget ceiling the projection replaces the executable action with an
    explicit paused human-review action.  An oversized evidence delta becomes
    a bounded refusal plus a content-addressed reference.  At the production
    adapter, an authority-heavy current-action field can itself become a
    resolvable reference to the exact persisted source so the ready transition
    remains below the host bound.
    """
    if not isinstance(action, Mapping):
        raise BriefProjectionError("loop next action must be a mapping")
    if previous is not None and not isinstance(previous, Mapping):
        raise BriefProjectionError("previous loop next action must be a mapping")

    budget = _usage_projection(wave_usage)
    current_action, new_evidence, unchanged_refs = _split_delta(
        action, previous, reference_artifact=reference_artifact
    )
    status = "ready"
    if not budget["dispatch_allowed"]:
        status = "human_scope_review"
        current_action = {
            "step": "human_scope_review",
            "paused": True,
            "dispatch_allowed": False,
            "reason": "binding_wave_budget_reached",
        }

    body = {
        "schema": DELTA_SCHEMA,
        "status": status,
        "step": current_action.get("step"),
        "current_action": current_action,
        "new_evidence": new_evidence,
        "unchanged_refs": unchanged_refs,
        "budget": budget,
    }
    projected = _finish(body)
    if projected["measurement"]["strictly_under_limit"]:
        return projected

    # Large Plan/Design instructions are still normal executable actions.  A
    # persisted production source lets us keep the transition ready while
    # replacing only the largest inline fields, one at a time, until it fits.
    # Direct/unit callers without a source retain the original fail-closed
    # refusal behavior.
    if reference_artifact is not None:
        compact_action = dict(current_action)
        candidates = sorted(
            (key for key in compact_action if key != "step"),
            key=lambda key: len(_canonical_bytes(compact_action[key])),
            reverse=True,
        )
        for key in candidates:
            compact_action[key] = _reference(
                f"loop-next/current-action/{key}", current_action[key],
                artifact=reference_artifact, field=key,
            )
            body["current_action"] = compact_action
            projected = _finish(body)
            if projected["measurement"]["strictly_under_limit"]:
                return projected
        current_action = compact_action

        # Dense legacy actions can contain many distinct evidence surfaces;
        # even one reference per field can exceed the bound.  Collapse that
        # manifest to one exact-source reference while retaining the routing
        # scalars needed by the host.  The referenced bytes are the same
        # immutable action persisted by ``project_next_action_for_host``.
        source_ref = _reference(
            "loop-next/source", action, artifact=reference_artifact)
        minimal_action: dict[str, Any] = {}
        for key in ("step", "role", "paused", "parallel"):
            value = action.get(key)
            if isinstance(value, (bool, int, float)) or (
                    isinstance(value, str) and len(value) <= 128):
                minimal_action[key] = value
        minimal_action["source_ref"] = source_ref
        collapsed = _finish({
            "schema": DELTA_SCHEMA,
            "status": status,
            "step": current_action.get("step"),
            "current_action": (
                current_action if status == "human_scope_review"
                else minimal_action
            ),
            "new_evidence": {"source_ref": source_ref},
            "unchanged_refs": {},
            "budget": budget,
        })
        if collapsed["measurement"]["strictly_under_limit"]:
            return collapsed

    refusal = _finish(
        {
            "schema": REFUSAL_SCHEMA,
            "status": "refused",
            "reason": "brief_token_budget_exceeded",
            "current_action": current_action,
            "budget": budget,
            "unchanged_refs": unchanged_refs,
            "artifact_ref": _reference("loop-next/oversized-delta", projected),
        }
    )
    if not refusal["measurement"]["strictly_under_limit"] and \
            reference_artifact is not None:
        refusal = _finish(
            {
                "schema": REFUSAL_SCHEMA,
                "status": "refused",
                "reason": "brief_token_budget_exceeded",
                "current_action": current_action,
                "budget": budget,
                "unchanged_ref_count": len(unchanged_refs),
                "artifact_ref": _reference(
                    "loop-next/source", action,
                    artifact=reference_artifact,
                ),
            }
        )
    if not refusal["measurement"]["strictly_under_limit"]:
        raise BriefProjectionError(
            "authority-bearing loop next fields exceed the bounded refusal limit"
        )
    return refusal
