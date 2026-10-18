"""Exercise actual collection partitions and persisted pass/fail runner results."""
import base64
from copy import deepcopy
import hashlib
import json
import os
import zlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

import pytest
from scripts import ci_local

ROOT = Path(__file__).resolve().parents[2]


def collect(tmp_path, suite='all', host=None, shard=None):
    # Keep every selection's raw inventory; an ExecutionLog starts at execution-0.
    output = Path(tempfile.mkdtemp(prefix='collection-evidence-', dir=tmp_path))
    args = [sys.executable, '-m', 'pytest', 'taskplane/tests',
            '--ignore=taskplane/tests/test_dashboard_browser.py', '--collect-only', '-q',
            '--taskplane-suite', suite]
    if host: args += ['--taskplane-host', host]
    if shard:
        args += ['--taskplane-shard-index', str(shard[0]), '--taskplane-shard-count', str(shard[1])]
    log = ci_local.ExecutionLog(output, ROOT)
    result = log.run(args, label='test-collection', pytest_run=True, collect=True)
    assert result['status'] == 'pass', result
    return result['final_collection']



def test_execution_fingerprint_tracks_candidate_inputs_without_generated_state(tmp_path):
    root = tmp_path / 'candidate'
    root.mkdir()
    source = root / 'runtime.py'
    source.write_text('answer = 1\n')
    log = ci_local.ExecutionLog(tmp_path / 'executions', root)
    first = log.run([sys.executable, '-c', 'print("checked")'], label='behavior')
    for relative in ('.git/objects/history', '.taskplane/history/record.json',
                     'outputs/unrelated.md', 'dist/archive.zip'):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('unrelated progress and generated output')
    second = log.run([sys.executable, '-c', 'print("checked")'], label='behavior')
    assert first['source_fingerprint_before'] == second['source_fingerprint_before']
    assert second['source_fingerprint_before'] == second['source_fingerprint_after']
    assert second['source_fingerprint_coverage']['kind'] == 'declared-candidate'
    source.write_text('answer = 2\n')
    changed = log.run([sys.executable, '-c', 'print("checked")'], label='behavior')
    assert changed['source_fingerprint_before'] != second['source_fingerprint_before']


def test_candidate_documentation_does_not_include_untracked_external_work(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    docs = tmp_path / 'docs'
    docs.mkdir()
    declared = docs / 'guide.md'
    declared.write_text('candidate documentation')
    subprocess.run(['git', '-C', str(tmp_path), 'add', 'docs/guide.md'], check=True)
    before = ci_local.candidate_source_map(tmp_path)
    (docs / 'external-note.md').write_text('unrelated external work')
    assert ci_local.candidate_source_map(tmp_path) == before
    declared.write_text('changed relevant documentation')
    assert ci_local.candidate_source_map(tmp_path) != before


def test_selected_symlink_directory_keeps_source_coverage_unknown(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import stat
    root = tmp_path / 'candidate'
    source = root / 'taskplane'
    source.mkdir(parents=True)
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == source or original(path))
    real_lstat = Path.lstat
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_mode=stat.S_IFLNK) if path == source else real_lstat(path))
    monkeypatch.setattr(ci_local.os, 'readlink', lambda path: 'external-source')
    log = ci_local.ExecutionLog(tmp_path / 'execution', root)
    record = log.run([sys.executable, '-c', 'print("checked")'], label='behavior')
    assert record['source_fingerprint_coverage']['kind'] == 'unknown'
    assert record['source_fingerprint_coverage']['unavailable_inputs'] == ['taskplane']


def test_explicit_candidate_inputs_report_missing_coverage(tmp_path):
    root = tmp_path / 'candidate'
    root.mkdir()
    log = ci_local.ExecutionLog(tmp_path / 'executions', root, source_inputs=['missing.py'])
    record = log.run([sys.executable, '-c', 'print("command ran")'], label='behavior')
    assert record['status'] == 'pass'
    assert record['source_fingerprint_coverage']['kind'] == 'unknown'
    assert record['source_fingerprint_coverage']['unavailable_inputs'] == ['missing.py']


def test_all_collected_tests_have_one_suite_and_short_ids(tmp_path):
    rows = collect(tmp_path)
    assert len(rows) == len({r['nodeid'] for r in rows})
    oversized = [(r['nodeid'].partition('[')[0], len(r['nodeid'])) for r in rows if len(r['nodeid']) >= 1000]
    assert not oversized, oversized
    assert all(r['selected'] for r in rows)
    partitions = {name: {r['nodeid'] for r in rows if r['suite']==name} for name in ci_local.SUITES if name != 'all'}
    assert all(partitions.values())
    assert len(set().union(*partitions.values())) == sum(map(len, partitions.values())) == len(rows)
    assert len(partitions['capacity']) >= 2 and len(partitions['packages']) >= 2
    assert any('test_native_repeated_repair_history' in n for n in partitions['capacity'])
    assert any('test_generated_archives_match_verified_source' in n for n in partitions['packages'])
    assert any('large-unicode' in n for n in partitions['portability'])
    binding = {r['nodeid'] for r in rows if r['nodeid'].startswith('taskplane/tests/test_workspace_binding.py::')}
    recovery = {r['nodeid'] for r in rows
                if '::test_extracted_packages_recover_displaced_state_through_declared_hooks[' in r['nodeid']}
    assert binding and binding <= partitions['portability']
    assert len(recovery) >= 2 and recovery <= partitions['portability']
    # Parameter data can contain another test's full node ID; only the actual
    # module/function identity determines ownership.
    platform_regressions = {r['nodeid'] for r in rows if any(name in r['nodeid'].partition('[')[0] for name in (
        'test_farm_telemetry.py::', 'test_farm_workers.py::', 'test_unbound_worker_recovery.py::',
        'test_blueprint_harness.py::', 'test_workflow_builder_live.py::',
        'test_workflow_delivery.py::test_original_source_relay',
        'test_workflow_local.py::test_nested_scope_matches_native_inventory',
        'test_exact_argv_foreign_interpreter_refused_in_execution_directory',
        'test_required_binding_allows_only_exact_installed_diagnostics',
        'test_windows_interpreter_search_respects_current_directory_policy',
        'test_missing_binding_exemptions_resolve_actual_interpreter',
        'test_valid_interpreter_identity_uses_execution_cwd',
        'test_claude_start_stop_contract_and_child_control_refusal'))}
    assert platform_regressions and platform_regressions <= partitions['portability']
    instruction_contracts = {
        'taskplane/tests/test_blueprint.py::test_selected_assets_include_transitive_prompts_and_detect_drift',
        'taskplane/tests/test_cli_delivery.py::test_both_extracted_packages_retain_cli_and_authority_boundaries',
    }
    assert instruction_contracts <= partitions['core']
    assert any('test_both_native_routing_contexts' in n for n in partitions['native'])


@pytest.mark.parametrize('suite,host', [('packages','codex'),('packages','claude')])
def test_package_host_selection_is_exact(tmp_path, suite, host):
    inventory = collect(tmp_path)
    retained = {path: path.read_bytes() for path in tmp_path.glob('collection-evidence-*/execution-0/*') if path.is_file()}
    assert retained
    expected = {row['nodeid'] for row in inventory if row['suite'] == suite and row['host'] == host}
    assert expected
    rows = collect(tmp_path, suite, host)
    assert all(path.read_bytes() == raw for path, raw in retained.items())
    assert len(list(tmp_path.glob('collection-evidence-*/execution-0/collection.json'))) == 2
    selected = [r for r in rows if r['selected']]
    assert {row['nodeid'] for row in rows} == {row['nodeid'] for row in inventory}
    assert len(selected) == len(expected)
    assert {row['nodeid'] for row in selected} == expected
    assert all(row['suite'] == suite and row['host'] == host for row in selected)


@pytest.mark.parametrize('suite,count', [('core', 4), ('core', 8), ('native', 6)])
def test_shards_cover_actual_collection_once(tmp_path, suite, count):
    baseline = {row['nodeid'] for row in collect(tmp_path, suite) if row['selected']}
    partitions = []
    for index in range(1, count + 1):
        rows = collect(tmp_path, suite, shard=(index, count))
        selected = [row for row in rows if row['selected']]
        assert selected and all(row['suite'] == suite and row['shard'] == index for row in selected)
        partitions.append({row['nodeid'] for row in selected})
    assert set().union(*partitions) == baseline
    assert sum(map(len, partitions)) == len(baseline)
    assert ci_local.shard_for(r'taskplane\tests\test_probe.py::test_a', 4) == ci_local.shard_for(
        'taskplane/tests/test_probe.py::test_a', 4)


@pytest.mark.parametrize('fail', [False, True], ids=['passing', 'failing'])
def test_runner_preserves_failure_and_writes_junit(tmp_path, monkeypatch, fail):
    # Run real pytest in a tiny checkout instead of invoking the repository suite recursively.
    tests = tmp_path/'taskplane/tests'; tests.mkdir(parents=True)
    scripts = tmp_path/'scripts'; scripts.mkdir()
    shutil.copyfile(ROOT/'scripts/ci_local.py', scripts/'ci_local.py')
    shutil.copyfile(ROOT/'taskplane/tests/conftest.py', tests/'conftest.py')
    shutil.copyfile(ROOT/'taskplane/tests/test_runner_isolation.py', tests/'test_runner_isolation.py')
    (tests/'test_probe.py').write_text('import os\ndef test_probe():\n    assert "CODEX_THREAD_ID" not in os.environ\n    assert '+str(not fail)+'\n')
    target = tmp_path/'reports/probe.xml'
    monkeypatch.setattr(ci_local, 'ROOT', tmp_path)
    monkeypatch.setenv('CODEX_THREAD_ID', 'must-not-leak')
    # Keep this probe out of the canary's own suite to avoid recursive collection.
    (tests/'pytest.ini').write_text('[pytest]\npython_files = test_probe.py\n')
    monkeypatch.setattr(sys, 'argv', ['ci_local', '--check', 'tests', '--junitxml', str(target)])
    assert ci_local.main() == (1 if fail else 0)
    suite = ET.parse(target).getroot().find('testsuite')
    assert suite.attrib['tests'] == '1'
    assert suite.attrib['failures'] == ('1' if fail else '0')


