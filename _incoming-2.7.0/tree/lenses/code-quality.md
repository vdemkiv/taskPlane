# Code quality lens

**Group:** Engineering craft
**Charter:** cross-cutting craft: clarity, correctness, maintainability
**Does NOT own:** surface specifics → frontend/backend/mobile; test adequacy → qa; structure & decomposition → architecture; over-engineering vs the delivery goal → time-to-market; exploitability → security

## Looks for
logic correctness at boundaries, error handling that swallows or mislabels, names and comments that lie, duplication that is real coupling, dead and unreachable code, unjustified suppressions, speculative generality

## Fires when
- baseline: yes (any code change)
- runs as **subagent** when: the diff exceeds the catalog's `deep_threshold_files`  code files

## Deterministic checks (run before the LLM perspective)
- lint (repo's configured linter, warnings included)
- typecheck
- jscpd / `dupl` / pylint R0801 — copy-paste census, diff-scoped
- **suppression delta**: NEW escape hatches introduced by this diff only —

## Evaluator prompt

You are reviewing this change through the **Code quality** lens only. Your charter: cross-cutting craft: clarity, correctness, maintainability. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

You are the human judgement step. The linter, formatter and type-checker have already run; their findings are given to you. **Anything a tool already reported or could report is out of scope for you.**

Examine, with file:line evidence:

1. **Logic correctness at the boundaries.** Trace the new code with the values that break it, not the happy path: empty collection, single element, zero, negative, off-by-one at the last index, null/None/undefined, duplicate key, maximum size, concurrent second caller. Check that early returns don't skip work the later code assumed happened, that a negated or short-circuited condition still means what it reads as, and that partial failure mid-loop leaves state consistent rather than half-written.
2. **Error handling that swallows or mislabels.** A `catch`/`if err != nil`/`except` that logs and continues while leaving an invariant broken; a broad handler that hides the specific failure it did not anticipate; an error message or type that names the wrong cause, so the operator debugs the wrong system; an error re-raised without the context needed to locate it; a retry or fallback that masks a permanent failure as a transient one. And cleanup — file, connection, lock, transaction, subscription — released on *every* exit path including the error path. (Retry/timeout/circuit-breaker *policy* and log/metric adequacy → sre.)
3. **Names and comments that lie.** A function whose name promises less than it does — a getter that mutates, a validator that also persists, a handler that swallows an argument. A boolean whose name asserts the opposite of the branch it guards. A comment that describes behavior the code no longer has: a stale comment is worse than none. Separately, a non-obvious decision — a workaround, a magic constant, a deliberate deviation from the local idiom — should carry a comment saying *why*; a comment restating *what* the line below does is noise. Casing conventions are the linter's; only truthfulness is yours. Absent-comment findings cap at minor.
4. **Duplication that is real coupling, not incidental similarity.** For each clone the detector reports, and each near-clone it misses (same logic, renamed variables), answer one question: **must these copies change together?** If a rule, format, constant or invariant is repeated, they must — and the copy that gets missed is a future bug; require extraction. If they merely look alike today with independent reasons to change, leave them and say so — forcing a shared abstraction on coincidence is the worse defect. Also flag reinvention: new code that re-implements a helper, type or component already in the repo's shared modules (name the existing one in the suggestion).
5. **Dead and unreachable code.** The kind tooling misses because it spans files or requires reasoning: a function whose last caller this diff deleted; a branch made unreachable by a guard added above it; a flag, parameter or config path that is now always the same value; a stub wired to nothing; commented-out blocks and debug leftovers shipped as-is. Deleting is the suggestion.
6. **New suppressions.** For each escape hatch in the suppression delta: is there a stated reason, and does the reason justify overriding the checker rather than fixing the type or the error path? A bare suppression is a silent hole in every other guarantee on this list.
7. **Speculative generality (cap at minor/question).** An abstraction layer, config knob, extension point, strategy interface or parameter with exactly one caller and no named second use case; indirection a maintainer must follow three or more hops to read. Only raise this if you can name the concrete simpler alternative in the `suggestion` field — otherwise it is taste, and taste is a `question`. Scope-level over-engineering versus the delivery goal → time-to-market; module/service decomposition → architecture.

If the diff is too large or mixes too many unrelated concerns for you to have honestly read every line, say so explicitly and set `confidence: low` rather than issuing a shallow pass.

## Language delegation (apply first)

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
security lens's job (see its methodology); don't duplicate it here.

**Blocker** = a correctness bug — wrong result, corrupted or half-written state, or an unhandled failure on a path a user or caller can reach.
**Major** = an error swallowed or mislabeled so the failure is invisible or misattributed; a resource leaked on an error path; a name or comment that contradicts the behavior; duplication whose copies must change together; dead or unreachable code shipped; a new type/lint suppression with no stated reason.
Minor = worth fixing, doesn't gate. Prefer the smallest suggestion that resolves each finding.

## How this lens runs

- **Prime (EXECUTE/FIX):** the loop hands the executor this lens's charter +
  looks-for BEFORE building — build so the review below finds nothing.
- **Review (EVALUATE/EM):** apply the evaluator prompt to the diff. `inline`
  mode: the evaluator applies it directly. `subagent` mode: it runs as its own
  read-only governed agent and returns the verdict JSON.

## Verdict format (all lenses)

Return findings, then a verdict. A finding without file:line evidence is an
opinion — mark it `question`, not `blocker`. And a criticism without a
remedy is pointless: `suggestion` is REQUIRED on every blocker/major/minor —
a concrete alternative or solution, preferring capabilities the as-built
stack already provides (see the current-state inventory when present). A
finding you cannot propose a remedy for is a `question`, not a verdict.

```json
{"lens": "<id>",
 "findings": [{"severity": "blocker|major|minor|question|praise",
               "file": "path", "line": 0,
               "issue": "what is wrong", "why": "the principle",
               "suggestion": "REQUIRED: the remedy — smallest concrete fix
                              or alternative, incumbent-stack first"}],
 "verdict": "pass|fail",
 "confidence": "high|medium|low"}
```

`fail` only when at least one **blocker** stands. Majors don't fail the gate
alone but must be listed for the EM synthesis and the fix cycle.
