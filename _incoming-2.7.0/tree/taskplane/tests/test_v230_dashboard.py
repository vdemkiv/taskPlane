"""v2.3.0 dashboard fix wave — regression tests for the 25 findings.

Highs
  H1  gate buttons must never fake success without a chat bridge (tpFire
      feature-detects sendPrompt FIRST; static view reveals the reply).
  H2  widget_paged honors PAGE_BUDGET — ENFORCED in emitted UTF-8 BYTES,
      wrapper included, split by meaning, nothing dropped silently.
  H3  a malformed/legacy trace record never crashes a render — corrupt
      lines are skipped WITH a visible notice, malformed dicts render as
      degraded-but-visible rows; budget exhaustion is disclosed by the
      dashboard AND the never-skippable headline.

Mediums/lows: i18n message catalog (plurals, sentence templates, ISO-UTC
timestamps, logical CSS + dir, no uppercase transforms), responsive grids,
single trace parse per render with tail-read (the trace is the audit
record — never rotated/deleted by the dashboard), spine per-view DOM ids,
aria states, schema bridging, escaping, entity-safe truncation.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard  # noqa: E402
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402


def _git(ws, *a):
    subprocess.run(["git", *a], cwd=ws, capture_output=True)


def _repo(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "src"))
    open(os.path.join(ws, "src", "a.py"), "w").write("x = 1\n")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "e@e")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    return ws


def _loop_ws(tmp, step="plan_approval", n_tasks=3):
    """A workspace with an active loop, tasks, and trace history."""
    ws = _repo(tmp)
    import requirements as reqs
    rec = reqs.record_requirement(
        ws, "demo feature",
        acceptance=["criterion one", "criterion two", "criterion three"])
    loop.init(ws, "fixture goal", requirement_id=rec["id"])
    os.makedirs(os.path.join(ws, "specs"), exist_ok=True)
    open(os.path.join(ws, "specs", "spec.md"), "w").write("# spec\n")
    loop.next_action(ws)
    loop.gate(ws, "pass", note="spec ok")
    st = loop.load(ws)
    st["tasks"] = [{"id": f"t{i+1}", "scope": [f"src/{i}/**"],
                    "status": "pending"} for i in range(n_tasks)]
    st["tasks"][0]["status"] = "passed"
    st["step"] = step
    loop.save(ws, st)
    for i in range(25):
        tp.trace(ws, "loop_step", step="execute", role="tp-executor")
        tp.trace(ws, "hook_deny", tool="Bash", reason=f"deny {i}")
    return ws


def _bytes(html):
    return len(html.encode("utf-8"))


# --------------------------------------------------------- H2: byte budget

class TestWidgetPagedBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _loop_ws(self.tmp)

    def test_pages_split_and_every_page_fits_in_emitted_bytes(self):
        pages = dashboard.widget_paged(self.ws)
        self.assertGreater(len(pages), 1)
        for p in pages:
            self.assertLessEqual(
                _bytes(p["html"]), dashboard.PAGE_BUDGET,
                f"page {p['title']!r} ships {_bytes(p['html'])} bytes — the "
                "byte budget is ENFORCED, wrapper included")

    def test_single_page_when_it_fits(self):
        pages = dashboard.widget_paged(self.ws, budget=200000)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["html"], dashboard.widget(self.ws))

    def test_pages_are_ordered_by_meaning_and_numbered(self):
        pages = dashboard.widget_paged(self.ws)
        titles = [p["title"] for p in pages]
        self.assertIn("status & gate", titles[0])
        blob = " ".join(titles)
        self.assertLess(blob.index("status"), blob.index("journey"))
        self.assertLess(blob.index("journey"), blob.index("lanes"))
        for t in titles:
            self.assertRegex(t, r"\d+/\d+$")

    def test_governance_carriers_land_on_page_one(self):
        pages = dashboard.widget_paged(self.ws)
        p1 = pages[0]["html"]
        self.assertIn("approve plan", p1)          # the human gate banner
        self.assertIn("tpFire", p1)                # actionable, not inert
        self.assertIn("sr-only", p1)

    def test_nothing_dropped_silently(self):
        pages = dashboard.widget_paged(self.ws)
        blob = " ".join(p["html"] for p in pages)
        for probe in ("t1", "t2", "t3", "criterion one", "live feed",
                      "step journey"):
            self.assertIn(probe, blob)
        # if the byte-fitter had to trim a page, the omission is explicit
        for p in pages:
            if "truncated to honor the page budget" in p["html"]:
                self.assertRegex(p["html"], r"\+\d+ more")

    def test_findings_pages_fit_in_bytes_even_with_multibyte_text(self):
        findings = [{"severity": "high", "domain": "i18n",
                     "title": f"finding {i} — ünïcödé ⚠ ",
                     "scenario": "é" * 200, "fix": "→" * 100}
                    for i in range(60)]
        pages = dashboard.render_findings_paged(findings, {"title": "big"})
        self.assertGreater(len(pages), 1)
        for p in pages:
            self.assertLessEqual(_bytes(p["html"]), dashboard.PAGE_BUDGET,
                                 p["title"])


# ------------------------------------------- H3a: trace robustness + notice

class TestTraceRobustness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        loop.init(self.ws, "g")
        os.makedirs(os.path.join(self.ws, "specs"), exist_ok=True)
        open(os.path.join(self.ws, "specs", "spec.md"), "w").write("# s\n")

    def _trace_path(self):
        return os.path.join(tp.tp_dir(self.ws), "trace.jsonl")

    def test_record_without_event_key_never_crashes_any_render(self):
        with open(self._trace_path(), "a") as f:
            f.write(json.dumps({"ts": 1}) + "\n")
        frag = dashboard.widget(self.ws)          # must not raise KeyError
        dashboard.render(self.ws, out=os.path.join(self.tmp, "d.html"))
        dashboard._journey(self.ws)
        # degraded-but-visible: the malformed record renders as a row
        self.assertIn("(unrecorded)", frag)

    def test_corrupt_lines_are_skipped_with_a_visible_notice(self):
        with open(self._trace_path(), "a") as f:
            f.write("{{{not json\n")
            f.write("also not json\n")
        frag = dashboard.widget(self.ws)
        self.assertIn("tp-trace-notice", frag)
        self.assertIn("2 unparseable trace lines skipped", frag)
        page = open(dashboard.render(
            self.ws, out=os.path.join(self.tmp, "d.html"))).read()
        self.assertIn("unparseable", page)

    def test_clean_trace_shows_no_notice(self):
        frag = dashboard.widget(self.ws)
        self.assertNotIn("tp-trace-notice", frag)

    def test_stats_counts_are_reported(self):
        with open(self._trace_path(), "a") as f:
            f.write("junk\n")
            f.write(json.dumps({"ts": 2}) + "\n")
        stats = {}
        dashboard._read_trace_all(self.ws, stats=stats)
        self.assertEqual(stats["unparseable"], 1)
        self.assertEqual(stats["degraded"], 1)


class TestTraceTailReadNeverRotates(unittest.TestCase):
    """The trace is the AUDIT RECORD: past the tail threshold the dashboard
    reads only recent bytes and SAYS so — it never rotates or deletes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _repo(self.tmp)
        loop.init(self.ws, "g")
        self._old = dashboard.TRACE_TAIL_BYTES
        dashboard.TRACE_TAIL_BYTES = 2048
        p = os.path.join(tp.tp_dir(self.ws), "trace.jsonl")
        with open(p, "a") as f:
            for i in range(200):
                f.write(json.dumps({"event": "loop_step", "ts": i,
                                    "step": "execute"}) + "\n")
        self.p = p

    def tearDown(self):
        dashboard.TRACE_TAIL_BYTES = self._old

    def test_tail_read_notice_and_audit_record_untouched(self):
        size_before = os.path.getsize(self.p)
        frag = dashboard.widget(self.ws)
        self.assertIn("showing recent events", frag)
        self.assertIn("trace.jsonl", frag)         # names where history lives
        self.assertEqual(os.path.getsize(self.p), size_before,
                         "the dashboard must never truncate the audit trace")
        self.assertFalse(os.path.exists(self.p + ".dash"),)
        # dashboard renders create no rotation artifacts of their own
        self.assertFalse(os.path.exists(
            os.path.join(tp.tp_dir(self.ws), "trace.1.jsonl")))

    def test_tail_still_yields_recent_events(self):
        stats = {}
        ev = dashboard._read_trace_all(self.ws, stats=stats)
        self.assertTrue(stats["tail"])
        self.assertGreater(stats["tail_skipped_bytes"], 0)
        self.assertTrue(ev)                         # recent events parsed
        self.assertEqual(ev[-1]["event"], "loop_step")


