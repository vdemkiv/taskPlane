"""T09 composition proof with actual producers and simulated authority/host ports.

The local authority callback models an already admitted host session; it is
not evidence of native readiness or the later phase-adapter cutover.
"""
from dataclasses import replace
import copy

import pytest

from taskplane import loop, tp, review_evidence, run_store
from taskplane.tests.test_r0001_agent_runtime import _setup
from taskplane.tests.test_r0001_telemetry_seal import sources, KEY
from taskplane.tests.test_r0001_knowledge_governance import apply


def setup(tmp_path, monkeypatch, *, actor="orchestrator", finding=None, unavailable=False,
          missing_checked_evidence=False):
    inputs = sources(tmp_path, monkeypatch, unavailable=unavailable)
    runtime, _, _ = _setup(tmp_path / "registry")
    store = review_evidence.ArtifactStore(str(tmp_path / "artifacts"))
    events = []

    def authorize(action, result):
        events.append("authorize:" + action)
        if actor != "orchestrator":
            raise PermissionError("orchestrator authority required")
        assert result["authority_fingerprint"] == "f" * 64

    # Actual immutable envelope -> scoped view -> lease -> result -> collection
    # -> current revision. The substantive review itself is simulated.
    result = inputs.runtime_receipt["payload"]
    envelope = review_evidence.create_envelope(store,
        target={"fingerprint": result["candidate_fingerprint"], "head": inputs.freshness["candidate_sha"]},
        diff={"files": ["taskplane/loop.py"]}, impact={},
        graph_quality={"status": "complete"}, runnability={},
        requirement={"id": "R-0001"}, acceptance=["FP-AC07"], contracts=[],
        change={"phase_result_fingerprint": result["fingerprint"],
                "definition_set_fingerprint": result["definition_set_fingerprint"]})
    view = review_evidence.create_scoped_view(store, envelope, slot_id="direct", lens_ids=["direct-evidence"])
    lease = review_evidence.create_slot_lease(store, envelope, view, slot_id="direct", lens_ids=["direct-evidence"])
    output = review_evidence.write_slot_result(store, lease, authored_slot="direct",
        lens_ids=["direct-evidence"], findings=[] if finding is None else [finding],
        lens_results=[{"lens": "direct-evidence", "verdict": "pass", "checked_evidence":
            [] if missing_checked_evidence else [{"file": "taskplane/loop.py", "line": 1,
                "claim": "Simulated direct review of the bound candidate"}]}])
    collection = review_evidence.collect_slot_results(store, [lease], [output])
    revision = {"revision": review_evidence.commit_revision(store, envelope, collection),
                "envelope": envelope, "leases": [lease], "results": [output]}

    def gate(definition, reviewed):
        events.append("gate")
        assert definition["gate"] == "human"
        return "f" * 64  # Simulated current, scoped human grant port.

    def knowledge(proposal, gate_fingerprint):
        events.append("knowledge")
        assert gate_fingerprint == "f" * 64
        # Exact replay of the actual RunStore apply output from T06/T08.
        owner = run_store.RunStore(home=str(tmp_path / "home"))
        return apply(owner, proposal, key=KEY, now=100,
            authorize=lambda proposal, action: gate_fingerprint)

    ports = loop.PhaseContinuationPorts(authorize=authorize, gate=gate,
        apply_knowledge=knowledge, commit=lambda value: store.put("phase-result", value))
    return inputs, runtime.registry, store, revision, ports, events


@pytest.mark.parametrize("case", ["connected", "unavailable-usage", "observation", "debt"])
def test_orchestrator_is_only_continuation_owner(tmp_path, monkeypatch, case):
    finding = None if case in {"connected", "unavailable-usage"} else {
        "class": "observation" if case == "observation" else "pre-existing",
        "severity": "high", "title": "Visible non-blocking evidence", "file": "taskplane/loop.py"}
    inputs, registry, store, revision, ports, events = setup(tmp_path, monkeypatch,
        finding=finding, unavailable=case == "unavailable-usage")
    request = loop.phase_evaluator_request(inputs, registry, store, ports.authorize)
    assert request["evaluation_lenses"] == []
    assert "working_lenses" not in request
    result = loop.continue_phase_result(inputs, registry, store, revision, ports)
    projected = tp.phase_continuation_output(result, inputs=inputs, registry=registry,
        store=store, revision=revision, authorize=ports.authorize)
    assert projected["continuation"] == {"kind": "advance", "successors": ["evaluate"]}
    assert projected["host_kind_version"] == "simulated:local-test"
    assert events.index("gate") < events.index("knowledge")
    assert projected["findings"] == ([] if finding is None else [finding])
    assert projected["telemetry"]["usage_status"] == ("unavailable" if case == "unavailable-usage" else "measured")


