#!/usr/bin/env python3
"""Run the same product checks used by CI, with failures visible in the log."""
from __future__ import annotations
import argparse
import base64
import binascii
from collections import Counter
import hashlib
import io
import json
import os
import platform
import re
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from xml.parsers import expat
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
SUITES = ("all", "portability", "core", "native", "packages", "capacity")


EVIDENCE_SCHEMA = 'taskplane.raw-evidence/zlib-base64-v1'
EVIDENCE_CHUNK_BYTES = 49152


def measurement_counters(samples: list) -> list:
    """Readable projection only; the referenced samples remain complete."""
    def counters(row):
        if not isinstance(row, dict):
            return {'malformed_sample': True}
        result = {key: value for key, value in row.items() if type(value) in (str, int, float, bool, type(None))}
        if isinstance(row.get('refreshes'), list):
            result['refreshes'] = [counters(refresh) for refresh in row['refreshes']]
        return result
    return [counters(sample) for sample in samples]


def _json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('utf-8')


def pack_evidence(document: dict) -> dict:
    """Lossless transport only: callers validate original observations first.

    References are content-addressed original bytes. Views restore the former
    JSON types; large samples are JSON values, never interpreted as native fields.
    No raw-evidence size limit is imposed by this codec.
    """
    if 'evidence_encoding' in document:
        raise ValueError('document is already encoded')
    blobs, sample_counters = {}, {}

    def reference(raw, view):
        sha = hashlib.sha256(raw).hexdigest()
        if sha not in blobs:
            compressed = zlib.compress(raw)
            blobs[sha] = {'encoding': 'zlib+base64', 'bytes': len(raw), 'sha256': sha,
                          'compressed_bytes': len(compressed),
                          'chunks': [base64.b64encode(compressed[i:i + EVIDENCE_CHUNK_BYTES]).decode('ascii')
                                     for i in range(0, len(compressed), EVIDENCE_CHUNK_BYTES)]}
        return {'$evidence': sha, 'view': view}

    def execution_path(path):
        return not path or (len(path) == 2 and path[0] == 'executions' and type(path[1]) is int)

    def encode(value, path=()):
        key = path[-1] if path else ''
        if isinstance(value, dict):
            if (len(path) >= 2 and path[-2] in ('raw_files', 'image_artifacts') and execution_path(path[:-2])):
                raw = base64.b64decode(value['base64'], validate=True)
                if (type(value.get('bytes')) is not int or value['bytes'] != len(raw)
                        or value.get('sha256') != hashlib.sha256(raw).hexdigest()):
                    raise ValueError('original byte envelope identity mismatch')
            # A native payload may legitimately contain our reserved spelling.
            if '$evidence' in value:
                return reference(_json_bytes(value), 'json')
            return {name: encode(child, (*path, name)) for name, child in value.items()}
        if isinstance(value, list):
            if key == 'samples':
                ref = reference(_json_bytes(value), 'json')
                sample_counters[ref['$evidence']] = measurement_counters(value)
                return ref
            return [encode(child, (*path, index)) for index, child in enumerate(value)]
        if isinstance(value, str):
            binary = ((key in ('stdout_base64', 'stderr_base64') and execution_path(path[:-1]))
                      or (key == 'base64' and len(path) >= 3 and path[-3] in ('raw_files', 'image_artifacts')
                          and execution_path(path[:-3])))
            if binary:
                raw = base64.b64decode(value, validate=True)
                if base64.b64encode(raw).decode('ascii') != value:
                    raise ValueError('noncanonical runner-owned byte envelope')
                return reference(raw, 'base64')
            if key in ('stdout', 'stderr', 'junit.xml', 'collection.json', 'profile.json') or len(value) >= 4096:
                try:
                    return reference(value.encode('utf-8'), 'utf8')
                except UnicodeEncodeError:
                    return reference(_json_bytes(value), 'json')
        return value

    result = encode(document)
    result['evidence_encoding'] = {'schema': EVIDENCE_SCHEMA, 'blobs': blobs, 'sample_counters': sample_counters,
        'sizes': {'decoded_json_bytes': len(_json_bytes(document)),
                  'unique_original_bytes': sum(blob['bytes'] for blob in blobs.values()),
                  'compressed_bytes': sum(blob['compressed_bytes'] for blob in blobs.values()),
                  'wire_json_bytes': 0}}
    sizes = result['evidence_encoding']['sizes']
    while sizes['wire_json_bytes'] != len(_json_bytes(result)):
        sizes['wire_json_bytes'] = len(_json_bytes(result))
    return result


def decode_evidence(document: dict) -> dict:
    """Verify every embedded stream/ref, then restore the pre-encoding JSON API."""
    metadata = document.get('evidence_encoding')
    if not isinstance(metadata, dict) or metadata.get('schema') != EVIDENCE_SCHEMA:
        raise ValueError('unsupported/missing raw evidence encoding')
    blobs, raw_by_sha = metadata.get('blobs'), {}
    if not isinstance(blobs, dict):
        raise ValueError('invalid evidence blob table')
    try:
        for sha, blob in blobs.items():
            if (not re.fullmatch('[0-9a-f]{64}', sha) or blob.get('sha256') != sha
                    or blob.get('encoding') != 'zlib+base64'
                    or type(blob.get('bytes')) is not int or blob['bytes'] < 0
                    or type(blob.get('compressed_bytes')) is not int or blob['compressed_bytes'] < 0
                    or not isinstance(blob.get('chunks'), list) or not blob['chunks']):
                raise ValueError('invalid evidence blob declaration')
            chunks = [base64.b64decode(chunk, validate=True) for chunk in blob['chunks']]
            if any(not chunk or len(chunk) > EVIDENCE_CHUNK_BYTES for chunk in chunks):
                raise ValueError('invalid evidence chunk length')
            compressed = b''.join(chunks)
            if len(compressed) != blob['compressed_bytes']:
                raise ValueError('compressed evidence length mismatch')
            decoder = zlib.decompressobj()
            # One extra byte detects a dishonest length without expanding the
            # rest of a bomb. Never call unbounded flush/decompress on this data.
            raw = decoder.decompress(compressed, blob['bytes'] + 1)
            if (len(raw) != blob['bytes'] or not decoder.eof or decoder.unused_data
                    or decoder.unconsumed_tail or hashlib.sha256(raw).hexdigest() != sha):
                raise ValueError('evidence length/digest/stream mismatch')
            raw_by_sha[sha] = raw

        def decode(value):
            if isinstance(value, dict):
                if '$evidence' in value:
                    if set(value) != {'$evidence', 'view'} or value['$evidence'] not in raw_by_sha:
                        raise ValueError('invalid/unresolved evidence reference')
                    raw = raw_by_sha[value['$evidence']]
                    if value['view'] == 'base64':
                        return base64.b64encode(raw).decode('ascii')
                    if value['view'] == 'utf8':
                        return raw.decode('utf-8')
                    if value['view'] == 'json':
                        return json.loads(raw.decode('utf-8'))
                    raise ValueError('unknown evidence view')
                return {key: decode(child) for key, child in value.items()}
            if isinstance(value, list):
                return [decode(child) for child in value]
            return value
        result = decode({key: value for key, value in document.items() if key != 'evidence_encoding'})
        for sha, counters in metadata.get('sample_counters', {}).items():
            if sha not in raw_by_sha or _json_bytes(counters) != _json_bytes(measurement_counters(json.loads(raw_by_sha[sha]))):
                raise ValueError('measurement counter projection mismatch')
        sizes = metadata.get('sizes', {})
        if (sizes.get('decoded_json_bytes') != len(_json_bytes(result))
                or sizes.get('wire_json_bytes') != len(_json_bytes(document))
                or sizes.get('unique_original_bytes') != sum(len(raw) for raw in raw_by_sha.values())
                or sizes.get('compressed_bytes') != sum(blob['compressed_bytes'] for blob in blobs.values())):
            raise ValueError('evidence size accounting mismatch')
        return result
    except (KeyError, TypeError, AttributeError, UnicodeError, binascii.Error, zlib.error, OverflowError) as exc:
        raise ValueError(f'invalid raw evidence: {exc}') from exc


def raw_evidence_bytes(record: dict, name: str) -> bytes:
    """Exact process/file bytes from a decoded ExecutionLog record."""
    value = record['raw_files'][name]
    raw = base64.b64decode(value['base64'], validate=True)
    if len(raw) != value['bytes'] or hashlib.sha256(raw).hexdigest() != value['sha256']:
        raise ValueError('raw file identity mismatch')
    return raw


def parse_junit_xml(data: str | bytes) -> ET.Element:
    """Parse evidence without DTDs, declared entities, or external resources.

    Reject at parser events, before the DTD's internal subset is processed;
    byte encodings such as UTF-16 cannot bypass a text-prefix check. Expat feeds
    only ordinary element/text events to TreeBuilder, preserving the runner's
    standard-library-only bootstrap and ElementTree's expanded namespace names.
    """
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator='}')

    def forbidden(*_args):
        raise ET.ParseError('DTD and entity declarations are forbidden in JUnit evidence')

    def expanded(name):
        return '{' + name if '}' in name else name

    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    parser.StartElementHandler = lambda name, attrs: builder.start(
        expanded(name), {expanded(key): value for key, value in attrs.items()})
    parser.EndElementHandler = lambda name: builder.end(expanded(name))
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ET.ParseError(str(exc)) from exc
    return builder.close()


def shard_for(nodeid: str, count: int) -> int:
    """Stable, one-based partition; every collected test has exactly one owner."""
    if count < 1:
        raise ValueError("shard count must be positive")
    key = nodeid.replace("\\", "/").encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big") % count + 1


def suite_for(nodeid: str, *, capacity: bool = False) -> str:
    """One owner for every test; adding an ordinary test keeps it in core."""
    path, _, name = nodeid.replace("\\", "/").partition("::")
    filename = path.rsplit("/", 1)[-1]
    if filename == "test_dashboard_browser.py":
        return "browser"
    if capacity:
        return "capacity"
    if (filename in ("test_context.py", "test_context_delivery.py", "test_workspace_binding.py",
                     "test_blueprint_harness.py", "test_workflow_builder_live.py")
            or (filename == "test_workflow_delivery.py" and name.startswith("test_original_source_relay"))
            or (filename == "test_workflow_local.py"
                and name.startswith("test_nested_scope_matches_native_inventory"))
            or filename in ("test_farm_telemetry.py", "test_farm_workers.py", "test_unbound_worker_recovery.py")
            or (filename == "test_review_remediation.py" and name.startswith((
                "test_required_binding_diagnostics_and_structured_write_boundary",
                "test_windows_interpreter_search_respects_current_directory_policy",
                "test_valid_interpreter_identity_uses_execution_cwd")))
            or (filename == "test_native_worker_dispatch.py"
                and name.partition("[")[0] == "test_claude_start_stop_contract_and_child_control_refusal")
            or (filename == "test_workflow_local.py"
                and name.startswith("test_extracted_packages_recover_displaced_state_through_declared_hooks["))):
        return "portability"
    if filename == "test_native_workflow_cli.py":
        return "native"
    if filename == "test_workflow_packages.py":
        return "packages"
    return "core"


# This module imports only the standard library. No candidate code is imported in
# the parent, and children receive isolation before Python imports any test/plugin.
SAFE_ENV = ('PATH', 'LANG', 'LC_ALL', 'TZ', 'PYTHONHASHSEED', 'NODE_ENV', 'CI',
            'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE')
BASELINE = '4a46e645ec628dc439cd9d163dbd63ee4906c3e6'


def isolated_environment(directory: Path, inherited=None) -> dict[str, str]:
    """Construct a child-only environment; never change the caller's environment."""
    inherited = os.environ if inherited is None else inherited
    env = {key: inherited[key] for key in SAFE_ENV if key in inherited}
    paths = {'HOME': 'home', 'USERPROFILE': 'home', 'CODEX_HOME': 'codex',
             'CLAUDE_CONFIG_DIR': 'claude', 'XDG_CONFIG_HOME': 'config',
             'XDG_CACHE_HOME': 'cache', 'XDG_DATA_HOME': 'data',
             'XDG_STATE_HOME': 'state', 'APPDATA': 'appdata',
             'LOCALAPPDATA': 'localappdata', 'TMPDIR': 'tmp', 'TEMP': 'tmp',
             'TMP': 'tmp', 'RUFF_CACHE_DIR': 'ruff', 'MYPY_CACHE_DIR': 'mypy',
             'PYTHONPYCACHEPREFIX': 'pycache'}
    for key, name in paths.items():
        target = directory / name
        target.mkdir(parents=True, exist_ok=True)
        env[key] = str(target.resolve())
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    if inherited.get('TASKPLANE_BROWSER_EXECUTABLE'):
        env['TASKPLANE_BROWSER_EXECUTABLE'] = inherited['TASKPLANE_BROWSER_EXECUTABLE']
    return env


def tree_map(root: Path) -> dict:
    """No following symlinks, including directories; retain modes and file census."""
    result = {}
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(parent) / name
            info = path.lstat()
            key = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode):
                result[key] = {'kind': 'symlink', 'target': os.readlink(path)}
            elif stat.S_ISREG(info.st_mode):
                data = path.read_bytes()
                result[key] = {'kind': 'file', 'sha256': hashlib.sha256(data).hexdigest(),
                               'bytes': len(data), 'mode': format(stat.S_IMODE(info.st_mode), '04o')}
            elif not stat.S_ISDIR(info.st_mode):
                result[key] = {'kind': 'special', 'mode': info.st_mode}
    return dict(sorted(result.items()))


# Ordinary commands verify relevant candidate inputs, not repository databases,
# generated evidence, historical copies or unrelated workspace documents.
CANDIDATE_DIRECTORIES = frozenset(('taskplane', 'scripts', 'hooks', 'skills', 'agents', 'lenses',
    'workflows', 'docs', 'assets', '.codex-plugin', '.claude-plugin', '.github'))
