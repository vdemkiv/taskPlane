"""Deterministic runtime guidance for dynamic model execution.

The control data says which workflow invariants matter.  It deliberately says
nothing about expected model wording, finding counts, or transcript bytes.
Live execution may vary; this module observes only machine-owned workflow facts
and returns one bounded correction before a repeated drift blocks.
"""

from __future__ import annotations

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

_LIFECYCLE_TERMINAL = {"success", "failed", "timeout", "cancelled",
                       "unavailable"}
_VALIDATION_STATUSES = {"valid", "invalid", "unavailable"}
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|"
    r"CREDENTIAL|PRIVATE_KEY)[A-Z0-9_]*)\s*=\s*[^\s,;]+")
_ABSOLUTE_PATH = re.compile(
    r"(?:(?:[A-Za-z]:[\\/])|/)(?:[^\s,;]+[\\/])*[^\s,;]*")


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


def review_facts(ws: str, step: str) -> dict:
    """Machine-owned ReviewKernel facts used by Evaluate and final EM."""
    expected_stage = "build" if step == "evaluate" else "review"
    facts = {key: False for key in REVIEW_FACTS}
    try:
        import review
        import review_evidence

        state = review._load_state(ws)
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
                    "lenses": [{key: row.get(key) for key in
                                ("lens", "verdict", "blockers")}
                               for row in verdict.get("lenses") or []
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
            except Exception:
                facts["output_schema_validated"] = False
            # The outer verdict is admissible only after the canonical leased
            # evidence beneath it has an observed producer/write receipt and
            # has been collected into one revision.
            facts["output_producer_observed"] = facts[
                "lens_results_collected"]
    except Exception:
        pass
    return facts


def collect_review_if_ready(ws: str, step: str) -> None:
    """Seal authored leased results before the submission checkpoint.

    Collection used to happen only inside the later gate.  The automatic
    guide runs at submission, so it performs the same deterministic collect
    when a matching kernel is ready. Missing or invalid results remain facts
    for the bounded correction; they are never synthesized here.
    """
    expected_stage = "build" if step == "evaluate" else "review"
    try:
        import review

        state = review._load_state(ws)
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
    if step in {"evaluate", "em"}:
        collect_review_if_ready(review_ws, step)
    facts = review_facts(review_ws, step) if step in {"evaluate", "em"} else {}
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
