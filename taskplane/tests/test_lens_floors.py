"""D5 and D6 — the two lens_signals defects the v2.13.0 field review exposed.

A real review of ``aws/karpenter-provider-aws#9464`` came back fully governed
and fingerprinted while getting both of these wrong:

D5 — the qa lens asserted "no test files" about a diff MADE of test files.
    The ``qa`` spec carried no path globs of its own, so a Go diff of
    ``*_test.go`` files earned no path signal, and its construct regex was
    lowercase-only, so Ginkgo's ``It(`` / ``Describe(`` / ``Expect(`` matched
    nothing. +66 lines of tests read as untested. An ``n/a`` that ASSERTS
    there was nothing to check is the coverage-honesty feature inverted.

D6 — the architecture floor promised a full pass and delivered a mention.
    The skill has claimed since v2.11.0 that a structurally significant
    change gets a full architecture pass; ``_apply_floors`` promoted
    ``n/a -> light`` and stopped. On the field diff architecture came back
    ``light`` while carrying ``hub module (12 direct dependents)`` IN ITS OWN
    EVIDENCE, and was swept in one line.

Three traps this file is written to avoid, because a previous attempt's
tests passed with the fixes fully reverted:

  1. The two halves of D5 are isolated. A body containing ``assert`` also
     satisfies the content regex and a ``_test.go`` name also satisfies the
     path globs, so a test using both passes with EITHER half reverted. The
     path tests use an inert body carrying no test construct in any case;
     the content tests use a NON-test filename.
  2. The content half asserts on the EVIDENCE string, not the verdict. ``qa``
     already scores ~0.35 (light) on any code change with no test file
     present, so ``assertNotEqual(verdict, "n/a")`` cannot fail.
  3. The hub threshold is a LITERAL here (``HUB_THRESHOLD``), never read from
     the module. A test that reads the constant moves with it — raising it to
     8000 would leave the suite green while the floor stopped firing on every
     real diff. A separate test pins the module constant TO that literal, so
     changing it is a recorded loosening.
  4. Suffix globs are exercised OUTSIDE the directory globs
     (``ruby/app/widget_spec.rb``, not ``spec/thing_spec.rb``), because
     ``**/spec/**`` would otherwise cover for a deleted ``**/*_spec.rb``.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lens_signals as ls  # noqa: E402

EMPTY_GRAPH = {"hub_dependents": 0, "boundary_contracts": [], "modules": [],
               "module_dependents": {}}

# TRAP 3: the pinned threshold is written HERE as a literal. It is never
# read from lens_signals, or raising the constant would silently move every
# assertion in this file with it.
HUB_THRESHOLD = 8

# A body with NO test construct in ANY case — no assert/expect(/it(/
# describe(/@pytest/unittest. It exists so the path tests measure the PATH
# signal alone (D5 half one).
INERT_GO = "package cache\n\nfunc Size(items []string) int {\n\treturn len(items)\n}\n"
INERT_PY = "def size(items):\n    return len(items)\n"
INERT_RB = "def size(items)\n  items.length\nend\n"
INERT_JAVA = "class Widget {\n    int size() { return 0; }\n}\n"
INERT_CS = "class Widget {\n    int Size() { return 0; }\n}\n"
INERT_TS = "export function size(items: string[]) { return items.length }\n"

# The construct shapes the field diff actually used: Ginkgo/Gomega, which is
# capitalized. Lives in a NON-test filename so it measures the CONTENT
# signal alone (D5 half two).
GINKGO = (
    "package suite\n"
    "\n"
    'var _ = Describe("NodeClaim", func() {\n'
    '\tIt("launches", func() {\n'
    "\t\tExpect(node).To(BeNil())\n"
    "\t})\n"
    "})\n"
)


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p) or root, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return rel


def entry(verdict, score=0.0, evidence=None, negative=None):
    """One verdict-map entry in the engine's own shape."""
    return {"verdict": verdict, "score": score,
            "evidence": list(evidence or []),
            "negative_evidence": list(negative or
                                      (["0 signals: nothing in scope"]
                                       if verdict == "n/a" else []))}


