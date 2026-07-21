"""taskplane-lite — the enforcement kernel, stdlib only.

A dependency-free port of the parts of taskplane that a Cowork plugin can
enforce *mechanically* on the host agent:

  * tool allowlist               (contract.allowed_tools)
  * filesystem scope boundaries  (coding.scope_paths / out_of_scope_paths)
  * shell command deny patterns  (coding.command_policy.deny)
  * shell write-target screening (redirects + tee/cp/mv/dd/sed -i/…)
  * Definition-of-Done gate       (git scope diff + a test command)

What a Cowork plugin CANNOT do (the honest limitation): intercept the host
agent's model calls, so the dollar/token budget is tracked cooperatively,
not enforced before spend. The tool/scope/command screen IS enforced by the
PreToolUse hook before the action runs — but it screens a *cooperative*
shell, not an OS sandbox. It reads the command string, makes wrapper
programs (env/nohup/sudo/xargs/…) and nested `sh -c`/`$()` transparent, and
blocks resolvable out-of-scope writes plus the clearly-destructive
unscopeable verbs (`find -delete/-exec`, `git checkout/reset/…`). A
read-only review contract additionally blocks every un-screenable mutator,
so the reviewed source is protected on a best-effort basis. It is NOT a
boundary against a determined interpreter: `python -c "…"` under a *build*
contract can still write anywhere, because a Turing-complete body can't be
screened from argv. For a hard guarantee, run review/build contracts on a
read-only bind-mount or in a container — the screen is defense-in-depth,
not the wall.

Behavior mirrors the audited taskplane hooks/DoD logic so a task governed
in Cowork behaves the same as one governed via the full proxy runtime.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import posixpath
import re
import shlex
import subprocess

# Claude Code / Cowork write tools → the input key that carries the path.
WRITE_TOOL_PATH_FIELDS = {
    "Write": ("file_path", "path"),
    "Edit": ("file_path", "path"),
    "MultiEdit": ("file_path", "path"),
    "NotebookEdit": ("notebook_path", "file_path", "path"),
    "str_replace": ("file_path", "path"),
}
WRITE_TOOLS = set(WRITE_TOOL_PATH_FIELDS)
COMMAND_TOOLS = {"Bash", "BashOutput"}

_CMD_SEP_RE = re.compile(r"[;&|\n]+")
# a redirect operator token after shlex tokenization: >, >>, 2>, &>, >| …
_REDIRECT_OP_RE = re.compile(r"^(?:\d*>>?|>\||&>>?|\d*>&\d*)$")
# a redirect written glued to its target: >file, 2>>log (no space)
_REDIRECT_GLUED_RE = re.compile(r"^(?:\d*>>?|>\|)(?P<f>[^>&].*)$")
# command substitutions whose bodies run their own commands
_SUBST_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
# bash ANSI-C quoting: $'…' with backslash escapes decoded by the shell
_ANSI_C_RE = re.compile(r"\$'((?:\\.|[^'\\])*)'")


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
             "setsid", "stdbuf", "timeout", "chrt", "eatmydata"}
_SHELLS = {"sh", "bash", "dash", "zsh", "ksh"}
# Interpreters whose inline-code flag hides arbitrary file writes from argv.
_INTERPRETERS = {"python", "python2", "python3", "perl", "ruby", "node",
                 "php", "lua", "Rscript", "deno", "bun"}
# git subcommands that rewrite tracked files in the working tree.
_GIT_MUTATORS = {"checkout", "reset", "restore", "clean", "stash"}
# git GLOBAL options that consume a SEPARATE following token as their value —
# `git -C <path> checkout` etc. Skipping the flag but not its value would let
# the value be misread as the subcommand, dodging the mutator screen.
_GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                   "--super-prefix", "--config-env", "--exec-path"}


# --------------------------------------------------------------- paths

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
    else:
        base = "/__ws__"
        joined = raw if posixpath.isabs(raw) else posixpath.join(base, raw)
        resolved = posixpath.normpath(joined)
    if resolved == base:
        return ""
    prefix = base.rstrip("/") + "/"
    if not resolved.startswith(prefix):
        return "ESCAPES:" + resolved
    return resolved[len(prefix):]


def match_any(path: str, globs) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in (globs or []))


def scope_violation(path: str, coding: dict) -> str | None:
    oos = coding.get("out_of_scope_paths") or []
    scope = coding.get("scope_paths") or []
    if oos and match_any(path, oos):
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
_WRITE_PROGRAMS = {
    "tee": _targets_tee, "cp": _targets_last_arg, "mv": _targets_last_arg,
    "install": _targets_last_arg, "rsync": _targets_last_arg,
    "ln": _targets_last_arg, "truncate": _targets_last_arg,
    "dd": _targets_dd, "sed": _targets_sed_i,
    "rm": _targets_tee, "shred": _targets_tee, "mkfifo": _targets_tee,
    "mknod": _targets_tee, "chmod": _targets_skip_first,
    "chown": _targets_skip_first,
}


def _token_subseq(needle, haystack) -> bool:
    if not needle:
        return False
    it = iter(haystack)
    return all(tok in it for tok in needle)


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
    return out


def _env_split_string(rest) -> list | None:
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
            i += 1                       # other env flag / VAR=val
            continue
        else:
            return None                  # reached the program: no -S form
        if val is not None:
            try:
                return shlex.split(val) + tail
            except ValueError:
                return val.split() + tail
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
}


def _unwrap(toks) -> list:
    """Strip leading transparent wrapper programs, returning the real argv.
    `env FOO=1 rm x` -> `rm x`; `timeout 5 rm x` -> `rm x`;
    `env -u NAME rm x` -> `rm x` (the `-u` swallows `NAME`)."""
    while toks:
        prog = os.path.basename(toks[0])
        if prog not in _WRAPPERS:
            return toks
        if prog == "env":
            split = _env_split_string(toks[1:])
            if split is not None:
                toks = split             # re-enter: may be another wrapper
                continue
        vflags = _WRAPPER_VALUE_FLAGS.get(prog, set())
        rest = toks[1:]
        while rest:
            tok = rest[0]
            if prog == "env" and "=" in tok and not tok.startswith("-"):
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


def _analyze(command: str, _depth: int = 0):
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
    """
    targets: list = []
    opaque = None
    if _depth > 6:                  # runaway substitution guard
        return targets, opaque

    # `N>|`/`>|` (force-clobber redirect) is semantically `>`; normalize it
    # BEFORE the separator split below eats the `|`, or `2>| /etc/f` splits
    # into an innocuous "2>" part and a bare-path part and the target is
    # never seen. Then decode bash ANSI-C quoting ($'…'): shlex doesn't know
    # it, so `eval $'rm \\x2drf x'` would otherwise tokenize into gibberish
    # that hides the real command.
    command = _ansi_c_unquote(command.replace(">|", ">"))

    for m in _SUBST_RE.finditer(command):
        body = m.group(1) or m.group(2) or ""
        if body.strip():
            t, o = _analyze(body, _depth + 1)
            targets += t
            opaque = opaque or o

    for part in _CMD_SEP_RE.split(command):
        try:
            toks = shlex.split(part)
        except ValueError:
            toks = part.split()
        if not toks:
            continue
        targets += _redirect_targets(toks)
        toks = _unwrap(toks)
        if not toks:
            continue
        prog = os.path.basename(toks[0])
        args = toks[1:]

        if prog in _SHELLS:
            for i, a in enumerate(args):
                if a == "-c" and i + 1 < len(args):
                    t, o = _analyze(args[i + 1], _depth + 1)
                    targets += t
                    opaque = opaque or o
                    break
            continue
        if prog == "eval":
            # eval re-parses its args as a shell command — screen what it
            # RUNS. Combined with the ANSI-C decode above this closes
            # `eval $'rm \x2drf x'`. An eval body we can't see through
            # (e.g. `eval "$CMD"`) still isn't provably safe — but eval of
            # a variable is rare in agent traffic and the screen stays a
            # cooperative best-effort layer, not an OS boundary.
            t, o = _analyze(" ".join(args), _depth + 1)
            targets += t
            opaque = opaque or o
            continue
        if prog == "xargs":
            sub = args
            while sub and sub[0].startswith("-"):
                sub = sub[1:]
            sub = _unwrap(sub)
            subprog = os.path.basename(sub[0]) if sub else ""
            if subprog in _WRITE_PROGRAMS or subprog in _INTERPRETERS \
                    or subprog in _SHELLS or subprog == "find":
                opaque = opaque or (
                    "destructive",
                    f"`xargs {subprog} …` runs a mutator on stdin-supplied "
                    "paths that can't be screened")
            continue
        fn = _WRITE_PROGRAMS.get(prog)
        if fn:
            targets += fn(args)
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
        if prog in _INTERPRETERS:
            if any(a in ("-c", "-e", "-E") or a.startswith("-e")
                   for a in args):
                opaque = opaque or (
                    "interpreter",
                    f"`{prog} -c/-e …` runs inline code whose file writes "
                    "can't be screened from argv")
            continue
        if prog == "git":
            # The subcommand is the first non-option arg — but git's GLOBAL
            # options can take a SEPARATE value that would otherwise be read
            # as the subcommand: `git -C /path checkout` must screen
            # `checkout`, not `/path`. Skip each value-taking global option
            # AND its argument before picking the subcommand.
            i, sub = 0, None
            while i < len(args):
                a = args[i]
                if not a.startswith("-"):
                    sub = a
                    break
                base = a.split("=", 1)[0]
                i += 1
                if base in _GIT_VALUE_OPTS and "=" not in a:
                    i += 1                 # swallow the option's value
            if sub in _GIT_MUTATORS:
                opaque = opaque or (
                    "destructive",
                    f"`git {sub}` rewrites tracked files in the working tree")
            continue

    return [t for t in targets if t], opaque


