#!/usr/bin/env python3
"""Generate docs/lens-catalog.md FROM lenses/catalog.json so the doc can't
drift from the engine that routes review. Run after editing the catalog:
    python3 scripts/gen_lens_catalog.py
The table + counts + tiers are derived; the prose notes below the table are
kept in this generator so the whole doc regenerates in one place."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAT = os.path.join(ROOT, "lenses", "catalog.json")
OUT = os.path.join(ROOT, "docs", "lens-catalog.md")

# Group display order.
ORDER = ["Product & delivery", "Engineering craft", "Architecture & systems",
         "Quality & verification", "Data", "Operations", "Interfaces",
         "Experience", "Docs", "Compliance"]

BASELINE_NOTE = {
    "code-quality": " *(signal baseline; not automatic dispatch)*",
    "security": " *(signal baseline; not automatic dispatch)*",
    "testability": " *(signal baseline; not automatic dispatch)*",
    "architecture": " *(mandatory evidenced floor; not automatic dispatch)*",
}
OPTIONAL = {"cost-finops", "i18n"}


def main(*, check=False):
    with open(CAT, encoding="utf-8") as f:
        data = json.load(f)
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
             "generator's prose), then regenerate — don't hand-edit. CI "
             "regenerates and diffs this file (and the other generated lens "
             "artifacts) on every push, so a stale copy fails the build.\n")
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

    L.append("## Mandatory floors remain evidence-based\n")
    L.append("Architecture and security cannot be silently omitted when "
             "canonical evidence shows their owned boundary. The focused "
             "policy applies each floor before it groups overlapping risks "
             "and caps normal Plan execution at 3–4 quick lenses. "
             "A floor guarantees an evidenced disposition; it does not "
             "authorize a Build/Fix/Evaluate/final-EM worker or an automatic "
             "full-catalog run.\n")

    # Strategy is a single SUMMONED lens (tp-northstar), not a scheduled
    # "advisory board" tier — the board was removed in v1.0. Only emit this
    # section if the catalog actually carries advisory-tier lenses (it does
    # not), so the doc can't advertise "0 strategy lenses". (v1.5.2)
    if board:
        L.append("## Advisory (strategy) tier\n")
        L.append(f"{len(board)} **strategy lenses** run at the "
                 "*should-we-build-this* level on requirements/roadmap/"
                 "context artifacts rather than code:\n")
        for x in board:
            L.append(f"- **`{x['id']}`** — {x['charter']}.")
        L.append("")

    L.append("## Routing notes\n")
    L.append("- Every routed Product, Design, and Plan stage emits "
             "one evidenced `execute_deep`, `execute_light`, `covered_by`, or "
             f"`not_applicable` row for all {n} lenses.")
    L.append("- Only the two `execute_*` rows dispatch. Normal delivery uses "
             "focused quick routes. Build, Fix, Evaluate, and final "
             "engineering review launch zero lens workers; Evaluate is a "
             "direct evidence collector and judge and creates no lens "
             "artifacts.")
    L.append("- More than four independent mandatory Plan risks split scope "
             "or require protected exact-target expanded-route authority. "
             "That authority is Plan-only.\n")

    L.append("## Adding a lens\n")
    L.append("Append an entry to `lenses/catalog.json` (id, name, group, "
             "charter, boundary, globs, task_types, baseline?, deep_globs), "
             "author its `lenses/<id>.md` evaluator prompt, then run "
             "`python3 scripts/gen_lens_catalog.py` to refresh this doc. The "
             "router picks the lens up automatically.")

    rendered = "\n".join(L) + "\n"
    if check:
        try:
            with open(OUT, encoding="utf-8") as f:
                current = f.read()
        except FileNotFoundError:
            current = ""
        if current != rendered:
            raise SystemExit(f"stale generated lens catalog: {OUT}")
        print(f"current {OUT}: {n} lenses across {len(groups)} groups "
              f"({len(board)} advisory)")
        return
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(rendered)
    print(f"wrote {OUT}: {n} lenses across {len(groups)} groups "
          f"({len(board)} advisory)")


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--check"]):
        raise SystemExit("usage: gen_lens_catalog.py [--check]")
    main(check=sys.argv[1:] == ["--check"])
