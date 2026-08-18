# Specification — host-parity governed PR review

## Problem

Taskplane PR review does not yet deliver one dependable governed experience on
Claude and Codex. Observed runs required repeated approvals, omitted the PR's
Definition of Ready, exposed only counts in the inline result, required a user
prompt before durable reports were produced, and handled collection failures
in ways that either required manual aggregation or suppressed valid lens work.

## Users and context

Engineers use Taskplane to review a pull request against its stated intent and
to make an informed approval decision. The same pinned PR and user choices must
produce equivalent governance, evidence, findings, and artifacts on Claude and
Codex even when their native child-agent, rendering, or approval mechanisms
differ. Recent evidence includes a Claude run that manually merged 120 findings
and produced 126 KiB Markdown and 342 KiB HTML only after prompting, and a
Codex run whose canonical collection failed because one otherwise valid
`fail`/one-finding frontend result carried an unresolved free-form declaration
identity. In that Codex run, all-or-nothing collection hid valid slot results;
its inline widget showed counts and a gate but not enough evidence to decide.

## In scope

- One host-parity governed PR-review contract from pinned target and PR
  requirements through DoR, lens dispatch, optional dynamic validation,
  collection, decision evidence, artifacts, and the human gate.
- Mandatory discovery of available requirement/DoR artifacts, including PR
  title, body/comments, commit messages, changelog, issue/spec/acceptance text,
  and repository-declared contracts, with explicit source provenance.
- Criterion-level DoR and implementation evidence in canonical results,
  dashboards, inline presentation, and lossless exports.
- Consolidated review choices and approvals, while retaining separate consent
  where authority, destructive/external effects, or materially changed scope
  genuinely require a new human decision.
- Resilient partial collection that preserves valid slot results, marks an
  incomplete revision provisional and non-approvable, and identifies every
  missing or invalid slot.
- Mechanical correction of transport/schema/provenance/declaration defects
  without repeating substantive lens reasoning when the judged evidence and
  finding content are unchanged and equivalence is verifiable.
- Decision-useful inline review evidence, including DoR status, criterion
  coverage, findings, provenance, validation state, gaps, and actions.
- Automatic, lossless, mutually consistent JSON, Markdown, and HTML artifacts
  for every published canonical or provisional findings revision.
- Large review artifacts and dashboards that do not prevent bounded lens
  dispatch, partial collection, inline pagination, or artifact generation.

## Out of scope

- Auto-approving a PR, bypassing the final human gate, or treating silence as
  approval.
- Combining consent for destructive writes, publishing/pushing, credential or
  permission escalation, external side effects, or a materially changed review
  target/scope with ordinary non-destructive review options.
- Editing or pushing the reviewed PR as part of read-only review. Dynamic
  validation may use a disposable copy only when the user authorized it.
- Weakening lens applicability, architecture/security floors, lease identity,
  producer independence, evidence provenance, schema validation, or canonical
  collector authority.
- Treating a provisional or incomplete revision as pass, approve, or final.
- Substantively rewriting a lens verdict/finding during mechanical repair, or
  claiming a rerun occurred when only metadata was repaired.
- Hard-coding one PR template, exact wording, host transcript format, or host-
  specific child-agent implementation as the only requirement source.
- Replacing the dependency graph, lens catalog, code-review policy, or general
  release/publishing workflow.

## Acceptance criteria

1. **Equivalent end-to-end outcomes on Claude and Codex.** Given the same
   pinned PR, repository state, requirements, review options, and deterministic
   lens fixtures, both hosts produce equivalent DoR records, routed lenses,
   leases, validation states, findings, collection status, gate state, and
   JSON/Markdown/HTML content. Host transport metadata may differ but cannot
   change the judgment. **Verify:** a golden cross-host scenario compares the
   canonical semantic payloads and reports no host-only missing stage.

2. **DoR sources are discovered before review routing.** Every review records
   which available sources were checked—PR title/body/comments, review-range
   commits, changelog, linked issue/spec/acceptance artifacts, and repository
   contracts—and preserves source identity and target/revision provenance.
   Absence or unreadability is explicit, never inferred as “not requested.”
   **Verify:** fixtures containing each source individually and in combination,
   plus missing, inaccessible, contradictory, and stale sources.

3. **Requirements are dynamically classified.** Requirement-like statements
   are classified into PR objective, acceptance criteria, review directives,
   constraints, and contextual notes from meaning and structure rather than an
   exact phrase match. Ambiguous or conflicting material is surfaced for one
   clarification decision before substantive dispatch. **Verify:** paraphrase,
   bullet, prose, commit-message, changelog, comment, conflicting-source, and
   non-requirement fixtures produce the expected classifications and prompt
   only when ambiguity can materially change the review.

