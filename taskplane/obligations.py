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

  It CAN NOW see      whether the render TOOL WAS CALLED, and with what
                      bytes. This line used to read "it CANNOT see" on the
                      grounds that `mcp__visualize__show_widget` happens in
                      the host, outside every process taskplane runs. That
                      was wrong. A PreToolUse matcher is a regex over TOOL
                      NAMES, and `mcp__<server>__<tool>` names match it like
                      any other — the same seam that already screens writes
                      and Task dispatches. So the render is observable at the
                      hook, and `observe()` below records it as a FACT.

AN ACKNOWLEDGEMENT IS STILL A CLAIM — IT IS NOW A CORROBORATED ONE. An ack
says "I showed it". An observation says "the tool ran, with these bytes".
Neither proves a human read the screen, and this module does not pretend
otherwise. But the three failures it was built for are now separable from
the record alone, with nobody watching:

  SKIPPED       issued, no ack, no observation.
  CLAIMED ONLY  acked, never observed. The ack is unsupported.
  SUBSTITUTED   observed with a fingerprint that is not the artifact the
                engine built — a hand-drawn chart, or the engine's HTML
                edited on the way through. This is the render contract
                ("byte-for-byte, no restyling") made checkable rather than
                merely asserted.

What remains genuinely unobservable is ATTENTION. That is why an ack is kept
and never renamed to `verified`.

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
import re
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

# ---- BINDING obligations: an obligation converted into a prohibition -----
#
# A hook can DENY an action. It cannot COMPEL one. That asymmetry is why
# every prohibition in this product is enforced at 100% — the screener
# refuses an out-of-scope write, refuses `rm -rf .`, refuses an interpreter
# escape — while every OBLIGATION ("render the board", "show the graph")
# sits at 0% and is left to the assistant's diligence. Five structural
# attempts to fix that by instruction failed, because an instruction is not
# a mechanism.
#
# The conversion is the mechanism. Not "you must show the graph" but "you
# may not reach the CONCLUSION until the graph has been shown". A conclusion
# is a command, a command is a tool call, and a tool call can be denied. So
# the obligation borrows the enforcement the screener already has.
#
# Three properties keep this from becoming a trap:
#
#   OPT-IN        `binding` is False by default. An ordinary obligation is
#                 still a pure instrument that gates nothing, so everything
#                 written above about deletability still holds.
#   NARROW        it blocks only taskplane's OWN completion commands. It can
#                 never block editing a file, running a test, or any command
#                 that is not the engine declaring the work finished.
#   ESCAPABLE     TASKPLANE_OBLIGATIONS=off disables blocking entirely while
#                 still recording. A governance mechanism with no documented
#                 way out is one people route around by uninstalling.
COMPLETION_PATTERNS = (
    r"\bdod\b",                # the Definition-of-Done exit gate
    r"\bloop\s+submit\b",
    r"\bloop\s+approve\b",
    r"\bloop\s+retro\b",
    r"\bloop\s+gate\b",
)


def blocking_enabled() -> bool:
    """Blocking is on unless explicitly disabled. Recording always happens."""
    return (os.environ.get("TASKPLANE_OBLIGATIONS") or "").strip().lower() \
        not in ("off", "0", "false", "advisory")


def blocking(ws: str) -> list:
    """Open obligations that were issued as BINDING, oldest first.

    Reads `status()`, so an obligation discharged by an acknowledgement OR
    corroborated by an observed render is no longer blocking — the honest
    path out is the same path that was asked for in the first place.
    """
    return [o for o in status(ws)["open"] if o.get("binding")]


def blocked_reason(ws: str, command: str) -> "str | None":
    """The deny message for `command`, or None if it may proceed.

    Only taskplane's own completion commands are ever blocked, and only
    when the workspace owes a binding artifact. The message names every
    open obligation and the exact command that discharges it, because a
    refusal that does not say how to proceed is just an obstacle.
    """
    if not command or not blocking_enabled():
        return None
    # Position matters, and one shared resolver decides it. Scanning every
    # token for a bare `tp` meant `git commit -m "tp dod"` read as a
    # completion and was refused. A program name is the FIRST word.
    verb = tp.taskplane_verb(command)
    if verb is None:
        return None            # not a taskplane command at all
    text = " ".join(str(command).split())
    if not any(re.search(p, text) for p in COMPLETION_PATTERNS):
        return None            # a taskplane command, but not a conclusion
    owed = blocking(ws)
    if not owed:
        return None
    lines = [f"  {o['id']}  {o.get('kind')}  {o.get('detail') or ''}".rstrip()
             + f"\n      show it, then: tp ack {o['id']}" for o in owed]
    return ("taskplane: this run owes {n} artifact{s} that {have} not been "
            "shown, so it cannot be declared finished yet.\n\n{list}\n\n"
            "These are the views the flow asked for — not extra work. "
            "Render each one, acknowledge it, then re-run this command. "
            "(Set TASKPLANE_OBLIGATIONS=off to disable this check.)").format(
        n=len(owed), s="" if len(owed) == 1 else "s",
        have="has" if len(owed) == 1 else "have", list="\n".join(lines))


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
          key: str | None = None, session: str | None = None,
          binding: bool = False) -> str | None:
    """Record that the engine demanded something. Returns the obligation id.

    Returns None only when the ledger could not be written, which callers
    ignore — issuing is never allowed to be load-bearing.
    """
    if kind not in KINDS:
        return None
    ts = time.time()
    # Resolve against the WORKSPACE, not the process cwd. A relative
    # artifact path hashed from wherever the CLI happened to be running
    # either found nothing or — worse — found a same-named file in another
    # checkout, and recorded ITS fingerprint as the engine's bytes. Every
    # later comparison then measured the wrong thing.
    fp = artifact_fingerprint(
        artifact if os.path.isabs(artifact) else os.path.join(ws, artifact)
    ) if artifact else None
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
        **({"binding": True} if binding else {}),
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


