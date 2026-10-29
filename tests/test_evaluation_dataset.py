"""Validate benchmark inputs and references without images leaving the machine."""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath

from PIL import Image
import pytest

from roometry.evaluation.schemas import ReconstructionCase
from roometry.uploads.schemas import RoomInputOptions


ROOT = Path(__file__).resolve().parents[1] / "data/evals"
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
CASES = MANIFEST["cases"]


def public_file(relative):
    path = PurePosixPath(relative)
    assert not path.is_absolute() and ".." not in path.parts
    assert "private" not in path.parts
    result = ROOT / relative
    assert result.is_file() and not result.is_symlink()
    assert result.resolve().is_relative_to(ROOT.resolve())
    assert not result.resolve().is_relative_to((ROOT / "private").resolve())
    return result


def test_benchmark_has_the_agreed_unique_cases_and_frozen_splits():
    assert MANIFEST["status"] == "annotated_not_run"
    assert len(CASES) == MANIFEST["target_case_count"] == 25
    assert len({case["id"] for case in CASES}) == 25
    assert Counter(case["room_type"] for case in CASES) == MANIFEST["target_room_types"]
    assert Counter(case["split"] for case in CASES) == MANIFEST["target_splits"]
    assert Counter(case["source"]["type"] for case in CASES) == {
        "generated": 20, "photographed": 5,
    }
    assert {case["id"] for case in CASES if case["split"] == "development"} == {
        "living_01", "bedroom_01", "kitchen_01", "bathroom_01", "office_01",
    }
    assert MANIFEST["human_validated"] is False
    assert Counter(case["clarification_policy"]["measurement_mode"] for case in CASES) == {
        "complete": 8, "partial": 8, "unknown": 9,
    }
    assert len({case["checksums"]["image_sha256"] for case in CASES}) == 25
    assert len({case["diversity"]["style"] for case in CASES}) >= 15


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_case_has_decodable_metadata_free_images_and_compatible_reference(case):
    image = public_file(case["primary_image"])
    assert image.stem == case["id"]
    assert case["primary_image"] in case["images"]
    assert sha256(image.read_bytes()).hexdigest() == case["checksums"]["image_sha256"]
    for relative in case["images"]:
        path = public_file(relative)
        with Image.open(path) as decoded:
            decoded.load()
            assert decoded.format == "JPEG" and decoded.mode == "RGB"
            assert min(decoded.size) >= 640 and max(decoded.size) <= 1440
            assert not decoded.getexif()
            assert not any(key in decoded.info for key in ("exif", "xmp", "comment"))
        assert path.stat().st_size < 2_000_000

    annotation = public_file(case["annotation"])
    assert sha256(annotation.read_bytes()).hexdigest() == case["checksums"]["annotation_sha256"]
    reference = ReconstructionCase.model_validate_json(annotation.read_text())
    assert reference.id == case["id"]
    assert reference.photo_files == case["images"]
    ids = {obj.id for obj in reference.objects}
    assert len(ids) == len(reference.objects)
    for relation in reference.relationships:
        assert relation.subject in ids and relation.target in ids
        assert relation.subject != relation.target

    visual = case["visual_reference"]
    entities = {entity["id"]: entity for entity in visual["entities"]}
    assert len(entities) == len(visual["entities"])
    assert ids == {
        key for key, entity in entities.items()
        if entity["importance"] != "representation_gap"
    }
    for entity in entities.values():
        assert entity["visible_location"]
        assert entity["importance"] in {"major", "secondary", "representation_gap"}
        support = entity["support_object_id"]
        assert support is None or (support in ids and support != entity["id"])


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_generation_inputs_do_not_contain_reference_answers(case):
    assert set(case["input"]) == {"room_type", "dimensions"}
    inputs = RoomInputOptions.model_validate_json(json.dumps(case["input"]))
    assert inputs.room_type == case["room_type"]
    assert inputs.goals == ["general_analysis"]
    assert inputs.description is None
    policy = case["clarification_policy"]
    assert policy["maximum_questions"] == 3
    assert policy["dimensions"] == case["input"]["dimensions"]
    if inputs.dimensions is None:
        assert policy["measurement_mode"] == "unknown"
    else:
        assert inputs.dimensions.measurements_source == "user_confirmed"
        provided = sum(
            getattr(inputs.dimensions, key) is not None
            for key in ("width_m", "length_m", "height_m")
        )
        assert provided == 3 if policy["measurement_mode"] == "complete" else 0 < provided < 3
    if case["source"]["type"] == "photographed":
        assert inputs.dimensions is None  # No measured ground truth was supplied.


def test_image_permissions_and_generation_provenance_are_recorded():
    generation_path = public_file("generation.json")
    assert sha256(generation_path.read_bytes()).hexdigest() == MANIFEST["generation_sha256"]
    records = json.loads(generation_path.read_text())["records"]
    by_id = {record["case_id"]: record for record in records}
    expected = {case["id"] for case in CASES if case["source"]["type"] == "generated"}
    assert len(records) == len(by_id) == 20 and set(by_id) == expected
    for case in CASES:
        source = case["source"]
        if source["type"] == "generated":
            assert source["publication_permission"] == "project_generated_asset"
            assert source["source_inputs"] == "text_only_no_private_images"
            record = by_id[case["id"]]
            assert record["method"] == "built_in_imagegen" and record["prompt"]
            assert record["model_id"] is None  # Tool did not disclose a model ID.
        else:
            assert source["publication_permission"] == "source_license"
            assert source["creator"] and source["modifications"]
            assert source["source_url"].startswith("https://commons.wikimedia.org/wiki/File:")
            assert source["license"] in {"CC0", "CC BY 4.0", "CC BY-SA 3.0", "CC BY-SA 4.0"}
            assert "creativecommons.org/" in source["license_url"]


def test_eight_improvements_reuse_originals_and_preserve_account_privacy():
    trials = [case["improvement_trial"] for case in CASES if case["improvement_trial"]]
    assert len(trials) == 8
    assert Counter(trial["focus"] for trial in trials) == {
        "openness": 2, "storage": 2, "workspace": 2, "aesthetics": 2,
    }
    assert all(trial["start_from"] == "this_case_original" for trial in trials)
    execution = MANIFEST["execution_requirements"]
    assert execution["authentication"] == "signed_in"
    assert execution["save_to_my_rooms"] is True
    assert execution["room_title"] == "primary_image_stem"
    assert execution["publish_to_explore"] is False
    assert execution["outputs"] == "artifacts/evaluations"


def test_dataset_fingerprint_covers_references_and_case_metadata():
    encoded = json.dumps(CASES, sort_keys=True, separators=(",", ":")).encode()
    assert sha256(encoded).hexdigest() == MANIFEST["dataset_fingerprint_sha256"]
    images = {path.relative_to(ROOT).as_posix() for path in (ROOT / "images").rglob("*.jpg")}
    assert images == {image for case in CASES for image in case["images"]}
    annotations = {path.relative_to(ROOT).as_posix() for path in (ROOT / "cases").glob("*.json")}
    assert annotations == {case["annotation"] for case in CASES}
