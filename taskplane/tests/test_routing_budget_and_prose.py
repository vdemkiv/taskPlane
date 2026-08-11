"""Two routing defects the review found (D-0005, D-0006).

D-0005 — THE DEEP CAP WAS ABSENT FROM THE ONE REVIEW THAT NEEDED IT.
`lens_signals.apply_budget` caps the deep set at 8 and demotes the overflow.
`route()` disables route v2 when `breadth="all"` — and `--all` is what a
whole-codebase review runs. The legacy path had no budget at all, and on a
large diff EVERY routed lens sees `large` and becomes its own subagent. The
most expensive review the product performs was the only one with no
spending limit: 26 deep agents under a cap of 8.

D-0006 — DETECTORS FIRED ON THIS REPO'S OWN DOCUMENTATION.
A content regex is a proxy for "this code DOES x". Run it over prose and it
becomes a proxy for "this document MENTIONS x". Editing five of this repo's
documentation files fired seventeen lenses: `dba` went DEEP because
`docs/routing-and-flows.md` explains query patterns, and `data-safety` fired
on `lenses/privacy-compliance.md` — a lens DEFINITION, a file whose entire
job is to describe migration markers so a reviewer can spot them.

The fix for each is bounded by its own complement, and those matter more
than the headline: a budget that dropped lenses would be worse than no
budget, and a prose rule that silenced tech-writer would be worse than the
noise. Both complements are pinned below.

Every assertion here was observed FAILING before it was kept.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lens  # noqa: E402
import lens_signals  # noqa: E402


# the REPO root (three levels up from tests/), not taskplane/ — the prose
# fixtures below are real files in this repository and the detectors read
# them off disk
ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

# A change wide enough that every routed lens sees `large` — the shape a
# whole-codebase review has, and the shape that produced 26 subagents.
WIDE = ([f"src/svc{i}/handler.py" for i in range(12)]
        + [f"web/ui{i}/View.tsx" for i in range(6)]
        + ["db/migrations/001_add.sql", "infra/main.tf",
           ".github/workflows/ci.yml", "docs/guide.md",
           "openapi/spec.yaml", "Dockerfile", "package.json",
           "src/auth/login.py", "src/pay/charge.py"])


class TestD0005DeepDispatchIsBudgeted(unittest.TestCase):
    def _route(self, breadth):
        return lens.route(WIDE, task_type="feature", breadth=breadth)

    def test_all_breadth_no_longer_dispatches_an_unbounded_fan_out(self):
        r = self._route("all")
        deep = [e for e in r["lenses"] if e["mode"] == "subagent"]
        self.assertLessEqual(len(deep), lens_signals.DEEP_CAP,
                             f"{len(deep)} subagents dispatched under a cap "
                             f"of {lens_signals.DEEP_CAP}")

    def test_the_overflow_is_demoted_and_not_dropped(self):
        """A budget that DROPPED lenses would be a coverage regression
        wearing a cost fix's clothes. Everything still runs; the cheap ones
        run inline."""
        r = self._route("all")
        demoted = [e for e in r["lenses"]
                   if e["mode"] == "inline" and e["tier"] == "deep"]
        self.assertTrue(demoted, "the fixture must actually overflow the cap")
        for e in demoted:
            self.assertTrue(any("deep cap" in x for x in e["reasons"]),
                            f"{e['id']} was demoted with no reason recorded")

    def test_the_same_lenses_are_still_selected(self):
        """The cap changes HOW a lens runs, never WHETHER."""
        capped = {e["id"] for e in self._route("all")["lenses"]}
        import copy
        cat = copy.deepcopy(lens.load_catalog())
        self.assertEqual(capped, {l["id"] for l in cat["lenses"]},
                         "breadth=all must still name every catalog lens")

    def test_the_budget_is_reported_with_the_decision(self):
        ctx = self._route("all")["context"]
        self.assertEqual(ctx["deep_cap"], lens_signals.DEEP_CAP)
        self.assertLessEqual(ctx["deep_dispatched"], ctx["deep_cap"])

    def test_an_architecture_full_pass_survives_the_budget(self):
        """Floors run AFTER the budget in lens_signals, and this module
        defines a full design pass as running in its own subagent. The
        budget may not quietly take that away."""
        r = self._route("all")
        arch = [e for e in r["lenses"] if e["id"] == "architecture"]
        self.assertEqual(len(arch), 1)
        if arch[0].get("effort") == "full":
            self.assertEqual(arch[0]["mode"], "subagent")

    def test_a_small_change_is_untouched(self):
        """The complement: under the cap, nothing is demoted and no reason
        text appears."""
        r = lens.route(["src/auth/session.py"], task_type="feature")
        self.assertTrue(all("deep cap" not in x
                            for e in r["lenses"] for x in e["reasons"]))

    def test_one_cap_not_two(self):
        """A local default here would be a second reader of one number —
        the drift shape this codebase already carries elsewhere. When the
        engine that owns the cap cannot be read, no cap is applied AND that
        is visible, rather than a guess that silently disagrees."""
        self.assertEqual(lens._deep_cap(), lens_signals.DEEP_CAP)
        self.assertEqual(lens._cap_deep_dispatch(
            [{"mode": "subagent", "reasons": []}] * 30, 0),
            [{"mode": "subagent", "reasons": []}] * 30)


class TestD0006ProseDescribesItDoesNotDo(unittest.TestCase):
    DOCS = ["docs/lens-catalog.md", "docs/routing-and-flows.md",
            "PRIVACY.md", "lenses/privacy-compliance.md",
            "docs/authority-matrix.md"]

    def _fired(self, files):
        v = lens_signals.route_verdicts(ROOT, files, stage="build")
        return {k: x["verdict"] for k, x in v.items() if x["verdict"] != "n/a"}

    def test_a_docs_only_change_stops_summoning_the_database_lenses(self):
        fired = self._fired(self.DOCS)
        for lid in ("dba", "data-safety", "scalability", "backend",
                    "mobile", "accessibility", "services-selection"):
            self.assertNotIn(lid, fired,
                             f"{lid} fired on documentation that only "
                             "DESCRIBES its subject")

    def test_the_lens_whose_surface_IS_documentation_still_fires(self):
        """The complement, and the reason this is not "never score
        markdown": tech-writer's own globs say `**/*.md`."""
        self.assertEqual(self._fired(self.DOCS).get("tech-writer"), "deep")

    def test_a_path_signal_on_prose_is_untouched(self):
        """Only CONTENT scanning is restricted. A doc a lens's globs claim
        still routes it, and the security floor still fires on an auth-ish
        path."""
        self.assertIn("security", self._fired(self.DOCS))

    def test_the_same_markers_in_CODE_still_fire(self):
        """The defect was the FILE KIND, not the regexes. Real SQL still
        summons the database lenses."""
        fired = self._fired(["db/migrations/001_add_price.sql"])
        self.assertIn("dba", fired)
        self.assertIn("data-safety", fired)

    def test_the_rule_reads_the_lenss_own_surface_not_a_second_list(self):
        """A hand-maintained list of doc-surfaced lenses would drift from
        the catalog the moment someone edited a glob. `_scannable` is built
        from the same globs the path signal uses."""
        import inspect
        src = inspect.getsource(lens_signals._spec_detect)
        self.assertIn("_scannable", src)
        self.assertIn("_glob_hit([rel], globs)", src)

    def test_prose_extensions_are_recognised_by_suffix(self):
        for p in ("a/b.md", "a/b.MD", "notes.rst", "x/y.txt", "z.adoc"):
            self.assertTrue(lens_signals._is_prose(p), p)
        for p in ("a/b.py", "a/b.sql", "a/b.tsx", "readme.json"):
            self.assertFalse(lens_signals._is_prose(p), p)


if __name__ == "__main__":
    unittest.main()
