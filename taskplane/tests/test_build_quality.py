"""The retained generic progression belongs to independently selected CI."""
import json
from pathlib import Path

import pytest
from taskplane import build_quality, ci_policy

CI_CANDIDATE = Path(__file__).parent / "fixtures" / "ci-policy" / "candidate.json"


def test_separate_build_receipt_api_is_removed():
    for name in ("begin_receipt", "seal_layer_evidence", "advance_validation", "admit_build_quality", "validate_receipt", "BUILD_QUALITY_RECEIPT_SCHEMA_ID"):
        assert not hasattr(build_quality, name)


def test_ci_progression_keeps_default_local_refusal_and_real_ci_requirement():
    with pytest.raises(build_quality.BuildQualityError, match="broad local"):
        build_quality.advance_progression("a" * 64, "changed-radius", execution="local")
    with pytest.raises(build_quality.BuildQualityError, match="must run in CI"):
        build_quality.advance_progression("a" * 64, "authoritative-ci", execution="local")


def test_ci_adapter_consumes_the_same_progression_authority():
    candidate = ci_policy.freeze_candidate(
        json.loads(CI_CANDIDATE.read_text(encoding="utf-8"))
    )
    assert ci_policy.VALIDATION_LAYERS is build_quality.VALIDATION_LAYERS
    progression = None
    for layer, execution in (
        ("static", "local"),
        ("exact-selector", "local"),
        ("changed-radius", "ci"),
        ("proportional-suite", "ci"),
        ("authoritative-ci", "ci"),
    ):
        progression = ci_policy.advance_validation(
            candidate,
            layer,
            execution=execution,
            prior=progression,
        )

    assert progression["authoritative"] is True
    assert progression["completed"] == list(build_quality.VALIDATION_LAYERS)