@pytest.mark.parametrize('args', [
    ['--check','quality','--suite','core'], ['--check','tests','--host','codex'],
    ['--check','package','--junitxml','result.xml'], ['--check','browser','--package-output','tmp'],
    ['--check','quality','--shard-index','1','--shard-count','4'],
    ['--check','tests','--shard-index','1'], ['--check','tests','--shard-count','4'],
    ['--check','tests','--shard-index','0','--shard-count','4'],
    ['--check','tests','--shard-index','5','--shard-count','4'],
    ['--check','tests','--shard-index','1','--shard-count','0'],
])
def test_incompatible_selections_fail_before_running(monkeypatch, args):
    monkeypatch.setattr(sys, 'argv', ['ci_local', *args])
    with pytest.raises(SystemExit) as error: ci_local.main()
    assert error.value.code == 2


def test_partition_includes_hostless_packages_browser_and_detects_omission():
    rows = [
        {'nodeid': 'test.py::test_core', 'suite': 'core', 'host': None},
        {'nodeid': 'test.py::test_packages[codex]', 'suite': 'packages', 'host': 'codex'},
        {'nodeid': 'test.py::test_packages[claude]', 'suite': 'packages', 'host': 'claude'},
        {'nodeid': 'test.py::test_packages_hostless', 'suite': 'packages', 'host': None},
        {'nodeid': 'browser.py::test_browser', 'suite': 'browser', 'host': None},
    ]
    actual = ci_local.matrix_selections(rows)
    assert ci_local.verify_partition(rows, actual)['status'] == 'pass'
    assert actual['packages/hostless'] == ['test.py::test_packages_hostless']
    del actual['packages/hostless']
    assert ci_local.verify_partition(rows, actual)['status'] == 'fail'
    actual = ci_local.matrix_selections(rows)
    actual['packages/codex'].append(rows[0]['nodeid'])
    assert ci_local.verify_partition(rows, actual)['status'] == 'fail'


@pytest.mark.parametrize('outcome,xfail', [('skipped', None), ('passed', 'expected failure'), ('failed', None)])
def test_required_skip_xfail_and_failure_cannot_pass(outcome, xfail):
    record = {'exit_code': 0, 'profile': {'selected_nodeids': ['probe::test'], 'reports': [
        {'nodeid': 'probe::test', 'when': 'call', 'outcome': outcome, 'wasxfail': xfail, 'longrepr': ''}]},
        'junit.xml': '<testsuite><testcase name="test" /></testsuite>'}
    assert ci_local.test_verdict(record)['status'] == 'fail'


def optional_record(node='taskplane/tests/test_review_remediation.py::test_windows_interpreter_search_respects_current_directory_policy[False]',
                    reason='Windows executable search policy', *, platform='darwin', parameters=None):
    return {'id': 0, 'label': 'targeted/LOCAL-X', 'status': 'pass', 'exit_code': 0,
        'collection': [{'nodeid': node, 'selected': True, 'suite': ci_local.suite_for(node), 'host': None}],
        'profile': {'selected_nodeids': [node], 'platform_capabilities': {
            'os_name': 'nt' if platform == 'win32' else 'posix', 'sys_platform': platform,
            'open_dir_fd': platform != 'win32', 'O_DIRECTORY': platform != 'win32',
            'O_NOFOLLOW': platform != 'win32', 'O_NONBLOCK': platform != 'win32', 'mkfifo': platform != 'win32'},
            'node_evidence': {node: {'parameters': parameters or {}}}, 'reports': [
                {'nodeid': node, 'when': 'setup', 'outcome': 'skipped', 'wasxfail': None,
                 'longrepr': reason, 'skip_evidence': {'reason': reason}}]},
        'junit.xml': '<testsuite><testcase name="test"><skipped/></testcase></testsuite>'}


def test_optional_platform_skip_is_distinct_and_cannot_cover_required_assertion():
    record = optional_record()
    verdict = ci_local.test_verdict(record)
    assert verdict['status'] == 'pass' and len(verdict['optional_platform_skips']) == 1
    node = record['profile']['selected_nodeids'][0]
    assert verdict['selected_count'] == verdict['collected_count'] == 1
    spec = {'id': 'LOCAL-X', 'assertions': ['required assertion']}
    assert ci_local.required_coverage(spec, [record], {}, {'LOCAL-X:1': [node]})['status'] == 'fail'
    partitions = ci_local.matrix_selections(record['collection'])
    assert ci_local.verify_partition(record['collection'], partitions)['status'] == 'pass'
    partitions['portability'] = []
    assert ci_local.verify_partition(record['collection'], partitions)['status'] == 'fail'


@pytest.mark.parametrize('change', ['unknown-node', 'forged-prefix', 'wrong-platform', 'missing-capability',
                                   'missing-selected', 'missing-collected', 'extra-outcome'])
def test_optional_skip_needs_exact_evidence(change):
    record = optional_record()
    if change == 'unknown-node':
        record = optional_record(node='taskplane/tests/test_review_remediation.py::test_new_test')
    elif change == 'forged-prefix':
        record = optional_record(reason='optional-platform: Windows executable search policy')
    elif change == 'wrong-platform':
        record = optional_record(platform='win32')
    elif change == 'missing-capability':
        del record['profile']['platform_capabilities']['O_DIRECTORY']
    elif change == 'missing-selected': record['profile']['selected_nodeids'] = []
    elif change == 'missing-collected': record['collection'] = []
    else: record['profile']['reports'].append({'nodeid': 'foreign::test', 'when': 'call', 'outcome': 'passed'})
    assert ci_local.test_verdict(record)['status'] == 'fail'


@pytest.mark.parametrize('node,reason,platform,params', [
    ('test_workspace_binding.py::test_unsupported_binding_cli_is_structured_and_preserves_files[native-bind]',
     'Native unsupported-runtime check runs where primitives are absent', 'darwin', {'capability': 'native'}),
    ('test_native_workflow_cli.py::test_unsupported_claude_context_refuses_through_declared_hooks',
     'Exercises a host without Claude invocation transport', 'linux', {}),
    ('test_workflow_local.py::test_control_state_remains_private_under_permissive_umask[local]',
     'POSIX mode bits and umask; Windows uses ACLs', 'win32', {'profile': 'local'}),
    ('test_claude_flow.py::test_cowork_cli_and_hooks_share_selected_root_without_scratch_store',
     'Strict workspace binding requires descriptor-relative no-follow directory operations', 'win32', {}),
])
def test_exact_optional_capability_rules(node, reason, platform, params):
    record = optional_record('taskplane/tests/' + node, reason, platform=platform, parameters=params)
    assert ci_local.test_verdict(record)['status'] == 'pass'
    if params:
        record['profile']['node_evidence'][record['profile']['selected_nodeids'][0]]['parameters'] = {}
        # Only rule-specific parameters gate optionality, not incidental parameters.
        if 'capability' in params or 'bad' in params:
            assert ci_local.test_verdict(record)['status'] == 'fail'


WINDOWS_CAPABILITY_CASES = [('test_native_worker_dispatch.py::test_claude_start_stop_contract_and_child_control_refusal[None-user-True]',
  'Native transcript identity requires no-follow reads',
  {'background': True, 'completion_defect': None, 'delivery': 'user'}),
 ('test_workspace_binding.py::test_native_targets_refuse_symlink_components[False-True]',
  'Strict workspace binding requires descriptor-relative no-follow directory operations',
  {'bound': True, 'directory': False}),
 ('test_reopen_checkpoint_consent.py::test_sec01_reopen_transport_cannot_record_approval_or_continue[observed-v2]',
  'Accepting original-source relay evidence requires secure descriptor-relative reads',
  {'consent_transport': 'observed-v2'}),
 ('test_claude_dispatch_order.py::test_missing_transcript_call_requires_current_automatic_root_provenance[profile]',
  'Interactive Claude fixtures require native no-follow transcript reads',
  {'defect': 'profile'}),
 ('test_cross_host_compatibility.py::test_claude_child_message_reports_supported_handback_without_delivery',
  'Interactive Claude fixtures require native no-follow transcript reads',
  {}),
 ('test_worker_runtime.py::test_denial_lifecycle_ingestion_survives_cache_capacity[after_denial-explicit-1024]',
  'Interactive Claude fixtures require native no-follow transcript reads',
  {'prior_events': 1024, 'timing': 'after_denial', 'variant': 'explicit'}),
 ('test_workflow_host.py::test_denial_direct_resume_and_adapter_checks_are_read_only[budget]',
  'Interactive Claude fixtures require native no-follow transcript reads',
  {'defect': 'budget'}),
 ('test_host_capabilities.py::test_recovery_continuation_refuses_changed_snapshot[duplicate]',
  'Requires no-follow nonblocking reader',
  {'change': 'duplicate'}),
 ('test_controller_storage.py::test_public_recovery_requires_receipt_and_preserves_original_provenance',
  'Restoration requires no-follow directory descriptors.',
  {}),
 ('test_farm_workers.py::test_handback_requires_actual_claim_and_observed_terminal',
  'Native transcript identity requires no-follow reads',
  {}),
 ('test_unbound_worker_recovery.py::test_recovery_with_unrelated_file_preserves_history_and_allows_fresh_attempt',
  'Transcript interpretation requires nonblocking no-follow reads',
  {}),
 ('test_workspace_binding.py::test_setup_denies_structured_writes_or_unbound_children[Read-args1-child1]',
  'Strict workspace binding requires descriptor-relative no-follow directory operations',
  {}),
 ('test_native_workflow_cli.py::test_both_native_routing_contexts_complete_seven_explicit_gates[claude]',
  'Successful Claude context transport requires macOS or Linux; refusal is tested separately',
  {'host': 'claude'})]


