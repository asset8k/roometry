"""Kitchen, bathroom and utility models, including reserved composite variants.

Basins describe a closed ceramic cross-section with an open interior. Counter
ledges are individual support rectangles; sinks and hobs are not generic flat
supports. These component definitions are inputs for the later geometry update.
"""

from dataclasses import replace
from types import MappingProxyType

from roometry.scenes.assets import ObjectType, Placement
from roometry.scenes.equipment import FRONT, configurations, disc, rod
from roometry.scenes.furniture import (
    Category,
    FurnitureAsset,
    FurniturePart,
    MaterialRole,
    Point,
    SupportSurface,
    part,
    surface,
)

Model = tuple[list[FurniturePart], list[SupportSurface]]
BASIN_PROFILE = (
    (0, 0),
    (0.28, 0),
    (0.37, 0.04),
    (0.44, 0.16),
    (0.48, 0.4),
    (0.5, 0.88),
    (0.495, 0.97),
    (0.48, 1),
    (0.455, 0.98),
    (0.45, 0.86),
    (0.425, 0.48),
    (0.35, 0.27),
    (0.22, 0.18),
    (0, 0.18),
)


def basin(
    name: str, size: Point, at: Point, material: MaterialRole = "primary_ceramic"
) -> FurniturePart:
    return part(name, size, at, material, shape="basin", profile=BASIN_PROFILE)


def frame(
    name: str,
    y: float,
    thickness: float,
    *,
    outer_x: float = 1,
    outer_z: float = 1,
    hole_x: float = 0.56,
    hole_z: float = 0.54,
    material: MaterialRole = "enamel",
) -> list[FurniturePart]:
    side = (outer_x - hole_x) / 2
    end = (outer_z - hole_z) / 2
    return [
        part(
            f"{name}_left",
            (side, thickness, outer_z),
            (-(hole_x + side) / 2, y, 0),
            material,
        ),
        part(
            f"{name}_right",
            (side, thickness, outer_z),
            ((hole_x + side) / 2, y, 0),
            material,
        ),
        part(
            f"{name}_back",
            (hole_x, thickness, end),
            (0, y, -(hole_z + end) / 2),
            material,
        ),
        part(
            f"{name}_front",
            (hole_x, thickness, end),
            (0, y, (hole_z + end) / 2),
            material,
        ),
    ]


def faucet(deck_y: float, *, z: float = -0.4, top: float = 1) -> list[FurniturePart]:
    upright_top = top - 0.025
    return [
        part(
            "tap_base",
            (0.12, 0.025, 0.14),
            (0, deck_y + 0.0125, z),
            "chrome",
            shape="cylinder",
        ),
        rod("tap_riser", (0, deck_y + 0.02, z), (0, upright_top, z), 0.024, "chrome"),
        rod(
            "tap_spout",
            (0, upright_top, z),
            (0, upright_top, z + 0.24),
            0.024,
            "chrome",
        ),
        part(
            "tap_outlet",
            (0.048, 0.05, 0.048),
            (0, top - 0.05, z + 0.24),
            "chrome",
            shape="cylinder",
        ),
        part("tap_lever", (0.16, 0.018, 0.035), (0.04, top - 0.009, z), "chrome"),
    ]


def base_cabinet(
    *, top_y: float = 1, oven: bool = False, island: bool = False, worktop: bool = False
) -> Model:
    p = [
        part("plinth", (0.86, 0.10, 0.82), (0, 0.05, -0.02), "primary_dark"),
        part("bottom", (0.94, 0.04, 0.9), (0, 0.12, -0.025)),
        part(
            "side_left", (0.04, top_y - 0.15, 0.9), (-0.45, (top_y + 0.11) / 2, -0.025)
        ),
        part(
            "side_right", (0.04, top_y - 0.15, 0.9), (0.45, (top_y + 0.11) / 2, -0.025)
        ),
        part(
            "back",
            (0.86, top_y - 0.15, 0.035),
            (0, (top_y + 0.11) / 2, -0.4575),
            "wood",
        ),
    ]
    top = part(
        "worktop",
        (1, 0.045, 1),
        (0, top_y - 0.0225, 0),
        "enamel" if worktop else "primary",
    )
    p.append(top)
    if oven:
        p += oven_front(0.19, top_y - 0.08)
    else:
        door_h = top_y - 0.20
        for side in (-1, 1):
            p += [
                part(
                    f"door_{side}",
                    (0.44, door_h, 0.035),
                    (side * 0.227, 0.15 + door_h / 2, 0.4425),
                ),
                part(
                    f"pull_{side}",
                    (0.18, 0.018, 0.035),
                    (side * 0.227, top_y - 0.14, 0.4725),
                    "chrome",
                    collision=False,
                ),
            ]
    if island:
        p.append(
            part(
                "rear_panel",
                (0.94, top_y - 0.14, 0.025),
                (0, (top_y + 0.10) / 2, -0.4625),
            )
        )
    return p, [surface(top)] if worktop else []


