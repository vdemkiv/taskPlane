"""R-0004 non-build stage handoffs remain bounded and explicit."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from taskplane import phase_handoff, taskplane_lite


ROOT = Path(__file__).resolve().parents[2]
FLOW_PATHS = {
    name: ROOT / "skills" / name / "flow.json"
    for name in ("taskplane", "tp-product", "tp-design")
}
SKILL_PATHS = {
    name: ROOT / "skills" / name / "SKILL.md"
    for name in ("taskplane", "tp-product", "tp-design")
}
STAGE_RUNTIME = {
    "dispatch_schema": "taskplane.stage-dispatch/v1",
    "startup_schema": "taskplane.stage-startup/v1",
    "handoff_schema": "taskplane.stage-handoff/v1",
    "bounded_inputs": [
        "authority", "input_handoff", "selected_artifacts", "budget",
        "declared_scope", "execution_claim",
    ],
    "forbidden_inheritance": [
        "agents", "conversations", "event_logs", "tool_transcripts",
        "leases", "runtime_state", "predecessor_roots",
    ],
    "non_build_terminal_outcomes": ["closed", "discarded"],
    "implicit_build": False,
    "rollout_modes": ["new-run", "enabled"],
    "rollback": "retain-v4-read-only-no-reverse-migration",
}
STARTUP_RELATIONSHIP = (
    "`taskplane.stage-dispatch/v1` contains "
    "`taskplane.stage-startup/v1`; its `input_handoff` is the versioned "
    "bounded `taskplane.stage-handoff/v1` manifest"
)


def _flow(name: str) -> dict[str, object]:
    return json.loads(FLOW_PATHS[name].read_text(encoding="utf-8"))


def test_facade_product_and_design_offer_explicit_non_build_terminal_path() \
        -> None:
    flow = _flow("taskplane")
    nodes = {row["id"]: row for row in flow["nodes"]}
    edges = {tuple(row) for row in flow["edges"]}

    assert nodes["terminal_handoff"] == {
        "id": "terminal_handoff",
        "label": "done / closed / discarded (no implicit Build)",
        "kind": "stage",
    }
    assert {("product", "terminal_handoff"),
            ("design", "terminal_handoff")} <= edges
    assert ("terminal_handoff", "build") not in edges


def test_facade_product_and_design_share_the_canonical_stage_runtime() -> None:
    for name in FLOW_PATHS:
        assert _flow(name)["stage_runtime"] == STAGE_RUNTIME


def test_product_and_design_keep_build_and_non_build_outcomes_distinct() \
        -> None:
    product = _flow("tp-product")
    product_edges = {tuple(row) for row in product["edges"]}
    product_nodes = {row["id"]: row for row in product["nodes"]}
    assert product_nodes["terminal_handoff"]["label"] == \
        "done / closed / discarded (no implicit Build)"
    assert ("approval", "build") in product_edges
    assert ("approval", "terminal_handoff") in product_edges
    assert ("terminal_handoff", "build") not in product_edges

    design = _flow("tp-design")
    design_edges = {tuple(row) for row in design["edges"]}
    design_nodes = {row["id"]: row for row in design["nodes"]}
    assert design_nodes["terminal_handoff"]["label"] == \
        "done / closed / discarded (no implicit Build)"
    assert ("approval", "terminal_handoff") in design_edges
    assert ("terminal_handoff", "build") not in design_edges


def test_non_build_skill_contracts_are_bounded_auditable_and_reusable_only_by_new_authority() \
        -> None:
    required = (
        STARTUP_RELATIONSHIP,
        "content-addressed artifacts",
        "stage authority, budget, and scope",
        "predecessor agents",
        "conversations",
        "event logs",
        "tool transcripts",
        "leases",
        "runtime roots",
        "`done`",
        "`closed`",
        "`discarded`",
        "no implicit Build",
        "explicit new authority",
        "audit",
    )
    for name, path in SKILL_PATHS.items():
        text = " ".join(path.read_text(encoding="utf-8").split())
        for phrase in required:
            assert phrase in text, f"{name} omits {phrase!r}"
        assert "starts from `taskplane.stage-handoff/v1`" not in text
        assert "consumes only `taskplane.stage-handoff/v1`" not in text


def _phase_reference(kind: str, digest: str) -> dict[str, object]:
    return {
        "schema": phase_handoff.ARTIFACT_REFERENCE_SCHEMA,
        "kind": kind, "digest": digest, "bytes": 1,
        "media_type": "application/json",
        "destination": phase_handoff.artifact_destination(digest),
        "locator": f"repo-artifact://sha256/{digest}",
    }


def _resume_handoff(phase: str) -> dict[str, object]:
    repository_id = "github.com/example/taskplane"
    source_commit, source_tree = "1" * 40, "2" * 40
    fingerprints = {"requirement": "a" * 64, "design": "b" * 64,
                    "plan": "c" * 64}
    artifacts = {
        kind: _phase_reference(kind, digest)
        for kind, digest in (("requirement", "d" * 64),
                             ("design", "e" * 64),
                             ("plan", "f" * 64))
    }
    gates = [("initial-authorization", fingerprints["requirement"]),
             ("design-approval", fingerprints["design"])]
    if phase == "plan":
        gates.append(("plan-approval", fingerprints["plan"]))
    authority, predecessor = [], None
    for gate, subject in gates:
        receipt = phase_handoff.create_human_gate_receipt(
            gate=gate, actor="human:vdemkiv", context=f"approved {gate}",
            subject_fingerprint=subject, repository_id=repository_id,
            source_commit=source_commit, source_tree=source_tree,
            predecessor_authority_fingerprint=predecessor)
        authority.append(receipt)
        predecessor = receipt["fingerprint"]
    first = phase_handoff.create_progress_receipt(
        producer="engine:test", sequence=1, phase=phase,
        obligation_id="O1", task_id=None, status="green",
        predecessor_receipt_fingerprint=None)
    second = phase_handoff.create_progress_receipt(
        producer="engine:test", sequence=2, phase=phase,
        obligation_id="O2", task_id=None, status="interrupted",
        predecessor_receipt_fingerprint=first["fingerprint"])
    proof = "python3 -m pytest -q taskplane/tests/test_stage_non_build_handoffs.py"
    plan = ({"fingerprint": fingerprints["plan"],
             "artifact": artifacts["plan"]} if phase == "plan" else None)
    tasks = ([{
        "id": "T-001", "ordinal": 1, "scope": ["taskplane/example.py"],
        "dependencies": [], "contracts": ["contract:phase-startup"],
        "acceptance": ["A1", "A2"], "proofs": [proof],
    }] if plan is not None else [])
    selected = [artifacts["requirement"], artifacts["design"]]
    if plan is not None:
        selected.append(artifacts["plan"])
    return phase_handoff.create_phase_handoff(
        repository={"id": repository_id},
        source={"commit": source_commit, "tree": source_tree},
        requirement={"id": "R-0001",
                     "fingerprint": fingerprints["requirement"],
                     "artifact": artifacts["requirement"]},
        design={"fingerprint": fingerprints["design"],
                "artifact": artifacts["design"]},
        plan=plan, producer={"phase": phase, "outcome": "interrupted"},
        successor={"phase": phase, "mode": "same-phase-resume"},
        obligations=[
            {"id": "O1", "ordinal": 1,
             "contracts": ["contract:phase-startup"],
             "acceptance": ["A1"], "proofs": [proof]},
            {"id": "O2", "ordinal": 2,
             "contracts": ["contract:phase-startup"],
             "acceptance": ["A2"], "proofs": [proof]},
        ], progress={"completed": ["O1"], "remaining": ["O2"]},
        tasks=tasks, contracts=[{
            "id": "contract:phase-startup", "relation": "provides"}],
        acceptance=[
            {"id": "A1", "ordinal": 1, "criterion": "first", "proofs": [proof]},
            {"id": "A2", "ordinal": 2, "criterion": "second", "proofs": [proof]},
        ], selected_artifacts=sorted(
            selected, key=lambda row: (row["kind"], row["digest"])),
        authority_receipts=authority, progress_receipts=[first, second],
        lineage={"predecessor_handoff_fingerprint": None,
                 "predecessor_receipt_head": second["fingerprint"]},
        exclusions=sorted(phase_handoff.REQUIRED_EXCLUSIONS))


@pytest.mark.parametrize("phase", ["design", "plan"])
def test_stateless_non_build_resume_schedules_only_remaining_work(
        phase: str) -> None:
    handoff = _resume_handoff(phase)
    startup = taskplane_lite.create_stateless_phase_startup(
        handoff, attempt_id=f"attempt-{phase}")
    projection = startup["projection"]
    scoped = startup["workers"][0]["scoped_view"]

    assert projection["progress"] == {
        "completed": ["O1"], "remaining": ["O2"]}
    assert [row["id"] for row in projection["obligations"]] == ["O2"]
    assert [row["id"] for row in projection["acceptance"]] == ["A2"]
    assert scoped["progress"] == projection["progress"]
    assert scoped["obligations"] == projection["obligations"]
    assert scoped["acceptance"] == projection["acceptance"]
    assert scoped["full_envelope_reference"] == \
        projection["full_envelope_reference"]

    terminal = copy.deepcopy(handoff)
    terminal["producer"]["outcome"] = "done"
    terminal["handoff_id"] = phase_handoff.handoff_identity(terminal)
    terminal["fingerprint"] = phase_handoff.manifest_fingerprint(terminal)
    with pytest.raises(taskplane_lite.StageDispatchError,
                       match="refused before dispatch"):
        taskplane_lite.create_stateless_phase_startup(terminal)


def test_design_to_plan_next_phase_schedules_all_successor_work() -> None:
    handoff = _resume_handoff("design")
    first = handoff["progress_receipts"][0]
    second = phase_handoff.create_progress_receipt(
        producer="engine:test", sequence=2, phase="design",
        obligation_id="O2", task_id=None, status="green",
        predecessor_receipt_fingerprint=first["fingerprint"])
    handoff["producer"] = {"phase": "design", "outcome": "done"}
    handoff["successor"] = {"phase": "plan", "mode": "next-phase"}
    handoff["progress"] = {"completed": ["O1", "O2"], "remaining": []}
    handoff["progress_receipts"] = [first, second]
    handoff["lineage"]["predecessor_receipt_head"] = second["fingerprint"]
    handoff["handoff_id"] = phase_handoff.handoff_identity(handoff)
    handoff["fingerprint"] = phase_handoff.manifest_fingerprint(handoff)

    startup = taskplane_lite.create_stateless_phase_startup(handoff)
    projection = startup["projection"]
    scoped = startup["workers"][0]["scoped_view"]

    assert projection["progress"] == {
        "completed": [], "remaining": ["O1", "O2"]}
    assert [row["id"] for row in projection["obligations"]] == ["O1", "O2"]
    assert [row["id"] for row in projection["acceptance"]] == ["A1", "A2"]
    assert scoped["progress"] == projection["progress"]
    assert scoped["obligations"] == projection["obligations"]
    assert scoped["acceptance"] == projection["acceptance"]
