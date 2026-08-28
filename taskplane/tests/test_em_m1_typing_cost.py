"""Focused evidence for M1-B typing and cost fail-closed behavior."""

from __future__ import annotations

import builtins
from pathlib import Path

import dispatch_telemetry
import lens
import lens_signals


ROOT = Path(__file__).resolve().parents[2]


def test_m03_compatibility_import_has_explicit_types_and_no_bare_ignore() -> None:
    source = (ROOT / "taskplane" / "dispatch_telemetry.py").read_text(
        encoding="utf-8"
    )

    assert "type: ignore" not in source
    assert "from .delivery_policy import DeliveryPolicyError" in source
    assert "from .delivery_ports import Clock, canonical_json, content_fingerprint" in source
    assert "from .spend import normalize_usage" in source
    assert issubclass(dispatch_telemetry.DispatchTelemetryError, ValueError)


def test_m04_routing_import_failure_fails_closed_on_deep_cap(monkeypatch) -> None:
    real_import = builtins.__import__

    def unavailable(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "lens_signals":
            raise ImportError("injected cap-provider failure")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", unavailable)

    catalog = {
        "code_extensions": [".py"],
        "deep_threshold_files": 1,
        "lenses": [
        {
            "id": f"lens-{index}",
            "name": f"Lens {index}",
            "baseline": "code",
            "globs": [],
            "deep_globs": [],
            "checks": [],
        }
        for index in range(26)
        ],
    }
    routed = lens.route(
        ["src/large_change.py"],
        catalog=catalog,
        breadth="all",
        use_signals=False,
    )

    assert routed["context"]["deep_cap"] is None
    assert routed["context"]["deep_dispatched"] == 0
    assert len(routed["lenses"]) == 26
    assert not [entry for entry in routed["lenses"] if entry["mode"] == "subagent"]
    assert all(
        "fail-closed spend control" in entry["reasons"][-1]
        for entry in routed["lenses"]
    )


def test_m04_valid_engine_cap_still_limits_deep_dispatch() -> None:
    cap = lens._deep_cap()
    assert cap == lens_signals.DEEP_CAP

    selected = [
        {"id": f"lens-{index}", "mode": "subagent", "reasons": []}
        for index in range(cap + 3)
    ]
    capped = lens._cap_deep_dispatch(selected, cap)

    assert sum(entry["mode"] == "subagent" for entry in capped) == cap
    assert sum(entry["mode"] == "inline" for entry in capped) == 3