def oven_front(low: float, high: float) -> list[FurniturePart]:
    h = high - low
    p = [
        part(
            "oven_door",
            (0.86, h * 0.76, 0.03),
            (0, low + h * 0.38, 0.46),
            "primary_dark",
        ),
        part(
            "oven_window",
            (0.68, h * 0.46, 0.012),
            (0, low + h * 0.34, 0.481),
            "screen",
            collision=False,
        ),
        part(
            "oven_handle", (0.65, 0.028, 0.035), (0, low + h * 0.67, 0.4825), "chrome"
        ),
        part("oven_controls", (0.86, h * 0.19, 0.03), (0, low + h * 0.89, 0.46)),
    ]
    for x in (-0.28, 0.28):
        p.append(
            disc(
                f"oven_knob_{x}",
                (0.085, h * 0.10, 0.025),
                (x, low + h * 0.89, 0.4875),
                "chrome",
                collision=False,
            )
        )
    p.append(
        part(
            "oven_display",
            (0.18, h * 0.075, 0.012),
            (0, low + h * 0.89, 0.481),
            "screen",
            collision=False,
        )
    )
    return p


def wall_cabinet() -> Model:
    p = [
        part("carcase", (1, 1, 0.94), (0, 0.5, -0.03)),
        part("recess", (0.94, 0.94, 0.025), (0, 0.5, 0.4525), "primary_dark"),
    ]
    for side in (-1, 1):
        p += [
            part(f"door_{side}", (0.46, 0.94, 0.025), (side * 0.238, 0.5, 0.4775)),
            part(
                f"pull_{side}",
                (0.024, 0.22, 0.02),
                (side * 0.045, 0.47, 0.49),
                "chrome",
                collision=False,
            ),
        ]
    return p, []


def sink_counter() -> Model:
    p, _ = base_cabinet(top_y=0.74, worktop=True)
    p = [piece for piece in p if piece.name != "worktop"]
    deck = frame("deck", 0.7175, 0.045, material="enamel")
    p += deck
    # A rectangular stainless bowl below the cutout, with an open interior.
    p += [
        part("sink_floor", (0.56, 0.025, 0.54), (0, 0.5375, 0), "chrome"),
        part("sink_left", (0.025, 0.19, 0.54), (-0.2675, 0.645, 0), "chrome"),
        part("sink_right", (0.025, 0.19, 0.54), (0.2675, 0.645, 0), "chrome"),
        part("sink_back", (0.51, 0.19, 0.025), (0, 0.645, -0.2575), "chrome"),
        part("sink_front", (0.51, 0.19, 0.025), (0, 0.645, 0.2575), "chrome"),
        part(
            "drain",
            (0.075, 0.008, 0.075),
            (0, 0.554, 0),
            "inset",
            shape="cylinder",
            collision=False,
        ),
    ]
    p += faucet(0.74)
    return p, [surface(piece) for piece in deck]


def burners(y: float, *, width: float = 0.27) -> list[FurniturePart]:
    p = []
    for i, (x, z) in enumerate(
        ((-0.23, -0.24), (0.23, -0.24), (-0.23, 0.24), (0.23, 0.24))
    ):
        p += [
            part(
                f"burner_{i}",
                (width, 0.006, width),
                (x, y - 0.002, z),
                "rubber",
                shape="cylinder",
            ),
            part(
                f"burner_ring_{i}",
                (width * 0.8, width * 0.8, 0.006),
                (x, y, z),
                "chrome",
                shape="torus",
                radius=0.016,
                rotation=FRONT,
                collision=False,
            ),
        ]
    return p


