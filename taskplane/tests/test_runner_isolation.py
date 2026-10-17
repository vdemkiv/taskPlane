"""Standalone unittest canary: no candidate runtime imports or live-store access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from scripts import ci_local


class TestUnittestRunnerIsolation(unittest.TestCase):
    def test_import_collection_test_child_and_failure_are_isolated(self):
        with tempfile.TemporaryDirectory(prefix='taskplane-isolation-canary-') as temporary:
            base = Path(temporary)
            outside = base / 'sentinel-stores'
            for name in ('home', 'codex', 'claude', 'cache'):
                directory = outside / name
                directory.mkdir(parents=True)
                sentinel = directory / 'must-survive.json'
                sentinel.write_text(json.dumps({'sentinel': name}), encoding='utf-8')
                sentinel.chmod(0o600)
            before = ci_local.tree_map(outside)
            inherited = dict(os.environ)
            inherited.update(HOME=str(outside / 'home'), USERPROFILE=str(outside / 'home'),
                             CODEX_HOME=str(outside / 'codex'), CLAUDE_CONFIG_DIR=str(outside / 'claude'),
                             XDG_CACHE_HOME=str(outside / 'cache'), CODEX_THREAD_ID='sentinel-thread',
                             CLAUDE_SESSION_ID='sentinel-session', TASKPLANE_WORKSPACE=str(outside),
                             TASKPLANE_BROWSER_EXECUTABLE='intentional-browser-override',
                             PYTHONPATH=str(outside), PYTEST_ADDOPTS='--must-not-leak')
            root = base / 'fixture'
            tests = root / 'taskplane/tests'
            tests.mkdir(parents=True)
            scripts = root / 'scripts'
            scripts.mkdir()
            shutil.copyfile(ci_local.ROOT / 'scripts/ci_local.py', scripts / 'ci_local.py')
            shutil.copyfile(ci_local.ROOT / 'taskplane/tests/conftest.py', tests / 'conftest.py')
            conftest_before = (tests / 'conftest.py').read_bytes()
            log = ci_local.ExecutionLog(base / 'outputs', root, inherited)
            isolation = str((base / 'outputs/isolation').resolve())
            stages = base / 'stages.jsonl'
            check = f'''import json, os, subprocess, sys
from pathlib import Path
ISOLATION = Path({isolation!r})
STAGES = Path({str(stages)!r})
def check(stage):
    for name in ('HOME', 'USERPROFILE', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'XDG_CACHE_HOME', 'TMPDIR'):
        assert Path(os.environ[name]).resolve().is_relative_to(ISOLATION), (name, os.environ[name])
    assert set(k for k in os.environ if k.startswith(('CODEX_', 'CLAUDE_', 'TASKPLANE_'))) == {{'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'TASKPLANE_BROWSER_EXECUTABLE'}}
    assert os.environ['TASKPLANE_BROWSER_EXECUTABLE'] == 'intentional-browser-override'
    assert os.environ['PYTHONDONTWRITEBYTECODE'] == '1'
    assert 'PYTEST_ADDOPTS' not in os.environ
    with STAGES.open('a') as stream: stream.write(json.dumps(stage) + '\\n')
check('import')
'''
            # A parametrization expression runs at collection, before autouse
            # fixtures. The same checks run in the call and in a real child.
            passing = check + '''import pytest
@pytest.mark.parametrize('value', [check('collection')])
def test_probe(value):
    check('test')
    child = subprocess.run([sys.executable, '-c', CHILD], capture_output=True, text=True)
    assert child.returncode == 0, child.stdout + child.stderr
'''
            child = check + "check('child')\n"
            (tests / 'test_probe.py').write_text(passing.replace('CHILD', repr(child)), encoding='utf-8')
            record = log.run(ci_local.pytest_command(['taskplane/tests/test_probe.py']), label='canary/passing', pytest_run=True)
            self.assertEqual(record['status'], 'pass', record)
            self.assertEqual(record['test_verdict']['selected_count'], 1)
            seen = [json.loads(line) for line in stages.read_text().splitlines()]
            self.assertTrue({'import', 'collection', 'test', 'child'} <= set(seen), seen)
            (tests / 'test_probe.py').write_text(check + "def test_probe():\n    check('failing-test')\n    assert False, 'intentional canary failure'\n", encoding='utf-8')
            failed = log.run(ci_local.pytest_command(['taskplane/tests/test_probe.py']), label='canary/failing', pytest_run=True)
            self.assertEqual(failed['exit_code'], 1, failed)
            self.assertEqual(failed['status'], 'fail')
            self.assertIn('intentional canary failure', failed['stdout'])
            self.assertIn('junit.xml', failed)
            (tests / 'test_probe.py').write_text(check + "check('failing-collection')\nraise RuntimeError('intentional collection failure')\n", encoding='utf-8')
            collection_failed = log.run(ci_local.pytest_command(['taskplane/tests/test_probe.py']) + ['--collect-only'],
                                        label='canary/failing-collection', pytest_run=True, collect=True)
            self.assertEqual(collection_failed['status'], 'fail')
            self.assertNotEqual(collection_failed['exit_code'], 0)
            self.assertIn(b'intentional collection failure', ci_local.raw_evidence_bytes(collection_failed, 'stdout'))
            self.assertEqual(ci_local.tree_map(outside), before, 'outside store content/modes/census changed')
            self.assertEqual((tests / 'conftest.py').read_bytes(), conftest_before)
            self.assertEqual(inherited['CODEX_THREAD_ID'], 'sentinel-thread', 'caller environment mutated')
            print(json.dumps(ci_local.pack_evidence({'canary': 'precollection-isolation',
                              'assertions': ['import', 'collection', 'test', 'spawned-child', 'failure-exit',
                                             'collection-failure-exit',
                                             'browser-exception', 'outside-store-hashes-modes-census', 'conftest-unchanged'],
                              'outside_before': before, 'outside_after': ci_local.tree_map(outside),
                              'executions': log.records}), sort_keys=True))


if __name__ == '__main__':
    unittest.main()
