"""Procedural core furniture: shared visual parts and geometry inputs.

Local coordinates are x/z [-.5, .5], y [0, 1], with +Z facing forward.
Parts are scaled by a scene object's dimensions; nominal sizes are catalog
examples, never inferred room measurements. Spatial checks approximate active
solid parts with individual conservative boxes and use declared support areas.
"""

from dataclasses import asdict, dataclass, replace
from types import MappingProxyType
from typing import Literal

from roometry.scenes.assets import ASSET_REGISTRY, ObjectType, Placement

Point = tuple[float, float, float]
MaterialRole = Literal[
    "primary",
    "primary_dark",
    "primary_metal",
    "wood",
    "metal",
    "linen",
    "inset",
    "screen",
    "rubber",
    "light",
    "indicator",
    "glass",
    "primary_ceramic",
    "chrome",
    "enamel",
    "clear_glass",
    "foliage",
    "foliage_dark",
    "soil",
    "paper",
    "mirror",
]
Shape = Literal[
    "box",
    "rounded_box",
    "cylinder",
    "tube",
    "ellipsoid",
    "torus",
    "basin",
    "frustum",
    "pleated_panel",
    "arched_panel",
]
Category = Literal[
    "furniture",
    "electronics",
    "lighting",
    "climate",
    "kitchen",
    "bathroom",
    "utility",
    "decor",
]


@dataclass(frozen=True)
class FurniturePart:
    name: str
    size: Point
    at: Point
    material: MaterialRole = "primary"
    shape: Shape = "rounded_box"
    radius: float = 0.012
    collision: bool = True
    top_scale: float = 1
    wall_thickness: float = 0.035  # Radial thickness for hollow tube primitives.
    bottom_scale: float = 1
    rotation: Point = (0, 0, 0)
    placements: tuple[Placement, ...] = ()  # Empty means every supported placement.
    profile: tuple[
        tuple[float, float], ...
    ] = ()  # Basin cross-section: radius, height in [0, 1].


@dataclass(frozen=True)
class SupportSurface:
    """An upward-facing rectangle, inside its supporting part, in local units."""

    name: str
    part: str
    y: float
    x_min: float
    x_max: float
    z_min: float
    z_max: float
    purpose: Literal["objects", "seating", "sleeping"] = "objects"


@dataclass(frozen=True)
class FurnitureAsset:
    asset_id: str
    object_type: ObjectType
    label: str
    dimensions_m: Point  # width, height, depth
    color: str
    parts: tuple[FurniturePart, ...]
    surfaces: tuple[SupportSurface, ...] = ()
    category: Category = "furniture"
    placement_dimensions_m: tuple[tuple[Placement, Point], ...] = ()

    def to_dict(self) -> dict:
        placements = sorted(ASSET_REGISTRY[self.asset_id].placements)
        anchors = {
            "floor": {"at": (0, 0, 0), "normal": (0, -1, 0)},
            "surface": {"at": (0, 0, 0), "normal": (0, -1, 0)},
            "wall": {"at": (0, 0.5, -0.5), "normal": (0, 0, -1)},
            "ceiling": {"at": (0, 1, 0), "normal": (0, 1, 0)},
        }
        dimensions = dict(self.placement_dimensions_m)
        return {
            **asdict(self),
            "placements": placements,
            "placement_dimensions_m": {
                p: dimensions.get(p, self.dimensions_m) for p in placements
            },
            "placement_anchors": {
                placement: anchors[placement] for placement in placements
            },
        }


def part(
    name: str,
    size: Point,
    at: Point,
    material: MaterialRole = "primary",
    *,
    radius: float = 0.012,
    collision: bool = True,
    shape: Shape = "rounded_box",
    top_scale: float = 1,
    bottom_scale: float = 1,
    rotation: Point = (0, 0, 0),
    wall_thickness: float = 0.035,
    profile: tuple[tuple[float, float], ...] = (),
) -> FurniturePart:
    return FurniturePart(
        name=name,
        size=size,
        at=at,
        material=material,
        shape=shape,
        radius=radius,
        collision=collision,
        top_scale=top_scale,
        bottom_scale=bottom_scale,
        rotation=rotation,
        wall_thickness=wall_thickness,
        profile=profile,
    )


