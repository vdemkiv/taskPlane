"""Engine-authored trace rows for the two things a model-behavior rubric
cannot otherwise check: WHICH context files a review actually wrote, and
WHICH tree a blast radius actually describes.

E3 — `review.write_context` emits `review_context_written`.
    Rubric item R7b-f asserts an EXACT SUBSTRING match of a context path
    inside a dispatched lens brief. The comparand has to be the literal
    string `write_context` returned and `context_note` embedded; a row that
    rebuilds the path from CONTEXT_DIR/DIFF_NAME is equal today and can
    drift tomorrow, and the item silently becomes unprovable rather than
    failing. The sha256 is read BACK off the disk, not hashed from the body
    the caller handed in: the claim being recorded is "this file, with these
    bytes, is on disk for the lens agents", and only a re-read proves it.

    THE DEFECT THIS SHIPPED WITH LAST TIME: the refusal path emitted
    `{"paths": []}` and nothing else. Rubric item R4 scores on row existence
    plus timestamp ordering, so a session whose workspace refused the
    context directory — a session that stored NO context at all — scored R4
    met. Every row now carries an explicit `status`, so "refused" and
    "nothing to write" can never be read as "written".

E4 — `head` + `scanned_head` on `graph_impact`, at all THREE emission sites.
    `head` is the tree under review; `scanned_head` is the HEAD the
    dependency graph itself was scanned at. Without both, a stale blast
    radius is indistinguishable from a current one.

    THE MUTATION THAT SURVIVED A FULL SUITE: taking `head` from `act_ws`.
    `act_ws` is gated on `state["parallel"]`, so in a SERIAL loop holding a
    task `workspace`, `act_ws` is the project tree while the diff — and the
    impact — come from the worker's worktree. The row would then name a tree
    containing none of the change, and every existing test still passed.
    `test_head_is_the_worker_worktree_not_the_project_in_a_serial_loop`
    exists to kill exactly that substitution.

BOTH ADDITIONS ARE RECORDING ONLY. They may not change a gate outcome, a
returned payload, an emitted event name, or an exception path.
`TestRecordingOnly` holds that two ways, because neither is sufficient
alone:

  * a DIFFERENTIAL against a variant of the current loop.py with the
    recording kwargs stripped back out — including scenarios that reach no
    emission site at all, so an invented row would show — under a frozen
    clock and a frozen uuid, with control determinism (variant vs itself)
    asserted BEFORE the comparison it is the control for;
  * CONTAINMENT tests, because a control derived from the current source
    carries any denial that sits outside the stripped kwargs and cancels it
    out. Two mutants proved that hole real. The containment tests pin the
    recording to a `**heads()` splat inside `tp.trace(...)` and to a bare
    `_record(...)` statement, where it cannot reach control flow at all.

The whole-loop comparison against the actual v2.13.0 blobs (every gate
outcome and every trace record from execute through sign-off) was run at
landing time; it is not a test here because `git show HEAD:` stops being a
baseline the moment this commits, and a differential against itself proves
nothing while still passing.
"""
import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402
import loop  # noqa: E402
import requirements as reqs  # noqa: E402
import review  # noqa: E402
import taskplane_lite as tp  # noqa: E402

FROZEN_TIME = 1750000000.0
FROZEN_UUID = uuid.UUID(int=0x5eed)


def _git(ws, *args):
    subprocess.run(["git", "-c", "user.email=e@e", "-c", "user.name=t",
                    *args], cwd=ws, check=True, capture_output=True,
                   text=True, encoding="utf-8", errors="replace")


def _repo(root, name="ws"):
    ws = os.path.join(root, name)
    os.makedirs(os.path.join(ws, "src"))
    with open(os.path.join(ws, "src", "a.py"), "w", encoding="utf-8") as f:
        f.write("import os\nx = 1\n")
    with open(os.path.join(ws, "src", "b.py"), "w", encoding="utf-8") as f:
        f.write("import a\ny = 2\n")
    _git(ws, "init", "-q")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")
    return ws


def _touch_commit(ws, rel="src/a.py", text="z = 3\n"):
    with open(os.path.join(ws, rel), "a", encoding="utf-8") as f:
        f.write(text)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "change")
    return tp.git_head(ws)


