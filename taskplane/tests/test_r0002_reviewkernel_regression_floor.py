"""R-0002/R-0003 floor for the shipped ReviewKernel reliability repairs.

These tests intentionally exercise the small, stable kernel contracts.  The
larger end-to-end fixtures named in ``REGRESSION_FIXTURES`` remain the owners
of host orchestration and Git lifecycle detail; this module prevents those
fixtures, or the kernel behavior they protect, from disappearing unnoticed.
"""
from __future__ import annotations

import ast
import copy
import importlib
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "taskplane"))
sys.path.insert(0, str(ROOT / "taskplane" / "tests"))

import evaluator_health  # noqa: E402
import review  # noqa: E402
import review_evidence  # noqa: E402
import review_repair  # noqa: E402
import taskplane_lite  # noqa: E402


def _lease(slot_id: str = "deep.security") -> dict:
    lens_id = slot_id.split(".")[-1]
    lease_fingerprint = "lease-" + lens_id
    return {
        "schema": "taskplane.slot-lease/v1",
        "lease_fingerprint": lease_fingerprint,
        "slot_id": slot_id,
        "lens_ids": [lens_id],
        "target_fingerprint": "target-1",
        "context_fingerprint": "context-1",
        "view_fingerprint": "view-" + lens_id,
        "canonical_revision": 1,
        "execution_binding": {
            "schema": "taskplane.review-execution-binding/v1",
            "repository_id": "repository-1",
            "repository_kind": "git-common-dir",
            "worktree_fingerprint": "worktree-1",
            "engine_fingerprint": "engine-1",
            "target": {"fingerprint": "target-1", "base": "base-1",
                       "head": "head-1"},
            "run_id": "original-run-1", "lens_ids": [lens_id],
            "slot_id": slot_id,
            "lease_fingerprint": lease_fingerprint,
            "producer": "lens-slot",
            "binding_fingerprint": "binding-" + lens_id,
        },
    }


def _finding(*, lens: str = "security", severity: str = "major") -> dict:
    return {
        "lens": lens,
        "kind": "defect",
        "severity": severity,
        "class": "regression",
        "file": "taskplane/review.py",
        "line": 17,
        "title": "Canonical evidence can be lost",
        "scenario": "collection accepts a contradictory producer summary",
        "fix": "derive the summary from admitted findings",
    }


def _result(lease: dict | None = None, *, findings=None) -> dict:
    lease = lease or _lease()
    return {
        "schema": "taskplane.lens-slot-output/v2",
        **{key: copy.deepcopy(lease[key]) for key in (
            "lease_fingerprint", "slot_id", "lens_ids",
            "target_fingerprint", "context_fingerprint",
            "view_fingerprint", "canonical_revision")},
        "authored_by": "lens-slot",
        "lens_results": [{
            "lens": lease["lens_ids"][0],
            "verdict": "pass",
            "blockers": 0,
            "checked_evidence": [{
                "file": "taskplane/review.py",
                "line": 17,
                "claim": "the collector boundary was inspected",
            }],
        }],
        "findings": copy.deepcopy(findings or []),
    }


def test_blocker_only_summary_is_normalized_once_without_rewriting_findings():
    lease = _lease()
    before = _result(lease, findings=[
        _finding(severity="major"),
        _finding(severity="blocker"),
    ])

    repaired = review_repair.normalize_slot_result(
        before, lease, canonical_findings=before["findings"])
    replay = review_repair.normalize_slot_result(
        repaired["result"], lease, canonical_findings=before["findings"])

    assert repaired["status"] == "repaired"
    assert repaired["producer_rerun_required"] is False
    assert repaired["result"]["findings"] == before["findings"]
    assert repaired["result"]["lens_results"][0]["verdict"] == "fail"
    assert repaired["result"]["lens_results"][0]["blockers"] == 2
    assert repaired["audit"]["derivation_authority"] == \
        "canonical-admissible-findings/v1"
    assert repaired["audit"]["equivalence"] == "proven"
    assert repaired["audit"]["equivalence_fingerprint_before"] == \
        repaired["audit"]["equivalence_fingerprint_after"]
    assert replay["status"] == "unchanged"
    assert replay["audit"]["changes"] == []


