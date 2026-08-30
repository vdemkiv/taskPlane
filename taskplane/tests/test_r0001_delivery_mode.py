import json
from pathlib import Path
import subprocess

import pytest

from taskplane import (
    build_c,
    lens,
    loop,
    review,
    storage as runtime_storage,
    tp as tp_cli,
)
from taskplane.delivery_policy import (
    DeliveryPolicyError,
    automatic_lens_workers_for_dispatch,
    create_empty_lens_collection_receipt,
    validate_plan_mode,
)
from taskplane.evaluation_output import (
    OutputValidationError,
    canonical_bytes,
    validate_evaluator_value,
)
from taskplane.producer_observation import ProducerObservationError


SOURCE_SHA = "a" * 40
PLAN_FINGERPRINT = "b" * 64
OBSERVATION_FINGERPRINT = "c" * 64


def _build_receipt(**plan_overrides):
    plan = {
        "requirement": "R-0001",
        "delivery_mode": "build",
        "automatic_lenses": [],
        "plan_authority": "human:operator",
    }
    plan.update(plan_overrides)
    return validate_plan_mode(
        plan,
        plan_fingerprint=PLAN_FINGERPRINT,
        source_sha=SOURCE_SHA,
    )


def _evaluator_result(**overrides):
    result = {
        "schema": "taskplane.evaluator-output/v2",
        "task": "task-a",
        "requirement": "R-0001",
        "verdict": "pass",
        "evaluation": {"status": "complete", "reason_code": "none",
                       "detail": ""},
        "criteria": [],
        "graph": {
            "dispositions": [],
            "requirements_checked": ["R-0001"],
            "contracts_checked": ["contract:delivery-mode-receipt"],
        },
        "failures": [],
    }
    result.update(overrides)
    return result


def _empty_collection(result):
    return create_empty_lens_collection_receipt(
        run_id="run-a",
        task_id="task-a",
        stage="Evaluate",
        expected_lenses=[],
        collected_lenses=[],
        result=result,
        result_validator=validate_evaluator_value,
        producer_observation_fingerprint=OBSERVATION_FINGERPRINT,
    )


def _complete_review_route():
    return {
        "lenses": [
            {
                "id": row["id"],
                "name": row["name"],
                "mode": "subagent",
                "tier": "deep",
                "verdict": "deep",
                "score": 10,
                "reasons": ["would be selected without delivery authority"],
                "evidence": ["controlled complete mapper result"],
                "checks": row.get("checks") or [],
                "looks_for": row.get("looks_for") or "",
            }
            for row in lens.load_catalog()["lenses"]
        ],
        "context": {"status": "complete", "breadth": "routed"},
    }


def _start_evaluate_kernel(workspace: Path, receipt):
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()
    return review.start_review(
        str(workspace),
        target={
            "fingerprint": "e" * 64,
            "head": head,
            "task": "task-a",
        },
        graph={
            "meta": {
                "scanned_head": head,
                "content_fingerprint": "f" * 64,
            },
            "modules": {"src": {"files": ["src/feature.py"]}},
            "edges": [],
        },
        impact={
            "touched": ["src"],
            "impacted": {},
            "total_impacted": 1,
            "unknown": [],
        },
        diff={
            "files": ["src/feature.py"],
            "changed_symbols": ["VALUE"],
        },
        runnability={"summary": "available", "checks": []},
        requirement={"id": "R-0001", "text": "zero automatic lenses"},
        acceptance=["the feature is complete"],
        contracts=["contract:delivery-mode-receipt"],
        stage=loop.EVALUATE_ROUTE_STAGE,
        task_type="feature",
        router=_complete_review_route,
        delivery_mode_receipt=receipt,
    )


def _plan_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    (workspace / "plan").mkdir(parents=True)
    (workspace / "src").mkdir()
    (workspace / "src" / "feature.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "taskplane@example.test"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Taskplane Test"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "baseline"], cwd=workspace, check=True
    )
    (workspace / "plan" / "tasks.json").write_text(
        json.dumps(
            {
                "requirement": "R-0001",
                "delivery_mode": "build",
                "automatic_lenses": [],
                "plan_authority": "human:operator",
                "tasks": [
                    {
                        "id": "task-a",
                        "scope": ["src/**"],
                        "tests": "true",
                        "criteria": ["the feature is complete"],
                    }
                ],
            }
        )
    )
    initialized = loop.init(
        str(workspace),
        "delivery receipt lifecycle",
        spec_path="specs/spec.md",
        checkpoints=[],
    )
    assert initialized["step"] == "plan"
    return workspace


def _gate_plan_to_execute(workspace: Path) -> dict:
    gated = loop.gate(str(workspace), "pass")
    assert "error" not in gated
    assert gated["step"] == "execute"
    return loop.load(str(workspace))


