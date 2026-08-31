from __future__ import annotations

import ast
import json
from pathlib import Path

from taskplane.settings import DEFAULT_SETTINGS_PATH, load_settings, settings_digest


ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = ROOT / "taskplane" / "settings_inventory.json"
FIXTURES = Path(__file__).parent / "fixtures" / "settings-inventory"


def _inventory() -> dict:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _semantic_leaf_paths(value: object, prefix: tuple[str, ...] = ()) -> set[str]:
    if isinstance(value, dict):
        result: set[str] = set()
        for key, item in value.items():
            if not prefix and key == "schema":
                continue
            result.update(_semantic_leaf_paths(item, prefix + (str(key),)))
        return result
    # A list is one governed setting, never one default owner per element.
    path = list(prefix)
    if len(path) >= 2 and path[0] == "stages":
        path[1] = "*"
    if len(path) >= 3 and path[0] == "lenses" and path[1] in {
            "routing", "counts"}:
        path[2] = "*"
    return {".".join(path)}


def _literal_environment_reads(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and node.args:
            target = node.func.value
            if isinstance(target, ast.Attribute) and target.attr == "environ" \
                    and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                names.add(node.args[0].value)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "environ":
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                names.add(key.value)
    return names


def _assigned_names(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return set()
    result: set[str] = set()
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        result.update(target.id for target in targets
                      if isinstance(target, ast.Name))
    return result


def _production_python() -> list[Path]:
    paths = list((ROOT / "taskplane").glob("*.py"))
    paths.extend((ROOT / "hooks").glob("*.py"))
    paths.extend((ROOT / "scripts").glob("*.py"))
    return sorted(set(paths))


def _fixture_has_inventory_violation(path: Path, inventory: dict) -> bool:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        value = json.loads(text)
        if path.name == "duplicate-authority.json":
            return bool(set(value) & {
                "stages", "lenses", "build", "tests", "limits", "workflow",
                "cleanup", "dashboard", "overrides", "observability",
            })
        if path.name == "missing-digest-flow.json":
            return "settings" in value and value["settings"].get(
                "binding") != inventory["authority"]["digest_field"]
        if path.name == "stale-dashboard-refresh.json":
            recovery = value.get("sessionRecovery") or {}
            return "eventType" in recovery or "replay" in recovery
    return any(name in text for name in (
        inventory["prohibited_direct_environment"] +
        inventory["prohibited_default_symbols"]))


def test_every_operational_setting_has_one_canonical_owner():
    inventory = _inventory()
    assert inventory["schema"] == "taskplane.operational-settings-inventory/v1"
    authority = inventory["authority"]
    assert authority == {
        "canonical_source": "taskplane/operational-settings.json",
        "typed_loader": "taskplane/settings.py:load_settings",
        "legacy_adapter": "taskplane/settings_legacy.py:migrate_legacy_settings",
        "digest_field": "settings_digest",
        "rule": authority["rule"],
    }

    canonical = json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))
    assert _semantic_leaf_paths(canonical) == set(inventory["canonical_keys"])
    effective = load_settings(DEFAULT_SETTINGS_PATH)
    assert _semantic_leaf_paths(effective.to_dict()) == set(
        inventory["canonical_keys"])
    assert effective.digest == settings_digest(effective)
    sources = [path.relative_to(ROOT).as_posix()
               for path in ROOT.rglob("operational-settings.json")]
    assert sources == [authority["canonical_source"]]

    classified: dict[str, list[str]] = {}
    for row in inventory["environment_dispositions"]:
        assert row["disposition"] in {
            "runtime-observation", "derived-value", "immutable-protocol",
            "justified-non-setting",
        }
        assert row["justification"].strip()
        for name in row["names"]:
            classified.setdefault(name, []).append(row["id"])
    assert all(len(owners) == 1 for owners in classified.values())
    assert not (set(classified) & set(
        inventory["prohibited_direct_environment"]))

    reads: dict[str, list[str]] = {}
    assigned: dict[str, list[str]] = {}
    for path in _production_python():
        if path == ROOT / "taskplane" / "settings.py":
            continue
        relative = path.relative_to(ROOT).as_posix()
        for name in _literal_environment_reads(path):
            reads.setdefault(name, []).append(relative)
        for name in _assigned_names(path):
            assigned.setdefault(name, []).append(relative)
    prohibited_reads = set(reads) & set(
        inventory["prohibited_direct_environment"])
    assert not prohibited_reads, {
        name: reads[name] for name in sorted(prohibited_reads)}
    unknown_reads = set(reads) - set(classified)
    assert not unknown_reads, {name: reads[name] for name in sorted(unknown_reads)}
    duplicate_defaults = set(assigned) & set(
        inventory["prohibited_default_symbols"])
    assert not duplicate_defaults, {
        name: assigned[name] for name in sorted(duplicate_defaults)}

    negative = inventory["negative_fixtures"]
    assert {path.relative_to(ROOT).as_posix() for path in FIXTURES.iterdir()} == \
        set(negative)
    assert all(_fixture_has_inventory_violation(ROOT / path, inventory)
               for path in negative)
