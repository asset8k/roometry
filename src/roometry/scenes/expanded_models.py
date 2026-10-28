"""Additional procedural models using the existing shared component primitives.

All shapes, support areas and placement configurations feed both browser meshes
and server geometry. Built-in fixtures expose ledges, never a support over a hole.
"""

from dataclasses import replace
from math import cos, sin, pi
from types import MappingProxyType

from roometry.scenes.expansion_specs import EXPANSION_SPECS, ExpansionSpec
from roometry.scenes.furniture import (
    FURNITURE_CATALOG, FurnitureAsset, SupportSurface, part, surface, legs,
    sofa, chair, table, bed, metal_shelf,
)
from roometry.scenes.fixture_models import (
    FIXTURE_CATALOG, base_cabinet, sink_counter, basin,
    cooker, toilet, bathtub, oven_front, panel_front, frame,
)
from roometry.scenes.decor import DECOR_CATALOG, curtains, rug, leaf_blade
from roometry.scenes.equipment import EQUIPMENT_CATALOG, rod


def combine(*models):
    return [p for parts, _ in models for p in parts], [s for _, areas in models for s in areas]


def fit(model, *, scale=(1., 1., 1.), at=(0., 0., 0.), prefix="", mirror=False):
    """Fit a subassembly into a normalized region, preserving its support names."""
    parts, surfaces = model
    sx, sy, sz = scale
    direction = -1 if mirror else 1
    converted = [replace(p, name=prefix + p.name,
                         size=(p.size[0] * sx, p.size[1] * sy, p.size[2] * sz),
                         at=(at[0] + direction * p.at[0] * sx, at[1] + p.at[1] * sy, at[2] + p.at[2] * sz),
                         rotation=(p.rotation[0], direction * p.rotation[1], direction * p.rotation[2])) for p in parts]
    areas = [replace(s, name=prefix + s.name, part=prefix + s.part,
                     y=at[1] + s.y * sy,
                     x_min=at[0] + (s.x_min if not mirror else -s.x_max) * sx,
                     x_max=at[0] + (s.x_max if not mirror else -s.x_min) * sx,
                     z_min=at[2] + s.z_min * sz, z_max=at[2] + s.z_max * sz) for s in surfaces]
    return converted, areas


def round_area(p, purpose="objects"):
    # A rectangle inscribed in the elliptical tabletop, not its bounding square.
    x, y, z = p.at
    w, h, d = p.size
    return SupportSurface(p.name, p.name, y + h / 2,
                          x - w * .34, x + w * .34, z - d * .34, z + d * .34, purpose)


def fronts(count=2, *, low=.12, high=.94, glass=False, drawers=False, paneled=False):
    parts = []
    for i in range(count):
        if drawers:
            size = (.91, (high - low) / count - .018, .035)
            at = (0, low + (i + .5) * (high - low) / count, .4475)
        else:
            size = (.94 / count - .018, high - low, .035)
            at = (-.47 + (i + .5) * .94 / count, (low + high) / 2, .4475)
        parts += (panel_front(f"front_{i}", size, at) if paneled and not glass
                  else [part(f"front_{i}", size, at, "glass" if glass else "primary")])
        parts.append(part(f"handle_{i}", (.15, .018, .025),
                          (at[0], at[1] + size[1] * .3, .4775), "metal", collision=False))
    return parts


def cabinet_shell(*, levels=3, open_front=False, glass=False, drawers=False, doors=2, floor=True):
    bottom = .1 if floor else 0
    parts = [part("left_side", (.035, 1 - bottom, .96), (-.4825, (1 + bottom) / 2, 0)),
             part("right_side", (.035, 1 - bottom, .96), (.4825, (1 + bottom) / 2, 0)),
             part("back", (.93, 1 - bottom, .035), (0, (1 + bottom) / 2, -.4825), "wood")]
    if floor:
        parts.append(part("plinth", (.9, .1, .9), (0, .05, 0), "primary_dark"))
    boards = [part(f"board_{i}", (.93, .025, .93), (0, bottom + .0125 + (1 - bottom - .025) * i / levels, 0), "wood") for i in range(levels + 1)]
    parts += boards
    if not open_front:
        parts += fronts(levels if drawers else doors, low=bottom + .035, high=.965, glass=glass, drawers=drawers)
    return parts, [surface(p, inset=.025) for p in boards]


def corner_model(*, mirror=False, slab=False, upper=False):
    top_y = 1 if not upper else .045
    boards = [part("rear_worktop", (1, .045 if not slab else 1, .35), (0, top_y - (.0225 if not slab else .5), -.325), "primary" if slab else "enamel"),
              part("return_worktop", (.27, .045 if not slab else 1, .65), (-.365, top_y - (.0225 if not slab else .5), .175), "primary" if slab else "enamel")]
    parts = boards.copy()
    if not slab:
        parts += [part("rear_cabinets", (.97, .9, .32), (0, .45, -.325)),
                  part("return_cabinets", (.25, .9, .65), (-.365, .45, .175))]
        for i in range(3):
            parts.append(part(f"rear_door_{i}", (.3, .76, .025), (-.32 + i * .32, .5, -.1525)))
        parts.append(part("return_door", (.025, .76, .6), (-.2275, .5, .175)))
        if upper:
            parts += [part("upper_rear_cap", (1, .035, .35), (0, .9825, -.325)),
                      part("upper_return_cap", (.27, .035, .65), (-.365, .9825, .175))]
    return fit((parts, [surface(p, inset=.015) for p in boards]), mirror=mirror)


