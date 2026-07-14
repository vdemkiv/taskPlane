
# /tp-graph — the map that saves the re-derivation

`TP=python3 "${CLAUDE_PLUGIN_ROOT}/taskplane/tp.py"`.

- **Scan:** `$TP graph scan` — builds/refreshes `knowledge/graph.json`
  (Python/JS/TS/Go imports, docker-compose services; incremental by file
  hash). Run after merges; it's deterministic and cheap.
- **Impact:** `$TP graph impact --files a.py,b.ts` (or `--base <ref>` for
  a diff) — reverse-dependency BFS: what's touched, what depends on it,
  by depth, across code AND infra. Lead reviews with this.
- **Record what scanners can't see:** `$TP graph edge "svc:api" "src/api"
  --kind runs` (HTTP calls, queues, crons, deploys). Recorded edges
  survive rescans.
- **Visualize:** `$TP graph html --files <changed> --out graph.html` —
  self-contained interactive map, changed=red, impacted=orange by depth;
  render it for the user.

If the graph is empty, scan first — never hand-derive dependencies the
scanner can compute.
