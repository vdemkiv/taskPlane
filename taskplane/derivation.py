"""The derivation ledger — what the run actually invoked, and what it
derived more than once.

    "the bottleneck right now is a model which is always trying to skip
     steps or just reinvent a wheel"                        — the complaint

Two rubric items need a fact the harness never recorded. R7a asks whether a
run RE-DERIVED expensive work (the diff, the blast radius, the graph scan)
instead of deriving it once and sharing it; R10 asks whether it INVENTED a
CLI surface. Neither was answerable, because an ALLOWED command left no
trace of what ran — only refusals reached `trace.jsonl` (`hook_deny`). The
approve path metered a number and printed a decision, and that was all.

So `.taskplane/derivations.jsonl` holds two row kinds:

    {"event": "command", "verb": ..., "decision": ..., "ts": ..., "host": ...}
    {"event": "derived", "key":  ..., "input_key": ..., "ts": ..., "host": ...}

THE ROW CARRIES THE VERB, NEVER THE COMMAND TEXT OR ITS ARGUMENTS. Command
text is unbounded — paths, prose, ids, secrets — and the previous attempt at
this design leaked one: with a depth-2 walk that accepted any lowercase
identifier as a subcommand, `tp ack o-a590b2f59e` recorded the OBLIGATION ID,
because an obligation id has verb shape. The fix is not a better shape
filter (nothing about `retro` or `status` looks like an argument — they are
real verbs elsewhere in this very CLI); it is that a token which is not a
KNOWN subcommand of the verb in hand can never be absorbed. `ack` takes no
subcommand, so the walk stops at `ack`, whatever follows it.

THE RULE THAT OUTRANKS EVERYTHING: RECORDING ONLY, NO DENIAL. This is read
from inside the enforcement hook, so if it can change one screen decision it
is a defect, not a feature. Every entry point swallows everything, the
writer follows `taskplane_lite.trace` (unlocked O_APPEND of one small line,
immediate EACCES) and NOT `obligations._append` (which takes a 10s
`file_lock` — on a blocked runtime dir that stalls the hook for the full
timeout, and a hook that stalls has changed behaviour). tp.py's approve path
emits its payload BEFORE calling in here, and guards the call as well.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shlex
import time
import uuid

import taskplane_lite as tp

LEDGER_NAME = "derivations.jsonl"

# What a derivation row can be ABOUT. Four expensive things a review or a
# build derives, each of which the harness already produces once and can
# hand around.
KEYS = ("diff", "impact", "graph_scan", "findings")

# The marker for a subcommand that is not in the table. R10 must be able to
# SEE that an invented surface was invoked, but printing the invented token
# would print an argument the moment the model merely mistypes an id. The
# fact is recorded; the text is not.
UNKNOWN = "?"


# ------------------------------------------------------------------ verbs
#
# THE COMMAND TREE, AS DATA. Every top-level taskplane subcommand mapped to
# the subcommands it really has (frozenset() = it takes none). A token is
# absorbed into the verb only if this table already knows it, which is what
# makes an argument structurally unable to reach a row.
#
# taskplane/tests/test_derivation_ledger.py checks this table against the
# LIVE argparse tree (`tp help --md`): the instrument for "did it invent a
# CLI surface" must not contain an invented one.
TP_COMMANDS: dict = {
    "ack": frozenset(),
    "budget": frozenset(),
    "clear": frozenset(),
    "context": frozenset(),
    "contracts": frozenset(),
    "dashboard": frozenset(),
    "decision": frozenset({"accept", "list", "new", "show", "supersede"}),
    "dod": frozenset(),
    "findings": frozenset(),
    "gc": frozenset(),
    "graph": frozenset({"contract", "edge", "html", "impact", "link",
                        "scan"}),
    "help": frozenset(),
    "init": frozenset(),
    "kb": frozenset({"lint", "list", "migrate", "record", "retrieve",
                     "where"}),
    "lens": frozenset({"dispatch", "list", "route", "show"}),
    "loop": frozenset({"approve", "claim", "evidence", "gate", "init",
                       "next", "resolve", "retro", "select", "status",
                       "submit", "verify-dispatch", "wave"}),
    "new": frozenset(),
    "north-star": frozenset(),
    "onboard": frozenset(),
    "ready": frozenset(),
    "req": frozenset({"debt", "list", "mode", "new", "score"}),
    "review": frozenset({"collect", "start"}),
    "screen": frozenset(),
    "screen-dispatch": frozenset(),
    "screen-render": frozenset(),
    "session-verify": frozenset(),
    "share": frozenset({"plan", "push", "set", "status"}),
    "status": frozenset(),
    "subagent-start": frozenset(),
    "subagent-stop": frozenset(),
    "summary": frozenset(),
    "target": frozenset({"fetch", "pin", "show", "tools"}),
    "track": frozenset({"close", "list", "new", "switch"}),
    "version": frozenset(),
    "yield": frozenset({"mark"}),
}

# git is the other program whose subcommand is worth knowing: a run that
# re-derives the diff by hand does it here.
GIT_COMMANDS = frozenset({
    "add", "apply", "blame", "branch", "checkout", "cherry-pick", "clone",
    "commit", "diff", "fetch", "grep", "log", "ls-files", "merge",
    "merge-base", "push", "rebase", "remote", "reset", "rev-parse",
    "rev-list", "show", "stash", "status", "switch", "tag", "worktree",
})

# Leading tokens that are not the program: interpreters, wrappers, and the
# `VAR=value` prefix. Deliberately the same list taskplane_lite uses when it
# answers "is this a taskplane command", so the two cannot drift.
_LAUNCHERS = frozenset(tp._TP_LAUNCHERS) | {"sh", "bash", "zsh", "xargs",
                                            "sudo", "timeout"}

# A program name we are willing to write down verbatim. Bounded, no path
# separators, no `=`, and it must not have the shape of an argument.
_PROGRAM_MAX = 32

# Shell operators that end one command and begin another. An agent writes
# `cd repo && tp graph impact` constantly; reading only the first word of
# the whole line would record `cd` and lose the derivation entirely.
_SEPARATORS = frozenset({"&&", "||", ";", "|", "&", "|&"})

# Leading segments that are not "what ran" — they position the next one. A
# NON-navigation first segment is never skipped: `rm -rf x && tp status`
# must record `rm`, not hide behind the taskplane call after it.
_NAVIGATION = frozenset({"cd", "pushd", "popd", "export", "set", "source",
                         ".", "true", "clear"})

# git's global flags that swallow the NEXT token. Without this
# `git -C /repo diff` reads its own repo path as the subcommand and the
# diff it derived goes unrecorded.
_GIT_VALUE_FLAGS = frozenset({"-C", "-c", "--git-dir", "--work-tree",
                              "--namespace", "--exec-path"})


def _tokens(command) -> list:
    text = " ".join(str(command or "").split())
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _program_index(toks) -> int:
    """Index of the program token, skipping `VAR=value` prefixes, launchers
    and their flags. Same positional rule as taskplane_lite.taskplane_verb —
    a program name is the FIRST word, not any word."""
    i = 0
    while i < len(toks):
        t = toks[i]
        if "=" in t and not t.startswith("-") and "/" not in t.split("=")[0]:
            i += 1
            continue
        base = posixpath.basename(t.replace("\\", "/"))
        if t.startswith("-") or base in _LAUNCHERS:
            i += 1
            continue
        return i
    return -1


def _next_operand(toks, start) -> "tuple[str | None, int]":
    """The next token that is not a flag, and the index after it."""
    i = start
    while i < len(toks):
        if toks[i].startswith("-"):
            i += 1
            continue
        return toks[i], i + 1
    return None, i


def _walk(prefix: str, table, toks, start) -> str:
    """Absorb a subcommand while — and only while — the table knows it.

    This is the whole anti-leak mechanism. A token that is not a KNOWN
    subcommand of the verb in hand cannot be absorbed under any shape:
    `ack` maps to an EMPTY set of subcommands, so `tp ack o-a590b2f59e`,
    `tp ack retro` and `tp ack status` all stop at `tp ack` — the id, the
    real-verb-elsewhere and the real-top-level-verb alike."""
    parts = [prefix]
    tok, nxt = _next_operand(toks, start)
    if tok is None:
        return prefix                   # `tp` / `git` on its own
    if not isinstance(table, dict):     # flat table (git): depth 1
        parts.append(tok if tok in table else UNKNOWN)
        return " ".join(parts)
    if tok not in table:
        return f"{prefix} {UNKNOWN}"    # an invented surface, unquoted
    parts.append(tok)
    children = table[tok]
    if children:
        sub, _ = _next_operand(toks, nxt)
        if sub is not None:
            parts.append(sub if sub in children else UNKNOWN)
    return " ".join(parts)


def _looks_like_an_argument(token: str) -> bool:
    """Shapes that are never a program name. A LAST line of defence only —
    the tables above are what actually keep arguments out, because the
    shapes that matter (`retro`, `status`, `pass`) are indistinguishable
    from verbs and no filter here could reject them."""
    t = token or ""
    return (not t or t.startswith("-") or len(t) > _PROGRAM_MAX
            or "=" in t or "/" in t or "\\" in t
            or not t[0].isalnum()
            or any(c.isspace() or c in "\"'`$;|&<>*?()[]{}" for c in t))


def _segments(toks) -> list:
    """One shell line split into the commands it actually runs."""
    out, cur = [], []
    for t in toks:
        if t in _SEPARATORS:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def _skip_value_flags(toks, i, flags) -> int:
    while i < len(toks) and toks[i] in flags:
        i += 2
    return i


def _segment_verb(toks) -> str:
    i = _program_index(toks)
    if i < 0:
        return ""
    # One resolver decides whether this is taskplane, and it is the same one
    # the screener's release/completion checks use (a program name is the
    # FIRST word, not any word) — so the ledger cannot disagree with the
    # screener about what a command is.
    if tp.taskplane_verb(" ".join(shlex.quote(t) for t in toks[i:])) is not None:
        return _walk("tp", TP_COMMANDS, toks, i + 1)
    base = posixpath.basename(toks[i].replace("\\", "/"))
    if base.endswith(".py"):
        base = base[:-3]
    if base == "git":
        return _walk("git", GIT_COMMANDS, toks,
                     _skip_value_flags(toks, i + 1, _GIT_VALUE_FLAGS))
    return UNKNOWN if _looks_like_an_argument(base) else base


def verb(command) -> str:
    """WHAT RAN — never how it was called.

    Returns e.g. `tp graph impact`, `tp ack`, `git diff`, `pytest`, or
    `tp ?` for a taskplane subcommand that does not exist. Returns "" when
    there is no program to name (an empty command, a Write/Edit event).

    A chained line names the first segment that DOES something: `cd repo &&
    tp graph impact` is a derivation, not a `cd`. Only navigation prefixes
    are skipped, so nothing can hide a real command behind a later one.
    """
    first = ""
    for seg in _segments(_tokens(command)):
        v = _segment_verb(seg)
        if v and v not in _NAVIGATION:
            return v
        first = first or v
    return first


# --------------------------------------------------------- classification
#
# THE CLASSIFICATION TABLE, AS DATA. Verb -> every derivation that ONE
# invocation of it performs.
#
# `tp review start` maps to TWO keys and that is the whole point of the
# tuple: the command exists precisely because it derives several expensive
# things in one call (it pins the target — the diff — and computes the blast
# radius — the impact). With one key per command the reference scenario is
# unsatisfiable: the run would look as though it never derived the impact,
# and its own later `graph impact` would score as a first use instead of as
# the repeat it is.
DERIVED_BY_VERB: dict = {
    "tp review start": ("impact", "diff"),
    "tp graph impact": ("impact",),
    "tp graph scan": ("graph_scan",),
    "tp findings": ("findings",),
    "git diff": ("diff",),
}


def classify(command) -> tuple:
    """Every derivation `command` performs, as a TUPLE (possibly empty)."""
    return DERIVED_BY_VERB.get(verb(command), ())


def _target(ws) -> dict:
    try:
        import target as tgt
        return tgt.load(ws) or {}
    except Exception:
        return {}


def input_key(ws, key: str) -> str:
    """WHICH work a derivation is about, so two calls that derive the same
    thing collide — that collision IS the R7a signal.

    Heads come from the pinned `target.json` when there is one, and a base
    may legitimately be empty (`tp target pin` with no --base), which keys
    as `..<head>` rather than degrading into something that would collide
    across heads."""
    rec = _target(ws)
    head = rec.get("head") or tp.git_head(ws) or ""
    if key in ("diff", "findings"):
        return f"{rec.get('base') or ''}..{head}"
    files = sorted(str(f) for f in (rec.get("changed_files") or []))
    digest = hashlib.sha256("\n".join(files).encode("utf-8")).hexdigest()
    return f"{head}|{digest[:16]}"


# ---------------------------------------------------------------- the ledger

def ledger_path(ws) -> str:
    return os.path.join(tp.tp_dir(ws), LEDGER_NAME)


def _append(ws, rows) -> bool:
    """One unlocked O_APPEND of one small line per row — the shape
    `taskplane_lite.trace` uses, NOT `obligations._append`.

    The difference is load-bearing: `_append` takes a 10s `file_lock`, so a
    runtime dir that cannot be written stalls the caller for the full
    timeout. The caller here is the PreToolUse hook, and a hook that waits
    ten seconds has changed the run's behaviour just as surely as a block
    would. This fails immediately (EACCES/EISDIR) and reports it as a
    return value, never as an exception."""
    if not rows:
        return False
    d = tp.tp_dir(ws)
    try:
        os.makedirs(d, exist_ok=True)
        tp._ensure_self_ignored(d)
        body = "".join(json.dumps(r, default=str, sort_keys=True) + "\n"
                       for r in rows)
        with open(ledger_path(ws), "a", encoding="utf-8") as f:
            f.write(body)
        return True
    except Exception:
        # An instrument must never cost anyone an action. There is no
        # stderr warning here either: this runs inside the hook, and the
        # hook's stderr is the agent's transcript.
        return False


def _row(**data) -> dict:
    return {"ts": time.time(), "host": tp.host(), **data}


def record(ws, command, decision, **extra) -> list:
    """Write what ran, and what it derived. Returns the rows written (empty
    on any failure). NEVER raises: every call site is inside the screener."""
    try:
        rows = []
        v = verb(command)
        if v:
            rows.append(_row(event="command", verb=v, decision=str(decision),
                             **extra))
        for key in classify(command):
            rows.append(_row(event="derived", key=key,
                             input_key=input_key(ws, key), **extra))
        return rows if _append(ws, rows) else []
    except Exception:
        return []


def read(ws) -> list:
    """Every well-formed row, oldest first. A torn line is skipped, not
    raised — a half-written last line must not blind the whole rubric."""
    out = []
    try:
        with open(ledger_path(ws), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except Exception:
        return out
    return out


# ------------------------------------------------------------- the pre-flight

# The probe's command. It classifies through the SAME table as everything
# else — a pre-flight that exercises a private path proves the private path.
PROBE_COMMAND = "tp graph impact"


def probe(ws) -> "str | None":
    """Pre-flight: write a row, READ IT BACK, and return its id.

    Return the id only if it is actually on disk. A healthy-looking id over
    an empty ledger is the exact failure this exists to catch: the recorder
    would then measure a run whose instrument never wrote anything and
    report zero repeats as compliance.

    Probe rows are marked `probe: true` and excluded from `repeats()`. They
    have to be: the probe issues a `graph impact` against the fixture and
    the model's own `graph impact` at the same head carries the SAME (key,
    input_key), so a fully compliant run would score exactly one repeat and
    fail the very item the probe protects.
    """
    try:
        pid = "p-" + uuid.uuid4().hex[:12]
        rows = [_row(event="derived", key=key, input_key=input_key(ws, key),
                     probe=True, id=pid) for key in classify(PROBE_COMMAND)]
        if not rows or not _append(ws, rows):
            return None
        for rec in read(ws):
            if rec.get("id") == pid:
                return pid
        return None
    except Exception:
        return None


def repeats(ws=None, rows=None) -> int:
    """R7a: how many derivations were done AGAIN — rows minus distinct
    (key, input_key), probe rows excluded.

    Exposed so no consumer re-implements the arithmetic; the exclusion is
    the part that is easy to get wrong and impossible to notice, because
    getting it wrong scores a compliant run as a repeat.
    """
    try:
        rs = [r for r in (read(ws) if rows is None else rows)
              if isinstance(r, dict) and r.get("event") == "derived"
              and not r.get("probe")]
        return len(rs) - len({(r.get("key"), r.get("input_key")) for r in rs})
    except Exception:
        return 0


def metrics(ws=None, rows=None) -> dict:
    """Canonical structural counters from the append-only ledger.

    `derived_bytes` is optional at the hook boundary.  Absence never becomes
    an invented estimate: the byte counter remains numeric for structural
    accounting and `derivation_bytes_observed` says whether it was measured.
    """
    try:
        source = read(ws) if rows is None else rows
        source = [r for r in source if isinstance(r, dict)]
        commands = [r for r in source if r.get("event") == "command"]
        derived = [r for r in source if r.get("event") == "derived"
                   and not r.get("probe")]
        seen = set()
        repeated_rows = []
        for row in derived:
            key = (row.get("key"), row.get("input_key"))
            if key in seen:
                repeated_rows.append(row)
            else:
                seen.add(key)
        def safe_size(row, field):
            value = row.get(field)
            return value if isinstance(value, int) and not isinstance(value, bool) \
                and value >= 0 else 0
        return {
            "cli_count": sum(1 for r in commands
                             if str(r.get("verb") or "").startswith("tp ")),
            "command_count": len(commands),
            "emitted_bytes": sum(safe_size(r, "emitted_bytes")
                                 for r in commands),
            "derivation_count": len(derived),
            "repeated_derivation_count": len(repeated_rows),
            "repeated_derivation_bytes": sum(
                safe_size(r, "derived_bytes") for r in repeated_rows),
            "derivation_bytes_observed": all(
                isinstance(r.get("derived_bytes"), int) for r in derived),
        }
    except Exception:
        return {"cli_count": 0, "command_count": 0, "emitted_bytes": 0,
                "derivation_count": 0, "repeated_derivation_count": 0,
                "repeated_derivation_bytes": 0,
                "derivation_bytes_observed": False}
