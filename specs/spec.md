# Specification — stage-isolated delivery entities and bounded artifact handoffs

## Problem

Taskplane currently treats a delivery loop too much like one continuously
evolving execution context. That makes stage completion, non-build termination,
splitting, retry, audit, and long-running histories vulnerable to context bleed,
mutable predecessor state, and successor startup costs that grow with irrelevant
runtime history.

Each Product, Design, Build, Review, Evaluation, and other delivery stage needs
an independently addressable lifecycle. Successors should start from a bounded,
versioned artifact handoff rather than inheriting agents, conversations, event
logs, tool transcripts, leases, or runtime state from predecessor execution.

## Users and context

Engineers, reviewers, product owners, orchestrators, and auditors need to know
what stage is active, how it relates to prior work, why a stage ended, which
artifacts were authorized for reuse, and whether a split or retry preserved
history. The requirement extends the governed-delivery foundation in `R-0003`
without changing its enforcement, collision-isolation, ReviewKernel evidence,
or worktree-cleanup decisions.

## In scope

- Stable stage entities for Product, Design, Build, Review, Evaluation, and
  other governed stage kinds.
- Explicit lineage, requirement/design revisions, bounded inputs, independent
  execution trees, and exactly one terminal outcome per stage entity.
- Terminal `done`, `closed`, and `discarded` semantics with evidence or
  attributable reasons and no silent reactivation.
- Versioned handoff manifests and explicitly selected content-addressed
  artifacts as the only successor context from predecessor stages.
- Non-build terminalization without an implementation stage.
- Parent-to-children split semantics with explicit artifact subsets,
  dependencies, budgets, and isolated child lifecycles.
- An active-stage pointer that is only a projection over immutable stage
  history and lineage.
- Atomic, idempotent terminalization, handoff, split, resume, reconnect, and
  retry behavior.
- Bounded status, dashboard, review, sign-off, and Retro projections.
- Successor startup whose payload, work, and token demand do not scale with
  irrelevant predecessor execution history.
- Lossless migration from singleton loop records, preserving ambiguity as an
  explicit unknown state rather than guessing.

## Out of scope

- Reusing or transferring live agents, conversations, event logs, tool
  transcripts, leases, process state, or mutable worktrees between stages.
- Reopening or rewriting a terminal stage, changing a predecessor outcome, or
  allowing one child to mutate parent or sibling history.
- Automatically creating Build when Product, Design, Review, Evaluation, or
  another non-build stage closes or is discarded.
- Replacing content-addressed artifacts with unversioned paths, implicit latest
  state, whole predecessor directories, or model-selected hidden context.
- Changing `R-0003` enforcement, collision screening, ReviewKernel provenance,
  safe worktree-cleanup eligibility, final human sign-off, or orchestrator-only
  gate authority.
- A new distributed scheduler, remote artifact service, source-control model,
  dependency-graph redesign, lens-catalog redesign, or release publication.
- Guessing terminal outcomes or lineage during migration when legacy evidence
  is missing or contradictory.
- Loading predecessor execution trees merely to render status, dashboards,
  review, sign-off, or Retro.

## Functional requirements

1. Every governed stage is a stable entity with its own id, stage kind,
   requirement revision, predecessor or parent links, bounded input manifest,
   independent execution tree, and one terminal lifecycle outcome.
2. Terminal outcomes distinguish completed deliverables (`done`), attributable
   no-further-work decisions (`closed`), and attributable non-consumable results
   (`discarded`); terminal entities cannot silently become active again.
3. A successor always creates a new execution tree and receives only a
   versioned manifest plus explicitly selected content-addressed artifacts.
4. Every handoff identifies the producer and its outcome, requirement/design
   revisions, target/commit when applicable, contracts, deliverables, evidence,
   artifact fingerprints, exclusions, and continuation authority.
5. Non-build work may end closed or discarded without creating an implementation
   stage while retaining addressable audit artifacts.
6. Splitting terminalizes the parent with an attributable split reason and
   creates independently addressable children with bounded artifact subsets,
   dependencies, budgets, and lifecycles.
7. The active-stage pointer remains a replaceable projection; lifecycle actions
   append or transition the addressed entity without reclassifying history.
8. Terminalization, handoff creation, split creation, reconnect, duplicate event,
   crash recovery, and retry are atomic and idempotent.
9. Human and machine status surfaces render current stage, predecessor outcome,
   handoff, and child lineage from bounded summaries alone.
10. Default successor startup remains bounded and invariant as irrelevant
    predecessor history grows by orders of magnitude.
11. Legacy singleton loop records migrate without losing governed records or
    inventing lifecycle facts that the evidence cannot prove.

## Acceptance criteria