def surface(
    p: FurniturePart,
    *,
    purpose: Literal["objects", "seating", "sleeping"] = "objects",
    inset: float = 0.01,
) -> SupportSurface:
    x, y, z = p.at
    w, h, d = p.size
    if p.shape == "rounded_box":
        inset = max(inset, min(p.radius, min(p.size) / 2))
    return SupportSurface(
        p.name,
        p.name,
        y + h / 2,
        x - w / 2 + inset,
        x + w / 2 - inset,
        z - d / 2 + inset,
        z + d / 2 - inset,
        purpose,
    )


def legs(
    height: float,
    *,
    x: float = 0.41,
    z: float = 0.39,
    thickness: float = 0.06,
    material: MaterialRole = "wood",
) -> list[FurniturePart]:
    return [
        part(
            f"leg_{i}",
            (thickness, height, thickness),
            (xx, height / 2, zz),
            material,
            shape="cylinder",
            bottom_scale=0.78,
        )
        for i, (xx, zz) in enumerate(((-x, -z), (x, -z), (-x, z), (x, z)))
    ]


def bed(crib: bool = False) -> tuple[list[FurniturePart], list[SupportSurface]]:
    p = legs(0.14)
    mattress = part(
        "mattress", (0.94, 0.18, 0.92), (0, 0.41, 0.015), "linen", radius=0.035
    )
    p += [part("frame", (1, 0.18, 1), (0, 0.23, 0), "wood"), mattress]
    if crib:
        p += [
            part("headboard", (1, 0.68, 0.055), (0, 0.66, -0.4725)),
            part("footboard", (1, 0.68, 0.055), (0, 0.66, 0.4725)),
        ]
        for side in (-1, 1):
            p.append(
                part(f"rail_{side}", (0.055, 0.055, 0.92), (side * 0.4725, 0.9725, 0))
            )
            for i in range(9):
                p.append(
                    part(
                        f"slat_{side}_{i}",
                        (0.025, 0.5, 0.022),
                        (side * 0.4725, 0.695, -0.39 + i * 0.0975),
                        radius=0.005,
                    )
                )
    else:
        p += [
            part("headboard", (1, 0.68, 0.075), (0, 0.66, -0.4625), radius=0.025),
            part(
                "headboard_inset",
                (0.87, 0.39, 0.035),
                (0, 0.74, -0.4075),
                "primary_dark",
            ),
            part("quilt", (0.95, 0.055, 0.62), (0, 0.5275, 0.17), radius=0.018),
            part("quilt_fold", (0.95, 0.03, 0.11), (0, 0.57, -0.085), "primary_dark"),
        ]
        for i, x in enumerate((-0.23, 0.23)):
            p.append(
                part(
                    f"pillow_{i}",
                    (0.39, 0.105, 0.21),
                    (x, 0.5525, -0.29),
                    "linen",
                    radius=0.035,
                )
            )
    return p, [surface(mattress, purpose="sleeping")]


def sofa(
    *, armchair: bool = False, chaise: int = 0
) -> tuple[list[FurniturePart], list[SupportSurface]]:
    p = legs(0.13, thickness=0.055, material="metal")
    # The main row and chaise have separate parts: the L's empty corner stays empty.
    depth, z = (0.56, -0.22) if chaise else (1, 0)
    p += [
        part("base", (1, 0.22, depth), (0, 0.24, z), "primary_dark", radius=0.035),
        part("back_frame", (0.88, 0.63, 0.15), (0, 0.685, -0.425), radius=0.045),
    ]
    for side in (-1, 1):
        p.append(
            part(
                f"arm_{side}",
                (0.12, 0.44, depth - 0.04),
                (side * 0.44, 0.51, z),
                radius=0.045,
            )
        )
    count = 1 if armchair else 3
    seats = []
    for i in range(count):
        x = -0.37 + 0.74 * (i + 0.5) / count
        seat = part(
            f"seat_{i}",
            (0.74 / count - 0.012, 0.18, depth - 0.19),
            (x, 0.43, z + 0.07),
            radius=0.035,
        )
        seats.append(seat)
        p += [
            seat,
            part(
                f"back_cushion_{i}",
                (0.74 / count - 0.012, 0.4, 0.17),
                (x, 0.75, -0.32),
                radius=0.045,
            ),
        ]
    if chaise:
        x = chaise * (0.37 - 0.74 / count / 2)
        chaise_seat = part(
            "chaise_seat",
            (0.74 / count - 0.012, 0.18, 0.46),
            (x, 0.43, 0.27),
            radius=0.025,
        )
        p += [
            part(
                "chaise_base",
                (0.74 / count, 0.22, 0.44),
                (x, 0.24, 0.28),
                "primary_dark",
                radius=0.025,
            ),
            chaise_seat,
            part(
                "chaise_foot_0", (0.055, 0.13, 0.055), (x - 0.08, 0.065, 0.44), "metal"
            ),
            part(
                "chaise_foot_1", (0.055, 0.13, 0.055), (x + 0.08, 0.065, 0.44), "metal"
            ),
        ]
        # Move front legs under the main row, away from the empty L corner.
        p[2] = replace(p[2], at=(-0.41, 0.065, -0.02))
        p[3] = replace(p[3], at=(0.41, 0.065, -0.02))
        seats.append(chaise_seat)
    return p, [surface(s, purpose="seating") for s in seats]


