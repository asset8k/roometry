"""Component requirements stated in an observation, not inferred extra objects."""

import re

from roometry.vision.assembly_context import assembly_features
from roometry.scenes.schemas import MAX_NOTES


def required_features(observation, objects=()):
    """Keep fitted fixtures visible even when analysis groups them into one ID.

    Separately inventoried sinks/taps satisfy the requirement themselves. A
    nearby/separate sink is not evidence that the counter includes one.
    """
    if observation.type not in {"kitchen_counter", "vanity"}:
        return set()
    children = {o.type for o in objects if o.support_object_id == observation.id}
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
        if re.search(r"\b(without|no|separate|beside|nearby)\b", description, re.I):
            continue
        if re.search(r"\b(sink|basin)\b", description, re.I) and "sink" not in children:
            features.add("basin")
        if (
            re.search(r"\b(tap|faucet)\b", description, re.I)
            and "faucet" not in children
        ):
            features.add("faucet")
    return features


def retains_features(asset, observation, objects=()):
    return required_features(observation, objects) <= set(assembly_features(asset))


def normalize_fixture_category(entity):
    """Canonical name for an explicitly wall-mounted kitchen cabinet.

    This is the same observed fixture and mount, not an alternative identity.
    Keep a note so the normalization is visible in the recorded observations.
    """
    if entity.get("type") != "kitchen_cabinet" or entity.get("placement") != "wall":
        return
    notes = entity.get("notes")
    if not isinstance(notes, list):
        return  # Leave malformed model data to normal validation.
    entity["type"] = "wall_cabinet"
    note = "Wall-mounted kitchen cabinet recorded under the catalog's wall_cabinet category."
    entity["notes"] = list(dict.fromkeys([*notes, note]))[-MAX_NOTES:]
