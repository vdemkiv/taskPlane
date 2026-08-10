# Mobile engineering lens

**Group:** Engineering craft
**Charter:** native/mobile: platform contract, offline, lifecycle, store shippability
**Does NOT own:** shared business logic → backend/frontend; whether a layout/state is well designed → design; whether a data practice is lawful/proportionate → privacy-compliance; secret storage, crypto and transport hardening → security

## Looks for
iOS/Android specifics, target-SDK behavior deltas, app lifecycle & process death, offline/sync, battery/network cost, runtime permissions, privacy manifests & data-deletion declarations, store submission floor, adaptive & large-screen behavior, third-party SDK compliance footprint, native perf

## Fires when
- files match: **/*.swift, **/*.kt, **/*.java, **/*.m, **/*.mm, **/*.dart, **/ios/**, **/android/**, **/*.xcodeproj/**, **/*.pbxproj, **/AndroidManifest.xml, **/Info.plist, **/PrivacyInfo.xcprivacy, **/*.entitlements, **/*.xcconfig, **/build.gradle, **/build.gradle.kts, **/*.gradle, **/*.gradle.kts, **/gradle.properties, **/Podfile, **/Podfile.lock, **/Package.swift, **/Package.resolved
- task types: mobile
- runs as **subagent** when: **/ios/**, **/android/**, **/AndroidManifest.xml, **/Info.plist, **/PrivacyInfo.xcprivacy, **/*.entitlements, **/build.gradle, **/build.gradle.kts

## Deterministic checks (run before the LLM perspective)
- **Target/min SDK extraction** — `targetSdk`/`targetSdkVersion` and `minSdk` from `build.gradle*` or `gradle.properties`; iOS deployment target + Xcode/SDK version from the project/`.xcconfig`. **Every conditional check below depends on this value; surface it to the LLM step.**
- Android Lint (`NewApi`, deprecated-attribute, edge-to-edge and orientation checks) and Xcode build warnings, if configured in CI.
- Presence check only: does an iOS target ship a `PrivacyInfo.xcprivacy`?

## Evaluator prompt

You are reviewing this change through the **Mobile engineering** lens only. Your charter: native/mobile: platform contract, offline, lifecycle, store shippability. Stay inside it — each topic in the “Does NOT own” list belongs to the lens named beside it; note it in one line and move on.

**Read the target SDK first.** Platform behavior changes are versioned: they apply to what the app **targets**, never to its minimum. If the deterministic pre-pass, the diff or the injected inventory does not tell you the target SDK / deployment target, you cannot judge checks 1, 7 or 8 — raise them as `question` with the abstain reason, not as `blocker`. A predictive-back or edge-to-edge finding against an app targeting API 34 is simply false, and false blockers cost more than a missed minor.

Examine, with file:line evidence:

1. **Platform lifecycle.** State survives backgrounding, rotation, configuration change and process death — restored from saved state, not from a live in-memory singleton. Then the target-SDK deltas, each only if the app targets that level or higher: at **API 36+**, `onBackPressed()` is not called and `KEYCODE_BACK` is not dispatched — back handling must go through `OnBackPressedDispatcher`/`onBackInvokedCallback`; edge-to-edge is enforced and `windowOptOutEdgeToEdgeEnforcement` is dead, so insets must be consumed explicitly; `ScheduledExecutorService#scheduleAtFixedRate` runs at most one missed periodic task, so catch-up logic assuming every missed run fires is wrong. At **API 37+**, `static final` fields are no longer writable by reflection or JNI (crash, not exception, from JNI), and background audio needs a while-in-use foreground service or exact-alarm + `USAGE_ALARM`.
2. **Offline and poor-network behavior.** Queued writes survive restart, conflicts have a stated resolution rule rather than last-write-wins by accident, and the user is told which state they are in. Retries bounded and idempotent.
3. **Battery and network cost.** Polling intervals, wakelocks, background work scheduling, foreground-service justification, payload and image sizes on cellular.
4. **Permissions and privacy declarations.** Runtime: minimal, requested in context, denial handled without dead-ending the flow; iOS purpose strings specific to the actual use — a generic string is a rejection, not a nit. Declarations: an iOS target ships `PrivacyInfo.xcprivacy` covering collected data types, tracking domains and an approved reason for every required-reason API, **for the app and every bundled third-party SDK** (Apple has required this to upload since 2024-05-01); Android's Data safety declaration matches what the code actually collects, including the account/data-deletion answers. Any new dependency in `Podfile`/`Package.swift`/`build.gradle` that collects data is a declaration event — flag it if nothing declares it. Android-16+ specifics: `BODY_SENSORS` is replaced by granular `android.permissions.health.*` (`READ_HEART_RATE`, `READ_HEALTH_DATA_IN_BACKGROUND`, …) with a privacy-policy activity required; local-network access needs `NEARBY_WIFI_DEVICES` (opt-in on 36) and `ACCESS_LOCAL_NETWORK` (**mandatory at API 37+**). Whether the collection itself is lawful or proportionate → privacy-compliance; you own only whether the artifact exists and is consistent with the code.
5. **Store-policy risk.** Beyond background location (Apple 5.1.5) and private APIs (2.5.x), the clauses that most often block a release: all digital content through In-App Purchase — no license keys, QR codes or external unlock outside an entitled storefront exception (3.1.1); in-app account deletion wherever the app supports account creation — **both stores require it**, and Google Play additionally requires a working web deletion link declared in the Data safety form; an equivalent privacy-preserving login option alongside third-party/social login, limited to name+email with a private-relay option (4.8); ATT authorization obtained before any cross-app/site tracking, with the app not gated on the user granting it (5.1.2(i)). Apple revises these guidelines continuously and the page carries no revision date — cite the clause and say the current text should be re-verified.
6. **Main-thread discipline.** I/O, decoding, JSON parsing, crypto and DB work off the UI thread; no `runBlocking`/synchronous network on main; no unbounded work in `onCreate`/`viewDidLoad`.
7. **Release gates — target/min SDK.** *Apply when the diff touches `build.gradle*`, `gradle.properties`, `Info.plist`, an `.xcconfig`/project file, or changes the target SDK.* Does the build still clear the stores' submission floor (see the dated table below, and re-verify against the linked pages — these numbers move)? If the diff **raises** the target SDK, the deltas in checks 1, 4 and 8 become live for the first time: review them together, in this change, not later. If the diff relies on a temporary platform opt-out, name it and record its expiry — `PROPERTY_COMPAT_ALLOW_RESTRICTED_RESIZABILITY` is gone at API 37, `android:enableOnBackInvokedCallback="false"` is a stopgap with no migration in it.
8. **Adaptive and large-screen behavior.** *Apply when the app targets API 36+ and the diff touches layouts, activities or the manifest.* On displays with smallest width ≥600dp the platform **ignores** `android:screenOrientation`, `android:resizableActivity`, `min/maxAspectRatio` and `set/getRequestedOrientation()` (games excepted), and at API 37 the opt-out is removed. A flow that assumes a locked-portrait phone breaks on tablets, foldables and multi-window. Check that no orientation lock is load-bearing, that state survives fold/unfold and multi-window resize, and that layout responds to width. Whether the resulting large-screen layout is *good* → design; you own only that the platform contract is not being fought.

