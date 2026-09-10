"""Non-authoritative package-in/result-out facade over incumbent boundaries.

Callers supply admitted definitions, attempt authority, host adapters, budgets,
and continuation values. This module neither persists nor advances a phase.
Host adapters are trusted tool boundaries, not worker-supplied callables. No
network, dependency acquisition, or inherited environment adapter is exposed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Callable, Final, Mapping, Protocol, Sequence, TypeVar

if __package__:
    from . import delivery_ports, producer_observation, review_evidence, stage_values as stage_entities
else:  # Existing tp.py direct-script entry loads its loop adapter flat.
    from taskplane import delivery_ports, producer_observation, review_evidence, stage_values as stage_entities


_BINDINGS: Final[tuple[str, ...]] = (
    "run_id", "phase_id", "attempt_id", "operation_id", "candidate_fingerprint",
    "definition_set_fingerprint", "phase_definition_fingerprint", "skill_content_fingerprint",
    "validator_identities", "validator_inventory_fingerprint", "consumed_artifact_schema_versions",
    "produced_artifact_schema_versions", "capability_set_fingerprint", "sealed_package_fingerprint",
    "knowledge_fingerprint", "authority_fingerprint", "host_kind_version", "nonce_digest",
    "lease_id", "fencing_token", "deadline", "budget",
)
_T = TypeVar("_T")


class RuntimeRefusal(ValueError):
    """Safe machine-readable boundary refusal, without private diagnostics."""


class Definition(Protocol):
    def to_dict(self) -> dict[str, object]: ...


class Registry(Protocol):
    @property
    def definition_set_fingerprint(self) -> str: ...

    @property
    def capability_set_fingerprint(self) -> str: ...

    def admit(self, phase_id: str, requested_capabilities: Sequence[str]) -> Definition: ...


@dataclass(frozen=True)
class Artifact:
    artifact_class: str
    schema: str
    reference: Mapping[str, object]

    def projection(self) -> dict[str, object]:
        return {"artifact_class": self.artifact_class, "artifact_schema_version": self.schema,
                "reference": dict(self.reference)}


def package_fingerprint(artifacts: tuple[Artifact, ...], knowledge: bytes,
                        envelope: Mapping[str, object]) -> str:
    return review_evidence.content_fingerprint({
        "artifacts": [artifact.projection() for artifact in artifacts],
        "knowledge_fingerprint": hashlib.sha256(knowledge).hexdigest(),
        "dispatch_envelope": dict(envelope),
    })


@dataclass(frozen=True)
class Dispatch:
    bindings: Mapping[str, object]
    package: tuple[Artifact, ...]
    knowledge: bytes
    issued: producer_observation.IssuedAttemptNonce
    nonce_bindings: Mapping[str, object]
    envelope: Mapping[str, object]


@dataclass(frozen=True)
class Observation:
    start_identity: str | None
    progress_identity: tuple[str, ...]
    terminal_identity: str | None
    effect_state: str
    outputs: tuple[Artifact, ...]


@dataclass(frozen=True)
class PreparedDispatch:
    """Validated input only; no launch identity, terminal result, or authority."""

    dispatch: Dispatch


class ToolBoundary:
    """Admitted local capabilities only; every call rechecks the current budget.

