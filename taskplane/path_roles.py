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


# --------------------------------------------------------- repo-declared config
# `components.yaml` is the one file a repo uses to tell taskplane about its own
# shape. It lived only in decompose (floors), but an EXCLUSION list is path
# classification, which is this module's job — and putting the shared parser
# here keeps `depgraph` from importing `decompose`, a pair that is already a
# mutual-import cycle.
#
# Supported shapes, deliberately still a tiny subset (stdlib only, no YAML
# dependency). Exactly three LINE SHAPES:
#
#     floors:                     a top-level section header
#       cluster_min_files: 2      an indented int key/value
#     exclude:
#       - vendor/generated/       an indented list item (NEW)
#
# `#` comments and blank lines are stripped first. Any OTHER line shape raises
# ValueError, and every caller fails OPEN — a malformed file must never
# silently narrow the graph, because a narrowed graph is a narrowed blast
# radius and that fails toward LESS review.
_CFG_SECTION = re.compile(r"^([A-Za-z_][\w-]*):\s*$")
_CFG_INT = re.compile(r"^\s+([A-Za-z_][\w-]*):\s*(-?\d+)\s*$")
_CFG_ITEM = re.compile(r"^\s+-\s*(\S.*?)\s*$")


def parse_components_yaml(text: str) -> dict:
    """{'floors': {name: int}, 'exclude': [prefix, ...]} from the flat subset.

    Raises ValueError on any unsupported line shape; callers fail open.
    """
    out: dict = {"floors": {}, "exclude": []}
    section = None
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        top = _CFG_SECTION.match(line)
        if top:
            section = top.group(1)
            continue
        # A shape is only valid in the section that uses it. A list item
        # under `floors:` is NOT "an item we ignore" — it is a malformed
        # floors file, and it must still raise so the caller fails open
        # WITH a report. Accepting it silently would drop the floors the
        # author intended and say nothing, which is worse than the error.
        kv = _CFG_INT.match(line)
        if kv and section == "floors":
            out["floors"][kv.group(1)] = int(kv.group(2))
            continue
        item = _CFG_ITEM.match(line)
        if item and section == "exclude":
            out["exclude"].append(item.group(1).strip("'\"").replace(
                "\\", "/").lstrip("./"))
            continue
        # A recognised shape in an UNKNOWN section stays ignorable, which is
        # the documented forward-compatibility behaviour.
        if (kv or item) and section not in ("floors", "exclude"):
            continue
        raise ValueError(f"unsupported components.yaml line: {raw!r}")
    return out


def is_excluded(relpath: str, prefixes) -> bool:
    """True when `relpath` sits under one of the declared exclude prefixes.

    Matched on SEGMENT boundaries, never as a raw string prefix: `corpus`
    must not also exclude `corpus-notes.md`. This is the same prefix-vs-
    segment distinction that made `.taskplane` wrongly match
    `.taskplane-kb/` elsewhere in this codebase.
    """
    rel = str(relpath or "").replace("\\", "/").lstrip("./")
    for p in prefixes or ():
        p = str(p).replace("\\", "/").strip("/")
        if not p:
            continue
        if rel == p or rel.startswith(p + "/"):
            return True
    return False
