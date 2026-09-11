# Budget enforcement

Phase workers receive their frozen phase token ceiling in the active hook
contract before launch. Quick lens workers have a ceiling of 100,000 native
tokens (or a smaller inherited ceiling) and eight metered actions. Standalone
review defaults to 1,000,000 tokens. These counts include cached input, uncached
input and output; they are not account quota percentages or dollar estimates.

The plugin's common PreToolUse screen covers every tool, including reads,
status, messaging, dispatch and waits. Inspection is exempt from the action
quota only. Missing required telemetry and malformed ceilings fail closed.
Bound phase workers use their own native transcript, never a parent counter.
Required token budgets remain binding even under an older advisory policy.

On exhaustion, preserve the attempt and evidence, report measured usage and
the ceiling, and ask the user to approve a specific additional budget or stop.
Do not self-grant, relaunch a worker, restart earlier phases, or use an advisory
waiver as automatic recovery. Stop hooks return `continue: false`; a Stop
`decision: block` would request another model turn and must not be used to
enforce a budget. Replayed Stop events preserve the stop decision.

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