GENERATED_DIRECTORIES = frozenset(('.git', '.taskplane', 'dist', 'outputs', 'output', 'history',
    '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'node_modules', '.venv', 'venv'))
CANDIDATE_ROOT_FILES = frozenset(('LICENSE', 'PRIVACY.md', 'TERMS.md', 'README.md', 'CHANGELOG.md'))
CANDIDATE_SUFFIXES = frozenset(('.py', '.json', '.toml', '.ini', '.cfg', '.yaml', '.yml', '.txt', '.lock'))


def candidate_source_map(root: Path, source_inputs: list[str] | None = None) -> dict:
    """Fingerprint declared candidate inputs; this is not a whole-workspace audit."""
    tracked_docs = None
    documentation_unknown = False
    if source_inputs is None and (root / '.git').exists():
        environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        environment['GIT_OPTIONAL_LOCKS'] = '0'
        observed = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--', 'docs'],
                                  capture_output=True, env=environment, timeout=5, check=False)
        documentation_unknown = observed.returncode != 0
        tracked_docs = {name.decode('utf-8', errors='surrogateescape')
                        for name in observed.stdout.split(b'\0') if name} if not documentation_unknown else set()
    def selected():
        if source_inputs is not None:
            for relative in source_inputs:
                path = safe_path(root, relative)
                if any(part in GENERATED_DIRECTORIES for part in Path(relative).parts):
                    raise ValueError('Generated/control state is not a candidate source input')
                yield path
            return
        for parent, dirs, files in os.walk(root, followlinks=False):
            at_root = Path(parent) == root
            admitted = [name for name in dirs if name not in GENERATED_DIRECTORIES
                        and (not at_root or name in CANDIDATE_DIRECTORIES)]
            dirs[:] = []
            for name in admitted:
                path = Path(parent) / name
                if path.is_symlink():
                    yield path
                else:
                    dirs.append(name)
            for name in files:
                path = Path(parent) / name
                relative = path.relative_to(root).as_posix()
                if tracked_docs is not None and relative.startswith('docs/') and relative not in tracked_docs:
                    continue
                if (not at_root or name in CANDIDATE_ROOT_FILES or path.suffix in CANDIDATE_SUFFIXES):
                    yield path
    result = {}
    if documentation_unknown and (root / 'docs').exists():
        result['docs'] = {'kind': 'unknown', 'reason': 'Tracked candidate documentation could not be selected.'}
    for path in selected():
        name = path.relative_to(root).as_posix()
        try:
            info = path.lstat()
        except FileNotFoundError:
            result[name] = {'kind': 'missing'}
            continue
        if stat.S_ISLNK(info.st_mode):
            result[name] = {'kind': 'symlink', 'target': os.readlink(path)}
        elif stat.S_ISREG(info.st_mode):
            raw = path.read_bytes()
            result[name] = {'kind': 'file', 'sha256': hashlib.sha256(raw).hexdigest(),
                            'bytes': len(raw), 'mode': format(stat.S_IMODE(info.st_mode), '04o')}
        else:
            result[name] = {'kind': 'special', 'mode': info.st_mode}
    return dict(sorted(result.items()))


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


# Written exclusively in the disposable output directory. The profile retains
# individual setup/call/teardown outcomes; JUnit's aggregate time alone cannot
# distinguish a slow fixture from collection or interpreter startup.
# Reviewed exact functions and predicates from their current decorators/helpers.
# New functions do not inherit optionality, even in modules with pytestmark.
# Human-readable skip explanations are retained in evidence, not rule identity.
OPTIONAL_SKIP_RULES = [('test_claude_fork_input_transform.py',
  'no_binding',
  {},
  ('test_exact_fork_flag_omission_keeps_original_and_admitted_digests',
   'test_conversion_never_hides_other_argument_or_native_framing_changes',
   'test_pretool_matching_uses_same_exact_conversion',
   'test_notification_delivery_run_id_is_metadata_not_authority')),
 ('test_claude_guarded_handback.py',
  'no_binding',
  {},
  ('test_guarded_report_joins_without_changing_original_or_delivered_evidence',
   'test_notice_cannot_override_original_integrity_or_native_proof',
   'test_existing_cursor_recovery_preserves_fifteen_accepted_results',
   'test_reconciliation_is_scoped_to_current_root_and_original_grant',
   'test_reconcile_cli_uses_original_attempt_without_automatic_acceptance',
   'test_reconciliation_revalidates_cached_transform_against_original_span',
   'test_literal_notice_in_original_report_never_authorizes_removal',
   'test_package_switch_refuses_mutation_and_preserves_original_native_proof',
   'test_exact_notice_without_observed_native_trigger_stays_blocked')),
 ('test_claude_interactive_recovery.py',
  'no_binding',
  {},
  ('test_startup_failure_handback_is_bounded_failure_and_fresh_retry_works',
   'test_startup_failure_admission_retains_identity_schema_and_binding_boundaries',
   'test_stop_history_and_failure_handback_attempts_have_bounds',
   'test_native_peer_handback_textless_stop_and_redirect_complete_exact_attempt',
   'test_delayed_parent_delivery_joins_accepts_and_replays_exact_attempt',
   'test_transcript_only_redirect_joins_exact_handback_and_accepts_once',
   'test_delayed_delivery_preserves_each_proof_and_chronology_boundary',
   'test_complete_rejected_redirect_has_diagnostic_without_success',
   'test_claude_discovery_and_owned_current_worker_controls',
   'test_claude_discovery_is_bounded_exact_selection',
   'test_claude_inventory_discovery_is_exact_selection',
   'test_claude_controls_keep_owned_current_attempt_and_automatic_proof',
   'test_claude_message_schema_rejects_broadcast_structured_or_conflicting_input',
   'test_claude_message_observable_backfill_is_display_only',
   'test_claude_discovery_cannot_grant_child_or_foreign_target_control',
   'test_claude_stop_request_never_joins_or_allows_new_input',
   'test_claude_listagents_empty_native_input_is_readonly_not_completion',
   'test_claude_listagents_requires_exact_automatic_root_provenance',
   'test_peer_redirect_requires_every_native_proof_part',
   'test_mid_turn_peer_attachment_requires_exact_native_envelope',
   'test_mid_turn_peer_attachment_rejects_malformed_report_and_rendering',
   'test_mid_turn_delivery_preserves_bound_handback_and_completion_requirements',
   'test_handback_acknowledgment_keeps_exact_automatic_admission',
   'test_retained_peer_delivery_is_revalidated_before_acceptance',
   'test_peer_completion_waits_for_bounded_parent_scan_and_retains_full_proof',
   'test_same_body_multiple_acknowledged_calls_cannot_claim_one_peer',
   'test_no_report_terminal_requires_exact_native_envelopes',
   'test_legacy_preclaim_stop_conflict_only_recovers_with_fresh_exact_no_report')),
 ('test_claude_oversized_history.py',
  'no_binding',
  {},
  ('test_real_sized_plain_and_companion_history_preserve_exact_launch_proof',
   'test_partial_large_record_is_recoverable_budget_limit_without_cursor_poison',
   'test_oversized_possible_evidence_or_ambiguous_record_is_never_skipped',
   'test_literal_tool_shapes_inside_human_text_are_data_not_native_evidence',
   'test_oversized_relevant_native_call_remains_unavailable',
   'test_existing_sealed_conflict_is_preserved_not_silently_reinterpreted')),
 ('test_claude_stop_before_handback.py',
  'no_binding',
  {},
  ('test_pre_handback_stop_reconciles_original_call_and_preserves_other_work',
   'test_pre_handback_sequence_does_not_relax_identity_or_delivery',
   'test_same_launch_competing_no_report_remains_conflict')),
 ('test_claude_worker_lifecycle.py',
  'no_binding',
  {},
  ('test_root_source_uses_original_project_with_worktree_cwd',
   'test_worker_source_honors_exact_config_root_without_path_relaxation',
   'test_preflight_retains_bounded_no_follow_native_proof',
   'test_unpinned_worker_cannot_select_or_override_source',
   'test_source_selection_refuses_untrusted_paths_and_lineage',
   'test_selection_never_follows_unsafe_native_paths',
   'test_launch_older_than_four_mib_survives_restart_and_append',
   'test_delayed_result_and_partial_header_resume_exact_newline',
   'test_competing_record_after_budget_boundary_prevents_premature_match',
   'test_retained_evidence_changes_are_sticky',
   'test_missing_original_source_retains_proof_but_cannot_refresh_match',
   'test_identical_duplicates_are_idempotent_and_appended_conflict_is_sticky',
   'test_record_limit_resumes_and_oversized_record_refuses',
   'test_preparation_watermark_refuses_preexisting_or_replayed_launch',
   'test_launch_appended_after_boundary_still_requires_post_preparation_timestamp',
   'test_shared_budget_limits_all_attempts_and_source_selection',
   'test_many_candidates_share_scan_and_keep_separate_evidence',
   'test_invalid_source_checksum_and_no_follow_capability_refuse',
   'test_malformed_serialized_cursor_refuses_without_crashing',
   'test_child_cannot_be_read_from_a_replaced_project_during_scan',
   'test_changed_source_reference_cannot_expand_parent_reads',
   'test_repeated_root_selection_preserves_descriptor_and_refreshes_admission_watermark',
   'test_pending_candidates_rotate_when_revalidation_budget_is_shared',
   'test_incremental_parser_preserves_exact_correlation_refusals',
   'test_learning_exact_worker_identity_does_not_discard_cursor_or_proof',
   'test_transcript_only_notification_requires_exact_native_provenance',
   'test_transcript_notification_cursor_upgrade_rescans_once_and_revalidates',
   'test_direct_notification_preserves_literal_and_escaped_report_text',
   'test_midturn_attachment_requires_native_delivery_provenance',
   'test_native_notification_requires_exact_provenance_and_launch',
   'test_old_launch_cursor_migrates_only_revalidated_spans',
   'test_real_shaped_launch_denial_regression',
   'test_denial_requires_exact_native_shape_and_empty_attempt',
   'test_denial_supported_variants_and_revalidation',
   'test_denial_rejects_competing_records_and_text_only_claims',
   'test_legacy_denial_requires_explicit_reverified_migration',
   'test_legacy_denial_recovery_cannot_clear_other_or_forged_evidence',
   'test_old_nonconflicting_projection_adds_only_reverified_denial_fields',
   'test_denial_source_must_stay_pinned_and_readable',
   'test_denial_waits_for_complete_watermark_and_finds_late_competitor',
   'test_legacy_denial_bounded_progress_is_private_and_resumes',
   'test_denial_partial_line_and_budget_exhaustion_preserve_unknown',
   'test_denial_admission_offsets_and_late_completion_remain_blocked',
   'test_pure_denial_explicit_lifecycle_and_observation_time_refuse',
   'test_explicit_delivery_cycles_keep_sequential_reports_distinct',
   'test_explicit_delivery_cycles_refuse_ambiguous_or_cross_cycle_evidence',
   'test_delivery_cycle_orders_validate_explicit_identity_not_timestamp_proximity')),
 ('test_farm_telemetry.py',
  'no_binding',
  {},
  ('test_hundreds_of_transients_obey_store_wide_pair_and_byte_bounds',
   'test_pinned_selected_legacy_and_leased_pairs_survive',
   'test_context_reference_becomes_a_permanent_pin',
   'test_partial_deletion_intent_recovers_only_owned_garbage')),
 ('test_farm_workers.py',
  'no_binding',
  {},
  ('test_bounded_reader_ignores_output_path_and_rejects_unsafe_files',
   'test_callless_stop_before_result_reconciles_exact_attempt',
   'test_controller_concurrent_identity_claim_context_and_replay',
   'test_controller_invocation_scope_refusals',
   'test_cli_locator_precedes_inherited_identity_and_missing_reference_refuses',
   'test_invocation_concurrent_double_consume_and_foreign_reference',
   'test_invocation_post_requires_exact_recorded_input_and_runtime',
   'test_genuine_root_context_gets_distinct_one_use_transport',
   'test_handback_requires_actual_claim_and_observed_terminal',
   'test_pending_reference_revalidates_current_attempt_and_source',
   'test_direct_worker_context_requires_explicit_claim')),
 ('test_host_native.py',
  'no_boot',
  {},
  ('test_real_process_start_identity_is_stable_and_not_ownership',)),
 ('test_host_capabilities.py',
  'no_recovery',
  {},
  ('test_complete_parent_and_child_over_64mib_use_bounded_continuations',
   'test_recovery_continuation_refuses_changed_snapshot',
   'test_recovery_retry_has_deterministic_progress_and_detects_late_conflict')),
 ('test_native_worker_dispatch.py',
  'no_recovery',
  {},
  ('test_partial_recovery_persists_cursor_without_releasing_reservation',)),
 ('test_native_worker_dispatch.py',
  'no_binding',
  {},
  ('test_claude_start_stop_contract_and_child_control_refusal',
   'test_claude_launch_requires_source_pinned_before_preparation')),
 ('test_claude_dispatch_order.py',
  'no_binding',
  {},
  ('test_launch_reservation_survives_either_flush_order_without_granting_child_access',
   'test_deferred_native_conflict_never_binds_or_clears_pending_attempt',
   'test_missing_transcript_call_requires_current_automatic_root_provenance',
   'test_caller_source_hint_cannot_survive_failed_automatic_selection')),
 ('test_cross_host_compatibility.py',
  'no_binding',
  {},
  ('test_claude_backfilled_message_retains_owned_attempt_and_does_not_join',
   'test_claude_unjoinable_launch_refuses_before_admission_and_can_abandon',
   'test_claude_preflight_rejected_call_can_be_corrected_without_new_grant',
   'test_claude_child_message_reports_supported_handback_without_delivery')),
 ('test_worker_runtime.py',
  'no_binding',
  {},
  ('test_denial_runtime_regression',
   'test_denial_lifecycle_ingestion_survives_cache_capacity',
   'test_denial_saturated_ingestion_requires_exact_automatic_lifecycle',
   'test_exact_automatic_lifecycle_preserves_successful_claude_launch',
   'test_denial_is_idempotent_unsatisfied_and_requires_fresh_grant_reason',
   'test_revoked_denial_refresh_never_uses_stale_quiescence',
   'test_new_denial_refuses_existing_child_or_work',
   'test_denial_transition_requires_observation_to_match_sealed_proof')),
 ('test_workflow_host.py',
  'no_binding',
  {},
  ('test_denial_controller_recovery_regression',
   'test_denial_lifecycle_ingestion_blocks_saturated_legacy_recovery',
   'test_denial_recovery_refusals_preserve_original_attempt',
   'test_denial_recovery_cli_preserves_before_history_and_revalidates_replay',
   'test_denial_recovery_partial_scan_stays_private_and_resumes',
   'test_denial_mutable_boundaries_refresh_and_persist_before_refusal',
   'test_denial_direct_resume_and_adapter_checks_are_read_only',
   'test_denial_does_not_veto_bound_negative_human_decision')),
 ('test_worker_runtime.py',
  'no_binding',
  {},
  ('test_bound_workers_require_independent_current_location_and_matching_contract',
   'test_bound_host_alias_write_preserves_exact_scope_and_symlink_checks')),
 ('test_controller_storage.py',
  'no_binding',
  {},
  ('test_public_recovery_requires_receipt_and_preserves_original_provenance',)),
 ('test_initialization_readiness.py',
  'no_binding',
  {},
  ('test_restoration_completion_preserves_controller_without_accepting_phases',)),
 ('test_initialization_recovery_receipt.py',
  'no_binding',
  {},
  ('test_exact_restoration_and_completed_retry_preserve_original_bytes',
   'test_completed_retry_retains_original_request_provenance',
   'test_matching_target_cannot_manufacture_recovery_provenance',
   'test_interrupted_initial_restore_finishes_same_receipt',
   'test_prepared_retry_rejects_changed_scope_without_writes',
   'test_restored_receipt_with_missing_target_is_not_replayed',
   'test_unsupported_directory_sync_never_acknowledges_restore')),
 ('test_native_approval_capture.py',
  'no_binding',
  {},
  ('test_ordinary_chat_approval_derives_real_ids_and_preserves_original_presentation',
   'test_prompt_flush_is_deferred_then_captured_before_next_tool_guard',
   'test_capture_rejects_nonhuman_stale_or_ambiguous_decisions',
   'test_semantic_candidate_uses_structured_checkpoint_binding',
   'test_unavailable_host_and_child_never_read_arbitrary_transcripts',
   'test_native_negative_decision_preserves_human_authority_despite_artifact_drift',
   'test_large_old_history_does_not_block_recent_approval_or_enter_proof',
   'test_recent_unreadable_evidence_never_falls_back_to_an_older_approval',
   'test_reverse_reader_preserves_cross_chunk_records_and_stops_before_old_history',
   'test_approval_capacity_exhaustion_is_explicit_and_does_not_poison_source',
   'test_native_capture_needs_orchestrator_semantic_candidate',
   'test_bound_presentation_survives_intermediate_commentary_without_prose_rules',
   'test_unbound_presentation_never_substitutes_nearest_assistant_text',
   'test_verifier_rereads_exact_bound_presentation_source',
   'test_later_explicit_presentation_target_cannot_reuse_older_current_question',
   'test_flow_decide_accepts_native_envelope_without_dashboard_or_transcript_metadata',
   'test_explicit_native_envelope_verifies_own_pin_without_harness_presentation',
   'test_bind_message_rejects_wrong_or_unavailable_native_sources')),
 ('test_native_workflow_cli.py',
  'claude_transport',
  {},
  ('test_unsupported_claude_context_refuses_through_declared_hooks',)),
 ('test_review_remediation.py',
  'not_windows',
  {},
  ('test_windows_interpreter_search_respects_current_directory_policy',)),
 ('test_unbound_worker_recovery.py',
  'no_recovery',
  {},
  ('test_historical_native_observation_does_not_invent_admission',
   'test_unbound_observation_refuses_ambiguous_or_malformed_proof',
   'test_recovery_releases_only_failed_reservation_and_allows_fresh_attempt',
   'test_recovery_with_unrelated_file_preserves_history_and_allows_fresh_attempt')),
 ('test_workflow_continuation.py',
  'no_binding',
  {},
  ('test_native_resume_retains_owner_run_approval_and_exact_request',
   'test_resume_refuses_without_changing_store',
   'test_resume_cli_inspection_and_verification_are_read_only',
   'test_foreign_report_explains_supported_next_step',
   'test_explicit_foreign_report_does_not_select_requesters_own_run',
   'test_resume_command_is_admitted_at_sealed_checkpoint')),
 ('test_workflow_delivery.py',
  'no_binding',
  {},
  ('test_original_source_relay_preserves_origin_and_exact_replay',
   'test_original_source_relay_exact_launch_forms',
   'test_original_source_relay_accepts_equivalent_single_execution_wrappers',
   'test_original_source_relay_rejects_ambiguous_or_computed_execution',
   'test_original_source_relay_replay_pins_original_frames')),
 ('test_workflow_local.py',
  'not_posix',
  {},
  ('test_control_state_remains_private_under_permissive_umask',)),
 ('test_workflow_recovery.py',
  'no_relative_nofollow',
  {},
  ('test_interrupted_restore_retirement_and_relocation_preserve_authentic_history',)),
 ('test_workspace_binding.py',
  'binding',
  {'capability': 'native'},
  ('test_unsupported_binding_cli_is_structured_and_preserves_files',)),
 ('test_workspace_binding.py',
  'no_binding',
  {},
  ('test_bind_is_idempotent_and_frozen_contract_is_checked',
   'test_malformed_evidence_refuses_without_state',
   'test_probe_reads_are_bounded_ordinary_no_symlink',
   'test_store_and_workspace_symlinks_are_rejected',
   'test_required_execution_observation_not_inferred_from_binding',
   'test_darwin_local_checks_actual_platform',
   'test_worker_location_requires_independent_runtime_observation',
   'test_any_policy_allows_explicit_remote_but_refuses_conflict',
   'test_policy_environment_cannot_weaken_binding',
   'test_split_cwd_aliases_one_root_and_no_scratch_store',
   'test_relative_targets_refuse_escape',
   'test_alias_neighbor_symlink_and_nested_alias_refused',
   'test_probe_drift_and_binding_tamper_refuse',
   'test_active_legacy_store_cannot_be_bound',
   'test_inactive_relocation_archives_exact_bytes_no_approval_transfer',
   'test_restored_retired_controller_relocates_exact_history_without_approvals',
   'test_invalid_restoration_history_refuses_without_mutation',
   'test_active_or_corrupt_recovery_is_read_only',
   'test_recovery_requires_correct_identity_fresh_probe_and_provenance',
   'test_copied_identity_refuses_even_with_fresh_claim',
   'test_same_path_copy_detected_by_directory_identity',
   'test_recovery_rejects_special_history_and_incomplete_marker',
   'test_interrupted_recovery_remains_fail_closed_and_original_bytes_survive',
   'test_cli_request_and_readonly_inspection',
   'test_digest_matches_canonical_record',
   'test_recovery_preserves_and_validates_archived_context_history',
   'test_corrupt_terminal_fields_do_not_authorize_recovery',
   'test_recovery_inventory_has_hard_bounds',
   'test_prompt_reaches_model_with_setup_guidance',
   'test_report_missing_binding_is_actionable_and_readonly',
   'test_selected_unbound_setup_reads_reachable',
   'test_setup_denies_structured_writes_or_unbound_children',
   'test_invalid_binding_is_not_first_run_onboarding',
   'test_frozen_missing_binding_is_not_onboarding',
   'test_inline_request_rejects_invalid_data_without_state',
   'test_selected_folder_inline_bind_then_standalone_engineering',
   'test_unrelated_cowork_prompt_does_not_select_taskplane',
   'test_discovered_child_cannot_use_onboarding_exemptions',
   'test_lost_binding_history_is_not_first_run_through_entry_points',
   'test_missing_binding_discovery_is_bounded_and_readonly',
   'test_existing_unbound_local_history_retains_legacy_resolution')),
 ('test_workspace_binding.py',
  'windows',
  {},
  ('test_posix_targets_keep_backslash_refusal',)),
 ('test_workspace_binding.py',
  'no_binding',
  {'bound': True},
  ('test_native_targets_refuse_symlink_components',)),
 ('test_claude_worker_invocation.py',
  'no_claude_transport',
  {},
  ('test_bad_quoting_and_oversize_refuse',
   'test_claim_reorders_and_quotes_only_literals',
   'test_cli_does_not_trust_inherited_identity',
   'test_cli_requires_exact_final_reference_and_canonical_argv',
   'test_command_dataclass_is_not_an_authority_constructor',
   'test_command_identity_and_option_confusion_refuse',
   'test_concurrent_consumers_under_controller_lock_admit_exactly_one',
   'test_context_requires_claimed_exact_worker_task_and_root_is_separate',
   'test_context_selectors_refuse_ambiguous_or_invalid_arguments',
   'test_corrupt_record_refuses_even_if_unkeyed_checksum_recomputed',
   'test_dispatch_failure_or_crash_burns_saved_reference',
   'test_duplicate_pre_hook_is_idempotent_only_while_identical_and_pending',
   'test_either_clock_expiry_or_regression_burns_validity',
   'test_every_current_identity_dimension_is_revalidated',
   'test_expiry_during_durable_save_burns_without_dispatch',
   'test_grant_only_selects_already_observed_actor',
   'test_issuance_requires_complete_guarded_automatic_binding',
   'test_issue_roundtrip_preserves_input_and_binds_both_digests',
   'test_malformed_record_values_refuse_without_type_errors',
   'test_metacharacters_refuse_even_inside_quotes',
   'test_persistence_precedes_dispatch_and_replay_always_refuses',
   'test_post_input_cannot_restore_missing_evicted_or_consumed_record',
   'test_reboot_runtime_drift_and_unavailable_clock_refuse',
   'test_runtime_launcher_drift_refuses_even_if_other_binding_matches',
   'test_shell_syntax_and_user_supplied_reference_refuse',
   'test_supported_context_grammar_reorders_flags',
   'test_two_actors_same_context_command_reverse_consumption_stay_separate',
   'test_unsafe_bash_input_refuses',
   'test_write_failure_does_not_dispatch_or_restore_in_memory_authority')),
 ('test_claude_flow.py',
  'no_binding',
  {},
  ('test_bound_checkpoint_controls_use_selected_workspace',
   'test_context_refusal_uses_resolved_workspace_in_retry',
   'test_cowork_cli_and_hooks_share_selected_root_without_scratch_store',
   'test_selected_workspace_export_is_validated_before_claude_environment_write')),
 ('test_claude_worker_invocation.py',
  'no_boot',
  {},
  ('test_real_os_boot_adapter_is_stable_or_refuses_when_os_denies_access',)),
 ('test_native_workflow_cli.py',
  'no_claude_transport',
  {'host': 'claude'},
  ('test_native_correction_resubmission',
   'test_native_state_repairs_through_production_interfaces',
   'test_native_repeated_repairs_preserve_checkpoint_history',
   'test_native_repeated_repair_history_fits_without_losing_evidence',
   'test_both_native_routing_contexts_complete_seven_explicit_gates')),
 ('test_workflow_packages.py',
  'no_claude_transport',
  {'host': 'claude'},
  ('test_generated_archives_match_verified_source',)),
 ('test_workflow_autonomy.py',
  'no_claude_transport',
  {'host': 'claude'},
  ('test_seven_phase_autonomous_native_journey',)),

 ('test_security_boundaries.py',
  'symlink_error',
  {},
  ('test_package_refuses_symlink_directory_before_read',)),
 ('test_em_remediation.py',
  'symlink_error',
  {},
  ('test_flow_never_writes_through_runtime_symlinks',)),
 ('test_native_session_meter.py',
  'long_filename',
  {},
  ('test_incremental_long_directory_entries_resume_without_false_eof',))]

# These transports alone enter consent_transport_case's capability-gated
# branches. Other transports of the same parametrized test remain required.
for _transport in ('observed-v2', 'native-capture'):
    for _parameter, _names in (
        ('consent_transport', (
            'test_sec01_transport_stale_or_foreign_binding_is_atomic',
            'test_sec01_transport_proof_precedes_question_validation',
            'test_sec01_exact_original_request_replay_is_idempotent',
            'test_sec01_consumed_event_cannot_approve_a_later_checkpoint',
            'test_sec01_conflicting_replay_is_refused_without_authority_mutation')),
        ('transport', ('test_sec02_caller_text_cannot_replace_pinned_original_question',)),
        ('consent_text_transport', ('test_sec01_current_phase_approval_can_continue_to_next_phase',)),
    ):
        OPTIONAL_SKIP_RULES.append(('test_delegated_checkpoint.py', 'no_binding',
            {_parameter: _transport}, _names))

OPTIONAL_SKIP_NODES = {}
for _module, _predicate, _params, _names in OPTIONAL_SKIP_RULES:
    for _name in _names:
        OPTIONAL_SKIP_NODES.setdefault(f"taskplane/tests/{_module}::{_name}", []).append(
            {"predicate": _predicate, "parameters": _params})


PROFILE_PLUGIN = '''import json, os, sys, time
import pytest
from pathlib import Path
try:
    from _pytest.subtests import SubtestReport
except ImportError:
    SubtestReport = ()
START = time.perf_counter()
DATA = {"reports": [], "collection_errors": [], "node_evidence": {},
        "platform_capabilities": {"os_name": os.name, "sys_platform": sys.platform,
            "open_dir_fd": os.open in os.supports_dir_fd,
            **{name: hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "mkfifo")}}}
def pytest_sessionstart(session):
    DATA["session_start_seconds"] = time.perf_counter() - START
def pytest_collection_finish(session):
    DATA["collection_end_seconds"] = time.perf_counter() - START
    DATA["selected_nodeids"] = [item.nodeid for item in session.items]
    collection_path = session.config.getoption("--taskplane-collection-report")
    if collection_path:
        original = json.loads(Path(collection_path).read_bytes())
        selected = set(DATA["selected_nodeids"])
        DATA["collected_inventory"] = [dict(row, selected=row["nodeid"] in selected,
            selection_before_pytest_filters=row["selected"]) for row in original]
    for item in session.items:
        params = getattr(getattr(item, "callspec", None), "params", {})
        DATA["node_evidence"][item.nodeid] = {
            "parameters": {key: value for key, value in params.items() if type(value) in (str, int, bool, type(None))},
            "skipif": [{"reason": marker.kwargs.get("reason"),
                        "conditions": [value if type(value) is bool else repr(value) for value in marker.args]}
                       for marker in item.iter_markers("skipif")]}
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    report.ci_skip_evidence = {}
    if report.skipped:
        detail = report.longrepr
        reason = detail[2] if isinstance(detail, tuple) and len(detail) == 3 else str(detail)
        report.ci_skip_evidence["reason"] = reason.removeprefix("Skipped: ")
        report.ci_skip_evidence["location"] = list(detail[:2]) if isinstance(detail, tuple) else None
        error = call.excinfo.value.__context__ if call.excinfo else None
        if isinstance(error, OSError):
            report.ci_skip_evidence["os_error"] = {"errno": error.errno, "message": str(error),
                                                   "filename": error.filename}
        report.ci_skip_evidence["properties"] = list(item.user_properties)
def pytest_collectreport(report):
    if report.failed:
        DATA["collection_errors"].append(str(report.longrepr))
def pytest_runtest_logreport(report):
    DATA["reports"].append({"nodeid": report.nodeid, "when": report.when,
        "report_type": type(report).__module__ + "." + type(report).__qualname__,
        "subtest": report.context._to_json() if isinstance(report, SubtestReport) else None,
        "outcome": report.outcome, "duration": report.duration,
        "skip_evidence": getattr(report, "ci_skip_evidence", {}),
        "wasxfail": getattr(report, "wasxfail", None),
        "longrepr": str(report.longrepr) if report.longrepr else None,
        "captured": {name: text for name, text in report.sections if name.endswith(" " + report.when)}})
def pytest_sessionfinish(session, exitstatus):
    DATA["session_seconds"] = time.perf_counter() - START
    DATA["exitstatus"] = int(exitstatus)
    Path(os.environ["CI_EXECUTION_PROFILE"]).write_text(json.dumps(DATA), encoding="utf-8")
'''


class ExecutionLog:
    """One raw record per actual process; consumers reference its stable index."""
    def __init__(self, output: Path, root: Path = ROOT, inherited=None, *, live_output=False, source_inputs=None):
        self.output, self.root = output, root
        self.live_output = live_output
        self.source_inputs = source_inputs
        self.environment = isolated_environment(output / 'isolation', inherited)
        self.bound_external_sources: dict[str, Path] = {}
        self.records: list[dict] = []
        plugins = output / 'plugins'
        plugins.mkdir(parents=True, exist_ok=True)
        (plugins / 'ci_execution_profile.py').write_text(PROFILE_PLUGIN, encoding='utf-8')
        self.environment['PYTHONPATH'] = os.pathsep.join((str(plugins), str(root), str(root / 'taskplane')))
        self.runtime = {'executable': sys.executable, 'resolved': str(Path(sys.executable).resolve()),
                        'version': sys.version, 'platform': platform.platform()}

    def run(self, argv: list[str], *, label: str, cwd: Path | None = None,
            pytest_run: bool = False, collect: bool = False, timeout: int = 3600) -> dict:
        index = len(self.records)
        destination = self.output / f'execution-{index}'
        destination.mkdir()
        command = list(argv)
        env = dict(self.environment)
        if cwd is not None and cwd != self.root:
            env['PYTHONPATH'] = os.pathsep.join((str(self.output / 'plugins'), str(cwd), str(cwd / 'taskplane')))
        if pytest_run:
            env['CI_EXECUTION_PROFILE'] = str(destination / 'profile.json')
            command += ['-p', 'ci_execution_profile', '-p', 'no:cacheprovider',
                        '--taskplane-collection-report', str(destination / 'collection.json')]
            if not collect and not any(arg.startswith('--junitxml') for arg in command):
                command += ['--junitxml', str(destination / 'junit.xml')]
        record = {'id': index, 'label': label, 'argv': command,
                  'cwd': str((cwd or self.root).resolve()), 'runtime': self.runtime,
                  'environment': env, 'timeout_seconds': timeout,
                  'stdout': '', 'stderr': '', 'raw_files': {}, 'exit_code': None, 'status': 'unknown'}
        source_before = candidate_source_map(self.root, self.source_inputs)
        record['source_fingerprint_before'] = digest(source_before)
        record['source_fingerprint_coverage'] = {'kind': 'declared-candidate' if source_before and all(row['kind'] == 'file' for row in source_before.values()) else 'unknown',
            'unavailable_inputs': [name for name, row in source_before.items() if row['kind'] != 'file'],
            'source_inputs': self.source_inputs, 'excluded_directories': sorted(GENERATED_DIRECTORIES),
            'documentation_selection': 'Tracked documentation in Git workspaces, or explicitly selected candidate inputs.',
            'assurance': 'Relevant candidate source only; not whole-workspace or host attestation.'}
        record['external_source_fingerprints_before'] = {name: digest(tree_map(path))
            for name, path in self.bound_external_sources.items()}
        (destination / 'command.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        start = time.perf_counter()
        buffers = {'stdout': [], 'stderr': []}
        transport_errors = []
        def drain(stream, name, console):
            try:
                with (destination / (name + '.raw')).open('wb') as spool:
                    while True:
                        block = stream.read1(65536)
                        if not block:
                            break
                        buffers[name].append(block)
                        spool.write(block)
                        spool.flush()
                        if self.live_output:
                            console.write(block.decode('utf-8', errors='replace'))
                            console.flush()
            except (OSError, ValueError) as exc:
                transport_errors.append(f'{name}: {exc}')
            finally:
                stream.close()
        try:
            process = subprocess.Popen(command, cwd=cwd or self.root, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            threads = [threading.Thread(target=drain, args=(stream, name, console), daemon=True)
                       for stream, name, console in ((process.stdout, 'stdout', sys.stdout),
                                                      (process.stderr, 'stderr', sys.stderr))]
            for thread in threads:
                thread.start()
            try:
                process.wait(timeout=timeout)
                record.update(exit_code=process.returncode, status='pass' if process.returncode == 0 else 'fail')
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                record.update(reason='timeout', process_tree_quiescence='unknown', status='unknown')
            for thread in threads:
                thread.join(timeout=5)
            if any(thread.is_alive() for thread in threads):
                record.update(status='unknown', reason='output transport remains open after process exit',
                              process_tree_quiescence='unknown')
        except OSError as exc:
            record['reason'] = f'{type(exc).__name__}: {exc}'
        def retain_bytes(name, raw):
            record['raw_files'][name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                                         'base64': base64.b64encode(raw).decode('ascii')}
        for name in ('stdout', 'stderr'):
            raw = b''.join(buffers[name])
            retain_bytes(name, raw)
            record[name] = raw.decode('utf-8', errors='replace')
            record[name + '_base64'] = base64.b64encode(raw).decode()
        if transport_errors:
            record.update(status='unknown', transport_errors=transport_errors)
        record['elapsed_seconds'] = time.perf_counter() - start
        for name in ('collection.json', 'profile.json', 'junit.xml'):
            path = destination / name
            # A user-specified JUnit path is still captured before temp cleanup.
            if name == 'junit.xml' and not path.exists():
                for offset, arg in enumerate(command):
                    if arg == '--junitxml' and offset + 1 < len(command):
                        path = Path(command[offset + 1])
                    elif arg.startswith('--junitxml='):
                        path = Path(arg.split('=', 1)[1])
            if path.is_file():
                raw = path.read_bytes()
                retain_bytes(name, raw)
                record[name] = raw.decode('utf-8', errors='replace')  # Display only, never parsing input.
                if name.endswith('.json'):
                    try:
                        record[name[:-5]] = json.loads(raw.decode('utf-8'))
                    except (ValueError, UnicodeError):
                        record.update(status='fail', reason=f'malformed {name}')
        if 'collected_inventory' in record.get('profile', {}):
            record['final_collection'] = record['profile']['collected_inventory']
            record['selection_provenance'] = {
                'original': 'collection / raw_files.collection.json (unaltered seed report)',
                'final': 'final_collection / profile.collected_inventory',
                'rule': 'Same complete original rows; selected iff present in final pytest session.items.'}
        if 'browser' in label.lower():
            record['image_artifacts'] = {}
            for image in Path(env['TMPDIR']).rglob('*'):
                if image.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp') and image.is_file() and not image.is_symlink():
                    raw_image = image.read_bytes()
                    record['image_artifacts'][str(image)] = {'sha256': hashlib.sha256(raw_image).hexdigest(),
                        'bytes': len(raw_image), 'base64': base64.b64encode(raw_image).decode()}
        if pytest_run:
            record['test_verdict'] = test_verdict(record, collect=collect)
            if record['test_verdict']['status'] != 'pass' and record['status'] != 'unknown':
                record['status'] = record['test_verdict']['status']
        record['fingerprint'] = digest({key: record[key] for key in ('argv', 'cwd', 'runtime', 'environment')})
        profile = record.get('profile', {})
        if profile:
            record['timing'] = {'process_wall_seconds': record['elapsed_seconds'],
                'session_seconds': profile.get('session_seconds'),
                'collection_seconds': profile.get('collection_end_seconds', 0) - profile.get('session_start_seconds', 0),
                'phase_seconds': {phase: sum(row['duration'] for row in profile.get('reports', []) if row['when'] == phase)
                                  for phase in ('setup', 'call', 'teardown')},
                'startup_limit': 'Process wall time includes startup; plugin timestamps start after Python/pytest imports.'}
        record['source_fingerprint_after'] = digest(candidate_source_map(self.root, self.source_inputs))
        record['external_source_fingerprints_after'] = {name: digest(tree_map(path))
            for name, path in self.bound_external_sources.items()}
        (destination / 'record.json').write_bytes(_json_bytes(pack_evidence(record)))
        self.records.append(record)
        return record


def optional_skip_evidence(profile: dict, report: dict) -> dict | None:
    """Classify one actual skip using reviewed node, parameter and capability facts."""
    node = report['nodeid']
    rules = OPTIONAL_SKIP_NODES.get(node.partition('[')[0], [])
    facts = profile.get('platform_capabilities', {})
    if (not isinstance(facts.get('os_name'), str) or not isinstance(facts.get('sys_platform'), str)
            or any(type(facts.get(key)) is not bool for key in
                   ('open_dir_fd', 'O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK', 'mkfifo'))):
        return None
    binding = facts['open_dir_fd'] and facts['O_DIRECTORY'] and facts['O_NOFOLLOW']
    transport = facts['os_name'] == 'posix' and facts['sys_platform'] in ('linux', 'darwin')
    predicates = {'binding': binding, 'no_binding': not binding,
        'no_relative_nofollow': not (facts['open_dir_fd'] and facts['O_NOFOLLOW']),
        'no_recovery': not (facts['O_NONBLOCK'] and facts['O_NOFOLLOW']),
        'no_boot': facts['sys_platform'] not in ('linux', 'darwin'),
        'not_windows': facts['os_name'] != 'nt', 'windows': facts['os_name'] == 'nt',
        'not_posix': facts['os_name'] != 'posix', 'claude_transport': transport,
        'no_claude_transport': not transport, 'no_fifo': not facts['mkfifo']}
    node_evidence = profile.get('node_evidence', {}).get(node, {})
    parameters = node_evidence.get('parameters', {})
    evidence = report.get('skip_evidence', {})
    reason, error = evidence.get('reason'), evidence.get('os_error', {})
    for rule in rules:
        predicate = rule['predicate']
        if any(type(parameters.get(key)) is not type(value) or parameters.get(key) != value
               for key, value in rule['parameters'].items()):
            continue
        allowed = predicates.get(predicate, False)
        if predicate == 'symlink_error':
            # This is the actual caught native OSError, not an annotation prefix.
            import errno
            allowed = (type(error.get('errno')) is int
                       and error['errno'] in (errno.EACCES, errno.EPERM, errno.ENOSYS, errno.ENOTSUP)
                       and isinstance(error.get('message'), str) and bool(error['message']))
        elif predicate == 'long_filename':
            import errno
            try:
                observations = [json.loads(value) for key, value in evidence.get('properties', [])
                                if key == 'usage_directory_observations']
                allowed = (error.get('errno') == errno.ENAMETOOLONG and len(observations) == 1
                    and observations[0] == {'initial_bytes': parameters.get('initial_bytes'),
                        'name_character': parameters.get('name_character'),
                        'name_bytes': len(('1' + parameters['name_character'] * 230 + '.jsonl').encode('utf-8')),
                        'supported': False, 'errno': errno.ENAMETOOLONG}
                    and parameters.get('name_character') in ('é', '漢')
                    and parameters.get('initial_bytes') in (12, 32, 64, 128, 256, 512, 640, 768, 1024, 4096))
            except (KeyError, TypeError, ValueError):
                allowed = False
        if allowed:
            return {'nodeid': node, 'reason': reason, 'predicate': predicate,
                    'platform_capabilities': facts, 'node_evidence': node_evidence,
                    'skip_evidence': evidence}
    return None


def is_subtest_report(report: dict) -> bool:
    """Only the captured pytest report class and its real context identify a subtest."""
    context = report.get('subtest')
    return (report.get('report_type') == '_pytest.subtests.SubtestReport'
            and report.get('when') == 'call' and isinstance(context, dict)
            and set(context) == {'msg', 'kwargs'}
            and (context['msg'] is None or isinstance(context['msg'], str))
            and isinstance(context['kwargs'], dict)
            and all(isinstance(key, str) and isinstance(value, str)
                    for key, value in context['kwargs'].items()))


def invalid_subtest_report(report: dict) -> bool:
    return (report.get('subtest') is not None
            or report.get('report_type') == '_pytest.subtests.SubtestReport') and not is_subtest_report(report)


def test_verdict(record: dict, *, collect: bool = False) -> dict:
    profile = record.get('profile', {})
    nodes = profile.get('selected_nodeids', [])
    problems = []
    if record.get('exit_code') != 0:
        problems.append('process did not succeed')
    if not nodes or len(nodes) != len(set(nodes)):
        problems.append('empty or duplicate selected inventory')
    if profile.get('collection_errors'):
        problems.append('collection errors')
    inventory = record.get('final_collection', record.get('collection', []))
    collected = [row['nodeid'] for row in inventory]
    selected = [row['nodeid'] for row in inventory if row.get('selected')]
    if not collected or len(collected) != len(set(collected)) or Counter(selected) != Counter(nodes):
        problems.append('collected/selected inventory mismatch')
    if 'final_collection' in record:
        original = record.get('collection', [])
        if Counter(collected) != Counter(row['nodeid'] for row in original):
            problems.append('final selection lost/duplicated original collected nodes')
        original_by_node = {row['nodeid']: row for row in original}
        for row in inventory:
            restored = {key: value for key, value in row.items() if key != 'selection_before_pytest_filters'}
            restored['selected'] = row.get('selection_before_pytest_filters')
            if restored != original_by_node.get(row['nodeid']):
                problems.append('final selection changed original collection metadata')
    outcomes = profile.get('reports', [])
    optional_skips = []
    for item in outcomes:
        if invalid_subtest_report(item):
            problems.append(f"{item['nodeid']}: invalid subtest provenance")
        if item['nodeid'] not in nodes:
            problems.append(f"{item['nodeid']}: outcome outside selected inventory")
        if item.get('wasxfail') is not None or item['outcome'] == 'failed':
            problems.append(f"{item['nodeid']}: {item['when']} failed/xfail")
        if item['outcome'] == 'skipped':
            evidence = None if is_subtest_report(item) else optional_skip_evidence(profile, item)
            if evidence is not None:
                optional_skips.append(evidence)
            else:
                problems.append(f"{item['nodeid']}: required skip")
    if not collect:
        calls = Counter(item['nodeid'] for item in outcomes
                        if item['when'] == 'call' and not is_subtest_report(item))
        optional_nodes = {item['nodeid'] for item in optional_skips}
        if any(calls[node] > 1 or (calls[node] != 1 and node not in optional_nodes) for node in nodes):
            problems.append('selected tests missing exactly one call outcome')
        if 'junit.xml' not in record:
            problems.append('missing JUnit')
        else:
            try:
                cases = list(parse_junit_xml(raw_evidence_bytes(record, 'junit.xml') if 'junit.xml' in record.get('raw_files', {}) else record['junit.xml']).iter('testcase'))
                if len(cases) != len(nodes):
                    problems.append('JUnit/selected inventory mismatch')
            except ET.ParseError:
                problems.append('invalid JUnit')
    return {'status': 'fail' if problems else 'pass', 'problems': problems,
            'optional_platform_skips': optional_skips, 'selected_count': len(nodes),
            'collected_count': len(collected)}


def canary_verdict(record: dict) -> dict:
    discovered = re.search(r'Ran ([1-9][0-9]*) tests? in ', record.get('stderr', ''))
    named = 'test_import_collection_test_child_and_failure_are_isolated' in record.get('stderr', '')
    return {'status': 'pass' if record.get('status') == 'pass' and discovered and named else 'fail',
            'execution': record['id'], 'discovered': int(discovered[1]) if discovered else 0}

def matrix_selections(rows: list[dict]) -> dict[str, list[str]]:
    """Every functional node has one owner; explicit history stays inventoried."""
    result: dict[str, list[str]] = {}
    for row in rows:
        category, node, host = row['suite'], row['nodeid'], row.get('host')
        if category == 'capacity':
            raise ValueError(f'unreviewed capacity classification: {node}')
        if category == 'packages':
            partition = f'{category}/{host or "hostless"}'
        elif category in ('core', 'native'):
            count = 4 if category == 'core' else 2
            partition = f'{category}/{shard_for(node, count)}of{count}'
        else:
            partition = category
        result.setdefault(partition, []).append(node)
    return result


def verify_partition(rows: list[dict], actual: dict[str, list[str]]) -> dict:
    complete = [row['nodeid'] for row in rows]
    inventory = [row['nodeid'] for row in rows]
    expected = matrix_selections(rows)
    observed = Counter(node for nodes in actual.values() for node in nodes)
    problems = []
    if not inventory or len(complete) != len(set(complete)):
        problems.append('empty or duplicate inventory')
    if observed != Counter(inventory):
        problems.append('matrix is not an exact-once union of inventory')
    if set(actual) != set(expected) or any(set(actual.get(key, [])) != set(nodes) for key, nodes in expected.items()):
        problems.append('category/host/shard ownership mismatch')
    return {'status': 'fail' if problems else 'pass', 'problems': problems,
            'platform': sys.platform, 'inventory': inventory, 'complete_inventory': complete,
            'partitions': actual,
            'count': len(inventory)}

# Assertion IDs are the one-based positions in the accepted verification strategy.
# Explicit bindings are reviewed source, never inferred from a filename or a green
# module. Later producers/integration must add their actual function bindings.
SCENARIO_REGISTRY = {'LOCAL-ACC-01:1': ['taskplane/tests/test_native_acceptance_persistence.py::test_repeated_terminal_preserves_exact_independent_acceptance',
                    'taskplane/tests/test_native_acceptance_persistence.py::test_conflicting_terminal_retains_history_with_explicit_invalidation'],
 'LOCAL-BROAD-AND-QUALITY:1': ['audit:broad-and-quality'],
 'LOCAL-BROWSER:1': ['taskplane/tests/test_dashboard_browser.py::test_dashboard_browser_functional_contract'],
 'LOCAL-CI-PROD-03:1': ['taskplane/tests/test_residual_obligations.py::test_repeated_invalidation_never_revives_dependants_without_new_evidence',
                        'taskplane/tests/test_residual_obligations.py::test_semantic_cycle_invalidates_coupled_evidence_without_execution_cycle',
                        'taskplane/tests/test_residual_obligations.py::test_each_fingerprint_change_invalidates_only_affected_contributions',
                        'taskplane/tests/test_residual_obligations.py::test_schedule_only_dependency_ready_residuals_and_preserve_semantic_root_review',
                        'taskplane/tests/test_residual_obligations.py::test_residual_dispatch_blockers_are_not_scheduled'],
 'LOCAL-CI-PROD-05:1': ['taskplane/tests/test_product_revision.py::test_revision_preview_and_same_run_return_preserve_approval_history',
                        'taskplane/tests/test_product_revision.py::test_revision_refusal_is_read_only',
                        'taskplane/tests/test_product_revision.py::test_revision_abort_retains_fences_and_resume_requires_review',
                        'taskplane/tests/test_product_revision.py::test_revision_fences_pending_reservation_until_supported_abandon',
                        'taskplane/tests/test_product_revision.py::test_revision_crash_boundary_and_exact_retry',
                        'taskplane/tests/test_product_revision.py::test_revision_requires_three_fresh_acceptances_before_build',


                        'taskplane/tests/test_product_revision.py::test_runtime_pin_refuses_hot_switch_but_preserves_read_access',


                        'taskplane/tests/test_product_revision.py::test_corrupt_revision_history_refuses_before_grant',
                        'taskplane/tests/test_product_revision.py::test_native_revision_dashboard_distinguishes_review_and_historical_approval',
                        'taskplane/tests/test_product_revision.py::test_selective_revision_preserves_unaffected_worker_until_result_review',

                        'taskplane/tests/test_product_revision.py::test_pending_revision_rechecks_current_graph_before_opening_product',
                        ],
 'LOCAL-CI-PROD-06:1': ['taskplane/tests/test_ci_local.py::test_optional_platform_skip_is_distinct_and_cannot_cover_required_assertion',
                        'taskplane/tests/test_ci_local.py::test_functional_collection_roundtrip_without_object_count_target',
                        'taskplane/tests/test_ci_local.py::test_root_and_native_reconciliation_preserves_accepted_subset',
                        'taskplane/tests/test_ci_local.py::test_optional_skip_needs_exact_evidence',
                        'taskplane/tests/test_ci_local.py::test_required_binding_never_accepts_incomplete_execution',
                        'taskplane/tests/test_ci_local.py::test_lossless_evidence_roundtrip_after_temporary_cleanup',
                        'audit:ci-definition',
                        'audit:exact-once-partition',
                        'audit:canary'],
 'LOCAL-CONTEXT-UPGRADE:1': ['taskplane/tests/test_product_revision.py::test_revision_preview_and_same_run_return_preserve_approval_history',
                             'taskplane/tests/test_product_revision.py::test_revision_refusal_is_read_only',
                             'taskplane/tests/test_product_revision.py::test_revision_abort_retains_fences_and_resume_requires_review',
                             'taskplane/tests/test_product_revision.py::test_revision_fences_pending_reservation_until_supported_abandon',
                             'taskplane/tests/test_product_revision.py::test_revision_crash_boundary_and_exact_retry',
                             'taskplane/tests/test_product_revision.py::test_revision_requires_three_fresh_acceptances_before_build',


                             'taskplane/tests/test_product_revision.py::test_runtime_pin_refuses_hot_switch_but_preserves_read_access',


                             'taskplane/tests/test_product_revision.py::test_corrupt_revision_history_refuses_before_grant',
                             'taskplane/tests/test_product_revision.py::test_native_revision_dashboard_distinguishes_review_and_historical_approval',
                             'taskplane/tests/test_product_revision.py::test_selective_revision_preserves_unaffected_worker_until_result_review',

                             'taskplane/tests/test_product_revision.py::test_pending_revision_rechecks_current_graph_before_opening_product',
                             ],
 'LOCAL-DEVOPS-01:1': ['audit:canary'],
 'LOCAL-DEVOPS-02:1': ['audit:exact-once-partition',
                       'audit:execution-profiles',
                       'audit:historical-ci',
                       'audit:ci-definition'],
 'LOCAL-DS-01:1': ['taskplane/tests/test_initialization_recovery_receipt.py::test_interrupted_initial_restore_finishes_same_receipt',
                   'taskplane/tests/test_initialization_recovery_receipt.py::test_matching_target_cannot_manufacture_recovery_provenance',
                   'taskplane/tests/test_initialization_recovery_receipt.py::test_prepared_retry_rejects_changed_scope_without_writes',
                   'taskplane/tests/test_initialization_recovery_receipt.py::test_exact_restoration_and_completed_retry_preserve_original_bytes',
                   'taskplane/tests/test_initialization_recovery_receipt.py::test_completed_retry_retains_original_request_provenance',
                   'taskplane/tests/test_initialization_recovery_receipt.py::test_restored_receipt_with_missing_target_is_not_replayed',
                   'taskplane/tests/test_initialization_recovery_receipt.py::test_unsupported_directory_sync_never_acknowledges_restore',
                   'taskplane/tests/test_initialization_recovery_receipt.py::test_missing_no_follow_primitive_refuses_without_authority_writes',
                   'taskplane/tests/test_workflow_recovery.py::test_interrupted_restore_retirement_and_relocation_preserve_authentic_history'],
 'LOCAL-DS-02:1': ['taskplane/tests/test_state_import_boundaries.py::test_import_durability_interruptions_retry_without_false_commit',
                   'taskplane/tests/test_state_import_boundaries.py::test_new_import_ancestry_flushes_child_and_owning_parent_before_commit',
                   'taskplane/tests/test_state_import_boundaries.py::test_existing_import_file_requires_successful_retry_sync',
                   'taskplane/tests/test_state_import_boundaries.py::test_unsupported_import_directory_sync_cannot_commit',
                   'taskplane/tests/test_state_import.py::test_invalid_import_destination_refuses_before_prepared_receipt',
                   'taskplane/tests/test_state_import.py::test_invalid_import_does_not_write_or_replace_evidence',
                   'taskplane/tests/test_state_import.py::test_racing_file_is_never_overwritten_or_retried_as_success',
                   'taskplane/tests/test_state_import_boundaries.py::test_import_cannot_outlive_retained_request_or_approval',
                   'taskplane/tests/test_state_import_boundaries.py::test_interrupted_import_cannot_switch_request_on_retry',
                   'taskplane/tests/test_state_import_boundaries.py::test_oversized_receipt_refuses_before_durable_publication'],
 'LOCAL-EVID-01:1': ['audit:obligation-dispositions', 'audit:packages'],
 'LOCAL-GRAPH:1': ['taskplane/tests/test_graph_primitives.py::test_graph_change_recomputes_components_without_changing_prior_snapshot',
                   'taskplane/tests/test_graph_primitives.py::test_annotations_and_test_fixtures_do_not_inflate_dependency_count',
                   'taskplane/tests/test_graph_primitives.py::test_manifest_identity_is_selected_from_nearest_owned_package',
                   'taskplane/tests/test_graph_primitives.py::test_iterative_scc_beyond_recursion_depth',
                   'taskplane/tests/test_graph_primitives.py::test_iterative_scc_boundary_shapes',
                   'taskplane/tests/test_graph_primitives.py::test_iterative_scc_order_and_resource_refusal',
                   'taskplane/tests/test_graph_primitives.py::test_iterative_scc_matches_reachability_oracle',
                   'taskplane/tests/test_graph_primitives.py::test_iterative_scc_explicit_node_limit',
                   'taskplane/tests/test_decompose.py::test_dependency_decomposition_deep_chain_and_cycle',
                   'taskplane/tests/test_decompose.py::test_dependency_decomposition_preserves_owner_and_source_boundaries',
                   'taskplane/tests/test_decompose.py::test_dependency_decomposition_ignores_scanner_annotations_and_declared_edges'],
 'LOCAL-HOST-WATCH:1': ['taskplane/tests/test_host_compatibility_watch.py::test_host_watch_observation_contract'],
 'LOCAL-INT-01:1': ['taskplane/tests/test_claude_stop_before_handback.py::test_pre_handback_stop_reconciles_original_call_and_preserves_other_work'],
 'LOCAL-INT-02:1': ['taskplane/tests/test_claude_worker_lifecycle.py::test_explicit_delivery_cycles_keep_sequential_reports_distinct',
                    'taskplane/tests/test_claude_worker_lifecycle.py::test_explicit_delivery_cycles_refuse_ambiguous_or_cross_cycle_evidence',
                    'taskplane/tests/test_claude_worker_lifecycle.py::test_delivery_cycle_orders_validate_explicit_identity_not_timestamp_proximity'],
 'LOCAL-LARGE-RECOVERY:1': ['taskplane/tests/test_host_capabilities.py::test_complete_parent_and_child_over_64mib_use_bounded_continuations',
                            'taskplane/tests/test_host_capabilities.py::test_recovery_continuation_refuses_changed_snapshot',
                            'taskplane/tests/test_host_capabilities.py::test_recovery_retry_has_deterministic_progress_and_detects_late_conflict',
                            'taskplane/tests/test_native_worker_dispatch.py::test_partial_recovery_persists_cursor_without_releasing_reservation'],
 'LOCAL-PACKAGE:1': ['audit:packages'],
 'LOCAL-RETENTION:1': ['taskplane/tests/test_workflow_retention.py::test_retire_preserves_unaccepted_output_and_rejects_stale_revision',
                       'taskplane/tests/test_workflow_retention.py::test_retention_roundtrip_tamper_and_failed_index_write',
                       'taskplane/tests/test_workflow_retention.py::test_archive_failure_keeps_original_index',
                       'taskplane/tests/test_workflow_retention.py::test_active_and_protected_history_never_auto_archive',
                       'taskplane/tests/test_workflow_retention.py::test_snapshot_stable_store_collects_only_owned_pair',
                       'taskplane/tests/test_workflow_retention.py::test_snapshot_exact_entry_cap_and_overflow',
                       'taskplane/tests/test_workflow_retention.py::test_snapshot_depth_cap_and_exhaustion',
                       'taskplane/tests/test_workflow_retention.py::test_snapshot_time_budget_counts_completed_read',
                       'taskplane/tests/test_workflow_retention.py::test_snapshot_reference_byte_cap_and_exhaustion',
                       'taskplane/tests/test_workflow_retention.py::test_snapshot_changing_complete_scans_delete_nothing',
                       'taskplane/tests/test_workflow_retention.py::test_snapshot_protected_identities',
                       'taskplane/tests/test_workflow_retention.py::test_snapshot_partial_scan_preserves_required_and_refuses_pressure',
                       'taskplane/tests/test_workflow_retention.py::test_snapshot_complete_pressure_reclaims_before_writing',
                       'taskplane/tests/test_workflow_retention.py::test_snapshot_byte_pressure_and_unsupported_collection',
                       'taskplane/tests/test_workflow_retention.py::test_snapshot_failed_durable_write_never_selects',
                       'taskplane/tests/test_workflow_retention.py::test_snapshot_byte_pressure_keeps_required_publication',
                       'taskplane/tests/test_workflow_retention.py::test_snapshot_nested_catalog_and_rendering_names_are_reference_roots'],
 'LOCAL-SEC-01:1': ['taskplane/tests/test_delegated_approval.py::test_delegated_policy_preserves_origin_and_advances_only_after_assessment',
                    'taskplane/tests/test_delegated_approval.py::test_delegated_policy_rejects_wrong_source_or_binding_without_mutation',
                    'taskplane/tests/test_delegated_approval.py::test_relay_replay_is_exact_and_cannot_authorize_a_new_checkpoint',
                    'taskplane/tests/test_delegated_checkpoint.py::test_owner_observation_applies_exact_checkpoint_and_preserves_source',
                    'taskplane/tests/test_delegated_checkpoint.py::test_wrong_or_stale_observations_are_rejected_without_mutation',
                    'taskplane/tests/test_delegated_checkpoint.py::test_sec01_transport_stale_or_foreign_binding_is_atomic',
                    'taskplane/tests/test_delegated_checkpoint.py::test_sec01_transport_proof_precedes_question_validation',
                    'taskplane/tests/test_delegated_checkpoint.py::test_sec02_v1_missing_original_question_requires_fresh_consent',
                    'taskplane/tests/test_delegated_checkpoint.py::test_sec02_caller_text_cannot_replace_pinned_original_question',
                    'taskplane/tests/test_delegated_checkpoint.py::test_sec01_exact_original_request_replay_is_idempotent',
                    'taskplane/tests/test_delegated_checkpoint.py::test_sec01_consumed_event_cannot_approve_a_later_checkpoint',
                    'taskplane/tests/test_delegated_checkpoint.py::test_sec01_conflicting_replay_is_refused_without_authority_mutation',
                    'taskplane/tests/test_ordinary_checkpoint_reply.py::test_ordinary_approval_keeps_null_link_and_complete_fixture_provenance',
                    'taskplane/tests/test_ordinary_checkpoint_reply.py::test_incomplete_conflicting_or_foreign_context_is_rejected_without_mutation',
                    'taskplane/tests/test_reopen_checkpoint_consent.py::test_reopen_yes_preserves_history_and_allows_only_checkpoint_revision',

                    'taskplane/tests/test_reopen_checkpoint_consent.py::test_yes_to_acceptance_stays_approval',


                    'taskplane/tests/test_reopen_checkpoint_consent.py::test_reopen_retains_evidence_and_replay_boundaries',

                    ],
 'LOCAL-SOURCE-IDENTITY:1': ['audit:source-identity',
                             'audit:producer-reconciliation',
                             'audit:candidate-graph'],
 'LOCAL-SRE-03:1': ['taskplane/tests/test_worker_results.py::test_result_completeness_requires_artifacts_checks_and_semantic_review',
                    'taskplane/tests/test_worker_results.py::test_durable_partial_rejected_evidence_precedes_structured_response',
                    'taskplane/tests/test_worker_results.py::test_invalid_requests_do_not_mutate_controller_or_publish_objects',
                    'taskplane/tests/test_worker_results.py::test_controller_size_limit_refuses_unchanged',
                    'taskplane/tests/test_worker_results.py::test_already_held_controller_lock_retains_generation_authority_and_size_checks',
                    'taskplane/tests/test_worker_results.py::test_prior_satisfaction_cannot_mask_changed_or_unknown_current_fingerprint',
                    'taskplane/tests/test_worker_results.py::test_absent_opaque_legacy_contract_remains_unchanged',
                    'taskplane/tests/test_worker_results.py::test_unknown_reserved_schema_refuses',
                    'taskplane/tests/test_worker_results.py::test_supported_completion_version_validates_and_only_freezes_attempt_cap',
                    'taskplane/tests/test_worker_results.py::test_runtime_pin_refuses_downgrade_hot_switch_and_unapproved_migration',
                    'taskplane/tests/test_context_reuse.py::test_actual_check_reuse_and_unrelated_edit',
                    'taskplane/tests/test_context_reuse.py::test_relevant_changes_never_reuse',
                    'taskplane/tests/test_context_reuse.py::test_negative_and_incomplete_evidence_preserves_findings',
                    'taskplane/tests/test_context_reuse.py::test_missing_or_stale_graph_is_not_eligible',
                    'taskplane/tests/test_context_reuse.py::test_changed_external_dependency_at_same_path_never_reuses',
                    'taskplane/tests/test_context_reuse.py::test_unrelated_external_dependency_does_not_disable_covered_reuse',
                    'taskplane/tests/test_context_reuse.py::test_contribution_capture_pins_each_actual_input_dimension',
                    'taskplane/tests/test_context_reuse.py::test_contribution_external_coverage_remains_unknown',
                    'taskplane/tests/test_context_reuse.py::test_transitive_dependency_census_changes_are_captured',
                    'taskplane/tests/test_residual_obligations.py::test_repeated_invalidation_never_revives_dependants_without_new_evidence',
                    'taskplane/tests/test_residual_obligations.py::test_semantic_cycle_invalidates_coupled_evidence_without_execution_cycle',
                    'taskplane/tests/test_residual_obligations.py::test_each_fingerprint_change_invalidates_only_affected_contributions',
                    'taskplane/tests/test_residual_obligations.py::test_schedule_only_dependency_ready_residuals_and_preserve_semantic_root_review',
                    'taskplane/tests/test_residual_obligations.py::test_residual_dispatch_blockers_are_not_scheduled',
                    'taskplane/tests/test_retry_budgets.py::test_retry_numeric_contract',
                    'taskplane/tests/test_retry_budgets.py::test_attempt_caps_have_no_implicit_default',
                    'taskplane/tests/test_retry_budgets.py::test_optional_caps_do_not_invent_unrelated_constraints',
                    'taskplane/tests/test_retry_budgets.py::test_later_elapsed_cap_cannot_restart_an_unknown_original_clock',
                    'taskplane/tests/test_retry_budgets.py::test_zero_reservation_and_missing_coverage_refuse_without_launch',
                    'taskplane/tests/test_retry_budgets.py::test_issue_rechecks_tightened_caps_and_retains_observed_overshoot',
                    'taskplane/tests/test_retry_budgets.py::test_uncertain_attempt_requires_authoritative_no_launch_proof',
                    'taskplane/tests/test_retry_budgets.py::test_reset_and_clock_faults_require_explicit_reconciliation',
                    'taskplane/tests/test_retry_budgets.py::test_all_applicable_scopes_reserve_in_one_atomic_commit'],
 'LOCAL-TELEMETRY-SEMANTICS:1': ['taskplane/tests/test_usage_accounting.py::test_original_missing_revision_is_recovered_only_to_same_build_visit',
                                 'taskplane/tests/test_usage_accounting.py::test_missing_transition_across_phases_remains_explained_not_guessed',
                                 'taskplane/tests/test_usage_accounting.py::test_follow_up_and_pre_run_are_separate_from_phases',
                                 'taskplane/tests/test_usage_accounting.py::test_missing_finish_boundary_does_not_claim_follow_up_amount',
                                 'taskplane/tests/test_usage_accounting.py::test_saved_or_partial_boundary_cannot_be_treated_as_fresh',
                                 'taskplane/tests/test_usage_accounting.py::test_late_session_and_unknown_boundary_have_session_amount_and_reason',
                                 'taskplane/tests/test_usage_accounting.py::test_no_boundaries_retains_known_amounts_and_does_not_invent_zero_usage',
                                 'taskplane/tests/test_usage_accounting.py::test_duplicate_boundaries_do_not_double_count_and_reset_is_explicit',
                                 'taskplane/tests/test_usage_accounting.py::test_host_review_excluded_and_cached_input_not_added_again',
                                 'taskplane/tests/test_usage_accounting.py::test_report_details_and_dashboard_explain_usage_without_raw_html',
                                 'taskplane/tests/test_usage_accounting.py::test_next_run_baseline_closes_history_when_native_log_is_unavailable',
                                 'taskplane/tests/test_usage_accounting.py::test_excess_session_allocation_is_explained_instead_of_silently_clamped',
                                 'taskplane/tests/test_usage_accounting.py::test_dashboard_distinguishes_known_empty_bucket_from_unknown',
                                 'taskplane/tests/test_usage_accounting.py::test_claude_reused_worker_charges_only_current_messages',
                                 'taskplane/tests/test_usage_accounting.py::test_root_baseline_must_be_complete_before_run_delta',
                                 'taskplane/tests/test_usage_accounting.py::test_reconcile_missing_known_worker_keeps_token_coverage_incomplete',
                                 'taskplane/tests/test_usage_accounting.py::test_claude_recovered_prerun_counter_does_not_become_run_usage',
                                 'taskplane/tests/test_usage_accounting.py::test_claude_missing_snapshot_still_reports_unavailable_identity',
                                 'taskplane/tests/test_usage_accounting.py::test_resumed_codex_reset_is_not_restored_from_saved_or_closing_counts',
                                 'taskplane/tests/test_usage_accounting.py::test_reconcile_checkpoint_preserves_partial_owned_attribution'],
 'OVERHEAD-01:1': ['taskplane/tests/test_overhead_remediation.py::test_public_seven_phase_flow_has_separate_human_decisions_without_overhead',
                   'taskplane/tests/test_overhead_remediation.py::test_task_dag_refuses_future_phase_prerequisite_before_publication']}


def safe_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part in ('..', '.') for part in path.parts):
        raise ValueError(f'unsafe relative path: {relative}')
    cursor = root
    for part in path.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError(f'symlink: {relative}')
    if not cursor.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'path escapes root: {relative}')
    return cursor


def load_manifest(path: Path, invocation: Path) -> tuple[dict, dict]:
    path = safe_path(invocation, str(path.relative_to(invocation)))
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema') != 'taskplane.remediation-source-manifest/v1' or manifest.get('source', {}).get('commit') != BASELINE:
        raise ValueError('source manifest schema/baseline mismatch')
    if safe_path(invocation, manifest['candidate_root']).resolve() != ROOT.resolve():
        raise ValueError('manifest cannot select another source root')
    inputs = {}
    for relative, expected in manifest['inputs']:
        bound = safe_path(invocation, relative)
        raw = bound.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f'bound prerequisite drift: {relative}')
        inputs[relative] = raw
    strategy_path = remediation_paths(path, invocation)['strategy']
    if strategy_path.relative_to(invocation).as_posix() not in inputs:
        raise ValueError('verification strategy must be a hash-bound manifest input')
    strategy = normalize_functional_strategy(json.loads(strategy_path.read_bytes()))
    if strategy.get('run') != manifest.get('run'):
        raise ValueError('verification strategy run mismatch')
    validate_functional_strategy(strategy)
    return manifest, strategy


