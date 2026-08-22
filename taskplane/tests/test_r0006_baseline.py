from __future__ import annotations

import hashlib
import json
import os
import subprocess

import pytest

import preflight


def _canonical_digest(value):
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _git(workspace, *args):
    result = subprocess.run(
        ["git", *args], cwd=workspace, check=True,
        stdout=subprocess.PIPE, text=True, encoding="utf-8")
    return result.stdout.strip()


def _knowledge_manifest(root, repo_id, repository_key):
    entries = []
    for directory, names, filenames in os.walk(root):
        names.sort()
        for name in sorted(filenames):
            if name.endswith(".lock"):
                continue
            path = os.path.join(directory, name)
            data = open(path, "rb").read()
            entries.append({
                "path": os.path.relpath(path, root).replace(os.sep, "/"),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
    value = {
        "schema": "taskplane.knowledge-preservation-manifest/v1",
        "repo_id": repo_id,
        "repository_key": repository_key,
        "root": os.path.realpath(root),
        "root_fingerprint": hashlib.sha256(
            os.path.realpath(root).encode("utf-8")).hexdigest(),
        "exclusions": ["*.lock"],
        "entries": entries,
    }
    value["manifest_digest"] = _canonical_digest(value)
    return value


@pytest.fixture
def baseline_fixture(tmp_path, monkeypatch):
    workspace = tmp_path / "checkout"
    home = tmp_path / "home"
    workspace.mkdir()
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test")
    design = workspace / "design"
    design.mkdir()
    (design / "design.md").write_text("approved design\n", encoding="utf-8")
    (design / "contract.json").write_text("{}\n", encoding="utf-8")
    _git(workspace, "add", "design")
    _git(workspace, "commit", "-qm", "design")
    revision = _git(workspace, "rev-parse", "HEAD")

    run_id = "fresh-run"
    repo_id = "github.com/example/project"
    repository_key = "github.com-example-project-1234567890"
    knowledge = home / "projects" / repository_key / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "decisions.md").write_text("kept\n", encoding="utf-8")
    (knowledge / "graph.json.lock").write_text("ignored\n", encoding="utf-8")
    graph_root = home / "runs" / run_id / "graph"
    artifact_root = home / "runs" / run_id / "artifacts"
    graph_root.mkdir(parents=True)
    graph = {
        "meta": {
            "content_fingerprint": "a" * 64,
            "scanned_head": revision,
        },
        "nodes": [],
        "edges": [],
    }
    (graph_root / "graph.json").write_text(
        json.dumps(graph), encoding="utf-8")
    locator = {
        "schema": "taskplane.workspace/v1",
        "run_id": run_id,
        "repo_id": repo_id,
        "repository_key": repository_key,
        "checkout": str(workspace),
        "primary_checkout": str(workspace),
        "home": str(home),
        "paths": {
            "state": str(home / "runs" / run_id / "state"),
            "graph": str(graph_root),
            "evidence": str(home / "runs" / run_id / "evidence"),
            "lenses": str(home / "runs" / run_id / "lenses"),
            "artifacts": str(artifact_root),
        },
    }
    monkeypatch.setattr(
        preflight.storage, "load_workspace_locator", lambda _ws: locator)
    trusted = _knowledge_manifest(knowledge, repo_id, repository_key)
    prior_design = [
        {
            "revision": revision,
            "path": path,
            "sha256": hashlib.sha256(
                (workspace / path).read_bytes()).hexdigest(),
        }
        for path in ("design/design.md", "design/contract.json")
    ]
    enforcement = {
        "schema": "taskplane.enforcement-status/v1",
        "status": "advisory",
        "evidence_id": "enf-evidence",
        "session_fingerprint": "session-fingerprint",
        "receipt_evidence": {
            "effective_path": "transitioning",
            "loaded_path": "/plugin/taskplane",
            "content_fingerprint": "b" * 64,
            "host_observation": "no compatible live receipt",
            "observed_at": "2026-08-21T23:31:12Z",
        },
        "advisory": {"actor": "human:vdemkiv"},
    }
    advisory = {
        "actor": "human:vdemkiv",
        "reason": "host hook receipt is unavailable",
        "scope": "R-0006:waves-1-5",
        "expires_at": "2026-12-31T23:59:59Z",
        "accepted_limitations": ["screen enforcement is advisory"],
    }
    return {
        "workspace": str(workspace),
        "revision": revision,
        "run_id": run_id,
        "trusted": trusted,
        "prior_design": prior_design,
        "enforcement": enforcement,
        "advisory": advisory,
        "knowledge": knowledge,
        "artifact_root": artifact_root,
    }


def _verify(fixture, **overrides):
    values = {
        "expected_run_id": fixture["run_id"],
        "trusted_knowledge_manifest": fixture["trusted"],
        "prior_design": fixture["prior_design"],
        "enforcement": fixture["enforcement"],
        "advisory_authorization": fixture["advisory"],
        "active_plan": {
            "run_id": fixture["run_id"],
            "status": "approved",
            "paths": ["plan/plan.md", "plan/tasks.json"],
        },
        "obsolete_run_ids": ["old-run"],
    }
    values.update(overrides)
    return preflight.verify_governance_baseline(
        fixture["workspace"], **values)


def test_baseline_binds_fresh_revision_graph_plan_design_and_knowledge(
        baseline_fixture):
    result = _verify(baseline_fixture)

    assert result["schema"] == "taskplane.governance-baseline/v1"
    assert result["repository"]["revision"] == baseline_fixture["revision"]
    assert result["repository"]["branch"] == "main"
    assert result["run"]["id"] == baseline_fixture["run_id"]
    assert result["graph"] == {
        "fingerprint": "a" * 64,
        "scanned_revision": baseline_fixture["revision"],
    }
    assert result["plan_authority"]["stale"] is False
    assert len(result["prior_design"]) == 2
    assert result["knowledge"]["preserved"] is True
    assert result["enforcement"]["status"] == "advisory"
    assert result["enforcement"]["enforced"] is False
    artifact = (baseline_fixture["artifact_root"] / "baseline" /
                "governance-baseline.json")
    assert json.loads(artifact.read_text(encoding="utf-8")) == result


@pytest.mark.parametrize("mutation,match", [
    ("changed", "knowledge preservation mismatch"),
    ("unexpected", "knowledge preservation mismatch"),
    ("missing", "knowledge preservation mismatch"),
])
def test_closed_manifest_rejects_changed_unexpected_or_missing_evidence(
        baseline_fixture, mutation, match):
    path = baseline_fixture["knowledge"] / "decisions.md"
    if mutation == "changed":
        path.write_text("changed\n", encoding="utf-8")
    elif mutation == "unexpected":
        (baseline_fixture["knowledge"] / "surprise.md").write_text(
            "new\n", encoding="utf-8")
    else:
        path.unlink()

    with pytest.raises(preflight.PreflightError, match=match):
        _verify(baseline_fixture)


def test_manifest_identity_digest_and_nonempty_store_are_mandatory(
        baseline_fixture):
    tampered = dict(baseline_fixture["trusted"])
    tampered["root"] = str(baseline_fixture["knowledge"].parent)
    with pytest.raises(preflight.PreflightError, match="canonical knowledge root"):
        _verify(baseline_fixture, trusted_knowledge_manifest=tampered)

    tampered = dict(baseline_fixture["trusted"])
    tampered["manifest_digest"] = "0" * 64
    with pytest.raises(preflight.PreflightError, match="manifest digest"):
        _verify(baseline_fixture, trusted_knowledge_manifest=tampered)

    tampered = dict(baseline_fixture["trusted"])
    tampered["entries"] = []
    unsigned = dict(tampered)
    unsigned.pop("manifest_digest")
    tampered["manifest_digest"] = _canonical_digest(unsigned)
    with pytest.raises(preflight.PreflightError, match="must not be empty"):
        _verify(baseline_fixture, trusted_knowledge_manifest=tampered)


def test_obsolete_run_stale_plan_graph_or_prior_design_drift_blocks(
        baseline_fixture):
    with pytest.raises(preflight.PreflightError, match="obsolete run pointer"):
        _verify(baseline_fixture,
                obsolete_run_ids=[baseline_fixture["run_id"]])
    with pytest.raises(preflight.PreflightError, match="stale Plan authority"):
        _verify(baseline_fixture, active_plan={
            "run_id": "old-run", "status": "approved",
            "paths": ["plan/plan.md"],
        })

    graph_path = (baseline_fixture["artifact_root"].parent / "graph" /
                  "graph.json")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    graph["meta"]["scanned_head"] = "0" * 40
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    with pytest.raises(preflight.PreflightError, match="graph is not refreshed"):
        _verify(baseline_fixture)

    graph["meta"]["scanned_head"] = baseline_fixture["revision"]
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    drift = [dict(row) for row in baseline_fixture["prior_design"]]
    drift[0]["sha256"] = "0" * 64
    with pytest.raises(preflight.PreflightError, match="prior Design drift"):
        _verify(baseline_fixture, prior_design=drift)


def test_only_live_is_enforced_and_advisory_must_be_bounded_and_attributable(
        baseline_fixture):
    result = _verify(baseline_fixture)
    assert result["enforcement"]["label"] == "advisory"
    assert result["enforcement"]["enforced"] is False

    incomplete = dict(baseline_fixture["advisory"])
    incomplete.pop("accepted_limitations")
    with pytest.raises(preflight.PreflightError,
                       match="bounded attributable advisory"):
        _verify(baseline_fixture, advisory_authorization=incomplete)

    unproven = dict(baseline_fixture["enforcement"], status="unproven")
    with pytest.raises(preflight.PreflightError,
                       match="enforcement is unproven"):
        _verify(baseline_fixture, enforcement=unproven,
                advisory_authorization=None)

    live = dict(baseline_fixture["enforcement"], status="live",
                advisory=None)
    result = _verify(baseline_fixture, enforcement=live,
                     advisory_authorization=None)
    assert result["enforcement"]["label"] == "enforced"
    assert result["enforcement"]["enforced"] is True


def test_hook_receipt_and_primary_main_checkout_are_authoritative(
        baseline_fixture):
    missing_receipt = dict(baseline_fixture["enforcement"])
    missing_receipt["receipt_evidence"] = {"effective_path": "transitioning"}
    with pytest.raises(preflight.PreflightError, match="hook-path receipt"):
        _verify(baseline_fixture, enforcement=missing_receipt)

    _git(baseline_fixture["workspace"], "checkout", "-qb", "topic")
    with pytest.raises(preflight.PreflightError, match="main revision"):
        _verify(baseline_fixture)
