"""The release-tag gate: run it on this repo, and prove it CATCHES drift.

Five releases (v2.5.0, v2.5.1, v2.6.0, v2.8.1, v2.8.2) shipped with no tag
and nobody noticed for months, because tagging lived in a human's memory of
the release routine. Writing the routine down again was never going to work
— every previous "resolved" of this class was written down and skipped. So
the routine is now a check that fails.

Running the gate on this repo (below) proves the CURRENT state is clean. It
does not prove the gate would notice if it stopped being clean — a check that
always passes is worse than no check, because it reads as evidence. So most
of this file builds throwaway git repos with a specific defect planted in
each, and asserts the gate names that defect.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import ci_local as ci_runner  # noqa: E402
import ci_release_tags as gate     # noqa: E402
import ci_evals  # noqa: E402
import release_provenance as provenance  # noqa: E402
from taskplane import release_evidence  # noqa: E402
from taskplane import (  # noqa: E402
    ci_policy, host_native, owned_cleanup, views, wave_metrics,
)


def _git(root, *args, check=True):
    p = subprocess.run(["git"] + list(args), cwd=root,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stdout}")
    return p.stdout.strip()


def _green_ci_receipt(runtime, cell, owned):
    ownership_material = {
        "schema": "taskplane.ci-owned-cell/v1",
        "candidate_fingerprint": runtime["candidate"]["fingerprint"],
        "source_sha": runtime["candidate"]["source_sha"],
        "cell_id": cell["id"],
        "containment_root": str(owned.parent),
        "relative_name": owned.name,
        "registered_before_run": True,
    }
    ownership = {
        **ownership_material,
        "fingerprint": gate._sha256_json(ownership_material),
    }
    cleanup_material = {
        "schema": ci_runner.CI_CLEANUP_SCHEMA,
        "registration_fingerprint": ownership["fingerprint"],
        "outcome": "success",
        "resources": [str(owned)],
        "status": "clean",
        "leak_count": 0,
        "leaks": [],
    }
    cleanup = {
        **cleanup_material,
        "fingerprint": gate._sha256_json(cleanup_material),
    }
    observed = {
        "implementation": "CPython", "python": "3.12.9",
        "os": "posix", "platform": "Linux", "machine": "x86_64",
    }
    commands = [{
        "argv": argv, "returncode": 0, "duration_ms": 0,
        "output_digest": "d" * 64,
    } for argv in ci_runner._ci_cell_commands(cell, owned)]
    payload = {
        "schema": ci_runner.CI_CELL_SCHEMA,
        "id": cell["id"], "kind": cell["kind"], "status": "green",
        "outcome": "success", "classification": None,
        "candidate_fingerprint": runtime["candidate"]["fingerprint"],
        "source_sha": runtime["candidate"]["source_sha"],
        "plan_fingerprint": runtime["plan"]["fingerprint"],
        "settings_receipt_fingerprint": runtime["settings_receipt"]["fingerprint"],
        "environment": {
            "candidate_fingerprint": runtime["candidate"]["fingerprints"]["environment"],
            "observed": observed,
            "observed_fingerprint": gate._sha256_json(observed),
        },
        "browser_fingerprint": (
            runtime["candidate"]["browser_fingerprint"]
            if cell["kind"] == "browser" else None
        ),
        "browser_observation": (
            runtime["candidate"]["browser"]
            if cell["kind"] == "browser" else None
        ),
        "selectors": cell["selectors"], "duration_ms": 0,
        "commands": commands, "output_digest": "e" * 64,
        "ownership": ownership, "cleanup": cleanup,
    }
    return {**payload, "receipt": gate._sha256_json(payload)}


def _pushed_sha_receipts(sha):
    return [
        {"name": name, "sha": sha, "conclusion": "success"}
        for name in ci_evals.PUSHED_GREEN_REQUIRED_CHECKS
    ]


def _pushed_sha_repository(tmp_path):
    remote = tmp_path / "remote.git"
    repository = tmp_path / "repository"
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
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


def _run_pushed_sha_proof(repository, checked_sha, receipts):
    receipt_path = repository.parent / "required-checks.json"
    receipt_path.write_text(
        json.dumps(_pushed_sha_receipts(receipts), sort_keys=True),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            sys.executable,
            str(Path(ROOT) / "scripts" / "ci_evals.py"),
            "--prove-pushed-sha",
            "--checked-sha", checked_sha,
            "--check-receipts", str(receipt_path),
            "--root", str(repository),
            "--json",
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
    )


def test_pushed_sha_proof_fetches_before_classifying_stale_tracking_ref(
        tmp_path):
    repository, remote, stale_sha = _pushed_sha_repository(tmp_path)
    publisher = tmp_path / "publisher"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(publisher)],
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    _git(publisher, "config", "user.name", "taskplane-test")
    _git(publisher, "config", "user.email", "taskplane@example.invalid")
    _git(publisher, "checkout", "-q", "-B", "main", "origin/main")
    (publisher / "tracked.txt").write_text("remote advanced\n", encoding="utf-8")
    _git(publisher, "commit", "-qam", "remote advanced")
    _git(publisher, "push", "-q", "origin", "main")
    remote_sha = _git(publisher, "rev-parse", "HEAD")
    assert _git(repository, "rev-parse", "refs/remotes/origin/main") == stale_sha

    result = _run_pushed_sha_proof(repository, stale_sha, stale_sha)

    assert result.returncode == 1
    proof = json.loads(result.stdout)
    assert proof["status"] == "local_green"
    assert proof["fetch_receipt"]["ok"] is True
    assert proof["remote_sha"] == remote_sha
    assert proof["behind_count"] == 1


def test_pushed_sha_proof_refuses_cached_ref_when_fetch_fails(tmp_path):
    repository, remote, sha = _pushed_sha_repository(tmp_path)
    unavailable = tmp_path / "remote-unavailable.git"
    remote.rename(unavailable)
    assert _git(repository, "rev-parse", "refs/remotes/origin/main") == sha

    result = _run_pushed_sha_proof(repository, sha, sha)

    assert result.returncode == 1
    proof = json.loads(result.stdout)
    assert proof["status"] == "refused"
    assert proof["fetch_receipt"]["ok"] is False
    assert proof["remote_sha"] is None
    assert any("fetch failed" in row for row in proof["errors"])


class _Repo:
    """A tiny repo whose only content is a plugin manifest and a CHANGELOG."""

    def __init__(self, path):
        self.path = path
        _git(path, "init", "-q", "-b", "main")
        _git(path, "config", "user.email", "t@example.com")
        _git(path, "config", "user.name", "t")
        os.makedirs(os.path.join(path, ".codex-plugin"))
        self.versions = []

    def release(self, version):
        with open(os.path.join(self.path, ".codex-plugin", "plugin.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"name": "taskplane", "version": version}, f)
        self.versions.append(version)
        self._changelog()
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-q", "-m", f"v{version}")
        return _git(self.path, "rev-parse", "HEAD")

    def _changelog(self, extra=()):
        rows = ["| Version | Highlights |", "| --- | --- |"]
        for v in reversed(list(self.versions) + list(extra)):
            rows.append(f"| **v{v}** | notes |")
        with open(os.path.join(self.path, "CHANGELOG.md"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")

    def changelog_claims(self, version):
        """Add a CHANGELOG row for a version that was never shipped."""
        self._changelog(extra=[version])
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-q", "-m", "changelog")

    def tag(self, version, commit="HEAD"):
        _git(self.path, "tag", "-a", f"v{version}", commit, "-m", f"v{version}")

    def audit(self):
        return gate.audit(self.path)

    def checks(self):
        return sorted(p["check"] for p in self.audit()["problems"])


class _RepoCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.repo = _Repo(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def _protected_main_evidence(root, source_sha, first_parent_sha, pull_head_sha):
    fixture = Path(ROOT) / "taskplane/tests/fixtures/release/protected-main-evidence.json"
    evidence = json.loads(fixture.read_text(encoding="utf-8"))
    for row in (
        evidence["merge_topology"], evidence["ci"],
        evidence["receipts"]["candidate"],
        evidence["receipts"]["dashboard"],
        evidence["receipts"]["wave_metrics"],
        evidence["receipts"]["cleanup"],
        *evidence["receipts"]["checks"].values(),
    ):
        for key in ("source_sha", "candidate_sha", "merge_created_sha",
                    "checked_sha"):
            if key in row:
                row[key] = source_sha
    evidence["source_sha"] = source_sha
    evidence["merge_topology"]["first_parent_sha"] = first_parent_sha
    evidence["merge_topology"]["pull_request_head_sha"] = pull_head_sha
    for package in evidence["packages"]:
        package["source_sha"] = source_sha
    inputs = release_evidence.release_input_digests(root)
    evidence["supply_chain"]["workflow_digest"] = inputs["workflow_digest"]
    evidence["supply_chain"]["lock_digest"] = inputs["lock_digest"]
    evidence["receipts"]["settings"]["digest"] = inputs["settings_digest"]
    return evidence


def test_tag_requires_exact_protected_main_green(tmp_path):
    """No branch/pre-merge green result can authorize a release tag."""
    repo = _Repo(str(tmp_path))
    (tmp_path / ".github/workflows").mkdir(parents=True)
    shutil.copy(Path(ROOT) / ".github/workflows/ci.yml",
                tmp_path / ".github/workflows/ci.yml")
    shutil.copy(Path(ROOT) / "requirements-dev.lock",
                tmp_path / "requirements-dev.lock")
    (tmp_path / "taskplane").mkdir()
    shutil.copy(Path(ROOT) / "taskplane/operational-settings.json",
                tmp_path / "taskplane/operational-settings.json")
    base = repo.release("1.0.0")
    repo.tag("1.0.0", base)
    _git(str(tmp_path), "checkout", "-q", "-b", "feature")
    pull_head = repo.release("1.1.0")
    _git(str(tmp_path), "checkout", "-q", "main")
    _git(str(tmp_path), "merge", "-q", "--no-ff", "feature",
         "-m", "merge release candidate")
    protected_head = _git(str(tmp_path), "rev-parse", "HEAD")

    evidence = _protected_main_evidence(
        tmp_path, protected_head, base, pull_head)
    receipt = release_evidence.create_protected_main_release_gate(
        evidence, repository=tmp_path)
    authorization = gate.authorize_tag(tmp_path, "1.1.0", receipt)
    assert authorization["authorized"] is True
    assert authorization["source_sha"] == protected_head

    branch_evidence = deepcopy(evidence)
    branch_evidence["source_sha"] = pull_head
    branch_evidence["merge_topology"]["merge_created_sha"] = pull_head
    branch_evidence["merge_topology"]["checked_sha"] = pull_head
    branch_evidence["merge_topology"]["pull_request_head_sha"] = "c" * 40
    branch_evidence["ci"]["candidate_sha"] = pull_head
    for name, row in branch_evidence["receipts"].items():
        if name == "checks":
            for check in row.values():
                check["source_sha"] = pull_head
        elif name != "settings":
            row["source_sha"] = pull_head
    for package in branch_evidence["packages"]:
        package["source_sha"] = pull_head
    branch_receipt = release_evidence.create_protected_main_release_gate(
        branch_evidence, repository=tmp_path)
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="protected branch head"):
        gate.authorize_tag(tmp_path, "1.1.0", branch_receipt)

    red = deepcopy(evidence)
    red["ci"]["conclusions"]["pytest-1"] = "failure"
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="required check"):
        release_evidence.create_protected_main_release_gate(
            red, repository=tmp_path)


def test_unsafe_workflow_bytes_cannot_be_laundered_by_digest_and_booleans(
        tmp_path):
    repo = _Repo(str(tmp_path))
    (tmp_path / ".github/workflows").mkdir(parents=True)
    shutil.copy(Path(ROOT) / ".github/workflows/ci.yml",
                tmp_path / ".github/workflows/ci.yml")
    shutil.copy(Path(ROOT) / "requirements-dev.lock",
                tmp_path / "requirements-dev.lock")
    (tmp_path / "taskplane").mkdir()
    shutil.copy(Path(ROOT) / "taskplane/operational-settings.json",
                tmp_path / "taskplane/operational-settings.json")
    base = repo.release("1.0.0")
    repo.tag("1.0.0", base)
    _git(str(tmp_path), "checkout", "-q", "-b", "feature")
    pull_head = repo.release("1.1.0")
    _git(str(tmp_path), "checkout", "-q", "main")
    _git(str(tmp_path), "merge", "-q", "--no-ff", "feature", "-m", "merge")
    protected = _git(str(tmp_path), "rev-parse", "HEAD")
    evidence = _protected_main_evidence(tmp_path, protected, base, pull_head)

    workflow = tmp_path / ".github/workflows/ci.yml"
    unsafe = workflow.read_text(encoding="utf-8")
    unsafe = unsafe.replace("permissions:\n  contents: read", "permissions: write-all")
    unsafe = unsafe.replace(
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/checkout@main",
        1,
    )
    unsafe += "\nenv:\n  RELEASE_TOKEN: ${{ secrets.RELEASE_TOKEN }}\n"
    workflow.write_text(unsafe, encoding="utf-8")
    # A malicious caller recomputes the digest and repeats all of the old
    # trusted booleans. The release boundary must inspect the bytes itself.
    evidence["supply_chain"]["workflow_digest"] = hashlib.sha256(
        workflow.read_bytes()).hexdigest()
    evidence["supply_chain"].update({
        "permissions": "contents:read",
        "immutable_actions": True,
        "hash_locked_dependencies": True,
        "credential_empty_untrusted_jobs": True,
    })
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="workflow supply-chain"):
        release_evidence.create_protected_main_release_gate(
            evidence, repository=tmp_path)


def test_workflow_supply_chain_rejects_job_level_write_permissions():
    workflow = (Path(ROOT) / ".github/workflows/ci.yml").read_text(
        encoding="utf-8")
    unsafe = workflow.replace(
        "    runs-on: ubuntu-latest",
        "    runs-on: ubuntu-latest\n    permissions:\n      contents: write",
        1,
    )
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="job permissions"):
        release_evidence._workflow_supply_chain(unsafe.encode("utf-8"))


@pytest.mark.parametrize("unsafe_row", [
    "package @ https://example.invalid/package.whl --hash=sha256:" + "1" * 64,
    "git+https://example.invalid/package.git#egg=package --hash=sha256:" +
    "1" * 64,
    "../package.whl --hash=sha256:" + "1" * 64,
    "unhashed==1.0",
    "-r other.lock",
    "--requirement other.lock",
    "-c constraints.lock",
])
def test_lock_validator_rejects_every_unsafe_executable_row(unsafe_row):
    good = "safe==1.0 --hash=sha256:" + "0" * 64
    assert release_evidence._lock_is_hash_pinned(
        f"{good}\n{unsafe_row}\n".encode("utf-8")) is False


def test_post_merge_release_cli_assembles_and_authorizes_from_sealed_receipts(
        tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    repo = _Repo(str(repository))
    (repository / ".github/workflows").mkdir(parents=True)
    shutil.copy(Path(ROOT) / ".github/workflows/ci.yml",
                repository / ".github/workflows/ci.yml")
    shutil.copy(Path(ROOT) / "requirements-dev.lock",
                repository / "requirements-dev.lock")
    (repository / "taskplane").mkdir()
    shutil.copy(Path(ROOT) / "taskplane/operational-settings.json",
                repository / "taskplane/operational-settings.json")
    base = repo.release("1.0.0")
    repo.tag("1.0.0", base)
    _git(str(repository), "checkout", "-q", "-b", "feature")
    pull_head = repo.release("1.1.0")
    _git(str(repository), "checkout", "-q", "main")
    _git(str(repository), "merge", "-q", "--no-ff", "feature", "-m", "merge")
    source = _git(str(repository), "rev-parse", "HEAD")

    candidate_input = json.loads((
        Path(ROOT) / "taskplane/tests/fixtures/ci-policy/candidate.json"
    ).read_text(encoding="utf-8"))
    settings = ci_runner._ci_settings(
        Path(ROOT) / "taskplane/operational-settings.json",
    )
    plan_input = ci_runner._ci_declaration(
        settings, event="push", ref="refs/heads/main", run_id="release",
    )
    candidate_input["source_sha"] = source
    candidate_input["fingerprints"]["settings"] = settings.digest
    candidate_input["fingerprints"]["shard-plan"] = gate._sha256_json(plan_input)
    candidate = ci_policy.freeze_candidate(candidate_input)
    plan = ci_policy.build_ci_plan(candidate, plan_input)
    settings_receipt = {
        "schema": "taskplane.authoritative-ci-settings-receipt/v1",
        "source": "canonical-loader",
        "precedence": ["defaults", "file", "overlay"],
        "candidate_sha": source,
        "settings_digest": plan["settings_digest"],
        "effective": {},
        "loader_receipt": {},
    }
    settings_receipt["fingerprint"] = gate._sha256_json(settings_receipt)
    runtime_payload = {
        "schema": "taskplane.authoritative-ci-runtime/v1",
        "candidate": candidate,
        "settings_receipt": settings_receipt,
        "plan": plan,
    }
    runtime = {**runtime_payload,
               "fingerprint": gate._sha256_json(runtime_payload)}
    cell_receipts = {
        cell["id"]: _green_ci_receipt(
            runtime, cell, artifacts / f"{cell['id']}-owned",
        )
        for cell in plan["cells"]
    }

    dashboard_snapshot = host_native.HostSurfaceSnapshot.create(
        workflow_id="taskplane-loop", run_id="release-run",
        target="signoff", revision=source, sequence=1, stage="signoff",
        state="complete", values={
            "candidate_sha": source,
            "generated_at": "2026-08-31T01:00:00Z",
        }, evidence=("release-gate",), safe_actions=())
    dashboard_delivery = views.deliver_dashboard(
        str(artifacts / "dashboard"), dashboard_snapshot.to_dict(),
        html_renderer=lambda _canonical: "<main>release dashboard</main>")
    dashboard = dashboard_delivery["publication_receipt"]
    dashboard_current = dashboard_delivery["current_head"]
    metrics_input = json.loads((
        Path(ROOT) / "taskplane/tests/fixtures/wave-metrics/closed-run.json"
    ).read_text(encoding="utf-8"))
    metrics_input["run"]["candidate_fingerprint"] = candidate["fingerprint"]
    for row in metrics_input["sources"].values():
        row["candidate_fingerprint"] = candidate["fingerprint"]
    metrics = wave_metrics.seal_wave_receipt(metrics_input)

    manifest = artifacts / "cleanup.json"
    owned_cleanup.create_manifest(
        manifest, repository_id="repo", workspace_fingerprint="4" * 64,
        settings_digest="5" * 64, run_id="release", task_id="release",
        attempt=1, evidence_root=artifacts / "cleanup-evidence")
    owner = owned_cleanup.load_manifest(manifest)["owner"]
    replay = artifacts / "publication-replay.json"
    owned_cleanup.write_publication_replay(
        replay, owner=owner, outcome="success", source_revision=1,
        source_fingerprint="6" * 64, trigger="terminal")
    cleanup_receipt = owned_cleanup.seal_and_cleanup(
        manifest, outcome="success", evidence={"publication-replay": replay})

    archives = []
    for kind in ("openai", "claude"):
        archive = artifacts / f"{kind}.zip"
        archive.write_bytes(kind.encode())
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        record = json.loads(provenance.write(
            repository, archive, digest, kind=kind
        ).read_text(encoding="utf-8"))
        archives.append(record)

    release_gate = gate.assemble_protected_main_gate(
        repository, pull_request_head_sha=pull_head, runtime=runtime,
        cell_receipts=cell_receipts, dashboard=dashboard,
        dashboard_current=dashboard_current,
        wave_metrics=metrics, cleanup=cleanup_receipt,
        openai_provenance=archives[0], claude_provenance=archives[1])
    authorization = gate.authorize_tag(
        repository, "1.1.0", release_gate)
    assert release_gate["source_sha"] == source
    assert authorization["authorized"] is True

    missing_sha = deepcopy(dashboard)
    missing_sha["candidate"].pop("source_sha")
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="dashboard publication evidence"):
        gate.assemble_protected_main_gate(
            repository, pull_request_head_sha=pull_head, runtime=runtime,
            cell_receipts=cell_receipts, dashboard=missing_sha,
            dashboard_current=dashboard_current,
            wave_metrics=metrics, cleanup=cleanup_receipt,
            openai_provenance=archives[0], claude_provenance=archives[1])

    wrong_snapshot = host_native.HostSurfaceSnapshot.create(
        workflow_id="taskplane-loop", run_id="release-run",
        target="signoff", revision="f" * 40, sequence=2, stage="signoff",
        state="complete", values={
            "candidate_sha": "f" * 40,
            "generated_at": "2026-08-31T01:01:00Z",
        }, evidence=("release-gate",), safe_actions=())
    wrong_delivery = views.deliver_dashboard(
        str(artifacts / "wrong-dashboard"), wrong_snapshot.to_dict(),
        html_renderer=lambda _canonical: "<main>wrong dashboard</main>")
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="dashboard publication evidence"):
        gate.assemble_protected_main_gate(
            repository, pull_request_head_sha=pull_head, runtime=runtime,
            cell_receipts=cell_receipts,
            dashboard=wrong_delivery["publication_receipt"],
            dashboard_current=wrong_delivery["current_head"],
            wave_metrics=metrics, cleanup=cleanup_receipt,
            openai_provenance=archives[0], claude_provenance=archives[1])


class TestThisRepoIsClean(unittest.TestCase):
    def test_every_shipped_version_here_is_tagged(self):
        res = gate.audit()
        if res.get("unavailable"):
            self.skipTest(f"release history unavailable: {res['unavailable']}")
        self.assertEqual(
            res["problems"], [],
            "release-tag drift: " + "; ".join(
                f"{p['check']} v{p['version']}: {p['detail']}"
                for p in res["problems"]))

    def test_this_repo_passes_the_gate_when_the_gate_can_see(self):
        """Runs the real script against this checkout — but only where the
        clone HAS the history and tags to check.

        The first version of this asserted exit 0 unconditionally. The main
        test job checks out shallow and tagless (only the dedicated
        release-tags job sets fetch-depth 0 and fetch-tags), so the gate
        correctly reported CANNOT VERIFY, exited 1, and the test failed on
        the gate being RIGHT. An environment-dependent assertion in a test
        is the same defect class the gate exists to catch: a claim nobody
        checked against the thing that settles it."""
        if gate.audit().get("unavailable"):
            self.skipTest("shallow/tagless clone — see the release-tags job")
        p = subprocess.run([sys.executable, "scripts/ci_release_tags.py"],
                           cwd=ROOT, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stdout)


class TestTheExitCodes(_RepoCase):
    """A gate that reports problems on stdout and exits 0 is not a gate.
    Proven on synthetic repos, so these assertions hold in EVERY CI job
    regardless of what that job fetched."""

    def _run(self, root):
        p = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts",
                                          "ci_release_tags.py"), root],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8")
        return p.returncode, p.stdout

    def test_the_source_returns_one_when_not_ok(self):
        src = open(os.path.join(ROOT, "scripts", "ci_release_tags.py"),
                   encoding="utf-8").read()
        self.assertIn("return 0 if res[\"ok\"] else 1", src)

    def test_a_clean_repo_exits_zero(self):
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        rc, out = self._run(self.dir)
        self.assertEqual(rc, 0, out)
        self.assertIn("ok:", out)

    def test_a_repo_with_drift_exits_one(self):
        self.repo.release("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        self.repo.release("1.2.0")
        self.repo.tag("1.2.0")
        rc, out = self._run(self.dir)
        self.assertEqual(rc, 1, out)
        self.assertIn("C1 v1.0.0", out)
        self.assertIn("FAIL", out)

    def test_a_tagless_clone_exits_one_and_says_it_cannot_verify(self):
        """The exact shape that made this file red in CI. It must FAIL
        closed and name the checkout setting that fixes it."""
        self.repo.release("1.0.0")
        rc, out = self._run(self.dir)
        self.assertEqual(rc, 1, out)
        self.assertIn("CANNOT VERIFY", out)
        self.assertIn("fetch-tags", out)

    def test_a_repo_with_no_mainline_exits_one(self):
        d = tempfile.mkdtemp()
        try:
            _git(d, "init", "-q", "-b", "topic")
            rc, out = self._run(d)
            self.assertEqual(rc, 1, out)
            self.assertIn("CANNOT VERIFY", out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_json_mode_carries_the_same_exit_code(self):
        self.repo.release("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        self.repo.release("1.2.0")
        self.repo.tag("1.2.0")
        p = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts",
                                          "ci_release_tags.py"),
             self.dir, "--json"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 1)
        self.assertFalse(json.loads(p.stdout)["ok"])


class TestItCatchesAMissingTag(_RepoCase):
    def test_an_untagged_older_release_is_C1(self):
        self.repo.release("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        self.repo.release("1.2.0")
        self.repo.tag("1.2.0")
        self.assertEqual(self.repo.checks(), ["C1"])
        self.assertIn("has no v1.0.0 tag",
                      self.repo.audit()["problems"][0]["detail"])

    def test_the_newest_release_may_be_untagged(self):
        """Between merging the bump and tagging after CI, main is legitimately
        one release ahead. At most ONE — which is what stops five from piling
        up."""
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.assertEqual(self.repo.checks(), [])

    def test_two_untagged_releases_is_never_ok(self):
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.repo.release("1.2.0")
        self.assertEqual(self.repo.checks(), ["C1"])

    def test_five_untagged_releases_reports_all_five(self):
        """The exact shape that actually happened."""
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        for v in ("1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0", "1.6.0"):
            self.repo.release(v)
        self.assertEqual(self.repo.checks(), ["C1"] * 5)


class TestTheReleaseInFlightIsExempt(_RepoCase):
    """A release is prepared by bumping the manifest and writing its
    CHANGELOG row BEFORE the commit reaches the mainline. C1 already
    tolerates the newest mainline version being untagged for that reason;
    C4 has to tolerate its CHANGELOG row for the same one."""

    def test_a_changelog_row_for_the_version_on_disk_is_fine(self):
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        # bump the working tree only: no commit, so the mainline has not
        # seen 1.2.0 yet, and the CHANGELOG already names it.
        with open(os.path.join(self.dir, ".codex-plugin", "plugin.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"name": "taskplane", "version": "1.2.0"}, f)
        self.repo.changelog_claims("1.2.0")
        self.assertEqual(self.repo.checks(), [])

    def test_a_stack_of_unpushed_release_commits_is_fine(self):
        """Two releases can be prepared locally before either is pushed —
        which is exactly what happened here: v2.11.0 was committed and
        v2.12.0 was in the working tree, and a one-version exemption called
        the older one fictional."""
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        # 1.1.0 and 1.2.0 committed locally; the mainline is `main` and has
        # them, so simulate "not yet on the mainline" by pointing the gate
        # at a mainline that stops earlier.
        self.repo.release("1.1.0")
        self.repo.release("1.2.0")
        with open(os.path.join(self.dir, ".codex-plugin", "plugin.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"name": "taskplane", "version": "1.3.0"}, f)
        self.repo.changelog_claims("1.3.0")
        checks = self.repo.checks()
        self.assertNotIn("C4", checks, f"unexpected: {self.repo.audit()['problems']}")

    def test_it_exempts_exactly_one_version_not_any_unshipped_row(self):
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        with open(os.path.join(self.dir, ".codex-plugin", "plugin.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"name": "taskplane", "version": "1.2.0"}, f)
        self.repo.changelog_claims("9.9.9")
        self.assertIn("C4", self.repo.checks())


class TestItCatchesAMisplacedTag(_RepoCase):
    def test_a_tag_on_the_wrong_commit_is_C3(self):
        self.repo.release("1.0.0")
        c11 = self.repo.release("1.1.0")
        c12 = self.repo.release("1.2.0")
        self.repo.tag("1.0.0", c11)      # points at the 1.1.0 tree
        self.repo.tag("1.1.0", c11)
        self.repo.tag("1.2.0", c12)
        self.assertEqual(self.repo.checks(), ["C3"])
        self.assertIn("declares '1.1.0', not '1.0.0'",
                      self.repo.audit()["problems"][0]["detail"])

    def test_a_tag_off_the_mainline_is_C2(self):
        """A tag on an abandoned branch looks fine in `git tag -l` and points
        at a tree nobody can check out from main."""
        c10 = self.repo.release("1.0.0")
        self.repo.tag("1.0.0", c10)
        _git(self.dir, "checkout", "-q", "-b", "side")
        with open(os.path.join(self.dir, "SIDE"), "w") as f:
            f.write("abandoned\n")      # or git dedupes it into one commit
        side = self.repo.release("1.1.0")
        self.repo.tag("1.1.0", side)
        _git(self.dir, "checkout", "-q", "-f", "main")
        self.repo.versions = ["1.0.0"]
        self.repo.release("1.1.0")       # 1.1.0 also shipped ON main
        self.repo.release("1.2.0")
        self.repo.tag("1.2.0")
        self.assertIn("C2", self.repo.checks())

    def test_an_annotated_tag_is_not_mistaken_for_a_dangling_commit(self):
        """The 'dangling v2.8.0' that cost an hour was an annotated tag OBJECT
        sha read as a commit sha. The gate must dereference."""
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        tags = gate.release_tags(self.dir)
        obj = _git(self.dir, "rev-parse", "v1.0.0")
        commit = _git(self.dir, "rev-parse", "v1.0.0^{}")
        self.assertNotEqual(obj, commit, "expected an annotated tag")
        self.assertEqual(tags["v1.0.0"], commit)

    def test_a_tagged_release_on_a_merged_side_parent_is_real(self):
        """v2.17.17's exact shape: its tagged manifest commit is reachable
        through a no-ff merge, but the merge's first-parent tree had already
        advanced. It is a release, not a fictional CHANGELOG row or tag."""
        c10 = self.repo.release("1.0.0")
        self.repo.tag("1.0.0", c10)
        _git(self.dir, "checkout", "-q", "-b", "release-side")
        side = self.repo.release("1.0.5")
        self.repo.tag("1.0.5", side)

        _git(self.dir, "checkout", "-q", "main")
        self.repo.versions = ["1.0.0"]
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        _git(self.dir, "merge", "-q", "--no-ff", "-s", "ours",
             "release-side", "-m", "merge released side parent")
        self.repo.versions = ["1.0.0", "1.1.0"]
        self.repo.changelog_claims("1.0.5")

        result = self.repo.audit()
        self.assertEqual(result["problems"], [])
        self.assertIn("1.0.5", result["shipped"])
        self.assertEqual(result["tagged_side_releases"], ["1.0.5"])


class TestItCatchesAFictionalRelease(_RepoCase):
    def test_a_changelog_row_no_tree_ever_declared_is_C4(self):
        """v2.4.0's row was ADDED by the commit that bumped to 2.5.0."""
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        self.repo.changelog_claims("1.0.5")
        self.assertEqual(self.repo.checks(), ["C4"])
        self.assertIn("no tree ever declared",
                      self.repo.audit()["problems"][0]["detail"])

    def test_a_tag_for_a_version_that_never_shipped_is_C6(self):
        self.repo.release("1.0.0")
        self.repo.tag("1.0.0")
        self.repo.release("1.1.0")
        self.repo.tag("1.1.0")
        self.repo.tag("9.9.9")
        self.assertIn("C6", self.repo.checks())


