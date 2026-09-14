---
name: tp-engineering
description: Review source code, changes, architecture, or security and report actionable findings. Also consumes Engineering evidence when explicitly invoked by a Taskplane delivery stage. Review does not implement fixes.
---

# Engineering review

Use the current conversation and native tools to inspect the requested repository.
Preserve the user's scope and existing decisions. Do not run onboarding, initialize
a delivery workflow, activate a contract, or require hook readiness for source review.

1. Select the readable checkout and the requested revision or comparison. A whole
   repository request includes its tracked source and needs no artificial diff.
   Reuse an available checkout; acquire or transfer source only when it is unavailable.
2. Inspect relevant source with native file, search, and command tools. Follow the
   host's permissions and agent lifecycle. Use supporting tools or agents only when
   the task warrants them and the user or applicable instructions authorize them.
3. Report concrete findings with severity, triggering conditions, file locations,
   and consequences. Distinguish confirmed defects from questions and missing evidence.
   Reuse applicable CI results. Run additional checks only for a specific evidence gap
   or changed behavior, respecting a static-review request.
4. Present the findings directly, including an honest coverage summary and remaining
   uncertainty. Completion does not require a dashboard acknowledgment or another
   approval merely to show the report. Apply fixes only when requested.

If a pinned inventory is useful, resolve the installed plugin's `taskplane/tp.py`
from its supplied root or this skill's location and invoke it with Python:

- `review start --scope repository --workspace <checkout>` for tracked source.
- `review start --base <ref> --workspace <checkout>` for a comparison.

The response references the selected source artifact; read it through native tools.
It creates no execution contract, dispatch, or tool budget. Any explicitly supplied
`--max-tokens` or `--max-actions` is advisory. `budget --workspace <checkout>` reports
native usage since the starting observation when both observations are available;
unavailable usage stays unknown. Use a native budget control when the host offers one.
Do not restart the conversation to reset a counter or load transcripts into the prompt
merely to inspect usage.

Use `onboard` only for an explicit setup/diagnostic request or a concrete setup failure.
Load [delivery review](references/delivery-review.md) only when an explicit stage
dispatch or existing delivery run requires it. Existing contracts are not silently
cleared; use the existing authorized recovery for that exact task.
