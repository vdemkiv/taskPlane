import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PORTFOLIO_PATH = ROOT / "taskplane" / "test_portfolio.json"
STRATEGY_PATH = ROOT / "taskplane" / "tests" / "fixtures" / "test-strategy" / "r0001.json"
SELECTOR = re.compile(r"^taskplane/tests/test_[^:]+\.py::(?:[A-Za-z_][A-Za-z0-9_]*::)*test_[A-Za-z0-9_]+$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _portfolio():
    return json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))


def _strategy_selectors():
    strategy = json.loads(STRATEGY_PATH.read_text(encoding="utf-8"))
    return {
        selector
        for criterion in strategy["acceptance_criteria"]
        for selector in criterion["selectors"]
    }


def _tracked_test_files():
    result = subprocess.run(
        [
            "git", "ls-files", "--cached", "--others", "--exclude-standard",
            "taskplane/tests/test_*.py",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return {
        line for line in result.stdout.splitlines()
        if line and (ROOT / line).is_file()
    }


def _selector_exists(selector):
    path_text, *parts = selector.split("::")
    path = ROOT / path_text
    if not path.is_file():
        return False
    source = path.read_text(encoding="utf-8")
    return re.search(rf"^\s*def {re.escape(parts[-1])}\s*\(", source, re.MULTILINE) is not None


def test_removed_tests_preserve_current_contract_coverage():
    portfolio = _portfolio()
    families = portfolio["families"]
    protected = _strategy_selectors()
    removed = [path for family in families for path in family["removed_files"]]

    assert portfolio["schema"] == "taskplane.test-portfolio/v1"
    assert re.fullmatch(r"[0-9a-f]{40}", portfolio["frozen_sha"])
    assert hashlib.sha256(STRATEGY_PATH.read_bytes()).hexdigest() == portfolio["authority"]["protected_selector_source_sha256"]
    assert len(families) >= portfolio["final"]["targets"]["families_min"]
    assert len(removed) == len(set(removed)) == portfolio["final"]["removed_files"]
    assert not {selector.split("::", 1)[0] for selector in protected}.intersection(removed)

    for family in families:
        assert family["classification"] in {
            "history-replay", "stale-fixture", "duplicate",
            "implementation-detail", "ceremonial",
        }
        assert family["authority"].strip()
        assert DIGEST.fullmatch(family["source_sha256"])
        assert family["before"]["files"] == len(family["removed_files"])
        assert family["before"]["cases"] > 0 and family["before"]["loc"] > 0
        assert family["after"] == {"files": 0, "cases": 0, "loc": 0}
        assert family["retained_selectors"]
        assert all(SELECTOR.fullmatch(selector) for selector in family["retained_selectors"])
        assert all(_selector_exists(selector) for selector in family["retained_selectors"])
        assert family["mutation_or_severed_edge"].strip()
        fixture_edges = family["fixtures_generators_consumers"]
        assert set(fixture_edges) == {"fixtures", "generators", "consumers", "disposition"}
        assert fixture_edges["consumers"] and fixture_edges["disposition"].strip()

    assert all(not (ROOT / path).exists() for path in removed)
    assert all(_selector_exists(selector) for selector in protected if selector.split("::", 1)[0] not in {
        "taskplane/tests/test_settings_inventory.py",
        "taskplane/tests/test_settings_flow_wiring.py",
    })
    assert {
        "security-and-authority",
        "cross-host-portability",
        "release-version-tag-provenance",
        "receipts-cache-and-owned-cleanup",
        "ci-supply-chain",
        "large-delivery-and-native-accessibility",
    } == set(portfolio["protected_floors"])


def test_portfolio_targets_are_met_without_count_only_deletion():
    portfolio = _portfolio()
    final = portfolio["final"]
    baseline = portfolio["baselines"]["current_pre_prune"]
    families = portfolio["families"]
    tracked = _tracked_test_files()

    assert len(tracked) == final["test_files"] <= final["targets"]["test_files_max"]
    assert baseline["test_files"] - final["removed_files"] + final["added_contract_files"] == final["test_files"]
    assert sum(row["before"]["files"] for row in families) == final["removed_files"]
    assert sum(row["before"]["cases"] for row in families) == final["removed_cases"]
    assert sum(row["before"]["loc"] for row in families) == final["removed_loc"]
    assert baseline["collected_cases"] - final["removed_cases"] + final["added_contract_cases"] == final["collected_cases"]
    assert baseline["test_loc"] - final["removed_loc"] + final["added_contract_loc"] == final["test_loc"]

    collected = subprocess.run(
        ["python3", "-m", "pytest", "--collect-only", "-q", "taskplane/tests"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    match = re.search(r"(\d+) tests collected", collected.stdout)
    assert match is not None
    assert int(match.group(1)) == final["collected_cases"] <= final["targets"]["collected_cases_max"]

    actual_loc = sum(
        (ROOT / path).read_text(encoding="utf-8").count("\n")
        for path in tracked
    )
    assert actual_loc == final["test_loc"]
    assert final["redundant_families_removed"] == len(families) >= final["targets"]["families_min"]
    assert all(row["authority"].strip() and row["retained_selectors"] for row in families)
