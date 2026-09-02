import copy
from pathlib import Path

import pytest

from taskplane import evaluate_child_evidence as evidence
from taskplane import evaluation_output


ROOT = Path(__file__).resolve().parents[2]


def _binding(**changes):
    value = {
        "requirement_id": "R-TEST",
        "candidate_sha": "1" * 40,
        "source_tree": "2" * 40,
        "design_fingerprint": "3" * 64,
        "plan_fingerprint": "4" * 64,
        "settings_digest": "5" * 64,
        "evaluator_attempt_id": "evaluate-attempt-1",
    }
    value.update(changes)
    return value


def _impact(**changes):
    value = {
        "schema": evidence.IMPACT_MANIFEST_SCHEMA,
        "implementation_files": ["taskplane/evaluation_output.py"],
        "test_files": ["taskplane/tests/test_evaluate_child_evidence.py"],
        "tests": [{
            "selector": (
                "taskplane/tests/test_evaluate_child_evidence.py::"
                "test_every_evaluator_starts_exactly_two_bound_evidence_"
                "producers_and_records_complete_lifecycle"
            ),
            "contract": "AC11",
        }],
        "producer_consumer_edges": [{
            "producer": "taskplane/evaluate_child_evidence.py",
            "consumer": "taskplane/evaluation_output.py",
            "selector": (
                "taskplane/tests/test_evaluate_child_evidence.py::"
                "test_evaluator_consumes_both_substantive_results_while_"
                "children_cannot_verdict_gate_or_repair"
            ),
            "freshness_inputs": ["candidate_sha", "source_tree"],
            "severed_edge": {
                "mutation": "remove one consumed result digest",
                "selector": (
                    "taskplane/tests/test_evaluate_child_evidence.py::"
                    "test_evaluator_consumes_both_substantive_results_while_"
                    "children_cannot_verdict_gate_or_repair"
                ),
            },
        }],
        "changed_interfaces": [{
            "producer": "taskplane/evaluate_child_evidence.py",
            "kind": "serialized",
            "slice": "evaluate-evidence",
            "fixture": {
                "path": "taskplane/tests/test_evaluate_child_evidence.py",
                "slice": "evaluate-evidence",
            },
        }],
        "failures": [{
            "id": "red-selector",
            "classification": "product",
            "classified_before_repair": True,
        }],
        "rejected_evidence_kinds": [
            "ceremonial", "source", "ast", "prose-shape", "byte-only"
        ],
    }
    value.update(changes)
    return value


def _results(assignments):
    language_assignment = next(
        row for row in assignments
        if row["producer_kind"] == evidence.LANGUAGE_PRODUCER
    )
    test_assignment = next(
        row for row in assignments
        if row["producer_kind"] == evidence.TEST_DESIGN_PRODUCER
    )
    language = {
        "schema": evidence.LANGUAGE_RESULT_SCHEMA,
        "producer_kind": evidence.LANGUAGE_PRODUCER,
        "assignment_digest": language_assignment["assignment_digest"],
        "language_coverage": [{
            "language": item["language"],
            "reference_id": item["reference"]["path"],
            "reference_sha256": item["reference"]["content_sha256"],
            "toolchain_id": item["toolchain"]["id"],
            "toolchain_fingerprint": item["toolchain"]["fingerprint"],
            "inspected_files": ["taskplane/evaluation_output.py"],
            "command_receipts": [{
                "command": item["required_commands"][0],
                "selectors": item["required_selectors"],
                "exit_code": 0,
                "passing_facts": 1,
            }],
            "findings": [],
        } for item in language_assignment["language_obligations"]],
    }
    test_design = {
        "schema": evidence.TEST_DESIGN_RESULT_SCHEMA,
        "producer_kind": evidence.TEST_DESIGN_PRODUCER,
        "assignment_digest": test_assignment["assignment_digest"],
        "current_value": [{
            "selector": test_assignment["test_obligations"]["tests"][0]["selector"],
            "classification": "protects-current-contract",
            "contract": "AC11",
            "evidence": "public evaluator attempt rejects a missing child",
        }],
        "producer_consumers": [{
            "producer": "taskplane/evaluate_child_evidence.py",
            "consumer": "taskplane/evaluation_output.py",
            "selector": test_assignment["test_obligations"]["producer_consumer_edges"][0]["selector"],
            "freshness_evidence": "candidate and source tree mutation is rejected",
            "severed_edge_evidence": "removing a result digest fails admission",
        }],
        "same_slice_fixtures": [{
            "producer": "taskplane/evaluate_child_evidence.py",
            "path": "taskplane/tests/test_evaluate_child_evidence.py",
            "slice": "evaluate-evidence",
        }],
        "failure_classifications": [{
            "id": "red-selector",
            "classification": "product",
            "classified_before_repair": True,
        }],
        "rejected_evidence": [{
            "kind": kind,
            "evidence": f"{kind} proof cannot establish runtime behavior",
        } for kind in test_assignment["test_obligations"]["rejected_evidence_kinds"]],
    }
    return {
        evidence.LANGUAGE_PRODUCER: language,
        evidence.TEST_DESIGN_PRODUCER: test_design,
    }


def _executed_run(binding=None, impact=None):
    assignments = evidence.prepare_assignments(
        ROOT, binding or _binding(), impact or _impact()
    )
    results = _results(assignments)
    receipts = []
    for assignment in assignments:
        receipts.extend(evidence.complete_lifecycle(
            assignment, results[assignment["producer_kind"]]
        ))
    return evidence.seal_evidence_run(assignments, receipts, results)


