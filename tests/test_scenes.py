"""Structural contract tests; these do not certify physical room layouts."""

import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from roometry.scenes.assets import ASSET_REGISTRY, ObjectType
from roometry.scenes.schemas import (
    MAX_NOTE_LENGTH,
    MAX_NOTES,
    MAX_OBJECTS,
    MAX_OPENINGS,
    Opening,
    RoomScene,
    RoomType,
    SceneObject,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "fixtures" / "bedroom_scene.json"


@pytest.fixture
def scene_data() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def find_object(data: dict[str, Any], object_id: str) -> dict[str, Any]:
    return next(obj for obj in data["objects"] if obj["id"] == object_id)


def set_value(data: dict[str, Any], path: tuple, value: Any) -> None:
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def test_fixture_validates_and_round_trips():
    scene = RoomScene.model_validate_json(FIXTURE.read_text())
    restored = RoomScene.model_validate_json(scene.model_dump_json())
    assert restored == scene
    assert len(scene.objects) == 8
    assert {obj.placement for obj in scene.objects} == {
        "floor",
        "surface",
        "wall",
        "ceiling",
    }
    assert {obj.color for obj in scene.objects} >= {
        "#008000",
        "#FFFF00",
        "#808080",
        "#000000",
    }
    assert {obj.id for obj in scene.objects if obj.fixed} == {
        "tv_1",
        "clock_1",
        "chandelier_1",
        "curtain_1",
    }
    assert (
        next(obj for obj in scene.objects if obj.id == "computer_1").support_object_id
        == "desk_1"
    )
    assert (
        next(obj for obj in scene.objects if obj.id == "curtain_1").opening_id
        == "window_1"
    )


def test_documented_examples_validate_and_category_list_matches():
    document = (ROOT / "docs" / "scene-contract.md").read_text()
    blocks = re.findall(r"```json\n(.*?)\n```", document, re.DOTALL)
    RoomScene.model_validate_json(blocks[0])
    SceneObject.model_validate_json(blocks[1])
    table = document.split("| Group | Categories | Allowed placement |", 1)[1].split(
        "`rug` includes", 1
    )[0]
    assert set(re.findall(r"`([a-z_]+)`", table)) == set(get_args(ObjectType))


def test_exported_json_schema_lists_categories_and_approved_assets():
    schema = RoomScene.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["$defs"]["SceneObject"]["properties"]["type"]["enum"]) == set(
        get_args(ObjectType)
    )
    assert set(schema["$defs"]["SceneObject"]["properties"]["asset_id"]["enum"]) == set(
        ASSET_REGISTRY
    )
    json.dumps(schema, allow_nan=False)


@pytest.mark.parametrize("room_type", get_args(RoomType))
def test_room_types_and_square_room_are_supported(scene_data, room_type):
    scene_data["room"].update(room_type=room_type, width_m=4, length_m=4)
    scene = RoomScene.model_validate(scene_data)
    assert scene.room.width_m == scene.room.length_m == 4.0


def test_empty_scene_collections_are_allowed(scene_data):
    scene_data.update(objects=[], openings=[], assumptions=[], warnings=[])
    assert RoomScene.model_validate(scene_data).objects == []


@pytest.mark.parametrize(
    "path",
    [
        ("room", "width_m"),
        ("room", "length_m"),
        ("room", "height_m"),
        ("objects", 0, "dimensions_m", "width"),
        ("objects", 0, "dimensions_m", "depth"),
        ("objects", 0, "dimensions_m", "height"),
        ("openings", 0, "width_m"),
        ("openings", 0, "height_m"),
    ],
)
@pytest.mark.parametrize(
    "value", [0, -1, float("nan"), float("inf"), -float("inf"), True, "2.5"]
)
def test_dimensions_reject_invalid_numbers(scene_data, path, value):
    set_value(scene_data, path, value)
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


@pytest.mark.parametrize(
    "path",
    [
        ("objects", 0, "position", "x"),
        ("objects", 0, "position", "y"),
        ("objects", 0, "position", "z"),
        ("objects", 0, "rotation_y_rad"),
        ("openings", 0, "offset_m"),
        ("openings", 1, "sill_height_m"),
    ],
)
@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), -float("inf"), False, "0"]
)
def test_positions_angles_and_offsets_reject_nonfinite_or_coerced_values(
    scene_data, path, value
):
    set_value(scene_data, path, value)
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