def write_targets(command: str) -> list:
    """Concrete write paths in a shell command (redirects + write-program
    args), seen through wrappers and nested `sh -c`/`$()`. Unscopeable
    mutators (interpreters, `find -delete`, VCS reverts) are surfaced
    separately via `_analyze`, not here."""
    return _analyze(command)[0]


def screen_command(cmd: str, coding: dict, workspace: str | None) -> str | None:
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    joined = " ".join(tokens)
    for pattern in (coding.get("command_policy") or {}).get("deny") or []:
        if _token_subseq(pattern.split(), tokens) or pattern in joined:
            return f"command matches deny pattern '{pattern}'"
    targets, opaque = _analyze(cmd)
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


# --------------------------------------------------------------- screen

def screen_tool(contract: dict, tool_name: str, tool_input: dict,
                workspace: str | None) -> tuple[bool, str]:
    """Return (allow, reason). Mirrors the taskplane PreToolUse hook."""
    allowed = contract.get("allowed_tools") or []
    if allowed and tool_name not in allowed:
        return False, f"tool '{tool_name}' not in allowed_tools"

    coding = contract.get("coding") or {}

    # Read-only contract: no filesystem writes EXCEPT an optional allowlist
    # of artifact dirs (write_allow). Used by reviewer/planner roles — e.g.
    # the EM may write review artifacts + scratch under .em-review/** but
    # never touch the reviewed source. Enforces the cardinal rule mechanically.
    if contract.get("read_only"):
        allow = contract.get("write_allow") or []
        if tool_name in WRITE_TOOLS:
            fields = WRITE_TOOL_PATH_FIELDS[tool_name]
            raw = next((tool_input[f] for f in fields if tool_input.get(f)), "")
            p = norm(str(raw), workspace)
            if not (p and match_any(p, allow)):
                return False, (f"read-only review contract: '{tool_name}' may "
                               f"only write under {allow or '(nothing)'} — "
                               "the reviewed source is protected")
        if tool_name in COMMAND_TOOLS:
            targets, opaque = _analyze(str(tool_input.get("command", "")))
            for t in targets:
                p = norm(t, workspace)
                if not (p and match_any(p, allow)):
                    return False, ("read-only review contract: command writes "
                                   f"'{t}' outside {allow or '(nothing)'} — "
                                   "the reviewed source is protected")
            # A review needs no mutator at all — block every un-screenable
            # one (interpreters AND destructive verbs), not just concrete
            # writes. This is what makes the read-only source protection hold
            # against `python -c`, `find -delete`, and `git checkout`.
            if opaque:
                return False, ("read-only review contract: " + opaque[1]
                               + f" — writes must stay under "
                               f"{allow or '(nothing)'}; the reviewed source "
                               "is protected (best-effort screen)")
        # deny patterns still apply below

    if tool_name in WRITE_TOOLS and (coding.get("scope_paths")
                                     or coding.get("out_of_scope_paths")):
        fields = WRITE_TOOL_PATH_FIELDS[tool_name]
        raw = next((tool_input[f] for f in fields if tool_input.get(f)), "")
        p = norm(str(raw), workspace)
        if p:
            v = scope_violation(p, coding)
            if v:
                return False, v

    if tool_name in COMMAND_TOOLS:
        v = screen_command(str(tool_input.get("command", "")), coding,
                           workspace)
        if v:
            return False, v

    return True, "within contract"


