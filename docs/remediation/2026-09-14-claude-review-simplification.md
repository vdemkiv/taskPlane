# Claude review: root cause and simplification proposal

Status: Product, Design, and Plan approved by the user; implemented for 2.24.0; local verification recorded below. Investigated source: `cf983ca0b02e2a3ef34d97d5124e2b2425a2c782` (2.23.10). The incident transcript was supplied by the user; the actual Cowork session and its usage records were not available for inspection.

## Finding

The current setup can prevent a usable native Claude session from reading its first source file. Fixing the session variable alone would unblock one failure while leaving the expensive startup and incorrect budget semantics. A fresh conversation is a workaround that discards useful context and repeats setup, not a product repair.

| Problem | Evidence | Consequence |
| --- | --- | --- |
| CLI and hooks select different stores | `storage.host_session_id()` reads `CODEX_THREAD_ID` or `CLAUDE_SESSION_ID`; `hook_session()` instead consumes the event's identity. No production reference recognizes `CLAUDE_CODE_SESSION_ID`. | A hook can enforce a contract that `contracts` and `budget` cannot find. |
| A new review inherits prior conversation spend | `tp.py` supplies cumulative `usage.total_tokens` directly to `spend.status()`. The standalone default is 1,000,000. There is no review-start subtraction on this path. | A conversation already at 3.75M is refused against a newly created 1M contract before any review work. |
| Recovery requires another native hook observation | `cmd_budget()` first loads the active contract, then requires its recorded native counter; the screen records that counter for a narrowly recognized approved grant. `_recovery_argv()` excludes environment-prefixed invocations. | The identity mismatch breaks the recovery route too. The existing grant implementation otherwise adds approved headroom above current usage; an in-session grant is not inherently impossible. |
| Normal entry requires delivery-system readiness | Common initialization applies to every direct entry, including help/status. `_onboard_report()` includes phase configuration, run bindings, launchers, trust, hook loading, settings, tools, installation guidance, model tiers, and foreign-state discovery. Review startup calls `_initialize_entry()` again. | Reading a repository depends on unrelated setup and repeated diagnostics. |
| Excess instructions and mixed host content | Engineering instructions: 389 lines / 25,105 bytes. Shared entry: 139 / 9,105. Harness rules: 78 / 4,559. Combined: 606 lines / 38,769 bytes. Shared entry includes Codex-specific instructions. | Substantial instructions are loaded before useful source review; diagnostics can add still more repeated context. These are byte counts, not measured Claude tokens. |
| Host packaging copies verbose compatibility data | The nine hooks contain 8,635 bytes of Windows command strings and 5,832 bytes of POSIX command strings; the complete manifest is 17,174 bytes. Claude packaging copies the file verbatim. | Unneeded command variants reach the Claude archive and can reach model context if dumped. Their presence does not show that Windows commands executed. |

The session mismatch was reproduced in memory with only `CLAUDE_CODE_SESSION_ID=reported-session`: ordinary storage selected the unpartitioned root, while `hook_session({session_id: reported-session})` selected a hashed session directory. The budget refusal was reproduced by passing a 3,750,000 observation to a new 1,000,000 contract. No production state was changed.

Claude Code's official [environment-variable reference](https://code.claude.com/docs/en/env-vars) documents `CLAUDE_CODE_SESSION_ID` for shell and hook subprocesses, matching hook input. Its [hook reference](https://code.claude.com/docs/en/hooks) documents native command/argument and shell selection; it does not document `commandWindows`. These are Claude Code facts, not proof of the exact installed Cowork version's capabilities. The supplied Cowork incident matches the reproduced source defect.

## Token explanation

The pasted 3.75M and 5.14M figures are reported totals, not independently extracted telemetry. Their difference is 1.39M after review opening. Taskplane's Claude normalizer distinguishes uncached input, cache reads, cache creation, and output, but this gate uses their raw total. Raw token totals are not a dollar bill, newly authored content, or proof that useful review work occurred.