def _ready_evaluate_workspace(root: Path):
    workspace = _plan_workspace(root)
    state = _gate_plan_to_execute(workspace)
    task = loop._current_task(state)
    opened = _start_evaluate_kernel(
        workspace, state["delivery_mode_receipt"]
    )
    task["status"] = "built"
    state["step"] = "evaluate"
    state.setdefault("review_kernel_runs", {})[
        loop._review_kernel_binding_key("evaluate", task)
    ] = {
        "schema": "taskplane.review-kernel-binding/v1",
        "run_id": opened["run_id"],
        "workspace": str(workspace),
        "stage": loop.EVALUATE_ROUTE_STAGE,
        "status": "ready",
    }
    loop.save(str(workspace), state)
    verdict_path = Path(runtime_storage.evaluation_path(str(workspace)))
    verdict_path.parent.mkdir(parents=True, exist_ok=True)
    verdict_path.write_bytes(canonical_bytes(_evaluator_result()))
    return workspace, opened


def test_plan_gate_requires_and_stamps_delivery_mode():
    receipt = _build_receipt()

    assert receipt["schema"] == "taskplane.delivery-mode-receipt/v1"
    assert receipt["requirement"] == "R-0001"
    assert receipt["plan_fingerprint"] == PLAN_FINGERPRINT
    assert receipt["mode"] == "build"
    assert receipt["automatic_lenses"] == []
    assert receipt["plan_authority"] == "human:operator"
    assert receipt["source_sha"] == SOURCE_SHA
    assert len(receipt["fingerprint"]) == 64

    with pytest.raises(DeliveryPolicyError, match="delivery mode"):
        _build_receipt(delivery_mode=None)


def test_build_mode_dispatch_creates_zero_automatic_lens_workers():
    created = []

    workers = automatic_lens_workers_for_dispatch(
        _build_receipt(), lambda lens: created.append(lens)
    )

    assert workers == ()
    assert created == []


def test_plan_gate_persists_receipt_to_subsequent_zero_lens_dispatch(tmp_path):
    workspace = _plan_workspace(tmp_path)
    state = _gate_plan_to_execute(workspace)
    receipt = state["delivery_mode_receipt"]

    action = loop.next_action(str(workspace))

    assert "error" not in action
    assert action["step"] == "execute"
    assert action["delivery_dispatch"]["delivery_mode_receipt"] == receipt
    assert action["delivery_dispatch"]["automatic_lens_workers"] == ()
    assert action["delivery_dispatch"]["automatic_lens_worker_count"] == 0
    assert action["lenses"] == []


def test_sever_delivery_mode_receipt_to_dispatch_fails_closed(
    tmp_path, monkeypatch
):
    worker_calls = []

    def forbidden(*_args, **_kwargs):
        worker_calls.append(True)
        raise AssertionError("legacy routing or worker construction ran")

    monkeypatch.setattr(loop.lens_router, "prime_scope", forbidden)
    monkeypatch.setattr(build_c, "authorize_delivery_dispatch", forbidden)

    for edge_failure in ("missing", "tampered"):
        workspace = _plan_workspace(tmp_path / edge_failure)
        state = _gate_plan_to_execute(workspace)
        if edge_failure == "missing":
            state.pop("delivery_mode_receipt")
            state["design_fingerprint"] = "d" * 64
        else:
            state["delivery_mode_receipt"]["source_sha"] = "d" * 40
        loop.save(str(workspace), state)

        action = loop.next_action(str(workspace))

        assert "build delivery mode refused before dispatch" in action["error"]

    assert worker_calls == []


def test_empty_expected_lenses_emits_successful_collection_receipt(tmp_path):
    workspace = _plan_workspace(tmp_path)
    opened = _start_evaluate_kernel(workspace, _build_receipt())
    started = review._load_state(str(workspace), opened["run_id"])

    collected = loop.collect_review_bridge(
        str(workspace),
        publish=False,
        run_id=opened["run_id"],
        evaluator_result=_evaluator_result(),
        producer_observation_fingerprint=OBSERVATION_FINGERPRINT,
    )
    completed = review._load_state(str(workspace), opened["run_id"])
    receipt = collected["empty_lens_collection"]

    assert opened["slots"] == []
    assert opened["expected_lenses"] == []
    assert opened["slot_conservation"]["status"] == "empty"
    assert started["expected_lenses"] == []
    assert started["slots"] == []
    assert "routing" not in started
    assert started["zero_lens_evaluation"] is True
    assert collected["status"] == "complete"
    assert completed["empty_lens_collection"] == receipt
    assert receipt["schema"] == "taskplane.empty-lens-collection/v1"
    assert receipt["expected_lenses"] == []
    assert receipt["collected_lenses"] == []
    assert receipt["status"] == "complete"
    assert receipt["producer_observation_fingerprint"] == OBSERVATION_FINGERPRINT
    assert len(receipt["result_fingerprint"]) == 64
    assert len(receipt["fingerprint"]) == 64


def test_cli_refuses_producer_observation_flag(tmp_path, capsys):
    with pytest.raises(SystemExit):
        tp_cli.main([
            "loop",
            "--workspace",
            str(tmp_path),
            "submit",
            "pass",
            "--producer-observation",
            "{}",
        ])

    assert "unrecognized arguments" in capsys.readouterr().err


