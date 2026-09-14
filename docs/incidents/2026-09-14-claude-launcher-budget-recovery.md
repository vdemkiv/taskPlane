# Claude initialization and approved budget recovery

The 2.23.7 source contained two installed-engine resolvers that required a
Codex manifest. Claude archives contain only `.claude-plugin/plugin.json`, so
the generated project CLI and managed-checkout lookup rejected valid Claude
installations. The version reader already understood both manifests; the two
resolvers had duplicated that rule incorrectly. They now share one validator,
accept either host's manifest, and reject conflicting or malformed metadata.

Setup also reported `applied` when the launcher result was unsuccessful, while
Claude reports omitted the launcher entirely. Explicit and automatic setup now
report failures, and both hosts expose the launcher result. Archive journey
tests execute the generated CLI after extraction; invoking only the engine
inside a source tree had missed this packaging defect.

The launcher had accumulated a second responsibility that belongs to the host:
native hooks preferred its cache resolver, and OpenAI packaging removed direct
plugin execution. Native hook commands now prefer the installed plugin root
supplied by Codex or Claude. The launcher remains optional command-line access
and a compatibility fallback for a missing old cache root. The existing engine
scope guard leaves unrelated projects untouched, accepts initialized projects
without a launcher, and retains compatibility with previously onboarded ones.
Native initialization does not require installing this extra entry point.
Codex documents its installed root and native hook trust in the
[hook reference](https://learn.chatgpt.com/docs/hooks#plugin-bundled-hooks).

The blocked review exposed a separate recovery gap: approved action grants and
clearing were already exempt from screening, but token grants did not exist and
recovery help was blocked. A narrow `budget --grant-tokens` operation now records
explicit approval against the existing contract and restores the approved
headroom above observed native usage. It does not reset usage, clear source
restrictions, introduce a new approval service, or create another task. Bare,
compound, spoofed and malformed token recovery commands remain screened.

Cleanup moved untracked scratch, old release bundles and one-off design/review
material to a checksum-verified archive outside the checkout. Existing review
evidence was retained, and the user-authorized stale contract was closed through
the supported `clear --approved-by` operation. Generated root release bundles
are now ignored as well as `dist/`.

Package and simulated-host tests do not prove a live Claude/Cowork hook executed.
Native execution receipts, plugin trust and host policy remain separate gates;
these repairs cannot manufacture them. The earlier governed engineering review
was not completed by this cleanup.
