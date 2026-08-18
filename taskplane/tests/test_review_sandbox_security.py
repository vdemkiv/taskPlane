from pathlib import Path
import hashlib
import json

import pytest

from taskplane.command_adapters import CommandAdapter, HostLaunch
from taskplane.command_runtime import CommandRuntime
from taskplane.review_session import (
    ReviewSessionError,
    create_session,
    record_consent,
    validation_evidence,
)


def _session():
    session = create_session(
        run_id="review-1",
        target={"fingerprint": "a" * 64, "revision": "head"},
        available_actions=[
            {"id": "dynamic_validation", "non_destructive": True},
        ],
    )
    return record_consent(session, response="dynamic", actor="human")


def _adapter(tmp_path, launches):
    runtime = CommandRuntime(
        str(tmp_path / "runtime"), workspace="repo", authorization="human")
    return CommandAdapter(
        host="codex",
        runtime=runtime,
        launcher=lambda command, cwd: (
            launches.append((command, cwd)) or HostLaunch(binding={"pid": 1})
        ),
        review_isolation_launcher=lambda command, cwd, policy: (
            launches.append((command, cwd)) or HostLaunch(
                binding={"pid": 1},
                isolation={
                    "schema": "taskplane.review-isolation-receipt/v1",
                    "network": "denied",
                    "scope": "complete-process-tree",
                    "mechanism": "test-process-sandbox",
                    "policy_fingerprint": hashlib.sha256(json.dumps(
                        policy, sort_keys=True, separators=(",", ":"))
                        .encode("utf-8")).hexdigest(),
                },
            )
        ),
    )


def _sandbox(root: Path):
    return {
        "schema": "taskplane.review-validation-sandbox/v1",
        "sandbox_id": "sandbox-1",
        "path": str(root),
        "disposable": True,
        "push_disabled": True,
    }


def test_validation_launch_is_bound_to_verified_sandbox_root(tmp_path):
    root = tmp_path / "sandbox"
    child = root / "client"
    child.mkdir(parents=True)
    launches = []
    adapter = _adapter(tmp_path, launches)

    handle = adapter.launch_review_validation(
        ["npm", "test"], cwd=str(child), session=_session(),
        sandbox=_sandbox(root),
    )

    assert launches == [(["npm", "test"], str(child.resolve()))]
    binding = adapter.snapshot(handle)["review_sandbox"]
    assert binding["sandbox_id"] == "sandbox-1"
    assert binding["push_disabled"] is True
    assert binding["root_fingerprint"]
    assert binding["isolation_fingerprint"]
    assert str(root) not in str(binding)


@pytest.mark.parametrize("cwd", ["outside", "symlink"])
def test_validation_launch_rejects_cwd_outside_real_sandbox(tmp_path, cwd):
    root = tmp_path / "sandbox"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    selected = outside
    if cwd == "symlink":
        selected = root / "escape"
        selected.symlink_to(outside, target_is_directory=True)
    launches = []
    adapter = _adapter(tmp_path, launches)

    with pytest.raises(ValueError, match="cwd.*sandbox"):
        adapter.launch_review_validation(
            ["npm", "test"], cwd=str(selected), session=_session(),
            sandbox=_sandbox(root),
        )
    assert launches == []


def test_validation_launch_rejects_unbound_boolean_only_claim(tmp_path):
    launches = []
    adapter = _adapter(tmp_path, launches)

    with pytest.raises(ValueError, match="sandbox root"):
        adapter.launch_review_validation(
            ["npm", "test"], cwd=str(tmp_path), session=_session(),
            sandbox={"sandbox_id": "box", "disposable": True,
                     "push_disabled": True},
        )
    assert launches == []


@pytest.mark.parametrize("command", [
    ["git", "push", "https://example.test/repo", "HEAD", "--no-verify"],
    ["git", "send-pack", "ssh://example.test/repo", "HEAD"],
    ["env", "TOKEN=x", "git", "push", "origin", "HEAD"],
    ["sh", "-c", "git push https://example.test/repo HEAD --no-verify"],
    ["git", "-c", "alias.x=push", "x", "https://example.test/repo", "HEAD"],
    ["git", "x", "https://example.test/repo", "HEAD"],
])
def test_validation_transport_rejects_push_and_shell_bypasses(tmp_path, command):
    root = tmp_path / "sandbox"
    root.mkdir()
    launches = []
    adapter = _adapter(tmp_path, launches)

    with pytest.raises(ValueError, match="push-disabled"):
        adapter.launch_review_validation(
            command, cwd=str(root), session=_session(), sandbox=_sandbox(root),
        )
    assert launches == []