def worktop_base(style):
    if style.startswith("counter_l") or style.startswith("corner_base") or style == "upper_corner":
        return corner_model(mirror=style.endswith("right"), upper=style == "upper_corner")
    if style == "counter_run":
        pieces = [fit(base_cabinet(worktop=True), scale=(1 / 3, 1, 1), at=(x, 0, 0), prefix=f"unit_{i}_")
                  for i, x in enumerate((-1 / 3, 0, 1 / 3))]
        p, _ = combine(*pieces)
        p = [a for a in p if not a.name.endswith("worktop")]
        top = part("continuous_worktop", (1, .045, 1), (0, .9775, 0), "enamel")
        return p + [top], [surface(top)]
    p, areas = base_cabinet(worktop=True, island=style in {"peninsula", "butcher_block"})
    if style in {"drawer_base", "single_base"}:
        p = [a for a in p if not a.name.startswith(("door_", "pull_"))]
        p += fronts(3 if style == "drawer_base" else 1, drawers=style == "drawer_base", paneled=True)
    if style == "waterfall":
        p += [part("waterfall_left", (.04, .955, 1), (-.48, .4775, 0), "enamel"),
              part("waterfall_right", (.04, .955, 1), (.48, .4775, 0), "enamel")]
    if style == "butcher_block":
        p = [replace(a, material="wood", size=(1, .08, 1), at=(0, .96, 0)) if a.name == "worktop" else a for a in p]
        for i in range(7):
            p.append(part(f"panel_molding_{i}", (.012, .65, .018), (-.43 + i * .143, .48, .473), collision=False))
    return p, [surface(next(a for a in p if a.name == "worktop"))]


def sink_assembly(style):
    if style in {"round_vessel", "rectangular_vessel", "apron_basin", "double_basin"}:
        if style == "round_vessel":
            return [basin("vessel", (1, 1, 1), (0, .5, 0))], []
        bowl = [part("bowl_floor", (1, .1, 1), (0, .05, 0), "primary_ceramic"),
                part("bowl_left", (.055, .9, 1), (-.4725, .55, 0), "primary_ceramic"),
                part("bowl_right", (.055, .9, 1), (.4725, .55, 0), "primary_ceramic"),
                part("bowl_back", (.89, .9, .055), (0, .55, -.4725), "primary_ceramic"),
                part("bowl_front", (.89, .9, .055), (0, .55, .4725), "primary_ceramic")]
        if style == "double_basin":
            bowl.append(part("bowl_divider", (.04, .9, .89), (0, .55, 0), "primary_ceramic"))
        if style == "apron_basin":
            bowl.append(part("apron_front", (.98, .85, .07), (0, .575, .465), "primary_ceramic"))
        return bowl, []
    p, areas = sink_counter()
    if style == "double_counter":
        p.append(part("sink_divider", (.025, .19, .51), (0, .645, 0), "chrome"))
    elif style == "apron_counter":
        p = [replace(a, material="enamel") if a.name.startswith("sink_") else a for a in p]
        p = [a for a in p if not a.name.startswith(("door_", "pull_"))]
        p += fronts(2, low=.15, high=.51, paneled=True)
        p += [part("apron_face", (.56, .21, .08), (0, .635, .46), "enamel"),
              part("apron_left_trim", (.2, .21, .04), (-.38, .635, .46)),
              part("apron_right_trim", (.2, .21, .04), (.38, .635, .46))]
    elif style == "drainer_counter":
        p, areas = fit((p, areas), scale=(.68, 1, 1), at=(-.16, 0, 0))
        deck = part("draining_board", (.32, .045, .86), (.34, .7175, 0), "chrome")
        p.append(deck)
        for i in range(6):
            p.append(part(f"drain_rib_{i}", (.015, .008, .68), (.21 + i * .046, .744, 0), "chrome", collision=False))
        areas.append(surface(deck, inset=.02))
    return p, areas


def plumbing(style):
    if style == "deck_faucet":
        p = [part("deck_base", (.4, .045, .25), (0, .0225, -.3), "primary_metal", shape="cylinder"),
             rod("riser", (0, .045, -.3), (0, .7, -.3), .035, "primary_metal"),
             part("side_lever", (.3, .035, .12), (.23, .6, -.3), "primary_metal")]
        for i in range(8):
            a, b = i * pi / 8, (i + 1) * pi / 8
            p.append(rod(f"gooseneck_{i}", (0, .7 + .25 * sin(a), -.05 - .25 * cos(a)),
                         (0, .7 + .25 * sin(b), -.05 - .25 * cos(b)), .035, "primary_metal"))
        p.append(part("outlet", (.15, .12, .12), (0, .64, .2), "chrome", shape="cylinder"))
        return p, []
    if style == "wall_faucet":
        return [part("wall_plate", (.85, .85, .06), (0, .5, -.47), "primary_metal"),
                part("spout", (.1, .15, .78), (0, .55, -.03), "chrome"),
                part("outlet", (.12, .25, .12), (0, .45, .3), "chrome"),
                part("mixer", (.18, .4, .14), (.3, .45, -.35), "primary_metal")], []
    p = [part("wall_mount", (.3, .12, .05), (0, .06, -.475), "primary_metal"),
         rod("riser", (0, .1, -.4), (0, .95, -.4), .035, "primary_metal"),
         part("mixer", (.8, .1, .12), (0, .2, -.4), "primary_metal")]
    if style == "rain_shower":
        p += [rod("overhead_arm", (0, .95, -.4), (0, .95, .2), .035, "primary_metal"),
              part("rain_head", (.7, .025, .55), (0, .92, .2), "primary_metal", shape="cylinder")]
    else:
        p += [part("handset", (.2, .2, .12), (0, .85, -.3), "chrome", shape="ellipsoid"),
              rod("hose_left", (-.18, .18, -.32), (-.12, .05, -.08), .025, "chrome"),
              rod("hose_right", (-.12, .05, -.08), (.1, .68, -.3), .025, "chrome")]
    return p, []


