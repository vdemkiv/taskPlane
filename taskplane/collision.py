"""Exclusive delivery authority and foreign-interference evidence.

The kernel is deterministic and host-neutral.  Adapters tell it whether the
exact workspace is governed; this module never guesses that authority from a
process-global signal or from a similarly named directory.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import uuid


REGISTRY_SCHEMA = "taskplane.delivery-isolation-registry/v1"
DECISION_SCHEMA = "taskplane.delivery-isolation-decision/v1"
INTERFERENCE_SCHEMA = "taskplane.foreign-interference/v1"
_KINDS = {"skill", "agent"}
_ACTIONS = {"no_op", "allow", "advise", "deny", "observed"}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _registry_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "collision_registry.json")


def load_registry(path: str | None = None) -> dict:
    with open(path or _registry_path(), encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or value.get("schema") != REGISTRY_SCHEMA:
        raise ValueError("delivery isolation registry schema is invalid")
    if not isinstance(value.get("version"), int) or value["version"] < 1:
        raise ValueError("delivery isolation registry version is invalid")
    for key in ("known_competitors", "taskplane_namespaces",
                "host_builtin_agents", "helper_skills",
                "foreign_state_signatures"):
        if key not in value:
            raise ValueError(f"delivery isolation registry lacks {key}")
    checked = copy.deepcopy(value)
    checked["fingerprint"] = _fingerprint(value)
    return checked


def _identity(value: object) -> str:
    return str(value or "").strip()


def _namespace_match(identity: str, namespaces: list) -> bool:
    lowered = identity.casefold()
    for raw in namespaces:
        item = str(raw).strip().casefold()
        if not item:
            continue
        if item.endswith(("-", "_")) and lowered.startswith(item):
            return True
        if lowered == item or lowered.startswith(item + ":") \
                or lowered.startswith(item + "/"):
            return True
    return False


def _continuation(step: str | None) -> str:
    current = str(step or "unknown").strip() or "unknown"
    if current in {"req", "refine", "product"}:
        return "tp product"
    if current == "design":
        return "tp design"
    return "tp loop next"


def classify(kind: str, identity: object, *, governed: bool,
             run_id: str | None = None, step: str | None = None,
             strict: bool = False, advisory: bool = False,
             registry: dict | None = None,
             allow: list[str] | None = None) -> dict:
    """Classify one skill invocation or agent dispatch.

    ``advisory`` is fail-safe in the honest direction: a decision may say
    what a live hook would have done, but never claims the inactive wall
    denied an action.
    """
    if kind not in _KINDS:
        raise ValueError("collision kind must be skill or agent")
    reg = registry or load_registry()
    name = _identity(identity)
    base = {
        "schema": DECISION_SCHEMA,
        "kind": kind,
        "identity": name,
        "run_id": str(run_id) if run_id is not None else None,
        "step": str(step) if step is not None else None,
        "registry_version": reg["version"],
        "registry_fingerprint": reg["fingerprint"],
        "continuation": _continuation(step),
    }
    if not governed:
        return {**base, "action": "no_op", "category": "ungoverned",
                "record": False, "silent": True,
                "reason": "no exact-workspace governed state is active"}

    additions = [str(item).strip().casefold() for item in (allow or [])
                 if str(item).strip()]
    if kind == "skill":
        if _namespace_match(name, reg["taskplane_namespaces"]):
            category, intended, record, silent = \
                "taskplane", "allow", False, True
        elif _namespace_match(name, reg["helper_skills"] + additions):
            category, intended, record, silent = \
                "helper", "allow", False, True
        elif _namespace_match(
                name, reg["known_competitors"]["skill_namespaces"]):
            category, intended, record, silent = \
                "known_competitor", "deny", True, False
        else:
            category, intended, record, silent = \
                "unknown_foreign", "deny" if strict else "advise", True, False
    else:
        lowered = name.casefold()
        builtins = {str(item).casefold()
                    for item in reg["host_builtin_agents"]}
        if _namespace_match(name, reg["taskplane_namespaces"]):
            category, intended, record, silent = \
                "taskplane", "allow", False, True
        elif lowered in builtins:
            category, intended, record, silent = \
                "host_builtin", "allow", False, True
        elif _namespace_match(
                name, reg["known_competitors"]["agent_namespaces"]):
            category, intended, record, silent = \
                "known_competitor", "deny", True, False
        else:
            category, intended, record, silent = \
                "unknown_foreign", "deny" if strict else "advise", True, False

    action = "observed" if advisory and intended in {"deny", "advise"} \
        else intended
    reason = (f"taskplane run={base['run_id'] or 'active'} step="
              f"{base['step'] or 'unknown'} classified foreign {kind} "
              f"{name!r} as {category}; use `{base['continuation']}` to "
              "continue the active Taskplane delivery")
    if advisory and action == "observed":
        reason += f"; advisory enforcement observed this only (would {intended})"
    return {**base, "action": action, "would_action": intended,
            "category": category, "record": record, "silent": silent,
            "reason": reason}


def validate_decision(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("schema") != DECISION_SCHEMA:
        raise ValueError("delivery isolation decision is invalid")
    if value.get("kind") not in _KINDS or value.get("action") not in _ACTIONS:
        raise ValueError("delivery isolation decision kind/action is invalid")
    if not isinstance(value.get("registry_version"), int) or \
            not str(value.get("registry_fingerprint") or ""):
        raise ValueError("delivery isolation decision lacks registry evidence")
    return copy.deepcopy(value)


def empty_ledger(*, run_id: str | None = None) -> dict:
    return {
        "schema": INTERFERENCE_SCHEMA,
        "run_id": str(run_id) if run_id is not None else None,
        "counts": {"denied_skills": 0, "denied_agents": 0,
                   "advised_invocations": 0, "observed_invocations": 0,
                   "signed_roots": 0},
        "identities": [], "state_roots": [], "events": [],
    }


def record(ledger: dict | None, decision: dict, *, observed_at: int | None = None,
           max_events: int = 256) -> dict:
    checked = validate_decision(decision)
    out = copy.deepcopy(ledger) if isinstance(ledger, dict) else \
        empty_ledger(run_id=checked.get("run_id"))
    if out.get("schema") != INTERFERENCE_SCHEMA:
        raise ValueError("foreign interference ledger is invalid")
    if not checked.get("record"):
        return out
    action, kind = checked["action"], checked["kind"]
    key = ("denied_skills" if action == "deny" and kind == "skill" else
           "denied_agents" if action == "deny" else
           "observed_invocations" if action == "observed" else
           "advised_invocations")
    out.setdefault("counts", {})[key] = int(
        out.get("counts", {}).get(key) or 0) + 1
    identity = {"kind": kind, "identity": checked["identity"],
                "category": checked.get("category")}
    if identity not in out.setdefault("identities", []):
        out["identities"].append(identity)
        out["identities"] = sorted(out["identities"], key=lambda row: (
            row["kind"], row["identity"]))[:128]
    event = {"event_id": uuid.uuid4().hex, "at": int(
        time.time() if observed_at is None else observed_at),
             "kind": kind, "identity": checked["identity"],
             "action": action, "would_action": checked.get("would_action"),
             "step": checked.get("step"),
             "registry_version": checked["registry_version"],
             "registry_fingerprint": checked["registry_fingerprint"]}
    out.setdefault("events", []).append(event)
    out["events"] = out["events"][-max(1, int(max_events)):]
    return out


def _contained_root(workspace: str, relative: str) -> str | None:
    root = os.path.realpath(workspace)
    raw = str(relative or "").replace("\\", "/").strip("/")
    if not raw or raw in {".", ".."} or raw.startswith("../"):
        return None
    candidate = os.path.abspath(os.path.join(root, *raw.split("/")))
    try:
        if os.path.commonpath((root, candidate)) != root:
            return None
    except ValueError:
        return None
    # A foreign-state exclusion must name the directory in this checkout,
    # not a symlink target outside it.
    if os.path.islink(candidate) or os.path.realpath(candidate) != candidate:
        return None
    return candidate


def discover_state_roots(workspace: str, *, registry: dict | None = None) -> list:
    """Return only roots satisfying a versioned structural signature."""
    reg = registry or load_registry()
    found = []
    for signature in reg["foreign_state_signatures"]:
        root = _contained_root(workspace, signature.get("root"))
        if root is None or not os.path.isdir(root):
            continue
        required_all = list(signature.get("required_all") or [])
        required_any = list(signature.get("required_any") or [])
        if not all(os.path.exists(os.path.join(root, *item.split("/")))
                   for item in required_all):
            continue
        if required_any and not any(
                os.path.exists(os.path.join(root, *item.split("/")))
                for item in required_any):
            continue
        found.append({
            "plugin": str(signature.get("plugin") or "unknown"),
            "root": str(signature["root"]).replace("\\", "/"),
            "registry_version": reg["version"],
            "registry_fingerprint": reg["fingerprint"],
            "remediation": reg.get("remediation"),
        })
    return sorted(found, key=lambda row: (row["plugin"], row["root"]))


def record_state_roots(ledger: dict | None, roots: list,
                       *, run_id: str | None = None) -> dict:
    out = copy.deepcopy(ledger) if isinstance(ledger, dict) else \
        empty_ledger(run_id=run_id)
    if out.get("schema") != INTERFERENCE_SCHEMA:
        raise ValueError("foreign interference ledger is invalid")
    unique = {(str(row.get("plugin")), str(row.get("root"))): copy.deepcopy(row)
              for row in roots if isinstance(row, dict) and row.get("root")}
    out["state_roots"] = [unique[key] for key in sorted(unique)]
    out.setdefault("counts", {})["signed_roots"] = len(out["state_roots"])
    return out


def validate_ledger(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("schema") != INTERFERENCE_SCHEMA:
        raise ValueError("foreign interference ledger is invalid")
    counts = value.get("counts")
    if not isinstance(counts, dict) or any(
            not isinstance(item, int) or isinstance(item, bool) or item < 0
            for item in counts.values()):
        raise ValueError("foreign interference counts are invalid")
    return copy.deepcopy(value)


def ledger_path(workspace: str) -> str:
    import taskplane_lite as tp
    return os.path.join(tp.tp_dir(workspace), "foreign-interference.json")


def load_ledger(workspace: str) -> dict | None:
    path = ledger_path(workspace)
    try:
        with open(path, encoding="utf-8") as handle:
            return validate_ledger(json.load(handle))
    except FileNotFoundError:
        return None


def persist(workspace: str, *, decision: dict | None = None,
            roots: list | None = None, run_id: str | None = None,
            observed_at: int | None = None) -> dict:
    """Lock and atomically update the workspace's durable bounded ledger."""
    import taskplane_lite as tp

    path = ledger_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with tp.file_lock(path, timeout=10.0):
        current = load_ledger(workspace) or empty_ledger(run_id=run_id)
        if decision is not None:
            current = record(current, decision, observed_at=observed_at)
        if roots is not None:
            current = record_state_roots(current, roots, run_id=run_id)
        tp.atomic_write_json(path, current, indent=2, sort_keys=True)
    return current
