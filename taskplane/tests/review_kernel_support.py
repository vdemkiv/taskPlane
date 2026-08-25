"""Canonical-review fixture for tests that exercise another gate rule.

The selective kernel's lifecycle/provenance tests use the complete leased-slot
protocol. Older sign-off, audit-cadence, and scope tests only need a valid
canonical revision so their unrelated assertion reaches the intended seam.
"""
import hashlib
import json
import os

import review
import review_evidence
import taskplane_lite as tp


def complete_evaluate_slots(ws, *, session_id="evaluate-fixture"):
    """Author every leased Evaluate result through its observed producer.

    Tests for a later gate rule use this helper to satisfy the real
    ReviewKernel boundary instead of bypassing it with free-form verdicts.
    """
    state = review._load_state(ws)
    store = review_evidence.ArtifactStore(ws)
    for index, slot in enumerate(state.get("slots") or []):
        lease = store.read(slot["lease"])
        brief = store.read(slot["brief"])
        payload = {
            **lease,
            "schema": "taskplane.lens-slot-output/v2",
            "authored_by": "lens-slot",
            "lens_results": [
                {"lens": lens_id, "verdict": "pass", "blockers": 0,
                 "checked_evidence": [
                     {"file": "src/a.py", "line": 1,
                      "claim": "reviewed governed source"}
                 ]}
                for lens_id in lease["lens_ids"]
            ],
            "findings": [],
        }
        if brief.get("language_references"):
            payload["references_applied"] = list(
                brief["language_references"])
        content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        event = {
            "session_id": session_id,
            "agent_id": f"{session_id}-{index}",
            "tool_name": "Write",
            "tool_input": {"file_path": slot["result_path"],
                           "content": content},
        }
        contract = {
            "task": brief["producer_contract"]["task"],
            "task_id": f"{session_id}-contract",
            "read_only": True,
            "write_allow": [slot["result_path"]],
        }
        task_slot = brief["producer_contract"]["task_slot"]
        review.register_slot_producer(
            ws, event=event, contract=contract, task_slot=task_slot)
        review.record_slot_write_observation(
            ws, event=event, contract=contract, task_slot=task_slot)
        path = os.path.join(ws, slot["result_path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(content)


def complete_review(ws, *, coverage, impact=None, tests=None, findings=None,
                    gate=None, report="# Engineering review\n\nNo blockers.\n"):
    store = review_evidence.ArtifactStore(ws)
    try:
        state = review._load_state(ws)
    except review.ReviewKernelError:
        run_id = hashlib.sha256(
            (os.path.realpath(ws) + "\0review-fixture").encode("utf-8")
        ).hexdigest()[:32]
        target_fp = hashlib.sha256(
            (os.path.realpath(ws) + "\0target").encode("utf-8")
        ).hexdigest()
        context_fp = hashlib.sha256(
            (os.path.realpath(ws) + "\0context").encode("utf-8")
        ).hexdigest()
        state = {"run_id": run_id, "status": "complete", "stage": "review",
                 "target": {"fingerprint": target_fp}}
    else:
        envelope = store.read(state["envelope"])
        target_fp = envelope["target_fingerprint"]
        context_fp = envelope["context_fingerprint"]

    rows = list(findings or [])
    findings_fp = review_evidence.content_fingerprint(
        {"result_fingerprints": [], "findings": rows})
    identity = {"target_fingerprint": target_fp,
                "context_fingerprint": context_fp,
                "findings_fingerprint": findings_fp,
                "canonical_revision": 1}
    tp.atomic_write_json(review_evidence._current_path(store), identity,
                         sort_keys=True)
    review._save_state(ws, {**state, "status": "complete", "stage": "review",
                            "revision": identity})

    os.makedirs(os.path.join(ws, ".em-review"), exist_ok=True)
    meta = {**identity, "lens_coverage": coverage,
            "impact": {} if impact is None else impact,
            "tests": ["true"] if tests is None else tests,
            "gate": gate or {"verdict": "recommend-pass"}}
    tp.atomic_write_json(
        os.path.join(ws, ".em-review", "findings.json"),
        {"meta": meta, "findings": rows}, sort_keys=True)
    with open(os.path.join(ws, ".em-review", "report.md"), "w",
              encoding="utf-8") as stream:
        stream.write(report)
    return identity
