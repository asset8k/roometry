"""Electronics, lights and climate fixtures using the shared model primitives.

Placement configurations share an asset ID. Decorative details are explicitly
excluded from future collision inputs; no animations, point lights or provider
requests are needed to display these assets.
"""

from dataclasses import replace
from math import asin, atan2, cos, pi, sin, sqrt
from types import MappingProxyType

from roometry.scenes.assets import ObjectType, Placement
from roometry.scenes.furniture import (
    Category,
    FurnitureAsset,
    FurniturePart,
    MaterialRole,
    Point,
    part,
)

FRONT: Point = (pi / 2, 0, 0)


def rod(
    name: str,
    start: Point,
    end: Point,
    radius: float,
    material: MaterialRole = "primary_metal",
    *,
    collision: bool = True,
) -> FurniturePart:
    delta = (end[0] - start[0], end[1] - start[1], end[2] - start[2])
    length = sqrt(sum(d * d for d in delta))
    dx, dy, dz = (d / length for d in delta)
    return part(
        name,
        (radius * 2, length, radius * 2),
        ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2, (start[2] + end[2]) / 2),
        material,
        shape="cylinder",
        rotation=(atan2(dz, dy), 0, -asin(max(-1, min(1, dx)))),
        collision=collision,
    )


def disc(
    name: str, size: Point, at: Point, material: MaterialRole, *, collision: bool = True
) -> FurniturePart:
    # Cylinders are Y-up; turn their axis toward local +Z for lenses and drivers.
    width, height, depth = size
    return part(
        name,
        (width, depth, height),
        at,
        material,
        shape="cylinder",
        rotation=FRONT,
        collision=collision,
    )


def monitor_parts(*, computer: bool = False) -> list[FurniturePart]:
    width, x = (0.69, -0.14) if computer else (1, 0)
    height, y = (0.68, 0.66) if computer else (0.77, 0.615)
    depth = 0.07 if computer else 0.16
    front = -0.35 + depth / 2
    p = [
        part("monitor_frame", (width, height, depth), (x, y, -0.35), radius=0.025),
        part(
            "screen",
            (width - 0.045, height - 0.06, 0.012),
            (x, y + 0.008, front + 0.006),
            "screen",
            collision=False,
        ),
        part(
            "monitor_neck",
            (0.055, 0.4 if computer else 0.23, 0.08),
            (x, 0.24 if computer else 0.145, -0.35),
            "primary_metal",
        ),
        part(
            "monitor_foot",
            (0.4, 0.04, 0.44 if computer else 1),
            (x, 0.02, -0.25 if computer else 0),
            "primary_metal",
        ),
        part(
            "status_light",
            (0.018, 0.009, 0.008),
            (x + width * 0.38, y - height / 2 + 0.018, front + 0.004),
            "indicator",
            collision=False,
        ),
    ]
    if not computer:
        return p
    p += [
        part("tower", (0.23, 0.72, 0.72), (0.385, 0.36, -0.02), radius=0.02),
        part("tower_front", (0.19, 0.66, 0.02), (0.385, 0.37, 0.35), "primary_dark"),
        part(
            "tower_vent",
            (0.13, 0.43, 0.012),
            (0.385, 0.31, 0.367),
            "rubber",
            collision=False,
        ),
        part(
            "tower_button",
            (0.025, 0.025, 0.01),
            (0.385, 0.66, 0.367),
            "indicator",
            collision=False,
        ),
        part("keyboard", (0.62, 0.035, 0.23), (-0.16, 0.0175, 0.355), radius=0.009),
        part("mouse", (0.085, 0.055, 0.13), (0.215, 0.0275, 0.37), shape="ellipsoid"),
    ]
    for row in range(4):
        p.append(
            part(
                f"key_row_{row}",
                (0.55, 0.009, 0.032),
                (-0.16, 0.0395, 0.28 + row * 0.047),
                "primary_dark",
                radius=0.003,
                collision=False,
            )
        )
    for i in range(5):
        p.append(
            part(
                f"tower_slit_{i}",
                (0.11, 0.012, 0.008),
                (0.385, 0.16 + i * 0.065, 0.378),
                "metal",
                collision=False,
                shape="box",
            )
        )
    return p


