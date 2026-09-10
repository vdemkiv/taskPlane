"""R-0006: Claude workflows are transports for canonical ReviewKernel slots."""
from __future__ import annotations

import json
import hashlib
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import evaluation_output as output  # noqa: E402
import review  # noqa: E402
import tp as cli  # noqa: E402


class _BriefStore:
    def __init__(self, brief):
        self.brief = brief

    def read(self, _ref):
        return self.brief




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


def test_evaluate_stage_brief_carries_the_complete_canonical_contract(tmp_path):
    contract = output.create_evaluator_contract(
        workspace=str(tmp_path), task="t1", capability_snapshot={
            "capabilities": {"native_structured_output": {
                "status": "unknown", "source": "test"}}})
    payload = {
        "step": "evaluate", "instruction": "evaluate", "task": {"id": "t1"},
        "output_contract": contract,
        "output_schema": contract["output_schema"],
        "resume_identity": output.resume_identity(contract),
        "max_attempts": contract["max_attempts"],
    }
    stage, workflow, problem = cli._stage_wave_run(payload)
    assert stage == "evaluate" and problem is None
    brief = workflow["args"]["briefs"][0]
    assert brief["output_schema"] == output.evaluator_output_schema()
    assert brief["output_contract"]["output_schema"] == brief["output_schema"]
    assert brief["resume_identity"] == output.resume_identity(contract)