def chair(stool: bool = False) -> tuple[list[FurniturePart], list[SupportSurface]]:
    seat_y = 0.93 if stool else 0.49
    seat = part(
        "seat", (1, 0.14 if stool else 0.1, 0.92), (0, seat_y, 0.02), radius=0.045
    )
    p = legs(seat_y - seat.size[1] / 2, x=0.35, z=0.32, thickness=0.07)
    p += [seat, part("front_stretcher", (0.72, 0.045, 0.045), (0, 0.23, 0.32), "wood")]
    if stool:
        p.append(part("back_stretcher", (0.72, 0.045, 0.045), (0, 0.23, -0.32), "wood"))
    else:
        p += [
            part("back_post_left", (0.055, 0.44, 0.06), (-0.36, 0.7, -0.43), "wood"),
            part("back_post_right", (0.055, 0.44, 0.06), (0.36, 0.7, -0.43), "wood"),
            part(
                "upholstered_back", (0.92, 0.36, 0.13), (0, 0.82, -0.435), radius=0.045
            ),
            part(
                "back_seam",
                (0.012, 0.24, 0.006),
                (0, 0.82, -0.367),
                "primary_dark",
                radius=0.002,
                collision=False,
            ),
        ]
    return p, [surface(seat, purpose="seating")]


def bench(ottoman: bool = False) -> tuple[list[FurniturePart], list[SupportSurface]]:
    cushion = part(
        "cushion",
        (1, 0.36 if ottoman else 0.27, 1),
        (0, 0.82 if ottoman else 0.865, 0),
        radius=0.055,
    )
    p = legs(0.12 if ottoman else 0.64, thickness=0.065)
    p += [
        part(
            "base",
            (0.94, 0.55 if ottoman else 0.1, 0.91),
            (0, 0.395 if ottoman else 0.68, 0),
            "primary_dark",
            radius=0.025,
        ),
        cushion,
    ]
    if not ottoman:
        p.append(part("brace", (0.82, 0.07, 0.07), (0, 0.27, 0), "wood"))
    return p, [surface(cushion, purpose="seating")]


def table(kind: str) -> tuple[list[FurniturePart], list[SupportSurface]]:
    top = part("tabletop", (1, 0.075, 1), (0, 0.9625, 0), radius=0.018)
    p = legs(
        0.925,
        thickness=0.055,
        x=0.4,
        z=0.37,
        material="metal" if kind == "desk" else "wood",
    )
    p.append(top)
    if kind == "desk":
        p += [
            part("drawer", (0.44, 0.12, 0.6), (0.22, 0.86, 0.1), "primary_dark"),
            part(
                "drawer_pull",
                (0.18, 0.015, 0.025),
                (0.22, 0.86, 0.4125),
                "metal",
                collision=False,
            ),
        ]
    elif kind == "coffee_table":
        p.append(part("lower_shelf", (0.84, 0.045, 0.76), (0, 0.26, 0), "wood"))
    else:
        p += [
            part("apron_front", (0.84, 0.09, 0.035), (0, 0.88, 0.39), "primary_dark"),
            part("apron_back", (0.84, 0.09, 0.035), (0, 0.88, -0.39), "primary_dark"),
        ]
    return p, [surface(top)]


