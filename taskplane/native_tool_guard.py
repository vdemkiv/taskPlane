"""Explicit native readers and one scoped heartbeat create; no wildcard MCP grants.

Scope is cooperative request provenance, not permission or connector availability.
The host must still enforce network access, app connection and human permissions.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from . import workflow as w
from .context import digest
from .context_handoff import binding

# These are reviewed entrypoints, not a prefix policy. In particular, MCP
# get/create names and multi-mode mutation tools gain no authority by spelling.
# App tools are exposed with both connector-qualified and normalized aliases.
_GITHUB_READERS = (
    'compare_commits', 'download_user_content', 'download_workflow_artifact',
    'fetch', 'fetch_blob', 'fetch_commit', 'fetch_commit_workflow_runs', 'fetch_file',
    'fetch_issue', 'fetch_issue_comments', 'fetch_pr', 'fetch_pr_comments',
    'fetch_pr_file_patch', 'fetch_pr_patch', 'fetch_workflow_job_logs',
    'fetch_workflow_job_steps', 'fetch_workflow_run_artifacts', 'fetch_workflow_run_jobs',
    'get_commit_combined_status', 'get_issue_comment_reactions', 'get_pr_diff',
    'get_pr_info', 'get_pr_reactions', 'get_pr_review_comment_reactions', 'get_profile',
    'get_repo', 'get_repo_collaborator_permission', 'get_user_login',
    'get_users_recent_prs_in_repo', 'list_installations', 'list_installed_accounts',
    'list_pr_changed_filenames', 'list_pull_request_review_threads',
    'list_pull_request_reviews', 'list_recent_issues', 'list_repositories',
    'list_repositories_by_affiliation', 'list_repositories_by_installation',
    'list_user_org_memberships', 'list_user_orgs', 'search', 'search_branches',
    'search_commits', 'search_installed_repositories_streaming',
    'search_installed_repositories_v2', 'search_issues', 'search_prs',
    'search_repositories',
)
_CODEX_READERS = (
    'check_app_update', 'get_handoff_status', 'get_usage_limits',
    'list_archived_threads', 'list_artifacts',
    'list_projects', 'list_threads', 'load_workspace_dependencies',
    'read_page_reference', 'read_thread', 'read_thread_terminal', 'wait_threads',
)
# Worktree creation status belongs to recovery admission, which correlates each
# poll with its pending operation before accepting a completed destination.
READERS = {
    **{f'mcp__codex_apps__github{separator}{name}': 'codex'
       for name in _GITHUB_READERS for separator in ('__', '_')},
    **{f'mcp__codex_app__{name}': 'codex' for name in _CODEX_READERS},
    'mcp__code_review__pull_requests_checks': 'codex',
    'mcp__code_review_pull_requests_checks': 'codex',
    'webrun': 'codex',
    'web.run': 'codex',
    'web__run': 'codex',
    'clockcurr_time': 'codex',
    'clock.curr_time': 'codex',
    'clock__curr_time': 'codex',
    'WebFetch': 'claude',
    'WebSearch': 'claude',
}
AUTOMATION = 'mcp__codex_app__automation_update'


def _text(value: Any, limit: int = 32768) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and '\0' not in value


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
    action = scope.get('native_automation')
    if action is not None:
        w.require(isinstance(action, dict) and set(action) == {'host', 'phase', 'request_reference', 'request'}
                  and action['host'] == 'codex' and action['phase'] in w.PHASES
                  and _text(action['request_reference'], 512) and _heartbeat(action['request']),
                  'invalid_evidence', 'native_automation supports one explicit Codex thread heartbeat create request.')


def read(tool: str, host: str) -> bool:
    """Recognize pure reader entry points; their host validates request schemas.

    Recognition grants neither mutation authority nor connector/network access.
    Do not duplicate the reader's argument parser or restrict result references.
    """
    if tool not in READERS:
        return False
    expected = READERS[tool]
    w.require(host == expected, 'unsupported_authority',
              f'{tool} requires {expected}. Use the current host native web reader when exposed and permitted; '
              'a private GitHub issue still requires an authorized connected reader.')
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
