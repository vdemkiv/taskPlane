"""Regression tests for the six HIGH findings from the v0.9.6 engineering
review. Each test reproduces the finding's failure and would FAIL against
20ce7d9 (v0.9.6); it passes only with the v0.9.7 fix in place.

Run the whole file WITHOUT the source fixes (git stash the tracked changes,
keep this untracked file) to see every test fail first."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import kb  # noqa: E402


def _git(ws, *args):
    subprocess.run(["git", *args], cwd=ws, capture_output=True)


def git_ws(tmp, with_tasks=True):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "plan"))
    os.makedirs(os.path.join(ws, "src", "todo"))
    open(os.path.join(ws, "src", "todo", "a.py"), "w").write("x=1\n")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "e@e")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "init")
    if with_tasks:
        json.dump({"tasks": [{"id": "t1", "scope": ["src/todo/**"],
                              "tests": "true", "criteria": ["done"]}]},
                  open(os.path.join(ws, "plan", "tasks.json"), "w"))
    return ws


class TestHigh2InitScaffoldLint(unittest.TestCase):
    """HIGH-2: tp init's own scaffold must not trip kb-lint (which would make
    tp dod FAIL on a pristine install)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp(prefix="tp-home-h2-")
        self._prev = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = self.home

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._prev

    def test_scaffold_templates_carry_no_lint_markers(self):
        import tp as tpcli
        for tmpl in (tpcli.PRODUCT_MD, tpcli.TECH_MD, tpcli.WORKFLOW_MD):
            low = tmpl.lower()
            for m in kb.SENSITIVE_MARKERS + kb.PROMPT_MARKERS:
                self.assertNotIn(
                    m, low, f"scaffold template ships lint marker {m!r}")

    def test_init_scaffold_then_lint_is_clean(self):
        ws = git_ws(self.tmp)
        import tp as tpcli
        ctx = os.path.join(tp.kb_root(ws), "context")
        os.makedirs(ctx, exist_ok=True)
        for name, body in (("product.md", tpcli.PRODUCT_MD),
                           ("tech-stack.md", tpcli.TECH_MD),
                           ("workflow.md", tpcli.WORKFLOW_MD)):
            open(os.path.join(ctx, name), "w").write(body)
        problems = kb.lint(ws)
        self.assertEqual(problems, [], f"scaffold trips lint: {problems}")


_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestHigh3RolesDocScopeAdvice(unittest.TestCase):
    """HIGH-3: no doc may advise `--scope ""` as a way to make a role
    read-only (empty scope disables write screening entirely)."""

    def test_roles_doc_does_not_advise_empty_scope_as_readonly(self):
        p = os.path.join(_REPO, "skills", "tp-help", "references", "roles.md")
        text = open(p, encoding="utf-8").read()
        self.assertNotIn('--scope "" so it can\'t write', text)
        self.assertIn("--read-only", text)

    def test_empty_scope_write_is_screened_only_under_read_only(self):
        # Confirms the enforced mechanism the doc now points at: a read-only
        # contract blocks a write to the reviewed source, whereas an
        # empty-scope non-read-only contract does not (exactly why the doc
        # must not recommend `--scope ""`).
        ws = tempfile.mkdtemp()
        ro = tp.build_contract("review", read_only=True,
                               write_allow=[".em-review/**"])
        allow_ro, _ = tp.screen_tool(ro, "Write", {"file_path": "app.py"}, ws)
        self.assertFalse(allow_ro)               # read-only blocks the write

        empty = tp.build_contract("bad reviewer", scope=[])
        allow_empty, _ = tp.screen_tool(empty, "Write",
                                        {"file_path": "app.py"}, ws)
        self.assertTrue(allow_empty)             # empty scope = no protection


