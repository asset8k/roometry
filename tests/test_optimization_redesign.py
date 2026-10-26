"""Four real graph paths and atomic catalog redesign; provider HTTP is mocked."""

from copy import deepcopy
from math import pi
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from roometry.agents.budget import RunBudget
from roometry.optimization.context import InvalidSceneContext, validate_context
from roometry.optimization.graph import OptimizationContext, build_graph
from roometry.optimization.inventory import catalog_options
from roometry.optimization.policy import evaluate, meaningful_gain
from roometry.optimization.schemas import CandidateProposal, OptimizationRun
from roometry.scenes.hashing import scene_hash
from roometry.scenes.validation import validate_geometry
from roometry.vision.schemas import ReconstructionConstraints
from tests import test_vision_api as api
from tests.test_component_geometry import model
from tests.test_geometry import obj
from tests.test_layout_scoring import case
from tests.test_optimization_execution import (
    aesthetic_pair,
    change,
    policy_run,
    proposal,
    provider_action,
    review,
)

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


def source():
    return SimpleNamespace(
        reconstruction_constraints=ReconstructionConstraints(),
        input_options=SimpleNamespace(description=None),
    )


def addition(asset="desk_standard_01", key="new_desk", x=2.0, z=2.0, angle=0.0):
    return {
        "object_id": key,
        "asset_id": asset,
        "position": {"x": x, "z": z},
        "rotation_y_rad": angle,
        "color": None,
    }


def patch(**operations):
    return CandidateProposal.model_validate({**proposal([]), **operations})


def scenario(focus):
    if focus == "openness":
        scene = case(
            [
                obj(
                    "barrier",
                    kind="wardrobe",
                    x=3.0,
                    z=3.0,
                    width=5.4,
                    depth=0.4,
                    height=2.0,
                )
            ]
        )
        plan = patch(changes=[change("barrier", position={"x": 3.0, "z": 0.25})])
    elif focus == "storage":
        scene = case([model("sofa_standard_01", "sofa", x=3.0, z=5.0)])
        plan = patch(
            additions=[addition("wardrobe_standard_01", "new_storage", x=1.0, z=0.4)]
        )
    elif focus == "workspace":
        scene = case([model("sofa_standard_01", "sofa", x=3.0, z=5.0)])
        plan = patch(
            additions=[
                addition(),
                addition("chair_standard_01", "new_chair", x=2.0, z=2.9, angle=pi),
            ]
        )
    else:
        scene = case([model("sofa_standard_01", "sofa", x=3.0, z=5.0)])
        plan = patch(changes=[change("sofa", color="#746A5B")])
    return scene, plan


@pytest.mark.parametrize("focus", ["openness", "storage", "workspace", "aesthetics"])
async def test_each_goal_runs_propose_validate_review_retain_with_real_catalog_and_no_paid_calls(
    harness, focus
):
    app, _, calls, actions = harness
    scene, plan = scenario(focus)
    before = scene.model_dump()
    run = policy_run(scene, focus)

    async def authorize():
        pass

    budget = RunBudget(
        run.limits, run.usage, authorize, lambda: False, run_id=run.run_id
    )
    context = OptimizationContext(
        source(),
        authorize,
        lambda: False,
        budget,
        app.state.optimization_runs.provider,
        None,
    )
    actions.extend(
        [
            provider_action(plan.model_dump(mode="json")),
            review(aesthetics=aesthetic_pair() if focus == "aesthetics" else None),
        ]
    )
    state = await build_graph(InMemorySaver()).ainvoke(
        {"run": run.model_dump(), "proposal": None, "candidate": None},
        {"configurable": {"thread_id": run.run_id}},
        context=context,
    )
    result = OptimizationRun.model_validate(state["run"])
    assert result.status == "completed", result.model_dump_json()
    assert len(result.attempts) == 1 and result.attempts[0].outcome == "accepted"
    assert result.usage.model_calls == len(calls) == 2
    assert validate_geometry(result.result_scene).valid
    assert scene.model_dump() == before and result.source_scene == scene
    assert (
        result.result_scene.room == scene.room
        and result.result_scene.openings == scene.openings
    )
    assert result.result_assessment.scene_hash == scene_hash(result.result_scene)
    if focus in {"storage", "workspace"}:
        assert getattr(result.baseline_assessment, focus).score is None
        assert getattr(result.result_assessment, focus).score >= 6
        assert all(
            o.provenance == "agent_added"
            for o in result.result_scene.objects
            if o.id.startswith("new_")
        )
    elif focus == "openness":
        assert result.attempts[0].after_score >= result.attempts[0].before_score + 0.5
    payload = calls[0]
    assert "catalog_floor_options" in payload["input"][0]["content"][0]["text"]
    assert '"budget"' not in payload["input"][0]["content"][0]["text"]


