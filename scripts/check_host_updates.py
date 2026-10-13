"""Report official host changes; heuristic triage, never automatic compatibility certification."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import urllib.request

TITLE = 'taskPlane host compatibility watch'
MARKER = 'taskplane-host-watch/v1'
MAX_RESPONSE = 2 * 1024 * 1024
MAX_RELEASE_RESPONSE = 8 * 1024 * 1024
RELEASES = {'codex': 'openai/codex', 'claude': 'anthropics/claude-code'}
DOCS = {
    'codex-changelog': 'https://learn.chatgpt.com/docs/changelog',
    'codex-security': 'https://learn.chatgpt.com/docs/security.md',
    'codex-subagents': 'https://learn.chatgpt.com/docs/agent-configuration/subagents.md',
    'claude-hooks': 'https://code.claude.com/docs/en/hooks.md',
    'claude-permissions': 'https://code.claude.com/docs/en/permissions.md',
    'claude-subagents': 'https://code.claude.com/docs/en/sub-agents.md',
}
TOPICS = {
    'permissions / approvals': (r'permission|approval|sandbox|guardian|policy',
        'Review taskPlane/proofPlane authority and denial contracts; test in an isolated host before adoption.'),
    'workers / hooks / tools': (r'subagent|sub.agent|hook|tool.?use|tool.?call|schema|mcp|dispatch',
        'Compare exposed schemas, hook ordering, child identity and completion with the host adapters.'),
    'state / recovery / context': (r'transcript|compaction|resume|recovery|context|session|worktree',
        'Check provenance, bounded readers, context intake and recovery invariants; preserve sealed evidence.'),
    'features / modes / deprecations': (r'added|new |mode|deprecated|removed|breaking|browser|plugin',
        'Assess usefulness and supported alternatives; turn relevant changes into scoped follow-up work.'),
    'model configuration': (r'\bmodel\b|reasoning|effort',
        'Separate host configuration from model identity; do not infer a model change or regression.'),
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Source redirected; review its canonical URL before trusting a new destination.')


class MainText(HTMLParser):
    """Hash visible changelog text, excluding navigation and executable markup."""
    def __init__(self):
        super().__init__()
        self.active = False
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'main': self.active = True
        if tag in {'script', 'style'}: self.skip += 1

    def handle_endtag(self, tag):
        if tag == 'main': self.active = False
        if tag in {'script', 'style'}: self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if self.active and not self.skip and data.strip(): self.parts.append(' '.join(data.split()))


def request(url, *, token='', method='GET', value=None):
    headers = {'User-Agent': 'taskPlane-host-watch', 'Accept': 'application/json, text/plain'}
    if token:
        if not url.startswith('https://api.github.com/'):
            raise ValueError('GitHub credentials may only be sent to api.github.com')
        headers['Authorization'] = 'Bearer ' + token
    data = None if value is None else json.dumps(value).encode()
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    limit = MAX_RELEASE_RESPONSE if url.startswith('https://api.github.com/repos/') and '/releases?' in url else MAX_RESPONSE
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f'Source exceeds the {limit}-byte response bound; coverage is incomplete.')
    return raw.decode('utf-8')


def fingerprint(text):
    return hashlib.sha256(text.encode()).hexdigest()


def topics(text):
    return [name for name, (pattern, _) in TOPICS.items() if re.search(pattern, text, re.I)]


def collect(previous, fetch):
    """Compare bounded official snapshots. Failed sources keep their prior baseline."""
    state = json.loads(json.dumps(previous))
    state.setdefault('releases', {})
    state.setdefault('docs', {})
    findings, errors, observed = [], [], {}
    for host, repo in RELEASES.items():
        try:
            rows = []
            # Release asset metadata can exceed a small response budget. Read
            # bounded pages and retain only the fields this check consumes.
            for page in range(1, 21):
                batch = json.loads(fetch(f'https://api.github.com/repos/{repo}/releases?per_page=5&page={page}'))
                if not isinstance(batch, list): raise ValueError('Release feed is malformed')
                rows.extend({key: row.get(key) for key in ('id', 'tag_name', 'html_url', 'body', 'prerelease', 'draft')}
                            for row in batch)
                if len(batch) < 5: break
            if not rows:
                raise ValueError('Release feed is empty or malformed')
            old = state['releases'].get(host, {})
            current = {}
            stable = prerelease = None
            for row in rows:
                if row.get('draft'):
                    continue
                tag, link, body = row['tag_name'], row['html_url'], row.get('body') or ''
                if not isinstance(tag, str) or not link.startswith(f'https://github.com/{repo}/releases/'):
                    raise ValueError('Release identity is outside the official repository')
                key = str(row['id'])
                current[key] = fingerprint(tag + '\n' + body)
                is_pre = bool(row.get('prerelease'))
                first = prerelease is None if is_pre else stable is None
                if is_pre and first: prerelease = tag
                if not is_pre and first: stable = tag
                if current[key] != old.get(key) and (old or first):
                    findings.append({'source': host, 'version': tag, 'url': link,
                        'change': 'release notes changed' if key in old else 'new release' if old else 'initial baseline',
                        'prerelease': is_pre, 'topics': topics(body), 'sha256': current[key],
                        'confidence': 'Official published notes; relevance is heuristic, runtime behavior untested.'})
            if not current:
                raise ValueError('Release feed contains no published releases')
            if old and not set(old).intersection(current):
                errors.append(f'{host}: no overlap with the prior 100-release window; review the release archive for a coverage gap.')
            state['releases'][host] = current
            observed[host] = {'latest_stable': stable, 'latest_prerelease': prerelease}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            errors.append(f'{host}: {type(exc).__name__}: {exc}')
    for name, url in DOCS.items():
        try:
            text = fetch(url)
            if name == 'codex-changelog' and '<html' in text[:1000].lower():
                parser = MainText(); parser.feed(text)
                text = '# Official changelog\n' + '\n'.join(parser.parts) if parser.parts else ''
            if not text.strip() or '<html' in text[:1000].lower() or not text.lstrip().startswith(('#', '>')):
                raise ValueError('Expected official Markdown documentation, not an error/HTML response')
            digest = fingerprint(text)
            old = state['docs'].get(name)
            if digest != old:
                findings.append({'source': name, 'version': 'unversioned documentation', 'url': url,
                    'change': 'documentation changed' if old else 'initial baseline',
                    'topics': topics(text), 'sha256': digest,
                    'confidence': 'Official document hash changed; semantics and host applicability require review.'})
            state['docs'][name] = digest
        except (OSError, ValueError, TypeError) as exc:
            errors.append(f'{name}: {type(exc).__name__}: {exc}')
    return state, {'checked_at': datetime.now(timezone.utc).isoformat(), 'observed_versions': observed,
                   'findings': findings, 'errors': errors}


def safe(text):
    return html.escape(str(text)).replace('@', '&#64;').replace('|', '&#124;').replace('\n', ' ')[:500]


def render(report):
    lines = ['# Host compatibility review', '', 'Checked: ' + report['checked_at'], '',
        'Automated official-source change detection and keyword triage. **Semantic review and native testing remain required.**',
        'This task does not update hosts, install plugins, change harness settings, or infer model regressions.', '',
        '## Observed upstream versions', '']
    for host, versions in report['observed_versions'].items():
        lines.append(f'- {host}: stable `{safe(versions["latest_stable"])}`; prerelease `{safe(versions["latest_prerelease"])}`. Installed versions are not inspected.')
    lines += ['', '## Changed sources', '']
    if not report['findings']:
        lines.append('No changed sources detected in successfully read sources. This is not a compatibility pass.')
    for finding in report['findings'][:40]:
        lines += [f'- [{safe(finding["source"])} — {safe(finding["version"])}]({finding["url"]}): {finding["change"]}.',
                  f'  Confidence: {finding["confidence"]}',
                  f'  Candidate relevance: {", ".join(finding["topics"]) or "No keyword match; read notes for unclassified changes."}']
    if len(report['findings']) > 40:
        lines.append('Additional changed sources are retained in the complete JSON artifact; this display is bounded to 40.')
    lines += ['', '## Recommended next actions', '']
    matched = {topic for finding in report['findings'] for topic in finding['topics']}
    for topic in TOPICS:
        if topic in matched: lines.append(f'- {TOPICS[topic][1]}')
    lines += ['- Record a human relevance/disposition comment here. A published feature is not proof it works in either host.',
              '', '## Source failures / coverage gaps', '']
    lines.extend('- ' + safe(error) for error in report['errors'])
    if not report['errors']: lines.append('None detected in the configured sources.')
    return '\n'.join(lines) + '\n'


def read_state(body):
    match = re.search(r'<!-- ' + MARKER + r'\n(.*?)\n-->', body, re.S)
    if not match:
        raise ValueError('Watch issue has no valid state marker; refusing to reset deduplication.')
    state = json.loads(match[1])
    if not isinstance(state, dict) or set(state) - {'releases', 'docs', 'last_report'}:
        raise ValueError('Watch state is malformed')
    if not all(isinstance(state.get(key, {}), dict) for key in ('releases', 'docs')):
        raise ValueError('Watch source state is malformed')
    return state


def find_issue(api):
    matches = []
    for page in range(1, 6):
        rows = api(f'/issues?state=all&per_page=100&page={page}')
        matches.extend(row for row in rows if row.get('title') == TITLE and 'pull_request' not in row)
        if len(rows) < 100: break
    else:
        raise ValueError('Issue inventory exceeds 500 entries; configure a narrower persistent report lookup.')
    if len(matches) > 1:
        raise ValueError('Multiple watch issues exist; refusing an ambiguous update.')
    if matches and matches[0]['state'] != 'open':
        raise ValueError('Watch issue is closed; reopen it or disable the scheduled workflow.')
    return matches[0] if matches else None


def publish(api, issue, state, report):
    text = render(report)
    report_id = fingerprint(json.dumps({k: v for k, v in report.items() if k != 'checked_at'}, sort_keys=True))
    # Durable report history uses comments only for changed findings/failures.
    # Save the dedup marker after the comment succeeds, never before delivery.
    if issue and (report['findings'] or report['errors']) and report_id != state.get('last_report'):
        api(f'/issues/{issue["number"]}/comments', 'POST', {'body': text})
    state = {**state, 'last_report': report_id}
    body = text + '\n<!-- ' + MARKER + '\n' + json.dumps(state, sort_keys=True) + '\n-->'
    if len(body) > 60000:
        raise ValueError('Report exceeds the issue body bound; do not truncate persisted state.')
    return api(f'/issues/{issue["number"]}' if issue else '/issues', 'PATCH' if issue else 'POST',
               {'title': TITLE, 'body': body})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('reports/host-watch'))
    parser.add_argument('--state', type=Path, help='Optional prior local state for a read-only dry run')
    parser.add_argument('--publish', action='store_true', help='Update the dedicated repository issue with GITHUB_TOKEN')
    args = parser.parse_args()
    token = os.environ.get('GITHUB_TOKEN', '')
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    issue = None
    state = json.loads(args.state.read_text()) if args.state else {}
    def api(path, method='GET', value=None):
        return json.loads(request(f'https://api.github.com/repos/{repo}' + path, token=token, method=method, value=value))
    if args.publish:
        if not token or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo) or args.state:
            parser.error('Publishing needs the workflow repository/token and its issue-backed state, not --state.')
        issue = find_issue(api)
        state = read_state(issue['body']) if issue else {}
    def fetch(url):
        return request(url, token=token if url.startswith('https://api.github.com/') else '')
    state, report = collect(state, fetch)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'report.md').write_text(render(report), encoding='utf-8')
    (args.output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    (args.output_dir / 'state.json').write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    if args.publish:
        result = publish(api, issue, state, report)
        print(result['html_url'])
    print(f'{len(report["findings"])} changed sources; {len(report["errors"])} failures/coverage gaps')
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
