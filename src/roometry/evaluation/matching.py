"""Resolve reference identities before evaluating placement or appearance.

Uses observation descriptions and observation colors, never candidate positions,
rotations, allowed assets, or relationship success. Tied non-interchangeable
instances remain explicitly ambiguous. IDs only break interchangeable ties.
"""

from dataclasses import dataclass
import re

from roometry.evaluation.schemas import ReconstructionCase
from roometry.scenes.schemas import RoomScene
from roometry.vision.schemas import RoomAnalysisResponse


@dataclass(frozen=True)
class IdentityMatches:
    final: dict[str, str]
    observations: dict[str, str]
    ambiguous: frozenset[str]


def color_distance(a, b):
    return sum((int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) ** 2 for i in (1, 3, 5)) ** .5


def compatible(expected, item):
    return item.type == expected.opening_type if expected.opening_type else item.type in expected.types


def _tokens(text):
    # Locations only disambiguate textual identity; no model coordinates enter.
    return set(re.findall(r"[a-z]+", text.lower())) - {
        "the", "a", "an", "of", "at", "on", "in", "with", "and", "to", "is",
        "wall", "room", "against", "near", "side", "front", "visible", "separate",
    }


def _assignment(scores):
    """Rectangular Hungarian assignment with explicit unmatched dummy columns."""
    if not scores:
        return []
    rows, columns = len(scores), len(scores[0])
    costs = [[-value for value in row] for row in scores]
    u, v, p, way = [0] * (rows + 1), [0] * (columns + 1), [0] * (columns + 1), [0] * (columns + 1)
    for i in range(1, rows + 1):
        p[0], j0 = i, 0
        minimum, used = [float("inf")] * (columns + 1), [False] * (columns + 1)
        while True:
            used[j0], i0, delta, j1 = True, p[j0], float("inf"), 0
            for j in range(1, columns + 1):
                if not used[j]:
                    current = costs[i0 - 1][j - 1] - u[i0] - v[j]
                    if current < minimum[j]:
                        minimum[j], way[j] = current, j0
                    if minimum[j] < delta:
                        delta, j1 = minimum[j], j
            for j in range(columns + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0], j0 = p[j1], j1
    result = [-1] * rows
    for j in range(1, columns + 1):
        if p[j]:
            result[p[j] - 1] = j - 1
    return result


def _interchangeable(case, left, right, visual):
    """A permutation must preserve every scored reference relationship."""
    def shape(ref):
        return (tuple(ref.types), ref.opening_type, ref.color, tuple(ref.label_terms),
                ref.material_colors, ref.allowed_assets, ref.wall_span_fraction, ref.width_depth_ratio,
                visual.get(ref.id, {}).get("support_object_id"))
    if shape(left) != shape(right):
        return False
    swap = {left.id: right.id, right.id: left.id}
    edges = sorted((r.kind, r.subject, r.target) for r in case.relationships)
    # same_end/near/opposite_end are symmetric even if serialized one way.
    def normalized(kind, subject, target):
        return (kind, subject, target) if kind == "faces" else (kind, *sorted((subject, target)))
    return sorted(normalized(*edge) for edge in edges) == sorted(
        normalized(kind, swap.get(subject, subject), swap.get(target, target)) for kind, subject, target in edges)


def match_identities(case: ReconstructionCase, analysis: RoomAnalysisResponse | None,
                     scene: RoomScene | None, visual_reference=None, overrides=None) -> IdentityMatches:
    expected = case.objects
    observed = sorted([*analysis.observations.objects, *analysis.observations.openings], key=lambda x: x.id) if analysis else []
    final = {item.id: item for item in [*scene.objects, *scene.openings]} if scene else {}
    # Candidate colors are deliberately excluded when observations are absent.
    sources = observed or sorted(final.values(), key=lambda x: x.id)
    visual = {item["id"]: item for item in (visual_reference or {}).get("entities", [])}
    descriptions = {}
    for item in sources:
        descriptions[item.id] = " ".join([
            getattr(item, "label", ""), getattr(item, "relative_location", "") or "",
            *getattr(item, "notes", []),
        ]).lower()
    scores = []
    for item in expected:
        hints = _tokens(visual.get(item.id, {}).get("visible_location", ""))
        row = []
        for source in sources:
            description = descriptions[source.id]
            if not compatible(item, source) or (item.label_terms and not any(term.lower() in description for term in item.label_terms)):
                row.append(-1_000_000)
                continue
            weight = 10_000 + 10 * len(hints & _tokens(description))
            color = getattr(source, "color", None) if observed else None
            if item.color and color:
                weight += max(0, 450 - round(color_distance(item.color, color)))
            row.append(weight)
        scores.append(row + [0] * len(expected))
    assignment = _assignment(scores)
    source_matches, final_matches, ambiguous = {}, {}, set()
    for i, j in enumerate(assignment):
        if j >= len(sources) or scores[i][j] <= 0:
            continue
        item, source = expected[i], sources[j]
        if observed:
            source_matches[item.id] = source.id
        if source.id in final and compatible(item, final[source.id]):
            final_matches[item.id] = source.id
        # Equivalent reference instances may be permuted, e.g. four identical
        # chairs all beside the same table. Distinct support/identity pairs may not.
        ties = [k for k in range(len(sources)) if scores[i][k] == scores[i][j]]
        if len(ties) > 1:
            peer_rows = [k for k in range(len(expected)) if k != i and scores[k][j] == scores[i][j]]
            if any(not _interchangeable(case, expected[k], item, visual) for k in peer_rows) or not peer_rows:
                ambiguous.add(item.id)
    overrides = overrides or {}
    if not set(overrides) <= {item.id for item in expected}:
        raise ValueError("Identity review names an unknown reference entity.")
    for key, value in overrides.items():
        ref = next(item for item in expected if item.id == key)
        if value not in final or not compatible(ref, final[value]):
            raise ValueError("Identity review must name a compatible retained entity.")
        final_matches[key] = value
        if value in {item.id for item in observed}:
            source_matches[key] = value
        ambiguous.discard(key)
    if len(set(final_matches.values())) != len(final_matches):
        raise ValueError("Identity review cannot reuse one entity for several references.")
    return IdentityMatches(final_matches, source_matches, frozenset(ambiguous))
