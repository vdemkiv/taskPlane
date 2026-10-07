#!/usr/bin/env python3
"""Read-only inspection of actual Workflow Builder acceptance evidence.

This verifies cooperative file/transcript evidence, not host attestation. Test
fixtures exercise the inspector; a passing fixture is never WFB-LIVE evidence.
See docs/workflow-builder.md for the evidence-index and byte-slice contract.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import stat
import sys
from typing import Any

SCHEMA = "taskplane.workflow-builder-live-evidence/v1"
LIMIT = 32 * 1024 * 1024
TOTAL_LIMIT = 256 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}\Z")
CONTEXT_PAGE_ITEMS = 64
CONTEXT_PAGE_LIMIT = 16 * 1024


class EvidenceError(ValueError):
    pass


def require(condition: Any, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def strict_json(raw: bytes | str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON key: " + key)
            result[key] = value
        return result
    def invalid(value):
        raise EvidenceError("Non-finite JSON number: " + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def objects(value: Any):
    """Decode structured command output, never promote it to native events."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)
    elif isinstance(value, str):
        try:
            decoded = strict_json(value)
        except (ValueError, UnicodeError):
            return
        if not isinstance(decoded, str):
            yield from objects(decoded)


class Reader:
    def __init__(self, base: Path):
        self.base, self.total = base, 0

    def path(self, value: str) -> Path:
        require(isinstance(value, str) and value, "Missing evidence path")
        path = Path(value)
        path = path if path.is_absolute() else self.base / path
        # No symlink aliasing of evidence. Paths may be outside the evidence
        # directory because installed packages and native transcripts live there.
        for part in (path, *path.parents):
            require(not part.is_symlink(), "Symlink evidence is unsupported: " + str(part))
        return path

    def raw(self, ref: dict[str, Any]) -> bytes:
        require(isinstance(ref, dict) and set(ref) <= {"path", "sha256", "offset", "bytes"},
                "Expected a path/sha256 evidence reference")
        require(isinstance(ref.get("sha256"), str) and SHA.fullmatch(ref["sha256"]),
                "Evidence needs a SHA-256 fingerprint")
        path = self.path(ref.get("path"))
        info = path.stat()
        require(stat.S_ISREG(info.st_mode), "Evidence must be a regular file: " + str(path))
        offset = ref.get("offset", 0)
        size = ref.get("bytes", info.st_size - offset)
        require(type(offset) is int and type(size) is int and offset >= 0 and 0 < size <= LIMIT
                and offset + size <= info.st_size, "Evidence slice is missing, oversized or out of bounds")
        self.total += size
        require(self.total <= TOTAL_LIMIT, "Evidence exceeds total read budget; select native record slices")
        with path.open("rb") as stream:
            stream.seek(offset)
            raw = stream.read(size)
        require(digest(raw) == ref["sha256"], "Stale evidence SHA-256: " + str(path))
        return raw

    def json(self, ref):
        return strict_json(self.raw(ref))

    def pinned(self, path: Path, sha: str) -> bytes:
        return self.raw({"path": str(path), "sha256": sha})

    def transcript(self, ref):
        refs = ref.get("segments") if isinstance(ref, dict) and set(ref) == {"segments"} else [ref]
        require(isinstance(refs, list) and 0 < len(refs) <= 128, "Invalid native transcript segments")
        rows = []
        for item in refs:
            path = self.path(item.get("path"))
            offset = item.get("offset", 0)
            if offset:
                require(type(offset) is int and offset > 0, "Invalid transcript offset")
                with path.open("rb") as stream:
                    stream.seek(offset - 1)
                    require(stream.read(1) == b"\n", "Transcript slice starts inside a native record")
            raw = self.raw(item)
            require(raw.endswith(b"\n") or offset + len(raw) == path.stat().st_size,
                    "Transcript slice ends inside a native record")
            # Each selected byte range must contain whole original JSONL rows.
            for line in raw.splitlines():
                if line.strip():
                    row = strict_json(line)
                    require(isinstance(row, dict), "Native transcript row must be an object")
                    rows.append(row)
        return Transcript(rows)


class Transcript:
    """Recognize native Claude and Codex frames, not quoted assistant claims."""
    def __init__(self, rows):
        self.rows = rows
        self.calls, self.results = {}, {}
        for row in rows:
            if row.get("type") in {"assistant", "user"}:
                message = row.get("message", {})
                blocks = message.get("content", []) if isinstance(message, dict) else []
                for block in blocks if isinstance(blocks, list) else []:
                    if not isinstance(block, dict):
                        continue
                    if row["type"] == "assistant" and block.get("type") == "tool_use":
                        self.calls[block["id"]] = (block.get("name"), block.get("input"), row)
                    elif row["type"] == "user" and block.get("type") == "tool_result":
                        self.results[block["tool_use_id"]] = (block.get("content"), block.get("is_error") is True, row)
            if row.get("type") == "response_item":
                payload = row.get("payload", {})
                if payload.get("type") in {"function_call", "custom_tool_call"}:
                    self.calls[payload["call_id"]] = (payload.get("name"), payload.get("arguments", payload.get("input")), row)
                elif payload.get("type") in {"function_call_output", "custom_tool_call_output"}:
                    self.results[payload["call_id"]] = (payload.get("output"), False, row)

    def pairs(self):
        for key, (name, args, row) in self.calls.items():
            result = self.results.get(key)
            if result and not result[1]:
                yield key, name, args, result[0], row, result[2]

    def command(self, words, *, after=None):
        matches = []
        for pair in self.pairs():
            _, name, args, output, row, _ = pair
            if name not in {"Bash", "exec", "functions.exec", "exec_command", "functions.exec_command"}:
                continue
            command = args.get("command", args.get("cmd", "")) if isinstance(args, dict) else str(args)
            if all(word in command for word in words) and (not after or row.get("timestamp", "") >= after):
                if not any(obj.get("exit_code") not in (None, 0) or obj.get("isError") is True for obj in objects(output)):
                    matches.append(pair)
        return matches

    def values(self, pairs):
        return [obj for pair in pairs for obj in objects(pair[3])]

    def record(self, sha):
        return next((row for row in self.rows if digest(row) == sha), None)


def state_from(value, run):
    state = value.get("runs", {}).get(run) if "runs" in value else value
    require(isinstance(state, dict) and state.get("run") == run, "Controller does not contain the selected run")
    return state


def within(root: Path, relative: str) -> Path:
    require(isinstance(relative, str) and relative and not Path(relative).is_absolute()
            and ".." not in Path(relative).parts, "Invalid workspace-relative evidence path")
    return root / relative


def manifest(reader, root, values):
    require(isinstance(values, dict) and values, "Missing artifact/input manifest")
    for relative, sha in values.items():
        path = within(root, relative)
        if isinstance(sha, dict):
            require(sha.get("schema") == "taskplane.task-definitions/v1", "Unsupported semantic manifest entry")
            path = reader.path(str(path))
            require(path.is_file() and path.stat().st_size <= LIMIT, "Oversized task manifest")
            raw = path.read_bytes()
            reader.total += len(raw)
            require(reader.total <= TOTAL_LIMIT, "Evidence exceeds total read budget")
            data = strict_json(raw)
            normalized = {**data, "tasks": [{k:v for k,v in row.items() if k not in {
                "status", "started_at", "completed_at", "updated_at", "elapsed_seconds"}} for row in data["tasks"]]}
            require(digest(normalized) == sha.get("digest"), "Stale semantic task manifest")
        else:
            reader.pinned(path, sha)


def launch(reader, ref, root, flag):
    transcript = reader.transcript(ref)
    for pair in transcript.pairs():
        args = json.dumps(pair[2], ensure_ascii=False) if not isinstance(pair[2], str) else pair[2]
        # This is an actual parent execution tool frame and returned native
        # command/session result, not an invented launcher attestation.
        if (pair[1] in {"exec", "functions.exec", "exec_command", "functions.exec_command"}
                and flag in args and root in args
                and re.search(r'["\']?tty["\']?\s*:\s*true', args.replace('\\"', '"'))
                and any(obj.get("session_id") is not None or obj.get("exit_code") == 0 for obj in objects(pair[3]))):
            return pair
    raise EvidenceError("Missing actual TTY tool call/result for " + flag + " " + root)


