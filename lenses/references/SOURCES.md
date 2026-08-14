# Language reference sources

Every language reference in this directory is written for taskplane. Facts are
checked against primary documentation; third-party prose is not copied unless
the source is explicitly permissive and pinned below.

| Source | Licence/use | Revision checked | Used by |
|---|---|---|---|
| [Go 1.26 release notes](https://go.dev/doc/go1.26), Go modules, memory model, `net/http`, `testing`, and vulnerability documentation | Primary factual source; original wording | 2026-08-14 | Go references |
| [spf13/go-skills](https://github.com/spf13/go-skills) | MIT; concepts adapted with attribution, defects independently corrected | `e67851cfcca008592c7c4965b8220c7cb37e2f1c` | Go references |
| [Python 3.14 documentation](https://docs.python.org/3.14/), especially `asyncio`, `typing`, `ExceptionGroup`, and `contextlib` | PSF documentation; factual source, original wording | 2026-08-14 | Python references |
| [Python Packaging User Guide](https://packaging.python.org/), including dependency groups and `pylock.toml` | Primary PyPA specification; factual source, original wording | 2026-08-14 | Python references |
| [TypeScript documentation](https://www.typescriptlang.org/docs/), including 6.0/7.0 release notes, module resolution, and project references | Official documentation; factual source, original wording | 2026-08-14 | TypeScript references |
| [typescript-eslint typed linting](https://typescript-eslint.io/getting-started/typed-linting/) and promise rules | MIT documentation; factual source, original wording | 2026-08-14 | TypeScript references |

## Explicit exclusions

No prose, examples, or structure is taken from unlicensed or incompatible
sources. In particular, `danvk/effective-typescript` is not used because its
book material is not under an OSI licence, and CC-BY-SA guidance is used only
as a pointer to facts that are re-verified against the primary documentation.
