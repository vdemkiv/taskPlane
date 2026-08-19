"""Exact, truthful cache for verified evaluator infrastructure outages."""
from __future__ import annotations

import copy
import hashlib
import os
from collections.abc import Mapping

import taskplane_lite as tp


class EvaluatorHealthError(ValueError):
    pass


_REASONS = frozenset({
    "host_unavailable", "agent_timeout", "transport_unavailable",
    "producer_receipt_unavailable", "orchestration_unavailable",
})


def cache_key(workspace: str, *, evaluator: str, evaluator_version: str,
              engine_fingerprint: str, capability: str,
              recovery_fingerprint: str) -> dict:
    """Bind an outage observation to its complete reusable identity."""
    values = {
        "evaluator": str(evaluator or "").strip(),
        "evaluator_version": str(evaluator_version or "").strip(),
        "engine_fingerprint": str(engine_fingerprint or "").strip(),
        "capability": str(capability or "").strip(),
        "recovery_fingerprint": str(recovery_fingerprint or "").strip(),
    }
    if any(not value for value in values.values()):
        raise EvaluatorHealthError("evaluator outage cache key is incomplete")
    root = tp.review_execution_root_identity(workspace)
    material = {
        "schema": "taskplane.evaluator-health-key/v1",
        **values,
        "repository_id": root["repository_id"],
        "worktree_fingerprint": root["worktree_fingerprint"],
    }
    return dict(material, fingerprint=hashlib.sha256(
        tp.canonical_json_bytes(material)).hexdigest())


class EvaluatorHealthCache:
    """Small content-keyed cache; cached outages never become lens verdicts."""

    def __init__(self, root: str):
        supplied = os.path.abspath(root)
        if os.path.realpath(supplied) != supplied:
            raise EvaluatorHealthError("evaluator health cache root is a symlink")
        self.root = supplied
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: Mapping) -> str:
        fingerprint = str(key.get("fingerprint") or "")
        if len(fingerprint) != 64 or any(c not in "0123456789abcdef"
                                         for c in fingerprint):
            raise EvaluatorHealthError("evaluator health cache key is invalid")
        return os.path.join(self.root, fingerprint + ".json")

    def record_unavailable(self, key: Mapping, *, failure: Mapping,
                           observed_at: float, valid_for: float) -> dict:
        reason = str(failure.get("reason_code") or "")
        if failure.get("status") != "infrastructure-unavailable" or \
                reason not in _REASONS:
            raise EvaluatorHealthError(
                "only verified infrastructure-unavailable may be cached")
        if valid_for <= 0:
            raise EvaluatorHealthError("evaluator outage validity must be positive")
        entry = {
            "schema": "taskplane.evaluator-health-cache/v1",
            "key": copy.deepcopy(dict(key)),
            "observed_at": float(observed_at),
            "valid_until": float(observed_at) + float(valid_for),
            "evaluation": {"status": "unavailable", "reason_code": reason},
        }
        path = self._path(key)
        prior = tp.load_json(path, default=None, what="evaluator health cache")
        if prior is not None and prior != entry:
            raise EvaluatorHealthError("evaluator outage cache entry is contradictory")
        tp.atomic_write_json(path, entry, sort_keys=True)
        return entry

    def lookup(self, key: Mapping, *, now: float) -> dict:
        entry = tp.load_json(
            self._path(key), default=None, what="evaluator health cache")
        if not isinstance(entry, dict) or entry.get("key") != dict(key):
            return {"status": "miss", "reason": "exact-key-miss"}
        if float(now) > float(entry.get("valid_until") or 0):
            return {"status": "miss", "reason": "expired"}
        evaluation = entry.get("evaluation")
        if not isinstance(evaluation, dict) or evaluation.get("status") != \
                "unavailable" or evaluation.get("reason_code") not in _REASONS:
            return {"status": "miss", "reason": "invalid-cache-record"}
        return {
            "status": "hit", "source": "verified-infrastructure-cache",
            "evaluation": copy.deepcopy(evaluation),
            "valid_until": entry["valid_until"],
        }


def evaluate_or_reuse(cache: EvaluatorHealthCache, key: Mapping, *,
                      launcher, now: float, valid_for: float) -> dict:
    """Avoid a repeated launch only for the exact verified outage key."""
    cached = cache.lookup(key, now=now)
    if cached.get("status") == "hit":
        return cached
    result = launcher()
    if not isinstance(result, Mapping):
        raise EvaluatorHealthError("evaluator launcher returned no result")
    if result.get("status") != "infrastructure-unavailable":
        return {"status": "launched", "evaluation": copy.deepcopy(dict(result))}
    entry = cache.record_unavailable(
        key, failure=result, observed_at=now, valid_for=valid_for)
    return {
        "status": "launched", "evaluation": copy.deepcopy(entry["evaluation"]),
        "valid_until": entry["valid_until"],
    }
