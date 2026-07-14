
# Go Code Quality

The language-specific standard for Go. `the code-quality lens` delegates here whenever changed files are `.go`. Go compiles strictly, so the quality bar shifts to what the compiler *doesn't* enforce: error handling, concurrency safety, and idiom. The single highest-value check in Go is **error handling** — treat it as the headline.

## 1. Tooling Gate (hard PASS/FAIL)

All must exit 0 / empty:

```bash
go build ./...                   # build break → FAIL
go vet ./...                     # suspicious constructs → FAIL
gofmt -l .                       # any file listed = unformatted → FAIL  (or: gofumpt -l .)
golangci-lint run                # aggregated linters → FAIL
go test -race ./...              # test failures OR data races → FAIL
```

`golangci-lint` should enable at minimum: `errcheck`, `govet`, `staticcheck`, `ineffassign`, `unused`, `gosimple`, `gosec`, `revive`. Absence of a lint config is a FAIL finding.

## 2. Error Handling (the central Go quality concern)

| Rule | Detail |
|---|---|
| Every error is checked | No `_ = doThing()` that discards a meaningful error; `errcheck` enforces this |
| Errors are wrapped with context | `fmt.Errorf("loading config: %w", err)` — `%w`, not `%v`, so the chain is inspectable |
| Inspect with `errors.Is` / `errors.As` | Never string-match on `err.Error()` |
| Sentinel/typed errors for domain cases | `var ErrNotFound = errors.New(...)`; custom error types for structured data |
| No `panic` in library code | `panic` only at `main`/init for truly unrecoverable startup; libraries return errors |
| Don't log **and** return | Handle an error once — wrap and return, or handle and stop; not both |

```bash
# Census of ignored errors / panics for the report:
grep -rnE '\b_\s*[:=]=?\s*[a-zA-Z].*\(|panic\(' --include='*.go' . | grep -v _test.go
```

## 3. Type & Interface Discipline

| Pattern | Rule |
|---|---|
| `any` / `interface{}` | Avoid as a type cop-out; use concrete types or generics (`[T any]`). Each use is a finding to justify |
| Type assertions `x.(T)` | Use the two-value form `v, ok := x.(T)` — never the panicking single form on untrusted values |
| Accept interfaces, return structs | Functions take the narrowest interface they need, return concrete types |
| Small interfaces | Prefer single-method interfaces (`io.Reader` style) over broad ones |

## 4. Naming Conventions (Go idiom)

| Element | Convention | Example |
|---|---|---|
| Exported identifiers | MixedCaps, capitalized, **with doc comment** | `func ParseOrder(...)` + `// ParseOrder ...` |
| Unexported | mixedCaps, lowercase first | `parseOrder` |
| Never `snake_case` | Go uses MixedCaps exclusively | not `parse_order` |
| Interfaces | often `-er` suffix | `Reader`, `Validator` |
| Receivers | short (1–2 chars), consistent per type | `func (s *Server) ...` |
| Packages | short, lowercase, no underscores/plurals | `order`, not `order_utils` |
| Acronyms | consistent case | `ID`, `URL`, `HTTP` — `userID`, not `userId` |
| No stutter | package + name shouldn't repeat | `order.New()`, not `order.NewOrder()` |

## 5. "Good Go" Presence Checklist

| Check | Look for |
|---|---|
| `context.Context` first param | Any blocking/IO/RPC call accepts and propagates `ctx` |
| `defer` for cleanup | `defer f.Close()`, `defer mu.Unlock()`, `defer resp.Body.Close()` |
| No goroutine leaks | Every goroutine has a clear exit path (ctx cancel / closed channel) |
| Channel direction | Params typed `<-chan` / `chan<-` where appropriate |
| Table-driven tests | Subtests via `tt := range cases` + `t.Run` |
| Zero-value usefulness | Types usable without explicit init where idiomatic |
| Boundary validation | External input (request bodies, flags, env) validated before use |

## 6. Concurrency Safety

`go test -race` must be clean (in §1). Shared mutable state is guarded by `sync.Mutex`/`sync.RWMutex` or owned by a single goroutine and communicated via channels. No `WaitGroup.Add` inside the goroutine it tracks. Flag any global mutable state.

## 7. Security Quick-Scan

`gosec` (via golangci-lint) findings reviewed. Flag: SQL built by string concatenation (require parameterized queries / `database/sql` placeholders); `exec.Command` with shell-interpolated args (pass args as a slice, never `sh -c` with user input); `math/rand` for tokens/secrets (require `crypto/rand`); hardcoded credentials; HTTP clients without timeouts; missing `defer resp.Body.Close()`; unvalidated file paths (`filepath.Clean` + base check).

## 8. Reuse & Duplication

```bash
dupl -threshold 50 ./...      # Go clone detector (also available as the `dupl` linter inside golangci-lint)
```

| Check | Standard |
|---|---|
| Verbatim / near-duplicate | dupl clones under the threshold; repeated logic extracted to a shared func/package |
| Reinvention (failure to reuse) | Reuse the stdlib and existing internal packages before writing new helpers; check the module's existing packages first |
| Shared placement | Common code lives in a shared package (e.g. `internal/...`), not copied across packages |

Go caveat: the community prefers a little duplication over the *wrong* abstraction ("a little copying is better than a little dependency"). Flag genuine clones, but don't force a shared abstraction onto two things that only look similar today.

## 9. Report Format (slots into the code-quality lens)

```markdown
### Go Quality: PASS ✅ / FAIL ❌
- Gate: build [ok] · vet [clean/N] · gofmt [ok/N unformatted] · golangci-lint [N] · race [clean/N]
- Error handling: ignored errors [N], unwrapped (%v not %w) [N], panic-in-lib [N] — [files:lines]
- any/interface{} overuse: [N] — [files:lines]
- Naming violations: [list]
- Concurrency: [race count / leak risks]
- Security (gosec): [N high, N medium] — [list]
```
