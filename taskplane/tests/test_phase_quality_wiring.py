"""Quality adapter wiring; synthetic layer fixtures are not execution proof."""
from __future__ import annotations

import json

from taskplane import checkpoint, phase_dispatch, phase_handoff
from taskplane.tests.test_phase_build_contract import native_build, _complete_build  # noqa: F401
import tp


def test_native_brief_preserves_quality_and_only_adds_exact_receipt_scope(native_build):
    root, handoff, assignment, _, module = native_build
    brief = phase_dispatch._hydrated_brief(str(root), handoff, assignment)
    assert brief["native_task"]["criteria"] == ["Task-local negative path remains covered"]
    quality = brief["completion"]["quality_admission"]
    assert quality["command"] == ["phase", "quality", "--request", brief["completion"]["request_path"]]
    contract = phase_dispatch._native_contract(str(root), brief, handoff)
    assert contract["coding"]["scope_paths"] == [*assignment["task"]["scope"], quality["path"]]
    assert brief["output_paths"] == assignment["task"]["scope"]
    assert quality["path"] == module.quality_path(handoff, assignment["task"])


def test_admitted_quality_is_portable_in_build_export(native_build):
    root, handoff, assignment, strategy, module = native_build
    receipt = _complete_build(strategy, module.begin_quality_receipt(str(root), handoff, assignment))
    local_path = root / module.quality_path(handoff, assignment["task"])
    local_path.parent.mkdir(parents=True)
    local_path.write_text(json.dumps(receipt), encoding="utf-8")
    authored = checkpoint.mint_phase_authoring_result(
        str(root), task=assignment["task"], assignment=assignment)
    quality = module.admit_quality(str(root), handoff, assignment, authored)
    # This test begins at the post-BUILD-C publication seam, not at execution.
    progress = []
    predecessor = handoff["progress_receipts"][-1]["fingerprint"]
    for index, obligation in enumerate(handoff["obligations"], len(handoff["progress_receipts"]) + 1):
        value = phase_handoff.create_progress_receipt(
            producer="engine:taskplane.phase-pickup/v1", sequence=index, phase="build",
            obligation_id=obligation["id"], task_id=assignment["task"]["id"],
            status="green", predecessor_receipt_fingerprint=predecessor,
            checkpoint_receipt_digest="7" * 64,
            integration_receipt_fingerprint="8" * 64)
        progress.append(value)
        predecessor = value["fingerprint"]
    result = tp._phase_publish_build_result(str(root), handoff, {
        "build_quality": quality, "progress_receipts": progress, "progress_receipt": progress[-1]})
    exported = json.loads((root / result["next_handoff"]["path"]).read_text(encoding="utf-8"))
    assert quality["artifact"] in exported["selected_artifacts"]
    published = root / quality["artifact"]["destination"]
    assert published.read_bytes() == phase_handoff.canonical_bytes(receipt)
    assert local_path.read_text(encoding="utf-8") == json.dumps(receipt)


def test_refusal_does_not_claim_zero_effects_after_mutating_boundary():
    untouched = tp._phase_refusal("handoff-malformed", "fixture")
    assert set(untouched["effects"].values()) == {0}
    partial = tp._phase_refusal("publication-conflict", "fixture",
                               possible_effects=("checkpoint", "publication"))
    assert partial["effects"] == {"dispatch": 0, "authoring": 0, "checkpoint": None,
                                  "publication": None, "integration": 0}
