"""Narrow local tool catalog; ownership and current scenes come from run state.

No model-supplied photos, input IDs, scene payloads, dimensions or arbitrary code
are accepted. MCP transport and furniture additions belong to later milestones.
"""

from dataclasses import asdict
from typing import Annotated

import anyio
from pydantic import Field

from roometry.agents.clarification import question_for
from roometry.agents.nodes import RunContext
from roometry.agents.state import (
    AgentState,
    ClarificationKind,
    RunModel,
    RunStep,
    ToolFeedback,
    ToolName,
)
from roometry.agents.tool_policy import already_inspected, clarification_candidates, clarification_target
from roometry.scenes.assets import ASSET_REGISTRY, ObjectType, Placement
from roometry.scenes.catalog import ASSET_CATALOG, dimensions_for, object_surfaces
from roometry.scenes.edit_schemas import (
    FloorPosition,
    MoveObject,
    PreviewEditRequest,
    RotateObject,
)
from roometry.scenes.schemas import FiniteNumber, Identifier
from roometry.scenes.scoring import score_layout
from roometry.scenes.service import preview_edit
from roometry.scenes.validation import GeometryReport, validate_geometry
from roometry.vision.errors import AnalysisFailure
from roometry.vision.evidence import apply_constraints
from roometry.vision.fidelity import evaluate_fidelity
from roometry.vision.proposal_schemas import SceneProposalRequest
from roometry.vision.service import AnalysisService


class NoArguments(RunModel):
    pass


class CatalogArguments(RunModel):
    object_type: ObjectType | None
    placement: Placement | None
    limit: Annotated[int, Field(ge=1, le=8)]


class MoveArguments(RunModel):
    object_id: Identifier
    position: FloorPosition


class RotateArguments(RunModel):
    object_id: Identifier
    rotation_y_rad: FiniteNumber


class ClarificationArguments(RunModel):
    kind: ClarificationKind
    object_id: Identifier | None


ARGUMENT_MODELS: dict[ToolName, type[RunModel]] = {
    "analyze_photos": NoArguments,
    "propose_scene": NoArguments,
    "read_scene": NoArguments,
    "search_catalog": CatalogArguments,
    "move_object": MoveArguments,
    "rotate_object": RotateArguments,
    "validate_scene": NoArguments,
    "score_layout": NoArguments,
    "finish": NoArguments,
    "request_clarification": ClarificationArguments,
}
DESCRIPTIONS: dict[ToolName, str] = {
    "analyze_photos": "Analyze the current owned room photos together. Successful analysis is cached.",
    "propose_scene": "Build an approximate base scene from the existing analysis and confirmed dimensions. Keeps estimates labeled; may return partial results or missing dimensions.",
    "read_scene": "Inspect the current server-held scene. Its full current geometry is also provided in each decision context.",
    "search_catalog": "Read approved local models, dimensions, placements and support areas. This tool does not add or replace objects.",
    "move_object": "Move an existing movable object to an absolute floor or support position. Server derives elevation, carries dependents, validates, and rolls back rejected edits.",
    "rotate_object": "Rotate an existing movable object about positive Y, in radians. Server validates and rolls back rejected edits.",
    "validate_scene": "Run deterministic component-aware geometry checks on the current scene.",
    "score_layout": "Measure current soft goal proxies and clearance without changing the scene. Unknown style scores remain unassessed; scores cannot override evidence or clearance.",
    "finish": "Finish with the existing validated scene or an honest incomplete result. Cannot fabricate successful geometry or resolve omitted observations.",
    "request_clarification": "For room_wall_axis use object_id=null to identify which wall matches a measured side before construction. Ask one focused three-choice question about missing room measurements (object_id=null), an observed object identity or facing ambiguity before construction, or whether an existing movable object may move. At most three questions per run; answered facts cannot be asked again. The server constructs the choices and applies the answer as a real constraint.",
}


