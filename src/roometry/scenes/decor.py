"""Finishing models for every remaining approved asset ID.

Artwork is authored from geometric shapes, with no external textures or fonts.
Plants, framed pictures and mirrors have separate mounting configurations. All
details use the common recipe format; the existing scene checker is unchanged.
"""

from dataclasses import replace
from math import cos, pi, sin
from types import MappingProxyType

from roometry.scenes.assets import ObjectType, Placement
from roometry.scenes.equipment import configurations, disc, rod
from roometry.scenes.fixture_models import BASIN_PROFILE
from roometry.scenes.furniture import (
    FurnitureAsset,
    FurniturePart,
    MaterialRole,
    Point,
    part,
)

PICTURE_PLACEMENTS: tuple[Placement, ...] = ("surface", "wall")
STANDING_PLACEMENTS: tuple[Placement, ...] = ("floor", "wall")
PLANT_PLACEMENTS: tuple[Placement, ...] = ("floor", "surface")


def rug() -> list[FurniturePart]:
    p = [part("woven_body", (1, 0.8, 1), (0, 0.4, 0), radius=0.025)]
    for side in (-1, 1):
        p += [
            part(
                f"long_border_{side}",
                (0.035, 0.12, 0.92),
                (side * 0.458, 0.86, 0),
                "primary_dark",
                collision=False,
            ),
            part(
                f"end_border_{side}",
                (0.95, 0.12, 0.03),
                (0, 0.86, side * 0.46),
                "primary_dark",
                collision=False,
            ),
            part(
                f"inner_long_seam_{side}",
                (0.008, 0.08, 0.84),
                (side * 0.42, 0.94, 0),
                "linen",
                collision=False,
            ),
            part(
                f"inner_end_seam_{side}",
                (0.84, 0.08, 0.008),
                (0, 0.94, side * 0.42),
                "linen",
                collision=False,
            ),
        ]
    for i, x in enumerate((-0.35, -0.21, -0.07, 0.07, 0.21, 0.35)):
        for side in (-1, 1):
            p.append(
                part(
                    f"stitch_{i}_{side}",
                    (0.055, 0.04, 0.004),
                    (x, 0.98, side * 0.438),
                    "linen",
                    collision=False,
                )
            )
    return p


def artwork(
    style: str, z: float, *, height: float, bottom: float
) -> list[FurniturePart]:
    def at(x: float, y: float, offset: float = 0) -> Point:
        return x, bottom + y * height, z + offset

    if style == "portrait":
        return [
            disc(
                "arch_top",
                (0.50, 0.50 * height, 0.008),
                at(0, 0.58),
                "primary_dark",
                collision=False,
            ),
            part(
                "arch_base",
                (0.50, 0.40 * height, 0.008),
                at(0, 0.38),
                "primary_dark",
                collision=False,
            ),
            disc(
                "arch_inner",
                (0.27, 0.29 * height, 0.01),
                at(0, 0.57, 0.006),
                "paper",
                collision=False,
            ),
            part(
                "arch_inner_base",
                (0.27, 0.31 * height, 0.01),
                at(0, 0.43, 0.006),
                "paper",
                collision=False,
            ),
            disc(
                "sun",
                (0.14, 0.14 * height, 0.008),
                at(0.26, 0.81),
                "wood",
                collision=False,
            ),
        ]
    if style == "landscape":
        return [
            disc(
                "sun",
                (0.18, 0.24 * height, 0.008),
                at(0.22, 0.70),
                "wood",
                collision=False,
            ),
            disc(
                "far_hill",
                (0.72, 0.30 * height, 0.01),
                at(-0.05, 0.39),
                "primary",
                collision=False,
            ),
            part(
                "ground",
                (0.74, 0.12 * height, 0.01),
                at(0, 0.28, 0.003),
                "primary",
                collision=False,
            ),
            disc(
                "near_hill",
                (0.48, 0.22 * height, 0.01),
                at(0.14, 0.32, 0.012),
                "primary_dark",
                collision=False,
            ),
            part(
                "foreground",
                (0.74, 0.12 * height, 0.01),
                at(0, 0.22, 0.018),
                "primary_dark",
                collision=False,
            ),
        ]
    return [
        part(
            "color_block",
            (0.28, 0.56 * height, 0.008),
            at(-0.20, 0.47),
            "primary_dark",
            collision=False,
        ),
        disc(
            "circle",
            (0.40, 0.40 * height, 0.01),
            at(0.12, 0.60, 0.006),
            "wood",
            collision=False,
        ),
        part(
            "underline",
            (0.58, 0.035 * height, 0.01),
            at(0, 0.23, 0.012),
            "primary",
            collision=False,
        ),
    ]