# --------------------------------------------------------------- DoD

def _run(cmd, cwd, shell=False, timeout=600):
    return subprocess.run(cmd, cwd=cwd, shell=shell, capture_output=True,
                          text=True, timeout=timeout)


# Paths the runtime/loop writes for itself. The DoD scope-diff must not
# count them as the task's out-of-scope changes — otherwise recording a KB
# decision or a plan would fail every governed task.
RUNTIME_OWNED = (".taskplane/", ".taskplane_output.json", "knowledge/",
                 "plan/", ".eval/", ".em-review/", ".security-review/",
                 ".tp-work/")


def changed_files(workspace: str, snapshot_ref: str) -> list:
    diff = _run(["git", "diff", "--name-only", snapshot_ref], cwd=workspace)
    untracked = _run(["git", "ls-files", "--others", "--exclude-standard"],
                     cwd=workspace)
    files = [f for f in (diff.stdout + untracked.stdout).splitlines()
             if f and not f.startswith(RUNTIME_OWNED)]
    return sorted(set(files))


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
        if _porcelain_path(ln).startswith(RUNTIME_OWNED):
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
        warnings.append("no DoD test_command — the exit gate will check scope "
                        "only, not behavior; set --tests to verify correctness")
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
              snapshot_ref: str | None) -> list:
    """Return a list of DoD errors ([] = pass). Fails closed if a scope
    diff is required but no snapshot exists."""
    errors: list = []
    coding = contract.get("coding") or {}
    dod = coding.get("dod") or {}

    if dod.get("require_clean_scope_diff", True) and coding.get("scope_paths"):
        if not snapshot_ref:
            errors.append("diff_scope: cannot verify — no git snapshot "
                          "(commit the workspace before governing)")
        else:
            for f in changed_files(workspace, snapshot_ref):
                p = f  # already workspace-relative from git
                v = scope_violation(p, coding)
                if v:
                    errors.append("diff_scope: " + v)

    tc = dod.get("test_command")
    if tc:
        proc = _run(tc, cwd=workspace, shell=True)
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
            errors.append(f"tests_pass: '{tc}' exited {proc.returncode}: "
                          + " | ".join(tail))
    return errors


