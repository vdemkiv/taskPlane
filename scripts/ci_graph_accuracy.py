#!/usr/bin/env python3
"""Graph accuracy — is the dependency graph RIGHT, on codebases that are not ours?

WHY THIS EXISTS. taskplane has a cost meter (ci_loop_cost.py) and now a yield
meter (yield_meter.py). It has never had an ACCURACY meter for the artifact
that the whole review depends on: the dependency graph. Nothing scored whether
the graph describes the codebase, so the graph drifted a long way without
anyone noticing — on this repo it reports 16 modules and 11 edges for a tree
whose engine alone has 18 modules and 57 internal edges, and it mints four
phantom modules out of test fixtures.

That was not discoverable by reading the code. It was discoverable only by
comparing derived output against a known answer, which is exactly what this
script does.

WHAT IS SCORED. Four fixture repos under corpus/, each with a hand-authored
`_expected.json` naming the modules and edges a competent reviewer would
expect. They are chosen to be the four profiles taskplane must handle:

  polyglot-app        TS front end, Python service, SQL, IaC, CI. The coupling
                      CROSSES runtimes (HTTP call, shared schema), which an
                      import graph cannot see by construction.
  workspace-monorepo  npm workspaces + go.work. Module identity is DECLARED in
                      manifests; path-depth guessing gets it wrong for free.
  plugin-md           A plugin whose product is markdown and declarative JSON.
                      Zero files match CODE_EXT, so the repo is invisible.
  legacy-monolith     Deep Java package tree plus DI wiring in XML: one edge
                      static analysis sees, one it never will.

SCORED IN THE GRAPH'S OWN MODEL, not a flat module/edge list. The design
carries node KINDS (module, `ext:` external, `svc:` infra, `contract:`,
`resource:`, `req:` requirement) and two edge FAMILIES that callers must not
confuse — DEPENDENCY ("from NEEDS to": imports, depends_on, consumes, depends,
calls, uses) versus STRUCTURAL/ANNOTATION (defined_in, planned, realizes,
provides, validates, changes), several of which point BACKWARDS relative to
the dependency they describe. Only the first family may answer "does real code
depend on this?".

So an edge found with the WRONG KIND is not a hit, and a structural edge
miscounted as a dependency is called out by name — that is the defect class
the engine's own comments record as D-0002, where a fixture's docker-compose
gave a module two "dependents" that were its own fixtures.

Each `_expected.json` is validated at load against the engine's `_node_kind`
and `is_dependency_edge`, so ground truth cannot drift from the design it is
meant to measure.

WHAT THE NUMBERS MEAN. Per profile, for nodes and for edges:

  recall      of the things that SHOULD be found, how many were.
              Low recall = blind spots. This is the number that matters most
              for review, because a missed edge is a missed blast radius.
  precision   of the things found, how many should have been.
              Low precision = fiction. Phantom modules minted from fixtures
              are precision failures, and they cost real money by routing
              lenses at things that do not exist.

Both are reported. Neither is averaged into a single score, because they fail
for different reasons and have different fixes.

EXPECTED EDGES CARRY A DIFFICULTY, and it is the honest part of this harness:

  easy           a same-language import the current scanner should already get
  manifest       declared in package.json / go.mod / pyproject / pom.xml —
                 free accuracy the scanner does not currently read
  declared       stated in the repo's own structured data (frontmatter, a
                 routing catalog) — free, and currently unread
  cross-runtime  an HTTP call, a shared schema, DI wiring. NOT statically
                 derivable in general. Scored separately and never counted as
                 a scanner failure; it is what `tp graph edge` and observed
                 dispatch exist for.
  not-derivable  the relationship is real but the stated ENDPOINTS cannot be
                 recovered from anything in the repo — a root build manifest
                 declares a dependency for the whole project and cannot say
                 which package uses it. Distinct from cross-runtime, where
                 the endpoints are knowable and only the CALL is invisible.
                 A scanner that produced these would be guessing.

So the headline is deliberately three numbers, not one: what the scanner
should get today, what a manifest reader would add for free, and what needs
recording rather than scanning.

THIS GATES NOTHING. It is an instrument, like the yield meter. It prints a
baseline and exits 0 unless a fixture is malformed. Once a scanner change
lands, the numbers move and the change is measured rather than argued. Pin it
later, on purpose, when there is a number worth defending.
"""
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "corpus")
sys.path.insert(0, os.path.join(ROOT, "taskplane"))

DIFFICULTY_ORDER = ("easy", "manifest", "declared", "cross-runtime",
                    "not-derivable")


def _load_expected(profile_dir):
    """Load ground truth AND validate it against the engine's own model, so
    the corpus cannot quietly encode a schema the product does not have."""
    import depgraph
    path = os.path.join(profile_dir, "_expected.json")
    with io.open(path, encoding="utf-8") as f:
        exp = json.load(f)
    for n in exp["nodes"]:
        real = depgraph._node_kind(n["id"])
        if real != n["kind"]:
            raise SystemExit(f"{path}: node {n['id']} declared kind "
                             f"{n['kind']!r}, engine says {real!r}")
    for e in exp["edges"]:
        fam = "dependency" if depgraph.is_dependency_edge(e) else "structural"
        if fam != e["family"]:
            raise SystemExit(f"{path}: edge kind {e['kind']!r} declared "
                             f"{e['family']!r}, engine says {fam!r}")
    return exp


