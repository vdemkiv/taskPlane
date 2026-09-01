"""Behavioral release-boundary tests for protected-main authorization."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import ci_evals  # noqa: E402
import ci_local  # noqa: E402
import ci_release_tags as gate  # noqa: E402
from taskplane import release_evidence  # noqa: E402


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if result.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}")
    return result.stdout.strip()


def _remote_repository(tmp_path: Path) -> tuple[Path, Path, str]:
    remote = tmp_path / "remote.git"
    repository = tmp_path / "repository"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True,
        text=True, encoding="utf-8", capture_output=True,
    )
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "taskplane-test")
    _git(repository, "config", "user.email", "taskplane@example.invalid")
    (repository / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-qm", "initial")
    _git(repository, "branch", "-M", "main")
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "-q", "-u", "origin", "main")
    return repository, remote, _git(repository, "rev-parse", "HEAD")


def _run_pushed_sha_proof(repository: Path, checked_sha: str) \
        -> subprocess.CompletedProcess[str]:
    receipts = repository.parent / "required-checks.json"
    receipts.write_text(json.dumps([
        {"name": name, "sha": checked_sha, "conclusion": "success"}
        for name in ci_evals.PUSHED_GREEN_REQUIRED_CHECKS
    ]), encoding="utf-8")
    return subprocess.run([
        sys.executable, str(ROOT / "scripts/ci_evals.py"),
        "--prove-pushed-sha", "--checked-sha", checked_sha,
        "--check-receipts", str(receipts), "--root", str(repository),
        "--json",
    ], text=True, encoding="utf-8", capture_output=True)


def test_pushed_sha_proof_fetches_before_judging_cached_main(tmp_path):
    repository, remote, stale_sha = _remote_repository(tmp_path)
    publisher = tmp_path / "publisher"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(publisher)], check=True,
        text=True, encoding="utf-8", capture_output=True,
    )
    _git(publisher, "config", "user.name", "taskplane-test")
    _git(publisher, "config", "user.email", "taskplane@example.invalid")
    _git(publisher, "checkout", "-q", "-B", "main", "origin/main")
    (publisher / "tracked.txt").write_text("remote advanced\n", encoding="utf-8")
    _git(publisher, "commit", "-qam", "remote advanced")
    _git(publisher, "push", "-q", "origin", "main")
    remote_sha = _git(publisher, "rev-parse", "HEAD")

    result = _run_pushed_sha_proof(repository, stale_sha)

    assert result.returncode == 1
    proof = json.loads(result.stdout)
    assert proof["status"] == "local_green"
    assert proof["fetch_receipt"]["ok"] is True
    assert proof["remote_sha"] == remote_sha


def test_pushed_sha_proof_refuses_when_remote_cannot_be_refreshed(tmp_path):
    repository, remote, sha = _remote_repository(tmp_path)
    remote.rename(tmp_path / "remote-unavailable.git")

    result = _run_pushed_sha_proof(repository, sha)

    assert result.returncode == 1
    proof = json.loads(result.stdout)
    assert proof["status"] == "refused"
    assert proof["fetch_receipt"]["ok"] is False
    assert proof["remote_sha"] is None


class _ReleaseRepository:
    def __init__(self, root: Path):
        self.root = root
        _git(root, "init", "-q", "-b", "main")
        _git(root, "config", "user.email", "taskplane@example.invalid")
        _git(root, "config", "user.name", "taskplane-test")
        (root / ".codex-plugin").mkdir()

    def release(self, version: str) -> str:
        (self.root / ".codex-plugin/plugin.json").write_text(
            json.dumps({"name": "taskplane", "version": version}),
            encoding="utf-8",
        )
        (self.root / "CHANGELOG.md").write_text(
            f"| Version | Highlights |\n| --- | --- |\n| **v{version}** | current |\n",
            encoding="utf-8",
        )
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", f"v{version}")
        return _git(self.root, "rev-parse", "HEAD")


def _release_repository(tmp_path: Path) -> tuple[Path, str, str, str]:
    repository = tmp_path / "release-repository"
    repository.mkdir()
    repo = _ReleaseRepository(repository)
    (repository / ".github/workflows").mkdir(parents=True)
    (repository / "taskplane").mkdir()
    shutil.copy(ROOT / ".github/workflows/ci.yml",
                repository / ".github/workflows/ci.yml")
    shutil.copy(ROOT / "requirements-dev.lock", repository / "requirements-dev.lock")
    shutil.copy(ROOT / "taskplane/operational-settings.json",
                repository / "taskplane/operational-settings.json")
    base = repo.release("1.0.0")
    _git(repository, "tag", "-a", "v1.0.0", base, "-m", "v1.0.0")
    _git(repository, "checkout", "-q", "-b", "feature")
    pull_head = repo.release("1.1.0")
    _git(repository, "checkout", "-q", "main")
    _git(repository, "merge", "-q", "--no-ff", "feature", "-m", "merge release")
    return repository, base, pull_head, _git(repository, "rev-parse", "HEAD")


def _protected_evidence(repository: Path, base: str, pull_head: str,
                        source: str) -> dict[str, object]:
    fixture = ROOT / "taskplane/tests/fixtures/release/protected-main-evidence.json"
    evidence = json.loads(fixture.read_text(encoding="utf-8"))
    evidence["source_sha"] = source
    topology = evidence["merge_topology"]
    topology.update({
        "merge_created_sha": source, "checked_sha": source,
        "first_parent_sha": base, "pull_request_head_sha": pull_head,
    })
    evidence["ci"]["candidate_sha"] = source
    for name, row in evidence["receipts"].items():
        if name == "checks":
            for check in row.values():
                check["source_sha"] = source
        elif name != "settings":
            row["source_sha"] = source
    for package in evidence["packages"]:
        package["source_sha"] = source
    inputs = release_evidence.release_input_digests(repository)
    evidence["supply_chain"]["workflow_digest"] = inputs["workflow_digest"]
    evidence["supply_chain"]["lock_digest"] = inputs["lock_digest"]
    evidence["receipts"]["settings"]["digest"] = inputs["settings_digest"]
    return evidence


def test_tag_requires_exact_protected_main_green(tmp_path):
    repository, base, pull_head, source = _release_repository(tmp_path)
    evidence = _protected_evidence(repository, base, pull_head, source)

    receipt = release_evidence.create_protected_main_release_gate(
        evidence, repository=repository)
    authorization = gate.authorize_tag(repository, "1.1.0", receipt)
    assert authorization["authorized"] is True
    assert authorization["source_sha"] == source

    red = deepcopy(evidence)
    red["ci"]["conclusions"]["pytest-1"] = "failure"
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="required check"):
        release_evidence.create_protected_main_release_gate(
            red, repository=repository)


def test_release_gate_inspects_workflow_bytes_and_locked_dependencies(tmp_path):
    repository, base, pull_head, source = _release_repository(tmp_path)
    evidence = _protected_evidence(repository, base, pull_head, source)
    workflow = repository / ".github/workflows/ci.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "permissions:\n  contents: read", "permissions: write-all"
        ), encoding="utf-8",
    )
    evidence["supply_chain"]["workflow_digest"] = hashlib.sha256(
        workflow.read_bytes()).hexdigest()

    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="workflow supply-chain"):
        release_evidence.create_protected_main_release_gate(
            evidence, repository=repository)

    safe = "safe==1.0 --hash=sha256:" + "0" * 64
    unsafe = "git+https://example.invalid/pkg.git#egg=pkg --hash=sha256:" + "1" * 64
    assert release_evidence._lock_is_hash_pinned(
        f"{safe}\n{unsafe}\n".encode("utf-8")) is False


def test_current_candidate_has_an_explicit_nonrelease_predecessor():
    disposition = gate.NOT_RELEASED["2.18.4"]
    assert disposition["superseded_by"] == "2.18.5"
    assert "never promoted" in disposition["reason"]


def _runtime_with_fingerprint(
    runtime: dict, digest: str, *, name: str = "runner",
    browser: dict | None = None,
) -> dict:
    varied = deepcopy(runtime)
    candidate = varied["candidate"]
    fingerprints = dict(candidate["fingerprints"])
    fingerprints[name] = digest
    varied["candidate"] = ci_local.freeze_candidate({
        "source_sha": candidate["source_sha"],
        "fingerprints": fingerprints,
        "browser": browser or candidate["browser"],
    })
    varied["plan"]["candidate_fingerprint"] = varied["candidate"]["fingerprint"]
    for cell in varied["plan"]["cells"]:
        cell["candidate_fingerprint"] = varied["candidate"]["fingerprint"]
        if cell["kind"] == "browser":
            cell["browser_environment"] = varied["candidate"]["browser"]
            cell["browser_fingerprint"] = \
                varied["candidate"]["browser_fingerprint"]
    varied["plan"]["fingerprint"] = ci_local._sha256_json({
        key: value for key, value in varied["plan"].items()
        if key != "fingerprint"
    })
    varied["fingerprint"] = ci_local._sha256_json({
        key: value for key, value in varied.items() if key != "fingerprint"
    })
    return ci_local.validate_authoritative_ci_runtime(varied)


def test_direct_ci_release_matrix_authenticates_each_host_runtime(tmp_path):
    source_sha = _git(ROOT, "rev-parse", "HEAD")
    base = ci_local.build_authoritative_ci_runtime(
        source_sha=source_sha, event="push", ref="refs/heads/main",
        run_id="release-matrix-journey",
    )
    runtimes = {}
    receipts = {}
    observed_browser = {
        **base["candidate"]["browser"],
        "executable": "/opt/host/chrome",
        "version": "Google Chrome 130.0.0",
    }
    for cell in base["plan"]["cells"]:
        cell_id = cell["id"]
        observed = {
            "implementation": "CPython",
            "python": str(cell.get("runtime") or "3.12").split("/")[0],
            "os": "nt" if cell["kind"] == "os-portability" else "posix",
            "platform": (
                "Windows" if cell["kind"] == "os-portability" else "Linux"
            ),
            "machine": "AMD64" if cell["kind"] == "os-portability" else "x86_64",
        }
        runtime = _runtime_with_fingerprint(
            base, ci_local._sha256_json({
                key: observed[key]
                for key in ("implementation", "python", "platform")
            }), browser=observed_browser if cell["kind"] == "browser" else None)
        cell_root = tmp_path / cell_id
        cell_root.mkdir()
        runtime_path = cell_root / "runtime.json"
        runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
        receipt_path = cell_root / "receipt.json"
        result = ci_local.run_authoritative_ci_cell(
            runtime_path, cell_id, receipt_path,
            environ={**os.environ, "RUNNER_TEMP": str(cell_root / "runner")},
            forced_outcome="handoff",
        )
        assert result == 1
        runtimes[cell_id] = runtime
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["environment"]["observed"] = observed
        receipt["environment"]["observed_fingerprint"] = \
            ci_local._sha256_json(observed)
        if cell["kind"] == "browser":
            receipt["browser_observation"] = observed_browser
        receipt["receipt"] = ci_local._sha256_json({
            key: value for key, value in receipt.items() if key != "receipt"
        })
        receipts[cell_id] = receipt

    authority, checked = gate.authenticate_direct_ci_matrix(runtimes, receipts)
    assert authority["source_sha"] == source_sha
    assert {row["id"] for row in checked} == set(runtimes)
    assert len({
        runtime["candidate"]["fingerprints"]["runner"]
        for runtime in runtimes.values()
    }) == 5
    assert authority["candidate"]["browser_policy"] == {
        key: value for key, value in observed_browser.items()
        if key not in {"executable", "version"}
    }
    assert "executable" not in authority["candidate"]["browser_policy"]
    assert "version" not in authority["candidate"]["browser_policy"]

    crossed = dict(receipts)
    first, second = list(crossed)[:2]
    crossed[first], crossed[second] = crossed[second], crossed[first]
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="direct receipt is invalid"):
        gate.authenticate_direct_ci_matrix(runtimes, crossed)

    changed_browser = deepcopy(receipts["dashboard-browser"])
    changed_browser["browser_observation"]["version"] = "Google Chrome 131.0.0"
    changed_browser["receipt"] = ci_local._sha256_json({
        key: value for key, value in changed_browser.items() if key != "receipt"
    })
    changed_receipts = {**receipts, "dashboard-browser": changed_browser}
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="direct receipt is invalid"):
        gate.authenticate_direct_ci_matrix(runtimes, changed_receipts)

    divergent = dict(runtimes)
    divergent[first] = _runtime_with_fingerprint(
        runtimes[first], hashlib.sha256(
            b"different-test-inventory").hexdigest(), name="tests")
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="do not share one candidate authority"):
        gate.authenticate_direct_ci_matrix(divergent, receipts)