# --------------------------------------------------------------- contracts

DEFAULT_DENY = ["git push", "rm -rf /", "pip publish", "npm publish"]
DEFAULT_OUT_OF_SCOPE = [".git/**", ".github/**", "deploy/**", "*.lock",
                        "**/.env", "**/secrets/**"]


DEFAULT_MAX_ACTIONS = 60          # build contracts: hook-enforced ceiling
DEFAULT_MAX_ACTIONS_RO = 40       # read-only review contracts


def build_contract(task: str, *, scope=None, read_only=False, write_allow=None,
                   tools=None, test_command=None, deny_extra=None,
                   max_actions=None) -> dict:
    """Build a contract dict — shared by tp.py new and the loop engine so a
    step's contract is exactly what the hook will enforce. Every contract
    carries an ACTION BUDGET (max_actions): the hook counts each governed
    tool call and blocks past the ceiling — mechanical, before the action
    runs, unlike dollar/token spend which stays cooperative."""
    import uuid
    if max_actions is None:
        max_actions = DEFAULT_MAX_ACTIONS_RO if read_only \
            else DEFAULT_MAX_ACTIONS
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
                    "require_clean_scope_diff": not read_only},
        },
    }
    if read_only:
        c["read_only"] = True
    if write_allow:
        c["write_allow"] = list(write_allow)
    return c