class TestTheExemptionListCannotRot(unittest.TestCase):
    """NOT_SHIPPED is the one soft spot: a list of versions the gate agrees
    not to demand tags for. C5 re-derives whether each entry is still true, so
    it cannot quietly become a way to un-tag a real release."""

    def test_every_exemption_carries_a_reason_and_a_destination(self):
        self.assertTrue(gate.NOT_SHIPPED)
        for v, info in gate.NOT_SHIPPED.items():
            with self.subTest(v):
                self.assertGreater(len(info.get("reason", "")), 60,
                                   "an exemption needs a real explanation")
                self.assertIn("co_released_with", info)

    def test_an_exemption_for_a_version_that_did_ship_is_C5(self):
        d = tempfile.mkdtemp()
        try:
            repo = _Repo(d)
            repo.release("1.0.0")
            repo.tag("1.0.0")
            repo.release("1.1.0")
            repo.tag("1.1.0")
            orig = gate.NOT_SHIPPED
            gate.NOT_SHIPPED = dict(orig)
            gate.NOT_SHIPPED["1.0.0"] = {"reason": "x" * 70,
                                         "co_released_with": "1.1.0"}
            try:
                checks = sorted(p["check"] for p in gate.audit(d)["problems"])
            finally:
                gate.NOT_SHIPPED = orig
            self.assertIn("C5", checks)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_every_exemption_is_still_not_shipped(self):
        res = gate.audit()
        if res.get("unavailable"):
            self.skipTest(res["unavailable"])
        for v in gate.NOT_SHIPPED:
            self.assertNotIn(v, res["shipped"],
                             f"v{v} is exempted but IS in the manifest "
                             f"history — tag it and drop the exemption")


