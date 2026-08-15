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
from functools import lru_cache


SCHEMA = "taskplane.runtime-evals/v1"
GUIDANCE_SCHEMA = "taskplane.runtime-guidance/v1"
CONTROL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "taskplane", "references", "runtime-evals.json")
_VALID_CLASSES = {"mechanical", "recoverable", "irreversible"}
REVIEW_FACTS = (
    "graph_before_route", "shared_review_context",
    "selective_lens_mapping", "lens_results_collected")


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
