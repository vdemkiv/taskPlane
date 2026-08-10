"""v2.3.1 #6 — the per-task contract-slot guarantee must actually be WIRED
into dispatch briefs, not just implemented in the kernel. A review run as
shipped must give each parallel lens agent a unique slot."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lens


ROUTING = {
    "lenses": [
        {"id": "security", "name": "Security", "tier": "deep",
         "looks_for": "x", "checks": []},
        {"id": "qa", "name": "QA", "tier": "deep", "looks_for": "y",
         "checks": []},
        {"id": "i18n", "name": "i18n", "tier": "sweep"},
    ],
    "context": {"changed_files": 3},
}


def test_each_deep_brief_has_a_unique_task_slot():
    d = lens.dispatch_briefs(ROUTING, base="HEAD")
    slots = [b["task_slot"] for b in d["deep"]]
    assert len(set(slots)) == len(slots) and all(slots)
    # the slot is also on the contract spec the host activates
    assert [b["contract"]["task_slot"] for b in d["deep"]] == slots


def test_brief_prompt_exports_the_slot_before_contract():
    d = lens.dispatch_briefs(ROUTING, base="HEAD")
    for b in d["deep"]:
        assert f"export TASKPLANE_TASK={b['task_slot']}" in b["prompt"]


def test_sweep_brief_has_its_own_slot():
    d = lens.dispatch_briefs(ROUTING, base="HEAD")
    sw = d["sweep"]
    assert sw["task_slot"] == "lens-sweep"
    assert "export TASKPLANE_TASK=lens-sweep" in sw["prompt"]
