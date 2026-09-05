"""Only explicit committed Product inputs can start repository-only Design."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from taskplane import depgraph, loop, phase_handoff, requirements


ROOT = Path(__file__).resolve().parents[2]


def _git(root, *args):
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, encoding="utf-8", errors="replace").strip()


def _artifact(root, kind, value):
    raw = phase_handoff.canonical_bytes(value)
    digest = hashlib.sha256(raw).hexdigest()
    destination = phase_handoff.artifact_destination(digest)
    path = root / destination
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    _git(root, "add", "-f", "--", destination)
    return phase_handoff.create_repository_artifact_reference(
        root, destination, kind=kind, publish=False)


def _entry(tmp_path, *, requirement_change=None, graph_change=None, dependency=None):
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Requirement entry fixture")
    _git(root, "config", "user.email", "entry@example.invalid")
    (root / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-qm", "initial product source")
    baseline = _git(root, "rev-parse", "HEAD")
    requirement = {
        "id": "R-ENTRY", "title": "Start Design from committed Product evidence",
        "functional": ["A fresh Design process consumes only the selected inputs."],
        "nfr": {name: "Preserve existing policy and reject missing evidence."
                for name in requirements.NFR_LENSES},
        "acceptance": ["Design starts without predecessor runtime."],
        "open_questions": [], "depends_on": [], "contracts": [],
        "context_files": ["app.py"], "status": "refined", "tags": [], "links": {},
    }
    if requirement_change:
        requirement_change(requirement)
    graph = {"modules": {"app": {"files": ["app.py"]}}, "edges": [],
             "meta": {"content_fingerprint": "a" * 64, "scanned_head": baseline}}
    if graph_change:
        graph_change(graph)
    refs = [_artifact(root, "requirement", requirement), _artifact(root, "graph", graph)]
    if dependency is not None:
        refs.append(_artifact(root, "requirement-" + dependency["id"], dependency))
    _git(root, "commit", "-qm", "seal exact requirement and baseline graph")
    source = {"commit": _git(root, "rev-parse", "HEAD"),
              "tree": _git(root, "rev-parse", "HEAD^{tree}")}
    rid = phase_handoff.repository_identity(root)
    criterion = "Design starts without predecessor runtime."
    proof = "python3 -m pytest -q tests/test_entry.py::test_entry"
    material = {
        "repository": {"id": rid}, "source": source,
        "requirement": {"id": "R-ENTRY", "fingerprint": refs[0]["digest"], "artifact": refs[0]},
        "design": None, "plan": None, "tasks": [],
        "obligations": [{"id": "AC1", "ordinal": 1, "contracts": [],
                         "acceptance": ["AC1"], "proofs": [proof]}],
        "acceptance": [{"id": "AC1", "ordinal": 1, "criterion": criterion, "proofs": [proof]}],
        "contracts": [], "selected_artifacts": sorted(refs, key=lambda row: (row["kind"], row["digest"])),
        # Attributable approval fixture only; no claimed native host observation.
        "authority_receipts": [phase_handoff.create_human_gate_receipt(
            gate="initial-authorization", actor="human:fixture-owner", context="Explicit entry approval fixture",
            decision="approved", subject_fingerprint=refs[0]["digest"], repository_id=rid,
            source_commit=source["commit"], source_tree=source["tree"])],
        "progress_receipts": [], "lineage": {"predecessor_handoff_fingerprint": None,
                                               "predecessor_receipt_head": None},
        "exclusions": sorted(phase_handoff.REQUIRED_EXCLUSIONS),
    }
    return root, {"material": material, "phase": "requirement", "outcome": "done",
                  "durable_progress": {"phase": "requirement", "state": "terminal", "outcome": "done"}}


def _export(root, request):
    return loop.publish_phase_export(str(root), **request)


def _no_publication(root, request, match):
    before = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    with pytest.raises((ValueError, phase_handoff.PhaseHandoffError), match=match):
        _export(root, request)
    assert not list((root / "exports/pickup").glob("**/handoff.json"))
    assert _git(root, "status", "--porcelain=v1", "--untracked-files=all") == before


def test_fresh_subprocess_exports_requirement_then_design_can_pick_up(tmp_path):
    root, request = _entry(tmp_path)
    request_path = root / ".git/entry-request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    env = {**os.environ, "TASKPLANE_HOME": str(tmp_path / "empty-home")}
    exported = subprocess.run(
        [sys.executable, str(ROOT / "taskplane/tp.py"), "phase", "export", "--request",
         ".git/entry-request.json", "--workspace", str(root)], cwd=root, env=env,
        text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    assert exported.returncode == 0, exported.stdout + exported.stderr
    result = json.loads(exported.stdout)
    assert result["code"] == "phase-exported"
    handoff_path = phase_handoff.handoff_path(result["handoff_id"])
    assert result["handoff_path"] == handoff_path
    handoff = json.loads((root / handoff_path).read_text(encoding="utf-8"))
    assert handoff["producer"] == {"phase": "requirement", "outcome": "done"}
    assert handoff["successor"] == {"phase": "design", "mode": "next-phase"}
    assert handoff["progress"] == {"completed": [], "remaining": ["AC1"]}
    assert handoff["progress_receipts"] == []
    assert handoff["authority_receipts"] == request["material"]["authority_receipts"]
    assert not (root / ".taskplane").exists()
    _git(root, "add", "-f", "exports/pickup")
    _git(root, "commit", "-qm", "publish initial Design handoff")
    picked = subprocess.run(
        [sys.executable, str(ROOT / "taskplane/tp.py"), "phase", "pickup",
         handoff_path, "--workspace", str(root)], cwd=root, env=env,
        text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    assert picked.returncode == 0, picked.stdout + picked.stderr
    startup = json.loads(picked.stdout)
    assert startup["phase"] == "design"
    assert startup["handoff_fingerprint"] == handoff["fingerprint"]
    assert "loop.json" not in {path.name for path in root.rglob("loop.json")}


def test_export_does_not_read_private_predecessor_state(tmp_path, monkeypatch):
    root, request = _entry(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("private predecessor state must not be read")
    for module, names in ((loop, ("load", "mutate")),
                          (requirements, ("get_requirement", "load_index")),
                          (depgraph, ("load", "scan")),
                          (loop.runtime_storage, ("load_workspace_locator",))):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    result = _export(root, request)
    assert result["handoff"]["successor"]["phase"] == "design"


@pytest.mark.parametrize("field,value", [
    ("design", {}), ("plan", {}), ("tasks", [{}]), ("progress_receipts", [{}]),
    ("lineage", {"predecessor_handoff_fingerprint": "a" * 64, "predecessor_receipt_head": None}),
])
def test_requirement_refuses_predecessor_state(tmp_path, field, value):
    root, request = _entry(tmp_path)
    request["material"][field] = value
    _no_publication(root, request, "predecessor phase state")


@pytest.mark.parametrize("change,match", [
    (lambda req: req.update(outcome="interrupted"), "terminal done"),
    (lambda req: req.update(receipt_evidence={}), "refuses receipt evidence"),
    (lambda req: req["durable_progress"].update(state="active"), "terminal done"),
    (lambda req: req["material"].update(authority_receipts=[]), "required gate chain"),
    (lambda req: req["material"]["authority_receipts"][0].pop("decision"), "missing fields: decision"),
    (lambda req: req["material"]["requirement"].update(fingerprint="f" * 64), "authority chain is stale"),
    (lambda req: req["material"]["source"].update(tree="f" * 40), "repository/source"),
    (lambda req: req["material"]["repository"].update(id="repo:foreign"), "authority chain is stale"),
])
def test_requirement_refuses_missing_or_foreign_authority(tmp_path, change, match):
    root, request = _entry(tmp_path)
    change(request)
    _no_publication(root, request, match)


@pytest.mark.parametrize("change,match", [
    (lambda req: req.pop("functional"), "explicit functional list"),
    (lambda req: req.pop("open_questions"), "explicit open_questions list"),
    (lambda req: req.update(functional=[]), "no functional statements"),
    (lambda req: req.update(open_questions=["Unresolved requirement question"]), "open question"),
    (lambda req: req.update(nfr={}), "NFR stated"),
    (lambda req: req.update(acceptance=["A different criterion"]), "acceptance/contracts differ"),
    (lambda req: req.update(depends_on=["R-MISSING"]), "missing dependency"),
])
def test_requirement_reuses_product_readiness_and_exact_projection(tmp_path, change, match):
    root, request = _entry(tmp_path, requirement_change=change)
    _no_publication(root, request, match)


@pytest.mark.parametrize("change,match", [
    (lambda graph: graph.pop("edges"), "complete baseline graph"),
    (lambda graph: graph["meta"].pop("content_fingerprint"), "baseline dependency graph is missing"),
    (lambda graph: graph["meta"].update(scanned_head="foreign"), "baseline dependency graph is stale"),
])
def test_requirement_refuses_missing_or_stale_baseline_graph(tmp_path, change, match):
    root, request = _entry(tmp_path, graph_change=change)
    _no_publication(root, request, match)


@pytest.mark.parametrize("mode", ["dirty", "stale-source", "stale-graph", "corrupt-artifact"])
def test_repository_authority_is_checked_before_publication(tmp_path, mode):
    root, request = _entry(tmp_path)
    match = "clean committed"
    if mode == "corrupt-artifact":
        artifact = root / request["material"]["selected_artifacts"][0]["destination"]
        artifact.write_text("{}\n", encoding="utf-8")
    else:
        (root / "app.py").write_text("def answer():\n    return 43\n", encoding="utf-8")
    if mode in {"stale-source", "stale-graph"}:
        _git(root, "add", "app.py")
        _git(root, "commit", "-qm", "change product after graph capture")
        match = "repository/source"
    if mode == "stale-graph":
        source = {"commit": _git(root, "rev-parse", "HEAD"), "tree": _git(root, "rev-parse", "HEAD^{tree}")}
        request["material"]["source"] = source
        prior = copy.deepcopy(request["material"]["authority_receipts"][0])
        request["material"]["authority_receipts"] = [phase_handoff.create_human_gate_receipt(
            gate=prior["gate"], actor=prior["actor"], context=prior["context"], decision="approved",
            subject_fingerprint=prior["subject_fingerprint"], repository_id=prior["repository_id"],
            source_commit=source["commit"], source_tree=source["tree"])]
        match = "baseline dependency graph is stale"
    _no_publication(root, request, match)


def test_requirement_dependency_uses_only_exact_selected_record(tmp_path, monkeypatch):
    dependency = {"id": "R-DEPENDENCY", "title": "Existing dependency", "functional": [],
                  "acceptance": [], "nfr": {}, "open_questions": [], "contracts": [],
                  "context_files": [], "depends_on": []}
    root, request = _entry(tmp_path,
        requirement_change=lambda req: req.update(depends_on=["R-DEPENDENCY"]), dependency=dependency)
    def forbidden(*args, **kwargs):
        raise AssertionError("dependency lookup must use selected committed JSON")
    monkeypatch.setattr(requirements, "get_requirement", forbidden)
    result = _export(root, request)
    assert len(result["handoff"]["selected_artifacts"]) == 3


def test_entry_rechecks_source_after_product_validation(tmp_path, monkeypatch):
    root, request = _entry(tmp_path)
    original = loop.reqs.product_dor
    def change_source(requirement):
        result = original(requirement)
        (root / "app.py").write_text("# changed during Product validation\n", encoding="utf-8")
        return result
    monkeypatch.setattr(loop.reqs, "product_dor", change_source)
    with pytest.raises(ValueError, match="source changed during validation"):
        _export(root, request)
    assert not list((root / "exports/pickup").glob("**/handoff.json"))
