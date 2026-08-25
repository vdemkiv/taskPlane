"""Deterministic managed repository and GitHub pull-request acquisition."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, Mapping

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


class RepositoryAcquisitionError(RuntimeError):
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = str(kind)
        self.detail = str(detail)


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


def acquire_with_recovery(acquire: Callable[[], object], *,
                          max_attempts: int = 3) -> dict:
    """Run repository preparation under bounded consolidated authority.

    Authentication is a genuine authority boundary.  Host policy and an
    unavailable external system wait for their state to change.  Routine
    transfer/checkout failures retry locally and never manufacture approval.
    """
    fingerprints: list[str] = []
    attempt = 0
    while True:
        attempt += 1
        try:
            value = acquire()
        except RepositoryAcquisitionError as exc:
            kind = str(exc.kind or "checkout").lower()
            if kind in {"authentication", "auth", "permission"}:
                return {"schema": "taskplane.repository-preparation/v1",
                        "status": "needs_user", "reason": "authority_required",
                        "detail": exc.detail, "attempts": attempt}
            if kind in {"host-policy", "policy"}:
                return {"schema": "taskplane.repository-preparation/v1",
                        "status": "waiting", "reason": "host_policy",
                        "detail": exc.detail, "attempts": attempt}
            if kind in {"external-unavailable", "external"}:
                return {"schema": "taskplane.repository-preparation/v1",
                        "status": "waiting", "reason": "external_unavailable",
                        "detail": exc.detail, "attempts": attempt}
            fingerprint = hashlib.sha256(
                f"{kind}\0{exc.detail}".encode("utf-8")).hexdigest()
            fingerprints.append(fingerprint)
            decision = recovery.decide_recovery(
                failure_class="network" if kind == "network" else "checkout",
                attempt=attempt, fingerprints=fingerprints,
                max_routine_attempts=max_attempts)
            if decision["status"] == "recover":
                continue
            return {"schema": "taskplane.repository-preparation/v1",
                    "status": "waiting", "reason": decision["reason"],
                    "detail": exc.detail, "attempts": attempt,
                    "recovery": decision}
        return {"schema": "taskplane.repository-preparation/v1",
                "status": "ready", "value": value, "attempts": attempt}


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


class RepositoryManager:
    """Own mirrors/worktrees outside report directories."""

    def __init__(self, *, home: str | None = None):
        self.home = storage.taskplane_home(home)

    def _run(self, argv: list[str], *, cwd: str | None = None,
             timeout: int = 600) -> str:
        try:
            result = subprocess.run(
                argv, cwd=cwd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RepositoryAcquisitionError(
                "network", f"command timed out after {timeout}s") from exc
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
        try:
            result = subprocess.run(
                argv, cwd=checkout, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", timeout=600, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RepositoryAcquisitionError(
                "network", "review diff hydration timed out after 600s") \
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
        """Run one targeted fetch with a bounded HTTP/1.1 transport fallback."""
        try:
            return self._run(argv)
        except RepositoryAcquisitionError as exc:
            detail = exc.detail.lower()
            if not any(token in detail for token in (
                    "rpc failed", "http 400", "http/2 stream",
                    "early eof", "remote end hung up")):
                raise
            fallback = [argv[0], "-c", "http.version=HTTP/1.1", *argv[1:]]
            return self._run(fallback)

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
                            timeout=30.0)

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

    def acquire_pr(self, identity: storage.RepositoryIdentity,
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
                           target: dict) -> AcquisitionResult:
        """Acquire the hosted repository's default HEAD without a PR."""
        layout = storage.resolve_layout(identity, home=self.home,
                                        run_id="acquisition")
        try:
            with self._acquisition_lock(layout):
                self._ensure_mirror(identity, layout)
                self._fetch(["git", "--git-dir", layout.mirror_path,
                             "fetch", "--prune", "origin"])
                head = self._run(["git", "--git-dir", layout.mirror_path,
                                  "rev-parse", "HEAD"])
                checkout = os.path.join(
                    layout.worktree_root, f"repo-{head[:12]}")
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
        except tp.StateError as exc:
            raise RepositoryAcquisitionError(
                "checkout", f"managed repository is busy: {exc}") from None
        return AcquisitionResult(
            checkout=os.path.realpath(checkout), base_ref="", base=head,
            head=head, merge_base=head, changed_files=(),
            metadata={"url": str(target.get("spec") or identity.remote or
                                  "")})
