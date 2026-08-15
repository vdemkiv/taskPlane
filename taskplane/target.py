"""WHAT was reviewed — recorded as a fact, not as prose.

Two field runs against `aws/karpenter-provider-aws#9464` both cloned the
repository, and neither could prove it. `tp new` took the target as free
text; the contract recorded `task`, `read_only`, `write_allow`, `budget` —
nothing about origin, base, head, or how the code arrived. So both reports
had to state the workspace and the diff base in PROSE, by hand, and nothing
in the harness could tell the difference between a review of a checkout and
a review of a rendered web diff. Identical artifacts, identical gate.

That is the same hole every other one in this product turned out to be: a
claim nobody checked against the thing that settles it. Here the thing that
settles it is git.

  * `pin()` reads the checkout: origin, HEAD sha, base ref + sha, merge
    base, dirty files. No network, no guessing.
  * `fingerprint()` reduces that to one comparable value, so findings can
    CITE the tree they were derived from and a gate can check the citation.
  * `acquire()` fetches a pull request deterministically — the same two git
    commands every time, recorded — instead of leaving acquisition to
    whatever the agent improvised that day.
  * `tools()` answers whether `git` and `gh` are actually present, because
    a review of a REMOTE target needs PR metadata (title, body, linked
    issues, the comment thread) that is not in the git objects at all. In
    the field `gh` was missing and that fell back to unauthenticated web
    reads, silently.

Nothing here writes to the reviewed source, and nothing here is a gate on
its own: it produces the record that tp.py's screener and the sign-off gate
check. Enforcement stays where enforcement lives.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess

RECORD_NAME = "target.json"

# github.com/OWNER/REPO/pull/N, OWNER/REPO#N, #N
_PR_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?(?P<host>[\w.-]+)/(?P<owner>[\w.-]+)/"
    r"(?P<repo>[\w.-]+)/pull/(?P<number>\d+)/?$")
_PR_SHORT = re.compile(
    r"^(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)#(?P<number>\d+)$")
_BARE_NUM = re.compile(r"^#?(?P<number>\d+)$")


def git(root, *args, timeout=60):
    """Run git in `root`. Never raises; returns (rc, stdout)."""
    try:
        p = subprocess.run(["git", *args], cwd=root,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return p.returncode, (p.stdout or "").strip()


# ------------------------------------------------------------------- tools

def _version(prog, *args):
    try:
        p = subprocess.run([prog, *args], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return (p.stdout or "").strip().splitlines()[0] if p.stdout else ""


def tools() -> dict:
    """What this host can actually do with git and GitHub.

    `gh` is not a nicety. A pull request's title, body, linked issues and
    review conversation are NOT in the git objects — a clone gives you the
    code and none of the intent. Without `gh` that context either goes
    missing or arrives over unauthenticated web reads that no one recorded,
    which is exactly what happened in both field runs."""
    git_v = _version("git", "--version")
    gh_path = shutil.which("gh")
    gh_v = _version("gh", "--version") if gh_path else None
    authed = None
    if gh_path:
        try:
            p = subprocess.run(["gh", "auth", "status"],
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", errors="replace", timeout=20)
            authed = p.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            authed = None
    return {
        "git": {"present": bool(shutil.which("git")), "version": git_v,
                "path": shutil.which("git")},
        "gh": {"present": bool(gh_path), "version": gh_v, "path": gh_path,
               "authenticated": authed},
    }


def install_hint() -> str:
    """The exact command for THIS host. Deliberately delegated to the
    platform package manager: taskplane will not download and execute a
    binary from a release URL it cannot verify offline, and a hardcoded
    checksum that nobody maintains is a worse guarantee than the user's own
    trusted package source."""
    import sys as _sys
    if _sys.platform == "darwin":
        return "brew install gh"
    if os.name == "nt":
        return "winget install --id GitHub.cli"
    for mgr, cmd in (("apt-get", "sudo apt-get install -y gh"),
                     ("dnf", "sudo dnf install -y gh"),
                     ("pacman", "sudo pacman -S --noconfirm github-cli"),
                     ("apk", "sudo apk add github-cli"),
                     ("brew", "brew install gh")):
        if shutil.which(mgr):
            return cmd
    return "see https://github.com/cli/cli#installation"


def ensure_gh(*, run=True) -> dict:
    """Install `gh` through the platform package manager, if it is missing.

    EXPLICIT by design — nothing calls this behind your back. It shells the
    package manager already trusted on this host rather than fetching a
    tarball, so the trust decision stays where the user already made it.
    Returns what it did; never raises."""
    if shutil.which("gh"):
        return {"action": "already-present", "ok": True,
                "tools": tools()["gh"]}
    cmd = install_hint()
    if not run or cmd.startswith("see "):
        return {"action": "manual", "ok": False, "command": cmd}
    try:
        p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
        out = (p.stdout or "")[-2000:]
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"action": "failed", "ok": False, "command": cmd,
                "detail": e.__class__.__name__}
    ok = bool(shutil.which("gh"))
    return {"action": "installed" if ok else "failed", "ok": ok,
            "command": cmd, "detail": "" if ok else out.strip()[-400:],
            "tools": tools()["gh"] if ok else None}


# ------------------------------------------------------------------ parsing

def parse(spec) -> dict:
    """A target specification -> a structured target. Unrecognised input is
    a `ref` target, never an error: reviewing a branch or a range is as
    legitimate as reviewing a PR."""
    s = str(spec or "").strip()
    if not s:
        return {"kind": "local", "spec": ""}
    for rx, host in ((_PR_URL, None), (_PR_SHORT, "github.com")):
        m = rx.match(s)
        if m:
            g = m.groupdict()
            return {"kind": "pr", "spec": s,
                    "host": g.get("host") or host or "github.com",
                    "owner": g["owner"],
                    "repo": g["repo"][:-4] if g["repo"].endswith(".git")
                    else g["repo"],
                    "number": int(g["number"])}
    m = _BARE_NUM.match(s)
    if m:
        return {"kind": "pr", "spec": s, "host": "github.com",
                "owner": None, "repo": None, "number": int(m["number"])}
    return {"kind": "ref", "spec": s}


# --------------------------------------------------------------- pinning

def _dirty(root) -> list:
    rc, out = git(root, "status", "--porcelain")
    return [] if rc != 0 else [l for l in out.splitlines() if l.strip()][:50]


def pin(root: str, base: str | None = None, target: dict | None = None) -> dict:
    """Read what this checkout actually IS. No network, no inference."""
    rc, head = git(root, "rev-parse", "HEAD")
    if rc != 0 or not re.fullmatch(r"[0-9a-f]{7,40}", head or ""):
        return {"ok": False,
                "reason": f"{root} is not a git checkout with a commit — a "
                          f"review cannot be bound to a tree that does not "
                          f"exist"}
    _, origin = git(root, "remote", "get-url", "origin")
    _, branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    shallow_rc, shallow_value = git(
        root, "rev-parse", "--is-shallow-repository")
    shallow = (shallow_value == "true" if shallow_rc == 0 and
               shallow_value in {"true", "false"} else None)
    rec = {"ok": True, "root": os.path.abspath(root),
           "origin": origin or None, "head": head, "branch": branch or None,
           "dirty": _dirty(root), "shallow": shallow}
    if target:
        rec["target"] = target
    if base:
        rc, base_sha = git(root, "rev-parse", base)
        rc2, mb = git(root, "merge-base", base, "HEAD")
        rec["base_ref"] = base
        rec["base"] = base_sha if rc == 0 else None
        rec["merge_base"] = mb if rc2 == 0 else None
        if rec["base"]:
            _, files = git(root, "diff", "--name-only",
                           rec["merge_base"] or rec["base"], "HEAD")
            rec["changed_files"] = sorted(f for f in files.splitlines() if f)
    rec["fingerprint"] = fingerprint(rec)
    return rec


def fingerprint(rec: dict) -> str:
    """One comparable value for the checkout and its diff history. NOT
    including dirty state: a reviewer may create `.em-review/**` under its
    contract without invalidating the binding, and the dirty list is
    recorded separately for anyone who wants to judge it.

    Merge-base and shallow state are identity, not diagnostics. Deepening a
    checkout can make a previously impossible PR diff valid without moving
    HEAD or the base tip; keeping the old three-field key would let that
    failed review state masquerade as current after the repair."""
    h = hashlib.sha256()
    for k in ("origin", "base", "head", "merge_base", "shallow"):
        encoded = json.dumps(rec.get(k), sort_keys=True,
                             separators=(",", ":"))
        h.update(f"{k}={encoded}\n".encode("utf-8"))
    return h.hexdigest()[:16]


def review_cache_identity(rec: dict, graph: dict) -> dict:
    """The complete identity for reusable PR-review setup evidence."""
    row = rec if isinstance(rec, dict) else {}
    meta = (graph or {}).get("meta") if isinstance(graph, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    graph_revision = str(meta.get("content_fingerprint") or "")
    material = {
        "schema": "taskplane.review-cache-identity/v1",
        "head": str(row.get("head") or ""),
        "base": str(row.get("base") or ""),
        "merge_base": str(row.get("merge_base") or ""),
        "shallow": row.get("shallow"),
        "graph_revision": graph_revision,
    }
    material["fingerprint"] = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":")).encode(
            "utf-8")).hexdigest()
    return material


def _remote_identity(value: str | None) -> tuple[str, str, str] | None:
    """Parse comparable hosted-git remotes; local paths stay unclassified."""
    text = str(value or "").strip()
    patterns = (
        r"^(?:https?|ssh)://(?:[^@/]+@)?(?P<host>[\w.-]+)/"
        r"(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$",
        r"^(?:[^@/:]+@)?(?P<host>[\w.-]+):(?P<owner>[\w.-]+)/"
        r"(?P<repo>[\w.-]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            values = match.groupdict()
            return tuple(str(values[key]).lower()
                         for key in ("host", "owner", "repo"))
    return None


def _review_recovery(rec: dict, *, checkout: bool = False) -> str:
    target = rec.get("target") if isinstance(rec, dict) else {}
    target = target if isinstance(target, dict) else {}
    spec = str(target.get("spec") or "").strip()
    base = str(rec.get("base_ref") or "").strip()
    if target.get("kind") == "pr" and spec:
        command = f"tp review start {shlex.quote(spec)}"
        if base:
            command += f" --base {shlex.quote(base)}"
        return command + " --fetch"
    if checkout and spec:
        command = f"git checkout {shlex.quote(spec)} && tp review start " \
            f"{shlex.quote(spec)}"
        if base:
            command += f" --base {shlex.quote(base)}"
        return command
    command = "tp review start"
    if spec:
        command += f" {shlex.quote(spec)}"
    if base:
        command += f" --base {shlex.quote(base)}"
    return command


def review_preflight(root: str, rec: dict | None,
                     remote: str = "origin") -> dict:
    """Validate PR/ref setup before graph work or kernel state can exist."""
    row = rec if isinstance(rec, dict) else {}
    if not row.get("ok"):
        return {"ok": False, "status": "wrong_repository",
                "reason": str(row.get("reason") or
                              "review target is not a valid checkout"),
                "recovery": _review_recovery(row)}
    target = row.get("target") if isinstance(row.get("target"), dict) else {}
    target_kind = target.get("kind")

    if target_kind == "pr" and all(target.get(key)
                                    for key in ("host", "owner", "repo")):
        actual = _remote_identity(row.get("origin"))
        expected = tuple(str(target[key]).lower()
                         for key in ("host", "owner", "repo"))
        if actual is not None and actual != expected:
            return {
                "ok": False, "status": "wrong_repository",
                "reason": (f"origin is {actual[0]}/{actual[1]}/{actual[2]}, "
                           f"but the pull request belongs to "
                           f"{expected[0]}/{expected[1]}/{expected[2]}"),
                "recovery": _review_recovery(row),
            }

    if row.get("base_ref") and (not row.get("base") or
                                 not row.get("merge_base")):
        shallow = " in this shallow checkout" if row.get("shallow") else ""
        return {
            "ok": False, "status": "merge_base_missing",
            "reason": (f"no merge base exists for {row.get('base_ref')} "
                       f"and HEAD{shallow}; the canonical PR diff cannot be "
                       "derived"),
            "recovery": _review_recovery(row),
        }

    expected_sha = None
    if target_kind == "pr":
        resolved = resolve_pr_head(root, target, remote=remote)
        if resolved.get("ok"):
            expected_sha = resolved.get("sha")
    elif target_kind == "ref" and target.get("spec"):
        rc, value = git(root, "rev-parse", str(target["spec"]))
        if rc == 0 and re.fullmatch(r"[0-9a-f]{7,40}", value or ""):
            expected_sha = value
    if expected_sha and not _sha_match(row.get("head"), expected_sha):
        return {
            "ok": False, "status": "target_not_checked_out",
            "reason": (f"the requested review target is {expected_sha[:12]}, "
                       f"but this workspace is at "
                       f"{str(row.get('head') or '')[:12] or '(none)'}"),
            "recovery": _review_recovery(row, checkout=target_kind == "ref"),
        }

    if row.get("base_ref") and not (row.get("changed_files") or []):
        return {
            "ok": False, "status": "empty_diff",
            "reason": ("the canonical merge-base-to-HEAD diff is empty; "
                       "there is no reviewed change to route"),
            "recovery": _review_recovery(row, checkout=target_kind == "ref"),
        }
    return {
        "ok": True, "status": "ready", "reason": None, "recovery": None,
        "identity": {key: row.get(key) for key in
                     ("head", "base", "merge_base", "shallow")},
    }


# -------------------------------------------------------------- acquisition

def acquire(root: str, spec, *, base: str | None = None,
            remote: str = "origin") -> dict:
    """Fetch a pull request into an existing checkout and check it out.

    Two git commands, the same two every time, recorded — so "how did the
    code get here" has an answer that is not a reviewer's recollection."""
    t = parse(spec)
    if t["kind"] != "pr":
        return {"ok": False, "reason": f"not a pull-request target: {spec!r}",
                "target": t}
    rc, _ = git(root, "rev-parse", "--git-dir")
    if rc != 0:
        return {"ok": False, "target": t,
                "reason": f"{root} is not a git checkout — clone the "
                          f"repository first, then re-run"}
    n = t["number"]
    branch = f"tp-pr-{n}"
    steps = []
    rc, out = git(root, "fetch", remote, f"pull/{n}/head:{branch}",
                  timeout=600)
    steps.append({"cmd": f"git fetch {remote} pull/{n}/head:{branch}",
                  "rc": rc, "out": out[-400:]})
    if rc != 0:
        return {"ok": False, "target": t, "steps": steps,
                "reason": f"could not fetch pull/{n}/head from {remote} "
                          f"— is this the right repository, and is the "
                          f"network reachable?"}
    rc, out = git(root, "checkout", branch)
    steps.append({"cmd": f"git checkout {branch}", "rc": rc,
                  "out": out[-400:]})
    if rc != 0:
        return {"ok": False, "target": t, "steps": steps,
                "reason": "fetched the PR but could not check it out"}
    if not base:
        _, default = git(root, "symbolic-ref", "--quiet",
                         f"refs/remotes/{remote}/HEAD")
        base = default.rsplit("/", 1)[-1] if default else "main"
        base = f"{remote}/{base}"
    rec = pin(root, base=base, target=t)
    # A shallow clone can resolve both tips while still lacking their merge
    # base.  `--fetch` is the promised one-command recovery, so give the base
    # history one bounded deepen before the preflight reports the remaining
    # defect. Repeating the same explicit command can deepen another bounded
    # slice; taskplane never silently unshallows the whole repository.
    if rec.get("ok") and rec.get("shallow") and rec.get("base") and \
            not rec.get("merge_base"):
        base_fetch = str(base)
        for prefix in (f"refs/remotes/{remote}/", f"{remote}/"):
            if base_fetch.startswith(prefix):
                base_fetch = base_fetch[len(prefix):]
                break
        rc, out = git(root, "fetch", "--deepen=256", remote, base_fetch,
                      timeout=600)
        steps.append({
            "cmd": f"git fetch --deepen=256 {remote} {base_fetch}",
            "rc": rc, "out": out[-400:]})
        if rc == 0:
            rec = pin(root, base=base, target=t)
    rec["steps"] = steps
    rec["acquired"] = True
    return rec


