# R-0002 accepted exception: H3-C residual retention gaps

Status: accepted delivery exception

Accepted by: Volodymyr Demkiv

Authority: user instruction, “bypass and proceed”

Candidate: `9b85aa9b578e5210eb31d5f9e6faff916a694d93`

The final independent H3-C evaluation passed all eight focused privacy tests
but found three residual retention gaps:

- authority events appended by the loop can bypass the trace retention lock,
  rotation, and archive sweep;
- a pre-upgrade active `trace.jsonl` below the rotation threshold can retain
  raw identities and free text indefinitely;
- abandoned nonterminal command handles are excluded from the aggregate
  TTL/count/byte retention policy.

The candidate was merged only under the attributed bypass above. H-23 and
H-25 must not be represented as independently green.

Required closure: route every authority append through the bounded trace
sink; migrate or expire the active pre-upgrade trace; and apply attributable
TTL/count/byte limits to abandoned nonterminal command handles without
removing live owned work.

Re-entry trigger: before relying on Taskplane retention guarantees for a
regulated workload or claiming R-0002 final independent evidence complete.