1. Every stage entity has a stable stage id, requirement revision, stage kind, parent or predecessor links, bounded input manifest, independent execution tree, and exactly one terminal outcome of done, closed, or discarded.
2. Done requires the declared deliverables and completion evidence; closed requires an attributable reason explaining why no further work is required; discarded requires an attributable reason explaining why its results must not be consumed. No terminal entity can silently return to active.
3. Starting a next stage creates a new execution tree and consumes only a versioned manifest plus explicitly selected content-addressed artifacts from predecessor stages; prior agents, conversations, event logs, tool transcripts, leases, and runtime state are not inherited as context.
4. A handoff manifest records producer stage id and outcome, requirement and design revisions, target and commit identity where applicable, contracts, deliverables, evidence references, artifact fingerprints, exclusions, and the actor and time authorizing continuation.
5. Product, Design, Review, Evaluation, and other non-build work can terminate closed or discarded without creating an implementation stage, while their retained artifacts remain addressable for audit or later explicit reuse.
6. Splitting a deliverable closes the parent with a split reason and creates two or more independently addressable child stage entities with explicit artifact subsets, dependencies, budgets, and lifecycles; one child outcome cannot mutate sibling or parent history.
7. The active-stage pointer is a replaceable projection only. Starting, resuming, splitting, or terminalizing a stage never overwrites or reclassifies a predecessor entity, and history lists every terminal and active entity with lineage.
8. Crash, duplicate event, reconnect, and retry fixtures prove stage terminalization, handoff creation, and split creation are atomic and idempotent, with no duplicate child, lost artifact, reopened terminal stage, or ambiguous active pointer.
9. Dashboard, status, review, sign-off, and Retro show the current stage, predecessor outcome, artifact handoff, and child lineage from bounded summaries without loading predecessor execution trees.
10. As irrelevant predecessor history grows from ten to one hundred thousand events, the default successor startup payload remains byte-identical and bounded to the manifest plus explicitly selected artifacts; startup work and token use do not scale with predecessor runtime history.
11. A migration converts singleton loop records into stage entities without losing requirements, tasks, decisions, evidence, commits, reviews, or audit history; ambiguous legacy state is preserved with an explicit unknown reason rather than guessed as pending, done, closed, or discarded.

## Non-functional requirements

- `security`: Stage identity, revisions, lineage, handoff authorization, artifact
  selection, and fingerprints are authenticated and least-privilege; successors
  cannot inherit undeclared runtime context, consume discarded results, rewrite
  terminal history, or bypass existing orchestrator and final-signoff authority.
- `architecture`: One canonical stage-entity lifecycle, artifact-handoff
  manifest, and delivery-lineage model serve every stage kind and projection;
  the active pointer, dashboards, adapters, and legacy readers never become
  competing sources of truth.
- `data-safety`: Terminal outcomes, lineage, artifacts, exclusions, evidence,
  splits, budgets, decisions, commits, reviews, and audits are immutable or
  append-only as appropriate and survive crashes, retries, migration, closure,
  and discard without loss, duplication, or silent reinterpretation.
- `sre`: Lifecycle transitions, handoff creation, split creation, retries,
  reconnects, and migration are atomic, idempotent, crash-recoverable, bounded,
  and observable; a terminal entity never reopens through replay.
- `dba`: Persisted entity, lineage, pointer, manifest, and migration schemas are
  versioned and indexed for direct current-stage and lineage lookup; migration
  is resumable and preserves explicit unknowns instead of lossy coercion.
- `integrability`: Versioned stage, handoff, and lineage contracts remain
  semantically portable across Product, Design, Build, Review, Evaluation,
  status, dashboard, sign-off, Retro, supported hosts, and legacy readers.
- `scalability`: Successor startup and default projections read only bounded
  manifests, summaries, and selected artifacts; payload bytes, startup work,
  and token use remain independent of predecessor event-history size through at
  least one hundred thousand irrelevant events.
- `cost-finops`: Stage transitions do not reload predecessor conversations,
  logs, tools, leases, or execution trees; retained audit history does not add
  model tokens or repeated runtime work to normal successor startup.
- `privacy-compliance`: Handoffs include only explicitly declared artifacts and
  minimum attributable metadata; conversations, tool transcripts, unrelated
  logs, secrets, credentials, and personal data do not cross stage boundaries
  unless a separately authorized artifact contract requires them.
- `accessibility`: Dashboard, status, review, sign-off, and Retro expose current
  stage, predecessor outcome, handoff, split lineage, and unknown migration
  states with semantic labels, readable text independent of color, keyboard-
  accessible navigation, and complete machine/Markdown equivalents.

## Contract handoff

- `scope_paths`:
  - `taskplane/loop.py`
  - `taskplane/track.py`
  - `taskplane/run_store.py`
  - `taskplane/storage.py`
  - `taskplane/retro.py`
  - `taskplane/dashboard.py`
  - `taskplane/taskplane_lite.py`
  - `taskplane/tp.py`
  - `taskplane/tests/**`
  - `docs/**`
  - `skills/**`
- `out_of_scope`: live runtime-context inheritance, terminal-stage reopening or
  rewriting, implicit Build creation, unversioned/implicit artifact transfer,
  R-0003 governance changes, new scheduler/artifact/source-control systems,
  graph/lens redesign, guessed migration outcomes, and publication.
- `dod.test_command`: `python3 -m pytest -q taskplane/tests`
- dependency:
  - `R-0003`
- contracts:
  - `contract:stage-entity-lifecycle`
  - `contract:stage-artifact-handoff`
  - `contract:delivery-lineage`
  - `contract:consolidated-authorization`
  - `contract:automatic-recovery`
  - `contract:review-evidence-binding`
- `contract_relations`:
  - provides `contract:stage-entity-lifecycle`
  - provides `contract:stage-artifact-handoff`
  - provides `contract:delivery-lineage`
  - changes `contract:consolidated-authorization`
  - changes `contract:automatic-recovery`
  - consumes `contract:review-evidence-binding`

This is a material cross-stage lifecycle and artifact-boundary change. It
requires Design before Plan or Build, with no blocking Product questions.