def picture(style: str, placement: Placement) -> list[FurniturePart]:
    bottom, height, z = (0, 1, -0.41) if placement == "wall" else (0.08, 0.92, 0.31)
    p = [part("backing", (1, height, 0.18), (0, bottom + height / 2, z), "wood")]
    for x in (-0.47, 0.47):
        p.append(
            part(
                f"frame_side_{x}",
                (0.06, height, 0.08),
                (x, bottom + height / 2, z + 0.11),
                "primary_metal",
            )
        )
    for y in (bottom + 0.03 * height, bottom + 0.97 * height):
        p.append(
            part(
                f"frame_end_{y}",
                (0.88, 0.06 * height, 0.08),
                (0, y, z + 0.11),
                "primary_metal",
            )
        )
    p += [
        part(
            "mat",
            (0.88, 0.88 * height, 0.02),
            (0, bottom + height / 2, z + 0.095),
            "linen",
            collision=False,
        ),
        part(
            "canvas",
            (0.76, 0.76 * height, 0.012),
            (0, bottom + height / 2, z + 0.113),
            "paper",
            collision=False,
        ),
        *artwork(style, z + 0.127, height=height, bottom=bottom),
    ]
    if placement == "surface":
        p += [
            part("easel_foot", (0.65, 0.035, 0.94), (0, 0.0175, -0.02), "primary_dark"),
            part("frame_rest", (0.35, 0.08, 0.10), (0, 0.04, 0.31), "primary_metal"),
            rod("easel_leg", (0, 0.78, 0.23), (0, 0.025, -0.43), 0.022, "wood"),
        ]
    return p


def clock() -> list[FurniturePart]:
    p = [
        disc("case", (1, 1, 0.80), (0, 0.5, -0.10), "primary"),
        disc("dial", (0.87, 0.87, 0.035), (0, 0.5, 0.32), "paper", collision=False),
        part(
            "bezel",
            (1, 1, 0.12),
            (0, 0.5, 0.37),
            "primary_metal",
            shape="torus",
            radius=0.035,
        ),
    ]
    for i in range(12):
        angle = i * pi / 6
        p.append(
            part(
                f"tick_{i}",
                (0.024, 0.055, 0.018),
                (sin(angle) * 0.36, 0.5 + cos(angle) * 0.36, 0.35),
                "inset",
                rotation=(0, 0, -angle),
                collision=False,
            )
        )
    for name, angle, length in (("hour", -pi / 3, 0.25), ("minute", pi / 3, 0.34)):
        p.append(
            part(
                name,
                (0.025, length, 0.025),
                (sin(angle) * length / 2, 0.5 + cos(angle) * length / 2, 0.39),
                "inset",
                rotation=(0, 0, -angle),
                collision=False,
            )
        )
    p.append(
        disc(
            "hub",
            (0.065, 0.065, 0.04),
            (0, 0.5, 0.43),
            "primary_metal",
            collision=False,
        )
    )
    return p


def curtains(*, closed: bool = False) -> list[FurniturePart]:
    p = [
        rod("curtain_rod", (-0.47, 0.965, -0.17), (0.47, 0.965, -0.17), 0.018, "metal")
    ]
    for side in (-1, 1):
        p += [
            part(
                f"wall_bracket_{side}",
                (0.065, 0.07, 0.34),
                (side * 0.44, 0.95, -0.33),
                "metal",
            ),
            part(
                f"finial_{side}",
                (0.055, 0.055, 0.10),
                (side * 0.4725, 0.965, -0.17),
                "primary_metal",
                shape="ellipsoid",
            ),
        ]
    panels = ((0, 0.90),) if closed else ((-0.34, 0.24), (0.34, 0.24))
    for i, (x, width) in enumerate(panels):
        p.append(
            part(
                f"fabric_{i}",
                (width, 0.92, 0.64),
                (x, 0.46, 0.12),
                shape="pleated_panel",
            )
        )
        # Top loops bridge each panel to the rod; the hem follows the same folds.
        for j in range(5):
            p.append(
                part(
                    f"loop_{i}_{j}",
                    (0.018, 0.065, 0.055),
                    (x + width * (j / 5 - 0.4), 0.935, -0.16),
                    "primary_dark",
                    shape="torus",
                    rotation=(0, pi / 2, 0),
                    radius=0.12,
                    collision=False,
                )
            )
    return p


