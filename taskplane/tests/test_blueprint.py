"""Executable v1 authoring/identity contract; fixtures do not prove host execution."""
from copy import deepcopy
import json
from pathlib import Path
from types import ModuleType

import pytest

from taskplane import blueprint as b, blueprint_catalog as c, workflow as w


def definition():
    return {
        "schema": b.SCHEMA, "id": "change-risk-review", "version": "0.1.0",
        "name": "Change risk review", "description": "Review a change and preserve evidence.",
        "route": {"kind": "standalone", "phase": "engineering"}, "trigger": {"kind": "manual"},
        "inputs": {
            "source_files": {"type": "workspace_file_list", "required": True},
            "output_prefix": {"type": "workspace_relative_directory", "required": True},
            "criteria": {"type": "acceptance_criteria", "required": True,
                         "default": [{"id": "CR1", "statement": "Identify concrete risks."}]},
        },
        "policy": {"approval_default": "manual", "source_writes": False,
                   "external_actions": [], "on_failure": "stop_and_report"},
        "tasks": [
            {"id": "security", "phase": "engineering", "capability": "taskplane.review.lens",
             "lens": "security", "purpose": "Inspect security risks", "execution": "native_required",
             "depends_on": [], "read_bindings": [{"input": "source_files"}],
             "outputs": [{"name": "findings", "kind": "report", "directory": {"input": "output_prefix"},
                          "relative_path": "security.md"}],
             "criteria_binding": {"input": "criteria"}, "verification": "Report locations and uncertainty."},
            {"id": "synthesis", "phase": "engineering", "capability": "taskplane.phase.synthesis",
             "purpose": "Integrate accepted reviewer evidence", "execution": "root",
             "execution_reason": "Root integrates the accepted independent review.",
             "execution_reference": "Definition synthesis task", "depends_on": ["security"],
             "read_bindings": [{"artifact": {"task": "security", "name": "findings"}}],
             "outputs": [{"name": "report", "kind": "report", "directory": {"input": "output_prefix"},
                          "relative_path": "report.md"}],
             "criteria_binding": {"input": "criteria"}, "verification": "Preserve all criterion results."},
        ],
        "acceptance_scenarios": ["A new invocation uses fresh decisions.", "Missing reviewer evidence blocks synthesis."],
        "runtime_requirements": list(c.BASE_CONTRACTS),
    }


def rejected(value, code):
    with pytest.raises(b.BlueprintError) as error:
        b.validate_definition(value)
    assert error.value.code == code, error.value.result()
    assert set(error.value.diagnostic()) == {"code", "location", "message", "remedy"}
    return error.value


def test_parse_canonical_roundtrip_is_detached_and_nonexecuting(tmp_path, monkeypatch):
    value = definition()
    value["inputs"]["source_files"]["default"] = ["src/z.py", "src/a.py"]
    monkeypatch.chdir(tmp_path)
    normalized = b.parse_definition(json.dumps(value, ensure_ascii=False))
    assert normalized["inputs"]["source_files"]["default"] == ["src/a.py", "src/z.py"]
    assert value["inputs"]["source_files"]["default"] == ["src/z.py", "src/a.py"]
    reverse_keys = dict(reversed(list(value.items())))
    assert b.canonical_definition(value) == b.canonical_definition(reverse_keys)
    assert b.definition_digest(value) == b.definition_digest(normalized)
    assert b.parse_definition(b.canonical_definition(normalized)) == normalized
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("raw,code", [
    (b'{"id":"one","id":"two"}', "duplicate_key"),
    (b'{"tasks":[{"id":"one","id":"two"}]}', "duplicate_key"),
    (b'{"x":NaN}', "invalid_json"), (b'{"x":Infinity}', "invalid_json"),
    (b'{"x":1e999}', "invalid_json"), (b'\xff', "invalid_json"),
    (b'\xef\xbb\xbf{}', "invalid_json"), (b'{}' + b' ' * b.MAX_DEFINITION_BYTES, "definition_too_large"),
], ids=["duplicate-root-key", "duplicate-task-key", "nan", "infinity",
        "overflow", "invalid-utf8", "bom", "oversize"])
