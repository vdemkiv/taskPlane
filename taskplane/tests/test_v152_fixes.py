"""v1.5.2 — fixes from the full-catalog review of v1.5.1 (all severities).

Each test fails on the v1.5.1 code. Grouped by the finding it closes.
"""
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402
import kb  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import tp as cli  # noqa: E402  (tp.py importable as a module)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TPPY = os.path.join(ROOT, "taskplane", "tp.py")


def _pop_store(case):
    """Clear the TASKPLANE_STORE override for ONE test and put it back.

    t9 (R-0011 E2): these used to be bare `os.environ.pop(...)` calls. On a
    machine (or CI leg) that exports TASKPLANE_STORE, the first such test
    deleted it for every LATER test module in the process — an invisible,
    order-dependent behavior change. conftest.py's _env_mutation_guard now
    fails the module on exactly that.
    """
    prev = os.environ.get("TASKPLANE_STORE")
    case.addCleanup(
        lambda: (os.environ.__setitem__("TASKPLANE_STORE", prev)
                 if prev is not None
                 else os.environ.pop("TASKPLANE_STORE", None)))
    os.environ.pop("TASKPLANE_STORE", None)


def _repo(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "src"))
    open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8").write("x=1\n")
    for a in (["init", "-q"], ["config", "user.email", "e@e"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "base"]):
        subprocess.run(["git", *a], cwd=ws, capture_output=True)
    return ws


# ---- security: write-screen coverage (H1) ----
class TestWriteScreen(unittest.TestCase):
    def _blocked(self, cmd, scope="src/**"):
        coding = {"scope_paths": [scope], "coding": {}}
        return tp.screen_command(cmd, coding, "/ws")

    def test_sort_o_outside_scope_blocked(self):
        self.assertIsNotNone(self._blocked("sort -o /etc/evil data.txt"))

    def test_cp_target_directory_flag_screened(self):
        # -t DIR puts the dest FIRST; the old last-arg rule screened a source
        self.assertIsNotNone(self._blocked("cp -t /etc a.py b.py"))

    def test_git_apply_blocked_under_governed_contract(self):
        self.assertIsNotNone(self._blocked("git apply patch.diff"))

    def test_patch_blocked_under_governed_contract(self):
        self.assertIsNotNone(self._blocked("patch < fix.diff"))

    def test_in_scope_sort_o_allowed(self):
        self.assertIsNone(self._blocked("sort -o src/out.txt src/in.txt"))


# ---- security: depgraph XSS (H2) ----
class TestDepgraphXSS(unittest.TestCase):
    def test_module_names_escaped_and_no_script_breakout(self):
        tmp = tempfile.mkdtemp()
        ws = _repo(tmp)
        g = {"modules": {"</script><img src=x onerror=alert(1)>": {"kind": "svc"},
                         "safe": {"kind": "svc"}},
             "edges": [], "files": {}, "recorded": []}
        out = os.path.join(tmp, "dg.html")
        orig = depgraph.load
        depgraph.load = lambda _ws: g            # crafted graph
        try:
            depgraph.to_html(ws, changed_files=["src/a.py"], out=out)
        finally:
            depgraph.load = orig
        html = open(out, encoding="utf-8").read()
        self.assertNotIn("<img src=x onerror", html)   # raw tag never forms
        # exactly one </script> — the legitimate closer, none injected
        self.assertEqual(html.count("</script>"), 1)


# ---- architecture: repo-mode collision-free ids (H3) ----
class TestRepoModeIds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        self._prev = os.environ.pop("TASKPLANE_STORE", None)
        tp.set_mode(self.ws, plan="team")

    def tearDown(self):
        if self._prev is not None:
            os.environ["TASKPLANE_STORE"] = self._prev

    def test_shared_store_ids_are_hash_suffixed(self):
        r = kb.record_decision(self.ws, "team decision", decision="d")
        self.assertRegex(r["id"], r"^\d{4}-[0-9a-f]{8}$")


# ---- architecture/testability: kb.mutate concurrency (H4, H6) ----
def _rec(ws):
    import kb as _kb
    _kb.record_decision(ws, "concurrent", decision="d")


