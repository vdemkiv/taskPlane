"""The scorer — one scenario, one recorded run, one verdict per rubric row.

`eval_scenario` owns WHAT a governed skill's flow must show, as data. This
module owns the answer: `evaluate(scenario, record)` returns a SCORECARD —
for every step in the manifest, one of

    pass | fail | no_evidence | n/a

with the evidence that decided it or the reason it could not be decided. It
is pure: records in, verdicts out. No model, no network, no subprocess, no
clock. The same inputs always produce the same card, which is what lets the
frozen corpus under `evals/negative/` pin this instrument's own behaviour.

It is also GENERIC. Every check is evaluated through the vocabulary
`eval_scenario` publishes — `CHECKS`, `RECORDS`, `SELECT_OPS`, `ANCHORS`,
`constraints()` — and nothing here knows the name of a skill. Adding a skill
is adding a JSON file.

AN ABSENT PRODUCING RECORD IS `no_evidence`, NEVER `pass`
---------------------------------------------------------
This outranks every other rule in the file, and it is stated once, over the
RECORD, ahead of any check's own logic — because it is the defect that failed
this layer's last evaluation:

    `repeats == 0` is arithmetically true over zero rows.

So a run whose derivation ledger was never written scored a PERFECT
efficiency result. The instrument's own failure read back as compliance,
which is precisely the failure this whole layer exists to catch. Getting that
one right for `repeats` alone would leave the hole open for the next check,
so the guard is applied to all of them: if the record a step reads is absent,
empty or unreadable, the step is `no_evidence`, whatever the check.

Three narrower vacuities are closed the same way, each being a claim that
rests on nothing:

  * an ANCHOR with no rows. "the findings file existed before any lens was
    briefed" is not TRUE when no lens was ever briefed — it is unanswerable.
    Scored as a pass, the flow that never reached the control point would
    outrank the flow that reached it late.
  * a `repeats` over zero derivation rows. Nothing was derived, so nothing
    could have been re-derived.
  * a derivation ledger with no pre-flight probe row. `derivation.probe()`
    writes a row and READS IT BACK; without that receipt, a short ledger and
    a ledger nobody could append to are the same picture, so the arithmetic
    over it is not evidence however clean it comes out. That is a RUN-level
    fact, reported as `instrument: broken`, and it only blinds the record the
    probe certifies — declaring the trace unknown because the ledger is would
    throw away real evidence, which is the same dishonesty pointed the other
    way.

`n/a` IS DECLARED, NEVER INFERRED
---------------------------------
`n/a` means the SCENARIO said `applicable: false` and gave a reason. A step
the scorer cannot evaluate is `no_evidence`. If the scorer could reach `n/a`
on its own, every unmeasurable row would leave the vector looking complete —
which is how a control point nobody checked comes to read exactly like a
control point that passed.

THE SCORE IS A VECTOR
---------------------
`verdicts` — a verdict per step id — is the result. `score` (pass over pass
plus fail, unknowns excluded) is a convenience for humans and it GATES
NOTHING: one row improving while another regresses leaves the average exactly
where it was. Gating is per item, and it belongs to whoever pins the vector.

THE ARITHMETIC THAT IS NOT HERE
-------------------------------
`repeats` delegates to `derivation.repeats()`. A private copy would have to
re-derive the probe exclusion, and the probe derives `impact` at the same
head the run does — so a private copy scores a compliant run as one repeat
and fails the very row the probe protects. There is one implementation of
that arithmetic and this module calls it.
"""
from __future__ import annotations

import io
import json
import os

import derivation
import eval_drivers
import eval_scenario as es

SCHEMA = "taskplane.eval-rubric/v1"
RUN_SCHEMA_V2 = "taskplane.eval-run/v2"
ABSOLUTE_SCHEMA = "taskplane.eval-absolute/v2"

# Counters are required even when the host cannot report tokens.  A missing
# counter is unknown instrumentation, not an economical zero.
EFFICIENCY_COUNTERS = (
    "cli_count", "emitted_bytes", "repeated_derivation_bytes",
    "dispatched_agent_count", "prompt_view_bytes", "artifact_render_bytes",
    "duplicate_artifact_bytes", "duplicate_html_emissions",
)
COMPARISON_KEYS = (
    "scenario", "fixture", "start_sha", "evaluated_sha",
    "task" + "plane_version", "host", "model", "reasoning_effort",
    "telemetry_method", "run_mode",
)
CLI_LIMIT = 12
PR_9464_TOKEN_LIMIT = 1_180_000

# The four things a rubric row can be. `no_evidence` is not a soft failure
# and not a soft pass: it is the instrument saying so.
VERDICTS = ("pass", "fail", "no_evidence", "n/a")
PASS, FAIL, NO_EVIDENCE, NA = VERDICTS

# Re-exported so a caller scores against ONE vocabulary. A second list here
# would be free to disagree with the manifest schema.
RECORDS = es.RECORDS

NEGATIVE_DIRNAME = os.path.join("evals", "negative")

# Which file carries which record in a frozen fixture. `derivations.jsonl`
# and `context.jsonl` are not in `ci_evals.RECORD_FILES` on purpose: a record
# missing its ledger has to stay LOADABLE, or `evals/negative/no-ledger/`
# would be rejected instead of scored and the invariant above would go
# untested while looking tested.
RECORD_FILES = {
    "trace": "trace.jsonl",
    "obligations": "obligations.jsonl",
    "dispatch": "dispatch.json",
    "derivations": "derivations.jsonl",
    "context": "context.jsonl",
}
RUN_FILE = "run.json"

