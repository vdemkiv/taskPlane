
# TypeScript Code Quality

The language-specific standard for TypeScript and TSX. `the code-quality lens` delegates here whenever changed files are `.ts`/`.tsx`. The compiler alone proves *valid* TypeScript; this skill proves *well-typed, consistently styled, safe* TypeScript.

## 1. Tooling Gate (hard PASS/FAIL)

All four must exit 0. Compiling is necessary but **not** sufficient — a lint and format gate are mandatory because `tsc` catches none of style, floating promises, unused vars, or import hygiene.

```bash
npx tsc --noEmit                 # type errors → FAIL
npx eslint . --max-warnings 0    # lint errors OR warnings → FAIL
npx prettier --check .           # formatting drift → FAIL
npm run build                    # build break → FAIL
```

If `eslint`/`prettier` are not configured, that absence is itself a FAIL finding — note it and recommend the baseline below.

## 2. Strict tsconfig Baseline (this gates the rest)

"No `any`" is meaningless under a loose config. The following compiler options are **required**; flag any that are missing or disabled. `strict` alone is not enough — the last three catch the most real bugs and are not in `strict`.

| Option | Why |
|---|---|
| `"strict": true` | Umbrella: `noImplicitAny`, `strictNullChecks`, `strictFunctionTypes`, etc. |
| `"noUncheckedIndexedAccess": true` | `arr[i]` and `record[key]` become `T \| undefined` — eliminates a huge class of runtime crashes |
| `"exactOptionalPropertyTypes": true` | Distinguishes "absent" from "`undefined`" |
| `"noImplicitReturns": true` | Every code path returns |
| `"noFallthroughCasesInSwitch": true` | Catches missing `break`/`return` |
| `"isolatedModules": true` + `"verbatimModuleSyntax": true` | Forces `import type`, safe for bundlers |
| `"forceConsistentCasingInFileNames": true` | Cross-OS import safety |

## 3. Type Safety & Escape Hatches

The compiler is only honest if the escape hatches are policed. **Scan for and report every occurrence** — these are how green builds hide untyped code:

| Pattern | Rule |
|---|---|
| `: any` / implicit any | Forbidden. Use `unknown` + narrowing at the edge |
| `as any`, `as unknown as X` | Forbidden. A double-cast is a type-system override — require justification or a real type |
| Non-null assertion `!` | Forbidden except where invariants are provably enforced; prefer narrowing or `?.` |
| `@ts-ignore` | Forbidden. If unavoidable use `@ts-expect-error` **with a reason comment** so it fails when the error disappears |
| `// eslint-disable*` | Each must carry a reason; bare disables are a finding |
| `Function`, `object`, `{}` types | Forbidden — they mean "almost anything" |

```bash
# Quick escape-hatch census for the report:
grep -rnE '\bas any\b|as unknown as|@ts-ignore|: any\b|eslint-disable' --include='*.ts' --include='*.tsx' src/
```

## 4. Naming Conventions

| Element | Convention | Example |
|---|---|---|
| Types / interfaces / enums | PascalCase, **no `I` prefix** | `User`, `OrderState` |
| Variables / functions | camelCase | `parseOrder` |
| React components | PascalCase | `OrderTable` |
| Component prop types | `{Component}Props` | `OrderTableProps` |
| Constants (module-level literals) | CONSTANT_CASE | `MAX_RETRIES` |
| Booleans | `is`/`has`/`should`/`can` prefix | `isLoading`, `hasAccess` |
| Generic params | `T`, or descriptive `TKey`, `TValue` | `Map<TKey, TValue>` |
| Files | kebab-case; component files match component | `order-table.tsx` |

## 5. "Good TypeScript" Presence Checklist (reward design, not just absence of bad)

| Check | Look for |
|---|---|
| Discriminated unions over loose objects | `{ kind: 'ok'; value } \| { kind: 'err'; error }` instead of optional fields |
| Exhaustiveness | `switch` over a union ends with a `never` default (`assertNever`) |
| `readonly` / `as const` | Immutable data marked; literal unions via `as const` |
| `import type` for type-only imports | Keeps runtime graph clean (enforced by `verbatimModuleSyntax`) |
| Boundary validation | **All external input** (API responses, `req.body`, env, JSON) parsed with zod/valibot before entering the typed domain — never cast |
| `satisfies` for config objects | Preserves literal types while checking shape |
| Prefer union literals / `as const` maps over `enum` | Smaller output, no runtime surprises |

## 6. Error Handling

`try/catch` on all async; user-facing feedback on failure; empty/loading/error states handled; no swallowed errors. In `catch (e)`, `e` is `unknown` — narrow before use (`e instanceof Error`). Forbid floating promises (`@typescript-eslint/no-floating-promises`): every promise is `await`ed, `.catch()`ed, or explicitly `void`ed.

## 7. Security Quick-Scan

No secrets in source; validate every trust-boundary input (see §5); no `eval`/`new Function`; no `dangerouslySetInnerHTML` with unsanitized data; no `any` on API boundary payloads; URL/SQL/shell inputs parameterized, never string-concatenated.

## 8. Reuse & Duplication

Run a copy-paste detector and confirm existing shared code is reused, not reinvented.

```bash
npx jscpd src/ --min-tokens 50 --threshold 1 --reporters consoleFull   # token-based; fail above the agreed duplication %
```

| Check | Standard |
|---|---|
| Verbatim duplication | jscpd duplication under the agreed threshold (e.g. ≤1–3%); no block copy-pasted across files |
| Near-duplicate logic | Same logic with renamed variables or minor edits is still duplication — extract a shared function/hook |
| Reinvention (failure to reuse) | Before accepting new code, confirm an equivalent doesn't already exist in `lib/`, `utils/`, `ui/`, or shared hooks/components; flag re-implementations of existing helpers, types, or components |
| Shared placement | Logic used by more than one feature lives in a shared location (`lib/`/`ui/`), not copied into each feature dir |
| Type reuse | Shared types are imported from `src/types/`, not redeclared per file |

Report the duplication count and locations; recommend extraction where it pays off, but don't force a shared abstraction onto two things that merely look similar today (avoid premature DRY).

## 9. Report Format (slots into the code-quality lens)

```markdown
### TypeScript Quality: PASS ✅ / FAIL ❌
- Gate: tsc [clean/N errors] · eslint [N] · prettier [ok/drift] · build [ok/fail]
- tsconfig strictness: [compliant / missing: noUncheckedIndexedAccess, ...]
- Escape hatches: as-any [N], non-null! [N], @ts-ignore [N] — [files:lines]
- Naming violations: [list]
- Unvalidated boundaries: [list]
- Good-TS gaps: [e.g., non-exhaustive switch at x.ts:42]
```
