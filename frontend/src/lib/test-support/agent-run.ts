import { parseAgentRun } from '../agent-run'
import type { AgentState } from '../agent-run'
import type { PreparedRoomInput } from '../room-input-response.generated'
import { analyzedRoom, needsDimensions, partialRoom, proposedRoom } from './room-analysis'

export function agentRun(input: PreparedRoomInput, patch: Partial<AgentState> = {}): AgentState {
  return parseAgentRun({
    run_id: '66666666-6666-4666-8666-666666666666', input_id: input.input_id,
    image_ids: input.images.map(image => image.image_id), input_options: input.options,
    created_at: new Date(Date.now() - 1000).toISOString(), input_expires_at: input.expires_at, expires_at: input.expires_at,
    limits: { max_steps: 8, max_model_calls: 8, max_tool_calls: 16, step_timeout_seconds: 185 },
    status: 'running', steps_used: 1, usage: { model_calls: 1, tool_calls: 0, input_tokens: 100, output_tokens: 30, unknown_usage_calls: 0 },
    continue_run: true, analysis: null, proposal: null, confirmed_dimensions: input.options.dimensions ?? null,
    clarification_answers: {}, submitted_answers: {}, questions: [], pending_question: null, object_mobility: {},
    pending_tool: { call_id: 'call_1', name: 'analyze_photos', arguments: {} }, tool_feedback: [],
    base_scene: null, working_scene: null, events: [], last_error: null, ...patch,
  })
}

export function waitingRun(input: PreparedRoomInput): AgentState {
  const question: NonNullable<AgentState['pending_question']> = {
    id: 'q1', kind: 'room_dimensions', question: 'What is the room height in meters?', dimension_axes: ['height_m'], entity_ids: [],
    choices: [ { id: 'enter_measurements', label: 'Enter the measurements' }, { id: 'leave_unknown', label: "I don't know — finish without a complete scene" }, { id: 'cancel_run', label: 'Cancel this run' } ],
  }
  return agentRun(input, { status: 'awaiting_answer', steps_used: 2, analysis: analyzedRoom(input), proposal: needsDimensions(input), pending_tool: null,
    usage: { model_calls: 3, tool_calls: 2, input_tokens: 1400, output_tokens: 860, unknown_usage_calls: 0 }, questions: [question], pending_question: question })
}

export function finishedRun(input: PreparedRoomInput, partial = false): AgentState {
  const proposal = partial ? partialRoom(input) : proposedRoom(input)
  return agentRun(input, { status: partial ? 'partial' : 'completed', steps_used: 6, pending_tool: null, continue_run: false,
    usage: { model_calls: 6, tool_calls: 4, input_tokens: 2800, output_tokens: 1700, unknown_usage_calls: 0 },
    analysis: analyzedRoom(input), proposal, confirmed_dimensions: { width_m: 4, length_m: 4, height_m: 2.6, measurements_source: 'user_confirmed' },
    base_scene: proposal.scene, working_scene: proposal.scene })
}
