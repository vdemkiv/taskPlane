"""T16 policy proof and explicit unresolved production probes.

The policy exercises its trusted provenance port with a local policy test
double. Those observations are never presented as W journey receipts. The
exact W selectors below remain blocking until their owning tasks supply real
attempt-bound probes; a missing producer cannot be replaced with fixtures.
"""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from taskplane import settings, wiring_closure as wiring


ROOT = Path(__file__).resolve().parents[2]
PLAN = json.loads((ROOT / "plan/tasks.json").read_text())
ROWS = {row["id"]: row for row in PLAN["wiring_manifest"]}


def policy_pair():
    # This is a labeled policy-unit observation. The actual wiring validator's
    # output crosses into the policy consumer; no future phase result is made.
    artifact = json.dumps(wiring.validate_plan_wiring_manifest(
        PLAN["wiring_manifest"], task_ids=[task["id"] for task in PLAN["tasks"]]),
        sort_keys=True).encode()
    positive = wiring.BoundaryObservation(
        selector=ROWS["W03"]["positive_selector"], behavior="policy-unit:manifest-admission",
        identity=("policy-run", "policy-attempt", "policy-operation", "candidate"),
        conditions_fingerprint="a" * 64, status="executed", outcome="accepted",
        output=artifact, artifact_reference="policy-unit:actual-manifest-output",
        changed_edges=(), refusal_edge=None, evidence_class="consumer-unit")
    negative = replace(positive, selector=ROWS["W03"]["severed_selector"],
        outcome="refused", changed_edges=("W03",), refusal_edge="W03")
    # Owner verification is a trusted composition port, exercised here with a
    # double that recognizes only the original observations. It is not a host
    # receipt issuer, nor proof of the W03 registry/resolver journey.
    def verify(observed):
        if observed not in (positive, negative):
            raise ValueError("observation is not retained by producer owner")
    return positive, negative, verify


def test_policy_requires_owner_verification_even_for_identical_bytes():
    positive, negative, verify = policy_pair()
    report = wiring.assess_boundary_pair(ROWS["W03"], positive, negative, verify_owner=verify)
    assert report == ["positive:evidence-class", "negative:evidence-class"]
    copied = replace(positive, artifact_reference="consumer-created-copy")
    assert copied.output == positive.output
    assert "positive:producer-provenance" in wiring.assess_boundary_pair(
        ROWS["W03"], copied, negative, verify_owner=verify)
    assert "positive:producer-provenance" in wiring.assess_boundary_pair(
        ROWS["W03"], positive, negative)


@pytest.mark.parametrize("case,expected", [
    ("stale", "negative:foreign-or-stale"), ("foreign", "negative:foreign-or-stale"),
    ("multi-edge", "negative:single-edge"), ("ceremonial", "negative:single-edge"),
    ("non-failing", "negative:non-failing"),
    ("different-behavior", "negative:different-public-behavior"),
    ("different-conditions", "negative:changed-nontarget-conditions"),
    ("wrong-selector", "negative:selector"), ("missing", "negative:probe-missing"),
    ("unexecutable", "negative:probe-unavailable"), ("unattributable", "negative:unattributable"),
    ("missing-output", "positive:missing-upstream-output"),
    ("consumer-unit", "positive:evidence-class"),
], ids=lambda value: value)
def test_boundary_policy_refuses_each_independent_gap(case, expected):
    positive, negative, verify = policy_pair()
    if case in {"stale", "foreign"}:
        negative = replace(negative, identity=(case, *negative.identity[1:]))
    elif case == "multi-edge":
        negative = replace(negative, changed_edges=("W03", "W04"))
    elif case == "ceremonial":
        negative = replace(negative, changed_edges=())
    elif case == "non-failing":
        negative = replace(negative, outcome="accepted")
    elif case == "different-behavior":
        negative = replace(negative, behavior="other")
    elif case == "different-conditions":
        negative = replace(negative, conditions_fingerprint="b" * 64)
    elif case == "wrong-selector":
        negative = replace(negative, selector=positive.selector)
    elif case == "missing":
        negative = None
    elif case == "unexecutable":
        negative = replace(negative, status="unavailable")
    elif case == "unattributable":
        negative = replace(negative, refusal_edge="W04")
    elif case == "missing-output":
        positive = replace(positive, output=None)
    else:
        positive = replace(positive, evidence_class="consumer-unit")
    assert expected in wiring.assess_boundary_pair(
        ROWS["W03"], positive, negative, verify_owner=verify)


