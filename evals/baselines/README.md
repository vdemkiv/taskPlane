# evals/baselines/ — the bar, per rubric item

This directory is the store the eval gate reads. It holds two kinds of file
per governed skill, and nothing else:

    <skill>.json            the baseline: a VERDICT VECTOR
    <skill>.waivers.jsonl   the waiver log: append-only, one lowering per line

Both are written and read by `scripts/ci_evals.py`. It is empty of baselines
on purpose: a baseline may only be set from an out-of-band run that the
dispatch hook actually observed, and such a run needs a model. Committing a
hand-written one would be a bar nobody measured, which is the exact thing the
eligibility check exists to refuse.

    python3 scripts/eval_record.py ...            # record an observed run
    python3 scripts/ci_evals.py --skill  <skill>  # look at the scorecard
    python3 scripts/ci_evals.py --set-baseline <skill>
    python3 scripts/ci_evals.py --gate --skill <skill>

## Why the baseline is a vector and not a number

`eval_rubric.evaluate()` also reports `score` — pass over pass plus fail. It
gates NOTHING, and this store does not carry it under that name (it is stored
as `score_for_humans`, so nobody compares it by reflex). Two arithmetic facts
are the whole reason:

  * one row improving while another regresses leaves the average exactly
    where it was; and
  * a row falling to `no_evidence` leaves the DENOMINATOR smaller, so the
    average goes UP.

`evals/negative/no-ledger/` is that argument as a fixture rather than a claim:
it pins `score: 1.0` beside `instrument: broken`. A bar of "not worse than
last time" is passed, perfectly, by an instrument that has gone blind.

## `<skill>.json`

```json
{
  "schema": "taskplane.eval-baseline/v1",
  "skill": "tp-engineering",
  "verdicts": {"R1": "pass", "R2": "pass", "R3": "no_evidence"},
  "inputs_fingerprint": "2ad09bc3…",
  "source_files": {"skills/tp-engineering/SKILL.md": "0f21…",
                   "agents/tp-engineering.md": "9c4b…"},
  "run": {"run_id": "20260813T101500Z-ab12cd",
          "path": "evals/runs/tp-engineering/20260813T101500Z-ab12cd",
          "mode": "out-of-band", "host": "claude", "hook_active": true,
          "recorded_at": 1755080100.0, "target_head": "…",
          "effective_tokens": null},
  "instrument": "ok",
  "score_for_humans": 0.86
}
```

`inputs_fingerprint` is the digest of the FLOW the skill's source files
mandate — not of their bytes, so a typo does not fire a gate people would
learn to waive. `source_files` carries the same digest per file, which is how
a STALE verdict names the input that moved instead of printing two hexes.

There is no wall clock anywhere in the file. Setting the same baseline from
the same run twice produces a byte-identical result, so `git diff` on this
directory shows a change only when the bar actually changed.

## `<skill>.waivers.jsonl`

One JSON object per line, appended, never edited in place:

```json
{"step": "R4", "from": "pass", "to": "no_evidence",
 "reason": "the lens dispatch hook is off on this host until #412 lands",
 "acceptor": "Ada Lovelace",
 "inputs_fingerprint": "2ad09bc3f1e4",
 "expires": "2026-11-30"}
```

Five required fields and two optional ones:

| field | required | what it does |
|---|---|---|
| `step` | yes | the rubric item. A blanket waiver is not a waiver. |
| `reason` | yes | the sentence someone has to be willing to have read back to them. |
| `acceptor` | yes | who is claiming this — see the section below for what that does and does not prove. |
| `inputs_fingerprint` | yes | the RELEVANCE bound: the flow this was argued about. |
| `expires` | yes | the WALL-CLOCK bound: `YYYY-MM-DD`, UTC, expiring at the end of that day. |
| `from` / `to` | no | NARROW the waiver to one transition, so a waiver written for an evidence gap does not silently absorb a later real failure of the same row. |

A malformed, unattributed or UNBOUNDED row BLOCKS rather than being skipped —
the row someone could not write correctly may be the row that was meant to
cover the drop being gated.

### Why a waiver is bounded, and on those two axes

A waiver is the only thing that lets a baseline drop past the gate. Without a
bound it covers that step's drops FOREVER: a sentence written once for a
transient evidence gap absorbs a real regression six months later, and nobody
re-reads it because it never asks to be re-read. Nothing is grandfathered —
this directory carried no waiver when the bounds landed, which made strict
free then and impossible later.

**`inputs_fingerprint` — tied to the thing that changed.** Copy it from the
baseline the drop is being gated against (a prefix of 12 hex characters or
more is enough; anything shorter, or anything that is not a digest, is
refused). The waiver applies only while the run being gated still digests to
that flow. When the skill moves, the drop that reappears under it is a
DIFFERENT drop, and the old sentence is not an argument about the new flow.

Run identity (`run_id`) was the other candidate for this axis and is not used:
it changes on every re-record, including re-records that changed nothing, so
it would train people to rewrite waivers mechanically — which is the habit the
bound exists to prevent.

**`expires` — the calendar.** The second axis, and the one that forces a
re-reading even when nothing moved. It may be at most **90 days** out; a date
further away satisfies a required-field check and bounds nothing, so it is
refused with the horizon named. Renewing is one appended line: re-read the
reason, decide it is still true, sign it again.

### What the gate does with each state

| state | when | gate |
|---|---|---|
| in force | inside both bounds | covers its drop, prints `WAIVED by …` plus the acceptor disclaimer |
| `EXPIRING` | within 14 days of `expires` | still covers; NAMED by an otherwise-green `--gate` run, with the date and the days left, so re-reading is prompted by the tool rather than by a broken build |
| `EXPIRED` | past `expires`, flow unchanged | covers nothing and **blocks**, naming the line, the date and the acceptor. Answer it by appending a renewal for the same `(step, from, to)`; the superseded row is still printed, never erased |
| `OUT OF SCOPE` | the run's fingerprint no longer starts with the waiver's | covers nothing, is reported on every run, and blocks only the drop it would have covered. The log is append-only, so a spent waiver has to be able to retire without reddening the build forever |

Scope is checked before the clock: a waiver about a flow that has moved has
nothing left to re-read, so demanding a renewal of it would be busywork with a
red build attached.

The failure mode all of this exists to avoid is a waiver quietly ceasing to
apply and the regression it used to cover surfacing later as if it were new.
Every state above is printed by name.

### What the acceptor check does not prove

**The acceptor string is not authenticated.** In this product the committer is
routinely the model, and it satisfies this check by typing a human's name.
The check rejects the two identities the machine already answers to — an
agent name from `agents/`, and the `taskplane-role:` marker the engine stamps
on its own briefs — and it stops there deliberately: a longer blocklist would
read as a stronger control than it is. There is no signature, no key, no
session identity behind it, and adding a mechanism that LOOKED like one would
be worse than the gap, because a fake control reads as a real one.

What a waiver buys is ATTRIBUTION. Every lowering of the bar is a named,
reasoned, dated-by-git line in an append-only file, printed by the gate when
it is applied, and visible in review. It is not authorisation, not consent,
and not evidence that the named person ever saw it. Read a waiver as a claim
to be checked, never as a signature.

The bounds above are the answer to that gap, and they are not authentication
either. `expires` is read against the clock of whatever machine runs the gate,
and `inputs_fingerprint` is checked against a run record the same committer
produced — neither is a trusted third party, and neither can tell you who
wrote the row. What they do is make an
unauthenticated waiver COST something to keep — it stops working when the
skill it was written about moves, and it has to be re-read and signed again
at least quarterly. A control nobody can forge is not on offer here. A
control nobody can forget is.