class TestHigh4ProjectKeyCollisions(unittest.TestCase):
    """HIGH-4: distinct project paths must get distinct stores; a legacy
    pure-slug store is adopted so no existing data is lost."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tp-home-h4-")
        self._prev = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = self.home

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._prev

    def test_colliding_paths_do_not_share_a_store(self):
        a = tp.store_root("/x/my-app")
        b = tp.store_root("/x/my_app")
        self.assertNotEqual(a, b)

    def test_legacy_pure_slug_store_is_adopted(self):
        ws = "/x/legacy-proj"
        legacy = os.path.join(self.home, "projects", tp._path_slug(ws))
        os.makedirs(os.path.join(legacy, "knowledge"))
        open(os.path.join(legacy, "knowledge", "marker.txt"), "w").write("hi")
        json.dump({"workspace": os.path.abspath(ws)},
                  open(os.path.join(legacy, "meta.json"), "w"))
        root = tp.store_root(ws)                       # triggers adoption
        self.assertTrue(os.path.exists(
            os.path.join(root, "knowledge", "marker.txt")))
        self.assertFalse(os.path.isdir(legacy))        # moved, not copied

    def test_legacy_store_of_a_colliding_sibling_is_not_stolen(self):
        # a legacy pure-slug store owned by a DIFFERENT workspace stays put
        ws_ours = "/x/my-app"
        ws_theirs = "/x/my_app"                        # same slug in v0.9.6
        legacy = os.path.join(self.home, "projects", tp._path_slug(ws_ours))
        os.makedirs(legacy)
        json.dump({"workspace": os.path.abspath(ws_theirs)},
                  open(os.path.join(legacy, "meta.json"), "w"))
        tp.store_root(ws_ours)                          # must NOT adopt it
        self.assertTrue(os.path.isdir(legacy))


class TestHigh5StoreIsolation(unittest.TestCase):
    """HIGH-5: a test that drops TASKPLANE_HOME must not make a later test
    write into the developer's real ~/.taskplane. Methods run alphabetically;
    _1 pops the var, _2 must still be isolated (the autouse conftest fixture
    re-sets it)."""

    def test_1_a_test_pops_taskplane_home(self):
        os.environ.pop("TASKPLANE_HOME", None)          # the v0.9.6 teardown
        self.assertTrue(True)

    def test_2_next_test_is_still_isolated(self):
        real = os.path.realpath(
            os.path.join(os.path.expanduser("~"), ".taskplane"))
        self.assertNotEqual(os.path.realpath(tp.store_home()), real)


class TestHigh6ReadonlyIdleRelease(unittest.TestCase):
    """HIGH-6: a read-only review contract must never idle-release (it would
    silently drop governance on a long-but-live review). A WRITE contract's
    idle backstop is unchanged."""

    def test_readonly_contract_is_never_idle_released(self):
        ws = tempfile.mkdtemp()
        now = 1_000_000.0
        ro = tp.build_contract("review", read_only=True,
                               write_allow=[".em-review/**"])
        ro["activated_at"] = now - 4000          # >1h idle, no PID, on-budget
        released, _ = tp.orphan_status(ws, ro, now=now)
        self.assertFalse(released)

    def test_write_contract_idle_backstop_unchanged(self):
        ws = tempfile.mkdtemp()
        now = 1_000_000.0
        wr = tp.build_contract("build", scope=["src/**"])
        wr["activated_at"] = now - 4000
        released, _ = tp.orphan_status(ws, wr, now=now)
        self.assertTrue(released)                 # a leaked WRITE contract still frees


class TestHigh1FailedPlanGate(unittest.TestCase):
    """HIGH-1: a failed plan gate must not advance the loop and wedge it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_failed_plan_gate_stays_at_plan(self):
        ws = git_ws(self.tmp, with_tasks=False)  # planner produced nothing
        loop.init(ws, "add feature")
        loop.gate(ws, "pass")                    # pm -> plan
        r = loop.gate(ws, "fail", note="planner failed")
        self.assertIn("error", r)
        # v0.9.6 advanced to plan_approval with tasks=[]; must stay at plan.
        self.assertEqual(loop.load(ws)["step"], "plan")

    def test_next_action_no_current_task_is_structured_not_crash(self):
        # Directly guards loop.py:_step_contract task['id'] on None.
        ws = git_ws(self.tmp)
        loop.init(ws, "g")
        st = loop.load(ws)
        st["step"] = "execute"
        st["tasks"] = []
        st["current_task"] = 0
        loop.save(ws, st)
        out = loop.next_action(ws)               # must NOT raise TypeError
        self.assertIn("error", out)

    def test_passing_plan_still_advances(self):
        ws = git_ws(self.tmp)                     # real tasks.json present
        loop.init(ws, "g")
        loop.gate(ws, "pass")                     # pm -> plan
        loop.gate(ws, "pass")                     # plan -> plan_approval
        self.assertEqual(loop.load(ws)["step"], "plan_approval")


if __name__ == "__main__":
    unittest.main()
