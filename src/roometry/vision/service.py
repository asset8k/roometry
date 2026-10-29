"""Session-scoped analysis with bounded spend, retries, memory, and lifetime.

The local cache shares the uploads adapter's one-worker/restart limitations.
It stores observations, never image bytes. Generation quotas remain separate.
"""

import json
import logging
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import partial
from time import perf_counter
from typing import Literal
from uuid import uuid4

import anyio
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError
from openai.types.responses import Response, ResponseUsage
from pydantic import ValidationError

from roometry.config import Settings
from roometry.assessment.schemas import RoomAssessment
from roometry.model_calls import ModelOperation
from roometry.uploads.errors import UploadFailure
from roometry.uploads.schemas import PreparedRoomInput
from roometry.uploads.storage import InputStorage
from roometry.vision.analyzer import PhotoAnalyzer, parse_observations, response_json
from roometry.vision.analysis_repair import repair_request, validate_repair_inventory
from roometry.vision.diagnostics import (
    provider_id,
    response_diagnostics,
    validation_diagnostics,
)
from roometry.vision.errors import AnalysisFailure
from roometry.vision.evidence import apply_constraints
from roometry.vision.wall_axes import resolve_wall_axes
from roometry.vision.fidelity import evaluate_fidelity
from roometry.vision.partial import assemble_partial
from roometry.vision.reconstruction_diagnostics import omission_diagnostics
from roometry.vision.importance import partial_quality
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.recovery import merge_repair, actionable_repair
from roometry.vision.draft_validation import parse_scene_draft
from roometry.vision.proposal_schemas import (
    AXES,
    DimensionName,
    SceneDraft,
    SceneProposalRequest,
    SceneProposalResponse,
)
from roometry.vision.proposals import assemble, issue, preflight, resolve_measurements
from roometry.vision.schemas import RoomAnalysisResponse, RoomObservations


@dataclass
class AnalysisState:
    owner: str
    expires_at: datetime
    analysis_id: str
    attempts: int = 0
    result: RoomAnalysisResponse | None = None
    confirmed_dimensions: dict[DimensionName, float] = field(default_factory=dict)
    scene_attempts: int = 0
    proposal_key: str | None = None
    proposal: SceneProposalResponse | None = None
    review_key: str | None = None
    review_attempted: bool = False
    visual_repair_attempted: bool = False
    reviewed_proposal: SceneProposalResponse | None = None
    assessment_key: str | None = None
    assessment_attempted: bool = False
    assessment_result: RoomAssessment | None = None
    # Internal, checkpointed diagnostics; never raw prompts, responses or photos.
    diagnostics: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class AttemptUsage:
    analysis_id: str
    model: str
    requested_model: str
    operation: ModelOperation
    attempt: int
    outcome: str
    stage: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cached_input_tokens: int | None
    reasoning_tokens: int | None