# The row list inside `dispatch.json`. The engine writes counts there today
# (`expected`, `unobserved`, `hook_active`); the per-brief rows the rubric
# needs are an ADDITIVE key, so one file serves both instruments and neither
# has to learn about the other.
DISPATCH_ROWS = "briefs"

# Worst-first. A definite failure outranks an unknown inside one step:
# evidence of a violation is evidence, and an unknown elsewhere in the same
# claim does not launder it away.
_RANK = {FAIL: 0, NO_EVIDENCE: 1, NA: 2, PASS: 3}


# ================================================================ the record

def record(trace=None, obligations=None, dispatch=None, derivations=None,
           context=None, run=None, unreadable=()) -> dict:
    """An in-memory record. `None` is ABSENT; `[]` is EMPTY; they differ.

    Keeping the distinction costs one branch and buys the difference between
    "the recorder never ran" and "the recorder ran and saw nothing", which is
    the first question anyone asks about a `no_evidence`.
    """
    return {
        "path": None,
        "rows": {"trace": trace, "obligations": obligations,
                 "dispatch": dispatch, "derivations": derivations,
                 "context": context},
        "run": run,
        "unreadable": tuple(unreadable),
    }


def status(rec, name: str) -> str:
    """`present` | `absent` | `empty` | `unreadable` for one record."""
    if name in (rec.get("unreadable") or ()):
        return "unreadable"
    rows = (rec.get("rows") or {}).get(name)
    if rows is None:
        return "absent"
    return "present" if rows else "empty"


def _jsonl(path: str, out: list) -> "str | None":
    """Every well-formed row of a JSONL file, oldest first.

    A torn line is SKIPPED, not raised — the same rule `derivation.read()`
    uses, and for the same reason: a half-written last line must not blind
    the whole rubric.
    """
    try:
        with io.open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError as exc:
        return str(exc.strerror or exc)
    return None


def _json(path: str):
    """(value, error). A fixture we cannot read is REPORTED, never raised."""
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f), None
    except ValueError:
        return None, "unparseable JSON"
    except OSError as exc:
        return None, str(exc.strerror or exc)


def read_record(path: str) -> dict:
    """Load one frozen record directory. The ONLY I/O in this module.

    Kept apart from `evaluate` so the scorer stays a pure function of data:
    every test above the loader constructs its record in memory, and the
    corpus is the only thing that touches a disk.
    """
    rec = record()
    rec["path"] = path
    unreadable = []
    for name, fname in sorted(RECORD_FILES.items()):
        full = os.path.join(path, fname)
        if not os.path.isfile(full):
            continue
        if fname.endswith(".jsonl"):
            rows: list = []
            err = _jsonl(full, rows)
            if err:
                unreadable.append(name)
                continue
            rec["rows"][name] = rows
            continue
        value, err = _json(full)
        if err:
            unreadable.append(name)
            continue
        if isinstance(value, list):
            rec["rows"][name] = value
        elif isinstance(value, dict):
            rec["rows"][name] = [r for r in (value.get(DISPATCH_ROWS) or ())
                                 if isinstance(r, dict)]
        else:
            unreadable.append(name)
    run_path = os.path.join(path, RUN_FILE)
    if os.path.isfile(run_path):
        value, err = _json(run_path)
        if err or not isinstance(value, dict):
            unreadable.append("run")
        else:
            rec["run"] = value
    rec["unreadable"] = tuple(sorted(set(unreadable)))
    return rec


def instrument(rec) -> tuple:
    """(`ok` | `broken`, reason). Broken means the LEDGER cannot be trusted.

    The pre-flight probe is the recorder's receipt that the ledger could be
    written and read back. Without it, zero repeats over a short ledger is
    not a measurement — it is the shape of a recorder that never appended a
    line, which is exactly the reading this layer exists to refuse.
    """
    st = status(rec, "derivations")
    if st != "present":
        return "broken", (f"the derivation ledger is {st}, so no pre-flight "
                          f"probe row can certify that it was writable")
    rows = rec["rows"]["derivations"]
    if not any(r.get("probe") for r in rows if isinstance(r, dict)):
        return "broken", ("the derivation ledger carries no pre-flight probe "
                          "row, so a short ledger cannot be told from a "
                          "ledger nobody could write")
    return "ok", None


# ============================================================== the selectors

def _eq(a, b) -> bool:
    """Equality that does not confuse `true` with `1`.

    Python says `True == 1`. JSON does not, and a rubric that reads
    `ready: 1` as `ready: true` grades a field the recorder never set.
    """
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return a == b


def _contains(got, operand) -> bool:
    if isinstance(got, str):
        return str(operand) in got
    if isinstance(got, (list, tuple)):
        return any(_eq(v, operand) for v in got)
    return False


def unknown_ops(select) -> tuple:
    """Selector operators outside `eval_scenario.SELECT_OPS`.

    Reported rather than ignored: a selector nobody can apply matches nothing,
    and a check over nothing would score `absent` as satisfied — a green row
    for a constraint that was never evaluated.
    """
    out = []
    if not isinstance(select, dict):
        return ()
    for field, want in select.items():
        if isinstance(want, dict):
            for op in want:
                if op not in es.SELECT_OPS:
                    out.append(f"{field}.{op}")
    return tuple(sorted(out))


