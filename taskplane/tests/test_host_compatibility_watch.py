"""Offline monitor fixtures; these never certify an external scheduled wake."""
from copy import deepcopy
import hashlib
import json

import pytest

from scripts import check_host_updates as watch


def observation(stamp="2026-10-16T12:00:00+00:00"):
    return {"schema": watch.SCHEMA, "host": "codex", "status": "observed",
            "observed_at": stamp, "source": {"kind": "fixture", "reference": "fixture/host-call-1"},
            "host_version": "1.0.0", "runtime": {"complete": True,
                "member_sha256": {"taskplane/tp.py": "a" * 64, "hooks/hooks.json": "b" * 64}},
            "capabilities": {name: "supported" for name in watch.CAPABILITIES}}


@pytest.mark.parametrize("case, expected", [
    ("stable", "stable"), ("version", "changed"), ("runtime", "changed"),
    ("member_removed", "changed"), ("capability", "changed"), ("unknown", "unknown"),
    ("partial", "unknown"), ("unavailable", "unknown"), ("unsupported", "unsupported"),
    ("unsupported_capability", "unsupported"), ("failed", "failed"),
    ("missing_baseline", "unknown"), ("stale", "unknown"), ("changed_partial", "changed"),
])
def test_host_watch_observation_contract(case, expected):
    previous = observation()
    current = observation("2026-10-16T12:01:00+00:00")
    current["source"]["reference"] = "fixture/host-call-2"
    if case in {"version", "changed_partial"}:
        current["host_version"] = "1.1.0"
    if case == "runtime":
        current["runtime"]["member_sha256"]["taskplane/tp.py"] = "c" * 64
    if case == "member_removed":
        del current["runtime"]["member_sha256"]["hooks/hooks.json"]
    if case == "capability":
        current["capabilities"]["interrupt"] = "unsupported"
    if case == "unsupported_capability":
        current["capabilities"]["interrupt"] = previous["capabilities"]["interrupt"] = "unsupported"
    if case == "unknown":
        current["capabilities"]["process_exit"] = "unknown"
    if case in {"partial", "changed_partial"}:
        current["runtime"]["complete"] = False
        current["runtime"]["member_sha256"]["hooks/hooks.json"] = None
    if case in {"unavailable", "unsupported", "failed"}:
        current["status"], current["reason"] = case, "fixture observation boundary unavailable"
    if case == "missing_baseline":
        previous = None
    if case == "stale":
        current["observed_at"] = previous["observed_at"]
    before = deepcopy((current, previous))
    report = watch.compare_observations(current, previous)
    assert report["status"] == expected
    assert (current, previous) == before
    assert report["current"] == current and report["previous"] == previous
    assert report["authority_verified"] is False
    assert report["native_execution_verified"] is False
    assert report["scheduled_wake_verified"] is False and report["notification_sent"] is False
    assert report["recheck_required"] == (expected != "stable")
    if expected == "changed":
        assert report["changes"]
    if expected == "unknown" or case == "changed_partial":
        assert report["unknowns"] and not report["comparison_complete"]
    if case == "stable":
        assert not report["changes"] and not report["unknowns"] and report["comparison_complete"]


@pytest.mark.parametrize("defect", ["foreign_host", "fixture_native", "naive_time", "bad_digest",
                                   "escaping_member", "bad_capability", "missing_reference"])
def test_invalid_observations_refuse_without_modifying_history(defect):
    previous, current = observation(), observation("2026-10-16T12:01:00Z")
    if defect == "foreign_host": current["host"] = "claude"
    if defect == "fixture_native": current["source"]["kind"] = "native"
    if defect == "naive_time": current["observed_at"] = "2026-10-16T12:01:00"
    if defect == "bad_digest": current["runtime"]["member_sha256"]["taskplane/tp.py"] = "unknown"
    if defect == "escaping_member": current["runtime"]["member_sha256"]["../outside"] = "c" * 64
    if defect == "bad_capability": current["capabilities"]["identity"] = "invented"
    if defect == "missing_reference": current["source"].pop("reference")
    original = deepcopy((previous, current))
    with pytest.raises(ValueError):
        watch.compare_observations(current, previous)
    assert (previous, current) == original


def test_cli_preserves_raw_inputs_and_reports_unknown_without_baseline(tmp_path, capsys):
    path = tmp_path / "observation.json"
    raw = (json.dumps(observation(), indent=2) + "\n").encode()
    path.write_bytes(raw)
    assert watch.main(["--current", str(path)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "unknown"
    assert report["inputs"]["current"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["inputs"]["current"]["bytes"] == len(raw)
    assert path.read_bytes() == raw
    assert sorted(p.name for p in tmp_path.iterdir()) == ["observation.json"]


def test_cli_reports_failed_input_without_overwriting_it(tmp_path, capsys):
    path = tmp_path / "broken.json"
    path.write_text('{"schema": "first", "schema": "second"}', encoding="utf-8")
    before = path.read_bytes()
    assert watch.main(["--current", str(path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "failed" and "duplicate" in report["reason"]
    assert path.read_bytes() == before


def test_new_native_reference_does_not_certify_execution_or_wake():
    previous, current = observation(), observation("2026-10-16T12:01:00Z")
    for value in (previous, current): value["source"]["kind"] = "native"
    current["source"]["reference"] = "native/scheduled-run-reported-by-caller"
    report = watch.compare_observations(current, previous)
    assert report["status"] == "stable"
    assert not report["native_execution_verified"] and not report["scheduled_wake_verified"]