class TestSingleTraceParse(unittest.TestCase):
    def test_widget_parses_the_trace_exactly_once(self):
        tmp = tempfile.mkdtemp()
        ws = _loop_ws(tmp)
        calls = []
        orig = dashboard._read_trace_all

        def counting(w, stats=None):
            calls.append(1)
            return orig(w, stats=stats)

        dashboard._read_trace_all = counting
        try:
            dashboard.widget(ws)
        finally:
            dashboard._read_trace_all = orig
        self.assertEqual(len(calls), 1,
                         "the trace must be parsed once per render, not 4-5x")

    def test_render_parses_the_trace_exactly_once(self):
        tmp = tempfile.mkdtemp()
        ws = _loop_ws(tmp)
        calls = []
        orig = dashboard._read_trace_all

        def counting(w, stats=None):
            calls.append(1)
            return orig(w, stats=stats)

        dashboard._read_trace_all = counting
        try:
            dashboard.render(ws, out=os.path.join(tmp, "d.html"))
        finally:
            dashboard._read_trace_all = orig
        self.assertEqual(len(calls), 1)


# --------------------------------------- H3b: budget exhaustion disclosure

class TestBudgetExhaustionDisclosure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _loop_ws(self.tmp, step="execute")

    def _contract(self, max_actions=5, used=5):
        c = {"task_id": "t2", "read_only": False,
             "coding": {"scope_paths": ["src/**"],
                        "command_policy": {"deny": ["rm", "curl", "wget"]}},
             "budget": {"max_actions": max_actions}}
        with open(os.path.join(tp.tp_dir(self.ws),
                               "active_contract.json"), "w") as f:
            json.dump(c, f)
        with open(os.path.join(tp.tp_dir(self.ws), "meter.json"), "w") as f:
            json.dump({"t2": {"actions": used}}, f)

    def test_widget_shows_distinct_exhausted_banner_not_idle(self):
        self._contract(5, 5)
        frag = dashboard.widget(self.ws)
        self.assertIn("action budget exhausted (5/5)", frag)
        self.assertNotIn("no action needed from you", frag)
        # the sr heading carries it too — never color/placement alone
        self.assertIn("action budget is exhausted", frag)

    def test_headline_says_so_too(self):
        self._contract(5, 5)
        h = dashboard.headline_loop(self.ws)
        self.assertIn("ACTION BUDGET EXHAUSTED (5/5)", h)

    def test_under_budget_reads_as_working(self):
        self._contract(5, 2)
        frag = dashboard.widget(self.ws)
        self.assertNotIn("action budget exhausted", frag)
        self.assertIn("no action needed from you", frag)
        self.assertNotIn("ACTION BUDGET EXHAUSTED",
                         dashboard.headline_loop(self.ws))


