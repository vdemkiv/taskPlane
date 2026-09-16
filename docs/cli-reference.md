# Taskplane CLI

Invoke `python3 <plugin>/taskplane/tp.py` with one of these commands.

| Command | Purpose |
| --- | --- |
| `flow start --workspace PATH --goal TEXT` | Start or reuse the shared delivery run |
| `flow progress --workspace PATH --phase PHASE --note TEXT` | Record actual progress |
| `flow attach --workspace PATH --tasks FILE --reviews FILE --evidence FILE` | Attach shared tasks and evidence |
| `flow report --workspace PATH [--run ID]` | Show recorded progress and native token coverage |
| `flow finish --workspace PATH --note TEXT` | Record the outcome and refresh the dashboard |
| `flow hook` | Observe a native hook event from stdin without a permission decision |
| `graph --workspace PATH scan --decompose` | Scan source dependencies and components |
| `graph --workspace PATH impact --files a.py,b.py --json` | Inspect affected consumers |
| `graph --workspace PATH edge SRC DST --kind runtime` | Record an observed relationship |
| `graph --workspace PATH html --out graph.html` | Render the interactive graph |
| `dashboard --workspace PATH [--out dashboard.html]` | Render the shared dashboard |
| `review start --workspace PATH --scope repository` | Capture tracked source for review |
| `review start --workspace PATH --base REF` | Capture a comparison for review |
| `lens --workspace PATH --files a.py,b.py --stage engineering` | Suggest applicable lenses |
| `version --verify` | Check host manifest versions |

Phases are Product, Design, Plan, Build, Evaluate, Engineering and Retro. They are
observations of work, not gates. The orchestrator advances the flow. There is no
Taskplane token cap, contract activation, lease, signed handoff or approval ledger.
Native host permissions and approvals requested by the user remain authoritative.

All runtime observations and graph data live in the workspace's `.taskplane/`.
Attach evidence using workspace-relative paths. Missing counters are unknown,
not zero. Token values reflect observed native usage and are not billing totals.