def remediation_paths(path: Path, invocation: Path) -> dict[str, Path]:
    """Use this manifest's accepted Plan and evidence, never a historical store."""
    relative = path.relative_to(invocation)
    if relative.parts[-2:] != ('build', 'source-manifest.json'):
        raise ValueError('manifest must name build/source-manifest.json')
    base = relative.parent.parent
    return {key: safe_path(invocation, (base / name).as_posix()) for key, name in {
        'strategy': 'plan/verification-strategy.json', 'plan': 'plan/output.json',
        'ledger': 'build/native-evidence.json'}.items()}


def validate_functional_strategy(strategy: dict) -> None:
    """Validate declared checks, without a global group/criterion/startup quota."""
    message = 'invalid functional strategy: declare unique checks, criteria and assertions'
    try:
        schema = strategy['schema']
        if schema not in ('remediation-plan-verification/v5', 'remediation-plan-verification/v6'):
            raise ValueError(message)
        key = 'required_engineering_subchecks' if schema.endswith('/v6') else 'required_local_subchecks'
        specs = strategy[key]
        if not isinstance(specs, list) or not specs:
            raise ValueError(message)
        if key != 'required_local_subchecks' and strategy.get('required_local_subchecks', specs) != specs:
            raise ValueError(message)
        ids = [spec['id'] for spec in specs]
        if any(not isinstance(value, str) or not value.strip() for value in ids) or len(ids) != len(set(ids)):
            raise ValueError(message)
        for spec in specs:
            criteria, assertions = spec['criteria'], spec['assertions']
            if (spec.get('required') is not True or not isinstance(criteria, list) or not criteria
                    or any(not isinstance(value, str) or not value.strip() for value in criteria)
                    or len(criteria) != len(set(criteria)) or not isinstance(assertions, list) or not assertions
                    or any(not isinstance(value, str) or not value.strip() for value in assertions)):
                raise ValueError(message)
        gates = strategy.get('later_stage_gates', [])
        if (not isinstance(gates, list) or any(not isinstance(gate, dict) or not isinstance(gate.get('id'), str)
                or not gate['id'].strip() or gate.get('required') is not True for gate in gates)
                or len(gates) != len({gate['id'] for gate in gates})):
            raise ValueError(message)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(message) from exc


