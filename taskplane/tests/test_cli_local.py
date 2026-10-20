"""Supported verification controls preserve scope and existing decisions."""
import pytest

from taskplane import workflow as w
from taskplane.tests.test_workflow_delivery import _RealVerificationDelivery


@pytest.mark.parametrize('change', [
    {'revision': -1}, {'check_id': 'not-planned'}, {'history': 'outside.json'},
    {'log': 'outside.log'}, {'log': 'pkg/app.py'}, {'timeout': 0},
])
def test_verification_refusal_never_executes_or_changes_phase_authority(tmp_path, change):
    delivery = _RealVerificationDelivery(tmp_path, 'codex')
    controller, state = delivery.controller, delivery.state
    options = dict(revision=state['revision'], check_id='primary', history=delivery.history,
                   log=delivery.logs['primary'][0], timeout=120)
    options.update(change)
    before = controller._path().read_bytes()
    source = (tmp_path / 'pkg/app.py').read_bytes()
    with pytest.raises(w.Refusal):
        controller.verify(state['run'], **options)
    assert controller._path().read_bytes() == before
    assert (tmp_path / 'pkg/app.py').read_bytes() == source
    assert not (tmp_path / delivery.history).exists()


def test_verification_does_not_give_a_child_root_authority(tmp_path):
    delivery = _RealVerificationDelivery(tmp_path, 'codex')
    controller, state = delivery.controller, delivery.state
    controller.principal = 'native-child'
    with pytest.raises(w.Refusal, match='Only the root'):
        controller.verify(state['run'], revision=state['revision'], check_id='primary',
                          history=delivery.history, log=delivery.logs['primary'][0])
