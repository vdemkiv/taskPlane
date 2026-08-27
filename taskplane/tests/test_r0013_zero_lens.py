from __future__ import annotations

import subprocess

from taskplane import lens, loop, review
from taskplane.delivery_policy import validate_plan_mode


def _workspace(tmp_path):
    ws = tmp_path / "repo"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"],
                   cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ws,
                   check=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=ws,
                   check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws,
                          check=True, capture_output=True, text=True).stdout.strip()
    return ws, head


def _route():
    return {
        "lenses": [{
            "id": row["id"], "name": row["name"], "mode": "subagent",
            "tier": "deep", "verdict": "deep", "score": 10,
            "reasons": ["legacy route"], "evidence": ["fixture"],
            "checks": row.get("checks") or [],
            "looks_for": row.get("looks_for") or "",
        } for row in lens.load_catalog()["lenses"]],
        "context": {"status": "complete", "breadth": "routed"},
    }


def _start(ws, head, *, receipt=review._DELIVERY_MODE_AUTHORITY_UNSET):
    return review.start_review(
        str(ws), target={"fingerprint": "e" * 64, "head": head,
                         "task": "task-a"},
        graph={"meta": {"scanned_head": head,
                         "content_fingerprint": "f" * 64},
               "modules": {"src": {"files": ["src/feature.py"]}},
               "edges": []},
        impact={"touched": ["src"], "impacted": {}, "total_impacted": 1,
                "unknown": []},
        diff={"files": ["src/feature.py"], "changed_symbols": ["VALUE"]},
        runnability={"summary": "available", "checks": []},
        requirement={"id": "R-0013", "text": "final EM"},
        acceptance=["delivery is complete"], contracts=[], stage="review",
        task_type="feature", router=_route,
        delivery_mode_receipt=receipt)


def test_execution_time_em_uses_sealed_authority_and_zero_slots(tmp_path):
    ws, head = _workspace(tmp_path)
    receipt = validate_plan_mode(
        {"requirement": "R-0013", "delivery_mode": "build",
         "automatic_lenses": [], "plan_authority": "human:operator"},
        plan_fingerprint="a" * 64, source_sha="b" * 40)

    opened = _start(ws, head, receipt=receipt)
    state = review._load_state(str(ws), opened["run_id"])

    assert opened["slots"] == []
    assert opened["expected_lenses"] == []
    assert state["delivery_mode_receipt"] == receipt
    assert all(row["tier"] == "n/a" and row["mode"] == "none"
               for row in state["routing"]["lenses"])

    collected = loop.collect_review_bridge(
        str(ws), publish=False, run_id=opened["run_id"],
        evaluator_result={"findings": {}, "report_sha256": "c" * 64},
        producer_observation_fingerprint="d" * 64,
        collection_stage="EM", result_validator=lambda value: value)
    assert collected["status"] == "complete"
    assert collected["empty_lens_collection"]["stage"] == "EM"


def test_legacy_em_keeps_normal_lens_slots(tmp_path):
    ws, head = _workspace(tmp_path)
    opened = _start(ws, head)

    assert opened["slots"]
    assert "delivery_mode_receipt" not in opened
