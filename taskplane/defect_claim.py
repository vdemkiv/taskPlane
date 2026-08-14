"""Finding admissibility — what may enter the findings surface (R-0013).

WHY THIS EXISTS. The v3 phase 3 review produced twenty-one findings. On
human read-back, roughly seven were not defects: a critique of a calibration
corpus's composition, an architectural property documented and true since
before the phase, edge-scan arithmetic for a hypothetical five-hundred-module
repo, an objection to a deliberate design choice conditional on a tool nobody
has installed, and a meta-observation about a line ratchet. Every one carried
a severity and rendered in a gate-blocking dashboard indistinguishable from a
real bug. Worse, the misfiling ran BOTH ways: A4 — a guardrail that had
shipped completely inert — sat in the same pile classed as an observation.

The finding discipline already answers "does this block?" (regression /
pre-existing / observation). It never answered the prior question: is this a
DEFECT — a claim that something misbehaves — or is it commentary?

WHY THIS IS NOT A TEXT HEURISTIC, AND WHY THAT MATTERS. The first cut of
this module scored finding prose for words like "verified" and "measured".
Run against the real corpus it kept a byte-count measurement of skill files
and downgraded a HIGH that the reviewer had reproduced live. That is the
same failure the module exists to fix — a check that looks rigorous and
sorts by the wrong signal. No machine reading a paragraph can tell a defect
from a well-measured observation, because the difference is not in the prose.

So the bar is STRUCTURAL. A finding that blocks a gate must carry an
explicit claim block:

  claim:
    trigger:  concrete inputs or state that reach the problem
    outcome:  the wrong thing that is then observed
    repro:    what another person runs or follows to see it

Writing that block is the reviewer's judgment, made explicit. The engine
only checks it is there and non-empty — which it can do honestly.

WHAT HAPPENS TO COMMENTARY. Nothing is deleted. A row that is neither a
structural defect claim nor a violation of a repository declaration is routed
to the note stream. It remains durable and measurable, but it does not render
as a finding and cannot gate the change.

THE DIRECTION THIS FAILS. A complete claim block always wins, even when a
producer labelled the row as a note. The engine never scores prose. It checks
only structural fields and resolvable declaration identities. The frozen
`finding_blocks` rule remains untouched and decides which *admissible*
findings block.
"""

from __future__ import annotations

import re
import os

REQUIRED = ("trigger", "outcome", "repro")

# Enough words that a field must say something; short enough that a real
# one-line repro ("run tp loop gate pass in a worktree") is never rejected.
MIN_FIELD_WORDS = 4


def _words(value) -> int:
    return len([w for w in re.split(r"\s+", str(value or "").strip()) if w])


def is_note(finding) -> bool:
    """A row the reviewer explicitly filed as commentary."""
    return (isinstance(finding, dict)
            and str(finding.get("kind") or "").strip().lower() == "note")


def claim_of(finding):
    """The finding's explicit claim block, or None. Deliberately does NOT
    infer one from prose — see the module docstring."""
    if not isinstance(finding, dict):
        return None
    claim = finding.get("claim")
    return claim if isinstance(claim, dict) else None


def claim_errors(finding) -> list:
    """Why this finding's claim is not usable ([] = a complete claim)."""
    claim = claim_of(finding)
    if claim is None:
        return ["no claim block: state trigger, outcome and repro — what "
                "reaches the problem, what goes wrong, and how someone else "
                "sees it — or file the row as `kind: note`"]
    errors = []
    for field in REQUIRED:
        if _words(claim.get(field)) < MIN_FIELD_WORDS:
            errors.append(f"claim.{field} is missing or too thin to act on")
    return errors


def is_defect_claim(finding) -> bool:
    return not claim_errors(finding)


# The statuses the em gate already treats as settled (audit.py's
# unresolved-high sweep). A settled row owes nothing: demanding a claim for
# a finding somebody already resolved would be the engine arguing with a
# closed ticket.
SETTLED_STATUSES = (
    "acted", "dismissed", "resolved", "accepted", "closed", "deferred",
    "not-a-defect",
)

NOTE_CATEGORIES = frozenset({
    "recorded-decision", "hypothetical-scale", "review-meta",
    "tool-enforced", "pre-existing-documented", "unrequired-absence",
})


