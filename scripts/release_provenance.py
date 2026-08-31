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

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping


PROVENANCE_SCHEMA = "taskplane.package-provenance/v1"
_FIELDS = {
    "schema", "kind", "archive", "sha256", "commit", "tree", "branch",
    "dirty", "verified_source", "release_inputs", "note", "fingerprint",
}


class ProvenanceError(RuntimeError):
    """A build whose source cannot be identified."""


def _git(root: Path, *args: str, strip: bool = True) -> str:
    # `encoding` is explicit because `text=True` alone decodes with the
    # LOCALE's preferred encoding, and CI runners routinely present
    # ANSI_X3.4-1968 (ascii). A branch name, tag or author line with one
    # non-ASCII byte would then raise UnicodeDecodeError from inside
    # subprocess — a crash with no relation to what was being checked.
    proc = subprocess.run(["git", *args], cwd=str(root),
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
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
    """Exact commit/tree/branch and dirty paths for the packaged checkout."""
    commit = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    try:
        branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    except ProvenanceError:
        branch = ""
    status = _git(root, "status", "--porcelain", strip=False)
    dirty = sorted(line[2:].strip() for line in status.splitlines()
                   if line.strip())
    return {"commit": commit, "tree": tree, "branch": branch, "dirty": dirty}


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        .encode("utf-8")
    ).hexdigest()


def _release_inputs(root: Path) -> dict[str, str] | None:
    required = (
        root / ".github/workflows/ci.yml",
        root / "requirements-dev.lock",
        root / "taskplane/operational-settings.json",
    )
    if not all(path.is_file() for path in required):
        return None
    try:
        from taskplane.release_evidence import release_input_digests
        return release_input_digests(root)
    except (ImportError, ValueError) as exc:
        raise ProvenanceError("canonical release inputs are invalid") from exc


def validate(record: Mapping[str, Any], *, expected_source_sha: str | None = None,
             require_release_inputs: bool = False) -> dict[str, Any]:
    """Validate a closed provenance record without granting CI authority."""
    if not isinstance(record, Mapping) or set(record) != _FIELDS:
        raise ProvenanceError("package provenance fields are not closed")
    value = dict(record)
    if value.get("schema") != PROVENANCE_SCHEMA:
        raise ProvenanceError("package provenance schema is invalid")
    if value.get("kind") not in {"openai", "claude", "local"}:
        raise ProvenanceError("package provenance kind is invalid")
    for field in ("archive", "sha256", "commit", "tree", "branch", "note"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ProvenanceError(f"package provenance {field} is required")
    if len(value["commit"]) != 40 or len(value["tree"]) != 40:
        raise ProvenanceError("package provenance Git identity is invalid")
    dirty = value.get("dirty")
    if not isinstance(dirty, list) or any(
        not isinstance(path, str) or not path for path in dirty
    ):
        raise ProvenanceError("package provenance dirty paths are invalid")
    if value.get("verified_source") is not (not dirty):
        raise ProvenanceError("package provenance clean-source claim is invalid")
    if expected_source_sha is not None and value["commit"] != expected_source_sha:
        raise ProvenanceError("package provenance names another source SHA")
    inputs = value.get("release_inputs")
    if inputs is not None:
        expected = {"workflow_digest", "lock_digest", "settings_digest"}
        if not isinstance(inputs, Mapping) or set(inputs) != expected or any(
            not isinstance(digest, str) or len(digest) != 64
            for digest in inputs.values()
        ):
            raise ProvenanceError("package release-input digests are invalid")
    if require_release_inputs and inputs is None:
        raise ProvenanceError("package release-input provenance is missing")
    projection = {key: value[key] for key in _FIELDS - {"fingerprint"}}
    if value.get("fingerprint") != _fingerprint(projection):
        raise ProvenanceError("package provenance fingerprint mismatch")
    return json.loads(json.dumps(value, sort_keys=True))


def release_gate_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project one package into the protected-main release gate."""
    value = validate(record, require_release_inputs=True)
    if value["kind"] not in {"openai", "claude"}:
        raise ProvenanceError("local package provenance cannot authorize release")
    if not value["verified_source"]:
        raise ProvenanceError("dirty package provenance cannot authorize release")
    if len(value["sha256"]) != 64:
        raise ProvenanceError("release archive digest must be SHA-256")
    return {
        "kind": value["kind"],
        "source_sha": value["commit"],
        "archive_digest": value["sha256"],
        "provenance_digest": value["fingerprint"],
        "verified_source": True,
        "dirty": [],
    }


def write(root: Path, archive: Path, digest: str, *,
          allow_dirty: bool = False, kind: str = "local") -> Path:
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
        "schema": PROVENANCE_SCHEMA,
        "kind": kind,
        "archive": archive.name,
        "sha256": digest,
        "commit": state["commit"],
        "tree": state["tree"],
        "branch": state["branch"],
        "dirty": state["dirty"],
        "verified_source": not state["dirty"],
        "release_inputs": _release_inputs(root),
        "note": ("`verified_source` means the archive is exactly this commit. "
                 "It does NOT assert that CI passed — a local build cannot "
                 "know that. Check `commit` against the CI status for that "
                 "SHA; the release leg asserts they match."),
    }
    record["fingerprint"] = _fingerprint(record)
    validate(record)
    path = archive.with_suffix(archive.suffix + ".provenance.json")
    path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path
