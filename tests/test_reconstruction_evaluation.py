"""Independent annotated arrangements catch defects that geometry accepts."""

import json
from pathlib import Path
from math import pi
import subprocess
import sys

import pytest

from roometry.evaluation.runner import evaluate_reconstruction
from roometry.evaluation.schemas import ReconstructionCase
from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry
from roometry.vision.schemas import RoomAnalysisResponse
from roometry.vision.proposal_schemas import DraftObject, DraftOpening, SceneDraft, UnresolvedEntity

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


def test_inventory_locates_missed_detections_catalog_conflicts_missing_drafts_and_validation_losses():
    case, analysis, scene = load('bedroom_multiview')
    draft = SceneDraft(
        wall_assignments=[],
        objects=[DraftObject.model_validate(obj.model_dump(include=set(DraftObject.model_fields))) for obj in scene.objects if obj.id != 'chair'],
        openings=[DraftOpening.model_validate(obj.model_dump(include=set(DraftOpening.model_fields))) for obj in scene.openings],
        assumptions=[], warnings=[],
    )
    analysis.observations.objects = [obj for obj in analysis.observations.objects if obj.id != 'radiator']
    next(obj for obj in analysis.observations.objects if obj.id == 'wardrobe').placement = 'wall'
    scene.objects = [obj for obj in scene.objects if obj.id not in {'radiator', 'wardrobe', 'chair'}]
    scene.openings = [obj for obj in scene.openings if obj.id != 'window']
    unresolved = [UnresolvedEntity.model_validate({
        'entity_id': 'window', 'kind': 'opening',
        'reason': {'code': 'geometry', 'entity_ids': ['window'], 'message': 'Opening exceeds its wall.'},
    })]
    report = evaluate_reconstruction(case, analysis, scene, draft=draft, unresolved=unresolved)
    inventory = {item.expected_id: item for item in report.inventory}
    assert not report.passed
    assert inventory['radiator'].loss_stage == 'analysis'
    assert inventory['radiator'].detected is False
    assert inventory['wardrobe'].loss_stage == 'catalog'
    assert inventory['wardrobe'].catalog_compatible is False
    assert inventory['chair'].loss_stage == 'construction' and inventory['chair'].drafted is False
    assert inventory['window'].loss_stage == 'validation' and inventory['window'].drafted is True
    assert inventory['window'].issue_codes == ['geometry']
    assert all(item.retained and item.loss_stage is None for key, item in inventory.items() if key not in {'radiator', 'wardrobe', 'chair', 'window'})


def test_unavailable_draft_does_not_claim_a_known_draft_stage():
    case, analysis, scene = load('living_room_tv')
    scene.objects = [obj for obj in scene.objects if obj.id != 'sofa']
    report = evaluate_reconstruction(case, analysis, scene)
    inventory = {item.expected_id: item for item in report.inventory}
    assert inventory['sofa'].detected is True
    assert inventory['sofa'].drafted is None
    assert inventory['sofa'].loss_stage == 'construction_or_validation'
    assert all(item.drafted is None for item in report.inventory)


@pytest.mark.parametrize('analysis_available', [True, False])
def test_failed_generations_produce_a_baseline_report_instead_of_crashing(analysis_available):
    case, analysis, _ = load('bedroom_multiview')
    report = evaluate_reconstruction(case, analysis if analysis_available else None, None)
    assert report.passed is False and report.major_object_coverage == 0
    assert len(report.inventory) == 10
    assert all(item.detected is (True if analysis_available else None) for item in report.inventory)
    assert all(item.loss_stage == ('construction_or_validation' if analysis_available else 'analysis_unavailable') for item in report.inventory)


def test_retained_geometry_is_not_reported_missing_when_analysis_is_unavailable():
    case, _, scene = load('living_room_tv')
    report = evaluate_reconstruction(case, None, scene)
    assert report.major_object_coverage == 1
    assert all(item.retained and item.detected is None and item.loss_stage is None for item in report.inventory)


def test_offline_cli_reports_failed_runs_without_images_or_paid_calls(tmp_path):
    failed_run = tmp_path / 'failed-run.json'
    failed_run.write_text(json.dumps({'analysis': None, 'working_scene': None, 'status': 'failed'}))
    command = [sys.executable, str(ROOT.parents[1] / 'scripts/run_evals.py'),
               '--case', str(ROOT / 'bedroom_multiview.json'), '--run', str(failed_run)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report['passed'] is False and len(report['inventory']) == 10
    assert all(item['loss_stage'] == 'analysis_unavailable' for item in report['inventory'])
