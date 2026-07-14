
# Python Code Quality

The language-specific standard for Python. `the code-quality lens` delegates here whenever changed files are `.py`. Python's flexibility means the type checker and linter — not the interpreter — are the quality gate. Code that *runs* is not code that *passes*.

## 1. Tooling Gate (hard PASS/FAIL)

All must exit 0. Recommended baseline toolchain (adapt to the repo's, but require an equivalent for each row):

```bash
ruff check .                     # lint (replaces flake8/isort/pyupgrade/pylint subset) → FAIL on any
ruff format --check .            # formatting drift → FAIL   (or: black --check .)
mypy --strict .                  # type errors → FAIL        (or: pyright in strict mode)
pytest -q                        # tests → FAIL
```

Absent type checker or linter config is itself a FAIL finding. Untyped code that "works" is the most common defect this gate exists to catch.

## 2. Strict Typing Baseline (this gates the rest)

Type hints are mandatory on all public functions, methods, and module-level constants. Enforce strictness explicitly — flag any of these missing from `pyproject.toml`/`mypy.ini`:

| Setting (mypy) | Why |
|---|---|
| `disallow_untyped_defs = true` | No unannotated function definitions |
| `disallow_any_generics = true` | No bare `list`, `dict` — must be `list[str]`, `dict[str, int]` |
| `disallow_incomplete_defs = true` | Partial annotations rejected |
| `warn_return_any = true` | Catches values silently typed `Any` |
| `no_implicit_optional = true` | `x: str = None` must be `x: str \| None` |
| `warn_unused_ignores = true` | Stale `# type: ignore` becomes a finding |
| `strict_equality = true` | Catches always-false comparisons |

(pyright strict mode covers the equivalents.)

## 3. Type Safety & Escape Hatches

**Scan for and report every occurrence** — these defeat the type checker while staying green:

| Pattern | Rule |
|---|---|
| `Any` (explicit) | Forbidden in signatures. Use `object` + narrowing, generics, or `Protocol` |
| `# type: ignore` (bare) | Forbidden. Require `# type: ignore[error-code]  # reason` |
| `cast()` overuse | A `cast` is an unchecked assertion — justify or fix the real type |
| `# noqa` (bare) | Require a specific code: `# noqa: E501` |
| Missing return annotation | Every public callable annotates its return (`-> None` included) |

```bash
grep -rnE '\bAny\b|# type: ignore(?!\[)|# noqa(?!:)|\bcast\(' --include='*.py' src/
```

## 4. Naming Conventions (PEP 8)

| Element | Convention | Example |
|---|---|---|
| Functions / variables / methods | snake_case | `parse_order` |
| Classes / exceptions / type aliases | PascalCase | `OrderState`, `ConfigError` |
| Constants | UPPER_SNAKE_CASE | `MAX_RETRIES` |
| Modules / packages | short, lowercase | `order_utils` |
| "Private" | single leading underscore | `_internal_cache` |
| Booleans | `is_`/`has_`/`should_` prefix | `is_active` |
| Type variables | short PascalCase | `T`, `KeyT` |

Forbid: single-char names (except loop indices/comprehensions), mutable default arguments (`def f(x=[])`).

## 5. "Good Python" Presence Checklist

| Check | Look for |
|---|---|
| Structured data is typed | `@dataclass`, `pydantic.BaseModel`, or `NamedTuple` — not loose dicts |
| Boundary validation | All external input (request bodies, JSON, env, config) validated via **pydantic** before use — never trusted raw |
| `Protocol` for structural typing | Duck typing made explicit and checkable |
| `Enum` / `StrEnum` for fixed sets | Not bare string literals |
| Context managers | `with` for files/locks/connections; custom `__enter__/__exit__` where relevant |
| `pathlib.Path` over `os.path` | Modern, safer path handling |
| Comprehensions / generators | Over manual loops where they read clearly; generators for large streams |
| f-strings | Over `%`/`.format()` |

## 6. Error Handling

Catch specific exceptions, never bare `except:` or `except Exception` without re-raise/handling. Define custom exception types for domain errors. Never swallow silently — log via `logging`, never `print`. Use `raise ... from err` to preserve context. Do not use `assert` for runtime validation (stripped under `-O`).

## 7. Security Quick-Scan

```bash
bandit -r src/ -ll    # high/medium severity → review
```
Flag: `eval`/`exec`/`pickle` on untrusted data; f-string/`%`-built SQL (require parameterized queries); `subprocess(..., shell=True)`; `yaml.load` without `SafeLoader`; hardcoded secrets; `requests` calls without `timeout=`; `assert` used for validation; `tempfile.mktemp`.

## 8. Reuse & Duplication

```bash
pylint --disable=all --enable=duplicate-code src/      # R0801 duplicate-code
# or multi-language: npx jscpd src/ --min-tokens 50 --threshold 1
```

| Check | Standard |
|---|---|
| Verbatim / near-duplicate | No repeated blocks (pylint R0801); the same logic with renamed names is duplication — extract a function/class |
| Reinvention (failure to reuse) | Confirm no existing helper in shared modules / `utils` / internal packages already does this before adding new code |
| Shared placement | Logic used across modules lives in a shared module/package, not copied into each |
| Reuse idioms | Prefer composition, `Protocol`/mixins, and the stdlib (`itertools`, `functools`) over re-implementing |

Report duplication locations and whether extraction is warranted; don't over-abstract one-off similarity.

## 9. Report Format (slots into the code-quality lens)

```markdown
### Python Quality: PASS ✅ / FAIL ❌
- Gate: ruff [N] · format [ok/drift] · mypy --strict [clean/N] · pytest [pass/fail]
- Typing strictness: [compliant / missing: disallow_untyped_defs, ...]
- Escape hatches: Any [N], bare type:ignore [N], cast [N] — [files:lines]
- Naming / PEP8 violations: [list]
- Unvalidated boundaries: [list]
- Security (bandit): [N high, N medium] — [list]
```
