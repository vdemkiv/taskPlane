"""Bounded native human/presentation observations; cooperative, not attestation.

Only a previously selected automatic root source is readable. No CLI path,
caller-supplied message ID, copied transcript, or child event selects evidence.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any, Iterator

from . import claude_worker_observations as native, workflow as w, host_capabilities
from .context import digest

SCHEMA = 'taskplane.native-decision/v1'


class _IncompleteTail(ValueError):
    """A concurrent append is not yet a complete native record."""


def _reverse_records(fd: int, path: Path, info: os.stat_result,
                     budget: native.ReadBudget) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Read the required recent suffix, without loading unrelated old history.

    Keep complete raw records for proof. Never truncate or skip an oversized
    record in the suffix being examined; it may be a newer human decision.
    The caller stops only after finding the latest human and its presentation.
    """
    end = info.st_size
    pending = b''
    while end:
        if not budget.remaining:
            raise native._RecordLimit('Native approval evidence exceeds the aggregate read budget.')
        size = min(16384, end, budget.remaining)
        start = end - size
        block = budget.read(fd, start, size)
        if len(block) != size:
            raise ValueError('Native approval source changed during reading')
        if end == info.st_size and not block.endswith(b'\n'):
            raise _IncompleteTail('Native prompt or presentation is not completely flushed.')
        parts = (block + pending).splitlines(keepends=True)
        offset = end + len(pending)
        complete = parts if start == 0 else parts[1:]
        for line in reversed(complete):
            offset -= len(line)
            if len(line) > native.MAX_LINE_BYTES:
                raise native._RecordLimit(f'Native approval record at byte {offset} exceeds the '
                                          f'{native.MAX_LINE_BYTES}-byte evidence limit; no decision was inferred.')
            if not budget.record():
                raise native._RecordLimit('Native approval evidence exceeds the record-count budget.')
            yield native._decode(line), native._reference(path, info, offset, line)
        pending = parts[0] if start else b''
        if len(pending) > native.MAX_LINE_BYTES:
            raise native._RecordLimit('Native approval record exceeds the evidence limit; no decision was inferred.')
        end = start


def _human(row: dict[str, Any]) -> bool:
    return (row.get('type') == 'user' and row.get('origin') == {'kind': 'human'}
            and row.get('turnOrigin') == 'human'
            and not row.get('isMeta') and not row.get('turnCompanion'))


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
    # The adapter supplies the observed presentation binding; prose is retained
    # as evidence, never parsed for exact question wording.
    return isinstance(text, str) and bool(text.strip())


