"""R-0007: explicit context injection and direct human deep authority."""
import ast
from pathlib import Path

import pytest

import evaluation_output
import lens
import review
import review_evidence


ROOT = Path(__file__).resolve().parents[2]


def _routing():
    return {"lenses": [{"id": "security", "name": "Security",
                         "tier": "deep", "verdict": "deep",
                         "checks": [], "looks_for": "risk"}],
            "context": {}}


def test_lens_module_never_imports_review_and_uses_explicit_note():
    tree = ast.parse((ROOT / "taskplane" / "lens.py").read_text())
    imports = {alias.name for node in ast.walk(tree)
               if isinstance(node, ast.Import) for alias in node.names}
    imports |= {node.module for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)}
    assert "review" not in imports
    note = "\nEXPLICIT-CONTEXT-NOTE\n"
    payload = lens.dispatch_briefs(_routing(), context_note=note)
    assert note in payload["deep"][0]["prompt"]


def test_automatic_deep_promotion_is_named_and_refused():
    with pytest.raises(ValueError, match="automatic-deep-creation-refused"):
        lens.dispatch_briefs(_routing(), sweep_concerns=[])


def test_human_authorization_is_exact_and_part_of_result_schema():
    authorization = {
        "schema": "taskplane.human-deep-authorization/v1",
        "source": "direct-human-command", "actor": "human:test",
        "requested_at": "2026-08-23T00:00:00Z",
        "lens_ids": ["security"], "request_receipt": "turn:1",
        "run_id": "a" * 32, "target_fingerprint": "b" * 64,
    }
    import review_evidence
    authorization["fingerprint"] = review_evidence.content_fingerprint(
        authorization)
    policy = review.human_deep_policy(authorization)
    assert policy["source"] == "direct-human-command"
    schema = evaluation_output.lens_slot_output_schema(
        human_deep_authorization=authorization)
    assert "human_deep_authorization" in schema["required"]
    assert schema["properties"]["human_deep_authorization"]["const"] == \
        authorization


def test_direct_human_command_preserves_authority_across_artifacts(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text("def changed(): return 2\n")
    start = review.start_review(
        str(tmp_path), target={"fingerprint": "target", "head": "abc"},
        graph={"meta": {"scanned_head": "abc"},
               "modules": {"src": {"files": ["src/service.py"]}},
               "edges": []},
        impact={"touched": ["src"], "impacted": {}, "unknown": [],
                "total_impacted": 0},
        diff={"files": ["src/service.py"], "changed_symbols": ["changed"],
              "patch": "+def changed(): return 2"},
        runnability={"summary": "available"},
        requirement={"id": "R-0007", "text": "safe change"},
        acceptance=["works"], contracts=["contract:review.collection"])
    deep = review.request_human_deep(
        str(tmp_path), run_id=start["run_id"], lens_ids=["security"],
        actor="human:test", request_receipt="turn:deep",
        requested_at="2026-08-23T00:00:00Z")
    authorization = deep["human_deep_authorization"]
    slot = deep["slots"][0]
    store = review_evidence.ArtifactStore(str(tmp_path))
    assert slot["human_deep_authorization"] == authorization
    assert store.read(slot["lease"])["human_deep_authorization"] == authorization
    assert store.read(slot["brief"])["human_deep_authorization"] == authorization
    assert store.read(deep["audit"])["authorization"] == authorization