def hob_counter() -> Model:
    p, _ = base_cabinet(top_y=0.98, worktop=True)
    p.append(part("hob", (0.84, 0.015, 0.84), (0, 0.9875, 0), "screen"))
    p += burners(0.997, width=0.26)
    return p, []


def hood() -> Model:
    p = frame("rim", 0.03, 0.06, hole_x=0.76, hole_z=0.76, material="primary_metal")
    p += [
        part(
            "canopy",
            (1, 0.24, 1),
            (0, 0.18, 0),
            "primary_metal",
            shape="frustum",
            top_scale=0.35,
        ),
        part("chimney", (0.34, 0.70, 0.60), (0, 0.65, -0.20)),
        part("filter", (0.76, 0.014, 0.76), (0, 0.014, 0), "metal"),
        part(
            "front_controls",
            (0.22, 0.025, 0.012),
            (0.25, 0.035, 0.49),
            "inset",
            collision=False,
        ),
    ]
    for x in (-0.31, 0.31):
        p.append(
            part(
                f"lamp_{x}",
                (0.075, 0.012, 0.075),
                (x, 0.01, 0.32),
                "light",
                shape="cylinder",
                collision=False,
            )
        )
    return p, []


def refrigerator() -> Model:
    p = [
        part("body", (1, 0.98, 0.95), (0, 0.51, -0.025), radius=0.025),
        part("fridge_door", (0.95, 0.62, 0.05), (0, 0.65, 0.465), radius=0.02),
        part("freezer_door", (0.95, 0.27, 0.05), (0, 0.185, 0.465), radius=0.02),
    ]
    for i, (y, h) in enumerate(((0.57, 0.27), (0.19, 0.14))):
        p.append(part(f"handle_{i}", (0.024, h, 0.02), (-0.36, y, 0.49), "chrome"))
    for x in (-0.38, 0.38):
        for z in (-0.35, 0.3):
            p.append(part(f"foot_{x}_{z}", (0.09, 0.03, 0.09), (x, 0.015, z), "rubber"))
    return p, []


def cooker() -> Model:
    p, _ = base_cabinet(top_y=0.99, oven=True)
    p += burners(0.997)
    return p, []


def dishwasher() -> Model:
    p = [
        part("body", (1, 0.94, 0.94), (0, 0.53, -0.03)),
        part("plinth", (0.92, 0.08, 0.86), (0, 0.04, -0.02), "primary_dark"),
        part("door", (0.94, 0.72, 0.035), (0, 0.45, 0.4575)),
        part("controls", (0.94, 0.14, 0.035), (0, 0.9, 0.4575), "primary_dark"),
        part("handle", (0.64, 0.026, 0.025), (0, 0.78, 0.4875), "chrome"),
        part(
            "display",
            (0.19, 0.055, 0.014),
            (0.2, 0.9, 0.482),
            "screen",
            collision=False,
        ),
    ]
    for x in (-0.30, -0.18):
        p.append(
            disc(
                f"button_{x}",
                (0.04, 0.04, 0.02),
                (x, 0.9, 0.485),
                "chrome",
                collision=False,
            )
        )
    return p, []


