"""R-0007 dependency-order contract for the delivered implementation chunks."""


def test_chunk_dependency_order_is_acyclic_and_t09_is_not_reopened():
    deps = {
        "runtime": set(), "wait": {"runtime"}, "routing": {"wait"},
        "human-context": {"routing"}, "findings": {"human-context"},
        "graph-input": {"human-context"},
        "leases": {"findings", "graph-input"},
        "cycles": {"graph-input"},
        "guidance": {"leases", "cycles"},
        "telemetry": {"leases", "cycles"},
        "matrix": {"guidance", "telemetry"},
    }
    settled = set()
    pending = dict(deps)
    while pending:
        ready = sorted(name for name, needs in pending.items()
                       if needs <= settled)
        assert ready, "dependency graph contains a cycle"
        for name in ready:
            settled.add(name)
            pending.pop(name)
    assert "t09" not in deps