@pytest.mark.parametrize('node,reason,parameters', WINDOWS_CAPABILITY_CASES,
                         ids=[case[0].split('::')[0] for case in WINDOWS_CAPABILITY_CASES])
def test_reviewed_windows_capability_skips_remain_explicit(node, reason, parameters):
    record = optional_record('taskplane/tests/' + node, reason, platform='win32', parameters=parameters)
    verdict = ci_local.test_verdict(record)
    assert verdict['status'] == 'pass' and len(verdict['optional_platform_skips']) == 1
    # Setup skips legitimately have no call report, but cannot prove an assertion.
    assert not any(r['when'] == 'call' for r in record['profile']['reports'])
    selected = record['profile']['selected_nodeids'][0]
    assert ci_local.required_coverage({'id': 'LOCAL-X', 'assertions': ['required']}, [record], {},
                                     {'LOCAL-X:1': [selected]})['status'] == 'fail'
    record['profile']['platform_capabilities'].update(os_name='posix', sys_platform='darwin',
        open_dir_fd=True, O_DIRECTORY=True, O_NOFOLLOW=True, O_NONBLOCK=True)
    assert ci_local.test_verdict(record)['status'] == 'fail'


@pytest.mark.parametrize('case', ['test_native_worker_dispatch.py::', 'test_workspace_binding.py::',
    'test_reopen_checkpoint_consent.py::test_sec01_reopen_transport_cannot_record_approval_or_continue', 'test_host_capabilities.py::'],
    ids=['native-reader', 'bound-only', 'transport-only', 'recovery-reader'])
@pytest.mark.parametrize('change', ['unknown-node', 'reason', 'malformed-capability', 'missing-capability',
                                   'missing-call', 'duplicate-call', 'missing-selected', 'xfail'])
def test_windows_skip_rules_do_not_hide_missing_or_forged_execution(case, change):
    node, reason, parameters = next(row for row in WINDOWS_CAPABILITY_CASES if row[0].startswith(case))
    record = optional_record('taskplane/tests/' + node, reason, platform='win32', parameters=parameters)
    profile = record['profile']; report = profile['reports'][0]
    if change == 'unknown-node':
        report['nodeid'] = profile['selected_nodeids'][0] = record['collection'][0]['nodeid'] = 'taskplane/tests/test_new.py::test_new'
    elif change == 'reason': report['skip_evidence']['reason'] += ' (unreviewed)'
    elif change == 'malformed-capability': profile['platform_capabilities']['O_NOFOLLOW'] = 0
    elif change == 'missing-capability': del profile['platform_capabilities']['O_NOFOLLOW']
    elif change == 'missing-call': report['outcome'] = 'passed'
    elif change == 'duplicate-call':
        profile['reports'].extend([{'nodeid': nodeid, 'when': 'call', 'outcome': 'passed', 'wasxfail': None}
                                   for nodeid in profile['selected_nodeids'] * 2])
    elif change == 'missing-selected': profile['selected_nodeids'] = []
    else: report['wasxfail'] = 'expected'
    assert ci_local.test_verdict(record)['status'] == 'fail'


@pytest.mark.parametrize('bound', [False, None, 1, 'True'])
def test_unbound_or_untyped_symlink_case_is_never_optional(bound):
    node, reason, _ = next(row for row in WINDOWS_CAPABILITY_CASES if row[0].startswith('test_workspace_binding.py::'))
    record = optional_record('taskplane/tests/' + node, reason, platform='win32', parameters={'bound': bound})
    assert ci_local.test_verdict(record)['status'] == 'fail'


@pytest.mark.parametrize('transport', ['observed-v1', 'observed-v3-direct', 'observed-v3-reaction', 'native-capture', None])
def test_consent_transport_skip_cannot_exempt_other_transport(transport):
    node, reason, _ = next(row for row in WINDOWS_CAPABILITY_CASES if row[0].startswith('test_reopen_checkpoint_consent.py::test_sec01_reopen_transport_cannot_record_approval_or_continue'))
    record = optional_record('taskplane/tests/' + node, reason, platform='win32', parameters={'consent_transport': transport})
    assert ci_local.test_verdict(record)['status'] == 'fail'


@pytest.mark.parametrize('host', ['codex', 'openai', 'unknown', None])
def test_claude_journey_skip_does_not_exempt_other_hosts(host):
    node, reason, _ = WINDOWS_CAPABILITY_CASES[-1]
    record = optional_record('taskplane/tests/' + node, reason, platform='win32', parameters={'host': host})
    assert ci_local.test_verdict(record)['status'] == 'fail'


@pytest.mark.parametrize('change', ['skip', 'missing-call', 'missing-node', 'xfail', 'error', 'duplicate-call'])
def test_required_binding_never_accepts_incomplete_execution(change):
    record = optional_record(); node = record['profile']['selected_nodeids'][0]
    record['profile']['reports'] = [{'nodeid': node, 'when': phase, 'outcome': 'passed', 'wasxfail': None}
                                   for phase in ('setup', 'call', 'teardown')]
    registry = {'LOCAL-X:1': [node]}; spec = {'id': 'LOCAL-X', 'assertions': ['required']}
    assert ci_local.required_coverage(spec, [record], {}, registry)['status'] == 'pass'
    if change == 'skip': record = optional_record()
    elif change == 'missing-call': del record['profile']['reports'][1]
    elif change == 'missing-node': record['profile']['selected_nodeids'] = []
    elif change == 'xfail': record['profile']['reports'][1]['wasxfail'] = 'expected'
    elif change == 'error': record['profile']['reports'][-1]['outcome'] = 'failed'
    else: record['profile']['reports'].append(record['profile']['reports'][1].copy())
    assert ci_local.required_coverage(spec, [record], {}, registry)['status'] == 'fail'


@pytest.mark.parametrize('outcome', ['passed', 'failed', 'skipped', 'xfail'])
def test_real_unittest_subtests_retain_provenance_and_required_outcomes(tmp_path, outcome):
    root = tmp_path / 'source'
    tests = root / 'taskplane/tests'; tests.mkdir(parents=True)
    scripts = root / 'scripts'; scripts.mkdir()
    shutil.copyfile(ROOT / 'scripts/ci_local.py', scripts / 'ci_local.py')
    shutil.copyfile(ROOT / 'taskplane/tests/conftest.py', tests / 'conftest.py')
    action = {'passed': 'self.assertEqual(value, 1)', 'failed': 'self.fail("subtest failure")',
              'skipped': 'self.skipTest("required subtest skip")', 'xfail': 'pytest.xfail("subtest xfail")'}[outcome]
    (tests / 'test_subtest_probe.py').write_text(
        'import unittest\nimport pytest\n'
        'class TestProbe(unittest.TestCase):\n'
        '    def test_subtests(self):\n'
        '        for value in (0, 1):\n'
        '            with self.subTest("actual case", value=value):\n'
        '                if value == 1:\n'
        f'                    {action}\n')
    log = ci_local.ExecutionLog(tmp_path / 'evidence', root)
    record = log.run(ci_local.pytest_command(['taskplane/tests/test_subtest_probe.py']),
                     label='targeted/LOCAL-X', pytest_run=True)
    expected = 'pass' if outcome == 'passed' else 'fail'
    assert record['status'] == expected, record['test_verdict']
    reports = record['profile']['reports']
    subtests = [row for row in reports if ci_local.is_subtest_report(row)]
    assert len(subtests) == 2
    assert [row['subtest'] for row in subtests] == [
        {'msg': 'actual case', 'kwargs': {'value': str(value)}} for value in (0, 1)]
    assert all(row['report_type'] == '_pytest.subtests.SubtestReport' for row in subtests)
    enclosing = [row for row in reports if not ci_local.is_subtest_report(row)]
    assert [row['when'] for row in enclosing] == ['setup', 'call', 'teardown']
    assert all(row['report_type'] == '_pytest.reports.TestReport' for row in enclosing)
    assert subtests[-1]['outcome'] == ('skipped' if outcome == 'xfail' else outcome)
    assert (subtests[-1]['wasxfail'] is not None) == (outcome == 'xfail')
    node = record['profile']['selected_nodeids'][0]
    spec, registry = {'id': 'LOCAL-X', 'assertions': ['required']}, {'LOCAL-X:1': [node]}
    # Subtest outcomes themselves must refuse coverage, even if an upstream
    # process or enclosing report claims success.
    checked = deepcopy(record)
    checked['exit_code'] = 0
    checked['status'] = 'pass'
    for row in checked['profile']['reports']:
        if not ci_local.is_subtest_report(row):
            row['outcome'], row['wasxfail'] = 'passed', None
    assert ci_local.test_verdict(checked)['status'] == expected
    assert ci_local.required_coverage(spec, [checked], {}, registry)['status'] == expected


@pytest.mark.parametrize('change', ['duplicate-call', 'missing-call', 'missing-type', 'ordinary-type',
                                   'missing-context', 'wrong-phase', 'optional-subtest-skip'])