@pytest.mark.parametrize(
    "path,value",
    [
        (("room", "width_m"), 30.1),
        (("room", "length_m"), 30.1),
        (("room", "height_m"), 10.1),
        (("objects", 0, "dimensions_m", "height"), 30.1),
        (("objects", 0, "position", "x"), 100.1),
        (("objects", 0, "position", "z"), -100.1),
        (("objects", 0, "position", "y"), -0.1),
        (("objects", 0, "position", "y"), 100.1),
        (("openings", 0, "offset_m"), -0.1),
        (("openings", 1, "sill_height_m"), -0.1),
    ],
)
def test_resource_and_nonnegative_limits(scene_data, path, value):
    set_value(scene_data, path, value)
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


@pytest.mark.parametrize("value", [True, False, 1.0, "1", 0, 2, None])
def test_schema_version_requires_exact_integer_one(scene_data, value):
    scene_data["schema_version"] = value
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("room",),
        ("objects", 0),
        ("objects", 0, "position"),
        ("objects", 0, "dimensions_m"),
        ("openings", 0),
    ],
)
def test_unknown_fields_rejected_at_every_level(scene_data, path):
    target = scene_data
    for key in path:
        target = target[key]
    target["unexpected"] = "not part of the contract"
    with pytest.raises(ValidationError) as error:
        RoomScene.model_validate(scene_data)
    assert any(item["type"] == "extra_forbidden" for item in error.value.errors())


@pytest.mark.parametrize("field", ["objects", "openings", "assumptions", "warnings"])
def test_collections_must_be_explicit_lists(scene_data, field):
    scene_data[field] = ()
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)
    del scene_data[field]
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


@pytest.mark.parametrize("field", ["support_object_id", "wall", "opening_id"])
def test_nullable_references_still_require_explicit_fields(scene_data, field):
    del scene_data["objects"][0][field]
    with pytest.raises(ValidationError) as error:
        RoomScene.model_validate(scene_data)
    assert ("objects", 0, field) in [item["loc"] for item in error.value.errors()]


@pytest.mark.parametrize("field", ["fixed", "locked"])
@pytest.mark.parametrize("value", [0, 1, "true", "false", None])
def test_flags_require_real_booleans(scene_data, field, value):
    scene_data["objects"][0][field] = value
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


@pytest.mark.parametrize(
    "value", ["green", "#FFF", "#000000FF", "#GGGGGG", "#000000\n", 123]
)
def test_invalid_colors_rejected(scene_data, value):
    scene_data["objects"][0]["color"] = value
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


def test_colors_are_normalized_without_changing_assets(scene_data):
    scene_data["objects"][0]["color"] = "#aBcDeF"
    obj = RoomScene.model_validate(scene_data).objects[0]
    assert obj.color == "#ABCDEF"
    assert obj.asset_id == "bed_standard_01"


@pytest.mark.parametrize(
    "angle,expected",
    [
        (-math.pi / 2, 3 * math.pi / 2),
        (math.tau, 0),
        (3 * math.pi, math.pi),
        (-math.tau, 0),
        (-1e-100, 0),
        (0, 0),
    ],
)
def test_angles_normalized_to_one_turn(scene_data, angle, expected):
    scene_data["objects"][0]["rotation_y_rad"] = angle
    normalized = RoomScene.model_validate(scene_data).objects[0].rotation_y_rad
    assert 0 <= normalized < math.tau
    assert normalized == pytest.approx(expected)


@pytest.mark.parametrize(
    "value", ["", " ", "bed 1", "../bed", "https://asset.test/bed", "a" * 65]
)
def test_invalid_instance_identifiers_rejected(scene_data, value):
    scene_data["objects"][0]["id"] = value
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("type", "spaceship"),
        ("asset_id", "bed_unknown_99"),
        ("asset_id", "desk_standard_01"),
        ("asset_id", "https://asset.test/bed.glb"),
        ("placement", "floating"),
        ("provenance", "imagined"),
        ("measurements_source", "exact_from_photo"),
    ],
)
def test_unknown_categories_assets_and_sources_rejected(scene_data, field, value):
    scene_data["objects"][0][field] = value
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("placement", "ceiling"),
        ("support_object_id", "desk_1"),
        ("wall", "north"),
        ("opening_id", "window_1"),
    ],
)
def test_floor_object_rejects_wrong_placement_or_references(scene_data, field, value):
    scene_data["objects"][0][field] = value
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