def laundry_machine(*, dryer: bool = False) -> list[FurniturePart]:
    p = [
        part("body", (1, 0.88, 0.94), (0, 0.52, -0.02), radius=0.025),
        part("top", (1, 0.04, 0.96), (0, 0.98, -0.02)),
        part("plinth", (0.94, 0.08, 0.88), (0, 0.04, -0.02), "primary_dark"),
        part("controls", (0.94, 0.13, 0.025), (0, 0.87, 0.4625)),
        part(
            "drawer",
            (0.28, 0.065, 0.012),
            (-0.27, 0.87, 0.48),
            "primary_dark",
            collision=False,
        ),
        part(
            "display",
            (0.17, 0.05, 0.012),
            (0.27, 0.87, 0.48),
            "screen",
            collision=False,
        ),
        disc("dial", (0.10, 0.08, 0.025), (0, 0.87, 0.4875), "chrome", collision=False),
        part(
            "door_rim",
            (0.7, 0.50, 0.05),
            (0, 0.46, 0.475),
            "chrome",
            shape="torus",
            radius=0.065,
        ),
        disc("door_recess", (0.64, 0.45, 0.025), (0, 0.46, 0.463), "rubber"),
        disc("drum", (0.50, 0.35, 0.014), (0, 0.46, 0.484), "screen", collision=False),
        disc(
            "door_glass",
            (0.61, 0.43, 0.012),
            (0, 0.46, 0.493),
            "clear_glass",
            collision=False,
        ),
        part(
            "door_latch",
            (0.035, 0.11, 0.025),
            (0.29, 0.46, 0.4875),
            "primary_dark",
            collision=False,
        ),
    ]
    if dryer:
        for i in range(4):
            p.append(
                part(
                    f"vent_{i}",
                    (0.6, 0.012, 0.01),
                    (0, 0.12 + 0.025 * i, 0.457),
                    "inset",
                    collision=False,
                    shape="box",
                )
            )
    else:
        p.append(
            part(
                "service_panel",
                (0.17, 0.08, 0.012),
                (0.31, 0.16, 0.458),
                "primary_dark",
                collision=False,
            )
        )
    return p


def laundry_stack() -> Model:
    p = []
    for name, offset, recipe in (
        ("washer", 0, laundry_machine()),
        ("dryer", 0.5, laundry_machine(dryer=True)),
    ):
        for piece in recipe:
            # Front-facing cylinders have their local Z axis along world Y.
            axis = 2 if piece.rotation == FRONT else 1
            size = list(piece.size)
            size[axis] *= 0.5
            p.append(
                replace(
                    piece,
                    name=f"{name}_{piece.name}",
                    size=(size[0], size[1], size[2]),
                    at=(piece.at[0], piece.at[1] * 0.5 + offset, piece.at[2]),
                )
            )
    return p, []


def microwave() -> Model:
    p = [
        part("body", (1, 0.92, 0.96), (0, 0.54, -0.02), radius=0.025),
        part("front", (0.95, 0.80, 0.035), (0, 0.53, 0.4575), "primary_dark"),
        part(
            "window",
            (0.62, 0.61, 0.012),
            (-0.13, 0.53, 0.481),
            "screen",
            collision=False,
        ),
        part("handle", (0.035, 0.55, 0.025), (0.23, 0.53, 0.4875), "chrome"),
        part(
            "display",
            (0.13, 0.12, 0.012),
            (0.37, 0.74, 0.481),
            "screen",
            collision=False,
        ),
    ]
    for y in (0.32, 0.48):
        p.append(
            disc(
                f"control_{y}",
                (0.08, 0.12, 0.024),
                (0.37, y, 0.488),
                "chrome",
                collision=False,
            )
        )
    for x in (-0.36, 0.36):
        for z in (-0.34, 0.30):
            p.append(part(f"foot_{x}_{z}", (0.1, 0.08, 0.1), (x, 0.04, z), "rubber"))
    return p, []


def kettle() -> Model:
    p = [
        part(
            "base", (0.72, 0.035, 0.92), (-0.10, 0.0175, 0), "rubber", shape="cylinder"
        ),
        part(
            "body",
            (0.66, 0.74, 0.88),
            (-0.10, 0.47, 0),
            "primary_metal",
            shape="cylinder",
            top_scale=0.7,
        ),
        part("rounded_base", (0.66, 0.13, 0.88), (-0.10, 0.10, 0), "primary_metal", shape="ellipsoid"),
        part("lid", (0.5, 0.06, 0.63), (-0.10, 0.87, 0), shape="cylinder"),
        part(
            "lid_knob",
            (0.1, 0.1, 0.12),
            (-0.10, 0.95, 0),
            "primary_dark",
            shape="ellipsoid",
        ),
        part(
            "handle",
            (0.34, 0.60, 0.12),
            (0.32, 0.50, 0),
            "rubber",
            shape="torus",
            radius=0.10,
        ),
        rod("spout", (-0.31, 0.57, 0), (-0.465, 0.77, 0), 0.035, "primary_metal"),
        part(
            "spout_tip",
            (0.068, 0.047, 0.075),
            (-0.46, 0.77, 0),
            "inset",
            shape="ellipsoid",
            collision=False,
        ),
        part(
            "switch",
            (0.07, 0.035, 0.12),
            (0.26, 0.20, 0),
            "primary_dark",
            collision=False,
        ),
    ]
    return p, []


