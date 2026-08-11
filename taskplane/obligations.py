"""Obligations — what the engine DEMANDED, and whether anyone did it.

WHY THIS EXISTS. Every test in this repository asks whether the machinery is
CORRECT. None of them asks whether it was USED. That gap is not theoretical:
it is the direct cause of the most-repeated complaint this project has ever
received — "no inline dashboard visualisation, no report, nothing", "this is
not the graph and dependency visualisation we designed", "again ignored graph
design". In every one of those cases the engine rendered the artifact, wrote
it to disk, put a pointer to it in the transition payload, and told the
assistant to show it. The engine was green. The product was broken. Nothing
anywhere recorded the difference, because the engine issues its demands as
PROSE inside a payload and then has no idea what happened next.

So the engine now writes down what it asked for. An obligation that is issued
and never acknowledged is a fact, in a ledger, that a scorer can count.

THE ABSENCE IS THE MEASUREMENT. That is the whole idea, and it drives every
decision below: issuance must be unconditional and cheap, and a missing
acknowledgement must never block, warn, retry, or nag. The moment an
unacknowledged obligation costs someone a gate, it stops being an instrument
and starts being a gate — and people route around gates.

WHAT NEEDS AN OBLIGATION, AND WHAT DOES NOT. This is the line that keeps the
ledger honest, and it is drawn on ONE question: can the engine see it?

  It CAN see          how many lenses it routed deep, how many subagents
                      started, which loop steps ran in which order, whether
                      an approval carried human attribution. Those are FACTS
                      and they stay facts — the scorer reads them from the
                      trace and no acknowledgement is involved. Asking the
                      assistant to attest to something the engine already
                      knows would be inviting it to disagree with the record.

  It CANNOT see       whether a rendered artifact was actually put in front
                      of a human. `mcp__visualize__show_widget` happens in
                      the host, outside every process taskplane runs. Only
                      these need a claim.

AN ACKNOWLEDGEMENT IS A CLAIM, NOT PROOF. An assistant could acknowledge
without rendering. That is stated plainly here and carried in the data:
acknowledgements are recorded as `claimed`, never as `verified`, and the
scorer reports them apart from engine-observed facts. The failure mode this
instrument was built for is SKIPPING, not lying, and a skip is exactly what
an unacknowledged obligation records. If lying ever becomes the problem, the
fix is host-transcript scoring, and this ledger will be the thing that shows
it is needed.

THE FINGERPRINT IS WHAT STOPS A SUBSTITUTE. A render obligation carries the
content fingerprint of the artifact the ENGINE produced. Discharging it means
citing that fingerprint. An assistant that draws its own chart instead — the
literal complaint behind "this is not the graph we designed" — has nothing to
cite, so the obligation stays open and the scorer counts it. This is the one
place the design does better than a naive "did you render? yes" checkbox.

WHAT THIS MODULE MAY NOT DO. It gates nothing. It cannot fail CI, block a
loop, refuse a tool, or change a verdict. Every write is best-effort and
swallowed. Delete this file and taskplane behaves exactly as before — the
same contract yield_meter.py holds, and the property that makes an instrument
safe to add and easy to remove if it does not earn its keep.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

import taskplane_lite as tp

LEDGER = "obligations.jsonl"

# The kinds that need a CLAIM, because the engine cannot observe them.
# Deliberately short. Every addition should have to answer "why can the
# engine not just see this?" — and most of the time it can.
RENDER_KINDS = frozenset({"render_dashboard", "render_graph"})

# There is no second family. Agent fan-out looked like it needed an
# obligation until the engine turned out to already record expected-vs-
# observed dispatch (`tp.record_expected_dispatch` / `tp.dispatch_report`,
# enforced at the PreToolUse Task hook), and loop-step order and approval
# attribution are already trace events. Adding obligations for any of them
# would have created a SECOND record of the same thing, free to disagree
# with the first — the drift shape this codebase already carries elsewhere.
# The scorer reads those from the engine and asks nobody to attest to them.
KINDS = RENDER_KINDS


def ledger_path(ws: str) -> str:
    """In the project STORE, beside the yield ledger.

    Not `.taskplane/`: runtime state is per-checkout, git-ignored and
    rotated, and compliance is only interesting as a TREND across sessions.
    """
    return os.path.join(tp.store_root(ws), LEDGER)


def _append(ws: str, record: dict) -> None:
    """Best effort, always. An instrument must never cost anyone a gate."""
    try:
        path = ledger_path(ws)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        record.setdefault("ts", time.time())
        with tp.file_lock(path):
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str, sort_keys=True) + "\n")
    except Exception:
        pass


def artifact_fingerprint(path: str) -> str | None:
    """Content hash of the artifact the engine produced, or None.

    Content, not mtime or size: the point is that the thing shown IS the
    thing built, and a fingerprint is the only claim a substitute cannot
    make.
    """
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return None


def _oid(kind: str, step: str, salt: str) -> str:
    """Deterministic in (kind, step, artifact-content). NOT time-based.

    Two reasons, and the second is the one that matters. A timestamped id
    leaks into artifacts this project compares BYTE FOR BYTE between Claude
    and Codex — the frozen dispatch and stage briefs — and a per-run value in
    a parity golden makes the golden unreproducible, which is how a
    cross-host contract quietly stops being checkable.

    The first reason is that it is simply truer. The board at a given step is
    ONE thing to show, not a fresh demand on every re-render; re-issuing is
    idempotent and the denominator stays honest instead of inflating each
    time a driver refreshes the view.

    The salt is the artifact's LOGICAL key, never its content. Content was
    the obvious choice and it was wrong: `dashboard.html` embeds elapsed
    times, so hashing it produced a different id on every render — volatile
    in precisely the goldens this determinism exists to protect. The
    fingerprint still travels in the row, where it does its real job of
    catching a substitute; it just does not decide identity.
    """
    raw = "\x1f".join((kind, step, salt)).encode("utf-8")
    return "o-" + hashlib.sha256(raw).hexdigest()[:10]


def issue(ws: str, kind: str, *, detail: str, step: str = "",
          expect: int | None = None, artifact: str | None = None,
          key: str | None = None, session: str | None = None) -> str | None:
    """Record that the engine demanded something. Returns the obligation id.

    Returns None only when the ledger could not be written, which callers
    ignore — issuing is never allowed to be load-bearing.
    """
    if kind not in KINDS:
        return None
    ts = time.time()
    fp = artifact_fingerprint(artifact) if artifact else None
    # `key` is the artifact's stable, LOGICAL name (".taskplane/dashboard.html"),
    # supplied by the caller because only the caller knows which part of an
    # absolute path is meaningful. Falling back to the basename keeps a
    # temp-dir path from making the id machine-specific.
    salt = key or (os.path.basename(artifact) if artifact else detail)
    oid = _oid(kind, step, salt)
    _append(ws, {
        "event": "issued", "id": oid, "kind": kind, "detail": detail,
        "step": step, "ts": ts,
        # `host` is what makes cross-host parity answerable at all: the same
        # scenario run on Claude and on Codex produces two ledgers, and the
        # only honest comparison is obligation-for-obligation.
        "host": tp.host(),
        **({"expect": int(expect)} if expect is not None else {}),
        **({"artifact": tp.to_posix(artifact)} if artifact else {}),
        **({"fingerprint": fp} if fp else {}),
        **({"session": session} if session else {}),
    })
    return oid


def acknowledge(ws: str, oid: str, *, evidence: str = "",
                fingerprint: str | None = None) -> dict:
    """Record a CLAIM that an obligation was discharged.

    Never called `verified`. The scorer keeps claims and engine-observed
    facts in separate columns for exactly this reason.
    """
    row = {"event": "acknowledged", "id": str(oid), "claimed": True,
           "evidence": str(evidence)[:400], "host": tp.host()}
    if fingerprint:
        row["fingerprint"] = str(fingerprint)[:64]
    _append(ws, row)
    return row


def read(ws: str) -> list:
    """Every ledger row, oldest first. Unparseable lines are SKIPPED and
    COUNTED by the caller, never silently dropped into a smaller number."""
    out: list = []
    try:
        with open(ledger_path(ws), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    out.append({"event": "unparseable"})
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        return []
    return out


def status(ws: str) -> dict:
    """{issued, acknowledged, open: [...], mismatched: [...]} by kind.

    `mismatched` is the substitute detector: acknowledged, but citing a
    fingerprint that is not the artifact the engine built. Reported apart
    from `open` because they are different failures — one skipped the
    artifact, the other showed something else.
    """
    rows = read(ws)
    issued = {r["id"]: r for r in rows
              if r.get("event") == "issued" and r.get("id")}
    acks: dict = {}
    for r in rows:
        if r.get("event") == "acknowledged" and r.get("id"):
            acks.setdefault(r["id"], r)
    open_, mismatched, met = [], [], []
    for oid, row in issued.items():
        ack = acks.get(oid)
        if ack is None:
            open_.append(row)
            continue
        want = row.get("fingerprint")
        got = ack.get("fingerprint")
        if want and got and want != got:
            mismatched.append({**row, "cited": got})
        else:
            met.append(row)
    return {
        "issued": len(issued),
        "acknowledged": len(met) + len(mismatched),
        "open": open_,
        "mismatched": mismatched,
        "met": met,
        "unparseable": sum(1 for r in rows if r.get("event") == "unparseable"),
    }