class D5_QaFindsTestFilesByPathAlone(unittest.TestCase):
    """D5, half one. Every body below is INERT — no test construct in any
    case — so the only thing that can produce a `path:` evidence line is the
    qa path globs. Deleting the globs reddens this class; the content regex
    cannot cover for them."""

    def _path_evidence(self, rel, body):
        with tempfile.TemporaryDirectory() as ws:
            write(ws, rel, body)
            ctx = ls.make_ctx(ws, [rel], graph=EMPTY_GRAPH)
            r = ls.detect("qa", ctx)
        return [e for e in r["evidence"] if e.startswith("path:")], r

    def _assert_path_hit(self, rel, body):
        hits, r = self._path_evidence(rel, body)
        self.assertTrue(
            any(e.startswith(f"path: {rel} matches ") for e in hits),
            f"qa saw no path signal for the test file {rel} — "
            f"evidence={r['evidence']} negative={r['negative_evidence']}")
        # the body really was inert: no content signal propped this up
        self.assertFalse([e for e in r["evidence"]
                          if e.startswith("content:")],
                         f"{rel}: the fixture body is not inert — the "
                         f"content regex fired: {r['evidence']}")

    def test_a_go_underscore_test_file_is_a_test_file(self):
        """The field diff's own shape: pkg/**/ *_test.go, outside any
        tests/ directory."""
        self._assert_path_hit("pkg/cache/cache_test.go", INERT_GO)

    def test_a_python_test_prefixed_file_is_a_test_file(self):
        self._assert_path_hit("src/pkg/test_thing.py", INERT_PY)

    def test_a_python_test_suffixed_file_is_a_test_file(self):
        self._assert_path_hit("src/pkg/thing_test.py", INERT_PY)

    def test_a_js_dot_test_file_is_a_test_file(self):
        self._assert_path_hit("web/app/widget.test.ts", INERT_TS)

    def test_a_js_dot_spec_file_is_a_test_file(self):
        self._assert_path_hit("web/app/widget.spec.ts", INERT_TS)

    def test_a_java_Test_suffixed_class_is_a_test_file(self):
        self._assert_path_hit("java/app/WidgetTest.java", INERT_JAVA)

    def test_a_csharp_Tests_suffixed_class_is_a_test_file(self):
        self._assert_path_hit("csharp/app/WidgetTests.cs", INERT_CS)

    def test_a_ruby_underscore_spec_file_is_a_test_file(self):
        """TRAP 4: this lives in ruby/app/, NOT in spec/, so **/spec/** cannot
        cover for a deleted **/*_spec.rb."""
        self._assert_path_hit("ruby/app/widget_spec.rb", INERT_RB)

    def test_the_tests_directory_is_a_test_directory(self):
        self._assert_path_hit("tests/helper.py", INERT_PY)

    def test_the_singular_test_directory_is_a_test_directory(self):
        self._assert_path_hit("test/helper.py", INERT_PY)

    def test_the_spec_directory_is_a_test_directory(self):
        self._assert_path_hit("spec/helper.rb", INERT_RB)


class D5_QaFindsTestConstructsInAnyCase(unittest.TestCase):
    """D5, half two. The filenames here are NOT test paths, so no path glob
    can produce the evidence asserted below — only the construct regex can.

    TRAP 2: these assert on the EVIDENCE STRING, never on the verdict. qa
    already scores W_PATH (0.35 -> light) on any code change carrying no
    test file, via the untested_trigger, so a verdict assertion here would
    pass with the regex fully reverted."""

    LABEL = "content: test constructs in "

    def _content_evidence(self, rel, body):
        with tempfile.TemporaryDirectory() as ws:
            write(ws, rel, body)
            ctx = ls.make_ctx(ws, [rel], graph=EMPTY_GRAPH)
            r = ls.detect("qa", ctx)
        return r

    def _assert_construct_named(self, rel, body):
        r = self._content_evidence(rel, body)
        self.assertIn(f"{self.LABEL}{rel}", r["evidence"],
                      f"qa did not name the test construct it found in "
                      f"{rel}: evidence={r['evidence']}")
        # the filename really was inert: no path glob propped this up
        self.assertFalse(
            [e for e in r["evidence"] if e.startswith("path: ")],
            f"{rel}: the fixture filename is not inert — a qa path glob "
            f"matched it: {r['evidence']}")

    def test_capitalized_ginkgo_constructs_are_test_constructs(self):
        """The exact +66 lines the field review read as untested: Ginkgo's
        Describe( / It( / Expect( are capitalized, and the regex was
        lowercase-only."""
        self._assert_construct_named("pkg/suite.go", GINKGO)

    def test_a_capitalized_assert_is_a_test_construct(self):
        self._assert_construct_named(
            "pkg/checks.go", "func run() {\n\tAssert(ok)\n}\n")

    def test_lowercase_constructs_still_fire(self):
        """The case fix widens; it must not trade one case for the other."""
        self._assert_construct_named(
            "pkg/checks.py", "def run():\n    assert ok\n")


