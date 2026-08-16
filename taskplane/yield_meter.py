"""The yield meter — what the harness RETURNS, next to what it costs.

WHY THIS EXISTS. taskplane has had a cost meter since R-0012:
`scripts/ci_loop_cost.py` pins how many lenses fire, how many engine calls a
task mandates, how many gates it passes. Every one of those numbers is
SPEND. Nothing anywhere recorded RETURN — which lens produced a finding a
human acted on, and which produced prose nobody read. So the only number
visible was the one going up, and every question about simplifying the
harness ("can we drop these six lenses?") had to be settled by taste.

This module records the other half. It exists to make DELETION defensible:
after a few months of real reviews, a lens that has fired forty times and
produced nothing anyone acted on can be removed with evidence instead of
argument. The instrument is subtractive by intent.

TWO MEASUREMENTS, and only two.

  LENS YIELD    per lens: reviews it was routed into, findings it produced,
                and what became of them.
  ESCAPE        per finding: WHICH GATE CAUGHT IT. The question behind this
                is "are defects slipping past implementation and surfacing
                only at review?" — caught-at-evaluate is cheap, caught-at-
                review is expensive, and filed-after-signoff is the one that
                reached a user.

WHAT IS A FACT AND WHAT IS A GUESS. `caught_at` is the loop step at the
moment the finding was recorded: a fact, taken from state, never inferred.
The stage that INTRODUCED a defect is not knowable from here, so it is not
stored and not reported — a plausible-looking origin column would be the
kind of fabricated precision this meter exists to replace.

Dispositions are likewise split and never blended:

  explicit    a human said acted / dismissed. Strong.
  inferred    the finding stopped recurring in later reviews, or persisted
              across them. WEAK — a finding also stops recurring because
              the diff moved on. Reported in its own columns, never added
              into the explicit ones, and never called "acted".
  unknown     no later review exists yet. Reported as unknown rather than
              rounded to zero, which would slander a lens, or to acted,
              which would flatter one.

WHAT THIS MODULE MAY NOT DO. It gates nothing. It cannot fail CI, block a
loop, refuse a tool, or change any verdict. The engine never reads it. Every
write is best-effort and swallowed: a broken ledger must never cost anyone a
gate. Delete this file and taskplane still works exactly as before — which
is the property that makes it safe to have added, and easy to remove if it
turns out not to earn its own keep.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

import taskplane_lite as tp
import storage as runtime_storage

LEDGER = "yield.jsonl"
SETTLED_VERDICTS = frozenset({
    "acted", "dismissed", "resolved", "accepted", "closed", "deferred",
    "not-a-defect",
})

# Reporting order, cheapest bucket first. Derived from BUCKETS below rather
# than restated, because the first version was a hand-written second list
# that named a step (`review`) the engine does not emit and was then never
# read by anything — a constant that documented a lie.

# Headline buckets. The point of the metric is the cost of lateness, so the
# per-step counts roll up into three that differ by an order of magnitude.
# The ENGINE's step ids, not plausible-looking synonyms. `em` is the review
# step (loop.py); `review` is not a step the loop ever emits. The first
# version listed `review` and relied on _bucket()'s fallthrough, so every em
# finding landed in at_review by accident and `fix` — a genuine in-task step
# — landed there too. Change the fallthrough and the headline inverts.
BUCKETS = {
    "in_task": ("execute", "evaluate", "fix"),
    "at_review": ("em", "signoff", "plan", "design", "design_approval"),
    "after_signoff": ("post-signoff",),
}
# Every step id BUCKETS knows, so a step the engine adds later cannot be
# silently absorbed by the fallthrough.
KNOWN_STEPS = frozenset(s for steps in BUCKETS.values() for s in steps)

_WS = re.compile(r"\s+")
# `foo.py:12`, `#12`, and the prose forms `line 12` / `L12`. Not every digit
# — "timeout 300s" and "timeout 30s" are different findings, and collapsing
# them would merge two claims into one row.
_LINENO = re.compile(r"[:#]\s*\d+\b|\bl(?:ine)?s?\.?\s*\d+\b",
                     re.IGNORECASE)


def ledger_path(ws: str) -> str:
    """In the project STORE, not `.taskplane/`.

    Runtime state is per-checkout, git-ignored and rotated; this ledger has
    to accumulate across checkouts and months to be worth anything. It
    follows `store_root`, so it lands wherever the project's mode already
    puts the knowledge base — private by default, repo-shared on a team.
    """
    return os.path.join(tp.store_root(ws), LEDGER)


def fingerprint(finding: dict) -> str:
    """Stable across reviews, so recurrence is detectable.

    Line numbers shift under any edit and would make every finding look new,
    so they are stripped from the title before hashing; the lens, the file
    and the claim are what identify it.
    """
    lens = str(finding.get("lens") or finding.get("domain") or "")
    path = tp.to_posix(str(finding.get("file") or finding.get("path") or ""))
    title = str(finding.get("title") or finding.get("summary") or "")
    title = _WS.sub(" ", _LINENO.sub("", title)).strip().lower()[:120]
    raw = "\x1f".join((lens, path, title)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _append(ws: str, record: dict) -> None:
    """Best effort, always. A meter must never cost anyone a gate."""
    try:
        path = ledger_path(ws)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        record.setdefault("ts", time.time())
        with tp.file_lock(path):
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str, sort_keys=True) + "\n")
    except Exception:
        pass


def record_review(ws: str, routed_lenses, *, caught_at: str,
                  review_id: str | None = None) -> str:
    """One record per review naming the lenses that FIRED.

    Without it a lens that fires often and finds nothing is
    indistinguishable from a lens that never fires at all — and those two
    call for opposite decisions.
    """
    rid = review_id or hashlib.sha256(
        f"{ws}|{time.time()}".encode("utf-8")).hexdigest()[:12]
    _append(ws, {"kind": "review", "review": rid, "caught_at": caught_at,
                 "fired": sorted({str(x) for x in (routed_lenses or [])})})
    return rid


def record_findings(ws: str, findings, *, caught_at: str,
                    review_id: str | None = None,
                    blockers=None) -> int:
    """Record each finding once, with the gate that caught it."""
    blocking = {fingerprint(f) for f in (blockers or [])}
    n = 0
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        fp = fingerprint(f)
        _append(ws, {"kind": "finding", "review": review_id, "fp": fp,
                     # a fingerprint nobody can read is a fingerprint nobody
                     # will disposition — carry just enough to recognise it
                     "label": _WS.sub(" ", str(
                         f.get("title") or f.get("summary") or "")).strip()[:70],
                     "lens": str(f.get("lens") or f.get("domain") or ""),
                     "file": tp.to_posix(str(
                         f.get("file") or f.get("path") or "")),
                     "class": str(f.get("class") or ""),
                     "severity": str(f.get("severity") or ""),
                     "blocks": fp in blocking,
                     "caught_at": caught_at})
        n += 1
    return n


def record_notes(ws: str, notes, *, caught_at: str,
                 review_id: str | None = None) -> int:
    """Persist routed commentary without polluting the findings surface."""
    n = 0
    for row in notes or []:
        if not isinstance(row, dict):
            continue
        _append(ws, {
            "kind": "note", "review": review_id,
            "fp": fingerprint(row),
            "label": _WS.sub(" ", str(
                row.get("title") or row.get("summary") or "")).strip()[:70],
            "lens": str(row.get("lens") or row.get("domain") or ""),
            "file": tp.to_posix(str(row.get("file") or row.get("path") or "")),
            "reason": str(row.get("admissibility_reason") or "note"),
            "caught_at": caught_at,
        })
        n += 1
    return n


def record_disposition(ws: str, fp: str, verdict: str, *,
                       by: str | None = None, note: str = "") -> dict:
    """A human's durable verdict on one finding.

    Explicit dispositions are asked for on BLOCKERS only. Those are the
    findings a human already reads at the gate, so the marginal cost is a
    keystroke; asking for the long tail would buy accuracy with exactly the
    friction this meter exists to help remove.
    """
    verdict = str(verdict or "").strip().lower()
    if verdict not in SETTLED_VERDICTS:
        return {"error": "verdict must be one of " +
                ", ".join(sorted(SETTLED_VERDICTS)) + f", got {verdict!r}"}
    if not fp:
        return {"error": "no finding fingerprint"}
    _append(ws, {"kind": "disposition", "fp": fp, "verdict": verdict,
                 "by": by or "(unattributed)", "note": note})
    return {"recorded": fp, "verdict": verdict}


def settled_findings(ws: str, *, files=None, limit: int = 200) -> list:
    """Bounded settled identities for a future review brief.

    Full historical bodies never cross the agent boundary.  The last
    disposition wins, and only identities scoped to changed files are
    returned when file metadata exists.
    """
    records = read_ledger(ws)
    metadata, dispositions = {}, {}
    for row in records:
        fp = str(row.get("fp") or "")
        if not fp:
            continue
        if row.get("kind") == "finding":
            metadata[fp] = {key: row.get(key) for key in
                            ("fp", "label", "lens", "file")}
        elif row.get("kind") == "disposition":
            dispositions[fp] = str(row.get("verdict") or "").lower()
    scope = {tp.to_posix(str(path)) for path in (files or []) if str(path)}
    rows = []
    for fp in sorted(dispositions):
        verdict = dispositions[fp]
        if verdict not in SETTLED_VERDICTS:
            continue
        meta = metadata.get(fp)
        if not meta:
            continue
        path = str(meta.get("file") or "")
        if scope and path and path not in scope:
            continue
        rows.append({**meta, "disposition": verdict})
        if len(rows) >= max(0, min(int(limit), 500)):
            break
    return rows


def record_counts(ws: str, lens: str, blockers: int, *,
                  caught_at: str, review_id: str | None = None) -> None:
    """A lens's blocker COUNT where the identities are not available.

    The evaluate gate's verdict reports `{lens, verdict, blockers: N}` — a
    number, not a list. Those blockers are real and they matter to the
    escape metric (they were caught IN the task, the cheap place), but they
    cannot be fingerprinted, so they cannot be dispositioned or deduped
    against a later review. They are stored as counts and reported as
    counts. Manufacturing a fingerprint for them would be the fabricated
    precision this meter exists to replace.
    """
    try:
        n = max(0, int(blockers or 0))
    except (TypeError, ValueError):
        return
    if not n:
        return
    _append(ws, {"kind": "counts", "review": review_id, "lens": str(lens),
                 "blockers": n, "caught_at": caught_at})


def gate_snapshot(ws: str, step: str, outcome: str) -> None:
    """THE hook: one call, at the one place every gate transition passes.

    Best-effort and idempotent. The review id is derived from the CONTENT,
    so re-gating the same findings records nothing new — gates get retried,
    and a meter that double-counted retries would reward flakiness.
    """
    try:
        if str(outcome or "").lower() not in ("pass", "fail"):
            return
        step = str(step or "")
        findings, routed = _artifact(ws, step)
        counts = _verdict_counts(ws) if step == "evaluate" else []
        if not findings and not routed and not counts:
            return
        seen = {r.get("review") for r in read_ledger(ws)}
        rid = "r" + hashlib.sha256(
            ("|".join([step] + sorted(fingerprint(f) for f in findings)
                      + sorted(routed)
                      + [f"{k}:{v}" for k, v in sorted(counts)])
             ).encode("utf-8")).hexdigest()[:11]
        if rid in seen:
            return
        record_review(ws, routed, caught_at=step, review_id=rid)
        record_findings(ws, findings, caught_at=step, review_id=rid,
                        blockers=[f for f in findings
                                  if str(f.get("severity", "")).lower()
                                  in ("high", "critical", "blocker")])
        for lens, n in counts:
            record_counts(ws, lens, n, caught_at=step, review_id=rid)
    except Exception:
        pass


def observation_bundle(ws: str, operation: str, observations) -> dict:
    """Meter one coarse read-only control operation as exactly one action.

    This is accounting, not an exemption: model work, leased writes and host
    tool calls continue through their existing hook/action paths.  Only the
    deterministic internal reads performed by a coarse command are bundled.
    """
    names = sorted({str(value).strip() for value in (observations or [])
                    if str(value).strip()})
    row = {"schema": "taskplane.observation-bundle/v1",
           "operation": str(operation), "observations": names,
           "actions": 1}
    try:
        import taskplane_lite as tp
        tp.trace(ws, "observation_bundle", operation=str(operation),
                 observations=names, actions=1)
    except Exception:
        pass
    return row


# Which step actually writes .em-review/findings.json. Everything else must
# NOT read it: loop.gate calls gate_snapshot on EVERY transition and nothing
# deletes that file, so a step-blind read re-recorded one review's findings
# at the next fix and evaluate gates — inflating the count AND landing them
# in the cheap in-task bucket, i.e. moving the headline metric in the
# flattering direction.
_FINDINGS_STEPS = frozenset({"em", "signoff"})


def _artifact(ws: str, step: str):
    """(findings, routed_lenses) for a step, from whatever THAT step wrote.

    Only the em/signoff path produces identified findings. The evaluate path
    reports per-lens counts, which are recorded separately by the caller of
    `record_counts` rather than being inflated into findings here.
    """
    findings, routed = [], []
    if step not in _FINDINGS_STEPS:
        return findings, routed
    doc = _load(runtime_storage.review_public_path(ws, "findings.json"))
    if isinstance(doc, dict):
        raw = doc.get("findings")
        meta = doc.get("meta") or {}
        routed = [str(x) for x in (meta.get("routed") or meta.get("lenses")
                                   or [])]
    else:
        raw = doc
    for f in raw or []:
        if isinstance(f, dict):
            findings.append(f)
    if not routed:
        routed = sorted({str(f.get("lens") or f.get("domain") or "")
                         for f in findings} - {""})
    return findings, routed


def _verdict_counts(ws: str) -> list:
    """[(lens, blockers)] from `.eval/verdict.json`, the evaluate gate's own
    artifact. Blockers caught HERE are caught in the task — the cheap place
    — which is precisely the comparison the escape metric exists to make."""
    doc = _load(runtime_storage.evaluation_path(ws))
    rows = (doc or {}).get("lenses") if isinstance(doc, dict) else None
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            n = int(row.get("blockers") or 0)
        except (TypeError, ValueError):
            continue
        if n > 0 and row.get("lens"):
            out.append((str(row["lens"]), n))
    return out


def _load(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _safe_ledger_path(ws: str) -> str:
    """For DISPLAY only — never let naming the file be the
    thing that crashes the report about the file."""
    try:
        return ledger_path(ws)
    except Exception:
        return "(store unavailable)"


def read_ledger(ws: str) -> list:
    """Every record, oldest first. Malformed lines are skipped, not fatal."""
    out = []
    try:
        # `ledger_path` resolves the store, which can itself fail (missing
        # home, unreadable mode config). Reading must be TOTAL: `tp yield`
        # degrading to "nothing recorded" is fine; a traceback is not.
        path = ledger_path(ws)
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except (OSError, ValueError, TypeError):
        return []
    return out


def _bucket(caught_at: str) -> str:
    for name, steps in BUCKETS.items():
        if caught_at in steps:
            return name
    return "at_review"


def report(ws: str) -> dict:
    """Lens yield and escape buckets. Explicit and inferred stay apart."""
    records = read_ledger(ws)
    reviews = [r for r in records if r.get("kind") == "review"]
    findings = [r for r in records if r.get("kind") == "finding"]
    notes = [r for r in records if r.get("kind") == "note"]
    dispositions = {}
    for r in records:
        if r.get("kind") == "disposition" and r.get("fp"):
            dispositions[r["fp"]] = r["verdict"]     # last word wins

    fired = {}
    for r in reviews:
        for lid in r.get("fired") or []:
            fired[lid] = fired.get(lid, 0) + 1

    # A finding is "still open" if it appears in the LATEST review that its
    # lens fired in. Anything earlier that no longer appears stopped
    # recurring — weak evidence, labelled as such.
    last_seen, first_seen = {}, {}
    for r in findings:
        fp, ts = r.get("fp"), r.get("ts") or 0
        if not fp:
            continue
        first_seen.setdefault(fp, ts)
        last_seen[fp] = max(last_seen.get(fp, 0), ts)
    # PER LENS, not global. The comment above states the rule as "the latest
    # review that ITS LENS fired in" and the first version computed one
    # global newest — so a security finding was marked stopped_recurring the
    # moment ANY later review landed, including one that routed no lenses in
    # common with it. That populates the inference column with events
    # carrying no information about the finding.
    newest_by_lens = {}
    for r in reviews:
        ts = r.get("ts") or 0
        for lid in r.get("fired") or []:
            if ts > newest_by_lens.get(lid, 0):
                newest_by_lens[lid] = ts

    lenses, seen_fp = {}, set()
    for r in findings:
        fp = r.get("fp")
        lid = r.get("lens") or "(unattributed)"
        row = lenses.setdefault(lid, {
            "lens": lid, "fired": 0, "findings": 0, "blockers": 0,
            "acted": 0, "dismissed": 0,
            "stopped_recurring": 0, "persisted": 0, "unknown": 0})
        row["findings"] += 1
        if r.get("blocks"):
            row["blockers"] += 1
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        verdict = dispositions.get(fp)
        if verdict in {"acted", "resolved", "closed"}:
            row["acted"] += 1
        elif verdict in {"dismissed", "accepted", "deferred", "not-a-defect"}:
            row["dismissed"] += 1
        else:
            # Only reviews THIS lens fired in can say anything about
            # whether its finding recurred.
            newest = newest_by_lens.get(lid, 0)
            if newest <= (first_seen.get(fp) or 0):
                row["unknown"] += 1      # no later review OF THIS LENS yet
            elif (last_seen.get(fp) or 0) < newest:
                row["stopped_recurring"] += 1
            else:
                row["persisted"] += 1
    for lid, count in fired.items():
        lenses.setdefault(lid, {
            "lens": lid, "fired": 0, "findings": 0, "blockers": 0,
            "acted": 0, "dismissed": 0,
            "stopped_recurring": 0, "persisted": 0, "unknown": 0})
        lenses[lid]["fired"] = count

    escape = {k: 0 for k in BUCKETS}
    by_step = {}
    for r in findings:
        step = str(r.get("caught_at") or "")
        by_step[step] = by_step.get(step, 0) + 1
        escape[_bucket(step)] += 1
    # Counted-but-unidentified blockers (the evaluate verdict). They belong
    # in the escape picture — that is the metric that asks WHERE things are
    # caught — but they can never be dispositioned, so they stay out of the
    # acted/dismissed columns entirely.
    counted = 0
    for r in records:
        if r.get("kind") != "counts":
            continue
        n = int(r.get("blockers") or 0)
        step = str(r.get("caught_at") or "")
        counted += n
        by_step[step] = by_step.get(step, 0) + n
        escape[_bucket(step)] += n
        row = lenses.setdefault(str(r.get("lens") or "(unattributed)"), {
            "lens": str(r.get("lens") or "(unattributed)"), "fired": 0,
            "findings": 0, "blockers": 0, "acted": 0, "dismissed": 0,
            "stopped_recurring": 0, "persisted": 0, "unknown": 0})
        row["blockers"] += n

    # Blockers with no human verdict yet — the marking worklist. Explicit
    # disposition is asked for HERE and nowhere else: these are the findings
    # a human already reads at the gate.
    open_blockers = []
    for r in findings:
        fp = r.get("fp")
        if r.get("blocks") and fp and fp not in dispositions:
            if fp not in {b["fp"] for b in open_blockers}:
                open_blockers.append({"fp": fp, "lens": r.get("lens") or "",
                                      "label": r.get("label") or "",
                                      "caught_at": r.get("caught_at") or ""})
    return {
        "reviews": len(reviews),
        "open_blockers": open_blockers,
        "findings": len(findings),
        "notes": len(notes),
        "lenses": sorted(lenses.values(),
                         key=lambda x: (-x["findings"], x["lens"])),
        "escape": escape,
        "caught_at": dict(sorted(by_step.items())),
        "dispositioned": len(dispositions),
        "counted_only": counted,
        "ledger": _safe_ledger_path(ws),
    }


def zero_yield(rep: dict, min_fires: int = 5) -> list:
    """Lenses that have fired enough to have had a chance, and returned
    nothing a human acted on. The deletion shortlist — a prompt for a
    decision, never a decision."""
    out = []
    for row in rep.get("lenses") or []:
        if row["fired"] >= min_fires and row["acted"] == 0:
            out.append(row["lens"])
    return sorted(out)


def render(rep: dict) -> str:
    """The human read-out. Spend on the left, return on the right."""
    lines = [f"yield over {rep['reviews']} review(s), "
             f"{rep['findings']} finding(s), "
             f"{rep['dispositioned']} dispositioned"]
    if not rep["findings"] and not rep["reviews"] \
            and not rep.get("counted_only"):
        lines.append("  (nothing recorded yet — the meter fills as reviews "
                     "run; it never blocks anything)")
        return "\n".join(lines)
    lines.append("")
    lines.append(f"  {'lens':<22}{'fired':>6}{'found':>6}{'block':>6}"
                 f"{'acted':>6}{'dism':>6}  {'stopped/persist/unknown':>24}")
    for row in rep["lenses"]:
        lines.append(
            f"  {row['lens'][:22]:<22}{row['fired']:>6}{row['findings']:>6}"
            f"{row['blockers']:>6}{row['acted']:>6}{row['dismissed']:>6}"
            f"  {row['stopped_recurring']:>8}/{row['persisted']}"
            f"/{row['unknown']}")
    e = rep["escape"]
    lines.append("")
    if rep.get("counted_only"):
        lines.append(f"  ({rep['counted_only']} blocker(s) known only as a "
                     "count, from evaluate verdicts — they shape the escape "
                     "picture but can never be dispositioned)")
    lines.append(f"  caught in task {e['in_task']}  ·  "
                 f"at review {e['at_review']}  ·  "
                 f"after sign-off {e['after_signoff']}")
    lines.append("  (acted/dism are HUMAN verdicts; stopped/persist are weak "
                 "inference and never counted as acted)")
    pending = rep.get("open_blockers") or []
    if pending:
        lines.append("")
        lines.append(f"  {len(pending)} blocker(s) awaiting your verdict "
                     "— `tp yield mark <fp> acted|dismissed`:")
        for b in pending[:12]:
            lines.append(f"    {b['fp']}  {b['lens'][:14]:<14} "
                         f"{b['label'][:52]}")
        if len(pending) > 12:
            lines.append(f"    … and {len(pending) - 12} more")
    shortlist = zero_yield(rep)
    if shortlist:
        lines.append("")
        lines.append("  fired >=5 times, never produced a finding a human "
                     "acted on: " + ", ".join(shortlist))
        lines.append("  (a question to answer, not a verdict — check whether "
                     "they were dispositioned at all)")
    return "\n".join(lines)
