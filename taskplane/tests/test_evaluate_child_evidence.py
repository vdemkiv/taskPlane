"""Public pass journey and semantic refusal matrix for Evaluate evidence."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from taskplane import evaluate_child_evidence as evidence
from taskplane import (
    evaluation_output, loop, run_artifacts, run_store, runnability, storage,
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
    test = obligations["tests"][0]
    edge = obligations["producer_consumer_edges"][0]
    test_design = {
        "schema": evidence.TEST_DESIGN_RESULT_SCHEMA,
        "producer_kind": evidence.TEST_DESIGN_PRODUCER,
        "reuse_key_digest": design["reuse_key_digest"],
        "current_value": [{
            **test, "classification": "protects-current-contract",
            "execution": _execution_ref(
                design, ["python3", "-m", "pytest", "-q", test["selector"]],
                "current:" + test["selector"]),
        }],
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
        }],
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
        loop.complete_evaluate_evidence_child(
            workspace=str(ROOT), artifact_root=str(root), run_id=run_id,
            assignment=assignment,
            result=results[assignment["producer_kind"]], work_units=2)

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