def blind() -> list[FurniturePart]:
    p = [
        part("headrail", (1, 0.08, 0.60), (0, 0.96, -0.20)),
        part("bottom_rail", (0.94, 0.04, 0.4), (0, 0.02, 0), "primary_dark"),
    ]
    for i in range(22):
        p.append(
            part(
                f"slat_{i}",
                (0.93, 0.026, 0.38),
                (0, 0.073 + i * 0.0385, -0.01),
                rotation=(0.16, 0, 0),
            )
        )
    for x in (-0.30, 0.30):
        p.append(
            rod(
                f"tape_{x}",
                (x, 0.04, 0.19),
                (x, 0.92, 0.19),
                0.006,
                "linen",
                collision=False,
            )
        )
    p += [
        rod(
            "pull_cord",
            (0.47, 0.14, 0.24),
            (0.47, 0.91, 0.24),
            0.004,
            "linen",
            collision=False,
        ),
        part(
            "pull",
            (0.035, 0.065, 0.045),
            (0.47, 0.11, 0.24),
            "wood",
            shape="ellipsoid",
            collision=False,
        ),
    ]
    return p


def mirror(placement: Placement) -> list[FurniturePart]:
    if placement == "wall":
        return [
            disc("back", (0.94, 0.94, 0.02), (0, 0.5, -0.49), "primary_dark"),
            part(
                "round_frame",
                (1, 1, 0.20),
                (0, 0.5, -0.30),
                "primary_metal",
                shape="torus",
                radius=0.035,
            ),
            disc(
                "glass", (0.86, 0.86, 0.02), (0, 0.5, -0.23), "mirror", collision=False
            ),
            part(
                "reflection",
                (0.035, 0.45, 0.006),
                (-0.12, 0.58, -0.215),
                "clear_glass",
                rotation=(0, 0, -0.4),
                collision=False,
            ),
        ]
    p = [
        part(
            "frame", (0.94, 0.94, 0.16), (0, 0.53, 0.21), "primary_metal", radius=0.035
        ),
        part(
            "glass",
            (0.82, 0.89, 0.016),
            (0, 0.53, 0.30),
            "mirror",
            radius=0.025,
            collision=False,
        ),
        part(
            "reflection",
            (0.035, 0.65, 0.008),
            (-0.10, 0.57, 0.314),
            "clear_glass",
            rotation=(0, 0, -0.3),
            collision=False,
        ),
        part("foot", (0.74, 0.04, 1), (0, 0.02, 0), "primary_dark"),
    ]
    for side in (-1, 1):
        p += [
            rod(
                f"kickstand_{side}",
                (side * 0.28, 0.76, 0.15),
                (side * 0.28, 0.025, -0.45),
                0.018,
            ),
            rod(
                f"front_leg_{side}",
                (side * 0.28, 0.025, 0.35),
                (side * 0.28, 0.14, 0.21),
                0.018,
            ),
        ]
    return p


def plant(placement: Placement) -> list[FurniturePart]:
    p = [
        part(
            "pot",
            (0.62, 0.30, 0.62),
            (0, 0.15, 0),
            "primary_ceramic",
            shape="tube",
            bottom_scale=0.72,
            wall_thickness=0.045,
        ),
        part(
            "pot_bottom",
            (0.45, 0.035, 0.45),
            (0, 0.0175, 0),
            "primary_ceramic",
            shape="cylinder",
        ),
        part(
            "soil",
            (0.55, 0.025, 0.55),
            (0, 0.255, 0),
            "soil",
            shape="cylinder",
            collision=False,
        ),
    ]
    if placement == "floor":
        p.append(rod("trunk", (0, 0.25, 0), (0.015, 0.85, 0), 0.018, "wood"))
        levels = ((0.48, 0.24), (0.63, 0.23), (0.78, 0.20), (0.84, 0.13))
    else:
        levels = ((0.43, 0.23), (0.60, 0.24), (0.76, 0.18), (0.84, 0.12))
    for level, (y, reach) in enumerate(levels):
        for j in range(4):
            angle = j * pi / 2 + level * 0.7
            x, z = cos(angle) * reach, sin(angle) * reach
            p.append(
                rod(
                    f"stem_{level}_{j}",
                    (0, 0.25 if placement == "surface" else y - 0.12, 0),
                    (x * 0.8, y, z * 0.8),
                    0.007,
                    "foliage_dark",
                    collision=False,
                )
            )
            p.append(
                part(
                    f"leaf_{level}_{j}",
                    (0.17, 0.22 if level < 3 else 0.19, 0.045),
                    (x, y + 0.025, z),
                    "foliage" if j % 2 else "foliage_dark",
                    shape="ellipsoid",
                    rotation=(0.35 * sin(angle), angle, -0.55 * cos(angle)),
                    collision=False,
                )
            )
    return p


