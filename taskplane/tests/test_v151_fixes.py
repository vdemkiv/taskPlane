"""v1.5.1 — fixes from the v1.5.0 full review (3 blockers, majors, minors).

Every test here FAILS on the v1.5.0 code. B1: .taskplane-kb in
RUNTIME_OWNED; B2: private mode refuses under TASKPLANE_STORE=repo (Tag);
B3: init --plan applies before store resolution. Majors: env-independent
config writes, plan-personal doesn't rewrite the team config, per-user loop
state, publish hardening (corrupt index, stale/lost markers, traversal,
unknown ids). Minors: set-shared no-op error, bare `share` usage error,
notice on repo-supplied config, remote-keyed mode, self-ignore content.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kb  # noqa: E402
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TPPY = os.path.join(ROOT, "taskplane", "tp.py")


def _cli(ws, *args, env=None):
    return subprocess.run([sys.executable, TPPY, *args,
                           "--workspace", ws],
                          capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, encoding="utf-8", errors="replace")


class _Ws(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(os.path.join(self.ws, "src"))
        open(os.path.join(self.ws, "src", "a.py"), "w", encoding="utf-8").write("x=1\n")
        subprocess.run(["git", "init", "-q"], cwd=self.ws)
        subprocess.run(["git", "config", "user.email", "e@e"], cwd=self.ws)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.ws)
        subprocess.run(["git", "add", "-A"], cwd=self.ws)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.ws)
        self._prev = os.environ.pop("TASKPLANE_STORE", None)

    def tearDown(self):
        if self._prev is not None:
            os.environ["TASKPLANE_STORE"] = self._prev


class TestB1RuntimeOwned(_Ws):
    def test_shared_store_writes_are_runtime_owned(self):
        self.assertIn(".taskplane-kb/", tp.RUNTIME_OWNED)
        tp.set_mode(self.ws, plan="team")
        kb.record_decision(self.ws, "gate decision", decision="d")
        head = tp.git_head(self.ws)
        changed = tp.changed_files(self.ws, head)
        self.assertFalse([f for f in changed
                          if f.startswith(".taskplane-kb")],
                         "shared-store bookkeeping must never count as a "
                         "task diff")


class TestB2PrivateInTag(_Ws):
    def test_set_private_refuses_under_repo_env(self):
        r = _cli(self.ws, "share", "set", "private",
                 env={"TASKPLANE_STORE": "repo"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("private mode is unavailable", r.stdout)

    def test_set_private_fine_without_env(self):
        r = _cli(self.ws, "share", "set", "private")
        self.assertEqual(r.returncode, 0)


class TestB3InitOrder(_Ws):
    def test_init_plan_team_scaffolds_into_repo_store(self):
        r = _cli(self.ws, "init", "--plan", "team")
        out = json.loads(r.stdout)
        self.assertEqual(out["mode"]["store"], "repo")
        # the context docs must exist where kb_root NOW resolves
        p = os.path.join(self.ws, ".taskplane-kb", "knowledge",
                         "context", "current-state.md")
        self.assertTrue(os.path.exists(p),
                        "context docs landed in the wrong store")


class TestConfigWriteHygiene(_Ws):
    def test_env_never_materializes_config_json(self):
        os.environ["TASKPLANE_STORE"] = "repo"
        try:
            tp.set_mode(self.ws, private=False)   # any setting call
        finally:
            os.environ.pop("TASKPLANE_STORE", None)
        self.assertFalse(os.path.exists(os.path.join(
            self.ws, ".taskplane-kb", "config.json")),
            "a transient env override must not create a committable config")

    def test_plan_personal_does_not_rewrite_team_config(self):
        tp.set_mode(self.ws, plan="team")
        cfg_p = os.path.join(self.ws, ".taskplane-kb", "config.json")
        before = open(cfg_p, encoding="utf-8").read()
        tp.set_mode(self.ws, plan="personal")
        self.assertEqual(open(cfg_p, encoding="utf-8").read(), before,
                         "one user's personal plan must not mutate the "
                         "committed team file")


class TestStatePerUser(_Ws):
    def test_team_mode_loop_state_stays_out_of_repo(self):
        tp.set_mode(self.ws, plan="team")
        loop.init(self.ws, "g")
        self.assertIsNotNone(loop.load(self.ws))
        self.assertFalse(os.path.exists(os.path.join(
            self.ws, ".taskplane-kb", "knowledge", "state")),
            "coordination state must be per-user; share knowledge, not "
            "the state machine")

    def test_tag_env_keeps_state_in_repo(self):
        os.environ["TASKPLANE_STORE"] = "repo"
        try:
            loop.init(self.ws, "g")
            self.assertTrue(os.path.exists(os.path.join(
                self.ws, ".taskplane-kb", "knowledge", "state",
                "loop.json")))
        finally:
            os.environ.pop("TASKPLANE_STORE", None)


class TestPublishHardening(_Ws):
    def setUp(self):
        super().setUp()
        tp.set_mode(self.ws, plan="team")
        tp.set_mode(self.ws, private=True)
        kb.record_decision(self.ws, "A", decision="a")
        kb.record_decision(self.ws, "B", decision="b")
        self.src_idx = os.path.join(tp.external_store_root(self.ws),
                                    "knowledge", "index.json")

    def test_corrupt_shared_index_aborts_push(self):
        dst = os.path.join(self.ws, ".taskplane-kb", "knowledge")
        os.makedirs(dst, exist_ok=True)
        open(os.path.join(dst, "index.json"), "w", encoding="utf-8").write("{truncated")
        out = kb.publish(self.ws)
        self.assertIn("error", out)
        self.assertEqual(out["pushed"], [])

    def test_stale_marker_repushes_after_store_rebuild(self):
        kb.publish(self.ws)
        shutil.rmtree(os.path.join(self.ws, ".taskplane-kb"))
        out = kb.publish(self.ws)          # markers are stale now
        self.assertEqual(len(out["pushed"]), 2,
                         "a rebuilt shared store must be repushable")

    def test_lost_marker_repaired_not_duplicated(self):
        kb.publish(self.ws)
        # simulate the crash window: private markers lost after dst write
        idx = json.load(open(self.src_idx, encoding="utf-8"))
        for d in idx["decisions"]:
            d.pop("published_as", None)
        json.dump(idx, open(self.src_idx, "w", encoding="utf-8"))
        out = kb.publish(self.ws)
        self.assertEqual(out["pushed"], [], "content-based idempotency: "
                         "retry must repair, not duplicate")
        self.assertEqual(len(out["already_published"]), 2)
        idx = json.load(open(self.src_idx, encoding="utf-8"))
        self.assertTrue(all(d.get("published_as")
                            for d in idx["decisions"]))

    def test_unknown_ids_reported_and_cli_fails(self):
        out = kb.publish(self.ws, ids=["9999"])
        self.assertEqual(out["unknown_ids"], ["9999"])
        r = _cli(self.ws, "share", "push", "--ids", "9999")
        self.assertEqual(r.returncode, 1)

    @unittest.skipUnless(
        "utf" in sys.getfilesystemencoding().lower(),
        "needs a UTF-8 filesystem encoding: this case carries non-ASCII "
        "through argv/paths, which a C-locale host cannot represent at all "
        "(a harness limit, not a product limit — Windows paths are UTF-16)")
    def test_missing_file_and_traversal_reported_as_malformed(self):
        idx = json.load(open(self.src_idx, encoding="utf-8"))
        idx["decisions"][0]["file"] = "decisions/ного-such.md"
        idx["decisions"][1]["file"] = "../../../../etc/hostname"
        json.dump(idx, open(self.src_idx, "w", encoding="utf-8"))
        out = kb.publish(self.ws)
        problems = {m["problem"] for m in out["malformed"]}
        self.assertEqual(len(out["malformed"]), 2)
        self.assertIn("file path escapes the private store", problems)


class TestShareUX(_Ws):
    def test_set_shared_on_personal_plan_errors_with_hint(self):
        r = _cli(self.ws, "share", "set", "shared")
        self.assertEqual(r.returncode, 1)
        self.assertIn("share plan team|enterprise", r.stdout)

    def test_bare_share_is_usage_error_not_traceback(self):
        r = subprocess.run([sys.executable, TPPY, "share"],
                           capture_output=True, text=True,
                           env={**os.environ}, encoding="utf-8", errors="replace")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)

    def test_repo_supplied_config_carries_notice(self):
        # a "cloned repo" ships config.json; the user has no mode.json
        os.makedirs(os.path.join(self.ws, ".taskplane-kb"), exist_ok=True)
        json.dump({"plan": "team", "store": "repo"},
                  open(os.path.join(self.ws, ".taskplane-kb",
                                    "config.json"), "w", encoding="utf-8"))
        prev = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = tempfile.mkdtemp()  # fresh user
        try:
            m = tp.get_mode(self.ws)
            self.assertEqual(m["store"], "repo")     # inheritance works
            self.assertIn("team-visible", m.get("notice", ""))
            tp.set_mode(self.ws, private=True)       # any own setting…
            self.assertNotIn("notice", tp.get_mode(self.ws))  # …silences
        finally:
            if prev is None:
                os.environ.pop("TASKPLANE_HOME", None)
            else:
                os.environ["TASKPLANE_HOME"] = prev

    def test_mode_follows_repo_across_checkouts_via_remote(self):
        subprocess.run(["git", "remote", "add", "origin",
                        "https://example.com/team/repo.git"], cwd=self.ws)
        tp.set_mode(self.ws, private=True)
        ws2 = os.path.join(self.tmp, "ws2")           # second checkout
        os.makedirs(ws2)
        subprocess.run(["git", "init", "-q"], cwd=ws2)
        subprocess.run(["git", "remote", "add", "origin",
                        "https://example.com/team/repo.git"], cwd=ws2)
        m = tp.get_mode(ws2)
        self.assertTrue(m["private"],
                        "privacy must follow the repo, not the path")


class TestSelfIgnoreContent(unittest.TestCase):
    def test_permissive_planted_gitignore_is_rewritten(self):
        d = tempfile.mkdtemp()
        gi = os.path.join(d, ".gitignore")
        open(gi, "w", encoding="utf-8").write("!trace.jsonl\n")     # planted by a repo
        tp._ensure_self_ignored(d)
        self.assertIn("*", open(gi, encoding="utf-8").read().splitlines())


if __name__ == "__main__":
    unittest.main()
