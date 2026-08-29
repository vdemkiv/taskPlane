"""The Evaluate-Loop engine — owned by taskplane.

taskplane owns the loop state machine, sequences the DoR/DoD gates, activates
each step's contract (so the PreToolUse hook enforces it), and records every
transition to `.taskplane/trace.jsonl`. The role agents are pluggable step
workers: the engine tells the driver which role to run and under which
contract; the driver runs it and reports the outcome back via `gate`.

State machine (per docs/loop-design.md, answers locked 2026-07-11):
  init → (pm if free-text goal, else optional design or plan)
  pm      → optional design → design_approval (human) → plan
  plan    → plan_approval (human) → execute
  execute → evaluate
  evaluate: pass → next task, or → em when all tasks pass
            unavailable → escalated recovery with a non-judged warning
            fail → fix (if fix_cycles < max) else escalated (human)
  fix     → evaluate
  em      → signoff (human) → retro/graph true-up → done
  escalated → (human) retry | skip | abort

Human gates: design approval (when requested), plan approval, and EM sign-off.
On FAIL: auto-fix up to
max_fix_cycles (default 2), then escalate. Goal input: free-text (→pm) or an
existing spec (→plan).
"""

from __future__ import annotations

from collections.abc import Mapping
import base64
import contextlib
import contextvars
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shlex
import stat
import sys
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

if __package__:
    from . import brief_projection
    from . import delivery_policy
    from . import dispatch_telemetry
    from . import evaluation_output as evaluation_output
    from . import plan_topology
    from . import producer_observation as producer_observation_policy
    from . import terminal_truth
    from .delivery_ports import SystemClock
else:  # pragma: no cover - direct CLI module loading
    import brief_projection
    import delivery_policy
    import dispatch_telemetry
    import plan_topology
    import producer_observation as producer_observation_policy
    import terminal_truth
    from delivery_ports import SystemClock

LOOP_FILE = "loop.json"
REVIEW_RAW_DIFF_RETENTION_SECONDS = 24 * 60 * 60
REVIEW_RAW_DIFF_MAX_ARTIFACTS = 32
REVIEW_RAW_DIFF_MAX_BYTES = 16 * 1024 * 1024


def _retained_review_diff_payload(*, base: str, files: list[str],
                                  patch: str,
                                  now: float | None = None,
                                  run_id: str | None = None,
                                  review_id: str | None = None) -> dict:
    created_at = float(time.time() if now is None else now)
    review_identity = str(review_id or hashlib.sha256(json.dumps(
        {"base": str(base), "files": list(files)}, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest())
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
        marker = tp.load_json(path, default=None,
                              what="raw diff retention watermark")
        if marker is not None:
            if marker.get("schema") != "taskplane.raw-diff-watermark/v1":
                raise ValueError("unsupported raw diff retention watermark")
            prior = float(marker["observed_at"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Damage can conservatively expire artifacts, never prolong them.
        prior = float("inf")
    high_water = max(float(observed_at), prior)
    if math.isfinite(high_water):
        tp.atomic_write_json(path, {
            "schema": "taskplane.raw-diff-watermark/v1",
            "observed_at": high_water,
        }, sort_keys=True)
    return high_water


def _raw_diff_entry(path: str, fingerprint: str, observed_at: float) -> dict:
    """Verify immutable bytes and the closed, creation-bound TTL schema."""
    with open(path, "rb") as source:
        raw = source.read(REVIEW_RAW_DIFF_MAX_BYTES + 1)
    if len(raw) > REVIEW_RAW_DIFF_MAX_BYTES or \
            hashlib.sha256(raw).hexdigest() != fingerprint:
        raise ValueError("content-addressed raw diff mismatch")
    payload = json.loads(raw.decode("utf-8"))
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")
    retention = payload.get("retention") if isinstance(payload, dict) else None
    required = {"schema", "created_at", "expires_at", "raw_fields",
                "delete_on_expiry", "run_id", "review_id"}
    if canonical != raw or not isinstance(retention, dict) or \
            set(retention) != required or retention.get("schema") != \
            "taskplane.raw-diff-retention/v1" or \
            retention.get("raw_fields") != ["patch"] or \
            retention.get("delete_on_expiry") is not True:
        raise ValueError("raw diff retention schema is invalid")
    created_at = float(retention["created_at"])
    expires_at = float(retention["expires_at"])
    if not math.isfinite(created_at) or not math.isfinite(expires_at) or \
            expires_at != created_at + REVIEW_RAW_DIFF_RETENTION_SECONDS or \
            observed_at < created_at or \
            not str(retention.get("run_id") or "").strip() or \
            not str(retention.get("review_id") or "").strip():
        raise ValueError("raw diff expiry or attribution is invalid")
    return {"fingerprint": fingerprint, "path": path, "bytes": len(raw),
            "created_at": created_at, "expires_at": expires_at,
            "run_id": str(retention["run_id"]),
            "review_id": str(retention["review_id"])}


def _purge_raw_diff(path: str) -> None:
    """Stage one validated private artifact before its irreversible purge."""
    directory = os.path.dirname(path)
    staging = os.path.join(directory, ".privacy-purge-" +
                           secrets.token_hex(12))
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
            if fingerprint in name and os.path.isfile(path) and \
                    not os.path.islink(path):
                _purge_raw_diff(path)
        tp._fsync_directory(directory)


def _enforce_review_diff_retention_locked(
        store, *, observed_at: float, keep_fingerprint: str | None = None,
        purge_fingerprint: str | None = None) -> dict:
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
            invalid.append({"fingerprint": fingerprint, "path": path,
                            "run_id": "unknown", "review_id": "unknown",
                            "reason": "symlink"})
            continue
        try:
            entries.append(_raw_diff_entry(path, fingerprint, observed_at))
        except (OSError, UnicodeError, ValueError, TypeError, KeyError,
                AttributeError):
            invalid.append({"fingerprint": fingerprint, "path": path,
                            "run_id": "unknown", "review_id": "unknown",
                            "reason": "invalid-or-tampered"})

    purged = []
    retained = 0
    retained_bytes = 0
    ordered = sorted(entries, key=lambda item: (
        item["fingerprint"] == keep_fingerprint,
        item["created_at"], item["fingerprint"]), reverse=True)
    for row in invalid + ordered:
        reason = row.get("reason")
        if row["fingerprint"] == purge_fingerprint:
            reason = "review-complete"
        elif not reason and row["expires_at"] <= observed_at:
            reason = "expired"
        elif not reason and retained >= REVIEW_RAW_DIFF_MAX_ARTIFACTS:
            reason = "count-bound"
        elif not reason and retained_bytes + row["bytes"] > \
                REVIEW_RAW_DIFF_MAX_BYTES:
            reason = "byte-bound"
        if not reason:
            retained += 1
            retained_bytes += row["bytes"]
            continue
        _purge_raw_diff(row["path"])
        _purge_raw_diff_derivatives(store, row["fingerprint"])
        purged.append({key: row[key] for key in (
            "fingerprint", "run_id", "review_id")} | {"reason": reason})
    if purged:
        tp._fsync_directory(directory)
    return {"removed": len(purged), "retained": retained,
            "purged": purged}


def enforce_review_diff_retention(
        workspace: str, *, store, now: float | None = None,
        keep_fingerprint: str | None = None,
        purge_fingerprint: str | None = None,
        _lock_held: bool = False) -> dict:
    """Purge expired/excess/tampered raw diffs under one store lock."""
    del workspace
    observed_at = float(time.time() if now is None else now)
    action = lambda: _enforce_review_diff_retention_locked(
        store, observed_at=observed_at,
        keep_fingerprint=keep_fingerprint,
        purge_fingerprint=purge_fingerprint)
    if _lock_held:
        result = action()
    else:
        with tp.file_lock(os.path.join(store.root, ".diff-retention")):
            result = action()
    return {**result,
            "retention_seconds": REVIEW_RAW_DIFF_RETENTION_SECONDS,
            "max_artifacts": REVIEW_RAW_DIFF_MAX_ARTIFACTS,
            "max_bytes": REVIEW_RAW_DIFF_MAX_BYTES}


def store_retained_review_diff(workspace: str, *, store, payload: dict,
                               now: float | None = None) -> dict:
    """Sweep and put under one lock shared by every concurrent reviewer."""
    observed_at = float(time.time() if now is None else now)
    with tp.file_lock(os.path.join(store.root, ".diff-retention")):
        enforce_review_diff_retention(
            workspace, store=store, now=observed_at, _lock_held=True)
        reference = store.put("diff", payload)
        enforce_review_diff_retention(
            workspace, store=store, now=observed_at,
            keep_fingerprint=reference["fingerprint"], _lock_held=True)
    return reference


def read_retained_review_diff(workspace: str, *, store, reference: dict,
                              now: float | None = None) -> dict:
    """Sweep and verify immediately before a governed raw-diff read."""
    with tp.file_lock(os.path.join(store.root, ".diff-retention")):
        enforce_review_diff_retention(
            workspace, store=store, now=now,
            keep_fingerprint=str(reference.get("fingerprint") or ""),
            _lock_held=True)
        return store.read(reference)


def _brief_source_root(ws: str) -> str:
    return os.path.join(tp.tp_dir(ws), "loop-next-sources-v1")


def project_next_action_for_host(
        ws: str, action: Mapping[str, object], *,
        wave_usage: Mapping[str, object] | None = None) -> dict:
    """Persist one exact action and return its bounded public delta.

    ``next_action`` remains the internal transition API used by tests and
    in-process adapters.  The CLI/host boundary calls this function so the
    model-facing payload is bounded without depriving internal consumers of
    the full authority-bearing action.
    """
    if not isinstance(action, Mapping):
        raise brief_projection.BriefProjectionError(
            "loop next action must be a mapping")
    if wave_usage is None:
        state = load(ws)
        ledger = (state or {}).get("dispatch_telemetry")
        if isinstance(ledger, Mapping):
            wave_usage = dispatch_telemetry.wave_usage(
                ledger, SystemClock())
    root = _brief_source_root(ws)
    os.makedirs(root, exist_ok=True)
    raw = brief_projection.canonical_text(action)
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    source_path = os.path.join(root, f"{fingerprint}.json")
    head_path = os.path.join(root, "HEAD.json")
    relative_source = os.path.relpath(source_path, os.path.realpath(ws)) \
        .replace(os.sep, "/")
    with tp.file_lock(head_path):
        previous = None
        if os.path.lexists(head_path):
            if os.path.islink(head_path):
                raise brief_projection.BriefProjectionError(
                    "loop next source head must not be a symlink")
            with open(head_path, encoding="utf-8") as stream:
                head = json.load(stream)
            previous_name = str(head.get("source") or "") \
                if isinstance(head, Mapping) else ""
            if not re.fullmatch(r"[0-9a-f]{64}\.json", previous_name):
                raise brief_projection.BriefProjectionError(
                    "loop next source head is invalid")
            previous_path = os.path.realpath(os.path.join(root, previous_name))
            if os.path.dirname(previous_path) != os.path.realpath(root):
                raise brief_projection.BriefProjectionError(
                    "loop next source head escapes its store")
            with open(previous_path, encoding="utf-8") as stream:
                loaded = json.load(stream)
            if not isinstance(loaded, Mapping):
                raise brief_projection.BriefProjectionError(
                    "prior loop next source is not an object")
            previous = loaded
        if os.path.lexists(source_path) and os.path.islink(source_path):
            raise brief_projection.BriefProjectionError(
                "loop next source must not be a symlink")
        if os.path.isfile(source_path):
            with open(source_path, encoding="utf-8") as stream:
                existing = stream.read()
            if existing != raw:
                raise brief_projection.BriefProjectionError(
                    "loop next source fingerprint collision")
        else:
            temporary = f"{source_path}.tmp.{os.getpid()}"
            try:
                with open(temporary, "x", encoding="utf-8", newline="") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, source_path)
            finally:
                with contextlib.suppress(OSError):
                    if os.path.exists(temporary):
                        os.unlink(temporary)
        projected = brief_projection.project(
            action, previous=previous, wave_usage=wave_usage,
            reference_artifact=relative_source,
        )
        tp.atomic_write_json(
            head_path, {"schema": "taskplane.loop-next-source-head/v1",
                        "source": f"{fingerprint}.json"}, sort_keys=True)
    return projected


def stamp_plan_delivery_mode(
        state: dict, declaration: Mapping[str, object], *,
        plan_fingerprint: str, source_sha: str,
        predecessor_fingerprint: str | None = None) -> dict:
    """Seal one explicit Plan delivery declaration into loop state.

    Validation happens before mutation so a malformed or contradictory mode
    cannot leave partial dispatch authority behind.
    """
    if not isinstance(state, dict):
        raise delivery_policy.DeliveryPolicyError(
            "loop state must be mutable for Plan delivery mode")
    receipt = delivery_policy.validate_plan_mode(
        declaration,
        plan_fingerprint=plan_fingerprint,
        source_sha=source_sha,
        predecessor_fingerprint=predecessor_fingerprint,
    )
    requirement_id = str(state.get("requirement_id") or "").strip()
    if requirement_id and receipt["requirement"] != requirement_id:
        raise delivery_policy.DeliveryPolicyError(
            "delivery-mode receipt requirement does not match the loop")
    state["delivery_mode_receipt"] = receipt
    return receipt


def _validated_delivery_mode(state: Mapping[str, object]) -> dict | None:
    receipt = state.get("delivery_mode_receipt")
    if receipt is None:
        return None
    if not isinstance(receipt, Mapping):
        raise delivery_policy.DeliveryPolicyError(
            "delivery-mode receipt must be a mapping")
    return delivery_policy.validate_delivery_mode_receipt(receipt)


def _plan_delivery_mode_from_file(
        ws: str, state: dict, *, apply: bool) -> dict | None:
    """Consume an explicitly declared Plan mode without changing legacy Plans."""
    path = os.path.join(ws, "plan", "tasks.json")
    try:
        with open(path, encoding="utf-8") as stream:
            plan = json.load(stream)
    except (OSError, ValueError):
        return _validated_delivery_mode(state)
    if not isinstance(plan, dict) or "delivery_mode" not in plan:
        return _validated_delivery_mode(state)
    declaration = {
        "requirement": plan.get("requirement") or state.get("requirement_id"),
        "delivery_mode": plan.get("delivery_mode"),
        "automatic_lenses": plan.get("automatic_lenses"),
        "plan_authority": plan.get("plan_authority"),
    }
    plan_fingerprint = hashlib.sha256(json.dumps(
        plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")).hexdigest()
    source_sha = str(tp.git_head(ws) or "")
    prior = _validated_delivery_mode(state)
    if prior and all((
            prior["requirement"] == declaration["requirement"],
            prior["plan_fingerprint"] == plan_fingerprint,
            prior["source_sha"] == source_sha,
            prior["mode"] == declaration["delivery_mode"],
            prior["automatic_lenses"] == declaration["automatic_lenses"],
            prior["plan_authority"] == declaration["plan_authority"],
    )):
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


def build_dispatch_lens_routing(
        state: Mapping[str, object], task: Mapping[str, object] | None,
        *, workspace: str) -> tuple[dict, dict | None]:
    """Prime legacy Build lenses or enforce a sealed zero-lens Build mode."""
    receipt = _validated_delivery_mode(state)
    if receipt is None:
        if str(state.get("design_fingerprint") or "").strip():
            raise delivery_policy.DeliveryPolicyError(
                "delivery-mode receipt is required for a Design-governed build")
        return (lens_router.prime_scope(
            (task or {}).get("scope"),
            task_type=(task or {}).get("type"), workspace=workspace), None)
    if receipt["mode"] != "build":
        raise delivery_policy.DeliveryPolicyError(
            "execute dispatch requires build delivery mode")
    authorization = build_c.authorize_delivery_dispatch(
        receipt, lens_worker_factory=lambda lens: lens)
    return ({
        "lenses": [],
        "context": {
            "delivery_mode": "build",
            "delivery_mode_receipt": receipt["fingerprint"],
            "automatic_lens_worker_count": 0,
        },
    }, authorization)


def bind_producer_observation(
        submission: Mapping[str, object], receipt: Mapping[str, object] | None,
        *, output_bytes: bytes, output_schema_id: str,
        output_contract_fingerprint: str) -> dict:
    """Refuse caller-authored provenance at the public loop boundary."""
    del submission, receipt, output_bytes, output_schema_id
    del output_contract_fingerprint
    raise producer_observation_policy.ProducerObservationError(
        "a genuine external host producer receipt is required; "
        "caller-supplied producer observation is refused"
    )


def producer_output_identity(ws: str, state: Mapping[str, object],
                             task: Mapping[str, object] | None, step: str,
                             *, active_contract: Mapping[str, object] | None =
                             None) -> dict:
    """Derive the one engine-owned output identity a host stop may observe."""
    if step not in {"evaluate", "em"}:
        raise producer_observation_policy.ProducerObservationError(
            "producer observation is only defined for evaluate or em")
    binding = review_kernel_binding(dict(state), step, dict(task or {}))
    if not binding or not str(binding.get("run_id") or "").strip():
        raise producer_observation_policy.ProducerObservationError(
            f"{step} ReviewKernel binding is missing")
    run_id = str(binding["run_id"])
    task_id = str((task or {}).get("id") or "engineering-signoff")
    producer = STEP_ROLE[step]
    dispatch = producer_observation_policy.validate_producer_dispatch(
        (active_contract or {}).get("producer_dispatch"), run_id=run_id,
        task_id=task_id, stage=step, producer=producer)
    source_sha = tp.git_head(ws)
    if step == "evaluate":
        contract = (active_contract or {}).get("output_contract")
        if not isinstance(contract, Mapping) or \
                contract.get("stage") != "evaluate" or \
                contract.get("task") != task_id or \
                contract.get("producer") != "tp-evaluator":
            raise producer_observation_policy.ProducerObservationError(
                "external host producer receipt cannot be matched: active "
                "evaluator output contract is missing or mismatched")
        output_path = str(contract.get("result_path") or "")
        resolved = (output_path if os.path.isabs(output_path) else
                    os.path.join(ws, output_path))
        try:
            with open(resolved, "rb") as stream:
                output_bytes = stream.read()
        except OSError as exc:
            raise producer_observation_policy.ProducerObservationError(
                "evaluator result bytes are missing") from exc
        output_schema_id = str(contract.get("output_schema_id") or "")
        contract_fingerprint = \
            producer_observation_policy.content_fingerprint(dict(contract))
    else:
        paths = [runtime_storage.review_public_path(ws, "findings.json"),
                 runtime_storage.review_public_path(ws, "report.md")]
        exact = []
        for path in paths:
            try:
                with open(path, "rb") as stream:
                    exact.append((path, stream.read()))
            except OSError as exc:
                raise producer_observation_policy.ProducerObservationError(
                    "EM result bytes are missing") from exc
        output_path = json.dumps(paths, separators=(",", ":"))
        output_bytes = producer_observation_policy.exact_output_bundle(exact)
        output_schema_id = "taskplane.em-output/v1"
        delivery = _validated_delivery_mode(dict(state))
        contract_fingerprint = producer_observation_policy.content_fingerprint({
            "schema": "taskplane.em-output-contract/v1",
            "run_id": run_id,
            "task_id": task_id,
            "stage": "em",
            "output_paths": paths,
            "delivery_mode_receipt": (delivery or {}).get("fingerprint"),
        })
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

STAGE_COMMAND_SCHEMA = "taskplane.stage-command-result/v1"
STAGE_HISTORY_SCHEMA = "taskplane.stage-history-page/v1"
STAGE_HISTORY_MAX_ITEMS = 100
_STAGE_RUN_BINDING_SCHEMA = "taskplane.loop-stage-run-binding/v1"
_STAGE_ROOT_AUTHORITY_SCHEMA = \
    "taskplane.loop-root-bootstrap-authority/v1"
_STAGE_RUN_BINDING_FIELDS = frozenset({
    "schema", "run_id", "repository_id", "repository_key", "run_schema",
    "root_stage_id", "store_identity_fingerprint",
})
_STAGE_ROOT_AUTHORITY_FIELDS = frozenset({
    "schema", "run_id", "repository_id", "repository_key", "worktree_id",
    "target_revision", "worktree_revision", "requirement_id",
    "requirement_revision", "requirement_fingerprint", "actor",
    "session_id", "authority_revision", "fingerprint",
})
_STAGE_ACTOR_IDENTIFIER = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_STAGE_RUNTIME_FIELDS = frozenset({
    "agent", "agents", "conversation", "conversations", "environment",
    "env", "event", "events", "eventlog", "eventlogs", "lease", "leases",
    "log", "logs", "path", "root", "runtime", "runtimestate", "tool",
    "tools", "tooltranscript", "tooltranscripts", "transcript",
    "transcripts", "workspace",
})
_STAGE_REQUEST_FIELDS = {
    "history": frozenset({"schema", "run_id", "cursor", "limit"}),
    "start": frozenset({
        "schema", "stage", "expected_revision", "operation_id",
        "expected_predecessor_fingerprints", "foreground", "authority",
        "declared_scope",
    }),
    "reuse": frozenset({
        "schema", "stage", "successor_stage", "expected_revision",
        "operation_id", "expected_predecessor_fingerprints", "foreground",
        "authority", "declared_scope", "reason", "actor",
    }),
    "resume": frozenset({
        "schema", "run_id", "stage_id", "expected_head_fingerprint",
        "expected_revision", "operation_id", "attempt_id", "authority",
        "declared_scope",
    }),
    "terminalize": frozenset({
        "schema", "run_id", "stage_id", "expected_head_fingerprint",
        "expected_revision", "operation_id", "outcome", "actor",
        "terminalized_at", "reason_code", "reason",
        "completed_deliverables", "completion_evidence", "handoff_manifest",
        "authority",
    }),
    "terminalize-and-start": frozenset({
        "schema", "run_id", "predecessor_stage_id", "stage",
        "successor_stage", "expected_head_fingerprint", "expected_revision",
        "operation_id", "outcome", "actor", "terminalized_at",
        "reason_code", "reason", "completed_deliverables",
        "completion_evidence", "foreground", "authority", "declared_scope",
    }),
    "split": frozenset({
        "schema", "run_id", "stage_id", "expected_head_fingerprint",
        "expected_revision", "operation_id", "child_specs", "actor",
        "terminalized_at", "reason", "authority", "declared_scopes",
    }),
}


def _stage_command_error(command: object, exc: Exception) -> dict:
    """Return a stable CLI error without changing legacy loop state."""
    return {
        "schema": STAGE_COMMAND_SCHEMA,
        "command": str(command or ""),
        "error": f"{exc.__class__.__name__}: {exc}",
    }


def _stage_request(request: object) -> dict:
    """Copy one JSON stage request before it crosses the lifecycle seam."""
    if not isinstance(request, Mapping):
        raise ValueError("stage request must be a JSON object")
    if any(not isinstance(key, str) for key in request):
        raise ValueError("stage request field names must be strings")
    # A canonical JSON round-trip both detaches caller-owned mutable values
    # and rejects Python-only values before an operation fingerprint is made.
    try:
        return json.loads(json.dumps(
            request, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("stage request must be canonical JSON") from exc


def _reject_stage_runtime_fields(value: object, label: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = "".join(character for character in key.lower()
                                 if character not in "-_. ")
            if normalized in _STAGE_RUNTIME_FIELDS:
                raise ValueError(
                    f"{label} contains forbidden runtime field {key!r}")
            _reject_stage_runtime_fields(child, label)
    elif isinstance(value, list):
        for child in value:
            _reject_stage_runtime_fields(child, label)


def _validate_stage_request(action: str, request: dict) -> dict:
    allowed = _STAGE_REQUEST_FIELDS[action]
    unknown = set(request) - allowed
    if unknown:
        raise ValueError(
            "stage request has unknown fields: " + ", ".join(sorted(unknown)))
    schema = request.get("schema")
    if schema is not None and schema != "taskplane.stage-command/v1":
        raise ValueError("stage request schema is invalid")
    _reject_stage_runtime_fields(request, "stage request")
    return request


def _stage_run_id(command: str, request: Mapping[str, object]) -> str:
    stage = request.get("stage")
    if command in {"reuse", "terminalize-and-start"} and stage is None:
        stage = request.get("successor_stage")
    if command in {"start", "reuse", "terminalize-and-start"}:
        if not isinstance(stage, Mapping):
            raise ValueError(f"{command} requires stage")
        run_id = stage.get("run_id")
    else:
        run_id = request.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError(f"{command} requires run_id")
    return run_id


def _stage_store(ws: str, run_id: str):
    """Open the exact locator-bound RunStore, with a legacy test fallback."""
    try:
        if __package__:
            from . import run_store as stage_run_store
        else:
            import run_store as stage_run_store
    except ImportError:
        import run_store as stage_run_store
    locator = runtime_storage.load_workspace_locator(ws)
    if isinstance(locator, Mapping):
        if locator.get("run_id") != run_id:
            raise ValueError("workspace belongs to a different stage run")
        home = locator.get("home")
        if not isinstance(home, str) or not home:
            raise ValueError("workspace stage store is unavailable")
        return stage_run_store.RunStore(home=home)
    return stage_run_store.RunStore()


def _stage_mode() -> str:
    """Resolve the fail-closed v4 rollout mode through the lite kernel."""
    resolver = getattr(tp, "stage_native_mode", None)
    if callable(resolver):
        return str(resolver())
    raw = os.environ.get("TASKPLANE_STAGE_NATIVE", "").strip().lower()
    if raw == "new-run":
        return "new-run"
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return "enabled"
    return "disabled"


def _stage_read_run_binding(state: object) -> dict | None:
    """Validate the singleton's non-optional migrated-run identity."""
    if not isinstance(state, Mapping) or "_stage_run_binding" not in state:
        return None
    raw = state.get("_stage_run_binding")
    if not isinstance(raw, Mapping) or set(raw) != _STAGE_RUN_BINDING_FIELDS:
        raise ValueError("stage-native run binding is invalid")
    binding = dict(raw)
    if binding.get("schema") != _STAGE_RUN_BINDING_SCHEMA or \
            binding.get("run_schema") != "taskplane.run/v4":
        raise ValueError("stage-native run binding is invalid")
    for field in ("run_id", "repository_id", "repository_key",
                  "root_stage_id"):
        value = binding.get(field)
        if not isinstance(value, str) or not value.strip() or \
                value != value.strip():
            raise ValueError("stage-native run binding is invalid")
    fingerprint = binding.get("store_identity_fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(
            r"[0-9a-f]{64}", fingerprint):
        raise ValueError("stage-native run binding is invalid")
    return binding


def _stage_store_identity_fingerprint(
        locator: Mapping[str, object], *, store_home: object = None) -> str:
    """Bind the singleton to one exact canonical locator/run-store root."""
    run_id = str(locator.get("run_id") or "")
    repository_id = str(locator.get("repo_id") or "")
    repository_key = str(locator.get("repository_key") or "")
    locator_home = os.path.realpath(str(locator.get("home") or ""))
    actual_home = os.path.realpath(str(store_home or locator_home))
    if not all((run_id, repository_id, repository_key, locator_home)) or \
            not os.path.isabs(locator_home) or actual_home != locator_home:
        raise ValueError("stage-native run store identity is invalid")
    run_path = os.path.realpath(os.path.join(actual_home, "runs", run_id))
    if os.path.commonpath((actual_home, run_path)) != actual_home:
        raise ValueError("stage-native run store identity is invalid")
    try:
        if __package__:
            from . import review_evidence
        else:
            import review_evidence
    except ImportError:
        import review_evidence
    return review_evidence.content_fingerprint({
        "schema": "taskplane.loop-stage-store-identity/v1",
        "home": actual_home,
        "run_path": run_path,
        "run_id": run_id,
        "repository_id": repository_id,
        "repository_key": repository_key,
    })


def _stage_bound_run_refusal(ws: str, state: object) -> dict | None:
    """Prove the exact locator/store for a singleton already bound to v4."""
    try:
        binding = _stage_read_run_binding(state)
    except Exception as exc:
        return {
            "error": "stage-native migrated run binding is invalid: "
                     f"{exc}",
            "stage_native": "read-only",
        }
    if binding is None:
        return None
    run_id = str(binding["run_id"])
    try:
        locator = runtime_storage.load_workspace_locator(ws)
    except Exception as exc:
        return {
            "error": "stage-native bound run locator is unreadable: "
                     f"{exc.__class__.__name__}: {exc}",
            "stage_native": "read-only", "run_id": run_id,
        }
    if not isinstance(locator, Mapping):
        return {
            "error": ("stage-native bound run locator is missing; migrated "
                      "v4 history is read-only until the exact locator is "
                      "restored"),
            "stage_native": "read-only", "run_id": run_id,
        }
    expected_locator = {
        "run_id": binding["run_id"],
        "repo_id": binding["repository_id"],
        "repository_key": binding["repository_key"],
    }
    if any(locator.get(key) != value
           for key, value in expected_locator.items()):
        return {
            "error": "stage-native bound run locator identity changed",
            "stage_native": "read-only", "run_id": run_id,
        }
    try:
        store = _stage_store(ws, run_id)
        current_store_fingerprint = _stage_store_identity_fingerprint(
            locator, store_home=getattr(store, "home", None))
        if current_store_fingerprint != binding[
                "store_identity_fingerprint"]:
            return {
                "error": "stage-native bound run store identity changed",
                "stage_native": "read-only", "run_id": run_id,
            }
        manifest = store.load(run_id)
    except Exception as exc:
        return {
            "error": "stage-native bound run store is unavailable: "
                     f"{exc.__class__.__name__}: {exc}",
            "stage_native": "read-only", "run_id": run_id,
        }
    repository = manifest.get("repository")
    heads = manifest.get("stage_heads")
    if manifest.get("schema") != "taskplane.run/v4" or \
            manifest.get("run_id") != run_id or \
            not isinstance(repository, Mapping) or \
            repository.get("repo_id") != binding["repository_id"] or \
            not isinstance(heads, Mapping) or \
            binding["root_stage_id"] not in heads:
        return {
            "error": "stage-native bound v4 run identity is invalid",
            "stage_native": "read-only", "run_id": run_id,
        }
    return None


def _stage_run_binding_value(
        locator: Mapping[str, object], manifest: Mapping[str, object],
        root_stage_id: str, *, store_home: object = None) -> dict:
    """Create the closed path-free binding persisted beside loop state."""
    repository = manifest.get("repository")
    if manifest.get("schema") != "taskplane.run/v4" or \
            not isinstance(repository, Mapping):
        raise ValueError("stage-native root did not produce a v4 run")
    value = {
        "schema": _STAGE_RUN_BINDING_SCHEMA,
        "run_id": str(manifest.get("run_id") or ""),
        "repository_id": str(locator.get("repo_id") or ""),
        "repository_key": str(locator.get("repository_key") or ""),
        "run_schema": "taskplane.run/v4",
        "root_stage_id": str(root_stage_id or ""),
        "store_identity_fingerprint": _stage_store_identity_fingerprint(
            locator, store_home=store_home),
    }
    if repository.get("repo_id") != value["repository_id"] or \
            locator.get("run_id") != value["run_id"] or \
            value["root_stage_id"] not in (manifest.get("stage_heads") or {}):
        raise ValueError("stage-native root binding identity is invalid")
    _stage_read_run_binding({"_stage_run_binding": value})
    return value


def _persist_stage_run_binding(
        ws: str, store: object, *, root_stage_id: str) -> dict:
    """Persist the verified v4 identity before returning any dispatch."""
    locator = runtime_storage.load_workspace_locator(ws)
    if not isinstance(locator, Mapping):
        raise ValueError("stage-native root locator is missing")
    run_id = str(locator.get("run_id") or "")
    manifest = store.load(run_id)
    value = _stage_run_binding_value(
        locator, manifest, root_stage_id,
        store_home=getattr(store, "home", None))
    with mutate(ws) as locked:
        if locked is None:
            raise ValueError("stage-native root singleton is missing")
        prior = _stage_read_run_binding(locked)
        if prior is not None and prior != value:
            raise ValueError("stage-native run binding changed")
        locked["_stage_run_binding"] = value
        locked.pop("_stage_native_new_run_pristine", None)
    return value


def _stage_mutation_blocker(mode: str, manifest: Mapping[str, object],
                            ws: str) \
        -> str | None:
    """Keep rollback readable while pausing every stage-native mutation."""
    schema = manifest.get("schema")
    if mode == "disabled":
        return "stage-native mutation is disabled"
    if schema == "taskplane.run/v3" and mode != "new-run":
        return ("unmigrated run requires TASKPLANE_STAGE_NATIVE=new-run; "
                "legacy loop behavior remains active")
    if schema == "taskplane.run/v3":
        migration_fields = [
            key for key, value in manifest.items()
            if "migration" in str(key).lower() and value not in (
                None, False, "", [], {})
        ]
        singleton = load(ws)
        pristine_new_run = bool(
            isinstance(singleton, Mapping)
            and singleton.get("_stage_native_new_run_pristine") is True
            and singleton.get("tasks") is None
            and singleton.get("current_task", 0) == 0
            and singleton.get("step") in {"pm", "design", "plan"}
            and not any(key in singleton for key in (
                "baseline", "selection", "replan_history", "retro")))
        if not pristine_new_run or migration_fields:
            return ("new-run mode cannot promote an existing singleton or "
                    "migration-bound run; legacy read-only behavior remains "
                    "active")
    if schema not in {"taskplane.run/v3", "taskplane.run/v4"}:
        return "run is not stage-capable"
    return None


def _stage_loop_mutation_refusal(
        ws: str, *, allow_new_run_bootstrap: bool = False) -> dict | None:
    """Pause singleton writes when rollback leaves a migrated v4 readable.

    An unmigrated v3 run remains on the byte-identical legacy path.  The
    locator and manifest are opened only by mutation entry points; disabled
    legacy reads therefore retain the old no-locator behavior.
    """
    try:
        singleton = _load_raw(ws)
    except Exception as exc:
        return {
            "error": "stage-native singleton authority is unreadable: "
                     f"{exc.__class__.__name__}: {exc}",
            "stage_native": "read-only",
        }
    if refusal := _stage_bound_run_refusal(ws, singleton):
        return refusal
    mode = _stage_mode()
    bootstrap_record = (singleton.get("_stage_native_root_authority")
                        if isinstance(singleton, Mapping) else None)
    if bootstrap_record is not None and _stage_read_run_binding(
            singleton) is None:
        pristine = singleton.get("_stage_native_new_run_pristine") is True
        if pristine and mode == "new-run" and allow_new_run_bootstrap:
            return None
        if not pristine:
            return {
                "error": "stage-native migrated run binding is missing",
                "stage_native": "read-only",
            }
        return {
            "error": ("stage-native initialized run requires its first "
                      "`loop next` in TASKPLANE_STAGE_NATIVE=new-run mode "
                      "before any other mutation"),
            "stage_native": "read-only",
        }
    if mode != "disabled":
        return None
    try:
        locator = runtime_storage.load_workspace_locator(ws)
    except Exception as exc:
        return {
            "error": ("stage-native mutation is disabled and rollback "
                      "identity could not be verified: "
                      f"{exc.__class__.__name__}: {exc}"),
            "stage_native": "read-only",
        }
    if not isinstance(locator, Mapping):
        return None
    run_id = str(locator.get("run_id") or "")
    if not run_id:
        return {
            "error": ("stage-native mutation is disabled and rollback run "
                      "identity is invalid"),
            "stage_native": "read-only",
        }
    try:
        manifest = _stage_store(ws, run_id).load(run_id)
    except Exception as exc:
        return {
            "error": ("stage-native mutation is disabled and rollback "
                      "store could not be verified: "
                      f"{exc.__class__.__name__}: {exc}"),
            "stage_native": "read-only", "run_id": run_id,
        }
    if manifest.get("schema") == "taskplane.run/v3":
        return None
    if manifest.get("schema") != "taskplane.run/v4":
        return {
            "error": "stage-native rollback manifest is invalid",
            "stage_native": "read-only", "run_id": run_id,
        }
    return {
        "error": ("stage-native mutation is disabled for this migrated v4 "
                  "run; history remains read-only"),
        "stage_native": "read-only",
        "run_id": run_id,
    }


def _current_stage_authority(
        ws: str, manifest: Mapping[str, object], expected: object) -> dict:
    """Re-resolve repository/worktree facts for the exact supplied binding.

    Actor/session and consolidated-authority revision are immutable receipt
    identities, so they remain from the request.  Facts owned by the current
    run manifest or checkout are replaced with their live values immediately
    before ``StageLifecycle`` commits and the repository validator compares
    the result with the indexed stage's expected binding.
    """
    if not isinstance(expected, Mapping):
        raise ValueError("stage command requires an authority binding")
    current = dict(expected)
    current["run_id"] = manifest.get("run_id")

    repository = manifest.get("repository")
    if isinstance(repository, Mapping):
        if repository.get("repo_id"):
            current["repository_id"] = repository.get("repo_id")
    try:
        locator = runtime_storage.load_workspace_locator(ws)
    except Exception:
        locator = None
    if isinstance(locator, Mapping):
        if locator.get("repo_id"):
            current["repository_id"] = locator.get("repo_id")
        if locator.get("repository_key"):
            current["repository_key"] = locator.get("repository_key")
        if locator.get("run_id") != manifest.get("run_id"):
            raise ValueError("workspace belongs to a different stage run")

    # A non-Git test/legacy checkout has no live revision to substitute.  A
    # real governed checkout always does, and any head drift then fails the
    # exact repository authority comparison rather than being advisory.
    live_revision = tp.git_head(ws)
    if live_revision and live_revision != "unknown":
        current["worktree_revision"] = live_revision

    target = manifest.get("target")
    if isinstance(target, Mapping):
        target_revision = (target.get("revision") or target.get("commit") or
                           target.get("head"))
        if target_revision:
            current["target_revision"] = target_revision
    return current


def _stage_lifecycle(ws: str, store: object, manifest: Mapping[str, object],
                     authority: object):
    """Lazy-load the t02 kernel; importing loop.py alone stays legacy-safe."""
    try:
        if __package__:
            from . import repository as stage_repository
            from . import stage_entities
        else:
            import repository as stage_repository
            import stage_entities
    except ImportError:
        # Package-style imports are not reliable when tp.py is executed as a
        # script from inside taskplane/.  Mirror the repository's established
        # dual-import convention without making stage_entities eager.
        import repository as stage_repository
        import stage_entities

    return stage_entities, stage_entities.StageLifecycle(
        store, workspace=ws,
        authority_resolver=lambda _current: _current_stage_authority(
            ws, _current, authority),
        authority_validator=stage_repository.revalidate_stage_authority)


def _indexed_stage(store: object, manifest: Mapping[str, object],
                   run_id: str, stage_id: str) -> dict:
    heads = manifest.get("stage_heads")
    if not isinstance(heads, Mapping) or stage_id not in heads:
        raise ValueError("stage is not indexed")
    head = heads[stage_id]
    if not isinstance(head, Mapping) or not isinstance(
            head.get("object"), Mapping):
        raise ValueError("stage head is invalid")
    read = getattr(store, "read_stage_object", None)
    if not callable(read):
        raise ValueError("stage store cannot read immutable objects")
    return read(run_id, dict(head["object"]))


def _verified_stage_handoff(lifecycle: object, store: object,
                            manifest: Mapping[str, object], stage: dict) \
        -> dict | None:
    """Resolve only the successor's selected, authority-bound handoff."""
    predecessors = list(stage.get("predecessor_stage_ids") or [])
    parents = list(stage.get("parent_stage_ids") or [])
    if not predecessors and parents:
        failures: list[Exception] = []
        for parent_id in parents:
            try:
                producer = _indexed_stage(
                    store, manifest, str(stage["run_id"]), str(parent_id))
                # A split handoff carries the explicit non-default reuse
                # authorization for its closed parent.  Route it through the
                # lifecycle verifier so that authorization is checked rather
                # than treating the child as an ordinary root consumer.
                return lifecycle._read_handoff(  # noqa: SLF001
                    stage["input_manifest_ref"], producer=producer,
                    consumer=stage)
            except Exception as exc:
                failures.append(exc)
        raise ValueError("no verified split-parent handoff is dispatchable") \
            from failures[-1]
    if not predecessors:
        # Root stages still cross the same bounded input boundary.  They have
        # no producer aggregate to compare, but their immutable reference is
        # verified against exact stage authority and selected artifacts before
        # the root is dispatchable.
        try:
            if __package__:
                from . import stage_handoff
            else:
                import stage_handoff
        except ImportError:
            import stage_handoff
        authority = stage.get("authority")
        if not isinstance(authority, Mapping):
            raise ValueError("root stage authority is invalid")
        return stage_handoff.read_manifest(
            lifecycle._artifact_store(),  # noqa: SLF001
            stage["input_manifest_ref"],
            expected_authority_revision=int(authority["authority_revision"]),
            expected_authority_fingerprint=str(
                authority["authority_fingerprint"]))
    failures: list[Exception] = []
    for predecessor_id in predecessors:
        try:
            producer = _indexed_stage(
                store, manifest, str(stage["run_id"]), str(predecessor_id))
            # StageLifecycle owns the producer/consumer binding checks.  This
            # read is deliberately after a successful receipt and does not
            # inspect an execution tree or any predecessor runtime record.
            return lifecycle._read_handoff(  # noqa: SLF001
                stage["input_manifest_ref"], producer=producer,
                consumer=stage)
        except Exception as exc:
            failures.append(exc)
    raise ValueError("no verified predecessor handoff is dispatchable") \
        from failures[-1]


def _stage_dispatch(store: object, lifecycle: object,
                    receipt: Mapping[str, object], stage: dict, *,
                    attempt_id: str | None = None,
                    declared_scope: object = None) -> dict:
    """Build the path-free, bounded runtime envelope for a fresh attempt."""
    verify = getattr(tp, "verify_stage_receipt", None)
    runtime = getattr(tp, "stage_runtime_dispatch", None)
    if not callable(verify) or not callable(runtime):
        raise ValueError("stage runtime serializer is unavailable")
    operation = str(receipt.get("operation") or "")
    checked_receipt = verify(
        receipt, expected_operation=operation,
        expected_stage_id=str(stage["stage_id"]))
    current = store.load(str(stage["run_id"]))
    handoff = _verified_stage_handoff(
        lifecycle, store, current, stage)
    return runtime(
        stage, checked_receipt, handoff,
        stage.get("selected_artifacts") or [], attempt_id=attempt_id,
        declared_scope=declared_scope)


def _preflight_stage_dispatch(stage: dict, handoff: dict,
                              declared_scope: object = None) -> None:
    """Prove bounded startup serialization before committing a new head."""
    compatibility = getattr(tp, "stage_dispatch_payload", None)
    if not callable(compatibility):
        raise ValueError("stage runtime serializer is unavailable")
    claim = {
        "schema": "taskplane.stage-execution-root-claim/v1",
        "run_id": stage["run_id"],
        "stage_id": stage["stage_id"],
        "execution_root_id": stage["execution_root_id"],
    }
    compatibility(
        stage, handoff, stage.get("selected_artifacts") or [], claim,
        declared_scope=declared_scope)


def _stage_bootstrap_pristine_root(
        ws: str, state: Mapping[str, object]) -> dict | None:
    """Commit the attributable first root before normal loop dispatch.

    The root is derived only from init-captured authority and the retained
    requirement. Content-addressed inputs make a crash between the v4 commit
    and singleton binding replay the same lifecycle operation.
    """
    if _stage_mode() != "new-run" or _stage_read_run_binding(state) is not None:
        return None
    if state.get("_stage_native_new_run_pristine") is not True:
        return None
    raw_authority = state.get("_stage_native_root_authority")
    if not isinstance(raw_authority, Mapping) or \
            set(raw_authority) != _STAGE_ROOT_AUTHORITY_FIELDS:
        raise ValueError("stage-native root bootstrap authority is invalid")
    root_authority = dict(raw_authority)

    try:
        if __package__:
            from . import design_contract as stage_design_contract
            from . import review_evidence, stage_handoff
        else:
            import design_contract as stage_design_contract
            import review_evidence
            import stage_handoff
    except ImportError:
        import design_contract as stage_design_contract
        import review_evidence
        import stage_handoff

    authority_material = dict(root_authority)
    authority_fingerprint = str(authority_material.pop("fingerprint", ""))
    if root_authority.get("schema") != _STAGE_ROOT_AUTHORITY_SCHEMA or \
            review_evidence.content_fingerprint(
                authority_material) != authority_fingerprint:
        raise ValueError("stage-native root bootstrap authority is invalid")
    session_id = str(
        os.environ.get("TASKPLANE_SESSION_ID") or
        os.environ.get("CODEX_THREAD_ID") or
        os.environ.get("CLAUDE_SESSION_ID") or "").strip()
    if session_id != root_authority.get("session_id"):
        raise ValueError("stage-native root bootstrap session changed")

    locator = runtime_storage.load_workspace_locator(ws)
    if not isinstance(locator, Mapping):
        raise ValueError("stage-native root bootstrap locator is missing")
    run_id = str(root_authority["run_id"])
    if locator.get("run_id") != run_id or \
            locator.get("repo_id") != root_authority["repository_id"] or \
            locator.get("repository_key") != root_authority["repository_key"]:
        raise ValueError("stage-native root bootstrap locator changed")
    store = _stage_store(ws, run_id)
    manifest = store.load(run_id)
    repository = manifest.get("repository")
    target = manifest.get("target")
    target_revision = (target.get("revision") or target.get("head")
                       if isinstance(target, Mapping) else None)
    if manifest.get("schema") not in {"taskplane.run/v3", "taskplane.run/v4"} or \
            not isinstance(repository, Mapping) or \
            repository.get("repo_id") != root_authority["repository_id"] or \
            target_revision != root_authority["target_revision"] or \
            tp.git_head(ws) != root_authority["worktree_revision"]:
        raise ValueError("stage-native root bootstrap authority changed")

    requirement_id = str(root_authority["requirement_id"])
    requirement = reqs.get_requirement(ws, requirement_id)
    if requirement is None or state.get("requirement_id") != requirement_id:
        raise ValueError("stage-native root bootstrap requirement is missing")
    requirement_fingerprint = stage_design_contract.requirement_fingerprint(
        ws, requirement_id)
    if requirement_fingerprint != root_authority["requirement_revision"] or \
            requirement_fingerprint != root_authority[
                "requirement_fingerprint"]:
        raise ValueError("stage-native root bootstrap requirement changed")

    step = str(state.get("step") or "")
    kind = _LOOP_STAGE_KINDS.get(step)
    if kind not in {"product", "design", "plan"}:
        raise ValueError("stage-native root bootstrap step is invalid")
    requirement_identity = {
        "id": requirement_id,
        "revision": requirement_fingerprint,
        "fingerprint": requirement_fingerprint,
    }
    contract_groups = {"provided": [], "consumed": [], "changed": []}
    contracts = []
    for row in requirement.get("contracts") or []:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
            continue
        contract_id = str(row["id"])
        contracts.append(contract_id)
        relation = str(row.get("relation") or "").lower()
        group = ("provided" if "provide" in relation else
                 "changed" if "change" in relation else "consumed")
        contract_groups[group].append(contract_id)
    contract_groups = {
        key: sorted(set(values)) for key, values in contract_groups.items()
    }
    contracts = sorted(set(contracts))
    root_input = {
        "schema": "taskplane.loop-root-input/v1",
        "run_id": run_id,
        "step": step,
        "stage_kind": kind,
        "goal": str(state.get("goal") or ""),
        "spec_path": state.get("spec_path"),
        "requirement": dict(requirement),
    }
    artifact_store = review_evidence.ArtifactStore(ws)
    selected_native = artifact_store.put("root-input", root_input)
    authority_native = artifact_store.put("root-authority", {
        "schema": "taskplane.loop-root-authority-evidence/v1",
        "authority": root_authority,
    })
    selected = review_evidence.portable_artifact_reference(
        artifact_store, selected_native)
    authorized_at = f"{str(requirement.get('date') or '1970-01-01')}T00:00:00Z"
    handoff = stage_handoff.create_manifest(
        artifact_store, producer_stage_id=f"input-{run_id}",
        producer_outcome="done", requirement=requirement_identity,
        design=None, target=None, commit=None, contracts=contract_groups,
        deliverables=["root-input"],
        evidence_references=[authority_native],
        selected_artifacts=[selected_native],
        exclusions=sorted(stage_handoff.REQUIRED_EXCLUSIONS),
        authorization={
            "actor": root_authority["actor"],
            "session_id": root_authority["session_id"],
            "authorized_at": authorized_at,
            "operation_id": "authorize-root-" + authority_fingerprint[:32],
            "authority_record": {
                "schema": "taskplane.authority-record-reference/v1",
                "authority_schema": "taskplane.consolidated-authorization/v1",
                "revision": root_authority["authority_revision"],
                "fingerprint": authority_fingerprint,
            },
            "nonconsumable_reuse": None,
        })
    input_ref = review_evidence.portable_artifact_reference(
        artifact_store, stage_handoff.store_manifest(artifact_store, handoff))

    stage_authority = {
        "schema": "taskplane.stage-authority-binding/v1",
        **{key: root_authority[key] for key in (
            "run_id", "repository_id", "repository_key", "worktree_id",
            "target_revision", "worktree_revision", "requirement_id",
            "requirement_revision", "actor", "session_id",
            "authority_revision")},
        "design_revision": None,
        "design_fingerprint": None,
        "authority_fingerprint": authority_fingerprint,
    }
    root_identity = {
        "schema": "taskplane.loop-root-stage-identity/v1",
        "run_id": run_id, "stage_kind": kind,
        "requirement": requirement_identity,
        "input_manifest_fingerprint": input_ref["fingerprint"],
        "authority_fingerprint": authority_fingerprint,
    }
    root_token = review_evidence.content_fingerprint(root_identity)[:32]
    stage_id = f"stage-{kind}-root-{root_token}"
    stage_entities, lifecycle = _stage_lifecycle(
        ws, store, manifest, stage_authority)
    stage = stage_entities.create_stage(
        run_id=run_id, stage_id=stage_id,
        requirement=requirement_identity, design=None, stage_kind=kind,
        parent_stage_ids=[], predecessor_stage_ids=[],
        input_manifest_ref=input_ref,
        execution_root_id=f"execution-{stage_id}",
        deliverables=_stage_loop_deliverables(kind, state),
        selected_artifacts=[selected],
        budget={"attempt_limit": max(
            1, int(state.get("max_fix_cycles") or 0) + 1)},
        dependencies=sorted(set(str(value) for value in
                                (requirement.get("depends_on") or []))),
        contracts=contracts, authority=stage_authority,
        created_at=authorized_at)
    root_contract = _step_contract(step, dict(state), ws)
    declared_scope = _stage_loop_scope(
        root_contract["coding"]["scope_paths"],
        root_contract["coding"].get("out_of_scope_paths") or [])
    _preflight_stage_dispatch(stage, handoff, declared_scope)
    operation_id = "bootstrap-root-" + stage["fingerprint"][:32]
    receipt = lifecycle.start_stage(
        stage, expected_revision=int(manifest["revision"]),
        operation_id=operation_id,
        expected_predecessor_fingerprints={}, foreground=True)
    checked = tp.verify_stage_receipt(
        receipt, expected_operation="start_stage",
        expected_stage_id=stage_id)
    _persist_stage_run_binding(ws, store, root_stage_id=stage_id)
    return checked


_LOOP_STAGE_KINDS = {
    "pm": "product", "design": "design", "design_approval": "design",
    "plan": "plan", "plan_approval": "plan", "execute": "build",
    "fix": "build", "evaluate": "evaluate", "selection": "evaluate",
    "em": "engineering", "signoff": "engineering",
    "escalated": "engineering", "retro": "retro",
}


def _stage_loop_context(
        ws: str, state: Mapping[str, object] | None = None, *,
        stage_id: str | None = None) -> dict | None:
    """Resolve an enabled v4 foreground without changing legacy state."""
    if _stage_mode() == "disabled":
        return None
    locator = runtime_storage.load_workspace_locator(ws)
    if not isinstance(locator, Mapping):
        return None
    run_id = str(locator.get("run_id") or "")
    if not run_id:
        return None
    store = _stage_store(ws, run_id)
    manifest = store.load(run_id)
    # Existing singleton runs remain byte-identical until migration commits.
    if manifest.get("schema") == "taskplane.run/v3" and \
            _stage_mode() == "new-run":
        raise ValueError(
            "stage-native new-run requires a committed root stage before "
            "dispatch")
    if manifest.get("schema") != "taskplane.run/v4":
        return None
    projection = manifest.get("active_stage_projection")
    if not isinstance(projection, Mapping):
        raise ValueError("stage-native loop projection is unavailable")
    active = list(projection.get("active_stage_ids") or [])
    foreground = stage_id or projection.get("foreground_stage_id")
    if foreground is None and isinstance(state, Mapping):
        task = _current_task(dict(state)) or {}
        task_id = str(task.get("id") or "")
        bindings = state.get("_stage_bindings")
        task_bindings = (bindings.get(task_id)
                         if isinstance(bindings, Mapping) else None)
        kind = _LOOP_STAGE_KINDS.get(str(state.get("step") or ""))
        if isinstance(task_bindings, Mapping) and kind:
            bound = task_bindings.get(kind)
            if isinstance(bound, str) and bound:
                foreground = bound
    if foreground is None and len(active) == 1:
        foreground = active[0]
    if foreground is not None and foreground not in active:
        raise ValueError("stage-native loop foreground is invalid")
    if len(active) > 1 and foreground is None:
        raise ValueError("stage-native loop foreground is ambiguous")
    if foreground is None:
        return {"store": store, "manifest": manifest, "run_id": run_id,
                "stage": None, "lifecycle": None, "stage_entities": None}
    stage = _indexed_stage(store, manifest, run_id, str(foreground))
    stage_entities, lifecycle = _stage_lifecycle(
        ws, store, manifest, stage.get("authority"))
    return {"store": store, "manifest": manifest, "run_id": run_id,
            "stage": stage, "lifecycle": lifecycle,
            "stage_entities": stage_entities}


def _stage_loop_identity(stage_entities: object, prefix: str,
                         material: Mapping[str, object]) -> str:
    fingerprint = stage_entities.request_fingerprint(dict(material))
    return prefix + fingerprint[:32]


def _stage_loop_dispatch(
        ws: str, state: Mapping[str, object], *, slot: str,
        declared_scope: Mapping[str, object] | None = None,
        stage_id: str | None = None) -> dict | None:
    """Resume the exact foreground stage and return its bounded dispatch."""
    context = _stage_loop_context(ws, state, stage_id=stage_id)
    if context is None:
        return None
    stage = context.get("stage")
    lifecycle = context.get("lifecycle")
    stage_entities = context.get("stage_entities")
    if not isinstance(stage, dict) or lifecycle is None:
        raise ValueError("stage-native loop has no active foreground stage")
    if stage_entities is None:
        try:
            if __package__:
                from . import stage_entities as stage_entities_module
            else:
                import stage_entities as stage_entities_module
        except ImportError:
            import stage_entities as stage_entities_module
        stage_entities = stage_entities_module
    step = str(state.get("step") or "")
    expected_kind = _LOOP_STAGE_KINDS.get(step)
    if expected_kind is None or stage.get("stage_kind") != expected_kind:
        raise ValueError(
            f"stage-native loop expected {expected_kind or step!r}, found "
            f"{stage.get('stage_kind')!r}")
    identity = {
        "schema": "taskplane.loop-stage-dispatch/v1",
        "run_id": stage["run_id"], "stage_id": stage["stage_id"],
        "stage_fingerprint": stage["fingerprint"], "step": step,
        "slot": str(slot),
    }
    operation_id = _stage_loop_identity(
        stage_entities, "loop-dispatch-", identity)
    attempt_id = _stage_loop_identity(
        stage_entities, "attempt-", {**identity, "operation_id": operation_id})
    receipt = lifecycle.resume_stage(
        str(stage["run_id"]), stage_id=str(stage["stage_id"]),
        expected_head_fingerprint=str(stage["fingerprint"]),
        expected_revision=int(context["manifest"]["revision"]),
        operation_id=operation_id, attempt_id=attempt_id)
    return _stage_dispatch(
        context["store"], lifecycle, receipt, stage,
        attempt_id=attempt_id, declared_scope=declared_scope)


def _stage_loop_deliverables(kind: str, state: Mapping[str, object]) -> list[str]:
    if kind == "build":
        current = _current_task(dict(state)) or {}
        if current.get("id"):
            return [str(current["id"])]
        return ["build-output"]
    return {
        "product": ["product-requirement"],
        "design": ["design-contract"],
        "plan": ["implementation-plan"],
        "evaluate": ["evaluation-verdict"],
        "engineering": ["engineering-review"],
        "retro": ["retrospective"],
    }.get(kind, [f"{kind}-output"])


def _stage_loop_scope(scope_paths: object,
                      out_of_scope_paths: object = ()) -> dict:
    return {
        "scope_paths": sorted(set(str(row) for row in
                                  (scope_paths or []) if str(row).strip())),
        "out_of_scope_paths": sorted(set(str(row) for row in
                                         (out_of_scope_paths or [])
                                         if str(row).strip())),
    }


def _stage_loop_completion_reference(
        ws: str, lifecycle: object, stage: Mapping[str, object], *,
        from_step: str, to_step: str, completion: Mapping[str, object]) \
        -> dict:
    """Commit the exact stage result before it can terminalize ``done``.

    A predecessor input handoff is startup context, never proof that the
    current stage completed.  This artifact is consequently made only from
    the gate/approval/Retro result that authorized this transition.
    """
    if not isinstance(completion, Mapping) or not completion:
        raise ValueError(
            "stage-native loop transition lacks committed completion evidence")
    try:
        detached = json.loads(json.dumps(
            dict(completion), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("stage completion result is not canonical JSON") \
            from exc
    artifact_store = lifecycle._artifact_store()  # noqa: SLF001
    value = {
        "schema": "taskplane.loop-stage-completion/v1",
        "run_id": stage["run_id"],
        "stage_id": stage["stage_id"],
        "stage_fingerprint": stage["fingerprint"],
        "from_step": str(from_step),
        "to_step": str(to_step),
        "workspace_revision": tp.git_head(ws),
        "result": detached,
    }
    native = artifact_store.put("completion-evidence", value)
    try:
        if __package__:
            from . import review_evidence
        else:
            import review_evidence
    except ImportError:
        import review_evidence
    return review_evidence.portable_artifact_reference(
        artifact_store, native)


_STAGE_OUTPUT_MAX_SOURCES = 64
_STAGE_OUTPUT_MAX_FILE_BYTES = 8 * 1024 * 1024
_STAGE_OUTPUT_MAX_TOTAL_BYTES = 16 * 1024 * 1024


def _before_stage_output_component_open(
        root: str, relative_path: str, component_index: int) -> None:
    """Deterministic no-op seam for substitution regression tests."""


def _stage_loop_open_directory_no_follow(path: str) -> int:
    """Open an absolute directory one no-follow component at a time."""
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise ValueError("race-safe stage output open is unavailable")
    absolute = os.path.abspath(path)
    drive, tail = os.path.splitdrive(absolute)
    anchor = drive + os.sep if absolute.startswith(os.sep) else drive
    if not anchor:
        raise ValueError("stage output root is not absolute")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | \
        getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(anchor, flags)
    try:
        for component in [part for part in tail.split(os.sep) if part]:
            opened = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = opened
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _stage_loop_read_output_no_follow(
        root: str, relative_path: str, *, required: bool,
        remaining_bytes: int) -> bytes | None:
    """Read one regular file through a descriptor-relative confined walk."""
    components = relative_path.split("/")
    if not components or any(part in {"", ".", ".."}
                             for part in components):
        raise ValueError("stage completion output path is not canonical")
    root_fd = _stage_loop_open_directory_no_follow(root)
    descriptor = root_fd
    try:
        for index, component in enumerate(components):
            _before_stage_output_component_open(root, relative_path, index)
            flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            if index < len(components) - 1:
                flags |= os.O_DIRECTORY
            try:
                opened = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not required:
                    return None
                raise ValueError(
                    f"required stage completion output is missing: "
                    f"{relative_path}") from None
            if descriptor != root_fd:
                os.close(descriptor)
            descriptor = opened
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("stage completion output is not a regular file")
        limit = min(_STAGE_OUTPUT_MAX_FILE_BYTES, remaining_bytes)
        if before.st_size > limit:
            raise ValueError("stage completion output exceeds its byte bound")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(128 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ValueError(
                    "stage completion output exceeds its byte bound")
        after = os.fstat(descriptor)
        stable = (
            stat.S_ISREG(after.st_mode) and before.st_dev == after.st_dev and
            before.st_ino == after.st_ino and before.st_size == after.st_size and
            before.st_mtime_ns == after.st_mtime_ns and total == after.st_size)
        if not stable:
            raise ValueError("stage completion output changed while sealing")
        return b"".join(chunks)
    except OSError as exc:
        raise ValueError(
            f"stage completion output could not be opened safely: {exc}") \
            from exc
    finally:
        if descriptor != root_fd:
            os.close(descriptor)
        os.close(root_fd)


def _stage_loop_trusted_output_workspace(
        lifecycle: object, completion: Mapping[str, object],
        output: Mapping[str, object]) -> tuple[str, object]:
    """Derive the capture root from stage storage and registered submission."""
    artifact_store = lifecycle._artifact_store()  # noqa: SLF001
    current_workspace = os.path.abspath(str(artifact_store.workspace))
    raw = str(output.get("source_workspace") or "").strip()
    if not raw or not os.path.isabs(raw) or os.path.normpath(raw) != raw:
        raise ValueError("stage completion output workspace is unavailable")
    claimed = os.path.abspath(raw)
    if claimed == current_workspace:
        return claimed, artifact_store

    submission = completion.get("submission")
    task_id = str(completion.get("task_id") or "")
    if not isinstance(submission, Mapping) or not str(
            submission.get("fingerprint") or "") or not task_id or \
            str(submission.get("task") or "") != task_id:
        raise ValueError("stage completion output workspace is unauthorized")
    expected = os.path.abspath(runtime_storage.task_worktree_path(
        current_workspace, task_id))
    if claimed != expected:
        raise ValueError("stage completion output workspace is unauthorized")
    current_locator = runtime_storage.load_workspace_locator(current_workspace)
    source_locator = runtime_storage.load_workspace_locator(claimed)
    registration = runtime_storage.load_task_worktree_registration(
        current_workspace, task_id)
    identity = ("run_id", "repo_id", "repository_key")
    if not isinstance(current_locator, Mapping) or \
            not isinstance(source_locator, Mapping) or \
            not isinstance(registration, Mapping) or any(
                current_locator.get(key) != source_locator.get(key)
                for key in identity) or \
            registration.get("run_id") != current_locator.get("run_id") or \
            registration.get("task_id") != task_id or \
            os.path.abspath(str(registration.get("path") or "")) != claimed or \
            os.path.abspath(str(
                registration.get("primary_checkout") or "")) != \
            current_workspace:
        raise ValueError("stage completion submission workspace is unbound")
    step = str(submission.get("step") or "")
    if step not in {"execute", "fix", "evaluate", "em"}:
        raise ValueError("stage completion submission step is invalid")
    evidence_paths = runtime_storage.submission_evidence_paths(claimed, step)
    if tp.workspace_fingerprint(
            claimed, submission.get("snapshot"),
            extra_paths=evidence_paths) != submission.get("fingerprint"):
        raise ValueError("stage completion submission workspace is stale")
    return claimed, artifact_store


def _stage_loop_managed_evidence_paths(
        source_workspace: str, completion: Mapping[str, object],
        output: Mapping[str, object]) -> dict[str, tuple[str, str]]:
    """Prove exact external evidence paths from the validated submission."""
    step = str(output.get("managed_evidence_step") or "")
    submission = completion.get("submission")
    if step not in {"evaluate", "em"}:
        return {}
    if not isinstance(submission, Mapping) or not str(
            submission.get("fingerprint") or ""):
        raise ValueError("managed completion evidence lacks its submission")
    locator = runtime_storage.load_workspace_locator(source_workspace)
    if not isinstance(locator, Mapping):
        raise ValueError("managed completion evidence lacks its run locator")
    paths = locator.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("managed completion evidence roots are unavailable")
    roots = []
    for key in (("evidence",) if step == "evaluate" else ("artifacts",)):
        raw_root = str(paths.get(key) or "").strip()
        if not raw_root or not os.path.isabs(raw_root):
            raise ValueError("managed completion evidence root is unavailable")
        supplied_root = os.path.abspath(raw_root)
        if os.path.normpath(supplied_root) != supplied_root:
            raise ValueError("managed completion evidence root is not canonical")
        roots.append(supplied_root)
    allowed = {}
    for expected in runtime_storage.submission_evidence_paths(
            source_workspace, step):
        canonical = os.path.abspath(str(expected))
        if os.path.normpath(canonical) != canonical:
            raise ValueError("managed completion evidence path is not canonical")
        try:
            contained = any(os.path.commonpath((root, canonical)) == root
                            for root in roots)
        except ValueError:
            contained = False
        if not contained:
            raise ValueError("managed completion evidence escaped its run root")
        root = next(root for root in roots
                    if os.path.commonpath((root, canonical)) == root)
        relative = os.path.relpath(canonical, root).replace(os.sep, "/")
        allowed[canonical] = (root, relative)
    return allowed


def _stage_loop_decision_completion(
        ws: str, *, schema: str, step: str, outcome: str,
        result: Mapping[str, object]) -> dict:
    """Create one bounded, file-free artifact for a control decision."""
    try:
        encoded = json.dumps(
            dict(result), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("stage control decision is not canonical JSON") \
            from exc
    if len(encoded.encode("utf-8")) > 16 * 1024:
        raise ValueError("stage control decision exceeds its 16 KiB bound")
    detached = json.loads(encoded)
    return {
        "schema": str(schema), "step": str(step),
        "outcome": str(outcome), "workspace_revision": tp.git_head(ws),
        **detached,
        "_stage_output": {
            "source_workspace": os.path.abspath(ws),
            "sources": [], "values": {"decision": detached},
        },
    }


def _stage_loop_completion_outputs(
        lifecycle: object, completion: Mapping[str, object]) \
        -> tuple[dict, list[dict], dict | None, dict | None]:
    """Seal current-stage outputs and derive an exact Build target pair."""
    if not isinstance(completion, Mapping) or not completion:
        raise ValueError("stage completion result is missing")
    output = completion.get("_stage_output")
    if not isinstance(output, Mapping):
        raise ValueError("stage completion output declaration is missing")
    source_workspace, artifact_store = _stage_loop_trusted_output_workspace(
        lifecycle, completion, output)
    managed_evidence = _stage_loop_managed_evidence_paths(
        source_workspace, completion, output)

    snapshots = []
    seen = set()
    sources = output.get("sources") or []
    if not isinstance(sources, list) or \
            len(sources) > _STAGE_OUTPUT_MAX_SOURCES:
        raise ValueError("stage completion output source count is invalid")
    captured_bytes = 0
    for index, raw in enumerate(sources):
        if not isinstance(raw, Mapping):
            raise ValueError("stage completion output source is invalid")
        supplied = str(raw.get("path") or "").strip()
        if not supplied:
            raise ValueError("stage completion output path is missing")
        if os.path.isabs(supplied):
            path = os.path.abspath(supplied)
            if path not in managed_evidence:
                raise ValueError(
                    "absolute stage completion output path is unauthorized")
            root, relative = managed_evidence[path]
        else:
            normalized = supplied.replace("\\", "/")
            components = normalized.split("/")
            if any(component in {"", ".", ".."} for component in components):
                raise ValueError(
                    "stage completion output path is not canonical")
            root, relative = source_workspace, normalized
        logical = str(raw.get("logical_path") or "").strip()
        if not logical:
            logical = (relative if not os.path.isabs(supplied) else
                       f"evidence/{index:03d}-{os.path.basename(path)}")
        logical = logical.replace("\\", "/")
        logical_components = logical.split("/")
        if os.path.isabs(logical) or not logical or any(
                component in {"", ".", ".."}
                for component in logical_components):
            raise ValueError("stage completion output path is not relative")
        if logical in seen:
            continue
        seen.add(logical)
        data = _stage_loop_read_output_no_follow(
            root, relative, required=raw.get("required") is True,
            remaining_bytes=_STAGE_OUTPUT_MAX_TOTAL_BYTES - captured_bytes)
        if data is None:
            continue
        captured_bytes += len(data)
        import base64
        import hashlib
        try:
            content = data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            content = base64.b64encode(data).decode("ascii")
            encoding = "base64"
        snapshots.append({
            "schema": "taskplane.loop-stage-output-snapshot/v1",
            "path": logical, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "encoding": encoding, "content": content,
        })

    public = {str(key): value for key, value in completion.items()
              if not str(key).startswith("_stage_")}
    bundle = {
        "schema": "taskplane.loop-stage-output-bundle/v1",
        "step": public.get("step"), "outcome": public.get("outcome"),
        "task_id": public.get("task_id"),
        "files": sorted(snapshots, key=lambda row: row["path"]),
        "values": dict(output.get("values") or {}),
    }
    native = artifact_store.put("stage-output", bundle)
    try:
        if __package__:
            from . import review_evidence
        else:
            import review_evidence
    except ImportError:
        import review_evidence
    artifacts = [review_evidence.portable_artifact_reference(
        artifact_store, native)]
    public["artifact_refs"] = artifacts

    target = commit = None
    build = output.get("build")
    if build is not None:
        if not isinstance(build, Mapping):
            raise ValueError("Build completion identity is invalid")
        sha = str(build.get("target_commit") or
                  tp.git_head(source_workspace) or "").strip()
        if len(sha) not in {40, 64} or any(
                character not in "0123456789abcdef" for character in sha):
            raise ValueError("Build completion target commit is unavailable")
        identity = runtime_storage.resolve_repository_identity(
            source_workspace)
        target_material = {
            "schema": "taskplane.loop-build-target/v1",
            "repository_id": identity.repo_id,
            "task_id": str(public.get("task_id") or ""), "sha": sha,
        }
        target_fingerprint = review_evidence.content_fingerprint(
            target_material)
        target = {"repository_id": identity.repo_id,
                  "fingerprint": target_fingerprint}
        commit = {"sha": sha, "target_fingerprint": target_fingerprint}
    public["target"] = target
    public["commit"] = commit
    return public, artifacts, target, commit


def _stage_loop_transition_operation_material(
        state: Mapping[str, object], *, run_id: str, from_step: str,
        to_step: str, from_kind: str, to_kind: str | None,
        terminal_outcome: str, terminal_only: bool,
        predecessor_stage_id: object,
        predecessor_head_fingerprint: object) -> dict:
    """Bind one loop transition to its exact immutable predecessor head."""
    current_task = _current_task(dict(state)) or {}
    return {
        "schema": "taskplane.loop-stage-transition/v1",
        "run_id": str(run_id), "from_step": str(from_step),
        "to_step": str(to_step), "from_kind": str(from_kind),
        "to_kind": to_kind, "outcome": str(terminal_outcome),
        "predecessor_stage_id": predecessor_stage_id,
        "predecessor_head_fingerprint": predecessor_head_fingerprint,
        "task_id": current_task.get("id"),
        "fix_cycles": int(current_task.get("fix_cycles") or 0),
        "terminal_only": bool(terminal_only),
    }


def _stage_loop_replayed_transition(
        ws: str, state: Mapping[str, object], *, from_step: str,
        to_step: str, from_kind: str, to_kind: str | None,
        terminal_outcome: str, terminal_only: bool,
        completion: Mapping[str, object] | None) -> dict | None:
    """Recover an exact stage commit made before singleton persistence.

    The active foreground is already the successor after
    ``terminalize_and_start``.  Deriving a fresh operation id from that head
    would both miss the committed receipt and fail kind validation.  Instead,
    verify the predecessor completion artifact, reconstruct the original
    predecessor-bound identity, and accept only the receipt whose current
    heads and lineage still match the durable aggregate.
    """
    if _stage_mode() == "disabled" or not isinstance(completion, Mapping):
        return None
    locator = runtime_storage.load_workspace_locator(ws)
    if not isinstance(locator, Mapping) or not locator.get("run_id"):
        return None
    run_id = str(locator["run_id"])
    store = _stage_store(ws, run_id)
    manifest = store.load(run_id)
    if manifest.get("schema") != "taskplane.run/v4":
        return None
    try:
        if __package__:
            from . import stage_entities as stage_entities_module
        else:
            import stage_entities as stage_entities_module
    except ImportError:
        import stage_entities as stage_entities_module
    operations = manifest.get("stage_operations") or {}
    heads = manifest.get("stage_heads") or {}
    if not isinstance(operations, Mapping) or not isinstance(heads, Mapping):
        raise ValueError("stage transition operation index is invalid")
    expected_operation = (
        "terminalize" if to_kind is None or terminal_only
        else "terminalize_and_start")
    candidates: list[dict] = []
    for raw in operations.values():
        if not isinstance(raw, dict) or raw.get("operation") != \
                expected_operation or not str(
                    raw.get("operation_id") or "").startswith(
                        "loop-transition-"):
            continue
        checked = tp.verify_stage_receipt(
            raw, expected_operation=expected_operation)
        result = checked.get("result") or {}
        predecessor_head = (result.get("head")
                            if expected_operation == "terminalize" else
                            result.get("predecessor_head"))
        if not isinstance(predecessor_head, Mapping) or not isinstance(
                predecessor_head.get("summary"), Mapping):
            raise ValueError("stage transition predecessor receipt is invalid")
        predecessor_id = str(
            predecessor_head["summary"].get("stage_id") or "")
        if not predecessor_id or heads.get(predecessor_id) != predecessor_head:
            continue
        predecessor = _indexed_stage(
            store, manifest, run_id, predecessor_id)
        if predecessor.get("stage_kind") != from_kind or \
                predecessor.get("state") != "terminal" or \
                predecessor.get("outcome") != terminal_outcome:
            continue
        terminal = predecessor.get("terminal") or {}
        evidence = terminal.get("completion_evidence") or []
        if not isinstance(evidence, list):
            raise ValueError("stage transition completion evidence is invalid")
        _entities, lifecycle = _stage_lifecycle(
            ws, store, manifest, predecessor.get("authority"))
        processed, _artifacts, _target, _commit = \
            _stage_loop_completion_outputs(lifecycle, completion)
        artifact_store = lifecycle._artifact_store()  # noqa: SLF001
        completion_rows = []
        for reference in evidence:
            try:
                value = artifact_store.read(reference)
            except Exception:
                continue
            if isinstance(value, Mapping) and value.get("schema") == \
                    "taskplane.loop-stage-completion/v1":
                completion_rows.append(value)
        exact = [row for row in completion_rows
                 if row.get("run_id") == run_id and
                 row.get("stage_id") == predecessor_id and
                 row.get("from_step") == from_step and
                 row.get("to_step") == to_step and
                 row.get("result") == processed]
        if len(exact) != 1:
            continue
        predecessor_fingerprint = str(
            exact[0].get("stage_fingerprint") or "")
        material = _stage_loop_transition_operation_material(
            state, run_id=run_id, from_step=from_step, to_step=to_step,
            from_kind=from_kind, to_kind=to_kind,
            terminal_outcome=terminal_outcome,
            terminal_only=terminal_only,
            predecessor_stage_id=predecessor_id,
            predecessor_head_fingerprint=predecessor_fingerprint)
        operation_id = _stage_loop_identity(
            stage_entities_module, "loop-transition-", material)
        if operation_id != checked.get("operation_id"):
            continue
        if expected_operation == "terminalize":
            if checked.get("stage_ids") != [predecessor_id] or \
                    manifest.get("active_stage_projection") != \
                    result.get("active_stage_projection"):
                continue
        else:
            successor_head = result.get("successor_head")
            if not isinstance(successor_head, Mapping) or not isinstance(
                    successor_head.get("summary"), Mapping):
                raise ValueError("stage transition successor receipt is invalid")
            successor_id = str(
                successor_head["summary"].get("stage_id") or "")
            if not successor_id or heads.get(successor_id) != successor_head or \
                    checked.get("stage_ids") != sorted(
                        [predecessor_id, successor_id]) or \
                    manifest.get("active_stage_projection") != \
                    result.get("active_stage_projection"):
                continue
            successor = _indexed_stage(
                store, manifest, run_id, successor_id)
            if successor.get("stage_kind") != to_kind or \
                    successor.get("state") != "active" or \
                    list(successor.get("predecessor_stage_ids") or []) != [
                        predecessor_id]:
                continue
            receipt_lineage = result.get("lineage") or []
            manifest_lineage = manifest.get("lineage") or []
            current_lineage = {
                str(row.get("fingerprint")) for row in manifest_lineage
                if isinstance(row, Mapping)}
            if not isinstance(receipt_lineage, list) or any(
                    not isinstance(row, Mapping) or
                    str(row.get("fingerprint")) not in current_lineage
                    for row in receipt_lineage):
                continue
        candidates.append(checked)
    if len(candidates) > 1:
        raise ValueError("stage transition crash recovery is ambiguous")
    return candidates[0] if candidates else None


def _stage_loop_transition(
        ws: str, state: Mapping[str, object], *, from_step: str,
        to_step: str, terminal_outcome: str = "done",
        completion: Mapping[str, object] | None = None,
        force: bool = False, terminal_only: bool = False) -> dict | None:
    """Mirror one loop kind transition in the immutable stage aggregate."""
    if completion is None and isinstance(
            state.get("_stage_completion"), Mapping):
        completion = state.get("_stage_completion")
    force = bool(force or state.get("_stage_force_transition"))
    if to_step == "failed" and terminal_outcome == "done":
        terminal_outcome = "discarded"
    from_kind = _LOOP_STAGE_KINDS.get(str(from_step))
    to_kind = _LOOP_STAGE_KINDS.get(str(to_step))
    if from_kind is None or (
            from_kind == to_kind and not force and not completion):
        return None
    replayed = _stage_loop_replayed_transition(
        ws, state, from_step=from_step, to_step=to_step,
        from_kind=from_kind, to_kind=to_kind,
        terminal_outcome=terminal_outcome, terminal_only=terminal_only,
        completion=completion)
    if replayed is not None:
        return replayed
    context = _stage_loop_context(ws, state)
    if context is None:
        return None
    stage_entities = context.get("stage_entities")
    stage = context.get("stage")
    manifest = context["manifest"]
    operation_material = _stage_loop_transition_operation_material(
        state, run_id=str(context["run_id"]), from_step=from_step,
        to_step=to_step, from_kind=from_kind, to_kind=to_kind,
        terminal_outcome=terminal_outcome, terminal_only=terminal_only,
        predecessor_stage_id=(
            stage.get("stage_id") if isinstance(stage, Mapping) else None),
        predecessor_head_fingerprint=(
            stage.get("fingerprint") if isinstance(stage, Mapping) else None))
    # Crash recovery: the stage commit may precede the loop.json commit.
    operations = manifest.get("stage_operations") or {}
    if stage_entities is None:
        try:
            if __package__:
                from . import stage_entities as stage_entities_module
            else:
                import stage_entities as stage_entities_module
        except ImportError:
            import stage_entities as stage_entities_module
        stage_entities = stage_entities_module
    operation_id = _stage_loop_identity(
        stage_entities, "loop-transition-", operation_material)
    prior = operations.get(operation_id) if isinstance(operations, Mapping) \
        else None
    if isinstance(prior, dict):
        return tp.verify_stage_receipt(prior)
    if not isinstance(stage, dict) or context.get("lifecycle") is None:
        raise ValueError("stage-native loop transition has no predecessor")
    if stage.get("stage_kind") != from_kind:
        raise ValueError(
            f"stage-native loop transition expected {from_kind!r}, found "
            f"{stage.get('stage_kind')!r}")

    lifecycle = context["lifecycle"]
    evidence = []
    selected_artifacts = []
    target = commit = None
    if completion:
        completion, selected_artifacts, target, commit = \
            _stage_loop_completion_outputs(lifecycle, completion)
        evidence = [_stage_loop_completion_reference(
            ws, lifecycle, stage, from_step=from_step, to_step=to_step,
            completion=completion)]
    if terminal_outcome == "done" and not evidence:
        raise ValueError(
            "stage-native loop transition lacks committed completion evidence")
    actor = str((stage.get("authority") or {}).get("actor") or "")
    authorized_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    completed = (list(stage.get("deliverables") or [])
                 if terminal_outcome == "done" else [])
    if to_kind is None or terminal_only:
        reason_code = None if terminal_outcome == "done" else "loop-terminal"
        reason = None if terminal_outcome == "done" else \
            f"loop ended at {to_step}"
        receipt = lifecycle.terminalize(
            str(stage["run_id"]), stage_id=str(stage["stage_id"]),
            expected_head_fingerprint=str(stage["fingerprint"]),
            expected_revision=int(manifest["revision"]),
            operation_id=operation_id, outcome=terminal_outcome, actor=actor,
            terminalized_at=authorized_at, reason_code=reason_code,
            reason=reason, completed_deliverables=completed,
            completion_evidence=evidence)
        return tp.verify_stage_receipt(
            receipt, expected_operation="terminalize",
            expected_stage_id=str(stage["stage_id"]))

    try:
        if __package__:
            from . import review_evidence, stage_handoff
        else:
            import review_evidence
            import stage_handoff
    except ImportError:
        import review_evidence
        import stage_handoff
    artifact_store = lifecycle._artifact_store()  # noqa: SLF001
    authority = dict(stage["authority"])
    authorization = {
        "actor": authority["actor"],
        "session_id": authority["session_id"],
        "authorized_at": authorized_at,
        "operation_id": operation_id,
        "authority_record": {
            "schema": "taskplane.authority-record-reference/v1",
            "authority_schema": "taskplane.consolidated-authorization/v1",
            "revision": authority["authority_revision"],
            "fingerprint": authority["authority_fingerprint"],
        },
    }
    successor_id = _stage_loop_identity(
        stage_entities, "stage-", {**operation_material,
                                    "predecessor": stage["stage_id"]})
    if not evidence:
        raise ValueError(
            "stage-native successor transition lacks stage-owned evidence")
    next_handoff = stage_handoff.create_manifest(
        artifact_store, producer_stage_id=str(stage["stage_id"]),
        producer_outcome=terminal_outcome,
        requirement=stage["requirement"], design=stage.get("design"),
        target=target, commit=commit,
        contracts={"provided": list(stage.get("contracts") or []),
                   "consumed": [], "changed": []},
        deliverables=completed, evidence_references=evidence,
        selected_artifacts=selected_artifacts,
        exclusions=sorted(stage_handoff.REQUIRED_EXCLUSIONS),
        authorization=authorization,
        allow_nonconsumable_reuse=terminal_outcome in {
            "closed", "discarded"})
    native_ref = stage_handoff.store_manifest(artifact_store, next_handoff)
    input_ref = review_evidence.portable_artifact_reference(
        artifact_store, native_ref)
    successor = stage_entities.create_stage(
        run_id=str(stage["run_id"]), stage_id=successor_id,
        requirement=stage["requirement"], design=stage.get("design"),
        stage_kind=to_kind, parent_stage_ids=[],
        predecessor_stage_ids=[str(stage["stage_id"])],
        input_manifest_ref=input_ref,
        execution_root_id=f"execution-{successor_id}",
        deliverables=_stage_loop_deliverables(to_kind, state),
        selected_artifacts=selected_artifacts,
        budget=dict(stage.get("budget") or {}), dependencies=[],
        contracts=list(stage.get("contracts") or []), authority=authority,
        created_at=authorized_at)
    _preflight_stage_dispatch(successor, next_handoff)
    receipt = lifecycle.terminalize_and_start(
        str(stage["stage_id"]), successor,
        expected_head_fingerprint=str(stage["fingerprint"]),
        expected_revision=int(manifest["revision"]),
        operation_id=operation_id, outcome=terminal_outcome, actor=actor,
        terminalized_at=authorized_at,
        completed_deliverables=completed, completion_evidence=evidence)
    return tp.verify_stage_receipt(
        receipt, expected_operation="terminalize_and_start",
        expected_stage_id=successor_id)


def stage_history(ws: str, run_id: str, *, cursor: object = None,
                  limit: int = STAGE_HISTORY_MAX_ITEMS) -> dict:
    """Public bounded read helper used by non-CLI host adapters."""
    try:
        return _stage_history(_stage_store(ws, run_id), run_id, {
            "cursor": cursor, "limit": limit,
        })
    except Exception as exc:
        return _stage_command_error("history", exc)


def _stage_history(store: object, run_id: str, request: Mapping[str, object]) \
        -> dict:
    manifest = store.load(run_id)
    if manifest.get("schema") != "taskplane.run/v4":
        return {
            "schema": STAGE_HISTORY_SCHEMA,
            "run_id": run_id,
            "legacy": True,
            "stages": [],
            "lineage": [],
            "next_cursor": None,
        }
    raw_limit = request.get("limit", STAGE_HISTORY_MAX_ITEMS)
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or \
            not 1 <= raw_limit <= STAGE_HISTORY_MAX_ITEMS:
        raise ValueError(
            f"history limit must be 1..{STAGE_HISTORY_MAX_ITEMS}")
    raw_cursor = request.get("cursor")
    if raw_cursor is None:
        offset = 0
    elif isinstance(raw_cursor, str) and raw_cursor.isdigit():
        offset = int(raw_cursor)
    elif isinstance(raw_cursor, int) and not isinstance(raw_cursor, bool):
        offset = raw_cursor
    else:
        raise ValueError("history cursor is invalid")
    if offset < 0:
        raise ValueError("history cursor is invalid")

    heads = manifest.get("stage_heads")
    if not isinstance(heads, Mapping):
        raise ValueError("stage history is unavailable")
    stage_ids = sorted(str(stage_id) for stage_id in heads)
    page_ids = stage_ids[offset:offset + raw_limit]
    items = []
    for stage_id in page_ids:
        head = heads[stage_id]
        if not isinstance(head, Mapping) or not isinstance(
                head.get("summary"), Mapping):
            raise ValueError("stage history contains an invalid head")
        # Return the already bounded summary.  Do not open the stage object,
        # predecessor tree, trace, transcript, lease, or meter.
        items.append(dict(head["summary"]))
    next_offset = offset + len(page_ids)
    page_set = set(page_ids)
    lineage = [dict(row) for row in manifest.get("lineage") or []
               if isinstance(row, Mapping) and
               str(row.get("child_stage_id") or "") in page_set]
    # One page is bounded independently from the persisted lineage fan-in.
    lineage = lineage[:STAGE_HISTORY_MAX_ITEMS]
    return {
        "schema": STAGE_HISTORY_SCHEMA,
        "run_id": run_id,
        "revision": manifest.get("revision"),
        "stages": items,
        "lineage": lineage,
        "cursor": str(offset),
        "next_cursor": (str(next_offset)
                        if next_offset < len(stage_ids) else None),
    }


def stage_command(ws: str, command: str, request: object) -> dict:
    """Run one explicit stage command without touching ``loop next/wave``.

    Stage entities are an additive rollout.  History remains readable for a
    migrated v4 run during rollback, while every mutation pauses when the
    feature is disabled.  A v3 run is auto-promoted by t02 only in the
    ``new-run`` canary mode; existing unmigrated callers stay on loop.json.
    """
    action = str(command or "").strip().lower()
    allowed = {"start", "resume", "terminalize", "terminalize-and-start",
               "split", "history", "reuse"}
    if action not in allowed:
        return _stage_command_error(
            action, ValueError("unknown stage command"))
    try:
        data = _validate_stage_request(action, _stage_request(request))
        run_id = _stage_run_id(action, data)
        if action != "history":
            try:
                singleton = _load_raw(ws)
            except Exception as exc:
                return _stage_command_error(action, exc)
            if refusal := _stage_bound_run_refusal(ws, singleton):
                return {
                    "schema": STAGE_COMMAND_SCHEMA,
                    "command": action, "run_id": run_id,
                    "enabled": False, "legacy": False,
                    "error": refusal["error"],
                    "stage_native": "read-only",
                }
        store = _stage_store(ws, run_id)
        if action == "history":
            return _stage_history(store, run_id, data)

        manifest = store.load(run_id)
        blocker = _stage_mutation_blocker(_stage_mode(), manifest, ws)
        if blocker:
            return {
                "schema": STAGE_COMMAND_SCHEMA,
                "command": action,
                "run_id": run_id,
                "enabled": False,
                "legacy": manifest.get("schema") == "taskplane.run/v3",
                "error": blocker,
            }
        stage_entities, lifecycle = _stage_lifecycle(
            ws, store, manifest, data.get("authority"))

        if action in {"start", "reuse"}:
            stage_value = data.get("stage") or data.get("successor_stage")
            if not isinstance(stage_value, Mapping):
                raise ValueError(f"{action} requires stage")
            stage = stage_entities.validate_stage(stage_value)
            current = store.load(run_id)
            predecessors = [
                _indexed_stage(store, current, run_id, str(stage_id))
                for stage_id in stage["predecessor_stage_ids"]
            ]
            verified_handoff = _verified_stage_handoff(
                lifecycle, store, current, stage)
            if action == "reuse":
                reason = data.get("reason")
                if not isinstance(reason, str) or not reason.strip() or \
                        reason != reason.strip() or \
                        len(reason.encode("utf-8")) > 4 * 1024 or any(
                            ord(character) < 32 or ord(character) == 127
                            for character in reason):
                    raise ValueError("reuse requires an attributable reason")
                if not stage.get("predecessor_stage_ids"):
                    raise ValueError("reuse requires a predecessor stage")
                producer = verified_handoff.get("producer")
                producer_id = (producer.get("stage_id")
                               if isinstance(producer, Mapping) else None)
                selected_producers = [
                    predecessor for predecessor in predecessors
                    if predecessor.get("stage_id") == producer_id
                ]
                if not selected_producers or any(
                        predecessor.get("outcome") not in {
                            "closed", "discarded"
                        } for predecessor in selected_producers):
                    raise ValueError(
                        "reuse requires a closed or discarded predecessor")
                authorization = verified_handoff.get("authorization")
                reuse_authorization = (authorization.get(
                    "nonconsumable_reuse")
                    if isinstance(authorization, Mapping) else None)
                actor = data.get("actor")
                if not isinstance(reuse_authorization, Mapping) or \
                        not isinstance(actor, str) or not actor.strip() or \
                        actor != authorization.get("actor") or \
                        actor != stage["authority"].get("actor"):
                    raise ValueError(
                        "reuse requires exact non-default handoff authority")
            elif any(predecessor.get("outcome") in {
                    "closed", "discarded"} for predecessor in predecessors):
                raise ValueError(
                    "closed or discarded predecessor requires reuse command")
            _preflight_stage_dispatch(
                stage, verified_handoff,
                declared_scope=data.get("declared_scope"))
            receipt = lifecycle.start_stage(
                stage,
                expected_revision=data.get("expected_revision"),
                operation_id=data.get("operation_id"),
                expected_predecessor_fingerprints=data.get(
                    "expected_predecessor_fingerprints"),
                foreground=data.get("foreground", True))
            checked = tp.verify_stage_receipt(
                receipt, expected_operation="start_stage",
                expected_stage_id=str(stage["stage_id"]))
            if manifest.get("schema") == "taskplane.run/v3":
                _persist_stage_run_binding(
                    ws, store, root_stage_id=str(stage["stage_id"]))
            if action == "reuse":
                tp.trace(
                    ws, "stage_nondefault_reuse", run_id=run_id,
                    stage_id=stage["stage_id"], actor=data["actor"],
                    reason=data["reason"], operation_id=data["operation_id"],
                    receipt_fingerprint=checked.get("result_fingerprint"))
            dispatch = _stage_dispatch(
                store, lifecycle, checked, stage,
                declared_scope=data.get("declared_scope"))
            return {"schema": STAGE_COMMAND_SCHEMA, "command": action,
                    "run_id": run_id, "receipt": checked,
                    "dispatch": dispatch}

        if action == "resume":
            current = store.load(run_id)
            active_stage = _indexed_stage(
                store, current, run_id, str(data.get("stage_id") or ""))
            verified_handoff = _verified_stage_handoff(
                lifecycle, store, current, active_stage)
            requested_attempt = data.get("attempt_id")
            if requested_attempt is None:
                requested_attempt = "attempt-" + \
                    stage_entities.request_fingerprint({
                        "run_id": run_id,
                        "stage_id": data.get("stage_id"),
                        "operation_id": data.get("operation_id"),
                    })[:24]
            compatibility = getattr(tp, "stage_dispatch_payload", None)
            if not callable(compatibility):
                raise ValueError("stage runtime serializer is unavailable")
            compatibility(
                active_stage, verified_handoff,
                active_stage.get("selected_artifacts") or [], {
                    "schema": "taskplane.stage-execution-attempt-claim/v1",
                    "run_id": active_stage["run_id"],
                    "stage_id": active_stage["stage_id"],
                    "execution_root_id": active_stage["execution_root_id"],
                    "attempt_id": requested_attempt,
                }, attempt_id=requested_attempt,
                declared_scope=data.get("declared_scope"))
            receipt = lifecycle.resume_stage(
                run_id, stage_id=data.get("stage_id"),
                expected_head_fingerprint=data.get(
                    "expected_head_fingerprint"),
                expected_revision=data.get("expected_revision"),
                operation_id=data.get("operation_id"),
                attempt_id=data.get("attempt_id"))
            checked = tp.verify_stage_receipt(
                receipt, expected_operation="resume_stage",
                expected_stage_id=str(data.get("stage_id") or ""))
            result = checked.get("result") or {}
            attempt_id = str(result.get("attempt_id") or "")
            current = store.load(run_id)
            stage = _indexed_stage(
                store, current, run_id, str(data.get("stage_id") or ""))
            dispatch = _stage_dispatch(
                store, lifecycle, checked, stage, attempt_id=attempt_id,
                declared_scope=data.get("declared_scope"))
            return {"schema": STAGE_COMMAND_SCHEMA, "command": action,
                    "run_id": run_id, "receipt": checked,
                    "dispatch": dispatch}

        if action == "terminalize":
            receipt = lifecycle.terminalize(
                run_id, stage_id=data.get("stage_id"),
                expected_head_fingerprint=data.get(
                    "expected_head_fingerprint"),
                expected_revision=data.get("expected_revision"),
                operation_id=data.get("operation_id"),
                outcome=data.get("outcome"), actor=data.get("actor"),
                terminalized_at=data.get("terminalized_at"),
                reason_code=data.get("reason_code"),
                reason=data.get("reason"),
                completed_deliverables=data.get(
                    "completed_deliverables") or (),
                completion_evidence=data.get("completion_evidence") or (),
                handoff_manifest=data.get("handoff_manifest"))
            checked = tp.verify_stage_receipt(
                receipt, expected_operation="terminalize",
                expected_stage_id=str(data.get("stage_id") or ""))
            return {"schema": STAGE_COMMAND_SCHEMA, "command": action,
                    "run_id": run_id, "receipt": checked}

        if action == "terminalize-and-start":
            successor_value = data.get("stage") or data.get(
                "successor_stage")
            if not isinstance(successor_value, Mapping):
                raise ValueError("terminalize-and-start requires stage")
            successor = stage_entities.validate_stage(successor_value)
            current = store.load(run_id)
            predecessor = _indexed_stage(
                store, current, run_id,
                str(data.get("predecessor_stage_id") or ""))
            prospective_terminal = stage_entities.terminalize_stage(
                predecessor, outcome=data.get("outcome"),
                actor=data.get("actor"),
                terminalized_at=data.get("terminalized_at"),
                reason_code=data.get("reason_code"),
                reason=data.get("reason"),
                completed_deliverables=data.get(
                    "completed_deliverables") or (),
                completion_evidence=data.get("completion_evidence") or ())
            verified_handoff = lifecycle._read_handoff(  # noqa: SLF001
                successor["input_manifest_ref"],
                producer=prospective_terminal, consumer=successor)
            _preflight_stage_dispatch(
                successor, verified_handoff,
                declared_scope=data.get("declared_scope"))
            receipt = lifecycle.terminalize_and_start(
                data.get("predecessor_stage_id"), successor,
                expected_head_fingerprint=data.get(
                    "expected_head_fingerprint"),
                expected_revision=data.get("expected_revision"),
                operation_id=data.get("operation_id"),
                outcome=data.get("outcome"), actor=data.get("actor"),
                terminalized_at=data.get("terminalized_at"),
                reason_code=data.get("reason_code"),
                reason=data.get("reason"),
                completed_deliverables=data.get(
                    "completed_deliverables") or (),
                completion_evidence=data.get("completion_evidence") or (),
                foreground=data.get("foreground", True))
            checked = tp.verify_stage_receipt(
                receipt, expected_operation="terminalize_and_start",
                expected_stage_id=str(successor["stage_id"]))
            dispatch = _stage_dispatch(
                store, lifecycle, checked, successor,
                declared_scope=data.get("declared_scope"))
            return {"schema": STAGE_COMMAND_SCHEMA, "command": action,
                    "run_id": run_id, "receipt": checked,
                    "dispatch": dispatch}

        current = store.load(run_id)
        parent = _indexed_stage(
            store, current, run_id, str(data.get("stage_id") or ""))
        prospective_split = stage_entities.create_split(
            parent, operation_id=data.get("operation_id"),
            child_specs=data.get("child_specs") or (),
            actor=data.get("actor"),
            terminalized_at=data.get("terminalized_at"),
            reason=data.get("reason"))
        scopes = data.get("declared_scopes") or {}
        if not isinstance(scopes, Mapping):
            raise ValueError("split declared scopes must be an object")
        for child in prospective_split["children"]:
            verified_handoff = lifecycle._read_handoff(  # noqa: SLF001
                child["input_manifest_ref"],
                producer=prospective_split["parent"], consumer=child)
            _preflight_stage_dispatch(
                child, verified_handoff,
                declared_scope=scopes.get(child["stage_id"]))
        receipt = lifecycle.split_stage(
            run_id, stage_id=data.get("stage_id"),
            expected_head_fingerprint=data.get(
                "expected_head_fingerprint"),
            expected_revision=data.get("expected_revision"),
            operation_id=data.get("operation_id"),
            child_specs=data.get("child_specs") or (),
            actor=data.get("actor"),
            terminalized_at=data.get("terminalized_at"),
            reason=data.get("reason"))
        checked = tp.verify_stage_receipt(
            receipt, expected_operation="split_stage")
        current = store.load(run_id)
        child_heads = ((checked.get("result") or {}).get("child_heads") or {})
        if not isinstance(child_heads, Mapping):
            raise ValueError("split receipt has no child heads")
        dispatches = []
        for child_id in sorted(str(value) for value in child_heads):
            child = _indexed_stage(store, current, run_id, child_id)
            dispatches.append(_stage_dispatch(
                store, lifecycle, checked, child,
                declared_scope=scopes.get(child_id)))
        return {"schema": STAGE_COMMAND_SCHEMA, "command": action,
                "run_id": run_id, "receipt": checked,
                "dispatches": dispatches}
    except Exception as exc:
        return _stage_command_error(action, exc)

# R-0006 row 1: the EVALUATE step routes lenses with the BUILD stage
# profile (route v2: build-profile candidates, R-0001 budget 5-7/cap-8
# inherited verbatim, component assembly from R-0003). ONE constant feeds
# BOTH the evaluate brief's routing and _evaluation_errors' expected-lens
# derivation, so the validator's expectation can never drift from what
# was dispatched. Final EM uses the review profile through the same kernel.
EVALUATE_ROUTE_STAGE = "build"
_DELIVERY_MODE_AUTHORITY_UNSET = object()


_review_kernel_binding_key = review_retry.binding_key
review_kernel_binding = review_retry.binding
review_session_authority_gate = review_session_engine.request_authority


def _consolidated_enabled() -> bool:
    return os.environ.get("TASKPLANE_CONSOLIDATED_FLOW", "").strip().lower() \
        in {"1", "true", "yes", "on"}


def _authorization_fields(ws: str, state: dict) -> dict:
    """Build the semantic preimplementation envelope from engine facts."""
    requirement = reqs.get_requirement(ws, state.get("requirement_id")) or {}
    design, _ = _design_contract(ws)
    tasks = state.get("tasks") or []
    scope = sorted({str(path) for task in tasks
                    for path in task.get("scope") or []})
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
    plan = [{
        "id": str(task.get("id") or ""),
        "scope": sorted(str(path) for path in task.get("scope") or []),
        "tests": str(task.get("tests") or ""),
        "deps": sorted(str(dep) for dep in task.get("deps") or []),
        "variant": task.get("variant"),
    } for task in tasks]
    return {
        "requirement": str(state.get("requirement_id") or ""),
        "acceptance": list(requirement.get("acceptance") or []),
        "target": {"repository": os.path.realpath(ws),
                   "revision": (state.get("authority_target_revision") or
                                tp.git_head(ws))},
        "scope": scope,
        "contracts": contracts,
        "design": {
            "decision": str((design or {}).get("decision") or ""),
            "depth_policy": ((design or {}).get("graph") or {}).get(
                "depth_policy") or {},
        },
        "plan": {"tasks": plan, "parallel": bool(state.get("parallel"))},
        "dynamic_validation": state.get("dynamic_validation_intent", "declared"),
        "sandbox": state.get("sandbox_authority", "ordinary_scoped_activity"),
        "recovery": {"max_fix_cycles": int(state.get("max_fix_cycles", 2)),
                     "gate_weakening": False},
        "evaluation": "declared tests, routed lenses, collection",
        "artifact_delivery": ["canonical_json", "inline_or_complete_markdown"],
        "execution_bounds": {"parallel": bool(state.get("parallel")),
                             "external_effects": False},
    }


def _product_definition_gate(requirement: dict) -> dict:
    """Product refinement is mechanical; strategic advice is attributable."""
    text = [str(requirement.get("title") or ""),
            *[str(x) for x in requirement.get("acceptance") or []]]
    advice = review_dor.north_star_advice(
        text, explicit=bool(requirement.get("north_star_requested")),
        advice=requirement.get("north_star_advice"))
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
    return {**authority_engine.mechanical_definition_gate("product", evidence),
            "north_star": advice}


def _preview_feedback(state: dict, text: str, *, actor: str,
                      authenticated: bool, kind: str) -> dict:
    return authority_engine.preview_change(
        text, actor=actor, authenticated=authenticated,
        requirement=str(state.get("requirement_id") or ""),
        target={"revision": str(state.get("authority_target_revision") or
                                state.get("baseline") or "")}, kind=kind)


def request_human_decision(state: dict, reason: str, response: object, *,
                           actor: str, thread: str, revision: str,
                           consumed: bool = False, fact: str = "",
                           consequence: str = "") -> dict:
    """Single production boundary for every exceptional human decision."""
    receipt = state.get("authority_receipt") or {}
    return authority_engine.decision_input(
        reason, response, fact=fact, consequence=consequence,
        actor=actor, thread=thread, revision=revision,
        expected_actor=str(receipt.get("actor") or ""),
        expected_thread=str(receipt.get("thread") or ""),
        expected_revision=str(state.get("authority_target_revision") or
                              state.get("baseline") or ""),
        consumed=consumed)


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
            fd = os.open(absolute, os.O_RDONLY |
                         getattr(os, "O_NOFOLLOW", 0))
            after = os.fstat(fd)
            if not stat.S_ISREG(after.st_mode) or \
                    (before.st_dev, before.st_ino) != \
                    (after.st_dev, after.st_ino):
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


def _open_directory_without_symlinks(path: str) -> int:
    """Open an absolute directory while rejecting every symlink component."""
    directory = os.path.abspath(path)
    drive, tail = os.path.splitdrive(directory)
    root = drive + os.sep if drive else os.sep
    parts = [part for part in tail.split(os.sep) if part]
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    supports_relative_open = (
        directory_flag is not None and nofollow is not None and
        os.open in getattr(os, "supports_dir_fd", set()))
    if supports_relative_open:
        flags = os.O_RDONLY | directory_flag | nofollow
        current_fd = os.open(root, os.O_RDONLY | directory_flag)
        current_path = root
        try:
            for part in parts:
                candidate = os.path.join(current_path, part)
                info = os.lstat(candidate)
                if stat.S_ISLNK(info.st_mode):
                    raise OSError(
                        "authority trace path contains a symlink: " +
                        candidate)
                if not stat.S_ISDIR(info.st_mode):
                    raise OSError(
                        "authority trace path component is not a directory: " +
                        candidate)
                next_fd = None
                try:
                    next_fd = os.open(part, flags, dir_fd=current_fd)
                    opened = os.fstat(next_fd)
                    if not stat.S_ISDIR(opened.st_mode) or (
                            info.st_dev, info.st_ino) != (
                                opened.st_dev, opened.st_ino):
                        raise OSError(
                            "authority trace path component changed while "
                            "opening: " + candidate)
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
        final_info = os.lstat(current_path)
        if stat.S_ISLNK(final_info.st_mode):
            raise OSError(
                "authority trace path contains a symlink: " + current_path)
        if not stat.S_ISDIR(final_info.st_mode):
            raise OSError(
                "authority trace path component is not a directory: " +
                current_path)
    flags = os.O_RDONLY
    if directory_flag is not None:
        flags |= directory_flag
    dir_fd = os.open(directory, flags)
    opened = os.fstat(dir_fd)
    if not stat.S_ISDIR(opened.st_mode) or (
            final_info.st_dev, final_info.st_ino) != (
                opened.st_dev, opened.st_ino):
        os.close(dir_fd)
        raise OSError("authority trace directory changed while opening")
    return dir_fd


def _append_authority_trace(ws: str, event: str, data: dict) -> None:
    """Append one authority trace without following directory/file links."""
    directory = os.path.abspath(tp.tp_dir(ws))
    dir_fd = _open_directory_without_symlinks(directory)
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
                raise OSError(
                    "authority trace file is a symlink: " + trace_path)
        if supports_relative_open:
            trace_fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
        else:
            trace_fd = os.open(os.path.join(directory, name), flags, 0o600)
        current = os.fstat(trace_fd)
        if not stat.S_ISREG(current.st_mode):
            raise OSError("authority trace target is not a regular file")
        if existing is not None and (
                existing.st_dev, existing.st_ino) != (
                    current.st_dev, current.st_ino):
            raise OSError("authority trace file changed while opening")
        # Authority outbox delivery uses the same closed privacy projection as
        # every ordinary audit append; the no-follow descriptor handling here
        # remains the stronger authority-specific filesystem boundary.
        record = tp.audit_record(event, data, observed_at=time.time())
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
        return any((row.get("links") or {}).get("authority_effect") == effect_id
                   for row in kb.list_decisions(ws))
    except (OSError, ValueError, TypeError):
        return False


def _enqueue_authority_effect(state: dict, effect_id: str, *,
                              trace_event: str, trace_data: dict,
                              kb_data: dict | None = None) -> None:
    outbox = state.setdefault("authority_effect_outbox", {})
    outbox.setdefault(effect_id, {
        "schema": "taskplane.authority-effect/v1", "status": "pending",
        "trace": {"delivered": False, "event": trace_event,
                  "data": trace_data},
        "kb": ({"delivered": False, "data": kb_data}
               if kb_data is not None else None),
    })


def reconcile_authority_effects(ws: str) -> dict:
    """Deliver durable authority effects idempotently after state commit."""
    if refusal := _stage_loop_mutation_refusal(ws):
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
                            ws, str(trace_effect.get("event") or
                                    "authority_effect"),
                            {**dict(trace_effect.get("data") or {}),
                             "authority_effect_id": effect_id})
                    trace_effect["delivered"] = _trace_effect_seen(ws, effect_id)
                kb_effect = row.get("kb")
                if trace_effect.get("delivered") and kb_effect and not \
                        kb_effect.get("delivered"):
                    if not _kb_effect_seen(ws, effect_id):
                        data = dict(kb_effect.get("data") or {})
                        links = {**dict(data.pop("links", {}) or {}),
                                 "authority_effect": effect_id}
                        kb.record_decision(ws, links=links, **data)
                    kb_effect["delivered"] = _kb_effect_seen(ws, effect_id)
            except Exception as exc:  # effect remains durable for retry
                row["last_error"] = f"{exc.__class__.__name__}: {exc}"
            complete = bool(trace_effect.get("delivered")) and (
                row.get("kb") is None or bool((row.get("kb") or {}).get(
                    "delivered")))
            if complete:
                row["status"] = "delivered"
                row.pop("last_error", None)
                delivered += 1
            else:
                row["status"] = "pending"
                pending += 1
    return {"delivered": delivered, "pending": pending}


def _host_session_envelope(state: dict, event: dict,
                           host_event: object | None) -> dict:
    """Bind trusted-session attribution to the loop's current target."""
    expected_revision = str(state.get("authority_target_revision") or
                            state.get("baseline") or "")
    receipt = state.get("authority_receipt") or {}
    envelope = authority_engine.HostSessionAdapter().observe(
        event, host_event,
        expected_actor=str(receipt.get("actor") or ""),
        expected_thread=str(receipt.get("thread") or ""),
        expected_revision=expected_revision,
        expected_target={"revision": expected_revision})
    if envelope.get("attributed") and not all((
            str(receipt.get("actor") or "").strip(),
            str(receipt.get("thread") or "").strip(), expected_revision)):
        return {**envelope, "attributed": False,
                "reasons": ["current_authority_unbound"]}
    return envelope


def handle_host_input(ws: str, event: dict,
                      host_event: object | None = None) -> dict:
    """Consume one trusted local host/session event.

    The supported deployment is a single trusted Codex/Claude session.  The
    separate adapter observation supplies attribution; labels in the event
    body do not.  Stale and replay checks remain mechanical and atomic.
    """
    if refusal := _stage_loop_mutation_refusal(ws):
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
            expected_revision = str(state.get("authority_target_revision") or
                                    state.get("baseline") or "")
            if envelope["revision"] != expected_revision:
                return {"accepted": False, "reasons": ["wrong_revision"]}
            event_id = envelope["event_id"]
            consumed = state.setdefault("consumed_host_events", {})
            if event_id in consumed:
                return {"accepted": False, "reasons": ["replayed_event"]}
            change = _preview_feedback(
                state, str(event.get("text") or ""), actor=envelope["actor"],
                authenticated=True,
                kind=str(event.get("change_kind") or ""))
            if not change["accepted"]:
                return change
            state.setdefault("preview_changes", []).append(change)
            if change["reauthorization_required"]:
                state["reauthorization_required"] = True
            consumed[event_id] = {
                "actor": envelope["actor"], "thread": envelope["thread"],
                "revision": envelope["revision"],
                "target": envelope["target"], "source": envelope["source"],
                "event_ref": envelope["event_ref"],
            }
            _enqueue_authority_effect(
                state, f"preview:{event_id}", trace_event="preview_change",
                trace_data={"actor": change["actor"],
                            "kind": change["kind"],
                            "material": change["material"],
                            "change": change["fingerprint"]})
        effects = reconcile_authority_effects(ws)
        return {**change, "effect_delivery": effects}
    if kind == "human_decision":
        with mutate(ws) as state:
            if state is None:
                return {"error": "no active loop"}
            envelope = _host_session_envelope(state, event, host_event)
            if not envelope["attributed"]:
                return {"authorized": False, "human_required": True,
                        "reasons": envelope["reasons"]}
            decision_id = envelope["event_id"]
            consumed = state.setdefault("consumed_host_decisions", {})
            response = event.get("response")
            if isinstance(response, dict):
                response = {**response, "authenticated": True}
            result = request_human_decision(
                state, str(event.get("reason") or "unsafe_or_ambiguous"),
                response, actor=envelope["actor"],
                thread=envelope["thread"], revision=envelope["revision"],
                consumed=decision_id in consumed,
                fact=str(event.get("fact") or ""),
                consequence=str(event.get("consequence") or ""))
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


def _derive_consolidated_authority(ws: str, state: dict,
                                   stage: str) -> dict | None:
    packet, receipt = (state.get("authority_packet"),
                       state.get("authority_receipt"))
    if not _consolidated_enabled() or not packet or not receipt:
        return None
    return authority_engine.derive(
        packet, receipt, stage=stage,
        current=_authorization_fields(ws, state),
        actor=str(receipt.get("actor") or ""),
        thread=str(receipt.get("thread") or ""))


def authorize_routine_flow(ws: str, flow: str) -> dict:
    """Production entry point used by each host flow to derive authority."""
    if refusal := _stage_loop_mutation_refusal(ws):
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
    tp.trace(ws, "authority_derived", flow=normalized,
             authorized=derived["authorized"],
             receipt=derived.get("receipt_fingerprint"))
    return derived

def _state_dir(ws: str) -> str:
    """Loop coordination state. v1.5.1: state is PER-USER even in team/repo
    knowledge mode — share knowledge, not the state machine. Two teammates'
    concurrent loops in a committed loop.json are guaranteed unmergeable
    conflicts, and flock on a git-round-tripped file serializes nothing
    across machines. The ONE exception is the explicit TASKPLANE_STORE=repo
    env override (Claude Tag): there the sandbox is ephemeral and
    single-writer, so committed state is exactly what lets the next session
    resume the loop."""
    if tp.store_env() == "repo":
        return os.path.join(tp.kb_root(ws), "state")
    ext = os.path.join(tp.external_store_root(ws), "knowledge", "state")
    if os.path.exists(os.path.join(ext, LOOP_FILE)):
        return ext
    legacy = os.path.join(ws, "knowledge", "state")   # unmigrated project
    if os.path.exists(os.path.join(legacy, LOOP_FILE)):
        return legacy
    return ext


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
}
HUMAN_STEPS = progress_engine.HUMAN_STEPS

COMMAND_WAVE_SCHEMA = command_wave.COMMAND_WAVE_SCHEMA
command_wave_create = command_wave.create
command_wave_resume = command_wave.resume
command_wave_update = command_wave.update


def governed_command(ws: str, action: str, request: object) -> dict:
    """Run one command lifecycle action through the live loop root."""
    return governed_commands.execute(ws, action, request)


def _native_dispatch_intent(
    ws: str, state: Mapping[str, object], *, step: str, task_id: str,
    dispatch: Mapping[str, object], wait_policy: Mapping[str, object],
    wave_id: str | None = None,
) -> dict:
    """Persist non-authoritative intent before the host launches an agent."""
    try:
        locator = runtime_storage.load_workspace_locator(ws)
    except Exception:
        locator = None
    run_id = str(locator.get("run_id") or "") \
        if isinstance(locator, Mapping) else ""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", run_id) is None:
        import hashlib
        material = json.dumps({
            "workspace": os.path.realpath(ws),
            "goal": state.get("goal"),
            "baseline": state.get("baseline"),
        }, sort_keys=True, separators=(",", ":"))
        run_id = "loop-" + hashlib.sha256(material.encode("utf-8")).hexdigest()
    role = str(dispatch.get("role") or STEP_ROLE.get(step) or step)
    result = governed_command(ws, "dispatch", {
        "authorization": f"loop-dispatch:{run_id}",
        "consumer": f"{role}:{task_id}",
        "host": "native-agent",
        "payload": {
            "schema": "taskplane.native-agent-dispatch/v1",
            "step": step,
            "role": role,
            "task_name": dispatch.get("task_name"),
            "wait_policy": dict(wait_policy),
        },
        "run_id": run_id,
        "task_id": task_id,
        "wave_id": wave_id,
    })
    result["wait_policy"] = dict(wait_policy)
    return result

# A task is SETTLED when nothing further is owed on it: it passed, or the
# selection gate closed it (not_selected / reference), or a human skipped it.
# Wave readiness and "are we done?" both reason over this set.
SETTLED = {"passed", "not_selected", "reference", "skipped",
           "done", "external"}
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


def _legacy_loop_path(ws: str) -> str:
    return os.path.join(tp.tp_dir(ws), LOOP_FILE)


def _load_raw(ws: str) -> dict | None:
    p = _loop_path(ws)
    if not os.path.exists(p):
        p = _legacy_loop_path(ws)          # pre-spec state, read once
        if not os.path.exists(p):
            return None
    # v2.3.0: a corrupt loop.json fails CLOSED with a typed error naming the
    # file and a remedy (tp.StateError) — never a bare JSONDecodeError
    # traceback, and never a silent default that would mask the corruption.
    return tp.load_json(p, what="loop state file")


_EVIDENCE_STATE_WORKSPACE = contextvars.ContextVar(
    "taskplane_evidence_state_workspace", default=None)


def load(ws: str) -> dict | None:
    """Load loop state and flush any crash-surviving authority outbox."""
    # CLI evidence may bind task bytes to its primary coordination state.
    state_ws = _EVIDENCE_STATE_WORKSPACE.get() or ws
    state = _load_raw(state_ws)
    read_only = _stage_loop_mutation_refusal(state_ws)
    if state is not None and read_only is None and any(
            row.get("status") != "delivered" for row in
            (state.get("authority_effect_outbox") or {}).values()):
        reconcile_authority_effects(state_ws)
        state = _load_raw(state_ws)
    return state


def save(ws: str, state: dict) -> None:
    os.makedirs(_state_dir(ws), exist_ok=True)
    # Atomic write (tp.atomic_write_json): parallel wave workers gate
    # concurrently against the shared loop.json — a torn read of a
    # half-written file is a corrupt loop that stalls everyone; a reader only
    # ever sees a complete state. (Lost-update races between concurrent
    # read-modify-write are serialized by `mutate()` below, which holds an
    # exclusive lock across the whole load→change→save.)
    tp.atomic_write_json(_loop_path(ws), state, indent=2)
    legacy = _legacy_loop_path(ws)         # migrate: single source of truth
    if os.path.exists(legacy):
        tp.safe_remove(legacy)


def record_enforcement(ws: str, decision: dict) -> dict:
    """Persist one canonical decision for all loop consumers and artifacts."""
    if refusal := _stage_loop_mutation_refusal(ws):
        return refusal
    import enforcement as enforcement_kernel

    checked = enforcement_kernel.validate_decision(decision)
    with mutate(ws) as state:
        if state is None:
            return {"error": "no active loop"}
        record = state.setdefault("enforcement", {
            "schema": "taskplane.run-enforcement/v1",
            "current": checked, "history": [],
        })
        history = list(record.get("history") or [])
        if not history or history[-1].get("evidence_id") != \
                checked.get("evidence_id"):
            history.append(checked)
        record.update({"schema": "taskplane.run-enforcement/v1",
                       "current": checked, "history": history[-64:]})
    tp.trace(ws, "enforcement_decision", status=checked["status"],
             evidence_id=checked["evidence_id"], mode=checked["mode"],
             actor=((checked.get("advisory") or {}).get("actor")))
    return checked


@contextlib.contextmanager
def mutate(ws: str):
    """Serialize a read-modify-write of the shared loop state. Concurrent wave
    workers each do load()→change→save(); without a lock two workers can read
    the same state and the second save clobbers the first's update (a gated
    task silently reverts to running and the loop stalls). An exclusive
    flock held across the whole critical section prevents that. Yields the
    current state dict; persists it on clean exit.

        with loop.mutate(ws) as st:
            task = next(t for t in st['tasks'] if t['id'] == tid)
            task['status'] = 'built'

    v2.3.0: the lock is tp.file_lock — where flock is unavailable or refused
    (Windows, FUSE/NFS/SMB mounts, exactly the hosts this plugin targets) it
    falls back to an atomic mkdir spin-lock, and if even that cannot be
    acquired it raises tp.StateError. Wave serialization is therefore never
    SILENTLY lost the way the old `except OSError: pass` fallback lost it.
    """
    os.makedirs(_state_dir(ws), exist_ok=True)
    with tp.file_lock(_loop_path(ws)):
        st = _load_raw(ws)
        original = (json.loads(json.dumps(st)) if st is not None else None)
        yield st
        if st is not None:
            fence = st.pop("_authority_revision_fence", None)
            if fence:
                before = tp.git_head(ws)
                if before != fence:
                    st.clear()
                    st.update(original or {})
                    st["_revision_fence_failed"] = {
                        "expected": str(fence), "actual": str(before)}
                    return
                save(ws, st)
                after = tp.git_head(ws)
                if after != fence:
                    if original is not None:
                        save(ws, original)
                    st.clear()
                    st.update(original or {})
                    st["_revision_fence_failed"] = {
                        "expected": str(fence), "actual": str(after)}
            else:
                save(ws, st)


TERMINAL_STEPS = ("done", "failed")


def automatic_cleanup_enabled() -> bool:
    """Rollback switch: default on; false returns to manual cleanup."""
    return (os.environ.get("TASKPLANE_AUTO_WORKTREE_CLEANUP") or "on").strip() \
        .lower() not in {"0", "false", "no", "off", "manual"}


def _cleanup_lifecycle(task: dict) -> dict:
    retention = task.get("evidence_retention") or {}
    return {
        "status": task.get("status"), "released": task.get("status") == "passed",
        "active": False, "failed": task.get("status") == "failed",
        "variant": task.get("variant"),
        "selected_variant": bool(task.get("selected")),
        "evidence_needed": retention.get("evidence_needed") is True,
    }


def _record_cleanup_state(ws: str, task_id: str, *, receipt: dict | None = None,
                          retention: dict | None = None,
                          cleanup_record: dict | None = None,
                          merge_error: str | None = None) -> None:
    with mutate(ws) as locked:
        if locked is None:
            raise tp.StateError(_loop_path(ws), "loop disappeared",
                                "restore the loop before cleanup")
        task = next((row for row in locked.get("tasks") or []
                     if row.get("id") == task_id), None)
        if task is None:
            raise tp.StateError(_loop_path(ws), "cleanup task disappeared",
                                "restore the approved task plan")
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
    if not automatic_cleanup_enabled() or task.get("variant") or \
            task.get("merge_on_pass") is False:
        return None
    worker = str(task.get("workspace") or "")
    if not worker:
        return None
    try:
        registration = runtime_storage.load_task_worktree_registration(
            ws, str(task["id"]))
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

        retention = review_evidence.retain_worktree_governance(
            ws, worker, str(task["id"]))
        receipt = repository.RepositoryManager().merge_registered_task(
            ws, task_id=str(task["id"]),
            run_id=str(registration.get("run_id") or "legacy"))
        # The merge receipt is durable before cleanup starts.
        _record_cleanup_state(ws, str(task["id"]), receipt=receipt,
                              retention=retention)
        current = load(ws) or {}
        stored_task = next((row for row in current.get("tasks") or []
                            if row.get("id") == task.get("id")), task)
        result = worktree_cleanup.cleanup(
            receipt, lifecycle=_cleanup_lifecycle(stored_task))
        _record_cleanup_state(ws, str(task["id"]), cleanup_record=result)
        tp.trace(ws, "worktree_cleanup_" + result["outcome"].replace("-", "_"),
                 task=task["id"], receipt_id=receipt["receipt_id"],
                 reason=result["reason"])
        return result
    except Exception as exc:
        _record_cleanup_state(ws, str(task["id"]), merge_error=str(exc))
        tp.trace(ws, "worktree_cleanup_preserved", task=task.get("id"),
                 reason=f"merge receipt unavailable: {exc}")
        return {"status": "preserved",
                "reason": f"merge receipt unavailable: {exc}"}


def cleanup_replay(ws: str) -> dict:
    """One bounded maintenance pass over durable receipts only."""
    # Cleanup replay is receipt-scoped lifecycle maintenance, not a stage or
    # singleton workflow transition.  It must remain available while the
    # stage-native rollout is paused so a crash after a durable merge receipt
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
        result = worktree_cleanup.cleanup(
            receipt, lifecycle=_cleanup_lifecycle(task))
        _record_cleanup_state(ws, str(task_id), cleanup_record=result)
        outcomes.append(result)
        tp.trace(ws, "worktree_cleanup_" + result["outcome"].replace("-", "_"),
                 task=task_id, receipt_id=receipt.get("receipt_id"),
                 reason=result["reason"], replay=True)
    return {"schema": "taskplane.worktree-cleanup-maintenance/v1",
            "attempted": len(outcomes), "outcomes": outcomes}


def _stage_native_init_authority(
        ws: str, requirement_id: str | None, by: str | None) -> dict | None:
    """Capture explicit, attributable authority before new-run state exists."""
    if _stage_mode() != "new-run":
        return None
    rid = str(requirement_id or "").strip()
    if not rid:
        raise ValueError(
            "stage-native new-run init requires --req <R-id> for an "
            "existing requirement")
    requirement = reqs.get_requirement(ws, rid)
    if requirement is None:
        raise ValueError(
            f"stage-native new-run init requirement '{rid}' does not exist")
    actor = str(by or "").strip()
    if not actor:
        raise ValueError(
            "stage-native new-run init requires --by <human identity>")
    if _STAGE_ACTOR_IDENTIFIER.fullmatch(actor) is None:
        raise ValueError(
            "stage-native new-run init --by actor must match "
            "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    session_id = str(
        os.environ.get("TASKPLANE_SESSION_ID") or
        os.environ.get("CODEX_THREAD_ID") or
        os.environ.get("CLAUDE_SESSION_ID") or "").strip()
    if not session_id:
        raise ValueError(
            "stage-native new-run init requires TASKPLANE_SESSION_ID, "
            "CODEX_THREAD_ID, or CLAUDE_SESSION_ID")
    if any(not value or len(value.encode("utf-8")) > 256 or any(
            ord(character) < 32 or ord(character) == 127
            for character in value) for value in (actor, session_id)):
        raise ValueError(
            "stage-native new-run init actor/session identity is invalid")
    try:
        locator = runtime_storage.load_workspace_locator(ws)
    except Exception as exc:
        raise ValueError(
            "stage-native new-run init requires a governed RunStore "
            f"workspace locator: {exc}") from exc
    if not isinstance(locator, Mapping):
        raise ValueError(
            "stage-native new-run init requires a governed RunStore "
            "workspace locator")
    run_id = str(locator.get("run_id") or "")
    if not run_id:
        raise ValueError(
            "stage-native new-run init requires a governed RunStore "
            "workspace locator")
    manifest = _stage_store(ws, run_id).load(run_id)
    if manifest.get("schema") != "taskplane.run/v3" or any(
            "migration" in str(key).lower() and value not in (
                None, False, "", [], {})
            for key, value in manifest.items()):
        raise ValueError(
            "stage-native new-run init requires an unmigrated v3 run")
    repository = manifest.get("repository")
    if not isinstance(repository, Mapping) or \
            repository.get("repo_id") != locator.get("repo_id") or \
            not locator.get("repository_key"):
        raise ValueError(
            "stage-native new-run init repository identity is invalid")
    target = manifest.get("target")
    target_revision = (target.get("revision") or target.get("head")
                       if isinstance(target, Mapping) else None)
    if not isinstance(target_revision, str) or not target_revision.strip():
        raise ValueError(
            "stage-native new-run init requires an exact target revision")
    worktree_revision = str(tp.git_head(ws) or "").strip()
    if not worktree_revision or worktree_revision == "unknown":
        raise ValueError(
            "stage-native new-run init requires an exact target revision")
    try:
        if __package__:
            from . import design_contract as stage_design_contract
            from . import review_evidence
        else:
            import design_contract as stage_design_contract
            import review_evidence
    except ImportError:
        import design_contract as stage_design_contract
        import review_evidence
    requirement_fingerprint = stage_design_contract.requirement_fingerprint(
        ws, rid)
    worktree_id = "worktree-" + review_evidence.content_fingerprint({
        "run_id": run_id,
        "repository_id": locator["repo_id"],
        "repository_key": locator["repository_key"],
        "target_revision": target_revision,
        "worktree_revision": worktree_revision,
    })[:24]
    record = {
        "schema": _STAGE_ROOT_AUTHORITY_SCHEMA,
        "run_id": run_id,
        "repository_id": str(locator["repo_id"]),
        "repository_key": str(locator["repository_key"]),
        "worktree_id": worktree_id,
        "target_revision": str(target_revision),
        "worktree_revision": worktree_revision,
        "requirement_id": rid,
        # The current requirement store is content-versioned rather than
        # counter-versioned.  Its exact retained content fingerprint is the
        # immutable revision consumed by the root stage.
        "requirement_revision": requirement_fingerprint,
        "requirement_fingerprint": requirement_fingerprint,
        "actor": actor,
        "session_id": session_id,
        "authority_revision": 1,
    }
    record["fingerprint"] = review_evidence.content_fingerprint(record)
    if set(record) != _STAGE_ROOT_AUTHORITY_FIELDS:
        raise ValueError("stage-native root bootstrap authority is invalid")
    return record


def init(ws: str, goal: str, spec_path: str | None = None,
         max_fix_cycles: int = 2, checkpoints=None,
         requirement_id: str | None = None, parallel: bool = False,
         design: bool = False, design_only: bool = False,
         force: bool = False, by: str | None = None,
         reuse_approved_design: bool = False) -> dict:
    if _stage_mode() == "new-run":
        try:
            prior_singleton = _load_raw(ws)
        except Exception as exc:
            return {
                "error": "stage-native new-run init cannot verify existing "
                         f"singleton/history: {exc.__class__.__name__}: {exc}",
                "refused": True,
            }
        if prior_singleton is not None:
            return {
                "error": ("stage-native new-run init refuses existing "
                          "singleton/history; start a fresh governed run "
                          "instead of --force replacement"),
                "refused": True,
                "step": prior_singleton.get("step"),
            }
    if refusal := _stage_loop_mutation_refusal(ws):
        return refusal
    try:
        root_authority = _stage_native_init_authority(
            ws, requirement_id, by)
    except Exception as exc:
        return {"error": str(exc), "refused": True}
    checkpoints = list(checkpoints if checkpoints is not None else
                       ["plan", "em"])
    # v2.3.0: init over an IN-FLIGHT loop refuses by default — one mistyped
    # init must not silently reset a governed session's step, tasks,
    # approvals and baseline. `force` discards deliberately, and even then
    # the prior state file is archived (visible, recoverable), never erased.
    existing = load(ws)
    reused_design = {}
    archived_to = None
    if reuse_approved_design:
        blockers = []
        if not existing:
            blockers.append("no prior loop exists")
        elif existing.get("step") != "done" or not existing.get(
                "design_only"):
            blockers.append("prior loop is not a completed design-only run")
        elif not existing.get("design_fingerprint"):
            blockers.append("prior design has no approved fingerprint")
        if not str(by or "").strip():
            blockers.append("reuse needs --by with attributable human authority")
        if existing and requirement_id != existing.get("requirement_id"):
            blockers.append("requirement does not match the approved design")
        if existing and spec_path != existing.get("spec_path"):
            blockers.append("spec path does not match the approved design")
        if existing and existing.get("design_fingerprint"):
            blockers.extend(_design_current_errors(ws, existing))
        if blockers:
            return {"error": "approved Design reuse refused", "refused": True,
                    "blockers": blockers,
                    "step": existing.get("step") if existing else None}
        reused_design = {
            "design_fingerprint": existing["design_fingerprint"],
            "design_approved_by": existing.get("design_approved_by"),
            **({"design_graph_fingerprint":
                existing["design_graph_fingerprint"]}
               if existing.get("design_graph_fingerprint") else {}),
            "design_reused_by": str(by).strip(),
        }
        src = _loop_path(ws) if os.path.exists(_loop_path(ws)) \
            else _legacy_loop_path(ws)
        archived_to = _loop_path(ws) + time.strftime(
            ".continued-%Y%m%d-%H%M%S") + f".{os.getpid()}"
        os.makedirs(_state_dir(ws), exist_ok=True)
        os.replace(src, archived_to)
        tp.trace(ws, "loop_design_reused", prior_step=existing.get("step"),
                 archived_to=archived_to,
                 fingerprint=existing["design_fingerprint"], by=by)
    elif existing and existing.get("step") not in TERMINAL_STEPS:
        if not force:
            return {"error": "an active loop already exists at step="
                             f"'{existing.get('step')}' — refusing to discard "
                             "its progress. Finish or abort it first "
                             "(`loop resolve abort`), or re-run init with "
                             "force to archive the current state and restart.",
                    "refused": True, "step": existing.get("step")}
        src = _loop_path(ws) if os.path.exists(_loop_path(ws)) \
            else _legacy_loop_path(ws)
        archived_to = _loop_path(ws) + time.strftime(
            ".replaced-%Y%m%d-%H%M%S") + f".{os.getpid()}"
        os.makedirs(_state_dir(ws), exist_ok=True)
        os.replace(src, archived_to)
        tp.trace(ws, "loop_init_replaced", prior_step=existing.get("step"),
                 archived_to=archived_to)
    state = {
        "governance_revision": 2,
        # Workers submit evidence; only the driver asks the engine to evaluate
        # a gate.  Older persisted loops omit this flag and remain resumable.
        "submission_required": True,
        "graph_governance": True,
        "goal": goal,
        "parallel": bool(parallel),
        "design_required": bool(design or design_only
                                or reuse_approved_design),
        "design_only": bool(design_only),
        "requirement_id": requirement_id,
        "spec_path": spec_path,
        "max_fix_cycles": int(max_fix_cycles),
        "checkpoints": checkpoints,
        "step": ("plan" if reuse_approved_design else
                 "design" if spec_path and (design or design_only)
                 else "plan" if spec_path else "pm"),
        "tasks": None,
        "current_task": 0,
        "consumed_host_decisions": {},
        "consumed_host_events": {},
        "authority_effect_outbox": {},
        **reused_design,
        # This marker is minted only by an attributable new-run init.  The
        # first `loop next` consumes it while atomically committing the exact
        # root; arbitrary pre-existing singleton history is never inferred to
        # be a canary.  The verified v4 binding replaces this eligibility.
        **({"_stage_native_new_run_pristine": True,
            "_stage_native_root_authority": root_authority}
           if root_authority is not None else {}),
    }
    save(ws, state)
    tp.trace(ws, "loop_init", goal=goal, spec_path=spec_path,
             first_step=state["step"], max_fix_cycles=max_fix_cycles,
             checkpoints=checkpoints, design=bool(design or design_only),
             design_only=bool(design_only))
    out = dict(state)
    if archived_to:
        out["previous_loop_archived"] = archived_to
        out["note"] = (
            f"approved Design reused; prior terminal loop archived to "
            f"{archived_to}" if reuse_approved_design else
            f"previous in-flight loop archived to {archived_to}")
    # v2.0.0: point the driver at prior gate snapshots (context cache) -
    # read the published state instead of re-deriving it.
    with contextlib.suppress(Exception):
        art = os.path.join(tp.store_root(ws), "artifacts")
        tracks = sorted(os.listdir(art)) if os.path.isdir(art) else []
        if tracks:
            out["prior_artifacts"] = {
                "path": art, "tracks": tracks,
                "note": "prior gate snapshots (dashboard, plan, "
                        "findings, graph, HEADLINES) - read these "
                        "before re-deriving context"}
    return out


# --------------------------------------------------------------- contracts

def _step_contract(step: str, state: dict, ws: str | None = None) -> dict:
    task = _current_task(state)
    if step == "pm":
        return tp.build_contract(
            f"PM: {state['goal']}", read_only=True,
            write_allow=["specs/**", "docs/**"],
            tools=["Read", "Grep", "Glob", "WebSearch", "Bash", "Write"])
    if step == "design":
        return tp.build_contract(
            f"DESIGN: {state['goal']}", read_only=True,
            write_allow=["design/**"],
            tools=["Read", "Grep", "Glob", "WebSearch", "Bash", "Write"])
    if step == "plan":
        return tp.build_contract(
            f"PLAN: {state['goal']}", read_only=True, write_allow=["plan/**"],
            tools=["Read", "Grep", "Glob", "Bash", "Write"])
    if step in ("execute", "fix"):
        verb = "EXECUTE" if step == "execute" else "FIX"
        return tp.build_contract(
            f"{verb}: {task['id']}", scope=task["scope"],
            test_command=task.get("tests"), plan_minted=True, regression_gate=True,
            tools=["Read", "Grep", "Glob", "Bash", "Write", "Edit",
                   "MultiEdit"])
    if step == "evaluate":
        return tp.build_contract(
            f"EVALUATE: {task['id']}", read_only=True,
            write_allow=runtime_storage.worker_write_allow(ws, ".eval/**"),
            tools=["Read", "Grep", "Glob", "Bash", "Write"])
    if step == "em":
        return tp.build_contract(
            "EM review", read_only=True,
            write_allow=runtime_storage.worker_write_allow(ws, ".em-review/**"),
            tools=["Read", "Grep", "Glob", "Bash", "Write", "Edit"])
    raise ValueError(f"no contract for step {step}")


def _bind_worker_submission(ws: str, state: dict, step: str,
                            contract: dict, task: dict | None) -> dict:
    """Bind worker lifecycle to its exact loop submission, without gating."""
    if not state.get("submission_required") or step not in {
            "execute", "fix", "evaluate", "em"}:
        return contract
    task_name = ((task or {}).get("id") or "engineering-signoff"
                 if step == "em" else (task or {}).get("id"))
    lifecycle = contract.get("worker_lifecycle") or {}
    return tp.bind_submission_contract(
        contract, ws, task=str(task_name), stage=step,
        slot=lifecycle.get("slot") or tp.task_slot(),
        locator={"type": "loop_submission"},
        validation_rule="loop-submission/v1")


def _current_task(state: dict):
    tasks = state.get("tasks")
    if not tasks:
        return None
    i = state.get("current_task", 0)
    return tasks[i] if 0 <= i < len(tasks) else None


def _parallel_evaluate_workspace(
        ws: str, state: Mapping[str, object],
        task: Mapping[str, object]) -> tuple[str | None, str | None]:
    """Resolve one unambiguous worker tree for parallel Evaluate.

    Falling back to the primary checkout here would pair worker target bytes
    with an unrelated primary graph.  Keep that identity failure explicit so
    missing or ambiguous worker state cannot silently weaken review routing.
    """
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        return None, ("parallel Evaluate task worktree is ambiguous: task "
                      "identity is missing")
    raw = str(task.get("workspace") or "").strip()
    if not raw or not os.path.isdir(raw):
        return None, ("parallel Evaluate task worktree is missing; restore "
                      "the exact claimed worktree and its graph before retry")
    supplied = os.path.abspath(os.path.expanduser(raw))
    worker = os.path.realpath(supplied)
    tasks = state.get("tasks")
    rows = tasks if isinstance(tasks, list) else []
    owners = sorted(str(row.get("id") or "")
                    for row in rows if isinstance(row, Mapping) and
                    row.get("workspace") and
                    os.path.realpath(str(row["workspace"])) == worker)
    if owners != [task_id]:
        return None, ("parallel Evaluate task worktree is ambiguous: "
                      f"workspace is assigned to {', '.join(owners)}")
    try:
        locator = runtime_storage.load_workspace_locator(ws)
        recovered = False
        if locator is None:
            legacy_expected = os.path.realpath(os.path.join(
                ws, ".tp-work", task_id))
            if worker == legacy_expected:
                expected_path = legacy_expected
            else:
                identity = runtime_storage.resolve_repository_identity(ws)
                managed_tasks = os.path.realpath(os.path.join(
                    runtime_storage.taskplane_home(), "checkouts",
                    identity.key, "worktrees", "tasks"))
                worker_parent = os.path.dirname(worker)
                run_parent = os.path.dirname(worker_parent)
                is_managed_shape = (
                    os.path.realpath(run_parent) == managed_tasks and
                    os.path.basename(worker) ==
                    runtime_storage._worktree_token(task_id))
                if not is_managed_shape:
                    return None, ("parallel Evaluate task worktree is not "
                                  "the exact canonical managed task "
                                  "worktree for this task")
                target_commit = str(task.get("target_commit") or "")
                if not target_commit:
                    return None, ("parallel Evaluate task worktree target "
                                  "commit is missing; the execute gate must "
                                  "bind the registered branch tip before "
                                  "evaluation")
                runtime_storage.reconstruct_worker_locator(
                    ws, supplied, task_id, target_commit=target_commit)
                worker_locator = runtime_storage.load_workspace_locator(worker)
                if worker_locator is None:
                    raise runtime_storage.StorageIdentityError(
                        "reconstructed worker locator is missing")
                expected_path = os.path.join(
                    worker_locator["home"], "checkouts",
                    worker_locator["repository_key"], "worktrees", "tasks",
                    str(worker_locator["run_id"]),
                    runtime_storage._worktree_token(task_id))
                recovered = True
        else:
            expected_path = runtime_storage.task_worktree_path(ws, task_id)
    except (OSError, ValueError, runtime_storage.StorageIdentityError) as exc:
        return None, ("parallel Evaluate task worktree identity is invalid: "
                      f"{exc}")
    expected_path = os.path.abspath(expected_path)
    managed_root = os.path.realpath(os.path.dirname(expected_path))
    expected = os.path.join(managed_root, os.path.basename(expected_path))
    try:
        is_contained = os.path.commonpath((managed_root, worker)) == \
            managed_root
    except ValueError:
        is_contained = False
    if os.path.normcase(os.path.normpath(supplied)) != \
            os.path.normcase(expected) or \
            os.path.realpath(os.path.dirname(supplied)) != managed_root or \
            os.path.basename(supplied) != os.path.basename(expected) or \
            worker != expected or not is_contained or os.path.islink(supplied):
        return None, ("parallel Evaluate task worktree is not the exact "
                      "canonical managed task worktree for this task")
    if worker == os.path.realpath(ws):
        return None, ("parallel Evaluate task worktree is ambiguous: the "
                      "primary checkout cannot serve as task graph evidence")
    try:
        registration = (None if recovered else
                        runtime_storage.load_task_worktree_registration(
                            ws, task_id))
        primary_identity = runtime_storage.resolve_repository_identity(ws)
        worker_identity = runtime_storage.resolve_repository_identity(worker)
    except (OSError, ValueError, runtime_storage.StorageIdentityError) as exc:
        return None, ("parallel Evaluate task worktree identity is invalid: "
                      f"{exc}")
    registered_repository = ((registration or {}).get("repository") or {})
    if not recovered and (not isinstance(registered_repository, Mapping) or \
            registration is None or \
            registration.get("linked") is not True or \
            os.path.realpath(str(
                registration.get("primary_checkout") or "")) != \
            os.path.realpath(ws) or \
            os.path.realpath(str(
                registration.get("path") or "")) != worker or \
            registered_repository.get("repo_id") != \
            primary_identity.repo_id or \
            worker_identity.repo_id != primary_identity.repo_id):
        return None, ("parallel Evaluate task worktree identity does not "
                      "match this run and repository")
    if not recovered and locator is not None and registration.get("run_id") != \
            locator.get("run_id"):
        return None, ("parallel Evaluate task worktree identity does not "
                      "match this run and repository")
    return worker, None


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
                "tp graph edge <consumer-module> <db-module> --kind data")
        diff = _sp.run(["git", "diff", "-U0", base, "--", *changed[:50]],
                       cwd=ws, capture_output=True, text=True
                       , encoding="utf-8", errors="replace").stdout[:60000]
        added = "\n".join(l for l in diff.splitlines()
                           if l.startswith("+"))
        if _re.search(r"https?://|requests\.|urllib|fetch\(|axios"
                      r"|http\.client|HttpClient", added):
            nudges.append(
                "diff adds HTTP calls - cross-service effects are not "
                "import edges; record them: tp graph edge <this-module> "
                "<called-service> --kind runtime")
        if _re.search(r"publish|subscribe|topic|queue|kafka|sqs|rabbit"
                      r"|emit\(", added, _re.I):
            nudges.append(
                "diff touches messaging (topic/queue) - consumers are "
                "invisible to the import graph; record them: tp graph "
                "edge <consumer> <contract:event-name> --kind consumes; "
                "record the producer with --kind provides. Dependency edges "
                "point from the dependent to the contract so contract changes "
                "impact consumers in the correct direction")
    except (OSError, _sp.SubprocessError, UnicodeDecodeError) as e:
        # Degraded nudging must be VISIBLE, never silent (v2.3.0): the
        # reviewer loses side-effect-channel hints, so say so once.
        import sys as _sys
        print(f"taskplane: edge-nudge scan degraded ({e.__class__.__name__}: "
              f"{e}) — record runtime edges manually via `tp graph edge`",
              file=_sys.stderr)
        try:
            tp.trace(ws, "edge_nudges_failed", error=str(e))
        except Exception:
            pass
    return nudges


def _diff_files(ws: str, base: str) -> list:
    import subprocess

    def run(args):
        return subprocess.run(["git", *args], cwd=ws, capture_output=True,
                              text=True, encoding="utf-8", errors="replace").stdout
    return [f for f in (run(["diff", "--name-only", base])
                        + run(["ls-files", "--others",
                               "--exclude-standard"])).splitlines() if f]


_REVIEW_RUNTIME_BUNDLE = None
_REVIEW_REQUIRED_MODULES = (
    "storage", "taskplane_lite", "review_evidence", "review",
    "graph_quality")
_REVIEW_MODULE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _review_source_stat(value) -> tuple:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _verified_review_module_source(checkout_root: str,
                                   module_name: str) -> dict:
    """Pin verified bytes for one direct target-checkout Python module."""
    root = os.path.realpath(os.path.abspath(checkout_root))
    if not os.path.isdir(root) or os.path.islink(root) or \
            not _REVIEW_MODULE_NAME.fullmatch(str(module_name or "")):
        raise RuntimeError("target review module root or name is invalid")
    path = os.path.abspath(os.path.join(root, module_name + ".py"))
    resolved = os.path.realpath(path)
    try:
        contained = os.path.commonpath((root, resolved)) == root
    except ValueError:
        contained = False
    if not contained or resolved != path:
        raise RuntimeError(
            f"target review module escapes checkout: {module_name}")
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise RuntimeError(
            f"required target review module is unavailable: {module_name}") \
            from exc
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError(
            f"target review module is not a regular file: {module_name}")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(
            f"target review module could not be pinned: {module_name}") \
            from exc
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
        raise RuntimeError(
            f"target review module changed while pinned: {module_name}") \
            from exc
    handle_stable = _review_source_stat(opened_before) == \
        _review_source_stat(opened_after)
    if _review_source_stat(before) != _review_source_stat(after) or not handle_stable or \
            not os.path.samestat(before, opened_before) or \
            not stat.S_ISREG(before.st_mode) or \
            not stat.S_ISREG(opened_before.st_mode) or \
            int(opened_after.st_size) != len(source) or \
            os.path.realpath(path) != path:
        raise RuntimeError(
            f"target review module changed while pinned: {module_name}")
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
        self.namespace = hashlib.sha256(
            self.root.encode("utf-8")).hexdigest()[:16]

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
                raise RuntimeError(
                    f"required target review module was not pinned: {name}")
            try:
                current = os.lstat(source["path"])
            except OSError as exc:
                raise RuntimeError(
                    f"required target review module changed: {name}") from exc
            if os.path.realpath(source["path"]) != source["path"] or \
                    _review_source_stat(current) != source["identity"]:
                raise RuntimeError(
                    f"required target review module changed: {name}")

    def _target_import(self, import_name, globals=None, locals=None,
                       fromlist=(), level=0):
        if level == 0:
            target = self._has_target(import_name)
            if target:
                return self.load(target)
        imported = self._base_import(
            import_name, globals, locals, fromlist, level)
        path = os.path.realpath(str(getattr(imported, "__file__", "") or ""))
        if path:
            try:
                contained = os.path.commonpath((self.root, path)) == self.root
            except ValueError:
                contained = False
            if not contained and os.path.basename(os.path.dirname(path)) == \
                    "taskplane":
                raise ImportError(
                    f"launcher-owned taskplane module refused: {import_name}")
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
            exec(compile(source["source"], source["path"], "exec"),
                 module.__dict__)
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
    imported_runtime_path = os.path.realpath(str(
        getattr(tp, "__file__", "") or ""))
    storage_path = os.path.realpath(str(
        getattr(imported_storage, "__file__", "") or ""))
    evidence_path = os.path.realpath(str(
        getattr(imported_evidence, "__file__", "") or ""))
    review_path = os.path.realpath(str(
        getattr(imported_review, "__file__", "") or ""))
    graph_quality_path = os.path.realpath(str(
        getattr(imported_graph_quality, "__file__", "") or ""))
    consistent = (
        not force_private
        and imported_runtime_path == pinned["taskplane_lite"]["path"]
        and storage_path == pinned["storage"]["path"]
        and evidence_path == pinned["review_evidence"]["path"]
        and review_path == pinned["review"]["path"]
        and graph_quality_path == pinned["graph_quality"]["path"]
        and getattr(imported_evidence, "tp", None) is tp
        and getattr(imported_evidence, "runtime_storage", None)
        is imported_storage
        and getattr(imported_review, "tp", None) is tp
        and getattr(imported_review, "runtime_storage", None)
        is imported_storage
        and getattr(imported_review, "review_evidence_runtime", None)
        is imported_evidence
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
        runtime_import = runtime.__dict__["__builtins__"]["__import__"]
        if getattr(evidence, "tp", None) is not runtime or \
                getattr(evidence, "runtime_storage", None) is not \
                target_storage or getattr(review_kernel, "tp", None) is not \
                runtime or getattr(review_kernel, "runtime_storage", None) is \
                not target_storage or getattr(
                    review_kernel, "review_evidence_runtime", None) is not \
                evidence or runtime_import("storage") is not target_storage:
            raise RuntimeError(
                "target review runtime bundle is internally inconsistent")
    _REVIEW_RUNTIME_BUNDLE = {
        "root": root, "runtime": runtime,
        "evidence": evidence, "review": review_kernel,
        "graph_quality": graph_quality_kernel, "loader": loader,
    }
    return runtime, evidence, review_kernel


class _ReviewGraphQualityError(RuntimeError):
    """Current graph evidence cannot authorize selective lens dispatch."""

    def __init__(self, quality: dict, reference: dict):
        self.quality = quality
        self.reference = reference
        reasons = ", ".join(quality.get("reasons") or []) or \
            "graph quality is incomplete"
        super().__init__(reasons)


def _strict_review_graph_quality(review_ws: str, *, target: dict,
                                 graph: dict, impact: dict, files: list,
                                 symbols: list, review_module,
                                 evidence_module) -> tuple[dict, dict, object]:
    """Persist admissible current graph evidence before the one route."""
    graph_quality = (_REVIEW_RUNTIME_BUNDLE or {}).get("graph_quality")
    if graph_quality is None:
        raise RuntimeError("target graph-quality runtime is unavailable")
    source_change = any(os.path.splitext(path)[1].lower() in {
        ".py", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".go",
        ".cs", ".java", ".rb"} for path in files)
    raw_expander = review_module.bounded_caller_expander(graph)
    expansion_cache = {}

    def one_bounded_expansion(**kwargs):
        # Preflight and ReviewKernel consume one identical expansion result;
        # the adapter itself is invoked at most once.
        if "result" not in expansion_cache:
            expansion_cache["result"] = raw_expander(**kwargs)
        return json.loads(json.dumps(expansion_cache["result"]))

    bounded_expander = (one_bounded_expansion
                        if symbols or not source_change else None)
    quality = graph_quality.assess(
        graph, target_head=str(target.get("head") or ""),
        changed_files=files, changed_symbols=symbols, impact=impact,
        caller_expander=bounded_expander, snapshot={
            "target_fingerprint": target.get("fingerprint"),
            "target_head": target.get("head"),
        })
    store = evidence_module.ArtifactStore(review_ws)
    reference = store.put(
        "graph-quality", quality, fingerprint=quality["fingerprint"])
    if quality.get("status") != "complete" or \
            quality.get("sufficient") is not True:
        raise _ReviewGraphQualityError(quality, reference)
    return quality, reference, bounded_expander


def _review_kernel(ws: str, diff_ws: str, *, base: str, step: str,
                   task: dict | None, graph: dict, impact: dict,
                   requirement: dict | None,
                   retry_context: dict | None = None,
                   delivery_mode_receipt: object =
                   _DELIVERY_MODE_AUTHORITY_UNSET) -> tuple[dict, dict]:
    """One evidence/routing kernel shared by Evaluate and final EM."""
    import hashlib
    import subprocess
    runtime_kernel, review_evidence, review = _review_runtime_modules()

    files = [f for f in _diff_files(diff_ws, base)
             if not f.startswith(lens_router.LOOP_OWNED) and
             (not task or not task.get("scope") or
              runtime_kernel.match_any(f, task.get("scope") or []))]
    diff_rc, patch = review.canonical_diff_patch(
        diff_ws, base, paths=files)
    if diff_rc:
        reason = ("canonical governed diff exceeds the 400000-byte bound"
                  if diff_rc == review.CANONICAL_DIFF_TOO_LARGE else
                  "canonical diff derivation failed")
        raise review.ReviewKernelError(reason)
    if files and not patch:
        raise review.ReviewKernelError(
            "canonical governed diff is empty for changed task files")
    head = tp.git_head(diff_ws) or ""
    target_material = {"workspace": os.path.realpath(diff_ws), "head": head,
                       "base": base, "step": step,
                       "task": (task or {}).get("id")}
    target = {**target_material, "fingerprint": hashlib.sha256(
        json.dumps(target_material, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")).hexdigest()}
    store = review_evidence.ArtifactStore(diff_ws)
    # Raw source diffs are private working evidence, not permanent canonical
    # history. The single store lock covers restart sweep + put + capacity.
    locator = runtime_storage.load_workspace_locator(diff_ws) or {}
    diff_ref = store_retained_review_diff(
        diff_ws, store=store, payload=_retained_review_diff_payload(
            base=base, files=files, patch=patch,
            run_id=str(locator.get("run_id") or "legacy-loop"),
            review_id=target["fingerprint"]))
    stage = "review" if step == "em" else EVALUATE_ROUTE_STAGE
    changed_symbols = review.changed_symbols_from_patch(patch)
    quality_ref = None
    if step == "evaluate":
        _, quality_ref, caller_expander = _strict_review_graph_quality(
            diff_ws, target=target, graph=graph, impact=impact, files=files,
            symbols=changed_symbols, review_module=review,
            evidence_module=review_evidence)
    else:
        caller_expander = review.bounded_caller_expander(graph)
    delivery_mode_argument = (
        {"delivery_mode_receipt": delivery_mode_receipt}
        if delivery_mode_receipt is not _DELIVERY_MODE_AUTHORITY_UNSET else {})
    manifest = review.start_review(
        diff_ws, target=target, graph=graph, impact=impact,
        diff={"files": files,
              "changed_symbols": changed_symbols,
              "artifact": review._portable_ref(diff_ref)},
        requirement=requirement or {},
        acceptance=(requirement or {}).get("acceptance") or [],
        contracts=(task or {}).get("contracts") or [],
        stage=stage,
        task_type=(task or {}).get("type"), base=base,
        caller_expander=caller_expander,
        routing_content=review.changed_content_from_patch(patch),
        retry_lenses=((retry_context or {}).get("lenses")
                      if step == "evaluate" else None),
        retry_source_run_id=((retry_context or {}).get("source_run_id")
                             if step == "evaluate" else None),
        **delivery_mode_argument)
    state = review._load_state(diff_ws, manifest.get("run_id"))
    if quality_ref is not None and state.get("quality") != quality_ref:
        # A route is immutable. A mismatch is terminal evidence, never a
        # reason to patch or invoke the selector again after sealing.
        raise review.ReviewKernelError(
            "sealed graph quality differs from pre-routing authority")
    return manifest, (state.get("routing") or {"lenses": [], "context": {
        "status": manifest.get("status"), "breadth": "routed"}})


def _bind_stateless_review_contract_actions(
        review_ws: str, manifest: dict, *, task_id: str,
        now: int | None = None) -> dict:
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
            raise ValueError(
                "review contract bootstrap lacks exact worker identity")
        action_material = {
            "run_id": run_id, "task_id": str(task_id),
            "slot_id": lease.get("slot_id"),
            "lease_fingerprint": lease.get("lease_fingerprint"),
            "worker_identity": worker_identity,
        }
        action_id = "review-action-" + hashlib.sha256(json.dumps(
            action_material, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()[:24]
        action = runtime_kernel.issue_review_contract_action(
            review_ws, run_id=run_id, task_id=str(task_id),
            role_marker=role_marker, worker_identity=worker_identity,
            action_id=action_id, lease=lease,
            producer_contract=producer,
            result_path=str(brief.get("result_path") or ""), now=now)
        expected = {
            "run_id": run_id, "task_id": str(task_id),
            "role_marker": role_marker,
            "worker_identity": worker_identity,
            "action_id": action_id,
            "lens_ids": list(lease.get("lens_ids") or []),
            "target_fingerprint": str(
                lease.get("target_fingerprint") or ""),
            "lease_fingerprint": str(
                lease.get("lease_fingerprint") or ""),
            "canonical_revision": int(
                lease.get("canonical_revision") or 0),
        }
        encode = lambda value: base64.urlsafe_b64encode(json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).decode("ascii").rstrip("=")
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
            os.path.realpath(os.path.join(
                os.path.dirname(__file__), "tp.py")),
            "review", "activate-contract", "--workspace",
            os.path.realpath(review_ws), "--task-slot",
            producer["task_slot"], "--signed-action", action_token,
            "--expected-identity", expected_token,
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
    if outstanding_members:
        if (any(not isinstance(row, Mapping) for row in wait_policies) or
                any(dict(row) != dict(wait_policies[0])
                    for row in wait_policies[1:])):
            raise ValueError(
                "review contract bootstrap needs one shared wait policy")
        bound["wait_invocation"] = event_wait_invocation(
            wait_policies[0], outstanding_members)
        bound["collection"] = {
            "schema": "taskplane.review-collection-bridge/v1",
            "function": "loop.collect_review_bridge",
            "run_id": run_id,
            "release_incomplete_producers": True,
        }
    return bound


# --------------------------------------------------------------- parallel

def _scopes_overlap(a, b) -> bool:
    """Two scopes conflict when one's fixed prefix contains the other's, on
    path-segment boundaries — conflicting tasks are serialized into later
    waves. Segment-aware so sibling dirs (src/a vs src/ab) do NOT collide,
    and empty-prefix globs don't conflict with everything. (The path math
    itself lives in the kernel — tp.scope_stems / tp.seg_prefix.)"""
    sa, sb = tp.scope_stems(a), tp.scope_stems(b)
    return any(tp.seg_prefix(x, y) or tp.seg_prefix(y, x)
               for x in sa for y in sb)


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
            if path.endswith(".py") and "/" in path and \
                    os.path.isfile(os.path.join(ws, path)):
                present.add(path)
    return present


def select_ready_tasks(
        tasks: list[dict], *, passed: set[str],
        repository_files: set[str],
        allow_isolated_variants: bool = False) \
        -> tuple[list[dict], list[dict], dict]:
    """Select the executable pairwise-disjoint ready set from the Plan.

    This is the runtime consumer of ``plan_topology``.  In particular, it
    respects implicit missing-test-artifact predecessors, so an apparently
    disjoint consumer cannot become false-ready before its test producer.
    """
    topology = plan_topology.classify_plan(
        tasks, repository_files=repository_files)
    by_id = {str(task.get("id")): task for task in tasks}
    pair_map = {
        frozenset((str(row["left"]), str(row["right"]))): row
        for row in topology["pairs"]
    }
    selected: list[dict] = []
    held: list[dict] = []
    for task_id in topology["task_ids"]:
        task = by_id[task_id]
        if task.get("status", "pending") != "pending":
            continue
        dependencies = set(topology["effective_dependencies"][task_id])
        unmet = sorted(dependencies - passed)
        if unmet:
            shared_owner = next((
                pair_map[frozenset((task_id, dependency))]["shared_owner"]
                for dependency in unmet
                if frozenset((task_id, dependency)) in pair_map
            ), f"dependency:{unmet[0]}")
            held.append({
                "task": task_id,
                "reason": "waiting on deps: " + ",".join(unmet),
                "shared_owner": shared_owner,
            })
            continue
        missing = list((topology.get("missing_test_assets") or {}).get(task_id) or [])
        if missing:
            held.append({
                "task": task_id,
                "reason": "missing test assets: " + ",".join(missing),
                "shared_owner": "test-artifact:" + missing[0],
            })
            continue
        blocker = next((
            pair_map[frozenset((task_id, str(member["id"])))]
            for member in selected
            if pair_map[frozenset((task_id, str(member["id"])))]
            ["disposition"] == "serialized"
            and not (
                allow_isolated_variants
                and task.get("variant")
                and member.get("variant")
                and task.get("variant") != member.get("variant"))
        ), None)
        if blocker is not None:
            held.append({
                "task": task_id,
                "reason": f"serialized by {blocker['shared_owner']}",
                "shared_owner": blocker["shared_owner"],
            })
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
        run_id = "loop-" + hashlib.sha256(json.dumps({
            "workspace": os.path.realpath(ws),
            "goal": state.get("goal"),
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    source_sha = str(state.get("baseline") or tp.git_head(ws) or "unknown")
    design_fingerprint = str(state.get("design_fingerprint") or
                             hashlib.sha256(b"legacy-design").hexdigest())
    plan_fingerprint = str(state.get("plan_fingerprint") or "")
    if not plan_fingerprint:
        runtime_fields = {
            "status", "fix_cycles", "workspace", "target_commit",
            "_submission", "evaluation", "convergence_history",
            "convergence_revision", "reanchor_authority",
        }
        sealed_tasks = [
            {key: value for key, value in task.items()
             if key not in runtime_fields}
            for task in state.get("tasks") or []
            if isinstance(task, Mapping)
        ]
        plan_fingerprint = hashlib.sha256(json.dumps(
            sealed_tasks, sort_keys=True, separators=(",", ":"),
            default=str).encode()).hexdigest()
    return {
        "run_id": run_id, "source_sha": source_sha,
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
                **_dispatch_telemetry_identity(ws, locked),
                started_at=clock.wall_time())
            locked["dispatch_telemetry"] = ledger
        dispatch_telemetry.validate_ledger(ledger)
        return dict(ledger)


def _dispatch_binding_for_task(
        ledger: Mapping[str, object], task_id: str) -> dict | None:
    return next((dict(row) for row in reversed(
        list(ledger.get("bindings") or []))
        if row.get("task_id") == task_id and
        not row.get("finalized_receipt_fingerprint")), None)



def record_native_dispatch_observation(
        ws: str, *, expected: Mapping[str, object],
        native_task_name: str) -> dict:
    """Bind one actual Codex spawn to its emitted native intent."""
    intent_id = str(expected.get("intent_id") or "").strip()
    task_id = str(expected.get("ref") or "").strip()
    if not intent_id or not task_id:
        return {"status": "unavailable",
                "reason": "native dispatch intent identity is unavailable"}
    _ensure_dispatch_telemetry(ws)
    with mutate(ws) as locked:
        if locked is None:
            raise dispatch_telemetry.DispatchTelemetryError("no active loop")
        ledger = locked.get("dispatch_telemetry")
        dispatch_telemetry.validate_ledger(ledger)
        task = next((row for row in locked.get("tasks") or []
                     if str(row.get("id") or "") == task_id), None)
        if not isinstance(task, Mapping):
            raise dispatch_telemetry.DispatchTelemetryError(
                "native dispatch task is absent from the active Plan")
        existing = next((row for row in ledger.get("bindings") or []
                         if row.get("dispatch_id") == intent_id), None)
        observed_at = ((existing or {}).get(
            "started_at", SystemClock().wall_time()))
        return dispatch_telemetry.bind_dispatch(ledger, {
            "dispatch_id": intent_id,
            "thread_id": str(native_task_name or intent_id),
            "thread_type": "worker",
            "task_id": task_id,
            "dependencies": [str(value) for value in task.get("deps") or []],
            "shared_owner": None,
            "started_at": observed_at,
            "ended_at": ((existing or {}).get("ended_at", observed_at)),
            "wait_duration_seconds": 0,
            "correction_count": int(task.get("fix_cycles") or 0),
            "events": list((existing or {}).get("events") or []),
        })

def record_observed_dispatch_usage(
        ws: str, *, task_id: str, normalized_usage: Mapping[str, object],
        source: str) -> dict:
    """Production hook adapter: persist observed cumulative provider usage."""
    usage = spend.dispatch_usage(dict(normalized_usage))
    source_fingerprint = hashlib.sha256(
        os.path.realpath(str(source or "")).encode("utf-8")).hexdigest()
    with mutate(ws) as locked:
        if locked is None:
            raise dispatch_telemetry.DispatchTelemetryError("no active loop")
        ledger = locked.get("dispatch_telemetry")
        dispatch_telemetry.validate_ledger(ledger)
        binding = _dispatch_binding_for_task(ledger, str(task_id))
        if binding is None:
            raise dispatch_telemetry.DispatchTelemetryError(
                "observed usage has no task dispatch binding")
        return dispatch_telemetry.observe_usage(
            ledger, dispatch_id=str(binding["dispatch_id"]), usage=usage,
            source_fingerprint=source_fingerprint)


def finalize_observed_dispatch_usage(
        ws: str, *, task_id: str, ended_at: float | None = None) -> dict:
    """Finalize one hook-observed dispatch into the binding budget ledger."""
    clock = SystemClock()
    with mutate(ws) as locked:
        if locked is None:
            raise dispatch_telemetry.DispatchTelemetryError("no active loop")
        ledger = locked.get("dispatch_telemetry")
        dispatch_telemetry.validate_ledger(ledger)
        binding = _dispatch_binding_for_task(ledger, str(task_id))
        if binding is None:
            return {"status": "unavailable", "reason":
                    "task dispatch binding is unavailable"}
        if binding.get("usage") is None:
            return {"status": "unavailable", "reason":
                    "provider usage observation is unavailable"}
        return dispatch_telemetry.finalize_usage(
            ledger, dispatch_id=str(binding["dispatch_id"]),
            ended_at=float(ended_at if ended_at is not None
                           else clock.wall_time()), clock=clock,
            events=[{"kind": "complete", "sequence": 1}])


def _verified_stage_loop_wave_split(
        ws: str, ready: list[dict], *,
        known_bindings: Mapping[str, str] | None = None) \
        -> dict[str, str] | None:
    """Recover one exact, still-current loop wave split.

    The RunStore commit and ``loop.json`` binding are separate durability
    boundaries.  A host can therefore stop after ``split_stage`` replaced the
    foreground parent but before the task table was updated.  At that point
    the active projection is intentionally ambiguous, so recovery must inspect
    persisted receipts before asking ``_stage_loop_context`` for a foreground.
    """
    if _stage_mode() == "disabled" or not ready:
        return None
    locator = runtime_storage.load_workspace_locator(ws)
    if not isinstance(locator, Mapping):
        return None
    run_id = str(locator.get("run_id") or "")
    if not run_id:
        return None
    store = _stage_store(ws, run_id)
    manifest = store.load(run_id)
    if manifest.get("schema") != "taskplane.run/v4":
        return None
    try:
        if __package__:
            from . import stage_entities
        else:
            import stage_entities
    except ImportError:
        import stage_entities

    operations = manifest.get("stage_operations") or {}
    if not isinstance(operations, Mapping):
        raise ValueError("stage-native split operation index is invalid")
    heads = manifest.get("stage_heads") or {}
    projection = manifest.get("active_stage_projection") or {}
    task_ids = [str(task.get("id") or "") for task in ready]
    if not all(task_ids) or len(set(task_ids)) != len(task_ids):
        raise ValueError("stage-native wave task identity is invalid")
    expected_bindings = ({str(key): str(value)
                          for key, value in known_bindings.items()}
                         if isinstance(known_bindings, Mapping) else {})
    if expected_bindings and set(expected_bindings) != set(task_ids):
        raise ValueError("stage-native wave bindings are partial")
    read_object = getattr(store, "read_stage_object", None)
    if not callable(read_object):
        raise ValueError("stage store cannot verify split child heads")
    candidates: list[dict[str, str]] = []
    for raw in operations.values():
        if not isinstance(raw, dict) or raw.get("operation") != "split_stage":
            continue
        checked = tp.verify_stage_receipt(
            raw, expected_operation="split_stage")
        operation_id = str(checked["operation_id"])
        if not operation_id.startswith("loop-wave-split-"):
            continue
        result = checked.get("result") or {}
        parent_head = result.get("parent_head")
        child_heads = result.get("child_heads")
        receipt_projection = result.get("active_stage_projection")
        if not isinstance(parent_head, Mapping) or not isinstance(
                child_heads, Mapping) or not isinstance(
                receipt_projection, Mapping):
            raise ValueError("loop wave split receipt result is invalid")
        parent_summary = parent_head.get("summary")
        if not isinstance(parent_summary, Mapping):
            raise ValueError("loop wave split parent head is invalid")
        parent_id = str(parent_summary.get("stage_id") or "")
        if not parent_id:
            continue
        expected_ids = [stage_entities.split_child_id(
            run_id, parent_id, operation_id, ordinal)
            for ordinal in range(len(child_heads))]
        if set(child_heads) != set(expected_ids):
            raise ValueError(
                "loop wave split child identity does not match its receipt")
        if checked.get("stage_ids") != sorted([parent_id, *expected_ids]):
            raise ValueError("loop wave split receipt stage set is invalid")

        if heads.get(parent_id) != parent_head:
            continue
        parent = _indexed_stage(store, manifest, run_id, parent_id)
        if parent.get("stage_kind") != "build" or \
                parent.get("state") != "terminal" or \
                parent.get("outcome") != "closed":
            raise ValueError("loop wave split parent is not closed Build")
        receipt_lineage = result.get("lineage") or []
        manifest_lineage = manifest.get("lineage") or []
        lineage_fingerprints = {
            str(row.get("fingerprint")) for row in manifest_lineage
            if isinstance(row, Mapping)}
        if not isinstance(receipt_lineage, list) or any(
                not isinstance(row, Mapping) or
                str(row.get("fingerprint")) not in lineage_fingerprints
                for row in receipt_lineage):
            raise ValueError("loop wave split lineage is not current")

        bindings: dict[str, str] = {}
        roots: list[str] = []
        for ordinal, child_id in enumerate(expected_ids):
            original_head = child_heads[child_id]
            if not isinstance(original_head, Mapping) or not isinstance(
                    original_head.get("object"), Mapping):
                raise ValueError("loop wave split child head is invalid")
            original = read_object(run_id, dict(original_head["object"]))
            deliverables = list(original.get("deliverables") or [])
            if len(deliverables) != 1:
                raise ValueError(
                    "loop wave split child deliverable is not singular")
            task_id = str(deliverables[0])
            if task_id in bindings:
                raise ValueError("loop wave split task binding is duplicated")
            child_id = expected_ids[ordinal]
            child = _indexed_stage(store, manifest, run_id, child_id)
            if child.get("stage_kind") != "build" or \
                    child.get("state") != "active" or \
                    list(child.get("parent_stage_ids") or []) != [parent_id] or \
                    list(child.get("predecessor_stage_ids") or []) or \
                    list(child.get("deliverables") or []) != [task_id]:
                raise ValueError(
                    "loop wave split child does not match its task binding")
            stable_fields = (
                "run_id", "stage_id", "stage_kind", "requirement", "design",
                "parent_stage_ids", "predecessor_stage_ids",
                "input_manifest_ref", "execution_root_id", "deliverables",
                "selected_artifacts", "budget", "dependencies", "contracts",
                "authority", "created_at")
            if any(child.get(field) != original.get(field)
                   for field in stable_fields) or int(
                       child.get("aggregate_revision") or 0) < int(
                           original.get("aggregate_revision") or 0):
                raise ValueError(
                    "loop wave split child identity advanced incompatibly")
            roots.append(str(child.get("execution_root_id") or ""))
            bindings[task_id] = child_id
        if not all(roots) or len(set(roots)) != len(roots) or \
                str(parent.get("execution_root_id") or "") in roots:
            raise ValueError("loop wave split execution roots are not unique")
        if expected_bindings:
            if any(bindings.get(task_id) != child_id
                   for task_id, child_id in expected_bindings.items()):
                continue
            candidates.append(dict(expected_bindings))
        elif list(bindings) == task_ids and \
                int(manifest.get("revision") or 0) == int(
                    checked["committed_revision"]) and \
                projection == receipt_projection:
            # Without a persisted singleton binding, only the exact
            # post-split state is safe to reconstruct.  Once a child has
            # dispatched, the durable binding is required to identify which
            # split owns the remaining work.
            candidates.append(bindings)
    if len(candidates) > 1:
        raise ValueError("stage-native wave split recovery is ambiguous")
    return candidates[0] if candidates else None


def _persist_stage_loop_wave_bindings(
        ws: str, state: Mapping[str, object], ready: list[dict],
        bindings: Mapping[str, str]) -> None:
    """Persist a verified split binding without accepting stale loop state."""
    task_ids = [str(task["id"]) for task in ready]
    with mutate(ws) as locked:
        if locked is None or locked.get("step") != "execute" or not \
                locked.get("parallel"):
            raise ValueError("stage-native wave advanced during split binding")
        locked_tasks = {str(task.get("id")): task
                        for task in (locked.get("tasks") or [])}
        existing_table = locked.get("_stage_bindings") or {}
        if any(
                task_id not in locked_tasks or
                locked_tasks[task_id].get("status") not in {"pending", "running"} or
                (locked_tasks[task_id].get("status") == "running" and not (
                    isinstance(existing_table, Mapping) and
                    isinstance(existing_table.get(task_id), Mapping) and
                    existing_table[task_id].get("build") == bindings[task_id]))
                for task_id in task_ids):
            raise ValueError("stage-native wave changed during split binding")
        current = _verified_stage_loop_wave_split(
            ws, ready, known_bindings=bindings)
        if current != dict(bindings):
            raise ValueError("stage-native wave split changed before binding")
        table = locked.setdefault("_stage_bindings", {})
        for task_id, child_id in bindings.items():
            existing = table.get(task_id)
            if isinstance(existing, Mapping) and existing.get("build") not in {
                    None, child_id}:
                raise ValueError("stage-native wave binding conflicts")
            table.setdefault(task_id, {})["build"] = child_id


def _stage_loop_wave_dispatches(
        ws: str, state: Mapping[str, object], ready: list[dict]) -> dict:
    """Bind each parallel entry to its own immutable stage/root."""
    if not ready:
        return {}
    table = state.get("_stage_bindings") or {}
    known = {
        str(task["id"]): str(binding["build"])
        for task in ready
        for binding in [table.get(str(task["id"]))
                        if isinstance(table, Mapping) else None]
        if isinstance(binding, Mapping) and binding.get("build")
    }
    if known and len(known) != len(ready):
        raise ValueError(
            "stage-native wave has partial persisted split bindings")
    recovered = _verified_stage_loop_wave_split(
        ws, ready, known_bindings=(known or None))
    if recovered is not None:
        _persist_stage_loop_wave_bindings(ws, state, ready, recovered)
        current = load(ws) or dict(state)
        claimed = {
            str(task.get("id")) for task in (current.get("tasks") or [])
            if isinstance(task, Mapping) and task.get("status") == "running"}
        return {
            str(task["id"]): _stage_loop_dispatch(
                ws, current, slot=str(task["id"]),
                declared_scope=_stage_loop_scope(
                    task.get("scope"), tp.DEFAULT_OUT_OF_SCOPE),
                stage_id=recovered[str(task["id"])])
            for task in ready if str(task["id"]) not in claimed
        }
    context = _stage_loop_context(ws, state)
    if context is None:
        return {}
    parent = context.get("stage")
    lifecycle = context.get("lifecycle")
    stage_entities = context.get("stage_entities")
    if not isinstance(parent, dict) or lifecycle is None or \
            stage_entities is None:
        raise ValueError("stage-native wave has no active build stage")
    if parent.get("stage_kind") != "build":
        raise ValueError("stage-native wave requires an active build stage")

    # A one-entry wave already owns the sole build root.  Record that exact
    # binding so later Evaluate dispatch cannot fall back to a different
    # foreground after another child becomes active.
    if len(ready) == 1:
        task_id = str(ready[0]["id"])
        with mutate(ws) as locked:
            locked.setdefault("_stage_bindings", {}).setdefault(
                task_id, {})["build"] = str(parent["stage_id"])
        return {task_id: _stage_loop_dispatch(
            ws, state, slot=task_id,
            declared_scope=_stage_loop_scope(
                ready[0].get("scope"), tp.DEFAULT_OUT_OF_SCOPE),
            stage_id=str(parent["stage_id"]))}

    try:
        if __package__:
            from . import review_evidence, stage_handoff
        else:
            import review_evidence
            import stage_handoff
    except ImportError:
        import review_evidence
        import stage_handoff
    artifact_store = lifecycle._artifact_store()  # noqa: SLF001
    task_ids = [str(task["id"]) for task in ready]
    operation_material = {
        "schema": "taskplane.loop-stage-wave-split/v1",
        "run_id": context["run_id"],
        "parent_stage_id": parent["stage_id"],
        "parent_fingerprint": parent["fingerprint"],
        "tasks": task_ids,
    }
    operation_id = _stage_loop_identity(
        stage_entities, "loop-wave-split-", operation_material)
    terminalized_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    authority = dict(parent["authority"])
    authorization_base = {
        "actor": authority["actor"],
        "session_id": authority["session_id"],
        "authorized_at": terminalized_at,
        "authority_record": {
            "schema": "taskplane.authority-record-reference/v1",
            "authority_schema": "taskplane.consolidated-authorization/v1",
            "revision": authority["authority_revision"],
            "fingerprint": authority["authority_fingerprint"],
        },
    }
    child_specs = []
    selected = list(parent.get("selected_artifacts") or [])
    for ordinal, task in enumerate(ready):
        task_id = str(task["id"])
        split_native = artifact_store.put("completion-evidence", {
            "schema": "taskplane.loop-wave-child-input/v1",
            "run_id": context["run_id"],
            "parent_stage_id": parent["stage_id"],
            "task_id": task_id,
            "scope": _stage_loop_scope(
                task.get("scope"), tp.DEFAULT_OUT_OF_SCOPE),
        })
        split_evidence = review_evidence.portable_artifact_reference(
            artifact_store, split_native)
        authorization = {
            **authorization_base,
            "operation_id": f"{operation_id}-child-{ordinal}",
        }
        handoff = stage_handoff.create_manifest(
            artifact_store,
            producer_stage_id=str(parent["stage_id"]),
            producer_outcome="closed", requirement=parent["requirement"],
            design=parent.get("design"), target=None, commit=None,
            contracts={"provided": list(parent.get("contracts") or []),
                       "consumed": [], "changed": []},
            deliverables=[task_id],
            evidence_references=[split_evidence],
            selected_artifacts=selected,
            exclusions=sorted(stage_handoff.REQUIRED_EXCLUSIONS),
            authorization=authorization, allow_nonconsumable_reuse=True)
        native_ref = stage_handoff.store_manifest(artifact_store, handoff)
        portable_ref = review_evidence.portable_artifact_reference(
            artifact_store, native_ref)
        child_specs.append({
            "stage_kind": "build",
            "input_manifest_ref": portable_ref,
            "selected_artifacts": selected,
            "dependencies": [],
            "budget": dict(parent.get("budget") or {}),
            "deliverables": [task_id],
            "contracts": list(parent.get("contracts") or []),
        })
    prospective = stage_entities.create_split(
        parent, operation_id=operation_id, child_specs=child_specs,
        actor=str(authority["actor"]), terminalized_at=terminalized_at,
        reason="bind independent execution roots for the parallel loop wave")
    for ordinal, child in enumerate(prospective["children"]):
        handoff = lifecycle._read_handoff(  # noqa: SLF001
            child["input_manifest_ref"], producer=prospective["parent"],
            consumer=child)
        _preflight_stage_dispatch(
            child, handoff,
            declared_scope=_stage_loop_scope(
                ready[ordinal].get("scope"), tp.DEFAULT_OUT_OF_SCOPE))
    split_receipt = lifecycle.split_stage(
        str(parent["run_id"]), stage_id=str(parent["stage_id"]),
        expected_head_fingerprint=str(parent["fingerprint"]),
        expected_revision=int(context["manifest"]["revision"]),
        operation_id=operation_id, child_specs=child_specs,
        actor=str(authority["actor"]), terminalized_at=terminalized_at,
        reason="bind independent execution roots for the parallel loop wave")
    tp.verify_stage_receipt(
        split_receipt, expected_operation="split_stage",
        expected_stage_id=str(parent["stage_id"]))
    bindings = _verified_stage_loop_wave_split(ws, ready)
    if bindings is None:
        raise ValueError("committed loop wave split is not recoverable")
    _persist_stage_loop_wave_bindings(ws, state, ready, bindings)
    dispatches = {}
    current = load(ws) or dict(state)
    for task in ready:
        task_id = str(task["id"])
        dispatches[task_id] = _stage_loop_dispatch(
            ws, current, slot=task_id,
            declared_scope=_stage_loop_scope(
                task.get("scope"), tp.DEFAULT_OUT_OF_SCOPE),
            stage_id=bindings[task_id])
    return dispatches


def wave(ws: str) -> dict:
    """The next parallel wave: every task whose dependencies have PASSED
    and whose scope is disjoint from the rest of the wave. Each entry ships
    its own contract + primed lenses + requirement — one governed agent per
    task, each in its own worktree. THE HARNESS IS PER AGENT: a worker's
    hook enforces its own task's contract in its own workspace."""
    if refusal := _stage_loop_mutation_refusal(ws):
        return refusal
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    if not state.get("parallel"):
        return {"error": "loop is serial — `loop init --parallel` to enable"}
    if state["step"] != "execute":
        return {"error": f"waves only at execute (current: {state['step']})"}
    try:
        delivery_receipt = _validated_delivery_mode(state)
    except delivery_policy.DeliveryPolicyError as exc:
        return {"error": "build delivery mode refused before dispatch: "
                + str(exc), "step": "execute", "parallel": True}
    if delivery_receipt is not None and delivery_receipt["mode"] != "build":
        return {"error": "build delivery mode refused before dispatch: "
                "execute dispatch requires build delivery mode",
                "step": "execute", "parallel": True}
    try:
        ledger = _ensure_dispatch_telemetry(ws)
        budget_projection = dispatch_telemetry.budget_projection(
            ledger, SystemClock())
    except dispatch_telemetry.DispatchTelemetryError as exc:
        return {"error": "dispatch telemetry refused before wave: " + str(exc),
                "step": "execute", "parallel": True}
    if not budget_projection["dispatch_allowed"]:
        tp.trace(ws, "loop_wave_budget_stop",
                 triggered=budget_projection["triggered"])
        return {
            "step": "human_scope_review", "parallel": True,
            "paused": True, "wave": [], "held": [],
            "budget": budget_projection,
            "instruction": "Binding delivery budget reached; obtain human "
                           "scope review before any new dispatch.",
        }
    state = load(ws) or state
    tasks = state.get("tasks") or []
    enforcement = ((state.get("enforcement") or {}).get("current"))
    passed = {t["id"] for t in tasks
              if t.get("status") in DEP_SATISFIED}
    try:
        ready, held, executable_topology = select_ready_tasks(
            tasks, passed=passed,
            repository_files=_declared_repository_test_files(ws, tasks),
            allow_isolated_variants=bool(state.get("ab")))
    except plan_topology.PlanTopologyError as exc:
        return {"error": "executable Plan topology refused dispatch: " + str(exc),
                "step": "execute", "parallel": True}
    # A/B variants deliberately overlap in isolated worktrees.  The approved
    # Plan topology remains the default; variant isolation is the one existing
    # runtime exception and never makes non-variant work parallel.
    if state.get("ab"):
        selected: list[dict] = []
        remaining: list[dict] = []
        for task in ready:
            clash = next((member for member in selected
                          if _scopes_overlap(task.get("scope"), member.get("scope"))
                          and not (task.get("variant") and member.get("variant")
                                   and task.get("variant") != member.get("variant"))),
                         None)
            if clash is None:
                selected.append(task)
            else:
                remaining.append({"task": task["id"],
                                  "reason": f"scope overlaps {clash['id']} — next wave",
                                  "shared_owner": "scope"})
        ready, held = selected, [*held, *remaining]

    wave_wait_policy = (
        event_wait_policy("execute-wave", len(ready)) if ready else None)
    wave_wait_invocation = (
        event_wait_invocation(
            wave_wait_policy, [str(task["id"]) for task in ready])
        if ready else None)
    # Validate the sealed zero-lens authorization for the entire ready set
    # before persisting even one native intent.  A severed member therefore
    # refuses the whole emitted set without leaving partial dispatch state.
    prepared_routing: dict[str, tuple[dict, dict | None]] = {}
    for task in ready:
        task_ws = task.get("workspace") or \
            runtime_storage.task_worktree_path(ws, task["id"])
        if not os.path.isdir(task_ws):
            task_ws = ws
        try:
            prepared_routing[str(task["id"])] = \
                build_dispatch_lens_routing(
                    state, task, workspace=task_ws)
        except delivery_policy.DeliveryPolicyError as exc:
            return {"error": "build delivery mode refused before dispatch: "
                    + str(exc), "step": "execute", "parallel": True}

    dispatches: dict[str, dict] = {}
    dispatch_intents: dict[str, dict] = {}
    for task in ready:
        task_id = str(task["id"])
        dispatch = tp.dispatch_fields(
            "step", "tp-executor", task_id,
            tp.step_tier("execute", task))
        dispatches[task_id] = dispatch
        intent = _native_dispatch_intent(
            ws, state, step="execute", task_id=task_id,
            dispatch=dispatch, wait_policy=wave_wait_policy,
            wave_id="execute-wave")
        if not str(intent.get("intent_id") or ""):
            return {"error": "native dispatch intent has no identity",
                    "step": "execute", "parallel": True}
        dispatch_intents[task_id] = intent

    entries = []
    for t in ready:
        dispatch = dispatches[str(t["id"])]
        prime, delivery_dispatch = prepared_routing[str(t["id"])]
        recalled = kb.retrieve(ws, files=t.get("scope") or [],
                               tags=[t["id"]], limit=3)
        rid = t.get("req") or state.get("requirement_id")
        rec = reqs.get_requirement(ws, rid) if rid else None
        is_variant = bool(state.get("ab") and t.get("variant"))
        entry = {**dispatch,
            "task": {"id": t["id"], "scope": t.get("scope"),
                     "tests": t.get("tests"), "deps": t.get("deps") or [],
                     "variant": t.get("variant")},
            "worktree": runtime_storage.task_worktree_reference(ws, t["id"]),
            "merge_on_pass": not is_variant,
            "lenses": prime["lenses"],
            **({"delivery_dispatch": delivery_dispatch}
               if delivery_dispatch is not None else {}),
            "language_references": (prime.get("context") or {}).get(
                "language_references") or [],
            "requirement": rec and {"id": rec["id"], "title": rec["title"],
                                    "acceptance": rec["acceptance"]},
            "design": _design_context(ws, state),
            "knowledge": kb.render_context(recalled),
            "runtime_evals": runtime_eval.guidance("execute"),
            **({"enforcement": enforcement} if enforcement else {}),
        }
        entry["wait_policy"] = dict(wave_wait_policy)
        entry["dispatch_intent"] = dispatch_intents[str(t["id"])]
        entries.append(entry)
    tp.trace(ws, "loop_wave", ready=[t["id"] for t in ready],
             held=[h["task"] for h in held],
             topology_fingerprint=executable_topology["fingerprint"])
    # Deadlock guard: nothing ready, nothing built to evaluate, yet tasks
    # are held — and none of them is held merely on a scope clash (which a
    # later wave clears). If every held task waits on a dep that can NEVER
    # pass (skipped/failed/absent) or on a cycle, the loop cannot self-
    # advance — surface it for the human instead of returning a silent
    # empty wave forever.
    built = any(t.get("status") == "built" for t in tasks)
    if not entries and not built and held:
        by_id = {t["id"]: t for t in tasks}
        stuck = []
        for h in held:
            t = by_id[h["task"]]
            unmet = set(t.get("deps") or []) - passed
            dead = [d for d in unmet
                    if d not in by_id
                    or by_id[d].get("status") in ("skipped", "failed")]
            waiting_on_scope = "scope overlaps" in h["reason"]
            if dead or (unmet and not waiting_on_scope
                        and not any(by_id.get(d, {}).get("status")
                                    in (None, "pending", "running", "built")
                                    for d in unmet)):
                stuck.append({"task": h["task"], "blocked_by": sorted(unmet),
                              "dead_deps": dead})
        if stuck:
            tp.trace(ws, "loop_deadlock", stuck=[s["task"] for s in stuck])
            return {
                "step": "execute", "parallel": True, "wave": [], "held": held,
                "deadlock": stuck,
                "error": "wave deadlock — held tasks depend on tasks that "
                         "can never pass (skipped/failed/missing or a "
                         "dependency cycle). Resolve with `loop resolve "
                         "skip|abort`, or fix plan/tasks.json deps.",
            }

    return {
        "step": "execute", "parallel": True,
        "wave": entries, "held": held,
        "wait_invocation": wave_wait_invocation,
        **({"enforcement": enforcement} if enforcement else {}),
        "runtime_evals": runtime_eval.guidance("execute"),
        "instruction": (
            "Dispatch ONE governed subagent per wave entry, concurrently. "
            "Per task: (1) `git worktree add <worktree> -b tp/<task>` from "
            "the approved baseline; (2) `tp.py loop claim <task> "
            "--agent-workspace <worktree>` — activates THAT task's contract "
            "in THAT worktree, so the hook confines the agent mechanically; "
            "(3) the subagent builds inside its worktree (TDD, primed "
            "lenses, acceptance criteria); (4) it COMMITS its work in the "
            "worktree (`git add -A && git commit`) and runs `tp.py loop "
            "submit pass|fail --task <id>`. The orchestrator alone runs "
            "the matching `loop gate`. When the wave empties, `loop next` "
            "evaluates each built task; on evaluate PASS merge its branch "
            "(`git merge tp/<task>`) and remove the worktree. "
            "EXCEPTION — entries with merge_on_pass=false are A/B variants: "
            "do NOT merge them; when all variants pass, the loop pauses at "
            "the SELECTION gate and the human picks what ships."),
    } if entries else {
        "step": "execute", "parallel": True, "wave": [], "held": held,
        **({"enforcement": enforcement} if enforcement else {}),
        "runtime_evals": runtime_eval.guidance("execute"),
        "instruction": "no dispatchable tasks — evaluate built tasks via "
                       "`loop next`, or resolve held dependencies.",
    }


def claim(ws: str, task_id: str, agent_ws: str) -> dict:
    """Activate `task_id`'s contract in the worker's own workspace
    (worktree). From here the worker's PreToolUse hook enforces this task's
    scope/tools/commands — the core invariant: every parallel agent runs
    under the harness, individually."""
    if refusal := _stage_loop_mutation_refusal(ws):
        return refusal
    # v2.3.0 (scalability): DoR preparation shells out to git in the worker's
    # worktree (slow) — prepare OUTSIDE the global lock, then commit the
    # claim under the lock with a status RE-CHECK.
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    if not state.get("parallel"):
        # A1 (R-0007): a direct claim on a serial loop forms a wave whose
        # submits deadlock (decision 0011) — fail closed BEFORE any
        # contract/DoR work, backstopping wave()'s existing refusal.
        tp.trace(ws, "loop_claim_blocked", task=task_id, reason="serial_mode")
        return {"error": "loop was initialized without --parallel — a wave "
                         "cannot claim; re-init with --parallel or run "
                         "serially via `loop next`"}
    t = next((x for x in state.get("tasks") or [] if x["id"] == task_id),
             None)
    if t is None:
        return {"error": f"no task {task_id}"}
    if t.get("status") not in ("pending", "running"):
        return {"error": f"task {task_id} is {t.get('status')} — "
                         "not claimable"}
    contract = tp.build_contract(
        f"EXECUTE: {t['id']}", scope=t.get("scope"),
        test_command=t.get("tests"), plan_minted=True, regression_gate=True,
        tools=["Read", "Grep", "Glob", "Bash", "Write", "Edit",
               "MultiEdit"])
    enforcement = ((state.get("enforcement") or {}).get("current"))
    if enforcement:
        contract["enforcement"] = enforcement
    agent_ws = os.path.realpath(os.path.abspath(agent_ws))
    locator_error = runtime_storage.worker_locator_error(ws, agent_ws, task_id)
    if locator_error: return {"error": locator_error, "task": task_id}
    dispatch = tp.dispatch_fields(
        "step", STEP_ROLE["execute"], str(task_id),
        tp.step_tier("execute", t))
    contract = tp.prepare_worker_contract(
        agent_ws, contract, stage="execute", task=str(task_id),
        task_name=dispatch["task_name"], role_marker=dispatch["role_marker"])
    contract = _bind_worker_submission(
        agent_ws, state, "execute", contract, t)
    snapshot = tp.git_head(agent_ws)
    dor_ready, blockers, warnings = tp.dor_check(
        contract, agent_ws, snapshot)
    if not dor_ready:
        tp.trace(ws, "loop_claim_blocked", task=task_id,
                 agent_workspace=agent_ws, dor_blockers=blockers)
        return {"error": "Definition of Ready failed — task was not "
                         "claimed", "task": task_id,
                "dor": {"ready": False, "blockers": blockers,
                        "warnings": warnings}}
    # Repeat claimability under the lock so two claimers cannot both win.
    with mutate(ws) as state:
        if state is None:
            return {"error": "no active loop"}
        t = next((x for x in state.get("tasks") or [] if x["id"] == task_id),
                 None)
        if t is None:
            return {"error": f"no task {task_id}"}
        if t.get("status") not in ("pending", "running"):
            return {"error": f"task {task_id} is {t.get('status')} — "
                             "not claimable"}
        tp.activate(
            agent_ws, contract, snapshot=snapshot,
            task_slot_override=contract["task_slot"])
        t["status"] = "running"
        t["workspace"] = agent_ws
    tp.trace(ws, "loop_claim", task=task_id, agent_workspace=agent_ws,
             dor_ready=dor_ready)
    return {"claimed": task_id, "workspace": agent_ws,
            "contract_bootstrap": {
                "schema": "taskplane.worker-contract-bootstrap/v1",
                "task_slot": contract["task_slot"],
                "worker_identity": dispatch["task_name"],
                "environment": {"TASKPLANE_TASK": contract["task_slot"]},
                "activation": "pending_subagent_start_binding",
                "control_plane_release": {
                    "command": "worker-release",
                    "signed_action": tp.encode_worker_release_action(
                        contract["worker_lifecycle"]["release_action"]),
                    "terminal_receipt_required": True,
                },
            },
            "contract": {"scope": contract["coding"]["scope_paths"],
                         "tests": contract["coding"]["dod"]["test_command"]},
            "dor": {"ready": dor_ready, "blockers": blockers,
                    "warnings": warnings},
            **({"enforcement": enforcement} if enforcement else {})}


# --------------------------------------------------------------- next / gate

def next_action(ws: str, rid: str | None = None) -> dict:
    """Advance to the current step's work: activate its contract and return
    what the driver should run. Human steps pause without activating."""
    if refusal := _stage_loop_mutation_refusal(
            ws, allow_new_run_bootstrap=True):
        return refusal
    state = load(ws)
    if state is None:
        return {"error": "no active loop — run `tp.py loop init` first"}
    # v2.3.0 wiring: attach a requirement BEFORE the design DoR evaluates —
    # the sanctioned mid-loop exit for a loop started without --req. The
    # validator (design_contract.design_attach_requirement) enforces the same
    # completeness the DoR demands; failure blocks, success persists.
    if rid:
        attach_errors: list = []
        with mutate(ws) as st:
            if st is None:
                return {"error": "no active loop — run `tp.py loop init` "
                                 "first"}
            attach_errors = _dc.design_attach_requirement(ws, st, rid)
        if attach_errors:
            return {"error": "requirement attach failed",
                    "blockers": attach_errors}
        state = load(ws)
    try:
        _stage_bootstrap_pristine_root(ws, state)
    except Exception as exc:
        return {"error": "stage-native root bootstrap failed closed: "
                f"{exc.__class__.__name__}: {exc}",
                "step": state.get("step")}
    state = load(ws)
    if state is None:
        return {"error": "no active loop — run `tp.py loop init` first"}
    step = state["step"]

    if step == "retro":
        return {
            "step": "retro", "paused": False, "action": "loop_retro",
            "runtime_evals": runtime_eval.guidance("retro"),
            "instruction": (
                "Run `tp loop retro` once. It seals the run's learning, "
                "refreshes the dependency graph, and moves the loop to done; "
                "no human approval is required."),
            "status": status(ws),
        }

    if step in HUMAN_STEPS:
        awaiting = {
            "design_approval": "human: review design/design.md and the "
                               "Design Contract, then `loop approve`",
            "plan_approval": ("human: review the consolidated requirement, "
                              "conditional design, plan, scope, validation, "
                              "recovery, and delivery packet, then `loop approve`"
                              if _consolidated_enabled() else
                              "human: review plan/plan.md, then `loop approve`"),
            "selection": "human: A/B gate — compare the variants (rendered "
                         "side by side, criteria + lenses + spend), then "
                         "`loop select <variant|task-id|hybrid>`",
            "signoff": "human: EM sign-off, then `loop approve`",
            "escalated": "human: `loop resolve retry|skip|abort` "
                         "(fix cycles exhausted)",
            "done": "loop complete",
            "failed": "loop aborted",
        }[step]
        out = {"step": step, "paused": True, "awaiting": awaiting,
               "status": status(ws)}
        if step == "selection":
            out["variants"] = [
                {"id": t["id"], "variant": t.get("variant"),
                 "status": t.get("status"), "scope": t.get("scope"),
                 "worktree": runtime_storage.task_worktree_reference(ws, t["id"])}
                for t in (state.get("tasks") or []) if t.get("variant")]
            out["instruction"] = (
                "Present BOTH variants for the human's pick: re-run each "
                "variant's tests (trust but verify), render both UIs side "
                "by side — live screenshots over mocks — with the criteria "
                "scoreboard, lens findings, and per-variant resource spend. "
                "Then WAIT; `loop select` only on their explicit choice.")
        if step == "signoff":
            # Run the MECHANICAL Definition-of-Done here so the human signs off
            # seeing both the EM's read-out AND the scope-diff/lint verdict.
            out["dod"] = _signoff_gate_dod(ws, state)
            # v2.3.0 wiring: accepted design drift and hand-declared edge
            # realizations are VISIBLE at sign-off, not dead-on-pass.
            notices = list(
                (state.get("signoff_evidence") or {}).get("notices") or [])
            if notices:
                out["notices"] = notices
        if step == "design_approval":
            design_errors = _design_dod_errors(ws, state)
            out["dod"] = {"passed": not design_errors,
                          "errors": design_errors,
                          "fingerprint": _design_evidence_fingerprint(ws)}
            # v2.3.0 wiring: self-attested lens evidence is surfaced AT the
            # human gate instead of being silently accepted.
            notices = _dc.design_approval_notices(ws)
            if notices:
                out["notices"] = notices
        out["runtime_evals"] = runtime_eval.guidance(step)
        return out

    # Parallel mode: EXECUTE is a wave (dispatch handled by `wave`/`claim`);
    # once workers report built, evaluate them one by one (read-only).
    # v2.3.0: the built→evaluate flip is a read-modify-write of the SHARED
    # loop.json while wave workers gate concurrently — apply it under
    # mutate() to a FRESH read (the same lost-update class H2 closed in
    # gate()), so a worker's just-gated status is never clobbered by saving
    # this function's earlier unlocked snapshot.
    if step == "execute" and state.get("parallel"):
        moved = False
        with mutate(ws) as fresh:
            if fresh is None:
                return {"error": "no active loop — run `tp.py loop init` "
                                 "first"}
            if fresh.get("step") != "execute" or not fresh.get("parallel"):
                moved = True                # advanced under us — re-dispatch
            else:
                built = [i for i, t in enumerate(fresh.get("tasks") or [])
                         if t.get("status") == "built"]
                if built:
                    fresh["current_task"] = built[0]
                    fresh["step"] = "evaluate"
            state = fresh
        if moved:
            return next_action(ws)
        step = state["step"]
        if step == "execute":
            return wave(ws)

    # Defence in depth: a per-task step must have a current task. If the loop
    # ever reaches execute/fix/evaluate with none (e.g. a plan that produced
    # no tasks), return a structured error instead of crashing in
    # _step_contract on task["id"].
    if step in ("execute", "fix", "evaluate") and _current_task(state) is None:
        return {"error": f"loop step '{step}' has no current task — the plan "
                         f"produced no tasks, so the loop should not be here. "
                         f"Re-run the plan step (`loop gate fail`, then "
                         f"re-plan) or start over with `loop init`.",
                "step": step, "status": status(ws)}

    # Per-task steps run in the task's own workspace when one was claimed.
    act_ws = ws
    is_parallel_evaluate = step == "evaluate" and bool(state.get("parallel"))
    if step in ("evaluate", "fix") and state.get("parallel"):
        current = _current_task(state) or {}
        if step == "evaluate":
            resolved_ws, workspace_error = _parallel_evaluate_workspace(
                ws, state, current)
            if workspace_error or resolved_ws is None:
                return {"error": workspace_error or
                        "parallel Evaluate task worktree is unresolved",
                        "step": step,
                        "status": status(ws)}
            act_ws = resolved_ws
        else:
            tws = current.get("workspace")
            act_ws = tws if tws and os.path.isdir(tws) else ws

    if step == "design" and not state.get("design_approved"):
        # H3 (v2.2.1): until the design is human-approved, the graph
        # baseline follows the CURRENT scan — capturing once from a stale
        # graph and then blocking on "rescan" left the stored fingerprint
        # permanently mismatched (the engine's own remedy deadlocked the
        # step). A pre-approval rescan re-baselines, with a trace.
        current_fp = (depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
        if state.get("design_graph_fingerprint") != current_fp:
            # v2.3.0: persist the rebaseline under the state lock on a fresh
            # read — a bare save() here could clobber a concurrent update.
            with mutate(ws) as fresh:
                if fresh is not None and fresh.get("step") == "design" \
                        and fresh.get("design_graph_fingerprint") != current_fp:
                    if fresh.get("design_graph_fingerprint"):
                        tp.trace(
                            ws, "design_rebaseline",
                            old=(fresh["design_graph_fingerprint"] or "")[:12],
                            new=(current_fp or "")[:12])
                    fresh["design_graph_fingerprint"] = current_fp
                if fresh is not None:
                    state = fresh

    capability_vars = {
        "TASKPLANE_MODEL_SELECTION", "TASKPLANE_EFFORT_SELECTION",
        "TASKPLANE_SUPPORTED_MODEL_ALIASES",
        "TASKPLANE_SUPPORTED_EFFORT_VALUES",
        "TASKPLANE_NATIVE_STRUCTURED_OUTPUT",
    }
    capability_snapshot = (
        host_capabilities.dispatch_snapshot_from_environment(
            ws, host=tp.host(), environment=os.environ)
        if step == "evaluate" or any(name in os.environ
                                     for name in capability_vars) else None)

    worker_task = _current_task(state)
    worker_ref = str((worker_task or {}).get("id") or step)
    dispatch = tp.dispatch_fields(
        "step", STEP_ROLE[step], worker_ref,
        tp.step_tier(step, worker_task),
        capability_snapshot=capability_snapshot,
        enforcement_mode=os.environ.get("TASKPLANE_ENFORCE_DISPATCH"))
    if dispatch.get("dispatch_blocked"):
        tp.trace(ws, "dispatch_route_resolved", step=step,
                 task=(worker_task or {}).get("id"), resolution="blocked",
                 reason=dispatch["dispatch_route"].get("reason"))
        return {"error": "strict host dispatch route cannot be honored — "
                         + dispatch["dispatch_route"].get("reason", ""),
                "step": step, **dispatch}

    contract = _step_contract(step, state, act_ws)
    enforcement = ((state.get("enforcement") or {}).get("current"))
    if enforcement:
        contract["enforcement"] = enforcement
    contract = tp.prepare_worker_contract(
        act_ws, contract, stage=step, task=worker_ref,
        task_name=dispatch["task_name"], role_marker=dispatch["role_marker"])
    evaluator_contract = None
    if step == "evaluate":
        evaluator_contract = evaluation_output.create_evaluator_contract(
            workspace=act_ws, task=str(_current_task(state)["id"]),
            slot=contract["task_slot"],
            capability_snapshot=capability_snapshot)
        contract = dict(contract)
        contract["output_contract"] = evaluator_contract
    contract = _bind_worker_submission(
        act_ws, state, step, contract, _current_task(state))
    snapshot = tp.git_head(act_ws)
    # Readiness is evaluated against the complete child contract in memory.
    # The slot is activated only after every control-plane precondition has
    # succeeded, immediately before the native dispatch is returned.  This
    # keeps a failed preflight from binding worker authority to the root
    # checkout while still ensuring the child cannot start before its slot.
    dor_ready, blockers, warnings = tp.dor_check(
        contract, act_ws, snapshot)
    if step == "design":
        design_dor = _design_dor(ws, state)
        blockers.extend(design_dor["blockers"])
        warnings.extend(design_dor["warnings"])
        dor_ready = not blockers
    tp.trace(ws, "loop_step", step=step, role=STEP_ROLE[step],
             task=(_current_task(state) or {}).get("id"),
             dor_ready=dor_ready, dor_blockers=blockers,
             dor_warnings=warnings)
    if not dor_ready:
        return {"error": "Definition of Ready failed — resolve blockers "
                         "before this step can start",
                "step": step, "role": STEP_ROLE[step],
                "dor": {"ready": False, "blockers": blockers,
                        "warnings": warnings},
                "status": status(ws)}

    # The graph is an input to evaluation, not a cache refreshed only after
    # review. A parallel evaluator scans its isolated task worktree rather
    # than publishing a partial worker graph into the shared checkout. This
    # must finish before impact, quality, routing, leases, or activation.
    if step == "em":
        # Make the final graph describe the merged, as-built system BEFORE
        # the engineering reviewer receives it.  Doing this at the EM gate
        # would invalidate the review's graph fingerprint at sign-off.
        try:
            _true_up_graph(ws, state)
        except Exception as exc:
            if state.get("graph_governance"):
                return {"error": f"graph true-up failed before {step}: {exc}",
                        "step": step, "status": status(ws)}
            tp.trace(ws, "graph_refresh_failed", step=step, error=str(exc))
    elif step == "evaluate":
        graph_refresh_ws = act_ws if is_parallel_evaluate else ws
        try:
            depgraph.scan(graph_refresh_ws)
        except Exception as exc:
            return {"error": f"graph refresh failed before {step}: {exc}",
                    "step": step, "status": status(ws),
                    "review_kernel": {"status": "impact_incomplete",
                                      "slots": []}}
    # Inject the handful of prior decisions relevant to this step's work, so
    # the role starts with context instead of re-deriving it (token savings).
    task = _current_task(state)
    # Evaluate uses only the canonical workspace returned by the resolver.
    # Re-reading task["workspace"] here would create a second path authority
    # after validation and reopen alias/retarget races for graph evidence.
    if is_parallel_evaluate or (
            step == "fix" and state.get("parallel")):
        wtree = act_ws
    else:
        candidate_ws = (task or {}).get("workspace") or ""
        wtree = candidate_ws if os.path.isdir(candidate_ws) else ws
    graph_ws = act_ws if is_parallel_evaluate else ws
    query_files = (task or {}).get("scope") or []
    query_tags = ([task["id"]] if task else []) + [state["goal"][:24]]
    recalled = kb.retrieve(ws, files=query_files, tags=query_tags, limit=5)
    if recalled:
        tp.trace(ws, "kb_recall", step=step,
                 decisions=[d["id"] for d in recalled])

    # Lens wiring. EXECUTE/FIX: PRIME — the same lenses that will review the
    # change are named before it's built. EVALUATE/EM: ROUTE on the real diff
    # since plan approval, so review effort lands exactly where change did.
    # Normal Evaluate and final EM consume the same selective mapper. The
    # explicit human/calibration `tp lens route --all` surface remains, but
    # delivery never substitutes full-catalog fan-out for uncertainty.
    routing, breadth = None, "routed"
    delivery_dispatch = None
    if step in ("pm", "plan"):
        # Advisory tier: C-level lenses run at STRATEGY level, always-on at
        # the pm/plan steps — never on code.
        routing = lens_router.route([], artifact_type="strategy", catalog=None)
    elif step == "design":
        # The design lens is mandatory at this phase, independent of diff
        # routing. Keep a fallback brief so an in-place minor update remains
        # resumable while the catalog file itself is being upgraded.
        design_req = reqs.get_requirement(ws, state.get("requirement_id"))
        design_scope = (design_req or {}).get("context_files") or []
        design_files = list(design_scope) + \
            lens_router.workspace_language_markers(ws, design_scope)
        routed = lens_router.route(
            design_files,
            task_type="solution-design", only=["solution-design"])
        routing = routed if routed.get("lenses") else {"lenses": [{
            "id": "solution-design", "name": "Solution design",
            "mode": "inline", "tier": "deep",
            "reasons": ["mandatory Design Contract lens"], "checks": [],
            "looks_for": "approach coherence, dependency boundaries, "
                         "trade-offs, failure modes, and verifiable delivery"
        }]}
    elif step in ("execute", "fix"):
        try:
            routing, delivery_dispatch = build_dispatch_lens_routing(
                state, task, workspace=wtree)
        except delivery_policy.DeliveryPolicyError as exc:
            return {"error": "build delivery mode refused before dispatch: "
                    + str(exc), "step": step, "status": status(ws)}
    elif step in ("evaluate", "em"):
        # Deferred until graph quality and complete impact exist below.
        # Mapping before that evidence is the ordering defect R-0005 closes.
        routing = None
    if routing:
        # RECORDING ONLY. Reading the breadth back off the lens LIST cannot
        # work — see lens.LENS_BREADTH_EVENT. `breadth` is the ask, verbatim.
        tp.trace(ws, "lens_route", step=step, requested_breadth=breadth,
                 engine_ran="signals" in (routing.get("context") or {}),
                 lenses=[[x["id"], x["mode"]] for x in routing["lenses"]])

    def heads():                    # lazy: only an emitting branch pays
        # The row must name the same canonical tree that supplied the graph
        # and impact.  A serial loop can retain an old task workspace after
        # a claim or resume, but Evaluate deliberately reviews the project
        # checkout in that mode.  Naming that stale worker tree would make
        # the trace describe bytes that were never scanned.
        return {"head": tp.git_head(graph_ws),
                "scanned_head": (depgraph.load(graph_ws).get("meta")
                                 or {}).get("scanned_head")}
    # Blast radius from the persistent dependency graph — the reviewer sees
    # what the change can break WITHOUT re-deriving dependencies (no tokens).
    imp = None
    if step in ("evaluate", "em"):
        diff_ws = act_ws if step == "evaluate" else ws
        changed = [f for f in _diff_files(
            diff_ws, state.get("baseline") or "HEAD")
            if not f.startswith(lens_router.LOOP_OWNED)]
        if changed or step == "em":
            review_policy = (_aggregate_impact_policy(state.get("tasks") or [])
                             if step == "em" else
                             depgraph.impact_policy(task or {}))
            imp = depgraph.impact(graph_ws, changed, policy=review_policy)
            # Product side of the blast radius: which OTHER requirements'
            # surface this diff touches (their criteria may need re-checking)
            # and which requirements depend on the affected ones.
            prod = depgraph.product_impact(graph_ws, changed)
            own = (task or {}).get("req") or state.get("requirement_id")
            own = depgraph.req_node(own) if own else None
            imp["affected_requirements"] = [
                r for r in prod["affected_requirements"] if r != own]
            imp["dependent_requirements"] = prod["dependent_requirements"]
            nudges = _edge_nudges(diff_ws, changed,
                                  state.get("baseline") or "HEAD")
            if nudges:
                imp["edge_suggestions"] = nudges
            tp.trace(ws, "graph_impact", step=step,
                     touched=imp["touched"],
                     impacted=imp["total_impacted"],
                     affected_reqs=imp["affected_requirements"], **heads())
    elif step in ("execute", "fix") and task:
        # v2.0.0: the BUILDER sees the blast radius BEFORE changing code
        # (previously only the judges at evaluate/em did) - side effects
        # get prevented, not just detected a loop-step later.
        scope = task.get("scope") or []
        if scope and depgraph.load(ws)["modules"]:
            mods = depgraph.scope_modules(ws, scope)
            if mods:
                imp = depgraph.impact(
                    ws, mods, policy=depgraph.impact_policy(task))
                if not imp["touched"]:
                    imp = None
                else:
                    tp.trace(ws, "graph_impact", step=step,
                             touched=imp["touched"],
                             impacted=imp["total_impacted"], **heads())
    elif step == "design":
        design_req = reqs.get_requirement(ws, state.get("requirement_id"))
        design_scope = (design_req or {}).get("context_files") or []
        design_modules = depgraph.scope_modules(ws, design_scope)
        if design_modules and depgraph.load(ws).get("modules"):
            design_policy = {"local_depth": 3,
                             "boundary_mode": "contract-only",
                             "contract_depth": 1, "requirement_depth": 1}
            imp = depgraph.impact(ws, design_modules, policy=design_policy)
            tp.trace(ws, "graph_impact", step=step,
                     touched=imp["touched"],
                     impacted=imp["total_impacted"], **heads())

    # Audit cadence (v3 Phase 1): the em brief advertises audit mode — due
    # every Nth completed review (default 5) or on a release flag — and,
    # when due, carries the recorded routing decision so the gate can check
    # findings against it (router-regression auto-filing).
    audit_info = None
    if step == "em":
        audit_info = _audit_brief(ws, state)
        if audit_info.get("due"):
            tp.trace(ws, "audit_due", reason=audit_info.get("reason"),
                     reviews_completed=audit_info.get("reviews_completed"))

    # Requirement anchoring: this task's R-id (or the loop's) is the spine —
    # its acceptance criteria are the DoD the evaluator holds the work to.
    req_rec = None
    rid = (task or {}).get("req") or state.get("requirement_id")
    if rid:
        req_rec = reqs.get_requirement(ws, rid)

    review_kernel = None
    review_workspace = None
    if step in ("evaluate", "em"):
        diff_ws = act_ws if is_parallel_evaluate else ws
        review_workspace = diff_ws
        base_ref = state.get("baseline") or "HEAD"
        try:
            review_delivery_authority = _DELIVERY_MODE_AUTHORITY_UNSET
            if step in {"evaluate", "em"}:
                delivery_receipt = _validated_delivery_mode(state)
                if step == "evaluate" and delivery_receipt is None and str(
                        state.get("design_fingerprint") or "").strip():
                    raise delivery_policy.DeliveryPolicyError(
                        "delivery-mode receipt is required for a "
                        "Design-governed Evaluate")
                if delivery_receipt is not None:
                    review_delivery_authority = delivery_receipt
            retry_context = (review_retry.incremental_context(
                ws, diff_ws, task, review_kernel_binding(state, "evaluate", task))
                if step == "evaluate" else None)
            review_kernel, routing = _review_kernel(
                ws, diff_ws, base=base_ref, step=step, task=task,
                graph=depgraph.load(graph_ws), impact=imp or {},
                requirement=req_rec, retry_context=retry_context,
                delivery_mode_receipt=review_delivery_authority)
            if step == "em" and review_delivery_authority is not \
                    _DELIVERY_MODE_AUTHORITY_UNSET and \
                    review_kernel.get("status") == "ready" and \
                    review_kernel.get("slots") == []:
                # EM has no automatic lens producers under the sealed Build
                # authority. Complete that engine-owned empty set now so the
                # engineering producer can synthesize the final report from
                # a canonical revision; its own exact report/findings bytes
                # are still host-observed and consumed at submit time.
                collected = collect_review_bridge(
                    diff_ws, publish=False, run_id=review_kernel["run_id"],
                    evaluator_result={
                        "delivery_mode_receipt":
                            review_delivery_authority["fingerprint"]},
                    producer_observation_fingerprint=
                        review_delivery_authority["fingerprint"],
                    collection_stage="EM", result_validator=lambda value: value)
                review_kernel = {**review_kernel,
                                 "status": collected.get("status"),
                                 "empty_lens_collection": collected.get(
                                     "empty_lens_collection")}
            review_kernel = _bind_stateless_review_contract_actions(
                diff_ws, review_kernel,
                task_id=str((task or {}).get("id") or
                            "engineering-signoff"))
        except _ReviewGraphQualityError as exc:
            tp.trace(ws, "review_graph_quality_blocked", step=step,
                     task=(task or {}).get("id"),
                     reasons=exc.quality.get("reasons") or [], slots=[])
            return {
                "error": "graph quality failed before selective review: "
                         + str(exc),
                "step": step, "status": status(ws),
                "graph_quality": {
                    "status": exc.quality.get("status"),
                    "reasons": list(exc.quality.get("reasons") or []),
                    "artifact": exc.reference,
                },
                "review_kernel": {"status": "impact_incomplete",
                                  "slots": []},
            }
        except Exception as exc:
            review_kernel = {"status": "kernel_unavailable", "slots": [],
                             "reason": f"{exc.__class__.__name__}: {exc}"}
            routing = {"lenses": [], "context": {
                "status": "kernel_unavailable", "breadth": "routed"}}
        binding = {
            "schema": "taskplane.review-kernel-binding/v1",
            "run_id": review_kernel.get("run_id"),
            "workspace": review_workspace,
            "stage": (EVALUATE_ROUTE_STAGE if step == "evaluate" else
                      "review"),
            "status": review_kernel.get("status"),
        }
        with mutate(ws) as fresh:
            if fresh is not None:
                fresh.setdefault("review_kernel_runs", {})[
                    _review_kernel_binding_key(step, task)] = binding
        tp.trace(ws, "lens_route", step=step, requested_breadth="routed",
                 engine_ran="signals" in (routing.get("context") or {}),
                 lenses=[[x["id"], x["mode"]]
                         for x in routing.get("lenses") or []],
                 kernel_status=review_kernel.get("status"))

    model_tier, model = dispatch["model_tier"], dispatch["model"]
    reasoning_effort, task_name = (dispatch["reasoning_effort"],
                                   dispatch["task_name"])
    if step in {"evaluate", "em"} and \
            _validated_delivery_mode(state) is not None:
        run_id = str((review_kernel or {}).get("run_id") or "").strip()
        task_id = str((task or {}).get("id") or "engineering-signoff")
        if not run_id:
            raise producer_observation_policy.ProducerObservationError(
                f"{step} producer dispatch lacks ReviewKernel identity")
        dispatch_projection = {
            "run_id": run_id,
            "task_id": task_id,
            "stage": step,
            "producer": STEP_ROLE[step],
            "task_name": task_name,
            "role_marker": dispatch["role_marker"],
            "model": model,
            "reasoning_effort": reasoning_effort,
        }
        contract["producer_dispatch"] = {
            **dispatch_projection,
            "fingerprint": producer_observation_policy.content_fingerprint(
                dispatch_projection),
        }
    tp.trace(ws, "model_tier", step=step,
             task=(task or {}).get("id"), tier=model_tier, model=model,
             reasoning_effort=reasoning_effort)
    tp.record_expected_dispatch(ws, "step", STEP_ROLE[step], model_tier,
                                model, ref=(task or {}).get("id") or step,
                                task_name=task_name,
                                reasoning_effort=reasoning_effort,
                                role_marker_value=dispatch["role_marker"],
                                dispatch_route=dispatch.get("dispatch_route"))
    if dispatch.get("dispatch_route"):
        tp.trace(ws, "dispatch_route_resolved", step=step,
                 task=(task or {}).get("id"),
                 resolution=dispatch["dispatch_route"].get("resolution"),
                 capability_source=dispatch["dispatch_route"].get(
                     "capability_source"),
                 exact_route_verified=False)
    model_note = None
    if model is None and (model_tier or "standard") != "standard":
        model_note = (f"tier '{model_tier}' resolves to inherit on this "
                      f"host — the planned routing has no effect; set "
                      f"TASKPLANE_MODEL_{str(model_tier).upper()} to "
                      "activate it")
    dispatch_wait_policy = event_wait_policy(
        f"{step}:{(task or {}).get('id') or step}", 1)
    result = {**dispatch,
        **({"model_note": model_note} if model_note else {}),
        **({
            "output_contract": evaluator_contract,
            "output_schema": evaluator_contract["output_schema"],
            "resume_identity": evaluation_output.resume_identity(
                evaluator_contract),
            "max_attempts": evaluator_contract["max_attempts"],
        } if evaluator_contract else {}),
        "step": step,
        "codex_dispatch": ("Use Codex's native subagent task orchestration with "
                           "this exact task_name, role instructions, standalone "
                           "role_marker, model when non-null, and "
                           "reasoning_effort."),
        "wait_policy": dispatch_wait_policy,
        **({"delivery_dispatch": delivery_dispatch}
           if delivery_dispatch is not None else {}),
        # cross-host artifact: '/'-shaped out, host-shaped in state
        "task": tp.posix_workspace(task),
        "contract": {"read_only": bool(contract.get("read_only")),
                     "scope": contract["coding"]["scope_paths"],
                     "write_allow": contract.get("write_allow"),
                     "tests": contract["coding"]["dod"]["test_command"]},
        "dor": {"ready": dor_ready, "blockers": blockers,
                "warnings": warnings},
        "knowledge": {"decisions": recalled,
                      # R-0002: accepted decisions whose modules overlap this
                      # task's scope are ALWAYS in force — injected
                      # unconditionally, not relevance-ranked.
                      "governing_decisions": kb.governing(
                          ws, contract["coding"]["scope_paths"]),
                      # R-0004: the as-built inventory — ALWAYS in the brief
                      # when filled, so design work is judged as a delta
                      # against what exists, never in a vacuum.
                      "current_state": kb.current_state(ws),
                      "context": kb.render_context(recalled)},
        "lenses": routing["lenses"] if routing else None,
        "language_references": ((routing.get("context") or {}).get(
            "language_references") if routing else None),
        "review_kernel": review_kernel,
        "runtime_evals": runtime_eval.guidance(step),
        "audit": audit_info,
        "impact": imp and {**imp, "context": depgraph.render_context(imp)},
        "design": _design_context(ws, state),
        "design_graph": ({
            "baseline_fingerprint": state.get("design_graph_fingerprint"),
            "summary": depgraph.summary(ws),
            "policy": depgraph.impact_policy({}),
            "rule": "propose modules and edges in design/contract.json; "
                    "do not mutate the as-built graph during Design"
        } if step == "design" else None),
        "requirement": req_rec and {
            "id": req_rec["id"], "title": req_rec["title"],
            "acceptance": req_rec["acceptance"],
            "open_questions": req_rec["open_questions"],
            # Structured requirement data is authoritative.  The rendered
            # context includes relations such as
            # ``changes:contract:pricing.checkout.total`` for humans; a
            # planner copying that entire display string as a contract id
            # produces an invalid ``changes:contract:...`` plan boundary.
            # Give both hosts the exact id and relation separately so the
            # plan can copy ``contract:...`` without rediscovery or retry.
            "contracts": [
                {"relation": row.get("relation"), "id": row.get("id")}
                for row in (req_rec.get("contracts") or [])
                if isinstance(row, dict) and row.get("id")
            ],
            "depends": list(req_rec.get("depends_on") or []),
            "context_files": list(req_rec.get("context_files") or []),
            "context": reqs.render_context([req_rec])},
        "instruction": _instruction(step, state, act_ws),
    }
    # Native Build/Fix/Evaluate delivery is described by the exact intent
    # below.  StageLifecycle remains the genuine governance boundary for
    # non-delivery stages, but it must not project a second per-agent tree or
    # execution-root authority onto Codex-native workers.
    if step not in {"execute", "evaluate", "fix"}:
        try:
            stage_dispatch = _stage_loop_dispatch(
                ws, state, slot=str((task or {}).get("id") or step),
                declared_scope=_stage_loop_scope(
                    contract["coding"]["scope_paths"],
                    contract["coding"].get("out_of_scope_paths") or []))
        except Exception as exc:
            return {"error": "stage-native loop dispatch failed closed: "
                    f"{exc.__class__.__name__}: {exc}",
                    "step": step, "status": status(ws)}
        if stage_dispatch is not None:
            result["stage_runtime_dispatch"] = stage_dispatch
    if step in {"execute", "evaluate", "fix"}:
        dispatch_member = str((task or {}).get("id") or step)
        result["dispatch_intent"] = _native_dispatch_intent(
            ws, state, step=step, task_id=dispatch_member,
            dispatch=dispatch, wait_policy=dispatch_wait_policy)
        result["wait_invocation"] = event_wait_invocation(
            dispatch_wait_policy, [dispatch_member])
    lifecycle = contract["worker_lifecycle"]
    release_action = tp.encode_worker_release_action(
        lifecycle["release_action"])
    result["contract_bootstrap"] = {
        "schema": "taskplane.worker-contract-bootstrap/v1",
        "task_slot": contract["task_slot"],
        "worker_identity": task_name,
        "environment": {"TASKPLANE_TASK": contract["task_slot"]},
        "activation": "pending_subagent_start_binding",
        "control_plane_release": {
            "command": "worker-release",
            "signed_action": release_action,
            "terminal_receipt_required": True,
        },
    }
    try:
        tp.activate(
            act_ws, contract, snapshot=snapshot,
            task_slot_override=contract["task_slot"])
    except Exception as exc:
        return {"error": "worker contract activation failed closed: "
                         f"{exc.__class__.__name__}: {exc}",
                "step": step, "status": status(ws)}
    return result


guide = runtime_eval.guide_loop


def event_wait_policy(outstanding_set: str, outstanding_count: int) -> dict:
    """Return the single long-lived event wait for a dispatched set."""
    if not str(outstanding_set or "").strip():
        raise ValueError("outstanding_set is required")
    if int(outstanding_count) < 1:
        raise ValueError("outstanding_count must be positive")
    return {
        "schema": "taskplane.wait-policy/v1",
        "outstanding_set": str(outstanding_set),
        "outstanding_count": int(outstanding_count),
        "mode": "event",
        "timeout_seconds": 1800,
        "minimum_timeout_seconds": 300,
        "reissue_after": ["completion", "attention"],
        "scheduled_polling": False,
    }


def event_wait_invocation(policy: Mapping[str, object],
                          outstanding_members: list[str], *,
                          wake: str | None = None) -> dict:
    """Emit one live event wait, or its wake-authorized reissue.

    A host may issue the first invocation immediately. A later invocation is
    a reissue and must carry the completion/attention event that woke the
    prior wait; timeouts and scheduled polling never authorize one.
    """
    value = dict(policy) if isinstance(policy, Mapping) else {}
    members = list(outstanding_members)
    if (value.get("schema") != "taskplane.wait-policy/v1" or
            value.get("mode") != "event" or
            value.get("scheduled_polling") is not False or
            int(value.get("timeout_seconds") or 0) < 1800 or
            value.get("reissue_after") != ["completion", "attention"]):
        raise ValueError("event wait policy is invalid")
    if (not members or any(not isinstance(member, str) or not member.strip()
                           for member in members) or
            len(set(members)) != len(members) or
            int(value.get("outstanding_count") or 0) != len(members)):
        raise ValueError("event wait outstanding set is invalid")
    if wake is not None and wake not in value["reissue_after"]:
        raise ValueError(
            "event wait reissue requires a completion or attention wake")
    return {
        "schema": "taskplane.event-wait-invocation/v1",
        "operation": "wait_for_events",
        "outstanding_set": value["outstanding_set"],
        "outstanding_members": members,
        "timeout_seconds": int(value["timeout_seconds"]),
        "scheduled": False,
        "reissue": wake is not None,
        "wake": wake,
    }


# BUILD-C consumes these incumbent services through dependency inversion.
# Keeping the binding here prevents the BUILD-C phase module from entering
# the loop/review import SCC while retaining fail-closed state and wait rules.
build_c.bind_loop_runtime(
    state_loader=load,
    wait_policy_factory=event_wait_policy,
    wait_invocation_factory=event_wait_invocation,
)


def _instruction(step: str, state: dict, ws: str | None = None) -> str:
    t = _current_task(state)
    evaluator_result, review_root = runtime_storage.instruction_artifact_paths(ws)
    return {
        "pm": "Run tp-product: author specs/spec.md, then call `req new` "
              "exactly once with complete functional, acceptance, "
              "context-file, contract, and NFR fields. Code-bearing scope "
              "must include exact `security` and `architecture` NFR ids. Do "
              "not call status, context, graph, graph impact, req score, req "
              "list/help, loop submit, new, or clear: the PM gate "
              "mechanically scores DoR and links context files to the "
              "planned graph. Return the R-id to the orchestrator.",
        "design": "Run tp-designer (read-only toward product code): inspect "
                  "the requirement, current state, decisions, and baseline "
                  "graph; author design/design.md and design/contract.json "
                  "using schema taskplane.design/v1. Compare alternatives, "
                  "select the HOW, define modules/contracts plus graph DoR/DoD "
                  "and depth policy, map acceptance, handle risks/rollout, "
                  "apply solution-design, and create a visualization only "
                  "when it materially clarifies the choice. Never mutate the "
                  "as-built graph. Return to the orchestrator; it validates "
                  "with `loop gate pass` and then pauses for human approval.",
        "plan": "Run the tp-planner role: derive impact once with `tp graph "
                "impact --files \"comma,separated,paths\" --json`; write "
                "plan/tasks.json (machine) "
                "and plan/plan.md (human) — tasks with scope, tests (ONE "
                "command string, never a list), "
                "criteria, dependencies, contracts, design_edges, and impact "
                "policy. Copy only `requirement.contracts[].id` into task "
                "contracts; the relation is metadata, not part of the id. When "
                "`design.approved` is true, cover its modules, edges, contracts, "
                "depth policy, and acceptance mapping without drift. Return "
                "to the orchestrator; it validates with `loop gate pass`.",
        "execute": f"Run the tp-executor on task {t and t['id']}: build "
                   "under this contract (TDD), honoring the PRIMED lenses "
                   "(see `lenses`) and the requirement's acceptance criteria "
                   "(see `requirement`) plus the approved Design Contract "
                   "when `design.approved` is true. Then `loop submit pass` (or `fail` "
                   "if you couldn't build it); only the orchestrator calls "
                   "`loop gate`.",
        "evaluate": f"Run the tp-evaluator (read-only) on task "
                    f"{t and t['id']}: START with `tp loop evidence --write` — "
                    "one call returns the suite result, the diff, and the exact "
                    "criteria, routed-lens and graph obligations this gate "
                    "demands, judgment slots empty; do NOT rebuild those by "
                    "hand. Then do what the engine cannot: prove each criterion "
                    "against real behavior, apply each ROUTED lens (prompt at "
                    "lenses/<id>.md) — inline ones yourself, one governed "
                    "read-only subagent per subagent-mode lens. Pass each "
                    "slot's contract_bootstrap unchanged: the orchestrator "
                    "must activate its signed contract before spawning that "
                    "worker and inject contract_bootstrap.environment into "
                    "the native child lifecycle. The exact worker start is "
                    "then host-observed and lease-bound before evidence "
                    "access. "
                    "Then disposition "
                    "graph impact + affected requirements; reject stale Design "
                    f"evidence. Fill the empty slots in {evaluator_result} "
                    "(submitted unchanged, it is refused). Then `loop submit "
                    "pass|fail`; if one bounded model/host attempt is unavailable "
                    "but bound tests are green and no product/lens defect exists, "
                    "record structured evaluation unavailability and `loop submit "
                    "unavailable` instead — it warns without opening FIX. Only "
                    "the orchestrator calls `loop gate`.",
        "fix": f"Run the tp-fixer on task {t and t['id']}: repair the "
               "listed failures + add a regression test. Then `loop submit "
               "pass`; only the orchestrator calls `loop gate`.",
        "em": "Run tp-engineering (read-only): run every emitted "
              "`review_kernel.slots` entry concurrently in one sweep set, "
              "using each slot's exact brief, lease, contract_bootstrap, and "
              "result_path, then issue exactly `review_kernel.wait_invocation` "
              "once. Refuse selector re-entry, serial fallback, and any "
              "deep/light/full/26-lens dispatch. "
              "Before each spawn, activate that slot's signed action and "
              "inject contract_bootstrap.environment into the native child "
              "lifecycle, so its SubagentStart is lease-bound before "
              "evidence access. Collect only through "
              "review_kernel.collection after the single event wait. Do "
              "not re-derive diff or impact per lens. "
              "Synthesize all verdicts + requirement-vs-implementation into "
              f"{os.path.join(review_root, 'report.md')} AND "
              f"{os.path.join(review_root, 'findings.json')} (including "
              "complete meta.lens_coverage, meta.impact, meta.design "
              "conformance when an approved design exists, tests, and gate "
              "verdict), record the verdict to the knowledge "
              "base, then `loop submit pass`. The orchestrator validates "
              "with `loop gate pass` before presenting human sign-off.",
    }[step]


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
_design_current_errors = _dc.design_current_errors
_design_dor = _dc.design_dor
_design_dod_errors = _dc.design_dod_errors
_design_plan_errors = _dc.design_plan_errors
_design_review_errors = _dc.design_review_errors


def _retained_production_authority_errors(ws: str) -> list[str]:
    """Enforce immutable R-0013 authority on descendant Taskplane checkouts."""
    try:
        if __package__:
            from . import native_authority
        else:  # pragma: no cover - direct installed CLI
            import native_authority
        if not native_authority.retained_r0013_authority_applies(ws):
            return []
        receipt = native_authority.validate_retained_r0013_authority(ws)
        if receipt.get("schema") != \
                native_authority.PRODUCTION_DESIGN_GATE_SCHEMA or \
                receipt.get("status") != "ready":
            return ["retained R-0013 production authority is unavailable"]
        return []
    except Exception as exc:
        return [f"retained R-0013 production authority: {exc}"]


def _design_context(ws: str, state: dict) -> dict | None:
    if not state.get("design_required"):
        return None
    contract, errors = _design_contract(ws)
    approved = bool(state.get("design_fingerprint"))
    stale = _design_current_errors(ws, state) if approved else []
    if stale:
        # M8 (v2.2.1): an approved design whose artifacts changed after
        # approval is NOT served as approved — the same staleness the
        # gates enforce is reported in every brief that carries it.
        approved = False
        errors = list(errors or []) + stale
    return {"approved": approved,
            "stale": bool(stale) or None,
            "fingerprint": state.get("design_fingerprint"),
            "contract": contract, "errors": errors}


def _criteria_for(ws: str, state: dict, task: dict) -> list:
    criteria = list(task.get("criteria") or [])
    rid = task.get("req") or state.get("requirement_id")
    rec = reqs.get_requirement(ws, rid) if rid else None
    if rec and not criteria:
        criteria = list(rec.get("acceptance") or criteria)
    criteria = [str(c).strip() for c in criteria if str(c).strip()]
    if not criteria and str(task.get("tests") or "").strip():
        criteria = [f"test command passes: {task['tests']}"]
    return criteria


def _aggregate_impact_policy(tasks) -> dict:
    return depgraph.aggregate_impact_policy(tasks)


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
        errors.extend(prefix + problem for problem in
                      tp.plan_test_command_errors(task.get("tests")))
        # A requirement or test command can help an evaluator explain a
        # legacy task, but neither is the executable task contract approved
        # at Plan.  Every task must carry its own non-empty criteria so a
        # metadata defect cannot silently expand evaluation to program-wide
        # acceptance criteria.
        explicit_criteria = task.get("criteria")
        if not isinstance(explicit_criteria, list) or not any(
                str(criterion).strip() for criterion in explicit_criteria):
            errors.append(prefix + "explicit acceptance criteria are "
                          "missing or empty")
        rid = task.get("req") or state.get("requirement_id")
        rec = reqs.get_requirement(ws, rid) if rid else None
        if rec:
            # Requirements own stable product/contract dependencies; the plan
            # may add contracts but cannot silently erase the requirement's
            # boundaries with an empty or narrower task-level list.
            merged_contracts, seen_contracts = [], set()
            for contract in list(rec.get("contracts") or []) + \
                    list(task.get("contracts") or []):
                cids = depgraph.contract_ids([contract])
                cid = cids[0] if cids else ""
                if cid and cid not in seen_contracts:
                    merged_contracts.append(contract)
                    seen_contracts.add(cid)
            if apply:
                task["contracts"] = merged_contracts
            for dep in rec.get("depends_on") or []:
                if reqs.get_requirement(ws, dep) is None:
                    errors.append(prefix + f"requirement dependency {dep} "
                                  "does not exist")
                elif apply:
                    # Requirements are the source of truth. Reconcile their
                    # product edges before graph Ready instead of depending on
                    # a particular CLI path having populated the derived map.
                    depgraph.link_requirement_dep(ws, rid, dep)
            if apply:
                for contract in rec.get("contracts") or []:
                    cids = depgraph.contract_ids([contract])
                    relation = (contract.get("relation", "changes")
                                if isinstance(contract, dict) else "changes")
                    if cids:
                        depgraph.record_edge(
                            ws, depgraph.req_node(rid), cids[0],
                            kind=relation, confidence="high")
        if apply:
            task["impact_policy"] = depgraph.impact_policy(task)
        if rid and task.get("high_cost"):
            if rec is None:
                errors.append(prefix + f"requirement {rid} does not exist")
            elif rec.get("open_questions"):
                errors.append(prefix + "requirement has unresolved questions: "
                              + "; ".join(rec["open_questions"]))
    graph_dor = depgraph.readiness(ws, state.get("tasks") or [])
    if apply:
        state["graph_dor"] = graph_dor
    errors.extend("graph DoR: " + e for e in graph_dor.get("errors") or [])
    errors.extend(tp.requirement_coverage_errors(state.get("tasks") or [],
        lambda rid: reqs.get_requirement(ws, rid), state.get("requirement_id")))
    errors.extend("design DoR: " + e for e in _design_plan_errors(ws, state))
    return errors


_REANCHOR_CONTRACT_FIELDS = (
    "id", "scope", "tests", "req", "deps", "type",
    # Accept both the documented semantic names and their task-file names.
    # If both are present they are both bound, so aliases cannot hide drift.
    "gap", "gap_category", "contracts", "modules", "new_modules",
    "design_edges", "impact", "impact_policy", "criteria",
)
_REANCHOR_SEQUENCE_FIELDS = frozenset({
    "scope", "deps", "contracts", "modules", "new_modules",
    "design_edges", "criteria",
})
_REANCHOR_MAPPING_FIELDS = frozenset({"impact", "impact_policy"})


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
    return hashlib.sha256(tp.canonical_json_bytes(
        _reanchor_contract(task))).hexdigest()


_REANCHOR_CRITERION_PROOF_SCHEMA = \
    "taskplane.reanchor-criterion-proof/v1"
_REANCHOR_PROOF_FIELDS = frozenset({
    "schema", "authority_schema", "task_id", "contract_fingerprint",
    "source_revision", "evaluation_sha256", "criteria_status_sha256",
    "receipt_sha256", "disposition", "key_id",
})


def _verified_criterion_evidence(value) -> bool:
    """Recognize only a post-verification engine authority projection."""
    if not isinstance(value, Mapping) or set(value) != \
            _REANCHOR_PROOF_FIELDS or value.get("schema") != \
            _REANCHOR_CRITERION_PROOF_SCHEMA or value.get(
                "authority_schema") != _REANCHOR_AUTHORITY_SCHEMA:
        return False
    if not str(value.get("task_id") or "").strip() or value.get(
            "disposition") not in {
                "independent-pass", "human-resolved-orchestration-outage"}:
        return False
    for field in ("contract_fingerprint", "evaluation_sha256",
                  "criteria_status_sha256", "receipt_sha256", "key_id"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(value.get(field) or "")):
            return False
    return bool(re.fullmatch(
        r"(?:[0-9a-f]{40}|[0-9a-f]{64})",
        str(value.get("source_revision") or "")))


_REANCHOR_AUTHORITY_SCHEMA = "taskplane.reanchor-pass-authority/v2"
_REANCHOR_AUTHORITY_REF_SCHEMA = \
    "taskplane.reanchor-pass-authority-reference/v1"
_REANCHOR_ANCESTRY_TIMEOUT_SECONDS = 10


def _validated_reanchor_verdict(task: Mapping, verdict: Mapping,
                                disposition: str) -> str:
    """Validate the complete gate verdict and digest its criterion statuses."""
    task_id = str(task.get("id") or "")
    requirement = str(task.get("req") or "")
    if not isinstance(verdict, Mapping) or verdict.get("schema") != \
            "taskplane.evaluator-output/v1" or str(
                verdict.get("task") or "") != task_id or str(
                verdict.get("requirement") or "") != requirement:
        raise ValueError("reanchor verdict identity is invalid")
    criteria = list(task.get("criteria") or [])
    rows = verdict.get("criteria")
    if not criteria or not isinstance(rows, list) or len(rows) != len(criteria):
        raise ValueError("reanchor verdict criteria are incomplete")
    normalized = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or row.get("criterion") != \
                criteria[index] or row.get("status") != "met":
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
    elif disposition == "human-resolved-orchestration-outage":
        evaluation = verdict.get("evaluation")
        if verdict.get("verdict") != "fail" or not isinstance(
                evaluation, Mapping) or evaluation.get("status") != \
                "unavailable" or evaluation.get("reason_code") != \
                "orchestration_unavailable":
            raise ValueError("reanchor verdict is not a resolved outage")
    else:
        raise ValueError("reanchor disposition is invalid")
    return hashlib.sha256(tp.canonical_json_bytes(normalized)).hexdigest()


def _reanchor_authority_material(task: Mapping, *, source_revision: str,
                                 evaluation_sha256: str,
                                 criteria_status_sha256: str,
                                 disposition: str,
                                 outage_identity=None) -> dict:
    return {
        "schema": _REANCHOR_AUTHORITY_SCHEMA,
        "task_id": str(task.get("id") or ""),
        "contract_fingerprint": _reanchor_fingerprint(task),
        "source_revision": str(source_revision or "").lower(),
        "evaluation_sha256": str(evaluation_sha256 or "").lower(),
        "criteria_status_sha256": str(
            criteria_status_sha256 or "").lower(),
        "disposition": str(disposition or ""),
        "outage_identity": (outage_identity if disposition ==
                            "human-resolved-orchestration-outage" else None),
    }


def _persist_reanchor_authority(workspace: str, task: Mapping,
                                disposition: str) -> tuple[dict, str]:
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
    criteria_status_sha256 = _validated_reanchor_verdict(
        task, verdict, disposition)
    warning = task.get("evaluation") if isinstance(
        task.get("evaluation"), Mapping) else {}
    material = _reanchor_authority_material(
        task, source_revision=source_revision,
        evaluation_sha256=evaluation_sha256,
        criteria_status_sha256=criteria_status_sha256,
        disposition=disposition,
        outage_identity=warning.get("outage_identity"))
    authority = tp._review_contract_authority(workspace, create=True)
    unsigned = {**material, "key_id": authority["key_id"]}
    signature = hmac.new(
        authority["secret"], tp.canonical_json_bytes(unsigned),
        hashlib.sha256).hexdigest()
    receipt = {**unsigned, "signature": signature}
    receipt_path = runtime_storage.evaluation_path(
        workspace, "reanchor-authority.json")
    tp.atomic_write_json(receipt_path, receipt, sort_keys=True)
    with open(receipt_path, "rb") as stream:
        receipt_bytes = stream.read()
    reference = {
        "schema": _REANCHOR_AUTHORITY_REF_SCHEMA,
        "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "key_id": authority["key_id"],
    }
    return reference, source_revision


def _verify_reanchor_authority(workspace: str, task: Mapping,
                               prior: Mapping, *, source_revision: str,
                               evaluation_sha256: str,
                               criteria_status_sha256: str,
                               disposition: str) -> tuple[dict | None,
                                                          str | None]:
    reference = prior.get("reanchor_authority")
    if not isinstance(reference, Mapping) or reference.get("schema") != \
            _REANCHOR_AUTHORITY_REF_SCHEMA or set(reference) != {
                "schema", "receipt_sha256", "key_id"}:
        return None, "engine-authored reanchor authority receipt is missing"
    receipt_path = runtime_storage.evaluation_path(
        workspace, "reanchor-authority.json")
    try:
        with open(receipt_path, "rb") as stream:
            receipt_bytes = stream.read()
        receipt = json.loads(receipt_bytes)
    except (OSError, ValueError) as exc:
        return None, f"engine-authored reanchor authority is unavailable: {exc}"
    if hashlib.sha256(receipt_bytes).hexdigest() != \
            reference.get("receipt_sha256"):
        return None, "engine-authored reanchor authority bytes changed"
    expected = _reanchor_authority_material(
        task, source_revision=source_revision,
        evaluation_sha256=evaluation_sha256,
        criteria_status_sha256=criteria_status_sha256,
        disposition=disposition,
        outage_identity=((prior.get("evaluation") or {}).get(
            "outage_identity") if isinstance(
                prior.get("evaluation"), Mapping) else None))
    try:
        authority = tp._review_contract_authority(workspace, create=False)
    except Exception as exc:
        return None, f"reanchor signing authority is unavailable: {exc}"
    if not isinstance(receipt, Mapping):
        return None, "engine-authored reanchor authority is malformed"
    unsigned = {**expected, "key_id": authority["key_id"]}
    signature = hmac.new(
        authority["secret"], tp.canonical_json_bytes(unsigned),
        hashlib.sha256).hexdigest()
    if reference.get("key_id") != authority["key_id"] or \
            set(receipt) != set(unsigned) | {"signature"} or \
            {key: receipt.get(key) for key in unsigned} != unsigned or \
            not hmac.compare_digest(str(receipt.get("signature") or ""),
                                    signature):
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
        ws: str, task: Mapping, prior: Mapping) -> tuple[dict | None,
                                                         str | None]:
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
            registration = runtime_storage.load_task_worktree_registration(
                ws, task_id)
        except runtime_storage.StorageIdentityError as exc:
            return None, f"managed source registration is invalid: {exc}"
        if not isinstance(registration, Mapping):
            return None, "managed source registration is missing"
        if os.path.realpath(str(registration.get("path") or "")) != workspace \
                or os.path.realpath(str(
                    registration.get("primary_checkout") or "")) != primary \
                or registration.get("branch_tip") != target \
                or registration.get("linked") is not True:
            return None, "managed source registration does not bind exact target"

    # Safe argv only: source evidence must still be reachable from the tree
    # whose new Plan is being accepted.
    import subprocess
    try:
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", target, "HEAD"],
            cwd=ws, capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=False,
            timeout=_REANCHOR_ANCESTRY_TIMEOUT_SECONDS)
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
    if not isinstance(verdict, Mapping) or verdict.get("schema") != \
            "taskplane.evaluator-output/v1":
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
    if isinstance(availability, Mapping) and \
            availability.get("status") == "unavailable":
        human = prior.get("human_resolution")
        warning = prior.get("evaluation")
        if not isinstance(human, Mapping) or human.get("decision") != "pass":
            return None, "unavailable evaluation has no resolved human pass"
        if not isinstance(warning, Mapping) or \
                warning.get("status") != "unavailable" or \
                warning.get("verdict") != "non-judged" or \
                warning.get("reason_code") != "orchestration_unavailable":
            return None, "resolved outage warning is not exact"
        if verdict.get("verdict") != "fail" or \
                availability.get("reason_code") != "orchestration_unavailable":
            return None, "durable outage verdict is not a non-judged failure"
        try:
            identity = evaluator_health.outage_identity(
                task=task_id, requirement=requirement,
                evaluation=availability, failures=failures)
        except evaluator_health.EvaluatorHealthError as exc:
            return None, f"durable outage identity is invalid: {exc}"
        if warning.get("outage_identity") != identity:
            return None, "resolved outage identity no longer matches verdict"
        resolution = "human-resolved-orchestration-outage"
    elif verdict.get("verdict") != "pass" or failures:
        return None, "durable evaluator verdict is not an exact pass"

    evaluation_sha256 = hashlib.sha256(verdict_bytes).hexdigest()
    try:
        criteria_status_sha256 = _validated_reanchor_verdict(
            task, verdict, resolution)
    except ValueError as exc:
        return None, f"durable evaluator verdict is invalid: {exc}"
    criterion_proof, authority_error = _verify_reanchor_authority(
        workspace, task, prior, source_revision=target,
        evaluation_sha256=evaluation_sha256,
        criteria_status_sha256=criteria_status_sha256,
        disposition=resolution)
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


def _reanchor_replanned_tasks(
        ws: str, state: dict) -> tuple[dict | None, list]:
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
                return None, (f"replan reanchor: {label} task identity "
                              "is missing or duplicated")
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
            pending[task_id] = {"task_id": task_id,
                                "reason": "new_task"}
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
                "task_id": task_id, "reason": "archived_task_not_passed",
                "archived_status": prior.get("status"),
            }
            continue
        evidence, evidence_error = _verify_reanchor_task_evidence(
            ws, task, prior)
        if evidence_error:
            pending[task_id] = {
                "task_id": task_id, "reason": "evidence_unverified",
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
            for field in ("workspace", "target_commit", "human_resolution",
                          "evaluation", "reanchor_authority"):
                if field in prior:
                    task[field] = json.loads(json.dumps(prior[field]))
            restored_ids.add(task_id)
            restored.append({
                "task_id": task_id,
                "contract_fingerprint": _reanchor_fingerprint(task),
                **dict(evidence or {}),
            })
            progressed = True

    for task_id, (task, _, _) in candidates.items():
        if task_id in restored_ids:
            continue
        missing = [dep for dep in list(task.get("deps") or [])
                   if dep not in restored_ids]
        pending[task_id] = {
            "task_id": task_id, "reason": "dependency_not_reanchored",
            "dependencies": missing,
        }

    receipt = {
        "schema": "taskplane.replan-reanchor/v1",
        "replan_index": len(history) - 1,
        "replan_by": history[-1].get("by"),
        "replan_reason": history[-1].get("reason"),
        "contract_fields": list(_REANCHOR_CONTRACT_FIELDS),
        "restored": restored,
        "pending": [pending[task_id] for task_id in current_by_id
                    if task_id in pending],
        "restored_count": len(restored),
        "pending_count": len(current) - len(restored),
        "dependency_closed": True,
    }
    receipt["fingerprint"] = hashlib.sha256(
        tp.canonical_json_bytes(receipt)).hexdigest()
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


def _task_graph_dod(ws: str, state: dict, task: dict) -> dict:
    """As-built dependency proof for one task.

    Parallel worktrees cannot replace the shared graph before merge, so their
    final graph proof is explicitly deferred to the merged EM review.
    """
    if state.get("parallel"):
        return {"passed": True, "deferred_to_post_merge": True,
                "errors": [], "impact": {}}
    baseline = state.get("baseline") or tp.snapshot_ref(ws)
    changed = [f for f in _diff_files(ws, baseline or "HEAD")
               if not f.startswith(lens_router.LOOP_OWNED)]
    stems = [g.split("*", 1)[0] for g in (task.get("scope") or [])]
    mine = [f for f in changed
            if not stems or any(f.startswith(s) for s in stems if s)]
    planned = ((task.get("blast") or {}).get("modules")
               or depgraph.scope_modules(ws, task.get("scope") or []))
    return depgraph.completion(
        ws, mine, planned_modules=planned,
        policy=task.get("impact_policy") or depgraph.impact_policy(task))


def _worker_stage_binding(workspace: str, stage: str,
                          task: Mapping | None) -> dict | None:
    """Read exact worker lifecycle metadata without binding root authority."""
    task_ref = str((task or {}).get("id") or stage)
    return tp.worker_contract_for_stage(
        workspace, stage=str(stage), task=task_ref)


def _worker_stage_contract(workspace: str, stage: str,
                           task: Mapping | None) -> dict:
    binding = _worker_stage_binding(workspace, stage, task)
    if binding is not None:
        return binding["contract"]
    return tp.load_active(workspace) or {}


def _worker_stage_snapshot(workspace: str, stage: str,
                           task: Mapping | None) -> str | None:
    binding = _worker_stage_binding(workspace, stage, task)
    return tp.snapshot_ref(
        workspace,
        task_slot_override=(binding or {}).get("slot"))


def _task_dod_errors(ws: str, state: dict, task: dict,
                     snapshot: str | None) -> list:
    contract = tp.build_contract(
        f"EXECUTE: {task['id']}", scope=task.get("scope"),
        test_command=task.get("tests"), plan_minted=True, regression_gate=True)
    # Scope regression evidence to this task; loop-owned artifacts self-gate.
    regression_files = [f for f in (tp.changed_files(ws, snapshot) if snapshot else [])
                        if tp.match_any(f, task.get("scope") or [])]
    suite_evidence = {}
    errors = (_design_current_errors(ws, state) + tp.dod_check(
        contract, ws, snapshot, ignore_prefixes=lens_router.LOOP_OWNED,
        regression_files=regression_files,
        suite_evidence=suite_evidence))
    # Preserve the long-standing four-argument patch seam used by race and
    # failure-injection tests. This is transient validation output: gate()
    # copies it into the fresh locked state only after all checks pass.
    if suite_evidence:
        state.setdefault("_validated_suite_evidence", {})[task["id"]] = \
            suite_evidence
    return errors


@contextlib.contextmanager
def _claimed_execute_suite_binding():
    """Run claimed EXECUTE/FIX suites from the task checkout namespace.

    The orchestrator engine may be older than a task branch that changes the
    engine itself. ``taskplane_lite.run_suite_command`` deliberately injects
    the orchestrator's module namespace for ordinary validation, which made
    an EXECUTE gate test stale engine bytes even though its cwd was the task
    worktree. A wave gate instead needs the same plain command semantics the
    executor used in that exact checkout. Force a fresh run so an earlier
    validator-namespace cache record cannot substitute for that evidence.
    FIX needs the same binding: a repair can change the engine that runs its
    declared suite, so injecting the stale orchestrator copy would reject the
    repair and can widen the gate into a repository-scale baseline run.
    """
    import subprocess

    original_runner = tp.run_suite_command
    original_lookup = tp.suite_cache_lookup

    def safe_argv(command):
        if isinstance(command, (list, tuple)):
            argv = list(command)
            if not argv or any(not isinstance(value, str) or not value
                               for value in argv):
                raise ValueError("declared suite argv is invalid")
            return argv
        if not isinstance(command, str) or not command.strip():
            raise ValueError("declared suite command is invalid")
        try:
            lexer = shlex.shlex(
                command, posix=True, punctuation_chars="|&;<>")
            lexer.whitespace_split = True
            lexer.commenters = ""
            argv = list(lexer)
        except ValueError as exc:
            raise ValueError(
                f"declared suite command has invalid quoting: {exc}") \
                from exc
        if not argv or any(token and set(token) <= set("|&;<>")
                           for token in argv):
            raise ValueError(
                "declared suite command contains shell operators")
        return argv

    def run_claimed(workspace, command, *, env=None, timeout=600):
        try:
            argv = safe_argv(command)
        except ValueError as exc:
            return subprocess.CompletedProcess(
                command, 2, stdout="", stderr=str(exc))
        return subprocess.run(
            argv,
            cwd=workspace,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            encoding="utf-8",
            errors="replace",
        )

    tp.run_suite_command = run_claimed
    tp.suite_cache_lookup = lambda workspace, command, env: None
    try:
        yield
    finally:
        tp.run_suite_command = original_runner
        tp.suite_cache_lookup = original_lookup


def collect_review_bridge(review_ws: str, *, publish: bool,
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
    """
    _, review_evidence, review_kernel = _review_runtime_modules()

    state = review_kernel._load_state(review_ws, run_id)
    try:
        store = review_evidence.ArtifactStore(review_ws)
        envelope_ref = state.get("envelope")
        envelope = store.read(envelope_ref) \
            if isinstance(envelope_ref, dict) else {}
        retained_diff = (envelope.get("diff") or {}).get("artifact")
        if isinstance(retained_diff, dict):
            read_retained_review_diff(
                review_ws, store=store, reference=retained_diff)
        empty_collection = None
        if state.get("delivery_mode_receipt") is not None:
            if state.get("expected_lenses") != [] or state.get("slots") != []:
                raise review_kernel.ReviewKernelError(
                    "sealed zero-lens Evaluate authority produced lens slots")
            if evaluator_result is None or \
                    producer_observation_fingerprint is None:
                raise review_kernel.ReviewKernelError(
                    "zero-lens collection requires a schema-valid producer "
                    "result and validated observation")
            validator = result_validator
            if validator is None:
                validator = (evaluation_output.validate_evaluator_value
                             if collection_stage == "Evaluate" else
                             lambda value: value)
            empty_collection = review_kernel.collect_expected_set(
                run_id=run_id,
                task_id=str((state.get("target") or {}).get("task") or ""),
                stage=collection_stage,
                expected_lenses=state["expected_lenses"],
                collected_lenses=[],
                result=evaluator_result,
                result_validator=validator,
                producer_observation_fingerprint=
                    producer_observation_fingerprint,
            )
        result = review_kernel.collect_review(
            review_ws, publish=publish, run_id=run_id,
            empty_lens_collection=empty_collection)
        if result.get("status") == "complete" and \
                isinstance(retained_diff, dict):
            purge = enforce_review_diff_retention(
                review_ws, store=store,
                purge_fingerprint=str(retained_diff.get("fingerprint") or ""))
            tp.trace(review_ws, "review_diff_retention_purge",
                     run_id=run_id,
                     review_id=str((state.get("target") or {}).get(
                         "fingerprint") or "unknown"),
                     count=purge.get("removed", 0))
        return result
    finally:
        review_kernel._release_slot_contracts(review_ws, state)


def _collect_zero_lens_evaluate_before_guidance(
        ws: str, act_ws: str, state: dict, task: dict,
        *, step: str = "evaluate") -> dict | None:
    """Consume the one native receipt and seal an ordinary empty set."""
    binding = review_kernel_binding(state, step, task)
    if not binding:
        return None
    kernel_ws = str(binding.get("workspace") or act_ws)
    _, _, review_kernel = _review_runtime_modules()
    kernel = review_kernel._load_state(kernel_ws, binding["run_id"])
    if kernel.get("delivery_mode_receipt") is None:
        return None
    if kernel.get("expected_lenses") != [] or kernel.get("slots") != []:
        raise review_kernel.ReviewKernelError(
            "sealed zero-lens Evaluate authority produced lens slots")
    active_contract = _worker_stage_contract(act_ws, step, task)
    material = producer_output_identity(
        act_ws, state, task, step, active_contract=active_contract)
    observation = producer_observation_policy.consume_matching_observation(
        **material)
    if step == "evaluate":
        result = evaluation_output.validate_evaluator_value(
            json.loads(material["output_bytes"].decode("utf-8")))
        collection_stage = "Evaluate"
        validator = evaluation_output.validate_evaluator_value
    else:
        findings_path = runtime_storage.review_public_path(
            act_ws, "findings.json")
        report_path = runtime_storage.review_public_path(act_ws, "report.md")
        findings, read_errors = _read_json(findings_path)
        if read_errors:
            raise producer_observation_policy.ProducerObservationError(
                "EM findings result is invalid")
        with open(report_path, "rb") as stream:
            report_bytes = stream.read()
        result = {"findings": findings,
                  "report_sha256": hashlib.sha256(report_bytes).hexdigest()}
        collection_stage = "EM"
        validator = lambda value: value
    if step == "evaluate":
        collect_review_bridge(
            kernel_ws, publish=False, run_id=binding["run_id"],
            evaluator_result=result,
            producer_observation_fingerprint=observation["fingerprint"],
            collection_stage=collection_stage, result_validator=validator)
    return observation


def _acceptance_evidence_errors(ws: str, state: dict, task: dict,
                                verdict: dict) -> list:
    """Static DoD evidence check, safe to compose with runtime guidance."""
    errors = []
    expected_criteria = _criteria_for(ws, state, task)
    rows = verdict.get("criteria") or []
    if not isinstance(rows, list):
        errors.append("evaluation criteria must be a list")
        rows = []
    by_criterion = {str(r.get("criterion", "")).strip(): r
                    for r in rows if isinstance(r, dict)}
    for criterion in expected_criteria:
        row = by_criterion.get(criterion)
        if not row:
            errors.append(f"acceptance criterion has no evidence: {criterion}")
        elif row.get("status") != "met" or not str(
                row.get("evidence") or "").strip():
            errors.append(
                f"acceptance criterion is not proven met: {criterion}")
    return errors


def _evaluation_errors(ws: str, state: dict, task: dict) -> list:
    """Validate evaluator evidence instead of trusting `gate pass`."""
    path = runtime_storage.evaluation_path(ws)
    verdict, errors = _read_json(path)
    if errors:
        return errors
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
    if not kernel or kernel.get("stage") != EVALUATE_ROUTE_STAGE:
        # Compatibility for callers that historically submitted and gated an
        # evaluator directly without first asking for `loop next`.  This is
        # still a single mapping: materialize the normal brief once, then the
        # validator below consumes only its persisted decision.
        try:
            action = getattr(next_action, "__wrapped__", next_action)(ws)
            if not action.get("error"):
                state = load(ws) or state
                binding = review_kernel_binding(state, "evaluate", task)
                kernel_ws = str((binding or {}).get("workspace") or ws)
                if binding:
                    kernel = _review._load_state(
                        kernel_ws, binding["run_id"])
        except Exception:
            kernel = None
    if kernel and kernel.get("status") == "ready" and \
            kernel.get("stage") == EVALUATE_ROUTE_STAGE:
        try:
            evaluator_result = None
            observation_fingerprint = None
            if kernel.get("delivery_mode_receipt") is not None:
                evaluator_result = evaluation_output.validate_evaluator_value(
                    verdict)
                submission = state.get("_submission") or {}
                observation = \
                    producer_observation_policy.validate_producer_observation(
                        submission.get("producer_observation"))
                with open(path, "rb") as stream:
                    verdict_bytes = stream.read()
                observation = evaluation_output.validate_submission_observation(
                    submission,
                    output_bytes=verdict_bytes,
                    output_schema_id=
                        evaluation_output.EVALUATOR_OUTPUT_SCHEMA_ID,
                    output_contract_fingerprint=observation[
                        "output_contract_fingerprint"],
                )
                observation_fingerprint = observation["fingerprint"]
            collect_review_bridge(
                kernel_ws, publish=False, run_id=kernel.get("run_id"),
                evaluator_result=evaluator_result,
                producer_observation_fingerprint=observation_fingerprint)
            kernel = _review._load_state(
                kernel_ws, kernel.get("run_id"))
        except Exception as exc:
            errors.append("evaluation leased slot collection failed: "
                          f"{exc.__class__.__name__}: {exc}")
    quick_output_sufficient = False
    if kernel and kernel.get("stage") == EVALUATE_ROUTE_STAGE:
        try:
            import review_evidence as _review_evidence
            quality = _review_evidence.ArtifactStore(kernel_ws).read(
                kernel["quality"])
            quick_output_sufficient = \
                runtime_eval._complete_quick_only_evaluation(
                    kernel, quality, verdict, _review)
        except Exception:
            # The ordinary complete-kernel path remains the fail-closed
            # fallback when the narrow R-0006 quick-output proof is absent or
            # malformed.
            quick_output_sufficient = False
    if (not kernel or kernel.get("status") != "complete" or
            kernel.get("stage") != EVALUATE_ROUTE_STAGE) and \
            not quick_output_sufficient:
        errors.append("evaluation selective review kernel is missing or incomplete")
    if verdict.get("task") != task.get("id"):
        errors.append("evaluation evidence is for task "
                      f"{verdict.get('task')!r}, expected {task.get('id')!r}")
    if verdict.get("verdict") != "pass":
        errors.append("evaluation verdict is not pass")

    errors.extend(_acceptance_evidence_errors(ws, state, task, verdict))

    # Derive the expected lens set with the SAME stage the evaluate brief
    # routed with (EVALUATE_ROUTE_STAGE — single-sourced, R-0006 row 1), so
    # expectation matches dispatch. Route v2 returns EVERY catalog lens for
    # coverage honesty; only the ROUTED ones (deep + light, mode != "none")
    # owe the evaluator a verdict row — n/a lenses carry their negative
    # evidence in the routing itself. On the legacy path no entry has mode
    # "none", so the filter is a no-op there.
    # Consume the one decision created after graph quality; never map again
    # inside the gate on potentially different inputs.
    routing = ((kernel or {}).get("routing") or {"lenses": []})
    expected_lenses = {entry["id"] for entry in routing.get("lenses") or []
                       if entry.get("mode") != "none"}
    canonical_rows = {str(row.get("lens") or ""): row for row in
                      ((kernel or {}).get("lens_results") or [])
                      if isinstance(row, dict)}
    canonical_blocking = _review.blocking_findings_by_lens(
        ((kernel or {}).get("revision") or {}).get("findings") or [])
    for lens_id, count in sorted(canonical_blocking.items()):
        errors.append(
            f"canonical blocking finding prevents Evaluate pass: {lens_id} "
            f"({count})")
    if not quick_output_sufficient and set(canonical_rows) != expected_lenses:
        missing_slots = sorted(expected_lenses - set(canonical_rows))
        unexpected_slots = sorted(set(canonical_rows) - expected_lenses)
        if missing_slots:
            errors.append("leased slot results omit routed lenses: "
                          + ", ".join(missing_slots))
        if unexpected_slots:
            errors.append("leased slot results contain unexpected lenses: "
                          + ", ".join(unexpected_slots))
    raw_lenses = verdict.get("lenses") or []
    if not isinstance(raw_lenses, list):
        errors.append("evaluation lenses must be a list")
        raw_lenses = []
    lens_rows = {str(r.get("lens", "")): r for r in raw_lenses
                 if isinstance(r, dict)}
    for lens_id in sorted(expected_lenses):
        row = lens_rows.get(lens_id)
        if not row:
            errors.append(f"routed lens has no verdict: {lens_id}")
        else:
            canonical = canonical_rows.get(lens_id)
            if canonical is None and not quick_output_sufficient:
                errors.append(f"routed lens lacks a leased slot result: {lens_id}")
                continue
            try:
                blocker_count = int(row.get("blockers") or 0)
            except (TypeError, ValueError):
                blocker_count = 1
            if row.get("verdict") != "pass" or blocker_count > 0:
                errors.append(f"routed lens did not pass cleanly: {lens_id}")
            if canonical is not None and (row.get("verdict"), blocker_count) != \
                    (canonical.get("verdict"), canonical.get("blockers")):
                errors.append(
                    f"routed lens verdict contradicts leased result: {lens_id}")
    if verdict.get("failures"):
        errors.append("evaluation contains unresolved failures")
    if state.get("graph_governance"):
        graph_dod = _task_graph_dod(ws, state, task)
        errors.extend("graph DoD: " + e for e in graph_dod.get("errors") or [])
        if not graph_dod.get("deferred_to_post_merge"):
            impact = graph_dod.get("impact") or {}
            direct = sorted({e.get("module")
                             for e in (impact.get("impacted") or {}).get(1, [])
                             if e.get("module")
                             and not str(e.get("module")).startswith("req:")})
            prod = depgraph.product_impact(ws,
                                           graph_dod.get("realized_modules") or [])
            own = task.get("req") or state.get("requirement_id")
            own = depgraph.req_node(own) if own else None
            affected = sorted(r for r in prod.get("affected_requirements") or []
                              if r != own)
            needs_graph_evidence = bool(
                direct or affected or graph_dod.get("contract_files")
                or impact.get("unknown") or impact.get("truncated"))
            graph_ev = verdict.get("graph") or {}
            if needs_graph_evidence and not isinstance(verdict.get("graph"), dict):
                errors.append("evaluation is missing graph impact evidence")
                graph_ev = {}
            dispositions = {str(x.get("node")): x for x in
                            (graph_ev.get("dispositions") or [])
                            if isinstance(x, dict)}
            allowed = {"tested", "contract-verified", "unaffected",
                       "follow-up", "requires-replan"}
            for node in direct:
                row = dispositions.get(node)
                if (not row or row.get("status") not in allowed
                        or not str(row.get("evidence") or "").strip()):
                    errors.append(f"graph impact has no evidenced disposition: {node}")
                elif row.get("status") == "requires-replan":
                    errors.append(f"graph impact requires replanning: {node}")
            checked = set(graph_ev.get("requirements_checked") or [])
            for rid in affected:
                if rid not in checked:
                    errors.append("affected requirement was not re-checked: " + rid)
            expected_contracts = set()
            for contract_row in task.get("contracts") or []:
                contract_id = (contract_row.get("id")
                               if isinstance(contract_row, dict)
                               else contract_row)
                if str(contract_id or "").strip():
                    expected_contracts.add(str(contract_id))
            checked_contracts = set(graph_ev.get("contracts_checked") or [])
            for contract in sorted(expected_contracts - checked_contracts):
                errors.append("declared contract was not verified: " + contract)
    return errors


def _canonical_evaluation_progress(ws: str, state: dict,
                                   task: dict) -> dict | None:
    """Project the committed evaluator revision into convergence facts."""
    import review as _review
    import review_evidence as _review_evidence

    binding = review_kernel_binding(state, "evaluate", task)
    if not binding:
        return None
    kernel_ws = str(binding.get("workspace") or ws)
    kernel = _review._load_state(kernel_ws, binding["run_id"])
    if kernel.get("status") != "complete" or \
            kernel.get("stage") != EVALUATE_ROUTE_STAGE:
        return None
    sealed = _review_evidence.sealed_current_revision(
        _review_evidence.ArtifactStore(kernel_ws), kernel.get("revision") or {})
    verdict, read_errors = _read_json(runtime_storage.evaluation_path(kernel_ws))
    if read_errors:
        verdict = {}
    criteria = verdict.get("criteria") if isinstance(verdict, dict) else []
    evidence_complete = sum(
        isinstance(row, dict) and row.get("status") == "met" and
        bool(str(row.get("evidence") or "").strip())
        for row in (criteria if isinstance(criteria, list) else []))
    suite = ((state.get("_suite_evidence") or {}).get(str(task.get("id")))
             or {})
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
            suite.get("schema") == "taskplane.suite-evidence/v1" and
            suite.get("returncode") == 0),
        "canonical_revision": sealed["canonical_revision"],
        "findings_fingerprint": sealed["findings_fingerprint"],
        "scope_fingerprint": _review_evidence.content_fingerprint({
            "scope": task.get("scope") or [],
            "contracts": task.get("contracts") or [],
        }),
        "authority_fingerprint": _review_evidence.content_fingerprint(
            state.get("authority_derivations") or {}),
    }


def _evaluation_unavailable_errors(ws: str, state: dict,
                                   task: dict) -> tuple[list, dict]:
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
        errors.append("evaluation evidence is for task "
                      f"{verdict.get('task')!r}, expected {task.get('id')!r}")
    if verdict.get("verdict") != "fail":
        errors.append("unavailable evaluation must retain verdict 'fail'")
    not_met = [row.get("criterion") for row in verdict.get("criteria") or []
               if isinstance(row, dict) and row.get("status") == "not-met"]
    if not_met:
        errors.append("product acceptance is not met: " + ", ".join(
            str(item) for item in not_met))
    blocking_lenses = [row.get("lens") for row in verdict.get("lenses") or []
                       if isinstance(row, dict) and
                       (row.get("verdict") == "fail" or
                        int(row.get("blockers") or 0) > 0)]
    if blocking_lenses:
        errors.append("product/lens failures cannot be classified as host "
                      "unavailability: " + ", ".join(
                          str(item) for item in blocking_lenses))
    if not (verdict.get("failures") or []):
        errors.append("evaluation unavailability has no bounded failure record")
    suite = ((state.get("_suite_evidence") or {}).get(str(task.get("id")))
             or {})
    if task.get("tests") and not (
            suite.get("schema") == "taskplane.suite-evidence/v1" and
            suite.get("returncode") == 0):
        errors.append("evaluation unavailability requires green mechanical "
                      "suite evidence from the execute/fix gate")
    if state.get("_build_failed") or task.get("_build_failed"):
        errors.append("a failed build is a product failure, not evaluation "
                      "unavailability")
    return errors, verdict


# One canonical severity vocabulary (v2.3.0). Producers disagree — the lens
# brief says high|med|low, the lens catalog's verdict schema says
# blocker|major|minor|question|praise, free-form reviews say critical —
# so every CONSUMER normalizes through this map. Enforcement rule: unknown
# or foreign severities map UP to 'high'; a finding a gate cannot classify
# must BLOCK, never pass or render as medium (fail closed).
SEVERITY_CANONICAL = ("high", "med", "low", "info")
_SEVERITY_MAP = {
    "high": "high", "critical": "high", "blocker": "high", "major": "high",
    "sev1": "high", "p0": "high", "p1": "high",
    "med": "med", "medium": "med", "moderate": "med",
    "low": "low", "minor": "low", "trivial": "low",
    "info": "info", "question": "info", "praise": "info", "note": "info",
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
    "regression": "regression", "regressed": "regression",
    "pre-existing": "pre-existing", "preexisting": "pre-existing",
    "pre_existing": "pre-existing", "existing": "pre-existing",
    "debt": "pre-existing", "legacy": "pre-existing",
    "observation": "observation", "taste": "observation",
    "style": "observation", "nit": "observation", "opinion": "observation",
    "suggestion": "observation", "enhancement": "observation",
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
        return True                      # no diff context → cannot exclude
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
    out = {"blockers": [], "regressions": [], "pre_existing": [],
           "observations": [], "unclassified": []}
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


# Evidence bundle → evidence.py (P2 / R-0012); extracted like audit.py, same
# line ratchet. UNTRUSTED INPUT — _evaluation_errors re-derives every
# obligation itself (evidence.py's docstring: why it stays out of VALIDATOR_SURFACE).
from evidence import EVIDENCE_JUDGMENT_KEYS, evidence  # noqa: E402,F401

# ---------------------------------------------------------------------------
# Audit sweep cadence + router-regression auto-filing (v3 Phase 1, R-0001):
# MOVED VERBATIM to audit.py (R-0006 / D-0004, v3 Phase 2), byte-frozen by
# taskplane/tests/test_audit_extraction.py. The names below are CALLER
# aliases bound once at import — NOT patch seams (t9 / R-0011 E6). Patch the
# MACHINERY at audit.<name> (audit.audit_counter, audit.audit_every, …),
# resolved module-locally inside audit.py: rebinding the loop alias is
# invisible to audit_due. Patch the GATE MATH at loop.<name>
# (finding_blocks, normalize_finding_class, load, _state_dir) — audit.py
# late-binds those via _loop() (audit.py:41-51) every call, so a patched
# loop.finding_blocks does govern the gate. TestPatchSeams pins both halves.
from audit import (  # noqa: E402,F401 — re-exports, not dead imports
    AUDIT_EVERY_DEFAULT,
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
    """Legacy coverage values are tier strings ('deep'|'sweep'); v2 values
    (contract:findings-v2) are {verdict, ...} objects. One accessor so the
    tier validation accepts both shapes."""
    if isinstance(v, dict):
        v = v.get("verdict")
    return str(v or "")


def _engineering_review_errors(ws: str, state: dict | None = None) -> list:
    """Require full-catalog lens evidence before the EM gate can pass."""
    path = runtime_storage.review_public_path(ws, "findings.json")
    findings, errors = _read_json(path)
    if errors:
        return errors
    report_path = runtime_storage.review_public_path(ws, "report.md")
    try:
        with open(report_path, encoding="utf-8") as report_file:
            report_text = report_file.read()
        if not report_text.strip():
            errors.append("engineering narrative report is empty")
    except OSError:
        errors.append("engineering narrative report is missing: "
                      + report_path)
    meta = findings.get("meta") or {}
    # Real EM/sign-off gates always pass loop state and therefore require the
    # canonical selective kernel. ``state=None`` is the long-standing pure
    # classification seam used by audit/finding unit tests; keeping it free
    # of repository orchestration lets those tests judge only the rule they
    # name without constructing a fake review transaction.
    if state is not None:
        try:
            import review as _review
            import review_evidence as _review_evidence
            binding = review_kernel_binding(
                state, "em", _current_task(state))
            if not binding:
                raise RuntimeError("loop state has no bound EM review kernel")
            kernel_ws = str(binding.get("workspace") or ws)
            kernel = _review._load_state(kernel_ws, binding["run_id"])
            if kernel.get("status") != "complete" or kernel.get("stage") != "review":
                errors.append("engineering selective review kernel is incomplete")
            else:
                current = _review_evidence._read_current(
                    _review_evidence.ArtifactStore(kernel_ws))
                for key, value in (current or {}).items():
                    if meta.get(key) != value:
                        errors.append("engineering review contradicts canonical "
                                      f"revision identity: {key}")
        except Exception as exc:
            errors.append("engineering canonical revision is missing: "
                          f"{exc.__class__.__name__}: {exc}")
    if state:
        errors.extend(_design_review_errors(ws, state, meta))
    coverage = meta.get("lens_coverage") or {}
    if not isinstance(coverage, dict):
        errors.append("engineering lens coverage must be an object")
        coverage = {}
    catalog = lens_router.load_catalog()
    expected = {entry["id"] for entry in catalog.get("lenses") or []}
    missing = sorted(expected - set(coverage))
    # Legacy tiers ('deep'|'sweep') and v2 verdicts (contract:findings-v2:
    # {verdict: deep|light|n/a|deep (forced), ...}) are both valid coverage.
    valid_tiers = ("deep", "sweep", "light", "n/a", "deep (forced)")
    invalid = sorted(k for k, v in coverage.items()
                     if k in expected
                     and _coverage_disposition(v) not in valid_tiers)
    if missing:
        errors.append("engineering review omitted lenses: " + ", ".join(missing))
    if invalid:
        errors.append("engineering review has invalid lens tiers: "
                      + ", ".join(invalid))
    # EM v3 tightening: a lens skipped as n/a must carry MACHINE-CHECKABLE
    # negative evidence (the v2 dict shape with negative_evidence). A bare
    # string "n/a" asserted the skip without evidence AND slipped past the
    # router-audit backstop (which only diffs dict-shaped decisions) — the
    # one disposition that reduces coverage was the one with no proof.
    bare_na = sorted(
        k for k, v in coverage.items()
        if k in expected and isinstance(v, str) and v.strip().lower() == "n/a")
    if bare_na:
        errors.append(
            "engineering review marks lenses n/a without negative evidence "
            "(use the v2 dict shape {verdict: 'n/a', negative_evidence: "
            "[...]}): " + ", ".join(bare_na))
    else:
        for k, v in sorted(coverage.items()):
            if (k in expected and isinstance(v, dict)
                    and str(v.get("verdict", "")).strip().lower() == "n/a"
                    and not v.get("negative_evidence")):
                errors.append(
                    "engineering review marks lens n/a with EMPTY "
                    "negative_evidence: " + k)
    impact_ev = meta.get("impact")
    if not isinstance(impact_ev, dict):
        errors.append("engineering review is missing dependency impact evidence")
    elif (state or {}).get("graph_governance"):
        required = {"touched", "impacted", "total_impacted", "unknown",
                    "depth_limit", "truncated", "policy", "graph"}
        missing_impact = sorted(required - set(impact_ev))
        if missing_impact:
            errors.append("engineering dependency impact evidence is incomplete: "
                          + ", ".join(missing_impact))
        changed = [f for f in _diff_files(
            ws, (state or {}).get("baseline") or "HEAD")
            if not f.startswith(lens_router.LOOP_OWNED)]
        if changed:
            review_policy = _aggregate_impact_policy(
                (state or {}).get("tasks") or [])
            expected = depgraph.impact(ws, changed, policy=review_policy)
            if not impact_ev.get("touched"):
                errors.append("engineering dependency impact names no touched modules")
            elif not set(expected.get("touched") or []) <= \
                    set(impact_ev.get("touched") or []):
                errors.append("engineering dependency impact does not cover the diff")
            expected_fp = (expected.get("graph") or {}).get("content_fingerprint")
            actual_fp = (impact_ev.get("graph") or {}).get("content_fingerprint")
            if expected_fp and actual_fp != expected_fp:
                errors.append("engineering dependency impact uses a stale graph revision")
            if impact_ev.get("policy") != review_policy:
                errors.append("engineering dependency impact uses the wrong review policy")
    if not meta.get("tests"):
        errors.append("engineering review is missing test evidence")
    gate = meta.get("gate") or {}
    if gate.get("verdict") not in ("pass", "recommend-pass"):
        errors.append("engineering review does not recommend sign-off — "
                      'set meta.gate.verdict to "pass" or "recommend-pass" '
                      "in " + runtime_storage.review_public_path(
                          ws, "findings.json"))
    rows = findings.get("findings") or []
    if not isinstance(rows, list):
        errors.append("engineering findings must be a list")
        rows = []
    # v2.3.0 raw unresolved-high sweep (body in audit.py): unknown
    # severities normalize UP to high and BLOCK. Machinery warn rows are
    # exempt ONLY when re-derived as legitimate this run — the A5 shape
    # alone is a costume any findings author can wear.
    errors.extend(_unresolved_high_errors(meta, rows))
    # R-0013: commentary may not block this gate (body in audit.py).
    errors.extend(_blocking_claim_errors(ws, state, rows))
    # Audit sweep (v3 Phase 1): when the review recorded a routing decision,
    # diff the findings against it — n/a-lens findings are auto-filed as
    # router regressions and block sign-off via the frozen finding_blocks
    # rule (no guardrail change).
    errors.extend(_router_audit_gate(ws, path, findings, meta, rows))
    return errors


def _submission_evidence_engine_workspace(
        ws: str, state: dict, task: dict | None, act_ws: str) -> str:
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
    if state.get("step") != "evaluate" or act_ws == ws or \
            not state.get("parallel") or not task:
        return act_ws
    target = str(task.get("target_commit") or "").strip()
    if len(target) not in (40, 64) or any(
            character not in "0123456789abcdef" for character in target):
        return act_ws
    try:
        if tp.git_head(act_ws) != target:
            return act_ws
        contained = tp._run(
            ["git", "merge-base", "--is-ancestor", target, "HEAD"],
            cwd=ws)
    except Exception:
        return act_ws
    return ws if contained.returncode == 0 else act_ws


def _run_submit_checkpoint(ws: str, state: Mapping[str, object],
                           task: Mapping[str, object], act_ws: str) -> dict:
    """Run one task-declared AC checkpoint through the incumbent runtime.

    The Plan declares stable checkpoint inputs.  Submit owns the mutable
    repository identity and scope, while ``checkpoint`` remains the sole
    preflight and receipt authority.  This keeps command lifecycle behavior
    on the existing governed launch/wait path and prevents a task-authored
    mapping from masquerading as proof.
    """
    declaration = task.get("checkpoint")
    if not isinstance(declaration, Mapping):
        raise checkpoint.CheckpointSpecError(
            f"task {task.get('id') or '?'} checkpoint declaration must be "
            "a mapping")
    reserved = sorted(set(declaration) & {
        "schema", "worktree_revision", "declared_scope", "receipt",
        "producer", "result",
    })
    if reserved:
        raise checkpoint.CheckpointSpecError(
            "checkpoint declaration contains engine-owned fields: " +
            ", ".join(reserved))
    spec = {
        **dict(declaration),
        "schema": checkpoint.CHECKPOINT_SCHEMA,
        "worktree_revision": tp.git_head(act_ws),
        "declared_scope": list(task.get("scope") or []),
    }
    validated = checkpoint.validate_checkpoint_spec(act_ws, spec)
    checkpoint_id = validated["checkpoint_id"]
    authorization = "loop-submit-checkpoint:" + str(task.get("id") or "task")
    run_id = str(state.get("run_id") or state.get("requirement_id") or
                 "loop")
    try:
        checkpoint_authority = \
            governed_commands.mint_semantic_checkpoint_authorization(
                act_ws, lifecycle_authorization=authorization,
                run_id=run_id, task_id=str(task.get("id") or "task"))
    except governed_commands.GovernedCommandError as exc:
        raise checkpoint.CheckpointReceiptError(
            f"checkpoint {checkpoint_id} authorization refused: {exc}") \
            from exc
    # This semantic action accepts no argv/cwd/env/executable from the worker.
    # The governed-command engine reloads the current Plan task, derives the
    # exact validated checkpoint, and executes it outside the reviewed source.
    launched = governed_commands.execute(act_ws, "checkpoint", {
        "authorization": authorization,
        "checkpoint_authority": checkpoint_authority,
        "run_id": run_id,
        "task_id": str(task.get("id") or "task"),
    })
    if launched.get("error"):
        raise checkpoint.CheckpointReceiptError(
            f"checkpoint {checkpoint_id} runtime launch failed: " +
            str(launched["error"]))
    observed = governed_commands.execute(act_ws, "wait", {
        "authorization": authorization,
        "handle": launched["handle"],
        "consumer": "checkpoint:" + checkpoint_id,
    })
    if observed.get("error"):
        raise checkpoint.CheckpointReceiptError(
            f"checkpoint {checkpoint_id} runtime wait failed: " +
            str(observed["error"]))
    if (observed.get("snapshot") or {}).get("state") != "succeeded":
        # Preserve the checkpoint engine's typed red/timed-out/cancelled
        # verdict.  A green boundary receipt is intentionally unavailable for
        # a failed proof, but failure still needs the canonical checkpoint
        # diagnostic rather than a generic sidecar error.
        return checkpoint.validate_and_mint(act_ws, spec, observed)
    receipt = checkpoint.validate_and_mint(
        act_ws, spec, observed,
        semantic_authorization=authorization)
    return receipt


def submit(ws: str, outcome: str, note: str = "",
           task_id: str | None = None) -> dict:
    """Worker submission — evidence request, never a state transition.

    Trust boundary (L12, v2.2.1): "orchestrator-only gating" is a PROTOCOL
    guarantee, not a process-isolation one — any process with workspace
    access can call gate(). What holds mechanically is the EVIDENCE: a gate
    only advances when the fingerprinted submission matches the bytes on
    disk, so a worker gating itself still cannot pass unproven work. Gate
    calls are traced for after-the-fact attribution.

    The engine, not the worker, computes the changed paths and fingerprint.
    The orchestrator subsequently calls ``gate``; if anything changed between
    submission and validation, the gate rejects the stale evidence.  Repeating
    the same submission is idempotent, which makes interrupted/resumed drivers
    safe.

    A4 (decision 0018): the record additionally carries ``engine_fingerprint``
    — the identity of the ENGINE BUILD that produced it (tp.engine_fingerprint
    over the validator surface). Purely additive: older gates ignore the
    unknown key, and the evaluate gate uses it to refuse evidence produced by
    a different build than the one validating it.
    """
    if refusal := _stage_loop_mutation_refusal(ws):
        return refusal
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    step = state.get("step")
    if step not in ("execute", "fix", "evaluate", "em"):
        return {"error": f"step '{step}' is not a worker submission step — "
                         "run `loop next` to see the current role and "
                         "instruction; submissions happen at execute/fix/"
                         "evaluate/em"}
    if outcome not in ("pass", "fail", "unavailable"):
        return {"error": "submission outcome must be pass, fail, or unavailable"}
    if outcome == "unavailable" and step != "evaluate":
        return {"error": "unavailable is only valid for model evaluation; "
                         "product execution/fix/review still submit pass or fail"}

    task = _current_task(state)
    act_ws = ws
    parallel_execute = step == "execute" and state.get("parallel")
    if not parallel_execute and task_id and \
            task_id != (task or {}).get("id"):
        # H1 (v2.2.1): outside a parallel EXECUTE wave the engine evaluates
        # ONE current task — silently dropping a mismatched --task would
        # record this worker's evidence against a different task.
        return {"error": f"--task {task_id} does not match the current "
                         f"task '{(task or {}).get('id')}' at step "
                         f"'{step}' — a wave worker submits only during "
                         "parallel EXECUTE; otherwise omit --task or "
                         "pass the current task's id"}
    if parallel_execute:
        task = next((x for x in state.get("tasks") or []
                     if x.get("id") == task_id), None)
        if task is None:
            return {"error": "parallel submit needs --task <id> of a wave member"}
        act_ws = task.get("workspace") or ws
    elif step in ("evaluate", "fix") and state.get("parallel"):
        tws = (task or {}).get("workspace")
        act_ws = tws if tws and os.path.isdir(tws) else ws

    runtime_guidance = None
    checkpoint_receipt = None
    producer_observation = None
    if outcome == "pass":
        if step in {"evaluate", "em"}:
            try:
                producer_observation = (
                    _collect_zero_lens_evaluate_before_guidance(
                        ws, act_ws, state, task)
                    if step == "evaluate" else
                    _collect_zero_lens_evaluate_before_guidance(
                        ws, act_ws, state, task, step=step))
            except Exception as exc:
                return {
                    "error": f"runtime {step} producer collection failed: "
                             f"{exc.__class__.__name__}: {exc}",
                    "submitted": False, "transitioned": False,
                }
        runtime_guidance = runtime_eval.guide_loop(ws, task_id=task_id)
        if runtime_guidance.get("error"):
            return {"error": "runtime eval checkpoint failed: "
                             + runtime_guidance["error"],
                    "submitted": False, "transitioned": False,
                    "runtime_eval": runtime_guidance}
        if runtime_guidance.get("status") != "on_path":
            status = runtime_guidance.get("status")
            evidence_errors = []
            if step == "evaluate":
                verdict, read_errors = _read_json(
                    runtime_storage.evaluation_path(act_ws))
                evidence_errors = (read_errors or
                                   _acceptance_evidence_errors(
                                       act_ws, state, task, verdict))
            return {
                "error": ("runtime eval detected recoverable execution drift; "
                          "apply the supplied correction before pass submission"
                          if status == "correct" else
                          "runtime eval blocked repeated unresolved execution "
                          "drift; submit fail or return to the orchestrator"),
                "submitted": False, "transitioned": False,
                "runtime_eval": runtime_guidance,
                **({"dod": {"passed": False,
                            "errors": evidence_errors}}
                   if evidence_errors else {}),
            }
        if step in ("execute", "fix") and isinstance(
                (task or {}).get("checkpoint"), Mapping):
            try:
                checkpoint_receipt = _run_submit_checkpoint(
                    ws, state, task, act_ws)
            except checkpoint.CheckpointSpecError as exc:
                return {
                    "error": f"AC checkpoint refused: {exc}",
                    "submitted": False, "transitioned": False,
                    "runtime_eval": runtime_guidance,
                }

    snapshot = _worker_stage_snapshot(act_ws, step, task)
    evidence_paths = runtime_storage.submission_evidence_paths(act_ws, step)
    graph_fingerprint = None
    if state.get("graph_governance") and \
            (step == "em" or step == "evaluate" and not state.get("parallel")):
        graph_fingerprint = (depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
    evidence_engine_ws = _submission_evidence_engine_workspace(
        ws, state, task, act_ws)
    submission = {
        "step": step,
        "task": (task or {}).get("id"),
        "outcome": outcome,
        "note": note,
        "workspace": act_ws,
        "snapshot": snapshot,
        "fingerprint": tp.workspace_fingerprint(
            act_ws, snapshot, extra_paths=evidence_paths),
        "changed_files": (tp.changed_files(act_ws, snapshot)
                          if snapshot else []),
        "evidence_paths": evidence_paths,
        "graph_fingerprint": graph_fingerprint,
        "engine_fingerprint": tp.engine_fingerprint(),
        # A4 REPAIR (EM, v3 phase 3): engine_fingerprint attests the process
        # RUNNING submit — the same installed plugin the gate uses, so it
        # could never fire. Stamp the engine in the workspace the EVIDENCE
        # came from; None where that workspace carries no engine copy.
        "evidence_engine_fingerprint":
            tp.workspace_engine_fingerprint(evidence_engine_ws),
        "submitted_at": int(time.time()),
    }
    if checkpoint_receipt is not None:
        submission["checkpoint_receipt"] = checkpoint_receipt
    if isinstance(producer_observation, Mapping):
        submission["producer_observation"] = producer_observation
    with mutate(ws) as locked:
        if locked is None:
            return {"error": "no active loop"}
        def _same(existing):
            # Both engine fingerprints are part of the identity: a
            # re-submission under a different running engine OR after the
            # exact task tip moves under the primary evidence producer must
            # replace stale metadata, not be deduplicated into it (A4's
            # merge-and-resubmit remedy).
            return existing and all(
                existing.get(k) == submission.get(k)
                for k in ("step", "task", "outcome", "fingerprint",
                          "engine_fingerprint",
                          "evidence_engine_fingerprint"))
        if parallel_execute:
            target = next((x for x in locked.get("tasks") or []
                           if x.get("id") == task_id), None)
            if target is None:
                return {"error": f"no task {task_id}"}
            if _same(target.get("_submission")):
                submission = target["_submission"]
            else:
                target["_submission"] = submission
        else:
            if _same(locked.get("_submission")):
                submission = locked["_submission"]
            else:
                locked["_submission"] = submission
    telemetry_finalization = None
    if step == "execute" and submission.get("task"):
        try:
            telemetry_finalization = finalize_observed_dispatch_usage(
                ws, task_id=str(submission["task"]),
                ended_at=float(submission["submitted_at"]))
        except dispatch_telemetry.DispatchTelemetryError as exc:
            telemetry_finalization = {
                "status": "unavailable", "reason": str(exc)}
    tp.trace(ws, "loop_submit", step=step, task=submission.get("task"),
             outcome=outcome, fingerprint=submission["fingerprint"][:12])
    return {"submitted": True, "transitioned": False,
            **({"runtime_eval": runtime_guidance}
               if runtime_guidance is not None else {}),
            **({"dispatch_telemetry": telemetry_finalization}
               if telemetry_finalization is not None else {}),
            "submission": submission,
            "next": "orchestrator: run loop gate with the submitted outcome"}


def _submission_staleness(ws: str, submission: dict) -> str | None:
    """Recompute the engine-owned attestations for a pending submission."""
    sub_ws = submission.get("workspace") or ws
    current_fp = tp.workspace_fingerprint(
        sub_ws, submission.get("snapshot"),
        extra_paths=submission.get("evidence_paths") or [])
    if current_fp != submission.get("fingerprint"):
        return "workspace or evidence changed after worker submission"
    graph_fp = submission.get("graph_fingerprint")
    if graph_fp:
        current_graph_fp = (depgraph.load(ws).get("meta") or {}).get(
            "content_fingerprint")
        if current_graph_fp != graph_fp:
            return "dependency graph changed after worker submission"
    return None


def _producer_observation_errors(
        act_ws: str, state: dict, task: dict | None, step: str,
        submission: Mapping[str, object] | None, *, clock=None) -> list[str]:
    """Re-attest the durable, consumed native receipt at the final gate."""
    if _validated_delivery_mode(state) is None or step not in {"evaluate", "em"}:
        return []
    try:
        material = producer_output_identity(
            act_ws, state, task, step,
            active_contract=_worker_stage_contract(act_ws, step, task))
        producer_observation_policy.validate_consumed_matching_observation(
            (submission or {}).get("producer_observation"), **material,
            clock=clock)
    except Exception as exc:
        return ["producer observation validation failed: "
                f"{exc.__class__.__name__}: {exc}"]
    return []


def _stage_loop_gate_completion(
        ws: str, state: Mapping[str, object], *, step: str, outcome: str,
        note: str = "", submission: Mapping[str, object] | None = None,
        approval: Mapping[str, object] | None = None,
        target_commit: str | None = None) \
        -> dict:
    """Return the bounded result that the current gate actually validated."""
    task = _current_task(dict(state)) or {}
    source_workspace = str(
        (submission or {}).get("workspace") or task.get("workspace") or ws)
    sources = []
    values = {}
    result = {
        "schema": "taskplane.loop-gate-result/v1",
        "step": str(step), "outcome": str(outcome),
        "task_id": task.get("id"), "note": str(note or "")[:1024],
        "workspace_revision": tp.git_head(ws),
    }
    if isinstance(submission, Mapping):
        portable_submission = dict(submission)
        portable_submission.pop("workspace", None)
        portable_submission["evidence_paths"] = [
            f"evidence/{index:03d}-{os.path.basename(str(path))}"
            for index, path in enumerate(submission.get("evidence_paths") or [])
        ]
        result["submission"] = portable_submission
        for index, path in enumerate(submission.get("changed_files") or []):
            sources.append({"path": str(path), "required": False})
        for index, path in enumerate(submission.get("evidence_paths") or []):
            sources.append({
                "path": str(path), "required": True,
                "logical_path":
                    f"evidence/{index:03d}-{os.path.basename(str(path))}",
            })
    if isinstance(approval, Mapping):
        result["approval"] = dict(approval)
    if step == "pm":
        requirement_id = state.get("requirement_id")
        result["requirement_id"] = requirement_id
        spec_path = str(state.get("spec_path") or "specs/spec.md")
        result["spec_path"] = spec_path
        record = reqs.get_requirement(ws, requirement_id) \
            if requirement_id else None
        if record is not None:
            values["requirement"] = dict(record)
        sources.append({"path": spec_path,
                        "required": record is None})
    elif step in {"design", "design_approval"}:
        result["design_fingerprint"] = state.get("design_fingerprint") or \
            _design_evidence_fingerprint(ws)
        sources.extend([
            {"path": "design/design.md", "required": True},
            {"path": "design/contract.json", "required": True},
        ])
    elif step in {"plan", "plan_approval"}:
        result["tasks"] = [str(row.get("id")) for row in
                           (state.get("tasks") or []) if row.get("id")]
        result["graph_dor"] = state.get("graph_dor")
        sources.extend([
            {"path": "plan/plan.md", "required": True},
            {"path": "plan/tasks.json", "required": True},
        ])
    elif step in {"em", "signoff"}:
        signoff = state.get("signoff_evidence")
        result["signoff_fingerprint"] = (
            review_session_engine.signoff_evidence_fingerprint(signoff)
            if isinstance(signoff, Mapping) and hasattr(
                review_session_engine, "signoff_evidence_fingerprint")
            else None)
    build_commit = str(target_commit or task.get("target_commit") or "")
    if step in {"execute", "fix"} and not any(
            source.get("logical_path") is None
            for source in sources if isinstance(source, Mapping)):
        for path in _diff_files(
                source_workspace, build_commit or
                str((submission or {}).get("snapshot") or "HEAD")):
            sources.append({"path": path, "required": False})
    result["_stage_output"] = {
        "source_workspace": source_workspace,
        "sources": sources, "values": values,
        **({"managed_evidence_step": str(step)}
           if isinstance(submission, Mapping) and
           submission.get("evidence_paths") and step in {"evaluate", "em"}
           else {}),
        **({"build": {"target_commit": build_commit}}
           if step in {"execute", "fix"} else {}),
    }
    return result


def gate(ws: str, outcome: str, note: str = "", task_id: str | None = None,
         rid: str | None = None) -> dict:
    """Record the current step's outcome, transition, and clear its contract."""
    if refusal := _stage_loop_mutation_refusal(ws):
        return refusal
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    # v2.3.0 wiring: `--req R-xxxx` attaches a requirement to the in-flight
    # loop through the SANCTIONED validator (design_contract.design_attach_
    # requirement) — it validates exactly what the design DoR demands and
    # refuses to swap an anchored requirement; nothing downstream is skipped.
    if rid:
        attach_errors: list = []
        with mutate(ws) as st:
            if st is None:
                return {"error": "no active loop"}
            attach_errors = _dc.design_attach_requirement(ws, st, rid)
        if attach_errors:
            return {"error": "requirement attach failed — the gate was not "
                             "evaluated", "blockers": attach_errors}
        state = load(ws)
    step = state["step"]
    # Preserve the same stage/task identity that `next_action` used before
    # gate validation mutates state (the Plan gate loads tasks, for example).
    # Recomputing after that load changes `plan` into the first task id and
    # strands the planner's exact worker slot.
    gate_worker_task = str((_current_task(state) or {}).get("id") or step)
    submission = None
    reanchor_receipt = None

    # v2.3.0: validate --task FIRST in a parallel wave. An unknown id used to
    # fall through to "worker evidence was not submitted", telling the driver
    # to submit for a task that does not exist (mirrors H1's submit-side
    # validation).
    if step == "execute" and state.get("parallel"):
        members = [str(x.get("id")) for x in state.get("tasks") or []]
        if not task_id:
            return {"error": "parallel gate needs --task <id> of a wave "
                             "member", "step": step}
        if task_id not in members:
            return {"error": f"unknown task id '{task_id}' — wave members: "
                             + ", ".join(members), "step": step}

    if state.get("submission_required") and step in \
            ("execute", "fix", "evaluate", "em"):
        task_for_submission = (_current_task(state) if step != "execute"
                               or not state.get("parallel") else
                               next((x for x in state.get("tasks") or []
                                     if x.get("id") == task_id), None))
        submission = ((task_for_submission or {}).get("_submission")
                      if step == "execute" and state.get("parallel") else
                      state.get("_submission"))
        if not submission:
            return {"error": "worker evidence was not submitted — the worker "
                             "must run `loop submit pass|fail|unavailable`; only the "
                             "orchestrator may evaluate `loop gate`",
                    "step": step}
        if submission.get("step") != step or submission.get("outcome") != outcome:
            return {"error": "gate request does not match the worker submission",
                    "step": step, "submission": submission}
        stale = _submission_staleness(ws, submission)
        if stale:
            return {"error": stale + " — discard stale evidence and submit again",
                    "step": step}

    # Parallel EXECUTE: a wave worker reports its own task's build outcome.
    # Concurrent workers gate against the SAME loop.json — serialize the whole
    # read-modify-write under an exclusive lock so a second worker's save
    # can't clobber the first's status update (which would revert a gated task
    # to running and stall the wave).
    if step == "execute" and state.get("parallel"):
        wt_precheck = next((x for x in state.get("tasks") or []
                            if x["id"] == task_id), None)
        if wt_precheck is None:
            return {"error": "parallel gate needs --task <id> of a wave "
                             "member"}
        # Fail closed: an uncommitted worktree means the branch carries
        # NOTHING — the merge would be empty and worktree removal would
        # destroy the work. Commit first, then gate.
        wt = wt_precheck.get("workspace")
        if outcome == "pass":
            with _claimed_execute_suite_binding():
                dod_errors = _task_dod_errors(
                    wt or ws, state, wt_precheck,
                    _worker_stage_snapshot(wt or ws, step, wt_precheck))
            if dod_errors:
                tp.trace(ws, "loop_gate_blocked", step=step, task=task_id,
                         reason="dod", errors=dod_errors)
                return {"error": "Definition of Done failed — task remains "
                                 "running", "dod": {"passed": False,
                                 "errors": dod_errors}}
        if outcome == "pass" and wt and os.path.isdir(wt) and tp.is_dirty(wt):
            return {"error": f"task {task_id}: uncommitted work in {wt} — "
                             "the tp/<task> branch carries nothing yet. "
                             "`git add -A && git commit` in the worktree, "
                             "then gate again."}
        with mutate(ws) as locked:
            t = next((x for x in (locked.get("tasks") or [])
                      if x["id"] == task_id), None)
            if t is None:
                return {"error": "parallel gate needs --task <id> of a wave "
                                 "member"}
            # v2.3.0: the final staleness re-attest runs INSIDE the lock,
            # immediately before the status commits — no TOCTOU window
            # between the attest and the transition.
            if state.get("submission_required"):
                stale = _submission_staleness(ws, submission)
                if stale:
                    return {"error": stale + " during gate validation — "
                                     "submit the final state again",
                            "step": step}
            prepared_registration = None
            if outcome == "pass":
                try:
                    prepared_registration = \
                        runtime_storage.refresh_task_worktree_tip(
                            ws, str(task_id))
                except runtime_storage.StorageIdentityError as exc:
                    return {"error": f"task {task_id}: managed worktree "
                                     f"target binding failed: {exc}",
                            "step": step}
            t["status"] = "built"
            if prepared_registration is not None:
                t["target_commit"] = prepared_registration["branch_tip"]
            verified_suite = ((state.get("_validated_suite_evidence") or {})
                              .get(t["id"]))
            if verified_suite:
                locked.setdefault("_suite_evidence", {})[t["id"]] = \
                    verified_suite
            t.pop("_submission", None)
            if outcome != "pass":
                t["_build_failed"] = True
            release_ws = t.get("workspace") or ws
            released_contracts = tp.release_worker_contracts_for_gate(
                release_ws, stage=step, task=str(task_id))
            if not released_contracts:
                tp.clear(release_ws)  # legacy pre-lifecycle run
            tp.trace(ws, "loop_gate", step=step, task=task_id, outcome=outcome,
                     note=note)
            running = [x["id"] for x in locked["tasks"]
                       if x.get("status") == "running"]
        return {"step": "execute", "task": task_id, "built": True,
                "still_running": running, "status": status(ws)}

    # H4 (v2.2.1): the pm gate was the one fail-open step — it advanced with
    # no spec and no submission. Symmetric minimal DoD: the authored
    # requirement must exist before the loop leaves Define.
    if step == "pm":
        if outcome != "pass":
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note=note or "pm rejected — staying at pm")
            return {"error": "pm gate: outcome was not 'pass' — refine the "
                             "requirement/spec, then gate again",
                    "step": "pm", "status": status(ws)}
        spec_rel = state.get("spec_path") or os.path.join("specs", "spec.md")
        spec_abs = spec_rel if os.path.isabs(spec_rel) \
            else os.path.join(ws, spec_rel)
        has_req = bool(state.get("requirement_id"))
        if not has_req and not (os.path.isfile(spec_abs)
                                and os.path.getsize(spec_abs) > 0):
            tp.trace(ws, "loop_gate_blocked", step=step, reason="dod",
                     errors=["no spec"])
            return {"error": "pm Definition of Done failed — no requirement "
                             "was authored. Write a non-empty specs/spec.md "
                             "(or record a requirement with `tp req new` and "
                             "attach its R-id with `tp loop gate pass --req "
                             "R-XXXX`), then gate again.",
                    "step": "pm",
                    "dod": {"passed": False,
                            "errors": [f"{spec_rel} missing or empty and no "
                                       "requirement_id is attached — the pm "
                                       "step authors the WHAT before the "
                                       "loop advances"]}}
        if has_req:
            rec = reqs.get_requirement(ws, state["requirement_id"])
            product_dor = reqs.product_dor(rec)
            refinement = product_dor["refinement"]
            if not product_dor["passed"]:
                errors = ([f"requirement {state['requirement_id']} is missing"]
                          if not rec else product_dor["errors"])
                tp.trace(ws, "loop_gate_blocked", step=step,
                         reason="requirement_dor", errors=errors)
                return {"error": "pm Definition of Ready failed — refine the "
                                 "same requirement before planning",
                        "step": "pm",
                        "dor": {"passed": False, "errors": errors,
                                "refinement": refinement}}
            # Dependencies and named contracts are recorded by req new. The
            # context-file ownership edge is equally mechanical and belongs
            # at this gate, not in a second model-authored graph command.
            context_files = list(rec.get("context_files") or [])
            if context_files:
                depgraph.link_requirement(
                    ws, rec["id"], context_files,
                    kind="planned", replace=True)
            state["requirement_refinement"] = refinement
            # Product owns refinement, including the optional strategic note.
            # The note is recorded as advisory evidence and cannot create a
            # standalone user gate or override the canonical Product DoR.
            product_evidence = dict(rec)
            product_evidence.setdefault("score", refinement.get("score", 1)
                                        if isinstance(refinement, dict) else 1)
            state["product_definition"] = _product_definition_gate(
                product_evidence)

    # Validate the proposed HOW while its read-only contract is active. The
    # designer cannot self-certify or mutate the as-built graph; a complete
    # contract advances only to the human approval gate.
    if step == "design":
        if outcome != "pass":
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note=note or "design rejected — staying at design")
            return {"error": "design gate: outcome was not 'pass' — revise "
                             "design/design.md and design/contract.json, then "
                             "gate again", "step": "design",
                    "status": status(ws)}
        design_errors = _design_dod_errors(ws, state)
        if design_errors:
            tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="design_dod", errors=design_errors)
            return {"error": "Design Definition of Done failed — revise the "
                             "Design Contract before approval",
                    "step": "design",
                    "dod": {"passed": False, "errors": design_errors}}

    # Validate the implementation-ready plan while its read-only contract is
    # still active. A rejected plan remains governed for the planner's retry.
    if step == "plan":
        _load_tasks(ws, state)
        if outcome != "pass":
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note=note or "plan rejected — staying at plan")
            return {"error": "plan gate: outcome was not 'pass' — the plan "
                             "was rejected. Revise plan/tasks.json (+ "
                             "plan/plan.md) and gate again; the loop stays at "
                             "the plan step.",
                    "step": "plan", "status": status(ws)}
        if not state.get("tasks"):
            tp.trace(ws, "loop_gate", step=step, outcome="rejected",
                     note="phantom plan: plan/tasks.json missing or empty")
            return {"error": "plan gate: plan/tasks.json is missing or has "
                             "no tasks — the plan exists only as words. "
                             "Write plan/tasks.json (+ plan/plan.md for the "
                             "human), then gate again."}
        dor_errors = _retained_production_authority_errors(ws)
        dor_errors.extend(_plan_dor_errors(ws, state, apply=True))
        if dor_errors:
            tp.trace(ws, "loop_gate_blocked", step=step, reason="dor",
                     errors=dor_errors)
            return {"error": "Definition of Ready failed — revise "
                             "plan/tasks.json before approval or execution",
                    "step": "plan",
                    "dor": {"ready": False, "blockers": dor_errors}}
        reanchor_receipt, reanchor_errors = _reanchor_replanned_tasks(
            ws, state)
        if reanchor_errors:
            tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="replan_reanchor_ambiguous",
                     errors=reanchor_errors)
            return {"error": "replan reanchor failed closed — repair the "
                             "append-only replan evidence before Plan "
                             "acceptance",
                    "step": "plan",
                    "dor": {"ready": False,
                            "blockers": reanchor_errors}}
        # B2: ordering at the GATE too — checkpoint-less loops skip approve.
        if (refusal := tp.plan_ordering_refusal(ws, state.get("tasks"),
                                                "gate")):
            return refusal

    task = _current_task(state)
    gated_task_id = str((task or {}).get("id") or "")
    act_ws = ws
    if step in ("evaluate", "fix") and state.get("parallel"):
        tws = (task or {}).get("workspace")
        act_ws = tws if tws and os.path.isdir(tws) else ws

    unavailable_verdict = None
    evaluation_progress = None

    # A reported PASS is a request to evaluate the gate. Evidence, not the
    # agent's assertion, determines whether the state machine advances.
    if outcome == "pass" and step in ("execute", "fix"):
        with _claimed_execute_suite_binding():
            dod_errors = _task_dod_errors(
                act_ws, state, task,
                _worker_stage_snapshot(act_ws, step, task))
        if dod_errors:
            tp.trace(ws, "loop_gate_blocked", step=step, reason="dod",
                     errors=dod_errors)
            return {"error": "Definition of Done failed — step did not "
                             "advance", "step": step,
                    "dod": {"passed": False, "errors": dod_errors}}
    if outcome == "pass" and step == "evaluate":
        # A4: the engine that PRODUCED this evidence vs the one about to
        # judge it — a pure pre-check (decision 0018), so equal engines
        # leave the walk below byte-unchanged.
        if (skew := tp.engine_skew_refusal(ws, state.get("_submission"))):
            return skew
        evidence_errors = _producer_observation_errors(
            act_ws, state, task, step, state.get("_submission"))
        evidence_errors.extend(_evaluation_errors(act_ws, state, task))
        if evidence_errors:
            tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="evaluation_evidence", errors=evidence_errors)
            return {"error": "evaluation evidence failed — step did not "
                             "advance", "step": step,
                    "dod": {"passed": False, "errors": evidence_errors}}
    if outcome == "unavailable" and step == "evaluate":
        if (skew := tp.engine_skew_refusal(ws, state.get("_submission"))):
            return skew
        unavailable_errors, unavailable_verdict = \
            _evaluation_unavailable_errors(act_ws, state, task)
        if unavailable_errors:
            tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="invalid_evaluation_unavailability",
                     errors=unavailable_errors)
            return {"error": "evaluation unavailability was not proven — "
                             "step did not advance", "step": step,
                    "dod": {"passed": False,
                            "errors": unavailable_errors}}
    if outcome == "fail" and step == "evaluate":
        unavailable_errors, _ = _evaluation_unavailable_errors(
            act_ws, state, task)
        if not unavailable_errors:
            return {"error": "evaluation infrastructure is unavailable, not "
                             "a product defect — gate unavailable; no FIX "
                             "cycle was opened", "step": step}
        try:
            evaluation_progress = _canonical_evaluation_progress(
                act_ws, state, task)
        except Exception as exc:  # legacy/incomplete runs retain old fallback
            tp.trace(ws, "review_convergence_unavailable",
                     task=(task or {}).get("id"),
                     error=f"{exc.__class__.__name__}: {exc}")
    signoff_evidence = None
    if outcome == "pass" and step == "em":
        signoff_errors = _producer_observation_errors(
            ws, state, task, step, state.get("_submission"))
        signoff_evidence, binding_errors = _signoff_evidence_binding(ws, state)
        signoff_errors.extend(binding_errors)
        if signoff_errors:
            tp.trace(ws, "loop_gate_blocked", step=step,
                     reason="terminal_signoff_evidence",
                     errors=signoff_errors)
            return {"error": "engineering review is incomplete or terminal "
                             "sign-off evidence failed — the loop remains "
                             "at engineering review",
                    "step": step,
                    "dod": {"passed": False, "errors": signoff_errors}}
    em_request_changes = None
    if outcome == "fail" and step == "em":
        review_submission = state.get("_submission") or {}
        em_request_changes = {
            "schema": "taskplane.engineering-review-request-changes/v1",
            "submission": dict(review_submission),
        }

    # H2 (v2.2.1): validation above ran on a snapshot and can take seconds
    # (tests, evidence, graph). Apply the transition under the state LOCK to
    # a FRESH read, so a wave worker's concurrent update to another task is
    # never clobbered by saving this stale snapshot wholesale. Fields the
    # VALIDATION itself computed on the snapshot (loaded plan tasks, graph
    # DoR) are carried over explicitly.
    _validated = state
    stage_transition = None
    with mutate(ws) as state:
        if state is None:
            return {"error": "no active loop"}
        if state.get("step") != step:
            return {"error": f"loop advanced to '{state.get('step')}' while "
                             "this gate was validating — run loop next and "
                             "gate again", "step": state.get("step")}
        stage_state_before = json.loads(json.dumps(state))
        # v2.3.0: the final staleness re-attest runs INSIDE the state lock,
        # immediately before the transition commits — the old pre-lock check
        # left a TOCTOU window in which a workspace edit got blessed by a
        # gate whose evidence was attested against different bytes. (The
        # contract is cleared AFTER the locked transition, below, so a
        # refused gate also leaves the workspace governed.)
        if _validated.get("submission_required") and step in \
                ("execute", "fix", "evaluate", "em"):
            stale = _submission_staleness(ws, submission)
            if stale:
                return {"error": stale + " during gate validation — submit "
                                 "the final state again", "step": step}
        if outcome == "pass" and step == "evaluate":
            current = _current_task(state)
            try:
                authority_ref, source_revision = \
                    _persist_reanchor_authority(
                        act_ws, current, "independent-pass")
            except Exception as exc:
                return {"error": "evaluation pass authority could not be "
                                 "persisted fail-closed: "
                                 f"{exc.__class__.__name__}: {exc}",
                        "step": step}
            current["workspace"] = os.path.realpath(act_ws)
            current["target_commit"] = source_revision
            current["reanchor_authority"] = authority_ref
        refreshed_fix_registration = None
        if outcome == "pass" and step == "fix" and state.get("parallel"):
            current = _current_task(state)
            try:
                refreshed_fix_registration = \
                    runtime_storage.refresh_task_worktree_tip(
                        ws, str((current or {}).get("id") or ""))
            except runtime_storage.StorageIdentityError as exc:
                return {"error": "fix gate could not bind the repaired "
                                 f"managed-worktree target: {exc}",
                        "step": step}
            current["target_commit"] = \
                refreshed_fix_registration["branch_tip"]
        if _validated.get("tasks") and not state.get("tasks"):
            state["tasks"] = _validated["tasks"]
        if step == "plan":
            # plan validation recomputed these on the snapshot via
            # _load_tasks: loaded tasks, the ab flag, the round-scoped
            # selection reset, and the graph DoR verdict.
            if _validated.get("tasks"):
                state["tasks"] = _validated["tasks"]
            if "ab" in _validated:
                state["ab"] = _validated["ab"]
            if "parallel" in _validated:
                state["parallel"] = _validated["parallel"]
            if "selection" not in _validated:
                state.pop("selection", None)
            if "graph_dor" in _validated:
                state["graph_dor"] = _validated["graph_dor"]
            # Plan DoR validates and seals the delivery declaration on the
            # unlocked snapshot.  The locked transition must carry those
            # exact validated bytes forward just like tasks and graph_dor;
            # otherwise the fresh read silently severs Plan authority from
            # the first Build dispatch.  Revalidate before copying so this
            # bridge cannot become a fallback or a receipt-forging seam.
            if "delivery_mode_receipt" in _validated:
                validated_delivery_receipt = \
                    delivery_policy.validate_delivery_mode_receipt(
                        _validated["delivery_mode_receipt"])
                if validated_delivery_receipt != \
                        _validated["delivery_mode_receipt"]:
                    return {
                        "error": "Plan delivery-mode receipt normalization "
                                 "changed during locked transition",
                        "step": step,
                    }
                state["delivery_mode_receipt"] = json.loads(json.dumps(
                    _validated["delivery_mode_receipt"]))
            for field in ("replan_reanchor", "replan_reanchor_history"):
                if field in _validated:
                    state[field] = json.loads(json.dumps(_validated[field]))
        elif "design_graph_fingerprint" in _validated and \
                "design_graph_fingerprint" not in state:
            state["design_graph_fingerprint"] = \
                _validated["design_graph_fingerprint"]
        completion_state = json.loads(json.dumps(state))
        if signoff_evidence is not None:
            completion_state["signoff_evidence"] = signoff_evidence
        completion = _stage_loop_gate_completion(
            ws, completion_state, step=step, outcome=outcome, note=note,
            submission=submission,
            target_commit=((refreshed_fix_registration or {}).get(
                "branch_tip")))
        state.pop("_submission", None)
        if step == "pm":
            if "requirement_refinement" in _validated:
                state["requirement_refinement"] = \
                    _validated["requirement_refinement"]
            state["step"] = ("design" if state.get("design_required") else "plan")
        elif step == "design":
            if _consolidated_enabled():
                contract, _ = _design_contract(ws)
                state["design_fingerprint"] = _design_evidence_fingerprint(
                    ws, contract)
                state["design_approved_by"] = "mechanical-definition-gate"
                _record_design_contracts(ws, state, contract)
                state["step"] = ("design_approval" if state.get("design_only")
                                 else "plan")
                tp.trace(ws, "mechanical_gate", gate="design",
                         outcome="pass", human_required=False)
            else:
                state["step"] = "design_approval"
        elif step == "plan":
            # Product↔engineering graph, PLANNED side: link each task's
            # requirement to the modules its scope intends to touch, then
            # annotate the task with its blast radius (engineering) and any
            # OTHER requirements whose surface it overlaps (product). The
            # human approves the plan seeing both; the executor's contract
            # briefing carries them; evaluation compares against them later.
            _annotate_plan_graph(ws, state)
            derivation = _derive_consolidated_authority(ws, state, "execute")
            if derivation and derivation.get("authorized"):
                state["authority_derivations"] = {
                    **(state.get("authority_derivations") or {}),
                    "execute": derivation,
                }
                state["step"] = "execute"
                tp.trace(ws, "mechanical_gate", gate="plan",
                         outcome="pass", human_required=False,
                         authority=derivation.get("fingerprint"))
            else:
                state["step"] = ("plan_approval"
                                 if "plan" in state["checkpoints"]
                                 else "execute")
            resume_at = _first_unsettled_task_index(state)
            state["current_task"] = resume_at if resume_at is not None else 0
            if state["step"] == "execute" and resume_at is None:
                state["step"] = "em"
            if state["step"] in ("execute", "em"):
                state["baseline"] = tp.git_head(ws)
        elif step == "execute":
            # a build always goes to evaluate; a FAILED build is flagged so
            # evaluate FAILs and routes to fix/escalate — one place owns the fail
            # policy (so the step transition itself is unconditional).
            state["step"] = "evaluate"
            verified_suite = ((_validated.get(
                "_validated_suite_evidence") or {}).get(
                    _current_task(state)["id"]))
            if verified_suite:
                current = _current_task(state)
                state.setdefault("_suite_evidence", {})[current["id"]] = \
                    verified_suite
            if outcome != "pass":
                state["_build_failed"] = True
        elif step == "evaluate":
            t = _current_task(state)
            build_failed = state.pop("_build_failed", False) or \
                t.pop("_build_failed", False)
            if outcome == "unavailable" and not build_failed:
                availability = dict(
                    (unavailable_verdict or {}).get("evaluation") or {})
                identity = evaluator_health.outage_identity(
                    task=str(t.get("id") or ""),
                    requirement=str((unavailable_verdict or {}).get(
                        "requirement") or ""),
                    evaluation=availability,
                    failures=list((unavailable_verdict or {}).get(
                        "failures") or []))
                warning = {
                    "task": t.get("id"),
                    "status": "unavailable",
                    "verdict": "non-judged",
                    "reason_code": availability.get("reason_code"),
                    "detail": str(availability.get("detail") or "")[:500],
                    "outage_identity": identity,
                }
                t["status"] = "unavailable"
                t["evaluation"] = warning
                warnings = state.setdefault("evaluation_warnings", [])
                warnings[:] = [row for row in warnings
                               if row.get("task") != t.get("id")]
                warnings.append(warning)
                # Infrastructure could not provide the independent judgment
                # required for readiness.  Keep the task unsettled and pause
                # at the existing governed recovery boundary; only an
                # attributed human retry/skip/defer/abort can move it.
                state["step"] = "escalated"
            elif outcome == "pass" and not build_failed:
                t["status"] = "passed"
                # After the LAST task: A/B loops pause at the human SELECTION
                # gate (variants never merge — one gets picked) — but only
                # ONCE; a post-selection fix cycle goes back to the review.
                after_last = ("selection" if state.get("ab")
                              and not state.get("selection") else "em")
                if state.get("parallel"):
                    # merge is the driver's job (instruction), state just moves on
                    if all(x.get("status") in SETTLED
                           for x in state["tasks"]):
                        state["step"] = after_last
                    else:
                        state["step"] = "execute"   # next wave / next built task
                else:
                    # serial: advance to the next UNSETTLED task, skipping any the
                    # skip-cascade already closed (else a dependency-failed task
                    # gets silently built and shipped).
                    nxt = _next_unsettled_index(state, state["current_task"])
                    if nxt is not None:
                        state["current_task"] = nxt
                        state["step"] = "execute"
                    else:
                        state["step"] = after_last
            else:
                t["fix_cycles"] = t.get("fix_cycles", 0) + 1
                if isinstance(evaluation_progress, dict):
                    import review_convergence

                    previous = t.get("convergence_revision")
                    history = list(t.get("convergence_history") or [])
                    if isinstance(previous, dict):
                        closed = {
                            finding for row in history if isinstance(row, dict)
                            for finding in ((row.get("findings") or {}).get(
                                "closed") or [])}
                        boundaries = t.get("convergence_boundaries") or {}
                        decision = review_convergence.evaluate_fix_cycle(
                            previous, evaluation_progress,
                            cycle=t["fix_cycles"], previously_closed=closed,
                            history=history,
                            max_cycles=t.get("max_fix_cycles"),
                            human_stop=boundaries.get("human_stop") is True,
                            unsafe_recovery=(
                                boundaries.get("unsafe_recovery") is True),
                            scope_changed=(
                                previous.get("scope_fingerprint") !=
                                evaluation_progress.get("scope_fingerprint") or
                                boundaries.get("scope_changed") is True),
                            authority_changed=(
                                previous.get("authority_fingerprint") !=
                                evaluation_progress.get("authority_fingerprint") or
                                boundaries.get("authority_changed") is True))
                    else:
                        # The first failed canonical evaluation establishes the
                        # comparison baseline and opens one bounded fix.
                        baseline = review_convergence.evaluate_fix_cycle(
                            evaluation_progress, evaluation_progress,
                            cycle=t["fix_cycles"])
                        decision = dict(
                            baseline, decision="continue",
                            reason="canonical_baseline_established")
                    history.append(decision)
                    t["convergence_history"] = history
                    t["convergence_revision"] = evaluation_progress
                    tp.trace(ws, "review_convergence_decision",
                             task=t.get("id"), cycle=t["fix_cycles"],
                             decision=decision["decision"],
                             reason=decision["reason"])
                    if decision["decision"] == "continue":
                        state["step"] = "fix"
                    else:
                        t["status"] = "failed"
                        state["step"] = "escalated"
                elif t["fix_cycles"] <= state["max_fix_cycles"]:
                    # Compatibility for old persisted runs that predate the
                    # canonical review revision required by convergence v1.
                    state["step"] = "fix"
                else:
                    t["status"] = "failed"
                    state["step"] = "escalated"
        elif step == "fix":
            verified_suite = ((_validated.get(
                "_validated_suite_evidence") or {}).get(
                    _current_task(state)["id"]))
            if verified_suite:
                current = _current_task(state)
                state.setdefault("_suite_evidence", {})[current["id"]] = \
                    verified_suite
            state["step"] = "evaluate"
        elif step == "em":
            if outcome == "pass":
                # The graph was true-d up before the EM brief, so its
                # fingerprint is part of the evidence being gated rather
                # than a post-review mutation.
                state["signoff_evidence"] = signoff_evidence
                state["signoff_dod"] = dict(signoff_evidence["dod"])
                state["step"] = "signoff"
            else:
                # Request-changes evidence stays bound to the reviewed
                # snapshot while the existing escalation/replan machinery
                # owns recovery.  Pass-only sign-off evidence is untouched.
                state["engineering_review_request_changes"] = \
                    em_request_changes
                state["step"] = "escalated"
        # Build/Fix/Evaluate task state is governed here, while Codex owns the
        # native agent tree.  Do not mirror those task transitions into a
        # second StageLifecycle hierarchy or execution-root authority.
        if step not in {"execute", "evaluate", "fix"}:
            try:
                stage_transition = _stage_loop_transition(
                    ws, state, from_step=step, to_step=state["step"],
                    completion=completion)
            except Exception as exc:
                state.clear()
                state.update(stage_state_before)
                return {"error": "stage-native loop transition failed closed: "
                        f"{exc.__class__.__name__}: {exc}", "step": step}
    if step == "em" and outcome == "pass":
        # One more COMPLETED engineering review: advance the audit cadence
        # (every Nth em review runs as a full audit sweep). A cadence-store
        # failure is traced, never allowed to block a validated sign-off.
        try:
            reviews = record_audit_review(ws)
            tp.trace(ws, "audit_review_recorded", reviews=reviews,
                     next_audit_due=audit_due(ws, state))
        except Exception as exc:      # noqa: BLE001
            tp.trace(ws, "audit_counter_failed", error=str(exc))
    # Release the step's contract only AFTER the locked transition committed
    # (v2.3.0): clearing before the lock left the workspace ungoverned during
    # the commit window; a refused gate above leaves it governed for retry.
    release_task = gate_worker_task
    released_contracts = tp.release_worker_contracts_for_gate(
        act_ws, stage=step, task=release_task)
    if not released_contracts:
        tp.clear(act_ws)  # legacy pre-lifecycle run
    cleanup_result = None
    cleanup_task = (_current_task(state) if isinstance(state, dict) else None)
    if step == "evaluate" and outcome == "pass" and state.get("parallel") \
            and cleanup_task is not None and \
            cleanup_task.get("status") == "passed":
        cleanup_result = _automatic_merge_cleanup(ws, cleanup_task)
        # The helper may have moved the loop to escalation when the merge
        # could not produce a durable receipt. Return the committed truth.
        state = load(ws) or state
    yield_meter.gate_snapshot(ws, step, outcome)   # records, never gates
    tp.trace(ws, "loop_gate", step=step, task=gated_task_id,
             outcome=outcome, note=note,
             **({"reason": ((unavailable_verdict or {}).get("evaluation")
                             or {}).get("reason_code")}
                if outcome == "unavailable" else {}))
    if reanchor_receipt is not None:
        tp.trace(ws, "loop_replan_reanchored",
                 restored=reanchor_receipt["restored_count"],
                 pending=reanchor_receipt["pending_count"],
                 tasks=[row["task_id"]
                        for row in reanchor_receipt["restored"]],
                 fingerprint=reanchor_receipt["fingerprint"])
    return {"step": state["step"], "status": status(ws),
            **({"stage_transition": stage_transition}
               if stage_transition is not None else {}),
            **({"worktree_cleanup": cleanup_result}
               if cleanup_result is not None else {}),
            **({"warning": (state.get("evaluation_warnings") or [])[-1]}
               if outcome == "unavailable" else {}),
            **({"reanchor": reanchor_receipt}
               if reanchor_receipt is not None else {}),
            }


def _compute_signoff_dod(ws: str, state: dict) -> dict:
    """Mechanical final DoD over aggregate scope, requirements, tests, graph,
    engineering evidence, and committed knowledge. Human sign-off remains."""
    scopes: list = []
    for t in (state.get("tasks") or []):
        scopes.extend(t.get("scope") or [])
    baseline = state.get("baseline")
    errors: list = []
    notices: list = []
    errors.extend("requirement DoD: " + e for e in tp.requirement_coverage_errors(
        state.get("tasks") or [], lambda rid: reqs.get_requirement(ws, rid),
        state.get("requirement_id"), require_passed=True))
    if scopes:
        # Aggregate diff-scope, EXCLUDING loop-owned artifacts: they are
        # authored by governed steps under their own write-allow contracts
        # and human gates, so requiring them inside the union of TASK
        # scopes was a contradiction. Every other engine path (evaluate
        # routing, em review, impact, and — A2 — the per-task DoD) filters
        # lens_router.LOOP_OWNED the same way. Fail-closed stance
        # unchanged: no snapshot still errors, and NON-loop-owned files
        # outside the union still block.
        if not baseline:
            errors.append("diff_scope: cannot verify — no git snapshot "
                          "(commit the workspace before governing)")
        else:
            # plan_minted: the union IS the human-approved plan's scopes
            # (approved wildcard-free literals keep their provenance-gated
            # override); DEFAULT_OUT_OF_SCOPE here is STRICTER than the
            # old synthetic contract, which had no out_of_scope at all.
            coding = {"scope_paths": scopes,
                      "out_of_scope_paths": list(tp.DEFAULT_OUT_OF_SCOPE),
                      "plan_minted": True}
            for f in tp.changed_files(ws, baseline):
                if f.startswith(lens_router.LOOP_OWNED):
                    continue
                v = tp.scope_violation(f, coding)
                if v:
                    errors.append("diff_scope: " + v)
            if errors:
                errors.append(
                    "diff_scope recovery: revert the out-of-scope files or "
                    "widen the owning task's scope via the human gate "
                    "(attributable: trace + KB decision), then re-run")
    if state.get("graph_governance"):
        try:
            depgraph.scan(ws)
        except Exception as exc:
            errors.append(f"graph_dod: final merged-tree scan failed: {exc}")
    for task in state.get("tasks") or []:
        test_command = task.get("tests")
        if not test_command:
            errors.append(f"task {task.get('id', '?')}: test command missing")
            continue
        test_contract = tp.build_contract(
            f"SIGNOFF TEST: {task.get('id', '?')}",
            scope=task.get("scope"), test_command=test_command,
            plan_minted=True, regression_gate=True)
        # Aggregate scope is already checked; run each task's scoped evidence.
        test_contract["coding"]["dod"]["require_clean_scope_diff"] = False
        regression_files = [f for f in (tp.changed_files(ws, baseline)
                                        if baseline else [])
                     if tp.match_any(f, task.get("scope") or [])]
        task_notices: list = []
        errors.extend(f"task {task.get('id', '?')}: {e}" for e in tp.dod_check(
            test_contract, ws, baseline, regression_files=regression_files,
            notices=task_notices))
        notices.extend(f"task {task.get('id', '?')}: {n}"
                       for n in task_notices)
    errors.extend(_engineering_review_errors(ws, state))
    for problem in kb.lint(ws):
        errors.append("kb_lint: " + (problem.get("file", "?")) + " — "
                      + problem.get("problem", ""))
    # D-0008: sign-off is the human's decision point. A `tests_pass` that was
    # CITED rather than executed is a fact about the evidence being signed
    # for, so it travels with the verdict instead of only into the trace.
    return {"passed": not errors, "errors": errors, "notices": notices,
            "scope": scopes, "baseline": baseline}


def _signoff_evidence_binding(ws: str, state: dict) -> tuple[dict | None, list]:
    """Seal final DoD and review identity at the integrated revision.

    Sign-off is a human decision over the EM-reviewed tree, not a fresh review
    of whichever bytes happen to occupy the shared checkout later.  The EM
    gate therefore computes the terminal mechanical verdict once and carries
    its run-owned review identity into the human gate.
    """
    revision = tp.git_head(ws)
    errors = []
    if not revision:
        errors.append("sign-off evidence has no integrated git revision")
    elif tp.changed_files(ws, revision):
        errors.append("sign-off evidence cannot bind an uncommitted product "
                      "diff; commit the reviewed integration tree first")
    if errors:
        return None, errors
    dod = _compute_signoff_dod(ws, state)
    if not dod["passed"]:
        return None, list(dod["errors"])
    binding = review_kernel_binding(state, "em", _current_task(state)) or {}
    findings, _ = _read_json(
        runtime_storage.review_public_path(ws, "findings.json"))
    meta = (findings or {}).get("meta") or {}
    return {
        "schema": "taskplane.signoff-evidence/v1",
        "integration_revision": revision,
        "requirement_id": state.get("requirement_id"),
        "baseline": state.get("baseline"),
        "review_kernel": dict(binding),
        "review_revision": {
            key: meta.get(key) for key in (
                "target_fingerprint", "context_fingerprint",
                "findings_fingerprint", "canonical_revision")
            if meta.get(key) is not None
        },
        "dod": dod,
        "notices": _dc.design_review_notices(meta),
    }, []


def _signoff_dod(ws: str, state: dict) -> dict:
    """Return the EM-sealed terminal verdict; never re-read shared evidence."""
    evidence = state.get("signoff_evidence")
    if isinstance(evidence, dict) \
            and evidence.get("schema") == "taskplane.signoff-evidence/v1" \
            and evidence.get("integration_revision") \
            and isinstance(evidence.get("dod"), dict):
        return dict(evidence["dod"])
    # Compatibility for callers that use this helper as the raw mechanical
    # aggregate.  Human-gate presentation does not use this legacy fallback;
    # next_action() surfaces a bounded-recovery marker instead.
    return _compute_signoff_dod(ws, state)


def _signoff_gate_dod(ws: str, state: dict) -> dict:
    if state.get("signoff_evidence"):
        return _signoff_dod(ws, state)
    return {
        "passed": False,
        "errors": ["legacy sign-off state has no immutable integration "
                   "evidence; approval can recover only when the current "
                   "revision and EM evidence still agree"],
        "notices": [], "scope": [], "baseline": state.get("baseline"),
        "legacy_recovery": True,
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
        relation = (row.get("relation", "changes")
                    if isinstance(row, dict) else "changes")
        depgraph.record_edge(ws, depgraph.req_node(rid), cids[0],
                             kind=relation, confidence="high",
                             note="approved design contract")
        applied.append(cids[0])
    if applied:
        tp.trace(ws, "design_contracts_recorded", gate="design_approval",
                 requirement=rid, contracts=applied)
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
        imp = depgraph.impact(
            ws, mods, policy=t.get("impact_policy")
            or depgraph.impact_policy(t)) if \
            depgraph.load(ws)["modules"] else None
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
            tp.trace(ws, "graph_shared_surface", task=t["id"],
                     requirement=rid, shared_with=shared)


def _true_up_graph(ws: str, state: dict) -> None:
    """Pre-EM graph work: realize requirements, then scan the final tree."""
    changed = [f for f in _diff_files(ws, state.get("baseline") or "HEAD")
               if not f.startswith(lens_router.LOOP_OWNED)]
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
        mine = [f for f in changed
                if any(f.startswith(s) for s in stems if s)]
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
            out.append({"task": t["id"], "requirement": rid,
                        "error": "requirement not found in the KB"})
            continue
        g = reqs.gate(rec, high_cost=bool(t.get("high_cost")),
                      changed_files=t.get("scope"), task_type=t.get("type"))
        mode = reqs.suggest_mode(g["score"], len(t.get("scope") or []))
        out.append({"task": t["id"], "requirement": rid, "gate": g,
                    "mode_suggestion": mode})
        tp.trace(ws, "refinement_gate", task=t["id"], requirement=rid,
                 score=g["score"], blocking=g["blocking"],
                 mode=mode["mode"])
    return out


def approve(ws: str, force: bool = False, by: str = None) -> dict:
    """Pass a human checkpoint (plan-approval or EM sign-off).

    `by` (v1.4.0, built for Claude Tag threads): WHO approved and where —
    e.g. "Dana R. — 'approved' in #platform-eng thread". Recorded into the
    trace event and the KB decision, so a gate pass is attributable to a
    human even in environments with no hook enforcement. In an unattended
    or Tag session, an approve WITHOUT `by` is exactly the self-approval
    the adherence experiment flags — drivers must pass the human's words."""
    if refusal := _stage_loop_mutation_refusal(ws):
        return refusal
    state = load(ws)
    if state is None:
        return {"error": "no active loop"}
    step = state["step"]
    refinement = None
    attestation_warning = None
    gate_notices: list = []
    define_projection = None
    if not str(by or "").strip() and step in ("plan_approval", "signoff"):
        # L5 (v2.2.1): symmetric attestation. Design approval hard-requires
        # --by; these two gates stay compatible but an anonymous pass is
        # RECORDED as unattributed and warned — not silently equivalent.
        by = "(unattributed)"
        attestation_warning = (f"{step} passed without --by — record WHO "
                               "approved (name + where) for an attributable "
                               "gate trail")
        tp.trace(ws, "loop_approve_unattributed", gate=step)
    if step == "design_approval":
        if not str(by or "").strip():
            return {"error": "design approval needs --by with the human's "
                             "identity/context; the designer cannot self-approve"}
        design_errors = _design_dod_errors(ws, state)
        if design_errors:
            tp.trace(ws, "loop_approve_blocked", gate="design",
                     reason="dod", errors=design_errors, by=by)
            return {"error": "Design Definition of Done failed — approval "
                             "cannot be recorded", "step": step,
                    "dod": {"passed": False, "errors": design_errors}}
        contract, _ = _design_contract(ws)
        state["design_fingerprint"] = _design_evidence_fingerprint(ws, contract)
        state["design_approved_by"] = by
        if not state.get("design_only") and build_c.program_enabled(ws):
            _, _, review_kernel = _review_runtime_modules()
            try:
                define_projection = build_c.project_define(
                    ws, state,
                    start_review=review_kernel.start_review,
                    selector=lens_router.route,
                    bind_actions=_bind_stateless_review_contract_actions)
            except (build_c.ProgramAuthorityError,
                    build_c.DefineProjectionError) as exc:
                return {"error": f"DEFINE projection refused: {exc}",
                        "step": step, "define_projection": {"slots": []}}
            state["define_projection"] = define_projection
        state["step"] = "done" if state.get("design_only") else "plan"
        tp.trace(ws, "loop_approve", gate="design", by=by,
                 fingerprint=state["design_fingerprint"][:12])
        # v2.3.0 wiring: notices (e.g. self-attested lens evidence) surface
        # in the approval response AND in the recorded approval decision.
        gate_notices = _dc.design_approval_notices(ws, contract)
        # v2.3.0: the sanctioned mechanical path for design-introduced
        # contracts into the graph — recorded + traced at the human
        # gate-pass (see _record_design_contracts). Plan DoR is unchanged.
        _record_design_contracts(ws, state, contract)
        modules = ((contract or {}).get("graph") or {}).get(
            "proposed_modules") or []
        kb.record_decision(
            ws, f"Design approved: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}\nApproved by: {by}\n"
                    f"Fingerprint: {state['design_fingerprint']}"
                    + ("".join("\nNotice: " + n for n in gate_notices)),
            decision=(contract or {}).get("decision", "Design approved."),
            tags=["design-approval", "solution-design"],
            context_files=list(modules),
            links={"loop": "design", "modules": list(modules)})
    elif step == "plan_approval":
        current_errors = _design_current_errors(ws, state)
        if current_errors:
            return {"error": "approved design is stale — plan approval is "
                             "blocked", "step": step,
                    "dor": {"ready": False, "blockers": current_errors}}
        # Refinement gate (advisory; hard only for high-cost tasks).
        refinement = _refinement_report(ws, state)
        blocked = [r for r in refinement if r.get("gate", {}).get("blocking")]
        if blocked and not force:
            return {"error": "refinement gate BLOCKED — a high-cost task's "
                             "requirement is under the threshold. Refine it "
                             "(close the gaps) or `loop approve --force`.",
                    "refinement": refinement}
        # B2 (R-0008): mechanical brief-shape-before-golden-regen ordering.
        if (refusal := tp.plan_ordering_refusal(ws, state.get("tasks"),
                                                "approve", by=by)):
            return refusal
        if _consolidated_enabled():
            state["authority_target_revision"] = tp.git_head(ws)
            fields = _authorization_fields(ws, state)
            packet = authority_engine.create_packet(fields)
            actor = str(by or "").strip()
            receipt = authority_engine.approve(
                packet, actor=actor,
                thread="loop:" + packet["fingerprint"][:20],
                authenticated=bool(actor))
            state["authority_packet"] = packet
            state["authority_receipt"] = receipt
            state["authority_derivations"] = {
                "execute": authority_engine.derive(
                    packet, receipt, stage="execute", current=fields,
                    actor=receipt["actor"], thread=receipt["thread"])
            }
            tp.trace(ws, "authority_packet", actor=receipt["actor"],
                     packet=packet["fingerprint"],
                     receipt=receipt["fingerprint"])
        if build_c.program_enabled(ws) and not state.get("define_projection"):
            state["design_approved_by"] = str(by or "").strip()
            state["design_fingerprint"] = str(
                state.get("design_fingerprint") or
                _design_evidence_fingerprint(ws))
            _, _, review_kernel = _review_runtime_modules()
            try:
                define_projection = build_c.project_define(
                    ws, state,
                    start_review=review_kernel.start_review,
                    selector=lens_router.route,
                    bind_actions=_bind_stateless_review_contract_actions)
            except (build_c.ProgramAuthorityError,
                    build_c.DefineProjectionError) as exc:
                return {"error": f"DEFINE projection refused: {exc}",
                        "step": step, "define_projection": {"slots": []}}
            state["define_projection"] = define_projection
        # Baseline for later diff-routing at EVALUATE/EM.
        state["baseline"] = tp.git_head(ws)
        resume_at = _first_unsettled_task_index(state)
        state["step"] = "execute" if resume_at is not None else "em"
        state["current_task"] = resume_at if resume_at is not None else 0
        tp.trace(ws, "loop_approve", gate="plan", by=by)
        # High-signal decision → the knowledge base.
        scope = sorted({g for t in (state.get("tasks") or [])
                        for g in t.get("scope", [])})
        kb.record_decision(
            ws, f"Plan approved: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}"
                    + (f"\nApproved by: {by}" if by else ""),
            decision=f"Approved a {len(state.get('tasks') or [])}-task plan.",
            tags=["plan-approval"], context_files=scope,
            links={"loop": "plan"})
    elif step == "signoff":
        if not state.get("signoff_evidence"):
            recovered, recovery_errors = _signoff_evidence_binding(ws, state)
            if recovery_errors:
                tp.trace(ws, "loop_approve_blocked", gate="em_signoff",
                         reason="legacy_evidence_unrecoverable",
                         errors=recovery_errors, by=by)
                return {
                    "error": "legacy sign-off evidence cannot be recovered "
                             "from the current integrated revision; return "
                             "this same loop to engineering review and "
                             "produce fresh terminal evidence",
                    "step": "signoff",
                    "dod": {"passed": False, "errors": recovery_errors},
                    "recovery": "same_loop_engineering_review",
                }
            state["signoff_evidence"] = recovered
            state["signoff_dod"] = dict(recovered["dod"])
            tp.trace(ws, "legacy_signoff_evidence_recovered",
                     revision=recovered["integration_revision"])
        dod = _signoff_dod(ws, state)
        if not dod["passed"]:
            tp.trace(ws, "loop_approve_blocked", gate="em_signoff",
                     reason="dod", errors=dod["errors"], by=by)
            return {"error": "Definition of Done failed — sign-off cannot "
                             "complete until the evidence is repaired",
                    "step": "signoff", "dod": dod}
        state["step"] = "retro"
        tp.trace(ws, "loop_approve", gate="em_signoff", final="retro", by=by)
        # v2.3.0 wiring: the sign-off payload carries the review's design
        # notices (accepted drift, declared edge realizations) when present.
        gate_notices = list(
            (state.get("signoff_evidence") or {}).get("notices") or [])
        scope = sorted({g for t in (state.get("tasks") or [])
                        for g in t.get("scope", [])})
        kb.record_decision(
            ws, f"Accepted: {state['goal'][:60]}",
            context=f"Goal: {state['goal']}"
                    + (f"\nApproved by: {by}" if by else "")
                    + ("".join("\nNotice: " + n for n in gate_notices)),
            decision="EM review passed and the human signed off — shipped.",
            tags=["accepted", "em-signoff"], context_files=scope,
            links={"loop": "signoff"})
    elif step == "selection":
        return {"error": "the selection gate needs a CHOICE, not a plain "
                         "approve — `loop select <variant|task-id|hybrid>`"}
    else:
        return {"error": f"nothing to approve at step '{step}'"}
    # Commit under the lock with a compare-and-swap on the entry step (v2.3.1):
    # approve() runs seconds of unlocked validation (signoff DoD runs every
    # task's tests, refinement, kb writes); an unlocked save could clobber a
    # concurrent gate() transition (the lost-update class the H2 fix closed in
    # gate()). If the on-disk step advanced while we worked, abort instead.
    stage_transition = None
    with mutate(ws) as locked:
        if locked.get("step") != step:
            return {"error": "the loop advanced concurrently during this "
                             f"approval (was '{step}', now "
                             f"'{locked.get('step')}') — re-run `loop next`",
                    "step": locked.get("step")}
        completion = _stage_loop_gate_completion(
            ws, state, step=step, outcome="approved", note=str(by or ""),
            approval={"actor": str(by or ""), "force": bool(force),
                      "notices": list(gate_notices)})
        try:
            stage_transition = _stage_loop_transition(
                ws, state, from_step=step, to_step=state["step"],
                completion=completion)
        except Exception as exc:
            return {"error": "stage-native loop transition failed closed: "
                    f"{exc.__class__.__name__}: {exc}", "step": step}
        locked.clear()
        locked.update(state)
    out = {"step": state["step"], "status": status(ws)}
    if stage_transition is not None:
        out["stage_transition"] = stage_transition
    if refinement:
        out["refinement"] = refinement
    if define_projection is not None:
        out["define_projection"] = define_projection
    if attestation_warning:
        out["warning"] = attestation_warning
    if gate_notices:
        out["notices"] = gate_notices
    if state["step"] == "retro":
        out["instruction"] = (
            "Human sign-off is recorded. Run `tp loop retro` once to record "
            "the lessons, true up the dependency graph, and close the loop.")
    return out


def select(ws: str, choice: str, note: str = "") -> dict:
    """The A/B selection gate — the human's pick of what ships. Accepts a
    variant letter, a task id, or 'hybrid'. This gate REPLACES the merge
    step variants never have: a winner goes to the engineering review; a
    hybrid goes back to plan for the graft (both variants kept as
    reference). Recorded to the KB — the WHY outlives the losing branch."""
    if refusal := _stage_loop_mutation_refusal(ws):
        return refusal
    reconcile_authority_effects(ws)
    stage_transition = None
    with mutate(ws) as state:
        if state is None:
            return {"error": "no active loop"}
        if state["step"] != "selection":
            return {"error": f"selection only at the selection gate "
                             f"(current: {state['step']})"}
        stage_before = json.loads(json.dumps(state))
        tasks = state.get("tasks") or []
        variants = [t for t in tasks if t.get("variant")] or tasks
        expected_revision = str(state.get("authority_target_revision") or
                                state.get("baseline") or "")
        # Revision validation and state mutation share one lock. A checkout
        # change can no longer land between validation and persistence.
        current_revision = tp.git_head(ws)
        # Pre-consolidation loops can resume at an already-open selection
        # gate without the later authority_target_revision/baseline fields.
        # There is no historical revision to reconstruct in that legacy
        # shape, so migrate it once to the revision observed under this same
        # lock. Current governed loops retain their persisted revision and
        # still fail closed on stale checkout changes; the revision fence
        # below protects both shapes through commit.
        if not expected_revision:
            expected_revision = current_revision
            state["authority_target_revision"] = current_revision
        boundary = authority_engine.build_selection(
            variants, selected=choice.strip(), revision=current_revision,
            expected_revision=expected_revision)
        if not boundary["authorized"] and \
                "stale_selection" in boundary["reasons"]:
            return {"error": "A/B selection is stale — the checkout revision "
                             "changed after the selection gate opened; refresh "
                             "the variants before choosing",
                    "expected_revision": expected_revision,
                    "actual_revision": current_revision,
                    "variants": [{"id": t["id"],
                                  "variant": t.get("variant")}
                                 for t in variants]}
        if not boundary["authorized"] and \
                "invalid_selection" in boundary["reasons"] and \
                choice.strip().lower() not in {
                    "hybrid", "neither", "none", "reject", "reject-both"}:
            return {"error": f"no variant matches '{choice}' — use a task "
                             "id, a variant letter, or 'hybrid'",
                    "variants": [{"id": t["id"],
                                  "variant": t.get("variant")}
                                 for t in variants]}
        state["_authority_revision_fence"] = current_revision
        if choice.strip().lower() == "hybrid":
            state["selection"] = {"choice": "hybrid", "note": note,
                                  "revision": current_revision}
            for t in variants:
                t["status"] = "reference"
            state["step"] = "plan"
            instruction = (
                "Hybrid selected: write a NEW plan/tasks.json with the graft "
                "task(s) — name the base variant's branch and what to graft "
                "from the other — then `loop gate pass`. Plan approval and "
                "the build/evaluate cycle apply as usual; both variant "
                "branches stay as reference until the retro.")
        elif choice.strip().lower() in (
                "neither", "none", "reject", "reject-both"):
        # Neither variant ships — the A/B round is abandoned. Both variants
        # become not_selected (kept as reference branches) and the loop goes
        # back to PLAN for a fresh approach, so the human who picks "neither"
        # has a real transition instead of parking at the selection gate.
            state["selection"] = {"choice": "neither", "note": note,
                                  "revision": current_revision}
            for t in variants:
                t["status"] = "not_selected"
            state["step"] = "plan"
            instruction = (
                "Neither variant selected: both are set aside (branches kept "
                "as reference). Write a NEW plan/tasks.json taking a "
                "different approach — what did both variants get wrong? — "
                "then `loop gate pass`. Plan approval and the build/evaluate "
                "cycle apply as usual.")
        else:
            c = choice.strip()
            win = next((t for t in variants
                        if t["id"] == c or
                        str(t.get("variant", "")).lower() == c.lower()), None)
            if win is None:
                return {"error": f"no variant matches '{choice}' — use a task "
                                 "id, a variant letter, or 'hybrid'",
                        "variants": [{"id": t["id"],
                                      "variant": t.get("variant")}
                                     for t in variants]}
            state["selection"] = {"choice": win["id"],
                                  "variant": win.get("variant"), "note": note,
                                  "revision": current_revision}
            win["selected"] = True
            win["status"] = "passed"
            for t in variants:
                if t is not win:
                    t["status"] = "not_selected"
            state["step"] = "em"
            instruction = (
                f"Winner: {win['id']}. Merge its branch "
                f"(`git merge tp/{win['id']}`), keep the losing branch as "
                "reference until the retro, clear the variant worktree "
                "contracts, then run the engineering review of the merged "
                "result (the complete selective routing decision).")
        selection = dict(state["selection"])
        goal = str(state.get("goal") or "")
        context_files = sorted({g for t in variants
                                for g in t.get("scope", [])})
        effect_id = f"selection:{current_revision}:{selection['choice']}"
        _enqueue_authority_effect(
            state, effect_id, trace_event="loop_select",
            trace_data={"choice": selection["choice"], "note": note},
            kb_data={
                "title": f"A/B selection: {selection['choice']} — {goal[:48]}",
                "context": (f"Goal: {goal}; variants: "
                            + ", ".join(t["id"] for t in variants)),
                "decision": (note or
                             f"Human selected {selection['choice']} at the "
                             "selection gate."),
                "tags": ["ab-selection"], "context_files": context_files,
                "links": {"loop": "selection"},
            })
        state["_stage_completion"] = _stage_loop_decision_completion(
            ws, schema="taskplane.loop-selection-result/v1",
            step="selection", outcome="selected",
            result={"choice": selection, "note": str(note or "")[:1024],
                    "selected_revision": current_revision})
        try:
            stage_transition = _stage_loop_transition(
                ws, state, from_step="selection", to_step=state["step"])
        except Exception as exc:
            state.clear()
            state.update(stage_before)
            return {"error": "stage-native selection transition failed "
                    f"closed: {exc.__class__.__name__}: {exc}",
                    "step": "selection"}
        state.pop("_stage_completion", None)
    state.pop("_authority_revision_fence", None)  # fake/test mutate adapters
    fence_failure = state.pop("_revision_fence_failed", None)
    if fence_failure:
        return {
            "error": "A/B selection is stale — the checkout revision changed "
                     "during the locked selection commit; no authority "
                     "transition was persisted",
            "expected_revision": fence_failure["expected"],
            "actual_revision": fence_failure["actual"],
        }
    effects = reconcile_authority_effects(ws)
    return {"step": state["step"], "selection": selection,
            "instruction": instruction, "status": status(ws),
            "effect_delivery": effects,
            **({"stage_transition": stage_transition}
               if stage_transition is not None else {})}


def _cascade_skip(state: dict, root_id: str) -> list:
    """Skip every task that (transitively) depends on root_id — they can
    never reach passed, so leaving them pending would deadlock the wave.
    Returns the ids that were cascaded."""
    tasks = state.get("tasks") or []
    dead = {root_id}
    cascaded = []
    changed = True
    while changed:
        changed = False
        for t in tasks:
            if t.get("status") in SETTLED:
                continue
            if set(t.get("deps") or []) & dead:
                t["status"] = "skipped"
                dead.add(t["id"])
                cascaded.append(t["id"])
                changed = True
    return cascaded


def resolve(ws: str, decision: str) -> dict:
    """Human decision when a task escalated (fix cycles exhausted)."""
    if refusal := _stage_loop_mutation_refusal(ws):
        return refusal
    state = load(ws)
    if state is None or state["step"] != "escalated":
        return {"error": "nothing escalated to resolve"}
    t = _current_task(state)
    cascaded = []
    if decision == "retry":
        evaluation = t.get("evaluation") or {}
        retry_evaluation = (
            t.get("status") == "unavailable"
            and evaluation.get("status") == "unavailable"
            and evaluation.get("verdict") == "non-judged"
        )
        t["fix_cycles"] = 0
        t["status"] = "running"
        # An unavailable evaluator produced no product judgment, so there is
        # no implementation finding to fix. Retry the missing judgment itself.
        # Judged product failures continue through the existing fix route.
        state["step"] = "evaluate" if retry_evaluation else "fix"
    elif decision == "pass":
        evaluation = t.get("evaluation") or {}
        accept_errors = []
        if not (
                t.get("status") == "unavailable"
                and evaluation.get("status") == "unavailable"
                and evaluation.get("verdict") == "non-judged"
                and evaluation.get("reason_code") ==
                "orchestration_unavailable"):
            accept_errors.append(
                "pass is only available for a non-judged orchestration "
                "outage")
        act_ws = str(t.get("workspace") or ws)
        unavailable_errors, verdict = _evaluation_unavailable_errors(
            act_ws, state, t)
        accept_errors.extend(unavailable_errors)
        criteria = (verdict or {}).get("criteria") or []
        if not criteria or any(
                not isinstance(row, dict) or row.get("status") != "met"
                for row in criteria):
            accept_errors.append(
                "human pass requires every task criterion to be evidenced "
                "as met")
        if accept_errors:
            return {"error": "unavailable evaluation cannot be accepted as "
                    "passed", "blockers": accept_errors}
        # This is a HUMAN recovery decision over a mechanically valid outage
        # envelope, not an evaluator self-pass.  Preserve the outage evidence
        # and make the exceptional acceptance explicit in task state.
        t["human_resolution"] = {
            "decision": "pass",
            "reason": "criteria met; quick-only evaluation accepted during "
                      "orchestration outage",
        }
        try:
            authority_ref, source_revision = _persist_reanchor_authority(
                act_ws, t, "human-resolved-orchestration-outage")
        except Exception as exc:
            t.pop("human_resolution", None)
            return {"error": "human pass authority could not be persisted "
                    "fail-closed: "
                    f"{exc.__class__.__name__}: {exc}"}
        t["workspace"] = os.path.realpath(act_ws)
        t["target_commit"] = source_revision
        t["reanchor_authority"] = authority_ref
        t["status"] = "passed"
        after_last = ("selection" if state.get("ab")
                      and not state.get("selection") else "em")
        if state.get("parallel"):
            state["step"] = (after_last if all(
                row.get("status") in SETTLED for row in state["tasks"])
                else "execute")
        else:
            nxt = _next_unsettled_index(state, state["current_task"])
            if nxt is not None:
                state["current_task"] = nxt
                state["step"] = "execute"
            else:
                state["step"] = after_last
    elif decision == "skip":
        t["status"] = "skipped"
        # Cascade: a task that depended (transitively) on the skipped one
        # can never satisfy deps⊆passed — skip it too, so it doesn't hold
        # the wave forever (the deadlock). Record which were cascaded.
        cascaded = _cascade_skip(state, t["id"])
        if cascaded:
            tp.trace(ws, "loop_skip_cascade", root=t["id"], skipped=cascaded)
        if state.get("parallel"):
            # settled-aware: advance only when every task is settled
            if all(x.get("status") in SETTLED for x in state["tasks"]):
                state["step"] = ("selection" if state.get("ab")
                                 and not state.get("selection") else "em")
            else:
                state["step"] = "execute"
        else:
            # serial: skip past any task the cascade just settled, so the
            # next execute is a task that still has work owed.
            nxt = _next_unsettled_index(state, state["current_task"])
            if nxt is not None:
                state["current_task"] = nxt
                state["step"] = "execute"
            else:
                state["step"] = "em"
    elif decision == "defer":
        # Human parks the task on an external gate: it settles AND satisfies
        # its dependents (the work will exist, just not via this loop) — the
        # clean form of what previously required hand-editing loop.json.
        t["status"] = "external"
        if state.get("parallel"):
            if all(x.get("status") in SETTLED for x in state["tasks"]):
                state["step"] = ("selection" if state.get("ab")
                                 and not state.get("selection") else "em")
            else:
                state["step"] = "execute"
        else:
            nxt = _next_unsettled_index(state, state["current_task"])
            if nxt is not None:
                state["current_task"] = nxt
                state["step"] = "execute"
            else:
                state["step"] = "em"
    elif decision == "abort":
        state["step"] = "failed"
    else:
        return {"error": "decision must be retry|pass|skip|defer|abort"}
    state["_stage_completion"] = _stage_loop_decision_completion(
        ws, schema="taskplane.loop-resolution-result/v1",
        step="escalated", outcome="resolved",
        result={"decision": decision, "task_id": t.get("id"),
                "resulting_status": t.get("status"),
                "resulting_step": state["step"],
                "cascaded_task_ids": list(cascaded)})
    stage_transition = None
    with mutate(ws) as locked:                       # v2.3.1: locked commit
        if locked.get("step") != "escalated":
            return {"error": "the loop advanced concurrently during resolve "
                             f"(now '{locked.get('step')}') — re-run",
                    "step": locked.get("step")}
        try:
            stage_transition = _stage_loop_transition(
                ws, state, from_step="escalated", to_step=state["step"])
        except Exception as exc:
            return {"error": "stage-native recovery transition failed "
                    f"closed: {exc.__class__.__name__}: {exc}",
                    "step": "escalated"}
        state.pop("_stage_completion", None)
        locked.clear()
        locked.update(state)
    tp.trace(ws, "loop_resolve", decision=decision, task=t.get("id"))
    return {"step": state["step"], "status": status(ws),
            **({"stage_transition": stage_transition}
               if stage_transition is not None else {})}
def replan(ws: str, by: str, reason: str) -> dict:
    if refusal := _stage_loop_mutation_refusal(ws):
        return refusal

    @contextlib.contextmanager
    def stage_bound_mutate(workspace: str):
        with mutate(workspace) as locked:
            before = json.loads(json.dumps(locked)) if locked is not None \
                else None
            from_step = str((locked or {}).get("step") or "")
            yield locked
            if locked is None or locked.get("step") == from_step:
                return
            locked["_stage_completion"] = _stage_loop_decision_completion(
                workspace, schema="taskplane.loop-replan-result/v1",
                step=from_step, outcome="replanned",
                result={"from_step": from_step,
                        "to_step": str(locked["step"]),
                        "by": str(by)[:256],
                        "reason": str(reason)[:1024]})
            locked["_stage_force_transition"] = True
            try:
                _stage_loop_transition(
                    workspace, locked, from_step=from_step,
                    to_step=str(locked["step"]))
            except Exception:
                locked.clear()
                locked.update(before or {})
                raise
            finally:
                locked.pop("_stage_completion", None)
                locked.pop("_stage_force_transition", None)

    def release_replanned_contract(workspace: str) -> None:
        released = tp.sweep_completed_worker_contracts(
            workspace, loop_state=load(workspace))
        if not released:
            tp.clear(workspace)  # legacy pre-lifecycle run

    try:
        return loop_recovery.replan(
            ws, by=by, reason=reason, load_state=load,
            mutate_state=stage_bound_mutate,
            clear_contract=release_replanned_contract,
            trace=tp.trace, record_decision=kb.record_decision)
    except Exception as exc:
        return {"error": "stage-native replan transition failed closed: "
                f"{exc.__class__.__name__}: {exc}",
                "step": (load(ws) or {}).get("step")}
def retro(ws: str) -> dict:
    if refusal := _stage_loop_mutation_refusal(ws):
        return refusal

    @contextlib.contextmanager
    def prepare_only_mutate(workspace: str):
        with mutate(workspace) as locked:
            yield locked
            if locked is None:
                return
            sealed = locked.get("retro") or {}
            if locked.get("step") in {"done", "failed"} and \
                    sealed.get("status") == "complete":
                locked["_retro_terminal_step"] = locked["step"]
                locked["step"] = "retro"

    result = retro_engine.run(
        ws, load_state=load, mutate_state=prepare_only_mutate,
        loop_path=_loop_path(ws), normalize_severity=normalize_severity)
    if not isinstance(result, dict) or result.get("error"):
        return result
    final = load(ws) or {}
    target_step = str(final.get("_retro_terminal_step") or final.get("step"))
    if target_step not in {"done", "failed"}:
        return result
    try:
        transition_kwargs = {"from_step": "retro", "to_step": target_step}
        if final.get("_retro_terminal_step"):
            completion = _stage_loop_gate_completion(
                ws, final, step="retro", outcome=target_step)
            completion["retro"] = result
            completion["_stage_output"]["values"]["retro"] = result
            transition_kwargs["completion"] = completion
        transition = _stage_loop_transition(ws, final, **transition_kwargs)
    except Exception as exc:
        # Keep the sealed report and its terminal target as a durable replay
        # marker.  ``retro_engine.run`` returns that same sealed report on the
        # next invocation, allowing stage terminalization to resume without
        # re-running or rewriting Retro.  The marker is cleared only after the
        # immutable stage transition succeeds and legacy state can commit.
        return {"error": "stage-native Retro terminalization failed closed: "
                f"{exc.__class__.__name__}: {exc}", "step": "retro",
                "retro": result}
    terminal_authority = None
    terminal_delivery = final.get("terminal_delivery")
    if terminal_delivery is not None:
        try:
            if not isinstance(terminal_delivery, Mapping):
                raise TypeError("terminal delivery composition must be a mapping")
            terminal_authority = terminal_truth.finalize_terminal_delivery(
                **dict(terminal_delivery))
        except Exception as exc:
            # The stage transition is replay-safe and the legacy loop marker
            # remains at Retro until the durable terminal bundle reconciles.
            # Never return a terminal outcome without its live CAS authority.
            return {"error": "terminal delivery failed closed: "
                    f"{exc.__class__.__name__}: {exc}", "step": "retro",
                    "retro": result}
    if final.get("_retro_terminal_step"):
        with mutate(ws) as locked:
            if locked is None or locked.get("step") != "retro":
                return {"error": "Retro legacy finalization lost its "
                        "prepared state", "step": (locked or {}).get("step")}
            sealed = locked.get("retro") or {}
            if sealed.get("status") != "complete":
                return {"error": "Retro report is not complete",
                        "step": "retro"}
            locked["step"] = target_step
            locked.pop("_retro_terminal_step", None)
    if transition is not None:
        result = {**result, "stage_transition": transition}
    if terminal_authority is not None:
        result = {**result, "terminal_authority": terminal_authority}
    return result
_load_tasks = loop_status.load_tasks
status = loop_status.status
user_summary = loop_status.user_summary
_publish_artifacts = loop_status.publish_artifacts
_with_dashboard = loop_status.with_dashboard


def _with_dispatch_dashboard(fn):
    """Refresh durable artifacts and return their stable delivery contract.

    ``next_action`` is serialized into Task-path and workflow prompts.  The
    receipt's hashes and byte counts include host-specific dispatch
    observations, so they differ when otherwise identical emissions occur in
    sequence.  The selected mode and artifact locations are semantic output,
    however: retain them and remove only those volatile content measurements.
    """
    wrapped = _with_dashboard(fn)

    def dispatch_wrapped(ws, *args, **kwargs):
        result = wrapped(ws, *args, **kwargs)
        if isinstance(result, dict):
            dashboard = result.get("dashboard")
            if isinstance(dashboard, dict):
                delivery = dashboard.get("delivery")
                if isinstance(delivery, dict):
                    stable = {key: value for key, value in delivery.items()
                              if key not in {"semantic_bytes",
                                             "semantic_sha256"}}
                    artifacts = stable.get("artifacts")
                    if isinstance(artifacts, dict):
                        stable["artifacts"] = {
                            name: ({key: value for key, value in receipt.items()
                                    if key not in {"bytes", "sha256"}}
                                   if isinstance(receipt, dict) else receipt)
                            for name, receipt in artifacts.items()
                        }
                    inline = stable.get("inline")
                    if isinstance(inline, dict):
                        stable["inline"] = {
                            key: value for key, value in inline.items()
                            if key not in {"bytes", "sha256",
                                           "semantic_bytes"}
                        }
                    dashboard["delivery"] = stable
        return result

    dispatch_wrapped.__name__ = fn.__name__
    dispatch_wrapped.__doc__ = fn.__doc__
    dispatch_wrapped.__wrapped__ = fn
    return dispatch_wrapped


gate = _with_dashboard(gate)
submit = _with_dashboard(submit)
next_action = _with_dispatch_dashboard(next_action)
approve = _with_dashboard(approve)
retro = _with_dashboard(retro)