def budget_status(contract: dict, used_actions: int) -> tuple[bool, str]:
    """The action-budget RULE, owned by the kernel so every enforcement path
    applies the same ceiling. Returns (ok, reason). ok=False means the next
    action must be blocked BEFORE it runs. The CLI hook meters `used_actions`
    (stateful I/O) and forwards it here; the decision lives with the rest of
    the harness, not in the CLI, so no caller can enforce scope while quietly
    skipping the ceiling.

    Exhaustion is a HUMAN APPROVAL GATE: the block stands (the wall is
    intentional — a governed agent must not free itself), and the message
    tells the agent to escalate. The HUMAN / the ungoverned main session
    raises the ceiling with `tp.py budget --grant N` or ends the task with
    `tp.py clear`."""
    max_a = (contract.get("budget") or {}).get("max_actions")
    if not max_a:
        return True, "no action ceiling set"
    if used_actions >= int(max_a):
        return False, (f"ACTION BUDGET exhausted ({used_actions}/{max_a}) — "
                       "STOP and ask the human to approve more actions. The "
                       "human raises the ceiling by running, FROM A "
                       "DIRECTORY OUTSIDE this workspace (governance is keyed "
                       "on cwd, so a grant issued from inside is itself "
                       "blocked): `tp.py budget --grant N --workspace <ws>`, "
                       "or ends the task with `tp.py clear --workspace <ws>`. "
                       "You cannot grant yourself budget; do not retry.")
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


def _active_contract_path(workspace: str) -> str:
    return os.path.join(tp_dir(workspace), "active_contract.json")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError):
        return True   # exists (or unknowable) — treat as alive, stay governed
    except (ValueError, OSError):
        return True
    return True


DEFAULT_ORPHAN_TTL = 3600   # seconds of NO screening activity → orphaned


def orphan_status(workspace: str, contract: dict,
                  now: float | None = None) -> tuple[bool, str]:
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
        with open(os.path.join(tp_dir(workspace), "meter.json")) as f:
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
    if max_a and used >= int(max_a):
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
    try:
        ttl = float(contract.get("orphan_ttl_seconds")
                    or os.environ.get("TASKPLANE_ORPHAN_TTL")
                    or DEFAULT_ORPHAN_TTL)
    except (TypeError, ValueError):
        ttl = DEFAULT_ORPHAN_TTL
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
    b = c.setdefault("budget", {})
    old = b.get("max_actions")
    if not old:
        return None   # unmetered contract — nothing to grant against
    b["max_actions"] = int(old) + int(extra)
    # Atomic write: a live screener may load_active concurrently; a torn read
    # of the contract fails CLOSED (block), so the grant meant to UNBLOCK
    # could momentarily hard-block. temp + os.replace — same as _meter_bump /
    # loop.save.
    path = _active_contract_path(workspace)
    tmp = path + f".tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(c, f, indent=2)
    os.replace(tmp, path)
    trace(workspace, "budget_granted", extra=int(extra), old=int(old),
          new=b["max_actions"], task_id=c.get("task_id"))
    return c


