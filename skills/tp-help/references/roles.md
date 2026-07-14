
# Orchestrate governed roles over the taskplane harness

Run work as a short pipeline of role-agents, each bounded by a taskplane
contract. taskplane is the **core harness**: it decides what each role may
touch (scope + tool allowlist, enforced by the PreToolUse hook), whether a
task is safe to start (Definition of Ready), and whether it is actually done
(Definition of Done). Roles supply the expertise; the harness supplies the
guardrails.

`PLUGIN=${CLAUDE_PLUGIN_ROOT}`. Run every command from the task's working
directory.

## The default pipeline

```
Product Manager ─▶ (defines the contract: scope + acceptance criteria = DoR/DoD)
      │
      ▼
   Executor      ─▶ (does the work under that contract; hook blocks out-of-scope)
      │
      ▼
EM code review   ─▶ (read-only DoR + DoD review of the in-scope diff)
      │
      ▼
   Human          ─▶ (owns the done/not-done decision)
```

### 1 — Product Manager defines the contract (Definition of Ready)

Invoke the **product-manager** agent to turn the goal into a scoped spec with
testable acceptance criteria. Its "contract handoff" gives you the exact
`scope_paths`, `out_of_scope`, and `dod.test_command`. Activate the contract
from it:

```bash
python3 "$PLUGIN/taskplane/tp.py" new \
    --scope "<from PM: scope_paths>" \
    --tests "<from PM: dod.test_command>" \
    "<the goal>"
python3 "$PLUGIN/taskplane/tp.py" ready      # DoR entry gate — must pass
```

Do not proceed past a NOT READY verdict.

### 2 — Executor builds under the contract

Do the implementation normally. The PreToolUse hook enforces the PM's scope:
any write outside it, any denied command, any disallowed tool is blocked
before it runs. If a block is legitimate, the scope was wrong — go back to the
PM/contract, widen deliberately, and note why.

### 3 — Engineering Manager reviews (Definition of Done)

Invoke the **em-code-reviewer** agent (read-only) against the in-scope diff.
It reports engineering-quality (DoR) findings and requirements coverage (DoD),
with a recommendation. Then run the mechanical gate:

```bash
python3 "$PLUGIN/taskplane/tp.py" dod        # scope-diff + KB-lint exit gate (also run by the loop at sign-off)
```

The governed loop runs this mechanical DoD automatically at the **sign-off**
gate (the scope-diff + KB-lint verdict is shown next to the EM read-out); `tp
dod` is the same check as a standalone CLI for an ungoverned close. Both the
EM's human-facing review and the mechanical DoD must be satisfied. The human
makes the final call.

## Roles registry

| Role | Agent | Runs as | Governs |
| --- | --- | --- | --- |
| Product Manager | `product-manager` | read-only planning contract | authors DoR: scope + acceptance criteria |
| Executor | (the main agent) | the build contract | bounded by scope/tools; hook-enforced |
| EM code reviewer | `em-code-reviewer` | read-only review contract | validates DoR (quality) + DoD (requirements) |

## Adding a new role (iterative improvement)

The structure is deliberately copy-paste extendable. To add a role (e.g. a
Security reviewer, a QA/test author, a Tech-Lead architect):

1. Add `agents/<role>.md` with frontmatter (`name`, `description` + `<example>`
   blocks, `model: inherit`, a `color`, and a **tight `tools` list**).
2. In the body, first bind the role's taskplane contract with the right
   flags so the harness enforces the role's boundaries (a reviewer gets
   `--read-only` — plus `--write-allow ".em-review/**"` for its findings — so
   it can't touch code; a builder gets a real `--scope`). Never use an empty
   `--scope ""` to mean read-only: an empty scope disables write screening
   entirely, so every write is approved — `tp new` even warns you. Read-only
   is a distinct, enforced mode; use it.
3. Define the role's own **DoR** (what it needs to start) and **DoD** (what
   its output must satisfy) — roles differ: a security review's "done" is not
   a UI builder's "done".
4. Add the role to the registry table above and, if it belongs in the default
   flow, to the pipeline.

Keep each role narrow and evidence-driven. The value of the system is that
every role is individually governed and every step leaves a trace in
`.taskplane/trace.jsonl` — the audit record for the whole workflow.

## When NOT to use this

Skip for quick one-off edits (use `govern-under-contract` directly) and for
pure conversation. Use this when a change is worth speccing, building, and
reviewing as distinct, governed steps.
