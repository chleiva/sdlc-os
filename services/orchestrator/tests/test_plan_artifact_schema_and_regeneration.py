"""Section 9.5's structured plan artifact: schema validity, generation
from a `PlanOutput`, and "regenerated, not hand-edited" as an actually
enforced property (there is no update/patch function to call instead)."""
from __future__ import annotations

from jsonschema import Draft202012Validator

from orchestrator.checkpoints import DEFAULT_BUDGETS
from orchestrator.model_backend import SubTask
from orchestrator.plan_artifact import (
    SCHEMA,
    PlanArtifactStore,
    diff_touches_out_of_scope,
    generate_plan_artifact,
    hash_human_plan,
)

from ._factories import make_plan_output


def test_schema_itself_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(SCHEMA)


def test_generate_plan_artifact_produces_a_schema_valid_document():
    plan = make_plan_output()
    artifact = generate_plan_artifact(
        run_id="run-1", plan_version=1, plan_output=plan, budget=DEFAULT_BUDGETS["S"], human_plan_text="the plan text"
    )
    Draft202012Validator(SCHEMA).validate(artifact)
    assert artifact["declared_scope"]["in_scope"] == ["src/foo.py", "tests/test_foo.py"]
    assert artifact["declared_scope"]["out_of_scope"] == ["src/bar.py"]
    assert artifact["acceptance_criteria_map"][0]["criterion_id"] == "AC1"
    assert artifact["subtask_graph"]["subtasks"][0]["mode"] == "sequential"
    assert artifact["risk"]["story_size"] == "S"
    assert artifact["source_plan_hash"] == hash_human_plan("the plan text")


def test_parallel_subtasks_without_a_fixed_interface_contract_are_demoted_not_rejected():
    """Section 8.1: "contracts before parallel writes" -- fixed before
    implementation starts. Real live-run bug this guards against: this
    used to raise `PlanArtifactValidationError` right here and abort the
    entire run before a human ever saw the plan-approval gate (confirmed
    on a real live run: the model set `parallel_group` on two subtasks
    and populated `interface_contract` on neither). Sequential execution
    of the same subtasks is always safe, so generation now repairs the
    artifact (demotes the whole group to sequential) instead of treating
    an authoring gap as fatal -- and calls `on_parallel_group_demoted`
    with the demoted ids so a caller can still surface it."""
    plan = make_plan_output(
        subtasks=[
            SubTask(task_id="t1", description="a", parallel_group="g1", depends_on=()),
            SubTask(task_id="t2", description="b", parallel_group="g1", depends_on=()),
        ]
    )
    demoted_calls = []
    artifact = generate_plan_artifact(
        run_id="run-2", plan_version=1, plan_output=plan, budget=DEFAULT_BUDGETS["S"], human_plan_text="t",
        on_parallel_group_demoted=demoted_calls.append,
    )
    assert demoted_calls == [["t1", "t2"]]
    for st in artifact["subtask_graph"]["subtasks"]:
        assert st["parallel_group"] is None
        assert st["mode"] == "sequential"
    Draft202012Validator(SCHEMA).validate(artifact)  # still a fully schema-valid artifact after repair


def test_a_group_with_only_some_members_contracted_is_fully_demoted():
    """Partial governance isn't real governance: one contracted member
    and one uncontracted member in the same group must demote BOTH, not
    just the uncontracted one -- a lone "parallel" subtask left behind
    would be meaningless."""
    plan = make_plan_output(
        subtasks=[
            SubTask(task_id="t1", description="a", parallel_group="g1", depends_on=(), interface_contract="POST /a -> {ok: bool}"),
            SubTask(task_id="t2", description="b", parallel_group="g1", depends_on=()),
        ]
    )
    artifact = generate_plan_artifact(run_id="run-2b", plan_version=1, plan_output=plan, budget=DEFAULT_BUDGETS["S"], human_plan_text="t")
    assert {st["task_id"]: st["parallel_group"] for st in artifact["subtask_graph"]["subtasks"]} == {"t1": None, "t2": None}


