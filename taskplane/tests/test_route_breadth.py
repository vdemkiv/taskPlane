"""Routing breadth is RECORDED, not inferred — `--all` vs a route that
happened to select the whole catalog.

WHY (the real defect, not a hypothetical): the eval rubric scores whether a
review ROUTED its lenses (the applicability engine picked them) or FORCED the
whole catalog with `--all`, which switches that engine OFF. `lens.py` decides
that internally — it branches on `breadth != "all"` — and never wrote the
fact down: the `lens_route` trace row carries `step` and `lenses` only, and
the derivation ledger strips flags by design. So `scripts/eval_record.py`
INFERS the breadth from the routed set (routed-set superset of the catalog =>
"all").

That inference is not merely wrong at some far edge. Route v2 emits an output
entry for EVERY catalog lens — n/a entries included, carrying their negative
evidence, because coverage honesty needs them — so the routed set of a
signal-driven review is ALWAYS the full catalog, and the inference calls
EVERY such review `--all`. The engine being on and the engine being off are
currently the same record.

These tests pin (a) that the two cases now record DIFFERENT values, and
(b) that recording them changed no routing decision, payload, brief or
exception path — proven differentially against `HEAD:taskplane/lens.py`.
"""

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
REPO = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)

import lens                      # noqa: E402
import lens_signals              # noqa: E402
import taskplane_lite as tp      # noqa: E402

CATALOG = lens.load_catalog()
CATALOG_IDS = [l["id"] for l in CATALOG["lenses"]]

# A diff with enough shape to summon several lenses on both the legacy and
# the signal-driven path (auth code, a web component, a doc).
DIFF = {
    "src/auth/session.py": "import os\nSECRET = os.environ['S']\n",
    "web/components/Card.tsx": "export const Card = () => <div/>;\n",
    "docs/guide.md": "# guide\n",
}
DIFF_FILES = sorted(DIFF)


# ----------------------------------------------------------------- helpers

def make_ws(governed: bool = True) -> str:
    """A throwaway workspace holding the diff.

    `governed` mirrors the real precondition: a run under a contract already
    has a `.taskplane/` record to append to. Routing must never CREATE that
    directory as a side effect — checked-in lens fixtures are passed to
    `route(workspace=...)` by the existing suites, and a router that seeded a
    governance dir inside the repo would dirty the tree on every run.
    """
    ws = tempfile.mkdtemp(prefix="tp-breadth-")
    for rel, body in DIFF.items():
        p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
    if governed:
        os.makedirs(os.path.join(ws, ".taskplane"), exist_ok=True)
    return ws


def make_git_ws() -> str:
    """A governed workspace that is also a git repo with an uncommitted diff,
    so `route_git_diff` (the path the em step takes with breadth='all') can
    be exercised end to end."""
    ws = make_ws()

    def git(*args):
        subprocess.run(["git", *args], cwd=ws, capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       check=False)

    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-qm", "base")
    with open(os.path.join(ws, "src", "auth", "session.py"), "a",
              encoding="utf-8") as f:
        f.write("TOKEN = 'x'\n")
    return ws