VASE_PROFILE = (
    (0, 0),
    (0.28, 0),
    (0.33, 0.04),
    (0.44, 0.25),
    (0.50, 0.50),
    (0.46, 0.68),
    (0.28, 0.82),
    (0.22, 0.96),
    (0.22, 1),
    (0.185, 1),
    (0.185, 0.93),
    (0.245, 0.81),
    (0.42, 0.66),
    (0.46, 0.50),
    (0.40, 0.27),
    (0.29, 0.09),
    (0, 0.09),
)


def vase() -> list[FurniturePart]:
    return [
        part(
            "ceramic_body",
            (1, 1, 1),
            (0, 0.5, 0),
            "primary_ceramic",
            shape="basin",
            profile=VASE_PROFILE,
        )
    ]


def books() -> list[FurniturePart]:
    p = []
    specs: tuple[tuple[float, float, float, float, float, MaterialRole], ...] = (
        (1, 0.30, 0.96, 0, 0, "primary"),
        (0.88, 0.34, 0.91, -0.035, 0.32, "wood"),
        (0.92, 0.32, 0.86, 0.035, 0.68, "primary_dark"),
    )
    for i, (width, height, depth, x, bottom, finish) in enumerate(specs):
        for side in (0, 1):
            p.append(
                part(
                    f"cover_{i}_{side}",
                    (width, 0.025, depth),
                    (x, bottom + 0.0125 + side * (height - 0.025), 0),
                    finish,
                )
            )
        p += [
            part(
                f"pages_{i}",
                (width - 0.035, height - 0.05, depth - 0.045),
                (x, bottom + height / 2, -0.01),
                "paper",
            ),
            part(
                f"spine_{i}",
                (width, height, 0.035),
                (x, bottom + height / 2, depth / 2 - 0.0175),
                finish,
            ),
        ]
        for j in range(3):
            p.append(
                part(
                    f"spine_mark_{i}_{j}",
                    (0.09, 0.015, 0.006),
                    (
                        x - width * 0.31 + j * 0.14,
                        bottom + height * 0.55,
                        depth / 2 + 0.003,
                    ),
                    "linen",
                    collision=False,
                )
            )
    return p


def souvenir(style: str) -> list[FurniturePart]:
    p = [part("plinth", (0.78, 0.12, 0.78), (0, 0.06, 0), "primary_dark")]
    if style == "sculpture":
        p.append(
            part(
                "loop",
                (0.76, 0.88, 0.32),
                (0, 0.56, 0),
                "primary_ceramic",
                shape="torus",
                radius=0.13,
            )
        )
    elif style == "orb":
        p += [
            part(
                "orb",
                (0.65, 0.65, 0.65),
                (0, 0.55, 0),
                "primary_ceramic",
                shape="ellipsoid",
            ),
            part(
                "orbit",
                (0.95, 0.88, 0.08),
                (0, 0.56, 0),
                "primary_metal",
                shape="torus",
                radius=0.025,
            ),
            rod("stem", (0, 0.12, 0), (0, 0.30, 0), 0.035),
        ]
    elif style == "bird":
        p += [
            part(
                "body",
                (0.67, 0.42, 0.62),
                (-0.06, 0.53, 0),
                "primary_ceramic",
                shape="ellipsoid",
            ),
            part(
                "head",
                (0.29, 0.30, 0.32),
                (0.19, 0.80, 0.02),
                "primary_ceramic",
                shape="ellipsoid",
            ),
            rod("beak", (0.29, 0.81, 0.03), (0.46, 0.79, 0.03), 0.025, "wood"),
            part(
                "tail",
                (0.32, 0.10, 0.40),
                (-0.31, 0.59, -0.03),
                "primary_dark",
                rotation=(0, 0, -0.35),
                shape="ellipsoid",
            ),
            part(
                "eye",
                (0.025, 0.025, 0.025),
                (0.25, 0.84, 0.16),
                "inset",
                shape="ellipsoid",
                collision=False,
            ),
        ]
        for z in (-0.12, 0.12):
            p.append(
                rod(
                    f"leg_{z}",
                    (-0.02, 0.12, z),
                    (-0.02, 0.40, z),
                    0.018,
                    "primary_metal",
                )
            )
    else:
        p += [
            rod("stem", (0, 0.12, 0), (0, 0.49, 0), 0.065),
            part(
                "cup",
                (0.64, 0.51, 0.64),
                (0, 0.745, 0),
                "primary_metal",
                shape="basin",
                profile=BASIN_PROFILE,
            ),
            part(
                "plaque",
                (0.40, 0.06, 0.012),
                (0, 0.065, 0.395),
                "paper",
                collision=False,
            ),
        ]
        for side in (-1, 1):
            p.append(
                part(
                    f"handle_{side}",
                    (0.32, 0.36, 0.10),
                    (side * 0.33, 0.74, 0),
                    "primary_metal",
                    shape="torus",
                    radius=0.09,
                )
            )
    return p


