from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import re

import pytest

from taskplane.authority import DECISION_SCHEMA
from taskplane.settings import (
    DEFAULT_SETTINGS_PATH, SettingsError, load_settings, settings_digest,
)


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

    def scope_nodes(scope: ast.AST) -> list[ast.AST]:
        result: list[ast.AST] = []

        def descend(node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (
                        ast.FunctionDef, ast.AsyncFunctionDef,
                        ast.ClassDef, ast.Lambda)):
                    continue
                result.append(child)
                descend(child)

        descend(scope)
        return result

    scopes: list[ast.AST] = [tree]
    scopes.extend(node for node in ast.walk(tree) if isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    for scope in scopes:
        nodes = scope_nodes(scope)
        aliases = {"environ"}
        assignments = [node for node in nodes
                       if isinstance(node, (ast.Assign, ast.AnnAssign))]
        changed = True
        while changed:
            changed = False
            for node in assignments:
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
                if node.value is None:
                    continue
                candidates = ([node.value.body, node.value.orelse]
                              if isinstance(node.value, ast.IfExp)
                              else list(node.value.values)
                              if isinstance(node.value, ast.BoolOp)
                              else [node.value])
                source_is_environment = any(
                    (isinstance(candidate, ast.Attribute) and
                     candidate.attr == "environ") or
                    (isinstance(candidate, ast.Name) and
                     candidate.id in aliases)
                    for candidate in candidates)
                if not source_is_environment:
                    continue
                for target in targets:
                    if isinstance(target, ast.Name) and \
                            target.id not in aliases:
                        aliases.add(target.id)
                        changed = True
        for node in nodes:
            if isinstance(node, ast.Call) and \
                    isinstance(node.func, ast.Attribute) and \
                    node.func.attr == "get" and node.args:
                target = node.func.value
                if ((isinstance(target, ast.Attribute) and
                     target.attr == "environ") or
                        (isinstance(target, ast.Name) and
                         target.id in aliases)) and \
                        isinstance(node.args[0], ast.Constant) and \
                        isinstance(node.args[0].value, str):
                    names.add(node.args[0].value)
            if isinstance(node, ast.Subscript) and (
                    (isinstance(node.value, ast.Attribute) and
                     node.value.attr == "environ") or
                    (isinstance(node.value, ast.Name) and
                     node.value.id in aliases)):
                key = node.slice
                if isinstance(key, ast.Constant) and \
                        isinstance(key.value, str):
                    names.add(key.value)
    # Adapter-owned dynamic tables are reads even though the lookup key is a
    # loop variable. Treat every environment-shaped table member as a read.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        target_names = {target.id for target in targets
                        if isinstance(target, ast.Name)}
        if not any("ENV" in name for name in target_names):
            continue
        for child in ast.walk(node.value):
            if isinstance(child, ast.Constant) and isinstance(child.value, str) \
                    and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", child.value):
                names.add(child.value)
    return names