def vanity_model(style):
    if style in {"vanity_double", "vanity_double_floor"}:
        p, areas = combine(*(fit(sink_counter(), scale=(.5, 1, 1), at=(x, 0, 0), prefix=f"basin_{i}_")
                             for i, x in enumerate((-.25, .25))))
    else:
        p, areas = sink_counter()
    p = [replace(a, material="enamel") if 'sink_' in a.name else a for a in p]
    if style in {"vanity_floating", "vanity_double"}:
        p = [replace(a, size=(a.size[0], .04, a.size[2]), at=(a.at[0], .02, a.at[2])) if a.name.endswith("plinth") else a for a in p]
        p.append(part("wall_plate", (.9, .4, .035), (0, .24, -.4825), "wood"))
    if style == "vanity_open":
        p = [a for a in p if not a.name.startswith(("door_", "pull_"))]
        shelf = part("towel_shelf", (.84, .035, .82), (0, .29, 0), "wood")
        p.append(shelf)
        areas.append(surface(shelf, inset=.025))
    if style == "vanity_compact":
        p = [a for a in p if not a.name.startswith(("door_", "pull_"))] + fronts(1, high=.65, paneled=True)
    return p, areas


def shower_model(style):
    if style == "bath_screen":
        return [part("glass_screen", (.96, .98, .2), (0, .5, .1), "clear_glass"),
                part("wall_channel", (.04, 1, 1), (-.48, .5, 0), "primary_metal"),
                part("base_channel", (.96, .025, .3), (0, .0125, .1), "primary_metal")], []
    p = [part("tray", (1, .025, 1), (0, .0125, 0), "primary_ceramic"),
         part("wet_floor", (.94, .006, .94), (0, .028, 0), "enamel"),
         part("drain", (.15, .008, .1), (.2, .035, -.25), "chrome", collision=False)]
    p += frame("tray_lip", .034, .018, hole_x=.94, hole_z=.94, material="primary_ceramic")
    if style.startswith("walk_in"):
        side = -.49 if style.endswith("left") else .49
        p += [part("glass_screen", (.012, .965, .96), (side, .5175, 0), "clear_glass"),
              part("screen_base", (.02, .04, 1), (side, .04, 0), "primary_metal"),
              part("brace", (.98, .012, .018), (0, .9, -.42), "primary_metal")]
        for i, z in enumerate((-.34, .34)):
            p.append(part(f"screen_clamp_{i}", (.04, .05, .05),
                          (side / abs(side) * .48, .069, z), "chrome"))
        p.append(part("brace_socket", (.04, .035, .04),
                      (side / abs(side) * .48, .9, -.42), "chrome"))
    else:
        p += [part("back_glass", (.96, .96, .012), (0, .52, -.49), "clear_glass"),
              part("side_glass", (.012, .96, .96), (-.49, .52, 0), "clear_glass")]
        for i in range(12):
            angle = (i + .5) * pi / 24
            p.append(part(f"curved_screen_{i}", (.012, .96, .94 * pi / 24),
                          (-.47 + .94 * sin(angle), .52, -.47 + .94 * cos(angle)),
                          "clear_glass", rotation=(0, angle + pi / 2, 0)))
        p.append(part("door_handle", (.04, .12, .04), (.2, .52, .2), "primary_metal"))
    return p, []


def towel_model(style):
    if style == "towel_ring":
        return [part("wall_mount", (.22, .22, .12), (0, .85, -.44), "primary_metal"),
                part("ring", (.85, .72, .18), (0, .4, .15), "primary_metal", shape="torus", radius=.04)], []
    ladder = style == "towel_ladder"
    p = [part(f"upright_{i}", (.055, 1, .07), (x, .5, -.465), "primary_metal") for i, x in enumerate((-.42, .42))]
    for i in range(5 if ladder else 9):
        y = .12 + i * (.18 if ladder else .095)
        p.append(part(f"rung_{i}", (.84, .025, .07), (0, y, -.43), "primary_metal"))
    if ladder:
        p += [part(f"foot_{i}", (.08, .045, .9), (x, .0225, 0), "rubber") for i, x in enumerate((-.42, .42))]
    else:
        p += [part(f"mount_{i}", (.1, .06, .07), (x, y, -.465), "chrome")
              for i, (x, y) in enumerate(((-.35, .1), (.35, .1), (-.35, .9), (.35, .9)))]
    return p, []


