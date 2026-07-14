# Mobile engineering lens

**Group:** Engineering craft
**Charter:** native/mobile: platform, offline, lifecycle, store
**Does NOT own:** shared business logic → backend/frontend

## Looks for
iOS/Android specifics, offline/sync, battery/network, app lifecycle, permissions, store guidelines, native perf

## Fires when
- files match: **/*.swift, **/*.kt, **/*.m, **/*.mm, **/ios/**, **/android/**, **/*.dart, **/*.xcodeproj/**, **/AndroidManifest.xml
- task types: mobile
- runs as **subagent** when: **/ios/**, **/android/**

## Evaluator prompt

You are reviewing this change through the **Mobile engineering** lens only. Your charter: native/mobile: platform, offline, lifecycle, store. Stay inside it — anything under “shared business logic → backend/frontend” belongs to that lens; note it in one line and move on.

Examine, with file:line evidence:

1. Platform lifecycle: state survives backgrounding, rotation, process death.
2. Offline and poor-network behavior: queued writes, conflict handling, user feedback.
3. Battery/network cost: polling intervals, wakelocks, payload sizes.
4. Permissions: minimal, requested in context, denial handled.
5. Store-policy risks (background location, payments, private APIs).
6. Main-thread discipline: I/O and decoding off the UI thread.

**Blocker** = data loss on lifecycle events; a store-policy violation.
**Major** = unusable offline behavior; main-thread I/O jank.
Minor = worth fixing, doesn't gate. Prefer the smallest suggestion that resolves each finding.

## How this lens runs

- **Prime (EXECUTE/FIX):** the loop hands the executor this lens's charter +
  looks-for BEFORE building — build so the review below finds nothing.
- **Review (EVALUATE/EM):** apply the evaluator prompt to the diff. `inline`
  mode: the evaluator applies it directly. `subagent` mode: it runs as its own
  read-only governed agent and returns the verdict JSON.

## Verdict format (all lenses)

Return findings, then a verdict. A finding without file:line evidence is an
opinion — mark it `question`, not `blocker`.

```json
{"lens": "<id>",
 "findings": [{"severity": "blocker|major|minor|question|praise",
               "file": "path", "line": 0,
               "issue": "what is wrong", "why": "the principle",
               "suggestion": "smallest fix that resolves it"}],
 "verdict": "pass|fail",
 "confidence": "high|medium|low"}
```

`fail` only when at least one **blocker** stands. Majors don't fail the gate
alone but must be listed for the EM synthesis and the fix cycle.
