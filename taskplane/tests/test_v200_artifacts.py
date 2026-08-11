"""v2.0.0 — gate-state artifacts published to the active store.

Every gate transition snapshots its decision artifacts (dashboard, plan,
findings, graph, HEADLINES.md, retro) into the ACTIVE store: team/enterprise
plan -> in-repo .taskplane-kb/ (commit it, the org sees progress from a fresh
clone); personal/private -> the external store (nothing leaks into the repo).
The snapshot doubles as a context cache for future sessions.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402


def _repo():
    ws = tempfile.mkdtemp(prefix="tp-art-")
    os.makedirs(os.path.join(ws, "src"))
    with open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8") as f:
        f.write("x = 1\n")
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t"]
                       + args, cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "base"], cwd=ws, check=True)
    return ws


class _Base(unittest.TestCase):
    def setUp(self):
        self.ws = _repo()
        self.home = tempfile.mkdtemp(prefix="tp-art-home-")
        # t9 (R-0011 E2): SAVE the prior values. The old tearDown popped
        # both unconditionally, so an exported TASKPLANE_STORE (or HOME)
        # vanished for every LATER test module in the same process.
        self._env0 = {k: os.environ.get(k)
                      for k in ("TASKPLANE_HOME", "TASKPLANE_STORE")}
        os.environ["TASKPLANE_HOME"] = self.home
        os.environ.pop("TASKPLANE_STORE", None)

    def tearDown(self):
        for k, v in self._env0.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _spec(self, ws):
        os.makedirs(os.path.join(ws, "specs"), exist_ok=True)
        with open(os.path.join(ws, "specs", "spec.md"), "w", encoding="utf-8") as f:
            f.write("# spec\n")

    def _run_to_gate(self):
        loop.init(self.ws, "ship the export feature")
        self._spec(self.ws)
        loop.gate(self.ws, "pass")  # pm -> plan (publishes)
        return os.path.join(tp.store_root(self.ws), "artifacts",
                            "ship-the-export-feature")


class TestTeamStorePublish(_Base):
    """UPDATED for D-0013. Two assertions here pinned the defect: on a team
    plan the store is `<repo>/.taskplane-kb/`, committed with the work, and
    a gate transition auto-copied the rendered dashboard and the model-
    authored `.em-review/findings.json` into it. PRIVACY.md promises the
    opposite in two places — that `.em-review/` and `.taskplane/` stay local
    and git-ignored on BOTH plans, and that publishing to a shared store is
    "a deliberate human act". No human act was involved; a gate was.

    What is pinned now is the corrected contract: the structured, already-
    committed material publishes, the model-authored prose does not, and
    what was withheld is NAMED rather than silently dropped."""

    def test_gate_publishes_structured_artifacts_into_repo_store(self):
        tp.set_mode(self.ws, plan="team")
        root = self._run_to_gate()
        self.assertTrue(root.startswith(self.ws), root)   # in-repo
        self.assertIn(".taskplane-kb", root)
        self.assertTrue(os.path.isfile(
            os.path.join(root, "HEADLINES.md")))

    def test_model_authored_prose_is_not_auto_committed(self):
        tp.set_mode(self.ws, plan="team")
        root = self._run_to_gate()
        self.assertFalse(os.path.exists(
            os.path.join(root, "dashboard.html")),
            "the rendered dashboard embeds review prose and must not land "
            "in a committed store without a human act")

    def test_the_opt_in_is_what_publishes_it(self):
        """The complement — this is a CONSENT gate, not a capability
        removal. A team that wants review artifacts in the repo says so."""
        tp.set_mode(self.ws, plan="team")
        os.environ["TASKPLANE_PUBLISH_REVIEW"] = "1"
        try:
            root = self._run_to_gate()
            self.assertTrue(os.path.isfile(
                os.path.join(root, "dashboard.html")))
        finally:
            os.environ.pop("TASKPLANE_PUBLISH_REVIEW", None)

    def test_an_external_store_is_unchanged(self):
        """PRIVACY.md's promise is about a COMMITTED store. A personal plan
        publishes to `~/.taskplane`, outside the repo and private, which is
        exactly the situation the policy describes as fine."""
        tp.set_mode(self.ws, plan="personal")
        root = self._run_to_gate()
        self.assertFalse(root.startswith(self.ws), root)
        self.assertTrue(os.path.isfile(
            os.path.join(root, "dashboard.html")))

    def test_gate_payload_names_artifacts(self):
        tp.set_mode(self.ws, plan="team")
        loop.init(self.ws, "g2")
        self._spec(self.ws)
        out = loop.gate(self.ws, "pass")
        self.assertIn("artifacts", out)
        self.assertIn("token cache", out["artifacts"]["note"])

    def test_the_plan_snapshots_but_the_findings_do_not(self):
        """`plan/plan.md` is the repo's own committed material and a human
        approved it at the plan gate. `.em-review/findings.json` is model
        output nobody consented to publish."""
        tp.set_mode(self.ws, plan="team")
        os.makedirs(os.path.join(self.ws, "plan"))
        open(os.path.join(self.ws, "plan", "plan.md"), "w", encoding="utf-8").write("# plan\n")
        os.makedirs(os.path.join(self.ws, ".em-review"))
        with open(os.path.join(self.ws, ".em-review", "findings.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"findings": []}, f)
        root = self._run_to_gate()
        self.assertTrue(os.path.isfile(os.path.join(root, "plan.md")))
        self.assertFalse(os.path.exists(os.path.join(root, "findings.json")))

    def test_what_was_withheld_is_named_in_the_payload(self):
        """Silently omitting the review would be its own defect: a reader of
        the snapshot must be able to tell it is incomplete, and why."""
        tp.set_mode(self.ws, plan="team")
        os.makedirs(os.path.join(self.ws, ".em-review"))
        with open(os.path.join(self.ws, ".em-review", "report.md"),
                  "w", encoding="utf-8") as f:
            f.write("# review\n")
        loop.init(self.ws, "withheld-goal")
        self._spec(self.ws)
        out = loop.gate(self.ws, "pass")
        art = out.get("artifacts") or {}
        self.assertIn("report.md", art.get("withheld") or [])
        self.assertIn("deliberate human act", art.get("withheld_reason", ""))

    def test_headlines_append_without_consecutive_dupes(self):
        tp.set_mode(self.ws, plan="team")
        root = self._run_to_gate()
        n1 = len(open(os.path.join(root, "HEADLINES.md"), encoding="utf-8").readlines())
        loop._publish_artifacts(self.ws)          # same state again
        n2 = len(open(os.path.join(root, "HEADLINES.md"), encoding="utf-8").readlines())
        self.assertEqual(n1, n2)                  # deduped
        # a real plan advances the step (fail-closed gate needs tasks)
        os.makedirs(os.path.join(self.ws, "plan"), exist_ok=True)
        open(os.path.join(self.ws, "plan", "plan.md"), "w", encoding="utf-8").write("# p\n")
        with open(os.path.join(self.ws, "plan", "tasks.json"), "w", encoding="utf-8") as f:
            json.dump({"tasks": [{"id": "t1", "scope": ["src/**"],
                                  "tests": "true",
                                  "criteria": ["works"],
                                  "status": "pending"}]}, f)
        out = loop.gate(self.ws, "pass")          # new step -> new line
        self.assertNotIn("error", out)
        n3 = len(open(os.path.join(root, "HEADLINES.md"), encoding="utf-8").readlines())
        self.assertGreater(n3, n2)


class TestPersonalStoreIsolation(_Base):
    def test_personal_plan_publishes_outside_the_repo(self):
        tp.set_mode(self.ws, plan="personal")
        root = self._run_to_gate()
        self.assertFalse(root.startswith(self.ws), root)  # external store
        self.assertTrue(os.path.isfile(
            os.path.join(root, "dashboard.html")))
        # and nothing landed in the repo
        self.assertFalse(os.path.isdir(
            os.path.join(self.ws, ".taskplane-kb", "artifacts")))


class TestFailOpen(_Base):
    def test_publish_failure_never_breaks_the_gate(self):
        tp.set_mode(self.ws, plan="team")
        loop.init(self.ws, "g3")
        self._spec(self.ws)
        orig = tp.store_root
        tp.store_root = lambda w: (_ for _ in ()).throw(OSError("disk"))
        try:
            out = loop.gate(self.ws, "pass")
        finally:
            tp.store_root = orig
        self.assertNotIn("error", out)
        self.assertNotIn("artifacts", out)


if __name__ == "__main__":
    unittest.main()
