"""Session-scoped analysis with bounded spend, retries, memory, and lifetime.

The local cache shares the uploads adapter's one-worker/restart limitations.
It stores observations, never image bytes. Generation quotas remain separate.
"""

import json
import logging
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from time import perf_counter
from uuid import uuid4

import anyio
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError
from openai.types.responses import Response, ResponseUsage
from pydantic import ValidationError

from roometry.config import Settings
from roometry.uploads.errors import UploadFailure
from roometry.uploads.schemas import PreparedRoomInput
from roometry.uploads.storage import InputStorage
from roometry.vision.analyzer import PhotoAnalyzer, parse_observations, response_json
from roometry.vision.diagnostics import (
    provider_id,
    response_diagnostics,
    validation_diagnostics,
)
from roometry.vision.errors import AnalysisFailure
from roometry.vision.proposal_request import build_proposal_request
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


@dataclass(frozen=True)
class AttemptUsage:
    analysis_id: str
    model: str
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
        # Bounded diagnostic history; structured log records also cover failures.
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
            for input_id, state in states:
                try:
                    self.storage.resolve(input_id, state.owner)
                except UploadFailure:
                    invalid.append(input_id)
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
    ) -> None:
        usage = getattr(response, "usage", None)
        usage = usage if isinstance(usage, ResponseUsage) else None

        def count(value: object) -> int | None:
            return value if type(value) is int and value >= 0 else None

        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)

        record = AttemptUsage(
            analysis_id=state.analysis_id,
            model=self.settings.model_name,
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
            with anyio.fail_after(self.settings.analysis_timeout_seconds):
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
        state = self._states.get(input_id)
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
            self._states[input_id] = state

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
                response = await self.analyzer.request(payload)
                observations = parse_observations(
                    response,
                    [image.image_id for image in prepared.images],
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
                raise
            except ValueError:
                outcome = "invalid_analysis"
                raise AnalysisFailure(
                    502,
                    "invalid_analysis",
                    "The analysis provider returned an invalid response.",
                ) from None
            finally:
                self._record(state, started, outcome, response, details=details)

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

    async def _owned_analysis(self, analysis_id: str, owner: str) -> AnalysisState:
        for input_id, state in list(self._states.items()):
            if (
                state.owner == owner
                and state.analysis_id == analysis_id
                and state.result is not None
            ):
                await anyio.to_thread.run_sync(self._resolve, input_id, owner)
                if self.storage.now() >= state.expires_at:
                    break
                return state
        raise AnalysisFailure(
            404, "analysis_not_found", "This analysis is unavailable or has expired."
        )

    async def propose(
        self, request: SceneProposalRequest, owner: str
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
            with anyio.fail_after(2 * self.settings.analysis_timeout_seconds):
                return await self._propose(request, owner)
        except TimeoutError:
            raise AnalysisFailure(
                504,
                "analysis_timeout",
                "Scene proposal timed out. Your observations remain cached until expiry.",
            ) from None
        finally:
            self._capacity.release()

    async def _propose(
        self, request: SceneProposalRequest, owner: str
    ) -> SceneProposalResponse:
        state = await self._owned_analysis(request.analysis_id, owner)
        analysis = state.result
        assert analysis is not None
        if request.dimensions is not None:
            for axis in AXES:
                value = getattr(request.dimensions, axis)
                if value is not None:
                    state.confirmed_dimensions[axis] = value
        measurements = resolve_measurements(
            analysis, state.confirmed_dimensions, request.allow_estimated_dimensions
        )
        base = {
            "analysis_id": analysis.analysis_id,
            "input_id": analysis.input_id,
            "dimensions": measurements.dimensions,
            "dimensions_to_confirm": measurements.to_confirm,
            "estimated_axes": measurements.estimated,
            "expires_at": analysis.expires_at,
            "warnings": [
                "This is an approximate base scene; review estimated sizes, layout, and uncertain identities."
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
        problems = preflight(analysis)
        if problems:
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
                    "issues": problems,
                }
            )
        cache_key = json.dumps(
            {
                "dimensions": measurements.dimensions.model_dump(),
                "estimated": measurements.estimated,
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
        draft, report, scene = None, None, None
        problems = []
        while state.scene_attempts < 2:
            payload = await anyio.to_thread.run_sync(
                build_proposal_request,
                self.settings,
                analysis,
                measurements,
                draft,
                problems,
            )
            await self._owned_analysis(analysis.analysis_id, owner)
            state.scene_attempts += 1
            started, outcome, response = perf_counter(), "interrupted", None
            details: dict[str, object] | None = None
            try:
                with anyio.fail_after(self.settings.analysis_timeout_seconds):
                    response = await self.analyzer.request(payload)
                try:
                    decoded = response_json(
                        response, self.settings.analysis_max_output_bytes
                    )
                except AnalysisFailure as error:
                    if error.code != "invalid_analysis":
                        raise
                    details = error.diagnostics
                    decoded = None
                try:
                    draft = SceneDraft.model_validate(decoded)
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
                            "Return exactly the requested scene draft schema with finite, valid geometry fields.",
                        )
                    ]
                else:
                    scene, report, problems = await anyio.to_thread.run_sync(
                        assemble, analysis, measurements, draft
                    )
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
                outcome = "success" if scene is not None else "invalid_proposal"
            except (APITimeoutError, TimeoutError):
                outcome = "analysis_timeout"
                raise AnalysisFailure(
                    504, "analysis_timeout", "The scene provider timed out."
                ) from None
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
                raise AnalysisFailure(
                    429 if error.status_code == 429 else 503,
                    "provider_rate_limited"
                    if error.status_code == 429
                    else "provider_unavailable",
                    "Scene proposal is temporarily unavailable.",
                ) from None
            except APIError:
                outcome = "provider_unavailable"
                raise AnalysisFailure(
                    503,
                    "provider_unavailable",
                    "The scene provider could not complete this request.",
                ) from None
            except AnalysisFailure as error:
                details = error.diagnostics
                outcome = error.code
                raise
            except ValueError:
                outcome = "invalid_analysis"
                raise AnalysisFailure(
                    502,
                    "invalid_analysis",
                    "The scene provider returned an invalid response.",
                ) from None
            finally:
                self._record(
                    state,
                    started,
                    outcome,
                    response,
                    stage="scene_proposal",
                    details=details,
                )
            # Deletion/replacement/expiry while awaiting a paid call revokes results
            # and prevents repair from sending the observations again.
            await self._owned_analysis(analysis.analysis_id, owner)
            if scene is not None:
                break
        result = SceneProposalResponse.model_validate(
            {
                **base,
                "status": "ready" if scene is not None else "incomplete",
                "scene": scene,
                "validation": report,
                "issues": problems,
            }
        )
        state.proposal_key, state.proposal = cache_key, result.model_copy(deep=True)
        return result
