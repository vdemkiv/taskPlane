# OpenAI plugin submission — taskplane 2.0.0

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
- **Short description:** Govern AI coding with proof
- **Long description:** taskplane helps heavy AI coding users build and review
  robust features. Definition of Ready blocks under-specified work; scoped
  contracts constrain execution; evidence-backed Definition of Done blocks
  unproven completion; and a full 25-lens engineering review exposes
  architecture, security, operability, data, UX, and other technical
  consequences for technical and nontechnical decision-makers.
- **Logo:** `assets/taskplane-logo.svg`
- **Composer icon:** `assets/taskplane-icon.svg`

Do not upload the existing animated GIF as a marketplace screenshot. The
current submission is skills-only and does not provide MCP Apps UI; OpenAI's
guidelines say not to submit UI screenshots for a plugin without UI. The two
square production brand assets above are bundled in the upload ZIP and declared
in `.codex-plugin/plugin.json`.

## Build the upload

From the repository root:

```bash
python3 scripts/package_openai.py
```

This writes:

- `dist/taskplane-2.0.0-openai.zip` — upload this in the **Skills only** flow.
- `dist/taskplane-2.0.0-openai.zip.sha256` — checksum for release provenance.

The ZIP is deterministic, has one top-level `taskplane/` directory, and
contains the Codex manifest, all seven Codex-relevant skills and their
references, the local Python runtime, lifecycle hooks, agent roles, lens
catalog, operating disciplines, runtime specifications, brand assets, and legal
files. It deliberately excludes Claude manifests, MCP/app configuration, the
Claude Tag-only skill, tests, non-runtime design documents, repository
metadata, generated local state, and the animated documentation GIF. The
builder validates the public directory field limits, skill front matter, asset
dimensions, skills-only constraints, archive safety, and OpenAI's documented
size and entry limits before reporting success.

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

Version 2.0.0 is taskplane's first OpenAI marketplace submission and its first
fully multi-host release. The skills-only package brings the same governed loop
to Codex and Claude: Definition of Ready blocks under-specified work, scoped
contracts constrain execution, evidence-backed Definition of Done blocks
unproven completion, and 25 review lenses surface technical consequences.
Durable gate artifacts let another session or teammate resume from approved
plans, findings, graph context, retrospectives, and progress headlines. The
dependency graph now shapes execution and review: hub changes escalate
architecture scrutiny, builders receive blast radius before editing, and
reviewers are prompted to record runtime edges the import scanner cannot see.
It does not add an MCP server, external service, authentication, or telemetry.

## Portal checklist

- Obtain **Apps Management: Write** in the publishing OpenAI organization.
- Complete individual or business verification under the same organization.
- Choose **Skills only** in the plugin submission portal.
- Run `python3 scripts/package_openai.py` and upload
  `dist/taskplane-2.0.0-openai.zip`.
- Use the listing fields and test cases above.
- Confirm the bundled logo and composer icon render correctly, then choose
  availability countries/regions.
- Confirm the public GitHub URLs resolve after the 2.0.0 changes are pushed.
- Complete policy attestations and submit for review.