def tray() -> list[FurniturePart]:
    p = [part("tray_base", (1, 0.10, 1), (0, 0.05, 0), "wood")]
    for x in (-0.47, 0.47):
        p.append(part(f"side_{x}", (0.06, 0.12, 1), (x, 0.13, 0), "wood"))
    for z in (-0.47, 0.47):
        p.append(part(f"end_{z}", (0.88, 0.12, 0.06), (0, 0.13, z), "wood"))
    for i, (x, height, width) in enumerate(((-0.19, 0.76, 0.24), (0.17, 0.52, 0.22))):
        p += [
            part(
                f"candle_{i}",
                (width, height, width * 1.4),
                (x, 0.10 + height / 2, 0.02),
                "primary_ceramic",
                shape="cylinder",
            ),
            part(
                f"wax_{i}",
                (width * 0.86, 0.012, width * 1.2),
                (x, 0.105 + height, 0.02),
                "linen",
                shape="cylinder",
                collision=False,
            ),
            rod(
                f"wick_{i}",
                (x, 0.11 + height, 0.02),
                (x, 0.14 + height, 0.02),
                0.008,
                "inset",
                collision=False,
            ),
        ]
    return p


def open_cup(*, height: float = 0.60) -> list[FurniturePart]:
    return [
        part(
            "cup",
            (0.84, height, 0.84),
            (0, height / 2, 0),
            "primary_ceramic",
            shape="tube",
            wall_thickness=0.055,
            bottom_scale=0.85,
        ),
        part(
            "cup_bottom",
            (0.72, 0.035, 0.72),
            (0, 0.0175, 0),
            "primary_ceramic",
            shape="cylinder",
        ),
    ]


def utensils() -> list[FurniturePart]:
    p = open_cup()
    p += [
        rod("spoon_handle", (-0.18, 0.15, 0.05), (-0.18, 0.85, 0.05), 0.024, "wood"),
        part(
            "spoon_bowl",
            (0.25, 0.23, 0.10),
            (-0.18, 0.88, 0.05),
            "wood",
            shape="ellipsoid",
        ),
        rod("spatula_handle", (0.17, 0.15, -0.03), (0.17, 0.84, -0.03), 0.024, "wood"),
        part("spatula_head", (0.28, 0.22, 0.07), (0.17, 0.89, -0.03), "wood"),
    ]
    for x in (0.10, 0.17, 0.24):
        p.append(
            part(
                f"spatula_slot_{x}",
                (0.025, 0.12, 0.008),
                (x, 0.90, 0.009),
                "inset",
                collision=False,
            )
        )
    return p


def soap() -> list[FurniturePart]:
    return [
        part(
            "bottle", (0.90, 0.71, 0.90), (0, 0.355, 0), "primary_ceramic", radius=0.07
        ),
        part(
            "neck", (0.34, 0.12, 0.34), (0, 0.77, 0), "primary_metal", shape="cylinder"
        ),
        rod("pump_stem", (0, 0.82, 0), (0, 0.95, 0), 0.045, "chrome"),
        part("pump", (0.62, 0.07, 0.23), (-0.12, 0.965, 0), "chrome"),
        part("outlet", (0.10, 0.09, 0.15), (-0.37, 0.90, 0), "chrome"),
        part("label", (0.49, 0.23, 0.012), (0, 0.38, 0.455), "linen", collision=False),
    ]