def _javascript_operational_defaults(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    pattern = re.compile(
        r"\b(?:maxAttempts|timeout(?:Seconds)?|shards|concurrency)\s*:"
        r"[^\n]*(?:\|\||\?\?)\s*(?:\d+|true|false|['\"])")
    return [match.group(0) for match in pattern.finditer(text)]


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
        if path.name == "dashboard-missing-required-event.json":
            return value["dashboard"]["refresh"]["lifecycle_events"] != \
                json.loads(DEFAULT_SETTINGS_PATH.read_text(encoding="utf-8"))[
                    "dashboard"]["refresh"]["lifecycle_events"]
    if path.name == "workflow-js-default.js.txt":
        return bool(re.search(r"maxAttempts\s*:[^\n]+\|\|\s*\d+", text))
    if path.name == "worker-terminal-bypass.py.txt":
        return "event_type=\"worker_terminal\"" in text and \
            "settings.dashboard.refresh" not in text
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
    expected = json.loads(json.dumps(canonical))
    for stage in expected["stages"].values():
        if stage["model"] == "inherit":
            stage["model"] = None
    assert effective.to_dict() == expected
    assert effective.receipt["precedence"] == ["defaults", "file"]
    assert effective.receipt["environment"] is None
    assert effective.receipt["overlay"] is None
    assert effective.runtime.to_dict() == canonical["runtime"]
    canonical_bytes = DEFAULT_SETTINGS_PATH.read_bytes()
    overlaid = load_settings(
        DEFAULT_SETTINGS_PATH,
        overlay={"runtime": {"inline_max_bytes": 12000}})
    assert overlaid.runtime.inline_max_bytes == 12000
    assert overlaid.runtime.audit_every == canonical["runtime"]["audit_every"]
    assert overlaid.receipt["precedence"] == ["defaults", "file", "overlay"]
    assert DEFAULT_SETTINGS_PATH.read_bytes() == canonical_bytes
    weakening = {
        "TASKPLANE_OBLIGATIONS": "off",
        "TASKPLANE_RUNNABILITY": "off",
    }
    with pytest.raises(SettingsError, match="exact authority"):
        load_settings(environment=weakening)
    authority_receipt = {
        "schema": DECISION_SCHEMA,
        "authorized": True,
        "authority_requested": "gate_weakening",
        "actor": "human:test",
        "thread": "settings-conformance",
        "revision": "1",
    }
    weakened = load_settings(
        environment=weakening, authority=authority_receipt)
    assert weakened.runtime.obligations == "advisory"
    assert weakened.runtime.runnability == "disabled"
    assert weakened.receipt["environment"]["authority_fingerprint"]
    governed_runtime_aliases = {
        "TASKPLANE_AUDIT_EVERY": "7",
        "TASKPLANE_ORPHAN_TTL_SECONDS": "7200",
    }
    with pytest.raises(SettingsError, match="exact authority"):
        load_settings(environment=governed_runtime_aliases)
    governed_runtime = load_settings(
        environment=governed_runtime_aliases, authority=authority_receipt)
    assert governed_runtime.runtime.audit_every == 7
    assert governed_runtime.runtime.orphan_ttl_seconds == 7200
    assert governed_runtime.receipt["environment"][
        "authority_fingerprint"]
    with pytest.raises(SettingsError, match="exact authority"):
        load_settings(environment={"TASKPLANE_ORPHAN_TTL_SECONDS": "0"})
    with pytest.raises(SettingsError, match="integer >= 1"):
        load_settings(
            environment={"TASKPLANE_ORPHAN_TTL_SECONDS": "0"},
            authority=authority_receipt)
    with pytest.raises(SettingsError, match="exact authority"):
        load_settings(environment={"TASKPLANE_ORPHAN_TTL": "7200"})
    assert effective.digest == settings_digest(effective)
    sources = [path.relative_to(ROOT).as_posix()
               for path in ROOT.rglob("operational-settings.json")]
    assert sources == [authority["canonical_source"]]

    package_authorities = {
        "taskplane/operational-settings.json",
        "taskplane/settings_inventory.json",
    }
    for script_name in ("package_openai.py", "package_claude.py"):
        script = ROOT / "scripts" / script_name
        spec = importlib.util.spec_from_file_location(
            f"_settings_inventory_{script.stem}", script)
        assert spec is not None and spec.loader is not None
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        assert set(packager.CANONICAL_AUTHORITY_FILES) == package_authorities
        files = (packager.package_files(packager.load_manifest())
                 if script_name == "package_openai.py"
                 else packager.package_files())
        members = {
            path.relative_to(packager.ROOT).as_posix() for path in files
        }
        assert package_authorities <= members, script_name

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
    javascript_defaults = {
        path.relative_to(ROOT).as_posix():
            _javascript_operational_defaults(path)
        for path in sorted((ROOT / "workflows").glob("*.js"))
        if _javascript_operational_defaults(path)
    }
    assert not javascript_defaults

    negative = inventory["negative_fixtures"]
    assert {path.relative_to(ROOT).as_posix() for path in FIXTURES.iterdir()} == \
        set(negative)
    assert all(_fixture_has_inventory_violation(ROOT / path, inventory)
               for path in negative)


def test_governed_runtime_consumers_require_exact_authority(
        tmp_path, monkeypatch):
    from taskplane import audit
    from taskplane import taskplane_lite as lite

    authority = {
        "schema": DECISION_SCHEMA,
        "authorized": True,
        "authority_requested": "gate_weakening",
        "actor": "human:test",
        "thread": "settings-consumers",
        "revision": "1",
    }
    monkeypatch.setenv("TASKPLANE_AUDIT_EVERY", "7")
    with pytest.raises(SettingsError, match="exact authority"):
        audit.audit_every()
    assert audit.audit_every(authority=authority) == 7
    monkeypatch.delenv("TASKPLANE_AUDIT_EVERY")

    monkeypatch.setenv("TASKPLANE_ORPHAN_TTL_SECONDS", "7200")
    contract = {
        "task_id": "t1",
        "activated_at": 1000,
        "budget": {"max_actions": 10},
    }
    with pytest.raises(SettingsError, match="exact authority"):
        lite.orphan_status(str(tmp_path), contract, now=5000)
    assert lite.orphan_status(
        str(tmp_path), contract, now=5000,
        settings_authority=authority)[0] is False
    monkeypatch.delenv("TASKPLANE_ORPHAN_TTL_SECONDS")

    invalid_contract = dict(contract, orphan_ttl_seconds=0)
    orphaned, reason = lite.orphan_status(
        str(tmp_path), invalid_contract, now=5000)
    assert orphaned is False
    assert "invalid non-positive" in reason
