"""Photo-backed scoring corrections, with controls that retain real failures."""

from copy import deepcopy
from hashlib import sha256
import json
from math import pi
from pathlib import Path
import socket

import pytest

from roometry.evaluation.calibration import apply_calibration
from roometry.evaluation.dataset import EvaluationInputError, load_dataset
from roometry.evaluation.matching import match_identities
from roometry.evaluation.metrics import reconstruction_metrics
from roometry.evaluation.runner import evaluate_reconstruction
from roometry.evaluation.schemas import ReconstructionCase, ReferenceRelation
from tests.test_reconstruction_evaluation import load

ROOT = Path(__file__).resolve().parents[1]


def reference(objects, relationships=()):
    return ReconstructionCase(id="calibration_test", photo_files=[], annotation_notes=[],
                              objects=objects, relationships=list(relationships))


def evaluate(case, analysis, scene, visual=None):
    identities = match_identities(case, analysis, scene, visual)
    report = evaluate_reconstruction(case, analysis, scene, identity_matches=identities.final,
        observation_matches=identities.observations, ambiguous_ids=identities.ambiguous,
        component_matches=identities.components)
    entry = {"input": {"dimensions": scene.room.model_dump()}, "visual_reference": visual or {}}
    metrics = reconstruction_metrics(entry, case, {"status": "completed"}, scene, analysis, identities, report)
    return identities, report, metrics


def test_twin_beds_at_opposite_side_walls_share_headboard_end_but_wrong_end_fails():
    _, _, scene = load("synthetic_study")
    bed = next(obj for obj in scene.objects if obj.type == "bed")
    scene.room.width_m, scene.room.length_m = 3.5, 4.
    left, right = bed.model_copy(deep=True), bed.model_copy(deep=True)
    for obj, key, x in [(left, "left_bed", .45), (right, "right_bed", 3.05)]:
        obj.id, obj.rotation_y_rad = key, 0.
        obj.dimensions_m.width, obj.dimensions_m.depth = .9, 2.1
        obj.position.x, obj.position.z = x, 1.05
    scene.objects, scene.openings = [left, right], []
    case = reference([{"id": obj.id, "types": ["bed"]} for obj in scene.objects],
        [{"kind": "same_end", "subject": left.id, "target": right.id}])
    matches = {obj.id: obj.id for obj in scene.objects}
    assert evaluate_reconstruction(case, None, scene, identity_matches=matches).wall_relationships == 1
    right.position.z, right.rotation_y_rad = 2.95, pi
    assert evaluate_reconstruction(case, None, scene, identity_matches=matches).wall_relationships == 0


def test_ambiguous_relation_does_not_suppress_unrelated_facing_or_color_checks():
    case, analysis, scene = load("synthetic_study")
    identities = match_identities(case, analysis, scene)
    ambiguous = frozenset({"shelf_a"})
    report = evaluate_reconstruction(case, analysis, scene, identity_matches=identities.final,
        observation_matches=identities.observations, ambiguous_ids=ambiguous)
    identities = type(identities)(identities.final, identities.observations, ambiguous)
    metrics = reconstruction_metrics({}, case, {"status": "completed"}, scene, analysis, identities, report)
    assert len(report.unassessed_relationships) == 1
    assert report.orientation == metrics["orientation"] == 1
    assert report.wall_relationships == metrics["wall_relationships"] == 1
    assert metrics["color_consistency"] == 1
    next(obj for obj in scene.objects if obj.type == "chair").rotation_y_rad += pi
    broken = evaluate_reconstruction(case, analysis, scene, identity_matches=identities.final,
                                    ambiguous_ids=ambiguous)
    assert broken.orientation == 0