class D5_QaStillProvesAbsenceWhenThereIsNothingToSee(unittest.TestCase):
    """The fix must NOT turn qa into a lens that always fires. A diff with
    genuinely no tests and no code still returns n/a — and carries the
    negative evidence that makes the n/a honest."""

    def test_a_prose_only_diff_is_still_na_with_negative_evidence(self):
        with tempfile.TemporaryDirectory() as ws:
            rel = write(ws, "notes/notes.md", "# release notes\n\nshipped.\n")
            ctx = ls.make_ctx(ws, [rel], graph=EMPTY_GRAPH)
            out = ls.verdicts(["qa"], ctx, floors=False)
        self.assertEqual(out["qa"]["verdict"], "n/a")
        self.assertTrue(out["qa"]["negative_evidence"],
                        "an n/a without negative evidence is the defect, "
                        "not the fix")
        self.assertIn("no test files",
                      " ".join(out["qa"]["negative_evidence"]))


class D6_StructuralSignificanceIsNamed(unittest.TestCase):
    """D6. `_structurally_significant` must RETURN A REASON, not a bool —
    the field diff's architecture verdict already carried
    `hub module (12 direct dependents)` in its evidence and still got swept,
    so the floor has to be able to say what it saw."""

    def _ctx(self, files, graph=None, body="x = 1\n"):
        ws = tempfile.mkdtemp()
        self.addCleanup(_rmtree, ws)
        for rel in files:
            write(ws, rel, body)
        return ls.make_ctx(ws, files, graph=graph or EMPTY_GRAPH)

    def test_a_hub_at_the_threshold_is_structurally_significant(self):
        ctx = self._ctx(["src/util.py"],
                        dict(EMPTY_GRAPH, hub_dependents=HUB_THRESHOLD))
        reason = ls._structurally_significant(ctx)
        self.assertTrue(reason, "a hub module at the threshold must be "
                                "structurally significant")
        self.assertIn("hub module", reason)
        self.assertIn(str(HUB_THRESHOLD), reason)

    def test_one_dependent_below_the_threshold_is_not_significant(self):
        """Pins the threshold from below. Lowering it (to the pre-existing
        _HUB_DEPENDENTS=3 signal, say) reddens here; raising it reddens the
        at-threshold test above."""
        ctx = self._ctx(["src/util.py"],
                        dict(EMPTY_GRAPH, hub_dependents=HUB_THRESHOLD - 1))
        self.assertIsNone(ls._structurally_significant(ctx))

    def test_the_module_constant_equals_the_pinned_threshold(self):
        """TRAP 3. Every other test writes 8 as a literal. This is the one
        place the constant is read, so moving it is a RECORDED loosening —
        one failing test naming the change — instead of a silent one."""
        self.assertEqual(ls.ARCH_HUB_DEPENDENTS, HUB_THRESHOLD)

    def test_a_named_boundary_contract_is_structurally_significant(self):
        ctx = self._ctx(["src/util.py"],
                        dict(EMPTY_GRAPH,
                             boundary_contracts=["contract:lens-brief"]))
        reason = ls._structurally_significant(ctx)
        self.assertTrue(reason)
        self.assertIn("contract:lens-brief", reason)

    def test_a_proto_path_is_structurally_significant(self):
        reason = ls._structurally_significant(self._ctx(["api/order.proto"]))
        self.assertTrue(reason)
        self.assertIn("api/order.proto", reason)

    def test_a_docker_compose_path_is_structurally_significant(self):
        reason = ls._structurally_significant(
            self._ctx(["deploy/docker-compose.yml"]))
        self.assertTrue(reason)
        self.assertIn("docker-compose", reason)

    def test_a_terraform_path_is_structurally_significant(self):
        reason = ls._structurally_significant(self._ctx(["infra/main.tf"]))
        self.assertTrue(reason)
        self.assertIn("infra/main.tf", reason)

    def test_a_k8s_path_is_structurally_significant(self):
        reason = ls._structurally_significant(
            self._ctx(["deploy/k8s/deployment.yaml"]))
        self.assertTrue(reason)
        self.assertIn("k8s", reason)

    def test_a_helm_path_is_structurally_significant(self):
        reason = ls._structurally_significant(
            self._ctx(["charts/helm/values.yaml"]))
        self.assertTrue(reason)
        self.assertIn("helm", reason)

    def test_an_ordinary_code_change_is_not_structurally_significant(self):
        self.assertIsNone(
            ls._structurally_significant(self._ctx(["src/util.py"])))


