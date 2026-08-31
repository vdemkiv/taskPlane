"""Atomic startup validation happens before worker-side effects."""
from __future__ import annotations

from taskplane import loop
from taskplane import preflight


def test_preflight_is_atomic_before_any_worker_or_worktree(
        tmp_path, monkeypatch):
    calls: list[str] = []

    def refused(**_kwargs):
        calls.append("preflight")
        raise preflight.PreflightError("settings digest mismatch")

    monkeypatch.setattr(preflight, "atomic_governed_startup", refused)
    monkeypatch.setattr(loop, "load", lambda _ws: {
        "parallel": True, "step": "execute", "tasks": [{
            "id": "t1", "status": "pending", "scope": ["src/**"],
            "tests": "true"}],
    })
    monkeypatch.setattr(loop.runtime_storage, "worker_locator_error",
                        lambda *_args: calls.append("worktree"))
    monkeypatch.setattr(loop.tp, "activate",
                        lambda *_args, **_kwargs: calls.append("contract"))
    result = loop.claim(str(tmp_path), "t1", str(tmp_path / "worker"))
    assert result["error"].startswith("atomic governed preflight failed")
    assert calls == ["preflight"]

