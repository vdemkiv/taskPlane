import hashlib
import json
from pathlib import Path

import pytest

from taskplane.command_adapters import CommandAdapter, HostLaunch
from taskplane.command_runtime import CommandRuntime
from taskplane.preview_runtime import PreviewDenied, PreviewRuntime


def capabilities(**supported):
    names = ("sandbox", "hosting", "browser", "side_panel")
    return {name: {"status": "supported" if supported.get(name) else
                   "unsupported", "source": "host", "confidence": "high"}
            for name in names}


def surface(calls):
    def invoke(name, cwd, preview):
        calls.append((name, cwd, preview["target"]))
        assert Path(cwd, "app.txt").read_text() == "pinned"
        return {"schema": "taskplane.host-preview-surface/v1",
                "surface": name, "binding": f"native:{name}:1"}
    return invoke


def register(tmp_path, *, flow="build", clock=None, transport=None):
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    (source / "app.txt").write_text("pinned")
    runtime = PreviewRuntime(tmp_path / "state", workspace=source,
                             authorization="session-a", clock=clock,
                             surface_transport=transport)
    preview = runtime.register(
        flow=flow, target="abc123", revision=7, source_root=source,
        authorization="session-a",
        capabilities=capabilities(sandbox=True, browser=True, side_panel=True),
        limits={"lifetime_seconds": 60, "cpu_seconds": 10,
                "memory_bytes": 1_000_000}, network_allowlist=[])
    return source, runtime, preview


def receipt(policy, **changes):
    value = {"schema": "taskplane.preview-isolation-receipt/v1",
             "network": "denied", "scope": "complete-process-tree",
             "push": "denied", "filesystem": "sandbox-only",
             "source": "immutable", "remotes": "disabled",
             "mechanism": "host-process-sandbox",
             "policy_fingerprint": hashlib.sha256(json.dumps(
                 policy, sort_keys=True, separators=(",", ":"))
                 .encode()).hexdigest()}
    value.update(changes)
    return value


@pytest.mark.parametrize("flow", ["design", "build", "dynamic_review"])
def test_authorized_pinned_preview_opens_native_surface(tmp_path, flow):
    calls = []
    source, runtime, preview = register(tmp_path, flow=flow,
                                                transport=surface(calls))
    sandbox = runtime.sandbox_path(preview["preview_id"])
    assert sandbox != source and (sandbox / "app.txt").read_text() == "pinned"
    opened = runtime.open(preview["preview_id"])
    evidence = runtime.observe(preview["preview_id"], interaction="click",
                               result="detail opened")
    closed = runtime.close(preview["preview_id"])
    assert calls == [("side_panel", str(sandbox), "abc123")]
    assert opened["state"] == "open" and opened["surface_binding_fingerprint"]
    assert evidence["target"] == "abc123" and evidence["revision"] == 7
    assert closed["outcome"] == "succeeded" and not sandbox.exists()
    assert (source / "app.txt").read_text() == "pinned"


def test_launch_preview_executes_complete_isolation_boundary(tmp_path):
    calls = []
    source, previews, preview = register(tmp_path, transport=surface(calls))
    sandbox = previews.sandbox_path(preview["preview_id"])
    launches = []

    def isolate(command, cwd, policy):
        launches.append((command, cwd, policy))
        return HostLaunch(binding={"pid": 42}, isolation=receipt(policy))

    runtime = CommandRuntime(str(tmp_path / "commands"), workspace=str(source),
                             authorization="session-a")
    adapter = CommandAdapter(host="codex", runtime=runtime,
                             launcher=lambda *_: pytest.fail("wrong launcher"),
                             review_isolation_launcher=isolate)
    command = ["python3", "-c", "print('working preview')"]
    handle = adapter.launch_preview(command, cwd=str(sandbox), preview=preview)
    snap = adapter.snapshot(handle)
    assert launches[0][0:2] == (command, str(sandbox.resolve()))
    assert {key: launches[0][2][key] for key in
            ("push", "filesystem", "source", "remotes")} == {
                "push": "deny", "filesystem": "sandbox-only",
                "source": "immutable", "remotes": "disabled"}
    assert snap["state"] == "running" and snap["preview"]["target"] == "abc123"
    adapter.notify(handle, {"state": "completed", "exit_code": 0})
    previews.open(preview["preview_id"])
    previews.close(preview["preview_id"])
    assert adapter.snapshot(handle)["state"] == "succeeded"
    assert (source / "app.txt").read_text() == "pinned"


