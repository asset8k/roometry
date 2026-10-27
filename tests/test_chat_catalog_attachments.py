"""Chat may add a real TV; catalog mounts and existing protection stay authoritative."""

import json
from copy import deepcopy
from math import pi

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

from roometry.agents.budget import RunBudget
from roometry.optimization.graph import OptimizationContext, build_graph
from roometry.optimization.inventory import catalog_options, scene_supports
from roometry.optimization.policy import evaluate
from roometry.optimization.schemas import CatalogAddition, OptimizationRun
from roometry.scenes.catalog import dimensions_for
from roometry.scenes.geometry import WALL_ROTATIONS_RAD
from roometry.scenes.validation import validate_geometry
from tests import test_vision_api as api
from tests.test_component_geometry import model
from tests.test_geometry import opening
from tests.test_layout_scoring import case
from tests.test_optimization_execution import (
    change,
    policy_run,
    provider_action,
    review,
)
from tests.test_optimization_redesign import addition, patch, source
from tests.test_optimization_runs import reconstructed
from tests.test_scene_refinement import request

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
pytestmark = pytest.mark.anyio


def tv(placement="surface", **fields):
    return {
        **addition("tv_standard_01", "new_tv"),
        "placement": placement,
        **(
            {"support_object_id": "console", "support_surface_id": "top"}
            if placement == "surface"
            else {"wall": "north", "elevation_m": 1.2}
        ),
        **fields,
    }


def test_catalog_exposes_tv_mount_sizes_and_real_support_choices_without_meshes():
    options = catalog_options()
    item = next(o for o in options if o["type"] == "tv")
    assert item["placements"] == ["surface", "wall"]
    assert item["placement_dimensions_m"]["wall"] == (1.2, 0.68, 0.06)
    assert "parts" not in json.dumps(options)
    assert len(json.dumps(options)) < 30_000
    assert not any(o["asset_id"] == "toilet_standard_01" for o in options)
    console = model("tv_stand_standard_01", "console", angle=pi / 2)
    areas = scene_supports(case([console]))["console"]
    assert {a["surface_id"] for a in areas} == {"top", "center_shelf"}
    assert next(a for a in areas if a["surface_id"] == "top")["height_m"] == 0.55


@pytest.mark.parametrize("wall", ["north", "south", "east", "west"])
def test_wall_tv_uses_mount_dimensions_and_inward_orientation_on_every_wall(wall):
    scene = case()
    original = deepcopy(scene)
    result = evaluate(
        policy_run(scene), patch(additions=[tv("wall", wall=wall)]), source()
    )
    assert not result.issues, result.issues
    obj = result.scene.objects[0]
    assert obj.rotation_y_rad == WALL_ROTATIONS_RAD[wall]
    assert (
        obj.dimensions_m.width,
        obj.dimensions_m.height,
        obj.dimensions_m.depth,
    ) == dimensions_for(obj.asset_id, "wall")
    assert obj.position.y == 1.2 and obj.provenance == "agent_added"
    assert obj.support_object_id is None and validate_geometry(result.scene).valid
    assert scene == original


@pytest.mark.parametrize("angle", [0.0, pi / 2, pi / 4])
@pytest.mark.parametrize("protected", ["fixed", "locked"])
def test_tv_can_use_a_rotated_protected_console_without_changing_it(angle, protected):
    console = model("tv_stand_standard_01", "console", angle=angle)
    setattr(console, protected, True)
    scene = case([console])
    result = evaluate(
        policy_run(scene), patch(additions=[tv(rotation_y_rad=angle)]), source()
    )
    assert not result.issues, result.issues
    new = result.scene.objects[1]
    assert new.position.y == 0.55 and new.support_surface_id == "top"
    assert result.scene.objects[0] == console and scene.objects == [console]