class D6_ArchitectureFloorsToDeepNotLight(unittest.TestCase):
    """D6, the promise itself: a structurally significant change gets a FULL
    architecture pass. `light` is a mention; the skill promised a pass."""

    def _ctx(self, files, graph=None):
        ws = tempfile.mkdtemp()
        self.addCleanup(_rmtree, ws)
        for rel in files:
            write(ws, rel, "x = 1\n")
        return ls.make_ctx(ws, files, graph=graph or EMPTY_GRAPH)

    def test_a_hub_module_promotes_architecture_all_the_way_to_deep(self):
        ctx = self._ctx(["src/util.py"],
                        dict(EMPTY_GRAPH, hub_dependents=HUB_THRESHOLD))
        out = ls._apply_floors({"architecture": entry("n/a")}, ctx)
        self.assertEqual(out["architecture"]["verdict"], "deep")

    def test_the_field_diffs_light_verdict_is_raised_to_deep(self):
        """The exact field shape: architecture came back `light` while its
        OWN evidence said `hub module (12 direct dependents)`. A floor that
        only fires on n/a never sees it."""
        ctx = self._ctx(["pkg/controllers/nodeclaim.go"],
                        dict(EMPTY_GRAPH, hub_dependents=12))
        out = ls._apply_floors(
            {"architecture": entry("light", 0.35,
                                   ["graph: hub module (12 direct "
                                    "dependents)"])}, ctx)
        self.assertEqual(out["architecture"]["verdict"], "deep")
        self.assertIn("floor", out["architecture"])
        self.assertIn("hub module", out["architecture"]["floor"])

    def test_a_boundary_contract_promotes_architecture_to_deep(self):
        ctx = self._ctx(["src/util.py"],
                        dict(EMPTY_GRAPH,
                             boundary_contracts=["contract:lens-brief"]))
        out = ls._apply_floors({"architecture": entry("n/a")}, ctx)
        self.assertEqual(out["architecture"]["verdict"], "deep")

    def test_a_proto_change_promotes_architecture_to_deep(self):
        ctx = self._ctx(["api/order.proto"])
        out = ls._apply_floors({"architecture": entry("n/a")}, ctx)
        self.assertEqual(out["architecture"]["verdict"], "deep")

    def test_an_ordinary_code_change_still_only_floors_to_light(self):
        """The pre-v2.13.0 floor is kept intact: depth is never manufactured
        on a change that shows no structural signal."""
        ctx = self._ctx(["src/util.py"])
        out = ls._apply_floors({"architecture": entry("n/a")}, ctx)
        self.assertEqual(out["architecture"]["verdict"], "light")

    def test_route_verdicts_gives_the_field_diff_a_full_pass(self):
        """End to end, through the budget: floors run AFTER the budget, so
        the promotion cannot be budgeted away."""
        ws = tempfile.mkdtemp()
        self.addCleanup(_rmtree, ws)
        rels = [write(ws, "pkg/controllers/nodeclaim.go", INERT_GO),
                write(ws, "pkg/controllers/nodeclaim_test.go", GINKGO)]
        out = ls.route_verdicts(ws, rels,
                                graph=dict(EMPTY_GRAPH, hub_dependents=12))
        self.assertEqual(out["architecture"]["verdict"], "deep")
        # ...and D5 end to end on the same diff: a Ginkgo test file is not
        # an untested change.
        self.assertNotEqual(out["qa"]["verdict"], "n/a")
        self.assertTrue(any("nodeclaim_test.go" in e
                            for e in out["qa"]["evidence"]),
                        out["qa"]["evidence"])