def test_invalid_json_before_resolution(raw, code, monkeypatch):
    monkeypatch.setattr(c, "capability_contract", lambda _: pytest.fail("capability lookup before JSON validation"))
    with pytest.raises(b.BlueprintError) as error:
        b.parse_definition(raw)
    assert error.value.code == code


def test_duplicate_key_pointer():
    with pytest.raises(b.BlueprintError) as error:
        b.parse_definition('{"tasks":[{"x/y":1,"x/y":2}]}')
    assert error.value.location == "/tasks/0/x~1y"


@pytest.mark.parametrize("target,key", [
    ((), "status"), (("route",), "approval"), (("trigger",), "schedule"),
    (("policy",), "approved"), (("inputs", "source_files"), "command"),
    (("tasks", 1), "run"), (("tasks", 1, "read_bindings", 0, "artifact"), "path"),
    (("tasks", 1, "outputs", 0), "command"), (("tasks", 1, "criteria_binding"), "expression"),
])
def test_unknown_fields_everywhere_before_capabilities(target, key, monkeypatch):
    value = definition()
    row = value
    for step in target:
        row = row[step]
    row[key] = "untrusted"
    monkeypatch.setattr(c, "capability_contract", lambda _: pytest.fail("lookup before closed-schema validation"))
    rejected(value, "unknown_field")


@pytest.mark.parametrize("mutate,code", [
    (lambda d: d.update(schema="taskplane.workflow-blueprint/draft-v0.1"), "unsupported_schema"),
    (lambda d: d.update(id="../escape"), "invalid_identifier"),
    (lambda d: d.update(version="01.2.3"), "invalid_version"),
    (lambda d: d.update(version="1.0.0-01"), "invalid_version"),
    (lambda d: d.update(route={"kind": "standalone", "phase": "build"}), "unsupported_route"),
    (lambda d: d.update(trigger={"kind": "event"}), "unsupported_trigger"),
    (lambda d: d["policy"].update(source_writes=True), "unsupported_policy"),
    (lambda d: d["policy"].update(source_writes=1), "unsupported_policy"),
    (lambda d: d["policy"].update(approval_default="automatic"), "unsupported_policy"),
    (lambda d: d["policy"].update(external_actions=["send-email"]), "unsupported_policy"),
    (lambda d: d["inputs"]["source_files"].update(type="command"), "unsupported_input_type"),
    (lambda d: d["inputs"]["source_files"].update(required="true"), "invalid_type"),
    (lambda d: d["tasks"][0].update(capability="user.custom"), "unsupported_capability"),
    (lambda d: d["tasks"][0].update(capability="taskplane.build.execution"), "capability_phase_mismatch"),
    (lambda d: d["tasks"][0].update(execution="root"), "execution_mismatch"),
    (lambda d: d["tasks"][0].update(lens="downloaded"), "unsupported_lens"),
    (lambda d: d["tasks"][0].update(execution_reason="fallback"), "execution_mismatch"),
    (lambda d: d["tasks"][1].pop("execution_reference"), "invalid_type"),
    (lambda d: d["tasks"][1].update(lens="security"), "unsupported_lens"),
    (lambda d: d["tasks"][1].update(id="security"), "duplicate_task"),
    (lambda d: d["tasks"][0].update(depends_on=["missing"]), "unknown_dependency"),
    (lambda d: d["tasks"][0].update(depends_on=["synthesis"]), "cyclic_dependency"),
    (lambda d: d["tasks"][1].update(depends_on=[]), "native_review_required"),
    (lambda d: d["tasks"][1]["read_bindings"][0]["artifact"].update(name="absent"), "unknown_artifact"),
    (lambda d: d["tasks"][0]["read_bindings"][0].update(input="absent"), "unknown_input"),
    (lambda d: d["tasks"][0].update(read_bindings=["source_files"]), "invalid_type"),
    (lambda d: d["tasks"][0]["read_bindings"][0].update(input="output_prefix"), "binding_type_mismatch"),
    (lambda d: d["tasks"][0]["outputs"][0].update(kind="source"), "unsupported_output"),
    (lambda d: d["tasks"][1]["outputs"][0].update(relative_path="security.md"), "output_collision"),
    (lambda d: d.update(acceptance_scenarios=[]), "invalid_type"),
    (lambda d: d.update(runtime_requirements=["unknown/v1"]), "unsupported_contract"),
    (lambda d: d.update(runtime_requirements=["native-default/v1"]), "missing_contract"),
])
def test_static_refusals(mutate, code):
    value = definition()
    mutate(value)
    rejected(value, code)


