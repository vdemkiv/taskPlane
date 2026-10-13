# Weekly host compatibility review

## Native review tool scope (local candidate)

The 2.33.10-compat.1 candidate recognizes the captured Codex hook names
`webrun`, `mcp__codex_apps__github__fetch_issue`, and
`mcp__codex_app__automation_update`. A tool name alone does not grant access.
Declare reads separately from a mutation in the initial run scope. These fields
are bound by the existing scope digest and request provenance; they cannot be
added by changing a sealed controller. The `request_reference` in each declaration
must equal the run's original user request reference.

```json
{
  "external_reads": {
    "host": "codex", "operations": ["web", "github_issue"],
    "request_reference": "conversation/actual-user-request"
  },
  "native_automation": {
    "host": "codex", "phase": "engineering",
    "request_reference": "conversation/actual-user-request",
    "request": {
      "mode": "create", "kind": "heartbeat", "name": "Review host updates",
      "prompt": "Read the authorized official sources and report verified compatibility changes.",
      "rrule": "FREQ=DAILY;INTERVAL=2", "status": "ACTIVE", "destination": "thread"
    }
  }
}
```

These are additions to a complete normal phase/path scope. Root-only readers
support HTTPS URL opens and bounded web search requests. The verified GitHub
schema uses `repository_full_name` and a positive integer `issue_number`.
Unknown web operations, connector mutations and additional arguments remain
unsupported. Claude uses `WebFetch`/`WebSearch` with `host: "claude"` and
`operations: ["web"]`; the Codex connector is not a verified Claude equivalent
for a private issue. Missing scope is a configuration refusal; unavailable or
disconnected host tools still require native installation/access resolution.

Heartbeat creation requires the exact declared arguments, an automatic observed
root call and the named open phase. The destination is the current local Codex
thread; an explicit `targetThreadId` must equal that root. Only hourly, daily or
weekly recurrence with a positive integer interval is implemented. Cron jobs,
other targets, updates, deletion and unknown modes have no guard in this candidate.
Admission is durably recorded as `admitted_outcome_unverified`, never success.
After any uncertain response inspect native inventory before proposing recovery;
a second create call in the same run is refused. A new run is not proof that a
previous creation failed. Confirm the actual returned ID, target and next run
before changing an existing schedule. Host permissions and app connection remain
independent; this scope never overrides their denials. Claude has no implemented
native Codex scheduler equivalent: retain a proposal for a separately authorized
scheduler. No scheduling happens through this documentation or package build.

## Existing GitHub schedule

GitHub Actions runs **Host compatibility watch** every Monday at **14:17 UTC**
(09:17 Florida standard time / 10:17 daylight time). GitHub may delay scheduled
runs. It runs only on the default branch and can also be dispatched manually.

The task reads the official `openai/codex` and `anthropics/claude-code` release
feeds, the official ChatGPT/Codex changelog, and both hosts' security/permissions,
subagent and Claude hook documentation. Sources are explicit in
`scripts/check_host_updates.py`; redirects fail visibly so a changed domain is
reviewed before use. The latest 100 releases per host are compared by identity
and content hash, including changed existing notes and prereleases. No overlap
with the previous window is reported as incomplete coverage, never as no change.
The first check establishes a baseline and reports the newest stable/prerelease
plus each configured document. A document hash change is not necessarily a
behavior change, and published versions are not installed-version observations.

Results live in one repository issue, **taskPlane host compatibility watch**.
Its hidden JSON marker stores the last checked source hashes. Changed findings
or failures get a history comment; unchanged sources do not generate duplicate
finding comments. Do not edit/remove the marker. Closing the issue stops report
updates with an explicit error; disable the Actions workflow to pause execution.
Each run also retains `report.md`, complete `report.json`, and `state.json` in the
**host-compatibility-report** artifact for 90 days.

This is deterministic change detection and keyword triage, **not a model-powered
semantic review**. Reports include source links, observed versions, candidate
relevance, evidence confidence and recommended taskPlane/proofPlane checks.
A maintainer must inspect relevant changes, record a disposition in the issue,
and separately authorize any implementation or native tests. Hooks, tool schemas,
approval behavior, worker lifecycle, context/state, modes and deprecations are
review topics. Model-related notes never prove a model change caused a regression.

Only the existing short-lived Actions `GITHUB_TOKEN` is used: repository contents
read and issues write. No paid API, additional secret, host installation, harness
mutation, active-session access, permission change or automatic update is involved.
Fetch/parse failures keep the prior source baseline and fail the workflow visibly.
Response, release-window, inventory and report bounds are explicit; incomplete
coverage is reported. Upstream text is data, never code or instructions to execute.

Read-only local check (no issue writes):

```sh
python scripts/check_host_updates.py --output-dir /tmp/taskplane-host-watch
python scripts/check_host_updates.py --state /tmp/taskplane-host-watch/state.json --output-dir /tmp/taskplane-host-watch-next
```