def available_tools(
    state: AgentState, *, reserve_decision: bool = True
) -> list[ToolName]:
    tools: list[ToolName] = ["finish"]
    if state.analysis is None:
        tools.append("analyze_photos")
    elif state.working_scene is None:
        tools.append("propose_scene")
    if state.working_scene is not None:
        inspection_tools: tuple[ToolName, ...] = (
            "read_scene",
            "search_catalog",
            "validate_scene",
            "score_layout",
        )
        for name in inspection_tools:
            if not already_inspected(state, name):
                tools.append(name)
        if any(
            not obj.fixed and not obj.locked and obj.placement in {"floor", "surface"}
            for obj in state.working_scene.objects
        ):
            tools.extend(["move_object", "rotate_object"])
    if clarification_candidates(state, reserve_decision=reserve_decision):
        tools.append("request_clarification")
    return tools


def tool_definitions(state: AgentState) -> list[dict]:
    definitions = []
    for name in available_tools(state):
        schema = ARGUMENT_MODELS[name].model_json_schema()
        if name == "request_clarification":
            questions = clarification_candidates(state)
            schema["properties"]["kind"]["enum"] = list(
                dict.fromkeys(q.kind for q in questions)
            )
            schema["properties"]["object_id"] = {
                "enum": [
                    *dict.fromkeys(
                        clarification_target(q) for q in questions
                    )
                ],
                "type": ["string", "null"],
            }
        definitions.append(
            {
                "type": "function",
                "name": name,
                "description": DESCRIPTIONS[name],
                "parameters": schema,
                "strict": True,
            }
        )
    return definitions


def report_summary(report: GeometryReport) -> dict:
    # Keep actionable identities and aggregate status without multiplying large
    # per-door connectivity lists across the agent's feedback history.
    doors = None
    if report.door_clearance is not None:
        source = report.door_clearance
        doors = source.model_dump(mode="json", exclude={"doors", "issues"})
        doors.update(door_count=len(source.doors), issue_count=len(source.issues))
        doors["doors"] = [
            {**door.model_dump(mode="json"),
             "access_object_ids": list(door.access_object_ids[:16]),
             "access_object_count": len(door.access_object_ids),
             "swing_object_ids": list(door.swing_object_ids[:16]),
             "swing_object_count": len(door.swing_object_ids)}
            for door in source.doors[:16]
        ]
        doors["issues"] = [issue.model_dump(mode="json") for issue in source.issues[:16]]
    walking = None
    if report.walking_space is not None:
        routes = report.walking_space
        walking = routes.model_dump(mode="json", exclude={"entrances", "issues"})
        walking.update(entrance_count=len(routes.entrances), issue_count=len(routes.issues))
        walking["entrances"] = [
            {**entry.model_dump(mode="json"),
             "reachable_door_ids": list(entry.reachable_door_ids[:16]),
             "reachable_door_count": len(entry.reachable_door_ids),
             "unreachable_door_ids": list(entry.unreachable_door_ids[:16]),
             "unreachable_door_count": len(entry.unreachable_door_ids)}
            for entry in routes.entrances[:16]
        ]
        walking["issues"] = [issue.model_dump(mode="json") for issue in routes.issues[:16]]
    return {
        "valid": report.valid,
        "policy_version": report.policy_version,
        "error_count": len(report.errors),
        "errors": [error.model_dump(mode="json") for error in report.errors[:32]],
        "warning_count": len(report.warnings),
        "warnings": [
            warning.model_dump(mode="json") for warning in report.warnings[:16]
        ],
        "door_clearance": doors,
        "walking_space": walking,
    }