def _matches(row, select) -> bool:
    for field, want in (select or {}).items():
        got = row.get(field)
        if isinstance(want, dict):
            for op, operand in want.items():
                if op == "in":
                    ok = any(_eq(got, v) for v in (operand or ()))
                elif op == "not_in":
                    ok = not any(_eq(got, v) for v in (operand or ()))
                elif op == "contains":
                    ok = _contains(got, operand)
                elif op == "present":
                    ok = (got is not None) == bool(operand)
                elif op == "absent":
                    ok = (got is None) == bool(operand)
                else:
                    return False
                if not ok:
                    return False
        elif not _eq(got, want):
            return False
    return True


# =============================================================== the ordering

def _sort_key(row, index: int) -> tuple:
    """(kind, value, position). Kind 2 means the row cannot be ordered.

    Ordering is on `eval_scenario.ORDER_KEY` and ties break on position
    within the record. Types are kept apart rather than coerced: comparing a
    float against an ISO string is a TypeError in Python and a lie in every
    other language.
    """
    ts = row.get(es.ORDER_KEY)
    if isinstance(ts, bool) or ts is None:
        return (2, "", index)
    if isinstance(ts, (int, float)):
        return (0, ts, index)
    if isinstance(ts, str):
        return (1, ts, index)
    return (2, "", index)


def _ordered(rows) -> list:
    return sorted(((_sort_key(r, i), r) for i, r in enumerate(rows or ())),
                  key=lambda pair: pair[0])


def _select(ordered, select) -> list:
    return [p for p in ordered if _matches(p[1], select)]


def _precedes(a, b, same_record: bool):
    """True / False / None when the two rows cannot be ordered at all."""
    if a[0] == 2 or b[0] == 2 or a[0] != b[0]:
        return None
    if a[1] != b[1]:
        return a[1] < b[1]
    return a[2] < b[2] if same_record else None


# ================================================================= the guards

def _guard(rec, name: str) -> "str | None":
    """The invariant, stated once. A reason means `no_evidence`."""
    if name not in RECORDS:
        return (f"unknown record {name!r} — the vocabulary is "
                f"{', '.join(RECORDS)}")
    st = status(rec, name)
    if st != "present":
        return (f"the {name} record is {st}, so this row rests on no "
                f"evidence at all — an absent record is never a pass")
    if name == "derivations":
        state, why = instrument(rec)
        if state != "ok":
            return why
    return None


def _rows(rec, name) -> list:
    return _ordered((rec.get("rows") or {}).get(name) or ())


def _present(rows, field) -> str:
    """`none` | `some` | `all` — how many rows carry `field`.

    An unrecorded field is an instrument gap, and blaming the run for what
    the recorder never wrote is the same dishonesty as the invariant, pointed
    the other way. Once SOME row carries it, the recorder demonstrably writes
    it and a row without it is a real gap.
    """
    have = sum(1 for _, r in rows if r.get(field) is not None)
    if not have:
        return "none"
    return "all" if have == len(rows) else "some"


def _ev(rows, limit=2) -> list:
    return [r for _, r in rows[:limit]]


# ================================================================ the checks
# Every one takes the constraint, the record and the rows its selector picked
# out, and returns (verdict, reason, evidence). The record-level guard has
# already run: these are only ever called over a PRESENT record.

def _exists(c, rec, sel):
    if sel:
        return PASS, f"{len(sel)} matching row(s)", {"matched": len(sel),
                                                     "rows": _ev(sel)}
    return FAIL, "no row matches the selector", {"matched": 0}


def _absent(c, rec, sel):
    if not sel:
        return PASS, "no row matches the selector, as required", {"matched": 0}
    return FAIL, f"{len(sel)} row(s) match a selector that must match none", \
        {"matched": len(sel), "rows": _ev(sel)}


def _count(c, rec, sel):
    lo, hi = c.get("min"), c.get("max")
    if lo is None and hi is None:
        return NO_EVIDENCE, ("`count` with neither `min` nor `max` cannot "
                             "fail, so it measures nothing"), {}
    n = len(sel)
    if lo is not None and n < lo:
        return FAIL, f"{n} matching row(s), fewer than the {lo} required", \
            {"matched": n}
    if hi is not None and n > hi:
        return FAIL, f"{n} matching row(s), more than the {hi} allowed", \
            {"matched": n, "rows": _ev(sel)}
    return PASS, f"{n} matching row(s), within [{lo}, {hi}]", {"matched": n}


def _anchor(c, key):
    """(record, select) for a named or inline anchor, or None."""
    spec = c.get(key)
    if isinstance(spec, str):
        spec = es.ANCHORS.get(spec)
    if not isinstance(spec, dict):
        return None
    return spec.get("record") or c.get("record"), spec.get("select")