def package(reader, item, state, definition):
    compilation = reader.json(item["compilation"])
    require(compilation.get("schema") == "taskplane.workflow-compilation/v1", "Wrong compilation schema")
    core = {k: v for k, v in compilation.items() if k != "digest"}
    require(digest(core) == compilation.get("digest"), "Compilation digest mismatch")
    require(compilation.get("definition_digest") == digest(definition), "Saved definition changed between invocations")
    root = Path(item["workspace"])
    folder = within(root, compilation["package_path"])
    require(reader.path(item["compilation"]["path"]).resolve() == folder / "compilation.json",
            "Compilation must reference the actual native package")
    members = compilation["members"]
    require({"definition.json", "bindings.json", "capabilities.json", "tasks.json"} <= set(members),
            "Incomplete compiled package")
    contents = {name: reader.pinned(within(folder, name), sha) for name, sha in members.items()}
    require(strict_json(contents["definition.json"]) == definition, "Package definition differs from saved definition")
    bindings = strict_json(contents["bindings.json"])
    require(digest(bindings) == compilation["binding_digest"] and bindings.get("workspace") == str(root)
            and bindings.get("status") == "complete" and not bindings.get("unresolved_inputs"), "Invalid bound inputs")
    binding = state.get("scope", {}).get("workflow_binding", {})
    for key in ("package_path", "definition_digest", "runtime_digest", "compiler_digest", "capability_digest"):
        require(binding.get(key) == compilation.get(key), "Controller/package binding mismatch: " + key)
    require(binding.get("package_digest") == compilation["digest"], "Foreign package in controller")
    for relative, row in bindings["source_manifest"].items():
        path = within(root, relative) if row["kind"] == "working" else within(root, compilation["snapshots"][relative])
        reader.pinned(path, row["sha256"])
    capabilities = strict_json(contents["capabilities.json"])
    runtime = capabilities["runtime"]
    require(runtime.get("compatible") is True and not runtime.get("blockers")
            and digest({k: v for k, v in runtime.items() if k != "digest"}) == runtime.get("digest")
            and runtime["digest"] == compilation["runtime_digest"], "Invalid pinned runtime identity")
    require(runtime.get("modules") and runtime.get("native_member_sha256"), "Runtime member evidence is absent")
    runtime_root = Path(runtime["runtime_root"])
    for relative, row in runtime["modules"].items():
        require(Path(row["path"]) == within(runtime_root, relative) and SHA.fullmatch(row.get("loaded_code_sha256", "")),
                "Loaded runtime module identity missing")
        reader.pinned(Path(row["path"]), row["sha256"])
    manifest(reader, runtime_root, runtime["native_member_sha256"])
    reader.pinned(Path(runtime["interpreter"]["path"]), runtime["interpreter"]["sha256"])
    return compilation, bindings, runtime