def content_fingerprint(text: str) -> str:
    """Fingerprint of a STRING, comparable with artifact_fingerprint().

    The engine writes an artifact as UTF-8 text and hashes its bytes; the
    host hands the hook the same content as a str. Hashing the UTF-8
    encoding is what makes the two comparable, and that comparability is
    the entire point — it is how a substitute is caught.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def observe(ws: str, *, tool: str, fingerprint: str | None,
            title: str = "", bytes_len: int = 0,
            session: str | None = None) -> dict:
    """Record that a render TOOL RAN. A fact, not a claim.

    Written from the PreToolUse hook, so it is recorded whether or not the
    assistant later acknowledges anything — which is the property that
    makes a skip visible with nobody watching. Best effort and never
    blocking: an instrument that could deny a render would be absurd, and
    one that can cost someone a gate stops being an instrument.
    """
    row = {"event": "observed", "observed": True, "tool": str(tool)[:120],
           "title": str(title)[:200], "bytes": int(bytes_len),
           "host": tp.host()}
    if fingerprint:
        row["fingerprint"] = str(fingerprint)[:64]
    if session:
        row["session"] = str(session)[:120]
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
    """Reconcile what was DEMANDED, what was CLAIMED, and what was OBSERVED.

    Three sources, deliberately not merged into one number:

      issued      the engine's demand
      acknowledged the assistant's claim
      observed    the render tool actually running, seen at the hook

    `mismatched` is the substitute detector on the CLAIM side: acknowledged
    while citing a fingerprint that is not the artifact the engine built.
    `substituted` is the same failure caught on the FACT side, and it is the
    stronger of the two because it does not depend on the assistant citing
    anything. `claimed_only` is an ack with no observation behind it — not
    proof of dishonesty (the host may not screen the render tool at all), but
    the one column that would show it if it were.
    """
    rows = read(ws)
    issued = {r["id"]: r for r in rows
              if r.get("event") == "issued" and r.get("id")}
    acks: dict = {}
    for r in rows:
        if r.get("event") == "acknowledged" and r.get("id"):
            acks.setdefault(r["id"], r)
    observed = [r for r in rows if r.get("event") == "observed"]
    seen_fp = {r.get("fingerprint") for r in observed if r.get("fingerprint")}
    issued_fp = {r.get("fingerprint") for r in issued.values()
                 if r.get("fingerprint")}
    open_, mismatched, met, corroborated, claimed_only = [], [], [], [], []
    for oid, row in issued.items():
        ack = acks.get(oid)
        want = row.get("fingerprint")
        if not want and row.get("artifact"):
            # The obligation NAMES an artifact but was issued before that
            # artifact had bytes (a seeded `--owes` obligation always is).
            # The engine's own file is still what "the engine's exact bytes"
            # means, so resolve it now rather than treating every such
            # obligation as uncheckable — which is what made a DELIVERED
            # artifact indistinguishable from a bare claim.
            art = row["artifact"]
            want = artifact_fingerprint(
                art if os.path.isabs(art) else os.path.join(ws, art))
        if ack is None:
            # An observation of the engine's exact bytes discharges the
            # obligation on its own: the artifact demonstrably reached the
            # render tool, which is a stronger record than an ack. Requiring
            # the ack as well would make the honest path the longer one.
            if want and want in seen_fp:
                met.append(row)
                corroborated.append(row)
            else:
                open_.append(row)
            continue
        got = ack.get("fingerprint")
        if want and got and want != got:
            mismatched.append({**row, "cited": got})
        else:
            met.append(row)
            if want and want in seen_fp:
                corroborated.append(row)
            elif observed:
                # The host IS reporting renders, and none of them was this
                # artifact. The claim stands alone.
                claimed_only.append(row)
    substituted = [r for r in observed
                   if r.get("fingerprint") and issued_fp
                   and r.get("fingerprint") not in issued_fp]
    return {
        "issued": len(issued),
        "acknowledged": len(met) + len(mismatched),
        "open": open_,
        "mismatched": mismatched,
        "met": met,
        "observed": len(observed),
        "corroborated": corroborated,
        "claimed_only": claimed_only,
        "substituted": substituted,
        "unparseable": sum(1 for r in rows if r.get("event") == "unparseable"),
    }
