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


def _nested_function(method: ast.FunctionDef, name: str) -> ast.FunctionDef:
    for node in method.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing nested function {name}")


def _call_path(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if not isinstance(function, ast.Attribute):
        return ""
    parts = [function.attr]
    value = function.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def _calls(node: ast.AST, path: str) -> list[ast.Call]:
    return [candidate for candidate in ast.walk(node)
            if isinstance(candidate, ast.Call)
            and _call_path(candidate) == path]


def _recorded_events(node: ast.AST) -> dict[str, ast.Call]:
    records = {}
    for call in _calls(node, "record_event"):
        if call.args and isinstance(call.args[0], ast.Constant) and \
                isinstance(call.args[0].value, str):
            records[call.args[0].value] = call
    return records


def _contains(root: ast.AST, child: ast.AST) -> bool:
    return any(node is child for node in ast.walk(root))


def test_m16_publication_concurrency_uses_events_not_sleep() -> None:
    """The race must synchronize on reservation attempts, never wall time."""

    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    method = _target_method()
    call_paths = {_call_path(call) for call in ast.walk(method)
                  if isinstance(call, ast.Call)}

    assert not any(name == "sleep" or name.endswith(".sleep")
                   for name in call_paths)
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        and any(alias.name == "time" for alias in node.names)
        for node in tree.body
    )

    ordered_lock = _nested_function(method, "ordered_file_lock")
    ordered_records = _recorded_events(ordered_lock)
    attempted = ordered_records["loser-lock-attempted"]
    acquired = ordered_records["loser-lock-acquired"]
    released = ordered_records["loser-lock-released"]

    real_lock_blocks = [
        node for node in ast.walk(ordered_lock)
        if isinstance(node, ast.With)
        and any(isinstance(item.context_expr, ast.Call)
                and _call_path(item.context_expr) == "real_file_lock"
                for item in node.items)
    ]
    actual_lock = next(
        node for node in real_lock_blocks if _contains(node, acquired))
    assert attempted.lineno < actual_lock.lineno < acquired.lineno
    assert not _contains(actual_lock, attempted)
    assert _contains(actual_lock, acquired)
    assert not _contains(actual_lock, released)
    assert actual_lock.end_lineno is not None
    assert actual_lock.end_lineno < released.lineno
    assert _contains(
        actual_lock, ordered_records["winner-transaction-lock-acquired"])

    final_entry = next(
        node for node in ast.walk(ordered_lock)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "entry_number"
        and any(isinstance(value, ast.Constant) and value.value == 3
                for value in node.test.comparators)
    )
    reservation_wait = _calls(final_entry, "loser_reservation_rejected.wait")
    assert len(reservation_wait) == 1
    assert reservation_wait[0].lineno < actual_lock.lineno

    acquire_reservation = _nested_function(
        method, "observed_acquire_reservation")
    acquire_try = next(node for node in acquire_reservation.body
                       if isinstance(node, ast.Try))
    assert len(_calls(acquire_try, "real_acquire_reservation")) == 1
    revision_handler = next(
        handler for handler in acquire_try.handlers
        if isinstance(handler.type, ast.Attribute)
        and handler.type.attr == "RevisionError")
    rejected = _recorded_events(revision_handler)[
        "loser-reservation-rejected"]
    assert _contains(revision_handler, rejected)
    assert any(isinstance(node, ast.Raise)
               for node in ast.walk(revision_handler))

    release_reservation = _nested_function(
        method, "observed_release_reservation")
    real_release = _calls(
        release_reservation, "real_release_reservation")
    winner_released = _recorded_events(release_reservation)[
        "winner-reservation-released"]
    assert len(real_release) == 1
    assert real_release[0].lineno < winner_released.lineno

    publish = _nested_function(method, "publish")
    publish_entered = _recorded_events(publish)["winner-publish-entered"]
    held_assertion = _calls(publish, "self.assertTrue")
    assert any(_calls(call, "winner_transaction_lock_acquired.is_set")
               for call in held_assertion)
    publish_wait = _calls(publish, "winner_publish_release.wait")
    assert len(publish_wait) == 1
    assert publish_entered.lineno < publish_wait[0].lineno

    patched_scope = next(node for node in method.body
                         if isinstance(node, ast.With)
                         and len(node.items) == 4)
    attempt_wait = _calls(patched_scope, "loser_lock_attempted.wait")
    acquired_state = _calls(patched_scope, "loser_lock_acquired.is_set")
    release_record = _recorded_events(patched_scope)[
        "winner-publish-release"]
    false_assertions = _calls(patched_scope, "self.assertFalse")
    assert len(attempt_wait) == 1 and len(acquired_state) >= 1
    assert any(_contains(call, acquired_state[0]) for call in false_assertions)
    assert attempt_wait[0].lineno < acquired_state[0].lineno \
        < release_record.lineno

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
    assert {
        "file_lock",
        "_acquire_collection_reservation",
        "_release_collection_reservation",
    } <= patched_attributes


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