def strategy_groups(strategy: dict) -> set[str]:
    return {spec['id'] for spec in strategy['required_local_subchecks']}


def strategy_criteria(strategy: dict) -> set[str]:
    return {criterion for spec in strategy['required_local_subchecks'] for criterion in spec['criteria']}


def normalize_functional_strategy(strategy: dict) -> dict:
    validate_functional_strategy(strategy)
    return {**strategy, 'required_local_subchecks': strategy[
        'required_engineering_subchecks' if strategy['schema'].endswith('/v6') else 'required_local_subchecks']}


def audit_source(manifest: dict, before: dict, after: dict) -> dict:
    rows = [dict(zip(manifest['files_columns'], row)) for row in manifest['files']]
    expected = {row['path'] for row in rows}
    problems = []
    if not expected or len(expected) != len(rows):
        problems.append('invalid expected source census')
    if set(before) != expected:
        problems.append('source census differs from accepted manifest')
    if before != after:
        problems.append('candidate bytes/modes changed during execution')
    if 'current_inventory' in manifest:
        prefix = manifest['candidate_root'] + '/'
        inventory = manifest['current_inventory']
        if not isinstance(inventory, dict) or any(not name.startswith(prefix) for name in inventory):
            problems.append('invalid current source inventory')
        elif normalized_source_inventory({name[len(prefix):]: value for name, value in inventory.items()}) != before:
            problems.append('current source identity differs from accepted inventory')
    modified, added, unchanged = [], [], []
    for row in rows:
        safe_path(ROOT, row['path'])
        actual = before.get(row['path'], {})
        if actual.get('kind') != 'file':
            problems.append(f"missing/nonregular source: {row['path']}")
        elif row['passive_sha256'] is not None and (actual['sha256'] != row['passive_sha256']
                or actual['mode'] != row['passive_mode'][-4:]):
            problems.append(f"passive identity drift: {row['path']}")
        if row['baseline_sha256'] is None:
            added.append(row['path'])
        elif actual.get('sha256') == row['baseline_sha256']:
            unchanged.append(row['path'])
        else:
            modified.append(row['path'])
    return {'status': 'fail' if problems else 'pass', 'problems': problems,
            'missing': sorted(expected - set(before)), 'unexpected': sorted(set(before) - expected),
            'modified': modified, 'new': added, 'unchanged': unchanged,
            'before': before, 'after': after,
            'feature_acceptance': 'Root separately reconciles accepted producer manifests; census alone is not acceptance.'}


