"""Targeted test-value evidence without inventory self-attestation."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
PORTFOLIO = ROOT / "taskplane" / "test_portfolio.json"


def _ledger() -> dict:
    return json.loads(PORTFOLIO.read_text(encoding="utf-8"))


def _selectors(ledger: dict) -> list[str]:
    values = [
        selector
        for removal in ledger["removals"]
        for selector in removal["replacement_selectors"]
    ]
    values.extend(row["selector"] for row in ledger["protected_contracts"])
    values.extend(
        selector
        for fixture in ledger["retained_fixtures"]
        for selector in fixture["consumer_selectors"]
    )
    values.extend(row["selector"] for row in ledger["audited_evidence"])
    return list(dict.fromkeys(values))


def _pytest(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *arguments], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", check=False,
    )


def test_ledger_is_explicitly_targeted_and_paths_match_the_current_tree() -> None:
    ledger = _ledger()

    assert ledger["schema"] == "taskplane.test-value-ledger/v1"
    assert ledger["scope"]["claim"] == "targeted-evidence-only"
    assert ledger["scope"]["complete_inventory_adjudication"] is False
    assert "files" not in ledger
    assert "evidence_revision" not in ledger

    removals = ledger["removals"]
    assert removals
    assert len({row["path"] for row in removals}) == len(removals)
    for row in removals:
        assert set(row) == {
            "path", "category", "reason", "replacement_selectors"}
        assert not (ROOT / row["path"]).exists()
        assert row["replacement_selectors"]

    for pattern in ledger["removed_fixture_families"]:
        assert not [path for path in ROOT.glob(pattern) if path.is_file()], \
            pattern
    for fixture in ledger["retained_fixtures"]:
        assert (ROOT / fixture["path"]).is_file()
        assert fixture["consumer_selectors"]


def test_every_ledger_selector_is_collected_by_pytest_not_inferred_from_ast(
) -> None:
    selectors = _selectors(_ledger())
    assert selectors

    collected = _pytest("--collect-only", "-q", *selectors)

    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert "no tests collected" not in collected.stdout


def test_audited_mechanisms_execute_behavioral_probes() -> None:
    ledger = _ledger()
    evidence = {row["selector"]: row for row in ledger["audited_evidence"]}
    cross_host = (
        "taskplane/tests/test_r0002_cross_host_journey.py::"
        "test_exact_dynamic_design_set_uses_portable_roles_and_host_receipts"
    )
    composition = (
        "taskplane/tests/test_r0002_control_plane_journey.py::"
        "test_dynamic_design_team_creates_one_portable_authorized_worker_per_lens"
    )
    probes = ledger["behavior_probes"]
    assert cross_host in probes
    assert composition in evidence
    assert composition not in probes
    assert set(probes) <= set(evidence) | {
        row["selector"] for row in ledger["protected_contracts"]}

    executed = _pytest("-q", *probes)

    assert executed.returncode == 0, executed.stdout + executed.stderr