# ------------------------------------------- is this the tree it claims to be

def resolve_pr_head(root: str, target: dict | None,
                    remote: str = "origin") -> dict:
    """The sha the remote serves for `refs/pull/N/head`, read not inferred.

    `refs/pull/N/head` is the pull request's OWN tip. Deliberately not
    `pull/N/merge` — that is GitHub's throwaway merge commit, and comparing
    a checkout against it would refuse every correctly checked-out pull
    request. Never raises, never touches the remote for a non-PR target."""
    t = target if isinstance(target, dict) else {}
    if t.get("kind") != "pr" or not t.get("number"):
        return {"ok": False, "sha": None, "remote": remote,
                "reason": "not a pull-request target"}
    ref = f"refs/pull/{int(t['number'])}/head"
    # A SHORT timeout on purpose. The answer is advisory — an unreachable
    # remote is not a wrong tree — so a sandbox with no network must pay
    # seconds for the question, not a minute before every review opens.
    rc, out = git(root, "ls-remote", remote, ref, timeout=30)
    sha = ""
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == ref:
            sha = parts[0].strip()
            break
    if rc != 0 or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return {"ok": False, "sha": None, "ref": ref, "remote": remote,
                "reason": f"could not resolve {ref} from {remote} — the "
                          f"remote may be unreachable, private, or not the "
                          f"repository this pull request belongs to"}
    return {"ok": True, "sha": sha, "ref": ref, "remote": remote}