def capture(state: dict[str, Any], *, host: str, presentation: dict[str, Any],
            event: dict[str, Any] | None = None, semantic_choice: str | None = None) -> dict[str, Any]:
    """Observe the latest real human reply, never scan arbitrary native files.

    Missing flush is pending; capacity and integrity failures are distinct.
    The source, complete record references and true native IDs/times
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
        human_pair = shown_pair = None
        with os.fdopen(fd, 'rb', buffering=0) as stream:
            info = native._regular(stream.fileno())
            if (native._identity(info) != source['identity'] or project != source['project_identity']
                    or info.st_size < source['selected_size']):
                raise ValueError('Native root transcript was replaced or truncated')
            session = native._verify(stream.fileno(), source['session_reference'], budget)
            if not session or session.get('sessionId') != state['root'] or session.get('isSidechain') is True:
                raise ValueError('Native root session reference changed')
            for row, ref in _reverse_records(stream.fileno(), path, info, budget):
                if row.get('sessionId') != state['root'] or row.get('isSidechain') is not False:
                    continue
                if _human(row):
                    if human_pair is not None:
                        break  # A previous human separates this reply from older presentations.
                    human_pair = row, ref
                elif (human_pair is not None and row.get('type') == 'assistant'
                      and isinstance(row.get('message'), dict) and row['message'].get('role') == 'assistant'
                      and _text(row)):
                    shown_pair = row, ref
                    break
            if native._regular(stream.fileno()).st_size < info.st_size:
                raise ValueError('Native root transcript was truncated during observation')
        if human_pair is None:
            return status('not_yet_available', 'The actual human prompt has not been observed.')
        human, human_ref = human_pair
        if human.get('promptSource') not in {None, 'user', 'typed'}:
            return status('unsupported', 'The latest human prompt has an unsupported native source; no older reply was substituted.')
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
        candidate = semantic_choice or (event.get("taskplane_semantic_choice") if event else None)
        if candidate not in {"approved", "changes_requested", "rejected", "cancelled"}:
            return {"status": "semantic_candidate_required", "excerpt": excerpt,
                    "reason": "The orchestrator must interpret this actual human response for the current checkpoint."}
        choice = candidate
        named = re.findall(r'\b[0-9a-f]{32}\b', excerpt.casefold())
        if any(identifier != expected['checkpoint'] for identifier in named):
            return status('conflict', 'The response names a different checkpoint.')
        if shown_pair is None:
            return status('not_yet_available', 'The original checkpoint presentation has not been observed.')
        assistant, shown_ref = shown_pair
        text = _text(assistant)
        assert text is not None
        if (len(text) > 16384 or not _shown(text, state, presentation.get('artifact', ''))
                or not native._stamp(stage['submitted_at']) <= native._stamp(assistant.get('timestamp')) < observed):
            return status('needs_clarification', 'The preceding assistant presentation does not identify this checkpoint.')
        if not all(isinstance(row.get('uuid'), str) and native._UUID.fullmatch(row['uuid']) for row in (human, assistant)):
            raise ValueError('Native message identity is missing')
        envelope = {'schema': SCHEMA, 'event_id': human['uuid'], 'choice': choice,
            'binding': expected, 'excerpt': excerpt, 'recorder': 'native_prompt_hook',
            'source': {'kind': 'native_prompt', 'reference': human['uuid'], 'conversation': state['root'],
                       'actor': 'user', 'automatic': False, 'observed_at': human['timestamp']},
            'presentation': {'checkpoint': expected['checkpoint'], 'reference': assistant['uuid'],
                             'at': assistant['timestamp'], 'text': text},
            'native_proof': {'source_sha256': source['sha256'], 'human': human_ref, 'presentation': shown_ref}}
        return {'status': 'observed', 'decision': envelope}
    except _IncompleteTail as exc:
        return status('not_yet_available', str(exc))
    except native._RecordLimit as exc:
        return status('unsupported', str(exc))
    except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError):
        return status('conflict', 'Selected native approval evidence is unavailable, malformed or changed.')


def verify(value: dict[str, Any], state: dict[str, Any], host: str,
           presentation: dict[str, Any]) -> dict[str, Any]:
    w.require(isinstance(value.get('event_id'), str), 'invalid_evidence', 'Native event identity is missing.')
    previous = state.get('decisions', {}).get(value.get('event_id'))
    if previous is not None:
        stage = w.current(state)
        w.require(value.get('binding') == previous.get('binding')
                  and stage.get('id') == previous['binding']['visit']
                  and stage.get('packet', {}).get('checkpoint') == previous['binding']['checkpoint'],
                  'stale_checkpoint', 'Native replay must retain its original checkpoint.')
        # Re-read the pinned native source at its original checkpoint binding.
        # This temporary copy cannot change workflow authority or accept a newer
        # response: the complete freshly captured envelope must still match.
        state = deepcopy(state)
        state['revision'] = previous['binding']['revision']
        state['visits'][state['index']]['decision'] = 'awaiting_human_approval'
    fresh = capture(state, host=host, presentation=presentation, semantic_choice=value.get("choice"))
    if previous is not None and fresh.get('status') == 'observed':
        # Accepting Plan may narrow the scope. Replaying its exact observation
        # retains the original scope authorization, never the newly narrowed one.
        fresh['decision']['binding']['scope_digest'] = previous['binding']['scope_digest']
    w.require(fresh.get('status') == 'observed' and digest(fresh['decision']) == digest(value),
              'invalid_evidence', 'Native approval is stale or its original human/presentation proof changed.')
    return {**value, 'schema': 'taskplane.observed-decision/v1'}
