"""Deterministic runtime guidance for dynamic model execution.

The control data says which workflow invariants matter.  It deliberately says
nothing about expected model wording, finding counts, or transcript bytes.
Live execution may vary; this module observes only machine-owned workflow facts
and returns one bounded correction before a repeated drift blocks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from functools import lru_cache
from typing import Any

import storage as runtime_storage


SCHEMA = "taskplane.runtime-evals/v1"
GUIDANCE_SCHEMA = "taskplane.runtime-guidance/v1"
LIFECYCLE_SCHEMA = "taskplane.evaluation-lifecycle/v1"
CONTROL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "taskplane", "references", "runtime-evals.json")
_VALID_CLASSES = {"mechanical", "recoverable", "irreversible"}
REVIEW_FACTS = (
    "graph_before_route", "shared_review_context",
    "selective_lens_mapping", "lens_results_collected",
    "output_schema_declared", "output_schema_validated",
    "output_producer_observed")

TOKEN_PROJECTION_SCHEMA = "taskplane.host-token-projection/v1"
STAGE_STARTUP_PROJECTION_SCHEMA = "taskplane.stage-startup-projection/v1"


def enforcement_projection(value: dict | None) -> dict:
    """Bounded lossless identity used by evidence and host projections."""
    row = value if isinstance(value, dict) else {}
    advisory = row.get("advisory") \
        if isinstance(row.get("advisory"), dict) else None
    return {
        "schema": "taskplane.enforcement-projection/v1",
        "status": (str(row.get("status")) if row.get("status") in {
            "live", "unproven", "advisory"} else "unproven"),
        "evidence_id": str(row.get("evidence_id") or "")[:80],
        "workspace_fingerprint": str(
            row.get("workspace_fingerprint") or "")[:64],
        "session_fingerprint": (str(row.get("session_fingerprint"))[:64]
                                if row.get("session_fingerprint") else None),
        "run_id": str(row.get("run_id"))[:128]
        if row.get("run_id") is not None else None,
        "revision": row.get("revision"),
        "mode": str(row.get("mode") or "warn"),
        "advisory": ({
            "actor": str(advisory.get("actor") or "")[:256],
            "acknowledged_at": str(
                advisory.get("acknowledged_at") or "")[:64],
            "decision_id": str(advisory.get("decision_id") or "")[:80],
        } if advisory else None),
    }


def foreign_interference_projection(value: dict | None) -> dict:
    """Bounded durable projection for status, retro, and runtime guidance."""
    row = value if isinstance(value, dict) else {}
    counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
    safe_counts = {
        key: max(0, int(counts.get(key) or 0)) for key in (
            "denied_skills", "denied_agents", "advised_invocations",
            "observed_invocations", "signed_roots")}
    identities = sorted({str(item.get("identity") or "")[:256]
                         for item in row.get("identities") or []
                         if isinstance(item, dict) and item.get("identity")})[:128]
    roots = sorted({str(item.get("root") or "")[:512]
                    for item in row.get("state_roots") or []
                    if isinstance(item, dict) and item.get("root")})[:64]
    total = sum(safe_counts.values())
    return {
        "schema": "taskplane.foreign-interference-projection/v1",
        "headline": total > 0, "total": total, "counts": safe_counts,
        "identities": identities, "state_roots": roots,
        "registry_evidence": sorted({
            str(item.get("registry_fingerprint") or "")[:64]
            for item in (row.get("events") or []) + (row.get("state_roots") or [])
            if isinstance(item, dict) and item.get("registry_fingerprint")})[:16],
    }


def worktree_cleanup_projection(value: object) -> dict:
    """Bounded cleanup outcomes used after worker directories disappear."""
    if isinstance(value, dict) and value.get("schema") == \
            "taskplane.worktree-cleanup/v1":
        rows = [value]
    elif isinstance(value, dict):
        rows = [row for row in value.values() if isinstance(row, dict)]
    elif isinstance(value, list):
        rows = [row for row in value if isinstance(row, dict)]
    else:
        rows = []
    allowed = {"pending", "preserved", "removed", "already-clean",
               "manual-attention"}
    projected = [{
        "receipt_id": str(row.get("receipt_id") or "")[:128],
        "task_id": str(row.get("task_id") or "")[:128],
        "outcome": (str(row.get("outcome"))
                    if row.get("outcome") in allowed else "manual-attention"),
        "reason": str(row.get("reason") or "")[:512],
        "outcome_fingerprint": str(
            row.get("outcome_fingerprint") or "")[:64],
    } for row in rows]
    counts = {name: sum(row["outcome"] == name for row in projected)
              for name in sorted(allowed)}
    attention = counts["preserved"] + counts["manual-attention"]
    return {"schema": "taskplane.worktree-cleanup-projection/v1",
            "headline": attention > 0, "attention": attention,
            "counts": counts, "outcomes": projected[:128]}


def review_fix_convergence_projection(
        previous_revision: dict, current_revision: dict, *, cycle: int,
        previously_closed: set[str] | None = None,
        history: list[dict] | None = None, max_cycles: int | None = None,
        human_stop: bool = False, unsafe_recovery: bool = False,
        scope_changed: bool = False, authority_changed: bool = False) -> dict:
    """Production runtime entry point for the bounded fix-cycle policy."""
    import review_convergence

    return review_convergence.evaluate_fix_cycle(
        previous_revision, current_revision, cycle=cycle,
        previously_closed=previously_closed, history=history,
        max_cycles=max_cycles, human_stop=human_stop,
        unsafe_recovery=unsafe_recovery, scope_changed=scope_changed,
        authority_changed=authority_changed)


def review_outcome_with_lens_telemetry(
        sealed_revision: dict, *, lifecycle: dict | None = None,
        usage_by_lens: dict | None = None, enabled: bool = True) -> dict:
    """Attach post-review metrics without allowing observation to affect review.

    This is the production boundary between authoritative review state and the
    optional telemetry projection.  Disabled telemetry performs no projection;
    both paths return the same independent review snapshot.
    """
    review_snapshot = copy.deepcopy(sealed_revision)
    telemetry = None
    if enabled:
        import lens_telemetry

        telemetry = lens_telemetry.build_lens_telemetry(
            review_snapshot, lifecycle=lifecycle,
            usage_by_lens=usage_by_lens)
    return {"review": review_snapshot, "telemetry": telemetry}


def observed_token_projection(usage: dict | None, *, provider: str,
                              source: str, scope: str) -> dict:
    """Project observed usage; absent provider data is never fabricated."""
    raw = usage if isinstance(usage, dict) else {}
    raw_value = raw.get("raw_total_tokens", raw.get("total_tokens"))
    effective_value = raw.get("effective_tokens", raw.get("effective"))

    def valid(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    present = [raw_value is not None, effective_value is not None]
    malformed = ((raw_value is not None and not valid(raw_value)) or
                 (effective_value is not None and not valid(effective_value)))
    if malformed:
        status, raw_value, effective_value = "malformed", None, None
    elif all(present):
        status = "observed"
    elif any(present):
        status = "partial"
    else:
        status = "unavailable"
    row = {
        "schema": TOKEN_PROJECTION_SCHEMA, "status": status,
        "raw_tokens": raw_value if valid(raw_value) else None,
        "effective_tokens": effective_value if valid(effective_value) else None,
        "scope": str(scope or "unknown"), "provider": str(provider or "unknown"),
        "source": str(source or "unavailable"),
        "observed": status in {"observed", "partial"}, "estimated": False,
    }
    encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
    row["fingerprint"] = hashlib.sha256(encoded).hexdigest()
    return row


def stage_startup_projection(dispatch: dict) -> dict:
    """Return exact bounded startup measurements from a verified dispatch."""
    import taskplane_lite

    serialized = taskplane_lite.stage_startup_bytes(dispatch)
    telemetry = dispatch.get("telemetry")
    if not isinstance(telemetry, dict):
        raise taskplane_lite.StageDispatchError(
            "stage startup telemetry is invalid")
    telemetry_fields = (
        "manifest_bytes", "startup_bytes", "startup_tokens",
        "selected_ref_count",
        "selected_ref_bytes", "predecessor_root_opens")
    if any(type(telemetry.get(field)) is not int
           or telemetry[field] < 0 for field in telemetry_fields):
        raise taskplane_lite.StageDispatchError(
            "stage startup telemetry is invalid")
    startup_bytes = len(serialized)
    startup_token_estimate = (startup_bytes + 3) // 4
    if telemetry["startup_bytes"] != startup_bytes or \
            telemetry["startup_tokens"] != startup_token_estimate:
        raise taskplane_lite.StageDispatchError(
            "stage startup telemetry mismatch")
    return {
        "schema": STAGE_STARTUP_PROJECTION_SCHEMA,
        "startup_sha256": hashlib.sha256(serialized).hexdigest(),
        "manifest_bytes": telemetry["manifest_bytes"],
        "startup_bytes": startup_bytes,
        "startup_token_estimate": startup_token_estimate,
        "selected_ref_count": telemetry["selected_ref_count"],
        "selected_ref_bytes": telemetry["selected_ref_bytes"],
        "predecessor_root_opens": telemetry["predecessor_root_opens"],
    }


_LIFECYCLE_TERMINAL = {"success", "failed", "timeout", "cancelled",
                       "unavailable"}
_VALIDATION_STATUSES = {"valid", "invalid", "unavailable"}
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|"
    r"CREDENTIAL|PRIVATE_KEY)[A-Z0-9_]*)\s*=\s*[^\s,;]+")
_ABSOLUTE_PATH = re.compile(
    r"(?:(?:[A-Za-z]:[\\/])|/)(?:[^\s,;]+[\\/])*[^\s,;]*")


def review_revision_projection(revision: dict) -> dict:
    """Project revision health without copying findings into machine context."""
    row = revision if isinstance(revision, dict) else {}
    completeness = row.get("completeness") \
        if isinstance(row.get("completeness"), dict) else {}
    gaps = row.get("gaps") if isinstance(row.get("gaps"), list) else []
    gap_ids = sorted({str(gap.get("slot_id") or "").strip()
                      for gap in gaps if isinstance(gap, dict)
                      and str(gap.get("slot_id") or "").strip()})
    approval = row.get("approval") \
        if isinstance(row.get("approval"), dict) else {}
    complete = completeness.get("complete") is True and not gap_ids
    return {
        "schema": "taskplane.review-revision-projection/v1",
        "status": "complete" if complete else "incomplete",
        "disposition": str(row.get("disposition") or
                           ("canonical" if complete else "provisional")),
        "canonical_revision": int(row.get("canonical_revision") or 0),
        "target_fingerprint": _bounded_diagnostic(
            row.get("target_fingerprint"), 128),
        "context_fingerprint": _bounded_diagnostic(
            row.get("context_fingerprint"), 128),
        "findings_fingerprint": _bounded_diagnostic(
            row.get("findings_fingerprint"), 128),
        "finding_count": len(row.get("findings") or [])
        if isinstance(row.get("findings"), list) else 0,
        "expected_slot_count": max(
            0, int(completeness.get("expected") or 0)),
        "collected_slot_count": max(
            0, int(completeness.get("collected") or 0)),
        "gap_slot_ids": gap_ids,
        "approval_enabled": approval.get("enabled") is True and complete,
    }


def command_wave_projection(wave: dict, *, efficiency: dict | None = None,
                            artifacts: list[dict] | None = None) -> dict:
    """Return bounded observed wave evidence; absence stays ``unproven``."""
    raw_efficiency = efficiency if isinstance(efficiency, dict) else {}
    total = raw_efficiency.get("total_raw_tokens")
    polling = max(0, int(raw_efficiency.get("polling_raw_tokens") or 0))
    measured = isinstance(total, int) and total > 0
    projected_artifacts = []
    for raw in artifacts if isinstance(artifacts, list) else []:
        row = raw if isinstance(raw, dict) else {}
        projected_artifacts.append({
            "path": _bounded_artifact_path(row.get("path"), 512),
            "sha256": _bounded_diagnostic(row.get("sha256"), 128),
            "bytes": max(0, int(row.get("bytes") or 0)),
            "truncated": row.get("truncated") is True,
        })
    return {
        "schema": "taskplane.command-wave-evidence/v1",
        "wave_id": _bounded_diagnostic(wave.get("wave_id"), 128),
        "members": dict(wave.get("members") or {}),
        "interrupted": wave.get("interrupted") is True,
        "ordinary_completion_deliveries": max(
            0, int(wave.get("ordinary_completion_deliveries") or 0)),
        "attention_deliveries": len(wave.get("delivered_attention") or []),
        "artifacts": projected_artifacts,
        "efficiency": {
            "launches": max(0, int(raw_efficiency.get("launches") or 0)),
            "model_wakes": max(0, int(raw_efficiency.get("model_wakes") or 0)),
            "unchanged_model_polls": max(
                0, int(raw_efficiency.get("unchanged_model_polls") or 0)),
            "polling_raw_tokens": polling,
            "total_raw_tokens": total if measured else None,
            "polling_raw_token_share": polling / total if measured else None,
            "measurement_status": "measured" if measured else "unproven",
        },
    }


def _bounded_diagnostic(value: Any, limit: int = 512) -> str:
    text = _SECRET_ASSIGNMENT.sub("<redacted>", str(value or ""))
    text = _ABSOLUTE_PATH.sub("<redacted-path>", text)
    raw = text.encode("utf-8", errors="replace")[:limit]
    while raw:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw[:-1]
    return ""


def _bounded_artifact_path(value: Any, limit: int = 512) -> str:
    """Preserve safe relative references without exposing host paths."""
    text = str(value or "")
    if _SECRET_ASSIGNMENT.search(text):
        return "<redacted>"
    normalized = text.replace("\\", "/")
    if (normalized.startswith("/") or
            re.match(r"^[A-Za-z]:/", normalized) or
            ".." in normalized.split("/")):
        return "<redacted-path>"
    raw = text.encode("utf-8", errors="replace")[:limit]
    while raw:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw[:-1]
    return ""


def build_evaluation_lifecycle(
        *, run_id: str, host: str, host_version: str | None,
        capability_source: str, transport: str, schema_transport: str,
        schema_fallback_reason: str | None, task: str | None,
        slot: str | None, lease: str | None, planned_model: str | None,
        planned_effort: str | None, observed_model: str | None,
        observed_effort: str | None, attempts: list[dict], duration_ms: int,
        terminal_status: str, validation_status: str, telemetry: dict,
        diagnostics: list[Any]) -> dict:
    """Build the bounded host-neutral lifecycle record accepted by evals."""
    safe_attempts = []
    for index, raw in enumerate(attempts if isinstance(attempts, list) else []):
        row = raw if isinstance(raw, dict) else {}
        safe_attempts.append({
            "attempt": int(row.get("attempt") or index + 1),
            "status": str(row.get("status") or "failed"),
            "duration_ms": max(0, int(row.get("duration_ms") or 0)),
        })
    safe_diagnostics = []
    for raw in diagnostics if isinstance(diagnostics, list) else []:
        if isinstance(raw, dict):
            code, message = raw.get("code"), raw.get("message")
        else:
            code, message = "evaluation", raw
        safe_diagnostics.append({
            "code": _bounded_diagnostic(code, 64) or "evaluation",
            "message": _bounded_diagnostic(message),
        })
    telemetry_row = telemetry if isinstance(telemetry, dict) else {}
    return {
        "schema": LIFECYCLE_SCHEMA,
        "run_id": _bounded_diagnostic(run_id, 128),
        "host": _bounded_diagnostic(host, 32),
        "host_version": _bounded_diagnostic(host_version, 64)
        if host_version else None,
        "capability_source": _bounded_diagnostic(capability_source, 256),
        "transport": _bounded_diagnostic(transport, 64),
        "schema_transport": _bounded_diagnostic(schema_transport, 64),
        "schema_fallback_reason": _bounded_diagnostic(
            schema_fallback_reason) if schema_fallback_reason else None,
        "identity": {"task": _bounded_diagnostic(task, 128) if task else None,
                     "slot": _bounded_diagnostic(slot, 128) if slot else None,
                     "lease": _bounded_diagnostic(lease, 128) if lease else None},
        "routing": {
            "planned": {"model": _bounded_diagnostic(planned_model, 128)
                         if planned_model else None,
                        "reasoning_effort": _bounded_diagnostic(
                            planned_effort, 32) if planned_effort else None},
            "observed": {"model": _bounded_diagnostic(observed_model, 128)
                          if observed_model else None,
                         "reasoning_effort": _bounded_diagnostic(
                             observed_effort, 32) if observed_effort else None},
        },
        "attempts": safe_attempts,
        "duration_ms": max(0, int(duration_ms or 0)),
        "terminal_status": str(terminal_status or "unavailable"),
        "validation_status": str(validation_status or "unavailable"),
        "telemetry": {
            "available": telemetry_row.get("available") is True,
            "reason": _bounded_diagnostic(telemetry_row.get("reason"))
            if telemetry_row.get("reason") else None,
        },
        "diagnostics": safe_diagnostics,
    }


def validate_evaluation_lifecycle(row: dict) -> list[str]:
    """Return exact schema errors; records do not enter evals when non-empty."""
    if not isinstance(row, dict):
        return ["evaluation lifecycle must be an object"]
    errors = []
    if row.get("schema") != LIFECYCLE_SCHEMA:
        errors.append(f"schema must be {LIFECYCLE_SCHEMA}")
    for key in ("run_id", "host", "capability_source", "transport",
                "schema_transport"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            errors.append(f"missing {key}")
    if row.get("terminal_status") not in _LIFECYCLE_TERMINAL:
        errors.append("invalid terminal_status")
    if row.get("validation_status") not in _VALIDATION_STATUSES:
        errors.append("invalid validation_status")
    attempts = row.get("attempts")
    if not isinstance(attempts, list):
        errors.append("attempts must be a list")
    elif any(not isinstance(item, dict)
             or not isinstance(item.get("attempt"), int)
             or not isinstance(item.get("duration_ms"), int)
             for item in attempts):
        errors.append("attempt record is invalid")
    if not isinstance(row.get("routing"), dict):
        errors.append("routing must be an object")
    if not isinstance(row.get("identity"), dict):
        errors.append("identity must be an object")
    telemetry = row.get("telemetry")
    if not isinstance(telemetry, dict) or not isinstance(
            telemetry.get("available"), bool):
        errors.append("telemetry availability is missing")
    diagnostics = row.get("diagnostics")
    if not isinstance(diagnostics, list):
        errors.append("diagnostics must be a list")
    elif any(not isinstance(item, dict)
             or len(str(item.get("message") or "").encode("utf-8")) > 512
             for item in diagnostics):
        errors.append("diagnostic is invalid or oversized")
    return errors


class RuntimeEvalError(ValueError):
    pass


@lru_cache(maxsize=4)
def load_controls(path: str | None = None) -> dict:
    source = path or CONTROL_PATH
    try:
        with open(source, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeEvalError(f"runtime eval controls unavailable: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RuntimeEvalError(f"runtime eval controls must use {SCHEMA}")
    if data.get("baseline_policy") != "telemetry-only":
        raise RuntimeEvalError("model baselines cannot gate runtime execution")
    controls = data.get("controls")
    if not isinstance(controls, list) or not controls:
        raise RuntimeEvalError("runtime eval controls must be a non-empty list")
    seen = set()
    for row in controls:
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            raise RuntimeEvalError("every runtime eval control needs an id")
        if row["id"] in seen:
            raise RuntimeEvalError(f"duplicate runtime eval control {row['id']}")
        seen.add(row["id"])
        if row.get("class") not in _VALID_CLASSES:
            raise RuntimeEvalError(f"invalid runtime eval class for {row['id']}")
        if not isinstance(row.get("steps"), list) or not row["steps"]:
            raise RuntimeEvalError(f"runtime eval control {row['id']} has no steps")
        if row.get("class") == "recoverable":
            if row.get("max_corrections") != 1 or not row.get("correction"):
                raise RuntimeEvalError(
                    f"recoverable control {row['id']} must define one correction")
    return data


def controls_fingerprint() -> str:
    encoded = json.dumps(load_controls(), sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def controls_for(step: str, *, checkpoint: str | None = None) -> list:
    rows = []
    for row in load_controls()["controls"]:
        if step not in row["steps"] and "*" not in row["steps"]:
            continue
        if checkpoint is not None and row.get("checkpoint") != checkpoint:
            continue
        rows.append({key: value for key, value in row.items()
                     if key not in {"steps"}})
    return rows


def guidance(step: str) -> dict:
    return {
        "schema": GUIDANCE_SCHEMA,
        "mode": "guide-and-recover",
        "baseline_policy": "telemetry-only",
        "controls_fingerprint": controls_fingerprint(),
        "checkpoint": ("Every `tp loop submit pass` automatically runs this "
                       "checkpoint. `tp loop guide` may run it earlier. Apply "
                       "its one bounded correction when present; repeated "
                       "unresolved drift is blocking."),
        "controls": controls_for(step),
    }


def assess(step: str, facts: dict | None,
           *, correction_attempts: int = 0) -> dict:
    """Assess machine facts only; dynamic model output is not an input."""
    facts = facts if isinstance(facts, dict) else {}
    rows = controls_for(step, checkpoint="before_submit")
    missing = []
    corrections = []
    max_corrections = 0
    for row in rows:
        absent = [fact for fact in row.get("required_facts") or []
                  if facts.get(fact) is not True]
        if not absent:
            continue
        missing.extend({"control": row["id"], "fact": fact}
                       for fact in absent)
        if row.get("correction"):
            corrections.append(row["correction"])
        max_corrections = max(max_corrections,
                              int(row.get("max_corrections") or 0))
    if not missing:
        return {"schema": GUIDANCE_SCHEMA, "status": "on_path",
                "step": step, "missing": [], "max_corrections": 1}
    status = ("correct" if correction_attempts < max_corrections
              else "blocked")
    return {
        "schema": GUIDANCE_SCHEMA, "status": status, "step": step,
        "missing": missing, "corrections": list(dict.fromkeys(corrections)),
        "correction_attempt": min(correction_attempts + 1, max_corrections),
        "max_corrections": max_corrections,
        "instruction": ("Apply the correction once, then re-check before "
                        "submitting pass." if status == "correct" else
                        "The same workflow drift remains after its correction; "
                        "submit fail or return control to the orchestrator."),
    }


def _complete_quick_only_evaluation(
        state: dict, quality: dict, verdict: dict, review_module: Any) -> bool:
    """Recognize the one requirement-bound alternative to deep-era receipts.

    R-0006 deliberately permits routing from a pinned immutable diff when
    graph enrichment is incomplete.  The legacy runtime controls predate
    that policy and otherwise reject the completed quick review solely
    because ``graph-quality.status`` is not ``complete``.  This predicate is
    intentionally stricter than the ordinary receipt checks: it admits only
    the exact quick-only manifest with no deep/promotion artifacts, no
    blocking canonical or provisional findings, and a fully green,
    schema-validated evaluator output.
    """
    policy = state.get("review_depth_policy") or {}
    receipt = state.get("review_depth_receipt") or {}
    if not (
            policy.get("schema") == "taskplane.review-depth-policy/v1"
            and policy.get("requirement_id") == "R-0006"
            and policy.get("depth") == "quick-only"
            and policy.get("deep_slots_allowed") is False
            and policy.get("complete_quick_output_sufficient") is True
            and state.get("status") in {"ready", "complete"}
            and receipt.get("status") == "satisfied"
            and receipt.get("outcome") == "quick_output_sufficient"
            and receipt.get("deep_slots") == []
            and int(receipt.get("promotion_attempts") or 0) == 0
            and not state.get("adaptive_wave")
            and not (state.get("quick_corrections") or [])):
        return False

    slots = [row for row in state.get("slots") or []
             if isinstance(row, dict)]
    if len(slots) != 1 or slots[0].get("slot_id") != "light-sweep":
        return False
    if receipt.get("quick_slots") != ["light-sweep"]:
        return False
    expected_lenses = [str(value) for value in slots[0].get("lens_ids") or []]
    if not expected_lenses or len(set(expected_lenses)) != len(expected_lenses):
        return False
    if quality.get("status") != "complete" and (
            (quality.get("review_fallback") or {}).get("mode") !=
            "immutable_diff"):
        return False

    revisions = [row for row in (
        state.get("revision"), state.get("provisional_revision"))
        if isinstance(row, dict)]
    if any(review_module.blocking_findings_by_lens(
            row.get("findings") or []) for row in revisions):
        return False
    canonical_rows = [row for row in state.get("lens_results") or []
                      if isinstance(row, dict)]
    if state.get("status") == "complete":
        canonical_ids = [str(row.get("lens") or "")
                         for row in canonical_rows]
        if set(canonical_ids) != set(expected_lenses) or \
                len(set(canonical_ids)) != len(canonical_ids):
            return False
        if any(row.get("verdict") != "pass" or
               not isinstance(row.get("blockers"), int) or
               row.get("blockers") != 0 for row in canonical_rows):
            return False
    else:
        # In the R-0006 workflow the evaluator's schema-bound output is the
        # quick result.  The legacy collection probe consequently records one
        # honest missing light-sweep producer, which must be the only gap.
        provisional = state.get("provisional_revision") or {}
        completeness = provisional.get("completeness") or {}
        gaps = provisional.get("gaps") or []
        if not (
                not canonical_rows
                and provisional.get("disposition") == "provisional"
                and completeness.get("expected") == 1
                and completeness.get("collected") == 0
                and completeness.get("missing") == 1
                and completeness.get("complete") is False
                and len(gaps) == 1
                and isinstance(gaps[0], dict)
                and gaps[0].get("slot_id") == "light-sweep"):
            return False

    evaluation = verdict.get("evaluation") or {}
    criteria = verdict.get("criteria") or []
    lens_rows = [row for row in verdict.get("lenses") or []
                 if isinstance(row, dict)]
    lens_ids = [str(row.get("lens") or "") for row in lens_rows]
    if not (
            verdict.get("verdict") == "pass"
            and evaluation.get("status") == "complete"
            and evaluation.get("reason_code") == "none"
            and not (verdict.get("failures") or [])
            and isinstance(criteria, list) and criteria
            and all(isinstance(row, dict) and row.get("status") == "met"
                    and bool(str(row.get("evidence") or "").strip())
                    for row in criteria)
            and len(set(lens_ids)) == len(lens_ids)
            and set(lens_ids) == set(expected_lenses)
            and all(row.get("verdict") == "pass" and
                    isinstance(row.get("blockers"), int) and
                    row.get("blockers") == 0 for row in lens_rows)):
        return False
    return True


def review_facts(ws: str, step: str, *, run_id: str) -> dict:
    """Machine-owned ReviewKernel facts used by Evaluate and final EM."""
    expected_stage = "build" if step == "evaluate" else "review"
    facts = {key: False for key in REVIEW_FACTS}
    try:
        import review
        import review_evidence

        state = review._load_state(ws, run_id)
        if not isinstance(state, dict) or state.get("stage") != expected_stage:
            return facts
        store = review_evidence.ArtifactStore(ws)
        quality = store.read(state["quality"]) if state.get("quality") else {}
        facts["graph_before_route"] = quality.get("status") == "complete"
        facts["shared_review_context"] = bool(state.get("envelope"))
        facts["selective_lens_mapping"] = bool(
            state.get("routing_decision") and state.get("routing"))
        facts["lens_results_collected"] = bool(
            state.get("status") == "complete" and state.get("revision"))
        if step == "em":
            # Final engineering review uses the strict leased lens-output
            # schema rather than the evaluator verdict schema. A complete
            # canonical revision can exist only after every leased file was
            # schema-validated and matched to its host-observed producer/write
            # receipt.
            import evaluation_output

            slots = state.get("slots") or []
            # An empty routed set owes no model output, but the kernel still
            # declares the one authoritative schema for any slot that would
            # be summoned. ``all([])`` is therefore the correct contract
            # result, not a missing-schema failure.
            declared = True
            for slot in slots:
                try:
                    brief = store.read(slot["brief"])
                    schema = brief.get("result_schema") or {}
                    declared = declared and schema.get("$id") == \
                        evaluation_output.LENS_SLOT_OUTPUT_SCHEMA_ID
                except Exception:
                    declared = False
            facts["output_schema_declared"] = declared
            facts["output_schema_validated"] = bool(
                declared and facts["lens_results_collected"])
            facts["output_producer_observed"] = bool(
                facts["lens_results_collected"])
        if step == "evaluate":
            verdict_path = runtime_storage.evaluation_path(ws)
            try:
                with open(verdict_path, "rb") as stream:
                    raw = stream.read(1024 * 1024 + 1)
                verdict = json.loads(raw.decode("utf-8"))
                import evaluation_output

                facts["output_schema_declared"] = (
                    isinstance(verdict, dict) and
                    verdict.get("schema") ==
                    evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID)
                graph = verdict.get("graph") if isinstance(
                    verdict.get("graph"), dict) else {}
                projection = {
                    "schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
                    "task": str(verdict.get("task") or ""),
                    "requirement": str(verdict.get("requirement") or
                                       verdict.get("req") or ""),
                    "verdict": verdict.get("verdict"),
                    "criteria": [{key: row.get(key) for key in
                                  ("criterion", "status", "evidence")}
                                 for row in verdict.get("criteria") or []
                                 if isinstance(row, dict)],
                    "graph": {
                        "dispositions": [
                            {key: row.get(key) for key in
                             ("node", "status", "evidence")}
                            for row in graph.get("dispositions") or []
                            if isinstance(row, dict)],
                        "requirements_checked": list(
                            graph.get("requirements_checked") or []),
                        "contracts_checked": list(
                            graph.get("contracts_checked") or []),
                    },
                    "failures": list(verdict.get("failures") or []),
                }
                evaluation_output.validate_output_bytes(
                    evaluation_output.canonical_bytes(projection), {
                        "output_schema":
                            evaluation_output.evaluator_output_schema(),
                        "max_bytes": evaluation_output.MAX_OUTPUT_BYTES,
                    })
                facts["output_schema_validated"] = \
                    facts["output_schema_declared"]
                if facts["output_schema_validated"] and \
                        state.get("zero_lens_evaluation") is True and \
                        state.get("status") == "complete":
                    facts["graph_before_route"] = True
                    facts["output_producer_observed"] = True
            except Exception:
                facts["output_schema_validated"] = False
            # The outer verdict is admissible only after the canonical leased
            # evidence beneath it has an observed producer/write receipt and
            # has been collected into one revision.
            if state.get("zero_lens_evaluation") is not True:
                facts["output_producer_observed"] = facts[
                    "lens_results_collected"]
        if not facts["selective_lens_mapping"] and \
                _completed_zero_lens_mapping_is_satisfied(
                    state, step, facts):
            # A completed zero-lens delivery owes no selective mapping.  The
            # fact is satisfied as non-applicable only after the canonical
            # revision and exact output contract have both been validated.
            facts["selective_lens_mapping"] = True
    except Exception:
        pass
    return facts


def _completed_zero_lens_mapping_is_satisfied(
        state: dict, step: str, facts: dict) -> bool:
    """Recognize only a canonical, schema-valid zero-lens completion."""
    revision = state.get("revision") \
        if isinstance(state.get("revision"), dict) else {}
    completeness = revision.get("completeness") \
        if isinstance(revision.get("completeness"), dict) else {}
    if not (
            state.get("status") == "complete"
            and state.get("expected_lenses") == []
            and state.get("slots") == []
            and revision.get("schema") == "taskplane.findings-revision/v2"
            and revision.get("disposition") == "canonical"
            and completeness.get("complete") is True
            and not (revision.get("gaps") or [])
            and all(facts.get(key) is True for key in (
                "graph_before_route", "shared_review_context",
                "lens_results_collected", "output_schema_declared",
                "output_schema_validated", "output_producer_observed"))):
        return False
    if step == "evaluate":
        return state.get("zero_lens_evaluation") is True and \
            state.get("lens_execution_policy") == "none"
    if step != "em":
        return False
    try:
        import delivery_policy

        receipt = delivery_policy.validate_delivery_mode_receipt(
            state.get("delivery_mode_receipt"))
    except Exception:
        return False
    return receipt.get("mode") == "build" and \
        receipt.get("automatic_lenses") == []


def collect_review_if_ready(ws: str, step: str, *, run_id: str) -> None:
    """Seal authored leased results before the submission checkpoint.

    Collection used to happen only inside the later gate.  The automatic
    guide runs at submission, so it performs the same deterministic collect
    when a matching kernel is ready. Missing or invalid results remain facts
    for the bounded correction; they are never synthesized here.
    """
    expected_stage = "build" if step == "evaluate" else "review"
    try:
        import review

        state = review._load_state(ws, run_id)
        if state.get("stage") == expected_stage and state.get("status") == "ready":
            review.collect_review(ws, publish=False, run_id=state.get("run_id"))
    except Exception:
        pass


def guide_loop(ws: str, task_id: str | None = None) -> dict:
    """Checkpoint the active loop and persist at most one correction."""
    import loop
    import taskplane_lite as tp

    state = loop.load(ws)
    if state is None:
        return {"error": "no active loop"}
    step = str(state.get("step") or "")
    task = loop._current_task(state)
    if step == "execute" and state.get("parallel"):
        if not task_id:
            return {"error": "parallel runtime guide needs --task <id>"}
        task = next((row for row in state.get("tasks") or []
                     if row.get("id") == task_id), None)
        if task is None:
            return {"error": f"no task {task_id}"}
    elif task_id and task_id != (task or {}).get("id"):
        return {"error": f"--task {task_id} does not match the current task"}
    key = f"{step}:{(task or {}).get('id') or '_'}"
    prior = ((state.get("runtime_eval") or {}).get(key) or {})
    attempts = int(prior.get("correction_attempts") or 0)
    review_ws = ws
    if step == "evaluate" and state.get("parallel"):
        candidate = (task or {}).get("workspace")
        if candidate and os.path.isdir(candidate):
            review_ws = candidate
    binding = (loop.review_kernel_binding(state, step, task)
               if step in {"evaluate", "em"} else None)
    if binding:
        review_ws = str(binding.get("workspace") or review_ws)
        collect_review_if_ready(
            review_ws, step, run_id=binding["run_id"])
    facts = (review_facts(review_ws, step, run_id=binding["run_id"])
             if binding else {}) if step in {"evaluate", "em"} else {}
    result = assess(step, facts, correction_attempts=attempts)
    recovered = result["status"] == "on_path" and attempts > 0
    with loop.mutate(ws) as fresh:
        if fresh is None:
            return {"error": "no active loop"}
        record = dict(prior, status=("recovered" if recovered else
                                     result["status"]), facts=facts)
        if result["status"] == "correct":
            record["correction_attempts"] = attempts + 1
        fresh.setdefault("runtime_eval", {})[key] = record
    event = ("runtime_eval_recovered" if recovered else
             f"runtime_eval_{result['status']}")
    tp.trace(ws, event, step=step, task=(task or {}).get("id"),
             missing=result.get("missing") or [], facts=facts,
             controls_fingerprint=controls_fingerprint())
    return {**result, "recovered": recovered, "facts": facts,
            "guidance": guidance(step)}
