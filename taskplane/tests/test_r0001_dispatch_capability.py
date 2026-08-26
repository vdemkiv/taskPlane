import math

import pytest

from taskplane.delivery_ports import (
    content_fingerprint,
    DeliveryPortError,
    FakeClock,
    RecordedTaskDispatchCapabilityFactory,
)
from taskplane.plan_topology import (
    PlanTopologyError,
    validate_scheduler_host_capability,
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


def test_direct_assignment_carries_exact_bound_default_deny_capability():
    from taskplane.delivery_ports import FakeClock, RecordedTaskDispatchCapabilityFactory
    from taskplane.plan_topology import admit_ready_batch, new_scheduler_state

    factory = RecordedTaskDispatchCapabilityFactory()
    state = new_scheduler_state(
        [{
            "id": "task-a", "deps": [], "scope": ["src/a.py"], "tests": "",
            "allowed_tools": ["read", "test"],
            "allowed_git_refs": ["refs/heads/task-a"],
        }],
        run_id="run", source_sha="a" * 40, design_fingerprint="design",
        plan_fingerprint="plan", stage="Execute", repository_files=set(),
    )

    result = admit_ready_batch(
        state, {"configured_host_concurrency": 1},
        {"max_in_flight": 1, "session_limit": 60}, None,
        FakeClock(wall_time=1), capability_factory=factory,
    )

    assignment = result["assignments"][0]
    capability = factory.created[0]
    assert assignment["capability"] == capability.projection
    assert capability.projection["reservation_fingerprint"] == result["reservation_fingerprint"]
    assert capability.projection["write_paths"] == ("src/a.py",)
    capability.require("write_path", "src/a.py", run_id="run", task_id="task-a", stage="Execute")
    with pytest.raises(DeliveryPortError, match="denies"):
        capability.require("write_path", "src/b.py", run_id="run", task_id="task-a")


@pytest.mark.parametrize("field", ["issued_at", "expires_at"])
@pytest.mark.parametrize("invalid_time", [math.nan, math.inf, -math.inf])
def test_scheduler_host_capability_rejects_non_finite_validity_times(
    field, invalid_time,
):
    material = {
        "schema": "taskplane.scheduler-host-capability/v1",
        "run_id": "run",
        "source_sha": "a" * 40,
        "plan_fingerprint": "b" * 64,
        "configured_host_concurrency": 1,
        "max_in_flight": 1,
        "issued_at": 1.0,
        "expires_at": 20.0,
        "cryptographic_authenticity_claimed": False,
    }
    material[field] = invalid_time
    receipt = {**material, "fingerprint": content_fingerprint(material)}

    with pytest.raises(PlanTopologyError, match="malformed"):
        validate_scheduler_host_capability(
            receipt,
            run_id="run",
            source_sha="a" * 40,
            plan_fingerprint="b" * 64,
            clock=FakeClock(wall_time=10),
        )
