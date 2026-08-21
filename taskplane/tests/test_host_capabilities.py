"""R-0006 capability foundation: configuration is not runtime evidence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import host_capabilities as hc
import taskplane_lite as lite
import tp as cli


def _obs(status: str, source: str = "host-receipt:test") -> hc.Observation:
    return hc.Observation(status=status, source=source, confidence="high",
                          reason=f"fixture says {status}")


def _repo() -> str:
    root = tempfile.mkdtemp(prefix="tp-host-cap-")
    os.makedirs(os.path.join(root, ".codex"))
    return root


def _bridge(ws: str) -> None:
    Path(ws, ".codex", "hooks.json").write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{
            "command": "python3 .taskplane/codex-hook.py session-verify"
        }]}]}}), encoding="utf-8")
    Path(ws, ".taskplane").mkdir(exist_ok=True)
    Path(ws, ".taskplane", "codex-hook.py").write_text(
        f"ENGINE = {os.path.abspath(cli.__file__)!r}\n", encoding="utf-8")


class TestCapabilitySnapshot:
    def test_duplicate_hook_paths_share_one_live_event_owner(self):
        ws = _repo()
        event = {"session_id": "session-1", "tool_use_id": "call-1",
                 "hook_event_name": "PreToolUse"}
        first = lite.claim_hook_event(
            ws, "screen", event, hook_path="native", wait_seconds=0)
        duplicate = lite.claim_hook_event(
            ws, "screen", event, hook_path="bridge", wait_seconds=0)

        assert first["execute"] is True
        assert duplicate["execute"] is False
        assert duplicate["claim_id"] == first["claim_id"]
        assert first["owner_id"] == duplicate["owner_id"]
        assert duplicate["response_class"] == "empty"

    def test_dead_hook_event_owner_can_be_recovered_once(self):
        ws = _repo()
        event = {"session_id": "session-1", "tool_use_id": "call-1",
                 "hook_event_name": "PreToolUse"}
        first = lite.claim_hook_event(
            ws, "screen", event, hook_path="native", wait_seconds=0)
        path = lite.hook_claim_journal_path(ws)
        journal = lite.load_json(path, what="test hook claims")
        journal["owners"][first["claim_id"]]["owner_pid"] = 99999999
        lite.atomic_write_json(path, journal, sort_keys=True)

        recovered = lite.claim_hook_event(
            ws, "screen", event, hook_path="bridge", wait_seconds=0)

        assert recovered["execute"] is True
        assert recovered["status"] == "recovered"
        assert recovered["owner_id"] != first["owner_id"]

    def test_runtime_hook_receipt_proves_the_loaded_session_without_repo_cwd(self):
        home = tempfile.mkdtemp(prefix="tp-host-receipt-")
        parent = tempfile.mkdtemp(prefix="tp-parent-workspace-")
        hc.record_runtime_hook_receipt(
            home, hook_path="native", observed_at=100.0,
            event={"session_id": "session-1", "tool_use_id": "call-1",
                   "hook_event_name": "PreToolUse", "cwd": parent})

        observed = hc.runtime_hook_observations(
            home, session_id="session-1", now=101.0)

        assert observed["native_plugin_hooks_loaded"].status == "supported"
        assert observed["managed_policy_permission"].status == "supported"
        assert "repository_trust" not in observed

    def test_runtime_hook_receipt_persists_for_session_and_cannot_cross_it(self):
        home = tempfile.mkdtemp(prefix="tp-host-receipt-")
        hc.record_runtime_hook_receipt(
            home, hook_path="native", observed_at=100.0,
            event={"session_id": "session-1", "tool_use_id": "call-1",
                   "hook_event_name": "PreToolUse"})

        assert hc.runtime_hook_observations(
            home, session_id="another-session", now=101.0) == {}
        assert hc.runtime_hook_observations(
            home, session_id="session-1", now=10_000.0)[
                "native_plugin_hooks_loaded"].status == "supported"

    def test_native_and_bridge_same_event_prove_exactly_once_identity(self):
        home = tempfile.mkdtemp(prefix="tp-host-receipt-")
        event = {"session_id": "session-1", "tool_use_id": "call-1",
                 "hook_event_name": "PreToolUse"}
        hc.record_runtime_hook_receipt(
            home, hook_path="native", observed_at=100.0, event=event)
        hc.record_runtime_hook_receipt(
            home, hook_path="bridge", observed_at=100.5, event=event)

        observed = hc.runtime_hook_observations(
            home, session_id="session-1", now=101.0)

        assert observed["stable_event_identity"].status == "supported"
        assert observed["repository_trust"].status == "supported"

    def test_bridge_receipt_is_scoped_to_its_repository(self):
        home = tempfile.mkdtemp(prefix="tp-host-receipt-")
        parent = tempfile.mkdtemp(prefix="tp-parent-")
        child = tempfile.mkdtemp(prefix="tp-child-")
        hc.record_runtime_hook_receipt(
            home, hook_path="bridge", observed_at=100.0,
            event={"session_id": "session-1", "tool_use_id": "call-1",
                   "hook_event_name": "PreToolUse", "cwd": parent})

        assert hc.runtime_hook_observations(
            home, session_id="session-1", workspace=child,
            now=101.0) == {}
        assert hc.runtime_hook_observations(
            home, session_id="session-1", workspace=parent,
            now=101.0)["repository_bridge_loaded"].status == "supported"

    def test_file_presence_never_claims_loaded_or_effective(self):
        ws = _repo()
        _bridge(ws)

        snapshot = hc.probe_snapshot(
            ws, host="codex", install_context="personal",
            native_installed=True, bridge_configured=True,
            observations={}, now="2026-08-14T12:00:00Z")
        view = hc.onboarding_projection(snapshot)

        assert view["install"]["status"] == "supported"
        assert view["loaded_session"]["status"] == "unknown"
        assert view["effective_path"]["value"] == "transitioning"
        assert view["ready"] is False
        assert snapshot.capability("native_plugin_hooks_loaded").status == "unknown"
        assert snapshot.capability("repository_bridge_loaded").status == "unknown"

    def test_personal_trusted_loaded_bridge_is_effective(self):
        ws = _repo()
        snapshot = hc.probe_snapshot(
            ws, host="codex", install_context="personal",
            native_installed=False, bridge_configured=True,
            observations={
                "repository_bridge_loaded": _obs("supported"),
                "repository_trust": _obs("supported"),
                "managed_policy_permission": _obs(
                    "supported", "host-policy:test"),
            }, now="2026-08-14T12:00:00Z")
        view = hc.onboarding_projection(snapshot)

        assert view["trust"]["status"] == "supported"
        assert view["managed_policy"]["status"] == "supported"
        assert view["loaded_session"]["status"] == "supported"
        assert view["effective_path"]["value"] == "bridge_effective"
        assert view["ready"] is True

    def test_native_requires_policy_and_observed_load_but_not_repo_trust(self):
        snapshot = hc.probe_snapshot(
            _repo(), host="codex", install_context="managed",
            native_installed=True, bridge_configured=False,
            observations={
                "native_plugin_hooks_loaded": _obs("supported"),
                "managed_policy_permission": _obs(
                    "supported", "managed-policy:test"),
            }, now="2026-08-14T12:00:00Z")
        view = hc.onboarding_projection(snapshot)

        assert view["trust"]["status"] == "unknown"
        assert view["effective_path"]["value"] == "native_effective"
        assert view["ready"] is True

    def test_untrusted_or_denied_managed_host_is_blocked(self):
        snapshot = hc.probe_snapshot(
            _repo(), host="codex", install_context="managed",
            native_installed=False, bridge_configured=True,
            observations={
                "repository_bridge_loaded": _obs("supported"),
                "repository_trust": _obs("unsupported", "host-trust:test"),
                "managed_policy_permission": _obs(
                    "unsupported", "managed-policy:test"),
            }, now="2026-08-14T12:00:00Z")
        view = hc.onboarding_projection(snapshot)

        assert view["trust"]["status"] == "unsupported"
        assert view["managed_policy"]["status"] == "unsupported"
        assert view["effective_path"]["value"] == "blocked"
        assert view["ready"] is False
        text = json.dumps(view).lower()
        assert "bypass" not in text
        assert "administrator" in text

    def test_contradictory_runtime_receipt_fails_closed(self):
        snapshot = hc.probe_snapshot(
            _repo(), host="codex", install_context="personal",
            native_installed=False, bridge_configured=False,
            observations={
                "native_plugin_hooks_loaded": _obs("supported"),
                "managed_policy_permission": _obs("supported"),
            }, now="2026-08-14T12:00:00Z")

        assert snapshot.capability("native_plugin_hooks_loaded").status == \
            "contradictory"
        assert hc.onboarding_projection(snapshot)["ready"] is False

    def test_snapshot_is_immutable_and_fingerprint_is_stable(self):
        kwargs = dict(
            ws=_repo(), host="codex", install_context="personal",
            native_installed=True, bridge_configured=False,
            observations={}, now="2026-08-14T12:00:00Z")
        first = hc.probe_snapshot(**kwargs)
        second = hc.probe_snapshot(**kwargs)

        assert first.fingerprint == second.fingerprint
        try:
            first.capabilities["repository_trust"] = _obs("supported")
        except TypeError:
            pass
        else:
            raise AssertionError("capability mapping is mutable")


class TestOnboardingProjection:
    def test_existing_loop_offers_advisory_continuation_without_claiming_live(
            self):
        ws = _repo()
        _bridge(ws)
        snapshot = hc.probe_snapshot(
            ws, host="codex", install_context="personal",
            native_installed=True, bridge_configured=True,
            observations={}, now="2026-08-14T12:00:00Z")
        view = hc.onboarding_projection(snapshot)

        with mock.patch.object(cli, "_existing_loop_step",
                               return_value="design_approval"):
            continued = cli._prefer_existing_loop_advisory(ws, view)

        assert continued["ready"] is False
        assert continued["effective_path"]["value"] == "transitioning"
        assert continued["next_action"] == "continue_advisory"
        assert continued["continuation"] == {
            "available": True,
            "loop_step": "design_approval",
            "status": "advisory",
            "requires": ["--advisory", "--by <human>"],
        }
        assert "start a new task only when live hook enforcement is required" \
            in continued["effective_path"]["reason"]

    def test_fresh_install_still_requires_new_session_for_live_enforcement(
            self):
        ws = _repo()
        _bridge(ws)
        snapshot = hc.probe_snapshot(
            ws, host="codex", install_context="personal",
            native_installed=True, bridge_configured=True,
            observations={}, now="2026-08-14T12:00:00Z")
        view = hc.onboarding_projection(snapshot)

        with mock.patch.object(cli, "_existing_loop_step", return_value=None):
            unchanged = cli._prefer_existing_loop_advisory(ws, view)

        assert unchanged["ready"] is False
        assert unchanged["next_action"] == "start_new_session"

    def test_loaded_native_hook_governs_a_different_managed_checkout(self):
        checkout = _repo()
        _bridge(checkout)
        home = tempfile.mkdtemp(prefix="tp-host-receipt-")
        hc.record_runtime_hook_receipt(
            home, hook_path="native",
            event={"session_id": "session-1", "tool_use_id": "call-1",
                   "hook_event_name": "PreToolUse",
                   "cwd": tempfile.mkdtemp(prefix="tp-parent-")})
        env = {"CODEX_HOME": "/tmp/codex",
               "CODEX_THREAD_ID": "session-1"}

        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cli.tp, "store_home", return_value=home), \
                mock.patch.object(cli, "_install_context",
                                  return_value="personal"):
            report = cli._onboard_report(checkout)

        caps = report["host_capabilities"]
        assert caps["effective_path"]["value"] == "native_effective"
        assert caps["ready"] is True
        trust = next(row for row in report["checks"]
                     if row["id"] == "repository_trust")
        assert trust["ok"] is True
        assert trust["detail"] == "not required for native hooks"
        workspace = next(row for row in report["checks"]
                         if row["id"] == "workspace")
        assert "continue in the current Codex task" in workspace["hint"]

    def test_codex_report_exposes_five_independent_host_states(self):
        ws = _repo()
        _bridge(ws)
        env = {
            "CODEX_HOME": "/tmp/codex",
            "TASKPLANE_NATIVE_HOOKS_LOADED": "unsupported",
            "TASKPLANE_BRIDGE_HOOKS_LOADED": "supported",
            "TASKPLANE_REPOSITORY_TRUST": "supported",
            "TASKPLANE_MANAGED_HOOK_POLICY": "supported",
        }
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cli, "_install_context",
                                  return_value="personal"):
            report = cli._onboard_report(ws)

        caps = report["host_capabilities"]
        assert set(("install", "trust", "managed_policy", "loaded_session",
                    "effective_path")).issubset(caps)
        assert caps["effective_path"]["value"] == "bridge_effective"

    def test_installer_does_not_mutate_managed_or_workspace_settings_when_denied(self):
        ws = _repo()
        managed = Path(ws, "managed-settings.json")
        managed.write_text('{"hooks":"denied","other":true}\n',
                           encoding="utf-8")
        codex = Path(ws, ".codex", "hooks.json")
        codex.write_text('{"hooks":{"SessionStart":[]},"other":true}\n',
                         encoding="utf-8")
        before_managed = managed.read_bytes()
        before_codex = codex.read_bytes()
        env = {
            "CODEX_HOME": "/tmp/codex",
            "TASKPLANE_MANAGED_HOOK_POLICY": "unsupported",
        }
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cli, "_MANAGED_SETTINGS_PATHS",
                                  (str(managed),)):
            report = cli._install_codex_hooks(ws)

        assert report["ok"] is False
        assert report["status"] == "blocked"
        assert managed.read_bytes() == before_managed
        assert codex.read_bytes() == before_codex

    def test_installer_does_not_guess_when_managed_policy_is_unknown(self):
        ws = _repo()
        managed = Path(ws, "managed-settings.json")
        managed.write_text('{"hooks":"managed"}\n', encoding="utf-8")
        codex = Path(ws, ".codex", "hooks.json")
        codex.write_text('{"hooks":{},"other":true}\n', encoding="utf-8")
        before = (managed.read_bytes(), codex.read_bytes())
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/codex"},
                             clear=True), \
                mock.patch.object(cli, "_MANAGED_SETTINGS_PATHS",
                                  (str(managed),)):
            report = cli._install_codex_hooks(ws)

        assert report["ok"] is False
        assert report["status"] == "blocked"
        assert (managed.read_bytes(), codex.read_bytes()) == before

    def test_receipt_values_are_bounded_and_invalid_values_contradict(self):
        env = {
            "TASKPLANE_REPOSITORY_TRUST": "definitely",
            "TASKPLANE_HOST_RECEIPT_REASON": "x" * 2000,
        }
        observations = hc.observations_from_environment(env)

        assert observations["repository_trust"].status == "contradictory"
        assert len(observations["repository_trust"].reason.encode()) <= 512
