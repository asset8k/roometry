"""Component requirements stated in an observation, not inferred extra objects."""

import re
from types import SimpleNamespace

from roometry.vision.assembly_context import assembly_features
from roometry.scenes.schemas import MAX_NOTES
from roometry.scenes.assets import ASSET_REGISTRY


def _mentions(description, nouns):
    """Keep positive components when another component is negated separately."""
    for match in re.finditer(rf"\b({nouns})\b", description, re.I):
        prefix = re.split(r"[,.;]", description[:match.start()])[-1]
        negatives = list(re.finditer(r"\b(without|no|separate|beside|nearby|near|above|over|next to)\b", prefix, re.I))
        if negatives and not re.search(r"\b(with|includes?|contains?)\b", prefix[negatives[-1].end():], re.I):
            continue
        return True
    return False


def required_features(observation, objects=()):
    """Keep fitted fixtures visible even when analysis groups them into one ID.

    Separately inventoried sinks/taps satisfy the requirement themselves. A
    nearby/separate sink is not evidence that the counter includes one.
    """
    if observation.type not in {"kitchen_counter", "kitchen_cabinet", "vanity", "cooker"}:
        return set()
    def value(obj, key):
        return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
    children = {value(o, "type") for o in objects if value(o, "support_object_id") == observation.id}
    features = set()
    descriptions = [
        observation.label,
        *[
            note
            for note in observation.notes
            if re.search(
                r"\b(with|includes?|contains?|integrated|inset|built[- ]in)\b",
                note,
                re.I,
            )
        ],
    ]
    for description in descriptions:
        if observation.type != "cooker" and _mentions(description, "sink|basin") and "sink" not in children:
            features.add("basin")
        if (
            observation.type != "cooker"
            and _mentions(description, "tap|faucet")
            and "faucet" not in children
        ):
            features.add("faucet")
        if _mentions(description, "hob|cooktop") and "cooker" not in children:
            features.add("cooktop")
    return features


def retains_features(asset, observation, objects=()):
    if inset_hob(observation) and "surface" not in ASSET_REGISTRY[asset.asset_id].placements:
        return False
    return required_features(observation, objects) <= set(assembly_features(asset))


def inset_hob(observation):
    """A photographed hob alone is not evidence for a freestanding oven range."""
    return (observation.type == "cooker"
            and _mentions(observation.label, "hob|cooktop")
            and not _mentions(observation.label, "oven|range|freestanding|standalone")
            and (observation.placement == "surface"
                 or bool(re.search(r"\b(inset|integrated|built[- ]in)\b", observation.label, re.I))))


def normalize_fixture_category(entity, objects=()):
    """Canonical categories for explicitly observed fitted assemblies.

    This is the same observed fixture and mount, not an alternative identity.
    Keep a note so the normalization is visible in the recorded observations.
    """
    if entity.get("type") != "kitchen_cabinet":
        return
    notes = entity.get("notes")
    if not isinstance(notes, list) or not all(isinstance(note, str) for note in notes):
        return  # Leave malformed model data to normal validation.
    if entity.get("placement") == "wall":
        category = "wall_cabinet"
        note = "Wall-mounted kitchen cabinet recorded under the catalog's wall_cabinet category."
    elif (entity.get("placement") in (None, "floor")
          and isinstance(entity.get("id"), str)
          and isinstance(entity.get("label"), str)
          and all(isinstance(note, str) for note in notes)
          and required_features(SimpleNamespace(**entity), objects) & {"basin", "faucet"}):
        category = "kitchen_counter"
        note = "Sink-bearing base cabinet recorded as a fitted kitchen_counter assembly; preserve its observed basin, faucet and identity."
    else:
        return
    entity["type"] = category
    entity["notes"] = list(dict.fromkeys([note, *notes]))[:MAX_NOTES]