def seating(style):
    if style == "curved_sofa":
        p, areas = legs(.12, x=.4, z=.25, thickness=.045, material="metal"), []
        for i in range(5):
            x = -.29 + i * .145
            z = .26 * (abs(x) / .29) ** 2 - .08
            yaw = -x
            seat = part(f"seat_{i}", (.185, .2, .58), (x, .42, z), radius=.045, rotation=(0, yaw, 0))
            p += [part(f"base_{i}", (.185, .22, .6), (x, .22, z), "primary_dark", radius=.04, rotation=(0, yaw, 0)),
                  seat, part(f"curved_back_{i}", (.185, .5, .16), (x - sin(yaw) * .31, .75, z - cos(yaw) * .31), radius=.045, rotation=(0, yaw, 0))]
            areas.append(surface(seat, purpose="seating"))
        return p, areas
    if style in {"modular_left", "modular_right"}:
        p, areas = sofa(chaise=-1 if style.endswith("left") else 1)
    else:
        p, areas = sofa(armchair=style in {"tufted_chair", "barrel_chair", "wing_chair", "recliner"})
    if style.startswith("modular") or style == "low_sofa":
        if style.startswith("modular"):
            p = [replace(a, radius=.055) if a.name.startswith(("arm_", "back_cushion_")) else a for a in p]
            p += [part(f"module_seam_{i}", (.008, .16, .5), (x, .27, -.15), "inset", collision=False)
                  for i, x in enumerate((-.14, .14))]
        else:
            p = [a for a in p if not a.name.startswith(("seat_", "back_cushion_"))]
            seats = [part(f"wide_seat_{i}", (.36, .18, .81), (x, .43, .07), radius=.05) for i, x in enumerate((-.19, .19))]
            p += seats + [part(f"wide_back_{i}", (.36, .38, .19), (x, .745, -.3),
                              radius=.055, rotation=(-.12, 0, 0)) for i, x in enumerate((-.19, .19))]
            areas = [surface(a, purpose="seating") for a in seats]
    if style in {"tufted_chair", "chesterfield"}:
        p = [replace(a, shape="ellipsoid", radius=.02) if a.name.startswith("arm_") else a for a in p]
        # Keep buttons on cushion faces, rather than suspended in the gaps
        # between the sofa's three independently rounded back cushions.
        button_xs = (-.3, -.15, 0, .15, .3) if style == "tufted_chair" else (-.74 / 3, 0, .74 / 3)
        for row in range(3):
            for col, x in enumerate(button_xs):
                y = .62 + row * .1
                p.append(part(f"tuft_{row}_{col}", (.02, .02, .012),
                              (x, y, -.3 - .12 * (y - .745) + .1),
                              "primary_dark", shape="ellipsoid", collision=False))
    if style == "barrel_chair":
        p = [a for a in p if not a.name.startswith(("arm_", "back_"))]
        p = [replace(a, size=(.9, .20, .82), radius=.065) if a.name == "base" else a for a in p]
        # Closely spaced, deeply rounded panels share a level crown. This reads
        # as one padded wraparound shell instead of a row of stepped posts.
        for i in range(25):
            angle = -2.1 + i * 4.2 / 24
            p.append(part(f"wrap_back_{i}", (.084, .46, .12),
                          (.38 * sin(angle), .73, -.34 * cos(angle)),
                          radius=.04, rotation=(0, -angle, 0)))
    if style == "wing_chair":
        p += [part(f"wing_{i}", (.1, .45, .32), (x, .775, -.28), radius=.035) for i, x in enumerate((-.35, .35))]
    if style == "recliner":
        p, areas = fit((p, areas), scale=(1, 1, .65), at=(0, 0, -.175))
        foot = part("raised_footrest", (.7, .14, .35), (0, .35, .325), radius=.04)
        p.append(foot)
        areas.append(surface(foot, purpose="seating"))
    return p, areas


def small_seating(style):
    if style in {"round_ottoman", "storage_ottoman"}:
        rounded = style == "round_ottoman"
        base = part("body", (.94, .82, .94), (0, .41, 0), "primary_dark", shape="cylinder" if rounded else "rounded_box", radius=.05)
        lid = part("padded_lid", (1, .16, 1), (0, .92, 0), shape="cylinder" if rounded else "rounded_box", radius=.05)
        return [base, lid], [round_area(lid, "seating") if rounded else surface(lid, purpose="seating")]
    if style == "storage_bench":
        p, _ = cabinet_shell(levels=1, open_front=True)
        cushion = part("seat_pad", (1, .12, 1), (0, .94, 0), radius=.035)
        p += [cushion, part("cubby_divider", (.03, .7, .92), (0, .5, 0), "wood")]
        bottom = next(a for a in p if a.name == "board_0")
        return p, [surface(bottom), surface(cushion, purpose="seating")]
    stool = style in {"woven_stool", "backed_stool"}
    p, areas = chair(stool=stool)
    if style == "dining_padded":
        p = [replace(a, material="primary", radius=.035) if "back" in a.name or "seat" in a.name else a for a in p]
    elif style == "dining_cantilever":
        p = [a for a in p if not a.name.startswith("leg_")]
        p += [part(f"skid_{i}", (.045, .045, .86), (x, .0225, 0), "metal") for i, x in enumerate((-.35, .35))]
        p += [part(f"front_upright_{i}", (.045, .43, .045), (x, .25, .4), "metal") for i, x in enumerate((-.35, .35))]
    elif style == "backed_stool":
        p, areas = fit((p, areas), scale=(1, .7, 1))
        p += [part("low_back", (.78, .28, .08), (0, .86, -.38), radius=.035)]
    elif style == "woven_stool":
        p = [replace(a, size=(a.size[0], a.size[1] - .025, a.size[2]), at=(a.at[0], a.at[1] - .0125, a.at[2]))
             if a.name == "seat" else a for a in p]
        for i in range(9):
            p += [part(f"woven_row_{i}", (.075, .02, .8), (-.34 + i * .085, .985, 0), "wood", collision=False),
                  part(f"woven_cross_{i}", (.8, .01, .075), (0, .995, -.34 + i * .085), "linen", collision=False)]
        areas = [surface(a, purpose="seating") for a in p if a.name == "seat"]
    return p, areas