def breadth_rows(ws: str) -> list:
    """Every recorded routing-breadth row in this workspace's trace."""
    path = os.path.join(ws, ".taskplane", "trace.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("event") == lens.LENS_BREADTH_EVENT:
                out.append(row)
    return out


def only_row(ws: str) -> dict:
    rows = breadth_rows(ws)
    assert len(rows) == 1, f"expected exactly one breadth row, got {rows}"
    return rows[0]


def inferred_breadth(lens_ids) -> str:
    """The recorder's CURRENT inference, reimplemented here.

    Deliberately a local copy of `scripts/eval_record.breadth_of`'s rule
    (routed-set superset of the catalog => "all") rather than an import: the
    point of the test is that this rule is not sufficient, so it must be
    stated in the test that disproves it, and it must keep failing the same
    way even if the recorder is repaired.
    """
    return "all" if set(CATALOG_IDS) <= set(lens_ids) else "routed"


def canonical(routing) -> str:
    return json.dumps(routing, sort_keys=True, indent=1, default=str)


def load_baseline_lens():
    """`HEAD:taskplane/lens.py` as its own module, from a scratch dir OUTSIDE
    the repo, so the differential compares two versions of this file while
    every module it imports (taskplane_lite, lens_signals, path_roles) stays
    the single shared one — the only variable is lens.py itself."""
    src = subprocess.run(["git", "show", "HEAD:taskplane/lens.py"], cwd=REPO,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", check=False)
    if src.returncode != 0 or not src.stdout:
        raise unittest.SkipTest(f"cannot read baseline lens.py: {src.stderr}")
    scratch = tempfile.mkdtemp(prefix="lens-baseline-")
    path = os.path.join(scratch, "lens_at_head.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src.stdout)
    spec = importlib.util.spec_from_file_location("lens_at_head", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # not registered in sys.modules
    mod._scratch_dir = scratch
    return mod


def boom(*a, **k):
    raise RuntimeError("engine down")


# The differential table. Each entry routes ONE case against whichever
# lens module it is handed, in a workspace built fresh for that call — so
# neither module can observe the other's side effects (including the trace
# row this change adds).
def case_legacy_routed(mod, ws):
    return mod.route(DIFF_FILES, task_type="feature", catalog=CATALOG,
                     workspace=ws)


def case_forced_all(mod, ws):
    return mod.route(DIFF_FILES, task_type="feature", catalog=CATALOG,
                     breadth="all", stage="review", workspace=ws)


def case_v2_routed(mod, ws):
    return mod.route(DIFF_FILES, task_type="feature", catalog=CATALOG,
                     stage="review", workspace=ws)


def case_v2_forced_lenses(mod, ws):
    return mod.route(DIFF_FILES, task_type="feature", catalog=CATALOG,
                     stage="review", only=["security", "architecture"],
                     workspace=ws)


def case_v2_skipped_lenses(mod, ws):
    return mod.route(DIFF_FILES, task_type="feature", catalog=CATALOG,
                     stage="review", skip=["security"], workspace=ws)


def case_engine_failure_fallback(mod, ws):
    with mock.patch.object(lens_signals, "route_verdicts", boom):
        return mod.route(DIFF_FILES, task_type="feature", catalog=CATALOG,
                         stage="review", workspace=ws)


def case_prime_scope(mod, ws):
    return mod.prime_scope(["src/auth/**"], task_type="feature",
                           catalog=CATALOG, workspace=ws)


def case_no_workspace(mod, ws):
    return mod.route(DIFF_FILES, task_type="feature", catalog=CATALOG)


def case_git_diff_all(mod, ws):
    return mod.route_git_diff(ws, base="HEAD", task_type="feature",
                              catalog=CATALOG, breadth="all", stage=None)


CASES = [
    ("legacy_routed", case_legacy_routed, make_ws),
    ("forced_all", case_forced_all, make_ws),
    ("v2_routed", case_v2_routed, make_ws),
    ("v2_forced_lenses", case_v2_forced_lenses, make_ws),
    ("v2_skipped_lenses", case_v2_skipped_lenses, make_ws),
    ("engine_failure_fallback", case_engine_failure_fallback, make_ws),
    ("prime_scope", case_prime_scope, make_ws),
    ("no_workspace", case_no_workspace, make_ws),
    ("git_diff_all", case_git_diff_all, make_git_ws),
]


class _WsCleanup(unittest.TestCase):
    def setUp(self):
        self._made = []

    def tearDown(self):
        for d in self._made:
            shutil.rmtree(d, ignore_errors=True)

    def ws(self, factory=make_ws, **kw):
        d = factory(**kw)
        self._made.append(d)
        return d


# ------------------------------------------------ what the record now says

class TestRecordedBreadthDistinguishesForcedAllFromFullRoute(_WsCleanup):
    """The scored distinction, now readable from the record alone."""

    def test_engine_off_and_engine_on_record_different_values(self):
        """`--all` (engine off) and a signal route that named every lens
        (engine on) must not produce the same record — that collision is the
        defect, and it is what the rubric silently mis-scores."""
        off_ws, on_ws = self.ws(), self.ws()
        case_forced_all(lens, off_ws)
        case_v2_routed(lens, on_ws)
        off, on = only_row(off_ws), only_row(on_ws)

        self.assertFalse(off["engine_ran"])
        self.assertTrue(on["engine_ran"])
        self.assertEqual(off["requested_breadth"], "all")
        self.assertEqual(on["requested_breadth"], "routed")
        self.assertNotEqual(
            {k: off.get(k) for k in ("requested_breadth", "engine_ran")},
            {k: on.get(k) for k in ("requested_breadth", "engine_ran")})

    def test_routed_set_alone_still_cannot_tell_them_apart(self):
        """The reason inference cannot replace recording: route v2 emits an
        entry for every catalog lens (n/a included, for coverage honesty), so
        the routed set of an ENGINE-ON review is identical in shape to a
        `--all` sweep and `eval_record.breadth_of` reads both as 'all'."""
        off_ws, on_ws = self.ws(), self.ws()
        off_ids = [x["id"] for x in case_forced_all(lens, off_ws)["lenses"]]
        on_ids = [x["id"] for x in case_v2_routed(lens, on_ws)["lenses"]]

        self.assertEqual(inferred_breadth(off_ids), "all")
        self.assertEqual(inferred_breadth(on_ids), "all")   # the misread
        self.assertEqual(inferred_breadth(off_ids), inferred_breadth(on_ids))
        # ...and the RECORD, which the inference is only a fallback for, does
        # tell them apart.
        self.assertNotEqual(only_row(off_ws)["engine_ran"],
                            only_row(on_ws)["engine_ran"])

    def test_requested_breadth_is_recorded_verbatim_not_normalised(self):
        """The caller-requested breadth is the scored fact; recording an
        effective or normalised value instead would re-hide `--all` behind a
        route that happened to widen."""
        ws = self.ws()
        case_forced_all(lens, ws)
        self.assertEqual(only_row(ws)["requested_breadth"], "all")

    def test_legacy_path_records_why_the_engine_did_not_run(self):
        """Engine-off has more than one cause (`--all`, no stage, no
        stage_profiles, use_signals=False); an unexplained False would make a
        reader guess exactly what this change exists to stop."""
        ws = self.ws()
        case_legacy_routed(lens, ws)      # no stage -> engine never engaged
        row = only_row(ws)
        self.assertFalse(row["engine_ran"])
        self.assertEqual(row["requested_breadth"], "routed")
        self.assertTrue(row["engine_off_reason"])
        self.assertIn("stage", row["engine_off_reason"])

    def test_forced_all_names_breadth_all_as_the_engine_off_cause(self):
        """`--all` is the cause the rubric scores; it must be named, not just
        implied by the requested breadth."""
        ws = self.ws()
        case_forced_all(lens, ws)
        self.assertIn("all", only_row(ws)["engine_off_reason"])

    def test_engine_failure_records_zero_dispatch_without_widening(self):
        """Mapper failure is named and remains selective with zero lenses."""
        ws = self.ws()
        case_engine_failure_fallback(lens, ws)
        row = only_row(ws)
        self.assertEqual(row["requested_breadth"], "routed")
        self.assertEqual(row["effective_breadth"], "routed")
        self.assertFalse(row["engine_ran"])
        self.assertIn("mapper_unavailable", row["engine_off_reason"])

    def test_engine_on_records_routed_as_the_effective_breadth(self):
        """The engine-on case must not report a widened breadth — otherwise
        it collides with the fail-open record instead of the `--all` one."""
        ws = self.ws()
        case_v2_routed(lens, ws)
        row = only_row(ws)
        self.assertEqual(row["effective_breadth"], "routed")
        self.assertNotIn("engine_off_reason", row)

    def test_row_carries_the_stage_and_lens_count_that_join_it_to_the_route(
            self):
        """The breadth row is written by the router; the `lens_route` row is
        written by the loop. A consumer needs enough on the breadth row to
        pair the two beyond timestamp order."""
        ws = self.ws()
        routing = case_v2_routed(lens, ws)
        row = only_row(ws)
        self.assertEqual(row["stage"], "review")
        self.assertEqual(row["lens_count"], len(routing["lenses"]))

    def test_git_diff_full_catalog_review_is_recorded_as_forced_all(self):
        """The em step routes through route_git_diff with breadth='all' — the
        exact review the rubric scores — so the fact must survive that entry
        point, not just a direct route() call."""
        ws = self.ws(make_git_ws)
        case_git_diff_all(lens, ws)
        row = only_row(ws)
        self.assertEqual(row["requested_breadth"], "all")
        self.assertFalse(row["engine_ran"])


# ---------------------------------------------- recording never denies

class TestRecordingChangesNoRoutingDecision(_WsCleanup):
    """A trace-row addition may not move routing. Proven against
    `HEAD:taskplane/lens.py`, control-first."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = load_baseline_lens()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls.baseline, "_scratch_dir", ""),
                      ignore_errors=True)

    def run_case(self, mod, fn, factory):
        ws = self.ws(factory)
        return canonical(fn(mod, ws))

    def test_control_baseline_matches_itself_for_every_case(self):
        """Asserted BEFORE any baseline-vs-new comparison: if two runs of the
        SAME code disagree, a byte-equality differential proves nothing at
        all, it just happens to pass."""
        for name, fn, factory in CASES:
            with self.subTest(case=name):
                a = self.run_case(self.baseline, fn, factory)
                b = self.run_case(self.baseline, fn, factory)
                self.assertEqual(a, b, f"{name}: control is not deterministic")

    def test_new_routing_is_byte_identical_to_baseline_for_every_case(self):
        """Routed, `--all`, forced lenses, skipped lenses, engine failure,
        primed scope, no workspace, and the git-diff entry point."""
        for name, fn, factory in CASES:
            if name == "engine_failure_fallback":
                continue  # R-0005 intentionally changes fallback to refusal.
            with self.subTest(case=name):
                self.assertEqual(self.run_case(self.baseline, fn, factory),
                                 self.run_case(lens, fn, factory),
                                 f"{name}: routing changed")

    def test_new_routing_is_byte_identical_when_the_tracer_raises(self):
        """A recorder that can break the thing it records is a denial path.
        With tp.trace raising on EVERY call, routing must be unchanged and
        no case may escape as an exception."""
        for name, fn, factory in CASES:
            if name == "engine_failure_fallback":
                continue  # R-0005 intentionally changes fallback to refusal.
            with self.subTest(case=name):
                base = self.run_case(self.baseline, fn, factory)
                with mock.patch.object(tp, "trace", boom):
                    new = self.run_case(lens, fn, factory)
                self.assertEqual(base, new, f"{name}: routing changed")

    def test_dispatch_briefs_are_byte_identical_for_every_case(self):
        """The brief is the payload that reaches a lens agent; a routing that
        matched but briefed differently would still be a behaviour change."""
        for name, fn, factory in CASES:
            if name == "engine_failure_fallback":
                continue  # R-0005 intentionally emits no briefs on failure.
            with self.subTest(case=name):
                a = self.baseline.dispatch_briefs(
                    fn(self.baseline, self.ws(factory)), base="HEAD")
                b = lens.dispatch_briefs(
                    fn(lens, self.ws(factory)), base="HEAD")
                self.assertEqual(canonical(a), canonical(b))

    def test_a_broken_route_still_raises_the_same_way(self):
        """The exception path is part of the contract: an unroutable catalog
        must fail identically, not be swallowed by the recorder."""
        broken = copy.deepcopy(CATALOG)
        # routes on any code change, then has no 'name' to build an entry
        broken["lenses"] = [{"id": "ghost", "baseline": "code"}]
        for mod in (self.baseline, lens):
            with self.subTest(module=mod.__name__):
                with self.assertRaises(KeyError):
                    mod.route(DIFF_FILES, task_type="feature", catalog=broken,
                              workspace=self.ws())


# ------------------------------------------- where the recorder may write

class TestRecorderWritesOnlyWhereARecordAlreadyExists(_WsCleanup):
    def test_routing_without_a_workspace_records_nothing_and_still_routes(
            self):
        """Most route() callers pass no workspace (pm/plan/design briefs);
        they must keep working, silently unrecorded."""
        routing = case_no_workspace(lens, None)
        self.assertTrue(routing["lenses"])

    def test_ungoverned_workspace_never_gains_a_taskplane_dir(self):
        """The existing route suites pass CHECKED-IN fixture directories as
        `workspace=`; seeding `.taskplane/` there would dirty the repo on
        every test run and make those fixtures' own file listings drift."""
        ws = self.ws(make_ws, governed=False)
        case_v2_routed(lens, ws)
        self.assertFalse(os.path.isdir(os.path.join(ws, ".taskplane")))
        self.assertEqual(breadth_rows(ws), [])


if __name__ == "__main__":
    unittest.main()
