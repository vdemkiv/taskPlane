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


class TestPidLivenessNeverSignalsOnWindows(unittest.TestCase):
    """`os.kill(pid, 0)` is not a liveness probe on Windows. CPython maps
    signal 0 to CTRL_C_EVENT and calls GenerateConsoleCtrlEvent, so the
    "probe" SENT Ctrl+C to the console process group — which is what killed
    the Windows CI leg mid-suite with KeyboardInterrupt and made every run
    report a partial result that read as slowness. It also never measured
    anything: a dead pid raises a generic OSError there, which the handler
    read as "unknowable, assume alive", so an orphaned contract was never
    auto-released on Windows.

    The Win32 branch is unit-tested here by injecting a fake kernel32, so it
    is covered on every host rather than only where it runs.
    """

    class _FakeKernel32:
        def __init__(self, handle=0, exit_code=None, last_error=87):
            self.handle, self.exit_code = handle, exit_code
            self.last_error, self.closed = last_error, []

        def OpenProcess(self, _access, _inherit, _pid):
            return self.handle

        def GetExitCodeProcess(self, _handle, out):
            if self.exit_code is None:
                return 0
            out._obj.value = self.exit_code
            return 1

        def GetLastError(self):
            return self.last_error

        def CloseHandle(self, handle):
            self.closed.append(handle)

    def test_a_running_process_is_alive(self):
        k = self._FakeKernel32(handle=1234, exit_code=259)   # STILL_ACTIVE
        self.assertTrue(tp._pid_alive_windows(4242, k))
        self.assertEqual(k.closed, [1234])                   # no handle leak

    def test_an_exited_process_is_dead(self):
        k = self._FakeKernel32(handle=1234, exit_code=0)
        self.assertFalse(tp._pid_alive_windows(4242, k))
        self.assertEqual(k.closed, [1234])

    def test_no_such_pid_is_dead(self):
        # OpenProcess fails with ERROR_INVALID_PARAMETER — the case the old
        # code mistook for "unknowable" and answered "alive", so a workspace
        # stayed governed by a process that no longer existed.
        k = self._FakeKernel32(handle=0, last_error=87)
        self.assertFalse(tp._pid_alive_windows(4242, k))

    def test_access_denied_stays_governed(self):
        # The process EXISTS and belongs to another user. Fail toward
        # governed: that is emphatically not an orphan.
        k = self._FakeKernel32(handle=0, last_error=5)
        self.assertTrue(tp._pid_alive_windows(4242, k))

    def test_unreadable_exit_code_stays_governed(self):
        k = self._FakeKernel32(handle=1234, exit_code=None)
        self.assertTrue(tp._pid_alive_windows(4242, k))

    def test_liveness_does_not_reach_os_kill_on_windows(self):
        """The regression that matters: no signal may be sent to probe."""
        import taskplane_lite
        sent, real_platform = [], taskplane_lite.sys.platform

        class _Boom:
            def kill(self, *a):
                sent.append(a)
                raise AssertionError("os.kill must never run on win32")

        real_os_kill = taskplane_lite.os.kill
        try:
            taskplane_lite.sys.platform = "win32"
            taskplane_lite.os.kill = _Boom().kill
            # Off Windows, ctypes.windll does not exist and the guarded
            # except returns True (governed). ON Windows the real Win32
            # probe runs. Either way the ONLY thing this test asserts is
            # the regression: no signal was sent. Use a pid known to be
            # alive so the answer is True on both hosts.
            self.assertTrue(tp._pid_alive(os.getpid()))
            self.assertEqual(sent, [])
        finally:
            taskplane_lite.sys.platform = real_platform
            taskplane_lite.os.kill = real_os_kill

    def test_posix_liveness_still_works(self):
        self.assertTrue(tp._pid_alive(os.getpid()))


