"""T16 detection-policy tests; snippets are hostile consumer-unit inputs.

Passing these tests does not provide W01-W34 or native journey evidence.
"""
from __future__ import annotations

import pytest

from taskplane import test_strategy


CASES = {
    "constructed": "result = {'accepted': True}\nconsume(result)",
    "synthesized-envelope": "result = encode({'accepted': True})\nconsume(result)",
    "inserted": "result = produce()\nresult['missing'] = {}\nconsume(result)",
    "aliased-mutation": "result = produce()\nalias = result\nalias.update(missing={})\nconsume(result)",
    "arbitrary-method": "result = produce()\nresult.add_missing_artifact()\nconsume(result)",
    "copied": "result = copy.deepcopy(produce())\nconsume(result)",
    "fixture": "consume(stored_fixture)",
    "arbitrary-helper": "def hidden():\n    return {'ok': True}\nconsume(hidden())",
    "dynamic-code": "result = eval(expression)\nconsume(result)",
    "monkeypatched": "monkeypatch.setattr(owner, 'produce', replacement)\nconsume(produce())",
    "rebound-producer": "produce = lambda: {'ok': True}\nconsume(produce())",
    "copied-file": "shutil.copyfile(old, current)\nconsume(current)",
    "consumer-bytes": "current.write_bytes(b'{}')\nconsume(current)",
    "conditional": "result = produce() if enabled else {}\nconsume(result)",
    "missing-consumer": "result = produce()",
    "unparseable": "def broken(",
}


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_review_detects_arbitrary_test_code_that_fabricates_missing_producer_outputs(case):
    report = test_strategy.inspect_boundary_test(
        CASES[case], filename="changed_test.py", producer_api="produce", consumer_api="consume")
    assert report["boundary_eligible"] is False
    assert report["findings"], case
    assert report["scanned_files"] == ["changed_test.py"]
    assert report["source_fingerprint"]


def test_labeled_consumer_unit_fixture_is_allowed_but_never_counts_as_boundary_evidence():
    report = test_strategy.inspect_boundary_test(
        "consume({'ok': True})", filename="unit_test.py", producer_api="produce",
        consumer_api="consume", evidence_class="consumer-unit")
    assert report["allowed"] is True
    assert report["boundary_eligible"] is False
    assert report["findings"]


def test_direct_call_and_import_alias_require_independent_runtime_provenance():
    report = test_strategy.inspect_boundary_test(
        "from taskplane.owner import produce as p\nvalue = p()\nconsume(value)",
        filename="changed_test.py", producer_api="taskplane.owner.produce", consumer_api="consume")
    assert report["findings"] == []
    assert report["producer_call_sites"] == [2]
    assert report["boundary_eligible"] is False
    assert report["runtime_provenance_required"] is True


def test_compound_bypasses_are_reported_independently():
    report = test_strategy.inspect_boundary_test(
        "value = produce()\nvalue.update(missing={})\nmonkeypatch.setattr(owner, 'produce', fake)\nconsume(value)",
        filename="changed_test.py", producer_api="produce", consumer_api="consume")
    codes = {finding["code"] for finding in report["findings"]}
    assert {"artifact-mutation", "producer-substitution", "unproven-consumer-input"} <= codes


@pytest.mark.parametrize("code", [
    "consume(produce())\nconsume({})",
    "result = produce()\nresult = {}\nconsume(result)",
    "result = produce()\nif changed:\n    result = {}\nconsume(result)",
    "def test_a():\n    result = produce()\ndef test_b():\n    consume(result)",
])
def test_one_real_call_does_not_mask_another_fabricated_path(code):
    report = test_strategy.inspect_boundary_test(
        code, filename="changed_test.py", producer_api="produce", consumer_api="consume")
    assert report["findings"]
