"""Offline benchmark plumbing, negative controls and honest accounting."""

from itertools import permutations
import json
from math import pi
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from roometry.evaluation.benchmark import run_benchmark
from roometry.evaluation.dataset import EvaluationInputError, load_dataset, load_recordings, local_path
from roometry.evaluation.matching import _assignment, match_identities
from roometry.evaluation.metrics import check_measurements, evaluate_improvement
from roometry.evaluation.reporting import write_report
from roometry.evaluation.reviews import attach_reviews, blind_sides, review_template
from roometry.evaluation.runner import evaluate_reconstruction
from roometry.evaluation.schemas import RecordedTrial, ReconstructionCase
from roometry.evaluation.telemetry import collect_telemetry
from roometry.scenes.hashing import scene_hash
from roometry.scenes.schemas import Opening
from tests.test_reconstruction_evaluation import load


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/evals/manifest.json"


def dump(path, data):
    path.write_text(json.dumps(data))
    return path


def recording_index(tmp_path, trials, **kwargs):
    return dump(tmp_path / "recordings.json", {
        "schema_version": 1, "recording_mode": "mock",
        "dataset_fingerprint_sha256": json.loads(MANIFEST.read_text())["dataset_fingerprint_sha256"],
        "trials": trials, **kwargs,
    })


def empty_scene():
    _, _, scene = load("living_room_tv")
    scene.objects = []
    shared = {"provenance": "photo_observed", "measurements_source": "estimated"}
    scene.openings = [
        Opening(id="window", type="window", wall="north", offset_m=1.5, width_m=1.1, height_m=1.5, sill_height_m=.7, swing="none", hinge=None, **shared),
        Opening(id="door", type="door", wall="north", offset_m=.2, width_m=.8, height_m=2., sill_height_m=0., swing="unknown", hinge="unknown", **shared),
    ]
    return scene


def run_data(scene=None, **kwargs):
    return {"run_id": "test-run", "status": "completed", "persistence": "account",
            "analysis": None, "working_scene": (scene or empty_scene()).model_dump(mode="json"),
            "usage": {"model_calls": 1, "tool_calls": 2},
            "limits": {"max_model_calls": 8, "max_tool_calls": 30}, **kwargs}


def trial(key="empty", case="living_07", **kwargs):
    return {"evaluation_id": key, "case_id": case, "kind": "reconstruction", "run": f"{key}.json", **kwargs}


def call(**kwargs):
    return {"response_id": "response-1", "run_id": "test-run", "model": "gpt-6.1-terra",
            "operation": "scene_proposal", "input_tokens": 1000, "cached_input_tokens": 0,
            "cache_write_tokens": 0, "output_tokens": 100, "outcome": "completed", **kwargs}


