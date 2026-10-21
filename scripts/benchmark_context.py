"""Matched synthetic review/delivery transport; never a native-token or model-quality claim."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import time
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from taskplane.context import digest, encode
from taskplane.context_handoff import Session, consume_required
from taskplane.tests.test_context_handoff import many_artifacts


def benchmark() -> dict:
    results = {}
    for name, phase in [('delivery', 'design'), ('review', 'engineering')]:
        with tempfile.TemporaryDirectory(prefix='taskplane-context-bench-') as temp:
            workspace = Path(temp).resolve()
            state = many_artifacts(workspace)
            state['visits'][state['index']]['phase'] = phase
            records = {}
            for contract in ('bounded/v1', 'bounded/v2'):
                current = deepcopy(state); current['context_contract'] = contract
                started = time.perf_counter()
                session = Session(workspace, current)
                receipt, responses = consume_required(session)
                session.validate(receipt)
                normative = [item['body'] for item in session.items if item['kind'] in ('requirements', 'accepted-output')]
                records[contract] = {'seconds':round(time.perf_counter()-started, 6),
                    'responses':len(responses), 'bytes':sum(len(encode(row)) for row in responses),
                    'required_inputs':len(session.required), 'available_inputs':len(session.items),
                    'normative_digest':digest(normative), 'receipt_validated':True}
            assert records['bounded/v1']['normative_digest'] == records['bounded/v2']['normative_digest']
            assert records['bounded/v1']['available_inputs'] == records['bounded/v2']['available_inputs']
            results[name] = {**records, 'byte_ratio':records['bounded/v2']['bytes']/records['bounded/v1']['bytes']}
    return {'schema':'taskplane.context-benchmark/v1', 'workload':'203-input sealed Product fixture into delivery/review consumer',
            'results':results, 'native_model_tokens':'not measured', 'model_task_quality':'not measured',
            'quality_proxy':'Identical normative requirements/output digest and all supporting inputs remain addressable.',
            'historical_target':'Original canonical <=0.5 benchmark failures remain historical; this is a new matched synthetic comparison.'}


if __name__ == '__main__':
    print(json.dumps(benchmark(), indent=2))
