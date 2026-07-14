import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lens  # noqa: E402

CAT = lens.load_catalog()   # uses the router's own plugin-root resolution


def ids(routing):
    return {x["id"] for x in routing["lenses"]}


def mode(routing, lid):
    return next(x["mode"] for x in routing["lenses"] if x["id"] == lid)


class TestLensRouter(unittest.TestCase):
    def test_matcher_double_star(self):
        self.assertTrue(lens._match("src/auth/login.py", "**/auth/**"))
        self.assertTrue(lens._match("web/components/Btn.tsx", "**/*.tsx"))
        self.assertFalse(lens._match("src/todo/core.py", "**/auth/**"))

    def test_baseline_fires_on_any_code(self):
        r = lens.route(["src/todo/core.py"], catalog=CAT)
        self.assertIn("code-quality", ids(r))
        self.assertIn("security", ids(r))       # security has code baseline
        self.assertIn("testability", ids(r))

    def test_no_lenses_on_nonuse_change(self):
        r = lens.route(["notes.txt"], catalog=CAT)
        # code-quality/testability/security are code-baseline → not on .txt
        self.assertEqual(ids(r), set())

    def test_design_fires_on_tsx_and_goes_deep(self):
        r = lens.route(["web/components/Card.tsx"], catalog=CAT)
        self.assertIn("design", ids(r))
        self.assertEqual(mode(r, "design"), "subagent")   # deep_globs *.tsx

    def test_security_deep_on_auth(self):
        r = lens.route(["src/auth/session.py"], catalog=CAT)
        self.assertEqual(mode(r, "security"), "subagent")  # deep_globs auth/**

    def test_security_inline_on_ordinary_code(self):
        r = lens.route(["src/todo/core.py"], catalog=CAT)
        self.assertEqual(mode(r, "security"), "inline")    # baseline, not deep

    def test_large_change_escalates_mode(self):
        files = [f"src/todo/m{i}.py" for i in range(9)]   # >= deep_threshold 8
        r = lens.route(files, catalog=CAT)
        self.assertEqual(mode(r, "code-quality"), "subagent")

    def test_product_on_spec(self):
        r = lens.route(["specs/complete.md"], catalog=CAT)
        self.assertIn("product", ids(r))

    def test_task_type_routing(self):
        r = lens.route(["src/x.py"], task_type="migration", catalog=CAT)
        self.assertIn("data-safety", ids(r))

    def test_role_lenses_fire_by_surface(self):
        self.assertIn("tech-writer", ids(lens.route(["docs/guide.md"], catalog=CAT)))
        self.assertIn("qa", ids(lens.route(["tests/test_x.py"], catalog=CAT)))
        self.assertIn("devops", ids(lens.route(["docker/Dockerfile"], catalog=CAT)))
        self.assertIn("dba", ids(lens.route(["db/schema.sql"], catalog=CAT)))
        self.assertIn("mobile", ids(lens.route(["ios/App.swift"], catalog=CAT)))
        self.assertIn("backend", ids(lens.route(["src/api/users.py"], catalog=CAT)))
        self.assertIn("frontend", ids(lens.route(["web/Home.tsx"], catalog=CAT)))
        self.assertIn("accessibility", ids(lens.route(["web/Home.tsx"], catalog=CAT)))

    def test_architecture_effort_tiers(self):
        # governance floor: even a localized code change gets a LIGHT
        # architecture pass — system design is always on (v0.8.0).
        r0 = lens.route(["src/todo/core.py"], catalog=CAT)
        arch0 = next(x for x in r0["lenses"] if x["id"] == "architecture")
        self.assertEqual(arch0["effort"], "light")
        self.assertEqual(arch0["mode"], "inline")
        # …but a pure docs change without architectural globs still skips it
        self.assertNotIn("architecture", ids(lens.route(["docs/notes.md"], catalog=CAT)))
        # light: touches a boundary
        r = lens.route(["src/api/orders.py"], catalog=CAT)
        arch = next(x for x in r["lenses"] if x["id"] == "architecture")
        self.assertEqual(arch["effort"], "light")
        self.assertEqual(arch["mode"], "inline")
        # full: multi-service infra → its own subagent
        r2 = lens.route(["docker-compose.yml", "svc/a.proto"], catalog=CAT)
        arch2 = next(x for x in r2["lenses"] if x["id"] == "architecture")
        self.assertEqual(arch2["effort"], "full")
        self.assertEqual(arch2["mode"], "subagent")
        # full: greenfield task type
        self.assertEqual(
            next(x for x in lens.route(["src/x.py"], task_type="system-design",
                 catalog=CAT)["lenses"] if x["id"] == "architecture")["effort"],
            "full")

    def test_charter_boundaries_present(self):
        # every lens declares what it does NOT own (de-conflict metadata)
        for lz in CAT["lenses"]:
            self.assertTrue(lz.get("boundary"), lz["id"])
            self.assertTrue(lz.get("charter"), lz["id"])

    def test_only_and_skip(self):
        r = lens.route(["src/auth/x.py"], catalog=CAT, only=["security"])
        self.assertEqual(ids(r), {"security"})
        r2 = lens.route(["src/auth/x.py"], catalog=CAT, skip=["security"])
        self.assertNotIn("security", ids(r2))

    def test_reasons_are_explained(self):
        r = lens.route(["src/auth/x.py"], catalog=CAT)
        sec = next(x for x in r["lenses"] if x["id"] == "security")
        self.assertTrue(sec["reasons"])   # non-empty explanation


if __name__ == "__main__":
    unittest.main()


class TestPrimeScope(unittest.TestCase):
    def test_auth_scope_primes_security_deep(self):
        r = lens.prime_scope(["src/auth/**"], catalog=CAT)
        sec = next(x for x in r["lenses"] if x["id"] == "security")
        self.assertEqual(sec["mode"], "subagent")
        self.assertTrue(r["context"]["primed_from_scope"])

    def test_plain_scope_primes_baselines(self):
        got = ids(lens.prime_scope(["src/todo/**"], catalog=CAT))
        self.assertIn("code-quality", got)
        self.assertIn("testability", got)

    def test_file_glob_scope_primes_surface_lens(self):
        self.assertIn("frontend",
                      ids(lens.prime_scope(["web/**"], catalog=CAT)))

    def test_empty_scope_primes_nothing(self):
        self.assertEqual(ids(lens.prime_scope([], catalog=CAT)), set())


class TestRouteGitDiffExcludesLoopOwned(unittest.TestCase):
    def test_loop_artifacts_do_not_route(self):
        import subprocess
        import tempfile
        ws = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=ws)
        for d in ("src", "plan", "knowledge", ".taskplane"):
            os.makedirs(os.path.join(ws, d), exist_ok=True)
        for p in ("src/a.py", "plan/tasks.json",
                  "knowledge/index.json", ".taskplane/loop.json"):
            with open(os.path.join(ws, p), "w") as f:
                f.write("{}")
        r = lens.route_git_diff(ws, base="HEAD", catalog=CAT)
        self.assertEqual(r["context"]["changed_files"], 1)   # only src/a.py
        self.assertNotIn("project-management", ids(r))
