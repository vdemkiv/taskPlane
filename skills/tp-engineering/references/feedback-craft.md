# Feedback craft — growth-oriented review + detail levels

How every taskplane review surface (EM synthesis, evaluator findings, lens
verdicts) presents feedback to humans. Severity decides *action*; this
reference decides *communication*.


Review feedback should make the engineer better, not just the PR. Think of it in two layers:

- **Automated layer (Definition of Ready):** quality, security, and duplication checks run by tooling — the code-quality lens (with the language references) and the security lens. Objective and reproducible, so present them compactly; engineers are expected to run and internalize them before review (shift-left), which frees attention for the feature itself.
- **Human layer (growth + feature):** design, trade-offs, whether the right thing was built, and the developmental feedback — where your judgment and voice go.

Severity already drives action (Critical → fix now, Important → before proceeding, Minor → note later). Layer communication craft on top of it:

- **Lead with genuine strengths** — specific, not flattery, and not a sandwich that hides a Critical issue.
- **Each substantive comment answers three things:** *what* (`file:line`), *why* (the principle, not just the rule), and *how* (a concrete fix or a link to the relevant skill so they can go deeper).
- **Pick one or two growth themes; don't flood with Minor nits** — growth happens on themes, not nits.
- **Mark non-defect comments too:** `praise:` for what was done well, `question:` where you may be missing context.
- **Tone: kind and direct.** Critique the code, never the person; no sarcasm, no rubber-stamping. Growth-oriented never means softening a Critical issue — the kindness is in making it specific, explained, and fixable.

### Feedback detail level (0 / 1 / 2)

The reviewer sets a detail level. It compresses explanation and low-severity items only — it **never** hides a Critical issue or a high-severity security finding (a verbosity setting must not suppress a defect). Default: 1.

| Level | Includes | Minor items |
|---|---|---|
| 0 — very detailed | Every comment with full what / why / how + skill links; full DoR findings | All included |
| 1 — middle (default) | Strengths, Critical + Important + key suggestions with brief why, 1–2 themes, DoR summary | Batched into one line, or omitted |
| 2 — high level | Verdict, Critical issues one line each, themes, one-line strengths, DoR pass/fail | Omitted |

Choose by audience: 0 for teaching or newer engineers, 1 for normal review, 2 for a quick scan or a status read-out.
