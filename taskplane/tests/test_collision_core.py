"""R-0003 t04: collision registry, classification, and durable evidence."""
from __future__ import annotations

import os

import pytest

import collision
from run_store import RevisionConflict, RunStore
import storage


def test_registry_is_versioned_and_fingerprinted():
    registry = collision.load_registry()
    assert registry["schema"] == \
        "taskplane.delivery-isolation-registry/v1"
    assert registry["version"] == 1
    assert len(registry["fingerprint"]) == 64
    assert "orchestrator-supaconductor" in \
        registry["known_competitors"]["skill_namespaces"]


@pytest.mark.parametrize("kind,identity", [
    ("skill", "orchestrator-supaconductor:go"),
    ("agent", "orchestrator-supaconductor:conductor-orchestrator"),
])
def test_known_competitor_denial_names_authority_and_continuation(
        kind, identity):
    decision = collision.classify(
        kind, identity, governed=True, run_id="run-7", step="execute")
    assert decision["action"] == "deny"
    assert decision["record"] is True
    assert "run=run-7" in decision["reason"]
    assert "step=execute" in decision["reason"]
    assert identity in decision["reason"]
    assert decision["continuation"] == "tp loop next"
    assert "`tp loop next`" in decision["reason"]


@pytest.mark.parametrize("helper", [
    "docx", "dataviz", "pptx", "xlsx", "documents:documents",
    "spreadsheets:Spreadsheets", "presentations:Presentations",
    "visualize:visualize",
])
def test_non_delivery_helpers_are_silent(helper):
    decision = collision.classify(
        "skill", helper, governed=True, run_id="run", step="execute")
    assert decision["action"] == "allow"
    assert decision["silent"] is True
    assert decision["record"] is False


def test_unknown_skill_advises_normally_and_denies_in_strict_mode():
    normal = collision.classify(
        "skill", "other-plugin:maybe", governed=True, step="plan")
    strict = collision.classify(
        "skill", "other-plugin:maybe", governed=True, step="plan",
        strict=True)
    assert normal["action"] == "advise"
    assert strict["action"] == "deny"
    assert normal["registry_fingerprint"] == strict["registry_fingerprint"]
    assert normal["registry_version"] == strict["registry_version"] == 1


def test_no_governed_state_is_a_true_no_op():
    decision = collision.classify(
        "skill", "orchestrator-supaconductor:go", governed=False,
        strict=True)
    assert decision["action"] == "no_op"
    assert decision["record"] is False
    assert decision["silent"] is True


def test_advisory_observes_without_claiming_denial():
    decision = collision.classify(
        "agent", "orchestrator-supaconductor:worker", governed=True,
        run_id="run", step="execute", advisory=True)
    assert decision["action"] == "observed"
    assert decision["would_action"] == "deny"
    assert "advisory enforcement observed this only" in decision["reason"]


def test_signed_root_requires_structure_not_name(tmp_path):
    unsigned = tmp_path / "conductor"
    unsigned.mkdir()
    assert collision.discover_state_roots(str(tmp_path)) == []

    (unsigned / "metadata.json").write_text("{}", encoding="utf-8")
    (unsigned / "tracks").mkdir()
    roots = collision.discover_state_roots(str(tmp_path))
    assert [row["root"] for row in roots] == ["conductor"]
    assert roots[0]["plugin"] == "orchestrator-supaconductor"
    assert "enabledPlugins" in roots[0]["remediation"]


def test_symlinked_signature_is_not_attributed_to_workspace(tmp_path):
    external = tmp_path.parent / (tmp_path.name + "-external")
    external.mkdir()
    (external / "metadata.json").write_text("{}", encoding="utf-8")
    (external / "tracks").mkdir()
    os.symlink(external, tmp_path / "conductor")
    assert collision.discover_state_roots(str(tmp_path)) == []


def test_ledger_counts_each_disposition_and_deduplicates_identity():
    ledger = collision.empty_ledger(run_id="run")
    denied = collision.classify(
        "skill", "orchestrator-supaconductor:go", governed=True,
        run_id="run", step="execute")
    advised = collision.classify(
        "skill", "unknown:skill", governed=True, run_id="run",
        step="execute")
    ledger = collision.record(ledger, denied, observed_at=1)
    ledger = collision.record(ledger, denied, observed_at=2)
    ledger = collision.record(ledger, advised, observed_at=3)
    assert ledger["counts"]["denied_skills"] == 2
    assert ledger["counts"]["advised_invocations"] == 1
    assert len(ledger["identities"]) == 2
    assert len(ledger["events"]) == 3


def test_run_store_atomically_records_interference(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    identity = storage.resolve_repository_identity(str(workspace))
    store = RunStore(home=str(tmp_path / "home"))
    manifest = store.create(
        identity, run_id="run-1", checkout=str(workspace),
        host={"name": "claude"}, target={"kind": "workspace"})
    decision = collision.classify(
        "agent", "orchestrator-supaconductor:worker", governed=True,
        run_id="run-1", step="execute")
    ledger = collision.record(None, decision, observed_at=1)

    recorded = store.record_foreign_interference(
        "run-1", expected_revision=manifest["revision"],
        interference=ledger)
    assert recorded["foreign_interference"] == ledger
    with pytest.raises(RevisionConflict):
        store.record_foreign_interference(
            "run-1", expected_revision=manifest["revision"],
            interference=ledger)


def test_workspace_ledger_is_durable_and_bounded(tmp_path):
    decision = collision.classify(
        "skill", "unknown:skill", governed=True, run_id="run",
        step="execute")
    collision.persist(str(tmp_path), decision=decision, observed_at=1)
    collision.persist(str(tmp_path), decision=decision, observed_at=2)
    loaded = collision.load_ledger(str(tmp_path))
    assert loaded["counts"]["advised_invocations"] == 2
    assert len(loaded["identities"]) == 1
    assert os.path.exists(collision.ledger_path(str(tmp_path)))
