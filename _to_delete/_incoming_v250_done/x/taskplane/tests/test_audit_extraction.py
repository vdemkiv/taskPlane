"""t5 (R-0006 / D-0004) — audit extraction, proven by a byte-frozen differential.

The audit machinery (cadence counter, audit_due, router_audit auto-filing,
the em-gate half, the em audit brief) moves from loop.py into its own module
taskplane/audit.py. The move must change ZERO behavior. Proof by differential:

  Phase A (CAPTURE — done against the UNMODIFIED loop.py, committed first):
      python3 taskplane/tests/test_audit_extraction.py --regen
    ran every scenario below through the pre-extraction loop.py and froze the
    outputs — gate error lists, rewritten findings.json BYTES, trace event
    names, audit brief dicts, cadence sequences — under
    taskplane/tests/fixtures/audit/.

  Phase B (DIFFERENTIAL — this test file, every run):
    the SAME scenarios replay against the current code and every output must
    be BYTE-IDENTICAL to the frozen corpus.

  DO NOT regenerate the corpus to make the differential pass: the fixtures
  ARE the pre-extraction ground truth. A mismatch means the extraction
  changed behavior — fix the code, not the goldens.

Scenario coverage (the audit surface):
  * cadence: fresh / progression across the every-5 boundary / corrupt state
    (fail toward MORE coverage; counter read fails closed; record resets) /
    release flags / TASKPLANE_AUDIT_EVERY override, floor and garbage;
  * _audit_brief: not-due, due (with the recorded routing decision from a
    real git workspace), corrupt-state, release — full dict frozen;
  * router_audit: exactly-n/a conversion, string verdicts, domain field,
    unrouted/lensless/non-dict findings ignored;
  * _router_audit_gate: block + findings.json bytes after auto-filing,
    idempotent re-run (bytes stable, no duplicate filing), resolved rows stop
    blocking, no-decision meta untouched, trace event names;
  * gate integration through loop._engineering_review_errors (the caller
    seam that stays in loop.py);
  * decision-shape helpers (_routing_decision_from_meta, _routing_decision_of,
    _router_regression_key, _is_router_regression) on every accepted shape.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import audit  # noqa: E402
import loop  # noqa: E402
import lens  # noqa: E402
import taskplane_lite as tp  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXDIR = os.path.join(HERE, "fixtures", "audit")

# Same shape the sweep tests use: a recorded per-lens routing decision with
# two n/a lenses, a deep, a light, and a governance-forced deep.
DECISION = {
    "i18n": {"verdict": "n/a", "score": 0,
             "negative_evidence": ["no user-facing strings detected"]},
    "security": {"verdict": "deep", "score": 4, "evidence": ["auth surface"]},
    "backend": {"verdict": "light", "score": 1, "evidence": ["weak signal"]},
    "architecture": {"verdict": "deep (forced)", "score": 0,
                     "evidence": ["governance floor"]},
    "mobile": {"verdict": "n/a", "score": 0,
               "negative_evidence": ["no mobile surface"]},
}


def canonical(doc) -> bytes:
    """One frozen serialization for structured outputs."""
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _bare_ws(tmp: str) -> str:
    ws = os.path.join(tmp, "bare")
    os.makedirs(ws, exist_ok=True)
    return ws


def _git_ws(tmp: str) -> str:
    """A minimal committed git workspace with an EMPTY diff vs HEAD, so the
    audit brief's shadow routing decision is fully deterministic."""
    ws = os.path.join(tmp, "gws")
    os.makedirs(os.path.join(ws, "src"))
    with open(os.path.join(ws, "src", "a.py"), "w") as f:
        f.write("x = 1\n")
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "e@e"],
                ["git", "config", "user.name", "t"],
                ["git", "add", "-A"],
                ["git", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=ws, check=True)
    return ws


def _trace_events(ws: str) -> list:
    """Trace records with volatile fields (ts) dropped — event names plus the
    stable audit payload fields are part of the frozen contract."""
    path = os.path.join(ws, ".taskplane", "trace.jsonl")
    out = []
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                rec = json.loads(line)
                e = {"event": rec.get("event")}
                for k in ("count", "lenses", "reason", "reviews_completed"):
                    if k in rec:
                        e[k] = rec[k]
                out.append(e)
    return out