class SceneTools:
    def __init__(self, analysis: AnalysisService):
        self.analysis = analysis

    async def __call__(self, state: AgentState, context: RunContext) -> RunStep:
        call = state.pending_tool
        if call is None:
            raise ValueError("There is no pending tool.")
        if call.name not in available_tools(state, reserve_decision=False):
            return RunStep(
                action="continue",
                feedback=ToolFeedback(
                    name=call.name,
                    outcome="rejected",
                    message="This tool is unavailable in the current run state.",
                ),
            )
        # The same strict parsing is used before checkpointing the decision and
        # again at dispatch. Extra authority fields never reach a service.
        arguments = ARGUMENT_MODELS[call.name].model_validate(call.arguments)
        budget = context.budget
        if budget is None or not context.owner:
            raise ValueError("Tool execution needs an owned run and call budget.")
        try:
            await budget.reserve_tool()
            await context.authorize()
            return await self._execute(state, context, arguments)
        except AnalysisFailure as error:
            # Upload revocation still propagates from the node's authorization
            # guard. Other provider failures stop rather than automatically
            # repeating an ambiguous/failed paid tool invocation.
            return RunStep(
                action="finish",
                failure="tool_failed",
                feedback=ToolFeedback(
                    name=call.name,
                    outcome="error",
                    message="The room tool could not complete this request.",
                    details={"code": error.code},
                ),
            )

    async def _execute(
        self, state: AgentState, context: RunContext, arguments: RunModel
    ) -> RunStep:
        call = state.pending_tool
        assert call is not None
        name = call.name

        def feedback(
            message: str, details: dict | None = None, *, rejected: bool = False
        ) -> ToolFeedback:
            return ToolFeedback(
                name=name,
                outcome="rejected" if rejected else "success",
                message=message,
                details=details or {},
            )

        if name == "analyze_photos":
            analysis = await self.analysis.analyze(state.input_id, context.owner)
            return RunStep(
                action="continue",
                analysis=analysis,
                feedback=feedback(
                    "Photo observations are available.",
                    {
                        "objects": len(analysis.observations.objects),
                        "openings": len(analysis.observations.openings),
                    },
                ),
            )
        if name == "propose_scene":
            assert state.analysis is not None
            try:
                axis_question = question_for(state, "room_wall_axis")
            except ValueError:
                axis_question = None
            if axis_question is not None:
                return RunStep(action="continue", pending_question=axis_question, feedback=feedback("Waiting to identify which observed wall matches the entered room dimension."))
            proposal = await self.analysis.propose(
                SceneProposalRequest(
                    analysis_id=state.analysis.analysis_id,
                    dimensions=state.confirmed_dimensions,
                    allow_estimated_dimensions=True,
                    constraints=state.reconstruction_constraints,
                ),
                context.owner,
                isolated_dimensions=True,
                allow_ambiguous_axes=True,
            )
            question = None
            if proposal.status in {"needs_dimensions", "needs_wall_assignment"} and len(state.questions) < 3:
                try:
                    question = question_for(state, "room_dimensions" if proposal.status == "needs_dimensions" else "room_wall_axis")
                except ValueError:
                    pass
            return RunStep(
                action="finish"
                if proposal.status in {"needs_dimensions", "needs_wall_assignment", "incomplete"}
                and question is None
                else "continue",
                proposal=proposal,
                pending_question=question,
                feedback=feedback(
                    "Scene construction returned " + proposal.status + ".",
                    {
                        "status": proposal.status,
                        "geometry": report_summary(proposal.validation) if proposal.validation else None,
                        "dimensions_to_confirm": proposal.dimensions_to_confirm,
                        "issues": [
                            item.model_dump(mode="json") for item in proposal.issues
                        ],
                        "unresolved_entities": [
                            item.model_dump(mode="json")
                            for item in proposal.unresolved_entities
                        ],
                    },
                ),
            )
        if name == "request_clarification":
            assert isinstance(arguments, ClarificationArguments)
            try:
                question = question_for(state, arguments.kind, arguments.object_id)
            except ValueError:
                return RunStep(
                    action="continue",
                    feedback=feedback(
                        "This question is unavailable, already answered, or targets a protected object. Use existing facts or finish honestly.",
                        rejected=True,
                    ),
                )
            return RunStep(
                action="continue",
                pending_question=question,
                feedback=feedback("Waiting for a focused human clarification."),
            )
        if name == "search_catalog":
            assert isinstance(arguments, CatalogArguments)
            models = [
                asset
                for asset in ASSET_CATALOG.values()
                if (
                    arguments.object_type is None
                    or asset.object_type == arguments.object_type
                )
                and (
                    arguments.placement is None
                    or arguments.placement in ASSET_REGISTRY[asset.asset_id].placements
                )
            ]
            result = []
            for asset in models[: arguments.limit]:
                placements = sorted(ASSET_REGISTRY[asset.asset_id].placements)
                result.append(
                    {
                        "asset_id": asset.asset_id,
                        "type": asset.object_type,
                        "label": asset.label,
                        "configurations": {
                            placement: {
                                "dimensions_m": dict(
                                    zip(
                                        ("width", "height", "depth"),
                                        dimensions_for(asset.asset_id, placement),
                                    )
                                ),
                                "support_surfaces": [
                                    asdict(area)
                                    for area in object_surfaces(
                                        asset.asset_id, placement
                                    )
                                ],
                            }
                            for placement in placements
                            if arguments.placement is None
                            or placement == arguments.placement
                        },
                    }
                )
            return RunStep(
                action="continue",
                feedback=feedback(
                    "Approved catalog models.",
                    {"total_matches": len(models), "models": result},
                ),
            )
        scene = state.working_scene
        if name == "finish":
            if scene is not None and not validate_geometry(scene).valid:
                return RunStep(
                    action="continue",
                    feedback=feedback(
                        "The current scene cannot be finalized because geometry validation failed.",
                        rejected=True,
                    ),
                )
            return RunStep(
                action="finish",
                feedback=feedback(
                    "Finished with the existing scene and its unresolved observations."
                    if scene
                    else "Finished without a complete reconstructed scene."
                ),
            )
        assert scene is not None
        if name == "read_scene":
            return RunStep(
                action="continue",
                feedback=feedback(
                    "The current scene is available in decision context.",
                    {
                        "object_ids": [obj.id for obj in scene.objects],
                        "opening_ids": [opening.id for opening in scene.openings],
                    },
                ),
            )
        if name == "score_layout":
            report = await anyio.to_thread.run_sync(score_layout, scene, list(state.input_options.goals))
            return RunStep(action="continue", feedback=feedback(
                "Soft goal proxies measured; this does not authorize redesign or override clearance.",
                {**report.model_dump(mode="json", exclude={"geometry"}), "geometry": report_summary(report.geometry)},
            ))
        if name == "validate_scene":
            report = await anyio.to_thread.run_sync(validate_geometry, scene)
            fidelity = (
                evaluate_fidelity(
                    apply_constraints(state.analysis, state.reconstruction_constraints, wall_axes=state.proposal.wall_axes if state.proposal else None),
                    scene,
                )
                if state.analysis
                else None
            )
            return RunStep(
                action="continue",
                feedback=feedback(
                    "Current scene geometry and reconstruction evidence checked.",
                    {
                        **report_summary(report),
                        "fidelity": fidelity.model_dump(mode="json")
                        if fidelity
                        else None,
                    },
                ),
            )
        if isinstance(arguments, MoveArguments):
            edit = MoveObject(
                operation="move",
                object_id=arguments.object_id,
                position=arguments.position,
            )
        elif isinstance(arguments, RotateArguments):
            edit = RotateObject(
                operation="rotate",
                object_id=arguments.object_id,
                rotation_y_rad=arguments.rotation_y_rad,
            )
        else:
            raise ValueError("Unsupported scene edit.")
        result = await anyio.to_thread.run_sync(
            preview_edit, PreviewEditRequest(scene=scene, edit=edit)
        )
        if result.accepted and state.analysis is not None:
            effective = apply_constraints(
                state.analysis, state.reconstruction_constraints,
                wall_axes=state.proposal.wall_axes if state.proposal else None,
            )
            before = evaluate_fidelity(effective, scene)
            after = evaluate_fidelity(effective, result.scene)
            failed_before = {
                (c.kind, tuple(c.entity_ids), c.message)
                for c in before.checks
                if c.required and not c.passed
            }
            new_failures = [
                c
                for c in after.checks
                if c.required
                and not c.passed
                and (c.kind, tuple(c.entity_ids), c.message) not in failed_before
            ]
            if new_failures:
                return RunStep(
                    action="continue",
                    feedback=feedback(
                        "Edit rejected; it contradicts recorded reconstruction evidence. The current scene is unchanged.",
                        {
                            "accepted": False,
                            "changed_object_ids": [],
                            "fidelity": [
                                c.model_dump(mode="json") for c in new_failures
                            ],
                        },
                        rejected=True,
                    ),
                )
        details = {
            "accepted": result.accepted,
            "changed_object_ids": list(result.changed_object_ids),
            "edit_errors": [
                item.model_dump(mode="json") for item in result.edit_errors
            ],
            "geometry": report_summary(
                result.candidate_validation or result.base_validation
            ),
        }
        return RunStep(
            action="continue",
            working_scene=result.scene if result.accepted else None,
            feedback=feedback(
                "Edit accepted."
                if result.accepted
                else "Edit rejected; the current scene is unchanged.",
                details,
                rejected=not result.accepted,
            ),
        )