def tables(style):
    if style == "l_desk":
        tops = [part("rear_worktop", (1, .05, .45), (0, .975, -.275)),
                part("side_worktop", (.35, .05, .55), (-.325, .975, .225))]
        p = tops + [part(f"leg_{i}", (.035, .95, .035), (x, .475, z), "metal")
                    for i, (x, z) in enumerate(((-.45, -.43), (.45, -.43), (.45, -.1), (-.45, .43), (-.2, .43)))]
        return p, [surface(t) for t in tops]
    if style == "standing_desk":
        top = part("worktop", (1, .04, 1), (0, .98, 0))
        p = [top]
        for i, x in enumerate((-.35, .35)):
            p += [part(f"foot_{i}", (.1, .05, .86), (x, .025, 0), "metal"),
                  part(f"lower_lift_{i}", (.075, .6, .1), (x, .35, 0), "metal"),
                  part(f"upper_lift_{i}", (.05, .55, .07), (x, .675, 0), "primary_metal")]
        p.append(part("lift_crossbar", (.7, .07, .07), (0, .8, 0), "metal"))
        return p, [surface(top)]
    if style == "nesting_tables":
        models = []
        for i, (x, size, height) in enumerate(((-.17, .65, 1), (.24, .5, .7))):
            top = part(f"tabletop_{i}", (size, .065, size), (x, height - .0325, 0), shape="cylinder")
            models.append(([top, part(f"pedestal_{i}", (.15, height - .065, .15), (x, (height - .065) / 2, 0), "metal", shape="cylinder")], [round_area(top)]))
        return combine(*models)
    if style in {"glass_round", "oval_table", "round_dining"}:
        shape = "ellipsoid" if style == "oval_table" else "cylinder"
        top = part("tabletop", (1, .07, 1), (0, .965, 0), "clear_glass" if style == "glass_round" else "primary", shape=shape)
        if style == "glass_round":
            p = legs(.93, x=.28, z=.28, thickness=.07) + [top,
                part("wood_edge", (.94, .94, .025), (0, .94, 0), "primary", shape="torus", radius=.025, rotation=(pi / 2, 0, 0))]
        else:
            p = [top, part("pedestal", (.3, .88, .3), (0, .44, 0), "primary", shape="cylinder"),
                 part("base", (.65, .05, .65), (0, .025, 0), "primary_dark", shape="cylinder")]
        return p, [round_area(top)]
    p, areas = table("table" if style == "extendable_table" else "coffee_table")
    if style == "extendable_table":
        p.append(part("extension_seam", (.006, .004, .96), (0, .998, 0), "primary_dark", collision=False))
    if style == "lift_table":
        p += [part("storage_body", (.86, .62, .82), (0, .6, 0), "primary_dark"),
              part("lid_seam", (.9, .012, .015), (0, .91, .42), "metal", collision=False)]
    return p, areas


