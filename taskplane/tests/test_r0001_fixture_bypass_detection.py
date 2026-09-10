"""T16 detection-policy tests; snippets are hostile consumer-unit inputs.

Passing these tests does not provide W01-W34 or native journey evidence.
"""
from __future__ import annotations

import json
import pytest

from taskplane import test_strategy


CASES = {
    "constructed_strategy": "strategy = {'schema': 'test-strategy/v1'}\nconsume(strategy)",
    "inserted_receipt": "result = produce()\nresult['receipt'] = {}\nconsume(result)",
    "synthetic_terminal": "terminal = {'status': 'complete'}\nconsume(terminal)",
    "copied_dispatch_permission": "permission = copy.deepcopy(produce())\nconsume(permission)",
    "fixture_rewritten_package": "package = produce()\npackage['artifacts'] = stored_fixture\nconsume(package)",
    "monkeypatched_producer": "monkeypatch.setattr(owner, 'produce', replacement)\nconsume(produce())",
    "constructed": "result = {'accepted': True}\nconsume(result)",
    "synthesized-envelope": "result = encode({'accepted': True})\nconsume(result)",
    "inserted": "result = produce()\nresult['missing'] = {}\nconsume(result)",
    "aliased-mutation": "result = produce()\nalias = result\nalias.update(missing={})\nconsume(result)",
    "arbitrary-method": "result = produce()\nresult.add_missing_artifact()\nconsume(result)",
    "copied": "result = copy.deepcopy(produce())\nconsume(result)",
    "fixture": "consume(stored_fixture)",
    "arbitrary-helper": "def hidden():\n    return {'ok': True}\nconsume(hidden())",
    "dynamic-code": "result = eval(expression)\nconsume(result)",
    "rebound-producer": "produce = lambda: {'ok': True}\nconsume(produce())",
    "copied-file": "shutil.copyfile(old, current)\nconsume(current)",
    "consumer-bytes": "current.write_bytes(b'{}')\nconsume(current)",
    "conditional": "result = produce() if enabled else {}\nconsume(result)",
    "missing-consumer": "result = produce()",
    "unparseable": "def broken(",
}


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_review_detects_arbitrary_test_code_that_fabricates_missing_producer_outputs(case, request, evidence_metadata):
    report = test_strategy.inspect_boundary_test(
        CASES[case], filename="changed_test.py", producer_api="produce", consumer_api="consume")
    assert report["boundary_eligible"] is False
    assert report["findings"], case
    assert report["scanned_files"] == ["changed_test.py"]
    assert report["source_fingerprint"]
    evidence_metadata.append(('fixture_bypass_case', json.dumps({'selector': request.node.nodeid, 'case_id': case, 'source_fingerprint': report['source_fingerprint'], 'scanned_files': report['scanned_files'], 'findings': report['findings'], 'collected': True, 'executed': True, 'outcome': 'refused', 'evidence_class': 'consumer-unit', 'boundary_eligible': False}, sort_keys=True)))


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