def audit_producer_reconciliation(manifest: dict, manifest_sha256: str, before: dict,
                                  after: dict, ledger_before: bytes, ledger_after: bytes,
                                  plan_raw: bytes = b'{}', invocation: Path | None = None) -> dict:
    result = {'status': 'fail', 'problems': [],
              'ledger_before_sha256': hashlib.sha256(ledger_before).hexdigest(),
              'ledger_after_sha256': hashlib.sha256(ledger_after).hexdigest(),
              'authority': 'Read-only validation of root-recorded reconciliation; never native acceptance.'}
    problems = result['problems']
    if ledger_before != ledger_after:
        problems.append('root reconciliation ledger changed during execution')
    try:
        ledger = json.loads(ledger_before)
        entries = [entry for entry in ledger['entries'] if entry.get('id') == 'BUILD-FINAL-PRODUCER-RECONCILIATION']
        if not entries:
            problems.append('missing root final producer reconciliation')
            return result
        entry = entries[-1]
        result['entry'] = entry
        rows = [dict(zip(manifest['files_columns'], row)) for row in manifest['files']]
        owners = {row['owner'] for row in rows}
        plan = json.loads(plan_raw)
        tasks = {task['id']: task for task in plan['task_dag']}
        if (entry.get('schema') != 'taskplane.remediation-producer-reconciliation/v2'
                or entry.get('run') != manifest['run'] or not entry.get('visit')
                or plan.get('run') != manifest['run']
                or entry.get('plan_sha256') != hashlib.sha256(plan_raw).hexdigest()
                or type(entry.get('revision')) is not int or entry['revision'] < 0
                or entry.get('source_manifest_sha256') != manifest_sha256
                or entry.get('candidate_root') != manifest['candidate_root']):
            problems.append('reconciliation binding mismatch')
        if entry.get('files') != before or entry.get('files') != after:
            problems.append('reconciled full source map differs from execution')
        results, observations = entry['task_results'], entry['native_observations']
        owner_manifests, owner_evidence = entry['owner_manifests'], entry['owner_evidence']
        native_owners = {owner for owner in owners if tasks.get(owner, {}).get('execution') == 'native_required'}
        if (set(results) != owners or set(observations) != native_owners
                or set(owner_manifests) != owners or set(owner_evidence) != owners):
            problems.append('incomplete/extra producer owner set')
        for owner in sorted(owners):
            accepted, observed = results.get(owner, {}), observations.get(owner, {})
            task = tasks.get(owner, {})
            if not accepted.get('accepted_at') or not accepted.get('reviewer') or not accepted.get('task_digest'):
                problems.append(f'{owner}: missing actual accepted task record')
            if owner in native_owners:
                if (not accepted.get('grant') or not accepted.get('worker_id')
                        or observed.get('state') != 'accepted' or observed.get('terminal_status') != 'completed'
                        or not observed.get('reference')
                        or observed.get('grant') != accepted.get('grant')
                        or observed.get('worker_id') != accepted.get('worker_id')):
                    problems.append(f'{owner}: missing/inconsistent accepted native record')
            elif task.get('execution') != 'root':
                problems.append(f'{owner}: missing planned root ownership')
            evidence = owner_evidence.get(owner)
            if (not isinstance(evidence, list) or not evidence
                    or any(not isinstance(ref, str) or not ref.strip() for ref in evidence)):
                problems.append(f'{owner}: missing actual handback/root inventory evidence references')
            checks = accepted.get('checks', [])
            if not checks or any(check.get('status') != 'pass' or not check.get('name') or not check.get('evidence') for check in checks):
                problems.append(f'{owner}: missing accepted checks')
            paths = {manifest['candidate_root'] + '/' + row['path'] for row in rows if row['owner'] == owner}
            planned = {name for name in task.get('paths', []) if name.startswith(manifest['candidate_root'] + '/')}
            if paths != planned or set(owner_manifests.get(owner, {})) != paths:
                problems.append(f'{owner}: full owner map differs from frozen Plan/source census')
            accepted_manifest = accepted.get('manifest', {})
            if not set(accepted_manifest) <= set(task.get('paths', [])):
                problems.append(f'{owner}: accepted manifest exceeds frozen scope')
            for relative in paths:
                name = relative[len(manifest['candidate_root']) + 1:]
                if owner_manifests.get(owner, {}).get(relative) != before.get(name, {}).get('sha256'):
                    problems.append(f'{owner}: reconciled handback file identity mismatch: {name}')
                if relative in accepted_manifest and accepted_manifest[relative] != before.get(name, {}).get('sha256'):
                    problems.append(f'{owner}: accepted file identity mismatch: {name}')
            for relative in set(accepted_manifest) - paths:
                if invocation is None or hashlib.sha256(safe_path(invocation, relative).read_bytes()).hexdigest() != accepted_manifest[relative]:
                    problems.append(f'{owner}: accepted evidence identity mismatch: {relative}')
            result['acceptance_scope'] = 'Accepted result manifests are preserved subsets; owner_manifests are separately reconciled full handback identity.'
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        problems.append(f'malformed producer reconciliation: {exc}')
    result['status'] = 'fail' if problems else 'pass'
    return result


