
# /tp-graph — the map that saves the re-derivation

`TP=python3 "${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}/taskplane/tp.py"`.

- **Scan:** `$TP graph scan` — builds/refreshes `knowledge/graph.json`
  (Python/JS/TS/Go imports, docker-compose services; incremental by file
  hash). Run after merges; it's deterministic and cheap.
- **Impact:** `$TP graph impact --files a.py,b.ts` (or `--base <ref>` for
  a diff) — policy-bounded reverse-dependency traversal: what's touched,
  what depends on it, and where evidence stops. Lead reviews with this.
- **Distributed contracts:** `$TP graph contract orders.v1 --provider
  services/orders --consumer services/billing` records both sides against a
  `contract:` node. Cross-entity review stops there by default; depth beyond
  the contract requires an explicit policy and in-scope evidence.
- **Record what scanners can't see:** `$TP graph edge "svc:api" "src/api"
  --kind runs` (HTTP calls, queues, crons, deploys). Recorded edges
  survive rescans.
- **Visualize:** `$TP graph html --files <changed> --out graph.html` —
  self-contained interactive map, changed=red, impacted=orange by depth;
  render it for the user.

If the graph is empty, scan first — never hand-derive dependencies the
scanner can compute.
