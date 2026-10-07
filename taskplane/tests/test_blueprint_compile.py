"""Compiler contract fixtures: these do not establish native host execution."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

from taskplane import blueprint as b, blueprint_catalog as c, blueprint_compile as bc
from taskplane import workflow as w
from taskplane.primitives import canonical_bytes, content_fingerprint
from taskplane.tests.test_blueprint import definition


@pytest.fixture
def source(tmp_path):
    (tmp_path / "main.py").write_text("answer = 42\n")
    (tmp_path / "dependency.py").write_text("dependency = True\n")
    (tmp_path / "test_main.py").write_text("assert 42 == 42\n")
    return tmp_path


def inputs(**updates):
    return {"source_files": ["main.py"], "output_prefix": "reports/first", **updates}


def check_error(code, action):
    with pytest.raises(b.BlueprintError) as caught:
        action()
    assert caught.value.code == code, caught.value.result()
    assert set(caught.value.diagnostic()) == {"code", "location", "message", "remedy"}
    return caught.value


def git(root, *args):
    return subprocess.check_output(["git", "-c", "core.autocrlf=false", "-C", str(root), *args],
                                   stderr=subprocess.PIPE).decode().strip()


def commit(root, message):
    git(root, "add", "-A")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


def git_definition():
    data = definition()
    data["inputs"].update(base_ref={"type": "git_ref", "required": True},
                          head_ref={"type": "git_ref", "required": True})
    data["tasks"][0]["read_bindings"] += [{"input": "base_ref"}, {"input": "head_ref"}]
    return data


def delivery_definition():
    data = definition()
    data["route"] = {"kind": "delivery"}
    data["policy"]["source_writes"] = True
    data["runtime_requirements"] = c.required_contracts(data["route"])
    data["inputs"]["build_files"] = {"type": "workspace_output_file_list", "required": True}
    product = deepcopy(data["tasks"][0])
    product.update(id="product-analysis", phase="product", capability="taskplane.product.analysis")
    product.pop("lens")
    product["outputs"][0]["relative_path"] = "requirements.md"
    build = deepcopy(product)
    build.update(id="implementation", phase="build", capability="taskplane.build.execution",
                 depends_on=["product-analysis"])
    build["outputs"] = [{"name": "source", "kind": "source", "binding": {"input": "build_files"}}]
    data["tasks"] = [product, build, *data["tasks"]]
    return data


def test_preview_is_side_effect_free_and_explicitly_incomplete(source, monkeypatch):
    before = sorted(str(p) for p in source.rglob("*"))
    monkeypatch.setattr(w, "new_state", lambda *a, **k: pytest.fail("preview created a run"))
    result = bc.preview(source, definition(), {})
    assert result["status"] == "incomplete"
    assert result["unresolved_inputs"] == ["output_prefix", "source_files"]
    assert not result["runnable"]
    assert result["effects"]["preview_writes"] == []
    assert sorted(str(p) for p in source.rglob("*")) == before
    result = bc.preview(source, definition(), inputs())
    assert result["status"] == "ready"
    assert result["decisions"]["execution_authorized"] is False
    assert "unknown" in result["decisions"]["capacity"]
    assert sorted(str(p) for p in source.rglob("*")) == before


def test_compilation_is_deterministic_and_uses_existing_validators(source):
    data = definition()
    first = bc.build_package(source, data, inputs(), ".taskplane/bootstrap/workflow-first")
    reordered = dict(reversed(list(data.items())))
    second = bc.build_package(source, reordered, dict(reversed(list(inputs().items()))),
                              ".taskplane/bootstrap/workflow-first")
    assert first == second
    assert not (source / ".taskplane").exists()
    record = first["compilation"]
    assert record["digest"] == content_fingerprint({k: v for k, v in record.items() if k != "digest"})
    assert "scope.json" not in record["members"]
    assert "compilation.json" not in record["members"]
    scope = first["scope"]
    assert set(scope["paths"]) == set(w.PHASES)
    assert scope["execution_contract"] == "native-default/v1"
    assert scope["workflow_binding"]["package_digest"] == record["digest"]
    tasks = json.loads(first["members"]["tasks.json"])["tasks"]
    assert tasks[0]["execution"] == "native_required"
    assert tasks[0]["review_lens"] == "security"
    assert tasks[-1]["execution"] == "root"
    assert set(tasks[-1]["dependencies"]) == {"security", "synthesis"}
    assert "--standalone" in first["start_arguments"]
    assert not any(key in canonical_bytes(record).decode() for key in ['"run":', '"visit":', '"grant":', '"timestamp":'])


def test_compile_reuse_verify_and_no_clobber(source):
    first = bc.compile_package(source, definition(), inputs())
    target = source / first["package_path"]
    before = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert first["status"] == "created"
    assert bc.compile_package(source, definition(), inputs())["status"] == "unchanged"
    assert bc.verify_package(source, first["workflow_binding"], scope=first["scope"])["status"] == "valid"
    assert {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()} == before
    changed = definition()
    changed["description"] += " Changed."
    check_error("package_integrity", lambda: bc.compile_package(source, changed, inputs(), first["package_path"]))
    assert (target / "definition.json").read_bytes() == before["definition.json"]


def test_phase_evidence_has_separate_invocation_namespace_and_preserves_declared_outputs(source):
    first = bc.compile_package(source, definition(), inputs(), ".taskplane/bootstrap/workflow-first")
    second = bc.build_package(source, definition(), inputs(), ".taskplane/bootstrap/workflow-second")
    files = first['compilation']['phase_files']['engineering']
    assert files == {name: '.taskplane/runtime-evidence/workflow-first/engineering/' + filename
                     for name, filename in [('packet', 'packet.json'), ('tasks', 'tasks.json'),
                                            ('report', 'report.md'), ('evidence', 'evidence.json')]}
    assert set(files.values()).isdisjoint(second['compilation']['phase_files']['engineering'].values())
    assert first['compilation']['task_outputs'] == second['compilation']['task_outputs'] == {
        'security/findings': ['reports/first/security.md'], 'synthesis/report': ['reports/first/report.md']}
    assert not (source / '.taskplane/runtime-evidence').exists()
    assert bc.preview(source, definition(), inputs(), out=first['package_path'])['phase_files'] == first['compilation']['phase_files']
    assert bc.compile_package(source, definition(), inputs(), first['package_path'])['status'] == 'unchanged'
    implicit = bc.build_package(source, definition(), inputs())
    other = bc.build_package(source, definition(), inputs(output_prefix='reports/other'))
    assert implicit['compilation']['phase_files'] != other['compilation']['phase_files']


@pytest.mark.parametrize('name', ['packet', 'tasks', 'report', 'evidence'])
def test_existing_phase_evidence_refuses_recompilation_and_fresh_start_without_clobber(source, name):
    package = bc.compile_package(source, definition(), inputs())
    path = source / package['compilation']['phase_files']['engineering'][name]
    path.parent.mkdir(parents=True)
    path.write_text('Existing invocation evidence\n')
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    check_error('output_exists', lambda: bc.compile_package(source, definition(), inputs()))
    check_error('output_exists', lambda: bc.verify_package(source, package['workflow_binding'], for_start=True))
    assert bc.verify_package(source, package['workflow_binding'])['status'] == 'valid'
    assert {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('relative', ['engineering/packet.json', 'engineering', 'engineering/packet.json/child'])
def test_declared_output_cannot_collide_with_internal_phase_evidence(source, relative):
    data = definition()
    data['tasks'][0]['outputs'][0]['relative_path'] = relative
    # User paths cannot enter .taskplane at all, so the typed binder refuses
    # even before the compiler's exact/ancestor output collision check.
    check_error('invalid_path', lambda: bc.compile_package(source, data,
        inputs(output_prefix='.taskplane/runtime-evidence/workflow-explicit'),
        '.taskplane/bootstrap/workflow-explicit'))
    assert not (source / '.taskplane').exists()


@pytest.mark.parametrize('kind', ['symlink', 'file'])
def test_internal_phase_evidence_parent_must_be_a_safe_directory(source, kind):
    parent = source / '.taskplane/runtime-evidence'
    parent.parent.mkdir()
    if kind == 'symlink':
        parent.symlink_to(source, target_is_directory=True)
    else:
        parent.write_text('Existing regular file\n')
    check_error('invalid_harness_contract' if kind == 'symlink' else 'invalid_path',
                lambda: bc.compile_package(source, definition(), inputs()))
    assert not (source / '.taskplane/bootstrap').exists()


def test_compilation_still_requires_a_declared_output_directory(source):
    data = delivery_definition()
    data['tasks'] = [data['tasks'][1]]
    data['tasks'][0]['depends_on'] = []
    del data['inputs']['output_prefix']
    check_error('missing_output_prefix', lambda: bc.build_package(source, data,
        {'source_files': ['main.py'], 'build_files': ['main.py']}))


def test_all_declared_dependency_test_inventory_is_pinned(source):
    data = definition()
    for name in ("dependency_files", "test_files"):
        data["inputs"][name] = {"type": "workspace_file_list", "required": True}
        data["tasks"][0]["read_bindings"].append({"input": name})
    bound = inputs(dependency_files=["dependency.py"], test_files=["test_main.py"])
    package = bc.compile_package(source, data, bound)
    bindings = bc.verify_package(source, package["workflow_binding"])["bindings"]
    assert set(bindings["source_manifest"]) == {"main.py", "dependency.py", "test_main.py"}
    (source / "test_main.py").write_text("assert False\n")
    check_error("source_drift", lambda: bc.verify_package(source, package["workflow_binding"]))


def test_deleted_files_are_git_snapshots_and_worker_reads_use_snapshot(source):
    git(source, "init", "-q")
    (source / "removed.py").write_text("removed = True\n")
    base = commit(source, "base")
    (source / "removed.py").unlink()
    head = commit(source, "delete")
    data = git_definition()
    values = inputs(source_files=["removed.py", "main.py"], base_ref=base, head_ref=head)
    package = bc.compile_package(source, data, values)
    inspected = bc.verify_package(source, package["workflow_binding"])
    row = inspected["bindings"]["source_manifest"]["removed.py"]
    assert row["kind"] == "deleted" and row["base_commit"] == base
    relative = inspected["compilation"]["snapshots"]["removed.py"]
    assert (source / relative).read_text() == "removed = True\n"
    task = inspected["compilation"]["task_patterns"]["engineering"]["tasks"][0]
    assert relative in task["read_inputs"] and "removed.py" not in task["read_inputs"]
    assert bc.preview(source, data, values)["source_manifest"]["removed.py"]["kind"] == "deleted"
    (source / "removed.py").write_text("reappeared = True\n")
    check_error("source_drift", lambda: bc.verify_package(source, package["workflow_binding"]))


def test_git_checkout_dirty_bytes_and_moving_refs_refuse(source):
    git(source, "init", "-q")
    base = commit(source, "base")
    git(source, "branch", "baseline")
    data = git_definition()
    bound = inputs(base_ref="baseline", head_ref="HEAD")
    package = bc.compile_package(source, data, bound)
    (source / "main.py").write_text("answer = 43\n")
    check_error("source_checkout_mismatch", lambda: bc.bind_inputs(source, data, bound))
    next_head = commit(source, "change")
    check_error("checkout_mismatch", lambda: bc.bind_inputs(source, data, inputs(base_ref=base, head_ref=base)))
    check_error("git_ref_drift", lambda: bc.verify_package(source, package["workflow_binding"]))
    git(source, "branch", "-f", "baseline", next_head)
    check_error("git_ref_drift", lambda: bc.verify_package(source, package["workflow_binding"]))


def test_git_reads_ignore_environment_routing(source, tmp_path, monkeypatch):
    git(source, "init", "-q")
    head = commit(source, "source")
    alien = source / "alien"
    alien.mkdir()
    git(alien, "init", "-q")
    (alien / "main.py").write_text("foreign = True\n")
    commit(alien, "alien")
    monkeypatch.setenv("GIT_DIR", str(alien / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(alien))
    result = bc.bind_inputs(source, git_definition(), inputs(base_ref=head, head_ref="HEAD"))
    assert result["git_refs"]["head_ref"] == head


@pytest.mark.parametrize("mutation", ["definition.json", "bindings.json", "capabilities.json", "tasks.json", "preview.md", "scope.json", "compilation.json"])
def test_each_pinned_member_tamper_is_rejected(source, mutation):
    package = bc.compile_package(source, definition(), inputs())
    path = source / package["package_path"] / mutation
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(b.BlueprintError):
        bc.verify_package(source, package["workflow_binding"])


@pytest.mark.parametrize("mutation", ["extra", "missing", "symlink"])
def test_package_inventory_is_exact_and_regular(source, mutation):
    package = bc.compile_package(source, definition(), inputs())
    target = source / package["package_path"]
    if mutation == "extra":
        (target / "extra.json").write_text("{}")
    elif mutation == "missing":
        (target / "tasks.json").unlink()
    else:
        (target / "preview.md").unlink()
        (target / "preview.md").symlink_to(source / "main.py")
    with pytest.raises(b.BlueprintError):
        bc.verify_package(source, package["workflow_binding"])


def test_partial_publication_has_no_runnable_manifest(source, monkeypatch):
    destination = ".taskplane/bootstrap/workflow-partial"
    original = bc.os.open
    def fail(path, flags, *args, **kwargs):
        if str(path).endswith("preview.json") and flags & os.O_CREAT:
            raise OSError("simulated interrupted publication")
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(bc.os, "open", fail)
    check_error("publication_failed", lambda: bc.compile_package(source, definition(), inputs(), destination))
    assert (source / destination / "definition.json").exists()
    assert not (source / destination / "compilation.json").exists()
    monkeypatch.setattr(bc.os, "open", original)
    check_error("input_unavailable", lambda: bc.compile_package(source, definition(), inputs(), destination))


@pytest.mark.parametrize("out", ["elsewhere", ".taskplane/bootstrap/workflow-../x", ".taskplane/bootstrap/workflow-one/extra", ".taskplane/bootstrap/workflow-one/", ".git/workflow-one"])
def test_bootstrap_namespace_is_exact(source, out):
    check_error("invalid_package_path", lambda: bc.build_package(source, definition(), inputs(), out))
    assert not (source / ".taskplane").exists()


@pytest.mark.parametrize("which", ["read", "output", "package"])
def test_symlink_paths_are_refused_before_writing(source, which):
    if which == "read":
        (source / "link.py").symlink_to(source / "main.py")
        action = lambda: bc.build_package(source, definition(), inputs(source_files=["link.py"]))
    elif which == "output":
        (source / "reports").symlink_to(source, target_is_directory=True)
        action = lambda: bc.build_package(source, definition(), inputs())
    else:
        (source / ".taskplane").symlink_to(source, target_is_directory=True)
        action = lambda: bc.build_package(source, definition(), inputs())
    check_error("invalid_path", action)


def test_runtime_drift_and_missing_contract_never_downgrade(source, monkeypatch):
    package = bc.compile_package(source, definition(), inputs())
    original = c.catalog
    def drift(*args, **kwargs):
        result = original(*args, **kwargs)
        result["compiler_digest"] = "0" * 64
        return result
    monkeypatch.setattr(c, "catalog", drift)
    check_error("runtime_drift", lambda: bc.verify_package(source, package["workflow_binding"]))
    def incompatible(*args, **kwargs):
        result = original(*args, **kwargs)
        result.update(compatible=False, blockers=[{"code": "unsupported_contract", "location": "/runtime", "message": "bounded/v2", "remedy": "Restore runtime"}])
        return result
    monkeypatch.setattr(c, "catalog", incompatible)
    check_error("runtime_incompatible", lambda: bc.build_package(source, definition(), inputs()))
    result = bc.preview(source, definition(), inputs())
    assert result["status"] == "blocked" and not result["runnable"]
    assert result["blockers"][0]["code"] == "unsupported_contract"


def test_delivery_reserves_separate_phase_files_and_outer_scope(source):
    package = bc.build_package(source, delivery_definition(), inputs(build_files=["main.py", "new.py"]))
    assert "--standalone" not in package["start_arguments"]
    assert package["compilation"]["entry_phase"] == "product"
    assert set(package["compilation"]["phase_files"]) == set(w.PHASES)
    assert len({p for files in package["compilation"]["phase_files"].values() for p in files.values()}) == 28
    assert {"main.py", "new.py"} <= set(package["scope"]["paths"]["build"])
    first = json.loads(package["members"]["tasks.json"])
    assert {t["phase"] for t in first["tasks"]} == {"product"}
    check_error("missing_build_scope", lambda: bc.build_package(source, delivery_definition(), inputs(build_files=[])))


def test_authorized_build_mutation_allows_only_exact_paths(source):
    package = bc.compile_package(source, delivery_definition(), inputs(source_files=["main.py", "dependency.py"], build_files=["main.py"]))
    binding = package["workflow_binding"]
    (source / "main.py").write_text("answer = 43\n")
    check_error("source_drift", lambda: bc.verify_package(source, binding))
    assert bc.verify_package(source, binding, authorized_mutable_paths=["main.py"])["status"] == "valid"
    check_error("mutable_scope_violation", lambda: bc.verify_package(source, binding, authorized_mutable_paths=["dependency.py"]))
    (source / "dependency.py").write_text("dependency = False\n")
    check_error("source_drift", lambda: bc.verify_package(source, binding, authorized_mutable_paths=["main.py"]))


def test_mutability_cannot_exempt_package_scope_or_runtime(source, monkeypatch):
    package = bc.compile_package(source, delivery_definition(), inputs(build_files=["main.py"]))
    binding = package["workflow_binding"]
    scope = deepcopy(package["scope"])
    scope["paths"]["build"].append("outside.py")
    check_error("scope_drift", lambda: bc.verify_package(source, binding, scope=scope, authorized_mutable_paths=["main.py"]))
    check_error("mutable_scope_violation", lambda: bc.verify_package(source, binding, authorized_mutable_paths=[package["package_path"] + "/definition.json"]))
    path = source / package["package_path"] / "definition.json"
    path.write_bytes(path.read_bytes() + b" ")
    check_error("package_integrity", lambda: bc.verify_package(source, binding, authorized_mutable_paths=["main.py"]))


def test_current_state_requires_accepted_plan_for_mutation(source):
    package = bc.compile_package(source, delivery_definition(), inputs(build_files=["main.py"]))
    state = w.new_state(str(source), "fixture-root", "fixture-run", package["scope"])
    (source / "main.py").write_text("answer = 43\n")
    check_error("mutable_scope_violation", lambda: bc.verify_package(source, package["workflow_binding"], state=state, authorized_mutable_paths=["main.py"]))
    state["index"] = 3
    state["visits"][2].update(decision="approved", packet={"output": {"write_scope": ["main.py"]}})
    # Authority/history authenticity belongs to the calling harness; this fixture
    # checks only its admitted Plan projection, not a fabricated approval claim.
    assert bc.verify_package(source, package["workflow_binding"], state=state)["mutable_paths"] == ["main.py"]
    state["visits"][2]["packet"]["output"]["write_scope"] = ["dependency.py"]
    check_error("mutable_scope_violation", lambda: bc.verify_package(source, package["workflow_binding"], state=state))


def test_authorized_build_commit_does_not_freeze_head(source):
    git(source, "init", "-q")
    base = commit(source, "base")
    data = delivery_definition()
    data["inputs"].update(base_ref={"type": "git_ref", "required": True}, head_ref={"type": "git_ref", "required": True})
    package = bc.compile_package(source, data, inputs(build_files=["main.py"], base_ref=base, head_ref="HEAD"))
    (source / "main.py").write_text("answer = 43\n")
    commit(source, "approved build")
    assert bc.verify_package(source, package["workflow_binding"], authorized_mutable_paths=["main.py"])["status"] == "valid"


def test_duplicate_criteria_and_bound_output_aliases_refuse(source):
    data = definition()
    data["inputs"]["other_criteria"] = {"type": "acceptance_criteria", "required": True,
                                        "default": [{"id": "CR1", "statement": "Different"}]}
    data["tasks"][1]["criteria_binding"] = {"input": "other_criteria"}
    check_error("duplicate_criterion", lambda: bc.build_package(source, data, inputs()))
    data = definition()
    data["inputs"]["other_prefix"] = {"type": "workspace_relative_directory", "required": True}
    data["tasks"][1]["outputs"][0].update(directory={"input": "other_prefix"}, relative_path="security.md")
    check_error("output_collision", lambda: bc.build_package(source, data, inputs(other_prefix="reports/first")))


def test_existing_output_or_future_parent_file_conflict(source):
    output = source / "reports/first/security.md"
    output.parent.mkdir(parents=True)
    output.write_text("prior invocation")
    assert bc.preview(source, definition(), inputs())["blockers"][0]["code"] == "output_exists"
    check_error("output_exists", lambda: bc.compile_package(source, definition(), inputs()))


def test_task_snapshot_limit_is_enforced_without_truncation(source):
    data = definition()
    data["route"] = {"kind": "standalone", "phase": "design"}
    seed = data["tasks"][0]
    seed.update(phase="design", capability="taskplane.design.analysis", instructions="x" * 10000)
    seed.pop("lens")
    data["tasks"] = []
    for index in range(8):
        row = deepcopy(seed)
        row["id"] = "design-" + str(index)
        row["outputs"][0]["relative_path"] = "report-" + str(index) + ".md"
        data["tasks"].append(row)
    check_error("task_snapshot_too_large", lambda: bc.build_package(source, data, inputs()))
    assert not (source / ".taskplane").exists()


def test_optional_referenced_inputs_are_still_required_and_unknowns_refuse(source):
    data = definition()
    data["inputs"]["source_files"]["required"] = False
    check_error("missing_input", lambda: bc.build_package(source, data, {"output_prefix": "reports/first"}))
    check_error("unknown_input", lambda: bc.bind_inputs(source, data, inputs(unknown="x")))
    check_error("missing_read", lambda: bc.bind_inputs(source, data, inputs(source_files=["missing.py"])))


def test_published_definition_edit_does_not_retarget_pinned_instance(source):
    data = definition()
    saved = b.save_definition(source, data)
    package = bc.compile_package(source, data, inputs())
    draft = source / saved["path"]
    data["description"] = "Changed elsewhere"
    draft.write_text(json.dumps(data))
    inspected = bc.verify_package(source, package["workflow_binding"])
    assert inspected["definition"]["description"] != data["description"]


def test_multiple_criteria_inputs_ignore_json_object_key_order(source):
    data = definition()
    data["inputs"]["additional_criteria"] = {"type": "acceptance_criteria", "required": True,
        "default": [{"id": "CR2", "statement": "Preserve evidence and uncertainty."}]}
    data["tasks"][1]["criteria_binding"] = {"input": "additional_criteria"}
    first = bc.build_package(source, data, inputs())
    shuffled = deepcopy(data)
    shuffled["inputs"] = dict(reversed(list(shuffled["inputs"].items())))
    shuffled = dict(reversed(list(shuffled.items())))
    second = bc.build_package(source, shuffled, inputs())
    assert first["package_digest"] == second["package_digest"]
    assert first["members"] == second["members"]
    assert set(first["scope"]["criteria"]) == {"CR1", "CR2"}


def test_delivery_plan_can_use_reserved_history_and_retry_logs_but_not_widen(source):
    from taskplane import workflow_evidence as ev
    data = delivery_definition()
    history = "reports/first/build/history.json"
    logs = ["reports/first/build/check-1.log", "reports/first/build/check-2.log"]
    package = bc.compile_package(source, data, inputs(source_files=["main.py", "test_main.py"],
                                                    build_files=["main.py", history, *logs]))
    state = w.new_state(str(source), "root", "fixture", package["scope"])
    state["index"] = 2
    rows = deepcopy(package["compilation"]["task_patterns"]["build"]["tasks"])
    checkpoint = next(row for row in rows if row["id"] == "workflow-build-checkpoint")
    implementation = next(row for row in rows if row["id"] == "implementation")
    # Reserved native outputs remain with their original producer, including logs.
    checkpoint["read_inputs"] += ["test_main.py"]
    phase = package["compilation"]["phase_files"]["build"]
    planned = {"write_scope": sorted(package["scope"]["paths"]["build"]),
               "build_outputs": [{"kind": kind, "path": path,
                                  "task": implementation["id"] if kind == "verification_history" else checkpoint["id"]}
                                  for kind, path in [("packet", phase["packet"]), ("report", phase["report"]),
                                                     ("verification_history", history)]],
               "verification_strategy": {"schema": ev.VERIFICATION_STRATEGY, "checks": [{
                   "id": "check", "name": "Actual project verification", "task": implementation["id"],
                   "kind": "unit", "environment": "fixture", "required": True,
                   "criteria": ["CR1"], "command": ["python3", "test_main.py"],
                   "source_inputs": ["main.py"], "test_inputs": ["test_main.py"], "evidence_outputs": logs}]},
               "integration_order": [row["id"] for row in rows]}
    assert ev.plan_preflight(source, state, planned, rows)["status"] == "validated"
    planned["write_scope"].append("unreserved.py")
    with pytest.raises(w.Refusal, match="outer Build scope"):
        ev.plan_preflight(source, state, planned, rows)


@pytest.mark.parametrize("out", [".taskplane//bootstrap/workflow-one", ".taskplane/bootstrap/./workflow-one"])
def test_lexical_package_path_aliases_are_not_accepted(source, out):
    check_error("invalid_package_path", lambda: bc.build_package(source, definition(), inputs(), out))


def test_output_under_regular_file_parent_is_rejected(source):
    (source / "reports").write_text("not a directory")
    check_error("invalid_path", lambda: bc.build_package(source, definition(), inputs()))


def test_read_and_output_ancestor_collisions_are_rejected(source):
    data = definition()
    data["tasks"][0]["outputs"][0]["relative_path"] = "folder"
    data["tasks"][1]["outputs"][0]["relative_path"] = "folder/report.md"
    check_error("output_collision", lambda: bc.build_package(source, data, inputs()))


def test_fresh_start_rejects_used_outputs_while_continuation_verifies(source):
    package = bc.compile_package(source, definition(), inputs())
    target = source / "reports/first/security.md"
    target.parent.mkdir(parents=True)
    target.write_text("completed review evidence")
    assert bc.verify_package(source, package["workflow_binding"])["status"] == "valid"
    check_error("output_exists", lambda: bc.verify_package(source, package["workflow_binding"], for_start=True))


@pytest.mark.parametrize('newline', [b'\n', b'\r\n'], ids=['lf', 'crlf'])
def test_git_replace_does_not_change_bound_commit_contents(source, newline):
    git(source, "init", "-q")
    original = b'answer = 42' + newline
    (source / "main.py").write_bytes(original)
    first = commit(source, "first")
    (source / "main.py").write_text("different = True\n")
    second = commit(source, "second")
    git(source, "replace", first, second)
    # The binder reads immutable objects, not repository-local replacement views.
    assert bc._blob(source, first, "main.py") == original


@pytest.mark.parametrize('field,value', [('review_lens', 'code-quality'), ('capability', 'taskplane.phase.synthesis'),
                                         ('criteria', []), ('read_inputs', []), ('paths', [])])
def test_delivery_review_native_anchors_survive_later_phase_refinement(source, field, value):
    from taskplane import workflow_evidence as evidence
    package = bc.build_package(source, delivery_definition(), inputs(build_files=['main.py']))
    rows = deepcopy(package['compilation']['task_patterns']['engineering']['tasks'])
    evidence.native_refinement(package['compilation'], rows, 'engineering')
    next(row for row in rows if row['id'] == 'security')[field] = value
    with pytest.raises(w.Refusal, match='native obligation'):
        evidence.native_refinement(package['compilation'], rows, 'engineering')
