"""Stage loop implementation; the caller supplies the composition root ports."""

from __future__ import annotations
import re

if __package__:
    from .primitives import content_fingerprint
else:
    from primitives import content_fingerprint
from collections.abc import Mapping, Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import terminal_truth


def _stage_command_error(_ports, command: object, exc: Exception) -> dict:
    """Return a stable CLI error without changing run state."""
    return {
        "schema": _ports.STAGE_COMMAND_SCHEMA,
        "command": str(command or ""),
        "error": f"{exc.__class__.__name__}: {exc}",
    }


def _stage_request(_ports, request: object) -> dict:
    """Copy one JSON stage request before it crosses the lifecycle seam."""
    if not isinstance(request, _ports.Mapping):
        raise ValueError("stage request must be a JSON object")
    if any(not isinstance(key, str) for key in request):
        raise ValueError("stage request field names must be strings")
    # A canonical JSON round-trip both detaches caller-owned mutable values
    # and rejects Python-only values before an operation fingerprint is made.
    try:
        return _ports.json.loads(
            _ports.json.dumps(
                request, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("stage request must be canonical JSON") from exc


def _reject_stage_runtime_fields(_ports, value: object, label: str) -> None:
    if isinstance(value, _ports.Mapping):
        for key, child in value.items():
            normalized = "".join(character for character in key.lower() if character not in "-_. ")
            if normalized in _ports._STAGE_RUNTIME_FIELDS:
                raise ValueError(f"{label} contains forbidden runtime field {key!r}")
            _ports._reject_stage_runtime_fields(child, label)
    elif isinstance(value, list):
        for child in value:
            _ports._reject_stage_runtime_fields(child, label)


def _validate_stage_request(_ports, action: str, request: dict) -> dict:
    allowed = _ports._STAGE_REQUEST_FIELDS[action]
    unknown = set(request) - allowed
    if unknown:
        raise ValueError("stage request has unknown fields: " + ", ".join(sorted(unknown)))
    schema = request.get("schema")
    if schema is not None and schema != "taskplane.stage-command/v1":
        raise ValueError("stage request schema is invalid")
    _ports._reject_stage_runtime_fields(request, "stage request")
    return request


def _stage_run_id(_ports, command: str, request: Mapping[str, object]) -> str:
    stage = request.get("stage")
    if command in {"reuse", "terminalize-and-start"} and stage is None:
        stage = request.get("successor_stage")
    if command in {"start", "reuse", "terminalize-and-start"}:
        if not isinstance(stage, _ports.Mapping):
            raise ValueError(f"{command} requires stage")
        run_id = stage.get("run_id")
    else:
        run_id = request.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError(f"{command} requires run_id")
    return run_id


def _stage_store(_ports, ws: str, run_id: str):
    """Open only the store explicitly bound to this workspace."""
    try:
        if _ports.__package__:
            from . import run_store as stage_run_store
        else:
            import run_store as stage_run_store
    except ImportError:
        import run_store as stage_run_store
    locator = _ports.runtime_storage.load_workspace_locator(ws)
    if isinstance(locator, _ports.Mapping):
        if locator.get("run_id") != run_id:
            raise ValueError("workspace belongs to a different stage run")
        home = locator.get("home")
        if not isinstance(home, str) or not home:
            raise ValueError("workspace stage store is unavailable")
        return stage_run_store.RunStore(home=home)
    raise ValueError("workspace has no run locator; initialize this workspace first")


def authorized_run_revision(_ports, ws, manifest, baseline, current):
    """Prove that a changed commit is an effect of the approved Build scope."""
    state = manifest.get("workflow") or {}
    if state.get("step") not in {"execute", "fix", "evaluate", "em", "signoff", "retro", "done"}:
        return False
    receipt = _ports._validated_delivery_mode(state)
    if receipt is None or receipt["mode"] != "build":
        return False
    locator = _ports.runtime_storage.load_workspace_locator(ws) or {}
    task_id = locator.get("task_id")
    tasks = [
        task for task in state.get("tasks") or [] if task_id is None or task.get("id") == task_id
    ]
    scopes = sorted({path for task in tasks for path in task.get("scope") or []})
    if (
        not scopes
        or _ports.tp._run(
            ["git", "merge-base", "--is-ancestor", baseline, current], cwd=ws
        ).returncode
    ):
        return False
    changed = _ports.tp._run(["git", "diff", "--name-only", "-z", baseline, current, "--"], cwd=ws)
    return changed.returncode == 0 and all(
        _ports.tp.writable_target(path, scopes, ws) for path in changed.stdout.split("\0") if path
    )


def _current_stage_authority(
    _ports, ws: str, manifest: Mapping[str, object], expected: object
) -> dict:
    """Re-resolve repository/worktree facts for the exact supplied binding.

    Actor/session and consolidated-authority revision are immutable receipt
    identities, so they remain from the request.  Facts owned by the current
    run manifest or checkout are replaced with their live values immediately
    before ``StageLifecycle`` commits and the repository validator compares
    the result with the indexed stage's expected binding.
    """
    if not isinstance(expected, _ports.Mapping):
        raise ValueError("stage command requires an authority binding")
    current = dict(expected)
    current["run_id"] = manifest.get("run_id")

    repository = manifest.get("repository")
    if isinstance(repository, _ports.Mapping):
        if repository.get("repo_id"):
            current["repository_id"] = repository.get("repo_id")
    try:
        locator = _ports.runtime_storage.load_workspace_locator(ws)
    except Exception:
        locator = None
    if isinstance(locator, _ports.Mapping):
        if locator.get("repo_id"):
            current["repository_id"] = locator.get("repo_id")
        if locator.get("repository_key"):
            current["repository_key"] = locator.get("repository_key")
        if locator.get("run_id") != manifest.get("run_id"):
            raise ValueError("workspace belongs to a different stage run")

    # A non-Git checkout has no live revision to substitute.  A
    # real governed checkout always does, and any head drift then fails the
    # exact repository authority comparison rather than being advisory.
    live_revision = _ports.tp.git_head(ws)
    if live_revision and live_revision != "unknown":
        baseline = str(expected.get("worktree_revision") or "")
        if live_revision != baseline and authorized_run_revision(
            _ports, ws, manifest, baseline, live_revision
        ):
            # Authority pins the approved input; the signed Build result pins
            # the produced commit. Only scoped, descendant effects qualify.
            current["worktree_revision"] = baseline
        else:
            current["worktree_revision"] = live_revision

    target = manifest.get("target")
    if isinstance(target, _ports.Mapping):
        target_revision = target.get("revision") or target.get("commit") or target.get("head")
        if target_revision:
            current["target_revision"] = target_revision
    return current


def _stage_lifecycle(
    _ports, ws: str, store: object, manifest: Mapping[str, object], authority: object
):
    """Load the stage lifecycle against the explicitly selected run."""
    try:
        if _ports.__package__:
            from . import repository as stage_repository
            from . import stage_entities
        else:
            import repository as stage_repository
            import stage_entities
    except ImportError:
        # Package-style imports are not reliable when tp.py is executed as a
        # script from inside taskplane/.  Mirror the repository's established
        # dual-import convention without making stage_entities eager.
        import repository as stage_repository
        import stage_entities

    return stage_entities, stage_entities.StageLifecycle(
        store,
        workspace=ws,
        authority_resolver=lambda _current: _ports._current_stage_authority(
            ws, _current, authority
        ),
        authority_validator=stage_repository.revalidate_stage_authority,
    )


def _indexed_stage(
    _ports, store: object, manifest: Mapping[str, object], run_id: str, stage_id: str
) -> dict:
    heads = manifest.get("stage_heads")
    if not isinstance(heads, _ports.Mapping) or stage_id not in heads:
        raise ValueError("stage is not indexed")
    head = heads[stage_id]
    if not isinstance(head, _ports.Mapping) or not isinstance(head.get("object"), _ports.Mapping):
        raise ValueError("stage head is invalid")
    read = getattr(store, "read_stage_object", None)
    if not callable(read):
        raise ValueError("stage store cannot read immutable objects")
    return read(run_id, dict(head["object"]))


def _verified_stage_handoff(
    _ports, lifecycle: object, store: object, manifest: Mapping[str, object], stage: dict
) -> dict | None:
    """Resolve only the successor's selected, authority-bound handoff."""
    from taskplane import phase_amendment

    amended = phase_amendment.verified_handoff(_ports, lifecycle, manifest, stage)
    if amended is not None:
        return amended
    predecessors = list(stage.get("predecessor_stage_ids") or [])
    parents = list(stage.get("parent_stage_ids") or [])
    if not predecessors and parents:
        failures: list[Exception] = []
        for parent_id in parents:
            try:
                producer = _ports._indexed_stage(
                    store, manifest, str(stage["run_id"]), str(parent_id)
                )
                # A split handoff carries the explicit non-default reuse
                # authorization for its closed parent.  Route it through the
                # lifecycle verifier so that authorization is checked rather
                # than treating the child as an ordinary root consumer.
                return lifecycle._read_handoff(  # noqa: SLF001
                    stage["input_manifest_ref"], producer=producer, consumer=stage
                )
            except Exception as exc:
                failures.append(exc)
        raise ValueError("no verified split-parent handoff is dispatchable") from failures[-1]
    if not predecessors:
        # Root stages still cross the same bounded input boundary.  They have
        # no producer aggregate to compare, but their immutable reference is
        # verified against exact stage authority and selected artifacts before
        # the root is dispatchable.
        try:
            if _ports.__package__:
                from . import stage_handoff
            else:
                import stage_handoff
        except ImportError:
            import stage_handoff
        authority = stage.get("authority")
        if not isinstance(authority, _ports.Mapping):
            raise ValueError("root stage authority is invalid")
        return stage_handoff.read_manifest(
            lifecycle._artifact_store(),  # noqa: SLF001
            stage["input_manifest_ref"],
            expected_authority_revision=int(authority["authority_revision"]),
            expected_authority_fingerprint=str(authority["authority_fingerprint"]),
        )
    failures: list[Exception] = []
    for predecessor_id in predecessors:
        try:
            producer = _ports._indexed_stage(
                store, manifest, str(stage["run_id"]), str(predecessor_id)
            )
            # StageLifecycle owns the producer/consumer binding checks.  This
            # read is deliberately after a successful receipt and does not
            # inspect an execution tree or any predecessor runtime record.
            return lifecycle._read_handoff(  # noqa: SLF001
                stage["input_manifest_ref"], producer=producer, consumer=stage
            )
        except Exception as exc:
            failures.append(exc)
    raise ValueError("no verified predecessor handoff is dispatchable") from failures[-1]


def _stage_dispatch(
    _ports,
    store: object,
    lifecycle: object,
    receipt: Mapping[str, object],
    stage: dict,
    *,
    attempt_id: str | None = None,
    declared_scope: object = None,
) -> dict:
    """Build the path-free, bounded runtime envelope for a fresh attempt."""
    verify = getattr(_ports.tp, "verify_stage_receipt", None)
    runtime = getattr(_ports.tp, "stage_runtime_dispatch", None)
    if not callable(verify) or not callable(runtime):
        raise ValueError("stage runtime serializer is unavailable")
    operation = str(receipt.get("operation") or "")
    checked_receipt = verify(
        receipt, expected_operation=operation, expected_stage_id=str(stage["stage_id"])
    )
    current = store.load(str(stage["run_id"]))
    handoff = _ports._verified_stage_handoff(lifecycle, store, current, stage)
    return runtime(
        stage,
        checked_receipt,
        handoff,
        stage.get("selected_artifacts") or [],
        attempt_id=attempt_id,
        declared_scope=declared_scope,
    )


def _preflight_stage_dispatch(
    _ports, stage: dict, handoff: dict, declared_scope: object = None
) -> None:
    """Prove bounded startup serialization before committing a new head."""
    serializer = getattr(_ports.tp, "stage_dispatch_payload", None)
    if not callable(serializer):
        raise ValueError("stage runtime serializer is unavailable")
    claim = {
        "schema": "taskplane.stage-execution-root-claim/v1",
        "run_id": stage["run_id"],
        "stage_id": stage["stage_id"],
        "execution_root_id": stage["execution_root_id"],
    }
    serializer(
        stage, handoff, stage.get("selected_artifacts") or [], claim, declared_scope=declared_scope
    )


def _stage_bootstrap_pristine_root(_ports, ws: str, state: Mapping[str, object]) -> dict | None:
    """Commit the attributable first root before normal loop dispatch.

    The root is derived only from init-captured authority and the retained
    requirement. Content-addressed inputs make a crash between the v4 commit
    and singleton binding replay the same lifecycle operation.
    """
    if state.get("_stage_native_new_run_pristine") is not True:
        return None
    raw_authority = state.get("_stage_native_root_authority")
    if (
        not isinstance(raw_authority, _ports.Mapping)
        or set(raw_authority) != _ports._STAGE_ROOT_AUTHORITY_FIELDS
    ):
        raise ValueError("stage-native root bootstrap authority is invalid")
    root_authority = dict(raw_authority)

    try:
        if _ports.__package__:
            from . import design_contract as stage_design_contract
            from . import review_evidence, stage_handoff
        else:
            import design_contract as stage_design_contract
            import review_evidence
            import stage_handoff
    except ImportError:
        import design_contract as stage_design_contract
        import review_evidence
        import stage_handoff

    authority_material = dict(root_authority)
    authority_fingerprint = str(authority_material.pop("fingerprint", ""))
    if (
        root_authority.get("schema") != _ports._STAGE_ROOT_AUTHORITY_SCHEMA
        or review_evidence.content_fingerprint(authority_material) != authority_fingerprint
    ):
        raise ValueError("stage-native root bootstrap authority is invalid")
    # The recorded session identifies the original authorization. It is not
    # a lease on the orchestrator's conversation. Revalidate the complete
    # durable authority below, retaining its exact original fingerprint.
    if _ports.tp.task_slot() is not None:
        raise ValueError("a worker cannot bootstrap the run root")

    locator = _ports.runtime_storage.load_workspace_locator(ws)
    if not isinstance(locator, _ports.Mapping):
        raise ValueError("stage-native root bootstrap locator is missing")
    run_id = str(root_authority["run_id"])
    if (
        locator.get("run_id") != run_id
        or locator.get("repo_id") != root_authority["repository_id"]
        or locator.get("repository_key") != root_authority["repository_key"]
    ):
        raise ValueError("stage-native root bootstrap locator changed")
    store = _ports._stage_store(ws, run_id)
    manifest = store.load(run_id)
    repository = manifest.get("repository")
    target = manifest.get("target")
    target_revision = (
        target.get("revision") or target.get("head") if isinstance(target, _ports.Mapping) else None
    )
    if (
        manifest.get("schema") != "taskplane.run/v4"
        or not isinstance(repository, _ports.Mapping)
        or repository.get("repo_id") != root_authority["repository_id"]
        or target_revision != root_authority["target_revision"]
        or _ports.tp.git_head(ws) != root_authority["worktree_revision"]
    ):
        raise ValueError("stage-native root bootstrap authority changed")

    requirement_id = str(root_authority["requirement_id"])
    requirement = _ports.reqs.get_requirement(ws, requirement_id)
    if requirement is None or state.get("requirement_id") != requirement_id:
        raise ValueError("stage-native root bootstrap requirement is missing")
    requirement_fingerprint = stage_design_contract.requirement_fingerprint(ws, requirement_id)
    if (
        requirement_fingerprint != root_authority["requirement_revision"]
        or requirement_fingerprint != root_authority["requirement_fingerprint"]
    ):
        raise ValueError("stage-native root bootstrap requirement changed")

    step = str(state.get("step") or "")
    kind = _ports._LOOP_STAGE_KINDS.get(step)
    if kind not in {"product", "design", "plan"}:
        raise ValueError("stage-native root bootstrap step is invalid")
    requirement_identity = {
        "id": requirement_id,
        "revision": requirement_fingerprint,
        "fingerprint": requirement_fingerprint,
    }
    contract_groups = {"provided": [], "consumed": [], "changed": []}
    contracts = []
    for row in requirement.get("contracts") or []:
        if not isinstance(row, _ports.Mapping) or not isinstance(row.get("id"), str):
            continue
        contract_id = str(row["id"])
        contracts.append(contract_id)
        relation = str(row.get("relation") or "").lower()
        group = (
            "provided"
            if "provide" in relation
            else "changed"
            if "change" in relation
            else "consumed"
        )
        contract_groups[group].append(contract_id)
    contract_groups = {key: sorted(set(values)) for key, values in contract_groups.items()}
    contracts = sorted(set(contracts))
    root_input = {
        "schema": "taskplane.loop-root-input/v1",
        "run_id": run_id,
        "step": step,
        "stage_kind": kind,
        "goal": str(state.get("goal") or ""),
        "spec_path": state.get("spec_path"),
        "requirement": dict(requirement),
    }
    artifact_store = review_evidence.ArtifactStore(ws)
    selected_native = artifact_store.put("root-input", root_input)
    authority_native = artifact_store.put(
        "root-authority",
        {
            "schema": "taskplane.loop-root-authority-evidence/v1",
            "authority": root_authority,
        },
    )
    selected = review_evidence.portable_artifact_reference(artifact_store, selected_native)
    authorized_at = f"{str(requirement.get('date') or '1970-01-01')}T00:00:00Z"
    handoff = stage_handoff.create_manifest(
        artifact_store,
        producer_stage_id=f"input-{run_id}",
        producer_outcome="done",
        requirement=requirement_identity,
        design=None,
        target=None,
        commit=None,
        contracts=contract_groups,
        deliverables=["root-input"],
        evidence_references=[authority_native],
        selected_artifacts=[selected_native],
        exclusions=sorted(stage_handoff.REQUIRED_EXCLUSIONS),
        authorization={
            "actor": root_authority["actor"],
            "session_id": root_authority["session_id"],
            "authorized_at": authorized_at,
            "operation_id": "authorize-root-" + authority_fingerprint[:32],
            "authority_record": {
                "schema": "taskplane.authority-record-reference/v1",
                "authority_schema": "taskplane.consolidated-authorization/v1",
                "revision": root_authority["authority_revision"],
                "fingerprint": authority_fingerprint,
            },
            "nonconsumable_reuse": None,
        },
    )
    input_ref = review_evidence.portable_artifact_reference(
        artifact_store, stage_handoff.store_manifest(artifact_store, handoff)
    )

    stage_authority = {
        "schema": "taskplane.stage-authority-binding/v1",
        **{
            key: root_authority[key]
            for key in (
                "run_id",
                "repository_id",
                "repository_key",
                "worktree_id",
                "target_revision",
                "worktree_revision",
                "requirement_id",
                "requirement_revision",
                "actor",
                "session_id",
                "authority_revision",
            )
        },
        "design_revision": None,
        "design_fingerprint": None,
        "authority_fingerprint": authority_fingerprint,
    }
    root_identity = {
        "schema": "taskplane.loop-root-stage-identity/v1",
        "run_id": run_id,
        "stage_kind": kind,
        "requirement": requirement_identity,
        "input_manifest_fingerprint": input_ref["fingerprint"],
        "authority_fingerprint": authority_fingerprint,
    }
    root_token = review_evidence.content_fingerprint(root_identity)[:32]
    stage_id = f"stage-{kind}-root-{root_token}"
    stage_entities, lifecycle = _ports._stage_lifecycle(ws, store, manifest, stage_authority)
    stage = stage_entities.create_stage(
        run_id=run_id,
        stage_id=stage_id,
        requirement=requirement_identity,
        design=None,
        stage_kind=kind,
        parent_stage_ids=[],
        predecessor_stage_ids=[],
        input_manifest_ref=input_ref,
        execution_root_id=f"execution-{stage_id}",
        deliverables=_ports._stage_loop_deliverables(kind, state),
        selected_artifacts=[selected],
        budget={"attempt_limit": max(1, int(state.get("max_fix_cycles") or 0) + 1)},
        dependencies=sorted(set(str(value) for value in (requirement.get("depends_on") or []))),
        contracts=contracts,
        authority=stage_authority,
        created_at=authorized_at,
    )
    root_contract = _ports._step_contract(step, dict(state), ws)
    declared_scope = _ports._stage_loop_scope(
        root_contract["coding"]["scope_paths"],
        root_contract["coding"].get("out_of_scope_paths") or [],
    )
    _ports._preflight_stage_dispatch(stage, handoff, declared_scope)
    operation_id = "bootstrap-root-" + stage["fingerprint"][:32]
    receipt = lifecycle.start_stage(
        stage,
        expected_revision=int(manifest["revision"]),
        operation_id=operation_id,
        expected_predecessor_fingerprints={},
        foreground=True,
    )
    checked = _ports.tp.verify_stage_receipt(
        receipt, expected_operation="start_stage", expected_stage_id=stage_id
    )
    with _ports.mutate(ws) as initialized:
        initialized.pop("_stage_native_new_run_pristine", None)
    return checked


def task_phase_state(_ports, ws, state, task_id=None):
    """Project one explicitly selected task without changing the run cursor."""
    locator = _ports.runtime_storage.load_workspace_locator(ws) or {}
    bound = locator.get("task_id")
    if bound and task_id and bound != task_id:
        raise ValueError("worker cannot select a sibling task")
    selected = bound or task_id
    if selected is None or state is None:
        return state
    indexes = [
        index for index, task in enumerate(state.get("tasks") or []) if task.get("id") == selected
    ]
    if len(indexes) != 1:
        raise ValueError("phase requires one exact task in this run")
    view = _ports._copy_json(state)
    view["current_task"] = indexes[0]
    task = view["tasks"][indexes[0]]
    if state.get("parallel"):
        view["step"] = task.get("phase_step") or {
            "pending": "execute",
            "running": "execute",
            "built": "evaluate",
        }.get(task.get("status"), state["step"])
        if task.get("evaluate_child_evidence") is not None:
            view["evaluate_child_evidence"] = task["evaluate_child_evidence"]
        else:
            view.pop("evaluate_child_evidence", None)
        if task.get("_submission") is not None:
            view["_submission"] = task["_submission"]
        else:
            view.pop("_submission", None)
    return view


def _stage_loop_context(
    _ports, ws: str, state: Mapping[str, object] | None = None, *, stage_id: str | None = None
) -> dict | None:
    """Resolve an explicit task stage, or the orchestrator's foreground."""
    locator = _ports.runtime_storage.load_workspace_locator(ws)
    if not isinstance(locator, _ports.Mapping):
        return None
    run_id = str(locator.get("run_id") or "")
    if not run_id:
        return None
    store = _ports._stage_store(ws, run_id)
    manifest = store.load(run_id)
    if manifest.get("schema") != "taskplane.run/v4":
        raise ValueError("unsupported_run_schema")
    projection = manifest.get("active_stage_projection")
    if not isinstance(projection, _ports.Mapping):
        raise ValueError("stage-native loop projection is unavailable")
    active = list(projection.get("active_stage_ids") or [])
    foreground = stage_id
    if foreground is None and isinstance(state, _ports.Mapping):
        task = _ports._current_task(dict(state)) or {}
        task_id = str(locator.get("task_id") or task.get("id") or "")
        bindings = state.get("_stage_bindings")
        task_bindings = bindings.get(task_id) if isinstance(bindings, _ports.Mapping) else None
        kind = _ports._LOOP_STAGE_KINDS.get(str(state.get("step") or ""))
        if isinstance(task_bindings, _ports.Mapping) and kind:
            bound = task_bindings.get(kind)
            if isinstance(bound, str) and bound:
                foreground = bound
        if locator.get("task_id") and foreground is None:
            raise ValueError("worker has no bound stage for its current phase")
    if foreground is None:
        foreground = projection.get("foreground_stage_id")
    if foreground is None and len(active) == 1:
        foreground = active[0]
    if foreground is not None and foreground not in active:
        raise ValueError("stage-native loop foreground is invalid")
    if len(active) > 1 and foreground is None:
        raise ValueError("stage-native loop foreground is ambiguous")
    if foreground is None:
        return {
            "store": store,
            "manifest": manifest,
            "run_id": run_id,
            "stage": None,
            "lifecycle": None,
            "stage_entities": None,
        }
    stage = _ports._indexed_stage(store, manifest, run_id, str(foreground))
    stage_entities, lifecycle = _ports._stage_lifecycle(ws, store, manifest, stage.get("authority"))
    return {
        "store": store,
        "manifest": manifest,
        "run_id": run_id,
        "stage": stage,
        "lifecycle": lifecycle,
        "stage_entities": stage_entities,
    }


def _stage_loop_identity(
    _ports, stage_entities: object, prefix: str, material: Mapping[str, object]
) -> str:
    fingerprint = stage_entities.request_fingerprint(dict(material))
    return prefix + fingerprint[:32]


def _stage_loop_dispatch(
    _ports,
    ws: str,
    state: Mapping[str, object],
    *,
    slot: str,
    declared_scope: Mapping[str, object] | None = None,
    stage_id: str | None = None,
) -> dict | None:
    """Resume the exact foreground stage and return its bounded dispatch."""
    context = _ports._stage_loop_context(ws, state, stage_id=stage_id)
    if context is None:
        return None
    stage = context.get("stage")
    lifecycle = context.get("lifecycle")
    stage_entities = context.get("stage_entities")
    if not isinstance(stage, dict) or lifecycle is None:
        raise ValueError("stage-native loop has no active foreground stage")
    if stage_entities is None:
        try:
            if _ports.__package__:
                from . import stage_entities as stage_entities_module
            else:
                import stage_entities as stage_entities_module
        except ImportError:
            import stage_entities as stage_entities_module
        stage_entities = stage_entities_module
    step = str(state.get("step") or "")
    expected_kind = _ports._LOOP_STAGE_KINDS.get(step)
    if expected_kind is None or stage.get("stage_kind") != expected_kind:
        raise ValueError(
            f"stage-native loop expected {expected_kind or step!r}, found "
            f"{stage.get('stage_kind')!r}"
        )
    identity = {
        "schema": "taskplane.loop-stage-dispatch/v1",
        "run_id": stage["run_id"],
        "stage_id": stage["stage_id"],
        "stage_fingerprint": stage["fingerprint"],
        "step": step,
        "slot": str(slot),
    }
    retries = _ports._phase_bridge_retries(context)
    if retries:
        identity["phase_operation"] = retries[-1]["result"]["next_operation"]
    operation_id = _ports._stage_loop_identity(stage_entities, "loop-dispatch-", identity)
    attempt_id = _ports._stage_loop_identity(
        stage_entities, "attempt-", {**identity, "operation_id": operation_id}
    )
    receipt = lifecycle.resume_stage(
        str(stage["run_id"]),
        stage_id=str(stage["stage_id"]),
        expected_head_fingerprint=str(stage["fingerprint"]),
        expected_revision=int(context["manifest"]["revision"]),
        operation_id=operation_id,
        attempt_id=attempt_id,
    )
    return _ports._stage_dispatch(
        context["store"],
        lifecycle,
        receipt,
        stage,
        attempt_id=attempt_id,
        declared_scope=declared_scope,
    )


def _stage_loop_deliverables(_ports, kind: str, state: Mapping[str, object]) -> list[str]:
    if kind == "build":
        current = _ports._current_task(dict(state)) or {}
        if current.get("id"):
            return [str(current["id"])]
        return ["build-output"]
    return {
        "product": ["product-requirement"],
        "design": ["design-contract"],
        "plan": ["implementation-plan"],
        "evaluate": ["evaluation-verdict"],
        "engineering": ["engineering-review"],
        "retro": ["retrospective"],
    }.get(kind, [f"{kind}-output"])


def _stage_loop_scope(_ports, scope_paths: object, out_of_scope_paths: object = ()) -> dict:
    return {
        "scope_paths": sorted(set(str(row) for row in (scope_paths or []) if str(row).strip())),
        "out_of_scope_paths": sorted(
            set(str(row) for row in (out_of_scope_paths or []) if str(row).strip())
        ),
    }


def _stage_loop_completion_reference(
    _ports,
    ws: str,
    lifecycle: object,
    stage: Mapping[str, object],
    *,
    from_step: str,
    to_step: str,
    completion: Mapping[str, object],
) -> dict:
    """Commit the exact stage result before it can terminalize ``done``.

    A predecessor input handoff is startup context, never proof that the
    current stage completed.  This artifact is consequently made only from
    the gate/approval/Retro result that authorized this transition.
    """
    if not isinstance(completion, _ports.Mapping) or not completion:
        raise ValueError("stage-native loop transition lacks committed completion evidence")
    try:
        detached = _ports.json.loads(
            _ports.json.dumps(
                dict(completion),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("stage completion result is not canonical JSON") from exc
    artifact_store = lifecycle._artifact_store()  # noqa: SLF001
    value = {
        "schema": "taskplane.loop-stage-completion/v1",
        "run_id": stage["run_id"],
        "stage_id": stage["stage_id"],
        "stage_fingerprint": stage["fingerprint"],
        "from_step": str(from_step),
        "to_step": str(to_step),
        "workspace_revision": _ports.tp.git_head(ws),
        "result": detached,
    }
    native = artifact_store.put("completion-evidence", value)
    try:
        if _ports.__package__:
            from . import review_evidence
        else:
            import review_evidence
    except ImportError:
        import review_evidence
    return review_evidence.portable_artifact_reference(artifact_store, native)


def _before_stage_output_component_open(
    _ports, root: str, relative_path: str, component_index: int
) -> None:
    """Deterministic no-op seam for substitution regression tests."""


def _stage_loop_open_directory_no_follow(_ports, path: str) -> int:
    """Open an absolute directory one no-follow component at a time."""
    if not hasattr(_ports.os, "O_NOFOLLOW") or _ports.os.open not in _ports.os.supports_dir_fd:
        raise ValueError("race-safe stage output open is unavailable")
    absolute = _ports.os.path.abspath(path)
    drive, tail = _ports.os.path.splitdrive(absolute)
    anchor = drive + _ports.os.sep if absolute.startswith(_ports.os.sep) else drive
    if not anchor:
        raise ValueError("stage output root is not absolute")
    flags = (
        _ports.os.O_RDONLY
        | _ports.os.O_DIRECTORY
        | _ports.os.O_NOFOLLOW
        | getattr(_ports.os, "O_CLOEXEC", 0)
    )
    descriptor = _ports.os.open(anchor, flags)
    try:
        for component in [part for part in tail.split(_ports.os.sep) if part]:
            opened = _ports.os.open(component, flags, dir_fd=descriptor)
            _ports.os.close(descriptor)
            descriptor = opened
        return descriptor
    except Exception:
        _ports.os.close(descriptor)
        raise


def _stage_loop_read_output_no_follow(
    _ports, root: str, relative_path: str, *, required: bool, remaining_bytes: int
) -> bytes | None:
    """Read one regular file through a descriptor-relative confined walk."""
    components = relative_path.split("/")
    if not components or any(part in {"", ".", ".."} for part in components):
        raise ValueError("stage completion output path is not canonical")
    root_fd = _ports._stage_loop_open_directory_no_follow(root)
    descriptor = root_fd
    try:
        for index, component in enumerate(components):
            _ports._before_stage_output_component_open(root, relative_path, index)
            flags = _ports.os.O_RDONLY | _ports.os.O_NOFOLLOW | getattr(_ports.os, "O_CLOEXEC", 0)
            if index < len(components) - 1:
                flags |= _ports.os.O_DIRECTORY
            try:
                opened = _ports.os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not required:
                    return None
                raise ValueError(
                    f"required stage completion output is missing: {relative_path}"
                ) from None
            if descriptor != root_fd:
                _ports.os.close(descriptor)
            descriptor = opened
        before = _ports.os.fstat(descriptor)
        if not _ports.stat.S_ISREG(before.st_mode):
            raise ValueError("stage completion output is not a regular file")
        limit = min(_ports._STAGE_OUTPUT_MAX_FILE_BYTES, remaining_bytes)
        if before.st_size > limit:
            raise ValueError("stage completion output exceeds its byte bound")
        chunks = []
        total = 0
        while True:
            chunk = _ports.os.read(descriptor, min(128 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise ValueError("stage completion output exceeds its byte bound")
        after = _ports.os.fstat(descriptor)
        stable = (
            _ports.stat.S_ISREG(after.st_mode)
            and before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
            and total == after.st_size
        )
        if not stable:
            raise ValueError("stage completion output changed while sealing")
        return b"".join(chunks)
    except OSError as exc:
        raise ValueError(f"stage completion output could not be opened safely: {exc}") from exc
    finally:
        if descriptor != root_fd:
            _ports.os.close(descriptor)
        _ports.os.close(root_fd)


def _stage_loop_trusted_output_workspace(
    _ports, lifecycle: object, completion: Mapping[str, object], output: Mapping[str, object]
) -> tuple[str, object]:
    """Derive the capture root from stage storage and registered submission."""
    artifact_store = lifecycle._artifact_store()  # noqa: SLF001
    current_workspace = _ports.os.path.abspath(str(artifact_store.workspace))
    raw = str(output.get("source_workspace") or "").strip()
    if not raw or not _ports.os.path.isabs(raw) or _ports.os.path.normpath(raw) != raw:
        raise ValueError("stage completion output workspace is unavailable")
    claimed = _ports.os.path.abspath(raw)
    if claimed == current_workspace:
        return claimed, artifact_store

    submission = completion.get("submission")
    task_id = str(completion.get("task_id") or "")
    if (
        not isinstance(submission, _ports.Mapping)
        or not str(submission.get("fingerprint") or "")
        or not task_id
        or str(submission.get("task") or "") != task_id
    ):
        raise ValueError("stage completion output workspace is unauthorized")
    expected = _ports.os.path.abspath(
        _ports.runtime_storage.task_worktree_path(current_workspace, task_id)
    )
    if claimed != expected:
        raise ValueError("stage completion output workspace is unauthorized")
    current_locator = _ports.runtime_storage.load_workspace_locator(current_workspace)
    source_locator = _ports.runtime_storage.load_workspace_locator(claimed)
    registration = _ports.runtime_storage.load_task_worktree_registration(
        current_workspace, task_id
    )
    identity = ("run_id", "repo_id", "repository_key")
    if (
        not isinstance(current_locator, _ports.Mapping)
        or not isinstance(source_locator, _ports.Mapping)
        or not isinstance(registration, _ports.Mapping)
        or any(current_locator.get(key) != source_locator.get(key) for key in identity)
        or registration.get("run_id") != current_locator.get("run_id")
        or registration.get("task_id") != task_id
        or _ports.os.path.abspath(str(registration.get("path") or "")) != claimed
        or _ports.os.path.abspath(str(registration.get("primary_checkout") or ""))
        != current_workspace
    ):
        raise ValueError("stage completion submission workspace is unbound")
    step = str(submission.get("step") or "")
    if step not in {"execute", "fix", "evaluate", "em"}:
        raise ValueError("stage completion submission step is invalid")
    evidence_paths = _ports.runtime_storage.submission_evidence_paths(claimed, step)
    if _ports.tp.workspace_fingerprint(
        claimed, submission.get("snapshot"), extra_paths=evidence_paths
    ) != submission.get("fingerprint"):
        raise ValueError("stage completion submission workspace is stale")
    return claimed, artifact_store


def _stage_loop_managed_evidence_paths(
    _ports, source_workspace: str, completion: Mapping[str, object], output: Mapping[str, object]
) -> dict[str, tuple[str, str]]:
    """Prove exact external evidence paths from the validated submission."""
    step = str(output.get("managed_evidence_step") or "")
    submission = completion.get("submission")
    if step not in {"evaluate", "em"}:
        return {}
    if not isinstance(submission, _ports.Mapping) or not str(submission.get("fingerprint") or ""):
        raise ValueError("managed completion evidence lacks its submission")
    locator = _ports.runtime_storage.load_workspace_locator(source_workspace)
    if not isinstance(locator, _ports.Mapping):
        raise ValueError("managed completion evidence lacks its run locator")
    paths = locator.get("paths")
    if not isinstance(paths, _ports.Mapping):
        raise ValueError("managed completion evidence roots are unavailable")
    roots = []
    for key in ("evidence",) if step == "evaluate" else ("artifacts",):
        raw_root = str(paths.get(key) or "").strip()
        if not raw_root or not _ports.os.path.isabs(raw_root):
            raise ValueError("managed completion evidence root is unavailable")
        supplied_root = _ports.os.path.abspath(raw_root)
        if _ports.os.path.normpath(supplied_root) != supplied_root:
            raise ValueError("managed completion evidence root is not canonical")
        roots.append(supplied_root)
    allowed = {}
    for expected in _ports.runtime_storage.submission_evidence_paths(source_workspace, step):
        canonical = _ports.os.path.abspath(str(expected))
        if _ports.os.path.normpath(canonical) != canonical:
            raise ValueError("managed completion evidence path is not canonical")
        try:
            contained = any(_ports.os.path.commonpath((root, canonical)) == root for root in roots)
        except ValueError:
            contained = False
        if not contained:
            raise ValueError("managed completion evidence escaped its run root")
        root = next(root for root in roots if _ports.os.path.commonpath((root, canonical)) == root)
        relative = _ports.os.path.relpath(canonical, root).replace(_ports.os.sep, "/")
        allowed[canonical] = (root, relative)
    return allowed


def _stage_loop_decision_completion(
    _ports, ws: str, *, schema: str, step: str, outcome: str, result: Mapping[str, object]
) -> dict:
    """Create one bounded, file-free artifact for a control decision."""
    try:
        encoded = _ports.json.dumps(
            dict(result), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("stage control decision is not canonical JSON") from exc
    if len(encoded.encode("utf-8")) > 16 * 1024:
        raise ValueError("stage control decision exceeds its 16 KiB bound")
    detached = _ports.json.loads(encoded)
    return {
        "schema": str(schema),
        "step": str(step),
        "outcome": str(outcome),
        "workspace_revision": _ports.tp.git_head(ws),
        **detached,
        "_stage_output": {
            "source_workspace": _ports.os.path.abspath(ws),
            "sources": [],
            "values": {"decision": detached},
        },
    }


def _stage_loop_completion_outputs(
    _ports, lifecycle: object, completion: Mapping[str, object]
) -> tuple[dict, list[dict], dict | None, dict | None]:
    """Seal current-stage outputs and derive an exact Build target pair."""
    if not isinstance(completion, _ports.Mapping) or not completion:
        raise ValueError("stage completion result is missing")
    output = completion.get("_stage_output")
    if not isinstance(output, _ports.Mapping):
        raise ValueError("stage completion output declaration is missing")
    source_workspace, artifact_store = _ports._stage_loop_trusted_output_workspace(
        lifecycle, completion, output
    )
    managed_evidence = _ports._stage_loop_managed_evidence_paths(
        source_workspace, completion, output
    )

    snapshots = []
    seen = set()
    sources = output.get("sources") or []
    if not isinstance(sources, list) or len(sources) > _ports._STAGE_OUTPUT_MAX_SOURCES:
        raise ValueError("stage completion output source count is invalid")
    captured_bytes = 0
    for index, raw in enumerate(sources):
        if not isinstance(raw, _ports.Mapping):
            raise ValueError("stage completion output source is invalid")
        supplied = str(raw.get("path") or "").strip()
        if not supplied:
            raise ValueError("stage completion output path is missing")
        if _ports.os.path.isabs(supplied):
            path = _ports.os.path.abspath(supplied)
            if path not in managed_evidence:
                raise ValueError("absolute stage completion output path is unauthorized")
            root, relative = managed_evidence[path]
        else:
            normalized = supplied.replace("\\", "/")
            components = normalized.split("/")
            if any(component in {"", ".", ".."} for component in components):
                raise ValueError("stage completion output path is not canonical")
            root, relative = source_workspace, normalized
        logical = str(raw.get("logical_path") or "").strip()
        if not logical:
            logical = (
                relative
                if not _ports.os.path.isabs(supplied)
                else f"evidence/{index:03d}-{_ports.os.path.basename(path)}"
            )
        logical = logical.replace("\\", "/")
        logical_components = logical.split("/")
        if (
            _ports.os.path.isabs(logical)
            or not logical
            or any(component in {"", ".", ".."} for component in logical_components)
        ):
            raise ValueError("stage completion output path is not relative")
        if logical in seen:
            continue
        seen.add(logical)
        data = _ports._stage_loop_read_output_no_follow(
            root,
            relative,
            required=raw.get("required") is True,
            remaining_bytes=_ports._STAGE_OUTPUT_MAX_TOTAL_BYTES - captured_bytes,
        )
        if data is None:
            continue
        captured_bytes += len(data)
        import base64
        import hashlib

        try:
            content = data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            content = base64.b64encode(data).decode("ascii")
            encoding = "base64"
        snapshots.append(
            {
                "schema": "taskplane.loop-stage-output-snapshot/v1",
                "path": logical,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "encoding": encoding,
                "content": content,
            }
        )

    public = {
        str(key): value for key, value in completion.items() if not str(key).startswith("_stage_")
    }
    bundle = {
        "schema": "taskplane.loop-stage-output-bundle/v1",
        "step": public.get("step"),
        "outcome": public.get("outcome"),
        "task_id": public.get("task_id"),
        "files": sorted(snapshots, key=lambda row: row["path"]),
        "values": dict(output.get("values") or {}),
    }
    native = artifact_store.put("stage-output", bundle)
    try:
        if _ports.__package__:
            from . import review_evidence
        else:
            import review_evidence
    except ImportError:
        import review_evidence
    artifacts = [review_evidence.portable_artifact_reference(artifact_store, native)]
    public["artifact_refs"] = artifacts

    target = commit = None
    build = output.get("build")
    if build is not None:
        if not isinstance(build, _ports.Mapping):
            raise ValueError("Build completion identity is invalid")
        sha = str(build.get("target_commit") or _ports.tp.git_head(source_workspace) or "").strip()
        if len(sha) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in sha
        ):
            raise ValueError("Build completion target commit is unavailable")
        identity = _ports.runtime_storage.resolve_repository_identity(source_workspace)
        target_material = {
            "schema": "taskplane.loop-build-target/v1",
            "repository_id": identity.repo_id,
            "task_id": str(public.get("task_id") or ""),
            "sha": sha,
        }
        target_fingerprint = review_evidence.content_fingerprint(target_material)
        target = {"repository_id": identity.repo_id, "fingerprint": target_fingerprint}
        commit = {"sha": sha, "target_fingerprint": target_fingerprint}
    public["target"] = target
    public["commit"] = commit
    return public, artifacts, target, commit


def _stage_loop_transition_operation_material(
    _ports,
    state: Mapping[str, object],
    *,
    run_id: str,
    from_step: str,
    to_step: str,
    from_kind: str,
    to_kind: str | None,
    terminal_outcome: str,
    terminal_only: bool,
    predecessor_stage_id: object,
    predecessor_head_fingerprint: object,
) -> dict:
    """Bind one loop transition to its exact immutable predecessor head."""
    current_task = _ports._current_task(dict(state)) or {}
    return {
        "schema": "taskplane.loop-stage-transition/v1",
        "run_id": str(run_id),
        "from_step": str(from_step),
        "to_step": str(to_step),
        "from_kind": str(from_kind),
        "to_kind": to_kind,
        "outcome": str(terminal_outcome),
        "predecessor_stage_id": predecessor_stage_id,
        "predecessor_head_fingerprint": predecessor_head_fingerprint,
        "task_id": current_task.get("id"),
        "fix_cycles": int(current_task.get("fix_cycles") or 0),
        "terminal_only": bool(terminal_only),
    }


def _stage_loop_transition(
    _ports,
    ws: str,
    state: Mapping[str, object],
    *,
    from_step: str,
    to_step: str,
    terminal_outcome: str = "done",
    completion: Mapping[str, object] | None = None,
    force: bool = False,
    terminal_only: bool = False,
) -> dict | None:
    """Apply the stage transition inside the enclosing run transaction."""
    if completion is None and isinstance(state.get("_stage_completion"), _ports.Mapping):
        completion = state.get("_stage_completion")
    force = bool(force or state.get("_stage_force_transition"))
    if to_step == "failed" and terminal_outcome == "done":
        terminal_outcome = "discarded"
    from_kind = _ports._LOOP_STAGE_KINDS.get(str(from_step))
    to_kind = _ports._LOOP_STAGE_KINDS.get(str(to_step))
    if from_kind is None or (from_kind == to_kind and not force and not completion):
        return None
    if (from_step, to_step) in {
        ("design", "design_approval"),
        ("plan", "plan_approval"),
        ("em", "signoff"),
    } and _ports._phase_bridge_context(ws, state) is not None:
        # Human approval retains the collected phase; it is not a new worker.
        return None
    source_state = {**state, "step": from_step}
    source_task_id = (completion or {}).get("task_id")
    if from_kind in {"build", "evaluate"} and source_task_id:
        source_state["current_task"] = next(
            index for index, row in enumerate(state["tasks"]) if row["id"] == source_task_id
        )
    context = _ports._stage_loop_context(ws, source_state)
    if context is None:
        raise ValueError("transition requires the current v4 phase aggregate")
    stage_entities = context.get("stage_entities")
    stage = context.get("stage")
    manifest = context["manifest"]
    operation_material = _ports._stage_loop_transition_operation_material(
        state,
        run_id=str(context["run_id"]),
        from_step=from_step,
        to_step=to_step,
        from_kind=from_kind,
        to_kind=to_kind,
        terminal_outcome=terminal_outcome,
        terminal_only=terminal_only,
        predecessor_stage_id=(stage.get("stage_id") if isinstance(stage, _ports.Mapping) else None),
        predecessor_head_fingerprint=(
            stage.get("fingerprint") if isinstance(stage, _ports.Mapping) else None
        ),
    )
    operations = manifest.get("stage_operations") or {}
    if stage_entities is None:
        try:
            if _ports.__package__:
                from . import stage_entities as stage_entities_module
            else:
                import stage_entities as stage_entities_module
        except ImportError:
            import stage_entities as stage_entities_module
        stage_entities = stage_entities_module
    operation_id = _ports._stage_loop_identity(
        stage_entities, "loop-transition-", operation_material
    )
    prior = operations.get(operation_id) if isinstance(operations, _ports.Mapping) else None
    if isinstance(prior, dict):
        return _ports.tp.verify_stage_receipt(prior)
    if not isinstance(stage, dict) or context.get("lifecycle") is None:
        raise ValueError("stage-native loop transition has no predecessor")
    if stage.get("stage_kind") != from_kind:
        raise ValueError(
            f"stage-native loop transition expected {from_kind!r}, found "
            f"{stage.get('stage_kind')!r}"
        )

    lifecycle = context["lifecycle"]
    evidence = []
    selected_artifacts = []
    target = commit = None
    if completion:
        completion, selected_artifacts, target, commit = _ports._stage_loop_completion_outputs(
            lifecycle, completion
        )
        evidence = [
            _ports._stage_loop_completion_reference(
                ws, lifecycle, stage, from_step=from_step, to_step=to_step, completion=completion
            )
        ]
    if terminal_outcome == "done" and not evidence:
        raise ValueError("stage-native loop transition lacks committed completion evidence")
    actor = str((stage.get("authority") or {}).get("actor") or "")
    authorized_at = _ports.time.strftime("%Y-%m-%dT%H:%M:%SZ", _ports.time.gmtime())
    completed = list(stage.get("deliverables") or []) if terminal_outcome == "done" else []
    if to_kind is None or terminal_only:
        reason_code = None if terminal_outcome == "done" else "loop-terminal"
        reason = None if terminal_outcome == "done" else f"loop ended at {to_step}"
        receipt = lifecycle.terminalize(
            str(stage["run_id"]),
            stage_id=str(stage["stage_id"]),
            expected_head_fingerprint=str(stage["fingerprint"]),
            expected_revision=int(manifest["revision"]),
            operation_id=operation_id,
            outcome=terminal_outcome,
            actor=actor,
            terminalized_at=authorized_at,
            reason_code=reason_code,
            reason=reason,
            completed_deliverables=completed,
            completion_evidence=evidence,
        )
        return _ports.tp.verify_stage_receipt(
            receipt, expected_operation="terminalize", expected_stage_id=str(stage["stage_id"])
        )

    try:
        if _ports.__package__:
            from . import review_evidence, stage_handoff
        else:
            import review_evidence
            import stage_handoff
    except ImportError:
        import review_evidence
        import stage_handoff
    artifact_store = lifecycle._artifact_store()  # noqa: SLF001
    authority = dict(stage["authority"])
    authorization = {
        "actor": authority["actor"],
        "session_id": authority["session_id"],
        "authorized_at": authorized_at,
        "operation_id": operation_id,
        "authority_record": {
            "schema": "taskplane.authority-record-reference/v1",
            "authority_schema": "taskplane.consolidated-authorization/v1",
            "revision": authority["authority_revision"],
            "fingerprint": authority["authority_fingerprint"],
        },
    }
    successor_id = _ports._stage_loop_identity(
        stage_entities, "stage-", {**operation_material, "predecessor": stage["stage_id"]}
    )
    if not evidence:
        raise ValueError("stage-native successor transition lacks stage-owned evidence")
    from taskplane import phase_amendment

    amended = phase_amendment.current(_ports, ws, dict(source_state))
    phase_attempt = _ports._phase_bridge_pending(ws, source_state)
    if amended is not None and amended["phase"] == "design":
        if from_kind != "design" or to_kind != "plan" or not state.get("design_approved_by"):
            raise ValueError("amended Design requires separate human approval before Plan")
        selected_artifacts = [row["reference"] for row in amended["artifacts"]]
        evidence.append(state["phase_amendment"]["receipt"])
        next_handoff = stage_handoff.create_manifest(
            artifact_store,
            producer_stage_id=str(stage["stage_id"]),
            producer_outcome="done",
            requirement=stage["requirement"],
            design=stage.get("design"),
            target=target,
            commit=commit,
            contracts={
                "provided": list(stage.get("contracts") or []),
                "consumed": [],
                "changed": [],
            },
            deliverables=completed,
            evidence_references=evidence,
            selected_artifacts=selected_artifacts,
            exclusions=sorted(stage_handoff.REQUIRED_EXCLUSIONS),
            authorization=authorization,
        )
        native_ref = stage_handoff.store_manifest(artifact_store, next_handoff)
    elif phase_attempt is not None or _ports._phase_bridge_context(ws, source_state) is not None:
        _ports._phase_bridge_gate_check(ws, source_state)
        if terminal_outcome != "done":
            raise ValueError("phase collection cannot authorize a non-successor transition")
        native_ref = phase_attempt["phase_runtime"]["completion"]["handoff"]
        next_handoff = stage_handoff.read_v2_manifest(
            artifact_store,
            native_ref,
            expected_authority_revision=authority["authority_revision"],
            expected_authority_fingerprint=authority["authority_fingerprint"],
        )
        selected_artifacts = next_handoff["selected_artifacts"]
        evidence = evidence + next_handoff["evidence_references"]
    else:
        next_handoff = stage_handoff.create_manifest(
            artifact_store,
            producer_stage_id=str(stage["stage_id"]),
            producer_outcome=terminal_outcome,
            requirement=stage["requirement"],
            design=stage.get("design"),
            target=target,
            commit=commit,
            contracts={
                "provided": list(stage.get("contracts") or []),
                "consumed": [],
                "changed": [],
            },
            deliverables=completed,
            evidence_references=evidence,
            selected_artifacts=selected_artifacts,
            exclusions=sorted(stage_handoff.REQUIRED_EXCLUSIONS),
            authorization=authorization,
            allow_nonconsumable_reuse=terminal_outcome in {"closed", "discarded"},
        )
        native_ref = stage_handoff.store_manifest(artifact_store, next_handoff)
    input_ref = review_evidence.portable_artifact_reference(artifact_store, native_ref)
    dependencies = []
    if to_kind == "engineering":
        for task in state.get("tasks") or []:
            if task.get("status") != "passed":
                raise ValueError("Engineering requires every task to pass Evaluate")
            evaluated = ((state.get("_stage_bindings") or {}).get(task["id"]) or {}).get("evaluate")
            if not evaluated:
                raise ValueError("Engineering task has no accepted Evaluate stage")
            if evaluated != stage["stage_id"]:
                dependencies.append(evaluated)
    successor = stage_entities.create_stage(
        run_id=str(stage["run_id"]),
        stage_id=successor_id,
        requirement=stage["requirement"],
        design=stage.get("design"),
        stage_kind=to_kind,
        parent_stage_ids=[],
        predecessor_stage_ids=[str(stage["stage_id"])],
        input_manifest_ref=input_ref,
        execution_root_id=f"execution-{successor_id}",
        deliverables=_ports._stage_loop_deliverables(to_kind, state),
        selected_artifacts=selected_artifacts,
        budget=dict(stage.get("budget") or {}),
        dependencies=sorted(set(dependencies)),
        contracts=list(stage.get("contracts") or []),
        authority=authority,
        created_at=authorized_at,
    )
    _ports._preflight_stage_dispatch(successor, next_handoff)
    receipt = lifecycle.terminalize_and_start(
        str(stage["stage_id"]),
        successor,
        expected_head_fingerprint=str(stage["fingerprint"]),
        expected_revision=int(manifest["revision"]),
        operation_id=operation_id,
        outcome=terminal_outcome,
        actor=actor,
        terminalized_at=authorized_at,
        completed_deliverables=completed,
        completion_evidence=evidence,
    )
    task = _ports._current_task(dict(state))
    if (
        task is not None
        and to_kind in {"build", "evaluate"}
        and (not state.get("parallel") or from_kind in {"build", "evaluate"})
    ):
        state.setdefault("_stage_bindings", {}).setdefault(task["id"], {})[to_kind] = successor_id
    return _ports.tp.verify_stage_receipt(
        receipt, expected_operation="terminalize_and_start", expected_stage_id=successor_id
    )


def stage_history(
    _ports, ws: str, run_id: str, *, cursor: object = None, limit: int | None = None
) -> dict:
    """Public bounded read helper used by non-CLI host adapters."""
    if limit is None:
        limit = _ports.STAGE_HISTORY_MAX_ITEMS
    try:
        return _ports._stage_history(
            _ports._stage_store(ws, run_id),
            run_id,
            {
                "cursor": cursor,
                "limit": limit,
            },
        )
    except Exception as exc:
        return _ports._stage_command_error("history", exc)


def _stage_history(_ports, store: object, run_id: str, request: Mapping[str, object]) -> dict:
    manifest = store.load(run_id)
    if manifest.get("schema") != "taskplane.run/v4":
        raise ValueError("unsupported_run_schema: stage history requires taskplane.run/v4")
    raw_limit = request.get("limit", _ports.STAGE_HISTORY_MAX_ITEMS)
    if (
        isinstance(raw_limit, bool)
        or not isinstance(raw_limit, int)
        or not 1 <= raw_limit <= _ports.STAGE_HISTORY_MAX_ITEMS
    ):
        raise ValueError(f"history limit must be 1..{_ports.STAGE_HISTORY_MAX_ITEMS}")
    raw_cursor = request.get("cursor")
    if raw_cursor is None:
        offset = 0
    elif isinstance(raw_cursor, str) and raw_cursor.isdigit():
        offset = int(raw_cursor)
    elif isinstance(raw_cursor, int) and not isinstance(raw_cursor, bool):
        offset = raw_cursor
    else:
        raise ValueError("history cursor is invalid")
    if offset < 0:
        raise ValueError("history cursor is invalid")

    heads = manifest.get("stage_heads")
    if not isinstance(heads, _ports.Mapping):
        raise ValueError("stage history is unavailable")
    stage_ids = sorted(str(stage_id) for stage_id in heads)
    page_ids = stage_ids[offset : offset + raw_limit]
    items = []
    for stage_id in page_ids:
        head = heads[stage_id]
        if not isinstance(head, _ports.Mapping) or not isinstance(
            head.get("summary"), _ports.Mapping
        ):
            raise ValueError("stage history contains an invalid head")
        # Return the already bounded summary.  Do not open the stage object,
        # predecessor tree, trace, transcript, lease, or meter.
        items.append(dict(head["summary"]))
    next_offset = offset + len(page_ids)
    page_set = set(page_ids)
    lineage = [
        dict(row)
        for row in manifest.get("lineage") or []
        if isinstance(row, _ports.Mapping) and str(row.get("child_stage_id") or "") in page_set
    ]
    # One page is bounded independently from the persisted lineage fan-in.
    lineage = lineage[: _ports.STAGE_HISTORY_MAX_ITEMS]
    return {
        "schema": _ports.STAGE_HISTORY_SCHEMA,
        "run_id": run_id,
        "revision": manifest.get("revision"),
        "stages": items,
        "lineage": lineage,
        "cursor": str(offset),
        "next_cursor": (str(next_offset) if next_offset < len(stage_ids) else None),
    }


def _project_bound_stage_start(_ports, ws, store, lifecycle, stage, *, foreground, receipt=None):
    """Validate and update the workflow checkpoint for an explicit stage start."""
    steps = {"product": "pm", "design": "design", "plan": "plan"}
    if not foreground or stage["stage_kind"] not in steps:
        return
    with _ports.mutate(ws) as state:
        if state is None:
            return
        if state["run_id"] != stage["run_id"]:
            raise ValueError("stage start belongs to a different run")
        current = store.load(stage["run_id"])
        lifecycle._check_authority(stage["authority"], current, stage)
        if state["step"] not in steps.values() or state.get("tasks"):
            raise ValueError("stage start cannot replace delivery task state")
        if _ports.tp._active_worker_contracts(ws):
            raise ValueError("stage start cannot replace an active worker")
        if receipt is not None:
            if (
                current["stage_operations"].get(receipt["operation_id"]) != receipt
                or current["active_stage_projection"]["foreground_stage_id"] != stage["stage_id"]
            ):
                raise ValueError("stage start receipt or foreground is stale")
            state["step"] = steps[stage["stage_kind"]]
            state["design_required"] = state.get("design_required") or state["step"] == "design"


def _start_or_reuse_stage(_ports, ws, action, data, store, run_id, stage_entities, lifecycle):
    stage_value = data.get("stage") or data.get("successor_stage")
    if not isinstance(stage_value, _ports.Mapping):
        raise ValueError(f"{action} requires stage")
    stage = stage_entities.validate_stage(stage_value)
    current = store.load(run_id)
    predecessors = [
        _ports._indexed_stage(store, current, run_id, str(stage_id))
        for stage_id in stage["predecessor_stage_ids"]
    ]
    verified_handoff = _ports._verified_stage_handoff(lifecycle, store, current, stage)
    if action == "reuse":
        reason = data.get("reason")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or reason != reason.strip()
            or len(reason.encode("utf-8")) > 4 * 1024
            or any(ord(character) < 32 or ord(character) == 127 for character in reason)
        ):
            raise ValueError("reuse requires an attributable reason")
        if not stage.get("predecessor_stage_ids"):
            raise ValueError("reuse requires a predecessor stage")
        producer = verified_handoff.get("producer")
        producer_id = producer.get("stage_id") if isinstance(producer, _ports.Mapping) else None
        selected_producers = [
            predecessor
            for predecessor in predecessors
            if predecessor.get("stage_id") == producer_id
        ]
        if not selected_producers or any(
            predecessor.get("outcome") not in {"closed", "discarded"}
            for predecessor in selected_producers
        ):
            raise ValueError("reuse requires a closed or discarded predecessor")
        authorization = verified_handoff.get("authorization")
        reuse_authorization = (
            authorization.get("nonconsumable_reuse")
            if isinstance(authorization, _ports.Mapping)
            else None
        )
        actor = data.get("actor")
        if (
            not isinstance(reuse_authorization, _ports.Mapping)
            or not isinstance(actor, str)
            or not actor.strip()
            or actor != authorization.get("actor")
            or actor != stage["authority"].get("actor")
        ):
            raise ValueError("reuse requires exact non-default handoff authority")
    elif any(predecessor.get("outcome") in {"closed", "discarded"} for predecessor in predecessors):
        raise ValueError("closed or discarded predecessor requires reuse command")
    _ports._preflight_stage_dispatch(
        stage, verified_handoff, declared_scope=data.get("declared_scope")
    )
    if action == "start":
        _ports._project_bound_stage_start(
            ws, store, lifecycle, stage, foreground=data.get("foreground", True)
        )
    receipt = lifecycle.start_stage(
        stage,
        expected_revision=data.get("expected_revision"),
        operation_id=data.get("operation_id"),
        expected_predecessor_fingerprints=data.get("expected_predecessor_fingerprints"),
        foreground=data.get("foreground", True),
    )
    checked = _ports.tp.verify_stage_receipt(
        receipt, expected_operation="start_stage", expected_stage_id=str(stage["stage_id"])
    )
    if action == "reuse":
        _ports.tp.trace(
            ws,
            "stage_nondefault_reuse",
            run_id=run_id,
            stage_id=stage["stage_id"],
            actor=data["actor"],
            reason=data["reason"],
            operation_id=data["operation_id"],
            receipt_fingerprint=checked.get("result_fingerprint"),
        )
    dispatch = _ports._stage_dispatch(
        store, lifecycle, checked, stage, declared_scope=data.get("declared_scope")
    )
    if action == "start":
        _ports._project_bound_stage_start(
            ws, store, lifecycle, stage, foreground=data.get("foreground", True), receipt=checked
        )
    return {
        "schema": _ports.STAGE_COMMAND_SCHEMA,
        "command": action,
        "run_id": run_id,
        "receipt": checked,
        "dispatch": dispatch,
    }


def _resume_stage(_ports, ws, action, data, store, run_id, stage_entities, lifecycle):
    current = store.load(run_id)
    active_stage = _ports._indexed_stage(store, current, run_id, str(data.get("stage_id") or ""))
    verified_handoff = _ports._verified_stage_handoff(lifecycle, store, current, active_stage)
    requested_attempt = data.get("attempt_id")
    if requested_attempt is None:
        requested_attempt = (
            "attempt-"
            + stage_entities.request_fingerprint(
                {
                    "run_id": run_id,
                    "stage_id": data.get("stage_id"),
                    "operation_id": data.get("operation_id"),
                }
            )[:24]
        )
    serializer = getattr(_ports.tp, "stage_dispatch_payload", None)
    if not callable(serializer):
        raise ValueError("stage runtime serializer is unavailable")
    serializer(
        active_stage,
        verified_handoff,
        active_stage.get("selected_artifacts") or [],
        {
            "schema": "taskplane.stage-execution-attempt-claim/v1",
            "run_id": active_stage["run_id"],
            "stage_id": active_stage["stage_id"],
            "execution_root_id": active_stage["execution_root_id"],
            "attempt_id": requested_attempt,
        },
        attempt_id=requested_attempt,
        declared_scope=data.get("declared_scope"),
    )
    receipt = lifecycle.resume_stage(
        run_id,
        stage_id=data.get("stage_id"),
        expected_head_fingerprint=data.get("expected_head_fingerprint"),
        expected_revision=data.get("expected_revision"),
        operation_id=data.get("operation_id"),
        attempt_id=data.get("attempt_id"),
    )
    checked = _ports.tp.verify_stage_receipt(
        receipt,
        expected_operation="resume_stage",
        expected_stage_id=str(data.get("stage_id") or ""),
    )
    result = checked.get("result") or {}
    attempt_id = str(result.get("attempt_id") or "")
    current = store.load(run_id)
    stage = _ports._indexed_stage(store, current, run_id, str(data.get("stage_id") or ""))
    dispatch = _ports._stage_dispatch(
        store,
        lifecycle,
        checked,
        stage,
        attempt_id=attempt_id,
        declared_scope=data.get("declared_scope"),
    )
    return {
        "schema": _ports.STAGE_COMMAND_SCHEMA,
        "command": action,
        "run_id": run_id,
        "receipt": checked,
        "dispatch": dispatch,
    }


def stage_command(_ports, ws: str, command: str, request: object) -> dict:
    """Execute one explicit command against the current run's stage aggregate."""
    action = str(command or "").strip().lower()
    allowed = {
        "start",
        "resume",
        "terminalize",
        "terminalize-and-start",
        "split",
        "history",
        "reuse",
    }
    if action not in allowed:
        return _ports._stage_command_error(action, ValueError("unknown stage command"))
    try:
        data = _ports._validate_stage_request(action, _ports._stage_request(request))
        run_id = _ports._stage_run_id(action, data)
        if action != "history":
            try:
                singleton = _ports._load_raw(ws)
            except Exception as exc:
                return _ports._stage_command_error(action, exc)
            if refusal := _ports._run_schema_refusal(ws):
                return {
                    "schema": _ports.STAGE_COMMAND_SCHEMA,
                    "command": action,
                    "run_id": run_id,
                    "enabled": False,
                    "error": refusal["error"],
                    "stage_native": "read-only",
                }
        store = _ports._stage_store(ws, run_id)
        if action == "history":
            return _ports._stage_history(store, run_id, data)

        manifest = store.load(run_id)
        if manifest.get("schema") != "taskplane.run/v4":
            raise ValueError("unsupported_run_schema")
        stage_entities, lifecycle = _ports._stage_lifecycle(
            ws, store, manifest, data.get("authority")
        )

        if action in {"start", "reuse"}:
            return _start_or_reuse_stage(
                _ports, ws, action, data, store, run_id, stage_entities, lifecycle
            )

        if action == "resume":
            return _resume_stage(_ports, ws, action, data, store, run_id, stage_entities, lifecycle)

        if action == "terminalize":
            receipt = lifecycle.terminalize(
                run_id,
                stage_id=data.get("stage_id"),
                expected_head_fingerprint=data.get("expected_head_fingerprint"),
                expected_revision=data.get("expected_revision"),
                operation_id=data.get("operation_id"),
                outcome=data.get("outcome"),
                actor=data.get("actor"),
                terminalized_at=data.get("terminalized_at"),
                reason_code=data.get("reason_code"),
                reason=data.get("reason"),
                completed_deliverables=data.get("completed_deliverables") or (),
                completion_evidence=data.get("completion_evidence") or (),
                handoff_manifest=data.get("handoff_manifest"),
            )
            checked = _ports.tp.verify_stage_receipt(
                receipt,
                expected_operation="terminalize",
                expected_stage_id=str(data.get("stage_id") or ""),
            )
            return {
                "schema": _ports.STAGE_COMMAND_SCHEMA,
                "command": action,
                "run_id": run_id,
                "receipt": checked,
            }

        if action == "terminalize-and-start":
            successor_value = data.get("stage") or data.get("successor_stage")
            if not isinstance(successor_value, _ports.Mapping):
                raise ValueError("terminalize-and-start requires stage")
            successor = stage_entities.validate_stage(successor_value)
            current = store.load(run_id)
            predecessor = _ports._indexed_stage(
                store, current, run_id, str(data.get("predecessor_stage_id") or "")
            )
            prospective_terminal = stage_entities.terminalize_stage(
                predecessor,
                outcome=data.get("outcome"),
                actor=data.get("actor"),
                terminalized_at=data.get("terminalized_at"),
                reason_code=data.get("reason_code"),
                reason=data.get("reason"),
                completed_deliverables=data.get("completed_deliverables") or (),
                completion_evidence=data.get("completion_evidence") or (),
            )
            verified_handoff = lifecycle._read_handoff(  # noqa: SLF001
                successor["input_manifest_ref"], producer=prospective_terminal, consumer=successor
            )
            _ports._preflight_stage_dispatch(
                successor, verified_handoff, declared_scope=data.get("declared_scope")
            )
            receipt = lifecycle.terminalize_and_start(
                data.get("predecessor_stage_id"),
                successor,
                expected_head_fingerprint=data.get("expected_head_fingerprint"),
                expected_revision=data.get("expected_revision"),
                operation_id=data.get("operation_id"),
                outcome=data.get("outcome"),
                actor=data.get("actor"),
                terminalized_at=data.get("terminalized_at"),
                reason_code=data.get("reason_code"),
                reason=data.get("reason"),
                completed_deliverables=data.get("completed_deliverables") or (),
                completion_evidence=data.get("completion_evidence") or (),
                foreground=data.get("foreground", True),
            )
            checked = _ports.tp.verify_stage_receipt(
                receipt,
                expected_operation="terminalize_and_start",
                expected_stage_id=str(successor["stage_id"]),
            )
            dispatch = _ports._stage_dispatch(
                store, lifecycle, checked, successor, declared_scope=data.get("declared_scope")
            )
            return {
                "schema": _ports.STAGE_COMMAND_SCHEMA,
                "command": action,
                "run_id": run_id,
                "receipt": checked,
                "dispatch": dispatch,
            }

        current = store.load(run_id)
        parent = _ports._indexed_stage(store, current, run_id, str(data.get("stage_id") or ""))
        prospective_split = stage_entities.create_split(
            parent,
            operation_id=data.get("operation_id"),
            child_specs=data.get("child_specs") or (),
            actor=data.get("actor"),
            terminalized_at=data.get("terminalized_at"),
            reason=data.get("reason"),
        )
        scopes = data.get("declared_scopes") or {}
        if not isinstance(scopes, _ports.Mapping):
            raise ValueError("split declared scopes must be an object")
        for child in prospective_split["children"]:
            verified_handoff = lifecycle._read_handoff(  # noqa: SLF001
                child["input_manifest_ref"], producer=prospective_split["parent"], consumer=child
            )
            _ports._preflight_stage_dispatch(
                child, verified_handoff, declared_scope=scopes.get(child["stage_id"])
            )
        receipt = lifecycle.split_stage(
            run_id,
            stage_id=data.get("stage_id"),
            expected_head_fingerprint=data.get("expected_head_fingerprint"),
            expected_revision=data.get("expected_revision"),
            operation_id=data.get("operation_id"),
            child_specs=data.get("child_specs") or (),
            actor=data.get("actor"),
            terminalized_at=data.get("terminalized_at"),
            reason=data.get("reason"),
        )
        checked = _ports.tp.verify_stage_receipt(receipt, expected_operation="split_stage")
        current = store.load(run_id)
        child_heads = (checked.get("result") or {}).get("child_heads") or {}
        if not isinstance(child_heads, _ports.Mapping):
            raise ValueError("split receipt has no child heads")
        dispatches = []
        for child_id in sorted(str(value) for value in child_heads):
            child = _ports._indexed_stage(store, current, run_id, child_id)
            dispatches.append(
                _ports._stage_dispatch(
                    store, lifecycle, checked, child, declared_scope=scopes.get(child_id)
                )
            )
        return {
            "schema": _ports.STAGE_COMMAND_SCHEMA,
            "command": action,
            "run_id": run_id,
            "receipt": checked,
            "dispatches": dispatches,
        }
    except Exception as exc:
        return _ports._stage_command_error(action, exc)


def dispatch_authority_error(_ports, state):
    """Check the persisted root identity before issuing any worker input."""
    if _ports.tp.task_slot() is not None:
        return "worker slots cannot dispatch phase workers"
    authority = state.get("_stage_native_root_authority")
    if (
        not isinstance(authority, _ports.Mapping)
        or set(authority) != _ports._STAGE_ROOT_AUTHORITY_FIELDS
    ):
        return "phase dispatch requires the current root authority"
    material = {key: value for key, value in authority.items() if key != "fingerprint"}
    if (
        authority.get("schema") != _ports._STAGE_ROOT_AUTHORITY_SCHEMA
        or authority.get("run_id") != state.get("run_id")
        or authority.get("requirement_id") != state.get("requirement_id")
        or content_fingerprint(material) != authority.get("fingerprint")
    ):
        return "phase dispatch root authority is invalid"
    return None


def _stage_native_init_authority(
    _ports, ws: str, requirement_id: str | None, by: str | None
) -> dict | None:
    """Capture explicit, attributable authority before new-run state exists."""
    rid = str(requirement_id or "").strip()
    if not rid:
        raise ValueError(
            "stage-native new-run init requires --req <R-id> for an existing requirement"
        )
    requirement = _ports.reqs.get_requirement(ws, rid)
    if requirement is None:
        raise ValueError(f"stage-native new-run init requirement '{rid}' does not exist")
    actor = str(by or "").strip()
    if not actor:
        raise ValueError("stage-native new-run init requires --by <human identity>")
    if _ports._STAGE_ACTOR_IDENTIFIER.fullmatch(actor) is None:
        raise ValueError(
            "stage-native new-run init --by actor must match ^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
        )
    session_id = str(
        _ports.os.environ.get("TASKPLANE_SESSION_ID")
        or _ports.os.environ.get("CODEX_THREAD_ID")
        or _ports.os.environ.get("CLAUDE_SESSION_ID")
        or ""
    ).strip()
    if not session_id:
        raise ValueError(
            "stage-native new-run init requires TASKPLANE_SESSION_ID, "
            "CODEX_THREAD_ID, or CLAUDE_SESSION_ID"
        )
    if any(
        not value
        or len(value.encode("utf-8")) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        for value in (actor, session_id)
    ):
        raise ValueError("stage-native new-run init actor/session identity is invalid")
    try:
        locator = _ports.runtime_storage.load_workspace_locator(ws)
    except Exception as exc:
        raise ValueError(
            f"stage-native new-run init requires a governed RunStore workspace locator: {exc}"
        ) from exc
    if not isinstance(locator, _ports.Mapping):
        raise ValueError("stage-native new-run init requires a governed RunStore workspace locator")
    run_id = str(locator.get("run_id") or "")
    if not run_id:
        raise ValueError("stage-native new-run init requires a governed RunStore workspace locator")
    manifest = _ports._stage_store(ws, run_id).load(run_id)
    if manifest.get("schema") != "taskplane.run/v4" or any(
        "migration" in str(key).lower() and value not in (None, False, "", [], {})
        for key, value in manifest.items()
    ):
        raise ValueError("phase initialization requires a current v4 run")
    repository = manifest.get("repository")
    if (
        not isinstance(repository, _ports.Mapping)
        or repository.get("repo_id") != locator.get("repo_id")
        or not locator.get("repository_key")
    ):
        raise ValueError("stage-native new-run init repository identity is invalid")
    target = manifest.get("target")
    target_revision = (
        target.get("revision") or target.get("head") if isinstance(target, _ports.Mapping) else None
    )
    if not isinstance(target_revision, str) or not target_revision.strip():
        raise ValueError("stage-native new-run init requires an exact target revision")
    worktree_revision = str(_ports.tp.git_head(ws) or "").strip()
    if not worktree_revision or worktree_revision == "unknown":
        raise ValueError("stage-native new-run init requires an exact target revision")
    try:
        if _ports.__package__:
            from . import design_contract as stage_design_contract
            from . import review_evidence
        else:
            import design_contract as stage_design_contract
            import review_evidence
    except ImportError:
        import design_contract as stage_design_contract
        import review_evidence
    requirement_fingerprint = stage_design_contract.requirement_fingerprint(ws, rid)
    worktree_id = (
        "worktree-"
        + review_evidence.content_fingerprint(
            {
                "run_id": run_id,
                "repository_id": locator["repo_id"],
                "repository_key": locator["repository_key"],
                "target_revision": target_revision,
                "worktree_revision": worktree_revision,
            }
        )[:24]
    )
    record = {
        "schema": _ports._STAGE_ROOT_AUTHORITY_SCHEMA,
        "run_id": run_id,
        "repository_id": str(locator["repo_id"]),
        "repository_key": str(locator["repository_key"]),
        "worktree_id": worktree_id,
        "target_revision": str(target_revision),
        "worktree_revision": worktree_revision,
        "requirement_id": rid,
        # The current requirement store is content-versioned rather than
        # counter-versioned.  Its exact retained content fingerprint is the
        # immutable revision consumed by the root stage.
        "requirement_revision": requirement_fingerprint,
        "requirement_fingerprint": requirement_fingerprint,
        "actor": actor,
        "session_id": session_id,
        "authority_revision": 1,
    }
    record["fingerprint"] = review_evidence.content_fingerprint(record)
    if set(record) != _ports._STAGE_ROOT_AUTHORITY_FIELDS:
        raise ValueError("stage-native root bootstrap authority is invalid")
    return record


def _verified_stage_loop_wave_split(
    _ports, ws: str, ready: list[dict], *, known_bindings: Mapping[str, str] | None = None
) -> dict[str, str] | None:
    """Recover one exact, still-current loop wave split.

    A committed split receipt identifies the children if a controller stops
    before dispatching them.  At that point
    the active projection is intentionally ambiguous, so recovery must inspect
    persisted receipts before asking ``_stage_loop_context`` for a foreground.
    """
    if not ready:
        return None
    locator = _ports.runtime_storage.load_workspace_locator(ws)
    if not isinstance(locator, _ports.Mapping):
        return None
    run_id = str(locator.get("run_id") or "")
    if not run_id:
        return None
    store = _ports._stage_store(ws, run_id)
    manifest = store.load(run_id)
    if manifest.get("schema") != "taskplane.run/v4":
        return None
    try:
        if _ports.__package__:
            from . import stage_entities
        else:
            import stage_entities
    except ImportError:
        import stage_entities

    operations = manifest.get("stage_operations") or {}
    if not isinstance(operations, _ports.Mapping):
        raise ValueError("stage-native split operation index is invalid")
    heads = manifest.get("stage_heads") or {}
    projection = manifest.get("active_stage_projection") or {}
    task_ids = [str(task.get("id") or "") for task in ready]
    if not all(task_ids) or len(set(task_ids)) != len(task_ids):
        raise ValueError("stage-native wave task identity is invalid")
    expected_bindings = (
        {str(key): str(value) for key, value in known_bindings.items()}
        if isinstance(known_bindings, _ports.Mapping)
        else {}
    )
    if expected_bindings and set(expected_bindings) != set(task_ids):
        raise ValueError("stage-native wave bindings are partial")
    read_object = getattr(store, "read_stage_object", None)
    if not callable(read_object):
        raise ValueError("stage store cannot verify split child heads")
    candidates: list[dict[str, str]] = []
    for raw in operations.values():
        if not isinstance(raw, dict) or raw.get("operation") != "split_stage":
            continue
        checked = _ports.tp.verify_stage_receipt(raw, expected_operation="split_stage")
        operation_id = str(checked["operation_id"])
        if not operation_id.startswith("loop-wave-split-"):
            continue
        result = checked.get("result") or {}
        parent_head = result.get("parent_head")
        child_heads = result.get("child_heads")
        receipt_projection = result.get("active_stage_projection")
        if (
            not isinstance(parent_head, _ports.Mapping)
            or not isinstance(child_heads, _ports.Mapping)
            or not isinstance(receipt_projection, _ports.Mapping)
        ):
            raise ValueError("loop wave split receipt result is invalid")
        parent_summary = parent_head.get("summary")
        if not isinstance(parent_summary, _ports.Mapping):
            raise ValueError("loop wave split parent head is invalid")
        parent_id = str(parent_summary.get("stage_id") or "")
        if not parent_id:
            continue
        expected_ids = [
            stage_entities.split_child_id(run_id, parent_id, operation_id, ordinal)
            for ordinal in range(len(child_heads))
        ]
        if set(child_heads) != set(expected_ids):
            raise ValueError("loop wave split child identity does not match its receipt")
        if checked.get("stage_ids") != sorted([parent_id, *expected_ids]):
            raise ValueError("loop wave split receipt stage set is invalid")

        if heads.get(parent_id) != parent_head:
            continue
        parent = _ports._indexed_stage(store, manifest, run_id, parent_id)
        if (
            parent.get("stage_kind") != "build"
            or parent.get("state") != "terminal"
            or parent.get("outcome") != "closed"
        ):
            raise ValueError("loop wave split parent is not closed Build")
        receipt_lineage = result.get("lineage") or []
        manifest_lineage = manifest.get("lineage") or []
        lineage_fingerprints = {
            str(row.get("fingerprint"))
            for row in manifest_lineage
            if isinstance(row, _ports.Mapping)
        }
        if not isinstance(receipt_lineage, list) or any(
            not isinstance(row, _ports.Mapping)
            or str(row.get("fingerprint")) not in lineage_fingerprints
            for row in receipt_lineage
        ):
            raise ValueError("loop wave split lineage is not current")

        bindings: dict[str, str] = {}
        roots: list[str] = []
        for ordinal, child_id in enumerate(expected_ids):
            original_head = child_heads[child_id]
            if not isinstance(original_head, _ports.Mapping) or not isinstance(
                original_head.get("object"), _ports.Mapping
            ):
                raise ValueError("loop wave split child head is invalid")
            original = read_object(run_id, dict(original_head["object"]))
            deliverables = list(original.get("deliverables") or [])
            if len(deliverables) != 1:
                raise ValueError("loop wave split child deliverable is not singular")
            task_id = str(deliverables[0])
            if task_id in bindings:
                raise ValueError("loop wave split task binding is duplicated")
            child_id = expected_ids[ordinal]
            child = _ports._indexed_stage(store, manifest, run_id, child_id)
            if (
                child.get("stage_kind") != "build"
                or child.get("state") != "active"
                or list(child.get("parent_stage_ids") or []) != [parent_id]
                or list(child.get("predecessor_stage_ids") or [])
                or list(child.get("deliverables") or []) != [task_id]
            ):
                raise ValueError("loop wave split child does not match its task binding")
            stable_fields = (
                "run_id",
                "stage_id",
                "stage_kind",
                "requirement",
                "design",
                "parent_stage_ids",
                "predecessor_stage_ids",
                "input_manifest_ref",
                "execution_root_id",
                "deliverables",
                "selected_artifacts",
                "budget",
                "dependencies",
                "contracts",
                "authority",
                "created_at",
            )
            if any(child.get(field) != original.get(field) for field in stable_fields) or int(
                child.get("aggregate_revision") or 0
            ) < int(original.get("aggregate_revision") or 0):
                raise ValueError("loop wave split child identity advanced incompatibly")
            roots.append(str(child.get("execution_root_id") or ""))
            bindings[task_id] = child_id
        if (
            not all(roots)
            or len(set(roots)) != len(roots)
            or str(parent.get("execution_root_id") or "") in roots
        ):
            raise ValueError("loop wave split execution roots are not unique")
        if expected_bindings:
            if any(
                bindings.get(task_id) != child_id for task_id, child_id in expected_bindings.items()
            ):
                continue
            candidates.append(dict(expected_bindings))
        elif (
            list(bindings) == task_ids
            and int(manifest.get("revision") or 0) == int(checked["committed_revision"])
            and projection == receipt_projection
        ):
            # Without a persisted singleton binding, only the exact
            # post-split state is safe to reconstruct.  Once a child has
            # dispatched, the durable binding is required to identify which
            # split owns the remaining work.
            candidates.append(bindings)
    if len(candidates) > 1:
        raise ValueError("stage-native wave split recovery is ambiguous")
    return candidates[0] if candidates else None


def _persist_stage_loop_wave_bindings(
    _ports, ws: str, state: Mapping[str, object], ready: list[dict], bindings: Mapping[str, str]
) -> None:
    """Persist a verified split binding without accepting stale loop state."""
    task_ids = [str(task["id"]) for task in ready]
    with _ports.mutate(ws) as locked:
        if locked is None or locked.get("step") != "execute" or not locked.get("parallel"):
            raise ValueError("stage-native wave advanced during split binding")
        locked_tasks = {str(task.get("id")): task for task in (locked.get("tasks") or [])}
        existing_table = locked.get("_stage_bindings") or {}
        if any(
            task_id not in locked_tasks
            or locked_tasks[task_id].get("status") not in {"pending", "running"}
            or (
                locked_tasks[task_id].get("status") == "running"
                and not (
                    isinstance(existing_table, _ports.Mapping)
                    and isinstance(existing_table.get(task_id), _ports.Mapping)
                    and existing_table[task_id].get("build") == bindings[task_id]
                )
            )
            for task_id in task_ids
        ):
            raise ValueError("stage-native wave changed during split binding")
        current = _ports._verified_stage_loop_wave_split(ws, ready, known_bindings=bindings)
        if current != dict(bindings):
            raise ValueError("stage-native wave split changed before binding")
        table = locked.setdefault("_stage_bindings", {})
        for task_id, child_id in bindings.items():
            existing = table.get(task_id)
            if isinstance(existing, _ports.Mapping) and existing.get("build") not in {
                None,
                child_id,
            }:
                raise ValueError("stage-native wave binding conflicts")
            table.setdefault(task_id, {})["build"] = child_id


def _stage_loop_wave_dispatches(
    _ports, ws: str, state: Mapping[str, object], ready: list[dict]
) -> dict:
    """Bind each parallel entry to its own immutable stage/root."""
    if not ready:
        return {}
    table = state.get("_stage_bindings") or {}
    known = {
        str(task["id"]): str(binding["build"])
        for task in ready
        for binding in [table.get(str(task["id"])) if isinstance(table, _ports.Mapping) else None]
        if isinstance(binding, _ports.Mapping) and binding.get("build")
    }
    if known and len(known) != len(ready):
        raise ValueError("stage-native wave has partial persisted split bindings")
    recovered = _ports._verified_stage_loop_wave_split(ws, ready, known_bindings=(known or None))
    if recovered is not None:
        _ports._persist_stage_loop_wave_bindings(ws, state, ready, recovered)
        current = _ports.load(ws) or dict(state)
        claimed = {
            str(task.get("id"))
            for task in (current.get("tasks") or [])
            if isinstance(task, _ports.Mapping) and task.get("status") == "running"
        }
        return {
            str(task["id"]): _ports._stage_loop_dispatch(
                ws,
                current,
                slot=str(task["id"]),
                declared_scope=_ports._stage_loop_scope(
                    task.get("scope"), _ports.tp.DEFAULT_OUT_OF_SCOPE
                ),
                stage_id=recovered[str(task["id"])],
            )
            for task in ready
            if str(task["id"]) not in claimed
        }
    context = _ports._stage_loop_context(ws, state)
    if context is None:
        return {}
    parent = context.get("stage")
    lifecycle = context.get("lifecycle")
    stage_entities = context.get("stage_entities")
    if not isinstance(parent, dict) or lifecycle is None or stage_entities is None:
        raise ValueError("stage-native wave has no active build stage")
    if parent.get("stage_kind") != "build":
        raise ValueError("stage-native wave requires an active build stage")

    # A one-entry wave already owns the sole build root.  Record that exact
    # binding so later Evaluate dispatch cannot fall back to a different
    # foreground after another child becomes active.
    if len(ready) == 1:
        task_id = str(ready[0]["id"])
        with _ports.mutate(ws) as locked:
            locked.setdefault("_stage_bindings", {}).setdefault(task_id, {})["build"] = str(
                parent["stage_id"]
            )
        return {
            task_id: _ports._stage_loop_dispatch(
                ws,
                state,
                slot=task_id,
                declared_scope=_ports._stage_loop_scope(
                    ready[0].get("scope"), _ports.tp.DEFAULT_OUT_OF_SCOPE
                ),
                stage_id=str(parent["stage_id"]),
            )
        }

    try:
        if _ports.__package__:
            from . import review_evidence, stage_handoff
        else:
            import review_evidence
            import stage_handoff
    except ImportError:
        import review_evidence
        import stage_handoff
    artifact_store = lifecycle._artifact_store()  # noqa: SLF001
    task_ids = [str(task["id"]) for task in ready]
    operation_material = {
        "schema": "taskplane.loop-stage-wave-split/v1",
        "run_id": context["run_id"],
        "parent_stage_id": parent["stage_id"],
        "parent_fingerprint": parent["fingerprint"],
        "tasks": task_ids,
    }
    operation_id = _ports._stage_loop_identity(
        stage_entities, "loop-wave-split-", operation_material
    )
    terminalized_at = _ports.time.strftime("%Y-%m-%dT%H:%M:%SZ", _ports.time.gmtime())
    authority = dict(parent["authority"])
    authorization_base = {
        "actor": authority["actor"],
        "session_id": authority["session_id"],
        "authorized_at": terminalized_at,
        "authority_record": {
            "schema": "taskplane.authority-record-reference/v1",
            "authority_schema": "taskplane.consolidated-authorization/v1",
            "revision": authority["authority_revision"],
            "fingerprint": authority["authority_fingerprint"],
        },
    }
    child_specs = []
    ordinals = {str(task["id"]): index for index, task in enumerate(ready)}
    selected = list(parent.get("selected_artifacts") or [])
    for ordinal, task in enumerate(ready):
        task_id = str(task["id"])
        split_native = artifact_store.put(
            "completion-evidence",
            {
                "schema": "taskplane.loop-wave-child-input/v1",
                "run_id": context["run_id"],
                "parent_stage_id": parent["stage_id"],
                "task_id": task_id,
                "scope": _ports._stage_loop_scope(
                    task.get("scope"), _ports.tp.DEFAULT_OUT_OF_SCOPE
                ),
            },
        )
        split_evidence = review_evidence.portable_artifact_reference(artifact_store, split_native)
        authorization = {
            **authorization_base,
            "operation_id": f"{operation_id}-child-{ordinal}",
        }
        handoff = stage_handoff.create_manifest(
            artifact_store,
            producer_stage_id=str(parent["stage_id"]),
            producer_outcome="closed",
            requirement=parent["requirement"],
            design=parent.get("design"),
            target=None,
            commit=None,
            contracts={
                "provided": list(parent.get("contracts") or []),
                "consumed": [],
                "changed": [],
            },
            deliverables=[task_id],
            evidence_references=[split_evidence],
            selected_artifacts=selected,
            exclusions=sorted(stage_handoff.REQUIRED_EXCLUSIONS),
            authorization=authorization,
            allow_nonconsumable_reuse=True,
        )
        native_ref = stage_handoff.store_manifest(artifact_store, handoff)
        portable_ref = review_evidence.portable_artifact_reference(artifact_store, native_ref)
        child_specs.append(
            {
                "stage_kind": "build",
                "input_manifest_ref": portable_ref,
                "selected_artifacts": selected,
                "dependencies": [
                    "child:" + str(ordinals[str(dependency)])
                    for dependency in task.get("deps") or []
                ],
                "budget": dict(parent.get("budget") or {}),
                "deliverables": [task_id],
                "contracts": list(parent.get("contracts") or []),
            }
        )
    prospective = stage_entities.create_split(
        parent,
        operation_id=operation_id,
        child_specs=child_specs,
        actor=str(authority["actor"]),
        terminalized_at=terminalized_at,
        reason="bind independent execution roots for the parallel loop wave",
    )
    for ordinal, child in enumerate(prospective["children"]):
        handoff = lifecycle._read_handoff(  # noqa: SLF001
            child["input_manifest_ref"], producer=prospective["parent"], consumer=child
        )
        _ports._preflight_stage_dispatch(
            child,
            handoff,
            declared_scope=_ports._stage_loop_scope(
                ready[ordinal].get("scope"), _ports.tp.DEFAULT_OUT_OF_SCOPE
            ),
        )
    split_receipt = lifecycle.split_stage(
        str(parent["run_id"]),
        stage_id=str(parent["stage_id"]),
        expected_head_fingerprint=str(parent["fingerprint"]),
        expected_revision=int(context["manifest"]["revision"]),
        operation_id=operation_id,
        child_specs=child_specs,
        actor=str(authority["actor"]),
        terminalized_at=terminalized_at,
        reason="bind independent execution roots for the parallel loop wave",
    )
    _ports.tp.verify_stage_receipt(
        split_receipt, expected_operation="split_stage", expected_stage_id=str(parent["stage_id"])
    )
    bindings = _ports._verified_stage_loop_wave_split(ws, ready)
    if bindings is None:
        raise ValueError("committed loop wave split is not recoverable")
    _ports._persist_stage_loop_wave_bindings(ws, state, ready, bindings)
    dispatches = {}
    current = _ports.load(ws) or dict(state)
    for task in ready:
        task_id = str(task["id"])
        dispatches[task_id] = _ports._stage_loop_dispatch(
            ws,
            current,
            slot=task_id,
            declared_scope=_ports._stage_loop_scope(
                task.get("scope"), _ports.tp.DEFAULT_OUT_OF_SCOPE
            ),
            stage_id=bindings[task_id],
        )
    return dispatches


def _stage_loop_gate_completion(
    _ports,
    ws: str,
    state: Mapping[str, object],
    *,
    step: str,
    outcome: str,
    note: str = "",
    submission: Mapping[str, object] | None = None,
    approval: Mapping[str, object] | None = None,
    target_commit: str | None = None,
) -> dict:
    """Return the bounded result that the current gate actually validated."""
    task = _ports._current_task(dict(state)) or {}
    source_workspace = str((submission or {}).get("workspace") or task.get("workspace") or ws)
    sources = []
    values = {}
    result = {
        "schema": "taskplane.loop-gate-result/v1",
        "step": str(step),
        "outcome": str(outcome),
        "task_id": task.get("id"),
        "note": str(note or "")[:1024],
        "workspace_revision": _ports.tp.git_head(ws),
    }
    if isinstance(submission, _ports.Mapping):
        portable_submission = dict(submission)
        portable_submission.pop("workspace", None)
        portable_submission["evidence_paths"] = [
            f"evidence/{index:03d}-{_ports.os.path.basename(str(path))}"
            for index, path in enumerate(submission.get("evidence_paths") or [])
        ]
        result["submission"] = portable_submission
        for index, path in enumerate(submission.get("changed_files") or []):
            sources.append({"path": str(path), "required": False})
        for index, path in enumerate(submission.get("evidence_paths") or []):
            sources.append(
                {
                    "path": str(path),
                    "required": True,
                    "logical_path": f"evidence/{index:03d}-{_ports.os.path.basename(str(path))}",
                }
            )
    if isinstance(approval, _ports.Mapping):
        result["approval"] = dict(approval)
    if step == "pm":
        requirement_id = state.get("requirement_id")
        result["requirement_id"] = requirement_id
        spec_path = str(state.get("spec_path") or "specs/spec.md")
        result["spec_path"] = spec_path
        record = _ports.reqs.get_requirement(ws, requirement_id) if requirement_id else None
        if record is not None:
            values["requirement"] = dict(record)
        sources.append({"path": spec_path, "required": record is None})
    elif step in {"design", "design_approval"}:
        result["design_fingerprint"] = state.get(
            "design_fingerprint"
        ) or _ports._design_evidence_fingerprint(ws)
        sources.extend(
            [
                {"path": "design/design.md", "required": True},
                {"path": "design/contract.json", "required": True},
            ]
        )
    elif step in {"plan", "plan_approval"}:
        result["tasks"] = [
            str(row.get("id")) for row in (state.get("tasks") or []) if row.get("id")
        ]
        result["graph_dor"] = state.get("graph_dor")
        sources.extend(
            [
                {"path": "plan/plan.md", "required": True},
                {"path": "plan/tasks.json", "required": True},
            ]
        )
    elif step in {"em", "signoff"}:
        signoff = state.get("signoff_evidence")
        result["signoff_fingerprint"] = (
            _ports.review_session_engine.signoff_evidence_fingerprint(signoff)
            if isinstance(signoff, _ports.Mapping)
            and hasattr(_ports.review_session_engine, "signoff_evidence_fingerprint")
            else None
        )
    build_commit = str(target_commit or task.get("target_commit") or "")
    if step in {"execute", "fix"} and not any(
        source.get("logical_path") is None
        for source in sources
        if isinstance(source, _ports.Mapping)
    ):
        for path in _ports._diff_files(
            source_workspace, build_commit or str((submission or {}).get("snapshot") or "HEAD")
        ):
            sources.append({"path": path, "required": False})
    result["_stage_output"] = {
        "source_workspace": source_workspace,
        "sources": sources,
        "values": values,
        **(
            {"managed_evidence_step": str(step)}
            if isinstance(submission, _ports.Mapping)
            and submission.get("evidence_paths")
            and step in {"evaluate", "em"}
            else {}
        ),
        **({"build": {"target_commit": build_commit}} if step in {"execute", "fix"} else {}),
    }
    return result


def archive(_ports, ws: str, *, by: str) -> dict:
    """Detach the exact workspace locator and retain all run evidence."""
    if not str(by or "").strip() or _ports.tp.task_slot() is not None:
        return {"error": "archive requires a human actor outside a worker slot"}
    locator = _ports.runtime_storage.load_workspace_locator(ws)
    if not locator:
        return {"error": "workspace has no run to archive"}
    source = _ports.runtime_storage._locator_path(ws)
    destination = source + ".archived-" + str(locator["run_id"])
    if _ports.os.path.lexists(destination):
        return {"error": "archive already exists", "path": destination}
    _ports.tp.atomic_write_json(
        destination + ".receipt.json",
        {
            "schema": "taskplane.run-archive/v1",
            "run_id": locator["run_id"],
            "actor": str(by).strip(),
            "archived_at": int(_ports.time.time()),
        },
    )
    _ports.os.rename(source, destination)
    return {
        "archived": True,
        "run_id": locator["run_id"],
        "locator": destination,
        "evidence_retained": True,
    }


def resume(_ports, ws: str) -> dict:
    """Resolve a root's durable inputs without a host session or any effect.

    This is a read projection of existing owners, not another coordinator or
    a transferable authorization. `next_action` still owns dispatch and its
    current authority, capability and duplicate-operation checks.
    """
    state = _ports._load_raw(ws)
    if state is None:
        return {
            "schema": "taskplane.loop-resume/v1",
            "status": "not_started",
            "next_action": "initialize_run",
            "command": "loop init",
        }
    if refusal := _ports._run_schema_refusal(ws):
        return {"schema": "taskplane.loop-resume/v1", "status": "refused", **refusal}
    tasks = state.get("tasks") or []
    return {
        "schema": "taskplane.loop-resume/v1",
        "status": "resumable",
        "read_only": True,
        "run_id": state["run_id"],
        "goal": state["goal"],
        "step": state["step"],
        "requirement_id": state.get("requirement_id"),
        "spec_path": state.get("spec_path"),
        "state_path": _ports._loop_path(ws),
        "current_task": state.get("current_task"),
        "tasks": [
            {"id": task["id"], "scope": task.get("scope") or [], "status": task.get("status")}
            for task in tasks
        ],
        "design_fingerprint": state.get("design_fingerprint"),
        "plan_fingerprint": state.get("plan_fingerprint"),
        "next_action": "resolve_current_action",
        "command": "loop next",
        "authority": "revalidate durable authorization before effects",
    }


STAGE_COMMAND_SCHEMA = "taskplane.stage-command-result/v1"
STAGE_HISTORY_SCHEMA = "taskplane.stage-history-page/v1"
STAGE_HISTORY_MAX_ITEMS = 100
_STAGE_ROOT_AUTHORITY_SCHEMA = "taskplane.loop-root-bootstrap-authority/v1"
_STAGE_ROOT_AUTHORITY_FIELDS = frozenset(
    {
        "schema",
        "run_id",
        "repository_id",
        "repository_key",
        "worktree_id",
        "target_revision",
        "worktree_revision",
        "requirement_id",
        "requirement_revision",
        "requirement_fingerprint",
        "actor",
        "session_id",
        "authority_revision",
        "fingerprint",
    }
)
_STAGE_ACTOR_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_STAGE_RUNTIME_FIELDS = frozenset(
    {
        "agent",
        "agents",
        "conversation",
        "conversations",
        "environment",
        "env",
        "event",
        "events",
        "eventlog",
        "eventlogs",
        "lease",
        "leases",
        "log",
        "logs",
        "path",
        "root",
        "runtime",
        "runtimestate",
        "tool",
        "tools",
        "tooltranscript",
        "tooltranscripts",
        "transcript",
        "transcripts",
        "workspace",
    }
)
_STAGE_REQUEST_FIELDS = {
    "history": frozenset({"schema", "run_id", "cursor", "limit"}),
    "start": frozenset(
        {
            "schema",
            "stage",
            "expected_revision",
            "operation_id",
            "expected_predecessor_fingerprints",
            "foreground",
            "authority",
            "declared_scope",
        }
    ),
    "reuse": frozenset(
        {
            "schema",
            "stage",
            "successor_stage",
            "expected_revision",
            "operation_id",
            "expected_predecessor_fingerprints",
            "foreground",
            "authority",
            "declared_scope",
            "reason",
            "actor",
        }
    ),
    "resume": frozenset(
        {
            "schema",
            "run_id",
            "stage_id",
            "expected_head_fingerprint",
            "expected_revision",
            "operation_id",
            "attempt_id",
            "authority",
            "declared_scope",
        }
    ),
    "terminalize": frozenset(
        {
            "schema",
            "run_id",
            "stage_id",
            "expected_head_fingerprint",
            "expected_revision",
            "operation_id",
            "outcome",
            "actor",
            "terminalized_at",
            "reason_code",
            "reason",
            "completed_deliverables",
            "completion_evidence",
            "handoff_manifest",
            "authority",
        }
    ),
    "terminalize-and-start": frozenset(
        {
            "schema",
            "run_id",
            "predecessor_stage_id",
            "stage",
            "successor_stage",
            "expected_head_fingerprint",
            "expected_revision",
            "operation_id",
            "outcome",
            "actor",
            "terminalized_at",
            "reason_code",
            "reason",
            "completed_deliverables",
            "completion_evidence",
            "foreground",
            "authority",
            "declared_scope",
        }
    ),
    "split": frozenset(
        {
            "schema",
            "run_id",
            "stage_id",
            "expected_head_fingerprint",
            "expected_revision",
            "operation_id",
            "child_specs",
            "actor",
            "terminalized_at",
            "reason",
            "authority",
            "declared_scopes",
        }
    ),
}