# ------------------------------------------ H1: no fake success w/o bridge

class TestStaticFallbackButtons(unittest.TestCase):
    def test_tpfire_feature_detects_before_mutating(self):
        tmp = tempfile.mkdtemp()
        frag = dashboard.widget(_loop_ws(tmp))
        body = frag[frag.index("function tpFire"):]
        guard = body.index("if(!window.sendPrompt){tpHint(b,m);return;}")
        mutate = body.index("b.disabled=true")
        self.assertLess(guard, mutate,
                        "tpFire must bail to the hint BEFORE restyling the "
                        "button as '✓ approved'")
        self.assertIn("function tpHint", frag)
        self.assertIn("reply in chat", frag)

    def test_onboarding_and_findings_buttons_use_tpsend_with_hint(self):
        html = dashboard.render_onboarding(
            {"checks": [{"id": "x", "label": "x", "ok": False,
                         "hint": "h"}], "next_action": "attach_folder"})
        self.assertIn("tpSend(", html)
        self.assertIn("function tpHint", html)
        f = dashboard.render_findings(
            [], {"title": "t", "gate": True,
                 "gate_buttons": [{"label": "ok", "prompt": "approve",
                                   "primary": True}]})
        self.assertIn("tpSend(", f)
        self.assertIn("function tpHint", f)