def test_subtests_never_replace_enclosing_call_or_relax_evidence(change):
    record = optional_record()
    node = record['profile']['selected_nodeids'][0]
    phases = [{'nodeid': node, 'when': phase, 'outcome': 'passed', 'wasxfail': None}
              for phase in ('setup', 'call', 'teardown')]
    subtest = dict(phases[1], report_type='_pytest.subtests.SubtestReport',
                   subtest={'msg': 'actual case', 'kwargs': {'value': '1'}})
    record['profile']['reports'] = [*phases, subtest]
    spec, registry = {'id': 'LOCAL-X', 'assertions': ['required']}, {'LOCAL-X:1': [node]}
    assert ci_local.test_verdict(record)['status'] == 'pass'
    assert ci_local.required_coverage(spec, [record], {}, registry)['status'] == 'pass'
    if change == 'duplicate-call': record['profile']['reports'].append(phases[1].copy())
    elif change == 'missing-call': record['profile']['reports'].remove(phases[1])
    elif change == 'missing-type': del subtest['report_type']
    elif change == 'ordinary-type': subtest['report_type'] = '_pytest.reports.TestReport'
    elif change == 'missing-context': del subtest['subtest']
    elif change == 'wrong-phase': subtest['when'] = 'setup'
    else:
        subtest['outcome'] = 'skipped'
        subtest['skip_evidence'] = {'reason': 'Windows executable search policy'}
    verdict = ci_local.test_verdict(record)
    assert verdict['status'] == 'fail'
    assert not verdict['optional_platform_skips']
    assert ci_local.required_coverage(spec, [record], {}, registry)['status'] == 'fail'


def test_missing_scenarios_and_empty_collection_fail_closed():
    assert ci_local.required_coverage({'id': 'LOCAL-UNBOUND', 'assertions': ['required']}, [], {})['status'] == 'fail'
    assert ci_local.test_verdict({'exit_code': 0, 'profile': {'selected_nodeids': []}}, collect=True)['status'] == 'fail'


def junit_evidence_record():
    node = 'taskplane/tests/test_probe.py::test_probe'
    payload = {'schema': 'taskplane.remediation-measurement/v1', 'subcheck': 'LOCAL-GRAPH',
               'scenario': 'LOCAL-GRAPH:1', 'kind': 'graph', 'samples': []}
    suite = ET.Element('testsuite')
    case = ET.SubElement(suite, 'testcase', classname='taskplane.tests.test_probe', name='test_probe')
    props = ET.SubElement(case, 'properties')
    ET.SubElement(props, 'property', name='remediation_measurement', value=json.dumps(payload))
    return {'id': 0, 'status': 'pass', 'exit_code': 0, 'label': 'targeted/LOCAL-GRAPH',
            'collection': [{'nodeid': node, 'selected': True}],
            'junit.xml': ET.tostring(suite, encoding='unicode'),
            'profile': {'selected_nodeids': [node], 'reports': [
                {'nodeid': node, 'when': phase, 'outcome': 'passed', 'wasxfail': None}
                for phase in ('setup', 'call', 'teardown')]}}


