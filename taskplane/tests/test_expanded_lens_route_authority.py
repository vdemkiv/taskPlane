"""Request-only worker adapter for protected expanded-route authority."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import subprocess

import pytest

from taskplane import taskplane_lite as tp


CONTEXT = "c" * 64
EXTRA_LENSES = ["privacy-compliance", "cost-finops"]


def _workspace(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def _request(workspace: Path, **overrides: object) -> dict:
    values: dict[str, object] = {
        "stage": "evaluate",
        "target": "LR-03@candidate",
        "context_fingerprint": CONTEXT,
        "extra_lens_ids": EXTRA_LENSES,
        "expected_cost": 2_400,
        "policy_version": "focused-routing/v1",
        "catalog_version": "catalog/v1",
        "action_id": "expanded-LR-03-1",
    }
    values.update(overrides)
    return tp.build_expanded_lens_route_authority_request(
        str(workspace), **values)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")


def test_request_is_closed_deterministic_and_contains_no_authority_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "repo")
    first = _request(workspace)
    monkeypatch.setenv(
        "TASKPLANE_EXPANDED_ROUTE_PROVIDER", "/worker/chosen/provider.py")
    monkeypatch.setenv("TASKPLANE_EXPANDED_ROUTE_CLOCK", "0")
    second = _request(workspace)

    assert first == second
    assert set(first) == {
        "schema", "workspace", "stage", "target", "context_fingerprint",
        "exact_ordered_lens_ids", "estimated_cost", "policy_version",
        "catalog_version", "action_id",
    }
    assert first["workspace"] == tp._workspace_identity_fingerprint(
        str(workspace))
    assert first["exact_ordered_lens_ids"] == EXTRA_LENSES
    assert first["estimated_cost"] == 2_400
    assert not ({
        "provider", "locator", "clock", "now", "verifier", "issuer", "secret",
        "custody", "approval", "consume",
    } & set(first))
    assert tp.expanded_lens_route_provider_request_fingerprint(first) == \
        hashlib.sha256(_canonical(first)).hexdigest()


@pytest.mark.parametrize(
    "overrides",
    [
        {"stage": "build"},
        {"stage": "fix"},
        {"target": ""},
        {"context_fingerprint": "short"},
        {"extra_lens_ids": []},
        {"extra_lens_ids": ["security", "security"]},
        {"extra_lens_ids": ["not a lens"]},
        {"expected_cost": 0},
        {"expected_cost": -1},
        {"policy_version": ""},
        {"catalog_version": ""},
        {"action_id": ""},
    ],
)
def test_request_rejects_broadened_or_malformed_route(
    tmp_path: Path, overrides: dict[str, object],
) -> None:
    workspace = _workspace(tmp_path / "repo")
    with pytest.raises(ValueError):
        _request(workspace, **overrides)


def test_worker_module_has_no_issuance_verification_or_consumption_authority(
) -> None:
    for name in (
        "issue_expanded_lens_route_action",
        "verify_expanded_lens_route_action",
        "consume_expanded_lens_route_action",
        "expanded_lens_route_action_consumed",
        "_expanded_lens_route_security_time",
        "_expanded_lens_route_rsa_signature_valid",
        "_expanded_lens_route_approval_verifier",
        "_expanded_lens_route_authority",
        "ExpandedRouteProviderClient",
        "ExpandedRouteProviderReceipt",
        "project_expanded_lens_route_provider_receipt",
    ):
        assert not hasattr(tp, name)

    signature = inspect.signature(
        tp.build_expanded_lens_route_authority_request)
    assert not ({
        "provider", "provider_path", "locator", "clock", "now", "verifier",
        "issuer", "approval_receipt",
    } & set(signature.parameters))
    source = inspect.getsource(tp)
    assert "import expanded_route_authority_provider" not in source
    assert "from .expanded_route_authority_provider" not in source
    assert "TASKPLANE_EXPANDED_ROUTE_PROVIDER" not in source
    assert "TASKPLANE_EXPANDED_ROUTE_CLOCK" not in source
    assert "import terminal_truth" not in source
    assert "from .terminal_truth" not in source
