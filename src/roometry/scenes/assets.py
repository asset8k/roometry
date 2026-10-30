"""Approved scene asset metadata. No meshes, prices, or retailer offers live here."""

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Literal, get_args

Placement = Literal["floor", "surface", "wall", "ceiling"]

ObjectType = Literal[
    "bed",
    "sofa",
    "armchair",
    "chair",
    "stool",
    "bench",
    "ottoman",
    "desk",
    "table",
    "coffee_table",
    "side_table",
    "nightstand",
    "wardrobe",
    "dresser",
    "shelf",
    "tv_stand",
    "shoe_rack",
    "sideboard",
    "wall_shelf",
    "storage_box",
    "basket",
    "bin",
    "computer",
    "laptop",
    "monitor",
    "printer",
    "game_console",
    "speaker",
    "tv",
    "projector",
    "rug",
    "picture",
    "clock",
    "window_covering",
    "souvenir",
    "vase",
    "book_stack",
    "household_accessory",
    "mirror",
    "plant",
    "floor_lamp",
    "table_lamp",
    "chandelier",
    "ceiling_light",
    "ceiling_fan",
    "radiator",
    "air_conditioner",
    "fan",
    "kitchen_cabinet",
    "kitchen_counter",
    "wall_cabinet",
    "extractor_hood",
    "refrigerator",
    "cooker",
    "dishwasher",
    "washing_machine",
    "dryer",
    "microwave",
    "kettle",
    "toaster",
    "sink",
    "toilet",
    "bathtub",
    "shower",
    "vanity",
    "towel_rack",
    "water_heater",
    "drying_rack",
    "countertop",
    "faucet",
    "shower_fixture",
    "wall_panel",
    "ceiling_detail",
]

_SURFACE_TYPES: set[ObjectType] = {
    "computer",
    "laptop",
    "monitor",
    "printer",
    "game_console",
    "souvenir",
    "vase",
    "book_stack",
    "household_accessory",
    "table_lamp",
    "microwave",
    "kettle",
    "toaster",
}
_WALL_TYPES: set[ObjectType] = {
    "wall_shelf",
    "clock",
    "window_covering",
    "radiator",
    "air_conditioner",
    "wall_cabinet",
    "extractor_hood",
    "towel_rack",
    "water_heater",
}
_CEILING_TYPES: set[ObjectType] = {"chandelier", "ceiling_light", "ceiling_fan"}
_MULTIPLE_PLACEMENTS: dict[ObjectType, frozenset[Placement]] = {
    "storage_box": frozenset({"floor", "surface"}),
    "basket": frozenset({"floor", "surface"}),
    "speaker": frozenset({"floor", "surface"}),
    "tv": frozenset({"surface", "wall"}),
    "picture": frozenset({"surface", "wall"}),
    "projector": frozenset({"surface", "ceiling"}),
    "mirror": frozenset({"floor", "wall"}),
    "plant": frozenset({"floor", "surface"}),
    "fan": frozenset({"floor", "surface"}),
    "sink": frozenset({"floor", "wall"}),
    "toilet": frozenset({"floor", "wall"}),
    "vanity": frozenset({"floor", "wall"}),
}
_SUPPORT_TYPES: set[ObjectType] = {
    "desk",
    "table",
    "coffee_table",
    "side_table",
    "nightstand",
    "dresser",
    "tv_stand",
    "sideboard",
    "shelf",
    "wall_shelf",
    "kitchen_counter",
}
_INSTALLED_APPLIANCE_AND_PLUMBING_TYPES: set[ObjectType] = {
    "kitchen_cabinet",
    "kitchen_counter",
    "refrigerator",
    "cooker",
    "dishwasher",
    "washing_machine",
    "dryer",
    "sink",
    "toilet",
    "bathtub",
    "shower",
    "vanity",
}
_FIXTURE_TYPES: set[ObjectType] = (
    _WALL_TYPES | _CEILING_TYPES | _INSTALLED_APPLIANCE_AND_PLUMBING_TYPES
)


@dataclass(frozen=True)
class AssetDefinition:
    """Structural capabilities of an approved, potentially unbuilt visual asset."""

    object_type: ObjectType
    placements: frozenset[Placement]
    supplies_surface: bool = False
    fixed_by_default: bool = False
    # Drapes can hide an unobserved opening or dress a plain wall. Blinds still
    # require an observed opening; this flag never exempts any collision check.
    allows_unlinked_covering: bool = False


