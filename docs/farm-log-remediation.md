# Farm audit remediation

The farm execution audit identified nine Taskplane defects: approval chronology,
hook attribution across run transitions, loss of original risk obligations,
incomplete Plan contracts, sealed-checkpoint recovery, native worker correlation,
usage boundaries, transient storage growth, and overstated verification coverage.

New observations must retain their original references and timestamps. An
explicit checkpoint name does not excuse a future or pre-checkpoint approval.
Legacy history remains readable without acquiring a new chronology assurance.
Hook call identity is recorded at admission, so a late result cannot move to the
run active when it arrives. Completed-run observations grant no execution scope.

Replacement carries findings and their required evidence, independently of the
replacement's narrower acceptance criteria. Resolving an obligation requires
evidence and an accepted disposition; passing unrelated checks does not close it.
Plans identify each Build packet, report and verification history owner and
declare the source, test and helper inputs needed by their checks. Verification
history retains failures and unknown results when a later attempt passes.
Static, unit, round-trip, browser, integration and independent verification are
distinct kinds; fixture, local and deployed environments are distinct evidence.

Native workers require current parent hook readiness before reservation and
dispatch. A Claude child identity must come from exact structured native
call/result and lineage observations. Root environment inheritance, a grant in a
prompt, or the only visible child is insufficient. Unsupported host invocation
identity and unavailable process-exit evidence remain explicit gaps.

Usage closure records preserve the committed transition's original measurement.
An at-finish view remains immutable while later follow-up observations accrue
separately. Only newly managed transient dashboard pairs are eligible for bounded
collection. Selected, pinned and otherwise referenced evidence is retained;
legacy snapshots are not retroactively adopted for deletion. Whole-store
inventory reports partial lower bounds when enumeration cannot complete.

## External farm obligations

These application and infrastructure obligations require their owners' runtime evidence:

| Obligation | Owner | Required evidence |
| --- | --- | --- |
| SEC-2 authorization | Infrastructure | Actual principal and inherited grants; allowed list/export paths; denied writes to users.json and config.json |
| Browser races and offline behavior | Application | Late history response, tenant-switch finance response, and failed presign-in scenarios in the application browser |
| Azure storage recovery | Backend/operations | Actual failure, conflict and recovery behavior |
| Deployed CI | Backend/operations | Results from deployed App and Infrastructure pipelines |

Candidate regression fixtures do not establish live Claude execution, a farm
browser result, cloud recovery, or a deployed pipeline result. Installed-plugin
observations and candidate source/package validation must be reported separately.
