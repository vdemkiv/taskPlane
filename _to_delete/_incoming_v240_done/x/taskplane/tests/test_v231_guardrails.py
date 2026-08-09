"""v2.3.1 enforcement-kernel fixes — each TIGHTENS or restores a guardrail;
none loosens. Tests assert the strict behavior so a future regression fails."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import taskplane_lite as tp

# The real repo root — never a machine-specific path (a hardcoded /tmp/... path
# passes locally but fails on CI, where the checkout lives elsewhere).
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ro():
    return tp.build_contract("review", read_only=True,
                             write_allow=[".em-review/**"])


def _build():
    return tp.build_contract("build", scope=["taskplane/**"])


def _stool(contract, cmd):
    ok, why = tp.screen_tool(contract, "Bash", {"command": cmd}, ROOT)
    return ok


# ---- #15 awk / ed / tar are now screened (were an escape) ----

def test_awk_write_is_blocked_under_readonly():
    assert _stool(_ro(), "awk 'BEGIN{print > \"main.py\"}'") is False


def test_gawk_mawk_blocked_under_readonly():
    assert _stool(_ro(), "gawk 'BEGIN{print > \"x\"}'") is False
    assert _stool(_ro(), "mawk 'BEGIN{print > \"x\"}'") is False


def test_ed_ex_blocked_under_readonly():
    assert _stool(_ro(), "ed script.ed") is False


def test_tar_extract_blocked_under_readonly():
    assert _stool(_ro(), "tar -xf archive.tar") is False


def test_unzip_extract_blocked_under_readonly():
    assert _stool(_ro(), "unzip payload.zip") is False


def test_tar_list_is_not_blocked():
    # -t is read-only listing, must remain allowed
    assert _stool(_ro(), "tar -tf archive.tar") is True


# ---- #14 tp.py's own CLI is exempt under read-only (was self-DoS'd) ----

def test_tp_cli_allowed_under_readonly_relative():
    assert _stool(_ro(), "python3 taskplane/tp.py findings --paged") is True


def test_tp_cli_allowed_under_readonly_absolute():
    cmd = f"python3 {os.path.join(ROOT, 'taskplane', 'tp.py')} summary"
    assert _stool(_ro(), cmd) is True


def test_arbitrary_python_script_still_blocked_under_readonly():
    # the exemption is ONLY tp.py — a stray script stays interpreter-opaque
    assert _stool(_ro(), "python3 evil.py") is False
    assert _stool(_ro(), "python3 -c \"open('x','w')\"") is False


def test_stray_file_named_tp_py_without_taskplane_parent_not_exempt():
    assert _stool(_ro(), "python3 /home/me/tp.py") is False


# ---- kb.mutate uses the shared lock (no silent lock-free) ----

def test_kb_mutate_uses_file_lock():
    os.environ.setdefault("TASKPLANE_HOME", tempfile.mkdtemp())
    import kb
    d = tempfile.mkdtemp()
    with kb.mutate(ROOT, root=d):
        pass  # must complete without a raw flock and create no leaked handle
    # the shared lock leaves either a .lock file or a cleaned .lockdir — never
    # proceeds unlocked silently; smoke: a second acquisition still works
    with kb.mutate(ROOT, root=d):
        pass


# ---- v3 dogfood: literal PLAN-MINTED scope beats default deny; unminted
# ---- (tp new) literals, wildcards, and secrets never do ----

def test_literal_scope_overrides_default_out_of_scope():
    # provenance required: coding["plan_minted"] is set ONLY by the loop
    # engine when the contract derives from a human-approved plan task
    coding = {"scope_paths": [".github/workflows/ci.yml"],
              "out_of_scope_paths": list(tp.DEFAULT_OUT_OF_SCOPE),
              "plan_minted": True}
    assert tp.scope_violation(".github/workflows/ci.yml", coding) is None


def test_unminted_literal_scope_stays_denied():
    # the EM-review exploit repro: identical coding WITHOUT plan provenance
    # (what `tp new --scope .git/hooks/x` re-minted by a governed agent
    # produces) must NOT punch through the default deny
    coding = {"scope_paths": [".github/workflows/ci.yml"],
              "out_of_scope_paths": list(tp.DEFAULT_OUT_OF_SCOPE)}
    assert tp.scope_violation(".github/workflows/ci.yml", coding) is not None


def test_build_contract_plan_minted_flag_propagates():
    # loop-engine path carries provenance; the default (tp new path) never
    minted = tp.build_contract("EXECUTE: t9", scope=["x.py"],
                               plan_minted=True)
    unminted = tp.build_contract("goal", scope=["x.py"])
    assert minted["coding"].get("plan_minted") is True
    assert "plan_minted" not in unminted["coding"]


def test_wildcard_scope_never_overrides_out_of_scope():
    # even WITH plan provenance: only wildcard-free literals qualify
    coding = {"scope_paths": [".github/**"],
              "out_of_scope_paths": list(tp.DEFAULT_OUT_OF_SCOPE),
              "plan_minted": True}
    assert tp.scope_violation(".github/workflows/ci.yml", coding) is not None


def test_secrets_family_cannot_be_overridden_even_literally():
    # paths that the default globs actually match (fnmatch semantics require
    # a directory prefix for '**/'): a literal scope entry must NOT punch
    # through the secrets family — not even a plan-minted one.
    coding = {"scope_paths": ["src/.env", "app/secrets/key.pem"],
              "out_of_scope_paths": list(tp.DEFAULT_OUT_OF_SCOPE),
              "plan_minted": True}
    assert tp.scope_violation("src/.env", coding) is not None
    assert tp.scope_violation("app/secrets/key.pem", coding) is not None


def test_root_level_env_and_secrets_are_denied():
    # EM v3 fix: fnmatch '**/'-globs need a directory prefix, so root-level
    # .env and secrets/ escaped the family — now covered explicitly, and
    # sacred: not even a plan-minted literal punches through.
    coding = {"scope_paths": [".env", "secrets/key.pem"],
              "out_of_scope_paths": list(tp.DEFAULT_OUT_OF_SCOPE),
              "plan_minted": True}
    assert tp.scope_violation(".env", coding) is not None
    assert tp.scope_violation("secrets/key.pem", coding) is not None


def test_unrelated_out_of_scope_paths_still_denied():
    coding = {"scope_paths": ["taskplane/loop.py"],
              "out_of_scope_paths": list(tp.DEFAULT_OUT_OF_SCOPE)}
    assert tp.scope_violation(".git/config", coding) is not None