def _standard_asset(object_type: ObjectType) -> AssetDefinition:
    placements: frozenset[Placement]
    if object_type in _MULTIPLE_PLACEMENTS:
        placements = _MULTIPLE_PLACEMENTS[object_type]
    elif object_type in _SURFACE_TYPES:
        placements = frozenset({"surface"})
    elif object_type in _WALL_TYPES:
        placements = frozenset({"wall"})
    elif object_type in _CEILING_TYPES:
        placements = frozenset({"ceiling"})
    else:
        placements = frozenset({"floor"})
    return AssetDefinition(
        object_type=object_type,
        placements=placements,
        supplies_surface=object_type in _SUPPORT_TYPES,
        fixed_by_default=object_type in _FIXTURE_TYPES,
    )


# Stable identifiers are reserved for simple procedural renderer assets. Their
# approval here does not mean the renderer or an MCP purchase entry exists yet.
_assets: dict[str, AssetDefinition] = {
    f"{object_type}_standard_01": _standard_asset(object_type)
    for object_type in get_args(ObjectType)
}
_assets.update(
    {
        "bed_futon_01": AssetDefinition("bed", frozenset({"floor"})),
        "sofa_sleeper_01": AssetDefinition("sofa", frozenset({"floor"})),
        "chair_office_01": AssetDefinition("chair", frozenset({"floor"})),
        "shelf_metal_01": _standard_asset("shelf"),
        "desk_shelved_01": _standard_asset("desk"),
        "ceiling_light_track_01": _standard_asset("ceiling_light"),
        "bed_crib_01": _standard_asset("bed"),
        "sofa_sectional_left_01": _standard_asset("sofa"),
        "sofa_sectional_right_01": _standard_asset("sofa"),
        "picture_square_01": _standard_asset("picture"),
        "picture_rectangular_01": _standard_asset("picture"),
        "picture_triptych_01": AssetDefinition("picture", frozenset({"wall"})),
        "rug_round_01": _standard_asset("rug"),
        "mirror_arch_01": _standard_asset("mirror"),
        "plant_palm_01": _standard_asset("plant"),
        "vase_cylinder_01": _standard_asset("vase"),
        "book_stack_upright_01": _standard_asset("book_stack"),
        "clock_square_01": _standard_asset("clock"),
        "souvenir_figurine_01": _standard_asset("souvenir"),
        "souvenir_ornament_01": _standard_asset("souvenir"),
        "souvenir_trophy_01": _standard_asset("souvenir"),
        "shelf_coat_rack_01": AssetDefinition("shelf", frozenset({"floor"})),
        "storage_box_chest_01": AssetDefinition("storage_box", frozenset({"floor"})),
        "basket_laundry_01": AssetDefinition("basket", frozenset({"floor"})),
        "kitchen_counter_island_01": _standard_asset("kitchen_counter"),
        "kitchen_counter_sink_01": AssetDefinition(
            "kitchen_counter",
            frozenset({"floor"}),
            supplies_surface=True,
            fixed_by_default=True,
        ),
        "kitchen_counter_hob_01": AssetDefinition(
            "kitchen_counter",
            frozenset({"floor"}),
            fixed_by_default=True,
        ),
        "cooker_inset_hob_01": AssetDefinition(
            "cooker", frozenset({"surface"}), fixed_by_default=True,
        ),
        "kitchen_cabinet_oven_01": _standard_asset("kitchen_cabinet"),
        "washing_machine_stack_01": _standard_asset("washing_machine"),
        "window_covering_curtain_01": _standard_asset("window_covering"),
        "window_covering_blind_01": _standard_asset("window_covering"),
        "household_accessory_bowl_01": _standard_asset("household_accessory"),
        "household_accessory_utensil_holder_01": _standard_asset("household_accessory"),
        "household_accessory_soap_dispenser_01": _standard_asset("household_accessory"),
        "household_accessory_toothbrush_cup_01": _standard_asset("household_accessory"),
        "household_accessory_caddy_01": _standard_asset("household_accessory"),
        "household_accessory_toilet_brush_01": AssetDefinition(
            "household_accessory",
            frozenset({"floor"}),
        ),
        "household_accessory_toilet_paper_holder_01": AssetDefinition(
            "household_accessory",
            frozenset({"wall"}),
            fixed_by_default=True,
        ),
    }
)

# Expansion metadata has no dependency on mesh builders. All reserved IDs must
# have a shared renderer/geometry recipe before being exposed to the model.
from roometry.scenes.expansion_specs import EXPANSION_SPECS

for _spec in EXPANSION_SPECS:
    _assets[_spec.asset_id] = AssetDefinition(
        _spec.object_type, frozenset(_spec.placements), _spec.support, _spec.fixed
    )

for _key in ("window_covering_standard_01", "window_covering_curtain_01", "window_covering_sheer_01"):
    _assets[_key] = replace(_assets[_key], allows_unlinked_covering=True)

ASSET_REGISTRY = MappingProxyType(_assets)
