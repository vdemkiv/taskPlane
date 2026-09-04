"""Canonical repository identity and hybrid taskPlane storage layout.

Source checkouts, durable knowledge, replaceable caches, and run artifacts
have different lifecycles.  This module gives them one owner without putting
source code under a report directory or keying one repository by every clone
path it happens to use.
"""
from __future__ import annotations

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


def taskplane_home(home: str | None = None) -> str:
    configured = home or os.environ.get("TASKPLANE_HOME") or \
        os.path.join(os.path.expanduser("~"), ".taskplane")
    return os.path.realpath(os.path.abspath(os.path.expanduser(configured)))


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
        return os.path.join(self.cache_root, "graphs", self.repository_key,
                            revision, f"{scanner}.json")


def resolve_layout(identity: RepositoryIdentity, *, run_id: str,
                   home: str | None = None) -> StorageLayout:
    """Return every canonical root for one repository/run without writing."""
    root = taskplane_home(home)
    key = identity.key
    run = validate_stage_path_id(run_id, "run id")
    run_root = os.path.join(root, "runs", run)
    checkout_root = os.path.join(root, "checkouts", key)
    project_root = os.path.join(root, "projects", key)
    return StorageLayout(
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


def _atomic_json(path: str, value: dict) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:  # directory fsync is unavailable on some hosts
            pass
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


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


def _locator_path(checkout: str) -> str:
    relative = _git_value(checkout, "rev-parse", "--git-path", LOCATOR)
    if not relative:
        raise StorageIdentityError(
            "workspace locator requires a valid Git checkout")
    return os.path.realpath(
        relative if os.path.isabs(relative) else os.path.join(
            checkout, relative))


def write_workspace_locator(checkout: str, *, identity: RepositoryIdentity,
                            layout: StorageLayout, run_id: str) -> str:
    """Write the only run-owned byte allowed in a managed checkout."""
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
    _atomic_json(path, value)
    return path


def load_workspace_locator(checkout: str) -> dict | None:
    """Load and validate an ignored checkout-to-run locator, if present."""
    root = os.path.realpath(os.path.abspath(checkout))
    try:
        path = _locator_path(root)
    except StorageIdentityError:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
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
    return value


def bind_hook_taskplane_home(
        checkout: str, environment: MutableMapping[str, str], *,
        hook_path: str | None = None) -> str:
    """Bind a governed hook process to its dedicated checkout locator.

    Calling this function declares that the invocation is a Taskplane hook.
    A governed checkout must bind to its locator. Before governance exists,
    only the installed native hook may bootstrap a session receipt, and only
    in the canonical user-default home. Repository bridges, custom homes, and
    conflicting or noncanonical values remain closed failures.
    """
    locator = load_workspace_locator(checkout)
    if locator is None:
        # A newly-created Codex task has no run locator yet.  The installed
        # native hook is nevertheless authoritative evidence that the host
        # loaded Taskplane, and its package command is inert unless the
        # repository-family launcher already exists.  Bind only that narrow
        # bootstrap case to the canonical user-default store; repository
        # bridges and custom/untrusted homes still fail closed.
        if str(hook_path or "").strip().lower() != "native":
            raise StorageIdentityError(
                "Taskplane hook requires a governed workspace locator")
        expected = taskplane_home(os.path.join(
            os.path.expanduser("~"), ".taskplane"))
    else:
        expected = str(locator["home"])
    configured = str(environment.get("TASKPLANE_HOME") or "")
    if configured:
        canonical = os.path.realpath(
            os.path.abspath(os.path.expanduser(configured)))
        if os.path.normcase(configured) != os.path.normcase(canonical):
            raise StorageIdentityError("hook TASKPLANE_HOME is not canonical")
        if os.path.normcase(canonical) != os.path.normcase(expected):
            raise StorageIdentityError(
                "hook TASKPLANE_HOME does not match the workspace locator")
    environment["TASKPLANE_HOME"] = expected
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
    root = taskplane_home(home)
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
    """Canonical immutable stage-object path for an external run store."""
    run = validate_stage_path_id(run_id, "run id")
    stage = validate_stage_path_id(stage_id, "stage id")
    digest = _path_component(fingerprint, _FINGERPRINT, "stage fingerprint")
    return _confined_stage_path(
        home, "runs", run, "stages", "objects", stage, f"{digest}.json",
        leaf_kind="file")


def _confined_directory_components(home: str,
                                   directory: str) -> tuple[str, list[str]]:
    root = taskplane_home(home)
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
            "stage storage requires a canonical external run locator")
    return locator, root