def scanner_inventory(source: dict, policy_raw: str) -> dict:
    """Independent reader for the exact pinned flat components exclusion policy."""
    prefixes = []
    section = None
    for line in policy_raw.splitlines():
        line = line.split('#', 1)[0].rstrip()
        if not line.strip():
            continue
        if line == 'exclude:':
            section = 'exclude'
        elif section == 'exclude' and re.fullmatch(r'  - [A-Za-z0-9_./-]+', line):
            prefix = line[4:].strip().rstrip('/')
            if not prefix or Path(prefix).is_absolute() or '..' in Path(prefix).parts:
                raise ValueError('unsafe scanner exclusion prefix')
            prefixes.append(prefix)
        else:
            raise ValueError('unreviewed scanner policy syntax')
    skip = {'.git', 'node_modules', '__pycache__', '.taskplane', '.tp-work', 'venv', '.venv',
            'dist', 'build', 'target', 'vendor', '.tox', '.mypy_cache', '.pytest_cache', '.eval',
            '.em-review', '.security-review'}
    excluded, expected = {}, []
    for name in source:
        reason = next((f'components.yaml:{prefix}' for prefix in prefixes if name == prefix or name.startswith(prefix + '/')), None)
        if not reason:
            reason = next((f'scanner-skip-directory:{part}' for part in Path(name).parts[:-1]
                           if part in skip or part.startswith('.tp-')), None)
        if name.startswith('knowledge/'):
            reason = 'scanner-knowledge-directory'
        if reason:
            excluded[name] = reason
        else:
            expected.append(name)
    return {'expected_files': sorted(expected), 'excluded_paths_and_rules': excluded,
            'declared_prefixes': prefixes, 'policy_sha256': hashlib.sha256(policy_raw.encode()).hexdigest()}

def graph_evidence_verdict(graph: dict, source: dict, policy_raw: str = '') -> dict:
    problems = []
    meta = graph.get('meta', {})
    quality, coverage, inputs = (meta.get(name, {}) for name in ('graph_scan_quality', 'source_coverage', 'source_inputs'))
    canonical = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    if meta.get('schema') != 2 or not graph.get('modules') or not graph.get('components'):
        problems.append('missing actual graph module/component schema')
    if (quality.get('schema') != 'taskplane.graph-scan-quality/v1' or quality.get('mode') != 'components'
            or quality.get('degraded') is not False or quality.get('failures') != []
            or quality.get('fingerprint') != canonical({k: v for k, v in quality.items() if k != 'fingerprint'})):
        problems.append('degraded/malformed/unbound strict graph quality')
    for producer in ('base-scanner', 'decomposition'):
        producer_row = quality.get('producers', {}).get(producer, {})
        if producer_row.get('status') != 'complete' or producer_row.get('failures') != []:
            problems.append(f'incomplete graph producer: {producer}')
    source_tree = canonical({'files': {p: row.get('hash', '') for p, row in graph.get('files', {}).items()},
                             'recorded': graph.get('recorded', [])})
    if (coverage.get('schema') != 'taskplane.source-touchpoint-coverage/v1'
            or coverage.get('producer') != 'taskplane/graph_decomposition.py'
            or coverage.get('status') != 'complete' or coverage.get('complete') is not True
            or coverage.get('stopping_conditions') != [] or coverage.get('source_tree') != source_tree
            or meta.get('source_tree') != source_tree
            or coverage.get('fingerprint') != canonical({k: v for k, v in coverage.items() if k != 'fingerprint'})):
        problems.append('missing/incomplete/foreign source touchpoint coverage')
    if set(coverage.get('touchpoints', {})) != {'source:files', 'source:modules', 'source:symbols', 'source:configuration', 'source:contracts', 'source:runtime'}:
        problems.append('incomplete source touchpoint inventory')
    inventory = scanner_inventory(source, policy_raw)
    if source.get('components.yaml', {}).get('sha256') != inventory['policy_sha256']:
        problems.append('scanner exclusion policy is not pinned to candidate bytes')
    if (inputs.get('schema') != 'taskplane.source-scan-inputs/v1' or inputs.get('exclude_error')
            or set(inputs.get('files', [])) != set(inventory['expected_files'])
            or inputs.get('excludes') != inventory['declared_prefixes'] or not inputs.get('content')):
        problems.append('scanner inventory differs from independently derived pinned exclusion policy')
    for name, sha256 in inputs.get('content', {}).items():
        if source.get(name, {}).get('sha256') != sha256:
            problems.append(f'graph/source SHA256 mismatch: {name}')
    return {'status': 'fail' if problems else 'pass', 'problems': problems,
            'source_inputs': inputs, 'quality': quality, 'coverage': coverage, 'scanner_inventory': inventory}


def audit_obligation_dispositions(invocation: Path, ledger_raw: bytes, plan_path: Path) -> dict:
    result = {'status': 'fail', 'problems': [], 'scope': 'Disposition completeness, not delivery closure'}
    problems = result['problems']
    try:
        ledger = json.loads(ledger_raw)
        plan = json.loads(plan_path.read_bytes())
        tasks = plan['task_dag']
        residuals = {row['id']: row for row in plan['residual_obligations']['categories']}
        owners = {task['id'] for task in tasks}
        entries = ledger['entries']
        matches = [entry for entry in entries if entry.get('id') == 'BUILD-FINAL-OBLIGATION-DISPOSITIONS']
        if not matches:
            problems.append('missing root final obligation dispositions')
            return result
        entry = matches[-1]
        result['entry'] = entry
        if entry.get('schema') != 'taskplane.remediation-obligation-dispositions/v1':
            problems.append('obligation schema mismatch')
        if set(entry.get('criteria', {})) != set(plan['criteria']) or set(entry.get('residuals', {})) != set(residuals):
            problems.append('expected every accepted functional criterion and later-stage obligation')
        references = {item['id']: item for item in entries if item.get('id')}
        valid_dependencies = set(plan['criteria']) | set(residuals) | owners
        classes = {'historical_report', 'source_inspection', 'fixture_execution', 'platform_ci', 'native_execution', 'package_identity', 'unresolved'}
        for key, row in {**entry.get('criteria', {}), **entry.get('residuals', {})}.items():
            if row.get('owner') not in owners:
                problems.append(f'{key}: owner must name a concrete accepted Plan task (external responsibility stays with its integration owner)')
            if (not isinstance(row.get('depends_on'), list) or not set(row['depends_on']) <= valid_dependencies
                    or row.get('evidence_class') not in classes
                    or row.get('disposition') not in {'pass', 'fail', 'blocked', 'unknown'}):
                problems.append(f'{key}: malformed dependency/class/disposition')
            residual = residuals.get(key)
            if residual and not set(residual.get('depends_on', [])) <= set(row.get('depends_on', [])):
                problems.append(f'{key}: accepted dependencies omitted')
            if not row.get('limits') or not row.get('next_action'):
                problems.append(f'{key}: missing concrete limitation/next action')
            observations = row.get('observations', [])
            if not isinstance(observations, list) or not observations:
                problems.append(f'{key}: missing actual observations')
                continue
            if row.get('disposition') == 'pass' and row.get('evidence_class') == 'unresolved':
                problems.append(f'{key}: unresolved evidence cannot be a passing disposition')
            for observation in observations:
                if isinstance(observation, str):
                    if observation not in references or observation == entry['id']:
                        problems.append(f'{key}: absent/self-referential ledger observation')
                elif isinstance(observation, dict) and observation.get('ledger_entry'):
                    if observation['ledger_entry'] not in references or observation['ledger_entry'] == entry['id']:
                        problems.append(f'{key}: missing ledger entry')
                elif isinstance(observation, dict) and observation.get('path'):
                    path = safe_path(invocation, observation['path'])
                    if (not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != observation.get('sha256')
                            or not (observation.get('command') or observation.get('observation'))):
                        problems.append(f'{key}: artifact observation missing/drifted')
                else:
                    problems.append(f'{key}: malformed observation')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        problems.append(f'malformed obligation dispositions: {exc}')
    result['status'] = 'fail' if problems else 'pass'
    return result


AUDIT_NAMES = {'canary', 'ci-definition', 'historical-ci', 'source-identity',
               'producer-reconciliation', 'obligation-dispositions', 'packages', 'candidate-graph',
               'exact-once-partition', 'quality', 'execution-profiles', 'broad-and-quality',
               'release-preservation'}


def registry_binding_problems(strategy: dict, bindings: dict) -> list[str]:
    specs = {spec['id']: spec for spec in strategy['required_local_subchecks']}
    expected = {f'{key}:{number}' for key, spec in specs.items() for number in range(1, len(spec['assertions']) + 1)}
    problems = []
    if not isinstance(bindings, dict) or set(bindings) != expected:
        problems.append('registry must contain every exact accepted assertion key and no extra keys')
    if not isinstance(bindings, dict):
        return problems
    all_tests = {name for spec in specs.values() for name in spec.get('test_modules', [])}
    for key, selectors in bindings.items():
        spec = specs.get(key.rsplit(':', 1)[0], {})
        allowed_tests = set(spec.get('test_modules', [])) or all_tests
        if (not isinstance(selectors, list) or not selectors
                or any(not isinstance(selector, str) for selector in selectors)
                or len(selectors) != len(set(selectors))):
            problems.append(f'{key}: missing/duplicate/malformed executable binding')
            continue
        for selector in selectors:
            if selector.startswith('audit:'):
                if selector[6:] not in AUDIT_NAMES:
                    problems.append(f'{key}: unknown executable audit')
            else:
                module, separator, name = selector.partition('::')
                if module not in allowed_tests or not separator or not name or '\\' in selector or '..' in Path(module).parts:
                    problems.append(f'{key}: binding outside declared test-module inputs')
    return problems


def audit_scenario_registry(ledger_raw: bytes, strategy: dict, manifest_sha256: str,
                            strategy_sha256: str, source: dict) -> dict:
    result = {'status': 'fail', 'problems': []}
    problems = result['problems']
    try:
        entries = json.loads(ledger_raw)['entries']
        matches = [entry for entry in entries if entry.get('id') == 'BUILD-FINAL-SCENARIO-REGISTRY']
        if not matches:
            problems.append('missing root final reviewed scenario registry')
            return result
        entry = matches[-1]
        result['entry'] = entry
        reconciliations = [e for e in entries if e.get('id') == 'BUILD-FINAL-PRODUCER-RECONCILIATION']
        reviewers = {r.get('reviewer') for r in reconciliations[-1].get('task_results', {}).values()} if reconciliations else set()
        if (entry.get('schema') != 'taskplane.remediation-scenario-registry/v1'
                or entry.get('source_manifest_sha256') != manifest_sha256
                or entry.get('verification_strategy_sha256') != strategy_sha256
                or entry.get('candidate_map_sha256') != digest(source)
                or not entry.get('reviewer') or reviewers != {entry['reviewer']}
                or not isinstance(entry.get('review_references'), list) or not entry['review_references']
                or any(not isinstance(ref, str) or not ref.strip() for ref in entry['review_references'])):
            problems.append('final scenario registry binding/reviewer/reference mismatch')
        bindings = entry['bindings']
        problems.extend(registry_binding_problems(strategy, bindings))
        result['bindings'] = bindings
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        problems.append(f'malformed final registry: {exc}')
    result['status'] = 'fail' if problems else 'pass'
    return result

def audit_packages(log: ExecutionLog, source: dict) -> dict:
    archives, executions, problems = [], [], []
    for host in ('openai', 'claude'):
        for repeat in range(2):
            output = log.output / f'package-{host}-{repeat}'
            record = log.run([sys.executable, f'scripts/package_{host}.py', '--output-dir', str(output)], label=f'package/{host}/{repeat}')
            executions.append(record['id'])
            if record['status'] != 'pass':
                problems.append(f'{host}/{repeat}: package producer failed')
                continue
            found = list(output.glob('*.zip')) + list(output.glob('*.plugin'))
            if len(found) != 1:
                problems.append(f'{host}/{repeat}: expected one archive')
                continue
            path = found[0]
            members = {}
            with zipfile.ZipFile(path) as archive:
                for entry in archive.infolist():
                    name = entry.filename
                    member = Path(name)
                    if member.is_absolute() or '..' in member.parts or '\\' in name or name in members or stat.S_ISLNK(entry.external_attr >> 16):
                        problems.append(f'unsafe/duplicate archive member: {name}')
                        continue
                    data = archive.read(entry)
                    value = hashlib.sha256(data).hexdigest()
                    members[name] = {'sha256': value, 'bytes': len(data), 'mode': entry.external_attr >> 16}
                    if source.get(name, {}).get('sha256') != value:
                        problems.append(f'archive/source parity mismatch: {name}')
                    if name.startswith(('taskplane/tests/', '.codex/', '.taskplane/')):
                        problems.append(f'excluded member included: {name}')
                manifest_name = ('.codex-plugin' if host == 'openai' else '.claude-plugin') + '/plugin.json'
                required = [manifest_name, 'hooks/hooks.json', 'taskplane/tp.py']
                if any(name not in members for name in required):
                    problems.append(f'{host}: missing manifest/hook/runtime identity')
                identities = {name: archive.read(name).decode('utf-8') for name in required if name in members and name.endswith('.json')}
            archives.append({'host': host, 'repeat': repeat, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                             'members': members, 'identities': identities})
        pair = [row for row in archives if row['host'] == host]
        if len(pair) != 2 or pair[0]['sha256'] != pair[1]['sha256'] or pair[0]['members'] != pair[1]['members']:
            problems.append(f'{host}: deterministic pair missing/mismatched')
    return {'status': 'fail' if problems else 'pass', 'problems': problems, 'executions': executions, 'archives': archives}


