"""The defects a real review of aws/karpenter-provider-aws#9464 exposed.

taskplane v2.9.0 was run end-to-end against a live upstream Go repo by a
separate session. The harness held — no write reached reviewed source — but
nine defects surfaced that no self-review had found, because they only
appear at the scale and shape of somebody else's repository. These tests
pin each one so the next real repo cannot reopen them.

The worst was not a crash. `tp findings` printed `0 high · 3 med · 13 low`
while the findings file carried `class: regression` — which the engine's own
gate blocks — and the review was summarised as "0 confirmed regressions,
approve". The engine had the answer and the headline did not say it.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

import dashboard                                              # noqa: E402
import depgraph                                               # noqa: E402
import taskplane_lite as tp                                   # noqa: E402
TP = os.path.join(ROOT, "taskplane", "tp.py")


class B6_TheHeadlineCarriesTheBlockingSet(unittest.TestCase):
    """The exact failure: a regression in the file, silence in the headline."""

    REG = {"severity": "high", "class": "regression", "domain": "devops",
           "file": "pkg/x.go", "line": 53, "title": "non-AWS error escapes"}
    PRE = {"severity": "medium", "class": "pre-existing", "title": "old"}
    OBS = {"severity": "low", "class": "observation", "title": "taste"}

    def test_a_regression_is_named_in_the_headline(self):
        h = dashboard.headline_findings([self.REG, self.PRE, self.OBS], {})
        self.assertIn("1 BLOCK", h)
        self.assertIn("1R", h)

    def test_the_9464_shape_no_longer_reads_as_nothing_blocks(self):
        """0 high by severity, 1 blocker by class — the headline must not
        report only the first half."""
        soft = dict(self.REG, severity="medium")
        h = dashboard.headline_findings([soft, self.PRE], {})
        self.assertIn("0 high", h)          # severity says nothing is high
        self.assertIn("1 BLOCK", h)         # class says one blocks

    def test_no_blockers_says_so_explicitly(self):
        h = dashboard.headline_findings([self.PRE, self.OBS], {})
        self.assertIn("0 block", h)
        self.assertIn("2 to triage", h)

    def test_an_empty_review_claims_nothing(self):
        h = dashboard.headline_findings([], {"title": "clean"})
        self.assertNotIn("BLOCK", h)
        self.assertNotIn("to triage", h)

    def test_the_split_comes_from_the_engine_not_a_second_rule(self):
        """If the headline re-implemented the rule it could disagree with
        the gate. It must agree with loop.classify_findings by construction."""
        import loop
        rows = [self.REG, self.PRE, self.OBS,
                {"severity": "high", "file": "pkg/x.go", "title": "unclassed"}]
        engine = len(loop.classify_findings(rows, ["pkg/x.go"])["blockers"])
        h = dashboard.headline_findings(
            rows, {"impact": {"changed_files": ["pkg/x.go"]}})
        self.assertIn(f"{engine} BLOCK", h)

    def test_the_headline_still_prints_if_classification_explodes(self):
        """The headline is the never-skippable carrier. It may lose the
        split; it may never fail to print the counts."""
        h = dashboard.headline_findings(
            [{"severity": "high", "class": object()}], {"title": "t"})
        self.assertIn("high", h)


class B7_VersionReadableFromWhicheverManifestShipped(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tp-ver-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def manifest(self, where, version="9.9.9"):
        d = os.path.join(self.root, where)
        os.makedirs(d, exist_ok=True)
        with io.open(os.path.join(d, "plugin.json"), "w",
                     encoding="utf-8") as f:
            json.dump({"name": "taskplane", "version": version}, f)

    def test_the_claude_only_layout_that_ships_can_read_its_version(self):
        """The Claude package and the .plugin archive contain
        .claude-plugin/ and NOT .codex-plugin/ — tp version raised
        'missing authoritative version manifest' on every such install."""
        import tp as tpcli                                    # noqa: F401
        self.manifest(".claude-plugin")
        sys.path.insert(0, os.path.join(ROOT, "taskplane"))
        import importlib
        mod = importlib.import_module("tp")
        self.assertEqual(mod.plugin_version(self.root), "9.9.9")

    def test_codex_remains_authoritative_when_both_are_present(self):
        import importlib
        mod = importlib.import_module("tp")
        self.manifest(".claude-plugin", "1.1.1")
        self.manifest(".codex-plugin", "2.2.2")
        self.assertEqual(mod.plugin_version(self.root), "2.2.2")

    def test_no_manifest_at_all_still_raises(self):
        import importlib
        mod = importlib.import_module("tp")
        with self.assertRaises(tp.StateError):
            mod.plugin_version(self.root)

    def test_the_shipped_package_layout_is_covered_by_a_gate(self):
        """A CI check that only ever inspects the source tree cannot see a
        defect introduced by packaging. This test IS that gate."""
        import importlib
        mod = importlib.import_module("tp")
        self.manifest(".claude-plugin", "2.9.0")
        self.assertEqual(mod.plugin_version(self.root), "2.9.0")


class B1_SiblingLensContractsCanWriteTheirOwnFindings(unittest.TestCase):

    def lens(self, name, actions=30):
        return {"task_id": f"lens-{name}", "read_only": True,
                "write_allow": [f".em-review/lens-{name}/**"],
                "budget": {"max_actions": actions}, "coding": {}}

    def test_six_lenses_each_write_their_own_findings(self):
        """The marquee feature. Before this, the intersection of six
        write-allows was EMPTY and 4 of 6 lenses produced no evidence."""
        u = tp._union_contract([self.lens(n) for n in
                                ("security", "qa", "architecture",
                                 "testability", "devops", "sweep")])
        for n in ("security", "qa", "sweep"):
            ok, why = tp.screen_tool(
                u, "Write",
                {"file_path": f".em-review/lens-{n}/findings.json"}, "/tmp")
            self.assertTrue(ok, f"{n}: {why}")

    def test_reviewed_source_is_still_untouchable(self):
        u = tp._union_contract([self.lens("a"), self.lens("b")])
        for path in ("pkg/providers/amifamily/bootstrap/bottlerocket.go",
                     "go.mod", "../escape.py", "/etc/passwd"):
            ok, _ = tp.screen_tool(u, "Write", {"file_path": path}, "/tmp")
            self.assertFalse(ok, path)

    def test_different_roots_are_NOT_siblings_and_still_intersect(self):
        """The no-loosening boundary. Contracts that genuinely compete over
        separate trees must keep resolving to the empty intersection."""
        u = tp._union_contract([
            {"task_id": "a", "read_only": True, "coding": {},
             "write_allow": [".em-review/a/**"], "budget": {"max_actions": 9}},
            {"task_id": "b", "read_only": True, "coding": {},
             "write_allow": ["plan/b/**"], "budget": {"max_actions": 9}}])
        self.assertIsNone(u.get("_sibling_root"))
        for path in (".em-review/a/x", "plan/b/x"):
            ok, _ = tp.screen_tool(u, "Write", {"file_path": path}, "/tmp")
            self.assertFalse(ok, path)

    def test_a_writer_in_the_wave_disqualifies_it(self):
        u = tp._union_contract([
            self.lens("a"),
            {"task_id": "build", "read_only": False, "coding": {},
             "write_allow": [".em-review/b/**"], "budget": {"max_actions": 9}}])
        self.assertIsNone(u.get("_sibling_root"))

    def test_an_unrooted_write_allow_disqualifies_it(self):
        for bad in ("**", "*", "../out/**"):
            u = tp._union_contract([self.lens("a"),
                                    {"task_id": "x", "read_only": True,
                                     "coding": {}, "write_allow": [bad],
                                     "budget": {"max_actions": 9}}])
            self.assertIsNone(u.get("_sibling_root"), bad)

    def test_read_only_still_latches_on(self):
        u = tp._union_contract([self.lens("a"), self.lens("b")])
        self.assertTrue(u["read_only"])


class B2_TheWaveBudgetIsNotOneAgentsAllowance(unittest.TestCase):

    def lens(self, n):
        return {"task_id": f"lens-{n}", "read_only": True, "coding": {},
                "write_allow": [f".em-review/lens-{n}/**"],
                "budget": {"max_actions": 30}}

    def test_six_agents_granted_thirty_each_get_a_wave_budget(self):
        """min() gave six agents ONE agent's 30 and killed them at roughly
        their tenth action each, before any could write findings."""
        u = tp._union_contract([self.lens(str(i)) for i in range(6)])
        self.assertEqual(u["budget"]["max_actions"], 180)

    def test_competing_contracts_keep_the_minimum(self):
        u = tp._union_contract([
            {"task_id": "a", "read_only": True, "coding": {},
             "write_allow": [".em-review/a/**"], "budget": {"max_actions": 5}},
            {"task_id": "b", "read_only": True, "coding": {},
             "write_allow": ["plan/b/**"], "budget": {"max_actions": 90}}])
        self.assertEqual(u["budget"]["max_actions"], 5)


class B5_TheRepoOwnGoModuleResolvesInternally(unittest.TestCase):
    ROOT_MOD = "github.com/aws/karpenter-provider-aws"

    def manifests(self):
        files = {"go.mod": f"module {self.ROOT_MOD}\n\ngo 1.26.5\n"}
        return depgraph.manifest_modules(list(files),
                                         lambda p: files.get(p, ""))

    def test_the_root_module_is_recorded(self):
        self.assertEqual(self.manifests()[depgraph.ROOT_MODULE_KEY],
                         self.ROOT_MOD)

    def test_an_intra_repo_import_is_not_external(self):
        """Every pkg/** import landed as ext: — so `graph impact` reported
        2 modules and no call structure on a 256-module repo, and the
        review's only blocker had to be traced by hand."""
        m = self.manifests()
        self.assertEqual(
            depgraph._strip_root_module(f"{self.ROOT_MOD}/pkg/providers/x", m),
            "pkg/providers/x")

    def test_third_party_imports_stay_external(self):
        m = self.manifests()
        for spec in ("github.com/pelletier/go-toml/v2",
                     "github.com/aws/aws-sdk-go-v2/service/ec2",
                     "sigs.k8s.io/controller-runtime"):
            self.assertIsNone(depgraph._strip_root_module(spec, m), spec)

    def test_the_repo_itself_never_becomes_a_module(self):
        """A root module id would collapse every file into one node — the
        opposite failure, and worse."""
        m = self.manifests()
        self.assertIsNone(depgraph._strip_root_module(self.ROOT_MOD, m))
        self.assertNotIn("", m.values())

    def test_the_reserved_key_is_never_matched_as_a_declared_name(self):
        m = self.manifests()
        self.assertIsNone(depgraph._declared_target(
            depgraph.ROOT_MODULE_KEY, m))

    def test_a_root_package_json_is_still_skipped(self):
        """The root-skip was right for npm — a root package.json names the
        REPO, not something other code imports."""
        files = {"package.json": '{"name": "karpenter-monorepo"}'}
        m = depgraph.manifest_modules(list(files), lambda p: files.get(p, ""))
        self.assertEqual(m, {})

    def test_a_prefix_that_merely_starts_the_same_does_not_match(self):
        m = self.manifests()
        self.assertIsNone(depgraph._strip_root_module(
            self.ROOT_MOD + "-legacy/pkg/x", m))


class B3_InspectionNeverCostsButReleaseStillDoes(unittest.TestCase):

    def test_release_and_inspection_are_recognised(self):
        import importlib
        mod = importlib.import_module("tp")
        for cmd in ("tp status", "tp contracts", "tp version"):
            self.assertTrue(mod._is_release_command(cmd), cmd)

    def test_ack_is_unmetered_because_it_cannot_widen_scope(self):
        """REVERSED in v2.11.0, deliberately — this test used to assert the
        opposite and the assertion was wrong.

        v2.10.0 refused to exempt `clear` from metering (right: clearing
        leaves the workspace ungoverned, where the screener abstains, so an
        exhausted agent could un-govern itself) and lumped `ack` in with it
        (wrong). `ack` moves the run TOWARD the gate: it discharges an
        obligation the run already owes, cannot touch a file, and is bounded
        by the number of obligations issued. Refusing it deadlocked a real
        review — the Stop hook demanded `tp ack <id>`, the budget refused
        `tp ack <id>`, and the hook fired ~12 times with no reachable state
        that satisfied it. The abuse this appears to open (acking a render
        that never happened) was already closed by the render-observation
        ledger, which files it as `claimed_only`.
        """
        import importlib
        mod = importlib.import_module("tp")
        for cmd in ("tp ack o-1", "tp ack --status",
                    "tp ack o-1 --evidence x"):
            self.assertTrue(mod._is_release_command(cmd), cmd)

    def test_doing_work_is_not_a_release_command(self):
        import importlib
        mod = importlib.import_module("tp")
        for cmd in ("tp dod", "tp loop submit", "tp graph scan", "tp clear",
                    "tp clear --all",
                    "git commit -m 'clear the decks'", "rm -rf .taskplane",
                    "make status"):
            self.assertFalse(mod._is_release_command(cmd), cmd)

    def test_clear_is_still_metered_the_wall_did_not_move(self):
        """The half of the v2.10.0 decision that stands. If exempting `ack`
        ever drifts into exempting `clear`, an exhausted agent regains the
        ability to un-govern itself, which is the bypass the wall exists to
        prevent."""
        import importlib
        mod = importlib.import_module("tp")
        self.assertNotIn("clear", mod._RELEASE_VERBS)
        self.assertNotIn("clear", mod._CLOSING_VERBS)
        for cmd in ("tp clear", "tp clear --all", "tp clear --slot lens-x"):
            self.assertFalse(mod._is_release_command(cmd), cmd)
            self.assertFalse(mod._is_closing_command(cmd), cmd)

    def test_a_command_that_merely_MENTIONS_taskplane_is_not_taskplane(self):
        """Both callers scanned every token for a bare `tp`, so
        `echo tp clear` was exempt from the meter and
        `git commit -m "tp dod"` was refused as a completion. A program
        name is the FIRST word. This test found that in my own fix."""
        import importlib
        mod = importlib.import_module("tp")
        for cmd in ("echo tp clear", "grep -rn 'tp status' .",
                    'git commit -m "tp dod"', "cat tp.py"):
            self.assertIsNone(tp.taskplane_verb(cmd), cmd)
            self.assertFalse(mod._is_release_command(cmd), cmd)

    def test_an_exhausted_agent_can_still_REPORT_but_not_escape(self):
        """The deadlock, through the real screener.

        `tp clear` is itself an action, so once the ceiling was reached the
        agent could not release its own contract: the slot stayed on disk,
        joined every later union, and taxed every subsequent agent. Testing
        the predicate alone missed this — a mutation that stopped consulting
        it at all still passed.
        """
        ws = tempfile.mkdtemp(prefix="tp-deadlock-")
        home = tempfile.mkdtemp(prefix="tp-deadlock-home-")
        prev = os.environ.get("TASKPLANE_HOME")
        os.environ["TASKPLANE_HOME"] = home
        try:
            for cmd in (["init", "-q", "."], ["config", "user.email", "a@b.c"],
                        ["config", "user.name", "t"]):
                subprocess.run(["git"] + cmd, cwd=ws, capture_output=True)
            with io.open(os.path.join(ws, "a.py"), "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
            subprocess.run(["git", "commit", "-qm", "i"], cwd=ws,
                           capture_output=True)
            subprocess.run(
                [sys.executable, TP, "new", "--read-only", "--max-actions",
                 "1", "goal", "--workspace", ws],
                capture_output=True, text=True, encoding="utf-8",
                env=dict(os.environ))

            def screen(command):
                ev = {"tool_name": "Bash", "cwd": ws,
                      "tool_input": {"command": command}}
                r = subprocess.run([sys.executable, TP, "screen"],
                                   input=json.dumps(ev), capture_output=True,
                                   text=True, encoding="utf-8",
                                   env=dict(os.environ))
                try:
                    return json.loads(r.stdout).get("decision")
                except Exception:
                    return None          # abstain

            for _ in range(4):
                screen("grep -rn TODO .")          # burn the ceiling
            self.assertEqual(screen("grep -rn TODO ."), "block",
                             "the ceiling should be exhausted by now")
            # Inspection still gets through — a stuck agent can say why.
            self.assertNotEqual(screen("tp contracts"), "block")
            self.assertNotEqual(screen("tp status"), "block")
            # …but RELEASE does not. The field report asked for `tp clear` to
            # be exempt; that would let an exhausted agent un-govern its own
            # workspace, where the screener abstains. The wall stands, and
            # recovery happens from OUTSIDE with `tp clear --all`.
            self.assertEqual(screen("tp clear"), "block")
        finally:
            if prev is None:
                os.environ.pop("TASKPLANE_HOME", None)
            else:
                os.environ["TASKPLANE_HOME"] = prev
            shutil.rmtree(ws, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)

    def test_the_launcher_forms_still_resolve(self):
        for cmd, verb in (("tp clear", "clear"),
                          ("tp.py dod", "dod"),
                          ("python3 tp.py clear", "clear"),
                          ("TASKPLANE_TASK=lens-x tp clear", "clear"),
                          ("/opt/plugin/taskplane/tp.py dod", "dod")):
            self.assertEqual(tp.taskplane_verb(cmd), verb, cmd)


class B8_FindingsRenderWithoutAWidgetHost(unittest.TestCase):

    FINDINGS = [{"severity": "high", "class": "regression", "domain": "d",
                 "file": "a.go", "line": 1, "title": "t",
                 "scenario": "s", "fix": "f"}]

    def test_the_document_defines_the_palette_the_fragments_assume(self):
        """Fragments saved to disk rendered as unstyled text, so a host that
        could not show them inline left the review illegible."""
        doc = dashboard.standalone_document(
            [p["html"] for p in
             dashboard.render_findings_paged(self.FINDINGS, {"title": "t"})])
        self.assertTrue(doc.startswith("<!DOCTYPE html>"))
        for var in ("--text-danger", "--surface-1", "--font-mono"):
            self.assertIn(var, doc)
        self.assertIn("prefers-color-scheme", doc)

    def test_the_fragments_are_embedded_verbatim(self):
        pages = [p["html"] for p in
                 dashboard.render_findings_paged(self.FINDINGS, {"title": "t"})]
        doc = dashboard.standalone_document(pages)
        for frag in pages:
            self.assertIn(frag, doc)

    def test_the_title_is_escaped(self):
        doc = dashboard.standalone_document([], title='</title><script>x')
        self.assertNotIn("<script>x", doc)


class B7_NullSinksAreNotWrites(unittest.TestCase):

    C = {"task_id": "t", "read_only": True, "coding": {},
         "write_allow": [".em-review/**"]}

    def test_discarding_output_is_allowed(self):
        for cmd in ("ls > /dev/null", "tp graph impact --files a > /dev/null",
                    "go test ./... > /dev/null 2>&1"):
            ok, why = tp.screen_tool(self.C, "Bash", {"command": cmd}, "/tmp")
            self.assertTrue(ok, f"{cmd}: {why}")

    def test_a_real_file_is_still_a_write(self):
        for cmd in ("echo x > out.txt", "cat a > /dev/shm/b",
                    "go build > pkg/x.go"):
            ok, _ = tp.screen_tool(self.C, "Bash", {"command": cmd}, "/tmp")
            self.assertFalse(ok, cmd)


class TheSkillDocumentsCommandsThatExist(unittest.TestCase):

    def test_the_decision_form_in_the_skill_is_the_real_one(self):
        with io.open(os.path.join(ROOT, "skills", "tp-engineering",
                                  "SKILL.md"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("$TP decision new ", src)
        self.assertNotIn('$TP decision "', src)


if __name__ == "__main__":
    unittest.main()