@pytest.mark.parametrize("value", ["../escape", "/absolute", "x//y", "x/./y", ".git/config",
                                    ".codex/config", "x/.agents/private", ".taskplane/state",
                                    "C:/output", "x\\y", "*.py", "${HOME}/a", "x/`id`", "x\nfile"])
def test_paths_refuse_traversal_metadata_globs_and_expressions(value):
    with pytest.raises(b.BlueprintError):
        b.validate_input_value("workspace_file_list", [value])
    data = definition()
    data["tasks"][0]["outputs"][0]["relative_path"] = value
    rejected(data, "invalid_path")


@pytest.mark.parametrize("input_type,value", [
    ("text", ""), ("git_ref", "--help"), ("git_ref", "$(command)"),
    ("workspace_file_list", ["a.py", "a.py"]),
    ("acceptance_criteria", []),
    ("acceptance_criteria", [{"id": "C1", "statement": "one"}, {"id": "C1", "statement": "two"}]),
    ("acceptance_criteria", [{"id": "C1", "statement": ""}]),
])
def test_invalid_defaults_are_not_deferred(input_type, value):
    data = definition()
    data["inputs"]["invalid"] = {"type": input_type, "required": False, "default": value}
    with pytest.raises(b.BlueprintError):
        b.validate_definition(data)


def test_criterion_input_coverage_and_task_limit():
    data = definition()
    data["inputs"]["other_criteria"] = {"type": "acceptance_criteria", "required": True}
    rejected(data, "uncovered_criteria")
    data = definition()
    data["tasks"] *= b.MAX_TASKS
    rejected(data, "invalid_type")


def test_delivery_source_outputs_and_later_phase_dependencies():
    data = definition()
    data["route"] = {"kind": "delivery"}
    data["policy"]["source_writes"] = True
    data["runtime_requirements"] = c.required_contracts(data["route"])
    data["inputs"]["build_files"] = {"type": "workspace_output_file_list", "required": True}
    build = deepcopy(data["tasks"][0])
    build.update(id="build", phase="build", capability="taskplane.build.execution")
    build.pop("lens")
    build["outputs"] = [{"name": "implementation", "kind": "source", "binding": {"input": "build_files"}}]
    data["tasks"].insert(0, build)
    assert b.validate_definition(data)["tasks"][0]["outputs"] == build["outputs"]
    build["depends_on"] = ["security"]
    rejected(data, "forward_phase_dependency")
    build["depends_on"] = []
    data["policy"]["source_writes"] = False
    rejected(data, "source_write_forbidden")


@pytest.mark.parametrize("phase,capability", [("product", "taskplane.product.analysis"), ("design", "taskplane.design.analysis")])
def test_standalone_analysis_routes(phase, capability):
    data = definition()
    data["route"]["phase"] = phase
    row = data["tasks"][0]
    row.update(phase=phase, capability=capability)
    row.pop("lens")
    data["tasks"] = [row]
    assert b.validate_definition(data)["route"]["phase"] == phase