def audit_candidate_graph(log: ExecutionLog, source: dict) -> dict:
    copied = log.output / 'graph-source'
    shutil.copytree(log.root, copied, symlinks=True)
    copy_map = tree_map(copied)
    if copy_map != source:
        return {'status': 'fail', 'reason': 'disposable graph source copy differs', 'copy': copy_map}
    record = log.run([sys.executable, 'taskplane/tp.py', 'graph', '--workspace', str(copied), 'scan', '--decompose', '--strict'],
                     cwd=copied, label='candidate-graph')
    target = copied / '.taskplane/knowledge/graph.json'
    result = {'status': record['status'], 'execution': record['id'], 'scan_root': str(copied),
              'source_map_sha256': digest(source), 'copy_map_sha256': digest(copy_map)}
    if not target.is_file():
        result.update(status='fail', reason='missing graph output')
    else:
        raw = target.read_text(encoding='utf-8')
        result['graph_raw'] = raw
        # Preserve the public scanner's full quality/decomposition data; a zero
        # exit without its graph is insufficient. Integration reviews coverage.
        try:
            graph = json.loads(raw)
            result['graph'] = graph
            result['verification'] = graph_evidence_verdict(graph, source, (copied / 'components.yaml').read_text(encoding='utf-8'))
            if result['verification']['status'] != 'pass':
                result['status'] = 'fail'
            copied_after = {k: v for k, v in tree_map(copied).items() if not k.startswith('.taskplane/')}
            if copied_after != source:
                result.update(status='fail', reason='graph execution changed copied source')
        except ValueError:
            result.update(status='fail', reason='malformed graph')
    return result


def audit_ci_definition(root: Path) -> dict:
    ci = (root / '.github/workflows/ci.yml').read_text()
    capacity = (root / '.github/workflows/capacity.yml').read_text()
    problems = []
    if 'timeout-minutes: 15' not in ci or '20 || 15' not in ci:
        problems.append('hard CI timeout contract missing')
    if 'windows-latest' not in ci or '--suite portability' not in ci or '--suite packages --host' not in ci:
        problems.append('representative Windows functional coverage missing')
    if any(value in ci for value in ('--suite capacity', 'benchmark_context.py', 'benchmark_usage.py')):
        problems.append('performance workload is a required CI dependency')
    if 'schedule:' in capacity or '--suite capacity' in capacity or '    if: false\n' not in capacity:
        problems.append('historical capacity workflow must remain disabled')
    if '--report-json' not in ci:
        problems.append('raw profile artifact capture missing')
    if 'if: always()' not in ci:
        problems.append('failed-run artifacts are not retained')
    return {'status': 'fail' if problems else 'pass', 'problems': problems,
            'ci_sha256': hashlib.sha256(ci.encode()).hexdigest(),
            'capacity_sha256': hashlib.sha256(capacity.encode()).hexdigest(),
            'later_gate': 'WIN-01: representative final-candidate functional Windows execution with complete partition and raw evidence; not certified locally.'}


def audit_historical_ci(invocation: Path) -> dict:
    # Read-only evidence, never needed to execute source. Absence is explicit.
    directory = invocation.parent.parent / '2026-10-11/task/host-audit/release-2.33.9'
    expected = '3dfb13d0709ba3d94c0c41f6fc391c1c57238bf771dda9080fa262913689d238'
    files = {}
    for name in ('main-windows-core4-attempt1.log', 'main-ci-attempt1.json', 'main-ci.json', 'pr-ci.json'):
        path = directory / name
        if path.is_file():
            data = path.read_bytes()
            files[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                           'base64': base64.b64encode(data).decode('ascii'),
                           'raw': data.decode('utf-8', errors='replace')}
    ok = len(files) == 4 and files['main-windows-core4-attempt1.log']['sha256'] == expected
    return {'status': 'pass' if ok else 'fail', 'files': files,
            'cause': 'unknown: overlapping shared test timings differ; variability is not a demonstrated cause',
            'github_run': 38403885257, 'artifacts': [11818658038, 11819871880]}


def required_coverage(spec: dict, records: list[dict], audits: dict, registry=None) -> dict:
    registry = SCENARIO_REGISTRY if registry is None else registry
    results = []
    for number, assertion in enumerate(spec['assertions'], 1):
        key = f"{spec['id']}:{number}"
        bindings = registry.get(key, [])
        missing, observed = [], []
        if not bindings:
            missing.append('no reviewed executable assertion binding')
        for binding in bindings:
            if binding.startswith('audit:'):
                audit = audits.get(binding[6:], {})
                observed.append({'audit': binding, 'status': audit.get('status', 'missing')})
                if audit.get('status') != 'pass':
                    missing.append(binding)
                continue
            matches = []
            for record in records:
                if not record['label'].startswith('targeted/'):
                    continue
                profile = record.get('profile', {})
                candidate_nodes = ([row['nodeid'] for row in record['collection']] if record.get('collection')
                                   else profile.get('selected_nodeids', []))
                for node in candidate_nodes:
                    if node == binding or node.startswith(binding + '['):
                        phases = [row for row in profile['reports'] if row['nodeid'] == node]
                        enclosing = [row for row in phases if not is_subtest_report(row)]
                        passed = (record['status'] == 'pass' and node in profile.get('selected_nodeids', [])
                                  and not any(invalid_subtest_report(p) for p in phases)
                                  and Counter(p['when'] for p in enclosing) == Counter({'setup': 1, 'call': 1, 'teardown': 1})
                                  and all(p['outcome'] == 'passed' and p.get('wasxfail') is None for p in phases))
                        matches.append({'nodeid': node, 'execution': record['id'], 'passed': passed})
            observed += matches
            if not matches or not all(row['passed'] for row in matches):
                missing.append(binding)
        results.append({'scenario': key, 'expected_assertion': assertion, 'bindings': bindings,
                        'observed': observed, 'missing': missing, 'status': 'fail' if missing else 'pass'})
    return {'status': 'pass' if all(row['status'] == 'pass' for row in results) else 'fail', 'scenarios': results}


def run_audit(function, *args) -> dict:
    """Keep an independent audit failure from suppressing later safe checks."""
    try:
        return function(*args)
    except Exception as exc:
        return {'status': 'fail', 'problems': [f'{type(exc).__name__}: {exc}'],
                'traceback': traceback.format_exc()}

def pytest_command(paths: list[str]) -> list[str]:
    return [sys.executable, '-m', 'pytest', *paths, '-q']


def normalized_source_inventory(files: dict) -> dict:
    """Canonical identity shared by the preserved baseline and final source map."""
    if not isinstance(files, dict) or not files:
        raise ValueError('empty/malformed source inventory')
    result = {}
    for name, value in files.items():
        safe_path(ROOT, name)
        mode = value['mode']
        if isinstance(mode, str):
            mode = int(mode, 8)
        if (value.get('kind', 'file') != 'file' or not re.fullmatch(r'[0-9a-f]{64}', value['sha256'])
                or type(value['bytes']) is not int or value['bytes'] < 0
                or type(mode) is not int or not 0 <= mode <= 0o7777):
            raise ValueError(f'invalid regular-file identity: {name}')
        result[name] = {'kind': 'file', 'sha256': value['sha256'], 'bytes': value['bytes'],
                        'mode': format(mode, '04o')}
    return dict(sorted(result.items()))


def bound_release_reference(reference: dict, invocation: Path, inputs: dict) -> bytes:
    name, expected = reference['path'], reference['sha256']
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError(f'invalid evidence digest: {name}')
    path = safe_path(invocation, name)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f'bound release evidence drift: {name}')
    if name in inputs and inputs[name] != expected:
        raise ValueError(f'conflicting evidence bindings: {name}')
    inputs[name] = expected
    return raw


def release_review_references(record: dict, invocation: Path, inputs: dict) -> None:
    if not isinstance(record.get('reviewer'), str) or not record['reviewer'].strip():
        raise ValueError('missing source reviewer')
    references = record.get('review_references')
    if not isinstance(references, list) or not references:
        raise ValueError('missing hash-bound review evidence')
    for reference in references:
        bound_release_reference(reference, invocation, inputs)


def audit_release_preservation(manifest: dict, invocation: Path, before: dict, after: dict) -> dict:
    """Review accounting is local evidence, never a replacement native receipt."""
    result = {'status': 'fail', 'problems': [], 'inputs': {},
              'authority': 'Explicit reviewed repair identity; no workflow approval, native acceptance, installation or publication.'}
    problems, inputs = result['problems'], result['inputs']
    try:
        if manifest['schema'] != 'taskplane.release-source-manifest/v1':
            raise ValueError('release manifest schema mismatch')
        if safe_path(invocation, manifest['candidate_root']).resolve() != ROOT.resolve():
            raise ValueError('release manifest cannot select another candidate root')
        configuration = safe_path(invocation, 'components.yaml').read_bytes()
        if configuration != b'exclude:\n  - work/runtime-activation-v5\n':
            raise ValueError('accepted workspace graph exclusion configuration changed')
        inputs['components.yaml'] = hashlib.sha256(configuration).hexdigest()
        if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', manifest['release']):
            raise ValueError('release manifest needs the candidate version')
        for host in ('.codex-plugin', '.claude-plugin'):
            metadata = json.loads(safe_path(ROOT, host + '/plugin.json').read_bytes())
            if metadata.get('version') != manifest['release']:
                raise ValueError(f'candidate version differs from release manifest: {host}')
        baseline = json.loads(bound_release_reference(manifest['baseline']['inventory'], invocation, inputs))
        original = normalized_source_inventory(baseline['files'])
        final = normalized_source_inventory(manifest['files'])
        if final != manifest['files']:
            raise ValueError('final files must use the canonical tree_map identity format')
        archive_raw = bound_release_reference(manifest['baseline']['archive'], invocation, inputs)
        if baseline['count'] != len(original) or baseline['archive_sha256'] != hashlib.sha256(archive_raw).hexdigest():
            raise ValueError('baseline inventory/archive binding mismatch')
        # ZIP bytes are retained historical evidence. Inspect them without extraction.
        archived = {}
        with zipfile.ZipFile(io.BytesIO(archive_raw)) as archive:
            for entry in archive.infolist():
                name = entry.filename
                safe_path(ROOT, name)
                if name in archived or '\\' in name or not stat.S_ISREG(entry.external_attr >> 16):
                    raise ValueError(f'unsafe/duplicate/nonregular baseline member: {name}')
                data = archive.read(entry)
                archived[name] = {'kind': 'file', 'sha256': hashlib.sha256(data).hexdigest(),
                                  'bytes': len(data), 'mode': format(stat.S_IMODE(entry.external_attr >> 16), '04o')}
        if archived != original:
            raise ValueError('retained baseline archive differs from original inventory')
        if before != final or after != final:
            problems.append('final candidate census/bytes/modes differ from bound manifest or changed during execution')
        if not set(original) <= set(final):
            problems.append('original source files were removed')
        expected_changes = {name for name in final if final[name] != original.get(name)}
        changes = manifest['changes']
        if not isinstance(changes, list) or len(changes) != len(expected_changes) or {row['path'] for row in changes} != expected_changes:
            raise ValueError('every changed/new path needs one exact reviewed change record')
        for row in changes:
            name = row['path']
            if row.get('before') != original.get(name) or row.get('after') != final[name]:
                problems.append(f'reviewed before/after identity mismatch: {name}')
            if not isinstance(row.get('reason'), str) or not row['reason'].strip():
                problems.append(f'missing repair reason: {name}')
            release_review_references(row, invocation, inputs)
        artifacts = manifest['retained_artifacts']
        roles = {row['role'] for row in artifacts}
        if not {'prior-package', 'maintenance-provenance', 'historical-manifest'} <= roles:
            problems.append('prior packages, maintenance provenance and historical manifest must remain retained')
        if {row.get('host') for row in artifacts if row['role'] == 'prior-package'} != {'openai', 'claude'}:
            problems.append('both historical host packages must remain retained')
        for row in artifacts:
            bound_release_reference(row, invocation, inputs)
        if baseline.get('original_archive_sha256') and not any(
                row['sha256'] == baseline['original_archive_sha256'] for row in artifacts):
            problems.append('original archive metadata evidence must remain retained')
        result.update(baseline_count=len(original), final_count=len(final),
                      workspace_configuration_sha256=inputs['components.yaml'],
                      baseline_map_sha256=digest(original), candidate_map_sha256=digest(final),
                      changed=sorted(name for name in expected_changes if name in original),
                      added=sorted(set(final) - set(original)), unchanged=sorted(set(final) - expected_changes))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile) as exc:
        problems.append(f'malformed release preservation: {exc}')
    result['status'] = 'fail' if problems else 'pass'
    return result


def load_release_manifest(path: Path, invocation: Path) -> tuple[dict, dict, dict]:
    path = safe_path(invocation, path.relative_to(invocation).as_posix())
    if path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('release manifest must be outside candidate source')
    raw = path.read_bytes()
    manifest = json.loads(raw)
    inputs = {path.relative_to(invocation).as_posix(): hashlib.sha256(raw).hexdigest()}
    strategy = normalize_functional_strategy(json.loads(bound_release_reference(manifest['strategy'], invocation, inputs)))
    registry = manifest['scenario_registry']
    release_review_references(registry, invocation, inputs)
    problems = registry_binding_problems(strategy, registry['bindings'])
    # A retired-run release must not relabel source review as native producer acceptance.
    if any(selector == 'audit:producer-reconciliation' for selectors in registry['bindings'].values() for selector in selectors):
        problems.append('direct release requires release-preservation, not retired-run producer acceptance')
    if problems:
        raise ValueError('; '.join(problems))
    gates = manifest['external_gates']
    if not isinstance(gates, list) or len(gates) != len(strategy.get('later_stage_gates', [])) or {gate['id'] for gate in gates} != {gate['id'] for gate in strategy.get('later_stage_gates', [])}:
        raise ValueError('release must retain all six external gate dispositions')
    for gate in gates:
        if gate.get('status') not in ('unknown', 'unsupported', 'pass', 'fail') or any(
                not isinstance(gate.get(key), str) or not gate[key].strip() for key in ('owner', 'reason', 'next_action')):
            raise ValueError(f'incomplete external gate disposition: {gate.get("id")}')
        evidence = gate.get('evidence')
        if not isinstance(evidence, list) or (gate['status'] == 'pass' and not evidence):
            raise ValueError('passing external claims need bound actual evidence')
        for reference in evidence:
            bound_release_reference(reference, invocation, inputs)
    reader = manifest['legacy_reader']
    release_review_references(reader, invocation, inputs)
    reader_root = safe_path(invocation, reader['path'])
    if reader_root.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('legacy reader must be an independently retained historical runtime')
    expected_reader = normalized_source_inventory(reader['files'])
    if expected_reader != reader['files'] or tree_map(reader_root) != expected_reader:
        raise ValueError('legacy reader full identity mismatch')
    if 'taskplane/workflow_host.py' not in expected_reader:
        raise ValueError('legacy reader runtime entry point missing')
    return manifest, strategy, inputs


def execute_functional_checks(log: ExecutionLog, strategy: dict, before: dict, audits: dict) -> None:
    """Shared mandatory functional execution for governed and direct release runs."""
    collection = log.run(pytest_command(['taskplane/tests']) + ['--collect-only'],
                         label='inventory/complete', pytest_run=True, collect=True)
    rows = collection.get('collection', []) if collection['status'] == 'pass' else []
    for spec in strategy['required_local_subchecks']:
        modules = spec.get('test_modules', [])
        if modules:
            missing = [name for name in spec.get('source_modules', []) + modules if not (ROOT / name).is_file()]
            if not missing:
                log.run(pytest_command(modules), label=f"targeted/{spec['id']}", pytest_run=True)
    audits['packages'] = run_audit(audit_packages, log, before)
    audits['candidate-graph'] = run_audit(audit_candidate_graph, log, before)
    actual = {}
    for partition, nodes in matrix_selections(rows).items():
        parts = partition.split('/')
        suite = parts[0]
        # Use the public suite/host/shard filters, with explicit
        # hostless paths so filtered host jobs cannot drop them.
        command = pytest_command(nodes if parts[-1] == 'hostless' else ['taskplane/tests'])
        if suite == 'browser':
            command = pytest_command(['taskplane/tests/test_dashboard_browser.py'])
        else:
            command += ['--taskplane-suite', suite]
        if suite == 'packages' and parts[-1] != 'hostless':
            command += ['--taskplane-host', parts[-1]]
        if suite in ('core', 'native'):
            index, count = parts[-1].split('of')
            command += ['--taskplane-shard-index', index, '--taskplane-shard-count', count]
        record = log.run(command, label=f'broad/{partition}', pytest_run=True)
        actual[partition] = record.get('profile', {}).get('selected_nodeids', [])
    audits['exact-once-partition'] = verify_partition(rows, actual)
    quality = []
    for command in ([sys.executable, '-m', 'ruff', 'check', '--no-cache', 'taskplane', 'scripts'],
                    [sys.executable, '-m', 'mypy'], [sys.executable, 'taskplane/tp.py', 'version', '--verify']):
        quality.append(log.run(command, label='quality')['id'])
    audits['quality'] = {'status': 'pass' if all(log.records[i]['status'] == 'pass' for i in quality) else 'fail', 'executions': quality}


