"""External knowledge store (R-EXT-STORE) — the KB lives OUTSIDE the repo,
one folder per project, so artifacts never get committed/pushed with code.

The headline invariant: after init + recording a decision, `git status` in
the repo is clean — zero taskplane files to accidentally commit.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import taskplane_lite as tl  # noqa: E402
import kb  # noqa: E402
import requirements as req  # noqa: E402
import depgraph as dg  # noqa: E402
import loop  # noqa: E402

_TP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "tp.py")


def _git(ws, *a):
    subprocess.run(["git", *a], cwd=ws, capture_output=True, check=False)


def _repo(prefix="tp-store-"):
    ws = tempfile.mkdtemp(prefix=prefix)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t"); _git(ws, "config", "user.name", "t")
    open(os.path.join(ws, "a.py"), "w").write("x = 1\n")
    _git(ws, "add", "-A"); _git(ws, "commit", "-qm", "base")
    return ws


def _status(ws):
    return subprocess.run(["git", "status", "--porcelain"], cwd=ws,
                          capture_output=True, text=True).stdout.strip()


class TestKey(unittest.TestCase):
    def test_key_is_readable_slug_and_collision_free(self):
        # The readable path slug is still the key's prefix...
        k = tl.project_key("/Users/x/Documents/app")
        self.assertTrue(k.startswith("-Users-x-Documents-app-"))
        # ...but paths that differ only by punctuation get DISTINCT keys
        # (v0.9.6 collapsed these to one shared store).
        keys = {tl.project_key(p) for p in
                ("/x/my-app", "/x/my_app", "/x/my.app", "/x/my app")}
        self.assertEqual(len(keys), 4)

    def test_store_under_home(self):
        prev = os.environ.get("TASKPLANE_HOME")
        home = tempfile.mkdtemp()
        os.environ["TASKPLANE_HOME"] = home
        try:
            root = tl.store_root("/tmp/proj")
            self.assertTrue(root.startswith(os.path.join(home, "projects")))
            self.assertTrue(tl.kb_root("/tmp/proj").endswith("knowledge"))
        finally:
            if prev is None:
                os.environ.pop("TASKPLANE_HOME", None)
            else:
                os.environ["TASKPLANE_HOME"] = prev


class TestExternalWrites(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tp-home-")
        self._prev_home = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = self.home
        self.ws = _repo()

    def tearDown(self):
        # Restore the prior value (do NOT pop) so a later test never falls
        # back to the developer's real ~/.taskplane.
        if self._prev_home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._prev_home

    def test_decision_lands_in_store_not_repo(self):
        e = kb.record_decision(self.ws, "use X", decision="because")
        # in the external store...
        self.assertTrue(os.path.exists(os.path.join(kb.kb_dir(self.ws),
                                                    e["file"])))
        self.assertTrue(kb.kb_dir(self.ws).startswith(self.home))
        # ...and NOT in the repo
        self.assertFalse(os.path.isdir(os.path.join(self.ws, "knowledge")))

    def test_repo_stays_clean_after_init_and_decision(self):
        # the headline invariant
        r = subprocess.run([sys.executable, _TP_PY, "init"], cwd=self.ws,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        kb.record_decision(self.ws, "a decision", decision="d")
        req.record_requirement(self.ws, "a requirement")
        dg.scan(self.ws)
        loop.save(self.ws, {"goal": "g", "step": "plan", "tasks": None,
                            "current_task": 0, "max_fix_cycles": 2,
                            "checkpoints": []})
        # .gitignore may be modified by init; that's the only allowed change
        dirty = [ln for ln in _status(self.ws).splitlines()
                 if ".gitignore" not in ln]
        self.assertEqual(dirty, [], f"repo not clean: {dirty}")
        # nothing knowledge-shaped is tracked
        tracked = subprocess.run(["git", "ls-files"], cwd=self.ws,
                                 capture_output=True, text=True).stdout
        self.assertNotIn("knowledge/", tracked)

    def test_graph_and_loop_state_in_store(self):
        dg.scan(self.ws)
        self.assertTrue(dg._path(self.ws).startswith(self.home))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "knowledge",
                                                     "graph.json")))
        loop.save(self.ws, {"goal": "g", "step": "plan", "tasks": None,
                            "current_task": 0, "max_fix_cycles": 2,
                            "checkpoints": []})
        self.assertTrue(loop._state_dir(self.ws).startswith(self.home))

    def test_meta_records_workspace_and_remote(self):
        _git(self.ws, "remote", "add", "origin",
             "https://example.com/me/app.git")
        meta = tl.write_store_meta(self.ws)
        self.assertEqual(meta["workspace"], os.path.abspath(self.ws))
        self.assertEqual(meta["git_remote"], "https://example.com/me/app.git")
        self.assertTrue(os.path.exists(tl.store_meta_path(self.ws)))


class TestMigration(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tp-home-")
        self._prev_home = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = self.home
        self.ws = _repo()
        # a legacy in-repo, git-tracked knowledge/ (the pre-store world)
        d = os.path.join(self.ws, "knowledge", "decisions")
        os.makedirs(d)
        open(os.path.join(self.ws, "knowledge", "index.json"), "w").write(
            '{"decisions": [{"id": "0001", "title": "old", "status": '
            '"accepted", "date": "2026-01-01", "tags": [], "file": '
            '"decisions/0001-old.md"}], "flows": []}')
        open(os.path.join(d, "0001-old.md"), "w").write("# old decision\n")
        _git(self.ws, "add", "-A"); _git(self.ws, "commit", "-qm", "legacy kb")

    def tearDown(self):
        if self._prev_home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._prev_home

    def test_legacy_read_before_migration(self):
        # before migration, kb_root points at the in-repo dir so reads work
        self.assertEqual(tl.kb_root(self.ws),
                         os.path.join(self.ws, "knowledge"))
        self.assertEqual(len(kb.list_decisions(self.ws)), 1)

    def test_migrate_moves_untracks_and_ignores(self):
        r = subprocess.run([sys.executable, _TP_PY, "kb", "migrate"],
                           cwd=self.ws, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        # data moved out of the repo, into the store
        self.assertFalse(os.path.isdir(os.path.join(self.ws, "knowledge")))
        self.assertTrue(os.path.exists(os.path.join(
            tl.store_root(self.ws), "knowledge", "index.json")))
        # decisions still readable post-move
        self.assertEqual(len(kb.list_decisions(self.ws)), 1)
        # knowledge/ untracked + gitignored
        tracked = subprocess.run(["git", "ls-files"], cwd=self.ws,
                                 capture_output=True, text=True).stdout
        self.assertNotIn("knowledge/", tracked)
        self.assertIn("knowledge/", open(os.path.join(self.ws,
                      ".gitignore")).read())

    def test_where_reports_paths(self):
        r = subprocess.run([sys.executable, _TP_PY, "kb", "where"],
                           cwd=self.ws, capture_output=True, text=True)
        info = json.loads(r.stdout)
        self.assertTrue(info["legacy_in_repo_present"])
        self.assertFalse(info["migrated"])


if __name__ == "__main__":
    unittest.main()
