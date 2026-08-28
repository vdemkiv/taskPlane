"""Exact-candidate integration proof for the complete R-0002 M1 surface."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import builtins
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "taskplane"))

from taskplane import (  # noqa: E402
    build_c,
    depgraph,
    design_sweep,
    dispatch_telemetry,
    lens,
    lens_signals,
    producer_observation,
    repository,
)
from taskplane.delivery_policy import DeliveryPolicyError  # noqa: E402
from taskplane.delivery_ports import TrustedGitInspector  # noqa: E402
from taskplane.tests import test_em_m1_proof_paths as proof_paths  # noqa: E402
from taskplane.tests import test_r0013_design_sweep as design_proof  # noqa: E402


LEAF_COMMITS = {
    "M1-A": "863a361392253e7672786471935a60af5d05c110",
    "M1-B": "e4c324da4b273e0d1bf8d4a58731c97aac230a3e",
    "M1-C": "a607084b4a2085f8436dbc0f4aa116fa51647c61",
    "M1-D": "eaebdfb196865a5f0cae5434a6a114f6b4b55522",
    "M1-E": "7d68bef91a8f4c294b536543b4c823c2158a5d31",
    "M1-F": "d1166e5cfdd303c155bdb1559d69db1c7f3020c6",
    "MX-DOCS-ARCH": "365876a3278c28f7555550d9c246926041e6931b",
}
EXACT_CANDIDATE_INPUTS = (
    "components.yaml",
    "design/contract.json",
    "docs/loop-design.md",
    ".github/workflows/ci.yml",
    "requirements-dev.lock",
    "CONTRIBUTING.md",
    "scripts/render_readme_gif.py",
    "taskplane/depgraph.py",
    "taskplane/dispatch_telemetry.py",
    "taskplane/lens.py",
    "taskplane/repository.py",
    "taskplane/build_c.py",
    "taskplane/tests/__init__.py",
    "taskplane/tests/test_em_m1_architecture.py",
    "taskplane/tests/test_em_mx_loop_docs.py",
    "taskplane/tests/test_em_m1_typing_cost.py",
    "taskplane/tests/test_em_m1_ci_dependencies.py",
    "taskplane/tests/test_em_m1_proof_paths.py",
    "taskplane/tests/test_r0013_design_sweep.py",
    "taskplane/tests/test_r0001_live_host_canary.py",
    "taskplane/tests/test_em_m1_repository.py",
    "taskplane/tests/test_repository_preflight.py",
    "taskplane/tests/test_em_m1_test_isolation.py",
    "taskplane/tests/test_em_m1_integration.py",
)
READ_ONLY_JOBS = (
    "tests", "python-quality", "zero-token-corpus", "wave3-contracts",
    "tests-portability", "validate-plugin", "docs-truth", "codex-parity",
    "codex-host",
)
TEST_JOBS = (
    "tests", "wave3-contracts", "tests-portability", "codex-parity",
    "codex-host", "release-tags",
)
TEST_TREE = {
    "pytest": "9.1.1",
    "colorama": "0.4.6",
    "exceptiongroup": "1.3.0",
    "iniconfig": "2.3.0",
    "packaging": "26.3",
    "pluggy": "1.6.0",
    "pygments": "2.21.0",
    "tomli": "2.2.1",
    "typing-extensions": "4.16.0",
}


def _candidate_blobs(snapshot) -> dict[str, bytes]:
    assert set(snapshot.evidence_sha256) == set(EXACT_CANDIDATE_INPUTS)
    blobs = {}
    for relative in EXACT_CANDIDATE_INPUTS:
        value = design_sweep.retained_repository_bytes(
            ROOT, relative, maximum=8_000_000, revision=snapshot.head_sha)
        assert value == (ROOT / relative).read_bytes()
        assert hashlib.sha256(value).hexdigest() == \
            snapshot.evidence_sha256[relative]
        blobs[relative] = value
    return blobs


def _ancestry_errors(candidate_sha: str,
                     leaves: dict[str, str] = LEAF_COMMITS) -> list[str]:
    errors = []
    for task_id, leaf_sha in leaves.items():
        result = subprocess.run(
            ["/usr/bin/git", "merge-base", "--is-ancestor",
             leaf_sha, candidate_sha],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            errors.append(f"{task_id} leaf {leaf_sha} is not an ancestor")
    return errors


def _job(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)",
        source,
    )
    return match.group(1) if match else ""


def _profile(source: str, name: str) -> str:
    prefix = f"# {name}: "
    return "\n".join(line.removeprefix(prefix)
                     for line in source.splitlines()
                     if line.startswith(prefix))


def _pins(profile: str) -> tuple[dict[str, str], set[str], list[str]]:
    versions: dict[str, str] = {}
    hashed: set[str] = set()
    invalid = []
    current = ""
    for raw in profile.splitlines():
        line = raw.strip()
        requirement = re.fullmatch(
            r"([A-Za-z0-9_-]+)==([A-Za-z0-9_.+-]+)"
            r"(?:\s*;\s*[^\\]+)?\s*\\?",
            line,
        )
        if requirement:
            current = requirement.group(1).lower()
            versions[current] = requirement.group(2)
        elif re.fullmatch(r"--hash=sha256:[0-9a-f]{64}\s*\\?", line) \
                and current:
            hashed.add(current)
        else:
            invalid.append(line)
    return versions, hashed, invalid


def _ci_errors(ci: str, lock: str, contributing: str,
               renderer: str, runtime: str) -> list[str]:
    errors = []
    for name in READ_ONLY_JOBS:
        job = _job(ci, name)
        if not job or "persist-credentials: false" not in job:
            errors.append(f"{name} retains checkout credentials")
    for name in TEST_JOBS:
        job = _job(ci, name)
        for fragment in (
            "Install hash-locked sealed test dependency tree",
            "requirements-dev.lock",
            "--require-hashes --no-deps",
        ):
            if fragment not in job:
                errors.append(f"{name} misses sealed install: {fragment}")
    if re.search(r"pip install\s+[\"']?pytest(?:==|[\"'])", ci):
        errors.append("CI directly installs a moving pytest")
    versions, hashes, invalid = _pins(_profile(lock, "test-lock"))
    if versions != TEST_TREE or hashes != set(TEST_TREE) or invalid:
        errors.append("pytest dependency tree is not exact and hash sealed")
    asset_versions, asset_hashes, asset_invalid = _pins(
        _profile(lock, "asset-lock"))
    if asset_versions != {"pillow": "12.2.0"} or \
            asset_hashes != {"pillow"} or asset_invalid:
        errors.append("Pillow asset dependency is not exact and hash sealed")
    build_versions, build_hashes, build_invalid = _pins(
        _profile(lock, "asset-build-lock"))
    if build_versions != {"setuptools": "80.9.0", "wheel": "0.45.1"} or \
            build_hashes != set(build_versions) or build_invalid:
        errors.append("asset build dependencies are not exact and hash sealed")
    for fragment in (
        "--require-hashes --no-deps --only-binary=:all: ",
        "--require-hashes --no-deps --no-binary=Pillow ",
        "--no-build-isolation",
        "python3 scripts/render_readme_gif.py",
        "git diff --exit-code -- docs/assets/taskplane-cowork-flow.gif",
    ):
        if fragment not in contributing:
            errors.append(f"asset regeneration misses: {fragment}")
    for fragment in (
        'parser.add_argument("--output"',
        "Image.Quantize.MEDIANCUT",
        "Image.Dither.NONE",
    ):
        if fragment not in renderer:
            errors.append(f"asset renderer misses: {fragment}")
    if re.search(r"(?m)^\s*(?:from\s+PIL\s+import|import\s+PIL\b)", runtime):
        errors.append("Pillow leaked into the stdlib-only runtime")
    return errors


def _docs_errors(document: str) -> list[str]:
    errors = []
    required = (
        "tp.py loop submit [pass|fail|unavailable]",
        "Only an EVALUATE worker may submit it",
        "Accepted decision `D-LOOP-STAGE-MIGRATION`",
        "Both conditions are required",
        "`D-LOOP-ENGINE-OWNERSHIP/v1`",
        "host orchestrator owns native worker lifecycle",
    )
    normalized = " ".join(document.split())
    for item in required:
        if item not in normalized:
            errors.append(f"loop design omits accepted fact: {item}")
    match = re.search(
        r"### Decision record: `D-LOOP-ENGINE-OWNERSHIP/v1`.*?"
        r"```json\n(?P<record>.*?)\n```",
        document,
        re.DOTALL,
    )
    if not match:
        errors.append("loop ownership decision record is unavailable")
        return errors
    try:
        record = json.loads(match.group("record"))
    except json.JSONDecodeError:
        errors.append("loop ownership decision record is not JSON")
        return errors
    if record.get("status") != "ACTIVE" or \
            record.get("owner") != "taskplane-loop-engine":
        errors.append("loop ownership decision is not active and owned")
    owners = record.get("authority_owners") or {}
    if owners.get("native_worker_dispatch_start_stop_and_wait") != \
            "host-orchestrator":
        errors.append("host-native lifecycle owner is not recorded")
    alternatives = record.get("alternatives") or []
    if len(alternatives) < 3 or sum(
            row.get("disposition") == "SELECTED" for row in alternatives) != 1:
        errors.append("loop ownership alternatives are incomplete")
    trigger = record.get("revisit_trigger") or {}
    if trigger.get("required_start_stop_receipt_pairing_percent") != 100 or \
            trigger.get("maximum_orphaned_worker_identities") != 0:
        errors.append("loop ownership revisit trigger is not measurable")
    return errors


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def wall_time(self) -> float:
        return 1_800_000_000.0 + self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def _repository_retry(monkeypatch: pytest.MonkeyPatch,
                      tmp_path: Path) -> dict:
    clock = _Clock()
    manager = repository.RepositoryManager(home=str(tmp_path / "repository"))
    calls = []

    def run(argv, **_kwargs):
        calls.append(list(argv))
        if len(calls) == 1:
            raise repository.RepositoryAcquisitionError(
                "network", "RPC failed; HTTP 400 integration")
        return "ready"

    monkeypatch.setattr(manager, "_run", run)
    result = repository.acquire_with_recovery(
        lambda: manager._fetch([
            "git", "fetch", "origin",
            "+refs/pull/7/head:refs/taskplane/pr/7/head",
        ]),
        deadline_seconds=10,
        base_backoff_seconds=0,
        max_backoff_seconds=0,
        monotonic=clock.monotonic,
        wall_time=clock.wall_time,
        sleep=clock.sleep,
        random_value=lambda: 0,
    )
    assert result["status"] == "ready" and result["attempts"] == 2
    assert len(calls) == 2 and clock.sleeps == []
    assert calls[1][:3] == ["git", "-c", "http.version=HTTP/1.1"]
    assert any("refs/pull/7/head:refs/taskplane/pr/7/head" in argument
               for argument in calls[1])
    return result


def _runtime(label: str) -> dict:
    return {
        "state_loader": lambda workspace: {
            "label": label, "workspace": workspace},
        "wait_policy_factory": lambda _phase, count: {
            "schema": "taskplane.wait-policy/v1",
            "mode": "event",
            "scheduled_polling": False,
            "timeout_seconds": 1800,
            "reissue_after": ["completion", "attention"],
            "outstanding_count": count,
            "label": label,
        },
        "wait_invocation_factory": lambda _policy, members: {
            "schema": "taskplane.event-wait-invocation/v1",
            "operation": "wait_for_events",
            "scheduled": False,
            "reissue": False,
            "outstanding_members": list(members),
            "label": label,
        },
    }


def _runtime_observation(label: str) -> tuple[str, str]:
    state = build_c._integration_state(f"workspace-{label}")
    _policy, invocation = build_c._assignment_wait(
        [f"member-{label}"],
        wait_policy_factory=None,
        wait_invocation_factory=None,
    )
    return state["label"], invocation["label"]


def _runtime_restoration() -> None:
    with build_c.scoped_loop_runtime(**_runtime("outer")):
        assert _runtime_observation("outer") == ("outer", "outer")
        with build_c.scoped_loop_runtime(**_runtime("inner")):
            assert _runtime_observation("inner") == ("inner", "inner")
        assert _runtime_observation("outer") == ("outer", "outer")

        barrier = threading.Barrier(3)

        def observe(label: str):
            with build_c.scoped_loop_runtime(**_runtime(label)):
                barrier.wait(timeout=5)
                value = _runtime_observation(label)
                barrier.wait(timeout=5)
                return value

        with ThreadPoolExecutor(max_workers=2) as pool:
            left = pool.submit(observe, "left")
            right = pool.submit(observe, "right")
            barrier.wait(timeout=5)
            assert _runtime_observation("outer") == ("outer", "outer")
            barrier.wait(timeout=5)
        assert left.result() == ("left", "left")
        assert right.result() == ("right", "right")


def test_ac6_engineering_foundations_close(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inspector = TrustedGitInspector()
    before = inspector.snapshot(ROOT, evidence_paths=EXACT_CANDIDATE_INPUTS)
    blobs = _candidate_blobs(before)
    assert re.fullmatch(r"[0-9a-f]{40,64}", before.head_sha)
    assert _ancestry_errors(before.head_sha) == []

    architecture = depgraph.architecture_map_proof(str(ROOT))
    assert architecture["complete"] is True
    assert architecture["errors"] == []
    custody = architecture["terminal_capability_custody"]
    assert custody["complete"] is True
    assert custody["selected"] == "durably-protected-issuer"

    dispatch_source = blobs["taskplane/dispatch_telemetry.py"].decode()
    assert "type: ignore" not in dispatch_source
    assert issubclass(dispatch_telemetry.DispatchTelemetryError,
                      DeliveryPolicyError)
    assert lens._deep_cap() == lens_signals.DEEP_CAP

    runtime_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "taskplane").glob("*.py")))
    assert _ci_errors(
        blobs[".github/workflows/ci.yml"].decode(),
        blobs["requirements-dev.lock"].decode(),
        blobs["CONTRIBUTING.md"].decode(),
        blobs["scripts/render_readme_gif.py"].decode(),
        runtime_source,
    ) == []
    assert _docs_errors(blobs["docs/loop-design.md"].decode()) == []

    audit = design_proof._canonical_ci_audit()
    assert hashlib.sha256(audit).hexdigest() == \
        design_proof.CANONICAL_CI_AUDIT_SHA256
    sweep = design_proof._validate_log(
        audit,
        source_thread=design_proof.CANONICAL_THREAD,
        design_turn=design_proof.CANONICAL_TURN,
        expected_audit_sha=design_proof.CANONICAL_CI_AUDIT_SHA256,
    )
    assert sweep["status"] == "complete"
    assert sweep["result_count"] == 26
    assert sweep["concurrent_batch_ids"] == ["native-overlap-batch-00"]
    for stage in ("evaluate", "em"):
        workspace = tmp_path / stage
        workspace.mkdir()
        receipt, _common, event = proof_paths._record_consume_validate(
            workspace, stage)
        identity = json.loads(receipt["host_session_or_turn"])
        assert receipt["stage"] == stage and receipt["host"] == "codex"
        assert identity["agent_id"] == event["agent_id"]

    assert _repository_retry(monkeypatch, tmp_path)["value"] == "ready"
    _runtime_restoration()
    assert inspector.assert_unchanged(before) is before


def test_ac6_mutated_authority_docs_and_ci_fail_closed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = copy.deepcopy(depgraph._read_design_architecture(str(ROOT)))
    parsed["nodes"] = []
    monkeypatch.setattr(depgraph, "_read_design_architecture", lambda _ws: parsed)
    assert depgraph.architecture_map_proof(str(ROOT))["complete"] is False

    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    lock = (ROOT / "requirements-dev.lock").read_text(encoding="utf-8")
    docs = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    renderer = (ROOT / "scripts/render_readme_gif.py").read_text(
        encoding="utf-8")
    runtime = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "taskplane").glob("*.py")))
    weakened = ci.replace("persist-credentials: false",
                          "persist-credentials: true", 1)
    assert any("credentials" in error for error in _ci_errors(
        weakened, lock, docs, renderer, runtime))

    loop_design = (ROOT / "docs/loop-design.md").read_text(encoding="utf-8")
    weakened_docs = loop_design.replace(
        "tp.py loop submit [pass|fail|unavailable]",
        "tp.py loop submit [pass|fail]",
        1,
    )
    assert any("unavailable" in error
               for error in _docs_errors(weakened_docs))

    custody_root = tmp_path / "custody"
    (custody_root / "taskplane").mkdir(parents=True)
    components = (ROOT / "components.yaml").read_text(encoding="utf-8")
    (custody_root / "components.yaml").write_text(
        components.replace(
            "  - selected: durably-protected-issuer",
            "  - selected: process-only-custody",
            1,
        ),
        encoding="utf-8",
    )
    (custody_root / "taskplane/terminal_truth.py").write_bytes(
        (ROOT / "taskplane/terminal_truth.py").read_bytes())
    assert depgraph.terminal_capability_custody_proof(
        str(custody_root))["complete"] is False


def test_ac6_runtime_and_producer_mutations_fail_closed(
        tmp_path: Path) -> None:
    workspace = tmp_path / "producer"
    workspace.mkdir()
    receipt, common, _event = proof_paths._record_consume_validate(
        workspace, "em")
    with pytest.raises(producer_observation.ProducerObservationError,
                       match="mismatched"):
        producer_observation.validate_consumed_matching_observation(
            receipt,
            **{**common, "output_bytes": common["output_bytes"] + b" "},
        )

    outer = _runtime("outer")
    with build_c.scoped_loop_runtime(**outer):
        with pytest.raises(TypeError, match="must be callable"):
            with build_c.scoped_loop_runtime(
                state_loader=None,
                wait_policy_factory=outer["wait_policy_factory"],
                wait_invocation_factory=outer["wait_invocation_factory"],
            ):
                raise AssertionError("unreachable")
        assert _runtime_observation("outer") == ("outer", "outer")


def test_ac6_missing_leaf_ancestry_is_refused() -> None:
    candidate = subprocess.check_output(
        ["/usr/bin/git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).strip()
    leaves = dict(LEAF_COMMITS)
    leaves["M1-A"] = "0" * 40
    errors = _ancestry_errors(candidate, leaves)
    assert errors == [f"M1-A leaf {'0' * 40} is not an ancestor"]


def test_ac6_deep_cap_import_mutation_fails_closed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def unavailable(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "lens_signals":
            raise ImportError("injected integration cap failure")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    assert lens._deep_cap() is None
    selected = [{"id": "quality", "mode": "subagent", "reasons": []}]
    assert lens._cap_deep_dispatch(selected, None)[0]["mode"] == "inline"
