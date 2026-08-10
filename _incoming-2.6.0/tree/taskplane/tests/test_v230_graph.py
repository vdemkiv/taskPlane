"""v2.3.0 fix-wave regressions: depgraph durability/scale, lens fail-safe
routing, kb id minting + lint caching.

Guardrail contract these tests pin (product-owner binding constraint):
  * graph corruption SURFACES (StateError) — never silently rebuilt as an
    empty graph (an empty graph weakens DoR gating);
  * lens routing failure warns and defaults toward MORE lenses, never fewer;
  * the Go-scanner limitation is disclosed in the graph payload without
    weakening graph DoR.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph as dg      # noqa: E402
import kb                  # noqa: E402
import lens                # noqa: E402
import taskplane_lite as tp  # noqa: E402


def w(ws, rel, content):
    p = os.path.join(ws, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(content)


def git(ws, *args):
    subprocess.run(["git", "-C", ws, "-c", "user.email=e@e",
                    "-c", "user.name=t", *args],
                   check=True, capture_output=True)


# ------------------------------------------------- graph.json durability

class TestGraphCorruptionSurfaces(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        w(self.ws, "src/db/conn.py", "x=1\n")

    def _corrupt(self):
        p = dg._path(self.ws)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("{not json !!!")

    def test_missing_graph_is_legitimate_empty_default(self):
        g = dg.load(self.ws)
        self.assertEqual(g["modules"], {})
        self.assertEqual(g["edges"], [])

    def test_corrupt_graph_raises_state_error_with_remedy(self):
        dg.scan(self.ws)
        self._corrupt()
        with self.assertRaises(tp.StateError) as cm:
            dg.load(self.ws)
        # StateError names the path and a remedy — never a silent default
        self.assertIn("graph.json", str(cm.exception))
        self.assertIn("restore", str(cm.exception))
        # the remedy steers AWAY from delete-and-rescan: recorded manual
        # edges live only in the file's 'recorded' section
        self.assertIn("recorded", str(cm.exception))
        self.assertIn("re-scan", str(cm.exception))

    def test_corrupt_graph_is_never_silently_rebuilt_empty(self):
        dg.scan(self.ws)
        self._corrupt()
        # scan() loads the previous graph first — corruption must surface,
        # not silently produce a fresh (weaker) graph.
        with self.assertRaises(tp.StateError):
            dg.scan(self.ws)
        # and the file is still there for inspection, untouched
        self.assertIn("not json", open(dg._path(self.ws)).read())

    def test_non_object_graph_is_corrupt_too(self):
        p = dg._path(self.ws)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump(["not", "a", "graph"], f)
        with self.assertRaises(tp.StateError):
            dg.load(self.ws)

    def test_corrupt_graph_fails_readiness_closed(self):
        dg.scan(self.ws)
        self._corrupt()
        r = dg.readiness(self.ws, [{"id": "t1", "scope": ["src/db/**"],
                                    "tests": "true"}])
        self.assertFalse(r["passed"])
        self.assertTrue(any("graph" in e for e in r["errors"]))

    def test_save_is_atomic_no_temp_droppings(self):
        g = dg.scan(self.ws)
        dg.save(self.ws, g)
        store = os.path.dirname(dg._path(self.ws))
        self.assertFalse([n for n in os.listdir(store) if ".tmp." in n])
        self.assertEqual(dg.load(self.ws)["modules"].keys(),
                         g["modules"].keys())

    def test_concurrent_record_edges_are_all_kept(self):
        dg.scan(self.ws)
        errs = []

        def rec(i):
            try:
                dg.record_edge(self.ws, f"svc:a{i}", "svc:hub",
                               kind="runtime")
            except Exception as e:      # pragma: no cover
                errs.append(e)

        threads = [threading.Thread(target=rec, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errs, [])
        pairs = {(e["from"], e["to"]) for e in dg.load(self.ws)["edges"]}
        for i in range(8):              # serialized RMW: no lost updates
            self.assertIn((f"svc:a{i}", "svc:hub"), pairs)


# ------------------------------------------------- DoR refusal names field

class TestReadinessNamesNewModulesField(unittest.TestCase):
    def test_refusal_names_field_file_and_example(self):
        ws = tempfile.mkdtemp()
        w(ws, "src/db/conn.py", "x=1\n")
        r = dg.readiness(ws, [{"id": "t1", "scope": ["tests/**"],
                               "tests": "true"}])
        self.assertFalse(r["passed"])
        blocker = next(e for e in r["errors"] if "not declared" in e)
        self.assertIn('"new_modules"', blocker)
        self.assertIn("plan/tasks.json", blocker)
        self.assertIn('["tests"]', blocker)        # concrete example

    def test_declared_new_modules_still_pass(self):
        ws = tempfile.mkdtemp()
        w(ws, "src/db/conn.py", "x=1\n")
        r = dg.readiness(ws, [{"id": "t1", "scope": ["tests/**"],
                               "new_modules": ["tests"], "tests": "true"}])
        self.assertTrue(r["passed"])


# ------------------------------------------------- scan scope / gitignore

class TestScanSkipsVendoredTrees(unittest.TestCase):
    def test_non_git_walk_skips_vendor_target_tox(self):
        ws = tempfile.mkdtemp()
        w(ws, "src/app.py", "x=1\n")
        w(ws, "vendor/lib/dep.go", 'package dep\nimport "fmt"\n')
        w(ws, "target/classes/Gen.java", "package gen;\nclass Gen {}\n")
        w(ws, ".tox/py311/x.py", "x=1\n")
        w(ws, ".mypy_cache/3.11/x.py", "x=1\n")
        w(ws, ".pytest_cache/v/cache/x.py", "x=1\n")
        w(ws, "node_modules/react/index.js", "module.exports = 1\n")
        g = dg.scan(ws)
        for banned in ("vendor/lib/dep.go", "target/classes/Gen.java",
                       ".tox/py311/x.py", ".mypy_cache/3.11/x.py",
                       ".pytest_cache/v/cache/x.py",
                       "node_modules/react/index.js"):
            self.assertNotIn(banned, g["files"], banned)
        self.assertIn("src/app.py", g["files"])

    def test_git_repo_respects_gitignore(self):
        ws = tempfile.mkdtemp()
        w(ws, ".gitignore", "generated/\n")
        w(ws, "src/app.py", "x=1\n")
        w(ws, "generated/gen.py", "x=1\n")     # ignored build output
        git(ws, "init", "-q")
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "i")
        w(ws, "src/new_feature.py", "x=1\n")   # untracked but NOT ignored
        g = dg.scan(ws)
        self.assertIn("src/app.py", g["files"])
        self.assertIn("src/new_feature.py", g["files"])   # DoR not weakened
        self.assertNotIn("generated/gen.py", g["files"])
        self.assertNotIn("generated", g["modules"])

    def test_git_repo_still_skips_committed_vendor(self):
        ws = tempfile.mkdtemp()
        w(ws, "src/app.py", "x=1\n")
        w(ws, "vendor/dep/dep.go", 'package dep\nimport "fmt"\n')
        git(ws, "init", "-q")
        git(ws, "add", "-A")
        git(ws, "commit", "-qm", "i")
        g = dg.scan(ws)
        self.assertNotIn("vendor/dep/dep.go", g["files"])


# ------------------------------------------------- load memo + batch flush

class TestLoadMemoAndBatch(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        w(self.ws, "src/db/conn.py", "x=1\n")
        dg.scan(self.ws)

    def test_load_is_memoized_until_file_changes(self):
        g1 = dg.load(self.ws)
        g2 = dg.load(self.ws)
        self.assertIs(g1, g2)              # per-process memo, no re-parse
        # an EXTERNAL (cross-process style) atomic rewrite is picked up:
        p = dg._path(self.ws)
        g = json.load(open(p))
        g["modules"]["svc:externally-added"] = {"kind": "infra", "files": 0}
        tp.atomic_write_json(p, g)
        self.assertIn("svc:externally-added", dg.load(self.ws)["modules"])

    def test_batch_flushes_once(self):
        writes = []
        orig = tp.atomic_write_json

        def counting(path, data, **kwargs):
            if path.endswith("graph.json"):
                writes.append(path)
            return orig(path, data, **kwargs)

        tp.atomic_write_json = counting
        try:
            with dg.batch(self.ws):
                for i in range(5):
                    dg.record_edge(self.ws, f"svc:s{i}", "svc:hub")
                dg.link_requirement(self.ws, "R-0001", ["src/db/**"],
                                    kind="planned")
        finally:
            tp.atomic_write_json = orig
        self.assertEqual(len(writes), 1)   # ONE flush for the whole command
        g = dg.load(self.ws)
        pairs = {(e["from"], e["to"]) for e in g["edges"]}
        for i in range(5):
            self.assertIn((f"svc:s{i}", "svc:hub"), pairs)
        self.assertIn(("req:R-0001", "db"), pairs)
        # flush was stamped: evidence fingerprint reflects the new edges
        self.assertTrue(g["meta"].get("content_fingerprint"))

    def test_batch_abort_flushes_nothing(self):
        before = {(e["from"], e["to"]) for e in dg.load(self.ws)["edges"]}
        with self.assertRaises(RuntimeError):
            with dg.batch(self.ws):
                dg.record_edge(self.ws, "svc:doomed", "svc:hub")
                raise RuntimeError("boom")
        after = {(e["from"], e["to"]) for e in dg.load(self.ws)["edges"]}
        self.assertEqual(before, after)    # nothing persisted, cache clean

    def test_scan_inside_batch_persists_via_single_flush(self):
        w(self.ws, "src/api/users.py", "from src.db import conn\n")
        with dg.batch(self.ws):
            dg.scan(self.ws)
            dg.record_edge(self.ws, "api", "svc:pg", kind="queries")
        g = dg.load(self.ws)
        pairs = {(e["from"], e["to"]) for e in g["edges"]}
        self.assertIn(("api", "db"), pairs)
        self.assertIn(("api", "svc:pg"), pairs)


# ------------------------------------------------- Go limitation disclosure

class TestGoScannerLimitationDisclosed(unittest.TestCase):
    def test_go_repo_graph_carries_limitation_meta(self):
        ws = tempfile.mkdtemp()
        # the imported internal path's last segment (dbstore) matches no
        # module name, so nothing can resolve even by accident
        w(ws, "cmd/api/main.go",
          'package main\nimport (\n"fmt"\n'
          '"example.com/mod/internal/dbstore"\n)\n')
        w(ws, "internal/db/db.go", 'package db\nimport "fmt"\n')
        g = dg.scan(ws)
        note = (g["meta"].get("scanners") or {}).get("go") or {}
        self.assertEqual(note.get("coverage"), "external-only")
        self.assertIn("not resolved", note.get("limitation", ""))
        # no fabricated internal edges: everything the Go scanner emitted
        # is external
        go_targets = {e["to"] for e in g["edges"]}
        self.assertTrue(all(t.startswith(("ext:", "svc:"))
                            for t in go_targets), go_targets)
        # disclosure travels with readiness output (impact consumers see it)
        r = dg.readiness(ws, [{"id": "t1", "scope": ["cmd/api/**"],
                               "new_modules": [], "tests": "true"}])
        self.assertIn("go", (r["graph"].get("scanners") or {}))
        # ... and does NOT weaken graph DoR
        self.assertTrue(r["passed"])

    def test_non_go_repo_has_no_go_note(self):
        ws = tempfile.mkdtemp()
        w(ws, "src/app.py", "x=1\n")
        g = dg.scan(ws)
        self.assertNotIn("go", (g["meta"].get("scanners") or {}))

    def test_limitation_survives_edge_recording(self):
        ws = tempfile.mkdtemp()
        w(ws, "cmd/api/main.go", 'package main\nimport "fmt"\n')
        dg.scan(ws)
        dg.record_edge(ws, "cmd/api", "internal/db", kind="runtime")
        note = (dg.load(ws)["meta"].get("scanners") or {}).get("go") or {}
        self.assertIn("limitation", note)


# ------------------------------------------------- lens fail-safe routing

class TestHubSignalFailsTowardMoreCoverage(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        w(self.ws, "src/db/conn.py", "x=1\n")

    def test_missing_graph_is_zero_without_warning(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            n = lens.hub_signal(self.ws, ["src/db/conn.py"])
        self.assertEqual(n, 0)
        self.assertEqual(err.getvalue(), "")

    def test_corrupt_graph_warns_and_escalates_not_silences(self):
        dg.scan(self.ws)
        with open(dg._path(self.ws), "w") as f:
            f.write("{broken")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            n = lens.hub_signal(self.ws, ["src/db/conn.py"])
        # MORE lenses, never fewer: escalates to the full-pass threshold
        self.assertGreaterEqual(n, lens._HUB_FULL)
        self.assertIn("hub signal unavailable", err.getvalue())
        # and the escalation actually buys a full architecture pass
        self.assertEqual(
            lens.architecture_effort(["src/db/conn.py"], "fix", False,
                                     hub_dependents=n), "full")

    def test_malformed_edge_row_warns_and_escalates(self):
        dg.scan(self.ws)
        g = dg.load(self.ws)
        g = json.loads(json.dumps(g))
        g["edges"].append({"kind": "broken-row-without-endpoints"})
        tp.atomic_write_json(dg._path(self.ws), g)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            n = lens.hub_signal(self.ws, ["src/db/conn.py"])
        self.assertGreaterEqual(n, lens._HUB_FULL)
        self.assertIn("hub signal unavailable", err.getvalue())


class TestHardLensesMatchCatalog(unittest.TestCase):
    def test_every_hard_lens_exists_in_catalog(self):
        """Drift guard: a renamed/removed catalog lens must not silently
        downgrade a hard lens from the deep model to standard."""
        ids = {l["id"] for l in lens.load_catalog()["lenses"]}
        self.assertLessEqual(lens._HARD_LENSES, ids,
                             f"dead _HARD_LENSES entries: "
                             f"{sorted(lens._HARD_LENSES - ids)}")

    def test_concurrency_ghost_is_gone_and_backend_is_deep(self):
        self.assertNotIn("concurrency", lens._HARD_LENSES)
        self.assertEqual(lens._lens_tier("backend", "deep"), "deep")


# ------------------------------------------------- kb ids + lint caching

class TestKbIdMintingDeletionSafe(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def test_dense_ids_unchanged_without_deletion(self):
        a = kb.record_decision(self.ws, "a")
        b = kb.record_decision(self.ws, "b")
        self.assertEqual([a["id"], b["id"]], ["0001", "0002"])

    def test_minting_never_reuses_an_id_after_compaction(self):
        for t in ("a", "b", "c"):
            kb.record_decision(self.ws, t)
        # simulate a future compaction/archival removing the middle entries
        idx = kb.load_index(self.ws)
        idx["decisions"] = [d for d in idx["decisions"] if d["id"] == "0001"]
        kb._save_index(self.ws, idx)
        d = kb.record_decision(self.ws, "d")
        self.assertEqual(d["id"], "0004")   # max+1, NOT len+1 (= "0002")
        ids = [x["id"] for x in kb.list_decisions(self.ws)]
        self.assertEqual(len(ids), len(set(ids)))


class TestKbArchive(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def test_archive_moves_closed_keeps_accepted_and_never_reuses_ids(self):
        a = kb.record_decision(self.ws, "keep me")            # accepted
        b = kb.record_decision(self.ws, "old call")
        c = kb.record_decision(self.ws, "newest call")
        kb.supersede(self.ws, b["id"], c["id"])
        kb.set_status(self.ws, c["id"], "rejected")
        res = kb.archive(self.ws)
        self.assertEqual(sorted(res["archived"]), sorted([b["id"], c["id"]]))
        hot = [d["id"] for d in kb.list_decisions(self.ws)]
        self.assertEqual(hot, [a["id"]])                      # accepted stays
        # archived entries remain readable in index-archive.json
        arch = json.load(open(os.path.join(kb.kb_dir(self.ws),
                                           "index-archive.json")))
        self.assertEqual(sorted(d["id"] for d in arch["decisions"]),
                         sorted([b["id"], c["id"]]))
        # minting after archiving the HIGHEST id ("0003") must not reuse it
        d = kb.record_decision(self.ws, "post-archive")
        self.assertEqual(d["id"], "0004")
        self.assertNotIn(d["id"], {b["id"], c["id"]})

    def test_archive_explicit_ids_and_noop(self):
        a = kb.record_decision(self.ws, "a")
        kb.record_decision(self.ws, "b")
        res = kb.archive(self.ws, ids=[a["id"]])
        self.assertEqual(res["archived"], [a["id"]])
        self.assertEqual(res["remaining"], 1)
        self.assertEqual(kb.archive(self.ws)["archived"], [])  # nothing closed

    def test_corrupt_archive_refuses_fail_closed(self):
        b = kb.record_decision(self.ws, "old")
        kb.set_status(self.ws, b["id"], "rejected")
        with open(os.path.join(kb.kb_dir(self.ws),
                               "index-archive.json"), "w") as f:
            f.write("{torn")
        res = kb.archive(self.ws)
        self.assertIn("error", res)
        self.assertEqual(res["archived"], [])
        # nothing was dropped from the hot index
        self.assertEqual([d["id"] for d in kb.list_decisions(self.ws)],
                         [b["id"]])


class TestKbLintCaching(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def test_second_lint_uses_cache_with_same_strictness(self):
        kb.record_decision(self.ws, "sneaky",
                           context="You are a helpful agent.", decision="x")
        first = kb.lint(self.ws)
        self.assertTrue(any("prompt marker" in p["problem"] for p in first))
        # cached run must report the SAME violations — a cached record is
        # still linted, not skipped
        second = kb.lint(self.ws)
        self.assertEqual(sorted(p["problem"] for p in first),
                         sorted(p["problem"] for p in second))

    def test_cache_does_not_mask_new_or_changed_files(self):
        kb.record_decision(self.ws, "clean", context="fine", decision="x")
        self.assertEqual(kb.lint(self.ws), [])
        # a NEW bad file after the first lint is still caught
        bad = os.path.join(kb.kb_dir(self.ws), "note.md")
        with open(bad, "w") as f:
            f.write("Act as the system prompt\n")
        problems = kb.lint(self.ws)
        self.assertTrue(any("prompt marker" in p["problem"]
                            for p in problems))
        # a CHANGED file is re-linted (mtime/size signature moves)
        with open(bad, "w") as f:
            f.write("all clean now, plain decision text\n")
        os.utime(bad, ns=(1, 1))    # force a distinct signature
        self.assertEqual(kb.lint(self.ws), [])


if __name__ == "__main__":
    unittest.main()