def git_head(workspace: str) -> str | None:
    r = _run(["git", "rev-parse", "HEAD"], cwd=workspace)
    return r.stdout.strip() or None


def activate(workspace: str, contract: dict,
             snapshot: str | None = "auto") -> dict:
    """Write the active contract + snapshot so the PreToolUse hook enforces
    it. Returns the contract. snapshot='auto' records git HEAD."""
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
    with open(os.path.join(d, "active_contract.json"), "w") as f:
        json.dump(contract, f, indent=2)
    with open(os.path.join(d, "snapshot"), "w") as f:
        f.write(snapshot or "")
    trace(workspace, "contract_activated", task_id=contract.get("task_id"),
          task=contract.get("task"), read_only=bool(contract.get("read_only")),
          scope=contract.get("coding", {}).get("scope_paths"),
          write_allow=contract.get("write_allow"), snapshot=snapshot)
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


def clear(workspace: str) -> None:
    path = os.path.join(tp_dir(workspace), "active_contract.json")
    if os.path.exists(path):
        c = load_active(workspace) or {}
        safe_remove(path)
        trace(workspace, "contract_cleared", task_id=c.get("task_id"))


def snapshot_ref(workspace: str) -> str | None:
    p = os.path.join(tp_dir(workspace), "snapshot")
    if os.path.exists(p):
        with open(p) as f:
            return f.read().strip() or None
    return None


# --------------------------------------------------------------- state

def _ensure_self_ignored(d: str) -> None:
    """The runtime dir ignores itself — a worker's `git add -A` must never
    commit contracts/traces, and merges must never collide on them."""
    gi = os.path.join(d, ".gitignore")
    if os.path.isdir(d) and not os.path.exists(gi):
        try:
            with open(gi, "w") as f:
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

# Default tier -> model. Only `cheap` maps to a concrete model out of the box;
# `standard`/`deep` inherit the session model (None) so nothing is forced and
# behaviour is unchanged until an operator opts in. Override any tier via
# TASKPLANE_MODEL_CHEAP / _STANDARD / _DEEP (value "inherit" or "" => inherit).
_DEFAULT_TIER_MODEL = {"cheap": "haiku", "standard": None, "deep": None}


# --- dispatch verification (tier routing is only real if the driver passes
# the emitted model to the Agent tool; these queues make that checkable) ---

def _dispatch_path(workspace: str, name: str) -> str:
    return os.path.join(tp_dir(workspace), name)


def _load_queue(path: str) -> list:
    try:
        with open(path) as f:
            q = json.load(f)
        return q if isinstance(q, list) else []
    except (OSError, ValueError):
        return []


def _save_queue(path: str, q: list) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(q[-200:], f, indent=1)
    os.replace(tmp, path)


def record_expected_dispatch(workspace: str, kind: str, agent: str,
                             model_tier: str, model: str | None,
                             ref: str | None = None) -> None:
    """Called when a brief is emitted (`loop next` / `lens dispatch`): what
    agent SHOULD be dispatched next, on what model. A queue, not a scalar —
    a parallel wave emits many briefs with different tiers at once."""
    path = _dispatch_path(workspace, "expected_dispatch.json")
    q = _load_queue(path)
    q.append({"ts": _now(), "kind": kind, "agent": agent, "ref": ref,
              "model_tier": model_tier, "model": model, "matched": False})
    _save_queue(path, q)


