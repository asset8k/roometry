"""Session-scoped analysis with bounded spend, retries, memory, and lifetime.

The local cache shares the uploads adapter's one-worker/restart limitations.
It stores observations, never image bytes. Generation quotas remain separate.
"""

import logging
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from time import perf_counter
from uuid import uuid4

import anyio
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError
from openai.types.responses import Response, ResponseUsage

from roometry.config import Settings
from roometry.uploads.errors import UploadFailure
from roometry.uploads.schemas import PreparedRoomInput
from roometry.uploads.storage import InputStorage
from roometry.vision.analyzer import PhotoAnalyzer, parse_observations
from roometry.vision.errors import AnalysisFailure
from roometry.vision.schemas import RoomAnalysisResponse, RoomObservations


@dataclass
class AnalysisState:
    owner: str
    expires_at: datetime
    analysis_id: str
    attempts: int = 0
    result: RoomAnalysisResponse | None = None


@dataclass(frozen=True)
class AttemptUsage:
    analysis_id: str
    model: str
    attempt: int
    outcome: str
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
            attempt=state.attempts,
            outcome=outcome,
            latency_ms=round((perf_counter() - started) * 1000),
            input_tokens=count(getattr(usage, "input_tokens", None)),
            output_tokens=count(getattr(usage, "output_tokens", None)),
            total_tokens=count(getattr(usage, "total_tokens", None)),
            cached_input_tokens=count(getattr(input_details, "cached_tokens", None)),
            reasoning_tokens=count(getattr(output_details, "reasoning_tokens", None)),
        )
        self.usage_records.append(record)
        logging.getLogger(__name__).info(
            "Room analysis provider attempt completed.",
            extra={"analysis_usage": asdict(record)},
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
                self._record(state, started, outcome, response)

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
