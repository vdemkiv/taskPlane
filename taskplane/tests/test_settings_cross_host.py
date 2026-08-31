from taskplane.host_capabilities import HostCapabilitySnapshot, Observation
from taskplane.settings import DEFAULT_SETTINGS_PATH, load_settings


def _host(name: str) -> HostCapabilitySnapshot:
    return HostCapabilitySnapshot(
        host=name,
        host_version="1",
        workspace_fingerprint="workspace",
        session_fingerprint="session",
        observed_at="2026-08-30T00:00:00Z",
        capabilities={"model_selection": Observation(
            status="supported", source=f"host:{name}", value=["gpt-5.6-sol"])},
        effective_path="native",
        fingerprint=name,
    )


def test_effective_settings_are_portable_and_safely_observable():
    codex = load_settings(DEFAULT_SETTINGS_PATH, host_capabilities=_host("codex"))
    claude = load_settings(DEFAULT_SETTINGS_PATH, host_capabilities=_host("claude"))

    assert codex.digest == claude.digest
    assert codex.to_dict() == claude.to_dict()
    assert codex.receipt == claude.receipt
    serialized = str(codex.receipt).lower()
    assert "token" not in serialized
    assert "password" not in serialized
    assert codex.receipt["settings_digest"] == codex.digest

