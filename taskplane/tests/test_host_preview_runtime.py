from pathlib import Path

import pytest

from taskplane.preview_runtime import PreviewDenied, PreviewRuntime


def capabilities(**supported):
    names = ("sandbox", "hosting", "browser", "side_panel")
    return {name: {"status": "supported" if supported.get(name) else
                   "unsupported", "source": "host", "confidence": "high"}
            for name in names}


@pytest.mark.parametrize("flow", ["design", "build", "dynamic_review"])
def test_authorized_pinned_preview_lifecycle(tmp_path, flow):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.txt").write_text("pinned")
    runtime = PreviewRuntime(tmp_path / "state", workspace=source,
                             authorization="session-a")
    preview = runtime.register(
        flow=flow, target="abc123", revision=7, source_root=source,
        authorization="session-a", capabilities=capabilities(
            sandbox=True, browser=True, side_panel=True),
        limits={"lifetime_seconds": 60, "cpu_seconds": 10,
                "memory_bytes": 1_000_000}, network_allowlist=[])
    assert preview["state"] == "registered"
    assert preview["surface"] == "side_panel"
    opened = runtime.open(preview["preview_id"])
    build = runtime.record_stage(preview["preview_id"], stage="build",
                                 outcome="succeeded", detail="bundle ready")
    evidence = runtime.observe(preview["preview_id"], interaction="click",
                               result="detail opened")
    closed = runtime.close(preview["preview_id"])
    assert opened["state"] == "open"
    assert build["target"] == "abc123"
    assert evidence["target"] == "abc123"
    assert evidence["revision"] == 7
    assert closed["state"] == "closed"
    assert closed["teardown"]["outcome"] == "succeeded"
    assert (source / "app.txt").read_text() == "pinned"


@pytest.mark.parametrize("case", [
    "unavailable", "denied", "escaped_path", "external_network",
    "attempted_push", "public_exposure",
])
def test_preview_failures_are_explicit_and_audited(tmp_path, case):
    source = tmp_path / "source"
    source.mkdir()
    runtime = PreviewRuntime(tmp_path / "state", workspace=source,
                             authorization="session-a")
    kwargs = dict(flow="build", target="abc", revision=1,
                  source_root=source, authorization="session-a",
                  capabilities=capabilities(sandbox=True, browser=True),
                  limits={"lifetime_seconds": 10, "cpu_seconds": 2,
                          "memory_bytes": 100_000}, network_allowlist=[])
    if case == "unavailable":
        kwargs["capabilities"] = capabilities()
    elif case == "denied":
        kwargs["authorization"] = "wrong"
    elif case == "escaped_path":
        kwargs["source_root"] = tmp_path
    elif case == "external_network":
        kwargs["network_allowlist"] = ["example.com"]
    elif case == "public_exposure":
        kwargs["visibility"] = "public"
    preview = None
    if case in {"unavailable", "denied", "escaped_path",
                "external_network", "public_exposure"}:
        with pytest.raises(PreviewDenied) as error:
            runtime.register(**kwargs)
        assert error.value.outcome == case
        assert runtime.audit()[-1]["outcome"] == case
    else:
        preview = runtime.register(**kwargs)
        failed = runtime.record_outcome(preview["preview_id"], case)
        assert failed["state"] == "failed"
        assert failed["outcome"] == case


@pytest.mark.parametrize("outcome", ["build_failed", "timed_out",
                                      "teardown_failed"])
def test_terminal_faults_never_synthesize_success(tmp_path, outcome):
    source = tmp_path / "source"
    source.mkdir()
    runtime = PreviewRuntime(tmp_path / "state", workspace=source,
                             authorization="a")
    preview = runtime.register(
        flow="dynamic_review", target="pin", revision=2,
        source_root=source, authorization="a",
        capabilities=capabilities(sandbox=True, hosting=True, browser=True),
        limits={"lifetime_seconds": 1, "cpu_seconds": 1,
                "memory_bytes": 1000}, network_allowlist=[])
    failed = runtime.record_outcome(preview["preview_id"], outcome)
    assert failed["state"] == "failed"
    assert not failed.get("succeeded", False)
    assert failed["teardown"]["attempted"] is True