def test_floor_object_cannot_float(scene_data):
    scene_data["objects"][0]["position"]["y"] = 0.01
    with pytest.raises(ValidationError, match="Floor objects require"):
        RoomScene.model_validate(scene_data)


@pytest.mark.parametrize("duplicate_kind", ["object", "opening", "cross_collection"])
def test_ids_are_unique_across_whole_scene(scene_data, duplicate_kind):
    if duplicate_kind == "object":
        scene_data["objects"].append(deepcopy(scene_data["objects"][0]))
    elif duplicate_kind == "opening":
        scene_data["openings"].append(deepcopy(scene_data["openings"][0]))
    else:
        scene_data["objects"][0]["id"] = "door_1"
    with pytest.raises(ValidationError, match="Duplicate scene ID"):
        RoomScene.model_validate(scene_data)


@pytest.mark.parametrize(
    "support_id", [None, "computer_1", "missing_desk", "door_1", "bed_1"]
)
def test_surface_support_must_exist_and_be_eligible(scene_data, support_id):
    find_object(scene_data, "computer_1")["support_object_id"] = support_id
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


def test_references_do_not_depend_on_list_order(scene_data):
    scene_data["objects"].reverse()
    assert len(RoomScene.model_validate(scene_data).objects) == 8


def test_nested_surface_support_and_cycles_rejected(scene_data):
    parent = deepcopy(find_object(scene_data, "computer_1"))
    parent.update(
        id="box_1",
        type="storage_box",
        asset_id="storage_box_standard_01",
        support_object_id="desk_1",
    )
    scene_data["objects"].append(parent)
    computer = find_object(scene_data, "computer_1")
    computer["support_object_id"] = "box_1"
    with pytest.raises(ValidationError, match="nested surface support"):
        RoomScene.model_validate(scene_data)
    parent["support_object_id"] = "computer_1"
    with pytest.raises(ValidationError, match="nested surface support"):
        RoomScene.model_validate(scene_data)


def test_wall_shelf_can_support_an_object(scene_data):
    desk = find_object(scene_data, "desk_1")
    desk.update(
        type="wall_shelf",
        asset_id="wall_shelf_standard_01",
        placement="wall",
        wall="north",
        fixed=True,
    )
    desk["position"] = {"x": 0.65, "y": 0.5, "z": 0.3}
    desk["dimensions_m"]["height"] = 0.25
    find_object(scene_data, "computer_1")["position"] = {"x": 0.65, "y": 0.75, "z": 0.3}
    assert RoomScene.model_validate(scene_data)


@pytest.mark.parametrize(
    "object_type,asset_id,placement",
    [
        ("game_console", "game_console_standard_01", "surface"),
        ("projector", "projector_standard_01", "ceiling"),
        ("refrigerator", "refrigerator_standard_01", "floor"),
        ("toilet", "toilet_standard_01", "wall"),
        ("washing_machine", "washing_machine_stack_01", "floor"),
        ("water_heater", "water_heater_standard_01", "wall"),
        ("kitchen_counter", "kitchen_counter_sink_01", "floor"),
        ("bed", "bed_crib_01", "floor"),
        ("household_accessory", "household_accessory_toilet_brush_01", "floor"),
        ("household_accessory", "household_accessory_toilet_paper_holder_01", "wall"),
    ],
)
def test_household_categories_and_reusable_variants(
    scene_data, object_type, asset_id, placement
):
    obj = deepcopy(find_object(scene_data, "computer_1"))
    obj.update(
        type=object_type,
        asset_id=asset_id,
        placement=placement,
        support_object_id="desk_1" if placement == "surface" else None,
        wall="north" if placement == "wall" else None,
    )
    if placement == "floor":
        obj["position"]["y"] = 0
    assert SceneObject.model_validate(obj).type == object_type