def cabinet(kind: str) -> tuple[list[FurniturePart], list[SupportSurface]]:
    wardrobe = kind == "wardrobe"
    low, top_y = (0.04 if wardrobe else 0.14), 0.97
    p = [] if wardrobe else legs(0.14, thickness=0.055)
    p += [
        part("bottom", (1, 0.06, 0.96), (0, low + 0.03, 0), "wood"),
        part("left_side", (0.045, top_y - low, 0.96), (-0.4775, (top_y + low) / 2, 0)),
        part("right_side", (0.045, top_y - low, 0.96), (0.4775, (top_y + low) / 2, 0)),
        part(
            "back", (0.91, top_y - low, 0.035), (0, (top_y + low) / 2, -0.4625), "wood"
        ),
        part("top", (1, 0.06, 1), (0, 0.97, 0)),
    ]
    if wardrobe:
        p.append(part("plinth", (0.92, 0.04, 0.88), (0, 0.02, -0.02), "primary_dark"))
        for side in (-1, 1):
            p += [
                part(
                    f"door_{side}", (0.45, 0.84, 0.045), (side * 0.234, 0.515, 0.4775)
                ),
                part(
                    f"handle_{side}",
                    (0.018, 0.17, 0.025),
                    (side * 0.045, 0.54, 0.4875),
                    "metal",
                    collision=False,
                ),
            ]
        return p, []
    if kind in {"dresser", "nightstand"}:
        count = 3 if kind == "dresser" else 2
        height = 0.68 / count
        for i in range(count):
            y = 0.23 + height * (i + 0.5)
            p += [
                part(f"drawer_{i}", (0.9, height - 0.016, 0.035), (0, y, 0.4825)),
                part(
                    f"pull_{i}",
                    (0.24, 0.025, 0.025),
                    (0, y, 0.4875),
                    "metal",
                    collision=False,
                ),
            ]
    else:
        for side in (-1, 1):
            p += [
                part(f"door_{side}", (0.3, 0.68, 0.035), (side * 0.305, 0.56, 0.4825)),
                part(
                    f"handle_{side}",
                    (0.06, 0.025, 0.025),
                    (side * 0.305, 0.78, 0.4875),
                    "metal",
                    collision=False,
                ),
                part(
                    f"divider_{side}",
                    (0.03, 0.72, 0.91),
                    (side * 0.14, 0.56, 0),
                    "wood",
                ),
            ]
        p.append(part("center_shelf", (0.25, 0.035, 0.91), (0, 0.54, 0), "wood"))
        if kind == "sideboard":
            p.append(part("center_door", (0.25, 0.68, 0.035), (0, 0.56, 0.4825)))
    surfaces = [surface(next(piece for piece in p if piece.name == "top"))]
    if kind == "tv_stand":
        surfaces.append(
            surface(next(piece for piece in p if piece.name == "center_shelf"))
        )
    return p, surfaces


def shelving(kind: str) -> tuple[list[FurniturePart], list[SupportSurface]]:
    p: list[FurniturePart] = []
    levels = [0, 0.31, 0.63, 0.96] if kind == "shelf" else [0, 0.48, 0.96]
    if kind == "wall_shelf":
        levels = [0, 0.96]
    surfaces = []
    for i, level in enumerate(levels):
        board = part(f"shelf_{i}", (1, 0.04, 1), (0, level + 0.02, 0))
        p.append(board)
        surfaces.append(surface(board))
    if kind == "shoe_rack":
        for side in (-1, 1):
            for back in (-1, 1):
                p.append(
                    part(
                        f"upright_{side}_{back}",
                        (0.045, 1, 0.045),
                        (side * 0.46, 0.5, back * 0.43),
                        "metal",
                    )
                )
    elif kind == "wall_shelf":
        for side in (-1, 1):
            p.append(
                part(
                    f"wall_upright_{side}",
                    (0.035, 0.96, 0.035),
                    (side * 0.4, 0.5, -0.46),
                    "metal",
                )
            )
    else:
        p += [
            part("left_side", (0.045, 0.92, 1), (-0.4775, 0.5, 0)),
            part("right_side", (0.045, 0.92, 1), (0.4775, 0.5, 0)),
            part("back", (0.91, 0.92, 0.025), (0, 0.5, -0.4875), "wood"),
        ]
        # Usable rectangles exclude the side panels and backing.
        surfaces = [
            replace(s, x_min=-0.445, x_max=0.445, z_min=-0.465) if s.y < 0.99 else s
            for s in surfaces
        ]
    return p, surfaces


