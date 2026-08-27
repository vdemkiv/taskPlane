from __future__ import annotations

from pathlib import Path

from taskplane.delivery_ports import SubprocessGitRunner
from taskplane.release_evidence import (
    COMPATIBILITY_PREVIOUS_VERSION,
    CURRENT_VERSION,
    HISTORICAL_GRAPH_REVISION,
    PREVIOUS_RELEASE_COMMIT,
    PREVIOUS_VERSION,
    SUPERSEDED_CANDIDATE_VERSION,
    forward_history_receipt,
    validate_forward_history,
)


ROOT = Path(__file__).resolve().parents[2]


def test_v21720_remains_released_incomplete():
    history = forward_history_receipt()

    assert history["released_generation"] == {
        "version": PREVIOUS_VERSION,
        "tag": "v2.17.20",
        "commit": PREVIOUS_RELEASE_COMMIT,
        "status": "released-incomplete",
        "re_release": False,
    }


def test_forward_candidate_is_exactly_v21724():
    history = forward_history_receipt()

    assert CURRENT_VERSION == "2.17.24"
    assert PREVIOUS_VERSION == "2.17.20"
    assert COMPATIBILITY_PREVIOUS_VERSION == "2.17.23"
    assert SUPERSEDED_CANDIDATE_VERSION == "2.17.23"
    assert history["forward_generation"] == {
        "version": "2.17.24",
        "repair_of": "2.17.23",
        "history_rewrite": False,
    }


def test_forward_candidate_is_exactly_v21723():
    """Retain the superseded local-candidate selector as a historical alias."""
    test_forward_candidate_is_exactly_v21724()


def test_forward_candidate_is_exactly_v21722():
    """Retain the immutable R-0001 wiring selector as a historical alias."""
    test_forward_candidate_is_exactly_v21724()


def test_historical_graph_revision_is_attributed_without_history_rewrite():
    history = forward_history_receipt()

    assert history["historical_graph"] == {
        "revision": HISTORICAL_GRAPH_REVISION,
        "classification": "attributed-inherited-limitation",
        "history_rewrite": False,
        "re_release": False,
        "verifier_weakened": False,
    }
    assert history["cryptographic_authenticity_claimed"] is False


def test_verifier_strength_and_release_history_are_unchanged():
    history = validate_forward_history(
        git_runner=SubprocessGitRunner(), repository=ROOT
    )

    assert validate_forward_history(history) == history
    assert history["released_generation"]["commit"] == PREVIOUS_RELEASE_COMMIT
