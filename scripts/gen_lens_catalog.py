#!/usr/bin/env python3
"""Generate docs/lens-catalog.md FROM lenses/catalog.json so the doc can't
drift from the engine that routes review. Run after editing the catalog:
    python3 scripts/gen_lens_catalog.py
The table + counts + tiers are derived; the prose notes below the table are
kept in this generator so the whole doc regenerates in one place."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAT = os.path.join(ROOT, "lenses", "catalog.json")
OUT = os.path.join(ROOT, "docs", "lens-catalog.md")

# Group display order.
ORDER = ["Product & delivery", "Engineering craft", "Architecture & systems",
         "Quality & verification", "Data", "Operations", "Interfaces",
         "Experience", "Docs", "Compliance"]

BASELINE_NOTE = {
    "code-quality": " *(baseline on any code)*",
    "security": " *(baseline on any code)*",
    "testability": " *(baseline on any code)*",
    "architecture": " *(always-on — light pass on any change, full pass when structural)*",
}
OPTIONAL = {"cost-finops", "i18n"}


def main():
    lenses = json.load(open(CAT))["lenses"] if isinstance(
        json.load(open(CAT)), dict) else json.load(open(CAT))
    # re-read cleanly (json.load consumed above only in the isinstance check)
    data = json.load(open(CAT))
    lenses = data["lenses"] if isinstance(data, dict) else data
    n = len(lenses)
    by_group = {}
    for x in lenses:
        by_group.setdefault(x.get("group", "Other"), []).append(x)
    groups = [g for g in ORDER if g in by_group] + \
        [g for g in by_group if g not in ORDER]

    board = by_group.get("Advisory (strategy)", [])
    L = []
    L.append("# Lens catalog — the full set\n")
    L.append(f"{n} lenses, grouped by the team perspective they represent. The "
             "design rule: **every lens has a distinct charter and an explicit "
             "\"does NOT own\" boundary, so they compose** — a `.tsx` change "
             "fires *design* (UX), *frontend* (implementation) and "
             "*accessibility* (a11y) without three of them reporting the same "
             "thing. Machine definitions live in `lenses/catalog.json`; each "
             "lens also has a `lenses/<id>.md` stub for its evaluator prompt.\n")
    L.append("> This file is GENERATED from `lenses/catalog.json` by "
             "`scripts/gen_lens_catalog.py`. Edit the catalog (or the "
             "generator's prose), then regenerate — don't hand-edit.\n")
    L.append("## The set, by group\n")
    L.append("| Group | Lens | Charter (what it uniquely owns) |")
    L.append("| --- | --- | --- |")
    for g in groups:
        rows = by_group[g]
        for i, x in enumerate(rows):
            gcol = f"**{g}**" if i == 0 else ""
            opt = " · *opt*" if x["id"] in OPTIONAL else ""
            note = BASELINE_NOTE.get(x["id"], "")
            L.append(f"| {gcol} | {x['id']}{opt} | {x['charter']}{note} |")
    L.append("\n*opt* = suggested/optional (off unless its files appear).\n")

    L.append("## Always-on floor: architecture & system design\n")
    L.append("**Architecture is routed on every code change** — a light pass "
             "on any diff, a full pass when the change is structurally "
             "significant. That floor is enforced by the engine "
             "(`tp lens route --all`), not by memory: component boundaries, "
             "data flow, contracts, and failure modes get a look even when no "
             "architecture files changed.\n")

    if board:
        L.append("## Advisory (strategy) tier\n")
        L.append("Beyond the per-change review lenses, "
                 f"{len(board)} **strategy lenses** run at the *should-we-"
                 "build-this* level, on requirements/roadmap/context "
                 "artifacts rather than code:\n")
        for x in board:
            L.append(f"- **`{x['id']}`** — {x['charter']}.")
        L.append("")
    else:
        L.append("## Strategy: the north-star review\n")
        L.append("Strategy is deliberately NOT a lens tier. The "
                 "*should-we-build-this* question is answered on demand by the "
                 "**north-star review** (`/tp-northstar`) \u2014 a summoned, "
                 "advisory pass that measures a target against the project's "
                 "`Direction / north star` (alignment + Leverage, "
                 "Reversibility, Opportunity cost, Coherence). It never gates "
                 "the loop and is not part of this per-change catalog.\n")

    L.append("## Routing notes\n")
    L.append("- **Baselines are intentionally only four** — `code-quality`, "
             "`security`, `testability`, and always-on `architecture` — so a "
             f"typical change fires ~4–7 lenses, not all {n}. Role lenses fire "
             "by context (files/task type).")
    L.append("- **Mode** (`inline` vs governed `subagent`) is per-lens, set by "
             "`deep_globs` or change size; a wide review fans them out as "
             "parallel `tp-lens` agents (`tp lens dispatch`).")
    L.append("- **`tp lens route`** shows exactly which fired and why; "
             "`--only`/`--skip` override; `--all` returns the full catalog "
             "(deep + sweep).\n")

    L.append("## Adding a lens\n")
    L.append("Append an entry to `lenses/catalog.json` (id, name, group, "
             "charter, boundary, globs, task_types, baseline?, deep_globs), "
             "author its `lenses/<id>.md` evaluator prompt, then run "
             "`python3 scripts/gen_lens_catalog.py` to refresh this doc. The "
             "router picks the lens up automatically.")

    open(OUT, "w").write("\n".join(L) + "\n")
    print(f"wrote {OUT}: {n} lenses across {len(groups)} groups "
          f"({len(board)} advisory)")


if __name__ == "__main__":
    main()
