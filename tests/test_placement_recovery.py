"""Synthetic and non-personal failures recover through the real policy and graph."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from roometry.agents.budget import RunBudget
from roometry.optimization.graph import OptimizationContext, build_graph
from roometry.optimization.placements import plan_placements
from roometry.optimization.policy import evaluate, meaningful_gain
from roometry.optimization.schemas import CandidateProposal, OptimizationRun
from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry
from tests import test_vision_api as api
from tests.test_optimization_execution import (
    change,
    policy_run,
    proposal,
    provider_action,
    review,
)
from tests.test_optimization_redesign import source as plain_source

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness
ROOT = Path(__file__).resolve().parents[1] / "data/fixtures/placement-regressions"


def source(scene):
    result = plain_source()
    # An architectural sill must not be counted as furniture storage.
    result.analysis = SimpleNamespace(
        observations=SimpleNamespace(
            objects=[
                SimpleNamespace(id=o.id, type=o.type, label="Window sill")
                for o in scene.objects
                if "sill" in o.id
            ],
            openings=[],
        )
    )
    return result


@pytest.fixture(
    scope="module",
    params=[
        ("synthetic_openness", "openness"),
        ("synthetic_storage", "storage"),
        ("living_room", "openness"),
    ],
)
def recovered(request):
    name, focus = request.param
    scene = RoomScene.model_validate_json((ROOT / f"{name}.json").read_text())
    run, observed = policy_run(scene, focus), source(scene)
    before = scene.model_dump_json()
    result = plan_placements(run, observed)
    assert scene.model_dump_json() == before
    return run, observed, result


def test_saved_failed_room_has_a_real_collision_free_meaningful_improvement(recovered):
    run, observed, result = recovered
    assert result.proposals, (run.focus, result.checked)
    for patch in result.proposals:
        outcome = evaluate(run, patch, observed)
        assert not outcome.issues and meaningful_gain(run, outcome, observed)
        assert validate_geometry(outcome.scene).valid
        assert outcome.after_score >= outcome.before_score + 0.5
        assert outcome.scene.room == run.source_scene.room
        assert outcome.scene.openings == run.source_scene.openings
        by_id = {o.id: o for o in outcome.scene.objects}
        assert all(
            by_id[o.id] == o for o in run.source_scene.objects if o.fixed or o.locked
        )
        if run.focus == "storage":
            assert patch.additions or patch.changes


@pytest.mark.anyio
async def test_rejected_model_coordinates_recover_without_another_proposal_call(
    harness, recovered, monkeypatch
):
    app, _, calls, actions = harness
    run, observed, result = recovered
    # The real bounded search above is reused to isolate graph call accounting.
    monkeypatch.setattr(
        "roometry.optimization.graph.plan_placements", lambda *a, **k: result
    )
    root = next(
        o for o in run.source_scene.objects if o.placement == "floor" and not o.fixed
    )
    bad = CandidateProposal.model_validate(
        proposal([change(root.id, position={"x": 100.0, "z": 100.0})])
    )
    actions.extend([provider_action(bad.model_dump(mode="json")), review()])

    async def authorize():
        pass

    context = OptimizationContext(
        observed,
        authorize,
        lambda: False,
        RunBudget(run.limits, run.usage, authorize, lambda: False),
        app.state.optimization_runs.provider,
        None,
    )
    graph = build_graph(InMemorySaver())
    state = await graph.ainvoke(
        {"run": run.model_dump()},
        {"configurable": {"thread_id": run.run_id}},
        context=context,
    )
    final = OptimizationRun.model_validate(state["run"])
    assert final.status == "completed"
    assert [a.outcome for a in final.attempts] == ["rejected", "accepted"]
    assert final.usage.model_calls == len(calls) == 2
    assert final.attempts[-1].review.preferred == "candidate"
    assert (
        final.result_scene != run.source_scene
        and validate_geometry(final.result_scene).valid
    )
    # Retrying the completed checkpoint does not dispatch another paid request.
    await graph.ainvoke(
        None, {"configurable": {"thread_id": run.run_id}}, context=context
    )
    assert len(calls) == 2


def test_search_never_repurposes_chat_instructions_or_movable_locks():
    scene = RoomScene.model_validate_json((ROOT / "synthetic_openness.json").read_text())
    run, observed = policy_run(scene), source(scene)
    assert not plan_placements(
        run.model_copy(update={"instruction": "Add a television"}), observed
    ).proposals
    for o in scene.objects:
        o.locked = True
    locked = policy_run(scene)
    assert not plan_placements(locked, observed).proposals
    assert plan_placements(run, observed, max_checked=0).checked == 0


@pytest.mark.anyio
async def test_local_option_still_requires_visual_approval(
    harness, recovered, monkeypatch
):
    app, _, calls, actions = harness
    run, observed, options = recovered
    run = run.model_copy(update={"max_candidates": 2})
    monkeypatch.setattr(
        "roometry.optimization.graph.plan_placements", lambda *a, **k: options
    )
    root = next(
        o for o in run.source_scene.objects if o.placement == "floor" and not o.fixed
    )
    bad = CandidateProposal.model_validate(
        proposal([change(root.id, position={"x": 100.0, "z": 100.0})])
    )
    actions.extend(
        [provider_action(bad.model_dump(mode="json")), review(preferred="original")]
    )

    async def authorize():
        pass

    context = OptimizationContext(
        observed,
        authorize,
        lambda: False,
        RunBudget(run.limits, run.usage, authorize, lambda: False),
        app.state.optimization_runs.provider,
        None,
    )
    state = await build_graph(InMemorySaver()).ainvoke(
        {"run": run.model_dump()},
        {"configurable": {"thread_id": run.run_id}},
        context=context,
    )
    final = OptimizationRun.model_validate(state["run"])
    assert final.status == "no_improvement" and final.result_scene is None
    assert final.source_scene == run.source_scene
    assert final.attempts[-1].review.preferred == "original"
    assert len(calls) == 2


def test_new_storage_is_placed_at_the_perimeter_instead_of_gaming_capacity_in_the_center():
    from roometry.optimization.placements import perimeter_storage
    from tests.test_layout_scoring import case

    scene = case([])
    run, observed = policy_run(scene, "storage"), source(scene)
    result = plan_placements(run, observed, max_checked=400)
    assert result.proposals
    for patch in result.proposals:
        final = evaluate(run, patch, observed)
        assert not final.issues and meaningful_gain(run, final, observed)
        assert patch.additions
        assert all(perimeter_storage(final.scene, obj) for obj in final.scene.objects)


@pytest.mark.anyio
@pytest.mark.parametrize("cancel_search", [False, True])
async def test_empty_model_proposal_recovers_and_cancellation_during_search_stops_before_review(
    harness, monkeypatch, cancel_search
):
    from roometry.optimization.placements import PlacementSearch
    from tests.test_optimization_redesign import scenario

    app, _, calls, actions = harness
    scene, plan = scenario("openness")
    run = policy_run(scene)
    cancelled = False

    def search(*args, **kwargs):
        nonlocal cancelled
        cancelled = cancel_search
        return PlacementSearch(proposals=(plan,))

    monkeypatch.setattr("roometry.optimization.graph.plan_placements", search)
    actions.append(
        provider_action(
            CandidateProposal(
                changes=[],
                summary="No arrangement proposed.",
                suggestions=[],
                finish=True,
            ).model_dump(mode="json")
        )
    )
    if not cancel_search:
        actions.append(review())

    async def authorize():
        pass

    context = OptimizationContext(
        plain_source(),
        authorize,
        lambda: cancelled,
        RunBudget(run.limits, run.usage, authorize, lambda: cancelled),
        app.state.optimization_runs.provider,
        None,
    )
    state = await build_graph(InMemorySaver()).ainvoke(
        {"run": run.model_dump()},
        {"configurable": {"thread_id": run.run_id}},
        context=context,
    )
    final = OptimizationRun.model_validate(state["run"])
    assert final.status == ("cancelled" if cancel_search else "completed")
    assert len(calls) == (1 if cancel_search else 2)
    assert final.source_scene == scene
    assert (final.result_scene is None) == cancel_search


def test_new_tall_storage_cannot_cover_a_visible_window_but_low_storage_can_fit_below_it():
    from roometry.optimization.placements import perimeter_storage
    from roometry.scenes.schemas import Opening
    from tests.test_component_geometry import model
    from tests.test_layout_scoring import case

    scene = case([])
    scene.openings.append(
        Opening(
            id="window",
            type="window",
            wall="north",
            offset_m=2.0,
            width_m=2.0,
            height_m=1.2,
            sill_height_m=1.0,
            swing="none",
            hinge=None,
            provenance="photo_observed",
            measurements_source="estimated",
        )
    )
    tall = model("shelf_standard_01", "shelf", x=3.0, z=0.215)
    low = model("shelf_low_metal_01", "shelf", x=3.0, z=0.19)
    assert not perimeter_storage(scene, tall)
    assert perimeter_storage(scene, low)