4. **Every acceptance criterion has a verdict and evidence.** The canonical
   and inline results list every extracted criterion as `pass`, `fail`,
   `unproven`, or `not-applicable`, with rationale, evidence references,
   verification method, and responsible lens or validation step. `unproven`
   and unjustified `not-applicable` criteria prevent approval. **Verify:** a PR
   with implemented, violated, untestable, and irrelevant criteria exercises
   all statuses and the corresponding gate outcomes.

5. **Review directives influence dispatch.** User/PR directives such as
   security, bugs, usability, scale performance, quality, and architecture are
   mapped to applicable catalog lenses and recorded separately from feature
   acceptance criteria. A broad directive is not silently reduced to static
   review when executable validation exists. **Verify:** semantic variants and
   multi-directive fixtures assert routed lenses and stated disposition.

6. **Routine choices are consolidated.** Before dispatch, one review decision
   presents all currently knowable, non-destructive choices, including dynamic
   validation and inline rendering, with consequences and safe defaults. The
   user's selection authorizes subsequent bounded steps without requiring an
   exact follow-up phrase or repeated confirmation. **Verify:** selecting the
   complete review once reaches the final human gate without another routine
   approval; free-form equivalent consent is accepted.

7. **New approval is requested only at a real boundary.** A fresh decision is
   allowed only for a materially changed target/scope, newly discovered
   destructive or external action, permission/credential escalation, unsafe
   operation, irreconcilable requirement ambiguity, or final PR disposition.
   Each request names the new fact and authority needed. **Verify:** approval
   trace fixtures distinguish permitted boundaries from repeated render,
   dynamic, collection, repair, retry, and artifact-generation prompts.

8. **Valid results survive partial collection.** If one or more leased results
   are missing or invalid, all valid results are retained and exposed in a
   provisional revision with per-slot status and reason. The revision is
   visibly incomplete, cannot pass or enable approval, and is superseded rather
   than discarded after recovery. **Verify:** zero, one, several, and all slot
   failures assert retained valid findings, explicit gaps, stable provenance,
   non-approvable gate state, and successful later canonicalization.

9. **Mechanical defects have bounded repair.** A result whose substantive
   verdict, findings, evidence, target, lens, slot, and producer are valid but
   whose declaration identity or other mechanically derivable schema/provenance
   field is unresolved can be repaired from authoritative leased data without
   re-running the lens. Repair records before/after values, derivation,
   authority, and equivalence proof. **Verify:** the observed free-form
   frontend declaration case repairs and collects without a substantive rerun;
   changed findings, evidence, target, producer, slot, or unverifiable identity
   require affected-slot rerun and cannot be mechanically repaired.

10. **Recovery is affected-slot-only.** Missing or substantively invalid slots
    may be retried independently; already valid slots are neither sent for
    additional review nor rewritten. Collection is idempotent across retries
    and produces one canonical revision with no duplicated findings.
    **Verify:** mixed-result retry fixtures measure producer invocations and
    prove only affected slots run again.

11. **Inline evidence supports a decision.** The inline review presentation
    shows target/revision, DoR sources and overall status, expandable criterion
    verdicts/evidence, lens disposition and execution status, findings grouped
    by severity/lens/file with rationale and suggested action, dynamic
    validation evidence, provisional gaps, collection provenance, and current
    gate reason. Approve/request-changes controls are interactive only when the
    revision is complete and approvable. **Verify:** interaction tests cover
    expansion, filtering, evidence navigation, provisional disabling, keyboard
    access, and action receipts on both hosts.

12. **Artifacts are automatic and lossless.** Publication of every provisional
    or canonical findings revision automatically writes JSON, Markdown, and
    HTML without a user prompt. Each contains the complete DoR, criterion
    evidence, lens/slot status, all findings, dynamic evidence, collection
    state, provenance, gate state, and artifact identities; no format contains
    findings absent from another. **Verify:** the 120-finding fixture generates
    all three formats automatically and round-trip comparison shows identical
    semantic records and counts.

13. **Large outputs remain reviewable.** Canonical evidence and artifact sizes,
    including at least the observed 126 KiB Markdown and 342 KiB HTML sizes, do
    not cause zero-slot dispatch, collection loss, inline-render failure, or
    truncation of exported findings. Scoped producer views remain bounded and
    inline content may be paged or referenced while full artifacts remain
    lossless. **Verify:** boundary and multi-megabyte fixtures assert selected
    slots equal dispatched slots, every finding survives export, and all pages
    retain the same revision/provenance.