def test_validation_transport_allows_explicit_read_only_git_builtin(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    launches = []
    adapter = _adapter(tmp_path, launches)

    adapter.launch_review_validation(
        ["git", "status", "--short"], cwd=str(root), session=_session(),
        sandbox=_sandbox(root),
    )

    assert launches == [(["git", "status", "--short"], str(root.resolve()))]


def test_interpreter_requires_process_tree_network_isolation(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    launches = []
    runtime = CommandRuntime(
        str(tmp_path / "runtime"), workspace="repo", authorization="human")
    adapter = CommandAdapter(
        host="codex", runtime=runtime,
        launcher=lambda command, cwd: (
            launches.append((command, cwd)) or HostLaunch(binding={"pid": 1})
        ),
    )
    command = [
        "python3", "-c",
        "import subprocess; subprocess.run(['git','push',"
        "'https://example.test/repo','HEAD','--no-verify'])",
    ]

    with pytest.raises(ValueError, match="process and network isolation"):
        adapter.launch_review_validation(
            command, cwd=str(root), session=_session(), sandbox=_sandbox(root),
        )

    assert launches == []


def test_interpreter_is_delegated_to_process_tree_network_isolation(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    isolated = []
    runtime = CommandRuntime(
        str(tmp_path / "runtime"), workspace="repo", authorization="human")
    command = ["python3", "-c", "print('dynamic validation')"]

    def isolate(selected, cwd, policy):
        isolated.append((selected, cwd, policy))
        return HostLaunch(
            binding={"pid": 1},
            isolation={
                "schema": "taskplane.review-isolation-receipt/v1",
                "network": "denied", "scope": "complete-process-tree",
                "mechanism": "host-process-sandbox",
                "policy_fingerprint": hashlib.sha256(json.dumps(
                    policy, sort_keys=True, separators=(",", ":"))
                    .encode("utf-8")).hexdigest(),
            },
        )

    adapter = CommandAdapter(
        host="codex", runtime=runtime,
        launcher=lambda selected, cwd: pytest.fail("ordinary launcher used"),
        review_isolation_launcher=isolate,
    )
    handle = adapter.launch_review_validation(
        command, cwd=str(root), session=_session(), sandbox=_sandbox(root),
    )

    assert isolated == [(command, str(root.resolve()), {
        "schema": "taskplane.review-isolation-policy/v1",
        "network": "deny", "scope": "complete-process-tree",
    })]
    assert adapter.snapshot(handle)["review_sandbox"][
        "isolation_fingerprint"]


def test_isolation_receipt_must_cover_network_and_descendants(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    launches = []
    runtime = CommandRuntime(
        str(tmp_path / "runtime"), workspace="repo", authorization="human")
    adapter = CommandAdapter(
        host="codex", runtime=runtime,
        launcher=lambda command, cwd: HostLaunch(binding={"pid": 1}),
        review_isolation_launcher=lambda command, cwd, policy: HostLaunch(
            binding={"pid": 1},
            isolation={"schema": "taskplane.review-isolation-receipt/v1",
                       "network": "denied", "scope": "parent-only",
                       "mechanism": "incomplete",
                       "policy_fingerprint": "not-the-requested-policy"}),
    )

    with pytest.raises(ValueError, match="process-tree isolation receipt"):
        adapter.launch_review_validation(
            ["npm", "test"], cwd=str(root), session=_session(),
            sandbox=_sandbox(root),
        )


def test_remote_evidence_cannot_treat_two_missing_observations_as_unchanged():
    with pytest.raises(ReviewSessionError, match="remote observations"):
        validation_evidence(
            submitted={"head_before": "abc", "head_after": "abc",
                       "remote_before": None, "remote_after": None},
            sandbox={"disposable": True, "push_disabled": True,
                     "push_attempts": 0, "delta_ref": "sha256:123"},
        )