def toothbrushes() -> list[FurniturePart]:
    p = open_cup(height=0.65)
    for i, (x, z) in enumerate(((-0.17, 0.06), (0.17, -0.08))):
        p += [
            part(
                f"handle_{i}",
                (0.075, 0.70, 0.10),
                (x, 0.52, z),
                "wood" if i else "primary_dark",
            ),
            part(f"head_{i}", (0.12, 0.15, 0.14), (x, 0.925, z)),
            part(
                f"bristles_{i}",
                (0.09, 0.12, 0.09),
                (x, 0.925, z + 0.095),
                "paper",
                collision=False,
            ),
        ]
    return p


def caddy() -> list[FurniturePart]:
    p = [part("base", (1, 0.06, 1), (0, 0.03, 0))]
    for x in (-0.465, 0, 0.465):
        p.append(part(f"divider_{x}", (0.07, 0.48, 1), (x, 0.30, 0)))
    for z in (-0.465, 0.465):
        p.append(part(f"end_{z}", (0.86, 0.48, 0.07), (0, 0.30, z)))
    for z in (-0.34, 0.34):
        p.append(rod(f"handle_post_{z}", (0, 0.50, z), (0, 0.965, z), 0.025, "wood"))
    p.append(rod("handle", (0, 0.965, -0.36), (0, 0.965, 0.36), 0.035, "wood"))
    return p


def toilet_brush() -> list[FurniturePart]:
    return [
        *open_cup(height=0.46),
        part(
            "brush_head", (0.42, 0.24, 0.42), (0, 0.24, 0), "inset", shape="ellipsoid"
        ),
        rod("handle", (0, 0.28, 0), (0, 0.95, 0), 0.04, "chrome"),
        part(
            "grip", (0.15, 0.22, 0.15), (0, 0.89, 0), "primary_dark", shape="cylinder"
        ),
    ]


def paper_holder() -> list[FurniturePart]:
    return [
        part("wall_plate", (0.26, 0.65, 0.10), (-0.34, 0.50, -0.45), "primary_metal"),
        rod("arm", (-0.34, 0.55, -0.40), (-0.34, 0.55, 0.08), 0.035),
        rod("spindle", (-0.37, 0.55, 0.08), (0.40, 0.55, 0.08), 0.03, "chrome"),
        part(
            "paper_roll",
            (0.72, 0.65, 0.72),
            (0, 0.55, 0.08),
            "paper",
            shape="tube",
            rotation=(0, 0, pi / 2),
            wall_thickness=0.35,
        ),
        part(
            "paper_tail",
            (0.56, 0.30, 0.018),
            (0, 0.255, 0.443),
            "paper",
            collision=False,
        ),
    ]


def round_rug() -> list[FurniturePart]:
    return [
        part("woven_body", (1, .8, 1), (0, .4, 0), shape="cylinder"),
        part("border", (.94, .94, .10), (0, .86, 0), "primary_dark", shape="torus", radius=.025, rotation=(pi / 2, 0, 0), collision=False),
        part("seam", (.84, .84, .06), (0, .95, 0), "linen", shape="torus", radius=.006, rotation=(pi / 2, 0, 0), collision=False),
    ]


def arched_mirror(placement: Placement) -> list[FurniturePart]:
    if placement == "wall":
        return [
            part("back", (1, 1, .10), (0, .5, -.45), "primary_dark", shape="arched_panel"),
            part("frame", (1, 1, .16), (0, .5, -.32), "primary_metal", shape="arched_panel"),
            part("glass", (.88, .94, .02), (0, .5, -.225), "mirror", shape="arched_panel", collision=False),
            part("reflection", (.03, .6, .006), (-.10, .51, -.21), "clear_glass", rotation=(0, 0, -.3), collision=False),
        ]
    return [replace(p, shape="arched_panel") if p.name in {"frame", "glass"} else p for p in mirror(placement)]


def palm(placement: Placement) -> list[FurniturePart]:
    p = [piece for piece in plant(placement) if piece.name in {"pot", "pot_bottom", "soil"}]
    for j in range(7):
        angle = j * 2 * pi / 7
        height = .86 - (j % 3) * .045
        p.append(rod(f"cane_{j}", (0, .25, 0), (cos(angle) * .04, height - .09, sin(angle) * .04), .009, "wood"))
        rib = ((.04, height - .09), (.14, height), (.28, height - .08), (.43, height - .22))
        for segment, ((r0, y0), (r1, y1)) in enumerate(zip(rib, rib[1:])):
            p.append(rod(f"frond_{j}_{segment}", (cos(angle) * r0, y0, sin(angle) * r0), (cos(angle) * r1, y1, sin(angle) * r1), .005, "foliage_dark", collision=False))
        # Leaflets attach to the rib and sweep sideways/down, like palm fronds.
        for k in range(4):
            reach = .10 + k * .075
            y = height - (.025, .025, .075, .15)[k]
            for side in (-1, 1):
                start = (cos(angle) * reach, y, sin(angle) * reach)
                end = (cos(angle) * (reach + .08) - sin(angle) * side * .13, y - .09, sin(angle) * (reach + .08) + cos(angle) * side * .13)
                leaf = rod(f"leaflet_{j}_{k}_{side}", start, end, .024, "foliage" if side > 0 else "foliage_dark", collision=False)
                p.append(replace(leaf, shape="ellipsoid", size=(.048, leaf.size[1], .015)))
    return p