def _rows(ws, event):
    out = []
    for p in tp.trace_paths(ws):
        with open(p, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("event") == event:
                    out.append(rec)
    return out


def _events(ws):
    out = []
    for p in tp.trace_paths(ws):
        with open(p, encoding="utf-8") as f:
            out.extend(json.loads(line).get("event") for line in f)
    return out


TASK = {"id": "t1", "scope": ["src/**"], "tests": "true",
        "criteria": ["it works"], "status": "pending", "fix_cycles": 0}


def _state(ws, step, *, baseline, task=None, parallel=False, **extra):
    st = {"goal": "g", "step": step,
          "tasks": [dict(task or TASK)] if step != "design" else None,
          "current_task": 0, "parallel": parallel, "max_fix_cycles": 2,
          "checkpoints": ["em"], "baseline": baseline}
    st.update(extra)
    loop.save(ws, st)
    return st


# ------------------------------------------------------------------ E3

class _TamperedWrite:
    """A file handle that puts MORE on disk than the caller wrote.

    The only way to tell "hashed the bytes on disk" from "hashed the body in
    memory" apart is to make the two differ."""

    MARK = "\n# tampered on the way to disk\n"

    def __init__(self, fh):
        self._fh = fh

    def write(self, s):
        return self._fh.write(s + self.MARK)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._fh.__exit__(*exc)


class TestReviewContextRow(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.ws = os.path.join(self.root, "ws")
        os.makedirs(self.ws)

    def _row(self):
        rows = _rows(self.ws, "review_context_written")
        self.assertEqual(len(rows), 1, "exactly one row per write_context")
        return rows[0]

    def test_written_row_carries_the_paths_the_caller_was_handed(self):
        """R7b-f asserts an exact substring match of a context path inside a
        dispatched brief. The row's comparand must therefore be the SAME
        string the caller got back — not a path rebuilt from CONTEXT_DIR and
        DIFF_NAME, which is equal today and free to drift tomorrow."""
        paths = review.write_context(self.ws, diff="d\n", blast_radius="b\n",
                                     impact={"touched": ["src"]})
        row = self._row()
        self.assertEqual(row["status"], "written")
        self.assertEqual(row["paths"], list(paths.values()))
        self.assertEqual(len(row["paths"]), 3)

    def test_row_paths_are_the_caller_s_own_string_objects(self):
        """The identity check is the anti-drift guard the equality check
        cannot make: a rebuilt `os.path.join(CONTEXT_DIR, DIFF_NAME)` is a
        DIFFERENT object with an equal value, so only `is` fails on the
        rebuild that R7b-f's substring match would eventually trip over."""
        seen = {}
        with mock.patch.object(review.tp, "trace",
                               side_effect=lambda w, e, **kw: seen.update(kw)):
            paths = review.write_context(self.ws, diff="d\n")
        for got, expected in zip(seen["paths"], paths.values()):
            self.assertIs(got, expected)

    def test_row_paths_appear_verbatim_in_the_brief_note(self):
        """The rubric's substring assertion, executed: every path in the row
        must occur literally in the note a brief carries instead of the
        payload. If these two ever disagree the item is unprovable."""
        paths = review.write_context(self.ws, diff="d\n", blast_radius="b\n")
        note = review.context_note(paths)
        for p in self._row()["paths"]:
            self.assertIn(p, note)

    def test_sha256_is_read_back_off_the_disk_not_hashed_from_the_body(self):
        """The row's claim is "this file, with these bytes, is on disk for
        the lens agents". A digest of the in-memory body claims something
        weaker — it cannot see a short write, a full disk, or anything a
        wrapper interposed. Proven by making disk and body differ."""
        body = "the whole diff\n"
        real_open = open

        def fake_open(path, mode="r", *a, **kw):
            fh = real_open(path, mode, *a, **kw)
            if "w" in mode and str(path).endswith(review.DIFF_NAME):
                return _TamperedWrite(fh)
            return fh

        with mock.patch("builtins.open", fake_open):
            paths = review.write_context(self.ws, diff=body)
        rel = paths[review.DIFF_NAME]
        with open(os.path.join(self.ws, rel), "rb") as f:
            on_disk = f.read()
        self.assertNotEqual(on_disk, body.encode())
        self.assertEqual(self._row()["sha256"][rel],
                         hashlib.sha256(on_disk).hexdigest())
        self.assertNotEqual(self._row()["sha256"][rel],
                            hashlib.sha256(body.encode()).hexdigest())

    def test_refusal_row_cannot_be_read_as_a_session_that_stored_context(self):
        """THE DEFECT: shipped as `{"paths": []}`, which rubric R4 — scoring
        on row existence and timestamp ordering — read as "context was
        stored". A workspace that REFUSED the directory scored the item met.
        The status field is what makes the two states different rows."""
        real_makedirs = os.makedirs

        def refuse(path, *a, **kw):
            if str(path).endswith(review.CONTEXT_DIR):
                raise OSError("read-only")
            return real_makedirs(path, *a, **kw)

        with mock.patch.object(review.os, "makedirs", refuse):
            paths = review.write_context(self.ws, diff="d\n")
        self.assertEqual(paths, {})
        row = self._row()
        self.assertEqual(row["status"], "refused")
        self.assertEqual(row["paths"], [])
        self.assertEqual(row["sha256"], {})

    def test_no_op_row_is_its_own_status_not_a_refusal_and_not_a_write(self):
        """Three outcomes, three statuses: nothing offered to write is not
        the same fact as a workspace that refused, and neither may be read
        as a written one."""
        paths = review.write_context(self.ws)
        self.assertEqual(paths, {})
        self.assertEqual(self._row()["status"], "empty")

    def test_recording_changed_neither_the_return_value_nor_the_files(self):
        """Recording only: write_context's contract to its callers — the
        mapping it returns and the bytes it leaves on disk — is exactly what
        it was before the row existed."""
        diff, brief = "the diff\n", "the blast radius\n"
        impact = {"touched": ["src"]}
        paths = review.write_context(self.ws, diff=diff, blast_radius=brief,
                                     impact=impact)
        self.assertEqual(paths, {
            review.DIFF_NAME: f"{review.CONTEXT_DIR}/{review.DIFF_NAME}",
            review.BRIEF_NAME: f"{review.CONTEXT_DIR}/{review.BRIEF_NAME}",
            review.IMPACT_NAME: f"{review.CONTEXT_DIR}/{review.IMPACT_NAME}"})
        for name, body in ((review.DIFF_NAME, diff),
                           (review.BRIEF_NAME, brief),
                           (review.IMPACT_NAME,
                            json.dumps(impact, indent=2, sort_keys=True))):
            with open(os.path.join(self.ws, paths[name]),
                      encoding="utf-8") as f:
                self.assertEqual(f.read(), body)


# ------------------------------------------------------------------ E4

class TestGraphImpactHeads(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.ws = _repo(self.root)
        depgraph.scan(self.ws)
        self.base = tp.git_head(self.ws)

    def _row(self, step):
        rows = [r for r in _rows(self.ws, "graph_impact")
                if r.get("step") == step]
        self.assertTrue(rows, f"no graph_impact row at step={step}")
        return rows[-1]

    def test_evaluate_row_names_the_tree_it_scanned_and_the_graphs_head(self):
        """Site 1 of 3. Without these two fields a reader cannot tell a
        blast radius derived from the tree under review from one derived
        from a graph scanned three commits ago."""
        head = _touch_commit(self.ws)
        _state(self.ws, "evaluate", baseline=self.base)
        self.assertIsNone(loop.next_action(self.ws).get("error"))
        row = self._row("evaluate")
        self.assertEqual(row["head"], head)
        self.assertIn("scanned_head", row)

    def test_execute_row_names_the_tree_it_scanned_and_the_graphs_head(self):
        """Site 2 of 3 — the builder's pre-change blast radius."""
        head = _touch_commit(self.ws)
        _state(self.ws, "execute", baseline=self.base)
        self.assertIsNone(loop.next_action(self.ws).get("error"))
        row = self._row("execute")
        self.assertEqual(row["head"], head)
        self.assertIn("scanned_head", row)

    def test_design_row_names_the_tree_it_scanned_and_the_graphs_head(self):
        """Site 3 of 3 — the design step's proposed-module radius."""
        ent = reqs.record_requirement(self.ws, "the thing",
                                      acceptance=["a1"],
                                      context_files=["src/a.py"])
        depgraph.scan(self.ws)
        _state(self.ws, "design", baseline=self.base,
               requirement_id=ent["id"], design_required=True)
        self.assertIsNone(loop.next_action(self.ws).get("error"))
        row = self._row("design")
        self.assertEqual(row["head"], tp.git_head(self.ws))
        self.assertEqual(row["scanned_head"], tp.git_head(self.ws))

    def test_head_is_the_worker_worktree_not_the_project_in_a_serial_loop(self):
        """THE SUBSTITUTION THAT SURVIVED A FULL SUITE: sourcing `head` from
        `act_ws`. `act_ws` is gated on state["parallel"]; the impact block's
        workspace is NOT. A serial loop carrying a task `workspace` — a
        stale claim, a resumed wave, a fix step after a worker exited —
        therefore diffs the WORKTREE while act_ws still names the project.
        A row taking head from act_ws names a tree holding none of the
        change, and nothing else in the suite notices."""
        worktree = os.path.join(self.root, "wt")
        _git(self.ws, "clone", "-q", self.ws, worktree)
        _git(worktree, "config", "user.email", "e@e")
        _git(worktree, "config", "user.name", "t")
        worker_head = _touch_commit(worktree)
        project_head = tp.git_head(self.ws)
        self.assertNotEqual(worker_head, project_head)

        task = dict(TASK, workspace=worktree)
        _state(self.ws, "evaluate", baseline=self.base, task=task,
               parallel=False)
        self.assertIsNone(loop.next_action(self.ws).get("error"))
        row = self._row("evaluate")
        self.assertEqual(row["head"], worker_head)
        self.assertNotEqual(row["head"], project_head)

    def test_scanned_head_is_the_graphs_head_not_the_projects(self):
        """`scanned_head` answers "which tree produced this dependency
        graph". Sourcing it from `git_head(ws)` would make it a copy of the
        project HEAD and the staleness it exists to expose invisible."""
        scanned = tp.git_head(self.ws)
        head = _touch_commit(self.ws)
        self.assertNotEqual(scanned, head)
        _state(self.ws, "execute", baseline=self.base)
        self.assertIsNone(loop.next_action(self.ws).get("error"))
        row = self._row("execute")
        self.assertEqual(row["scanned_head"], scanned)
        self.assertEqual(row["head"], head)

    def test_affected_reqs_stays_on_the_review_rows_only(self):
        """The row shape is deliberately NOT uniform: the product half of
        the blast radius is computed only at evaluate/em. Pinned so the
        head/scanned_head addition cannot quietly regularise it."""
        _touch_commit(self.ws)
        _state(self.ws, "evaluate", baseline=self.base)
        loop.next_action(self.ws)
        self.assertIn("affected_reqs", self._row("evaluate"))
        _state(self.ws, "execute", baseline=self.base)
        loop.next_action(self.ws)
        self.assertNotIn("affected_reqs", self._row("execute"))

    def test_review_kernel_reads_the_selected_tree_once(self):
        """The canonical review kernel owns routing now. It reads the project
        tree in a serial loop and the claimed worktree in a parallel loop;
        no legacy pre-kernel route pass is allowed."""
        worktree = os.path.join(self.root, "wt")
        _git(self.ws, "clone", "-q", self.ws, worktree)
        _git(worktree, "config", "user.email", "e@e")
        _git(worktree, "config", "user.name", "t")
        _touch_commit(worktree)
        task = dict(TASK, workspace=worktree)

        seen = []
        original = review.start_review

        def spy(w, *a, **kw):
            seen.append(w)
            return original(w, *a, **kw)

        _state(self.ws, "evaluate", baseline=self.base, task=task,
               parallel=False)
        with mock.patch.object(review, "start_review", spy):
            loop.next_action(self.ws)
        self.assertEqual(seen, [self.ws], "serial kernel reads the project")

        seen.clear()
        _state(self.ws, "evaluate", baseline=self.base, task=task,
               parallel=True)
        with mock.patch.object(review, "start_review", spy):
            loop.next_action(self.ws)
        self.assertEqual(seen, [worktree], "parallel kernel reads the claim")

    def test_every_graph_impact_site_carries_the_heads(self):
        """Source-level backstop for the per-site tests: dropping the kwargs
        from ONE of the three sites leaves the other two green, and the site
        that lost them records a blast radius nobody can date."""
        with open(loop.__file__, encoding="utf-8") as f:
            src = f.read()
        sites = src.split('tp.trace(ws, "graph_impact"')[1:]
        self.assertEqual(len(sites), 3, "loop.py must keep three sites")
        for i, tail in enumerate(sites):
            call = tail.split(")\n")[0]
            self.assertIn("**heads()", call, f"site {i + 1} lost the heads")


# ------------------------------------------------------- recording only

def _loop_without_the_recording(tmpdir):
    """The CURRENT loop.py with the recording kwargs stripped back out.

    A differential against `git show HEAD:` rots the moment this lands: the
    baseline becomes the new code and the comparison quietly proves nothing.
    Deriving the control from the current source instead keeps the claim
    ("the row is the ONLY difference") true for every future edit."""
    with open(loop.__file__, encoding="utf-8") as f:
        src = f.read()
    stripped = src.replace(", **heads()", "")
    if stripped == src:                                   # pragma: no cover
        raise AssertionError("no recording kwargs found to strip")
    path = os.path.join(tmpdir, "loop_norow.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(stripped)
    spec = importlib.util.spec_from_file_location("loop_norow", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRecordingOnly(unittest.TestCase):
    """Neither addition may change a gate outcome, a returned payload, an
    emitted event name, or an exception path.

    TWO GUARDS, because the differential alone is not enough. A control
    derived from the current source cancels out any denial that ALSO sits
    outside the stripped kwargs: mutants that leaked `heads()` into the
    returned payload and that returned `{"error": "stale graph"}` on a head
    mismatch both SURVIVED the differential, because the control ran them
    too. The containment tests below close that: the recording may only
    ever be a `**heads()` splat inside a `tp.trace(...)` call, and
    `_record` may only ever be a bare statement. Neither can then reach a
    condition, a return, or an assignment at all."""

    def test_the_heads_recording_can_never_reach_control_flow(self):
        """Containment, not comparison: EVERY load of `heads` in loop.py
        must be the `**heads()` splat of a `.trace(...)` call. A blast
        radius may be described by a row; it may not decide anything."""
        with open(loop.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        loads = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Name) and n.id == "heads"
                 and isinstance(n.ctx, ast.Load)]
        splatted = []
        for call in ast.walk(tree):
            if not (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "trace"):
                continue
            for kw in call.keywords:
                if kw.arg is None:
                    splatted += [n for n in ast.walk(kw.value)
                                 if isinstance(n, ast.Name) and n.id == "heads"]
        self.assertEqual(len(loads), 3, "three sites, three uses")
        self.assertEqual(len(loads), len(splatted),
                         "`heads` is read somewhere that is not a trace row")

    def test_the_context_row_can_never_reach_control_flow(self):
        """Same containment for E3: `_record` is a statement, never a
        value. It cannot become a condition, a return, or a raise."""
        with open(review.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())

        def named(node):
            return (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_record")

        calls = [n for n in ast.walk(tree) if named(n)]
        statements = [s.value for s in ast.walk(tree)
                      if isinstance(s, ast.Expr) and named(s.value)]
        self.assertEqual(len(calls), 2, "one row per exit path")
        self.assertEqual(len(calls), len(statements),
                         "_record's value is being used for something")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.norow = _loop_without_the_recording(cls.tmp)

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.ws = _repo(self.root)
        depgraph.scan(self.ws)
        self.base = tp.git_head(self.ws)

    def _run(self, module):
        """One next_action: its payload and the event names it appended."""
        before = len(_events(self.ws))
        payload = module.next_action(self.ws)
        return payload, _events(self.ws)[before:]

    def _differential(self, step, **state_kw):
        """[control, control, subject] — the control pair FIRST, because a
        nondeterministic control (a fresh uuid4 task_id, an int(time.time())
        stamp) makes the real comparison prove nothing at all."""
        _state(self.ws, step, baseline=self.base, **state_kw)
        with mock.patch.object(time, "time", return_value=FROZEN_TIME), \
                mock.patch.object(uuid, "uuid4", return_value=FROZEN_UUID):
            self._run(self.norow)                       # warm side effects
            base1 = self._run(self.norow)
            base2 = self._run(self.norow)
            subject = self._run(loop)
        self.assertEqual(base1, base2,
                         f"CONTROL IS NONDETERMINISTIC at step={step} — the "
                         f"differential below would prove nothing")
        self.assertEqual(base2, subject,
                         f"the recording changed behaviour at step={step}")
        return subject

    def test_execute_payload_and_events_are_unchanged(self):
        _touch_commit(self.ws)
        _, events = self._differential("execute")
        self.assertIn("graph_impact", events)

    def test_design_payload_and_events_are_unchanged(self):
        ent = reqs.record_requirement(self.ws, "the thing",
                                      acceptance=["a1"],
                                      context_files=["src/a.py"])
        depgraph.scan(self.ws)
        _, events = self._differential("design", requirement_id=ent["id"],
                                       design_required=True)
        self.assertIn("graph_impact", events)

    def test_a_step_that_reaches_no_site_gains_no_row(self):
        """The scenario an INVENTED row would show up in: the plan step
        emits no graph_impact at all, and must still emit none."""
        _, events = self._differential("plan")
        self.assertNotIn("graph_impact", events)

    def test_a_failed_dor_still_returns_before_any_of_this(self):
        """The exception/short-circuit path: a step that fails its
        Definition of Ready returns the same error payload and emits the
        same events, with no blast radius recorded either way."""
        task = dict(TASK, tests=None)
        payload, events = self._differential("execute", task=task)
        self.assertIn("error", payload)
        self.assertNotIn("graph_impact", events)


if __name__ == "__main__":                                # pragma: no cover
    unittest.main()
