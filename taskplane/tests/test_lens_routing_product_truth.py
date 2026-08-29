"""Regression contract for the public focused lens-routing truth."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TRUTH_FILES = (
    "specs/spec.md",
    "design/design.md",
    "design/visual.html",
    "plan/plan.md",
    "docs/routing-and-flows.md",
    "docs/lenses-and-knowledge.md",
    "docs/lens-catalog.md",
    "docs/configuration.md",
    "docs/onboarding.md",
    "README.md",
)


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_machine_truth_declares_complete_focused_stage_contract() -> None:
    contract = json.loads(_text("design/contract.json"))
    tasks = json.loads(_text("plan/tasks.json"))

    contract_ids = {row["id"] for row in contract["contracts"]}
    assert {
        "contract:lens.focused-stage-routing",
        "contract:review.catalog-disposition",
        "contract:delivery.stage-lens-execution",
        "contract:authority.expanded-lens-route",
        "resource:authority.expanded-lens-route-custody",
        "resource:review.route-fingerprint",
        "resource:telemetry.lens-route",
    } <= contract_ids

    policy = tasks["delivery_policy"]
    assert policy["build_lens_workers"] == 0
    assert policy["fix_lens_workers"] == 0
    assert "three-or-four" in policy["evaluate"]
    dispositions = tasks["plan_route"]["dispositions"]
    assert len(dispositions) == 26
    assert len({row["lens"] for row in dispositions}) == 26
    assert {row["disposition"] for row in dispositions} <= {
        "execute_deep", "execute_light", "covered_by", "not_applicable"
    }
    assert all(row.get("evidence") and row.get("reason") for row in dispositions)


def test_current_product_truth_describes_the_same_dispatch_model() -> None:
    truth = "\n".join(_text(path) for path in TRUTH_FILES).lower()

    for required in (
        "all 26",
        "execute_deep",
        "execute_light",
        "build and fix",
        "zero lens",
        "3–4",
        "fingerprint",
        "redact",
        "expanded-route",
        "protected",
    ):
        assert required in truth, required


def test_current_guides_do_not_reassert_superseded_normal_routing() -> None:
    current_guides = "\n".join(
        _text(path).lower()
        for path in (
            "docs/routing-and-flows.md",
            "docs/lenses-and-knowledge.md",
            "docs/lens-catalog.md",
            "docs/configuration.md",
            "docs/onboarding.md",
            "README.md",
        )
    )
    forbidden = (
        "design 8 · build 5 · review 26",
        "engine failure fails open to the full catalog",
        "fail-open ladder only ever widens",
        "cap-8, demote-never-drop",
        "overflow is demoted to `light`",
        "only `deep` slots plus at most one",
        "exactly 4–5 relevant light-sweep agents",
        "architecture is routed on every code change",
    )
    assert not [phrase for phrase in forbidden if phrase in current_guides]


def test_guides_name_bounded_private_route_telemetry_and_selective_reuse() -> None:
    truth = "\n".join(
        _text(path).lower()
        for path in (
            "docs/routing-and-flows.md",
            "docs/configuration.md",
            "README.md",
        )
    )
    for required in (
        "selected count",
        "estimated and actual tokens",
        "runtime",
        "cache reuse",
        "invalidation cause",
        "512",
        "128 kib",
        "repository-relative",
        "only invalidated",
    ):
        assert required in truth, required
