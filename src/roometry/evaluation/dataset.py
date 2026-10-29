"""Load frozen public references and recorded artifacts; no provider imports."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath

from roometry.evaluation.schemas import ReconstructionCase, RecordingIndex


class EvaluationInputError(ValueError):
    """A malformed local input, with no raw payload included in its message."""


def _reject_constant(value):
    raise ValueError("Non-finite JSON numbers are not valid evaluation evidence.")


def read_json(path: Path):
    try:
        if path.stat().st_size > 32_000_000:
            raise EvaluationInputError("Evaluation JSON exceeds the 32 MB input limit.")
        return json.loads(path.read_text(), parse_constant=_reject_constant)
    except (OSError, UnicodeError, ValueError) as exc:
        raise EvaluationInputError("Evaluation JSON is missing or unreadable.") from exc


def local_path(root: Path, relative: str) -> Path:
    """Disallow traversal, symlink escapes, absolute paths and network URLs."""
    part = PurePosixPath(relative)
    if not relative or part.is_absolute() or ".." in part.parts or ":" in relative or "\\" in relative:
        raise EvaluationInputError("Artifact paths must be relative local paths without traversal.")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise EvaluationInputError("Artifact resolves outside its recording directory.")
    return path


@dataclass(frozen=True)
class Dataset:
    root: Path
    manifest: dict
    entries: dict[str, dict]
    references: dict[str, ReconstructionCase]


def load_dataset(path: Path) -> Dataset:
    manifest = read_json(path)
    try:
        cases = manifest["cases"]
        fingerprint = sha256(json.dumps(cases, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if fingerprint != manifest["dataset_fingerprint_sha256"]:
            raise EvaluationInputError("Frozen dataset fingerprint does not match.")
        if sha256((path.parent / "generation.json").read_bytes()).hexdigest() != manifest["generation_sha256"]:
            raise EvaluationInputError("Generation provenance checksum does not match.")
        entries, references = {}, {}
        for entry in cases:
            key = entry["id"]
            if key in entries:
                raise EvaluationInputError("Dataset case IDs must be unique.")
            for relative in [entry["annotation"], *entry["images"]]:
                if "private" in PurePosixPath(relative).parts:
                    raise EvaluationInputError("Public dataset must not include private paths.")
            image = local_path(path.parent, entry["primary_image"])
            annotation = local_path(path.parent, entry["annotation"])
            if (sha256(image.read_bytes()).hexdigest() != entry["checksums"]["image_sha256"]
                or sha256(annotation.read_bytes()).hexdigest() != entry["checksums"]["annotation_sha256"]):
                raise EvaluationInputError("Dataset image or annotation checksum does not match.")
            reference = ReconstructionCase.model_validate_json(annotation.read_text())
            if reference.id != key or reference.photo_files != entry["images"]:
                raise EvaluationInputError("Reference identities and photo paths must match the manifest.")
            entries[key], references[key] = entry, reference
        return Dataset(path.parent, manifest, entries, references)
    except EvaluationInputError:
        raise
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise EvaluationInputError("Public dataset manifest or reference is invalid.") from exc


def load_recordings(path: Path, dataset: Dataset) -> RecordingIndex:
    try:
        index = RecordingIndex.model_validate_json(path.read_text())
        if index.dataset_fingerprint_sha256 != dataset.manifest["dataset_fingerprint_sha256"]:
            raise EvaluationInputError("Recordings belong to another dataset version.")
        seen, primary = set(), set()
        by_id = {trial.evaluation_id: trial for trial in index.trials}
        for trial in index.trials:
            if trial.evaluation_id in seen or trial.case_id not in dataset.entries or trial.evaluation_id.startswith("not_run_"):
                raise EvaluationInputError("Recording IDs must be unique and identify known cases.")
            seen.add(trial.evaluation_id)
            if trial.kind == "reconstruction":
                if trial.parent_evaluation_id is not None:
                    raise EvaluationInputError("A reconstruction cannot have an improvement parent.")
            else:
                parent = by_id.get(trial.parent_evaluation_id)
                if parent is None or parent.kind != "reconstruction" or parent.case_id != trial.case_id:
                    raise EvaluationInputError("An improvement needs its recorded reconstruction parent.")
                if not dataset.entries[trial.case_id].get("improvement_trial"):
                    raise EvaluationInputError("This case is not in the frozen improvement subset.")
            if not trial.repeat:
                key = (trial.case_id, trial.kind)
                if key in primary:
                    raise EvaluationInputError("Only one primary recording per case and task is allowed.")
                primary.add(key)
            # Validate path syntax now; a missing file is isolated to its trial.
            for field in ("run", "cost", "timing", "draft", "flow_checks"):
                relative = getattr(trial, field)
                if relative is not None:
                    local_path(path.parent, relative)
        if any(trial.repeat and (trial.case_id, trial.kind) not in primary for trial in index.trials):
            raise EvaluationInputError("A repeated recording needs its primary trial.")
        return index
    except EvaluationInputError:
        raise
    except (OSError, ValueError) as exc:
        raise EvaluationInputError("Recording index is invalid.") from exc