def laptop() -> list[FurniturePart]:
    p = [
        part("base", (1, 0.055, 1), (0, 0.0275, 0), radius=0.018),
        part("display_frame", (1, 0.92, 0.055), (0, 0.54, -0.465), radius=0.02),
        part(
            "screen", (0.92, 0.79, 0.008), (0, 0.55, -0.4325), "screen", collision=False
        ),
        part("hinge", (0.85, 0.045, 0.08), (0, 0.0775, -0.43), "primary_metal"),
        part(
            "keyboard_well",
            (0.87, 0.008, 0.35),
            (0, 0.059, -0.16),
            "rubber",
            collision=False,
        ),
        part(
            "trackpad",
            (0.35, 0.006, 0.2),
            (0, 0.058, 0.25),
            "primary_dark",
            collision=False,
        ),
        disc(
            "webcam", (0.013, 0.018, 0.005), (0, 0.97, -0.434), "inset", collision=False
        ),
    ]
    for i in range(4):
        p.append(
            part(
                f"key_row_{i}",
                (0.81, 0.009, 0.047),
                (0, 0.068, -0.28 + i * 0.075),
                "primary_dark",
                collision=False,
                radius=0.003,
            )
        )
    return p


def printer() -> list[FurniturePart]:
    p = [
        part("body", (1, 0.76, 0.8), (0, 0.46, -0.1), radius=0.035),
        part("scanner_glass", (0.94, 0.07, 0.78), (0, 0.875, -0.1), "inset"),
        part("scanner_lid", (1, 0.09, 0.8), (0, 0.955, -0.1), radius=0.025),
        part("control_panel", (0.86, 0.12, 0.022), (0, 0.75, 0.311), "primary_dark"),
        part(
            "display",
            (0.21, 0.065, 0.008),
            (0.23, 0.75, 0.326),
            "screen",
            collision=False,
        ),
        part(
            "power_button",
            (0.025, 0.025, 0.009),
            (-0.3, 0.75, 0.326),
            "indicator",
            collision=False,
        ),
        part(
            "paper_slot",
            (0.73, 0.07, 0.016),
            (0, 0.44, 0.31),
            "rubber",
            collision=False,
        ),
        part("paper_tray", (0.78, 0.045, 0.28), (0, 0.16, 0.36), "primary_dark"),
        part("paper", (0.64, 0.012, 0.26), (0, 0.189, 0.36), "linen", collision=False),
    ]
    for x in (-0.38, 0.38):
        for z in (-0.38, 0.18):
            p.append(part(f"foot_{x}_{z}", (0.08, 0.08, 0.08), (x, 0.04, z), "rubber"))
    for i in range(5):
        p.append(
            part(
                f"vent_{i}",
                (0.008, 0.022, 0.4),
                (-0.496, 0.24 + 0.07 * i, -0.12),
                "primary_dark",
                collision=False,
                shape="box",
            )
        )
    return p


def console() -> list[FurniturePart]:
    p = [
        part("bottom_plate", (1, 0.08, 1), (0, 0.04, 0), radius=0.025),
        part("chassis", (0.94, 0.8, 0.94), (0, 0.48, 0), "primary_dark", radius=0.02),
        part("top_plate", (1, 0.12, 1), (0, 0.94, 0), radius=0.03),
        part(
            "disc_slot",
            (0.52, 0.026, 0.014),
            (-0.12, 0.65, 0.476),
            "rubber",
            collision=False,
        ),
        part(
            "power",
            (0.032, 0.07, 0.013),
            (0.36, 0.65, 0.476),
            "indicator",
            collision=False,
        ),
        part(
            "usb", (0.075, 0.035, 0.012), (0.27, 0.28, 0.476), "rubber", collision=False
        ),
    ]
    for i in range(9):
        p.append(
            part(
                f"vent_{i}",
                (0.009, 0.52, 0.025),
                (-0.474, 0.45, -0.32 + i * 0.07),
                "rubber",
                collision=False,
                shape="box",
            )
        )
    return p