def storage_models(style):
    if style == "offset_shelves":
        boards = [part("upper_board", (.72, .06, 1), (-.14, .97, 0), "primary"),
                  part("lower_board", (.72, .06, 1), (.14, .1, 0), "primary")]
        p = boards + [part(f"wall_mount_{i}", (.07, .35, .12), (x, .4, -.44), "metal") for i, x in enumerate((-.3, .3))]
        return p, [surface(a) for a in boards]
    if style == "corner_shelf":
        p = [part(f"post_{i}", (.04, 1, .04), (x, .5, z), "primary_metal")
             for i, (x, z) in enumerate(((-.46, -.46), (.46, -.46), (-.46, .46)))]
        boards = [part(f"shelf_{i}", (.92, .025, .92), (0, y, 0), "wood") for i, y in enumerate((.08, .37, .66, .95))]
        return p + boards, [surface(a, inset=.025) for a in boards]
    if style == "divider":
        p, areas = cabinet_shell(levels=4, open_front=True, floor=False)
        p = [a for a in p if a.name != "back"]
        p += [part(f"divider_{i}", (.025, .95, .93), (x, .5, 0), "primary") for i, x in enumerate((-.16, .16))]
        # Each cubby has its own usable support rectangle.
        areas = [replace(s, name=f"{s.name}_{i}", x_min=x - .13, x_max=x + .13)
                 for s in areas for i, x in enumerate((-.31, 0, .31))]
        return p, areas
    if style == "sliding_wardrobe":
        p, _ = cabinet_shell(levels=2, open_front=True)
        p += [part("left_sliding_door", (.48, .86, .025), (-.245, .54, .465)),
              part("right_mirror_door", (.48, .86, .025), (.245, .54, .48), "mirror"),
              part("upper_track", (.96, .02, .04), (0, .98, .47), "metal")]
        return p, []
    if style == "media_wall":
        console = fit(cabinet_shell(levels=1, doors=3), scale=(1, .23, 1), prefix="console_")
        towers = [fit(cabinet_shell(levels=4, open_front=True, floor=False), scale=(.16, 1, .85),
                      at=(x, 0, -.06), prefix=f"tower_{i}_") for i, x in enumerate((-.42, .42))]
        return combine(console, *towers)
    floating = style == "floating_console"
    p, _ = cabinet_shell(levels=1, doors=3, floor=not floating)
    if style == "fluted_console":
        for i in range(42):
            p.append(part(f"flute_{i}", (.01, .78, .015), (-.45 + i * .9 / 41, .54, .475), collision=False))
    if style == "wide_console":
        p = [replace(a, size=(a.size[0], .08, a.size[2]), at=(a.at[0], .04, a.at[2])) if a.name == "plinth" else a for a in p]
    top = next(a for a in p if a.name == "board_1")
    return p, [surface(top)]


def architectural(style):
    ceiling = style.startswith("ceiling_")
    if style == "tv_surround":
        p = [part("top", (1, .1, 1), (0, .95, 0), radius=.04),
             part("bottom", (1, .1, 1), (0, .05, 0), radius=.04),
             part("left", (.08, .8, 1), (-.46, .5, 0), radius=.035),
             part("right", (.08, .8, 1), (.46, .5, 0), radius=.035),
             part("led_top", (.82, .014, .035), (0, .895, .48), "light", collision=False),
             part("led_bottom", (.82, .014, .035), (0, .105, .48), "light", collision=False)]
        return p, []
    if ceiling:
        p = []
        for level in range(2 if style == "ceiling_recess" else 1):
            inset = level * .08
            for name, size, at in (
                ("rear", (1 - inset * 2, .6, .035), (0, .7 - level * .2, -.4825 + inset)),
                ("front", (1 - inset * 2, .6, .035), (0, .7 - level * .2, .4825 - inset)),
                ("left", (.035, .6, .93 - inset * 2), (-.4825 + inset, .7 - level * .2, 0)),
                ("right", (.035, .6, .93 - inset * 2), (.4825 - inset, .7 - level * .2, 0)),
            ):
                p += [part(f"frame_{level}_{name}", size, at),
                      part(f"led_{level}_{name}", (size[0], .025, size[2]), (at[0], at[1] - .3, at[2]), "light", collision=False)]
        return p, []
    p = [part("panel_back", (1, 1, .25), (0, .5, -.375), "primary")]
    if style == "panel_slatted":
        p += [part(f"wood_slat_{i}", (.035, 1, .65), (-.475 + i * .05, .5, .075), "wood", radius=.003) for i in range(20)]
    else:
        p += [part("top_molding", (1, .025, .55), (0, .9875, .225)),
              part("bottom_molding", (1, .025, .55), (0, .0125, .225)),
              part("left_molding", (.025, .95, .55), (-.4875, .5, .225)),
              part("right_molding", (.025, .95, .55), (.4875, .5, .225))]
        if style == "panel_wainscot":
            for i in range(3):
                p.append(part(f"inset_divider_{i}", (.025, .8, .3), (-.33 + i * .33, .5, .2)))
        if style == "panel_stone":
            p.append(part("stone_face", (.98, .98, .75), (0, .5, .125), "enamel"))
    return p, []


def lighting(style):
    if style == "geometric_light":
        p = [part("ceiling_rose", (.45, .07, .25), (0, .965, 0), "primary_metal")]
        for i, x in enumerate((-.3, .3)):
            p.append(part(f"suspension_{i}", (.008, .65, .008), (x, .605, 0), "metal"))
        for name, size, at in (("rear", (1, .025, .03), (0, .25, -.45)),
                               ("front", (1, .025, .03), (0, .25, .45)),
                               ("left", (.03, .025, .87), (-.485, .25, 0)),
                               ("right", (.03, .025, .87), (.485, .25, 0))):
            p += [part(f"frame_{name}", size, at, "primary_metal"),
                  part(f"diffuser_{name}", (size[0], .012, size[2]), (at[0], .23, at[2]), "light", collision=False)]
        return p, []
    floor = style == "floor_globe"
    p = [part("base", (.7, .06 if floor else .12, .7), (0, .03 if floor else .06, 0), "primary_metal", shape="cylinder")]
    if floor:
        p.append(part("stem", (.035, .75, .035), (0, .42, 0), "primary_metal", shape="cylinder"))
    p.append(part("opal_globe", (1, .24 if floor else .84, 1), (0, .88 if floor else .58, 0), "light", shape="ellipsoid"))
    return p, []