Network and acquisition stay unavailable until a separately approved incumbent
port can enforce address pinning and content pins. This excludes DNS rebinding
as well as direct private-address requests. Environment is always explicit and
empty; these callbacks must not launch an ambient-environment subprocess.
"""

    def __init__(self, check_budget: Callable[[], None], *, registry: Registry,
                 phase_id: str, run_id: str,
                 capability: delivery_ports.TaskDispatchCapability | None) -> None:
        self._check_budget = check_budget
        self._registry = registry
        self._phase_id = phase_id
        self._run_id = run_id
        self._capability = capability

    @property
    def environment(self) -> dict[str, str]:
        return {}

    def call(self, capability: str, action: Callable[[], _T]) -> _T:
        self._check_budget()
        if capability.startswith("lifecycle:"):
            raise RuntimeRefusal("lifecycle_api_attempt")
        # Local reads compose the incumbent exact-path capability. Network,
        # environment, acquisition and arbitrary shell tools have no adapter.
        if not capability.startswith("root:") or self._capability is None:
            raise RuntimeRefusal("capability_denied")
        try:
            self._registry.admit(self._phase_id, (capability,))
            bindings = {"run_id": self._run_id, "stage": self._phase_id}
            self._capability.require("tool", "read", **bindings)
            self._capability.require("read_path", capability.removeprefix("root:"), **bindings)
        except (ValueError, delivery_ports.DeliveryPortError) as exc:
            raise RuntimeRefusal("capability_denied") from exc
        result = action()
        self._check_budget()
        return result


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise RuntimeRefusal("package_mismatch")
    return dict(value)


def _rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise RuntimeRefusal("package_mismatch")
    return [_object(row) for row in value]


@dataclass
class AgentRuntime:
    registry: Registry
    store: review_evidence.ArtifactStore
    nonce: producer_observation.AttemptNonceSource
    clock: delivery_ports.Clock
    launch: Callable[[dict[str, object], tuple[Artifact, ...], ToolBoundary], str]
    observe: Callable[[str], Observation]
    validators: Mapping[str, Callable[[Mapping[str, object]], object]]
    usage: Callable[[], Mapping[str, int | None]]
    continuation: Callable[[str | None], Mapping[str, object]]
    capability: delivery_ports.TaskDispatchCapability | None = None
    resource_limits_advisory: bool = False

    def _budget(self, bindings: Mapping[str, object]) -> None:
        limits = _object(bindings["budget"])
        consumed = self.usage()
        if set(consumed) != set(limits):
            raise RuntimeRefusal("budget_exhausted")
        for name, maximum in limits.items():
            used = consumed[name]
            if used is None:
                if self.resource_limits_advisory:
                    continue
                raise RuntimeRefusal("observation_unavailable")
            if type(maximum) is not int or type(used) is not int:
                raise RuntimeRefusal("budget_exhausted")
            if not isinstance(maximum, int) or not isinstance(used, int) or used < 0:
                raise RuntimeRefusal("budget_exhausted")
            if self.resource_limits_advisory:
                continue
            if used > maximum:
                raise RuntimeRefusal("budget_exhausted")
            if name in {"tokens", "wall_ms", "attempts"} and used >= maximum:
                raise RuntimeRefusal("budget_exhausted")
        deadline = datetime.fromisoformat(str(bindings["deadline"]).replace("Z", "+00:00"))
        if not self.resource_limits_advisory and self.clock.wall_time() >= deadline.timestamp():
            raise RuntimeRefusal("budget_exhausted")

    def _artifacts(self, artifacts: tuple[Artifact, ...], declared: object,
                   validators: object, bindings: Mapping[str, object]) -> list[dict[str, object]]:
        specifications = _rows(declared)
        counts: dict[str, int] = {}
        references: list[dict[str, object]] = []
        if not isinstance(validators, list) or not validators:
            raise RuntimeRefusal("package_mismatch")
        for artifact in artifacts:
            spec = next((row for row in specifications if row["artifact_class"] == artifact.artifact_class), None)
            if spec is None or spec["artifact_schema_version"] != artifact.schema:
                raise RuntimeRefusal("package_mismatch")
            counts[artifact.artifact_class] = counts.get(artifact.artifact_class, 0) + 1
            if counts[artifact.artifact_class] > 1 and spec.get("cardinality", "one") != "many":
                raise RuntimeRefusal("package_mismatch")
            reference = review_evidence.portable_artifact_reference(self.store, dict(artifact.reference))
            payload = _object(self.store.read(reference))
            if payload.get("schema") != artifact.schema:
                raise RuntimeRefusal("package_mismatch")
            if any(key in payload and payload[key] != bindings[key]
                   for key in ("run_id", "candidate_fingerprint")):
                raise RuntimeRefusal("package_mismatch")
            for identity in validators:
                if not isinstance(identity, str) or identity not in self.validators:
                    raise RuntimeRefusal("package_mismatch")
                self.validators[identity](payload)
            references.append(reference)
        if any(row["required"] and not counts.get(str(row["artifact_class"])) for row in specifications):
            raise RuntimeRefusal("package_mismatch")
        return references

    def prepare(self, dispatch: Dispatch) -> PreparedDispatch:
        """Admit input before the external host is asked to launch anything."""
        result = self._run(dispatch, prepare_only=True)
        if not isinstance(result, PreparedDispatch):
            raise RuntimeRefusal(str(result["reason_code"]))
        return result

    def complete(self, prepared: PreparedDispatch, observed: Observation) -> dict[str, object]:
        """Revalidate and collect from the incumbent trusted observation port.

        Callers must authenticate external observations before supplying them.
        Missing terminal observations never become an accepted result.
        """
        result = self._run(prepared.dispatch, observed=observed)
        assert isinstance(result, dict)
        return result

    def run(self, dispatch: Dispatch) -> dict[str, object]:
        """Preserved synchronous facade over the same admission and collection."""
        result = self._run(dispatch)
        assert isinstance(result, dict)
        return result

    def _run(self, dispatch: Dispatch, *, prepare_only: bool = False,
             observed: Observation | None = None) -> dict[str, object] | PreparedDispatch:
        """Validate, dispatch once, observe and return; never decide readiness.

        Missing/invalid identity fields cannot be represented by a truthful
        closed envelope and raise StageValidationError before any effects.
        Well-formed but mismatched requests return bound terminal refusals.
        """
        bindings = {key: dispatch.bindings[key] for key in _BINDINGS}
        base: dict[str, object] = {
            "schema": stage_entities.AGENT_RUNTIME_SCHEMA, **bindings,
            "start_identity": None, "progress_identity": [], "terminal_identity": None,
            "effect_state": "none", "collected_output_references": [],
            "produces_conformance": False, "knowledge_proposals": [],
            "evaluator_dispatch_eligibility": False, "retry_class": "none",
            "status": "refused", "reason_code": "package_mismatch",
            "continuation": dict(self.continuation("package_mismatch")),
        }
        base = stage_entities.create_contract(base)
        bindings = {key: base[key] for key in _BINDINGS}
        reason: str | None = None
        try:
            if set(dispatch.bindings) != set(_BINDINGS):
                raise RuntimeRefusal("package_mismatch")
            definition = self.registry.admit(str(bindings["phase_id"]), ()).to_dict()
            expected = {
                "definition_set_fingerprint": self.registry.definition_set_fingerprint,
                "capability_set_fingerprint": self.registry.capability_set_fingerprint,
                "phase_definition_fingerprint": definition["fingerprint"],
                "skill_content_fingerprint": definition["skill_content_fingerprint"],
                "validator_identities": definition["domain_validator_refs"],
                "validator_inventory_fingerprint": definition["validator_inventory_fingerprint"],
                "budget": definition["budget"],
            }
            for relation, field in (("consumes", "consumed_artifact_schema_versions"),
                                    ("produces", "produced_artifact_schema_versions")):
                expected[field] = [{key: row[key] for key in ("artifact_class", "artifact_schema_version")}
                                   for row in _rows(definition[relation])]
            if any(bindings[key] != value for key, value in expected.items()):
                raise RuntimeRefusal("package_mismatch")
            if bindings["knowledge_fingerprint"] != hashlib.sha256(dispatch.knowledge).hexdigest():
                raise RuntimeRefusal("knowledge_fingerprint_mismatch")
            if bindings["sealed_package_fingerprint"] != package_fingerprint(dispatch.package, dispatch.knowledge, dispatch.envelope):
                raise RuntimeRefusal("package_mismatch")
            nonce = self.nonce.validate(dispatch.issued, dispatch.nonce_bindings,
                enforce_deadline=not self.resource_limits_advisory)
            for key, value in dispatch.nonce_bindings.items():
                if key in bindings and key != "deadline" and bindings[key] != value:
                    raise RuntimeRefusal("package_mismatch")
            if bindings["nonce_digest"] != nonce["nonce_digest"] or bindings["host_kind_version"] != \
                    f"{nonce['host_kind']}:{nonce['host_version']}" or \
                    datetime.fromisoformat(str(bindings["deadline"]).replace("Z", "+00:00")).timestamp() != nonce["deadline"]:
                raise RuntimeRefusal("package_mismatch")
            envelope = dict(dispatch.envelope)
            if envelope.get("role") != definition["role"] or envelope.get("model_tier") != definition["model_tier"] or \
                    envelope.get("dispatch_blocked") or envelope.get("role_marker") != delivery_ports.role_marker(str(definition["role"])):
                raise RuntimeRefusal("package_mismatch")
            self._artifacts(dispatch.package, definition["consumes"], definition["domain_validator_refs"], bindings)
            self._budget(bindings)
            boundary = ToolBoundary(lambda: self._budget(bindings), registry=self.registry,
                phase_id=str(bindings["phase_id"]), run_id=str(bindings["run_id"]), capability=self.capability)
            if prepare_only:
                return PreparedDispatch(dispatch)
            base["effect_state"] = "uncertain"
            if observed is None:
                identity = self.nonce.dispatch(dispatch.issued, dispatch.nonce_bindings,
                    lambda: self.launch(envelope, dispatch.package, boundary))
                observed = self.observe(identity)
            elif self.nonce.effect_state(dispatch.nonce_bindings) not in {"dispatch_uncertain", "dispatched", "observed"}:
                raise RuntimeRefusal("observation_unavailable")
            base.update(start_identity=observed.start_identity, progress_identity=list(observed.progress_identity),
                        terminal_identity=observed.terminal_identity, effect_state=observed.effect_state)
            if not observed.start_identity or not observed.terminal_identity:
                raise RuntimeRefusal("terminal_evidence_missing")
            self.nonce.reconcile(dispatch.issued, dispatch.nonce_bindings, lambda operation: "observed")
            self._budget(bindings)
            try:
                references = self._artifacts(observed.outputs, definition["produces"], definition["domain_validator_refs"], bindings)
            except (ValueError, OSError) as exc:
                raise RuntimeRefusal("produces_nonconforming") from exc
            base.update(collected_output_references=references, produces_conformance=True,
                        evaluator_dispatch_eligibility=True, status="accepted")
        except RuntimeRefusal as exc:
            reason = str(exc)
        except (ValueError, KeyError, OSError):
            reason = "observation_unavailable" if base["effect_state"] == "uncertain" else "package_mismatch"
        if reason is not None:
            base.update(status="refused", evaluator_dispatch_eligibility=False,
                        retry_class="reconcile" if base["effect_state"] == "uncertain" else "none")
        base.update(reason_code=reason, continuation=dict(self.continuation(reason)))
        return stage_entities.create_contract({key: value for key, value in base.items() if key != "fingerprint"})
