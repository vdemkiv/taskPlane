"""Bounded native human/presentation observations; cooperative, not attestation.

Only a previously selected automatic root source is readable. No CLI path,
caller-supplied message ID, copied transcript, or child event selects evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any

from . import claude_worker_observations as native, workflow as w, host_capabilities
from .context import digest
from .workflow_approval import conversational_choice

SCHEMA = 'taskplane.native-decision/v1'


def _text(row: dict[str, Any]) -> str | None:
    message = row.get('message')
    if not isinstance(message, dict):
        return None
    content = message.get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Thinking is not presentation; tool calls/results are not user text.
        if any(not isinstance(part, dict) or part.get('type') not in {'text', 'thinking'} for part in content):
            return None
        return '\n'.join(part['text'] for part in content if part.get('type') == 'text'
                         and isinstance(part.get('text'), str))
    return None


def _shown(text: str, state: dict[str, Any], artifact: str) -> bool:
    stage = w.current(state)
    checkpoint = stage['packet']['checkpoint']
    phase = stage['phase']
    if not re.search(r'\b(?:approv\w*|accept\w*|sign[ -]off)\b', text, re.I):
        return False
    explicit = checkpoint in text
    named = (state['run'] in text and re.search(r'\b' + phase + r'\b', text, re.I)
             and re.search(r'\brevision\s*[:#]?\s*' + str(state['revision']) + r'\b', text, re.I))
    linked = bool(artifact and (artifact in text or str(Path(artifact).relative_to(state['workspace'])) in text))
    return bool(explicit or named or linked)


def capture(state: dict[str, Any], *, host: str, presentation: dict[str, Any],
            event: dict[str, Any] | None = None) -> dict[str, Any]:
    """Observe the latest real human reply, never scan arbitrary native files.

    Missing flush/oversized tail is recoverable; a subsequent automatic root
    hook rechecks it. The source, record references and true native IDs/times
    are preserved. No decision or phase state is changed here.
    """
    def status(value: str, reason: str) -> dict[str, Any]:
        return {'status': value, 'reason': reason}

    if host != 'claude':
        return status('unsupported_host', 'This host has no selected native chat capture adapter; retain its observed-envelope path.')
    if event is not None:
        observed = event.get('taskplane_observed_binding', {})
        if (event.get('taskplane_automatic_hook') is not True or event.get('session_id') != state['root']
                or not isinstance(observed, dict)
                or observed.get('root') != state['root'] or observed.get('principal') != state['root']
                or observed.get('profile') != 'native_workflow' or event.get('agent_id')
                or event.get('taskplane_runtime_identity') != host_capabilities.runtime_identity()
                or event.get('parent_session_id') or event.get('hook_event_name') not in
                {'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop'}):
            return status('unsupported', 'Native approval capture requires the automatic owning root hook.')
    stage = w.current(state)
    if stage['decision'] != 'awaiting_human_approval' or not stage.get('packet'):
        return status('not_applicable', 'There is no pending human checkpoint.')
    expected = w.binding(state, stage['packet'])
    source = state.get('claude_transcript_source')
    if not source:
        return status('not_yet_available', 'The automatic root transcript source has not been observed.')
    try:
        if (not native.supported_reader() or not native._valid_seal(source, 'taskplane.claude-transcript-source/v1')
                or source.get('parent') != state['root']
                or event is not None and event.get('transcript_path', source['path']) != source['path']):
            raise ValueError('Native root source is unavailable or changed')
        path = native._source_path(state['root'], source['path'])
        fd, project = native._open_source(path)
        budget = native.ReadBudget()
        records: list[tuple[dict[str, Any], dict[str, Any]]] = []
        with os.fdopen(fd, 'rb', buffering=0) as stream:
            info = native._regular(stream.fileno())
            if (native._identity(info) != source['identity'] or project != source['project_identity']
                    or info.st_size < source['selected_size']):
                raise ValueError('Native root transcript was replaced or truncated')
            session = native._verify(stream.fileno(), source['session_reference'], budget)
            if not session or session.get('sessionId') != state['root'] or session.get('isSidechain') is True:
                raise ValueError('Native root session reference changed')
            start = max(0, info.st_size - budget.remaining)
            raw = budget.read(stream.fileno(), start, info.st_size - start)
            if start:
                split = raw.find(b'\n')
                if split < 0:
                    return status('not_yet_available', 'Native approval tail exceeds the bounded reader.')
                start += split + 1
                raw = raw[split + 1:]
            if raw and not raw.endswith(b'\n'):
                return status('not_yet_available', 'Native prompt or presentation is not completely flushed.')
            for line in raw.splitlines(keepends=True):
                if not budget.record() or len(line) > native.MAX_LINE_BYTES:
                    return status('not_yet_available', 'Native approval tail exceeds the bounded reader.')
                row = native._decode(line)
                if row.get('sessionId') == state['root'] and row.get('isSidechain') is False:
                    records.append((row, native._reference(path, info, start, line)))
                start += len(line)
            if native._regular(stream.fileno()).st_size < info.st_size:
                raise ValueError('Native root transcript was truncated during observation')
        humans = [(index, row, ref) for index, (row, ref) in enumerate(records)
                  if row.get('type') == 'user'
                  and row.get('origin') == {'kind': 'human'} and row.get('turnOrigin') == 'human'
                  and row.get('promptSource') in {None, 'user', 'typed'} and not row.get('isMeta')
                  and not row.get('turnCompanion')]
        if not humans:
            return status('not_yet_available', 'The actual human prompt has not been observed.')
        index, human, human_ref = humans[-1]
        if not isinstance(human.get('message'), dict) or human['message'].get('role') != 'user':
            return status('conflict', 'The latest native human message is malformed.')
        excerpt = _text(human)
        if event is not None and event.get('hook_event_name') == 'UserPromptSubmit' and event.get('prompt') != excerpt:
            return status('not_yet_available', 'The current human prompt has not been flushed yet.')
        observed = native._stamp(human.get('timestamp'))
        if not native._stamp(stage['submitted_at']) <= observed <= datetime.now(timezone.utc):
            return status('not_yet_available', 'No human response follows this checkpoint submission.')
        if not isinstance(excerpt, str) or not 0 < len(excerpt) <= 4096:
            return status('needs_clarification', 'Human response is outside the bounded decision contract.')
        choice = conversational_choice(excerpt)
        if choice is None:
            return status('needs_clarification', 'The response does not express one unqualified checkpoint decision.')
        named = re.findall(r'\b[0-9a-f]{32}\b', excerpt.casefold())
        if any(identifier != expected['checkpoint'] for identifier in named):
            return status('conflict', 'The response names a different checkpoint.')
        prior_human = humans[-2][0] if len(humans) > 1 else -1
        shown = [(row, ref) for row, ref in records[prior_human + 1:index]
                 if row.get('type') == 'assistant' and isinstance(row.get('message'), dict)
                 and row['message'].get('role') == 'assistant'
                 and _text(row)]
        if not shown:
            return status('not_yet_available', 'The original checkpoint presentation has not been observed.')
        assistant, shown_ref = shown[-1]
        text = _text(assistant)
        assert text is not None
        if (len(text) > 16384 or not _shown(text, state, presentation.get('artifact', ''))
                or not native._stamp(stage['submitted_at']) <= native._stamp(assistant.get('timestamp')) < observed):
            return status('needs_clarification', 'The preceding assistant presentation does not identify this checkpoint.')
        if (len(re.findall(r'(?m)^\s*\d+[.)]\s', text)) > 1 or text.count('?') > 1) and not re.search(
                r'\b(?:approv\w*|accept\w*|confirm\w*|sign[ -]off|reject\w*|cancel\w*)\b', excerpt, re.I):
            return status('needs_clarification', 'A brief assent follows multiple choices; its target is ambiguous.')
        if not all(isinstance(row.get('uuid'), str) and native._UUID.fullmatch(row['uuid']) for row in (human, assistant)):
            raise ValueError('Native message identity is missing')
        envelope = {'schema': SCHEMA, 'event_id': human['uuid'], 'choice': choice,
            'binding': expected, 'excerpt': excerpt, 'recorder': 'native_prompt_hook',
            'source': {'kind': 'native_prompt', 'reference': human['uuid'], 'conversation': state['root'],
                       'actor': 'user', 'automatic': False, 'observed_at': human['timestamp']},
            'presentation': {'checkpoint': expected['checkpoint'], 'reference': assistant['uuid'],
                             'at': assistant['timestamp']},
            'native_proof': {'source_sha256': source['sha256'], 'human': human_ref, 'presentation': shown_ref}}
        return {'status': 'observed', 'decision': envelope}
    except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError):
        return status('conflict', 'Selected native approval evidence is unavailable, malformed or changed.')


def verify(value: dict[str, Any], state: dict[str, Any], host: str,
           presentation: dict[str, Any]) -> dict[str, Any]:
    fresh = capture(state, host=host, presentation=presentation)
    w.require(fresh.get('status') == 'observed' and digest(fresh['decision']) == digest(value),
              'invalid_evidence', 'Native approval is stale or its original human/presentation proof changed.')
    return {**value, 'schema': 'taskplane.observed-decision/v1'}
