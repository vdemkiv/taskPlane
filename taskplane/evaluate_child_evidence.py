"""Evaluate child contracts backed only by the canonical run ledger."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

if __package__:
    from . import (governed_commands, lens, run_artifacts, run_store,
                   runnability, test_strategy)
else:  # pragma: no cover
    import governed_commands
    import lens
    import run_artifacts
    import run_store
    import runnability
    import test_strategy

IMPACT_MANIFEST_SCHEMA = "taskplane.evaluate-impact-manifest/v1"
ASSIGNMENT_SCHEMA = "taskplane.evaluate-child-assignment/v2"
LIFECYCLE_SCHEMA = "taskplane.evaluate-child-lifecycle/v1"
LANGUAGE_RESULT_SCHEMA = "taskplane.evaluate-language-code-quality/v2"
TEST_DESIGN_RESULT_SCHEMA = "taskplane.evaluate-test-design/v2"
RESULT_INDEX_SCHEMA = "taskplane.evaluate-child-result-index/v1"
CONSUMPTION_SCHEMA = "taskplane.evaluate-evidence-consumption/v2"
LANGUAGE_PRODUCER = "language-code-quality"
TEST_DESIGN_PRODUCER = "test-design"
PRODUCER_KINDS = (LANGUAGE_PRODUCER, TEST_DESIGN_PRODUCER)
LIFECYCLE_KINDS = ("assignment", "start", "activity", "result", "terminal")
QUALITY_CHECK_IDS = ("lint", "format", "strict-typing", "security-static")
REJECTED_EVIDENCE_KINDS = test_strategy.REJECTED_BEHAVIORAL_EVIDENCE
FORBIDDEN_AUTHORITIES = (
    "verdict", "gate", "dispatch", "mutation", "delivery-classification",
    "repair",
)
BINDING_FIELDS = (
    "task_id", "requirement_id", "candidate_sha", "source_tree",
    "design_fingerprint", "plan_fingerprint", "settings_digest",
    "evaluator_attempt_id", "impact_manifest_fingerprint",
)
EVENT_TYPES = ("assignment", "start", "progress", "evidence-reference", "terminal")
RESULT_SCHEMAS = {
    LANGUAGE_PRODUCER: LANGUAGE_RESULT_SCHEMA,
    TEST_DESIGN_PRODUCER: TEST_DESIGN_RESULT_SCHEMA,
}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class EvidenceContractError(ValueError):
    """Evidence cannot authorize an evaluator decision."""


def _canonical(value: object) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False) + "\n").encode()
    except (TypeError, ValueError) as exc:
        raise EvidenceContractError(f"evidence is not canonical JSON: {exc}") from None


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: object, label: str, minimum: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise EvidenceContractError(f"{label} must be substantive")
    return value


def _list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise EvidenceContractError(f"{label} must be a non-empty list")
    result = [_text(item, label) for item in value]
    if len(result) != len(set(result)):
        raise EvidenceContractError(f"{label} contains duplicates")
    return result


def _reject_authority(value: object, parent: str = "") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key).replace("_", "-").lower()
            if name in FORBIDDEN_AUTHORITIES and not (
                    name == "mutation" and parent == "severed-edge"):
                raise EvidenceContractError(f"child claims forbidden authority: {name}")
            _reject_authority(child, name)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_authority(child, parent)


def _manifest_digest(value: Mapping[str, Any]) -> str:
    if value.get("schema") != IMPACT_MANIFEST_SCHEMA:
        raise EvidenceContractError("impact manifest schema is invalid")
    paths = _list(value.get("implementation_files"), "implementation files") + \
        _list(value.get("test_files"), "test files")
    if any(Path(item).is_absolute() or ".." in Path(item).parts for item in paths):
        raise EvidenceContractError("impact manifest paths must be relative")
    return _digest(dict(value))


def _ledger(root: str | Path | None = None, run_id: str | None = None) \
        -> tuple[Path, dict, Path]:
    try:
        if run_id is not None:
            owner = run_store.RunStore().load(run_id)
            selected = Path(owner["paths"]["artifacts"]).absolute()
        elif root is not None:
            selected = Path(root).absolute()
        else:
            raise KeyError("ledger identity")
        manifest = run_artifacts.load_manifest(selected)
        owner = run_store.RunStore().load(manifest["binding"]["run_id"])
        if Path(owner["paths"]["artifacts"]).absolute() != selected:
            raise EvidenceContractError("durable evidence ledger is foreign")
        run_artifacts.verify_manifest(selected, expected_binding=manifest["binding"])
        checkout = Path(owner["repository"]["checkout"]).absolute()
        if not checkout.is_dir() or checkout.is_symlink():
            raise EvidenceContractError("durable evidence checkout is unavailable")
        return selected, manifest, checkout
    except (KeyError, OSError, run_artifacts.RunArtifactError,
            run_store.RunStoreError) as exc:
        raise EvidenceContractError(
            f"durable evidence ledger is unavailable or corrupt: {exc}") from None


def _assignment_digest(value: Mapping[str, Any]) -> str:
    return _digest({key: copy.deepcopy(item) for key, item in value.items()
                    if key != "assignment_digest"})


def _reuse_key(kind: str, binding: Mapping[str, Any], obligations: Mapping[str, Any],
               ledger_fingerprint: str) -> str:
    stable = {key: copy.deepcopy(item) for key, item in binding.items()
              if key != "evaluator_attempt_id"}
    return _digest({
        "producer_kind": kind, "binding": stable,
        "ledger_binding_fingerprint": ledger_fingerprint,
        "obligations": copy.deepcopy(dict(obligations)),
        "lifecycle": [LIFECYCLE_SCHEMA, *LIFECYCLE_KINDS, *EVENT_TYPES],
        "result_schema": RESULT_SCHEMAS[kind],
    })


def _assert_current_assignment(value: Mapping[str, Any],
                               manifest: Mapping[str, Any]) -> dict:
    """Rebind a durable assignment to the manifest that is consuming it."""
    row = _validate_assignment(value)
    ledger = manifest["binding"]
    candidate = ledger["candidate"]
    binding = row["binding"]
    if row["ledger_binding_fingerprint"] != ledger["fingerprint"] or \
            binding["candidate_sha"] != candidate.get("revision") or \
            binding["source_tree"] != candidate.get("source_tree") or \
            binding["settings_digest"] != ledger["settings_digest"]:
        raise EvidenceContractError("durable child assignment is foreign")
    for field in ("design_fingerprint", "plan_fingerprint", "settings_digest",
                  "impact_manifest_fingerprint"):
        if not _DIGEST.fullmatch(_text(binding[field], f"binding {field}")):
            raise EvidenceContractError(f"binding {field} is not a SHA-256 digest")
    return row


def _fixture_digests(assignment: Mapping[str, Any], workspace: Path) -> None:
    if assignment["producer_kind"] != TEST_DESIGN_PRODUCER:
        return
    for row in assignment["test_obligations"]["changed_interfaces"]:
        if row["kind"] not in {"serialized", "external"}:
            continue
        fixture = row["fixture"]
        relative = Path(fixture["path"])
        target = workspace / relative
        try:
            if relative.is_absolute() or ".." in relative.parts or \
                    not target.is_file() or target.is_symlink():
                raise OSError("unsafe or missing fixture")
            observed = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            raise EvidenceContractError(
                "changed interface fixture is missing after assignment") from None
        if observed != fixture.get("content_sha256"):
            raise EvidenceContractError(
                "changed interface fixture changed after assignment")


def prepare_assignments(workspace: str | Path, binding: Mapping[str, Any],
                        impact_manifest: Mapping[str, Any], *,
                        artifact_root: str | Path) -> list[dict]:
    """Bind two non-lens producers to the current candidate and durable run."""
    _, manifest, _ = _ledger(root=artifact_root)
    impact_digest = _manifest_digest(impact_manifest)
    bound = copy.deepcopy(dict(binding))
    if bound.get("impact_manifest_fingerprint") not in (None, impact_digest):
        raise EvidenceContractError("impact manifest fingerprint is stale")
    bound["impact_manifest_fingerprint"] = impact_digest
    if set(bound) != set(BINDING_FIELDS):
        raise EvidenceContractError("evidence binding is incomplete")
    for field in BINDING_FIELDS:
        _text(bound[field], f"binding {field}")
    ledger_binding = manifest["binding"]
    candidate = ledger_binding["candidate"]
    if bound["settings_digest"] != ledger_binding["settings_digest"] or \
            bound["candidate_sha"] != candidate.get("revision") or \
            candidate.get("source_tree", bound["source_tree"]) != bound["source_tree"]:
        raise EvidenceContractError("evidence candidate is foreign")
    implementation = list(impact_manifest["implementation_files"])
    try:
        registry = lens.language_quality_registry(implementation)
        probes = runnability.probe_language_quality_toolchains(
            str(workspace), [row["language"] for row in registry])
        test_obligations = test_strategy.current_value_obligations(
            impact_manifest, workspace=workspace)
    except (ValueError, FileNotFoundError,
            test_strategy.StrategyContractError) as exc:
        raise EvidenceContractError(str(exc)) from None
    by_language = {row.get("language"): row for row in probes}
    if len(by_language) != len(probes) or set(by_language) != {
            row["language"] for row in registry}:
        raise EvidenceContractError("language quality toolchain is ambiguous")
    language_rows = []
    for reference in registry:
        probe = by_language[reference["language"]]
        checks = probe.get("checks")
        if not isinstance(checks, list) or [row.get("id") for row in checks] != \
                list(QUALITY_CHECK_IDS):
            raise EvidenceContractError("language quality checks are incomplete")
        if any(row.get("verdict") != runnability.RUNS for row in checks):
            raise EvidenceContractError("required language quality tool is unavailable")
        required = [{
            "id": row["id"], "argv": _list(row.get("argv"), "quality argv"),
            "tool": _text(row.get("tool"), "quality tool"),
            "tool_version": _text(row.get("tool_version"), "quality tool version"),
        } for row in checks]
        language_rows.append({
            "language": reference["language"], "reference": copy.deepcopy(reference),
            "toolchain_fingerprint": _text(probe.get("fingerprint"), "toolchain"),
            "implementation_files": sorted(
                path for path in implementation if reference["language"] in
                lens.implementation_languages([path])),
            "required_commands": required,
        })
    obligations = {
        LANGUAGE_PRODUCER: {"implementation_files": implementation,
                            "language_obligations": language_rows},
        TEST_DESIGN_PRODUCER: {"test_obligations": test_obligations},
    }
    result = []
    ledger_fp = ledger_binding["fingerprint"]
    for kind in PRODUCER_KINDS:
        row = {
            "schema": ASSIGNMENT_SCHEMA, "producer_kind": kind,
            "binding": copy.deepcopy(bound),
            "ledger_binding_fingerprint": ledger_fp,
            "capabilities": {name: False for name in FORBIDDEN_AUTHORITIES},
            **copy.deepcopy(obligations[kind]),
        }
        row["reuse_key_digest"] = _reuse_key(kind, bound, obligations[kind], ledger_fp)
        row["assignment_digest"] = _assignment_digest(row)
        result.append(row)
    return result


def _validate_assignment(value: object) -> dict:
    if not isinstance(value, Mapping) or value.get("schema") != ASSIGNMENT_SCHEMA or \
            value.get("producer_kind") not in PRODUCER_KINDS or \
            value.get("capabilities") != {
                name: False for name in FORBIDDEN_AUTHORITIES}:
        raise EvidenceContractError("child assignment is invalid")
    row = copy.deepcopy(dict(value))
    binding = row.get("binding")
    if not isinstance(binding, Mapping) or set(binding) != set(BINDING_FIELDS):
        raise EvidenceContractError("child assignment binding is incomplete")
    obligations = ({"implementation_files": row.get("implementation_files"),
                    "language_obligations": row.get("language_obligations")}
                   if row["producer_kind"] == LANGUAGE_PRODUCER else
                   {"test_obligations": row.get("test_obligations")})
    expected = _reuse_key(row["producer_kind"], binding, obligations,
                          _text(row.get("ledger_binding_fingerprint"), "ledger"))
    if row.get("reuse_key_digest") != expected or \
            row.get("assignment_digest") != _assignment_digest(row):
        raise EvidenceContractError("child assignment or reuse key is stale")
    return row


def _runtime(value: object, label: str, *, workspace: Path, run_id: str,
             assignment: Mapping[str, Any], argv: list[str],
             consumed: set[str]) -> Mapping[str, Any]:
    """Resolve one existing governed-command receipt; never trust result flags."""
    if not isinstance(value, Mapping) or set(value) != {"authorization", "handle"}:
        raise EvidenceContractError(f"{label} requires governed execution provenance")
    try:
        receipt = governed_commands.semantic_checkpoint_execution_evidence(
            str(workspace), _text(value.get("authorization"), "authorization"),
            _text(value.get("handle"), "execution handle"))
    except governed_commands.GovernedCommandError as exc:
        raise EvidenceContractError(
            f"{label} governed execution provenance is unavailable: {exc}") from None
    binding = assignment["binding"]
    identity = receipt.get("identity")
    fingerprint = receipt.get("receipt_digest")
    if not isinstance(identity, Mapping) or identity.get("run_id") != run_id or \
            identity.get("task_id") != binding["task_id"] or \
            receipt.get("source_sha") != binding["candidate_sha"] or \
            receipt.get("target_sha") != binding["candidate_sha"] or \
            receipt.get("plan_fingerprint") != binding["plan_fingerprint"] or \
            receipt.get("runtime_argv") != argv or \
            receipt.get("state") != "succeeded" or receipt.get("exit_code") != 0 or \
            not isinstance(fingerprint, str) or not _DIGEST.fullmatch(fingerprint):
        raise EvidenceContractError(f"{label} governed execution receipt is foreign")
    if fingerprint in consumed:
        raise EvidenceContractError(
            f"{label} reuses execution proof for another obligation")
    consumed.add(fingerprint)
    return receipt


def _language_substance(assignment: Mapping[str, Any], result: Mapping[str, Any], *,
                        workspace: Path, run_id: str,
                        consumed: set[str]) -> int:
    expected = {row["language"]: row for row in assignment["language_obligations"]}
    rows = result.get("language_coverage")
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise EvidenceContractError("language evidence is incomplete")
    facts = 0
    covered = set()
    for row in rows:
        obligation = expected.get(row.get("language")) if isinstance(row, Mapping) else None
        language = row.get("language") if isinstance(row, Mapping) else None
        if obligation is None or row.get("reference_id") != obligation["reference"]["path"] or \
                row.get("reference_sha256") != obligation["reference"]["content_sha256"] or \
                row.get("toolchain_fingerprint") != obligation["toolchain_fingerprint"] or \
                row.get("inspected_files") != obligation["implementation_files"]:
            raise EvidenceContractError("language evidence is foreign or stale")
        if language in covered:
            raise EvidenceContractError("language obligation is covered more than once")
        covered.add(language)
        commands = row.get("command_receipts")
        if not isinstance(commands, list) or len(commands) != len(
                obligation["required_commands"]):
            raise EvidenceContractError("language command evidence is incomplete")
        for actual, required in zip(commands, obligation["required_commands"]):
            _runtime(actual, "language command", workspace=workspace,
                     run_id=run_id, assignment=assignment,
                     argv=required["argv"], consumed=consumed)
            facts += 1
        if not isinstance(row.get("findings"), list):
            raise EvidenceContractError("language findings are invalid")
    if covered != set(expected):
        raise EvidenceContractError("language evidence omitted an obligation")
    return facts


def _test_substance(assignment: Mapping[str, Any], result: Mapping[str, Any], *,
                    workspace: Path, run_id: str,
                    consumed: set[str]) -> int:
    obligations = assignment["test_obligations"]
    tests = {row["selector"]: row for row in obligations["tests"]}
    current = result.get("current_value")
    if not isinstance(current, list) or len(current) != len(tests):
        raise EvidenceContractError("current-value evidence is incomplete")
    facts = 0
    covered_tests = set()
    for row in current:
        expected = tests.get(row.get("selector")) if isinstance(row, Mapping) else None
        argv = ["python3", "-m", "pytest", "-q", row.get("selector")]
        _runtime(row.get("execution"), "current-value evidence",
                 workspace=workspace, run_id=run_id, assignment=assignment,
                 argv=argv, consumed=consumed)
        if expected is None or row.get("contract") != expected["contract"] or \
                row.get("classification") not in {
                    "protects-current-contract", "obsolete-replace", "obsolete-remove"}:
            raise EvidenceContractError("current-value exact selector did not pass")
        if row["selector"] in covered_tests:
            raise EvidenceContractError("test obligation is covered more than once")
        covered_tests.add(row["selector"])
        facts += 1
    if covered_tests != set(tests):
        raise EvidenceContractError("current-value evidence omitted an obligation")
    edges = {(row["producer"], row["consumer"], row["selector"]): row
             for row in obligations["producer_consumer_edges"]}
    actual_edges = result.get("producer_consumers")
    if not isinstance(actual_edges, list) or len(actual_edges) != len(edges):
        raise EvidenceContractError("producer-consumer evidence is incomplete")
    covered_edges = set()
    for row in actual_edges:
        key = (row.get("producer"), row.get("consumer"), row.get("selector"))
        expected = edges.get(key)
        if expected is None:
            raise EvidenceContractError("producer-consumer evidence is foreign")
        _runtime(row.get("execution"), "producer-consumer evidence",
                 workspace=workspace, run_id=run_id, assignment=assignment,
                 argv=["python3", "-m", "pytest", "-q", expected["selector"]],
                 consumed=consumed)
        _runtime(row.get("severed_edge_execution"), "severed-edge evidence",
                 workspace=workspace, run_id=run_id, assignment=assignment,
                 argv=["python3", "-m", "pytest", "-q",
                       expected["severed_edge"]["selector"]], consumed=consumed)
        if key in covered_edges:
            raise EvidenceContractError("producer-consumer obligation is covered more than once")
        covered_edges.add(key)
        facts += 2
    if covered_edges != set(edges):
        raise EvidenceContractError("producer-consumer evidence omitted an obligation")
    expected_fixtures = {(row["producer"], row["fixture"]["path"], row["slice"])
                         for row in obligations["changed_interfaces"]
                         if row["kind"] in {"serialized", "external"}}
    fixtures = result.get("same_slice_fixtures")
    actual_fixtures = {(row.get("producer"), row.get("path"), row.get("slice"))
                       for row in fixtures or [] if isinstance(row, Mapping)}
    if not isinstance(fixtures, list) or len(fixtures) != len(expected_fixtures) or \
            actual_fixtures != expected_fixtures:
        raise EvidenceContractError("same-slice fixture evidence is incomplete")
    failures = result.get("failure_classifications")
    expected_failures = {row["id"]: row for row in obligations["failures"]}
    if not isinstance(failures, list) or len(failures) != len(expected_failures):
        raise EvidenceContractError("failure classifications are incomplete")
    covered_failures = set()
    for row in failures:
        expected = expected_failures.get(row.get("id")) if isinstance(row, Mapping) else None
        if expected is None or row.get("classification") != expected["classification"]:
            raise EvidenceContractError("failure classification is stale")
        _text(row.get("reason"), "failure reason", 12)
        _text(row.get("owner"), "failure owner", 3)
        _text(row.get("cluster"), "failure cluster", 3)
        if row["id"] in covered_failures:
            raise EvidenceContractError("failure is classified more than once")
        covered_failures.add(row["id"])
    if covered_failures != set(expected_failures):
        raise EvidenceContractError("failure classification omitted an obligation")
    return facts + len(fixtures) + len(failures)


def validate_result(assignment: Mapping[str, Any], result: Mapping[str, Any], *,
                    workspace: str | Path, run_id: str) -> dict:
    """Validate one result and return its canonical durable index metadata."""
    checked = _validate_assignment(assignment)
    _reject_authority(result)
    selected_workspace = Path(workspace).absolute()
    selected_run = _text(run_id, "run id")
    consumed: set[str] = set()
    kind = checked["producer_kind"]
    if result.get("schema") != RESULT_SCHEMAS[kind] or \
            result.get("producer_kind") != kind or \
            result.get("reuse_key_digest") != checked["reuse_key_digest"]:
        raise EvidenceContractError("child result binding is stale")
    count = (_language_substance(
        checked, result, workspace=selected_workspace, run_id=selected_run,
        consumed=consumed) if kind == LANGUAGE_PRODUCER else
        _test_substance(
            checked, result, workspace=selected_workspace, run_id=selected_run,
            consumed=consumed))
    if count < 1:
        raise EvidenceContractError("child result is non-substantive")
    return {
        "schema": RESULT_INDEX_SCHEMA, "producer_kind": kind,
        "assignment_digest": checked["assignment_digest"],
        "reuse_key_digest": checked["reuse_key_digest"],
        "result_schema": RESULT_SCHEMAS[kind], "substantive_count": count,
        "status": "complete",
    }


def _entry(manifest: Mapping[str, Any], reference: object) -> dict:
    if not isinstance(reference, Mapping):
        raise EvidenceContractError("durable result reference is invalid")
    rows = [row for row in manifest["classes"]["validation"]["entries"]
            if row.get("fingerprint") == reference.get("fingerprint")]
    if len(rows) != 1 or rows[0] != dict(reference):
        raise EvidenceContractError("durable result reference is foreign")
    return rows[0]


def _producer(root: Path, manifest: Mapping[str, Any], workspace: Path,
              assignment: Mapping[str, Any]) -> dict:
    checked = _assert_current_assignment(assignment, manifest)
    _fixture_digests(checked, workspace)
    attempt_id = checked["binding"]["evaluator_attempt_id"] + "-" + checked["producer_kind"]
    rows = [row for row in manifest["classes"]["agent-activity"]["entries"]
            if row["metadata"].get("agent_attempt_id") == attempt_id]
    if len(rows) != 5 or [row["metadata"]["event_type"] for row in rows] != \
            list(EVENT_TYPES):
        raise EvidenceContractError("child lifecycle is incomplete, duplicate, or unordered")
    events = [row["metadata"] for row in rows]
    if events[0]["details"].get("assignment") != checked:
        raise EvidenceContractError("durable assignment is stale")
    for kind, event in zip(LIFECYCLE_KINDS, events):
        details = event.get("details")
        if not isinstance(details, Mapping) or details.get("schema") != LIFECYCLE_SCHEMA or \
                details.get("receipt_kind") != kind or \
                details.get("assignment_digest") != checked["assignment_digest"] or \
                details.get("reuse_key_digest") != checked["reuse_key_digest"] or \
                event.get("task_id") != checked["binding"]["task_id"] or \
                event.get("worker_id") != checked["producer_kind"] or \
                event.get("lens") != "non-lens-" + checked["producer_kind"]:
            raise EvidenceContractError("child lifecycle receipt is stale")
    units = events[2]["details"].get("work_units")
    refs = events[3].get("evidence_references")
    if isinstance(units, bool) or not isinstance(units, int) or units < 1 or \
            not isinstance(refs, list) or len(refs) != 1 or \
            events[4].get("evidence_references") != refs or \
            events[4]["details"].get("outcome") != "success":
        raise EvidenceContractError("child lifecycle has no substantive terminal result")
    result_entry = _entry(manifest, refs[0])
    try:
        payload = (root / result_entry["locator"]).read_bytes()
        result = json.loads(payload)
    except (OSError, ValueError) as exc:
        raise EvidenceContractError(f"durable result is unreadable: {exc}") from None
    execution = events[3]["details"].get("execution")
    metadata = validate_result(
        checked, result, workspace=workspace,
        run_id=manifest["binding"]["run_id"])
    if execution == "reused":
        reusable = find_reusable_result(root, checked)
        if reusable != result_entry:
            raise EvidenceContractError("reused child result is not authoritative")
        metadata["assignment_digest"] = result_entry["metadata"].get(
            "assignment_digest")
    if hashlib.sha256(payload).hexdigest() != result_entry["sha256"] or \
            _canonical(result) != payload or result_entry["metadata"] != metadata:
        raise EvidenceContractError("durable result bytes or index are stale")
    if execution not in {"executed", "reused"} or \
            events[4]["details"].get("execution") != execution or any(
                event["details"].get("result_fingerprint") != result_entry["fingerprint"] or
                event["details"].get("result_sha256") != result_entry["sha256"]
                for event in events[3:]):
        raise EvidenceContractError("child result lineage is stale")
    if execution == "executed" and metadata["assignment_digest"] != \
            checked["assignment_digest"]:
        raise EvidenceContractError("executed child result is foreign")
    return {
        "producer_kind": checked["producer_kind"],
        "assignment_digest": checked["assignment_digest"],
        "reuse_key_digest": checked["reuse_key_digest"],
        "receipt_sequences": [row["sequence"] for row in rows],
        "lifecycle_kinds": list(LIFECYCLE_KINDS),
        "result_locator": result_entry["locator"],
        "result_sha256": result_entry["sha256"],
        "result_fingerprint": result_entry["fingerprint"],
        "substantive_count": metadata["substantive_count"],
        "execution": execution, "consumed": True,
    }


def find_reusable_result(root: str | Path, assignment: Mapping[str, Any]) -> dict | None:
    """Resolve one complete identical result from the authoritative ledger."""
    selected, manifest, workspace = _ledger(root=root)
    checked = _validate_assignment(assignment)
    candidates = {}
    for row in manifest["classes"]["agent-activity"]["entries"]:
        details = row["metadata"].get("details")
        prior = details.get("assignment") if isinstance(details, Mapping) else None
        if not isinstance(prior, Mapping) or prior.get("producer_kind") != \
                checked["producer_kind"] or prior.get("reuse_key_digest") != \
                checked["reuse_key_digest"] or prior.get("assignment_digest") == \
                checked["assignment_digest"]:
            continue
        prior_attempt = prior["binding"]["evaluator_attempt_id"] + "-" + \
            prior["producer_kind"]
        prior_rows = [item for item in manifest["classes"]["agent-activity"]["entries"]
                      if item["metadata"].get("agent_attempt_id") == prior_attempt]
        if len(prior_rows) != 5 or prior_rows[3]["metadata"]["details"].get(
                "execution") != "executed":
            continue
        try:
            summary = _producer(selected, manifest, workspace, prior)
        except EvidenceContractError:
            continue
        entry = next(row for row in manifest["classes"]["validation"]["entries"]
                     if row["fingerprint"] == summary["result_fingerprint"])
        candidates[entry["fingerprint"]] = entry
    if len(candidates) > 1:
        raise EvidenceContractError("reusable child evidence is ambiguous")
    return copy.deepcopy(next(iter(candidates.values()))) if candidates else None


def consume_evidence(*, run_id: str, evaluator_attempt_id: str) -> dict:
    """Derive consumption from durable records, never caller-authored digests."""
    root, manifest, workspace = _ledger(run_id=_text(run_id, "run id"))
    attempt = _text(evaluator_attempt_id, "evaluator attempt id")
    assignments = []
    for row in manifest["classes"]["agent-activity"]["entries"]:
        details = row["metadata"].get("details")
        value = details.get("assignment") if isinstance(details, Mapping) else None
        if isinstance(value, Mapping) and (value.get("binding") or {}).get(
                "evaluator_attempt_id") == attempt:
            assignments.append(_validate_assignment(value))
    by_kind = {row["producer_kind"]: row for row in assignments}
    if len(assignments) != 2 or set(by_kind) != set(PRODUCER_KINDS):
        raise EvidenceContractError("Evaluate requires exactly two durable children")
    bindings = [row["binding"] for row in assignments]
    if any(binding != bindings[0] for binding in bindings[1:]):
        raise EvidenceContractError("durable child binding is ambiguous")
    binding = bindings[0]
    return {
        "schema": CONSUMPTION_SCHEMA, "run_id": run_id,
        "evaluator_attempt_id": attempt, "task_id": binding["task_id"],
        "requirement_id": binding["requirement_id"],
        "binding": copy.deepcopy(binding), "catalog_lens_count": 0,
        "producers": [_producer(root, manifest, workspace, by_kind[kind])
                      for kind in PRODUCER_KINDS],
    }


def validate_consumption(value: Mapping[str, Any], *, expected_task: str | None = None,
                         expected_requirement: str | None = None,
                         expected_binding: Mapping[str, Any] | None = None) -> dict:
    if not isinstance(value, Mapping):
        raise EvidenceContractError("evidence consumption is invalid")
    derived = consume_evidence(
        run_id=_text(value.get("run_id"), "run id"),
        evaluator_attempt_id=_text(value.get("evaluator_attempt_id"), "attempt"))
    if dict(value) != derived:
        raise EvidenceContractError("consumption does not match durable records")
    if expected_task is not None and derived["task_id"] != expected_task:
        raise EvidenceContractError("child evidence belongs to another task")
    if expected_requirement is not None and derived["requirement_id"] != expected_requirement:
        raise EvidenceContractError("child evidence belongs to another requirement")
    if expected_binding is None or dict(expected_binding) != derived["binding"]:
        raise EvidenceContractError(
            "child evidence is not bound to the current evaluator candidate")
    if any(row["substantive_count"] < 1 or row["consumed"] is not True
           for row in derived["producers"]):
        raise EvidenceContractError("child results were not substantively consumed")
    return derived
