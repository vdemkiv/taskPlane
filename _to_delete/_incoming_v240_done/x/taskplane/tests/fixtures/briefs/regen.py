#!/usr/bin/env python3
"""Regenerate the frozen dispatch-payload goldens (t6, R-0002 Codex parity).

Run from the repo root — ONLY for a DELIBERATE dispatch-shape change
(regenerating to silence a red parity leg defeats the guard):

    python3 taskplane/tests/fixtures/briefs/regen.py

What it does:
  * freezes the routing input: `changed_files.json` = the workspace-relative
    file list of the checked-in fixture tree under `workspace/`;
  * captures `lens.dispatch_briefs(lens.route(<frozen files>))` as
    `golden_dispatch_routed.json` (the Codex Task-dispatch payload,
    contract:lens-brief) and the breadth="all" capture (deep briefs + the
    batched sweep brief) as `golden_dispatch_all.json`.

DETERMINISM / SCRUB RULES (the goldens must be identical on every machine):
  * env scrub — captured with CODEX_HOME, CODEX_THREAD_ID,
    TASKPLANE_MODEL_CHEAP/STANDARD/DEEP, TASKPLANE_WORKFLOWS and
    CLAUDE_CODE_WORKFLOWS unset, so tier->model resolution is the shipped
    default (cheap -> "haiku", standard/deep -> null on a claude host);
  * path scrub — the routing input is the workspace-RELATIVE file list; no
    absolute paths exist anywhere in the payload (asserted below);
  * no timestamps — the payload carries none (asserted below);
  * byte normalization — json.dumps(payload, indent=2, sort_keys=True),
    default ensure_ascii, trailing newline.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TASKPLANE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, TASKPLANE)

# env scrub (see module docstring) — the capture must not inherit host state
for var in ("CODEX_HOME", "CODEX_THREAD_ID", "TASKPLANE_MODEL_CHEAP",
            "TASKPLANE_MODEL_STANDARD", "TASKPLANE_MODEL_DEEP",
            "TASKPLANE_WORKFLOWS", "CLAUDE_CODE_WORKFLOWS"):
    os.environ.pop(var, None)

import lens  # noqa: E402

HEADER = """\
# GOLDEN — frozen Codex Task-dispatch payload (t6, R-0002 / contract:lens-brief).
# Any change to the dispatch-payload shape fails CI's parity leg even when the
# workflow path still works. Regenerate ONLY for a deliberate shape change:
#     python3 taskplane/tests/fixtures/briefs/regen.py
# Scrub rules (determinism): captured with CODEX_HOME/CODEX_THREAD_ID/
# TASKPLANE_MODEL_*/TASKPLANE_WORKFLOWS/CLAUDE_CODE_WORKFLOWS unset;
# workspace-relative paths only; no timestamps; sorted keys; indent=2.
"""


def tree_files(root):
    out = []
    for dirpath, dirs, names in os.walk(root):
        dirs.sort()
        for n in sorted(names):
            out.append(os.path.relpath(os.path.join(dirpath, n),
                                       root).replace(os.sep, "/"))
    return sorted(out)


def _assert_deterministic(payload):
    s = json.dumps(payload)
    assert "/tmp/" not in s and HERE not in s, "absolute path leaked"
    for k in ("timestamp", "time", "date"):
        assert f'"{k}"' not in s, f"nondeterministic field {k!r} leaked"


def write_golden(name, payload):
    _assert_deterministic(payload)
    with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
        f.write(HEADER)
        f.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {name}")


def main():
    files = tree_files(os.path.join(HERE, "workspace"))
    with open(os.path.join(HERE, "changed_files.json"), "w",
              encoding="utf-8") as f:
        f.write(json.dumps(files, indent=2, sort_keys=True) + "\n")
    print(f"wrote changed_files.json ({len(files)} files)")
    routed = lens.dispatch_briefs(lens.route(files), base="HEAD",
                                  max_actions=30)
    write_golden("golden_dispatch_routed.json", routed)
    everything = lens.dispatch_briefs(lens.route(files, breadth="all"),
                                      base="HEAD", max_actions=30)
    write_golden("golden_dispatch_all.json", everything)


if __name__ == "__main__":
    main()
