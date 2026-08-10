"""Deterministic path-role classification shared by routing engines.

Keep this module dependency-free: both ``lens`` and ``lens_signals`` import
it, and neither router should carry a subtly different definition of a test
file. The previous implementation lived in ``lens`` and matched SUBSTRINGS,
so ``contest.py``, ``latest.py``, ``specification.py`` and ``protest/`` all
counted as tests and silently suppressed the QA untested trigger on real
code changes.
"""

from __future__ import annotations

import os
import re


_TEST_DIRECTORIES = frozenset({
    "test", "tests", "spec", "specs", "__tests__", "testing",
    "e2e", "cypress", "playwright", "integration-tests",
})
_TEST_SUPPORT_FILES = frozenset({"conftest.py"})
_TEST_PREFIXES = ("test_", "test-", "spec_", "spec-")
_TEST_SUFFIXES = ("_test", "-test", "_tests", "-tests", "_spec", "-spec")
_TEST_INFIXES = (".test.", ".spec.")
# CamelCase suffix conventions: FooTest.java, OrderTests.cs, PaymentSpec.scala.
# Anchored on a preceding lowercase letter or digit so that "latest" and
# "contest" — which contain "test" only as a substring — never match.
_CAMEL_TEST = re.compile(r"[a-z0-9](?:Test|Tests|Spec|Specs)$")


def is_test_path(path: str) -> bool:
    """Return whether *path* follows a conventional test path shape.

    Matching is segment- and filename-aware. In particular, product files
    such as ``contest.py``, ``latest.py``, and ``specification.py`` are not
    tests merely because their names contain the strings ``test`` or
    ``spec``.
    """
    normalized = str(path or "").replace("\\", "/")
    if os.sep != "/":
        normalized = normalized.replace(os.sep, "/")
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return False
    if any(part.lower() in _TEST_DIRECTORIES for part in parts[:-1]):
        return True

    raw_name = parts[-1]
    name = raw_name.lower()
    if name in _TEST_SUPPORT_FILES or any(token in name
                                          for token in _TEST_INFIXES):
        return True
    raw_stem = raw_name.rsplit(".", 1)[0] if "." in raw_name else raw_name
    if _CAMEL_TEST.search(raw_stem):
        return True
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return (stem in {"test", "tests", "spec", "specs"}
            or stem.startswith(_TEST_PREFIXES)
            or stem.endswith(_TEST_SUFFIXES))


def change_adds_no_test(files, code_extensions) -> bool:
    """True when a change touches code and the QA trigger should fire.

    The documented ``TASKPLANE_QA_BASELINE`` escape hatch deliberately
    promotes every code change, including one that carries tests.
    """
    paths = [str(path) for path in files or []]
    has_code = any(any(path.lower().endswith(str(ext).lower())
                       for ext in code_extensions or ())
                   for path in paths)
    if os.environ.get("TASKPLANE_QA_BASELINE", "").strip().lower() in {
            "1", "true", "yes", "on"}:
        return has_code
    return has_code and not any(is_test_path(path) for path in paths)
