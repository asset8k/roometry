"""Truthful function scores and no-cost optimization prerequisites."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from roometry.assessment.evidence import assessment_evidence, evidence_for_run
from roometry.assessment.metrics import measure_room
from roometry.assessment.readiness import reconstruction_gaps
from roometry.optimization.policy import meaningful_gain
from roometry.optimization.schemas import StartOptimizationRequest
from roometry.scenes.validation import validate_geometry
from tests.test_coherent_reconstruction import office
from tests.test_optimization_runs import reconstructed, request
from tests import test_vision_api as api

anyio_backend = api.anyio_backend
isolated_environment = api.isolated_environment
harness = api.harness


def observed(analysis, scene, missing=()):
    proposal=SimpleNamespace(unresolved_entities=[SimpleNamespace(entity_id=key) for key in missing],estimated_axes=[])
    return SimpleNamespace(analysis=analysis,proposal=proposal,working_scene=scene)


def value(card,key):
    return next(m.value for m in card.metrics if m.key==key)


def test_workspace_averages_all_stations_and_requires_their_own_paired_chairs():
    analysis,scene=office()
    evidence=assessment_evidence(analysis)
    good=measure_room(scene,evidence=evidence)
    assert good.workspace.score==10.
    assert len(good.workspace.entity_ids)==2
    bad=scene.model_copy(deep=True)
    chair=next(obj for obj in bad.objects if obj.id=='chair_a')
    chair.position.x=chair.position.z=.5
    assert validate_geometry(bad).valid
    score=measure_room(bad,evidence=evidence)
    assert score.workspace.score==7.5
    assert value(score.workspace,'usable_workstation_count')==1
    assert len(score.workspace.entity_ids)==2
    assert reconstruction_gaps(observed(analysis,bad),bad)
    # A fitting chair from the second desk is not the photographed first chair.
    next(obj for obj in bad.objects if obj.id=='chair_b').position.x=1.2
    assert measure_room(bad,evidence=evidence).workspace.score==5.
    assert scene==office()[1]


def test_missing_display_and_missing_second_desk_are_unassessed_not_ten_out_of_ten():
    analysis,scene=office()
    evidence=assessment_evidence(analysis)
    for ids in [{'screen'},{'desk_b'}]:
        incomplete=scene.model_copy(deep=True)
        incomplete.objects=[obj for obj in incomplete.objects if obj.id not in ids]
        score=measure_room(incomplete,evidence=evidence)
        assert score.workspace.score is None and score.workspace.status=='unassessed'
        assert reconstruction_gaps(observed(analysis,incomplete,ids),incomplete)


def test_missing_principal_furniture_cannot_earn_an_empty_floor_win_but_can_be_restored():
    analysis,scene=office()
    missing=scene.model_copy(deep=True)
    missing.objects=[obj for obj in missing.objects if obj.id not in {'desk_b','chair_b'}]
    state=observed(analysis,missing,['desk_b','chair_b'])
    evidence=evidence_for_run(state)
    score=measure_room(missing,evidence=evidence)
    assert score.openness.score is None
    assert score.workspace.score is None
    assert score.openness.confidence=='low'
    # Historical unresolved receipts cannot hide a successful explicit restoration.
    assert measure_room(scene,evidence=evidence).openness.score is not None
    assert measure_room(scene,evidence=evidence).workspace.score==10.
    assert not reconstruction_gaps(state,scene)


def test_missing_minor_wall_detail_does_not_block_goals_or_hide_usable_workspaces():
    analysis,scene=office()
    trim=analysis.observations.objects[0].model_copy(update={'id':'trim','type':'wall_panel','label':'Accent trim',
        'placement':'wall','appears_fixed':True,'support_object_id':None})
    analysis.observations.objects.append(trim)
    state=observed(analysis,scene,['trim'])
    assert not reconstruction_gaps(state,scene)
    assert measure_room(scene,evidence=evidence_for_run(state)).workspace.score==10.


def test_unknown_function_is_not_fabricated_zero_and_new_workspace_needs_a_usable_chair():
    from tests.test_layout_scoring import case,desk
    from tests.test_component_geometry import model
    before=case()
    without_chair=case([desk()])
    with_chair=case([desk(),model('chair_office_01','seat',x=2.,z=2.9,angle=3.141592653589793)])
    source=SimpleNamespace(analysis=None,proposal=None)
    run=SimpleNamespace(source_scene=before,result_scene=None,focus='workspace',instruction=None)
    a=measure_room(without_chair)
    assert a.workspace.score<=5.
    assert not meaningful_gain(run,SimpleNamespace(assessment=a),source)
    b=measure_room(with_chair)
    assert b.workspace.score>=6.
    assert meaningful_gain(run,SimpleNamespace(assessment=b),source)


@pytest.mark.anyio
@pytest.mark.parametrize('focus',['openness','storage','workspace','aesthetics'])
async def test_missing_major_baseline_is_rejected_before_paid_work_but_remains_readable(harness,focus,monkeypatch):
    app,client,calls,_=harness
    runs,owner,state=await reconstructed(harness)
    broken=state.model_copy(deep=True)
    broken.working_scene.objects=[o for o in broken.working_scene.objects if o.id!='bed_observed']
    read=runs.read
    async def read_snapshot(run_id,identity):
        original=await read(run_id,identity)
        return broken.model_copy(deep=True) if run_id==state.run_id else original
    monkeypatch.setattr(runs,'read',read_snapshot)
    count=len(calls)
    response=await client.post(f'/api/agent-runs/{state.run_id}/optimizations',json=request(focus))
    assert response.status_code==409
    error=response.json()['detail'][0]
    assert error['code']=='reconstruction_required' and 'bed' in error['message'].lower()
    assert 'Edit layout' in error['message'] and 'chat' in error['message']
    assert len(calls)==count and not app.state.optimization_runs._runs
    assert (await runs.read(state.run_id,owner)).working_scene==broken.working_scene
    # Explicit chat restoration is a separate intent, with no fabricated score gain.
    child=await app.state.optimization_runs.prepare(state.run_id,owner,StartOptimizationRequest(
        request_id=str(uuid4()),focus='workspace',instruction='Restore the missing bed.',context={'scene':broken.working_scene}))
    assert child.status=='prepared' and len(calls)==count


def test_integrated_shower_head_does_not_become_a_false_baseline_blocker():
    from tests.test_coherent_reconstruction import fixture_conflict
    analysis,scene=fixture_conflict()
    analysis.observations.objects=analysis.observations.objects[:1]
    scene.objects=scene.objects[:1]
    shower=analysis.observations.objects[0]
    shower.wall_id='window_wall'
    fixture=shower.model_copy(update={'id':'fixture','type':'shower_fixture','placement':'wall',
        'label':'Shower head and mixer','notes':[]})
    analysis.observations.objects.append(fixture)
    state=observed(analysis,scene,['fixture'])
    assert not reconstruction_gaps(state,scene)
    # The unresolved observation remains disclosed, even though its function is visible.
    assert state.proposal.unresolved_entities[0].entity_id=='fixture'
    fixture.label='Handheld shower and mixer'
    assert reconstruction_gaps(state,scene)==[fixture.label]
    fixture.label='Shower head and mixer';fixture.wall_id='other_wall'
    assert reconstruction_gaps(state,scene)==[fixture.label]
    fixture.wall_id='window_wall'
    analysis.observations.objects.append(shower.model_copy(update={'id':'other_shower'}))
    assert fixture.label in reconstruction_gaps(state,scene)
