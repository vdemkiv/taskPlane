"""T14 local boundaries: real producers, explicitly simulated host/authority.

No native journey, independent judgment, sign-off or publication is evidenced.
The publication port records local calls only; downstream approval is a named
consumer fixture, never substituted for actual Engineering/PR producer output.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from taskplane import agent_runtime, delivery_ports, dispatch_telemetry, retro
from taskplane import release_evidence, producer_observation, wave_metrics
from taskplane.tests.test_r0001_agent_runtime import _setup
from taskplane.tests.test_r0001_telemetry_seal import sources, FRESHNESS


def _retro_inputs(tmp_path, monkeypatch):
    inputs = sources(tmp_path / "upstream", monkeypatch)
    runtime, dispatch, calls = _setup(tmp_path / "retro")
    telemetry_ref = runtime.store.put("attempt-telemetry", dispatch_telemetry.produce_attempt_telemetry(inputs))
    evidence = wave_metrics.produce_terminal_evidence(dispatch_ledger=inputs.ledger,
        clock=delivery_ports.FakeClock(wall_time=120), candidate_fingerprint="a" * 64,
        evaluator_summary=retro.evaluator_summary([]), settings_digest="b" * 64)
    wave = wave_metrics.seal_terminal_metrics(evidence, dispatch_ledger=inputs.ledger,
        clock=delivery_ports.FakeClock(wall_time=120), candidate_fingerprint="a" * 64,
        archive_upper_bound_tokens=None)
    evidence_ref = runtime.store.put("terminal-evidence", evidence)
    metrics_ref = runtime.store.put("terminal-metrics", wave)
    definition = runtime.registry.admit("retro", ()).to_dict()
    envelope = delivery_ports.dispatch_envelope("retro", definition["role"], "T14",
        definition["model_tier"], role_instructions="Read only the sealed terminal evidence.",
        requested_model=None, requested_effort="high", settings_digest="b" * 64)
    envelope["terminal_evidence_fingerprints"] = [ref["fingerprint"] for ref in
        (telemetry_ref, evidence_ref, metrics_ref)]
    binding = {**dispatch.nonce_bindings, "phase_id": "retro", "attempt_id": "retro-attempt",
        "operation_id": "retro-op", "phase_definition_fingerprint": definition["fingerprint"],
        "sealed_package_fingerprint": agent_runtime.package_fingerprint(dispatch.package, dispatch.knowledge, envelope)}
    issued = runtime.nonce.issue(binding)
    dispatch = replace(dispatch, envelope=envelope, nonce_bindings=binding, issued=issued,
        bindings={**dispatch.bindings, **{k: v for k, v in binding.items()
            if k in dispatch.bindings and k != "deadline"}, "nonce_digest": issued.receipt["nonce_digest"],
            "skill_content_fingerprint": definition["skill_content_fingerprint"]})
    runtime.launch = lambda *args: (calls.append("launch") or "simulated-worker-1")
    runtime.continuation = lambda reason: {"kind": "hold" if reason else "evaluate", "phase_id": "retro"}
    return runtime, dispatch, calls, dict(telemetry_inputs=inputs, telemetry_ref=telemetry_ref,
        terminal_evidence_ref=evidence_ref, terminal_metrics_ref=metrics_ref)


@pytest.mark.parametrize("case", ["connected", "missing-telemetry", "missing-wave", "missing-evidence",
    "changed-telemetry", "stale-candidate", "stale-tree", "stale-impact", "foreign-run",
    "unavailable-usage", "deleted-at-effect", "missing-retro-output"])
def test_retro_requires_terminal_telemetry(tmp_path, monkeypatch, case, record_property):
    runtime, dispatch, calls, kwargs = _retro_inputs(tmp_path, monkeypatch)
    if case.startswith("missing-") and case != "missing-retro-output":
        kwargs[{"missing-telemetry": "telemetry_ref", "missing-wave": "terminal_metrics_ref",
            "missing-evidence": "terminal_evidence_ref"}[case]] = None
    elif case == "changed-telemetry":
        Path(kwargs["telemetry_ref"]["path"]).write_bytes(b"{}")
    elif case.startswith("stale-"):
        field = {"stale-candidate": "candidate_sha", "stale-tree": "source_tree",
            "stale-impact": "impact_manifest_fingerprint"}[case]
        inputs = kwargs["telemetry_inputs"]
        kwargs["telemetry_inputs"] = replace(inputs, freshness={**inputs.freshness, field: "0" * len(FRESHNESS[field])})
    elif case == "foreign-run":
        dispatch = replace(dispatch, bindings={**dispatch.bindings, "run_id": "foreign"})
    elif case == "unavailable-usage":
        inputs = sources(tmp_path / "unavailable", monkeypatch, unavailable=True)
        missing = dispatch_telemetry.produce_attempt_telemetry(inputs)
        assert missing["token_counts_when_available"] is None
        kwargs.update(telemetry_inputs=inputs, telemetry_ref=runtime.store.put("attempt-telemetry", missing))
    elif case == "deleted-at-effect":
        original = runtime.nonce.dispatch
        def sever(issued, binding, action):
            def effect():
                Path(kwargs["terminal_metrics_ref"]["path"]).unlink()
                return action()
            return original(issued, binding, effect)
        monkeypatch.setattr(runtime.nonce, "dispatch", sever)
    elif case == "missing-retro-output":
        observe = runtime.observe
        runtime.observe = lambda identity: replace(observe(identity), outputs=())
    if case == "connected":
        ref = retro.run_retro_phase(runtime, dispatch, **kwargs)
        result = retro.read_retro_phase(runtime.store, ref, **kwargs)
        assert result["runtime_result"]["phase_id"] == "retro"
        assert result["terminal_metrics_fingerprint"] == runtime.store.read(kwargs["terminal_metrics_ref"])["fingerprint"]
        assert calls == ["launch", "observe"]
    else:
        with pytest.raises((ValueError, OSError)):
            retro.run_retro_phase(runtime, dispatch, **kwargs)
        assert calls == (["launch", "observe"] if case == "missing-retro-output" else [])
    record_property("case", case)
    record_property("evidence_mode", "local-production-with-simulated-host-and-authority")


def _publication(tmp_path, monkeypatch):
    runtime, dispatch, calls, retro_args = _retro_inputs(tmp_path, monkeypatch)
    retro_ref = retro.run_retro_phase(runtime, dispatch, **retro_args)
    package = b"exact already-tested package (consumer fixture)"
    binding = dict(run_id="run-1", candidate_fingerprint="a" * 64, **FRESHNESS,
        definition_set_fingerprint=runtime.registry.definition_set_fingerprint,
        phase_definition_fingerprint=dispatch.bindings["phase_definition_fingerprint"],
        knowledge_fingerprint=dispatch.bindings["knowledge_fingerprint"],
        authority_fingerprint="f" * 64, repository_id="simulated/repository",
        protected_main_commit="a" * 40, package_sha256=hashlib.sha256(package).hexdigest(),
        version="2.20.0", tag="v2.20.0", channel="test", destination="local-simulated-sink",
        action="publish", final_signoff_fingerprint="d" * 64, protected_main_fingerprint="e" * 64,
        predecessor_fingerprint=retro_ref["fingerprint"])
    approval = dict(approval_id="human-publication-1", actor="human:simulated", action="publish",
        binding_fingerprint=delivery_ports.content_fingerprint(binding), issued_at=100, expires_at=180)
    # This is the explicitly simulated outside-model authority port. It checks
    # exact bytes against its own approval, not a worker's confirmed=True flag.
    def authority(value):
        return value["approval"] == approval and value["binding"] == binding
    grant_ref, issued = release_evidence.issue_publication_grant(runtime.store,
        nonce_source=runtime.nonce, binding=binding, approval=approval, authority_check=authority)
    effects = []
    args = dict(store=runtime.store, nonce_source=runtime.nonce, grant_ref=grant_ref, issued=issued,
        current_binding=lambda: dict(binding), authority_check=authority, package=package,
        retro_ref=retro_ref, retro_inputs=retro_args,
        publication=lambda payload, bound: effects.append((payload, bound)))
    return runtime, args, effects, binding


@pytest.mark.parametrize("case", ["connected", "sever-retro-output"])
def test_publication_grant_valid_once(tmp_path, monkeypatch, case, record_property):
    runtime, args, effects, binding = _publication(tmp_path, monkeypatch)
    if case == "sever-retro-output":
        Path(args["retro_ref"]["path"]).unlink()
        with pytest.raises((ValueError, OSError)):
            release_evidence.consume_publication_grant(**args)
        assert effects == []
    else:
        release_evidence.consume_publication_grant(**args)
        assert effects == [(args["package"], binding)]
    record_property("evidence_mode", "local-production-with-simulated-publication-authority")


@pytest.mark.parametrize("case", ["replay", "restart-replay", "reconciled-replay", "changed-package", "destination", "predecessor",
    "candidate_sha", "source_tree", "impact_manifest_fingerprint", "final-signoff", "protected-main",
    "version", "tag", "channel", "action", "foreign-run", "grant-substitution", "missing-grant",
    "expired", "revoked", "movement-at-effect", "revoked-at-effect", "deleted-grant-at-effect",
    "publisher-failure"])
def test_publication_grant_replay_substitution_destination_and_predecessor_movement_refused(
        tmp_path, monkeypatch, case, record_property):
    runtime, args, effects, binding = _publication(tmp_path, monkeypatch)
    if case in {"replay", "restart-replay", "reconciled-replay"}:
        release_evidence.consume_publication_grant(**args)
        if case == "reconciled-replay":
            grant = runtime.store.read(args["grant_ref"])
            runtime.nonce.reconcile(args["issued"], release_evidence._publication_nonce_binding(grant),
                lambda operation: "effect_free")
        if case == "restart-replay":
            source = runtime.nonce
            args["nonce_source"] = type(source)(source.store, key=b"k" * 32, clock=source.clock)
    elif case == "changed-package":
        args["package"] += b"rebuilt"
    elif case in {"grant-substitution", "missing-grant"}:
        if case == "missing-grant":
            args["grant_ref"] = None
        else:
            value = runtime.store.read(args["grant_ref"])
            value["binding"]["destination"] = "substitute"
            value["fingerprint"] = delivery_ports.content_fingerprint({k: v for k, v in value.items() if k != "fingerprint"})
            args["grant_ref"] = runtime.store.put("publication-grant", value)
    elif case == "expired":
        runtime.clock.advance(81)
    elif case == "revoked":
        args["authority_check"] = lambda value: False
    elif case.endswith("at-effect"):
        original = runtime.nonce.dispatch
        def sever(issued, nonce_binding, action):
            def effect():
                if case == "deleted-grant-at-effect":
                    Path(args["grant_ref"]["path"]).unlink()
                elif case == "revoked-at-effect":
                    monkeypatch.setitem(args, "revoked", True)
                else:
                    binding["predecessor_fingerprint"] = "0" * 64
                return action()
            return original(issued, nonce_binding, effect)
        monkeypatch.setattr(runtime.nonce, "dispatch", sever)
        if case == "revoked-at-effect":
            original_authority = args["authority_check"]
            args["authority_check"] = lambda value: not args.get("revoked") and original_authority(value)
    elif case == "publisher-failure":
        def fail(*values):
            effects.append(values)
            raise OSError("simulated publication outcome is uncertain")
        args["publication"] = fail
        with pytest.raises(OSError):
            release_evidence.consume_publication_grant(**args)
    else:
        field = {"predecessor": "predecessor_fingerprint", "final-signoff": "final_signoff_fingerprint",
            "protected-main": "protected_main_commit", "foreign-run": "run_id"}.get(case, case)
        current = dict(binding, **{field: ("0" * len(binding[field]) if
            field.endswith("fingerprint") or field in {"candidate_sha", "source_tree", "protected_main_commit"}
            else "tag" if field == "action" else "changed")})
        args["current_binding"] = lambda: current
    with pytest.raises((ValueError, OSError, producer_observation.ProducerObservationError)):
        release_evidence.consume_publication_grant(**{k: v for k, v in args.items() if k != "revoked"})
    assert len(effects) == (1 if case in {"replay", "restart-replay", "reconciled-replay", "publisher-failure"} else 0)
    record_property("case", case)
    record_property("evidence_mode", "local-production-with-simulated-publication-authority")