def _order(c, rec, sel, key, want_before: bool):
    spec = _anchor(c, key)
    if spec is None:
        return NO_EVIDENCE, (f"the anchor {c.get(key)!r} does not resolve — "
                             f"an unresolvable anchor would be satisfied by "
                             f"absence"), {}
    a_name, a_select = spec
    bad = _guard(rec, a_name)
    if bad:
        return NO_EVIDENCE, f"anchor: {bad}", {}
    if unknown_ops(a_select):
        return NO_EVIDENCE, (f"anchor selector uses operator(s) "
                             f"{', '.join(unknown_ops(a_select))} outside "
                             f"{', '.join(es.SELECT_OPS)}"), {}
    anchor_rows = _select(_rows(rec, a_name), a_select)
    if not anchor_rows:
        # The run never reached the reference point. "It happened first" is
        # then unanswerable, not true — and a pass here would rank the flow
        # that never got there above the flow that got there late.
        return NO_EVIDENCE, (f"the anchor {c.get(key)!r} has no rows in the "
                             f"{a_name} record, so the ordering question "
                             f"cannot be answered"), {}
    if not sel:
        # The SUBJECT is the mandated act, over a record that demonstrably
        # works. Its absence is an observation, not an unknown.
        return FAIL, ("no row matches the selector, so nothing could have "
                      "been in the required order"), {"matched": 0}
    first, anchor = sel[0], anchor_rows[0]
    same = c.get("record") == a_name
    got = _precedes(first[0], anchor[0], same) if want_before \
        else _precedes(anchor[0], first[0], same)
    if got is None:
        return NO_EVIDENCE, (f"the two rows cannot be ordered on "
                             f"`{es.ORDER_KEY}`"), \
            {"rows": [first[1], anchor[1]]}
    word = "before" if want_before else "after"
    said = "" if got else "NOT "
    return (PASS if got else FAIL), \
        f"the first matching row is {said}{word} the anchor", \
        {"row": first[1], "anchor": anchor[1]}


def _before(c, rec, sel):
    return _order(c, rec, sel, "before", True)


def _after(c, rec, sel):
    return _order(c, rec, sel, "after", False)


def _comparand(c, rec):
    """(value, reason). A reason means the comparand itself is unknown."""
    if "value" in c:
        return c.get("value"), None
    spec = c.get("equals")
    if not isinstance(spec, dict):
        return None, "`field_equals` needs a `value` or an `equals`"
    field = spec.get("field")
    if not field:
        return None, "`equals` names no `field`"
    name = spec.get("record")
    if name == "run":
        run = rec.get("run")
        if not isinstance(run, dict):
            return None, ("the run's own identity is absent, so there is "
                          "nothing to compare against")
        if run.get(field) is None:
            return None, f"the run carries no {field!r}"
        return run.get(field), None
    bad = _guard(rec, name)
    if bad:
        return None, f"comparand: {bad}"
    if unknown_ops(spec.get("select")):
        return None, "comparand selector uses an unknown operator"
    rows = _select(_rows(rec, name), spec.get("select"))
    values = [r.get(field) for _, r in rows if r.get(field) is not None]
    if not values:
        return None, (f"no row in the {name} record carries {field!r}, so "
                      f"the comparand is unknown")
    if any(not _eq(v, values[0]) for v in values):
        return None, (f"the comparand is ambiguous — {name}.{field} takes "
                      f"more than one value")
    return values[0], None


def _field_equals(c, rec, sel):
    field = c.get("field")
    if not field:
        return NO_EVIDENCE, "`field_equals` names no `field`", {}
    if not sel:
        return NO_EVIDENCE, ("no row matches the selector, so there is "
                             "nothing to compare"), {"matched": 0}
    want, why = _comparand(c, rec)
    if why:
        return NO_EVIDENCE, why, {}
    have = _present(sel, field)
    if have == "none":
        return NO_EVIDENCE, (f"no matching row carries {field!r} — the field "
                             f"is unrecorded, which is an instrument gap and "
                             f"not a mismatch"), {"matched": len(sel)}
    wrong = [p for p in sel if not _eq(p[1].get(field), want)]
    if wrong:
        return FAIL, (f"{len(wrong)} of {len(sel)} matching row(s) carry a "
                      f"{field!r} that is not {want!r}"), \
            {"expected": want, "rows": _ev(wrong)}
    return PASS, f"every matching row carries {field!r} == {want!r}", \
        {"expected": want, "matched": len(sel)}


def _key_fields(c) -> tuple:
    key = c.get("key")
    if isinstance(key, dict):
        return key.get("left"), key.get("right")
    return key, key


def _pairs(c, rec, sel):
    left_field, right_field = _key_fields(c)
    if not left_field or not right_field:
        return NO_EVIDENCE, "`pairs` needs a `key`", {}
    spec = c.get("with")
    if not isinstance(spec, dict):
        return NO_EVIDENCE, "`pairs` needs a `with`", {}
    name = spec.get("record")
    bad = _guard(rec, name)
    if bad:
        return NO_EVIDENCE, f"partner: {bad}", {}
    if unknown_ops(spec.get("select")):
        return NO_EVIDENCE, "partner selector uses an unknown operator", {}
    if not sel:
        # Zero dispatched briefs are valid only when another engine record
        # proves this was the selective routing decision, rather than a
        # stopped or uninstrumented review. The manifest must opt into that
        # evidence explicitly; ordinary empty pairs remain no-evidence.
        empty = c.get("allow_empty_with")
        if isinstance(empty, dict):
            name = empty.get("record")
            bad = _guard(rec, name)
            if bad:
                return NO_EVIDENCE, f"empty-dispatch proof: {bad}", {}
            if unknown_ops(empty.get("select")):
                return NO_EVIDENCE, ("empty-dispatch proof selector uses an "
                                     "unknown operator"), {}
            proof = _select(_rows(rec, name), empty.get("select"))
            if proof:
                return PASS, ("zero rows are justified by the selective "
                              "zero-dispatch engine record"), \
                    {"matched": 0, "empty_proof": _ev(proof)}
        return NO_EVIDENCE, ("no row matches the selector, so there is "
                             "nothing to pair"), {"matched": 0}
    if _present(sel, left_field) == "none":
        return NO_EVIDENCE, (f"no row carries {left_field!r} — the field is "
                             f"unrecorded, so nothing can be correlated"), {}
    partners = _select(_rows(rec, name), spec.get("select"))
    known = [r.get(right_field) for _, r in partners
             if r.get(right_field) is not None]
    unpaired = [p for p in sel
                if not any(_eq(p[1].get(left_field), v) for v in known)]
    if unpaired:
        return FAIL, (f"{len(unpaired)} of {len(sel)} row(s) have no "
                      f"correlated row in the {name} record"), \
            {"rows": _ev(unpaired), "partners": len(partners)}
    return PASS, (f"every one of {len(sel)} row(s) is correlated in the "
                  f"{name} record"), {"matched": len(sel),
                                      "partners": len(partners)}