def stage_object_path(checkout: str, stage_id: str, fingerprint: str) -> str:
    """Resolve an immutable stage object without falling back into source."""
    locator, checkout_root = _stage_locator(checkout)
    path = stage_object_path_for_run(
        str(locator["home"]), str(locator["run_id"]), stage_id, fingerprint)
    if os.path.commonpath((checkout_root, path)) == checkout_root:
        raise StorageIdentityError("stage object path is inside source checkout")
    return path


def stage_execution_root(checkout: str, stage_id: str,
                         attempt_id: str | None = None) -> str:
    """Resolve an isolated stage tree without falling back into source."""
    locator, checkout_root = _stage_locator(checkout)
    path = stage_execution_root_for_run(
        str(locator["home"]), str(locator["run_id"]), stage_id, attempt_id)
    if os.path.commonpath((checkout_root, path)) == checkout_root:
        raise StorageIdentityError(
            "stage execution root is inside source checkout")
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


def _registration_candidates(home: str, worker: str,
                             task_id: str) -> list[tuple[str, str, dict]]:
    """Find exact-path registrations without trusting a checkout locator."""
    runs = os.path.join(home, "runs")
    try:
        entries = sorted(os.scandir(runs), key=lambda item: item.name)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise StorageIdentityError(
            f"managed worktree registrations are unreadable: {exc}") from exc
    token = _worktree_token(task_id) + ".json"
    candidates = []
    for entry in entries:
        if not _RUN_ID.fullmatch(entry.name) or \
                not entry.is_dir(follow_symlinks=False):
            continue
        path = os.path.join(entry.path, "state", "worktree-registrations",
                            token)
        if not os.path.lexists(path):
            continue
        if os.path.islink(path) or os.path.realpath(path) != \
                os.path.abspath(path):
            raise StorageIdentityError(
                "managed worktree registration uses an unsafe symlink")
        try:
            with open(path, encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError) as exc:
            raise StorageIdentityError(
                f"managed worktree registration is unreadable: {exc}") \
                from exc
        if not isinstance(value, dict):
            raise StorageIdentityError(
                "managed worktree registration is invalid")
        # Historical runs may reuse the same task id. Only a registration
        # claiming this exact worker can authorize reconstruction.
        if os.path.realpath(str(value.get("path") or "")) == worker:
            candidates.append((entry.name, path, value))
    return candidates


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