class ContextTree:
    """Read-only CAS reader; never trust a receipt as proof of model delivery."""
    def __init__(self, reader, workspace):
        self.reader, self.workspace, self.nodes = reader, workspace, {}

    def node(self, ref):
        key = ref if isinstance(ref, str) else ref.get("sha256")
        require(isinstance(key, str) and SHA.fullmatch(key), "Invalid context object digest")
        if key not in self.nodes:
            require(len(self.nodes) < 20000, "Context tree exceeds node budget")
            raw = self.reader.pinned(self.workspace / ".taskplane/context-v1/objects" / (key + ".json"), key)
            node = strict_json(raw)
            require(digest(node) == key and node.get("schema") == "taskplane.context-object/v1"
                    and node.get("form") in {"value", "dict", "list", "text", "entries", "groups",
                        "dict-groups", "list-groups", "text-groups", "dict-chunks", "list-chunks",
                        "dict-chunks-groups", "list-chunks-groups"}, "Invalid canonical context object")
            self.nodes[key] = (node, len(raw))
        node, size = self.nodes[key]
        if isinstance(ref, dict):
            require(ref == {"schema": "taskplane.context-reference/v1", "kind": node["kind"],
                           "sha256": key, "bytes": size, "source_key": node["source_key"]},
                    "Context reference metadata differs from canonical bytes")
        return node

    def children(self, node):
        if node["form"] == "value":
            return []
        if node["form"] == "dict":
            return [entry[1] for entry in node["data"]]
        if node["form"] == "entries":
            return [entry[1] if isinstance(entry, list) else entry for entry in node["data"]]
        return node["data"]

    def descendants(self, ref):
        seen, pending = set(), [(ref, 0)]
        while pending:
            current, depth = pending.pop()
            require(depth <= 32, "Context tree exceeds depth budget")
            node = self.node(current)
            key = current["sha256"]
            if key not in seen:
                seen.add(key)
                pending.extend((child, depth + 1) for child in self.children(node))
        return seen

    def resolve(self, ref, depth=0, budget=None):
        budget = [20000, 128 * 1024 * 1024] if budget is None else budget
        require(depth <= 32 and budget[0] > 0 and budget[1] > 0, "Context tree exceeds expansion budget")
        node = self.node(ref)
        key = ref if isinstance(ref, str) else ref["sha256"]
        budget[0] -= 1
        budget[1] -= self.nodes[key][1]
        form, data = node["form"], node["data"]
        if form in {"value", "entries"}:
            return data
        if form == "groups" or form.endswith("-groups"):
            data = [entry for group in data for entry in self.resolve(group, depth + 1, budget)]
            if form == "groups":
                return data
            form = form.removesuffix("-groups")
        if form == "dict":
            return {key: self.resolve(value, depth + 1, budget) for key, value in data}
        values = [self.resolve(value, depth + 1, budget) for value in data]
        if form == "dict-chunks":
            return {key: value for chunk in values for key, value in chunk.items()}
        if form == "list-chunks":
            return [item for chunk in values for item in chunk]
        return "".join(values) if form == "text" else values

    def collection(self, value):
        values = self.resolve(value["details"]) if value.get("details") else value["items"]
        require(isinstance(values, list) and len(values) == value["total"]
                and values[:len(value["items"])] == value["items"], "Invalid context collection")
        return values

    def page(self, key, number, section=None):
        node = self.node(key)
        require(type(number) is int and number >= 0, "Invalid context page cursor")
        data = node["data"]
        if section is not None:
            require(node["form"] in {"dict", "value"}, "Invalid context page section")
            source = dict(data) if node["form"] == "dict" else data
            require(isinstance(source, dict) and section in source, "Unknown context page section")
            data = {section: source[section]}
        entries = data if isinstance(data, list) else [data]
        count = max(1, (len(entries) + CONTEXT_PAGE_ITEMS - 1) // CONTEXT_PAGE_ITEMS)
        require(number < count, "Invalid context page cursor")
        expected = {"schema": "taskplane.context-page/v1", "sha256": key,
                    "kind": node["kind"], "form": node["form"], "section": section,
                    "page": number, "pages": count, "total": len(entries),
                    "data": entries[number * CONTEXT_PAGE_ITEMS:(number + 1) * CONTEXT_PAGE_ITEMS]
                        if isinstance(data, list) else data,
                    "next_page": number + 1 if number + 1 < count else None, "untrusted_data": True}
        require(len(json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
                <= CONTEXT_PAGE_LIMIT, "Oversized context page")
        return expected


def context_response(value):
    """Unwrap transport only, never search arbitrary quoted data for proof."""
    for _ in range(8):
        if isinstance(value, str):
            try:
                value = strict_json(value)
            except (ValueError, UnicodeError) as exc:
                raise EvidenceError("Context response is incomplete, filtered or persisted instead of delivered") from exc
        elif isinstance(value, list) and all(isinstance(block, dict) and block.get("type") == "text" for block in value):
            value = "".join(block["text"] for block in value)
        elif isinstance(value, dict) and value.get("schema", "").startswith("taskplane.context-"):
            return value
        elif isinstance(value, dict) and value.get("exit_code") == 0 and isinstance(value.get("output"), str):
            value = value["output"]
        else:
            break
    raise EvidenceError("Context response is incomplete, filtered or persisted instead of delivered")


def context_commands(transcript, binding, task):
    for pair in transcript.command(["flow context", binding["run"]]):
        args = pair[2]
        if isinstance(args, str):
            try:
                args = strict_json(args)
            except ValueError:
                continue
        command = args.get("command", args.get("cmd", "")) if isinstance(args, dict) else ""
        tokens = list(shlex.shlex(command, posix=True, punctuation_chars=True))
        if "flow" not in tokens:
            continue
        start = tokens.index("flow")
        if tokens[start:start + 2] != ["flow", "context"]:
            continue
        flags = tokens[start + 2:]
        require(len(flags) % 2 == 0 and all(flags[i].startswith("--") for i in range(0, len(flags), 2))
                and not any(token in {"|", "||", "&", "&&", ";", ">", ">>", "<", "(" , ")"} for token in tokens),
                "Context commands must be unfiltered foreground calls")
        options = dict(zip(flags[::2], flags[1::2]))
        require(len(options) == len(flags) // 2 and set(options) <= {
            "--workspace", "--run", "--task", "--drain", "--read-required", "--read", "--page", "--section", "--consume"},
            "Unsupported or duplicate context command options")
        if options.get("--task") != task:
            continue
        require(options.get("--run") == binding["run"] and options.get("--workspace") == binding["workspace"],
                "Foreign context command binding")
        for row in (pair[4], pair[5]):
            if row.get("type") in {"assistant", "user"}:
                require(row.get("sessionId") == binding["root"] and row.get("cwd") == binding["workspace"]
                        and row.get("agentId") == binding.get("consumer", {}).get("worker_id"),
                        "Foreign native context consumer")
        yield options, context_response(pair[3])


def verify_context_delivery(reader, workspace, transcript, binding, receipt, task=None):
    """Strict reusable audit for worker, root task, or whole-phase context.

    A caller supplies the exact expected consumer binding and final receipt.
    This proves returned bytes, never attention, approval or full live acceptance.
    """
    store = ContextTree(reader, workspace)
    delivered = store.resolve(receipt["receipt"])
    require(store.node(receipt["receipt"])["kind"] == "delivery-receipt"
            and delivered.get("schema") == "taskplane.context-delivery-receipt/v1"
            and delivered.get("binding") == binding and delivered.get("status") == "returned",
            "Context receipt is partial, stale or foreign")
    responses = list(context_commands(transcript, binding, task))
    terminal = [(options, value) for options, value in responses if "--drain" in options
                and value.get("schema") == "taskplane.context-drain/v1" and value.get("done") is True
                and value.get("remaining_required") == 0 and value.get("context_receipt") == receipt]
    require(terminal, "Full context delivery was not observed")
    handoff_ref = terminal[-1][1]["handoff_ref"]
    handoff = store.resolve(handoff_ref)
    require(store.node(handoff_ref)["kind"] == "context-handoff"
            and handoff.get("schema") == "taskplane.context-handoff/v1" and handoff.get("binding") == binding
            and digest({k:v for k,v in handoff.items() if k != "digest"}) == handoff.get("digest")
            and delivered.get("handoff_digest") == handoff["digest"], "Foreign or altered context handoff")
    view = store.resolve(handoff["view_ref"])
    require(view.get("schema") == "taskplane.context-view/v1" and view.get("binding") == binding
            and digest({k:v for k,v in view.items() if k != "digest"}) == view.get("digest")
            and delivered.get("view_digest") == view["digest"], "Foreign or altered context view")
    required = store.collection(handoff["required_inputs"])
    require(sorted(required, key=lambda item: item["id"]) == store.collection(view["required_inputs"]),
            "Handoff and view required inputs differ")
    original = {item["ref"]["sha256"]: store.descendants(item["ref"]) for item in required}
    trees = dict(original)
    references = store.collection(view["references"])
    by_id = {item["id"]: item["ref"] for item in references}
    require(all(by_id.get(item["id"]) == item["ref"] for item in required), "Required input reference is foreign")
    allowed = set().union(*(store.descendants(item["ref"]) for item in references))
    for ref in (handoff_ref, handoff["view_ref"], view["authority_ref"],
                handoff["required_inputs"].get("details"), handoff["accepted_inputs"].get("details"),
                view["references"].get("details"), view["required_inputs"].get("details")):
        if ref:
            allowed |= store.descendants(ref)
    if view.get("body_reuse"):
        reuse = view["body_reuse"]
        provenance = store.descendants(reuse["details"])
        allowed |= provenance
        for group in store.collection(reuse):
            body = store.resolve(group["body_ref"])
            shared = store.descendants(group["body_ref"])
            for item in group["inputs"]:
                require(by_id.get(item["id"]) == item["ref"]
                        and digest(store.resolve(item["ref"])) == digest(body), "Altered context body reuse")
                if item["ref"]["sha256"] in trees:
                    trees[item["ref"]["sha256"]] = shared | provenance
    expected_roots = sorted(trees)
    consumed = expected_roots if len(expected_roots) <= 64 else {
        "schema": "taskplane.consumed-inputs/v1", "count": len(expected_roots), "sha256": digest(expected_roots)}
    require(delivered.get("returned_refs") == expected_roots and receipt.get("consumed_inputs") == consumed,
            "Context returned-body inventory differs from required trees")
    seen, pages = set(), {}
    reached_terminal = False
    for options, value in responses:
        operation = [key for key in ("--drain", "--read-required", "--read", "--consume") if key in options]
        if not operation:
            require(value.get("schema") == "taskplane.context-preparation/v1"
                    and value.get("binding") == binding and value.get("handoff_ref") == handoff_ref,
                    "Foreign context preparation")
            continue
        require(len(operation) == 1, "Ambiguous context operation")
        operation = operation[0]
        if operation != "--read":
            require(options[operation] == handoff_ref["sha256"], "Foreign context handoff command")
        if operation in {"--drain", "--read-required"}:
            schema = "taskplane.context-drain/v1" if operation == "--drain" else "taskplane.context-read-batch/v1"
            require(value.get("schema") == schema and value.get("handoff_ref") == handoff_ref
                    and isinstance(value.get("pages"), list), "Foreign context batch")
            returned_pages = value["pages"]
        elif operation == "--read":
            require(value.get("schema") == "taskplane.context-read/v1" and isinstance(value.get("page"), dict),
                    "Invalid context read response")
            returned_pages = [value["page"]]
            page = returned_pages[0]
            require(page.get("sha256") == options[operation]
                    and page.get("page") == int(options.get("--page", "0"))
                    and page.get("section") == options.get("--section"), "Context read command/page mismatch")
        else:
            require(value.get("schema") == "taskplane.context-consumption/v1" and digest(value.get("view")) == digest(view),
                    "Context consumption omitted or altered its view")
            for identity, body in view["inline"].items():
                require(identity in by_id and digest(body) == digest(store.resolve(by_id[identity])),
                        "Altered inline context body")
                seen |= store.descendants(by_id[identity])
            returned_pages = []
        for page in returned_pages:
            key = page.get("sha256")
            require(key in allowed, "Foreign context body page")
            expected = store.page(key, page.get("page"), page.get("section"))
            require(digest(page) == digest(expected), "Omitted or altered context body page")
            if page["section"] is None:
                pages.setdefault(key, set()).add(page["page"])
                if pages[key] == set(range(page["pages"])):
                    seen.add(key)
        actual_receipt = value.get("context_receipt", {})
        actual = store.resolve(actual_receipt["receipt"])
        require(actual.get("schema") == "taskplane.context-delivery-receipt/v1"
                and actual.get("binding") == binding and actual.get("handoff_digest") == handoff["digest"]
                and actual.get("view_digest") == view["digest"] and actual.get("status") == "returned",
                "Context response receipt is foreign")
        returned = actual.get("returned_refs")
        require(isinstance(returned, list) and returned == sorted(set(returned)) and set(returned) <= set(trees)
                and type(value.get("remaining_required")) is int
                and value["remaining_required"] == len(trees) - len(returned), "Invalid context response receipt inventory")
        inventory = returned if len(returned) <= 64 else {
            "schema": "taskplane.consumed-inputs/v1", "count": len(returned), "sha256": digest(returned)}
        require(actual_receipt.get("consumed_inputs") == inventory, "Context returned-body inventory differs from receipt")
        require(all(trees[key] <= seen or original[key] <= seen for key in returned),
                "Required context body pages were omitted from native tool results")
        if value.get("schema") == "taskplane.context-drain/v1":
            require(value.get("done") is (value["remaining_required"] == 0), "Conflicting context terminal state")
        if value.get("context_receipt") == receipt:
            require(operation == "--drain" and value.get("done") is True and value.get("next_action") is None,
                    "Final context receipt lacks terminal drain")
            reached_terminal = True
            break
    require(reached_terminal and all(trees[key] <= seen or original[key] <= seen for key in trees),
            "Required context body pages were omitted from native tool results")
    return {"handoff": handoff_ref["sha256"], "receipt": receipt["receipt"]["sha256"],
            "required_roots": len(trees), "observed_nodes": len(seen)}


def worker(reader, item, state, task, root_transcript, runtime):
    result = state.get("task_results", {}).get(task["id"], {})
    row = state.get("workers", {}).get(result.get("grant"), {})
    identity, grant = row.get("worker_id"), result.get("grant")
    require(identity and identity != state["root"] and row.get("host") == "claude"
            and row.get("state") == "accepted" and row.get("task_id") == task["id"]
            and row.get("grant_id") == grant and row.get("claimed_at")
            and row.get("terminal_status") == "completed" and row.get("ended_at")
            and not any(row.get(key) for key in ("revoked_at", "identity_conflict", "completion_conflict")),
            "Missing claimed, terminal, accepted Claude worker: " + task["id"])
    for key in ("workspace", "root", "run"):
        require(row.get(key) == state[key] and row.get("binding", {}).get(key) == state[key], "Foreign worker binding")
    require(result.get("worker_id") == identity and result.get("task_digest") == row.get("task_digest")
            and result.get("reviewer") == state["root"] and result.get("accepted_at", "") >= row["ended_at"],
            "Accepted result is not joined to this worker")
    frozen = {**{key:value for key,value in task.items() if key not in {
        "status", "started_at", "completed_at", "updated_at", "elapsed_seconds", "criteria", "acceptance_criteria"}},
        "criteria": task.get("criteria", task.get("acceptance_criteria", []))}
    require(row.get("task_digest") == digest(frozen), "Worker does not implement the pinned native task")
    require(result.get("input_manifest") == row.get("input_manifest")
            and result.get("dependency_results") == row.get("dependency_results"), "Stale worker result inputs")
    root = Path(item["workspace"])
    manifest(reader, root, result["manifest"])
    manifest(reader, root, result["input_manifest"])
    require(set(task.get("read_inputs", [])) - set(task["paths"]) <= set(result["input_manifest"]),
            "Worker result omits required reads")
    require(set(result.get("outputs", [])) == set(task["paths"]), "Worker omitted declared outputs")
    evidence = next((entry["transcript"] for entry in item["worker_transcripts"] if entry["worker_id"] == identity), None)
    require(evidence, "Native worker transcript is missing")
    transcript = reader.transcript(evidence)
    require(any(record.get("agentId") == identity and record.get("sessionId") == state["root"]
                and record.get("cwd") == str(root) and record.get("isSidechain") is True for record in transcript.rows),
            "Foreign or missing native worker header")
    claim = transcript.values(transcript.command(["flow worker", "claim", grant, state["run"]]))
    require(any(value.get("grant_id") == grant and value.get("task_id") == task["id"] for value in claim),
            "Claim assertion lacks its actual command/result")
    receipt = row.get("context_receipt", {})
    reference = receipt["receipt"]
    consumer = {"worker_id": identity, "grant_id": grant, "attempt": row["attempt"],
                "task_id": task["id"], "task_generation": row["task_generation"]}
    require(row.get("context_delivery", {}).get("remaining_required") == 0, "Context receipt is partial, stale or foreign")
    verify_context_delivery(reader, root, transcript, {**row["binding"], "consumer": consumer}, receipt, task["id"])
    proof = row.get("hook_readiness", {})
    expected = row.get("expected_runtime", {})
    require(expected.get("root") == runtime["runtime_root"]
            and expected.get("member_sha256") == runtime["native_member_sha256"]
            and proof.get("root") == expected["root"] and proof.get("member_sha256") == expected["member_sha256"]
            and not proof.get("mismatch") and any(pair[0] == proof.get("matched_call") for pair in transcript.pairs()),
            "Automatic hook pair is missing, foreign or from another runtime")
    launch_call = root_transcript.calls.get(row.get("call_id"))
    launch_result = root_transcript.results.get(row.get("call_id"))
    require(launch_call and launch_call[0] in {"Agent", "Task"} and grant in json.dumps(launch_call[1])
            and launch_result and not launch_result[1]
            and launch_result[2].get("toolUseResult", {}).get("agentId") == identity,
            "Native launch is not joined to the actual parent call/result")
    handback = row.get("handback", {})
    notification = handback.get("notification", {})
    native = root_transcript.record(notification.get("record_sha256"))
    stop = row.get("stop_observation", {})
    require(handback.get("status") == "delivered" and stop.get("event_id") and stop.get("observed_at")
            and stop["event_id"] == handback.get("stop_event") and native and native.get("sessionId") == state["root"]
            and native.get("cwd") == str(root) and (native.get("origin") == {"kind": "task-notification", "producer": "session-task"}
                           or native.get("attachment", {}).get("origin") == {"kind": "task-notification", "producer": "session-task"}),
            "Terminal assertion lacks the actual native completion notification")
    accepted = root_transcript.values(root_transcript.command(["flow worker", "accept-result", grant, task["id"], state["run"]]))
    require(any(value.get("grant") == grant and value.get("worker_id") == identity
                and value.get("manifest") == result["manifest"] for value in accepted),
            "Result acceptance lacks its actual root command/result")
    return identity, grant, reference["sha256"]


def evidence_time(value):
    require(isinstance(value, str), "Missing native evidence timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None and parsed <= datetime.now(timezone.utc), "Invalid native evidence timestamp")
    return parsed


def checkpoint_identity(state):
    stage = state["visits"][state["index"]]
    require(not state.get("retired") and not state.get("superseded") and not stage.get("superseded")
            and not state.get("invalidation_pending") and stage.get("packet"), "Stale relay checkpoint")
    revision = stage.get("packet_revision")
    require(type(revision) is int and revision > 0, "Missing relay packet revision")
    scope_digest = stage.get("checkpoint_scope_digest", digest(state["scope"]))
    require(scope_digest == digest(state["scope"]) or (stage.get("phase") == "plan"
            and stage.get("decision") == "approved" and stage.get("approved_scope_digest") == digest(state["scope"])),
            "Relay checkpoint scope drifted")
    return {**{key: state[key] for key in ("root", "run", "workspace")}, "visit": stage["id"],
            "checkpoint": stage["packet"]["checkpoint"], "packet_revision": revision,
            "scope_digest": scope_digest,
            "manifest_digest": digest(stage["packet"])}


def relay_commands(transcript, state, action):
    """Only direct target CLI calls: no quoted command, wrapper or wait note."""
    for pair in transcript.pairs():
        if pair[1] != "Bash" or not isinstance(pair[2], dict):
            continue
        lexer = shlex.shlex(pair[2].get("command", ""), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
        if (len(tokens) < 4 or not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(tokens[0]).name)
                or Path(tokens[1]).name != "tp.py" or tokens[2:4] != ["flow", action]):
            continue
        flags = tokens[4:]
        if (len(flags) % 2 or not all(flags[n].startswith("--") for n in range(0, len(flags), 2))
                or any(token in {"|", "||", "&", "&&", ";", ">", ">>", "<", "(", ")"} for token in tokens)):
            continue
        options = dict(zip(flags[::2], flags[1::2]))
        if (len(options) != len(flags) // 2 or options.get("--run") != state["run"]
                or options.get("--workspace") != state["workspace"]):
            continue
        if not all(row.get("sessionId") == state["root"] and row.get("cwd") == state["workspace"]
                   and not row.get("isSidechain") for row in (pair[4], pair[5])):
            continue
        value = pair[3]
        for _ in range(4):
            if isinstance(value, str):
                try:
                    value = strict_json(value)
                except ValueError:
                    break
            elif isinstance(value, list) and all(isinstance(b, dict) and b.get("type") == "text" for b in value):
                value = "".join(b["text"] for b in value)
            else:
                break
        if (isinstance(value, dict) and value.get("schema") == "taskplane.command-summary/v1"
                and value.get("action") == action and value.get("run") == state["run"]
                and value.get("errors", {}).get("blocking") is False):
            yield pair, options, value


def original_source_launch(reader, relay, state):
    """Recognize the controller's bounded native launch forms without executing JS."""
    refs = relay.get("launch", {}).get("segments", [])
    require(len(refs) == 2, "Original-source relay needs one launch call/result pair")
    selected = reader.transcript(relay["launch"])
    require(len(selected.rows) == 2, "Original-source relay needs two complete launch frames")
    pairs = list(selected.pairs())
    require(len(pairs) == 1, "Original-source relay launch is not a paired native call/result")
    pair = pairs[0]
    args = pair[2]
    if pair[1] in {"exec", "functions.exec"}:
        match = re.fullmatch(r"\s*text\(await tools\.exec_command\((\{.*\})\)\);?\s*", str(args), re.S)
        require(match, "Unsupported original-source relay launch wrapper")
        args = strict_json(match[1])
    elif pair[1] in {"exec_command", "functions.exec_command"} and isinstance(args, str):
        args = strict_json(args)
    require(pair[1] in {"exec", "functions.exec", "exec_command", "functions.exec_command"}
            and isinstance(args, dict) and args.get("tty") is True and args.get("workdir") == state["workspace"],
            "Original-source relay launch needs the exact native target workspace and TTY")
    command = args.get("cmd")
    require(isinstance(command, str), "Original-source relay launch command is missing")
    quote, escaped = "", False
    for char in command:
        if quote == "'":
            if char == "'":
                quote = ""
            continue  # Quoted prompt text is literal data, including newlines.
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char in "$`\n\r":
            raise EvidenceError("Unsupported original-source relay shell expansion")
        elif char == '"':
            quote = "" if quote == '"' else '"'
        elif char == "'" and not quote:
            quote = "'"
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
    flag = relay.get("launch_flag")
    executable = Path(tokens[0]) if tokens else Path()
    options, position = {}, 1
    while position < len(tokens) and tokens[position].startswith("--"):
        key = tokens[position]
        require(key in {"--session-id", "--resume", "--plugin-dir"} and key not in options and position + 1 < len(tokens),
                "Unsupported or duplicate original-source relay launch option")
        options[key] = tokens[position + 1]
        position += 2
    require(tokens and executable.is_absolute() and (executable.name == "claude" or (
                executable.parent.name == "versions" and executable.parent.parent.name == "claude"
                and re.fullmatch(r"\d+\.\d+\.\d+", executable.name)))
            and flag in {"--session-id", "--resume"} and position >= len(tokens) - 1
            and options.get(flag) == state["root"]
            and ("--resume" if flag == "--session-id" else "--session-id") not in options
            and not any(re.fullmatch(r"[;&|<>()]+", token) for token in tokens),
            "Relay parent launch targets an unrelated native session or unsupported command")
    call, result = pair[4].get("payload", {}), pair[5].get("payload", {})
    direct = pair[1] in {"exec_command", "functions.exec_command"}
    require(pair[4] == selected.rows[0] and pair[5] == selected.rows[1]
            and call.get("type") == ("function_call" if direct else "custom_tool_call")
            and result.get("type") == ("function_call_output" if direct else "custom_tool_call_output"),
            "Original-source relay needs actual native launch frames")
    output = pair[3]
    if isinstance(output, list):
        require(0 < len(output) <= 2 and all(isinstance(block, dict) and block.get("type") == "input_text"
                and isinstance(block.get("text"), str) for block in output)
                and (len(output) == 1 or output[0]["text"].startswith("Script completed\n")),
                "Unsupported original-source relay launch result")
        output = output[-1]["text"]
    require(isinstance(output, str), "Missing original-source relay native launch result")
    observed = strict_json(output)
    require(isinstance(observed, dict) and type(observed.get("session_id")) is int and observed["session_id"] > 0
            and observed.get("exit_code") is None and not observed.get("error"),
            "Original-source relay lacks an actual running native session result")
    return pair


def verify_approval_relay(reader, item, state, transcript, decision=None):
    """Inspect original Codex approval of a Claude checkpoint, before or after recording.

    This is evidence consistency, not a controller action or host attestation.
    A pending result neither records approval nor satisfies finish/live acceptance.
    """
    relay = item["approval_relay"]
    require(relay.get("schema") == "taskplane.approval-relay/v1", "Unsupported approval relay schema")
    envelope = relay.get("decision", {})
    schema = envelope.get("schema")
    require(schema in {"taskplane.observed-decision/v1", "taskplane.observed-decision/v2"}, "Unsupported relay decision schema")
    source_preserving = schema == "taskplane.observed-decision/v2"
    if source_preserving:
        require(len(json.dumps(envelope, ensure_ascii=False).encode()) <= 16384,
                "Original-source decision exceeds the controller's envelope bound")
    binding = checkpoint_identity(state)
    require(relay.get("binding") == binding and all(item.get(k) == state[k] for k in ("root", "run", "workspace")),
            "Foreign or stale relay checkpoint binding")
    stage = state["visits"][state["index"]]
    require(stage.get("decision") in {"awaiting_human_approval", "approved"}, "Relay checkpoint is not pending or approved")
    require(source_preserving or (stage["decision"] == "awaiting_human_approval" and decision is None
                                 and not state.get("decisions", {}).get(envelope.get("event_id"))),
            "Historical v1 remapped relay cannot certify a recorded cross-session approval")
    origin = relay.get("origin_conversation")
    require(isinstance(origin, str) and origin and origin != state["root"], "Relay needs its distinct original conversation")
    if source_preserving:
        require(re.fullmatch(r"[0-9a-f-]{36}", origin), "Original-source relay needs its original native conversation ID")
    original = reader.path(relay["session_meta"].get("path"))
    require(original.is_absolute() and original.is_relative_to(Path.home() / ".codex" / "sessions")
            and original.name.startswith("rollout-") and original.name.endswith("-" + origin + ".jsonl"),
            "Relay must reference the original native Codex session, not a copied log")
    if source_preserving:
        require(re.fullmatch(r"\d{4}/\d{2}/\d{2}/rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-"
                + re.escape(origin) + r"\.jsonl", str(original.relative_to(Path.home() / ".codex" / "sessions"))),
                "Original-source relay needs the original native session directory and filename")

    def original_refs(ref):
        refs = ref.get("segments") if set(ref) == {"segments"} else [ref]
        require(isinstance(refs, list) and refs, "Missing relay native references")
        for part in refs:
            require(set(part) == {"path", "sha256", "offset", "bytes"}
                    and reader.path(part["path"]) == original, "Relay evidence is not from one original parent session")
            if source_preserving:
                require(Path(part["path"]).is_absolute() and str(Path(part["path"])) == part["path"]
                        and len(part["path"]) <= 4096 and ".." not in Path(part["path"]).parts
                        and type(part["offset"]) is int and 0 <= part["offset"] < 2**63
                        and type(part["bytes"]) is int and 0 < part["bytes"] <= 131072,
                        "Original-source relay needs bounded literal original frame references")
                raw = reader.raw(part)
                require(raw.endswith(b"\n") and raw.count(b"\n") == 1,
                        "Original-source relay reference must select one complete native frame")
        return refs

    def single(ref):
        original_refs(ref)
        rows = reader.transcript(ref).rows
        require(len(rows) == 1, "Relay reference must select one complete original native frame")
        return rows[0]

    meta = single(relay["session_meta"])
    require(relay["session_meta"]["offset"] == 0 and meta.get("type") == "session_meta"
            and meta.get("payload", {}).get("id") == origin
            and meta["payload"].get("session_id", origin) == origin,
            "Foreign native parent session identity")
    if source_preserving:
        require(isinstance(meta["payload"].get("source"), str) and meta["payload"]["source"] in {"vscode", "cli"}
                and not any(obj.get(key) for obj in (meta, meta["payload"])
                            for key in ("isSidechain", "subagent", "automation_id", "scheduled_task_id")),
                "Original-source relay must originate in a native human Codex session")

    def message(row, role):
        payload = row.get("payload", {})
        require(row.get("type") == "response_item" and payload.get("type") == "message"
                and payload.get("role") == role and isinstance(payload.get("id"), str) and payload["id"],
                "Relay source is not the required native " + role + " message")
        blocks = payload.get("content")
        require(isinstance(blocks, list) and blocks and all(isinstance(block, dict)
                and block.get("type") == ("input_text" if role == "user" else "output_text")
                and isinstance(block.get("text"), str) for block in blocks), "Unsupported relay message content")
        if role == "user" or source_preserving:
            retained = row.get("metadata", {}).get("retained_source", {})
            metadata = payload.get("internal_chat_message_metadata_passthrough", {})
            kinds = metadata.get("content_item_kinds")
            require(retained.get("complete") is True and retained.get("id", {}).get("message_id") == payload["id"]
                    and retained["id"].get("role") == role and (role != "user" or kinds == ["user.text"])
                    and not any(obj.get(key) for obj in (row, payload, row.get("metadata", {}), metadata)
                                for key in ("origin", "promptSource", "turnOrigin", "automation_id", "scheduled_task_id",
                                            "toolUseResult", "tool_call_id", "is_automation", "isSidechain")),
                    "Relay approval is a system, automation or tool claim, not an original human frame")
        return "".join(block["text"] for block in blocks)

    human = single(relay["human"])
    shown = single(relay["presentation"])
    excerpt, presentation_text = message(human, "user"), message(shown, "assistant")
    human_at, shown_at = evidence_time(human.get("timestamp")), evidence_time(shown.get("timestamp"))
    require(evidence_time(meta.get("timestamp")) <= evidence_time(state["started_at"])
            <= evidence_time(stage["submitted_at"]) <= shown_at < human_at
            and relay["presentation"]["offset"] + relay["presentation"]["bytes"] <= relay["human"]["offset"],
            "Relay approval precedes its actual checkpoint presentation")
    dashboard_path = str(Path(state["workspace"]) / ".taskplane" / "dashboard.html")
    links = re.findall(r"\[[^\]\n]+\]\(<?([^\n<>]+?)>?\)", presentation_text)
    if not source_preserving:
        require(dashboard_path in links, "Parent presentation lacks the exact native target dashboard link")

    launch_refs = original_refs(relay["launch"])
    flag = relay.get("launch_flag")
    require(flag in {"--session-id", "--resume"}, "Unsupported relay launch flag")
    launched = (original_source_launch(reader, relay, state) if source_preserving
                else launch(reader, relay["launch"], state["root"], flag))
    args = launched[2]
    if isinstance(args, str):
        try:
            args = strict_json(args)
        except ValueError:
            pass
    if source_preserving:
        target_launch = True  # The strict parser above checked the actual argv.
    elif isinstance(args, dict):
        tokens = shlex.split(args.get("cmd", ""))
        target_launch = args.get("tty") is True and any(tokens[n:n + 2] == [flag, state["root"]]
                                                       for n in range(len(tokens) - 1))
    else:
        # The native Codex exec wrapper can construct argv from a bound session
        # constant. Recognize this narrow form without evaluating JavaScript or
        # treating a target ID merely mentioned in its prompt as the launch ID.
        code = str(args)
        array = re.search(r"tools\.exec_command\(\{cmd:\[(.*?)\]\.join\(\" \"\)", code, re.S)
        variables = re.findall(r'\bconst\s+(\w+)\s*=\s*"' + re.escape(state["root"]) + r'"\s*;', code)
        target_launch = bool(array and any(re.search(r'"' + re.escape(flag) + r'"\s*,\s*sh\(' + re.escape(name) + r'\)',
                                                    array[1]) for name in variables))
    require(target_launch, "Relay parent launch targets an unrelated native session")
    require(all(row.get("type") == "response_item" for row in (launched[4], launched[5]))
            and max(ref["offset"] + ref["bytes"] for ref in launch_refs) <= relay["presentation"]["offset"]
            and evidence_time(launched[4].get("timestamp")) <= evidence_time(launched[5].get("timestamp")) <= shown_at,
            "Relay parent has no preceding native target launch/resume")
    if source_preserving:
        ordered_refs = [relay["session_meta"], *launch_refs, relay["presentation"], relay["human"]]
        ordered_rows = [meta, launched[4], launched[5], shown, human]
        require(all(a["offset"] + a["bytes"] <= b["offset"] for a, b in zip(ordered_refs, ordered_refs[1:]))
                and all(evidence_time(a.get("timestamp")) <= evidence_time(b.get("timestamp"))
                        for a, b in zip(ordered_rows, ordered_rows[1:])), "Original-source relay frame ordering changed")

    native = reader.json(relay["native_presentation"])
    presentation = native.get("presentation", {})
    require(native.get("schema") == "taskplane.harness/v1"
            and all(native.get(k) == state[k] for k in ("root", "run", "workspace"))
            and presentation.get("checkpoint") == binding and presentation.get("outcome") in {"linked", "verified"}
            and presentation.get("binding") == {"run": state["run"], "visit": stage["id"], "revision": binding["packet_revision"]},
            "Native presentation is not bound to the relay checkpoint")
    artifact = reader.path(relay["html"]["path"])
    require(artifact == Path(presentation.get("artifact", "")) and artifact.parent == Path(state["workspace"]) / ".taskplane"
            and re.fullmatch(r"snapshot-[a-f0-9-]+\.html", artifact.name)
            and reader.path(relay["snapshot"]["path"]) == artifact.with_suffix(".json"),
            "Relay snapshot is not the original immutable native presentation")
    if source_preserving:
        require(str(artifact) in links and binding["checkpoint"] in re.findall(r"\b[0-9a-f]{32}\b", presentation_text),
                "Parent presentation lacks the exact immutable checkpoint identity and HTML link")
        for key in ("html", "snapshot"):
            ref = relay[key]
            require(set(ref) == {"path", "offset", "bytes", "sha256"} and type(ref["offset"]) is int and ref["offset"] == 0
                    and Path(ref["path"]).is_absolute() and str(Path(ref["path"])) == ref["path"] and ".." not in Path(ref["path"]).parts
                    and len(ref["path"]) <= 4096 and type(ref["bytes"]) is int and 0 < ref["bytes"] <= 8 * 1024 * 1024
                    and ref["bytes"] == reader.path(ref["path"]).stat().st_size,
                    "Original-source relay requires the complete immutable native snapshot files")
    html, model_raw = reader.raw(relay["html"]), reader.raw(relay["snapshot"])
    require(digest(html) == presentation.get("digest") and digest(model_raw) == presentation.get("model_digest"),
            "Relay native presentation digest mismatch")
    model = strict_json(model_raw)
    snapshot = model.get("snapshot", {})
    expected = {k: binding[k] for k in ("root", "run", "workspace", "visit")}
    expected["revision"] = binding["packet_revision"]
    require(all(snapshot.get(k) == v for k, v in expected.items()) and not snapshot.get("historical")
            and snapshot.get("presentation_target") == dashboard_path
            and checkpoint_identity(model["workflow"]) == binding
            and evidence_time(snapshot.get("generated_at")) <= shown_at,
            "Relay snapshot has stale checkpoint, scope or presentation identity")
    manifest(reader, Path(state["workspace"]), stage["packet"].get("manifest"))

    expected_binding = {**{k: v for k, v in binding.items() if k != "packet_revision"}, "revision": binding["packet_revision"]}
    source = envelope.get("source", {})
    def reference(ref):
        return f"{original}#offset={ref['offset']}&bytes={ref['bytes']}"
    require(envelope.get("choice") == "approved"
            and envelope.get("binding") == expected_binding and envelope.get("recorder") == "root_orchestrator"
            and isinstance(envelope.get("event_id"), str) and 0 < len(envelope["event_id"]) <= 512
            and source == {"kind": "conversation", "actor": "user", "automatic": False,
                           "conversation": origin if source_preserving else state["root"],
                           "reference": reference(relay["human"]), "observed_at": human["timestamp"]}
            and envelope.get("excerpt") == excerpt and 0 < len(excerpt) <= 4096 and len(source.get("reference", "")) <= 512
            and envelope.get("presentation") == {"checkpoint": binding["checkpoint"],
                    "reference": reference(relay["presentation"]), "at": shown["timestamp"]},
            "Relay decision changed the original human excerpt, reference, timestamp or checkpoint binding")
    if source_preserving:
        expected_relay = {"schema": "taskplane.original-source-relay/v1", **{key: relay[key] for key in (
            "session_meta", "human", "presentation", "launch", "launch_flag", "html", "snapshot")}}
        require(envelope.get("relay") == expected_relay,
                "Original-source decision relay differs from the verified original references")
        require(envelope.get("checkpoint_explicit", False) is False,
                "Original-source decision requires its actual preceding presentation")
    require(excerpt.strip().casefold() in {"approve", "approved", "approve as is", "approved as is", "looks good, proceed", "go ahead"},
            "Relay needs an unambiguous supported human approval; retain full wording for review")

    # Join successful target commands to their native session and exact returned
    # checkpoint. Quoted output in a parent/assistant frame is never authority.
    for action in ("submit", "present"):
        matches = [pair for pair, _, value in relay_commands(transcript, state, action)
                   if evidence_time(pair[4].get("timestamp")) <= evidence_time(pair[5].get("timestamp")) <= shown_at
                   and value.get("binding", {}).get("pending_checkpoint") == expected_binding]
        require(matches, "Relay lacks an actual target " + action + " result for this exact checkpoint")
    recorded = state.get("decisions", {}).get(envelope["event_id"])
    if decision is not None:
        require(recorded == decision, "Relay decision is not the recorded event")
    if stage["decision"] == "approved" or decision is not None:
        require(isinstance(recorded, dict) and recorded.get("choice") == "approved" and recorded.get("automatic") is False
                and recorded.get("event_id") == envelope["event_id"] and recorded.get("human") is True
                and recorded.get("binding") == expected_binding
                and recorded.get("provenance", {}).get("source") == source
                and recorded["provenance"].get("excerpt") == excerpt
                and recorded["provenance"].get("recorder") == envelope["recorder"]
                and recorded["provenance"].get("presentation") == envelope["presentation"]
                and recorded["provenance"].get("schema") == schema
                and recorded["provenance"].get("relay") == expected_relay
                and recorded["provenance"].get("checkpoint_explicit") is False
                and recorded["provenance"].get("chronology") == "verified/v1"
                and evidence_time(recorded["provenance"].get("recorded_at")) >= human_at,
                "Recorded decision does not preserve the verified relay provenance")
        require(any(human_at <= evidence_time(pair[4].get("timestamp")) <= evidence_time(pair[5].get("timestamp"))
                    and strict_json(options.get("--decision-json", "null")) == envelope
                    and value.get("status") == "approved"
                    and all(value.get("binding", {}).get(k) == state[k] for k in ("root", "run", "workspace"))
                    and value["binding"].get("visit") == stage["id"]
                    for pair, options, value in relay_commands(transcript, state, "decide")),
                "Recorded relay lacks its actual post-approval target decision command/result")
    else:
        require(not recorded, "Pending checkpoint already contains the relay event")
    return {"status": "pass", "recorded": bool(recorded), "historical_only": not source_preserving,
            "origin_conversation": origin,
            "human_message_id": human["payload"]["id"], "source_reference": source["reference"],
            "binding": binding, "event_id": envelope["event_id"],
            "assurance": "Original native frame and checkpoint consistency only; no controller authorization or host attestation."}


def authorized_finish(reader, state, transcript, tasks, item=None):
    require(state.get("finished") is True and not state.get("retired") and not state.get("superseded"),
            "Invocation has no actual finished outcome")
    stage = state["visits"][state["index"]]
    packet = stage.get("packet", {})
    require(stage.get("decision") == "approved" and packet.get("checkpoint"), "Final checkpoint is not accepted")
    require(packet.get("execution_evidence") == "native-results/v1", "Final packet has no native result evidence")
    manifest(reader, Path(state["workspace"]), packet.get("manifest"))
    output = packet.get("output", {})
    require(output.get("run") == state["run"] and output.get("visit") == stage["id"], "Foreign final synthesis")
    for task in tasks:
        if task.get("execution") != "native_required":
            continue
        result = state["task_results"][task["id"]]
        require(any(coverage.get("status") == "native_verified" and coverage.get("task_id") == task["id"]
                    and coverage.get("reviewer") == result["worker_id"] and coverage.get("grant") == result["grant"]
                    for coverage in output.get("lens_coverage", [])), "Final synthesis omits a joined native lens")
    require(transcript.command(["flow", "submit", state["run"]]), "Final synthesis was not submitted through the runtime")
    decisions = []
    for event, decision in state.get("decisions", {}).items():
        binding = decision.get("binding", {})
        if decision.get("choice") != "approved" or binding.get("checkpoint") != packet["checkpoint"]:
            continue
        require(all(binding.get(key) == state[key] for key in ("root", "run", "workspace"))
                and binding.get("visit") == stage["id"], "Foreign final decision")
        provenance = decision.get("provenance", {})
        source = provenance.get("source", decision.get("source", {}))
        require(source.get("actor") == "user" and source.get("reference"), "Final outcome has no run-bound human authority")
        reference = source["reference"]
        excerpt = provenance.get("excerpt", decision.get("excerpt", ""))
        human_rows = [row for row in transcript.rows if row.get("type") == "user"
                      and row.get("sessionId") == state["root"]
                      and row.get("promptSource") != "system" and not row.get("toolUseResult")
                      and isinstance(row.get("message", {}).get("content"), str)]
        dialogs = [pair for pair in transcript.pairs() if pair[1] == "AskUserQuestion"]
        direct = excerpt and (any(row.get("uuid") and row["uuid"] in reference
                                 and excerpt in row["message"]["content"] for row in human_rows)
                             or any(pair[0] in reference and excerpt in str(pair[3]) for pair in dialogs))
        if item and "approval_relay" in item:
            verify_approval_relay(reader, item, state, transcript, decision)
        else:
            require(source.get("conversation") == state["root"], "Final outcome has no run-bound human authority")
            require(direct, "Human authority has no matching native user/dialog source")
        if decision.get("automatic"):
            policy = state.get("approval_policy", {})
            require(policy.get("digest") == decision.get("policy_digest")
                    and policy.get("binding", {}).get("run") == state["run"]
                    and policy.get("binding", {}).get("root") == state["root"], "Copied automatic policy")
        require(transcript.command(["flow", "auto-decide" if decision.get("automatic") else "decide", state["run"]]),
                "Final decision lacks its actual controller command/result")
        decisions.append(event)
    require(decisions and transcript.command(["flow", "finish", state["run"]]),
            "Accepted checkpoint was not observed finishing through the runtime")
    return decisions


def dashboard(reader, item, state):
    evidence = item["dashboard"]
    view = reader.json(evidence["snapshot"])
    workflow = view.get("workflow", {})
    require(workflow.get("root") == state["root"] and workflow.get("run") == state["run"], "Foreign native dashboard")
    visit = workflow.get("visits", [])[workflow["index"]]
    require(visit["id"] in {value["id"] for value in state["visits"]}
            and type(workflow.get("revision")) is int and workflow["revision"] <= state["revision"], "Invalid dashboard visit/revision")
    html = reader.raw(evidence["html"]).decode("utf-8")
    require(state["run"] in html and state["root"] in html and visit["id"] in html
            and ".taskplane/dashboard.html" in view.get("dashboard", ""), "HTML is not the native run dashboard")
    render = reader.transcript(evidence["render"])
    require(any("cua" in name and "snapshot" in str(output).lower()
                and state["run"] in str(output) and visit["id"] in str(output)
                and str(workflow["revision"]) in str(output)
                for _, name, _, output, _, _ in render.pairs()), "No actual rendered native dashboard identity")
    screenshot = reader.raw(evidence["screenshot"])
    require(len(screenshot) > 100 and (screenshot.startswith(b"\x89PNG\r\n\x1a\n") or screenshot.startswith(b"\xff\xd8\xff")),
            "Rendered dashboard screenshot is missing or invalid")


def interruption(reader, item, state, transcript):
    evidence = item.get("interruption")
    if not evidence:
        return False
    before = state_from(reader.json(evidence["before"]), state["run"])
    require(before.get("root") == state["root"] and before.get("workspace") == state["workspace"]
            and not before.get("finished") and before["revision"] <= state["revision"]
            and before["scope"].get("workflow_binding") == state["scope"].get("workflow_binding"),
            "Interruption replaced the original run or package")
    require(before["visits"][before["index"]].get("decision") != "approved", "Interruption must precede accepted finish")
    stopped = reader.transcript(evidence["interrupt"])
    require(any(("write_stdin" in name or "write_stdin" in str(args))
                and any(token in str(args) for token in ("\\u0003", "\\x03", "\x03", "/exit"))
                and any(obj.get("exit_code") is not None for obj in objects(output))
                for _, name, args, output, _, _ in stopped.pairs()),
            "Interruption request has no observed terminal exit")
    resumed = launch(reader, evidence["resume"], state["root"], "--resume")
    after = resumed[4].get("timestamp")
    require(after and transcript.command(["workflow check", state["run"]], after=after)
            and transcript.command(["flow report", state["run"]], after=after),
            "Same-session resume lacks actual original-run check/report")
    require(all(state.get("decisions", {}).get(key) == value for key, value in before.get("decisions", {}).items()),
            "Resume rewrote previous decision history")
    return True


def verify(path: Path) -> dict[str, Any]:
    reader = Reader(path.resolve().parent)
    checks, observations = [], []
    def check(name, action):
        try:
            value = action()
            checks.append({"name": name, "status": "pass"})
            return value
        except (EvidenceError, OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError) as exc:
            checks.append({"name": name, "status": "fail", "reason": str(exc)})
            return None
    def index():
        require(path.stat().st_size <= LIMIT, "Evidence index is oversized")
        value = strict_json(path.read_bytes())
        require(value.get("schema") == SCHEMA, "Unsupported evidence index schema")
        require(value.get("status") == "observed", "Actual host evidence is " + str(value.get("status", "missing")))
        require(isinstance(value.get("invocations"), list) and len(value["invocations"]) == 2,
                "Two actual invocation records are required")
        return value
    data = check("evidence index", index)
    if data:
        definition = check("saved definition", lambda: reader.json(data["definition"]))
        if definition:
            def authoring():
                transcript = reader.transcript(data["authoring_transcript"])
                values = transcript.values(transcript.command(["workflow save", definition["id"]]))
                require(any(value.get("definition_digest") == digest(definition) and value.get("status") in {"created", "unchanged", "saved"}
                            for value in values), "Authoring/save lacks actual installed command/result")
            check("actual definition save", authoring)
            for number, item in enumerate(data["invocations"], 1):
                def invocation():
                    root = Path(item["workspace"])
                    require(root.is_absolute(), "Invocation workspace must be absolute")
                    state = state_from(reader.json(item["controller"]), item["run"])
                    require(state["root"] == item["root"] and state["workspace"] == str(root), "Foreign controller identity")
                    compilation, bindings, runtime = package(reader, item, state, definition)
                    transcript = reader.transcript(item["root_transcript"])
                    require(any(row.get("sessionId") == state["root"] and row.get("cwd") == str(root)
                                for row in transcript.rows), "Foreign native root transcript")
                    launch(reader, item["launch"], state["root"], "--session-id")
                    compiled = transcript.values(transcript.command(["workflow compile", compilation["package_path"], runtime["runtime_root"]]))
                    started_values = transcript.values(transcript.command(["flow start", compilation["package_path"]]))
                    require(any(value.get("package_digest") == compilation["digest"] for value in compiled)
                            and any(value.get("run") == state["run"] for value in started_values), "No actual compiled invocation")
                    tasks = compilation["task_patterns"][compilation["entry_phase"]]
                    tasks = tasks.get("tasks", []) if isinstance(tasks, dict) else tasks
                    native = [task for task in tasks if task.get("execution") == "native_required"]
                    require({task.get("review_lens") for task in native} >= {"security", "code-quality"}, "Distinct required review lenses are absent")
                    workers = [worker(reader, item, state, task, transcript, runtime) for task in native]
                    require(len({value[0] for value in workers}) == len(workers), "Review lenses reused one native identity")
                    decisions = authorized_finish(reader, state, transcript, tasks, item)
                    dashboard(reader, item, state)
                    resumed = interruption(reader, item, state, transcript)
                    observations.append({"run": state["run"], "root": state["root"], "values": bindings["values"],
                        "package": compilation["digest"], "runtime": runtime["digest"], "workers": workers,
                        "decisions": decisions, "resumed": resumed})
                check("invocation " + str(number), invocation)
            def distinct():
                require(len(observations) == 2, "Both invocations must pass before aggregate acceptance")
                a, b = observations
                require(a["run"] != b["run"] and a["package"] != b["package"], "Reused invocation identity")
                ignored = {"output_prefix", "invocation_label"}
                require({k:v for k,v in a["values"].items() if k not in ignored} !=
                        {k:v for k,v in b["values"].items() if k not in ignored}, "Only labels/output prefixes differ; actual inputs must change")
                require(a["values"].get("output_prefix") != b["values"].get("output_prefix"), "Reused output prefix")
                for column, label in enumerate(("worker", "grant", "context receipt")):
                    require(not {row[column] for row in a["workers"]} & {row[column] for row in b["workers"]}, "Reused " + label)
                require(not set(a["decisions"]) & set(b["decisions"]), "Copied decision identities")
                require(a["resumed"] or b["resumed"], "Actual same-session interruption/resume is not_run")
            check("two fresh invocations and recovery", distinct)
    return {"schema": "taskplane.workflow-builder-live-verification/v1",
            "status": "pass" if checks and all(row["status"] == "pass" for row in checks) else "fail",
            "checks": checks, "runs": [row["run"] for row in observations],
            "assurance": "Cooperative native controller, package and transcript consistency; no host attestation. Screenshot semantics and user intent still require review. Fixtures certify this verifier only."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.evidence)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