def _sha_match(a, b) -> bool:
    """Abbreviations name the same commit. `git ls-remote` serves 40
    characters; a record may carry an abbreviated head, and treating that as
    a mismatch would refuse a correct tree."""
    a = str(a or "").strip().lower()
    b = str(b or "").strip().lower()
    if len(a) < 7 or len(b) < 7:
        return False
    return a.startswith(b) or b.startswith(a)


def wrong_tree(root: str, rec: dict | None,
               remote: str = "origin") -> str | None:
    """Why this checkout is not the pull request's head — or None.

    THE DEFECT THIS EXISTS FOR: a field run reviewed an 83-file working tree
    as a 4-file pull request. Contract activated, target pinned,
    fingerprinted, every obligation discharged, `steps.target ok: true`
    throughout — and seven deep lenses cited evidence from files the pull
    request never touches. The binding proved the findings came from the
    pinned tree; nothing proved the pinned tree was the pull request.

    Two deliberate non-refusals, because a refusal that fires when it should
    not is a refusal someone switches off:

      * a NON-PR target returns None WITHOUT touching the remote — reviewing
        a branch or a range is legitimate and must stay free;
      * an UNRESOLVABLE remote returns None. Offline is an environment fact,
        not a wrong tree.
    """
    r = rec if isinstance(rec, dict) else {}
    t = r.get("target")
    t = t if isinstance(t, dict) else {}
    if t.get("kind") != "pr":
        return None
    res = resolve_pr_head(root, t, remote=remote)
    want = res.get("sha") if res.get("ok") else None
    if not want:
        return None                     # advisory, never blocking
    have = r.get("head") or ""
    if _sha_match(have, want):
        return None
    owner, repo, n = t.get("owner"), t.get("repo"), t.get("number")
    label = (f"{owner}/{repo}#{n}" if owner and repo
             else f"pull request #{n}")
    return (f"this checkout is not the tree {label} is about: the pull "
            f"request's head ({res.get('ref')} on {remote}) is "
            f"{want[:12]}, but this workspace is at {have[:12] or '(none)'}. "
            f"A review of the wrong tree passes every gate — the run that "
            f"prompted this scored an 83-file working tree as a 4-file pull "
            f"request, with seven lenses citing files the pull request never "
            f"touched. Check the pull request out (`tp target fetch "
            f"{label}` or `tp review start {label} --fetch`) and re-run.")