def test_save_reuse_new_version_and_pinned_bytes(tmp_path):
    data = definition()
    result = b.save_definition(tmp_path, data)
    published = tmp_path / result["path"]
    pinned = published.read_bytes()
    assert result["status"] == "created"
    assert b.save_definition(tmp_path, data)["status"] == "unchanged"
    assert b.load_definition(published) == b.validate_definition(data)
    data["description"] = "A changed draft"
    with pytest.raises(b.BlueprintError, match="different bytes"):
        b.save_definition(tmp_path, data)
    assert published.read_bytes() == pinned
    data["version"] = "0.2.0"
    assert b.save_definition(tmp_path, data)["status"] == "created"
    assert published.read_bytes() == pinned
    assert sorted(p.name for p in tmp_path.iterdir()) == ["workflows"]


def test_save_exact_path_regular_files_symlinks_and_byte_identity(tmp_path):
    data = definition()
    with pytest.raises(b.BlueprintError) as error:
        b.save_definition(tmp_path, data, "elsewhere.json")
    assert error.value.code == "invalid_save_path"
    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / "workflows").symlink_to(other, target_is_directory=True)
    with pytest.raises(b.BlueprintError) as error:
        b.save_definition(tmp_path, data)
    assert error.value.code == "save_unavailable"
    assert list(other.iterdir()) == []
    (tmp_path / "workflows").unlink()
    result = b.save_definition(tmp_path, data)
    target = tmp_path / result["path"]
    target.write_text(json.dumps(data, indent=2))
    with pytest.raises(b.BlueprintError) as error:
        b.save_definition(tmp_path, data)
    assert error.value.code == "published_version_conflict"


def test_contract_catalog_is_explicit_detached_and_scoped():
    row = c.capability_contract("taskplane.review.lens")
    assert row["execution"] == "native_required"
    assert row["effect_class"] == "artifact-write"
    assert row["role_path"] == "agents/tp-lens.md"
    row["phases"].append("build")
    assert c.capability_contract("taskplane.review.lens")["phases"] == ["engineering"]
    assert c.capability_contract("agents/custom.md") is None
    assert c.capability_contract("send-email") is None


def test_runtime_identity_pins_executed_module_contracts_without_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    identity = c.runtime_identity(route={"kind": "delivery"})
    assert set(c.KNOWN_CONTRACTS) <= set(identity["contracts"])
    assert identity["runtime_root"] == str(Path(c.__file__).resolve().parents[1])
    assert len(identity["interpreter"]["sha256"]) == 64
    for relative in ("taskplane/tp.py", "hooks/hooks.json", "taskplane/claude_worker_observations.py"):
        assert len(identity["native_member_sha256"][relative]) == 64
    for name in ("workflow", "workflow_evidence", "worker_runtime", "context_handoff", "blueprint", "blueprint_catalog"):
        row = identity["modules"]["taskplane/" + name + ".py"]
        assert len(row["sha256"]) == len(row["loaded_code_sha256"]) == 64
    # B2 supplies the compiler after B1. Its absence must remain an explicit blocker.
    missing = not (Path(c.__file__).parent / "blueprint_compile.py").exists()
    assert identity["compatible"] is not missing
    assert list(tmp_path.iterdir()) == []


def test_runtime_unknown_contract_and_missing_typed_support(monkeypatch):
    identity = c.runtime_identity(["future/v2"])
    assert any(d["code"] == "unsupported_contract" and d["message"] == "future/v2" for d in identity["blockers"])
    from taskplane import workflow_evidence
    monkeypatch.setattr(workflow_evidence, "VERIFICATION_HISTORY", "taskplane.verification-history/v0")
    identity = c.runtime_identity(route={"kind": "delivery"})
    assert "taskplane.verification-history/v1" not in identity["contracts"]
    assert not identity["compatible"]


def test_reject_both_scope_validator_is_not_native_contract(monkeypatch):
    def broken(scope):
        raise w.Refusal("invalid_evidence", "all scopes refused")
    monkeypatch.setattr(w, "validate_scope", broken)
    identity = c.runtime_identity()
    assert "native-default/v1" not in identity["contracts"]
    assert any(row["message"] == "native-default/v1" for row in identity["blockers"])


