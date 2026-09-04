"""Deterministic managed repository and GitHub pull-request acquisition."""
from __future__ import annotations

import copy
import contextvars
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from typing import Callable, Mapping, Sequence

from delivery_ports import GitResult, GitRunner, SubprocessGitRunner
import storage
import recovery
import taskplane_lite as tp


STAGE_AUTHORITY_SCHEMA = "taskplane.stage-authority-binding/v1"
_STAGE_AUTHORITY_FIELDS = frozenset({
    "schema", "run_id", "repository_id", "repository_key", "worktree_id",
    "target_revision", "worktree_revision", "requirement_id",
    "requirement_revision", "design_revision", "design_fingerprint",
    "actor", "session_id", "authority_revision", "authority_fingerprint",
})
_AUTHORITY_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_AUTHORITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_AUTHORITY_REPOSITORY_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_AUTHORITY_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_PICKUP_MERGE_FIELDS = frozenset({
    "schema", "status", "task_id", "primary_checkout", "branch_tip",
    "fingerprint",
})
PHASE_REPOSITORY_RECEIPT_SCHEMA = "taskplane.repository-phase-receipt/v1"
_PHASE_REPOSITORY_RECEIPT_FIELDS = frozenset({
    "schema", "status", "repository_id", "task_id", "revision",
    "source_receipt_fingerprint", "fingerprint",
})
REPOSITORY_PREPARATION_REQUEST_FIELDS = frozenset({
    "schema", "operation_id", "run_id", "target",
    "workspace_locator_fingerprint", "attempt",
    "predecessor_result_fingerprint",
})
REPOSITORY_PREPARATION_TARGET_FIELDS = frozenset({
    "kind", "repository_id", "remote", "requested_ref",
})
REPOSITORY_PREPARATION_RESULT_FIELDS = frozenset({
    "schema", "operation_id", "run_id", "request_fingerprint", "attempt",
    "status", "reason_code", "retryability", "refusal_identity",
    "predecessor_result_fingerprint", "repository_id",
    "remote_default_branch", "remote_default_ref", "fetch_receipt",
    "resolved_sha", "checkout", "fingerprint",
})
_PREPARATION_REQUEST_SCHEMA = \
    "taskplane.repository-preparation-request/v1"
_PREPARATION_RESULT_SCHEMA = "taskplane.repository-preparation/v1"
_PREPARATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PREPARATION_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_PREPARATION_RETRYABILITY = {
    "ready": ("ready", "none"),
    "authority_required": ("needs_user", "retry_after_user"),
    "host_policy": ("waiting", "retry_after_external"),
    "external_unavailable": ("waiting", "retry_after_external"),
    "repeated_failure": ("waiting", "retry_after_external"),
    "invalid_request": ("refused", "change_request"),
    "remote_default_missing": ("refused", "change_request"),
    "remote_default_ambiguous": ("refused", "change_request"),
    "default_ref_unfetched": ("refused", "change_request"),
    "identity_mismatch": ("refused", "change_request"),
}
_PREPARATION_BOUNDARIES = {
    "authority_required": "git_transport",
    "host_policy": "git_transport",
    "external_unavailable": "git_transport",
    "repeated_failure": "git_transport",
    "invalid_request": "request_validation",
    "remote_default_missing": "remote_default_advertisement",
    "remote_default_ambiguous": "remote_default_advertisement",
    "default_ref_unfetched": "fetched_default_ref",
    "identity_mismatch": "repository_identity",
}
_DEFAULT_ACQUISITION_DEADLINE_SECONDS = 600.0
_DEFAULT_RETRY_BASE_SECONDS = 1.0
_DEFAULT_RETRY_MAX_SECONDS = 30.0
_ACQUISITION_DEADLINE: contextvars.ContextVar[
    tuple[float, Callable[[], float]] | None
] = contextvars.ContextVar("taskplane_repository_deadline", default=None)
_ACQUISITION_RETRY_OWNER: contextvars.ContextVar[bool] = \
    contextvars.ContextVar("taskplane_repository_retry_owner", default=False)
_ACQUISITION_HTTP11: contextvars.ContextVar[bool] = \
    contextvars.ContextVar("taskplane_repository_http11", default=False)


class RepositoryAcquisitionError(RuntimeError):
    def __init__(self, kind: str, detail: str, *,
                 preparation_result: dict | None = None,
                 retry_after: str | float | int | None = None,
                 recovery_result: dict | None = None):
        super().__init__(detail)
        self.kind = str(kind)
        self.detail = str(detail)
        self.preparation_result = copy.deepcopy(preparation_result)
        self.retry_after = retry_after
        self.recovery_result = copy.deepcopy(recovery_result)


def guard_terminal_delivery(
    terminal_receipt: Mapping,
    *,
    action: str,
    current_sha: str,
    resulting_sha: str | None = None,
    external_authority: bool = False,
) -> dict:
    """Refuse delivery claims that do not preserve the finalized exact SHA."""
    try:
        from taskplane import terminal_truth
    except ImportError:  # direct executable/import compatibility
        import terminal_truth
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"done", "merge", "push", "tag", "publish", "release"}:
        raise RepositoryAcquisitionError("terminal_truth", "delivery action is invalid")
    try:
        checked = terminal_truth.assert_terminal_authority(
            terminal_receipt, expected_sha=current_sha
        )
    except terminal_truth.TerminalTruthError as exc:
        raise RepositoryAcquisitionError("terminal_truth", exc.detail) from exc
    terminal_sha = checked["bundle"]["identity"]["full_source_sha"]
    if normalized_action == "merge" and resulting_sha is None:
        raise RepositoryAcquisitionError(
            "terminal_truth",
            "merge requires the observed resulting_sha for exact-SHA revalidation",
        )
    if resulting_sha is not None and str(resulting_sha) != terminal_sha:
        raise RepositoryAcquisitionError(
            "terminal_truth",
            "SHA-changing merge invalidates exact-SHA terminal finalization",
        )
    if normalized_action in {"push", "tag", "publish", "release"} and \
            external_authority is not True:
        raise RepositoryAcquisitionError(
            "external_authority",
            f"{normalized_action} is not authorized by terminal truth",
        )
    return checked


