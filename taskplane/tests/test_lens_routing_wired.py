"""The applicability engine must actually route the wave.

Route v2 — content, graph and requirement signals producing a per-lens
`deep | light | n/a` verdict, every n/a carrying machine-checkable negative
evidence — shipped in v2.4.0 and was unreachable from the CLI for six
releases. `route()` enables it only when `stage` or `use_signals` is passed;
`cmd_lens` passed neither, so `lens route` and `lens dispatch` — where a
review spends its tokens — took the glob-based legacy path. The only caller
in the codebase that passed `stage="review"` was `audit.py`, the coverage
REPORTER. The engine scored the diff for a report and the wave ignored it.

Compounding it, `tp-engineering/SKILL.md` mandated `--all` on every review
command, and `--all` disables the engine BY CONSTRUCTION (`breadth != "all"`
in `route()`). Two independent causes; fixing either alone changed nothing,
which is why this file pins both.

Measured on the field case (aws/karpenter-provider-aws#9464, a Go type
addition plus a docs edit): the legacy router summoned 6 lenses deep and
marked nothing n/a; the engine routes 2 deep, 4 light, 20 n/a. The real run
dispatched 6 agents and 336,242 tokens, and its most expensive lens —
architecture, 61,454 tokens — is one the engine marks n/a on that diff, and
its headline claim was the one an independent fact-check knocked down.

What must stay true:

  * the CLI ASKS for signal-driven routing, on both `route` and `dispatch`;
  * an `n/a` lens gets NO brief and NO agent;
  * an `n/a` lens always says why — a skip without evidence is a silent
    skip, which is the thing full-catalog review existed to prevent;
  * `--all` still forces the whole catalog, because "run everything" is a
    legitimate thing to want on purpose;
  * the review skill does not spend it by default.
"""
import io
import json
import os
import subprocess
import sys
import unittest
import contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))
import lens                       # noqa: E402
import tp as cli                  # noqa: E402


