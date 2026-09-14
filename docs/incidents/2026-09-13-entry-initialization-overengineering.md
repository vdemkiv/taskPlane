# TaskPlane initialization repair: scope and execution incident

Date: 2026-09-13. Repository: taskPlane. Baseline: `41a8575` (2.23.5).

## What the user requested

The user asked for fresh, isolated reviews, proper initialization from every
TaskPlane entry point, sessions that cannot block one another, a version bump,
a marketplace ZIP, and delivery to main. They subsequently asked whether the
Design lens had actually been used and explicitly rejected overengineering.

## What I did wrong

I treated a missing initialization sequence and an incompatible read-only tool
set as a reason to build a new transport. The abandoned draft added a local MCP
server, a command adapter/registry, authenticated invocation grants, private
keys, readiness receipts, file IO, and another human-decision protocol. Each
new boundary generated more failure modes and validation work. This expanded
a repair into a second control system and changed the package's distribution
requirements without that being necessary to fix startup locking.

The draft was incomplete. Checks found source-write risks through hard links,
worker/coordinator identity gaps, missing operations, inconsistent path handling,
and insufficient approval/readiness checks. Adding more machinery to patch
those new gaps prolonged the mistake. Passing a few adapter fixtures did not
establish a working installed plugin.

I spent the user's time and tokens on that expansion. The user described the
change as 19k lines. I have not established a reliable count of production code
versus generated design/inventory text, so this report does not claim that
figure as a measured code diff. I cannot refund the consumed tokens or credits.

## Did I adopt TaskPlane's design flow?

Yes: I used the repository's `agents/tp-designer.md`,
`lenses/solution-design.md`, and `skills/tp-design/references/design-contract.md`
as design guidance for this implementation. I used designer and independent
review agents and produced a narrative, a contract, lens dispositions, and an
operation inventory. The adapter proposal and its expanded acceptance criteria
were my engineering choices; those documents did not require this architecture.

The sequence matters. Implementation had already started before the user asked
whether the Design lens was used. I then acknowledged that the earlier
architecture check was not that lens, paused implementation, and performed a
source-guided design exercise before continuing the adapter. I must not rewrite
that history as “design was completed before any code.”

TaskPlane had been uninstalled. This was not an initialized, installed TaskPlane
review run. There was no TaskPlane engine gate, live-host attestation, or engine
approval for the design. The independent design assessment was an assessment
of documents; it did not prove the proposed implementation worked. I adopted
the product's methodology manually and overstretched it instead of using it to
bound a small fix. Repository guidance was useful context, not new user scope
or permission to invent more infrastructure.

## Correction

The adapter draft was removed from the working tree and was never committed
or pushed. A temporary backup was retained outside the repository; it is not
release content. Unrelated user files were preserved.

The replacement uses existing onboarding, launcher installation, session
storage, contract screening, and package builders. Every skill points to the
same initialization sequence. Initialization repairs missing setup and leaves
existing runs intact. A small session-local compatibility declaration lets
activation reject a read-only contract whose required file tools are missing.
That declaration grants no permissions and is not called host proof. Existing
inspection/recovery remains reachable during missing budget telemetry.

This fixes failed startup and unexpected tool locking; it does not manufacture
native file tools on a host that lacks them. Such a host receives an explicit
unsupported-tool result before a restrictive contract is activated. The
existing source-write protection remains in force.

Future repairs should begin with a short failure description, reuse the
existing owner, and add only the change and regression checks needed to resolve
that failure. A new transport or approval system is a separate feature decision,
not an incidental implementation detail. Documented design approval is never
a substitute for implementation evidence.
