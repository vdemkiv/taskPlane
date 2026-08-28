"""Exact-candidate integration proof for the complete R-0002 H2 surface."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "taskplane"))

from taskplane import (  # noqa: E402
    depgraph, design_sweep, dispatch_telemetry, graph_primitives, lens,
    native_authority, preview_runtime, review, tp,
)
from taskplane.delivery_ports import FakeClock  # noqa: E402


EXACT_CANDIDATE_INPUTS = (
    ".github/workflows/ci.yml",
    "pyproject.toml",
    "requirements-dev.lock",
    "design/contract.json",
    "plan/tasks.json",
    "components.yaml",
    "taskplane/native_authority.py",
    "taskplane/design_sweep.py",
    "taskplane/preview_runtime.py",
    "taskplane/tp.py",
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


def _head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        encoding="utf-8", errors="replace",
    ).strip()


def _exact_candidate_bytes(relative: str, candidate_sha: str) -> bytes:
    """Read one candidate blob through the production retained-Git reader."""
    retained = design_sweep.retained_repository_bytes(
        ROOT, relative, maximum=8_000_000, revision=candidate_sha)
    working = (ROOT / relative).read_bytes()
    assert working == retained, f"{relative} drifted from the exact candidate"
    return retained


def _quality_errors(ci: str, policy: str, lock: str) -> list[str]:
    errors = []
    job_match = re.search(
        r"(?ms)^  python-quality:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)",
        ci,
    )
    job = job_match.group(1) if job_match else ""
    for fragment in (
        "name: Python quality (ruff + strict mypy)",
        "--require-hashes -r requirements-dev.lock",
        "python -m ruff check --output-format=github taskplane hooks scripts",
        "python -m mypy --strict --config-file pyproject.toml",
    ):
        if fragment not in job:
            errors.append(f"missing quality CI edge: {fragment}")
    if "continue-on-error:" in job or "strategy:" in job:
        errors.append("quality CI edge is non-blocking or matrix-expanded")
    for fragment in (
        'files = ["taskplane/*.py"]',
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


def _retained_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build native audit transport from exact retained result artifacts."""
    revision = native_authority.RETAINED_R0013_AUTHORITY_REVISION
    catalog = json.loads(design_sweep.retained_repository_bytes(
        ROOT, "lenses/catalog.json", maximum=2_000_000,
        revision=revision))
    thread = "01a00000-0000-7000-8000-000000000021"
    turn = "01a00000-0000-7000-8000-000000000022"
    rows = [{
        "timestamp": "1970-01-01T00:00:01Z", "ordinal": 0,
        "type": "session_meta", "payload": {"id": thread,
        "session_id": thread},
    }]
    for index, row in enumerate(catalog["lenses"]):
        lens_id = row["id"]
        agent = f"/root/r0013_design_lens_{lens_id.replace('-', '_')}"
        result = design_sweep.retained_repository_bytes(
            ROOT, f"design/lens-evidence/{lens_id}.json",
            maximum=2_000_000, revision=revision)
        rows.extend((
            {
                "timestamp": f"1970-01-01T00:01:{index:02d}Z",
                "ordinal": index * 2 + 1, "type": "event_msg",
                "payload": {
                    "type": "item_completed", "thread_id": thread,
                    "turn_id": turn, "started_at_ms": 100_000 + index,
                    "completed_at_ms": 100_000 + index,
                    "item": {
                        "type": "SubAgentActivity", "id": f"start-{index:02d}",
                        "kind": "started", "agent_thread_id": f"agent-{index:02d}",
                        "agent_path": agent,
                    },
                },
            },
            {
                "timestamp": f"1970-01-01T00:03:{index:02d}Z",
                "ordinal": index * 2 + 2, "type": "response_item",
                "payload": {
                    "type": "agent_message", "id": f"final-{index:02d}",
                    "author": agent, "recipient": "/root",
                    "content": [{"type": "input_text", "text": (
                        "Message Type: FINAL_ANSWER\nTask name: /root\n"
                        f"Sender: {agent}\nPayload:\n"
                        "taskplane-result-path:"
                        f"design/lens-evidence/{lens_id}.json\n"
                        "taskplane-result-sha256:"
                        f"{hashlib.sha256(result).hexdigest()}"
                    )}],
                    "internal_chat_message_metadata_passthrough": {
                        "turn_id": turn, "create_time": 200.0 + index,
                    },
                },
            },
        ))
    raw = b"".join(json.dumps(
        row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for row in rows)
    audit = tmp_path / "retained-r0013-audit.jsonl"
    audit.write_bytes(raw)
    monkeypatch.setattr(native_authority, "RETAINED_R0013_SOURCE_THREAD", thread)
    monkeypatch.setattr(native_authority, "RETAINED_R0013_DESIGN_TURN", turn)
    monkeypatch.setattr(
        native_authority, "RETAINED_R0013_AUDIT_SHA256",
        hashlib.sha256(raw).hexdigest())
    return audit


def _preview(tmp_path: Path, candidate_sha: str) -> tuple[dict, dict]:
    source = tmp_path / "preview-source"
    source.mkdir()
    payload = b"print('exact candidate preview')\n"
    (source / "app.py").write_bytes(payload)
    runtime = preview_runtime.PreviewRuntime(
        tmp_path / "preview-state", workspace=source,
        authorization=candidate_sha)
    preview = runtime.register(
        flow="design", target=candidate_sha, revision=1,
        source_root=source, authorization=candidate_sha,
        capabilities={
            "sandbox": {"status": "supported", "source": "native"},
            "browser": {"status": "supported", "source": "native"},
        },
        limits={
            "lifetime_seconds": 60, "cpu_seconds": 10,
            "memory_bytes": 1_000_000, "startup_entries": 1,
            "startup_file_bytes": 64, "startup_total_bytes": 64,
            "startup_seconds": 5,
        },
        network_allowlist=[],
    )
    return preview, runtime.close(preview["preview_id"])


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


def _screen(candidate_sha: str, source_fingerprint: str) -> dict:
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
    }, usage={
        "input_tokens": 12, "cached_input_tokens": 4,
        "uncached_input_tokens": 8, "output_tokens": 3,
        "reasoning_tokens": 0, "total_tokens": 15,
    }, source_fingerprint=source_fingerprint)
    return dispatch_telemetry.screen_dispatch(
        ledger, FakeClock(wall_time=3), current_stage="build",
        outstanding_set_fingerprint="b" * 64,
        preserved_context_fingerprint="c" * 64,
    )


