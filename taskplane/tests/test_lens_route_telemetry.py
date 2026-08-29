from __future__ import annotations

import copy
import json

import pytest

from taskplane import dispatch_telemetry


def _route(*, reason: str = "selected:trust-boundary") -> dict:
    return {
        "schema": "taskplane.lens-route-policy/v1",
        "stage": "evaluate",
        "selected": ["security", "architecture"],
        "dispositions": [
            {"lens": "security", "disposition": "execute_deep",
             "reason": reason},
            {"lens": "architecture", "disposition": "execute_light",
             "reason": "selected:system-boundary"},
        ],
        "route_fingerprint": "a" * 64,
    }


def _metrics() -> dict:
    return {
        "security": {
            "estimated_tokens": 900,
            "actual_tokens": 750,
            "runtime_ms": 25,
            "cache_reused": False,
            "invalidation_cause": "changed:trust-boundary",
        },
        "architecture": {
            "estimated_tokens": 800,
            "actual_tokens": 0,
            "runtime_ms": 3,
            "cache_reused": True,
            "invalidation_cause": None,
        },
    }


def test_route_telemetry_is_complete_bounded_and_redacted() -> None:
    private_target = "/Users/alice/private/taskPlane:LR-04"
    secret_reason = (
        "OPENAI_API_KEY=sk-super-private at "
        "/Users/alice/private/raw.diff\n+password = leaked"
    )
    secret_invalidation_cause = "secret=invalidation-private-value"
    route = _route(reason=secret_reason)
    metrics = _metrics()
    metrics["security"]["invalidation_cause"] = secret_invalidation_cause

    record = dispatch_telemetry.build_lens_route_telemetry(
        route,
        target=private_target,
        terminal_status="success",
        lens_metrics=metrics,
    )

    assert record["schema"] == "taskplane.lens-route-telemetry/v1"
    assert record["stage"] == "evaluate"
    assert record["selected_count"] == 2
    assert record["route_fingerprint"] == "a" * 64
    assert record["terminal_status"] == "success"
    assert record["totals"] == {
        "estimated_tokens": 1700,
        "actual_tokens": 750,
        "runtime_ms": 28,
        "cache_reused_count": 1,
        "invalidation_count": 1,
    }
    assert record["lenses"] == [
        {
            "lens": "security",
            "reason": record["lenses"][0]["reason"],
            "estimated_tokens": 900,
            "actual_tokens": 750,
            "runtime_ms": 25,
            "cache_reused": False,
            "invalidation_cause": record["lenses"][0][
                "invalidation_cause"],
        },
        {
            "lens": "architecture",
            "reason": "selected:system-boundary",
            "estimated_tokens": 800,
            "actual_tokens": 0,
            "runtime_ms": 3,
            "cache_reused": True,
            "invalidation_cause": None,
        },
    ]
    assert record["lenses"][0]["reason"].startswith("redacted-content:")
    assert record["lenses"][0]["invalidation_cause"].startswith(
        "redacted-content:")
    assert record["redactions"] == 2
    assert len(record["target_pseudonym"]) == 64
    assert dispatch_telemetry.validate_lens_route_telemetry(record) == record

    encoded = json.dumps(record, sort_keys=True)
    for private in (
        private_target, "alice", "sk-super-private", "password",
        "raw.diff", secret_invalidation_cause,
    ):
        assert private not in encoded
    assert all(len(row["reason"].encode("utf-8")) <= 512
               for row in record["lenses"])
    assert len(encoded.encode("utf-8")) <= 128 * 1024


@pytest.mark.parametrize(
    ("supplied", "canonical"),
    [
        ("pass", "success"),
        ("fail", "failed"),
        ("cancelled", "cancelled"),
        ("interruption", "interrupted"),
        ("handoff", "handoff"),
    ],
)
def test_route_telemetry_covers_every_terminal_path(
    supplied: str, canonical: str,
) -> None:
    record = dispatch_telemetry.build_lens_route_telemetry(
        _route(), target="LR-04", terminal_status=supplied,
        lens_metrics=_metrics(),
    )

    assert record["terminal_status"] == canonical
    assert dispatch_telemetry.validate_lens_route_telemetry(record) == record


@pytest.mark.parametrize(
    "mutator,match",
    [
        (lambda metrics: metrics.pop("architecture"), "exactly selected"),
        (lambda metrics: metrics["security"].pop("runtime_ms"), "closed"),
        (lambda metrics: metrics["architecture"].update(actual_tokens=1),
         "reused lens cannot record actual tokens"),
        (lambda metrics: metrics["architecture"].update(
            invalidation_cause="changed:diff"),
         "reused lens cannot record invalidation"),
        (lambda metrics: metrics["security"].update(actual_tokens=-1),
         "non-negative"),
    ],
)
def test_route_telemetry_fails_closed_on_incomplete_or_contradictory_usage(
    mutator, match: str,
) -> None:
    metrics = _metrics()
    mutator(metrics)

    with pytest.raises(dispatch_telemetry.DispatchTelemetryError, match=match):
        dispatch_telemetry.build_lens_route_telemetry(
            _route(), target="LR-04", terminal_status="success",
            lens_metrics=metrics,
        )


