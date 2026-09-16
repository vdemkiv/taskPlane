"""Generate lenses/<id>.md evaluator prompts from catalog.json + review guides.

The catalog stays the single source of truth for charter/boundary/globs; this
script merges in the hand-authored review guide per lens (what to examine,
what counts as a blocker vs major) and the shared verdict format. Re-run after
editing GUIDES or the catalog.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Hand-authored review guides. examine: the specific things this lens checks
# in a diff. blocker/major: severity anchors so verdicts are consistent.
_GROUNDING = (
    "Ground judgment in the baseline and existing decisions selected for the "
    "requested review. Missing context is a coverage limitation; never search another "
    "workspace, knowledge store, or prior session for substitute inputs. Prefer "
    "existing capabilities and the smallest concrete correction."
)


GUIDES = json.load(open(os.path.join(HERE, "_guides_data.json")))
# Guides moved out of this file in lenses 2.0 — 26 lenses x ~7 multi-line
# checks is past the size where a Python literal stays editable. Each entry:
#   examine[]  the numbered checks, rendered in order
#   blocker/major  severity anchors
#   preamble   optional standing instruction BEFORE the numbered list
#   caveat     optional standing rule AFTER it (e.g. "a coverage % is never
#              on its own a blocker") — these bound what the lens may claim
#              and were the most common thing the old template could not say
#   routing_note  optional prose under "Fires when", for a trigger whose
#              reason is not obvious from the globs



# Deep references shipped with the plugin (lenses/references/*) — appended
# to the evaluator prompt of the lenses that own them.
REFS = {
 "security": """## Deep methodology (subagent mode / high-stakes surfaces)

Follow `lenses/references/security-methodology.md` — the full procedure:
scanner gate first (gitleaks, ecosystem CVE audit, semgrep/bandit/gosec),
then OWASP Web Top 10 (2021) passes incl. access control & RLS, injection,
auth/session, data protection — and the OWASP LLM Top 10 (2025) passes when
the change touches an AI surface (prompt-injection input guard included).
Grade findings by its severity table; a scanner that cannot run is itself a
finding.""",
 "dba": """## Deep references

- **Engine choice** (requirement/plan time): follow
  `lenses/references/database-selection.md` — four workload questions,
  relational-by-default, scenario table, polyglot red flags. Record the
  choice to the KB.
- **Migration scripts**: the schema QUALITY side of
  `lenses/references/migration-scripts.md` §5 (hygiene) and §4 (data
  correctness) — safety belongs to data-safety; don't double-grade.""",
 "data-safety": """## Deep reference — migration scripts

Follow `lenses/references/migration-scripts.md` in full: expand/contract
as the only safe shape, lock analysis on hot tables, tested reversibility,
data correctness for existing rows, idempotency. Its severity anchors
override the generic ones below for migration files.""",
 "design": """## Deep audit (subagent mode / UI-heavy changes)

Follow `lenses/references/ui-audit.md` for the full pass: state inventory
(loading/empty/error/partial/success per surface), flow walk (entry → happy
→ failure → recovery → exit), consistency sweep (tokens, spacing scale,
type ramp), and the usability heuristics checklist. Hand a11y findings to
the accessibility lens — note, don't grade them.""",
 "code-quality": """## Language delegation (apply first)

Detect the changed files' language and apply the matching deep reference in
`lenses/references/` **in addition to** the examine list above:

| Changed files | Reference |
|---|---|
| `.ts` / `.tsx` | `typescript-code-quality.md` |
| `.py` | `python-code-quality.md` |
| `.go` | `go-code-quality.md` |
| other / mixed | the generic examine list; name unknown-language files |

Each language reference carries its own Reuse & Duplication section (run a
copy-paste detector, e.g. `jscpd`) — new code must reuse existing
helpers/components/types, not re-implement them. Deep security review is the
security lens's job (see its methodology); don't duplicate it here.""",
}

VERDICT = """## Shared review evidence

Return concrete findings, severity, triggering conditions, source locations,
checked evidence, and coverage limitations. Use `agents/tp-lens.md` and attach
this evidence to the existing run and review index. The root orchestrator
integrates results and advances delivery.
"""