def test_new_support_and_tv_are_resolved_even_when_child_is_listed_first():
    scene = case()
    plan = patch(additions=[tv(), addition("tv_stand_standard_01", "console")])
    result = evaluate(policy_run(scene), plan, source())
    assert not result.issues, result.issues
    assert validate_geometry(result.scene).valid
    assert (
        next(o for o in result.scene.objects if o.id == "new_tv").support_object_id
        == "console"
    )


def test_surface_height_includes_a_wall_support_elevation_and_selected_lower_shelf():
    shelf = model("wall_shelf_standard_01", "shelf", placement="wall", z=0.125, y=1.0)
    shelf.fixed = True
    scene = case([shelf])
    item = {
        **addition("book_stack_standard_01", "books", z=0.125),
        "placement": "surface",
        "support_object_id": "shelf",
        "support_surface_id": "shelf_0",
    }
    result = evaluate(policy_run(scene), patch(additions=[item]), source())
    assert not result.issues, result.issues
    assert result.scene.objects[1].position.y == pytest.approx(1.02)
    assert result.scene.objects[0] == shelf


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "removed",
        "unknown_surface",
        "ambiguous_surface",
        "nested",
        "wrong_placement",
    ],
)
def test_invalid_supports_or_mounts_do_not_mutate_the_scene(defect):
    console = model("tv_stand_standard_01", "console")
    scene = case([console])
    item, removals = tv(), []
    if defect == "missing":
        item["support_object_id"] = "missing"
    elif defect == "removed":
        removals = ["console"]
    elif defect == "unknown_surface":
        item["support_surface_id"] = "invented_top"
    elif defect == "ambiguous_surface":
        item["support_surface_id"] = None
    elif defect == "wrong_placement":
        item = addition("tv_standard_01", "new_tv")
    else:
        child = model(
            "book_stack_standard_01",
            "books",
            placement="surface",
            support="console",
            surface="top",
            y=0.55,
        )
        scene.objects.append(child)
        item["support_object_id"] = "books"
    original = deepcopy(scene)
    with pytest.raises(ValueError):
        evaluate(
            policy_run(scene), patch(additions=[item], removals=removals), source()
        )
    assert scene == original


@pytest.mark.parametrize(
    "defect",
    [
        "too_small",
        "outside_support",
        "solid_overlap",
        "opening_overlap",
        "outside_room",
    ],
)
def test_geometry_still_rejects_bad_attachment_candidates(defect):
    console = model("tv_stand_standard_01", "console")
    item, objects = tv(), [console]
    if defect == "too_small":
        console.dimensions_m.width = 0.3
    elif defect == "outside_support":
        item["position"] = {"x": 4.0, "z": 2.0}
    elif defect == "solid_overlap":
        objects.append(
            model(
                "tv_standard_01",
                "old_tv",
                placement="surface",
                support="console",
                surface="top",
                y=0.55,
            )
        )
    elif defect == "opening_overlap":
        item, objects = tv("wall"), []
    else:
        item, objects = tv("wall", elevation_m=2.9), []
    scene = case(objects)
    if defect == "opening_overlap":
        scene.openings = [opening(offset=1.5)]
    original = deepcopy(scene)
    result = evaluate(policy_run(scene), patch(additions=[item]), source())
    assert result.issues and scene == original


@pytest.mark.parametrize(
    "fields",
    [
        {"placement": "wall"},
        {"placement": "surface"},
        {"placement": "floor", "wall": "north"},
        {"placement": "surface", "support_object_id": "new_tv"},
        {"placement": "ceiling"},
    ],
)
def test_attachment_contract_rejects_missing_or_conflicting_metadata(fields):
    with pytest.raises(ValidationError):
        CatalogAddition.model_validate(
            {**addition("tv_standard_01", "new_tv"), **fields}
        )


