"""Thin phase-output binding for the existing native submission/lifecycle seam.

A signed terminal observation binds bytes present when the exact native owner
stopped. It is not proof that the child authored every byte, a substantive
Design/Plan verdict, or authority for a successor phase.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from typing import Any, TYPE_CHECKING, cast

if TYPE_CHECKING:
    from . import phase_handoff
elif __package__:
    from . import phase_handoff
else:
    import phase_handoff


OBSERVATION_SCHEMA = "taskplane.phase-output-observation/v1"
VALIDATION_RULE = "phase-output/v1"
_IDENTITY_FIELDS = frozenset({
    "phase", "worker_id", "attempt_id", "handoff_fingerprint", "subject_fingerprint"})
_ARTIFACT_FIELDS = frozenset({"path", "kind", "media_type", "digest", "bytes"})


def _kernel() -> Any:
    if TYPE_CHECKING:
        from . import taskplane_lite as kernel
    elif __package__:
        from . import taskplane_lite as kernel
    else:
        import taskplane_lite as kernel
    return kernel


def _identity(brief: dict[str, Any]) -> dict[str, Any]:
    template = brief.get("result_template")
    protocol = brief.get("protocol")
    if (protocol not in {"repository-phase", "repository-phase-review"} or
            brief.get("phase") not in {"design", "plan"} or not isinstance(template, dict) or
            template.get("phase") != brief["phase"]):
        raise ValueError("phase observed output identity is invalid")
    if protocol == "repository-phase":
        if template.get("schema") != "taskplane.phase-worker-result/v1":
            raise ValueError("phase observed output identity is invalid")
        identity = {key: template.get(key) for key in _IDENTITY_FIELDS}
    else:
        producer = brief.get("producer_contract")
        lens = brief.get("lens")
        if (not isinstance(producer, dict) or not isinstance(lens, str) or
                not re.fullmatch(r"[a-z][a-z0-9-]{0,127}", lens) or
                template.get("schema") != "taskplane.phase-lens-result/v1" or
                template.get("lens") != lens or
                template.get("worker_identity") != brief.get("task_name")):
            raise ValueError("phase observed output lens identity is invalid")
        path = f"{brief['phase']}/lenses/{lens}.json"
        if any(brief.get(key) != path for key in ("output", "result_path")) or \
                brief.get("output_paths") != [path] or producer.get("result_path") != path:
            raise ValueError("phase observed output lens path is invalid")
        identity = {"phase": brief["phase"], "worker_id": lens,
                    **{key: producer.get(key) for key in
                       ("attempt_id", "handoff_fingerprint", "subject_fingerprint")},
                    **{key: template.get(key) for key in
                       ("lens", "worker_identity", "team_plan_fingerprint", "candidate_fingerprint")}}
        if any(not re.fullmatch(r"[0-9a-f]{64}", str(identity[key] or ""))
               for key in ("team_plan_fingerprint", "candidate_fingerprint")):
            raise ValueError("phase observed output lens fingerprints are invalid")
    if any(not re.fullmatch(r"[0-9a-f]{64}", str(identity[key] or ""))
           for key in ("handoff_fingerprint", "subject_fingerprint")) or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", str(identity[key] or ""))
            for key in ("worker_id", "attempt_id")):
        raise ValueError("phase observed output identity is incomplete")
    return {"protocol": protocol, **identity}


def _lifecycle_stage(brief: dict[str, Any]) -> str:
    return "phase-" + str(brief["phase"]) + (
        "-review" if brief["protocol"] == "repository-phase-review" else "")


def bind_output_submission(workspace: str, contract: dict[str, Any],
                           brief: dict[str, Any]) -> dict[str, Any]:
    """Require authored phase files, not the later orchestrator-sealed result."""
    identity = _identity(brief)
    return cast(dict[str, Any], _kernel().bind_submission_contract(
        contract, workspace, task=brief["task_name"], stage=_lifecycle_stage(brief),
        slot=brief["task_slot"], locator={"type": "phase_output", **identity},
        validation_rule=VALIDATION_RULE))


def is_phase_contract(contract: dict[str, Any]) -> bool:
    """Only exact repository-phase owner/lens contracts extend terminal receipts."""
    brief = contract.get("phase_dispatch")
    lifecycle = contract.get("worker_lifecycle")
    if not isinstance(brief, dict) or not isinstance(lifecycle, dict):
        return False
    try:
        _identity(brief)
    except ValueError:
        return False
    return bool(contract.get("worker_scoped") is True and
                lifecycle.get("schema") == _kernel().WORKER_CONTRACT_LIFECYCLE_SCHEMA and
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", str(brief.get("task_slot") or "")) and
                str(brief.get("task_name") or "").strip() and
                lifecycle.get("stage") == _lifecycle_stage(brief) and
                lifecycle.get("expected_task_name") == brief.get("task_name") and
                lifecycle.get("slot") == brief.get("task_slot") == contract.get("task_id"))


def _artifact_specs(brief: dict[str, Any], *, visual: bool = False) -> list[tuple[str, str, str]]:
    phase = str(brief["phase"])
    if brief["protocol"] == "repository-phase-review":
        return [(brief["result_path"], phase + "-lens-" + brief["lens"], "application/json")]
    paths = phase_handoff.phase_output_paths(phase)
    specs = [(paths[0], phase, "application/json"),
             (paths[1], phase + "-narrative", "text/markdown")]
    if visual:
        specs.append(("design/visual.html", "design-visual", "text/html"))
    return specs


def _read_output(workspace: str, relative: str) -> bytes:
    path = os.path.join(os.path.realpath(workspace), relative)
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or os.path.realpath(path) != path:
        raise ValueError("phase output path is unsafe")
    limit = int(_kernel().SUBMISSION_ARTIFACT_MAX_BYTES)
    if info.st_size > limit:
        raise ValueError("phase output exceeds its bound")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit or not raw.strip():
        raise ValueError("phase output is empty or oversized")
    raw.decode("utf-8")
    return raw


def observe_terminal_output(workspace: str, contract: dict[str, Any]) -> dict[str, Any] | None:
    """Snapshot terminal bytes only after the lifecycle caller authenticates owner.

    Missing/invalid files remain explicit failed observations so interruption
    and failure can still release the exact contract without inventing proof.
    """
    if not is_phase_contract(contract):
        return None
    brief = contract["phase_dispatch"]
    identity = _identity(brief)
    observed = {"schema": OBSERVATION_SCHEMA, **identity,
                "status": "corrupt", "artifacts": []}
    binding = contract.get("submission_contract")
    kernel = _kernel()
    if (not isinstance(binding, dict) or binding.get("schema") != kernel.SUBMISSION_CONTRACT_SCHEMA or
            binding.get("required") is not True or binding.get("validation_rule") != VALIDATION_RULE or
            binding.get("workspace_fingerprint") != kernel._workspace_identity_fingerprint(workspace) or
            binding.get("task") != brief["task_name"] or
            binding.get("stage") != _lifecycle_stage(brief) or
            binding.get("slot") != brief["task_slot"] or
            binding.get("locator") != {"type": "phase_output", **identity}):
        return observed
    try:
        phase = str(identity["phase"])
        specs = _artifact_specs(brief)
        primary = _read_output(workspace, specs[0][0])
        payload = json.loads(primary.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("phase machine output must be an object")
        visual = payload.get("visualization")
        if brief["protocol"] == "repository-phase" and phase == "design" and \
                isinstance(visual, dict) and visual.get("required") is True:
            if visual.get("path") != "design/visual.html":
                raise ValueError("required Design visual must use its canonical path")
            specs = _artifact_specs(brief, visual=True)
        artifacts = []
        for index, (path, kind, media) in enumerate(specs):
            raw = primary if index == 0 else _read_output(workspace, path)
            artifacts.append({"path": path, "kind": kind, "media_type": media,
                              "digest": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
    except FileNotFoundError:
        observed["status"] = "missing"
    except (OSError, UnicodeError, ValueError):
        pass
    else:
        observed.update({"status": "observed", "artifacts": artifacts})
    return observed


def submission_status(workspace: str, contract: dict[str, Any],
                      binding: dict[str, Any]) -> dict[str, Any]:
    observed = observe_terminal_output(workspace, contract)
    status = str((observed or {}).get("status") or "corrupt")
    valid = status == "observed"
    return cast(dict[str, Any], _kernel()._submission_result(
        contract, binding, "valid" if valid else status, valid=valid, block=not valid,
        artifact=("exact phase lens result JSON" if (observed or {}).get(
            "protocol") == "repository-phase-review" else
            "exact phase machine and narrative files (plus required Design visual)"),
        recovery="write the required phase files, then return to the orchestrator; "
                 "the worker need not seal result.json"))


def validate_terminal_observation(value: object, brief: dict[str, Any]) -> dict[str, Any]:
    identity = _identity(brief)
    if (not isinstance(value, dict) or set(value) != set(identity) | {"schema", "status", "artifacts"} or
            value.get("schema") != OBSERVATION_SCHEMA or
            any(value.get(key) != item for key, item in identity.items()) or
            value.get("status") not in {"observed", "missing", "corrupt"} or
            not isinstance(value.get("artifacts"), list)):
        raise ValueError("phase observed output is malformed or foreign")
    artifacts = value["artifacts"]
    if value["status"] != "observed":
        if artifacts:
            raise ValueError("failed phase observed output cannot carry completion hashes")
        return copy.deepcopy(value)
    specs = _artifact_specs(brief, visual=len(artifacts) == 3)
    if len(artifacts) != len(specs) or (len(artifacts) == 3 and identity["phase"] != "design"):
        raise ValueError("phase observed output artifact set is invalid")
    for row, (path, kind, media) in zip(artifacts, specs):
        if (not isinstance(row, dict) or set(row) != _ARTIFACT_FIELDS or
                row.get("path") != path or row.get("kind") != kind or row.get("media_type") != media or
                not re.fullmatch(r"[0-9a-f]{64}", str(row.get("digest") or "")) or
                type(row.get("bytes")) is not int or not 0 < row["bytes"] <=
                _kernel().SUBMISSION_ARTIFACT_MAX_BYTES):
            raise ValueError("phase observed output artifact is malformed")
    return copy.deepcopy(value)


def verify_output_observation(terminal_receipt: dict[str, Any], brief: dict[str, Any],
                              references: list[dict[str, Any]]) -> None:
    """After receipt authentication, compare collector refs with terminal bytes."""
    owner = terminal_receipt.get("owner")
    if (terminal_receipt.get("authority") != "host-lifecycle" or not isinstance(owner, dict) or
            not owner.get("session_id") or not owner.get("agent_id") or
            owner.get("task_name") != brief["task_name"] or
            terminal_receipt.get("slot") != brief["task_slot"] or
            terminal_receipt.get("stage") != _lifecycle_stage(brief)):
        raise ValueError("phase observed output has no exact native producer")
    observed = validate_terminal_observation(terminal_receipt.get("phase_output"), brief)
    if observed["status"] != "observed":
        raise ValueError("phase observed output is unavailable")
    actual = [phase_handoff._validate_artifact_shape(row) for row in references]
    expected = [{key: row[key] for key in ("kind", "media_type", "digest", "bytes")}
                for row in observed["artifacts"]]
    if [{key: row[key] for key in ("kind", "media_type", "digest", "bytes")}
            for row in actual] != expected:
        raise ValueError("collected artifacts differ from the phase observed output")