def test_ac3_live_wiring_bounds_and_quality(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """All H2 producers close together against one immutable candidate."""
    candidate_sha = _head()
    blobs = {
        relative: _exact_candidate_bytes(relative, candidate_sha)
        for relative in EXACT_CANDIDATE_INPUTS
    }
    assert re.fullmatch(r"[0-9a-f]{40,64}", candidate_sha)

    assert _quality_errors(
        blobs[".github/workflows/ci.yml"].decode(),
        blobs["pyproject.toml"].decode(),
        blobs["requirements-dev.lock"].decode(),
    ) == []

    audit = _retained_audit(tmp_path, monkeypatch)
    authority = native_authority.validate_retained_r0013_authority(
        ROOT, audit_path=audit)
    assert authority["status"] == "ready"
    assert authority["authority"]["status"] == "ready"
    assert authority["delivery_roots"]["status"] == "ready"
    assert authority["design_sweep"]["status"] == "ready"
    assert len(authority["fingerprint"]) == 64

    preview, closed = _preview(tmp_path, candidate_sha)
    assert preview["target"] == candidate_sha
    assert preview["startup_inventory"]["entries"] == 1
    assert preview["startup_limits"] == {
        "startup_entries": 1, "startup_total_bytes": 64,
        "startup_file_bytes": 64, "startup_seconds": 5,
    }
    assert preview["visibility"] == "private"
    assert preview["network"] == {"mode": "deny", "allowlist": []}
    assert closed["state"] == "closed" and closed["outcome"] == "succeeded"

    budget = tp._standalone_review_budget(None)
    assert budget["max_tokens"] == 25_000_000
    assert budget["token_usage_required"] is True
    projection, checkpoint = _usage_projection(tmp_path)
    assert projection["status"] == "available"
    assert projection["usage"]["total_tokens"] == 15
    assert projection["byte_limit"] == 4096
    assert checkpoint["offset"] == projection["bytes_read"]
    screen = _screen(candidate_sha, projection["source_fingerprint"])
    assert screen["source_sha"] == candidate_sha
    assert screen["dispatch_allowed"] is True
    assert screen["usage_capability"]["enforcement"] == "host-observed"
    assert screen["usage_capability"]["observed_tokens"] == 15

    host_log = tmp_path / "bounded-review.jsonl"
    host_log.write_bytes(
        (b'{"old":"discarded"}\n' * 100) + b'{"tail":"retained"}\n')
    monkeypatch.setattr(review, "MAX_HOST_TRANSCRIPT_BYTES", 128)
    records = review._host_review_records(str(host_log))
    assert records[-1] == {"tail": "retained"}
    assert len(records) < 100

    graph = depgraph.architecture_map_proof(str(ROOT))
    assert graph["status"] == "complete" and graph["complete"] is True
    assert graph["truncated"] is False
    assert graph["node_count"] == 14
    assert graph["edge_count"] == 24
    assert graph["current_design_edge_count"] == 23
    assert graph["errors"] == []
    assert len(graph["fingerprint"]) == 64

    shared_matcher = lens.glob_match
    assert graph_primitives.glob_match is shared_matcher
    corpus = (
        ("src/auth/login.py", "**/auth/**", True),
        ("web/components/Btn.tsx", "**/*.tsx", True),
        ("nested/api/schema.json", "api/*.json", False),
    )
    for path, pattern, expected in corpus:
        assert shared_matcher.path_matches(path, pattern) is expected
        assert lens._match(path, pattern) is expected
        assert graph_primitives._match(path, pattern) is expected


@pytest.mark.parametrize(
    ("target", "old", "new"),
    (
        ("ci", "python -m ruff check", "echo ruff check"),
        ("policy", "strict = true", "strict = false"),
        ("lock", "ruff==0.12.9", "ruff>=0.12.9"),
    ),
)
def test_ac3_quality_mutations_fail_closed(target: str, old: str, new: str) \
        -> None:
    sources = {
        "ci": (ROOT / ".github/workflows/ci.yml").read_text(),
        "policy": (ROOT / "pyproject.toml").read_text(),
        "lock": (ROOT / "requirements-dev.lock").read_text(),
    }
    assert old in sources[target]
    sources[target] = sources[target].replace(old, new, 1)
    assert _quality_errors(sources["ci"], sources["policy"], sources["lock"])


def test_ac3_missing_wiring_bounds_and_graph_fail_closed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "over-bound-preview"
    source.mkdir()
    (source / "a.py").write_bytes(b"a")
    (source / "b.py").write_bytes(b"b")
    runtime = preview_runtime.PreviewRuntime(
        tmp_path / "over-bound-state", workspace=source,
        authorization="candidate")
    with pytest.raises(preview_runtime.PreviewDenied, match="entry limit"):
        runtime.register(
            flow="build", target="candidate", revision=1,
            source_root=source, authorization="candidate",
            capabilities={
                "sandbox": {"status": "supported", "source": "native"},
                "browser": {"status": "supported", "source": "native"},
            },
            limits={
                "lifetime_seconds": 60, "cpu_seconds": 10,
                "memory_bytes": 1_000_000, "startup_entries": 1,
            }, network_allowlist=[])

    oversized = tmp_path / "oversized-transcript.jsonl"
    oversized.write_bytes(b"x" * 65)
    unavailable, checkpoint = dispatch_telemetry.project_transcript_usage(
        str(oversized), provider="codex", byte_limit=64)
    assert unavailable["status"] == "unavailable"
    assert "byte cap" in unavailable["reason"]
    assert checkpoint is None

    broken = tmp_path / "missing-graph" / "design" / "contract.json"
    broken.parent.mkdir(parents=True)
    design = json.loads((ROOT / "design/contract.json").read_text())
    design.pop("architecture_decomposition")
    broken.write_text(json.dumps(design), encoding="utf-8")
    graph = depgraph.architecture_map_proof(str(broken.parents[1]))
    assert graph["status"] == "incomplete"
    assert graph["complete"] is False
    assert any("missing architecture_decomposition" in error
               for error in graph["errors"])

    monkeypatch.setattr(
        native_authority, "validate_delivery_roots",
        lambda _root: (_ for _ in ()).throw(
            native_authority.NativeAuthorityError("severed live roots")),
    )
    with pytest.raises(native_authority.NativeAuthorityError,
                       match="severed live roots"):
        native_authority.validate_production_design_gate(
            ROOT, authority_revision=
            native_authority.RETAINED_R0013_AUTHORITY_REVISION,
            sweep_evidence=native_authority.retained_r0013_sweep_evidence(
                tmp_path / "not-consumed-before-root-validation.jsonl"),
        )
