# Managed PR review journey

This eval freezes workflow checkpoints, not model wording or a deterministic
review verdict. Its fixture test exercises the Codex path that failed on PR
35183: managed checkout, merge-base diff, graph-quality policy, selective
routing, host-observed leased writes, parent-workspace collection, and one
canonical revision.

The model remains free to produce different valid findings. The run is on
path only when every result is attributable to its sealed slot and the engine
can collect it without recloning, redispatching, or asking for another task.