@pytest.mark.parametrize("case", ["sever-loop-output", "missing-commit", "missing-telemetry-seal",
    "missing-knowledge", "stale-candidate", "stale-tree", "stale-impact", "foreign-definition",
    "missing-runtime-terminal", "missing-review", "missing-checked-evidence", "missing-review-output",
    "pass-with-blocker", "resolved-without-evidence", "missing-gate", "changed-gate"])
def test_continuation_requires_commit_and_telemetry_seal(tmp_path, monkeypatch, case):
    finding = {"class": "regression", "severity": "low", "title": "Cannot pass", "file": "taskplane/loop.py"}
    if case == "resolved-without-evidence":
        finding["resolved"] = True
    inputs, registry, store, revision, ports, _ = setup(tmp_path, monkeypatch,
        finding=finding if case in {"pass-with-blocker", "resolved-without-evidence"} else None,
        missing_checked_evidence=case == "missing-checked-evidence")
    if case in {"pass-with-blocker", "resolved-without-evidence", "missing-gate", "missing-review", "missing-checked-evidence"}:
        if case == "missing-gate":
            ports = replace(ports, gate=lambda definition, review: None)
        if case == "missing-review":
            revision = {}
        with pytest.raises((ValueError, PermissionError)):
            loop.continue_phase_result(inputs, registry, store, revision, ports)
        return
    result = loop.continue_phase_result(inputs, registry, store, revision, ports)
    assert tp.phase_continuation_output(result, inputs=inputs, registry=registry,
        store=store, revision=revision, authorize=ports.authorize)["continuation"]["kind"] == "advance"
    if case == "sever-loop-output":
        result = None
    elif case in {"missing-commit", "missing-telemetry-seal"}:
        result = copy.deepcopy(result)
        result.pop("committed_result" if case == "missing-commit" else "telemetry_readiness")
    elif case == "changed-gate":
        result = dict(result, gate_fingerprint="1" * 64)
    elif case == "missing-review-output":
        revision = dict(revision, results=[])
    elif case == "missing-knowledge":
        inputs = replace(inputs, knowledge_receipts=())
    elif case.startswith("stale-"):
        field = {"stale-candidate": "candidate_sha", "stale-tree": "source_tree", "stale-impact": "impact_manifest_fingerprint"}[case]
        inputs = replace(inputs, freshness=dict(inputs.freshness, **{field: "9" * len(str(inputs.freshness[field]))}))
    elif case == "foreign-definition":
        registry = replace(registry, definition_set_fingerprint="9" * 64)
    else:
        raw = copy.deepcopy(inputs.runtime_receipt)
        raw["payload"]["terminal_identity"] = None
        inputs = replace(inputs, runtime_receipt=raw)
    with pytest.raises((ValueError, PermissionError)):
        tp.phase_continuation_output(result, inputs=inputs, registry=registry,
            store=store, revision=revision, authorize=ports.authorize)


@pytest.mark.parametrize("case", ["agent", "evaluator", "sever-tp-output", "copied-continuation", "late-authority-loss"])
def test_evaluator_and_agent_cannot_progress(tmp_path, monkeypatch, case):
    inputs, registry, store, revision, ports, events = setup(tmp_path, monkeypatch,
        actor=case if case in {"agent", "evaluator"} else "orchestrator")
    if case in {"agent", "evaluator"}:
        for action in (lambda: loop.phase_evaluator_request(inputs, registry, store, ports.authorize),
                       lambda: loop.continue_phase_result(inputs, registry, store, revision, ports)):
            with pytest.raises(PermissionError):
                action()
        assert "gate" not in events and "knowledge" not in events
        return
    result = loop.continue_phase_result(inputs, registry, store, revision, ports)
    output = tp.phase_continuation_output(result, inputs=inputs, registry=registry,
        store=store, revision=revision, authorize=ports.authorize)
    assert loop.require_phase_continuation(output, inputs, registry, store, revision, ports.authorize)
    if case == "sever-tp-output":
        output = None
    elif case == "copied-continuation":
        output = dict(output, continuation={"kind": "advance", "successors": ["retro"]})
    else:
        def denied(action, runtime):
            raise PermissionError("authority expired")
        ports = replace(ports, authorize=denied)
    with pytest.raises((ValueError, PermissionError)):
        loop.require_phase_continuation(output, inputs, registry, store, revision, ports.authorize)
