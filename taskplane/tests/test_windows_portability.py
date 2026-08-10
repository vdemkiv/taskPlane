"""Windows portability, reproduced on any host.

These defects were only ever visible on the advisory `windows-latest` CI leg
— which is slow, times out before finishing the suite, and by construction
cannot block — so they survived a month of green CI. Each test here
reproduces one of them on the runner you are already using, by driving the
host-shaped input through the code that used to mishandle it rather than by
requiring the host.

Classes covered:

  1. SEPARATORS. Windows hands back `C:\\ws\\src\\a.py`; every path taskplane
     COMPARES (scope globs, module ids, component ids) is `/`-shaped. The
     worst consequence was governance, not cosmetics: `norm()` did a string
     prefix test of a backslash path against a `/`-terminated base, so every
     path in a Windows workspace came back `ESCAPES:` and the contract
     screener refused a worker's own in-scope file.

  2. NEWLINES. A file checked out with CRLF must produce the same detector
     score as the same file with LF, or one diff routes differently on
     Windows than it does in CI — and the byte-frozen goldens are the
     things that would disagree.

  3. READ-ONLY TEARDOWN. git marks `.git/objects` read-only and Windows
     refuses to unlink a read-only file. POSIX only needs a writable parent
     directory, which is why this never shows up here — unless the
     DIRECTORY is made read-only too, which is what the test does.

The encoding class is not here: it has its own CI leg (a C locale gives
Python an ASCII default, narrower than cp1252) that runs the WHOLE suite.
"""
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decompose  # noqa: E402
import depgraph as dg  # noqa: E402
import lens_signals as ls  # noqa: E402
import taskplane_lite as tp  # noqa: E402


class TestSeparatorsAreNormalizedAtTheBoundary(unittest.TestCase):
    def test_to_posix(self):
        self.assertEqual(tp.to_posix(r"C:\ws\src\a.py"), "C:/ws/src/a.py")
        self.assertEqual(tp.to_posix("src/a.py"), "src/a.py")
        self.assertEqual(tp.to_posix(""), "")
        self.assertEqual(tp.to_posix(None), "")

    def test_norm_does_not_declare_a_windows_path_out_of_scope(self):
        """THE governance defect: with a Windows-shaped realpath, every
        in-scope file resolved to 'ESCAPES:' and the screener refused it."""
        real, isabs = os.path.realpath, os.path.isabs
        join = os.path.join
        try:
            os.path.realpath = lambda p: (
                str(p).replace("/", "\\") if str(p)[1:2] == ":"
                else "C:\\ws" + str(p).replace("/", "\\"))
            os.path.isabs = lambda p: str(p)[1:2] == ":" or isabs(p)
            os.path.join = lambda a, b: str(a).rstrip("\\") + "\\" + \
                str(b).replace("/", "\\")
            self.assertEqual(tp.norm("src/a.py", "/ws"), "src/a.py")
            self.assertEqual(tp.norm("src/sub/b.py", "/ws"), "src/sub/b.py")
        finally:
            os.path.realpath, os.path.isabs, os.path.join = real, isabs, join

    def test_norm_still_refuses_a_real_escape_on_a_windows_shaped_host(self):
        """The complement — the fix must not turn containment off."""
        real, isabs = os.path.realpath, os.path.isabs
        join = os.path.join
        try:
            os.path.realpath = lambda p: (
                "C:\\other\\secrets.txt" if "escape" in str(p)
                else str(p).replace("/", "\\") if str(p)[1:2] == ":"
                else "C:\\ws" + str(p).replace("/", "\\"))
            os.path.isabs = lambda p: str(p)[1:2] == ":" or isabs(p)
            os.path.join = lambda a, b: str(a).rstrip("\\") + "\\" + \
                str(b).replace("/", "\\")
            self.assertTrue(tp.norm("escape", "/ws").startswith("ESCAPES:"))
        finally:
            os.path.realpath, os.path.isabs, os.path.join = real, isabs, join

    def test_module_of_never_mints_a_backslash_id(self):
        self.assertEqual(dg.module_of(r"src\auth\session.py"), "auth")
        self.assertEqual(dg.module_of("src/auth/session.py"), "auth")
        self.assertEqual(dg.module_of(r"web\components\Card.tsx"),
                         dg.module_of("web/components/Card.tsx"))
        for path in (r"src\auth\session.py", r"small\tiny.py",
                     r"engine\mod\views\list.py"):
            with self.subTest(path=path):
                self.assertNotIn("\\", dg.module_of(path))

    def test_component_ids_are_slash_shaped_whatever_the_host_walk_yields(self):
        files = ["engine/mod/views/list.py", "engine/mod/views/detail.py",
                 "engine/mod/store/db.py", "engine/mod/core.py"]
        common = decompose.posixpath.commonpath(
            [decompose.posixpath.dirname(f) for f in files])
        self.assertEqual(common, "engine/mod")
        self.assertNotIn("\\", common)


