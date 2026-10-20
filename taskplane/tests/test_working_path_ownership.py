"""Current unsubmitted output claims have the same cross-root protection."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from taskplane import workflow as w, workflow_host as h
from taskplane.tests.test_run_workspace_isolation import request
from taskplane.tests.test_workflow_local import supported_build


@pytest.mark.parametrize("tool", ["write_file", "Write", "exec_command", "Bash"])
def test_foreign_unsubmitted_working_claim_blocks_write(tmp_path, tool):
    c, state = supported_build(tmp_path, "claude" if tool in {"Write", "Bash"} else "codex")
    db = json.loads(c._path().read_text())
    other = deepcopy(db)
    other["root"] = "other-root"
    active = other["runs"][other["active"]]
    active["root"] = "other-root"
    foreign = tmp_path / ".taskplane" / ("workflow-" + "f" * 32 + ".json")
    foreign.write_text(json.dumps(other))
    current = deepcopy(state)
    current["workflow_available"] = True
    c.adapter.name = "claude" if tool in {"Write", "Bash"} else "codex"
    # This output is not a source input sealed by the earlier Plan. Its claim
    # must be protected solely by the currently working Build phase.
    args = ({"cmd": "touch build.json"} if tool == "exec_command" else
            {"command": "touch build.json"} if tool == "Bash" else
            {"file_path": "build.json", "content": "overwrite"})
    with pytest.raises(w.Refusal, match="(?i)(another active|owned|claim|conflict)"):
        c._guard({"tool_name": tool, "tool_input": args}, current)
    active["retired"] = {"fixture": True}
    foreign.write_text(json.dumps(other))
    c._guard({"tool_name": tool, "tool_input": args}, current)


def test_competing_claims_are_atomic_and_explicit_retirement_releases(tmp_path):
    barrier = threading.Barrier(2)
    controllers = [h.Controller(tmp_path, root, h.installed_adapter("codex"))
                   for root in ("first", "second")]
    def start(controller):
        barrier.wait(timeout=10)
        try:
            return controller, controller.start(request())
        except w.Refusal as error:
            return controller, error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, controllers))
    winners = [(c, state) for c, state in results if isinstance(state, dict)]
    losers = [(c, error) for c, error in results if isinstance(error, w.Refusal)]
    assert len(winners) == len(losers) == 1
    owner, state = winners[0]
    assert "owns" in str(losers[0][1])
    owner.guard({"tool_name": "write_file", "tool_input": {"path": "product.json", "content": "owned"}}, state["run"])
    retired = owner.apply("retire", state["run"], expected_revision=state["revision"],
                          native_reference=json.dumps({"request_reference": "fixture/owner-release",
                                                       "reason": "Owner explicitly releases the active scope"}))
    acquired = losers[0][0].start(request())
    assert acquired["root"] != state["root"] and retired["retired"]
    assert owner.report(state["run"])["history"] == retired["history"]