def foliage(style):
    wall = style == "wall_plant"
    trailing = style in {"wall_plant", "trailing_plant"}
    pot_y = .875 if wall else .125 if trailing else .15
    root_y = .99 if wall else .24 if trailing else .29
    pot_h = .25 if trailing else .3
    pot_z = -.1 if wall else 0
    p = [part("pot", (.6, pot_h, .6), (0, pot_y, pot_z), "primary", shape="tube", bottom_scale=.7, wall_thickness=.045),
         part("pot_bottom", (.42, .018, .42), (0, pot_y - pot_h / 2 + .009, pot_z), "primary", shape="cylinder"),
         part("soil", (.54, .015, .54), (0, root_y, -.1 if wall else 0), "soil", shape="cylinder", collision=False)]
    if wall:
        p.append(part("wall_bracket", (.4, .2, .06), (0, .85, -.47), "metal"))
    for i in range(5 if trailing else 7):
        angle = i * 2 * pi / (5 if trailing else 7)
        x, z = .34 * sin(angle), .3 * cos(angle)
        tip_y = .08 + i * .055 if trailing else .58 + (i % 3) * .18
        root = (0, .88 if wall else .24 if trailing else .28, -.1 if wall else 0)
        tip = (x, tip_y, z)
        if trailing and not wall:
            arch = (x * .45, .7 + (i % 2) * .15, z * .45)
            end = tuple(a + (b - a) * .88 for a, b in zip(arch, tip))
            p += [rod(f"stem_arch_{i}", root, arch, .012, "foliage_dark"),
                  rod(f"stem_tip_{i}", arch, end, .012, "foliage_dark")]
            root = arch
        else:
            end = tuple(a + (b - a) * .88 for a, b in zip(root, tip))
            p.append(rod(f"stem_{i}", root, end, .012, "foliage_dark"))
        for j in range(3):
            f = .4 + j * .22
            at = (x * f, root[1] + (tip_y - root[1]) * f, root[2] + (z - root[2]) * f)
            p += leaf_blade(f"leaf_{i}_{j}",
                            (.14 if trailing else .24, .12 if trailing else .25, .014 if trailing else .02),
                            at, (.55 + .15 * sin(angle + j), angle + j * .4, .25 * cos(angle)),
                            "foliage" if (i + j) % 3 else "foliage_dark")
    return p, []