def test_minor_plant_shuffle_and_recolor_cannot_pass_as_functional_improvements():
    plant = model("plant_palm_01", "plant", x=1.0, z=1.0)
    # Use whichever floor plant variant exists; inventory remains the same.
    scene = case([plant])
    run = policy_run(scene)
    for operations in [
        {"changes": [change("plant", position={"x": 1.05, "z": 1.0})]},
        {"changes": [change("plant", color="#746A5B")]},
    ]:
        result = evaluate(run, patch(**operations), source())
        assert not meaningful_gain(run, result, source())


def test_removing_optional_clutter_is_explicit_and_does_not_delete_a_supported_possession():
    table = model("coffee_table_standard_01", "table", x=2.0, z=2.0)
    vase = model(
        "vase_standard_01",
        "vase",
        placement="surface",
        support="table",
        x=2.0,
        z=2.0,
        y=table.dimensions_m.height,
    )
    scene = case([table, vase])
    before = scene.model_dump()
    with pytest.raises(ValueError, match="explicitly"):
        evaluate(policy_run(scene), patch(removals=["table"]), source())
    result = evaluate(policy_run(scene), patch(removals=["table", "vase"]), source())
    assert not result.issues and set(result.changed) == {"table", "vase"}
    assert not result.scene.objects and scene.model_dump() == before
    scene.objects[1].locked = True
    with pytest.raises(ValueError, match="unlocked"):
        evaluate(policy_run(scene), patch(removals=["table", "vase"]), source())


def test_emptying_primary_room_functions_is_rejected_even_with_a_better_openness_score():
    scene = case([model("sofa_standard_01", "sofa")])
    result = evaluate(policy_run(scene), patch(removals=["sofa"]), source())
    assert any("seating" in issue for issue in result.issues)


def test_replacement_uses_catalog_sizes_and_preserves_supported_items_and_identity():
    desk = model("desk_standard_01", "desk", width=1.6, depth=0.7, x=2.0, z=2.0)
    monitor = model(
        "monitor_standard_01",
        "monitor",
        placement="surface",
        support="desk",
        x=2.0,
        z=2.0,
        y=desk.dimensions_m.height,
    )
    scene = case([desk, monitor])
    result = evaluate(
        policy_run(scene, "aesthetics"),
        patch(replacements=[{"object_id": "desk", "asset_id": "desk_standard_01"}]),
        source(),
    )
    assert validate_geometry(result.scene).valid and not result.issues
    new = result.scene.objects[0]
    assert (
        new.id == "desk"
        and new.provenance == "agent_added"
        and new.measurements_source == "catalog"
    )
    assert new.dimensions_m == model("desk_standard_01").dimensions_m
    child = result.scene.objects[1]
    assert (
        child.id == "monitor"
        and child.support_object_id == "desk"
        and child.support_surface_id is not None
    )
    assert child.position.y == pytest.approx(new.dimensions_m.height)
    scene.objects[1].locked = True
    with pytest.raises(ValueError, match="locked child"):
        evaluate(
            policy_run(scene),
            patch(replacements=[{"object_id": "desk", "asset_id": "desk_standard_01"}]),
            source(),
        )


@pytest.mark.parametrize("operation", ["removals", "replacements", "changes"])
@pytest.mark.parametrize("flag", ["fixed", "locked"])
def test_every_operation_respects_protection(operation, flag):
    scene = case([model("sofa_standard_01", "sofa")])
    setattr(scene.objects[0], flag, True)
    value = (
        ["sofa"]
        if operation == "removals"
        else [{"object_id": "sofa", "asset_id": "sofa_standard_01"}]
        if operation == "replacements"
        else [change("sofa", color="#746A5B")]
    )
    with pytest.raises(ValueError, match="unlocked"):
        evaluate(policy_run(scene), patch(**{operation: value}), source())


@pytest.mark.parametrize(
    "defect", ["unknown_asset", "fixed_asset", "duplicate_id", "overlap", "outside"]
)
def test_invalid_addition_is_not_retained_and_does_not_mutate_original(defect):
    scene = case([model("sofa_standard_01", "sofa")])
    before = scene.model_dump()
    item = addition()
    if defect == "unknown_asset":
        item["asset_id"] = "invented"
    elif defect == "fixed_asset":
        item["asset_id"] = "toilet_standard_01"
    elif defect == "duplicate_id":
        item["object_id"] = "sofa"
    elif defect == "overlap":
        item["position"] = {"x": 2.0, "z": 2.0}
    else:
        item["position"] = {"x": 25.0, "z": 25.0}
    if defect in {"unknown_asset", "fixed_asset", "duplicate_id"}:
        with pytest.raises(ValueError):
            evaluate(policy_run(scene), patch(additions=[item]), source())
    else:
        assert evaluate(policy_run(scene), patch(additions=[item]), source()).issues
    assert scene.model_dump() == before


def test_new_operations_do_not_loosen_manual_snapshot_authorization():
    scene, plan = scenario("storage")
    candidate = evaluate(policy_run(scene, "storage"), plan, source()).scene
    with pytest.raises(InvalidSceneContext):
        validate_context(scene, candidate)
    assert validate_context(candidate, deepcopy(candidate)) == candidate
    assert catalog_options() and all(
        "dimensions_m" in item for item in catalog_options()
    )
    with pytest.raises(ValidationError):
        patch(changes=[change("sofa", color="#746A5B")], removals=["sofa"])