def _canonical_fingerprint(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _preparation_string(value: object, label: str, *,
                        identifier: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or \
            len(value) > 2048 or any(
                ord(character) < 32 or ord(character) == 127
                for character in value):
        raise RepositoryAcquisitionError(
            "identity", f"repository preparation {label} is invalid")
    if identifier and not _PREPARATION_ID.fullmatch(value):
        raise RepositoryAcquisitionError(
            "identity", f"repository preparation {label} is invalid")
    return value


def _valid_requested_ref(value: object) -> str | None:
    if value is None:
        return None
    ref = _preparation_string(value, "requested ref")
    if not _valid_branch_ref(ref):
        raise RepositoryAcquisitionError(
            "identity", "repository preparation requested ref is not exact")
    return ref


def _valid_branch_ref(ref: object) -> bool:
    if not isinstance(ref, str) or not ref.startswith("refs/heads/"):
        return False
    branch = ref[len("refs/heads/"):]
    return bool(branch and len(ref) <= 1024
                and not branch.startswith(('/', '.'))
                and not branch.endswith(('/', '.', '.lock'))
                and ".." not in branch and "@{" not in branch
                and "//" not in branch and not any(
                    character.isspace() or character in "~^:?*[\\"
                    or ord(character) < 32 or ord(character) == 127
                    for character in branch))


def validate_repository_preparation_request(request: object) -> dict:
    """Validate and normalize the closed repository preparation request."""
    if not isinstance(request, dict) or set(request) != \
            REPOSITORY_PREPARATION_REQUEST_FIELDS:
        raise RepositoryAcquisitionError(
            "identity", "repository preparation request fields are invalid")
    if request.get("schema") != _PREPARATION_REQUEST_SCHEMA:
        raise RepositoryAcquisitionError(
            "identity", "repository preparation request schema is invalid")
    target = request.get("target")
    if not isinstance(target, dict) or set(target) != \
            REPOSITORY_PREPARATION_TARGET_FIELDS or \
            target.get("kind") != "repository":
        raise RepositoryAcquisitionError(
            "identity", "repository preparation target is invalid")
    normalized = copy.deepcopy(request)
    normalized["operation_id"] = _preparation_string(
        request.get("operation_id"), "operation id", identifier=True)
    normalized["run_id"] = _preparation_string(
        request.get("run_id"), "run id", identifier=True)
    normalized["target"]["repository_id"] = _preparation_string(
        target.get("repository_id"), "repository id")
    normalized["target"]["remote"] = _preparation_string(
        target.get("remote"), "remote")
    normalized["target"]["requested_ref"] = _valid_requested_ref(
        target.get("requested_ref"))
    locator = request.get("workspace_locator_fingerprint")
    if not isinstance(locator, str) or not \
            _PREPARATION_FINGERPRINT.fullmatch(locator):
        raise RepositoryAcquisitionError(
            "identity", "repository preparation locator is invalid")
    attempt = request.get("attempt")
    predecessor = request.get("predecessor_result_fingerprint")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise RepositoryAcquisitionError(
            "identity", "repository preparation attempt is invalid")
    if predecessor is not None and (not isinstance(predecessor, str) or
                                    not _PREPARATION_FINGERPRINT.fullmatch(
                                        predecessor)):
        raise RepositoryAcquisitionError(
            "identity", "repository preparation predecessor is invalid")
    if (attempt == 1) != (predecessor is None):
        raise RepositoryAcquisitionError(
            "identity", "repository preparation predecessor is unbound")
    return normalized


def _request_fingerprint(request: Mapping[str, object]) -> str:
    """Fingerprint the retry-stable logical request, excluding lineage."""
    return _canonical_fingerprint({
        field: copy.deepcopy(request[field])
        for field in (
            "schema", "operation_id", "run_id", "target",
            "workspace_locator_fingerprint")
    })


def _refusal_identity(request_fingerprint: str, operation_id: str,
                      reason_code: str) -> str:
    return _canonical_fingerprint({
        "request_fingerprint": request_fingerprint,
        "operation_id": operation_id,
        "reason_code": reason_code,
        "failed_boundary": _PREPARATION_BOUNDARIES[reason_code],
    })


def _preparation_result(request: Mapping[str, object], *, reason_code: str,
                        repository_id: str | None = None,
                        remote_default_branch: str | None = None,
                        remote_default_ref: str | None = None,
                        fetch_receipt: dict | None = None,
                        resolved_sha: str | None = None,
                        checkout: str | None = None) -> dict:
    status, retryability = _PREPARATION_RETRYABILITY[reason_code]
    request_fingerprint = _request_fingerprint(request)
    material = {
        "schema": _PREPARATION_RESULT_SCHEMA,
        "operation_id": request["operation_id"],
        "run_id": request["run_id"],
        "request_fingerprint": request_fingerprint,
        "attempt": request["attempt"],
        "status": status,
        "reason_code": reason_code,
        "retryability": retryability,
        "refusal_identity": (None if status == "ready" else
                             _refusal_identity(
                                 request_fingerprint,
                                 str(request["operation_id"]), reason_code)),
        "predecessor_result_fingerprint":
            request["predecessor_result_fingerprint"],
        "repository_id": repository_id,
        "remote_default_branch": remote_default_branch,
        "remote_default_ref": remote_default_ref,
        "fetch_receipt": copy.deepcopy(fetch_receipt),
        "resolved_sha": resolved_sha,
        "checkout": checkout,
    }
    result = {**material, "fingerprint": _canonical_fingerprint(material)}
    return validate_repository_preparation_result(result)


def _invalid_request_result(request: object) -> dict:
    raw = request if isinstance(request, dict) else {}
    operation_id = raw.get("operation_id")
    run_id = raw.get("run_id")
    attempt = raw.get("attempt")
    predecessor = raw.get("predecessor_result_fingerprint")
    target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
    safe = {
        "schema": _PREPARATION_REQUEST_SCHEMA,
        "operation_id": (operation_id if isinstance(operation_id, str) and
                         _PREPARATION_ID.fullmatch(operation_id)
                         else "invalid-operation"),
        "run_id": (run_id if isinstance(run_id, str) and
                   _PREPARATION_ID.fullmatch(run_id) else "invalid-run"),
        "target": {
            "kind": "repository",
            "repository_id": str(target.get("repository_id") or "invalid"),
            "remote": str(target.get("remote") or "invalid"),
            "requested_ref": None,
        },
        "workspace_locator_fingerprint": (
            str(raw.get("workspace_locator_fingerprint"))
            if _PREPARATION_FINGERPRINT.fullmatch(
                str(raw.get("workspace_locator_fingerprint") or ""))
            else "0" * 64),
        "attempt": (attempt if isinstance(attempt, int) and
                    not isinstance(attempt, bool) and attempt > 0 else 1),
        "predecessor_result_fingerprint": (
            predecessor if isinstance(predecessor, str) and
            _PREPARATION_FINGERPRINT.fullmatch(predecessor) else None),
    }
    if safe["attempt"] == 1:
        safe["predecessor_result_fingerprint"] = None
    elif safe["predecessor_result_fingerprint"] is None:
        safe["attempt"] = 1
    return _preparation_result(safe, reason_code="invalid_request")


def validate_repository_preparation_result(result: object) -> dict:
    """Validate the exact closed result and its content fingerprint."""
    if not isinstance(result, dict) or set(result) != \
            REPOSITORY_PREPARATION_RESULT_FIELDS:
        raise RepositoryAcquisitionError(
            "identity", "repository preparation result fields are invalid")
    if result.get("schema") != _PREPARATION_RESULT_SCHEMA:
        raise RepositoryAcquisitionError(
            "identity", "repository preparation result schema is invalid")
    operation_id = result.get("operation_id")
    run_id = result.get("run_id")
    if not isinstance(operation_id, str) or not \
            _PREPARATION_ID.fullmatch(operation_id) or \
            not isinstance(run_id, str) or not _PREPARATION_ID.fullmatch(run_id):
        raise RepositoryAcquisitionError(
            "identity", "repository preparation result identity is invalid")
    for field in ("request_fingerprint", "fingerprint"):
        if not isinstance(result.get(field), str) or not \
                _PREPARATION_FINGERPRINT.fullmatch(result[field]):
            raise RepositoryAcquisitionError(
                "identity", f"repository preparation {field} is invalid")
    attempt = result.get("attempt")
    predecessor = result.get("predecessor_result_fingerprint")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1 \
            or (attempt == 1) != (predecessor is None) or \
            (predecessor is not None and (
                not isinstance(predecessor, str) or
                not _PREPARATION_FINGERPRINT.fullmatch(predecessor))):
        raise RepositoryAcquisitionError(
            "identity", "repository preparation result lineage is invalid")
    reason = result.get("reason_code")
    expected = _PREPARATION_RETRYABILITY.get(str(reason))
    if expected != (result.get("status"), result.get("retryability")):
        raise RepositoryAcquisitionError(
            "identity", "repository preparation retryability is invalid")
    facts = (
        result.get("repository_id"), result.get("remote_default_branch"),
        result.get("remote_default_ref"), result.get("fetch_receipt"),
        result.get("resolved_sha"), result.get("checkout"),
    )
    if result.get("status") == "ready":
        if result.get("refusal_identity") is not None or \
                not all(facts) or not all(isinstance(result.get(field), str)
                    for field in ("repository_id", "remote_default_branch",
                                  "remote_default_ref", "checkout")) or \
                not isinstance(result.get("fetch_receipt"), dict) \
                or not _GIT_OBJECT_ID.fullmatch(
                    str(result.get("resolved_sha") or "")):
            raise RepositoryAcquisitionError(
                "identity", "ready repository preparation facts are invalid")
    else:
        if any(value is not None for value in facts) or not isinstance(
                result.get("refusal_identity"), str) or not \
                _PREPARATION_FINGERPRINT.fullmatch(
                    result["refusal_identity"]):
            raise RepositoryAcquisitionError(
                "identity", "repository refusal facts are invalid")
        expected_refusal = _refusal_identity(
            result["request_fingerprint"], operation_id, str(reason))
        if result["refusal_identity"] != expected_refusal:
            raise RepositoryAcquisitionError(
                "identity", "repository refusal identity is invalid")
    material = {key: result[key] for key in result if key != "fingerprint"}
    if result["fingerprint"] != _canonical_fingerprint(material):
        raise RepositoryAcquisitionError(
            "identity", "repository preparation fingerprint is invalid")
    return copy.deepcopy(result)


def _git_call(git_runner: GitRunner, args: Sequence[str], *,
              cwd: str | None = None) -> GitResult:
    try:
        result = git_runner.run(tuple(args), cwd=cwd)
    except AssertionError:
        raise
    except RepositoryAcquisitionError as exc:
        return GitResult(
            1, "", f"taskplane-error:{exc.kind}\n{exc.detail}")
    except Exception as exc:
        return GitResult(
            1, "", f"taskplane-error:checkout\nGit runner failed: {exc}")
    if not isinstance(result, GitResult):
        try:
            result = GitResult(
                int(result.returncode), str(result.stdout or ""),
                str(result.stderr or ""))
        except (AttributeError, TypeError, ValueError) as exc:
            return GitResult(
                1, "", "taskplane-error:checkout\n"
                f"Git runner returned an invalid result: {exc}")
    return result


def _remote_matches(expected: str, observed: str) -> bool:
    try:
        return storage.identity_from_remote(expected).repo_id == \
            storage.identity_from_remote(observed).repo_id
    except ValueError:
        if os.path.isabs(expected) or os.path.isabs(observed):
            return os.path.realpath(expected) == os.path.realpath(observed)
        return expected.rstrip("/") == observed.rstrip("/")


def _git_wait_reason(result: GitResult) -> str:
    marker = str(result.stderr or "").splitlines()[0:1]
    if marker and marker[0].startswith("taskplane-error:"):
        kind = marker[0].split(":", 1)[1].strip().lower()
        if kind in {"authentication", "auth", "permission"}:
            return "authority_required"
        if kind in {"host-policy", "policy"}:
            return "host_policy"
        if kind in {"external-unavailable", "external", "network"}:
            return "external_unavailable"
    classification = _classify_failure(
        "\n".join((result.stdout, result.stderr)))
    if classification == "authentication":
        return "authority_required"
    if classification == "network":
        return "external_unavailable"
    return "repeated_failure"


def _advertised_remote_default(output: str) -> tuple[str, str] | str:
    lines = [line for line in str(output or "").splitlines() if line]
    if not lines:
        return "remote_default_missing"
    symrefs: list[str] = []
    object_ids: list[str] = []
    malformed = False
    for line in lines:
        fields = line.split("\t")
        if len(fields) != 2 or fields[1] != "HEAD":
            malformed = True
            continue
        if fields[0].startswith("ref: "):
            symrefs.append(fields[0][5:])
        elif _GIT_OBJECT_ID.fullmatch(fields[0].lower()):
            object_ids.append(fields[0].lower())
        else:
            malformed = True
    if len(symrefs) != 1 or len(object_ids) != 1 or malformed or not \
            _valid_branch_ref(symrefs[0]):
        return "remote_default_ambiguous"
    return symrefs[0], object_ids[0]


def _retry_or_replay(request: Mapping[str, object],
                     prior_result: object | None) -> dict | None:
    attempt = int(request["attempt"])
    if prior_result is None:
        if attempt != 1:
            return _preparation_result(
                request, reason_code="invalid_request")
        return None
    try:
        prior = validate_repository_preparation_result(prior_result)
    except RepositoryAcquisitionError:
        return _preparation_result(request, reason_code="invalid_request")
    identity_matches = (
        prior["operation_id"] == request["operation_id"]
        and prior["run_id"] == request["run_id"]
        and prior["request_fingerprint"] == _request_fingerprint(request)
    )
    if prior["attempt"] == attempt:
        if identity_matches and prior["predecessor_result_fingerprint"] == \
                request["predecessor_result_fingerprint"]:
            return prior
        return _preparation_result(request, reason_code="invalid_request")
    if (not identity_matches or attempt != prior["attempt"] + 1
            or request["predecessor_result_fingerprint"] !=
            prior["fingerprint"] or prior["retryability"] not in {
                "retry_after_user", "retry_after_external"}):
        return _preparation_result(request, reason_code="invalid_request")
    return None


def prepare(request: object, *, mirror_path: str, worktree_root: str,
            git_runner: GitRunner | None = None,
            prior_result: object | None = None) -> dict:
    """Prepare a hosted default checkout without trusting the mirror's HEAD.

    Git ordering is intentionally strict: fetch, read the remote advertisement,
    verify the corresponding fetched tracking ref, bind the mirror's symbolic
    HEAD to that verified ref, and only then resolve/create the checkout.
    """
    try:
        normalized = validate_repository_preparation_request(request)
    except RepositoryAcquisitionError:
        return _invalid_request_result(request)
    replay = _retry_or_replay(normalized, prior_result)
    if replay is not None:
        return replay

    runner = git_runner or SubprocessGitRunner()
    mirror = os.path.realpath(os.path.abspath(mirror_path))
    worktrees = os.path.realpath(os.path.abspath(worktree_root))
    expected_remote = normalized["target"]["remote"]
    common = ("--git-dir", mirror)

    remote_result = _git_call(
        runner, (*common, "remote", "get-url", "origin"))
    if remote_result.returncode:
        return _preparation_result(
            normalized, reason_code=_git_wait_reason(remote_result))
    observed_remotes = [line.strip() for line in remote_result.stdout.splitlines()
                        if line.strip()]
    if len(observed_remotes) != 1 or not _remote_matches(
            expected_remote, observed_remotes[0]):
        return _preparation_result(
            normalized, reason_code="identity_mismatch")

    refspec = "+refs/heads/*:refs/remotes/origin/*"
    fetch = _git_call(
        runner, (*common, "fetch", "--prune", "origin", refspec))
    if fetch.returncode:
        return _preparation_result(
            normalized, reason_code=_git_wait_reason(fetch))
    fetch_receipt = {
        "schema": "taskplane.repository-fetch/v1",
        "remote": expected_remote,
        "refspec": refspec,
        "output_fingerprint": _canonical_fingerprint({
            "stdout": fetch.stdout, "stderr": fetch.stderr}),
    }

    advertisement = _git_call(
        runner, (*common, "ls-remote", "--symref", "origin", "HEAD"))
    if advertisement.returncode:
        return _preparation_result(
            normalized, reason_code=_git_wait_reason(advertisement))
    advertised = _advertised_remote_default(advertisement.stdout)
    if isinstance(advertised, str):
        return _preparation_result(normalized, reason_code=advertised)
    advertised_ref, advertised_sha = advertised
    requested_ref = normalized["target"]["requested_ref"]
    if requested_ref is not None and requested_ref != advertised_ref:
        return _preparation_result(
            normalized, reason_code="identity_mismatch")
    branch = advertised_ref[len("refs/heads/"):]
    fetched_ref = f"refs/remotes/origin/{branch}"

    fetched = _git_call(
        runner, (*common, "show-ref", "--verify", "--hash", fetched_ref))
    fetched_lines = [line.strip().lower()
                     for line in fetched.stdout.splitlines() if line.strip()]
    if fetched.returncode or len(fetched_lines) != 1 or not \
            _GIT_OBJECT_ID.fullmatch(fetched_lines[0]):
        return _preparation_result(
            normalized, reason_code="default_ref_unfetched")
    if fetched_lines[0] != advertised_sha:
        return _preparation_result(
            normalized, reason_code="identity_mismatch")
    bound = _git_call(
        runner, (*common, "symbolic-ref", "HEAD", fetched_ref))
    if bound.returncode:
        return _preparation_result(
            normalized, reason_code=_git_wait_reason(bound))
    commit = _git_call(
        runner, (*common, "rev-parse", "--verify", f"{fetched_ref}^{{commit}}"))
    resolved = commit.stdout.strip().lower()
    if commit.returncode or not _GIT_OBJECT_ID.fullmatch(resolved):
        return _preparation_result(
            normalized, reason_code="default_ref_unfetched")
    if resolved != advertised_sha:
        return _preparation_result(
            normalized, reason_code="identity_mismatch")
    try:
        os.makedirs(worktrees, exist_ok=True)
    except OSError:
        return _preparation_result(
            normalized, reason_code="host_policy")
    checkout = os.path.realpath(os.path.join(
        worktrees, f"repo-{resolved[:12]}"))
    if os.path.isdir(checkout):
        existing = _git_call(runner, ("rev-parse", "--verify", "HEAD^{commit}"),
                             cwd=checkout)
        if existing.returncode or existing.stdout.strip().lower() != resolved:
            return _preparation_result(
                normalized, reason_code="identity_mismatch")
    else:
        checked_out = _git_call(
            runner, (*common, "worktree", "add", "--detach", checkout,
                     resolved))
        if checked_out.returncode:
            return _preparation_result(
                normalized, reason_code=_git_wait_reason(checked_out))
    return _preparation_result(
        normalized, reason_code="ready",
        repository_id=normalized["target"]["repository_id"],
        remote_default_branch=branch, remote_default_ref=fetched_ref,
        fetch_receipt=fetch_receipt, resolved_sha=resolved,
        checkout=checkout)


def _authority_string(value: object, label: str, *,
                      pattern: re.Pattern[str] | None = None,
                      limit: int = 256) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or \
            len(value) > limit or any(
                ord(character) < 32 or ord(character) == 127
                for character in value) or os.path.isabs(value) or \
            _WINDOWS_ABSOLUTE_PATH.match(value):
        raise RepositoryAcquisitionError(
            "authority", f"stage authority {label} is invalid")
    if pattern is not None and not pattern.fullmatch(value):
        raise RepositoryAcquisitionError(
            "authority", f"stage authority {label} is invalid")
    return value


def _normalize_stage_authority(binding: object) -> dict:
    """Validate the closed, path-free authority identity for one mutation."""
    if not isinstance(binding, dict):
        raise RepositoryAcquisitionError(
            "authority", "stage authority binding must be an object")
    keys = set(binding)
    if keys != _STAGE_AUTHORITY_FIELDS:
        raise RepositoryAcquisitionError(
            "authority", "stage authority binding fields are incomplete")
    if binding.get("schema") != STAGE_AUTHORITY_SCHEMA:
        raise RepositoryAcquisitionError(
            "authority", "stage authority binding schema is invalid")
    normalized = copy.deepcopy(binding)
    normalized["run_id"] = _authority_string(
        binding.get("run_id"), "run id", pattern=_AUTHORITY_RUN_ID,
        limit=128)
    normalized["repository_id"] = _authority_string(
        binding.get("repository_id"), "repository id",
        pattern=_AUTHORITY_REPOSITORY_ID)
    for field, label in (
            ("repository_key", "repository key"),
            ("worktree_id", "worktree id"),
            ("requirement_id", "requirement id"),
            ("actor", "actor"),
            ("session_id", "session id")):
        normalized[field] = _authority_string(
            binding.get(field), label, pattern=_AUTHORITY_ID)
    for field, label in (
            ("target_revision", "target revision"),
            ("worktree_revision", "worktree revision"),
            ("requirement_revision", "requirement revision")):
        normalized[field] = _authority_string(
            binding.get(field), label, limit=256)
    design_revision = binding.get("design_revision")
    design_fingerprint = binding.get("design_fingerprint")
    if (design_revision is None) != (design_fingerprint is None):
        raise RepositoryAcquisitionError(
            "authority", "stage authority design identity is incomplete")
    if design_revision is not None:
        normalized["design_revision"] = _authority_string(
            design_revision, "design revision", limit=256)
        normalized["design_fingerprint"] = _authority_string(
            design_fingerprint, "design fingerprint",
            pattern=_AUTHORITY_FINGERPRINT, limit=64)
    revision = binding.get("authority_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise RepositoryAcquisitionError(
            "authority", "stage authority revision is invalid")
    normalized["authority_fingerprint"] = _authority_string(
        binding.get("authority_fingerprint"), "fingerprint",
        pattern=_AUTHORITY_FINGERPRINT, limit=64)
    return normalized


def revalidate_stage_authority(expected_binding: dict,
                               current_binding: dict) -> dict:
    """Fail closed unless every stage-mutation authority fact is still exact.

    The caller resolves ``current_binding`` at the last possible moment under
    its run transaction lock.  This seam performs no advisory upgrade and
    stores no host paths; any identity or revision drift is a stable authority
    failure rather than a mergeable lifecycle conflict.
    """
    expected = _normalize_stage_authority(expected_binding)
    current = _normalize_stage_authority(current_binding)
    for field in sorted(_STAGE_AUTHORITY_FIELDS - {"schema"}):
        if expected[field] != current[field]:
            raise RepositoryAcquisitionError(
                "authority", f"stage authority binding changed: {field}")
    return copy.deepcopy(current)


@dataclass(frozen=True)
class AcquisitionResult:
    checkout: str
    base_ref: str
    base: str
    head: str
    merge_base: str
    changed_files: tuple[str, ...]
    metadata: dict


def _positive_finite(value: object, label: str, *, allow_zero: bool = False) \
        -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or \
            not math.isfinite(float(value)) or \
            (float(value) < 0 if allow_zero else float(value) <= 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{label} must be a {qualifier} finite number")
    return float(value)


def _retry_after_seconds(exc: RepositoryAcquisitionError, *, now: float) \
        -> float | None:
    """Return a provider delay from a typed value or HTTP header excerpt."""
    value: object = exc.retry_after
    if value is None:
        match = re.search(
            r"(?im)^\s*retry-after\s*:\s*([^\r\n]+)", exc.detail)
        if match is None:
            return None
        value = match.group(1).strip()
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) or \
            (isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value)):
        seconds = float(value)
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    if not isinstance(value, str):
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return None
    return max(0.0, parsed.timestamp() - now)


def _acquisition_timeout(maximum: float) -> float:
    """Cap one blocking operation by the active end-to-end deadline."""
    bounded = _positive_finite(maximum, "operation timeout")
    active = _ACQUISITION_DEADLINE.get()
    if active is None:
        return bounded
    deadline, monotonic = active
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise RepositoryAcquisitionError(
            "network", "repository acquisition deadline elapsed")
    return min(bounded, remaining)


def _attempt_record(*, attempt: int, started: float, finished: float,
                    status: str, failure_class: str | None = None,
                    detail: str | None = None) -> dict:
    record = {
        "attempt": attempt,
        "started_after_seconds": round(started, 6),
        "duration_seconds": round(max(0.0, finished - started), 6),
        "status": status,
    }
    if failure_class is not None:
        record["failure_class"] = failure_class
    if detail is not None:
        record["detail_fingerprint"] = hashlib.sha256(
            detail.encode("utf-8")).hexdigest()
    return record


def _waiting_recovery(*, reason: str, detail: str, attempts: int,
                      telemetry: list[dict],
                      recovery_record: dict | None = None) -> dict:
    result = {
        "schema": "taskplane.repository-preparation/v1",
        "status": "waiting", "reason": reason, "detail": detail,
        "attempts": attempts, "attempt_telemetry": copy.deepcopy(telemetry),
    }
    if recovery_record is not None:
        result["recovery"] = recovery_record
    return result


def acquire_with_recovery(acquire: Callable[[], object], *,
                          max_attempts: int = 3,
                          deadline_seconds: float =
                          _DEFAULT_ACQUISITION_DEADLINE_SECONDS,
                          base_backoff_seconds: float =
                          _DEFAULT_RETRY_BASE_SECONDS,
                          max_backoff_seconds: float =
                          _DEFAULT_RETRY_MAX_SECONDS,
                          monotonic: Callable[[], float] = time.monotonic,
                          wall_time: Callable[[], float] = time.time,
                          sleep: Callable[[float], None] = time.sleep,
                          random_value: Callable[[], float] = random.random) \
        -> dict:
    """Run repository preparation under bounded consolidated authority.

    Authentication is a genuine authority boundary.  Host policy and an
    unavailable external system wait for their state to change.  Routine
    transfer/checkout failures have exactly one retry owner here.  Every wait
    uses capped exponential full jitter, honors Retry-After, and remains
    inside one absolute deadline shared with the underlying subprocesses.
    """
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or \
            max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    deadline_span = _positive_finite(deadline_seconds, "deadline_seconds")
    base_backoff = _positive_finite(
        base_backoff_seconds, "base_backoff_seconds", allow_zero=True)
    max_backoff = _positive_finite(
        max_backoff_seconds, "max_backoff_seconds", allow_zero=True)
    if base_backoff > max_backoff:
        raise ValueError("base_backoff_seconds cannot exceed max_backoff_seconds")
    started_at = monotonic()
    deadline = started_at + deadline_span
    telemetry: list[dict] = []
    fingerprints: list[str] = []
    attempt = 0
    deadline_token = _ACQUISITION_DEADLINE.set((deadline, monotonic))
    owner_token = _ACQUISITION_RETRY_OWNER.set(True)
    http_token = _ACQUISITION_HTTP11.set(False)
    try:
        while True:
            if monotonic() >= deadline:
                return _waiting_recovery(
                    reason="acquisition_deadline",
                    detail="repository acquisition deadline elapsed",
                    attempts=attempt, telemetry=telemetry)
            attempt += 1
            attempt_started = monotonic()
            try:
                value = acquire()
            except RepositoryAcquisitionError as exc:
                finished = monotonic()
                preparation_result = exc.preparation_result
                kind = str(exc.kind or "checkout").lower()
                record = _attempt_record(
                    attempt=attempt, started=attempt_started - started_at,
                    finished=finished - started_at, status="failed",
                    failure_class=kind, detail=exc.detail)
                telemetry.append(record)
                if kind in {"authentication", "auth", "permission"}:
                    if preparation_result is not None:
                        return preparation_result
                    return {"schema": "taskplane.repository-preparation/v1",
                            "status": "needs_user",
                            "reason": "authority_required",
                            "detail": exc.detail, "attempts": attempt,
                            "attempt_telemetry": copy.deepcopy(telemetry)}
                if kind in {"host-policy", "policy"}:
                    if preparation_result is not None:
                        return preparation_result
                    return _waiting_recovery(
                        reason="host_policy", detail=exc.detail,
                        attempts=attempt, telemetry=telemetry)
                if kind in {"external-unavailable", "external"}:
                    if preparation_result is not None:
                        return preparation_result
                    return _waiting_recovery(
                        reason="external_unavailable", detail=exc.detail,
                        attempts=attempt, telemetry=telemetry)
                if preparation_result is not None and \
                        preparation_result.get("status") == "refused":
                    return preparation_result
                if finished >= deadline:
                    return _waiting_recovery(
                        reason="acquisition_deadline", detail=exc.detail,
                        attempts=attempt, telemetry=telemetry)
                fingerprint = hashlib.sha256(
                    f"{kind}\0{exc.detail}".encode("utf-8")).hexdigest()
                fingerprints.append(fingerprint)
                failure_class = "network" if kind == "network" else "checkout"
                decision = recovery.decide_recovery(
                    failure_class=failure_class, attempt=attempt,
                    fingerprints=fingerprints,
                    max_routine_attempts=max_attempts)
                if attempt >= max_attempts and decision["status"] == "recover":
                    decision = {
                        "schema": "taskplane.recovery-decision/v1",
                        "status": "escalate", "reason": "retry_budget_exhausted",
                        "attempt": attempt, "failure_class": failure_class,
                    }
                if decision["status"] != "recover":
                    if preparation_result is not None:
                        return preparation_result
                    return _waiting_recovery(
                        reason=decision["reason"], detail=exc.detail,
                        attempts=attempt, telemetry=telemetry,
                        recovery_record=decision)

                exponential_cap = min(
                    max_backoff, base_backoff * (2 ** (attempt - 1)))
                fraction = float(random_value())
                if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
                    raise ValueError("random_value must return a number in [0, 1]")
                jittered = exponential_cap * fraction
                provider_delay = _retry_after_seconds(exc, now=wall_time())
                wait_seconds = max(jittered, provider_delay or 0.0)
                remaining = deadline - monotonic()
                record.update({
                    "backoff_seconds": round(wait_seconds, 6),
                    "backoff_source": (
                        "retry_after" if provider_delay is not None and
                        provider_delay >= jittered else "exponential_jitter"),
                    "retry_after_seconds": (round(provider_delay, 6)
                                             if provider_delay is not None
                                             else None),
                })
                if wait_seconds >= remaining:
                    return _waiting_recovery(
                        reason="acquisition_deadline",
                        detail=("retry delay cannot complete within the "
                                "repository acquisition deadline"),
                        attempts=attempt, telemetry=telemetry)
                if kind == "network" and any(token in exc.detail.lower()
                        for token in ("rpc failed", "http 400", "http/2 stream",
                                      "early eof", "remote end hung up")):
                    _ACQUISITION_HTTP11.set(True)
                if wait_seconds:
                    sleep(wait_seconds)
                continue

            finished = monotonic()
            telemetry.append(_attempt_record(
                attempt=attempt, started=attempt_started - started_at,
                finished=finished - started_at, status="ready"))
            if finished >= deadline:
                return _waiting_recovery(
                    reason="acquisition_deadline",
                    detail="repository acquisition completed after its deadline",
                    attempts=attempt, telemetry=telemetry)
            if isinstance(value, AcquisitionResult):
                metadata = dict(value.metadata)
                metadata["repository_retry"] = {
                    "schema": "taskplane.repository-retry-telemetry/v1",
                    "deadline_seconds": deadline_span,
                    "attempts": copy.deepcopy(telemetry),
                }
                value = AcquisitionResult(
                    checkout=value.checkout, base_ref=value.base_ref,
                    base=value.base, head=value.head,
                    merge_base=value.merge_base,
                    changed_files=value.changed_files, metadata=metadata)
            return {"schema": "taskplane.repository-preparation/v1",
                    "status": "ready", "value": value, "attempts": attempt,
                    "attempt_telemetry": copy.deepcopy(telemetry)}
    finally:
        _ACQUISITION_HTTP11.reset(http_token)
        _ACQUISITION_RETRY_OWNER.reset(owner_token)
        _ACQUISITION_DEADLINE.reset(deadline_token)


_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _valid_engines(plugin_family: str) -> list[tuple[tuple[int, int, int], str]]:
    family = os.path.realpath(os.path.expanduser(plugin_family))
    try:
        names = os.listdir(family)
    except PermissionError as exc:
        raise RepositoryAcquisitionError("host-policy", str(exc)) from exc
    except OSError as exc:
        raise RepositoryAcquisitionError("external-unavailable", str(exc)) \
            from exc
    roots = [family, *(os.path.join(family, name) for name in names)]
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for root in roots:
        real_root = os.path.realpath(root)
        try:
            if os.path.commonpath((family, real_root)) != family:
                continue
            manifest = os.path.join(real_root, ".codex-plugin", "plugin.json")
            engine = os.path.realpath(os.path.join(real_root, "taskplane", "tp.py"))
            if os.path.commonpath((family, engine)) != family:
                continue
            with open(manifest, encoding="utf-8") as source:
                data = json.load(source)
        except PermissionError as exc:
            raise RepositoryAcquisitionError("host-policy", str(exc)) from exc
        except (OSError, ValueError, TypeError):
            continue
        version = data.get("version") if isinstance(data, dict) else None
        match = _VERSION.fullmatch(str(version or ""))
        if not match or data.get("name") != "taskplane" or not os.path.isfile(engine):
            continue
        if real_root != family and os.path.basename(real_root) != str(version):
            continue
        candidates.append((tuple(int(value) for value in match.groups()), engine))
    return candidates


def resolve_worktree_continuity(workspace: str, *, plugin_family: str) -> dict:
    """Resolve current worktree, stable launcher, and newest valid engine."""
    family = storage.resolve_repository_family(workspace)
    launcher = family.get("launcher")
    if not launcher:
        return {"schema": "taskplane.worktree-continuity/v1",
                "status": "waiting_external", "reason": "launcher_unavailable",
                "worktree": family.get("worktree")}
    try:
        candidates = _valid_engines(plugin_family)
    except RepositoryAcquisitionError as exc:
        return {"schema": "taskplane.worktree-continuity/v1",
                "status": "waiting_host_policy" if exc.kind == "host-policy"
                else "waiting_external",
                "reason": "host_policy" if exc.kind == "host-policy"
                else "engine_unavailable", "worktree": family.get("worktree"),
                "launcher": launcher}
    if not candidates:
        return {"schema": "taskplane.worktree-continuity/v1",
                "status": "waiting_external", "reason": "engine_unavailable",
                "worktree": family.get("worktree"), "launcher": launcher}
    engine = max(candidates, key=lambda row: row[0])[1]
    return {"schema": "taskplane.worktree-continuity/v1", "status": "ready",
            "reason": "continuity_verified", "worktree": family["worktree"],
            "launcher": launcher, "engine": engine}


def _classify_failure(output: str) -> str:
    value = str(output or "").lower()
    if any(token in value for token in (
            "authentication", "permission denied", "could not read username",
            "repository not found", "http 401", "http 403")):
        return "authentication"
    if any(token in value for token in (
            "could not resolve host", "network is unreachable", "timed out",
            "connection reset", "connection refused", "temporary failure",
            "rpc failed", "http 400", "http/2 stream", "early eof",
            "remote end hung up")):
        return "network"
    return "checkout"


def validate_pickup_merge_receipt(receipt: object, *, task_id: str,
                                  revision: str) -> dict:
    """Validate historical pickup merge evidence without checkout-local state."""
    if not isinstance(receipt, Mapping) or set(receipt) != \
            _PICKUP_MERGE_FIELDS:
        raise RepositoryAcquisitionError(
            "identity", "pickup merge receipt fields are invalid")
    primary = receipt.get("primary_checkout")
    if not isinstance(primary, str) or not os.path.isabs(primary) or \
            not primary.strip():
        raise RepositoryAcquisitionError(
            "identity", "pickup merge receipt checkout is invalid")
    material = {
        name: receipt[name] for name in _PICKUP_MERGE_FIELDS
        if name != "fingerprint"
    }
    expected = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if receipt.get("schema") != "taskplane.repository-pickup-merge/v1" or \
            receipt.get("status") != "integrated" or \
            receipt.get("task_id") != task_id or \
            receipt.get("branch_tip") != revision or \
            receipt.get("fingerprint") != expected:
        raise RepositoryAcquisitionError(
            "identity", "pickup merge receipt identity is invalid")
    return dict(receipt)


def project_phase_repository_receipt(
        receipt: object, *, repository_id: str, task_id: str,
        revision: str) -> dict:
    """Project validated local merge evidence into a portable receipt.

    The incumbent merge receipt retains its absolute checkout for validation
    inside the repository boundary.  That path is deliberately absent from
    this successor projection; the projection instead binds the exact full
    receipt fingerprint, logical repository identity, task, and revision.
    """
    checked = validate_pickup_merge_receipt(
        receipt, task_id=task_id, revision=revision)
    repository = str(repository_id or "").strip()
    if not repository or not _AUTHORITY_REPOSITORY_ID.fullmatch(repository):
        raise RepositoryAcquisitionError(
            "identity", "phase repository identity is invalid")
    material = {
        "schema": PHASE_REPOSITORY_RECEIPT_SCHEMA,
        "status": "integrated",
        "repository_id": repository,
        "task_id": task_id,
        "revision": revision,
        "source_receipt_fingerprint": checked["fingerprint"],
    }
    return {**material, "fingerprint": _canonical_fingerprint(material)}


def validate_phase_repository_receipt(
        receipt: object, *, repository_id: str, task_id: str,
        revision: str, source_receipt_fingerprint: str | None = None) -> dict:
    """Validate the closed host-path-free repository receipt projection."""
    if not isinstance(receipt, Mapping) or set(receipt) != \
            _PHASE_REPOSITORY_RECEIPT_FIELDS:
        raise RepositoryAcquisitionError(
            "identity", "phase repository receipt fields are invalid")
    material = {
        field: receipt[field]
        for field in _PHASE_REPOSITORY_RECEIPT_FIELDS - {"fingerprint"}
    }
    expected = _canonical_fingerprint(material)
    if receipt.get("schema") != PHASE_REPOSITORY_RECEIPT_SCHEMA or \
            receipt.get("status") != "integrated" or \
            receipt.get("repository_id") != repository_id or \
            receipt.get("task_id") != task_id or \
            receipt.get("revision") != revision or \
            not isinstance(receipt.get("source_receipt_fingerprint"), str) or \
            not _AUTHORITY_FINGERPRINT.fullmatch(
                str(receipt.get("source_receipt_fingerprint"))) or \
            (source_receipt_fingerprint is not None and
             receipt.get("source_receipt_fingerprint") !=
             source_receipt_fingerprint) or \
            receipt.get("fingerprint") != expected:
        raise RepositoryAcquisitionError(
            "identity", "phase repository receipt identity is invalid")
    return dict(receipt)


# The Build submit adapter uses this shorter contract-oriented name.
project_repository_receipt = project_phase_repository_receipt


class RepositoryManager:
    """Own mirrors/worktrees outside report directories."""

    def __init__(self, *, home: str | None = None,
                 git_runner: GitRunner | None = None):
        self.home = storage.taskplane_home(home)
        self.git_runner = git_runner

    def _git_port(self) -> GitRunner:
        if self.git_runner is not None:
            return self.git_runner
        manager = self

        class _IncumbentGitRunner:
            def run(self, args: Sequence[str], *, cwd=None) -> GitResult:
                argv = ["git", *args]
                output = (manager._fetch(argv) if "fetch" in args
                          else manager._run(argv, cwd=cwd))
                return GitResult(0, output, "")

        return _IncumbentGitRunner()

    def _run(self, argv: list[str], *, cwd: str | None = None,
             timeout: int = 600) -> str:
        effective_timeout = _acquisition_timeout(float(timeout))
        try:
            result = subprocess.run(
                argv, cwd=cwd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", timeout=effective_timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RepositoryAcquisitionError(
                "network", "command exceeded the repository acquisition "
                f"deadline after {effective_timeout:g}s") from exc
        except OSError as exc:
            raise RepositoryAcquisitionError(
                "checkout", f"could not execute {argv[0]}: {exc}") from exc
        output = (result.stdout or "").strip()
        if result.returncode:
            raise RepositoryAcquisitionError(
                _classify_failure(output), output[-1200:] or
                f"{argv[0]} exited {result.returncode}")
        return output

    def _materialize_review_diff(self, checkout: str, merge_base: str,
                                 head: str) -> None:
        """Hydrate every blob the immutable review patch will require.

        Existing mirrors may be partial clones even when the checkout is not
        marked shallow. Name-only diff succeeds without blobs, so it cannot
        prove the later review is offline-capable. Rendering the binary patch
        to DEVNULL forces Git's promisor remote to resolve the exact content
        while repository preflight can still turn auth/network failures into
        a structured user action.
        """
        argv = ["git", "diff", "--no-ext-diff", "--no-textconv",
                "--binary", merge_base, head, "--"]
        effective_timeout = _acquisition_timeout(600.0)
        try:
            result = subprocess.run(
                argv, cwd=checkout, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", timeout=effective_timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RepositoryAcquisitionError(
                "network", "review diff hydration exceeded the repository "
                f"acquisition deadline after {effective_timeout:g}s") \
                from exc
        except OSError as exc:
            raise RepositoryAcquisitionError(
                "checkout", f"could not execute git diff: {exc}") from exc
        if result.returncode:
            output = str(result.stderr or "").strip()
            raise RepositoryAcquisitionError(
                _classify_failure(output), output[-1200:] or
                f"git diff hydration exited {result.returncode}")

    def _fetch(self, argv: list[str]) -> str:
        """Run exactly one fetch attempt chosen by the sole retry owner."""
        selected = argv
        if _ACQUISITION_HTTP11.get():
            selected = [argv[0], "-c", "http.version=HTTP/1.1", *argv[1:]]
        return self._run(selected)

    @staticmethod
    def _remote_url(identity: storage.RepositoryIdentity) -> str:
        if identity.kind != "hosted" or not identity.host or \
                not identity.owner:
            raise RepositoryAcquisitionError(
                "identity", "managed remote checkout needs a hosted identity")
        return f"https://{identity.host}/{identity.owner}/{identity.name}.git"

    def _metadata(self, target: dict) -> dict:
        spec = str(target.get("spec") or "")
        argv = ["gh", "pr", "view"]
        if target.get("number"):
            argv.append(str(int(target["number"])))
            if target.get("owner") and target.get("repo"):
                argv.extend(["--repo",
                             f"{target['owner']}/{target['repo']}"])
        else:
            argv.append(spec)
        argv.extend(["--json", "baseRefName,headRefOid,title,url"])
        raw = self._run(argv)
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise RepositoryAcquisitionError(
                "metadata", "GitHub returned invalid pull-request metadata") \
                from exc
        if not isinstance(value, dict) or not value.get("baseRefName") or \
                not value.get("headRefOid"):
            raise RepositoryAcquisitionError(
                "metadata", "pull-request metadata lacks base/head identity")
        return value

    def _ensure_mirror(self, identity: storage.RepositoryIdentity,
                       layout: storage.StorageLayout) -> None:
        remote = self._remote_url(identity)
        os.makedirs(layout.checkout_root, exist_ok=True)
        if os.path.isdir(layout.mirror_path):
            valid = self._run(["git", "--git-dir", layout.mirror_path,
                               "rev-parse", "--is-bare-repository"])
            if valid != "true":
                raise RepositoryAcquisitionError(
                    "checkout", "managed mirror is not a bare repository")
        else:
            temporary = tempfile.mkdtemp(
                prefix=".mirror-acquire-", dir=layout.checkout_root)
            candidate = os.path.join(temporary, "mirror.git")
            try:
                # PR review never needs every branch/tag/ref. An empty bare
                # mirror lets acquire_pr fetch only its base and PR head;
                # `clone --mirror` made large public repositories download
                # their complete ref universe and fail with GitHub HTTP 400.
                self._run(["git", "init", "--bare", candidate])
                self._run(["git", "--git-dir", candidate, "remote", "add",
                           "origin", remote])
                os.replace(candidate, layout.mirror_path)
            finally:
                # This directory was minted above and can contain only an
                # incomplete replaceable mirror, never a user checkout.
                shutil.rmtree(temporary, ignore_errors=True)
            return
        self._run(["git", "--git-dir", layout.mirror_path, "remote",
                   "set-url", "origin", remote])

    def _acquisition_lock(self, layout: storage.StorageLayout):
        return tp.file_lock(os.path.join(layout.checkout_root, "acquisition"),
                            timeout=_acquisition_timeout(30.0))

    def merge_registered_task(self, primary_checkout: str, *, task_id: str,
                              run_id: str | None = None) -> dict:
        """Ordinary orchestrator merge followed by the durable receipt data.

        Cleanup is intentionally not performed here.  Callers must persist
        the returned receipt before invoking the cleanup transaction.
        """
        import worktree_cleanup

        primary = os.path.realpath(primary_checkout)
        registration = storage.load_task_worktree_registration(primary, task_id)
        if registration is None:
            raise RepositoryAcquisitionError(
                "identity", "task worktree has no managed registration")
        primary_ref = self._run(
            ["git", "symbolic-ref", "--quiet", "HEAD"], cwd=primary)
        if not primary_ref.startswith("refs/heads/"):
            raise RepositoryAcquisitionError(
                "identity", "primary branch is detached or ambiguous")
        try:
            registration = storage.refresh_task_worktree_tip(primary, task_id)
        except storage.StorageIdentityError as exc:
            raise RepositoryAcquisitionError("identity", str(exc)) from exc
        # Ordinary Git merge semantics protect tracked local changes that
        # would be overwritten. Loop-owned plan/spec artifacts may remain
        # dirty in the primary checkout and are not cleanup candidates.
        # Requiring a globally clean primary would deadlock every normal
        # governed loop before its task branch could land. Worker cleanliness
        # is likewise a cleanup eligibility fact: governance evidence may be
        # untracked after Evaluate and must cause preservation, not suppress a
        # valid merge receipt.
        self._run(["git", "merge", "--no-edit",
                   registration["branch_ref"]], cwd=primary)
        return worktree_cleanup.record_merge_receipt(
            primary, task_id=task_id,
            run_id=run_id or registration.get("run_id"))

    def owned_worktree_resource(self, primary_checkout: str, *, task_id: str,
                                merge_receipt: Mapping,
                                lifecycle: Mapping) -> dict:
        """Adapt exact repository registration into cleanup manifest facts."""
        primary = os.path.realpath(primary_checkout)
        registration = storage.load_task_worktree_registration(primary, task_id)
        if registration is None:
            raise RepositoryAcquisitionError(
                "identity", "task worktree has no managed registration")
        managed = os.path.abspath(str(registration.get("path") or ""))
        root, relative = os.path.split(managed)
        if (not root or not relative or merge_receipt.get("managed_path") != managed or
                merge_receipt.get("receipt_id") is None):
            raise RepositoryAcquisitionError(
                "identity", "task worktree cleanup identity is ambiguous")
        try:
            import worktree_cleanup
        except ImportError:
            from taskplane import worktree_cleanup
        stable_identity = worktree_cleanup.resource_identity(
            dict(merge_receipt), lifecycle=dict(lifecycle))
        return {
            "kind": "worktree",
            "containment_root": root,
            "relative_name": relative,
            "stable_identity": stable_identity,
            "policy": {"merge_receipt": copy.deepcopy(dict(merge_receipt)),
                       "lifecycle": copy.deepcopy(dict(lifecycle))},
        }

    def register_owned_worktree(
            self, manifest: str, primary_checkout: str, *, task_id: str,
            merge_receipt: Mapping, lifecycle: Mapping,
            creator_nonce: str, dependencies: Sequence[str] = (),
            evidence_refs: Sequence[str] = (
                "terminal-state", "handoff", "publication-replay")) -> str:
        """Register and activate the incumbent exact worktree adapter.

        Worktree creation owners call this at their reserve-before-use seam;
        cleanup remains delegated to :mod:`worktree_cleanup` and never uses
        path or branch-name inference.
        """
        try:
            from taskplane import owned_cleanup
        except ImportError:
            import owned_cleanup
        descriptor = self.owned_worktree_resource(
            primary_checkout, task_id=task_id,
            merge_receipt=merge_receipt, lifecycle=lifecycle)
        resource_id = owned_cleanup.reserve_resource(
            manifest, creator_nonce=creator_nonce,
            evidence_refs=evidence_refs, dependencies=dependencies,
            **descriptor)
        owned_cleanup.activate_resource(manifest, resource_id)
        return resource_id

    def accept_pickup_revision(self, primary_checkout: str, *, task_id: str,
                               revision: str) -> dict:
        """Accept an exact already-current revision at the merge boundary."""
        primary = os.path.realpath(primary_checkout)
        observed = self._run(["git", "rev-parse", "HEAD"], cwd=primary)
        if observed != revision:
            raise RepositoryAcquisitionError(
                "identity", "pickup revision differs from repository HEAD")
        material = {
            "schema": "taskplane.repository-pickup-merge/v1",
            "status": "integrated", "task_id": task_id,
            "primary_checkout": primary, "branch_tip": revision,
        }
        return {**material, "fingerprint": hashlib.sha256(json.dumps(
            material, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()}

    def _owned_acquisition(self, acquire: Callable[[], AcquisitionResult]) \
            -> AcquisitionResult:
        """Apply the one acquisition policy unless an outer owner is active."""
        if _ACQUISITION_RETRY_OWNER.get():
            return acquire()
        recovered = acquire_with_recovery(acquire)
        if recovered["status"] == "ready":
            return recovered["value"]
        reason = str(recovered.get("reason") or
                     recovered.get("reason_code") or "repeated_failure")
        kind = {
            "authority_required": "authentication",
            "host_policy": "host-policy",
            "external_unavailable": "network",
        }.get(reason, "network")
        preparation = recovered if "reason_code" in recovered else None
        raise RepositoryAcquisitionError(
            kind, str(recovered.get("detail") or reason),
            preparation_result=preparation, recovery_result=recovered)

    def acquire_pr(self, identity: storage.RepositoryIdentity,
                   target: dict) -> AcquisitionResult:
        """Acquire a pull request through the sole bounded retry owner."""
        return self._owned_acquisition(
            lambda: self._acquire_pr_once(identity, target))

    def _acquire_pr_once(self, identity: storage.RepositoryIdentity,
                         target: dict) -> AcquisitionResult:
        metadata = self._metadata(target)
        number = int(target["number"])
        base_name = str(metadata["baseRefName"])
        expected_head = str(metadata["headRefOid"])
        layout = storage.resolve_layout(identity, home=self.home,
                                        run_id="acquisition")
        try:
            with self._acquisition_lock(layout):
                self._ensure_mirror(identity, layout)
                head_ref = f"refs/taskplane/pr/{number}/head"
                base_ref = f"refs/remotes/origin/{base_name}"
                self._fetch([
                    "git", "--git-dir", layout.mirror_path, "fetch", "origin",
                    f"+refs/heads/{base_name}:{base_ref}",
                    f"+refs/pull/{number}/head:{head_ref}"])
                head = self._run(["git", "--git-dir", layout.mirror_path,
                                  "rev-parse", head_ref])
                base = self._run(["git", "--git-dir", layout.mirror_path,
                                  "rev-parse", base_ref])
                if not (head.startswith(expected_head) or
                        expected_head.startswith(head)):
                    raise RepositoryAcquisitionError(
                        "identity", "fetched pull-request head differs from "
                        "GitHub metadata; retry after the remote settles")
                merge_base = self._run([
                    "git", "--git-dir", layout.mirror_path, "merge-base",
                    base, head])
                checkout = os.path.join(layout.worktree_root,
                                        f"pr-{number}-{head[:12]}")
                os.makedirs(layout.worktree_root, exist_ok=True)
                if os.path.isdir(checkout):
                    existing = self._run(
                        ["git", "rev-parse", "HEAD"], cwd=checkout)
                    if existing != head:
                        raise RepositoryAcquisitionError(
                            "identity", f"managed checkout {checkout} has moved")
                else:
                    self._run(["git", "--git-dir", layout.mirror_path,
                               "worktree", "add", "--detach", checkout, head])
                self._materialize_review_diff(checkout, merge_base, head)
                changed = self._run([
                    "git", "diff", "--name-only", merge_base, "HEAD"],
                    cwd=checkout)
        except tp.StateError as exc:
            raise RepositoryAcquisitionError(
                "checkout", f"managed repository is busy: {exc}") from None
        return AcquisitionResult(
            checkout=os.path.realpath(checkout), base_ref=base_ref, base=base,
            head=head, merge_base=merge_base,
            changed_files=tuple(sorted(line for line in changed.splitlines()
                                       if line.strip())),
            metadata={"title": str(metadata.get("title") or ""),
                      "url": str(metadata.get("url") or target.get("spec") or
                                 "")})

    def acquire_repository(self, identity: storage.RepositoryIdentity,
                           target: dict, *, run_id: str = "acquisition",
                           attempt: int = 1,
                           predecessor_result_fingerprint: str | None = None,
                           prior_result: dict | None = None) \
            -> AcquisitionResult:
        """Acquire a hosted repository through the sole bounded retry owner."""
        if _ACQUISITION_RETRY_OWNER.get():
            return self._acquire_repository_once(
                identity, target, run_id=run_id, attempt=attempt,
                predecessor_result_fingerprint=
                predecessor_result_fingerprint, prior_result=prior_result)
        current_attempt = attempt
        current_predecessor = predecessor_result_fingerprint
        current_prior = prior_result

        def acquire_once() -> AcquisitionResult:
            nonlocal current_attempt, current_predecessor, current_prior
            try:
                return self._acquire_repository_once(
                    identity, target, run_id=run_id, attempt=current_attempt,
                    predecessor_result_fingerprint=current_predecessor,
                    prior_result=current_prior)
            except RepositoryAcquisitionError as exc:
                observed = exc.preparation_result
                if observed is not None and "attempt" in observed and \
                        "fingerprint" in observed:
                    current_attempt = int(observed["attempt"]) + 1
                    current_predecessor = str(observed["fingerprint"])
                    current_prior = observed
                raise

        return self._owned_acquisition(acquire_once)

    def _acquire_repository_once(
            self, identity: storage.RepositoryIdentity, target: dict, *,
            run_id: str = "acquisition", attempt: int = 1,
            predecessor_result_fingerprint: str | None = None,
            prior_result: dict | None = None) -> AcquisitionResult:
        layout = storage.resolve_layout(identity, home=self.home,
                                        run_id="acquisition")
        try:
            with self._acquisition_lock(layout):
                self._ensure_mirror(identity, layout)
                remote = self._remote_url(identity)
                locator_fingerprint = _canonical_fingerprint({
                    "home": layout.home,
                    "repository_key": layout.repository_key,
                    "mirror_path": os.path.realpath(layout.mirror_path),
                    "worktree_root": os.path.realpath(layout.worktree_root),
                    "run_id": run_id,
                })
                request = {
                    "schema": _PREPARATION_REQUEST_SCHEMA,
                    "operation_id": "prepare-" + hashlib.sha256(
                        f"{run_id}\0{identity.repo_id}".encode("utf-8")
                    ).hexdigest()[:24],
                    "run_id": run_id,
                    "target": {
                        "kind": "repository",
                        "repository_id": identity.repo_id,
                        "remote": remote,
                        "requested_ref": target.get("requested_ref"),
                    },
                    "workspace_locator_fingerprint": locator_fingerprint,
                    "attempt": attempt,
                    "predecessor_result_fingerprint":
                        predecessor_result_fingerprint,
                }
                preparation = prepare(
                    request, mirror_path=layout.mirror_path,
                    worktree_root=layout.worktree_root,
                    git_runner=self._git_port(), prior_result=prior_result)
        except tp.StateError as exc:
            raise RepositoryAcquisitionError(
                "checkout", f"managed repository is busy: {exc}") from None
        if preparation["status"] != "ready":
            kind = {
                "authority_required": "authentication",
                "host_policy": "host-policy",
                "external_unavailable": "network",
                "repeated_failure": "checkout",
            }.get(preparation["reason_code"], "identity")
            raise RepositoryAcquisitionError(
                kind, preparation["reason_code"],
                preparation_result=preparation)
        head = preparation["resolved_sha"]
        checkout = preparation["checkout"]
        return AcquisitionResult(
            checkout=os.path.realpath(checkout),
            base_ref=preparation["remote_default_ref"], base=head,
            head=head, merge_base=head, changed_files=(),
            metadata={"url": str(target.get("spec") or identity.remote or
                                  ""),
                      "repository_preparation": preparation})