class AnalysisService:
    def __init__(
        self, settings: Settings, storage: InputStorage, analyzer: PhotoAnalyzer
    ):
        self.settings, self.storage, self.analyzer = settings, storage, analyzer
        self._capacity = anyio.Semaphore(1)
        self._states: dict[str, AnalysisState] = {}
        # Stage-validation diagnostics. The analyzer's shared provider ledger
        # also includes decisions; these two histories must not be added together.
        # Unknown usage stays null, including cancelled/ambiguous network requests.
        self.usage_records: deque[AttemptUsage] = deque(maxlen=256)

    def _resolve(self, input_id: str, owner: str) -> PreparedRoomInput:
        try:
            return self.storage.resolve(input_id, owner)
        except UploadFailure:
            raise AnalysisFailure(
                404, "input_not_found", "This room input is unavailable or has expired."
            ) from None
        except OSError:
            raise AnalysisFailure(
                503, "storage_unavailable", "Photo storage is temporarily unavailable."
            ) from None

    def _load_images(self, prepared: PreparedRoomInput, owner: str) -> list[bytes]:
        images = []
        try:
            for metadata in prepared.images:
                image = self.storage.read_image(
                    prepared.input_id, metadata.image_id, owner
                )
                if (
                    len(image) != metadata.prepared_bytes
                    or len(image) > self.settings.upload_prepared_max_bytes
                ):
                    raise OSError("Stored image metadata mismatch")
                images.append(image)
        except UploadFailure:
            raise AnalysisFailure(
                404, "input_not_found", "This room input is unavailable or has expired."
            ) from None
        except OSError:
            raise AnalysisFailure(
                503,
                "storage_unavailable",
                "The stored photos are unavailable. Upload them again.",
            ) from None
        return images

    async def cleanup(self) -> None:
        states = list(self._states.items())

        def invalid_inputs() -> list[str]:
            invalid = []
            for key, state in states:
                input_id = key.rsplit(":", 1)[-1]
                try:
                    self.storage.resolve(input_id, state.owner)
                except UploadFailure:
                    invalid.append(key)
            return invalid

        invalid = await anyio.to_thread.run_sync(invalid_inputs)
        for input_id in invalid:
            self._states.pop(input_id, None)

    def _record(
        self,
        state: AnalysisState,
        started: float,
        outcome: str,
        response: Response | None,
        stage: str = "analysis",
        details: dict[str, object] | None = None,
        *,
        operation: ModelOperation = "analysis",
        model: str | None = None,
    ) -> None:
        usage = getattr(response, "usage", None)
        usage = usage if isinstance(usage, ResponseUsage) else None

        def count(value: object) -> int | None:
            return value if type(value) is int and value >= 0 else None

        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        requested_model = model or self.settings.model_route(operation).model
        actual_model = (
            response.model or requested_model
            if isinstance(response, Response) else requested_model
        )

        record = AttemptUsage(
            analysis_id=state.analysis_id,
            model=actual_model,
            requested_model=requested_model,
            operation=operation,
            attempt=state.attempts if stage == "analysis" else state.scene_attempts,
            stage=stage,
            outcome=outcome,
            latency_ms=round((perf_counter() - started) * 1000),
            input_tokens=count(getattr(usage, "input_tokens", None)),
            output_tokens=count(getattr(usage, "output_tokens", None)),
            total_tokens=count(getattr(usage, "total_tokens", None)),
            cached_input_tokens=count(getattr(input_details, "cached_tokens", None)),
            reasoning_tokens=count(getattr(output_details, "reasoning_tokens", None)),
        )
        self.usage_records.append(record)
        # Uvicorn's default text logger does not print `extra`, and INFO from
        # application loggers is normally hidden. Render safe failure diagnostics
        # at WARNING so a 200/incomplete proposal can actually be diagnosed.
        diagnostic = {**response_diagnostics(response), **(details or {})}
        self._retain_diagnostic(state, {**asdict(record), "diagnostics": diagnostic})
        logging.getLogger(__name__).log(
            logging.INFO if outcome == "success" else logging.WARNING,
            "Room model attempt completed: %s",
            json.dumps(
                {**asdict(record), "diagnostics": diagnostic}, ensure_ascii=True
            ),
            extra={
                "analysis_usage": asdict(record),
                "analysis_diagnostics": diagnostic,
            },
        )

    def _retain_diagnostic(self, state, entry):
        state.diagnostics = [*state.diagnostics[-31:], entry]

    def record_repair_outcome(self, state, operation, outcome, started, *, response=None, error=None, details=None):
        """Persist useful failure facts without retaining arbitrary exception text."""
        entry = {"operation": operation, "outcome": outcome,
                 "elapsed_ms": round((perf_counter()-started)*1000),
                 **response_diagnostics(response), **(details or {})}
        if isinstance(error, ValidationError):
            entry["validation"] = validation_diagnostics(error, SceneDraft)
            entry["reason"] = "schema_validation"
        elif isinstance(error, AnalysisFailure):
            entry["reason"] = error.code
            if (error.diagnostics or {}).get("reason") in {"repair_scope_violation", "max_output_tokens"}:
                entry["failure_rule"] = error.diagnostics["reason"]
        elif error is not None:
            # Class names come from fixed exception handlers, not error messages.
            entry["reason"] = type(error).__name__
        self._retain_diagnostic(state, entry)
        logging.getLogger(__name__).log(logging.INFO if outcome == "accepted" else logging.WARNING,
            "Room repair outcome: analysis_id=%s diagnostic=%s", state.analysis_id, json.dumps(entry))

    async def analyze(self, input_id: str, owner: str) -> RoomAnalysisResponse:
        key = self.settings.openai_api_key
        if (
            not self.settings.analysis_enabled
            or key is None
            or not key.get_secret_value().strip()
        ):
            raise AnalysisFailure(
                503,
                "analysis_unavailable",
                "Room analysis is not configured or is disabled.",
            )
        try:
            self._capacity.acquire_nowait()
        except anyio.WouldBlock:
            raise AnalysisFailure(
                429,
                "analysis_busy",
                "Another analysis is running. Please try again shortly.",
            ) from None
        try:
            # A completed inconsistent result may need one correction. Each
            # provider request still has its own timeout; total work is bounded.
            deadline = self.settings.analysis_timeout_seconds * min(
                2, self.settings.analysis_max_provider_attempts
            )
            with anyio.fail_after(deadline):
                return await self._analyze(input_id, owner)
        except TimeoutError:
            raise AnalysisFailure(
                504,
                "analysis_timeout",
                "Room analysis timed out. You can retry while the photos remain available.",
            ) from None
        finally:
            self._capacity.release()

    async def _analyze(self, input_id: str, owner: str) -> RoomAnalysisResponse:
        prepared = await anyio.to_thread.run_sync(self._resolve, input_id, owner)
        await self.cleanup()
        cache_key = self.cache_key(input_id, owner)
        state = self._states.get(cache_key)
        if state is not None and state.result is not None:
            return state.result.model_copy(deep=True)
        if (
            state is not None
            and state.attempts >= self.settings.analysis_max_provider_attempts
        ):
            raise AnalysisFailure(
                429,
                "analysis_limit_reached",
                "This upload has reached its analysis attempt limit. Upload a new batch to try again.",
            )
        if (
            state is None
            and len(self._states) >= self.settings.analysis_max_cached_inputs
        ):
            raise AnalysisFailure(
                429,
                "analysis_busy",
                "Analysis capacity is full. Please try again after temporary inputs expire.",
            )
        images = await anyio.to_thread.run_sync(self._load_images, prepared, owner)
        payload = await anyio.to_thread.run_sync(
            self.analyzer.build_request, prepared, images
        )
        # The encoded payload owns the bytes only until this request completes.
        del images
        if state is None:
            state = AnalysisState(
                owner=owner, expires_at=prepared.expires_at, analysis_id=str(uuid4())
            )
            self._states[cache_key] = state

        repair_used = False
        original_response: Response | None = None
        while state.attempts < self.settings.analysis_max_provider_attempts:
            # A discarded/replaced/expired batch must not trigger another attempt.
            await anyio.to_thread.run_sync(self._resolve, input_id, owner)
            state.attempts += 1
            started, outcome, response = perf_counter(), "interrupted", None
            details: dict[str, object] | None = None
            observations: RoomObservations | None = None
            retry = False
            failure: AnalysisFailure | None = None
            try:
                response = await self.analyzer.request(payload, operation="analysis")
                observations = parse_observations(
                    response,
                    [image.image_id for image in prepared.images],
                    self.settings.analysis_max_output_bytes,
                )
                if original_response is not None:
                    validate_repair_inventory(
                        original_response, observations,
                        self.settings.analysis_max_output_bytes,
                    )
                outcome = "success"
            except APITimeoutError:
                outcome = "analysis_timeout"
                raise AnalysisFailure(
                    504,
                    "analysis_timeout",
                    "The analysis provider timed out. You can retry while the photos remain available.",
                ) from None
            except APIStatusError as error:
                details = {
                    "provider_status": error.status_code,
                    "provider_request_id": provider_id(error.request_id),
                }
                retry = error.status_code == 429 or error.status_code >= 500
                if error.status_code == 429:
                    outcome = "provider_rate_limited"
                    failure = AnalysisFailure(
                        429,
                        "provider_rate_limited",
                        "The analysis provider is busy. Please try again shortly.",
                    )
                else:
                    outcome = "provider_unavailable"
                    failure = AnalysisFailure(
                        503,
                        "provider_unavailable",
                        "Room analysis is temporarily unavailable. Please try again.",
                    )
            except APIConnectionError:
                # A missing response may still have incurred provider usage. Do
                # not automatically repeat ambiguous network/time-out failures.
                outcome = "provider_unavailable"
                raise AnalysisFailure(
                    503,
                    "provider_unavailable",
                    "The analysis provider could not be reached. Please try again.",
                ) from None
            except APIError:
                outcome = "provider_unavailable"
                raise AnalysisFailure(
                    503,
                    "provider_unavailable",
                    "The analysis provider returned an unexpected response.",
                ) from None
            except AnalysisFailure as error:
                details = error.diagnostics
                outcome = error.code
                corrected = (
                    repair_request(
                        payload, response, error,
                        max_output_bytes=self.settings.analysis_max_output_bytes,
                        max_request_bytes=self.settings.analysis_max_request_bytes,
                    )
                    if response is not None
                    and not repair_used
                    and state.attempts < self.settings.analysis_max_provider_attempts
                    else None
                )
                if corrected is None:
                    raise
                payload = corrected
                repair_used = True
                original_response = response
                retry, failure = True, error
            except ValueError:
                outcome = "invalid_analysis"
                raise AnalysisFailure(
                    502,
                    "invalid_analysis",
                    "The analysis provider returned an invalid response.",
                ) from None
            finally:
                self._record(
                    state, started, outcome, response,
                    details=details, model=payload["model"],
                )

            if failure is not None:
                if (
                    retry
                    and state.attempts < self.settings.analysis_max_provider_attempts
                ):
                    await anyio.sleep(0.5)
                    continue
                raise failure

            if observations is None:
                raise AnalysisFailure(
                    502, "invalid_analysis", "The model returned no analysis."
                )

            # Revoke results if the source disappeared while the provider ran.
            await anyio.to_thread.run_sync(self._resolve, input_id, owner)
            created_at = self.storage.now()
            if created_at >= prepared.expires_at:
                raise AnalysisFailure(
                    404,
                    "input_not_found",
                    "This room input is unavailable or has expired.",
                )
            result = RoomAnalysisResponse(
                analysis_id=state.analysis_id,
                input_id=input_id,
                status="analyzed",
                created_at=created_at,
                expires_at=prepared.expires_at,
                input_options=prepared.options,
                observations=observations,
            )
            state.result = result.model_copy(deep=True)
            return result
        raise AnalysisFailure(
            429,
            "analysis_limit_reached",
            "This upload has reached its analysis attempt limit.",
        )

    @staticmethod
    def cache_key(input_id: str, owner: str) -> str:
        # A browser may explicitly reuse its upload after switching accounts.
        # Confirmed relationships and construction attempts belong to that owner.
        return f"{owner}:{input_id}" if owner.startswith("account:") else input_id

    async def _owned_analysis(self, analysis_id: str, owner: str) -> AnalysisState:
        for state in list(self._states.values()):
            if (
                state.owner == owner
                and state.analysis_id == analysis_id
                and state.result is not None
            ):
                await anyio.to_thread.run_sync(self._resolve, state.result.input_id, owner)
                if self.storage.now() >= state.expires_at:
                    break
                return state
        raise AnalysisFailure(
            404, "analysis_not_found", "This analysis is unavailable or has expired."
        )

    async def propose(
        self,
        request: SceneProposalRequest,
        owner: str,
        *,
        isolated_dimensions: bool = False,
        allow_ambiguous_axes: bool = False,
        assume_missing_dimensions: bool = False,
    ) -> SceneProposalResponse:
        try:
            self._capacity.acquire_nowait()
        except anyio.WouldBlock:
            raise AnalysisFailure(
                429, "analysis_busy", "Another room analysis or proposal is running."
            ) from None
        try:
            # Scene generation permits an initial call and one repair. Each gets
            # the configured per-call deadline; a slow first call must not consume
            # the repair's entire time budget. The total remains bounded.
            with anyio.fail_after(2 * self.settings.request_timeout("scene_proposal")):
                return await self._propose(
                    request, owner, isolated_dimensions=isolated_dimensions, allow_ambiguous_axes=allow_ambiguous_axes,
                    assume_missing_dimensions=assume_missing_dimensions,
                )
        except TimeoutError:
            raise AnalysisFailure(
                504,
                "analysis_timeout",
                "Scene proposal timed out. Your observations remain cached until expiry.",
            ) from None
        finally:
            self._capacity.release()

    async def _propose(
        self,
        request: SceneProposalRequest,
        owner: str,
        *,
        isolated_dimensions: bool = False,
        allow_ambiguous_axes: bool = False,
        assume_missing_dimensions: bool = False,
    ) -> SceneProposalResponse:
        state = await self._owned_analysis(request.analysis_id, owner)
        analysis = state.result
        assert analysis is not None
        try:
            analysis = apply_constraints(analysis, request.constraints)
        except ValueError:
            raise AnalysisFailure(422, "invalid_request", "The reconstruction answers do not match this room's observations.") from None
        # Agent runs carry their own confirmed answers. Another run using this
        # upload must not inherit them as silently confirmed measurements.
        confirmed_dimensions = {} if isolated_dimensions else state.confirmed_dimensions
        if request.dimensions is not None:
            for axis in AXES:
                value = getattr(request.dimensions, axis)
                if value is not None:
                    confirmed_dimensions[axis] = value
        measurements = resolve_measurements(
            analysis, confirmed_dimensions, request.allow_estimated_dimensions,
            assume_missing=assume_missing_dimensions,
        )
        base = {
            "analysis_id": analysis.analysis_id,
            "input_id": analysis.input_id,
            "dimensions": measurements.dimensions,
            "dimensions_to_confirm": measurements.to_confirm,
            "estimated_axes": measurements.estimated,
            "assumed_axes": measurements.assumed,
            "expires_at": analysis.expires_at,
            "warnings": [
                "This is an approximate base scene; review estimated sizes, layout, and uncertain identities.",
                *measurements.estimate_notes,
                *measurements.assumption_notes,
            ],
        }
        if measurements.to_confirm:
            return SceneProposalResponse.model_validate(
                {
                    **base,
                    "status": "needs_dimensions",
                    "scene": None,
                    "validation": None,
                    "issues": [],
                }
            )
        analysis.wall_axes = resolve_wall_axes(analysis, measurements.dimensions, request.constraints, allow_ambiguous=allow_ambiguous_axes)
        base["wall_axes"] = analysis.wall_axes
        if analysis.wall_axes.status == "needs_clarification":
            if state.scene_attempts >= 2:
                return SceneProposalResponse.model_validate({**base, "status": "incomplete", "scene": None, "validation": None, "issues": [issue("proposal_limit_reached", "This analysis has used its initial scene attempt and repair allowance.")]})
            return SceneProposalResponse.model_validate({**base, "status": "needs_wall_assignment", "scene": None, "validation": None, "issues": []})
        base["warnings"].extend(analysis.wall_axes.warnings)
        problems = preflight(analysis)
        if any(not problem.entity_ids for problem in problems):
            logging.getLogger(__name__).warning(
                "Scene proposal incomplete: %s",
                json.dumps(
                    {
                        "analysis_id": analysis.analysis_id,
                        "reason": "preflight",
                        "issue_codes": list(
                            dict.fromkeys(problem.code for problem in problems)
                        ),
                    }
                ),
            )
            return SceneProposalResponse.model_validate(
                {
                    **base,
                    "status": "incomplete",
                    "scene": None,
                    "validation": None,
                    "issues": problems[:50],
                }
            )
        cache_key = json.dumps(
            {
                "dimensions": measurements.dimensions.model_dump(),
                "estimated": measurements.estimated,
                "assumed": measurements.assumed,
                "constraints": request.constraints.model_dump(),
                "wall_axis_status": analysis.wall_axes.status,
            },
            sort_keys=True,
        )
        if state.proposal_key == cache_key and state.proposal is not None:
            return state.proposal.model_copy(deep=True)
        # Initial proposal plus one repair, shared across HTTP retries and dimension
        # revisions. New flags or measurements cannot reset the paid-call budget.
        if state.scene_attempts >= 2:
            logging.getLogger(__name__).warning(
                "Scene proposal incomplete: %s",
                json.dumps(
                    {
                        "analysis_id": analysis.analysis_id,
                        "reason": "proposal_limit_reached",
                    }
                ),
            )
            return SceneProposalResponse.model_validate(
                {
                    **base,
                    "status": "incomplete",
                    "scene": None,
                    "validation": None,
                    "issues": [
                        issue(
                            "proposal_limit_reached",
                            "This analysis has used its initial scene attempt and repair allowance.",
                        )
                    ],
                }
            )
        key = self.settings.openai_api_key
        if (
            not self.settings.analysis_enabled
            or key is None
            or not key.get_secret_value().strip()
        ):
            raise AnalysisFailure(
                503,
                "analysis_unavailable",
                "Scene proposal is disabled or not configured.",
            )
        prepared = await anyio.to_thread.run_sync(self._resolve, analysis.input_id, owner)
        images = await anyio.to_thread.run_sync(self._load_images, prepared, owner)
        draft, report, scene = None, None, None
        best_partial = None
        best_partial_draft = None
        best_quality = (-1, -1, -10000, -1, -1)
        observed = {item.id: item for item in [*analysis.observations.objects, *analysis.observations.openings]}
        problems = []
        repair_failure: AnalysisFailure | None = None
        while state.scene_attempts < 2:
            # Reservation is shared by every run/retry using this analysis.
            # An ambiguous first request still spends the initial construction allowance.
            operation: Literal["scene_proposal", "scene_repair"] = (
                "scene_proposal" if state.scene_attempts == 0 else "scene_repair"
            )
            previous_draft = draft
            try:
                payload = await anyio.to_thread.run_sync(
                    partial(build_proposal_request, operation=operation),
                    self.settings,
                    analysis,
                    measurements,
                    draft,
                    problems,
                    images,
                )
            except AnalysisFailure as error:
                if best_partial is None:
                    raise
                repair_failure = error
                await self._owned_analysis(analysis.analysis_id, owner)
                break
            await self._owned_analysis(analysis.analysis_id, owner)
            state.scene_attempts += 1
            started, outcome, response = perf_counter(), "interrupted", None
            details: dict[str, object] | None = None
            output_limited = False
            repair_failure = None
            try:
                with anyio.fail_after(self.settings.request_timeout(operation)):
                    response = await self.analyzer.request(payload, operation=operation)
                try:
                    decoded = response_json(
                        response, self.settings.analysis_max_output_bytes
                    )
                except AnalysisFailure as error:
                    output_limited = (
                        error.code == "analysis_incomplete"
                        and error.diagnostics is not None
                        and error.diagnostics.get("reason") == "max_output_tokens"
                    )
                    if error.code != "invalid_analysis" and not output_limited:
                        raise
                    details = error.diagnostics
                    decoded = None
                try:
                    draft = parse_scene_draft(decoded, analysis)
                    if operation == "scene_repair" and previous_draft is not None:
                        draft = merge_repair(analysis, previous_draft, draft, problems)
                except (ValidationError, ValueError, RecursionError) as error:
                    details = details or (
                        validation_diagnostics(error, SceneDraft)
                        if isinstance(error, ValidationError)
                        else {"reason": "draft_schema"}
                    )
                    draft = None
                    scene, report = None, None
                    problems = [
                        issue(
                            "draft_schema",
                            "Scene construction exceeded its response limit before completing all observed objects."
                            if output_limited
                            else "Return exactly the requested scene draft schema with finite, valid geometry fields.",
                        )
                    ]
                else:
                    scene, report, problems = await anyio.to_thread.run_sync(
                        assemble, analysis, measurements, draft
                    )
                    if scene is None:
                        candidate = await anyio.to_thread.run_sync(
                            assemble_partial, analysis, measurements, draft
                        )
                        # Send omissions/dependent failures to the same bounded
                        # repair. A global inventory mismatch alone used to hide
                        # exactly which missing observations needed restoring.
                        if candidate[0] is not None:
                            unresolved_ids = {item.entity_id for item in candidate[3]}
                            problems = [*candidate[2], *[p for p in problems if unresolved_ids.intersection(p.entity_ids)]][:50]
                        retained = candidate[0]
                        if retained is not None:
                            major_high, major, secondary, total = partial_quality(retained, observed)
                            candidate_fidelity = evaluate_fidelity(analysis, retained)
                            failures = sum(check.required and not check.passed for check in candidate_fidelity.checks)
                            quality = (major_high, major, -failures, secondary, total)
                            if quality > best_quality:
                                best_partial = candidate
                                best_partial_draft = draft.model_copy(deep=True)
                                best_quality = quality
                            if not candidate[2] and not candidate[3]:
                                scene, report, problems = candidate[:3]
                if problems and details is None:
                    details = {
                        "reason": "proposal_validation",
                        "issue_codes": list(
                            dict.fromkeys(problem.code for problem in problems)
                        ),
                        "geometry_codes": list(
                            dict.fromkeys(error.code for error in report.errors)
                        )[:12]
                        if report
                        else [],
                    }
                outcome = (
                    "analysis_incomplete"
                    if output_limited
                    else "success"
                    if scene is not None
                    else "invalid_proposal"
                )
            except (APITimeoutError, TimeoutError):
                outcome = "analysis_timeout"
                repair_failure = AnalysisFailure(
                    504, "analysis_timeout", "The scene provider timed out."
                )
                if best_partial is None:
                    raise repair_failure from None
            except APIStatusError as error:
                details = {
                    "provider_status": error.status_code,
                    "provider_request_id": provider_id(error.request_id),
                }
                outcome = (
                    "provider_rate_limited"
                    if error.status_code == 429
                    else "provider_unavailable"
                )
                repair_failure = AnalysisFailure(
                    429 if error.status_code == 429 else 503,
                    "provider_rate_limited"
                    if error.status_code == 429
                    else "provider_unavailable",
                    "Scene proposal is temporarily unavailable.",
                )
                if best_partial is None:
                    raise repair_failure from None
            except APIError:
                outcome = "provider_unavailable"
                repair_failure = AnalysisFailure(
                    503,
                    "provider_unavailable",
                    "The scene provider could not complete this request.",
                )
                if best_partial is None:
                    raise repair_failure from None
            except AnalysisFailure as error:
                details = error.diagnostics
                outcome = error.code
                repair_failure = error
                if best_partial is None:
                    raise
            except ValueError:
                outcome = "invalid_analysis"
                repair_failure = AnalysisFailure(
                    502,
                    "invalid_analysis",
                    "The scene provider returned an invalid response.",
                )
                if best_partial is None:
                    raise repair_failure from None
            finally:
                self._record(
                    state,
                    started,
                    outcome,
                    response,
                    stage="scene_proposal",
                    details={
                        "output_token_limit": self.settings.scene_max_output_tokens,
                        "reasoning_effort": self.settings.model_route(operation).reasoning_effort,
                        **(details or {}),
                    },
                    operation=operation,
                    model=payload["model"],
                )
            # Deletion/replacement/expiry while awaiting a paid call revokes results
            # and prevents repair from sending the observations again.
            await self._owned_analysis(analysis.analysis_id, owner)
            if repair_failure is not None:
                break
            if scene is not None:
                break
            if best_partial is not None and draft is not None and not actionable_repair(analysis, draft, problems):
                break
        unresolved = []
        if scene is None and best_partial is not None:
            final_problems = problems
            scene, report, problems, unresolved = best_partial
            draft = best_partial_draft
            if repair_failure is not None:
                problems = [
                    issue(
                        "repair_failed",
                        f"The repair did not complete ({repair_failure.code}); showing the validated partial result from the earlier attempt.",
                    ),
                    *problems,
                ][:50]
            elif any(problem.code == "draft_schema" for problem in final_problems):
                problems = [
                    issue(
                        "repair_failed",
                        final_problems[0].message
                        + " Showing the earlier validated partial result.",
                    ),
                    *problems,
                ][:50]
        elif scene is None and draft is not None:
            (
                scene,
                partial_report,
                partial_problems,
                unresolved,
            ) = await anyio.to_thread.run_sync(
                assemble_partial, analysis, measurements, draft
            )
            if scene is not None or unresolved:
                report, problems = partial_report, partial_problems
        if scene is not None and analysis.wall_axes.status == "estimated":
            problems = [*problems, issue("fidelity", "The wall-to-measurement assignment remains unconfirmed; this preview uses an approximate orientation.", *analysis.wall_axes.candidate_wall_ids)][:50]
        result = SceneProposalResponse.model_validate(
            {
                **base,
                "status": ("partial" if unresolved or problems else "ready")
                if scene is not None
                else "incomplete",
                "scene": scene,
                "validation": report,
                "fidelity": evaluate_fidelity(analysis, scene) if scene is not None else None,
                "issues": problems,
                "unresolved_entities": unresolved,
            }
        )
        state.proposal_key, state.proposal = cache_key, result.model_copy(deep=True)
        if unresolved:
            logging.getLogger(__name__).warning(
                "Reconstruction omissions: %s",
                json.dumps(omission_diagnostics(analysis, draft, scene, unresolved), ensure_ascii=True),
            )
        return result