def _corrupt_cadence(mod, ws: str) -> str:
    path = mod._audit_path(ws)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{not json")
    return path


# --------------------------------------------------------------- scenarios
# Every scenario takes the module under test (`mod`) and a private tmp dir,
# and returns (structured_doc, {relative_name: raw_bytes}).

def scen_cadence_fresh(mod, tmp):
    ws = _bare_ws(tmp)
    doc = {
        "counter": mod.audit_counter(ws),
        "due": mod.audit_due(ws, {}),
        "every": mod.audit_every(),
        "brief": mod._audit_brief(ws, {}),
    }
    return doc, {}


def scen_cadence_progression(mod, tmp):
    ws = _bare_ws(tmp)
    seq = []
    for _ in range(6):
        seq.append({"counter": mod.audit_counter(ws),
                    "due": mod.audit_due(ws, {}),
                    "recorded": mod.record_audit_review(ws)})
    with open(mod._audit_path(ws), "rb") as f:
        raw = f.read()
    doc = {"sequence": seq, "counter_after": mod.audit_counter(ws)}
    return doc, {"audit.json": raw}


def scen_cadence_release(mod, tmp):
    ws = _bare_ws(tmp)
    states = [
        {},
        {"release_review": True},
        {"release": 1},
        {"tasks": [{"id": "x", "release": True}]},
        {"tasks": [{"id": "x", "type": "release"}]},
        {"tasks": [{"id": "x"}]},
        {"tasks": ["not-a-dict"]},
    ]
    doc = {"due_flags": [mod.audit_due(ws, s) for s in states],
           "brief_release": mod._audit_brief(ws, {"release_review": True})}
    return doc, {}


def scen_cadence_corrupt(mod, tmp):
    ws = _bare_ws(tmp)
    _corrupt_cadence(mod, ws)
    due = mod.audit_due(ws, {})
    try:
        mod.audit_counter(ws)
        err = None
    except tp.StateError as exc:
        err = {"type": type(exc).__name__,
               "mentions_corrupt": "corrupt audit cadence state" in str(exc)}
    brief = mod._audit_brief(ws, {})
    reset = mod.record_audit_review(ws)   # corrupt → reset, never stall
    with open(mod._audit_path(ws), "rb") as f:
        raw = f.read()
    doc = {"due": due, "counter_error": err, "brief": brief,
           "record_after_corrupt": reset,
           "counter_after_reset": mod.audit_counter(ws)}
    return doc, {"audit.json": raw}


def scen_cadence_env(mod, tmp):
    ws = _bare_ws(tmp)
    out = {}
    for raw in ("2", "0", "-3", "1", "often", "7", ""):
        with mock.patch.dict(os.environ, {"TASKPLANE_AUDIT_EVERY": raw}):
            out[raw or "<unset-like-blank>"] = {
                "every": mod.audit_every(),
                "due_fresh": mod.audit_due(ws, {}),
            }
    return {"by_value": out, "default": mod.audit_every()}, {}


def scen_brief_due(mod, tmp):
    ws = _git_ws(tmp)
    for _ in range(4):
        mod.record_audit_review(ws)      # upcoming review #5 → audit due
    return {"brief": mod._audit_brief(ws, {}),
            "due": mod.audit_due(ws, {})}, {}


def scen_brief_corrupt_git(mod, tmp):
    ws = _git_ws(tmp)
    _corrupt_cadence(mod, ws)
    return {"brief": mod._audit_brief(ws, {})}, {}


def scen_router_audit(mod, tmp):
    ws = _bare_ws(tmp)
    findings = [
        {"lens": "i18n", "severity": "med", "title": "hardcoded string",
         "file": "src/a.py", "line": 3},
        {"lens": "security", "severity": "high", "title": "real issue"},
        {"lens": "backend", "severity": "low", "title": "light note"},
        {"lens": "architecture", "severity": "med", "title": "floored"},
        {"lens": "mobile", "severity": "low", "title": "second n/a miss"},
    ]
    doc = {
        "basic": mod.router_audit(ws, DECISION, findings),
        "string_verdicts": mod.router_audit(
            ws, {"mobile": "n/a", "qa": "deep", "i18n": "NA"},
            [{"domain": "mobile", "severity": "low", "title": "x"},
             {"domain": "qa", "severity": "low", "title": "y"},
             {"lens": "i18n", "severity": "med", "title": "z"}]),
        "ignored": mod.router_audit(ws, DECISION, [
            {"severity": "high", "title": "no lens field"},
            {"lens": "does-not-exist", "severity": "high",
             "title": "unknown lens"},
            "not-a-dict"]),
        "non_dict_decision": mod.router_audit(ws, "not-a-dict", [
            {"lens": "i18n", "severity": "med", "title": "q"}]),
        "empty": mod.router_audit(ws, DECISION, None),
    }
    return doc, {}


def scen_decision_shapes(mod, tmp):
    v2_cov = {"i18n": {"verdict": "n/a", "score": 0,
                       "negative_evidence": ["none"]},
              "qa": {"verdict": "deep", "score": 3, "evidence": ["e"]},
              "legacy": "sweep"}
    routing_v2 = {"lenses": [
        {"id": "i18n", "tier": "n/a", "verdict": "n/a", "score": 0,
         "negative_evidence": ["no strings"]},
        {"id": "security", "tier": "deep", "verdict": "deep", "score": 4,
         "evidence": ["auth"], "reasons": ["ignored when evidence set"]},
        {"id": "qa", "tier": "light", "verdict": "light", "score": 1,
         "reasons": ["fallback reasons"]},
    ]}
    legacy_routing = {"lenses": [{"id": "qa", "tier": "deep"}]}
    reg_row = {"owner": "router", "class": "regression",
               "domain": "router+i18n",
               "finding": {"title": "t", "file": "f.py", "line": 7}}
    doc = {
        "from_meta_decision": mod._routing_decision_from_meta(
            {"routing_decision": DECISION}),
        "from_meta_v2_coverage": mod._routing_decision_from_meta(
            {"lens_coverage": v2_cov}),
        "from_meta_legacy": mod._routing_decision_from_meta(
            {"lens_coverage": {"qa": "sweep"}}),
        "from_meta_none": mod._routing_decision_from_meta(None),
        "of_v2": mod._routing_decision_of(routing_v2),
        "of_legacy": mod._routing_decision_of(legacy_routing),
        "of_empty": mod._routing_decision_of({}),
        "regression_key": list(mod._router_regression_key(reg_row)),
        "regression_key_bare": list(mod._router_regression_key(
            {"domain": "router+x"})),
        "is_regression": [
            mod._is_router_regression(reg_row),
            mod._is_router_regression({"owner": "router", "class": "observation"}),
            mod._is_router_regression({"class": "regression"}),
            mod._is_router_regression("not-a-dict"),
        ],
    }
    return doc, {}


def _gate_ws(tmp, meta, rows):
    ws = os.path.join(tmp, "gatews")
    d = os.path.join(ws, ".em-review")
    os.makedirs(d)
    path = os.path.join(d, "findings.json")
    with open(path, "w") as f:
        json.dump({"meta": meta, "findings": rows}, f)
    return ws, path


def scen_gate_direct(mod, tmp):
    meta = {"routing_decision": DECISION}
    rows = [
        {"lens": "i18n", "severity": "med", "class": "observation",
         "title": "hardcoded locale string", "file": "src/a.py", "line": 3},
        {"lens": "backend", "severity": "low", "title": "light note"},
        {"lens": "architecture", "severity": "med", "title": "floored"},
    ]
    ws, path = _gate_ws(tmp, meta, rows)
    doc1 = tp.load_json(path)
    errs = mod._router_audit_gate(ws, path, doc1, doc1["meta"],
                                  doc1["findings"])
    with open(path, "rb") as f:
        raw_after = f.read()
    trace_first = _trace_events(ws)
    # idempotent re-run: same errors, no duplicate filing, bytes stable
    doc2 = tp.load_json(path)
    errs2 = mod._router_audit_gate(ws, path, doc2, doc2["meta"],
                                   doc2["findings"])
    with open(path, "rb") as f:
        raw_rerun = f.read()
    # resolving the filed regression stops the block
    doc3 = tp.load_json(path)
    for r in doc3["findings"]:
        if r.get("owner") == "router":
            r["status"] = "accepted"
    tp.atomic_write_json(path, doc3, indent=2)
    doc4 = tp.load_json(path)
    errs3 = mod._router_audit_gate(ws, path, doc4, doc4["meta"],
                                   doc4["findings"])
    doc = {"errors": errs, "errors_rerun": errs2,
           "rerun_bytes_identical": raw_rerun == raw_after,
           "errors_after_resolve": errs3,
           "trace_after_first_run": trace_first,
           "trace_after_all_runs": _trace_events(ws)}
    return doc, {"findings-after.json": raw_after,
                 "findings-rerun.json": raw_rerun}


def scen_gate_no_decision(mod, tmp):
    rows = [{"lens": "i18n", "severity": "med", "title": "note"}]
    ws, path = _gate_ws(tmp, {"tests": "ok"}, rows)
    with open(path, "rb") as f:
        before = f.read()
    doc1 = tp.load_json(path)
    errs = mod._router_audit_gate(ws, path, doc1, doc1["meta"],
                                  doc1["findings"])
    with open(path, "rb") as f:
        after = f.read()
    doc = {"errors": errs, "file_untouched": before == after,
           "trace": _trace_events(ws)}
    return doc, {}


def _em_review_ws(tmp, name, na=("i18n",), findings_rows=()):
    """Full-catalog v2 (contract:findings-v2) em-review fixture workspace."""
    ws = os.path.join(tmp, name)
    d = os.path.join(ws, ".em-review")
    os.makedirs(d)
    coverage = {}
    for e in lens.load_catalog()["lenses"]:
        lid = e["id"]
        if lid in na:
            coverage[lid] = {"verdict": "n/a", "score": 0,
                             "negative_evidence": ["no signal detected"]}
        else:
            coverage[lid] = {"verdict": "deep", "score": 3,
                             "evidence": ["signal"]}
    meta = {"lens_coverage": coverage, "impact": {"touched": []},
            "tests": "pytest -q: pass",
            "gate": {"verdict": "recommend-pass"}, "audit": True}
    with open(os.path.join(d, "findings.json"), "w") as f:
        json.dump({"meta": meta, "findings": list(findings_rows)}, f)
    with open(os.path.join(d, "report.md"), "w") as f:
        f.write("# review\nok\n")
    return ws, os.path.join(d, "findings.json")


def scen_gate_integration(mod, tmp):
    """Through loop._engineering_review_errors — the caller seam that STAYS
    in loop.py and must keep invoking the (moved) gate identically."""
    del mod  # the integration seam is loop's, whichever module owns the gate
    # n/a-lens finding: auto-filed, blocks, findings.json rewritten
    ws1, p1 = _em_review_ws(tmp, "w1", findings_rows=[
        {"lens": "i18n", "severity": "med", "class": "observation",
         "title": "hardcoded locale string", "file": "src/a.py"}])
    errs1 = loop._engineering_review_errors(ws1, None)
    with open(p1, "rb") as f:
        raw1 = f.read()
    errs1_rerun = loop._engineering_review_errors(ws1, None)
    with open(p1, "rb") as f:
        raw1_rerun = f.read()
    # deep-lens finding: nothing filed, no block
    ws2, p2 = _em_review_ws(tmp, "w2", findings_rows=[
        {"lens": "security", "severity": "med", "class": "observation",
         "title": "note on a deep lens"}])
    errs2 = loop._engineering_review_errors(ws2, None)
    with open(p2) as f:
        filed2 = [r for r in json.load(f)["findings"]
                  if r.get("owner") == "router"]
    # clean v2 review: gate passes
    ws3, _ = _em_review_ws(tmp, "w3", findings_rows=[])
    errs3 = loop._engineering_review_errors(ws3, None)
    doc = {"errors_na_block": errs1,
           "errors_na_rerun": errs1_rerun,
           "rerun_bytes_identical": raw1 == raw1_rerun,
           "trace_na_ws": _trace_events(ws1),
           "errors_deep": errs2, "filed_for_deep": filed2,
           "errors_clean": errs3}
    return doc, {"findings-na-after.json": raw1}


SCENARIOS = {
    "cadence-fresh": scen_cadence_fresh,
    "cadence-progression": scen_cadence_progression,
    "cadence-release": scen_cadence_release,
    "cadence-corrupt": scen_cadence_corrupt,
    "cadence-env": scen_cadence_env,
    "brief-due": scen_brief_due,
    "brief-corrupt-git": scen_brief_corrupt_git,
    "router-audit": scen_router_audit,
    "decision-shapes": scen_decision_shapes,
    "gate-direct": scen_gate_direct,
    "gate-no-decision": scen_gate_no_decision,
    "gate-integration": scen_gate_integration,
}


def run_scenario(fn, mod):
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.dict(os.environ):
            os.environ.pop("TASKPLANE_AUDIT_EVERY", None)
            return fn(mod, tmp)


def regen():
    os.makedirs(FIXDIR, exist_ok=True)
    for name, fn in SCENARIOS.items():
        doc, raws = run_scenario(fn, loop)
        with open(os.path.join(FIXDIR, name + ".json"), "wb") as f:
            f.write(canonical(doc))
        for rel, raw in raws.items():
            with open(os.path.join(FIXDIR, f"{name}--{rel}"), "wb") as f:
                f.write(raw)
        print("froze", name)


class DifferentialMixin:
    """Replay every scenario and byte-compare against the frozen corpus."""
    mod = loop

    def _frozen(self, rel: str) -> bytes:
        path = os.path.join(FIXDIR, rel)
        self.assertTrue(
            os.path.exists(path),
            f"missing frozen fixture {path} — the capture phase "
            "(--regen against the UNMODIFIED loop.py) must be committed "
            "before any extraction edit")
        with open(path, "rb") as f:
            return f.read()

    def test_differential_byte_identity(self):
        for name, fn in SCENARIOS.items():
            with self.subTest(scenario=name):
                doc, raws = run_scenario(fn, self.mod)
                self.assertEqual(
                    canonical(doc), self._frozen(name + ".json"),
                    f"scenario '{name}' diverged from the pre-extraction "
                    "behavior")
                for rel, raw in raws.items():
                    self.assertEqual(
                        raw, self._frozen(f"{name}--{rel}"),
                        f"scenario '{name}' artifact '{rel}' is not "
                        "byte-identical to the pre-extraction bytes")


class TestDifferentialViaLoop(DifferentialMixin, unittest.TestCase):
    """The corpus replayed through loop.<name> — the re-export surface every
    existing caller (and test) uses."""
    mod = loop


class TestDifferentialViaAudit(DifferentialMixin, unittest.TestCase):
    """The corpus replayed through audit.<name> directly — the extracted
    module must be byte-identical to the pre-extraction loop.py too."""
    mod = audit


# The full moved surface: every one of these must resolve at loop.<name>
# (zero caller churn) AND live bodily in audit.py only.
MOVED_CONSTANTS = ("AUDIT_FILE", "AUDIT_EVERY_DEFAULT")
MOVED_FUNCTIONS = (
    "_audit_path", "audit_every", "audit_counter", "record_audit_review",
    "_release_review_flagged", "audit_due", "router_audit",
    "_routing_decision_from_meta", "_is_router_regression",
    "_router_regression_key", "_router_audit_gate", "_routing_decision_of",
    "_audit_brief",
)


def _src(mod) -> str:
    with open(mod.__file__, encoding="utf-8") as f:
        return f.read()


class TestExtractionStructure(unittest.TestCase):
    def test_every_moved_name_still_resolves_at_loop(self):
        """Re-export contract: loop.<name> IS audit.<name> — same objects, so
        existing callers and monkeypatching tests work unchanged."""
        for name in MOVED_CONSTANTS + MOVED_FUNCTIONS:
            with self.subTest(name=name):
                self.assertTrue(hasattr(loop, name),
                                f"loop.{name} no longer resolves")
                self.assertIs(getattr(loop, name), getattr(audit, name),
                              f"loop.{name} is not the audit.py object")

    def test_loop_contains_no_audit_function_bodies(self):
        src = _src(loop)
        for name in MOVED_FUNCTIONS:
            with self.subTest(name=name):
                self.assertNotIn(
                    f"def {name}(", src,
                    f"loop.py still defines {name} — the body must live "
                    "ONLY in audit.py")
        for name in MOVED_CONSTANTS:
            with self.subTest(name=name):
                self.assertNotIn(f"{name} = ", src)
        self.assertIn("from audit import", src)

    def test_audit_defines_the_whole_moved_surface(self):
        src = _src(audit)
        for name in MOVED_FUNCTIONS:
            self.assertIn(f"def {name}(", src)
        for name in MOVED_CONSTANTS:
            self.assertIn(f"{name} = ", src)

    def test_loop_shrank_by_the_moved_region(self):
        """Pre-extraction loop.py was 3191 lines; the moved audit region was
        ~255. Pin the shrink so the bodies cannot quietly creep back."""
        with open(loop.__file__, encoding="utf-8") as f:
            n = len(f.readlines())
        self.assertLessEqual(
            n, 2990, f"loop.py is {n} lines — the audit extraction shrink "
            "(3191 → ~2961) has been undone or eroded")

    def test_gate_math_stays_single_sourced_in_loop(self):
        """audit.py CALLS the frozen finding_blocks rule; it must never grow
        its own copy of the gate math."""
        self.assertNotIn("def finding_blocks(", _src(audit))
        self.assertIn("def finding_blocks(", _src(loop))
        self.assertIn("finding_blocks(", _src(audit))   # the call-back seam

    def test_audit_module_joins_the_agnostic_module_scan(self):
        """test_review_wave.py pins loop.py/lens.py free of any coupling to
        the wave-runner plugin; the design extends that pin to audit.py."""
        src = _src(audit).lower()
        for banned in ("work" + "flow", "review-wave"):
            self.assertNotIn(banned, src,
                             f"audit.py must stay {banned}-agnostic")


if __name__ == "__main__":
    if "--regen" in sys.argv:
        regen()
    else:
        unittest.main()