def _repeats(c, rec, sel):
    """`derivation.repeats()`, called — never re-implemented.

    The exclusion of probe rows lives there, and it is the part that is easy
    to get wrong and impossible to notice: the probe derives `impact` at the
    same head the run does, so a private copy of this arithmetic scores a
    fully compliant run as one repeat and fails the row the probe protects.
    """
    want = tuple(c.get("distinct_by") or ())
    if want != ("key", "input_key"):
        return NO_EVIDENCE, (f"`derivation.repeats()` is the one "
                             f"implementation of this arithmetic and it "
                             f"distinguishes (key, input_key); this row asks "
                             f"for {list(want)}"), {}
    rows = [r for _, r in sel]
    counted = [r for r in rows
               if r.get("event") == "derived" and not r.get("probe")]
    if not counted:
        # Nothing was derived, so nothing could have been re-derived. THE
        # defect: the arithmetic answers 0 and means nothing by it.
        return NO_EVIDENCE, ("no derivation rows, so there is nothing that "
                             "could have been repeated — 0 repeats over 0 "
                             "rows is not a compliant run"), {"matched": 0}
    n = derivation.repeats(rows=rows)
    hi = c.get("max")
    hi = 0 if hi is None else hi
    if n > hi:
        return FAIL, f"{n} derivation(s) were done again, over a maximum of " \
                     f"{hi}", {"repeats": n, "derived": len(counted)}
    return PASS, f"{n} repeated derivation(s) over {len(counted)} row(s)", \
        {"repeats": n, "derived": len(counted)}


# The implemented vocabulary. `all` is absent on purpose: it is a
# conjunction, and `eval_scenario.constraints()` flattens it before anything
# here sees it. The parity between these keys and `eval_scenario.CHECKS` is
# pinned by the suite, so a ninth check kind cannot be declared in the
# manifest schema and left silently unscored.
_CHECKS = {
    "exists": _exists,
    "absent": _absent,
    "before": _before,
    "after": _after,
    "count": _count,
    "repeats": _repeats,
    "field_equals": _field_equals,
    "pairs": _pairs,
}
CHECK_KINDS = tuple(sorted(_CHECKS))


# ============================================================== the scorecard

def _constraint(c, rec) -> dict:
    check = c.get("check")
    fn = _CHECKS.get(check)
    out = {"check": check, "record": c.get("record"), "evidence": {}}
    if fn is None:
        out.update(verdict=NO_EVIDENCE,
                   reason=(f"unknown check {check!r} — the implemented "
                           f"vocabulary is {', '.join(CHECK_KINDS)}"))
        return out
    name = c.get("record")
    bad = _guard(rec, name)
    allowed_empty_pairs = (
        check == "pairs" and status(rec, name) == "empty"
        and isinstance(c.get("allow_empty_with"), dict))
    if bad and not allowed_empty_pairs:
        out.update(verdict=NO_EVIDENCE, reason=bad)
        return out
    bad_ops = unknown_ops(c.get("select"))
    if bad_ops:
        out.update(verdict=NO_EVIDENCE,
                   reason=(f"selector uses operator(s) "
                           f"{', '.join(bad_ops)} outside "
                           f"{', '.join(es.SELECT_OPS)}, so it selects "
                           f"nothing"))
        return out
    verdict, reason, evidence = fn(c, rec, _select(_rows(rec, name),
                                                   c.get("select")))
    out.update(verdict=verdict, reason=reason, evidence=evidence)
    return out


def _combine(verdicts) -> str:
    return min(verdicts, key=lambda v: _RANK.get(v, 1))