def toaster() -> Model:
    p = [
        part("body", (1, 0.86, 1), (0, 0.51, 0), radius=0.07),
        part("top", (0.94, 0.06, 0.94), (0, 0.97, 0), "chrome", radius=0.025),
        part("base", (0.92, 0.08, 0.9), (0, 0.04, 0), "rubber"),
        part("lever", (0.04, 0.08, 0.15), (0.48, 0.56, 0), "chrome"),
        disc(
            "browning_dial",
            (0.12, 0.20, 0.04),
            (0.28, 0.32, 0.48),
            "chrome",
            collision=False,
        ),
    ]
    for z in (-0.22, 0.22):
        p.append(
            part(
                f"slot_{z}",
                (0.70, 0.008, 0.14),
                (0, 0.996, z),
                "rubber",
                radius=0.015,
                collision=False,
            )
        )
    return p, []


def sink(placement: Placement) -> list[FurniturePart]:
    wall = placement == "wall"
    deck_y, bowl_h = (0.48, 0.48) if wall else (0.83, 0.18)
    p = [
        basin("bowl", (1, bowl_h, 0.9), (0, deck_y - bowl_h / 2, 0.04)),
        part(
            "tap_ledge",
            (0.38, 0.055, 0.17),
            (0, deck_y - 0.0275, -0.415),
            "primary_ceramic",
        ),
    ]
    p += faucet(deck_y)
    if wall:
        p.append(
            part("wall_plate", (0.55, 0.4, 0.05), (0, 0.24, -0.475), "primary_ceramic")
        )
    else:
        p += [
            part(
                "pedestal",
                (0.3, 0.66, 0.36),
                (0, 0.33, -0.08),
                "primary_ceramic",
                radius=0.05,
            ),
            part(
                "foot",
                (0.42, 0.055, 0.50),
                (0, 0.0275, -0.08),
                "primary_ceramic",
                shape="ellipsoid",
            ),
        ]
    return p


def toilet(placement: Placement) -> list[FurniturePart]:
    wall = placement == "wall"
    bowl_h, bowl_y, seat_y = (0.55, 0.66, 0.97) if wall else (0.28, 0.38, 0.5525)
    p = [
        basin("bowl", (0.92, bowl_h, 0.78), (0, bowl_y, 0.1)),
        part(
            "seat",
            (1, 0.045 if not wall else 0.06, 0.86),
            (0, seat_y, 0.07),
            "primary_ceramic",
            shape="tube",
            wall_thickness=0.13,
        ),
        part(
            "hinges",
            (0.67, 0.035, 0.07),
            (0, seat_y - 0.015, -0.28),
            "chrome",
            collision=False,
        ),
    ]
    if wall:
        p += [
            part(
                "underside",
                (0.70, 0.5, 0.58),
                (0, 0.25, 0.08),
                "primary_ceramic",
                shape="ellipsoid",
            ),
            part("wall_plate", (0.72, 0.48, 0.22), (0, 0.42, -0.39), "primary_ceramic"),
        ]
    else:
        p += [
            part(
                "base",
                (0.50, 0.26, 0.60),
                (0, 0.13, 0.06),
                "primary_ceramic",
                radius=0.06,
            ),
            part(
                "cistern",
                (0.84, 0.50, 0.22),
                (0, 0.72, -0.39),
                "primary_ceramic",
                radius=0.04,
            ),
            part(
                "cistern_lid",
                (0.89, 0.06, 0.24),
                (0, 0.97, -0.38),
                "primary_ceramic",
                radius=0.025,
            ),
            part(
                "flush_button",
                (0.14, 0.012, 0.10),
                (0.20, 0.994, -0.38),
                "chrome",
                shape="cylinder",
                collision=False,
            ),
        ]
    return p


