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
  * graph_decompose trace {components, recomputed, cache_hits, floor_folded,
    error?}
"""
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decompose as dc  # noqa: E402
import depgraph as dg  # noqa: E402
import graph_primitives as lens_signals
import storage as tpl  # noqa: E402

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
    with open(os.path.join(d, "huge.py"), "w", encoding="utf-8") as f:
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
    with open(os.path.join(d, "table.py"), "w", encoding="utf-8") as f:
        f.write(src)
    return ws


def _by_id(components):
    return {c["id"]: c for c in components}


def _decompose_traces(ws):
    p = os.path.join(tpl.tp_dir(ws), "trace.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        recs = [json.loads(line) for line in f if line.strip()]
    return [r for r in recs if r.get("event") == "graph_decompose"]


def _opaque_trace_field(row, field):
    """Read one intentionally unapproved diagnostic key after audit sealing."""
    key = "field:" + hashlib.sha256(field.encode()).hexdigest()[:20]
    if field in row:
        raise AssertionError(f"audit trace reopened clear field {field!r}")
    return row[key]


def _assert_minimized_trace_value(case, actual, clear_value):
    """Prove the private diagnostic is hidden but content-bound."""
    encoded = json.dumps(clear_value, sort_keys=True, default=str,
                         separators=(",", ":")).encode("utf-8", "replace")
    case.assertEqual(actual, {
        "schema": "taskplane.audit-minimized/v1",
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    })


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
        with open(os.path.join(ws, "components.yaml"), "w", encoding="utf-8") as f:
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
        with open(os.path.join(ws, "components.yaml"), "w", encoding="utf-8") as f:
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
        with open(os.path.join(ws, "components.yaml"), "w", encoding="utf-8") as f:
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
        with open(os.path.join(ws, "components.yaml"), "w", encoding="utf-8") as f:
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
        with open(os.path.join(ws, "components.yaml"), "w", encoding="utf-8") as f:
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
        with open(os.path.join(ws, "components.yaml"), "w", encoding="utf-8") as f:
            f.write("floors:\n  cluster_min_files: 0\n")
        clamped_hash = dc.floors_hash(ws)
        with open(os.path.join(ws, "components.yaml"), "w", encoding="utf-8") as f:
            f.write("floors:\n  cluster_min_files: 1\n")
        self.assertEqual(dc.floors_hash(ws), clamped_hash)

    def test_derive_carries_the_degraded_floor_marker_in_stats(self):
        """The marker rides derive()'s error channel (stats['error']) —
        the same channel depgraph forwards into the graph_decompose
        trace — and the scan still completes on the clamped floors."""
        ws = _miniapp(self.tmp)
        with open(os.path.join(ws, "components.yaml"), "w", encoding="utf-8") as f:
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
        values = {key: _opaque_trace_field(last, key) for key in
                  ("components", "recomputed", "cache_hits", "floor_folded")}
        self.assertEqual(values["recomputed"], 0)
        self.assertEqual(values["cache_hits"], values["components"])
        self.assertGreater(values["components"], 0)

        corrupt = json.loads(after)
        corrupt["meta"]["source_coverage"]["fingerprint"] = "0" * 64
        with open(p, "w", encoding="utf-8") as stream:
            json.dump(corrupt, stream)
        repaired = dg.scan(ws, decompose=True)
        dg.require_complete_source_coverage(repaired["meta"]["source_coverage"])
        with mock.patch.dict(dg._SCAN_COVERAGE_LIMITS, max_elapsed_ms=0):
            limited = dg.scan(ws, decompose=True)
        self.assertEqual(limited["meta"]["source_coverage"]["status"], "partial")
        self.assertIn("time", {row["reason"] for row in
            limited["meta"]["source_coverage"]["stopping_conditions"]})

    def test_single_component_recompute(self):
        ws = _miniapp(self.tmp)
        g1 = dg.scan(ws, decompose=True)
        before = _by_id(g1["components"])
        with open(os.path.join(ws, "engine/mod/views/list.py"), "a", encoding="utf-8") as f:
            f.write("\n# touched\n")
        g2 = dg.scan(ws, decompose=True)
        after = _by_id(g2["components"])
        traces = _decompose_traces(ws)
        self.assertEqual(_opaque_trace_field(traces[-1], "recomputed"), 1)
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
        self.assertIn("bigapp/gen", g["modules"])
        self.assertNotIn("components", g)
        self.assertEqual(g["meta"]["source_coverage"]["status"], "partial")
        with self.assertRaisesRegex(ValueError, "partial"):
            dg.require_complete_source_coverage(g["meta"]["source_coverage"])
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
        with open(dg._path(ws), encoding="utf-8") as f:
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
                           capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
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
        src = open(os.path.join(self.ws, self.rel), encoding="utf-8").read()
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
        with open(os.path.join(ws, "components.yaml"), "w", encoding="utf-8") as f:
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
        text = open(os.path.join(ws, rel), encoding="utf-8").read()
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
