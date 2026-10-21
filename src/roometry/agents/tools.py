"""Narrow local tool catalog; ownership and current scenes come from run state.

No model-supplied photos, input IDs, scene payloads, dimensions or arbitrary code
are accepted. MCP transport and furniture additions belong to later milestones.
"""

from dataclasses import asdict
from typing import Annotated

import anyio
from pydantic import Field

from roometry.agents.nodes import RunContext
from roometry.agents.state import AgentState, RunModel, RunStep, ToolFeedback, ToolName
from roometry.scenes.assets import ASSET_REGISTRY, ObjectType, Placement
from roometry.scenes.catalog import ASSET_CATALOG, dimensions_for, object_surfaces
from roometry.scenes.edit_schemas import (
    FloorPosition,
    MoveObject,
    PreviewEditRequest,
    RotateObject,
)
from roometry.scenes.schemas import FiniteNumber, Identifier
from roometry.scenes.service import preview_edit
from roometry.scenes.validation import GeometryReport, validate_geometry
from roometry.vision.errors import AnalysisFailure
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


ARGUMENT_MODELS: dict[ToolName, type[RunModel]] = {
    "analyze_photos": NoArguments,
    "propose_scene": NoArguments,
    "read_scene": NoArguments,
    "search_catalog": CatalogArguments,
    "move_object": MoveArguments,
    "rotate_object": RotateArguments,
    "validate_scene": NoArguments,
    "finish": NoArguments,
}
DESCRIPTIONS: dict[ToolName, str] = {
    "analyze_photos": "Analyze the current owned room photos together. Successful analysis is cached.",
    "propose_scene": "Build an approximate base scene from the existing analysis and confirmed dimensions. Keeps estimates labeled; may return partial results or missing dimensions.",
    "read_scene": "Inspect the current server-held scene. Its full current geometry is also provided in each decision context.",
    "search_catalog": "Read approved local models, dimensions, placements and support areas. This tool does not add or replace objects.",
    "move_object": "Move an existing movable object to an absolute floor or support position. Server derives elevation, carries dependents, validates, and rolls back rejected edits.",
    "rotate_object": "Rotate an existing movable object about positive Y, in radians. Server validates and rolls back rejected edits.",
    "validate_scene": "Run deterministic component-aware geometry checks on the current scene.",
    "finish": "Finish with the existing validated scene or an honest incomplete result. Cannot fabricate successful geometry or resolve omitted observations.",
}


def available_tools(state: AgentState) -> list[ToolName]:
    tools: list[ToolName] = ["search_catalog", "finish"]
    if state.analysis is None:
        tools.append("analyze_photos")
    elif state.working_scene is None:
        tools.append("propose_scene")
    if state.working_scene is not None:
        tools.extend(["read_scene", "move_object", "rotate_object", "validate_scene"])
    return tools


def tool_definitions(state: AgentState) -> list[dict]:
    return [
        {
            "type": "function",
            "name": name,
            "description": DESCRIPTIONS[name],
            "parameters": ARGUMENT_MODELS[name].model_json_schema(),
            "strict": True,
        }
        for name in available_tools(state)
    ]


def report_summary(report: GeometryReport) -> dict:
    return {
        "valid": report.valid,
        "policy_version": report.policy_version,
        "error_count": len(report.errors),
        "errors": [error.model_dump(mode="json") for error in report.errors[:32]],
        "warnings": [
            warning.model_dump(mode="json") for warning in report.warnings[:4]
        ],
    }


class SceneTools:
    def __init__(self, analysis: AnalysisService):
        self.analysis = analysis

    async def __call__(self, state: AgentState, context: RunContext) -> RunStep:
        call = state.pending_tool
        if call is None:
            raise ValueError("There is no pending tool.")
        if call.name not in available_tools(state):
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
            proposal = await self.analysis.propose(
                SceneProposalRequest(
                    analysis_id=state.analysis.analysis_id,
                    dimensions=state.confirmed_dimensions,
                    allow_estimated_dimensions=True,
                ),
                context.owner,
            )
            return RunStep(
                action="finish"
                if proposal.status in {"needs_dimensions", "incomplete"}
                else "continue",
                proposal=proposal,
                feedback=feedback(
                    "Scene construction returned " + proposal.status + ".",
                    {
                        "status": proposal.status,
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
        if name == "validate_scene":
            report = await anyio.to_thread.run_sync(validate_geometry, scene)
            return RunStep(
                action="continue",
                feedback=feedback(
                    "Current scene geometry checked.", report_summary(report)
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
