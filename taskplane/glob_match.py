"""Dependency-neutral repository path glob matching.

Both the high-level lens router and the lower graph-signal router depend on
this module.  It deliberately imports neither routing layer, which prevents
the old duplication from becoming an import cycle.
"""
from __future__ import annotations

import fnmatch
import os


def path_matches(path: str, pattern: str) -> bool:
    """Match one repository path with Taskplane's historical ``**/`` rules."""
    if fnmatch.fnmatch(path, pattern):
        return True
    if pattern.startswith("**/"):
        tail = pattern[3:]
        if (fnmatch.fnmatch(path, tail)
                or fnmatch.fnmatch(os.path.basename(path), tail)):
            return True
        parts = path.split("/")
        return any(fnmatch.fnmatch("/".join(parts[index:]), tail)
                   for index in range(len(parts)))
    return False


def first_match(files, patterns):
    """Return the first deterministic ``(file, pattern)`` match, if any."""
    for pattern in patterns or ():
        for path in files or ():
            if path_matches(path, pattern):
                return path, pattern
    return None


def matches_by_pattern(files, patterns) -> list:
    """Return the first file matched by each pattern, preserving order."""
    hits = []
    for pattern in patterns or ():
        for path in files or ():
            if path_matches(path, pattern):
                hits.append((path, pattern))
                break
    return hits
