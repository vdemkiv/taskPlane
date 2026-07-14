import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kb  # noqa: E402


class TestKB(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def test_record_writes_adr_and_index(self):
        e = kb.record_decision(
            self.ws, "optimistic locking for todo.complete",
            context="concurrent completes", decision="use version column",
            tags=["todo", "concurrency"], context_files=["src/todo/**"])
        self.assertEqual(e["id"], "0001")
        self.assertTrue(os.path.exists(
            os.path.join(kb.kb_dir(self.ws), e["file"])))   # external store
        self.assertEqual(len(kb.list_decisions(self.ws)), 1)

    def test_ids_increment(self):
        kb.record_decision(self.ws, "a")
        e2 = kb.record_decision(self.ws, "b")
        self.assertEqual(e2["id"], "0002")

    def test_retrieve_by_file_overlap(self):
        kb.record_decision(self.ws, "todo lock", tags=["todo"],
                           context_files=["src/todo/**"])
        kb.record_decision(self.ws, "auth session", tags=["auth"],
                           context_files=["src/auth/**"])
        got = kb.retrieve(self.ws, files=["src/todo/core.py", "src/todo/**"])
        self.assertEqual([d["title"] for d in got], ["todo lock"])

    def test_retrieve_by_tag(self):
        kb.record_decision(self.ws, "x", tags=["concurrency"],
                           context_files=["lib/**"])
        got = kb.retrieve(self.ws, files=["src/other/**"], tags=["concurrency"])
        self.assertEqual(len(got), 1)

    def test_retrieve_ranks_and_limits(self):
        kb.record_decision(self.ws, "strong", tags=["todo"],
                           context_files=["src/todo/**"])   # path+tag
        kb.record_decision(self.ws, "weak", tags=[],
                           context_files=["src/todo/**"])   # path only
        got = kb.retrieve(self.ws, files=["src/todo/**"], tags=["todo"], limit=1)
        self.assertEqual(got[0]["title"], "strong")

    def test_superseded_excluded_by_default(self):
        a = kb.record_decision(self.ws, "old", context_files=["src/x/**"])
        b = kb.record_decision(self.ws, "new", context_files=["src/x/**"])
        kb.supersede(self.ws, a["id"], b["id"])
        titles = [d["title"] for d in kb.retrieve(self.ws, files=["src/x/**"])]
        self.assertIn("new", titles)
        self.assertNotIn("old", titles)

    def test_render_context_is_compact(self):
        kb.record_decision(self.ws, "a decision", tags=["t"],
                           context_files=["src/**"])
        ds = kb.retrieve(self.ws, files=["src/**"])
        text = kb.render_context(ds)
        self.assertIn("[0001] a decision", text)
        self.assertIn("knowledge base", text)


class TestKBLoopIntegration(unittest.TestCase):
    def test_approve_records_and_next_recalls(self):
        import json
        import subprocess
        import loop

        ws = tempfile.mkdtemp()
        os.makedirs(os.path.join(ws, "plan"))
        os.makedirs(os.path.join(ws, "src", "todo"))
        open(os.path.join(ws, "src", "todo", "a.py"), "w").write("x=1\n")
        for c in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", *c], cwd=ws)
        subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                        "commit", "-qm", "i"], cwd=ws)
        json.dump({"tasks": [{"id": "t1", "scope": ["src/todo/**"],
                              "tests": "true"}]},
                  open(os.path.join(ws, "plan", "tasks.json"), "w"))

        loop.init(ws, "add complete()", spec_path="s", checkpoints=["plan", "em"])
        loop.next_action(ws); loop.gate(ws, "pass")     # plan → plan_approval
        loop.approve(ws)                                 # records a decision
        self.assertTrue(kb.list_decisions(ws))           # KB has an entry

        act = loop.next_action(ws)                       # execute step
        self.assertIn("knowledge", act)
        recalled = act["knowledge"]["decisions"]
        self.assertTrue(any(d["title"].startswith("Plan approved")
                            for d in recalled))           # relevant recall


if __name__ == "__main__":
    unittest.main()


class TestNoPromptDataLint(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def test_clean_store_passes(self):
        kb.record_decision(self.ws, "use optimistic locking",
                           context="concurrent completes",
                           decision="version column", tags=["db"])
        self.assertEqual(kb.lint(self.ws), [])

    def test_prompt_marker_fails(self):
        kb.record_decision(
            self.ws, "sneaky",
            context="You are a helpful agent. Follow these instructions.",
            decision="x")
        problems = kb.lint(self.ws)
        self.assertTrue(problems)
        self.assertIn("prompt marker", problems[0]["problem"])

    def test_oversized_field_fails(self):
        import json as j
        import os as o
        kbd = kb.kb_dir(self.ws)          # external store
        o.makedirs(kbd, exist_ok=True)
        with open(o.path.join(kbd, "blob.json"), "w") as f:
            j.dump({"dump": "x" * 5000}, f)
        problems = kb.lint(self.ws)
        self.assertTrue(any("exceeds" in p["problem"] for p in problems))


class TestStateInExternalStore(unittest.TestCase):
    def test_loop_and_tracks_live_under_external_state(self):
        import loop
        import track
        ws = tempfile.mkdtemp()
        loop.save(ws, {"goal": "g", "step": "plan", "tasks": None,
                       "current_task": 0, "max_fix_cycles": 2,
                       "checkpoints": []})
        track.new(ws, "t1", "goal")
        self.assertTrue(os.path.exists(
            os.path.join(loop._state_dir(ws), "loop.json")))   # external store
        self.assertTrue(os.path.exists(
            os.path.join(track._state_dir(ws), "tracks.json")))
        # nothing loop/track-shaped left in the runtime dir
        tp_dir = os.path.join(ws, ".taskplane")
        self.assertFalse(os.path.exists(os.path.join(tp_dir, "loop.json")))