def test_accessory_variant_does_not_allow_arbitrary_placement(scene_data):
    obj = deepcopy(find_object(scene_data, "computer_1"))
    obj.update(
        type="household_accessory", asset_id="household_accessory_toilet_brush_01"
    )
    with pytest.raises(ValidationError, match="does not allow surface"):
        SceneObject.model_validate(obj)


@pytest.mark.parametrize(
    "asset_id,object_type",
    [
        ("shelf_coat_rack_01", "shelf"),
        ("kitchen_counter_sink_01", "kitchen_counter"),
        ("kitchen_counter_hob_01", "kitchen_counter"),
    ],
)
def test_nonflat_variants_cannot_support_surface_objects(
    scene_data, asset_id, object_type
):
    find_object(scene_data, "desk_1").update(type=object_type, asset_id=asset_id)
    with pytest.raises(ValidationError, match="approved surface"):
        RoomScene.model_validate(scene_data)


def test_wall_requires_host_and_ceiling_forbids_host(scene_data):
    tv = find_object(scene_data, "tv_1")
    tv["wall"] = None
    with pytest.raises(ValidationError, match="Wall objects require"):
        RoomScene.model_validate(scene_data)
    tv["wall"] = "south"
    find_object(scene_data, "chandelier_1")["wall"] = "north"
    with pytest.raises(ValidationError, match="Only wall objects"):
        RoomScene.model_validate(scene_data)


@pytest.mark.parametrize(
    "opening_id,wall",
    [
        (None, "east"),
        ("missing_window", "east"),
        ("door_1", "north"),
        ("window_1", "west"),
    ],
)
def test_window_covering_requires_matching_window(scene_data, opening_id, wall):
    find_object(scene_data, "curtain_1").update(opening_id=opening_id, wall=wall)
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("swing", "inward"),
        ("hinge", "unknown"),
        ("provenance", "agent_added"),
        ("wall", "diagonal"),
        ("measurements_source", "catalog"),
    ],
)
def test_window_fields_are_consistent(scene_data, field, value):
    scene_data["openings"][1][field] = value
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


def test_doors_support_unknown_and_nonswinging_cases(scene_data):
    door = scene_data["openings"][0]
    door.update(swing="unknown", hinge="unknown")
    assert Opening.model_validate(door).swing == "unknown"
    door.update(swing="none", hinge=None)
    assert Opening.model_validate(door).hinge is None


@pytest.mark.parametrize("field,value", [("sill_height_m", 0.1), ("hinge", None)])
def test_swinging_door_fields_are_consistent(scene_data, field, value):
    scene_data["openings"][0][field] = value
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


def test_nonswinging_door_cannot_have_hinge(scene_data):
    scene_data["openings"][0]["swing"] = "none"
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene_data)


@pytest.mark.parametrize(
    "field,limit", [("objects", MAX_OBJECTS), ("openings", MAX_OPENINGS)]
)
def test_scene_item_count_caps(scene_data, field, limit):
    items = []
    for index in range(limit + 1):
        item = deepcopy(scene_data[field][0])
        item["id"] = f"item_{index}"
        items.append(item)
    scene_data[field] = items
    with pytest.raises(ValidationError) as error:
        RoomScene.model_validate(scene_data)
    assert any(item["type"] == "too_long" for item in error.value.errors())


@pytest.mark.parametrize("field", ["assumptions", "warnings"])
@pytest.mark.parametrize(
    "value",
    [[], [""], ["   "], ["x" * (MAX_NOTE_LENGTH + 1)], ["note"] * (MAX_NOTES + 1)],
)
def test_note_validation_and_limits(scene_data, field, value):
    scene_data[field] = value
    if value == []:
        assert RoomScene.model_validate(scene_data)
    else:
        with pytest.raises(ValidationError):
            RoomScene.model_validate(scene_data)


def test_revalidation_catches_tampered_nested_model(scene_data):
    scene = RoomScene.model_validate(scene_data)
    scene.objects[0].color = "not a color"
    with pytest.raises(ValidationError):
        RoomScene.model_validate(scene)


def test_schema_valid_does_not_mean_geometry_valid(scene_data):
    # A syntactically valid position beyond this room must pass to the later
    # geometry validator, rather than being mistaken for an accepted layout.
    scene_data["objects"][0]["position"]["x"] = 10
    assert RoomScene.model_validate(scene_data).objects[0].position.x == 10