The pasted estimate of twenty turns at 250k context is not a measurement. Neither “without a single wasted action” nor “your real usage is nowhere near 5M of fresh tokens” is established by that total alone. Exact attribution needs the native per-request categories. Loading long cross-host instructions, dumping the hook manifest, and repeatedly diagnosing Taskplane are identifiable avoidable work regardless of cache pricing. Cache hits do not make these steps useful.

## Product

The user asks for a source review and should receive findings supported by source locations. Accessing the selected checkout, preserving the requested revision/scope, and honestly reporting missing evidence provide value. Requiring onboarding, phase readiness, a private contract, and budget recovery before a native file read does not.

Normal Claude entry and review should use the model's native tools immediately. They must preserve the current conversation, available checkout, existing user decisions, and review scope. A long conversation must not be disqualified by work done before the review. A source review must not require tests to be rerun when applicable CI evidence already exists.

## Design

1. **Remove onboarding from the normal review, help, and status path.** Keep setup diagnostics available when explicitly requested or when a concrete required operation fails. A review needs a selected readable checkout and an explicit revision/scope; it does not need the delivery phase registry, settings questionnaire, hook readiness screen, launcher repair, or installation dashboard.
2. **Use native tool permissions and lifecycle directly.** Stop automatically creating a Taskplane read-only contract and per-tool budget wall for an ordinary source review. Keep repository scope selection, findings, and evidence validation as domain functions. Do not replace the retired screen with another command wrapper, permission parser, file reader, scheduler, or onboarding service. Remove review-only hook work that exists solely to support those deleted gates. Any hook needed by a separately requested delivery workflow must have a concrete domain purpose; it cannot become a prerequisite for ordinary review.
3. **Use native identity wherever retained state needs it.** Consolidate the existing consumers around the native session resolver. Recognize `CLAUDE_CODE_SESSION_ID`; support the existing legacy name only for compatibility. Hook and CLI identities must agree, and conflicting identities must produce a concise diagnostic. Do not require an environment-prefix workaround or a fabricated identity handoff when the host already supplies the ID.
4. **Keep budgets truthful and native.** Remove the automatic 1M conversation-total review gate. Use a native budget control when the host exposes one. Otherwise describe an explicitly requested budget as advisory rather than recreating hard enforcement in hooks. For usage reporting, retain the native categories and measure the review's delta from its observed start. Reuse the existing observation data; do not add another meter, billing estimator, grant protocol, or polling loop. Old observations and approvals remain historical evidence, never rewritten counters.
5. **Make context specific to the task and host.** Give ordinary review one short entry procedure. Load detailed delivery, recovery, and other-host instructions only when that work is actually selected. Never require the model to dump a hook manifest to discover event names. Produce Claude hook metadata only from fields supported by the declared Claude host. Prefer the host's documented direct invocation where supported; do not carry repeated Windows `cmd.exe` fallback chains into the Cowork path. Check Cowork separately before asserting Claude Code capabilities apply there.

This design deliberately deletes prerequisites and duplicated native controls. Merely increasing the default ceiling, creating a fresh conversation, adding more recovery commands, or introducing another coordinator is rejected.

## Plan

| Order | Change | Acceptance |
| --- | --- | --- |
| 1 | Reduce normal review/help/status entry; remove automatic onboarding and review-contract activation | A readable local checkout reaches its first native source read without onboarding output, contract activation, or a fresh conversation. Repository and diff scopes remain explicit. |
| 2 | Unify retained Claude session consumers | A host exporting only `CLAUDE_CODE_SESSION_ID` gives hooks and ordinary CLI calls the same session/store. Legacy-name compatibility, conflicting identities, foreign sessions, and resume are covered. No new identity service. |
| 3 | Remove the automatic cumulative token gate from ordinary review and retain attributable usage | A conversation with 3.75M prior tokens can begin review. Cached input is separately reported. Prior conversation work is not charged as review work. No replacement per-read hook gate or automatic budget approval loop. |
| 4 | Prune review-only hook behavior and split host-specific instructions/package metadata | The normal review entry material is at most 8 KiB; normal readiness output, when needed, is at most 2 KiB. These are acceptance bounds on authored output, not new runtime limit machinery. Claude review loads no Codex procedure or Windows fallback strings. Native supported platforms keep only their required invocation metadata. |
| 5 | Verify the actual failure boundary and deliver | Run targeted identity, review-start, usage, and package checks. Reuse unaffected passing CI. Validate the installed Cowork journey with native ID, substantial pre-existing usage, a clean whole-repository scope, and source reads. Also test explicit diagnostics and preservation of older state. Record actual host results separately from simulated tests. |