def bathtub() -> Model:
    p = [
        basin("tub", (1, 1, 1), (0, 0.5, 0)),
        part(
            "drain",
            (0.06, 0.008, 0.11),
            (0.14, 0.184, 0),
            "chrome",
            shape="cylinder",
            collision=False,
        ),
    ]
    return p, []


def vanity(placement: Placement) -> list[FurniturePart]:
    p, _ = base_cabinet(top_y=0.64, worktop=True)
    if placement == "wall":
        p = [
            replace(piece, size=(0.86, 0.12, 0.82), at=(0, 0.06, -0.02))
            if piece.name == "plinth"
            else piece
            for piece in p
        ]
        p.append(part("wall_plate", (0.82, 0.5, 0.05), (0, 0.32, -0.475), "wood"))
    p += [basin("bowl", (0.74, 0.20, 0.72), (0, 0.74, 0.06))]
    p += faucet(0.64)
    return p


def shower() -> Model:
    p = [
        part("tray", (1, 0.024, 1), (0, 0.012, 0), "primary_ceramic"),
        part("wet_floor", (0.94, 0.008, 0.94), (0, 0.028, 0), "enamel"),
        part(
            "drain",
            (0.12, 0.006, 0.12),
            (0.25, 0.035, -0.25),
            "chrome",
            collision=False,
        ),
    ]
    p += frame(
        "tray_rim", 0.042, 0.028, hole_x=0.90, hole_z=0.90, material="primary_ceramic"
    )
    for x in (-0.485, 0.485):
        for z in (-0.485, 0.485):
            p.append(part(f"post_{x}_{z}", (0.03, 0.97, 0.03), (x, 0.515, z), "chrome"))
    p += frame("upper_frame", 0.985, 0.03, hole_x=0.94, hole_z=0.94, material="chrome")
    p += [
        part("back_glass", (0.94, 0.92, 0.008), (0, 0.51, -0.475), "clear_glass"),
        part("left_glass", (0.008, 0.92, 0.94), (-0.475, 0.51, 0), "clear_glass"),
        part("right_glass", (0.008, 0.92, 0.94), (0.475, 0.51, 0), "clear_glass"),
        part("fixed_glass", (0.46, 0.92, 0.008), (-0.235, 0.51, 0.475), "clear_glass"),
        part("door_glass", (0.46, 0.92, 0.008), (0.235, 0.51, 0.475), "clear_glass"),
        part("door_pull", (0.025, 0.12, 0.02), (0.055, 0.51, 0.49), "chrome"),
        rod("riser", (0, 0.14, -0.42), (0, 0.90, -0.42), 0.015, "chrome"),
        rod("overhead_arm", (0, 0.90, -0.42), (0, 0.90, -0.12), 0.014, "chrome"),
        part(
            "shower_head",
            (0.23, 0.018, 0.23),
            (0, 0.89, -0.12),
            "chrome",
            shape="cylinder",
        ),
        part("mixer", (0.24, 0.025, 0.07), (0, 0.32, -0.42), "chrome"),
    ]
    return p, []


def towel_rack() -> Model:
    p = []
    for x in (-0.43, 0.43):
        p.append(
            part(
                f"upright_{x}",
                (0.04, 1, 0.06),
                (x, 0.5, 0.4),
                "primary_metal",
                shape="cylinder",
            )
        )
        for y in (0.15, 0.85):
            p += [
                disc(
                    f"wall_plate_{x}_{y}", (0.09, 0.13, 0.06), (x, y, -0.47), "chrome"
                ),
                rod(f"stand_off_{x}_{y}", (x, y, -0.44), (x, y, 0.4), 0.018, "chrome"),
            ]
    for y in (0.1, 0.5, 0.9):
        p.append(
            rod(f"rail_{y}", (-0.43, y, 0.4), (0.43, y, 0.4), 0.022, "primary_metal")
        )
    return p, []


