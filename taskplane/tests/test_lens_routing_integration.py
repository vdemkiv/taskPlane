"""Shared lens CLI protocol and execution-stage zero-lens policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taskplane import delivery_policy
from taskplane import evaluation_output
from taskplane import review
from taskplane import runtime_eval


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


@pytest.fixture
def lens_workspace(tmp_path, git_ws):
    import subprocess
    workspace = git_ws(tmp_path / "repo")
    source = workspace / "service.py"
    source.write_text("def greeting(): return 'hello'\n", encoding="utf-8")
    subprocess.run(["git", "add", "service.py"], cwd=workspace, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-qm", "base"],
                   cwd=workspace, check=True)
    source.write_text("def greeting(): return 'welcome'\n", encoding="utf-8")
    return workspace


def test_cli_route_applies_signals_to_the_explicit_repository(lens_workspace, capsys):
    from taskplane import tp as cli
    from taskplane import lens
    workspace = lens_workspace
    assert cli.main(["lens", "route", "--workspace", str(workspace), "--json"]) == 0
    routing = json.loads(capsys.readouterr().out)
    assert {row["id"] for row in routing["lenses"]} == {
        row["id"] for row in lens.load_catalog()["lenses"]}
    assert all(row.get("verdict") for row in routing["lenses"])


@pytest.mark.parametrize("host_environment", [
    {}, {"TASKPLANE_WORKFLOWS": "1"},
    {"CODEX_THREAD_ID": "fixture-thread", "TASKPLANE_WORKFLOWS": "1"},
])
def test_cli_dispatch_binds_the_shared_plan_on_every_host(
        lens_workspace, capsys, monkeypatch, host_environment):
    from taskplane import tp as cli
    from taskplane import review_evidence
    for key in ("CODEX_HOME", "CODEX_THREAD_ID", "TASKPLANE_WORKFLOWS",
                "CLAUDE_CODE_WORKFLOWS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in host_environment.items():
        monkeypatch.setenv(key, value)
    assert cli.main(["lens", "dispatch", "--workspace", str(lens_workspace)]) == 0
    payload = json.loads(capsys.readouterr().out)
    store = review_evidence.ArtifactStore(str(lens_workspace))
    plan = store.read(payload["plan"])
    assert plan["schema"] == "taskplane.lens-plan/v1"
    assert payload["dispatch"]
    assert {row["slot_id"] for row in payload["dispatch"]} == {
        row["slot_id"] for row in plan["slots"]}
    assert len({row["result_path"] for row in payload["dispatch"]}) == len(plan["slots"])
    for emitted, sealed in zip(payload["dispatch"], plan["dispatch"]):
        assert emitted["brief"] == sealed["brief"]
        brief = store.read(emitted["brief"])
        assert brief["methodology"]["id"] in emitted["lens_ids"]
        assert brief["producer_contract"]["write_allow"] == [emitted["result_path"]]