def test_runtime_foreign_module_and_loaded_body_mutation(monkeypatch, tmp_path):
    from taskplane import workflow_evidence
    original = c.runtime_identity()
    monkeypatch.setattr(workflow_evidence, "typed_plan", lambda output: False)
    changed = c.runtime_identity()
    assert original["digest"] != changed["digest"]
    monkeypatch.setattr(workflow_evidence, "__file__", str(tmp_path / "workflow_evidence.py"))
    blocked = c.runtime_identity()
    assert any(d["code"] == "runtime_module_unavailable" and d["location"].endswith("workflow_evidence") for d in blocked["blockers"])


def test_selected_assets_include_transitive_prompts_and_detect_drift(monkeypatch):
    selected = definition()["tasks"]
    initial = c.catalog(selected)
    assets = initial["capabilities"][1]["assets"]
    assert "lenses/references/security-methodology.md" in assets
    assert "lenses/references/prompt-injection-defense.md" in assets
    assert "skills/tp-go/references/shared-flow.md" in assets
    original_read = c.evidence.read
    def drift(root, relative):
        raw = original_read(root, relative)
        return raw + b"\nchanged" if relative.endswith("prompt-injection-defense.md") else raw
    monkeypatch.setattr(c.evidence, "read", drift)
    changed = c.catalog(selected)
    assert initial["capability_digest"] != changed["capability_digest"]
    def missing(root, relative):
        if relative.endswith("agents/tp-lens.md"):
            raise w.Refusal("invalid_evidence", "missing role")
        return original_read(root, relative)
    monkeypatch.setattr(c.evidence, "read", missing)
    assert any(d["code"] == "capability_asset_unavailable" for d in c.catalog(selected)["blockers"])


def test_catalog_identity_is_deterministic_and_lens_selection_is_bounded():
    data = definition()
    assert c.catalog(data["tasks"]) == c.catalog(list(reversed(data["tasks"])))
    selected = deepcopy(data["tasks"][:1])
    selected[0]["lens"] = "code-quality"
    snapshot = c.catalog(selected)
    assets = snapshot["capabilities"][0]["assets"]
    assert "lenses/references/python-code-quality.md" in assets
    assert "lenses/references/SOURCES.md" in assets
    assert "lenses/security.md" not in assets


def test_synthesis_cannot_replace_or_skip_native_lens_work():
    data = definition()
    data["tasks"] = [data["tasks"][1]]
    data["tasks"][0].update(depends_on=[], read_bindings=[])
    rejected(data, "native_review_required")
    data = definition()
    second = deepcopy(data["tasks"][0])
    second.update(id="quality", lens="code-quality")
    second["outputs"][0]["relative_path"] = "quality.md"
    data["tasks"].insert(1, second)
    rejected(data, "native_review_required")
    data["tasks"][-1]["depends_on"].append("quality")
    assert b.validate_definition(data)


def test_missing_native_hook_asset_is_structured_blocker(monkeypatch):
    original_read = c.evidence.read
    def read(root, relative):
        if relative == "hooks/hooks.json":
            raise w.Refusal("invalid_evidence", "hook manifest unavailable")
        return original_read(root, relative)
    monkeypatch.setattr(c.evidence, "read", read)
    identity = c.runtime_identity()
    assert not identity["compatible"]
    assert any(d["code"] == "runtime_asset_unavailable" and d["location"].endswith("hooks/hooks.json") for d in identity["blockers"])


def test_finite_json_nesting_and_load_missing_file(tmp_path):
    with pytest.raises(b.BlueprintError):
        b.parse_definition("[" * 2000 + "0" + "]" * 2000)
    with pytest.raises(b.BlueprintError) as error:
        b.load_definition(tmp_path / "absent.json")
    assert error.value.code == "definition_unavailable"
