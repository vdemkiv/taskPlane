
# Product Manager — author the contract

Dispatch the `product-manager` agent. It binds a planning contract (writes
limited to `docs/**`,`specs/**`), turns the goal into problem / users / in &
out of scope / testable acceptance criteria, and emits a **contract handoff**
(`scope_paths`, `out_of_scope`, `dod.test_command`). Hand that to the build
step via `python3 "${CLAUDE_PLUGIN_ROOT}/taskplane/tp.py" new --scope … --tests …`
so the implementation runs under exactly the contract the PM defined.
