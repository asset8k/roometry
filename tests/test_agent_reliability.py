"""Regression checks for useful tools and free, honest terminal transitions."""

import pytest

from roometry.agents.nodes import RunContext, finalize
from roometry.agents.state import RunLimits, RunUsage, ToolFeedback
from roometry.agents.tools import available_tools, tool_definitions
from roometry.config import Settings
from pydantic import ValidationError
from tests.test_agent_graph import Room, OWNER
from tests import test_vision_api

anyio_backend = test_vision_api.anyio_backend
isolated_environment = test_vision_api.isolated_environment
harness = test_vision_api.harness
pytestmark = pytest.mark.anyio


async def noop():
    pass


async def populated():
    room = Room()
    async def step(state, context):
        raise AssertionError('No adapter invocation expected')
    initial = await room.runs(step).create(room.prepared.input_id, OWNER)
    return initial.model_copy(update={
        'status': 'running', 'analysis': room.analysis,
        'proposal': room.proposal(), 'working_scene': room.proposal().scene,
        'base_scene': room.proposal().scene,
    })


async def test_inspection_tools_are_once_per_accepted_revision_and_rejected_edits_do_not_reset_them():
    state = await populated()
    inspected = [ToolFeedback(name=name, outcome='success', message='Checked')
                 for name in ('read_scene', 'search_catalog', 'validate_scene', 'score_layout')]
    state = state.model_copy(update={'tool_feedback': inspected})
    assert not {'read_scene', 'search_catalog', 'validate_scene', 'score_layout'} & set(available_tools(state))
    for outcome, changed, expected in [('rejected', [], False), ('success', [], False), ('success', ['chair_a'], True)]:
        updated = state.model_copy(update={'tool_feedback': [*inspected, ToolFeedback(name='move_object', outcome=outcome, message='Result', details={'changed_object_ids': changed})]})
        assert ('validate_scene' in available_tools(updated)) == expected


async def test_question_definitions_include_only_available_kinds_and_existing_eligible_objects():
    state = await populated()
    question = next(tool for tool in tool_definitions(state) if tool['name'] == 'request_clarification')
    properties = question['parameters']['properties']
    assert properties['kind']['enum'] == ['object_mobility']
    candidates = properties['object_id']['enum']
    assert candidates and None not in candidates
    assert all(any(obj.id == key and not obj.locked and not obj.fixed for obj in state.working_scene.objects) for key in candidates)
    assert 'unresolved_sofa' not in candidates
    near_limit = state.model_copy(update={'usage': RunUsage(model_calls=state.limits.max_model_calls - 1)})
    assert 'request_clarification' not in available_tools(near_limit)


async def test_no_useful_action_finalizes_without_a_paid_decision(harness):
    app, _, calls, _ = harness
    state = await populated()
    scene = state.working_scene.model_copy(deep=True)
    for obj in scene.objects:
        obj.fixed = True
    state = state.model_copy(update={
        'working_scene': scene,
        'tool_feedback': [ToolFeedback(name=name, outcome='success', message='Checked')
                          for name in ('read_scene', 'search_catalog', 'validate_scene', 'score_layout')],
    })
    assert available_tools(state) == ['finish']
    context = RunContext(app.state.agent_runs.advance, noop, lambda: False, app.state.agent_runs.now)
    step = await app.state.agent_runs.advance(state, context)
    assert step.action == 'finish' and not calls


@pytest.mark.parametrize('partial', [False, True])
async def test_free_finalization_keeps_budget_reason_and_never_upgrades_a_partial(partial):
    state = await populated()
    room = Room()
    proposal = room.proposal(partial)
    state = state.model_copy(update={
        'status': 'budget_exhausted', 'stop_reason': 'budget_limit',
        'proposal': proposal, 'base_scene': proposal.scene, 'working_scene': proposal.scene,
    })
    result = finalize(state)
    assert result['status'] == ('partial' if partial else 'completed')
    assert result['stop_reason'] == 'budget_limit'
    assert result['pending_question'] is None and result['pending_tool'] is None
    assert finalize(state.model_copy(update={'working_scene': None}))['status'] == 'budget_exhausted'
    assert finalize(state.model_copy(update={'status': 'failed', 'last_error': 'decision_failed'}))['status'] == 'failed'
    invalid = proposal.scene.model_copy(deep=True)
    invalid.objects[0].position.x = 99
    assert finalize(state.model_copy(update={'working_scene': invalid}))['status'] == 'budget_exhausted'


async def test_server_configuration_supplies_larger_separate_budgets(harness):
    app, _, _, _ = harness
    limits = app.state.agent_runs.default_limits
    assert limits.max_steps == RunLimits().max_steps == 16
    assert limits.max_model_calls == RunLimits().max_model_calls == 20
    assert limits.max_tool_calls == RunLimits().max_tool_calls == 24


@pytest.mark.parametrize('field,maximum', [('agent_max_steps', 32), ('agent_max_model_calls', 32), ('agent_max_tool_calls', 64)])
def test_budget_settings_are_configurable_but_bounded(field, maximum):
    assert getattr(Settings(_env_file=None, **{field: maximum}), field) == maximum
    for value in (0, maximum + 1):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **{field: value})
