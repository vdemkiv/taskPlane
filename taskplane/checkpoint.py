"""BUILD-C acceptance-checkpoint specifications and Plan scope closure.

This module owns the input boundary.  Runtime-event validation and receipt
minting are deliberately layered on top by the next BUILD-C task; callers
cannot turn a checkpoint specification into evidence merely by constructing a
mapping that looks like a receipt.
"""

from __future__ import annotations

import fnmatch
import os
import re
import stat
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence


CHECKPOINT_SCHEMA = "taskplane.build-c-checkpoint/v1"
CHECKPOINT_RECEIPT_SCHEMA = "taskplane.build-c-checkpoint-receipt/v1"

CLOSED_GAP_CATEGORIES = (
    "ac-bound-checkpoints",
    "engine-minted-checkpoint-receipts",
    "submit-to-checkpoint-wiring",
    "define-stage-projection",
    "direct-no-state-graph-disjoint-assignment",
    "checkpoint-to-integration-authorization",
)

ORDERED_CHECKPOINT_PHASES = (
    "compile_import",
    "focused_proof",
    "forbidden_state_counts",
    "ratchet_delta",
    "engineering_judgment",
)

_SPEC_FIELDS = frozenset({
    "schema", "checkpoint_id", "phase", "ac_ids",
    "predecessor_checkpoint_ids", "worktree_revision", "declared_scope",
    "focused_proof", "ratchet_baseline",
})
_PROOF_FIELDS = frozenset({"path", "argv"})
_R0010_TASK = re.compile(r"(?:^|-)r0010(?:-|$)", re.IGNORECASE)


class CheckpointSpecError(ValueError):
    """A checkpoint specification cannot safely start its focused proof."""


def _strings(value, field: str, *, nonempty: bool = True) -> list[str]:
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes))
            or any(not isinstance(item, str) or not item.strip()
                   for item in value)):
        raise CheckpointSpecError(f"{field} must be a list of non-empty strings")
    result = [item.strip() for item in value]
    if nonempty and not result:
        raise CheckpointSpecError(f"{field} must not be empty")
    if len(result) != len(set(result)):
        raise CheckpointSpecError(f"{field} contains duplicate values")
    return result


