"""One bounded Responses API attempt; local Pydantic validation follows JSON output.

Use the Pydantic-generated strict JSON Schema with responses.create rather than
parse so usage is available even when a completed output fails semantic validation.
"""

import base64
import json

from openai import AsyncOpenAI
from openai.types.responses import (
    Response,
    ResponseInputContentParam,
    ResponseOutputMessage,
    ResponseOutputRefusal,
    ResponseOutputText,
    ResponseReasoningItem,
)
from openai.types.responses.response_create_params import (
    ResponseCreateParamsNonStreaming,
)
from pydantic import ValidationError

from roometry.config import Settings
from roometry.uploads.schemas import PreparedRoomInput
from roometry.vision.errors import AnalysisFailure
from roometry.vision.prompts import OBSERVATION_INSTRUCTIONS, observation_context
from roometry.vision.schemas import RoomObservations


class PhotoAnalyzer:
    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None):
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    def build_request(
        self, prepared: PreparedRoomInput, images: list[bytes]
    ) -> ResponseCreateParamsNonStreaming:
        if len(images) != len(prepared.images):
            raise AnalysisFailure(
                503,
                "storage_unavailable",
                "The stored photos are unavailable. Upload them again.",
            )
        # Bound before allocating base64 strings, then check the complete payload.
        encoded_size = sum(4 * ((len(image) + 2) // 3) for image in images)
        if encoded_size > self.settings.analysis_max_request_bytes:
            raise AnalysisFailure(
                413,
                "request_too_large",
                "These photos are too large for analysis. Upload smaller photos.",
            )
        content: list[ResponseInputContentParam] = [
            {"type": "input_text", "text": observation_context(prepared)}
        ]
        for metadata, image in zip(prepared.images, images):
            content.extend(
                [
                    {
                        "type": "input_text",
                        "text": f"Photo evidence ID: {metadata.image_id}",
                    },
                    {
                        "type": "input_image",
                        "detail": "high",
                        "image_url": "data:image/jpeg;base64,"
                        + base64.b64encode(image).decode("ascii"),
                    },
                ]
            )
        payload: ResponseCreateParamsNonStreaming = {
            "model": self.settings.model_name,
            "instructions": OBSERVATION_INSTRUCTIONS,
            "input": [{"role": "user", "content": content}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "room_observations",
                    "strict": True,
                    "schema": RoomObservations.model_json_schema(),
                }
            },
            "max_output_tokens": self.settings.analysis_max_output_tokens,
            "store": False,
            "tools": [],
            "truncation": "disabled",
        }
        # This conservative encoding includes schema/instructions/context and all
        # images. No photos are silently dropped to fit the provider request cap.
        if (
            len(json.dumps(payload, ensure_ascii=True).encode("utf-8"))
            > self.settings.analysis_max_request_bytes
        ):
            raise AnalysisFailure(
                413,
                "request_too_large",
                "These photos and notes exceed the analysis request limit.",
            )
        return payload

    async def request(self, payload: ResponseCreateParamsNonStreaming) -> Response:
        if self._client is None:
            key = self.settings.openai_api_key
            if key is None or not key.get_secret_value().strip():
                raise AnalysisFailure(
                    503, "analysis_unavailable", "Room analysis is not configured."
                )
            self._client = AsyncOpenAI(
                api_key=key.get_secret_value(),
                base_url="https://api.openai.com/v1",
                max_retries=0,
                timeout=self.settings.analysis_timeout_seconds,
            )
        # Also override injected clients: retries belong to the service's single
        # per-input budget, including explicit retries after a failed HTTP request.
        response = await self._client.with_options(
            max_retries=0, timeout=self.settings.analysis_timeout_seconds
        ).responses.create(**payload)
        if not isinstance(response, Response):
            raise AnalysisFailure(
                502, "invalid_analysis", "The model returned an unexpected analysis."
            )
        return response

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.close()


def parse_observations(
    response: Response, image_ids: list[str], max_bytes: int
) -> RoomObservations:
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        raise AnalysisFailure(
            502,
            "invalid_analysis",
            "The analysis provider returned an invalid response.",
        )
    for item in output:
        content = getattr(item, "content", None)
        if (
            isinstance(item, ResponseOutputMessage)
            and isinstance(content, list)
            and any(isinstance(part, ResponseOutputRefusal) for part in content)
        ):
            raise AnalysisFailure(
                422,
                "analysis_refused",
                "The model could not analyze these photos. Try different room photos.",
            )
    if response.status == "incomplete":
        raise AnalysisFailure(
            502,
            "analysis_incomplete",
            "The analysis did not finish within its output limits. Try fewer room photos.",
        )
    if response.status != "completed":
        raise AnalysisFailure(
            503,
            "provider_unavailable",
            "Room analysis is temporarily unavailable. Please try again.",
        )
    texts = []
    for item in output:
        if isinstance(item, ResponseOutputMessage):
            if getattr(item, "status", None) != "completed":
                raise AnalysisFailure(
                    502,
                    "analysis_incomplete",
                    "The model returned an incomplete analysis.",
                )
            content = getattr(item, "content", None)
            if not isinstance(content, list) or any(
                not isinstance(part, ResponseOutputText) for part in content
            ):
                raise AnalysisFailure(
                    502,
                    "invalid_analysis",
                    "The model returned an unexpected analysis.",
                )
            texts.extend(getattr(part, "text", None) for part in content)
        elif not isinstance(item, ResponseReasoningItem):
            raise AnalysisFailure(
                502, "invalid_analysis", "The model returned an unexpected analysis."
            )
    try:
        if (
            len(texts) != 1
            or not isinstance(texts[0], str)
            or len(texts[0].encode("utf-8")) > max_bytes
        ):
            raise ValueError("Unexpected output size")

        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON field")
                result[key] = value
            return result

        observations = RoomObservations.model_validate(
            json.loads(texts[0], object_pairs_hook=unique_keys)
        )
        if observations.image_ids != image_ids:
            raise ValueError("Image evidence mismatch")
        return observations
    except (ValidationError, ValueError, RecursionError):
        raise AnalysisFailure(
            502,
            "invalid_analysis",
            "The model returned an invalid analysis. Please try again.",
        ) from None