def upright_books() -> list[FurniturePart]:
    p = []
    finishes: tuple[MaterialRole, ...] = ("primary", "wood", "primary_dark", "linen")
    for i, (x, height) in enumerate(((-.29, .90), (-.09, 1), (.11, .84), (.29, .95))):
        for side in (-1, 1):
            p.append(part(f"cover_{i}_{side}", (.025, height, .88), (x + side * .075, height / 2, 0), finishes[i]))
        p += [part(f"pages_{i}", (.13, height - .04, .82), (x, height / 2, -.02), "paper"), part(f"spine_{i}", (.175, height, .035), (x, height / 2, .425), finishes[i])]
        for y in (.19, .72):
            p.append(part(f"spine_band_{i}_{y}", (.13, .018, .006), (x, y, .446), "paper", collision=False))
    for side in (-1, 1):
        p += [part(f"bookend_base_{side}", (.22, .025, 1), (side * .39, .0125, 0), "primary_metal"), part(f"bookend_{side}", (.035, .55, 1), (side * .4825, .275, 0), "primary_metal")]
    return p


def square_clock() -> list[FurniturePart]:
    p = [replace(piece, shape="rounded_box", radius=.04) if piece.name in {"case", "dial"} else piece for piece in clock() if piece.name != "bezel"]
    for side in (-1, 1):
        p += [part(f"frame_side_{side}", (.04, 1, .10), (side * .48, .5, .37), "primary_metal"), part(f"frame_end_{side}", (.92, .04, .10), (0, .5 + side * .48, .37), "primary_metal")]
    return p


def triptych() -> list[FurniturePart]:
    p = []
    for i, (x, style) in enumerate(((-.35, "portrait"), (0, "square"), (.35, "landscape"))):
        for piece in picture(style, "wall"):
            p.append(replace(piece, name=f"panel_{i}_{piece.name}", size=(piece.size[0] * .30, *piece.size[1:]), at=(x + piece.at[0] * .30, *piece.at[1:])))
    return p


_assets: dict[str, FurnitureAsset] = {}


def register(
    object_type: ObjectType,
    label: str,
    size: Point,
    model: list[FurniturePart],
    *,
    asset_id: str | None = None,
    color: str = "#B4A48F",
    dimensions: tuple[tuple[Placement, Point], ...] = (),
) -> None:
    key = asset_id or f"{object_type}_standard_01"
    _assets[key] = FurnitureAsset(
        key,
        object_type,
        label,
        size,
        color,
        tuple(model),
        category="decor",
        placement_dimensions_m=dimensions,
    )


register("rug", "Woven bordered rug", (2.0, 0.015, 1.4), rug(), color="#B9AA91")
for suffix, label, style, size in (
    ("standard", "Framed arch print", "portrait", (0.45, 0.60, 0.05)),
    ("square", "Framed geometric print", "square", (0.50, 0.50, 0.05)),
    ("rectangular", "Framed landscape print", "landscape", (0.80, 0.50, 0.05)),
):
    register(
        "picture",
        label,
        size,
        configurations(
            tuple(
                (placement, picture(style, placement))
                for placement in PICTURE_PLACEMENTS
            )
        ),
        asset_id=f"picture_{suffix}_01",
        color="#8C7660",
        dimensions=(("surface", (size[0] * 0.6, size[1] * 0.6, 0.16)),),
    )
