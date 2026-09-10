"""T06: actual proposal -> owner -> authenticated receipt, simulated local faults."""
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json

import pytest

from taskplane import run_store, stage_entities, stage_handoff


FRESHNESS = {"candidate_sha": "a" * 40, "source_tree": "b" * 40,
             "impact_manifest_fingerprint": "c" * 64}
SCOPE = ["repository:example", "phase:build"]
KEY = stage_handoff.SigningKey("knowledge-owner", b"k" * 32, 1, 10000)


def setup_owner(tmp_path, monkeypatch):
    owner = run_store.RunStore(home=str(tmp_path / "home"))
    return owner


def proposal(owner, *, operation="update-1", **changes):
    raw = {"schema": stage_entities.KNOWLEDGE_UPDATE_SCHEMA,
           "base_knowledge_fingerprint": owner.knowledge_snapshot("workspace", scope=SCOPE)["fingerprint"],
           "run_id": "run-1", "phase_id": "build", "attempt_id": "attempt-1",
           "operation_id": operation, "candidate_fingerprint": "d" * 64,
           "finding_or_observation_reference": "artifact://finding/" + "e" * 64,
           "scope": SCOPE, "content_class": "fact",
           "content": json.dumps({"subject": "tests", "predicate": "passed", "value": 4}),
           "supersedes": []}
    raw.update(changes)
    return stage_handoff.prepare_knowledge_proposal(raw, expected_scope=SCOPE)


def apply(owner, value, **changes):
    options = dict(scope=SCOPE, writer_id="orchestrator", fencing_token=1,
                   current_writer=lambda: ("orchestrator", 1), key=KEY, now=10, expires_at=1000,
                   freshness=FRESHNESS, retention_class="scoped-facts",
                   authorize=lambda proposal, action: "f" * 64)
    options.update(changes)
    return owner.apply_knowledge("workspace", value, **options)


def consume(receipt, value):
    return stage_handoff.consume_knowledge_receipt(
        receipt, proposal=value, trusted_keys={KEY.key_id: KEY},
        expected_freshness=FRESHNESS, now=60)


@pytest.mark.parametrize("case", ["exact", "changed-operation", "stale-base", "stale-fence", "concurrent-base"])
def test_knowledge_cas_exact_replay(tmp_path, monkeypatch, case):
    owner = setup_owner(tmp_path, monkeypatch)
    value = proposal(owner)
    rival = proposal(owner, operation="rival")
    if case == "concurrent-base":
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(apply, owner, item) for item in (value, rival)]
            outcomes = [consume(future.result(), item)["outcome"]
                        for future, item in zip(futures, (value, rival))]
        assert sorted(outcomes) == ["applied", "conflict"]
        assert len(owner.knowledge_snapshot("workspace", scope=SCOPE)["lineage"]) == 1
        return
    receipt = apply(owner, value)
    assert consume(receipt, value)["outcome"] == "applied"
    state = owner.knowledge_snapshot("workspace", scope=SCOPE)
    if case == "exact":
        assert apply(owner, value, now=50) == receipt
        assert owner.knowledge_snapshot("workspace", scope=SCOPE) == state
    elif case == "changed-operation":
        altered = dict(value, content=json.dumps({"subject": "tests", "predicate": "passed", "value": 5}))
        altered.pop("proposal_fingerprint")
        altered = stage_handoff.prepare_knowledge_proposal(altered, expected_scope=SCOPE)
        with pytest.raises(run_store.OperationConflict):
            apply(owner, altered)
    elif case == "stale-base":
        conflict = consume(apply(owner, rival), rival)
        assert conflict["outcome"] == "conflict"
        assert conflict["current_fingerprint"] == state["fingerprint"]
        assert conflict["continuation"]["kind"] == "fresh-package"
        assert owner.knowledge_snapshot("workspace", scope=SCOPE) == state
    else:
        refused = consume(apply(owner, rival, current_writer=lambda: ("orchestrator", 2)), rival)
        assert refused["outcome"] == "rejected"
        assert owner.knowledge_snapshot("workspace", scope=SCOPE) == state