def coat_rack() -> tuple[list[FurniturePart], list[SupportSurface]]:
    p = [
        part("base", (0.95, 0.04, 0.95), (0, 0.02, 0), "primary_dark"),
        part("post", (0.055, 0.96, 0.055), (0, 0.52, 0), shape="cylinder"),
    ]
    for i, y in enumerate((0.72, 0.9)):
        p += [
            part(f"hook_x_{i}", (0.86, 0.035, 0.035), (0, y, 0)),
            part(f"hook_z_{i}", (0.035, 0.035, 0.86), (0, y, 0)),
        ]
        for side in (-1, 1):
            p += [
                part(
                    f"hook_end_x_{i}_{side}",
                    (0.035, 0.075, 0.035),
                    (side * 0.4125, y + 0.02, 0),
                ),
                part(
                    f"hook_end_z_{i}_{side}",
                    (0.035, 0.075, 0.035),
                    (0, y + 0.02, side * 0.4125),
                ),
            ]
    return p, []


def storage(chest: bool = False) -> tuple[list[FurniturePart], list[SupportSurface]]:
    p = [
        part("body", (0.96, 0.84, 0.96), (0, 0.42, 0), radius=0.025),
        part("lid", (1, 0.1, 1), (0, 0.95, 0), "primary_dark", radius=0.025),
        part("lid_seal", (0.97, 0.06, 0.97), (0, 0.87, 0), "wood", radius=0.015),
    ]
    for side in (-1, 1):
        p.append(
            part(
                f"handle_{side}",
                (0.025, 0.08, 0.22),
                (side * 0.4875, 0.65, 0),
                "metal",
                collision=False,
            )
        )
    if chest:
        for x in (-0.33, 0.33):
            p.append(
                part(
                    f"strap_{x}",
                    (0.035, 0.85, 0.015),
                    (x, 0.425, 0.4875),
                    "wood",
                    collision=False,
                )
            )
        p.append(
            part("latch", (0.07, 0.12, 0.02), (0, 0.85, 0.49), "metal", collision=False)
        )
    return p, [surface(p[1])]


def basket() -> tuple[list[FurniturePart], list[SupportSurface]]:
    p = [part("bottom", (0.93, 0.05, 0.93), (0, 0.025, 0), "primary_dark")]
    # Open top and an actual cavity; woven bands are visual detail, not extra solids.
    for side in (-1, 1):
        p += [
            part(f"wall_x_{side}", (0.035, 0.9, 0.93), (side * 0.4475, 0.5, 0)),
            part(f"wall_z_{side}", (0.86, 0.9, 0.035), (0, 0.5, side * 0.4475)),
            part(
                f"rim_x_{side}",
                (0.07, 0.07, 1),
                (side * 0.465, 0.965, 0),
                "primary_dark",
            ),
            part(
                f"rim_z_{side}",
                (0.86, 0.07, 0.07),
                (0, 0.965, side * 0.465),
                "primary_dark",
            ),
        ]
        for i in range(6):
            y = 0.13 + i * 0.14
            p += [
                part(
                    f"weave_x_{side}_{i}",
                    (0.008, 0.022, 0.92),
                    (side * 0.469, y, 0),
                    "primary_dark",
                    collision=False,
                ),
                part(
                    f"weave_z_{side}_{i}",
                    (0.86, 0.022, 0.008),
                    (0, y, side * 0.469),
                    "primary_dark",
                    collision=False,
                ),
            ]
        for i in range(9):
            offset = -0.39 + i * 0.0975
            p += [
                part(
                    f"strand_x_{side}_{i}",
                    (0.008, 0.84, 0.012),
                    (side * 0.474, 0.49, offset),
                    "primary_dark",
                    collision=False,
                    shape="box",
                ),
                part(
                    f"strand_z_{side}_{i}",
                    (0.012, 0.84, 0.008),
                    (offset, 0.49, side * 0.474),
                    "primary_dark",
                    collision=False,
                    shape="box",
                ),
            ]
    return p, []


def bin_model() -> tuple[list[FurniturePart], list[SupportSurface]]:
    return [
        part(
            "base",
            (0.82, 0.035, 0.82),
            (0, 0.0175, 0),
            "primary_dark",
            shape="cylinder",
        ),
        part("shell", (1, 0.91, 1), (0, 0.49, 0), shape="tube", bottom_scale=0.82),
        part("rim", (1, 0.055, 1), (0, 0.9725, 0), "primary_dark", shape="tube"),
    ], []


_assets: dict[str, FurnitureAsset] = {}


