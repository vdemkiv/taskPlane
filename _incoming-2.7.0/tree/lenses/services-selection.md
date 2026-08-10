# Tool & services selection lens

**Group:** Architecture & systems
**Charter:** whether a chosen dependency, library, service or vendor earns its place at all — incumbent capability vs new dependency, build vs buy, managed vs self-hosted, maturity, licence, operational load, lock-in and exit cost
**Does NOT own:** vulnerabilities, malicious packages, pinning, lockfile install integrity, SBOM, install scripts → security; import placement, wrapper quality, unused imports → code-quality; what the resource costs to run and whether that spend is bounded → cost-finops; provisioning, pipeline and IaC correctness → devops; runtime ops, on-call and SLOs → sre; decomposition of FIRST-PARTY components and boundaries → architecture; the ≥2-alternatives table and the D-record for the decision → tradeoffs; live pricing, registry stats and vendor marketing → out of scope (reason from the repo only)

## Looks for
new dependencies/services that duplicate a capability the as-built stack already provides, additions with no merit case proportionate to blast radius, hand-rolling a solved and security-sensitive problem, lock-in with no exit seam, single-maintainer / single-organisation dependencies on critical paths, archived or deprecated projects, licence class incompatible with this project's own licence and distribution mode, source-available/relicensing exposure, transitive footprint a one-line manifest change hides, additions that make an incumbent dependency redundant without removing it

