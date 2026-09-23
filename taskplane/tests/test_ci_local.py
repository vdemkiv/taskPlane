"""Exercise actual collection partitions and persisted pass/fail runner results."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from scripts import ci_local

ROOT = Path(__file__).resolve().parents[2]


def collect(tmp_path, suite='all', host=None):
    target = tmp_path/'collection.json'
    args = [sys.executable, '-m', 'pytest', 'taskplane/tests',
            '--ignore=taskplane/tests/test_dashboard_browser.py', '--collect-only', '-q',
            '--taskplane-suite', suite, '--taskplane-collection-report', str(target)]
    if host: args += ['--taskplane-host', host]
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout+result.stderr
    return json.loads(target.read_text())


def test_all_collected_tests_have_one_suite_and_short_ids(tmp_path):
    rows = collect(tmp_path)
    assert len(rows) == len({r['nodeid'] for r in rows})
    assert all(r['selected'] and len(r['nodeid']) < 1000 for r in rows)
    partitions = {name: {r['nodeid'] for r in rows if r['suite']==name} for name in ci_local.SUITES if name != 'all'}
    assert all(partitions.values())
    assert len(set().union(*partitions.values())) == sum(map(len, partitions.values())) == len(rows)
    assert len(partitions['capacity']) == len(partitions['packages']) == 2
    assert all('test_native_repeated_repair_history' in n for n in partitions['capacity'])
    assert all('test_generated_archives_match_verified_source' in n for n in partitions['packages'])
    assert any('large-unicode' in n for n in partitions['portability'])
    assert any('test_source_instruction_contract' in n for n in partitions['core'])
    assert any('test_both_native_routing_contexts' in n for n in partitions['native'])


@pytest.mark.parametrize('suite,host', [('capacity','codex'),('packages','claude')])
def test_expensive_host_selection_is_exact(tmp_path, suite, host):
    rows = collect(tmp_path, suite, host)
    selected = [r for r in rows if r['selected']]
    assert len(selected) == 1
    assert selected[0]['suite'] == suite and selected[0]['host'] == host
    assert len([r for r in rows if r['suite']==suite]) == 2


@pytest.mark.parametrize('fail', [False, True], ids=['passing', 'failing'])
def test_runner_preserves_failure_and_writes_junit(tmp_path, monkeypatch, fail):
    # Run real pytest in a tiny checkout instead of invoking the repository suite recursively.
    tests = tmp_path/'taskplane/tests'; tests.mkdir(parents=True)
    scripts = tmp_path/'scripts'; scripts.mkdir()
    shutil.copyfile(ROOT/'scripts/ci_local.py', scripts/'ci_local.py')
    shutil.copyfile(ROOT/'taskplane/tests/conftest.py', tests/'conftest.py')
    (tests/'test_probe.py').write_text('import os\ndef test_probe():\n    assert "CODEX_THREAD_ID" not in os.environ\n    assert '+str(not fail)+'\n')
    target = tmp_path/'reports/probe.xml'
    monkeypatch.setattr(ci_local, 'ROOT', tmp_path)
    monkeypatch.setenv('CODEX_THREAD_ID', 'must-not-leak')
    monkeypatch.setattr(sys, 'argv', ['ci_local', '--check', 'tests', '--junitxml', str(target)])
    assert ci_local.main() == (1 if fail else 0)
    suite = ET.parse(target).getroot().find('testsuite')
    assert suite.attrib['tests'] == '1'
    assert suite.attrib['failures'] == ('1' if fail else '0')


@pytest.mark.parametrize('args', [
    ['--check','quality','--suite','core'], ['--check','tests','--host','codex'],
    ['--check','package','--junitxml','result.xml'], ['--check','browser','--package-output','tmp'],
])
def test_incompatible_selections_fail_before_running(monkeypatch, args):
    monkeypatch.setattr(sys, 'argv', ['ci_local', *args])
    with pytest.raises(SystemExit) as error: ci_local.main()
    assert error.value.code == 2
