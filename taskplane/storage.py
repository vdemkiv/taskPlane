"""Canonical repository identity and hybrid taskPlane storage layout.

Source checkouts, durable knowledge, replaceable caches, and run artifacts
have different lifecycles.  This module gives them one owner without putting
source code under a report directory or keying one repository by every clone
path it happens to use.
"""
from __future__ import annotations

if __package__:
    from .primitives import atomic_json as _atomic_json
else:
    from primitives import atomic_json as _atomic_json

from collections.abc import MutableMapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlsplit


_SCP_REMOTE = re.compile(
    r"^(?:[^@/:]+@)?(?P<host>[A-Za-z0-9.-]+):"
    r"(?P<path>[^/].*)$")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_COMMIT_ID = re.compile(r"^[0-9a-fA-F]{40,64}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
STAGE_EXECUTION_ROOT_CLAIM = ".taskplane-execution-root.json"
STAGE_EXECUTION_ATTEMPT_CLAIM = ".taskplane-execution-attempt.json"
LOCATOR = os.path.join("taskplane", "workspace.json")


class StorageIdentityError(RuntimeError):
    pass


class _NoGitLocator(StorageIdentityError):
    pass


def resolve_repository_family(workspace: str) -> dict:
    """Locate the exact current worktree and its repository-family launcher.

    Worktrees may be nested beneath a managed parent and may move between
    sessions.  Resolution therefore starts from the current path on every
    call; no checkout path is cached in canonical state.
    """
    current = os.path.realpath(os.path.abspath(os.path.expanduser(workspace)))
    if not os.path.isdir(current):
        current = os.path.dirname(current)
    chain: list[str] = []
    cursor = current
    while True:
        chain.append(cursor)
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent
    def git_path(*arguments: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *arguments], cwd=current, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        value = result.stdout.strip()
        return os.path.realpath(value) if result.returncode == 0 and value \
            else None

    worktree = git_path("rev-parse", "--path-format=absolute",
                        "--show-toplevel") or next((
        path for path in chain
        if os.path.exists(os.path.join(path, ".git"))), current)
    launcher = next((os.path.realpath(os.path.join(path, ".taskplane",
                                                   "codex-hook.py"))
                     for path in chain
                     if os.path.isfile(os.path.join(path, ".taskplane",
                                                    "codex-hook.py"))), None)
    if launcher is None:
        # Linked worktrees created by Codex are external siblings, not
        # descendants of the primary checkout. Git's common directory is the
        # portable repository-family boundary; its parent is the primary
        # checkout for a non-bare repository.
        common = git_path("rev-parse", "--path-format=absolute",
                          "--git-common-dir")
        candidate = (os.path.join(os.path.dirname(common), ".taskplane",
                                  "codex-hook.py") if common else None)
        if candidate and os.path.isfile(candidate):
            launcher = os.path.realpath(candidate)
    return {"schema": "taskplane.repository-family/v1",
            "worktree": worktree, "launcher": launcher}


@dataclass(frozen=True)
class RepositoryIdentity:
    """Stable logical repository identity plus this checkout's location."""

    repo_id: str
    kind: str
    host: str | None
    owner: str | None
    name: str
    remote: str | None
    workspace: str | None = None

    @property
    def key(self) -> str:
        readable = "-".join(
            value for value in (self.host, self.owner, self.name) if value)
        readable = _SAFE.sub("-", readable).strip("-.").lower() or "repo"
        digest = hashlib.sha256(self.repo_id.encode("utf-8")).hexdigest()[:10]
        return f"{readable[:80]}-{digest}"

    def to_dict(self) -> dict:
        return asdict(self)


def _hosted_parts(value: str) -> tuple[str, str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = _SCP_REMOTE.match(text)
    if match and "://" not in text:
        host = match.group("host")
        path = match.group("path")
    else:
        parsed = urlsplit(text)
        if parsed.scheme not in {"http", "https", "ssh", "git"}:
            return None
        host = parsed.hostname or ""
        path = parsed.path.lstrip("/")
    parts = [part for part in path.rstrip("/").split("/") if part]
    if len(parts) != 2 or not host:
        return None
    owner, name = parts
    if name.lower().endswith(".git"):
        name = name[:-4]
    if not owner or not name:
        return None
    return host.lower(), owner.lower(), name.lower()


def identity_from_remote(remote: str, *, workspace: str | None = None) \
        -> RepositoryIdentity:
    """Resolve equivalent HTTPS/SSH hosted remotes to one repository id."""
    parts = _hosted_parts(remote)
    if parts is None:
        raise ValueError(f"remote is not a hosted repository identity: {remote}")
    host, owner, name = parts
    return RepositoryIdentity(
        repo_id=f"{host}/{owner}/{name}", kind="hosted", host=host,
        owner=owner, name=name, remote=str(remote),
        workspace=(os.path.realpath(workspace) if workspace else None))


def _git_value(workspace: str, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=workspace, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = (result.stdout or "").strip()
    return value if result.returncode == 0 and value else None


def resolve_repository_identity(workspace: str, *, remote: str | None = None) \
        -> RepositoryIdentity:
    """Resolve a checkout to hosted identity, falling back to local git id."""
    root = os.path.realpath(os.path.abspath(workspace))
    value = remote or _git_value(root, "remote", "get-url", "origin")
    if value and _hosted_parts(value):
        return identity_from_remote(value, workspace=root)
    common = _git_value(root, "rev-parse", "--git-common-dir")
    common_root = None
    if common:
        common_root = os.path.realpath(
            common if os.path.isabs(common) else os.path.join(root, common))
    family_root = (os.path.dirname(common_root)
                   if common_root and os.path.basename(common_root) == ".git"
                   else root)
    name = os.path.basename(family_root.rstrip(os.sep)) or "repository"
    # A local-only repository is path-owned. Key it by the canonical root so
    # identity stays stable across the explicit `git init` recovery step.
    # Hosted repositories are keyed by remote and already unify worktrees.
    # Linked worktrees of a local-only repository share one Git common dir.
    # Keying by the checkout path split one repository into unrelated owners.
    digest = hashlib.sha256(family_root.encode("utf-8")).hexdigest()[:16]
    return RepositoryIdentity(
        repo_id=f"local/{name.lower()}/{digest}", kind="local", host=None,
        owner=None, name=name, remote=value, workspace=root)


def project_taskplane_home(workspace: str) -> str:
    """Return the project's execution directory without following its links."""
    root = os.path.realpath(os.path.abspath(os.path.expanduser(workspace)))
    path = os.path.join(root, ".taskplane")
    if os.path.lexists(path):
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise StorageIdentityError(
                "project execution storage must be a directory without symlinks")
    return path


def taskplane_home(home: str | None = None, *,
                   workspace: str | None = None) -> str:
    """Resolve explicit storage, an existing run binding, or project storage.

    Existing locators remain authoritative until a deliberate storage
    selection. No old user-home state is searched, copied, or migrated.
    """
    if home:
        return os.path.realpath(os.path.abspath(os.path.expanduser(home)))
    configured = os.environ.get("TASKPLANE_HOME")
    root = os.path.realpath(os.path.abspath(workspace or os.getcwd()))
    if configured:
        canonical = os.path.realpath(os.path.abspath(os.path.expanduser(configured)))
        selection = _load_storage_selection(root) if workspace else None
        if selection and canonical != selection["home"]:
            raise StorageIdentityError(
                "TASKPLANE_HOME does not match selected project execution storage")
        return canonical
    locator = load_workspace_locator(root)
    if locator:
        return str(locator["home"])
    return project_taskplane_home(root)


@dataclass(frozen=True)
class StorageLayout:
    home: str
    repository_key: str
    repository_record: str
    checkout_root: str
    mirror_path: str
    worktree_root: str
    project_root: str
    knowledge_root: str
    run_root: str
    state_root: str
    graph_root: str
    evidence_root: str
    lens_root: str
    artifact_root: str
    cache_root: str

    def graph_cache_path(self, head: str, scanner_version: str) -> str:
        revision = _SAFE.sub("-", str(head)).strip("-.") or "unknown"
        scanner = _SAFE.sub("-", str(scanner_version)).strip("-.") or "unknown"
        return _confined_stage_path(
            self.home, "cache", "graphs", self.repository_key,
            revision, f"{scanner}.json", leaf_kind="file")


def resolve_layout(identity: RepositoryIdentity, *, run_id: str,
                   home: str | None = None) -> StorageLayout:
    """Return every canonical root for one repository/run without writing."""
    root = taskplane_home(home, workspace=identity.workspace)
    key = identity.key
    run = validate_stage_path_id(run_id, "run id")
    run_root = os.path.join(root, "runs", run)
    checkout_root = os.path.join(root, "checkouts", key)
    project_root = os.path.join(root, "projects", key)
    layout = StorageLayout(
        home=root,
        repository_key=key,
        repository_record=os.path.join(root, "repositories", f"{key}.json"),
        checkout_root=checkout_root,
        mirror_path=os.path.join(checkout_root, "mirror.git"),
        worktree_root=os.path.join(checkout_root, "worktrees"),
        project_root=project_root,
        knowledge_root=os.path.join(project_root, "knowledge"),
        run_root=run_root,
        state_root=os.path.join(run_root, "state"),
        graph_root=os.path.join(run_root, "graph"),
        evidence_root=os.path.join(run_root, "evidence"),
        lens_root=os.path.join(run_root, "lenses"),
        artifact_root=os.path.join(run_root, "artifacts"),
        cache_root=os.path.join(root, "cache"),
    )
    for name in ("checkout_root", "worktree_root", "project_root", "knowledge_root",
                 "run_root", "state_root", "graph_root", "evidence_root", "lens_root",
                 "artifact_root", "cache_root"):
        relative = os.path.relpath(getattr(layout, name), root)
        _confined_stage_path(root, *relative.split(os.sep), leaf_kind="directory")
    _confined_stage_path(root, "repositories", f"{key}.json", leaf_kind="file")
    return layout




@contextmanager
def _storage_file_lock(path: str, *, timeout: float = 10.0):
    """Cross-host storage lock with a fail-closed, owner-bound fallback."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    try:
        handle = open(path, "a+b")
    except OSError as exc:
        raise StorageIdentityError(
            f"could not open dashboard storage lock: {exc}") from exc
    try:
        import fcntl
    except ImportError:
        fcntl = None
    if fcntl is not None:
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX)
            except OSError as exc:
                raise StorageIdentityError(
                    f"could not acquire dashboard storage lock: {exc}") \
                    from exc
            yield
        finally:
            handle.close()
        return
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
    if msvcrt is not None:
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + max(0.1, timeout)
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise StorageIdentityError(
                            "could not acquire dashboard storage lock") \
                            from exc
                    time.sleep(0.01)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()
        return
    handle.close()
    lockdir = path + ".lockdir"
    owner_path = os.path.join(lockdir, "owner")
    owner = secrets.token_hex(32)
    deadline = time.monotonic() + max(0.1, timeout)
    while True:
        try:
            os.mkdir(lockdir)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise StorageIdentityError(
                    "could not acquire dashboard storage lock")
            time.sleep(0.01)
            continue
        except OSError as exc:
            raise StorageIdentityError(
                f"could not acquire dashboard storage lock: {exc}") from exc
        try:
            with open(owner_path, "x", encoding="ascii") as marker:
                marker.write(owner)
                marker.flush()
                os.fsync(marker.fileno())
        except OSError as exc:
            try:
                os.rmdir(lockdir)
            except OSError:
                pass
            raise StorageIdentityError(
                f"could not establish dashboard lock ownership: {exc}") \
                from exc
        break
    try:
        yield
    finally:
        try:
            with open(owner_path, encoding="ascii") as marker:
                observed_owner = marker.read()
            if observed_owner != owner:
                raise StorageIdentityError(
                    "could not release dashboard storage lock: "
                    "ownership is ambiguous")
            os.unlink(owner_path)
            os.rmdir(lockdir)
        except StorageIdentityError:
            raise
        except OSError as exc:
            raise StorageIdentityError(
                f"could not release dashboard storage lock: {exc}") from exc


def _checked_binding_path(path: str) -> str:
    """Keep binding authority in Git metadata and reject redirected nodes."""
    absolute = os.path.abspath(path)
    for candidate in (os.path.dirname(absolute), absolute):
        try:
            mode = os.lstat(candidate).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise StorageIdentityError("workspace binding uses an unsafe symlink")
        expected = stat.S_ISDIR if candidate != absolute else stat.S_ISREG
        if not expected(mode):
            raise StorageIdentityError("workspace binding has an unsafe file type")
    return absolute


def _locator_path(checkout: str) -> str:
    # Run locators belong to the selected checkout's private Git directory.
    # Read that explicit marker directly on the hot path; invoking Git for
    # every state/trace read made a phase perform thousands of subprocesses.
    # This resolves metadata only, never another checkout's run or artifacts.
    marker = os.path.join(checkout, ".git")
    directory = marker if os.path.isdir(marker) else None
    if directory is None and os.path.isfile(marker):
        try:
            with open(marker, encoding="utf-8") as handle:
                line = handle.read(4097).strip()
        except (OSError, UnicodeError) as exc:
            raise StorageIdentityError("workspace Git marker is unreadable") from exc
        if len(line) > 4096 or not line.startswith("gitdir: ") or "\n" in line:
            raise StorageIdentityError("workspace Git marker is invalid")
        directory = os.path.join(checkout, line[len("gitdir: "):])
    if directory is not None and os.path.isfile(os.path.join(directory, "HEAD")):
        return _checked_binding_path(os.path.join(
            os.path.realpath(directory), LOCATOR))
    relative = _git_value(checkout, "rev-parse", "--git-path", LOCATOR)
    if not relative:
        raise _NoGitLocator(
            "workspace locator requires a valid Git checkout")
    return _checked_binding_path(
        relative if os.path.isabs(relative) else os.path.join(checkout, relative))


def _selection_path(checkout: str) -> str:
    return _checked_binding_path(os.path.join(
        os.path.dirname(_locator_path(checkout)), "execution-storage.json"))


def _binding_fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _load_storage_selection(checkout: str) -> dict | None:
    try:
        path = _selection_path(checkout)
    except _NoGitLocator:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise StorageIdentityError("execution storage selection is unreadable") from exc
    root = os.path.realpath(os.path.abspath(checkout))
    if not isinstance(value, dict) or set(value) != {
            "schema", "checkout", "home", "previous_binding"} or \
            value.get("schema") != "taskplane.execution-storage-selection/v1" or \
            value.get("checkout") != root or \
            value.get("home") != project_taskplane_home(root):
        raise StorageIdentityError("execution storage selection identity is invalid")
    previous = value.get("previous_binding")
    if previous is not None:
        if not isinstance(previous, dict) or set(previous) != {
                "run_id", "home", "locator_path", "locator_fingerprint",
                "manifest_fingerprint", "archive_path"} or \
                not _FINGERPRINT.fullmatch(str(previous.get("locator_fingerprint"))) or \
                not _FINGERPRINT.fullmatch(str(previous.get("manifest_fingerprint"))) or \
                previous.get("locator_path") != _locator_path(root):
            raise StorageIdentityError("execution storage previous binding is invalid")
        validate_stage_path_id(previous.get("run_id"), "previous run id")
        expected = _selection_archive_path(
            value["home"], previous["locator_fingerprint"])
        if previous.get("archive_path") != expected:
            raise StorageIdentityError("execution storage archive path is invalid")
        try:
            with open(expected, encoding="utf-8") as handle:
                archive = json.load(handle)
        except (OSError, ValueError) as exc:
            raise StorageIdentityError("execution storage archive is unavailable") from exc
        if not isinstance(archive, dict) or \
                _binding_fingerprint(archive.get("locator")) != previous["locator_fingerprint"] or \
                _binding_fingerprint(archive.get("manifest")) != previous["manifest_fingerprint"]:
            raise StorageIdentityError("execution storage archive fingerprint mismatch")
        manifest = _unused_preflight_manifest(root, archive["locator"])
        if _binding_fingerprint(manifest) != previous["manifest_fingerprint"]:
            raise StorageIdentityError(
                "execution_storage_migration_required: preserved prior manifest changed; "
                "explicit migration is required to clear the read-only history guard")
    return value


def _selection_archive_path(home: str, fingerprint: str) -> str:
    digest = _path_component(fingerprint, _FINGERPRINT, "binding fingerprint")
    return _confined_stage_path(
        home, "storage-selections", digest + ".json", leaf_kind="file")


def write_workspace_locator(checkout: str, *, identity: RepositoryIdentity,
                            layout: StorageLayout, run_id: str) -> str:
    """Bind the checkout's private Git metadata to one canonical run."""
    root = os.path.realpath(os.path.abspath(checkout))
    home = os.path.realpath(layout.home)
    paths = {
        "state": layout.state_root, "graph": layout.graph_root,
        "evidence": layout.evidence_root, "lenses": layout.lens_root,
        "artifacts": layout.artifact_root,
    }
    for value in paths.values():
        if os.path.commonpath((home, os.path.realpath(value))) != home:
            raise StorageIdentityError("run path escapes taskPlane home")
    value = {
        "schema": "taskplane.workspace/v1", "run_id": str(run_id),
        "repo_id": identity.repo_id, "repository_key": identity.key,
        "checkout": root, "primary_checkout": root,
        "home": home, "paths": paths,
    }
    path = _locator_path(root)
    selection = _load_storage_selection(root)
    if selection and home != selection["home"]:
        raise StorageIdentityError("workspace locator differs from selected execution storage")
    _atomic_json(path, value)
    return path


def load_workspace_locator(checkout: str) -> dict | None:
    """Load and validate an ignored checkout-to-run locator, if present."""
    root = os.path.realpath(os.path.abspath(checkout))
    try:
        path = _locator_path(root)
    except _NoGitLocator:
        return None
    selection = _load_storage_selection(root)
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        if selection and selection.get("previous_binding"):
            raise StorageIdentityError("selected storage's previous locator is missing")
        return None
    except (OSError, ValueError) as exc:
        raise StorageIdentityError(f"workspace locator is unreadable: {exc}")
    if not isinstance(value, dict) or value.get("schema") != \
            "taskplane.workspace/v1":
        raise StorageIdentityError("workspace locator has an invalid schema")
    if os.path.realpath(str(value.get("checkout") or "")) != root:
        raise StorageIdentityError("workspace locator belongs to another checkout")
    primary = os.path.realpath(str(value.get("primary_checkout") or root))
    if not os.path.isabs(primary):
        raise StorageIdentityError("workspace locator has no primary checkout")
    if primary != root and (not isinstance(value.get("task_id"), str) or not value["task_id"].strip()):
        raise StorageIdentityError("worker workspace locator has no explicit task identity")
    stored_home = str(value.get("home") or "")
    home = os.path.realpath(os.path.abspath(os.path.expanduser(stored_home)))
    if not stored_home or not os.path.isabs(stored_home):
        raise StorageIdentityError("workspace locator has no canonical home")
    if os.path.normcase(stored_home) != os.path.normcase(home):
        raise StorageIdentityError("workspace locator home is not canonical")
    paths = value.get("paths")
    if not isinstance(paths, dict) or set(paths) != {
            "state", "graph", "evidence", "lenses", "artifacts"}:
        raise StorageIdentityError("workspace locator paths are incomplete")
    for item in paths.values():
        if not isinstance(item, str) or not os.path.isabs(item) or \
                os.path.commonpath((home, os.path.realpath(item))) != home:
            raise StorageIdentityError("workspace locator path escapes its home")
    if selection:
        previous = selection.get("previous_binding")
        if previous and _binding_fingerprint(value) == previous["locator_fingerprint"]:
            return None
        if home != selection["home"]:
            raise StorageIdentityError("workspace locator differs from selected execution storage")
    return value


def bind_workspace_taskplane_home(
        checkout: str, environment: MutableMapping[str, str]) -> str | None:
    """Recover the storage selection for CLI and hook processes alike."""
    locator = load_workspace_locator(checkout)
    selection = _load_storage_selection(checkout) if locator is None else None
    expected = (str(locator["home"]) if locator else
                str(selection["home"]) if selection else None)
    if expected is None:
        return None
    _bind_taskplane_home(environment, expected)
    return expected


def _unused_preflight_manifest(checkout: str, locator: dict) -> dict:
    """Prove that rebinding cannot detach any governed execution authority."""
    refusal = "execution_storage_migration_required"
    if locator.get("task_id") or locator.get("primary_checkout") != checkout:
        raise StorageIdentityError(f"{refusal}: worker bindings cannot be reselected")
    # This read is intentionally inspect(), never load(): even a read lock or
    # journal relay would write to storage the user is no longer authorizing.
    if __package__:
        from . import run_store as run_store_module
    else:
        import run_store as run_store_module
    try:
        _confined_stage_path(locator["home"], "runs", locator["run_id"],
                             "manifest.json", leaf_kind="file")
        manifest = run_store_module.RunStore(home=locator["home"]).inspect(
            locator["run_id"])
    except (OSError, ValueError, StorageIdentityError, run_store_module.RunStoreError) as exc:
        raise StorageIdentityError(
            f"{refusal}: prior run could not be inspected") from exc
    identity = resolve_repository_identity(checkout)
    repository = manifest.get("repository") or {}
    allowed = {
        "schema", "run_id", "revision", "status", "repository", "target",
        "host", "preflight", "contract", "paths", "run_artifacts",
        "stage_heads", "lineage", "stage_operations", "stage_journal_outbox",
        "active_stage_projection",
    }
    if set(manifest) - allowed or \
            manifest.get("status") not in {"preflight", "ready", "awaiting_user", "waiting_external"} or \
            manifest.get("contract") != {"status": "inactive", "task_id": None} or \
            any(manifest.get(key) for key in (
                "stage_heads", "lineage", "stage_operations", "stage_journal_outbox")) or \
            manifest.get("paths") != locator.get("paths") or \
            repository.get("checkout") != checkout or \
            repository.get("repo_id") != identity.repo_id or \
            locator.get("repo_id") != identity.repo_id or \
            locator.get("repository_key") != identity.key:
        raise StorageIdentityError(
            f"{refusal}: existing run is not an unused repository preflight")
    layout = resolve_layout(identity, home=locator["home"], run_id=locator["run_id"])
    expected_paths = {
        "state": layout.state_root, "graph": layout.graph_root,
        "evidence": layout.evidence_root, "lenses": layout.lens_root,
        "artifacts": layout.artifact_root,
    }
    if locator.get("paths") != expected_paths:
        raise StorageIdentityError(f"{refusal}: prior run paths are not canonical")
    roots = [layout.run_root]
    def unreadable(error: OSError) -> None:
        raise StorageIdentityError(f"{refusal}: prior run state is unreadable") from error

    for root in roots:
        if os.path.islink(root):
            raise StorageIdentityError(f"{refusal}: prior run state uses an unsafe symlink")
        if not os.path.exists(root):
            continue
        for directory, names, filenames in os.walk(
                root, followlinks=False, onerror=unreadable):
            for name in names + filenames:
                path = os.path.join(directory, name)
                if os.path.islink(path):
                    raise StorageIdentityError(f"{refusal}: prior run contains an unsafe symlink")
                relative = os.path.relpath(path, root).split(os.sep)
                if name in {"loop.json", "tracks.json", STAGE_EXECUTION_ROOT_CLAIM,
                            STAGE_EXECUTION_ATTEMPT_CLAIM} or \
                        "submission" in name.lower() or \
                        (relative[0] == "stages" and os.path.isfile(path)):
                    raise StorageIdentityError(
                        f"{refusal}: prior run has execution or submission state")
    # The shared knowledge tree contains retained histories from other runs.
    # v4 execution is owned by the bound manifest, never the track catalog.
    # Only a singleton/current-run legacy loop can make this binding's
    # unused-preflight status ambiguous; unrelated histories stay untouched.
    history_root = os.path.join(layout.knowledge_root, "state")
    if os.path.isdir(history_root):
        active_history = None
        tracks_path = os.path.join(history_root, "tracks.json")
        if os.path.lexists(tracks_path):
            if os.path.islink(tracks_path):
                raise StorageIdentityError(f"{refusal}: active track catalog uses an unsafe symlink")
            try:
                with open(tracks_path, encoding="utf-8") as handle:
                    tracks = json.load(handle)
            except (OSError, ValueError) as exc:
                raise StorageIdentityError(f"{refusal}: active track catalog is unreadable") from exc
            if not isinstance(tracks, dict):
                raise StorageIdentityError(f"{refusal}: active track catalog is invalid")
            active = tracks.get("active")
            if active:
                token = validate_stage_path_id(active, "active track id")
                active_history = _confined_stage_path(
                    locator["home"], "projects", identity.key, "knowledge", "state",
                    "tracks", token, "loop.json", leaf_kind="file")
        for directory, names, filenames in os.walk(
                history_root, followlinks=False, onerror=unreadable):
            names[:] = [name for name in names
                        if not os.path.islink(os.path.join(directory, name))]
            if "loop.json" not in filenames:
                continue
            path = os.path.join(directory, "loop.json")
            if os.path.islink(path):
                raise StorageIdentityError(f"{refusal}: historical loop is not safely inspectable")
            try:
                with open(path, encoding="utf-8") as handle:
                    history = json.load(handle)
            except (OSError, ValueError) as exc:
                raise StorageIdentityError(f"{refusal}: historical loop is unreadable") from exc
            if not isinstance(history, dict):
                raise StorageIdentityError(f"{refusal}: historical loop identity is ambiguous")
            history_run = history.get("run_id")
            if history_run == locator["run_id"] or \
                    ((directory == history_root or path == active_history)
                     and history and not history_run):
                raise StorageIdentityError(
                    f"{refusal}: current binding has legacy execution state")
    return manifest


def select_project_execution_storage(
        workspace: str, *, environment: MutableMapping[str, str] | None = None) -> dict:
    """Explicitly choose local execution without creating or migrating a run.

    The only supersedable binding is an unused preflight. The old manifest
    and locator are archived locally before a trusted Git selection marker
    is written. The original locator and external run remain untouched.
    """
    root = os.path.realpath(os.path.abspath(workspace))
    home = project_taskplane_home(root)
    locator_path = _locator_path(root)
    existing_selection = _load_storage_selection(root)
    locator = load_workspace_locator(root)
    if existing_selection:
        if environment is not None:
            environment["TASKPLANE_HOME"] = home
        return {**existing_selection, "status": "already_selected",
                "prior_history_guard": bool(existing_selection["previous_binding"])}
    if locator and locator["home"] == home:
        if environment is not None:
            environment["TASKPLANE_HOME"] = home
        return {"schema": "taskplane.execution-storage-selection/v1",
                "status": "already_selected", "checkout": root,
                "home": home, "previous_binding": None, "prior_history_guard": False}
    manifest = _unused_preflight_manifest(root, locator) if locator else None
    previous = None
    if locator:
        fingerprint = _binding_fingerprint(locator)
        archive_path = _selection_archive_path(home, fingerprint)
        previous = {
            "run_id": locator["run_id"], "home": locator["home"],
            "locator_path": locator_path, "locator_fingerprint": fingerprint,
            "manifest_fingerprint": _binding_fingerprint(manifest),
            "archive_path": archive_path,
        }
        os.makedirs(home, mode=0o700, exist_ok=True)
        project_taskplane_home(root)
        _ensure_confined_directories(home, os.path.dirname(archive_path))
        archive = {"schema": "taskplane.execution-storage-archive/v1",
                   "locator": locator, "manifest": manifest}
        if os.path.exists(archive_path):
            with open(archive_path, encoding="utf-8") as handle:
                if json.load(handle) != archive:
                    raise StorageIdentityError("execution storage archive already differs")
        else:
            _atomic_json(archive_path, archive)
        # Re-prove the old manifest after archiving and before changing which
        # run future invocations may use. No authority is silently discarded.
        if _binding_fingerprint(_unused_preflight_manifest(root, locator)) != \
                previous["manifest_fingerprint"] or \
                _binding_fingerprint(load_workspace_locator(root)) != fingerprint:
            raise StorageIdentityError("execution storage previous binding changed")
    value = {"schema": "taskplane.execution-storage-selection/v1",
             "checkout": root, "home": home, "previous_binding": previous}
    _atomic_json(_selection_path(root), value)
    if environment is not None:
        environment["TASKPLANE_HOME"] = home
    return {**value, "status": "selected", "prior_history_guard": bool(previous)}


def _bind_taskplane_home(environment: MutableMapping[str, str], expected: str) -> None:
    configured = str(environment.get("TASKPLANE_HOME") or "")
    if configured:
        canonical = taskplane_home(configured)
        if os.path.normcase(configured) != os.path.normcase(canonical):
            raise StorageIdentityError("hook TASKPLANE_HOME is not canonical")
        if os.path.normcase(canonical) != os.path.normcase(expected):
            raise StorageIdentityError(
                "hook TASKPLANE_HOME does not match the workspace locator")
    environment["TASKPLANE_HOME"] = expected


def bind_hook_taskplane_home(
        checkout: str, environment: MutableMapping[str, str], *,
        hook_path: str | None = None) -> str:
    """Bind a governed hook process to its dedicated checkout locator.

    Calling this function declares that the invocation is a Taskplane hook.
    A governed checkout binds to its locator. Before governance exists,
    both installed hook paths use the canonical project receipt home.
    Run initialization is independent of hook readiness and produces the
    locator before dispatch. Hooks never create a competing run binding.
    """
    expected = bind_workspace_taskplane_home(checkout, environment)
    if expected is None:
        # Installation precedes run initialization. Native and repository
        # hooks share this receipt-only bootstrap home; neither creates or
        # selects a run. Explicit configured homes are deliberate opt-ins.
        if str(hook_path or "").strip().lower() not in {"native", "bridge"}:
            raise StorageIdentityError(
                "Taskplane hook requires a governed workspace locator")
        configured = str(environment.get("TASKPLANE_HOME") or "")
        expected = (taskplane_home(configured) if configured else
                    project_taskplane_home(checkout))
    _bind_taskplane_home(environment, expected)
    return expected


def managed_path(checkout: str, area: str, *parts: str) -> str | None:
    """Return a validated run-owned path, or ``None`` for legacy workspaces."""
    locator = load_workspace_locator(checkout)
    if locator is None:
        return None
    roots = locator["paths"]
    if area not in roots:
        raise StorageIdentityError(f"unknown managed run area: {area}")
    root = os.path.realpath(roots[area])
    path = os.path.realpath(os.path.join(root, *parts))
    if os.path.commonpath((root, path)) != root:
        raise StorageIdentityError("managed run path escapes its area")
    return path


def managed_path_allowed(checkout: str, path: str) -> bool:
    """Whether an absolute artifact path belongs to this checkout's run."""
    locator = load_workspace_locator(checkout)
    if locator is None or not os.path.isabs(str(path or "")):
        return False
    candidate = os.path.realpath(path)
    return any(os.path.commonpath((os.path.realpath(root), candidate)) ==
               os.path.realpath(root) for root in locator["paths"].values())


def _path_component(value: object, pattern: re.Pattern[str],
                    label: str) -> str:
    if not isinstance(value, str):
        raise StorageIdentityError(f"{label} is not a safe path component")
    text = value
    if text in {"", ".", ".."} or not pattern.fullmatch(text):
        raise StorageIdentityError(f"{label} is not a safe path component")
    return text


def validate_stage_path_id(value: object, label: str = "stage id") -> str:
    """Validate one portable run/stage/execution/attempt path identity."""
    return _path_component(value, _RUN_ID, label)


def _confined_stage_path(home: str, *parts: str,
                         leaf_kind: str) -> str:
    """Resolve one stage path and reject unsafe existing filesystem nodes.

    Path construction is deliberately side-effect free.  The RunStore owns
    directory and immutable-object creation under its lock.  Existing nodes
    are inspected with ``lstat`` so a symlink is never silently resolved into
    stage authority, even when its destination remains beneath Taskplane home.
    """
    root = os.path.abspath(os.path.expanduser(home))
    if os.path.realpath(root) != root:
        raise StorageIdentityError("taskPlane home uses an unsafe symlink")
    if os.path.lexists(root) and not stat.S_ISDIR(os.lstat(root).st_mode):
        raise StorageIdentityError("taskPlane home is not a directory")
    path = os.path.abspath(os.path.join(root, *parts))
    if os.path.commonpath((root, path)) != root:
        raise StorageIdentityError("stage path escapes taskPlane home")
    current = root
    relative = os.path.relpath(path, root).split(os.sep)
    for index, part in enumerate(relative):
        current = os.path.join(current, part)
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            # A descendant cannot exist when its ancestor is absent.  Keep
            # walking only to return the deterministic path without writing.
            continue
        except OSError as exc:
            raise StorageIdentityError(
                f"stage path is not safely inspectable: {exc}") from exc
        if stat.S_ISLNK(mode):
            raise StorageIdentityError("stage path uses an unsafe symlink")
        is_leaf = index == len(relative) - 1
        if not is_leaf and not stat.S_ISDIR(mode):
            raise StorageIdentityError(
                "stage path ancestor is not a directory")
        if is_leaf and leaf_kind == "directory" and not stat.S_ISDIR(mode):
            raise StorageIdentityError("stage execution root is not a directory")
        if is_leaf and leaf_kind == "file" and not stat.S_ISREG(mode):
            raise StorageIdentityError("stage object path is not a regular file")
    return path


def stage_object_path_for_run(home: str, run_id: str, stage_id: str,
                              fingerprint: str) -> str:
    """Canonical immutable stage-object path for the selected run store."""
    run = validate_stage_path_id(run_id, "run id")
    stage = validate_stage_path_id(stage_id, "stage id")
    digest = _path_component(fingerprint, _FINGERPRINT, "stage fingerprint")
    return _confined_stage_path(
        home, "runs", run, "stages", "objects", stage, f"{digest}.json",
        leaf_kind="file")


def _confined_directory_components(home: str,
                                   directory: str) -> tuple[str, list[str]]:
    root = _confined_stage_path(home, leaf_kind="directory")
    target = os.path.abspath(directory)
    if os.path.commonpath((root, target)) != root:
        raise StorageIdentityError("stage directory escapes taskPlane home")
    relative = os.path.relpath(target, root)
    components = [] if relative == "." else relative.split(os.sep)
    if any(part in {"", ".", ".."} for part in components):
        raise StorageIdentityError("stage directory component is invalid")
    return root, components


def _open_confined_directory(home: str, directory: str, *,
                             create: bool) -> int:
    """Open a pinned directory chain using mkdirat/openat without links."""
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW") or \
            os.open not in os.supports_dir_fd or \
            os.mkdir not in os.supports_dir_fd:
        raise StorageIdentityError(
            "stage storage needs no-follow dir-fd support")
    root, components = _confined_directory_components(home, directory)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise StorageIdentityError(
            f"taskPlane home is not safely inspectable: {exc}") from exc
    try:
        for part in components:
            try:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                    try:
                        os.fsync(descriptor)
                    except OSError:
                        pass
                except FileExistsError:
                    # A competing creator still needs the same openat proof.
                    pass
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise StorageIdentityError("stage path is not a directory")
        return descriptor
    except StorageIdentityError:
        os.close(descriptor)
        raise
    except OSError as exc:
        os.close(descriptor)
        raise StorageIdentityError(
            f"stage directory is not safely accessible: {exc}") from exc


def _create_claimed_directory(parent_fd: int, name: str, *,
                              claim_name: str, payload: bytes) -> int:
    """Create one directory leaf or verify its exact create-once claim."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        created = True
        try:
            os.fsync(parent_fd)
        except OSError:
            pass
    except FileExistsError:
        pass
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise StorageIdentityError(
            "stage execution root is not a safe directory") from exc
    claim_flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        try:
            claim_fd = os.open(claim_name, claim_flags, dir_fd=descriptor)
        except FileNotFoundError:
            if not created:
                raise StorageIdentityError(
                    "existing stage execution root has no exact claim")
            write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            try:
                claim_fd = os.open(
                    claim_name, write_flags, 0o600, dir_fd=descriptor)
                with os.fdopen(claim_fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.fsync(descriptor)
            except OSError as exc:
                raise StorageIdentityError(
                    "stage execution-root claim could not be created") from exc
            return descriptor
        except OSError as exc:
            raise StorageIdentityError(
                "stage execution-root claim is not safely readable") from exc
        with os.fdopen(claim_fd, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise StorageIdentityError(
                    "stage execution-root claim is not a private regular file")
            existing = handle.read(len(payload) + 1)
        if existing != payload or metadata.st_size != len(payload):
            raise StorageIdentityError(
                "stage execution root belongs to another claim")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _canonical_claim_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode(
                           "utf-8")


def _verify_execution_root_claim(directory_fd: int, *, claim_name: str,
                                 payload: bytes) -> None:
    """Verify exact private claim bytes through one pinned directory FD."""
    try:
        claim_fd = os.open(
            claim_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    except OSError as exc:
        raise StorageIdentityError(
            "stage execution-root claim is not safely readable") from exc
    with os.fdopen(claim_fd, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise StorageIdentityError(
                "stage execution-root claim is not a private regular file")
        existing = handle.read(len(payload) + 1)
    if existing != payload or metadata.st_size != len(payload):
        raise StorageIdentityError(
            "stage execution root belongs to another claim")


def _reopen_execution_root_claim(
        home: str, path: str, pinned_fd: int, *, claim_name: str,
        payload: bytes) -> None:
    """Bind a current canonical path to the exact inode already claimed."""
    current_fd = _open_confined_directory(home, path, create=False)
    try:
        pinned = os.fstat(pinned_fd)
        current = os.fstat(current_fd)
        if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
            raise StorageIdentityError(
                "stage execution root changed during claim")
        _verify_execution_root_claim(
            current_fd, claim_name=claim_name, payload=payload)
    finally:
        os.close(current_fd)


def _root_claim(run_id: str, stage_id: str,
                execution_root_id: str) -> dict:
    return {
        "schema": "taskplane.stage-execution-root-claim/v1",
        "run_id": run_id,
        "stage_id": stage_id,
        "execution_root_id": execution_root_id,
    }


def _attempt_claim(run_id: str, stage_id: str, execution_root_id: str,
                   attempt_id: str) -> dict:
    return {
        "schema": "taskplane.stage-execution-attempt-claim/v1",
        "run_id": run_id,
        "stage_id": stage_id,
        "execution_root_id": execution_root_id,
        "attempt_id": attempt_id,
    }


def _before_stage_execution_root_reopen(path: str) -> None:
    """Deterministic no-op seam for rename/substitution regression tests."""


def claim_stage_execution_root_for_run(
        home: str, run_id: str, stage_id: str, execution_root_id: str,
        attempt_id: str | None = None) -> dict:
    """Create once or re-prove an exact isolated stage/attempt root claim."""
    run = validate_stage_path_id(run_id, "run id")
    stage = validate_stage_path_id(stage_id, "stage id")
    execution = validate_stage_path_id(
        execution_root_id, "execution root id")
    attempt = (validate_stage_path_id(attempt_id, "attempt id")
               if attempt_id is not None else None)
    executions = _confined_stage_path(
        home, "runs", run, "stages", "executions", leaf_kind="directory")
    executions_fd = _open_confined_directory(home, executions, create=True)
    root_claim = _root_claim(run, stage, execution)
    root_payload = _canonical_claim_bytes(root_claim)
    try:
        stage_fd = _create_claimed_directory(
            executions_fd, stage, claim_name=STAGE_EXECUTION_ROOT_CLAIM,
            payload=root_payload)
    finally:
        os.close(executions_fd)
    try:
        stage_path = stage_execution_root_for_run(home, run, stage)
        if attempt is None:
            path = stage_path
            _before_stage_execution_root_reopen(stage_path)
            _reopen_execution_root_claim(
                home, stage_path, stage_fd,
                claim_name=STAGE_EXECUTION_ROOT_CLAIM,
                payload=root_payload)
            claim = root_claim
        else:
            attempt_claim = _attempt_claim(run, stage, execution, attempt)
            attempt_payload = _canonical_claim_bytes(attempt_claim)
            attempt_fd = _create_claimed_directory(
                stage_fd, attempt,
                claim_name=STAGE_EXECUTION_ATTEMPT_CLAIM,
                payload=attempt_payload)
            try:
                path = stage_execution_root_for_run(home, run, stage, attempt)
                _before_stage_execution_root_reopen(path)
                _reopen_execution_root_claim(
                    home, stage_path, stage_fd,
                    claim_name=STAGE_EXECUTION_ROOT_CLAIM,
                    payload=root_payload)
                _reopen_execution_root_claim(
                    home, path, attempt_fd,
                    claim_name=STAGE_EXECUTION_ATTEMPT_CLAIM,
                    payload=attempt_payload)
            finally:
                os.close(attempt_fd)
            claim = attempt_claim
        result = dict(claim)
        result["root"] = path
        return result
    finally:
        os.close(stage_fd)


def _ensure_confined_directories(home: str, directory: str) -> None:
    descriptor = _open_confined_directory(home, directory, create=True)
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def ensure_stage_object_parent_for_run(
        home: str, run_id: str, stage_id: str, fingerprint: str) -> str:
    """Safely create and return one immutable object's parent directory."""
    path = stage_object_path_for_run(home, run_id, stage_id, fingerprint)
    parent = os.path.dirname(path)
    _ensure_confined_directories(home, parent)
    # Revalidate after creation so a replaced ancestor never becomes an
    # accepted object destination.
    checked = stage_object_path_for_run(home, run_id, stage_id, fingerprint)
    if checked != path:
        raise StorageIdentityError("stage object path changed during creation")
    return parent


def stage_execution_root_for_run(home: str, run_id: str, stage_id: str,
                                 attempt_id: str | None = None) -> str:
    """Canonical isolated execution root, optionally for one resume attempt."""
    run = validate_stage_path_id(run_id, "run id")
    stage = validate_stage_path_id(stage_id, "stage id")
    parts = ["runs", run, "stages", "executions", stage]
    if attempt_id is not None:
        parts.append(validate_stage_path_id(attempt_id, "attempt id"))
    return _confined_stage_path(home, *parts, leaf_kind="directory")


def _stage_locator(checkout: str) -> tuple[dict, str]:
    root = os.path.realpath(os.path.abspath(checkout))
    locator = load_workspace_locator(root)
    if locator is None:
        raise StorageIdentityError(
            "stage storage requires a canonical run locator")
    return locator, root


def _validate_stage_source_separation(locator: dict, checkout: str,
                                      path: str) -> None:
    if os.path.commonpath((checkout, path)) != checkout:
        return
    primary = str(locator["primary_checkout"])
    home = project_taskplane_home(primary)
    if locator["home"] != home or \
            os.path.commonpath((home, path)) != home or path == home:
        raise StorageIdentityError("stage path is inside source checkout")


def stage_object_path(checkout: str, stage_id: str, fingerprint: str) -> str:
    """Resolve an immutable stage object without falling back into source."""
    locator, checkout_root = _stage_locator(checkout)
    path = stage_object_path_for_run(
        str(locator["home"]), str(locator["run_id"]), stage_id, fingerprint)
    _validate_stage_source_separation(locator, checkout_root, path)
    return path


def stage_execution_root(checkout: str, stage_id: str,
                         attempt_id: str | None = None) -> str:
    """Resolve an isolated stage tree without falling back into source."""
    locator, checkout_root = _stage_locator(checkout)
    path = stage_execution_root_for_run(
        str(locator["home"]), str(locator["run_id"]), stage_id, attempt_id)
    _validate_stage_source_separation(locator, checkout_root, path)
    return path


def evaluation_root(checkout: str) -> str:
    """Canonical evaluator-artifact root for managed and legacy workspaces."""
    return (managed_path(checkout, "evidence", "evaluation") or
            os.path.join(os.path.realpath(checkout), ".eval"))


def evaluation_path(checkout: str, name: str = "verdict.json") -> str:
    return os.path.join(evaluation_root(checkout), name)


def evaluator_contract_path(checkout: str) -> str:
    """Portable legacy path or canonical absolute managed result path."""
    managed = managed_path(checkout, "evidence", "evaluation", "verdict.json")
    return managed or ".eval/verdict.json"


def review_public_root(checkout: str) -> str:
    """Canonical final-review projection root."""
    return (managed_path(checkout, "artifacts", "public") or
            os.path.join(os.path.realpath(checkout), ".em-review"))


def review_public_path(checkout: str, name: str) -> str:
    return os.path.join(review_public_root(checkout), name)


def dashboard_path(checkout: str) -> str:
    return (managed_path(checkout, "artifacts", "mission-control",
                         "dashboard.html") or
            os.path.join(os.path.realpath(checkout), ".taskplane",
                         "dashboard.html"))


def dependency_graph_visual_path(checkout: str) -> str:
    return (managed_path(checkout, "artifacts", "dependency-graph.html") or
            os.path.join(os.path.realpath(checkout), ".taskplane",
                         "depgraph.html"))


def lane_findings_path(checkout: str, lens_id: str) -> str:
    """Compatibility lane evidence without checkout-local model output."""
    managed = managed_path(checkout, "lenses", "legacy",
                           f"lens-{lens_id}", "findings.json")
    return managed or os.path.join(os.path.realpath(checkout), ".em-review",
                                   f"lens-{lens_id}", "findings.json")


def _worktree_token(task_id: str) -> str:
    raw = str(task_id or "task")
    slug = _SAFE.sub("-", raw).strip("-.")[:60] or "task"
    return f"{slug}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:8]}"


def task_worktree_path(checkout: str, task_id: str) -> str:
    locator = load_workspace_locator(checkout)
    if locator is None:
        return os.path.join(os.path.realpath(checkout), ".tp-work", task_id)
    return os.path.join(locator["home"], "checkouts",
                        locator["repository_key"], "worktrees", "tasks",
                        str(locator["run_id"]), _worktree_token(task_id))


def task_worktree_reference(checkout: str, task_id: str) -> str:
    return (task_worktree_path(checkout, task_id)
            if load_workspace_locator(checkout) else f".tp-work/{task_id}")


def task_worktree_registration_path(primary_checkout: str,
                                    task_id: str) -> str:
    locator = load_workspace_locator(primary_checkout)
    base = (os.path.join(locator["paths"]["state"],
                         "worktree-registrations") if locator else
            os.path.join(os.path.realpath(primary_checkout), ".taskplane",
                         "worktree-registrations"))
    return os.path.join(base, _worktree_token(task_id) + ".json")


def _worktree_branch_tip(checkout: str) -> tuple[str | None, str | None]:
    branch = _git_value(checkout, "symbolic-ref", "--quiet", "HEAD")
    tip = _git_value(checkout, "rev-parse", "HEAD")
    return branch, tip


def _git_common_dir(checkout: str) -> str | None:
    common = _git_value(checkout, "rev-parse", "--git-common-dir")
    if not common:
        return None
    return os.path.realpath(
        common if os.path.isabs(common) else os.path.join(checkout, common))


def _linked_worktree_record(primary: str, worker: str) -> dict | None:
    """Return the one exact Git worktree-list record for ``worker``."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"], cwd=primary,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    matches = []
    for block in result.stdout.strip().split("\n\n"):
        record = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            if key in {"worktree", "HEAD", "branch"}:
                record[key] = value
        if os.path.realpath(record.get("worktree") or "") == worker:
            matches.append(record)
    return matches[0] if len(matches) == 1 else None


def validate_task_worktree_registration(
        primary_checkout: str, worker_checkout: str, task_id: str,
        *, target_commit: str, registration: dict) -> dict:
    """Re-prove a registered linked worktree at one independent target."""
    primary = os.path.realpath(os.path.abspath(primary_checkout))
    supplied = os.path.abspath(os.path.expanduser(worker_checkout))
    worker = os.path.realpath(supplied)
    if supplied != worker or worker == primary or "\n" in worker:
        raise StorageIdentityError(
            "managed worktree path uses an unsafe alias or symlink")
    target = str(target_commit or "")
    if not _COMMIT_ID.fullmatch(target):
        raise StorageIdentityError("managed worktree target commit is invalid")
    if not isinstance(registration, dict) or registration.get("schema") != \
            "taskplane.managed-task-worktree/v1":
        raise StorageIdentityError("managed worktree registration is invalid")
    run_id = str(registration.get("run_id") or "")
    if not _RUN_ID.fullmatch(run_id) or run_id == "legacy":
        raise StorageIdentityError("managed worktree run identity is invalid")
    identity = resolve_repository_identity(primary)
    worker_identity = resolve_repository_identity(worker)
    repository = registration.get("repository") or {}
    if not isinstance(repository, dict) or \
            repository.get("repo_id") != identity.repo_id or \
            registration.get("repository_key") != identity.key or \
            worker_identity.repo_id != identity.repo_id:
        raise StorageIdentityError("managed worktree repository changed")
    registered_primary = os.path.realpath(str(
        registration.get("primary_checkout") or ""))
    registered_worker = os.path.realpath(str(registration.get("path") or ""))
    if registration.get("linked") is not True or \
            registration.get("task_id") != str(task_id) or \
            registered_primary != primary or registered_worker != worker:
        raise StorageIdentityError("managed worktree registration identity mismatch")
    primary_common = _git_common_dir(primary)
    worker_common = _git_common_dir(worker)
    if not primary_common or worker_common != primary_common:
        raise StorageIdentityError("worker is not linked to the primary repository")
    branch, head = _worktree_branch_tip(worker)
    branch_ref = str(registration.get("branch_ref") or "")
    if not branch_ref.startswith("refs/heads/") or "\n" in branch_ref or \
            not branch or branch != branch_ref:
        raise StorageIdentityError("managed task branch changed")
    resolved_target = _git_value(worker, "rev-parse", "--verify",
                                 f"{target}^{{commit}}")
    primary_tip = _git_value(primary, "rev-parse", branch_ref)
    if resolved_target != target or head != target or primary_tip != target or \
            registration.get("branch_tip") != target:
        raise StorageIdentityError(
            "managed task target commit and current HEAD differ")
    linked = _linked_worktree_record(primary, worker)
    if not linked or linked.get("HEAD") != target or \
            linked.get("branch") != branch_ref:
        raise StorageIdentityError("managed linked-worktree identity changed")
    return registration


def load_task_worktree_registration(primary_checkout: str,
                                    task_id: str) -> dict | None:
    path = task_worktree_registration_path(primary_checkout, task_id)
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise StorageIdentityError(
            f"managed worktree registration is unreadable: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != \
            "taskplane.managed-task-worktree/v1":
        raise StorageIdentityError("managed worktree registration is invalid")
    expected = os.path.realpath(task_worktree_path(primary_checkout, task_id))
    if value.get("task_id") != str(task_id) or \
            os.path.realpath(str(value.get("path") or "")) != expected:
        raise StorageIdentityError("managed worktree registration identity mismatch")
    return value


def register_task_worktree(primary_checkout: str, worker_checkout: str,
                           task_id: str) -> dict:
    """Durably register one exact linked task worktree outside its tree."""
    primary = os.path.realpath(primary_checkout)
    worker = os.path.realpath(os.path.abspath(worker_checkout))
    expected = os.path.realpath(task_worktree_path(primary, task_id))
    if worker != expected:
        raise StorageIdentityError(
            "worker checkout is outside its managed task worktree path")
    branch, tip = _worktree_branch_tip(worker)
    if not tip:
        raise StorageIdentityError(
            "managed task worktree must have a recorded tip")
    identity = resolve_repository_identity(primary)
    worker_identity = resolve_repository_identity(worker)
    if worker_identity.repo_id != identity.repo_id:
        raise StorageIdentityError("worker belongs to another repository")
    parent = load_workspace_locator(primary)
    if parent is None or parent.get("task_id"):
        raise StorageIdentityError("managed task worktree requires an explicit primary run locator")
    value = {
        "schema": "taskplane.managed-task-worktree/v1",
        "repository": identity.to_dict(),
        "repository_key": identity.key,
        "run_id": parent["run_id"],
        "task_id": str(task_id), "path": worker,
        "primary_checkout": primary, "branch_ref": branch,
        "branch_tip": tip, "linked": True,
        "registered_at": int(time.time()),
    }
    _atomic_json(task_worktree_registration_path(primary, task_id), value)
    return value


def refresh_task_worktree_tip(primary_checkout: str, task_id: str) -> dict:
    """Record the exact task-branch tip immediately before its merge.

    Registration happens when a task is claimed, before the executor creates
    its commits.  The merge boundary therefore refreshes only the mutable tip
    after re-proving the immutable repository, path, task, and branch identity.
    """
    primary = os.path.realpath(primary_checkout)
    value = load_task_worktree_registration(primary, task_id)
    if value is None:
        raise StorageIdentityError(
            "managed task worktree registration is missing")
    worker = os.path.realpath(str(value.get("path") or ""))
    expected = os.path.realpath(task_worktree_path(primary, task_id))
    if worker != expected:
        raise StorageIdentityError("managed worktree path changed")
    identity = resolve_repository_identity(primary)
    worker_identity = resolve_repository_identity(worker)
    if identity.repo_id != value.get("repository", {}).get("repo_id") or \
            worker_identity.repo_id != identity.repo_id:
        raise StorageIdentityError("managed worktree repository changed")
    branch, tip = _worktree_branch_tip(worker)
    if not branch or branch != value.get("branch_ref"):
        raise StorageIdentityError("managed task branch changed")
    if not tip:
        raise StorageIdentityError("managed task branch tip is unavailable")
    branch_tip = _git_value(primary, "rev-parse", branch)
    if branch_tip != tip:
        raise StorageIdentityError("managed task branch ref and HEAD differ")
    refreshed = dict(value)
    refreshed["branch_tip"] = tip
    refreshed["prepared_at"] = int(time.time())
    _atomic_json(task_worktree_registration_path(primary, task_id), refreshed)
    return refreshed


def _worker_locator_value(primary_checkout: str, worker_checkout: str,
                          task_id: str) -> dict:
    """Derive one worker binding from the explicitly selected parent run."""
    parent = load_workspace_locator(primary_checkout)
    if parent is None or parent.get("task_id"):
        raise StorageIdentityError("worker binding requires the primary run locator")
    expected = os.path.realpath(task_worktree_path(primary_checkout, task_id))
    worker = os.path.realpath(os.path.abspath(worker_checkout))
    if worker != expected:
        raise StorageIdentityError(
            "worker checkout is outside its managed task worktree path")
    token = _worktree_token(task_id)
    paths = {}
    for area, root in parent["paths"].items():
        paths[area] = os.path.join(root, "worktrees", token)
    value = {
        "schema": "taskplane.workspace/v1",
        "task_id": str(task_id), "run_id": parent["run_id"], "repo_id": parent["repo_id"],
        "repository_key": parent["repository_key"], "checkout": worker,
        "primary_checkout": parent.get("primary_checkout") or
        os.path.realpath(primary_checkout),
        "home": parent["home"], "paths": paths,
    }
    existing = load_workspace_locator(worker)
    if existing is not None and existing != value:
        raise StorageIdentityError("worker locator belongs to another task or run")
    return value


def bind_worker_locator(primary_checkout: str, worker_checkout: str,
                        task_id: str) -> str:
    """Bind only the explicitly selected worker; never overwrite another owner."""
    value = _worker_locator_value(primary_checkout, worker_checkout, task_id)
    path = _locator_path(worker_checkout)
    if load_workspace_locator(worker_checkout) is None:
        _atomic_json(path, value)
    register_task_worktree(primary_checkout, worker_checkout, task_id)
    return path


def worker_locator_error(primary: str, worker: str, task_id: str) -> str | None:
    """Check startup identity without creating or rewriting a locator."""
    try:
        _worker_locator_value(primary, worker, task_id)
        registration = load_task_worktree_registration(primary, task_id)
        if registration is None:
            raise StorageIdentityError("managed worktree registration is missing")
        validate_task_worktree_registration(primary, worker, task_id,
            target_commit=_git_value(worker, "rev-parse", "HEAD"), registration=registration)
    except StorageIdentityError as exc:
        return str(exc)
    return None


def managed_write_allow(checkout: str) -> list[str] | None:
    """Exact isolated run roots a managed read-only worker may populate."""
    locator = load_workspace_locator(checkout)
    if locator is None:
        return None
    return [os.path.join(path, "**")
            for path in sorted(locator["paths"].values())]


def worker_write_allow(checkout: str) -> list[str]:
    paths = managed_write_allow(checkout)
    if paths is None:
        raise StorageIdentityError("phase worker requires an explicit run locator")
    return paths


def submission_evidence_paths(checkout: str, step: str) -> list[str]:
    if step == "evaluate":
        return [evaluation_path(checkout)]
    if step == "em":
        return [review_public_path(checkout, "findings.json"),
                review_public_path(checkout, "report.md")]
    return []


def instruction_artifact_paths(checkout: str | None) -> tuple[str, str]:
    if checkout:
        return evaluator_contract_path(checkout), review_public_root(checkout)
    return ".eval/verdict.json", ".em-review"


def control_root(workspace: str) -> str:
    locator = load_workspace_locator(workspace)
    return os.path.join(locator["paths"]["state"], "control") if locator else os.path.join(workspace, ".taskplane")


def resolved_worktree(workspace: str) -> str:
    candidate = os.path.realpath(os.path.abspath(workspace))
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=candidate,
            capture_output=True, text=True, encoding="utf-8", timeout=10, check=False)
        top = result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        top = ""
    return os.path.realpath(top) if top else candidate

if __package__:
    from .primitives import (StateError, atomic_write_json, _run, _durable_makedirs, _fsync_directory)
else:
    from primitives import (StateError, atomic_write_json, _run, _durable_makedirs, _fsync_directory)


def tp_dir(workspace: str) -> str:
    # Managed hybrid checkouts keep their complete control plane in the
    # canonical run root.  Unmanaged/local workspaces preserve the historic
    # per-checkout runtime for compatibility and isolated worktree workers.
    locator = load_workspace_locator(workspace)
    if locator:
        return os.path.join(locator["paths"]["state"], "control")
    return project_taskplane_home(workspace)


def store_home(workspace: str | None = None) -> str:
    """Private knowledge and execution share the same canonical home."""
    return taskplane_home(workspace=workspace)


def _workspace_identity(workspace: str) -> str:
    """Canonical checkout identity shared by parent and child processes."""
    return os.path.realpath(os.path.abspath(workspace))


def _path_slug(workspace: str) -> str:
    ap = _workspace_identity(workspace)
    return re.sub(r"[^A-Za-z0-9]+", "-", ap) or "-"


def project_key(workspace: str) -> str:
    """Stable, COLLISION-FREE per-project key: a readable path slug plus a
    short hash of the canonical absolute path.

    The slug alone (the v0.9.6 scheme) collapses every run of non-alphanumerics
    to '-', so distinct projects whose paths differ only by punctuation —
    /x/my-app, /x/my_app, /x/my.app — all map to ONE key and silently share a
    store (KB, requirements, and loop.json — a gate in one corrupts the other).
    An 8-char hash guarantees distinct keys while the slug stays readable."""
    # A managed hybrid checkout carries one validated, ignored locator.  It
    # binds every clone/worktree of the same hosted repository to the same
    # durable project knowledge root while run state remains run-scoped.
    # Local/unmanaged checkouts preserve the historical path identity.
    locator = load_workspace_locator(workspace)
    if locator:
        return str(locator["repository_key"])
    # Use the same repository identity before and after run registration.
    # Onboarding never moves requirements or searches an older project store.
    return resolve_repository_identity(workspace).key


def store_env() -> str:
    """The TASKPLANE_STORE override, normalized ('repo' | 'external' | '').
    One reader so the kernel and loop can't drift on how the env is parsed."""
    return os.environ.get("TASKPLANE_STORE", "").strip().lower()


def external_store_root(workspace: str) -> str:
    """The private store, separate from an explicitly shared knowledge tree.

    The historic name is retained for callers; its default home is now the
    active project's .taskplane directory.
    """
    locator = load_workspace_locator(workspace)
    home = str(locator["home"]) if locator else store_home(workspace)
    root = _confined_stage_path(home, "projects", project_key(workspace),
                                leaf_kind="directory")
    return root


def repo_store_root(workspace: str) -> str:
    """The SHARED in-repo store (<ws>/.taskplane-kb/) — committed with the
    work, so it survives Claude Tag's ephemeral sandbox and is visible to
    every teammate who clones the branch."""
    return os.path.join(os.path.abspath(workspace), ".taskplane-kb")


def _mode_file(workspace: str) -> str:
    return os.path.join(external_store_root(workspace), "mode.json")


def _remote_mode_file(workspace: str) -> str | None:
    """Fallback mode file keyed by the git remote URL, so plan/privacy
    settings follow the REPO across checkouts/paths (a second clone without
    its own mode.json inherits the user's choice, closing the quiet privacy
    hole where `share set private` in checkout A did nothing in checkout B)."""
    if not os.path.isdir(workspace):
        return None
    try:
        r = _run(["git", "remote", "get-url", "origin"], cwd=workspace)
        url = (r.stdout or "").strip()
    except OSError:
        return None
    if not url:
        return None
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(store_home(workspace), "modes", f"{h}.json")


def _read_personal_mode(workspace: str) -> tuple[dict, bool]:
    """(settings, found) — path-keyed mode.json first; the remote-keyed
    fallback (which shells out to git) is consulted only on a miss.

    FAIL SAFE (v2.3.0): a mode file that EXISTS but won't read/parse is a
    damaged privacy control, not "no setting recorded". Resolve it as
    private=True — the more restrictive residency — so corruption can never
    silently downgrade a user's `share set private` to the committed SHARED
    in-repo store. (set_mode heals the file on the next explicit setting.)"""
    for p in (_mode_file(workspace), _remote_mode_file(workspace)):
        if not p:
            continue
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f), True
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return {"private": True}, True   # corrupt/unreadable -> private
    return {}, False


def _persistent_mode(workspace: str) -> dict:
    """Mode resolution EXCLUDING the TASKPLANE_STORE env override — the
    durable truth that set_mode may materialize into a committed config.
    (v1.5.1: deciding the config.json write from the env-influenced mode let
    a transient env var create a committable artifact for the whole team.)

    A committed shared config expresses the repository owner's preference;
    it is not consent from a newly arrived user.  Until that user records a
    local choice, keep writes in the private store and make the one-command
    shared opt-in explicit.  Managed hosts that deliberately force
    ``TASKPLANE_STORE=repo`` still take the environment-override path in
    :func:`get_mode`.
    """
    personal, found = _read_personal_mode(workspace)
    plan = personal.get("plan")
    private = bool(personal.get("private"))
    if private:
        return {"plan": plan, "store": "external", "private": True,
                "source": "private-setting"}
    shared_cfg = os.path.join(repo_store_root(workspace), "config.json")
    if os.path.exists(shared_cfg):
        if not plan:
            try:
                with open(shared_cfg, encoding="utf-8") as f:
                    plan = json.load(f).get("plan")
            except (AttributeError, OSError, ValueError):
                pass
        if not found:
            return {
                "plan": plan or "team",
                "store": "external",
                "private": True,
                "source": "shared-config-unconfirmed",
                "notice": (
                    "this repo offers a SHARED in-repo store "
                    "(.taskplane-kb/ — committed with the code), but this "
                    "new local user remains PRIVATE in the selected project store "
                    "until sharing is explicitly confirmed. Run `tp share "
                    "set shared` to opt in; `tp share set private` keeps "
                    "knowledge local."
                ),
            }
        out = {"plan": plan or "team", "store": "repo", "private": False,
               "source": "shared-config"}
        return out
    if plan in ("team", "enterprise"):
        return {"plan": plan, "store": "repo", "private": False,
                "source": "plan"}
    return {"plan": plan or "personal", "store": "external",
            "private": False, "source": "default"}


def get_mode(workspace: str) -> dict:
    """v1.5.0 — plan-aware store resolution. Returns
    {"plan", "store" ("external"|"repo"), "private", "source"[, "notice"]}.

    Precedence:
      1. TASKPLANE_STORE env (explicit override — Tag skill, tests)
      2. the user's PRIVATE setting (mode.json; also remote-keyed fallback)
      3. the user's recorded shared choice
      4. an unconfirmed committed shared config remains external/private
      5. the recorded plan: team/enterprise -> repo, personal -> external
      6. default: external (personal)."""
    env = store_env()
    if env in ("repo", "external"):
        personal, _ = _read_personal_mode(workspace)
        return {"plan": personal.get("plan"), "store": env,
                "private": bool(personal.get("private")), "source": "env"}
    return _persistent_mode(workspace)


def set_mode(workspace: str, plan: str | None = None,
             private: bool | None = None) -> dict:
    """Update the plan and/or private flag (both changeable any time).
    Personal settings persist in the private store's mode.json AND a
    remote-keyed copy (so they follow the repo across checkouts). The
    committed shared config (<ws>/.taskplane-kb/config.json) is written
    ONLY from the env-independent resolution, and ONLY for an explicit
    team/enterprise plan — a transient TASKPLANE_STORE, or one user's
    personal-plan declaration, must never rewrite the team's file."""
    cfg, _ = _read_personal_mode(workspace)
    if plan is not None:
        cfg["plan"] = plan
        if plan == "personal":
            # A personal-plan selection is an explicit private/local choice,
            # not acknowledgement of a repository's shared-store proposal.
            cfg["private"] = True
    if private is not None:
        cfg["private"] = bool(private)
    targets = [p for p in (_mode_file(workspace),
                           _remote_mode_file(workspace)) if p]
    wrote_any, last_err = False, None
    for p in targets:
        try:
            # Atomic (v2.3.0): mode.json is the private-vs-shared CONTROL
            # file — a torn write must keep the old file, never drop the
            # user's `private` flag.
            atomic_write_json(p, cfg, indent=2)
            wrote_any = True
        except OSError as e:
            last_err = e
    if targets and not wrote_any:
        # Every persistence target failed — a silent no-op here means the
        # user's `share set private` never took effect. Surface it. (v1.5.2)
        raise OSError(f"could not persist taskplane mode to any of "
                      f"{targets}: {last_err}")
    persistent = _persistent_mode(workspace)
    if persistent["store"] == "repo" \
            and persistent["plan"] in ("team", "enterprise") \
            and cfg.get("plan") in ("team", "enterprise"):
        try:
            os.makedirs(repo_store_root(workspace), exist_ok=True)
            with open(os.path.join(repo_store_root(workspace),
                                   "config.json"), "w", encoding="utf-8") as f:
                json.dump({"plan": persistent["plan"], "store": "repo"},
                          f, indent=2)
        except OSError:
            pass
    return get_mode(workspace)


def store_root(workspace: str) -> str:
    """This project's store dir — private (selected home, default .taskplane) or
    in-repo (<ws>/.taskplane-kb, the Claude Tag / team-shared mode),
    resolved by get_mode(): TASKPLANE_STORE env wins, then the user's
    private setting, then a committed shared config, then the plan
    (team/enterprise -> repo, personal -> external)."""
    if get_mode(workspace)["store"] == "repo":
        return repo_store_root(workspace)
    return external_store_root(workspace)


def kb_root(workspace: str) -> str:
    """Resolve only the explicitly selected project store."""
    return _confined_stage_path(store_root(workspace), "knowledge",
                                leaf_kind="directory")


def store_meta_path(workspace: str) -> str:
    return os.path.join(store_root(workspace), "meta.json")


def _quarantine_shared_store_meta(path: str, workspace: str | None = None) -> str | None:
    """Move a stale shared locator into private recovery storage."""
    if not os.path.lexists(path):
        return None
    quarantine = _confined_stage_path(store_home(workspace), "privacy-quarantine",
                                      leaf_kind="directory")
    _durable_makedirs(quarantine)
    identity = hashlib.sha256(os.path.abspath(path).encode("utf-8")).hexdigest()
    destination = os.path.join(quarantine, f"store-meta-{identity}.json")
    if os.path.exists(destination):
        destination += "." + secrets.token_hex(8)
    os.replace(path, destination)
    _fsync_directory(os.path.dirname(path) or ".")
    _fsync_directory(quarantine)
    return destination


def write_store_meta(workspace: str) -> dict:
    """Record the store owner without publishing workstation identity.

    The private store retains the exact checkout locator needed by
    legacy adoption and local recovery.  A repository store is committed and
    shared, so it carries only stable pseudonyms and a repository fingerprint;
    neither an absolute path nor a credential-bearing remote URL crosses that
    boundary.
    """
    root = store_root(workspace)
    os.makedirs(root, exist_ok=True)
    remote = _run(["git", "config", "--get", "remote.origin.url"],
                  cwd=workspace).stdout.strip() or None
    shared = get_mode(workspace)["store"] == "repo"
    if shared:
        workspace_digest = hashlib.sha256(
            _workspace_identity(workspace).encode("utf-8")).hexdigest()
        repository_material = remote or project_key(workspace)
        meta = {
            "schema": "taskplane.store-meta/v2",
            "shared": True,
            "workspace_key": "workspace:" + workspace_digest[:24],
            "repository_fingerprint": hashlib.sha256(
                repository_material.encode("utf-8")).hexdigest(),
        }
    else:
        meta = {"key": project_key(workspace),
                "workspace": os.path.abspath(workspace),
                "workspace_realpath": _workspace_identity(workspace),
                "git_remote": remote,
                "shared": False}
    path = store_meta_path(workspace)
    try:
        atomic_write_json(path, meta, indent=2,
                          sort_keys=True)
    except OSError as exc:
        if shared:
            try:
                quarantined = _quarantine_shared_store_meta(path, workspace)
            except OSError as quarantine_error:
                raise StateError(
                    path, "shared store metadata write failed and stale raw "
                    "metadata could not be quarantined",
                    str(quarantine_error)) from exc
            raise StateError(
                path, "shared store metadata write failed closed",
                ("stale raw metadata moved to private quarantine " +
                 str(quarantined)) if quarantined else
                "no shared metadata was published") from exc
        raise StateError(path, "private store metadata write failed",
                         str(exc)) from exc
    return meta
