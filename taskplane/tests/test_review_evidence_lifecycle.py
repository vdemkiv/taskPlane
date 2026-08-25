from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
sys.path.insert(0, os.path.join(ROOT, "taskplane", "tests"))

import evaluator_health  # noqa: E402
import review  # noqa: E402
import review_artifacts  # noqa: E402
import review_evidence  # noqa: E402
import review_repair  # noqa: E402


def _lease(slot: str = "deep.security") -> dict:
    return {
        "schema": "taskplane.slot-lease/v1",
        "lease_fingerprint": "lease-" + slot,
        "slot_id": slot,
        "lens_ids": [slot.split(".")[-1]],
        "target_fingerprint": "target-1",
        "context_fingerprint": "context-1",
        "view_fingerprint": "view-" + slot,
        "canonical_revision": 1,
    }


def _finding(*, severity: str = "minor", lens: str = "security",
             title: str = "bounded finding", kind: str = "defect",
             finding_class: str = "regression", **extra) -> dict:
    return {
        "lens": lens, "kind": kind, "severity": severity,
        "class": finding_class, "file": "taskplane/review.py", "line": 17,
        "title": title, "scenario": "the review can lose a real defect",
        "fix": "preserve the canonical evidence", **extra,
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
            "lens": lease["lens_ids"][0], "verdict": "pass", "blockers": 0,
            "checked_evidence": [{
                "file": "taskplane/review.py", "line": 17,
                "claim": "the canonical collector was inspected",
            }],
        }],
        "findings": copy.deepcopy(findings or []),
    }