# ---------------------------------------------------- spine ids + aria

class TestSpinePerViewIds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.frag = dashboard.widget(_loop_ws(self.tmp))

    def test_ids_are_unique_per_view(self):
        # only visited stages are clickable/id'd; at plan_approval that is
        # pm..plan_approval
        for sid in ("pm", "plan", "plan_approval"):
            self.assertEqual(
                self.frag.count(f'id="tp-spine-s-{sid}"'), 1, sid)
            self.assertEqual(
                self.frag.count(f'id="tp-spine-d-{sid}"'), 1, sid)
            self.assertNotIn(f'id="tp-spine-{sid}"', self.frag)

    def test_tpspine_targets_the_active_views_copy(self):
        self.assertIn('getElementById("tp-spine-"+sfx+"-"+sid)', self.frag)

    def test_selection_carries_aria_current(self):
        self.assertIn('setAttribute("aria-current","true")', self.frag)


class TestAriaStates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.frag = dashboard.widget(_loop_ws(self.tmp))

    def test_tabs_expose_role_and_selected_state(self):
        self.assertIn('role="tablist"', self.frag)
        self.assertIn('role="tab"', self.frag)
        self.assertIn('aria-selected="true"', self.frag)
        self.assertIn('setAttribute("aria-selected"', self.frag)

    def test_view_toggle_exposes_aria_pressed_and_noncolor_cue(self):
        self.assertIn('id="tp-vb-simple" aria-pressed="true"', self.frag)
        self.assertIn('setAttribute("aria-pressed"', self.frag)
        self.assertIn("textDecoration", self.frag)

    def test_widget_feed_is_a_polite_live_region(self):
        self.assertIn('aria-live="polite"', self.frag)

    def test_journey_items_carry_expanded_state(self):
        self.assertIn('aria-expanded="false"', self.frag)
        self.assertIn('setAttribute("aria-expanded"', self.frag)

    def test_wave_board_phase_is_live(self):
        html = dashboard.render_lens_wave(
            [{"id": "security", "status": "running"}])
        self.assertIn('aria-live="polite"', html)


# --------------------------------------------------------- i18n readiness

class TestMessageCatalog(unittest.TestCase):
    def test_plural_selection(self):
        self.assertEqual(dashboard._msg("n_findings", n=1), "1 finding")
        self.assertEqual(dashboard._msg("n_findings", n=2), "2 findings")
        self.assertEqual(dashboard._msg("n_tasks_planned", n=1),
                         "1 task planned")

    def test_no_parenthesized_plurals_in_widget(self):
        tmp = tempfile.mkdtemp()
        frag = dashboard.widget(_loop_ws(tmp))
        for bad in ("task(s)", "issue(s)", "lens(es)", "module(s)",
                    "warning(s)", "action(s)"):
            self.assertNotIn(bad, frag, bad)

    def test_gate_subtitle_pluralizes_task_count(self):
        tmp = tempfile.mkdtemp()
        frag1 = dashboard.widget(_loop_ws(tmp, n_tasks=1))
        self.assertIn("1 task planned", frag1)
        frag3 = dashboard.widget(_loop_ws(tempfile.mkdtemp(), n_tasks=3))
        self.assertIn("3 tasks planned", frag3)

    def test_headline_is_one_template_with_gate_segment(self):
        tmp = tempfile.mkdtemp()
        h = dashboard.headline_loop(_loop_ws(tmp, step="plan_approval"))
        self.assertIn("step=plan_approval", h)
        self.assertIn("YOUR GATE", h)


class TestTimestamps(unittest.TestCase):
    def test_fmt_ts_is_iso8601_utc(self):
        self.assertEqual(dashboard._fmt_ts(0), "1970-01-01T00:00:00Z")

    def test_journey_when_row_is_utc_marked(self):
        tmp = tempfile.mkdtemp()
        frag = dashboard.widget(_loop_ws(tmp))
        m = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z UTC", frag)
        self.assertIsNotNone(m, "timestamps must be ISO-8601 UTC with the "
                             "offset explicit, not server-local HH:MM:SS")


