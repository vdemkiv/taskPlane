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
    with open(os.path.join(ws, "src", "a.py"), "w") as f:
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
        os.environ["TASKPLANE_HOME"] = self.home
        os.environ.pop("TASKPLANE_STORE", None)

    def tearDown(self):
        os.environ.pop("TASKPLANE_HOME", None)
        os.environ.pop("TASKPLANE_STORE", None)

    def _spec(self, ws):
        os.makedirs(os.path.join(ws, "specs"), exist_ok=True)
        with open(os.path.join(ws, "specs", "spec.md"), "w") as f:
            f.write("# spec\n")

    def _run_to_gate(self):
        loop.init(self.ws, "ship the export feature")
        self._spec(self.ws)
        loop.gate(self.ws, "pass")  # pm -> plan (publishes)
        return os.path.join(tp.store_root(self.ws), "artifacts",
                            "ship-the-export-feature")


class TestTeamStorePublish(_Base):
    def test_gate_publishes_into_repo_store(self):
        tp.set_mode(self.ws, plan="team")
        root = self._run_to_gate()
        self.assertTrue(root.startswith(self.ws), root)   # in-repo
        self.assertIn(".taskplane-kb", root)
        self.assertTrue(os.path.isfile(
            os.path.join(root, "dashboard.html")))
        self.assertTrue(os.path.isfile(
            os.path.join(root, "HEADLINES.md")))

    def test_gate_payload_names_artifacts(self):
        tp.set_mode(self.ws, plan="team")
        loop.init(self.ws, "g2")
        self._spec(self.ws)
        out = loop.gate(self.ws, "pass")
        self.assertIn("artifacts", out)
        self.assertIn("token cache", out["artifacts"]["note"])

    def test_plan_and_findings_snapshot_when_present(self):
        tp.set_mode(self.ws, plan="team")
        os.makedirs(os.path.join(self.ws, "plan"))
        open(os.path.join(self.ws, "plan", "plan.md"), "w").write("# plan\n")
        os.makedirs(os.path.join(self.ws, ".em-review"))
        with open(os.path.join(self.ws, ".em-review", "findings.json"),
                  "w") as f:
            json.dump({"findings": []}, f)
        root = self._run_to_gate()
        self.assertTrue(os.path.isfile(os.path.join(root, "plan.md")))
        self.assertTrue(os.path.isfile(os.path.join(root, "findings.json")))

    def test_headlines_append_without_consecutive_dupes(self):
        tp.set_mode(self.ws, plan="team")
        root = self._run_to_gate()
        n1 = len(open(os.path.join(root, "HEADLINES.md")).readlines())
        loop._publish_artifacts(self.ws)          # same state again
        n2 = len(open(os.path.join(root, "HEADLINES.md")).readlines())
        self.assertEqual(n1, n2)                  # deduped
        # a real plan advances the step (fail-closed gate needs tasks)
        os.makedirs(os.path.join(self.ws, "plan"), exist_ok=True)
        open(os.path.join(self.ws, "plan", "plan.md"), "w").write("# p\n")
        with open(os.path.join(self.ws, "plan", "tasks.json"), "w") as f:
            json.dump({"tasks": [{"id": "t1", "scope": ["src/**"],
                                  "tests": "true",
                                  "criteria": ["works"],
                                  "status": "pending"}]}, f)
        out = loop.gate(self.ws, "pass")          # new step -> new line
        self.assertNotIn("error", out)
        n3 = len(open(os.path.join(root, "HEADLINES.md")).readlines())
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