def consume_expectation(workspace: str, agent: str) -> dict | None:
    """Oldest unmatched expectation for this agent short-name (host
    subagent_type arrives namespaced, e.g. `taskplane:tp-lens`)."""
    short = (agent or "").split(":")[-1]
    path = _dispatch_path(workspace, "expected_dispatch.json")
    q = _load_queue(path)
    for e in q:
        if not e.get("matched") and e.get("agent") == short:
            e["matched"] = True
            _save_queue(path, q)
            return e
    return None


def record_observed_dispatch(workspace: str, agent: str, model: str | None,
                             expected: dict | None, ok: bool) -> None:
    path = _dispatch_path(workspace, "observed_dispatch.json")
    q = _load_queue(path)
    q.append({"ts": _now(), "agent": (agent or "").split(":")[-1],
              "model": model, "ok": ok,
              "expected_model": expected and expected.get("model"),
              "expected_tier": expected and expected.get("model_tier"),
              "ref": expected and expected.get("ref")})
    _save_queue(path, q)


def dispatch_report(workspace: str) -> dict:
    """Audit: per emitted brief, did a dispatch with the right model land?
    This is the by-hand trace.jsonl analysis, mechanized."""
    exp = _load_queue(_dispatch_path(workspace, "expected_dispatch.json"))
    obs = _load_queue(_dispatch_path(workspace, "observed_dispatch.json"))
    mismatches = [o for o in obs if not o.get("ok")]
    unobserved = [e for e in exp if not e.get("matched")]
    return {"expected": len(exp), "observed": len(obs),
            "mismatches": mismatches, "unobserved": len(unobserved),
            "hook_active": bool(obs),
            "note": None if obs else
            "no dispatches observed — enable the check with "
            "TASKPLANE_ENFORCE_DISPATCH=warn|strict (PreToolUse Task hook)"}


def _now() -> float:
    import time
    return time.time()


# Effective tier per loop step when a task doesn't override it. Reasoning-heavy
# steps ask for `deep` (a no-op unless the operator points DEEP at a stronger
# model); build/verify steps stay `standard`. A planner marks an individual
# SIMPLE task `"model": "cheap"` in tasks.json to route just that task cheaper.
STEP_DEFAULT_TIER = {
    "pm": "deep", "plan": "deep", "em": "deep",
    "execute": "standard", "fix": "standard", "evaluate": "standard",
}


def model_for_tier(tier: str | None) -> str | None:
    """Resolve an abstract capability tier to a concrete model id for the Agent
    tool's `model` param, or None meaning "inherit the session model". Env
    TASKPLANE_MODEL_<TIER> overrides the default ("inherit"/"" => None). An
    unknown tier degrades to inherit (None) rather than raising, so a bad tier
    never blocks the loop."""
    t = (tier or "standard").strip().lower()
    env = os.environ.get("TASKPLANE_MODEL_" + t.upper())
    if env is not None:
        env = env.strip()
        return None if env in ("", "inherit") else env
    return _DEFAULT_TIER_MODEL.get(t)


def step_tier(step: str, task: dict | None = None) -> str:
    """The effective tier for a loop step: an explicit, valid per-task `model`
    tier wins; otherwise the step default (see STEP_DEFAULT_TIER). An invalid
    task tier is ignored (falls back to the step default)."""
    if task and task.get("model") in MODEL_TIERS:
        return task["model"]
    return STEP_DEFAULT_TIER.get(step, "standard")


def tp_dir(workspace: str) -> str:
    # Per-checkout RUNTIME (contracts, trace, meter, snapshot). Stays local
    # and git-ignored — parallel workers each need their own under .tp-work/,
    # and it must never be committed. The KNOWLEDGE base, by contrast, lives
    # in the external store below so it never rides along on `git add -A`.
    return os.path.join(workspace, ".taskplane")


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


def _path_slug(workspace: str) -> str:
    ap = os.path.abspath(workspace)
    return re.sub(r"[^A-Za-z0-9]+", "-", ap) or "-"