def test_batch_never_connects_and_failures_stay_in_denominator(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("An offline evaluator must not connect to the network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    dump(tmp_path / "empty.json", run_data())
    dump(tmp_path / "bad.json", {"status": "failed", "analysis": {"malformed": True}, "working_scene": None})
    dump(tmp_path / "repeat.json", run_data())
    index = recording_index(tmp_path, [trial(), trial("bad", "living_01"), trial("repeat", repeat=True), trial("missing", "office_01")])
    _, report = run_benchmark(MANIFEST, index)
    values = report["reconstruction"]
    assert values["planned"] == 25 and values["attempted"] == 3 and values["not_run"] == 22
    assert values["repeated_recordings"] == 1
    assert values["usable_completion_rate"] == pytest.approx(1 / 3)
    assert values["overall_success_rate"] is None
    rows = {row["evaluation_id"]: row for row in report["trials"]}
    assert rows["empty"]["code_pass"] and rows["empty"]["visual_review"]["status"] == "pending"
    assert "invalid_analysis" in rows["bad"]["issues"]
    assert "missing_or_invalid_run_artifact" in rows["missing"]["issues"]
    assert report["spending"]["known_estimated_usd"] is None
    assert sum(group["planned"] for group in report["improvements"].values()) == 8
    assert report["by_source"]["photographed"]["attempted"] == 1


def test_photo_verified_extra_does_not_override_other_checks(tmp_path):
    from tests.test_component_geometry import model
    candidate = empty_scene()
    candidate.objects = [model("side_table_standard_01", "small_table", x=1., z=2.)]
    dump(tmp_path / "empty.json", run_data(candidate))
    index = recording_index(tmp_path, [trial()])
    _, before = run_benchmark(MANIFEST, index)
    assert before["trials"][0]["metrics"]["unsupported_extra_major_objects"] == ["small_table"]
    evidence = {"small_table": "Photo review confirms the unannotated small table."}
    index = recording_index(tmp_path, [trial(verified_visible_extras=evidence)])
    _, after = run_benchmark(MANIFEST, index)
    old, new = before["trials"][0]["metrics"], after["trials"][0]["metrics"]
    assert new["unsupported_extra_major_objects"] == []
    assert new["photo_verified_visible_extras"] == evidence
    assert new["major_object_coverage"] == old["major_object_coverage"]
    assert new["hard_issues"] == old["hard_issues"]
    candidate.objects[0].position.x = -1.
    dump(tmp_path / "empty.json", run_data(candidate))
    _, invalid = run_benchmark(MANIFEST, index)
    assert not invalid["trials"][0]["code_pass"]
    assert "object_outside_room" in invalid["trials"][0]["metrics"]["hard_issues"]


@pytest.mark.parametrize("entity_id", ["not_in_scene", "window"])
def test_photo_verified_extra_cannot_excuse_unknown_or_matched_anchors(tmp_path, entity_id):
    dump(tmp_path / "empty.json", run_data())
    index = recording_index(tmp_path, [trial(verified_visible_extras={entity_id: "Review must name an unmatched retained object."})])
    _, result = run_benchmark(MANIFEST, index)
    assert not result["trials"][0]["code_pass"]
    assert "invalid_trial_evidence" in result["trials"][0]["issues"]


@pytest.mark.parametrize("change", ["wrong_fingerprint", "duplicate", "duplicate_improvement", "traversal", "wrong_parent", "repeat_without_primary"])
def test_invalid_recording_index_cannot_distort_totals(tmp_path, change):
    entries = [trial()]
    kwargs = {}
    if change == "wrong_fingerprint":
        kwargs["dataset_fingerprint_sha256"] = "0" * 64
    elif change == "duplicate":
        entries.append(trial("another"))
    elif change == "duplicate_improvement":
        entries = [trial("base", "living_02"), trial("a", "living_02", kind="improvement", parent_evaluation_id="base"), trial("b", "living_02", kind="improvement", parent_evaluation_id="base")]
    elif change == "traversal":
        entries[0]["run"] = "../private.json"
    elif change == "wrong_parent":
        entries.append(trial("improve", "living_02", kind="improvement", parent_evaluation_id="empty"))
    else:
        entries[0]["repeat"] = True
    with pytest.raises(EvaluationInputError):
        load_recordings(recording_index(tmp_path, entries, **kwargs), load_dataset(MANIFEST))


def test_dataset_checksum_and_symlink_boundary(tmp_path):
    manifest = json.loads(MANIFEST.read_text())
    manifest["cases"][0]["input"]["room_type"] = "office"
    with pytest.raises(EvaluationInputError, match="fingerprint"):
        load_dataset(dump(tmp_path / "manifest.json", manifest))
    (tmp_path / "escape").symlink_to(ROOT / "data")
    with pytest.raises(EvaluationInputError, match="outside"):
        local_path(tmp_path, "escape/evals/manifest.json")


def test_assignment_is_global_and_not_greedy():
    scores = [[9, 8, 0, 0], [9, 1, 0, 0]]
    actual = _assignment(scores)
    best = max(sum(scores[i][j] for i, j in enumerate(cols)) for cols in permutations(range(4), 2))
    assert sum(scores[i][j] for i, j in enumerate(actual)) == best == 17


def test_identity_matching_is_order_independent_and_does_not_hide_wrong_colors_or_rotation():
    case, analysis, scene = load("synthetic_study")
    mapping = {item.id: f"entity_{n}" for n, item in enumerate([*scene.objects, *scene.openings])}
    for item in [*analysis.observations.objects, *analysis.observations.openings, *scene.objects, *scene.openings]:
        item.id = mapping[item.id]
    scene.objects.reverse()
    analysis.observations.objects.reverse()
    next(obj for obj in scene.objects if obj.type == "bed").color = "#000000"
    next(obj for obj in scene.objects if obj.type == "chair").rotation_y_rad += pi
    identities = match_identities(case, analysis, scene)
    assert identities.final == mapping
    assert not identities.ambiguous
    result = evaluate_reconstruction(case, analysis, scene, identity_matches=identities.final, observation_matches=identities.observations)
    assert result.major_object_coverage == 1
    assert result.color_consistency < 1 and result.orientation < 1


def test_identical_windows_are_interchangeable_but_distinct_instances_need_review():
    case, analysis, scene = load("living_room_tv")
    assert not match_identities(case, analysis, scene).ambiguous
    # Two textually identical observations cannot identify unequal reference
    # colors. Candidate colors/locations must not be used to make scores pass.
    expected = ReconstructionCase(id="repeated", photo_files=[], annotation_notes=[], relationships=[], objects=[
        {"id": "dark", "types": ["sofa"], "color": "#000000"},
        {"id": "light", "types": ["sofa"], "color": "#FFFFFF"},
    ])
    source = analysis.observations.objects[0].model_copy(deep=True)
    source.type, source.label, source.color = "sofa", "Sofa", None
    analysis.observations.objects = [source.model_copy(update={"id": "one"}), source.model_copy(update={"id": "two"})]
    analysis.observations.openings = []
    sofa = next(obj for obj in scene.objects if obj.type == "sofa")
    scene.objects = [sofa.model_copy(update={"id": "one"}), sofa.model_copy(update={"id": "two"})]
    scene.openings = []
    unresolved = match_identities(expected, analysis, scene)
    assert unresolved.ambiguous == {"dark", "light"}
    fixed = match_identities(expected, analysis, scene, overrides={"dark": "two", "light": "one"})
    assert not fixed.ambiguous
    with pytest.raises(ValueError, match="reuse"):
        match_identities(expected, analysis, scene, overrides={"dark": "one", "light": "one"})


def test_user_measurements_are_hard_constraints_but_unknown_dimensions_are_not_zero():
    scene = empty_scene()
    assert check_measurements({"input": {"dimensions": None}}, {}, scene) == []
    entry = {"input": {"dimensions": {"width_m": 4.}}}
    assert "supplied_measurement_changed:width_m" in check_measurements(entry, {}, scene)
    scene.room.measurements_source = "user_confirmed"
    assert "unconfirmed_estimate_claimed_as_user_measurement" in check_measurements({"input": {}}, {}, scene)


def test_costs_include_failed_calls_deduplicate_and_do_not_invent_missing_usage(tmp_path):
    dump(tmp_path / "cost.json", {"calls": [call(outcome="incomplete"), call(outcome="incomplete"), call(response_id="response-2", run_id="repair-run", operation="scene_repair")]})
    entries = [RecordedTrial(**trial(cost="cost.json")), RecordedTrial(**trial("repair", "living_01", cost="cost.json"))]
    raws = {"empty": run_data(), "repair": run_data(run_id="repair-run")}
    individual, total = collect_telemetry(tmp_path, entries, raws)
    assert total["complete_usage"] and total["unique_recorded_calls"] == 2
    assert total["duplicate_response_rows_removed"] == 1
    assert float(total["known_estimated_usd"]) == pytest.approx(.003)
    assert float(individual["empty"]["estimated_usd"]) == pytest.approx(.0015)
    assert individual["repair"]["repairs"] == 1
    dump(tmp_path / "cost.json", {"calls": [call(), call(input_tokens=2000)]})
    _, total = collect_telemetry(tmp_path, entries[:1], raws)
    assert not total["complete_usage"] and total["unpriced_calls"] == 1
    assert total["lower_bound_only"]
    dump(tmp_path / "cost.json", {"calls": [call(model="unknown-model")]})
    individual, total = collect_telemetry(tmp_path, entries[:1], {"empty": run_data(usage="invalid", limits=[])})
    assert individual["empty"]["model_calls"] is None
    assert not total["complete_usage"]


@pytest.mark.parametrize("focus", ["openness", "storage", "workspace", "aesthetics"])
def test_all_four_improvement_metrics_use_retained_scenes_not_narrative_scores(focus):
    from tests.test_optimization_redesign import scenario, source
    from tests.test_optimization_execution import policy_run
    from roometry.optimization.policy import evaluate
    before, proposal = scenario(focus)
    after = evaluate(policy_run(before, focus), proposal, source()).scene
    raw = {"source_scene": before.model_dump(mode="json"), "source_scene_hash": scene_hash(before),
           "result_scene": after.model_dump(mode="json"), "status": "completed", "focus": focus}
    values = evaluate_improvement(before, raw, focus)
    assert values["accepted"] and values["changed_objects"]
    assert values["meaningful_proxy_gain"] is (None if focus == "aesthetics" else True)
    assert values["visual_review_required"]
    if focus == "aesthetics":
        assert values["after_proxy_score"] is None
    raw["result_scene"] = before.model_dump(mode="json")
    assert not evaluate_improvement(before, raw, focus)["accepted"]


def test_improvement_cannot_change_locks_room_or_parent_and_plant_shuffle_is_not_a_gain():
    _, _, before = load("living_room_tv")
    after = before.model_copy(deep=True)
    after.objects[0].locked = True
    before.objects[0].locked = True
    after.objects[0].color = "#123456"
    raw = {"source_scene": before.model_dump(mode="json"), "result_scene": after.model_dump(mode="json"),
           "focus": "storage", "status": "completed"}
    values = evaluate_improvement(before, raw, "storage")
    assert not values["accepted"] and "fixed_or_locked_objects_changed" in values["hard_issues"]
    assert not values["meaningful_proxy_gain"]
    assert not evaluate_improvement(empty_scene(), raw, "storage")["source_preserved"]


def test_chat_reviews_are_pending_until_filled_and_stale_items_are_isolated(tmp_path):
    dump(tmp_path / "empty.json", run_data())
    index = recording_index(tmp_path, [trial()])
    _, report = run_benchmark(MANIFEST, index)
    row = next(row for row in report["trials"] if row["evaluation_id"] == "empty")
    template = review_template(report["trials"])
    assert not attach_reviews(report["trials"], template)
    assert row["visual_review"]["status"] == "pending"
    review = template["reviews"][0]
    review.update(scores={key: 2 for key in review["scores"]}, confidence="medium", evidence=["Door and window remain on the same wall; room stays empty."])
    assert not attach_reviews(report["trials"], template)
    assert row["visual_review"]["passed"]
    review["evidence_sha256"] = "0" * 64
    assert attach_reviews(report["trials"], template)
    assert row["visual_review"]["status"] == "invalid"
    assert all(item["visual_review"]["status"] == "pending" for item in report["trials"] if item is not row)


def test_blind_improvement_judge_does_not_convert_a_tie_to_success():
    row = {"evaluation_id": "comparison", "evidence_sha256": "a" * 64, "kind": "improvement", "has_scene": True, "visual_review": {"status": "pending"}}
    document = review_template([row])
    item = document["reviews"][0]
    item.update(preferred="tie", meaningful_change=False, constraints_followed=True, confidence="high", evidence=["Only a small decor move is visible."])
    attach_reviews([row], document)
    assert not row["visual_review"]["passed"]
    item.update(preferred=next(side for side, role in blind_sides(row).items() if role == "candidate"), meaningful_change=True)
    attach_reviews([row], document)
    assert row["visual_review"]["passed"]


def test_live_account_receipts_need_matching_owner_title_and_no_publication(tmp_path):
    dump(tmp_path / "empty.json", run_data())
    receipt = {"room_id": "room-123", "title": "living_07", "account_id": "account", "published": False}
    index = recording_index(tmp_path, [trial(saved_room=receipt)], recording_mode="live", account_id="account")
    _, report = run_benchmark(MANIFEST, index)
    assert report["trials"][0]["account"]["passed"]
    receipt["published"] = True
    recording_index(tmp_path, [trial(saved_room=receipt)], recording_mode="live", account_id="account")
    _, report = run_benchmark(MANIFEST, index)
    assert not report["trials"][0]["code_pass"]


def test_private_report_export_renders_locally_and_refuses_overwrites(tmp_path, monkeypatch):
    from roometry.evaluation import reporting
    monkeypatch.setattr(reporting, "REPOSITORY", tmp_path)
    monkeypatch.setattr(reporting, "OUTPUT_ROOT", tmp_path / "artifacts/evaluations")
    dump(tmp_path / "empty.json", run_data())
    dataset, report = run_benchmark(MANIFEST, recording_index(tmp_path, [trial()]))
    output = tmp_path / "artifacts/evaluations/first"
    exported = write_report(dataset, report, output, render=True)
    assert exported["trials"][0]["metrics"]["render_success"]
    assert len(list(output.rglob("*.png"))) == 3
    assert set(path.name for path in output.iterdir()) >= {"report.json", "results.csv", "summary.md", "visual-review.json", "review-packet.json", "blind-key.json"}
    assert '"_scene"' not in (output / "report.json").read_text()
    assert exported["reconstruction"]["overall_success_rate"] is None
    with pytest.raises(EvaluationInputError, match="already exists"):
        write_report(dataset, report, output)
    with pytest.raises(EvaluationInputError, match="artifacts/evaluations"):
        write_report(dataset, report, tmp_path / "data/evals/public-output")


def test_bad_review_file_or_one_malformed_judgment_does_not_erase_other_results(tmp_path):
    dump(tmp_path / "empty.json", run_data())
    index = recording_index(tmp_path, [trial()])
    broken = tmp_path / "review.json"
    broken.write_text("{broken")
    _, report = run_benchmark(MANIFEST, index, reviews_path=broken)
    assert report["review_import_issues"] == ["missing_or_invalid_review_file"]
    assert report["trials"][0]["code_pass"]
    document = review_template(report["trials"])
    document["reviews"].insert(0, {"evaluation_id": [], "kind": "reconstruction"})
    document["reviews"].append({"evaluation_id": "not_run_living_01", "kind": "reconstruction", "evidence_sha256": "0" * 64})
    assert len(attach_reviews(report["trials"], document)) == 2


def test_positive_visual_review_cannot_override_geometry_or_measurement_failure(tmp_path):
    scene = empty_scene()
    scene.openings[0].offset_m = scene.room.width_m + 1
    dump(tmp_path / "empty.json", run_data(scene))
    index = recording_index(tmp_path, [trial()])
    _, report = run_benchmark(MANIFEST, index)
    document = review_template(report["trials"])
    item = document["reviews"][0]
    item.update(scores={key: 2 for key in item["scores"]}, confidence="high", evidence=["Mock positive judgment to test the geometry veto."])
    _, result = run_benchmark(MANIFEST, index, reviews_path=dump(tmp_path / "review.json", document))
    assert result["trials"][0]["visual_review"]["passed"]
    assert result["trials"][0]["overall_success"] is False


@pytest.mark.parametrize("patch,issue", [
    ({"usage": {"model_calls": 10, "tool_calls": 2}}, "model_or_tool_budget_exceeded"),
    ({"questions": [{}, {}, {}, {}]}, "clarification_cap_exceeded"),
])
def test_flow_contract_failures_cannot_pass_as_success(tmp_path, patch, issue):
    dump(tmp_path / "empty.json", run_data(**patch))
    _, report = run_benchmark(MANIFEST, recording_index(tmp_path, [trial()]))
    assert not report["trials"][0]["code_pass"] and issue in report["trials"][0]["issues"]


def test_live_improvement_inherits_account_ownership_and_saves_version_of_same_room(tmp_path):
    from tests.test_optimization_redesign import scenario, source
    from tests.test_optimization_execution import policy_run
    from roometry.optimization.policy import evaluate
    before, proposal = scenario("storage")
    after = evaluate(policy_run(before, "storage"), proposal, source()).scene
    dump(tmp_path / "base.json", run_data(before))
    dump(tmp_path / "improve.json", {
        "source_scene": before.model_dump(mode="json"), "result_scene": after.model_dump(mode="json"),
        "source_scene_hash": scene_hash(before), "focus": "storage", "status": "completed",
    })
    receipt = {"room_id": "same-room", "title": "kitchen_03", "account_id": "account", "published": False}
    index = recording_index(tmp_path, [trial("base", "kitchen_03", saved_room=receipt),
        trial("improve", "kitchen_03", kind="improvement", parent_evaluation_id="base", saved_room=receipt)], recording_mode="live", account_id="account")
    _, report = run_benchmark(MANIFEST, index)
    row = next(item for item in report["trials"] if item["evaluation_id"] == "improve")
    assert row["account"]["passed"] and row["metrics"]["meaningful_proxy_gain"]
    assert row["overall_success"] is None  # No visual judge yet.


def test_batch_cli_replays_without_loading_provider_clients(tmp_path):
    dump(tmp_path / "empty.json", run_data())
    index = recording_index(tmp_path, [trial()])
    # Fail if this entrypoint initializes app/provider modules or tries a socket.
    command = '''import runpy, socket, sys
def blocked(*args, **kwargs):
    raise AssertionError("Network access from offline CLI")
socket.socket.connect = blocked
sys.argv = ["scripts/run_evals.py", *sys.argv[1:]]
runpy.run_path("scripts/run_evals.py", run_name="__main__")
assert "roometry.main" not in sys.modules
assert "openai" not in sys.modules
'''
    output = ROOT / "artifacts/evaluations" / ("pytest-cli-" + tmp_path.name)
    # Test directories are unique per pytest run, even across repeated invocations.
    from uuid import uuid4
    output = output.with_name(output.name + "-" + uuid4().hex[:8])
    result = subprocess.run([sys.executable, "-c", command, "--manifest", str(MANIFEST),
        "--recordings", str(index), "--output", str(output)], cwd=ROOT, capture_output=True, text=True)
    try:
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["recording_mode"] == "mock" and payload["reconstruction"]["attempted"] == 1
        assert (output / "report.json").exists()
    finally:
        import shutil
        if output.exists():
            shutil.rmtree(output)


@pytest.mark.parametrize('label,title,passed',[
    ('Eval 2','Eval 2 · living_07',True),
    ('Eval 2','living_07',False),
    ('Eval 2','Eval 1 · living_07',False),
    ('Eval 2 pilot','Eval 2 pilot · living_07',True),
])
def test_round_labels_keep_saved_rooms_distinct_without_relaxing_receipt_checks(tmp_path,label,title,passed):
    dump(tmp_path/'empty.json',run_data())
    receipt={'room_id':'room-123','title':title,'account_id':'account','published':False}
    index=recording_index(tmp_path,[trial(saved_room=receipt)],recording_mode='live',account_id='account',run_label=label)
    _,report=run_benchmark(MANIFEST,index)
    assert report['trials'][0]['account']['passed'] is passed