def _repository_path(worktree: str, value: object, field: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value.strip():
        raise CheckpointSpecError(f"{field} must be a repository-relative path")
    relpath = value.strip().replace("\\", "/")
    if os.path.isabs(relpath) or relpath == ".." or relpath.startswith("../"):
        raise CheckpointSpecError(f"{field} escapes the worktree: {relpath}")
    root = os.path.realpath(worktree)
    candidate = os.path.realpath(os.path.join(root, relpath))
    try:
        inside = os.path.commonpath((root, candidate)) == root
    except ValueError:
        inside = False
    if not inside:
        raise CheckpointSpecError(f"{field} escapes the worktree: {relpath}")
    return relpath, candidate


def _git(worktree: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=worktree, capture_output=True,
                          text=True, check=False)


def _scope_contains(path: str, scope: Sequence[str]) -> bool:
    return any(path == pattern or fnmatch.fnmatchcase(path, pattern)
               for pattern in scope)


def validate_checkpoint_spec(worktree: str, spec: Mapping) -> dict:
    """Validate and normalize an AC checkpoint before any command starts.

    The focused proof must be a tracked, non-symlink regular file in the exact
    worktree revision named by the specification.  The returned phase order is
    engine-owned and therefore cannot be supplied or reordered by a caller.
    """
    if not isinstance(spec, Mapping):
        raise CheckpointSpecError("checkpoint specification must be a mapping")
    unknown = sorted(set(spec) - _SPEC_FIELDS)
    if unknown:
        raise CheckpointSpecError("unknown checkpoint fields: "
                                  + ", ".join(unknown))
    if spec.get("schema") != CHECKPOINT_SCHEMA:
        raise CheckpointSpecError(f"schema must be {CHECKPOINT_SCHEMA}")
    for field in ("checkpoint_id", "phase", "worktree_revision"):
        if not isinstance(spec.get(field), str) or not spec[field].strip():
            raise CheckpointSpecError(f"{field} must be a non-empty string")

    ac_ids = _strings(spec.get("ac_ids"), "ac_ids")
    predecessors = _strings(spec.get("predecessor_checkpoint_ids", []),
                            "predecessor_checkpoint_ids", nonempty=False)
    if spec["checkpoint_id"] in predecessors:
        raise CheckpointSpecError("checkpoint cannot name itself as predecessor")
    scope = _strings(spec.get("declared_scope"), "declared_scope")
    for item in scope:
        _repository_path(worktree, item, "declared_scope")

    proof = spec.get("focused_proof")
    if not isinstance(proof, Mapping):
        raise CheckpointSpecError("focused_proof must be a mapping")
    proof_unknown = sorted(set(proof) - _PROOF_FIELDS)
    if proof_unknown:
        raise CheckpointSpecError("unknown focused_proof fields: "
                                  + ", ".join(proof_unknown))
    proof_path, absolute_proof = _repository_path(
        worktree, proof.get("path"), "focused_proof.path")
    argv = _strings(proof.get("argv"), "focused_proof.argv")
    if not _scope_contains(proof_path, scope):
        raise CheckpointSpecError(
            f"focused proof {proof_path} is outside declared_scope")
    try:
        mode = os.lstat(absolute_proof).st_mode
    except FileNotFoundError as exc:
        raise CheckpointSpecError(
            f"focused proof does not exist: {proof_path}") from exc
    if not stat.S_ISREG(mode):
        raise CheckpointSpecError(
            f"focused proof must be a tracked regular file: {proof_path}")
    tracked = _git(worktree, "ls-files", "--error-unmatch", "--", proof_path)
    if tracked.returncode != 0:
        raise CheckpointSpecError(
            f"focused proof must be a tracked regular file: {proof_path}")
    unchanged = _git(worktree, "diff", "--quiet", "HEAD", "--", proof_path)
    if unchanged.returncode != 0:
        raise CheckpointSpecError(
            f"focused proof must match exact HEAD: {proof_path}")
    if proof_path not in argv:
        raise CheckpointSpecError(
            f"focused_proof.argv must name focused proof {proof_path}")

    head = _git(worktree, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise CheckpointSpecError("worktree revision could not be resolved")
    revision = spec["worktree_revision"].strip()
    if revision != head.stdout.strip():
        raise CheckpointSpecError(
            "worktree_revision is stale; checkpoint requires exact HEAD")
    ratchet = spec.get("ratchet_baseline")
    if not isinstance(ratchet, Mapping):
        raise CheckpointSpecError("ratchet_baseline must be a mapping")

    return {
        "schema": CHECKPOINT_SCHEMA,
        "checkpoint_id": spec["checkpoint_id"].strip(),
        "phase": spec["phase"].strip(),
        "ac_ids": ac_ids,
        "predecessor_checkpoint_ids": predecessors,
        "worktree_revision": revision,
        "declared_scope": scope,
        "focused_proof": {"path": proof_path, "argv": argv},
        "ratchet_baseline": dict(ratchet),
        "ordered_phases": list(ORDERED_CHECKPOINT_PHASES),
    }


def _is_r0010_code_task(task: Mapping) -> bool:
    task_id = str(task.get("id") or "")
    if not _R0010_TASK.search(task_id):
        return False
    return any(str(path).replace("\\", "/").endswith(".py")
               for path in task.get("scope") or [])


def closed_gap_errors(tasks: Sequence[Mapping]) -> tuple[list[str], bool]:
    """Return Plan blockers and whether new human scope authority is needed."""
    implementation = [task for task in tasks or ()
                      if isinstance(task, Mapping)
                      and _is_r0010_code_task(task)]
    errors: list[str] = []
    categories: list[str] = []
    scope_decision_required = False
    for task in implementation:
        task_id = str(task.get("id") or "?")
        category = task.get("gap_category")
        if not isinstance(category, str) or not category.strip():
            errors.append(f"task {task_id}: gap_category is missing")
            continue
        category = category.strip()
        categories.append(category)
        if category not in CLOSED_GAP_CATEGORIES:
            errors.append(
                f"task {task_id}: gap_category {category!r} is not in the "
                "closed six-category R-0010 catalog; scope_decision_required")
            scope_decision_required = True
    for category, count in sorted(Counter(categories).items()):
        if count > 1:
            errors.append(
                f"duplicate R-0010 gap_category {category!r} appears {count} times")
    missing = [category for category in CLOSED_GAP_CATEGORIES
               if category not in categories]
    if missing:
        errors.append("closed R-0010 gap categories are missing: "
                      + ", ".join(missing))
    return errors, scope_decision_required


def validate_closed_gap_plan(tasks: Sequence[Mapping]) -> dict:
    """Return the machine-readable closed-six readiness verdict."""
    errors, scope_decision_required = closed_gap_errors(tasks)
    observed = {str(task.get("gap_category", "")).strip()
                for task in tasks or () if isinstance(task, Mapping)}
    return {
        "passed": not errors,
        "errors": errors,
        "scope_decision_required": scope_decision_required,
        "categories": [category for category in CLOSED_GAP_CATEGORIES
                       if category in observed],
    }
