
# Security review — the security lens, on demand

Apply the security lens to a diff or path without waiting for the loop's
EVALUATE step. Read-only by contract; report, don't repair.

## Procedure

1. **Bind the review contract** so writes are mechanically blocked
   (`PLUGIN=${CLAUDE_PLUGIN_ROOT}`):

   ```bash
   python3 "$PLUGIN/taskplane/tp.py" new --read-only \
       --write-allow ".security-review/**" \
       --tools "Read,Grep,Glob,Bash,Write" "SECURITY REVIEW: <target>"
   ```

2. **Scope the target.** Default: current uncommitted changes + last commit
   (`git diff HEAD~1` + untracked). A branch/PR/path argument overrides it.

3. **Run the methodology** in `$PLUGIN/lenses/references/security-methodology.md`:
   scanners first (gitleaks; npm audit / pip-audit / govulncheck; semgrep /
   bandit / gosec — a scanner that can't run is itself a finding), then the
   passes: dependencies, secrets & config, auth/session, access control
   (incl. RLS policies), injection, data protection, and the AI/LLM surface
   with the prompt-injection input-boundary guard.

4. **Report** to `.security-review/report.md` with the lens verdict format
   (`$PLUGIN/lenses/security.md`): findings with severity + file:line +
   smallest fix, then a graded verdict. CRITICAL/HIGH findings mean the work
   must not ship until resolved — escalate per `docs/authority-matrix.md`;
   do not fix anything yourself.

5. **Record and release.** Log the verdict to the knowledge base
   (`tp.py kb record "Security review: <target> — <verdict>" --tags
   security-review`) and `tp.py clear` the contract when done.