def register(
    object_type: ObjectType,
    label: str,
    dimensions: Point,
    color: str,
    model: tuple[list[FurniturePart], list[SupportSurface]],
    *,
    asset_id: str | None = None,
) -> None:
    asset_id = asset_id or f"{object_type}_standard_01"
    parts, surfaces = model
    _assets[asset_id] = FurnitureAsset(
        asset_id, object_type, label, dimensions, color, tuple(parts), tuple(surfaces)
    )


register("bed", "Upholstered bed", (1.6, 1.05, 2.1), "#80917E", bed())
register(
    "bed", "Slatted crib", (0.75, 1, 1.4), "#C0A184", bed(True), asset_id="bed_crib_01"
)
register("sofa", "Three-seat sofa", (2.25, 0.88, 0.95), "#B4A48F", sofa())
register(
    "sofa",
    "Sofa · left chaise",
    (2.8, 0.88, 1.65),
    "#B4A48F",
    sofa(chaise=-1),
    asset_id="sofa_sectional_left_01",
)
register(
    "sofa",
    "Sofa · right chaise",
    (2.8, 0.88, 1.65),
    "#B4A48F",
    sofa(chaise=1),
    asset_id="sofa_sectional_right_01",
)
register(
    "armchair", "Lounge armchair", (0.85, 0.86, 0.85), "#AAB5A2", sofa(armchair=True)
)
register("chair", "Upholstered dining chair", (0.5, 0.88, 0.55), "#B9A68F", chair())
register("stool", "Padded stool", (0.4, 0.48, 0.4), "#A69780", chair(True))
register("bench", "Upholstered bench", (1.2, 0.47, 0.42), "#9DAB99", bench())
register("ottoman", "Upholstered ottoman", (0.7, 0.43, 0.55), "#B4A48F", bench(True))
register("desk", "Writing desk", (1.3, 0.75, 0.65), "#AE8B67", table("desk"))
register("table", "Dining table", (1.8, 0.76, 0.9), "#947356", table("table"))
register(
    "coffee_table",
    "Coffee table with shelf",
    (1.05, 0.42, 0.6),
    "#947356",
    table("coffee_table"),
)
register("side_table", "Side table", (0.48, 0.52, 0.45), "#AE8B67", table("side_table"))
register(
    "nightstand",
    "Two-drawer nightstand",
    (0.5, 0.55, 0.42),
    "#AE8B67",
    cabinet("nightstand"),
)
register("wardrobe", "Double wardrobe", (1.2, 2.1, 0.6), "#B7AF9E", cabinet("wardrobe"))
register(
    "dresser", "Three-drawer dresser", (1.05, 0.85, 0.48), "#B7AF9E", cabinet("dresser")
)
register("shelf", "Open bookcase", (0.85, 1.8, 0.32), "#AE8B67", shelving("shelf"))
register(
    "shelf",
    "Standing coat rack",
    (0.5, 1.75, 0.5),
    "#947356",
    coat_rack(),
    asset_id="shelf_coat_rack_01",
)
register(
    "tv_stand", "Media console", (1.65, 0.55, 0.42), "#947356", cabinet("tv_stand")
)
register(
    "shoe_rack",
    "Three-tier shoe rack",
    (0.8, 0.55, 0.3),
    "#AE8B67",
    shelving("shoe_rack"),
)
register(
    "sideboard",
    "Three-door sideboard",
    (1.4, 0.85, 0.43),
    "#B7AF9E",
    cabinet("sideboard"),
)
register(
    "wall_shelf",
    "Two-tier wall shelf",
    (0.85, 0.5, 0.25),
    "#AE8B67",
    shelving("wall_shelf"),
)
register("storage_box", "Lidded storage box", (0.45, 0.3, 0.35), "#A0AA96", storage())
register(
    "storage_box",
    "Storage chest",
    (0.85, 0.45, 0.42),
    "#AE8B67",
    storage(True),
    asset_id="storage_box_chest_01",
)
register("basket", "Woven basket", (0.4, 0.28, 0.32), "#B49A72", basket())
register(
    "basket",
    "Laundry basket",
    (0.42, 0.6, 0.35),
    "#B49A72",
    basket(),
    asset_id="basket_laundry_01",
)
register("bin", "Open waste bin", (0.28, 0.35, 0.28), "#8B9388", bin_model())

FURNITURE_CATALOG = MappingProxyType(_assets)