class TestRtlAndResponsive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ws = _loop_ws(self.tmp)
        self.frag = dashboard.widget(self.ws)
        self.page = open(dashboard.render(
            self.ws, out=os.path.join(self.tmp, "d.html"))).read()

    def test_document_root_carries_lang_and_dir(self):
        self.assertIn('lang="en"', self.page)
        self.assertIn("dir=", self.page)
        self.assertIn('dir="auto"', self.frag)     # widget root too

    def test_no_physical_direction_properties(self):
        for bad in ("margin-left:", "padding-left:", "text-align:left",
                    "text-align:right", "border-left:"):
            self.assertNotIn(bad, self.frag, bad)

    def test_no_uppercase_transform_on_labels(self):
        self.assertNotIn("text-transform:uppercase", self.frag)
        self.assertNotIn(".upper()", self.frag)

    def test_widget_grids_collapse_below_640(self):
        self.assertIn("@media (max-width:640px)", self.frag)
        self.assertIn('class="tp-grid2"', self.frag)
        self.assertIn('class="tp-jgrid"', self.frag)

    def test_full_page_grid_has_breakpoint(self):
        self.assertIn("@media", self.page)

    def test_arrows_go_through_the_helper(self):
        self.assertEqual(dashboard._arrow(), "→")
        self.assertEqual(dashboard._arrow(back=True), "←")