register("clock", "Round wall clock", (0.32, 0.32, 0.04), clock(), color="#BA9863")
register(
    "window_covering",
    "Full-width pleated curtain",
    (1.6, 2.2, 0.18),
    curtains(closed=True),
    color="#C4B49B",
)
register(
    "window_covering",
    "Open pair of curtains",
    (1.6, 2.2, 0.18),
    curtains(),
    asset_id="window_covering_curtain_01",
    color="#C4B49B",
)
register(
    "window_covering",
    "Venetian blind",
    (1.2, 1.5, 0.10),
    blind(),
    asset_id="window_covering_blind_01",
    color="#C6C1B0",
)
register(
    "mirror",
    "Standing / round wall mirror",
    (0.65, 1.65, 0.35),
    configurations(
        tuple((placement, mirror(placement)) for placement in STANDING_PLACEMENTS)
    ),
    color="#A99068",
    dimensions=(("wall", (0.65, 0.65, 0.055)),),
)
register(
    "plant",
    "Leafy potted plant",
    (0.65, 1.6, 0.65),
    configurations(
        tuple((placement, plant(placement)) for placement in PLANT_PLACEMENTS)
    ),
    color="#A98A6C",
    dimensions=(("surface", (0.24, 0.36, 0.24)),),
)
register("vase", "Rounded ceramic vase", (0.20, 0.32, 0.20), vase(), color="#B18468")
register(
    "book_stack",
    "Three stacked hardbacks",
    (0.32, 0.14, 0.23),
    books(),
    color="#6B837D",
)
for suffix, label, style, size in (
    ("standard", "Loop ceramic sculpture", "sculpture", (0.18, 0.28, 0.12)),
    ("figurine", "Bird figurine", "bird", (0.18, 0.18, 0.12)),
    ("ornament", "Orb ornament", "orb", (0.18, 0.22, 0.15)),
    ("trophy", "Display trophy", "trophy", (0.18, 0.26, 0.13)),
):
    register(
        "souvenir",
        label,
        size,
        souvenir(style),
        asset_id=f"souvenir_{suffix}_01",
        color="#B39A70",
    )
for suffix, label, size, model in (
    ("standard", "Tray with candles", (0.36, 0.09, 0.24), tray()),
    (
        "bowl",
        "Ceramic serving bowl",
        (0.26, 0.10, 0.26),
        [
            part(
                "bowl",
                (1, 1, 1),
                (0, 0.5, 0),
                "primary_ceramic",
                shape="basin",
                profile=BASIN_PROFILE,
            )
        ],
    ),
    ("utensil_holder", "Utensil pot", (0.13, 0.32, 0.13), utensils()),
    ("soap_dispenser", "Pump soap dispenser", (0.09, 0.20, 0.085), soap()),
    ("toothbrush_cup", "Toothbrush cup", (0.10, 0.20, 0.10), toothbrushes()),
    ("caddy", "Divided storage caddy", (0.28, 0.22, 0.18), caddy()),
    ("toilet_brush", "Toilet brush and holder", (0.12, 0.40, 0.12), toilet_brush()),
    (
        "toilet_paper_holder",
        "Wall toilet paper holder",
        (0.23, 0.20, 0.14),
        paper_holder(),
    ),
):
    register(
        "household_accessory",
        label,
        size,
        model,
        asset_id=f"household_accessory_{suffix}_01",
        color="#A6AA96",
    )

register("rug", "Round bordered rug", (1.6, .015, 1.6), round_rug(), asset_id="rug_round_01", color="#B9AA91")
register("mirror", "Arched mirror", (.65, 1.65, .35), configurations(tuple((placement, arched_mirror(placement)) for placement in STANDING_PLACEMENTS)), asset_id="mirror_arch_01", color="#A99068", dimensions=(("wall", (.65, .95, .055)),))
register("plant", "Potted palm", (.85, 1.6, .85), configurations(tuple((placement, palm(placement)) for placement in PLANT_PLACEMENTS)), asset_id="plant_palm_01", color="#B69A79", dimensions=(("surface", (.30, .45, .30)),))
register("vase", "Slim cylinder vase", (.13, .32, .13), [part("body", (1, 1, 1), (0, .5, 0), "primary_ceramic", shape="tube", wall_thickness=.045), part("bottom", (.94, .035, .94), (0, .0175, 0), "primary_ceramic", shape="cylinder")], asset_id="vase_cylinder_01", color="#B18468")
register("book_stack", "Upright books with bookends", (.34, .25, .18), upright_books(), asset_id="book_stack_upright_01", color="#6B837D")
register("clock", "Square wall clock", (.30, .30, .045), square_clock(), asset_id="clock_square_01", color="#BA9863")
register("picture", "Three-panel wall print", (1.2, .55, .05), triptych(), asset_id="picture_triptych_01", color="#8C7660")

DECOR_CATALOG = MappingProxyType(_assets)
