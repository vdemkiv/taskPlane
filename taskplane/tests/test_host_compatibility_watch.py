"""Offline source/report fixtures; these do not establish host compatibility."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts import check_host_updates as watch


def sources():
    data = {url: '# Official docs\nPermissions and subagent hooks.\n' for url in watch.DOCS.values()}
    for repo in watch.RELEASES.values():
        data[f'https://api.github.com/repos/{repo}/releases?per_page=5&page=1'] = json.dumps([
            {'id': 2, 'tag_name': '2.0-preview', 'prerelease': True,
             'html_url': f'https://github.com/{repo}/releases/tag/2.0-preview', 'body': 'New tool schema.'},
            {'id': 1, 'tag_name': '1.0', 'prerelease': False,
             'html_url': f'https://github.com/{repo}/releases/tag/1.0', 'body': 'Fixed approval and transcript recovery.'}])
    return data


def test_initial_baseline_then_unchanged_sources_are_deduplicated():
    data = sources()
    state, first = watch.collect({}, data.__getitem__)
    assert len(first['findings']) == 10 and not first['errors']
    again, report = watch.collect(state, data.__getitem__)
    assert again == state and not report['findings'] and not report['errors']
    assert report['observed_versions']['codex']['latest_prerelease'] == '2.0-preview'
    assert 'not a compatibility pass' in watch.render(report)


def test_changed_existing_release_and_doc_retain_links_topics_and_uncertainty():
    data = sources(); state, _ = watch.collect({}, data.__getitem__)
    url = next(url for url in data if '/openai/codex/' in url)
    rows = json.loads(data[url]); rows[1]['body'] += ' Model configuration deprecated.'; data[url] = json.dumps(rows)
    data[watch.DOCS['claude-hooks']] += 'New hook lifecycle.\n'
    _, report = watch.collect(state, data.__getitem__)
    assert len(report['findings']) == 2
    release = report['findings'][0]
    assert release['change'] == 'release notes changed' and release['version'] == '1.0'
    assert 'model configuration' in release['topics']
    rendered = watch.render(report)
    assert release['url'] in rendered and 'runtime behavior untested' in rendered
    assert 'do not infer a model change' in rendered


def test_failure_keeps_prior_baseline_and_reports_incomplete_coverage():
    data = sources(); state, _ = watch.collect({}, data.__getitem__)
    previous = deepcopy(state)
    def fetch(url):
        if url == watch.DOCS['claude-hooks']: raise OSError('source unavailable')
        return data[url]
    updated, report = watch.collect(state, fetch)
    assert updated['docs'] == previous['docs'] and state == previous
    assert len(report['errors']) == 1 and 'unavailable' in report['errors'][0]
    url = next(url for url in data if '/openai/codex/' in url)
    rows = json.loads(data[url])
    for row in rows: row['id'] += 100
    data[url] = json.dumps(rows)
    assert any('coverage gap' in error for error in watch.collect(state, data.__getitem__)[1]['errors'])


@pytest.mark.parametrize('bad', ['<html>not docs</html>', '', 'permission denied'])
def test_error_pages_are_not_saved_as_a_new_document_baseline(bad):
    data = sources(); data[watch.DOCS['codex-security']] = bad
    state, report = watch.collect({}, data.__getitem__)
    assert 'codex-security' not in state['docs'] and report['errors']


def test_changelog_html_excludes_navigation_and_scripts():
    data = sources()
    url = watch.DOCS['codex-changelog']
    data[url] = '<html><nav>one</nav><main><h1>Changes</h1><p>New hooks</p><script>dynamic()</script></main></html>'
    state, first = watch.collect({}, data.__getitem__)
    assert not first['errors']
    data[url] = data[url].replace('one', 'two').replace('dynamic()', 'other()')
    assert not watch.collect(state, data.__getitem__)[1]['findings']


def test_release_pages_are_bounded_and_assets_are_not_persisted():
    data = sources(); calls = []
    def fetch(url):
        calls.append(url)
        if 'api.github.com' not in url: return data[url]
        repo = 'openai/codex' if '/openai/' in url else 'anthropics/claude-code'
        page = int(url.rsplit('=', 1)[1])
        return json.dumps([{'id': page * 5 + i, 'tag_name': str(page * 5 + i), 'body': 'New hooks',
                           'html_url': f'https://github.com/{repo}/releases/tag/{page * 5 + i}',
                           'assets': ['unused']} for i in range(5)])
    state, report = watch.collect({}, fetch)
    assert len(state['releases']['codex']) == 100 and len(calls) == 46
    assert 'unused' not in json.dumps(state) and not report['errors']


def test_publish_roundtrip_and_no_duplicate_comments():
    state, report = watch.collect({}, sources().__getitem__)
    calls = []
    def api(path, method='GET', value=None):
        calls.append((path, method, value))
        return {'number': 42, 'state': 'open', 'title': watch.TITLE, **value}
    issue = watch.publish(api, None, state, report)
    saved = watch.read_state(issue['body'])
    watch.publish(api, issue, saved, report)
    assert not any(path.endswith('/comments') for path, _, _ in calls)
    changed = deepcopy(report); changed['errors'] = ['new failure']
    watch.publish(api, issue, saved, changed)
    assert len([path for path, _, _ in calls if path.endswith('/comments')]) == 1


def test_ambiguous_closed_or_corrupt_issue_is_not_silently_reset():
    issue = {'title': watch.TITLE, 'state': 'closed', 'number': 42}
    with pytest.raises(ValueError, match='closed'): watch.find_issue(lambda _: [issue])
    with pytest.raises(ValueError, match='Multiple'): watch.find_issue(lambda _: [issue, issue])
    with pytest.raises(ValueError, match='marker'): watch.read_state('lost state')


def test_workflow_is_report_only_and_uses_existing_pinned_actions():
    root = Path(__file__).resolve().parents[2]
    text = (root / '.github/workflows/host-compatibility.yml').read_text()
    assert "cron: '17 14 * * 1'" in text and 'workflow_dispatch:' in text
    assert 'contents: read' in text and 'issues: write' in text and 'contents: write' not in text
    assert 'persist-credentials: false' in text and '--publish' in text
    assert 'default_branch' in text and 'GITHUB_TOKEN: ${{ github.token }}' in text
    assert 'pull_request' not in text and 'secrets.' not in text
