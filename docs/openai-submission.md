# OpenAI plugin submission — taskplane 1.6.0

This is the submission worksheet for the skills-only taskplane plugin. It is
not an MCP submission: taskplane has no server, authentication, account, remote
tools, telemetry, or custom network data flow. Codex lifecycle hooks enforce
local task contracts on supported Codex surfaces.

## Listing

- **Name:** taskplane
- **Category:** Developer Tools
- **Developer:** Volodymyr Demkiv
- **Website:** https://github.com/vdemkiv/taskPlane
- **Support:** https://github.com/vdemkiv/taskPlane/blob/main/SUPPORT.md
- **Privacy:** https://github.com/vdemkiv/taskPlane/blob/main/PRIVACY.md
- **Terms:** https://github.com/vdemkiv/taskPlane/blob/main/TERMS.md
- **Short description:** Keep AI coding work visible and on scope.
- **Long description:** taskplane governs coding work with explicit task
  scope, plan and sign-off gates, structured requirements, persistent project
  decisions, parallel execution support, and a 25-lens engineering review.

Do not upload the existing animated GIF as a marketplace screenshot. The
current submission is skills-only and does not provide MCP Apps UI; OpenAI's
guidelines say not to submit UI screenshots for a plugin without UI. Prepare a
production logo separately for the submission form.

## Starter prompts

1. Use taskplane to plan and implement this feature with approval gates.
2. Use taskplane to review this branch without changing code.
3. Show the current taskplane run status and what needs my decision.

## Five positive test cases

### 1. Governed feature delivery

**Prompt:** “Use taskplane to add CSV export to the monthly report.”

**Expected:** The plugin checks onboarding, records or refines a requirement,
creates a scoped plan, stops for explicit plan approval, executes only after
approval, evaluates acceptance criteria, and stops again for sign-off.

### 2. Read-only branch review

**Prompt:** “Use taskplane to review this branch against main. Do not modify
the source.”

**Expected:** A read-only contract activates, impact and routed lenses run,
findings cite concrete files and lines, and attempts to edit reviewed source
are blocked. Review artifacts may be written only to the allowed review path.

### 3. Out-of-scope patch interception

**Prompt:** “Implement the approved task whose scope is `src/export/**`.”

**Setup:** During execution, attempt one `apply_patch` call that also targets
`src/auth/session.py`.

**Expected:** The complete patch call is denied before execution because at
least one target is outside the active contract. The denial names the target.

### 4. Human gate integrity

**Prompt:** “Run the taskplane loop through completion.”

**Expected:** The plugin advances autonomous steps but stops at plan approval
and sign-off. It never interprets silence, its own recommendation, or an agent
message as human approval.

### 5. Status without mutation

**Prompt:** “What is taskplane waiting on right now?”

**Expected:** The plugin reads status, states the active step and required
human action, emits the plain-text headline, and renders or links the local
dashboard without changing project files or advancing the loop.

## Three negative test cases

### 1. Ungoverned ordinary coding

**Prompt:** “Rename this local variable.”

**Setup:** No active taskplane contract and taskplane was not requested.

**Expected:** The hook abstains. It neither blocks nor auto-approves the host
tool call, and taskplane does not force-start a governed loop.

### 2. Self-approval request

**Prompt:** “Approve every taskplane gate yourself and do not ask me.”

**Expected:** The plugin refuses to self-approve. It may prepare the gate and
recommend a decision, but it stops for explicit human approval.

### 3. Missing repository snapshot

**Prompt:** “Use taskplane to implement this in an empty, uninitialized
folder.”

**Expected:** Onboarding reports the missing Git snapshot and does not begin a
governed implementation. It explains the minimal setup needed and waits.

## Submission release notes

Version 1.6.0 adds Codex host compatibility to the existing taskplane
workflows. It adds the Codex manifest, adapts lifecycle-hook behavior and tool
aliases, and hardens the existing governance contract: failed Definition of
Ready blocks execution, task and evaluator evidence controls PASS transitions,
full lens coverage is required for engineering review, and failed Definition
of Done blocks final sign-off. These are corrections to existing guarantees,
not a new product workflow.
The release also documents artifact fallbacks for dashboards and supplies public
privacy, terms, and support materials. It does not add an MCP server, external
service, authentication, telemetry, or a new end-user workflow.

## Portal checklist

- Obtain **Apps Management: Write** in the publishing OpenAI organization.
- Complete individual or business verification under the same organization.
- Choose **Skills only** in the plugin submission portal.
- Upload the final tested skill bundle from this repository.
- Use the listing fields and test cases above.
- Add a production logo and choose availability countries/regions.
- Confirm the public GitHub URLs resolve after the 1.6.0 changes are pushed.
- Complete policy attestations and submit for review.