def _write_fixture_slot_results(fixture, *, verdict_by_lens: dict[str, str],
                                run_id: str) -> None:
    """Author one sealed result per slot with lens-specific verdicts."""
    state = review._load_state(fixture.ws, run_id)
    store = review_evidence.ArtifactStore(fixture.ws)
    for index, slot in enumerate(state["slots"]):
        lease = store.read(slot["lease"])
        brief = store.read(slot["brief"])
        lens_results = []
        for lens_id in lease["lens_ids"]:
            verdict = verdict_by_lens.get(lens_id, "pass")
            lens_results.append({
                "lens": lens_id,
                "verdict": verdict,
                "blockers": 1 if verdict == "fail" else 0,
                **({"checked_evidence": [{
                    "file": "src/service.py",
                    "line": 1,
                    "claim": "reviewed the changed service behavior",
                }]} if verdict == "pass" else {}),
            })
        row = {
            **lease,
            "schema": "taskplane.lens-slot-output/v2",
            "authored_by": "lens-slot",
            "lens_results": lens_results,
            "findings": [],
        }
        if brief.get("language_references"):
            row["references_applied"] = list(brief["language_references"])
        content = json.dumps(row, sort_keys=True, separators=(",", ":"))
        event = {
            "session_id": f"lens-session-{state['run_id']}",
            "agent_id": f"lens-child-{state['run_id'][:8]}-{index}",
            "tool_name": "Write",
            "tool_input": {
                "file_path": slot["result_path"],
                "content": content,
            },
        }
        contract = {
            "task": brief["producer_contract"]["task"],
            "task_id": f"lens-contract-{index}",
            "read_only": True,
            "write_allow": [slot["result_path"]],
        }
        review.register_slot_producer(
            fixture.ws, event=event, contract=contract,
            task_slot=brief["producer_contract"]["task_slot"])
        review.record_slot_write_observation(
            fixture.ws, event=event, contract=contract,
            task_slot=brief["producer_contract"]["task_slot"])
        path = os.path.join(fixture.ws, slot["result_path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(content)


def _partial_revision(tmp_path, findings: list[dict], *, prior=None) -> dict:
    store = review_evidence.ArtifactStore(str(tmp_path))
    envelope = store.put("envelope", {
        "target_fingerprint": "target-1",
        "context_fingerprint": "context-1",
    })
    collected = {
        "canonical_revision": 1,
        "target_fingerprint": "target-1",
        "context_fingerprint": "context-1",
        "expected_slot_ids": ["deep.security", "deep.qa"],
        "collected_slot_ids": ["deep.security"],
        "slot_ids": ["deep.security"],
        "result_fingerprints": ["result-security"],
        "results": [{
            "slot_id": "deep.security", "source": "result-security.json",
            "result_fingerprint": "result-security", "findings": findings,
        }],
        "gaps": [{"slot_id": "deep.qa", "reason": "producer unavailable"}],
        "completeness": {"expected": 2, "collected": 1,
                         "missing": 1, "complete": False},
    }
    return review.build_review_revision(
        store, envelope, collected, prior_provisional=prior)


@pytest.mark.parametrize("finding", [
    _finding(severity="blocker"),
    _finding(severity="high"),
    _finding(severity="minor", title="Security vulnerability permits bypass",
             vulnerability=True),
    _finding(severity="minor", title="Destructive cleanup deletes user data",
             harmful=True),
])
def test_severe_harm_immediately_yields_immutable_request_changes(
        tmp_path, finding):
    revision = _partial_revision(tmp_path, [finding])

    assert revision["disposition"] == "provisional"
    assert revision["recommendation"] == "request-changes"
    assert revision["approval"]["enabled"] is False
    assert revision["gaps"] == [
        {"slot_id": "deep.qa", "reason": "producer unavailable"}]
    assert revision["findings"][0]["result_fingerprint"] == "result-security"


def test_non_trigger_and_invalidated_finding_do_not_publish_request_changes(
        tmp_path):
    revision = _partial_revision(tmp_path, [
        _finding(severity="minor", finding_class="observation"),
        _finding(severity="high", status="invalidated"),
    ])

    assert revision["recommendation"] == "incomplete"
    assert revision["severe_harm_triggers"] == []


def test_provisional_lineage_is_idempotent_and_supersedes_changed_evidence(
        tmp_path):
    first = _partial_revision(tmp_path, [_finding(severity="high")])
    replay = _partial_revision(
        tmp_path, [_finding(severity="high")], prior=first)
    changed = _partial_revision(
        tmp_path, [_finding(severity="high", title="different severe defect")],
        prior=first)

    assert replay["artifact"]["fingerprint"] == first["artifact"]["fingerprint"]
    assert replay["supersedes_provisional"] is None
    assert changed["supersedes_provisional"] == first["artifact"]["fingerprint"]


@pytest.mark.parametrize("mutation", [
    lambda row: row["collected_slot_ids"].pop(),
    lambda row: row["collected_slot_ids"].append("deep.security"),
    lambda row: row["result_fingerprints"].append("result-security"),
    lambda row: row["completeness"].update(complete=False),
    lambda row: row["gaps"].append({"slot_id": "deep.security", "reason": "x"}),
])
def test_approval_collection_requires_exactly_once_conservation(mutation):
    collected = {
        "expected_slot_ids": ["deep.qa", "deep.security"],
        "collected_slot_ids": ["deep.qa", "deep.security"],
        "result_fingerprints": ["result-qa", "result-security"],
        "results": [{"slot_id": "deep.qa"}, {"slot_id": "deep.security"}],
        "gaps": [],
        "completeness": {"expected": 2, "collected": 2,
                         "missing": 0, "complete": True},
    }
    broken = copy.deepcopy(collected)
    mutation(broken)

    with pytest.raises(review_evidence.ProvenanceError, match="conservation"):
        review_evidence.require_approvable_collection(broken)

    assert review_evidence.require_approvable_collection(collected) is True


def test_metadata_contradiction_repairs_from_canonical_findings_once():
    lease = _lease()
    before = _result(lease, findings=[_finding(severity="high")])

    repaired = review_repair.normalize_slot_result(
        before, lease, canonical_findings=before["findings"])
    replay = review_repair.normalize_slot_result(
        repaired["result"], lease, canonical_findings=before["findings"])

    summary = repaired["result"]["lens_results"][0]
    assert summary["verdict"] == "fail"
    assert summary["blockers"] == 1
    assert repaired["audit"]["derivation_authority"] == \
        "canonical-admissible-findings/v1"
    assert repaired["audit"]["equivalence"] == "proven"
    assert replay["status"] == "unchanged"


def test_substantive_mutation_reruns_only_the_affected_slot():
    lease = _lease()
    lease["execution_binding"] = {
        "schema": "taskplane.review-execution-binding/v1",
        "repository_id": "repository-1",
        "repository_kind": "git-common-dir",
        "worktree_fingerprint": "worktree-1",
        "engine_fingerprint": "engine-1",
        "target": {"fingerprint": "target-1", "base": "base-1",
                   "head": "head-1"},
        "run_id": "original-run-1", "lens_ids": ["security"],
        "slot_id": "deep.security",
        "lease_fingerprint": lease["lease_fingerprint"],
        "producer": "lens-slot", "binding_fingerprint": "binding-1",
    }
    result = _result(lease)
    result["lens_results"][0]["checked_evidence"] = "rewritten"
    sibling = _lease("deep.qa")

    planned = review_repair.normalize_or_plan_retry(
        result, lease, canonical_findings=[], leases=[lease, sibling],
        valid_results={"deep.qa": "result-qa"}, attempts={})

    assert planned["status"] == "retry"
    assert planned["affected_slot_ids"] == ["deep.security"]
    assert [row["slot_id"] for row in planned["retry_plan"][
        "producer_calls"]] == ["deep.security"]
    assert planned["retry_plan"]["producer_calls"][0][
        "execution_binding"] == lease["execution_binding"]
    assert planned["retry_plan"]["reused_results"] == {
        "deep.qa": "result-qa"}


def test_exact_execution_binding_rejects_every_identity_change(tmp_path):
    root = str(tmp_path)
    subprocess.run(["git", "init", "-q", root], check=True)
    target = {"fingerprint": "target-1", "base": "base-1", "head": "head-1"}
    expected = {
        "run_id": "run-1", "lens_ids": ["security"],
        "slot_id": "deep.security", "lease_fingerprint": "lease-security",
        "producer": "lens-slot",
    }
    binding = review_evidence.create_execution_binding(
        root, target=target, **expected)

    assert review_evidence.verify_execution_binding(
        root, binding, target=target, **expected) is True
    mutations = [
        ("target", {**target, "head": "head-2"}),
        ("run_id", "run-2"), ("lens_ids", ["qa"]),
        ("slot_id", "deep.qa"), ("lease_fingerprint", "lease-qa"),
        ("producer", "another-producer"),
    ]
    for field, value in mutations:
        args = dict(expected)
        actual_target = target
        if field == "target":
            actual_target = value
        else:
            args[field] = value
        with pytest.raises(review_evidence.ProvenanceError,
                           match="execution binding"):
            review_evidence.verify_execution_binding(
                root, binding, target=actual_target, **args)

    for field in ("engine_fingerprint", "worktree_fingerprint"):
        mutated_binding = copy.deepcopy(binding)
        mutated_binding[field] = "foreign-" + field
        with pytest.raises(review_evidence.ProvenanceError,
                           match="execution binding"):
            review_evidence.verify_execution_binding(
                root, mutated_binding, target=target, **expected)

    sibling = tmp_path.parent / (tmp_path.name + "-sibling")
    sibling.mkdir()
    subprocess.run(["git", "init", "-q", str(sibling)], check=True)
    with pytest.raises(review_evidence.ProvenanceError,
                       match="execution binding"):
        review_evidence.verify_execution_binding(
            str(sibling), binding, target=target, **expected)


def test_evaluator_outage_cache_is_exact_expiring_and_never_a_verdict(tmp_path):
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
    key = evaluator_health.cache_key(
        str(repository), evaluator="tp-evaluator",
        evaluator_version="2.17.8",
        engine_fingerprint="engine-1", capability="subagent",
        recovery_fingerprint="recovery-1")
    cache.record_unavailable(
        key, failure={"status": "infrastructure-unavailable",
                      "reason_code": "orchestration_unavailable"},
        observed_at=100.0, valid_for=30.0)

    hit = cache.lookup(key, now=120.0)
    assert hit["status"] == "hit"
    assert hit["evaluation"] == {
        "status": "unavailable", "reason_code": "orchestration_unavailable"}
    assert "verdict" not in hit and "verdict" not in hit["evaluation"]
    assert cache.lookup(key, now=131.0)["status"] == "miss"

    for changed in (
        {"evaluator": "another-evaluator"},
        {"evaluator_version": "2.17.9"}, {"engine_fingerprint": "engine-2"},
        {"capability": "workflow"}, {"recovery_fingerprint": "recovery-2"},
    ):
        args = {
            "evaluator": "tp-evaluator", "evaluator_version": "2.17.8",
            "engine_fingerprint": "engine-1", "capability": "subagent",
            "recovery_fingerprint": "recovery-1", **changed,
        }
        other = evaluator_health.cache_key(str(repository), **args)
        assert cache.lookup(other, now=120.0)["status"] == "miss"
    for other_workspace in (sibling, foreign):
        other = evaluator_health.cache_key(
            str(other_workspace), evaluator="tp-evaluator",
            evaluator_version="2.17.8", engine_fingerprint="engine-1",
            capability="subagent", recovery_fingerprint="recovery-1")
        assert cache.lookup(other, now=120.0)["status"] == "miss"


def test_exact_cached_outage_avoids_only_the_repeated_launch(tmp_path):
    cache = evaluator_health.EvaluatorHealthCache(str(tmp_path / "cache"))
    base = {
        "evaluator": "tp-evaluator", "evaluator_version": "2.17.8",
        "engine_fingerprint": "engine-1", "capability": "subagent",
        "recovery_fingerprint": "recovery-1",
    }
    key = evaluator_health.cache_key(str(tmp_path), **base)
    launches = []

    def unavailable():
        launches.append("launch")
        return {"status": "infrastructure-unavailable",
                "reason_code": "orchestration_unavailable"}

    first = evaluator_health.evaluate_or_reuse(
        cache, key, launcher=unavailable, now=100.0, valid_for=30.0)
    reused = evaluator_health.evaluate_or_reuse(
        cache, key, launcher=unavailable, now=110.0, valid_for=30.0)
    recovered_key = evaluator_health.cache_key(
        str(tmp_path), **{**base, "recovery_fingerprint": "recovery-2"})
    changed = evaluator_health.evaluate_or_reuse(
        cache, recovered_key, launcher=unavailable, now=110.0, valid_for=30.0)

    assert first["status"] == "launched"
    assert reused["status"] == "hit"
    assert changed["status"] == "launched"
    assert launches == ["launch", "launch"]


def test_canonical_result_keeps_normalized_summary_and_repair_lineage(tmp_path):
    store = review_evidence.ArtifactStore(str(tmp_path))
    lease = _lease()
    lease_ref = store.put("lease", lease)
    before = _result(lease, findings=[_finding(severity="high")])
    normalized = review_repair.normalize_slot_result(
        before, lease, canonical_findings=before["findings"])

    result_ref = review_evidence.write_slot_result(
        store, lease_ref, authored_slot=lease["slot_id"],
        lens_ids=lease["lens_ids"], findings=before["findings"],
        lens_results=normalized["result"]["lens_results"],
        repair_audit=normalized["audit"])
    result = store.read(result_ref)

    assert result["findings"] == before["findings"]
    assert result["lens_results"][0]["verdict"] == "fail"
    assert result["repair_audit"]["equivalence"] == "proven"


def test_real_collector_consumes_repair_and_exact_execution_binding():
    from test_review_routing import TestSelectiveReviewKernel

    fixture = TestSelectiveReviewKernel()
    fixture.setUp()
    try:
        started = fixture._start()
        state = review._load_state(fixture.ws, started["run_id"])
        store = review_evidence.ArtifactStore(fixture.ws)
        for slot in state["slots"]:
            lease = store.read(slot["lease"])
            assert review_evidence.verify_execution_binding(
                fixture.ws, lease["execution_binding"],
                target=state["target"], run_id=state["run_id"],
                lens_ids=lease["lens_ids"], slot_id=lease["slot_id"],
                lease_fingerprint=lease["lease_fingerprint"],
                producer=lease["producer"])
        fixture._write_slot_results(verdict="fail", findings=lambda lease: [{
            **_finding(severity="high", lens=lease["lens_ids"][0]),
            "claim": {"trigger": "exercise the changed review collector",
                      "outcome": "the evidence lifecycle is broken",
                      "repro": "collect the leased fixture output"},
        }])

        manifest = review.collect_review(
            fixture.ws, publish=False, run_id=started["run_id"])
        collected = review._load_state(fixture.ws, started["run_id"])
        canonical_results = [store.read(ref)
                             for ref in store.references("slot-result")]

        assert manifest["status"] == "complete"
        assert manifest["review_depth_policy"]["outcome"] == \
            "correction_required"
        assert manifest["quick_corrections"]
        assert all(row["action"] == "return-same-task-for-correction"
                   and row["deep_dispatch"] is False
                   for row in manifest["quick_corrections"])
        assert "adaptive_wave" not in collected
        assert review.blocking_findings_by_lens(
            collected["revision"]["findings"])
        assert canonical_results
        assert all(row["repair_audit"]["equivalence"] == "proven"
                   for row in canonical_results)
        assert all(row["lens_results"][0]["verdict"] == "fail"
                   for row in canonical_results)
    finally:
        fixture.tearDown()


def test_collect_normalizes_derivable_summary_contradiction_in_one_call_without_producer_rerun():
    from test_review_routing import TestSelectiveReviewKernel

    fixture = TestSelectiveReviewKernel()
    fixture.setUp()
    try:
        def producer_outputs(slots):
            outputs = {}
            for slot in slots:
                with open(os.path.join(fixture.ws, slot["result_path"]),
                          "rb") as stream:
                    outputs[slot["slot_id"]] = stream.read()
            return outputs

        started = fixture._start()
        fixture._write_slot_results(
            verdict="pass", findings=lambda lease: []
            if lease["slot_id"] == "light-sweep" else [{
                **_finding(severity="high", lens=lease["lens_ids"][0]),
                "claim": {
                    "trigger": "collect a pass/zero producer summary",
                    "outcome": "canonical blocking findings require fail/one",
                    "repro": "collect the sealed fixture output once",
                },
            }])
        before = review._load_state(fixture.ws, started["run_id"])
        original_outputs = producer_outputs(before["slots"])

        manifest = review.collect_review(
            fixture.ws, publish=False, run_id=started["run_id"])
        after = review._load_state(fixture.ws, started["run_id"])
        store = review_evidence.ArtifactStore(fixture.ws)
        validations = [store.read(ref)
                       for ref in after["result_validations"]]

        assert manifest["status"] == "complete"
        assert manifest.get("gaps") in (None, [])
        assert after["counters"]["dispatched_agent_count"] == \
            before["counters"]["dispatched_agent_count"]
        assert producer_outputs(before["slots"]) == original_outputs
        assert validations
        assert all(row["repair"]["equivalence"] == "proven"
                   for row in validations)
        assert all(row["repair"]["equivalence_fingerprint_before"] ==
                   row["repair"]["equivalence_fingerprint_after"]
                   for row in validations)
        changed_repairs = [row["repair"] for row in validations
                           if row["repair"]["changes"]]
        assert changed_repairs
        assert all(
            {change["path"] for change in repair["changes"]} == {
                "lens_results[0].blockers", "lens_results[0].verdict"}
            for repair in changed_repairs)
    finally:
        fixture.tearDown()


def test_collect_rejects_fail_to_pass_normalization_without_checked_evidence_and_retries_only_original_producer(
):
    from test_review_routing import TestSelectiveReviewKernel

    fixture = TestSelectiveReviewKernel()
    fixture.setUp()
    try:
        started = fixture._start(
            retry_lenses={"security"}, retry_source_run_id="a" * 32)
        before = review._load_state(fixture.ws, started["run_id"])
        assert 4 <= len(before["slots"]) <= 5
        original = next(slot for slot in before["slots"]
                        if slot["lens_ids"] == ["security"])
        original_brief = review_evidence.ArtifactStore(fixture.ws).read(
            original["brief"])
        original_task = original_brief["role"]["task_name"]

        # A producer may report fail without checked evidence. With no
        # canonical blocking finding, changing that to pass would invent a
        # positive judgment unsupported by anything the producer inspected.
        _write_fixture_slot_results(
            fixture, verdict_by_lens={"security": "fail"},
            run_id=started["run_id"])
        manifest = review.collect_review(
            fixture.ws, publish=False, run_id=started["run_id"])
        after = review._load_state(fixture.ws, started["run_id"])

        assert manifest["status"] != "complete"
        assert manifest["approval"]["enabled"] is False
        assert after["status"] == "ready"
        assert len(manifest["gaps"]) == 1
        assert manifest["gaps"][0]["slot_id"] == original["slot_id"]
        assert manifest["gaps"][0]["producer_task"] == original_task
        assert "checked evidence" in manifest["gaps"][0]["reason"]
    finally:
        fixture.tearDown()


def test_complete_collection_still_cannot_approve_unproven_acceptance():
    revision = {
        "canonical_revision": 1, "target_fingerprint": "target-1",
        "context_fingerprint": "context-1",
        "findings_fingerprint": "findings-1", "findings": [],
        "disposition": "canonical", "recommendation": "complete",
        "completeness": {"expected": 1, "collected": 1,
                         "missing": 0, "complete": True},
        "gaps": [], "approval": {"enabled": True},
    }
    state = {"run_id": "run-1", "target": {"head": "head-1"},
             "slots": [{"slot_id": "deep.qa", "lens_ids": ["qa"]}]}
    dor = {"canonical": {"approvable": True, "criteria": [{
        "id": "AC-1", "text": "the change is verified"}]}}
    validation = {"status": "needs_evidence", "criteria": [{
        "id": "AC-1", "criterion": "the change is verified",
        "status": "cannot_verify", "evidence": "no runnable proof"}]}

    model = review.production_review_model(
        state, revision, dor=dor, requirements_validation=validation)

    assert model["collection"]["status"] == "complete"
    assert model["gate"]["approval_enabled"] is False
    assert model["gate"]["actions"] == []


def test_large_revision_round_trip_keeps_canonical_and_rendered_evidence(
        tmp_path):
    findings = [{
        "id": f"F-{index}", "lens": "security", "severity": "high",
        "title": (f"Finding {index} " + "evidence " * 400),
        "rationale": "known evidence", "action": "repair it",
        "provenance": [{"slot_id": "deep.security"}],
    } for index in range(8)]
    revision = {
        "canonical_revision": 3, "target_fingerprint": "a" * 64,
        "context_fingerprint": "b" * 64,
        "findings_fingerprint": review_evidence.content_fingerprint(findings),
        "findings": findings, "disposition": "provisional",
        "recommendation": "request-changes",
        "completeness": {"expected": 2, "collected": 1,
                         "missing": 1, "complete": False},
        "gaps": [{"slot_id": "deep.qa", "reason": "producer unavailable"}],
        "approval": {"enabled": False},
    }
    state = {
        "run_id": "run-large", "target": {"head": "head-large"},
        "slots": [
            {"slot_id": "deep.security", "lens_ids": ["security"]},
            {"slot_id": "deep.qa", "lens_ids": ["qa"]},
        ],
    }
    dor = {"canonical": {"status": "ready", "approvable": True,
                          "sources": [], "criteria": []}}
    validation = {"status": "needs_evidence", "criteria": []}

    output = review.publish_production_review(
        str(tmp_path), state, revision, dor=dor,
        requirements_validation=validation, host="codex")
    publication = output["publication"]
    json_path = tmp_path / publication["artifacts"]["json"]["relative_path"]
    markdown_path = tmp_path / publication["artifacts"]["markdown"][
        "relative_path"]
    canonical = review_artifacts.parse_artifact("json", json_path.read_bytes())
    rendered = review_artifacts.parse_artifact(
        "markdown", markdown_path.read_bytes())

    assert canonical["findings"] == findings
    assert rendered["findings"] == findings
    assert canonical["collection"]["gaps"] == revision["gaps"]
    assert output["inline_pages"]
    assert all(page["transport"]["host"] == "codex"
               for page in output["inline_pages"])
