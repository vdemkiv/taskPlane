# `evals/fixture-repo/` — the pull request a recorded run reviews

A scenario that graded a real GitHub PR would grade GitHub too. A rename
upstream, a force-push, a rate limit, an outage — any of them moves the
score, and then nobody can say whether the model got worse or the network
did. So the "pull request" this layer records against is frozen here, and a
score drop is unambiguously the model's fault.

## What is here

    tree-a/       the base tree, as PLAIN FILES
    tree-b/       the head tree, as PLAIN FILES
    manifest.json the identity, the dates, and the commit SHAs both trees
                  must produce
    conftest.py   keeps these trees out of the repository's own pytest
                  collection (see the file; it is not optional)

## Why plain files and never a committed `.git`

A nested `.git` directory inside a repository is committed as a **gitlink** —
a single mode-160000 tree entry naming a commit SHA. The objects behind that
SHA are not committed with it, because they live in the inner repo's own
object database, which git will not walk. A fresh clone therefore gets the
pointer and nothing to resolve it with: the fixture cannot be materialized at
all, and the failure appears as an empty directory rather than as an error.

Two trees plus a deterministic builder has none of that. It is diffable in
review, it survives every packaging path, and the SHAs it produces are pinned
in `manifest.json` and checked on every build.

## Determinism

`scripts/eval_record.py:build_fixture()` builds these trees into a throwaway
directory under an environment constructed from nothing — author and
committer identity, both dates, `TZ`, the locale and every git config source
are pinned, never inherited. The head SHA is compared against
`manifest.json` **before** a recorded run is allowed to proceed, and a
mismatch is refused loudly rather than recorded quietly.

Change a byte in either tree and the SHAs move. That is the point: re-pin
the manifest deliberately (`python3 scripts/eval_record.py --build <dir>`
prints what it built), and every recorded run made against the old bytes is
correctly marked stale.

## The diff the scenario grades

`tree-a` -> `tree-b` is one feature landing: order-level discount codes.

    README.md                 M   the rule the feature adds
    pricing/checkout.py       M   applies the discount before tax
    pricing/discount.py       A   the new module
    tests/test_checkout.py    M   one new case
    tests/test_discount.py    A   the new module's tests

Five files, two of them tests, one new module with a new import edge. There
is something honest for a qa lens to say (`percent_off` is unclamped, and no
test covers a code above 100) and something for the architecture floor to
say (`checkout` gains a dependency), which is what makes a recorded review of
this diff worth scoring at all.
