"""Start a review in ONE call, and hand every lens agent ONE copy of the context.

Two measured costs, one cause: the review's opening sequence and its fan-out
both re-derive things taskplane already holds.

  * The opening. A review ran onboard, init, new, target, graph scan, graph
    impact, lens route, lens dispatch and two dashboard renders before a
    single lens looked at the diff — about ten shell calls, at a measured
    ~11k effective tokens each, and every command AND its output stays in
    the conversation to be re-read on every later turn. `tp loop evidence`
    already proved the fix for the evaluate step in v2.6: return everything
    the step needs in one payload, with the judgement slots empty.

  * The fan-out. Four lens agents cost ~754k effective tokens, "each
    carrying its own copy of the diff and the blast-radius brief". The diff
    is identical for all of them. Writing it once and citing the path costs
    one file; embedding it N times costs N copies at output weight.

Neither changes what a review DECIDES. The briefs carry the same contract,
the same lens, the same read-only harness; they just stop restating a
document that is already on disk next to them.
"""
import json
import os

CONTEXT_DIR = os.path.join(".em-review", "context")
DIFF_NAME = "diff.patch"
IMPACT_NAME = "impact.json"
BRIEF_NAME = "blast-radius.md"


def context_dir(ws: str) -> str:
    return os.path.join(ws, CONTEXT_DIR)


def write_context(ws: str, *, diff: str = "", impact: dict | None = None,
                  blast_radius: str = "") -> dict:
    """Write the shared review context ONCE. Returns the paths written, or
    an empty dict if the workspace will not take them — in which case the
    caller keeps embedding, because a missing file must degrade to the old
    behaviour rather than to a brief with no context at all."""
    d = context_dir(ws)
    out = {}
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return out
    for name, body in ((DIFF_NAME, diff),
                       (BRIEF_NAME, blast_radius),
                       (IMPACT_NAME, json.dumps(impact, indent=2,
                                                sort_keys=True)
                        if impact else "")):
        if not body:
            continue
        p = os.path.join(d, name)
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            out[name] = os.path.join(CONTEXT_DIR, name)
        except OSError:
            continue
    return out


def context_note(paths: dict) -> str:
    """What a brief says INSTEAD of carrying the payload.

    Deliberately explicit that the files are already there: an agent told
    only "the diff is available" will re-derive it with `git diff`, which is
    the cost this exists to remove."""
    if not paths:
        return ""
    lines = ["\nSHARED REVIEW CONTEXT — already on disk, read it, do NOT "
             "re-derive it:"]
    if DIFF_NAME in paths:
        lines.append(f"  {paths[DIFF_NAME]}  — the full diff under review "
                     f"(do not run `git diff` again)")
    if BRIEF_NAME in paths:
        lines.append(f"  {paths[BRIEF_NAME]}  — blast radius from the "
                     f"dependency graph (do not re-run `graph impact`)")
    if IMPACT_NAME in paths:
        lines.append(f"  {paths[IMPACT_NAME]}  — the impact payload as JSON")
    lines.append("  Every lens agent in this wave reads the SAME files. "
                 "They were written once, before dispatch.")
    return "\n".join(lines) + "\n"