def test_substantive_mutation_reissues_only_the_affected_slot_and_keeps_lineage():
    lease = _lease()
    sibling = _lease("deep.qa")
    invalid = _result(lease)
    invalid["lens_results"][0]["checked_evidence"] = "mutated"

    recovery = review_repair.normalize_or_plan_retry(
        invalid, lease, canonical_findings=[], leases=[lease, sibling],
        valid_results={"deep.qa": "sealed-result-qa"}, attempts={})

    assert recovery["status"] == "retry"
    assert recovery["affected_slot_ids"] == ["deep.security"]
    assert recovery["retry_plan"]["reused_results"] == {
        "deep.qa": "sealed-result-qa"}
    assert recovery["retry_plan"]["producer_calls"] == [{
        "slot_id": "deep.security",
        "lease_fingerprint": lease["lease_fingerprint"],
        "attempt": 1,
        "reason": "slot result lens verdict is invalid",
        "execution_binding": lease["execution_binding"],
    }]


def test_exact_identity_and_once_complete_conservation_are_approval_floors(
        tmp_path):
    workspace = str(tmp_path / "repo")
    subprocess.run(["git", "init", "-q", workspace], check=True)
    target = {"fingerprint": "target-1", "base": "base-1", "head": "head-1"}
    identity = {
        "run_id": "run-1", "lens_ids": ["security"],
        "slot_id": "deep.security", "lease_fingerprint": "lease-security",
        "producer": "lens-slot",
    }
    binding = review_evidence.create_execution_binding(
        workspace, target=target, **identity)
    assert review_evidence.verify_execution_binding(
        workspace, binding, target=target, **identity) is True

    for field, changed in (
        ("run_id", "run-2"), ("slot_id", "deep.qa"),
        ("lease_fingerprint", "lease-stale"),
        ("producer", "copied-producer"),
    ):
        other = {**identity, field: changed}
        with pytest.raises(review_evidence.ProvenanceError,
                           match="execution binding"):
            review_evidence.verify_execution_binding(
                workspace, binding, target=target, **other)

    for field in ("engine_fingerprint", "worktree_fingerprint"):
        changed_binding = copy.deepcopy(binding)
        changed_binding[field] = "foreign-" + field
        with pytest.raises(review_evidence.ProvenanceError,
                           match="execution binding"):
            review_evidence.verify_execution_binding(
                workspace, changed_binding, target=target, **identity)

    collection = {
        "expected_slot_ids": ["deep.qa", "deep.security"],
        "collected_slot_ids": ["deep.qa", "deep.security"],
        "result_fingerprints": ["result-qa", "result-security"],
        "results": [{"slot_id": "deep.qa"}, {"slot_id": "deep.security"}],
        "gaps": [],
        "completeness": {"expected": 2, "collected": 2,
                         "missing": 0, "complete": True},
    }
    assert review_evidence.require_approvable_collection(collection) is True
    for mutation in ("missing", "duplicate"):
        broken = copy.deepcopy(collection)
        if mutation == "missing":
            broken["collected_slot_ids"].pop()
        else:
            broken["collected_slot_ids"].append("deep.security")
        with pytest.raises(review_evidence.ProvenanceError,
                           match="conservation"):
            review_evidence.require_approvable_collection(broken)


def test_contract_projection_is_shape_safe_for_every_review_lifecycle_shape():
    contracts = [
        {"task_id": "coding", "coding": {"scope_paths": ["src/**"]}},
        {"task_id": "read-only", "read_only": True,
         "write_allow": ["plan/**"]},
        {"task_id": "review-kernel", "read_only": True,
         "write_allow": [".em-review/kernel-v2/results/slot.json"]},
        {"task_id": "released", "read_only": True},
        {"task": "legacy-review", "scope": ["legacy/**"]},
    ]

    projected = [taskplane_lite.contract_projection(row) for row in contracts]

    assert projected[0]["display_scope"] == ["src/**"]
    assert projected[1]["display_scope"] == ["plan/**"]
    assert projected[2]["display_scope"] == [
        ".em-review/kernel-v2/results/slot.json"]
    assert projected[3]["scope_paths"] == []
    assert projected[4]["scope_paths"] == []


