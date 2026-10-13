# Weekly host compatibility review

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