class D6_TheFloorNeverLowers(unittest.TestCase):
    """A floor that can DEMOTE is not a floor. The promote helper consults an
    order table BOTH ways; these pin that it never writes a verdict lower
    than the one already there."""

    def _ctx(self, files, graph=None):
        ws = tempfile.mkdtemp()
        self.addCleanup(_rmtree, ws)
        for rel in files:
            write(ws, rel, "x = 1\n")
        return ls.make_ctx(ws, files, graph=graph or EMPTY_GRAPH)

    def test_an_already_deep_architecture_survives_the_light_floor(self):
        """An ordinary code change targets `light`. Applied to a deep
        verdict that is a DEMOTION — the exact way a floor turns into a
        ceiling."""
        ctx = self._ctx(["src/util.py"])
        out = ls._apply_floors(
            {"architecture": entry("deep", 0.9, ["path: src/util.py"])}, ctx)
        self.assertEqual(out["architecture"]["verdict"], "deep")
        self.assertEqual(out["architecture"]["evidence"],
                         ["path: src/util.py"],
                         "a no-op floor must not append evidence either")

    def test_an_already_deep_security_survives_the_security_floor(self):
        ctx = self._ctx(["hooks/pre_tool.py"])
        out = ls._apply_floors(
            {"security": entry("deep", 0.9, ["path: hooks/pre_tool.py"])},
            ctx)
        self.assertEqual(out["security"]["verdict"], "deep")

    def test_an_already_light_architecture_is_not_rewritten_by_the_light_floor(self):
        ctx = self._ctx(["src/util.py"])
        out = ls._apply_floors(
            {"architecture": entry("light", 0.3, ["path: src/util.py"])}, ctx)
        self.assertEqual(out["architecture"]["verdict"], "light")
        self.assertIn("floor", out["architecture"],
                      "the satisfied floor remains an applicability marker")

    def test_applying_the_floors_twice_changes_nothing(self):
        """Idempotence: apply_budget re-runs the floors on maps that may
        already carry them."""
        ctx = self._ctx(["src/util.py"],
                        dict(EMPTY_GRAPH, hub_dependents=HUB_THRESHOLD))
        m = {"architecture": entry("n/a")}
        once = ls._apply_floors(m, ctx)
        first = list(once["architecture"]["evidence"])
        twice = ls._apply_floors(once, ctx)
        self.assertEqual(twice["architecture"]["verdict"], "deep")
        self.assertEqual(twice["architecture"]["evidence"], first)

    def test_an_unrecognized_verdict_is_left_alone(self):
        """`deep (forced)` is a real verdict elsewhere in the engine. A floor
        that does not recognize a word must not overwrite it with a lower
        one — fail toward keeping the claimed depth the engine already
        recorded."""
        ctx = self._ctx(["src/util.py"])
        out = ls._apply_floors(
            {"architecture": entry("deep (forced)", 0.0, ["forced"])}, ctx)
        self.assertEqual(out["architecture"]["verdict"], "deep (forced)")


class D6_TheSecurityFloorIsUnchanged(unittest.TestCase):
    """The D6 rewrite touches the shared promote helper, so the pre-existing
    security floor is re-pinned here rather than assumed."""

    def _ctx(self, files, graph=None):
        ws = tempfile.mkdtemp()
        self.addCleanup(_rmtree, ws)
        for rel in files:
            write(ws, rel, "x = 1\n")
        return ls.make_ctx(ws, files, graph=graph or EMPTY_GRAPH)

    def test_security_is_floored_to_light_on_an_enforcement_diff(self):
        ctx = self._ctx(["hooks/pre_tool.py"])
        out = ls._apply_floors({"security": entry("n/a")}, ctx)
        self.assertEqual(out["security"]["verdict"], "light")
        self.assertIn("floor", out["security"])

    def test_security_is_not_floored_to_deep(self):
        """Only architecture got the deep floor. Widening the security floor
        to deep would be a different, unreviewed change."""
        ctx = self._ctx(["src/auth/login.py"],
                        dict(EMPTY_GRAPH,
                             boundary_contracts=["contract:lens-brief"]))
        out = ls._apply_floors({"security": entry("n/a")}, ctx)
        self.assertEqual(out["security"]["verdict"], "light")

    def test_a_prose_only_diff_floors_nothing(self):
        ctx = self._ctx(["notes/notes.txt"])
        out = ls._apply_floors(
            {"security": entry("n/a"), "architecture": entry("n/a")}, ctx)
        self.assertEqual(out["security"]["verdict"], "n/a")
        self.assertEqual(out["architecture"]["verdict"], "n/a")


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
