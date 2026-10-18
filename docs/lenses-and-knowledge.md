# Review lenses and project knowledge

A lens is a useful perspective on a concrete risk, not a required worker or phase.
Choose relevant correctness/security/architecture/maintenance concerns from the
actual change, dependencies and requirements. Use the [catalog](lens-catalog.md)
when it helps; no all-catalog disposition ledger, minimum lens count or mandatory
audit cadence is required for new runs.

Root may conduct one integrated review. Useful scoped native reviewers can add
independence/concurrency. Preserve explicit independence obligations and report
who actually reviewed which scope. Two lenses from one reviewer remain one
reviewer; absent native capacity is not independent coverage.

Record findings with location, severity, failure and evidence. Link accepted
requirements/design/Plan and latest applicable verification. Graphs and durable
notes help understanding but do not authorize writes or replace source evidence.
Current runtime records live in workspace-local `.taskplane/`; historical
external knowledge/loop/share layouts are not current operational instructions.
