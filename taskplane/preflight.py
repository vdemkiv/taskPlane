"""Resumable repository preconditions that finish before governance starts."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import uuid
from datetime import datetime, timezone

import repository
import recovery
import run_store
import storage
import taskplane_lite as tp
import target as target_module


class PreflightError(RuntimeError):
    pass


_BOOTSTRAP_SCHEMA = "taskplane.preflight-bootstrap/v1"
_KNOWLEDGE_MANIFEST_SCHEMA = \
    "taskplane.knowledge-preservation-manifest/v1"
_GOVERNANCE_BASELINE_SCHEMA = "taskplane.governance-baseline/v1"


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            count += len(chunk)
            digest.update(chunk)
    return count, digest.hexdigest()


def _closed_knowledge_entries(root: str) -> list[dict]:
    """Hash every regular knowledge object, excluding lock files only."""
    if not os.path.isdir(root):
        raise PreflightError("canonical knowledge root is unavailable")
    entries: list[dict] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        names.sort()
        filenames.sort()
        for name in names:
            path = os.path.join(directory, name)
            if os.path.islink(path):
                raise PreflightError(
                    "canonical knowledge root contains an unsafe symlink")
        for name in filenames:
            path = os.path.join(directory, name)
            if os.path.islink(path) or not os.path.isfile(path):
                raise PreflightError(
                    "canonical knowledge root contains a non-regular entry")
            if name.endswith(".lock"):
                continue
            size, digest = _sha256_file(path)
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            entries.append({"path": relative, "bytes": size,
                            "sha256": digest})
    return sorted(entries, key=lambda row: row["path"])


def _verify_knowledge_manifest(locator: dict, trusted: dict) -> dict:
    if not isinstance(trusted, dict) or trusted.get("schema") != \
            _KNOWLEDGE_MANIFEST_SCHEMA:
        raise PreflightError("trusted pre-cleanup knowledge manifest is absent")
    home = os.path.realpath(str(locator.get("home") or ""))
    key = str(locator.get("repository_key") or "")
    if not home or not os.path.isabs(home) or not key:
        raise PreflightError("workspace locator has no canonical knowledge root")
    root = os.path.realpath(os.path.join(home, "projects", key, "knowledge"))
    project = os.path.realpath(os.path.join(home, "projects", key))
    if os.path.commonpath((project, root)) != project:
        raise PreflightError("canonical knowledge root escapes the project")
    root_fingerprint = hashlib.sha256(root.encode("utf-8")).hexdigest()
    if (os.path.realpath(str(trusted.get("root") or "")) != root
            or trusted.get("root_fingerprint") != root_fingerprint
            or trusted.get("repo_id") != locator.get("repo_id")
            or trusted.get("repository_key") != key):
        raise PreflightError(
            "trusted manifest does not name the canonical knowledge root")
    if trusted.get("exclusions") != ["*.lock"]:
        raise PreflightError("knowledge manifest exclusions must be locks only")
    signed = dict(trusted)
    supplied_digest = signed.pop("manifest_digest", None)
    if supplied_digest != _canonical_digest(signed):
        raise PreflightError("knowledge manifest digest mismatch")
    expected = trusted.get("entries")
    if not isinstance(expected, list) or not expected:
        raise PreflightError("trusted knowledge manifest must not be empty")
    if expected != sorted(expected, key=lambda row: str(row.get("path"))):
        raise PreflightError("knowledge manifest entries are not sorted")
    current = _closed_knowledge_entries(root)
    if current != expected:
        raise PreflightError("knowledge preservation mismatch")
    return {"root": root, "root_fingerprint": root_fingerprint,
            "manifest_digest": supplied_digest, "entry_count": len(current),
            "preserved": True}


def _git_output(workspace: str, *args: str, binary: bool = False):
    try:
        result = subprocess.run(
            ["git", *args], cwd=workspace, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=not binary,
            encoding=None if binary else "utf-8",
            errors=None if binary else "replace", timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f"Git baseline evidence is unavailable: {exc}") \
            from exc
    if result.returncode != 0:
        error = (result.stderr.decode("utf-8", errors="replace")
                 if binary else result.stderr)
        raise PreflightError(
            f"Git baseline evidence is unavailable: {str(error).strip()}")
    return result.stdout if binary else result.stdout.strip()


def _verify_prior_design(workspace: str, entries: list[dict]) -> list[dict]:
    if not isinstance(entries, list) or not entries:
        raise PreflightError("prior Design evidence is absent")
    supplied_paths = ({str(row.get("path") or "") for row in entries}
                      if all(isinstance(row, dict) for row in entries)
                      else set())
    revisions = {str(row.get("revision") or "") for row in entries}
    if len(revisions) != 1 or not next(iter(revisions), ""):
        raise PreflightError("prior Design evidence is not one committed view")
    prior_revision = next(iter(revisions))
    expected_paths = set(filter(None, _git_output(
        workspace, "ls-tree", "-r", "--name-only", prior_revision,
        "--", "design").splitlines()))
    current_revision = _git_output(workspace, "rev-parse", "HEAD")
    current_paths = set(filter(None, _git_output(
        workspace, "ls-tree", "-r", "--name-only", current_revision,
        "--", "design").splitlines()))
    if (not expected_paths or supplied_paths != expected_paths
            or len(entries) != len(expected_paths)
            or current_paths != expected_paths
            or set(_closed_checkout_paths(
                workspace, "design")) != expected_paths):
        raise PreflightError("prior Design evidence is incomplete")
    verified: list[dict] = []
    for raw in entries:
        if not isinstance(raw, dict):
            raise PreflightError("prior Design evidence is malformed")
        revision = str(raw.get("revision") or "")
        path = str(raw.get("path") or "")
        expected = str(raw.get("sha256") or "")
        if not revision or not path.startswith("design/") or ".." in \
                path.split("/"):
            raise PreflightError("prior Design evidence is malformed")
        data = _git_output(
            workspace, "show", f"{revision}:{path}", binary=True)
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected:
            raise PreflightError(f"prior Design drift detected for {path}")
        current = _git_output(
            workspace, "show", f"{current_revision}:{path}", binary=True)
        checkout_path = os.path.join(workspace, *path.split("/"))
        checkout_size, checkout_digest = _sha256_file(checkout_path)
        if (current != data or checkout_size != len(data)
                or checkout_digest != digest):
            raise PreflightError(f"prior Design drift detected for {path}")
        object_id = _git_output(
            workspace, "rev-parse", f"{revision}:{path}")
        verified.append({"revision": revision, "path": path,
                         "object_id": object_id, "bytes": len(data),
                         "sha256": digest})
    return verified


def _closed_checkout_paths(root: str, relative_root: str) -> list[str]:
    """Return every regular path below one checkout root, without links."""
    absolute_root = os.path.join(root, relative_root)
    if not os.path.isdir(absolute_root) or os.path.islink(absolute_root):
        raise PreflightError(f"canonical {relative_root}/ payload is absent")
    paths: list[str] = []
    for directory, names, filenames in os.walk(
            absolute_root, followlinks=False):
        names.sort()
        filenames.sort()
        for name in names:
            if os.path.islink(os.path.join(directory, name)):
                raise PreflightError(
                    f"canonical {relative_root}/ payload contains a symlink")
        for name in filenames:
            path = os.path.join(directory, name)
            if os.path.islink(path) or not os.path.isfile(path):
                raise PreflightError(
                    f"canonical {relative_root}/ payload is not regular")
            paths.append(os.path.relpath(path, root).replace(os.sep, "/"))
    return sorted(paths)


def _verify_active_plan(workspace: str, revision: str, run_id: str,
                        active_plan: dict) -> dict:
    """Bind approved Plan metadata to the complete committed Plan payload."""
    plan = active_plan if isinstance(active_plan, dict) else {}
    supplied_paths = plan.get("paths")
    approved_revision = str(plan.get("revision") or "")
    approved_entries = plan.get("entries")
    approval_fingerprint = str(plan.get("fingerprint") or "")
    if (plan.get("run_id") != run_id or plan.get("status") != "approved"
            or not approved_revision
            or not isinstance(supplied_paths, list) or not supplied_paths
            or not isinstance(approved_entries, list)
            or any(not isinstance(path, str) or not path.startswith("plan/")
                   or ".." in path.split("/") for path in supplied_paths)):
        raise PreflightError("stale Plan authority is active")

    approval = {
        "run_id": run_id,
        "status": "approved",
        "revision": approved_revision,
        "paths": supplied_paths,
        "entries": approved_entries,
    }
    if approval_fingerprint != _canonical_digest(approval):
        raise PreflightError("stale Plan approval fingerprint is active")

    approved_output = _git_output(
        workspace, "ls-tree", "-r", "--name-only", approved_revision,
        "--", "plan")
    approved_paths = sorted(
        path for path in approved_output.splitlines() if path)
    entry_paths = ([str(row.get("path") or "")
                    for row in approved_entries]
                   if all(isinstance(row, dict) for row in approved_entries)
                   else [])
    if (not approved_paths or sorted(supplied_paths) != approved_paths
            or len(set(supplied_paths)) != len(supplied_paths)
            or sorted(entry_paths) != approved_paths
            or len(set(entry_paths)) != len(entry_paths)):
        raise PreflightError("stale Plan authority is active")

    approved_by_path = {row["path"]: row for row in approved_entries}
    for path in approved_paths:
        row = approved_by_path[path]
        committed = _git_output(
            workspace, "show", f"{approved_revision}:{path}", binary=True)
        if (row.get("object_id") != _git_output(
                workspace, "rev-parse", f"{approved_revision}:{path}")
                or row.get("bytes") != len(committed)
                or row.get("sha256") != hashlib.sha256(
                    committed).hexdigest()):
            raise PreflightError(
                f"stale Plan approval evidence is active for {path}")

    committed_output = _git_output(
        workspace, "ls-tree", "-r", "--name-only", revision, "--", "plan")
    committed_paths = sorted(
        path for path in committed_output.splitlines() if path)
    if (not committed_paths
            or approved_paths != committed_paths
            or _closed_checkout_paths(workspace, "plan") != committed_paths):
        raise PreflightError("stale Plan authority is active")

    entries: list[dict] = []
    for path in committed_paths:
        committed = _git_output(
            workspace, "show", f"{revision}:{path}", binary=True)
        checkout_path = os.path.join(workspace, *path.split("/"))
        size, digest = _sha256_file(checkout_path)
        approved_row = approved_by_path[path]
        if (size != len(committed)
                or digest != hashlib.sha256(committed).hexdigest()
                or digest != approved_row["sha256"]
                or len(committed) != approved_row["bytes"]
                or _git_output(
                    workspace, "rev-parse", f"{revision}:{path}") !=
                approved_row["object_id"]):
            raise PreflightError(f"stale Plan payload is active for {path}")
        entries.append({
            "path": path,
            "object_id": _git_output(
                workspace, "rev-parse", f"{revision}:{path}"),
            "bytes": size,
            "sha256": digest,
        })
    authority = {
        "run_id": run_id,
        "status": "approved",
        "revision": approved_revision,
        "current_revision": revision,
        "paths": committed_paths,
        "entries": entries,
        "approval_fingerprint": approval_fingerprint,
        "stale": False,
    }
    authority["fingerprint"] = _canonical_digest(authority)
    return authority


def _baseline_enforcement(value: dict,
                          advisory_authorization: dict | None) -> dict:
    if not isinstance(value, dict) or value.get("schema") != \
            "taskplane.enforcement-status/v1":
        raise PreflightError("enforcement evidence is absent")
    status = value.get("status")
    if status == "unproven":
        raise PreflightError("enforcement is unproven")
    if status not in {"live", "advisory"}:
        raise PreflightError("enforcement status is invalid")
    evidence_id = str(value.get("evidence_id") or "")
    session_id = str(value.get("session_fingerprint") or "")
    receipt = value.get("receipt_evidence")
    receipt_fields = ("effective_path", "loaded_path", "content_fingerprint",
                      "host_observation", "observed_at",
                      "session_fingerprint")
    if not evidence_id or not session_id or not isinstance(receipt, dict) or \
            any(not receipt.get(field) for field in receipt_fields):
        raise PreflightError("enforcement hook-path receipt is incomplete")
    if receipt.get("session_fingerprint") != session_id:
        raise PreflightError("enforcement receipt belongs to another session")
    receipt_fingerprint = str(receipt.get("content_fingerprint") or "")
    if (len(receipt_fingerprint) != 64
            or any(character not in "0123456789abcdef"
                   for character in receipt_fingerprint.lower())):
        raise PreflightError("enforcement hook-path receipt is incomplete")
    authorization = None
    if status == "advisory":
        required = ("actor", "reason", "scope", "expires_at",
                    "accepted_limitations")
        if (not isinstance(advisory_authorization, dict)
                or any(not advisory_authorization.get(key)
                       for key in required)
                or not isinstance(
                    advisory_authorization.get("accepted_limitations"), list)):
            raise PreflightError(
                "advisory enforcement needs bounded attributable advisory "
                "authorization")
        recorded_actor = str((value.get("advisory") or {}).get("actor") or "")
        if recorded_actor != str(advisory_authorization.get("actor") or ""):
            raise PreflightError(
                "advisory enforcement needs bounded attributable advisory "
                "authorization")
        expires_at = str(advisory_authorization.get("expires_at") or "")
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PreflightError(
                "advisory enforcement authorization expiry is invalid") \
                from exc
        if (expiry.tzinfo is None
                or expiry.astimezone(timezone.utc) <= datetime.now(
                    timezone.utc)):
            raise PreflightError(
                "advisory enforcement authorization is expired")
        authorization = dict(advisory_authorization)
    elif advisory_authorization is not None:
        raise PreflightError("live enforcement must not carry advisory authority")
    host_observation = " ".join(str(
        receipt.get("host_observation") or "").lower().split())
    denies_live_receipt = any(phrase in host_observation for phrase in (
        "no compatible live receipt",
        "no session-compatible hook receipt",
        "live receipt unavailable",
    ))
    if (status == "live" and (
            receipt.get("effective_path") not in {
                "native_effective", "bridge_effective"}
            or denies_live_receipt)):
        raise PreflightError(
            "live enforcement contradicts the hook-path receipt")
    return {"status": status, "label": ("enforced" if status == "live"
                                         else "advisory"),
            "enforced": status == "live", "evidence_id": evidence_id,
            "session_id": session_id, "hook_path_receipt": dict(receipt),
            "advisory_authorization": authorization}


def _graph_content_fingerprint(graph: dict) -> str:
    """Recompute the canonical fingerprint emitted by depgraph._stamp_meta."""
    try:
        graph_material = {
            "files": {
                path: row.get("hash", "")
                for path, row in (graph.get("files") or {}).items()
            },
            "edges": sorted((
                edge["from"], edge["to"], edge["kind"],
                edge.get("source"), edge.get("confidence"))
                for edge in (graph.get("edges") or [])),
        }
        payload = json.dumps(
            graph_material, sort_keys=True,
            separators=(",", ":")).encode("utf-8")
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PreflightError("graph content fingerprint is malformed") from exc
    return hashlib.sha256(payload).hexdigest()


def verify_governance_baseline(
        workspace: str, *, expected_run_id: str,
        trusted_knowledge_manifest: dict, prior_design: list[dict],
        enforcement: dict, active_plan: dict,
        obsolete_run_ids=(), advisory_authorization: dict | None = None) \
        -> dict:
    """Verify and emit the closed R-0006 governance baseline.

    All authority-bearing inputs are checked against canonical Git, locator,
    graph, and external-knowledge facts.  Only the resulting run artifact is
    written; Design and knowledge inputs remain byte-for-byte untouched.
    """
    requested_root = os.path.realpath(os.path.abspath(workspace))
    locator = storage.load_workspace_locator(requested_root)
    if not isinstance(locator, dict):
        raise PreflightError("fresh governed run locator is absent")
    run_id = str(locator.get("run_id") or "")
    if run_id != str(expected_run_id or ""):
        raise PreflightError("workspace locator does not name the fresh run")
    if run_id in {str(value) for value in obsolete_run_ids}:
        raise PreflightError("obsolete run pointer is active")
    root = os.path.realpath(str(locator.get("primary_checkout") or ""))
    if not root or not os.path.isabs(root):
        raise PreflightError("workspace locator has no primary checkout")
    primary_locator = storage.load_workspace_locator(root)
    if (not isinstance(primary_locator, dict)
            or primary_locator.get("run_id") != run_id
            or primary_locator.get("repo_id") != locator.get("repo_id")):
        raise PreflightError("primary checkout has an obsolete run pointer")
    locator = primary_locator
    revision = _git_output(root, "rev-parse", "HEAD")
    branch = _git_output(root, "branch", "--show-current") or None
    if branch != "main":
        raise PreflightError("governance baseline must record the main revision")
    plan_authority = _verify_active_plan(
        root, revision, run_id, active_plan)
    paths = locator.get("paths") or {}
    graph_path = os.path.join(str(paths.get("graph") or ""), "graph.json")
    try:
        with open(graph_path, encoding="utf-8") as handle:
            graph = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PreflightError(f"refreshed graph evidence is unavailable: {exc}") \
            from exc
    graph_meta = graph.get("meta") if isinstance(graph, dict) else None
    fingerprint = str((graph_meta or {}).get("content_fingerprint") or "")
    scanned_revision = str((graph_meta or {}).get("scanned_head") or "")
    if (len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in
                   fingerprint.lower())
            or fingerprint != _graph_content_fingerprint(graph)
            or scanned_revision != revision):
        raise PreflightError("graph is not refreshed at the baseline revision")

    knowledge = _verify_knowledge_manifest(
        locator, trusted_knowledge_manifest)
    design = _verify_prior_design(root, prior_design)
    enforcement_record = _baseline_enforcement(
        enforcement, advisory_authorization)
    record = {
        "schema": _GOVERNANCE_BASELINE_SCHEMA,
        "repository": {"repo_id": locator.get("repo_id"),
                       "repository_key": locator.get("repository_key"),
                       "branch": branch, "revision": revision},
        "run": {"id": run_id, "locator_schema": locator.get("schema"),
                "obsolete_pointer": False},
        "graph": {"fingerprint": fingerprint,
                  "scanned_revision": scanned_revision},
        "plan_authority": plan_authority,
        "prior_design": design,
        "knowledge": knowledge,
        "enforcement": enforcement_record,
    }
    record["fingerprint"] = _canonical_digest(record)
    artifact_root = os.path.realpath(str(paths.get("artifacts") or ""))
    home = os.path.realpath(str(locator.get("home") or ""))
    if not artifact_root or os.path.commonpath((home, artifact_root)) != home:
        raise PreflightError("baseline artifact path escapes canonical storage")
    artifact = os.path.join(
        artifact_root, "baseline", "governance-baseline.json")
    tp.atomic_write_json(artifact, record, sort_keys=True)
    return record


def reconcile_onboarding_checks(checks: list[dict], *, repair,
                                prior_prompt_ids=()) -> dict:
    """Apply the canonical setup matrix without creating prompt loops.

    The caller supplies the actual repair boundary.  Host-policy and external
    states are observations, not approval questions; an authority-required
    check creates one attributable action until its state changes.
    """
    prompted = {str(value) for value in prior_prompt_ids}
    rows: list[dict] = []
    actions: list[dict] = []
    for raw in checks:
        check = recovery.validate_setup_check(raw)
        check_id = check["id"]
        classification = check["classification"]
        if classification == "self-repairable":
            try:
                repaired = repair(dict(check)) is True
            except (OSError, RuntimeError, ValueError) as exc:
                rows.append({**check, "status": "repair_failed",
                             "reason": str(exc)[:400]})
            else:
                rows.append({**check, "status": "repaired" if repaired else
                             "repair_failed"})
        elif classification == "authority-required":
            rows.append({**check, "status": "needs_authority"})
            if check_id not in prompted:
                actions.append({
                    "schema": "taskplane.setup-authority-action/v1",
                    "id": check_id,
                    "authority": check.get("detail") or check_id,
                })
                prompted.add(check_id)
        elif classification == "host-policy":
            rows.append({**check, "status": "waiting_host_policy"})
        else:
            rows.append({**check, "status": "waiting_external"})
    if actions:
        status = "needs_user"
    elif any(row["status"] in {"repair_failed", "waiting_host_policy",
                               "waiting_external", "needs_authority"}
             for row in rows):
        status = "waiting"
    else:
        status = "ready"
    return {
        "schema": "taskplane.onboarding-recovery/v1",
        "status": status,
        "checks": rows,
        "actions": actions,
        "prompt_ids": sorted(prompted),
    }


def new_run_id() -> str:
    return uuid.uuid4().hex


def _bootstrap_key(workspace: str, spec: str) -> str:
    material = (os.path.realpath(workspace) + "\0" + str(spec)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:24]


def _bootstrap_path(workspace: str, spec: str) -> str:
    return os.path.join(tp.tp_dir(os.path.realpath(workspace)), "preflight",
                        _bootstrap_key(workspace, spec) + ".json")


def bootstrap_response(row: dict) -> dict:
    return {"schema": "taskplane.preflight/v1",
            "run_id": row["run_id"], "status": "needs_user",
            "action": dict(row["action"]),
            "reason": str(row.get("reason") or "")}


def find_bootstrap(workspace: str, *, spec: str | None = None,
                   run_id: str | None = None) -> dict | None:
    """Find the pre-store user gate that survives denied external storage.

    This record lives in the caller workspace because the canonical RunStore
    is the resource whose permission is being requested.  It contains no
    credentials, only the sealed retry identity and user action.
    """
    root = os.path.join(tp.tp_dir(os.path.realpath(workspace)), "preflight")
    paths = ([_bootstrap_path(workspace, spec)] if spec is not None else
             [os.path.join(root, name) for name in sorted(os.listdir(root))
              if name.endswith(".json")] if os.path.isdir(root) else [])
    for path in paths:
        row = tp.load_json(path, default=None, what="preflight bootstrap")
        if not isinstance(row, dict) or row.get("schema") != _BOOTSTRAP_SCHEMA:
            continue
        if run_id is None or row.get("run_id") == run_id:
            return {**row, "_path": path}
    return None


def persist_storage_pause(workspace: str, *, spec: str, host: dict,
                          run_id: str, detail: str) -> dict:
    action = RepositoryPreflight._action(
        run_id, kind="authorize_storage_root",
        prompt=("taskPlane needs access to its external repository/run "
                "storage. Approve access and resume this review; a repeated "
                "review start will remain paused."),
        detail=detail, choices=("approve", "retry", "cancel"))
    row = {"schema": _BOOTSTRAP_SCHEMA, "run_id": str(run_id),
           "status": "needs_user", "workspace": os.path.realpath(workspace),
           "spec": str(spec), "host": dict(host), "reason": detail,
           "action": action}
    tp.atomic_write_json(_bootstrap_path(workspace, spec), row, sort_keys=True)
    return bootstrap_response(row)


def authorize_bootstrap(workspace: str, *, run_id: str, action_id: str,
                        response: str, approved_by: str) -> dict:
    row = find_bootstrap(workspace, run_id=run_id)
    if not row:
        raise PreflightError("no pending storage authorization matches run")
    action = row.get("action") or {}
    if action.get("action_id") != action_id:
        raise PreflightError("pending user action does not match")
    if response not in action.get("choices", []):
        raise PreflightError(f"response is not allowed: {response}")
    if response == "cancel":
        tp.safe_remove(row["_path"])
        return {"schema": "taskplane.preflight/v1", "run_id": run_id,
                "status": "cancelled"}
    durable = {key: value for key, value in row.items() if key != "_path"}
    durable["status"] = "authorized"
    durable["authorization"] = {"response": response, "by": approved_by}
    tp.atomic_write_json(row["_path"], durable, sort_keys=True)
    return durable


def clear_bootstrap(row: dict) -> None:
    path = row.get("_path")
    if isinstance(path, str):
        tp.safe_remove(path)


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

    def _waiting(self, run_id: str, manifest: dict, *, reason: str,
                 detail: str, recovery_record: dict | None = None) -> dict:
        preflight_state = {"status": "waiting", "reason": str(reason),
                           "detail": str(detail)[:1600],
                           "pending_action": None}
        if recovery_record is not None:
            preflight_state["recovery"] = dict(recovery_record)
        updated = self.store.commit(
            run_id, expected_revision=int(manifest["revision"]),
            changes={"status": "waiting_external",
                     "preflight": preflight_state})
        return {"schema": "taskplane.preflight/v1", "run_id": run_id,
                "status": "waiting", "reason": str(reason),
                "detail": str(detail)[:1600],
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
            def acquire():
                if parsed.get("kind") == "pr":
                    return self.acquirer.acquire_pr(identity, parsed)
                return self.acquirer.acquire_repository(identity, parsed)

            consolidated = os.environ.get("TASKPLANE_CONSOLIDATED_FLOW", "") \
                .strip().lower() in {"1", "true", "yes", "on"}
            try:
                if consolidated:
                    preparation = repository.acquire_with_recovery(acquire)
                    if preparation["status"] == "ready":
                        acquired = preparation["value"]
                    elif preparation["status"] == "needs_user":
                        command = (["gh", "auth", "login", "--web"]
                                   if gh.get("present") else [])
                        return self._needs_user(run, manifest, self._action(
                            run, kind="authenticate_repository",
                            prompt=("Repository authentication is required. "
                                    "Sign in or authorize access, then "
                                    "taskPlane will resume this same run."),
                            detail=preparation["detail"], command_argv=command,
                            choices=("approve", "retry", "cancel")))
                    else:
                        return self._waiting(
                            run, manifest, reason=preparation["reason"],
                            detail=preparation["detail"],
                            recovery_record=preparation.get("recovery"))
                else:
                    acquired = acquire()
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