def test_route_telemetry_rejects_tampering_and_unsupported_route_shape() -> None:
    record = dispatch_telemetry.build_lens_route_telemetry(
        _route(), target="LR-04", terminal_status="success",
        lens_metrics=_metrics(),
    )
    tampered = copy.deepcopy(record)
    tampered["totals"]["actual_tokens"] += 1
    with pytest.raises(dispatch_telemetry.DispatchTelemetryError,
                       match="fingerprint"):
        dispatch_telemetry.validate_lens_route_telemetry(tampered)

    route = _route()
    route["dispositions"][1]["disposition"] = "not_applicable"
    with pytest.raises(dispatch_telemetry.DispatchTelemetryError,
                       match="selected dispositions"):
        dispatch_telemetry.build_lens_route_telemetry(
            route, target="LR-04", terminal_status="success",
            lens_metrics=_metrics(),
        )


@pytest.mark.parametrize(
    "shape",
    ["selected", "disposition", "metrics", "persisted"],
)
def test_route_telemetry_rejects_non_string_lens_ids(shape: str) -> None:
    route = _route()
    metrics = _metrics()
    if shape == "selected":
        route["selected"][0] = 7
    elif shape == "disposition":
        route["dispositions"][0]["lens"] = 7
    elif shape == "metrics":
        metrics[7] = metrics.pop("security")
    else:
        record = dispatch_telemetry.build_lens_route_telemetry(
            route, target="LR-04", terminal_status="success",
            lens_metrics=metrics,
        )
        record["lenses"][0]["lens"] = 7
        material = {key: value for key, value in record.items()
                    if key != "fingerprint"}
        record["fingerprint"] = dispatch_telemetry.content_fingerprint(
            material)
        with pytest.raises(dispatch_telemetry.DispatchTelemetryError,
                           match="bounded lowercase lens id"):
            dispatch_telemetry.validate_lens_route_telemetry(record)
        return

    with pytest.raises(dispatch_telemetry.DispatchTelemetryError,
                       match=("exactly selected" if shape == "metrics" else
                              "bounded lowercase lens id")):
        dispatch_telemetry.build_lens_route_telemetry(
            route, target="LR-04", terminal_status="success",
            lens_metrics=metrics,
        )


def test_route_telemetry_reserves_redaction_prefix_for_provenance() -> None:
    raw_reason = "redacted-content:manual"
    record = dispatch_telemetry.build_lens_route_telemetry(
        _route(reason=raw_reason), target="LR-04", terminal_status="success",
        lens_metrics=_metrics(),
    )

    assert record["lenses"][0]["reason"] != raw_reason
    assert record["lenses"][0]["reason"].startswith("redacted-content:")
    assert record["redactions"] == 1
    assert dispatch_telemetry.validate_lens_route_telemetry(record) == record

    malformed = copy.deepcopy(record)
    malformed["lenses"][0]["reason"] = raw_reason
    material = {key: value for key, value in malformed.items()
                if key != "fingerprint"}
    malformed["fingerprint"] = dispatch_telemetry.content_fingerprint(
        material)
    with pytest.raises(dispatch_telemetry.DispatchTelemetryError,
                       match="not privacy-safe"):
        dispatch_telemetry.validate_lens_route_telemetry(malformed)


def test_route_telemetry_rejects_unbounded_usage_and_artifacts() -> None:
    metrics = _metrics()
    metrics["security"]["estimated_tokens"] = \
        dispatch_telemetry.MAX_LENS_ROUTE_TOKENS + 1
    with pytest.raises(dispatch_telemetry.DispatchTelemetryError,
                       match="token bound"):
        dispatch_telemetry.build_lens_route_telemetry(
            _route(), target="LR-04", terminal_status="success",
            lens_metrics=metrics,
        )

    route = _route(reason="selected:" + "x" * (128 * 1024))
    record = dispatch_telemetry.build_lens_route_telemetry(
        route, target="LR-04", terminal_status="success",
        lens_metrics=_metrics(),
    )
    assert len(record["lenses"][0]["reason"].encode("utf-8")) <= 512
    assert len(json.dumps(record).encode("utf-8")) < \
        dispatch_telemetry.MAX_LENS_ROUTE_ARTIFACT_BYTES
