"""Fail-closed, non-lens evidence contracts for an Evaluate attempt.

Evaluate still makes the verdict directly.  This module only binds and
validates two read-only evidence producers: language code quality and test
design.  It deliberately contains no dispatch, gate, mutation, classification
authority, or repair API.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

if __package__:
    from . import lens, runnability, test_strategy
else:  # pragma: no cover - direct plugin module loading
    import lens
    import runnability
    import test_strategy


IMPACT_MANIFEST_SCHEMA = "taskplane.evaluate-impact-manifest/v1"
ASSIGNMENT_SCHEMA = "taskplane.evaluate-child-assignment/v1"
LIFECYCLE_SCHEMA = "taskplane.evaluate-child-lifecycle/v1"
LANGUAGE_RESULT_SCHEMA = "taskplane.evaluate-language-code-quality/v1"
TEST_DESIGN_RESULT_SCHEMA = "taskplane.evaluate-test-design/v1"
EVIDENCE_RUN_SCHEMA = "taskplane.evaluate-child-evidence-run/v1"
CONSUMPTION_SCHEMA = "taskplane.evaluate-evidence-consumption/v1"

LANGUAGE_PRODUCER = "language-code-quality"
TEST_DESIGN_PRODUCER = "test-design"
PRODUCER_KINDS = (LANGUAGE_PRODUCER, TEST_DESIGN_PRODUCER)
LIFECYCLE_KINDS = ("assignment", "start", "activity", "result", "terminal")
BINDING_FIELDS = (
    "requirement_id",
    "candidate_sha",
    "source_tree",
    "design_fingerprint",
    "plan_fingerprint",
    "settings_digest",
    "evaluator_attempt_id",
    "impact_manifest_fingerprint",
)
FORBIDDEN_AUTHORITIES = (
    "verdict",
    "gate",
    "dispatch",
    "mutation",
    "delivery-classification",
    "repair",
)
_FORBIDDEN_RESULT_FIELDS = {
    "verdict", "gate", "dispatch", "mutation", "delivery_classification",
    "delivery-classification", "repair",
}


class EvidenceContractError(ValueError):
    """Evidence cannot authorize an evaluator pass."""


def _canonical(value: Any) -> bytes:
    try:
        text = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EvidenceContractError(f"evidence is not canonical JSON: {exc}") \
            from None
    return text.encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _nonempty(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceContractError(f"{context} must be non-empty")
    return value


def _unique_strings(value: Any, context: str, *, allow_empty=False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise EvidenceContractError(f"{context} must be a non-empty list")
    rows = [_nonempty(item, context) for item in value]
    if len(rows) != len(set(rows)):
        raise EvidenceContractError(f"{context} contains duplicate values")
    return rows


def impact_manifest_fingerprint(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping) or \
            value.get("schema") != IMPACT_MANIFEST_SCHEMA:
        raise EvidenceContractError(
            f"impact manifest schema must be {IMPACT_MANIFEST_SCHEMA}"
        )
    implementation = _unique_strings(
        value.get("implementation_files"), "implementation files"
    )
    _unique_strings(value.get("test_files"), "test files")
    if any(Path(item).is_absolute() or ".." in Path(item).parts
           for item in implementation + list(value["test_files"])):
        raise EvidenceContractError("impact manifest paths must be relative")
    return _digest(dict(value))


def _validate_binding(binding: Mapping[str, Any], impact_digest: str) -> dict:
    if not isinstance(binding, Mapping):
        raise EvidenceContractError("evidence binding must be an object")
    complete = copy.deepcopy(dict(binding))
    supplied = complete.get("impact_manifest_fingerprint")
    if supplied is not None and supplied != impact_digest:
        raise EvidenceContractError("impact manifest fingerprint is stale")
    complete["impact_manifest_fingerprint"] = impact_digest
    if set(complete) != set(BINDING_FIELDS):
        missing = sorted(set(BINDING_FIELDS) - set(complete))
        extra = sorted(set(complete) - set(BINDING_FIELDS))
        raise EvidenceContractError(
            f"evidence binding is incomplete (missing={missing}, extra={extra})"
        )
    for name in BINDING_FIELDS:
        _nonempty(complete[name], f"binding {name}")
    return complete


def _assignment_digest(assignment: Mapping[str, Any]) -> str:
    return _digest({key: copy.deepcopy(value)
                    for key, value in assignment.items()
                    if key != "assignment_digest"})


def _reuse_key(producer_kind: str, binding: Mapping[str, Any],
               obligations: Mapping[str, Any]) -> dict:
    material = {
        "producer_kind": producer_kind,
        "binding": copy.deepcopy(dict(binding)),
        "obligations": copy.deepcopy(dict(obligations)),
    }
    return {"material": material, "digest": _digest(material)}


def prepare_assignments(workspace: str | Path, binding: Mapping[str, Any],
                        impact_manifest: Mapping[str, Any]) -> list[dict]:
    """Create exactly two candidate-bound read-only producer assignments."""
    impact_digest = impact_manifest_fingerprint(impact_manifest)
    bound = _validate_binding(binding, impact_digest)
    implementation = list(impact_manifest["implementation_files"])
    try:
        registry = lens.language_quality_registry(implementation)
    except (ValueError, FileNotFoundError) as exc:
        raise EvidenceContractError(str(exc)) from None
    languages = [row["language"] for row in registry]
    try:
        probes = runnability.probe_language_toolchains(
            str(workspace), languages
        )
    except ValueError as exc:
        raise EvidenceContractError(str(exc)) from None
    if len(probes) != len(registry):
        raise EvidenceContractError("language toolchain probe is ambiguous")
    probe_by_language = {row.get("language"): row for row in probes}
    if len(probe_by_language) != len(probes):
        raise EvidenceContractError("duplicate language toolchain probe")
    language_obligations = []
    for reference in registry:
        check = probe_by_language.get(reference["language"])
        if not isinstance(check, dict):
            raise EvidenceContractError(
                f"missing toolchain for {reference['language']}"
            )
        if check.get("verdict") != runnability.RUNS:
            raise EvidenceContractError(
                f"toolchain for {reference['language']} is unsupported or "
                f"unavailable: {check.get('detail', 'no detail')}"
            )
        language_obligations.append({
            "language": reference["language"],
            "reference": copy.deepcopy(reference),
            "toolchain": copy.deepcopy(check),
            "required_commands": [check["command"]],
            "required_selectors": sorted(
                path for path in implementation
                if reference["language"] in lens.implementation_languages(
                    [path]
                )
            ),
        })
    try:
        test_obligations = test_strategy.current_value_obligations(
            impact_manifest
        )
    except test_strategy.StrategyContractError as exc:
        raise EvidenceContractError(str(exc)) from None

    assignments = []
    for kind, obligations in (
        (LANGUAGE_PRODUCER, {
            "implementation_files": implementation,
            "language_obligations": language_obligations,
        }),
        (TEST_DESIGN_PRODUCER, {"test_obligations": test_obligations}),
    ):
        assignment = {
            "schema": ASSIGNMENT_SCHEMA,
            "producer_kind": kind,
            "binding": copy.deepcopy(bound),
            "capabilities": {name: False for name in FORBIDDEN_AUTHORITIES},
            **copy.deepcopy(obligations),
        }
        assignment["reuse_key"] = _reuse_key(kind, bound, obligations)
        assignment["assignment_digest"] = _assignment_digest(assignment)
        assignments.append(assignment)
    return assignments


def _validate_assignment(value: Mapping[str, Any]) -> dict:
    if not isinstance(value, Mapping) or value.get("schema") != ASSIGNMENT_SCHEMA:
        raise EvidenceContractError("child assignment schema is invalid")
    kind = value.get("producer_kind")
    if kind not in PRODUCER_KINDS:
        raise EvidenceContractError("child producer kind is invalid")
    capabilities = value.get("capabilities")
    if capabilities != {name: False for name in FORBIDDEN_AUTHORITIES}:
        raise EvidenceContractError("child assignment grants forbidden authority")
    binding = value.get("binding")
    if not isinstance(binding, Mapping) or set(binding) != set(BINDING_FIELDS):
        raise EvidenceContractError("child assignment binding is incomplete")
    for field in BINDING_FIELDS:
        _nonempty(binding[field], f"binding {field}")
    reuse = value.get("reuse_key")
    if not isinstance(reuse, Mapping) or set(reuse) != {"material", "digest"} \
            or reuse.get("digest") != _digest(reuse.get("material")):
        raise EvidenceContractError("child evidence reuse key is incomplete")
    if reuse["material"].get("producer_kind") != kind or \
            reuse["material"].get("binding") != dict(binding):
        raise EvidenceContractError("child evidence reuse key is stale")
    if value.get("assignment_digest") != _assignment_digest(value):
        raise EvidenceContractError("child assignment digest is stale")
    return copy.deepcopy(dict(value))


def _receipt_digest(value: Mapping[str, Any]) -> str:
    return _digest({key: copy.deepcopy(item) for key, item in value.items()
                    if key != "receipt_digest"})


def complete_lifecycle(assignment: Mapping[str, Any], result: Mapping[str, Any]) \
        -> list[dict]:
    """Build the five host-persistable lifecycle receipt payloads.

    A host still owns persistence/observation.  This helper only produces the
    canonical values it must attest and is also useful to non-mocked journey
    tests at that storage boundary.
    """
    checked = _validate_assignment(assignment)
    result_digest = _digest(dict(result))
    details = (
        {"assigned": True},
        {"started": True},
        {"work_units": 1, "activity": "candidate evidence inspected"},
        {"result_digest": result_digest},
        {"status": "complete", "result_digest": result_digest},
    )
    receipts = []
    for ordinal, (kind, detail) in enumerate(zip(LIFECYCLE_KINDS, details), 1):
        receipt = {
            "schema": LIFECYCLE_SCHEMA,
            "producer_kind": checked["producer_kind"],
            "assignment_digest": checked["assignment_digest"],
            "binding": copy.deepcopy(checked["binding"]),
            "kind": kind,
            "ordinal": ordinal,
            "detail": detail,
        }
        receipt["receipt_digest"] = _receipt_digest(receipt)
        receipts.append(receipt)
    return receipts


def _reject_authority_fields(value: Mapping[str, Any]) -> None:
    forbidden = set(value) & _FORBIDDEN_RESULT_FIELDS
    if forbidden:
        raise EvidenceContractError(
            "child result claims forbidden authority: " + sorted(forbidden)[0]
        )


def _validate_language_result(assignment: Mapping[str, Any],
                              result: Mapping[str, Any]) -> dict:
    if not isinstance(result, Mapping) or \
            result.get("schema") != LANGUAGE_RESULT_SCHEMA or \
            result.get("producer_kind") != LANGUAGE_PRODUCER:
        raise EvidenceContractError("language result schema is invalid")
    _reject_authority_fields(result)
    if result.get("assignment_digest") != assignment["assignment_digest"]:
        raise EvidenceContractError("language result assignment is stale")
    coverage = result.get("language_coverage")
    if not isinstance(coverage, list) or not coverage:
        raise EvidenceContractError("language evidence is empty")
    obligations = {row["language"]: row
                   for row in assignment["language_obligations"]}
    if len(coverage) != len(obligations):
        raise EvidenceContractError("language coverage is missing or duplicate")
    seen = set()
    substantive = 0
    impacted = set(assignment["implementation_files"])
    for row in coverage:
        if not isinstance(row, Mapping):
            raise EvidenceContractError("language coverage row is invalid")
        language = row.get("language")
        if language in seen:
            raise EvidenceContractError("language coverage contains duplicate rows")
        seen.add(language)
        obligation = obligations.get(language)
        if not obligation:
            raise EvidenceContractError("language coverage is ambiguous")
        reference = obligation["reference"]
        toolchain = obligation["toolchain"]
        if row.get("reference_id") != reference["path"] or \
                row.get("reference_sha256") != reference["content_sha256"] or \
                row.get("toolchain_id") != toolchain["id"] or \
                row.get("toolchain_fingerprint") != toolchain["fingerprint"]:
            raise EvidenceContractError("language reference/toolchain is stale")
        inspected = set(_unique_strings(
            row.get("inspected_files"), "inspected language files"
        ))
        if not inspected.issubset(impacted):
            raise EvidenceContractError("language evidence inspects foreign files")
        commands = row.get("command_receipts")
        findings = row.get("findings")
        if not isinstance(commands, list) or not isinstance(findings, list):
            raise EvidenceContractError("language result details are invalid")
        seen_commands = []
        failed_commands = 0
        for command in commands:
            if not isinstance(command, Mapping):
                raise EvidenceContractError("language command receipt is invalid")
            _nonempty(command.get("command"), "language command")
            seen_commands.append(command["command"])
            if command.get("selectors") != obligation["required_selectors"]:
                raise EvidenceContractError(
                    "language command selectors are stale"
                )
            facts = command.get("passing_facts")
            exit_code = command.get("exit_code")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool) \
                    or not isinstance(facts, int) or isinstance(facts, bool) \
                    or facts < 0 or (exit_code == 0 and facts <= 0):
                raise EvidenceContractError(
                    "language command evidence must be substantive"
                )
            if exit_code != 0:
                failed_commands += 1
            substantive += facts
        if seen_commands != obligation["required_commands"]:
            raise EvidenceContractError(
                "language command evidence is missing or ambiguous"
            )
        for finding in findings:
            if not isinstance(finding, Mapping):
                raise EvidenceContractError("language finding is invalid")
            _nonempty(finding.get("title"), "language finding title")
            _nonempty(finding.get("evidence"), "language finding evidence")
            substantive += 1
        if failed_commands and not findings:
            raise EvidenceContractError(
                "failed language commands require substantive findings"
            )
        if not commands and not findings:
            raise EvidenceContractError("language evidence is non-substantive")
    if seen != set(obligations) or substantive <= 0:
        raise EvidenceContractError("language evidence is incomplete")
    return {
        "digest": _digest(dict(result)),
        "language_count": len(coverage),
        "substantive_count": substantive,
    }


def _validate_test_design_result(assignment: Mapping[str, Any],
                                 result: Mapping[str, Any]) -> dict:
    if not isinstance(result, Mapping) or \
            result.get("schema") != TEST_DESIGN_RESULT_SCHEMA or \
            result.get("producer_kind") != TEST_DESIGN_PRODUCER:
        raise EvidenceContractError("test-design result schema is invalid")
    _reject_authority_fields(result)
    if result.get("assignment_digest") != assignment["assignment_digest"]:
        raise EvidenceContractError("test-design result assignment is stale")
    obligations = assignment["test_obligations"]
    current = result.get("current_value")
    if not isinstance(current, list) or not current:
        raise EvidenceContractError("current-value test evidence is empty")
    expected_tests = {row["selector"]: row for row in obligations["tests"]}
    seen_tests = set()
    for row in current:
        if not isinstance(row, Mapping):
            raise EvidenceContractError("current-value evidence row is invalid")
        selector = row.get("selector")
        if selector in seen_tests or selector not in expected_tests:
            raise EvidenceContractError("current-value evidence is duplicate or foreign")
        seen_tests.add(selector)
        if row.get("classification") not in {
            "protects-current-contract", "obsolete-replace", "obsolete-remove"
        } or row.get("contract") != expected_tests[selector]["contract"]:
            raise EvidenceContractError("current-value classification is invalid")
        _nonempty(row.get("evidence"), "current-value evidence")
    if seen_tests != set(expected_tests):
        raise EvidenceContractError("current-value evidence misses impacted tests")

    consumers = result.get("producer_consumers")
    expected_edges = {(row["producer"], row["consumer"], row["selector"])
                      for row in obligations["producer_consumer_edges"]}
    if not isinstance(consumers, list) or len(consumers) != len(expected_edges):
        raise EvidenceContractError("producer-consumer evidence is incomplete")
    seen_edges = set()
    for row in consumers:
        if not isinstance(row, Mapping):
            raise EvidenceContractError("producer-consumer evidence row is invalid")
        key = (row.get("producer"), row.get("consumer"), row.get("selector"))
        if key in seen_edges or key not in expected_edges:
            raise EvidenceContractError("producer-consumer evidence is duplicate or foreign")
        seen_edges.add(key)
        _nonempty(row.get("freshness_evidence"), "freshness evidence")
        _nonempty(row.get("severed_edge_evidence"), "severed-edge evidence")

    fixtures = result.get("same_slice_fixtures")
    if not isinstance(fixtures, list):
        raise EvidenceContractError("same-slice fixtures must be a list")
    expected_fixtures = {
        (row["producer"], row["fixture"]["path"], row["slice"])
        for row in obligations["changed_interfaces"]
        if row["kind"] in {"serialized", "external"}
    }
    actual_fixtures = set()
    for row in fixtures:
        if not isinstance(row, Mapping):
            raise EvidenceContractError("same-slice fixture row is invalid")
        key = (row.get("producer"), row.get("path"), row.get("slice"))
        if key in actual_fixtures:
            raise EvidenceContractError("same-slice fixture is duplicate")
        actual_fixtures.add(key)
    if actual_fixtures != expected_fixtures:
        raise EvidenceContractError("changed interface fixture is not in the same slice")

    failures = result.get("failure_classifications")
    if not isinstance(failures, list):
        raise EvidenceContractError("failure classifications must be a list")
    expected_failure_ids = {row["id"] for row in obligations["failures"]}
    actual_failure_ids = set()
    for row in failures:
        if not isinstance(row, Mapping) or row.get("id") in actual_failure_ids:
            raise EvidenceContractError("failure classification is duplicate")
        actual_failure_ids.add(row.get("id"))
        if row.get("classification") not in \
                test_strategy.EVIDENCE_FAILURE_CLASSES or \
                row.get("classified_before_repair") is not True:
            raise EvidenceContractError("failure must be classified before repair")
    if actual_failure_ids != expected_failure_ids:
        raise EvidenceContractError("failure classifications are incomplete")

    rejected = result.get("rejected_evidence")
    if not isinstance(rejected, list):
        raise EvidenceContractError("rejected evidence must be a list")
    rejected_kinds = []
    for row in rejected:
        if not isinstance(row, Mapping):
            raise EvidenceContractError("rejected evidence row is invalid")
        rejected_kinds.append(row.get("kind"))
        _nonempty(row.get("evidence"), "rejected evidence")
    if rejected_kinds != obligations["rejected_evidence_kinds"]:
        raise EvidenceContractError(
            "ceremonial/source/AST/prose-shape/byte-only rejection is incomplete"
        )
    counts = {
        "current_value_count": len(current),
        "producer_consumer_count": len(consumers),
        "severed_edge_count": len(consumers),
        "same_slice_fixture_count": len(fixtures),
        "failure_class_count": len(failures),
        "rejected_ceremonial_count": len(rejected),
    }
    substantive = sum(counts.values())
    if substantive <= 0:
        raise EvidenceContractError("test-design evidence is non-substantive")
    return {"digest": _digest(dict(result)), **counts,
            "substantive_count": substantive}


def _validate_receipts(assignment: Mapping[str, Any], receipts: list,
                       result_digest: str, substantive_count: int) -> None:
    if not isinstance(receipts, list) or len(receipts) != len(LIFECYCLE_KINDS):
        raise EvidenceContractError("child lifecycle receipt cardinality is invalid")
    if [row.get("kind") for row in receipts] != list(LIFECYCLE_KINDS):
        raise EvidenceContractError("child lifecycle receipts are missing or duplicate")
    for ordinal, row in enumerate(receipts, 1):
        if not isinstance(row, Mapping) or row.get("schema") != LIFECYCLE_SCHEMA \
                or row.get("producer_kind") != assignment["producer_kind"] \
                or row.get("assignment_digest") != assignment["assignment_digest"] \
                or row.get("binding") != assignment["binding"] \
                or row.get("ordinal") != ordinal \
                or row.get("receipt_digest") != _receipt_digest(row):
            raise EvidenceContractError("child lifecycle receipt is stale or invalid")
    activity = receipts[2].get("detail") or {}
    if not isinstance(activity.get("work_units"), int) or \
            isinstance(activity.get("work_units"), bool) or \
            activity["work_units"] <= 0:
        raise EvidenceContractError("child activity must be non-null and nonzero")
    if (receipts[3].get("detail") or {}).get("result_digest") != result_digest \
            or (receipts[4].get("detail") or {}).get("result_digest") != result_digest \
            or substantive_count <= 0:
        raise EvidenceContractError("child result lifecycle is not bound")


def seal_evidence_run(assignments, receipts, results) -> dict:
    """Validate two full lifecycles and seal their substantive results."""
    if not isinstance(assignments, list) or len(assignments) != 2:
        raise EvidenceContractError("Evaluate requires exactly two child assignments")
    checked = [_validate_assignment(row) for row in assignments]
    by_kind = {row["producer_kind"]: row for row in checked}
    if set(by_kind) != set(PRODUCER_KINDS) or len(by_kind) != 2:
        raise EvidenceContractError("Evaluate child producer cardinality is invalid")
    if not isinstance(results, Mapping) or set(results) != set(PRODUCER_KINDS):
        raise EvidenceContractError("Evaluate requires exactly two child results")
    if not isinstance(receipts, list):
        raise EvidenceContractError("Evaluate child receipts must be a list")
    summaries = {
        LANGUAGE_PRODUCER: _validate_language_result(
            by_kind[LANGUAGE_PRODUCER], results[LANGUAGE_PRODUCER]
        ),
        TEST_DESIGN_PRODUCER: _validate_test_design_result(
            by_kind[TEST_DESIGN_PRODUCER], results[TEST_DESIGN_PRODUCER]
        ),
    }
    producers = []
    for kind in PRODUCER_KINDS:
        assignment = by_kind[kind]
        lifecycle = [row for row in receipts
                     if isinstance(row, Mapping)
                     and row.get("producer_kind") == kind]
        _validate_receipts(
            assignment, lifecycle, summaries[kind]["digest"],
            summaries[kind]["substantive_count"],
        )
        producers.append({
            "producer_kind": kind,
            "assignment_digest": assignment["assignment_digest"],
            "binding": copy.deepcopy(assignment["binding"]),
            "reuse_key_digest": assignment["reuse_key"]["digest"],
            "lifecycle_kinds": list(LIFECYCLE_KINDS),
            "activity_count": lifecycle[2]["detail"]["work_units"],
            "result_substantive_count": summaries[kind]["substantive_count"],
        })
    run = {
        "schema": EVIDENCE_RUN_SCHEMA,
        "producer_count": 2,
        "catalog_lens_count": 0,
        "executed": True,
        "producers": producers,
        "results": summaries,
        "result_payloads": copy.deepcopy(dict(results)),
    }
    run["run_digest"] = _digest(run)
    return run


def _validate_run(run: Mapping[str, Any]) -> dict:
    if not isinstance(run, Mapping) or run.get("schema") != EVIDENCE_RUN_SCHEMA \
            or run.get("producer_count") != 2 \
            or run.get("catalog_lens_count") != 0:
        raise EvidenceContractError("evidence run envelope is invalid")
    material = {key: copy.deepcopy(value) for key, value in run.items()
                if key != "run_digest"}
    if run.get("run_digest") != _digest(material):
        raise EvidenceContractError("evidence run digest is stale")
    producers = run.get("producers")
    results = run.get("results")
    payloads = run.get("result_payloads")
    if not isinstance(producers, list) or len(producers) != 2 or \
            {row.get("producer_kind") for row in producers} != set(PRODUCER_KINDS) \
            or not isinstance(results, Mapping) or set(results) != set(PRODUCER_KINDS) \
            or not isinstance(payloads, Mapping) or set(payloads) != set(PRODUCER_KINDS):
        raise EvidenceContractError("evidence run producer/result set is invalid")
    for kind in PRODUCER_KINDS:
        summary = results[kind]
        if not isinstance(summary, Mapping) or \
                not isinstance(summary.get("substantive_count"), int) or \
                summary["substantive_count"] <= 0 or \
                summary.get("digest") != _digest(payloads[kind]):
            raise EvidenceContractError("evidence run result is empty or stale")
    return copy.deepcopy(dict(run))


def reuse_or_execute(assignments, prior_run: Mapping[str, Any]) -> dict:
    """Reuse only a complete, content-identical two-producer evidence key."""
    checked = [_validate_assignment(row) for row in assignments]
    if len(checked) != 2 or \
            {row["producer_kind"] for row in checked} != set(PRODUCER_KINDS):
        raise EvidenceContractError("reuse requires exactly two assignments")
    try:
        prior = _validate_run(prior_run)
    except EvidenceContractError:
        return {"executed": True, "reason": "prior-evidence-incomplete",
                "results": None}
    prior_keys = {row["producer_kind"]: row["reuse_key_digest"]
                  for row in prior["producers"]}
    current_keys = {row["producer_kind"]: row["reuse_key"]["digest"]
                    for row in checked}
    if prior_keys != current_keys:
        return {"executed": True, "reason": "evidence-key-changed",
                "results": None}
    return {
        "executed": False,
        "reason": "complete-content-identical-key",
        "results": copy.deepcopy(prior["results"]),
    }


def consume_evidence(run: Mapping[str, Any]) -> dict:
    """Return the exact evidence block directly consumed by the evaluator."""
    checked = _validate_run(run)
    value = {
        "schema": CONSUMPTION_SCHEMA,
        "catalog_lens_count": 0,
        "producer_count": 2,
        "results": {
            kind: {
                "digest": checked["results"][kind]["digest"],
                "consumed": True,
                "substantive_count": checked["results"][kind]["substantive_count"],
                "execution": "executed" if checked.get("executed") else "reused",
            }
            for kind in PRODUCER_KINDS
        },
        "children": {"forbidden_authorities": list(FORBIDDEN_AUTHORITIES)},
        "evaluator": {"verdict_owner": "evaluator"},
        "evidence_run_digest": checked["run_digest"],
    }
    value["consumption_digest"] = _digest(value)
    return value


def validate_consumption(value: Mapping[str, Any]) -> dict:
    if not isinstance(value, Mapping) or value.get("schema") != CONSUMPTION_SCHEMA \
            or value.get("catalog_lens_count") != 0 \
            or value.get("producer_count") != 2:
        raise EvidenceContractError("evaluator evidence consumption is invalid")
    results = value.get("results")
    if not isinstance(results, Mapping) or set(results) != set(PRODUCER_KINDS):
        raise EvidenceContractError("evaluator did not consume exactly two results")
    for kind in PRODUCER_KINDS:
        row = results[kind]
        if not isinstance(row, Mapping) or row.get("consumed") is not True or \
                not isinstance(row.get("substantive_count"), int) or \
                isinstance(row.get("substantive_count"), bool) or \
                row["substantive_count"] <= 0 or \
                not isinstance(row.get("digest"), str) or \
                len(row["digest"]) != 64 or \
                row.get("execution") not in {"executed", "reused"}:
            raise EvidenceContractError(
                f"{kind} result was not substantively consumed"
            )
    if value.get("children") != {
        "forbidden_authorities": list(FORBIDDEN_AUTHORITIES)
    } or value.get("evaluator") != {"verdict_owner": "evaluator"}:
        raise EvidenceContractError("child/evaluator authority boundary is invalid")
    _nonempty(value.get("evidence_run_digest"), "evidence run digest")
    material = {key: copy.deepcopy(item) for key, item in value.items()
                if key != "consumption_digest"}
    if value.get("consumption_digest") != _digest(material):
        raise EvidenceContractError("evaluator evidence consumption is stale")
    return copy.deepcopy(dict(value))


def evaluator_route_summary() -> dict:
    """Public proof that evidence producers are not catalog lens workers."""
    return {
        "catalog_lens_count": 0,
        "producer_count": 2,
        "producer_kinds": list(PRODUCER_KINDS),
    }