def test_relational_filler_does_not_tie_left_and_right_workstation_identity():
    _, analysis, scene = load("synthetic_study")
    source = next(obj for obj in analysis.observations.objects if obj.type == "desk")
    desk = next(obj for obj in scene.objects if obj.type == "desk")
    analysis.observations.objects = [source.model_copy(update={"id": "left", "label": "White desk beside window", "relative_location": "Beside the window", "color": None}),
        source.model_copy(update={"id": "right", "label": "White desk on right wall", "relative_location": "Along right desk wall", "color": None})]
    analysis.observations.openings = []
    scene.objects = [desk.model_copy(update={"id": key}) for key in ["left", "right"]]
    scene.openings = []
    case = reference([{"id": "desk_a", "types": ["desk"]}, {"id": "desk_b", "types": ["desk"]}],
        [{"kind": "near", "subject": "desk_a", "target": "desk_b"}])
    visual = {"entities": [{"id": "desk_a", "visible_location": "Left workstation nearer window."},
                            {"id": "desk_b", "visible_location": "Right workstation beside cabinet."}]}
    matches = match_identities(case, analysis, scene, visual)
    assert matches.final == {"desk_a": "left", "desk_b": "right"}
    assert not matches.ambiguous
    # Candidate pose must not be used to decide the mapping.
    scene.objects.reverse()
    scene.objects[0].position.x = 100.
    assert match_identities(case, analysis, scene, visual) == matches


def test_explicit_pendant_group_requires_all_observed_members_to_survive():
    _, analysis, scene = load("synthetic_study")
    source = next(obj for obj in analysis.observations.objects if obj.type == "ceiling_light")
    light = next(obj for obj in scene.objects if obj.type == "ceiling_light")
    analysis.observations.objects = [source.model_copy(update={"id": key, "label": "Dining pendant"}) for key in ["one", "two"]]
    analysis.observations.openings = []
    scene.objects = [light.model_copy(deep=True, update={"id": key}) for key in ["one", "two"]]
    scene.objects[1].position.x += 1.
    scene.openings = []
    case = reference([{"id": "fixture", "types": ["ceiling_light", "chandelier"],
                       "label_terms": ["pendant", "dining"], "segmented_fixture": True}])
    matches, _, metrics = evaluate(case, analysis, scene)
    assert not matches.ambiguous
    assert matches.groups == {"fixture": ("one", "two")}
    assert metrics["major_object_coverage"] == 1
    scene.objects.pop()
    _, _, metrics = evaluate(case, analysis, scene)
    assert metrics["major_object_coverage"] == 0
    assert "incomplete_fixture_group:fixture" in metrics["fidelity_issues"]
    # This permission cannot merge independently constrained lights.
    case.relationships = [ReferenceRelation(kind="near", subject="fixture", target="fixture")]
    with pytest.raises(ValueError, match="isolated"):
        match_identities(case, analysis, scene)


@pytest.mark.parametrize("integrated", [True, False])
def test_hutch_component_requires_real_catalog_geometry_on_the_correct_host(integrated):
    _, analysis, scene = load("synthetic_study")
    desk = next(obj for obj in scene.objects if obj.type == "desk")
    desk.asset_id = "desk_shelved_01" if integrated else "desk_standard_01"
    scene.objects, scene.openings = [desk], []
    analysis.observations.objects = [next(obj for obj in analysis.observations.objects if obj.type == "desk")]
    analysis.observations.openings = []
    case = reference([{"id": "desk", "types": ["desk"]},
        {"id": "hutch", "types": ["shelf"], "component_of": "desk", "component_part_names": ["hutch_board"]}])
    visual = {"entities": [{"id": "desk", "importance": "major"},
        {"id": "hutch", "importance": "secondary", "support_object_id": "desk"}]}
    matches, report, metrics = evaluate(case, analysis, scene, visual)
    assert ("hutch" in matches.components) is integrated
    assert metrics["support_relationships"] == int(integrated)
    inventory = {entry.expected_id: entry for entry in report.inventory}
    assert inventory["hutch"].retained is integrated
    if integrated:
        assert inventory["hutch"].observed_id == inventory["desk"].observed_id
        assert report.major_object_coverage == 1
    else:
        assert report.major_object_coverage == .5