class TestFullPageA11y(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.page = open(dashboard.render(
            _loop_ws(tmp), out=os.path.join(tmp, "d.html"))).read()

    def test_pipeline_state_has_sr_text_not_color_only(self):
        self.assertIn("current step", self.page)
        self.assertIn("human gate", self.page)
        self.assertIn('class="sr"', self.page)

    def test_contrast_floor_on_legend_and_card_headings(self):
        self.assertIn(".legend{color:#94948a", self.page)
        self.assertNotIn("#66665c", self.page)
        self.assertNotIn("#77776c;margin-bottom:11px", self.page)

    def test_feed_is_a_live_region(self):
        self.assertIn('aria-live="polite"', self.page)


class TestPagedFindingsSrHeadings(unittest.TestCase):
    def test_summary_and_chunks_carry_sr_headings(self):
        findings = [{"severity": "high", "domain": "d",
                     "title": f"t{i}", "scenario": "x" * 200,
                     "fix": "y" * 150} for i in range(60)]
        pages = dashboard.render_findings_paged(findings, {"title": "big"})
        self.assertGreater(len(pages), 1)
        self.assertIn('class="sr-only"', pages[0]["html"])
        for p in pages[1:]:
            self.assertIn('<h3 class="sr-only">', p["html"])


# ----------------------------------------------------- schemas + escaping

class TestSchemaBridge(unittest.TestCase):
    def test_charter_severities_bucket_correctly(self):
        f = [{"severity": "blocker", "issue": "b"},
             {"severity": "major", "issue": "m"},
             {"severity": "minor", "issue": "n"},
             {"severity": "question", "issue": "q"},
             {"severity": "praise", "issue": "p"}]
        h = dashboard.headline_findings(f, {"title": "r"})
        # v2.3.1: dashboard buckets via loop.normalize_severity, so `major`
        # buckets to HIGH (matching the gate), not med. This test previously
        # pinned the divergent `major -> med` bug that finding #9 fixed.
        self.assertIn("2 high", h)                  # blocker + major
        self.assertIn("0 med", h)
        self.assertIn("1 low", h)                   # minor
        self.assertIn("(3 findings)", h)           # notes are NOT defects
        self.assertIn("2 notes (question/praise/machinery, not defects)",
                      h)

    def test_unknown_severity_never_downgrades(self):
        h = dashboard.headline_findings(
            [{"severity": "wild", "title": "x"}], {"title": "r"})
        self.assertIn("1 high", h)
        self.assertIn("unrated", h)

    def test_charter_fields_alias_to_renderer_fields(self):
        card = dashboard._compact_card(
            {"severity": "major", "issue": "the issue text",
             "why": "the why text", "suggestion": "the suggestion"})
        self.assertIn("the issue text", card)
        self.assertIn("the why text", card)
        self.assertIn("the suggestion", card)


class TestEscaping(unittest.TestCase):
    def test_lane_badge_label_is_escaped(self):
        out = dashboard._lane(
            {"id": "t1", "status": "<img src=x onerror=alert(1)>",
             "scope": ["src/**"]}, "execute")
        self.assertNotIn("<img", out)
        self.assertIn("&lt;img", out)

    def test_compact_card_truncates_before_escaping(self):
        card = dashboard._compact_card(
            {"severity": "high", "title": "t",
             "scenario": "x" + "&" * 300})
        # no dangling half-entity like '&am' at a slice boundary
        self.assertIsNone(
            re.search(r"&(?!(amp|lt|gt|quot|#39|#\d+);)[a-z]*\s*<",
                      card),
            "an HTML entity was bisected by truncation")


# ------------------------------------------------- decomposition guardrail

class TestWidgetComposition(unittest.TestCase):
    """widget() is a composition of named part-builders; widget_paged
    reuses the SAME parts, so the two render paths cannot drift."""

    def test_parts_and_widget_agree(self):
        tmp = tempfile.mkdtemp()
        ws = _loop_ws(tmp)
        parts = dashboard._widget_parts(ws)
        frag = dashboard.widget(ws)
        for key in ("gatebar", "harness_panel", "loop_panel", "stats"):
            self.assertTrue(parts[key])
            self.assertIn(parts[key], frag,
                          f"part {key!r} must appear verbatim in widget()")

    def test_named_builders_exist(self):
        for name in ("_widget_spine", "_widget_gatebar", "_widget_lanes",
                     "_widget_feed", "_widget_ministats", "_widget_dor",
                     "_widget_parts"):
            self.assertTrue(callable(getattr(dashboard, name)), name)


# ------------------------------------------- A5 machinery warn rows (Phase 3)

class TestMachineryWarnRowsRenderAsAdvisory(unittest.TestCase):
    """A5's warn row PRESERVES the wrapped finding's severity by contract
    (contract:findings-v2), so ONE unattributed blocker used to render as TWO
    blocker cards and a headline count of 2 — the warn row (whose own title is
    the meta-issue "unattributed finding: no lens attribution") never blocks.
    The renderer now treats a warn row whose nested original is present in the
    same set as an advisory DUPLICATE: counted as a note, still labelled with
    the underlying severity."""

    def _rows(self, severity="blocker"):
        import audit
        decision = {"sre": {"verdict": "deep", "score": 3}}
        original = {"severity": severity, "title": "real defect",
                    "class": "regression", "status": "open",
                    "file": "a.py", "line": 7}
        return [original] + audit._unattributed_rows(decision, [original])

    def test_headline_counts_one_defect_not_two(self):
        rows = self._rows()
        self.assertEqual(len(rows), 2)              # the machinery row IS filed
        h = dashboard.headline_findings(rows, {"title": "em review"})
        self.assertIn("1 high", h)                  # not 2
        self.assertIn("(1 finding)", h)
        self.assertIn("1 note", h)                  # nothing dropped

    def test_findings_dashboard_badges_it_as_machinery_not_a_blocker(self):
        rows = self._rows()
        html = dashboard.render_findings(rows, {"title": "em"})
        self.assertIn('aria-label="filter: high (1)"', html)
        self.assertIn('aria-label="filter: notes (1)"', html)
        # honest: the advisory badge still NAMES the underlying severity
        self.assertIn("machinery warn · blocker", html)

    def test_forged_warn_row_with_no_original_keeps_its_severity(self):
        # shape alone must not downgrade anything: every field of the warn
        # shape lives in worker-authored findings.json, and a row that
        # duplicates nothing on this page can be exactly what the em gate
        # blocks on. The renderer must never show LESS than the gate.
        forged = {"severity": "blocker", "class": "observation",
                  "owner": "router", "warn": True,
                  "domain": "router+unattributed", "title": "costume",
                  "status": "open",
                  "finding": {"title": "ghost", "file": "z.py", "line": 1}}
        h = dashboard.headline_findings([forged], {"title": "em"})
        self.assertIn("1 high", h)
        self.assertIn("(1 finding)", h)
        self.assertNotIn("1 note", h)

    def test_ordinary_findings_are_untouched(self):
        rows = [{"severity": "blocker", "title": "b"},
                {"severity": "med", "title": "m"},
                {"severity": "low", "title": "l"}]
        h = dashboard.headline_findings(rows, {"title": "em"})
        self.assertIn("1 high", h)
        self.assertIn("1 med", h)
        self.assertIn("1 low", h)
        self.assertIn("(3 findings)", h)


if __name__ == "__main__":
    unittest.main()
