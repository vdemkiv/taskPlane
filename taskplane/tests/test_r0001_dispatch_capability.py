import pytest

from taskplane.delivery_ports import (
    DeliveryPortError,
    RecordedTaskDispatchCapabilityFactory,
)

def _capability(**overrides):
    values = dict(
        run_id="run", source_sha="a" * 40, design_fingerprint="design",
        plan_fingerprint="plan", task_id="task-a", stage="Execute",
        reservation_fingerprint="reservation", predecessor_fingerprint=None,
        allowed_tools=("read", "test"), read_paths=("src/a.py",), write_paths=("src/a.py",),
        allowed_git_refs=("refs/heads/task-a",), allowed_network_endpoints=(), credential_handles=(),
    )
    values.update(overrides)
    return RecordedTaskDispatchCapabilityFactory().create(**values)


def test_task_capability_defaults_deny_every_undeclared_surface():
    capability = _capability()
    assert capability.allows("tool", "read")
    for surface, value in (("tool", "shell"), ("read_path", "src/b.py"), ("git_ref", "refs/heads/main"), ("network_endpoint", "example.com"), ("credential_handle", "token")):
        with pytest.raises(DeliveryPortError, match="denies"):
            capability.require(surface, value, run_id="run", task_id="task-a", stage="Execute")


def test_workers_receive_no_release_credentials_or_irreversible_tools():
    projection = _capability().projection
    assert projection["release_credentials_available"] is False
    assert projection["irreversible_actions_allowed"] is False
    assert projection["cryptographic_authenticity_claimed"] is False
    with pytest.raises(DeliveryPortError, match="irreversible"):
        _capability(allowed_tools=("read", "publish"))
    with pytest.raises(DeliveryPortError, match="release credentials"):
        _capability(credential_handles=("release-token",))


def test_cross_task_run_ref_path_network_and_credential_reuse_is_refused():
    capability = _capability()
    with pytest.raises(DeliveryPortError, match="run_id"):
        capability.require("tool", "read", run_id="other", task_id="task-a")
    with pytest.raises(DeliveryPortError, match="task_id"):
        capability.require("tool", "read", run_id="run", task_id="task-b")


def test_irreversible_action_requires_outside_model_human_recheck():
    capability = _capability()
    for action in ("push", "tag", "install", "publish", "credential-release"):
        assert not capability.allows("tool", action)


def test_generic_capability_remains_exact_bound_and_default_deny():
    capability = _capability()
    assert capability.projection["write_paths"] == ("src/a.py",)
    capability.require(
        "write_path", "src/a.py",
        run_id="run", task_id="task-a", stage="Execute")
    with pytest.raises(DeliveryPortError, match="denies"):
        capability.require(
            "write_path", "src/b.py",
            run_id="run", task_id="task-a")
