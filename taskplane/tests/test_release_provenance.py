"""A release artifact must name the tree it came from (D-0010).

Both packagers were already deterministic — build twice, get identical
bytes, proven in CI — and that is a real property. It answers the wrong
question. Determinism says "this archive is reproducible FROM SOME TREE". It
never said WHICH tree, and it said nothing about whether that tree had ever
passed a test.

So a maintainer could build from a working copy with uncommitted edits, or
from a branch whose CI was red, and the archive plus its `.sha256` would look
exactly as trustworthy as one built from a verified tag. The digest is a
checksum of the archive, not evidence about its source. The user installing
the plugin had no way to ask the only question that matters: is what I am
installing the code that was tested?

A local build genuinely cannot know whether CI passed, and claiming
otherwise would be worse than saying nothing. What it can do is make the
question ANSWERABLE — record the commit — and refuse the cases where the
answer is already known to be no.

Every assertion here was observed FAILING before it was kept.
"""
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

ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import release_provenance as prov  # noqa: E402
from taskplane import release_evidence  # noqa: E402


class _Repo(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tp-prov-"))
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        for args in (["init", "-q"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"], ["add", "-A"],
                     ["commit", "-qm", "base"]):
            subprocess.run(["git", *args], cwd=str(self.root),
                           capture_output=True)
        # OUTSIDE the repo, like a real `--output-dir /tmp/...` build: an
        # archive written into a non-ignored path would itself make the tree
        # dirty, which is true of the product too (dist/ is gitignored).
        self.outdir = Path(tempfile.mkdtemp(prefix="tp-prov-out-"))
        self.archive = self.outdir / "pkg.zip"
        self.archive.write_bytes(b"archive-bytes")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.outdir, ignore_errors=True)

    def _head(self):
        return subprocess.run(["git", "rev-parse", "HEAD"],
                              cwd=str(self.root), capture_output=True,
                              text=True, encoding="utf-8",
                              errors="replace").stdout.strip()


class TestTheArtifactNamesItsCommit(_Repo):
    def test_a_clean_build_records_the_commit(self):
        path = prov.write(self.root, self.archive, "d" * 64)
        rec = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(rec["commit"], self._head())
        self.assertEqual(rec["sha256"], "d" * 64)
        self.assertEqual(rec["archive"], "pkg.zip")
        self.assertTrue(rec["verified_source"])
        self.assertEqual(rec["dirty"], [])

    def test_the_record_sits_beside_the_archive(self):
        path = prov.write(self.root, self.archive, "d" * 64)
        self.assertEqual(path.name, "pkg.zip.provenance.json")

    def test_it_does_not_claim_ci_passed(self):
        """The line this must not cross. A local build cannot know, and a
        provenance file that implied it would be worse than none — it would
        launder an unverified artifact."""
        rec = json.loads(prov.write(self.root, self.archive, "d" * 64)
                         .read_text(encoding="utf-8"))
        self.assertNotIn("ci", {k.lower() for k in rec})
        self.assertIn("does NOT assert that CI passed", rec["note"])

    def test_release_projection_binds_canonical_inputs_and_package_kind(self):
        (self.root / ".github/workflows").mkdir(parents=True)
        shutil.copy(Path(ROOT) / ".github/workflows/ci.yml",
                    self.root / ".github/workflows/ci.yml")
        shutil.copy(Path(ROOT) / "requirements-dev.lock",
                    self.root / "requirements-dev.lock")
        (self.root / "taskplane").mkdir()
        shutil.copy(Path(ROOT) / "taskplane/operational-settings.json",
                    self.root / "taskplane/operational-settings.json")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "release inputs"],
                       cwd=self.root, check=True)
        digest = "e" * 64
        record = json.loads(prov.write(
            self.root, self.archive, digest, kind="openai"
        ).read_text(encoding="utf-8"))

        projected = prov.release_gate_record(record)
        self.assertEqual(projected["kind"], "openai")
        self.assertEqual(projected["source_sha"], self._head())
        self.assertEqual(projected["archive_digest"], digest)
        self.assertEqual(projected["provenance_digest"], record["fingerprint"])
        self.assertEqual(
            set(record["release_inputs"]),
            {"settings_digest", "workflow_digest", "lock_digest"},
        )