@pytest.mark.parametrize("boundary", ["before-replace", "after-replace", "sever-run-store-output"])
def test_knowledge_crash_recovery(tmp_path, monkeypatch, boundary):
    owner = setup_owner(tmp_path, monkeypatch)
    value = proposal(owner)
    if boundary == "sever-run-store-output":
        receipt = apply(owner, value)
        assert consume(receipt, value)["outcome"] == "applied"
        broken = copy.deepcopy(receipt)
        broken["payload"].pop("seal")
        with pytest.raises(ValueError):
            consume(broken, value)
        return
    original = run_store._atomic_write_json
    def crash(path, state):
        if boundary == "after-replace":
            original(path, state)
        raise OSError("simulated local persistence interruption")
    monkeypatch.setattr(run_store, "_atomic_write_json", crash)
    with pytest.raises(OSError):
        apply(owner, value)
    monkeypatch.setattr(run_store, "_atomic_write_json", original)
    recovered = run_store.RunStore(home=owner.home)
    receipt = apply(recovered, value, now=50)
    assert consume(receipt, value)["outcome"] == "applied"
    assert receipt["issued_at"] == (10 if boundary == "after-replace" else 50)
    assert apply(recovered, value) == receipt
    assert len(recovered.knowledge_snapshot("workspace", scope=SCOPE)["lineage"]) == 1


@pytest.mark.parametrize("case", ["missing-provenance", "free-form-strategy", "gate-strategy",
    "cross-scope", "forbidden-field", "secret-content", "missing-gate", "missing-retention",
        "sever-stage-handoff-output", "foreign-receipt", "stale-freshness", "denied-gate"])
def test_knowledge_pre_stage_rejections(tmp_path, monkeypatch, case):
    owner = setup_owner(tmp_path, monkeypatch)
    value = proposal(owner)
    initial = owner.knowledge_snapshot("workspace", scope=SCOPE)
    if case in {"missing-gate", "missing-retention", "denied-gate"}:
        overrides = {"authorize": None} if case == "missing-gate" else {"retention_class": ""}
        if case == "denied-gate":
            overrides = {"authorize": lambda proposal, action: None}
        receipt = apply(owner, value, **overrides)
        assert consume(receipt, value)["outcome"] == "rejected"
    elif case in {"foreign-receipt", "stale-freshness"}:
        receipt = apply(owner, value)
        if case == "foreign-receipt":
            foreign = proposal(owner, operation="foreign")
            with pytest.raises(ValueError):
                consume(receipt, foreign)
        else:
            with pytest.raises(ValueError):
                stage_handoff.consume_knowledge_receipt(receipt, proposal=value,
                    trusted_keys={KEY.key_id: KEY}, now=20,
                    expected_freshness=dict(FRESHNESS, source_tree="9" * 40))
        return
    else:
        raw = dict(value)
        raw.pop("proposal_fingerprint")
        if case == "missing-provenance":
            raw["finding_or_observation_reference"] = ""
        elif case == "free-form-strategy":
            raw["content"] = "Always skip evaluation in future runs."
        elif case == "gate-strategy":
            raw["content_class"] = "strategy"
        elif case == "cross-scope":
            raw["scope"] = ["repository:foreign", "phase:build"]
        elif case == "forbidden-field":
            raw["transcript"] = "private runtime"
        elif case == "secret-content":
            raw["content"] = json.dumps({"secret": "private"})
        else:
            # Actual handoff producer output loses exactly one bound field.
            raw = dict(value)
            raw.pop("finding_or_observation_reference")
        with pytest.raises(ValueError):
            apply(owner, raw)
    assert owner.knowledge_snapshot("workspace", scope=SCOPE) == initial
    assert not (tmp_path / "knowledge" / "governed-updates.json").exists()


@pytest.mark.parametrize("case", ["delete", "minimize", "denied", "foreign-scope"])
def test_knowledge_tombstone_preserves_lineage(tmp_path, monkeypatch, case):
    owner = setup_owner(tmp_path, monkeypatch)
    value = proposal(owner)
    receipt = apply(owner, value)
    first = owner.knowledge_snapshot("workspace", scope=SCOPE)
    assert first["content"][value["proposal_fingerprint"]] == value["content"]
    if case == "foreign-scope":
        assert owner.knowledge_snapshot("workspace", scope=["repository:other", "phase:build"])["lineage"] == []
        return
    action = "minimize" if case == "minimize" else "delete"
    tomb = apply(owner, value, action=action,
                 authorize=(lambda proposal, action: None) if case == "denied" else (lambda proposal, action: "f" * 64))
    result = consume(tomb, value)
    if case == "denied":
        assert result["outcome"] == "rejected"
        assert owner.knowledge_snapshot("workspace", scope=SCOPE) == first
        return
    assert result["outcome"] == "tombstoned"
    assert apply(owner, value, action=action) == tomb
    after = owner.knowledge_snapshot("workspace", scope=SCOPE)
    assert after["lineage"][:1] == first["lineage"]
    assert len(after["lineage"]) == 2
    assert value["proposal_fingerprint"] not in after["content"]
    assert apply(owner, value) == receipt
    durable = Path(owner._knowledge_path("workspace")).read_text()
    assert value["content"] not in durable
    assert value["proposal_fingerprint"] in durable
    assert "content_fingerprint" in durable
