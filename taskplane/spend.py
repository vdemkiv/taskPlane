"""What a governed run actually COSTS, read from the host's transcript.

The action ceiling was the only budget this product had, and an action is a
terrible proxy for cost. Measured on one real review: 777k effective tokens
across 69 shell commands — about 11k per action — but the spread inside that
average is two orders of magnitude. `tp status` is a few hundred tokens; one
lens agent reading a large file is twenty-five thousand. The meter counted
them identically, so "raise the limit from 40 to 80" was never a fine-tune;
it was buying another ~440k tokens sight unseen.

Tokens are the thing that is actually scarce, and the host already writes
them down: every assistant message in the transcript JSONL carries a `usage`
block. This module reads it. Nothing here estimates, models, or predicts —
it sums what was recorded.

EFFECTIVE tokens, not raw. Cache reads are cheap and cache writes and output
are not, so a raw sum tells you almost nothing about cost: the same review
was ~22M raw and ~3.8M effective. The weights below are the ones the host's
own usage report uses, and they live in one place so a budget, a report and
a gate cannot disagree about what a token cost.

This is a CEILING input, never a source of truth about billing. It fails
OPEN in every direction — no transcript, an unreadable line, a missing usage
block — because a budget that blocks when its instrument breaks would make a
broken instrument into a broken product. The action ceiling still stands
underneath it.
"""
import json
import os

# Cache reads ×0.1, cache writes ×2, output ×5, plain input ×1.
WEIGHTS = {"input": 1.0, "cache_read": 0.1, "cache_write": 2.0, "output": 5.0}


def weigh(usage: dict) -> float:
    """One usage block -> effective tokens."""
    if not isinstance(usage, dict):
        return 0.0
    def n(*keys):
        for k in keys:
            v = usage.get(k)
            if isinstance(v, (int, float)):
                return float(v)
        return 0.0
    return (n("input_tokens") * WEIGHTS["input"]
            + n("cache_read_input_tokens") * WEIGHTS["cache_read"]
            + n("cache_creation_input_tokens") * WEIGHTS["cache_write"]
            + n("output_tokens") * WEIGHTS["output"])


def read_transcript(path: str) -> dict:
    """Sum a transcript's recorded usage. Never raises."""
    total, messages = 0.0, 0
    if not path or not os.path.isfile(path):
        return {"available": False, "reason": "no transcript at that path",
                "effective": 0, "messages": 0}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue          # a torn last line is normal, not fatal
                msg = row.get("message")
                usage = msg.get("usage") if isinstance(msg, dict) else None
                if usage is None:
                    usage = row.get("usage")
                if isinstance(usage, dict):
                    w = weigh(usage)
                    if w:
                        total += w
                        messages += 1
    except OSError as e:
        return {"available": False, "reason": e.__class__.__name__,
                "effective": 0, "messages": 0}
    return {"available": True, "effective": int(total), "messages": messages}


def event_transcript(event: dict) -> "str | None":
    """The transcript path a hook event names, under any of the keys hosts
    have used for it."""
    for k in ("transcript_path", "agent_transcript_path", "transcript"):
        v = (event or {}).get(k)
        if isinstance(v, str) and v:
            return v
    return None


def status(contract: dict, spent: int) -> tuple:
    """(ok, reason). A token ceiling is advisory-by-absence: a contract that
    sets none is governed by the action ceiling alone, exactly as before."""
    cap = (contract.get("budget") or {}).get("max_tokens")
    if cap is None:
        return True, "no token ceiling set"
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        return True, "unreadable token ceiling — ignored"
    if cap <= 0:
        return True, "no token ceiling set"
    if spent >= cap:
        return False, (
            f"TOKEN BUDGET exhausted ({spent:,}/{cap:,} effective tokens) — "
            f"STOP. This ceiling counts what the host recorded, weighted the "
            f"way cost actually falls (cache reads x0.1, cache writes x2, "
            f"output x5), so it tracks spend rather than tool-call count. A "
            f"human raises it from OUTSIDE this workspace: `tp.py budget "
            f"--grant-tokens N --workspace <ws>`, or ends the task with "
            f"`tp.py clear --workspace <ws>`. You cannot grant yourself "
            f"budget; do not retry.")
    return True, f"{spent:,}/{cap:,} effective tokens"


def cost_per_action(spent: int, actions: int) -> float:
    """What an action cost on this run. The number that makes the action
    ceiling legible: 11,261 on the measured review, not a constant."""
    return (float(spent) / actions) if actions else 0.0
