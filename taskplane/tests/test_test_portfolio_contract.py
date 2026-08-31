import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PORTFOLIO_PATH = ROOT / "taskplane" / "test_portfolio.json"
STRATEGY_PATH = (
    ROOT / "taskplane" / "tests" / "fixtures" / "test-strategy" / "r0001.json"
)
PLAN_PATH = ROOT / "plan" / "tasks.json"
SELECTOR = re.compile(
    r"^taskplane/tests/test_[^:]+\.py::"
    r"(?:[A-Za-z_][A-Za-z0-9_]*::)*test_[A-Za-z0-9_]+$"
)
DIGEST = re.compile(r"^[0-9a-f]{64}$")
PROOF_GUARD = "TASKPLANE_PORTFOLIO_PROOF_CHILD"
HISTORY_PREFIXES = ("exports/verification/",)


def _portfolio():
    return json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))


def _strategy_selectors():
    strategy = json.loads(STRATEGY_PATH.read_text(encoding="utf-8"))
    return {
        selector
        for criterion in strategy["acceptance_criteria"]
        for selector in criterion["selectors"]
    }


def _tracked_files():
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return {
        line
        for line in result.stdout.splitlines()
        if line and (ROOT / line).is_file()
    }


def _tracked_test_files():
    return {
        path
        for path in _tracked_files()
        if path.startswith("taskplane/tests/test_") and path.endswith(".py")
    }


def _git_blob(revision, path):
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _family_source_digest(revision, paths):
    material = bytearray()
    for path in sorted(paths):
        blob = _git_blob(revision, path)
        material.extend(path.encode("utf-8"))
        material.extend(b"\0")
        material.extend(hashlib.sha256(blob).hexdigest().encode("ascii"))
        material.extend(b"\0")
        material.extend(str(len(blob)).encode("ascii"))
        material.extend(b"\n")
    return hashlib.sha256(material).hexdigest()


def _inventory_digest(paths):
    material = "".join(f"{path}\n" for path in sorted(paths)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _collect_nodeids(selectors):
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *selectors],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if line.startswith("taskplane/tests/") and "::" in line
    }


def _assert_exact_nodeids_collect(selectors):
    assert selectors
    assert all(SELECTOR.fullmatch(selector) for selector in selectors)
    nodeids = _collect_nodeids(sorted(selectors))
    for selector in selectors:
        assert any(
            nodeid == selector or nodeid.startswith(selector + "[")
            for nodeid in nodeids
        ), selector


def _deleted_reference_kind(content, deleted):
    if deleted.encode("utf-8") in content:
        return "full-path"
    if Path(deleted).name.encode("utf-8") in content:
        return "basename"
    return None


def _dangling_consumers(removed):
    dangling = []
    for path in sorted(_tracked_files()):
        if path == "taskplane/test_portfolio.json" or path.startswith(
            HISTORY_PREFIXES
        ):
            continue
        content = (ROOT / path).read_bytes()
        for deleted in removed:
            reference_kind = _deleted_reference_kind(content, deleted)
            if reference_kind is not None:
                dangling.append((path, deleted, reference_kind))
    return dangling


