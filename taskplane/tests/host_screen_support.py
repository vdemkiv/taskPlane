"""Explicitly simulated host receipts for CLI behavior fixtures.

These fixtures exercise strict enforcement with a known live-host precondition.
They are test data, never native-host or release-acceptance evidence.

These CLI boundary tests declare hook readiness as a precondition regardless
of the runner's environment. The real capability reader and strict enforcement
still run. Missing, foreign-session and actual hook receipts are exercised
separately through the extracted-package CLI, without this helper.
"""
import os
from unittest import mock

from taskplane import host_capabilities, taskplane_lite


def record_simulated_hook(workspace, *, environment=None):
    """Supply the fixture's explicit readiness precondition on every runner."""
    environment = dict(os.environ if environment is None else environment)
    with mock.patch.dict(os.environ, environment, clear=True):
        return host_capabilities.record_runtime_hook_receipt(
            taskplane_lite.store_home(str(workspace)), hook_path="native",
            engine_fingerprint=taskplane_lite._entry_engine_fingerprint(),
            event={"hook_event_name": "PreToolUse", "cwd": str(workspace),
                   "session_id": environment.get("CODEX_THREAD_ID") or
                                 environment.get("CLAUDE_SESSION_ID")})


def confirmed_cli_hooks(cli):
    """Supply host evidence while retaining the real snapshot and policy."""
    original = cli._host_capability_snapshot

    def snapshot(workspace, *args, **kwargs):
        record_simulated_hook(workspace)
        return original(workspace, *args, **kwargs)

    return mock.patch.object(cli, "_host_capability_snapshot", side_effect=snapshot)