def reconstruct_worker_locator(
        primary_checkout: str, worker_checkout: str, task_id: str, *,
        target_commit: str, home: str | None = None) -> str:
    """Restore a missing worker locator from one exact durable registration.

    This recovery is intentionally narrower than ``bind_worker_locator``:
    the primary must have no locator; an existing worker locator is accepted
    only when byte-equivalent in meaning to the reconstructed value. No run is
    inferred from branch names, paths, or conversation state.
    """
    primary = os.path.realpath(os.path.abspath(primary_checkout))
    supplied = os.path.abspath(os.path.expanduser(worker_checkout))
    worker = os.path.realpath(supplied)
    if load_workspace_locator(primary) is not None:
        raise StorageIdentityError(
            "primary locator exists; managed reconstruction is not applicable")
    worker_locator = _locator_path(worker)
    root = taskplane_home(home)
    candidates = _registration_candidates(root, worker, task_id)
    if len(candidates) != 1:
        label = "missing" if not candidates else "ambiguous"
        raise StorageIdentityError(
            f"managed worktree registration is {label}")
    run_dir, _, registration = candidates[0]
    if registration.get("run_id") != run_dir:
        raise StorageIdentityError(
            "managed worktree registration run identity mismatch")
    validate_task_worktree_registration(
        primary, supplied, task_id, target_commit=target_commit,
        registration=registration)
    identity = resolve_repository_identity(primary)
    layout = resolve_layout(identity, home=root, run_id=run_dir)
    token = _worktree_token(task_id)
    expected = os.path.realpath(os.path.join(
        layout.checkout_root, "worktrees", "tasks", run_dir, token))
    if worker != expected:
        raise StorageIdentityError(
            "managed worktree path is not canonical for its registered run")
    git_dir = _git_value(worker, "rev-parse", "--git-dir")
    if not git_dir:
        raise StorageIdentityError("managed worktree Git directory is missing")
    git_root = os.path.realpath(
        git_dir if os.path.isabs(git_dir) else os.path.join(worker, git_dir))
    if os.path.commonpath((git_root, worker_locator)) != git_root:
        raise StorageIdentityError("worker locator path escapes its Git directory")
    paths = {area: os.path.join(root_path, "worktrees", token)
             for area, root_path in {
                 "state": layout.state_root, "graph": layout.graph_root,
                 "evidence": layout.evidence_root,
                 "lenses": layout.lens_root,
                 "artifacts": layout.artifact_root,
             }.items()}
    value = {
        "schema": "taskplane.workspace/v1", "run_id": run_dir,
        "repo_id": identity.repo_id, "repository_key": identity.key,
        "checkout": worker, "primary_checkout": primary,
        "home": root, "paths": paths,
    }
    if os.path.lexists(worker_locator):
        existing = load_workspace_locator(worker)
        if existing != value:
            raise StorageIdentityError(
                "existing worker locator does not match the exact registration")
        return worker_locator
    _atomic_json(worker_locator, value)
    return worker_locator


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
    value = {
        "schema": "taskplane.managed-task-worktree/v1",
        "repository": identity.to_dict(),
        "repository_key": identity.key,
        "run_id": str((parent or {}).get("run_id") or "legacy"),
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


def bind_worker_locator(primary_checkout: str, worker_checkout: str,
                        task_id: str) -> str | None:
    """Bind a managed linked worktree to isolated roots in the same run."""
    parent = load_workspace_locator(primary_checkout)
    if parent is None:
        return task_worktree_registration_path(
            primary_checkout,
            register_task_worktree(primary_checkout, worker_checkout,
                                   task_id)["task_id"])
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
        "run_id": parent["run_id"], "repo_id": parent["repo_id"],
        "repository_key": parent["repository_key"], "checkout": worker,
        "primary_checkout": parent.get("primary_checkout") or
        os.path.realpath(primary_checkout),
        "home": parent["home"], "paths": paths,
    }
    path = _locator_path(worker)
    _atomic_json(path, value)
    register_task_worktree(primary_checkout, worker_checkout, task_id)
    return path


def worker_locator_error(primary: str, worker: str, task_id: str) -> str | None:
    try:
        bind_worker_locator(primary, worker, task_id)
    except StorageIdentityError as exc:
        return str(exc)
    return None


def managed_write_allow(checkout: str) -> list[str] | None:
    """Exact external run roots a managed read-only worker may populate."""
    locator = load_workspace_locator(checkout)
    if locator is None:
        return None
    return [os.path.join(path, "**")
            for path in sorted(locator["paths"].values())]


def worker_write_allow(checkout: str | None, legacy: str) -> list[str]:
    return (managed_write_allow(checkout) if checkout else None) or [legacy]


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


DASHBOARD_PUBLICATION_SCHEMA = "taskplane.dashboard-publication-store/v1"


def dashboard_snapshot_store_path(workspace: str) -> str:
    """Return the workspace-local atomic dashboard publication head."""
    root = os.path.realpath(os.path.abspath(workspace))
    return os.path.join(root, ".taskplane", "dashboard-state", "current.json")


def load_dashboard_publication(workspace: str) -> dict | None:
    """Load and authenticate the complete persisted snapshot history."""
    path = dashboard_snapshot_store_path(workspace)
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise StorageIdentityError(
            f"dashboard publication is unreadable: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != \
            DASHBOARD_PUBLICATION_SCHEMA or set(value) != {
                "schema", "current", "history"}:
        raise StorageIdentityError("dashboard publication schema is invalid")
    history = value.get("history")
    if not isinstance(history, list) or not history:
        raise StorageIdentityError("dashboard publication history is invalid")
    try:
        if __package__:
            from .host_native import (ContradictorySnapshotError,
                                      HostSurfaceSnapshot)
        else:
            from taskplane.host_native import (ContradictorySnapshotError,
                                               HostSurfaceSnapshot)
        checked = [HostSurfaceSnapshot.from_dict(row) for row in history]
    except (TypeError, ValueError) as exc:
        raise StorageIdentityError(
            f"dashboard publication snapshot is invalid: {exc}") from exc
    identities: dict[tuple[str, str, str, str, int], str] = {}
    for snapshot in checked:
        key = (snapshot.workflow_id, snapshot.run_id, snapshot.target,
               snapshot.revision, snapshot.sequence)
        prior = identities.get(key)
        if prior is not None and prior != snapshot.fingerprint:
            raise ContradictorySnapshotError(
                "contradictory snapshots share one sequence")
        identities[key] = snapshot.fingerprint
    current = HostSurfaceSnapshot.from_dict(value["current"])
    if current.to_dict() != checked[-1].to_dict():
        raise StorageIdentityError(
            "dashboard publication current head does not match history")
    return {"schema": DASHBOARD_PUBLICATION_SCHEMA,
            "current": current.to_dict(),
            "history": [snapshot.to_dict() for snapshot in checked]}