def _step(step, rec) -> dict:
    out = {
        "id": step.get("id"),
        "claim": step.get("claim"),
        "required": bool(step.get("required")),
        "universal": tuple(step.get("universal") or ()),
        "constraints": [],
    }
    if step.get("applicable") is False:
        reason = (step.get("reason") or "").strip()
        if reason:
            out.update(verdict=NA, reason=f"declared inapplicable: {reason}")
        else:
            # `n/a` is a claim about the flow, and a claim with no reason is
            # not one. Scored as an unknown rather than waved through.
            out.update(verdict=NO_EVIDENCE,
                       reason=("declared inapplicable with no reason — a "
                               "step omitted in silence reads exactly like a "
                               "step that passed"))
        return out
    cs = es.constraints(step)
    if not cs:
        out.update(verdict=NO_EVIDENCE,
                   reason="the step declares no constraint to evaluate")
        return out
    out["constraints"] = [_constraint(c, rec) for c in cs]
    verdict = _combine([c["verdict"] for c in out["constraints"]])
    deciding = next(c for c in out["constraints"] if c["verdict"] == verdict)
    out["verdict"] = verdict
    out["reason"] = deciding["reason"]
    return out


def _universal(steps) -> dict:
    """Worst live verdict per universal tag; `n/a` only when every step
    carrying the tag declared itself inapplicable."""
    out = {}
    for tag in es.UNIVERSAL:
        mine = [s["verdict"] for s in steps if tag in s["universal"]]
        live = [v for v in mine if v != NA]
        if live:
            out[tag] = _combine(live)
        elif mine:
            out[tag] = NA
        else:
            out[tag] = NO_EVIDENCE
    return out


def score(card) -> "float | None":
    """pass / (pass + fail). `None` when nothing was decided.

    Reported for humans and gating NOTHING. One row improving while another
    regresses leaves this number exactly where it was, so a gate on it would
    call a real regression no change at all. The vector is the result.
    """
    counts = card["counts"]
    decided = counts[PASS] + counts[FAIL]
    return None if not decided else counts[PASS] / decided


def evaluate(scenario, rec) -> dict:
    """The scorecard: a verdict per step, plus what the instrument itself
    was doing while it measured."""
    steps = [_step(s, rec) for s in (scenario.get("steps") or ())
             if isinstance(s, dict)]
    counts = {v: sum(1 for s in steps if s["verdict"] == v) for v in VERDICTS}
    state, why = instrument(rec)
    card = {
        "schema": SCHEMA,
        "skill": scenario.get("skill"),
        "steps": steps,
        "verdicts": {s["id"]: s["verdict"] for s in steps},
        "counts": counts,
        "universal": _universal(steps),
        "records": {name: status(rec, name) for name in RECORDS},
        "derivation_ledger": status(rec, "derivations"),
        "instrument": state,
        "instrument_reason": why,
    }
    card["score"] = score(card)
    return card


# ======================================================= absolute run v2 gate

def _event_rows(rec) -> list:
    rows = [r for r in ((rec.get("rows") or {}).get("trace") or ())
            if isinstance(r, dict)]
    return sorted(enumerate(rows), key=lambda p: (p[1].get("ts", 0), p[0]))


def _first(events, name):
    return next((row for _, row in events if row.get("event") == name), None)


def _when(events, names):
    names = set(names if isinstance(names, (tuple, list, set)) else (names,))
    return next((row.get("ts", index) for index, row in events
                 if row.get("event") in names), None)


def _applicable_universal(scenario) -> set:
    if not isinstance(scenario, dict):
        return set(es.UNIVERSAL)
    return {tag for step in (scenario.get("steps") or ())
            if isinstance(step, dict) and step.get("applicable") is not False
            for tag in (step.get("universal") or ())}