class TestArtifactsAreByteIdenticalAcrossHosts(unittest.TestCase):
    """taskplane FINGERPRINTS its own artifacts and compares them byte for
    byte. Windows text mode turns every "\n" json.dump writes into "\r\n",
    so the same state written on two hosts produced different BYTES — and
    the audit differential caught it as a product divergence
    (b'{\\r\\n  "reviews": 6\\r\\n}')."""

    def test_atomic_write_json_emits_lf_on_every_host(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "state.json")
            tp.atomic_write_json(path, {"reviews": 6})
            with open(path, "rb") as f:
                raw = f.read()
            self.assertNotIn(b"\r\n", raw)
            self.assertIn(b"\n", raw)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestBareRootGuardCoversEveryNamedHome(unittest.TestCase):
    """`os.path.expanduser` consults USERPROFILE on Windows and never HOME,
    so the guard that refuses to scope a contract at the session home was
    silently inert on a host that names its home the other way."""

    def test_home_and_userprofile_are_both_protected(self):
        import tp as tpcli
        d = tempfile.mkdtemp()          # not a git repo -> bare
        try:
            for var in ("HOME", "USERPROFILE"):
                with self.subTest(var=var):
                    saved = os.environ.get(var)
                    os.environ[var] = d
                    try:
                        self.assertTrue(tpcli._bare_root(d),
                                        f"{var} home left unprotected")
                    finally:
                        if saved is None:
                            os.environ.pop(var, None)
                        else:
                            os.environ[var] = saved
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestNoNewHostShapedPathArithmetic(unittest.TestCase):
    """A static ratchet. The graph and component layers reason about
    repo-relative, '/'-shaped paths; `os.path.join`/`dirname`/`relpath` are
    HOST-shaped and silently reintroduce this whole class. Filesystem
    access (joining onto a workspace, realpath, exists) legitimately uses
    os.path, so the pin is on the count, not on absence."""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 12 -> 13 (D-0017, repo-declared exclusions): load_excludes joins
    # components.yaml onto the workspace root to OPEN it. That is filesystem
    # access, the category this ratchet's own message says to raise the pin
    # for — not repo-path arithmetic, which still goes through posixpath.
    # The exclusion MATCHING itself is in path_roles.is_excluded and is
    # '/'-shaped on every host by construction.
    # 13 -> 14 (D-0007, declared module identity): _scan_locked's `_read_text`
    # joins a manifest's repo-relative path onto the workspace root to OPEN
    # it — the same filesystem-access category. Every id the reader then
    # mints stays '/'-shaped by construction: `manifest_modules` splits with
    # posixpath and normalizes `\` out of the declared name, and `module_of`
    # walks its prefixes with posixpath.dirname. A Windows scan produces
    # `@acme/ui`, never `@acme\ui` — pinned in test_module_identity.py.
    # 14 -> 15 (D-0016, artifacts as nodes): the artifact pass joins a
    # repo-relative path onto the workspace root to hash the file. Same
    # filesystem-access category again. The artifact TEST (`_is_artifact`)
    # and the reference resolver both run on '/'-shaped paths via posixpath,
    # and `_file_refs` normalizes `\` out of every candidate token before
    # looking it up, so a Windows scan resolves `agents\reviewer.md` in a
    # markdown body to the same edge a Linux scan does.
    # 15 -> 16 (D-0010 wave, Maven dependency edges): the pom reader joins a
    # repo-relative manifest path onto the workspace root to OPEN it. Third
    # instance of the same filesystem-access category, and like the others
    # every id it derives goes through posixpath — the artifactId it emits is
    # a bare `ext:<name>` with no separator in it at all.
    PINS = {"depgraph.py": 16, "decompose.py": 3}

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


class TestEmittedArtifactPathsAreSlashShaped(unittest.TestCase):
    """Dispatch briefs are CROSS-HOST artifacts — their parity goldens are
    compared byte for byte between Claude and Codex — so a path that
    renders `\\` on one host and `/` on the other is a product
    divergence, not a cosmetic one.

    Three fields still carried the host shape after the role-instruction
    fix: the worker workspace (`.tp-work\\t1`), the dashboard pointer
    (`.taskplane\\dashboard.html`) and the published artifacts root. The
    Windows leg caught all three at once, on the same two golden compares.
    """

    def test_worker_workspace_is_normalized_on_the_way_into_a_brief(self):
        win = {"id": "t1", "workspace": r"C:\ws\.tp-work\t1",
               "scope": ["src/**"]}
        out = tp.posix_workspace(win)
        self.assertEqual(out["workspace"], "C:/ws/.tp-work/t1")
        self.assertNotIn("\\", out["workspace"])
        # the caller's task is not mutated: stored state keeps the host
        # shape, because every filesystem use of it still wants that
        self.assertEqual(win["workspace"], r"C:\ws\.tp-work\t1")
        self.assertEqual(out["scope"], win["scope"])

    def test_absent_workspace_is_passed_through_untouched(self):
        for value in ({"id": "t1"}, {"id": "t1", "workspace": None}, None):
            with self.subTest(task=value):
                self.assertIs(tp.posix_workspace(value), value)

    def test_frozen_goldens_carry_no_host_separator(self):
        """The goldens are the gate for the whole class, but they only
        FIRE on a host that produces backslashes. This runs everywhere and
        fails if a golden is ever regenerated on Windows — which would
        freeze the divergence instead of catching it."""
        import glob
        import json as _json
        fixtures = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixtures", "briefs")
        goldens = sorted(glob.glob(os.path.join(fixtures, "golden_*.json")))
        self.assertTrue(goldens, "no golden briefs found to check")

        def walk(node, path):
            if isinstance(node, str):
                # No string in ANY golden carries a backslash today, so the
                # rule is simply "none may". A path-shaped heuristic would
                # have missed `.taskplane\\dashboard.html`, which contains
                # no forward slash at all — verified by injecting exactly
                # that and watching the clever version pass.
                if "\\" in node:
                    yield path, node
            elif isinstance(node, list):
                for i, item in enumerate(node):
                    yield from walk(item, f"{path}[{i}]")
            elif isinstance(node, dict):
                for key, item in node.items():
                    yield from walk(item, f"{path}.{key}")

        for g in goldens:
            with self.subTest(golden=os.path.basename(g)):
                with open(g, encoding="utf-8") as f:
                    raw = f.read()
                # goldens carry a '#' comment header before the JSON body
                body = "".join(l for l in raw.splitlines(keepends=True)
                               if not l.startswith("#"))
                data = _json.loads(body)
                bad = list(walk(data, ""))
                self.assertEqual(
                    bad, [],
                    "golden carries host-shaped paths — it was regenerated "
                    "on a host that emits '\\'. Briefs are compared byte "
                    "for byte across hosts; regenerate on a '/'-shaped host "
                    "and fix the emitter, do not freeze the divergence.")



if __name__ == "__main__":
    unittest.main()