def build(spec: ExpansionSpec):
    style = spec.recipe
    if style in {"low_metal_shelf", "medium_metal_shelf"}:
        return metal_shelf(2 if style == "low_metal_shelf" else 3)
    if style in {"drawer_base", "single_base", "corner_base_left", "corner_base_right", "counter_run", "counter_l_left", "counter_l_right", "peninsula", "waterfall", "butcher_block", "upper_corner"}:
        return worktop_base(style)
    if style in {"tall_pantry", "tall_appliance", "glass_pantry", "upper_single", "upper_glass", "upper_open", "medicine_cabinet"}:
        p, areas = cabinet_shell(levels=4 if style.startswith("tall") or style == "glass_pantry" else 2,
                                 doors=1 if style == "upper_single" else 2,
                                 open_front=style == "upper_open", glass=style in {"upper_glass", "glass_pantry"},
                                 floor=not style.startswith("upper") and style != "medicine_cabinet")
        if style == "tall_appliance":
            p = [a for a in p if not a.name.startswith(("front_", "handle_"))]
            upper = fit((fronts(1, low=.69, high=.965), []), prefix="upper_")[0]
            lower = fit((fronts(1, low=.12, high=.28), []), prefix="lower_")[0]
            p += upper + lower
            p += [replace(a, name="oven_" + a.name) for a in oven_front(.3, .65)]
        if style == "medicine_cabinet":
            p = [replace(a, material="mirror") if a.name.startswith("front_") else a for a in p]
        return p, areas
    if style.startswith("slab"):
        if style != "slab":
            return corner_model(slab=True, mirror=style.endswith("right"))
        slab = part("worktop", (1, 1, 1), (0, .5, 0), "primary")
        return [slab], [surface(slab)]
    if style in {"apron_counter", "double_counter", "drainer_counter", "apron_basin", "double_basin", "round_vessel", "rectangular_vessel"}:
        return sink_assembly(style)
    if style in {"deck_faucet", "wall_faucet", "rain_shower", "hand_shower"}:
        return plumbing(style)
    if style.startswith("vanity_"):
        return vanity_model(style)
    if style in {"toilet_concealed", "toilet_compact"}:
        p = toilet("wall")
        if style == "toilet_concealed":
            p.append(part("pedestal", (.65, .55, .65), (0, .275, -.05), "primary_ceramic"))
        return p, []
    if style in {"walk_in_left", "walk_in_right", "quadrant_shower", "bath_screen"}:
        return shower_model(style)
    if style in {"alcove_bath", "corner_bath"}:
        p, _ = bathtub()
        if style == "alcove_bath":
            p += [part("apron", (1, .9, .04), (0, .45, .48), "primary_ceramic"),
                  part("left_end", (.04, .9, .92), (-.48, .45, 0), "primary_ceramic")]
        else:
            p += [part("rear_ledge", (1, .12, .16), (0, .94, -.42), "primary_ceramic"),
                  part("side_ledge", (.16, .12, .84), (-.42, .94, .08), "primary_ceramic")]
        return p, []
    if style == "niche":
        p = [part("back", (1, 1, .05), (0, .5, -.475)),
             part("left", (.04, 1, 1), (-.48, .5, 0)), part("right", (.04, 1, 1), (.48, .5, 0))]
        boards = [part(f"ledge_{i}", (.92, .04, .95), (0, y, .025)) for i, y in enumerate((.02, .5, .98))]
        return p + boards, [surface(a) for a in boards]
    if style in {"towel_ladder", "towel_ring", "heated_towel"}:
        return towel_model(style)
    if style == "round_mirror":
        return [part("backlight", (.98, .1, .98), (0, .5, -.45), "light", shape="cylinder", rotation=(pi / 2, 0, 0), collision=False),
                part("mirror_disc", (1, 1, .18), (0, .5, .2), "mirror", shape="ellipsoid"),
                part("mount", (.6, .6, .1), (0, .5, -.45), "primary")], []
    if style in {"modular_sofa", "modular_left", "modular_right", "curved_sofa", "low_sofa", "chesterfield", "tufted_chair", "barrel_chair", "wing_chair", "recliner"}:
        return seating(style)
    if style in {"dining_padded", "dining_cantilever", "woven_stool", "backed_stool", "round_ottoman", "storage_ottoman", "storage_bench"}:
        return small_seating(style)
    if style in {"glass_round", "oval_table", "nesting_tables", "lift_table", "round_dining", "extendable_table", "l_desk", "standing_desk"}:
        return tables(style)
    if style in {"floating_console", "fluted_console", "wide_console", "media_wall", "offset_shelves", "divider", "corner_shelf", "sliding_wardrobe"}:
        return storage_models(style)
    if style == "padded_bed":
        p, areas = bed()
        p = [replace(a, material="primary", radius=.035) if a.name in {"frame", "headboard", "footboard"} else a for a in p]
        return p, areas
    if style.startswith(("panel_", "ceiling_")) or style == "tv_surround":
        return architectural(style)
    if style in {"geometric_light", "floor_globe", "table_globe"}:
        return lighting(style)
    if style in {"trailing_plant", "wall_plant", "monstera"}:
        return foliage(style)
    if style == "sheer_curtain":
        return [replace(a, material="glass") if a.shape == "pleated_panel" else a for a in curtains()], []
    if style == "roman_blind":
        p = [part("fabric_panel", (1, .96, .18), (0, .48, 0), "primary")]
        p += [part(f"fabric_fold_{i}", (1, .025, .8), (0, .12 + i * .16, .1), "linen") for i in range(6)]
        p.append(part("wall_rail", (1, .04, 1), (0, .98, 0), "primary_metal"))
        return p, []
    if style == "shag_rug":
        p = rug()
        p += [part(f"pile_row_{i}", (.93, .12, .008), (0, .85, -.45 + i * .03), "linen", collision=False) for i in range(31)]
        return p, []
    if style == "wide_range":
        p, areas = cooker()
        p += [part(f"extra_burner_{i}", (.2, .01, .23), (0, .99, z), "rubber", shape="torus", radius=.025) for i, z in enumerate((-.23, .22))]
        return p, areas
    if style == "french_fridge":
        p = [part("body", (1, 1, .92), (0, .5, -.04), "primary_metal")]
        p += [part(f"upper_door_{i}", (.485, .68, .05), (x, .65, .445), "primary_metal") for i, x in enumerate((-.25, .25))]
        p += [part("freezer_drawer", (.98, .27, .05), (0, .145, .445), "primary_metal")]
        p += [part(f"handle_{i}", (.025, .35, .035), (x, .67, .4825), "chrome") for i, x in enumerate((-.055, .055))]
        return p, []
    if style == "integrated_dishwasher":
        p, _ = base_cabinet()
        p = [a for a in p if not a.name.startswith(("door_", "pull_"))]
        p += fronts(1)
        p.append(part("control_strip", (.9, .045, .02), (0, .94, .475), "metal"))
        return p, []
    if style in {"chimney_hood", "integrated_hood"}:
        p = [part("canopy", (1, .25 if style == "chimney_hood" else 1, 1), (0, .125 if style == "chimney_hood" else .5, 0), "primary_metal")]
        if style == "chimney_hood":
            p.append(part("chimney", (.42, .75, .55), (0, .625, -.225), "primary_metal"))
        p.append(part("task_light", (.6, .025, .4), (0, .025, .1), "light", collision=False))
        return p, []
    raise ValueError(f"Missing expansion recipe: {style}")


def catalog():
    baseline = {**FURNITURE_CATALOG, **FIXTURE_CATALOG, **EQUIPMENT_CATALOG, **DECOR_CATALOG}
    result = {}
    for spec in EXPANSION_SPECS:
        parts, areas = build(spec)
        category = spec.batch if spec.batch in {"kitchen", "bathroom", "furniture"} else next(
            (a.category for a in baseline.values() if a.object_type == spec.object_type), "decor")
        # Small potted monstera uses the same recipe at a genuinely tabletop size.
        configs = (("surface", (.32, .48, .32)),) if spec.recipe == "monstera" else ()
        result[spec.asset_id] = FurnitureAsset(spec.asset_id, spec.object_type, spec.label, spec.dimensions,
                                               spec.color, tuple(parts), tuple(areas) if spec.support else (),
                                               category, configs)
    return result


EXPANSION_CATALOG = MappingProxyType(catalog())