class TestADirtyTreeIsRefused(_Repo):
    def _dirty(self):
        (self.root / "a.py").write_text("x = 2\n", encoding="utf-8")

    def test_packaging_a_dirty_tree_raises(self):
        self._dirty()
        with self.assertRaises(prov.ProvenanceError) as cm:
            prov.write(self.root, self.archive, "d" * 64)
        self.assertIn("DIRTY", str(cm.exception))
        self.assertIn("a.py", str(cm.exception))

    def test_the_refusal_explains_what_it_protects(self):
        self._dirty()
        with self.assertRaises(prov.ProvenanceError) as cm:
            prov.write(self.root, self.archive, "d" * 64)
        self.assertIn("--allow-dirty", str(cm.exception))
        self.assertIn("check it against CI", str(cm.exception))

    def test_the_override_stamps_the_artifact_permanently(self):
        """A local test archive stays possible. It stops being able to
        pretend it is a release."""
        self._dirty()
        rec = json.loads(
            prov.write(self.root, self.archive, "d" * 64, allow_dirty=True)
            .read_text(encoding="utf-8"))
        self.assertFalse(rec["verified_source"])
        self.assertEqual(rec["dirty"], ["a.py"])

    def test_an_untracked_file_counts_as_dirty(self):
        """It ships inside the archive, so it is part of what the commit
        claim would be covering."""
        (self.root / "extra.py").write_text("y = 1\n", encoding="utf-8")
        with self.assertRaises(prov.ProvenanceError):
            prov.write(self.root, self.archive, "d" * 64)

    def test_a_worktree_modification_path_is_not_mangled(self):
        """`--porcelain` puts the status in the first TWO columns, so a
        worktree-only edit starts with a SPACE. Stripping the whole output
        ate that space on the first line and shifted one path by one
        character — a corruption visible only in the error message."""
        self._dirty()
        state = prov.source_state(self.root)
        self.assertEqual(state["dirty"], ["a.py"])


class TestNoGitNoArtifact(unittest.TestCase):
    def test_a_non_repo_cannot_be_packaged(self):
        d = Path(tempfile.mkdtemp(prefix="tp-prov-nogit-"))
        try:
            (d / "pkg.zip").write_bytes(b"x")
            with self.assertRaises(prov.ProvenanceError):
                prov.write(d, d / "pkg.zip", "d" * 64)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestBothPackagersUseIt(unittest.TestCase):
    """One rule, both archives. A Claude-only guard would leave the OpenAI
    submission archive exactly as unaccountable as before."""

    def test_each_packager_writes_a_provenance_record(self):
        for name in ("package_claude.py", "package_openai.py"):
            src = open(os.path.join(ROOT, "scripts", name),
                       encoding="utf-8").read()
            with self.subTest(script=name):
                self.assertIn("release_provenance", src)
                self.assertIn("allow_dirty", src)

    def test_a_refused_build_leaves_no_archive_behind(self):
        """Half-written output is how an unaccountable zip ends up in a
        release: the build 'failed', but dist/ still has a plausible file."""
        for name in ("package_claude.py", "package_openai.py"):
            src = open(os.path.join(ROOT, "scripts", name),
                       encoding="utf-8").read()
            with self.subTest(script=name):
                self.assertIn("output.unlink(missing_ok=True)", src)
                self.assertIn("checksum.unlink(missing_ok=True)", src)


def _protected_main_fixture(root: Path, source: str, first_parent: str,
                            pull_head: str) -> dict:
    fixture = ROOT + "/taskplane/tests/fixtures/release/protected-main-evidence.json"
    evidence = json.loads(Path(fixture).read_text(encoding="utf-8"))
    evidence["source_sha"] = source
    topology = evidence["merge_topology"]
    topology.update({
        "merge_created_sha": source,
        "checked_sha": source,
        "first_parent_sha": first_parent,
        "pull_request_head_sha": pull_head,
    })
    evidence["ci"]["candidate_sha"] = source
    for name, row in evidence["receipts"].items():
        if name != "settings":
            row["source_sha"] = source
    for package in evidence["packages"]:
        package["source_sha"] = source
    inputs = release_evidence.release_input_digests(root)
    evidence["supply_chain"]["workflow_digest"] = inputs["workflow_digest"]
    evidence["supply_chain"]["lock_digest"] = inputs["lock_digest"]
    evidence["receipts"]["settings"]["digest"] = inputs["settings_digest"]
    return evidence


