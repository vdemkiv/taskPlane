"""Compatibility facade for component decomposition.

The documented components.yaml rule remains exact: an
unsupported line SHAPE raises ValueError, while ``load_floors`` catches that
error and the
whole file fails OPEN to the defaults with the failure reported.  The
implementation now lives below the graph scanner in ``graph_decomposition``;
this module preserves the established direct API and test seams without
importing depgraph or lens_signals.
"""
from __future__ import annotations

import graph_decomposition as _engine
import graph_primitives
import path_roles


# Public constants and long-standing direct helpers.
CANDIDATE_MIN_FILES = _engine.CANDIDATE_MIN_FILES
BIG_FILE_LINES = _engine.BIG_FILE_LINES
CLUSTER_MIN_FILES = _engine.CLUSTER_MIN_FILES
CLUSTER_MIN_SYMBOLS = _engine.CLUSTER_MIN_SYMBOLS
CLUSTER_MIN_LINES = _engine.CLUSTER_MIN_LINES
MAX_FILE_BYTES = _engine.MAX_FILE_BYTES
MAX_MODULE_FILES = _engine.MAX_MODULE_FILES

posixpath = _engine.posixpath

_DerivationError = _engine._DerivationError
_syntax_error = _engine._syntax_error
_unreadable_error = _engine._unreadable_error
_read_text = _engine._read_text


def _parse_components_yaml(text: str) -> dict:
    """Preserve the facade's visible one-parser compatibility seam."""
    cfg = path_roles.parse_components_yaml(text)
    return {key: value for key, value in cfg["floors"].items()
            if key in _engine._FLOOR_KEYS}


load_floors = _engine.load_floors
floors_hash = _engine.floors_hash
_sanitize = _engine._sanitize
_sym_prefix = _engine._sym_prefix
_count_lines = _engine._count_lines
_module_of = graph_primitives.module_of
_repo_stems = _engine._repo_stems
_module_stems = _engine._module_stems
_py_import_map = _engine._py_import_map
_file_refs = _engine._file_refs
_fingerprint = _engine._fingerprint
_symbol_clusters = _engine._symbol_clusters
_derive_module = _engine._derive_module
_graph_payload = _engine._graph_payload
_lens_map = _engine._lens_map


def derive(workspace: str, graph: dict, prev: dict | None = None):
    """Delegate to the decomposition engine through compatibility seams.

    ``_read_text`` has historically been monkeypatchable by fail-open tests
    and embedders.  Reflect the facade's current callable for the duration of
    this direct invocation; scanner-owned calls use the engine directly.
    """
    original = _engine._read_text
    _engine._read_text = _read_text
    try:
        return _engine.derive(workspace, graph, prev)
    finally:
        _engine._read_text = original
