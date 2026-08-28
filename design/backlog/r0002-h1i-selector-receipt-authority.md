# R-0002 accepted exception: H1-I selector receipt authority

Status: accepted delivery exception

Accepted by: Volodymyr Demkiv

Authority: user instruction, “bypass and proceed with the plan”

Candidate: `7757ff1bf5418d3d387afe74e733803c05b452aa`

The final independent H1-I evaluation passed the focused H1 integration
suite (8 tests) and governed-command/checkpoint suite (53 tests), but found
that `remediation_trace` accepts a canonically re-digested selector execution
receipt without proof that the governed selector producer actually executed
the selector. The receipt binds the claimed fields but is not minted under
engine-held authority.

The candidate was merged only under the attributed bypass above. It must not
be described as independently green for selector-execution authority.

Required closure: selector execution receipts must be minted exclusively by
the governed checkpoint broker under engine-held, one-use authority and the
consumer must reject caller-generated or re-digested substitutes.

Re-entry trigger: before claiming R-0002 final independent evidence complete,
or before relying on `remediation_trace` as release authority, whichever
comes first.
