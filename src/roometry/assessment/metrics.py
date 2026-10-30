"""Comparable MVP proxies for usable space, storage access and a workstation.

The existing layout-score-v1 API remains available for technical diagnostics.
These card scores use room-assessment-v3 and cannot be compared across policies.
"""

from math import cos, sin
from types import SimpleNamespace

from roometry.assessment.schemas import AssessmentCard, RoomAssessment, recommendation
from roometry.assessment.workspace import workspace_card
from roometry.assessment.evidence import AssessmentEvidence, GEOMETRY_CATEGORIES
from roometry.scenes.collision import component_bounds
from roometry.scenes.geometry import Rectangle, ObjectBounds, boxes_overlap, intervals_overlap, rectangle_contains
from roometry.scenes.hashing import scene_hash
from roometry.scenes.navigation import NavigationLimit, build_navigation_grid
from roometry.scenes.scoring import STORAGE_TYPES, ScoreMetric, score_layout
from roometry.scenes.schemas import RoomScene


def unknown(explanation, *, source="geometry", status="unassessed"):
    return AssessmentCard(status=status, score=None, confidence="low", source=source, explanation=explanation)


def metric(key, value, unit):
    return ScoreMetric(key=key, value=float(value), unit=unit)


def with_recommendation(assessment, **changes):
    data = assessment.model_dump()
    data.update(changes)
    cards = SimpleNamespace(**{key: AssessmentCard.model_validate(data[key]) for key in ("openness", "storage", "workspace", "aesthetics")})
    data["recommended"] = recommendation(cards)
    return RoomAssessment.model_validate(data)


def measure_room(scene: RoomScene, *, incomplete_inventory=False, evidence: AssessmentEvidence | None = None) -> RoomAssessment:
    # Scores always describe one exact scene snapshot; no provider or mutation.
    report = score_layout(scene)
    evidence = evidence or AssessmentEvidence(uncertain_categories=GEOMETRY_CATEGORIES if incomplete_inventory else frozenset())
    def confidence(category):
        return "low" if category in evidence.uncertain_categories else "medium"
    aesthetics = unknown("The visual assessment has not completed yet.", source="photos")
    notes = ["Estimated ratings of the original reconstruction, not photo accuracy or a safety certification.",
             "Scores use the same fixed rubric for comparisons. Storage capacity and personal needs are not established by photos."]
    if evidence.uncertain_categories:
        notes.append("Only categories affected by missing room evidence have limited confidence; unaffected categories remain useful.")
    if not report.geometry.valid:
        missing = unknown("This category needs valid reconstructed geometry.")
        return RoomAssessment(scene_hash=scene_hash(scene), openness=missing, storage=missing,
                              workspace=missing, aesthetics=aesthetics, recommended=None, notes=notes)
    solids = {o.id: component_bounds(o) for o in scene.objects if o.type != "rug"}
    floor_area = scene.room.width_m * scene.room.length_m
    footprints = tuple(dict.fromkeys(part.bounds.footprint for parts in solids.values() for part in parts
                                     if intervals_overlap(part.bounds.vertical, (.02, 1.8))))
    try:
        grid = build_navigation_grid(scene.room.width_m, scene.room.length_m, .1, footprints, .4)
        _, sizes = grid.regions(.3)
        usable, main = sum(sizes) * grid.dx * grid.dz, max(sizes, default=0) * grid.dx * grid.dz
        connected = main / usable if usable else 0.
        # Connected probe-center area (not raw empty footprint) changes when
        # furniture divides a room even if occupied area stays constant.
        value = 10 * (.65 * min(1., main / (.4 * floor_area)) + .35 * connected)
        walking = report.geometry.walking_space
        if walking and walking.status == "blocked": value = min(value, 3.)
        elif walking and walking.status == "narrow": value = min(value, 6.)
        route = " Entrance routes could not be assessed." if not walking or walking.status == "unassessed" else f" Entrance routes are {walking.status} in the sampled model."
        openness = AssessmentCard(status="rated", score=round(value, 1), confidence=confidence("openness"), source="geometry",
            explanation=f"About {main:.1f} m² forms the largest usable walking area.{route}",
            metrics=[metric("main_walking_center_area", main, "m2"), metric("walking_center_area", usable, "m2"),
                     metric("connected_fraction", connected, "fraction"), metric("grid_step_x", grid.dx, "m"), metric("grid_step_z", grid.dz, "m")])
    except NavigationLimit:
        openness = unknown("The bounded space check could not finish; no space rating is claimed.")

    containers = [o for o in scene.objects if o.type in STORAGE_TYPES and o.placement in {"floor", "wall"}
                  and o.id not in evidence.excluded_storage_ids]
    present_ids = {obj.id for obj in scene.objects}
    if (evidence.missing_storage and not evidence.storage_ids) or evidence.storage_ids - present_ids:
        storage = unknown('Observed storage remains unresolved. Complete it before rating storage capacity and access.')
    elif not containers:
        storage = unknown("Observed storage remains unresolved." if evidence.missing_storage else
                          "No storage furniture was identified. Architectural window sills and hidden storage are not rated.")
    else:
        room = Rectangle((scene.room.width_m / 2, scene.room.length_m / 2), ((1., 0.), (0., 1.)),
                         (scene.room.width_m / 2, scene.room.length_m / 2))
        accessible = []
        for obj in containers:
            c, s = cos(obj.rotation_y_rad), sin(obj.rotation_y_rad)
            offset = obj.dimensions_m.depth / 2 + .3
            zone = Rectangle((obj.position.x + s * offset, obj.position.z + c * offset),
                             ((c, -s), (s, c)), (min(.6, obj.dimensions_m.width) / 2, .3))
            box = ObjectBounds(zone, (.02, 1.8))
            if rectangle_contains(room, zone) and not any(boxes_overlap(box, part.bounds)
                    for key, parts in solids.items() if key != obj.id for part in parts):
                accessible.append(obj.id)
        volume = sum(o.dimensions_m.width * o.dimensions_m.height * o.dimensions_m.depth for o in containers)
        accessible_volume = sum(o.dimensions_m.width * o.dimensions_m.height * o.dimensions_m.depth
                                for o in containers if o.id in accessible)
        access = len(accessible) / len(containers)
        capacity = volume / (volume + .075 * floor_area)
        storage = AssessmentCard(status="rated", score=round(10 * (.7 * access + .3 * capacity), 1), confidence=confidence("storage"),
            source="geometry", explanation=f"{len(accessible)} of {len(containers)} identified storage pieces have clear front approach space. Capacity is a size estimate; contents and door swings are unknown.",
            entity_ids=[o.id for o in containers], metrics=[metric("accessible_storage_fraction", access, "fraction"),
                metric("nominal_storage_volume", volume, "m3"), metric("storage_objects", len(containers), "count"),
                metric('accessible_storage_volume',accessible_volume,'m3')])

    workspace = workspace_card(scene, report, evidence, confidence("workspace"))
    if evidence.missing_floor_ids - present_ids:
        openness = unknown("Major floor furniture is missing. Complete the reconstruction before rating space and movement.")
    def explain_uncertainty(key, card):
        if key in evidence.uncertain_categories and card.status == "rated":
            return card.model_copy(update={"explanation": (card.explanation + " Relevant room evidence remains uncertain.")[:400]})
        return card
    cards = SimpleNamespace(openness=explain_uncertainty("openness", openness), storage=explain_uncertainty("storage", storage),
                            workspace=explain_uncertainty("workspace", workspace), aesthetics=aesthetics)
    return RoomAssessment(scene_hash=scene_hash(scene), **vars(cards), recommended=recommendation(cards), notes=notes)