class TestNewlineNormalization(unittest.TestCase):
    def test_crlf_reads_identically_to_lf(self):
        ws = tempfile.mkdtemp()
        try:
            with open(os.path.join(ws, "crlf.py"), "wb") as f:
                f.write(b"import os\r\n\r\ndef add(a, b):\r\n    return a+b\r\n")
            with open(os.path.join(ws, "lf.py"), "wb") as f:
                f.write(b"import os\n\ndef add(a, b):\n    return a+b\n")
            ctx = ls.make_ctx(ws, ["crlf.py", "lf.py"],
                              graph={"hub_dependents": 0,
                                     "boundary_contracts": [],
                                     "modules": []})
            self.assertEqual(ctx.read("crlf.py"), ctx.read("lf.py"))
            self.assertNotIn("\r", ctx.read("crlf.py"))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_a_crlf_checkout_scores_the_same_as_an_lf_one(self):
        """Detector regexes are line-anchored and their scores are frozen in
        goldens: a CRLF checkout must not route the same diff differently."""
        body = (b"def test_add():\n    assert add(1, 2) == 3\n"
                b"def test_sub():\n    assert sub(3, 1) == 2\n")
        scores = []
        for newline in (b"\n", b"\r\n"):
            ws = tempfile.mkdtemp()
            try:
                with open(os.path.join(ws, "tests_app.py"), "wb") as f:
                    f.write(body.replace(b"\n", newline))
                ctx = ls.make_ctx(ws, ["tests_app.py"],
                                  graph={"hub_dependents": 0,
                                         "boundary_contracts": [],
                                         "modules": []})
                scores.append(ls.detect("qa", ctx)["score"])
            finally:
                shutil.rmtree(ws, ignore_errors=True)
        self.assertEqual(scores[0], scores[1])


class TestReadOnlyTeardown(unittest.TestCase):
    """git marks .git/objects read-only; Windows then refuses to unlink
    them, so a throwaway repo could not be torn down and the PermissionError
    surfaced as a test failure. Reproduced here by making the DIRECTORY
    read-only, which POSIX does refuse."""

    @unittest.skipIf(os.getuid() == 0 if hasattr(os, "getuid") else False,
                     "root ignores directory permissions, so the failure "
                     "this guards cannot be staged as the root user")
    def test_rmtree_clears_the_read_only_bit_and_succeeds(self):
        root = tempfile.mkdtemp()
        victim = os.path.join(root, "repo")
        os.makedirs(os.path.join(victim, "objects"))
        target = os.path.join(victim, "objects", "deadbeef")
        with open(target, "w", encoding="utf-8") as f:
            f.write("object\n")
        os.chmod(target, stat.S_IREAD)
        os.chmod(os.path.join(victim, "objects"), stat.S_IREAD | stat.S_IEXEC)
        try:
            shutil.rmtree(victim)          # the patched rmtree
            self.assertFalse(os.path.exists(victim))
        finally:
            for d, _s, fs in os.walk(root):
                os.chmod(d, 0o700)
                for n in fs:
                    try:
                        os.chmod(os.path.join(d, n), 0o600)
                    except OSError:
                        pass
            shutil.rmtree(root, ignore_errors=True)


class TestNoNewHostShapedPathArithmetic(unittest.TestCase):
    """A static ratchet. The graph and component layers reason about
    repo-relative, '/'-shaped paths; `os.path.join`/`dirname`/`relpath` are
    HOST-shaped and silently reintroduce this whole class. Filesystem
    access (joining onto a workspace, realpath, exists) legitimately uses
    os.path, so the pin is on the count, not on absence."""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    PINS = {"depgraph.py": 12, "decompose.py": 3}

    def test_host_shaped_path_calls_do_not_grow(self):
        import re
        pattern = re.compile(
            r"os\.path\.(join|dirname|basename|normpath|relpath|commonpath)")
        for name, pin in self.PINS.items():
            with self.subTest(module=name):
                src = open(os.path.join(self.ROOT, name),
                           encoding="utf-8").read()
                found = len(pattern.findall(src))
                self.assertLessEqual(
                    found, pin,
                    f"{name}: {found} host-shaped path calls (pin {pin}). "
                    "If this is filesystem access, raise the pin in the same "
                    "commit and say why; if it is repo-path arithmetic, use "
                    "posixpath so Windows keeps producing '/'-shaped ids.")


if __name__ == "__main__":
    unittest.main()
