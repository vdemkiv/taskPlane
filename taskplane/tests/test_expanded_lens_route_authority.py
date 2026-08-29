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


def _receipt(request: dict) -> dict:
    action = {
        "schema": "taskplane.expanded-lens-route-action/v1",
        "key_id": "1" * 64,
        "repository_source_path":
            "taskplane/expanded_route_authority_provider.py",
        "repository_commit": "a" * 40,
        "source_sha256": "2" * 64,
        "package_sha256": "3" * 64,
        "provider_protocol_version":
            "taskplane.expanded-route-authority-provider/v1",
        **{key: value for key, value in request.items() if key != "schema"},
        "issued_at": 1_700_000_000,
        "expiry": 1_700_000_300,
        "approver_identity": "human:operator",
        "approver_key_fingerprint": "4" * 64,
        "approval_receipt_digest": "5" * 64,
        "seal": "6" * 64,
    }
    return {
        "schema": "taskplane.expanded-lens-route-consumption/v2",
        "provider_protocol_version":
            "taskplane.expanded-route-authority-provider/v1",
        "locator_fingerprint": "7" * 64,
        "action": action,
        "action_fingerprint": hashlib.sha256(_canonical(action)).hexdigest(),
        "approval_receipt_digest": "5" * 64,
        "consumed_at": 1_700_000_001,
        "recovered": False,
        "seal": "8" * 64,
    }


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


def test_provider_receipt_projection_preserves_exact_request_binding(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo")
    request = _request(workspace)
    receipt = _receipt(request)

    projected = tp.project_expanded_lens_route_provider_receipt(
        request, receipt)

    assert projected == receipt
    assert projected is not receipt
    assert projected["action"]["action_id"] == request["action_id"]
    assert projected["action"]["workspace"] == request["workspace"]


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("action", "stage"), "plan"),
        (("action", "target"), "other"),
        (("action", "exact_ordered_lens_ids"), ["cost-finops"]),
        (("action", "estimated_cost"), 99),
        (("action", "approval_receipt_digest"), "9" * 64),
        (("action_fingerprint",), "9" * 64),
        (("provider_protocol_version",), "provider/v0"),
        (("seal",), "short"),
    ],
)
def test_provider_receipt_projection_rejects_binding_or_schema_mutation(
    tmp_path: Path, path: tuple[str, ...], replacement: object,
) -> None:
    workspace = _workspace(tmp_path / "repo")
    request = _request(workspace)
    receipt = _receipt(request)
    target = receipt
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement

    with pytest.raises(ValueError):
        tp.project_expanded_lens_route_provider_receipt(request, receipt)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("key_id", "short"),
        ("repository_source_path", "taskplane/worker_chosen_provider.py"),
        ("repository_commit", "short"),
        ("approver_identity", ""),
    ],
)
def test_provider_receipt_projection_rejects_malformed_provider_provenance(
    tmp_path: Path, field: str, replacement: object,
) -> None:
    workspace = _workspace(tmp_path / "repo")
    request = _request(workspace)
    receipt = _receipt(request)
    receipt["action"][field] = replacement
    receipt["action_fingerprint"] = hashlib.sha256(
        _canonical(receipt["action"])).hexdigest()

    with pytest.raises(ValueError):
        tp.project_expanded_lens_route_provider_receipt(request, receipt)


def test_projection_does_not_accept_extra_clear_scope_or_floor_authority(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo")
    request = _request(workspace)
    receipt = _receipt(request)
    for key in ("clear", "scope", "mandatory_floor"):
        candidate = json.loads(json.dumps(receipt))
        candidate[key] = True
        with pytest.raises(ValueError):
            tp.project_expanded_lens_route_provider_receipt(
                request, candidate)
