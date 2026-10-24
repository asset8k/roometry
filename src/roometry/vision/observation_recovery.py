"""Isolate optional photo hypotheses; core identities/evidence stay strict.

Never salvage malformed JSON, an invalid upload manifest, core entities or
oversized collections. Discarding optional metadata does not infer new facts.
The recovered result still passes the complete RoomObservations contract.
"""

from copy import deepcopy
from collections import Counter

from pydantic import ValidationError

from roometry.scenes.schemas import MAX_NOTES
from roometry.vision.schemas import RoomObservations


def recover_optional_observations(decoded):
    if not isinstance(decoded, dict):
        return None
    if "possible_duplicates" not in decoded:
        return None  # Required envelope fields must not be synthesized.
    recovered = deepcopy(decoded)
    # A wrong collection type/size is a response-level error, not permission to
    # truncate data or consume unbounded validation work.
    for field, maximum in (("walls", 4), ("relationships", 200), ("possible_duplicates", 100)):
        values = recovered.get(field, [])
        if not isinstance(values, list) or len(values) > maximum:
            return None
    spans = []
    for wall in recovered.get("walls", []):
        if not isinstance(wall, dict):
            return None
        spans.append(wall.get("span"))
        wall["span"] = None
    optional = {field: recovered.get(field) for field in ("relationships", "possible_duplicates", "finishes")}
    recovered.update(relationships=[], possible_duplicates=[], finishes=None)
    try:
        RoomObservations.model_validate(recovered)
    except ValidationError:
        return None  # Preserve original diagnostics and bounded repair behavior.
    warnings = []
    for index, span in enumerate(spans):
        if span is None:
            continue
        recovered["walls"][index]["span"] = span
        try:
            RoomObservations.model_validate(recovered)
        except ValidationError:
            recovered["walls"][index]["span"] = None
            warnings.append(f"Wall {index + 1}: invalid shorter/longer-wall evidence was ignored; its dimension assignment remains uncertain.")
    for field in ("relationships", "possible_duplicates"):
        repeated = Counter(value.get("id") for value in optional[field] or []
                           if isinstance(value, dict) and isinstance(value.get("id"), str)) if field == "relationships" else {}
        for index, value in enumerate(optional[field] or []):
            if isinstance(value, dict) and isinstance(value.get("id"), str) and repeated.get(value["id"], 0) > 1:
                warnings.append("Spatial relationships with duplicate identities were ignored; their endpoints remain uncertain.")
                continue
            recovered[field].append(value)
            try:
                RoomObservations.model_validate(recovered)
            except ValidationError:
                recovered[field].pop()
                label = "spatial relationship" if field == "relationships" else "possible-duplicate hint"
                warnings.append(f"Invalid {label} {index + 1} was ignored; other observations were retained.")
    if optional["finishes"] is not None:
        recovered["finishes"] = optional["finishes"]
        try:
            RoomObservations.model_validate(recovered)
        except ValidationError:
            recovered["finishes"] = None
            warnings.append("Invalid photo finish evidence was ignored; room finishes remain unknown.")
    if not warnings:
        return None
    summary = f"Photo analysis retained valid observations while ignoring {len(warnings)} invalid optional details. Those details remain unverified."
    recovered["warnings"] = [summary, *list(dict.fromkeys(warnings)), *recovered["warnings"]][:MAX_NOTES]
    return RoomObservations.model_validate(recovered)