def speaker(placement: Placement) -> list[FurniturePart]:
    tall = placement == "floor"
    p = [part("cabinet", (1, 0.93, 0.88), (0, 0.535, -0.06), radius=0.025)]
    for x in (-0.36, 0.36):
        for z in (-0.33, 0.25):
            p.append(part(f"foot_{x}_{z}", (0.1, 0.07, 0.1), (x, 0.035, z), "rubber"))
    drivers = (
        [(0.28, 0.19), (0.57, 0.19), (0.84, 0.095)]
        if tall
        else [(0.39, 0.43), (0.81, 0.16)]
    )
    for i, (y, h) in enumerate(drivers):
        width = 0.73 if i < len(drivers) - 1 else 0.34
        p += [
            disc(f"driver_{i}", (width, h, 0.06), (0, y, 0.42), "rubber"),
            part(
                f"driver_rim_{i}",
                (width + 0.025, h + 0.015, 0.025),
                (0, y, 0.465),
                "metal",
                shape="torus",
                radius=0.035,
            ),
            part(
                f"driver_cap_{i}",
                (width * 0.34, h * 0.34, 0.035),
                (0, y, 0.475),
                "inset",
                shape="ellipsoid",
            ),
        ]
    p.append(
        part("badge", (0.1, 0.025, 0.008), (0, 0.105, 0.386), "metal", collision=False)
    )
    return p


def television(placement: Placement) -> list[FurniturePart]:
    mounted = placement == "wall"
    height, y, depth, z = (
        (1, 0.5, 0.93, 0.035) if mounted else (0.91, 0.545, 0.25, -0.25)
    )
    front = z + depth / 2
    p = [
        part("frame", (1, height, depth - 0.012), (0, y, z - 0.006), radius=0.025),
        part(
            "screen",
            (0.945, height - 0.07, 0.012),
            (0, y + 0.008, front - 0.006),
            "screen",
            collision=False,
        ),
        part(
            "status_light",
            (0.015, 0.007, 0.008),
            (0.4, y - height / 2 + 0.018, front - 0.004),
            "indicator",
            collision=False,
        ),
    ]
    if mounted:
        p.append(part("wall_plate", (0.3, 0.35, 0.07), (0, 0.5, -0.465), "metal"))
    else:
        for x in (-0.32, 0.32):
            p += [
                part(
                    f"foot_{x}", (0.22, 0.035, 0.92), (x, 0.0175, 0.04), "primary_metal"
                ),
                part(
                    f"stand_{x}",
                    (0.025, 0.07, 0.08),
                    (x, 0.07, -0.12),
                    "primary_metal",
                ),
            ]
    return p


def projector(placement: Placement) -> list[FurniturePart]:
    mounted = placement == "ceiling"
    h, y = (0.4, 0.2) if mounted else (0.8, 0.46)
    p = [
        part("housing", (1, h, 0.8), (0, y, -0.05), radius=0.045),
        disc("lens_housing", (0.31, h * 0.77, 0.12), (-0.23, y, 0.41), "primary_dark"),
        disc(
            "lens",
            (0.235, h * 0.58, 0.014),
            (-0.23, y, 0.479),
            "screen",
            collision=False,
        ),
        part(
            "lens_ring",
            (0.315, h * 0.79, 0.035),
            (-0.23, y, 0.477),
            "metal",
            shape="torus",
            radius=0.025,
        ),
    ]
    for i in range(5):
        p.append(
            part(
                f"vent_{i}",
                (0.33, h * 0.035, 0.012),
                (0.22, y - h * 0.25 + i * h * 0.12, 0.356),
                "rubber",
                collision=False,
                shape="box",
            )
        )
    if mounted:
        p += [
            part("ceiling_plate", (0.5, 0.07, 0.5), (0, 0.965, -0.05), "primary_metal"),
            part("mount_post", (0.06, 0.4825, 0.06), (0, 0.68875, -0.05), "primary_metal"),
            part("top_trim", (0.88, 0.0475, 0.7), (0, 0.42375, -0.05)),
        ]
    else:
        p += [
            part("controls", (0.28, 0.045, 0.2), (0.18, 0.9775, -0.12), "primary_dark"),
            part("top_trim", (0.88, 0.095, 0.7), (0, 0.9075, -0.05)),
            part(
                "power",
                (0.025, 0.012, 0.04),
                (0.18, 0.994, -0.12),
                "indicator",
                collision=False,
            ),
        ]
        for x in (-0.38, 0.38):
            for z in (-0.34, 0.22):
                p.append(
                    part(f"foot_{x}_{z}", (0.1, 0.06, 0.1), (x, 0.03, z), "rubber")
                )
    return p