async def test_non_improving_aesthetic_rubric_cannot_override_acceptance(harness):
    app, _, _, actions = harness
    scene, plan = scenario("aesthetics")
    run = policy_run(scene, "aesthetics").model_copy(update={"max_candidates": 1})

    async def authorize():
        pass

    context = OptimizationContext(
        source(),
        authorize,
        lambda: False,
        RunBudget(run.limits, run.usage, authorize, lambda: False),
        app.state.optimization_runs.provider,
        None,
    )
    pair = aesthetic_pair()
    pair["candidate"] = pair["retained"]
    actions.extend(
        [provider_action(plan.model_dump(mode="json")), review(aesthetics=pair)]
    )
    state = await build_graph(InMemorySaver()).ainvoke(
        {"run": run.model_dump()},
        {"configurable": {"thread_id": run.run_id}},
        context=context,
    )
    result = OptimizationRun.model_validate(state["run"])
    assert result.status == "no_improvement" and result.result_scene is None
    assert "rubric" in result.attempts[0].issues[0]


async def test_trivial_candidate_gets_feedback_without_review_or_touching_the_original(
    harness,
):
    app, _, calls, actions = harness
    scene, plan = scenario("openness")
    run = policy_run(scene)

    async def authorize():
        pass

    context = OptimizationContext(
        source(),
        authorize,
        lambda: False,
        RunBudget(run.limits, run.usage, authorize, lambda: False),
        app.state.optimization_runs.provider,
        None,
    )
    actions.extend(
        [
            provider_action(
                patch(changes=[change("barrier", color="#746A5B")]).model_dump(
                    mode="json"
                )
            ),
            provider_action(plan.model_dump(mode="json")),
            review(),
        ]
    )
    state = await build_graph(InMemorySaver()).ainvoke(
        {"run": run.model_dump()},
        {"configurable": {"thread_id": run.run_id}},
        context=context,
    )
    result = OptimizationRun.model_validate(state["run"])
    assert result.status == "completed"
    assert [a.outcome for a in result.attempts] == ["no_gain", "accepted"]
    assert result.usage.model_calls == len(calls) == 3
    assert "meaningful gain" in result.attempts[0].issues[0]
    assert result.result_scene.objects[0].color == scene.objects[0].color


@pytest.mark.parametrize("operation", ["remove_clutter", "replace_bulky_sofa"])
async def test_inventory_changes_are_retained_by_the_graph_not_only_suggested(
    harness, operation
):
    app, _, calls, actions = harness
    if operation == "remove_clutter":
        scene = case(
            [
                obj(
                    "clutter",
                    kind="coffee_table",
                    x=3.0,
                    z=3.0,
                    width=5.4,
                    depth=0.4,
                    height=0.5,
                )
            ]
        )
        plan = patch(removals=["clutter"])
    else:
        scene = case(
            [
                model(
                    "sofa_sectional_left_01", "sofa", width=3.2, depth=1.8, x=2.0, z=3.0
                )
            ]
        )
        scene.room.width_m = 4.0
        plan = patch(
            replacements=[{"object_id": "sofa", "asset_id": "sofa_standard_01"}]
        )
    original = scene.model_dump()
    run = policy_run(scene)

    async def authorize():
        pass

    context = OptimizationContext(
        source(),
        authorize,
        lambda: False,
        RunBudget(run.limits, run.usage, authorize, lambda: False),
        app.state.optimization_runs.provider,
        None,
    )
    actions.extend([provider_action(plan.model_dump(mode="json")), review()])
    state = await build_graph(InMemorySaver()).ainvoke(
        {"run": run.model_dump()},
        {"configurable": {"thread_id": run.run_id}},
        context=context,
    )
    result = OptimizationRun.model_validate(state["run"])
    assert result.status == "completed" and result.result_scene is not None
    assert result.attempts[0].after_score >= result.attempts[0].before_score + 0.5
    assert result.attempts[0].changed_object_ids
    assert result.usage.model_calls == len(calls) == 2
    assert scene.model_dump() == original
    if operation == "remove_clutter":
        assert not result.result_scene.objects
    else:
        assert result.result_scene.objects[0].asset_id == "sofa_standard_01"


def test_compact_same_model_replacement_is_visible_and_keeps_existing_palette():
    from roometry.scenes.schemas import MaterialColors

    desk = model("desk_standard_01", "desk", width=1.6, depth=0.7)
    desk.color = "#746A5B"
    desk.material_colors = MaterialColors(metal="#AB9977")
    scene = case([desk])
    result = evaluate(
        policy_run(scene, "aesthetics"),
        patch(replacements=[{"object_id": "desk", "asset_id": "desk_standard_01"}]),
        source(),
    )
    assert result.changed == ["desk"] and not result.issues
    assert result.scene.objects[0].color == desk.color
    assert result.scene.objects[0].material_colors == desk.material_colors
