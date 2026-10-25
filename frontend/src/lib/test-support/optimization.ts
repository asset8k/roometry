import type { AgentState } from '../agent-run'
import type { OptimizationRun, StartOptimizationRequest } from '../optimization'

export function preparedImprovement(source: AgentState, request: StartOptimizationRequest): OptimizationRun {
  return {
    run_id: '88888888-8888-4888-8888-888888888888', source_run_id: source.run_id, input_id: source.input_id,
    source_scene_hash: source.room_assessment?.scene_hash ?? 'a'.repeat(64), source_scene: structuredClone(source.working_scene!),
    source_status: source.status === 'partial' ? 'partial' : 'completed', baseline_assessment: source.room_assessment ?? null,
    focus: request.focus, instruction: request.instruction ?? null, parent_run_id: request.context?.parent_run_id ?? null,
    preferences: { budget: request.preferences?.budget ?? null, style: request.preferences?.style ?? null, constraints: request.preferences?.constraints ?? null },
    created_at: new Date().toISOString(), expires_at: source.expires_at, status: 'prepared',
    stage: 'prepared', max_candidates: 3, limits: { max_model_calls: 6, max_steps: 16, max_tool_calls: 24, step_timeout_seconds: 90 },
    usage: { model_calls: 0, tool_calls: 0, input_tokens: 0, output_tokens: 0, unknown_usage_calls: 0 },
    attempts: [], result_scene: null, result_assessment: null, suggestions: [], message: 'Ready to run the selected improvement.', limitations: [],
  }
}
