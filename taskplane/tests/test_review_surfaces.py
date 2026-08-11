"""WS-G — the review's own surfaces.

Every test here exists because a human read a real review and could not
use what came back. The engine was green in all three cases, so the unit
suite as it stood could not have caught any of them.
"""
import base64
import gzip
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard          # noqa: E402
import depgraph           # noqa: E402


class CleanListReadability(unittest.TestCase):
    """'this part is unreadable' — 35 sentences welded with '; '."""

    def _clean(self, n):
        return [f"lens{i}: a check that passed, with a clause; and another"
                for i in range(n)]

    def test_each_check_is_its_own_row_not_one_semicolon_paragraph(self):
        html = dashboard._render_clean(self._clean(4))
        self.assertEqual(html.count("<li"), 4)
        # the old join is what made it unreadable
        self.assertNotIn("clause; and another; lens1", html)

    def test_the_domain_prefix_becomes_a_label_and_leaves_the_sentence(self):
        html = dashboard._render_clean(["security: no secrets in logs"])
        self.assertIn(">security</span>", html)
        self.assertIn("no secrets in logs", html)
        # the label is lifted OUT of the body, not duplicated into it
        self.assertEqual(html.count("security"), 1)

    def test_a_sentence_that_merely_contains_a_colon_is_left_whole(self):
        item = "note that config.yaml: line 12 has the flag"
        html = dashboard._render_clean([item])
        self.assertIn("has the flag", html)
        self.assertNotIn("<span style=\"font-family:var(--font-mono);"
                         "font-size:10.5px;color:var(--text-muted);"
                         "text-transform:uppercase", html)

    def test_the_cap_names_itself_instead_of_lying_about_coverage(self):
        html = dashboard._render_clean(self._clean(35))
        self.assertIn("35 area", html)          # header tells the truth
        self.assertIn("+23 more", html)         # and so does the omission
        self.assertEqual(html.count("<li"), dashboard._CLEAN_SHOWN + 1)

    def test_no_cap_marker_when_nothing_was_dropped(self):
        html = dashboard._render_clean(self._clean(3))
        self.assertNotIn("more clean check", html)

    def test_empty_clean_list_renders_nothing_at_all(self):
        self.assertEqual(dashboard._render_clean([]), "")

    def test_the_summary_page_renders_the_clean_list_as_rows(self):
        """The wiring, not just the helper.

        An earlier version of this test asserted only that "35 area"
        appeared on the page -- which the old semicolon paragraph also
        said, so reverting the fix left it green. It now asserts the
        SHAPE the reader actually complained about.
        """
        findings = [{"severity": "high", "domain": "d", "file": "a.py",
                     "line": 1, "title": f"t{i}", "scenario": "s" * 400,
                     "fix": "f" * 400} for i in range(40)]
        pages = dashboard.render_findings_paged(
            findings, {"title": "r", "clean": self._clean(35)})
        page = pages[0]["html"]
        self.assertIn("35 area", page)
        self.assertEqual(page.count("<li"), dashboard._CLEAN_SHOWN + 1)
        self.assertIn("+23 more", page)
        self.assertNotIn("clause; and another; lens1", page)


class GraphFocus(unittest.TestCase):
    """A 620 KB page is a fine file and an impossible widget."""

    def _graph(self):
        return {
            "modules": {m: {"files": 1, "kind": "module"}
                        for m in ("a", "b", "c", "far")},
            "edges": [
                {"from": "b", "to": "a", "kind": "imports"},
                {"from": "c", "to": "b", "kind": "imports"},
                {"from": "far", "to": "c", "kind": "imports"},
            ],
        }

    def _imp(self):
        return {"touched": ["a"],
                "impacted": {"1": [{"module": "b", "via": "a",
                                    "kind": "imports"}],
                             "2": [{"module": "c", "via": "b",
                                    "kind": "imports"}]},
                "total_impacted": 2}

    def test_depth_one_keeps_the_neighbours_and_drops_the_rest(self):
        g, imp, note = depgraph.focus_graph(self._graph(), self._imp(), 1)
        self.assertEqual(sorted(g["modules"]), ["a", "b"])
        self.assertNotIn("far", g["modules"])
        self.assertEqual(imp["total_impacted"], 1)

    def test_an_edge_survives_only_if_BOTH_endpoints_do(self):
        g, _, _ = depgraph.focus_graph(self._graph(), self._imp(), 1)
        # c->b is dropped even though b is kept: a half-edge would draw a
        # line to a node that is not on the page.
        self.assertEqual(g["edges"],
                         [{"from": "b", "to": "a", "kind": "imports"}])

    def test_depth_two_reaches_further(self):
        g, imp, _ = depgraph.focus_graph(self._graph(), self._imp(), 2)
        self.assertEqual(sorted(g["modules"]), ["a", "b", "c"])
        self.assertEqual(imp["total_impacted"], 2)

    def test_what_was_dropped_is_stated_never_silently_omitted(self):
        _, _, note = depgraph.focus_graph(self._graph(), self._imp(), 1)
        self.assertIn("2/4 modules", note)
        self.assertIn("1/3 edges", note)

    def test_focus_does_not_mutate_the_graph_it_was_given(self):
        g = self._graph()
        depgraph.focus_graph(g, self._imp(), 1)
        self.assertEqual(len(g["modules"]), 4)
        self.assertEqual(len(g["edges"]), 3)