def lamp(*, table: bool = False) -> list[FurniturePart]:
    p = [
        part(
            "base",
            (0.66, 0.035, 0.66),
            (0, 0.0175, 0),
            "primary_metal",
            shape="cylinder",
        ),
        part("shade", (1, 0.26, 1), (0, 0.84, 0), shape="tube", top_scale=0.65),
        part("socket", (0.075, 0.09, 0.075), (0, 0.72, 0), "metal", shape="cylinder"),
        part("bulb", (0.15, 0.09, 0.15), (0, 0.79, 0), "light", shape="ellipsoid"),
        part(
            "finial",
            (0.055, 0.03, 0.055),
            (0, 0.985, 0),
            "primary_metal",
            shape="ellipsoid",
        ),
    ]
    if table:
        p += [
            part("ceramic_body", (0.44, 0.46, 0.44), (0, 0.265, 0), shape="ellipsoid"),
            part(
                "neck",
                (0.055, 0.26, 0.055),
                (0, 0.605, 0),
                "primary_metal",
                shape="cylinder",
            ),
        ]
    else:
        p.append(
            part(
                "stem",
                (0.035, 0.73, 0.035),
                (0, 0.4, 0),
                "primary_metal",
                shape="cylinder",
            )
        )
    for x in (-0.24, 0.24):
        p.append(
            rod(
                f"shade_brace_{x}",
                (0, 0.75, 0),
                (x, 0.74, 0),
                0.009,
                "metal",
                collision=False,
            )
        )
    for x in (-0.12, 0.12):
        p += [
            rod(
                f"harp_upright_{x}",
                (x, 0.75, 0),
                (x, 0.94, 0),
                0.006,
                "metal",
                collision=False,
            ),
            rod(
                f"harp_top_{x}",
                (x, 0.94, 0),
                (0, 0.975, 0),
                0.006,
                "metal",
                collision=False,
            ),
        ]
    return p


def chandelier() -> list[FurniturePart]:
    p = [
        part("ceiling_canopy", (0.19, 0.065, 0.19), (0, 0.9675, 0), shape="cylinder"),
        part(
            "stem",
            (0.035, 0.88, 0.035),
            (0, 0.495, 0),
            "primary_metal",
            shape="cylinder",
        ),
        part(
            "hub", (0.13, 0.09, 0.13), (0, 0.34, 0), "primary_metal", shape="ellipsoid"
        ),
        part(
            "finial",
            (0.07, 0.07, 0.07),
            (0, 0.035, 0),
            "primary_metal",
            shape="ellipsoid",
        ),
    ]
    for i in range(5):
        angle = i * 2 * pi / 5
        x, z = 0.34 * cos(angle), 0.34 * sin(angle)
        p += [
            rod(f"arm_{i}", (0, 0.34, 0), (x, 0.31, z), 0.013),
            part(
                f"socket_{i}",
                (0.06, 0.07, 0.06),
                (x, 0.28, z),
                "primary_metal",
                shape="cylinder",
            ),
            part(
                f"globe_{i}",
                (0.22, 0.22, 0.22),
                (x, 0.15, z),
                "glass",
                shape="ellipsoid",
            ),
            part(
                f"bulb_{i}",
                (0.13, 0.14, 0.13),
                (x, 0.15, z),
                "light",
                shape="ellipsoid",
            ),
        ]
    return p