class TestDeclaredButNotReleasedCannotRot(unittest.TestCase):
    """Superseded candidate trees are explicit and may never hide a release."""

    def test_every_nonrelease_carries_a_reason_and_successor(self):
        for version, info in gate.NOT_RELEASED.items():
            with self.subTest(version):
                self.assertGreater(len(info.get("reason", "")), 60)
                self.assertRegex(
                    info.get("superseded_by", ""), r"^\d+\.\d+\.\d+$"
                )

    def test_real_nonreleases_are_declared_and_untagged(self):
        res = gate.audit()
        if res.get("unavailable"):
            self.skipTest(res["unavailable"])
        exact_tags = {
            name.lstrip("v")
            for name, sha in res["tags"].items()
            if res["shipped"].get(name.lstrip("v")) == sha
        }
        for version in gate.NOT_RELEASED:
            with self.subTest(version):
                self.assertIn(
                    version, set(res["shipped"]) | set(res["prepared"])
                )
                self.assertNotIn(version, exact_tags)

    def test_a_tagged_release_cannot_be_marked_not_released(self):
        d = tempfile.mkdtemp()
        try:
            repo = _Repo(d)
            repo.release("1.0.0")
            repo.tag("1.0.0")
            original = gate.NOT_RELEASED
            gate.NOT_RELEASED = {
                "1.0.0": {
                    "reason": "x" * 70,
                    "superseded_by": "1.0.1",
                }
            }
            try:
                self.assertIn("C7", repo.checks())
            finally:
                gate.NOT_RELEASED = original
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_declared_nonrelease_requires_its_changelog_disposition(self):
        d = tempfile.mkdtemp()
        try:
            repo = _Repo(d)
            repo.release("1.0.0")
            repo.tag("1.0.0")
            with open(
                os.path.join(d, "CHANGELOG.md"), "w", encoding="utf-8"
            ) as stream:
                stream.write("| Version | Highlights |\n| --- | --- |\n")
            original = gate.NOT_RELEASED
            gate.NOT_RELEASED = {
                "1.0.0": {
                    "reason": "x" * 70,
                    "superseded_by": "1.0.1",
                }
            }
            try:
                audit = repo.audit()
            finally:
                gate.NOT_RELEASED = original
            self.assertIn("C7", [row["check"] for row in audit["problems"]])
            self.assertIn("CHANGELOG disposition is missing", audit["problems"][0]["detail"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestItFailsClosedWhenItCannotSee(unittest.TestCase):
    """A gate that skips when the clone is shallow or tagless is a gate that
    passes on every misconfigured runner."""

    def test_a_repo_with_no_main_is_unavailable_not_ok(self):
        d = tempfile.mkdtemp()
        try:
            _git(d, "init", "-q", "-b", "topic")
            res = gate.audit(d)
            self.assertFalse(res["ok"])
            self.assertIn("unavailable", res)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_repo_with_no_tags_is_unavailable_not_ok(self):
        d = tempfile.mkdtemp()
        try:
            repo = _Repo(d)
            repo.release("1.0.0")
            res = gate.audit(d)
            self.assertFalse(res["ok"])
            self.assertIn("fetch-tags", res["unavailable"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_unavailable_exits_nonzero(self):
        d = tempfile.mkdtemp()
        try:
            _git(d, "init", "-q", "-b", "topic")
            src = open(os.path.join(ROOT, "scripts", "ci_release_tags.py"),
                       encoding="utf-8").read()
            self.assertIn("CANNOT VERIFY", src)
            self.assertIn("        return 1", src)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestCiWiring(unittest.TestCase):
    """The gate has to actually run, with enough history to be able to see."""

    def setUp(self):
        with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"),
                  encoding="utf-8") as f:
            self.ci = f.read()

    def test_ci_runs_the_gate(self):
        self.assertIn("scripts/ci_release_tags.py", self.ci)

    def test_the_leg_that_runs_it_fetches_full_history_and_tags(self):
        idx = self.ci.index("scripts/ci_release_tags.py")
        job = self.ci[:idx]
        tail = job[job.rindex("runs-on:"):]
        self.assertIn("fetch-depth: 0", tail,
                      "the release-tag gate needs full history")
        self.assertIn("fetch-tags: true", tail,
                      "the release-tag gate needs tags")


if __name__ == "__main__":
    unittest.main()