def _go_repo(d):
    """The field case's shape: a Go type change plus a docs edit."""
    os.makedirs(os.path.join(d, "pkg", "providers", "amifamily", "bootstrap"))
    os.makedirs(os.path.join(d, "website", "content"))
    for a in (["init", "-q"], ["config", "user.email", "e@e"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=d, capture_output=True)
    with open(os.path.join(d, "go.mod"), "w", encoding="utf-8") as f:
        f.write("module github.com/aws/karpenter-provider-aws\n\ngo 1.24\n")
    boot = os.path.join(d, "pkg", "providers", "amifamily", "bootstrap",
                        "bottlerocket.go")
    with open(boot, "w", encoding="utf-8") as f:
        f.write('package bootstrap\n\ntype BottlerocketSettings struct {\n'
                '\tKubernetes *K `toml:"kubernetes,omitempty"`\n}\n')
    doc = os.path.join(d, "website", "content", "nodeclasses.md")
    with open(doc, "w", encoding="utf-8") as f:
        f.write("# EC2NodeClass\n\nUnknown TOML fields will be ignored.\n")
    subprocess.run(["git", "add", "-A"], cwd=d, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=d,
                   capture_output=True)
    with open(boot, "a", encoding="utf-8") as f:
        f.write('\ntype BottlerocketAutoscaling struct {\n'
                '\tShouldWait *bool `toml:"should-wait,omitempty"`\n}\n')
    with open(doc, "a", encoding="utf-8") as f:
        f.write("\n- `settings.autoscaling` is now modelled.\n")
    return d


def _cli(ws, *extra):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli.main(["lens", "dispatch", "--workspace", ws, *extra])
    return rc, json.loads(out.getvalue())


class _Case(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.d = tempfile.mkdtemp()
        _go_repo(self.d)
        self._home = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = os.path.join(self.d, "_store")

    def tearDown(self):
        import shutil
        if self._home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = self._home
        shutil.rmtree(self.d, ignore_errors=True)


class TestTheCliAsksForTheEngine(_Case):
    def test_dispatch_carries_a_routing_decision_by_default(self):
        """`routing_decision` exists ONLY on the v2 path — its presence is
        the mechanical proof the engine ran."""
        _, payload = _cli(self.d)
        self.assertIn("routing_decision", payload,
                      "the CLI took the legacy path — the engine did not run")
        self.assertEqual(len(payload["routing_decision"]),
                         len(lens.load_catalog()["lenses"]),
                         "every catalog lens must be dispositioned")

    def test_route_asks_for_it_too_not_only_dispatch(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["lens", "route", "--workspace", self.d, "--json"])
        self.assertEqual(rc, 0)
        routing = json.loads(out.getvalue())
        self.assertTrue(any("verdict" in x for x in routing["lenses"]),
                        "`lens route` still runs the glob router")

    def test_an_na_lens_gets_no_brief_and_no_agent(self):
        _, payload = _cli(self.d)
        na = {k for k, v in payload["routing_decision"].items()
              if v["verdict"] == "n/a"}
        self.assertTrue(na, "a narrow diff should mark some lenses n/a")
        dispatched = {b["id"] for b in payload["deep"]} | set(
            (payload["sweep"] or {}).get("ids") or [])
        self.assertFalse(na & dispatched,
                         f"n/a lenses were dispatched anyway: "
                         f"{sorted(na & dispatched)}")

    def test_every_skip_says_why(self):
        """A skip with no evidence is a silent skip. Running all 26 lenses
        was the old way of never having to answer this."""
        _, payload = _cli(self.d)
        for lid, d in payload["routing_decision"].items():
            if d["verdict"] == "n/a":
                with self.subTest(lid):
                    self.assertTrue(
                        d.get("negative_evidence"),
                        f"{lid} is n/a with no negative evidence")

    def test_it_dispatches_strictly_fewer_agents_than_forcing_everything(self):
        _, routed = _cli(self.d)
        _, forced = _cli(self.d, "--all")
        n_routed = len(routed["deep"]) + (1 if routed["sweep"] else 0)
        n_forced = len(forced["deep"]) + (1 if forced["sweep"] else 0)
        self.assertLess(n_routed, n_forced,
                        f"routing saved nothing: {n_routed} vs {n_forced}")


class TestForcingEverythingStillWorks(_Case):
    """`--all` is a legitimate deliberate choice; it just stops being the
    default the skill spends on every review."""

    def test_all_still_runs_the_whole_catalog(self):
        _, payload = _cli(self.d, "--all")
        ids = {b["id"] for b in payload["deep"]} | set(
            (payload["sweep"] or {}).get("ids") or [])
        self.assertEqual(len(ids), len(lens.load_catalog()["lenses"]))

    def test_all_takes_the_legacy_path_as_documented(self):
        """`route()` reads `breadth != "all"`. If that ever changes, the
        skill's warning about --all becomes wrong and must change with it."""
        _, payload = _cli(self.d, "--all")
        self.assertNotIn("routing_decision", payload)


class TestTheEngineFailsOpen(_Case):
    def test_a_broken_engine_widens_the_review_and_says_so(self):
        """The fail-safe direction is MORE coverage, never less — a broken
        signal engine must not silently shrink a review to nothing."""
        import lens_signals
        orig = lens_signals.route_verdicts

        def boom(*a, **k):
            raise RuntimeError("engine down")

        lens_signals.route_verdicts = boom
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                _, payload = _cli(self.d)
        finally:
            lens_signals.route_verdicts = orig
        ids = {b["id"] for b in payload["deep"]} | set(
            (payload["sweep"] or {}).get("ids") or [])
        self.assertEqual(len(ids), len(lens.load_catalog()["lenses"]),
                         "a failed engine must fall open to full coverage")
        self.assertIn("lens applicability engine unavailable", err.getvalue())


class TestTheSkillDoesNotSpendAll(unittest.TestCase):
    """The second cause. The engine being wired in is worthless if the
    persona that drives reviews keeps passing the flag that turns it off."""

    FILES = ("skills/tp-engineering/SKILL.md",
             "skills/tp-engineering/references/em-session.md",
             "agents/tp-engineering.md")

    def _lines(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            return f.read().splitlines()

    def test_no_review_command_in_the_docs_passes_all(self):
        offenders = []
        for rel in self.FILES:
            for i, line in enumerate(self._lines(rel), 1):
                if "lens route --all" in line or "lens dispatch" in line \
                        and "--all" in line:
                    offenders.append(f"{rel}:{i}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "a review command still passes --all, which disables the "
            "applicability engine:\n" + "\n".join(offenders))

    def test_the_skill_explains_that_all_disables_the_engine(self):
        text = "\n".join(self._lines("skills/tp-engineering/SKILL.md"))
        self.assertIn("Do NOT pass `--all`", text)
        self.assertIn("n/a", text)

    def test_the_skill_no_longer_claims_every_lens_runs(self):
        """The old sentence — 'Every review applies the full catalog —
        nothing skipped' — is what made --all look mandatory."""
        text = "\n".join(self._lines("skills/tp-engineering/SKILL.md"))
        self.assertNotIn("applies the full catalog — nothing skipped", text)


if __name__ == "__main__":
    unittest.main()