@pytest.mark.parametrize("change", [
    {"network": "allowed"}, {"scope": "parent-only"},
    {"push": "allowed"}, {"filesystem": "unbounded"},
    {"source": "mutable"}, {"remotes": "enabled"},
    {"policy_fingerprint": "replayed"}, {"mechanism": ""},
])
def test_launch_preview_rejects_incomplete_boundary_receipt(tmp_path, change):
    _, previews, preview = register(tmp_path, transport=surface([]))
    sandbox = previews.sandbox_path(preview["preview_id"])
    commands = CommandRuntime(str(tmp_path / "commands"), workspace="repo",
                              authorization="a")
    adapter = CommandAdapter(host="claude", runtime=commands,
                             launcher=lambda *_: HostLaunch(binding={"pid": 1}),
                             review_isolation_launcher=lambda c, w, p: HostLaunch(
                                 binding={"pid": 1}, isolation=receipt(p, **change)))
    with pytest.raises(ValueError, match="isolation receipt"):
        adapter.launch_preview(["node", "app.js"], cwd=str(sandbox),
                               preview=preview)


@pytest.mark.parametrize("case", ["unavailable", "denied", "escaped_path",
                                   "external_network", "public_exposure"])
def test_registration_failures_are_explicit_and_audited(tmp_path, case):
    source = tmp_path / "source"
    source.mkdir()
    runtime = PreviewRuntime(tmp_path / "state", workspace=source,
                             authorization="session-a")
    kwargs = dict(flow="build", target="abc", revision=1, source_root=source,
                  authorization="session-a",
                  capabilities=capabilities(sandbox=True, browser=True),
                  limits={"lifetime_seconds": 10, "cpu_seconds": 2,
                          "memory_bytes": 100_000}, network_allowlist=[])
    if case == "unavailable": kwargs["capabilities"] = capabilities()
    elif case == "denied": kwargs["authorization"] = "wrong"
    elif case == "escaped_path": kwargs["source_root"] = tmp_path
    elif case == "external_network": kwargs["network_allowlist"] = ["x.test"]
    elif case == "public_exposure": kwargs["visibility"] = "public"
    with pytest.raises(PreviewDenied) as error:
        runtime.register(**kwargs)
    assert error.value.outcome == case and runtime.audit()[-1]["outcome"] == case


def test_build_failure_and_timeout_are_real_lifecycle_outcomes(tmp_path):
    now = [10.0]
    _, runtime, preview = register(tmp_path, clock=lambda: now[0],
                                   transport=surface([]))
    failed = runtime.record_stage(preview["preview_id"], stage="build",
                                  outcome="failed", detail="exit 1")
    terminal = runtime.record_outcome(preview["preview_id"], "build_failed")
    assert failed["outcome"] == "failed" and terminal["state"] == "failed"
    _, runtime2, preview2 = register(tmp_path / "other", clock=lambda: now[0],
                                     transport=surface([]))
    now[0] = 100.0
    timed = runtime2.open(preview2["preview_id"])
    assert timed["outcome"] == "timed_out" and timed["teardown"]["attempted"]


def test_push_path_network_and_teardown_faults_fail_safe(tmp_path, monkeypatch):
    source, previews, preview = register(tmp_path, transport=surface([]))
    sandbox = previews.sandbox_path(preview["preview_id"])
    commands = CommandRuntime(str(tmp_path / "commands"), workspace="repo",
                              authorization="a")
    adapter = CommandAdapter(host="codex", runtime=commands,
                             launcher=lambda *_: HostLaunch(binding={"pid": 1}),
                             review_isolation_launcher=lambda c, w, p: HostLaunch(
                                 binding={"pid": 1}, isolation=receipt(p)))
    with pytest.raises(ValueError, match="read-only git"):
        adapter.launch_preview(["git", "push"], cwd=str(sandbox), preview=preview)
    with pytest.raises(ValueError, match="escapes registered sandbox"):
        adapter.launch_preview(["node", "app.js"], cwd=str(source), preview=preview)
    assert (source / "app.txt").read_text() == "pinned"
    monkeypatch.setattr("taskplane.preview_runtime.shutil.rmtree",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError()))
    closed = previews.close(preview["preview_id"])
    assert closed["outcome"] == "teardown_failed"