def graph_problem(rec: dict | None, findings: dict | None) -> str | None:
    """Why the blast radius describes another revision — or None.

    The dependency graph is the one input a reviewer is told NOT to
    re-derive, so a graph scanned at a different head names files as they
    were somewhere else and the review that reads it looks exactly like a
    review that read the right one.

    FAILS OPEN ON ABSENCE. An older findings file carries no `scanned_head`
    at all; inventing a mismatch there would block reviews that are fine,
    which is how a refusal gets deleted rather than fixed."""
    meta = (findings or {}).get("meta") if isinstance(findings, dict) else None
    impact = meta.get("impact") if isinstance(meta, dict) else None
    graph = impact.get("graph") if isinstance(impact, dict) else None
    scanned = graph.get("scanned_head") if isinstance(graph, dict) else None
    scanned = str(scanned or "")[:12]
    head = str((rec or {}).get("head") or "")[:12] \
        if isinstance(rec, dict) else ""
    if not scanned or not head or scanned == head:
        return None
    return (f"the blast radius was read out of a dependency graph scanned at "
            f"{scanned}, but the reviewed tree is {head} — the impact names "
            f"modules as they were at another revision, and every lens that "
            f"cited it cited that revision. Re-run `tp graph scan` in the "
            f"reviewed checkout and recompute the impact.")