def test_premerge_first_parent_topology_matches_release_gate(tmp_path):
    """The predicted base/head pair must be the protected merge's parents."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path,
                   check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path,
                   check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path,
                   check=True)
    # Release input bytes are part of the proof, not ambient mutable state.
    (tmp_path / ".github/workflows").mkdir(parents=True)
    shutil.copy(Path(ROOT) / ".github/workflows/ci.yml",
                tmp_path / ".github/workflows/ci.yml")
    shutil.copy(Path(ROOT) / "requirements-dev.lock",
                tmp_path / "requirements-dev.lock")
    (tmp_path / "taskplane").mkdir()
    shutil.copy(Path(ROOT) / "taskplane/operational-settings.json",
                tmp_path / "taskplane/operational-settings.json")
    (tmp_path / "base").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path,
                   check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                                   text=True).strip()
    subprocess.run(["git", "checkout", "-qb", "feature"], cwd=tmp_path,
                   check=True)
    (tmp_path / "feature").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "feature"], cwd=tmp_path,
                   check=True)
    pull_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    subprocess.run(["git", "checkout", "-q", "main"], cwd=tmp_path,
                   check=True)
    subprocess.run(["git", "merge", "-q", "--no-ff", "feature", "-m", "merge"],
                   cwd=tmp_path, check=True)
    protected = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()

    evidence = _protected_main_fixture(tmp_path, protected, base, pull_head)
    receipt = release_evidence.create_protected_main_release_gate(
        evidence, repository=tmp_path)
    assert release_evidence.validate_protected_main_release_gate(
        receipt, repository=tmp_path)["source_sha"] == protected

    (tmp_path / "requirements-dev.lock").write_text(
        "changed==2 --hash=sha256:" + "2" * 64 + "\n", encoding="utf-8")
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="dependency lock evidence drifted"):
        release_evidence.validate_protected_main_release_gate(
            receipt, repository=tmp_path)
    shutil.copy(Path(ROOT) / "requirements-dev.lock",
                tmp_path / "requirements-dev.lock")

    wrong = deepcopy(evidence)
    wrong["merge_topology"]["first_parent_sha"] = "d" * 40
    wrong_receipt = release_evidence.create_protected_main_release_gate(
        wrong, repository=tmp_path)
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="first-parent topology"):
        release_evidence.validate_protected_main_release_gate(
            wrong_receipt, repository=tmp_path)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("ci", "event"), "pull_request", "pre-merge CI"),
        (("supply_chain", "immutable_actions"), False, "immutable pins"),
        (("receipts", "browser", "fresh"), False, "browser receipt is stale"),
        (("receipts", "dashboard", "source_sha"), "f" * 40,
         "dashboard receipt is stale"),
        (("receipts", "wave_metrics", "recounted"), True,
         "without re-count"),
        (("receipts", "cleanup", "leak_count"), 1, "zero leaks"),
        (("packages", 0, "dirty"), ["generated"], "dirty"),
    ],
)
def test_release_gate_refuses_every_severed_freshness_edge(path, value, message):
    evidence = json.loads((
        Path(ROOT) / "taskplane/tests/fixtures/release/protected-main-evidence.json"
    ).read_text(encoding="utf-8"))
    target = evidence
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(release_evidence.ReleaseEvidenceError, match=message):
        release_evidence.create_protected_main_release_gate(
            evidence, repository=ROOT)


def test_release_gate_refuses_missing_package_provenance():
    evidence = json.loads((
        Path(ROOT) / "taskplane/tests/fixtures/release/protected-main-evidence.json"
    ).read_text(encoding="utf-8"))
    evidence["packages"].pop()
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="exactly two package provenance"):
        release_evidence.create_protected_main_release_gate(
            evidence, repository=ROOT)


def test_release_gate_rejects_malformed_git_and_sha256_fields():
    fixture = Path(ROOT) / \
        "taskplane/tests/fixtures/release/protected-main-evidence.json"
    evidence = json.loads(fixture.read_text(encoding="utf-8"))
    inputs = release_evidence.release_input_digests(ROOT)
    evidence["supply_chain"]["workflow_digest"] = inputs["workflow_digest"]
    evidence["supply_chain"]["lock_digest"] = inputs["lock_digest"]
    evidence["receipts"]["settings"]["digest"] = inputs["settings_digest"]

    malformed_digest = deepcopy(evidence)
    malformed_digest["packages"][0]["archive_digest"] = "A" * 64
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="lowercase SHA-256"):
        release_evidence.create_protected_main_release_gate(
            malformed_digest, repository=ROOT)

    malformed_git = deepcopy(evidence)
    malformed_git["source_sha"] = "A" * 40
    with pytest.raises(release_evidence.ReleaseEvidenceError,
                       match="lowercase Git SHA"):
        release_evidence.create_protected_main_release_gate(
            malformed_git, repository=ROOT)


if __name__ == "__main__":
    unittest.main()