def ceiling_light() -> list[FurniturePart]:
    return [
        part("rim", (1, 0.66, 1), (0, 0.67, 0), shape="cylinder"),
        part("diffuser", (0.91, 0.36, 0.91), (0, 0.18, 0), "light", shape="ellipsoid"),
        part(
            "rim_detail",
            (0.985, 0.05, 0.985),
            (0, 0.43, 0),
            "primary_metal",
            shape="tube",
        ),
    ]


def ceiling_fan() -> list[FurniturePart]:
    p = [
        part("canopy", (0.18, 0.08, 0.18), (0, 0.96, 0), shape="cylinder"),
        part(
            "downrod",
            (0.035, 0.45, 0.035),
            (0, 0.695, 0),
            "primary_metal",
            shape="cylinder",
        ),
        part(
            "motor",
            (0.23, 0.22, 0.23),
            (0, 0.36, 0),
            "primary_metal",
            shape="ellipsoid",
        ),
        part(
            "light_socket",
            (0.1, 0.05, 0.1),
            (0, 0.245, 0),
            "primary_metal",
            shape="cylinder",
        ),
        part("diffuser", (0.2, 0.16, 0.2), (0, 0.15, 0), "light", shape="ellipsoid"),
        part(
            "cap", (0.15, 0.12, 0.15), (0, 0.06, 0), "primary_metal", shape="ellipsoid"
        ),
    ]
    for i in range(3):
        angle = i * 2 * pi / 3
        p.append(
            part(
                f"blade_{i}",
                (0.38, 0.035, 0.14),
                (0.27 * cos(angle), 0.34, -0.27 * sin(angle)),
                radius=0.018,
                rotation=(0, angle, 0),
            )
        )
    return p


def radiator() -> list[FurniturePart]:
    p = [
        part("wall_bracket", (0.84, 0.7, 0.05), (0, 0.5, -0.475), "metal"),
        part("top_manifold", (0.94, 0.055, 0.13), (0, 0.88, 0.23), "primary_metal"),
        part("bottom_manifold", (0.94, 0.055, 0.13), (0, 0.1, 0.23), "primary_metal"),
        part(
            "valve", (0.08, 0.11, 0.14), (0.46, 0.055, 0.43), "metal", shape="cylinder"
        ),
    ]
    for i in range(9):
        p.append(
            part(
                f"fin_{i}",
                (0.082, 0.94, 0.9),
                (-0.42 + i * 0.105, 0.53, 0),
                radius=0.03,
            )
        )
    return p


def air_conditioner() -> list[FurniturePart]:
    p = [
        part("housing", (1, 0.93, 0.96), (0, 0.535, 0), radius=0.055),
        part("wall_plate", (0.9, 0.6, 0.04), (0, 0.58, -0.48), "metal"),
        part(
            "air_outlet",
            (0.88, 0.1, 0.018),
            (0, 0.125, 0.49),
            "rubber",
            collision=False,
        ),
        part("lower_trim", (0.92, 0.055, 0.04), (0, 0.05, 0.455)),
        part("louver", (0.86, 0.045, 0.16), (0, 0.0225, 0.4)),
        part(
            "display",
            (0.095, 0.045, 0.01),
            (0.33, 0.55, 0.486),
            "screen",
            collision=False,
        ),
        part(
            "status",
            (0.024, 0.013, 0.008),
            (0.33, 0.48, 0.49),
            "indicator",
            collision=False,
        ),
    ]
    for i in range(7):
        p.append(
            part(
                f"guide_{i}",
                (0.009, 0.07, 0.013),
                (-0.36 + i * 0.12, 0.125, 0.493),
                "primary_dark",
                collision=False,
                shape="box",
            )
        )
    return p