@pytest.mark.parametrize('encoding', [None, 'utf-8', 'utf-16'])
@pytest.mark.parametrize('doctype', [
    '<!DOCTYPE testsuite>',
    '<!DOCTYPE testsuite [<!ENTITY a "expanded"><!ENTITY b "&a;&a;&a;">]>',
    '<!DOCTYPE testsuite [<!ENTITY external SYSTEM "file:///etc/passwd">]>',
    '<!DOCTYPE testsuite SYSTEM "https://invalid.example/external.dtd">',
    '<!DOCTYPE testsuite [<!ENTITY % external SYSTEM "https://invalid.example/external.dtd">%external;]>',
], ids=['doctype', 'nested-entities', 'file-entity', 'external-dtd', 'parameter-entity'])
def test_junit_rejects_hostile_declarations(encoding, doctype):
    record = junit_evidence_record()
    registry = {'LOCAL-GRAPH:1': record['profile']['selected_nodeids']}
    assert ci_local.test_verdict(record)['status'] == 'pass'
    hostile = doctype + record['junit.xml']
    if encoding is None:
        record['junit.xml'] = hostile
    else:
        raw = ('<?xml version="1.0" encoding="' + encoding + '"?>' + hostile).encode(encoding)
        # A benign text preview must never override the actual process bytes.
        record['raw_files'] = {'junit.xml': {'base64': base64.b64encode(raw).decode('ascii'),
            'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}}
    verdict = ci_local.test_verdict(record)
    assert verdict['status'] == 'fail' and verdict['problems'] == ['invalid JUnit']


@pytest.mark.parametrize('document', [
    '<testsuite><testcase name="test &amp; &#233;">&lt;!DOCTYPE harmless&gt;</testcase></testsuite>',
    '<testsuite><!-- <!DOCTYPE harmless> --><testcase><![CDATA[<!ENTITY harmless>]]></testcase></testsuite>',
    '<testsuite xmlns="urn:junit" xmlns:p="urn:property"><testcase p:value="é">text</testcase></testsuite>',
])
@pytest.mark.parametrize('encoding', [None, 'utf-8', 'utf-16'])
def test_junit_parser_preserves_ordinary_xml(document, encoding):
    raw = document if encoding is None else document.encode(encoding)
    actual = ci_local.parse_junit_xml(raw)
    expected = ET.fromstring(raw)
    assert ET.tostring(actual) == ET.tostring(expected)


@pytest.mark.parametrize('document', ['<testsuite>', '<testsuite>&undeclared;</testsuite>', ''])
def test_malformed_junit_evidence_fails_closed(document):
    record = junit_evidence_record()
    record['junit.xml'] = document
    assert ci_local.test_verdict(record)['problems'] == ['invalid JUnit']


@pytest.mark.parametrize('mutation', ['unchanged', 'enabled', 'missing-condition', 'schedule', 'capacity-suite'])
def test_historical_capacity_workflow_stays_disabled(tmp_path, mutation):
    workflows = tmp_path / '.github/workflows'
    workflows.mkdir(parents=True)
    shutil.copyfile(ROOT / '.github/workflows/ci.yml', workflows / 'ci.yml')
    capacity = (ROOT / '.github/workflows/capacity.yml').read_text()
    if mutation == 'enabled':
        capacity = capacity.replace('    if: false\n', '    if: true\n')
    elif mutation == 'missing-condition':
        capacity = capacity.replace('    if: false\n', '')
    elif mutation == 'schedule':
        capacity = capacity.replace('  workflow_dispatch:', '  schedule:\n    - cron: "0 0 * * *"\n  workflow_dispatch:')
    elif mutation == 'capacity-suite':
        capacity = capacity.replace('echo ', 'python scripts/ci_local.py --check tests --suite capacity # ')
    (workflows / 'capacity.yml').write_text(capacity)
    verdict = ci_local.audit_ci_definition(tmp_path)
    assert verdict['status'] == ('pass' if mutation == 'unchanged' else 'fail')
    assert verdict['problems'] == ([] if mutation == 'unchanged' else [
        'historical capacity workflow must remain disabled'])


def test_manifest_path_validation_rejects_traversal_and_symlinks(tmp_path):
    with pytest.raises(ValueError):
        ci_local.safe_path(tmp_path, '../outside')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (tmp_path / 'link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        ci_local.safe_path(tmp_path, 'link/file')




def test_zero_unittest_discovery_cannot_unlock_ci():
    record = {'id': 0, 'status': 'pass', 'stderr': 'Ran 0 tests in 0.001s\nOK\n'}
    assert ci_local.canary_verdict(record)['status'] == 'fail'






def test_graph_json_presence_is_not_complete_source_quality():
    assert ci_local.graph_evidence_verdict({'nodes': ['invented'], 'decomposition': {'status': 'pass'}}, {})['status'] == 'fail'


def test_missing_root_reconciliation_cannot_pass():
    result = ci_local.audit_producer_reconciliation({}, 'unknown', {}, {}, b'{"entries":[]}', b'{"entries":[]}')
    assert result['status'] == 'fail'
    assert 'missing root final producer reconciliation' in result['problems']


@pytest.mark.parametrize('failed_audit', ['producer-reconciliation-preflight', 'scenario-registry'])
def test_composite_preflight_refuses_candidate_execution(tmp_path, monkeypatch, capsys, failed_audit):
    # Synthetic root records test dispatch ordering, not feature correctness.
    plan = tmp_path / '.taskplane/remediation-v5/plan'
    plan.mkdir(parents=True)
    (plan / 'verification-strategy.json').write_text('{}')
    (plan / 'output.json').write_text('{}')
    manifest_path = tmp_path / '.taskplane/remediation-v5/build/source-manifest.json'
    manifest_path.parent.mkdir()
    manifest_path.write_text('{}')
    strategy = {'required_local_subchecks': [
        {'id': f'LOCAL-PROBE-{i}', 'required': True, 'criteria': [], 'assertions': ['required']}
        for i in range(2)]}
    monkeypatch.setattr(ci_local, 'load_manifest', lambda *args: ({}, strategy))
    monkeypatch.setattr(ci_local, 'tree_map', lambda *args: {})
    monkeypatch.setattr(ci_local, 'audit_source', lambda *args: {'status': 'pass', 'problems': []})
    for name in ('audit_ci_definition', 'audit_historical_ci', 'audit_obligation_dispositions', 'canary_verdict'):
        monkeypatch.setattr(ci_local, name, lambda *args: {'status': 'pass'})
    monkeypatch.setattr(ci_local, 'audit_producer_reconciliation', lambda *args: {
        'status': 'fail' if failed_audit == 'producer-reconciliation-preflight' else 'pass'})
    monkeypatch.setattr(ci_local, 'audit_scenario_registry', lambda *args: {
        'status': 'fail' if failed_audit == 'scenario-registry' else 'pass', 'bindings': {}})
    launched = []
    class CanaryOnlyLog:
        def __init__(self, *args):
            self.records = []
        def run(self, argv, *, label, **kwargs):
            assert label == 'canary/unittest', 'candidate execution happened before accepted preflight'
            launched.append(label)
            record = {'id': 0, 'label': label, 'status': 'pass'}
            self.records.append(record)
            return record
    monkeypatch.setattr(ci_local, 'ExecutionLog', CanaryOnlyLog)
    assert ci_local.remediation(manifest_path, tmp_path) == 1
    report = ci_local.decode_evidence(json.loads(capsys.readouterr().out))
    assert launched == ['canary/unittest']
    assert failed_audit in report['dependency_deferral']['failed_preconditions']
    assert len(report['subchecks']) == 2
    assert all(row['status'] == 'fail' and failed_audit in row['deferred_by'] for row in report['subchecks'])




def test_timeout_keeps_actual_partial_raw_streams(tmp_path):
    log = ci_local.ExecutionLog(tmp_path / 'output', ROOT)
    record = log.run([sys.executable, '-c',
                      'import sys,time; sys.stdout.buffer.write(b"before-timeout\\n"); sys.stdout.buffer.flush(); '
                      'sys.stderr.buffer.write(b"error-before-timeout\\n"); sys.stderr.buffer.flush(); time.sleep(5)'],
                     label='timeout-probe', timeout=0.1)
    assert record['status'] == 'unknown' and record['reason'] == 'timeout'
    assert 'before-timeout' in record['stdout'] and 'error-before-timeout' in record['stderr']
    assert (tmp_path / 'output/execution-0/stdout.raw').read_bytes() == b'before-timeout\n'
    assert record['stdout_base64']
    assert (tmp_path / 'output/execution-0/command.json').is_file()


def test_scanner_exclusions_are_explicit_and_do_not_change_source_identity():
    source = {name: {} for name in ('engine.py', 'corpus/sample.py', 'corpus-notes.md',
                                    'taskplane/tests/fixtures/test.py', '.github/workflows/ci.yml')}
    audit = ci_local.scanner_inventory(source, 'exclude:\n  - corpus\n  - taskplane/tests/fixtures\n')
    assert audit['expected_files'] == ['.github/workflows/ci.yml', 'corpus-notes.md', 'engine.py']
    assert set(audit['excluded_paths_and_rules']) == {'corpus/sample.py', 'taskplane/tests/fixtures/test.py'}
    assert len(source) == 5


@pytest.mark.parametrize('include_hostless', [False, True], ids=['verified-empty', 'actual-hostless'])
def test_hostless_wrapper_uses_actual_collection_and_exact_selection(tmp_path, monkeypatch, include_hostless):
    root = tmp_path / 'candidate'
    tests = root / 'taskplane/tests'
    tests.mkdir(parents=True)
    (root / 'scripts').mkdir()
    for name in ('ci_local.py',):
        shutil.copyfile(ROOT / 'scripts' / name, root / 'scripts' / name)
    for name in ('conftest.py', 'test_runner_isolation.py'):
        shutil.copyfile(ROOT / 'taskplane/tests' / name, tests / name)
    source = "import pytest\n@pytest.mark.parametrize('host',['codex','claude'])\ndef test_host(host):\n    assert host\n"
    if include_hostless:
        source += 'def test_hostless():\n    assert True\n'
    (tests / 'test_workflow_packages.py').write_text(source)
    target = tmp_path / 'reports/execution.json'
    monkeypatch.setattr(ci_local, 'ROOT', root)
    monkeypatch.setattr(sys, 'argv', ['ci_local', '--check', 'tests', '--suite', 'packages',
                                     '--host', 'hostless', '--report-json', str(target)])
    assert ci_local.main() == 0
    report = ci_local.decode_evidence(json.loads(target.read_text()))
    collection = next(row for row in report['executions'] if row['label'] == 'hostless/inventory')
    nodes = collection['hostless_partition']['selected_nodeids']
    assert nodes == (['taskplane/tests/test_workflow_packages.py::test_hostless'] if include_hostless else [])
    actual = [row for row in report['executions'] if row['label'] == 'tests']
    assert len(actual) == int(include_hostless)
    if actual:
        assert actual[0]['profile']['selected_nodeids'] == nodes
    raw = next(target.parent.glob('execution-raw-*'))
    assert (raw / 'execution-0/command.json').is_file()
    assert (raw / 'execution-0/stdout.raw').is_file()


def test_final_registry_pins_source_and_rejects_unknown_audits_and_modules():
    strategy = {'required_local_subchecks': [{'id': 'LOCAL-DEVOPS-01', 'assertions': ['required'],
                                             'test_modules': ['taskplane/tests/test_ci_local.py']}]}
    entry = {'id': 'BUILD-FINAL-SCENARIO-REGISTRY', 'schema': 'taskplane.remediation-scenario-registry/v1',
             'source_manifest_sha256': 'manifest', 'verification_strategy_sha256': 'strategy',
             'candidate_map_sha256': ci_local.digest({}), 'reviewer': 'fixture-root', 'review_references': ['fixture-review'],
             'bindings': {'LOCAL-DEVOPS-01:1': ['audit:canary']}}
    entries = [{'id': 'BUILD-FINAL-PRODUCER-RECONCILIATION', 'task_results': {'fixture-owner': {'reviewer': 'fixture-root'}}}, entry]
    def check():
        return ci_local.audit_scenario_registry(json.dumps({'entries': entries}).encode(), strategy, 'manifest', 'strategy', {})['status']
    assert check() == 'pass'
    entry['bindings']['LOCAL-DEVOPS-01:1'] = ['audit:invented']
    assert check() == 'fail'
    entry['bindings']['LOCAL-DEVOPS-01:1'] = ['taskplane/tests/undeclared.py::test_probe']
    assert check() == 'fail'
    entry['bindings']['LOCAL-DEVOPS-01:1'] = ['taskplane/tests/test_ci_local.py::test_zero_unittest_discovery_cannot_unlock_ci']
    assert check() == 'pass'
    entry['candidate_map_sha256'] = 'stale'
    assert check() == 'fail'


@pytest.mark.parametrize('node,count', [
    ('taskplane/tests/test_review_remediation.py::test_windows_interpreter_search_respects_current_directory_policy', 2),
    ('taskplane/tests/test_workspace_binding.py::test_unsupported_binding_cli_is_structured_and_preserves_files', 12),
    ('taskplane/tests/test_native_workflow_cli.py::test_unsupported_claude_context_refuses_through_declared_hooks', 1),
    ('taskplane/tests/test_native_session_meter.py::test_incremental_long_directory_entries_resume_without_false_eof[12-\\u6f22]', 1),
], ids=['windows-search', 'binding-native', 'unsupported-claude', 'long-filename'])
def test_real_optional_cases_keep_complete_inventory(tmp_path, node, count):
    # Uses the actual source tests and dependencies, never copied predicates or
    # runtime stubs. Missing integration dependencies remain test failures.
    log = ci_local.ExecutionLog(tmp_path/'real-optional', ROOT)
    record = log.run(ci_local.pytest_command([node]), label='focused/actual-optional', pytest_run=True)
    assert record['status'] == 'pass', record['test_verdict']
    selected = record['profile']['selected_nodeids']
    assert len(selected) == count and selected == [row['nodeid'] for row in record['collection'] if row['selected']]
    assert ci_local.verify_partition(record['collection'], ci_local.matrix_selections(record['collection']))['status'] == 'pass'
    for skip in record['test_verdict']['optional_platform_skips']:
        skipped_node = skip['nodeid']
        assert skipped_node in selected and skip['reason'] and skip['platform_capabilities']
        record['label'] = 'targeted/LOCAL-X'
        assert ci_local.required_coverage({'id': 'LOCAL-X', 'assertions': ['required']}, [record], {},
                                          {'LOCAL-X:1': [skipped_node]})['status'] == 'fail'


def test_symlink_skip_requires_actual_native_error():
    node = 'taskplane/tests/test_security_boundaries.py::test_package_refuses_symlink_directory_before_read[1]'
    record = optional_record(node, 'Native account cannot create symlinks', platform='win32')
    assert ci_local.test_verdict(record)['status'] == 'fail'
    import errno
    record['profile']['reports'][0]['skip_evidence']['os_error'] = {'errno': errno.EPERM, 'message': 'operation not permitted'}
    assert ci_local.test_verdict(record)['status'] == 'pass'
    record['profile']['reports'][0]['skip_evidence']['os_error']['errno'] = errno.EIO
    assert ci_local.test_verdict(record)['status'] == 'fail'


def test_lossless_evidence_roundtrip_after_temporary_cleanup(tmp_path):
    # Real pytest/JUnit transport with an opaque native payload above 8 MiB.
    # This is a codec fixture, not a performance or native-host certification.
    with tempfile.TemporaryDirectory(prefix='ci-codec-fixture-') as directory:
        base = Path(directory); root = base/'source'; tests = root/'taskplane/tests'; tests.mkdir(parents=True)
        (root/'scripts').mkdir()
        shutil.copyfile(ROOT/'scripts/ci_local.py', root/'scripts/ci_local.py')
        shutil.copyfile(ROOT/'taskplane/tests/conftest.py', tests/'conftest.py')
        (tests/'test_codec_probe.py').write_text('''import json, os

def test_measurement(record_property):
    document = {'schema': 'taskplane.remediation-measurement/v1', 'kind': 'usage',
        'subcheck': 'LOCAL-CI-PROD-06', 'scenario': 'LOCAL-CI-PROD-06:1',
        'samples': [{'sample_id': 'full-opaque', 'raw_executions': [{'exit_code': -9, 'timed_out': False}],
                     'continuation': {'$evidence': 'native-opaque', 'view': False, 'position': -1},
                     'payload': 'opaque-native-' * 700000}]}
    record_property('remediation_measurement', json.dumps(document))
    os.write(1, b"stdout-\\xff\\x00")
    os.write(2, b"stderr-\\xfe\\x00")
''')
        log = ci_local.ExecutionLog(base/'execution', root)
        record = log.run(ci_local.pytest_command(['taskplane/tests/test_codec_probe.py']) + ['-s'],
                         label='targeted/LOCAL-CI-PROD-06', pytest_run=True)
        assert record['status'] == 'pass', record['test_verdict']
        properties = ci_local.parse_junit_xml(ci_local.raw_evidence_bytes(record, 'junit.xml')).findall('.//property')
        measurements = [json.loads(prop.attrib['value']) for prop in properties if prop.get('name') == 'remediation_measurement']
        assert len(measurements) == 1
        raw = {name: ci_local.raw_evidence_bytes(record, name) for name in record['raw_files']}
        assert b'stdout-\xff\x00' in raw['stdout'] and b'stderr-\xfe\x00' in raw['stderr']
        assert len(raw['junit.xml']) > 8 * 1024 * 1024
        document = {'status': 'pass', 'executions': [record], 'measurements': measurements,
                    'samples': measurements[0]['samples']}
        packed = ci_local.pack_evidence(document)
        # UTF8 JUnit and the original raw-file bytes share the same blob.
        assert packed['executions'][0]['junit.xml']['$evidence'] == packed['executions'][0]['raw_files']['junit.xml']['base64']['$evidence']
        wire = ci_local._json_bytes(packed)
        (tmp_path/'codec-wire.json').write_bytes(wire)
    assert not base.exists()
    decoded = ci_local.decode_evidence(json.loads(wire))
    assert decoded == document
    for name, original in raw.items():
        assert ci_local.raw_evidence_bytes(decoded['executions'][0], name) == original
    assert decoded['samples'][0]['continuation']['view'] is False
    sizes = packed['evidence_encoding']['sizes']
    assert sizes['wire_json_bytes'] == len(wire)
    print(json.dumps({'codec_fixture_sizes': sizes, 'junit_original_bytes': len(raw['junit.xml']),
                      'measurement_json_bytes': len(ci_local._json_bytes(measurements))}, sort_keys=True))


@pytest.mark.parametrize('corruption', ['version', 'length', 'digest', 'truncated', 'trailing', 'corrupt',
                                      'unknown-ref', 'view', 'chunk', 'size-accounting'])
def test_evidence_decoder_refuses_corruption(corruption):
    packed = ci_local.pack_evidence({'stdout': 'hello' * 10000})
    metadata = packed['evidence_encoding']; sha = packed['stdout']['$evidence']; blob = metadata['blobs'][sha]
    if corruption == 'version': metadata['schema'] = 'future'
    elif corruption == 'length': blob['bytes'] = 1
    elif corruption == 'digest': blob['sha256'] = '0' * 64
    elif corruption in ('truncated', 'trailing', 'corrupt'):
        compressed = b''.join(base64.b64decode(chunk) for chunk in blob['chunks'])
        compressed = compressed[:-1] if corruption == 'truncated' else compressed + b'junk' if corruption == 'trailing' else b'bad stream'
        blob.update(chunks=[base64.b64encode(compressed).decode()], compressed_bytes=len(compressed))
    elif corruption == 'unknown-ref': packed['stdout']['$evidence'] = '0' * 64
    elif corruption == 'view': packed['stdout']['view'] = 'pickle'
    elif corruption == 'chunk': blob['chunks'] = ['???']
    else: metadata['sizes']['decoded_json_bytes'] += 1
    with pytest.raises(ValueError): ci_local.decode_evidence(packed)


def test_original_invalid_file_bytes_survive_semantic_refusal(tmp_path):
    log = ci_local.ExecutionLog(tmp_path/'raw-invalid', ROOT)
    dest = tmp_path/'raw-invalid/execution-0'
    command = [sys.executable, '-c',
        'from pathlib import Path; import sys; p=Path(sys.argv[1]); '
        '(p/"profile.json").write_bytes(b"{\\xff}"); '
        '(p/"collection.json").write_bytes(b"[\\xfe]"); '
        '(p/"junit.xml").write_bytes(b"<testsuite>\\xfd</testsuite>")', str(dest)]
    record = log.run(command, label='invalid-original-files')
    assert record['status'] == 'fail'
    decoded = ci_local.decode_evidence(ci_local.pack_evidence({'executions': [record]}))['executions'][0]
    for name, raw in [('profile.json', b'{\xff}'), ('collection.json', b'[\xfe]'),
                      ('junit.xml', b'<testsuite>\xfd</testsuite>')]:
        assert ci_local.raw_evidence_bytes(decoded, name) == raw
    assert 'profile' not in decoded and 'collection' not in decoded




def test_opaque_native_base64_names_and_reserved_reference_roundtrip():
    opaque = {'native': {'opaque_base64': 'not a base64 token', 'base64': 'Zh==',
                         'nested': [{'stdout_base64': '???', 'base64': 'Zg==\n'}],
                         'reference': {'$evidence': 'native-value', 'view': 'custom'},
                         'stdout': chr(0xD800), 'long_string': chr(0xD800) * 5000}}
    assert ci_local.decode_evidence(ci_local.pack_evidence(opaque)) == opaque


def test_final_selection_preserves_seed_report_before_pytest_filtering(tmp_path):
    root = tmp_path/'source'; tests = root/'taskplane/tests'; tests.mkdir(parents=True)
    (root/'scripts').mkdir()
    shutil.copyfile(ROOT/'scripts/ci_local.py', root/'scripts/ci_local.py')
    shutil.copyfile(ROOT/'taskplane/tests/conftest.py', tests/'conftest.py')
    (tests/'test_filter.py').write_text('def test_selected(): pass\ndef test_filtered(): pass\n')
    log = ci_local.ExecutionLog(tmp_path/'evidence', root)
    record = log.run(ci_local.pytest_command(['taskplane/tests/test_filter.py']) + ['-k', 'selected'],
                     label='focused/final-selection', pytest_run=True)
    assert record['status'] == 'pass', record['test_verdict']
    assert json.loads(ci_local.raw_evidence_bytes(record, 'collection.json')) == record['collection']
    assert len(record['collection']) == len(record['final_collection']) == 2
    assert sum(row['selected'] for row in record['collection']) == 2
    assert sum(row['selected'] for row in record['final_collection']) == 1
    assert record['profile']['selected_nodeids'] == ['taskplane/tests/test_filter.py::test_selected']
    assert record['selection_provenance']['original'].startswith('collection / raw_files.collection.json')
    broken = deepcopy(record); broken['final_collection'].pop()
    assert ci_local.test_verdict(broken)['status'] == 'fail'




def test_multiple_evidence_chunks_and_original_identity_validation():
    raw = os.urandom(131072)
    record = {'stdout_base64': base64.b64encode(raw).decode(), 'raw_files': {'stdout': {
        'base64': base64.b64encode(raw).decode(), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}}}
    packed = ci_local.pack_evidence(record)
    ref = packed['stdout_base64']['$evidence']
    assert len(packed['evidence_encoding']['blobs'][ref]['chunks']) >= 3
    assert packed['raw_files']['stdout']['base64']['$evidence'] == ref
    assert ci_local.raw_evidence_bytes(ci_local.decode_evidence(packed), 'stdout') == raw
    record['raw_files']['stdout']['bytes'] += 1
    with pytest.raises(ValueError, match='original byte envelope identity'):
        ci_local.pack_evidence(record)


def functional_strategy():
    return {'schema': 'remediation-plan-verification/v5', 'required_local_subchecks': [
        {'id': 'CHECK-CORE', 'required': True, 'criteria': ['CORE'], 'assertions': ['Core behavior is verified.']},
        {'id': 'CHECK-PACKAGE', 'required': True, 'criteria': ['PACKAGE'], 'assertions': ['Extracted package works.']}],
        'later_stage_gates': [{'id': 'NATIVE-01', 'required': True}, {'id': 'WIN-01', 'required': True}]}


def test_functional_collection_roundtrip_without_object_count_target(tmp_path):
    from taskplane.context import Store
    values = [{'check': f'fixture-{i}', 'observations': {'status': 'pass', 'detail': ['retained', 'é']}}
              for i in range(3000)]
    store = Store(tmp_path)
    reference = store.put('check-records', values)
    assert store.resolve(reference) == values




@pytest.mark.parametrize('defect', ['missing', 'duplicate', 'optional', 'criterion', 'assertion'])
def test_functional_strategy_validates_declared_checks(defect):
    strategy = functional_strategy()
    ci_local.validate_functional_strategy(strategy)
    checks = strategy['required_local_subchecks']
    if defect == 'missing': strategy['required_local_subchecks'] = []
    elif defect == 'duplicate': checks[0]['id'] = checks[1]['id']
    elif defect == 'optional': checks[0]['required'] = False
    elif defect == 'criterion': checks[0]['criteria'] *= 2
    else: checks[0]['assertions'] = [' ']
    with pytest.raises(ValueError, match='invalid functional strategy'):
        ci_local.validate_functional_strategy(strategy)


def test_functional_composite_uses_declared_checks(tmp_path, monkeypatch, capsys):
    # Synthetic passing audit/dispatch records test orchestration only. The real
    # authority, collection and native lifecycle regressions are independently bound.
    base = tmp_path/'.taskplane/remediation-v5'
    (base/'plan').mkdir(parents=True); (base/'build').mkdir()
    strategy = functional_strategy()
    (base/'plan/verification-strategy.json').write_text(json.dumps(strategy))
    (base/'plan/output.json').write_text('{}')
    manifest = base/'build/source-manifest.json'; manifest.write_text('{}')
    monkeypatch.setattr(ci_local, 'load_manifest', lambda *args: ({}, strategy))
    source = {'taskplane/tests/conftest.py': {'sha256': 'fixture-conftest'}}
    monkeypatch.setattr(ci_local, 'tree_map', lambda *args: source)
    for name in ('audit_source', 'audit_producer_reconciliation', 'audit_ci_definition',
                 'audit_historical_ci', 'audit_obligation_dispositions', 'canary_verdict',
                 'audit_packages', 'audit_candidate_graph'):
        monkeypatch.setattr(ci_local, name, lambda *args: {'status': 'pass'})
    registry = {f"{spec['id']}:{number}": ['audit:canary'] for spec in strategy['required_local_subchecks']
                for number in range(1, len(spec['assertions']) + 1)}
    monkeypatch.setattr(ci_local, 'audit_scenario_registry', lambda *args: {'status': 'pass', 'bindings': registry})
    node = 'taskplane/tests/test_probe.py::test_integrity'
    rows = [{'nodeid': node, 'suite': 'core', 'host': None, 'selected': True}]
    class FunctionalLog:
        def __init__(self, *args): self.records = []
        def run(self, argv, *, label, **kwargs):
            record = {'id': len(self.records), 'label': label, 'status': 'pass', 'argv': argv,
                      'elapsed_seconds': 10**12, 'timing_provider': None,
                      'profile': {'selected_nodeids': [node]}, 'junit.xml': '<testsuite/>', 'collection': rows}
            self.records.append(record)
            return record
    monkeypatch.setattr(ci_local, 'ExecutionLog', FunctionalLog)
    assert ci_local.remediation(manifest, tmp_path) == 0
    report = ci_local.decode_evidence(json.loads(capsys.readouterr().out))
    assert {row['id'] for row in report['subchecks']} == {'CHECK-CORE', 'CHECK-PACKAGE'}


def test_root_and_native_reconciliation_preserves_accepted_subset(tmp_path):
    root_path, native_path = 'candidate/root.py', 'candidate/native.py'
    frozen = tmp_path/'source-manifest.json'; frozen.write_text('{}')
    task = {'accepted_at': 'observed', 'reviewer': 'root', 'task_digest': 'actual-task-digest',
            'checks': [{'name': 'syntax', 'status': 'pass', 'evidence': 'actual-check'}]}
    source = {'root.py': {'sha256': 'root-sha'}, 'native.py': {'sha256': 'native-sha'}}
    manifest = {'run': 'run', 'candidate_root': 'candidate', 'files_columns': ['path', 'owner'],
                'files': [['root.py', 'bootstrap'], ['native.py', 'native']]}
    plan = {'run': 'run', 'task_dag': [
        {'id': 'bootstrap', 'execution': 'root', 'paths': [root_path, 'source-manifest.json'],
         },
        {'id': 'native', 'execution': 'native_required', 'paths': [native_path]}]}
    raw = json.dumps(plan).encode()
    entry = {'id': 'BUILD-FINAL-PRODUCER-RECONCILIATION',
        'schema': 'taskplane.remediation-producer-reconciliation/v2', 'run': 'run', 'visit': 'visit', 'revision': 1,
        'source_manifest_sha256': 'manifest', 'plan_sha256': hashlib.sha256(raw).hexdigest(),
        'candidate_root': 'candidate', 'files': source,
        'task_results': {'bootstrap': {**task, 'grant': None, 'worker_id': None,
                                      'manifest': {'source-manifest.json': hashlib.sha256(frozen.read_bytes()).hexdigest()}},
                         'native': {**task, 'grant': 'grant', 'worker_id': 'worker', 'manifest': {}}},
        'native_observations': {'native': {'state': 'accepted', 'terminal_status': 'completed',
            'reference': 'native-final', 'grant': 'grant', 'worker_id': 'worker'}},
        'owner_manifests': {'bootstrap': {root_path: 'root-sha'}, 'native': {native_path: 'native-sha'}},
        'owner_evidence': {'bootstrap': ['root-inventory'], 'native': ['actual-handback']}}
    def check():
        ledger = json.dumps({'entries': [entry]}).encode()
        return ci_local.audit_producer_reconciliation(manifest, 'manifest', source, source, ledger, ledger, raw, tmp_path)
    assert check()['status'] == 'pass'
    assert entry['task_results']['native']['manifest'] == {}
    entry['native_observations']['native']['worker_id'] = 'foreign'
    assert check()['status'] == 'fail'
    entry['native_observations']['native']['worker_id'] = 'worker'
    entry['owner_manifests']['native'][native_path] = 'changed'
    assert check()['status'] == 'fail'








def release_fixture(tmp_path, monkeypatch):
    root = tmp_path / 'candidate'; root.mkdir()
    (tmp_path / 'components.yaml').write_bytes(b'exclude:\n  - work/runtime-activation-v5\n')
    for host in ('.codex-plugin', '.claude-plugin'):
        (root / host).mkdir()
        (root / host / 'plugin.json').write_text(json.dumps({'version': '2.33.11'}))
    (root / 'unchanged.py').write_bytes(b'unchanged\n')
    (root / 'changed.py').write_bytes(b'original\n')
    baseline = ci_local.tree_map(root)
    archive_path = tmp_path / 'baseline.zip'
    with zipfile.ZipFile(archive_path, 'w') as archive:
        for name, value in baseline.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100000 | int(value['mode'], 8)) << 16
            archive.writestr(info, (root / name).read_bytes())
    def reference(path, value=None):
        target = tmp_path / path
        if value is not None:
            target.write_text(json.dumps(value))
        return {'path': path, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    inventory = {'files': {name: {'sha256': row['sha256'], 'bytes': row['bytes'], 'mode': int(row['mode'], 8)}
                           for name, row in baseline.items()}, 'count': len(baseline),
                 'archive_sha256': hashlib.sha256(archive_path.read_bytes()).hexdigest()}
    original = reference('baseline.json', inventory)
    review = reference('review.json', {'scope': 'fixture repair and registry review'})
    reader = tmp_path / 'legacy'
    (reader / 'taskplane').mkdir(parents=True)
    (reader / 'taskplane/workflow_host.py').write_text('# Synthetic identity fixture; never used as an actual legacy reader.\n')
    (root / 'changed.py').write_bytes(b'repaired\n')
    (root / 'added.py').write_bytes(b'new regression\n')
    files = ci_local.tree_map(root)
    strategy = functional_strategy()
    manifest = {'schema': 'taskplane.release-source-manifest/v1', 'release': '2.33.11',
        'candidate_root': 'candidate', 'strategy': reference('strategy.json', strategy),
        'baseline': {'inventory': original, 'archive': reference('baseline.zip')}, 'files': files,
        'legacy_reader': {'path': 'legacy', 'files': ci_local.tree_map(reader),
                          'reviewer': 'fixture-root', 'review_references': [review]},
        'changes': [{'path': name, 'before': baseline.get(name), 'after': row, 'reason': 'reviewed repair',
                     'reviewer': 'fixture-root', 'review_references': [review]}
                    for name, row in files.items() if row != baseline.get(name)],
        'retained_artifacts': [{**reference('old-' + host + '.json', {'host': host}),
                                'role': 'prior-package', 'host': host} for host in ('openai', 'claude')]
                              + [{**review, 'role': 'maintenance-provenance'}, {**original, 'role': 'historical-manifest'}],
        'scenario_registry': {'reviewer': 'fixture-root', 'review_references': [review],
            'bindings': {f"{spec['id']}:{number}": ['audit:canary']
                         for spec in strategy['required_local_subchecks']
                         for number in range(1, len(spec['assertions']) + 1)}},
        'external_gates': [{'id': key, 'status': 'unknown', 'owner': 'fixture-owner',
                            'reason': 'actual host execution unavailable', 'next_action': 'observe supported host',
                            'evidence': []} for key in sorted(gate["id"] for gate in strategy["later_stage_gates"])]}
    monkeypatch.setattr(ci_local, 'ROOT', root)
    return manifest, root, reference


def test_release_preserves_baseline_and_hashes_only_reviewed_changes(tmp_path, monkeypatch):
    manifest, root, reference = release_fixture(tmp_path, monkeypatch)
    before = ci_local.tree_map(root)
    frozen = (tmp_path / 'baseline.zip').read_bytes(), (tmp_path / 'baseline.json').read_bytes()
    audit = ci_local.audit_release_preservation(manifest, tmp_path, before, before)
    assert audit['status'] == 'pass', audit
    assert audit['unchanged'] == ['.claude-plugin/plugin.json', '.codex-plugin/plugin.json', 'unchanged.py']
    assert audit['changed'] == ['changed.py'] and audit['added'] == ['added.py']
    assert frozen == ((tmp_path / 'baseline.zip').read_bytes(), (tmp_path / 'baseline.json').read_bytes())
    reference('release.json', manifest)
    loaded, strategy, inputs = ci_local.load_release_manifest(tmp_path / 'release.json', tmp_path)
    assert loaded == manifest and len(strategy['required_local_subchecks']) == 2
    assert 'strategy.json' in inputs and 'review.json' in inputs


@pytest.mark.parametrize('defect', ['unreviewed-change', 'wrong-before', 'wrong-after', 'review-drift',
    'final-drift', 'mode-drift', 'removed-original', 'baseline-drift', 'archive-drift', 'missing-maintenance', 'missing-package', 'version', 'exclusion'])
def test_release_preservation_refuses_unaccounted_changes(tmp_path, monkeypatch, defect):
    manifest, root, reference = release_fixture(tmp_path, monkeypatch)
    before = ci_local.tree_map(root); after = deepcopy(before)
    if defect == 'unreviewed-change': manifest['changes'].pop()
    elif defect == 'wrong-before': manifest['changes'][0]['before'] = before['unchanged.py']
    elif defect == 'wrong-after': manifest['changes'][0]['after'] = before['unchanged.py']
    elif defect == 'review-drift': (tmp_path / 'review.json').write_bytes(b'changed review')
    elif defect == 'final-drift': after['changed.py']['sha256'] = 'a' * 64
    elif defect == 'mode-drift': after['changed.py']['mode'] = '0777'
    elif defect == 'removed-original':
        del manifest['files']['unchanged.py']; del before['unchanged.py']; del after['unchanged.py']
    elif defect == 'baseline-drift': (tmp_path / 'baseline.json').write_bytes(b'{}')
    elif defect == 'archive-drift': (tmp_path / 'baseline.zip').write_bytes(b'bad zip')
    elif defect == 'missing-maintenance': manifest['retained_artifacts'] = [r for r in manifest['retained_artifacts'] if r['role'] != 'maintenance-provenance']
    elif defect == 'version': manifest['release'] = '9.0.0'
    elif defect == 'exclusion': (tmp_path / 'components.yaml').write_text('exclude:\n  - work\n')
    else: manifest['retained_artifacts'].pop(0)
    assert ci_local.audit_release_preservation(manifest, tmp_path, before, after)['status'] == 'fail'


@pytest.mark.parametrize('defect', ['missing-assertion', 'unknown-audit', 'retired-producer',
                                   'missing-external', 'unsupported-status', 'unproved-pass', 'review-drift',
                                   'reader-drift', 'reader-missing', 'reader-current'])
def test_release_loader_retains_assertions_and_external_evidence(tmp_path, monkeypatch, defect):
    manifest, root, reference = release_fixture(tmp_path, monkeypatch)
    bindings = manifest['scenario_registry']['bindings']; key = next(iter(bindings))
    if defect == 'missing-assertion': bindings.pop(key)
    elif defect == 'unknown-audit': bindings[key] = ['audit:invented']
    elif defect == 'retired-producer': bindings[key] = ['audit:producer-reconciliation']
    elif defect == 'missing-external': manifest['external_gates'].pop()
    elif defect == 'unsupported-status': manifest['external_gates'][0]['status'] = 'deferred-pass'
    elif defect == 'unproved-pass': manifest['external_gates'][0]['status'] = 'pass'
    elif defect == 'reader-drift': (tmp_path / 'legacy/taskplane/workflow_host.py').write_text('drift')
    elif defect == 'reader-missing': manifest['legacy_reader']['files'] = {}
    elif defect == 'reader-current': manifest['legacy_reader']['path'] = 'candidate'
    else: (tmp_path / 'review.json').write_bytes(b'drift')
    reference('release.json', manifest)
    with pytest.raises(ValueError):
        ci_local.load_release_manifest(tmp_path / 'release.json', tmp_path)


def test_release_preflight_never_executes_unreviewed_candidate(tmp_path, monkeypatch, capsys):
    manifest, root, reference = release_fixture(tmp_path, monkeypatch)
    manifest['changes'].pop(); reference('release.json', manifest)
    def refused(*args, **kwargs):
        raise AssertionError('candidate executed before reviewed preservation preflight')
    monkeypatch.setattr(ci_local.ExecutionLog, 'run', refused)
    monkeypatch.setattr(ci_local, 'execute_functional_checks', refused)
    for name in ('audit_ci_definition', 'audit_historical_ci'):
        monkeypatch.setattr(ci_local, name, lambda *args: {'status': 'pass'})
    assert ci_local.release_verification(tmp_path / 'release.json', tmp_path) == 1
    report = ci_local.decode_evidence(json.loads(capsys.readouterr().out))
    assert report['status'] == 'fail' and report['executions'] == []
    assert len(report['subchecks']) == 2
    assert {row['status'] for row in report['external_gates']} == {'unknown'}


def test_explicit_reader_input_does_not_inherit_shell_path_and_is_fingerprinted(tmp_path):
    root = tmp_path / 'candidate'; root.mkdir()
    reader = tmp_path / 'legacy'; reader.mkdir()
    (reader / 'source.py').write_text('# identity-only fixture\n')
    log = ci_local.ExecutionLog(tmp_path / 'execution', root,
        inherited={**os.environ, 'TEST_TASKPLANE_LEGACY_RUNTIME': '/unreviewed/shell/runtime'})
    assert 'TEST_TASKPLANE_LEGACY_RUNTIME' not in log.environment
    log.environment['TEST_TASKPLANE_LEGACY_RUNTIME'] = str(reader)
    log.bound_external_sources['legacy-reader'] = reader
    record = log.run([sys.executable, '-c', 'import os;print(os.environ["TEST_TASKPLANE_LEGACY_RUNTIME"])'],
                     label='fixture/explicit-reader-input')
    assert record['status'] == 'pass' and record['stdout'].strip() == str(reader)
    assert record['external_source_fingerprints_before'] == record['external_source_fingerprints_after'] == {
        'legacy-reader': ci_local.digest(ci_local.tree_map(reader))}


@pytest.mark.parametrize('defect', [None, 'quality', 'packages', 'required-assertion', 'source-during-execution', 'reader-during-execution', 'manifest-during-load'])
def test_release_orchestration_keeps_failures_and_external_unknowns(tmp_path, monkeypatch, capsys, defect):
    # Synthetic execution records prove dispatch/verdict composition, not native
    # behavior or functional correctness. Real runner checks cover process capture.
    manifest, root, reference = release_fixture(tmp_path, monkeypatch)
    manifest_reference = reference('release.json', manifest)
    if defect == 'manifest-during-load':
        # Atomic replacement after the first read must not make executed A claim
        # B's identity. The final input audit must detect B against retained A.
        original_read = Path.read_bytes
        reads = []
        def replacing_read(path):
            raw = original_read(path)
            if path == tmp_path / 'release.json':
                reads.append(raw)
                if len(reads) == 1:
                    replacement = tmp_path / 'replacement.json'
                    replacement.write_text(json.dumps({**manifest, 'release': '9.0.0'}))
                    replacement.replace(path)
            return raw
        monkeypatch.setattr(Path, 'read_bytes', replacing_read)
    fingerprint = ci_local.digest(ci_local.tree_map(root))
    for name in ('audit_ci_definition', 'audit_historical_ci', 'canary_verdict'):
        monkeypatch.setattr(ci_local, name, lambda *args: {'status': 'pass'})
    class FixtureLog:
        def __init__(self, *args):
            self.records = []
            self.environment = {}
            self.bound_external_sources = {}
        def run(self, argv, *, label, **kwargs):
            external = {name: ci_local.digest(ci_local.tree_map(path)) for name, path in self.bound_external_sources.items()}
            row = {'id': len(self.records), 'label': label, 'status': 'pass',
                   'source_fingerprint_before': fingerprint, 'source_fingerprint_after': fingerprint,
                   'external_source_fingerprints_before': external, 'external_source_fingerprints_after': external,
                   'profile': {}, 'junit.xml': '<testsuite/>'}
            self.records.append(row)
            return row
    monkeypatch.setattr(ci_local, 'ExecutionLog', FixtureLog)
    calls = []
    def execute(log, strategy, before, audits):
        calls.append('shared-functional-execution')
        assert log.environment['TEST_TASKPLANE_LEGACY_RUNTIME'] == str(tmp_path / 'legacy')
        log.run([], label='broad/core')
        for name in ('exact-once-partition', 'quality', 'packages', 'candidate-graph'):
            audits[name] = {'status': 'fail' if defect == name else 'pass'}
        if defect == 'required-assertion':
            audits['canary']['status'] = 'fail'
        if defect == 'source-during-execution':
            (root / 'unchanged.py').write_text('unexpected mutation')
        if defect == 'reader-during-execution':
            (tmp_path / 'legacy/taskplane/workflow_host.py').write_text('unexpected mutation')
    monkeypatch.setattr(ci_local, 'execute_functional_checks', execute)
    assert ci_local.release_verification(tmp_path / 'release.json', tmp_path) == (0 if defect is None else 1)
    report = ci_local.decode_evidence(json.loads(capsys.readouterr().out))
    assert calls == ['shared-functional-execution']
    assert report['local_status'] == ('pass' if defect is None else 'fail')
    assert report['release_status'] != 'pass'
    assert report['external_gate_status'] == 'requires_external_review'
    assert {row['status'] for row in report['external_gates']} == {'unknown'}
    assert len(report['subchecks']) == 2 and report['functional_contract']['assertions'] == 2
    assert report['audits']['packages']['source_manifest_sha256'] == report['manifest_sha256']
    assert report['manifest_sha256'] == manifest_reference['sha256']
    if defect == 'manifest-during-load':
        assert report['release'] == '2.33.11'
        assert report['audits']['accepted-input-identity']['changed_inputs'] == ['release.json']
        assert len(reads) == 2  # One parse/hash read and the independent final identity read.
