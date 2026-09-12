"""Explicitly simulated host receipts for CLI behavior fixtures.

These fixtures exercise strict enforcement with a known live-host precondition.
They are test data, never native-host or release-acceptance evidence.

The simulation is conditional by design.  A spawned or in-process CLI only
demands a hook receipt when the environment advertises a live host session, so
these helpers engage only then.  Where no marker is present -- a CI runner, for
example -- they do nothing at all, and every fixture takes exactly the path it
takes without this module.  That keeps the fixtures from inventing enforcement
state on a host that would not have required it.
"""
import contextlib
import os
from unittest import mock

from taskplane import host_capabilities, taskplane_lite


#: Environment variables whose presence makes the CLI treat the process as a
#: live host session and therefore require a screen receipt.
HOST_SESSION_MARKERS = (
    "CLAUDE_CODE_VERSION", "CLAUDECODE", "CLAUDE_SESSION_ID", "CODEX_THREAD_ID",
)


def live_host_markers(environment=None):
    """Return the markers that make this environment a live host session."""
    source = os.environ if environment is None else environment
    return tuple(name for name in HOST_SESSION_MARKERS if source.get(name))


def record_simulated_hook(workspace, *, environment=None):
    """Record one simulated receipt, but only under a live host session."""
    environment = dict(os.environ if environment is None else environment)
    if not live_host_markers(environment):
        return None
    with mock.patch.dict(os.environ, environment, clear=True):
        return host_capabilities.record_runtime_hook_receipt(
            taskplane_lite.store_home(str(workspace)), hook_path="native",
            event={"hook_event_name": "PreToolUse", "cwd": str(workspace),
                   "session_id": environment.get("CODEX_THREAD_ID") or
                                 environment.get("CLAUDE_SESSION_ID")})


def confirmed_cli_hooks(cli):
    """Patch the CLI capability snapshot only when a host session is live.

    Without a marker this returns a null context, so the fixture leaves the
    CLI untouched and the test observes unmodified behavior.
    """
    if not live_host_markers():
        return contextlib.nullcontext()
    original = cli._host_capability_snapshot

    def snapshot(workspace, *args, **kwargs):
        record_simulated_hook(workspace)
        return original(workspace, *args, **kwargs)

    return mock.patch.object(cli, "_host_capability_snapshot", side_effect=snapshot)