Existing contracts must not be silently cleared to make acceptance pass. Handle the affected historical contract through an explicit, attributable migration/recovery decision when implementation begins. The proposed default flow prevents the same trap from being created again.

All changed skill bindings must be regenerated through the existing producers, including both phase-definition content fingerprints and evaluation scenario input fingerprints. No new generic validation framework is needed.

The user approved Build after this proposal was presented. The earlier remediation remains historical evidence; its local and CI passes did not establish the failed native Cowork journey.

## Implementation and verification

The approved native source path is implemented in the existing CLI and review
module. It saves the pinned inventory and optional starting observation, without
creating a contract, kernel dispatch, dashboard obligation, or per-tool budget
wall. Help/status instructions are similarly direct; detailed delivery instructions
are conditional references. SessionStart emits no ordinary-review onboarding text.
The existing delivery workflow and explicitly activated historical contracts remain
available. This change does not clear, rewrite, or silently adopt an old contract.

Claude native session identity and its compatibility alias now share one resolver.
Native hosts need no environment-file handoff. The usage report reuses the existing
transcript projection, subtracts the review baseline, preserves cache categories,
and reports missing or mismatched observations as unavailable. Repeating the same
source selection retains the starting observation and explicit advisory limits.

Local evidence:

- Simulated native Claude events select the same session store as ordinary CLI
  commands. Conflicting identities refuse with a concise diagnostic; legacy identity
  and existing delivery resume/recovery remain covered.
- A simulated 3,750,000-token conversation starts a whole-repository review and
  reaches its first native source read without setup or contract activation. A
  subsequent 5,140,000 observation reports a 1,390,000 delta, including a separate
  cached-input delta. A second check uses the real transcript parser with synthetic
  native usage rows and preserves fresh input, cache reads, cache creation, and output.
- An extracted Claude archive starts review in a clean checkout and its packaged
  hook permits the native source read. It creates no active contract. Extracted
  package tests also cover the retained explicit delivery path on both archive layouts.
- Whole-repository and diff scope, wrong-checkout refusal, clean empty diffs,
  session isolation, and preservation of historical contract bytes are covered.
  Private untracked Taskplane output is excluded without modifying ignore rules;
  tracked changes remain in scope.
- The main entry plus engineering instructions total 4,189 bytes, below the 8 KiB
  acceptance bound. The compact Claude hook projection is 1,913 bytes; it contains
  no Windows command field or repeated launcher-discovery chain. These are byte
  measurements, not model token or billing estimates.
- Ruff and configured mypy pass (114 production files). Version consistency,
  phase bindings, evaluation scenarios, and release-surface verification pass.
  The final affected set passes 185 tests and 212 subtests; the usage/telemetry
  set passes 58 tests. Earlier affected review, identity, setup, package, and
  compatibility checks also passed after targeted corrections;
  obsolete mandatory-onboarding/contract assertions were updated to the approved
  behavior while preserving explicit-delivery tests. The unaffected prior CI run
  for `cf983ca` passed; current-commit CI is checked after push.

The actual reported Cowork session, its live environment, and its raw telemetry
were not accessible. Local simulated events and extracted-package subprocesses are
not described as live Cowork acceptance. The affected historical contract was not
cleared or migrated. Source builds do not install or publish a Marketplace plugin.

## CI follow-up

The first 2.24.0 CI run (`34902130366`, commit `5ee9526`) passed 4,412 tests
and failed two stale SessionStart assertions. Those tests still required setup
text in an empty folder and a worker sweep without an active delivery. They now
verify quiet ordinary entry and explicitly cover both inactive and active delivery
sessions. The seven other CI jobs passed. The repair changes tests only; version
2.24.0 and the native review implementation are retained.