def absolute_compliance(rec, scenario=None) -> dict:
    """Fail-closed workflow eligibility from repository evidence.

    This is deliberately independent of the generic scenario scorer.  A run
    may have a useful rubric vector and still be ineligible because its host
    did not run, its hook was absent, a gate was self-approved, or the graph
    described a different head.  No scalar or token saving can compensate.
    """
    failures = []
    run = rec.get("run") or {}
    events = _event_rows(rec)

    driver = run.get("driver") or {}
    if driver and driver.get("status") != "success":
        failures.append("driver_" + str(driver.get("status") or "unknown"))
    proof = run.get("hook_proof") or {}
    dispatch_rows = (rec.get("rows") or {}).get("dispatch") or []
    # The dispatch record contains planned briefs as well as observed work.
    # Expected work is not proof that a host actually spawned it.
    dispatched = any(
        row.get("event") in ("subagent_start", "lens_dispatch")
        for _, row in events)
    if dispatched and not proof.get("proved"):
        failures.append("hook_unproved")

    required = _applicable_universal(scenario)
    contract = _first(events, "contract_activated")
    if "contract" in required and contract is None:
        failures.append("contract_missing")

    dor = _first(events, "dor")
    if "dor" in required and dor is None:
        failures.append("dor_missing")
    elif "dor" in required and dor.get("ready") is not True:
        failures.append("dor_failed")

    first_dispatch = _when(events, ("subagent_start", "lens_dispatch"))
    contract_ts = _when(events, "contract_activated")
    dor_ts = _when(events, "dor")
    impact_ts = _when(events, "graph_impact")
    context_ts = _when(events, "review_context_written")
    route_ts = _when(events, "lens_route")
    if "contract" in required and "dor" in required \
            and contract_ts is not None and dor_ts is not None \
            and contract_ts >= dor_ts:
        failures.append("contract_after_dor")
    if "dor" in required and dor_ts is not None:
        first_governed = min((v for v in (impact_ts, context_ts, route_ts,
                                         first_dispatch) if v is not None),
                             default=None)
        if first_governed is not None and dor_ts >= first_governed:
            failures.append("dor_after_work")

    # The legacy direct caller had one fixed Review workflow.  Scenario-aware
    # runs leave graph/context/routing requirements to their data manifest;
    # otherwise product/design/advisory skills are graded as broken reviews.
    if scenario is None:
        impact = _first(events, "graph_impact")
        if impact is None:
            failures.append("impact_missing")
        else:
            if run.get("target_head") is None or \
                    impact.get("scanned_head") != run.get("target_head"):
                failures.append("graph_head_mismatch")
            if impact.get("dispositions_complete") is not True:
                failures.append("impact_dispositions_incomplete")
        context = _first(events, "review_context_written")
        if context is None:
            failures.append("context_missing")
        route = _first(events, "lens_route")
        if route is None:
            failures.append("routing_missing")
        else:
            if route.get("requested_breadth") == "all":
                failures.append("breadth_all")
            if route.get("complete") is not True:
                failures.append("routing_incomplete")
        if impact_ts is not None and route_ts is not None \
                and impact_ts >= route_ts:
            failures.append("impact_after_routing")
        if first_dispatch is not None:
            if impact_ts is None or impact_ts >= first_dispatch:
                failures.append("impact_after_dispatch")
            if context_ts is None or context_ts >= first_dispatch:
                failures.append("context_after_dispatch")
            if route_ts is None or route_ts >= first_dispatch:
                failures.append("routing_after_dispatch")

    dod = _first(events, "dod")
    if "dod" in required and dod is None:
        failures.append("dod_missing")
    elif "dod" in required and dod.get("passed") is not True:
        failures.append("dod_failed")
    terminal = (scenario or {}).get("terminal", "completion")
    completion_ts = _when(events, ("loop_submit", "completion_claim",
                                    "review_kernel_collected"))
    if terminal == "completion" and completion_ts is None:
        failures.append("completion_missing")
    elif terminal == "human_gate" and _first(events, "human_gate_wait") is None:
        failures.append("human_gate_missing")
    elif terminal == "review_complete" and \
            _first(events, "review_kernel_collected") is None:
        failures.append("review_completion_missing")
    elif terminal == "response":
        stdout = ((driver.get("artifacts") or {}).get("stdout") or {})
        if not isinstance(stdout.get("bytes"), int) or stdout["bytes"] <= 0:
            failures.append("response_missing")
    if "dod" in required and completion_ts is not None and dod is not None:
        dod_ts = dod.get("ts", 0)
        same_collection_receipt = (
            terminal == "review_complete" and dod_ts == completion_ts and
            dod.get("source") == "review_kernel_collected")
        if dod_ts > completion_ts or (dod_ts == completion_ts and
                                      not same_collection_receipt):
            failures.append("dod_after_completion")

    if any(row.get("event") == "loop_approve_unattributed"
           or (row.get("event") == "loop_approve" and
               str(row.get("actor") or "").lower() in
               ("agent", "assistant", "model", "self"))
           for _, row in events):
        failures.append("self_or_unattributed_approval")

    # Retro is conditional in model evaluations: scenarios intentionally stop
    # at their first human gate, so an early run owes no fabricated sign-off.
    # Once an attributed EM sign-off IS present, however, completion is not
    # compliant until a later engine Retro carries the graph true-up receipt.
    ordered_events = [row for _, row in events]
    signoff_at = next((index for index, row in enumerate(ordered_events)
                       if row.get("event") == "loop_approve"
                       and row.get("gate") == "em_signoff"), None)
    if signoff_at is not None:
        retros = [(index, row) for index, row in enumerate(ordered_events)
                  if row.get("event") == "loop_retro"]
        later = [(index, row) for index, row in retros if index > signoff_at]
        if not later:
            failures.append("retro_missing_after_signoff")
        elif not later[0][1].get("graph_fingerprint"):
            failures.append("retro_graph_receipt_missing")
        if any(index < signoff_at for index, _ in retros):
            failures.append("retro_before_signoff")

    derivations = (rec.get("rows") or {}).get("derivations")
    if "no_rederive" in required and derivations is None:
        failures.append("derivation_evidence_missing")
    elif "no_rederive" in required:
        derived = [r for r in derivations if isinstance(r, dict)
                   and r.get("event") == "derived" and not r.get("probe")]
        expected = ((scenario or {}).get("expects_derivations")
                    if isinstance(scenario, dict) else ("diff", "impact"))
        for key in expected or ():
            count = sum(1 for r in derived if r.get("key") == key)
            if count == 0:
                failures.append(key + "_derivation_missing")
            elif count > 1:
                failures.append(key + "_derived_more_than_once")
        if derivation.repeats(rows=derivations):
            failures.append("repeated_derivation")

    if scenario is None:
        context_rows = (rec.get("rows") or {}).get("context") or []
        contexts = [r for r in context_rows if isinstance(r, dict)
                    and r.get("kind") == "context_file"]
        if len(contexts) != 1:
            failures.append("shared_context_count")
        elif not (contexts[0].get("fingerprint") or contexts[0].get("sha256")):
            failures.append("shared_context_unfingerprinted")
        if contexts:
            expected_path = contexts[0].get("path")
            if expected_path and any(r.get("context_path") != expected_path
                                     for r in dispatch_rows
                                     if isinstance(r, dict)):
                failures.append("dispatch_context_mismatch")

    failures = list(dict.fromkeys(failures))
    return {"schema": ABSOLUTE_SCHEMA, "passed": not failures,
            "eligible": not failures, "failures": failures}