def project_key(workspace: str) -> str:
    """Stable, COLLISION-FREE per-project key: a readable path slug plus a
    short hash of the absolute path.

    The slug alone (the v0.9.6 scheme) collapses every run of non-alphanumerics
    to '-', so distinct projects whose paths differ only by punctuation —
    /x/my-app, /x/my_app, /x/my.app — all map to ONE key and silently share a
    store (KB, requirements, and loop.json — a gate in one corrupts the other).
    Appending an 8-char hash of the exact absolute path guarantees every
    distinct path gets its own store while keeping the slug human-readable."""
    ap = os.path.abspath(workspace)
    slug = _path_slug(workspace)
    return f"{slug}-{hashlib.sha1(ap.encode('utf-8')).hexdigest()[:8]}"


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
    ap = os.path.abspath(workspace)
    owns = True
    try:
        with open(os.path.join(legacy_root, "meta.json")) as f:
            owner = json.load(f).get("workspace")
        if owner and os.path.abspath(owner) != ap:
            owns = False        # belongs to a colliding sibling — don't steal
    except (OSError, ValueError):
        owns = True             # no/unreadable meta: the slug is ours to keep
    if owns:
        try:
            os.makedirs(os.path.dirname(new_root), exist_ok=True)
            shutil.move(legacy_root, new_root)
        except OSError:
            pass


def store_root(workspace: str) -> str:
    """This project's own store dir: <store_home>/projects/<key>/."""
    root = os.path.join(store_home(), "projects", project_key(workspace))
    if not os.path.isdir(root):
        _adopt_legacy_store(workspace, root)
    return root


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
    if os.path.isdir(ext):
        return ext
    legacy = os.path.join(workspace, "knowledge")
    if os.path.isdir(legacy):
        return legacy
    return ext


def store_meta_path(workspace: str) -> str:
    return os.path.join(store_root(workspace), "meta.json")


def write_store_meta(workspace: str) -> dict:
    """Record what this store belongs to — absolute workspace path and git
    remote — so the store is self-describing and a future collaboration/sync
    can map a shared KB back to its project. Idempotent."""
    root = store_root(workspace)
    os.makedirs(root, exist_ok=True)
    remote = _run(["git", "config", "--get", "remote.origin.url"],
                  cwd=workspace).stdout.strip() or None
    meta = {"key": project_key(workspace),
            "workspace": os.path.abspath(workspace),
            "git_remote": remote}
    try:
        with open(store_meta_path(workspace), "w") as f:
            json.dump(meta, f, indent=2)
    except OSError:
        pass
    return meta


def migrate_store(workspace: str) -> dict:
    """Relocate a legacy in-repo knowledge/ into the external store (data
    move only — no git ops; the CLI does the untrack + gitignore). Idempotent:
    a no-op once the external store exists. Returns what happened."""
    import shutil
    legacy = os.path.join(workspace, "knowledge")
    ext = os.path.join(store_root(workspace), "knowledge")
    moved = False
    if os.path.isdir(legacy) and not os.path.isdir(ext):
        os.makedirs(os.path.dirname(ext), exist_ok=True)
        shutil.move(legacy, ext)
        moved = True
    write_store_meta(workspace)
    return {"moved": moved, "store": ext, "legacy": legacy}


def load_active(workspace: str) -> dict | None:
    path = os.path.join(tp_dir(workspace), "active_contract.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def trace(workspace: str, event: str, **data) -> None:
    import time
    d = tp_dir(workspace)
    os.makedirs(d, exist_ok=True)
    _ensure_self_ignored(d)
    # Every record carries a monotonic wall-clock ts so the mission-control
    # feed can order events across parallel worker trace files by TIME, not
    # by which file they happened to be concatenated from.
    rec = {"event": event, "ts": time.time()}
    rec.update(data)
    try:
        with open(os.path.join(d, "trace.jsonl"), "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass
