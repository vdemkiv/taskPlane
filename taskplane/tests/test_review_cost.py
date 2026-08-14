"""Four measured costs, four mechanisms.

From one real review of aws/karpenter-provider-aws#9464: 3.77M effective
tokens over 99 turns. The breakdown is what this file exists to change.

  777k  shell commands & taskplane CLI — 69 of them, ~11,261 effective
        tokens each, and every command AND its output then sits in the
        conversation to be re-read on every later turn. A review ran about
        ten of them before a single lens saw the diff.
  754k  four lens agents, "each carrying its own copy of the diff and the
        blast-radius brief" — the diff is identical for all four.
  450k  inline dashboards, because the driver pasted back ~52k characters of
        HTML that taskplane had already written to disk. Caused by the
        v2.9.0 render obligation: making the render enforceable made the
        cheapest compliance path the most expensive one.

And the budget that was supposed to bound all of it counted TOOL CALLS. At
~11k effective tokens per action with a two-order-of-magnitude spread,
"raise 40 to 80" was never a fine-tune — it bought ~440k tokens sight
unseen.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
import lens                       # noqa: E402
import depgraph                   # noqa: E402
import review as rv               # noqa: E402
import spend                      # noqa: E402
import taskplane_lite as tp       # noqa: E402
import tp as cli                  # noqa: E402


def _run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(list(args))
        except SystemExit as e:
            rc = int(e.code or 0)
    return rc, out.getvalue(), err.getvalue()


class _WS(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.ws = os.path.join(self.d, "repo")
        os.makedirs(os.path.join(self.ws, "pkg"))
        for a in (["init", "-q"], ["config", "user.email", "e@e"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=self.ws, capture_output=True)
        self._write("go.mod", "module example.com/m\n\ngo 1.24\n")
        self._write("pkg/a.go", "package a\n\nfunc A() int { return 1 }\n")
        self._commit("base")
        self._write("pkg/a.go", "package a\n\nfunc A() int { return 2 }\n")
        self._commit("change")
        self._home = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = os.path.join(self.d, "store")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._home
        os.environ.pop("TASKPLANE_INLINE_MAX", None)
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, rel, body):
        p = os.path.join(self.ws, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)

    def _commit(self, msg):
        subprocess.run(["git", "add", "-A"], cwd=self.ws, capture_output=True)
        subprocess.run(["git", "commit", "-qm", msg], cwd=self.ws,
                       capture_output=True)


# ------------------------------------------------- 1. render by reference

class RenderByReference(_WS):
    def _findings(self, n):
        f = [{"severity": "high", "title": f"finding {i}",
              "class": "observation", "file": "pkg/a.go", "line": i,
              "domain": "code-quality", "scenario": "x" * 400,
              "fix": "y" * 400} for i in range(n)]
        os.makedirs(os.path.join(self.ws, ".em-review"), exist_ok=True)
        with open(os.path.join(self.ws, ".em-review", "findings.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"meta": {"title": "probe"}, "findings": f}, fh)

    def test_a_large_document_is_delivered_not_retyped(self):
        self._findings(40)
        rc, out, _ = _run("findings", "--workspace", self.ws)
        self.assertEqual(rc, 0)
        self.assertIn("RENDER-BY-REFERENCE:", out)
        self.assertIn("do NOT paste its contents back", out)
        self.assertTrue(os.path.isfile(
            os.path.join(self.ws, ".em-review", "findings.html")))

    def test_the_headline_still_comes_first(self):
        """The never-skippable carrier survives the change — the numbers
        must reach the human whether or not the artifact is opened."""
        self._findings(40)
        _, out, _ = _run("findings", "--workspace", self.ws)
        self.assertTrue(out.startswith("HEADLINE:"), out[:80])

    def test_a_small_fragment_is_still_inline(self):
        """Reference mode is for documents, not for messages. A short
        review should not cost the human a file to open."""
        self._findings(1)
        _, out, _ = _run("findings", "--workspace", self.ws)
        self.assertNotIn("RENDER-BY-REFERENCE:", out)
        self.assertIn("sr-only", out)

    def test_the_threshold_is_configurable_and_can_be_disabled(self):
        self._findings(40)
        os.environ["TASKPLANE_INLINE_MAX"] = "0"
        _, out, _ = _run("findings", "--workspace", self.ws)
        self.assertNotIn("RENDER-BY-REFERENCE:", out)

    def test_paged_and_html_modes_are_untouched(self):
        self._findings(40)
        for flag in ("--paged", "--html"):
            with self.subTest(flag):
                _, out, _ = _run("findings", "--workspace", self.ws, flag)
                self.assertNotIn("RENDER-BY-REFERENCE:", out)

    def test_delivering_the_file_discharges_the_obligation(self):
        """The point: a delivered file is the SAME bytes as an inline
        render, so it is not a weaker discharge — it is the one that does
        not cost a full re-authoring."""
        import obligations
        _run("new", "--read-only", "--write-allow", ".em-review/**",
             "--workspace", self.ws, "--base", "HEAD~1", "review: probe")
        art = os.path.join(self.ws, ".em-review", "findings.html")
        os.makedirs(os.path.dirname(art), exist_ok=True)
        with open(art, "w", encoding="utf-8") as f:
            f.write("<html>engine bytes</html>")
        oid = obligations.issue(self.ws, "render_dashboard", detail="d",
                                step="review", artifact=".em-review/findings.html")
        rc, out, _ = _run("ack", oid, "--delivered", art,
                          "--workspace", self.ws)
        self.assertEqual(rc, 0)
        st = obligations.status(self.ws)
        self.assertEqual(st["open"], [])
        self.assertGreaterEqual(st["observed"], 1)
        self.assertTrue([o for o in st["corroborated"] if o["id"] == oid],
                        "a delivered file must corroborate, not merely claim")

    def test_an_obligation_fingerprints_the_artifact_in_ITS_workspace(self):
        """Found by the test above. `issue()` hashed a relative artifact
        path against the PROCESS cwd, so an obligation issued from anywhere
        but the workspace either recorded nothing or recorded a same-named
        file from another checkout as "the engine's bytes" — and every later
        comparison then measured the wrong file."""
        import obligations
        decoy = os.path.join(self.d, "decoy")
        os.makedirs(os.path.join(decoy, ".em-review"))
        with open(os.path.join(decoy, ".em-review", "findings.html"), "w",
                  encoding="utf-8") as f:
            f.write("<html>a DIFFERENT checkout's file</html>")
        art = os.path.join(self.ws, ".em-review", "findings.html")
        os.makedirs(os.path.dirname(art), exist_ok=True)
        with open(art, "w", encoding="utf-8") as f:
            f.write("<html>engine bytes</html>")
        cwd = os.getcwd()
        os.chdir(decoy)
        try:
            oid = obligations.issue(
                self.ws, "render_dashboard", detail="d", step="review",
                artifact=".em-review/findings.html")
        finally:
            os.chdir(cwd)
        row = next(r for r in obligations.read(self.ws)
                   if r.get("id") == oid and r.get("event") == "issued")
        self.assertEqual(row["fingerprint"],
                         obligations.artifact_fingerprint(art),
                         "the obligation fingerprinted the wrong checkout")

    def test_delivering_a_substitute_is_recorded_as_a_mismatch(self):
        """Delivery must not become a way to discharge with any file at
        all — the fingerprint is what the ledger compares, either way."""
        import obligations
        _run("new", "--read-only", "--write-allow", ".em-review/**",
             "--workspace", self.ws, "--base", "HEAD~1", "review: probe")
        art = os.path.join(self.ws, ".em-review", "findings.html")
        os.makedirs(os.path.dirname(art), exist_ok=True)
        with open(art, "w", encoding="utf-8") as f:
            f.write("<html>engine bytes</html>")
        other = os.path.join(self.ws, ".em-review", "my-own.html")
        with open(other, "w", encoding="utf-8") as f:
            f.write("<html>something I wrote instead</html>")
        oid = obligations.issue(self.ws, "render_dashboard", detail="d",
                                step="review", artifact=".em-review/findings.html")
        _run("ack", oid, "--delivered", other, "--workspace", self.ws)
        st = obligations.status(self.ws)
        self.assertTrue(st["mismatched"],
                        "a delivered substitute must be recorded as one")


# ------------------------------------------------ 2. one call, not ten

class OneCallOpening(_WS):
    def _start(self, *extra):
        # Positive fixture supplies actual complete scanner + symbol-index
        # evidence at the pinned head. It therefore earns a normal route;
        # the separate graph-quality tests keep partial evidence fail-closed.
        graph = depgraph.scan(self.ws)
        graph["meta"]["scanners"]["go"] = {
            "coverage": "complete", "covered_files": ["pkg/a.go"],
            "total_files": 1}
        graph["symbol_edges"] = [{"caller": "main", "callee": "A",
                                  "contract": "contract:entrypoint"}]
        depgraph.save(self.ws, graph)
        rc, out, err = _run("review", "start", "--base", "HEAD~1",
                            "--workspace", self.ws, *extra)
        return rc, json.loads(out), err

    def test_it_establishes_every_fact_a_review_opens_with(self):
        rc, d, _ = self._start()
        self.assertEqual(rc, 0, d)
        self.assertEqual(d["status"], "ready")
        self.assertEqual(sum(d["routing_counts"].values()),
                         len(lens.load_catalog()["lenses"]))
        self.assertLessEqual(len(json.dumps(d).encode()), 16 * 1024)

    def test_cli_manifest_counter_covers_final_contract_and_tool_fields(self):
        _, d, _ = self._start()
        import review_evidence
        self.assertIn("contract", d)
        self.assertIn("tools", d)
        self.assertEqual(d["manifest_bytes"],
                         len(review_evidence.canonical_bytes(d)))
        self.assertEqual(d["counters"]["emitted_bytes"],
                         d["manifest_bytes"])

    def test_it_returns_the_briefs_ready_to_dispatch(self):
        _, d, _ = self._start()
        self.assertTrue(d["slots"])
        self.assertLessEqual(sum(row["slot_id"] == "light-sweep"
                                 for row in d["slots"]), 1)
        self.assertNotIn("dispatch", d)

    def test_it_activates_the_contract_and_pins_the_target(self):
        _, d, _ = self._start()
        c = tp.load_active(self.ws)
        self.assertTrue(c["read_only"])
        self.assertEqual(c["target"]["fingerprint"],
                         d["target_fingerprint"])

    def test_it_seeds_the_obligations_a_review_owes(self):
        _, d, _ = self._start()
        # Owed state remains in the obligation ledger, not duplicated on
        # compact stdout.
        import obligations
        self.assertTrue(obligations.status(self.ws)["issued"])
        self.assertNotIn("owes", d)

    def test_it_decides_nothing(self):
        """It establishes facts. A step that produced findings or a verdict
        would be a grader grading its own inputs."""
        _, d, _ = self._start()
        self.assertNotIn("findings", d)
        self.assertNotIn("verdict", d)
        # The only "verdict"s anywhere in the payload are ROUTING verdicts —
        # which lens runs, not what it concluded.
        self.assertEqual(d["routing_decision"]["kind"], "routing-decision")
        self.assertNotIn("dispositions", d["routing_decision"])
        blob = json.dumps(d).lower()
        for word in ('"severity":', '"blocker"', '"sign_off"'):
            self.assertNotIn(word, blob, f"review start emitted {word}")

    def test_a_target_it_cannot_pin_fails_before_anything_is_activated(self):
        empty = os.path.join(self.d, "empty")
        os.makedirs(empty)
        rc, out, _ = _run("review", "start", "--workspace", empty)
        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(out)["ok"])
        self.assertFalse((tp.load_active(empty) or {}).get("task_id"))

    def test_a_token_ceiling_can_be_set_at_the_opening(self):
        _, d, _ = self._start("--max-tokens", "750000")
        self.assertEqual(tp.load_active(self.ws)["budget"]["max_tokens"],
                         750000)


# ------------------------------------- 3. one copy of the diff, not four

class SharedReviewContext(_WS):
    def test_the_context_is_written_once(self):
        paths = rv.write_context(self.ws, diff="DIFF", blast_radius="BLAST",
                                 impact={"touched": ["m"]})
        self.assertEqual(sorted(paths), ["blast-radius.md", "diff.patch",
                                         "impact.json"])
        for rel in paths.values():
            self.assertTrue(os.path.isfile(os.path.join(self.ws, rel)))

    def test_briefs_cite_the_paths_instead_of_carrying_the_payload(self):
        routing = {"context": {"changed_files": 2},
                   "lenses": [{"id": "security", "name": "S", "tier": "deep",
                               "mode": "subagent", "reasons": ["r"],
                               "checks": ["c"]},
                              {"id": "perf", "name": "P", "tier": "sweep",
                               "mode": "inline", "reasons": ["r"],
                               "checks": ["c"]}]}
        paths = rv.write_context(self.ws, diff="D" * 5000,
                                 blast_radius="B" * 5000)
        out = lens.dispatch_briefs(routing, context_paths=paths)
        for prompt in ([b["prompt"] for b in out["deep"]]
                       + [out["sweep"]["prompt"]]):
            self.assertIn("SHARED REVIEW CONTEXT", prompt)
            self.assertIn(".em-review/context/diff.patch", prompt)
            self.assertNotIn("D" * 200, prompt)

    def test_it_tells_the_agent_not_to_re_derive(self):
        """An agent told only that a file exists will run `git diff` anyway,
        which is the cost this removes."""
        note = rv.context_note(rv.write_context(self.ws, diff="d",
                                                blast_radius="b"))
        self.assertIn("do NOT", note)
        self.assertIn("do not run `git diff` again", note)
        self.assertIn("do not re-run `graph impact`", note)

    def test_no_context_means_the_old_embedding_behaviour(self):
        """A workspace that will not take the files must degrade to the
        previous behaviour, not to a brief with no context at all."""
        routing = {"context": {"changed_files": 1},
                   "lenses": [{"id": "security", "name": "S", "tier": "deep",
                               "mode": "subagent", "reasons": ["r"],
                               "checks": ["c"]}]}
        out = lens.dispatch_briefs(routing, impact_context="BLAST RADIUS HERE")
        self.assertIn("BLAST RADIUS HERE", out["deep"][0]["prompt"])
        self.assertNotIn("SHARED REVIEW CONTEXT", out["deep"][0]["prompt"])

    def test_an_unwritable_workspace_returns_no_paths(self):
        self.assertEqual(rv.write_context("/proc/nonexistent/x", diff="d"), {})

    def test_four_agents_share_one_diff(self):
        """The measured shape: N briefs, one payload."""
        routing = {"context": {"changed_files": 4},
                   "lenses": [{"id": i, "name": i, "tier": "deep",
                               "mode": "subagent", "reasons": ["r"],
                               "checks": ["c"]}
                              for i in ("security", "code-quality",
                                        "testability", "architecture")]}
        big = "X" * 20000
        paths = rv.write_context(self.ws, diff=big)
        out = lens.dispatch_briefs(routing, context_paths=paths)
        total = sum(len(b["prompt"]) for b in out["deep"])
        self.assertEqual(len(out["deep"]), 4)
        self.assertLess(total, len(big),
                        "four briefs together still cost more than one copy "
                        "of the diff")


# ------------------------------------------- 4. budget in tokens, not actions

class TokenBudget(unittest.TestCase):
    def _transcript(self, path, n=20, **usage):
        u = {"input_tokens": 1000, "cache_read_input_tokens": 50000,
             "cache_creation_input_tokens": 2000, "output_tokens": 3000}
        u.update(usage)
        with open(path, "w", encoding="utf-8") as f:
            for _ in range(n):
                f.write(json.dumps({"message": {"usage": u}}) + "\n")

    def test_the_weights_are_the_ones_cost_actually_follows(self):
        self.assertEqual(spend.weigh({"input_tokens": 1}), 1.0)
        self.assertEqual(spend.weigh({"cache_read_input_tokens": 10}), 1.0)
        self.assertEqual(spend.weigh({"cache_creation_input_tokens": 1}), 2.0)
        self.assertEqual(spend.weigh({"output_tokens": 1}), 5.0)

    def test_a_raw_sum_would_have_told_you_almost_nothing(self):
        """~22M raw vs ~3.8M effective on the same review. A ceiling on the
        raw number would bind at a completely different place."""
        u = {"input_tokens": 1000, "cache_read_input_tokens": 100000,
             "cache_creation_input_tokens": 0, "output_tokens": 1000}
        raw = sum(u.values())
        self.assertGreater(raw / spend.weigh(u), 5)

    def test_it_sums_what_the_host_recorded(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "t.jsonl")
            self._transcript(p, n=20)
            rep = spend.read_transcript(p)
            self.assertTrue(rep["available"])
            self.assertEqual(rep["messages"], 20)
            self.assertEqual(rep["effective"], 20 * 25000)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_torn_line_does_not_lose_the_whole_transcript(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "t.jsonl")
            self._transcript(p, n=3)
            with open(p, "a", encoding="utf-8") as f:
                f.write('{"message": {"usage": {"output_tokens": 1')
            self.assertEqual(spend.read_transcript(p)["messages"], 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_missing_transcript_fails_open(self):
        """A budget that blocks when its instrument breaks turns a broken
        instrument into a broken product. The action ceiling still stands."""
        rep = spend.read_transcript("/nonexistent/t.jsonl")
        self.assertFalse(rep["available"])
        self.assertEqual(rep["effective"], 0)

    def test_no_ceiling_means_no_change(self):
        ok, why = spend.status({"budget": {}}, 10_000_000)
        self.assertTrue(ok)
        self.assertIn("no token ceiling", why)

    def test_the_ceiling_binds_and_says_how_to_raise_it(self):
        ok, why = spend.status({"budget": {"max_tokens": 100}}, 100)
        self.assertFalse(ok)
        self.assertIn("TOKEN BUDGET exhausted", why)
        self.assertIn("--grant-tokens", why)
        self.assertIn("OUTSIDE this workspace", why)

    def test_an_unreadable_ceiling_is_ignored_not_enforced(self):
        self.assertTrue(spend.status({"budget": {"max_tokens": "lots"}},
                                     10 ** 9)[0])

    def test_it_names_what_an_action_actually_cost(self):
        """The number that makes the action ceiling legible: 11,261 on the
        measured review, not a constant anyone could have guessed."""
        self.assertEqual(round(spend.cost_per_action(777_000, 69)), 11261)
        self.assertEqual(spend.cost_per_action(1000, 0), 0.0)

    def test_the_hook_finds_the_transcript_under_any_of_its_names(self):
        for key in ("transcript_path", "agent_transcript_path", "transcript"):
            with self.subTest(key):
                self.assertEqual(spend.event_transcript({key: "/x"}), "/x")
        self.assertIsNone(spend.event_transcript({}))


class TokenCeilingThroughTheScreener(_WS):
    def _screen(self, command, transcript=None):
        ev = {"tool_name": "Bash", "tool_input": {"command": command},
              "cwd": self.ws}
        if transcript:
            ev["transcript_path"] = transcript
        out = io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(ev))
        try:
            with contextlib.redirect_stdout(out):
                cli.main(["screen"])
        finally:
            sys.stdin = old
        text = out.getvalue().strip()
        if not text:
            return "abstain", ""
        d = json.loads(text)
        return d.get("decision", "allow"), d.get("reason", "")

    def _contract_with(self, max_tokens):
        _run("new", "--read-only", "--write-allow", ".em-review/**",
             "--workspace", self.ws, "--base", "HEAD~1",
             "--max-tokens", str(max_tokens), "review: probe")
        p = os.path.join(self.ws, "t.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for _ in range(20):
                f.write(json.dumps({"message": {"usage": {
                    "input_tokens": 1000, "cache_read_input_tokens": 50000,
                    "cache_creation_input_tokens": 2000,
                    "output_tokens": 3000}}}) + "\n")
        return p          # 500,000 effective

    def test_over_the_ceiling_blocks(self):
        tr = self._contract_with(200_000)
        decision, why = self._screen("grep -rn foo .", tr)
        self.assertEqual(decision, "block")
        self.assertIn("TOKEN BUDGET exhausted", why)

    def test_under_the_ceiling_proceeds(self):
        tr = self._contract_with(900_000)
        self.assertNotEqual(self._screen("grep -rn foo .", tr)[0], "block")

    def test_with_no_transcript_the_ceiling_cannot_bind(self):
        self._contract_with(1)
        self.assertNotEqual(self._screen("grep -rn foo .")[0], "block")

    def test_inspection_is_still_free_even_over_the_ceiling(self):
        """A run that cannot report why it stopped is worse than one that
        overspends by one status call."""
        tr = self._contract_with(1)
        for cmd in ("tp status", "tp contracts", "tp ack --status"):
            with self.subTest(cmd):
                self.assertEqual(self._screen(cmd, tr)[0], "abstain")


if __name__ == "__main__":
    unittest.main()
