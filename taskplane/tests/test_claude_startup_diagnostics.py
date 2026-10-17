"""Production-controller error boundaries, with explicitly injected fixture failures."""
import json

import pytest

from taskplane import workflow as w, claude_startup_recovery as recovery
from taskplane.tests.test_claude_interactive_recovery import Interactive
from taskplane.tests.test_worker_runtime import setup, reserve


@pytest.mark.parametrize('failure', [
    w.Refusal('stale_checkpoint', 'Exact selected binding changed.'),
    ValueError('private evidence content'), TypeError('wrong shape'), KeyError('private field'),
    OSError('private path'), IndexError('missing entry'), AttributeError('wrong object'),
    json.JSONDecodeError('private JSON', '{', 1)])
def test_selected_startup_error_preserves_exact_refusal_and_database(tmp_path, monkeypatch, failure):
    host = Interactive(tmp_path, monkeypatch, count=1)
    host.launch()
    state = host.controller.report(diagnostics=False)
    before = host.controller._path().read_bytes()
    def selected(*args, **kwargs):
        raise failure
    def unrelated(*args, **kwargs):
        raise AssertionError('Selected recovery must not mutate unrelated denial observations.')
    monkeypatch.setattr(recovery, 'inspect_reconciliation', selected)
    monkeypatch.setattr(host.controller, '_refresh_denials_locked', unrelated)
    with pytest.raises(w.Refusal) as caught:
        host.controller.worker(state['run'], 'reconcile-startup', revision=state['revision'], grant=host.grant)
    if isinstance(failure, w.Refusal):
        assert caught.value is failure
    else:
        assert caught.value.reason == 'invalid_evidence'
        assert caught.value.detail == 'Retained native startup recovery evidence is malformed or unavailable.'
    assert host.controller._path().read_bytes() == before


def test_claude_recovery_on_codex_reports_unsupported_without_a_workaround(tmp_path):
    controller, state = setup(tmp_path, count=1)
    item = reserve(controller, state)
    before = controller._path().read_bytes()
    with pytest.raises(w.Refusal) as caught:
        controller.worker(state['run'], 'reconcile-startup', revision=state['revision'],
                          grant=item['grant']['grant_id'])
    assert caught.value.reason == 'unsupported_authority'
    assert controller._path().read_bytes() == before
