"""Exact-candidate integration proof for the complete R-0002 H2 surface."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "taskplane"))

from taskplane import (  # noqa: E402
    command_adapters, depgraph, design_sweep, dispatch_telemetry,
    graph_primitives, lens, loop, native_authority, preview_runtime, review, tp,
)
from taskplane.delivery_ports import (  # noqa: E402
    FakeClock, TrustedGitInspector,
)


# Every production/configuration input at the H2 join is bound to the same
# trusted Git snapshot. In particular, the three native delivery roots are
# included alongside their H2/HX consumers instead of treating leaf tests as
# evidence that the live composition still reaches them.
EXACT_CANDIDATE_INPUTS = (
    ".github/workflows/ci.yml",
    "pyproject.toml",
    "requirements-dev.lock",
    "components.yaml",
    "design/contract.json",
    "plan/tasks.json",
    "lenses/catalog.json",
    "taskplane/loop.py",
    "taskplane/build_c.py",
    "taskplane/tp.py",
    "taskplane/plan_topology.py",
    "taskplane/command_runtime.py",
    "taskplane/command_adapters.py",
    "taskplane/native_authority.py",
    "taskplane/design_sweep.py",
    "taskplane/preview_runtime.py",
    "taskplane/review.py",
    "taskplane/dispatch_telemetry.py",
    "taskplane/depgraph.py",
    "taskplane/graph_primitives.py",
    "taskplane/lens.py",
    "taskplane/glob_match.py",
)
QUALITY_PINS = {
    "mypy": "1.17.1",
    "mypy-extensions": "1.1.0",
    "pathspec": "1.1.1",
    "ruff": "0.12.9",
    "typing-extensions": "4.16.0",
}


def _canonical_fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _exact_candidate_bytes(candidate_sha: str,
                           evidence: dict[str, str]) -> dict[str, bytes]:
    """Read every input from Git and bind it to trusted snapshot evidence."""
    assert set(evidence) == set(EXACT_CANDIDATE_INPUTS)
    blobs: dict[str, bytes] = {}
    for relative in EXACT_CANDIDATE_INPUTS:
        retained = design_sweep.retained_repository_bytes(
            ROOT, relative, maximum=8_000_000, revision=candidate_sha)
        assert (ROOT / relative).read_bytes() == retained, (
            f"{relative} drifted from the exact candidate")
        assert hashlib.sha256(retained).hexdigest() == evidence[relative]
        blobs[relative] = retained
    return blobs


def _quality_errors(ci: str, policy: str, lock: str) -> list[str]:
    errors: list[str] = []
    job_match = re.search(
        r"(?ms)^  python-quality:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)",
        ci,
    )
    job = job_match.group(1) if job_match else ""
    for fragment in (
        "name: Python quality (ruff + strict mypy)",
        "runs-on: ubuntu-latest",
        'python-version: "3.14"',
        "python -m pip install --disable-pip-version-check",
        "--require-hashes -r requirements-dev.lock",
        "python -m ruff check --output-format=github taskplane hooks scripts",
        "python -m mypy --strict --config-file pyproject.toml",
    ):
        if fragment not in job:
            errors.append(f"missing quality CI edge: {fragment}")
    if re.search(r"(?m)^\s*if\s*:", job):
        errors.append("quality CI edge is conditionally disabled")
    if "continue-on-error:" in job or "strategy:" in job:
        errors.append("quality CI edge is non-blocking or matrix-expanded")
    for fragment in (
        'files = ["taskplane/*.py"]',
        'target-version = "py310"',
        'python_version = "3.10"',
        "strict = true",
        "warn_unused_configs = true",
        "warn_unused_ignores = true",
        "incremental = false",
    ):
        if fragment not in policy:
            errors.append(f"missing strict quality policy: {fragment}")

    versions: dict[str, str] = {}
    hashed: set[str] = set()
    current = ""
    for raw in lock.splitlines():
        line = raw.strip()
        match = re.fullmatch(
            r"([A-Za-z0-9_-]+)==([A-Za-z0-9_.+-]+)\s*\\?", line)
        if match:
            current = match.group(1).lower()
            versions[current] = match.group(2)
        elif line.startswith("--hash=sha256:") and current and re.fullmatch(
                r"--hash=sha256:[0-9a-f]{64}\s*\\?", line):
            hashed.add(current)
    if versions != QUALITY_PINS:
        errors.append("quality tool versions are not the exact reviewed pins")
    if hashed != set(QUALITY_PINS):
        errors.append("quality tool artifacts are not all hash-bound")
    return errors


def _matcher_errors() -> list[str]:
    errors: list[str] = []
    shared_matcher = sys.modules["glob_match"]
    if lens.glob_match is not shared_matcher:
        errors.append("lens routing is severed from the shared glob matcher")
    if graph_primitives.glob_match is not shared_matcher:
        errors.append("graph routing is severed from the shared glob matcher")
    corpus = (
        ("src/auth/login.py", "**/auth/**", True),
        ("web/components/Btn.tsx", "**/*.tsx", True),
        ("nested/api/schema.json", "api/*.json", False),
    )
    for path, pattern, expected in corpus:
        shared = shared_matcher.path_matches(path, pattern)
        if shared is not expected:
            errors.append(f"shared matcher parity changed for {path}:{pattern}")
        if lens._match(path, pattern) is not shared:
            errors.append(f"lens matcher parity changed for {path}:{pattern}")
        if graph_primitives._match(path, pattern) is not shared:
            errors.append(f"graph matcher parity changed for {path}:{pattern}")
    return errors


def _graph_errors(proof: dict) -> list[str]:
    errors = list(proof.get("errors") or [])
    expected = {
        "status": "complete", "complete": True, "truncated": False,
        "node_count": 14, "edge_count": 24,
        "current_design_edge_count": 23,
    }
    for key, value in expected.items():
        if proof.get(key) != value:
            errors.append(f"architecture proof {key} is not {value!r}")
    registry = proof.get("semantic_endpoint_registry") or {}
    if registry != {
            "schema": depgraph.SEMANTIC_ENDPOINT_REGISTRY_SCHEMA,
            "count": len(depgraph._SEMANTIC_ENDPOINT_REGISTRY),
            "fingerprint": depgraph._SEMANTIC_ENDPOINT_REGISTRY_FINGERPRINT,
    }:
        errors.append("semantic endpoint registry authority changed")
    if proof.get("observed_edges") != proof.get("declared_edges"):
        errors.append("accepted architecture edges were not all observed")
    for authority in ("accepted_authority", "current_design_authority"):
        if not all(str(value or "") for value in
                   (proof.get(authority) or {}).values()):
            errors.append(f"{authority} is incomplete")
    return errors


def _real_retained_audit() -> Path:
    evidence = native_authority.retained_r0013_sweep_evidence()
    path = Path(evidence["codex_audit_path"])
    assert path.is_file(), "the retained R-0013 native audit is unavailable"
    assert _file_sha256(path) == \
        evidence["expected_source_log_sha256"]
    return path


def _production_gate(audit_path: Path) -> dict:
    """Run the supported executable CLI, not a fabricated authority fixture."""
    completed = subprocess.run(
        [
            sys.executable, str(Path(tp.__file__).resolve()),
            "production-gate", "--workspace", str(ROOT),
            "--audit-path", str(audit_path),
        ],
        cwd=ROOT, env=dict(os.environ), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout)


def _loop_plan_gate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        candidate_sha: str, git_executable: str,
        audit_path: Path) -> tuple[dict, dict]:
    """Exercise loop.gate with real retained authority and its refusal edge."""
    checkout = tmp_path / "loop-candidate"
    subprocess.run(
        [git_executable, "clone", "--quiet", "--shared", str(ROOT),
         str(checkout)],
        cwd=ROOT, env=dict(os.environ), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )
    clone_snapshot = TrustedGitInspector(git_executable).snapshot(checkout)
    assert clone_snapshot.head_sha == candidate_sha

    monkeypatch.setenv("TASKPLANE_HOME", str(tmp_path / "loop-home"))
    monkeypatch.setenv("TASKPLANE_R0013_CODEX_AUDIT", str(audit_path))
    monkeypatch.delenv("TASKPLANE_STAGE_NATIVE", raising=False)
    initialized = loop.init(
        str(checkout), "H2 production composition proof",
        spec_path="specs/spec.md", checkpoints=["plan"])
    assert initialized.get("step") == "plan" and not initialized.get("error")
    live = loop.gate(str(checkout), "pass")
    assert "retained R-0013 production authority" not in json.dumps(live)

    monkeypatch.setenv(
        "TASKPLANE_R0013_CODEX_AUDIT", str(tmp_path / "missing-audit.jsonl"))
    unavailable = loop.gate(str(checkout), "pass")
    assert "retained R-0013 production authority" in json.dumps(unavailable)
    assert "unavailable" in json.dumps(unavailable).lower()
    return live, unavailable


def _preview_via_cli(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        candidate_sha: str) -> tuple[dict, dict]:
    source = tmp_path / "preview-source"
    source.mkdir()
    (source / "app.py").write_text(
        "print('exact candidate preview')\n", encoding="utf-8")
    state = tmp_path / "preview-state"
    isolation_calls: list[tuple[list[str], Path, dict]] = []
    surface_calls: list[tuple[str, Path, str]] = []

    def isolate(command, cwd, policy):
        isolation_calls.append((list(command), Path(cwd), dict(policy)))
        return command_adapters.HostLaunch(
            binding={"native": "h2-integration-isolation"},
            isolation={
                "schema": "taskplane.preview-isolation-receipt/v1",
                "network": "denied", "scope": "complete-process-tree",
                "push": "denied", "filesystem": "sandbox-only",
                "source": "immutable", "remotes": "disabled",
                "cpu": "rlimit-enforced", "memory": "rlimit-enforced",
                "mechanism": "h2-deterministic-host-seam",
                "policy_fingerprint": _canonical_fingerprint(dict(policy)),
                "process_ownership": {
                    "schema": "taskplane.preview-process-ownership/v1",
                    "pid": 101, "pgid": 101, "started": "fixture-command",
                    "role": "preview-command", "generation": 1,
                },
            })

    def surface(name, sandbox, preview):
        surface_calls.append((name, Path(sandbox), preview["flow"]))
        return {
            "schema": "taskplane.host-preview-surface/v1",
            "surface": name, "binding": "h2-integration-surface",
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
        "flow": "build", "host": "codex", "state_root": str(state),
        "source_root": str(source), "authorization": candidate_sha,
        "target": candidate_sha, "revision": 1,
        "capabilities": {
            "sandbox": {"status": "supported", "source": "native"},
            "browser": {"status": "supported", "source": "native"},
        },
        "command": ["python3", "app.py"],
        "limits": {
            "lifetime_seconds": 60, "cpu_seconds": 10,
            "memory_bytes": 1_000_000, "startup_entries": 1,
            "startup_file_bytes": 64, "startup_total_bytes": 64,
            "startup_seconds": 5,
        },
    }
    request_path = tmp_path / "preview-request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    assert tp.main(["preview", "--request", str(request_path)]) == 0
    launched = json.loads(capsys.readouterr().out)
    assert isolation_calls[0][0] == ["python3", "app.py"]
    assert isolation_calls[0][1].is_dir()
    assert isolation_calls[0][2]["scope"] == "complete-process-tree"
    assert surface_calls == [("browser", isolation_calls[0][1], "build")]

    # Registration and opening happened through the CLI. A restarted real
    # runtime owns only the terminal close; the deterministic teardown avoids
    # signalling the fixture process identities.
    runtime = preview_runtime.PreviewRuntime(
        state / "previews", workspace=source, authorization=candidate_sha,
        process_teardown=lambda _preview_id, _ownership: True)
    closed = runtime.close(launched["preview"]["preview_id"])
    return launched, closed


def _usage_projection(tmp_path: Path) -> tuple[dict, dict]:
    transcript = tmp_path / "native-session.jsonl"
    transcript.write_text(json.dumps({"message": {
        "id": "h2-native-observation", "usage": {
            "input_tokens": 12,
            "input_tokens_details": {"cached_tokens": 4},
            "output_tokens": 3, "total_tokens": 15,
        },
    }}) + "\n", encoding="utf-8")
    projection, checkpoint = dispatch_telemetry.project_transcript_usage(
        str(transcript), provider="codex", byte_limit=4096)
    assert checkpoint is not None
    return projection, checkpoint


def _ledger(candidate_sha: str, *, usage: dict | None) -> dict:
    ledger = dispatch_telemetry.new_ledger(
        run_id="h2-integration", source_sha=candidate_sha,
        design_fingerprint="design-h2", plan_fingerprint="plan-h2",
        started_at=1,
    )
    dispatch_telemetry.bind_dispatch(ledger, {
        "dispatch_id": "h2-native", "thread_id": "h2-native-thread",
        "thread_type": "worker", "task_id": "H2-I",
        "dependencies": ["H2-A", "H2-B", "H2-C", "HX-GRAPH"],
        "shared_owner": None, "started_at": 2, "ended_at": 2,
        "wait_duration_seconds": 0, "correction_count": 0, "events": [],
    }, usage=usage, source_fingerprint="a" * 64 if usage is not None else None)
    return ledger


def _screen(ledger: dict) -> dict:
    return dispatch_telemetry.screen_dispatch(
        ledger, FakeClock(wall_time=3), current_stage="build",
        outstanding_set_fingerprint="b" * 64,
        preserved_context_fingerprint="c" * 64,
    )


def _candidate_copy(tmp_path: Path) -> Path:
    target = tmp_path / "candidate-copy"
    shutil.copytree(
        ROOT, target, symlinks=True,
        ignore=shutil.ignore_patterns(
            ".git", ".pytest_cache", ".mypy_cache", "__pycache__"),
    )
    return target


def _rewrite_design(root: Path, mutate) -> None:
    path = root / "design/contract.json"
    design = json.loads(path.read_text(encoding="utf-8"))
    mutate(design)
    path.write_text(json.dumps(design), encoding="utf-8")


def test_ac3_live_wiring_bounds_and_quality(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """All H2 producers close together against one immutable candidate."""
    inspector = TrustedGitInspector()
    before = inspector.snapshot(ROOT, evidence_paths=EXACT_CANDIDATE_INPUTS)
    candidate_sha = before.head_sha
    candidate_tree = before.tree_sha
    blobs = _exact_candidate_bytes(
        candidate_sha, dict(before.evidence_sha256))

    assert re.fullmatch(r"[0-9a-f]{40,64}", candidate_sha)
    assert re.fullmatch(r"[0-9a-f]{40,64}", candidate_tree)
    assert _quality_errors(
        blobs[".github/workflows/ci.yml"].decode(),
        blobs["pyproject.toml"].decode(),
        blobs["requirements-dev.lock"].decode(),
    ) == []

    audit_path = _real_retained_audit()
    authority = _production_gate(audit_path)
    assert authority["schema"] == native_authority.PRODUCTION_DESIGN_GATE_SCHEMA
    assert authority["status"] == "ready"
    assert authority["authority_revision"] == \
        native_authority.RETAINED_R0013_AUTHORITY_REVISION
    assert authority["authority"]["status"] == "ready"
    assert authority["delivery_roots"]["status"] == "ready"
    assert authority["design_sweep"]["status"] == "ready"
    assert len(authority["fingerprint"]) == 64

    live_gate, unavailable_gate = _loop_plan_gate(
        tmp_path, monkeypatch, candidate_sha, before.git_executable, audit_path)
    assert isinstance(live_gate, dict) and isinstance(unavailable_gate, dict)

    preview, closed = _preview_via_cli(
        tmp_path, monkeypatch, capsys, candidate_sha)
    assert preview["schema"] == "taskplane.working-preview-launch/v1"
    assert preview["flow"] == "build"
    assert preview["preview"]["target"] == candidate_sha
    assert preview["preview"]["state"] == "open"
    assert preview["preview"]["startup_inventory"]["entries"] == 1
    assert preview["preview"]["visibility"] == "private"
    assert preview["preview"]["network"] == {
        "mode": "deny", "allowlist": []}
    assert closed["state"] == "closed" and closed["outcome"] == "succeeded"

    budget = tp._standalone_review_budget(None)
    assert budget["max_tokens"] == 25_000_000
    assert budget["token_usage_required"] is True
    projection, checkpoint = _usage_projection(tmp_path)
    assert projection["status"] == "available"
    assert projection["usage"]["total_tokens"] == 15
    assert projection["byte_limit"] == 4096
    assert checkpoint["offset"] == projection["bytes_read"]
    positive = _screen(_ledger(
        candidate_sha, usage={
            "input_tokens": 12, "cached_input_tokens": 4,
            "uncached_input_tokens": 8, "output_tokens": 3,
            "reasoning_tokens": 0, "total_tokens": 15,
        }))
    assert positive["source_sha"] == candidate_sha
    assert positive["dispatch_allowed"] is True
    assert positive["usage_capability"]["enforcement"] == "host-observed"
    assert positive["usage_capability"]["observed_tokens"] == 15

    host_log = tmp_path / "bounded-review.jsonl"
    host_log.write_bytes(
        (b'{"old":"discarded"}\n' * 100) + b'{"tail":"retained"}\n')
    monkeypatch.setattr(review, "MAX_HOST_TRANSCRIPT_BYTES", 128)
    records = review._host_review_records(str(host_log))
    assert records[-1] == {"tail": "retained"}
    assert len(records) < 100

    graph = depgraph.architecture_map_proof(str(ROOT))
    assert _graph_errors(graph) == []
    assert _matcher_errors() == []

    unchanged = inspector.assert_unchanged(before)
    assert unchanged.head_sha == candidate_sha
    assert unchanged.tree_sha == candidate_tree


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    (
        ("python-quality:\n", "python-quality:\n    if: false\n",
         "conditionally disabled"),
        ("  python-quality:\n"
         "    name: Python quality (ruff + strict mypy)\n"
         "    runs-on: ubuntu-latest",
         "  python-quality:\n"
         "    name: Python quality (ruff + strict mypy)\n"
         "    continue-on-error: true\n"
         "    runs-on: ubuntu-latest",
         "non-blocking"),
        ("python -m ruff check", "echo ruff check", "missing quality CI edge"),
    ),
)
def test_ac3_disabled_or_nonblocking_quality_job_fails_closed(
        old: str, new: str, reason: str) -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    policy = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "requirements-dev.lock").read_text(encoding="utf-8")
    assert old in ci
    errors = _quality_errors(ci.replace(old, new, 1), policy, lock)
    assert any(reason in error for error in errors)


def test_ac3_severed_matcher_consumer_fails_closed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lens, "glob_match",
        SimpleNamespace(path_matches=sys.modules["glob_match"].path_matches))
    assert _matcher_errors() == [
        "lens routing is severed from the shared glob matcher"]


def test_ac3_mutated_accepted_graph_edge_fails_closed(tmp_path: Path) -> None:
    root = _candidate_copy(tmp_path)

    def mutate(design: dict) -> None:
        architecture = design["architecture_decomposition"]
        architecture["semantic_edges"][0]["reason"] = "mutated authority"
        material = {key: value for key, value in architecture.items()
                    if key != "content_fingerprint"}
        architecture["content_fingerprint"] = _canonical_fingerprint(material)

    _rewrite_design(root, mutate)
    errors = _graph_errors(depgraph.architecture_map_proof(str(root)))
    assert any("immutable 24-edge authority" in error for error in errors)


def test_ac3_mutated_registry_fails_closed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    registry = set(depgraph._SEMANTIC_ENDPOINT_REGISTRY)
    registry.remove("contract:delivery.production-wiring")
    monkeypatch.setattr(
        depgraph, "_SEMANTIC_ENDPOINT_REGISTRY", frozenset(registry))
    monkeypatch.setattr(
        depgraph, "_SEMANTIC_ENDPOINT_REGISTRY_FINGERPRINT",
        _canonical_fingerprint(sorted(registry)))
    errors = _graph_errors(depgraph.architecture_map_proof(str(ROOT)))
    assert any("unregistered semantic endpoint" in error for error in errors)


def test_ac3_mutated_current_graph_edge_fails_closed(tmp_path: Path) -> None:
    root = _candidate_copy(tmp_path)

    def mutate(design: dict) -> None:
        design["graph"]["proposed_edges"][0]["reason"] = \
            "mutated current authority"

    _rewrite_design(root, mutate)
    errors = _graph_errors(depgraph.architecture_map_proof(str(root)))
    assert any("approved authority for R-0002" in error for error in errors)


def test_ac3_h33_missing_host_usage_fails_dispatch_screen() -> None:
    candidate_sha = TrustedGitInspector().snapshot(ROOT).head_sha
    unavailable = _screen(_ledger(candidate_sha, usage=None))
    assert unavailable["status"] == "human_scope_review"
    assert unavailable["dispatch_allowed"] is False
    assert unavailable["usage_capability"] == {
        "schema": dispatch_telemetry.USAGE_CAPABILITY_SCHEMA,
        "status": "unavailable", "budget_claim": False,
        "enforcement": "not-enforced", "observed_tokens": None,
        "reason": "active native usage is missing before the next dispatch",
    }
    assert unavailable["checkpoint"]["resume_allowed"] is False
    assert "missing" in unavailable["checkpoint"][
        "reason_in_user_language"].lower()


def test_ac3_preview_bound_failure_uses_supported_cli(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "over-bound-preview"
    source.mkdir()
    (source / "a.py").write_bytes(b"a")
    (source / "b.py").write_bytes(b"b")
    request = {
        "flow": "build", "host": "codex",
        "state_root": str(tmp_path / "over-bound-state"),
        "source_root": str(source), "authorization": "candidate",
        "target": "candidate", "revision": 1,
        "capabilities": {
            "sandbox": {"status": "supported", "source": "native"},
            "browser": {"status": "supported", "source": "native"},
        },
        "command": ["python3", "a.py"],
        "limits": {
            "lifetime_seconds": 60, "cpu_seconds": 10,
            "memory_bytes": 1_000_000, "startup_entries": 1,
        },
    }
    request_path = tmp_path / "over-bound-preview.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    assert tp.main(["preview", "--request", str(request_path)]) == 1
    denied = json.loads(capsys.readouterr().out)
    assert denied["status"] == "unavailable"
    assert "entry limit" in denied["error"]
