"""Final cross-surface conformance for accepted drift D-0014 (LR-09)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from taskplane import delivery_policy
from taskplane import evaluation_output
from taskplane import lens_route_policy
from taskplane import review
from taskplane import runtime_eval


ROOT = Path(__file__).resolve().parents[2]
LR10_BOOTSTRAP_COMMIT = "8e261f03bf2c527d0042a3a5dd41f352f0cc40d3"


def _plan() -> dict:
    return json.loads((ROOT / "plan/tasks.json").read_text(encoding="utf-8"))


def _origin(stage: str) -> dict:
    return delivery_policy.create_execution_stage_origin_receipt(
        stage=stage,
        run_id="run-lr09-conformance",
        session_id="session-lr09-conformance",
        task_name=f"tp_{stage}_lr09",
        agent_id=f"{stage}-lr09-agent",
        dispatch_identity_fingerprint="a" * 64,
    )


def _attempt(stage: str, outcome: str) -> tuple[list[dict], list[dict]]:
    identity = {
        "stage": stage,
        "run_id": "run-lr09-conformance",
        "session_id": "session-lr09-conformance",
        "task_name": f"tp_{stage}_lr09",
        "agent_id": f"{stage}-lr09-agent",
    }
    native = [
        {"hook_event_name": "SubagentStart", **identity},
        {"hook_event_name": outcome, **identity},
    ]
    ledger = [
        {"event": "started", **identity},
        {"event": outcome, **identity},
    ]
    return native, ledger


def test_stage_policy_and_accepted_plan_authority_are_one_closed_boundary() -> None:
    plan = _plan()
    drift = plan["accepted_drift"]

    assert delivery_policy.ROUTED_LENS_STAGES == {
        "product", "design", "plan"
    }
    assert lens_route_policy.ROUTED_STAGES == {
        "product", "design", "plan"
    }
    assert delivery_policy.ZERO_LENS_STAGES == {
        "build", "fix", "evaluate", "em"
    }
    assert drift["id"] == "D-0014"
    assert drift["accepted_by"] == "human:vdemkiv"
    assert drift["historical_design_immutable"] is True

    dispositions = plan["plan_route"]["dispositions"]
    assert len(dispositions) == 26
    assert len({row["lens"] for row in dispositions}) == 26
    assert sum(row["disposition"] == "execute_light"
               for row in dispositions) == 4
    assert not [row for row in dispositions
                if row["disposition"] == "execute_deep"]


@pytest.mark.parametrize(
    "stage", ["build", "fix", "evaluate", "em"]
)
@pytest.mark.parametrize(
    "outcome", ["passed", "failed", "cancelled", "interrupted", "handed_off"]
)
def test_every_zero_lens_terminal_path_has_no_lens_worker_start(
    stage: str, outcome: str
) -> None:
    native, ledger = _attempt(stage, outcome)
    receipt = delivery_policy.validate_stage_lens_execution(
        stage=stage,
        native_trace=native,
        session_ledger=ledger,
        expected_origin_receipt=_origin(stage),
    )

    assert receipt["lens_execution_policy"] == "none"
    assert receipt["terminal_outcome"] == outcome
    assert receipt["lens_worker_start_count"] == 0


def test_evaluate_kernel_output_and_guidance_have_no_lens_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repo"
    changed = workspace / "src/service.py"
    changed.parent.mkdir(parents=True)
    changed.write_text("def changed():\n    return 2\n", encoding="utf-8")

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Evaluate must not route, retry, or seek authority")

    monkeypatch.setattr(review, "_focused_evaluate_route", forbidden)
    monkeypatch.setattr(review, "apply_expanded_route_authority", forbidden)
    kernel = review.start_review(
        str(workspace),
        target={"fingerprint": "a" * 64, "head": "abc123"},
        graph={
            "meta": {"scanned_head": "abc123", "content_fingerprint": "graph"},
            "modules": {"src": {"files": ["src/service.py"]}},
            "edges": [],
        },
        impact={
            "touched": ["src"], "impacted": {}, "total_impacted": 1,
            "unknown": [],
        },
        diff={"files": ["src/service.py"], "changed_symbols": ["changed"]},
        runnability={"summary": "available"},
        requirement={"id": "R-0001", "text": "zero-lens Evaluate"},
        acceptance=["direct evidence remains judged"],
        contracts=["contract:delivery.stage-lens-execution"],
        # ReviewKernel receives the changed delivery stage. ``build`` is the
        # loop's Evaluate target and must therefore open the D-0014 zero-slot
        # collector rather than a standalone review fan-out.
        stage="build",
        task_type="integration",
        router=forbidden,
        routing_content={"src/service.py": changed.read_text(encoding="utf-8")},
        design_contract={
            "schema": "taskplane.design/v1",
            "stage_policy": {"evaluate": {"selection": "focused"}},
        },
    )

    assert kernel["slots"] == []
    assert kernel["expected_lenses"] == []
    assert kernel["lens_execution_policy"] == "none"
    assert not ({
        "focused_route", "routing_decision", "dispositions", "leases",
        "retry_lenses", "lens_results",
    } & set(kernel))

    output_properties = evaluation_output.evaluator_output_schema()["properties"]
    assert not ({"lenses", "lens_routes", "slots", "dispositions"}
                & set(output_properties))

    guidance = runtime_eval.guidance("evaluate")
    guidance_text = json.dumps(guidance, sort_keys=True).lower()
    assert "zero-lens-evaluate-evidence" in guidance_text
    assert "exact diff" in guidance_text
    assert "provenance" in guidance_text
    assert "do not create or collect lens work" in guidance_text


def test_lr10_bootstrap_precedes_join_and_final_em_must_attribute_drift() -> None:
    present = subprocess.run(
        ["git", "merge-base", "--is-ancestor", LR10_BOOTSTRAP_COMMIT, "HEAD"],
        cwd=ROOT,
        check=False,
    )
    assert present.returncode == 0, (
        f"exact LR-10 bootstrap commit {LR10_BOOTSTRAP_COMMIT} is absent"
    )

    plan = _plan()
    assert plan["remaining_dispatch_chain"][-1] == "EM:accepted_drift-D-0014"
    assert plan["accepted_drift"]["final_em_obligation"] == (
        "surface accepted_drift with accepted_by human:vdemkiv"
    )