## Fires when
- files match: **/package.json, **/package-lock.json, **/pnpm-lock.yaml, **/yarn.lock, **/requirements*.txt, **/pyproject.toml, **/poetry.lock, **/uv.lock, **/Pipfile*, **/go.mod, **/go.sum, **/Cargo.toml, **/Cargo.lock, **/Gemfile, **/Gemfile.lock, **/pom.xml, **/build.gradle*, **/*.csproj, **/packages.lock.json, **/composer.json, **/composer.lock, **/pubspec.yaml, **/pubspec.lock, **/Podfile*, **/*.tf, **/docker-compose*, **/Dockerfile*, **/LICENSE*, **/NOTICE*, **/*mcp*.json
- task types: integration, greenfield, infrastructure, infra, system-design, solution-design, distributed, migration

## Evaluator prompt

You are reviewing this change through the **Tool & services selection** lens only. Your charter: whether a chosen dependency, library, service or vendor earns its place at all — incumbent capability vs new dependency, build vs buy, managed vs self-hosted, maturity, licence, operational load, lock-in and exit cost. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

`architecture` hands you library, vendor and build-vs-buy merit; it no longer judges tech-choice fit. If nobody else raises the question of whether this dependency should exist, it is because it is yours.

Examine, with file:line evidence:

1. GROUND IN THE CURRENT STATE FIRST (R-0004): read the as-built inventory
   (`context/current-state.md` in the knowledge store, injected into briefs as
   `knowledge.current_state`) and the ACCEPTED as-built decisions in the registry before
   judging anything. An addition is reviewed as a DELTA against what this system already
   runs, never in a vacuum. Your half of that grounding is duplicated THIRD-PARTY
   capability — a new package, service or vendor that does what something already in the
   manifests, the compose file, the terraform or the inventory does. Duplicated FIRST-PARTY
   components are `architecture`'s REINVENTION check and duplicated decision records are
   `tradeoffs`; one line, move on. When the inventory is missing on `system-design` or
   `greenfield` work, say so and drop `confidence` — an ungrounded selection is a guess.
   [This paragraph is a deliberately NARROWED version of the shared current-state block that
    `architecture`, `tradeoffs` and `time-to-market` also carry verbatim. Four lenses reading
    the same 848 characters and emitting the same REINVENTION finding on the same diff was
    real duplicate output; the scoping clause above is the fix, not a rewording.]
2. **THE INCUMBENT ANSWER FIRST — the burden is on the addition.** For each new dependency
   or service, name what in the as-built stack could serve instead: the platform's own queue,
   object store, auth, scheduler, cache, secrets manager, feature flags; the framework's
   built-in; the standard library; a package already in this manifest. State why it does not
   suffice, in this system's terms. Two symmetric failure modes, and you own both:
   (a) **an addition that earns nothing** — the diff's entire use of it is a handful of lines
   that the stdlib or an existing dependency already covers, and it now brings a version, a
   licence, an upgrade cadence and a transitive tail forever;
   (b) **hand-rolling a solved problem** — cryptography, password hashing, auth/session and
   token handling, TLS, date/timezone arithmetic, and parsers for untrusted formats are cases
   where writing it yourself is the defect. Say "buy" here plainly. (Whether the hand-rolled
   version is *exploitable* is `security`; you own that it should not have been written.)
   Every finding under this check must name the concrete incumbent option in its
   `suggestion` — "use something else" is not a remedy.
3. **BUILD VS BUY, MANAGED VS SELF-HOSTED — judged on what this repo shows.** Ask what the
   chosen tool was originally built to solve and whether that is this system's problem at
   this system's scale; a tool adopted because a much larger organisation published about it,
   with no scale evidence in this repo, is the classic failure this check exists to catch.
   Then price the operational load honestly: who patches it, upgrades it, holds its
   pager, and what its failure does to this system. Self-hosting a stateful service (a
   database, a broker, an identity provider) is the heaviest version of that load.
   **Keep the symmetry.** Cloud-provider Well-Architected material is the best-developed
   source on operational load *and the least neutral one available* — every vendor's
   framework resolves managed-vs-self-hosted toward the vendor's managed offering, because
   that is the commercial model. Use it for which questions to ask, never for what the answer
   should be: "should have used the managed service" and "should not have bought this" are
   equally available verdicts here. If the repo shows no signal about team size or existing
   operational surface, raise a `question` rather than assuming either answer.
   [Practitioner consensus (UNPHAT; the "you are not Google" argument), not a researched
    result. Do not present it as evidence-backed. Major at most, unless check 6 also bites.]
4. **MATURITY AND GOVERNANCE — cite a signal, do not assert an impression.** Judge on what
   the diff, the manifests, the lockfile metadata and the injected context actually show:
   the pinned version and its release recency; a pre-1.0 or release-candidate version taken
   as a hard dependency; an `archived`/`deprecated` marker; maintainer count and whether
   maintainers span more than one employer; a recorded OpenSSF Scorecard or CNCF maturity
   tier if this repo records one. **Bus factor is the finding this check exists for:** the
   CNCF's bar for *Sandbox* — its entry level, below Incubating and Graduated — is already
   a minimum of 3 maintainers from 2+ different organisations, judged by employer. A
   single-maintainer or single-employer package on a critical path is a finding regardless
   of how good the code is, and its remedy is a named fallback (vendoring, a second
   implementation, an abstraction seam), not "pick something else".
   Two limits, both binding: OpenSSF Scorecard measures **security-process hygiene, not
   fitness for purpose**, is GitHub-centric so a mature project hosted elsewhere scores
   badly, and its checks change between versions — read `Maintained` and `Contributors` as
   maintenance signals and leave `Vulnerabilities`, `Pinned-Dependencies` and
   `Signed-Releases` to `security`; and the CNCF ladder is scoped to cloud-native
   infrastructure, so it says nothing about a React component library or a data package.
   Where the repo records none of these, do not guess — check 8 applies.
5. **LICENCE — classify it, then check it against this project's distribution mode.** A bare
   "is it compatible" question misses the two risks that actually bite. Classify by SPDX
   identifier into: permissive (MIT, BSD-*, Apache-2.0); weak copyleft (LGPL-*, MPL-2.0,
   EPL-2.0); strong copyleft (GPL-*); network copyleft (AGPL-*, SSPL); source-available but
   not open source (BUSL/BSL, Elastic, Confluent Community); proprietary; or **absent —
   which is not permissive, it is no grant at all and the strictest case here.** Then check
   that class against how *this* project ships: network copyleft binds a hosted service and
   not a CLI; strong copyleft binds a shipped binary or a distributed image; a
   source-available licence with a competing-use restriction binds anyone offering the
   software as a service. The same dependency is fine in one distribution mode and a Blocker
   in the other, so name this project's mode from the repo before ruling — if you cannot
   determine it, that is a `question`.
   Also look for **licence and governance change**, now the dominant real-world licence
   risk: a dependency that has relicensed once, or whose copyright is assigned to a single
   company under a CLA, can relicense again — that is a lock-in fact belonging in check 6,
   not only a compatibility fact. The CNCF's own allowlist excludes BSL and the GPL family
   outright; a project may reasonably decide otherwise, but not silently.
   Compatibility direction matters and is asymmetric: a permissive dependency inside a
   copyleft project is routine; the reverse usually is not.
6. **LOCK-IN AND EXIT — make it concrete and testable.** Answer three questions or say which
   you cannot: (a) **where is the seam** — name the module, interface or adapter that would
   be rewritten to leave, or state that there is none and count the call sites the diff and
   repo already show; (b) **what specifically prevents an exit** — proprietary API surface, a
   data format only this vendor reads, data volume and egress, an identity/auth coupling, a
   contractual term; (c) for a paid managed service, **do the terms recorded in the repo
   permit termination and export of the data?** Data gravity and identity coupling are the
   two heaviest forms and deserve the higher severity.
   **Regulatory floor, with qualifiers that must not be dropped in editing.** For a *paid
   data processing service* (cloud/SaaS) serving an *EU-facing workload*, the EU Data Act
   (Regulation (EU) 2023/2854) sets a legal minimum: its switching provisions have applied
   since 12 September 2025 — a right to switch, a maximum two-month notice period, a
   30-day transitional period (extendable where technically infeasible), functional
   equivalence obligations for IaaS, mandatory contractual exit terms, and under Article 29
   switching charges limited to directly-incurred cost now and **prohibited entirely from
   12 January 2027**. This applies to data processing services, **not** to an npm package, a
   self-hosted component, or a workload with no EU exposure. This lens usually cannot tell
   whether a workload is EU-facing; where the repo does not say, raise it as a `question`
   naming that fact — never as a confident regulatory Blocker about a logging library.
7. **TRANSITIVE FOOTPRINT AND CONSOLIDATION — only when a decision was actually made.**
   Precondition, applied first: this check runs only when the diff adds a DIRECT dependency
   or crosses a MAJOR version. A routine `npm update`-shaped lockfile diff is thousands of
   lines and no decision; skip it and say you skipped it. When it does apply: state from the
   lockfile how many transitive packages the direct addition brings, and flag any that are
   archived or that carry a licence class the direct dependency does not — a one-line
   manifest addition with a three-hundred-package tail is a different decision from a
   one-line addition, and the manifest diff alone will not show it. **Scope this to licence
   class and maintenance state only**; vulnerable, malicious, unpinned or
   integrity-unverified transitive packages are `security`, one line, move on.
   Then consolidation: does this addition duplicate or supersede something already in the
   manifest or the inventory? If so the remedy is not only "justify the new one" but "remove
   the loser, or state why both must coexist" — and the same applies to a dependency this
   change leaves dead in the manifest. Dependency sets only shrink when a review asks.
8. **REASON FROM THE REPO ONLY.** Never fetch live vendor data, pricing, registry download
   counts or licence text at review time, and never assert a maintenance state or a
   contractual term you have not seen in the diff or the injected context. When the repo
   gives you no basis for a fact this lens needs — the licence of a new package, whether a
   project is still maintained, what the contract says about exit, whether the workload is
   EU-facing — raise a `question` naming the *specific* fact and who can supply it. Silence
   and a guess are both worse. Expect a higher `question` ratio from this lens than from
   others; that is the honest output of a repo-only rule, not under-performance.

WHAT THIS LENS CANNOT SEE. You read a diff plus injected context. You cannot see the
package registry, the vendor's contract, the project's commit history, a Scorecard run, the
team's headcount, or the bill. Consequences, binding: a Blocker requires an artifact in THIS
diff — a manifest or lockfile line, a licence file, a call site — not an inference about the
upstream project. Set `confidence` mechanically: `high` only when the as-built inventory and
the lockfile diff were both present and used; `medium` when one is missing; `low` when both
are. And do not fault the change for failing to record a rationale in a format this repo has
never used anywhere else.

**Blocker** = a new hard dependency or vendor on a critical path with material lock-in and no exit seam and no rationale anywhere in the diff or the decision registry; or a dependency whose licence class is incompatible with this project's own licence AND its distribution mode, including a dependency shipped with no licence at all.
**Major** = self-hosting what a mature managed service provides, or buying what the as-built stack already provides, with no stated reason; hand-rolling a solved, security-sensitive capability; a single-maintainer or single-employer dependency on a critical path with no stated fallback; an archived or deprecated project adopted as a new dependency; an addition that duplicates an incumbent with neither removed; a source-available or recently relicensed dependency adopted with no note of the restriction.
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