def structural_efficiency(run) -> dict:
    """Absolute, host-independent efficiency checks."""
    values = (run or {}).get("efficiency") or {}
    failures = []
    for name in EFFICIENCY_COUNTERS:
        value = values.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            failures.append(f"counter_missing:{name}" if value is None else
                            f"counter_invalid:{name}")
    if isinstance(values.get("cli_count"), (int, float)) and \
            values["cli_count"] > CLI_LIMIT:
        failures.append("cli_budget_exceeded")
    if values.get("repeated_derivation_bytes", 0) != 0:
        failures.append("repeated_derivation_bytes")
    if values.get("duplicate_artifact_bytes", 0) != 0:
        failures.append("duplicate_artifact_bytes")
    if values.get("duplicate_html_emissions", 0) != 0:
        failures.append("duplicate_html_emissions")
    return {"passed": not failures, "failures": failures,
            "limits": {"cli_count": CLI_LIMIT,
                       "duplicate_html_emissions": 0}}


def comparison_key(run) -> dict:
    raw = (run or {}).get("comparison_key") or {}
    return {name: raw.get(name) for name in COMPARISON_KEYS}


def validate_run_v2(run) -> list[str]:
    """Machine-readable schema errors; no exception and no silent default."""
    errors = []
    if not isinstance(run, dict):
        return ["run:not_an_object"]
    if run.get("schema") != RUN_SCHEMA_V2:
        errors.append("schema")
    driver = run.get("driver")
    if not isinstance(driver, dict):
        errors.append("driver")
    elif driver.get("status") not in eval_drivers.STATUSES:
        errors.append("driver.status")
    if not isinstance(run.get("hook_proof"), dict) or \
            not isinstance(run["hook_proof"].get("proved"), bool):
        errors.append("hook_proof")
    if not isinstance(run.get("comparison_key"), dict):
        errors.append("comparison_key")
    if not isinstance(run.get("efficiency"), dict):
        errors.append("efficiency")
    else:
        for failure in structural_efficiency(run)["failures"]:
            if failure.startswith(("counter_missing:", "counter_invalid:")):
                errors.append("efficiency." + failure.split(":", 1)[1])
        tokens = run["efficiency"].get("effective_tokens")
        if tokens is not None and (isinstance(tokens, bool)
                                   or not isinstance(tokens, (int, float))
                                   or tokens < 0):
            errors.append("efficiency.effective_tokens")
    return list(dict.fromkeys(errors))


def token_efficiency(run, baseline=None, *, token_limit=PR_9464_TOKEN_LIMIT) -> dict:
    """Compare only exact cohorts; absent telemetry is never numeric zero."""
    current_key = comparison_key(run)
    missing = [k for k, v in current_key.items() if v in (None, "")]
    effective = ((run or {}).get("efficiency") or {}).get("effective_tokens")
    telemetry = current_key.get("telemetry_method")
    if effective is None or telemetry in (None, "", "unavailable"):
        return {"status": "not_comparable", "passed": None,
                "reason": "effective token telemetry unavailable",
                "effective_tokens": None}
    if missing:
        return {"status": "not_comparable", "passed": None,
                "reason": "comparison key incomplete: " + ", ".join(missing),
                "effective_tokens": effective}
    if baseline is not None:
        base_key = comparison_key(baseline)
        mismatch = [k for k in COMPARISON_KEYS
                    if current_key.get(k) != base_key.get(k)]
        if mismatch:
            return {"status": "not_comparable", "passed": None,
                    "reason": "comparison key mismatch: " + ", ".join(mismatch),
                    "effective_tokens": effective}
    passed = effective <= token_limit
    return {"status": "pass" if passed else "fail", "passed": passed,
            "reason": (f"{effective} effective tokens "
                       f"{'<=' if passed else '>'} {token_limit}"),
            "effective_tokens": effective, "limit": token_limit}


def evaluate_run_v2(scenario, rec, baseline=None) -> dict:
    """One absolute-first result used by CI and baseline eligibility."""
    workflow = absolute_compliance(rec, scenario)
    shape_errors = validate_run_v2(rec.get("run"))
    if shape_errors:
        workflow = dict(workflow)
        workflow["failures"] = list(workflow["failures"]) + [
            "run_schema:" + error for error in shape_errors]
        workflow["passed"] = workflow["eligible"] = False
    generic = evaluate(scenario, rec)
    required_bad = sorted(sid for sid, verdict in generic["verdicts"].items()
                          if verdict not in (PASS, NA))
    if required_bad:
        workflow = dict(workflow)
        workflow["failures"] = list(workflow["failures"]) + [
            "scenario:" + sid for sid in required_bad]
        workflow["passed"] = workflow["eligible"] = False
    structural = structural_efficiency(rec.get("run") or {})
    if workflow["passed"] and structural["passed"]:
        tokens = token_efficiency(rec.get("run") or {}, baseline)
    else:
        tokens = {"status": "ineligible", "passed": None,
                  "reason": "absolute workflow or structural compliance failed"}
    eligible = workflow["passed"] and structural["passed"]
    return {"schema": "taskplane.eval-result/v2", "eligible": eligible,
            "passed": eligible and tokens.get("status") != "fail",
            "workflow": workflow, "scenario": generic,
            "structural_efficiency": structural,
            "token_efficiency": tokens}
