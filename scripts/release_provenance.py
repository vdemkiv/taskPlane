#!/usr/bin/env python3
"""What tree did this release artifact come from, and had it been verified?

D-0010. Both packagers were deterministic — build twice, get identical bytes,
proven in CI — and that is a real property, but it answers the wrong
question. Determinism says "this archive is reproducible FROM SOME TREE". It
does not say WHICH tree, and it says nothing at all about whether that tree
ever passed a test.

So nothing tied a shipped artifact to a green commit. A maintainer could
build from a working copy with uncommitted edits, or from a branch whose CI
was red, and the archive plus its `.sha256` would look exactly as
trustworthy as one built from a verified tag. The digest is a checksum of
the archive, not evidence about its source — and a user installing the
plugin has no way to ask the only question that matters: *is what I am
installing the code that was tested?*

WHAT THIS RECORDS, AND WHAT IT DELIBERATELY DOES NOT

A local build cannot know whether CI passed; asserting that it did would be
worse than saying nothing. What it CAN do is make the question answerable,
and refuse the cases where the answer is already known to be "no":

  commit        the exact source commit. This is the whole point: with it,
                "did CI pass for this artifact?" becomes a lookup against
                that commit. Without it, the question has no subject.
  dirty         files modified since that commit. A dirty build is not the
                commit it claims, so it is REFUSED by default — the archive
                would be a tree nobody can reconstruct or check.
  branch        where it came from, for humans.
  verified_source
                False whenever `--allow-dirty` was used. An artifact built
                over uncommitted edits stays buildable — sometimes you need
                a local test archive — but it is stamped, permanently, and
                the stamp travels in the JSON next to the checksum.

CI asserts that the provenance commit equals the commit under test, so an
artifact built in the release path is bound to the tree that leg verified.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


class ProvenanceError(RuntimeError):
    """A build whose source cannot be identified."""


def _git(root: Path, *args: str, strip: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=str(root),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise ProvenanceError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or 'no output'}")
    # `--porcelain` encodes the status in the first TWO columns, so a
    # worktree-only modification begins with a SPACE (" M path"). Stripping
    # the whole output eats that space on the first line only, which silently
    # shifts one path by one character — the kind of corruption that shows up
    # as a mangled filename in an error message and nowhere else.
    return proc.stdout.strip() if strip else proc.stdout


def source_state(root: Path) -> dict:
    """{'commit', 'branch', 'dirty': [paths]} for the tree being packaged."""
    commit = _git(root, "rev-parse", "HEAD")
    try:
        branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    except ProvenanceError:
        branch = ""
    status = _git(root, "status", "--porcelain", strip=False)
    dirty = sorted(line[2:].strip() for line in status.splitlines()
                   if line.strip())
    return {"commit": commit, "branch": branch, "dirty": dirty}


def write(root: Path, archive: Path, digest: str, *,
          allow_dirty: bool = False) -> Path:
    """Write `<archive>.provenance.json`; refuse a dirty tree by default.

    Raises ProvenanceError when the source cannot be identified (no git) or
    when the tree is dirty and `allow_dirty` was not asked for — refusing is
    the point: an unidentifiable artifact must not reach a release path
    looking like an identifiable one.
    """
    state = source_state(root)
    if state["dirty"] and not allow_dirty:
        listed = ", ".join(state["dirty"][:8])
        more = (f" (+{len(state['dirty']) - 8} more)"
                if len(state["dirty"]) > 8 else "")
        raise ProvenanceError(
            f"refusing to package a DIRTY tree — {len(state['dirty'])} "
            f"uncommitted file(s): {listed}{more}. The archive would claim "
            f"commit {state['commit'][:12]} while containing something else, "
            "so nothing could check it against CI. Commit them, or pass "
            "--allow-dirty for a local test build (which is stamped "
            "verified_source: false and must not be released).")
    record = {
        "archive": archive.name,
        "sha256": digest,
        "commit": state["commit"],
        "branch": state["branch"],
        "dirty": state["dirty"],
        "verified_source": not state["dirty"],
        "note": ("`verified_source` means the archive is exactly this commit. "
                 "It does NOT assert that CI passed — a local build cannot "
                 "know that. Check `commit` against the CI status for that "
                 "SHA; the release leg asserts they match."),
    }
    path = archive.with_suffix(archive.suffix + ".provenance.json")
    path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path