def fan_blade(index: int, head_height: float, head_y: float) -> FurniturePart:
    # Bake the circular head's nonuniform scale into each rotated ellipse.
    # This keeps the blades inside the cage for tall and desktop configurations.
    angle = index * 2 * pi / 3 + 0.2
    a, b = 0.24, 0.095
    c, s = cos(angle), sin(angle)
    xx = a * a * c * c + b * b * s * s
    yy = head_height**2 * (a * a * s * s + b * b * c * c)
    xy = head_height * (a * a - b * b) * c * s
    half = (xx + yy) / 2
    delta = sqrt(((xx - yy) / 2) ** 2 + xy * xy)
    return part(
        f"blade_{index}",
        (2 * sqrt(half + delta), 2 * sqrt(half - delta), 0.045),
        (0.2 * c, head_y + 0.2 * head_height * s, 0.26),
        "primary_dark",
        shape="ellipsoid",
        rotation=(0, 0, atan2(2 * xy, xx - yy) / 2),
    )


def fan(placement: Placement) -> list[FurniturePart]:
    tall = placement == "floor"
    head_height, head_y = (0.33, 0.835) if tall else (0.76, 0.62)
    base_h, neck_h = (0.045, 0.65) if tall else (0.075, 0.21)
    p = [
        part("base", (0.82, base_h, 1), (0, base_h / 2, 0), shape="cylinder"),
        part(
            "neck",
            (0.055, neck_h, 0.07),
            (0, base_h + neck_h / 2, -0.08),
            "primary_metal",
            shape="cylinder",
        ),
        rod("yoke", (0, base_h + neck_h, -0.08), (0, head_y, -0.08), 0.025),
        disc(
            "motor", (0.3, head_height * 0.28, 0.42), (0, head_y, -0.09), "primary_dark"
        ),
        part(
            "cage_front",
            (1, head_height, 0.035),
            (0, head_y, 0.4),
            "metal",
            shape="torus",
            radius=0.007,
        ),
        part(
            "cage_rear",
            (1, head_height, 0.035),
            (0, head_y, 0.12),
            "metal",
            shape="torus",
            radius=0.007,
        ),
    ]
    for scale in (0.72, 0.44):
        p.append(part(
            f"inner_hoop_{scale}", (scale, head_height * scale, 0.012),
            (0, head_y, 0.42), "metal", shape="torus", radius=0.005, collision=False,
        ))
    for i in range(16):
        angle = i * pi / 8
        x, y = 0.486 * cos(angle), head_y + 0.486 * head_height * sin(angle)
        p.append(
            rod(
                f"spoke_{i}",
                (0, head_y, 0.42),
                (x, y, 0.42),
                0.0025,
                "metal",
                collision=False,
            )
        )
        if i % 4 == 0:
            p.append(rod(f"cage_bridge_{i}", (x, y, 0.12), (x, y, 0.4), 0.003, "metal"))
    p += [fan_blade(i, head_height, head_y) for i in range(3)]
    p += [
        disc(
            "hub", (0.19, head_height * 0.19, 0.07), (0, head_y, 0.39), "primary_metal"
        ),
        disc(
            "hub_badge",
            (0.105, head_height * 0.105, 0.014),
            (0, head_y, 0.432),
            "primary",
            collision=False,
        ),
    ]
    if tall:
        p.append(
            part(
                "adjustment_collar",
                (0.09, 0.05, 0.1),
                (0, 0.63, -0.08),
                "primary_dark",
                shape="cylinder",
            )
        )
    return p


def configurations(
    recipes: tuple[tuple[Placement, list[FurniturePart]], ...],
) -> tuple[FurniturePart, ...]:
    return tuple(
        replace(p, name=f"{placement}_{p.name}", placements=(placement,))
        for placement, parts in recipes
        for p in parts
    )


_assets: dict[str, FurnitureAsset] = {}


def register(
    object_type: ObjectType,
    label: str,
    size: Point,
    color: str,
    category: Category,
    parts: list[FurniturePart] | tuple[FurniturePart, ...],
    *,
    dimensions: tuple[tuple[Placement, Point], ...] = (),
) -> None:
    asset_id = f"{object_type}_standard_01"
    _assets[asset_id] = FurnitureAsset(
        asset_id,
        object_type,
        label,
        size,
        color,
        tuple(parts),
        category=category,
        placement_dimensions_m=dimensions,
    )