@pytest.mark.parametrize("asset,passes", [("kitchen_cabinet_standard_01", False), ("kitchen_counter_sink_01", True)])
def test_sink_words_do_not_supply_a_basin_but_actual_components_do(asset, passes):
    _, _, scene = load("synthetic_study")
    template = next(obj for obj in scene.objects if obj.type == "desk")
    counter = template.model_copy(deep=True, update={"id": "counter", "asset_id": asset,
        "type": "kitchen_cabinet" if asset.startswith("kitchen_cabinet") else "kitchen_counter"})
    scene.objects, scene.openings = [counter], []
    case = reference([{"id": "sink_run", "types": ["kitchen_counter", "kitchen_cabinet"], "required_features": ["basin", "faucet"]}])
    _, report, metrics = evaluate(case, None, scene)
    assert report.feature_checks == {"sink_run:basin": passes, "sink_run:faucet": passes}
    assert bool([issue for issue in metrics["fidelity_issues"] if issue.startswith("missing_functional_feature:")]) is not passes
    if not passes:
        # A decorative bowl on the worktop is not an installed kitchen sink.
        bowl = counter.model_copy(update={"id": "bowl", "type": "household_accessory", "asset_id": "household_accessory_bowl_01",
            "placement": "surface", "support_object_id": "counter"})
        scene.objects.append(bowl)
        report = evaluate_reconstruction(case, None, scene, identity_matches={"sink_run": "counter"})
        assert not report.feature_checks["sink_run:basin"]


def test_calibration_keeps_frozen_inputs_and_checksums_and_is_offline(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline calibration attempted network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    path = ROOT / "data/evals/manifest.json"
    before = sha256(path.read_bytes()).hexdigest()
    dataset = load_dataset(path)
    original_refs = deepcopy(dataset.references)
    calibrated, evidence = apply_calibration(dataset, ROOT / "data/evals/calibration-v1.json")
    assert sha256(path.read_bytes()).hexdigest() == before
    assert calibrated.entries == dataset.entries and calibrated.manifest == dataset.manifest
    assert dataset.references == original_refs
    assert calibrated.references["kitchen_01"].objects[0].required_features == ["basin", "faucet"]
    assert evidence["sha256"] and evidence["version"]
    bad = json.loads((ROOT / "data/evals/calibration-v1.json").read_text())
    bad["dataset_fingerprint_sha256"] = "0" * 64
    target = tmp_path / "wrong.json"
    target.write_text(json.dumps(bad))
    with pytest.raises(EvaluationInputError, match="calibration"):
        apply_calibration(dataset, target)


def test_tied_surplus_chair_can_be_interchangeable_without_hiding_distinct_relations():
    _, analysis, scene = load("synthetic_study")
    source = next(obj for obj in analysis.observations.objects if obj.type == "chair")
    chair = next(obj for obj in scene.objects if obj.type == "chair")
    analysis.observations.objects = [source.model_copy(update={"id": key, "label": label,
        "relative_location": None, "color": None, "notes": []}) for key, label in
        [("one", "Left chair"), ("two", "Right chair"), ("three", "Right chair")]]
    analysis.observations.openings = []
    scene.objects = [chair.model_copy(deep=True, update={"id": key}) for key in ["one", "two", "three"]]
    scene.openings = []
    case = reference([{"id": "a", "types": ["chair"]}, {"id": "b", "types": ["chair"]},
        {"id": "table", "types": ["table"]}],
        [{"kind": "near", "subject": key, "target": "table"} for key in ["a", "b"]])
    visual = {"entities": [{"id": "a", "visible_location": "Left"}, {"id": "b", "visible_location": "Right"}]}
    identities = match_identities(case, analysis, scene, visual)
    assert not identities.ambiguous
    assert len(set(identities.final.values())) == 2  # The third chair is still an unmatched extra.
    case.relationships[1].kind = "faces"
    assert "b" in match_identities(case, analysis, scene, visual).ambiguous


def test_real_sink_insert_can_supply_features_only_to_its_own_host():
    _, _, scene = load("synthetic_study")
    template = next(obj for obj in scene.objects if obj.type == "desk")
    counter = template.model_copy(update={"id": "counter", "type": "kitchen_counter", "asset_id": "kitchen_counter_island_01"})
    sink = template.model_copy(update={"id": "insert", "type": "sink", "asset_id": "sink_round_vessel_01",
        "placement": "surface", "support_object_id": "counter"})
    scene.objects, scene.openings = [counter, sink], []
    case = reference([{"id": "run", "types": ["kitchen_counter"], "required_features": ["basin", "faucet"]}])
    result = evaluate_reconstruction(case, None, scene, identity_matches={"run": "counter"})
    assert result.feature_checks == {"run:basin": True, "run:faucet": False}
    sink.support_object_id = "some_other_counter"
    result = evaluate_reconstruction(case, None, scene, identity_matches={"run": "counter"})
    assert not any(result.feature_checks.values())
