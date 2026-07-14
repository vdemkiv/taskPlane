# Design lens — deep UI audit

The full pass the design lens runs in subagent mode (UI-heavy or large
front-end changes). Everything is judged against the product's own design
system first; taste comes last.

## 1. State inventory (per surface touched)

For every screen/component in the diff, verify all five states exist and are
designed, not accidental: **loading** (skeleton/spinner with layout parity),
**empty** (first-use guidance, not a blank div), **error** (what happened +
what to do next), **partial** (some data failed / long lists truncated), and
**success**. A state that can occur but has no design is a finding.

## 2. Flow walk

Walk each user journey the change creates or alters: entry point → happy
path → failure path → recovery → exit. Check: no dead ends (every failure
has a way forward), back/cancel always work, destructive actions confirm
with consequence named, progress is never silently lost.

## 3. Consistency sweep

Spacing on the system's scale (no magic 13px), typography from the ramp,
color from tokens (never hex-in-component), components reused not re-drawn,
iconography one family, motion durations consistent. Divergence is a
finding with the token that should have been used.

## 4. Hierarchy & affordance

The most important element reads first at a squint; interactive things look
interactive and inert things don't; primary action is singular per view;
labels say what happens, not implementation words.

## 5. Responsiveness

Real breakpoints exercised (not just resized desktop): layout reflows,
touch targets ≥ 44px on touch surfaces, nothing truncates meaning.

## 6. Usability heuristics (quick checklist)

Visibility of system status; match to real-world language; user control
(undo over confirm where possible); consistency; error prevention over
error messages; recognition over recall; flexibility for experts
(shortcuts) without hurting novices; minimal design — every element earns
its place.

## Boundaries

Accessibility specifics (keyboard, ARIA, contrast) → the accessibility
lens: note them, don't grade them. FE implementation quality (state
management, render cost) → the frontend lens. Copy accuracy → tech-writer.

Verdict format: the standard lens JSON (`lenses/design.md`), findings with
screen + state + evidence, smallest fix per finding.
