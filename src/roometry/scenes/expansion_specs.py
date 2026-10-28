"""The model expansion inventory, independent of renderer and registry imports.

Dimensions are nominal width/height/depth in metres. Variants describe geometry
and mounting changes; colour alone never creates another catalogue entry.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExpansionSpec:
    object_type: str
    variant: str
    label: str
    dimensions: tuple[float, float, float]
    recipe: str
    batch: str
    placements: tuple[str, ...] = ("floor",)
    support: bool = False
    fixed: bool = False
    color: str = "#B7A48A"

    @property
    def asset_id(self) -> str:
        return f"{self.object_type}_{self.variant}_01"


def kitchen(kind, variant, label, size, recipe, **kw):
    return ExpansionSpec(kind, variant, label, size, recipe, "kitchen", fixed=True, **kw)


def bathroom(kind, variant, label, size, recipe, **kw):
    return ExpansionSpec(kind, variant, label, size, recipe, "bathroom", fixed=kw.pop("fixed", True), **kw)


def furniture(kind, variant, label, size, recipe, **kw):
    return ExpansionSpec(kind, variant, label, size, recipe, "furniture", **kw)


def finishing(kind, variant, label, size, recipe, **kw):
    return ExpansionSpec(kind, variant, label, size, recipe, "finishing", **kw)


EXPANSION_SPECS = (
    # Kitchen — 32 models. Fitted runs and inset fixtures are single assemblies.
    kitchen("kitchen_cabinet", "drawers", "Three-drawer base cabinet", (.6, .9, .6), "drawer_base", support=True),
    kitchen("kitchen_cabinet", "single_door", "Single-door base cabinet", (.45, .9, .6), "single_base", support=True),
    kitchen("kitchen_cabinet", "corner_left", "Left corner base cabinet", (1.2, .9, 1.2), "corner_base_left", support=True),
    kitchen("kitchen_cabinet", "corner_right", "Right corner base cabinet", (1.2, .9, 1.2), "corner_base_right", support=True),
    kitchen("kitchen_cabinet", "tall_pantry", "Tall pantry cabinet", (.6, 2.2, .6), "tall_pantry"),
    kitchen("kitchen_cabinet", "tall_appliance", "Tall cabinet with built-in oven", (.6, 2.2, .6), "tall_appliance"),
    kitchen("kitchen_cabinet", "glass_pantry", "Glass-front pantry cabinet", (.8, 2.2, .42), "glass_pantry"),
    kitchen("wall_cabinet", "single", "Single-door upper cabinet", (.45, .75, .35), "upper_single", placements=("wall",)),
    kitchen("wall_cabinet", "glass", "Glass-front upper cabinet", (.8, .75, .35), "upper_glass", placements=("wall",)),
    kitchen("wall_cabinet", "open", "Open upper cabinet with shelves", (.6, .75, .32), "upper_open", placements=("wall",), support=True),
    kitchen("wall_cabinet", "corner", "Corner upper cabinet", (.7, .75, .7), "upper_corner", placements=("wall",)),
    kitchen("kitchen_counter", "run", "Three-cabinet run with continuous worktop", (2.4, .9, .65), "counter_run", support=True),
    kitchen("kitchen_counter", "l_left", "L-shaped fitted counter, left return", (2.4, .9, 1.8), "counter_l_left", support=True),
    kitchen("kitchen_counter", "l_right", "L-shaped fitted counter, right return", (2.4, .9, 1.8), "counter_l_right", support=True),
    kitchen("kitchen_counter", "peninsula", "Breakfast peninsula with seating overhang", (1.8, .9, .9), "peninsula", support=True),
    kitchen("kitchen_counter", "waterfall", "Waterfall stone kitchen island", (2., .9, 1.), "waterfall", support=True),
    kitchen("kitchen_counter", "butcher_block", "Paneled island with thick wood worktop", (1.8, .9, .9), "butcher_block", support=True),
    kitchen("kitchen_counter", "apron_sink", "Apron-front sink cabinet with faucet", (.9, 1.2, .65), "apron_counter", support=True),
    kitchen("kitchen_counter", "double_sink", "Double-bowl sink counter with faucet", (1.2, 1.2, .65), "double_counter", support=True),
    kitchen("kitchen_counter", "drainer_sink", "Sink counter with draining board and faucet", (1.2, 1.2, .65), "drainer_counter", support=True),
    kitchen("countertop", "standard", "Continuous straight worktop slab", (2.4, .04, .65), "slab", placements=("wall", "surface"), support=True),
    kitchen("countertop", "l_left", "L-shaped worktop slab, left return", (2.4, .04, 1.8), "slab_l_left", placements=("wall", "surface"), support=True),
    kitchen("countertop", "l_right", "L-shaped worktop slab, right return", (2.4, .04, 1.8), "slab_l_right", placements=("wall", "surface"), support=True),
    kitchen("sink", "apron_wall", "Wall-supported apron-front kitchen sink", (.8, .3, .58), "apron_basin", placements=("wall",), color="#E7E4DB"),
    kitchen("sink", "kitchen_double", "Wall-supported double kitchen sink", (1., .25, .55), "double_basin", placements=("wall",), color="#C1C4C4"),
    kitchen("faucet", "standard", "Deck-mounted gooseneck faucet", (.18, .35, .28), "deck_faucet", placements=("surface",), color="#AAB1B4"),
    kitchen("faucet", "wall", "Wall-mounted spout and mixer", (.3, .18, .25), "wall_faucet", placements=("wall",), color="#313436"),
    kitchen("extractor_hood", "chimney", "Box chimney extractor hood", (.9, .85, .5), "chimney_hood", placements=("wall",)),
    kitchen("extractor_hood", "integrated", "Integrated canopy extractor hood", (.6, .2, .32), "integrated_hood", placements=("wall",)),
    kitchen("cooker", "wide_range", "Wide range cooker with six burners", (.9, .9, .65), "wide_range", color="#363B3C"),
    kitchen("refrigerator", "french_door", "French-door refrigerator with lower freezer", (.9, 1.85, .72), "french_fridge", color="#B5BABB"),
    kitchen("dishwasher", "integrated", "Paneled integrated dishwasher", (.6, .82, .58), "integrated_dishwasher"),
    # Bathroom — 24 models, including independent wall shower fittings.
    bathroom("vanity", "floating_single", "Floating single-basin drawer vanity", (.9, .7, .5), "vanity_floating", placements=("wall",), support=True),
    bathroom("vanity", "floating_double", "Floating double-basin vanity", (1.5, .7, .52), "vanity_double", placements=("wall",), support=True),
    bathroom("vanity", "floor_double", "Double-basin vanity with floor legs", (1.5, 1., .52), "vanity_double_floor", support=True),
    bathroom("vanity", "inset_single", "Single vanity with inset basin and faucet", (.9, 1.1, .52), "vanity_inset", support=True),
    bathroom("vanity", "open_shelf", "Open-shelf basin vanity with towel storage", (.9, 1.1, .5), "vanity_open", support=True),
    bathroom("vanity", "compact", "Compact corner-bathroom vanity", (.5, .95, .38), "vanity_compact", support=True),
    bathroom("sink", "round_vessel", "Round countertop vessel basin", (.42, .15, .42), "round_vessel", placements=("surface",), color="#E7E4DB"),
    bathroom("sink", "rectangular_vessel", "Rectangular countertop vessel basin", (.55, .14, .38), "rectangular_vessel", placements=("surface",), color="#E7E4DB"),
    bathroom("toilet", "concealed_cistern", "Floor toilet with concealed cistern", (.38, .45, .62), "toilet_concealed", color="#E7E4DB"),
    bathroom("toilet", "wall_compact", "Compact wall-hung toilet", (.36, .36, .52), "toilet_compact", placements=("wall",), color="#E7E4DB"),
    bathroom("shower", "walk_in_left", "Walk-in shower with left glass screen", (1.2, 2.1, .9), "walk_in_left"),
    bathroom("shower", "walk_in_right", "Walk-in shower with right glass screen", (1.2, 2.1, .9), "walk_in_right"),
    bathroom("shower", "quadrant", "Quarter-round corner shower enclosure", (.9, 2.1, .9), "quadrant_shower"),
    bathroom("shower", "bath_screen", "Fixed bath-top glass shower screen", (.8, 1.4, .04), "bath_screen", placements=("wall",)),
    bathroom("shower_fixture", "standard", "Wall rainfall shower set with mixer", (.3, 1.1, .45), "rain_shower", placements=("wall",), color="#333638"),
    bathroom("shower_fixture", "handheld", "Handheld shower rail, hose and mixer", (.25, .9, .15), "hand_shower", placements=("wall",), color="#AFB6B8"),
    bathroom("bathtub", "alcove", "Paneled alcove bathtub", (1.7, .6, .75), "alcove_bath", color="#E7E4DB"),
    bathroom("bathtub", "corner", "Corner bathtub with rounded front", (1.4, .6, 1.4), "corner_bath", color="#E7E4DB"),
    bathroom("wall_shelf", "shower_niche", "Framed shower niche insert with shelf", (.6, .4, .12), "niche", placements=("wall",), support=True),
    bathroom("towel_rack", "ladder", "Freestanding floor towel ladder", (.5, 1.6, .28), "towel_ladder", fixed=False),
    bathroom("towel_rack", "ring", "Wall towel ring", (.2, .24, .08), "towel_ring", placements=("wall",)),
    bathroom("towel_rack", "heated", "Wall heated towel ladder", (.5, 1., .1), "heated_towel", placements=("wall",)),
    bathroom("mirror", "round_backlit", "Round backlit bathroom mirror", (.75, .75, .05), "round_mirror", placements=("wall",)),
    bathroom("wall_cabinet", "medicine_mirror", "Mirrored medicine cabinet with shelves", (.6, .75, .16), "medicine_cabinet", placements=("wall",), support=True),
    # Furniture — 36 models. New silhouettes, mechanisms and mounting options.
    furniture("sofa", "modular_straight", "Low modular sofa with broad cushions", (2.6, .85, 1.), "modular_sofa", color="#E1DCD1"),
    furniture("sofa", "modular_corner_left", "Modular sectional with left return", (3., .85, 2.), "modular_left", color="#E1DCD1"),
    furniture("sofa", "modular_corner_right", "Modular sectional with right return", (3., .85, 2.), "modular_right", color="#E1DCD1"),
    furniture("sofa", "curved", "Curved crescent sofa", (2.5, .85, 1.1), "curved_sofa", color="#DBD4C7"),
    furniture("sofa", "low_profile", "Low-profile sofa on exposed metal legs", (2.2, .8, .9), "low_sofa", color="#949C9F"),
    furniture("sofa", "chesterfield", "Tufted roll-arm Chesterfield sofa", (2.3, .8, .95), "chesterfield", color="#6B5846"),
    furniture("armchair", "tufted", "Deep tufted lounge chair", (1., .85, 1.), "tufted_chair", color="#315B47"),
    furniture("armchair", "barrel", "Rounded barrel lounge chair", (.85, .8, .85), "barrel_chair", color="#BBAA91"),
    furniture("armchair", "wingback", "High-back wing chair", (.85, 1.1, .9), "wing_chair", color="#9A8C7E"),
    furniture("armchair", "recliner", "Reclining lounge chair with footrest", (.9, 1., 1.45), "recliner", color="#777B78"),
    furniture("chair", "dining_upholstered", "Upholstered dining chair with padded back", (.52, .9, .58), "dining_padded"),
    furniture("chair", "dining_cantilever", "Cantilever-frame dining chair", (.5, .86, .56), "dining_cantilever"),
    furniture("stool", "woven_bar", "Woven-seat backless bar stool", (.42, .7, .42), "woven_stool"),
    furniture("stool", "backed_bar", "Padded bar stool with low back", (.48, 1., .5), "backed_stool"),
    furniture("ottoman", "round", "Round upholstered ottoman", (.8, .42, .8), "round_ottoman"),
    furniture("ottoman", "storage", "Upholstered storage ottoman with lid seam", (.9, .45, .55), "storage_ottoman"),
    furniture("bench", "storage", "Entry bench with lower cubby storage", (1.2, .5, .4), "storage_bench", support=True),
    furniture("coffee_table", "round_glass", "Round glass coffee table with wood legs", (.9, .42, .9), "glass_round", support=True),
    furniture("coffee_table", "oval", "Oval pedestal coffee table", (1.2, .4, .65), "oval_table", support=True),
    furniture("coffee_table", "nesting", "Pair of nesting round coffee tables", (1.1, .45, .75), "nesting_tables", support=True),
    furniture("coffee_table", "lift_top", "Closed lift-top coffee table with storage", (1.1, .45, .6), "lift_table", support=True),
    furniture("table", "round_pedestal", "Round pedestal dining table", (1.1, .75, 1.1), "round_dining", support=True),
    furniture("table", "extendable", "Rectangular dining table with extension seam", (1.8, .75, .9), "extendable_table", support=True),
    furniture("tv_stand", "floating", "Wall-mounted floating media console", (1.8, .35, .4), "floating_console", placements=("wall",), support=True, fixed=True),
    furniture("tv_stand", "fluted", "Fluted-front low media console", (1.8, .55, .42), "fluted_console", support=True),
    furniture("tv_stand", "low_wide", "Wide low media console on a plinth", (2.4, .4, .45), "wide_console", support=True),
    furniture("tv_stand", "media_wall", "Media wall assembly with console and side shelves, no TV", (2.8, 2.2, .45), "media_wall", support=True, fixed=True),
    furniture("wall_shelf", "offset", "Offset floating wall shelves", (1.5, .55, .24), "offset_shelves", placements=("wall",), support=True, fixed=True),
    furniture("shelf", "room_divider", "Open cubby room-divider shelving", (1.4, 1.6, .35), "divider", support=True),
    furniture("shelf", "corner", "Corner open shelving unit", (.55, 1.8, .55), "corner_shelf", support=True),
    furniture("shelf", "low_metal", "Low waist-height open metal and wood shelving console, two tiers", (1.05, .8, .35), "low_metal_shelf", support=True),
    furniture("shelf", "medium_metal", "Medium-height open metal and wood shelving, three tiers", (.65, 1.1, .32), "medium_metal_shelf", support=True),
    furniture("wardrobe", "sliding", "Sliding-door wardrobe with mirrored panel", (1.8, 2.2, .6), "sliding_wardrobe"),
    furniture("bed", "upholstered", "Upholstered bed with padded headboard", (1.65, 1.05, 2.15), "padded_bed"),
    furniture("desk", "l_shaped", "L-shaped desk with side return", (1.6, .75, 1.3), "l_desk", support=True),
    furniture("desk", "standing", "Height-adjustable standing desk", (1.4, 1.05, .7), "standing_desk", support=True),
    # Finishing and decor — 16 models; architecture has explicit semantic types.
    finishing("wall_panel", "standard", "Plain framed wall panel", (1.2, 2.4, .04), "panel_plain", placements=("wall",), fixed=True),
    finishing("wall_panel", "slatted", "Vertical wood-slat accent panel", (1.2, 2.4, .06), "panel_slatted", placements=("wall",), fixed=True),
    finishing("wall_panel", "wainscot", "Paneled wainscot wall section", (1.2, 1., .04), "panel_wainscot", placements=("wall",), fixed=True),
    finishing("wall_panel", "stone", "Stone backsplash wall panel", (1.8, .6, .025), "panel_stone", placements=("wall",), fixed=True),
    finishing("wall_panel", "tv_surround", "Rounded illuminated TV surround, open center", (2.2, 1.5, .12), "tv_surround", placements=("wall",), fixed=True),
    finishing("ceiling_detail", "standard", "Perimeter ceiling cove frame with light strip", (4., .15, 5.), "ceiling_cove", placements=("ceiling",), fixed=True),
    finishing("ceiling_detail", "recessed", "Layered recessed ceiling frame", (3., .2, 4.), "ceiling_recess", placements=("ceiling",), fixed=True),
    finishing("ceiling_light", "geometric", "Geometric suspended rectangular LED pendant", (1.2, .5, .8), "geometric_light", placements=("ceiling",), fixed=True),
    finishing("floor_lamp", "globe", "Floor lamp with spherical opal shade", (.35, 1.5, .35), "floor_globe"),
    finishing("table_lamp", "globe", "Small spherical opal table lamp", (.23, .28, .23), "table_globe", placements=("surface",)),
    finishing("plant", "trailing_surface", "Trailing pothos in a tabletop pot", (.45, .65, .4), "trailing_plant", placements=("surface",), color="#A18C66"),
    finishing("plant", "trailing_wall", "Trailing plant in a wall-mounted pot", (.45, .9, .28), "wall_plant", placements=("wall",), fixed=True, color="#A18C66"),
    finishing("plant", "monstera", "Broad-leaf monstera in a floor planter", (.85, 1.3, .85), "monstera", placements=("floor", "surface"), color="#BEAA8C"),
    finishing("window_covering", "sheer", "Pleated sheer curtains with rail", (1.8, 2.2, .12), "sheer_curtain", placements=("wall",), fixed=True),
    finishing("window_covering", "roman", "Folded fabric Roman blind", (1.2, 1.5, .08), "roman_blind", placements=("wall",), fixed=True),
    finishing("rug", "shaggy", "Deep-pile area rug with bound edge", (2., .025, 2.8), "shag_rug"),
)

EXPANSION_IDS = frozenset(spec.asset_id for spec in EXPANSION_SPECS)