def _execute_proofs(selectors):
    if os.environ.get(PROOF_GUARD) == "1":
        return
    environment = dict(os.environ)
    environment[PROOF_GUARD] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *selectors],
        cwd=ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_removed_tests_preserve_current_contract_coverage():
    portfolio = _portfolio()
    families = portfolio["families"]
    protected = _strategy_selectors()
    removed = [path for family in families for path in family["removed_files"]]
    future_rows = portfolio["future_selectors"]
    future = {row["selector"] for row in future_rows}

    assert portfolio["schema"] == "taskplane.test-portfolio/v1"
    assert re.fullmatch(r"[0-9a-f]{40}", portfolio["frozen_sha"])
    assert hashlib.sha256(STRATEGY_PATH.read_bytes()).hexdigest() == (
        portfolio["authority"]["protected_selector_source_sha256"]
    )
    assert len(families) >= portfolio["final"]["targets"]["families_min"]
    assert len(removed) == len(set(removed)) == portfolio["final"]["removed_files"]
    assert _inventory_digest(removed) == portfolio["inventory_sha256"]
    assert not {
        selector.split("::", 1)[0] for selector in protected
    }.intersection(removed)

    retained = set()
    for family in families:
        assert family["classification"] in {
            "history-replay",
            "stale-fixture",
            "duplicate",
            "implementation-detail",
            "ceremonial",
        }
        assert family["authority"].strip()
        assert DIGEST.fullmatch(family["source_sha256"])
        assert family["source_sha256"] == _family_source_digest(
            portfolio["frozen_sha"], family["removed_files"]
        )
        assert family["before"]["files"] == len(family["removed_files"])
        assert family["before"]["cases"] > 0 and family["before"]["loc"] > 0
        assert family["after"] == {"files": 0, "cases": 0, "loc": 0}
        assert family["retained_selectors"]
        retained.update(family["retained_selectors"])

        mutation = family["mutation_proof"]
        assert set(mutation) == {"kind", "operator", "selector"}
        assert mutation["kind"] in {"mutation", "severed-edge"}
        assert len(mutation["operator"].split()) >= 4
        assert mutation["selector"] in family["retained_selectors"]
        assert SELECTOR.fullmatch(mutation["selector"])

        fixture_edges = family["fixtures_generators_consumers"]
        assert set(fixture_edges) == {
            "fixtures", "generators", "consumers", "disposition"
        }
        assert fixture_edges["consumers"] and fixture_edges["disposition"].strip()

    assert all(not (ROOT / path).exists() for path in removed)
    deleted_path = "taskplane/tests/" + "test_" + "views_seam.py"
    basename_reference = b"retained test_" + b"views_seam.py"
    assert _deleted_reference_kind(basename_reference, deleted_path) == "basename"
    assert _dangling_consumers(removed) == []

    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    tasks = {task["id"]: task for task in plan["tasks"]}
    missing_protected = {
        selector for selector in protected
        if not (ROOT / selector.split("::", 1)[0]).is_file()
    }
    assert missing_protected == set()
    assert future == set()
    assert future_rows == []
    for row in future_rows:
        assert set(row) == {"selector", "owner_task", "depends_on", "relation"}
        assert row["owner_task"] == "SET-CONFORMANCE"
        assert row["depends_on"] == "TEST-PORTFOLIO"
        assert row["relation"] == "ordered-successor"
        owner = tasks[row["owner_task"]]
        assert row["depends_on"] in owner["deps"]
        assert row["selector"] in owner["tests"]

    current_protected = protected - future
    _assert_exact_nodeids_collect(retained | current_protected)
    proofs = portfolio["executable_proofs"]
    assert set(proofs).issubset(retained | current_protected)
    _assert_exact_nodeids_collect(set(proofs))
    _execute_proofs(proofs)

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
    assert (
        baseline["test_files"] - final["removed_files"]
        + final["added_contract_files"] == final["test_files"]
    )
    assert sum(row["before"]["files"] for row in families) == final["removed_files"]
    assert sum(row["before"]["cases"] for row in families) == final["removed_cases"]
    assert sum(row["before"]["loc"] for row in families) == final["removed_loc"]
    assert (
        baseline["collected_cases"] - final["removed_cases"]
        + final["added_contract_cases"] == final["collected_cases"]
    )
    assert (
        baseline["test_loc"] - final["removed_loc"]
        + final["added_contract_loc"] == final["test_loc"]
    )

    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "taskplane/tests"],
        cwd=ROOT,
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    match = re.search(r"(\d+) tests collected", collected.stdout)
    assert match is not None
    assert (
        int(match.group(1)) == final["collected_cases"]
        <= final["targets"]["collected_cases_max"]
    )

    actual_loc = sum(
        (ROOT / path).read_text(encoding="utf-8").count("\n")
        for path in tracked
    )
    assert actual_loc == final["test_loc"]
    assert (
        final["redundant_families_removed"] == len(families)
        >= final["targets"]["families_min"]
    )
    assert all(
        row["authority"].strip() and row["retained_selectors"]
        for row in families
    )