def test_reviewkernel_supported_runtime_smoke_uses_the_matrix_interpreter():
    """CI executes this behavioral smoke on Python 3.10, 3.11, and 3.12."""
    assert sys.version_info >= (3, 10)
    command = (
        "import dashboard,review,tp;"
        "assert review.review_execution_preflight()['status']=='needs_user';"
        "assert 'review' in dashboard.render_review_workflow("
        "status='ready',slots=[],graph_complete=True).lower();"
        "assert callable(tp.main)"
    )
    result = subprocess.run(
        [sys.executable, "-c", command], cwd=str(ROOT / "taskplane"),
        text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False)
    assert result.returncode == 0, result.stderr

    help_result = subprocess.run(
        [sys.executable, str(ROOT / "taskplane" / "tp.py"), "--help"],
        cwd=str(ROOT), text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False)
    assert help_result.returncode == 0, help_result.stderr
    assert help_result.stdout.startswith("usage: tp.py")


def test_unsupported_runtime_refuses_once_before_state_or_dependency_import(
        tmp_path):
    source = ROOT / "taskplane" / "tp.py"
    ast.parse(source.read_text(encoding="utf-8"), filename=str(source),
              feature_version=(3, 9))
    command = (
        "import runpy,sys;"
        "sys.version_info=(3,9,18,'final',0);"
        f"runpy.run_path({str(source)!r},run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-c", command], cwd=str(tmp_path),
        text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        "taskplane requires Python 3.10 or newer; found Python 3.9.18"]
    assert not (tmp_path / ".taskplane").exists()


def test_incomplete_active_review_run_stays_addressable(tmp_path):
    run_id = "a" * 32
    state = {
        "run_id": run_id,
        "status": "ready",
        "stage": "deep",
        "target": {"fingerprint": "target-1"},
        "manifest": {"schema": "taskplane.review-start-manifest/v2",
                     "run_id": run_id},
    }
    review._save_state(str(tmp_path), state)

    loaded = review._load_state(str(tmp_path), run_id)

    assert loaded["run_id"] == run_id
    assert loaded["status"] == "ready"
    assert review.resolve_review_workspace(str(tmp_path), run_id) == \
        os.path.realpath(tmp_path)


def test_review_continuation_choices_are_executable_and_consistent(monkeypatch):
    preflight = review.review_execution_preflight(
        run_id="b" * 32, runnability={"checks": []})
    choices = preflight["action"]["choices"]

    assert preflight["status"] == "needs_user"
    assert [row["response"] for row in choices] == [
        "dynamic", "dynamic-render", "static"]
    launcher = (("py" if os.name == "nt" else "python3") +
                " .taskplane/codex-hook.py review option ")
    assert [row["command"] for row in choices] == [
        launcher + "dynamic --run-id " + "b" * 32,
        launcher + "dynamic-render --run-id " + "b" * 32,
        launcher + "static --run-id " + "b" * 32,
    ]
    with monkeypatch.context() as platform:
        platform.setattr(review.os, "name", "nt")
        windows_choices = review.review_execution_preflight(
            run_id="b" * 32, runnability={"checks": []})["action"]["choices"]
    windows_launcher = "py .taskplane/codex-hook.py review option "
    assert [row["command"] for row in windows_choices] == [
        windows_launcher + "dynamic --run-id " + "b" * 32,
        windows_launcher + "dynamic-render --run-id " + "b" * 32,
        windows_launcher + "static --run-id " + "b" * 32,
    ]


def test_unproven_acceptance_evidence_cannot_become_approvable():
    model = review.production_review_model(
        {"run_id": "run-1", "target": {"head": "head-1"},
         "slots": [{"slot_id": "deep.qa", "lens_ids": ["qa"]}]},
        {"canonical_revision": 1, "target_fingerprint": "target-1",
         "context_fingerprint": "context-1",
         "findings_fingerprint": "findings-1", "findings": [],
         "disposition": "canonical", "recommendation": "complete",
         "completeness": {"expected": 1, "collected": 1,
                          "missing": 0, "complete": True},
         "gaps": [], "approval": {"enabled": True}},
        dor={"canonical": {"approvable": True, "criteria": [{
            "id": "AC-1", "text": "the change is verified"}]}},
        requirements_validation={"status": "needs_evidence", "criteria": [{
            "id": "AC-1", "criterion": "the change is verified",
            "status": "cannot_verify", "evidence": "no runnable proof"}]})

    assert model["collection"]["status"] == "complete"
    assert model["gate"]["approval_enabled"] is False
    assert model["gate"]["actions"] == []


