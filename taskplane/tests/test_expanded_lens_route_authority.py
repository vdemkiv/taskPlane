"""Exact, one-use authority for exceptional expanded lens routes."""

from __future__ import annotations

import copy
import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from taskplane import taskplane_lite as tp


NOW = 1_700_000_000
CONTEXT = "c" * 64
EXTRA_LENSES = ["privacy-compliance", "cost-finops"]
_RSA_N = int(
    "a624209c76ef5732b116b1e648580b29bdb0e7f6a90565fb6f9d83e56fdaea37"
    "d07e815dc554d1ec449dc6cd4642b305ffba3bc608c08d131df0098036c0ad79"
    "4b087969d55a812a8b29768ade83ca8a669dbce71b3acb70cf97554059fa01b2"
    "3fc4b658883e8743a614c2e846d7280cc003c0a7993546c3c5305c08a02f460d"
    "945cfd9d88fb04f5fc952de253a86aeb2c0ecbc39baaf02720a4b7e0ca3da123"
    "c8bc8198e14d2c1f8d0a183508372c3f96338cd5bda4b87583c671bccce3999f"
    "7549e42e338505a1bffc8ee07266770c92a3c34221df7d2dd3851ef939cac3ca"
    "4259be3f2ef022d3bcd75407905215b0b8d7490aa725618df134590d9249cebd",
    16,
)
_RSA_D = int(
    "191c9f503efadca575165ed3d58df73bfb27bcdbefbeb8e42901f8306af87e0b"
    "eb27dfe25e43fc89d772389d08d8668a4ad5a998bc746c2e5e494c8a545c49ac"
    "2a6ef0b9122e40953f5d0845a3adec64806fa9a08de154642c006dfa90cf04c8"
    "1e32dbb3e47dfd0078df2cf9a2517d8475d66b5d79bf0f7fe23375c9b8fa843e"
    "255b6b72211ffdb8950fa2d558a5b79a52572a0b1de9634f5f46b3a13c0fc5f5"
    "03cc7ceec52d15f17bf516854055817f3372b273ffdea069fbc55373ef3587a257"
    "6a491892617f54c9f3866d3640a39666a4e3b1372a516fa3baaa5c5fea4d3a0a"
    "63a75619c2c3cafe60e5f0085127fc4c08a21688910e4ed512c3ab95fb2389",
    16,
)
_RSA_E = 65_537
_RSA_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _workspace(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def _route_values(**overrides: object) -> dict[str, object]:
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
    return values


def _approval_key_id() -> str:
    value = {
        "algorithm": "rsa-pkcs1v15-sha256",
        "modulus": format(_RSA_N, "x"),
        "exponent": _RSA_E,
    }
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _workspace_locator_path(workspace: Path) -> Path:
    relative = subprocess.run(
        ["git", "rev-parse", "--git-path", "taskplane/workspace.json"],
        cwd=workspace, check=True, capture_output=True, text=True,
    ).stdout.strip()
    locator = Path(relative)
    if not locator.is_absolute():
        locator = workspace / locator
    return locator


def _control_plane_state(workspace: Path) -> Path:
    """Provision the test host's protected Git locator and external state."""
    home = (workspace.parent / f".control-plane-{workspace.name}").resolve()
    paths = {
        name: str(home / name)
        for name in ("state", "graph", "evidence", "lenses", "artifacts")
    }
    for path in paths.values():
        Path(path).mkdir(parents=True, exist_ok=True)
    locator = _workspace_locator_path(workspace)
    locator.parent.mkdir(parents=True, exist_ok=True)
    tp.atomic_write_json(str(locator), {
        "schema": "taskplane.workspace/v1",
        "run_id": "run-expanded-route-test",
        "repo_id": f"local/{workspace.name}",
        "repository_key": f"local-{workspace.name}",
        "checkout": str(workspace.resolve()),
        "primary_checkout": str(workspace.resolve()),
        "home": str(home),
        "paths": paths,
    }, sort_keys=True)
    return Path(paths["state"])


def _provision_control_plane_verifier(workspace: Path) -> Path:
    control = _control_plane_state(workspace) / "control"
    control.mkdir(exist_ok=True)
    path = control / "expanded-lens-route-approval-verifier.json"
    if path.exists():
        return path
    tp.atomic_write_json(str(path), {
        "schema": "taskplane.expanded-lens-route-approval-verifier/v1",
        "key_id": _approval_key_id(),
        "algorithm": "rsa-pkcs1v15-sha256",
        "modulus": format(_RSA_N, "x"),
        "exponent": _RSA_E,
    }, sort_keys=True)
    os.chmod(path, 0o600)
    return path


def _proof_signature(value: dict[str, object]) -> str:
    unsigned = {key: item for key, item in value.items()
                if key != "signature"}
    encoded = json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode()
    digest_info = _RSA_SHA256_PREFIX + hashlib.sha256(encoded).digest()
    size = (_RSA_N.bit_length() + 7) // 8
    message = b"\x00\x01" + (b"\xff" * (size - len(digest_info) - 3)) + \
        b"\x00" + digest_info
    signature = pow(int.from_bytes(message, "big"), _RSA_D, _RSA_N)
    return base64.b64encode(signature.to_bytes(size, "big")).decode("ascii")


def _approval(workspace: Path, **overrides: object) -> dict:
    values = _route_values(**overrides)
    _provision_control_plane_verifier(workspace)
    proof_identity = hashlib.sha256(json.dumps({
        key: values[key] for key in (
            "stage", "target", "context_fingerprint", "extra_lens_ids",
            "expected_cost", "policy_version", "action_id", "now",
        )
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    approval = {
        "schema": "taskplane.expanded-lens-route-control-plane-proof/v1",
        "key_id": _approval_key_id(),
        "proof_id": "approval-" + proof_identity,
        "workspace_fingerprint": tp._workspace_identity_fingerprint(
            str(workspace)),
        **{key: values[key] for key in (
            "stage", "target", "context_fingerprint", "extra_lens_ids",
            "expected_cost", "policy_version", "action_id",
        )},
        "approved_by": "human:operator",
        "approval_receipt_id": "host-receipt-expanded-LR-03-1",
        "issued_at": values["now"],
        "expires_at": int(values["now"]) + int(values["ttl_seconds"]),
    }
    approval["signature"] = _proof_signature(approval)
    return approval


def _issue(workspace: Path, **overrides: object) -> dict:
    values = _route_values(**overrides)
    proof = values.pop("approval_proof", None)
    if proof is None:
        proof = _approval(workspace, **overrides)
    return tp.issue_expanded_lens_route_action(
        str(workspace), approval_proof=proof, **values)


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
    assert receipt["approved_by"] == "human:operator"
    assert receipt["approval_receipt_id"] == \
        "host-receipt-expanded-LR-03-1"
    assert receipt["approval_fingerprint"] == \
        tp.expanded_lens_route_action_fingerprint(action)
    assert receipt["action_fingerprint"] == \
        tp.expanded_lens_route_action_fingerprint(action)
    assert "signature" not in receipt

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
        ("approved_by", "human:attacker", {}),
        ("approval_receipt_id", "forged-receipt", {}),
        ("approval_fingerprint", "0" * 64, {}),
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
        "schema", "key_id", "proof_id", "action_id",
        "workspace_fingerprint",
        "stage", "target", "context_fingerprint", "extra_lens_ids",
        "expected_cost", "policy_version", "issued_at", "expires_at",
        "approved_by", "approval_receipt_id", "signature",
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


def test_worker_cannot_self_issue_after_clearing_task_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "repo")
    values = _route_values()
    monkeypatch.setenv("TASKPLANE_TASK", "task_worker_attempt")
    monkeypatch.delenv("TASKPLANE_TASK")
    _provision_control_plane_verifier(workspace)
    forged = _approval(workspace)
    forged["approved_by"] = "human:attacker"
    forged["approval_receipt_id"] = "caller-chosen"
    forged["signature"] = base64.b64encode(b"0" * 256).decode("ascii")

    assert not hasattr(
        tp, "control_plane_expanded_lens_route_approval_attestation")
    with pytest.raises(tp.StateError, match="proof signature"):
        tp.issue_expanded_lens_route_action(
            str(workspace), approval_proof=forged,
            **values,
        )

    assert not (workspace / ".taskplane" /
                "expanded-lens-route-authority.json").exists()


def test_worker_cannot_retrieve_or_use_downstream_action_signer(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo")
    action = _issue(workspace)
    public_verifier = tp._expanded_lens_route_approval_verifier(str(workspace))
    forged = copy.deepcopy(action)
    forged["action_id"] = "expanded-LR-03-forged"

    assert not hasattr(tp, "_expanded_lens_route_authority")
    assert not hasattr(tp, "_expanded_lens_route_signature")
    assert set(public_verifier) == {
        "key_id", "algorithm", "modulus", "exponent"}
    assert "secret" not in public_verifier
    with pytest.raises(tp.StateError, match="signature|mismatches"):
        tp.consume_expanded_lens_route_action(
            str(workspace), forged,
            **_expected(action_id="expanded-LR-03-forged"),
        )


def test_worker_workspace_cannot_replace_external_verifier_anchor(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo")
    proof = _approval(workspace)
    external_anchor = Path(
        tp._expanded_lens_route_approval_verifier_path(str(workspace)))
    workspace_anchor = workspace / ".taskplane" / \
        "expanded-lens-route-approval-verifier.json"
    workspace_anchor.parent.mkdir(exist_ok=True)
    tp.atomic_write_json(str(workspace_anchor), {
        "schema": "taskplane.expanded-lens-route-approval-verifier/v1",
        "key_id": "0" * 64,
        "algorithm": "rsa-pkcs1v15-sha256",
        "modulus": "f" * 512,
        "exponent": 65_537,
    }, sort_keys=True)
    os.chmod(workspace_anchor, 0o600)

    assert os.path.commonpath((workspace.resolve(), external_anchor.resolve())) \
        != str(workspace.resolve())
    action = tp.issue_expanded_lens_route_action(
        str(workspace), approval_proof=proof, **_route_values())
    assert action == proof
    assert external_anchor != workspace_anchor

    unmanaged = _workspace(tmp_path / "unmanaged")
    unmanaged_proof = _approval(unmanaged)
    trusted_anchor = Path(
        tp._expanded_lens_route_approval_verifier_path(str(unmanaged)))
    unmanaged_anchor = unmanaged / ".taskplane" / \
        "expanded-lens-route-approval-verifier.json"
    unmanaged_anchor.parent.mkdir(exist_ok=True)
    tp.atomic_write_json(
        str(unmanaged_anchor), json.loads(trusted_anchor.read_text()),
        sort_keys=True)
    os.chmod(unmanaged_anchor, 0o600)
    _workspace_locator_path(unmanaged).unlink()

    with pytest.raises(tp.StateError, match="locator is missing"):
        tp.issue_expanded_lens_route_action(
            str(unmanaged), approval_proof=unmanaged_proof,
            **_route_values())


def test_control_plane_proof_tamper_and_replay_fail_closed(
    tmp_path: Path,
) -> None:
    tamper_workspace = _workspace(tmp_path / "tamper")
    proof = _approval(tamper_workspace)
    tampered = copy.deepcopy(proof)
    tampered["expected_cost"] = 2_401
    with pytest.raises(tp.StateError, match="proof signature"):
        tp.issue_expanded_lens_route_action(
            str(tamper_workspace), approval_proof=tampered,
            **_route_values())

    replay_workspace = _workspace(tmp_path / "replay")
    replay_proof = _approval(replay_workspace)
    first = tp.issue_expanded_lens_route_action(
        str(replay_workspace), approval_proof=replay_proof,
        **_route_values())
    assert first == replay_proof
    with pytest.raises(tp.StateError, match="proof.*replay|already used"):
        tp.issue_expanded_lens_route_action(
            str(replay_workspace), approval_proof=replay_proof,
            **_route_values())


def test_expiry_crossing_under_consumption_lock_fails_closed(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo")
    action = _issue(workspace, ttl_seconds=300)

    with mock.patch.object(
        tp._time, "time", side_effect=[NOW + 299, NOW + 300],
    ):
        with pytest.raises(tp.StateError, match="stale|expired"):
            tp.consume_expanded_lens_route_action(
                str(workspace), action, **_expected(now=None))

    assert not tp.expanded_lens_route_action_consumed(
        str(workspace), "expanded-LR-03-1")


def test_permission_hardening_failure_rejects_external_trust_anchor(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "repo")
    proof = _approval(workspace)
    verifier_path = Path(
        tp._expanded_lens_route_approval_verifier_path(str(workspace)))
    os.chmod(verifier_path, 0o644)

    with pytest.raises(tp.StateError, match="trusted permissions|mode 0600"):
        tp.issue_expanded_lens_route_action(
            str(workspace), approval_proof=proof,
            **_route_values())

    assert not (workspace / ".taskplane" /
                "expanded-lens-route-authority.json").exists()


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

    _provision_control_plane_verifier(other)
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
