"""Controller for the current stateless phase flow.

Product -> Design -> Plan -> Build -> Evaluate -> EM Review -> sign-off ->
Retro. Fix is an isolated phase when Evaluate requests a correction. Plan
approval and final sign-off remain human decisions.

The v4 RunStore aggregate owns transitions. Workers receive only verified
phase packages; workflow files and dashboards are derived projections.
Independent tasks can progress in separate worktrees before joining at EM.
"""

from __future__ import annotations
import os
import sys

# Direct CLI imports resolve the package from this engine's own directory.
if not __package__:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from taskplane import agent_runtime, build_c, design_host_transport, loop_recovery, retro, review

from taskplane import phase_records, stage_artifacts

from collections.abc import Iterable, Mapping
import base64
import copy
import contextlib
import contextvars
import hashlib
import hmac
import json
import math
import re
import secrets
import shlex
import stat
import time

import authority as authority_engine
import build_c
import checkpoint
import command_wave
import governed_commands
import depgraph
import evaluation_output
import evaluator_health
import host_capabilities
import kb
import lens as lens_router
import loop_status
import loop_recovery
import progress as progress_engine
import retro as retro_engine
import requirements as reqs
import review_retry
import runtime_eval
import review_session as review_session_engine
import review_dor
import storage as runtime_storage
import spend
import taskplane_lite as tp
import yield_meter
from taskplane import run_context, phase_harness, stage_loop, dispatch, gates

if __package__:
    from . import brief_projection
    from . import delivery_policy
    from . import dispatch_telemetry
    from . import em_outage
    from . import evaluation_output as evaluation_output
    from . import failure_routing
    from . import lens_route_policy
    from . import native_session_meter
    from . import owned_cleanup
    from . import settings as operational_settings
    from . import plan_topology
    from . import release_evidence
    from . import run_artifacts
    from . import run_store as run_store_engine
    from . import root_seed
    from . import test_strategy
    from . import producer_observation as producer_observation_policy
    from . import terminal_truth
    from . import wave_metrics
    from .delivery_ports import SystemClock
else:  # pragma: no cover - direct CLI module loading
    import brief_projection
    import delivery_policy
    import dispatch_telemetry
    import em_outage
    import failure_routing
    import lens_route_policy
    import native_session_meter
    import owned_cleanup
    from taskplane import settings as operational_settings
    import plan_topology
    import release_evidence
    import run_artifacts
    import run_store as run_store_engine
    import root_seed
    import test_strategy
    import producer_observation as producer_observation_policy
    import terminal_truth
    import wave_metrics
    from delivery_ports import SystemClock

LOOP_FILE = "loop.json"
REVIEW_RAW_DIFF_RETENTION_SECONDS = 24 * 60 * 60
REVIEW_RAW_DIFF_MAX_ARTIFACTS = 32
REVIEW_RAW_DIFF_MAX_BYTES = 16 * 1024 * 1024


