"""Resumable repository preconditions that finish before governance starts."""
from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import uuid

import repository
import run_store
import storage
import target as target_module


class PreflightError(RuntimeError):
    pass


class RepositoryPreflight:
    """Prepare local/remote source and persist actionable user pauses."""

    def __init__(self, *, home: str | None = None, tools_provider=None,
                 acquirer=None, action_runner=None):
        self.store = run_store.RunStore(home=home)
        self.tools_provider = tools_provider or target_module.tools
        self.acquirer = acquirer or repository.RepositoryManager(home=home)
        self.action_runner = action_runner or self._run_action

    @staticmethod
    def _run_action(argv: list[str]) -> dict:
        if not argv or not all(isinstance(value, str) and value
                               for value in argv):
            return {"returncode": 2, "output": "approved action is empty"}
        try:
            completed = subprocess.run(
                list(argv), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", timeout=900, check=False)
        except subprocess.TimeoutExpired:
            return {"returncode": 124,
                    "output": "approved action timed out after 900 seconds"}
        except OSError as exc:
            return {"returncode": 127,
                    "output": f"approved action could not start: {exc}"}
        return {"returncode": int(completed.returncode),
                "output": str(completed.stdout or "")[-1600:]}

    @staticmethod
    def _pr_identity(parsed: dict) -> storage.RepositoryIdentity:
        if not all(parsed.get(key) for key in ("host", "owner", "repo")):
            raise PreflightError(
                "a numeric pull-request target needs a repository context")
        remote = (f"https://{parsed['host']}/{parsed['owner']}/"
                  f"{parsed['repo']}.git")
        return storage.identity_from_remote(remote)

    def _load_or_create(self, identity: storage.RepositoryIdentity, *,
                        run_id: str, checkout: str, host: dict,
                        target: dict) -> dict:
        try:
            current = self.store.load(run_id)
        except run_store.RunStoreError:
            return self.store.create(
                identity, run_id=run_id, checkout=checkout, host=host,
                target=target)
        recorded = (current.get("repository") or {}).get("repo_id")
        if recorded != identity.repo_id:
            raise PreflightError(
                f"run {run_id} belongs to {recorded}, not {identity.repo_id}")
        return current

    @staticmethod
    def _action(run_id: str, *, kind: str, prompt: str, detail: str,
                command_argv: list[str] | None = None,
                command_argv_sequence: list[list[str]] | None = None,
                choices: tuple[str, ...] = ("approve", "cancel")) -> dict:
        material = f"{run_id}\0{kind}\0{detail}".encode("utf-8")
        return {
            "schema": "taskplane.user-action/v1",
            "action_id": hashlib.sha256(material).hexdigest()[:20],
            "kind": kind,
            "prompt": prompt,
            "detail": detail,
            "command_argv": list(command_argv or []),
            "command_argv_sequence": [list(argv) for argv in
                                      (command_argv_sequence or [])],
            "choices": list(choices),
        }

    def _needs_user(self, run_id: str, manifest: dict, action: dict) -> dict:
        updated = self.store.commit(
            run_id, expected_revision=int(manifest["revision"]),
            changes={"status": "awaiting_user",
                     "preflight": {"status": "needs_user",
                                   "pending_action": action}})
        return {"schema": "taskplane.preflight/v1", "run_id": run_id,
                "status": "needs_user", "action": action,
                "revision": updated["revision"]}

    def prepare(self, spec: str, *, workspace: str, host: dict,
                run_id: str | None = None) -> dict:
        run = str(run_id or uuid.uuid4().hex)
        parsed = target_module.parse(spec)
        source_workspace = workspace
        candidate = os.path.realpath(os.path.abspath(os.path.expanduser(
            str(spec or "")))) if str(spec or "").strip() else None
        if candidate and os.path.isdir(candidate):
            source_workspace = candidate
            parsed = {"kind": "local", "spec": candidate}
        remote_identity = None
        if parsed.get("kind") != "pr":
            try:
                remote_identity = storage.identity_from_remote(spec)
            except ValueError:
                remote_identity = None
            if remote_identity is not None:
                parsed = {
                    "kind": "repository", "spec": str(spec),
                    "host": remote_identity.host,
                    "owner": remote_identity.owner,
                    "repo": remote_identity.name,
                }
        tools = self.tools_provider()
        if parsed.get("kind") in {"pr", "repository"}:
            identity = remote_identity or self._pr_identity(parsed)
            layout = storage.resolve_layout(identity, home=self.store.home,
                                            run_id=run)
            manifest = self._load_or_create(
                identity, run_id=run, checkout=layout.worktree_root,
                host=host, target=parsed)
            persisted_target = manifest.get("target") or {}
            persisted_checkout = str(
                (manifest.get("repository") or {}).get("checkout") or "")
            if manifest.get("status") == "ready" and \
                    (manifest.get("preflight") or {}).get("status") == \
                    "ready" and persisted_target.get("ok") is True and \
                    os.path.isdir(persisted_checkout):
                # A ready run is pinned evidence, not a request to contact
                # GitHub again. The downstream target preflight re-verifies
                # the local head/diff before governance starts.
                return {
                    "schema": "taskplane.preflight/v1", "run_id": run,
                    "status": "ready", "checkout": persisted_checkout,
                    "target": persisted_target,
                    "revision": int(manifest["revision"]),
                }
            if not (tools.get("git") or {}).get("present"):
                return self._needs_user(run, manifest, self._action(
                    run, kind="install_git",
                    prompt="Git is required. Install it, then continue this run.",
                    detail="git executable is unavailable",
                    choices=("retry", "cancel")))
            gh = tools.get("gh") or {}
            if parsed.get("kind") == "pr" and not gh.get("present"):
                command = shlex.split(target_module.install_hint())
                return self._needs_user(run, manifest, self._action(
                    run, kind="install_gh",
                    prompt=("GitHub CLI is required for authenticated PR "
                            "metadata. Approve installation and continue."),
                    detail="gh executable is unavailable",
                    command_argv=command))
            if parsed.get("kind") == "pr" and \
                    gh.get("authenticated") is not True:
                return self._needs_user(run, manifest, self._action(
                    run, kind="authenticate_gh",
                    prompt=("GitHub authentication is required. Sign in, then "
                            "taskPlane will resume this same run."),
                    detail="gh is not authenticated",
                    command_argv=["gh", "auth", "login", "--web"]))
            try:
                if parsed.get("kind") == "pr":
                    acquired = self.acquirer.acquire_pr(identity, parsed)
                else:
                    acquired = self.acquirer.acquire_repository(
                        identity, parsed)
            except repository.RepositoryAcquisitionError as exc:
                if exc.kind == "authentication":
                    command = (["gh", "auth", "login", "--web"]
                               if gh.get("present") else [])
                    return self._needs_user(run, manifest, self._action(
                        run, kind="authenticate_repository",
                        prompt=("Repository authentication is required. "
                                "Sign in or authorize access, then taskPlane "
                                "will resume this same run."),
                        detail=exc.detail, command_argv=command,
                        choices=("approve", "retry", "cancel")))
                if exc.kind == "network":
                    return self._needs_user(run, manifest, self._action(
                        run, kind="retry_acquisition",
                        prompt=("Repository transfer failed. taskPlane "
                                "already limited the fetch to the requested "
                                "target and tried its compatible transport; "
                                "retry or cancel."),
                        detail=exc.detail, choices=("retry", "cancel")))
                return self._needs_user(run, manifest, self._action(
                    run, kind="retry_acquisition",
                    prompt=("Repository checkout failed. Retry or cancel."),
                    detail=exc.detail,
                    choices=("retry", "cancel")))
            target = {
                "ok": True, "root": acquired.checkout,
                "origin": identity.remote or
                f"https://{identity.repo_id}.git",
                "head": acquired.head, "branch": None, "dirty": [],
                "shallow": False, "target": parsed,
                "base_ref": acquired.base_ref, "base": acquired.base,
                "merge_base": acquired.merge_base,
                "changed_files": list(acquired.changed_files),
                "metadata": dict(acquired.metadata),
            }
            target["fingerprint"] = target_module.fingerprint(target)
            layout = storage.resolve_layout(
                identity, home=self.store.home, run_id=run)
            try:
                storage.write_workspace_locator(
                    acquired.checkout, identity=identity, layout=layout,
                    run_id=run)
            except (OSError, storage.StorageIdentityError) as exc:
                return self._needs_user(run, manifest, self._action(
                    run, kind="authorize_storage_root",
                    prompt=("taskPlane needs permission to bind the managed "
                            "checkout to its external run storage. Approve "
                            "access, then retry this run."),
                    detail=f"{exc.__class__.__name__}: {exc}",
                    choices=("retry", "cancel")))
            updated = self.store.commit(
                run, expected_revision=int(manifest["revision"]),
                changes={
                    "status": "ready",
                    "repository": {"checkout": acquired.checkout},
                    "target": target,
                    "preflight": {
                        "status": "ready", "pending_action": None,
                        "completed_steps": [
                            "resolve", "authenticate", "acquire", "fetch",
                            "checkout", "verify"]}})
            return {"schema": "taskplane.preflight/v1", "run_id": run,
                    "status": "ready", "checkout": acquired.checkout,
                    "target": target, "revision": updated["revision"]}

        identity = storage.resolve_repository_identity(source_workspace)
        manifest = self._load_or_create(
            identity, run_id=run, checkout=source_workspace, host=host,
            target=parsed)
        if not (tools.get("git") or {}).get("present"):
            return self._needs_user(run, manifest, self._action(
                run, kind="install_git",
                prompt="Git is required. Install it, then continue this run.",
                detail="git executable is unavailable",
                choices=("retry", "cancel")))
        pinned = target_module.pin(source_workspace, target=parsed)
        if not pinned.get("ok"):
            return self._needs_user(run, manifest, self._action(
                run, kind="initialize_or_commit_git",
                prompt=("This folder needs a Git repository and baseline "
                        "commit. Approve taskPlane to initialize and commit "
                        "the current files, or cancel."),
                detail=str(pinned.get("reason") or "Git baseline missing"),
                command_argv_sequence=[
                    ["git", "-C", source_workspace, "init"],
                    ["git", "-C", source_workspace, "add", "-A"],
                    ["git", "-C", source_workspace, "-c",
                     "user.name=taskPlane", "-c",
                     "user.email=taskplane@local", "commit", "--allow-empty",
                     "-m", "Initialize repository for taskPlane"],
                ],
                choices=("initialize", "cancel")))
        checkout = os.path.realpath(source_workspace)
        layout = storage.resolve_layout(
            identity, home=self.store.home, run_id=run)
        try:
            storage.write_workspace_locator(
                checkout, identity=identity, layout=layout, run_id=run)
        except (OSError, storage.StorageIdentityError) as exc:
            return self._needs_user(run, manifest, self._action(
                run, kind="authorize_storage_root",
                prompt=("taskPlane needs permission to bind this checkout "
                        "to its external run storage. Approve access, then "
                        "retry this run."),
                detail=f"{exc.__class__.__name__}: {exc}",
                choices=("retry", "cancel")))
        updated = self.store.commit(
            run, expected_revision=int(manifest["revision"]),
            changes={"status": "ready", "repository": {"checkout": checkout},
                     "target": pinned,
                     "preflight": {"status": "ready",
                                   "pending_action": None,
                                   "completed_steps": [
                                       "resolve", "checkout", "verify"]}})
        return {"schema": "taskplane.preflight/v1", "run_id": run,
                "status": "ready", "checkout": checkout,
                "target": pinned, "revision": updated["revision"]}

    def authorize(self, run_id: str, *, action_id: str, response: str,
                  approved_by: str) -> dict:
        manifest = self.store.load(run_id)
        action = (manifest.get("preflight") or {}).get("pending_action")
        if not isinstance(action, dict) or action.get("action_id") != action_id:
            raise PreflightError("pending user action does not match")
        if response not in action.get("choices", []):
            raise PreflightError(f"response is not allowed: {response}")
        if response == "cancel":
            updated = self.store.commit(
                run_id, expected_revision=int(manifest["revision"]),
                changes={"status": "cancelled",
                         "preflight": {"status": "cancelled",
                                       "pending_action": None,
                                       "authorization": {
                                           "response": response,
                                           "by": approved_by}}})
            return {"schema": "taskplane.preflight/v1", "run_id": run_id,
                    "status": "cancelled", "revision": updated["revision"]}
        updated = self.store.commit(
            run_id, expected_revision=int(manifest["revision"]),
            changes={"status": "preflight",
                     "preflight": {"status": "authorized",
                                   "authorization": {"response": response,
                                                     "by": approved_by}}})
        return {"schema": "taskplane.preflight/v1", "run_id": run_id,
                "status": "authorized",
                "next": "execute_action_then_retry",
                "command_argv": (list(action.get("command_argv") or [])
                                 if response != "retry" else []),
                "command_argv_sequence": (
                    [list(argv) for argv in (action.get(
                        "command_argv_sequence") or [])]
                    if response != "retry" else []),
                "revision": updated["revision"]}

    def resume(self, run_id: str, *, action_id: str, response: str,
               approved_by: str) -> dict:
        """Apply one explicit human decision and resume the same run.

        The command is stored by the engine before the pause and is executed
        as argv, never through a shell.  A failed command returns another
        actionable pause; it cannot strand the caller in a traceback or
        activate a governance contract.
        """
        authorized = self.authorize(
            run_id, action_id=action_id, response=response,
            approved_by=approved_by)
        if authorized["status"] == "cancelled":
            return authorized
        command = list(authorized.get("command_argv") or [])
        commands = [list(argv) for argv in
                    (authorized.get("command_argv_sequence") or [])]
        if command:
            commands.insert(0, command)
        for current_command in commands:
            outcome = self.action_runner(current_command)
            if int(outcome.get("returncode", 1)) != 0:
                current = self.store.load(run_id)
                prior = (current.get("preflight") or {}).get(
                    "pending_action") or {}
                detail = str(outcome.get("output") or
                             "approved action failed")[-1200:]
                action = self._action(
                    run_id, kind=str(prior.get("kind") or "retry_action"),
                    prompt=(str(prior.get("prompt") or
                                "The prerequisite still needs your input.")),
                    detail=detail, command_argv=current_command,
                    choices=tuple(prior.get("choices") or
                                  ("approve", "cancel")))
                return self._needs_user(run_id, current, action)
        manifest = self.store.load(run_id)
        target = manifest.get("target") or {}
        spec = str(target.get("spec") or "")
        checkout = str((manifest.get("repository") or {}).get("checkout")
                       or os.getcwd())
        return self.prepare(
            spec, workspace=checkout, host=dict(manifest.get("host") or {}),
            run_id=run_id)