def test_public_loop_submit_refuses_recorded_producer_double(tmp_path):
    class RecordedProducerDouble(dict):
        pass

    recorded = RecordedProducerDouble(schema="recorded-producer-double")
    with pytest.raises(TypeError, match="producer_observation"):
        loop.submit(
            str(tmp_path),
            "pass",
            producer_observation=recorded,
        )
    with pytest.raises(ProducerObservationError, match="external host"):
        loop.bind_producer_observation(
            {"step": "evaluate", "task": "task-a"},
            recorded,
            output_bytes=b"{}\n",
            output_schema_id="taskplane.evaluator-output/v2",
            output_contract_fingerprint="d" * 64,
        )


def test_missing_host_receipt_fails_before_collection_guidance_and_submission(
    tmp_path, monkeypatch
):
    workspace, opened = _ready_evaluate_workspace(tmp_path)
    calls = []

    monkeypatch.setattr(
        loop,
        "collect_review_bridge",
        lambda *_args, **_kwargs: calls.append("collection"),
    )
    monkeypatch.setattr(
        loop.runtime_eval,
        "guide_loop",
        lambda *_args, **_kwargs: calls.append("guidance"),
    )

    refused = loop.submit(str(workspace), "pass")
    state = loop.load(str(workspace))

    assert refused["submitted"] is False
    assert "external host producer receipt" in refused["error"]
    assert calls == []
    assert "_submission" not in state
    assert review._load_state(
        str(workspace), opened["run_id"]
    )["status"] == "ready"


@pytest.mark.parametrize("edge", ["missing", "tampered"])
def test_evaluate_review_kernel_severed_delivery_authority_fails_before_start(
    tmp_path, monkeypatch, edge
):
    workspace = _plan_workspace(tmp_path)
    receipt = _build_receipt()
    if edge == "missing":
        receipt = None
    else:
        receipt["source_sha"] = "d" * 40
    starts = []

    def forbidden_start(*_args, **_kwargs):
        starts.append(True)
        raise AssertionError("ReviewKernel state was persisted")

    monkeypatch.setattr(review, "_save_state", forbidden_start)

    with pytest.raises(review.ReviewKernelError, match="delivery-mode authority"):
        _start_evaluate_kernel(workspace, receipt)

    assert starts == []


def test_malformed_empty_lens_result_is_not_success():
    result = _evaluator_result()
    result.pop("verdict")
    with pytest.raises(OutputValidationError, match="missing verdict"):
        _empty_collection(result)


def test_empty_lens_path_never_enters_outage_resolution():
    receipt = _empty_collection(_evaluator_result())

    assert receipt["status"] == "complete"
    assert not any("outage" in key or "resolution" in key for key in receipt)


def test_plan_gate_receipt_is_the_build_dispatch_authority():
    state = {"requirement_id": "R-0001"}
    receipt = loop.stamp_plan_delivery_mode(
        state,
        {
            "requirement": "R-0001",
            "delivery_mode": "build",
            "automatic_lenses": [],
            "plan_authority": "human:operator",
        },
        plan_fingerprint=PLAN_FINGERPRINT,
        source_sha=SOURCE_SHA,
    )
    created = []

    dispatch = build_c.authorize_delivery_dispatch(
        receipt, lens_worker_factory=lambda lens: created.append(lens)
    )

    assert state["delivery_mode_receipt"] == receipt
    assert dispatch["delivery_mode_receipt"] == receipt
    assert dispatch["automatic_lens_workers"] == ()
    assert created == []


def test_review_empty_expected_set_uses_normal_collection_not_outage():
    outage_calls = []

    receipt = review.collect_expected_set(
        run_id="run-a",
        task_id="task-a",
        stage="Evaluate",
        expected_lenses=[],
        collected_lenses=[],
        result=_evaluator_result(),
        result_validator=validate_evaluator_value,
        producer_observation_fingerprint=OBSERVATION_FINGERPRINT,
        outage_resolver=lambda *_args, **_kwargs: outage_calls.append(True),
    )

    assert receipt["schema"] == "taskplane.empty-lens-collection/v1"
    assert receipt["status"] == "complete"
    assert outage_calls == []


def test_design_governed_missing_delivery_mode_never_uses_legacy_lens_fallback(
    monkeypatch,
):
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("lens routing or worker construction was invoked")

    monkeypatch.setattr(loop.lens_router, "prime_scope", forbidden)
    monkeypatch.setattr(build_c, "authorize_delivery_dispatch", forbidden)

    with pytest.raises(DeliveryPolicyError, match="delivery-mode receipt"):
        loop.build_dispatch_lens_routing(
            {
                "requirement_id": "R-0001",
                "design_fingerprint": "d" * 64,
            },
            {"id": "t05", "scope": ["taskplane/**"], "type": "integration"},
            workspace="/workspace",
        )

    assert calls == []