def _safe_repo_file(workspace: str, raw_path: str) -> str | None:
    """Resolve a repo-relative declaration path without accepting traversal."""
    rel = str(raw_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel:
        return None
    root = os.path.realpath(workspace)
    candidate = os.path.realpath(os.path.join(root, rel))
    try:
        if os.path.commonpath((root, candidate)) != root:
            return None
    except (OSError, ValueError):
        return None
    return candidate if os.path.isfile(candidate) else None


def declaration_resolves(workspace: str, identity) -> bool:
    """Resolve the finite declaration identities a violation may cite.

    Accepted forms are deliberately mechanical, never prose:
    ``R-0001``; ``decision:0001``; ``config:path#key``;
    ``budget:path#name``; and ``reference:path#Heading``.  Config/budget keys
    must occur in the named repository file.  Reference headings must match an
    actual Markdown heading in ``lenses/references``.
    """
    value = str(identity or "").strip()
    if not value:
        return False
    if re.fullmatch(r"R-\d{4,}", value):
        try:
            import requirements
            return requirements.get_requirement(workspace, value) is not None
        except Exception:
            return False
    if value.startswith("decision:"):
        decision_id = value.split(":", 1)[1].strip()
        if not decision_id:
            return False
        try:
            import kb
            return kb.get_decision(workspace, decision_id) is not None
        except Exception:
            return False
    match = re.fullmatch(r"(config|budget|reference):([^#]+)#(.+)", value)
    if not match:
        return False
    kind, rel, anchor = match.groups()
    path = _safe_repo_file(workspace, rel)
    if not path:
        return False
    if kind == "reference" and not rel.replace("\\", "/").startswith(
            "lenses/references/"):
        return False
    try:
        with open(path, encoding="utf-8") as stream:
            text = stream.read()
    except OSError:
        return False
    if kind == "reference":
        wanted = re.sub(r"\s+", " ", anchor.strip()).casefold()
        return any(re.sub(r"\s+", " ", line.lstrip("#").strip()).casefold()
                   == wanted for line in text.splitlines()
                   if line.startswith("#"))
    # Config and budget declarations are identifiers, not arbitrary prose.
    token = anchor.strip().split(".")[-1]
    return bool(token and re.search(r"(?<![A-Za-z0-9_-])" +
                                    re.escape(token) +
                                    r"(?![A-Za-z0-9_-])", text))


def admissibility(finding, *, workspace: str | None = None,
                  resolver=None) -> dict:
    """Classify one producer row as defect, violation, or note.

    Complete defect claims have priority over every producer label.  A
    violation is admissible only when its ``declares`` identity resolves.
    Everything else is a note; no row is silently discarded.
    """
    if not isinstance(finding, dict):
        return {"kind": "note", "admissible": False,
                "reason": "finding is not an object"}
    if is_defect_claim(finding):
        return {"kind": "defect", "admissible": True, "reason": "claim"}
    kind = str(finding.get("kind") or "").strip().lower()
    if kind == "violation":
        check = resolver
        if check is None and workspace is not None:
            check = lambda identity: declaration_resolves(workspace, identity)
        if check is not None and check(finding.get("declares")):
            return {"kind": "violation", "admissible": True,
                    "reason": "declared-standard"}
        return {"kind": "note", "admissible": False,
                "reason": "unresolved-declaration"}
    category = str(finding.get("note_category") or "").strip().lower()
    reason = category if category in NOTE_CATEGORIES else "no-admissible-claim"
    return {"kind": "note", "admissible": False, "reason": reason}


def _settled(finding) -> bool:
    return (isinstance(finding, dict)
            and str(finding.get("status", "open")).lower() in SETTLED_STATUSES)


def blocking_errors(findings, blocks) -> list:
    """Refuse a gate blocked by a row that never said what breaks.

    `blocks` is the caller's frozen predicate (loop.finding_blocks) — this
    module never decides WHICH findings block, only that a blocking one owes
    a claim. Purely additive: it can add a refusal, never remove one.

    A row marked `kind: note` that still blocks is its own refusal: a note
    is commentary by the reviewer's own declaration and must not carry a
    blocking severity."""
    errors = []
    for f in findings or []:
        if not blocks(f) or _settled(f):
            continue
        title = str((f or {}).get("title") or "untitled")[:70]
        if is_note(f):
            errors.append(f"finding is filed as a note but blocks the gate — "
                          f"a note cannot carry a blocking severity: {title}")
            continue
        for e in claim_errors(f):
            errors.append(f"blocking finding without a defect claim ({e}): "
                          f"{title}")
    return errors


def partition(findings) -> dict:
    """Split reviewer output for RENDERING: claimed defects, unclaimed rows,
    and declared notes. Notes lose their severity — that is the point.
    Nothing is dropped."""
    claimed, unclaimed, notes = [], [], []
    for f in findings or []:
        if is_note(f):
            note = dict(f) if isinstance(f, dict) else {"title": str(f)}
            note.pop("severity", None)
            notes.append(note)
        elif is_defect_claim(f):
            claimed.append(f)
        else:
            unclaimed.append(f)
    return {"findings": claimed, "unclaimed": unclaimed, "notes": notes}
