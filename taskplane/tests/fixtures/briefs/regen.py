#!/usr/bin/env python3
"""Regenerate the frozen dispatch-payload goldens (t6, R-0002 Codex parity;
extended by t4, R-0004, to the three stage waves).

Run from the repo root — ONLY for a DELIBERATE dispatch-shape change
(regenerating to silence a red parity leg defeats the guard):

    python3 taskplane/tests/fixtures/briefs/regen.py

What it does:
  * freezes the routing input: `changed_files.json` = the workspace-relative
    file list of the checked-in fixture tree under `workspace/`;
  * captures `lens.dispatch_briefs(lens.route(<frozen files>))` as
    `golden_dispatch_routed.json` (the Codex Task-dispatch payload,
    contract:lens-brief) and the breadth="all" capture (deep briefs + the
    batched sweep brief) as `golden_dispatch_all.json`;
  * t4 (R-0004): drives the frozen stage-wave journey (stage_fixture.py —
    the SAME module the parity tests replay through) in a throwaway git
    workspace and captures the Task-path stdout of the three governed
    stage dispatches — `loop wave` (execute) and `loop next`
    (evaluate/fix) — as `golden_stage_execute.json`,
    `golden_stage_evaluate.json` and `golden_stage_fix.json`, after the
    documented scrub (stage_fixture module docstring: env scrub, <WS>/
    <STORE>/<PLUGIN> path tokens, zeroed unix times, <SHA> for git shas
    and graph fingerprints, <DATE> for calendar dates, sorted keys).

DETERMINISM / SCRUB RULES (the goldens must be identical on every machine):
  * env scrub — captured with CODEX_HOME, CODEX_THREAD_ID,
    TASKPLANE_MODEL_CHEAP/STANDARD/DEEP,
    TASKPLANE_REASONING_CHEAP/STANDARD/DEEP, TASKPLANE_WORKFLOWS and
    CLAUDE_CODE_WORKFLOWS unset, so model and reasoning resolution use the
    shipped defaults;
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
PLUGIN_ROOT = os.path.dirname(TASKPLANE)
sys.path.insert(0, TASKPLANE)
sys.path.insert(0, HERE)          # stage_fixture.py lives next to this file

# env scrub (see module docstring) — the capture must not inherit host state
for var in ("CODEX_HOME", "CODEX_THREAD_ID", "TASKPLANE_MODEL_CHEAP",
            "TASKPLANE_MODEL_STANDARD", "TASKPLANE_MODEL_DEEP",
            "TASKPLANE_REASONING_CHEAP", "TASKPLANE_REASONING_STANDARD",
            "TASKPLANE_REASONING_DEEP",
            "TASKPLANE_WORKFLOWS", "CLAUDE_CODE_WORKFLOWS",
            "TASKPLANE_TASK"):
    os.environ.pop(var, None)

import lens  # noqa: E402
import stage_fixture  # noqa: E402  (lives next to this script)

HEADER = """\
# GOLDEN — frozen Codex Task-dispatch payload (t6, R-0002 / contract:lens-brief).
# Any change to the dispatch-payload shape fails CI's parity leg even when the
# workflow path still works. Regenerate ONLY for a deliberate shape change:
#     python3 taskplane/tests/fixtures/briefs/regen.py
# Scrub rules (determinism): captured with CODEX_HOME/CODEX_THREAD_ID/
# TASKPLANE_MODEL_*/TASKPLANE_REASONING_*/TASKPLANE_WORKFLOWS/
# CLAUDE_CODE_WORKFLOWS unset;
# plugin root -> <PLUGIN>; workspace-relative inputs; no timestamps;
# sorted keys; indent=2.
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


def scrub(payload):
    """Replace the emitted absolute role-instruction root portably."""
    if isinstance(payload, str):
        # Either separator shape: the emitted role path is '/'-shaped on
        # every host now, while PLUGIN_ROOT is host-shaped.
        root = PLUGIN_ROOT.rstrip("/\\")
        for cand in (root, root.replace("\\", "/")):
            if payload.startswith(cand):
                suffix = payload[len(cand):].lstrip("/\\").replace("\\", "/")
                return "<PLUGIN>/" + suffix if suffix else "<PLUGIN>"
        return (payload.replace(root, "<PLUGIN>")
                .replace(root.replace("\\", "/"), "<PLUGIN>"))
    if isinstance(payload, list):
        return [scrub(item) for item in payload]
    if isinstance(payload, dict):
        return {key: scrub(value) for key, value in payload.items()}
    return payload


def write_golden(name, payload):
    payload = scrub(payload)
    _assert_deterministic(payload)
    with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
        f.write(HEADER)
        f.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {name}")


STAGE_HEADER = """\
# GOLDEN — frozen stage-wave Task-dispatch payload (t4, R-0004 /
# contract:wave-workflow). The Task path is the MANDATORY byte-identical
# fallback and the only Codex path: any change to a stage dispatch payload
# fails CI's stage parity leg even when the workflow path still works.
# Regenerate ONLY for a deliberate shape change:
#     python3 taskplane/tests/fixtures/briefs/regen.py
# Scrub rules (stage_fixture.py docstring): env scrub (CODEX_*/
# TASKPLANE_MODEL_*/TASKPLANE_REASONING_*/TASKPLANE_WORKFLOWS/
# CLAUDE_CODE_WORKFLOWS/TASKPLANE_TASK unset); <WS>/<STORE>/<PLUGIN> path
# tokens; unix times
# zeroed; git shas + graph fingerprints -> <SHA>; calendar dates ->
# <DATE>; sorted keys; indent=2.
"""


def write_stage_golden(name, body):
    stage_fixture.assert_deterministic(body, name)
    with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
        f.write(STAGE_HEADER)
        f.write(body)
    print(f"wrote {name}")


def regen_stage_goldens():
    """t4: capture the three stage-wave Task-path payloads through the
    frozen journey (stage_fixture.py) with an isolated external store."""
    import tempfile
    prior_home = os.environ.get("TASKPLANE_HOME")
    os.environ["TASKPLANE_HOME"] = tempfile.mkdtemp(prefix="tp-regen-store-")
    try:
        ws = stage_fixture.build_repo(tempfile.mkdtemp(prefix="tp-regen-ws-"))
        captures = stage_fixture.journey(ws)
        for stage in stage_fixture.STAGES:
            write_stage_golden(stage_fixture.GOLDENS[stage],
                               stage_fixture.scrubbed_bytes(captures[stage],
                                                            ws))
    finally:
        if prior_home is None:
            os.environ.pop("TASKPLANE_HOME", None)
        else:
            os.environ["TASKPLANE_HOME"] = prior_home


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
    regen_stage_goldens()


if __name__ == "__main__":
    main()
