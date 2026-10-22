"""Independent annotated arrangements catch defects that geometry accepts."""

import json
from pathlib import Path
from math import pi

import pytest

from roometry.evaluation.runner import evaluate_reconstruction
from roometry.evaluation.schemas import ReconstructionCase
from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry
from roometry.vision.schemas import RoomAnalysisResponse

ROOT = Path(__file__).resolve().parents[1] / "data/evals"


def load(name):
    case = ReconstructionCase.model_validate_json((ROOT / (name + ".json")).read_text())
    data = json.loads((ROOT / "reference" / (name + ".json")).read_text())
    return case, RoomAnalysisResponse.model_validate_json(json.dumps(data["analysis"])), RoomScene.model_validate(data["working_scene"])


@pytest.mark.parametrize("name", ["bedroom_multiview", "living_room_tv"])
def test_hand_annotated_reference_is_geometry_valid_and_meets_photo_arrangement_gate(name):
    case, analysis, scene = load(name)
    assert validate_geometry(scene).valid
    report = evaluate_reconstruction(case, analysis, scene)
    assert report.passed, report.failures
    assert report.major_object_coverage == report.wall_relationships == report.orientation == 1
    assert report.color_consistency == report.asset_match == 1
    assert set(report.matched_ids) == {item.id for item in case.objects}


@pytest.mark.parametrize("defect", ["facing", "color", "missing_inventory", "wrong_end", "wrong_model", "narrow_wardrobe"])
def test_bedroom_regression_rejects_recognizability_errors(defect):
    case, analysis, scene = load("bedroom_multiview")
    objects = {obj.id: obj for obj in scene.objects}
    if defect == "facing":
        objects["chair"].rotation_y_rad += pi
    elif defect == "color":
        objects["sleeper"].color = "#FFFFFF"
    elif defect == "wrong_end":
        objects["wardrobe"].position.z = .4
    elif defect == "wrong_model":
        objects["sleeper"].asset_id = "bed_standard_01"
    elif defect == "narrow_wardrobe":
        objects["wardrobe"].dimensions_m.width = .6
    else:
        # Even if the analyzer failed to notice it, the independent photo
        # annotation must expose the missing anchor rather than score 100%.
        scene.objects = [o for o in scene.objects if o.id != "radiator"]
        analysis.observations.objects = [o for o in analysis.observations.objects if o.id != "radiator"]
    report = evaluate_reconstruction(case, analysis, scene)
    assert not report.passed and report.failures
    if defect == "missing_inventory":
        assert report.major_object_coverage == .9
        assert any("radiator" in message for message in report.failures)


def test_living_room_sofa_facing_the_window_fails_even_when_it_fits():
    case, analysis, scene = load("living_room_tv")
    sofa = next(o for o in scene.objects if o.id == "sofa")
    sofa.rotation_y_rad = pi
    # More room for this deliberately wrong orientation, still zero collisions.
    sofa.position.x = 3.5
    sofa.position.z = 5.5
    assert validate_geometry(scene).valid
    report = evaluate_reconstruction(case, analysis, scene)
    assert not report.passed and report.orientation < 1
    assert any("sofa" in error and "faces" in error for error in report.failures)
