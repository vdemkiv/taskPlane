"""BUILD-C acceptance-checkpoint specifications and Plan scope closure.

This module owns the input boundary.  Runtime-event validation and receipt
minting are deliberately layered on top by the next BUILD-C task; callers
cannot turn a checkpoint specification into evidence merely by constructing a
mapping that looks like a receipt.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

try:
    import checkpoint_boundary
except ImportError:  # package import path
    from taskplane import checkpoint_boundary

try:
    from taskplane import taskplane_lite as contract_engine
    from taskplane import terminal_truth
    from taskplane import wiring_closure
except ImportError:  # direct executable/import compatibility
    import taskplane_lite as contract_engine
    import terminal_truth
    import wiring_closure


CHECKPOINT_SCHEMA = "taskplane.build-c-checkpoint/v1"
CHECKPOINT_RECEIPT_SCHEMA = "taskplane.build-c-checkpoint-receipt/v1"
DESIGN_CONTRACT = os.path.join("design", "contract.json")

CLOSED_GAP_CATEGORIES = (
    "ac-bound-checkpoints",
    "engine-minted-checkpoint-receipts",
    "submit-to-checkpoint-wiring",
    "define-stage-projection",
    "direct-no-state-graph-disjoint-assignment",
    "checkpoint-to-integration-authorization",
)

ORDERED_CHECKPOINT_PHASES = (
    "compile_import",
    "focused_proof",
    "forbidden_state_counts",
    "ratchet_delta",
    "engineering_judgment",
)

_SPEC_FIELDS = frozenset({
    "schema", "checkpoint_id", "phase", "ac_ids",
    "predecessor_checkpoint_ids", "worktree_revision", "declared_scope",
    "focused_proof", "ratchet_baseline",
})
_PROOF_FIELDS = frozenset({"path", "argv"})
_R0010_TASK = re.compile(r"(?:^|-)r0010(?:-|$)", re.IGNORECASE)
_COMMAND_RESULT_FIELDS = frozenset({
    "schema", "action", "handle", "identity", "lifecycle_states",
    "snapshot", "event",
})
_TERMINAL_STATES = frozenset({"succeeded", "failed", "timed_out", "cancelled"})
_GREEN_STATE = "succeeded"
_ENGINE_PRODUCER = "taskplane.checkpoint-engine/v1"
_IDENTITY_SCHEMA = "taskplane.governed-command-identity/v1"
_RESULT_SCHEMA = "taskplane.governed-command-result/v1"
_EVENT_SCHEMA = "taskplane.command-event/v1"
_STATE_SCHEMA = "taskplane.command-state/v1"
_DELIVERY_SCHEMA = "taskplane.command-delivery-receipt/v1"
_SEMANTIC_EXECUTION_RECEIPT_SCHEMA = \
    "taskplane.semantic-checkpoint-execution-receipt/v1"
_SAFE_ENVIRONMENT_KEYS = (
    "LANG", "LC_ALL", "LC_CTYPE", "PATH", "PYTHONHASHSEED", "TZ",
)
_STATELESS_INHERITED_ENVIRONMENT_KEYS = (
    "LANG", "LC_ALL", "LC_CTYPE", "PYTHONHASHSEED", "TZ",
)
_PYTHON_EXECUTABLE = re.compile(r"python(?:\d+(?:\.\d+)*)?\Z")
_PYTEST_EXECUTABLES = frozenset({"pytest", "py.test"})
_NON_EXECUTING_PYTEST_OPTIONS = frozenset({
    "--collect-only", "--co", "--fixtures", "--fixtures-per-test",
    "--help", "--setup-only", "--version",
})
_PYTEST_PLUGIN = "taskplane.checkpoint"
_PYTEST_REVISION_OPTION = "--taskplane-checkpoint-revision"
_PYTEST_SCOPE_OPTION = "--taskplane-checkpoint-scope"
_OBSERVED_REVISION_PREFIX = "taskplane-checkpoint-observed-revision="
_GIT_REVISION = re.compile(r"[0-9a-f]{40,64}\Z")
_PICKUP_OUTPUT_EVIDENCE_LIMIT_BYTES = 64 * 1024
_PICKUP_OUTPUT_READ_CHARS = 16 * 1024
_OUTPUT_TRUNCATION_MARKER = b"\n... taskplane output truncated ...\n"


class CheckpointSpecError(ValueError):
    """A checkpoint specification cannot safely start its focused proof."""


class CheckpointReceiptError(CheckpointSpecError):
    """Engine-observed checkpoint evidence cannot mint a green receipt."""


def validate_candidate_wiring_for_checkpoint(
    receipt: Mapping,
    *,
    repository_fingerprint: str,
    full_source_sha: str,
    requirement_id: str,
) -> dict:
    """Require the live real-checkout producer before terminal checkpointing."""
    try:
        return wiring_closure.validate_candidate_checkout_receipt(
            receipt,
            expected_repository_fingerprint=repository_fingerprint,
            expected_head_sha=full_source_sha,
            expected_requirement_id=requirement_id,
        )
    except wiring_closure.WiringClosureError as exc:
        raise CheckpointReceiptError(str(exc)) from exc


def terminal_tasks_and_gates_surface(
    identity: Mapping,
    *,
    tasks: Sequence[Mapping],
    gates: Sequence[Mapping],
) -> dict:
    """Prepare the tasks/gates projection without granting terminal status."""
    return terminal_truth.prepare_terminal_surface(
        "tasks_and_gates",
        identity,
        {"tasks": [dict(item) for item in tasks],
         "gates": [dict(item) for item in gates]},
    )


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")).hexdigest()


def _bounded_process_output(process) -> tuple[str, str, int, bool]:
    """Consume merged text output with fixed in-memory retained evidence."""
    digest = hashlib.sha256()
    byte_count = 0
    retained = bytearray()
    head = bytearray()
    tail = bytearray()
    truncated = False
    marker_size = len(_OUTPUT_TRUNCATION_MARKER)
    retained_budget = _PICKUP_OUTPUT_EVIDENCE_LIMIT_BYTES - marker_size
    head_budget = retained_budget // 2
    tail_budget = retained_budget - head_budget

    stream = process.stdout
    if stream is None:
        raise RuntimeError("focused proof output stream is unavailable")
    while True:
        text = stream.read(_PICKUP_OUTPUT_READ_CHARS)
        if not text:
            break
        encoded = text.encode("utf-8")
        digest.update(encoded)
        byte_count += len(encoded)
        if not truncated and len(retained) + len(encoded) <= \
                _PICKUP_OUTPUT_EVIDENCE_LIMIT_BYTES:
            retained.extend(encoded)
            continue
        if not truncated:
            combined = bytes(retained) + encoded
            head.extend(combined[:head_budget])
            tail.extend(combined[-tail_budget:])
            retained.clear()
            truncated = True
            continue
        tail.extend(encoded)
        if len(tail) > tail_budget:
            del tail[:-tail_budget]

    if not truncated:
        summary = bytes(retained).decode("utf-8")
    else:
        summary = (
            bytes(head).decode("utf-8", errors="ignore") +
            _OUTPUT_TRUNCATION_MARKER.decode("ascii") +
            bytes(tail).decode("utf-8", errors="ignore")
        )
    return summary, digest.hexdigest(), byte_count, truncated


def receipt_digest(receipt: Mapping) -> str:
    """Return the canonical digest of a receipt without its digest field."""
    if not isinstance(receipt, Mapping):
        raise CheckpointReceiptError("checkpoint receipt must be a mapping")
    return _canonical_digest({
        key: value for key, value in receipt.items()
        if key != "receipt_digest"
    })


def _strings(value, field: str, *, nonempty: bool = True) -> list[str]:
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes))
            or any(not isinstance(item, str) or not item.strip()
                   for item in value)):
        raise CheckpointSpecError(f"{field} must be a list of non-empty strings")
    result = [item.strip() for item in value]
    if nonempty and not result:
        raise CheckpointSpecError(f"{field} must not be empty")
    if len(result) != len(set(result)):
        raise CheckpointSpecError(f"{field} contains duplicate values")
    return result


def _repository_path(worktree: str, value: object, field: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value.strip():
        raise CheckpointSpecError(f"{field} must be a repository-relative path")
    relpath = value.strip().replace("\\", "/")
    if os.path.isabs(relpath) or relpath == ".." or relpath.startswith("../"):
        raise CheckpointSpecError(f"{field} escapes the worktree: {relpath}")
    root = os.path.realpath(worktree)
    candidate = os.path.realpath(os.path.join(root, relpath))
    try:
        inside = os.path.commonpath((root, candidate)) == root
    except ValueError:
        inside = False
    if not inside:
        raise CheckpointSpecError(f"{field} escapes the worktree: {relpath}")
    return relpath, candidate


def _git(worktree: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=worktree, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          check=False)


def _scope_contains(path: str, scope: Sequence[str]) -> bool:
    return any(path == pattern or fnmatch.fnmatchcase(path, pattern)
               for pattern in scope)


def _reserved_pytest_identity_arg(value: str) -> bool:
    return (_PYTEST_PLUGIN in value or
            any(value == option or value.startswith(option + "=")
                for option in (
                    _PYTEST_REVISION_OPTION, _PYTEST_SCOPE_OPTION)))


def _focused_pytest_argv(argv: Sequence[str], proof_path: str,
                         revision: str, scope: Sequence[str]) -> list[str]:
    """Return a pytest command cryptographically bound to ``revision``.

    The incumbent command analyzer supports direct pytest commands, so the
    common ``python -m pytest`` spelling is normalized to that existing form.
    Engine-owned plugin options carry the exact Git revision and declared
    scope in argv, making the incumbent runtime fingerprint revision-specific
    while letting the executing process verify the scoped tree itself.
    """
    command = list(argv)
    executable = os.path.basename(command[0]) if command else ""
    if (_PYTHON_EXECUTABLE.fullmatch(executable) and
            command[1:3] == ["-m", "pytest"]):
        command = ["pytest", *command[3:]]
    elif command[0] not in _PYTEST_EXECUTABLES:
        raise CheckpointSpecError(
            "focused_proof.argv must invoke pytest directly or through Python")

    if any(item in _NON_EXECUTING_PYTEST_OPTIONS for item in command[1:]):
        raise CheckpointSpecError(
            "focused_proof.argv must execute pytest, not only inspect tests")

    selector = command[-1] if command else ""
    if selector != proof_path and not selector.startswith(proof_path + "::"):
        raise CheckpointSpecError(
            f"focused_proof.argv must run pytest target {proof_path}")
    engine_arguments = ["-p", _PYTEST_PLUGIN,
                        _PYTEST_REVISION_OPTION, revision]
    for item in scope:
        engine_arguments.extend([_PYTEST_SCOPE_OPTION, item])
    start = -(len(engine_arguments) + 1)
    if command[start:-1] == engine_arguments:
        earlier = command[1:start]
        if any(_reserved_pytest_identity_arg(item) for item in earlier):
            raise CheckpointSpecError(
                "focused_proof.argv must not override checkpoint identity")
        return command
    if any(_reserved_pytest_identity_arg(item) for item in command[1:]):
        raise CheckpointSpecError(
            "focused_proof.argv must invoke pytest for the exact revision")
    command[-1:-1] = engine_arguments
    return command


def _scope_changes(workspace: str, scope: Sequence[str]) -> list[str]:
    pathspecs = [
        (":(glob)" if any(char in item for char in "*?[") else ":(literal)")
        + item for item in scope
    ]
    status = _git(workspace, "status", "--porcelain=v1", "-z",
                  "--untracked-files=all", "--", *pathspecs)
    if status.returncode != 0:
        raise ValueError("declared checkpoint scope could not be inspected")
    return [entry for entry in status.stdout.split("\0") if entry]


def pytest_addoption(parser) -> None:
    """Register engine-owned identity inputs for the checkpoint plugin."""
    group = parser.getgroup("taskplane-checkpoint")
    group.addoption(_PYTEST_REVISION_OPTION, action="store")
    group.addoption(_PYTEST_SCOPE_OPTION, action="append", default=[])


def _pytest_checkpoint_context(config) -> tuple[str, str, list[str]]:
    revision = str(config.getoption(_PYTEST_REVISION_OPTION) or "")
    if not _GIT_REVISION.fullmatch(revision):
        raise ValueError("checkpoint pytest plugin requires an exact revision")
    scope = config.getoption(_PYTEST_SCOPE_OPTION)
    if (not isinstance(scope, list) or not scope or
            any(not isinstance(item, str) or not item.strip()
                for item in scope) or len(scope) != len(set(scope))):
        raise ValueError("checkpoint pytest plugin requires declared scope")
    workspace = str(Path(config.invocation_params.dir).resolve())
    return workspace, revision, [item.strip() for item in scope]


def _observed_repository_revision(workspace: str) -> str:
    head = _git(workspace, "rev-parse", "HEAD")
    revision = head.stdout.strip() if head.returncode == 0 else ""
    if not _GIT_REVISION.fullmatch(revision):
        raise ValueError("checkpoint pytest plugin cannot observe Git HEAD")
    return revision


def pytest_configure(config) -> None:
    """Fail the governed pytest process unless it starts at its claimed SHA."""
    import pytest

    try:
        workspace, expected, scope = _pytest_checkpoint_context(config)
        observed = _observed_repository_revision(workspace)
        changes = _scope_changes(workspace, scope)
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc
    if observed != expected:
        raise pytest.UsageError(
            "checkpoint pytest runtime-observed repository revision "
            f"{observed} does not match {expected}")
    if changes:
        raise pytest.UsageError(
            "checkpoint pytest runtime-observed declared scope is dirty: "
            + changes[0])
    config._taskplane_checkpoint_workspace = workspace
    config._taskplane_checkpoint_revision = observed
    config._taskplane_checkpoint_scope = scope


def pytest_sessionfinish(session, exitstatus) -> None:
    """Recheck Git identity and attest it in the governed runtime output."""
    config = session.config
    workspace = getattr(config, "_taskplane_checkpoint_workspace", None)
    expected = getattr(config, "_taskplane_checkpoint_revision", None)
    scope = getattr(config, "_taskplane_checkpoint_scope", None)
    if not workspace or not expected or not scope:
        return
    try:
        observed = _observed_repository_revision(workspace)
        changes = _scope_changes(workspace, scope)
    except ValueError:
        observed = ""
        changes = ["inspection failed"]
    if observed != expected or changes:
        session.exitstatus = 1
        return
    terminal = config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.ensure_newline()
        terminal.write_line(_OBSERVED_REVISION_PREFIX + observed)


def validate_checkpoint_spec(worktree: str, spec: Mapping) -> dict:
    """Validate and normalize an AC checkpoint before any command starts.

    The focused proof must be a tracked, non-symlink regular file in the exact
    worktree revision named by the specification.  The returned phase order is
    engine-owned and therefore cannot be supplied or reordered by a caller.
    """
    if not isinstance(spec, Mapping):
        raise CheckpointSpecError("checkpoint specification must be a mapping")
    unknown = sorted(set(spec) - _SPEC_FIELDS)
    if unknown:
        raise CheckpointSpecError("unknown checkpoint fields: "
                                  + ", ".join(unknown))
    if spec.get("schema") != CHECKPOINT_SCHEMA:
        raise CheckpointSpecError(f"schema must be {CHECKPOINT_SCHEMA}")
    for field in ("checkpoint_id", "phase", "worktree_revision"):
        if not isinstance(spec.get(field), str) or not spec[field].strip():
            raise CheckpointSpecError(f"{field} must be a non-empty string")

    ac_ids = _strings(spec.get("ac_ids"), "ac_ids")
    design_binding = None
    contract_path = os.path.join(worktree, DESIGN_CONTRACT)
    if os.path.isfile(contract_path):
        try:
            with open(contract_path, encoding="utf-8") as stream:
                design = json.load(stream)
        except (OSError, ValueError) as exc:
            raise CheckpointSpecError(
                "Design Contract cannot supply checkpoint acceptance tests: "
                f"invalid evidence {contract_path}: {exc}") from exc
        if not isinstance(design, Mapping):
            raise CheckpointSpecError(
                "Design Contract cannot supply checkpoint acceptance tests: "
                f"invalid evidence {contract_path}: root must be an object")
        try:
            design_binding = wiring_closure.checkpoint_acceptance_tests(
                worktree, design, ac_ids)
        except wiring_closure.WiringClosureError as exc:
            raise CheckpointSpecError(str(exc)) from exc
    predecessors = _strings(spec.get("predecessor_checkpoint_ids", []),
                            "predecessor_checkpoint_ids", nonempty=False)
    if spec["checkpoint_id"] in predecessors:
        raise CheckpointSpecError("checkpoint cannot name itself as predecessor")
    scope = _strings(spec.get("declared_scope"), "declared_scope")
    for item in scope:
        _repository_path(worktree, item, "declared_scope")

    proof = spec.get("focused_proof")
    if not isinstance(proof, Mapping):
        raise CheckpointSpecError("focused_proof must be a mapping")
    proof_unknown = sorted(set(proof) - _PROOF_FIELDS)
    if proof_unknown:
        raise CheckpointSpecError("unknown focused_proof fields: "
                                  + ", ".join(proof_unknown))
    proof_path, absolute_proof = _repository_path(
        worktree, proof.get("path"), "focused_proof.path")
    argv = _strings(proof.get("argv"), "focused_proof.argv")
    if not _scope_contains(proof_path, scope):
        raise CheckpointSpecError(
            f"focused proof {proof_path} is outside declared_scope")
    try:
        mode = os.lstat(absolute_proof).st_mode
    except FileNotFoundError as exc:
        raise CheckpointSpecError(
            f"focused proof does not exist: {proof_path}") from exc
    if not stat.S_ISREG(mode):
        raise CheckpointSpecError(
            f"focused proof must be a tracked regular file: {proof_path}")
    tracked = _git(worktree, "ls-files", "--error-unmatch", "--", proof_path)
    if tracked.returncode != 0:
        raise CheckpointSpecError(
            f"focused proof must be a tracked regular file: {proof_path}")
    unchanged = _git(worktree, "diff", "--quiet", "HEAD", "--", proof_path)
    if unchanged.returncode != 0:
        raise CheckpointSpecError(
            f"focused proof must match exact HEAD: {proof_path}")
    head = _git(worktree, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise CheckpointSpecError("worktree revision could not be resolved")
    revision = spec["worktree_revision"].strip()
    if revision != head.stdout.strip():
        raise CheckpointSpecError(
            "worktree_revision is stale; checkpoint requires exact HEAD")
    try:
        changes = _scope_changes(worktree, scope)
    except ValueError as exc:
        raise CheckpointSpecError(str(exc)) from exc
    if changes:
        raise CheckpointSpecError(
            "declared scope must match exact HEAD: " + changes[0])
    argv = _focused_pytest_argv(argv, proof_path, revision, scope)
    if design_binding is not None:
        if proof_path not in design_binding["files"]:
            raise CheckpointSpecError(
                f"focused proof is not Design-declared for {', '.join(ac_ids)}: "
                f"{proof_path}")
        focused_selector = argv[-1]
        if focused_selector not in design_binding["selectors"]:
            raise CheckpointSpecError(
                f"focused proof selector is not Design-declared for "
                f"{', '.join(ac_ids)}: {focused_selector}")
    ratchet = spec.get("ratchet_baseline")
    if not isinstance(ratchet, Mapping):
        raise CheckpointSpecError("ratchet_baseline must be a mapping")

    return {
        "schema": CHECKPOINT_SCHEMA,
        "checkpoint_id": spec["checkpoint_id"].strip(),
        "phase": spec["phase"].strip(),
        "ac_ids": ac_ids,
        "predecessor_checkpoint_ids": predecessors,
        "worktree_revision": revision,
        "declared_scope": scope,
        "focused_proof": {"path": proof_path, "argv": argv},
        "ratchet_baseline": dict(ratchet),
        "ordered_phases": list(ORDERED_CHECKPOINT_PHASES),
    }


def _validated_identity(value: object, field: str) -> dict:
    if (not isinstance(value, Mapping) or set(value) != {
            "schema", "run_id", "task_id"} or
            value.get("schema") != _IDENTITY_SCHEMA or
            any(not isinstance(value.get(key), str) or
                not value[key].strip() for key in ("run_id", "task_id"))):
        raise CheckpointReceiptError(f"{field} identity is invalid")
    return dict(value)


def _validated_predecessors(spec: Mapping,
                            receipts: Sequence[Mapping]) -> list[str]:
    if (not isinstance(receipts, Sequence) or
            isinstance(receipts, (str, bytes))):
        raise CheckpointReceiptError("predecessor receipts must be a list")
    expected = list(spec["predecessor_checkpoint_ids"])
    if len(receipts) != len(expected):
        raise CheckpointReceiptError(
            "predecessor receipt count does not match checkpoint specification")
    digests: list[str] = []
    for checkpoint_id, receipt in zip(expected, receipts):
        if not isinstance(receipt, Mapping):
            raise CheckpointReceiptError("predecessor receipt must be a mapping")
        identity = receipt.get("identity")
        supplied_digest = receipt.get("receipt_digest")
        if (receipt.get("schema") != CHECKPOINT_RECEIPT_SCHEMA or
                receipt.get("producer") != _ENGINE_PRODUCER or
                receipt.get("verdict") != "green" or
                not isinstance(identity, Mapping) or
                identity.get("checkpoint_id") != checkpoint_id or
                not isinstance(supplied_digest, str) or
                supplied_digest != receipt_digest(receipt)):
            raise CheckpointReceiptError(
                f"predecessor checkpoint receipt is invalid: {checkpoint_id}")
        digests.append(supplied_digest)
    return digests


def _validated_runtime_result(
        worktree: str, spec: Mapping, result: object, *,
        semantic_authorization: str | None = None) -> dict:
    if not isinstance(result, Mapping):
        raise CheckpointReceiptError(
            "governed command result must be an engine mapping")
    unknown = sorted(set(result) - _COMMAND_RESULT_FIELDS)
    if unknown:
        raise CheckpointReceiptError(
            "governed command result has caller-authored fields: " +
            ", ".join(unknown))
    missing = sorted(_COMMAND_RESULT_FIELDS - set(result))
    if missing:
        raise CheckpointReceiptError(
            "governed command result is missing: " + ", ".join(missing))
    if (result.get("schema") != _RESULT_SCHEMA or
            result.get("action") != "wait"):
        raise CheckpointReceiptError(
            "checkpoint requires an observed governed command wait result")

    identity = _validated_identity(result.get("identity"), "result")
    snapshot = result.get("snapshot")
    event = result.get("event")
    if not isinstance(snapshot, Mapping) or snapshot.get("schema") != _STATE_SCHEMA:
        raise CheckpointReceiptError("command snapshot is invalid")
    if not isinstance(event, Mapping) or event.get("schema") != _EVENT_SCHEMA:
        raise CheckpointReceiptError("command terminal event is invalid")
    event_identity = _validated_identity(event.get("identity"), "event")
    snapshot_identity = _validated_identity(
        snapshot.get("identity"), "snapshot")
    if identity != event_identity or identity != snapshot_identity:
        raise CheckpointReceiptError("governed command identities are mixed")

    handle = result.get("handle")
    if (not isinstance(handle, str) or not handle or
            snapshot.get("handle") != handle or event.get("handle") != handle):
        raise CheckpointReceiptError("governed command handles are mixed")
    state = event.get("state")
    if (state not in _TERMINAL_STATES or snapshot.get("state") != state or
            not isinstance(result.get("lifecycle_states"), Sequence) or
            isinstance(result.get("lifecycle_states"), (str, bytes)) or
            not result["lifecycle_states"] or
            result["lifecycle_states"][-1] != state):
        raise CheckpointReceiptError("governed command terminal state is mixed")

    delivery = event.get("delivery_receipt")
    if (not isinstance(delivery, Mapping) or
            delivery.get("schema") != _DELIVERY_SCHEMA or
            delivery.get("delivery_key") != event.get("delivery_key") or
            delivery.get("revision") != event.get("revision") or
            not str(delivery.get("consumer") or "").strip()):
        raise CheckpointReceiptError(
            "checkpoint result lacks an engine delivery receipt")

    expected_workspace = hashlib.sha256(
        str(Path(worktree).resolve()).encode("utf-8")).hexdigest()
    if snapshot.get("workspace_fingerprint") != expected_workspace:
        raise CheckpointReceiptError("command result belongs to another worktree")
    command_digest = _canonical_digest(spec["focused_proof"]["argv"])
    expected_runtime_command = hashlib.sha256(
        command_digest.encode("utf-8")).hexdigest()
    if snapshot.get("command_fingerprint") != expected_runtime_command:
        raise CheckpointReceiptError(
            "command result does not match the focused proof exact revision")

    artifact = event.get("artifact")
    if artifact != snapshot.get("artifact"):
        raise CheckpointReceiptError("command output artifacts are mixed")
    if not isinstance(artifact, Mapping) or set(artifact) != {
            "path", "sha256", "bytes", "truncated"}:
        raise CheckpointReceiptError("command output artifact is invalid")
    if (not isinstance(artifact.get("bytes"), int) or
            artifact["bytes"] < 0 or
            not isinstance(artifact.get("sha256"), str) or
            len(artifact["sha256"]) != 64 or
            not isinstance(artifact.get("truncated"), bool)):
        raise CheckpointReceiptError("command output artifact is invalid")
    exit_code = event.get("exit_code")
    if (isinstance(exit_code, bool) or not isinstance(exit_code, int) or
            snapshot.get("exit_code") != exit_code):
        raise CheckpointReceiptError("command exit status is mixed")
    if state != _GREEN_STATE or exit_code != 0:
        raise CheckpointReceiptError(
            f"focused_proof ended {state}; later phases stopped")
    output = event.get("output_delta")
    if not isinstance(output, str) or snapshot.get("output_summary") != output:
        raise CheckpointReceiptError("command output evidence is mixed")
    retained_bytes = output.encode("utf-8")
    raw_output_retained = snapshot.get("output_digest") == artifact["sha256"]
    boundary = None
    if raw_output_retained and artifact["truncated"]:
        marker = _OUTPUT_TRUNCATION_MARKER.decode("ascii")
        if (artifact["bytes"] <= _PICKUP_OUTPUT_EVIDENCE_LIMIT_BYTES or
                len(retained_bytes) > _PICKUP_OUTPUT_EVIDENCE_LIMIT_BYTES or
                output.count(marker) != 1):
            raise CheckpointReceiptError("command output evidence is mixed")
    elif (hashlib.sha256(retained_bytes).hexdigest() != artifact["sha256"] or
          len(retained_bytes) != artifact["bytes"]):
        raise CheckpointReceiptError("command output evidence is mixed")
    if raw_output_retained:
        attestation = _OBSERVED_REVISION_PREFIX + spec["worktree_revision"]
        if output.count(attestation) != 1:
            raise CheckpointReceiptError(
                "focused_proof lacks the runtime-observed repository revision")
    else:
        if not isinstance(semantic_authorization, str) or \
                not semantic_authorization.strip():
            raise CheckpointReceiptError(
                "privacy-minimized checkpoint output requires an exact "
                "semantic post-proof receipt")
        if not re.fullmatch(
                r"\[REDACTED\]\n\[OUTPUT_MINIMIZED bytes=\d+ "
                r"sha256=[0-9a-f]{64}\]", output):
            raise CheckpointReceiptError(
                "privacy-minimized checkpoint output is invalid")
        try:
            boundary = checkpoint_boundary.load_execution_evidence(
                str(Path(worktree).resolve()), semantic_authorization, handle)
        except Exception as exc:
            raise CheckpointReceiptError(
                "semantic checkpoint post-proof receipt is invalid") from exc
        if (not isinstance(boundary, Mapping) or
                boundary.get("schema") !=
                _SEMANTIC_EXECUTION_RECEIPT_SCHEMA or
                boundary.get("workspace") != str(Path(worktree).resolve()) or
                boundary.get("handle") != handle or
                boundary.get("identity") != identity or
                boundary.get("source_sha") != spec["worktree_revision"] or
                boundary.get("target_sha") != spec["worktree_revision"] or
                boundary.get("checkpoint_id") != spec["checkpoint_id"] or
                boundary.get("post_authority_verified") is not True or
                boundary.get("output_sha256") !=
                snapshot.get("output_digest") or
                boundary.get("state") != _GREEN_STATE or
                boundary.get("exit_code") != 0 or
                not isinstance(boundary.get("receipt_digest"), str) or
                len(boundary["receipt_digest"]) != 64):
            raise CheckpointReceiptError(
                "semantic checkpoint post-proof receipt is mixed")
    return {"identity": identity, "snapshot": dict(snapshot),
            "event": dict(event), "artifact": dict(artifact),
            "semantic_boundary": dict(boundary) if boundary else None}


def validate_and_mint(worktree: str, spec: Mapping,
                      command_result: Mapping, *,
                      predecessor_receipts: Sequence[Mapping] = (),
                      active_contract: Mapping | None = None,
                      runtime_environment: Mapping[str, str] | None = None,
                      runtime_command: Sequence[str] | None = None,
                      semantic_authorization: str | None = None
                      ) -> dict:
    """Mint one green receipt exclusively from runtime and repository facts.

    The caller supplies only the checkpoint specification and the incumbent
    governed-command result.  Producer, verdict, output, environment, result,
    revision, and receipt fields are all derived here; extra caller fields are
    refused by the two closed input boundaries.
    """
    try:
        validated = validate_checkpoint_spec(worktree, spec)
    except CheckpointSpecError as exc:
        raise CheckpointReceiptError(str(exc)) from exc
    predecessor_digests = _validated_predecessors(
        validated, predecessor_receipts)
    observed = _validated_runtime_result(
        worktree, validated, command_result,
        semantic_authorization=semantic_authorization)
    runtime_command_evidence = {}
    if runtime_command is not None:
        runtime_command = list(runtime_command)
        runtime_fingerprint = _canonical_digest(runtime_command)
        if (observed["snapshot"].get("metrics") or {}).get(
                "runtime_command_fingerprint") != runtime_fingerprint:
            raise CheckpointReceiptError(
                "stateless runtime command identity is mixed")
        runtime_command_evidence = {
            "runtime_argv": runtime_command,
            "runtime_fingerprint": runtime_fingerprint,
        }

    contract = (active_contract if active_contract is not None else
                contract_engine.load_active(str(Path(worktree).resolve())))
    if not isinstance(contract, Mapping):
        raise CheckpointReceiptError(
            "checkpoint receipt requires an exact active contract")
    event = observed["event"]
    snapshot = observed["snapshot"]
    artifact = observed["artifact"]
    boundary = observed.get("semantic_boundary")
    if boundary is not None:
        boundary_environment = boundary.get("runtime_environment")
        if not isinstance(boundary_environment, Mapping):
            raise CheckpointReceiptError(
                "semantic checkpoint runtime environment is invalid")
        if (runtime_environment is not None and
                dict(runtime_environment) != dict(boundary_environment)):
            raise CheckpointReceiptError(
                "semantic checkpoint runtime environment is mixed")
        runtime_environment = dict(boundary_environment)
    environment = (dict(runtime_environment)
                   if runtime_environment is not None else {
                       key: os.environ[key] for key in _SAFE_ENVIRONMENT_KEYS
                       if key in os.environ
                   })
    engine_material = {
        "producer": _ENGINE_PRODUCER,
        "checkpoint_schema": CHECKPOINT_SCHEMA,
        "receipt_schema": CHECKPOINT_RECEIPT_SCHEMA,
        "ordered_phases": list(ORDERED_CHECKPOINT_PHASES),
    }
    receipt = {
        "schema": CHECKPOINT_RECEIPT_SCHEMA,
        "producer": _ENGINE_PRODUCER,
        "engine_fingerprint": _canonical_digest(engine_material),
        "active_contract_fingerprint": _canonical_digest(contract),
        "identity": {
            "run_id": observed["identity"]["run_id"],
            "task_id": observed["identity"]["task_id"],
            "checkpoint_id": validated["checkpoint_id"],
            "ac_ids": list(validated["ac_ids"]),
        },
        "phase": validated["phase"],
        "ordered_phases": list(validated["ordered_phases"]),
        "completed_phases": ["focused_proof"],
        "command": {
            "argv": list(validated["focused_proof"]["argv"]),
            "cwd": str(Path(worktree).resolve()),
            "fingerprint": snapshot["command_fingerprint"],
            "handle": event["handle"],
            "runtime_revision": event["revision"],
            **runtime_command_evidence,
        },
        "environment_fingerprint": _canonical_digest(environment),
        "output": {
            "sha256": artifact["sha256"],
            "bytes": artifact["bytes"],
            "truncated": artifact["truncated"],
            "redactions": int((snapshot.get("metrics") or {}).get(
                "output_redactions", 0)),
        },
        "result": {
            "state": event["state"],
            "exit_code": event["exit_code"],
            "digest": _canonical_digest(event),
        },
        "worktree_revision": validated["worktree_revision"],
        "declared_scope": list(validated["declared_scope"]),
        "predecessor_receipt_digests": predecessor_digests,
        "verdict": "green",
    }
    if boundary is not None:
        receipt["runtime_boundary_receipt_digest"] = \
            boundary["receipt_digest"]
    receipt["receipt_digest"] = receipt_digest(receipt)
    return receipt


def run_and_mint_stateless(worktree: str, spec: Mapping, *,
                           identity: Mapping, active_contract: Mapping) -> dict:
    """Run one focused proof and mint the incumbent receipt without state."""
    validated = validate_checkpoint_spec(worktree, spec)
    authorized_argv = list(validated["focused_proof"]["argv"])
    argv = [os.path.abspath(sys.executable), "-m", "pytest",
            *authorized_argv[1:]]
    package_root = str(Path(__file__).resolve().parent.parent)
    environment = {
        key: os.environ[key] for key in _STATELESS_INHERITED_ENVIRONMENT_KEYS
        if key in os.environ
    }
    environment.update({
        "PATH": os.defpath,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": package_root,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    })
    process = subprocess.Popen(
        argv, cwd=worktree, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        env=environment,
    )
    output, output_digest, output_bytes, output_truncated = \
        _bounded_process_output(process)
    returncode = process.wait()
    state = "succeeded" if returncode == 0 else "failed"
    command_digest = _canonical_digest(authorized_argv)
    command_fingerprint = hashlib.sha256(
        command_digest.encode("utf-8")
    ).hexdigest()
    handle = hashlib.sha256(_pickup_runtime_material(
        identity, validated["checkpoint_id"], validated["worktree_revision"]
    )).hexdigest()[:32]
    checked_identity = _validated_identity(identity, "pickup")
    artifact = {
        "path": "pickup/focused-proof.log", "sha256": output_digest,
        "bytes": output_bytes, "truncated": output_truncated,
    }
    event = {
        "schema": _EVENT_SCHEMA, "handle": handle, "revision": 1,
        "state": state, "reason": state, "exit_code": returncode,
        "elapsed_ms": 0, "output_delta": output, "artifact": artifact,
        "delivery_key": "pickup-focused-proof", "identity": checked_identity,
        "delivery_receipt": {
            "schema": _DELIVERY_SCHEMA, "consumer": "pickup:checkpoint",
            "delivery_key": "pickup-focused-proof", "revision": 1,
        },
    }
    snapshot = {
        "schema": _STATE_SCHEMA, "handle": handle,
        "workspace_fingerprint": hashlib.sha256(
            str(Path(worktree).resolve()).encode("utf-8")
        ).hexdigest(),
        "authorization_fingerprint": _canonical_digest(active_contract),
        "command_fingerprint": command_fingerprint, "state": state,
        "revision": 1, "identity": checked_identity,
        "exit_code": returncode, "reason": state,
        "artifact": artifact, "output_summary": output,
        "output_digest": output_digest, "metrics": {
            "output_redactions": 0,
            "runtime_command_fingerprint": _canonical_digest(argv),
        },
    }
    result = {
        "schema": _RESULT_SCHEMA, "action": "wait", "handle": handle,
        "identity": checked_identity,
        "lifecycle_states": ["created", "running", state],
        "snapshot": snapshot, "event": event,
    }
    return validate_and_mint(
        worktree, spec, result, active_contract=active_contract,
        runtime_environment=environment, runtime_command=argv,
    )


def _pickup_runtime_material(identity: Mapping, checkpoint_id: str,
                             revision: str) -> bytes:
    return json.dumps({
        "identity": dict(identity), "checkpoint_id": checkpoint_id,
        "revision": revision,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _is_r0010_code_task(task: Mapping) -> bool:
    task_id = str(task.get("id") or "")
    if not _R0010_TASK.search(task_id):
        return False
    return any(str(path).replace("\\", "/").endswith(".py")
               for path in task.get("scope") or [])


def closed_gap_errors(tasks: Sequence[Mapping]) -> tuple[list[str], bool]:
    """Return Plan blockers and whether new human scope authority is needed."""
    implementation = [task for task in tasks or ()
                      if isinstance(task, Mapping)
                      and _is_r0010_code_task(task)]
    errors: list[str] = []
    categories: list[str] = []
    scope_decision_required = False
    for task in implementation:
        task_id = str(task.get("id") or "?")
        category = task.get("gap_category")
        if not isinstance(category, str) or not category.strip():
            errors.append(f"task {task_id}: gap_category is missing")
            continue
        category = category.strip()
        categories.append(category)
        if category not in CLOSED_GAP_CATEGORIES:
            errors.append(
                f"task {task_id}: gap_category {category!r} is not in the "
                "closed six-category R-0010 catalog; scope_decision_required")
            scope_decision_required = True
    for category, count in sorted(Counter(categories).items()):
        if count > 1:
            errors.append(
                f"duplicate R-0010 gap_category {category!r} appears {count} times")
    missing = [category for category in CLOSED_GAP_CATEGORIES
               if category not in categories]
    if missing:
        errors.append("closed R-0010 gap categories are missing: "
                      + ", ".join(missing))
    return errors, scope_decision_required


def validate_closed_gap_plan(tasks: Sequence[Mapping]) -> dict:
    """Return the machine-readable closed-six readiness verdict."""
    errors, scope_decision_required = closed_gap_errors(tasks)
    observed = {str(task.get("gap_category", "")).strip()
                for task in tasks or () if isinstance(task, Mapping)}
    return {
        "passed": not errors,
        "errors": errors,
        "scope_decision_required": scope_decision_required,
        "categories": [category for category in CLOSED_GAP_CATEGORIES
                       if category in observed],
    }
