"""R-0003 t1 — decomposition engine (taskplane/decompose.py) tests.

Pins, per the approved Design Contract (contract:component-map) and the t1
acceptance criteria:
  * floors: a module decomposes when >=8 code files OR a >=600-line code
    file; a file cluster needs >=2 files; an intra-file symbol cluster needs
    >=4 top-level symbols spanning >=120 lines; residue folds into
    `<module>::core`; a below-floor module IS its single `::core` component
  * component shape: {id, module, files, symbols, fingerprint, deps,
    lens_map} (+ derived_by; degraded marker on failure), id `<module>::<cluster>`
  * determinism: same tree -> byte-identical derivation
  * components.yaml floor override (documented schema), malformed -> defaults
  * fingerprint cache: unchanged modules skip re-derivation; lens maps
    recompute ONLY on component fingerprint change
  * fail-open: bad AST / unreadable file degrades that module to ::core with
    a degraded marker — never raises out of scan
  * additive layer: modules/edges/files byte-untouched; no `components` key
    (and byte-identical scan behavior) without --decompose; plain scan never
    invokes decompose; meta.content_fingerprint not bumped by the layer
  * CLI: `tp graph scan --decompose` derives; without the flag stdout keys
    are unchanged
  * self-repo acceptance: >=3 components for taskplane/dashboard.py with
    pairwise-distinct dep sets
  * graph_decompose trace {components, recomputed, cache_hits, floor_folded,
    error?}
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard  # noqa: E402
import decompose as dc  # noqa: E402
import depgraph as dg  # noqa: E402
import lens  # noqa: E402
import lens_signals  # noqa: E402
import taskplane_lite as tpl  # noqa: E402

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TESTS))
FIXTURES = os.path.join(TESTS, "fixtures", "decompose")
TPPY = os.path.join(REPO, "taskplane", "tp.py")

CONTRACT_FIELDS = {"id", "module", "files", "symbols", "fingerprint",
                   "deps", "lens_map"}


def _miniapp(tmp):
    ws = os.path.join(tmp, "ws")
    shutil.copytree(os.path.join(FIXTURES, "miniapp"), ws)
    return ws


def _bigfile_ws(tmp, *, broken=False):
    """Synthetic module whose ONLY decomposition trigger is a >=600-line
    file: 6 render_* + 5 db_* top-level defs (each >=55 lines via comment
    padding) plus 2 misc symbols below the intra-file cluster floor."""
    ws = os.path.join(tmp, "bigws")
    d = os.path.join(ws, "bigapp", "gen")
    os.makedirs(d)
    pad = "".join(f"    # pad line {i}\n" for i in range(55))
    out = ["'''Generated hot-spot module (fixture).'''\n",
           "import textwrap\n\n"]
    for i in range(6):
        out.append(f"def render_part_{i}(x):\n{pad}"
                   f"    return esc_html(str(x)) + '{i}'\n\n")
    for i in range(5):
        out.append(f"def db_fetch_{i}(q):\n{pad}"
                   f"    return textwrap.dedent(q) + '{i}'\n\n")
    out.append("def esc_html(s):\n    return s.replace('<', '&lt;')\n\n")
    out.append("def misc_note():\n    return 'x'\n")
    if broken:
        out.append("def broken(:\n    pass\n")
    src = "".join(out)
    assert src.count("\n") >= 600, "fixture must trip the big-file floor"
    with open(os.path.join(d, "huge.py"), "w") as f:
        f.write(src)
    return ws


def _symbolless_bigfile_ws(tmp):
    """B3 (R-0008): a module whose ONLY code file is a >=BIG_FILE_LINES
    Python file of module-level DATA — no top-level def/class at all, so no
    symbol cluster can earn a node AND there is no residual symbol either.
    Built programmatically so the fixture is a real >=600-line module."""
    ws = os.path.join(tmp, "dataws")
    d = os.path.join(ws, "dataapp", "gen")
    os.makedirs(d)
    src = ("'''Generated lookup table (fixture) — module-level data only.'''\n"
           "import json  # noqa: F401\n\n"
           + "".join("ROW_%d = {'id': %d, 'label': 'row %d'}\n" % (i, i, i)
                     for i in range(700)))
    assert src.count("\n") >= dc.BIG_FILE_LINES, "fixture must trip the floor"
    with open(os.path.join(d, "table.py"), "w") as f:
        f.write(src)
    return ws


def _by_id(components):
    return {c["id"]: c for c in components}


def _decompose_traces(ws):
    p = os.path.join(tpl.tp_dir(ws), "trace.jsonl")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        recs = [json.loads(line) for line in f if line.strip()]
    return [r for r in recs if r.get("event") == "graph_decompose"]


class TestFloors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_below_candidate_floor_is_single_core(self):
        ws = _miniapp(self.tmp)
        g = dg.scan(ws)
        comps, _stats = dc.derive(ws, g)
        small = [c for c in comps if c["module"] == "small"]
        self.assertEqual(len(small), 1)
        self.assertEqual(small[0]["id"], "small::core")
        self.assertEqual(small[0]["derived_by"], "core")
        self.assertEqual(small[0]["files"], ["small/tiny.py"])

    def test_directory_clusters_and_residual_core(self):
        ws = _miniapp(self.tmp)
        g = dg.scan(ws)
        comps, stats = dc.derive(ws, g)
        mod = _by_id([c for c in comps if c["module"] == "engine/mod"])
        self.assertIn("engine/mod::views", mod)
        self.assertIn("engine/mod::store", mod)
        self.assertIn("engine/mod::core", mod)
        self.assertEqual(mod["engine/mod::views"]["files"],
                         ["engine/mod/views/detail.py", "engine/mod/views/list.py"])
        self.assertEqual(mod["engine/mod::store"]["files"],
                         ["engine/mod/store/cache.py", "engine/mod/store/db.py"])
        # the four loose utils fail the >=2-file cluster floor -> residual
        self.assertEqual(mod["engine/mod::core"]["files"],
                         [f"engine/mod/util{i}.py" for i in (1, 2, 3, 4)])
        self.assertGreaterEqual(stats["floor_folded"], 1)

    def test_component_deps_cross_cluster_and_external(self):
        ws = _miniapp(self.tmp)
        g = dg.scan(ws)
        comps, _stats = dc.derive(ws, g)
        mod = _by_id(comps)
        views_deps = {(d["to"], d["kind"])
                      for d in mod["engine/mod::views"]["deps"]}
        self.assertIn(("engine/mod::store", "references"), views_deps)
        store_deps = {(d["to"], d["kind"])
                      for d in mod["engine/mod::store"]["deps"]}
        self.assertIn(("ext:sqlalchemy", "imports"), store_deps)

    def test_big_file_symbol_clusters(self):
        ws = _bigfile_ws(self.tmp)
        g = dg.scan(ws)
        comps, _stats = dc.derive(ws, g)
        mod = _by_id([c for c in comps if c["module"] == "bigapp/gen"])
        # one file, three components: render (6 sym), db (5 sym), core (rest)
        self.assertIn("bigapp/gen::render", mod)
        self.assertIn("bigapp/gen::db", mod)
        self.assertIn("bigapp/gen::core", mod)
        render = mod["bigapp/gen::render"]
        self.assertEqual(render["files"], ["bigapp/gen/huge.py"])
        self.assertEqual(len(render["symbols"]), 6)
        self.assertTrue(all(s.startswith("render_")
                            for s in render["symbols"]))
        # esc_html/misc_note fall below the 4-symbol/120-line floor -> core
        self.assertIn("esc_html", mod["bigapp/gen::core"]["symbols"])
        # render calls esc_html (core) -> a references dep; db does not
        render_deps = {(d["to"], d["kind"]) for d in render["deps"]}
        self.assertIn(("bigapp/gen::core", "references"), render_deps)
        db_deps = {(d["to"], d["kind"])
                   for d in mod["bigapp/gen::db"]["deps"]}
        self.assertNotIn(("bigapp/gen::core", "references"), db_deps)

    def test_symbol_floor_constants_named(self):
        # floors live as NAMED constants (t1 criterion) with the design values
        self.assertEqual(dc.CANDIDATE_MIN_FILES, 8)
        self.assertEqual(dc.BIG_FILE_LINES, 600)
        self.assertEqual(dc.CLUSTER_MIN_FILES, 2)
        self.assertEqual(dc.CLUSTER_MIN_SYMBOLS, 4)
        self.assertEqual(dc.CLUSTER_MIN_LINES, 120)


class TestShapeAndDeterminism(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_component_contract_shape(self):
        ws = _miniapp(self.tmp)
        g = dg.scan(ws)
        comps, _stats = dc.derive(ws, g)
        self.assertTrue(comps)
        catalog_ids = {l["id"] for l in lens_signals.load_catalog()["lenses"]}
        for c in comps:
            self.assertTrue(CONTRACT_FIELDS <= set(c),
                            f"{c.get('id')}: missing contract fields")
            self.assertIn("derived_by", c)
            mod, sep, cluster = c["id"].partition("::")
            self.assertEqual(sep, "::")
            self.assertEqual(mod, c["module"])
            self.assertTrue(cluster)
            self.assertNotIn("::", cluster)
            self.assertEqual(c["files"], sorted(set(c["files"])))
            self.assertEqual(c["symbols"], sorted(set(c["symbols"])))
            for d in c["deps"]:
                self.assertEqual(set(d), {"to", "kind"})
                self.assertNotEqual(d["to"], c["id"])
            self.assertEqual(len(c["fingerprint"]), 64)  # sha256 hex
            self.assertEqual(set(c["lens_map"]), catalog_ids)
            for entry in c["lens_map"].values():
                self.assertEqual(set(entry), {"verdict", "score", "evidence"})
                self.assertIn(entry["verdict"], ("deep", "light", "n/a"))
        # sorted by id, no duplicate ids
        ids = [c["id"] for c in comps]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), len(set(ids)))

    def test_derivation_is_deterministic(self):
        ws = _miniapp(self.tmp)
        g = dg.scan(ws)
        comps1, _ = dc.derive(ws, g)
        comps2, _ = dc.derive(ws, g)
        self.assertEqual(json.dumps(comps1, sort_keys=True),
                         json.dumps(comps2, sort_keys=True))


class TestComponentsYaml(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_overrides_floors(self):
        ws = _miniapp(self.tmp)
        with open(os.path.join(ws, "components.yaml"), "w") as f:
            f.write("# raise the floors so nothing decomposes\n"
                    "floors:\n"
                    "  candidate_min_files: 99\n"
                    "  big_file_lines: 100000\n")
        g = dg.scan(ws)
        comps, _stats = dc.derive(ws, g)
        mod = [c for c in comps if c["module"] == "engine/mod"]
        self.assertEqual([c["id"] for c in mod], ["engine/mod::core"])

    def test_malformed_yaml_fails_open_to_defaults(self):
        ws = _miniapp(self.tmp)
        with open(os.path.join(ws, "components.yaml"), "w") as f:
            f.write("floors: {{{:::not yaml\n\t???")
        g = dg.scan(ws)
        comps, _stats = dc.derive(ws, g)   # must not raise
        self.assertIn("engine/mod::views", {c["id"] for c in comps})

    # ---- A7 (R-0007): floor overrides clamp to >= 1 with a degraded marker

    def test_zero_and_negative_floors_clamp_to_one_with_degraded_marker(self):
        """A7: floors of 0 and -3 load as 1 — a floor below 1 could never
        be applied — and the clamp is REPORTED (per-key, with the given
        value) as a `degraded:` marker in the error channel, per the
        fail-open convention: proceed on safe values, never silently."""
        ws = _miniapp(self.tmp)
        with open(os.path.join(ws, "components.yaml"), "w") as f:
            f.write("floors:\n"
                    "  cluster_min_files: 0\n"
                    "  big_file_lines: -3\n")
        floors, err = dc.load_floors(ws)
        self.assertEqual(floors["cluster_min_files"], 1)
        self.assertEqual(floors["big_file_lines"], 1)
        # untouched keys keep their defaults
        self.assertEqual(floors["candidate_min_files"],
                         dc.CANDIDATE_MIN_FILES)
        self.assertIsNotNone(err)
        self.assertIn("degraded", err)
        self.assertIn("cluster_min_files=0", err)
        self.assertIn("big_file_lines=-3", err)

    def test_valid_positive_floors_load_unchanged_without_marker(self):
        ws = _miniapp(self.tmp)
        with open(os.path.join(ws, "components.yaml"), "w") as f:
            f.write("floors:\n"
                    "  cluster_min_files: 3\n"
                    "  big_file_lines: 900\n")
        floors, err = dc.load_floors(ws)
        self.assertIsNone(err)
        self.assertEqual(floors["cluster_min_files"], 3)
        self.assertEqual(floors["big_file_lines"], 900)

    def test_no_file_default_path_is_unchanged(self):
        ws = _miniapp(self.tmp)
        floors, err = dc.load_floors(ws)
        self.assertIsNone(err)
        self.assertEqual(floors, {
            "candidate_min_files": dc.CANDIDATE_MIN_FILES,
            "big_file_lines": dc.BIG_FILE_LINES,
            "cluster_min_files": dc.CLUSTER_MIN_FILES,
            "cluster_min_symbols": dc.CLUSTER_MIN_SYMBOLS,
            "cluster_min_lines": dc.CLUSTER_MIN_LINES})

    def test_garbage_floor_value_fails_open_to_defaults_with_marker(self):
        """A non-integer floor value cannot be clamped — the file fails
        OPEN to the defaults with the existing `ignored` marker."""
        ws = _miniapp(self.tmp)
        with open(os.path.join(ws, "components.yaml"), "w") as f:
            f.write("floors:\n"
                    "  big_file_lines: lots\n")
        floors, err = dc.load_floors(ws)
        self.assertEqual(floors["big_file_lines"], dc.BIG_FILE_LINES)
        self.assertIsNotNone(err)
        self.assertIn("ignored", err)

    def test_floors_hash_reflects_clamped_values(self):
        """The cache key hashes the EFFECTIVE (clamped) floors: a file
        pinning a floor at 0 and one pinning it at 1 are the same
        configuration."""
        ws = _miniapp(self.tmp)
        with open(os.path.join(ws, "components.yaml"), "w") as f:
            f.write("floors:\n  cluster_min_files: 0\n")
        clamped_hash = dc.floors_hash(ws)
        with open(os.path.join(ws, "components.yaml"), "w") as f:
            f.write("floors:\n  cluster_min_files: 1\n")
        self.assertEqual(dc.floors_hash(ws), clamped_hash)

    def test_derive_carries_the_degraded_floor_marker_in_stats(self):
        """The marker rides derive()'s error channel (stats['error']) —
        the same channel depgraph forwards into the graph_decompose
        trace — and the scan still completes on the clamped floors."""
        ws = _miniapp(self.tmp)
        with open(os.path.join(ws, "components.yaml"), "w") as f:
            f.write("floors:\n  candidate_min_files: -1\n")
        g = dg.scan(ws)
        comps, stats = dc.derive(ws, g)    # must not raise
        self.assertTrue(comps)
        self.assertIn("degraded", stats["error"] or "")
        self.assertIn("candidate_min_files=-1", stats["error"])


class TestCacheAndNoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rescan_no_change_is_noop(self):
        ws = _miniapp(self.tmp)
        dg.scan(ws, decompose=True)
        p = dg._path(ws)
        with open(p, "rb") as f:
            before = f.read()
        dg.scan(ws, decompose=True)
        with open(p, "rb") as f:
            after = f.read()
        self.assertEqual(before, after)   # byte-identical graph.json
        traces = _decompose_traces(ws)
        self.assertGreaterEqual(len(traces), 2)
        last = traces[-1]
        for key in ("components", "recomputed", "cache_hits", "floor_folded"):
            self.assertIn(key, last)
        self.assertEqual(last["recomputed"], 0)
        self.assertEqual(last["cache_hits"], last["components"])
        self.assertGreater(last["components"], 0)

    def test_single_component_recompute(self):
        ws = _miniapp(self.tmp)
        g1 = dg.scan(ws, decompose=True)
        before = _by_id(g1["components"])
        with open(os.path.join(ws, "engine/mod/views/list.py"), "a") as f:
            f.write("\n# touched\n")
        g2 = dg.scan(ws, decompose=True)
        after = _by_id(g2["components"])
        traces = _decompose_traces(ws)
        self.assertEqual(traces[-1]["recomputed"], 1)
        # only the touched component's fingerprint moved; every other
        # component (incl. the untouched module 'small') is byte-identical
        self.assertNotEqual(before["engine/mod::views"]["fingerprint"],
                            after["engine/mod::views"]["fingerprint"])
        for cid in before:
            if cid == "engine/mod::views":
                continue
            self.assertEqual(before[cid], after[cid], f"{cid} changed")

    def test_unchanged_module_skips_rederivation(self):
        ws = _miniapp(self.tmp)
        g = dg.scan(ws)
        comps, _ = dc.derive(ws, g)
        prev = dict(g)
        prev["components"] = comps
        prev["meta"] = dict(g.get("meta") or {})
        prev["meta"]["decompose"] = {"floors": dc.floors_hash(ws)}
        comps2, stats2 = dc.derive(ws, g, prev=prev)
        self.assertEqual(stats2["recomputed"], 0)
        self.assertEqual(stats2["modules_skipped"], 2)  # engine/mod + small
        self.assertEqual(json.dumps(comps, sort_keys=True),
                         json.dumps(comps2, sort_keys=True))


class TestFailOpen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bad_ast_degrades_module_never_raises(self):
        ws = _bigfile_ws(self.tmp, broken=True)
        g = dg.scan(ws, decompose=True)   # must not raise
        mod = [c for c in g["components"] if c["module"] == "bigapp/gen"]
        self.assertEqual([c["id"] for c in mod], ["bigapp/gen::core"])
        self.assertTrue(mod[0].get("degraded"))
        self.assertEqual(mod[0]["derived_by"], "core")
        traces = _decompose_traces(ws)
        self.assertIn("error", traces[-1])

    def test_unreadable_file_degrades_module(self):
        ws = _miniapp(self.tmp)
        g = dg.scan(ws)
        real = dc._read_text

        def poisoned(workspace, rel, *a, **k):
            if rel.startswith("engine/mod/"):
                raise OSError("simulated unreadable file")
            return real(workspace, rel, *a, **k)

        dc._read_text = poisoned
        try:
            comps, stats = dc.derive(ws, g)   # must not raise
        finally:
            dc._read_text = real
        mod = [c for c in comps if c["module"] == "engine/mod"]
        self.assertEqual([c["id"] for c in mod], ["engine/mod::core"])
        self.assertTrue(mod[0].get("degraded"))
        self.assertTrue(stats["error"])

    def test_route_verdicts_failure_degrades_not_raises(self):
        ws = _miniapp(self.tmp)
        g = dg.scan(ws)
        real = lens_signals.route_verdicts

        def boom(*a, **k):
            raise RuntimeError("detector meltdown")

        lens_signals.route_verdicts = boom
        try:
            comps, stats = dc.derive(ws, g)   # must not raise
        finally:
            lens_signals.route_verdicts = real
        self.assertTrue(comps)
        for c in comps:
            self.assertTrue(c.get("degraded"))
            self.assertEqual(c["lens_map"], {})
        self.assertTrue(stats["error"])


class TestAdditiveSchema(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plain_scan_has_legacy_keys_only(self):
        ws = _miniapp(self.tmp)
        dg.scan(ws)
        with open(dg._path(ws)) as f:
            raw = json.load(f)
        self.assertEqual(set(raw),
                         {"modules", "edges", "files", "recorded", "meta"})
        self.assertNotIn("decompose", raw["meta"])

    def test_plain_scan_never_invokes_decompose(self):
        ws = _miniapp(self.tmp)
        called = []
        real = dc.derive
        dc.derive = lambda *a, **k: called.append(1) or real(*a, **k)
        try:
            dg.scan(ws)
        finally:
            dc.derive = real
        self.assertEqual(called, [])

    def test_layer_is_additive_over_identical_scan_sections(self):
        ws = _miniapp(self.tmp)
        g_plain = json.loads(json.dumps(
            {k: dg.scan(ws)[k] for k in ("modules", "edges", "files")}))
        ws2 = os.path.join(self.tmp, "ws2")
        shutil.copytree(ws, ws2, ignore=shutil.ignore_patterns(".taskplane"))
        g_dec = dg.scan(ws2, decompose=True)
        for key in ("modules", "edges", "files"):
            self.assertEqual(json.dumps(g_plain[key], sort_keys=True),
                             json.dumps(g_dec[key], sort_keys=True),
                             f"decomposition must not touch {key}")
        self.assertIn("components", g_dec)

    def test_meta_content_fingerprint_not_bumped_by_layer(self):
        # the graph evidence fingerprint covers files+edges only — deriving
        # the layer must not invalidate fingerprints of undecomposed graphs
        ws = _miniapp(self.tmp)
        fp_plain = dg.scan(ws)["meta"]["content_fingerprint"]
        fp_dec = dg.scan(ws, decompose=True)["meta"]["content_fingerprint"]
        self.assertEqual(fp_plain, fp_dec)

    def test_plain_scan_preserves_existing_layer(self):
        ws = _miniapp(self.tmp)
        g1 = dg.scan(ws, decompose=True)
        g2 = dg.scan(ws)   # no flag: the layer is carried, not dropped
        self.assertEqual(json.dumps(g1["components"], sort_keys=True),
                         json.dumps(g2["components"], sort_keys=True))


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        r = subprocess.run([sys.executable, TPPY, *args],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_scan_without_flag_stdout_unchanged(self):
        ws = _miniapp(self.tmp)
        out = self._run("graph", "--workspace", ws, "scan")
        self.assertEqual(list(out), ["modules", "edges", "files", "stored"])

    def test_scan_decompose_reports_components(self):
        ws = _miniapp(self.tmp)
        out = self._run("graph", "--workspace", ws, "scan", "--decompose")
        self.assertIn("components", out)
        self.assertGreaterEqual(out["components"], 4)


class TestSelfRepoAcceptance(unittest.TestCase):
    """Design acceptance row 1: the repo's own tree, taskplane/dashboard.py
    (3,300+ lines) yields >=3 components with pairwise-distinct dep sets."""

    def test_dashboard_yields_three_plus_components(self):
        g = dg.scan(REPO)
        comps, stats = dc.derive(REPO, g)
        dash = [c for c in comps
                if "taskplane/dashboard.py" in c["files"]]
        self.assertGreaterEqual(len(dash), 3)
        dep_sets = [frozenset((d["to"], d["kind"]) for d in c["deps"])
                    for c in dash]
        for i in range(len(dep_sets)):
            for j in range(i + 1, len(dep_sets)):
                self.assertNotEqual(
                    dep_sets[i], dep_sets[j],
                    f"{dash[i]['id']} and {dash[j]['id']} share a dep set")
        self.assertFalse(stats["error"],
                         f"self-repo derivation degraded: {stats['error']}")


# ==========================================================================
# t2 — route v2 COMPONENT ASSEMBLY (design acceptance rows 4-7, R-0003).
#
# The `webshop` fixture is one module (shop/webapp, 8 code files) whose
# decomposition yields four components with DISTINCT lens profiles:
#   shop/webapp::renderer  (tsx)   -> frontend/design/accessibility deep
#   shop/webapp::dbio      (sql)   -> dba/data-safety
#   shop/webapp::gateway   (auth)  -> security/backend
#   shop/webapp::core      (utils) -> baselines only
# Pins: a single-component diff routes THAT component's lenses (plus
# floors) with meta.component_attribution; the cache only PROPOSES (live
# signals dispose); floors + cap-8 demote-never-drop run AFTER assembly on
# the REAL diff ctx; --lens force applies post-assembly; the fail-open
# ladder (component -> module -> breadth=all) only ever WIDENS (structural
# superset guarantee) with a `component_layer_failed` trace; the layer
# ABSENT means byte-identical Phase 1 routing with no component keys; the
# dashboard/graph render the layer additively.
# ==========================================================================

RENDER_DIFF = ["shop/webapp/renderer/screen.tsx",
               "shop/webapp/renderer/widget.tsx"]


def _routed(routing):
    """The deep+light lens ids — the review coverage the routing claims."""
    return {x["id"] for x in routing["lenses"] if x["tier"] != "n/a"}


def _entry(routing, lid):
    return next(x for x in routing["lenses"] if x["id"] == lid)


def _layer_traces(ws):
    p = os.path.join(tpl.tp_dir(ws), "trace.jsonl")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        recs = [json.loads(line) for line in f if line.strip()]
    return [r for r in recs if r.get("event") == "component_layer_failed"]


class _WebshopBase(unittest.TestCase):
    """A scanned+decomposed copy of the webshop fixture per test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = os.path.join(self.tmp, "ws")
        shutil.copytree(os.path.join(FIXTURES, "webshop"), self.ws)
        self.graph = dg.scan(self.ws, decompose=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def route(self, files, **kw):
        return lens.route(files, stage="review", workspace=self.ws, **kw)

    def doctor(self, fn):
        """Mutate the persisted graph.json (cache-poisoning harness)."""
        p = dg._path(self.ws)
        with open(p) as f:
            raw = json.load(f)
        fn(raw)
        with open(p, "w") as f:
            json.dump(raw, f)

    def doctor_component(self, suffix, fn):
        def apply(raw):
            hits = [c for c in raw["components"]
                    if c["id"].endswith("::" + suffix)]
            assert hits, f"no component ::{suffix} in fixture graph"
            for c in hits:
                fn(c)
        self.doctor(apply)


class TestComponentRouting(_WebshopBase):
    def test_single_component_diff_routes_component_lenses(self):
        # Design acceptance row 4: a renderer-only diff routes the renderer
        # component's lenses (frontend/design deep), NOT the whole-module
        # route, and meta names the contributing component per routed lens.
        r = self.route(RENDER_DIFF)
        self.assertTrue(r["context"]["component_route"])
        self.assertEqual(r["context"]["components"],
                         ["shop/webapp::renderer"])
        for lid in ("frontend", "design", "accessibility"):
            x = _entry(r, lid)
            self.assertEqual(x["tier"], "deep", lid)
            self.assertEqual(x["component_attribution"],
                             ["shop/webapp::renderer"], lid)
        # dbio's lens does NOT ride along on a renderer diff
        self.assertEqual(_entry(r, "dba")["tier"], "n/a")
        # routed set == the component's cached proposals disposed on the
        # live diff, plus floors — every routed lens is either attributed
        # to the renderer component or floored.
        renderer = next(c for c in self.graph["components"]
                        if c["id"] == "shop/webapp::renderer")
        proposals = {lid for lid, e in renderer["lens_map"].items()
                     if e["verdict"] in ("deep", "light")}
        for x in r["lenses"]:
            if x["tier"] == "n/a":
                continue
            if "component_attribution" in x:
                self.assertIn(x["id"], proposals)
                self.assertEqual(x["component_attribution"],
                                 ["shop/webapp::renderer"])
            else:
                self.assertIn("floor", x, x["id"])
        # context carries the full routed-lens attribution map
        attr = r["context"]["component_attribution"]
        self.assertEqual(set(attr),
                         {x["id"] for x in r["lenses"]
                          if "component_attribution" in x})
        # ALL catalog lenses stay visible; every n/a is evidenced
        cat_ids = {l["id"] for l in lens.load_catalog()["lenses"]}
        self.assertEqual({x["id"] for x in r["lenses"]}, cat_ids)
        for x in r["lenses"]:
            if x["tier"] == "n/a":
                self.assertTrue(x["negative_evidence"], x["id"])

    def test_multi_component_diff_unions_and_attributes(self):
        # A diff spanning dbio+gateway assembles the UNION of both cached
        # maps; a lens both propose is attributed to both components.
        files = sorted(f for c in self.graph["components"]
                       if c["id"] in ("shop/webapp::dbio",
                                      "shop/webapp::gateway")
                       for f in c["files"])
        r = self.route(files)
        self.assertEqual(r["context"]["components"],
                         ["shop/webapp::dbio", "shop/webapp::gateway"])
        self.assertEqual(_entry(r, "backend")["component_attribution"],
                         ["shop/webapp::dbio", "shop/webapp::gateway"])
        self.assertEqual(_entry(r, "dba")["component_attribution"],
                         ["shop/webapp::dbio"])
        self.assertEqual(_entry(r, "security")["component_attribution"],
                         ["shop/webapp::gateway"])
        # the renderer's lenses do not ride along
        self.assertEqual(_entry(r, "frontend")["tier"], "n/a")

    def test_cache_proposes_live_disposes(self):
        # NEVER trust cached maps for final verdicts: a bogus cached deep
        # proposal (dba on the renderer) must be discarded by the live diff
        # signals — the cache proposes, the live signals dispose.
        self.doctor_component("renderer", lambda c: c["lens_map"].update(
            {"dba": {"verdict": "deep", "score": 0.9,
                     "evidence": ["poisoned cache"]}}))
        r = self.route(RENDER_DIFF)
        self.assertTrue(r["context"]["component_route"])
        x = _entry(r, "dba")
        self.assertEqual(x["tier"], "n/a")
        self.assertTrue(x["negative_evidence"])
        self.assertNotIn("poisoned cache", " ".join(x.get("evidence", [])))

    def test_forced_lens_runs_despite_component_na(self):
        # --lens force applies POST-assembly: dba is neither proposed by
        # the renderer nor live-evidenced, yet the force runs it deep.
        r = self.route(RENDER_DIFF, only=["dba"])
        self.assertTrue(r["context"]["component_route"])
        x = _entry(r, "dba")
        self.assertEqual(x["tier"], "deep")
        self.assertEqual(x["verdict"], "deep (forced)")
        for other in r["lenses"]:
            if other["id"] != "dba":
                self.assertEqual(other["tier"], "n/a")
                self.assertTrue(other["negative_evidence"])


class TestComponentGuardrails(_WebshopBase):
    def test_security_floor_runs_on_live_diff_never_from_cache(self):
        # Enforcement diff in one component cannot route security n/a even
        # when the CACHED map says n/a — floors run AFTER assembly on the
        # REAL diff ctx (an auth-ish file is in the diff).
        self.doctor_component("gateway", lambda c: c["lens_map"].update(
            {"security": {"verdict": "n/a", "score": 0.0, "evidence": []}}))
        r = self.route(["shop/webapp/gateway/login_auth.py"])
        self.assertTrue(r["context"]["component_route"])
        sec = _entry(r, "security")
        self.assertIn(sec["tier"], ("light", "deep"))
        self.assertIn("floor", sec)
        self.assertIn("auth-ish surface touched",
                      " ".join(sec["evidence"]))

    def test_architecture_floor_survives_component_assembly(self):
        # architecture >= light on ANY code change, even when the cached
        # component map is doctored to not propose it.
        self.doctor_component("core", lambda c: c["lens_map"].update(
            {"architecture": {"verdict": "n/a", "score": 0.0,
                              "evidence": []}}))
        r = self.route(["shop/webapp/util_a.py"])
        self.assertTrue(r["context"]["component_route"])
        arch = _entry(r, "architecture")
        self.assertIn(arch["tier"], ("light", "deep"))
        self.assertIn("floor", arch)

    def test_cap8_demote_never_drop_after_assembly(self):
        # The budget runs AFTER assembly on the union: never more than the
        # hard cap deep; overflow is demoted to light with the demotion
        # recorded — never dropped.
        allfiles = sorted(f for c in self.graph["components"]
                          for f in c["files"])
        r = self.route(allfiles)
        self.assertTrue(r["context"]["component_route"])
        deep = [x for x in r["lenses"] if x["tier"] == "deep"]
        self.assertLessEqual(len(deep), lens_signals.DEEP_CAP)
        # exercise the demotion path through the SAME assembly seam with a
        # tightened cap (test-scoped; restored below)
        orig = lens_signals.DEEP_CAP
        lens_signals.DEEP_CAP = 3
        try:
            r2 = self.route(allfiles)
        finally:
            lens_signals.DEEP_CAP = orig
        self.assertTrue(r2["context"]["component_route"])
        deep2 = [x for x in r2["lenses"] if x["tier"] == "deep"]
        self.assertEqual(len(deep2), 3)
        demoted = [x for x in r2["lenses"]
                   if any("budget: demoted" in e for e in x["evidence"])]
        self.assertTrue(demoted)
        for x in demoted:
            self.assertEqual(x["tier"], "light")   # demoted, never dropped
        cat_ids = {l["id"] for l in lens.load_catalog()["lenses"]}
        self.assertEqual({x["id"] for x in r2["lenses"]}, cat_ids)

    def test_unevidenced_na_still_refused(self):
        # The existing rule holds at component granularity: an n/a without
        # negative evidence anywhere in the output is a contract breach.
        r = self.route(RENDER_DIFF)
        for x in r["lenses"]:
            if x["tier"] == "n/a":
                self.assertTrue(x["negative_evidence"],
                                f"{x['id']}: unevidenced n/a")


class TestComponentFailOpen(_WebshopBase):
    def test_fail_open_superset(self):
        # Design acceptance row 6, the STRUCTURAL SUPERSET GUARANTEE:
        # fallback NEVER NARROWS — each ladder rung's routed (deep+light)
        # coverage is a superset-or-equal of the more precise rung.
        # Doctor the cached map so the component route is a STRICT subset
        # (frontend narrowed away), then break the layer: the module rung
        # must restore frontend and cover everything the component rung
        # routed, with the failure traced.
        self.doctor_component("renderer", lambda c: c["lens_map"].update(
            {"frontend": {"verdict": "n/a", "score": 0.0, "evidence": []}}))
        r_comp = self.route(RENDER_DIFF)
        self.assertTrue(r_comp["context"]["component_route"])
        fe = _entry(r_comp, "frontend")
        self.assertEqual(fe["tier"], "n/a")   # narrowed by assembly...
        self.assertIn("component assembly",
                      " ".join(fe["negative_evidence"]))
        # ...now corrupt the layer -> module rung
        self.doctor(lambda raw: raw.__setitem__("components",
                                                {"corrupt": True}))
        r_mod = self.route(RENDER_DIFF)
        self.assertNotIn("component_route", r_mod["context"])
        self.assertIn("component_layer_failed", r_mod["context"])
        # superset-or-equal — here STRICT: the module rung widened
        self.assertLess(_routed(r_comp), _routed(r_mod))
        self.assertEqual(_entry(r_mod, "frontend")["tier"], "deep")
        # the failure is traced, never silent
        traces = _layer_traces(self.ws)
        self.assertTrue(traces)
        self.assertIn("corrupt", traces[-1]["error"])
        # removing the layer entirely also only widens (and is untraced —
        # an undecomposed graph is a legitimate state, not a failure)
        n_traces = len(traces)
        self.doctor(lambda raw: raw.pop("components"))
        r_absent = self.route(RENDER_DIFF)
        self.assertTrue(_routed(r_comp) <= _routed(r_absent))
        self.assertEqual(len(_layer_traces(self.ws)), n_traces)

    def test_unmapped_changed_file_widens_to_module_route(self):
        newf = os.path.join(self.ws, "shop/webapp/newfile.py")
        with open(newf, "w") as f:
            f.write("x = 1\n")
        r = self.route(["shop/webapp/newfile.py"])
        self.assertNotIn("component_route", r["context"])
        self.assertIn("maps to no component",
                      r["context"]["component_layer_failed"])
        self.assertIn("maps to no component",
                      _layer_traces(self.ws)[-1]["error"])

    def test_stale_fingerprint_widens_to_module_route(self):
        with open(os.path.join(self.ws, RENDER_DIFF[0]), "a") as f:
            f.write("// edited after the scan\n")
        r = self.route(RENDER_DIFF)
        self.assertNotIn("component_route", r["context"])
        self.assertIn("stale fingerprint",
                      r["context"]["component_layer_failed"])
        self.assertIn("stale fingerprint",
                      _layer_traces(self.ws)[-1]["error"])

    def test_degraded_component_widens_to_module_route(self):
        self.doctor_component("renderer",
                              lambda c: c.update({"degraded": True}))
        r = self.route(RENDER_DIFF)
        self.assertNotIn("component_route", r["context"])
        self.assertIn("degraded", r["context"]["component_layer_failed"])

    def test_absent_layer_is_byte_identical_phase1(self):
        # The component path engages ONLY when the components key exists:
        # without it, the module-level route v2 output is byte-identical —
        # no component keys anywhere in the routing OR dispatch payload.
        self.doctor(lambda raw: raw.pop("components"))
        r = self.route(RENDER_DIFF)
        blob = json.dumps(r)
        for marker in ("component_route", "component_attribution",
                       "component_layer_failed"):
            self.assertNotIn(marker, blob)
        d = lens.dispatch_briefs(r, base="HEAD")
        self.assertNotIn("component_attribution", json.dumps(d))


class TestComponentAttributionOnBriefs(_WebshopBase):
    def test_briefs_and_routing_decision_carry_attribution(self):
        # contract:lens-brief ADDITIVE key: deep briefs and the
        # routing_decision entries name the contributing component(s) —
        # this is how meta.component_attribution reaches findings meta.
        r = self.route(RENDER_DIFF)
        d = lens.dispatch_briefs(r, base="HEAD")
        self.assertTrue(d["deep"])
        for b in d["deep"]:
            self.assertEqual(b["component_attribution"],
                             ["shop/webapp::renderer"], b["id"])
            # the rest of the brief contract is unchanged
            self.assertEqual(b["task_slot"], f"lens-{b['id']}")
            self.assertTrue(b["contract"]["read_only"])
        decision = d["routing_decision"]
        for lid, entry in decision.items():
            if str(entry.get("verdict", "n/a")).startswith(("deep",
                                                            "light")):
                if "component_attribution" in entry:
                    self.assertEqual(entry["component_attribution"],
                                     ["shop/webapp::renderer"], lid)
            else:
                self.assertNotIn("component_attribution", entry, lid)
        # at least the component's headline lenses are attributed
        self.assertIn("component_attribution", decision["frontend"])


class TestComponentLayerRendering(_WebshopBase):
    def test_dashboard_renders_component_layer(self):
        # Design acceptance row 5: component nodes + their edges reach the
        # rendered graph HTML (distinct visual class), and the review-graph
        # panel names the layer; HEADLINE/coverage FORMATS are pinned
        # unchanged by test_dashboard_coverage_v2 (legacy path untouched).
        out = dg.to_html(self.ws, ["shop/webapp/renderer/screen.tsx"])
        with open(out) as f:
            html = f.read()
        for cid in ("shop/webapp::renderer", "shop/webapp::dbio",
                    "shop/webapp::gateway", "shop/webapp::core"):
            self.assertIn(cid, html)
        self.assertIn("compnode", html)          # distinct visual class
        self.assertIn(">component</span>", html.replace("</i>", ">")
                      .replace('<i class="dot" style="background:#3aa76d">',
                               ""))
        self.assertIn('"components":', html)     # component edges ride the
        self.assertIn('"deps":', html)           # embedded layer data
        self.assertEqual(html.count("</script>"), 1)   # no new script seam
        panel = dashboard.render_review_graph(self.ws)
        self.assertIn("components (decomposed)", panel)

    def test_undecomposed_graph_html_has_no_component_layer(self):
        self.doctor(lambda raw: raw.pop("components"))
        out = dg.to_html(self.ws, ["shop/webapp/renderer/screen.tsx"])
        with open(out) as f:
            html = f.read()
        self.assertNotIn('"components":', html)
        self.assertNotIn("decomposed component node", html)
        panel = dashboard.render_review_graph(self.ws)
        self.assertNotIn("components (decomposed)", panel)

    def test_coverage_map_shows_component_attribution(self):
        # Additive on the coverage map: an entry carrying
        # component_attribution renders the contributing component; the
        # same map without the key renders byte-identically to before.
        base = {"frontend": {"verdict": "deep", "score": 0.9,
                             "evidence": ["content: 2 component files"]}}
        with_attr = json.loads(json.dumps(base))
        with_attr["frontend"]["component_attribution"] = \
            ["shop/webapp::renderer"]
        h_plain = dashboard.render_lens_coverage(base)
        h_attr = dashboard.render_lens_coverage(with_attr)
        self.assertIn("shop/webapp::renderer", h_attr)
        self.assertIn("via shop/webapp::renderer", h_attr)
        self.assertNotIn("shop/webapp::renderer", h_plain)
        # legacy byte-identity: rendering the plain map is unaffected by
        # the attribution feature existing at all
        self.assertEqual(h_plain,
                         dashboard.render_lens_coverage(
                             json.loads(json.dumps(base))))


class TestPhase2EmFixes(_WebshopBase):
    """Phase 2 EM review fixes — each TIGHTENS; regression tests pin them."""

    def test_scan_hash_refuses_traversal_and_symlink_escape(self):
        # HIGH (em fix): _scan_hash reads paths supplied by graph.json's
        # components layer — repo data. Escapes must return None (-> stale
        # -> the wider route), exactly like decompose._read_text.
        outside = os.path.join(self.tmp, "secret.txt")
        open(outside, "w").write("SECRET")
        self.assertIsNone(lens._scan_hash(self.ws, "../secret.txt"))
        self.assertIsNone(lens._scan_hash(self.ws, outside))
        os.symlink(outside, os.path.join(self.ws, "link.txt"))
        self.assertIsNone(lens._scan_hash(self.ws, "link.txt"))
        # a normal contained read still hashes
        rel = sorted(f for f in self.graph["files"])[0]
        self.assertIsNotNone(lens._scan_hash(self.ws, rel))

    def test_crafted_components_path_widens_not_reads(self):
        # end-to-end: a doctored components file entry pointing outside the
        # workspace makes the layer STALE (widens) instead of reading the
        # foreign file.
        outside = os.path.join(self.tmp, "host_file")
        open(outside, "w").write("x")

        def poison(raw):
            raw["components"][0]["files"].append("../host_file")
        self.doctor(poison)
        r = self.route(["shop/webapp/render/screen.tsx"])
        # the ladder widened (module or breadth route) — never crashed,
        # never treated the escape as a fresh component member
        self.assertFalse(r["context"].get("component_route", False))

    def test_lens_map_cache_invalidated_by_graph_drift(self):
        # MED (em fix): a cached lens_map bakes in _graph_payload's hub/
        # boundary flags; graph drift (module becomes a hub) must
        # invalidate BOTH cache levels — the every-rung-only-widens
        # guarantee forbids a stale map narrowing.
        g1 = dg.load(self.ws)
        comps1 = [c for c in g1["components"]
                  if c["module"] == "shop/webapp"]
        self.assertTrue(all(c.get("graph_sig") for c in comps1))
        # drift: add dependents making shop/webapp a hub
        for i in range(4):
            dg.record_edge(self.ws, f"consumer{i}", "shop/webapp",
                           kind="imports", confidence="high")
        g2 = dg.scan(self.ws, decompose=True)
        comps2 = [c for c in g2["components"]
                  if c["module"] == "shop/webapp"]
        s1 = {c["id"]: c["graph_sig"] for c in comps1}
        s2 = {c["id"]: c["graph_sig"] for c in comps2}
        self.assertNotEqual(s1, s2,
                            "graph drift must change the graph signature")
        # unchanged files + changed graph -> lens maps recomputed, not
        # cache-reused: replay derive() with the PRE-drift graph as prev and
        # assert the drifted module took the recompute path (derive stats
        # are not persisted in graph meta, so probe the API directly)
        comps, stats = dc.derive(self.ws, g2, prev=g1)
        self.assertGreater(stats["recomputed"], 0,
                           "stale lens_map must not be reused after drift")
        self.assertEqual(stats["modules_skipped"], 0,
                         "module-level skip must not fire across drift "
                         "for the drifted module set")


# ==========================================================================
# t5 / B3 (R-0008 design row 3) — symbol-less big-file decomposition honesty.
#
# A >=BIG_FILE_LINES Python file with NO top-level symbols earned no cluster
# and left no residual symbol, so it vanished from the derivation: the module
# rendered as a `::core` with an EMPTY file span, every diff touching it hit
# "changed file maps to no component", and the component layer permanently
# disengaged for that module behind a remedy (re-scan) that reproduced the
# same empty core. Such a file now joins `::core` as a WHOLE-FILE member
# (file, hash, '') — the honest fold.
# ==========================================================================


class TestB3SymbollessBigFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _symbolless_bigfile_ws(self.tmp)
        self.rel = "dataapp/gen/table.py"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _hashes(self, g):
        return {rel: (row or {}).get("hash", "")
                for rel, row in (g.get("files") or {}).items()}

    def test_fixture_really_trips_the_big_file_floor_with_no_symbols(self):
        src = open(os.path.join(self.ws, self.rel)).read()
        self.assertGreaterEqual(src.count("\n"), dc.BIG_FILE_LINES)
        floors, _e = dc.load_floors(self.ws)
        clusters, residual, tops, _tree, _folded = dc._symbol_clusters(
            src, self.rel, floors)
        self.assertEqual(clusters, {})
        self.assertEqual(residual, [])
        self.assertEqual(tops, {})

    def test_whole_file_member_joins_core(self):
        g = dg.scan(self.ws)
        hashes = self._hashes(g)
        floors, _e = dc.load_floors(self.ws)
        comps, _folded = dc._derive_module(
            self.ws, "dataapp/gen", [self.rel], hashes, floors,
            dc._repo_stems(g))
        self.assertEqual([c["id"] for c in comps], ["dataapp/gen::core"])
        core = comps[0]
        # the (file, hash, '') whole-file member — the B3 fold
        self.assertEqual(core["_members"],
                         [(self.rel, hashes[self.rel], "")])
        self.assertEqual(core["files"], [self.rel])
        self.assertEqual(core["symbols"], [])

    def test_derived_core_is_not_empty(self):
        g = dg.scan(self.ws)
        comps, _stats = dc.derive(self.ws, g)
        core = _by_id(comps)["dataapp/gen::core"]
        self.assertEqual(core["files"], [self.rel])
        self.assertTrue(core["lens_map"])
        self.assertNotIn("degraded", core)

    def test_component_shape_unchanged(self):
        g = dg.scan(self.ws)
        comps, _stats = dc.derive(self.ws, g)
        for c in comps:
            self.assertTrue(CONTRACT_FIELDS.issubset(set(c)), c["id"])

    def test_no_component_anywhere_has_an_empty_span(self):
        """No fixture in the suite may yield a component with an empty
        span — an empty ::core is exactly the dishonesty B3 closes."""
        roots = [_miniapp(self.tmp), _bigfile_ws(self.tmp), self.ws,
                 os.path.join(self.tmp, "webshop")]
        shutil.copytree(os.path.join(FIXTURES, "webshop"), roots[-1])
        empty = []
        for ws in roots:
            g = dg.scan(ws)
            comps, _stats = dc.derive(ws, g)
            for c in comps:
                if not c["files"] and not c["symbols"]:
                    empty.append((ws, c["id"]))
        self.assertEqual(empty, [], "components with an empty span: %s"
                                    % empty)

    def test_layer_engages_and_component_routes_the_diff(self):
        g = dg.scan(self.ws, decompose=True)
        self.assertEqual([c["id"] for c in g["components"]],
                         ["dataapp/gen::core"])
        r = lens.route([self.rel], stage="review", workspace=self.ws)
        self.assertTrue(r["context"]["component_route"],
                        "the layer must engage, not widen on "
                        "'maps to no component'")
        self.assertEqual(r["context"]["components"], ["dataapp/gen::core"])
        self.assertEqual(_layer_traces(self.ws), [])

    def test_core_routed_set_is_a_superset_of_the_files_own_signals(self):
        dg.scan(self.ws, decompose=True)
        r = lens.route([self.rel], stage="review", workspace=self.ws)
        routed = {x["id"] for x in r["lenses"] if x["tier"] != "n/a"}
        own = lens_signals.route_verdicts(self.ws, [self.rel],
                                          stage="review")
        own_set = {lid for lid, v in own.items() if v["verdict"] != "n/a"}
        self.assertTrue(own_set)
        self.assertTrue(own_set.issubset(routed),
                        "component route narrowed the folded file's own "
                        "signals: missing %s" % sorted(own_set - routed))


# ==========================================================================
# t5 / E4 (R-0011 design row 4) — decompose.py doc/behavior truth. The
# docstrings claimed "unknown keys are ignored" (the real rule: unsupported
# LINE SHAPES raise ValueError; the whole file then fails OPEN to the
# defaults with the error reported) and a 2-tuple `_symbol_clusters` return
# (really a 5-tuple). Behavior does not move; the docs are corrected and
# pinned here so doc/behavior drift is unreachable.
# ==========================================================================


class TestE4DocstringTruth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unsupported_line_shape_raises_value_error(self):
        with self.assertRaises(ValueError) as cm:
            dc._parse_components_yaml("floors:\n  - candidate_min_files: 8\n")
        self.assertIn("unsupported components.yaml line", str(cm.exception))

    def test_unknown_keys_inside_supported_shapes_are_ignored(self):
        self.assertEqual(
            dc._parse_components_yaml("floors:\n  bogus_key: 3\n"
                                      "  cluster_min_files: 5\n"),
            {"cluster_min_files": 5})
        self.assertEqual(
            dc._parse_components_yaml("other:\n  cluster_min_files: 5\n"), {})

    def test_whole_file_fails_open_to_defaults_and_is_reported(self):
        ws = _miniapp(self.tmp)
        with open(os.path.join(ws, "components.yaml"), "w") as f:
            f.write("floors:\n  - candidate_min_files: 8\n")
        floors, err = dc.load_floors(ws)
        self.assertEqual(floors["candidate_min_files"],
                         dc.CANDIDATE_MIN_FILES)
        self.assertIsNotNone(err)
        self.assertIn("ignored (defaults used)", err)

    def test_symbol_clusters_returns_the_documented_five_tuple(self):
        ws = _bigfile_ws(self.tmp)
        rel = "bigapp/gen/huge.py"
        floors, _e = dc.load_floors(ws)
        text = open(os.path.join(ws, rel)).read()
        out = dc._symbol_clusters(text, rel, floors)
        self.assertEqual(len(out), 5)
        clusters, residual, tops, tree, folded = out
        self.assertIsInstance(clusters, dict)
        self.assertTrue(all(isinstance(v, list) for v in clusters.values()))
        self.assertIsInstance(residual, list)
        self.assertIsInstance(tops, dict)
        self.assertIsInstance(tree, ast.Module)
        self.assertIsInstance(folded, int)
        self.assertEqual(residual, sorted(residual))

    def test_module_docstring_states_the_real_yaml_behavior(self):
        doc = dc.__doc__
        self.assertIn("unsupported line SHAPE raises ValueError", doc)
        self.assertIn("fails OPEN to the defaults", doc)
        self.assertNotIn("unknown keys\nare ignored", doc)

    def test_symbol_clusters_docstring_states_the_real_return_shape(self):
        doc = dc._symbol_clusters.__doc__
        self.assertIn("5-tuple", doc)
        for part in ("clusters", "residual", "tops", "tree", "folded"):
            self.assertIn(part, doc)
        self.assertNotIn("Returns (clusters {name: [symbol nodes]}, "
                         "residual [nodes])", doc)


if __name__ == "__main__":
    unittest.main()
