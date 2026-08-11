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
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import release_provenance as prov  # noqa: E402


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
                              text=True).stdout.strip()


class TestTheArtifactNamesItsCommit(_Repo):
    def test_a_clean_build_records_the_commit(self):
        path = prov.write(self.root, self.archive, "deadbeef")
        rec = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(rec["commit"], self._head())
        self.assertEqual(rec["sha256"], "deadbeef")
        self.assertEqual(rec["archive"], "pkg.zip")
        self.assertTrue(rec["verified_source"])
        self.assertEqual(rec["dirty"], [])

    def test_the_record_sits_beside_the_archive(self):
        path = prov.write(self.root, self.archive, "d")
        self.assertEqual(path.name, "pkg.zip.provenance.json")

    def test_it_does_not_claim_ci_passed(self):
        """The line this must not cross. A local build cannot know, and a
        provenance file that implied it would be worse than none — it would
        launder an unverified artifact."""
        rec = json.loads(prov.write(self.root, self.archive, "d")
                         .read_text(encoding="utf-8"))
        self.assertNotIn("ci", {k.lower() for k in rec})
        self.assertIn("does NOT assert that CI passed", rec["note"])


class TestADirtyTreeIsRefused(_Repo):
    def _dirty(self):
        (self.root / "a.py").write_text("x = 2\n", encoding="utf-8")

    def test_packaging_a_dirty_tree_raises(self):
        self._dirty()
        with self.assertRaises(prov.ProvenanceError) as cm:
            prov.write(self.root, self.archive, "d")
        self.assertIn("DIRTY", str(cm.exception))
        self.assertIn("a.py", str(cm.exception))

    def test_the_refusal_explains_what_it_protects(self):
        self._dirty()
        with self.assertRaises(prov.ProvenanceError) as cm:
            prov.write(self.root, self.archive, "d")
        self.assertIn("--allow-dirty", str(cm.exception))
        self.assertIn("check it against CI", str(cm.exception))

    def test_the_override_stamps_the_artifact_permanently(self):
        """A local test archive stays possible. It stops being able to
        pretend it is a release."""
        self._dirty()
        rec = json.loads(
            prov.write(self.root, self.archive, "d", allow_dirty=True)
            .read_text(encoding="utf-8"))
        self.assertFalse(rec["verified_source"])
        self.assertEqual(rec["dirty"], ["a.py"])

    def test_an_untracked_file_counts_as_dirty(self):
        """It ships inside the archive, so it is part of what the commit
        claim would be covering."""
        (self.root / "extra.py").write_text("y = 1\n", encoding="utf-8")
        with self.assertRaises(prov.ProvenanceError):
            prov.write(self.root, self.archive, "d")

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
                prov.write(d, d / "pkg.zip", "d")
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


if __name__ == "__main__":
    unittest.main()
