#!/usr/bin/env python3
"""Regenerate the three governed stage-wave transport goldens.

Run from the repo root — ONLY for a DELIBERATE dispatch-shape change
(regenerating to silence a red parity leg defeats the guard):

    python3 taskplane/tests/fixtures/briefs/regen.py

It drives the frozen stage-wave journey (stage_fixture.py —
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
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TASKPLANE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
PLUGIN_ROOT = os.path.dirname(TASKPLANE)
sys.path.insert(0, PLUGIN_ROOT)
sys.path[:0] = [TASKPLANE, HERE]  # direct modules + stage_fixture.py

# env scrub (see module docstring) — the capture must not inherit host state
for var in ("CODEX_HOME", "CODEX_THREAD_ID", "TASKPLANE_MODEL_CHEAP",
            "TASKPLANE_MODEL_STANDARD", "TASKPLANE_MODEL_DEEP",
            "TASKPLANE_REASONING_CHEAP", "TASKPLANE_REASONING_STANDARD",
            "TASKPLANE_REASONING_DEEP",
            "TASKPLANE_WORKFLOWS", "CLAUDE_CODE_WORKFLOWS",
            "TASKPLANE_TASK", "TASKPLANE_SESSION_ID"):
    os.environ.pop(var, None)

import stage_fixture  # noqa: E402  (lives next to this script)


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
# tokens; unix times zeroed; enforcement observations -> <TIME>; git shas,
# graph fingerprints and signed-local evidence -> <SHA>; dispatch intents and
# ReviewKernel leases/signatures -> stable tokens; calendar dates -> <DATE>;
# sorted keys; indent=2.
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
    regen_stage_goldens()


if __name__ == "__main__":
    main()
