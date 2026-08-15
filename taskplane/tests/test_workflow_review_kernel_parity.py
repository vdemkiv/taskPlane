"""R-0006: Claude workflows are transports for canonical ReviewKernel slots."""
from __future__ import annotations

import json
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import evaluation_output as output  # noqa: E402
import review  # noqa: E402


def _source(name: str) -> str:
    with open(os.path.join(ROOT, "workflows", name), encoding="utf-8") as stream:
        return stream.read()


def _lease():
    return {
        "lease_fingerprint": "lease-a", "slot_id": "deep.backend",
        "lens_ids": ["backend"], "target_fingerprint": "target-a",
        "context_fingerprint": "context-a", "view_fingerprint": "view-a",
        "canonical_revision": 7,
    }


def test_review_resume_identity_binds_the_complete_leased_slot_contract():
    schema = output.lens_slot_output_schema()
    producer = {
        "task": "review lens slot deep.backend lease lease-a",
        "task_slot": "review-lease-a", "read_only": True,
        "write_allow": [".em-review/kernel-v2/results/lease-a.json"],
    }
    base = review.review_slot_resume_identity(
        lease=_lease(), result_schema=schema, producer_contract=producer,
        result_path=producer["write_allow"][0])
    for field in ("target_fingerprint", "context_fingerprint",
                  "view_fingerprint", "lease_fingerprint", "slot_id",
                  "canonical_revision"):
        changed = _lease()
        changed[field] = "different" if field != "canonical_revision" else 8
        assert review.review_slot_resume_identity(
            lease=changed, result_schema=schema, producer_contract=producer,
            result_path=producer["write_allow"][0]) != base
    changed_schema = json.loads(json.dumps(schema))
    changed_schema["title"] = "different"
    assert review.review_slot_resume_identity(
        lease=_lease(), result_schema=changed_schema,
        producer_contract=producer,
        result_path=producer["write_allow"][0]) != base
    changed_producer = dict(producer, task_slot="review-other")
    assert review.review_slot_resume_identity(
        lease=_lease(), result_schema=schema,
        producer_contract=changed_producer,
        result_path=producer["write_allow"][0]) != base


def test_review_kernel_uses_the_canonical_strict_lens_schema():
    schema = output.lens_slot_output_schema()
    assert schema["$id"] == review.RESULT_SCHEMA
    assert schema["additionalProperties"] is False
    assert schema["properties"]["findings"]["items"][
        "additionalProperties"] is False
    assert review.result_schema_for_slot([]) == schema


def test_review_workflow_consumes_slot_schema_identity_and_returns_receipts():
    source = _source("review-wave.js")
    for token in ("args.slots", "b.result_schema", "b.resume_identity",
                  "b.result_path", "b.lease", "maxAttempts", "receipts"):
        assert token in source
    assert "per_lens" not in source
    assert "routing_decision" not in source
    assert "loop gate" not in source.lower()
    assert "loop approve" not in source.lower()


def test_evaluate_workflow_uses_declared_evaluator_contract_and_receipts_only():
    source = _source("evaluate-wave.js")
    for token in ("output_contract", "output_schema", "resume_identity",
                  "max_attempts", "receipts"):
        assert token in source
    assert "verdicts:" not in source


def test_all_stage_workflows_declare_strict_versioned_output_schemas():
    for name in ("execute-wave.js", "evaluate-wave.js", "fix-wave.js"):
        source = _source(name)
        assert "'$schema': 'https://json-schema.org/draft/2020-12/schema'" \
            in source
        assert "'$id': 'taskplane." in source
        assert "additionalProperties: false" in source
        assert "output_contract" in source
        for forbidden in ("loop.gate", "loop.approve", "loop.advance"):
            assert forbidden not in source


def test_agent_roles_require_schema_validation_and_worker_stop_boundary():
    for name in ("tp-evaluator.md", "tp-engineering.md", "tp-lens.md"):
        source = open(os.path.join(ROOT, "agents", name), encoding="utf-8").read()
        assert "output schema" in source.lower()
        assert "host-observed" in source.lower()
        assert "never call `loop gate`" in source.lower()


def test_runtime_guidance_requires_declared_validated_observed_output():
    path = os.path.join(
        ROOT, "skills", "taskplane", "references", "runtime-evals.json")
    controls = json.load(open(path, encoding="utf-8"))["controls"]
    output_control = next(row for row in controls
                          if row["id"] == "schema-output-before-submit")
    assert output_control["required_facts"] == [
        "output_schema_declared", "output_schema_validated",
        "output_producer_observed"]
    assert output_control["max_corrections"] == 1
