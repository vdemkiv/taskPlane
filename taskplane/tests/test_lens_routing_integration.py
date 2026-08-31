"""Final cross-surface conformance for accepted drift D-0014 (LR-09)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from taskplane import delivery_policy
from taskplane import evaluation_output
from taskplane import review
from taskplane import runtime_eval
from taskplane.tests import run_lr10_parallel as parallel_runner


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


def _flatten_shards(shards: dict[str, tuple[str, ...]]) -> list[str]:
    return [selector for selectors in shards.values() for selector in selectors]


def test_lr09_parallel_profile_is_closed_exact_and_default_safe() -> None:
    default = parallel_runner.resolve_profile([])
    lr09 = parallel_runner.resolve_profile(["--profile", "lr09"])

    assert default.name == "lr10"
    assert default.shards == parallel_runner.SHARDS
    assert len(_flatten_shards(default.shards)) == 11
    assert lr09.name == "lr09"
    assert 3 <= len(lr09.shards) <= 5
    assigned = _flatten_shards(lr09.shards)
    assert len(assigned) == len(set(assigned)) == 14
    assert set(assigned) == {
        "taskplane/tests/test_delivery_policy.py",
        "taskplane/tests/test_lens_route_policy.py",
        "taskplane/tests/test_lens_route_telemetry.py",
        "taskplane/tests/test_expanded_route_authority_provider.py",
        "taskplane/tests/test_expanded_lens_route_authority.py",
        "taskplane/tests/test_review_routing.py",
        "taskplane/tests/test_evaluation_output_contract.py",
        "taskplane/tests/test_evidence_bundle.py",
        "taskplane/tests/test_runtime_eval_guidance.py",
        "taskplane/tests/test_focused_lens_routing.py",
        "taskplane/tests/test_loop.py",
        "taskplane/tests/test_agents_skills_focused_routing.py",
        "taskplane/tests/test_lens_routing_product_truth.py",
        "taskplane/tests/test_lens_routing_integration.py",
    }
    assert default.hermetic_pytest is False
    assert lr09.hermetic_pytest is True

    rejected = (
        ["--profile", "unknown"],
        ["--profile=lr09"],
        ["--profile", "lr09", "extra"],
        ["extra"],
    )
    for argv in rejected:
        with pytest.raises(ValueError, match="usage"):
            parallel_runner.resolve_profile(argv)


def test_lr09_shard_subprocess_is_argv_safe_and_hermetic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    class Process:
        returncode = 0

    def popen(argv: list[str], **kwargs: object) -> Process:
        observed["argv"] = argv
        observed.update(kwargs)
        return Process()

    monkeypatch.setenv("TASKPLANE_TASK", "must-not-leak")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    monkeypatch.setenv("PYTEST_PLUGINS", "host_plugin")
    process = parallel_runner._start(
        "proof", ("one.py", "two.py"), tmp_path,
        popen_factory=popen, hermetic_pytest=True,
    )

    assert isinstance(process, Process)
    assert observed["argv"] == [
        sys.executable, "-m", "pytest", "-q", "-x",
        "-p", "no:cacheprovider", "one.py", "two.py",
    ]
    assert observed["cwd"] == parallel_runner.ROOT
    assert observed["shell"] is False
    env = observed["env"]
    assert isinstance(env, dict)
    assert "TASKPLANE_TASK" not in env
    assert "PYTEST_ADDOPTS" not in env
    assert "PYTEST_PLUGINS" not in env
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert env["TMPDIR"] == env["TEMP"] == env["TMP"] == str(tmp_path)
    assert os.fspath(tmp_path) not in os.environ.get("PYTEST_ADDOPTS", "")


@pytest.mark.parametrize("returncode", [0, 3])
def test_runner_collects_all_results_and_cleans_owned_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    started: list[str] = []
    collected: list[str] = []

    class Process:
        def __init__(self, selector: str) -> None:
            self.selector = selector
            self.returncode = returncode if selector == "first.py" else 0

        def communicate(self, timeout: float) -> tuple[str, str]:
            collected.append(self.selector)
            return self.selector, ""

    def popen(argv: list[str], **_kwargs: object) -> Process:
        selector = argv[-1]
        started.append(selector)
        return Process(selector)

    parent = tmp_path / "runner"

    def roots(shards: dict[str, tuple[str, ...]]) -> tuple[Path, dict[str, Path]]:
        parent.mkdir()
        output: dict[str, Path] = {}
        for index, name in enumerate(shards, 1):
            child = parent / f"{index:02d}-{name}"
            child.mkdir()
            output[name] = child
        return parent, output

    monkeypatch.setattr(parallel_runner, "_create_temp_roots", roots)
    shards = {"first": ("first.py",), "second": ("second.py",)}
    returned_parent, results = parallel_runner.run_shards(
        shards, popen_factory=popen, clock=lambda: 100.0,
        hermetic_pytest=True,
    )

    assert started == ["first.py", "second.py"]
    assert collected == ["first.py", "second.py"]
    assert [row.status for row in results] == (
        ["passed", "passed"] if returncode == 0 else ["failed", "passed"]
    )
    assert returned_parent == parent
    assert not parent.exists()


@pytest.mark.parametrize(
    "terminal_signal",
    [KeyboardInterrupt(), SystemExit("cancelled"), SystemExit("handed-off")],
)
def test_runner_finally_terminates_collects_and_cleans_on_terminal_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    terminal_signal: BaseException,
) -> None:
    events: list[str] = []

    class Process:
        returncode = None

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            events.append("terminate")

        def kill(self) -> None:
            events.append("kill")

        def communicate(self, timeout: float) -> tuple[str, str]:
            events.append("collect")
            self.returncode = -15
            return "partial", "interrupted"

    parent = tmp_path / "runner"

    def roots(shards: dict[str, tuple[str, ...]]) -> tuple[Path, dict[str, Path]]:
        parent.mkdir()
        child = parent / "01-only"
        child.mkdir()
        return parent, {"only": child}

    monkeypatch.setattr(parallel_runner, "_create_temp_roots", roots)
    monkeypatch.setattr(
        parallel_runner, "_collect_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(terminal_signal),
    )

    with pytest.raises(type(terminal_signal)):
        parallel_runner.run_shards(
            {"only": ("only.py",)},
            popen_factory=lambda *_args, **_kwargs: Process(),
            clock=lambda: 100.0,
            hermetic_pytest=True,
        )

    assert events[:2] == ["terminate", "collect"]
    assert not parent.exists()


def test_runner_rejects_incomplete_or_nonpassing_aggregate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    shards = {"one": ("one.py",), "two": ("two.py",)}
    passed = parallel_runner.ShardResult(
        "01-one", "one", ("one.py",), "passed", 0, "ok", "", 1.0,
    )
    failed = parallel_runner.ShardResult(
        "02-two", "two", ("two.py",), "timeout", None,
        "partial", "timeout", 2.0,
    )

    with pytest.raises(RuntimeError, match="incomplete"):
        parallel_runner.validate_results(shards, [passed])
    false_pass = parallel_runner.ShardResult(
        "02-two", "two", ("two.py",), "passed", None, "", "", 2.0,
    )
    with pytest.raises(RuntimeError, match="false pass"):
        parallel_runner.validate_results(shards, [passed, false_pass])
    assert parallel_runner._render_results([passed, failed]) == 1
    output = capsys.readouterr().out
    assert output.index("01-one") < output.index("02-two")
