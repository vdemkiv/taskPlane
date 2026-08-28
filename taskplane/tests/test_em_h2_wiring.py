from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from taskplane import design_sweep, native_authority, preview_runtime


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


def _sweep_root(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "repository"
    lens_ids = [f"lens-{index:02d}" for index in range(26)]
    catalog = {"lenses": [{"id": lens_id} for lens_id in lens_ids]}
    source_fingerprint = "a" * 64
    design = {
        "schema": "taskplane.design/v1",
        "design_sweep": {"completed_state": {
            "source_content_fingerprint": source_fingerprint,
        }},
    }
    _write_json(root / "lenses/catalog.json", catalog)
    _write_json(root / "design/contract.json", design)
    for lens_id in lens_ids:
        _write_json(root / f"design/lens-evidence/{lens_id}.json", {
            "schema": "taskplane.design-lens-result/v1", "lens": lens_id,
        })
    audit = tmp_path / "native-audit.jsonl"
    audit.write_text("{}\n", encoding="utf-8")
    evidence = {
        "codex_audit_path": str(audit),
        "source_thread_id": "thread-a",
        "design_turn_id": "turn-a",
        "expected_source_log_sha256": hashlib.sha256(b"{}\n").hexdigest(),
    }
    return root, evidence


def test_h11_design_sweep_validator_is_reachable_or_removed(
        tmp_path, monkeypatch):
    root, evidence = _sweep_root(tmp_path)
    observed = []

    def validate(catalog, **kwargs):
        observed.append((catalog, kwargs))
        return {
            "schema": design_sweep.DESIGN_SWEEP_SCHEMA,
            "source_thread_id": kwargs["source_thread_id"],
            "design_turn_id": kwargs["design_turn_id"],
            "fingerprint": "b" * 64,
        }

    monkeypatch.setattr(design_sweep, "validate_design_sweep", validate)
    receipt = design_sweep.validate_retained_design_sweep(root, evidence=evidence)
    assert receipt["schema"] == "taskplane.production-design-sweep-gate/v1"
    assert receipt["status"] == "ready"
    assert len(observed) == 1
    catalog, kwargs = observed[0]
    assert len(catalog["lenses"]) == 26
    assert set(kwargs["result_evidence"]) == {
        f"lens-{index:02d}" for index in range(26)}
    assert all(isinstance(value, bytes)
               for value in kwargs["result_evidence"].values())
    assert kwargs["codex_audit_evidence"] == Path(
        evidence["codex_audit_path"])

    monkeypatch.setattr(
        design_sweep, "validate_design_sweep",
        lambda *_a, **_k: (_ for _ in ()).throw(
            design_sweep.DesignSweepError("severed sweep edge")))
    with pytest.raises(design_sweep.DesignSweepError,
                       match="severed sweep edge"):
        design_sweep.validate_retained_design_sweep(root, evidence=evidence)
    with pytest.raises(design_sweep.DesignSweepError,
                       match="evidence fields"):
        design_sweep.validate_retained_design_sweep(
            root, evidence={**evidence, "caller_results": {"fake": "pass"}})


@pytest.mark.parametrize(
    ("flow", "entry_name"),
    [("design", "launch_design_preview"),
     ("build", "launch_build_preview"),
     ("dynamic_review", "launch_dynamic_review_preview")],
)
def test_h12_preview_entrypoints_execute_from_supported_flow(
        monkeypatch, flow, entry_name):
    calls = []

    def launch(**kwargs):
        calls.append(kwargs)
        return {"schema": "live-preview", "flow": flow}

    monkeypatch.setattr(preview_runtime, entry_name, launch)
    request = {
        "flow": flow, "host": "codex", "state_root": "state",
        "source_root": "source", "authorization": "authority",
        "target": "candidate", "revision": 7,
        "capabilities": {}, "command": ["python3", "app.py"],
        "limits": {},
    }
    result = preview_runtime.launch_preview_request(request)
    assert result == {"schema": "live-preview", "flow": flow}
    assert calls == [{name: value for name, value in request.items()
                      if name != "flow"}]

    with pytest.raises(preview_runtime.PreviewDenied,
                       match="request fields"):
        preview_runtime.launch_preview_request({**request, "fake_pass": True})


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
