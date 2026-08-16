"""Canonical repository identity and hybrid taskPlane storage layout.

Source checkouts, durable knowledge, replaceable caches, and run artifacts
have different lifecycles.  This module gives them one owner without putting
source code under a report directory or keying one repository by every clone
path it happens to use.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
import re
import subprocess
import tempfile
from urllib.parse import urlsplit


_SCP_REMOTE = re.compile(
    r"^(?:[^@/:]+@)?(?P<host>[A-Za-z0-9.-]+):"
    r"(?P<path>[^/].*)$")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
LOCATOR = os.path.join("taskplane", "workspace.json")


class StorageIdentityError(RuntimeError):
    pass


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
    name = os.path.basename(root.rstrip(os.sep)) or "repository"
    # A local-only repository is path-owned. Key it by the canonical root so
    # identity stays stable across the explicit `git init` recovery step.
    # Hosted repositories are keyed by remote and already unify worktrees.
    digest = hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]
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
    run = str(run_id or "")
    if not _RUN_ID.fullmatch(run) or run in {".", ".."}:
        raise StorageIdentityError("run id is not a safe path component")
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
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


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
    home = os.path.realpath(str(value.get("home") or ""))
    if not home or not os.path.isabs(home):
        raise StorageIdentityError("workspace locator has no canonical home")
    paths = value.get("paths")
    if not isinstance(paths, dict) or set(paths) != {
            "state", "graph", "evidence", "lenses", "artifacts"}:
        raise StorageIdentityError("workspace locator paths are incomplete")
    for item in paths.values():
        if not isinstance(item, str) or not os.path.isabs(item) or \
                os.path.commonpath((home, os.path.realpath(item))) != home:
            raise StorageIdentityError("workspace locator path escapes its home")
    return value


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


def bind_worker_locator(primary_checkout: str, worker_checkout: str,
                        task_id: str) -> str | None:
    """Bind a managed linked worktree to isolated roots in the same run."""
    parent = load_workspace_locator(primary_checkout)
    if parent is None:
        return None
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
