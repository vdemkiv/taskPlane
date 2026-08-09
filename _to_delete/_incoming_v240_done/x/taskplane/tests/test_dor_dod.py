"""DoR/DoD visibility wiring (v1.0.0):
- DoR is computed every loop step and now traced with blockers/warnings, and the
  dashboard renders a DoR strip from it.
- The mechanical DoD (scope-diff + KB-lint) now runs at the sign-off gate
  (loop._signoff_dod) and is surfaced in the next_action payload + dashboard.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loop  # noqa: E402
import taskplane_lite as tp  # noqa: E402
import dashboard  # noqa: E402
import lens  # noqa: E402


def _git(ws, *a):
    subprocess.run(["git", *a], cwd=ws, capture_output=True)


def _repo(tmp):
    ws = os.path.join(tmp, "ws")
    os.makedirs(os.path.join(ws, "src"))
    open(os.path.join(ws, "src", "a.py"), "w").write("x = 1\n")
    open(os.path.join(ws, "README.md"), "w").write("# readme\n")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "e@e")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    return ws


def _head(ws):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws,
                          capture_output=True, text=True).stdout.strip()


class TestSignoffDoD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _state(self, base, scope=("src/**",)):
        return {"goal": "g", "step": "signoff", "current_task": 0,
                "max_fix_cycles": 2, "checkpoints": ["plan", "em"],
                "tasks": [{"id": "t1", "scope": list(scope),
                           "tests": "true", "criteria": ["works"]}],
                "baseline": base}

    def _review_evidence(self, ws):
        coverage = {x["id"]: "sweep"
                    for x in lens.load_catalog()["lenses"]}
        os.makedirs(os.path.join(ws, ".em-review"), exist_ok=True)
        with open(os.path.join(ws, ".em-review", "report.md"), "w") as f:
            f.write("# Engineering review\n\nNo blockers.\n")
        with open(os.path.join(ws, ".em-review", "findings.json"), "w") as f:
            json.dump({"meta": {"lens_coverage": coverage, "impact": {},
                                "tests": ["true"],
                                "gate": {"verdict": "recommend-pass"}},
                       "findings": []}, f)

    def test_in_scope_change_passes(self):
        ws = _repo(self.tmp)
        base = _head(ws)
        open(os.path.join(ws, "src", "a.py"), "w").write("x = 2\n")  # in scope
        self._review_evidence(ws)
        d = loop._signoff_dod(ws, self._state(base))
        self.assertTrue(d["passed"], d["errors"])

    def test_out_of_scope_change_fails(self):
        ws = _repo(self.tmp)
        base = _head(ws)
        open(os.path.join(ws, "README.md"), "w").write("# changed\n")  # NOT src
        d = loop._signoff_dod(ws, self._state(base))
        self.assertFalse(d["passed"])
        self.assertTrue(any("diff_scope" in e for e in d["errors"]), d["errors"])

    def test_loop_owned_artifacts_do_not_fail_signoff_scope(self):
        # design/, plan/, specs/ etc. are authored by governed steps under
        # their own write-allow contracts + human gates; the sign-off
        # aggregate must not flag them (t-task DoD REQUIRES committing
        # design/contract.json — flagging that commit was a contradiction).
        ws = _repo(self.tmp)
        base = _head(ws)
        os.makedirs(os.path.join(ws, "design"), exist_ok=True)
        open(os.path.join(ws, "design", "contract.json"), "w").write("{}\n")
        os.makedirs(os.path.join(ws, "plan"), exist_ok=True)
        open(os.path.join(ws, "plan", "tasks.json"), "w").write("[]\n")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-qm", "loop artifacts")
        self._review_evidence(ws)
        d = loop._signoff_dod(ws, self._state(base))
        self.assertTrue(d["passed"], d["errors"])

    def test_signoff_aggregate_enforces_default_out_of_scope(self):
        # STRICTER than before: a denied-family file reachable only through
        # a WILDCARD scope blocks at sign-off (no literal, no provenance
        # override) — the old synthetic contract had no out_of_scope at all.
        ws = _repo(self.tmp)
        base = _head(ws)
        os.makedirs(os.path.join(ws, "src", "secrets"), exist_ok=True)
        open(os.path.join(ws, "src", "secrets", "k.pem"), "w").write("x\n")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-qm", "secret")
        self._review_evidence(ws)
        d = loop._signoff_dod(ws, self._state(base, scope=("src/**",)))
        self.assertFalse(d["passed"])
        self.assertTrue(any("out_of_scope" in e for e in d["errors"]),
                        d["errors"])

    def test_bare_string_na_coverage_blocks_signoff(self):
        # EM v3 tightening: the one disposition that REDUCES coverage (n/a)
        # must carry machine-checkable negative evidence; a bare string
        # 'n/a' also slipped past the router-audit backstop.
        ws = _repo(self.tmp)
        base = _head(ws)
        open(os.path.join(ws, "src", "a.py"), "w").write("x = 2\n")
        self._review_evidence(ws)
        path = os.path.join(ws, ".em-review", "findings.json")
        doc = json.load(open(path))
        lid = sorted(doc["meta"]["lens_coverage"])[0]
        doc["meta"]["lens_coverage"][lid] = "n/a"
        json.dump(doc, open(path, "w"))
        d = loop._signoff_dod(ws, self._state(base))
        self.assertFalse(d["passed"])
        self.assertTrue(any("negative evidence" in e for e in d["errors"]),
                        d["errors"])

    def test_dict_na_with_negative_evidence_passes(self):
        ws = _repo(self.tmp)
        base = _head(ws)
        open(os.path.join(ws, "src", "a.py"), "w").write("x = 2\n")
        self._review_evidence(ws)
        path = os.path.join(ws, ".em-review", "findings.json")
        doc = json.load(open(path))
        lid = sorted(doc["meta"]["lens_coverage"])[0]
        doc["meta"]["lens_coverage"][lid] = {
            "verdict": "n/a",
            "negative_evidence": ["0 i18n markers across the diff"]}
        json.dump(doc, open(path, "w"))
        d = loop._signoff_dod(ws, self._state(base))
        self.assertTrue(d["passed"], d["errors"])

    def test_kb_lint_folds_into_dod(self):
        ws = _repo(self.tmp)
        base = _head(ws)
        ctx = os.path.join(tp.kb_root(ws), "context")   # isolated by conftest
        os.makedirs(ctx, exist_ok=True)
        open(os.path.join(ctx, "product.md"), "w").write("Paid SKU ~15k/yr\n")
        d = loop._signoff_dod(ws, self._state(base))
        self.assertFalse(d["passed"])
        self.assertTrue(any("kb_lint" in e for e in d["errors"]), d["errors"])


class TestPayloadAndTrace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_signoff_next_action_carries_dod(self):
        ws = _repo(self.tmp)
        base = _head(ws)
        loop.init(ws, "g")
        st = loop.load(ws)
        st.update({"step": "signoff",
                   "tasks": [{"id": "t1", "scope": ["src/**"]}],
                   "baseline": base})
        loop.save(ws, st)
        out = loop.next_action(ws)
        self.assertEqual(out["step"], "signoff")
        self.assertIn("dod", out)
        self.assertIn("passed", out["dod"])

    def test_loop_step_trace_carries_dor_detail(self):
        ws = _repo(self.tmp)
        loop.init(ws, "g")          # pm
        loop.gate(ws, "pass")       # pm -> plan
        loop.next_action(ws)        # plan step -> traces loop_step + DoR detail
        tr = [json.loads(ln) for ln in
              open(os.path.join(tp.tp_dir(ws), "trace.jsonl")) if ln.strip()]
        steps = [e for e in tr if e.get("event") == "loop_step"]
        self.assertTrue(steps)
        self.assertIn("dor_ready", steps[-1])
        self.assertIn("dor_blockers", steps[-1])


class TestDashboardSurfaces(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_widget_shows_dor_strip_and_dod_verdict(self):
        ws = _repo(self.tmp)
        base = _head(ws)
        loop.init(ws, "g")
        loop.gate(ws, "pass")       # -> plan
        loop.next_action(ws)        # emits a loop_step trace (DoR)
        st = loop.load(ws)
        st.update({"step": "signoff",
                   "tasks": [{"id": "t1", "scope": ["src/**"]}],
                   "baseline": base})
        loop.save(ws, st)
        frag = dashboard.widget(ws)
        self.assertIn("DoR", frag)   # entry-gate strip
        self.assertIn("DoD", frag)   # sign-off verdict


if __name__ == "__main__":
    unittest.main()