USAGE = """## How this lens runs

Apply this lens where it helps verify the requested outcome. Product, Design,
Plan, Build, Evaluate, Engineering and Retro share one task decomposition,
dependency graph and dashboard. Use native tools and host permissions.
Delegate only when authorized and useful. There is no mandatory lens count,
sealed brief, lease, submission receipt or Taskplane token cap.
"""

def build(lz):
    g = GUIDES[lz["id"]]
    lines = [f"# {lz['name']} lens", ""]
    lines += [f"**Group:** {lz['group']}",
              f"**Charter:** {lz['charter']}",
              f"**Does NOT own:** {lz['boundary']}", ""]
    lines += ["## Looks for", lz["looks_for"], ""]
    fires = []
    if lz.get("globs"):
        fires.append("- files match: " + ", ".join(lz["globs"]))
    if lz.get("task_types"):
        fires.append("- task types: " + ", ".join(lz["task_types"]))
    if lz.get("baseline"):
        fires.append("- baseline: yes (any code change)")
    if lz.get("deep_globs"):
        fires.append("- runs as **subagent** when: "
                     + ", ".join(lz["deep_globs"]))
    if lz.get("untested_trigger"):
        fires.append("- untested change: any code change that adds no test file")
    if fires:
        lines += ["## Fires when"] + fires + [""]
        if g.get("routing_note"):
            lines += ["> " + g["routing_note"], ""]
    if lz.get("checks"):
        lines += ["## Deterministic checks (run before the LLM perspective)"]
        lines += [f"- {c}" for c in lz["checks"]] + [""]
    # A multi-clause boundary rendered as “anything under “<all of it>”
    # belongs to that lens” is nonsense — there is no single "that lens", and
    # a clause that is a policy statement rather than a redirect reads as a
    # dangling reference. Clauses are split and each names its own owner.
    _clauses = [c.strip() for c in lz["boundary"].split(";") if c.strip()]
    if len(_clauses) > 1:
        _bound = ("Stay inside it — each topic in the “Does NOT own” list "
                  "belongs to the lens named beside it; note it in one line "
                  "and move on.")
    else:
        _bound = (f"Stay inside it — anything under “{lz['boundary']}” "
                  "belongs to that lens; note it in one line and move on.")
    lines += ["## Evaluator prompt", "",
              f"You are reviewing this change through the **{lz['name']}** "
              f"lens only. Your charter: {lz['charter']}. {_bound}", ""]
    if g.get("preamble"):
        lines += [g["preamble"], ""]
    lines += ["Examine, with file:line evidence:", ""]
    lines += [f"{i}. {item}" for i, item in enumerate(g["examine"], 1)]
    if g.get("caveat"):
        lines += ["", g["caveat"]]
    if lz["id"] in REFS:
        lines += ["", REFS[lz["id"]]]
    lines += ["", f"**Blocker** = {g['blocker']}.",
              f"**Major** = {g['major']}.",
              "Minor = worth fixing, doesn't gate. Prefer the smallest "
              "suggestion that resolves each finding.", "",
              USAGE, "", VERDICT, ""]
    return "\n".join(lines)


# Lenses whose prompt file is deliberately hand-authored (richer than this
# template can express). The generator leaves them alone but still fails on a
# catalog lens that has NEITHER a guide here NOR a hand-authored prompt file.
HAND_AUTHORED = {"solution-design"}

def main():
    cat = json.load(open(os.path.join(HERE, "catalog.json")))
    missing = [lz["id"] for lz in cat["lenses"]
               if lz["id"] not in GUIDES and lz["id"] not in HAND_AUTHORED]
    assert not missing, f"no review guide for: {missing}"
    for lz in cat["lenses"]:
        path = os.path.join(HERE, lz["id"] + ".md")
        if lz["id"] in HAND_AUTHORED:
            with open(path) as stream:
                content = stream.read().split("## How this lens runs")[0].rstrip()
            content += "\n\n" + USAGE + "\n\n" + VERDICT + "\n"
        else:
            content = build(lz)
        with open(path, "w") as stream:
            stream.write(content.rstrip() + "\n")
    print(f"wrote {len(cat['lenses'])} prompts with one shared protocol")


if __name__ == "__main__":
    main()
