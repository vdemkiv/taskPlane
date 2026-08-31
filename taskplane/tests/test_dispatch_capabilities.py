"""Capability-bound host routing reaches only supported child arguments."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import host_capabilities as hc  # noqa: E402
import taskplane_lite as tp  # noqa: E402


def snapshot(host="codex", *, model="supported", effort="supported",
             aliases=("gpt-5-codex",), efforts=("low", "high")):
    observations = {
        "model_selection": hc.Observation(
            model, "host-receipt:model", "high"),
        "supported_model_aliases": hc.Observation(
            "supported", "host-receipt:model-aliases", "high",
            value=list(aliases)),
        "effort_selection": hc.Observation(
            effort, "host-receipt:effort", "high"),
        "supported_effort_values": hc.Observation(
            "supported", "host-receipt:efforts", "high",
            value=list(efforts)),
    }
    return hc.probe_snapshot(
        ".", host=host, install_context="personal",
        native_installed=True, bridge_configured=False,
        observations=observations, now="2026-08-15T00:00:00Z")


class TestDispatchResolution(unittest.TestCase):
    def test_supported_route_passes_exact_model_and_effort(self):
        route = hc.resolve_dispatch_route(
            snapshot(), tier="deep", requested_model="gpt-5-codex",
            requested_effort="high", mode="strict")
        self.assertEqual(route["resolution"], "exact")
        self.assertEqual(route["effective_model"], "gpt-5-codex")
        self.assertEqual(route["effective_effort"], "high")
        self.assertEqual(route["passed_arguments"], {
            "model": "gpt-5-codex", "reasoning_effort": "high"})
        self.assertFalse(route["exact_route_verified"],
                         "verification needs a host receipt, not a plan")

    def test_unsupported_route_falls_back_without_sending_arguments(self):
        route = hc.resolve_dispatch_route(
            snapshot(model="unsupported", effort="unsupported"),
            tier="deep", requested_model="gpt-5-codex",
            requested_effort="high", mode="warn")
        self.assertEqual(route["resolution"], "unsupported_fallback")
        self.assertEqual(route["effective_model"], None)
        self.assertEqual(route["effective_effort"], None)
        self.assertEqual(route["passed_arguments"], {})
        self.assertTrue(route["reason"])

    def test_strict_mode_blocks_before_dispatch(self):
        route = hc.resolve_dispatch_route(
            snapshot(effort="unknown"), tier="deep",
            requested_model="gpt-5-codex", requested_effort="high",
            mode="strict")
        self.assertEqual(route["resolution"], "blocked")
        self.assertTrue(route["block_before_dispatch"])
        self.assertEqual(route["passed_arguments"], {})

    def test_cross_provider_model_is_never_sent(self):
        route = hc.resolve_dispatch_route(
            snapshot(aliases=("claude-opus-4",)), tier="deep",
            requested_model="claude-opus-4", requested_effort="high",
            mode="warn")
        self.assertEqual(route["effective_model"], None)
        self.assertNotIn("model", route["passed_arguments"])
        self.assertIn("foreign provider", route["reason"])

    def test_corrupt_capability_value_fails_closed(self):
        broken = snapshot(aliases=("gpt-5-codex",))
        observations = dict(broken.capabilities)
        observations["supported_model_aliases"] = hc.Observation(
            "supported", "corrupt-fixture", "low", value={"bad": True})
        broken = hc.probe_snapshot(
            ".", host="codex", install_context="personal",
            native_installed=True, bridge_configured=False,
            observations=observations)
        route = hc.resolve_dispatch_route(
            broken, tier="deep", requested_model="gpt-5-codex",
            requested_effort="high", mode="warn")
        self.assertEqual(route["effective_model"], None)
        self.assertIn("alias evidence", route["reason"])

    def test_host_receipt_can_verify_the_exact_route(self):
        route = hc.resolve_dispatch_route(
            snapshot(), tier="deep", requested_model="gpt-5-codex",
            requested_effort="high", mode="strict",
            observed={"model": "gpt-5-codex", "reasoning_effort": "high",
                      "host_observed": True})
        self.assertTrue(route["exact_route_verified"])
        self.assertEqual(route["observed_model"], "gpt-5-codex")

    def test_dispatch_payload_uses_only_resolved_arguments(self):
        with mock.patch.dict(os.environ, {
                "TASKPLANE_MODEL_STANDARD": "gpt-5-codex",
                "TASKPLANE_REASONING_STANDARD": "high"}, clear=False):
            fields = tp.dispatch_fields(
                "step", "tp-executor", "t1", "deep",
                capability_snapshot=snapshot(), enforcement_mode="warn")
        self.assertEqual(fields["model"], "gpt-5-codex")
        self.assertEqual(fields["reasoning_effort"], "high")
        self.assertEqual(fields["dispatch_route"]["resolution"], "exact")

    def test_dispatch_payload_drops_unproved_arguments(self):
        with mock.patch.dict(os.environ, {
                "TASKPLANE_MODEL_STANDARD": "gpt-5-codex",
                "TASKPLANE_REASONING_STANDARD": "high"}, clear=False):
            fields = tp.dispatch_fields(
                "step", "tp-executor", "t1", "deep",
                capability_snapshot=snapshot(
                    model="unknown", effort="unknown"),
                enforcement_mode="warn")
        self.assertIsNone(fields["model"])
        self.assertIsNone(fields["reasoning_effort"])
        self.assertEqual(fields["dispatch_route"]["resolution"],
                         "unsupported_fallback")


if __name__ == "__main__":
    unittest.main()
