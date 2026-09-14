# Common entry initialization

Every direct user entry uses this sequence, including help/status, resumed
work, and entry after an update. A specialist never assumes the facade ran.

1. Resolve the currently installed engine. Use the stable workspace launcher
   when present; otherwise use the installed plugin's `taskplane/tp.py`.
2. Run `onboard --initialize --json --available-tools "<comma-separated names of tools actually available in this task>" --workspace <checkout>`.
   Use the current tool inventory, not examples from these instructions. This
   declaration is a compatibility check, not proof of host enforcement.
3. Read the result. Initialization creates missing setup and the launcher,
   preserves existing context/run state, and checks the committed checkout,
   run binding, phase definitions, and live hooks through existing onboarding.
   Continue governed work only when `ready` is true. Help and status may still
   explain an unmet prerequisite.
4. Before a read-only review, also require `review_file_tools.ready`. A host
   exposing only shell commands cannot run the existing read-only contract.
   Report the missing file tools and leave the task ungoverned. Do not activate
   a contract to discover afterward that its tools are unavailable. No tool
   adapter, permission bypass, or replacement runtime is part of this sequence.
5. Use existing repository preparation for a different target. Standalone
   review initializes the prepared checkout again after committed-code scope
   checks and before deriving evidence or activating its contract. Start a
   fresh run only when requested; never copy old findings as current evidence.

Repeated entry rechecks current readiness and replaces only this host session's
compatibility declaration. Session isolation stays with the existing storage
and hook owners. A dispatched worker consumes its sealed startup; it does not
reinitialize or replace the coordinator's run.

If an existing read-only contract blocks reentry, invoke the installed engine's
absolute `tp.py` path for the onboarding command above. Existing status and
explicit recovery commands remain reachable when budget telemetry is missing.
