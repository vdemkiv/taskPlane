"""The scenario manifest — what a governed skill's flow must SHOW, as data.

    "Skills agents and lenses are the most important part of this plugin"

`scripts/ci_evals.py` asks whether the machinery was USED. It answers from
records — a trace, an obligations ledger, a dispatch report — and it answers
the same six questions for every session, because those questions are about
the ENGINE. This module answers the other half: whether one named SKILL's
flow actually ran the way that skill mandates. That question is different per
skill, and the whole design problem is to answer it without per-skill code.

So a scenario is DATA. `evals/scenarios/<skill>.json` declares, for one
skill, a list of rubric rows. Each row carries

    claim    what a human would say the flow must show
    record   which record decides it (trace / obligations / dispatch /
             derivations / context)
    check    one of a fixed constraint vocabulary, evaluated generically

and nothing else. The scorer iterates `constraints(step)`, looks the rows up
in the named record, applies the named check, and never learns that
`tp-engineering` exists. Adding a skill is adding a JSON file.

WHY `inputs_fingerprint` IS A FLOW EXTRACT AND NOT A FILE HASH
--------------------------------------------------------------
A recorded run goes stale when the skill it graded changes, and the
fingerprint is what detects that. Hashing the skill's bytes would detect a
typo exactly as loudly as it detects the deletion of `$TP graph impact` —
and a staleness gate that fires on every typo is a gate people learn to
waive. That is the failure this layer exists to prevent, so it must not be
the failure this layer introduces.

`flow_extract()` therefore reduces a markdown source file to the flow it
MANDATES:

    require surface tp graph impact     a taskplane surface the file names
    forbid  flag    --all               a flag the file forbids
    term    DoD                         a gate term of art the file uses

Prose is discarded. Polarity is per SENTENCE, so "Do NOT pass `--all`" and
"Pass `--all`" are different extracts — the mutation a bag-of-surfaces
digest cannot see. Surfaces are resolved through `derivation.TP_COMMANDS`,
the SAME table the derivation ledger walks, so the fingerprint and the
ledger cannot disagree about what a taskplane command is. A surface the
table does not know resolves to `derivation.UNKNOWN` and is DROPPED: two
different invented surfaces would collapse into one item, and the deletion
of either would then be invisible.

The extract is emitted per FILE. A mandate that moves out of `SKILL.md` and
survives only in `agents/<skill>.md` has changed which document governs, and
that is a flow change worth a re-record.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not score. `evaluate` belongs to the rubric lane; this module owns
the schema, the loader, the guards and the fingerprint. The two meet at
`RECORDS`, `CHECKS`, `ANCHORS` and `constraints()`, which is why those are
tables rather than prose.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re

import derivation

SCHEMA = "taskplane.eval-scenario/v1"

# The extract's own version. It is INSIDE the hashed text: change the
# extraction rules and every fingerprint moves, which is correct — a
# fingerprint means nothing without the rule that produced it.
FLOW_EXTRACT_SCHEMA = "taskplane.eval-scenario/flow-extract/v1"

SCENARIO_DIRNAME = os.path.join("evals", "scenarios")

# The skills that run under an enforced contract.  Read-only skills are still
# evaluated, but against advisory scenarios whose governed controls are
# explicitly n/a.  Keeping the two sets separate prevents a help/status run
# from being graded as an incomplete delivery run while retaining the useful
# distinction for callers that only want contract-bearing skills.
GOVERNED_SKILLS = ("taskplane", "tp-build", "tp-design", "tp-engineering",
                   "tp-go", "tp-product", "tp-tag")
ADVISORY_SKILLS = ("tp-help", "tp-northstar", "tp-status")
EVALUATED_SKILLS = GOVERNED_SKILLS + ADVISORY_SKILLS
TERMINALS = ("completion", "human_gate", "review_complete", "response")

# --- the record vocabulary -------------------------------------------------
#
# WHICH RECORD DECIDES A ROW. Three of these are `ci_evals.RECORD_FILES`
# verbatim; two are the records Wave 1 added and the scorer needs.
#
#   trace        .taskplane/trace.jsonl — the engine's own event stream
#   obligations  .taskplane/obligations.jsonl — issued vs acknowledged
#   dispatch     dispatch.json — the briefs the engine composed, one row each
#   derivations  .taskplane/derivations.jsonl — what ran, what it derived
#   context      what the run PUT ON DISK for sharing: the target pin, the
#                shared review context files, the findings file, and each
#                lens's findings file. This is the only record that is not a
#                file the engine already writes; the recorder lane
#                synthesizes it (see RECORD_ROWS) because "the diff was
#                derived once and every lens read that one copy" is a fact
#                about artifacts, not about events.
RECORDS = ("trace", "obligations", "dispatch", "derivations", "context")

# The row shape each record is expected to present to the scorer. Written
# down here because two lanes consume it and a shape agreed in conversation
# is a shape that drifts.
RECORD_ROWS: dict = {
    "trace": "one row per trace.jsonl line; `event` names it; `ts` orders it",
    "obligations": "one row per obligations.jsonl line; `event` is "
                   "issued|acknowledged, `id` correlates them",
    "dispatch": "one row per composed brief: legacy {lens, context_path, ts} "
                "or ReviewKernel {kind: review-kernel-slot, slot_id, "
                "context_fingerprint, ts}",
    "derivations": "one row per derivations.jsonl line; `event` is "
                   "command|derived",
    "context": "one row per artifact the run wrote: "
               "{kind: target|context_file|findings|lens_findings|"
               "review_envelope|slot_result, path, head, base, fingerprint, "
               "sha256, lens, slot_id, ts}",
}

# Ordering is compared on this field. Every record the recorder synthesizes
# must carry it; where two rows share a `ts`, position within the record is
# the tie-break.
ORDER_KEY = "ts"

# What a `field_equals` comparand may be read from. `run` is the record
# `ci_evals.load_record` already loads — the run's identity (whose PR, which
# head) — and it is the only truth outside the session's own records that a
# rubric row is allowed to compare against.
COMPARANDS = RECORDS + ("run",)

# --- the constraint vocabulary ---------------------------------------------
#
# Seven checks, all evaluable without knowing the skill:
#
#   exists       >=1 row in `record` matches `select`
#   absent       0 rows match `select`
#   before       the FIRST matching row precedes the first anchor row
#   after        the FIRST matching row follows the first anchor row
#   count        the number of matching rows is within min/max
#   repeats      len(rows) - len(distinct(row[k] for k in distinct_by)) is
#                within max. This is `derivation.repeats()` stated as data,
#                and it is what R7a — "did it derive the same thing twice" —
#                reduces to.
#   field_equals every matching row's `field` equals a resolved comparand
#                (`value`, or `equals` naming another record's field)
#   pairs        for every matching row there is a correlated row in
#                `with.record` — "one findings file per dispatched brief"
#   all          conjunction of `of`, so one CLAIM can rest on two facts
#                without becoming two rubric rows
CHECKS = ("exists", "absent", "before", "after", "count", "repeats",
          "field_equals", "pairs", "all")

# Selector operators. A selector is `{field: value}` (equality) or
# `{field: {op: operand}}`. Bounded on purpose: an open operator set is an
# expression language, and an expression language in a manifest is per-skill
# code wearing a hat.
SELECT_OPS = ("in", "not_in", "contains", "present", "absent")

# --- the anchors -----------------------------------------------------------
#
# `before: "first_write"` is only evaluable because every anchor name
# resolves, in ONE table, to a record and a selector. An unresolvable anchor
# would be scored as satisfied-by-absence, which is the shape of every gate
# that quietly stopped gating.
ANCHORS: dict = {
    "first_write": {
        "record": "trace",
        "select": {"event": "workspace_write"},
    },
    "first_dispatch": {
        "record": "trace",
        "select": {"event": {"in": ["subagent_start",
                                    "review_dispatch_path"]}},
    },
    "first_dispatch_or_collection": {
        "record": "trace",
        "select": {"event": {"in": ["subagent_start",
                                    "review_dispatch_path",
                                    "review_kernel_collected"]}},
    },
    "first_brief": {
        "record": "trace",
        "select": {"event": {"in": ["subagent_start", "lens_route",
                                    "review_dispatch_path"]}},
    },
    "completion_claim": {
        "record": "trace",
        "select": {"event": {"in": ["loop_submit", "loop_gate",
                                    "loop_approve", "loop_retro"]}},
    },
}

# --- the universal rubric --------------------------------------------------
#
# Four control points every governed flow has, tagged rather than numbered so
# a skill's own step ids stay its own. A manifest must COVER all four: a
# skill that genuinely lacks one declares the step with `applicable: false`
# and a reason. Omission is the failure mode — a control point nobody checked
# reads exactly like a control point that passed.
UNIVERSAL = ("contract", "dor", "dod", "no_rederive")

# --- events ----------------------------------------------------------------
#
# What the engine actually emits today, from `tp.trace(ws, "...")` call
# sites. A scenario that selects on an event outside this set is selecting on
# nothing and would score `no evidence` forever.
ENGINE_EVENTS = (
    "design_contracts_recorded", "dod", "dor", "graph_impact",
    "graph_req_link", "graph_scan", "lens_route", "loop_approve",
    "loop_gate", "loop_retro", "loop_step", "loop_submit",
    "review_context_written", "review_dispatch_path", "review_kernel_collected",
    "review_kernel_started", "subagent_start",
)

# Events these scenarios need that the engine does NOT emit today. The
# recorder lane synthesizes them; each names its source so the coordination
# is written down rather than assumed. `contract_activated` in particular is
# the one the schema's own reference example selects on, and there is no
# `tp.trace(ws, "contract_activated")` anywhere in the engine.
SYNTHETIC_EVENTS: dict = {
    "evaluation_started":
        "the recorder's timestamp immediately after fixture/setup work",
    "human_gate_wait":
        "the frozen loop state's current HUMAN_STEPS entry; the model stopped "
        "without approving it",
    "contract_activated":
        "the active contract's own `activated_at` (taskplane_lite.activate "
        "records it; nothing traces it) — one row at that timestamp",
    "workspace_write":
        "the PostToolUse Write/Edit stream, or the earliest mtime under the "
        "contract's write-allow globs — the moment the run first changed the "
        "workspace",
}

# Fields a scenario selects on that the engine's rows do NOT carry today.
# Same discipline as SYNTHETIC_EVENTS and the same reason: an unrecorded
# field selects nothing, and a rubric row that selects nothing scores
# `no evidence` forever while reading like a shy session.
SYNTHETIC_FIELDS: dict = {
    "trace.lens_route.breadth":
        "the routing breadth `lens.py` already branches on (`breadth != "
        "'all'`); the trace row carries `step` and `lenses` but not the "
        "breadth, so `--all` is currently unobservable from any record",
    "context.*":
        "the whole `context` record — the recorder walks the target pin, "
        "`.em-review/context/`, `.em-review/findings.json` and each "
        "`.em-review/lens-*/findings.json`",
    "dispatch.context_path":
        "the context path each composed brief cites (review.context_note "
        "embeds it); dispatch.json records counts, not per-brief rows",
}

_TOP_KEYS = ("schema", "skill", "title", "source_files", "inputs_fingerprint",
             "expects_derivations", "declared_surfaces", "terminal", "steps")
_STEP_KEYS = ("id", "claim", "record", "check", "select", "event", "before",
              "after", "field", "value", "equals", "with", "key", "min",
              "max", "distinct_by", "of", "allow_empty_with", "required", "universal",
              "applicable", "reason")


# ====================================================================== flow
# The extract. Everything below this line is about reducing markdown to the
# flow it mandates, and nothing else.

# Sentence polarity. A tight marker list on purpose: "it is NOT optional" and
# "has nothing to render" are NOT prohibitions, and a looser matcher would
# read them as ones and invert two real mandates.
_NEGATION = re.compile(
    r"\b(do not|do NOT|don't|never|must not|should not|shouldn't|avoid)\b",
    re.IGNORECASE)

# Gate terms of art. Two entries, deliberately: these are tokens that do not
# occur in ordinary reworded prose, so their presence is a claim about the
# flow. Polarity does not apply — "never skip the DoD" and "run the DoD" both
# mean the file is about the DoD gate.
_TERMS = ("DoR", "DoD")

_FENCE = re.compile(r"```.*?```", re.S)
# Inline code CROSSES LINE BREAKS. Every skill file wraps at ~76 columns, so
# `$TP new --read-only --write-allow ".em-review/**" --owes review` is three
# source lines and one span. A newline-bounded matcher captured the fragment
# that happened to fit, which made the extract depend on where the paragraph
# was wrapped — i.e. it made a re-wrap look like a flow change, the very
# false alarm this design exists to avoid. Spans are whitespace-normalized
# for the same reason. A "span" containing a blank line is not one: that is
# an unpaired backtick, and pairing it would swallow whole paragraphs.
_INLINE = re.compile(r"`([^`]+?)`", re.S)
# A sentence may end behind its own markup: `**... DO NOT RETYPE THEM.**`
# ends a sentence, and a matcher that required the period to touch the
# whitespace merged that heading's prohibition into the NEXT sentence's
# mandates.
_SENTENCE = re.compile(r"(?<=[.!?])[*_)\"']*\s+")
_FLAG = re.compile(r"--[a-z][a-z0-9-]*")
_MARK = "\x00%d\x00"
_MARK_RE = re.compile(r"\x00(\d+)\x00")

# `$TP` is how every skill spells the CLI. Normalized to `tp` before the
# derivation walker sees it, because the walker resolves a PROGRAM and `$TP`
# is a shell variable.
_TP_ALIASES = ("${TP}", "$TP", "${TP:-tp}")


def _spans(text: str):
    """(masked prose, [code span, ...]). Fenced blocks contribute one span
    per non-empty line; inline backticks contribute one span each."""
    spans: list = []

    def take(body: str) -> str:
        idx = []
        for line in body.splitlines():
            line = " ".join(line.split())
            if line:
                spans.append(line)
                idx.append(len(spans) - 1)
        return "".join(_MARK % i for i in idx)

    def one(match) -> str:
        body = match.group(1)
        if "\n\n" in body:
            return match.group(0)     # an unpaired backtick, not a span
        spans.append(" ".join(body.split()))
        return _MARK % (len(spans) - 1)

    masked = _FENCE.sub(lambda m: take(m.group(0).strip("`")), text)
    masked = _INLINE.sub(one, masked)
    return masked, spans


def _surface(span: str) -> str:
    """The taskplane surface a code span names, or "".

    Two accepted spellings. `$TP graph impact --files x` carries the program
    and is handed to `derivation.verb` after normalization. `review start`
    does not — the skills write it that way constantly — so a BARE span is
    accepted only when its first token is a known top-level verb AND its
    second is a known subcommand of that verb. One-word spans are never
    absorbed: `status`, `new`, `context` and `clear` are all ordinary
    English and all real verbs, and no shape filter can tell those apart.
    """
    text = span.strip()
    for alias in _TP_ALIASES:
        text = text.replace(alias, "tp")
    toks = text.split()
    if not toks:
        return ""
    head = os.path.basename(toks[0].replace("\\", "/"))
    if head not in ("tp", "tp.py", "taskplane"):
        subs = derivation.TP_COMMANDS.get(toks[0])
        if not subs or len(toks) < 2 or toks[1] not in subs:
            return ""
        text = "tp " + text
    verb = derivation.verb(text)
    if not verb.startswith("tp ") or derivation.UNKNOWN in verb:
        return ""
    return verb


def flow_extract(text: str) -> tuple:
    """One markdown file reduced to the flow it mandates, sorted.

    Deterministic and set-valued: repeating a mandate, reordering paragraphs
    and re-wrapping lines all produce the same tuple, while deleting the last
    mention of a surface, a flag or a gate term does not.
    """
    masked, spans = _spans(text)
    items = set()
    for para in masked.split("\n\n"):
        for sentence in _SENTENCE.split(para):
            for m in _MARK_RE.finditer(sentence):
                # Polarity is decided by the prose BEFORE the span, not by
                # the sentence as a whole. "Do NOT pass `--all`" forbids;
                # "`tp dod` … is refused — doing the review is never blocked"
                # does not, and a sentence-wide matcher inverted every
                # mandate that happened to be followed by a reassurance.
                before = _MARK_RE.sub(" ", sentence[:m.start()])
                polarity = "forbid" if _NEGATION.search(before) else "require"
                span = spans[int(m.group(1))]
                surface = _surface(span)
                if surface:
                    items.add(f"{polarity} surface {surface}")
                for flag in _FLAG.findall(span):
                    items.add(f"{polarity} flag {flag}")
    for term in _TERMS:
        if re.search(r"\b%s\b" % re.escape(term), text):
            items.add(f"term {term}")
    return tuple(sorted(items))


def _extract_text(root: str, rel: str) -> str:
    """The canonical block for one source file. A file that is not there says
    so IN the text — a deleted skill file is the loudest flow change there
    is, and it must move the fingerprint rather than raise."""
    path = os.path.join(root, rel.replace("/", os.sep))
    try:
        with io.open(path, encoding="utf-8") as f:
            body = flow_extract(f.read())
    except OSError:
        return f"file {rel}\nmissing\n"
    return "file %s\n%s\n" % (rel, "\n".join(body))


def extract(root: str, source_files) -> str:
    """The full canonical text the fingerprint digests. Returned, not just
    hashed, so a STALE verdict can show WHAT moved instead of two hexes."""
    files = sorted(str(f) for f in (source_files or []))
    return (FLOW_EXTRACT_SCHEMA + "\n"
            + "".join(_extract_text(root, rel) for rel in files))


def fingerprint(root: str, source_files) -> str:
    return hashlib.sha256(
        extract(root, source_files).encode("utf-8")).hexdigest()


# =================================================================== loading

def scenario_dir(root: str) -> str:
    return os.path.join(root, SCENARIO_DIRNAME)


def discover(root: str) -> dict:
    """{skill: path} for every manifest on disk. Loose `.json` FILES, never
    directories — `evals/scenarios/` must stay invisible to
    `ci_evals._discover`, which only ever considers directories and would
    otherwise have to grow a marker."""
    out = {}
    d = scenario_dir(root)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for name in names:
        path = os.path.join(d, name)
        if name.endswith(".json") and os.path.isfile(path):
            out[name[:-5]] = path
    return out


def load(path: str) -> dict:
    with io.open(path, encoding="utf-8") as f:
        scenario = json.load(f)
    if not isinstance(scenario, dict):
        raise ValueError(f"{path}: a scenario is a JSON object")
    scenario["_path"] = path
    return scenario


def constraints(step: dict) -> tuple:
    """A step flattened into the constraints the scorer evaluates.

    `check: "all"` is the only nesting the vocabulary has, and it exists so a
    single human CLAIM can rest on two facts — "derived once AND every brief
    cited it" — without splitting into two rubric rows that a human would
    read as two separate requirements. Each nested constraint inherits the
    step's record unless it names its own.
    """
    if step.get("check") != "all":
        return (step,)
    out = []
    for sub in step.get("of") or ():
        merged = dict(sub)
        merged.setdefault("record", step.get("record"))
        out.append(merged)
    return tuple(out)


def selected_events(scenario: dict) -> tuple:
    """Every TRACE event name any constraint selects on, anchors included —
    the set a recorder has to be able to produce.

    Scoped to the trace record on purpose: `event` is a field name in three
    records and they do not share a namespace. `derivations` uses it for
    `command`/`derived` and `obligations` for `issued`/`acknowledged`, and
    folding those into the trace vocabulary would either fail every check or
    force three unrelated words into ENGINE_EVENTS.
    """
    found = set()

    def walk(select, record):
        if not isinstance(select, dict) or record != "trace":
            return
        value = select.get("event")
        if isinstance(value, str):
            found.add(value)
        elif isinstance(value, dict):
            for op in ("in", "not_in"):
                for v in value.get(op) or ():
                    found.add(v)

    for step in scenario.get("steps") or ():
        for c in constraints(step):
            walk(c.get("select"), c.get("record"))
            for key in ("with", "equals"):
                other = c.get(key) or {}
                walk(other.get("select"), other.get("record"))
            for anchor in (c.get("before"), c.get("after")):
                if isinstance(anchor, str):
                    resolved = ANCHORS.get(anchor) or {}
                    walk(resolved.get("select"), resolved.get("record"))
                elif isinstance(anchor, dict):
                    walk(anchor.get("select"), anchor.get("record"))
    return tuple(sorted(found))


def stale(scenario: dict, root: str) -> "str | None":
    """None when the manifest still describes its own source files; a reason
    naming the drift when it does not."""
    want = scenario.get("inputs_fingerprint")
    got = fingerprint(root, scenario.get("source_files") or ())
    if want == got:
        return None
    return (f"STALE: the flow extract of {scenario.get('skill')}'s source "
            f"files digests to {got}, and the manifest records {want}")


# ================================================================= the guards

def _bad_select(select, where: str) -> list:
    out = []
    if select is None:
        return out
    if not isinstance(select, dict):
        return [f"{where}: `select` must be an object"]
    for field, value in select.items():
        if not isinstance(value, dict):
            continue
        for op in value:
            if op not in SELECT_OPS:
                out.append(f"{where}: unknown selector operator {op!r} on "
                           f"{field!r} — the vocabulary is "
                           f"{', '.join(SELECT_OPS)}")
    return out


def _bad_constraint(c: dict, where: str) -> list:
    out = []
    check = c.get("check")
    if check not in CHECKS:
        out.append(f"{where}: unknown check {check!r} — the vocabulary is "
                   f"{', '.join(CHECKS)}")
    record = c.get("record")
    if record not in RECORDS:
        out.append(f"{where}: unknown record {record!r} — the vocabulary is "
                   f"{', '.join(RECORDS)}")
    out += _bad_select(c.get("select"), where)
    out += _bad_select((c.get("with") or {}).get("select"), where + " (with)")
    for key in ("before", "after"):
        anchor = c.get(key)
        if isinstance(anchor, str) and anchor not in ANCHORS:
            out.append(f"{where}: unknown anchor {anchor!r} on `{key}` — the "
                       f"anchors are {', '.join(sorted(ANCHORS))}")
        elif isinstance(anchor, dict):
            out += _bad_select(anchor.get("select"), where + f" ({key})")
    if check == "repeats" and not c.get("distinct_by"):
        out.append(f"{where}: `repeats` needs `distinct_by` — without it the "
                   f"check counts rows, not re-derivations")
    if check == "pairs" and not (c.get("with") and c.get("key")):
        out.append(f"{where}: `pairs` needs `with` and `key`")
    empty = c.get("allow_empty_with")
    if empty is not None:
        if check != "pairs" or not isinstance(empty, dict) or \
                empty.get("record") not in RECORDS:
            out.append(f"{where}: `allow_empty_with` is only valid on "
                       "`pairs` and must name a known record")
        else:
            out += _bad_select(empty.get("select"),
                               where + " (allow_empty_with)")
    if check == "field_equals" and not c.get("field"):
        out.append(f"{where}: `field_equals` needs `field`")
    equals = c.get("equals")
    if isinstance(equals, dict):
        if equals.get("record") not in COMPARANDS:
            out.append(f"{where}: `equals.record` {equals.get('record')!r} is "
                       f"not a comparand ({', '.join(COMPARANDS)})")
        if not equals.get("field"):
            out.append(f"{where}: `equals` needs a `field`")
    return out


def _bad_step(step, index: int, seen: set) -> list:
    where = f"steps[{index}]"
    if not isinstance(step, dict):
        return [f"{where}: a step is an object"]
    sid = step.get("id")
    where = f"step {sid!r}" if sid else where
    out = []
    if not sid:
        out.append(f"{where}: every step needs an `id`")
    elif sid in seen:
        out.append(f"{where}: duplicate step id {sid!r}")
    else:
        seen.add(sid)
    for key in step:
        if key not in _STEP_KEYS:
            out.append(f"{where}: unknown key {key!r} — a lenient loader "
                       f"would drop the constraint it was meant to carry")
    if not (step.get("claim") or "").strip():
        out.append(f"{where}: every step needs a human-readable `claim`")
    if step.get("applicable") is False and not (step.get("reason")
                                                or "").strip():
        out.append(f"{where}: `applicable: false` needs a `reason` — a step "
                   f"omitted in silence reads exactly like a step that "
                   f"passed")
    for tag in step.get("universal") or ():
        if tag not in UNIVERSAL:
            out.append(f"{where}: unknown universal tag {tag!r} — the "
                       f"universal rubric is {', '.join(UNIVERSAL)}")
    if step.get("check") == "all" and not (step.get("of") or ()):
        out.append(f"{where}: `check: all` needs a non-empty `of`")
    if step.get("record") not in RECORDS:
        out.append(f"{where}: unknown record {step.get('record')!r} — the "
                   f"vocabulary is {', '.join(RECORDS)}")
    for c in constraints(step):
        out += _bad_constraint(c, where)
    return out


def validate(scenario: dict, root: "str | None" = None) -> tuple:
    """Every error in `scenario`, as strings. Empty means valid.

    Strict about unknown keys in both directions. A lenient loader accepts a
    typo'd key, drops the constraint it carried, and reports a green rubric
    row for a control point that was never checked — which is the exact
    dishonesty this whole layer exists to remove.
    """
    out = []
    if not isinstance(scenario, dict):
        return ("a scenario is a JSON object",)
    if scenario.get("schema") != SCHEMA:
        out.append(f"schema must be {SCHEMA!r}, not "
                   f"{scenario.get('schema')!r}")
    for key in _TOP_KEYS:
        if key not in scenario:
            out.append(f"missing required key {key!r}")
    for key in scenario:
        if key not in _TOP_KEYS and not key.startswith("_"):
            out.append(f"unknown key {key!r}")

    skill = scenario.get("skill")
    path = scenario.get("_path")
    if path:
        stem = os.path.basename(path)[:-5]
        if skill != stem:
            out.append(f"skill {skill!r} does not match its file name "
                       f"{stem!r} — the file name is how the scorer finds it")

    sources = scenario.get("source_files") or []
    if not sources:
        out.append("source_files must name at least one file — a scenario "
                   "with no inputs has nothing to go stale against")
    if root:
        for rel in sources:
            if not os.path.isfile(os.path.join(root,
                                               str(rel).replace("/", os.sep))):
                out.append(f"source file {rel} does not exist")

    if scenario.get("terminal") not in TERMINALS:
        out.append("terminal must be one of " + ", ".join(TERMINALS))

    for key in scenario.get("expects_derivations") or ():
        if key not in derivation.KEYS:
            out.append(f"expects_derivations: {key!r} is not a derivation "
                       f"ledger key ({', '.join(derivation.KEYS)})")

    mandated = set()
    if root:
        for rel in sources:
            for item in flow_extract(_read(root, rel)):
                if item.startswith("require surface "):
                    mandated.add(item[len("require surface "):])
    for surface in scenario.get("declared_surfaces") or ():
        if derivation.verb(surface) != surface or \
                derivation.UNKNOWN in str(surface):
            out.append(f"declared_surfaces: {surface!r} is not a real "
                       f"taskplane surface")
        elif root and mandated and surface not in mandated:
            out.append(f"declared_surfaces: {surface!r} is not mandated by "
                       f"any of this skill's source files — a scenario may "
                       f"not grade a flow the skill never declared")

    steps = scenario.get("steps")
    if not isinstance(steps, list) or not steps:
        out.append("steps must be a non-empty list")
    else:
        seen: set = set()
        covered: set = set()
        live: set = set()
        for i, step in enumerate(steps):
            out += _bad_step(step, i, seen)
            if isinstance(step, dict):
                covered.update(step.get("universal") or ())
                if step.get("applicable") is not False:
                    live.update(step.get("universal") or ())
        # The two fields have to agree. A skill that derives nothing cannot
        # RE-derive anything, so a live `no_rederive` row there is a row that
        # can never fail — a fake green, which is worse than an absent one
        # because it looks checked. And a skill that DOES derive may not opt
        # out of the row that catches the complaint this ledger was built
        # for.
        derives = bool(scenario.get("expects_derivations"))
        if derives and "no_rederive" not in live:
            out.append("expects_derivations names derivations, so the "
                       "`no_rederive` step may not be declared inapplicable")
        if not derives and "no_rederive" in live:
            out.append("expects_derivations is empty, so a live "
                       "`no_rederive` step can never fail — declare it "
                       "`applicable: false` with the reason")
        for tag in UNIVERSAL:
            if tag not in covered:
                out.append(f"no step covers the universal rubric tag "
                           f"{tag!r} — declare it, with "
                           f"`applicable: false` and a reason if this "
                           f"skill's flow genuinely lacks it")

    if root and "inputs_fingerprint" in scenario and sources:
        reason = stale(scenario, root)
        if reason:
            out.append(reason.replace("STALE", "inputs_fingerprint is stale"))
    return tuple(out)


def _read(root: str, rel: str) -> str:
    try:
        with io.open(os.path.join(root, str(rel).replace("/", os.sep)),
                     encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""
