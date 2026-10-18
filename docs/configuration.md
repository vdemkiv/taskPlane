# Runtime configuration

Inspect the actual loaded runtime's `version --verify`, relevant command `--help`
and capability output. Version labels/source paths alone do not prove a session
loaded new skills or hooks. New runs use the compact contract; configuration does
not restore receipt/startup/fan-out or automatic approval defaults.

## Workspace and host observations

| Setting | Meaning |
| --- | --- |
| `TASKPLANE_WORKSPACE` | Explicit selected execution root; supplied workspace selectors must agree with its supported binding/alias |
| `TASKPLANE_SURFACE` | Host surface such as Cowork; does not prove local execution |
| `TASKPLANE_WORKSPACE_POLICY` | Existing selected-project execution restriction; a weaker environment value cannot relax stored policy |
| `TASKPLANE_EXECUTION_LOCATION` / `TASKPLANE_EXECUTION_REFERENCE` | Separate current command environment observation |
| `TASKPLANE_WORKER_LOCATION` / `TASKPLANE_WORKER_REFERENCE` | Separate current worker environment observation |
| `CODEX_THREAD_ID`, `CLAUDE_SESSION_ID` | Host-supplied session observations; not worker identity or human approval by themselves |
| `PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT` | Host plugin location inputs where supported; empty roots must not select `/taskplane/tp.py` |

Host adapters may expose version, hook/trust/policy, native lifecycle, model/effort
and usage capabilities. Preserve actual observation status and provenance; missing
means unknown/unavailable, not supported. An environment label cannot admit a
protected host owner or grant native identity, scope or approval.

## Removed configuration

Old loop/share/plan-aware store, wave transport, lens quota, startup-account,
receipt, render-obligation and maintenance-acknowledgement examples are historical.
Do not use environment settings from old manuals to bypass current gates or make
unsupported functionality appear available. New-run ownership is root/serial or
useful native work; explicit independence obligations remain binding.

Use [state ownership](state-spec.md) for current `.taskplane/` owners and backups,
[onboarding](onboarding.md) for host reload/project binding, and [CLI](cli-reference.md)
for supported operations. Installation, publication, messaging and host permissions
remain separately authorized actions. Optional metrics never change phase authority.
