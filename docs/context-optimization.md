# Durable context and relevant freshness

New runs use `brief/v1`. `flow context` returns goal, current phase/result,
accepted decisions, useful tasks/findings, source/evidence references and missing
input warnings. A fresh consumer reads that brief and follows material references;
it does not need predecessor chat/tool replay or delivery-attention proof.

Optional paging bounds transport. No consume/read receipt, DeliveryJournal,
startup account/probe, response digest, timing target, byte quota or completion
fence is required. File-integrity limits used to read genuine immutable evidence
are distinct from model-operated attention accounting.

Tasks/results reuse only relevant source/test/config inputs, task generation,
criterion epochs, accepted prerequisite identities, check command/environment and
workspace/package identity. Missing dependency coverage makes reuse unknown.
Changed dependencies invalidate affected transitive consumers; unrelated new
files and progress/storage revisions do not force hash acknowledgement or restart.

New checks create immutable attempt/log records under their producing visit.
Evaluate may produce fresh evidence without rewriting Build history. Every later
consumer retains the newest applicable fail/unknown rather than fall back to an
older pass. Repeat work after relevant changes/failures or a specific gap.

Old receipt/startup contracts remain historical evidence. Candidate mutation of
an active old-contract run refuses; do not upgrade it implicitly or transfer old
approval/context authority. Use its original compatible runtime or an explicitly
authorized retirement followed by a fresh run.