register(
    "computer",
    "Desktop computer set",
    (0.78, 0.46, 0.5),
    "#72797C",
    "electronics",
    monitor_parts(computer=True),
)
register(
    "laptop", "Open laptop", (0.34, 0.23, 0.24), "#88908D", "electronics", laptop()
)
register(
    "monitor",
    "Desktop monitor",
    (0.6, 0.43, 0.22),
    "#626B6C",
    "electronics",
    monitor_parts(),
)
register(
    "printer",
    "Multifunction printer",
    (0.42, 0.28, 0.36),
    "#B6BBB6",
    "electronics",
    printer(),
)
register(
    "game_console",
    "Universal game console",
    (0.32, 0.09, 0.25),
    "#C7CBC5",
    "electronics",
    console(),
)
register(
    "speaker",
    "Speaker",
    (0.22, 0.9, 0.28),
    "#756A5E",
    "electronics",
    configurations((("floor", speaker("floor")), ("surface", speaker("surface")))),
    dimensions=(("surface", (0.2, 0.35, 0.24)),),
)
register(
    "tv",
    "Flat-screen television",
    (1.2, 0.74, 0.28),
    "#626B6C",
    "electronics",
    configurations((("surface", television("surface")), ("wall", television("wall")))),
    dimensions=(("wall", (1.2, 0.68, 0.06)),),
)
register(
    "projector",
    "Room projector",
    (0.32, 0.14, 0.24),
    "#B8BDB6",
    "electronics",
    configurations(
        (("surface", projector("surface")), ("ceiling", projector("ceiling")))
    ),
    dimensions=(("ceiling", (0.32, 0.28, 0.24)),),
)
register(
    "floor_lamp", "Shaded floor lamp", (0.46, 1.6, 0.46), "#B8A27D", "lighting", lamp()
)
register(
    "table_lamp",
    "Ceramic table lamp",
    (0.32, 0.55, 0.32),
    "#B8A27D",
    "lighting",
    lamp(table=True),
)
register(
    "chandelier",
    "Five-arm globe chandelier",
    (0.72, 0.65, 0.72),
    "#897457",
    "lighting",
    chandelier(),
)
register(
    "ceiling_light",
    "Flush ceiling light",
    (0.4, 0.13, 0.4),
    "#B8BDB6",
    "lighting",
    ceiling_light(),
)
register(
    "ceiling_fan",
    "Three-blade ceiling fan",
    (1.2, 0.4, 1.2),
    "#9A8063",
    "lighting",
    ceiling_fan(),
)
register("radiator", "Panel radiator", (1, 0.6, 0.12), "#C6C9BF", "climate", radiator())
register(
    "air_conditioner",
    "Wall air conditioner",
    (0.85, 0.28, 0.22),
    "#C6C9BF",
    "climate",
    air_conditioner(),
)
register(
    "fan",
    "Caged fan",
    (0.43, 1.3, 0.36),
    "#ABB5AB",
    "climate",
    configurations((("floor", fan("floor")), ("surface", fan("surface")))),
    dimensions=(("surface", (0.38, 0.45, 0.3)),),
)

_assets["ceiling_light_track_01"] = FurnitureAsset(
    "ceiling_light_track_01", "ceiling_light", "Ceiling track with adjustable spotlights",
    (1.8, .22, .24), "#25272B",
    (part("track", (1, .16, .18), (0, .92, 0), radius=.006),
     *tuple(p for i, x in enumerate((-.38, 0, .38)) for p in (
         part(f"stem_{i}", (.035, .32, .06), (x, .68, 0), "metal"),
         part(f"spot_{i}", (.15, .5, .8), (x, .27, 0), shape="cylinder"),
         part(f"lens_{i}", (.12, .015, .65), (x, .012, 0), "light", shape="cylinder", collision=False),
     ))), category="lighting",
)

EQUIPMENT_CATALOG = MappingProxyType(_assets)
