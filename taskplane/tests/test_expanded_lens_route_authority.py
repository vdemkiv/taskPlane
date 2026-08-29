"""Exact, one-use authority for exceptional expanded lens routes."""

from __future__ import annotations

import copy
import shutil
import subprocess
from pathlib import Path

import pytest

from taskplane import taskplane_lite as tp


NOW = 1_700_000_000
CONTEXT = "c" * 64
EXTRA_LENSES = ["privacy-compliance", "cost-finops"]


def _workspace(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def _issue(workspace: Path, **overrides: object) -> dict:
    values: dict[str, object] = {
        "stage": "evaluate",
        "target": "LR-03@candidate",
        "context_fingerprint": CONTEXT,
        "extra_lens_ids": EXTRA_LENSES,
        "expected_cost": 2_400,
        "policy_version": "focused-routing/v1",
        "action_id": "expanded-LR-03-1",
        "now": NOW,
        "ttl_seconds": 300,
    }
    values.update(overrides)
    return tp.issue_expanded_lens_route_action(str(workspace), **values)


def _expected(**overrides: object) -> dict:
    values: dict[str, object] = {
        "stage": "evaluate",
        "target": "LR-03@candidate",
        "context_fingerprint": CONTEXT,
        "extra_lens_ids": EXTRA_LENSES,
        "expected_cost": 2_400,
        "policy_version": "focused-routing/v1",
        "action_id": "expanded-LR-03-1",
        "now": NOW + 1,
    }
    values.update(overrides)
    return values


def test_exact_action_verifies_and_consumes_once(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo")
    action = _issue(workspace)

    verified = tp.verify_expanded_lens_route_action(
        str(workspace), action, **_expected())
    receipt = tp.consume_expanded_lens_route_action(
        str(workspace), action, **_expected())

    assert verified == action
    assert receipt["schema"] == \
        "taskplane.expanded-lens-route-consumption/v1"
    assert receipt["action_id"] == "expanded-LR-03-1"
    assert receipt["stage"] == "evaluate"
    assert receipt["target"] == "LR-03@candidate"
    assert receipt["context_fingerprint"] == CONTEXT
    assert receipt["extra_lens_ids"] == EXTRA_LENSES
    assert receipt["expected_cost"] == 2_400
    assert receipt["policy_version"] == "focused-routing/v1"
    assert receipt["action_fingerprint"] == \
        tp.expanded_lens_route_action_fingerprint(action)
    assert receipt["signature"]

    with pytest.raises(tp.StateError, match="already consumed|replay"):
        tp.consume_expanded_lens_route_action(
            str(workspace), action, **_expected(now=NOW + 2))


@pytest.mark.parametrize(
    ("field", "mutated", "expected_override"),
    [
        ("stage", "plan", {}),
        ("target", "LR-04@candidate", {}),
        ("context_fingerprint", "d" * 64, {}),
        ("extra_lens_ids", ["privacy-compliance"], {}),
        ("expected_cost", 2_401, {}),
        ("policy_version", "focused-routing/v2", {}),
        ("action_id", "expanded-LR-03-2", {}),
        ("signature", "0" * 64, {}),
        ("expires_at", NOW, {}),
        ("stage", "evaluate", {"stage": "plan"}),
        ("target", "LR-03@candidate", {"target": "other-target"}),
        (
            "context_fingerprint",
            CONTEXT,
            {"context_fingerprint": "e" * 64},
        ),
        (
            "extra_lens_ids",
            EXTRA_LENSES,
            {"extra_lens_ids": list(reversed(EXTRA_LENSES))},
        ),
        ("expected_cost", 2_400, {"expected_cost": 999}),
        (
            "policy_version",
            "focused-routing/v1",
            {"policy_version": "focused-routing/v2"},
        ),
        (
            "action_id",
            "expanded-LR-03-1",
            {"action_id": "another-action"},
        ),
    ],
)
def test_action_or_expected_identity_mutation_fails_closed(
    tmp_path: Path,
    field: str,
    mutated: object,
    expected_override: dict[str, object],
) -> None:
    workspace = _workspace(tmp_path / "repo")
    action = _issue(workspace)
    candidate = copy.deepcopy(action)
    candidate[field] = mutated

    with pytest.raises(tp.StateError):
        tp.verify_expanded_lens_route_action(
            str(workspace), candidate,
            **_expected(**expected_override),
        )


def test_schema_is_closed_and_cannot_weaken_general_enforcement(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo")
    action = _issue(workspace)
    assert set(action) == {
        "schema", "key_id", "action_id", "workspace_fingerprint",
        "stage", "target", "context_fingerprint", "extra_lens_ids",
        "expected_cost", "policy_version", "issued_at", "expires_at",
        "signature",
    }
    assert not ({"clear", "scope", "mandatory_floor", "contract"}
                & set(action))

    for field, value in (
        ("clear", True),
        ("mandatory_floor", "disabled"),
        ("extra_lens_ids", EXTRA_LENSES + ["security"]),
    ):
        candidate = copy.deepcopy(action)
        candidate[field] = value
        with pytest.raises(tp.StateError):
            tp.consume_expanded_lens_route_action(
                str(workspace), candidate, **_expected())

    assert not tp.expanded_lens_route_action_consumed(
        str(workspace), "expanded-LR-03-1")


@pytest.mark.parametrize(
    "overrides",
    [
        {"stage": "build"},
        {"stage": "fix"},
        {"extra_lens_ids": []},
        {"extra_lens_ids": ["security", "security"]},
        {"extra_lens_ids": ["not a lens"]},
        {"context_fingerprint": "short"},
        {"expected_cost": 0},
        {"expected_cost": -1},
        {"policy_version": ""},
        {"action_id": ""},
        {"ttl_seconds": 0},
        {"ttl_seconds": 3_601},
    ],
)
def test_issuer_rejects_broadened_or_malformed_authority(
    tmp_path: Path, overrides: dict[str, object],
) -> None:
    workspace = _workspace(tmp_path / "repo")
    with pytest.raises(tp.StateError):
        _issue(workspace, **overrides)


def test_expiry_and_workspace_binding_fail_without_consuming(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo")
    other = _workspace(tmp_path / "other")
    action = _issue(workspace, ttl_seconds=10)

    with pytest.raises(tp.StateError, match="stale|expired"):
        tp.consume_expanded_lens_route_action(
            str(workspace), action, **_expected(now=NOW + 11))
    assert not tp.expanded_lens_route_action_consumed(
        str(workspace), "expanded-LR-03-1")

    other_control = other / ".taskplane"
    other_control.mkdir()
    shutil.copy2(
        workspace / ".taskplane" / "expanded-lens-route-authority.json",
        other_control / "expanded-lens-route-authority.json",
    )
    with pytest.raises(tp.StateError, match="another workspace"):
        tp.verify_expanded_lens_route_action(
            str(other), action, **_expected())


def test_action_id_cannot_be_reissued_to_bypass_replay(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "repo")
    first = _issue(workspace)
    tp.consume_expanded_lens_route_action(
        str(workspace), first, **_expected())
    replacement = _issue(
        workspace,
        context_fingerprint="f" * 64,
        extra_lens_ids=["security"],
        expected_cost=1_000,
        now=NOW + 2,
    )

    with pytest.raises(tp.StateError, match="already consumed|replay"):
        tp.consume_expanded_lens_route_action(
            str(workspace), replacement,
            **_expected(
                context_fingerprint="f" * 64,
                extra_lens_ids=["security"],
                expected_cost=1_000,
                now=NOW + 3,
            ),
        )
