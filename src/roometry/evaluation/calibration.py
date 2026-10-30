"""Opt-in, versioned reference corrections over an unchanged photo dataset.

Only offline scoring consumes this file. Images, user inputs, original
annotations, recording fingerprints and visual-review evidence remain frozen.
"""

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

from pydantic import Field

from roometry.evaluation.dataset import Dataset, EvaluationInputError, read_json
from roometry.evaluation.schemas import ReferenceModel, ReferenceObject, ReconstructionCase


class ReferenceCorrection(ReferenceModel):
    case_id: str
    object_id: str
    reason: str = Field(min_length=20)
    changes: dict


class Calibration(ReferenceModel):
    version: str
    dataset_fingerprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    corrections: list[ReferenceCorrection]


def apply_calibration(dataset: Dataset, path: Path):
    try:
        document = Calibration.model_validate_json(json.dumps(read_json(path)))
        if document.dataset_fingerprint_sha256 != dataset.manifest["dataset_fingerprint_sha256"]:
            raise ValueError("Wrong dataset")
        references = dict(dataset.references)
        seen = set()
        for correction in document.corrections:
            key = correction.case_id, correction.object_id
            if key in seen or correction.case_id not in references:
                raise ValueError("Unknown or duplicate correction")
            seen.add(key)
            case = references[correction.case_id].model_dump(mode="json")
            obj = next(item for item in case["objects"] if item["id"] == correction.object_id)
            # Reference-only metadata; cannot modify photos, inputs, splits or
            # pass gates, and cannot change the physical reference identity.
            if not correction.changes or "id" in correction.changes:
                raise ValueError("Invalid reference correction")
            obj.update(correction.changes)
            ReferenceObject.model_validate_json(json.dumps(obj))
            references[correction.case_id] = ReconstructionCase.model_validate_json(json.dumps(case))
        metadata = {"version": document.version, "sha256": sha256(path.read_bytes()).hexdigest(),
                    "corrections": document.model_dump(mode="json")["corrections"],
                    "note": "Reference-only calibration; original images, inputs and recordings unchanged. Existing visual judgments reused for identical photo/scene evidence."}
        return replace(dataset, references=references), metadata
    except (ValueError, KeyError, StopIteration, TypeError) as exc:
        raise EvaluationInputError("Evaluation calibration is invalid or belongs to another dataset.") from exc