def water_heater() -> Model:
    p = [
        part("tank", (0.85, 0.76, 0.85), (0, 0.57, 0), shape="cylinder"),
        part("top_cap", (0.85, 0.10, 0.85), (0, 0.95, 0), shape="ellipsoid"),
        part("bottom_cap", (0.85, 0.10, 0.85), (0, 0.20, 0), shape="ellipsoid"),
        part("wall_plate", (0.50, 0.6, 0.05), (0, 0.58, -0.475), "metal"),
        part("controls", (0.33, 0.14, 0.03), (0, 0.25, 0.43), "primary_dark"),
        disc(
            "thermostat",
            (0.09, 0.06, 0.02),
            (0, 0.25, 0.455),
            "chrome",
            collision=False,
        ),
    ]
    for x in (-0.15, 0.15):
        p.append(
            part(
                f"pipe_{x}",
                (0.07, 0.15, 0.07),
                (x, 0.075, 0),
                "chrome",
                shape="cylinder",
            )
        )
    return p, []


def drying_rack() -> Model:
    p = []
    for x in (-0.4, 0.4):
        p += [
            rod(f"leg_a_{x}", (x, 0.025, -0.43), (x, 0.76, 0.38), 0.012, "chrome"),
            rod(f"leg_b_{x}", (x, 0.025, 0.43), (x, 0.76, -0.38), 0.012, "chrome"),
        ]
        for z in (-0.43, 0.43):
            p.append(
                part(
                    f"foot_{x}_{z}",
                    (0.05, 0.05, 0.05),
                    (x, 0.025, z),
                    "primary_dark",
                    shape="ellipsoid",
                )
            )
    for x in (-0.4, 0.4):
        p.append(rod(f"deck_{x}", (x, 0.76, -0.46), (x, 0.76, 0.46), 0.012))
    for z in (-0.46, 0.46):
        p.append(rod(f"deck_edge_{z}", (-0.4, 0.76, z), (0.4, 0.76, z), 0.012))
    for x in (-0.25, -0.125, 0, 0.125, 0.25):
        p.append(rod(f"line_{x}", (x, 0.76, -0.46), (x, 0.76, 0.46), 0.006, "chrome"))
    for side in (-1, 1):
        for z in (-0.46, 0.46):
            p.append(
                rod(
                    f"wing_{side}_{z}",
                    (side * 0.34, 0.76, z),
                    (side * 0.46, 0.97, z),
                    0.010,
                )
            )
        for x in (0.37, 0.41, 0.45):
            y = 0.76 + (x - 0.34) / 0.12 * 0.21
            p.append(
                rod(
                    f"wing_line_{side}_{x}",
                    (side * x, y, -0.46),
                    (side * x, y, 0.46),
                    0.006,
                    "chrome",
                )
            )
        p.append(
            part(
                f"wing_cap_{side}",
                (0.035, 0.05, 0.94),
                (side * 0.46, 0.975, 0),
                shape="rounded_box",
            )
        )
    p.append(rod("brace", (-0.4, 0.42, 0), (0.4, 0.42, 0), 0.012))
    return p, []


_assets: dict[str, FurnitureAsset] = {}


def register(
    object_type: ObjectType,
    label: str,
    size: Point,
    color: str,
    category: Category,
    model: Model,
    *,
    asset_id: str | None = None,
    dimensions: tuple[tuple[Placement, Point], ...] = (),
) -> None:
    parts, surfaces = model
    asset_id = asset_id or f"{object_type}_standard_01"
    _assets[asset_id] = FurnitureAsset(
        asset_id,
        object_type,
        label,
        size,
        color,
        tuple(parts),
        tuple(surfaces),
        category,
        dimensions,
    )