14. **Failures are truthful and actionable.** Host limitations, unavailable
    dynamic checks, invalid references, renderer failures, artifact-write
    failures, and unrepaired slots are named with affected evidence, gate
    impact, and safe recovery. They are not reported as zero findings, declined
    user choice, completed review, or pass. **Verify:** failure injection on
    both hosts asserts stable non-success states and no false approval path.

15. **Read-only review remains isolated.** Dynamic validation and any temporary
    build repair occur only in a disposable review copy, never push code, and
    record the original pinned target plus sandbox delta so results remain
    distinguishable from the submitted PR. **Verify:** broken-build fixtures
    assert the source checkout/ref and remote are unchanged, the repair diff is
    preserved as validation evidence, and the final review states both original
    and sandbox outcomes.

16. **Existing complete reviews remain compatible.** Small, fully valid review
    runs retain their routing, findings, gate behavior, and supported artifact
    consumers. **Verify:** existing golden reviews pass unchanged or through an
    explicit versioned migration, with no test removal, skip, xfail, lowered
    floor, or weakened governance assertion.

## Non-functional requirements

- `security`: Review authority stays bound to pinned target, immutable lease,
  observed producer, verified evidence, and human gate. Mechanical repair cannot
  invent substantive content, dynamic sandboxes cannot push, and artifacts or
  widgets cannot smuggle actions, secrets, credentials, or path escapes.
- `architecture`: Claude and Codex use one canonical DoR, routing, lease,
  result, repair, collection, artifact, and gate contract; host adapters provide
  transport only and cannot create parallel review truth.
- `data-safety`: Valid slot results and findings are never discarded by another
  slot's failure. Provisional and canonical revisions are immutable,
  supersession is explicit, artifact generation is lossless, and repair has an
  auditable before/after record.
- `sre`: Every stage is resumable and idempotent with bounded retries, named
  partial/unavailable/failure states, affected-slot recovery, and no infinite
  approval, repair, collection, or render loop.
- `integrability`: Review, DoR, criterion-evidence, repair, collection, widget,
  and artifact schemas are versioned and host-neutral; existing consumers have
  an explicit compatible migration path.
- `privacy-compliance`: Inline views, diagnostics, and artifacts minimize
  personal data and redact secrets, credentials, unnecessary absolute paths,
  and unrelated host transcript content without destroying audit identity.
- `accessibility`: Inline evidence and gates are keyboard operable, expose
  semantic states and labels, preserve focus through expansion/pagination, and
  do not rely on color alone for severity or approval availability.
- `scalability`: Many lenses, large diffs, hundreds of findings, and large
  dashboards keep producer inputs bounded, avoid per-lens duplication of full
  evidence, and preserve complete exports without preventing dispatch.
- `cost-finops`: Consolidated approvals and affected-slot-only recovery avoid
  redundant model turns and substantive reruns; telemetry distinguishes
  initial lens work, mechanical repair, and retry cost.

## Contract handoff

- `scope_paths`:
  - `taskplane/review.py`
  - `taskplane/review_evidence.py`
  - `taskplane/evidence.py`
  - `taskplane/runtime_eval.py`
  - `taskplane/dashboard.py`
  - `taskplane/loop.py`
  - `taskplane/command_runtime.py`
  - `taskplane/command_adapters.py`
  - `workflows/**`
  - `skills/**`
  - `agents/**`
  - `lenses/**`
  - `taskplane/tests/**`
  - `docs/**`
  - `specs/spec.md`
- `out_of_scope`:
  - automatic PR approval or bypass of the final human gate;
  - destructive/external actions without distinct authority;
  - edits or pushes to the reviewed PR;
  - weakening lens, lease, provenance, schema, or collector controls;
  - dependency-graph and lens-catalog redesign;
  - host-specific parallel sources of truth;
  - unrelated release, marketplace, and product behavior.
- `dod.test_command`: `python3 -m pytest taskplane/tests -q`
- dependencies: none.
- contracts:
  - `contract:review-kernel-slot`
  - `contract:review-kernel-partial-revision`
  - `contract:review-kernel-mechanical-repair`
  - `contract:review-dor-evidence`
  - `contract:review-host-adapter`
  - `contract:review-human-consent`
  - `contract:review-inline-presentation`
  - `contract:review-artifact-set`

This is a cross-host, cross-module, security-sensitive protocol change with
new externally consumed schemas and failure states. It requires Design before
Build. There are no blocking Product questions.