def test_compound_proof_gaps_do_not_short_circuit():
    positive, negative, verify = policy_pair()
    negative = replace(negative, changed_edges=("W03", "W04"), outcome="accepted",
                       status="unavailable", refusal_edge=None)
    findings = wiring.assess_boundary_pair(ROWS["W03"], positive, negative, verify_owner=verify)
    assert {"negative:probe-unavailable", "negative:single-edge", "negative:non-failing",
            "negative:unattributable"} <= set(findings)


def _registry_probe(*, severed):
    """Actual local W03 owner output; no host, grant or phase result is faked."""
    definitions = [json.loads(value) for value in settings.load_settings().phase_definitions]
    skills = {row["skill_ref"]: (ROOT / row["skill_ref"]).read_bytes() for row in definitions}
    registry = settings.load_phase_registry(definitions, skills=skills,
        validator_inventory={"taskplane.stage_entities.validate_stage": "taskplane.stage/v1"},
        artifact_schemas={"stage": "taskplane.stage/v1"})
    produced = next(phase for phase in registry.phases if phase.id == "build")
    assert registry.admit("build", ()) is produced
    if severed:
        # Remove only the actual build definition at the registry/resolver edge;
        # the same phase ID, request, settings, skills and fingerprints remain.
        disconnected = replace(registry, phases=tuple(phase for phase in registry.phases
                                                     if phase is not produced))
        with pytest.raises(settings.SettingsError, match="unknown phase id"):
            disconnected.admit("build", ())
    else:
        assert registry.admit("build", ()).canonical_bytes == produced.canonical_bytes


def _required_pair(edge_id, *, severed):
    if edge_id == "W03":
        _registry_probe(severed=severed)
        return
    row = ROWS[edge_id]
    pytest.fail(f"{edge_id}: required actual-producer probe is missing: "
                f"{row['producer']} -> {row['boundary']} -> {row['consumer']} "
                f"(owner {row['task']}); T16 policy-unit evidence cannot satisfy this journey")


@pytest.mark.parametrize("edge_id", wiring.PLAN_WIRING_EDGE_IDS, ids=str)
def test_wiring_production_path(edge_id):
    _required_pair(edge_id, severed=False)


@pytest.mark.parametrize("edge_id", wiring.PLAN_WIRING_EDGE_IDS, ids=str)
def test_wiring_severed_edge_fails_closed(edge_id):
    _required_pair(edge_id, severed=True)


def test_missing_plan_observations_report_every_gap():
    gaps = wiring.assess_plan_wiring(PLAN["wiring_manifest"],
        task_ids=[task["id"] for task in PLAN["tasks"]], pairs={})
    assert len(gaps) == 34
    assert set(gaps) == set(wiring.PLAN_WIRING_EDGE_IDS)


def test_all_severed_edges_have_attributable_single_edge_failure():
    gaps = []
    for edge_id in wiring.PLAN_WIRING_EDGE_IDS:
        if edge_id == "W03":
            _registry_probe(severed=False)
            _registry_probe(severed=True)
        else:
            gaps.append(edge_id)
    # This exact aggregate selector is a real completion gate, not a policy
    # unit test that passes because missing evidence was correctly rejected.
    pytest.fail("W03 local registry/resolver pair passed; remaining actual producer/severance "
                "probes are missing: " + ", ".join(gaps))
