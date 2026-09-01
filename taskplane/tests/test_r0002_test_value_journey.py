"""Current test-value adjudication as an executable repository contract."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
PORTFOLIO = ROOT / "taskplane" / "test_portfolio.json"


def _definitions(path: Path) -> set[tuple[str, ...]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[tuple[str, ...]] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.add((node.name,))
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.add((node.name, child.name))
    return found


def _selector_exists(selector: str) -> bool:
    path_text, *node_parts = selector.split("::")
    path = ROOT / path_text
    return path.is_file() and tuple(node_parts) in _definitions(path)


def _module_strings(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_current_inventory_is_completely_adjudicated_and_removals_collect() -> None:
    portfolio = json.loads(PORTFOLIO.read_text(encoding="utf-8"))
    inventory = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "taskplane" / "tests").glob("test_*.py")
    }
    records = portfolio["files"]

    assert {record["path"] for record in records} == inventory
    assert len(records) == len(inventory)
    assert {record["classification"] for record in records} <= {
        "retain", "rewrite"
    }
    assert all(record["reason"] for record in records)

    removed = portfolio["removals"]
    assert all(not (ROOT / row["path"]).exists() for row in removed)
    replacements = [
        selector
        for row in removed
        for selector in row["replacement_selectors"]
    ]
    assert replacements and all(_selector_exists(item) for item in replacements)

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *replacements],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "no tests collected" not in completed.stdout


def test_protected_contracts_and_fixture_consumers_remain_live() -> None:
    portfolio = json.loads(PORTFOLIO.read_text(encoding="utf-8"))
    protected = [
        selector
        for selectors in portfolio["protected_contracts"].values()
        for selector in selectors
    ]
    assert protected and all(_selector_exists(item) for item in protected)

    for fixture in portfolio["fixtures"]["retained"]:
        fixture_path = ROOT / fixture["path"]
        consumers = fixture["consumer_selectors"]
        assert fixture_path.is_file()
        assert consumers and all(_selector_exists(item) for item in consumers)
        fixture_parts = fixture_path.relative_to(ROOT).parts
        referenced = set()
        for selector in consumers:
            module = ROOT / selector.split("::", 1)[0]
            referenced.update(_module_strings(module))
        assert any(
            fixture_path.name in value
            or any(part in value for part in fixture_parts[-3:-1])
            for value in referenced
        ), f"retained fixture has no structural consumer: {fixture['path']}"


def test_accepted_evidence_excludes_low_value_mechanisms() -> None:
    portfolio = json.loads(PORTFOLIO.read_text(encoding="utf-8"))
    forbidden = set(portfolio["policy"]["forbidden_evidence_mechanisms"])
    accepted = portfolio["accepted_evidence"]

    assert accepted and all(_selector_exists(row["selector"]) for row in accepted)
    assert not forbidden.intersection(row["mechanism"] for row in accepted)
    assert all(
        row["mechanism"] in {"public-journey", "semantic-refusal", "severed-edge"}
        for row in accepted
    )
