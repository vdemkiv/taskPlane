"""Deterministic managed repository and GitHub pull-request acquisition."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import shutil
import subprocess
import tempfile

import storage
import taskplane_lite as tp


class RepositoryAcquisitionError(RuntimeError):
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = str(kind)
        self.detail = str(detail)


@dataclass(frozen=True)
class AcquisitionResult:
    checkout: str
    base_ref: str
    base: str
    head: str
    merge_base: str
    changed_files: tuple[str, ...]
    metadata: dict


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