class TestKbConcurrency(unittest.TestCase):
    def test_parallel_record_no_orphans_no_dupes(self):
        tmp = tempfile.mkdtemp()
        ws = _repo(tmp)
        _pop_store(self)
        # "fork" is POSIX-only. The point of the case is CONCURRENT
        # writers against one index, which spawn gives just as well —
        # and Windows is exactly where the locking needs proving.
        method = ("fork" if "fork" in multiprocessing.get_all_start_methods()
                  else "spawn")
        ctx = multiprocessing.get_context(method)
        procs = [ctx.Process(target=_rec, args=(ws,)) for _ in range(6)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        idx = kb.load_index(ws)
        ids = [d["id"] for d in idx["decisions"]]
        self.assertEqual(len(ids), 6)                 # none lost
        self.assertEqual(len(set(ids)), 6)            # none duplicated
        # every index entry has its file on disk (no orphan)
        for d in idx["decisions"]:
            self.assertTrue(os.path.exists(
                os.path.join(kb.kb_dir(ws), d["file"])))


# ---- testability: in-process CLI harness (H7) + workspace clobber (H5) ----
class TestCliInProcess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        _pop_store(self)

    def test_main_argv_runs_in_process(self):
        rc = cli.main(["decision", "new", "hi", "--workspace", self.ws])
        self.assertEqual(rc, 0)

    def test_workspace_flag_before_subcommand_not_clobbered(self):
        # `decision --workspace X new …` puts --workspace on the PARENT; the
        # child subparser's SUPPRESS default must not reset it to None/cwd.
        rc = cli.main(["decision", "--workspace", self.ws, "new", "hi"])
        self.assertEqual(rc, 0)
        # the decision landed in THIS ws's store, not cwd's
        self.assertTrue(kb.load_index(self.ws)["decisions"])


# ---- qa: gitignore anchor (H8) ----
class TestGitignoreAnchor(unittest.TestCase):
    def test_team_store_is_committable(self):
        tmp = tempfile.mkdtemp()
        ws = _repo(tmp)
        _pop_store(self)
        cli.main(["init", "--plan", "team", "--workspace", ws])
        # the anchored pattern must NOT ignore the shared store
        r = subprocess.run(["git", "check-ignore",
                            ".taskplane-kb/knowledge/index.json"],
                           cwd=ws, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertNotEqual(r.returncode, 0,
                            "shared store must be committable (not ignored)")


# ---- share UX guards (mediums) ----
class TestShareGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        _pop_store(self)

    def _run(self, *args):
        return subprocess.run([sys.executable, TPPY, *args,
                               "--workspace", self.ws],
                              capture_output=True, text=True,
                              env={**os.environ}, encoding="utf-8", errors="replace")

    def test_push_on_personal_plan_is_guarded(self):
        r = self._run("share", "push")
        self.assertEqual(r.returncode, 1)
        self.assertIn("team/enterprise", r.stdout)

    def test_plan_personal_stays_private_when_shared_config_present(self):
        tp.set_mode(self.ws, plan="team")           # creates shared config
        r = self._run("share", "plan", "personal")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        # A repository may offer a shared store, but explicitly selecting a
        # personal plan is a durable local/private choice.  The old notice
        # asserted the pre-M2 behavior where the committed config silently
        # kept this user in the repository store.
        self.assertEqual(
            (out["plan"], out["store"], out["private"], out["source"]),
            ("personal", "external", True, "private-setting"))
        self.assertNotEqual(os.path.realpath(out["store_path"]),
                            os.path.realpath(tp.repo_store_root(self.ws)))

    def test_decision_accept_unknown_id_exits_1(self):
        r = self._run("decision", "accept", "9999")
        self.assertEqual(r.returncode, 1)


# ---- set_mode raises when it cannot persist (medium) ----
class TestSetModePersistFailure(unittest.TestCase):
    def test_raises_when_no_target_writable(self):
        tmp = tempfile.mkdtemp()
        ws = _repo(tmp)
        prev = os.environ.get("TASKPLANE_HOME")
        # point HOME at a path that can't be created (a file, not a dir)
        clash = os.path.join(tmp, "afile")
        open(clash, "w", encoding="utf-8").write("x")
        os.environ["TASKPLANE_HOME"] = os.path.join(clash, "store")
        try:
            # Durable state now fails closed with the kernel's typed state
            # error before attempting a write below a non-directory anchor.
            with self.assertRaises(tp.StateError) as raised:
                tp.set_mode(ws, private=True)
            self.assertEqual(raised.exception.path, clash)
            self.assertIn("not a directory", str(raised.exception))
        finally:
            if prev is None:
                os.environ.pop("TASKPLANE_HOME", None)
            else:
                os.environ["TASKPLANE_HOME"] = prev


# ---- self-ignore content check already in v151; store_env helper (med) ----
class TestStoreEnvHelper(unittest.TestCase):
    def test_store_env_normalizes(self):
        prev = os.environ.get("TASKPLANE_STORE")
        os.environ["TASKPLANE_STORE"] = "  REPO  "
        try:
            self.assertEqual(tp.store_env(), "repo")
        finally:
            if prev is None:
                os.environ.pop("TASKPLANE_STORE", None)
            else:
                os.environ["TASKPLANE_STORE"] = prev


if __name__ == "__main__":
    unittest.main()
