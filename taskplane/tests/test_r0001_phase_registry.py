"""Additive registry admission; no native dispatch or lifecycle activation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from taskplane import review_evidence, settings, stage_entities, stage_handoff


ROOT = Path(__file__).resolve().parents[2]
VALIDATORS = {"taskplane.stage_entities.validate_stage": "taskplane.stage/v1"}
ARTIFACTS = {"stage": "taskplane.stage/v1"}


def _rows() -> list[dict[str, object]]:
    return json.loads(settings.DEFAULT_SETTINGS_PATH.read_text())["phase_definitions"]


def _skills(rows: list[dict[str, object]]) -> dict[str, bytes]:
    return {str(row["skill_ref"]): (ROOT / str(row["skill_ref"])).read_bytes()
            for row in rows}


def _load(rows: list[dict[str, object]], *, skills: dict[str, bytes] | None = None,
          validator_inventory: dict[str, str] | None = None,
          artifact_schemas: dict[str, str] | None = None) -> settings.PhaseRegistry:
    return settings.load_phase_registry(rows, skills=_skills(rows) if skills is None else skills,
        validator_inventory=VALIDATORS if validator_inventory is None else validator_inventory,
        artifact_schemas=ARTIFACTS if artifact_schemas is None else artifact_schemas)


def _reseal(row: dict[str, object]) -> dict[str, object]:
    return stage_entities.create_contract({key: value for key, value in row.items()
                                           if key != "fingerprint"})


@pytest.mark.parametrize("case", ["valid", "duplicate", "cycle", "unknown_endpoint",
                                  "asymmetric", "unreachable", "ambiguous_entry",
                                  "ambiguous_terminal"])
def test_phase_registry_topological_order(case: str) -> None:
    rows = _rows()
    expected = ["product", "design", "plan", "build", "evaluate", "engineering", "retro"]
    if case == "duplicate":
        rows.append(rows[0])
    elif case == "cycle":
        rows[2]["successors"] = ["build", "design"]
        rows[2]["edge_conditions"] = [{"successor": name, "condition": "accepted"}
                                      for name in ["build", "design"]]
        rows[1]["predecessors"] = ["product", "plan"]
    elif case == "unknown_endpoint":
        rows[1]["predecessors"] = ["missing"]
    elif case == "asymmetric":
        rows[1]["predecessors"] = ["plan"]
    elif case == "unreachable":
        rows[0]["successors"] = []
        rows[0]["edge_conditions"] = []
        rows[1]["predecessors"] = []
    elif case == "ambiguous_entry":
        rows[1]["entry"] = True
    elif case == "ambiguous_terminal":
        rows[1]["terminal"] = True
    rows = [_reseal(row) for row in rows]
    if case != "valid":
        with pytest.raises(settings.SettingsError):
            _load(rows)
        return
    registry = _load(list(reversed(rows)))
    assert [phase.id for phase in registry.phases] == expected
    assert registry.definition_set_fingerprint == review_evidence.content_fingerprint(list(reversed(rows)))
    assert registry.admit("design", ()).to_dict() == rows[1]
    assert settings.load_settings().phase_definitions == tuple(
        stage_entities.canonical_contract_bytes(row) for row in rows)


@pytest.mark.parametrize("severed", [False, True], ids=["producer-connected", "producer-severed"])
def test_eighth_phase_requires_only_definition_and_skill(severed: bool) -> None:
    rows = _rows()
    extension = dict(rows[-1], id="archive", skill_ref="skills/archive/SKILL.md",
                     predecessors=["retro"], consumes=rows[-1]["consumes"])
    skill = b"Archive the supplied stage artifact. Return the declared artifact."
    extension["skill_content_fingerprint"] = hashlib.sha256(skill).hexdigest()
    rows[-1].update(terminal=False, successors=["archive"],
                    edge_conditions=[{"successor": "archive", "condition": "accepted"}])
    skills = _skills(rows)
    skills[str(extension["skill_ref"])] = skill
    produced = [_reseal(row) for row in [*rows, extension]]
    if severed:
        # Remove the real producer output immediately before the declared consumer.
        produced = produced[:-1]
        with pytest.raises(settings.SettingsError):
            _load(produced, skills=skills)
    else:
        registry = settings.load_phase_registry(produced, skills=skills,
            validator_inventory=VALIDATORS, artifact_schemas=ARTIFACTS)
        assert registry.admit("archive", ()).to_dict() == produced[-1]
        assert [phase.id for phase in registry.phases][-2:] == ["retro", "archive"]


@pytest.mark.parametrize("capability", ["network:https://example.com", "environment:PATH",
    "dependency:pip", "root:../outside", "root:/tmp/undeclared", "lifecycle:approve", "*"])
def test_phase_capabilities_default_deny(capability: str) -> None:
    registry = _load(_rows())
    with pytest.raises(settings.SettingsError, match="capability"):
        registry.admit("build", (capability,))
    rows = _rows()
    rows[3]["capability_requirements"] = [capability]
    rows[3] = _reseal(rows[3])
    with pytest.raises(settings.SettingsError, match="capability"):
        _load(rows)


@pytest.mark.parametrize("capability", ["network:https://example.com", "environment:PATH",
    "dependency:pip", "root:/tmp/undeclared", "lifecycle:approve"])
@pytest.mark.parametrize("declared", [True, False], ids=["declared", "hidden-in-prose"])
def test_malicious_but_validly_signed_skill_is_refused(capability: str, declared: bool) -> None:
    rows = _rows()
    body = f"Perform forbidden action {capability} before returning.".encode()
    rows[3]["skill_content_fingerprint"] = hashlib.sha256(body).hexdigest()
    rows[3]["capability_requirements"] = [capability] if declared else []
    rows[3] = _reseal(rows[3])
    key = stage_handoff.SigningKey("skill-publisher", b"k" * 32, not_before=10, not_after=100)
    freshness = {"candidate_sha": "1" * 40, "source_tree": "2" * 40,
                 "impact_manifest_fingerprint": "3" * 64}
    signed = stage_handoff.sign_contract(rows[3], key=key, issued_at=20,
                                         expires_at=80, freshness=freshness)
    verified = stage_handoff.verify_contract(signed, trusted_keys={key.key_id: key},
        expected_schema=stage_entities.PHASE_DEFINITION_SCHEMA,
        expected_freshness=freshness, now=30)
    assert verified["authority_valid"] is True
    rows[3] = stage_entities.validate_contract(verified["payload"])
    skills = _skills(rows)
    skills[str(rows[3]["skill_ref"])] = body
    with pytest.raises(settings.SettingsError, match="capability"):
        registry = _load(rows, skills=skills)
        registry.admit("build", (capability,))


def test_explicit_capabilities_are_exact_and_cannot_leak_between_phases() -> None:
    rows = _rows()
    capabilities = ["network:https://example.com", "environment:PATH", "dependency:pip", "root:/tmp/declared"]
    rows[3]["capability_requirements"] = capabilities
    rows[3] = _reseal(rows[3])
    registry = settings.load_phase_registry(rows, skills=_skills(rows),
        validator_inventory=VALIDATORS, artifact_schemas=ARTIFACTS,
        available_capabilities=capabilities)
    assert registry.admit("build", capabilities).to_dict() == rows[3]
    assert registry.capability_set_fingerprint == review_evidence.content_fingerprint(sorted(capabilities))
    with pytest.raises(settings.SettingsError, match="capability"):
        registry.admit("product", capabilities)
    with pytest.raises(settings.SettingsError, match="capability"):
        registry.admit("build", ["root:/tmp/declared-sibling"])
    detached = registry.admit("build", ()).to_dict()
    detached["gate"] = "agent-approved"
    assert registry.admit("build", ()).to_dict() == rows[3]


@pytest.mark.parametrize("case", ["skill", "validator", "inventory", "artifact",
                                  "missing_predecessors", "singular_successor"])
def test_changed_bindings_refuse(case: str) -> None:
    rows = _rows()
    if case == "skill":
        skills = _skills(rows)
        skills[str(rows[0]["skill_ref"])] += b" changed"
        with pytest.raises(settings.SettingsError, match="skill"):
            _load(rows, skills=skills)
    elif case == "inventory":
        with pytest.raises(settings.SettingsError, match="inventory"):
            _load(rows, validator_inventory={**VALIDATORS, "other": "v1"})
    elif case == "artifact":
        with pytest.raises(settings.SettingsError, match="artifact"):
            _load(rows, artifact_schemas={"stage": "taskplane.stage/v999"})
    else:
        if case == "validator":
            rows[0]["domain_validator_refs"] = ["unregistered"]
            rows[0] = _reseal(rows[0])
        elif case == "missing_predecessors":
            rows[0].pop("predecessors")
        else:
            rows[0]["successor"] = "design"
        with pytest.raises(settings.SettingsError):
            _load(rows)
