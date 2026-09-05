"""Native binding facade over dependency-neutral phase output observations.

The lifecycle kernel uses phase_output directly; this facade serves higher
level dispatch/collection without making the observer import its caller.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING, cast

if TYPE_CHECKING or __package__:
    from . import phase_output, taskplane_lite as kernel
    from .phase_output import (
        OBSERVATION_SCHEMA as OBSERVATION_SCHEMA,
        VALIDATION_RULE as VALIDATION_RULE,
        is_phase_contract as is_phase_contract,
        validate_terminal_observation as validate_terminal_observation,
        verify_output_observation as verify_output_observation,
    )
else:
    import phase_output
    import taskplane_lite as kernel
    from phase_output import (
        OBSERVATION_SCHEMA as OBSERVATION_SCHEMA,
        VALIDATION_RULE as VALIDATION_RULE,
        is_phase_contract as is_phase_contract,
        validate_terminal_observation as validate_terminal_observation,
        verify_output_observation as verify_output_observation,
    )


def bind_output_submission(workspace: str, contract: dict[str, Any],
                           brief: dict[str, Any]) -> dict[str, Any]:
    """Require authored phase files, not the later orchestrator-sealed result."""
    identity = phase_output._identity(brief)
    return cast(dict[str, Any], kernel.bind_submission_contract(
        contract, workspace, task=brief["task_name"], stage=phase_output._lifecycle_stage(brief),
        slot=brief["task_slot"], locator={"type": "phase_output", **identity},
        validation_rule=VALIDATION_RULE))


def observe_terminal_output(workspace: str, contract: dict[str, Any]) -> dict[str, Any] | None:
    return phase_output.observe_terminal_output(
        workspace, contract, workspace_fingerprint=kernel._workspace_identity_fingerprint(workspace))


def submission_status(workspace: str, contract: dict[str, Any],
                      binding: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], kernel._phase_submission_status(workspace, contract, binding))
