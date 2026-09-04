"""Acceptance seams for repository-only phase pickup."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable

import pytest

from taskplane import phase_handoff, phase_pickup


ROOT = Path(__file__).resolve().parents[2]
PROOF = "python3 -m pytest -q taskplane/tests/test_stage_handoff.py"


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True).strip()


def _authority_chain(
        repository_id: str, commit: str, tree: str,
        gates: list[tuple[str, str]]) -> list[dict[str, object]]:
    predecessor = None
    receipts = []
    for gate, subject in gates:
        receipt = phase_handoff.create_human_gate_receipt(
            gate=gate, actor="human:vdemkiv", context=f"approved {gate}",
            subject_fingerprint=subject, repository_id=repository_id,
            source_commit=commit, source_tree=tree,
            predecessor_authority_fingerprint=predecessor)
        predecessor = str(receipt["fingerprint"])
        receipts.append(receipt)
    return receipts


def _handoff(root: Path, journey: str) -> dict[str, object]:
    commit, tree = _git(root, "rev-parse", "HEAD"), _git(
        root, "rev-parse", "HEAD^{tree}")
    repository_id = phase_handoff.repository_identity(root)
    sources = [
        ("requirement", "specs/spec.md", "a" * 64),
        ("design", "design/design.md", "b" * 64),
        ("plan", "plan/plan.md", "c" * 64),
    ]
    artifact_count = {"design": 1, "plan": 2, "build": 3,
                      "resume": 2}[journey]
    artifacts = [
        phase_handoff.create_artifact_reference(
            root, path, kind=kind, media_type="text/markdown")
        for kind, path, _fingerprint in sources[:artifact_count]
    ]
    subjects = {kind: fingerprint for kind, _path, fingerprint in sources}
    gates = [("initial-authorization", subjects["requirement"])]
    if artifact_count >= 2:
        gates.append(("design-approval", subjects["design"]))
    if artifact_count == 3:
        gates.append(("plan-approval", subjects["plan"]))

    producer, successor = {
        "design": (
            {"phase": "requirement", "outcome": "done"},
            {"phase": "design", "mode": "next-phase"}),
        "plan": (
            {"phase": "design", "outcome": "done"},
            {"phase": "plan", "mode": "next-phase"}),
        "build": (
            {"phase": "plan", "outcome": "done"},
            {"phase": "build", "mode": "next-phase"}),
        "resume": (
            {"phase": "design", "outcome": "interrupted"},
            {"phase": "design", "mode": "same-phase-resume"}),
    }[journey]
    receipts = []
    if journey == "resume":
        receipts.append(phase_handoff.create_progress_receipt(
            producer="engine:taskplane.loop/v1", sequence=1,
            phase="design", obligation_id="AC1", task_id=None,
            status="interrupted", predecessor_receipt_fingerprint=None))
    plan = ({"fingerprint": subjects["plan"], "artifact": artifacts[2]}
            if artifact_count == 3 else None)
    tasks = ([{
        "id": "T-001", "ordinal": 1,
        "scope": ["taskplane/phase_handoff.py"], "dependencies": [],
        "contracts": ["contract:stateless-phase-pickup"],
        "acceptance": ["AC1"], "proofs": [PROOF],
    }] if plan is not None else [])
    return phase_handoff.create_phase_handoff(
        repository={"id": repository_id},
        source={"commit": commit, "tree": tree},
        requirement={"id": "R-0001", "fingerprint": subjects["requirement"],
                     "artifact": artifacts[0]},
        design=({"fingerprint": subjects["design"], "artifact": artifacts[1]}
                if artifact_count >= 2 else None),
        plan=plan, producer=producer, successor=successor,
        obligations=[{
            "id": "AC1", "ordinal": 1,
            "contracts": ["contract:stateless-phase-pickup"],
            "acceptance": ["AC1"], "proofs": [PROOF],
        }],
        progress={"completed": [], "remaining": ["AC1"]},
        tasks=tasks,
        contracts=[{
            "id": "contract:stateless-phase-pickup",
            "relation": "provides",
        }],
        acceptance=[{
            "id": "AC1", "ordinal": 1,
            "criterion": "continue from sealed repository evidence",
            "proofs": [PROOF],
        }],
        selected_artifacts=sorted(
            artifacts, key=lambda row: (row["kind"], row["digest"])),
        authority_receipts=_authority_chain(
            repository_id, commit, tree, gates),
        progress_receipts=receipts,
        lineage={
            "predecessor_handoff_fingerprint": None,
            "predecessor_receipt_head": (
                receipts[-1]["fingerprint"] if receipts else None),
        },
        exclusions=sorted(phase_handoff.REQUIRED_EXCLUSIONS),
    )


def _published_checkout(tmp_path: Path, journey: str) \
        -> tuple[Path, dict[str, object]]:
    root = tmp_path / "producer"
    subprocess.run(["git", "clone", "-q", str(ROOT), str(root)], check=True)
    subprocess.run(
        ["git", "config", "user.email", "phase-test@example.invalid"],
        cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Phase test"], cwd=root, check=True)
    handoff = _handoff(root, journey)
    phase_handoff.publish_phase_handoff(root, handoff)
    subprocess.run(
        ["git", "add", "-f", "exports/pickup"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", f"publish {journey} handoff"],
        cwd=root, check=True)
    return root, handoff


@pytest.mark.parametrize(
    ("journey", "command", "phase", "task_id"),
    [
        ("design", "pickup", "design", None),
        ("plan", "pickup", "plan", None),
        ("build", "pickup", "build", "T-001"),
        ("resume", "resume", "design", None),
    ],
)
def test_public_phase_pickup_works_from_fresh_clone_and_empty_home(
        tmp_path: Path, journey: str, command: str, phase: str,
        task_id: str | None) -> None:
    producer, handoff = _published_checkout(tmp_path, journey)
    consumer = tmp_path / "consumer"
    subprocess.run(["git", "clone", "-q", str(producer), str(consumer)],
                   check=True)
    private_home = tmp_path / "empty-home"
    private_home.mkdir()
    relative = phase_handoff.handoff_path(str(handoff["handoff_id"]))
    completed = subprocess.run(
        [sys.executable, str(consumer / "taskplane" / "tp.py"),
         "phase", command, relative, "--workspace", str(consumer)],
        cwd=consumer, env={**os.environ, "TASKPLANE_HOME": str(private_home)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert (result["status"], result["code"], result["phase"]) == (
        "ready", "phase-ready", phase)
    assert result["task_id"] == task_id
    assert result["handoff_fingerprint"] == handoff["fingerprint"]
    assert result["lineage"]["status"] == "verified"


@pytest.mark.parametrize(
    ("material_journey", "producer_phase", "successor_phase"),
    [("plan", "design", "plan"), ("build", "plan", "build")],
)
def test_public_completed_phase_export_starts_all_successor_work(
        tmp_path: Path, material_journey: str, producer_phase: str,
        successor_phase: str) -> None:
    producer = tmp_path / f"{producer_phase}-producer"
    subprocess.run(["git", "clone", "-q", str(ROOT), str(producer)],
                   check=True)
    _git(producer, "config", "user.email", "phase-test@example.invalid")
    _git(producer, "config", "user.name", "Phase test")
    source = _handoff(producer, material_journey)
    material = {key: source[key] for key in (
        "repository", "source", "requirement", "design", "plan",
        "obligations", "tasks", "contracts", "acceptance",
        "selected_artifacts", "authority_receipts", "progress_receipts",
        "lineage", "exclusions",
    )}
    request = producer / ".git" / "phase-export-request.json"
    request.write_text(json.dumps({
        "material": material, "phase": producer_phase, "outcome": "done",
        "durable_progress": {
            "phase": producer_phase, "state": "terminal", "outcome": "done",
        },
    }), encoding="utf-8")

    exported = subprocess.run(
        [sys.executable, str(producer / "taskplane" / "tp.py"),
         "phase", "export", "--request", ".git/phase-export-request.json",
         "--workspace", str(producer)],
        cwd=producer, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, check=False)
    assert exported.returncode == 0, exported.stderr
    export_result = json.loads(exported.stdout)
    assert (export_result["status"], export_result["code"]) == (
        "complete", "phase-exported")
    _git(producer, "add", "-f", "exports/pickup")
    _git(producer, "commit", "-qm", f"export {producer_phase}")

    consumer = tmp_path / f"{successor_phase}-consumer"
    subprocess.run(["git", "clone", "-q", str(producer), str(consumer)],
                   check=True)
    relative = phase_handoff.handoff_path(export_result["handoff_id"])
    handoff = phase_handoff.load_phase_handoff(consumer, relative)
    picked_up = subprocess.run(
        [sys.executable, str(consumer / "taskplane" / "tp.py"),
         "phase", "pickup", relative, "--workspace", str(consumer)],
        cwd=consumer, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, check=False)
    assert picked_up.returncode == 0, picked_up.stderr
    result = json.loads(picked_up.stdout)
    obligation_ids = [row["id"] for row in handoff["obligations"]]
    startup_obligations = (
        [row["id"] for row in result["startup"]["projection"]["obligations"]]
        if successor_phase == "plan"
        else result["startup"]["task"]["acceptance"]
    )
    assert result["phase"] == successor_phase
    assert (result["completed_count"], result["remaining_count"]) == (
        0, len(obligation_ids))
    assert startup_obligations == obligation_ids


def _reseal_authority(handoff: dict[str, object]) -> None:
    repository = handoff["repository"]
    source = handoff["source"]
    assert isinstance(repository, dict) and isinstance(source, dict)
    gates = [
        (str(receipt["gate"]), str(receipt["subject_fingerprint"]))
        for receipt in handoff["authority_receipts"]
    ]
    handoff["authority_receipts"] = _authority_chain(
        str(repository["id"]), str(source["commit"]), str(source["tree"]),
        gates)
    handoff["handoff_id"] = phase_handoff.handoff_identity(handoff)
    handoff["fingerprint"] = phase_handoff.manifest_fingerprint(handoff)


def _fault(root: Path, handoff: dict[str, object], case: str) \
        -> tuple[dict[str, object], dict[str, object]]:
    damaged = copy.deepcopy(handoff)
    options: dict[str, object] = {}
    if case == "malformed":
        damaged["unknown"] = "runtime-state"
    elif case == "tampered":
        damaged["fingerprint"] = "0" * 64
    elif case == "stale":
        damaged["source"]["tree"] = "f" * 40
        _reseal_authority(damaged)
    elif case == "foreign":
        damaged["repository"]["id"] = "github.com/foreign/repository"
        _reseal_authority(damaged)
    elif case == "ambiguous":
        first = phase_handoff.create_progress_receipt(
            producer="engine:test", sequence=1, phase="plan",
            obligation_id="AC1", task_id=None, status="green",
            predecessor_receipt_fingerprint=None)
        second = phase_handoff.create_progress_receipt(
            producer="engine:test", sequence=1, phase="plan",
            obligation_id="AC1", task_id=None, status="green",
            predecessor_receipt_fingerprint=str(first["fingerprint"]))
        damaged["progress"] = {"completed": ["AC1"], "remaining": []}
        damaged["progress_receipts"] = [first, second]
        damaged["lineage"]["predecessor_receipt_head"] = second["fingerprint"]
        damaged["handoff_id"] = phase_handoff.handoff_identity(damaged)
        damaged["fingerprint"] = phase_handoff.manifest_fingerprint(damaged)
    elif case == "dirty":
        (root / "untracked-effect.txt").write_text("dirty", encoding="utf-8")
    elif case == "incomplete":
        del damaged["plan"]
    elif case == "artifact":
        artifact = damaged["selected_artifacts"][0]
        path = root / str(artifact["destination"])
        path.write_bytes(b"tampered")
        subprocess.run(["git", "add", "-f", str(artifact["destination"])],
                       cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "tamper artifact"],
                       cwd=root, check=True)
    elif case == "scope":
        requested = copy.deepcopy(damaged["tasks"][0])
        requested["scope"].append("taskplane/loop.py")
        options["requested_task"] = requested
    elif case == "conflict":
        path = root / phase_handoff.handoff_path(str(damaged["handoff_id"]))
        path.write_bytes(b"{}")
    return damaged, options


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("malformed", "handoff-malformed"),
        ("tampered", "handoff-integrity"),
        ("stale", "source-stale"),
        ("foreign", "repository-foreign"),
        ("ambiguous", "receipt-lineage"),
        ("dirty", "checkout-dirty"),
        ("incomplete", "handoff-malformed"),
        ("artifact", "artifact-integrity"),
        ("conflict", "publication-conflict"),
        ("scope", "scope-widened"),
    ],
)
def test_invalid_handoffs_refuse_before_every_downstream_effect(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        case: str, code: str) -> None:
    root, valid = _published_checkout(tmp_path, "build")
    handoff, options = _fault(root, valid, case)
    effects = {name: 0 for name in (
        "dispatch", "authoring", "checkpoint", "publication", "integration")}

    def forbidden(name: str) -> Callable[..., object]:
        def call(*_args: object, **_kwargs: object) -> object:
            effects[name] += 1
            raise AssertionError(f"{name} ran before refusal")
        return call

    monkeypatch.setattr(phase_pickup, "_build_assignment", forbidden("dispatch"))
    monkeypatch.setattr(
        phase_pickup.build_c.checkpoint, "run_and_mint_stateless",
        forbidden("checkpoint"))
    monkeypatch.setattr(
        phase_pickup.build_c.repository.RepositoryManager,
        "accept_pickup_revision", forbidden("integration"))
    monkeypatch.setattr(
        phase_pickup.phase_handoff, "publish_progress_receipt",
        forbidden("publication"))

    with pytest.raises(phase_handoff.PhaseHandoffError if case == "conflict"
                       else phase_pickup.PhasePickupError) as caught:
        if case == "conflict":
            phase_handoff.publish_phase_handoff(root, handoff)
        else:
            phase_pickup.run(
                str(root), handoff, author=forbidden("authoring"), **options)

    assert caught.value.code == code
    assert effects == {
        "dispatch": 0, "authoring": 0, "checkpoint": 0,
        "publication": 0, "integration": 0,
    }