def test_every_evaluator_starts_exactly_two_bound_evidence_producers_and_records_complete_lifecycle():
    run = _executed_run()

    assert run["producer_count"] == 2
    assert run["catalog_lens_count"] == 0
    assert {row["producer_kind"] for row in run["producers"]} == {
        evidence.LANGUAGE_PRODUCER,
        evidence.TEST_DESIGN_PRODUCER,
    }
    for producer in run["producers"]:
        assert producer["lifecycle_kinds"] == list(evidence.LIFECYCLE_KINDS)
        assert producer["activity_count"] > 0
        assert producer["result_substantive_count"] > 0
        assert set(producer["binding"]) == set(evidence.BINDING_FIELDS)
        assert all(producer["binding"].values())


def test_language_quality_covers_every_impacted_language_and_fails_closed_on_missing_unsupported_or_ambiguous_mapping():
    assignments = evidence.prepare_assignments(ROOT, _binding(), _impact())
    language = next(row for row in assignments
                    if row["producer_kind"] == evidence.LANGUAGE_PRODUCER)
    assert [row["language"] for row in language["language_obligations"]] == ["python"]
    assert all(row["reference"]["content_sha256"] and
               row["toolchain"]["verdict"] == "runs"
               for row in language["language_obligations"])

    with pytest.raises(evidence.EvidenceContractError, match="unsupported"):
        evidence.prepare_assignments(
            ROOT, _binding(),
            _impact(implementation_files=["src/service.rs"]),
        )
    with pytest.raises(evidence.EvidenceContractError, match="duplicate"):
        evidence.prepare_assignments(
            ROOT, _binding(),
            _impact(implementation_files=[
                "taskplane/evaluation_output.py",
                "taskplane/evaluation_output.py",
            ]),
        )


def test_test_design_classifies_current_value_and_proves_wiring_freshness_same_slice_and_failure_classes():
    run = _executed_run()
    summary = run["results"][evidence.TEST_DESIGN_PRODUCER]

    assert summary["current_value_count"] == 1
    assert summary["producer_consumer_count"] == 1
    assert summary["severed_edge_count"] == 1
    assert summary["same_slice_fixture_count"] == 1
    assert summary["failure_class_count"] == 1
    assert summary["rejected_ceremonial_count"] == 5
    assert summary["substantive_count"] >= 10

    assignments = evidence.prepare_assignments(ROOT, _binding(), _impact())
    results = _results(assignments)
    results[evidence.TEST_DESIGN_PRODUCER]["rejected_evidence"][0]["evidence"] = ""
    receipts = []
    for assignment in assignments:
        receipts.extend(evidence.complete_lifecycle(
            assignment, results[assignment["producer_kind"]]
        ))
    with pytest.raises(evidence.EvidenceContractError, match="non-empty"):
        evidence.seal_evidence_run(assignments, receipts, results)


def test_evaluator_consumes_both_substantive_results_while_children_cannot_verdict_gate_or_repair():
    run = _executed_run()
    value = {
        "schema": evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
        "task": "P12-evaluator-evidence",
        "requirement": "R-TEST",
        "verdict": "pass",
        "evaluation": {"status": "complete", "reason_code": "none", "detail": "direct evidence checked"},
        "criteria": [{"criterion": "AC11", "status": "met", "evidence": "two substantive results consumed"}],
        "graph": {"dispositions": [], "requirements_checked": ["R-TEST"], "contracts_checked": ["contract:evaluate.evidence-consumption/v1"]},
        "failures": [],
    }
    attached = evaluation_output.attach_child_evidence(value, run)
    validated = evaluation_output.validate_evaluator_value(
        attached, expected_lenses=[], require_child_evidence=True
    )

    assert validated["child_evidence"]["evaluator"]["verdict_owner"] == "evaluator"
    assert all(row["consumed"] and row["substantive_count"] > 0
               for row in validated["child_evidence"]["results"].values())
    assert set(validated["child_evidence"]["children"]["forbidden_authorities"]) == set(evidence.FORBIDDEN_AUTHORITIES)

    forged = copy.deepcopy(attached)
    forged["child_evidence"]["results"][evidence.TEST_DESIGN_PRODUCER]["consumed"] = False
    with pytest.raises(evaluation_output.OutputValidationError,
                       match="consumed"):
        evaluation_output.validate_evaluator_value(
            forged, expected_lenses=[], require_child_evidence=True
        )


def test_exact_unchanged_evidence_reuse_avoids_reexecution_and_changed_binding_forces_fresh_checks():
    run = _executed_run()
    assignments = evidence.prepare_assignments(ROOT, _binding(), _impact())

    reused = evidence.reuse_or_execute(assignments, run)
    assert reused["executed"] is False
    assert reused["reason"] == "complete-content-identical-key"
    assert all(reused["results"][kind]["substantive_count"] > 0
               for kind in evidence.PRODUCER_KINDS)

    changed = evidence.prepare_assignments(
        ROOT, _binding(source_tree="9" * 40), _impact()
    )
    fresh = evidence.reuse_or_execute(changed, run)
    assert fresh == {
        "executed": True,
        "reason": "evidence-key-changed",
        "results": None,
    }
