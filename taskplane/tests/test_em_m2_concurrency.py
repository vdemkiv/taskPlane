"""Focused M-16 proof for deterministic review-publication concurrency."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


SOURCE = Path(__file__).with_name("test_review_routing.py")
TARGET_CLASS = "TestSelectiveReviewKernel"
TARGET_METHOD = "test_concurrent_collect_loser_never_publishes_authoritative_views"


def _target_method() -> ast.FunctionDef:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == TARGET_CLASS:
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and \
                        member.name == TARGET_METHOD:
                    return member
    raise AssertionError(f"missing {TARGET_CLASS}.{TARGET_METHOD}")


def _calls(method: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name):
            names.add(function.id)
        elif isinstance(function, ast.Attribute):
            parts = [function.attr]
            value = function.value
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                parts.append(value.id)
            names.add(".".join(reversed(parts)))
    return names


def test_m16_publication_concurrency_uses_events_not_sleep() -> None:
    """The race must synchronize on reservation attempts, never wall time."""

    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    method = _target_method()
    calls = _calls(method)

    assert not any(name == "sleep" or name.endswith(".sleep") for name in calls)
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        and any(alias.name == "time" for alias in node.names)
        for node in tree.body
    )

    required_calls = {
        "threading.Event",
        "loser_lock_waiting.set",
        "loser_lock_waiting.wait",
        "loser_reservation_resolved.set",
        "loser_reservation_resolved.wait",
        "real_file_lock",
        "release.set",
    }
    assert required_calls <= calls

    patched_attributes = {
        node.args[1].value
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "object"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "patch"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    }
    assert "file_lock" in patched_attributes


def test_m16_event_driven_publication_race_is_adversarial() -> None:
    """Run the real loser/winner proof and surface its unittest diagnostics."""

    from taskplane.tests.test_review_routing import (  # noqa: PLC0415
        TestSelectiveReviewKernel as RoutingCase,
    )

    case = RoutingCase(methodName=TARGET_METHOD)
    result = unittest.TestResult()
    case.run(result)

    diagnostics = [
        f"{test.id()}: {detail}"
        for test, detail in [*result.failures, *result.errors]
    ]
    assert result.testsRun == 1
    assert not result.skipped
    assert result.wasSuccessful(), "\n".join(diagnostics)
