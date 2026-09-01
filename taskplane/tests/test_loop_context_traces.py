"""Behavioral contracts for privacy-safe context and graph activity rows."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import depgraph  # noqa: E402
import loop  # noqa: E402
import requirements as reqs  # noqa: E402
import review  # noqa: E402
import taskplane_lite as tp  # noqa: E402

def _audit_field(name):
    """The durable trace's privacy-safe name for a producer field."""
    return tp._sanitize_audit_key(name)


def _audit_value(value):
    """The durable trace's content-addressed projection of private text."""
    return tp._audit_minimized(value)


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
        dispatched brief. The producer identity is pinned by the next test;
        the durable row must carry the exact content-addressed descriptor of
        that list and must not disclose any path."""
        paths = review.write_context(self.ws, diff="d\n", blast_radius="b\n",
                                     impact={"touched": ["src"]})
        row = self._row()
        self.assertEqual(row["status"], "written")
        self.assertEqual(row["paths"], _audit_value(list(paths.values())))
        for path in paths.values():
            self.assertNotIn(path, json.dumps(row))

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
        producer input must occur literally in the note a brief carries.
        The audit row correlates by digest and does not disclose the path."""
        paths = review.write_context(self.ws, diff="d\n", blast_radius="b\n")
        note = review.context_note(paths)
        row = self._row()
        self.assertEqual(row["paths"], _audit_value(list(paths.values())))
        for p in paths.values():
            self.assertIn(p, note)
            self.assertNotIn(p, json.dumps(row))

    def test_sha256_is_read_back_off_the_disk_not_hashed_from_the_body(self):
        """The row's content descriptor means "this file digest was read
        from disk". A digest of the in-memory body claims something weaker:
        it cannot see anything a writer interposed. Proven by making disk and
        body differ and comparing their privacy-minimized digest values."""
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
        recorded = self._row()["sha256"][_audit_field(rel)]
        self.assertEqual(
            recorded, _audit_value(hashlib.sha256(on_disk).hexdigest()))
        self.assertNotEqual(
            recorded, _audit_value(hashlib.sha256(body.encode()).hexdigest()))

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
        self.assertEqual(row["paths"], _audit_value([]))
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
        from a graph scanned three commits ago. Audit privacy keeps their
        exact values content-addressed rather than plaintext."""
        head = _touch_commit(self.ws)
        _state(self.ws, "evaluate", baseline=self.base)
        self.assertIsNone(loop.next_action(self.ws).get("error"))
        row = self._row("evaluate")
        self.assertEqual(row[_audit_field("head")], _audit_value(head))
        self.assertIn(_audit_field("scanned_head"), row)

    def test_execute_row_names_the_tree_it_scanned_and_the_graphs_head(self):
        """Site 2 of 3 — the builder's pre-change blast radius."""
        head = _touch_commit(self.ws)
        _state(self.ws, "execute", baseline=self.base)
        self.assertIsNone(loop.next_action(self.ws).get("error"))
        row = self._row("execute")
        self.assertEqual(row[_audit_field("head")], _audit_value(head))
        self.assertIn(_audit_field("scanned_head"), row)

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
        head = tp.git_head(self.ws)
        self.assertEqual(row[_audit_field("head")], _audit_value(head))
        self.assertEqual(row[_audit_field("scanned_head")],
                         _audit_value(head))

    # Keep the historical node id so baseline inventories remain replayable;
    # canonical serial authority now deliberately selects the project tree.
    def test_head_is_the_worker_worktree_not_the_project_in_a_serial_loop(self):
        """Serial Evaluate reviews the project checkout even when old task
        state still names a claimed worker.  The impact row must name that
        canonical project tree, never bytes from the stale worker."""
        worktree = os.path.join(self.root, "wt")
        _git(self.ws, "clone", "-q", self.ws, worktree)
        _git(worktree, "config", "user.email", "e@e")
        _git(worktree, "config", "user.name", "t")
        worker_head = _touch_commit(worktree)
        project_head = _touch_commit(self.ws, text="project = 4\n")
        self.assertNotEqual(worker_head, project_head)

        task = dict(TASK, workspace=worktree)
        _state(self.ws, "evaluate", baseline=self.base, task=task,
               parallel=False)
        self.assertIsNone(loop.next_action(self.ws).get("error"))
        row = self._row("evaluate")
        self.assertEqual(row[_audit_field("head")],
                         _audit_value(project_head))
        self.assertNotEqual(row[_audit_field("head")],
                            _audit_value(worker_head))

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
        self.assertEqual(row[_audit_field("scanned_head")],
                         _audit_value(scanned))
        self.assertEqual(row[_audit_field("head")], _audit_value(head))

    def test_affected_reqs_stays_on_the_review_rows_only(self):
        """The row shape is deliberately NOT uniform: the product half of
        the blast radius is computed only at evaluate/em. Pinned so the
        head/scanned_head addition cannot quietly regularise it."""
        _touch_commit(self.ws)
        _state(self.ws, "evaluate", baseline=self.base)
        loop.next_action(self.ws)
        self.assertIn(_audit_field("affected_reqs"), self._row("evaluate"))
        _state(self.ws, "execute", baseline=self.base)
        loop.next_action(self.ws)
        self.assertNotIn(_audit_field("affected_reqs"),
                         self._row("execute"))

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
        original = loop._review_kernel

        def spy(project_ws, diff_ws, *a, **kw):
            seen.append(diff_ws)
            return original(project_ws, diff_ws, *a, **kw)

        _state(self.ws, "evaluate", baseline=self.base, task=task,
               parallel=False)
        with mock.patch.object(loop, "_review_kernel", spy):
            action = loop.next_action(self.ws)
        self.assertEqual(
            (action.get("review_kernel") or {}).get("status"), "ready",
            action)
        self.assertEqual(seen, [self.ws], "serial kernel reads the project")

        seen.clear()
        _state(self.ws, "evaluate", baseline=self.base, task=task,
               parallel=True)
        # Worktree identity resolution is covered by the loop/storage suites;
        # this test isolates which already-resolved tree ReviewKernel reads.
        with mock.patch.object(
                loop, "_parallel_evaluate_workspace",
                return_value=(worktree, None)), \
                mock.patch.object(loop, "_review_kernel", spy):
            action = loop.next_action(self.ws)
        self.assertEqual(
            (action.get("review_kernel") or {}).get("status"), "ready",
            action)
        self.assertEqual(seen, [worktree], "parallel kernel reads the claim")

if __name__ == "__main__":                                # pragma: no cover
    unittest.main()