async def test_combined_tv_shelf_removal_and_bed_color_request_runs_through_graph(
    harness,
):
    app, _, calls, actions = harness
    wardrobe = model("wardrobe_standard_01", "wardrobe", x=5.0, z=0.4, width=1.8)
    wardrobe.color = "#B7CCD4"
    console = model("tv_stand_standard_01", "console", x=3.0, z=0.3)
    console.locked = True
    scene = case(
        [
            model("bed_futon_01", "bed", x=1.0, z=4.0),
            wardrobe,
            console,
            model("shelf_metal_01", "middle_shelf", x=3.0, z=3.0),
        ]
    )
    assert validate_geometry(scene).valid
    original = deepcopy(scene)
    plan = patch(
        additions=[tv(position={"x": 3.0, "z": 0.3})],
        removals=["middle_shelf"],
        changes=[change("bed", color=wardrobe.color)],
        summary="Added a TV, removed the middle shelf and matched the bed to the wardrobe.",
    )
    run = policy_run(scene, "aesthetics").model_copy(
        update={
            "instruction": "Add a TV, remove the middle shelf and match the bed color to the wardrobe."
        }
    )

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
    result = await build_graph(InMemorySaver()).ainvoke(
        {"run": run.model_dump()},
        {"configurable": {"thread_id": run.run_id}},
        context=context,
    )
    retained = OptimizationRun.model_validate(result["run"])
    assert retained.status == "completed", retained.model_dump_json()
    mapped = {o.id: o for o in retained.result_scene.objects}
    assert (
        mapped["new_tv"].type == "tv"
        and mapped["new_tv"].support_object_id == "console"
    )
    assert "middle_shelf" not in mapped and mapped["bed"].color == wardrobe.color
    assert mapped["console"] == console and mapped["wardrobe"] == wardrobe
    assert scene == original and validate_geometry(retained.result_scene).valid
    assert len(calls) == retained.usage.model_calls == 2
    data = json.loads(calls[0]["input"][0]["content"][0]["text"])
    assert any(o["type"] == "tv" for o in data["catalog_addition_options"])
    assert data["scene_support_surfaces"]["console"]


async def test_chat_http_adds_wall_tv_and_followup_preserves_it_without_reanalysis(
    harness,
):
    _, client, calls, actions = harness
    _, _, original = await reconstructed(harness)
    base = original.working_scene
    url = f"/api/agent-runs/{original.run_id}/optimizations"
    prepared = await client.post(
        url, json=request(base, text="Add a wall-mounted TV on the south wall.")
    )
    assert prepared.status_code == 200, prepared.text
    run_id = prepared.json()["run_id"]
    plan = patch(
        additions=[
            tv("wall", wall="south", elevation_m=1.4, position={"x": 2.5, "z": 2.0})
        ]
    )
    actions.extend([provider_action(plan.model_dump(mode="json")), review()])
    before = len(calls)
    response = await client.post(f"{url}/{run_id}/execute", json={})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed", result
    assert (
        next(o for o in result["result_scene"]["objects"] if o["id"] == "new_tv")[
            "placement"
        ]
        == "wall"
    )
    assert len(calls) == before + 2
    assert (await client.post(f"{url}/{run_id}/execute", json={})).json() == result
    retained_scene = OptimizationRun.model_validate_json(response.text).result_scene
    followup = await client.post(
        url, json=request(retained_scene, run_id, "Use a darker TV frame.")
    )
    assert followup.status_code == 200, followup.text
    assert followup.json()["source_scene"] == retained_scene.model_dump(mode="json")
    assert len(calls) == before + 2 and original.working_scene == base
    actions.extend(
        [
            provider_action(
                patch(changes=[change("new_tv", color="#303030")]).model_dump(
                    mode="json"
                )
            ),
            review(),
        ]
    )
    revised = await client.post(f"{url}/{followup.json()['run_id']}/execute", json={})
    assert revised.status_code == 200 and revised.json()["status"] == "completed", (
        revised.text
    )
    final_tv = next(
        o for o in revised.json()["result_scene"]["objects"] if o["id"] == "new_tv"
    )
    first_tv = next(o for o in result["result_scene"]["objects"] if o["id"] == "new_tv")
    assert final_tv == {**first_tv, "color": "#303030"}
    assert len(calls) == before + 4
