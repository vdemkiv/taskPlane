# Budget enforcement

Phase workers receive their frozen phase token ceiling in the active hook
contract before launch. Quick lens workers have a ceiling of 100,000 native
tokens (or a smaller inherited ceiling) and eight metered actions. Standalone
review defaults to 1,000,000 tokens. These counts include cached input, uncached
input and output; they are not account quota percentages or dollar estimates.

The plugin's common PreToolUse screen covers every tool, including reads,
status, messaging, dispatch and waits. Bounded inspection, recovery help and
explicitly approved recovery remain accessible after exhaustion or missing
telemetry. Productive work still fails closed without required telemetry.
Bound phase workers use their own native transcript, never a parent counter.
Required token budgets remain binding even under an older advisory policy.

On exhaustion, preserve the attempt and evidence, report measured usage and
the ceiling, and ask the user to approve a specific additional budget or stop.
Do not self-grant, relaunch a worker, restart earlier phases, or use an advisory
waiver as automatic recovery. Stop hooks return `continue: false`; a Stop
`decision: block` would request another model turn and must not be used to
enforce a budget. Replayed Stop events preserve the stop decision.

After the user approves a specific additional token amount, invoke the installed
engine or the optional project CLI with `budget --grant-tokens N --approved-by
USER --workspace <workspace>`. This records the existing chat approval; the
attribution flag is not independent proof of a human message. Apply the grant
once, inspect the result, and resume the existing task without requesting the
same approval again. Native tool permissions still apply to the command.

The hook retains the current host-selected counter source in the existing
contract meter. The grant re-reads that native counter and sets the ceiling to
`max(previous ceiling, observed usage) + approved additional tokens`. For
example, a 1 million ceiling with 49 million observed tokens and approval for
10 million additional tokens becomes a 59 million ceiling. No usage is erased,
and the contract's source permissions and other limits remain unchanged.
Missing current usage refuses the grant instead of assuming zero. Action-only
approval continues to use `--grant N`; it does not raise a token ceiling.

Hooks enforce tool boundaries. They cannot cancel inference already in flight
or impose the host's generation limit, so one response can cross the ceiling
before its usage is visible. This is not a guarantee of zero token overshoot.

Workers receive a self-contained startup read instruction. Lenses read scoped
views and only referenced sections, batch independent reads, write a compact
result, and stop. They do not load the whole envelope by default or coordinate
other agents. Phase collection returns findings inline. Preparing an unchanged,
completed lens plan returns no dispatches. Short polling is refused; use a
completion/attention event wait instead.

Install/reload the updated plugin hook manifest for the all-tools matcher to
take effect. Updating Python source alone does not change hooks already loaded
by a running host. Keep the single plugin hook registration; do not add project
hooks as a workaround.
