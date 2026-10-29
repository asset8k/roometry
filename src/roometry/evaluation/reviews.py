"""Structured chat-judge handoff and import. This module never calls an LLM."""

from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RUBRIC_VERSION = "roometry-visual-v1"
DIMENSIONS = ("major_objects", "layout", "orientation_support", "proportions", "appearance")


class VisualReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    evaluation_id: str
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["reconstruction", "improvement"]
    scores: dict[str, int | None] | None = None
    preferred: Literal["A", "B", "tie", "unassessed"] | None = None
    meaningful_change: bool | None = None
    constraints_followed: bool | None = None
    confidence: Literal["low", "medium", "high"] | None = None
    evidence: list[str] = Field(default_factory=list)


def evidence_fingerprint(dataset, row, scene=None, original=None):
    from roometry.scenes.hashing import scene_hash

    values = [RUBRIC_VERSION, dataset.manifest["dataset_fingerprint_sha256"], row["evaluation_id"],
              scene_hash(scene) if scene else None, scene_hash(original) if original else None]
    return sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def blind_sides(row):
    # Fixed random-looking assignment is reproducible and unaffected by scores.
    original_is_a = int(row["evidence_sha256"][-1], 16) % 2 == 0
    return {"A": "original" if original_is_a else "candidate",
            "B": "candidate" if original_is_a else "original"}


def review_template(rows):
    return {
        "schema_version": 1, "rubric_version": RUBRIC_VERSION,
        "review_method": "chat_llm", "reviewer_model": None,
        "instructions": [
            "Fill only after inspecting the source photos and saved renders. No judge API is needed.",
            "Score each reconstruction dimension 0=wrong/absent, 1=partly recognizable, 2=consistent with visible evidence.",
            "Inspect required features and known representation gaps in review-packet.json. Do not penalize unknown centimeters or optional small decor.",
            "Improvement A/B order is blinded. Prefer the more useful layout for the stated goal; do not read app scores or blind-key.json before judging.",
            "A plant shuffle is not meaningful practical improvement. Preserve major room functions, fixed installations and locks.",
            "Use unassessed/low confidence when the evidence is insufficient. Keep evidence_sha256 unchanged.",
        ],
        "reviews": [
            {"evaluation_id": row["evaluation_id"], "evidence_sha256": row["evidence_sha256"],
             "kind": row["kind"], "scores": {key: None for key in DIMENSIONS} if row["kind"] == "reconstruction" else None,
             "preferred": None, "meaningful_change": None, "constraints_followed": None,
             "confidence": None, "evidence": []}
            for row in rows if row.get("has_scene")
        ],
    }


def attach_reviews(rows, document):
    """One malformed/stale judgment cannot discard the other recorded results."""
    by_id = {row["evaluation_id"]: row for row in rows}
    errors, seen = [], set()
    if document is None:
        return errors
    if (not isinstance(document, dict) or document.get("schema_version") != 1
        or document.get("rubric_version") != RUBRIC_VERSION or document.get("review_method") != "chat_llm"
        or not isinstance(document.get("reviews"), list)):
        return ["invalid_review_document"]
    for raw in document["reviews"]:
        try:
            review = VisualReview.model_validate_json(json.dumps(raw))
            row = by_id.get(review.evaluation_id)
            if row is None or not row.get("has_scene") or "evidence_sha256" not in row:
                raise ValueError("unknown_evaluation")
            if review.evaluation_id in seen:
                # A contradictory duplicate invalidates that judgment, not the run.
                row["visual_review"] = {"status": "invalid", "reason": "duplicate_review"}
                raise ValueError("duplicate_review")
            seen.add(review.evaluation_id)
            if review.evidence_sha256 != row["evidence_sha256"] or review.kind != row["kind"]:
                raise ValueError("stale_or_wrong_review")
            if review.confidence is None:
                continue  # Unfilled template is explicitly pending.
            if not review.evidence or not any(text.strip() for text in review.evidence):
                raise ValueError("review_needs_visible_evidence")
            if review.kind == "reconstruction":
                if review.scores is None or set(review.scores) != set(DIMENSIONS):
                    raise ValueError("review_needs_all_rubric_dimensions")
                if any(value is None for value in review.scores.values()):
                    continue
                if any(type(value) is not int or not 0 <= value <= 2 for value in review.scores.values()):
                    raise ValueError("review_scores_outside_rubric")
                score = sum(review.scores.values())
                result = {"status": "reviewed", "score_out_of_10": score,
                          "passed": score >= 8 and min(review.scores.values()) >= 1,
                          "scores": review.scores}
            else:
                if review.preferred is None or review.meaningful_change is None or review.constraints_followed is None:
                    continue
                preferred = blind_sides(row).get(review.preferred, review.preferred)
                result = {"status": "reviewed", "preferred": preferred,
                          "passed": preferred == "candidate" and review.meaningful_change and review.constraints_followed,
                          "meaningful_change": review.meaningful_change,
                          "constraints_followed": review.constraints_followed}
            result.update(confidence=review.confidence, evidence=review.evidence,
                          reviewer_model=document.get("reviewer_model"), method="chat_llm")
            row["visual_review"] = result
        except (ValueError, TypeError):
            errors.append("invalid_or_stale_review")
            if isinstance(raw, dict) and isinstance(raw.get("evaluation_id"), str) and raw["evaluation_id"] in by_id:
                by_id[raw["evaluation_id"]]["visual_review"] = {"status": "invalid"}
    return errors