# ------------------------------------------------------------------ storage

def record_path(ws: str) -> str:
    import taskplane_lite as tp
    return os.path.join(tp.tp_dir(ws), RECORD_NAME)


def save(ws: str, rec: dict) -> dict:
    p = record_path(ws)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2, sort_keys=True)
    except OSError:
        pass
    return rec


def load(ws: str) -> dict | None:
    try:
        with open(record_path(ws), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


# -------------------------------------------------------------- the binding

def cited_fingerprint(findings: dict) -> str | None:
    """The fingerprint a findings document CITES, if any."""
    identity = (findings or {}).get("identity") or {}
    if isinstance(identity, dict) and identity.get("target_fingerprint"):
        return str(identity["target_fingerprint"])
    meta = (findings or {}).get("meta") or {}
    t = meta.get("target")
    if isinstance(t, dict):
        return t.get("fingerprint") or None
    return t if isinstance(t, str) and t else None


def canonical_identity(rec: dict) -> dict:
    """The one target tuple used by context, findings and projections."""
    row = rec if isinstance(rec, dict) else {}
    fp = str(row.get("fingerprint") or "").strip()
    if not fp:
        raise ValueError("target fingerprint is required")
    return {"target_fingerprint": fp,
            "target_head": str(row.get("head") or ""),
            "target_base": str(row.get("base") or "")}


def binding_problem(ws: str, findings: dict | None = None) -> str | None:
    """Why this review is not bound to a checkout — or None.

    Returns a REASON, never a decision. tp.py's screener and the sign-off
    gate decide what to do with it; keeping the judgement out of here is the
    same rule the rest of the harness follows."""
    rec = load(ws)
    if not rec or not rec.get("ok"):
        return ("this run is not bound to a reviewed tree: no target record "
                "in this workspace. Run `tp target pin --base <ref>` (or "
                "`tp target fetch <pr-url>`) in the checkout under review, "
                "so the findings name the commit they came from. A review "
                "nothing can tie to a tree is a review of nothing "
                "checkable.")
    if findings is None:
        return None
    cited = cited_fingerprint(findings)
    if not cited:
        return (f"the findings do not cite the reviewed tree. Copy the "
                f"target record into findings `meta.target` — this "
                f"workspace is pinned to {rec['fingerprint']} "
                f"({(rec.get('head') or '')[:9]}).")
    if cited != rec["fingerprint"]:
        return (f"the findings cite tree {cited}, but this workspace is "
                f"pinned to {rec['fingerprint']} "
                f"({(rec.get('head') or '')[:9]}) — they were derived from "
                f"a different checkout than the one being signed off.")
    # LAST, and here rather than beside it: `binding_problem` is what the
    # screener and the sign-off gate consult. A graph check they never call
    # is a mechanism that does not exist.
    return graph_problem(rec, findings)
