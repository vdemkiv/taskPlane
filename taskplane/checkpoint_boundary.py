"""Dependency-neutral registration seam for semantic checkpoint evidence.

The governed command composition root owns durable execution evidence, while
``checkpoint`` owns validation and receipt minting.  Keeping the provider
injected here preserves that ownership direction without making either owner
import the other in both directions.
"""
from __future__ import annotations

from collections.abc import Callable


_EXECUTION_EVIDENCE_LOADER: Callable[
    [str, str, str], dict[str, object]
] | None = None


def register_execution_evidence_loader(
        loader: Callable[[str, str, str], dict[str, object]]) -> None:
    """Register the governed runtime's semantic evidence reader."""
    global _EXECUTION_EVIDENCE_LOADER
    if not callable(loader):
        raise TypeError("checkpoint evidence loader must be callable")
    _EXECUTION_EVIDENCE_LOADER = loader


def load_execution_evidence(
        workspace: str, authorization: str, handle: str) -> dict[str, object]:
    """Read semantic evidence through the registered governed boundary."""
    if _EXECUTION_EVIDENCE_LOADER is None:
        raise RuntimeError("checkpoint evidence loader is not registered")
    return _EXECUTION_EVIDENCE_LOADER(workspace, authorization, handle)
