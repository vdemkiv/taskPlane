"""Explicit native readers and one scoped heartbeat create; no wildcard MCP grants.

Scope is cooperative request provenance, not permission or connector availability.
The host must still enforce network access, app connection and human permissions.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any
from urllib.parse import urlsplit

from . import workflow as w
from .context import digest
from .context_handoff import binding

READERS = {
    'webrun': ('codex', 'web'),
    'web.run': ('codex', 'web'),
    'web__run': ('codex', 'web'),
    'mcp__codex_apps__github__fetch_issue': ('codex', 'github_issue'),
    'mcp__codex_apps__github_fetch_issue': ('codex', 'github_issue'),
    'WebFetch': ('claude', 'web'),
    'WebSearch': ('claude', 'web'),
}
AUTOMATION = 'mcp__codex_app__automation_update'


def _text(value: Any, limit: int = 32768) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and '\0' not in value


def _url(value: Any) -> bool:
    if not _text(value, 4096):
        return False
    try:
        url = urlsplit(value)
        return url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        return False


def _web(args: dict[str, Any]) -> bool:
    if set(args) - {'open', 'search_query', 'response_length'} or not (args.get('open') or args.get('search_query')):
        return False
    if args.get('response_length', 'short') not in ('short', 'medium', 'long'):
        return False
    for name in ('open', 'search_query'):
        values = args.get(name, [])
        if not isinstance(values, list) or len(values) > (4 if name == 'search_query' else 8):
            return False
        for row in values:
            if not isinstance(row, dict):
                return False
            if name == 'open':
                if set(row) - {'ref_id', 'lineno'} or not _url(row.get('ref_id')):
                    return False
                if 'lineno' in row and (type(row['lineno']) is not int or row['lineno'] < 0):
                    return False
            else:
                if set(row) - {'q', 'domains', 'recency'} or not _text(row.get('q')):
                    return False
                if 'recency' in row and (type(row['recency']) is not int or row['recency'] < 1):
                    return False
                if 'domains' in row and not _domains(row['domains']):
                    return False
    return True


def _domains(value: Any) -> bool:
    return (isinstance(value, list) and 0 < len(value) <= 20
            and all(isinstance(item, str) and re.fullmatch(r'[A-Za-z0-9.-]{1,253}', item) for item in value))


def _issue(args: dict[str, Any]) -> bool:
    # Only the captured selector is verified. Other connector selectors remain
    # unsupported until their schemas and repository resolution are tested.
    return (set(args) == {'repository_full_name', 'issue_number'}
            and isinstance(args['repository_full_name'], str)
            and bool(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args['repository_full_name']))
            and type(args['issue_number']) is int and args['issue_number'] > 0)


def _heartbeat(args: Any) -> bool:
    return (isinstance(args, dict)
            and {'mode', 'kind', 'name', 'prompt', 'rrule', 'status', 'destination'} <= set(args)
            and not set(args) - {'mode', 'kind', 'name', 'prompt', 'rrule', 'status', 'destination',
                                'targetThreadId', 'notificationPolicy'}
            and args['mode'] == 'create' and args['kind'] == 'heartbeat'
            and args['status'] in ('ACTIVE', 'PAUSED') and args['destination'] == 'thread'
            and _text(args['name'], 200) and _text(args['prompt'])
            and isinstance(args['rrule'], str)
            and bool(re.fullmatch(r'FREQ=(HOURLY|DAILY|WEEKLY);INTERVAL=[1-9][0-9]{0,2}', args['rrule']))
            and args.get('notificationPolicy') in (None, 'failed_runs_only')
            and ('targetThreadId' not in args or _text(args['targetThreadId'], 200)))


def validate_scope(scope: dict[str, Any]) -> None:
    reads = scope.get('external_reads')
    if reads is not None:
        w.require(isinstance(reads, dict) and set(reads) == {'host', 'operations', 'request_reference'}
                  and reads['host'] in ('codex', 'claude') and _text(reads['request_reference'], 512)
                  and isinstance(reads['operations'], list) and bool(reads['operations'])
                  and all(isinstance(op, str) and op in ({'web', 'github_issue'} if reads['host'] == 'codex'
                                                       else {'web'}) for op in reads['operations'])
                  and len(set(reads['operations'])) == len(reads['operations']),
                  'invalid_evidence', 'external_reads needs an exact host, supported operations and user request reference.')
    action = scope.get('native_automation')
    if action is not None:
        w.require(isinstance(action, dict) and set(action) == {'host', 'phase', 'request_reference', 'request'}
                  and action['host'] == 'codex' and action['phase'] in w.PHASES
                  and _text(action['request_reference'], 512) and _heartbeat(action['request']),
                  'invalid_evidence', 'native_automation supports one explicit Codex thread heartbeat create request.')


def read(tool: str, args: dict[str, Any], host: str, state: dict[str, Any], *, root: bool) -> bool:
    if tool not in READERS:
        return False
    expected, operation = READERS[tool]
    w.require(host == expected, 'unsupported_authority',
              f'{tool} requires {expected}. Use the current host native web reader when exposed and scoped; '
              'a private GitHub issue still requires an authorized connected reader.')
    scoped = state['scope'].get('external_reads', {})
    w.require(root and scoped.get('host') == host and operation in scoped.get('operations', [])
              and scoped.get('request_reference') == state.get('request_provenance', {}).get('reference'),
              'scope_violation', 'External read is not declared for this root request. Initialize an exact external_reads scope; '
              'do not widen a sealed scope or bypass host access restrictions.')
    valid = (_issue(args) if operation == 'github_issue' else _web(args) if host == 'codex' else
             set(args) == {'url', 'prompt'} and _url(args.get('url')) and _text(args.get('prompt'))
             if tool == 'WebFetch' else
             not set(args) - {'query', 'allowed_domains', 'blocked_domains'} and _text(args.get('query'))
             and all(_domains(args[key]) for key in ('allowed_domains', 'blocked_domains') if key in args))
    w.require(valid, 'scope_violation', 'Unsupported native read arguments; only the documented read schema is admitted.')
    return True


def automation(event: dict[str, Any], host: str, state: dict[str, Any], *, root: bool) -> bool:
    if (event.get('tool_name') or event.get('tool')) != AUTOMATION:
        return False
    w.require(host == 'codex', 'unsupported_authority',
              'Native Codex heartbeat creation is unavailable in Claude. Preserve the requested schedule as a proposal; '
              'use a separately authorized scheduler with a supported adapter. No automation was created.')
    args, scope = event.get('tool_input', {}), state['scope'].get('native_automation', {})
    observed = event.get('taskplane_observed_binding', {})
    call = event.get('tool_use_id') or event.get('call_id')
    w.require(root and event.get('taskplane_automatic_hook') is True
              and observed.get('root') == observed.get('principal') == state['root'] and _text(call, 512),
              'scope_violation', 'Automation requires an automatic observed root call; workers cannot schedule work.')
    w.require(_heartbeat(args) and scope.get('host') == host and scope.get('phase') == w.current(state)['phase']
              and scope.get('request_reference') == state.get('request_provenance', {}).get('reference')
              and args == scope.get('request') and args.get('targetThreadId', state['root']) == state['root'],
              'scope_violation', 'Automation is not this phase\'s exact scoped heartbeat request. '
              'Declare native_automation at initialization; unknown modes, other threads and broader mutations remain denied.')
    old = state.get('native_automation_dispatch')
    record = {'call_id': call, 'request_digest': digest(args), 'binding': binding(state),
              'request_reference': scope['request_reference'], 'status': 'admitted_outcome_unverified'}
    w.require(old is None or old == record, 'scope_violation',
              'This heartbeat request was already admitted. Inspect the native automation inventory before any retry; '
              'an unknown outcome is not proof of failure or permission to create a duplicate.')
    state['native_automation_dispatch'] = deepcopy(record)
    return True
