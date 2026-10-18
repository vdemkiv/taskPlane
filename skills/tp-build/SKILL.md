---
name: tp-build
description: Build a requested feature through to verified output. Reuse existing product decisions and use optional alternatives only when requested or needed.
---

# Build a feature

Use [delivery](../tp-go/SKILL.md) and [the shared flow](../tp-go/references/shared-flow.md).
Clarify the outcome, inspect the implementation, reuse accepted Product/Design
and prepare an executable Plan before changing source. Plan fixes concrete
Build ownership, relevant inputs, exact permitted paths and meaningful checks.
Do not require those filenames before Product.

Implement the accepted approach and remove obsolete tests/fixtures/callers with
removed code. A/B variants are optional and only useful when requested or needed
to settle a material choice. This skill authorizes useful bounded native work;
respect serial constraints and actual dependencies. No startup ceremony or
worker quota is required. Keep native completion evidence distinct from messages.

Run affected checks, record pass/fail/unknown and present Build for separate
approval. Evaluate, Engineering and Retro each still require their own approval.
Installation, publication and host permissions remain separate actions.
