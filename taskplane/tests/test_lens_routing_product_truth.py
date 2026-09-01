"""Regression contract for the public focused lens-routing truth."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CURRENT_TRUTH_FILES = (
    "docs/routing-and-flows.md",
    "docs/lenses-and-knowledge.md",
    "docs/lens-catalog.md",
    "README.md",
)


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_current_product_truth_describes_the_same_dispatch_model() -> None:
    truth = "\n".join(_text(path) for path in CURRENT_TRUTH_FILES).lower()

    for required in (
        "all 26",
        "execute_deep",
        "execute_light",
        "product and design",
        "plan",
        "zero lens",
        "3–4",
        "direct evidence collector and judge",
        "no lens route",
        "disposition ledger",
        "retry/invalidation",
        "build, fix, evaluate",
        "final engineering review",
        "expanded-route",
        "plan-only",
        "d-0014",
        "human:vdemkiv",
    ):
        assert required in truth, required


def test_current_guides_do_not_reassert_superseded_normal_routing() -> None:
    current_guides = "\n".join(
        _text(path).lower()
        for path in CURRENT_TRUTH_FILES
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

    stale_evaluate_contracts = (
        r"(?:plan and evaluate|plan/evaluate).{0,100}(?:3\s*[–-]\s*4|risks?|route)",
        r"evaluate.{0,80}(?:executes|dispatches|runs).{0,40}3\s*[–-]\s*4",
        r"product, design, plan, and evaluate.{0,100}disposition",
        r"evaluate recomputes",
        r"evaluate.{0,80}dispatches only invalidated",
        r"canonical evaluate routing",
    )
    assert not [
        pattern for pattern in stale_evaluate_contracts
        if re.search(pattern, current_guides, flags=re.DOTALL)
    ]


def test_routed_stage_telemetry_is_bounded_and_excludes_evaluate() -> None:
    truth = " ".join(_text("docs/lenses-and-knowledge.md").lower().split())
    for required in (
        "selected count",
        "estimated and actual tokens",
        "runtime",
        "cache reuse",
        "invalidation cause",
        "512",
        "128 kib",
        "repository-relative",
        "lens-free evaluate emits no lens-route telemetry artifact",
        "evaluate neither recomputes nor reuses a lens route",
    ):
        assert required in truth, required


def test_generated_catalog_is_current_via_argv_safe_check() -> None:
    command = [sys.executable, "scripts/gen_lens_catalog.py", "--check"]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr)
                       if part).strip()
    assert completed.returncode == 0, (
        f"generated lens catalog is stale; argv={command!r}; output={output}"
    )
    assert "current" in completed.stdout, output
