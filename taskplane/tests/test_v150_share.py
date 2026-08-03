"""v1.5.0 — plan-aware onboarding, private mode, and share push.

Onboarding records the Claude plan (personal vs team/enterprise) and it is
changeable any time (`tp share plan`). Personal -> the classic private
external store; team/enterprise -> the shared in-repo store
(.taskplane-kb/, Claude-Tag compatible). On a team plan an individual can
still work in PRIVATE mode (`tp share set private`) and later publish
selected decisions to the shared store with `tp share push` — deliberate,
id-remapped, idempotent, like pushing commits.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kb  # noqa: E402
import taskplane_lite as tp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


class _Ws(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(self.ws)
        subprocess.run(["git", "init", "-q"], cwd=self.ws)
        self._prev = os.environ.pop("TASKPLANE_STORE", None)

    def tearDown(self):
        if self._prev is not None:
            os.environ["TASKPLANE_STORE"] = self._prev


class TestModeResolution(_Ws):
    def test_default_is_personal_external(self):
        m = tp.get_mode(self.ws)
        self.assertEqual((m["plan"], m["store"]), ("personal", "external"))
        self.assertNotIn(self.ws, tp.store_root(self.ws))

    def test_team_plan_switches_to_repo_store(self):
        m = tp.set_mode(self.ws, plan="team")
        self.assertEqual(m["store"], "repo")
        self.assertEqual(tp.store_root(self.ws),
                         os.path.join(self.ws, ".taskplane-kb"))
        # shared config is written so a teammate's clone inherits the mode
        cfg = json.load(open(os.path.join(self.ws, ".taskplane-kb",
                                          "config.json")))
        self.assertEqual(cfg["plan"], "team")

    def test_plan_is_updatable_back_to_personal(self):
        tp.set_mode(self.ws, plan="enterprise")
        m = tp.set_mode(self.ws, plan="personal")
        # NOTE: shared config still exists from the team era, so the store
        # stays repo via shared-config unless the user goes private — the
        # committed team store must not silently vanish for teammates.
        self.assertEqual(m["source"], "shared-config")
        m2 = tp.set_mode(self.ws, private=True)
        self.assertEqual(m2["store"], "external")

    def test_private_mode_overrides_team_plan(self):
        tp.set_mode(self.ws, plan="team")
        m = tp.set_mode(self.ws, private=True)
        self.assertEqual((m["store"], m["source"]),
                         ("external", "private-setting"))
        self.assertNotIn(self.ws, tp.store_root(self.ws))
        back = tp.set_mode(self.ws, private=False)
        self.assertEqual(back["store"], "repo")

    def test_env_override_still_wins(self):
        tp.set_mode(self.ws, plan="team")
        os.environ["TASKPLANE_STORE"] = "external"
        try:
            self.assertEqual(tp.get_mode(self.ws)["source"], "env")
            self.assertNotIn(self.ws, tp.store_root(self.ws))
        finally:
            os.environ.pop("TASKPLANE_STORE", None)

    def test_shared_config_inherited_by_fresh_clone_identity(self):
        # simulate a teammate: same repo dir (carries config.json), but a
        # different TASKPLANE_HOME (their machine — no personal mode.json)
        tp.set_mode(self.ws, plan="team")
        prev = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = tempfile.mkdtemp()
        try:
            m = tp.get_mode(self.ws)
            self.assertEqual((m["store"], m["source"]),
                             ("repo", "shared-config"))
        finally:
            if prev is None:
                os.environ.pop("TASKPLANE_HOME", None)
            else:
                os.environ["TASKPLANE_HOME"] = prev


class TestSharePush(_Ws):
    def setUp(self):
        super().setUp()
        tp.set_mode(self.ws, plan="team")
        tp.set_mode(self.ws, private=True)     # work privately
        kb.record_decision(self.ws, "priv A", decision="a",
                           status="proposed")
        kb.record_decision(self.ws, "priv B", decision="b")

    def test_push_selected_then_rest_idempotent(self):
        out = kb.publish(self.ws, ids=["0001"])
        self.assertEqual([p["private"] for p in out["pushed"]], ["0001"])
        shared1 = out["pushed"][0]["shared"]
        self.assertRegex(shared1, r"^0001-[0-9a-f]{8}$")  # collision-free
        out2 = kb.publish(self.ws)             # pushes only what's left
        self.assertEqual([p["private"] for p in out2["pushed"]], ["0002"])
        self.assertEqual(out2["already_published"],
                         [{"private": "0001", "shared": shared1}])
        out3 = kb.publish(self.ws)
        self.assertEqual(out3["pushed"], [])   # fully idempotent

    def test_pushed_decisions_visible_in_shared_store(self):
        kb.publish(self.ws)
        tp.set_mode(self.ws, private=False)    # flip to the shared store
        titles = [d["title"] for d in kb.list_decisions(self.ws)]
        self.assertEqual(titles, ["priv A", "priv B"])
        d = next(x for x in kb.load_index(self.ws)["decisions"]
                 if x["published_from"] == "0001")
        body = open(os.path.join(kb.kb_dir(self.ws), d["file"])).read()
        self.assertIn("priv A", body)

    def test_shared_ids_remap_when_shared_store_not_empty(self):
        tp.set_mode(self.ws, private=False)
        kb.record_decision(self.ws, "team already had one", decision="x")
        tp.set_mode(self.ws, private=True)
        out = kb.publish(self.ws)
        seqs = [p["shared"][:4] for p in out["pushed"]]
        self.assertEqual(seqs, ["0002", "0003"])  # appended after team's 0001
        for p in out["pushed"]:                    # hash-suffixed, no dense
            self.assertRegex(p["shared"], r"^000\d-[0-9a-f]{8}$")


class TestOnboardingSurface(_Ws):
    def test_init_accepts_plan_and_reports_mode(self):
        tppy = os.path.join(ROOT, "taskplane", "tp.py")
        subprocess.run(["git", "commit", "-qm", "x", "--allow-empty"],
                       cwd=self.ws)
        r = subprocess.run([sys.executable, tppy, "init", "--plan", "team",
                            "--workspace", self.ws],
                           capture_output=True, text=True,
                           env={**os.environ})
        out = json.loads(r.stdout)
        self.assertEqual(out["mode"]["plan"], "team")
        self.assertIsNone(out["plan_question"])   # answered — no nag

    def test_init_without_plan_asks_the_question(self):
        tppy = os.path.join(ROOT, "taskplane", "tp.py")
        subprocess.run(["git", "commit", "-qm", "x", "--allow-empty"],
                       cwd=self.ws)
        r = subprocess.run([sys.executable, tppy, "init",
                            "--workspace", self.ws],
                           capture_output=True, text=True,
                           env={**os.environ})
        out = json.loads(r.stdout)
        self.assertIn("ASK THE HUMAN", out["plan_question"])

    def test_setup_reference_documents_the_flow(self):
        body = open(os.path.join(ROOT, "skills", "tp-go", "references",
                                 "setup.md")).read()
        for must in ("share plan", "share set private", "share push"):
            self.assertIn(must, body)


if __name__ == "__main__":
    unittest.main()