def test_parallel_subtasks_with_fixed_contracts_are_accepted():
    plan = make_plan_output(
        subtasks=[
            SubTask(task_id="t1", description="a", parallel_group="g1", depends_on=(), interface_contract="POST /a -> {ok: bool}"),
            SubTask(task_id="t2", description="b", parallel_group="g1", depends_on=(), interface_contract="POST /b -> {ok: bool}"),
        ]
    )
    artifact = generate_plan_artifact(run_id="run-3", plan_version=1, plan_output=plan, budget=DEFAULT_BUDGETS["S"], human_plan_text="t")
    assert all(st["mode"] == "parallel" for st in artifact["subtask_graph"]["subtasks"])


def test_regeneration_is_a_new_artifact_not_a_patch(tmp_path):
    store = PlanArtifactStore(tmp_path / "artifacts")
    plan_v1 = make_plan_output(scope_in=("src/a.py",))
    artifact_v1 = generate_plan_artifact(run_id="run-4", plan_version=1, plan_output=plan_v1, budget=DEFAULT_BUDGETS["S"], human_plan_text="v1 text")
    store.save(artifact_v1)

    plan_v2 = make_plan_output(scope_in=("src/a.py", "src/b.py"))
    artifact_v2 = generate_plan_artifact(run_id="run-4", plan_version=2, plan_output=plan_v2, budget=DEFAULT_BUDGETS["S"], human_plan_text="v2 text (human changed the plan)")
    store.save(artifact_v2)

    # Both versions are retained (a re-plan is a visible, logged change).
    assert store.load_version("run-4", 1)["declared_scope"]["in_scope"] == ["src/a.py"]
    assert store.load_version("run-4", 2)["declared_scope"]["in_scope"] == ["src/a.py", "src/b.py"]
    assert store.load_latest("run-4")["plan_version"] == 2
    assert store.load_version("run-4", 1)["source_plan_hash"] != store.load_version("run-4", 2)["source_plan_hash"]

    # There is no mutate-in-place API on the store or the generator --
    # the only way this module lets a caller change a declared scope is
    # to call generate_plan_artifact again with a new PlanOutput.
    assert not hasattr(store, "update")
    assert not hasattr(store, "patch")


def test_diff_touches_out_of_scope_is_a_pure_set_difference_against_the_artifact():
    plan = make_plan_output(scope_in=("src/foo.py", "src/bar.py"))
    artifact = generate_plan_artifact(run_id="run-6", plan_version=1, plan_output=plan, budget=DEFAULT_BUDGETS["S"], human_plan_text="t")
    assert diff_touches_out_of_scope(artifact, ["src/foo.py", "src/bar.py"]) == []
    assert diff_touches_out_of_scope(artifact, ["src/foo.py", "src/unknown.py"]) == ["src/unknown.py"]


def test_an_invalid_hand_built_artifact_fails_schema_validation():
    """A caller cannot bypass generation and hand-craft a shape the
    schema doesn't allow (e.g. an extra unknown field, or a missing
    required one) without it being caught immediately."""
    plan = make_plan_output()
    artifact = generate_plan_artifact(run_id="run-5", plan_version=1, plan_output=plan, budget=DEFAULT_BUDGETS["S"], human_plan_text="t")
    tampered = dict(artifact)
    del tampered["declared_scope"]
    errors = list(Draft202012Validator(SCHEMA).iter_errors(tampered))
    assert errors, "removing a required field must fail schema validation"

    tampered_2 = dict(artifact)
    tampered_2["unexpected_hand_added_field"] = "sneaky"
    errors_2 = list(Draft202012Validator(SCHEMA).iter_errors(tampered_2))
    assert errors_2, "additionalProperties: false must reject an unknown hand-added field"
