"""Non-destructive adoption of legacy taskPlane checkout locations."""
from __future__ import annotations

import os
import subprocess

import run_store
import storage


def _git(checkout: str, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", *args], cwd=checkout, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{exc.__class__.__name__}: {exc}"
    return int(result.returncode), str(result.stdout or "").strip()


def _legacy_checkouts(workspace: str) -> list[str]:
    scratch = os.path.join(os.path.realpath(workspace),
                           ".em-review", "scratch")
    if not os.path.isdir(scratch) or os.path.islink(scratch):
        return []
    found = []
    for root, directories, _files in os.walk(scratch, followlinks=False):
        directories[:] = [name for name in directories
                           if not os.path.islink(os.path.join(root, name))]
        if os.path.isdir(os.path.join(root, ".git")) or \
                os.path.isfile(os.path.join(root, ".git")):
            found.append(os.path.realpath(root))
            directories[:] = []
    return sorted(set(found))


def migrate_legacy_checkouts(workspace: str, *, home: str | None = None) \
        -> dict:
    """Register clean legacy clones; never move or delete a checkout."""
    store = run_store.RunStore(home=home)
    rows = []
    for checkout in _legacy_checkouts(workspace):
        rc, dirty = _git(checkout, "status", "--porcelain")
        if rc != 0:
            rows.append({"path": checkout, "status": "invalid_git_checkout",
                         "detail": dirty[-400:]})
            continue
        if dirty:
            rows.append({"path": checkout, "status": "dirty_user_checkout",
                         "detail": "left in place; inspect before adoption"})
            continue
        try:
            identity = storage.resolve_repository_identity(checkout)
        except Exception as exc:
            rows.append({"path": checkout, "status": "identity_unavailable",
                         "detail": f"{exc.__class__.__name__}: {exc}"})
            continue
        if identity.kind != "hosted":
            rows.append({"path": checkout, "status": "local_checkout",
                         "detail": "left in place; no hosted identity"})
            continue
        store.register_checkout(
            identity, checkout=checkout, source="legacy-em-review-scratch")
        rows.append({"path": checkout,
                     "repo_id": identity.repo_id,
                     "repository_key": identity.key,
                     "status": "registered_legacy_alias",
                     "detail": "left in place; future acquisition uses the "
                               "canonical managed checkout root"})
    adopted = sum(row["status"] == "registered_legacy_alias" for row in rows)
    return {
        "schema": "taskplane.storage-migration/v1",
        "workspace": os.path.realpath(workspace),
        "checkouts": rows, "adopted": adopted,
        "review_required": len(rows) - adopted,
        "destructive_actions": 0,
    }
