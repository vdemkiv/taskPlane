from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from taskplane import (
    command_adapters, design_sweep, native_authority, preview_runtime, tp,
)


ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _production_gate_root(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    _write_json(root / "design/contract.json", {
        "schema": "taskplane.design/v1",
        "requirement": "R-0013",
        "design_sweep": {"completed_state": {}},
    })
    _write_json(root / "plan/tasks.json", {
        "schema": "taskplane.plan/v1", "requirement": "R-0013",
        "tasks": [],
    })
    return root


def test_h10_native_authority_validator_is_reachable_from_supported_flow(
        tmp_path, monkeypatch):
    root = _production_gate_root(tmp_path)
    calls = []

    def authority(design, plan):
        calls.append(("authority", design["requirement"], plan["requirement"]))
        return {"schema": "authority", "status": "ready"}

    def roots(source_root):
        calls.append(("roots", Path(source_root)))
        return {"schema": "roots", "status": "ready"}

    def sweep(source_root, *, evidence):
        calls.append(("sweep", Path(source_root), dict(evidence)))
        return {"schema": "sweep", "status": "ready"}

    monkeypatch.setattr(native_authority, "validate_design_and_plan", authority)
    monkeypatch.setattr(native_authority, "validate_delivery_roots", roots)
    monkeypatch.setattr(design_sweep, "validate_retained_design_sweep", sweep)
    receipt = native_authority.validate_production_design_gate(
        root, sweep_evidence={"host": "retained"})

    assert receipt["schema"] == "taskplane.production-design-gate/v1"
    assert receipt["status"] == "ready"
    assert calls == [
        ("authority", "R-0013", "R-0013"),
        ("roots", root.resolve()),
        ("sweep", root.resolve(), {"host": "retained"}),
    ]

    for name in ("validate_design_and_plan", "validate_delivery_roots"):
        monkeypatch.setattr(native_authority, name,
                            lambda *_a, **_k: (_ for _ in ()).throw(
                                native_authority.NativeAuthorityError("severed")))
        with pytest.raises(native_authority.NativeAuthorityError,
                           match="severed"):
            native_authority.validate_production_design_gate(
                root, sweep_evidence={"host": "retained"})
        monkeypatch.setattr(native_authority, "validate_design_and_plan", authority)
        monkeypatch.setattr(native_authority, "validate_delivery_roots", roots)


@pytest.mark.parametrize(
    "flow", ["design", "build", "dynamic_review"],
)
def test_h12_preview_entrypoints_execute_from_supported_flow(
        tmp_path, monkeypatch, capsys, flow):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("print('preview')\n", encoding="utf-8")
    state = tmp_path / "state"
    isolation_calls = []
    surface_calls = []

    def isolate(command, cwd, policy):
        isolation_calls.append((list(command), Path(cwd), dict(policy)))
        fingerprint = hashlib.sha256(json.dumps(
            dict(policy), sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        return command_adapters.HostLaunch(
            binding={"native": "preview-fixture"},
            isolation={
                "schema": "taskplane.preview-isolation-receipt/v1",
                "network": "denied", "scope": "complete-process-tree",
                "push": "denied", "filesystem": "sandbox-only",
                "source": "immutable", "remotes": "disabled",
                "cpu": "rlimit-enforced", "memory": "rlimit-enforced",
                "mechanism": "focused-host-fixture",
                "policy_fingerprint": fingerprint,
                "process_ownership": {
                    "schema": "taskplane.preview-process-ownership/v1",
                    "pid": 101, "pgid": 101, "started": "fixture",
                    "role": "preview-command", "generation": 1,
                },
            })

    def surface(surface_name, sandbox, preview):
        surface_calls.append((surface_name, Path(sandbox), preview["flow"]))
        return {
            "schema": "taskplane.host-preview-surface/v1",
            "surface": surface_name, "binding": "focused-surface",
            "process_ownership": {
                "schema": "taskplane.preview-process-ownership/v1",
                "pid": 102, "pgid": 102, "started": "fixture-surface",
                "role": "host-surface", "generation": 1,
            },
        }

    monkeypatch.setattr(
        command_adapters, "os_preview_isolation_launcher", isolate)
    monkeypatch.setattr(command_adapters, "native_surface_transport", surface)
    request = {
        "flow": flow, "host": "codex", "state_root": str(state),
        "source_root": str(source), "authorization": "authority",
        "target": "candidate", "revision": 7,
        "capabilities": _capabilities(),
        "command": ["python3", "app.py"],
        "limits": {"lifetime_seconds": 60, "cpu_seconds": 10,
                   "memory_bytes": 1_000_000},
    }
    request_path = tmp_path / f"{flow}.json"
    _write_json(request_path, request)
    assert tp.main(["preview", "--request", str(request_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "taskplane.working-preview-launch/v1"
    assert result["flow"] == flow
    assert result["preview"]["state"] == "open"
    assert isolation_calls[0][0] == ["python3", "app.py"]
    assert isolation_calls[0][1].is_dir()
    assert isolation_calls[0][2]["scope"] == "complete-process-tree"
    assert surface_calls == [
        ("browser", isolation_calls[0][1], flow)]

    with pytest.raises(preview_runtime.PreviewDenied,
                       match="request fields"):
        preview_runtime.launch_preview_request({**request, "fake_pass": True})
    with pytest.raises(preview_runtime.PreviewDenied, match="shell wrapper"):
        preview_runtime.launch_preview_request(
            {**request, "command": ["sh", "-c", "python3 app.py"]})


def test_h12_preview_cli_normalizes_untyped_host_startup_failure(
        tmp_path, monkeypatch, capsys):
    request = tmp_path / "preview.json"
    request.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        preview_runtime, "load_preview_request", lambda _path: {})
    monkeypatch.setattr(
        preview_runtime, "launch_preview_request",
        lambda _request: (_ for _ in ()).throw(
            OSError("native isolation startup unavailable")))

    assert tp.main(["preview", "--request", str(request)]) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result == {
        "schema": "taskplane.working-preview-launch/v1",
        "status": "unavailable", "outcome": "unavailable",
        "error": ("preview host startup failed: OSError: "
                  "native isolation startup unavailable"),
    }
    assert captured.err == ""


def _capabilities() -> dict:
    return {
        "sandbox": {"status": "supported", "source": "native"},
        "browser": {"status": "supported", "source": "native"},
    }


def _register(source: Path, state: Path, **startup_limits):
    runtime = preview_runtime.PreviewRuntime(
        state, workspace=source, authorization="authority")
    limits = {
        "lifetime_seconds": 60, "cpu_seconds": 10,
        "memory_bytes": 1_000_000, **startup_limits,
    }
    preview = runtime.register(
        flow="build", target="candidate", revision=1, source_root=source,
        authorization="authority", capabilities=_capabilities(), limits=limits,
        network_allowlist=[])
    return runtime, preview


@pytest.mark.parametrize(
    ("files", "limits", "message"),
    [
        ({"a": b"1", "b": b"2"}, {"startup_entries": 1}, "entry limit"),
        ({"a": b"12345"}, {"startup_file_bytes": 4,
                            "startup_total_bytes": 8}, "file exceeds"),
        ({"a": b"123", "b": b"456"}, {"startup_file_bytes": 4,
                                         "startup_total_bytes": 5},
         "total-byte limit"),
    ],
)
def test_h29_preview_refuses_entry_and_byte_limit_overrun(
        tmp_path, files, limits, message):
    source = tmp_path / "source"
    source.mkdir()
    for name, content in files.items():
        (source / name).write_bytes(content)
    runtime = preview_runtime.PreviewRuntime(
        tmp_path / "state", workspace=source, authorization="authority")
    with pytest.raises(preview_runtime.PreviewDenied, match=message) as error:
        runtime.register(
            flow="build", target="candidate", revision=1, source_root=source,
            authorization="authority", capabilities=_capabilities(),
            limits={"lifetime_seconds": 60, "cpu_seconds": 10,
                    "memory_bytes": 1_000_000, **limits},
            network_allowlist=[])
    assert error.value.outcome == "unavailable"
    assert runtime.audit()[-1]["outcome"] == "unavailable"
    assert not list((tmp_path / "state" / "previews").glob("*/sandbox"))


def test_h29_preview_streams_one_bounded_manifest_and_excludes_generated(
        tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_bytes(b"print('bounded')\n")
    generated = source / "node_modules"
    generated.mkdir()
    (generated / "huge.bin").write_bytes(b"x" * 100)
    (source / ".git").write_text("gitdir: /private/repository", encoding="utf-8")

    monkeypatch.setattr(
        Path, "read_bytes",
        lambda _self: pytest.fail("preview hashing must stream, not read whole files"))
    runtime, preview = _register(
        source, tmp_path / "state", startup_entries=1,
        startup_file_bytes=32, startup_total_bytes=32)
    assert preview["startup_inventory"] == {
        "entries": 1, "regular_file_bytes": len(b"print('bounded')\n")}
    sandbox = runtime.sandbox_path(preview["preview_id"])
    assert (sandbox / "app.py").is_file()
    assert not (sandbox / "node_modules").exists()
    assert not (sandbox / ".git").exists()


def test_h29_preview_rejects_symlink_and_post_inventory_source_drift(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "escape").symlink_to(outside)
    runtime = preview_runtime.PreviewRuntime(
        tmp_path / "linked-state", workspace=linked,
        authorization="authority")
    with pytest.raises(preview_runtime.PreviewDenied, match="symlink"):
        runtime.register(
            flow="build", target="candidate", revision=1, source_root=linked,
            authorization="authority", capabilities=_capabilities(),
            limits={"lifetime_seconds": 60, "cpu_seconds": 10,
                    "memory_bytes": 1_000_000}, network_allowlist=[])

    source = tmp_path / "source"
    source.mkdir()
    app = source / "app.py"
    app.write_text("before", encoding="utf-8")
    runtime, preview = _register(source, tmp_path / "state")
    app.write_text("after", encoding="utf-8")
    closed = runtime.close(preview["preview_id"])
    assert closed["state"] == "failed"
    assert closed["outcome"] == "escaped_path"


def test_h29_preview_descriptor_refuses_same_content_symlink_swap(
        tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    app = source / "app.py"
    app.write_bytes(b"same-content\n")
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"same-content\n")
    original = preview_runtime._materialize_manifest

    def swap_then_materialize(*args, **kwargs):
        app.unlink()
        app.symlink_to(outside)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        preview_runtime, "_materialize_manifest", swap_then_materialize)
    runtime = preview_runtime.PreviewRuntime(
        tmp_path / "state", workspace=source, authorization="authority")
    with pytest.raises(preview_runtime.PreviewDenied,
                       match="materialization|unavailable") as error:
        runtime.register(
            flow="build", target="candidate", revision=1,
            source_root=source, authorization="authority",
            capabilities=_capabilities(),
            limits={"lifetime_seconds": 60, "cpu_seconds": 10,
                    "memory_bytes": 1_000_000}, network_allowlist=[])
    assert error.value.outcome == "unavailable"
    assert runtime.audit()[-1]["outcome"] == "unavailable"
    assert not list((tmp_path / "state" / "previews").glob("*"))


def test_h29_preview_preparation_exception_is_structured_and_cleans_scope(
        tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("bounded", encoding="utf-8")

    def fail_preparation(*_args, **_kwargs):
        raise RuntimeError("injected preparation fault")

    monkeypatch.setattr(
        preview_runtime, "_materialize_manifest", fail_preparation)
    runtime = preview_runtime.PreviewRuntime(
        tmp_path / "state", workspace=source, authorization="authority")
    with pytest.raises(preview_runtime.PreviewDenied,
                       match="injected preparation fault") as error:
        runtime.register(
            flow="build", target="candidate", revision=1,
            source_root=source, authorization="authority",
            capabilities=_capabilities(),
            limits={"lifetime_seconds": 60, "cpu_seconds": 10,
                    "memory_bytes": 1_000_000}, network_allowlist=[])
    assert error.value.outcome == "unavailable"
    assert runtime.audit()[-1]["outcome"] == "unavailable"
    assert not list((tmp_path / "state" / "previews").glob("*"))


def test_h29_preview_registration_has_one_aggregate_startup_deadline(
        tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("bounded", encoding="utf-8")
    now = [0.0]

    def advancing_clock():
        now[0] += 1.0
        return now[0]

    monkeypatch.setattr(preview_runtime.time, "monotonic", advancing_clock)
    runtime = preview_runtime.PreviewRuntime(
        tmp_path / "state", workspace=source, authorization="authority")
    with pytest.raises(preview_runtime.PreviewDenied,
                       match="startup time limit") as error:
        runtime.register(
            flow="build", target="candidate", revision=1, source_root=source,
            authorization="authority", capabilities=_capabilities(),
            limits={"lifetime_seconds": 60, "cpu_seconds": 10,
                    "memory_bytes": 1_000_000, "startup_seconds": 1},
            network_allowlist=[])
    assert error.value.outcome == "unavailable"


def test_h29_expiry_mid_cleanup_detaches_active_scope(
        tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("bounded", encoding="utf-8")
    cleanup_started = [False]
    cleanup_now = [0.1]
    original_cleanup = preview_runtime._bounded_remove_tree_at

    def fail_after_partial_materialization(_source, sandbox, *_args, **_kwargs):
        nested = sandbox / "nested"
        nested.mkdir()
        (nested / "partial.txt").write_text("owned", encoding="utf-8")
        raise RuntimeError("injected partial materialization")

    def observed_cleanup(*args, **kwargs):
        cleanup_started[0] = True
        return original_cleanup(*args, **kwargs)

    def cleanup_expiry_clock():
        if not cleanup_started[0]:
            return 0.1
        cleanup_now[0] += 0.6
        return cleanup_now[0]

    monkeypatch.setattr(
        preview_runtime, "_materialize_manifest",
        fail_after_partial_materialization)
    monkeypatch.setattr(
        preview_runtime, "_bounded_remove_tree_at", observed_cleanup)
    monkeypatch.setattr(
        preview_runtime.time, "monotonic", cleanup_expiry_clock)
    runtime = preview_runtime.PreviewRuntime(
        tmp_path / "state", workspace=source, authorization="authority")

    with pytest.raises(preview_runtime.PreviewDenied,
                       match="cleanup failed") as error:
        runtime.register(
            flow="build", target="candidate", revision=1, source_root=source,
            authorization="authority", capabilities=_capabilities(),
            limits={"lifetime_seconds": 60, "cpu_seconds": 10,
                    "memory_bytes": 1_000_000, "startup_seconds": 1},
            network_allowlist=[])
    assert error.value.outcome == "unavailable"
    assert not list((tmp_path / "state" / "previews").glob("*"))
    assert list((tmp_path / "state" / "quarantine").glob("*"))


@pytest.mark.parametrize("swap", ["root", "subtree"])
def test_h29_cleanup_never_follows_root_or_subtree_symlink_swap(
        tmp_path, monkeypatch, swap):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("bounded", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "protected.txt"
    protected.write_text("must-survive", encoding="utf-8")

    def swap_then_fail(_source, sandbox, *_args, **_kwargs):
        if swap == "root":
            sandbox.rmdir()
            sandbox.parent.rmdir()
            sandbox.parent.symlink_to(outside, target_is_directory=True)
        else:
            (sandbox / "nested").symlink_to(
                outside, target_is_directory=True)
        raise RuntimeError(f"injected {swap} swap")

    monkeypatch.setattr(
        preview_runtime, "_materialize_manifest", swap_then_fail)
    runtime = preview_runtime.PreviewRuntime(
        tmp_path / "state", workspace=source, authorization="authority")
    with pytest.raises(preview_runtime.PreviewDenied) as error:
        runtime.register(
            flow="build", target="candidate", revision=1, source_root=source,
            authorization="authority", capabilities=_capabilities(),
            limits={"lifetime_seconds": 60, "cpu_seconds": 10,
                    "memory_bytes": 1_000_000}, network_allowlist=[])

    assert error.value.outcome == "unavailable"
    assert protected.read_text(encoding="utf-8") == "must-survive"
    assert not list((tmp_path / "state" / "previews").glob("*"))
    assert not list((tmp_path / "state" / "quarantine").glob("*"))