def _scan(profile_dir):
    """Run the REAL scanner over the fixture and return (modules, edges)."""
    import depgraph
    import taskplane_lite as tp
    home = os.path.join(profile_dir, ".tp-accuracy-home")
    prev = os.environ.get("TASKPLANE_HOME")
    os.environ["TASKPLANE_HOME"] = home
    try:
        depgraph.scan(profile_dir)
        g = depgraph.load(profile_dir) or {}
    finally:
        if prev is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = prev
    mods = set(g.get("modules") or {})
    edges = {(str(e.get("from")), str(e.get("to")))
             for e in (g.get("edges") or []) if isinstance(e, dict)}
    kinded = {(str(e.get("from")), str(e.get("to")), str(e.get("kind")))
              for e in (g.get("edges") or []) if isinstance(e, dict)}
    return mods, edges, kinded, tp


def _score(found, expected):
    found, expected = set(found), set(expected)
    hit = found & expected
    recall = len(hit) / len(expected) if expected else 1.0
    precision = len(hit) / len(found) if found else (1.0 if not expected else 0.0)
    return recall, precision, sorted(expected - found), sorted(found - expected)


def main() -> int:
    if not os.path.isdir(CORPUS):
        print(f"graph-accuracy: no corpus at {CORPUS}", file=sys.stderr)
        return 1
    profiles = sorted(d for d in os.listdir(CORPUS)
                      if os.path.isdir(os.path.join(CORPUS, d)))
    if not profiles:
        print("graph-accuracy: corpus is empty", file=sys.stderr)
        return 1

    by_difficulty = {d: [0, 0] for d in DIFFICULTY_ORDER}   # [found, total]
    print("graph accuracy — the current scanner against a known answer\n")

    for name in profiles:
        d = os.path.join(CORPUS, name)
        exp = _load_expected(d)
        mods_found, edges_found, edges_kinded, _tp = _scan(d)

        exp_nodes = {n["id"] for n in exp["nodes"]}
        exp_mods = {n["id"] for n in exp["nodes"] if n["kind"] == "module"}
        own_found = {m for m in mods_found
                     if not m.startswith(("ext:", "svc:", "req:",
                                          "contract:", "resource:"))}
        m_rec, m_prec, m_missed, m_phantom = _score(own_found, exp_mods)

        # nodes scored by KIND — a design that carries contract:/resource:
        # nodes is not measured by counting modules alone
        by_kind = {}
        for n in exp["nodes"]:
            k = n["kind"]
            slot = by_kind.setdefault(k, [0, 0])
            slot[1] += 1
            if n["id"] in mods_found:
                slot[0] += 1

        # an edge must match from, to AND kind — a right pair with the wrong
        # kind means the graph says something different about the code
        exp_edges = [(e["from"], e["to"], e["kind"]) for e in exp["edges"]]
        e_rec, e_prec, e_missed, _ = _score(edges_kinded, exp_edges)

        print(f"  {name}")
        print(f"    modules  recall {m_rec:>5.0%}  precision {m_prec:>5.0%}"
              f"   ({len(own_found)} found / {len(exp_mods)} expected)")
        print("    nodes by kind  " + " · ".join(
            f"{k} {v[0]}/{v[1]}" for k, v in sorted(by_kind.items())))
        print(f"    edges    recall {e_rec:>5.0%}  precision {e_prec:>5.0%}"
              f"   ({len(edges_kinded)} found / {len(exp_edges)} expected)"
              "   [from+to+KIND must all match]")
        dep_exp = sum(1 for e in exp["edges"] if e["family"] == "dependency")
        dep_found = sum(1 for e in exp["edges"] if e["family"] == "dependency"
                        and (e["from"], e["to"], e["kind"]) in edges_kinded)
        print(f"    dependency-family edges (the ones that answer "
              f"'what depends on this?'): {dep_found}/{dep_exp}")
        if m_phantom:
            print(f"    phantom modules: {', '.join(m_phantom[:6])}")
        if m_missed:
            print(f"    missed modules : {', '.join(m_missed[:6])}")
        # A 0% that is NOT a blind spot: the right structure under invented
        # names. Worth separating, because the fix is completely different —
        # reading a manifest, not writing a scanner. Identity still counts as
        # a miss: `graph impact` answering "touches ui" when every manifest,
        # import and human says "@acme/ui" cannot be cross-referenced with
        # anything else, which is most of what an id is for.
        if m_rec == 0 and len(own_found) == len(exp_mods) and own_found:
            print("    NOTE: found the right NUMBER of modules under invented "
                  "names — the shape is there, the identity is not. "
                  "A manifest reader fixes this outright.")

        for e in exp["edges"]:
            diff = e.get("difficulty", "easy")
            if diff not in by_difficulty:
                by_difficulty[diff] = [0, 0]
            by_difficulty[diff][1] += 1
            if (e["from"], e["to"]) in edges_found:
                by_difficulty[diff][0] += 1
        print(f"    why this profile: {exp['why']}")
        print()

    print("  edge recall BY DIFFICULTY — where the accuracy actually goes")
    for diff in DIFFICULTY_ORDER:
        found, total = by_difficulty.get(diff, [0, 0])
        if not total:
            continue
        pct = found / total
        note = {
            "easy": "same-language imports the scanner should already get",
            "manifest": "declared in a build manifest — free, currently unread",
            "declared": "stated in the repo's own structured data — free, unread",
            "cross-runtime": "not statically derivable; record, do not scan",
            "not-derivable": "endpoints unrecoverable from the repo; a "
                             "scanner producing these would be guessing",
        }[diff]
        print(f"    {diff:<14} {found}/{total} ({pct:>4.0%})   {note}")

    print("\n  This gates nothing. It is a baseline: change a scanner and these "
          "numbers move.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
