import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from taskplane.command_adapters import (
    CommandAdapter, HostLaunch, _pid_start_identity, _register_preview_process,
    os_preview_isolation_launcher, teardown_preview_processes,
)
from taskplane.command_runtime import CommandRuntime
from taskplane.preview_runtime import (
    PreviewDenied, PreviewRuntime, launch_build_preview,
    launch_design_preview, launch_dynamic_review_preview,
    launch_working_preview,
)


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
             "cpu": "rlimit-enforced", "memory": "rlimit-enforced",
             "process_ownership": {
                 "schema": "taskplane.preview-process-ownership/v1",
                 "pid": 42, "pgid": 42, "started": "fixture",
                 "role": "preview-command", "generation": 1},
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


@pytest.mark.parametrize("flow", ["design", "build", "dynamic_review"])
def test_production_entry_invokes_native_surface_and_os_isolation(
        tmp_path, monkeypatch, flow):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.txt").write_text("pinned")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text(
        '[remote "origin"]\nurl=https://example.test/private.git\n')
    calls = []

    class Process:
        pid = 731

        def wait(self, timeout):
            raise __import__("subprocess").TimeoutExpired("preview", timeout)

    def popen(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        # Both the preview process and host surface receive only the copy.
        cwd = Path(kwargs["cwd"])
        assert cwd != source and not (cwd / ".git").exists()
        return Process()

    monkeypatch.setattr("taskplane.command_adapters.subprocess.Popen", popen)
    monkeypatch.setattr(
        "taskplane.command_adapters._process_identity",
        lambda process, role, generation=1: {
            "schema": "taskplane.preview-process-ownership/v1",
            "pid": process.pid, "pgid": process.pid, "started": "fixture",
            "role": role, "generation": generation})
    monkeypatch.setattr("taskplane.command_adapters.sys.platform", "darwin")
    original_isfile = __import__("os").path.isfile
    monkeypatch.setattr(
        "taskplane.command_adapters.os.path.isfile",
        lambda path: True if path == "/usr/bin/sandbox-exec"
        else original_isfile(path))
    monkeypatch.setenv("TASKPLANE_SIDE_PANEL_COMMAND", "native-side-panel open")
    entry = {"design": launch_design_preview,
             "build": launch_build_preview,
             "dynamic_review": launch_dynamic_review_preview}[flow]
    result = entry(
        host="codex", state_root=tmp_path / "state",
        source_root=source, authorization="human", target="commit-a",
        revision=4, capabilities=capabilities(sandbox=True, side_panel=True),
        command=["python3", "-c", "print('preview')"],
        limits={"lifetime_seconds": 60, "cpu_seconds": 10,
                "memory_bytes": 1_000_000})
    assert result["flow"] == flow and result["preview"]["state"] == "open"
    assert result["preview"]["command_lifecycle"]["process_group_binding"]
    assert calls[0][0][0] == "/usr/bin/sandbox-exec"
    assert calls[1][0][0:2] == ["native-side-panel", "open"]
    assert calls[1][0][-2:] == ["--preview-id", result["preview"]["preview_id"]]
    assert (source / ".git" / "config").read_text().startswith('[remote')


def test_production_entry_fails_closed_without_os_or_surface(tmp_path,
                                                             monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.txt").write_text("pinned")
    monkeypatch.delenv("TASKPLANE_BROWSER_COMMAND", raising=False)
    monkeypatch.setattr("taskplane.command_adapters.sys.platform", "linux")
    with pytest.raises(OSError, match="isolation is unavailable"):
        launch_working_preview(
            flow="build", host="claude", state_root=tmp_path / "state",
            source_root=source, authorization="human", target="commit-a",
            revision=4, capabilities=capabilities(sandbox=True, browser=True),
            command=["node", "app.js"],
            limits={"lifetime_seconds": 60, "cpu_seconds": 10,
                    "memory_bytes": 1_000_000})


@pytest.mark.parametrize("returncode,output,match", [
    (71, b"sandbox-exec: sandbox_apply: Operation not permitted",
     "could not apply isolation"),
    (0, b"", "exited during startup"),
])
def test_os_isolation_never_receipts_early_exit(tmp_path, monkeypatch,
                                                returncode, output, match):
    root = tmp_path / "sandbox"
    root.mkdir()

    class EarlyExit:
        pid = 55

        def wait(self, timeout):
            return returncode

        def communicate(self, timeout=0):
            return output, b""

    monkeypatch.setattr("taskplane.command_adapters.subprocess.Popen",
                        lambda *_a, **_k: EarlyExit())
    monkeypatch.setattr("taskplane.command_adapters.sys.platform", "darwin")
    monkeypatch.setattr("taskplane.command_adapters.os.path.isfile",
                        lambda path: path == "/usr/bin/sandbox-exec")
    policy = {"network": "deny", "scope": "complete-process-tree",
              "push": "deny", "filesystem": "sandbox-only",
              "source": "immutable", "remotes": "disabled",
              "sandbox_id": "pin", "preview_id": "preview-a",
              "limits": {"cpu_seconds": 1, "memory_bytes": 10_000_000}}
    with pytest.raises(OSError, match=match):
        os_preview_isolation_launcher(["/usr/bin/true"], str(root), policy)


def test_os_isolation_receipts_only_long_lived_startup(tmp_path, monkeypatch):
    root = tmp_path / "sandbox"
    root.mkdir()

    class Running:
        pid = 56

        def wait(self, timeout):
            raise __import__("subprocess").TimeoutExpired("preview", timeout)

    monkeypatch.setattr("taskplane.command_adapters.subprocess.Popen",
                        lambda *_a, **_k: Running())
    monkeypatch.setattr(
        "taskplane.command_adapters._process_identity",
        lambda process, role, generation=1: {
            "schema": "taskplane.preview-process-ownership/v1",
            "pid": process.pid, "pgid": process.pid, "started": "fixture",
            "role": role, "generation": generation})
    monkeypatch.setattr("taskplane.command_adapters.sys.platform", "darwin")
    monkeypatch.setattr("taskplane.command_adapters.os.path.isfile",
                        lambda path: path == "/usr/bin/sandbox-exec")
    policy = {"network": "deny", "scope": "complete-process-tree",
              "push": "deny", "filesystem": "sandbox-only",
              "source": "immutable", "remotes": "disabled",
              "sandbox_id": "pin", "preview_id": "preview-b",
              "limits": {"cpu_seconds": 1, "memory_bytes": 10_000_000}}
    launched = os_preview_isolation_launcher(
        ["python3", "-m", "http.server"], str(root), policy)
    assert launched.binding["pid"] == 56
    assert launched.isolation["mechanism"] == "macos-seatbelt"


def test_production_startup_failure_is_durable_and_surface_never_opens(
        tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.txt").write_text("pinned")
    surface_calls = []

    class SandboxApplyFailure:
        pid = 57

        def wait(self, timeout):
            return 71

        def communicate(self, timeout=0):
            return b"sandbox-exec: sandbox_apply: Operation not permitted", b""

    def popen(argv, **kwargs):
        if argv[0] != "/usr/bin/sandbox-exec":
            surface_calls.append(argv)
        return SandboxApplyFailure()

    monkeypatch.setattr("taskplane.command_adapters.subprocess.Popen", popen)
    monkeypatch.setattr(
        "taskplane.command_adapters._process_identity",
        lambda process, role, generation=1: {
            "schema": "taskplane.preview-process-ownership/v1",
            "pid": process.pid, "pgid": process.pid, "started": "fixture",
            "role": role, "generation": generation})
    monkeypatch.setattr("taskplane.command_adapters.sys.platform", "darwin")
    monkeypatch.setattr("taskplane.command_adapters.os.path.isfile",
                        lambda path: path == "/usr/bin/sandbox-exec")
    monkeypatch.setenv("TASKPLANE_SIDE_PANEL_COMMAND", "native-side-panel")
    state = tmp_path / "state"
    with pytest.raises(OSError, match="could not apply isolation"):
        launch_build_preview(
            host="codex", state_root=state, source_root=source,
            authorization="human", target="commit-a", revision=4,
            capabilities=capabilities(sandbox=True, side_panel=True),
            command=["/usr/bin/true"], limits={"lifetime_seconds": 60,
                "cpu_seconds": 10, "memory_bytes": 1_000_000})
    assert surface_calls == []
    audit = json.loads((state / "previews" / "audit.json").read_text())
    assert any("sandbox_apply" in row["detail"] for row in audit)
    assert audit[-1]["outcome"] == "unavailable"


def test_close_kills_all_real_preview_process_groups_before_removal(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    runtime = PreviewRuntime(tmp_path / "state", workspace=source,
                             authorization="a",
                             process_teardown=teardown_preview_processes)
    preview = runtime.register(
        flow="build", target="pin", revision=1, source_root=source,
        authorization="a", capabilities=capabilities(
            sandbox=True, browser=True), limits={"lifetime_seconds": 60,
            "cpu_seconds": 2, "memory_bytes": 50_000_000},
        network_allowlist=[])
    processes = [subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True) for _ in range(2)]
    preview["process_ownership"] = [
        _register_preview_process(preview["preview_id"], process,
                                  role="preview-command")
        for process in processes]
    runtime._save(preview)
    closed = runtime.close(preview["preview_id"])
    assert closed["outcome"] == "succeeded"
    assert closed["teardown"]["processes_stopped"] is True
    assert all(process.poll() is not None for process in processes)
    assert not runtime._path(preview["preview_id"]).parent.joinpath(
        "sandbox").exists()


def test_deadline_automatically_kills_real_process_tree(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.txt").write_text("pinned")
    holder = {}
    def transport(name, cwd, preview):
        return {"schema": "taskplane.host-preview-surface/v1",
                "surface": name, "binding": "native:fixture",
                "process_ownership": holder["ownership"]}
    runtime = PreviewRuntime(tmp_path / "state", workspace=source,
                             authorization="a", surface_transport=transport,
                             process_teardown=teardown_preview_processes)
    preview = runtime.register(
        flow="design", target="pin", revision=1, source_root=source,
        authorization="a", capabilities=capabilities(
            sandbox=True, side_panel=True), limits={"lifetime_seconds": 1,
            "cpu_seconds": 2, "memory_bytes": 50_000_000},
        network_allowlist=[])
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True)
    ownership = _register_preview_process(preview["preview_id"], process,
                                          role="preview-command")
    holder["ownership"] = dict(ownership, role="host-surface")
    preview["process_ownership"] = [ownership]
    runtime._save(preview)
    runtime.open(preview["preview_id"])
    runtime.arm_deadline(preview["preview_id"])
    deadline = time.monotonic() + 3
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert process.poll() is not None
    assert runtime._load(preview["preview_id"])["outcome"] == "timed_out"


def test_resource_limit_enforcement_fails_closed_when_unsupported(
        tmp_path, monkeypatch):
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setattr("taskplane.command_adapters.sys.platform", "darwin")
    monkeypatch.setattr("taskplane.command_adapters.os.path.isfile",
                        lambda path: path == "/usr/bin/sandbox-exec")
    monkeypatch.setattr("taskplane.command_adapters._resource", None)
    policy = {"network": "deny", "scope": "complete-process-tree",
              "push": "deny", "filesystem": "sandbox-only",
              "source": "immutable", "remotes": "disabled",
              "sandbox_id": "pin", "preview_id": "preview-c",
              "limits": {"cpu_seconds": 1, "memory_bytes": 10_000_000}}
    with pytest.raises(OSError, match="CPU/memory enforcement is unavailable"):
        os_preview_isolation_launcher(["sleep", "30"], str(root), policy)


def test_failed_process_teardown_never_reports_success(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    runtime = PreviewRuntime(tmp_path / "state", workspace=source,
                             authorization="a",
                             process_teardown=lambda _id, _ownership: False)
    preview = runtime.register(
        flow="build", target="pin", revision=1, source_root=source,
        authorization="a", capabilities=capabilities(sandbox=True, browser=True),
        limits={"lifetime_seconds": 60, "cpu_seconds": 2,
                "memory_bytes": 50_000_000}, network_allowlist=[])
    closed = runtime.close(preview["preview_id"])
    assert closed["state"] == "failed"
    assert closed["outcome"] == "teardown_failed"
    assert closed["teardown"]["processes_stopped"] is False


def test_restart_rehydrates_ownership_and_kills_real_detached_group(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    root = tmp_path / "state"
    runtime = PreviewRuntime(root, workspace=source, authorization="a",
                             process_teardown=teardown_preview_processes)
    preview = runtime.register(
        flow="build", target="pin", revision=1, source_root=source,
        authorization="a", capabilities=capabilities(sandbox=True, browser=True),
        limits={"lifetime_seconds": 60, "cpu_seconds": 2,
                "memory_bytes": 50_000_000}, network_allowlist=[])
    launcher = subprocess.Popen(
        [sys.executable, "-c", "import subprocess,sys; p=subprocess.Popen("
         "[sys.executable,'-c','import time; time.sleep(30)'],"
         "start_new_session=True,stdout=subprocess.DEVNULL,"
         "stderr=subprocess.DEVNULL); print(p.pid, flush=True)"],
        stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    child_pid = int(launcher.communicate(timeout=2)[0].strip())
    ownership = {"schema": "taskplane.preview-process-ownership/v1",
                 "pid": child_pid, "pgid": child_pid,
                 "started": _pid_start_identity(child_pid),
                 "role": "preview-command", "generation": 1}
    preview["process_ownership"] = [ownership]
    runtime._save(preview)
    # Simulate host restart: no Popen object or module registry survives.
    import taskplane.command_adapters as adapters
    with adapters._PREVIEW_PROCESS_LOCK:
        adapters._PREVIEW_PROCESSES.clear()
    restarted = PreviewRuntime(root, workspace=source, authorization="a",
                               process_teardown=teardown_preview_processes)
    closed = restarted.close(preview["preview_id"])
    assert closed["outcome"] == "succeeded"
    with pytest.raises(ProcessLookupError):
        __import__("os").getpgid(child_pid)


def test_pid_reuse_identity_mismatch_is_never_signalled(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    runtime = PreviewRuntime(tmp_path / "state", workspace=source,
                             authorization="a",
                             process_teardown=teardown_preview_processes)
    preview = runtime.register(
        flow="build", target="pin", revision=1, source_root=source,
        authorization="a", capabilities=capabilities(sandbox=True, browser=True),
        limits={"lifetime_seconds": 60, "cpu_seconds": 2,
                "memory_bytes": 50_000_000}, network_allowlist=[])
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True)
    preview["process_ownership"] = [{
        "schema": "taskplane.preview-process-ownership/v1",
        "pid": process.pid, "pgid": process.pid, "started": "reused-pid",
        "role": "preview-command", "generation": 1}]
    runtime._save(preview)
    closed = runtime.close(preview["preview_id"])
    assert closed["outcome"] == "teardown_failed"
    assert process.poll() is None
    __import__("os").killpg(process.pid, __import__("signal").SIGKILL)
    process.wait(timeout=2)


def test_missing_or_corrupt_restart_ownership_preserves_sandbox(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    runtime = PreviewRuntime(tmp_path / "state", workspace=source,
                             authorization="a",
                             process_teardown=teardown_preview_processes)
    preview = runtime.register(
        flow="build", target="pin", revision=1, source_root=source,
        authorization="a", capabilities=capabilities(sandbox=True, browser=True),
        limits={"lifetime_seconds": 60, "cpu_seconds": 2,
                "memory_bytes": 50_000_000}, network_allowlist=[])
    preview["process_ownership"] = [{"schema": "corrupt", "pid": "bad"}]
    runtime._save(preview)
    closed = runtime.close(preview["preview_id"])
    assert closed["outcome"] == "teardown_failed"
    assert runtime.sandbox_path(preview["preview_id"]).is_dir()