def remediation(path: Path, invocation: Path) -> int:
    report = {'schema': 'taskplane.remediation-execution/v1', 'status': 'fail',
              'invocation_cwd': str(invocation), 'manifest_path': str(path),
              'subchecks': [], 'executions': [], 'audits': {},
              'reuse': 'none: every execution is current; semantic/native acceptance is separate',
              'later_gates': 'Actual Windows matrices, native lifecycle/restart, publication, and future monitor wake remain external.'}
    try:
        manifest, strategy = load_manifest(path, invocation)
        paths = remediation_paths(path, invocation)
        report['manifest_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        report['functional_contract'] = {'groups': sorted(strategy_groups(strategy)),
            'criteria': sorted(strategy_criteria(strategy)),
            'limits': 'Configured retry safety and operational process timeouts remain; no performance acceptance target.'}
        report['verification_strategy_sha256'] = hashlib.sha256(paths['strategy'].read_bytes()).hexdigest()
        ledger_path = paths['ledger']
        plan_raw = paths['plan'].read_bytes()
        input_digests = {str(path): report['manifest_sha256'],
                        str(paths['strategy']): report['verification_strategy_sha256'],
                        str(paths['plan']): hashlib.sha256(plan_raw).hexdigest()}
        ledger_before = ledger_path.read_bytes() if ledger_path.is_file() else b'{}'
        before = tree_map(ROOT)
        initial_identity = audit_source(manifest, before, before)
        with tempfile.TemporaryDirectory(prefix='taskplane-remediation-') as temp:
            log = ExecutionLog(Path(temp), ROOT)
            report['executions'] = log.records
            audits = report['audits']
            audits['source-identity-preflight'] = initial_identity
            audits['producer-reconciliation-preflight'] = run_audit(audit_producer_reconciliation, manifest, report['manifest_sha256'], before, before, ledger_before, ledger_before, plan_raw, invocation)
            audits['scenario-registry'] = run_audit(audit_scenario_registry, ledger_before, strategy, report['manifest_sha256'], report['verification_strategy_sha256'], before)
            registry = audits['scenario-registry'].get('bindings', SCENARIO_REGISTRY) if audits['scenario-registry']['status'] == 'pass' else SCENARIO_REGISTRY
            canary = log.run([sys.executable, '-m', 'unittest',
                             'taskplane.tests.test_runner_isolation.TestUnittestRunnerIsolation', '-v'],
                            label='canary/unittest')
            # The exact documented command is standalone; unittest's zero tests
            # can exit zero, so require its explicit nonzero summary as well.
            audits['canary'] = canary_verdict(canary)
            audits['ci-definition'] = run_audit(audit_ci_definition, ROOT)
            audits['historical-ci'] = run_audit(audit_historical_ci, invocation)
            preconditions = ('source-identity-preflight', 'producer-reconciliation-preflight', 'scenario-registry', 'canary')
            ready = all(audits[name]['status'] == 'pass' for name in preconditions)
            if ready:
                execute_functional_checks(log, strategy, before, audits)
            else:
                report['dependency_deferral'] = {'reason': 'Complete source identity, accepted producer reconciliation, final scenario registry and passing canary required before candidate imports or execution',
                    'failed_preconditions': {name: audits[name] for name in preconditions if audits[name]['status'] != 'pass'}}
            after = tree_map(ROOT)
            audits['source-identity'] = audit_source(manifest, before, after)
            ledger_after = ledger_path.read_bytes() if ledger_path.is_file() else b'{}'
            audits['producer-reconciliation'] = run_audit(audit_producer_reconciliation, manifest, report['manifest_sha256'], before, after, ledger_before, ledger_after, plan_raw, invocation)
            audits['obligation-dispositions'] = run_audit(audit_obligation_dispositions, invocation, ledger_before, paths['plan'])
            changed_inputs = [name for name, sha in input_digests.items()
                              if not Path(name).is_file() or hashlib.sha256(Path(name).read_bytes()).hexdigest() != sha]
            audits['accepted-input-identity'] = {'status': 'fail' if changed_inputs else 'pass',
                                                  'digests': input_digests, 'changed_inputs': changed_inputs}
            profiles = [r for r in log.records if r['label'].startswith(('targeted/', 'broad/'))]
            audits['execution-profiles'] = {'status': 'pass' if profiles and all('profile' in r and 'junit.xml' in r for r in profiles) else 'fail',
                                             'executions': [r['id'] for r in profiles],
                                             'overlap_policy': 'Targeted and broad runs are distinct; never add their test counts.'}
            broad = [r for r in log.records if r['label'].startswith('broad/')]
            audits['broad-and-quality'] = {'status': 'pass' if broad and all(r['status'] == 'pass' for r in broad)
                and all(audits.get(name, {}).get('status') == 'pass' for name in ('exact-once-partition', 'quality', 'packages')) else 'fail',
                'executions': [r['id'] for r in broad]}
            for spec in strategy['required_local_subchecks']:
                coverage = required_coverage(spec, log.records, audits, registry)
                subcheck = {'id': spec['id'], 'required': spec['required'], 'criteria': spec['criteria'],
                            'status': coverage['status'], 'coverage': coverage,
                            'executions': [r['id'] for r in log.records if r['label'] == 'targeted/' + spec['id']]}
                if not ready:
                    subcheck.update(status='fail', deferred_by=list(report['dependency_deferral']['failed_preconditions']))
                report['subchecks'].append(subcheck)
            report['executions'] = log.records
            report['environment_binding'] = {'received_allowlist': {k: os.environ[k] for k in SAFE_ENV if k in os.environ},
                                             'browser_override_received': bool(os.environ.get('TASKPLANE_BROWSER_EXECUTABLE'))}
            report['status'] = 'pass' if ({s['id'] for s in report['subchecks']} == strategy_groups(strategy) and all(s['status'] == 'pass' for s in report['subchecks'])
                                          and all(a['status'] == 'pass' for a in audits.values())
                                          and all(r['status'] == 'pass' for r in log.records)) else 'fail'
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        report['fatal'] = f'{type(exc).__name__}: {exc}'
    print(_json_bytes(pack_evidence(report)).decode('utf-8'), flush=True)
    return 0 if report['status'] == 'pass' else 1


def release_verification(path: Path, invocation: Path) -> int:
    """Verify a reviewed repair without inventing continuation of a retired run."""
    report = {'schema': 'taskplane.release-execution/v1', 'status': 'fail', 'local_status': 'fail',
              'release_status': 'incomplete', 'manifest_path': str(path), 'invocation_cwd': str(invocation),
              'subchecks': [], 'executions': [], 'audits': {},
              'scope': 'Local functional verification only. External gate evidence, installation and publication remain separate.',
              'reuse': 'none; every execution is current and bound to the final manifest'}
    try:
        manifest, strategy, inputs = load_release_manifest(path, invocation)
        report.update(manifest_sha256=inputs[path.relative_to(invocation).as_posix()],
                      verification_strategy_sha256=manifest['strategy']['sha256'],
                      release=manifest['release'], external_gates=manifest['external_gates'],
                      functional_contract={'groups': sorted(strategy_groups(strategy)), 'criteria': sorted(strategy_criteria(strategy)),
                                           'assertions': sum(len(spec['assertions']) for spec in strategy['required_local_subchecks']),
                                           'coverage': 'Every selected retained test is executed; no historical exclusion list.'})
        before = tree_map(ROOT)
        reader_root = safe_path(invocation, manifest['legacy_reader']['path'])
        reader_before = tree_map(reader_root)
        reader_fingerprint = digest(reader_before)
        report['candidate_map_sha256'] = digest(before)
        report['legacy_reader'] = {'path': manifest['legacy_reader']['path'],
                                  'files': reader_before, 'fingerprint': reader_fingerprint,
                                  'authority': 'Retained historical reader input; inability to read v3 is verified by the actual required test.'}
        audits = report['audits']
        audits['legacy-reader-preflight'] = {'status': 'pass' if reader_before == manifest['legacy_reader']['files'] else 'fail',
                                            'fingerprint': reader_fingerprint}
        audits['obligation-dispositions'] = {'status': 'pass', 'criteria': sorted(strategy_criteria(strategy)),
            'external_gates': manifest['external_gates'],
            'scope': 'Validated disposition completeness only; unknown/unsupported external obligations remain unverified.'}
        audits['release-preservation'] = audit_release_preservation(manifest, invocation, before, before)
        inputs.update(audits['release-preservation']['inputs'])
        registry = manifest['scenario_registry']['bindings']
        with tempfile.TemporaryDirectory(prefix='taskplane-release-') as temp:
            log = ExecutionLog(Path(temp), ROOT)
            report['executions'] = log.records
            # Refused identity/review evidence must not reach candidate imports.
            if all(audits[name]['status'] == 'pass' for name in ('release-preservation', 'legacy-reader-preflight')):
                canary = log.run([sys.executable, '-m', 'unittest',
                                 'taskplane.tests.test_runner_isolation.TestUnittestRunnerIsolation', '-v'], label='canary/unittest')
                audits['canary'] = canary_verdict(canary)
            else:
                audits['canary'] = {'status': 'fail', 'reason': 'release preservation preflight failed; candidate not executed'}
            ready = all(audits[name]['status'] == 'pass' for name in ('release-preservation', 'legacy-reader-preflight', 'canary'))
            if ready:
                # This explicit reviewed manifest input is never inherited from the shell.
                log.environment['TEST_TASKPLANE_LEGACY_RUNTIME'] = str(reader_root.resolve())
                log.bound_external_sources['legacy-reader'] = reader_root
            audits['ci-definition'] = run_audit(audit_ci_definition, ROOT)
            audits['historical-ci'] = run_audit(audit_historical_ci, invocation)
            if ready:
                execute_functional_checks(log, strategy, before, audits)
            else:
                report['dependency_deferral'] = 'Complete reviewed preservation evidence and a passing canary are required before candidate execution.'
            after = tree_map(ROOT)
            audits['source-identity'] = {'status': 'pass' if before == after == manifest['files'] else 'fail',
                'before': before, 'after': after, 'manifest_sha256': report['manifest_sha256']}
            audits['release-preservation-final'] = audit_release_preservation(manifest, invocation, before, after)
            changed_inputs = [name for name, sha in inputs.items() if not safe_path(invocation, name).is_file()
                              or hashlib.sha256(safe_path(invocation, name).read_bytes()).hexdigest() != sha]
            audits['accepted-input-identity'] = {'status': 'fail' if changed_inputs else 'pass',
                                               'digests': inputs, 'changed_inputs': changed_inputs}
            profiles = [record for record in log.records if record['label'].startswith(('targeted/', 'broad/'))]
            audits['execution-profiles'] = {'status': 'pass' if profiles and all('profile' in record and 'junit.xml' in record for record in profiles) else 'fail',
                'executions': [record['id'] for record in profiles], 'overlap_policy': 'Targeted and broad counts are distinct; never add them.'}
            broad = [record for record in log.records if record['label'].startswith('broad/')]
            audits['broad-and-quality'] = {'status': 'pass' if broad and all(record['status'] == 'pass' for record in broad)
                and all(audits.get(name, {}).get('status') == 'pass' for name in ('exact-once-partition', 'quality', 'packages')) else 'fail',
                'executions': [record['id'] for record in broad]}
            for spec in strategy['required_local_subchecks']:
                coverage = required_coverage(spec, log.records, audits, registry)
                report['subchecks'].append({'id': spec['id'], 'required': True, 'criteria': spec['criteria'],
                                           'status': coverage['status'], 'coverage': coverage})
            expected_fingerprint = digest(before)
            execution_fingerprint = digest(candidate_source_map(ROOT))
            audits['execution-source-binding'] = {'status': 'pass' if log.records and all(
                record.get('source_fingerprint_before') == execution_fingerprint
                and record.get('source_fingerprint_after') == execution_fingerprint for record in log.records) else 'fail',
                'candidate_map_sha256': expected_fingerprint, 'manifest_sha256': report['manifest_sha256']}
            reader_after = tree_map(reader_root)
            reader_executions = [record for record in log.records if record['label'] != 'canary/unittest']
            audits['legacy-reader-identity'] = {'status': 'pass' if reader_before == reader_after == manifest['legacy_reader']['files']
                and reader_executions and all(record.get('external_source_fingerprints_before') == {'legacy-reader': reader_fingerprint}
                    and record.get('external_source_fingerprints_after') == {'legacy-reader': reader_fingerprint}
                    for record in reader_executions) else 'fail',
                'before': reader_before, 'after': reader_after, 'fingerprint': reader_fingerprint}
            if 'packages' in audits:
                audits['packages'].update(source_manifest_sha256=report['manifest_sha256'],
                                          candidate_map_sha256=expected_fingerprint)
            passed = (all(row['status'] == 'pass' for row in report['subchecks'])
                      and all(row['status'] == 'pass' for row in audits.values())
                      and all(row['status'] == 'pass' for row in log.records))
            report['status'] = report['local_status'] = 'pass' if passed else 'fail'
            # Evidence bindings retain external claims; this local runner cannot certify them.
            report['external_gate_status'] = 'requires_external_review'
            report['release_status'] = 'pending_external_verification' if passed else 'local_verification_failed'
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile) as exc:
        report['fatal'] = f'{type(exc).__name__}: {exc}'
    print(_json_bytes(pack_evidence(report)).decode('utf-8'), flush=True)
    return 0 if report['local_status'] == 'pass' else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', action='store_true', help='Include the real-browser dashboard suite')
    parser.add_argument('--check', choices=['tests', 'quality', 'package', 'browser', 'remediation', 'release'])
    parser.add_argument('--remediation-manifest', type=Path)
    parser.add_argument('--release-manifest', type=Path)
    parser.add_argument('--report-json', type=Path, help='Persist actual execution/profile evidence outside source')
    parser.add_argument('--suite', choices=SUITES, default='all')
    parser.add_argument('--host', choices=['codex', 'claude', 'hostless'])
    parser.add_argument('--junitxml', type=Path)
    parser.add_argument('--package-output', type=Path)
    parser.add_argument('--shard-index', type=int)
    parser.add_argument('--shard-count', type=int)
    args = parser.parse_args()
    if args.suite != 'all' and args.check != 'tests':
        parser.error('--suite requires --check tests')
    if args.host and (args.check != 'tests' or args.suite not in ('packages', 'capacity')):
        parser.error('--host requires the packages or capacity test suite')
    if args.junitxml and args.check not in ('tests', 'browser'):
        parser.error('--junitxml requires --check tests or browser')
    if args.package_output and args.check not in (None, 'package'):
        parser.error('--package-output requires the package check')
    if (args.check == 'remediation') != bool(args.remediation_manifest):
        parser.error('--check remediation requires --remediation-manifest and vice versa')
    if (args.check == 'release') != bool(args.release_manifest):
        parser.error('--check release requires --release-manifest and vice versa')
    if args.check in ('remediation', 'release') and (args.browser or args.report_json):
        parser.error('remediation/release emits its complete record to stdout; no additional modes/output files')
    if args.shard_index is not None or args.shard_count is not None:
        if (args.check != 'tests' or args.shard_index is None or args.shard_count is None
                or not 1 <= args.shard_index <= args.shard_count):
            parser.error('test sharding requires --check tests and 1 <= --shard-index <= --shard-count')
    if args.report_json and (args.report_json.resolve().is_relative_to(ROOT.resolve())
                             or args.report_json.name in ('results.json', 'checks.json')):
        parser.error('--report-json must be outside candidate source and cannot name formal results.json/checks.json')
    if args.check == 'remediation':
        # Resolve at invocation cwd, before any candidate child changes cwd.
        return remediation(args.remediation_manifest.absolute(), Path.cwd().resolve())
    if args.check == 'release':
        return release_verification(args.release_manifest.absolute(), Path.cwd().resolve())
    checks = {
        'tests': [pytest_command(['taskplane/tests', '--ignore=taskplane/tests/test_dashboard_browser.py']) + ['--taskplane-suite', args.suite]],
        'quality': [[sys.executable, '-m', 'ruff', 'check', '--no-cache', 'taskplane', 'scripts'],
                    [sys.executable, '-m', 'mypy'], [sys.executable, 'taskplane/tp.py', 'version', '--verify']],
        'package': [[sys.executable, f'scripts/package_{host}.py'] for host in ('openai', 'claude')],
        'browser': [pytest_command(['taskplane/tests/test_dashboard_browser.py'])],
    }
    if args.host and args.host != 'hostless':
        checks['tests'][0] += ['--taskplane-host', args.host]
    if args.shard_count is not None:
        checks['tests'][0] += ['--taskplane-shard-index', str(args.shard_index), '--taskplane-shard-count', str(args.shard_count)]
    if args.junitxml:
        checks[args.check][0] += ['--junitxml', str(args.junitxml.resolve())]
    selected = [args.check] if args.check else ['tests', 'quality', 'package'] + (['browser'] if args.browser else [])
    with tempfile.TemporaryDirectory(prefix='taskplane-ci-') as temp:
        output = Path(temp)
        if args.report_json:
            target = args.report_json.resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            output = Path(tempfile.mkdtemp(prefix=target.stem + '-raw-', dir=target.parent))
        log = ExecutionLog(output, ROOT, live_output=True)
        package_output = args.package_output.resolve() if args.package_output else output / 'packages'
        for command in checks['package']:
            command += ['--output-dir', str(package_output)]
        canary = log.run([sys.executable, '-m', 'unittest', 'taskplane.tests.test_runner_isolation.TestUnittestRunnerIsolation', '-v'], label='canary/unittest')
        canary['canary_verdict'] = canary_verdict(canary)
        canary['status'] = canary['canary_verdict']['status']
        records = [canary]
        hostless_empty = False
        if canary['status'] == 'pass' and args.host == 'hostless':
            collection = log.run(pytest_command(['taskplane/tests']) + ['--collect-only', '--taskplane-suite', args.suite],
                                 label='hostless/inventory', pytest_run=True, collect=True)
            records.append(collection)
            if collection['status'] == 'pass':
                nodes = [row['nodeid'] for row in collection.get('collection', [])
                         if row['suite'] == args.suite and row.get('host') is None]
                collection['hostless_partition'] = {'verified_inventory_count': len(collection.get('collection', [])), 'selected_nodeids': nodes}
                hostless_empty = not nodes
                checks['tests'][0] = pytest_command(nodes) + ['--taskplane-suite', args.suite]
                if args.junitxml and nodes:
                    checks['tests'][0] += ['--junitxml', str(args.junitxml.resolve())]
        if all(r['status'] == 'pass' for r in records) and not hostless_empty:
            for name in selected:
                for command in checks[name]:
                    records.append(log.run(command, label=name, pytest_run=name in ('tests', 'browser')))
        for record in records:
            print(f"\n{record['label']}: {json.dumps(record['argv'])}", flush=True)
        result = 0 if all(r['status'] == 'pass' for r in records) else 1
        if args.report_json:
            target = args.report_json.resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_json_bytes(pack_evidence({'schema': 'taskplane.ci-execution/v1',
                'status': 'pass' if result == 0 else 'fail',
                'source_fingerprint': records[-1]['source_fingerprint_after'],
                'source_fingerprint_coverage': records[-1]['source_fingerprint_coverage'],
                'executions': records})))
        return result


if __name__ == '__main__':
    raise SystemExit(main())