def _retained_review_diff_payload(
    *,
    base: str,
    files: list[str],
    patch: str,
    now: float | None = None,
    run_id: str | None = None,
    review_id: str | None = None,
) -> dict:
    created_at = float(time.time() if now is None else now)
    review_identity = str(
        review_id
        or hashlib.sha256(
            json.dumps(
                {"base": str(base), "files": list(files)}, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    return {
        "schema": "taskplane.retained-review-diff/v1",
        "base": str(base),
        "files": list(files),
        "patch": str(patch),
        "retention": {
            "schema": "taskplane.raw-diff-retention/v1",
            "created_at": created_at,
            "expires_at": created_at + REVIEW_RAW_DIFF_RETENTION_SECONDS,
            "raw_fields": ["patch"],
            "delete_on_expiry": True,
            "run_id": str(run_id or "unattributed"),
            "review_id": review_identity,
        },
    }


def _review_diff_retention_time(store, observed_at: float) -> float:
    """Advance a durable high-water mark so clock rollback cannot extend TTL."""
    path = os.path.join(store.root, ".diff-retention-watermark.json")
    prior = observed_at
    try:
        marker = tp.load_json(path, default=None, what="raw diff retention watermark")
        if marker is not None:
            if marker.get("schema") != "taskplane.raw-diff-watermark/v1":
                raise ValueError("unsupported raw diff retention watermark")
            prior = float(marker["observed_at"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Damage can conservatively expire artifacts, never prolong them.
        prior = float("inf")
    high_water = max(float(observed_at), prior)
    if math.isfinite(high_water):
        tp.atomic_write_json(
            path,
            {
                "schema": "taskplane.raw-diff-watermark/v1",
                "observed_at": high_water,
            },
            sort_keys=True,
        )
    return high_water


def _raw_diff_entry(path: str, fingerprint: str, observed_at: float) -> dict:
    """Verify immutable bytes and the closed, creation-bound TTL schema."""
    with open(path, "rb") as source:
        raw = source.read(REVIEW_RAW_DIFF_MAX_BYTES + 1)
    if len(raw) > REVIEW_RAW_DIFF_MAX_BYTES or hashlib.sha256(raw).hexdigest() != fingerprint:
        raise ValueError("content-addressed raw diff mismatch")
    payload = json.loads(raw.decode("utf-8"))
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    retention = payload.get("retention") if isinstance(payload, dict) else None
    required = {
        "schema",
        "created_at",
        "expires_at",
        "raw_fields",
        "delete_on_expiry",
        "run_id",
        "review_id",
    }
    if (
        canonical != raw
        or not isinstance(retention, dict)
        or set(retention) != required
        or retention.get("schema") != "taskplane.raw-diff-retention/v1"
        or retention.get("raw_fields") != ["patch"]
        or retention.get("delete_on_expiry") is not True
    ):
        raise ValueError("raw diff retention schema is invalid")
    created_at = float(retention["created_at"])
    expires_at = float(retention["expires_at"])
    if (
        not math.isfinite(created_at)
        or not math.isfinite(expires_at)
        or expires_at != created_at + REVIEW_RAW_DIFF_RETENTION_SECONDS
        or observed_at < created_at
        or not str(retention.get("run_id") or "").strip()
        or not str(retention.get("review_id") or "").strip()
    ):
        raise ValueError("raw diff expiry or attribution is invalid")
    return {
        "fingerprint": fingerprint,
        "path": path,
        "bytes": len(raw),
        "created_at": created_at,
        "expires_at": expires_at,
        "run_id": str(retention["run_id"]),
        "review_id": str(retention["review_id"]),
    }


def _purge_raw_diff(path: str) -> None:
    """Stage one validated private artifact before its irreversible purge."""
    directory = os.path.dirname(path)
    staging = os.path.join(directory, ".privacy-purge-" + secrets.token_hex(12))
    os.replace(path, staging)
    try:
        os.unlink(staging)
    finally:
        if os.path.exists(staging):
            os.replace(staging, path)


def _purge_raw_diff_derivatives(store, fingerprint: str) -> None:
    """Purge only fingerprint-bound copies in known private derivative roots."""
    for dirname in ("diff-derived", "derived-diff", "diff-pre-upgrade"):
        directory = os.path.join(store.root, dirname)
        if not os.path.isdir(directory) or os.path.islink(directory):
            continue
        for name in sorted(os.listdir(directory)):
            path = os.path.join(directory, name)
            if fingerprint in name and os.path.isfile(path) and not os.path.islink(path):
                _purge_raw_diff(path)
        tp._fsync_directory(directory)


def _enforce_review_diff_retention_locked(
    store,
    *,
    observed_at: float,
    keep_fingerprint: str | None = None,
    purge_fingerprint: str | None = None,
) -> dict:
    directory = os.path.join(store.root, "diff")
    if not os.path.isdir(directory):
        return {"removed": 0, "retained": 0, "purged": []}
    observed_at = _review_diff_retention_time(store, observed_at)
    entries = []
    invalid = []
    for name in sorted(os.listdir(directory)):
        if not re.fullmatch(r"[0-9a-f]{64}\.json", name):
            continue
        fingerprint = name[:-5]
        path = os.path.join(directory, name)
        if os.path.islink(path):
            invalid.append(
                {
                    "fingerprint": fingerprint,
                    "path": path,
                    "run_id": "unknown",
                    "review_id": "unknown",
                    "reason": "symlink",
                }
            )
            continue
        try:
            entries.append(_raw_diff_entry(path, fingerprint, observed_at))
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
            invalid.append(
                {
                    "fingerprint": fingerprint,
                    "path": path,
                    "run_id": "unknown",
                    "review_id": "unknown",
                    "reason": "invalid-or-tampered",
                }
            )

    purged = []
    retained = 0
    retained_bytes = 0
    ordered = sorted(
        entries,
        key=lambda item: (
            item["fingerprint"] == keep_fingerprint,
            item["created_at"],
            item["fingerprint"],
        ),
        reverse=True,
    )
    for row in invalid + ordered:
        reason = row.get("reason")
        if row["fingerprint"] == purge_fingerprint:
            reason = "review-complete"
        elif not reason and row["expires_at"] <= observed_at:
            reason = "expired"
        elif not reason and retained >= REVIEW_RAW_DIFF_MAX_ARTIFACTS:
            reason = "count-bound"
        elif not reason and retained_bytes + row["bytes"] > REVIEW_RAW_DIFF_MAX_BYTES:
            reason = "byte-bound"
        if not reason:
            retained += 1
            retained_bytes += row["bytes"]
            continue
        _purge_raw_diff(row["path"])
        _purge_raw_diff_derivatives(store, row["fingerprint"])
        purged.append(
            {key: row[key] for key in ("fingerprint", "run_id", "review_id")} | {"reason": reason}
        )
    if purged:
        tp._fsync_directory(directory)
    return {"removed": len(purged), "retained": retained, "purged": purged}


def enforce_review_diff_retention(
    workspace: str,
    *,
    store,
    now: float | None = None,
    keep_fingerprint: str | None = None,
    purge_fingerprint: str | None = None,
    _lock_held: bool = False,
) -> dict:
    """Purge expired/excess/tampered raw diffs under one store lock."""
    del workspace
    observed_at = float(time.time() if now is None else now)
    action = lambda: _enforce_review_diff_retention_locked(
        store,
        observed_at=observed_at,
        keep_fingerprint=keep_fingerprint,
        purge_fingerprint=purge_fingerprint,
    )
    if _lock_held:
        result = action()
    else:
        with tp.file_lock(os.path.join(store.root, ".diff-retention")):
            result = action()
    return {
        **result,
        "retention_seconds": REVIEW_RAW_DIFF_RETENTION_SECONDS,
        "max_artifacts": REVIEW_RAW_DIFF_MAX_ARTIFACTS,
        "max_bytes": REVIEW_RAW_DIFF_MAX_BYTES,
    }


def store_retained_review_diff(
    workspace: str, *, store, payload: dict, now: float | None = None
) -> dict:
    """Sweep and put under one lock shared by every concurrent reviewer."""
    observed_at = float(time.time() if now is None else now)
    with tp.file_lock(os.path.join(store.root, ".diff-retention")):
        enforce_review_diff_retention(workspace, store=store, now=observed_at, _lock_held=True)
        reference = store.put("diff", payload)
        enforce_review_diff_retention(
            workspace,
            store=store,
            now=observed_at,
            keep_fingerprint=reference["fingerprint"],
            _lock_held=True,
        )
    return reference


def read_retained_review_diff(
    workspace: str, *, store, reference: dict, now: float | None = None
) -> dict:
    """Sweep and verify immediately before a governed raw-diff read."""
    with tp.file_lock(os.path.join(store.root, ".diff-retention")):
        enforce_review_diff_retention(
            workspace,
            store=store,
            now=now,
            keep_fingerprint=str(reference.get("fingerprint") or ""),
            _lock_held=True,
        )
        return store.read(reference)


def project_next_action_for_host(*args, **kwargs):
    return dispatch.project_next_action_for_host(sys.modules[__name__], *args, **kwargs)


def stamp_plan_delivery_mode(
    state: dict,
    declaration: Mapping[str, object],
    *,
    plan_fingerprint: str,
    source_sha: str,
    predecessor_fingerprint: str | None = None,
) -> dict:
    """Seal one explicit Plan delivery declaration into loop state.

    Validation happens before mutation so a malformed or contradictory mode
    cannot leave partial dispatch authority behind.
    """
    if not isinstance(state, dict):
        raise delivery_policy.DeliveryPolicyError(
            "loop state must be mutable for Plan delivery mode"
        )
    receipt = delivery_policy.validate_plan_mode(
        declaration,
        plan_fingerprint=plan_fingerprint,
        source_sha=source_sha,
        predecessor_fingerprint=predecessor_fingerprint,
    )
    requirement_id = str(state.get("requirement_id") or "").strip()
    if requirement_id and receipt["requirement"] != requirement_id:
        raise delivery_policy.DeliveryPolicyError(
            "delivery-mode receipt requirement does not match the loop"
        )
    state["delivery_mode_receipt"] = receipt
    return receipt


def _validated_delivery_mode(state: Mapping[str, object]) -> dict | None:
    receipt = state.get("delivery_mode_receipt")
    if receipt is None:
        return None
    if not isinstance(receipt, Mapping):
        raise delivery_policy.DeliveryPolicyError("delivery-mode receipt must be a mapping")
    return delivery_policy.validate_delivery_mode_receipt(receipt)


def _plan_delivery_mode_from_file(ws: str, state: dict, *, apply: bool) -> dict | None:
    """Consume the current Plan delivery declaration."""
    if not state.get("design_fingerprint"):
        raise delivery_policy.DeliveryPolicyError("Plan requires current Design authority")
    required_declaration = {"delivery_mode", "automatic_lenses", "plan_authority"}
    path = os.path.join(ws, "plan", "tasks.json")
    try:
        with open(path, encoding="utf-8") as stream:
            plan = json.load(stream)
    except (OSError, ValueError) as exc:
        raise delivery_policy.DeliveryPolicyError(
            "current Plan declaration is unavailable"
        ) from exc
    source_plan = plan
    if isinstance(plan, dict) and not required_declaration.issubset(plan):
        context = _phase_bridge_context(ws, state)
        if context is not None and context["stage"]["stage_kind"] == "plan":
            _phase_bridge_gate_check(ws, state)
            build = context["registry"].admit("build", ()).to_dict()
            if build["working_lenses"] or build["evaluation_lenses"]:
                raise delivery_policy.DeliveryPolicyError(
                    "selected Build definition is not zero-lens"
                )
            # These are a projection of current authenticated authority, not
            # worker-authored permission and not a rewrite of the Plan file.
            plan = dict(plan)
            plan.setdefault("delivery_mode", "build")
            plan.setdefault("automatic_lenses", [])
            plan.setdefault(
                "plan_authority",
                "phase:"
                + context["run_id"]
                + ":"
                + context["stage"]["stage_id"]
                + ":"
                + context["stage"]["authority"]["authority_fingerprint"],
            )
    if not isinstance(plan, dict) or not required_declaration.issubset(plan):
        raise delivery_policy.DeliveryPolicyError(
            "Design-governed Plan requires delivery_mode=build, "
            "automatic_lenses=[], and plan_authority"
        )
    declaration = {
        "requirement": plan.get("requirement") or state.get("requirement_id"),
        "delivery_mode": plan.get("delivery_mode"),
        "automatic_lenses": plan.get("automatic_lenses"),
        "plan_authority": plan.get("plan_authority"),
    }
    if declaration["delivery_mode"] != "build":
        raise delivery_policy.DeliveryPolicyError(
            "Design-governed Plan requires delivery_mode=build"
        )
    if declaration["automatic_lenses"] != []:
        raise delivery_policy.DeliveryPolicyError(
            "Design-governed Plan requires automatic_lenses=[]"
        )
    plan_fingerprint = hashlib.sha256(
        json.dumps(
            source_plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    source_sha = str(tp.git_head(ws) or "")
    prior = _validated_delivery_mode(state)
    if prior and all(
        (
            prior["requirement"] == declaration["requirement"],
            prior["plan_fingerprint"] == plan_fingerprint,
            prior["source_sha"] == source_sha,
            prior["mode"] == declaration["delivery_mode"],
            prior["automatic_lenses"] == declaration["automatic_lenses"],
            prior["plan_authority"] == declaration["plan_authority"],
        )
    ):
        return prior
    receipt = delivery_policy.validate_plan_mode(
        declaration,
        plan_fingerprint=plan_fingerprint,
        source_sha=source_sha,
        predecessor_fingerprint=(prior or {}).get("fingerprint"),
    )
    if apply:
        state["delivery_mode_receipt"] = receipt
    return receipt


def build_dispatch_lens_routing(*args, **kwargs):
    return dispatch.build_dispatch_lens_routing(sys.modules[__name__], *args, **kwargs)


_FOCUSED_STAGE_LENSES = {
    "product": frozenset(
        {
            "product",
            "design",
            "security",
            "privacy-compliance",
            "accessibility",
            "i18n",
            "mobile",
            "cost-finops",
            "time-to-market",
            "services-selection",
        }
    ),
    "design": frozenset(
        {
            "solution-design",
            "architecture",
            "integrability",
            "security",
            "privacy-compliance",
            "data-safety",
            "scalability",
            "tradeoffs",
            "services-selection",
            "testability",
            "devops",
            "dba",
            "sre",
        }
    ),
    "plan": frozenset(
        {
            "architecture",
            "project-management",
            "testability",
            "security",
            "cost-finops",
            "integrability",
            "devops",
            "data-safety",
            "privacy-compliance",
        }
    ),
}
_FOCUSED_STAGE_KEYWORDS = {
    "architecture": ("architecture", "canonical", "control plane", "modular"),
    "backend": ("loader", "orchestrator", "state machine", "typed"),
    "frontend": ("browser", "dashboard", "html", "css", "visual"),
    "qa": ("test strategy", "regression", "test suite", "failure"),
    "testability": ("fixture", "selector", "test design", "test suite"),
    "security": ("auth", "credential", "permission", "security", "trust"),
    "privacy-compliance": ("personal data", "privacy", "retention"),
    "data-safety": ("backup", "data loss", "migration", "storage"),
    "integrability": (" api ", "contract", "integration", "interface"),
    "scalability": ("capacity", "scale", "throughput"),
    "performance": ("latency", "performance", "runtime"),
    "accessibility": ("accessibility", "keyboard", "screen reader"),
    "i18n": ("i18n", "locale", "translation"),
    "mobile": ("android", "ios", "mobile"),
    "cost-finops": ("budget", "cost", "spend", "token"),
    "time-to-market": ("deadline", "launch", "time to market"),
    "services-selection": ("provider", "service", "vendor"),
    "tradeoffs": ("alternative", "tradeoff", "trade-off"),
    "devops": ("deploy", "packaging", "pipeline", "release"),
    "dba": (" sql ", "database", "index", "relational query"),
    "sre": (
        "availability",
        "crash",
        "failure",
        "incident",
        "operational",
        "recovery",
        "retry",
        "rollback",
    ),
}


def _focused_stage_route(*args, **kwargs):
    return dispatch._focused_stage_route(sys.modules[__name__], *args, **kwargs)


def _copy_json(value: object) -> object:
    """Return a detached canonical JSON value for action payloads."""
    return json.loads(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    )


def _focused_plan_inputs(*args, **kwargs):
    return dispatch._focused_plan_inputs(sys.modules[__name__], *args, **kwargs)


def _focused_stage_evidence(*args, **kwargs):
    return dispatch._focused_stage_evidence(sys.modules[__name__], *args, **kwargs)


def _design_input_fingerprint(ws: str) -> str:
    """Identify Design inputs while excluding this stage's own outputs."""
    head = str(tp.git_head(ws) or "").strip()
    if not head:
        raise ValueError("Design input fingerprint requires git HEAD")
    digest = hashlib.sha256(b"taskplane.design-input/v1\0" + head.encode())
    paths = [path for path in tp.changed_files(ws, head) if not path.startswith("design/")]
    for relative in sorted(set(paths)):
        if (
            os.path.isabs(relative)
            or relative == ".."
            or relative.startswith("../")
            or "/../" in relative
        ):
            raise ValueError("Design input fingerprint found an unsafe path")
        full = os.path.join(ws, relative)
        digest.update(b"\0path\0" + relative.encode("utf-8", errors="surrogateescape"))
        try:
            info = os.lstat(full)
            digest.update(f"\0mode:{info.st_mode:o}\0size:{info.st_size}\0".encode())
            if os.path.islink(full):
                digest.update(
                    b"symlink\0" + os.readlink(full).encode("utf-8", errors="surrogateescape")
                )
            elif os.path.isfile(full):
                with open(full, "rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
        except FileNotFoundError:
            digest.update(b"\0deleted\0")
    return digest.hexdigest()


def _run_artifact_root(ws: str, state: Mapping[str, object]) -> str:
    """Resolve this run's private artifact root from canonical run identity."""
    run_id = str(state.get("run_id") or "").strip()
    if not run_id:
        raise run_artifacts.RunArtifactError("run artifacts require an active run id")
    locator = runtime_storage.load_workspace_locator(ws)
    if isinstance(locator, Mapping):
        if locator.get("run_id") != run_id:
            raise run_artifacts.RunArtifactError("workspace locator belongs to another run")
    identity = runtime_storage.resolve_repository_identity(ws)
    if isinstance(locator, Mapping) and locator.get("repo_id") != identity.repo_id:
        raise run_artifacts.RunArtifactError("workspace locator belongs to another repository")
    # Host dispatch and loop composition share this exact storage authority.
    # A workspace locator proves identity but cannot redirect evidence to a
    # second root.
    store = _artifact_owner_store(ws)
    manifest = store.load(run_id)
    layout = runtime_storage.resolve_layout(identity, home=store.home, run_id=run_id)
    repository = manifest.get("repository") or {}
    root = str((manifest.get("paths") or {}).get("artifacts") or "")
    owner = os.path.realpath(str((locator or {}).get("primary_checkout") or ws))
    if (
        repository.get("repo_id") != identity.repo_id
        or repository.get("checkout") != owner
        or root != layout.artifact_root
    ):
        raise run_artifacts.RunArtifactError("canonical run artifact owner is foreign")
    return os.path.realpath(root)


def _artifact_owner_store(ws: str):
    """Use only the run store selected by this workspace's explicit locator."""
    locator = runtime_storage.load_workspace_locator(ws)
    if locator is None:
        raise run_artifacts.RunArtifactError("run artifacts require an explicit workspace locator")
    return run_store_engine.RunStore(home=locator["home"])


def _ensure_run_artifact_parent(ws: str, state: Mapping[str, object]) -> str:
    """Authenticate the existing v4 artifact owner; never synthesize a run."""
    run_id = str(state.get("run_id") or "").strip()
    if not run_id:
        raise run_artifacts.RunArtifactError("run artifacts require an active run id")
    return _run_artifact_root(ws, state)


def _ensure_run_artifacts(
    ws: str,
    state: Mapping[str, object],
    *,
    settings_digest: str,
    stage_instance_id: str,
    candidate_fingerprint: str,
    requirement_id: str,
    requirement_fingerprint: str,
    stage_id: str = "design",
) -> tuple[str, dict, dict]:
    """Create or authenticate the one immutable artifact manifest binding."""
    run_id = str(state.get("run_id") or "").strip()
    locator = runtime_storage.load_workspace_locator(ws)
    identity = runtime_storage.resolve_repository_identity(ws)
    if isinstance(locator, Mapping) and locator.get("repo_id") != identity.repo_id:
        raise run_artifacts.RunArtifactError("workspace locator belongs to another repository")
    repository_id = identity.repo_id
    candidate = {
        "id": f"{requirement_id or 'unattached'}@{tp.git_head(ws)}",
        "fingerprint": candidate_fingerprint,
        "revision": str(tp.git_head(ws) or ""),
        "source_tree": str(tp._run(["git", "rev-parse", "HEAD^{tree}"], cwd=ws).stdout.strip()),
        "requirement": requirement_id,
        "requirement_fingerprint": requirement_fingerprint,
        "goal_fingerprint": hashlib.sha256(
            str(state.get("goal") or "").encode("utf-8")
        ).hexdigest(),
    }
    binding = run_artifacts.create_binding(
        repository_id=repository_id,
        run_id=run_id,
        stage_id=stage_id,
        stage_instance_id=stage_instance_id,
        candidate=candidate,
        settings_digest=settings_digest,
        source_fingerprint=hashlib.sha256(
            json.dumps(
                {
                    "candidate": candidate,
                    "baseline": str(state.get("baseline") or ""),
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
    )
    root = _ensure_run_artifact_parent(ws, state)
    manifest_path = os.path.join(root, run_artifacts.MANIFEST_NAME)
    if not os.path.exists(manifest_path):
        run_artifacts.create_manifest(root, binding=binding)
    manifest = run_artifacts.load_manifest(root)
    if manifest.get("binding") != binding:
        raise run_artifacts.RunArtifactError(
            "run artifact manifest belongs to another current binding"
        )
    # The state locator is immutable identity, not a snapshot of manifest
    # contents.  Verification is deliberately refreshed on every replay; its
    # fingerprint naturally changes whenever an owned artifact is appended.
    # The complete immutable owner remains in ``run_artifact_binding``.
    run_artifacts.verify_manifest(root, expected_binding=binding)
    reference = run_artifacts.manifest_locator_reference()
    return root, binding, reference


def _ensure_owned_cleanup_manifest(
    ws: str, artifact_root: str, artifact_binding: Mapping[str, object]
) -> str:
    """Initialize the one run-owned after-run cleanup authority."""
    run_root = os.path.dirname(os.path.realpath(artifact_root))
    path = os.path.join(run_root, "cleanup", "owned-resources.json")
    evidence_root = os.path.join(run_root, "evidence", "cleanup")
    workspace_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "repository_id": artifact_binding.get("repository_id"),
                "workspace": os.path.realpath(ws),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    expected_owner = {
        "repository_id": str(artifact_binding.get("repository_id") or ""),
        "workspace_fingerprint": workspace_fingerprint,
        "settings_digest": str(artifact_binding.get("settings_digest") or ""),
        "run_id": str(artifact_binding.get("run_id") or ""),
        "task_id": "governed-run",
        "attempt": 1,
    }
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        owned_cleanup.create_manifest(
            path, **expected_owner, evidence_root=evidence_root, durable_artifacts=artifact_root
        )
    manifest = owned_cleanup.load_manifest(path)
    if manifest.get("owner") != expected_owner:
        raise owned_cleanup.OwnedCleanupError(
            "owned cleanup manifest belongs to another governed run"
        )
    if manifest.get("durable_artifacts") is None:
        owned_cleanup.bind_durable_artifacts(path, artifact_root)
    return path


def _prepare_run_control_plane(ws: str, state: Mapping[str, object]) -> dict:
    """Create or authenticate the one whole-run evidence/cleanup boundary.

    Product, Design and Plan can each be the first governed stage.  The run
    candidate is therefore minted once from the bytes present at ``init`` and
    remains immutable; later Design input freshness is a separate binding.
    """
    step = str(state.get("run_start_step") or state.get("step") or "")
    stage_id = _LOOP_STAGE_KINDS.get(step)
    if stage_id not in {"product", "design", "plan"}:
        raise run_artifacts.RunArtifactError(
            "run control-plane initialization requires Product, Design, or Plan"
        )
    effective = operational_settings.load_settings(environment=os.environ)
    requirement_id = str(state.get("requirement_id") or "").strip()
    requirement_fingerprint = _dc.requirement_fingerprint(ws, requirement_id)
    existing_binding = state.get("run_artifact_binding")
    candidate_fingerprint = str(
        state.get("run_candidate_fingerprint")
        or (
            (existing_binding.get("candidate") or {}).get("fingerprint")
            if isinstance(existing_binding, Mapping)
            else ""
        )
        or _design_input_fingerprint(ws)
    )
    stage_instance_id = str(
        state.get("run_stage_instance_id")
        or f"{stage_id}-"
        + hashlib.sha256(
            f"{state.get('run_id')}:{candidate_fingerprint}:{effective.digest}".encode("utf-8")
        ).hexdigest()[:24]
    )
    if isinstance(existing_binding, Mapping):
        # Product attaches a requirement after startup. That changes the
        # Design input, not the immutable owner of the whole run's artifacts.
        root = _ensure_run_artifact_parent(ws, state)
        manifest = run_artifacts.load_manifest(root)
        binding = manifest["binding"]
        candidate = binding.get("candidate") or {}
        expected = run_artifacts.create_binding(
            repository_id=runtime_storage.resolve_repository_identity(ws).repo_id,
            run_id=str(state.get("run_id") or ""),
            stage_id=stage_id,
            stage_instance_id=stage_instance_id,
            candidate=candidate,
            settings_digest=effective.digest,
            source_fingerprint=hashlib.sha256(
                json.dumps(
                    {
                        "candidate": candidate,
                        "baseline": str(state.get("baseline") or ""),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
        )
        if (
            binding != existing_binding
            or binding != expected
            or candidate.get("fingerprint") != candidate_fingerprint
            or candidate.get("goal_fingerprint")
            != hashlib.sha256(str(state.get("goal") or "").encode("utf-8")).hexdigest()
        ):
            raise run_artifacts.RunArtifactError(
                "run control-plane binding changed at run_artifact_binding"
            )
        run_artifacts.verify_manifest(root, expected_binding=binding)
        reference = run_artifacts.manifest_locator_reference()
    else:
        root, binding, reference = _ensure_run_artifacts(
            ws,
            state,
            settings_digest=effective.digest,
            stage_instance_id=stage_instance_id,
            candidate_fingerprint=candidate_fingerprint,
            requirement_id=requirement_id,
            requirement_fingerprint=requirement_fingerprint,
            stage_id=stage_id,
        )
    cleanup_manifest = _ensure_owned_cleanup_manifest(ws, root, binding)
    fields = {
        "run_start_step": step,
        "run_stage_instance_id": stage_instance_id,
        "run_candidate_fingerprint": candidate_fingerprint,
        "settings_digest": effective.digest,
        "run_artifacts": reference,
        "run_artifact_binding": binding,
        "owned_cleanup_manifest": cleanup_manifest,
    }
    for key, value in fields.items():
        if key in state and state.get(key) != value:
            raise run_artifacts.RunArtifactError(f"run control-plane binding changed at {key}")
    return fields


def _prepare_design_control_plane(ws: str, state: Mapping[str, object]) -> tuple[dict, object]:
    """Produce and persist the mandatory, settings-bound Design inputs."""
    run_id = str(state.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("Design requires its existing run identity")
    effective = operational_settings.load_settings(environment=os.environ)
    run_control = _prepare_run_control_plane(ws, state)
    catalog = lens_router.load_catalog()
    catalog_fingerprint = lens_route_policy.catalog_fingerprint(list(catalog.get("lenses") or []))
    catalog_ids = [
        str(row.get("id") or "")
        for row in catalog.get("lenses") or []
        if isinstance(row, Mapping) and row.get("id")
    ]
    policy = effective.lenses.policy_for("design", catalog_ids=catalog_ids)
    requirement_id = str(state.get("requirement_id") or "").strip()
    requirement = reqs.get_requirement(ws, requirement_id) if requirement_id else None
    requirement_fingerprint = _dc.requirement_fingerprint(ws, requirement_id)
    input_fingerprint = _design_input_fingerprint(ws)
    artifact_binding = run_control["run_artifact_binding"]
    candidate_fingerprint = str((artifact_binding.get("candidate") or {}).get("fingerprint") or "")
    context_files = (
        ((requirement or {}).get("context_files") or []) if isinstance(requirement, Mapping) else []
    )
    receipt = depgraph.prepare_design_decomposition(
        ws, context_files, settings_digest=effective.digest
    )
    binding = {
        "schema": "taskplane.design-control-plane-binding/v1",
        "run_id": run_id,
        # Host transport authenticates against the immutable whole-run
        # artifact owner.  Keep the evolving Design input instance separate
        # so Product-started runs do not sever that authority at Design.
        "stage_instance_id": artifact_binding["stage_instance_id"],
        "design_input_instance_id": "design-"
        + hashlib.sha256(
            f"{run_id}:{input_fingerprint}:{effective.digest}".encode("utf-8")
        ).hexdigest()[:24],
        "requirement": requirement_id,
        "requirement_fingerprint": requirement_fingerprint,
        "goal_fingerprint": hashlib.sha256(
            str(state.get("goal") or "").encode("utf-8")
        ).hexdigest(),
        "candidate_fingerprint": candidate_fingerprint,
        "input_fingerprint": input_fingerprint,
        "catalog_fingerprint": catalog_fingerprint,
        "settings_digest": effective.digest,
        "decomposition_fingerprint": receipt["fingerprint"],
        "lens_policy": policy.to_dict(),
    }
    binding["fingerprint"] = hashlib.sha256(
        json.dumps(
            binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    artifact_root = _run_artifact_root(ws, {**dict(state), **run_control})
    artifact_reference = run_control["run_artifacts"]
    artifact_manifest = run_artifacts.load_manifest(artifact_root)
    cleanup_manifest = _ensure_owned_cleanup_manifest(ws, artifact_root, artifact_binding)
    graph_entries = artifact_manifest["classes"]["dependency-graphs"]["entries"]
    graph_reference = next(
        (
            row
            for row in graph_entries
            if (row.get("metadata") or {}).get("receipt_fingerprint") == receipt["fingerprint"]
        ),
        None,
    )
    if graph_reference is None:
        graph_reference = depgraph.publish_design_decomposition(ws, artifact_root, receipt)
    changed = False
    with mutate(ws) as fresh:
        if (
            fresh is None
            or fresh.get("step") != "design"
            or fresh.get("requirement_id") != state.get("requirement_id")
        ):
            raise ValueError("Design advanced while decomposition was running")
        changed = fresh.get("design_control_plane_binding") != binding
        fresh.update(run_control)
        fresh["settings_digest"] = effective.digest
        fresh["design_lens_policy"] = policy.to_dict()
        fresh["design_decomposition_receipt"] = receipt
        fresh["design_control_plane_binding"] = binding
        fresh["design_graph_fingerprint"] = receipt["graph_fingerprint"]
        fresh["run_artifacts"] = artifact_reference
        fresh["run_artifact_binding"] = artifact_binding
        fresh["owned_cleanup_manifest"] = cleanup_manifest
        fresh.setdefault("run_artifact_refs", {})["design_decomposition"] = graph_reference
    if changed:
        tp.trace(
            ws,
            "design_control_plane_ready",
            requirement=requirement_id,
            status=receipt["status"],
            graph=receipt["graph_fingerprint"][:12],
            components=receipt["component_count"],
            selected_components=receipt["selected_component_count"],
            settings=effective.digest[:12],
            maximum_lenses=policy.max_count,
            fingerprint=binding["fingerprint"],
        )
    return receipt, policy


def _design_control_plane_errors(ws: str, state: Mapping[str, object]) -> list[str]:
    """Refuse a missing, stale, degraded, or severed Design input binding."""
    try:
        effective = operational_settings.load_settings(environment=os.environ)
        catalog = lens_router.load_catalog()
        catalog_rows = list(catalog.get("lenses") or [])
        catalog_ids = [
            str(row.get("id") or "")
            for row in catalog_rows
            if isinstance(row, Mapping) and row.get("id")
        ]
        policy = effective.lenses.policy_for("design", catalog_ids=catalog_ids)
    except Exception as exc:
        return [f"Design control-plane validation failed: {exc.__class__.__name__}: {exc}"]
    errors = []
    receipt = state.get("design_decomposition_receipt")
    binding = state.get("design_control_plane_binding")
    if not isinstance(receipt, Mapping):
        return ["Design decomposition receipt is missing"]
    if not isinstance(binding, Mapping):
        return ["Design control-plane binding is missing"]
    receipt_material = {str(key): value for key, value in receipt.items() if key != "fingerprint"}
    if (
        receipt.get("fingerprint")
        != hashlib.sha256(
            json.dumps(
                receipt_material,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    ):
        errors.append("Design decomposition receipt fingerprint is invalid")
    binding_material = {str(key): value for key, value in binding.items() if key != "fingerprint"}
    if (
        binding.get("fingerprint")
        != hashlib.sha256(
            json.dumps(
                binding_material,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    ):
        errors.append("Design control-plane binding fingerprint is invalid")
    if receipt.get("status") != "ready":
        errors.append(
            "Design decomposition is degraded: "
            + ", ".join(str(reason) for reason in receipt.get("degraded_reasons") or [])
        )
    if receipt.get("head") != tp.git_head(ws) or receipt.get("scanned_head") != tp.git_head(ws):
        errors.append("Design decomposition is stale for the current HEAD")
    if receipt.get("graph_fingerprint") != state.get("design_graph_fingerprint"):
        errors.append("Design decomposition graph snapshot is severed")
    if (
        receipt.get("settings_digest") != effective.digest
        or state.get("settings_digest") != effective.digest
    ):
        errors.append("Design decomposition settings digest is stale")
    if (
        state.get("design_lens_policy") != policy.to_dict()
        or binding.get("lens_policy") != policy.to_dict()
    ):
        errors.append("Design lens policy is stale or severed")
    if binding.get("requirement") != state.get("requirement_id") or binding.get(
        "decomposition_fingerprint"
    ) != receipt.get("fingerprint"):
        errors.append("Design control-plane binding does not match this run")
    if binding.get("run_id") != state.get("run_id"):
        errors.append("Design control-plane run identity is stale or severed")
    artifact_binding = state.get("run_artifact_binding")
    if (
        not isinstance(artifact_binding, Mapping)
        or binding.get("stage_instance_id") != artifact_binding.get("stage_instance_id")
        or binding.get("candidate_fingerprint")
        != (artifact_binding.get("candidate") or {}).get("fingerprint")
    ):
        errors.append("Design host transport artifact authority is severed")
    if binding.get("requirement_fingerprint") != _dc.requirement_fingerprint(
        ws, state.get("requirement_id")
    ):
        errors.append("Design requirement content changed after decomposition")
    if (
        binding.get("goal_fingerprint")
        != hashlib.sha256(str(state.get("goal") or "").encode("utf-8")).hexdigest()
    ):
        errors.append("Design goal binding is stale or severed")
    input_fingerprint = binding.get("input_fingerprint", binding.get("candidate_fingerprint"))
    if input_fingerprint != _design_input_fingerprint(ws):
        errors.append("Design source bytes changed after decomposition")
    if binding.get("catalog_fingerprint") != lens_route_policy.catalog_fingerprint(catalog_rows):
        errors.append("Design lens catalog changed after decomposition")
    if policy.max_count > operational_settings.DESIGN_LENS_MAX:
        errors.append("Design lens policy exceeds the immutable maximum")
    return errors


def bind_producer_observation(
    submission: Mapping[str, object],
    receipt: Mapping[str, object] | None,
    *,
    output_bytes: bytes,
    output_schema_id: str,
    output_contract_fingerprint: str,
) -> dict:
    """Refuse caller-authored provenance at the public loop boundary."""
    del submission, receipt, output_bytes, output_schema_id
    del output_contract_fingerprint
    raise producer_observation_policy.ProducerObservationError(
        "a genuine external host producer receipt is required; "
        "caller-supplied producer observation is refused"
    )


def producer_output_identity(
    ws: str,
    state: Mapping[str, object],
    task: Mapping[str, object] | None,
    step: str,
    *,
    active_contract: Mapping[str, object] | None = None,
    em_output_snapshot: Mapping[str, object] | None = None,
) -> dict:
    """Derive the one engine-owned output identity a host stop may observe."""
    if step not in {"evaluate", "em"}:
        raise producer_observation_policy.ProducerObservationError(
            "producer observation is only defined for evaluate or em"
        )
    binding = review_kernel_binding(dict(state), step, dict(task or {}))
    if not binding or not str(binding.get("run_id") or "").strip():
        raise producer_observation_policy.ProducerObservationError(
            f"{step} ReviewKernel binding is missing"
        )
    run_id = str(binding["run_id"])
    task_id = str((task or {}).get("id") or "engineering-signoff")
    producer = STEP_ROLE[step]
    dispatch = producer_observation_policy.validate_producer_dispatch(
        (active_contract or {}).get("producer_dispatch"),
        run_id=run_id,
        task_id=task_id,
        stage=step,
        producer=producer,
    )
    source_sha = tp.git_head(ws)
    if step == "evaluate":
        contract = (active_contract or {}).get("output_contract")
        if (
            not isinstance(contract, Mapping)
            or contract.get("stage") != "evaluate"
            or contract.get("task") != task_id
            or contract.get("producer") != "tp-evaluator"
        ):
            raise producer_observation_policy.ProducerObservationError(
                "external host producer receipt cannot be matched: active "
                "evaluator output contract is missing or mismatched"
            )
        output_path = str(contract.get("result_path") or "")
        resolved = output_path if os.path.isabs(output_path) else os.path.join(ws, output_path)
        try:
            with open(resolved, "rb") as stream:
                output_bytes = stream.read()
        except OSError as exc:
            raise producer_observation_policy.ProducerObservationError(
                "evaluator result bytes are missing"
            ) from exc
        output_schema_id = str(contract.get("output_schema_id") or "")
        contract_fingerprint = producer_observation_policy.content_fingerprint(dict(contract))
    else:
        paths = [
            runtime_storage.review_public_path(ws, "findings.json"),
            runtime_storage.review_public_path(ws, "report.md"),
        ]
        if em_output_snapshot is None:
            exact = []
            for path in paths:
                try:
                    with open(path, "rb") as stream:
                        exact.append((path, stream.read()))
                except OSError as exc:
                    raise producer_observation_policy.ProducerObservationError(
                        "EM result bytes are missing"
                    ) from exc
        else:
            captured = em_outage.output_snapshot_bytes(em_output_snapshot)
            exact = [(paths[0], captured["findings"]), (paths[1], captured["report"])]
        output_path = json.dumps(paths, separators=(",", ":"))
        output_bytes = producer_observation_policy.exact_output_bundle(exact)
        output_schema_id = "taskplane.em-output/v1"
        delivery = _validated_delivery_mode(dict(state))
        contract_fingerprint = producer_observation_policy.content_fingerprint(
            {
                "schema": "taskplane.em-output-contract/v1",
                "run_id": run_id,
                "task_id": task_id,
                "stage": "em",
                "output_paths": paths,
                "delivery_mode_receipt": (delivery or {}).get("fingerprint"),
            }
        )
    return {
        "workspace": ws,
        "evidence_root": tp.store_root(ws),
        "run_id": run_id,
        "task_id": task_id,
        "stage": step,
        "producer": producer,
        "output_path": output_path,
        "output_bytes": output_bytes,
        "output_schema_id": output_schema_id,
        "output_contract_fingerprint": contract_fingerprint,
        "source_sha": source_sha,
        "producer_dispatch": dispatch,
    }


from taskplane.stage_loop import (
    STAGE_COMMAND_SCHEMA,
    STAGE_HISTORY_SCHEMA,
    STAGE_HISTORY_MAX_ITEMS,
    _STAGE_ROOT_AUTHORITY_SCHEMA,
    _STAGE_ROOT_AUTHORITY_FIELDS,
    _STAGE_ACTOR_IDENTIFIER,
    _STAGE_RUNTIME_FIELDS,
    _STAGE_REQUEST_FIELDS,
)


def _stage_command_error(*args, **kwargs):
    return stage_loop._stage_command_error(sys.modules[__name__], *args, **kwargs)


def _stage_request(*args, **kwargs):
    return stage_loop._stage_request(sys.modules[__name__], *args, **kwargs)


def _reject_stage_runtime_fields(*args, **kwargs):
    return stage_loop._reject_stage_runtime_fields(sys.modules[__name__], *args, **kwargs)


def _validate_stage_request(*args, **kwargs):
    return stage_loop._validate_stage_request(sys.modules[__name__], *args, **kwargs)


def _stage_run_id(*args, **kwargs):
    return stage_loop._stage_run_id(sys.modules[__name__], *args, **kwargs)


def _stage_store(*args, **kwargs):
    return stage_loop._stage_store(sys.modules[__name__], *args, **kwargs)


def _run_schema_refusal(ws: str) -> dict | None:
    """Refuse an unsupported run without reading or converting projections."""
    try:
        _load_raw(ws)
    except (ValueError, OSError, run_store_engine.RunStoreError) as exc:
        return {
            "error": str(exc),
            "code": "unsupported_run_schema"
            if "unsupported_run_schema" in str(exc)
            else "invalid_run",
            "dispatch_allowed": False,
        }
    return None


def _current_stage_authority(*args, **kwargs):
    return stage_loop._current_stage_authority(sys.modules[__name__], *args, **kwargs)


def _stage_lifecycle(*args, **kwargs):
    return stage_loop._stage_lifecycle(sys.modules[__name__], *args, **kwargs)


def _indexed_stage(*args, **kwargs):
    return stage_loop._indexed_stage(sys.modules[__name__], *args, **kwargs)


def _verified_stage_handoff(*args, **kwargs):
    return stage_loop._verified_stage_handoff(sys.modules[__name__], *args, **kwargs)


def _stage_dispatch(*args, **kwargs):
    return stage_loop._stage_dispatch(sys.modules[__name__], *args, **kwargs)


def _preflight_stage_dispatch(*args, **kwargs):
    return stage_loop._preflight_stage_dispatch(sys.modules[__name__], *args, **kwargs)


def _stage_bootstrap_pristine_root(*args, **kwargs):
    return stage_loop._stage_bootstrap_pristine_root(sys.modules[__name__], *args, **kwargs)


_LOOP_STAGE_KINDS = {
    "pm": "product",
    "design": "design",
    "design_approval": "design",
    "plan": "plan",
    "plan_approval": "plan",
    "execute": "build",
    "fix": "build",
    "evaluate": "evaluate",
    "selection": "evaluate",
    "em": "engineering",
    "signoff": "engineering",
    "escalated": "engineering",
    "retro": "retro",
}


def _stage_loop_context(*args, **kwargs):
    return stage_loop._stage_loop_context(sys.modules[__name__], *args, **kwargs)


def _stage_loop_identity(*args, **kwargs):
    return stage_loop._stage_loop_identity(sys.modules[__name__], *args, **kwargs)


def _stage_loop_dispatch(*args, **kwargs):
    return stage_loop._stage_loop_dispatch(sys.modules[__name__], *args, **kwargs)


def _stage_loop_deliverables(*args, **kwargs):
    return stage_loop._stage_loop_deliverables(sys.modules[__name__], *args, **kwargs)


def _stage_loop_scope(*args, **kwargs):
    return stage_loop._stage_loop_scope(sys.modules[__name__], *args, **kwargs)


def _stage_loop_completion_reference(*args, **kwargs):
    return stage_loop._stage_loop_completion_reference(sys.modules[__name__], *args, **kwargs)


_STAGE_OUTPUT_MAX_SOURCES = 64
_STAGE_OUTPUT_MAX_FILE_BYTES = 8 * 1024 * 1024
_STAGE_OUTPUT_MAX_TOTAL_BYTES = 16 * 1024 * 1024


def _before_stage_output_component_open(*args, **kwargs):
    return stage_loop._before_stage_output_component_open(sys.modules[__name__], *args, **kwargs)


def _stage_loop_open_directory_no_follow(*args, **kwargs):
    return stage_loop._stage_loop_open_directory_no_follow(sys.modules[__name__], *args, **kwargs)


def _stage_loop_read_output_no_follow(*args, **kwargs):
    return stage_loop._stage_loop_read_output_no_follow(sys.modules[__name__], *args, **kwargs)


def _stage_loop_trusted_output_workspace(*args, **kwargs):
    return stage_loop._stage_loop_trusted_output_workspace(sys.modules[__name__], *args, **kwargs)


def _stage_loop_managed_evidence_paths(*args, **kwargs):
    return stage_loop._stage_loop_managed_evidence_paths(sys.modules[__name__], *args, **kwargs)


def _stage_loop_decision_completion(*args, **kwargs):
    return stage_loop._stage_loop_decision_completion(sys.modules[__name__], *args, **kwargs)


def _stage_loop_completion_outputs(*args, **kwargs):
    return stage_loop._stage_loop_completion_outputs(sys.modules[__name__], *args, **kwargs)


def _stage_loop_transition_operation_material(*args, **kwargs):
    return stage_loop._stage_loop_transition_operation_material(
        sys.modules[__name__], *args, **kwargs
    )


def _stage_loop_transition(*args, **kwargs):
    return stage_loop._stage_loop_transition(sys.modules[__name__], *args, **kwargs)


def stage_history(*args, **kwargs):
    return stage_loop.stage_history(sys.modules[__name__], *args, **kwargs)


def _stage_history(*args, **kwargs):
    return stage_loop._stage_history(sys.modules[__name__], *args, **kwargs)


def _project_bound_stage_start(*args, **kwargs):
    return stage_loop._project_bound_stage_start(sys.modules[__name__], *args, **kwargs)


def stage_command(*args, **kwargs):
    return stage_loop.stage_command(sys.modules[__name__], *args, **kwargs)


# Evaluate consumes the Build candidate through the shared zero-lens kernel.
EVALUATE_ROUTE_STAGE = "build"
_DELIVERY_MODE_AUTHORITY_UNSET = object()


_review_kernel_binding_key = review_retry.binding_key
review_kernel_binding = review_retry.binding
review_session_authority_gate = review_session_engine.request_authority


def _authorization_fields(ws: str, state: dict) -> dict:
    """Build the semantic preimplementation envelope from engine facts."""
    requirement = reqs.get_requirement(ws, state.get("requirement_id")) or {}
    context = _phase_bridge_context(ws, state)
    if context is None:
        raise ValueError("approval requires the current phase package")
    design = phase_harness.input_package(sys.modules[__name__], context).read("design")
    tasks = state.get("tasks") or []
    scope = sorted({str(path) for task in tasks for path in task.get("scope") or []})
    contracts = {
        str(row.get("id")): str(row.get("relation") or "")
        for row in requirement.get("contracts") or []
        if isinstance(row, dict) and row.get("id")
    }
    for row in (design or {}).get("contracts") or []:
        if isinstance(row, dict) and row.get("id"):
            contracts[str(row["id"])] = {
                "relation": str(row.get("relation") or ""),
                "description": str(row.get("description") or ""),
            }
    plan = [
        {
            "id": str(task.get("id") or ""),
            "scope": sorted(str(path) for path in task.get("scope") or []),
            "tests": str(task.get("tests") or ""),
            "deps": sorted(str(dep) for dep in task.get("deps") or []),
            "variant": task.get("variant"),
        }
        for task in tasks
    ]
    return {
        "requirement": str(state.get("requirement_id") or ""),
        "acceptance": list(requirement.get("acceptance") or []),
        "target": {
            "repository": os.path.realpath(ws),
            "revision": (state.get("authority_target_revision") or tp.git_head(ws)),
        },
        "scope": scope,
        "contracts": contracts,
        "design": {
            "decision": str((design or {}).get("decision") or ""),
            "depth_policy": ((design or {}).get("graph") or {}).get("depth_policy") or {},
        },
        "plan": {"tasks": plan, "parallel": bool(state.get("parallel"))},
        "dynamic_validation": state.get("dynamic_validation_intent", "declared"),
        "sandbox": state.get("sandbox_authority", "ordinary_scoped_activity"),
        "recovery": {
            "max_fix_cycles": int(state.get("max_fix_cycles", 2)),
            "gate_weakening": False,
        },
        "evaluation": "declared tests and sealed direct evidence with zero lens workers",
        "artifact_delivery": ["canonical_json", "inline_or_complete_markdown"],
        "execution_bounds": {"parallel": bool(state.get("parallel")), "external_effects": False},
    }


def _product_definition_gate(requirement: dict) -> dict:
    """Product refinement is mechanical; strategic advice is attributable."""
    text = [
        str(requirement.get("title") or ""),
        *[str(x) for x in requirement.get("acceptance") or []],
    ]
    advice = review_dor.north_star_advice(
        text,
        explicit=bool(requirement.get("north_star_requested")),
        advice=requirement.get("north_star_advice"),
    )
    evidence = {
        "requirement": requirement.get("title") or requirement.get("id"),
        "acceptance": requirement.get("acceptance"),
        # Pass the facts themselves. Truthy ``checked/items`` envelopes made
        # empty collections look complete and allowed incomplete refinement.
        "contracts": requirement.get("contracts"),
        "dependencies": requirement.get("dependencies"),
        "nfrs": requirement.get("nfrs"),
        "score": requirement.get("score"),
    }
    return {
        **authority_engine.mechanical_definition_gate("product", evidence),
        "north_star": advice,
    }


def _preview_feedback(
    state: dict, text: str, *, actor: str, authenticated: bool, kind: str
) -> dict:
    return authority_engine.preview_change(
        text,
        actor=actor,
        authenticated=authenticated,
        requirement=str(state.get("requirement_id") or ""),
        target={
            "revision": str(state.get("authority_target_revision") or state.get("baseline") or "")
        },
        kind=kind,
    )


def request_human_decision(
    state: dict,
    reason: str,
    response: object,
    *,
    actor: str,
    thread: str,
    revision: str,
    consumed: bool = False,
    fact: str = "",
    consequence: str = "",
) -> dict:
    """Single production boundary for every exceptional human decision."""
    receipt = state.get("authority_receipt") or {}
    return authority_engine.decision_input(
        reason,
        response,
        fact=fact,
        consequence=consequence,
        actor=actor,
        thread=thread,
        revision=revision,
        expected_actor=str(receipt.get("actor") or ""),
        expected_thread=str(receipt.get("thread") or ""),
        expected_revision=str(
            state.get("authority_target_revision") or state.get("baseline") or ""
        ),
        consumed=consumed,
    )


def _trace_effect_seen(ws: str, effect_id: str) -> bool:
    root = os.path.realpath(tp.tp_dir(ws))
    for path in tp.trace_paths(ws):
        fd = None
        try:
            absolute = os.path.abspath(path)
            if os.path.commonpath((root, absolute)) != root:
                continue
            before = os.lstat(absolute)
            if not stat.S_ISREG(before.st_mode):
                continue
            fd = os.open(absolute, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            after = os.fstat(fd)
            if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (
                after.st_dev,
                after.st_ino,
            ):
                os.close(fd)
                fd = None
                continue
            with os.fdopen(fd, encoding="utf-8") as handle:
                fd = None
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("authority_effect_id") == effect_id:
                        return True
        except OSError:
            continue
        finally:
            if fd is not None:
                os.close(fd)
    return False


def _open_directory_without_symlinks(path: str, *, create: bool = False) -> int:
    """Open an absolute directory while rejecting every symlink component."""
    directory = os.path.abspath(path)
    drive, tail = os.path.splitdrive(directory)
    root = drive + os.sep if drive else os.sep
    parts = [part for part in tail.split(os.sep) if part]
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    supports_relative_open = (
        directory_flag is not None
        and nofollow is not None
        and os.open in getattr(os, "supports_dir_fd", set())
    )
    if supports_relative_open:
        flags = os.O_RDONLY | directory_flag | nofollow
        current_fd = os.open(root, os.O_RDONLY | directory_flag)
        current_path = root
        try:
            for part in parts:
                candidate = os.path.join(current_path, part)
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                info = os.lstat(candidate)
                if stat.S_ISLNK(info.st_mode):
                    raise OSError("authority trace path contains a symlink: " + candidate)
                if not stat.S_ISDIR(info.st_mode):
                    raise OSError("authority trace path component is not a directory: " + candidate)
                next_fd = None
                try:
                    next_fd = os.open(part, flags, dir_fd=current_fd)
                    opened = os.fstat(next_fd)
                    if not stat.S_ISDIR(opened.st_mode) or (info.st_dev, info.st_ino) != (
                        opened.st_dev,
                        opened.st_ino,
                    ):
                        raise OSError(
                            "authority trace path component changed while opening: " + candidate
                        )
                except Exception:
                    if next_fd is not None:
                        os.close(next_fd)
                    raise
                os.close(current_fd)
                current_fd = next_fd
                current_path = candidate
            return current_fd
        except Exception:
            os.close(current_fd)
            raise

    # Platforms without relative O_NOFOLLOW traversal still fail closed for
    # ordinary accidental substitution by checking every ancestor before the
    # final open. The deployment threat model excludes a hostile same-UID
    # process racing this fallback.
    current_path = root
    final_info = os.lstat(root)
    for part in parts:
        current_path = os.path.join(current_path, part)
        if create:
            try:
                os.mkdir(current_path, mode=0o700)
            except FileExistsError:
                pass
        final_info = os.lstat(current_path)
        if stat.S_ISLNK(final_info.st_mode):
            raise OSError("authority trace path contains a symlink: " + current_path)
        if not stat.S_ISDIR(final_info.st_mode):
            raise OSError("authority trace path component is not a directory: " + current_path)
    flags = os.O_RDONLY
    if directory_flag is not None:
        flags |= directory_flag
    dir_fd = os.open(directory, flags)
    opened = os.fstat(dir_fd)
    if not stat.S_ISDIR(opened.st_mode) or (final_info.st_dev, final_info.st_ino) != (
        opened.st_dev,
        opened.st_ino,
    ):
        os.close(dir_fd)
        raise OSError("authority trace directory changed while opening")
    return dir_fd


def _append_authority_trace(ws: str, event: str, data: dict) -> None:
    """Append one authority trace without following directory/file links."""
    audit_data = copy.deepcopy(data)
    audit_state = _load_raw(ws) or {}
    root_receipt = audit_state.get("root_hygiene_receipt")
    if isinstance(root_receipt, Mapping):
        audit_data["root_hygiene_receipt"] = copy.deepcopy(root_receipt)
    directory = os.path.abspath(tp.tp_dir(ws))
    dir_fd = _open_directory_without_symlinks(directory, create=True)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    supports_relative_open = os.open in getattr(os, "supports_dir_fd", set())
    trace_fd = None
    existing = None
    try:
        name = "trace.jsonl"
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if nofollow is not None:
            flags |= nofollow
        if nofollow is None:
            trace_path = os.path.join(directory, name)
            try:
                existing = os.lstat(trace_path)
            except FileNotFoundError:
                existing = None
            if existing is not None and stat.S_ISLNK(existing.st_mode):
                raise OSError("authority trace file is a symlink: " + trace_path)
        if supports_relative_open:
            trace_fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
        else:
            trace_fd = os.open(os.path.join(directory, name), flags, 0o600)
        current = os.fstat(trace_fd)
        if not stat.S_ISREG(current.st_mode):
            raise OSError("authority trace target is not a regular file")
        if existing is not None and (existing.st_dev, existing.st_ino) != (
            current.st_dev,
            current.st_ino,
        ):
            raise OSError("authority trace file changed while opening")
        # Authority outbox delivery uses the same closed privacy projection as
        # every ordinary audit append; the no-follow descriptor handling here
        # remains the stronger authority-specific filesystem boundary.
        record = tp.audit_record(event, audit_data, observed_at=time.time())
        raw = (json.dumps(record, default=str) + "\n").encode("utf-8")
        written = os.write(trace_fd, raw)
        if written != len(raw):
            raise OSError("authority trace append was incomplete")
        os.fsync(trace_fd)
    finally:
        if trace_fd is not None:
            os.close(trace_fd)
        os.close(dir_fd)


def _kb_effect_seen(ws: str, effect_id: str) -> bool:
    try:
        return any(
            (row.get("links") or {}).get("authority_effect") == effect_id
            for row in kb.list_decisions(ws)
        )
    except (OSError, ValueError, TypeError):
        return False


def _enqueue_authority_effect(
    state: dict, effect_id: str, *, trace_event: str, trace_data: dict, kb_data: dict | None = None
) -> None:
    outbox = state.setdefault("authority_effect_outbox", {})
    outbox.setdefault(
        effect_id,
        {
            "schema": "taskplane.authority-effect/v1",
            "status": "pending",
            "trace": {"delivered": False, "event": trace_event, "data": trace_data},
            "kb": ({"delivered": False, "data": kb_data} if kb_data is not None else None),
        },
    )


def reconcile_authority_effects(ws: str) -> dict:
    """Deliver durable authority effects idempotently after state commit."""
    if refusal := _run_schema_refusal(ws):
        return refusal
    delivered = pending = 0
    with mutate(ws) as state:
        if state is None:
            return {"delivered": 0, "pending": 0}
        for effect_id, row in (state.get("authority_effect_outbox") or {}).items():
            if row.get("status") == "delivered":
                delivered += 1
                continue
            trace_effect = row.get("trace") or {}
            try:
                if not trace_effect.get("delivered"):
                    if not _trace_effect_seen(ws, effect_id):
                        _append_authority_trace(
                            ws,
                            str(trace_effect.get("event") or "authority_effect"),
                            {
                                **dict(trace_effect.get("data") or {}),
                                "authority_effect_id": effect_id,
                            },
                        )
                    trace_effect["delivered"] = _trace_effect_seen(ws, effect_id)
                kb_effect = row.get("kb")
                if trace_effect.get("delivered") and kb_effect and not kb_effect.get("delivered"):
                    if not _kb_effect_seen(ws, effect_id):
                        data = dict(kb_effect.get("data") or {})
                        links = {**dict(data.pop("links", {}) or {}), "authority_effect": effect_id}
                        kb.record_decision(ws, links=links, **data)
                    kb_effect["delivered"] = _kb_effect_seen(ws, effect_id)
            except Exception as exc:  # effect remains durable for retry
                row["last_error"] = f"{exc.__class__.__name__}: {exc}"
            complete = bool(trace_effect.get("delivered")) and (
                row.get("kb") is None or bool((row.get("kb") or {}).get("delivered"))
            )
            if complete:
                row["status"] = "delivered"
                row.pop("last_error", None)
                delivered += 1
            else:
                row["status"] = "pending"
                pending += 1
    return {"delivered": delivered, "pending": pending}


def _host_session_envelope(state: dict, event: dict, host_event: object | None) -> dict:
    """Bind trusted-session attribution to the loop's current target."""
    expected_revision = str(state.get("authority_target_revision") or state.get("baseline") or "")
    receipt = state.get("authority_receipt") or {}
    envelope = authority_engine.HostSessionAdapter().observe(
        event,
        host_event,
        expected_actor=str(receipt.get("actor") or ""),
        expected_thread=str(receipt.get("thread") or ""),
        expected_revision=expected_revision,
        expected_target={"revision": expected_revision},
    )
    if envelope.get("attributed") and not all(
        (
            str(receipt.get("actor") or "").strip(),
            str(receipt.get("thread") or "").strip(),
            expected_revision,
        )
    ):
        return {**envelope, "attributed": False, "reasons": ["current_authority_unbound"]}
    return envelope


@loop_status.with_dashboard
def handle_host_input(ws: str, event: dict, host_event: object | None = None) -> dict:
    """Consume one trusted local host/session event.

    The supported deployment is a single trusted Codex/Claude session.  The
    separate adapter observation supplies attribution; labels in the event
    body do not.  Stale and replay checks remain mechanical and atomic.
    """
    if refusal := _run_schema_refusal(ws):
        return refusal
    if not isinstance(event, dict):
        return {"error": "host event must be a mapping"}
    reconcile_authority_effects(ws)
    kind = str(event.get("type") or "").strip().lower()
    if kind == "preview_feedback":
        with mutate(ws) as state:
            if state is None:
                return {"error": "no active loop"}
            envelope = _host_session_envelope(state, event, host_event)
            if not envelope["attributed"]:
                return {"accepted": False, "reasons": envelope["reasons"]}
            expected_revision = str(
                state.get("authority_target_revision") or state.get("baseline") or ""
            )
            if envelope["revision"] != expected_revision:
                return {"accepted": False, "reasons": ["wrong_revision"]}
            event_id = envelope["event_id"]
            consumed = state.setdefault("consumed_host_events", {})
            if event_id in consumed:
                return {"accepted": False, "reasons": ["replayed_event"]}
            change = _preview_feedback(
                state,
                str(event.get("text") or ""),
                actor=envelope["actor"],
                authenticated=True,
                kind=str(event.get("change_kind") or ""),
            )
            if not change["accepted"]:
                return change
            state.setdefault("preview_changes", []).append(change)
            if change["reauthorization_required"]:
                state["reauthorization_required"] = True
            consumed[event_id] = {
                "actor": envelope["actor"],
                "thread": envelope["thread"],
                "revision": envelope["revision"],
                "target": envelope["target"],
                "source": envelope["source"],
                "event_ref": envelope["event_ref"],
            }
            _enqueue_authority_effect(
                state,
                f"preview:{event_id}",
                trace_event="preview_change",
                trace_data={
                    "actor": change["actor"],
                    "kind": change["kind"],
                    "material": change["material"],
                    "change": change["fingerprint"],
                },
            )
        effects = reconcile_authority_effects(ws)
        return {**change, "effect_delivery": effects}
    if kind == "human_decision":
        with mutate(ws) as state:
            if state is None:
                return {"error": "no active loop"}
            envelope = _host_session_envelope(state, event, host_event)
            if not envelope["attributed"]:
                return {"authorized": False, "human_required": True, "reasons": envelope["reasons"]}
            decision_id = envelope["event_id"]
            consumed = state.setdefault("consumed_host_decisions", {})
            response = event.get("response")
            if isinstance(response, dict):
                response = {**response, "authenticated": True}
            result = request_human_decision(
                state,
                str(event.get("reason") or "unsafe_or_ambiguous"),
                response,
                actor=envelope["actor"],
                thread=envelope["thread"],
                revision=envelope["revision"],
                consumed=decision_id in consumed,
                fact=str(event.get("fact") or ""),
                consequence=str(event.get("consequence") or ""),
            )
            if result["authorized"]:
                consumed[decision_id] = {
                    "actor": envelope["actor"],
                    "thread": envelope["thread"],
                    "revision": envelope["revision"],
                    "target": envelope["target"],
                    "source": envelope["source"],
                    "event_ref": envelope["event_ref"],
                }
            return result
    return {"error": "host event type must be preview_feedback|human_decision"}


def _derive_consolidated_authority(ws: str, state: dict, stage: str) -> dict | None:
    packet, receipt = (state.get("authority_packet"), state.get("authority_receipt"))
    if not packet or not receipt:
        return None
    return authority_engine.derive(
        packet,
        receipt,
        stage=stage,
        current=_authorization_fields(ws, state),
        actor=str(receipt.get("actor") or ""),
        thread=str(receipt.get("thread") or ""),
    )


def authorize_routine_flow(ws: str, flow: str) -> dict:
    """Production entry point used by each host flow to derive authority."""
    if refusal := _run_schema_refusal(ws):
        return refusal
    normalized = str(flow or "").strip().lower().replace("-", "_")
    if normalized not in authority_engine.ROUTINE_FLOWS:
        return {"error": f"unknown routine flow '{flow}'"}
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    derived = _derive_consolidated_authority(ws, state, normalized)
    if derived is None:
        return {"error": "consolidated authorization is unavailable"}
    tp.trace(
        ws,
        "authority_derived",
        flow=normalized,
        authorized=derived["authorized"],
        receipt=derived.get("receipt_fingerprint"),
    )
    return derived


def _state_dir(ws: str) -> str:
    locator = runtime_storage.load_workspace_locator(ws)
    return (
        locator["paths"]["state"]
        if locator
        else os.path.join(tp.external_store_root(ws), "knowledge", "state")
    )


def state_dir(ws: str) -> str:
    """THE exported owner of the loop-state location rule (v2.3.0).

    Any module that touches per-user coordination state (loop.json,
    tracks.json — see docs/state-spec.md, 'Loop coordination state is
    per-user') must resolve its directory HERE instead of re-deriving via
    tp.kb_root/store_root: re-derivation is exactly how track state ended up
    in the committed team store on a team plan. TASKPLANE_STORE=repo remains
    the single exception, and this function owns it."""
    return _state_dir(ws)


# Non-build steps are read-only with artifact allowances; build/fix use plan scope.
# pm and em are two deliberate personas (split in v0.8.0): tp-product owns
# the requirement; tp-engineering owns the final all-lens review.
STEP_ROLE = {
    "pm": "tp-product",
    "design": "tp-designer",
    "plan": "tp-planner",
    "execute": "tp-executor",
    "evaluate": "tp-evaluator",
    "fix": "tp-fixer",
    "em": "tp-engineering",
    "retro": "tp-retro",
}
HUMAN_STEPS = progress_engine.HUMAN_STEPS
WORKER_SUBMISSION_STEPS = frozenset({"design", "execute", "fix", "evaluate", "em"})

COMMAND_WAVE_SCHEMA = command_wave.COMMAND_WAVE_SCHEMA
command_wave_create = command_wave.create
command_wave_resume = command_wave.resume
command_wave_update = command_wave.update


def governed_command(ws: str, action: str, request: object) -> dict:
    """Run one command lifecycle action through the live loop root."""
    return governed_commands.execute(ws, action, request)


def _native_dispatch_intent(*args, **kwargs):
    return dispatch._native_dispatch_intent(sys.modules[__name__], *args, **kwargs)


# A task is SETTLED when nothing further is owed on it: it passed, or the
# selection gate closed it (not_selected / reference), or a human skipped it.
# Wave readiness and "are we done?" both reason over this set.
SETTLED = {"passed", "not_selected", "reference", "skipped", "done", "external"}
# Statuses that SATISFY a dependency: the work exists (passed here,
# `done` seeded from outside the loop, `external` deferred to an
# external gate by an explicit human decision). `skipped` settles a
# task but does NOT satisfy its dependents (they cascade-skip).
DEP_SATISFIED = {"passed", "done", "external"}

PIPELINE = progress_engine.PIPELINE
SELECTION_STEP = progress_engine.SELECTION_STEP
NativeProgressSession = progress_engine.NativeProgressSession
project_agent_topology = progress_engine.project_agent_topology
splice_selection = progress_engine.splice_selection
display_pipeline = progress_engine.display_pipeline


def _next_unsettled_index(state: dict, after: int):
    """Next task index strictly after `after` whose task is not SETTLED, or
    None when the rest are all settled. Serial advance uses this so a task
    the skip-cascade already settled is never re-executed."""
    tasks = state.get("tasks") or []
    for i in range(after + 1, len(tasks)):
        if tasks[i].get("status") not in SETTLED:
            return i
    return None


def _loop_path(ws: str) -> str:
    return os.path.join(_state_dir(ws), LOOP_FILE)


def _load_raw(ws: str) -> dict | None:
    locator = runtime_storage.load_workspace_locator(ws)
    if not locator or not locator.get("run_id"):
        return None
    manifest = _stage_store(ws, locator["run_id"]).inspect(locator["run_id"])
    state = manifest.get("workflow")
    if state is not None and (
        not isinstance(state, dict) or state.get("run_id") != manifest["run_id"]
    ):
        raise ValueError("run workflow identity is invalid")
    return state


_EVIDENCE_STATE_WORKSPACE = contextvars.ContextVar(
    "taskplane_evidence_state_workspace", default=None
)


def load(ws: str) -> dict | None:
    """Load loop state and flush any crash-surviving authority outbox."""
    # CLI evidence may bind task bytes to its primary coordination state.
    state_ws = _EVIDENCE_STATE_WORKSPACE.get() or ws
    state = _load_raw(state_ws)
    read_only = _run_schema_refusal(state_ws)
    if (
        state is not None
        and read_only is None
        and any(
            row.get("status") != "delivered"
            for row in (state.get("authority_effect_outbox") or {}).values()
        )
    ):
        reconcile_authority_effects(state_ws)
        state = _load_raw(state_ws)
    return stage_loop.task_phase_state(sys.modules[__name__], ws, state)


def archive(*args, **kwargs):
    return stage_loop.archive(sys.modules[__name__], *args, **kwargs)


def resume(*args, **kwargs):
    return stage_loop.resume(sys.modules[__name__], *args, **kwargs)


def save(ws: str, state: dict) -> None:
    run_id = str(state["run_id"])
    _stage_store(ws, run_id).save_workflow(run_id, state)


def record_enforcement(ws: str, decision: dict) -> dict:
    """Persist one canonical decision for all loop consumers and artifacts."""
    if refusal := _run_schema_refusal(ws):
        return refusal
    import enforcement as enforcement_kernel

    checked = enforcement_kernel.validate_decision(decision)
    with mutate(ws) as state:
        if state is None:
            return {"error": "no active loop"}
        record = state.setdefault(
            "enforcement",
            {
                "schema": "taskplane.run-enforcement/v1",
                "current": checked,
                "history": [],
            },
        )
        history = list(record.get("history") or [])
        if not history or history[-1].get("evidence_id") != checked.get("evidence_id"):
            history.append(checked)
        record.update(
            {"schema": "taskplane.run-enforcement/v1", "current": checked, "history": history[-64:]}
        )
    tp.trace(
        ws,
        "enforcement_decision",
        status=checked["status"],
        evidence_id=checked["evidence_id"],
        mode=checked["mode"],
        actor=((checked.get("advisory") or {}).get("actor")),
    )
    return checked


@contextlib.contextmanager
def mutate(ws: str):
    """Commit workflow decisions and stage heads in one run transaction."""
    locator = runtime_storage.load_workspace_locator(ws)
    if not locator or not locator.get("run_id"):
        yield None
        return
    run_id = str(locator["run_id"])
    with _stage_store(ws, run_id).transaction(run_id):
        state = _load_raw(ws)
        original = _copy_json(state) if state is not None else None
        yield state
        if state is None:
            return
        fence = state.pop("_authority_revision_fence", None)
        if fence and tp.git_head(ws) != fence:
            state.clear()
            state.update(original or {})
            state["_revision_fence_failed"] = {
                "expected": str(fence),
                "actual": str(tp.git_head(ws)),
            }
            raise ValueError("repository revision changed during transition")
        save(ws, state)


TERMINAL_STEPS = ("done", "failed")


def automatic_cleanup_enabled() -> bool:
    """Use the one canonical worktree-cleanup policy."""
    if __package__:
        from .settings import load_settings
    else:
        from settings import load_settings
    return load_settings().cleanup.worktrees == "after-merge"


def _cleanup_lifecycle(task: dict) -> dict:
    retention = task.get("evidence_retention") or {}
    return {
        "status": task.get("status"),
        "released": task.get("status") == "passed",
        "active": False,
        "failed": task.get("status") == "failed",
        "variant": task.get("variant"),
        "selected_variant": bool(task.get("selected")),
        "evidence_needed": retention.get("evidence_needed") is True,
    }


def _record_cleanup_state(
    ws: str,
    task_id: str,
    *,
    receipt: dict | None = None,
    retention: dict | None = None,
    cleanup_record: dict | None = None,
    merge_error: str | None = None,
) -> None:
    with mutate(ws) as locked:
        if locked is None:
            raise tp.StateError(
                _loop_path(ws), "loop disappeared", "restore the loop before cleanup"
            )
        task = next((row for row in locked.get("tasks") or [] if row.get("id") == task_id), None)
        if task is None:
            raise tp.StateError(
                _loop_path(ws), "cleanup task disappeared", "restore the approved task plan"
            )
        if receipt is not None:
            locked.setdefault("task_merges", {})[task_id] = receipt
            task["merge_receipt_id"] = receipt["receipt_id"]
        if retention is not None:
            task["evidence_retention"] = retention
        if cleanup_record is not None:
            locked.setdefault("worktree_cleanups", {})[task_id] = cleanup_record
            task["cleanup_outcome"] = cleanup_record["outcome"]
        if merge_error is not None:
            task["merge_error"] = str(merge_error)[:1200]
            locked["step"] = "escalated"


def _automatic_merge_cleanup(ws: str, task: dict) -> dict | None:
    """Orchestrator-only post-evaluate merge → receipt → cleanup boundary."""
    if not automatic_cleanup_enabled() or task.get("variant") or task.get("merge_on_pass") is False:
        return None
    worker = str(task.get("workspace") or "")
    if not worker:
        return None
    try:
        registration = runtime_storage.load_task_worktree_registration(ws, str(task["id"]))
    except Exception as exc:
        registration = None
        registration_error = str(exc)
    else:
        registration_error = "managed registration is missing"
    if registration is None:
        return {"status": "preserved", "reason": registration_error}
    try:
        import repository
        import review_evidence
        import worktree_cleanup

        retention = review_evidence.retain_worktree_governance(ws, worker, str(task["id"]))
        receipt = repository.RepositoryManager().merge_registered_task(
            ws, task_id=str(task["id"]), run_id=registration["run_id"]
        )
        # The merge receipt is durable before cleanup starts.
        _record_cleanup_state(ws, str(task["id"]), receipt=receipt, retention=retention)
        current = load(ws) or {}
        stored_task = next(
            (row for row in current.get("tasks") or [] if row.get("id") == task.get("id")), task
        )
        result = worktree_cleanup.cleanup(receipt, lifecycle=_cleanup_lifecycle(stored_task))
        _record_cleanup_state(ws, str(task["id"]), cleanup_record=result)
        tp.trace(
            ws,
            "worktree_cleanup_" + result["outcome"].replace("-", "_"),
            task=task["id"],
            receipt_id=receipt["receipt_id"],
            reason=result["reason"],
        )
        return result
    except Exception as exc:
        _record_cleanup_state(ws, str(task["id"]), merge_error=str(exc))
        tp.trace(
            ws,
            "worktree_cleanup_preserved",
            task=task.get("id"),
            reason=f"merge receipt unavailable: {exc}",
        )
        return {"status": "preserved", "reason": f"merge receipt unavailable: {exc}"}


def cleanup_replay(ws: str) -> dict:
    """One bounded maintenance pass over durable receipts only."""
    # Cleanup replay is receipt-scoped lifecycle maintenance, not a stage or
    # workflow transition. It remains available after a crash following a durable merge receipt
    # cannot strand an eligible worktree indefinitely.
    import worktree_cleanup

    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    outcomes = []
    tasks = {str(row.get("id")): row for row in state.get("tasks") or []}
    for task_id, receipt in sorted((state.get("task_merges") or {}).items()):
        prior = (state.get("worktree_cleanups") or {}).get(task_id) or {}
        if prior.get("outcome") in {"removed", "already-clean"}:
            outcomes.append(prior)
            continue
        task = tasks.get(str(task_id)) or {"id": task_id, "status": "passed"}
        result = worktree_cleanup.cleanup(receipt, lifecycle=_cleanup_lifecycle(task))
        _record_cleanup_state(ws, str(task_id), cleanup_record=result)
        outcomes.append(result)
        tp.trace(
            ws,
            "worktree_cleanup_" + result["outcome"].replace("-", "_"),
            task=task_id,
            receipt_id=receipt.get("receipt_id"),
            reason=result["reason"],
            replay=True,
        )
    return {
        "schema": "taskplane.worktree-cleanup-maintenance/v1",
        "attempted": len(outcomes),
        "outcomes": outcomes,
    }


def _stage_native_init_authority(*args, **kwargs):
    return stage_loop._stage_native_init_authority(sys.modules[__name__], *args, **kwargs)


def init(
    ws: str,
    goal: str,
    spec_path: str | None = None,
    max_fix_cycles: int = 2,
    checkpoints=None,
    requirement_id: str | None = None,
    parallel: bool = False,
    design: bool = False,
    design_only: bool = False,
    force: bool = False,
    by: str | None = None,
    reuse_approved_design: bool = False,
    enforcement_decision: dict | None = None,
) -> dict:
    if enforcement_decision is not None:
        import enforcement as enforcement_kernel

        enforcement_decision = enforcement_kernel.validate_decision(enforcement_decision)
    if refusal := _run_schema_refusal(ws):
        return refusal
    if _load_raw(ws) is not None:
        return {
            "error": "run already initialized; archive it before starting a new run",
            "refused": True,
        }
    try:
        root_authority = _stage_native_init_authority(ws, requirement_id, by)
    except Exception as exc:
        return {"error": str(exc), "refused": True}
    checkpoints = list(checkpoints if checkpoints is not None else ["plan", "em"])
    if reuse_approved_design:
        return {"error": "select the approved Design as an explicit stage input", "refused": True}
    state = {
        "governance_revision": 2,
        "run_id": root_authority["run_id"],
        "baseline": tp.git_head(ws),
        "started_at": time.time(),
        # Workers submit evidence; the controller evaluates each gate.
        "submission_required": True,
        "graph_governance": True,
        "goal": goal,
        "parallel": bool(parallel),
        "design_required": True,
        "design_only": bool(design_only),
        "requirement_id": requirement_id,
        "spec_path": spec_path,
        "max_fix_cycles": int(max_fix_cycles),
        "checkpoints": checkpoints,
        "step": "pm",
        "tasks": None,
        "current_task": 0,
        "consumed_host_decisions": {},
        "consumed_host_events": {},
        "authority_effect_outbox": {},
        **(
            {
                "enforcement": {
                    "schema": "taskplane.run-enforcement/v1",
                    "current": enforcement_decision,
                    "history": [enforcement_decision],
                }
            }
            if enforcement_decision is not None
            else {}
        ),
        # This marker is minted only by an attributable new-run init.  The
        # first `loop next` consumes it while atomically committing the exact
        # root; arbitrary pre-existing singleton history is never inferred to
        # be a canary.  The verified v4 binding replaces this eligibility.
        **(
            {"_stage_native_new_run_pristine": True, "_stage_native_root_authority": root_authority}
            if root_authority is not None
            else {}
        ),
    }
    try:
        state.update(_prepare_run_control_plane(ws, state))
        if root_authority is not None:
            settings = operational_settings.load_settings(environment=os.environ)
            if settings.digest != state["settings_digest"]:
                raise ValueError("initial settings changed before snapshot capture")
            state["settings_snapshot"] = settings.to_dict()
    except Exception as exc:
        return {
            "error": "whole-run control-plane initialization failed closed: "
            f"{exc.__class__.__name__}: {exc}",
            "refused": True,
            "step": state["step"],
        }
    try:
        with _stage_store(ws, state["run_id"]).transaction(state["run_id"]):
            save(ws, state)
            phase_harness.initialize(sys.modules[__name__], ws, state)
            _stage_bootstrap_pristine_root(ws, state)
            state = load(ws)
    except (ValueError, OSError) as exc:
        return {
            "error": "phase runtime initialization refused: " + str(exc),
            "refused": True,
            "run_id": state["run_id"],
        }
    tp.trace(
        ws,
        "loop_init",
        goal=goal,
        spec_path=spec_path,
        first_step=state["step"],
        max_fix_cycles=max_fix_cycles,
        checkpoints=checkpoints,
        design=bool(design or design_only),
        design_only=bool(design_only),
    )
    out = dict(state)
    return out


# --------------------------------------------------------------- contracts


def _step_contract(step: str, state: dict, ws: str | None = None) -> dict:
    task = _current_task(state)
    if step == "retro":
        context = _phase_bridge_context(ws, state)
        if context is None:
            raise ValueError("Retro worker requires admitted phase routing")
        paths = context["configuration"]["output_paths"].get("retro")
        if not isinstance(paths, dict) or not paths:
            raise ValueError("Retro requires declared output paths")
        return tp.build_contract(
            "RETRO: sealed terminal evidence",
            read_only=True,
            write_allow=list(paths.values()),
            tools=["Read", "Grep", "Glob", "Bash", "Write"],
        )
    if step == "pm":
        return tp.build_contract(
            f"PM: {state['goal']}",
            read_only=True,
            write_allow=["specs/**", "docs/**"],
            tools=["Read", "Grep", "Glob", "WebSearch", "Bash", "Write"],
        )
    if step == "design":
        return tp.build_contract(
            f"DESIGN: {state['goal']}",
            read_only=True,
            write_allow=["design/**"],
            tools=["Read", "Grep", "Glob", "WebSearch", "Bash", "Write"],
        )
    if step == "plan":
        return tp.build_contract(
            f"PLAN: {state['goal']}",
            read_only=True,
            write_allow=["plan/**"],
            tools=["Read", "Grep", "Glob", "Bash", "Write"],
        )
    if step in ("execute", "fix"):
        verb = "EXECUTE" if step == "execute" else "FIX"
        return tp.build_contract(
            f"{verb}: {task['id']}",
            scope=task["scope"],
            test_command=task.get("tests"),
            plan_minted=True,
            regression_gate=True,
            test_timeout_seconds=tp.task_test_timeout_seconds(task),
            tools=["Read", "Grep", "Glob", "Bash", "Write", "Edit", "MultiEdit"],
        )
    if step == "evaluate":
        paths = _phase_bridge_context(ws, state)["configuration"]["output_paths"]["evaluate"]
        return tp.build_contract(
            f"EVALUATE: {task['id']}",
            read_only=True,
            write_allow=runtime_storage.worker_write_allow(ws) + list(paths.values()),
            tools=["Read", "Grep", "Glob", "Bash", "Write"],
        )
    if step == "em":
        paths = _phase_bridge_context(ws, state)["configuration"]["output_paths"]["engineering"]
        return tp.build_contract(
            "EM review",
            read_only=True,
            write_allow=runtime_storage.worker_write_allow(ws) + list(paths.values()),
            tools=["Read", "Grep", "Glob", "Bash", "Write", "Edit"],
        )
    raise ValueError(f"no contract for step {step}")


def _bind_worker_submission(
    ws: str, state: dict, step: str, contract: dict, task: dict | None
) -> dict:
    """Bind worker lifecycle to its exact loop submission, without gating."""
    if step not in WORKER_SUBMISSION_STEPS:
        return contract
    if state.get("submission_required") is not True:
        raise ValueError("phase dispatch requires submission authority")
    task_name = (
        (task or {}).get("id") or "engineering-signoff" if step == "em" else (task or {}).get("id")
    )
    lifecycle = contract.get("worker_lifecycle") or {}
    return tp.bind_submission_contract(
        contract,
        ws,
        task=str(task_name),
        stage=step,
        slot=lifecycle.get("slot") or tp.task_slot(),
        locator={"type": "loop_submission"},
        validation_rule="loop-submission/v1",
    )


def _current_task(state: dict):
    if state.get("step") in {"em", "signoff", "retro", "done", "failed"}:
        return None
    tasks = state.get("tasks")
    if not tasks:
        return None
    i = state.get("current_task", 0)
    return tasks[i] if 0 <= i < len(tasks) else None


def _reserve_worker_dispatch_ref(*args, **kwargs):
    return dispatch._reserve_worker_dispatch_ref(sys.modules[__name__], *args, **kwargs)


def _parallel_evaluate_workspace(*args, **kwargs):
    return dispatch._parallel_evaluate_workspace(sys.modules[__name__], *args, **kwargs)


def _edge_nudges(ws: str, changed, base: str) -> list:
    """Spot side-effect channels the import scanner cannot see (v2.0.0):
    SQL/migrations, HTTP calls, queue/topic messaging in the diff. Each
    nudge asks the reviewer to record the runtime edge (`tp graph edge`)
    so the NEXT change to that surface has a true blast radius."""
    import re as _re
    import subprocess as _sp

    nudges = []
    try:
        names = " ".join(changed)
        if _re.search(r"\.sql\b|/migrations?/", names):
            nudges.append(
                "diff touches SQL/migrations - schema changes ripple to "
                "every consumer of those tables; record the edge: "
                "tp graph edge <consumer-module> <db-module> --kind data"
            )
        diff = _sp.run(
            ["git", "diff", "-U0", base, "--", *changed[:50]],
            cwd=ws,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout[:60000]
        added = "\n".join(l for l in diff.splitlines() if l.startswith("+"))
        if _re.search(
            r"https?://|requests\.|urllib|fetch\(|axios"
            r"|http\.client|HttpClient",
            added,
        ):
            nudges.append(
                "diff adds HTTP calls - cross-service effects are not "
                "import edges; record them: tp graph edge <this-module> "
                "<called-service> --kind runtime"
            )
        if _re.search(
            r"publish|subscribe|topic|queue|kafka|sqs|rabbit"
            r"|emit\(",
            added,
            _re.I,
        ):
            nudges.append(
                "diff touches messaging (topic/queue) - consumers are "
                "invisible to the import graph; record them: tp graph "
                "edge <consumer> <contract:event-name> --kind consumes; "
                "record the producer with --kind provides. Dependency edges "
                "point from the dependent to the contract so contract changes "
                "impact consumers in the correct direction"
            )
    except (OSError, _sp.SubprocessError, UnicodeDecodeError) as e:
        # Degraded nudging must be VISIBLE, never silent (v2.3.0): the
        # reviewer loses side-effect-channel hints, so say so once.
        import sys as _sys

        print(
            f"taskplane: edge-nudge scan degraded ({e.__class__.__name__}: "
            f"{e}) — record runtime edges manually via `tp graph edge`",
            file=_sys.stderr,
        )
        try:
            tp.trace(ws, "edge_nudges_failed", error=str(e))
        except Exception:
            pass
    return nudges


def _diff_files(ws: str, base: str) -> list:
    import subprocess

    def run(args):
        return subprocess.run(
            ["git", *args],
            cwd=ws,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout

    return [
        f
        for f in (
            run(["diff", "--name-only", base]) + run(["ls-files", "--others", "--exclude-standard"])
        ).splitlines()
        if f
    ]


_REVIEW_RUNTIME_BUNDLE = None
_REVIEW_REQUIRED_MODULES = (
    "storage",
    "taskplane_lite",
    "review_evidence",
    "review",
    "graph_quality",
)
_REVIEW_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _review_source_stat(value) -> tuple:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _verified_review_module_source(checkout_root: str, module_name: str) -> dict:
    """Pin verified bytes for one direct target-checkout Python module."""
    root = os.path.realpath(os.path.abspath(checkout_root))
    if (
        not os.path.isdir(root)
        or os.path.islink(root)
        or not _REVIEW_MODULE_NAME.fullmatch(str(module_name or ""))
    ):
        raise RuntimeError("target review module root or name is invalid")
    path = os.path.abspath(os.path.join(root, module_name + ".py"))
    resolved = os.path.realpath(path)
    try:
        contained = os.path.commonpath((root, resolved)) == root
    except ValueError:
        contained = False
    if not contained or resolved != path:
        raise RuntimeError(f"target review module escapes checkout: {module_name}")
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise RuntimeError(f"required target review module is unavailable: {module_name}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError(f"target review module is not a regular file: {module_name}")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"target review module could not be pinned: {module_name}") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            opened_before = os.fstat(stream.fileno())
            source = stream.read()
            opened_after = os.fstat(stream.fileno())
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    try:
        after = os.lstat(path)
    except OSError as exc:
        raise RuntimeError(f"target review module changed while pinned: {module_name}") from exc
    handle_stable = _review_source_stat(opened_before) == _review_source_stat(opened_after)
    if (
        _review_source_stat(before) != _review_source_stat(after)
        or not handle_stable
        or not os.path.samestat(before, opened_before)
        or not stat.S_ISREG(before.st_mode)
        or not stat.S_ISREG(opened_before.st_mode)
        or int(opened_after.st_size) != len(source)
        or os.path.realpath(path) != path
    ):
        raise RuntimeError(f"target review module changed while pinned: {module_name}")
    return {
        "name": module_name,
        "path": path,
        "source": source,
        "identity": _review_source_stat(after),
        "sha256": hashlib.sha256(source).hexdigest(),
    }


class _CheckoutReviewModuleBundle:
    """Isolated target modules whose local imports remain target-bound."""

    def __init__(self, checkout_root: str):
        import builtins

        self.root = os.path.realpath(os.path.abspath(checkout_root))
        self.sources = {}
        self.modules = {}
        self._base_import = builtins.__import__
        self._builtins = dict(vars(builtins))
        self._builtins["__import__"] = self._target_import
        self.namespace = hashlib.sha256(self.root.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _leaf(import_name: str) -> str | None:
        name = str(import_name or "")
        if name.startswith("taskplane."):
            name = name.rsplit(".", 1)[-1]
        elif "." in name:
            return None
        return name if _REVIEW_MODULE_NAME.fullmatch(name) else None

    def _has_target(self, import_name: str) -> str | None:
        leaf = self._leaf(import_name)
        if not leaf:
            return None
        try:
            os.lstat(os.path.join(self.root, leaf + ".py"))
        except OSError:
            return None
        return leaf

    def pin(self, module_name: str) -> dict:
        leaf = self._leaf(module_name)
        if not leaf:
            raise RuntimeError("target review module name is invalid")
        source = self.sources.get(leaf)
        if source is None:
            source = _verified_review_module_source(self.root, leaf)
            self.sources[leaf] = source
        return source

    def assert_required_current(self) -> None:
        for name in _REVIEW_REQUIRED_MODULES:
            source = self.sources.get(name)
            if source is None:
                raise RuntimeError(f"required target review module was not pinned: {name}")
            try:
                current = os.lstat(source["path"])
            except OSError as exc:
                raise RuntimeError(f"required target review module changed: {name}") from exc
            if (
                os.path.realpath(source["path"]) != source["path"]
                or _review_source_stat(current) != source["identity"]
            ):
                raise RuntimeError(f"required target review module changed: {name}")

    def _target_import(self, import_name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0:
            target = self._has_target(import_name)
            if target:
                return self.load(target)
        imported = self._base_import(import_name, globals, locals, fromlist, level)
        path = os.path.realpath(str(getattr(imported, "__file__", "") or ""))
        if path:
            try:
                contained = os.path.commonpath((self.root, path)) == self.root
            except ValueError:
                contained = False
            if not contained and os.path.basename(os.path.dirname(path)) == "taskplane":
                raise ImportError(f"launcher-owned taskplane module refused: {import_name}")
        return imported

    def load(self, module_name: str):
        import types

        leaf = self._leaf(module_name)
        if not leaf:
            raise ImportError(f"target review module is invalid: {module_name}")
        if leaf in self.modules:
            return self.modules[leaf]
        source = self.pin(leaf)
        private_name = f"_taskplane_checkout_{self.namespace}_{leaf}"
        module = types.ModuleType(private_name)
        module.__file__ = source["path"]
        module.__package__ = ""
        module.__dict__["__builtins__"] = self._builtins
        self.modules[leaf] = module

        # Decorators may consult sys.modules while the class body executes.
        # The private identity exists only for that bounded execution and the
        # launcher's table is restored byte-for-byte afterward.
        missing = object()
        previous = sys.modules.get(private_name, missing)
        sys.modules[private_name] = module
        try:
            # Execute only the pinned local module bytes verified by pin(); this is
            # the checkout module loader, not an expression from request data.
            exec(compile(source["source"], source["path"], "exec"), module.__dict__)  # nosec B102
        except Exception:
            self.modules.pop(leaf, None)
            raise
        finally:
            if previous is missing:
                sys.modules.pop(private_name, None)
            else:
                sys.modules[private_name] = previous
        return module


def _review_runtime_modules():
    """Return one checkout-consistent runtime/evidence/review bundle."""
    global _REVIEW_RUNTIME_BUNDLE
    root = os.path.realpath(os.path.dirname(__file__))
    cached = _REVIEW_RUNTIME_BUNDLE
    force_private = False
    if isinstance(cached, dict) and cached.get("root") == root:
        try:
            cached["loader"].assert_required_current()
        except RuntimeError:
            # The checkout advanced in this process. Discard every pinned
            # policy/runtime module and reload current verified bytes as one
            # private bundle; path-equal canonical imports may still be old.
            _REVIEW_RUNTIME_BUNDLE = None
            force_private = True
        else:
            return (cached["runtime"], cached["evidence"], cached["review"])

    loader = _CheckoutReviewModuleBundle(root)
    pinned = {name: loader.pin(name) for name in _REVIEW_REQUIRED_MODULES}

    import review as imported_review
    import review_evidence as imported_evidence
    import graph_quality as imported_graph_quality
    import storage as imported_storage

    imported_runtime_path = os.path.realpath(str(getattr(tp, "__file__", "") or ""))
    storage_path = os.path.realpath(str(getattr(imported_storage, "__file__", "") or ""))
    evidence_path = os.path.realpath(str(getattr(imported_evidence, "__file__", "") or ""))
    review_path = os.path.realpath(str(getattr(imported_review, "__file__", "") or ""))
    graph_quality_path = os.path.realpath(
        str(getattr(imported_graph_quality, "__file__", "") or "")
    )
    consistent = (
        not force_private
        and imported_runtime_path == pinned["taskplane_lite"]["path"]
        and storage_path == pinned["storage"]["path"]
        and evidence_path == pinned["review_evidence"]["path"]
        and review_path == pinned["review"]["path"]
        and graph_quality_path == pinned["graph_quality"]["path"]
        and getattr(imported_evidence, "runtime_storage", None) is imported_storage
        and getattr(imported_review, "tp", None) is tp
        and getattr(imported_review, "runtime_storage", None) is imported_storage
        and getattr(imported_review, "review_evidence_runtime", None) is imported_evidence
        and getattr(imported_review, "terminal_truth_runtime", None) is terminal_truth
    )
    if consistent:
        runtime, evidence, review_kernel = tp, imported_evidence, imported_review
        graph_quality_kernel = imported_graph_quality
    else:
        target_storage = loader.load("storage")
        runtime = loader.load("taskplane_lite")
        evidence = loader.load("review_evidence")
        review_kernel = loader.load("review")
        graph_quality_kernel = loader.load("graph_quality")
        # The provider client and live receipt are orchestrator-owned object
        # identities.  A private checkout ReviewKernel must consume those
        # launcher's exact classes rather than a second private import that no
        # authentic live receipt could ever inhabit.
        review_kernel.terminal_truth_runtime = terminal_truth
        runtime_import = runtime.__dict__["__builtins__"]["__import__"]
        if (
            getattr(evidence, "runtime_storage", None) is not target_storage
            or getattr(review_kernel, "tp", None) is not runtime
            or getattr(review_kernel, "runtime_storage", None) is not target_storage
            or getattr(review_kernel, "review_evidence_runtime", None) is not evidence
            or getattr(review_kernel, "terminal_truth_runtime", None) is not terminal_truth
            or runtime_import("storage") is not target_storage
        ):
            raise RuntimeError("target review runtime bundle is internally inconsistent")
    _REVIEW_RUNTIME_BUNDLE = {
        "root": root,
        "runtime": runtime,
        "evidence": evidence,
        "review": review_kernel,
        "graph_quality": graph_quality_kernel,
        "loader": loader,
    }
    return runtime, evidence, review_kernel


class _ReviewGraphQualityError(RuntimeError):
    """Current graph evidence cannot authorize selective lens dispatch."""

    def __init__(self, quality: dict, reference: dict):
        self.quality = quality
        self.reference = reference
        reasons = ", ".join(quality.get("reasons") or []) or "graph quality is incomplete"
        super().__init__(reasons)


def _strict_review_graph_quality(
    review_ws: str,
    *,
    target: dict,
    graph: dict,
    impact: dict,
    files: list,
    symbols: list,
    review_module,
    evidence_module,
) -> tuple[dict, dict, object]:
    """Persist admissible current graph evidence before the one route."""
    graph_quality = (_REVIEW_RUNTIME_BUNDLE or {}).get("graph_quality")
    if graph_quality is None:
        raise RuntimeError("target graph-quality runtime is unavailable")
    source_change = any(
        os.path.splitext(path)[1].lower()
        in {".py", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".go", ".cs", ".java", ".rb"}
        for path in files
    )
    raw_expander = review_module.bounded_caller_expander(graph)
    expansion_cache = {}

    def one_bounded_expansion(**kwargs):
        # Preflight and ReviewKernel consume one identical expansion result;
        # the adapter itself is invoked at most once.
        if "result" not in expansion_cache:
            expansion_cache["result"] = raw_expander(**kwargs)
        return json.loads(json.dumps(expansion_cache["result"]))

    bounded_expander = one_bounded_expansion if symbols or not source_change else None
    quality = graph_quality.assess(
        graph,
        target_head=str(target.get("head") or ""),
        changed_files=files,
        changed_symbols=symbols,
        impact=impact,
        caller_expander=bounded_expander,
        snapshot={
            "target_fingerprint": target.get("fingerprint"),
            "target_head": target.get("head"),
        },
    )
    store = evidence_module.ArtifactStore(review_ws)
    reference = store.put("graph-quality", quality, fingerprint=quality["fingerprint"])
    if quality.get("status") != "complete" or quality.get("sufficient") is not True:
        raise _ReviewGraphQualityError(quality, reference)
    return quality, reference, bounded_expander


def _delivery_diff_patch(
    ws: str, diff_ws: str, *, base: str, files: list, scope: list, review
) -> tuple[str, dict | None]:
    """Retry one measured capture under the existing human run-only policy."""
    state = load(ws)
    policy = None
    if run_context.selected(state):
        run_id = str(state["run_id"])
        policy = phase_harness.resource_policy(_stage_store(ws, run_id).load(run_id), run_id)
        if policy is not None and policy["actor"] != state["_stage_native_root_authority"].get(
            "actor"
        ):
            raise review.ReviewKernelError("diff resource policy actor differs from run authority")

    def identity():
        current = load(ws)
        if (
            not current
            or current.get("run_id") != state["run_id"]
            or current.get("_stage_native_root_authority") != state["_stage_native_root_authority"]
        ):
            raise review.ReviewKernelError("diff capture run or authority changed")
        current_policy = phase_harness.resource_policy(
            _stage_store(ws, str(current["run_id"])).load(str(current["run_id"])),
            str(current["run_id"]),
        )
        resolved = tp._run(["git", "rev-parse", "--verify", base + "^{commit}"], cwd=diff_ws)
        if resolved.returncode or not resolved.stdout.strip():
            raise review.ReviewKernelError("diff comparison base cannot be resolved")
        return {
            "run_id": current["run_id"],
            "authority": current.get("_stage_native_root_authority"),
            "policy": current_policy,
            "base": resolved.stdout.strip(),
            "head": tp.git_head(diff_ws),
            "scope": list(scope),
            "files": list(files),
            "source_fingerprint": tp.workspace_fingerprint(diff_ws, base),
        }

    pinned = identity() if policy is not None else None
    limit = review.DEFAULT_MAX_DIFF_BYTES
    rc, patch = review.canonical_diff_patch(diff_ws, base, paths=files, max_bytes=limit)
    capacity = None
    if rc == review.CANONICAL_DIFF_TOO_LARGE and pinned is not None:
        if identity() != pinned or pinned["policy"] != policy:
            raise review.ReviewKernelError("diff capture source, scope or policy changed")
        try:
            measured = json.loads(patch)
            required = measured["bytes"]
            valid = (
                measured["reason_code"] == "canonical_diff_too_large"
                and measured["max_diff_bytes"] == limit
                and type(required) is int
                and required > limit
            )
        except (ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            raise review.ReviewKernelError("diff capture size measurement is invalid")
        # This admission only increases capture capacity. The existing raw-diff
        # custody owner still enforces its independent serialized-byte cap.
        if required > REVIEW_RAW_DIFF_MAX_BYTES:
            raise review.ReviewKernelError("measured diff exceeds independent custody capacity")
        rc, patch = review.canonical_diff_patch(diff_ws, base, paths=files, max_bytes=required)
        if identity() != pinned:
            raise review.ReviewKernelError("diff capture source, scope or policy changed")
        if not rc and len(patch.encode("utf-8")) != required:
            raise review.ReviewKernelError("diff capture differs from measured size")
        capacity = {
            "bytes": required,
            "previous_max_diff_bytes": limit,
            "max_diff_bytes": required,
            "capacity_increase_bytes": required - limit,
            "additional_cost": "unknown",
            "run_id": pinned["run_id"],
            "resource_policy_fingerprint": policy["fingerprint"],
            "source_fingerprint": pinned["source_fingerprint"],
            "base": pinned["base"],
            "head": pinned["head"],
            "scope": pinned["scope"],
        }
    if rc:
        raise review.ReviewKernelError(
            patch if rc == review.CANONICAL_DIFF_TOO_LARGE else "canonical diff derivation failed"
        )
    return patch, capacity


def _review_kernel(
    ws: str,
    diff_ws: str,
    *,
    base: str,
    step: str,
    task: dict | None,
    graph: dict,
    impact: dict,
    requirement: dict | None,
    test_evidence: Mapping[str, object] | None = None,
    retry_context: dict | None = None,
    expanded_route_provider_client: terminal_truth.ExpandedRouteProviderClient | None = None,
    expanded_route_provider_receipt: terminal_truth.ExpandedRouteProviderReceipt | None = None,
    delivery_mode_receipt: object = _DELIVERY_MODE_AUTHORITY_UNSET,
) -> tuple[dict, dict]:
    """One evidence/routing kernel shared by Evaluate and final EM."""
    import hashlib
    import subprocess

    runtime_kernel, review_evidence, review = _review_runtime_modules()

    files = [
        f
        for f in _diff_files(diff_ws, base)
        if not f.startswith(lens_router.LOOP_OWNED)
        and (
            not task
            or not task.get("scope")
            or runtime_kernel.match_any(f, task.get("scope") or [])
        )
    ]
    patch, diff_capacity = _delivery_diff_patch(
        ws, diff_ws, base=base, files=files, scope=(task or {}).get("scope") or [], review=review
    )
    if files and not patch:
        raise review.ReviewKernelError("canonical governed diff is empty for changed task files")
    head = tp.git_head(diff_ws) or ""
    target_material = {
        "workspace": os.path.realpath(diff_ws),
        "head": head,
        "base": base,
        "step": step,
        "task": (task or {}).get("id") or ("engineering-signoff" if step == "em" else None),
    }
    target = {
        **target_material,
        "fingerprint": hashlib.sha256(
            json.dumps(target_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    store = review_evidence.ArtifactStore(diff_ws)
    # Raw source diffs are private working evidence, not permanent canonical
    # history. The single store lock covers restart sweep + put + capacity.
    locator = runtime_storage.load_workspace_locator(diff_ws)
    if not locator:
        raise review.ReviewKernelError("loop review requires its current run locator")
    diff_ref = store_retained_review_diff(
        diff_ws,
        store=store,
        payload=_retained_review_diff_payload(
            base=base,
            files=files,
            patch=patch,
            run_id=locator["run_id"],
            review_id=target["fingerprint"],
        ),
    )
    stage = "review" if step == "em" else EVALUATE_ROUTE_STAGE
    changed_symbols = review.changed_symbols_from_patch(patch)
    quality_ref = None
    if step == "evaluate":
        _, quality_ref, caller_expander = _strict_review_graph_quality(
            diff_ws,
            target=target,
            graph=graph,
            impact=impact,
            files=files,
            symbols=changed_symbols,
            review_module=review,
            evidence_module=review_evidence,
        )
    else:
        caller_expander = review.bounded_caller_expander(graph)
    delivery_mode_argument = (
        {"delivery_mode_receipt": delivery_mode_receipt}
        if delivery_mode_receipt is not _DELIVERY_MODE_AUTHORITY_UNSET
        else {}
    )
    manifest = review.start_review(
        diff_ws,
        target=target,
        graph=graph,
        impact=impact,
        diff={
            "files": files,
            "changed_symbols": changed_symbols,
            "artifact": review._portable_ref(diff_ref),
            **({"capacity": diff_capacity} if diff_capacity is not None else {}),
        },
        requirement=requirement or {},
        test_evidence=test_evidence or {},
        acceptance=(requirement or {}).get("acceptance") or [],
        contracts=(task or {}).get("contracts") or [],
        stage=stage,
        task_type=(task or {}).get("type"),
        base=base,
        caller_expander=caller_expander,
        routing_content=review.changed_content_from_patch(patch),
        retry_lenses=((retry_context or {}).get("lenses") if step == "evaluate" else None),
        retry_source_run_id=(
            (retry_context or {}).get("source_run_id") if step == "evaluate" else None
        ),
        expanded_route_provider_client=expanded_route_provider_client,
        expanded_route_provider_receipt=expanded_route_provider_receipt,
        **delivery_mode_argument,
    )
    if diff_capacity is not None:
        manifest = {**manifest, "diff_capacity": diff_capacity}
    state = review._load_state(diff_ws, manifest.get("run_id"))
    if quality_ref is not None and state.get("quality") != quality_ref:
        # A route is immutable. A mismatch is terminal evidence, never a
        # reason to patch or invoke the selector again after sealing.
        raise review.ReviewKernelError("sealed graph quality differs from pre-routing authority")
    return manifest, (
        state.get("routing")
        or {"lenses": [], "context": {"status": manifest.get("status"), "breadth": "routed"}}
    )


def _bind_stateless_review_contract_actions(
    review_ws: str, manifest: dict, *, task_id: str, now: int | None = None
) -> dict:
    """Attach one signed, self-activating contract action to every slot.

    ReviewKernel's immutable brief and lease remain the source identities.
    This projection adds no predecessor/session material and creates no active
    slot: a fresh exact worker verifies the action, then derives its own
    least-privilege read-only enforcement cache before evidence access.
    """
    import hashlib

    runtime_kernel, review_evidence, _ = _review_runtime_modules()

    if not isinstance(manifest, dict) or manifest.get("status") != "ready":
        return manifest
    bound = json.loads(json.dumps(manifest))
    store = review_evidence.ArtifactStore(review_ws)
    run_id = str(bound.get("run_id") or "")
    if not run_id or not str(task_id or "").strip():
        raise ValueError("review contract bootstrap needs run and task identity")
    wait_policies = []
    outstanding_members = []
    for slot in bound.get("slots") or []:
        if not isinstance(slot, dict):
            raise ValueError("review contract bootstrap slot is malformed")
        lease = store.read(slot.get("lease") or {})
        brief = store.read(slot.get("brief") or {})
        wait_policies.append(brief.get("wait_policy"))
        outstanding_members.append(str(slot.get("slot_id") or ""))
        producer = brief.get("producer_contract")
        role = brief.get("role") or {}
        role_marker = str(role.get("role_marker") or "")
        worker_identity = str(role.get("task_name") or "")
        if not role_marker or not worker_identity:
            raise ValueError("review contract bootstrap lacks exact worker identity")
        action_material = {
            "run_id": run_id,
            "task_id": str(task_id),
            "slot_id": lease.get("slot_id"),
            "lease_fingerprint": lease.get("lease_fingerprint"),
            "worker_identity": worker_identity,
        }
        action_id = (
            "review-action-"
            + hashlib.sha256(
                json.dumps(action_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:24]
        )
        action = runtime_kernel.issue_review_contract_action(
            review_ws,
            run_id=run_id,
            task_id=str(task_id),
            role_marker=role_marker,
            worker_identity=worker_identity,
            action_id=action_id,
            lease=lease,
            producer_contract=producer,
            result_path=str(brief.get("result_path") or ""),
            now=now,
        )
        expected = {
            "run_id": run_id,
            "task_id": str(task_id),
            "role_marker": role_marker,
            "worker_identity": worker_identity,
            "action_id": action_id,
            "lens_ids": list(lease.get("lens_ids") or []),
            "target_fingerprint": str(lease.get("target_fingerprint") or ""),
            "lease_fingerprint": str(lease.get("lease_fingerprint") or ""),
            "canonical_revision": int(lease.get("canonical_revision") or 0),
        }
        encode = (
            lambda value: base64.urlsafe_b64encode(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )
        action_token = encode(action)
        expected_token = encode(expected)
        # Bind the activation command to the CLI shipped beside this loop
        # implementation.  A governed suite can intentionally execute a
        # target worktree with its launcher runtime already imported; using
        # ``tp.__file__`` in that context crosses revisions and emits a CLI
        # path the target parser must reject.  The sibling path keeps the
        # production command target-local without replacing global modules.
        command_argv = [
            sys.executable,
            os.path.realpath(os.path.join(os.path.dirname(__file__), "tp.py")),
            "review",
            "activate-contract",
            "--workspace",
            os.path.realpath(review_ws),
            "--task-slot",
            producer["task_slot"],
            "--signed-action",
            action_token,
            "--expected-identity",
            expected_token,
        ]
        slot["contract_bootstrap"] = {
            "schema": "taskplane.review-contract-bootstrap/v1",
            "required_before_evidence": True,
            "activation_order": "orchestrator_before_subagent_start",
            "authority": "signed_action",
            "active_slot_semantics": "derived_cache_not_authority",
            "function": "taskplane_lite.activate_review_contract_action",
            # Dispatch metadata, not an inline shell prefix. The orchestrator
            # activates the signed contract first and injects this exact slot
            # into the native child lifecycle so SubagentStart can bind the
            # child to its lease before evidence is authored.
            "environment": {"TASKPLANE_TASK": producer["task_slot"]},
            "command": "review activate-contract",
            "command_argv": command_argv,
            "host_command": shlex.join(command_argv),
            "task_slot": producer["task_slot"],
            "workspace": os.path.realpath(review_ws),
            "expected": expected,
            "action": action,
        }
        tp.record_expected_dispatch(
            review_ws, "lens", role["agent"], role["model_tier"], tp.model_for_tier(role["model_tier"]),
            ref=lease["lease_fingerprint"], task_name=worker_identity,
            reasoning_effort=role["reasoning_effort"], role_marker_value=role_marker,
        )
    if outstanding_members:
        if any(not isinstance(row, Mapping) for row in wait_policies) or any(
            dict(row) != dict(wait_policies[0]) for row in wait_policies[1:]
        ):
            raise ValueError("review contract bootstrap needs one shared wait policy")
        bound["wait_invocation"] = event_wait_invocation(wait_policies[0], outstanding_members)
        bound["collection"] = {
            "schema": "taskplane.review-collection-bridge/v1",
            "function": "loop.collect_review_bridge",
            "run_id": run_id,
            "release_incomplete_producers": True,
        }
    return bound


def _scopes_overlap(a, b) -> bool:
    """Two scopes conflict when one's fixed prefix contains the other's, on
    path-segment boundaries — conflicting tasks are serialized into later
    waves. Segment-aware so sibling dirs (src/a vs src/ab) do NOT collide,
    and empty-prefix globs don't conflict with everything. (The path math
    itself lives in the kernel — tp.scope_stems / tp.seg_prefix.)"""
    sa, sb = tp.scope_stems(a), tp.scope_stems(b)
    return any(tp.seg_prefix(x, y) or tp.seg_prefix(y, x) for x in sa for y in sb)


def _declared_repository_test_files(ws: str, tasks: list[dict]) -> set[str]:
    present: set[str] = set()
    for task in tasks:
        command = task.get("tests")
        if not isinstance(command, str):
            continue
        try:
            tokens = shlex.split(command)
        except ValueError:
            continue
        for token in tokens:
            path = token.split("::", 1)[0].replace("\\", "/").removeprefix("./")
            if path.endswith(".py") and "/" in path and os.path.isfile(os.path.join(ws, path)):
                present.add(path)
    return present


def select_ready_tasks(
    tasks: list[dict],
    *,
    passed: set[str],
    repository_files: set[str],
    allow_isolated_variants: bool = False,
) -> tuple[list[dict], list[dict], dict]:
    """Select the executable pairwise-disjoint ready set from the Plan.

    This is the runtime consumer of ``plan_topology``.  In particular, it
    respects implicit missing-test-artifact predecessors, so an apparently
    disjoint consumer cannot become false-ready before its test producer.
    """
    topology = plan_topology.classify_plan(tasks, repository_files=repository_files)
    by_id = {str(task.get("id")): task for task in tasks}
    pair_map = {frozenset((str(row["left"]), str(row["right"]))): row for row in topology["pairs"]}
    selected: list[dict] = []
    held: list[dict] = []
    for task_id in topology["task_ids"]:
        task = by_id[task_id]
        if task.get("status", "pending") != "pending":
            continue
        dependencies = set(topology["effective_dependencies"][task_id])
        unmet = sorted(dependencies - passed)
        if unmet:
            shared_owner = next(
                (
                    pair_map[frozenset((task_id, dependency))]["shared_owner"]
                    for dependency in unmet
                    if frozenset((task_id, dependency)) in pair_map
                ),
                f"dependency:{unmet[0]}",
            )
            held.append(
                {
                    "task": task_id,
                    "reason": "waiting on deps: " + ",".join(unmet),
                    "shared_owner": shared_owner,
                }
            )
            continue
        missing = list((topology.get("missing_test_assets") or {}).get(task_id) or [])
        if missing:
            held.append(
                {
                    "task": task_id,
                    "reason": "missing test assets: " + ",".join(missing),
                    "shared_owner": "test-artifact:" + missing[0],
                }
            )
            continue
        blocker = next(
            (
                pair_map[frozenset((task_id, str(member["id"])))]
                for member in selected
                if pair_map[frozenset((task_id, str(member["id"])))]["disposition"] == "serialized"
                and not (
                    allow_isolated_variants
                    and task.get("variant")
                    and member.get("variant")
                    and task.get("variant") != member.get("variant")
                )
            ),
            None,
        )
        if blocker is not None:
            held.append(
                {
                    "task": task_id,
                    "reason": f"serialized by {blocker['shared_owner']}",
                    "shared_owner": blocker["shared_owner"],
                }
            )
            continue
        selected.append(task)
    return selected, held, topology


def _dispatch_telemetry_identity(ws: str, state: Mapping[str, object]) -> dict:
    try:
        locator = runtime_storage.load_workspace_locator(ws)
    except Exception:
        locator = None
    run_id = str((locator or {}).get("run_id") or state.get("run_id") or "")
    if not run_id:
        run_id = (
            "loop-"
            + hashlib.sha256(
                json.dumps(
                    {
                        "workspace": os.path.realpath(ws),
                        "goal": state.get("goal"),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
    source_sha = str(state.get("baseline") or tp.git_head(ws) or "unknown")
    design_fingerprint = str(state.get("design_fingerprint") or hashlib.sha256(b"null").hexdigest())
    plan_fingerprint = str(state.get("plan_fingerprint") or "")
    if not plan_fingerprint:
        runtime_fields = {
            "status",
            "fix_cycles",
            "workspace",
            "target_commit",
            "_submission",
            "evaluation",
            "convergence_history",
            "convergence_revision",
            "reanchor_authority",
        }
        sealed_tasks = [
            {key: value for key, value in task.items() if key not in runtime_fields}
            for task in state.get("tasks") or []
            if isinstance(task, Mapping)
        ]
        plan_fingerprint = hashlib.sha256(
            json.dumps(sealed_tasks, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
    return {
        "run_id": run_id,
        "source_sha": source_sha,
        "design_fingerprint": design_fingerprint,
        "plan_fingerprint": plan_fingerprint,
    }


def _ensure_dispatch_telemetry(ws: str) -> dict:
    """Create/read the live binding ledger under the loop state lock."""
    clock = SystemClock()
    with mutate(ws) as locked:
        if locked is None:
            raise dispatch_telemetry.DispatchTelemetryError("no active loop")
        ledger = locked.get("dispatch_telemetry")
        if ledger is None:
            ledger = dispatch_telemetry.new_ledger(
                **_dispatch_telemetry_identity(ws, locked), started_at=clock.wall_time()
            )
            locked["dispatch_telemetry"] = ledger
        dispatch_telemetry.validate_ledger(ledger)
        return dict(ledger)


def _build_delivery_root_preparation(
    ws: str,
    state: Mapping[str, object],
    *,
    seed_ref: str,
    wave_id: str,
    prepared_at: str,
    operation_id: str,
    design: Mapping[str, object],
    plan: Mapping[str, object],
    pickups: list[Mapping[str, object]],
    outstanding_human_gates: list[Mapping[str, object]],
    predecessor_terminal_projection: Mapping[str, object],
) -> tuple[dict, dict, object]:
    """Build an idempotent seed without advancing the loop state."""
    settings = operational_settings.load_settings(environment=os.environ)
    if state.get("settings_digest") not in {None, settings.digest}:
        raise ValueError("root preparation settings changed during the run")
    run_id = str(state.get("run_id") or "").strip()
    candidate_sha = str(state.get("baseline") or "").strip()
    if not run_id or re.fullmatch(r"[0-9a-f]{40,64}", candidate_sha) is None:
        raise ValueError("root preparation run identity is incomplete")
    # A failed Plan CAS may leave the exact immutable seed on disk.  Reuse its
    # timestamp for the stable operation so retry proves identity instead of
    # conflicting only because wall time advanced.
    try:
        prior_seed = root_seed.load_root_seed(ws, seed_ref)
    except root_seed.RootSeedError:
        prior_seed = None
    if isinstance(prior_seed, Mapping) and prior_seed.get("operation_id") == str(operation_id):
        prepared_at = str(prior_seed.get("prepared_at") or prepared_at)
    context = {
        "run_id": run_id,
        "wave_id": str(wave_id),
        "candidate_sha": candidate_sha,
        "settings": settings,
        "delivery_mode": "iteration",
        "design": dict(design),
        "plan": dict(plan),
        "prepared_at": str(prepared_at),
        "operation_id": str(operation_id),
    }
    inputs = {
        "pickups": [dict(row) for row in pickups],
        "wave_budgets": {
            key: settings.limits.budgets[key]
            for key in ("max_actions", "target_tokens", "max_tokens")
        },
        "outstanding_human_gates": [dict(row) for row in outstanding_human_gates],
        "predecessor_terminal_projection": dict(predecessor_terminal_projection),
    }
    receipt = root_seed.prepare_root_seed(ws, seed_ref, context, inputs)
    seed = root_seed.load_root_seed(ws, receipt["seed_ref"])
    root_seed.verify_prepare_receipt(
        seed, receipt, settings=settings, expected_seed_ref=receipt["seed_ref"]
    )
    prepared = {
        "status": "prepared",
        "wave_id": str(wave_id),
        "seed_ref": receipt["seed_ref"],
        "seed_fingerprint": receipt["seed_fingerprint"],
        "prepare_receipt": receipt,
    }
    return receipt, prepared, settings


def prepare_delivery_root(
    ws: str,
    *,
    seed_ref: str,
    wave_id: str,
    prepared_at: str,
    operation_id: str,
    design: Mapping[str, object],
    plan: Mapping[str, object],
    pickups: list[Mapping[str, object]],
    outstanding_human_gates: list[Mapping[str, object]],
    predecessor_terminal_projection: Mapping[str, object],
) -> dict:
    """Prepare the public loop's sole reference-only root seed."""
    state = load(ws)
    if state is None:
        raise ValueError("root preparation requires an active loop")
    receipt, prepared, settings = _build_delivery_root_preparation(
        ws,
        state,
        seed_ref=seed_ref,
        wave_id=wave_id,
        prepared_at=prepared_at,
        operation_id=operation_id,
        design=design,
        plan=plan,
        pickups=pickups,
        outstanding_human_gates=outstanding_human_gates,
        predecessor_terminal_projection=predecessor_terminal_projection,
    )
    with mutate(ws) as locked:
        binding = receipt["binding"]
        if (
            locked is None
            or locked.get("run_id") != binding["run_id"]
            or locked.get("baseline") != binding["candidate_sha"]
        ):
            raise ValueError("root preparation run changed before commit")
        prior = locked.get("root_hygiene")
        if prior is not None and prior != prepared:
            raise ValueError("root preparation conflicts with existing wave")
        locked["settings_digest"] = settings.digest
        locked["root_hygiene"] = prepared
    return receipt


def _prepare_approved_plan_root(ws: str, state: Mapping[str, object]) -> dict:
    """Prepare only the first approved delivery wave before its state CAS."""
    from taskplane.primitives import content_fingerprint

    tasks = [dict(task) for task in state.get("tasks") or [] if isinstance(task, Mapping)]
    if not tasks:
        raise ValueError("approved Plan has no delivery tasks")
    wave_id = str(tasks[0].get("wave") or "execute")
    wave_tasks = [task for task in tasks if str(task.get("wave") or "execute") == wave_id]
    plan_path = os.path.join(ws, "plan", "tasks.json")
    with open(plan_path, "rb") as stream:
        plan_fingerprint = hashlib.sha256(stream.read()).hexdigest()
    design_fingerprint = str(state.get("design_fingerprint") or _design_evidence_fingerprint(ws))
    pickups = [
        {
            "id": str(task["id"]),
            "write_scopes": list(task.get("scope") or []),
            "disjointness_receipt_fingerprint": hashlib.sha256(
                json.dumps(
                    {"task": task["id"], "scope": task.get("scope") or []},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        }
        for task in wave_tasks
    ]
    # Reaffirming a changed Plan/source in the same run needs a new immutable
    # seed. Exact approval retries keep the same identity and timestamp; prior
    # generations (including legacy waves/... seeds) remain untouched.
    generation = content_fingerprint(
        {
            "run_id": state["run_id"],
            "wave_id": wave_id,
            "candidate_sha": state.get("baseline"),
            "design_fingerprint": design_fingerprint,
            "plan_fingerprint": plan_fingerprint,
            "settings_digest": state.get("settings_digest"),
            "pickups": pickups,
        }
    )
    seed_ref = os.path.relpath(
        os.path.join(
            runtime_storage.project_taskplane_home(ws), "root-seeds", generation + ".json"
        ),
        os.path.realpath(ws),
    ).replace(os.sep, "/")
    _, prepared, settings = _build_delivery_root_preparation(
        ws,
        state,
        seed_ref=seed_ref,
        wave_id=wave_id,
        prepared_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        operation_id="prepare-" + generation,
        design={"path": "design/contract.json", "fingerprint": design_fingerprint},
        plan={"path": "plan/tasks.json", "fingerprint": plan_fingerprint},
        pickups=pickups,
        outstanding_human_gates=[],
        predecessor_terminal_projection={"status": "none"},
    )
    return {
        "prepared": prepared,
        "settings_digest": settings.digest,
        "plan_fingerprint": plan_fingerprint,
    }


def open_delivery_wave(
    ws: str,
    *,
    host_start_receipt: Mapping[str, object],
    first_observation: Mapping[str, object],
    observation_authority: bytes,
    override: Mapping[str, object] | None = None,
) -> dict:
    """Consume prepared seed plus authenticated host start/observation."""
    if __package__:
        from . import host_native
    else:  # pragma: no cover
        import host_native
    state = load(ws)
    if state is None:
        raise ValueError("wave open requires an active loop")
    root = state.get("root_hygiene")
    if not isinstance(root, Mapping) or root.get("status") not in {"prepared", "open"}:
        raise ValueError("wave open requires a prepared root seed")
    settings = operational_settings.load_settings(environment=os.environ)
    if state.get("settings_digest") != settings.digest:
        raise ValueError("wave open settings do not match the prepared seed")
    seed = root_seed.load_root_seed(ws, str(root.get("seed_ref") or ""))
    root_seed.verify_prepare_receipt(
        seed,
        root.get("prepare_receipt"),
        settings=settings,
        expected_seed_ref=str(root.get("seed_ref") or ""),
    )
    start = host_native.validate_root_session_start(
        host_start_receipt, authority=observation_authority, seed=seed
    )
    if root.get("status") == "open":
        if (
            root.get("host_start_receipt") == start
            and root.get("first_observation") == first_observation
            and root.get("observation_authority_fingerprint")
            == hashlib.sha256(observation_authority).hexdigest()
        ):
            return copy.deepcopy(dict(root))
        raise ValueError("root generation is already open with different evidence")
    prior_ledger = state.get("dispatch_telemetry")
    prior_admission = (prior_ledger or {}).get("root_admission") or {}
    prior_meter = prior_admission.get("meter")
    generation = prior_meter is not None
    if generation:
        meter = native_session_meter.open_root_generation(
            first_observation, prior=prior_meter, authority=observation_authority
        )
        if meter.get("status") != "available":
            raise ValueError(
                "root generation observation refused: " + str(meter.get("reason_code"))
            )
    else:
        meter = native_session_meter.fold_root_observations(
            [first_observation], authority=observation_authority
        )
    watermark = meter.get("watermark") if isinstance(meter, Mapping) else None
    if (
        not isinstance(watermark, Mapping)
        or watermark.get("status_receipt_fingerprint") != start["fingerprint"]
    ):
        raise ValueError("first root observation is not bound to the host start receipt")
    first = meter.get("first_observed_input_tokens")
    reasons = []
    if meter.get("status") != "available":
        reasons.append(str(meter.get("reason_code") or "root_usage_unavailable"))
    if meter.get("resumed") is not False:
        reasons.append("root session is resumed")
    seed_budget = settings.workflow.root_session.consumer_projection("root-seed.prepare")[
        "seed_budget_tokens"
    ]
    if isinstance(first, bool) or not isinstance(first, int) or first <= 0:
        reasons.append("first observed input is missing or zero")
    elif first > seed_budget:
        reasons.append("first observed input exceeds seed budget")
    resource_policy = None
    if (
        override is None
        and reasons == ["first observed input exceeds seed budget"]
        and run_context.selected(state)
    ):
        # A long-lived root keeps its full cumulative counter. The existing
        # human resource decision overrides only this numeric seed check,
        # never host identity, available usage, or resume evidence.
        resource_store = _stage_store(ws, str(state["run_id"]))
        resource_policy = phase_harness.resource_policy(
            resource_store.load(str(state["run_id"])), str(state["run_id"])
        )
        if resource_policy is not None:
            if resource_policy["actor"] != (state.get("_stage_native_root_authority") or {}).get(
                "actor"
            ):
                raise ValueError("root resource policy actor differs from run authority")
            override = {
                "by": resource_policy["actor"],
                "reason": "Saved advisory resource policy " + resource_policy["fingerprint"],
            }
    attributed_override = None
    if reasons:
        if override is None:
            raise ValueError("; ".join(reasons))
        if (
            not isinstance(override, Mapping)
            or set(override) != {"by", "reason"}
            or not str(override.get("by") or "").strip()
            or not str(override.get("reason") or "").strip()
        ):
            raise ValueError("root-session override must be attributable")
        attributed_override = {
            "by": str(override["by"]),
            "reason": str(override["reason"]),
            "failed_checks": reasons,
        }
    with mutate(ws) as locked:
        if locked is None or locked.get("root_hygiene") != root:
            raise ValueError("root preparation changed before wave open")
        if locked.get("dispatch_telemetry") != prior_ledger:
            raise ValueError("root admission changed before wave open")
        if resource_policy is not None and (
            locked.get("run_id") != state["run_id"]
            or locked.get("_stage_native_root_authority")
            != state.get("_stage_native_root_authority")
            or phase_harness.resource_policy(
                resource_store.load(str(state["run_id"])), str(state["run_id"])
            )
            != resource_policy
        ):
            raise ValueError("root resource policy changed before wave open")
        ledger = locked.get("dispatch_telemetry")
        if ledger is None:
            ledger = dispatch_telemetry.new_ledger(
                **_dispatch_telemetry_identity(ws, locked), started_at=SystemClock().wall_time()
            )
            locked["dispatch_telemetry"] = ledger
        # The settings owner exposes one immutable Part A snapshot.  P10's
        # named prepare consumer has already validated it; admission receives
        # those exact four values rather than loading another source/default.
        policy = settings.workflow.root_session.to_dict()
        dispatch_telemetry.configure_root_admission(
            ledger, root_session_settings=policy, settings_digest=settings.digest
        )
        record_meter = (
            dispatch_telemetry.open_root_generation
            if generation
            else dispatch_telemetry.record_root_meter
        )
        record_meter(ledger, meter, observation_authority=observation_authority)
        ledger.setdefault("root_openings", []).append(
            {
                "seed_ref": root["seed_ref"],
                "host_start_receipt": copy.deepcopy(start),
                "first_observation": copy.deepcopy(dict(first_observation)),
            }
        )
        opened = {
            **dict(root),
            "status": "open",
            "host_start_fingerprint": start["fingerprint"],
            "host_start_receipt": copy.deepcopy(start),
            "first_observation": copy.deepcopy(dict(first_observation)),
            "host": {"adapter": start["host"], "runtime": start.get("host_version")},
            "session_pseudonym": start["session_pseudonym"],
            "meter": meter,
            "observation_authority_fingerprint": hashlib.sha256(observation_authority).hexdigest(),
            "conformance": "overridden" if reasons else "pass",
            "canary_eligible": not reasons,
            "override": attributed_override,
        }
        locked["root_hygiene"] = opened
    return opened


def admit_native_dispatch(
    ws: str,
    *,
    observation_authority: bytes,
    dispatch: Mapping[str, object],
    current_stage: str,
    outstanding_set_fingerprint: str,
    preserved_context_fingerprint: str,
    observations: list[Mapping[str, object]] | None = None,
) -> dict:
    """Advance the authenticated meter and atomically admit one dispatch."""
    with mutate(ws) as locked:
        if locked is None:
            raise ValueError("dispatch admission requires an active loop")
        root = locked.get("root_hygiene")
        if not isinstance(root, Mapping) or root.get("status") != "open":
            raise ValueError("dispatch admission requires an open fresh root")
        ledger = locked.get("dispatch_telemetry")
        if not isinstance(ledger, Mapping):
            raise ValueError("dispatch admission ledger is unavailable")
        if observations:
            admission = ledger.get("root_admission") or {}
            prior_meter = admission.get("meter") or {}
            meter = native_session_meter.fold_root_observations(
                observations, authority=observation_authority, prior=prior_meter.get("watermark")
            )
            dispatch_telemetry.record_root_meter(
                ledger, meter, observation_authority=observation_authority
            )
            locked["root_hygiene"] = {**dict(root), "meter": meter}
        decision = dispatch_telemetry.screen_dispatch(
            ledger,
            SystemClock(),
            current_stage=current_stage,
            outstanding_set_fingerprint=outstanding_set_fingerprint,
            preserved_context_fingerprint=preserved_context_fingerprint,
            observation_authority=observation_authority,
            admission_operation_id=str(dispatch.get("dispatch_id") or ""),
            dispatch=dispatch,
            resource_limits_advisory=run_context.resource_limits_advisory(ws),
        )
        if not decision.get("dispatch_allowed"):
            locked["root_hygiene"] = {
                **dict(locked["root_hygiene"]),
                "status": "admissions_closed",
                "admission_refusal": decision.get("fingerprint"),
            }
        return decision


def record_delivery_root_observation(
    ws: str, *, observation: Mapping[str, object], observation_authority: bytes
) -> dict:
    """Advance the one open root watermark from a real host-hook turn."""
    with mutate(ws) as locked:
        if locked is None or not isinstance(locked.get("root_hygiene"), Mapping):
            raise ValueError("root observation requires an active delivery root")
        root = locked["root_hygiene"]
        if root.get("status") != "open":
            raise ValueError("root observation requires an open delivery root")
        meter = native_session_meter.fold_root_observations(
            [observation],
            authority=observation_authority,
            prior=(root.get("meter") or {}).get("watermark"),
        )
        dispatch_telemetry.record_root_meter(
            locked["dispatch_telemetry"], meter, observation_authority=observation_authority
        )
        locked["root_hygiene"] = {**dict(root), "meter": meter}
        return meter


def start_evaluate_evidence_children(
    *,
    workspace: str,
    artifact_root: str,
    binding: Mapping[str, object],
    impact_manifest: Mapping[str, object],
) -> list[dict]:
    """Public composition root for the two required Evaluate producers."""
    return runtime_eval.start_evaluate_evidence_children(
        workspace,
        artifact_root=artifact_root,
        binding=dict(binding),
        impact_manifest=dict(impact_manifest),
    )


def observe_evaluate_evidence_child_start(
    *, artifact_root: str, assignment: Mapping[str, object], dispatch_id: str, native_task_name: str
) -> dict:
    """Bind one evidence-child start to the observed native dispatch."""
    return runtime_eval.observe_evaluate_evidence_child_start(
        artifact_root=artifact_root,
        assignment=dict(assignment),
        dispatch_id=dispatch_id,
        native_task_name=native_task_name,
    )


def complete_evaluate_evidence_child(
    *,
    workspace: str,
    artifact_root: str,
    run_id: str,
    assignment: Mapping[str, object],
    result: Mapping[str, object],
    work_units: int,
) -> dict:
    """Public composition root for one child producer terminal result."""
    return runtime_eval.complete_evaluate_evidence_child(
        workspace,
        artifact_root=artifact_root,
        run_id=run_id,
        assignment=dict(assignment),
        result=dict(result),
        work_units=work_units,
    )


def consume_evaluate_evidence_before_pass(
    value: Mapping[str, object],
    *,
    artifact_root: str,
    run_id: str,
    evaluator_attempt_id: str,
    expected_binding: Mapping[str, object],
) -> dict:
    """Consume the canonical two-child ledger directly before PASS."""
    del artifact_root  # run_id resolves the same canonical RunStore owner.
    return runtime_eval.consume_evaluate_evidence_before_pass(
        dict(value),
        run_id=run_id,
        evaluator_attempt_id=evaluator_attempt_id,
        expected_binding=dict(expected_binding),
    )


def _screen_public_native_route(
    ws: str,
    state: Mapping[str, object],
    *,
    stage: str,
    tasks: list[Mapping[str, object]],
    observation_authority: bytes | None,
    dispatch: Mapping[str, object],
) -> dict | None:
    """Enforce root preparation/open/meter admission before intent emission."""
    if stage not in {"execute", "fix", "evaluate", "plan", "em", "retro"}:
        return None
    if (
        stage in {"plan", "em", "retro"}
        and (state.get("dispatch_telemetry") or {}).get("root_admission") is None
    ):
        return None
    root = state.get("root_hygiene")
    if not isinstance(root, Mapping):
        raise ValueError("native dispatch requires prepared root evidence")
    if root.get("status") != "open":
        raise ValueError("native dispatch requires prepared and opened fresh root evidence")
    # Root-session settings govern every native delivery dispatch; ``tasks``
    # contributes only the exact outstanding-set binding, never eligibility.
    if not isinstance(observation_authority, bytes) or not observation_authority:
        raise ValueError("native dispatch requires authenticated root observation authority")
    expected_authority = hashlib.sha256(observation_authority).hexdigest()
    if root.get("observation_authority_fingerprint") != expected_authority:
        raise ValueError("native dispatch root observation authority is foreign")
    task_ids = sorted(str(task.get("id") or "") for task in tasks)
    outstanding = hashlib.sha256(
        json.dumps(
            {"stage": stage, "tasks": task_ids}, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    preserved = hashlib.sha256(
        json.dumps(
            {
                "run_id": state.get("run_id"),
                "baseline": state.get("baseline"),
                "settings_digest": state.get("settings_digest"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    with mutate(ws) as locked:
        if locked is None or locked.get("root_hygiene") != root:
            raise ValueError("native dispatch root evidence changed before admission")
        ledger = locked.get("dispatch_telemetry")
        if not isinstance(ledger, Mapping):
            raise ValueError("native dispatch root admission ledger is unavailable")
        dispatch_id = str(dispatch.get("dispatch_id") or "").strip()
        if not dispatch_id:
            raise ValueError("native dispatch admission requires an exact intent id")
        usage = source_fingerprint = None
        if stage == "plan":
            prior = next(
                (
                    row
                    for row in ledger.get("bindings", [])
                    if row.get("dispatch_id") == dispatch_id
                ),
                None,
            )
            if prior is not None:
                if any(
                    prior.get(key) != dispatch.get(key)
                    for key in (
                        "dispatch_id",
                        "thread_id",
                        "thread_type",
                        "task_id",
                        "dependencies",
                        "shared_owner",
                        "correction_count",
                    )
                ):
                    raise ValueError("Plan dispatch admission identity changed")
                dispatch = prior
                usage = prior.get("usage")
                source_fingerprint = prior.get("usage_source_fingerprint")
        decision = dispatch_telemetry.screen_dispatch(
            ledger,
            SystemClock(),
            current_stage=stage,
            outstanding_set_fingerprint=outstanding,
            preserved_context_fingerprint=preserved,
            observation_authority=observation_authority,
            admission_operation_id=dispatch_id,
            dispatch=dispatch,
            usage=usage,
            source_fingerprint=source_fingerprint,
            resource_limits_advisory=run_context.resource_limits_advisory(ws),
        )
        if not decision.get("dispatch_allowed"):
            locked["root_hygiene"] = {
                **dict(root),
                "status": "admissions_closed",
                "admission_refusal": decision.get("fingerprint"),
            }
            reason = (decision.get("checkpoint") or {}).get(
                "reason_in_user_language"
            ) or decision.get("status")
            raise ValueError("native dispatch refused by root meter admission: " + str(reason))
        return decision


def _native_delivery_dispatch_binding(
    state: Mapping[str, object],
    *,
    stage: str,
    task: Mapping[str, object],
    intent_id: str,
    native_task_name: str,
) -> dict:
    """Build the exact binding later consumed by the host start observation."""
    return {
        "dispatch_id": str(intent_id),
        "thread_id": str(native_task_name),
        "thread_type": {"evaluate": "evaluator", "em": "guardian"}.get(stage, "worker"),
        "task_id": str(task.get("id") or stage),
        "dependencies": [str(value) for value in task.get("deps") or []],
        "shared_owner": None,
        "started_at": 0,
        "ended_at": 0,
        "wait_duration_seconds": 0,
        "correction_count": int(task.get("fix_cycles") or 0),
        "events": [],
    }


def _failed_build_classification(
    ws: str, state: Mapping, task: Mapping, *, evaluator_attempt_id: str
) -> dict | None:
    """Project detected red for independent classification, never acceptance."""
    if not (state.get("_build_failed") or task.get("_build_failed")):
        return None
    if (
        not (state.get("_build_failed") is True or task.get("_build_failed") is True)
        or state.get("step") != "evaluate"
        or not evaluator_attempt_id
        or not state.get("run_id")
        or (_current_task(dict(state)) or {}).get("id") != task.get("id")
    ):
        raise ValueError("failed Build classification lacks its exact Evaluate run/attempt")
    detection = task.get("failure_routing")
    if not isinstance(detection, Mapping):
        raise ValueError("failed Build classification requires retained detection evidence")
    records = failure_routing.validate_failure_records(detection.get("records") or [])
    expected = failure_routing.route_failure_records(records)
    expected["fingerprint"] = hashlib.sha256(
        json.dumps(
            expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    if detection != expected or len(records) != 1:
        raise ValueError("failed Build detection inventory changed")
    record = records[0]
    evidence = record["evidence"]
    historical_id = str(record["candidate"]["id"])
    if (
        record["source"] != "taskplane.loop.gate"
        or record["class"] != "unknown"
        or record["stage"] not in {"execute", "fix"}
        or evidence.get("stage") != record["stage"]
        or evidence.get("task") != task.get("id")
        or evidence.get("submission_outcome") != "fail"
        or not re.fullmatch(re.escape(str(task.get("id"))) + r"@[0-9a-f]{40}", historical_id)
        or record["candidate"]["fingerprint"] != hashlib.sha256(historical_id.encode()).hexdigest()
        or not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("submission_fingerprint") or ""))
    ):
        raise ValueError("failed Build detection is missing, foreign or not an owned red")
    submission = evidence.get("submission")
    if submission is not None and (
        not isinstance(submission, Mapping)
        or submission.get("task") != task["id"]
        or submission.get("step") != record["stage"]
        or submission.get("outcome") != "fail"
        or submission.get("fingerprint") != evidence["submission_fingerprint"]
    ):
        raise ValueError("retained failed Build submission conflicts with detection")
    return {
        "mode": "failure-classification-only",
        "run_id": state["run_id"],
        "task_id": task["id"],
        "evaluator_attempt_id": evaluator_attempt_id,
        "candidate": _failure_candidate_identity(ws, task),
        "detected_failure": _copy_json(detection),
        "full_submission_status": "retained" if submission is not None else "unavailable",
        "failed_submission": _copy_json(submission) if submission is not None else None,
        "acceptance_allowed": False,
        "instruction": "Independently classify the retained failed Build evidence against the current candidate. "
        "Retain its historical candidate and submission identity; do not relabel historical evidence as current. "
        "A workspace fingerprint is not the missing full submission or a unique attempt receipt. When the "
        "full submission is unavailable, disclose that limit and collect bounded current independent "
        "evidence; do not reconstruct historical bytes or infer product ownership from the detection alone. "
        "Produce a complete candidate-bound failure inventory through the normal evaluator output and native "
        "observation path. PASS and unavailable cannot erase this detected failure. Only product-only "
        "classification can open Fix; other classes retain their owned recovery or hold. Do not create "
        "acceptance children, Plan selectors or edges, or rerun a broad acceptance suite for this classification.",
    }


def _task_evidence_state(state, task_id):
    if not state.get("parallel") or state.get("step") not in {"execute", "evaluate", "fix"}:
        return state
    matches = [task for task in state.get("tasks") or [] if task.get("id") == task_id]
    if len(matches) != 1:
        raise ValueError("evidence requires its exact task")
    return matches[0]


def _prepare_public_evaluate_evidence(
    ws: str,
    act_ws: str,
    state: Mapping[str, object],
    task: Mapping[str, object],
    *,
    evaluator_attempt_id: str,
) -> dict:
    """Derive current impact and start the exact two evaluator children."""
    artifact_root = _run_artifact_root(ws, state)
    manifest = run_artifacts.load_manifest(artifact_root)
    owner = manifest["binding"]
    candidate = owner["candidate"]
    freshness, _ = _phase_bridge_freshness(act_ws, list(task.get("scope") or []))
    source_tree = freshness["source_tree"]
    candidate_sha = freshness["candidate_sha"]
    if not candidate_sha or not source_tree:
        raise ValueError("Evaluate evidence owner lacks candidate revision/source tree")
    changed = [
        path
        for path in _diff_files(act_ws, _review_baseline(ws, state, "evaluate") or "HEAD")
        if not path.startswith(lens_router.LOOP_OWNED)
        and tp.match_any(path, task.get("scope") or [])
    ]
    implementation_files = sorted(
        path for path in changed if not path.startswith("taskplane/tests/") and path.endswith(".py")
    )
    tokens = shlex.split(str(task.get("tests") or ""))
    selectors = sorted(
        {
            token
            for token in tokens
            if re.fullmatch(
                r"[^\s:]+\.py::[A-Za-z_][A-Za-z0-9_]*"
                r"(?:::[A-Za-z_][A-Za-z0-9_]*)*",
                token,
            )
        }
    )
    test_files = sorted({selector.split("::", 1)[0] for selector in selectors})
    if not implementation_files or not test_files or not selectors:
        raise ValueError(
            "Evaluate impact requires changed implementation files and exact selectors"
        )
    authority = task.get("test_strategy_authority_receipt")
    if (
        not isinstance(authority, Mapping)
        or authority.get("fingerprint")
        != hashlib.sha256(
            tp.canonical_json_bytes(
                {key: value for key, value in authority.items() if key != "fingerprint"}
            )
        ).hexdigest()
    ):
        raise ValueError("Evaluate requires the sealed Plan test-strategy selection")
    selection = authority["selection"]
    if not set(selection["selectors"]) <= set(selectors):
        raise ValueError("Evaluate command differs from its approved selectors")
    producer_consumer_edges, changed_interfaces = [], []
    for producer in selection["producers"]:
        if producer["path"] not in implementation_files:
            continue
        for severed in producer["severed_edges"]:
            consumer = severed["consumer"]
            for selector in selection["selectors"]:
                if selector.split("::", 1)[0] == consumer:
                    producer_consumer_edges.append(
                        {
                            "producer": producer["path"],
                            "consumer": consumer,
                            "selector": selector,
                            "freshness_inputs": producer["freshness_inputs"],
                            "severed_edge": {key: severed[key] for key in ("mutation", "selector")},
                        }
                    )
        for fixture in producer["interface_fixtures"]:
            changed_interfaces.append(
                {
                    "producer": producer["path"],
                    "kind": producer["interface_kind"],
                    "slice": producer["slice"],
                    "fixture": copy.deepcopy(fixture),
                }
            )
    if {row["producer"] for row in producer_consumer_edges} != set(implementation_files):
        raise ValueError("Evaluate selection does not cover every changed producer")
    contract_id = str((task.get("criteria") or ["current-contract"])[0])
    classified_failures = task.get("classified_failures", [])
    if not isinstance(classified_failures, list):
        raise ValueError("Evaluate impact requires classified Fix failures")
    impact_manifest = {
        "schema": "taskplane.evaluate-impact-manifest/v1",
        "implementation_files": implementation_files,
        "test_files": test_files,
        "tests": [{"selector": selector, "contract": contract_id} for selector in selectors],
        "producer_consumer_edges": producer_consumer_edges,
        "changed_interfaces": copy.deepcopy(changed_interfaces),
        "failures": copy.deepcopy(classified_failures),
        "rejected_evidence_kinds": ["ceremonial", "source", "ast", "prose-shape", "byte-only"],
    }
    design_fp = str(state.get("design_fingerprint") or candidate.get("fingerprint") or "")
    plan_fp = str(
        state.get("plan_fingerprint")
        or hashlib.sha256(
            json.dumps(
                state.get("tasks") or [], sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
        ).hexdigest()
    )
    binding = {
        "task_id": str(task.get("id") or ""),
        "requirement_id": str(task.get("req") or state.get("requirement_id") or ""),
        "candidate_sha": candidate_sha,
        "source_tree": source_tree,
        "design_fingerprint": design_fp,
        "plan_fingerprint": plan_fp,
        "settings_digest": str(owner.get("settings_digest") or ""),
        "evaluator_attempt_id": str(evaluator_attempt_id),
    }
    assignments = start_evaluate_evidence_children(
        workspace=act_ws,
        artifact_root=artifact_root,
        binding=binding,
        impact_manifest=impact_manifest,
    )
    exact_binding = assignments[0]["binding"]
    if any(row["binding"] != exact_binding for row in assignments):
        raise ValueError("Evaluate child bindings are ambiguous")
    record = {
        "schema": "taskplane.evaluate-evidence-route/v1",
        "run_id": str(owner["run_id"]),
        "workspace": str(act_ws),
        "artifact_root": str(artifact_root),
        "evaluator_attempt_id": str(evaluator_attempt_id),
        "binding": exact_binding,
        "assignments": assignments,
    }
    with mutate(ws) as locked:
        if (
            locked is None
            or stage_loop.task_phase_state(sys.modules[__name__], ws, locked, task["id"])["step"]
            != "evaluate"
        ):
            raise ValueError("Evaluate advanced before child start committed")
        owner = _task_evidence_state(locked, task["id"])
        prior = owner.get("evaluate_child_evidence")
        if prior is not None and prior != record:
            raise ValueError("Evaluate child route conflicts with active attempt")
        owner["evaluate_child_evidence"] = record
    return record


def _dispatch_public_evaluate_evidence_children(
    ws: str,
    state: Mapping[str, object],
    task: Mapping[str, object],
    route: Mapping[str, object],
    *,
    observation_authority: bytes | None,
    model_tier: str,
) -> dict:
    """Emit and admit the two real native non-lens child dispatches."""
    from taskplane import review_evidence

    rows = []
    child_ws = str(route["workspace"])
    context = _phase_bridge_context(
        child_ws, stage_loop.task_phase_state(sys.modules[__name__], ws, state, task["id"])
    )
    artifacts = context["artifacts"]
    role = context["definition"]["role"]
    wait_policy = event_wait_policy("evaluate-evidence", 2)
    for assignment in route.get("assignments") or []:
        kind = str(assignment.get("producer_kind") or "")
        dispatch = tp.dispatch_fields("step", role, str(task.get("id") or "evaluate"), model_tier)
        dispatch["task_name"] = (
            str(dispatch["task_name"])
            + "_"
            + kind.replace("-", "_")
            + "_"
            + str(route["evaluator_attempt_id"])[-12:]
        )[:96]
        intent = _native_dispatch_intent(
            ws,
            state,
            step="evaluate",
            task_id=str(task.get("id") or "evaluate"),
            dispatch=dispatch,
            wait_policy=wait_policy,
            wave_id="evaluate-evidence",
        )
        intent_id = str(intent.get("intent_id") or "")
        if not intent_id:
            raise ValueError("Evaluate evidence child intent has no identity")
        admission = _screen_public_native_route(
            ws,
            state,
            stage="evaluate",
            tasks=[task],
            observation_authority=observation_authority,
            dispatch=_native_delivery_dispatch_binding(
                state,
                stage="evaluate",
                task=task,
                intent_id=intent_id,
                native_task_name=str(dispatch["task_name"]),
            ),
        )
        contract = tp.prepare_worker_contract(
            child_ws,
            tp.build_contract(
                f"EVALUATE EVIDENCE: {kind}", read_only=True, tools=["Read", "Grep", "Glob", "Bash"]
            ),
            stage="evaluate-evidence",
            task=str(task["id"]),
            task_name=dispatch["task_name"],
            role_marker=dispatch["role_marker"],
        )
        contract["worker_lifecycle"]["dispatch_intent_id"] = intent_id
        contract["worker_lifecycle"]["dispatch_intent_run_id"] = intent["identity"]["run_id"]
        inputs = {
            "schema": "taskplane.evaluate-child-input/v1",
            "run_id": context["run_id"],
            "stage_id": context["stage"]["stage_id"],
            "authority_fingerprint": context["stage"]["authority"]["authority_fingerprint"],
            "assignment": copy.deepcopy(assignment),
            "instruction": "Execute the exact read-only evidence assignment and return its JSON result. "
            "Use governed command receipts. Do not verdict, gate, dispatch or repair.",
        }
        contract["evidence_input"] = review_evidence.portable_artifact_reference(
            artifacts, artifacts.put("evaluate-child-input", inputs)
        )
        tp.activate(
            child_ws,
            contract,
            snapshot=tp.git_head(child_ws),
            task_slot_override=contract["task_slot"],
        )
        tp.record_expected_dispatch(
            ws,
            "step",
            role,
            dispatch["model_tier"],
            dispatch["model"],
            ref=str(task.get("id") or "evaluate"),
            task_name=dispatch["task_name"],
            reasoning_effort=dispatch["reasoning_effort"],
            role_marker_value=dispatch["role_marker"],
            intent_id=intent_id,
            intent_run_id=(intent.get("identity") or {}).get("run_id"),
        )
        rows.append(
            {
                **dispatch,
                "assignment": copy.deepcopy(assignment),
                "evidence_input": contract["evidence_input"],
                "contract": contract,
                "contract_bootstrap": {
                    "schema": "taskplane.worker-contract-bootstrap/v1",
                    "task_slot": contract["task_slot"],
                    "worker_identity": dispatch["task_name"],
                    "environment": {"TASKPLANE_TASK": contract["task_slot"], "PWD": child_ws},
                    "activation": "pending_subagent_start_binding",
                    "control_plane_release": {
                        "command": "worker-release",
                        "signed_action": tp.encode_worker_release_action(
                            contract["worker_lifecycle"]["release_action"]
                        ),
                        "terminal_receipt_required": True,
                    },
                },
                "dispatch_intent": intent,
                "root_admission": admission,
                "prompt": "Read-only evidence producer. Execute the exact "
                "assignment obligations and return only the required "
                "JSON result. Do not verdict, gate, dispatch, mutate, "
                "classify delivery, or repair.",
            }
        )
    if len(rows) != 2 or {row["assignment"]["producer_kind"] for row in rows} != {
        "language-code-quality",
        "test-design",
    }:
        raise ValueError("Evaluate requires exactly two evidence child dispatches")
    updated = {
        **dict(route),
        "child_dispatches": rows,
        "wait_invocation": event_wait_invocation(wait_policy, [row["task_name"] for row in rows]),
    }
    with mutate(ws) as locked:
        if locked is None:
            raise ValueError("Evaluate run disappeared")
        owner = _task_evidence_state(locked, task["id"])
        if owner.get("evaluate_child_evidence") != route:
            raise ValueError("Evaluate evidence route changed before dispatch")
        owner["evaluate_child_evidence"] = copy.deepcopy(updated)
    return updated


def observed_evaluate_evidence_child(state: Mapping[str, object], native_task_name: str):
    """Resolve a terminal event to one exact task-owned evidence route."""
    owners = state.get("tasks", []) if state.get("parallel") else [state]
    matches = [
        (route, row)
        for owner in owners
        if isinstance((route := owner.get("evaluate_child_evidence")), Mapping)
        for row in route.get("child_dispatches") or []
        if row.get("task_name") == native_task_name
    ]
    if not matches:
        return None, None
    if len(matches) != 1:
        raise ValueError("Evaluate evidence child has ambiguous task ownership")
    return matches[0]


def complete_observed_evaluate_evidence_child(ws: str, event: Mapping[str, object]) -> dict | None:
    """Consume one native child terminal JSON into its durable lifecycle."""
    route, child = observed_evaluate_evidence_child(
        load(ws) or {}, str(event.get("task_name") or event.get("agent_type") or "")
    )
    if child is None:
        return None
    raw = event.get("last_assistant_message")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Evaluate evidence child returned no JSON result")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Evaluate evidence child result is not exact JSON") from exc
    return complete_evaluate_evidence_child(
        workspace=str(route["workspace"]),
        artifact_root=str(route["artifact_root"]),
        run_id=str(route["run_id"]),
        assignment=child["assignment"],
        result=result,
        work_units=1,
    )


def _dispatch_binding_for_attempt(
    ledger: Mapping[str, object],
    task_id: str,
    native_task_name: str | None = None,
    dispatch_id: str | None = None,
) -> dict | None:
    matches = [
        dict(row)
        for row in ledger.get("bindings") or []
        if row.get("task_id") == task_id
        and (dispatch_id is None or row.get("dispatch_id") == dispatch_id)
        and (native_task_name is None or row.get("thread_id") == native_task_name)
    ]
    if len(matches) > 1:
        raise dispatch_telemetry.DispatchTelemetryError(
            "native dispatch attempt binding is ambiguous"
        )
    return matches[0] if matches else None


def _invalidate_terminal_metrics(state: dict) -> None:
    """A later authenticated observation supersedes cached terminal absence."""
    for field in (
        "wave_metrics_evidence",
        "wave_metrics_receipt",
        "wave_metrics_unavailable",
        "wave_metrics_ledger",
        "terminal_metrics",
    ):
        state.pop(field, None)


def record_native_dispatch_observation(
    ws: str,
    *,
    expected: Mapping[str, object],
    native_task_name: str,
    observed_at: float | None = None,
) -> dict:
    """Bind one actual Codex spawn to its emitted native intent."""
    intent_id = str(expected.get("intent_id") or "").strip()
    dispatch_ref = str(expected.get("ref") or "").strip()
    if not intent_id or not dispatch_ref:
        return {"status": "unavailable", "reason": "native dispatch intent identity is unavailable"}
    _ensure_dispatch_telemetry(ws)
    with mutate(ws) as locked:
        if locked is None:
            raise dispatch_telemetry.DispatchTelemetryError("no active loop")
        ledger = locked.get("dispatch_telemetry")
        dispatch_telemetry.validate_ledger(ledger)
        if str(expected.get("intent_run_id") or "") != str(locked.get("run_id") or ""):
            raise dispatch_telemetry.DispatchTelemetryError(
                "native dispatch intent belongs to another governed run"
            )
        if expected.get("kind") == "step":
            task_view = stage_loop.task_phase_state(
                sys.modules[__name__],
                ws,
                locked,
                dispatch_ref
                if any(row.get("id") == dispatch_ref for row in locked.get("tasks") or [])
                else None,
            )
            active_step = str(task_view.get("step") or "")
            phase = _phase_bridge_context(ws, task_view)
            role = phase["definition"]["role"] if phase is not None else STEP_ROLE.get(active_step)
            if active_step not in STEP_ROLE or expected.get("agent") != role:
                raise dispatch_telemetry.DispatchTelemetryError(
                    "native stage dispatch does not match the active loop step"
                )
            task = next(
                (
                    row
                    for row in locked.get("tasks") or []
                    if str(row.get("id") or "") == dispatch_ref
                ),
                None,
            )
            if task is None and dispatch_ref != active_step:
                raise dispatch_telemetry.DispatchTelemetryError(
                    "native stage dispatch task is absent from the active loop"
                )
            task_id = dispatch_ref
            dependencies = (
                [str(value) for value in task.get("deps") or []]
                if isinstance(task, Mapping)
                else []
            )
            correction_count = int(task.get("fix_cycles") or 0) if isinstance(task, Mapping) else 0
            thread_type = {
                "evaluate": "evaluator",
                "em": "guardian",
            }.get(active_step, "worker")
        else:
            task_id = dispatch_ref
            task = next(
                (row for row in locked.get("tasks") or [] if str(row.get("id") or "") == task_id),
                None,
            )
            if not isinstance(task, Mapping):
                raise dispatch_telemetry.DispatchTelemetryError(
                    "native dispatch task is absent from the active Plan"
                )
            dependencies = [str(value) for value in task.get("deps") or []]
            correction_count = int(task.get("fix_cycles") or 0)
            thread_type = "worker"
        existing = next(
            (row for row in ledger.get("bindings") or [] if row.get("dispatch_id") == intent_id),
            None,
        )
        observed_at = (existing or {}).get("started_at") or (
            SystemClock().wall_time() if observed_at is None else observed_at
        )
        binding = dispatch_telemetry.bind_dispatch(
            ledger,
            {
                "dispatch_id": intent_id,
                "thread_id": str(native_task_name or intent_id),
                "thread_type": thread_type,
                "task_id": task_id,
                "dependencies": dependencies,
                "shared_owner": None,
                "started_at": observed_at,
                "ended_at": ((existing or {}).get("ended_at") or observed_at),
                "wait_duration_seconds": 0,
                "correction_count": correction_count,
                "events": list((existing or {}).get("events") or []),
            },
        )
        stored = next(
            row for row in ledger.get("bindings") or [] if row.get("dispatch_id") == intent_id
        )
        if (
            stored.get("started_at") == 0
            and stored.get("ended_at") == 0
            and not stored.get("events")
        ):
            stored["events"] = [
                dispatch_telemetry.dispatch_event(
                    dispatch_id=intent_id,
                    thread_id=str(native_task_name),
                    thread_type=thread_type,
                    task_id=task_id,
                    sequence=1,
                    kind="progress",
                    at=observed_at,
                    payload={"phase": "native-start"},
                )
            ]
            ledger["revision"] = int(ledger["revision"]) + 1
            dispatch_telemetry.validate_ledger(ledger)
            binding = dict(stored)
        evidence_route = _task_evidence_state(locked, task_id).get("evaluate_child_evidence")
        if isinstance(evidence_route, Mapping):
            child = next(
                (
                    row
                    for row in evidence_route.get("child_dispatches") or []
                    if row.get("task_name") == native_task_name
                    and (row.get("dispatch_intent") or {}).get("intent_id") == intent_id
                ),
                None,
            )
            if isinstance(child, Mapping):
                observe_evaluate_evidence_child_start(
                    artifact_root=str(evidence_route["artifact_root"]),
                    assignment=child["assignment"],
                    dispatch_id=intent_id,
                    native_task_name=native_task_name,
                )
        return binding


def record_observed_dispatch_usage(
    ws: str,
    *,
    task_id: str,
    normalized_usage: Mapping[str, object],
    source: str | None = None,
    source_fingerprint: str | None = None,
    native_task_name: str | None = None,
    dispatch_id: str | None = None,
) -> dict:
    """Production hook adapter: persist observed cumulative provider usage."""
    usage = spend.dispatch_usage(dict(normalized_usage))
    observed_source = str(source_fingerprint or "").strip()
    if not observed_source:
        observed_source = hashlib.sha256(
            os.path.realpath(str(source or "")).encode("utf-8")
        ).hexdigest()
    with mutate(ws) as locked:
        if locked is None:
            raise dispatch_telemetry.DispatchTelemetryError("no active loop")
        ledger = locked.get("dispatch_telemetry")
        dispatch_telemetry.validate_ledger(ledger)
        binding = _dispatch_binding_for_attempt(ledger, str(task_id), native_task_name, dispatch_id)
        if binding is None:
            raise dispatch_telemetry.DispatchTelemetryError(
                "observed usage has no task dispatch binding"
            )
        result = dispatch_telemetry.observe_usage(
            ledger,
            dispatch_id=str(binding["dispatch_id"]),
            usage=usage,
            source_fingerprint=observed_source,
        )
        _invalidate_terminal_metrics(locked)
        return result


def record_native_session_snapshot(
    ws: str, *, task_id: str, dispatch_id: str, snapshot: Mapping[str, object]
) -> dict:
    """Persist native lineage and return the non-duplicated attempt delta."""
    checked = native_session_meter.validate_snapshot(snapshot)
    with mutate(ws) as locked:
        if locked is None:
            raise dispatch_telemetry.DispatchTelemetryError("no active loop")
        dispatch_ledger = locked.get("dispatch_telemetry")
        dispatch_telemetry.validate_ledger(dispatch_ledger)
        binding = _dispatch_binding_for_attempt(
            dispatch_ledger, str(task_id), None, str(dispatch_id)
        )
        if binding is None:
            raise dispatch_telemetry.DispatchTelemetryError(
                "native session snapshot has no dispatch binding"
            )
        ledger = locked.setdefault(
            "native_session_telemetry",
            {
                "schema": "taskplane.native-session-ledger/v1",
                "records": [],
            },
        )
        if (
            not isinstance(ledger, dict)
            or ledger.get("schema") != "taskplane.native-session-ledger/v1"
            or not isinstance(ledger.get("records"), list)
        ):
            raise dispatch_telemetry.DispatchTelemetryError("native session ledger is invalid")
        for record in ledger["records"]:
            if not isinstance(record, Mapping):
                raise dispatch_telemetry.DispatchTelemetryError(
                    "native session ledger record is invalid"
                )
            if record.get("snapshot_fingerprint") == checked["fingerprint"]:
                if record.get("dispatch_id") != dispatch_id:
                    raise dispatch_telemetry.DispatchTelemetryError(
                        "native session snapshot is bound to another dispatch"
                    )
                return copy.deepcopy(dict(record))
        prior = []
        for record in ledger["records"]:
            if not isinstance(record, Mapping):
                continue
            prior_snapshot = record.get("snapshot")
            if (
                isinstance(prior_snapshot, Mapping)
                and prior_snapshot.get("source_identity_fingerprint")
                == checked["source_identity_fingerprint"]
            ):
                prior.append(record)
        previous_usage = {key: 0 for key in checked["usage"]}
        if prior:
            prior_snapshot = prior[-1].get("snapshot")
            prior_usage = (
                prior_snapshot.get("usage") if isinstance(prior_snapshot, Mapping) else None
            )
            if not isinstance(prior_usage, Mapping):
                raise dispatch_telemetry.DispatchTelemetryError(
                    "native session ledger record is invalid"
                )
            previous_usage = dict(prior_usage)
        attributed = {
            key: int(checked["usage"][key]) - int(previous_usage[key]) for key in checked["usage"]
        }
        if any(value < 0 for value in attributed.values()):
            raise dispatch_telemetry.DispatchTelemetryError(
                "native physical-segment counter moved backwards"
            )
        record = {
            "dispatch_id": str(dispatch_id),
            "task_id": str(task_id),
            "session_id": checked["session_id"],
            "snapshot_fingerprint": checked["fingerprint"],
            "snapshot": checked,
            "attributed_usage": attributed,
        }
        ledger["records"].append(record)
        record["dispatch_usage"] = {
            key: sum(
                int(row["attributed_usage"][key])
                for row in ledger["records"]
                if row["dispatch_id"] == dispatch_id
            )
            for key in attributed
        }
        ledger["aggregate"] = native_session_meter.aggregate(
            [row["snapshot"] for row in ledger["records"]]
        )
        material = {key: value for key, value in ledger.items() if key != "fingerprint"}
        ledger["fingerprint"] = hashlib.sha256(
            json.dumps(
                material, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
            ).encode("utf-8")
        ).hexdigest()
        _invalidate_terminal_metrics(locked)
        return copy.deepcopy(record)


def record_native_orchestrator_snapshot(
    ws: str, *, snapshot: Mapping[str, object], observation_authority: bytes | None = None
) -> dict:
    """Bind a native root/resume segment to the wave's measured main work."""
    checked = native_session_meter.validate_snapshot(snapshot)
    _ensure_dispatch_telemetry(ws)
    dispatch_id = "native-main-" + str(checked["source_identity_fingerprint"])[:32]
    clock = SystemClock()
    with mutate(ws) as locked:
        if locked is None:
            raise dispatch_telemetry.DispatchTelemetryError("no active loop")
        ledger = locked.get("dispatch_telemetry")
        dispatch_telemetry.validate_ledger(ledger)
        existing = next(
            (row for row in ledger.get("bindings") or [] if row.get("dispatch_id") == dispatch_id),
            None,
        )
        if existing is None:
            binding = {
                "dispatch_id": dispatch_id,
                "thread_id": str(checked["session_id"]),
                "thread_type": "main",
                "task_id": "orchestrator",
                "dependencies": [],
                "shared_owner": None,
                "started_at": clock.wall_time(),
                "ended_at": clock.wall_time(),
                "wait_duration_seconds": 0,
                "correction_count": 0,
                "events": [],
            }
            if ledger.get("root_admission") is None:
                dispatch_telemetry.bind_dispatch(ledger, binding)
            else:
                stage = str(locked.get("step") or "execute")
                task_ids = sorted(str(task.get("id") or "") for task in locked.get("tasks") or [])
                outstanding = hashlib.sha256(
                    json.dumps(
                        {"stage": stage, "tasks": task_ids}, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
                preserved = hashlib.sha256(
                    json.dumps(
                        {
                            "run_id": locked.get("run_id"),
                            "baseline": locked.get("baseline"),
                            "settings_digest": locked.get("settings_digest"),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                dispatch_telemetry.screen_dispatch(
                    ledger,
                    clock,
                    current_stage=stage,
                    outstanding_set_fingerprint=outstanding,
                    preserved_context_fingerprint=preserved,
                    observation_authority=observation_authority,
                    admission_operation_id=dispatch_id,
                    dispatch=binding,
                    resource_limits_advisory=run_context.resource_limits_advisory(ws),
                )
    record = record_native_session_snapshot(
        ws, task_id="orchestrator", dispatch_id=dispatch_id, snapshot=checked
    )
    usage = dict(record["dispatch_usage"])
    normalized = {
        "schema": "taskplane.token-usage/v2",
        "available": True,
        "provider": "codex",
        "reason": None,
        **usage,
        "cache_creation_tokens": 0,
        "raw_total_tokens": usage["total_tokens"],
        "effective_tokens": int(
            usage["uncached_input_tokens"] * spend.WEIGHTS["input"]
            + usage["cached_input_tokens"] * spend.WEIGHTS["cache_read"]
            + usage["output_tokens"] * spend.WEIGHTS["output"]
        ),
    }
    observed = record_observed_dispatch_usage(
        ws,
        task_id="orchestrator",
        normalized_usage=normalized,
        source_fingerprint=str(checked["source_identity_fingerprint"]),
        dispatch_id=dispatch_id,
    )
    return {"dispatch": observed, "native_session": record}


def finalize_observed_dispatch_usage(
    ws: str,
    *,
    task_id: str,
    ended_at: float | None = None,
    outcome: str = "complete",
    native_task_name: str | None = None,
    usage_unavailable: bool = False,
    unavailable_reason: str | None = None,
    dispatch_id: str | None = None,
    phase_runtime: bool = False,
) -> dict:
    """Finalize one hook-observed dispatch into the binding budget ledger."""
    terminal_kind = {
        "success": "complete",
        "complete": "complete",
        "failure": "failed",
        "failed": "failed",
        "cancellation": "cancelled",
        "cancelled": "cancelled",
        "interruption": "interrupted",
        "interrupted": "interrupted",
        "handoff": "handoff",
    }.get(str(outcome or "").strip().lower())
    if terminal_kind is None:
        raise dispatch_telemetry.DispatchTelemetryError("dispatch terminal outcome is invalid")
    clock = SystemClock()
    with mutate(ws) as locked:
        if locked is None:
            raise dispatch_telemetry.DispatchTelemetryError("no active loop")
        ledger = locked.get("dispatch_telemetry")
        dispatch_telemetry.validate_ledger(ledger)
        binding = _dispatch_binding_for_attempt(ledger, str(task_id), native_task_name, dispatch_id)
        if binding is None:
            return {"status": "unavailable", "reason": "task dispatch binding is unavailable"}
        if binding.get("usage") is None:
            if not usage_unavailable:
                return {
                    "status": "unavailable",
                    "reason": "provider usage observation is unavailable",
                }
            result = dispatch_telemetry.terminalize_unavailable(
                ledger,
                dispatch_id=str(binding["dispatch_id"]),
                ended_at=float(ended_at if ended_at is not None else clock.wall_time()),
                outcome=terminal_kind,
                reason=str(unavailable_reason or "provider usage observation is unavailable"),
            )
        else:
            ended = float(ended_at if ended_at is not None else clock.wall_time())
            events = [{"kind": terminal_kind, "sequence": 1}]
            if phase_runtime:
                stored = next(
                    row
                    for row in ledger["bindings"]
                    if row["dispatch_id"] == binding["dispatch_id"]
                )
                if not stored["finalized_receipt_fingerprint"]:
                    stored["ended_at"] = ended
                    stored["events"] = [
                        *stored["events"],
                        dispatch_telemetry.dispatch_event(
                            dispatch_id=stored["dispatch_id"],
                            thread_id=stored["thread_id"],
                            thread_type=stored["thread_type"],
                            task_id=stored["task_id"],
                            kind=terminal_kind,
                            sequence=len(stored["events"]) + 1,
                            at=ended,
                        ),
                    ]
                    # The host's authenticated usage/source remain identical;
                    # the incumbent integrity producer binds the newly observed
                    # terminal timing and event along with those original facts.
                    stored["usage_integrity_fingerprint"] = (
                        dispatch_telemetry._usage_integrity_fingerprint(
                            ledger, stored, stored["usage"], stored["usage_source_fingerprint"]
                        )
                    )
                events = stored["events"]
            result = dispatch_telemetry.finalize_usage(
                ledger,
                dispatch_id=str(binding["dispatch_id"]),
                ended_at=ended,
                clock=clock,
                events=events,
            )
        _invalidate_terminal_metrics(locked)
        return result


def _verified_stage_loop_wave_split(*args, **kwargs):
    return stage_loop._verified_stage_loop_wave_split(sys.modules[__name__], *args, **kwargs)


def _persist_stage_loop_wave_bindings(*args, **kwargs):
    return stage_loop._persist_stage_loop_wave_bindings(sys.modules[__name__], *args, **kwargs)


def _stage_loop_wave_dispatches(*args, **kwargs):
    return stage_loop._stage_loop_wave_dispatches(sys.modules[__name__], *args, **kwargs)


@run_context.operation
def wave(*args, **kwargs):
    return dispatch.wave(sys.modules[__name__], *args, **kwargs)


def claim(*args, **kwargs):
    return dispatch.claim(sys.modules[__name__], *args, **kwargs)


# --------------------------------------------------------------- next / gate


def read_pending_action(*args, **kwargs):
    return dispatch.read_pending_action(sys.modules[__name__], *args, **kwargs)


@run_context.operation
@loop_status.with_dashboard
def next_action(*args, **kwargs):
    result = dispatch.next_action(sys.modules[__name__], *args, **kwargs)
    return project_next_action_for_host(args[0] if args else kwargs["ws"], result)


guide = runtime_eval.guide_loop


def event_wait_policy(*args, **kwargs):
    return dispatch.event_wait_policy(sys.modules[__name__], *args, **kwargs)


def event_wait_invocation(*args, **kwargs):
    return dispatch.event_wait_invocation(sys.modules[__name__], *args, **kwargs)


# Design Contract validation lives in design_contract.py (v2.2.1) — thin
# delegates keep loop's internal API stable for callers and tests.
import design_contract as _dc

_read_json = _dc.read_json
DESIGN_SCHEMA = _dc.DESIGN_SCHEMA
DESIGN_CONTRACT = _dc.DESIGN_CONTRACT
DESIGN_NARRATIVE = _dc.DESIGN_NARRATIVE
_design_path = _dc.design_path
_design_contract = _dc.design_contract
_design_safe_rel = _dc.design_safe_rel
_design_evidence_paths = _dc.design_evidence_paths
_design_evidence_fingerprint = _dc.design_evidence_fingerprint


def _design_current_errors(ws, state):
    context = _phase_bridge_context(ws, state)
    if context is None:
        return (
            ["current phase Design authority is unavailable"]
            if state.get("design_required")
            else []
        )
    if context["stage"]["stage_kind"] not in {"plan", "build", "evaluate", "engineering"}:
        return _dc.design_current_errors(ws, state)
    try:
        package = phase_harness.input_package(sys.modules[__name__], context)
        design = package.read("design")
        if design["requirement"] != state["requirement_id"]:
            raise ValueError("Design belongs to another requirement")
        return []
    except (ValueError, KeyError, OSError) as exc:
        return ["approved phase Design is unavailable: " + str(exc)]


_design_dor = _dc.design_dor
_base_design_dod_errors = _dc.design_dod_errors
_design_plan_errors = _dc.design_plan_errors
_design_review_errors = _dc.design_review_errors
_design_review_notices = _dc.design_review_notices


def _design_dod_errors(ws: str, state: dict) -> list:
    """Join the Design artifact DoD with its mandatory runtime inputs."""
    from taskplane import phase_amendment

    try:
        amendment = phase_amendment.current(sys.modules[__name__], ws, state)
        if amendment is not None and amendment["phase"] == "design":
            return _base_design_dod_errors(ws, state)
    except (ValueError, OSError) as exc:
        return ["Design amendment refused: " + str(exc)]
    try:
        phase = _phase_bridge_context(ws, state)
        if phase is None or phase["stage"]["stage_kind"] != "design":
            raise ValueError("current phase is not Design")
        _phase_bridge_gate_check(ws, state)
        runtime_errors = []
    except (ValueError, OSError) as exc:
        runtime_errors = [f"Design phase evidence refused: {exc}"]
    return [
        *_base_design_dod_errors(ws, state),
        *_design_control_plane_errors(ws, state),
        *runtime_errors,
    ]


def _design_context(ws: str, state: dict) -> dict | None:
    if not state.get("design_required") or not state.get("design_fingerprint"):
        return None
    stale = _design_current_errors(ws, state)
    if stale:
        # Diagnose stale authority without handing its bytes to a worker.
        return {
            "approved": False,
            "stale": True,
            "fingerprint": state.get("design_fingerprint"),
            "contract": None,
            "errors": stale,
        }
    contract, errors = _design_contract(ws)
    return {
        "approved": not bool(errors),
        "stale": None,
        "fingerprint": state.get("design_fingerprint"),
        "contract": contract,
        "errors": errors,
    }


def _criteria_for(ws: str, state: dict, task: dict) -> list:
    del ws, state
    criteria = task.get("criteria")
    if not isinstance(criteria, list):
        return []
    return [value.strip() for value in criteria if isinstance(value, str) and value.strip()]


def _aggregate_impact_policy(tasks) -> dict:
    return depgraph.aggregate_impact_policy(tasks)


def _expanded_task_contracts(requirement: Mapping, task: Mapping) -> list:
    """The existing Plan gate's requirement-first contract expansion."""
    merged, seen = [], set()
    for contract in list(requirement.get("contracts") or []) + list(task.get("contracts") or []):
        ids = depgraph.contract_ids([contract])
        cid = ids[0] if ids else ""
        if cid and cid not in seen:
            merged.append(contract)
            seen.add(cid)
    return merged


def _plan_dor_errors(ws: str, state: dict, apply: bool = False) -> list:
    """Definition of Ready for implementation, derived from the plan.

    M3 (v2.2.1): a Ready CHECK must not mutate. With apply=False
    (default) this is pure — it inspects and reports. Only the plan
    GATE passes apply=True, which merges requirement contracts into
    tasks, records requirement/contract edges, resolves each task's
    impact policy, and stores the graph DoR verdict on the state."""
    errors = []
    try:
        _plan_delivery_mode_from_file(ws, state, apply=apply)
    except delivery_policy.DeliveryPolicyError as exc:
        errors.append("Plan delivery mode: " + str(exc))
    for task in state.get("tasks") or []:
        prefix = f"task {task.get('id', '?')}: "
        if not task.get("scope"):
            errors.append(prefix + "scope is missing")
        errors.extend(
            prefix + problem for problem in tp.plan_test_command_errors(task.get("tests"))
        )
        try:
            tp.task_test_timeout_seconds(task)
        except ValueError as exc:
            errors.append(prefix + "test timeout: " + str(exc))
        # Each task owns its criteria. Missing metadata must never expand
        # evaluation to unrelated requirement or program-wide acceptance.
        explicit_criteria = task.get("criteria")
        if not isinstance(explicit_criteria, list) or not any(
            str(criterion).strip() for criterion in explicit_criteria
        ):
            errors.append(prefix + "explicit acceptance criteria are missing or empty")
        rid = task.get("req") or state.get("requirement_id")
        rec = reqs.get_requirement(ws, rid) if rid else None
        if rec:
            # Requirements own stable product/contract dependencies; the plan
            # may add contracts but cannot silently erase the requirement's
            # boundaries with an empty or narrower task-level list.
            merged_contracts = _expanded_task_contracts(rec, task)
            if apply:
                task["contracts"] = merged_contracts
            for dep in rec.get("depends_on") or []:
                if reqs.get_requirement(ws, dep) is None:
                    errors.append(prefix + f"requirement dependency {dep} does not exist")
                elif apply:
                    # Requirements are the source of truth. Reconcile their
                    # product edges before graph Ready instead of depending on
                    # a particular CLI path having populated the derived map.
                    depgraph.link_requirement_dep(ws, rid, dep)
            if apply:
                for contract in rec.get("contracts") or []:
                    cids = depgraph.contract_ids([contract])
                    relation = (
                        contract.get("relation", "changes")
                        if isinstance(contract, dict)
                        else "changes"
                    )
                    if cids:
                        depgraph.record_edge(
                            ws, depgraph.req_node(rid), cids[0], kind=relation, confidence="high"
                        )
        if apply:
            task["impact_policy"] = depgraph.impact_policy(task)
        try:
            strategy_authority = _seal_task_test_strategy_authority(ws, state, task)
        except (OSError, ValueError, test_strategy.StrategyContractError) as exc:
            errors.append(prefix + "test-strategy authority: " + str(exc))
        else:
            if apply and strategy_authority is not None:
                task["test_strategy_authority_receipt"] = strategy_authority
        if rid and task.get("high_cost"):
            if rec is None:
                errors.append(prefix + f"requirement {rid} does not exist")
            elif rec.get("open_questions"):
                errors.append(
                    prefix
                    + "requirement has unresolved questions: "
                    + "; ".join(rec["open_questions"])
                )
    graph_dor = depgraph.readiness(ws, state.get("tasks") or [])
    if apply:
        state["graph_dor"] = graph_dor
    errors.extend("graph DoR: " + e for e in graph_dor.get("errors") or [])
    errors.extend(
        tp.requirement_coverage_errors(
            state.get("tasks") or [],
            lambda rid: reqs.get_requirement(ws, rid),
            state.get("requirement_id"),
        )
    )
    errors.extend("design DoR: " + e for e in _design_plan_errors(ws, state))
    return errors


_REANCHOR_CONTRACT_FIELDS = (
    "id",
    "scope",
    "tests",
    "req",
    "deps",
    "type",
    # Accept both the documented semantic names and their task-file names.
    # If both are present they are both bound, so aliases cannot hide drift.
    "gap",
    "gap_category",
    "contracts",
    "modules",
    "new_modules",
    "design_edges",
    "impact",
    "impact_policy",
    "criteria",
    "acceptance_refs",
    "test_contract",
    "test_strategy_authority",
)
_REANCHOR_SEQUENCE_FIELDS = frozenset(
    {
        "scope",
        "deps",
        "contracts",
        "modules",
        "new_modules",
        "design_edges",
        "criteria",
        "acceptance_refs",
    }
)
_REANCHOR_MAPPING_FIELDS = frozenset(
    {
        "impact",
        "impact_policy",
        "test_contract",
        "test_strategy_authority",
    }
)

_REANCHOR_RESOLVED_OUTAGE_REASONS = {
    "human-resolved-orchestration-outage": "orchestration_unavailable",
    "human-resolved-producer-receipt-outage": "producer_receipt_unavailable",
}


def _reanchor_contract(task: Mapping) -> dict:
    """Canonical immutable task contract, excluding all runtime fields."""
    contract = {}
    for field in _REANCHOR_CONTRACT_FIELDS:
        value = task.get(field)
        if field in _REANCHOR_SEQUENCE_FIELDS and value is None:
            value = []
        elif field in _REANCHOR_MAPPING_FIELDS and value is None:
            value = {}
        contract[field] = value
    return contract


def _reanchor_fingerprint(task: Mapping) -> str:
    return hashlib.sha256(tp.canonical_json_bytes(_reanchor_contract(task))).hexdigest()


_REANCHOR_CRITERION_PROOF_SCHEMA = "taskplane.reanchor-criterion-proof/v1"
_REANCHOR_PROOF_FIELDS = frozenset(
    {
        "schema",
        "authority_schema",
        "task_id",
        "contract_fingerprint",
        "source_revision",
        "evaluation_sha256",
        "criteria_status_sha256",
        "receipt_sha256",
        "disposition",
        "key_id",
    }
)


def _verified_criterion_evidence(value) -> bool:
    """Recognize only a post-verification engine authority projection."""
    if (
        not isinstance(value, Mapping)
        or set(value) != _REANCHOR_PROOF_FIELDS
        or value.get("schema") != _REANCHOR_CRITERION_PROOF_SCHEMA
        or value.get("authority_schema") != _REANCHOR_AUTHORITY_SCHEMA
    ):
        return False
    if not str(value.get("task_id") or "").strip() or value.get("disposition") not in {
        "independent-pass",
        *_REANCHOR_RESOLVED_OUTAGE_REASONS,
    }:
        return False
    for field in (
        "contract_fingerprint",
        "evaluation_sha256",
        "criteria_status_sha256",
        "receipt_sha256",
        "key_id",
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", str(value.get(field) or "")):
            return False
    return bool(
        re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", str(value.get("source_revision") or ""))
    )


_REANCHOR_AUTHORITY_SCHEMA = "taskplane.reanchor-pass-authority/v2"
_REANCHOR_AUTHORITY_REF_SCHEMA = "taskplane.reanchor-pass-authority-reference/v1"
_REANCHOR_ANCESTRY_TIMEOUT_SECONDS = 10


def _validated_reanchor_verdict(task: Mapping, verdict: Mapping, disposition: str) -> str:
    """Validate the complete gate verdict and digest its criterion statuses."""
    task_id = str(task.get("id") or "")
    requirement = str(task.get("req") or "")
    if (
        not isinstance(verdict, Mapping)
        or verdict.get("schema") != evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID
        or str(verdict.get("task") or "") != task_id
        or str(verdict.get("requirement") or "") != requirement
    ):
        raise ValueError("reanchor verdict identity is invalid")
    criteria = list(task.get("criteria") or [])
    rows = verdict.get("criteria")
    if not criteria or not isinstance(rows, list) or len(rows) != len(criteria):
        raise ValueError("reanchor verdict criteria are incomplete")
    normalized = []
    for index, row in enumerate(rows):
        if (
            not isinstance(row, Mapping)
            or row.get("criterion") != criteria[index]
            or row.get("status") != "met"
        ):
            raise ValueError("reanchor verdict criteria are not exactly met")
        # Criterion prose remains ordinary evaluator explanation. It is
        # validated as present but contributes no authority by itself.
        descriptive = row.get("evidence")
        if not isinstance(descriptive, str) or not descriptive.strip():
            raise ValueError("reanchor verdict criterion description is missing")
        normalized.append({"criterion": criteria[index], "status": "met"})
    failures = verdict.get("failures")
    if not isinstance(failures, list):
        raise ValueError("reanchor verdict failures are malformed")
    if disposition == "independent-pass":
        if verdict.get("verdict") != "pass" or failures:
            raise ValueError("reanchor verdict is not an independent pass")
    elif disposition in _REANCHOR_RESOLVED_OUTAGE_REASONS:
        evaluation = verdict.get("evaluation")
        if (
            verdict.get("verdict") != "fail"
            or not isinstance(evaluation, Mapping)
            or evaluation.get("status") != "unavailable"
            or evaluation.get("reason_code") != _REANCHOR_RESOLVED_OUTAGE_REASONS[disposition]
        ):
            raise ValueError("reanchor verdict is not a resolved outage")
    else:
        raise ValueError("reanchor disposition is invalid")
    return hashlib.sha256(tp.canonical_json_bytes(normalized)).hexdigest()


def _reanchor_authority_material(
    task: Mapping,
    *,
    source_revision: str,
    evaluation_sha256: str,
    criteria_status_sha256: str,
    disposition: str,
    outage_identity=None,
) -> dict:
    return {
        "schema": _REANCHOR_AUTHORITY_SCHEMA,
        "task_id": str(task.get("id") or ""),
        "contract_fingerprint": _reanchor_fingerprint(task),
        "source_revision": str(source_revision or "").lower(),
        "evaluation_sha256": str(evaluation_sha256 or "").lower(),
        "criteria_status_sha256": str(criteria_status_sha256 or "").lower(),
        "disposition": str(disposition or ""),
        "outage_identity": (
            outage_identity if disposition in _REANCHOR_RESOLVED_OUTAGE_REASONS else None
        ),
    }


def _persist_reanchor_authority(
    workspace: str, task: Mapping, disposition: str
) -> tuple[dict, str]:
    """Persist one signed receipt only after an authoritative pass gate."""
    # The gate binds the checkout it actually judged.  Never inherit a
    # caller-authored/copyable target_commit field as signing authority.
    source_revision = str(tp.git_head(workspace) or "").lower()
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", source_revision):
        raise ValueError("reanchor authority source revision is invalid")
    verdict_path = runtime_storage.evaluation_path(workspace)
    with open(verdict_path, "rb") as stream:
        verdict_bytes = stream.read()
    try:
        verdict = json.loads(verdict_bytes)
    except (TypeError, ValueError) as exc:
        raise ValueError("reanchor verdict JSON is invalid") from exc
    evaluation_sha256 = hashlib.sha256(verdict_bytes).hexdigest()
    criteria_status_sha256 = _validated_reanchor_verdict(task, verdict, disposition)
    warning = task.get("evaluation") if isinstance(task.get("evaluation"), Mapping) else {}
    material = _reanchor_authority_material(
        task,
        source_revision=source_revision,
        evaluation_sha256=evaluation_sha256,
        criteria_status_sha256=criteria_status_sha256,
        disposition=disposition,
        outage_identity=warning.get("outage_identity"),
    )
    authority = tp._review_contract_authority(workspace, create=True)
    unsigned = {**material, "key_id": authority["key_id"]}
    signature = hmac.new(
        authority["secret"], tp.canonical_json_bytes(unsigned), hashlib.sha256
    ).hexdigest()
    receipt = {**unsigned, "signature": signature}
    receipt_path = runtime_storage.evaluation_path(workspace, "reanchor-authority.json")
    tp.atomic_write_json(receipt_path, receipt, sort_keys=True)
    with open(receipt_path, "rb") as stream:
        receipt_bytes = stream.read()
    reference = {
        "schema": _REANCHOR_AUTHORITY_REF_SCHEMA,
        "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "key_id": authority["key_id"],
    }
    return reference, source_revision


def _verify_reanchor_authority(
    workspace: str,
    task: Mapping,
    prior: Mapping,
    *,
    source_revision: str,
    evaluation_sha256: str,
    criteria_status_sha256: str,
    disposition: str,
) -> tuple[dict | None, str | None]:
    reference = prior.get("reanchor_authority")
    if (
        not isinstance(reference, Mapping)
        or reference.get("schema") != _REANCHOR_AUTHORITY_REF_SCHEMA
        or set(reference) != {"schema", "receipt_sha256", "key_id"}
    ):
        return None, "engine-authored reanchor authority receipt is missing"
    receipt_path = runtime_storage.evaluation_path(workspace, "reanchor-authority.json")
    try:
        with open(receipt_path, "rb") as stream:
            receipt_bytes = stream.read()
        receipt = json.loads(receipt_bytes)
    except (OSError, ValueError) as exc:
        return None, f"engine-authored reanchor authority is unavailable: {exc}"
    if hashlib.sha256(receipt_bytes).hexdigest() != reference.get("receipt_sha256"):
        return None, "engine-authored reanchor authority bytes changed"
    expected = _reanchor_authority_material(
        task,
        source_revision=source_revision,
        evaluation_sha256=evaluation_sha256,
        criteria_status_sha256=criteria_status_sha256,
        disposition=disposition,
        outage_identity=(
            (prior.get("evaluation") or {}).get("outage_identity")
            if isinstance(prior.get("evaluation"), Mapping)
            else None
        ),
    )
    try:
        authority = tp._review_contract_authority(workspace, create=False)
    except Exception as exc:
        return None, f"reanchor signing authority is unavailable: {exc}"
    if not isinstance(receipt, Mapping):
        return None, "engine-authored reanchor authority is malformed"
    unsigned = {**expected, "key_id": authority["key_id"]}
    signature = hmac.new(
        authority["secret"], tp.canonical_json_bytes(unsigned), hashlib.sha256
    ).hexdigest()
    if (
        reference.get("key_id") != authority["key_id"]
        or set(receipt) != set(unsigned) | {"signature"}
        or {key: receipt.get(key) for key in unsigned} != unsigned
        or not hmac.compare_digest(str(receipt.get("signature") or ""), signature)
    ):
        return None, "engine-authored reanchor authority does not match exact pass"
    proof = {
        "schema": _REANCHOR_CRITERION_PROOF_SCHEMA,
        "authority_schema": receipt["schema"],
        "task_id": receipt["task_id"],
        "contract_fingerprint": receipt["contract_fingerprint"],
        "source_revision": receipt["source_revision"],
        "evaluation_sha256": receipt["evaluation_sha256"],
        "criteria_status_sha256": receipt["criteria_status_sha256"],
        "receipt_sha256": reference["receipt_sha256"],
        "disposition": receipt["disposition"],
        "key_id": receipt["key_id"],
    }
    if not _verified_criterion_evidence(proof):
        return None, "engine-authored criterion proof is malformed"
    return proof, None


def _verify_reanchor_task_evidence(
    ws: str, task: Mapping, prior: Mapping
) -> tuple[dict | None, str | None]:
    """Verify exact durable source and evaluation evidence for one pass."""
    task_id = str(task.get("id") or "")
    workspace_raw = str(prior.get("workspace") or "").strip()
    target = str(prior.get("target_commit") or "").strip().lower()
    if not workspace_raw or not os.path.isdir(workspace_raw):
        return None, "passed source workspace is missing"
    workspace = os.path.realpath(workspace_raw)
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", target):
        return None, "passed source target commit is missing or invalid"
    if tp.git_head(workspace) != target:
        return None, "passed source worktree no longer resolves to target"
    if tp.is_dirty(workspace):
        return None, "passed source worktree has uncommitted product changes"

    primary = os.path.realpath(ws)
    if workspace != primary:
        try:
            registration = runtime_storage.load_task_worktree_registration(ws, task_id)
        except runtime_storage.StorageIdentityError as exc:
            return None, f"managed source registration is invalid: {exc}"
        if not isinstance(registration, Mapping):
            return None, "managed source registration is missing"
        if (
            os.path.realpath(str(registration.get("path") or "")) != workspace
            or os.path.realpath(str(registration.get("primary_checkout") or "")) != primary
            or registration.get("branch_tip") != target
            or registration.get("linked") is not True
        ):
            return None, "managed source registration does not bind exact target"

    # Safe argv only: source evidence must still be reachable from the tree
    # whose new Plan is being accepted.
    import subprocess

    try:
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", target, "HEAD"],
            cwd=ws,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_REANCHOR_ANCESTRY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None, "passed source ancestry verification timed out"
    if ancestry.returncode != 0:
        return None, "passed source target is not in the current repository history"

    verdict_path = runtime_storage.evaluation_path(workspace)
    try:
        with open(verdict_path, "rb") as stream:
            verdict_bytes = stream.read()
        verdict = json.loads(verdict_bytes)
    except (OSError, ValueError) as exc:
        return None, f"durable evaluator verdict is unavailable: {exc}"
    if (
        not isinstance(verdict, Mapping)
        or verdict.get("schema") != evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID
    ):
        return None, "durable evaluator verdict schema is invalid"
    if str(verdict.get("task") or "") != task_id:
        return None, "durable evaluator verdict names a different task"
    requirement = str(task.get("req") or "")
    if str(verdict.get("requirement") or "") != requirement:
        return None, "durable evaluator verdict names a different requirement"

    criteria = list(task.get("criteria") or [])
    rows = verdict.get("criteria")
    if not isinstance(rows, list) or len(rows) != len(criteria):
        return None, "durable evaluator verdict has incomplete criteria"
    observed = []
    for row in rows:
        if not isinstance(row, Mapping):
            return None, "durable evaluator criterion evidence is malformed"
        observed.append(row.get("criterion"))
        if row.get("status") != "met":
            return None, "durable evaluator criterion is not proven met"
        descriptive = row.get("evidence")
        if not isinstance(descriptive, str) or not descriptive.strip():
            return None, "durable evaluator criterion description is missing"
    if observed != criteria or len(set(map(str, observed))) != len(observed):
        return None, "durable evaluator criteria do not exactly match the task"

    failures = verdict.get("failures")
    if not isinstance(failures, list):
        return None, "durable evaluator failures are malformed"
    availability = verdict.get("evaluation")
    resolution = "independent-pass"
    if isinstance(availability, Mapping) and availability.get("status") == "unavailable":
        human = prior.get("human_resolution")
        warning = prior.get("evaluation")
        if not isinstance(human, Mapping) or human.get("decision") != "pass":
            return None, "unavailable evaluation has no resolved human pass"
        reason_code = availability.get("reason_code")
        if reason_code not in set(_REANCHOR_RESOLVED_OUTAGE_REASONS.values()):
            return None, "resolved outage reason is not supported"
        if (
            not isinstance(warning, Mapping)
            or warning.get("status") != "unavailable"
            or warning.get("verdict") != "non-judged"
            or warning.get("reason_code") != reason_code
        ):
            return None, "resolved outage warning is not exact"
        if verdict.get("verdict") != "fail" or availability.get("reason_code") != reason_code:
            return None, "durable outage verdict is not a non-judged failure"
        try:
            identity = evaluator_health.outage_identity(
                task=task_id, requirement=requirement, evaluation=availability, failures=failures
            )
        except evaluator_health.EvaluatorHealthError as exc:
            return None, f"durable outage identity is invalid: {exc}"
        if warning.get("outage_identity") != identity:
            return None, "resolved outage identity no longer matches verdict"
        if reason_code == "producer_receipt_unavailable":
            if (
                not str(human.get("actor") or "").strip()
                or human.get("outage_reason_code") != reason_code
                or human.get("outage_fingerprint") != identity.get("fingerprint")
            ):
                return (
                    None,
                    "producer-receipt acceptance is not bound to the exact human-approved outage",
                )
            resolution = "human-resolved-producer-receipt-outage"
        else:
            resolution = "human-resolved-orchestration-outage"
    elif verdict.get("verdict") != "pass" or failures:
        return None, "durable evaluator verdict is not an exact pass"

    evaluation_sha256 = hashlib.sha256(verdict_bytes).hexdigest()
    try:
        criteria_status_sha256 = _validated_reanchor_verdict(task, verdict, resolution)
    except ValueError as exc:
        return None, f"durable evaluator verdict is invalid: {exc}"
    criterion_proof, authority_error = _verify_reanchor_authority(
        workspace,
        task,
        prior,
        source_revision=target,
        evaluation_sha256=evaluation_sha256,
        criteria_status_sha256=criteria_status_sha256,
        disposition=resolution,
    )
    if authority_error:
        return None, authority_error
    if not _verified_criterion_evidence(criterion_proof):
        return None, "engine-authored criterion proof is invalid"

    return {
        "target_commit": target,
        "workspace": workspace,
        "evaluation_path": verdict_path,
        "evaluation_sha256": evaluation_sha256,
        "criterion_proof": criterion_proof,
        "resolution": resolution,
    }, None


def _reanchor_replanned_tasks(ws: str, state: dict) -> tuple[dict | None, list]:
    """Restore only evidence-proven, unchanged, dependency-closed passes."""
    history = state.get("replan_history")
    if not history:
        return None, []
    if not isinstance(history, list) or not isinstance(history[-1], Mapping):
        return None, ["replan reanchor: latest replan history is ambiguous"]
    archived = history[-1].get("tasks")
    current = state.get("tasks")
    if not isinstance(archived, list) or not isinstance(current, list):
        return None, ["replan reanchor: latest task snapshots are ambiguous"]

    def indexed(tasks, label):
        result = {}
        for item in tasks:
            if not isinstance(item, Mapping):
                return None, f"replan reanchor: {label} task is malformed"
            task_id = str(item.get("id") or "").strip()
            if not task_id or task_id in result:
                return None, (f"replan reanchor: {label} task identity is missing or duplicated")
            result[task_id] = item
        return result, None

    prior_by_id, error = indexed(archived, "archived")
    if error:
        return None, [error]
    current_by_id, error = indexed(current, "current")
    if error:
        return None, [error]

    candidates = {}
    pending = {}
    for task_id, task in current_by_id.items():
        task["status"] = "pending"
        prior = prior_by_id.get(task_id)
        if prior is None:
            pending[task_id] = {"task_id": task_id, "reason": "new_task"}
            continue
        if _reanchor_contract(task) != _reanchor_contract(prior):
            pending[task_id] = {
                "task_id": task_id,
                "reason": "immutable_contract_changed",
                "current_contract": _reanchor_fingerprint(task),
                "archived_contract": _reanchor_fingerprint(prior),
            }
            continue
        if prior.get("status") != "passed":
            pending[task_id] = {
                "task_id": task_id,
                "reason": "archived_task_not_passed",
                "archived_status": prior.get("status"),
            }
            continue
        evidence, evidence_error = _verify_reanchor_task_evidence(ws, task, prior)
        if evidence_error:
            pending[task_id] = {
                "task_id": task_id,
                "reason": "evidence_unverified",
                "detail": evidence_error,
            }
            continue
        candidates[task_id] = (task, prior, evidence)

    restored_ids = set()
    restored = []
    progressed = True
    while progressed:
        progressed = False
        for task_id, (task, prior, evidence) in candidates.items():
            if task_id in restored_ids:
                continue
            dependencies = list(task.get("deps") or [])
            if any(dep not in restored_ids for dep in dependencies):
                continue
            task["status"] = "passed"
            task["fix_cycles"] = int(prior.get("fix_cycles") or 0)
            for field in (
                "workspace",
                "target_commit",
                "human_resolution",
                "evaluation",
                "reanchor_authority",
            ):
                if field in prior:
                    task[field] = json.loads(json.dumps(prior[field]))
            restored_ids.add(task_id)
            restored.append(
                {
                    "task_id": task_id,
                    "contract_fingerprint": _reanchor_fingerprint(task),
                    **dict(evidence or {}),
                }
            )
            progressed = True

    for task_id, (task, _, _) in candidates.items():
        if task_id in restored_ids:
            continue
        missing = [dep for dep in list(task.get("deps") or []) if dep not in restored_ids]
        pending[task_id] = {
            "task_id": task_id,
            "reason": "dependency_not_reanchored",
            "dependencies": missing,
        }

    receipt = {
        "schema": "taskplane.replan-reanchor/v1",
        "replan_index": len(history) - 1,
        "replan_by": history[-1].get("by"),
        "replan_reason": history[-1].get("reason"),
        "contract_fields": list(_REANCHOR_CONTRACT_FIELDS),
        "restored": restored,
        "pending": [pending[task_id] for task_id in current_by_id if task_id in pending],
        "restored_count": len(restored),
        "pending_count": len(current) - len(restored),
        "dependency_closed": True,
    }
    receipt["fingerprint"] = hashlib.sha256(tp.canonical_json_bytes(receipt)).hexdigest()
    state["replan_reanchor"] = receipt
    audit = state.setdefault("replan_reanchor_history", [])
    if not audit or audit[-1].get("fingerprint") != receipt["fingerprint"]:
        audit.append(json.loads(json.dumps(receipt)))
    return receipt, []


def _first_unsettled_task_index(state: Mapping) -> int | None:
    for index, task in enumerate(state.get("tasks") or []):
        if task.get("status") not in SETTLED:
            return index
    return None


def _review_baseline(ws: str, state: Mapping, step: str) -> str | None:
    from taskplane import phase_amendment

    comparison = phase_amendment.review_comparison(sys.modules[__name__], ws, dict(state))
    return comparison or state.get("baseline")


def _task_graph_dod(ws: str, state: dict, task: dict) -> dict:
    """As-built dependency proof in the caller's exact task or merged tree."""
    baseline = _review_baseline(ws, state, "evaluate") or tp.snapshot_ref(ws)
    changed = [
        f for f in _diff_files(ws, baseline or "HEAD") if not f.startswith(lens_router.LOOP_OWNED)
    ]
    mine = [f for f in changed if not task.get("scope") or tp.match_any(f, task["scope"])]
    planned = (task.get("blast") or {}).get("modules") or depgraph.scope_modules(
        ws, task.get("scope") or []
    )
    return depgraph.completion(
        ws,
        mine,
        planned_modules=planned,
        policy=task.get("impact_policy") or depgraph.impact_policy(task),
    )


def _task_graph_evidence_errors(ws: str, state: dict, task: dict, verdict: dict) -> list:
    """The same dependency evidence obligations at task Evaluate and merged EM."""
    if not state.get("graph_governance"):
        return []
    graph_dod = _task_graph_dod(ws, state, task)
    errors = ["graph DoD: " + error for error in graph_dod.get("errors") or []]
    impact = graph_dod.get("impact") or {}
    graph = depgraph.load(ws)
    module_ids = depgraph.declared_module_ids(graph)
    # Keep the complete graph, but apply the same bookkeeping boundary as
    # the changed-file check. Unknown and mixed-source modules remain owed.
    owned_modules, source_modules = set(), set()
    for path in graph.get("files") or {}:
        modules = owned_modules if path.startswith(lens_router.LOOP_OWNED) else source_modules
        modules.add(depgraph.module_of(path, module_ids))
    owned_modules -= source_modules
    direct = sorted(
        {
            e.get("module")
            for e in (impact.get("impacted") or {}).get(1, [])
            if e.get("module")
            and e.get("module") not in owned_modules
            and not str(e.get("module")).startswith("req:")
        }
    )
    prod = depgraph.product_impact(ws, graph_dod.get("realized_modules") or [])
    own = task.get("req") or state.get("requirement_id")
    own = depgraph.req_node(own) if own else None
    affected = sorted(r for r in prod.get("affected_requirements") or [] if r != own)
    needs_graph_evidence = bool(
        direct
        or affected
        or graph_dod.get("contract_files")
        or impact.get("unknown")
        or impact.get("truncated")
    )
    graph_ev = verdict.get("graph") or {}
    if needs_graph_evidence and not isinstance(verdict.get("graph"), dict):
        errors.append("evaluation is missing graph impact evidence")
        graph_ev = {}
    dispositions = {
        str(x.get("node")): x for x in (graph_ev.get("dispositions") or []) if isinstance(x, dict)
    }
    allowed = {"tested", "contract-verified", "unaffected", "follow-up", "requires-replan"}
    for node in direct:
        row = dispositions.get(node)
        if (
            not row
            or row.get("status") not in allowed
            or not str(row.get("evidence") or "").strip()
        ):
            errors.append(f"graph impact has no evidenced disposition: {node}")
        elif row.get("status") == "requires-replan":
            errors.append(f"graph impact requires replanning: {node}")
    checked = set(graph_ev.get("requirements_checked") or [])
    for rid in affected:
        if rid not in checked:
            errors.append("affected requirement was not re-checked: " + rid)
    expected_contracts = set()
    for contract_row in task.get("contracts") or []:
        contract_id = contract_row.get("id") if isinstance(contract_row, dict) else contract_row
        if str(contract_id or "").strip():
            expected_contracts.add(str(contract_id))
    checked_contracts = set(graph_ev.get("contracts_checked") or [])
    for contract in sorted(expected_contracts - checked_contracts):
        errors.append("declared contract was not verified: " + contract)
    return errors


def _worker_stage_binding(workspace: str, stage: str, task: Mapping | None) -> dict | None:
    """Read exact worker lifecycle metadata without binding root authority."""
    task_ref = str((task or {}).get("id") or stage)
    return tp.worker_contract_for_stage(workspace, stage=str(stage), task=task_ref)


def _worker_stage_contract(workspace: str, stage: str, task: Mapping | None) -> dict:
    binding = _worker_stage_binding(workspace, stage, task)
    if binding is not None:
        return binding["contract"]
    return tp.load_active(workspace) or {}


def _worker_stage_snapshot(workspace: str, stage: str, task: Mapping | None) -> str | None:
    binding = _worker_stage_binding(workspace, stage, task)
    return tp.snapshot_ref(workspace, task_slot_override=(binding or {}).get("slot"))


def _task_dod_errors(ws: str, state: dict, task: dict, snapshot: str | None) -> list:
    contract = tp.build_contract(
        f"EXECUTE: {task['id']}",
        scope=task.get("scope"),
        test_command=task.get("tests"),
        plan_minted=True,
        regression_gate=True,
        test_timeout_seconds=tp.task_test_timeout_seconds(task),
    )
    # Scope regression evidence to this task; loop-owned artifacts self-gate.
    regression_files = [
        f
        for f in (tp.changed_files(ws, snapshot) if snapshot else [])
        if tp.match_any(f, task.get("scope") or [])
    ]
    suite_evidence = {}
    errors = _design_current_errors(ws, state) + tp.dod_check(
        contract,
        ws,
        snapshot,
        ignore_prefixes=lens_router.LOOP_OWNED,
        regression_files=regression_files,
        suite_evidence=suite_evidence,
    )
    # Preserve the long-standing four-argument patch seam used by race and
    # failure-injection tests. This is transient validation output: gate()
    # copies it into the fresh locked state only after all checks pass.
    if suite_evidence:
        state.setdefault("_validated_suite_evidence", {})[task["id"]] = suite_evidence
    return errors


@contextlib.contextmanager
def _claimed_execute_suite_binding():
    """Keep gate command policy while using the existing checkout runner.

    Leading assignments belong to the child environment, not argv. The
    incumbent runner owns transitive Python checkout binding and execution.
    """
    import subprocess

    original_runner = tp.run_suite_command

    def safe_argv(command):
        if isinstance(command, (list, tuple)):
            argv = list(command)
            if not argv or any(not isinstance(value, str) or not value for value in argv):
                raise ValueError("declared suite argv is invalid")
            return argv
        if not isinstance(command, str) or not command.strip():
            raise ValueError("declared suite command is invalid")
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>")
            lexer.whitespace_split = True
            lexer.commenters = ""
            argv = list(lexer)
        except ValueError as exc:
            raise ValueError(f"declared suite command has invalid quoting: {exc}") from exc
        if not argv or any(token and set(token) <= set("|&;<>") for token in argv):
            raise ValueError("declared suite command contains shell operators")
        return argv

    def run_claimed(workspace, command, *, env=None, timeout=600):
        try:
            argv = safe_argv(command)
            child_env = dict(os.environ if env is None else env)
            while argv and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[0]):
                name, value = argv.pop(0).split("=", 1)
                child_env[name] = value
            if not argv:
                raise ValueError("declared suite command has no executable")
        except ValueError as exc:
            return subprocess.CompletedProcess(command, 2, stdout="", stderr=str(exc))
        return original_runner(workspace, argv, env=child_env, timeout=timeout)

    tp.run_suite_command = run_claimed
    try:
        yield
    finally:
        tp.run_suite_command = original_runner


def _purge_review_generation_diff(review_ws: str, run_id: str) -> None:
    """End the raw diff's lifetime only after its actual consumer commits."""
    _, review_evidence, review_kernel = _review_runtime_modules()
    state = review_kernel._load_state(review_ws, run_id)
    store = review_evidence.ArtifactStore(review_ws)
    envelope = store.read(state["envelope"])
    retained_diff = (envelope.get("diff") or {}).get("artifact")
    if isinstance(retained_diff, dict):
        purge = enforce_review_diff_retention(
            review_ws, store=store, purge_fingerprint=str(retained_diff.get("fingerprint") or "")
        )
        tp.trace(
            review_ws,
            "review_diff_retention_purge",
            run_id=run_id,
            review_id=str((state.get("target") or {}).get("fingerprint") or "unknown"),
            count=purge.get("removed", 0),
        )


def collect_review_bridge(
    review_ws: str,
    *,
    publish: bool,
    run_id: str,
    evaluator_result: dict | None = None,
    producer_observation_fingerprint: str | None = None,
    collection_stage: str = "Evaluate",
    result_validator=None,
) -> dict:
    """Collect a ReviewKernel run and release its exact producer slots.

    A provisional collection still ends the producer wave: missing or
    invalid outputs become named repair evidence, while stale producer
    contracts must not remain in the parent contract union.
    EM collection prepares the reviewer input; its committed gate owns purge.
    """
    _, review_evidence, review_kernel = _review_runtime_modules()

    state = review_kernel._load_state(review_ws, run_id)
    try:
        store = review_evidence.ArtifactStore(review_ws)
        envelope_ref = state.get("envelope")
        envelope = store.read(envelope_ref) if isinstance(envelope_ref, dict) else {}
        retained_diff = (envelope.get("diff") or {}).get("artifact")
        if isinstance(retained_diff, dict):
            read_retained_review_diff(review_ws, store=store, reference=retained_diff)
        empty_collection = None
        if (
            state.get("zero_lens_evaluation") is True
            or state.get("delivery_mode_receipt") is not None
        ):
            if state.get("expected_lenses") != [] or state.get("slots") != []:
                raise review_kernel.ReviewKernelError(
                    "sealed zero-lens Evaluate authority produced lens slots"
                )
            if evaluator_result is None or producer_observation_fingerprint is None:
                raise review_kernel.ReviewKernelError(
                    "zero-lens collection requires a schema-valid producer "
                    "result and validated observation"
                )
            validator = result_validator
            if validator is None:
                validator = (
                    evaluation_output.validate_evaluator_value
                    if collection_stage == "Evaluate"
                    else lambda value: value
                )
            empty_collection = review_kernel.collect_expected_set(
                run_id=run_id,
                task_id=str((state.get("target") or {}).get("task") or ""),
                stage=collection_stage,
                expected_lenses=state["expected_lenses"],
                collected_lenses=[],
                result=evaluator_result,
                result_validator=validator,
                producer_observation_fingerprint=producer_observation_fingerprint,
            )
        result = review_kernel.collect_review(
            review_ws, publish=publish, run_id=run_id, empty_lens_collection=empty_collection
        )
        if result.get("status") == "complete" and collection_stage != "EM":
            _purge_review_generation_diff(review_ws, run_id)
        return result
    finally:
        review_kernel._release_slot_contracts(review_ws, state)


def _collect_zero_lens_evaluate_before_guidance(
    ws: str, act_ws: str, state: dict, task: dict, *, step: str = "evaluate"
) -> dict | None:
    """Consume the one native receipt and seal an ordinary empty set."""
    binding = review_kernel_binding(state, step, task)
    if not binding:
        return None
    kernel_ws = str(binding.get("workspace") or act_ws)
    _, _, review_kernel = _review_runtime_modules()
    kernel = review_kernel._load_state(kernel_ws, binding["run_id"])
    if (
        kernel.get("zero_lens_evaluation") is not True
        and kernel.get("delivery_mode_receipt") is None
    ):
        return None
    if kernel.get("expected_lenses") != [] or kernel.get("slots") != []:
        raise review_kernel.ReviewKernelError(
            "sealed zero-lens Evaluate authority produced lens slots"
        )
    active_contract = _worker_stage_contract(act_ws, step, task)
    material = producer_output_identity(act_ws, state, task, step, active_contract=active_contract)
    observation = (state.get("_submission") or {}).get("producer_observation")
    if observation is None:
        observation = producer_observation_policy.consume_matching_observation(**material)
    else:
        producer_observation_policy.validate_consumed_matching_observation(observation, **material)
    if step == "evaluate":
        raw_result = json.loads(material["output_bytes"].decode("utf-8"))
        evidence_route = state.get("evaluate_child_evidence")
        if not isinstance(evidence_route, Mapping):
            raise ValueError("Evaluate child evidence route is missing")
        result = consume_evaluate_evidence_before_pass(
            raw_result,
            artifact_root=_run_artifact_root(ws, state),
            run_id=str(evidence_route.get("run_id") or ""),
            evaluator_attempt_id=str(evidence_route.get("evaluator_attempt_id") or ""),
            expected_binding=evidence_route.get("binding") or {},
        )
        if result != raw_result:
            raise ValueError("evaluator output did not directly consume canonical child evidence")
        collection_stage = "Evaluate"
        validator = lambda value: evaluation_output.validate_evaluator_value(
            value,
            expected_lenses=[],
            expected_evidence_binding=dict(evidence_route.get("binding") or {}),
        )
    else:
        findings_path = runtime_storage.review_public_path(act_ws, "findings.json")
        report_path = runtime_storage.review_public_path(act_ws, "report.md")
        findings, read_errors = _read_json(findings_path)
        if read_errors:
            raise producer_observation_policy.ProducerObservationError(
                "EM findings result is invalid"
            )
        with open(report_path, "rb") as stream:
            report_bytes = stream.read()
        result = {"findings": findings, "report_sha256": hashlib.sha256(report_bytes).hexdigest()}
        collection_stage = "EM"
        validator = lambda value: value
    if step == "evaluate":
        collect_review_bridge(
            kernel_ws,
            publish=False,
            run_id=binding["run_id"],
            evaluator_result=result,
            producer_observation_fingerprint=observation["fingerprint"],
            collection_stage=collection_stage,
            result_validator=validator,
        )
    return observation


def _acceptance_evidence_errors(
    ws: str, state: dict, task: dict, verdict: dict, *, phase_package=None
) -> list:
    """Candidate-bound DoD evidence check shared with runtime guidance."""
    errors = []
    if phase_package is not None:
        from taskplane import plan_topology

        planned = _validated_phase_contribution_plan(phase_package, state)
        return plan_topology.acceptance_evidence_errors(
            planned["acceptance"], verdict, read=phase_package.store.read
        )
    # A native aggregate derives its obligations from its retained predecessor
    # chain. Omitting structured proof cannot fall back to nonempty prose.
    try:
        aggregate = _current_phase_contribution_package(ws, state)
        if aggregate is not None:
            package, build_handoff = aggregate
            references = [
                json.loads(row.get("evidence", ""))["acceptance_evidence"]
                for row in verdict.get("criteria", [])
            ]
            if not references or any(row != references[0] for row in references):
                raise ValueError(
                    "aggregate criteria require one exact acceptance evidence reference"
                )
            accept_phase_contributions(
                package, state, package.store.read(references[0]), build_handoff=build_handoff
            )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        errors.append("aggregate contribution acceptance failed: " + str(exc))
    expected_criteria = _criteria_for(ws, state, task)
    rows = verdict.get("criteria") or []
    if not isinstance(rows, list):
        errors.append("evaluation criteria must be a list")
        rows = []
    by_criterion = {str(r.get("criterion", "")).strip(): r for r in rows if isinstance(r, dict)}
    for criterion in expected_criteria:
        row = by_criterion.get(criterion)
        if not row:
            errors.append(f"acceptance criterion has no evidence: {criterion}")
        elif row.get("status") != "met" or not str(row.get("evidence") or "").strip():
            errors.append(f"acceptance criterion is not proven met: {criterion}")
    return errors


def _evaluation_errors(ws: str, state: dict, task: dict, *, artifact_ws: str | None = None) -> list:
    """Validate evaluator evidence instead of trusting `gate pass`."""
    path = runtime_storage.evaluation_path(ws)
    verdict, errors = _read_json(path)
    if errors:
        return errors
    evidence_route = state.get("evaluate_child_evidence")
    if not isinstance(evidence_route, Mapping):
        errors.append("Evaluate child evidence route is missing")
    else:
        try:
            consumed = consume_evaluate_evidence_before_pass(
                verdict,
                artifact_root=_run_artifact_root(artifact_ws or ws, state),
                run_id=str(evidence_route.get("run_id") or ""),
                evaluator_attempt_id=str(evidence_route.get("evaluator_attempt_id") or ""),
                expected_binding=evidence_route.get("binding") or {},
            )
            if consumed != verdict:
                errors.append("evaluator output did not directly consume canonical child evidence")
        except Exception as exc:
            errors.append(
                f"Evaluate child evidence admission failed: {exc.__class__.__name__}: {exc}"
            )
    errors.extend(_design_current_errors(ws, state))
    import review as _review

    binding = review_kernel_binding(state, "evaluate", task)
    kernel_ws = str((binding or {}).get("workspace") or ws)
    kernel = None
    if binding:
        try:
            kernel = _review._load_state(kernel_ws, binding["run_id"])
        except Exception:
            kernel = None
    if kernel and kernel.get("status") == "ready" and kernel.get("stage") == EVALUATE_ROUTE_STAGE:
        try:
            evaluator_result = None
            observation_fingerprint = None
            if (
                kernel.get("zero_lens_evaluation") is True
                or kernel.get("delivery_mode_receipt") is not None
            ):
                evaluator_result = evaluation_output.validate_evaluator_value(
                    verdict,
                    expected_lenses=[],
                    expected_evidence_binding=dict((evidence_route or {}).get("binding") or {}),
                )
                submission = state.get("_submission") or {}
                observation = producer_observation_policy.validate_producer_observation(
                    submission.get("producer_observation")
                )
                with open(path, "rb") as stream:
                    verdict_bytes = stream.read()
                observation = evaluation_output.validate_submission_observation(
                    submission,
                    output_bytes=verdict_bytes,
                    output_schema_id=evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
                    output_contract_fingerprint=observation["output_contract_fingerprint"],
                )
                observation_fingerprint = observation["fingerprint"]
            collect_review_bridge(
                kernel_ws,
                publish=False,
                run_id=kernel.get("run_id"),
                evaluator_result=evaluator_result,
                producer_observation_fingerprint=observation_fingerprint,
            )
            kernel = _review._load_state(kernel_ws, kernel.get("run_id"))
        except Exception as exc:
            errors.append(
                f"evaluation leased slot collection failed: {exc.__class__.__name__}: {exc}"
            )
    if (
        not kernel
        or kernel.get("status") != "complete"
        or kernel.get("stage") != EVALUATE_ROUTE_STAGE
    ):
        errors.append("evaluation evidence kernel is missing or incomplete")
    if verdict.get("task") != task.get("id"):
        errors.append(
            f"evaluation evidence is for task {verdict.get('task')!r}, expected {task.get('id')!r}"
        )
    if verdict.get("verdict") != "pass":
        errors.append("evaluation verdict is not pass")

    errors.extend(_acceptance_evidence_errors(ws, state, task, verdict))

    # Evaluate owns one judgment output. Lens routes, leased lens results,
    # retry invalidation, and lens verdict conservation are intentionally not
    # part of this stage after D-0014.
    canonical_blocking = _review.blocking_findings_by_lens(
        ((kernel or {}).get("revision") or {}).get("findings") or []
    )
    for lens_id, count in sorted(canonical_blocking.items()):
        errors.append(f"canonical blocking finding prevents Evaluate pass: {lens_id} ({count})")
    if verdict.get("failures"):
        errors.append("evaluation contains unresolved failures")
    errors.extend(_task_graph_evidence_errors(ws, state, task, verdict))
    return errors


def _canonical_evaluation_progress(ws: str, state: dict, task: dict) -> dict | None:
    """Project the committed evaluator revision into convergence facts."""
    import review as _review
    import review_evidence as _review_evidence

    binding = review_kernel_binding(state, "evaluate", task)
    if not binding:
        return None
    kernel_ws = str(binding.get("workspace") or ws)
    kernel = _review._load_state(kernel_ws, binding["run_id"])
    if kernel.get("status") != "complete" or kernel.get("stage") != EVALUATE_ROUTE_STAGE:
        return None
    sealed = _review_evidence.sealed_current_revision(
        _review_evidence.ArtifactStore(kernel_ws), kernel.get("revision") or {}
    )
    verdict, read_errors = _read_json(runtime_storage.evaluation_path(kernel_ws))
    if read_errors:
        verdict = {}
    criteria = verdict.get("criteria") if isinstance(verdict, dict) else []
    evidence_complete = sum(
        isinstance(row, dict)
        and row.get("status") == "met"
        and bool(str(row.get("evidence") or "").strip())
        for row in (criteria if isinstance(criteria, list) else [])
    )
    suite = (state.get("_suite_evidence") or {}).get(str(task.get("id"))) or {}
    import yield_meter

    finding_rows = []
    for row in sealed.get("findings") or []:
        if not isinstance(row, dict) or row.get("admissible") is False:
            continue
        identity = str(row.get("fingerprint") or row.get("id") or "").strip()
        if not identity:
            identity = yield_meter.fingerprint(row)
        if identity:
            finding_rows.append({"id": identity, "admissible": True})
    return {
        "findings": finding_rows,
        "acceptance_evidence_complete": evidence_complete,
        "tests_passed": int(
            suite.get("schema") == "taskplane.suite-evidence/v1" and suite.get("returncode") == 0
        ),
        "canonical_revision": sealed["canonical_revision"],
        "findings_fingerprint": sealed["findings_fingerprint"],
        "scope_fingerprint": _review_evidence.content_fingerprint(
            {
                "scope": task.get("scope") or [],
                "contracts": task.get("contracts") or [],
            }
        ),
        "authority_fingerprint": _review_evidence.content_fingerprint(
            state.get("authority_derivations") or {}
        ),
    }


def _evaluation_unavailable_errors(ws: str, state: dict, task: dict) -> tuple[list, dict]:
    """Admit a pure model/host outage without inventing a product defect."""
    path = runtime_storage.evaluation_path(ws)
    verdict, errors = _read_json(path)
    if errors:
        return errors, {}
    try:
        evaluation_output.validate_evaluator_value(verdict)
    except evaluation_output.OutputValidationError as exc:
        errors.append(f"evaluation output is invalid ({exc.code}): {exc}")
        return errors, verdict
    availability = verdict.get("evaluation") or {}
    if availability.get("status") != "unavailable":
        errors.append("evaluation does not declare structured unavailability")
    if availability.get("reason_code") in (None, "", "none"):
        errors.append("evaluation unavailability has no host reason code")
    if verdict.get("task") != task.get("id"):
        errors.append(
            f"evaluation evidence is for task {verdict.get('task')!r}, expected {task.get('id')!r}"
        )
    if verdict.get("verdict") != "fail":
        errors.append("unavailable evaluation must retain verdict 'fail'")
    not_met = [
        row.get("criterion")
        for row in verdict.get("criteria") or []
        if isinstance(row, dict) and row.get("status") == "not-met"
    ]
    if not_met:
        errors.append("product acceptance is not met: " + ", ".join(str(item) for item in not_met))
    blocking_lenses = [
        row.get("lens")
        for row in verdict.get("lenses") or []
        if isinstance(row, dict)
        and (row.get("verdict") == "fail" or int(row.get("blockers") or 0) > 0)
    ]
    if blocking_lenses:
        errors.append(
            "product/lens failures cannot be classified as host "
            "unavailability: " + ", ".join(str(item) for item in blocking_lenses)
        )
    if not (verdict.get("failures") or []):
        errors.append("evaluation unavailability has no bounded failure record")
    suite = (state.get("_suite_evidence") or {}).get(str(task.get("id"))) or {}
    if task.get("tests") and not (
        suite.get("schema") == "taskplane.suite-evidence/v1" and suite.get("returncode") == 0
    ):
        errors.append(
            "evaluation unavailability requires green mechanical "
            "suite evidence from the execute/fix gate"
        )
    if state.get("_build_failed") or task.get("_build_failed"):
        errors.append("a failed build is a product failure, not evaluation unavailability")
    return errors, verdict


def _failure_candidate_identity(ws: str, task: Mapping[str, object]) -> dict:
    head = str(tp.git_head(ws) or "").strip()
    candidate_id = f"{str(task.get('id') or 'unknown')}@{head}"
    return {
        "id": candidate_id,
        "fingerprint": hashlib.sha256(candidate_id.encode("utf-8")).hexdigest(),
    }


def _detected_build_failure_routing(
    ws: str, task: Mapping[str, object], submission: Mapping[str, object], stage: str
) -> dict:
    """Persist detection truth without guessing product ownership.

    A failed Build/Fix submission proves a red, but it does not prove whether
    the product, test, environment, or infrastructure is wrong.  Detection
    therefore mints a typed ``unknown`` record and a hold route.  A later
    independent evaluator may replace it only with a complete candidate-bound
    inventory; exclusively product records are the sole Fix authority.
    """
    candidate = _failure_candidate_identity(ws, task)
    evidence = {
        "submission_fingerprint": str(submission.get("fingerprint") or "unavailable"),
        "submission_outcome": str(submission.get("outcome") or "fail"),
        "stage": stage,
        "task": str(task.get("id") or "unknown"),
    }
    # Keep the exact detection submission when available. A missing payload
    # cannot supply acceptance authority to later gates.
    if submission.get("task") == task.get("id") and submission.get("step") == stage:
        evidence["submission"] = _copy_json(submission)
    record = {
        "schema": failure_routing.FAILURE_RECORD_SCHEMA_ID,
        "id": "build-detection-"
        + hashlib.sha256(
            json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
                "utf-8"
            )
        ).hexdigest()[:24],
        "source": "taskplane.loop.gate",
        "stage": stage,
        "repro": "re-run the exact failed Build/Fix submission evidence",
        "evidence": evidence,
        "evidence_digest": failure_routing.evidence_digest(evidence),
        "class": "unknown",
        "reason": "Build failure detected; ownership is not yet classified",
        "owner": "independent-evaluation",
        "cluster": "build-detection",
        "route": failure_routing.route_for_class("unknown"),
        "candidate": candidate,
    }
    checked = failure_routing.validate_failure_record(
        record, expected_stage=stage, expected_candidate=candidate
    )
    decision = failure_routing.route_failure_records([checked])
    decision["fingerprint"] = hashlib.sha256(
        json.dumps(
            decision, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    return decision


_DESIGN_TEST_STRATEGY_REFERENCE_SCHEMA = "taskplane.design-test-strategy-reference/v1"
_PLAN_TEST_STRATEGY_REFERENCE_SCHEMA = "taskplane.plan-test-strategy-reference/v1"
_TEST_STRATEGY_AUTHORITY_SCHEMA = "taskplane.test-strategy-authority/v1"
_DESIGN_STRATEGY_REFERENCE_FIELDS = frozenset(
    {
        "schema",
        "path",
        "strategy_fingerprint",
    }
)
_PLAN_STRATEGY_REFERENCE_FIELDS = frozenset(
    {
        *_DESIGN_STRATEGY_REFERENCE_FIELDS,
        "criterion_ids",
        "changed_producer_ids",
    }
)


def _strategy_authority_strings(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(
            not isinstance(item, str) or not item.strip() or item != item.strip() for item in value
        )
    ):
        raise ValueError(f"{label} must be a non-empty trimmed string list")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} contains duplicates")
    return list(value)


def _phase_bridge_context(*args, **kwargs):
    return phase_harness._phase_bridge_context(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_registry(*args, **kwargs):
    return phase_harness._phase_bridge_registry(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_authorize(*args, **kwargs):
    return phase_harness._phase_bridge_authorize(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_retries(*args, **kwargs):
    return phase_harness._phase_bridge_retries(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_operation(*args, **kwargs):
    return phase_harness._phase_bridge_operation(sys.modules[__name__], *args, **kwargs)


def _phase_retry_release(*args, **kwargs):
    return phase_harness._phase_retry_release(sys.modules[__name__], *args, **kwargs)


def _resolve_phase_retry(*args, **kwargs):
    return phase_harness._resolve_phase_retry(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_freshness(*args, **kwargs):
    return phase_harness._phase_bridge_freshness(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_signing(*args, **kwargs):
    return phase_harness._phase_bridge_signing(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_telemetry(*args, **kwargs):
    return phase_harness._phase_bridge_telemetry(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_retro_inputs(*args, **kwargs):
    return phase_harness._phase_bridge_retro_inputs(sys.modules[__name__], *args, **kwargs)


def collect_phase_runtime_telemetry(*args, **kwargs):
    return phase_harness.collect_phase_runtime_telemetry(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_runtime(*args, **kwargs):
    return phase_harness._phase_bridge_runtime(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_build_owner(*args, **kwargs):
    return phase_harness._phase_bridge_build_owner(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_output_location(*args, **kwargs):
    return phase_harness._phase_bridge_output_location(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_preparation_operation(*args, **kwargs):
    return phase_harness._phase_bridge_preparation_operation(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_prepare(*args, **kwargs):
    return phase_harness._phase_bridge_prepare(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_pending(*args, **kwargs):
    return phase_harness._phase_bridge_pending(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_attempt(*args, **kwargs):
    return phase_harness._phase_bridge_attempt(sys.modules[__name__], *args, **kwargs)


def observe_phase_runtime_hook(*args, **kwargs):
    return phase_harness.observe_phase_runtime_hook(sys.modules[__name__], *args, **kwargs)


def _collect_phase_attempt(*args, **kwargs):
    return phase_harness._collect_phase_attempt(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_gate_check(*args, **kwargs):
    return phase_harness._phase_bridge_gate_check(sys.modules[__name__], *args, **kwargs)


def _phase_bridge_retro_completion(*args, **kwargs):
    return phase_harness._phase_bridge_retro_completion(sys.modules[__name__], *args, **kwargs)


from taskplane.stage_handoff import (
    PhasePackage,
    _phase_result_definition,
    consume_phase_handoff,
    produce_phase_handoff,
)


def validate_spec_phase_artifact(*args, **kwargs):
    return phase_harness.validate_spec_phase_artifact(sys.modules[__name__], *args, **kwargs)


def store_spec_phase_outputs(*args, **kwargs):
    return phase_harness.store_spec_phase_outputs(sys.modules[__name__], *args, **kwargs)


def _phase_contribution_inventory(design):
    """Select the engine wave's explicitly declared FP-AC inventory only."""
    declared = "design_counts" in design or any(
        isinstance(row, Mapping) and str(row.get("criterion_id", "")).startswith("FP-AC")
        for row in design.get("acceptance_map", [])
    )
    if declared:
        depgraph.design_traceability_inventory(dict(design))
    return declared


def seal_phase_plan_task(
    store: object,
    package: PhasePackage,
    state: Mapping[str, object],
    task: Mapping[str, object],
    *,
    workspace: str | None = None,
) -> dict:
    """Plan-owned candidate producer using actual sealed Design outputs."""
    if package.store is not store:
        raise ValueError("Plan package artifact store differs")
    if not any(
        row["artifact_class"] == "plan-task"
        for row in package.registry.admit(package.phase_id, ()).to_dict()["produces"]
    ):
        raise ValueError("phase definition cannot produce Plan authority")
    from taskplane import plan_topology, review_evidence

    result = _copy_json(task)
    if "tasks" in result:
        if not isinstance(result["tasks"], list) or not result["tasks"]:
            raise ValueError("Plan requires a nonempty task inventory")
        for row in result["tasks"]:
            if not isinstance(row, dict):
                raise ValueError("Plan task must be an object")
            row["test_strategy_authority_receipt"] = _seal_task_test_strategy_authority(
                "", state, row, design_package=package
            )
        design = package.read("design")
        binding = {
            "run_id": package.run_id,
            "candidate_fingerprint": package.candidate_fingerprint,
            "plan_fingerprint": review_evidence.content_fingerprint(result),
            "design_fingerprint": state["design_fingerprint"],
        }
        value = {"schema": "taskplane.plan-task/v1", "plan": result}
        if _phase_contribution_inventory(design):
            value.update(
                traceability=plan_topology.build_plan_traceability(design, result),
                owners=plan_topology.build_plan_owner_inventory(design, result),
                acceptance=plan_topology.build_plan_acceptance(design, result, binding=binding),
            )
    else:
        result["test_strategy_authority_receipt"] = _seal_task_test_strategy_authority(
            "", state, result, design_package=package
        )
        value = {"schema": "taskplane.plan-task/v1", "task": result}
    if workspace is not None:
        value["dependency_outputs"] = plan_topology.produce_dependency_plan(
            workspace,
            binding={
                "run_id": package.run_id,
                "candidate_fingerprint": package.candidate_fingerprint,
                "requirement_fingerprint": review_evidence.content_fingerprint(
                    package.read("requirement")
                ),
                "design_fingerprint": state["design_fingerprint"],
                "plan_fingerprint": review_evidence.content_fingerprint(result),
            },
            seam_contracts=package.read("design").get("seam_contracts", []),
            plan=result if "tasks" in result else None,
        )
    return validate_spec_phase_artifact(value)


def _validated_phase_contribution_plan(package, state):
    from taskplane import plan_topology, review_evidence

    planned = package.read("plan-task")
    plan = planned["plan"]
    for task in plan["tasks"]:
        _validated_task_test_strategy_authority("", state, task, design_package=package)
    design = package.read("design")
    if not _phase_contribution_inventory(design):
        if {"traceability", "owners", "acceptance"} & set(planned):
            raise ValueError("ordinary Plan contains undeclared contribution authority")
        return planned
    binding = {
        "run_id": package.run_id,
        "candidate_fingerprint": package.candidate_fingerprint,
        "plan_fingerprint": review_evidence.content_fingerprint(plan),
        "design_fingerprint": state["design_fingerprint"],
    }
    if (
        planned["traceability"] != plan_topology.build_plan_traceability(design, plan)
        or planned["owners"] != plan_topology.build_plan_owner_inventory(design, plan)
        or planned["acceptance"]
        != plan_topology.build_plan_acceptance(design, plan, binding=binding)
    ):
        raise ValueError("Plan contribution/proof lineage differs from its actual producer")
    return planned


def _current_phase_contribution_package(ws, state, *, require_contributions=True):
    """Resolve retained Plan/Build lineage for the existing aggregate DoD."""
    from taskplane import stage_handoff

    context = _phase_bridge_context(ws, state)
    if context is None or context["stage"]["stage_kind"] not in {"evaluate", "engineering"}:
        return None
    authority = context["stage"]["authority"]
    options = {
        "expected_authority_revision": authority["authority_revision"],
        "expected_authority_fingerprint": authority["authority_fingerprint"],
    }
    reference = context["stage"]["input_manifest_ref"]
    built = None
    multi = False
    for _ in range(3):  # Engineering -> Evaluate -> Build -> Plan, no broad walk.
        manifest = stage_handoff.read_v2_manifest(context["artifacts"], reference, **options)
        for artifact in manifest["produced_artifacts"] + manifest["inherited_artifacts"]:
            if artifact["artifact_class"] == "plan-task":
                multi = "plan" in context["artifacts"].read(artifact["reference"])
            if (
                require_contributions
                and artifact["artifact_class"] == "design"
                and not _phase_contribution_inventory(
                    context["artifacts"].read(artifact["reference"])
                )
            ):
                return None
        if require_contributions and not multi:
            return None
        phase = manifest["phase_result"]["phase_id"]
        if phase == "build":
            built = reference
        if phase == "plan":
            if built is None:
                raise ValueError("aggregate contribution acceptance lacks Build completion")
            package = consume_phase_handoff(
                context["artifacts"],
                reference,
                registry=context["registry"],
                phase_id="build",
                **options,
                expected_run_id=context["run_id"],
                expected_candidate_fingerprint=context["configuration"]["candidate_fingerprint"],
            )
            return package, built
        previous = [
            row for row in manifest["evidence_references"] if row["kind"] == "stage-handoff"
        ]
        if len(previous) != 1:
            raise ValueError("aggregate contribution predecessor is missing or ambiguous")
        reference = previous[0]
    raise ValueError("aggregate contribution Plan lineage exceeds its phase boundary")


def accept_phase_contributions(package, state, evidence, *, build_handoff):
    """Aggregate current Plan obligations through the incumbent acceptance check."""
    if package.phase_id != "build":
        raise ValueError("acceptance requires the current Build input package")
    from taskplane import stage_handoff, review_evidence

    built = stage_handoff.read_v2_manifest(
        package.store,
        build_handoff,
        expected_authority_revision=package.authority_revision,
        expected_authority_fingerprint=package.authority_fingerprint,
    )
    result = built["phase_result"]
    _phase_result_definition(package.registry, result)
    if (
        result["phase_id"] != "build"
        or result["run_id"] != package.run_id
        or result["candidate_fingerprint"] != package.candidate_fingerprint
        or package.reference["fingerprint"]
        not in {row["fingerprint"] for row in built["evidence_references"]}
    ):
        raise ValueError("acceptance Build/Plan candidate binding is stale")
    conformance = [
        package.store.read(row["reference"])
        for row in built["produced_artifacts"]
        if row["artifact_class"] == "realized-conformance"
    ]
    if len(conformance) != 1 or conformance[0]["status"] != "conformant":
        raise ValueError("acceptance requires current Build conformance")
    errors = _acceptance_evidence_errors("", state, {}, evidence, phase_package=package)
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "status": "accepted",
        "plan_handoff": package.reference["fingerprint"],
        "build_handoff": build_handoff["fingerprint"],
        "candidate_fingerprint": package.candidate_fingerprint,
        "evidence_reference": package.store.put("acceptance-evidence", dict(evidence)),
        "evidence_fingerprint": review_evidence.content_fingerprint(evidence),
    }


def seal_phase_build_conformance(
    store: object, package: PhasePackage, workspace: str, *, task_id=None
) -> dict:
    """Build consumes actual Plan outputs and compares fresh integrated source."""
    from taskplane import graph_decomposition, plan_topology, review_evidence, wiring_closure

    if package.store is not store or package.phase_id != "build":
        raise ValueError("Build requires its own sealed Plan package")
    planned = package.read("plan-task")
    manifest = package.read("seam-manifest")
    if "plan" in planned:
        _validated_phase_contribution_plan(
            package,
            {
                "design_required": True,
                "run_id": package.run_id,
                "design_fingerprint": manifest["binding"]["design_fingerprint"],
            },
        )
    for name, value in planned.get("dependency_outputs", {}).items():
        if package.read(name) != value:
            raise ValueError("Plan dependency output differs from sealed producer bytes")
    if set(planned.get("dependency_outputs", {})) != {
        "source-coverage",
        "decomposition",
        "seam-manifest",
    }:
        raise ValueError("Plan dependency producer outputs missing")
    decomposition = package.read("decomposition")
    coverage = graph_decomposition.require_complete_source_coverage(
        package.read("source-coverage"), source_tree=decomposition["source_tree"]
    )
    if (
        decomposition["coverage_fingerprint"] != coverage["fingerprint"]
        or manifest["decomposition_fingerprint"] != decomposition["fingerprint"]
        or manifest["binding"]["graph_fingerprint"] != decomposition["fingerprint"]
        or manifest["binding"]["source_tree"] != coverage["source_tree"]
        or manifest["binding"]["requirement_fingerprint"]
        != review_evidence.content_fingerprint(package.read("requirement"))
    ):
        raise ValueError("Build dependency provenance is stale")
    topology = plan_topology.expected_dependency_topology(
        decomposition, planned.get("plan"), package.read("design").get("seam_contracts", [])
    )
    expected = wiring_closure.build_seam_manifest(
        decomposition,
        binding=manifest["binding"],
        contracts=package.read("design").get("seam_contracts", []),
        expected=topology,
    )
    if manifest != expected:
        raise ValueError("Build seam manifest differs from dependency-derived Plan")
    if (
        manifest["binding"]["candidate_fingerprint"] != package.candidate_fingerprint
        or manifest["binding"]["run_id"] != package.run_id
        or manifest["binding"]["plan_fingerprint"]
        != review_evidence.content_fingerprint(planned.get("plan", planned.get("task")))
    ):
        raise ValueError("Build seam binding is stale")
    graph = plan_topology._depgraph.scan(workspace, decompose=True)
    realized = plan_topology.dependency_plan_projection(graph, planned.get("plan"))
    task_scope = (
        plan_topology.task_conformance_scope(topology, task_id)
        if task_id is not None and topology is not None
        else None
    )
    return wiring_closure.realized_seam_conformance(manifest, realized, task_scope=task_scope)


def produce_spec_phase_candidates(store, definition, authored, *, package, state, workspace):
    """Shared production boundary for native collection and local adapter tests."""
    result = _copy_json(authored)
    if definition["id"] == "plan":
        planned = seal_phase_plan_task(
            store, package, state, authored["plan-task"], workspace=workspace
        )
        result = {"plan-task": planned, **planned["dependency_outputs"]}
    elif definition["id"] == "build":
        current = stage_loop.task_phase_state(sys.modules[__name__], workspace, state)
        task = _current_task(current)
        if not task and "plan" in package.read("plan-task"):
            raise ValueError("Build conformance requires its exact current task")
        result["realized-conformance"] = seal_phase_build_conformance(
            store, package, workspace, task_id=task["id"] if task else None
        )
    return result


def _test_strategy_plan_contract(task: Mapping[str, object]) -> dict:
    """Project only the immutable Plan fields that authorize Build tests."""
    return {
        key: _copy_json(task.get(key))
        for key in (
            "id",
            "tests",
            "criteria",
            "acceptance_refs",
            "test_contract",
            "test_strategy_authority",
        )
    }


def _seal_task_test_strategy_authority(
    ws: str,
    state: Mapping[str, object],
    task: Mapping[str, object],
    *,
    design_package: PhasePackage | None = None,
) -> dict | None:
    """Derive one Design+Plan authority; Build can never mint this record."""
    if not state.get("design_required"):
        raise ValueError("Plan requires current Design authority")
    if not isinstance(task.get("test_contract"), Mapping):
        return None
    if design_package is None:
        context = _phase_bridge_context(ws, state)
        if context is None:
            raise ValueError("test strategy requires the current phase input package")
        design_package = phase_harness.input_package(sys.modules[__name__], context)
    design = design_package.read("design")
    design_settings = design.get("test_strategy")
    design_reference = (
        design_settings.get("authority") if isinstance(design_settings, Mapping) else None
    )
    if "test_strategy_reference" in design:
        if design_reference is not None and design_reference != design["test_strategy_reference"]:
            raise ValueError("sealed Design strategy references conflict")
        design_reference = design["test_strategy_reference"]
    strategy = test_strategy.validate_strategy(design_package.read("test-strategy"))
    if (
        isinstance(design_settings, Mapping)
        and design_settings.get("schema") == test_strategy.SCHEMA
    ):
        # Inline Design carries the strategy itself. Its separately sealed
        # artifact must be exactly the same approved strategy before the
        # incumbent Plan receipt producer can use the canonical output path.
        if test_strategy.validate_strategy(design_settings) != strategy:
            raise ValueError("embedded Design strategy differs from its sealed artifact")
        if design_reference is None:
            design_reference = {
                "schema": _DESIGN_TEST_STRATEGY_REFERENCE_SCHEMA,
                "path": "design/test-strategy.json",
                "strategy_fingerprint": strategy["contract_fingerprint_sha256"],
            }
    plan_reference = task.get("test_strategy_authority")
    if (
        not isinstance(design_reference, Mapping)
        or set(design_reference) != _DESIGN_STRATEGY_REFERENCE_FIELDS
        or design_reference.get("schema") != _DESIGN_TEST_STRATEGY_REFERENCE_SCHEMA
    ):
        raise ValueError("approved Design test-strategy reference is missing or invalid")
    if (
        not isinstance(plan_reference, Mapping)
        or set(plan_reference) != _PLAN_STRATEGY_REFERENCE_FIELDS
        or plan_reference.get("schema") != _PLAN_TEST_STRATEGY_REFERENCE_SCHEMA
    ):
        raise ValueError("approved Plan test-strategy reference is missing or invalid")
    if any(
        plan_reference.get(field) != design_reference.get(field)
        for field in ("path", "strategy_fingerprint")
    ):
        raise ValueError("Plan test strategy differs from the approved Design artifact")
    rel = design_reference["path"]
    if strategy["contract_fingerprint_sha256"] != design_reference["strategy_fingerprint"]:
        raise ValueError("sealed Design strategy fingerprint differs")
    if design_package.run_id != state.get("run_id"):
        raise ValueError("sealed Design package belongs to another run")
    criterion_ids = _strategy_authority_strings(
        plan_reference.get("criterion_ids"), "Plan test-strategy criterion_ids"
    )
    producer_ids = _strategy_authority_strings(
        plan_reference.get("changed_producer_ids"), "Plan test-strategy changed_producer_ids"
    )
    criteria = {
        str(row.get("id")): row
        for row in strategy.get("acceptance_criteria") or []
        if isinstance(row, Mapping)
    }
    producers = {
        str(row.get("id")): row
        for row in strategy.get("producers") or []
        if isinstance(row, Mapping)
    }
    missing_criteria = sorted(set(criterion_ids) - set(criteria))
    missing_producers = sorted(set(producer_ids) - set(producers))
    if missing_criteria or missing_producers:
        raise ValueError(
            "Plan test-strategy selection is absent from the approved "
            f"artifact: criteria={missing_criteria}, producers={missing_producers}"
        )
    selected_selectors = [
        selector
        for criterion_id in criterion_ids
        for selector in criteria[criterion_id]["selectors"]
    ]
    if len(selected_selectors) != len(set(selected_selectors)):
        raise ValueError("Plan test-strategy selection contains overlapping selectors")
    design_map = _dc.acceptance_test_map(design)
    if not isinstance(design_map, Mapping) or not design_map:
        raise ValueError("approved Design exact selector map is unavailable")
    design_selectors = [selector for selectors in design_map.values() for selector in selectors]
    outside_design = sorted(set(selected_selectors) - set(design_selectors))
    if outside_design:
        raise ValueError(
            "Plan test strategy selects tests outside approved Design: " + ", ".join(outside_design)
        )
    refs = task.get("acceptance_refs")
    if refs is not None:
        accepted_refs = _strategy_authority_strings(refs, "Plan task acceptance_refs")
        missing_refs = sorted(set(accepted_refs) - set(design_map))
        if missing_refs:
            raise ValueError(
                "Plan task acceptance refs are absent from approved Design: "
                + "; ".join(missing_refs)
            )
        referenced_selectors = [
            selector for criterion in accepted_refs for selector in design_map[criterion]
        ]
        outside_refs = sorted(set(selected_selectors) - set(referenced_selectors))
        uncovered_refs = [
            criterion
            for criterion in accepted_refs
            if not set(design_map[criterion]).intersection(selected_selectors)
        ]
        if outside_refs or uncovered_refs:
            raise ValueError(
                "Plan test-strategy selection is outside or does not cover "
                "its exact Design acceptance refs: "
                f"outside={outside_refs}, uncovered={uncovered_refs}"
            )
    design_fingerprint = str(state.get("design_fingerprint") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", design_fingerprint):
        raise ValueError("approved Design fingerprint is missing or invalid")
    material = {
        "schema": _TEST_STRATEGY_AUTHORITY_SCHEMA,
        "task": str(task.get("id") or ""),
        "design_fingerprint": design_fingerprint,
        "design_selectors_fingerprint": hashlib.sha256(
            tp.canonical_json_bytes(design_map)
        ).hexdigest(),
        "plan_contract_fingerprint": hashlib.sha256(
            tp.canonical_json_bytes(_test_strategy_plan_contract(task))
        ).hexdigest(),
        "artifact": {
            "path": rel,
            "strategy_fingerprint": strategy["contract_fingerprint_sha256"],
        },
        "selection": {
            "criterion_ids": criterion_ids,
            "selectors": selected_selectors,
            "changed_producer_ids": producer_ids,
            "producers": [copy.deepcopy(producers[key]) for key in producer_ids],
        },
    }
    if design_package is not None:
        material["package_binding"] = {
            "run_id": design_package.run_id,
            "candidate_fingerprint": design_package.candidate_fingerprint,
            "authority_fingerprint": design_package.authority_fingerprint,
            "definition_set_fingerprint": design_package.registry.definition_set_fingerprint,
            "artifacts": [
                artifact.projection()
                for artifact in design_package.artifacts
                if artifact.artifact_class in {"design", "test-strategy"}
            ],
        }
    return {
        **material,
        "fingerprint": hashlib.sha256(tp.canonical_json_bytes(material)).hexdigest(),
    }


def _validated_task_test_strategy_authority(
    ws: str,
    state: Mapping[str, object],
    task: Mapping[str, object],
    *,
    design_package: PhasePackage | None = None,
) -> dict | None:
    expected = _seal_task_test_strategy_authority(ws, state, task, design_package=design_package)
    if expected is None:
        return None
    recorded = task.get("test_strategy_authority_receipt")
    if recorded != expected:
        raise ValueError("approved Design/Plan test-strategy authority is missing or stale")
    return expected


def _task_submission_authority_required(task: Mapping[str, object] | None) -> bool:
    """Design-authored test contracts retain exact worker submission authority."""
    return isinstance((task or {}).get("test_contract"), Mapping)


def _evaluation_failure_routing(ws: str, state: dict, task: dict) -> tuple[list, dict, dict]:
    """Admit one classified evaluator inventory before any correction."""
    del state
    verdict, errors = _read_json(runtime_storage.evaluation_path(ws))
    if errors:
        return errors, verdict, {}
    try:
        evaluation_output.validate_evaluator_value(verdict)
        records = failure_routing.validate_failure_records(
            verdict.get("failures") or [], expected_candidate=_failure_candidate_identity(ws, task)
        )
        if any(record.get("stage") not in {"build", "execute", "evaluate"} for record in records):
            raise failure_routing.FailureRoutingError(
                "failure_stage", "delivery correction only accepts Build or Evaluate failures"
            )
        decision = failure_routing.route_failure_records(records)
        decision["fingerprint"] = hashlib.sha256(
            json.dumps(
                decision, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
        ).hexdigest()
    except (evaluation_output.OutputValidationError, failure_routing.FailureRoutingError) as exc:
        code = getattr(exc, "code", "failure_admission")
        errors.append(f"failure classification is invalid ({code}): {exc}")
        return errors, verdict, {}
    if verdict.get("task") != task.get("id"):
        errors.append(
            f"failure inventory is for task {verdict.get('task')!r}, expected {task.get('id')!r}"
        )
    availability = verdict.get("evaluation") or {}
    if availability.get("status") != "complete" or availability.get("reason_code") != "none":
        errors.append("ordinary failure routing requires a completed judgment")
    if verdict.get("verdict") != "fail":
        errors.append("failure routing requires evaluator verdict 'fail'")
    return errors, verdict, decision


# One canonical severity vocabulary (v2.3.0). Producers disagree — the lens
# brief says high|med|low, the lens catalog's verdict schema says
# blocker|major|minor|question|praise, free-form reviews say critical —
# so every CONSUMER normalizes through this map. Enforcement rule: unknown
# or foreign severities map UP to 'high'; a finding a gate cannot classify
# must BLOCK, never pass or render as medium (fail closed).
SEVERITY_CANONICAL = ("high", "med", "low", "info")
_SEVERITY_MAP = {
    "high": "high",
    "critical": "high",
    "blocker": "high",
    "major": "high",
    "sev1": "high",
    "p0": "high",
    "p1": "high",
    "med": "med",
    "medium": "med",
    "moderate": "med",
    "low": "low",
    "minor": "low",
    "trivial": "low",
    "info": "info",
    "question": "info",
    "praise": "info",
    "note": "info",
    "nit": "info",
}


def normalize_severity(value) -> str:
    """Map any producer's severity onto the canonical enum — UNKNOWN maps UP
    to 'high' so an unclassifiable finding blocks rather than slips through.
    Shared consumption point for the EM gate and the dashboard renderer."""
    return _SEVERITY_MAP.get(str(value or "").strip().lower(), "high")


# Review discipline (v2.3.1). A finding's CLASS decides whether it gates a
# change, orthogonally to how bad it is. This is what stops a whole-tree
# 26-lens sweep (which always yields ~100 observations) from reading as "100
# blockers": only a regression, or a NEW high defect in the change's own diff,
# blocks — pre-existing debt and taste are surfaced but never block the change.
_CLASS_MAP = {
    "regression": "regression",
    "regressed": "regression",
    "pre-existing": "pre-existing",
    "preexisting": "pre-existing",
    "pre_existing": "pre-existing",
    "existing": "pre-existing",
    "debt": "pre-existing",
    "observation": "observation",
    "taste": "observation",
    "style": "observation",
    "nit": "observation",
    "opinion": "observation",
    "suggestion": "observation",
    "enhancement": "observation",
}


def normalize_finding_class(value) -> str:
    """Canonical finding class, or 'unclassified' when absent/foreign.

    Unlike severity, an unknown class maps to 'unclassified' (NOT up to
    'regression') — taste must never be inflated to a blocker. But an absent
    class does NOT let a high slip through either: `finding_blocks` routes an
    unclassified finding through the severity rule, so you cannot hide a real
    high defect merely by omitting the class."""
    v = str(value or "").strip().lower()
    return _CLASS_MAP.get(v, "unclassified")


def _finding_in_diff(finding: dict, changed_files) -> bool:
    if changed_files is None:
        return True  # no diff context → cannot exclude
    f = str(finding.get("file") or "").replace("\\", "/")
    return f in {str(c).replace("\\", "/") for c in changed_files}


def finding_blocks(finding: dict, changed_files=None) -> bool:
    """Does this finding block THIS change's gate?

    regression                     -> always blocks
    pre-existing / observation     -> never blocks (surfaced, tracked)
    unclassified + high + in-diff  -> blocks (a new high defect in the
                                      change's own surface — fail closed)
    unclassified + high + no diff  -> blocks (cannot prove it's old)
    anything else                  -> does not block
    """
    cls = normalize_finding_class(finding.get("class"))
    if cls == "regression":
        return True
    if cls in ("pre-existing", "observation"):
        return False
    # unclassified: fall back to the severity rule (danger fails closed)
    if normalize_severity(finding.get("severity")) != "high":
        return False
    return _finding_in_diff(finding, changed_files)


def classify_findings(findings, changed_files=None) -> dict:
    """Split a findings list into the blocker set and the triage buckets, so a
    review headline reads '7 block · 93 to triage' instead of '100 issues'."""
    out = {
        "blockers": [],
        "regressions": [],
        "pre_existing": [],
        "observations": [],
        "unclassified": [],
    }
    for f in findings or []:
        cls = normalize_finding_class(f.get("class"))
        if cls == "regression":
            out["regressions"].append(f)
        elif cls == "pre-existing":
            out["pre_existing"].append(f)
        elif cls == "observation":
            out["observations"].append(f)
        else:
            out["unclassified"].append(f)
        if finding_blocks(f, changed_files):
            out["blockers"].append(f)
    return out


# Evidence proposals are revalidated against the current phase authority.
from evidence import EVIDENCE_JUDGMENT_KEYS, evidence  # noqa: E402,F401

# Audit mechanics belong to audit.py; gate policy remains in this owner.
from audit import (  # noqa: E402,F401 — re-exports, not dead imports
    AUDIT_FILE,
    _audit_brief,
    _audit_path,
    _is_machinery_warn_row,
    _is_router_regression,
    _release_review_flagged,
    _blocking_claim_errors,
    _router_audit_gate,
    _router_regression_key,
    _routing_decision_from_meta,
    _routing_decision_of,
    _unresolved_high_errors,
    audit_counter,
    audit_due,
    audit_every,
    record_audit_review,
    router_audit,
)


def _coverage_disposition(v) -> str:
    """Read a current structured coverage disposition."""
    return str(v.get("verdict") or "") if isinstance(v, dict) else ""


def _engineering_review_errors(*args, **kwargs):
    return gates._engineering_review_errors(sys.modules[__name__], *args, **kwargs)


def _submission_evidence_engine_workspace(
    ws: str, state: dict, task: dict | None, act_ws: str
) -> str:
    """Choose the engine tree that produced task evaluation evidence.

    A parallel evaluator keeps reading the claimed worktree so its source and
    evidence fingerprints remain task-scoped.  Once that exact task tip is
    contained in the primary checkout, however, a merge-and-resubmit is
    produced under the primary engine that now owns validation.  Continuing
    to stamp the surviving pre-merge worktree would make the documented
    engine-skew remedy impossible until worktree cleanup.

    Fail closed toward the worktree unless both identities are exact: the
    worktree must still be at the recorded task target and that target must be
    an ancestor of primary HEAD.  Unmerged, advanced, detached, or otherwise
    ambiguous worktrees therefore retain their independent engine stamp.
    """
    if state.get("step") != "evaluate" or act_ws == ws or not state.get("parallel") or not task:
        return act_ws
    target = str(task.get("target_commit") or "").strip()
    if len(target) not in (40, 64) or any(
        character not in "0123456789abcdef" for character in target
    ):
        return act_ws
    try:
        if tp.git_head(act_ws) != target:
            return act_ws
        contained = tp._run(["git", "merge-base", "--is-ancestor", target, "HEAD"], cwd=ws)
    except Exception:
        return act_ws
    return ws if contained.returncode == 0 else act_ws


def _run_submit_checkpoint(*args, **kwargs):
    return gates._run_submit_checkpoint(sys.modules[__name__], *args, **kwargs)


def _task_submission_authority(*args, **kwargs):
    return gates._task_submission_authority(sys.modules[__name__], *args, **kwargs)


def _task_submission_authority_error(*args, **kwargs):
    return gates._task_submission_authority_error(sys.modules[__name__], *args, **kwargs)


@run_context.operation
@loop_status.with_dashboard
def submit(*args, **kwargs):
    return gates.submit(sys.modules[__name__], *args, **kwargs)


def _submission_staleness(*args, **kwargs):
    return gates._submission_staleness(sys.modules[__name__], *args, **kwargs)


def collect_submission_observation(ws: str, *, slot: str) -> dict:
    return gates.collect_submission_observation(sys.modules[__name__], ws, slot=slot)


def _producer_observation_errors(*args, **kwargs):
    return gates._producer_observation_errors(sys.modules[__name__], *args, **kwargs)


def _stage_loop_gate_completion(*args, **kwargs):
    return stage_loop._stage_loop_gate_completion(sys.modules[__name__], *args, **kwargs)


@run_context.operation
@loop_status.with_dashboard
def gate(*args, **kwargs):
    return gates.gate(sys.modules[__name__], *args, **kwargs)


def _compute_signoff_dod(ws, state, **kwargs):
    review = dict(state, baseline=_review_baseline(ws, state, "signoff"))
    return gates._compute_signoff_dod(sys.modules[__name__], ws, review, **kwargs)


def _signoff_evidence_binding(ws, state, **kwargs):
    review = dict(state, baseline=_review_baseline(ws, state, "signoff"))
    return gates._signoff_evidence_binding(sys.modules[__name__], ws, review, **kwargs)


def _signoff_dod(*args, **kwargs):
    return gates._signoff_dod(sys.modules[__name__], *args, **kwargs)


def _signoff_gate_dod(*args, **kwargs):
    return gates._signoff_gate_dod(sys.modules[__name__], *args, **kwargs)


def _seal_terminal_metrics_before_retro(ws: str, state: dict) -> dict:
    """Set measured or explicit attributable-unavailable terminal truth."""
    from taskplane import review_evidence

    root_state = state.get("root_hygiene")
    if (
        state.get("step") != "signoff"
        and isinstance(root_state, Mapping)
        and root_state.get("status") in {"open", "admissions_closed"}
    ):
        ledger_for_root = state.get("dispatch_telemetry") or {}
        worker_tokens = sum(
            int((row.get("usage") or {}).get("total_tokens") or 0)
            for row in ledger_for_root.get("bindings") or []
            if isinstance(row, Mapping)
            and row.get("thread_type") != "main"
            and isinstance(row.get("usage"), Mapping)
        )
        # Artifact ownership is stable across Design/Build baselines. Keep
        # the seed identity intact and seal under the artifact's candidate.
        root_binding = run_artifacts.validate_binding(state.get("run_artifact_binding"))
        root_receipt = wave_metrics.finalize_root_hygiene_canary(
            root_state,
            candidate_sha=str(root_binding["candidate"].get("revision") or ""),
            worker_tokens=worker_tokens,
        )
        existing_root = state.get("root_hygiene_receipt")
        if existing_root is not None and existing_root != root_receipt:
            raise wave_metrics.WaveMetricsError(
                "canonical root hygiene receipt changed during terminal seal"
            )
        state["root_hygiene_receipt"] = root_receipt
        artifact_root = _run_artifact_root(ws, state)
        retained = wave_metrics.publish_root_hygiene(artifact_root, root_receipt)
        state.setdefault("run_artifact_refs", {})["root_hygiene"] = retained
    existing = state.get("wave_metrics_receipt")
    if isinstance(existing, Mapping):
        state["wave_metrics_receipt"] = wave_metrics.validate_wave_receipt(existing)
        state.pop("wave_metrics_unavailable", None)
        return {
            "status": (
                "partial"
                if wave_metrics.token_usage_projection(state["wave_metrics_receipt"])["status"]
                == "partial"
                else "measured"
            ),
            "fingerprint": state["wave_metrics_receipt"]["fingerprint"],
        }
    binding = state.get("run_artifact_binding") or {}
    candidate = str((binding.get("candidate") or {}).get("fingerprint") or "")
    evidence = state.get("wave_metrics_evidence")
    ledger = state.get("dispatch_telemetry")
    upper_bound = state.get("wave_metrics_archive_upper_bound_tokens")
    unavailable_schema = "taskplane.terminal-wave-metrics-unavailable-evidence/v1"
    if isinstance(evidence, Mapping) and evidence.get("schema") == unavailable_schema:
        unavailable = {
            "schema": "taskplane.wave-metrics-unavailable/v1",
            "candidate_fingerprint": evidence.get("candidate_fingerprint"),
            "reason": evidence.get("reason"),
            "attempts": list(evidence.get("attempts") or []),
        }
        unavailable["fingerprint"] = hashlib.sha256(
            json.dumps(
                unavailable,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        state["wave_metrics_unavailable"] = unavailable
        state.pop("wave_metrics_receipt", None)
        return {
            "status": "unavailable",
            "fingerprint": unavailable["fingerprint"],
            "reason": unavailable["reason"],
        }
    try:
        if not candidate:
            raise wave_metrics.WaveMetricsError("terminal metrics candidate binding is unavailable")
        if not isinstance(ledger, Mapping):
            raise wave_metrics.WaveMetricsError("terminal dispatch ledger is unavailable")
        for binding_row in ledger.get("bindings") or []:
            if (
                not isinstance(binding_row, Mapping)
                or binding_row.get("thread_type") != "main"
                or binding_row.get("finalized_receipt_fingerprint")
            ):
                continue
            dispatch_telemetry.finalize_usage(
                ledger,
                dispatch_id=str(binding_row["dispatch_id"]),
                ended_at=SystemClock().wall_time(),
                clock=SystemClock(),
                events=[{"kind": "complete", "sequence": 1}],
            )
        run_id = str(state.get("run_id") or "").strip()
        if not run_id:
            raise wave_metrics.WaveMetricsError(
                "terminal dispatch intent census has no run identity"
            )
        census = tp.dispatch_intent_census(ws, run_id)
        if census["truncated"]:
            raise wave_metrics.WaveMetricsError("terminal dispatch intent census is truncated")
        if census["duplicate_intent_ids"]:
            raise wave_metrics.WaveMetricsError(
                "terminal dispatch intent census contains duplicate intents"
            )
        # Permission-hook matching is not a native start observation. The
        # complete intent census must match the authenticated usage ledger;
        # its terminal producer below rejects missing Start/Stop evidence.
        expected_intents = set(census["intent_ids"])
        observed_intents = {
            str(row.get("dispatch_id") or "")
            for row in ledger.get("bindings") or []
            if isinstance(row, Mapping) and row.get("thread_type") != "main"
        }
        if not expected_intents or expected_intents != observed_intents:
            missing = sorted(expected_intents - observed_intents)
            unexpected = sorted(observed_intents - expected_intents)
            raise wave_metrics.WaveMetricsError(
                "terminal dispatch intent census does not match the usage "
                f"ledger (missing={missing}, unexpected={unexpected})"
            )
        if upper_bound is not None and (
            isinstance(upper_bound, bool) or not isinstance(upper_bound, int) or upper_bound < 0
        ):
            raise wave_metrics.WaveMetricsError("terminal archive upper bound is invalid")
        if not isinstance(evidence, Mapping):
            evidence = wave_metrics.produce_terminal_evidence(
                dispatch_ledger=ledger,
                clock=SystemClock(),
                candidate_fingerprint=candidate,
                evaluator_summary=retro_engine.evaluator_summary(list(state.get("tasks") or [])),
                settings_digest=str(
                    state.get("settings_digest") or binding.get("settings_digest") or ""
                ),
                archive_upper_bound_tokens=upper_bound,
                billing_total_tokens=state.get("wave_metrics_billing_total_tokens"),
            )
            state["wave_metrics_evidence"] = evidence
        receipt = wave_metrics.seal_terminal_metrics(
            evidence,
            dispatch_ledger=ledger,
            clock=SystemClock(),
            candidate_fingerprint=candidate,
            archive_upper_bound_tokens=upper_bound,
            billing_total_tokens=state.get("wave_metrics_billing_total_tokens"),
        )
    except (wave_metrics.WaveMetricsError, dispatch_telemetry.DispatchTelemetryError) as exc:
        attempts = []
        if isinstance(ledger, Mapping):
            try:
                attempts = dispatch_telemetry.terminal_attempt_attribution(ledger)
            except dispatch_telemetry.DispatchTelemetryError:
                attempts = []
        reason = f"{exc.__class__.__name__}: {exc}"
        evaluators = retro_engine.evaluator_summary(list(state.get("tasks") or []))
        unavailable_evidence = {
            "schema": unavailable_schema,
            "candidate_fingerprint": candidate or None,
            "reason": reason[:1024],
            "attempts": attempts,
            "evaluator_summary": evaluators,
        }
        unavailable_evidence["fingerprint"] = hashlib.sha256(
            json.dumps(
                unavailable_evidence,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        state["wave_metrics_evidence"] = unavailable_evidence
        unavailable = {
            "schema": "taskplane.wave-metrics-unavailable/v1",
            "candidate_fingerprint": candidate or None,
            "reason": reason[:1024],
            "attempts": attempts,
        }
        unavailable["fingerprint"] = hashlib.sha256(
            json.dumps(
                unavailable,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        state["wave_metrics_unavailable"] = unavailable
        state.pop("wave_metrics_receipt", None)
        return {
            "status": "unavailable",
            "fingerprint": unavailable["fingerprint"],
            "reason": unavailable["reason"],
        }
    state["wave_metrics_receipt"] = receipt
    state["wave_metrics_ledger"] = review_evidence.ArtifactStore(ws).put("terminal-ledger", ledger)
    state.pop("wave_metrics_unavailable", None)
    return {
        "status": (
            "partial"
            if wave_metrics.token_usage_projection(receipt)["status"] == "partial"
            else "measured"
        ),
        "fingerprint": receipt["fingerprint"],
    }


def _finalize_owned_run_cleanup(ws: str, state: Mapping[str, object], *, outcome: str) -> dict:
    """Seal terminal evidence, clean exact-owned resources, prove no leaks."""
    path = str(state.get("owned_cleanup_manifest") or "")
    artifact_binding = state.get("run_artifact_binding")
    if not path and isinstance(artifact_binding, Mapping):
        path = _ensure_owned_cleanup_manifest(ws, _run_artifact_root(ws, state), artifact_binding)
    if not path:
        raise owned_cleanup.OwnedCleanupError("governed terminal cleanup manifest is unavailable")
    manifest = owned_cleanup.load_manifest(path)
    if manifest.get("terminal") is not None:
        return owned_cleanup.cleanup_manifest(path)
    artifact_root = _run_artifact_root(ws, state)
    artifact_manifest = run_artifacts.load_manifest(artifact_root)
    source_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "binding": artifact_manifest["binding"]["fingerprint"],
                "terminal_artifacts": state.get("terminal_artifacts"),
                "outcome": outcome,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    evidence_dir = os.path.join(os.path.dirname(path), "terminal-input")
    os.makedirs(evidence_dir, exist_ok=True)
    terminal_path = os.path.join(evidence_dir, f"terminal-{source_fingerprint}.json")
    publication_path = os.path.join(evidence_dir, f"publication-{source_fingerprint}.json")
    terminal_value = {
        "schema": "taskplane.governed-terminal-evidence/v1",
        "run_id": state.get("run_id"),
        "outcome": outcome,
        "artifact_binding_fingerprint": artifact_manifest["binding"]["fingerprint"],
        "terminal_artifacts": state.get("terminal_artifacts"),
        "terminal_metrics": state.get("terminal_metrics"),
    }
    if not os.path.exists(terminal_path):
        tp.atomic_write_json(terminal_path, terminal_value, sort_keys=True)
    elif tp.load_json(terminal_path, what="terminal cleanup evidence") != terminal_value:
        raise owned_cleanup.OwnedCleanupError("terminal cleanup evidence conflicts")
    owner = manifest["owner"]
    owned_cleanup.write_publication_replay(
        publication_path,
        owner=owner,
        outcome=outcome,
        source_revision=max(1, int(artifact_manifest.get("revision") or 1)),
        source_fingerprint=source_fingerprint,
        trigger=("handoff" if outcome == "handoff" else "terminal"),
    )
    receipt = owned_cleanup.seal_and_cleanup(
        path,
        outcome=outcome,
        evidence={"terminal": terminal_path, "publication-replay": publication_path},
    )
    if (
        receipt.get("cleanup_status") != "clean"
        or receipt.get("leak_count") != 0
        or not (receipt.get("artifact_verification") or {}).get("readable")
    ):
        raise owned_cleanup.OwnedCleanupError(
            "owned cleanup did not prove a clean readable terminal run"
        )
    return receipt


_WHOLE_RUN_TERMINAL_OUTCOMES = frozenset(
    {
        "cancellation",
        "interruption",
        "handoff",
    }
)
_RUN_CONTROL_STATE_FIELDS = (
    "run_start_step",
    "run_stage_instance_id",
    "run_candidate_fingerprint",
    "run_artifacts",
    "run_artifact_binding",
    "owned_cleanup_manifest",
)


def _terminal_run_artifact_binding(
    ws: str, state: Mapping[str, object], authority: Mapping[str, object]
) -> dict:
    present = [field for field in _RUN_CONTROL_STATE_FIELDS if field in state]
    if len(present) != len(_RUN_CONTROL_STATE_FIELDS):
        raise ValueError("whole-run control-plane state is partial or ambiguous")
    binding = run_artifacts.validate_binding(state.get("run_artifact_binding"))
    if (
        state.get("run_artifacts") != run_artifacts.manifest_locator_reference()
        or state.get("run_stage_instance_id") != binding.get("stage_instance_id")
        or state.get("run_candidate_fingerprint")
        != (binding.get("candidate") or {}).get("fingerprint")
        or not str(state.get("run_start_step") or "").strip()
        or not str(state.get("owned_cleanup_manifest") or "").strip()
    ):
        raise ValueError("whole-run control-plane binding is inconsistent")
    return binding


def _whole_run_terminal_paths(ws: str, state: Mapping[str, object]) -> tuple[str, str]:
    run_root = os.path.dirname(_run_artifact_root(ws, state))
    root = os.path.join(run_root, "terminal")
    return (
        os.path.join(root, "whole-run-intent.json"),
        os.path.join(root, "whole-run-receipt.json"),
    )


def _terminal_fingerprint(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def _whole_run_terminal_authority(state: Mapping[str, object], *, by: str) -> dict:
    """Derive host-session authority; a worker cannot attest its own stop."""
    try:
        slot = tp.task_slot()
    except Exception as exc:
        raise ValueError(f"whole-run terminal authority has an invalid worker slot: {exc}") from exc
    if slot is not None:
        raise ValueError(
            "whole-run terminal authority is orchestrator-only; worker self-attestation is refused"
        )
    actor = str(by or "").strip()
    if not actor or _STAGE_ACTOR_IDENTIFIER.fullmatch(actor) is None:
        raise ValueError("whole-run terminal authority requires an attributable --by identifier")
    session_id = str(
        os.environ.get("TASKPLANE_SESSION_ID")
        or os.environ.get("CODEX_THREAD_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or ""
    ).strip()
    if (
        not session_id
        or len(session_id.encode("utf-8")) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in session_id)
    ):
        raise ValueError("whole-run terminal authority requires an attributable host session")
    root = state.get("_stage_native_root_authority")
    if isinstance(root, Mapping) and root.get("actor") != actor:
        raise ValueError("whole-run terminal authority does not match the run root")
    host = (
        "codex"
        if os.environ.get("CODEX_THREAD_ID")
        else "claude"
        if os.environ.get("CLAUDE_SESSION_ID")
        else "taskplane-host"
    )
    authority = {
        "schema": "taskplane.whole-run-terminal-authority/v1",
        "kind": "orchestrator-host-session",
        "host": host,
        "session_id": session_id,
        "actor": actor,
        "run_id": str(state.get("run_id") or ""),
    }
    return {**authority, "fingerprint": _terminal_fingerprint(authority)}


def _validate_whole_run_terminal_intent(
    ws: str, state: Mapping[str, object], intent: object
) -> dict:
    required = {
        "schema",
        "run_id",
        "outcome",
        "candidate_fingerprint",
        "artifact_binding_fingerprint",
        "workspace_revision",
        "workspace_fingerprint",
        "authority",
        "created_at_ns",
        "fingerprint",
    }
    if (
        not isinstance(intent, Mapping)
        or set(intent) != required
        or intent.get("schema") != "taskplane.whole-run-terminal-intent/v1"
        or intent.get("outcome") not in _WHOLE_RUN_TERMINAL_OUTCOMES
    ):
        raise ValueError("whole-run terminal intent is invalid")
    material = {key: value for key, value in intent.items() if key != "fingerprint"}
    if intent.get("fingerprint") != _terminal_fingerprint(material):
        raise ValueError("whole-run terminal intent fingerprint is stale")
    binding = run_artifacts.validate_binding(state.get("run_artifact_binding"))
    manifest = run_artifacts.load_manifest(_run_artifact_root(ws, state))
    candidate = str((binding.get("candidate") or {}).get("fingerprint") or "")
    if (
        manifest.get("binding") != binding
        or intent.get("run_id") != state.get("run_id")
        or intent.get("candidate_fingerprint") != candidate
        or intent.get("artifact_binding_fingerprint") != binding.get("fingerprint")
        or intent.get("workspace_revision") != tp.git_head(ws)
        or intent.get("workspace_fingerprint")
        != tp.workspace_fingerprint(ws, str(state.get("baseline") or ""))
    ):
        raise ValueError("whole-run terminal intent is stale for the current run/candidate")
    authority = intent.get("authority")
    if (
        not isinstance(authority, Mapping)
        or authority.get("kind") != "orchestrator-host-session"
        or authority.get("run_id") != state.get("run_id")
        or authority.get("fingerprint")
        != _terminal_fingerprint(
            {key: value for key, value in authority.items() if key != "fingerprint"}
        )
    ):
        raise ValueError("whole-run terminal intent authority is invalid")
    return dict(intent)


def _publish_whole_run_terminal_activity(
    ws: str, state: Mapping[str, object], intent: Mapping[str, object]
) -> dict:
    root = _run_artifact_root(ws, state)
    attempt_id = "terminal-" + str(intent["fingerprint"])[:32]
    manifest = run_artifacts.load_manifest(root)
    matches = [
        entry
        for entry in manifest["classes"]["agent-activity"]["entries"]
        if (entry.get("metadata") or {}).get("agent_attempt_id") == attempt_id
    ]
    event_type = {"cancellation": "cancel", "interruption": "interruption", "handoff": "handoff"}[
        str(intent["outcome"])
    ]
    details = {
        "outcome": intent["outcome"],
        "authority_fingerprint": intent["authority"]["fingerprint"],
        "intent_fingerprint": intent["fingerprint"],
    }
    if matches:
        if (
            len(matches) != 1
            or matches[0]["metadata"].get("event_type") != event_type
            or matches[0]["metadata"].get("details") != details
        ):
            raise run_artifacts.RunArtifactError("whole-run terminal activity replay is ambiguous")
        return matches[0]
    return run_artifacts.append_activity(
        root,
        event_type=event_type,
        agent_attempt_id=attempt_id,
        worker_id="orchestrator-" + str(intent["authority"]["fingerprint"])[:24],
        task_id="governed-run",
        lens="zero-lens-orchestrator",
        details=details,
        occurred_at_ns=int(intent["created_at_ns"]),
    )


def _whole_run_terminal_stage_predecessor(ws: str, state: Mapping[str, object]) -> dict | None:
    """Close the exact canonical stage operation before its receipt exists."""
    context = _stage_loop_context(ws, state)
    if context is None:
        return None
    stage = context.get("stage")
    if not isinstance(stage, Mapping) or stage.get("state") != "active":
        raise ValueError("whole-run terminal stage predecessor is not exactly active")
    from_step = str(state.get("step") or "")
    from_kind = _LOOP_STAGE_KINDS.get(from_step)
    if from_kind != stage.get("stage_kind"):
        raise ValueError("whole-run terminal stage predecessor kind is severed")
    try:
        if __package__:
            from . import stage_entities as stage_entities_module
        else:
            import stage_entities as stage_entities_module
    except ImportError:
        import stage_entities as stage_entities_module
    material = _stage_loop_transition_operation_material(
        state,
        run_id=str(context["run_id"]),
        from_step=from_step,
        to_step="failed",
        from_kind=from_kind,
        to_kind=None,
        terminal_outcome="closed",
        terminal_only=True,
        predecessor_stage_id=stage["stage_id"],
        predecessor_head_fingerprint=stage["fingerprint"],
    )
    predecessor = {
        "schema": "taskplane.whole-run-terminal-stage-predecessor/v1",
        "run_id": str(context["run_id"]),
        "stage_id": str(stage["stage_id"]),
        "stage_kind": str(stage["stage_kind"]),
        "head_fingerprint": str(stage["fingerprint"]),
        "loop_step": from_step,
        "operation_id": _stage_loop_identity(stage_entities_module, "loop-transition-", material),
    }
    return {**predecessor, "fingerprint": _terminal_fingerprint(predecessor)}


def _reconcile_whole_run_terminal_stage(
    ws: str, state: Mapping[str, object], predecessor: object
) -> dict | None:
    """Perform or verify the one receipt-bound canonical close operation."""
    if predecessor is None:
        return None
    fields = {
        "schema",
        "run_id",
        "stage_id",
        "stage_kind",
        "head_fingerprint",
        "loop_step",
        "operation_id",
        "fingerprint",
    }
    if (
        not isinstance(predecessor, Mapping)
        or set(predecessor) != fields
        or predecessor.get("schema") != "taskplane.whole-run-terminal-stage-predecessor/v1"
        or predecessor.get("fingerprint")
        != _terminal_fingerprint(
            {key: value for key, value in predecessor.items() if key != "fingerprint"}
        )
        or predecessor.get("run_id") != state.get("run_id")
        or _LOOP_STAGE_KINDS.get(str(predecessor.get("loop_step") or ""))
        != predecessor.get("stage_kind")
    ):
        raise ValueError("whole-run terminal stage predecessor receipt is invalid")
    locator = runtime_storage.load_workspace_locator(ws)
    if not isinstance(locator, Mapping) or locator.get("run_id") != predecessor["run_id"]:
        raise ValueError("whole-run terminal stage predecessor locator is severed")
    store = _stage_store(ws, str(predecessor["run_id"]))
    manifest = store.load(str(predecessor["run_id"]))
    operation = (manifest.get("stage_operations") or {}).get(predecessor["operation_id"])
    if operation is None:
        heads = manifest.get("stage_heads") or {}
        head = heads.get(predecessor["stage_id"])
        if not isinstance(head, Mapping):
            raise ValueError("whole-run terminal stage predecessor head is missing")
        stage = store.read_stage_object(str(predecessor["run_id"]), head["object"])
        if (
            stage.get("state") != "active"
            or stage.get("fingerprint") != predecessor["head_fingerprint"]
            or stage.get("stage_kind") != predecessor["stage_kind"]
        ):
            raise ValueError("whole-run terminal stage predecessor head changed")
        transition_state = {**dict(state), "step": predecessor["loop_step"]}
        operation = _stage_loop_transition(
            ws,
            transition_state,
            from_step=predecessor["loop_step"],
            to_step="failed",
            terminal_outcome="closed",
            terminal_only=True,
        )
    if not isinstance(operation, Mapping):
        raise ValueError("whole-run terminal canonical stage was not closed")
    checked = tp.verify_stage_receipt(
        dict(operation),
        expected_operation="terminalize",
        expected_stage_id=str(predecessor["stage_id"]),
    )
    if checked.get("operation_id") != predecessor["operation_id"]:
        raise ValueError("whole-run terminal canonical operation identity changed")
    current = store.load(str(predecessor["run_id"]))
    head = (current.get("stage_heads") or {}).get(predecessor["stage_id"])
    if not isinstance(head, Mapping) or checked.get("result", {}).get("head") != head:
        raise ValueError("whole-run terminal canonical head is stale")
    stage = store.read_stage_object(str(predecessor["run_id"]), head["object"])
    if stage.get("state") != "terminal" or stage.get("outcome") != "closed":
        raise ValueError("whole-run terminal canonical stage is not terminal/closed")
    return checked


def _retire_terminal_run_workers(ws: str, intent: Mapping[str, object]) -> list[dict]:
    """Revoke only this run's signed worker slots under its terminal authority.

    This is control-plane interruption, never a claim that a native worker
    completed successfully. Other runs and the orchestrator keep their slots.
    """
    from taskplane import review_evidence

    selected = []
    for slot, contract in tp._active_worker_contracts(ws):
        requested = contract.get("phase_runtime") or {}
        if requested.get("run_id") != intent["run_id"]:
            continue
        material = review_evidence.ArtifactStore(ws).read(requested["reference"])
        if material["contract_slot"] != slot or material["bindings"]["run_id"] != intent["run_id"]:
            raise ValueError("terminal worker has a foreign phase binding")
        lifecycle = contract["worker_lifecycle"]
        tp._verify_worker_release_action(ws, slot, lifecycle["release_action"], contract)
        if lifecycle.get("status") == "active":
            source = design_host_transport.phase_nonce_source(tp, ws, str(intent["run_id"]), existing_only=True)
            issued = source.recover(material["nonce_bindings"])
            start, terminal = source.phase_hooks(issued, material["nonce_bindings"])
            if any(lifecycle.get("owner") != {key: observed["owner"][key]
                    for key in ("session_id", "agent_id", "task_name")}
                    for observed in (start, terminal)):
                raise ValueError("stop the exact active worker before terminal cleanup")
        selected.append((slot, contract))
    releases = []
    for slot, contract in selected:
        with tp.file_lock(tp.active_contract_path(ws, slot)):
            if tp.load_json(tp.active_contract_path(ws, slot), default=None) != contract:
                raise ValueError("terminal worker changed during cleanup; reconcile its lifecycle")
            lifecycle = contract["worker_lifecycle"]
            terminal = lifecycle.get("terminal") or tp.record_worker_terminal(
                ws, slot, event=None, outcome=intent["outcome"],
                submission_status="whole-run-terminal:" + str(intent["fingerprint"]),
                authority="loop-gate",
            )
            releases.append(tp.release_worker_contract(
                ws, slot, action=lifecycle["release_action"], terminal_receipt=terminal,
            ))
    return releases


def _complete_whole_run_terminal(ws: str, intent: Mapping[str, object]) -> dict:
    """Replay one persisted exact terminal intent through existing owners."""
    state = load(ws)
    if state is None:
        raise ValueError("whole-run terminal replay has no active run")
    intent = _validate_whole_run_terminal_intent(ws, state, intent)
    _intent_path, receipt_path = _whole_run_terminal_paths(ws, state)
    if os.path.exists(receipt_path):
        receipt = tp.load_json(receipt_path, what="whole-run terminal receipt")
        material = (
            {key: value for key, value in receipt.items() if key != "fingerprint"}
            if isinstance(receipt, Mapping)
            else {}
        )
        if material.get("intent_fingerprint") != intent["fingerprint"] or receipt.get(
            "fingerprint"
        ) != _terminal_fingerprint(material):
            raise ValueError("whole-run terminal receipt conflicts")
        _retire_terminal_run_workers(ws, intent)
        transition = _reconcile_whole_run_terminal_stage(
            ws, state, receipt.get("stage_predecessor")
        )
        with mutate(ws) as locked:
            if locked is None:
                raise ValueError("whole-run terminal replay lost its run")
            locked["whole_run_terminal"] = dict(receipt)
            locked["terminal_outcome"] = intent["outcome"]
            locked["step"] = "failed"
        return {
            **dict(receipt),
            **({"stage_transition": transition} if transition is not None else {}),
        }

    # Stage-native startup is itself replay-safe and must occur only after the
    # intent has been made durable.
    _stage_bootstrap_pristine_root(ws, state)
    worker_releases = _retire_terminal_run_workers(ws, intent)
    with mutate(ws) as locked:
        if locked is None:
            raise ValueError("whole-run terminal replay lost its run")
        _validate_whole_run_terminal_intent(ws, locked, intent)
        locked["terminal_metrics"] = _seal_terminal_metrics_before_retro(ws, locked)
    state = load(ws) or state
    activity = _publish_whole_run_terminal_activity(ws, state, intent)
    terminal_artifacts = state.get("terminal_artifacts")
    if not isinstance(terminal_artifacts, Mapping):
        unavailable = state.get("wave_metrics_unavailable")
        report = {
            "schema": "taskplane.non-normal-terminal-report/v1",
            "terminal_intent": dict(intent),
            "evaluator_summary": retro_engine.evaluator_summary(list(state.get("tasks") or [])),
            **(
                {"wave_metrics_unavailable": dict(unavailable)}
                if isinstance(unavailable, Mapping)
                else {}
            ),
        }
        terminal_artifacts = retro_engine.publish_terminal_artifacts(
            _run_artifact_root(ws, state),
            wave_receipt=(
                dict(state["wave_metrics_receipt"])
                if isinstance(state.get("wave_metrics_receipt"), Mapping)
                else None
            ),
            report=report,
            lifecycle_outcome=str(intent["outcome"]),
        )
        with mutate(ws) as locked:
            if locked is None:
                raise ValueError("terminal artifact publication lost its run")
            locked["terminal_artifacts"] = dict(terminal_artifacts)
            locked.setdefault("run_artifact_refs", {}).update(
                {
                    "terminal_telemetry": terminal_artifacts["telemetry"],
                    "terminal_retro": terminal_artifacts["retro"],
                    "terminal_activity": activity,
                }
            )
    state = load(ws) or state
    cleanup = _finalize_owned_run_cleanup(ws, state, outcome=str(intent["outcome"]))
    with mutate(ws) as locked:
        if locked is None:
            raise ValueError("terminal cleanup lost its run")
        locked["terminal_cleanup"] = cleanup
        cleanup_ref = cleanup.get("durable_cleanup_artifact")
        if isinstance(cleanup_ref, Mapping):
            locked.setdefault("run_artifact_refs", {})["terminal_cleanup"] = dict(cleanup_ref)
    state = load(ws) or state
    stage_predecessor = _whole_run_terminal_stage_predecessor(ws, state)
    receipt_material = {
        "schema": "taskplane.whole-run-terminal-receipt/v1",
        "run_id": intent["run_id"],
        "outcome": intent["outcome"],
        "intent_fingerprint": intent["fingerprint"],
        "candidate_fingerprint": intent["candidate_fingerprint"],
        "artifact_binding_fingerprint": intent["artifact_binding_fingerprint"],
        "authority_fingerprint": intent["authority"]["fingerprint"],
        "terminal_metrics": state.get("terminal_metrics"),
        "terminal_artifacts": state.get("terminal_artifacts"),
        "terminal_cleanup": state.get("terminal_cleanup"),
        "worker_releases": worker_releases,
        "activity_fingerprint": activity["fingerprint"],
        "stage_predecessor": stage_predecessor,
    }
    receipt = {**receipt_material, "fingerprint": _terminal_fingerprint(receipt_material)}
    tp.atomic_write_json(receipt_path, receipt, sort_keys=True)
    transition = _reconcile_whole_run_terminal_stage(ws, state, stage_predecessor)
    with mutate(ws) as locked:
        if locked is None:
            raise ValueError("whole-run terminal transition lost its run")
        locked["whole_run_terminal"] = receipt
        locked["terminal_outcome"] = intent["outcome"]
        locked["step"] = "failed"
    return {**receipt, **({"stage_transition": transition} if transition is not None else {})}


@run_context.operation
def terminalize_run(ws: str, outcome: str, *, by: str) -> dict:
    """Public idempotent close for cancellation, interruption, or handoff."""
    if outcome not in _WHOLE_RUN_TERMINAL_OUTCOMES:
        return {
            "error": "whole-run terminal outcome must be cancellation, interruption, or handoff"
        }
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    if state.get("step") in TERMINAL_STEPS and not state.get("whole_run_terminal"):
        return {
            "error": "normal success/failure remains Retro-governed and "
            "cannot use the non-normal terminal operation",
            "step": state.get("step"),
        }
    try:
        authority = _whole_run_terminal_authority(state, by=by)
        binding = _terminal_run_artifact_binding(ws, state, authority)
        state = load(ws)
        if state is None:
            raise ValueError("whole-run terminal preparation lost its active run")
        intent_material = {
            "schema": "taskplane.whole-run-terminal-intent/v1",
            "run_id": state["run_id"],
            "outcome": outcome,
            "candidate_fingerprint": binding["candidate"]["fingerprint"],
            "artifact_binding_fingerprint": binding["fingerprint"],
            "workspace_revision": tp.git_head(ws),
            "workspace_fingerprint": tp.workspace_fingerprint(ws, str(state.get("baseline") or "")),
            "authority": authority,
            "created_at_ns": time.time_ns(),
        }
        intent_path, _receipt_path = _whole_run_terminal_paths(ws, state)
        if os.path.exists(intent_path):
            persisted = tp.load_json(intent_path, what="whole-run terminal intent")
            # created_at is intentionally replayed from the first persisted
            # request; all other requested authority and candidate fields must
            # remain exact.
            intent_material["created_at_ns"] = (
                persisted.get("created_at_ns") if isinstance(persisted, Mapping) else None
            )
        intent = {**intent_material, "fingerprint": _terminal_fingerprint(intent_material)}
        if os.path.exists(intent_path):
            if persisted != intent:
                raise ValueError("another whole-run terminal intent is active")
        else:
            tp.atomic_write_json(intent_path, intent, sort_keys=True)
        return {**_complete_whole_run_terminal(ws, intent), "terminalized": True}
    except Exception as exc:
        return {
            "error": f"whole-run terminal operation failed closed: {exc.__class__.__name__}: {exc}",
            "step": (load(ws) or {}).get("step"),
        }


def replay_terminal_intent(ws: str) -> dict | None:
    """SessionStart replay of an existing intent; never infer terminality."""
    state = load(ws)
    if state is None or not isinstance(state.get("run_artifact_binding"), Mapping):
        return None
    intent_path, _receipt_path = _whole_run_terminal_paths(ws, state)
    if not os.path.exists(intent_path):
        return None
    try:
        intent = tp.load_json(intent_path, what="whole-run terminal intent")
        return {**_complete_whole_run_terminal(ws, intent), "terminalized": True, "replayed": True}
    except Exception as exc:
        return {
            "error": "persisted whole-run terminal replay failed closed: "
            f"{exc.__class__.__name__}: {exc}",
            "step": (load(ws) or {}).get("step"),
        }


def _em_outage_repository_identity(ws: str) -> dict:
    """Return only durable repository identity, never caller assertions."""
    try:
        locator = runtime_storage.load_workspace_locator(ws)
    except Exception:
        locator = None
    identity = {}
    if isinstance(locator, Mapping):
        for key in ("repo_id", "repository_key", "run_id", "worktree_id"):
            if locator.get(key) not in (None, ""):
                identity[key] = str(locator[key])
    git_common = tp._run(["git", "rev-parse", "--git-common-dir"], cwd=ws)
    if git_common.returncode == 0 and str(git_common.stdout or "").strip():
        common = str(git_common.stdout).strip()
        if not os.path.isabs(common):
            common = os.path.join(ws, common)
        identity["git_common_dir"] = os.path.realpath(common)
    return identity


def _em_outage_candidate(
    ws: str,
    state: dict,
    *,
    contract: Mapping[str, object] | None = None,
    output_snapshot: Mapping[str, object] | None = None,
) -> tuple[dict, dict, dict, dict]:
    """Re-derive the exact valid EM candidate and its terminal DoD."""
    if state.get("step") != "em":
        raise em_outage.EmOutageError("loop is not at final engineering review")
    task = _current_task(state)
    task_ref = str((task or {}).get("id") or "engineering-signoff")
    binding = review_kernel_binding(state, "em", task)
    if not isinstance(binding, Mapping) or not binding.get("run_id"):
        raise em_outage.EmOutageError("EM ReviewKernel binding is missing")
    active = dict(contract or _worker_stage_contract(ws, "em", task))
    lifecycle = active.get("worker_lifecycle") or {}
    if (
        active.get("worker_scoped") is not True
        or lifecycle.get("stage") != "em"
        or str(lifecycle.get("task") or "") != task_ref
    ):
        raise em_outage.EmOutageError("exact EM worker contract is missing")
    slot = str(lifecycle.get("slot") or active.get("task_slot") or "")
    expected_worker = str(lifecycle.get("expected_task_name") or "")
    dispatch = active.get("producer_dispatch") or {}
    if output_snapshot is None:
        findings_path = runtime_storage.review_public_path(ws, "findings.json")
        report_path = runtime_storage.review_public_path(ws, "report.md")
        snapshot = em_outage.capture_output_snapshot(findings_path, report_path)
    else:
        snapshot = em_outage.validate_output_snapshot(output_snapshot)
    material = producer_output_identity(
        ws, state, task, "em", active_contract=active, em_output_snapshot=snapshot
    )

    # All product, mechanical, zero-lens, output, graph, requirement, test,
    # and final-signoff checks run before an outage can even be represented.
    signoff_evidence, errors = _signoff_evidence_binding(ws, state, output_snapshot=snapshot)
    if errors or signoff_evidence is None:
        raise em_outage.EmOutageError("EM has non-producer blockers: " + "; ".join(errors))
    try:
        import review as _review

        kernel_ws = str(binding.get("workspace") or ws)
        kernel = _review._load_state(kernel_ws, str(binding["run_id"]))
    except Exception as exc:
        raise em_outage.EmOutageError("EM ReviewKernel identity is unreadable") from exc
    if (
        kernel.get("status") != "complete"
        or kernel.get("stage") != "review"
        or kernel.get("expected_lenses") != []
        or kernel.get("slots") != []
    ):
        raise em_outage.EmOutageError(
            "EM outage recovery requires one complete zero-lens ReviewKernel"
        )
    kernel_identity = {
        "binding": dict(binding),
        "schema": kernel.get("schema"),
        "run_id": kernel.get("run_id"),
        "stage": kernel.get("stage"),
        "status": kernel.get("status"),
        "expected_lenses": [],
        "slots": [],
        "revision": kernel.get("revision"),
        "delivery_mode_receipt": kernel.get("delivery_mode_receipt"),
        "zero_lens_evaluation": kernel.get("zero_lens_evaluation"),
    }
    hashes = em_outage.output_hashes(snapshot=snapshot)
    identity = em_outage.outage_identity(
        repository=_em_outage_repository_identity(ws),
        store=os.path.realpath(tp.store_root(ws)),
        worktree=os.path.realpath(ws),
        run_id=str(binding["run_id"]),
        slot=slot,
        expected_worker=expected_worker,
        output_contract_fingerprint=str(material["output_contract_fingerprint"]),
        producer_dispatch_fingerprint=str(dispatch.get("fingerprint") or ""),
        integration_revision=str(material["source_sha"] or ""),
        outputs=hashes,
        output_snapshot_fingerprint=str(snapshot["fingerprint"]),
        review_kernel=kernel_identity,
        task=task_ref,
        accepted_drift="D-0014",
    )
    terminal_contract = {
        "task_id": active.get("task_id"),
        "task_slot": active.get("task_slot"),
        "worker_scoped": active.get("worker_scoped"),
        "worker_lifecycle": dict(lifecycle),
        "producer_dispatch": dict(dispatch),
    }
    return identity, terminal_contract, signoff_evidence, snapshot


def _em_outage_control_plane_identity(ws: str, state: dict) -> dict:
    """Authenticate the slot-less loop controller against live run identity."""
    if tp.task_slot() is not None:
        raise em_outage.EmOutageError("worker TASKPLANE_TASK context cannot resolve final EM")
    if tp.load_active(ws) is not None:
        raise em_outage.EmOutageError("an active worker contract cannot act as control plane")
    repository = _em_outage_repository_identity(ws)
    material = {
        "schema": em_outage.CONTROL_PLANE_SCHEMA,
        "authority": "slotless-loop-control-plane",
        "repository": repository,
        "store": os.path.realpath(tp.store_root(ws)),
        "worktree": os.path.realpath(ws),
        "run_id": state["run_id"],
    }
    material["fingerprint"] = hashlib.sha256(tp.canonical_json_bytes(material)).hexdigest()
    return material


def _prepare_em_outage_audit(
    transaction: Mapping[str, object], receipt: Mapping[str, object]
) -> dict:
    """Persist or re-use the exact immutable audit from a retained snapshot."""
    path = str(transaction["path"])
    prior = transaction.get("prior")
    expected = transaction.get("expected")
    if prior is not None and not isinstance(prior, bytes):
        raise em_outage.EmOutageError("EM outage audit snapshot is invalid")
    if not isinstance(expected, bytes):
        raise em_outage.EmOutageError("EM outage audit transaction is invalid")
    exists_now = os.path.lexists(path)
    if exists_now != (prior is not None) or (
        exists_now and em_outage.read_regular_bytes(path) != prior
    ):
        raise em_outage.EmOutageError("EM outage audit changed before persistence")
    if prior is not None:
        if prior != expected:
            raise em_outage.EmOutageError("a different immutable EM outage audit already exists")
        return dict(transaction)
    # The durable primitive may replace the name before a directory fsync
    # fails.  The caller already retained enough exact material to compare and
    # restore that partial outcome.
    tp.atomic_write_json(path, dict(receipt), sort_keys=True)
    if em_outage.read_regular_bytes(path) != expected:
        raise em_outage.EmOutageError("EM outage audit did not persist with exact bytes")
    return dict(transaction)


def _restore_em_outage_audit(transaction: Mapping[str, object]) -> None:
    """Compare-and-restore only the audit written by this resolution attempt."""
    path = str(transaction["path"])
    prior = transaction.get("prior")
    expected = transaction.get("expected")
    if prior is not None and not isinstance(prior, bytes):
        raise em_outage.EmOutageError("EM outage audit snapshot is invalid")
    if not isinstance(expected, bytes):
        raise em_outage.EmOutageError("EM outage audit transaction is invalid")
    if not os.path.lexists(path):
        if prior is not None:
            tp.atomic_write_bytes(path, prior)
        return
    current = em_outage.read_regular_bytes(path)
    if current == prior:
        return
    if current != expected:
        raise em_outage.EmOutageError("EM outage audit changed during rollback")
    if prior is None:
        tp.safe_remove(path)
        if os.path.lexists(path):
            raise em_outage.EmOutageError("EM outage audit path survived rollback")
    else:
        tp.atomic_write_bytes(path, prior)
        if em_outage.read_regular_bytes(path) != prior:
            raise em_outage.EmOutageError("EM outage audit bytes did not roll back exactly")


def _em_outage_resolution_persisted(
    state: Mapping[str, object] | None, receipt: Mapping[str, object], audit_path: str
) -> bool:
    """Recognize a commit that survived a late persistence exception."""
    if not isinstance(state, Mapping) or state.get("step") != "signoff":
        return False
    outage = state.get("engineering_review_outage") or {}
    evidence = state.get("signoff_evidence") or {}
    return (
        isinstance(outage, Mapping)
        and outage.get("consumed") is True
        and outage.get("resolution") == receipt
        and outage.get("audit_path") == audit_path
        and state.get("engineering_review_outage_resolution") == receipt
        and isinstance(evidence, Mapping)
        and evidence.get("producer_receipt_outage") == receipt
    )


def _resolve_em_producer_receipt_outage(
    ws: str, *, by: str | None, accept: bool, supplied_fingerprint: str | None
) -> dict:
    """CAS-consume one current aggregate outage under the loop-state lock."""
    actor = str(by or "").strip()
    supplied = str(supplied_fingerprint or "").strip()
    if not accept or not actor or not supplied:
        return {
            "error": "EM producer-receipt acceptance requires --by, "
            "--accept-producer-receipt-outage, and the exact "
            "current --outage-fingerprint"
        }
    audit_transaction = None
    audit_path = None
    receipt = None
    committed = False
    try:
        with mutate(ws) as state:
            if state is None or state.get("step") != "em":
                return {"error": "no current final-EM outage to resolve"}
            state_before = json.loads(json.dumps(state))
            outage = state.get("engineering_review_outage")
            if not isinstance(outage, Mapping) or outage.get("consumed") is True:
                return {"error": "no unconsumed final-EM outage to resolve"}
            sealed = em_outage.validate_outage_identity(outage.get("identity") or {})
            if sealed["fingerprint"] != supplied:
                return {"error": "final-EM outage fingerprint is not the exact current fingerprint"}
            terminal_contract = outage.get("terminal_contract")
            if not isinstance(terminal_contract, Mapping):
                return {"error": "final-EM terminal contract is missing"}
            snapshot = em_outage.validate_output_snapshot(outage.get("output_snapshot") or {})
            if snapshot["fingerprint"] != sealed["output_snapshot_fingerprint"]:
                return {"error": "final-EM output snapshot identity is stale"}
            current, _, signoff_evidence, consumed_snapshot = _em_outage_candidate(
                ws, state, contract=terminal_contract, output_snapshot=snapshot
            )
            if current != sealed:
                return {
                    "error": "final-EM outage identity is stale; review "
                    "bytes, revision, contract, or kernel changed"
                }
            authority = _em_outage_control_plane_identity(ws, state)
            receipt = em_outage.resolution_receipt(current, actor=actor, control_plane=authority)
            audit_path = runtime_storage.review_public_path(ws, "em-outage-resolution.json")
            # Retain the exact previous bytes before touching the immutable
            # public alias.  If any later transition or aggregate persistence
            # fails, the outer recovery restores this exact path state.
            expected = json.dumps(receipt, indent=1, sort_keys=True).encode("utf-8")
            prior = (
                em_outage.read_regular_bytes(audit_path) if os.path.lexists(audit_path) else None
            )
            audit_transaction = {"path": audit_path, "prior": prior, "expected": expected}
            audit_transaction = _prepare_em_outage_audit(audit_transaction, receipt)
            resolved = dict(outage)
            resolved["consumed"] = True
            resolved["resolution"] = receipt
            resolved["audit_path"] = audit_path
            state["engineering_review_outage"] = resolved
            evidence = dict(signoff_evidence)
            evidence["em_output_snapshot"] = em_outage.output_snapshot_evidence(consumed_snapshot)
            evidence["producer_receipt_outage"] = receipt
            evidence["accepted_drift"] = {"id": "D-0014", "accepted_by": "human:vdemkiv"}
            state["signoff_evidence"] = evidence
            state["signoff_dod"] = dict(evidence["dod"])
            state["engineering_review_outage_resolution"] = receipt
            completion = _stage_loop_gate_completion(
                ws, state, step="em", outcome="pass", note="exact producer-receipt outage accepted"
            )
            state["step"] = "signoff"
            try:
                stage_transition = _stage_loop_transition(
                    ws, state, from_step="em", to_step="signoff", completion=completion
                )
            except Exception:
                state.clear()
                state.update(state_before)
                raise
        committed = True
    except Exception as exc:
        recovery_error = None
        if audit_transaction is not None and not committed:
            try:
                durable = _load_raw(ws)
                if not _em_outage_resolution_persisted(
                    durable, receipt or {}, str(audit_path or "")
                ):
                    _restore_em_outage_audit(audit_transaction)
            except Exception as recovery:
                recovery_error = recovery
        detail = f"{exc.__class__.__name__}: {exc}"
        if recovery_error is not None:
            detail += (
                "; audit recovery failed closed: "
                f"{recovery_error.__class__.__name__}: "
                f"{recovery_error}"
            )
        return {"error": "final-EM outage resolution failed closed: " + detail}
    tp.trace(
        ws,
        "em_producer_receipt_outage_resolved",
        fingerprint=supplied,
        actor=actor,
        authority=authority["fingerprint"],
    )
    return {
        "step": "signoff",
        "outage_resolution": receipt,
        "stage_transition": stage_transition,
        "status": status(ws),
    }


def _record_design_contracts(ws: str, state: dict, contract: dict | None) -> list:
    """The sanctioned mechanical path for DESIGN-introduced contracts into
    the dependency graph (v2.3.0).

    A design may legitimately propose a NEW boundary (e.g.
    contract:order-cancelled-v2) that is not declared on the requirement.
    Only requirement contracts were auto-recorded, the designer is forbidden
    to mutate the graph, and the planner's contract has no Bash tool — so
    graph readiness blocked with 'contracts are not recorded in the
    dependency graph' and no in-band remedy. At the human design-approval
    gate-PASS the engine records each approved design contract as a
    req→contract edge (registering the contract node), recorded + traced.
    Plan DoR is NOT weakened: it still independently verifies every declared
    contract is recorded — this only provides the governed path that records
    them. Returns the recorded contract ids."""
    rid = state.get("requirement_id")
    if not rid:
        return []
    applied = []
    for row in (contract or {}).get("contracts") or []:
        cids = depgraph.contract_ids([row])
        if not cids:
            continue
        relation = row.get("relation", "changes") if isinstance(row, dict) else "changes"
        depgraph.record_edge(
            ws,
            depgraph.req_node(rid),
            cids[0],
            kind=relation,
            confidence="high",
            note="approved design contract",
        )
        applied.append(cids[0])
    if applied:
        tp.trace(
            ws,
            "design_contracts_recorded",
            gate="design_approval",
            requirement=rid,
            contracts=applied,
        )
    return applied


def _annotate_plan_graph(ws: str, state: dict) -> None:
    """Plan-gate graph work: planned req→module links + per-task blast."""
    # Batch by requirement first: link_requirement(replace=True) refreshes a
    # requirement's whole edge set of one kind, so calling it once per task
    # would let a second task sharing the requirement WIPE the first's edges.
    planned = {}
    for t in state.get("tasks") or []:
        rid = t.get("req") or state.get("requirement_id")
        scope = t.get("scope") or []
        if rid and scope:
            planned.setdefault(rid, []).extend(scope)
    for rid, scopes in planned.items():
        depgraph.link_requirement(ws, rid, scopes, kind="planned")

    for t in state.get("tasks") or []:
        rid = t.get("req") or state.get("requirement_id")
        scope = t.get("scope") or []
        if not scope:
            continue
        mods = depgraph.scope_modules(ws, scope)
        imp = (
            depgraph.impact(ws, mods, policy=t.get("impact_policy") or depgraph.impact_policy(t))
            if depgraph.load(ws)["modules"]
            else None
        )
        prod = depgraph.product_impact(ws, mods)
        own = depgraph.req_node(rid) if rid else None
        shared = [r for r in prod["affected_requirements"] if r != own]
        t["blast"] = {
            "modules": mods,
            "impacted": imp["total_impacted"] if imp else 0,
            "unknown": imp["unknown"] if imp else mods,
            "truncated": bool(imp and imp.get("truncated")),
            "policy": t.get("impact_policy") or depgraph.impact_policy(t),
            "shared_with": shared,
            "dependent_requirements": prod["dependent_requirements"],
        }
        if shared:
            tp.trace(ws, "graph_shared_surface", task=t["id"], requirement=rid, shared_with=shared)


def _true_up_graph(ws: str, state: dict) -> None:
    """Pre-EM graph work: realize requirements, then scan the final tree."""
    changed = [
        f
        for f in _diff_files(ws, _review_baseline(ws, state, "em") or "HEAD")
        if not f.startswith(lens_router.LOOP_OWNED)
    ]
    if not changed:
        depgraph.scan(ws)
        tp.trace(ws, "graph_true_up", files=0)
        return
    # Batch by requirement (see _annotate_plan_graph) so multiple tasks
    # sharing one requirement accumulate their realized surface instead of
    # the last task's replace=True wiping the earlier ones'.
    realized = {}
    for t in state.get("tasks") or []:
        rid = t.get("req") or state.get("requirement_id")
        if not rid:
            continue
        stems = [g.split("*", 1)[0] for g in (t.get("scope") or [])]
        mine = [f for f in changed if any(f.startswith(s) for s in stems if s)]
        realized.setdefault(rid, []).extend(mine)
    for rid, files in realized.items():
        depgraph.link_requirement(ws, rid, files or changed, kind="realizes")
    # Scan after recording the realized edges so the graph fingerprint covers
    # both the final code tree and requirement-to-implementation truth.
    depgraph.scan(ws)
    tp.trace(ws, "graph_true_up", files=len(changed))


def _refinement_report(ws: str, state: dict) -> list:
    """Score each task's anchored requirement at the plan gate — the
    forecast shows BEFORE a build starts (requirements-at-the-core)."""
    out = []
    for t in state.get("tasks") or []:
        rid = t.get("req") or state.get("requirement_id")
        if not rid:
            continue
        rec = reqs.get_requirement(ws, rid)
        if rec is None:
            out.append(
                {"task": t["id"], "requirement": rid, "error": "requirement not found in the KB"}
            )
            continue
        g = reqs.gate(
            rec,
            high_cost=bool(t.get("high_cost")),
            changed_files=t.get("scope"),
            task_type=t.get("type"),
        )
        mode = reqs.suggest_mode(g["score"], len(t.get("scope") or []))
        out.append({"task": t["id"], "requirement": rid, "gate": g, "mode_suggestion": mode})
        tp.trace(
            ws,
            "refinement_gate",
            task=t["id"],
            requirement=rid,
            score=g["score"],
            blocking=g["blocking"],
            mode=mode["mode"],
        )
    return out


@run_context.operation
@loop_status.with_dashboard
def approve(*args, **kwargs):
    return gates.approve(sys.modules[__name__], *args, **kwargs)


@loop_status.with_dashboard
def select(*args, **kwargs):
    return gates.select(sys.modules[__name__], *args, **kwargs)


def _cascade_skip(*args, **kwargs):
    return gates._cascade_skip(sys.modules[__name__], *args, **kwargs)


@run_context.operation
@loop_status.with_dashboard
def resolve(*args, **kwargs):
    return gates.resolve(sys.modules[__name__], *args, **kwargs)


@loop_status.with_dashboard
def replan(*args, **kwargs):
    return gates.replan(sys.modules[__name__], *args, **kwargs)


@run_context.operation
@loop_status.with_dashboard
def retro(*args, **kwargs):
    return gates.retro(sys.modules[__name__], *args, **kwargs)


_load_tasks = loop_status.load_tasks
status = loop_status.status
user_summary = loop_status.user_summary
_publish_artifacts = loop_status.publish_artifacts
