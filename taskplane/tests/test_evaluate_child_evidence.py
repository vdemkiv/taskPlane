"""Public pass journey and semantic refusal matrix for Evaluate evidence."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess

import pytest

from taskplane import evaluate_child_evidence as evidence
from taskplane import (
    dispatch_telemetry, evaluation_output, host_native, loop,
    native_session_meter,
    run_artifacts, run_store, runnability, storage,
)
from taskplane import tp as tp_cli
from taskplane.settings import load_settings
from taskplane.tests.test_native_root_session import (
    AUTHORITY, _capability, _write_root,
)
from taskplane.tests.test_native_terminal_telemetry import (
    _write_codex_transcript,
)

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = "5" * 64


def _binding(attempt: str = "evaluate-attempt-1") -> dict:
    return {
        "task_id": "P12-evaluator-evidence", "requirement_id": "R-TEST",
        "candidate_sha": "1" * 40, "source_tree": "2" * 40,
        "design_fingerprint": "3" * 64, "plan_fingerprint": "4" * 64,
        "settings_digest": SETTINGS, "evaluator_attempt_id": attempt,
    }


def _impact() -> dict:
    selector = (
        "taskplane/tests/test_evaluate_child_evidence.py::"
        "test_evaluator_consumes_both_substantive_results_while_children_cannot_verdict_gate_or_repair"
    )
    return {
        "schema": evidence.IMPACT_MANIFEST_SCHEMA,
        "implementation_files": ["taskplane/evaluation_output.py"],
        "test_files": ["taskplane/tests/test_evaluate_child_evidence.py"],
        "tests": [{"selector": selector, "contract": "AC11"}],
        "producer_consumer_edges": [{
            "producer": "taskplane/evaluate_child_evidence.py",
            "consumer": "taskplane/evaluation_output.py", "selector": selector,
            "freshness_inputs": ["candidate_sha", "source_tree"],
            "severed_edge": {
                "mutation": "remove the durable result reference",
                "selector": selector,
            },
        }],
        "changed_interfaces": [{
            "producer": "taskplane/evaluate_child_evidence.py",
            "kind": "serialized", "slice": "evaluate-evidence",
            "fixture": {
                "path": "taskplane/tests/test_evaluate_child_evidence.py",
                "slice": "evaluate-evidence",
            },
        }],
        "failures": [{"id": "red-selector", "classification": "product",
                      "classified_before_repair": True}],
        "rejected_evidence_kinds": list(evidence.REJECTED_EVIDENCE_KINDS),
    }


def _probe(_root: str, languages: list[str], **_kwargs: object) -> list[dict]:
    assert languages == ["python"]
    commands = (
        ("lint", "ruff", ["python3", "-m", "ruff", "check"]),
        ("format", "ruff", ["python3", "-m", "ruff", "format", "--check"]),
        ("strict-typing", "mypy", ["python3", "-m", "mypy", "--strict"]),
        ("security-static", "bandit", ["python3", "-m", "bandit", "-r", "taskplane"]),
    )
    return [{
        "language": "python", "fingerprint": "9" * 64,
        "checks": [{"id": check_id, "tool": tool, "argv": argv,
                    "tool_version": "test-version", "verdict": runnability.RUNS}
                   for check_id, tool, argv in commands],
    }]


def _execution_ref(assignment: dict, argv: list[str], label: str) -> dict:
    payload = {
        "argv": argv, "label": label,
        "run_id": "run-evaluator-evidence",
        "task_id": assignment["binding"]["task_id"],
        "source_sha": assignment["binding"]["candidate_sha"],
        "plan_fingerprint": assignment["binding"]["plan_fingerprint"],
    }
    return {"authorization": "test-authority",
            "handle": "test:" + json.dumps(
                payload, sort_keys=True, separators=(",", ":"))}


def _governed_receipt(_workspace: str, authorization: str, handle: str, *,
                      assignment_binding: dict, argv: list[str]) -> dict:
    assert authorization == "test-authority" and handle.startswith("test:")
    payload = json.loads(handle.removeprefix("test:"))
    assert assignment_binding["task_id"] == payload["task_id"]
    assert argv == payload["argv"]
    return {
        "identity": {"run_id": payload["run_id"],
                     "task_id": payload["task_id"]},
        "source_sha": payload["source_sha"],
        "target_sha": payload["source_sha"],
        "plan_fingerprint": payload["plan_fingerprint"],
        "runtime_argv": payload["argv"], "state": "succeeded", "exit_code": 0,
        "receipt_digest": hashlib.sha256(handle.encode()).hexdigest(),
    }


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
         checkout: Path = ROOT) -> tuple[Path, str]:
    home = tmp_path / "home"
    monkeypatch.setenv("TASKPLANE_HOME", str(home))
    identity = storage.identity_from_remote("https://github.com/example/project.git")
    owner = run_store.RunStore(home=str(home))
    run_id = "run-evaluator-evidence"
    state = owner.create(
        identity, run_id=run_id, checkout=str(checkout),
        host={"kind": "codex"}, target={"kind": "workspace"})
    root = Path(state["paths"]["artifacts"])
    run_artifacts.create_manifest(root, binding=run_artifacts.create_binding(
        repository_id=identity.repo_id, run_id=run_id, stage_id="evaluate",
        stage_instance_id="evaluate-attempt-1",
        candidate={"id": "candidate", "fingerprint": "a" * 64,
                   "revision": "1" * 40, "source_tree": "2" * 40},
        settings_digest=SETTINGS, source_fingerprint="b" * 64))
    monkeypatch.setattr(runnability, "probe_language_quality_toolchains", _probe)
    monkeypatch.setattr(
        evidence.governed_commands, "governed_command_execution_evidence",
        _governed_receipt)
    return root, run_id


def _assign(root: Path, attempt: str = "evaluate-attempt-1",
            impact: dict | None = None, *, workspace: Path = ROOT) -> list[dict]:
    return evidence.prepare_assignments(
        workspace, _binding(attempt), impact or _impact(), artifact_root=root)


def _results(assignments: list[dict]) -> dict[str, dict]:
    language = next(row for row in assignments
                    if row["producer_kind"] == evidence.LANGUAGE_PRODUCER)
    design = next(row for row in assignments
                  if row["producer_kind"] == evidence.TEST_DESIGN_PRODUCER)
    quality = {
        "schema": evidence.LANGUAGE_RESULT_SCHEMA,
        "producer_kind": evidence.LANGUAGE_PRODUCER,
        "reuse_key_digest": language["reuse_key_digest"],
        "language_coverage": [{
            "language": item["language"], "reference_id": item["reference"]["path"],
            "reference_sha256": item["reference"]["content_sha256"],
            "toolchain_fingerprint": item["toolchain_fingerprint"],
            "inspected_files": item["implementation_files"],
            "command_receipts": [
                _execution_ref(language, command["argv"],
                               "quality:" + command["id"])
                for command in item["required_commands"]], "findings": [],
        } for item in language["language_obligations"]],
    }
    obligations = design["test_obligations"]
    test_design = {
        "schema": evidence.TEST_DESIGN_RESULT_SCHEMA,
        "producer_kind": evidence.TEST_DESIGN_PRODUCER,
        "reuse_key_digest": design["reuse_key_digest"],
        "current_value": [{
            **test, "classification": "protects-current-contract",
            "execution": _execution_ref(
                design, ["python3", "-m", "pytest", "-q", test["selector"]],
                "current:" + test["selector"]),
        } for test in obligations["tests"]],
        "producer_consumers": [{
            "producer": edge["producer"], "consumer": edge["consumer"],
            "selector": edge["selector"],
            "execution": _execution_ref(
                design, ["python3", "-m", "pytest", "-q", edge["selector"]],
                "edge:" + edge["producer"] + ":" + edge["consumer"]),
            "severed_edge_execution": _execution_ref(
                design, ["python3", "-m", "pytest", "-q",
                         edge["severed_edge"]["selector"]],
                "severed:" + edge["producer"] + ":" + edge["consumer"]),
        } for edge in obligations["producer_consumer_edges"]],
        "same_slice_fixtures": [{
            "producer": row["producer"], "path": row["fixture"]["path"],
            "slice": row["slice"],
        } for row in obligations["changed_interfaces"]],
        "failure_classifications": [{
            "id": row["id"], "classification": row["classification"],
            "reason": "candidate behavior contradicted the current contract",
            "owner": "product-code", "cluster": "evidence-admission",
        } for row in obligations["failures"]],
    }
    return {evidence.LANGUAGE_PRODUCER: quality,
            evidence.TEST_DESIGN_PRODUCER: test_design}


def _record(root: Path, assignments: list[dict], results: dict[str, dict], *,
            omit_terminal: str | None = None,
            reused: dict[str, dict] | None = None) -> None:
    for assignment in assignments:
        kind = assignment["producer_kind"]
        attempt_id = assignment["binding"]["evaluator_attempt_id"] + "-" + kind
        common = {"schema": evidence.LIFECYCLE_SCHEMA, "producer_kind": kind,
                  "assignment_digest": assignment["assignment_digest"],
                  "reuse_key_digest": assignment["reuse_key_digest"]}
        def append(event_type: str, receipt_kind: str, details: dict,
                   references: tuple[dict, ...] = ()) -> None:
            run_artifacts.append_activity(
                root, event_type=event_type, agent_attempt_id=attempt_id,
                worker_id=kind, task_id=assignment["binding"]["task_id"],
                lens="non-lens-" + kind,
                details={**common, "receipt_kind": receipt_kind, **details},
                evidence_references=references)
        append("assignment", "assignment", {"assignment": assignment})
        append("start", "start", {})
        append("progress", "activity", {"work_units": 2})
        entry = ((reused or {}).get(kind) or run_artifacts.publish_artifact(
            root, "validation", results[kind],
            metadata=evidence.validate_result(
                assignment, results[kind], workspace=ROOT,
                run_id=assignment["binding"].get("run_id",
                                                  "run-evaluator-evidence"))))
        execution = "reused" if reused else "executed"
        result_detail = {"execution": execution,
                         "result_fingerprint": entry["fingerprint"],
                         "result_sha256": entry["sha256"]}
        append("evidence-reference", "result", result_detail, (entry,))
        if omit_terminal != kind:
            append("terminal", "terminal",
                   {**result_detail, "outcome": "success"}, (entry,))


def _pass() -> dict:
    return {
        "schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
        "task": "P12-evaluator-evidence", "requirement": "R-TEST",
        "verdict": "pass",
        "evaluation": {"status": "complete", "reason_code": "none",
                       "detail": "durable evidence consumed"},
        "criteria": [{"criterion": "AC11", "status": "met",
                      "evidence": "two durable results consumed"}],
        "graph": {"dispositions": [], "requirements_checked": ["R-TEST"],
                  "contracts_checked": ["contract:evaluate.evidence-consumption/v1"]},
        "failures": [],
    }


def test_every_evaluator_starts_exactly_two_bound_evidence_producers_and_records_complete_lifecycle(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, run_id = _run(tmp_path, monkeypatch)
    assignments = loop.start_evaluate_evidence_children(
        workspace=str(ROOT), artifact_root=str(root), binding=_binding(),
        impact_manifest=_impact())
    assert [row["producer_kind"] for row in assignments] == [
        evidence.LANGUAGE_PRODUCER, evidence.TEST_DESIGN_PRODUCER]
    results = _results(assignments)
    for assignment in assignments:
        kind = assignment["producer_kind"]
        loop.observe_evaluate_evidence_child_start(
            artifact_root=str(root), assignment=assignment,
            dispatch_id="intent-" + kind,
            native_task_name="tp_evidence_" + kind.replace("-", "_"))
        loop.complete_evaluate_evidence_child(
            workspace=str(ROOT), artifact_root=str(root), run_id=run_id,
            assignment=assignment,
            result=results[kind], work_units=2)

    consumed = loop.consume_evaluate_evidence_before_pass(
        _pass(), artifact_root=str(root), run_id=run_id,
        evaluator_attempt_id="evaluate-attempt-1",
        expected_binding=assignments[0]["binding"])
    assert consumed["verdict"] == "pass"
    assert {row["producer_kind"] for row in
            consumed["child_evidence"]["producers"]} == {
        evidence.LANGUAGE_PRODUCER, evidence.TEST_DESIGN_PRODUCER}


def test_evaluator_consumes_both_substantive_results_while_children_cannot_verdict_gate_or_repair(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, run_id = _run(tmp_path, monkeypatch)
    first = _assign(root)
    _record(root, first, _results(first))
    attached = evaluation_output.attach_child_evidence(
        _pass(), run_id=run_id, evaluator_attempt_id="evaluate-attempt-1",
        expected_binding=first[0]["binding"])
    consumed = evaluation_output.validate_evaluator_value(
        attached, expected_lenses=[],
        expected_evidence_binding=first[0]["binding"])["child_evidence"]
    assert consumed["catalog_lens_count"] == 0
    assert {row["producer_kind"] for row in consumed["producers"]} == \
        set(evidence.PRODUCER_KINDS)
    assert all(row["consumed"] and row["substantive_count"] > 0
               for row in consumed["producers"])
    assert [row["id"] for row in first[0]["language_obligations"][0][
        "required_commands"]] == list(evidence.QUALITY_CHECK_IDS)

    second = _assign(root, "evaluate-attempt-2")
    reusable = {row["producer_kind"]: evidence.find_reusable_result(root, row)
                for row in second}
    assert all(reusable.values())
    _record(root, second, _results(second), reused=reusable)
    reused = evidence.consume_evidence(
        run_id=run_id, evaluator_attempt_id="evaluate-attempt-2")
    assert all(row["execution"] == "reused" for row in reused["producers"])


@pytest.mark.parametrize("case", [
    "missing-child", "incomplete-lifecycle", "foreign-consumption",
    "corrupt-result", "unavailable-tool", "claim-only", "missing-fixture",
    "nested-authority", "changed-reuse-key", "one-character-claim",
])
def test_language_quality_covers_every_impacted_language_and_fails_closed_on_missing_unsupported_or_ambiguous_mapping(
        case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, run_id = _run(tmp_path, monkeypatch)
    assignments = _assign(root)
    results = _results(assignments)
    if case == "missing-child":
        with pytest.raises(evaluation_output.OutputValidationError,
                           match="child evidence is required"):
            evaluation_output.validate_evaluator_value(_pass(), expected_lenses=[])
    elif case == "incomplete-lifecycle":
        _record(root, assignments, results,
                omit_terminal=evidence.TEST_DESIGN_PRODUCER)
        with pytest.raises(evidence.EvidenceContractError, match="lifecycle"):
            evidence.consume_evidence(
                run_id=run_id, evaluator_attempt_id="evaluate-attempt-1")
    elif case == "foreign-consumption":
        value = _pass()
        value["child_evidence"] = {
            "schema": evidence.CONSUMPTION_SCHEMA, "run_id": "foreign-run",
            "evaluator_attempt_id": "evaluate-attempt-1",
            "task_id": value["task"], "requirement_id": value["requirement"],
            "catalog_lens_count": 0, "producers": [],
        }
        with pytest.raises(evaluation_output.OutputValidationError, match="durable"):
            evaluation_output.validate_evaluator_value(value, expected_lenses=[])
    elif case == "corrupt-result":
        _record(root, assignments, results)
        entry = run_artifacts.load_manifest(root)["classes"]["validation"]["entries"][0]
        (root / entry["locator"]).write_text("{}\n", encoding="utf-8")
        with pytest.raises(evidence.EvidenceContractError, match="durable"):
            evidence.consume_evidence(
                run_id=run_id, evaluator_attempt_id="evaluate-attempt-1")
    elif case == "unavailable-tool":
        probe = _probe("", ["python"])
        probe[0]["checks"][2]["verdict"] = runnability.UNAVAILABLE
        monkeypatch.setattr(runnability, "probe_language_quality_toolchains",
                            lambda *_args, **_kwargs: probe)
        with pytest.raises(evidence.EvidenceContractError, match="unavailable"):
            _assign(root)
    elif case == "claim-only":
        result = results[evidence.TEST_DESIGN_PRODUCER]
        result["producer_consumers"][0]["execution"] = {
            "evidence_kind": "prose-shape", "claim": "x"}
        with pytest.raises(evidence.EvidenceContractError, match="governed"):
            evidence.validate_result(
                assignments[1], result, workspace=ROOT, run_id=run_id)
    elif case == "missing-fixture":
        impact = _impact()
        impact["changed_interfaces"][0]["fixture"]["path"] = \
            "fixtures/does-not-exist.json"
        with pytest.raises(evidence.EvidenceContractError, match="fixture"):
            _assign(root, impact=impact)
    elif case == "nested-authority":
        result = results[evidence.LANGUAGE_PRODUCER]
        result["language_coverage"][0]["capabilities"] = {"verdict": True}
        with pytest.raises(evidence.EvidenceContractError,
                           match="forbidden authority"):
            evidence.validate_result(
                assignments[0], result, workspace=ROOT, run_id=run_id)
    elif case == "changed-reuse-key":
        _record(root, assignments, results)
        impact = _impact()
        impact["tests"][0]["contract"] = "AC12"
        changed = _assign(root, "evaluate-attempt-2", impact)
        assert all(evidence.find_reusable_result(root, row) is None
                   for row in changed)
    else:
        result = results[evidence.TEST_DESIGN_PRODUCER]
        result["failure_classifications"][0]["reason"] = "x"
        with pytest.raises(evidence.EvidenceContractError, match="substantive"):
            evidence.validate_result(
                assignments[1], result, workspace=ROOT, run_id=run_id)


def test_current_binding_and_governed_execution_receipts_are_mandatory(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, run_id = _run(tmp_path, monkeypatch)
    assignments = _assign(root)
    results = _results(assignments)
    results[evidence.LANGUAGE_PRODUCER]["language_coverage"][0][
        "command_receipts"][0] = {
            "evidence_kind": "runtime", "exit_code": 0, "passing_facts": 1}
    with pytest.raises(evidence.EvidenceContractError, match="governed"):
        evidence.validate_result(
            assignments[0], results[evidence.LANGUAGE_PRODUCER],
            workspace=ROOT, run_id=run_id)
    results = _results(assignments)
    _record(root, assignments, results)
    attached = evaluation_output.attach_child_evidence(
        _pass(), run_id=run_id, evaluator_attempt_id="evaluate-attempt-1",
        expected_binding=assignments[0]["binding"])
    foreign = copy.deepcopy(assignments[0]["binding"])
    foreign["candidate_sha"] = "7" * 40
    with pytest.raises(evaluation_output.OutputValidationError,
                       match="current evaluator candidate"):
        evaluation_output.validate_evaluator_value(
            attached, expected_lenses=[], expected_evidence_binding=foreign)


def test_test_design_classifies_current_value_and_proves_wiring_freshness_same_slice_and_failure_classes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _run(tmp_path, monkeypatch)
    missing = _impact()
    missing["tests"][0]["selector"] = (
        "taskplane/tests/test_evaluate_child_evidence.py::does_not_exist")
    with pytest.raises(evidence.EvidenceContractError, match="collect"):
        _assign(root, impact=missing)

    unclassified = _impact()
    unclassified["failures"][0]["classification"] = None
    with pytest.raises(evidence.EvidenceContractError, match="classification"):
        _assign(root, impact=unclassified)

    impact = _impact()
    impact["tests"].append({
        "selector": (
            "taskplane/tests/test_evaluate_child_evidence.py::"
            "test_current_binding_and_governed_execution_receipts_are_mandatory"),
        "contract": "AC12",
    })
    assignments = _assign(root, impact=impact)
    result = _results(assignments)[evidence.TEST_DESIGN_PRODUCER]
    result["current_value"] = [result["current_value"][0],
                               copy.deepcopy(result["current_value"][0])]
    with pytest.raises(evidence.EvidenceContractError, match="reuses|covered"):
        evidence.validate_result(
            assignments[1], result, workspace=ROOT,
            run_id="run-evaluator-evidence")


def test_nonexistent_edge_or_severed_selector_refuses_assignment(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _run(tmp_path, monkeypatch)
    for location in ("edge", "severed"):
        impact = _impact()
        selector = ("taskplane/tests/test_evaluate_child_evidence.py::"
                    "does_not_exist_edge")
        if location == "edge":
            impact["producer_consumer_edges"][0]["selector"] = selector
        else:
            impact["producer_consumer_edges"][0]["severed_edge"]["selector"] = selector
        with pytest.raises(evidence.EvidenceContractError, match="collect"):
            _assign(root, impact=impact)


def test_public_evaluate_preparation_consumes_explicit_edges_and_refuses_severed_coverage(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = _run(tmp_path, monkeypatch)
    workspace = tmp_path / "loop"
    workspace.mkdir()
    state = {
        "run_id": "run-evaluator-evidence", "baseline": "1" * 40,
        "step": "evaluate", "design_fingerprint": "3" * 64,
        "plan_fingerprint": "4" * 64, "requirement_id": "R-TEST",
        "tasks": [], "goal": "explicit evidence edges",
    }
    loop.save(str(workspace), state)
    producers = ["taskplane/loop.py", "taskplane/runtime_eval.py"]
    selectors = [
        "taskplane/tests/test_evaluate_child_evidence.py::"
        "test_every_evaluator_starts_exactly_two_bound_evidence_producers_and_records_complete_lifecycle",
        "taskplane/tests/test_evaluate_child_evidence.py::"
        "test_public_evaluate_preparation_consumes_explicit_edges_and_refuses_severed_coverage",
    ]
    edges = [{
        "producer": producer,
        "consumer": "taskplane/tests/test_evaluate_child_evidence.py",
        "selector": selector,
        "freshness_inputs": [
            "candidate_sha", "source_tree", "impact_manifest_fingerprint"],
        "severed_edge": {
            "mutation": "remove the approved public edge",
            "selector": selector,
        },
    } for producer, selector in zip(producers, selectors)]
    task = {
        "id": "P13", "req": "R-TEST", "criteria": ["AC12"],
        "tests": "python3 -m pytest -q " + " ".join(selectors),
        "evaluation_evidence_edges": edges,
        "changed_interfaces": [{
            "producer": "taskplane/loop.py", "kind": "in-process",
            "slice": "P13", "fixture": None,
        }],
        "classified_failures": [{
            "id": "P13-F4", "classification": "mixed",
            "classified_before_repair": True,
        }],
    }
    monkeypatch.setattr(loop, "_run_artifact_root", lambda *_args: str(root))
    monkeypatch.setattr(loop, "_diff_files", lambda *_args: producers)

    severed = {**task, "evaluation_evidence_edges": edges[:-1]}
    with pytest.raises(ValueError, match="cover every changed producer"):
        loop._prepare_public_evaluate_evidence(
            str(workspace), str(ROOT), state, severed,
            evaluator_attempt_id="evaluate-attempt-1")

    route = loop._prepare_public_evaluate_evidence(
        str(workspace), str(ROOT), state, task,
        evaluator_attempt_id="evaluate-attempt-1")
    obligations = next(
        assignment["test_obligations"] for assignment in route["assignments"]
        if assignment["producer_kind"] == evidence.TEST_DESIGN_PRODUCER)
    assert obligations["producer_consumer_edges"] == edges


def test_public_next_action_observes_two_children_and_gate_consumes_them(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    workspace = tmp_path / "public-evaluate"
    for directory in ("taskplane", "tests", "plan"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    (workspace / "taskplane" / "producer_a.py").write_text(
        "VALUE = 1\n", encoding="utf-8")
    (workspace / "taskplane" / "producer_b.py").write_text(
        "VALUE = 1\n", encoding="utf-8")
    (workspace / "tests" / "test_public.py").write_text(
        "import pytest\n\n"
        "from taskplane import producer_a, producer_b\n\n"
        "def consume(value):\n"
        "    if value is None:\n"
        "        raise ValueError('producer edge is severed')\n"
        "    return value\n\n"
        "def test_producer_a():\n    assert consume(producer_a.VALUE) == 2\n\n"
        "def test_producer_b():\n    assert consume(producer_b.VALUE) == 2\n\n"
        "def test_producer_a_severed():\n"
        "    with pytest.raises(ValueError, match='severed'):\n"
        "        consume(None)\n\n"
        "def test_producer_b_severed():\n"
        "    with pytest.raises(ValueError, match='severed'):\n"
        "        consume(None)\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin",
         "https://github.com/example/project.git"],
        cwd=workspace, check=True)
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c",
         "user.email=test@example.invalid", "commit", "-qm", "base"],
        cwd=workspace, check=True)
    baseline = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True).strip()
    selectors = ["tests/test_public.py::test_producer_a",
                 "tests/test_public.py::test_producer_b"]
    severed_selectors = [
        "tests/test_public.py::test_producer_a_severed",
        "tests/test_public.py::test_producer_b_severed",
    ]
    producers = ["taskplane/producer_a.py", "taskplane/producer_b.py"]
    edges = [{
        "producer": producer, "consumer": "tests/test_public.py",
        "selector": selector,
        "freshness_inputs": [
            "candidate_sha", "source_tree", "impact_manifest_fingerprint"],
        "severed_edge": {
            "mutation": "remove the public producer-to-consumer value",
            "selector": severed_selector,
        },
    } for producer, selector, severed_selector in zip(
        producers, selectors, severed_selectors)]
    task = {
        "id": "P13", "req": "R-TEST", "status": "built", "deps": [],
        "scope": ["taskplane/**", "tests/**"],
        "criteria": ["public evidence composes"],
        "tests": "python3 -m pytest -q " + " ".join(
            selectors + severed_selectors),
        "evaluation_evidence_edges": edges,
        "changed_interfaces": [{
            "producer": producer, "kind": "serialized", "slice": "P13",
            "fixture": {"path": "tests/test_public.py", "slice": "P13"},
        } for producer in producers],
        "classified_failures": [{
            "id": "P13-F3", "classification": "product",
            "classified_before_repair": True,
        }],
    }
    plan_path = workspace / "plan" / "tasks.json"
    plan_path.write_text(json.dumps({"tasks": [task]}) + "\n",
                         encoding="utf-8")
    (workspace / "taskplane" / "producer_a.py").write_text(
        "VALUE = 2\n", encoding="utf-8")
    (workspace / "taskplane" / "producer_b.py").write_text(
        "VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c",
         "user.email=test@example.invalid", "commit", "-qm", "candidate"],
        cwd=workspace, check=True)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True).strip()
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=workspace,
        text=True).strip()

    settings = load_settings()
    run_id = "run-evaluator-evidence"
    identity = storage.resolve_repository_identity(str(workspace))
    owner = run_store.RunStore()
    owner_state = owner.create(
        identity, run_id=run_id, checkout=str(workspace),
        host={"kind": "codex"}, target={"kind": "workspace"})
    artifact_root = Path(owner_state["paths"]["artifacts"])
    artifact_binding = run_artifacts.create_binding(
        repository_id=identity.repo_id, run_id=run_id, stage_id="evaluate",
        stage_instance_id="evaluate-attempt-1",
        candidate={"id": "candidate", "fingerprint": hashlib.sha256(
            head.encode()).hexdigest(), "revision": head,
            "source_tree": tree},
        settings_digest=settings.digest, source_fingerprint="b" * 64)
    run_artifacts.create_manifest(artifact_root, binding=artifact_binding)
    canonical_task = json.loads(plan_path.read_text(
        encoding="utf-8"))["tasks"][0]
    state = {
        "run_id": run_id, "baseline": baseline, "step": "evaluate",
        "goal": "public Evaluate composition", "requirement_id": "R-TEST",
        "design_fingerprint": "3" * 64,
        "plan_fingerprint": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "settings_digest": settings.digest, "tasks": [canonical_task],
        "current_task": 0, "max_fix_cycles": 1, "checkpoints": [],
        "run_artifact_binding": artifact_binding,
    }
    loop.save(str(workspace), state)
    loop.prepare_delivery_root(
        str(workspace), seed_ref="waves/W1/root-seed.json", wave_id="W1",
        prepared_at="2026-09-02T04:00:00Z",
        operation_id="prepare-run-evaluator-evidence-W1",
        design={"path": "design/contract.json", "fingerprint": "3" * 64},
        plan={"path": "plan/tasks.json",
              "fingerprint": state["plan_fingerprint"]},
        pickups=[{"id": "P13", "write_scopes": producers,
                  "disjointness_receipt_fingerprint": "d" * 64}],
        outstanding_human_gates=[],
        predecessor_terminal_projection={"status": "none"})
    seed = json.loads((workspace / "waves" / "W1" /
                       "root-seed.json").read_text(encoding="utf-8"))
    start = host_native.start_root_session(
        _capability(workspace, settings.digest), seed, run_id=run_id,
        wave_id="W1", candidate_sha=baseline,
        settings_digest=settings.digest, session_pseudonym="f" * 64,
        started_at="2026-09-02T04:00:01Z", issuer_sequence=1,
        authority=AUTHORITY)
    transcript = workspace / "root.jsonl"
    observation = native_session_meter.seal_root_observation(
        _write_root(transcript, total=40_000, sequence=1), sequence=1,
        session_role="root", status_receipt_fingerprint=start["fingerprint"],
        authority=AUTHORITY)
    loop.open_delivery_wave(
        str(workspace), host_start_receipt=start,
        first_observation=observation, observation_authority=AUTHORITY)

    monkeypatch.setattr(runnability, "probe_language_quality_toolchains", _probe)
    monkeypatch.setattr(
        evidence.governed_commands, "governed_command_execution_evidence",
        _governed_receipt)
    monkeypatch.setattr(loop, "_review_kernel", lambda *_a, **_k: ({
        "status": "ready", "run_id": "evaluate-attempt-1", "slots": [],
        "expected_lenses": [], "zero_lens_evaluation": True,
    }, {"lenses": [], "context": {"status": "ready"}}))
    action = loop.next_action(
        str(workspace), root_observation_authority=AUTHORITY)
    assert "error" not in action, json.dumps({
        "action": action,
        "ledger": loop.load(str(workspace)).get("dispatch_telemetry"),
        "root": loop.load(str(workspace)).get("root_hygiene"),
    }, sort_keys=True)
    children = action["evaluate_child_evidence"]["child_dispatches"]
    assert len(children) == 2
    assert {row["assignment"]["producer_kind"] for row in children} == \
        set(evidence.PRODUCER_KINDS)

    for child in children:
        expected = tp_cli.tp.peek_expectation(
            str(workspace), child["task_name"], strict=True)
        assert expected is not None
        loop.record_native_dispatch_observation(
            str(workspace), expected=expected,
            native_task_name=child["task_name"])
    assignments = [row["assignment"] for row in children]
    results = _results(assignments)

    def stop(child: dict) -> None:
        transcript = workspace / (child["task_name"] + ".jsonl")
        _write_codex_transcript(
            transcript, label=child["task_name"], input_tokens=10,
            cached_tokens=0, output_tokens=2)
        event = {"cwd": str(workspace), "agent_id": child["task_name"],
                 "agent_type": child["task_name"], "turn_id": "turn-1",
                 "task_name": child["task_name"], "provider": "codex",
                 "agent_transcript_path": str(transcript),
                 "last_assistant_message": json.dumps(
                     results[child["assignment"]["producer_kind"]])}
        monkeypatch.setattr(tp_cli.sys, "stdin", io.StringIO(json.dumps(event)))
        assert tp_cli.cmd_subagent_stop(None) == 0
        assert capsys.readouterr().out.strip() == "{}"
        binding = next(
            row for row in loop.load(str(workspace))["dispatch_telemetry"]
            ["bindings"]
            if row["dispatch_id"] == child["dispatch_intent"]["intent_id"])
        assert binding["usage"]["total_tokens"] == 12
        assert binding["finalized_receipt_fingerprint"]

    stop(children[0])
    verdict = {
        "schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
        "task": "P13", "requirement": "R-TEST", "verdict": "pass",
        "evaluation": {"status": "complete", "reason_code": "none",
                       "detail": "two child results consumed"},
        "criteria": [{"criterion": "public evidence composes",
                      "status": "met", "evidence": "public journey"}],
        "graph": {"dispositions": [], "requirements_checked": ["R-TEST"],
                  "contracts_checked": []}, "failures": [],
    }
    verdict_path = Path(storage.evaluation_path(str(workspace)))
    verdict_path.parent.mkdir(parents=True, exist_ok=True)
    verdict_path.write_text(json.dumps(verdict), encoding="utf-8")

    monkeypatch.setattr(loop, "_producer_observation_errors",
                        lambda *_a, **_k: [])
    current = loop.load(str(workspace))
    incomplete = loop._evaluation_errors(
        str(workspace), current, current["tasks"][0])
    assert any("child evidence admission failed" in error.lower()
               for error in incomplete), incomplete
    blocked = loop.gate(str(workspace), "pass")
    assert "error" in blocked
    assert loop.load(str(workspace))["step"] == "evaluate"

    stop(children[1])
    attached = evaluation_output.attach_child_evidence(
        verdict, run_id=run_id, evaluator_attempt_id="evaluate-attempt-1",
        expected_binding=assignments[0]["binding"])
    verdict_path.write_text(json.dumps(attached), encoding="utf-8")
    current = loop.load(str(workspace))
    remaining = loop._evaluation_errors(
        str(workspace), current, current["tasks"][0])
    assert not any("child evidence admission failed" in error.lower()
                   for error in remaining), remaining
    outstanding = hashlib.sha256(json.dumps(
        {"stage": "evaluate", "tasks": ["P13"]}, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    preserved = hashlib.sha256(json.dumps(
        {"run_id": current["run_id"], "baseline": current["baseline"],
         "settings_digest": current["settings_digest"]}, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    next_admission = loop.admit_native_dispatch(
        str(workspace), observation_authority=AUTHORITY,
        dispatch={
            "dispatch_id": "f" * 64, "thread_id": "next-evaluator-child",
            "thread_type": "evaluator", "task_id": "P13",
            "dependencies": [], "shared_owner": None,
            "started_at": 0, "ended_at": 0,
            "wait_duration_seconds": 0, "correction_count": 0,
            "events": [],
        },
        current_stage="evaluate",
        outstanding_set_fingerprint=outstanding,
        preserved_context_fingerprint=preserved)
    assert next_admission["dispatch_allowed"] is True
    monkeypatch.setattr(loop, "_persist_reanchor_authority",
                        lambda *_a, **_k: ({"fingerprint": "e" * 64}, head))
    passed = loop.gate(str(workspace), "pass")
    assert "child evidence admission failed" not in json.dumps(passed).lower()
    if "error" not in passed:
        assert loop.load(str(workspace))["tasks"][0]["status"] == "passed"


@pytest.mark.parametrize("scenario", (
    "missing-binding", "first-failure", "conflicting-replay",
    "foreign-ledger", "root-settings", "route-binding",
    "current-manifest",
))
def test_evidence_child_stop_requires_successful_current_terminal_authority(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str], scenario: str) -> None:
    root, run_id = _run(tmp_path, monkeypatch)
    assignments = loop.start_evaluate_evidence_children(
        workspace=str(ROOT), artifact_root=str(root), binding=_binding(),
        impact_manifest=_impact())
    assignment = assignments[0]
    result = _results(assignments)[assignment["producer_kind"]]
    task_name = "tp_evaluator_p13_evidence_" + scenario.replace("-", "_")
    dispatch_id = "f" * 64
    ledger = dispatch_telemetry.new_ledger(
        run_id=run_id, source_sha="1" * 40,
        design_fingerprint="3" * 64, plan_fingerprint="4" * 64,
        started_at=0)
    dispatch_telemetry.bind_dispatch(ledger, {
        "dispatch_id": dispatch_id, "thread_id": task_name,
        "thread_type": "evaluator", "task_id": _binding()["task_id"],
        "dependencies": [], "shared_owner": None,
        "started_at": 0, "ended_at": 0,
        "wait_duration_seconds": 0, "correction_count": 0, "events": [],
    })
    route = {
        "schema": "taskplane.evaluate-evidence-route/v1",
        "run_id": run_id, "workspace": str(ROOT),
        "artifact_root": str(root),
        "evaluator_attempt_id": "evaluate-attempt-1",
        "binding": assignment["binding"], "assignments": assignments,
        "child_dispatches": [{
            "task_name": task_name, "assignment": assignment,
            "dispatch_intent": {"intent_id": dispatch_id},
        }],
    }
    loop.save(str(tmp_path), {
        "run_id": run_id, "step": "evaluate", "baseline": "1" * 40,
        "design_fingerprint": "3" * 64, "plan_fingerprint": "4" * 64,
        "settings_digest": _binding()["settings_digest"],
        "tasks": [{"id": _binding()["task_id"], "deps": []}],
        "current_task": 0, "evaluate_child_evidence": route,
        "dispatch_telemetry": ledger,
    })
    expected = {
        "kind": "step", "agent": loop.STEP_ROLE["evaluate"],
        "ref": _binding()["task_id"], "intent_id": dispatch_id,
        "intent_run_id": run_id,
    }
    loop.record_native_dispatch_observation(
        str(tmp_path), expected=expected, native_task_name=task_name)
    with loop.mutate(str(tmp_path)) as locked:
        dispatch_telemetry.configure_root_admission(
            locked["dispatch_telemetry"], root_session_settings={
                "resume": "forbidden", "seed": "digest-only",
                "seed_budget_tokens": 100, "root_budget_tokens": 1000,
            }, settings_digest=_binding()["settings_digest"])
    transcript = tmp_path / (task_name + ".jsonl")
    _write_codex_transcript(
        transcript, label=task_name, input_tokens=10,
        cached_tokens=0, output_tokens=2)
    if scenario == "missing-binding":
        with loop.mutate(str(tmp_path)) as locked:
            locked["dispatch_telemetry"]["bindings"] = []
            locked["dispatch_telemetry"]["revision"] += 1
            dispatch_telemetry.validate_ledger(locked["dispatch_telemetry"])
    elif scenario == "foreign-ledger":
        state = loop.load(str(tmp_path))
        local = next(row for row in state["dispatch_telemetry"]["bindings"]
                     if row["dispatch_id"] == dispatch_id)
        foreign = dispatch_telemetry.new_ledger(
            run_id="foreign-run", source_sha="9" * 40,
            design_fingerprint="8" * 64, plan_fingerprint="7" * 64,
            started_at=0)
        fields = (
            "dispatch_id", "thread_id", "thread_type", "task_id",
            "dependencies", "shared_owner", "started_at", "ended_at",
            "wait_duration_seconds", "correction_count", "events",
        )
        dispatch_telemetry.bind_dispatch(
            foreign, {key: copy.deepcopy(local[key]) for key in fields})
        with loop.mutate(str(tmp_path)) as locked:
            locked["dispatch_telemetry"] = foreign
    elif scenario == "root-settings":
        with loop.mutate(str(tmp_path)) as locked:
            locked["dispatch_telemetry"].pop("root_admission")
            locked["dispatch_telemetry"]["revision"] += 1
            dispatch_telemetry.validate_ledger(locked["dispatch_telemetry"])
    elif scenario == "route-binding":
        with loop.mutate(str(tmp_path)) as locked:
            locked["evaluate_child_evidence"]["binding"][
                "source_tree"] = "8" * 40
    elif scenario == "current-manifest":
        assignment = copy.deepcopy(assignment)
        assignment["binding"]["source_tree"] = "8" * 40
        obligations = {
            "implementation_files": assignment["implementation_files"],
            "language_obligations": assignment["language_obligations"],
        }
        assignment["reuse_key_digest"] = evidence._reuse_key(
            assignment["producer_kind"], assignment["binding"],
            obligations, assignment["ledger_binding_fingerprint"])
        assignment["assignment_digest"] = evidence._assignment_digest(
            assignment)
        result = _results([assignment, assignments[1]])[
            assignment["producer_kind"]]
        with loop.mutate(str(tmp_path)) as locked:
            route = locked["evaluate_child_evidence"]
            route["binding"] = copy.deepcopy(assignment["binding"])
            route["assignments"][0] = copy.deepcopy(assignment)
            route["child_dispatches"][0]["assignment"] = copy.deepcopy(
                assignment)
    event = {
        "cwd": str(tmp_path), "agent_id": task_name,
        "agent_type": task_name, "task_name": task_name,
        "turn_id": "turn-success", "provider": "codex",
        "status": "success", "agent_transcript_path": str(transcript),
        "last_assistant_message": json.dumps(result),
    }
    if scenario == "conflicting-replay":
        monkeypatch.setattr(
            tp_cli.sys, "stdin", io.StringIO(json.dumps(event)))
        assert tp_cli.cmd_subagent_stop(None) == 0
        assert capsys.readouterr().out.strip() == "{}"

    def artifact_bytes() -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for artifact_class in ("agent-activity", "validation")
            for path in sorted((root / artifact_class).iterdir())
            if path.is_file()
        }

    before = artifact_bytes()
    if scenario in {"first-failure", "conflicting-replay"}:
        event.update({"turn_id": "turn-failure", "status": "failure"})
    monkeypatch.setattr(tp_cli.sys, "stdin", io.StringIO(json.dumps(event)))

    assert tp_cli.cmd_subagent_stop(None) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["decision"] == "block"
    expected_reason = {
        "missing-binding": "exact native dispatch binding",
        "first-failure": "successful exact terminal outcome",
        "conflicting-replay": "successful exact terminal outcome",
        "foreign-ledger": "current evidence authority",
        "root-settings": "current evidence authority",
        "route-binding": "current route binding",
        "current-manifest": "durable child assignment is foreign",
    }[scenario]
    assert expected_reason in output["reason"]
    assert artifact_bytes() == before
    sealed = loop.load(str(tmp_path))["dispatch_telemetry"]
    if scenario == "first-failure":
        assert sealed["dispatches"][0]["events"] == [{
            "kind": "failed", "sequence": 1}]
    elif scenario == "conflicting-replay":
        assert sealed["dispatches"][0]["events"] == [{
            "kind": "complete", "sequence": 1}]
    elif scenario == "foreign-ledger":
        assert sealed["run_id"] == "foreign-run"


def test_one_receipt_cannot_cover_freshness_and_severed_edge(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, run_id = _run(tmp_path, monkeypatch)
    assignments = _assign(root)
    result = _results(assignments)[evidence.TEST_DESIGN_PRODUCER]
    edge = result["producer_consumers"][0]
    edge["severed_edge_execution"] = edge["execution"]
    with pytest.raises(evidence.EvidenceContractError, match="reuses"):
        evidence.validate_result(
            assignments[1], result, workspace=ROOT, run_id=run_id)


def test_exact_unchanged_evidence_reuse_avoids_reexecution_and_changed_binding_forces_fresh_checks(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    fixture = checkout / "test_sample.py"
    fixture.write_text("def test_current():\n    assert True\n", encoding="utf-8")
    impact = _impact()
    selector = "test_sample.py::test_current"
    impact["test_files"] = ["test_sample.py"]
    impact["tests"] = [{"selector": selector, "contract": "AC12"}]
    impact["producer_consumer_edges"][0]["selector"] = selector
    impact["producer_consumer_edges"][0]["severed_edge"]["selector"] = selector
    impact["changed_interfaces"][0]["fixture"]["path"] = "test_sample.py"
    root, run_id = _run(tmp_path, monkeypatch, checkout=checkout)
    first = _assign(root, impact=impact, workspace=checkout)
    _record(root, first, _results(first))
    fixture.write_text("def test_current():\n    assert 1 == 1\n", encoding="utf-8")
    with pytest.raises(evidence.EvidenceContractError, match="changed after assignment"):
        evidence.consume_evidence(
            run_id=run_id, evaluator_attempt_id="evaluate-attempt-1")
    changed = _assign(
        root, "evaluate-attempt-2", impact=impact, workspace=checkout)
    changed_design = next(
        row for row in changed
        if row["producer_kind"] == evidence.TEST_DESIGN_PRODUCER)
    assert evidence.find_reusable_result(root, changed_design) is None
    _record(root, changed, _results(changed))
    fixture.unlink()
    with pytest.raises(evidence.EvidenceContractError, match="missing after assignment"):
        evidence.consume_evidence(
            run_id=run_id, evaluator_attempt_id="evaluate-attempt-2")

    stable_root, _ = _run(tmp_path / "stable", monkeypatch)
    original = _assign(stable_root)
    _record(stable_root, original, _results(original))
    for attempt in ("evaluate-attempt-2", "evaluate-attempt-3"):
        reused_assignments = _assign(stable_root, attempt)
        reused = {row["producer_kind"]:
                  evidence.find_reusable_result(stable_root, row)
                  for row in reused_assignments}
        assert all(reused.values())
        _record(stable_root, reused_assignments, _results(reused_assignments),
                reused=reused)
