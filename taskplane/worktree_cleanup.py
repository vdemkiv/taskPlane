"""Proof-carrying, fail-closed cleanup of Taskplane task worktrees."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import subprocess
import time

import storage
import taskplane_lite as tp


MERGE_SCHEMA = "taskplane.task-merge/v1"
CLEANUP_SCHEMA = "taskplane.worktree-cleanup/v1"
_OUTCOMES = {"pending", "preserved", "removed", "already-clean",
             "manual-attention"}


class CleanupError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _git(cwd: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CleanupError(f"git {' '.join(args)} unavailable: {exc}") from exc


def _git_value(cwd: str, *args: str) -> str | None:
    result = _git(cwd, *args)
    value = (result.stdout or "").strip()
    return value if result.returncode == 0 and value else None


def _worktree_rows(primary: str) -> list[dict]:
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain", "-z"], cwd=primary,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        check=False)
    if result.returncode:
        raise CleanupError("git worktree registration is unavailable: "
                           + result.stderr.decode("utf-8", "replace")[-800:])
    rows = []
    for raw_record in result.stdout.split(b"\0\0"):
        fields = [field for field in raw_record.split(b"\0") if field]
        if not fields:
            continue
        row = {}
        for raw in fields:
            key, _, value = raw.partition(b" ")
            name = key.decode("utf-8", "surrogateescape")
            row[name] = (value.decode("utf-8", "surrogateescape")
                         if value else True)
        if row.get("worktree"):
            row["worktree"] = os.path.realpath(row["worktree"])
            rows.append(row)
    return rows


def validate_merge_receipt(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("schema") != MERGE_SCHEMA:
        raise ValueError("task merge receipt is invalid")
    required = ("receipt_id", "run_id", "task_id", "managed_path",
                "branch_ref", "branch_tip", "primary_checkout",
                "primary_ref", "primary_tip", "repository")
    if any(not value.get(key) for key in required):
        raise ValueError("task merge receipt is incomplete")
    # Observation time is audit metadata, not merge identity. Replaying the
    # same exact merge proof must address the same receipt.
    payload = {key: item for key, item in value.items()
               if key not in {"receipt_id", "merged_at"}}
    if value["receipt_id"] != "merge-" + _fingerprint(payload)[:24]:
        raise ValueError("task merge receipt fingerprint mismatch")
    return copy.deepcopy(value)


def record_merge_receipt(primary_checkout: str, *, task_id: str,
                         run_id: str | None = None,
                         merged_at: int | None = None) -> dict:
    """Record a merge only after local Git proves the registered tip landed."""
    primary = os.path.realpath(primary_checkout)
    registration = storage.load_task_worktree_registration(primary, task_id)
    if registration is None:
        raise CleanupError("task worktree has no exact managed registration")
    primary_ref = _git_value(primary, "symbolic-ref", "--quiet", "HEAD")
    if not primary_ref or not primary_ref.startswith("refs/heads/"):
        raise CleanupError("primary branch is detached or ambiguous")
    primary_tip = _git_value(primary, "rev-parse", "HEAD")
    if not primary_tip:
        raise CleanupError("primary branch tip is unavailable")
    branch_tip = str(registration.get("branch_tip") or "")
    landed = _git(primary, "merge-base", "--is-ancestor", branch_tip,
                  primary_tip)
    if landed.returncode:
        raise CleanupError("registered task tip is not merged into primary")
    identity = storage.resolve_repository_identity(primary)
    if identity.repo_id != (registration.get("repository") or {}).get("repo_id"):
        raise CleanupError("registered worktree repository identity changed")
    payload = {
        "schema": MERGE_SCHEMA, "run_id": str(
            run_id or registration.get("run_id") or "legacy"),
        "task_id": str(task_id),
        "repository": {"repo_id": identity.repo_id,
                       "repository_key": identity.key},
        "managed_path": registration["path"],
        "branch_ref": registration["branch_ref"],
        "branch_tip": branch_tip, "primary_checkout": primary,
        "primary_ref": primary_ref, "primary_tip": primary_tip,
        "merged_at": int(time.time() if merged_at is None else merged_at),
    }
    identity = {key: value for key, value in payload.items()
                if key != "merged_at"}
    payload["receipt_id"] = "merge-" + _fingerprint(identity)[:24]
    return payload


def _retention_reason(lifecycle: dict) -> str | None:
    row = lifecycle if isinstance(lifecycle, dict) else {}
    if row.get("variant") or row.get("variant_id") or \
            row.get("selected_variant"):
        return "variant worktrees are retained"
    status = str(row.get("status") or "")
    if row.get("active") or status in {"active", "running", "claimed"}:
        return "active task worktrees are retained"
    terminal = {"passed", "failed", "error", "cancelled", "interrupted",
                "timed_out", "handoff", "recovery"}
    if row.get("released") is not True or status not in terminal:
        return "task lifecycle is not terminal and released"
    if row.get("evidence_needed") or row.get("retain_evidence"):
        return "review evidence still requires this worktree"
    return None


def _git_state_marker(primary: str, worker: str) -> str | None:
    for cwd, markers in ((primary, ("MERGE_HEAD", "CHERRY_PICK_HEAD",
                                    "REVERT_HEAD", "rebase-merge",
                                    "rebase-apply", "sequencer")),
                         (worker, ("index.lock", "HEAD.lock", "MERGE_HEAD",
                                   "rebase-merge", "rebase-apply",
                                   "sequencer"))):
        for marker in markers:
            path = _git_value(cwd, "rev-parse", "--git-path", marker)
            if path:
                absolute = path if os.path.isabs(path) else os.path.join(cwd, path)
                if os.path.exists(absolute):
                    return marker
    return None


def _result(receipt: dict, outcome: str, *, reason: str,
            checks: dict | None = None) -> dict:
    if outcome not in _OUTCOMES:
        raise ValueError("invalid cleanup outcome")
    row = {
        "schema": CLEANUP_SCHEMA, "receipt_id": receipt["receipt_id"],
        "run_id": receipt["run_id"], "task_id": receipt["task_id"],
        "outcome": outcome, "reason": str(reason),
        "checked": copy.deepcopy(checks or {}),
        "recorded_at": int(time.time()),
    }
    row["outcome_fingerprint"] = _fingerprint({
        key: value for key, value in row.items() if key != "recorded_at"})
    return row


def validate_cleanup_record(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("schema") != CLEANUP_SCHEMA \
            or value.get("outcome") not in _OUTCOMES:
        raise ValueError("worktree cleanup record is invalid")
    if not value.get("receipt_id") or not value.get("outcome_fingerprint"):
        raise ValueError("worktree cleanup record is incomplete")
    payload = {key: item for key, item in value.items()
               if key not in {"recorded_at", "outcome_fingerprint"}}
    if value["outcome_fingerprint"] != _fingerprint(payload):
        raise ValueError("worktree cleanup record fingerprint mismatch")
    return copy.deepcopy(value)


def eligibility(receipt: dict, *, lifecycle: dict) -> dict:
    """Read-only full eligibility proof. Any uncertainty is preservation."""
    receipt = validate_merge_receipt(receipt)
    primary = os.path.realpath(receipt["primary_checkout"])
    candidate = os.path.abspath(receipt["managed_path"])
    checks = {}
    reason = _retention_reason(lifecycle)
    if reason:
        return _result(receipt, "preserved", reason=reason, checks=checks)
    try:
        identity = storage.resolve_repository_identity(primary)
        checks["repository"] = identity.repo_id
        if identity.repo_id != receipt["repository"]["repo_id"]:
            raise CleanupError("repository identity mismatch")
        expected = os.path.abspath(storage.task_worktree_path(
            primary, receipt["task_id"]))
        checks["derived_path"] = expected
        if candidate != expected:
            raise CleanupError("managed path mismatch")
        registration = storage.load_task_worktree_registration(
            primary, receipt["task_id"])
        if registration is None:
            raise CleanupError("managed registration is missing")
        for key in ("path", "branch_ref", "branch_tip", "run_id", "task_id"):
            expected_value = (candidate if key == "path" else receipt.get(key))
            if str(registration.get(key)) != str(expected_value):
                raise CleanupError(f"managed registration {key} mismatch")
        rows = _worktree_rows(primary)
        matches = [row for row in rows if row.get("worktree") == candidate]
        path_exists = os.path.lexists(candidate)
        checks["path_exists"] = path_exists
        checks["registered"] = len(matches)
        if not path_exists and not matches:
            return _result(receipt, "already-clean",
                           reason="exact path and Git registration are absent",
                           checks=checks)
        if not path_exists or len(matches) != 1:
            return _result(receipt, "manual-attention",
                           reason="path and Git registration disagree",
                           checks=checks)
        mode = os.lstat(candidate).st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise CleanupError("candidate is not a real directory")
        git_link = os.path.join(candidate, ".git")
        if not os.path.isfile(git_link) or stat.S_ISLNK(os.lstat(git_link).st_mode) \
                or os.lstat(git_link).st_nlink != 1:
            raise CleanupError("candidate is not a linked worktree")
        row = matches[0]
        if row.get("locked") or row.get("prunable"):
            raise CleanupError("Git worktree is locked or prunable")
        if row.get("branch") != receipt["branch_ref"] or \
                row.get("HEAD") != receipt["branch_tip"]:
            raise CleanupError("registered branch or tip changed")
        live_branch = _git_value(candidate, "symbolic-ref", "--quiet", "HEAD")
        live_tip = _git_value(candidate, "rev-parse", "HEAD")
        if live_branch != receipt["branch_ref"] or \
                live_tip != receipt["branch_tip"]:
            raise CleanupError("live branch or tip changed")
        primary_ref = _git_value(primary, "symbolic-ref", "--quiet", "HEAD")
        if primary_ref != receipt["primary_ref"] or \
                not str(primary_ref or "").startswith("refs/heads/"):
            raise CleanupError("primary branch is missing or ambiguous")
        primary_tip = _git_value(primary, "rev-parse", "HEAD")
        if not primary_tip:
            raise CleanupError("primary tip is missing")
        checks["primary_tip"] = primary_tip
        clean = _git(candidate, "status", "--porcelain=v2",
                     "--untracked-files=all", "--ignored=no")
        if clean.returncode or (clean.stdout or ""):
            raise CleanupError("worktree is dirty, staged, untracked, or unmerged")
        marker = _git_state_marker(primary, candidate)
        if marker:
            raise CleanupError(f"Git operation state is active: {marker}")
        try:
            active = tp.load_active(candidate)
        except Exception as exc:
            raise CleanupError("worktree contract state is unreadable") from exc
        if active:
            raise CleanupError("worktree still has an active contract")
        ancestor = _git(primary, "merge-base", "--is-ancestor",
                        receipt["branch_tip"], primary_tip)
        if ancestor.returncode:
            raise CleanupError("recorded branch tip is not in current primary")
        checks.update({"branch_ref": live_branch, "branch_tip": live_tip,
                       "primary_ref": primary_ref, "clean": True,
                       "lifecycle": "terminal_released", "ancestor": True})
    except (CleanupError, storage.StorageIdentityError, OSError) as exc:
        return _result(receipt, "preserved", reason=str(exc), checks=checks)
    return _result(receipt, "pending", reason="eligible", checks=checks)


def resource_identity(receipt: dict, *, lifecycle: dict) -> dict:
    """Return the exact live identity consumed by the owned-cleanup adapter."""
    checked = validate_merge_receipt(receipt)
    proof = eligibility(checked, lifecycle=lifecycle)
    if proof.get("outcome") != "pending":
        raise CleanupError(
            "worktree identity is not cleanup-eligible: " +
            str(proof.get("reason") or proof.get("outcome")))
    return {
        "schema": "taskplane.owned-worktree-identity/v1",
        "registration_path": os.path.abspath(checked["managed_path"]),
        "branch_ref": checked["branch_ref"],
        "branch_tip": checked["branch_tip"],
        "run_id": checked["run_id"],
        "task_id": checked["task_id"],
        "merge_receipt_id": checked["receipt_id"],
        "repository_id": checked["repository"]["repo_id"],
        "lifecycle_fingerprint": _fingerprint(lifecycle),
    }


def cleanup(receipt: dict, *, lifecycle: dict) -> dict:
    """Revalidate under a receipt lock, then remove once without force."""
    receipt = validate_merge_receipt(receipt)
    primary = os.path.realpath(receipt["primary_checkout"])
    lock_path = storage.task_worktree_registration_path(
        primary, receipt["task_id"]) + ".cleanup"
    try:
        with tp.file_lock(lock_path, timeout=10.0):
            proof = eligibility(receipt, lifecycle=lifecycle)
            if proof["outcome"] != "pending":
                return proof
            candidate = receipt["managed_path"]
            removed = _git(primary, "worktree", "remove", "--", candidate)
            if removed.returncode:
                return _result(
                    receipt, "manual-attention",
                    reason="plain git worktree remove failed; no force retry: "
                    + (removed.stderr or removed.stdout or "unknown error")[-800:],
                    checks=proof["checked"])
            rows = _worktree_rows(primary)
            registered = any(row.get("worktree") == os.path.abspath(candidate)
                             for row in rows)
            exists = os.path.lexists(candidate)
            checked = {**proof["checked"],
                       "post_registered": registered,
                       "post_path_exists": exists,
                       "remove_command": ["git", "worktree", "remove", "--",
                                          candidate],
                       "force": False, "branch_deleted": False}
            if registered or exists:
                return _result(receipt, "manual-attention",
                               reason="post-removal identity check failed",
                               checks=checked)
            return _result(receipt, "removed", reason="eligible linked worktree removed",
                           checks=checked)
    except tp.StateError as exc:
        return _result(receipt, "preserved",
                       reason=f"cleanup lock unavailable: {exc}")
    except (CleanupError, OSError) as exc:
        return _result(
            receipt, "manual-attention",
            reason=f"cleanup execution failed; no force retry: {exc}")