class GraphFragment(unittest.TestCase):
    """The fragment must carry the ENGINE's page, not a lookalike."""

    PAGE = ('<!DOCTYPE html><html><head><style>body{color:red}</style>'
            '</head><body><h1>graph &amp; "map"</h1>'
            '<script>var d={"a":1};</script></body></html>')

    def _unpack(self, frag):
        b64 = re.search(r'atob\("([^"]+)"\)', frag).group(1)
        return gzip.decompress(base64.b64decode(b64)).decode("utf-8")

    def test_the_page_survives_byte_for_byte(self):
        self.assertEqual(self._unpack(depgraph.as_fragment(self.PAGE)),
                         self.PAGE)

    def test_a_fragment_carries_no_document_level_markup_of_its_own(self):
        frag = depgraph.as_fragment(self.PAGE)
        self.assertFalse(frag.lstrip().startswith("<!DOCTYPE"))
        self.assertNotIn("<body", frag.split("atob")[0])

    def test_the_host_page_cannot_be_restyled_by_the_graphs_css(self):
        frag = depgraph.as_fragment(self.PAGE)
        # the payload is opaque base64; `body{color:red}` reaches the host
        # only if someone inlined the stylesheet instead of iframing it
        self.assertNotIn("body{color:red}", frag)
        self.assertIn("<iframe", frag)

    def test_the_iframe_is_sandboxed_to_scripts_only(self):
        frag = depgraph.as_fragment(self.PAGE)
        self.assertIn('sandbox="allow-scripts"', frag)
        self.assertNotIn("allow-same-origin", frag)

    def test_the_element_id_is_derived_from_the_page_so_two_can_coexist(self):
        a = depgraph.as_fragment(self.PAGE)
        b = depgraph.as_fragment(self.PAGE + "<!-- other -->")
        self.assertNotEqual(re.search(r'id="(tpg-\w+)"', a).group(1),
                            re.search(r'id="(tpg-\w+)"', b).group(1))

    def test_the_same_page_yields_the_same_id_so_renders_are_idempotent(self):
        self.assertEqual(re.search(r'id="(tpg-\w+)"',
                                   depgraph.as_fragment(self.PAGE)).group(1),
                         re.search(r'id="(tpg-\w+)"',
                                   depgraph.as_fragment(self.PAGE)).group(1))

    def test_it_is_smaller_than_the_page_it_carries(self):
        big = self.PAGE + ('<div class="module">x</div>' * 4000)
        self.assertLess(len(depgraph.as_fragment(big)), len(big) / 4)

    def test_a_reader_without_scripts_is_told_why_the_graph_is_missing(self):
        self.assertIn("<noscript>", depgraph.as_fragment(self.PAGE))


class GraphHtmlWiring(unittest.TestCase):
    """focus/fragment reach to_html, and the default is unchanged."""

    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="tp-wsg-")
        self.home = tempfile.mkdtemp(prefix="tp-wsg-home-")
        self._env = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = self.home
        depgraph.save(self.ws, {
            "modules": {"a": {"files": 1}, "b": {"files": 1},
                        "far": {"files": 1}},
            "edges": [{"from": "b", "to": "a", "kind": "imports"},
                      {"from": "far", "to": "b", "kind": "imports"}]})

    def tearDown(self):
        if self._env is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._env
        shutil.rmtree(self.ws, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def _read(self, path):
        with io.open(path, encoding="utf-8") as f:
            return f.read()

    def test_the_default_render_is_still_a_whole_document(self):
        out = depgraph.to_html(self.ws, ["a/x.py"],
                               out=os.path.join(self.ws, "g.html"))
        self.assertTrue(self._read(out).startswith("<!DOCTYPE html>"))

    def test_fragment_mode_writes_a_fragment(self):
        out = depgraph.to_html(self.ws, ["a/x.py"], fragment=True,
                               out=os.path.join(self.ws, "f.html"))
        html = self._read(out)
        self.assertFalse(html.startswith("<!DOCTYPE html>"))
        self.assertIn("<iframe", html)

    def test_focus_reaches_the_rendered_subtitle(self):
        out = depgraph.to_html(self.ws, ["a/x.py"], focus=1,
                               out=os.path.join(self.ws, "z.html"))
        self.assertIn("focused to depth 1", self._read(out))

    def test_focus_without_a_change_set_leaves_the_structural_view_whole(self):
        out = depgraph.to_html(self.ws, [], focus=1,
                               out=os.path.join(self.ws, "s.html"))
        html = self._read(out)
        self.assertNotIn("focused to depth", html)
        self.assertIn("far", html)


if __name__ == "__main__":
    unittest.main()
