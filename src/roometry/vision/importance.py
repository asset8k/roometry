"""Small retention policy for partial reconstruction, never geometry permission."""

from roometry.scenes.schemas import RoomScene
from roometry.vision.schemas import ObjectObservation, OpeningObservation

Observation = ObjectObservation | OpeningObservation

_PRIMARY = frozenset({
    "bed", "sofa", "table", "desk", "wardrobe", "kitchen_cabinet",
    "kitchen_counter", "wall_cabinet", "refrigerator", "cooker", "sink",
    "toilet", "bathtub", "shower", "vanity", "washing_machine", "dryer", "countertop",
})
_FURNITURE = frozenset({
    "armchair", "chair", "stool", "bench", "ottoman", "dresser", "shelf",
    "tv_stand", "shoe_rack", "sideboard", "wall_shelf", "coffee_table",
    "side_table", "nightstand", "tv",
    "monitor", "computer", "laptop", "table_lamp",
})


def importance(entity: Observation) -> int:
    return retention_importance(entity)


def partial_quality(scene: RoomScene, observed: dict[str, Observation]) -> tuple[int, int, int, int]:
    retained = [observed[item.id] for item in [*scene.objects, *scene.openings]]
    # A valid major room element matters more than any number of accessories.
    # Only already validated subsets reach this ranking; nothing gets resized
    # or moved, and an invalid anchor can always remain unresolved.
    return (
        sum(retention_importance(item) == 0 and item.confidence == "high" for item in retained),
        sum(retention_importance(item) == 0 for item in retained),
        sum(retention_importance(item) == 1 for item in retained),
        len(retained),
    )


def retention_importance(entity: Observation) -> int:
    """Being fixed does not make a trim/light more important than furniture."""
    fixed_decor = {"ceiling_detail", "ceiling_light", "chandelier", "picture", "clock", "window_covering", "wall_panel"}
    if isinstance(entity, OpeningObservation) or entity.type in _PRIMARY or (entity.appears_fixed and entity.type not in fixed_decor):
        return 0
    return 1 if entity.type in _FURNITURE else 2
