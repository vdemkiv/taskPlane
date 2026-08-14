# Frozen PR-9464 correctness oracle

This directory freezes the behavioral shape that the live review of
`aws/karpenter-provider-aws#9464` initially missed. It is evaluator data only;
taskPlane production routing must not import it or special-case its symbols.

The changed serialization path has two callers: provisioning and EC2NodeClass
validation. Both converge on `EnsureAll`, continue to `Bottlerocket.Script`,
and reach the changed `MarshalTOML`. The validation caller is load-bearing:
the new non-AWS render error reaches a controller branch documented as
unreachable and can create a reconcile loop. The oracle therefore keeps that
finding at Blocker and requires backend and code-quality review while retaining
the architecture and security floors.

`oracle.json` also freezes acceptance bounds, not a fabricated measurement:
the historical taskPlane baseline is 2.36M effective tokens; an independently
recorded comparable replay must be at most 1.18M, at most 12 top-level CLI
calls, and emit no duplicate dashboard HTML. Missing telemetry remains
`not_comparable` under the evaluator rubric.