register(
    "kitchen_cabinet",
    "Base kitchen cabinet",
    (0.6, 0.9, 0.58),
    "#A8B09D",
    "kitchen",
    base_cabinet(),
)
register(
    "kitchen_cabinet",
    "Built-in oven cabinet",
    (0.6, 0.9, 0.58),
    "#A8B09D",
    "kitchen",
    base_cabinet(oven=True),
    asset_id="kitchen_cabinet_oven_01",
)
register(
    "kitchen_counter",
    "Worktop counter",
    (0.6, 0.92, 0.62),
    "#A8B09D",
    "kitchen",
    base_cabinet(worktop=True),
)
register(
    "kitchen_counter",
    "Kitchen island",
    (1.4, 0.92, 0.85),
    "#A8B09D",
    "kitchen",
    base_cabinet(worktop=True, island=True),
    asset_id="kitchen_counter_island_01",
)
register(
    "kitchen_counter",
    "Sink counter",
    (1.2, 1.24, 0.62),
    "#A8B09D",
    "kitchen",
    sink_counter(),
    asset_id="kitchen_counter_sink_01",
)
register(
    "kitchen_counter",
    "Hob counter",
    (0.6, 0.94, 0.62),
    "#A8B09D",
    "kitchen",
    hob_counter(),
    asset_id="kitchen_counter_hob_01",
)
register(
    "wall_cabinet",
    "Double wall cabinet",
    (0.8, 0.7, 0.34),
    "#A8B09D",
    "kitchen",
    wall_cabinet(),
)
register(
    "extractor_hood",
    "Chimney extractor hood",
    (0.6, 0.72, 0.48),
    "#B3B9B3",
    "kitchen",
    hood(),
)
register(
    "refrigerator",
    "Fridge freezer",
    (0.65, 1.85, 0.67),
    "#BCC3BC",
    "kitchen",
    refrigerator(),
)
register(
    "cooker", "Freestanding cooker", (0.6, 0.92, 0.6), "#BCC3BC", "kitchen", cooker()
)
register(
    "dishwasher", "Dishwasher", (0.6, 0.85, 0.6), "#BCC3BC", "kitchen", dishwasher()
)
register(
    "washing_machine",
    "Front-load washing machine",
    (0.6, 0.85, 0.62),
    "#C5C9C1",
    "utility",
    (laundry_machine(), []),
)
register(
    "washing_machine",
    "Washer and dryer stack",
    (0.6, 1.72, 0.64),
    "#C5C9C1",
    "utility",
    laundry_stack(),
    asset_id="washing_machine_stack_01",
)
register(
    "dryer",
    "Front-load dryer",
    (0.6, 0.85, 0.62),
    "#C5C9C1",
    "utility",
    (laundry_machine(dryer=True), []),
)
register(
    "microwave",
    "Countertop microwave",
    (0.48, 0.29, 0.38),
    "#BCC3BC",
    "kitchen",
    microwave(),
)
register("kettle", "Electric kettle", (0.25, 0.27, 0.2), "#A8B09D", "kitchen", kettle())
register(
    "toaster", "Two-slot toaster", (0.30, 0.20, 0.18), "#A8B09D", "kitchen", toaster()
)
register(
    "sink",
    "Ceramic washbasin",
    (0.6, 1.08, 0.48),
    "#D4D8CF",
    "bathroom",
    (list(configurations((("floor", sink("floor")), ("wall", sink("wall"))))), []),
    dimensions=(("wall", (0.6, 0.40, 0.48)),),
)
register(
    "toilet",
    "Toilet",
    (0.39, 0.8, 0.7),
    "#D4D8CF",
    "bathroom",
    (list(configurations((("floor", toilet("floor")), ("wall", toilet("wall"))))), []),
    dimensions=(("wall", (0.39, 0.40, 0.6)),),
)
register(
    "bathtub",
    "Freestanding bathtub",
    (1.7, 0.62, 0.78),
    "#D4D8CF",
    "bathroom",
    bathtub(),
)
register(
    "shower", "Glass shower enclosure", (0.9, 2.1, 0.9), "#D4D8CF", "bathroom", shower()
)
register(
    "vanity",
    "Vessel basin vanity",
    (0.8, 1.1, 0.5),
    "#A8B09D",
    "bathroom",
    (list(configurations((("floor", vanity("floor")), ("wall", vanity("wall"))))), []),
    dimensions=(("wall", (0.8, 0.85, 0.5)),),
)
register(
    "towel_rack",
    "Wall towel rack",
    (0.6, 0.35, 0.15),
    "#BCC3BC",
    "bathroom",
    towel_rack(),
)
register(
    "water_heater",
    "Wall water heater",
    (0.43, 0.85, 0.43),
    "#C5C9C1",
    "utility",
    water_heater(),
)
register(
    "drying_rack",
    "Folding drying rack",
    (1.45, 1.0, 0.58),
    "#BCC3BC",
    "utility",
    drying_rack(),
)

FIXTURE_CATALOG = MappingProxyType(_assets)