# These are deliberately named as public node ids instead of copied here.
# The declared R-0003 radius executes the applicable routing/lifecycle/loop
# modules; this inventory makes deletion or renaming of an inherited fixture a
# visible regression in the focused floor too.
REGRESSION_FIXTURES = {
    "pr_commit_and_readme_dor": (
        "test_review_preflight",
        "test_review_dor_classifies_commit_claims_and_review_directives"),
    "dynamic_validation_consent": (
        "test_review_preflight",
        "test_explicit_dynamic_option_is_consent_without_magic_chat_phrase"),
    "scope_denial": ("test_dor_dod", "test_out_of_scope_change_fails"),
    "immutable_provenance": (
        "test_review_evidence_lifecycle",
        "test_collect_normalizes_derivable_summary_contradiction_in_one_call_without_producer_rerun"),
    "early_request_changes": (
        "test_review_evidence_lifecycle",
        "test_severe_harm_immediately_yields_immutable_request_changes"),
    "approval_conservation": (
        "test_review_evidence_lifecycle",
        "test_approval_collection_requires_exactly_once_conservation"),
}


@pytest.mark.parametrize("behavior", sorted(REGRESSION_FIXTURES))
def test_accepted_reviewkernel_fixture_inventory_is_retained(behavior):
    module_name, function_name = REGRESSION_FIXTURES[behavior]
    module = importlib.import_module(module_name)
    owners = [module, *(value for value in vars(module).values()
                        if inspect.isclass(value))]
    assert any(callable(getattr(owner, function_name, None)) for owner in owners)


def test_evaluator_outage_is_unavailable_and_cached_only_by_exact_identity(
        tmp_path):
    repository = tmp_path / "repository"
    sibling = tmp_path / "sibling"
    foreign = tmp_path / "foreign"
    for path in (repository, foreign):
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                       cwd=path, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=path,
                       check=True)
        (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=path,
                       check=True)
    subprocess.run(["git", "worktree", "add", "-q", "--detach",
                    str(sibling), "HEAD"], cwd=repository, check=True)

    cache = evaluator_health.EvaluatorHealthCache(str(tmp_path / "cache"))
    base = {
        "evaluator": "tp-evaluator",
        "evaluator_version": "2.17.11",
        "engine_fingerprint": "engine-1",
        "capability": "subagent",
        "recovery_fingerprint": "recovery-1",
    }
    key = evaluator_health.cache_key(str(repository), **base)
    cache.record_unavailable(
        key, failure={"status": "infrastructure-unavailable",
                      "reason_code": "orchestration_unavailable"},
        observed_at=100.0, valid_for=30.0)

    hit = cache.lookup(key, now=120.0)
    assert hit["evaluation"] == {
        "status": "unavailable", "reason_code": "orchestration_unavailable"}
    assert "verdict" not in hit["evaluation"]
    assert cache.lookup(key, now=131.0)["status"] == "miss"
    for field, changed in (
        ("evaluator", "other"), ("evaluator_version", "2.17.12"),
        ("engine_fingerprint", "engine-2"), ("capability", "workflow"),
        ("recovery_fingerprint", "recovery-2"),
    ):
        other = evaluator_health.cache_key(
            str(repository), **{**base, field: changed})
        assert cache.lookup(other, now=120.0)["status"] == "miss"
    for other_workspace in (sibling, foreign):
        other = evaluator_health.cache_key(str(other_workspace), **base)
        assert cache.lookup(other, now=120.0)["status"] == "miss"


def test_claimed_worktree_dod_and_evaluator_binding_fixtures_are_retained():
    module = importlib.import_module("test_loop")
    owners = [value for value in vars(module).values() if inspect.isclass(value)]
    methods = {name for owner in owners for name in vars(owner)}

    assert "test_parallel_execute_gate_validates_claimed_task_worktree" in methods
    assert "test_evaluator_evidence_binds_claimed_worktree_from_either_checkout" \
        in methods
