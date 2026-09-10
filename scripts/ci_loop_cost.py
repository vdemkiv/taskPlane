#!/usr/bin/env python3
"""Check the H2 three-task cost target from one explicitly supplied run seal.

This consumer never starts a loop, guesses a store, or treats fixture counters
as a live-host measurement. The host CI job supplies its terminal receipt.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from taskplane import wave_metrics  # noqa: E402


def measure(receipt: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(receipt, Mapping) or receipt.get("schema") != wave_metrics.TERMINAL_RECEIPT_SCHEMA:
        raise ValueError("cost check requires the current terminal wave receipt")
    checked = wave_metrics.validate_wave_receipt(receipt)
    observed = checked["usage_truth"]["observed"]
    attempts = observed["attempts"]
    tasks = checked["evaluator_summary"]["evaluators"]
    coverage = observed["coverage"]
    roots = [row for row in attempts if row["thread_type"] == "main"]
    workers = [row for row in attempts if row["thread_type"] != "main"]
    errors = []
    if len({row["task"] for row in tasks}) != 3:
        errors.append("sample requires exactly three independently evaluated tasks")
    if any(row["status"] != "complete" or row["verdict"] not in {"pass", "fail"}
           or not row["identity_fingerprint"] for row in tasks):
        errors.append("every task requires a producer-bound evaluation")
    if coverage["status"] != "complete" or coverage["percent"] != 100:
        errors.append("sample requires measured usage for every dispatched attempt")
    if not roots or not workers or observed["total_tokens"] <= 0:
        errors.append("sample requires positive root and worker measurements")
    root_tokens = sum(row["total_tokens"] or 0 for row in roots)
    if root_tokens <= 0 or not all(row["receipt_fingerprint"] for row in attempts):
        errors.append("sample has missing root usage or terminal producer receipts")
    share = coverage["root_share_percent"]
    if share is None or share > 25:
        errors.append("root token share exceeds 25 percent or is unmeasured")
    return {
        "schema": "taskplane.loop-cost-check/v1",
        "status": "fail" if errors else "pass", "errors": errors,
        "receipt_fingerprint": checked["fingerprint"],
        "candidate_fingerprint": checked["run"]["candidate_fingerprint"],
        "evaluated_tasks": len(tasks), "measured_attempts": len(attempts),
        "coverage_percent": coverage["percent"], "root_share_percent": share,
        "root_share_limit_percent": 25,
        "provenance": "supplied terminal seal; host authenticity remains the producer's responsibility",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, help="exact terminal wave receipt from the host CI job")
    args = parser.parse_args(argv)
    try:
        if args.receipt is None:
            raise ValueError("live three-task receipt missing; supply --receipt PATH")
        if (not args.receipt.is_file() or args.receipt.is_symlink() or
                args.receipt.stat().st_size > 2 * 1024 * 1024):
            raise ValueError("receipt must be one bounded regular file")
        report = measure(json.loads(args.receipt.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report = {"schema": "taskplane.loop-cost-check/v1", "status": "fail",
                  "errors": [str(exc)], "root_share_percent": None}
    print(json.dumps(report, sort_keys=True, indent=2))
    return int(report["status"] != "pass")


if __name__ == "__main__":
    raise SystemExit(main())