def commit_dashboard_snapshot(workspace: str, snapshot) -> dict:
    """CAS one authenticated HostSurfaceSnapshot into the durable head.

    An exact duplicate is idempotent. Equal sequence with different bytes is
    contradictory, and lower/non-monotonic updates fail closed.
    """
    try:
        if __package__:
            from .host_native import (ContradictorySnapshotError,
                                      HostSurfaceSnapshot)
        else:
            from taskplane.host_native import (ContradictorySnapshotError,
                                               HostSurfaceSnapshot)
        authenticated = HostSurfaceSnapshot.from_dict(snapshot.to_dict())
    except (AttributeError, TypeError, ValueError) as exc:
        raise StorageIdentityError(
            f"dashboard snapshot is invalid: {exc}") from exc
    path = dashboard_snapshot_store_path(workspace)
    with _storage_file_lock(path + ".lock"):
        prior = load_dashboard_publication(workspace)
        history = list((prior or {}).get("history") or [])
        if history:
            previous = HostSurfaceSnapshot.from_dict(history[-1])
            stable = (authenticated.workflow_id, authenticated.run_id,
                      authenticated.target, authenticated.revision)
            previous_stable = (previous.workflow_id, previous.run_id,
                               previous.target, previous.revision)
            if stable == previous_stable and \
                    authenticated.sequence == previous.sequence:
                if authenticated.fingerprint != previous.fingerprint:
                    raise ContradictorySnapshotError(
                        "contradictory snapshots share one sequence")
                return {"schema": DASHBOARD_PUBLICATION_SCHEMA,
                        "current": previous.to_dict(), "history": history,
                        "replayed": True}
            if (authenticated.workflow_id, authenticated.run_id,
                    authenticated.target) == (
                    previous.workflow_id, previous.run_id, previous.target) \
                    and authenticated.sequence <= previous.sequence:
                raise StorageIdentityError(
                    "dashboard snapshot sequence is not monotonic")
        history.append(authenticated.to_dict())
        # Publication history is bounded presentation evidence, not the event
        # journal. The authoritative workflow journal retains the full run.
        history = history[-256:]
        stored = {"schema": DASHBOARD_PUBLICATION_SCHEMA,
                  "current": authenticated.to_dict(), "history": history}
        _atomic_json(path, stored)
        return {**stored, "replayed": False}


def commit_dashboard_event(workspace: str, event) -> dict:
    """Durably append one authenticated snapshot event, idempotently."""
    try:
        if __package__:
            from .host_native import HostSurfaceEvent
        else:
            from taskplane.host_native import HostSurfaceEvent
        checked = HostSurfaceEvent.from_dict(event.to_dict())
    except (AttributeError, TypeError, ValueError) as exc:
        raise StorageIdentityError(f"dashboard event is invalid: {exc}") \
            from exc
    root = os.path.dirname(dashboard_snapshot_store_path(workspace))
    path = os.path.join(root, "events.json")
    with _storage_file_lock(path + ".lock"):
        try:
            with open(path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except FileNotFoundError:
            stored = {"schema": "taskplane.dashboard-events/v1",
                      "events": []}
        except (OSError, ValueError) as exc:
            raise StorageIdentityError(
                f"dashboard event history is unreadable: {exc}") from exc
        if not isinstance(stored, dict) or stored.get("schema") != \
                "taskplane.dashboard-events/v1" or \
                not isinstance(stored.get("events"), list):
            raise StorageIdentityError("dashboard event history is invalid")
        events = [HostSurfaceEvent.from_dict(row)
                  for row in stored["events"]]
        if any(row.fingerprint == checked.fingerprint for row in events):
            return {"event": checked.to_dict(), "replayed": True}
        events.append(checked)
        value = {"schema": "taskplane.dashboard-events/v1",
                 "events": [row.to_dict() for row in events[-512:]]}
        _atomic_json(path, value)
        return {"event": checked.to_dict(), "replayed": False}
