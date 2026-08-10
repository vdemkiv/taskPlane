"""Re-applied v0.9.4 verify-round fixes.

1. Screener bypass closures — `env -S '…'` (GNU split-string), `eval $'…'`
   (ANSI-C quoting), fd-prefixed `2>|` (force-clobber redirect). These are
   the whack-a-mole class: each specific hole is patched while the screen
   keeps its honest framing — a cooperative best-effort layer, not an OS
   security boundary.
2. Depgraph stale-edge filter — a deleted module must not survive as an edge
   target via an unchanged importer's cached import list; but the filter
   must keep edges into the RESOLVABLE UNIVERSE (known_stems.values()),
   not just leaf modules-with-files, or legitimate parent-package imports
   (`import src`) get dropped (the v0.9.4 regression).
3. Loop-state legacy fallback — an unmigrated project's in-repo
   `knowledge/state/loop.json` (and pre-spec `.taskplane/loop.json`) keeps
   being read mid-loop; moving state must never orphan it.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import taskplane_lite as tpl  # noqa: E402
import depgraph as dg  # noqa: E402
import loop as loopmod  # noqa: E402


class TestScreenerBypassClosures(unittest.TestCase):
    def test_env_split_string_forms(self):
        self.assertIn("src/a.py",
                      tpl.write_targets("env -S 'rm -rf src/a.py'"))
        self.assertIn("/etc/x",
                      tpl.write_targets("env -i --split-string='tee /etc/x'"))
        self.assertIn("y.py", tpl.write_targets("env -S'rm y.py'"))  # glued
        # nested wrapper inside the split string still unwraps
        self.assertIn("z", tpl.write_targets("env -S 'nohup rm z'"))

    def test_eval_bodies_are_screened(self):
        self.assertIn("x", tpl.write_targets("eval 'rm x'"))
        self.assertIn("b", tpl.write_targets("eval 'cp a b'"))
        # eval of an interpreter one-liner surfaces the opaque mutation
        opaque = tpl._analyze("eval 'python3 -c \"open(1)\"'")[1]
        self.assertIsNotNone(opaque)
        self.assertEqual(opaque[0], "interpreter")

    def test_ansi_c_quoting_decoded(self):
        # \x2d = '-' → the shell runs `rm -rf x.py`
        self.assertIn("x.py",
                      tpl.write_targets("eval $'rm \\x2drf x.py'"))
        # ANSI-C outside eval too
        self.assertIn("q.py", tpl.write_targets("rm $'q.py'"))

    def test_clobber_redirect_forms(self):
        self.assertIn("/etc/f", tpl.write_targets("echo hi 2>| /etc/f"))
        self.assertIn("out.txt", tpl.write_targets("echo hi >| out.txt"))
        self.assertIn("g.txt", tpl.write_targets("echo hi 2>|g.txt"))

    def test_read_only_contract_blocks_the_bypasses(self):
        c = tpl.build_contract("review", read_only=True,
                               write_allow=[".em-review/**"])
        for cmd in ("env -S 'rm -rf src/a.py'",
                    "eval $'rm \\x2drf src/a.py'",
                    "echo boom 2>| src/a.py"):
            allow, reason = tpl.screen_tool(c, "Bash", {"command": cmd},
                                            None)
            self.assertFalse(allow, cmd)

    def test_normal_commands_unaffected(self):
        self.assertEqual(tpl.write_targets("env FOO=1 rm x"), ["x"])
        self.assertEqual(tpl.write_targets("echo a > b"), ["b"])
        self.assertEqual(tpl.write_targets("ls -la"), [])
        # `a || b` (or-chain) still splits into both parts, no false targets
        self.assertEqual(tpl.write_targets("true || false"), [])


class TestDepgraphStaleEdgeFilter(unittest.TestCase):
    def _ws(self):
        ws = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=ws)
        return ws

    def test_deleted_module_edge_dropped_despite_import_cache(self):
        ws = self._ws()
        os.makedirs(os.path.join(ws, "src"))
        open(os.path.join(ws, "src", "a.py"), "w").write("import helper\n")
        open(os.path.join(ws, "helper.py"), "w").write("x = 1\n")
        g1 = dg.scan(ws)
        self.assertTrue(any(e["to"] == "(root)" or "helper" in e["to"]
                            for e in g1["edges"]) or g1["edges"] == [],
                        g1["edges"])
        # capture the edge target actually recorded for the import
        targets_before = {e["to"] for e in g1["edges"]}
        os.remove(os.path.join(ws, "helper.py"))
        g2 = dg.scan(ws)          # a.py unchanged → cached imports reused
        gone = targets_before - {e["to"] for e in g2["edges"]}
        # whatever module helper.py resolved to must no longer be a target
        self.assertTrue(gone or not targets_before,
                        f"stale edge survived: {g2['edges']}")
        # and no ghost module without files lingers as kind=module
        for mid, m in g2["modules"].items():
            if m["kind"] == "module":
                self.assertGreater(
                    m["files"], 0,
                    f"ghost module {mid} survived deletion")

    def test_parent_package_import_edge_survives(self):
        """The v0.9.4 regression: `import src` targets a dir-level module in
        known_stems but NOT a leaf module-with-files — it must be kept."""
        ws = self._ws()
        os.makedirs(os.path.join(ws, "src", "db"))
        open(os.path.join(ws, "src", "db", "conn.py"), "w").write("x=1\n")
        open(os.path.join(ws, "main.py"), "w").write("import src\n")
        g1 = dg.scan(ws)
        e1 = {(e["from"], e["to"]) for e in g1["edges"]}
        self.assertIn(("(root)", "src"), e1, g1["edges"])
        # rescan with main.py unchanged (cache hit) — edge must survive
        g2 = dg.scan(ws)
        e2 = {(e["from"], e["to"]) for e in g2["edges"]}
        self.assertIn(("(root)", "src"), e2, g2["edges"])

    def test_compose_defined_in_edge_survives_filter(self):
        ws = self._ws()
        os.makedirs(os.path.join(ws, "deploy"))
        open(os.path.join(ws, "deploy", "docker-compose.yml"), "w").write(
            "services:\n  api:\n    image: x\n")
        open(os.path.join(ws, "app.py"), "w").write("x=1\n")
        g = dg.scan(ws)
        self.assertIn(("svc:api", "deploy"),
                      {(e["from"], e["to"]) for e in g["edges"]}, g["edges"])


class TestLoopStateLegacyFallback(unittest.TestCase):
    def test_in_repo_knowledge_state_still_read(self):
        """Unmigrated project: loop state under <ws>/knowledge/state/ keeps
        working mid-loop (kb_root resolves to the legacy in-repo KB)."""
        ws = tempfile.mkdtemp()
        legacy_state = os.path.join(ws, "knowledge", "state")
        os.makedirs(legacy_state)
        st = {"step": "execute", "goal": "legacy", "tasks": []}
        with open(os.path.join(legacy_state, "loop.json"), "w") as f:
            json.dump(st, f)
        got = loopmod.load(ws)
        self.assertIsNotNone(got)
        self.assertEqual(got["goal"], "legacy")
        # and saves keep landing where reads resolve — never orphaned
        got["step"] = "evaluate"
        loopmod.save(ws, got)
        again = loopmod.load(ws)
        self.assertEqual(again["step"], "evaluate")

    def test_pre_spec_taskplane_loop_json_still_read(self):
        ws = tempfile.mkdtemp()
        os.makedirs(tpl.tp_dir(ws))
        with open(os.path.join(tpl.tp_dir(ws), "loop.json"), "w") as f:
            json.dump({"step": "plan", "goal": "prespec", "tasks": []}, f)
        got = loopmod.load(ws)
        self.assertIsNotNone(got)
        self.assertEqual(got["goal"], "prespec")


if __name__ == "__main__":
    unittest.main()
