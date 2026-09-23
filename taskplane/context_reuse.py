"""Conservative verification reuse with source, runtime and command provenance.

These records preserve observations, including failures. They never carry gates.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any

from . import depgraph, workflow as w, workflow_evidence as evidence
from .context import Store, digest

ENVIRONMENT = ("PATH", "LANG", "LC_ALL", "TZ", "PYTHONHASHSEED", "PYTHONPATH", "NODE_ENV", "CI")


def key(workspace: Path, *, paths: list[str], tests: list[str], criteria: list[str],
        command: list[str], tool: str, contract: str,
        environment: dict[str, str] | None = None) -> dict[str, Any]:
    """Unknown graph coverage is a cache miss, never a guessed dependency boundary."""
    graph = depgraph.load(str(workspace))
    complete = bool(graph) and depgraph.source_inputs_current(str(workspace), graph)
    quality = depgraph.scan_quality(graph) if graph else {"degraded": True}
    meta = graph.get("meta", {})
    complete = (complete and not quality.get("degraded") and quality.get("mode") == "components"
                and meta.get("source_coverage", {}).get("complete") is True
                and meta.get("module_confidence") != "low")
    modules = graph.get("modules", {})
    def module(path: str) -> str | None:
        matches = [name for name in modules if name == "." or path == name or path.startswith(name + "/")]
        return max(matches, key=len) if matches else None
    selected = {module(path) for path in paths + tests}
    complete = complete and None not in selected and bool(paths and tests and criteria and command and tool and contract)
    closure = set(selected) - {None}
    while True:
        expanded = closure | {edge.get(side) for edge in graph.get("edges", [])
                               for side in ("from", "to")
                               if edge.get("from") in closure or edge.get("to") in closure}
        if expanded == closure:
            break
        closure = expanded
    # A complete workspace scan is not complete runtime dependency coverage.
    # External nodes name packages, but do not bind their installed contents;
    # even an unchanged PYTHONPATH can point at a changed implementation.
    unverified_dependencies = sorted(str(name) for name in closure
                                    if name not in modules
                                    or modules[name].get("kind") == "external")
    complete = complete and not unverified_dependencies
    files = (set(paths + tests) | {path for path in graph.get("files", {}) if module(path) in closure}
             | set(graph.get("context_files", {})))
    fingerprints: dict[str, str] = {}
    for path in sorted(files):
        target = evidence.path(workspace, path)
        fingerprints[path] = hashlib.sha256(evidence.read(workspace, path)).hexdigest() if target.is_file() else "missing"
    executable = Path(command[0]) if command else Path("/nonexistent")
    complete = complete and executable.is_absolute() and executable.is_file()
    executable_hash = hashlib.sha256(executable.read_bytes()).hexdigest() if executable.is_file() else "unknown"
    # Only hashes of explicitly allowlisted nonsecret settings enter persisted data.
    env = environment if environment is not None else dict(os.environ)
    body = {"schema": "taskplane.verification-key/v1", "eligible": bool(complete),
            "paths": sorted(paths), "tests": sorted(tests),
            "files": fingerprints, "modules": sorted(str(x) for x in closure),
            "criteria": sorted(criteria), "command": command, "tool": tool, "contract": contract,
            "runtime": {"executable": str(executable), "sha256": executable_hash,
                        "python": sys.version, "platform": platform.platform(),
                        "taskplane": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in sorted(Path(__file__).parent.glob("*.py"))}},
            "environment": digest({name: env.get(name) for name in ENVIRONMENT}),
            "unverified_dependencies": unverified_dependencies,
            "coverage": "complete" if complete else "unknown"}
    return {**body, "digest": digest(body)}


def record(store: Store, provenance: dict[str, Any], *, status: str, producer: str,
           result: dict[str, Any], findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    w.require(status in {"pass", "fail", "unknown"} and bool(producer),
              "invalid_context", "Verification needs an observed status and producer.")
    w.require(status != "pass" or result.get("returncode") == 0,
              "invalid_context", "Passing verification requires a successful observed command.")
    body = {"schema": "taskplane.verification-record/v1", "key": provenance,
            "status": status, "producer": producer, "result": result,
            "findings": findings or [], "authority": "none"}
    return store.put("verification-record", body)


def lookup(store: Store, reference: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    prior = store.resolve(reference)
    w.require(isinstance(prior, dict) and prior.get("schema") == "taskplane.verification-record/v1",
              "invalid_context", "Invalid verification record.")
    eligible = (current.get("eligible") is True and prior.get("key") == current
                and prior.get("status") == "pass" and prior.get("result", {}).get("returncode") == 0)
    return {"status": "reused" if eligible else "miss", "prior_status": prior.get("status"),
            "producer": prior.get("producer"), "findings": prior.get("findings", []),
            "evidence_ref": reference, "key_digest": current.get("digest"), "approval": "not_transferred"}


def run_check(workspace: Path, provenance: dict[str, Any], *, producer: str,
              timeout: float = 120) -> dict[str, Any]:
    """Execute the explicitly supplied verification command once; no shell expansion."""
    def current() -> dict[str, Any]:
        return key(workspace, paths=provenance["paths"], tests=provenance["tests"],
                   criteria=provenance["criteria"], command=provenance["command"],
                   tool=provenance["tool"], contract=provenance["contract"])
    w.require(current() == provenance, "invalid_context", "Verification inputs changed before execution.")
    result = subprocess.run(provenance["command"], cwd=workspace, capture_output=True,
                            text=True, timeout=timeout, check=False,
                            env={name: os.environ[name] for name in ENVIRONMENT if name in os.environ})
    status = "pass" if result.returncode == 0 else "fail"
    if current() != provenance:
        status = "unknown"
    return record(Store(workspace), provenance, status=status,
                  producer=producer, result={"returncode": result.returncode,
                                            "stdout": result.stdout, "stderr": result.stderr})


def inherited(workspace: Path, reference: dict[str, Any]) -> dict[str, Any]:
    store = Store(workspace)
    prior = store.resolve(reference)
    saved = prior["key"]
    current = key(workspace, paths=saved["paths"], tests=saved["tests"],
                  criteria=saved["criteria"], command=saved["command"],
                  tool=saved["tool"], contract=saved["contract"])
    return lookup(store, reference, current)
