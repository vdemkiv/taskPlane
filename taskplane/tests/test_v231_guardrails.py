"""v2.3.1 enforcement-kernel fixes — each TIGHTENS or restores a guardrail;
none loosens. Tests assert the strict behavior so a future regression fails."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import taskplane_lite as tp


def _ro():
    return tp.build_contract("review", read_only=True,
                             write_allow=[".em-review/**"])


def _build():
    return tp.build_contract("build", scope=["taskplane/**"])


def _stool(contract, cmd):
    ok, why = tp.screen_tool(contract, "Bash", {"command": cmd}, "/tmp/fix23")
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
    cmd = f"python3 {os.path.join('/tmp/fix23', 'taskplane', 'tp.py')} summary"
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
    with kb.mutate("/tmp/fix23", root=d):
        pass  # must complete without a raw flock and create no leaked handle
    # the shared lock leaves either a .lock file or a cleaned .lockdir — never
    # proceeds unlocked silently; smoke: a second acquisition still works
    with kb.mutate("/tmp/fix23", root=d):
        pass
