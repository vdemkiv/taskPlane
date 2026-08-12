#!/usr/bin/env python3
"""Every shipped version is tagged, and every tag names a real release.

Five releases — v2.5.0, v2.5.1, v2.6.0, v2.8.1, v2.8.2 — went out with no
tag at all, and nobody noticed for months, because tagging lived in a human's
memory of the release routine. Two CHANGELOG rows (v2.4.0, v2.3.0) name
versions that NO tree ever declared: their content shipped inside the next
version, so the row invents a release boundary that git cannot corroborate.
And a "dangling v2.8.0" was chased for an hour before turning out to be an
annotated tag OBJECT sha read as if it were a commit sha.

All three are the same failure: the release history was asserted in prose and
never checked against the only source that can settle it — the version the
manifests actually held, commit by commit, on the mainline.

So this is the check. The manifest history is the source of truth; tags and
the CHANGELOG are claims about it, and both are verified here.

  C1  every version the manifest held on the mainline has a `v<version>` tag
      (except the newest, which may be the release in flight — so at most ONE
      untagged release can ever exist, which is the whole point)
  C2  every release tag resolves to a commit reachable from the mainline
  C3  a tag's commit declares that version in its manifest
  C4  every CHANGELOG version was either shipped or is listed in NOT_SHIPPED
      with a reason
  C5  nothing in NOT_SHIPPED was actually shipped — an exemption that starts
      being true is a bypass, not an exemption
  C6  no `v*` tag names a version that never existed

Run: python3 scripts/ci_release_tags.py [--json]
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFESTS = (".codex-plugin/plugin.json", ".claude-plugin/plugin.json",
             ".claude-plugin/marketplace.json")

# Versions the CHANGELOG names that no tree ever declared. Each needs a
# reason a human wrote down, and C5 re-checks that the reason is still true.
NOT_SHIPPED = {
    "2.3.0": {
        "reason": "released in the SAME commit as v2.3.1 — the manifest went "
                  "2.2.1 -> 2.3.1 in one step and the commit subject says so "
                  "('v2.3.0 + v2.3.1'). The v2.3.0 tag deliberately points at "
                  "that shared commit.",
        "co_released_with": "2.3.1",
    },
    "2.4.0": {
        "reason": "never shipped as its own version. The work described in "
                  "the v2.4.0 CHANGELOG row landed inside v2.5.0 — the row "
                  "itself was ADDED by the commit that bumped the manifest to "
                  "2.5.0. No tree has ever declared 2.4.0, so no tag can "
                  "honestly point anywhere.",
        "co_released_with": "2.5.0",
    },
}

# Version numbers deliberately skipped — never bumped to, never released.
SKIPPED = {"2.7.2": "burned during the v2.7.x lens rewrite; never bumped to."}


def git(root, *args):
    p = subprocess.run(["git"] + list(args), cwd=root,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout.strip()


def vkey(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def mainline(root):
    """The branch releases ship from. Named explicitly so a topic branch
    cannot quietly redefine what 'released' means."""
    for ref in ("refs/remotes/origin/main", "refs/heads/main"):
        if git(root, "rev-parse", "--verify", "--quiet", ref)[0] == 0:
            return ref
    return None


def version_at(root, commit):
    for path in MANIFESTS:
        rc, blob = git(root, "show", f"{commit}:{path}")
        if rc != 0 or not blob:
            continue
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        v = data.get("version")
        if not v and isinstance(data.get("plugins"), list) and data["plugins"]:
            v = data["plugins"][0].get("version")
        if v:
            return v
    return None


def shipped_versions(root, ref):
    """version -> the first first-parent commit on the mainline whose
    manifest declares it. Only commits that TOUCH a manifest are read."""
    rc, out = git(root, "log", "--first-parent", "--reverse",
                  "--format=%H", ref, "--", *MANIFESTS)
    if rc != 0:
        return {}
    intro, seen = {}, set()
    for c in out.split():
        v = version_at(root, c)
        if v and v not in seen:
            seen.add(v)
            intro[v] = c
    return intro


def release_tags(root):
    """tag -> the COMMIT it names (annotated tags dereferenced). Reading the
    tag object's own sha as a commit is what produced the phantom
    'dangling v2.8.0'."""
    rc, out = git(root, "show-ref", "--tags", "-d")
    tags = {}
    if rc != 0:
        return tags
    for line in out.splitlines():
        sha, ref = line.split(" ", 1)
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            tags[name[:-3]] = sha           # dereferenced: always wins
        else:
            tags.setdefault(name, sha)
    return {k: v for k, v in tags.items() if k.startswith("v")}


def changelog_versions(root):
    path = os.path.join(root, "CHANGELOG.md")
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("| **v"):
                out.append(line.split("**")[1].lstrip("v"))
    return out


def audit(root=ROOT):
    ref = mainline(root)
    if ref is None:
        return {"ok": False, "unavailable": "no main branch found — fetch "
                "origin/main (CI needs fetch-depth: 0 and tags)"}
    intro = shipped_versions(root, ref)
    if not intro:
        return {"ok": False, "unavailable": "no manifest history reachable — "
                "a shallow clone cannot verify release tags"}
    tags = release_tags(root)
    if not tags:
        return {"ok": False, "unavailable": "no tags fetched — CI needs "
                "`fetch-tags: true` (a tagless clone cannot verify tags)"}

    problems = []
    newest = max(intro, key=vkey)

    for v in sorted(intro, key=vkey):
        name = "v" + v
        sha = tags.get(name)
        if sha is None:
            if v == newest:
                continue          # the release in flight; tagged after CI
            problems.append({
                "check": "C1", "version": v,
                "detail": f"shipped at {intro[v][:9]} on {ref} but has no "
                          f"{name} tag. Fix: git tag -a {name} "
                          f"{intro[v][:9]} -m \"{name}\" && git push origin "
                          f"{name}"})
            continue
        if git(root, "merge-base", "--is-ancestor", sha, ref)[0] != 0:
            problems.append({
                "check": "C2", "version": v,
                "detail": f"{name} -> {sha[:9]} is NOT reachable from {ref}"})
            continue
        declared = version_at(root, sha)
        if declared != v and v not in NOT_SHIPPED:
            problems.append({
                "check": "C3", "version": v,
                "detail": f"{name} -> {sha[:9]}, whose manifest declares "
                          f"{declared!r}, not {v!r}"})

    # The version the WORKING TREE declares is the release in flight: its
    # CHANGELOG row is written before the bump reaches the mainline, which
    # is the correct order. C1 already tolerates the newest mainline
    # version being untagged for the same reason; this is that rule's twin
    # for C4. It exempts exactly one version — the one on disk right now —
    # so a CHANGELOG row for a version nobody is preparing still fails.
    in_flight = None
    try:
        with open(os.path.join(root, MANIFESTS[0]), encoding="utf-8") as f:
            in_flight = json.load(f).get("version")
    except (OSError, ValueError):
        in_flight = None

    shipped = set(intro)
    for v in changelog_versions(root):
        if v in shipped or v in NOT_SHIPPED or v == in_flight:
            continue
        problems.append({
            "check": "C4", "version": v,
            "detail": f"CHANGELOG names v{v} but no tree ever declared it. "
                      f"Either it shipped (and this check is wrong) or it "
                      f"needs a NOT_SHIPPED entry saying where it went."})

    for v, info in NOT_SHIPPED.items():
        if v in shipped:
            problems.append({
                "check": "C5", "version": v,
                "detail": f"v{v} IS in the manifest history ({intro[v][:9]}) "
                          f"but sits in NOT_SHIPPED — remove the exemption "
                          f"and tag it."})

    known = shipped | set(NOT_SHIPPED)
    for name in tags:
        v = name[1:]
        if v not in known:
            problems.append({
                "check": "C6", "version": v,
                "detail": f"tag {name} names a version no tree ever declared"})

    return {"ok": not problems, "mainline": ref, "newest": newest,
            "in_flight": in_flight,
            "shipped": {v: intro[v] for v in sorted(intro, key=vkey)},
            "tags": {k: tags[k] for k in sorted(tags, key=lambda t: vkey(t[1:]))},
            "not_shipped": sorted(NOT_SHIPPED), "skipped": sorted(SKIPPED),
            "problems": problems}


def main():
    res = audit()
    if "--json" in sys.argv:
        print(json.dumps(res, indent=2, sort_keys=True))
        return 0 if res.get("ok") else 1
    if res.get("unavailable"):
        print(f"release tags: CANNOT VERIFY — {res['unavailable']}")
        return 1
    print(f"release tags: {len(res['shipped'])} shipped version(s) on "
          f"{res['mainline']}, {len(res['tags'])} tag(s)")
    print(f"  newest ({res['newest']}) may be untagged — the release in "
          f"flight; every older one must be tagged")
    if res["not_shipped"]:
        print(f"  never shipped as own version: "
              f"{', '.join('v' + v for v in res['not_shipped'])} "
              f"(reasons in NOT_SHIPPED)")
    if res["skipped"]:
        print(f"  version numbers skipped: "
              f"{', '.join('v' + v for v in res['skipped'])}")
    for p in res["problems"]:
        print(f"  {p['check']} v{p['version']}: {p['detail']}")
    print("ok: every shipped version is tagged and every tag names a real "
          "release" if res["ok"] else
          f"FAIL: {len(res['problems'])} release-tag problem(s)")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
