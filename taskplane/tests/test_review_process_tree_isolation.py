"""Regression coverage for process-tree isolation in dynamic review.

The disabled Git remote and pre-push hook are useful defence in depth, but
neither covers an explicit URL combined with ``--no-verify``.  The security
boundary is therefore the host isolation launcher, and its receipt must cover
the entire descendant tree and deny network access.
"""
from pathlib import Path
import hashlib
import json

import pytest

from taskplane.command_adapters import CommandAdapter, HostLaunch
from taskplane.command_runtime import CommandRuntime
from taskplane.review_session import create_session, record_consent


def _session():
    return record_consent(create_session(
        run_id="review-process-tree",
        target={"fingerprint": "a" * 64, "revision": "head"},
        available_actions=[
            {"id": "dynamic_validation", "non_destructive": True},
        ],
    ), response="dynamic", actor="human")


def _sandbox(root: Path):
    return {
        "schema": "taskplane.review-validation-sandbox/v1",
        "sandbox_id": "process-tree-sandbox",
        "path": str(root),
        "disposable": True,
        "push_disabled": True,
    }


def _receipt(policy, **overrides):
    receipt = {
        "schema": "taskplane.review-isolation-receipt/v1",
        "network": "denied",
        "scope": "complete-process-tree",
        "mechanism": "test-host-process-sandbox",
        "policy_fingerprint": hashlib.sha256(json.dumps(
            policy, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest(),
    }
    receipt.update(overrides)
    return receipt


def _adapter(tmp_path, isolation_launcher=None):
    return CommandAdapter(
        host="codex",
        runtime=CommandRuntime(
            str(tmp_path / "runtime"), workspace="repo",
            authorization="human"),
        launcher=lambda command, cwd: HostLaunch(binding={"pid": 1}),
        review_isolation_launcher=isolation_launcher,
    )


def test_explicit_url_push_no_verify_is_rejected_before_launch(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    launches = []
    adapter = _adapter(
        tmp_path,
        lambda command, cwd, policy: launches.append(command),
    )

    with pytest.raises(ValueError, match="push-disabled"):
        adapter.launch_review_validation(
            ["git", "push", "https://example.test/repo.git", "HEAD",
             "--no-verify"],
            cwd=str(root), session=_session(), sandbox=_sandbox(root),
        )

    assert launches == []


def test_descendant_push_is_confined_by_complete_tree_network_policy(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    observed = []

    def isolate(command, cwd, policy):
        observed.append((command, cwd, policy))
        return HostLaunch(binding={"pid": 2}, isolation=_receipt(policy))

    adapter = _adapter(tmp_path, isolate)
    command = [
        "python3", "-c",
        "import subprocess; subprocess.run([\"git\", \"push\", "
        "\"https://example.test/repo.git\", \"HEAD\", \"--no-verify\"])",
    ]

    handle = adapter.launch_review_validation(
        command, cwd=str(root), session=_session(), sandbox=_sandbox(root),
    )

    assert observed == [(command, str(root.resolve()), {
        "schema": "taskplane.review-isolation-policy/v1",
        "network": "deny",
        "scope": "complete-process-tree",
    })]
    assert adapter.snapshot(handle)["review_sandbox"][
        "isolation_fingerprint"]


@pytest.mark.parametrize("receipt_change", [
    {"network": "allowed"},
    {"scope": "parent-only"},
    {"policy_fingerprint": "replayed-receipt"},
    {"mechanism": ""},
])
def test_incomplete_or_replayed_isolation_receipt_fails_closed(
        tmp_path, receipt_change):
    root = tmp_path / "sandbox"
    root.mkdir()

    def isolate(command, cwd, policy):
        return HostLaunch(
            binding={"pid": 3},
            isolation=_receipt(policy, **receipt_change),
        )

    adapter = _adapter(tmp_path, isolate)

    with pytest.raises(ValueError, match="process-tree isolation receipt"):
        adapter.launch_review_validation(
            ["npm", "test"], cwd=str(root), session=_session(),
            sandbox=_sandbox(root),
        )


def test_normal_build_and_local_disposable_writes_remain_allowed(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    observed = []

    def isolate(command, cwd, policy):
        observed.append((command, cwd, policy))
        return HostLaunch(binding={"pid": 4}, isolation=_receipt(policy))

    adapter = _adapter(tmp_path, isolate)
    command = ["npm", "run", "build"]

    adapter.launch_review_validation(
        command, cwd=str(root), session=_session(), sandbox=_sandbox(root),
    )

    assert observed[0][0] == command
    assert observed[0][1] == str(root.resolve())
    # Filesystem writes are deliberately not denied: the checkout is a
    # disposable copy and build/test output is part of dynamic evidence.
    assert "filesystem" not in observed[0][2]