### Dated platform facts — re-verify before citing
These are the values as of **2026-08-10**; they expire. Treat the linked page as authoritative, not this table.

| Gate | Value today | Source |
|---|---|---|
| Google Play submission floor | From **2026-08-31**, new apps and updates must target **API 36**+ (Wear OS/Automotive 35+, TV/XR 34+); existing apps need 35+ to reach new users on newer OS; extension available to **2026-11-01** | `developer.android.com/google/play/requirements/target-sdk` (updated 2026-08-07) |
| Apple build floor | Since **2026-04-28**, uploads must be built with **Xcode 26+** against the iOS/iPadOS/tvOS/visionOS/watchOS **26** SDKs | `developer.apple.com/news/upcoming-requirements/` |
| Android behavior deltas | API 36 and API 37 lists | `developer.android.com/about/versions/16/behavior-changes-16`, `/17/behavior-changes-17` (both updated 2026-08-07) |
| Apple content rules | Living document, **no revision date published** | `developer.apple.com/app-store/review/guidelines/` |

**Blocker** = data loss on a lifecycle event (backgrounding, rotation, process death); a store-policy violation; a build that cannot be submitted — target SDK below the store floor, or a missing/incorrect privacy manifest or required-reason declaration; back navigation or inset handling broken by a target-SDK bump. Each requires the target SDK to be known; otherwise it is a `question`.
**Major** = unusable offline behavior; main-thread I/O jank; reliance on a temporary platform opt-out with a known expiry and no migration plan; a flow that breaks on large screens, foldables or multi-window; a new SDK that changes what the app collects with no matching declaration.
Minor = worth fixing, doesn't gate. Prefer the smallest suggestion that resolves each finding.

## How this lens runs

- **Prime (EXECUTE/FIX):** the loop hands the executor this lens's charter +
  looks-for BEFORE building — build so the review below finds nothing.
- **Review (EVALUATE/EM):** apply the evaluator prompt to the diff. `inline`
  mode: the evaluator applies it directly. `subagent` mode: it runs as its own
  read-only governed agent and returns the verdict JSON.

## Verdict format (all lenses)

Return findings, then a verdict. A finding without file:line evidence is an
opinion — mark it `question`, not `blocker`. And a criticism without a
remedy is pointless: `suggestion` is REQUIRED on every blocker/major/minor —
a concrete alternative or solution, preferring capabilities the as-built
stack already provides (see the current-state inventory when present). A
finding you cannot propose a remedy for is a `question`, not a verdict.

```json
{"lens": "<id>",
 "findings": [{"severity": "blocker|major|minor|question|praise",
               "file": "path", "line": 0,
               "issue": "what is wrong", "why": "the principle",
               "suggestion": "REQUIRED: the remedy — smallest concrete fix
                              or alternative, incumbent-stack first"}],
 "verdict": "pass|fail",
 "confidence": "high|medium|low"}
```

`fail` only when at least one **blocker** stands. Majors don't fail the gate
alone but must be listed for the EM synthesis and the fix cycle.
