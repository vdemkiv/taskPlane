import importlib.util
from pathlib import Path

import spend


ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


eval_record = _load("command_efficiency_eval_record", "scripts/eval_record.py")
eval_skills = _load("command_efficiency_eval_skills", "scripts/eval_skills.py")


def measured(**overrides):
    values = {
        "launches": 1,
        "elapsed_ms": 300_000,
        "meaningful_wakes": 1,
        "unchanged_model_polls": 0,
        "polling_raw_tokens": 50,
        "total_raw_tokens": 10_000,
        "avoided_polling_raw_tokens": 950,
        "baseline_polling_raw_tokens": 1_000,
        "timeouts": 0,
        "cancellations": 0,
    }
    values.update(overrides)
    return values


def test_canonical_efficiency_passes_all_hard_gates():
    got = spend.command_efficiency(measured())
    assert got["schema"] == "taskplane.command-efficiency/v1"
    assert got["measurement_status"] == "measured"
    assert got["polling_token_reduction"] == .95
    assert got["polling_raw_token_share"] == .005
    assert got["gate"] == {"status": "pass", "failures": []}


def test_efficiency_hard_gates_fail_closed_without_denominators():
    got = spend.command_efficiency(measured(
        total_raw_tokens=None, baseline_polling_raw_tokens=None,
        avoided_polling_raw_tokens=None))
    assert got["measurement_status"] == "unproven"
    assert got["polling_token_reduction"] is None
    assert got["polling_raw_token_share"] is None
    assert got["gate"]["status"] == "unproven"
    assert set(got["gate"]["failures"]) == {
        "polling token reduction is unproven",
        "polling token share is unproven",
    }


def test_each_threshold_is_strict_and_unchanged_polling_fails():
    got = spend.command_efficiency(measured(
        unchanged_model_polls=1, polling_raw_tokens=101,
        avoided_polling_raw_tokens=899))
    assert got["gate"]["status"] == "fail"
    assert got["gate"]["failures"] == [
        "unchanged model polls must equal zero",
        "polling token reduction must be at least 90%",
        "polling raw tokens must be less than 1% of total raw tokens",
    ]


def test_invalid_or_boolean_counters_are_unproven_not_coerced_to_zero():
    got = spend.command_efficiency(measured(launches=True, timeouts=-1))
    assert got["launches"] is None
    assert got["timeouts"] is None
    assert got["measurement_status"] == "unproven"
    assert got["gate"]["status"] == "unproven"


def test_frozen_record_uses_bounded_driver_counters_and_provider_total_once():
    driver = {"command_efficiency": measured(total_raw_tokens=None)}
    got = eval_record._command_efficiency(
        driver_result=driver,
        cost={"available": True, "raw_total_tokens": 10_000})
    assert got["total_raw_tokens"] == 10_000
    assert got["gate"]["status"] == "pass"
    assert "prompt" not in got and "transcript" not in got


def test_frozen_record_does_not_add_provider_tokens_to_driver_total():
    driver = {"command_efficiency": measured(total_raw_tokens=10_000)}
    got = eval_record._command_efficiency(
        driver_result=driver,
        cost={"available": True, "raw_total_tokens": 10_000})
    assert got["total_raw_tokens"] == 10_000


def test_cross_host_adapter_projection_is_identical_and_bounded():
    raw = {"attempted": True, "duration_ms": 42, "status": "timeout",
           "command_efficiency": measured(elapsed_ms=42, timeouts=1)}
    assert eval_skills.command_efficiency_telemetry(raw, host="codex") == \
        eval_skills.command_efficiency_telemetry(raw, host="claude")
    got = eval_skills.command_efficiency_telemetry(raw, host="codex")
    assert set(got) == set(measured())
    assert got["timeouts"] == 1
