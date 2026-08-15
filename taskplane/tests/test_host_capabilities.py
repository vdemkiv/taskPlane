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
