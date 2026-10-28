"""One bounded Responses API attempt; local Pydantic validation follows JSON output.

Use the Pydantic-generated strict JSON Schema with responses.create rather than
parse so usage is available even when a completed output fails semantic validation.
"""

import base64
import json
import logging
from collections import deque
from copy import deepcopy
from dataclasses import asdict
from time import perf_counter

from openai import AsyncOpenAI, APIConnectionError, APIError, APIStatusError, APITimeoutError
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
from roometry.model_calls import ModelAttempt, ModelOperation, current_observer
from roometry.uploads.schemas import PreparedRoomInput
from roometry.vision.diagnostics import provider_id, validation_diagnostics
from roometry.vision.errors import AnalysisFailure
from roometry.vision.prompts import OBSERVATION_INSTRUCTIONS, observation_context
from roometry.vision.schemas import RoomObservations
from roometry.vision.evidence import strict_schema
from roometry.vision.observation_recovery import recover_optional_observations


class PhotoAnalyzer:
    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None):
        self.settings = settings
        self._client = client
        self._owns_client = client is None
        self.usage_records: deque[ModelAttempt] = deque(maxlen=256)

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
        # Ground evidence at decoding time, not merely in prose instructions.
        # UUID shape alone cannot distinguish this upload from another room.
        image_ids = [image.image_id for image in prepared.images]
        schema = strict_schema(RoomObservations)
        for properties in [schema["properties"], *(
            definition.get("properties", {}) for definition in schema.get("$defs", {}).values()
        )]:
            for name in ("image_ids", "source_image_ids"):
                if name in properties:
                    properties[name]["items"]["enum"] = image_ids
                    properties[name]["maxItems"] = len(image_ids)
        schema["properties"]["image_ids"]["minItems"] = len(image_ids)
        payload: ResponseCreateParamsNonStreaming = {
            "model": self.settings.model_route("analysis").model,
            "instructions": OBSERVATION_INSTRUCTIONS,
            "input": [{"role": "user", "content": content}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "room_observations",
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": self.settings.analysis_max_output_tokens,
            "store": False,
            "tools": [],
            "truncation": "disabled",
        }
        effort = self.settings.model_route("analysis").reasoning_effort
        if effort != "default":
            payload["reasoning"] = {"effort": effort}
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

    async def request(
        self, payload: ResponseCreateParamsNonStreaming, *, operation: ModelOperation
    ) -> Response:
        # Routing belongs to trusted application code, never model tool arguments.
        route = self.settings.model_route(operation)
        payload = {**payload, "model": route.model}
        if route.model.startswith(("gpt-6", "gpt-5.6")):
            payload["prompt_cache_options"] = {"mode": self.settings.model_prompt_cache_mode}
        if route.reasoning_effort == "default":
            payload.pop("reasoning", None)
        else:
            payload["reasoning"] = {"effort": route.reasoning_effort}
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
        observer = current_observer()
        if observer is not None:
            await observer.before_call()
        response = None
        started, outcome, request_id = perf_counter(), "interrupted", None
        try:
            response = await self._client.with_options(
                max_retries=0, timeout=self.settings.analysis_timeout_seconds
            ).responses.create(**payload)
            outcome = (
                response.status if isinstance(response, Response) else "unexpected_response"
            )
        except APITimeoutError:
            outcome = "timeout"
            raise
        except APIStatusError as error:
            outcome = "rate_limited" if error.status_code == 429 else "provider_error"
            request_id = provider_id(error.request_id)
            raise
        except APIConnectionError:
            outcome = "connection_error"
            raise
        except APIError:
            outcome = "provider_error"
            raise
        finally:
            usage = response.usage if isinstance(response, Response) else None
            if observer is not None:
                observer.after_call(usage)
            record = ModelAttempt(
                operation=operation,
                run_id=getattr(observer, "run_id", None),
                requested_model=route.model,
                model=(response.model or route.model) if isinstance(response, Response) else route.model,
                outcome=outcome or "unknown",
                latency_ms=round((perf_counter() - started) * 1000),
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                total_tokens=usage.total_tokens if usage else None,
                cached_input_tokens=getattr(
                    getattr(usage, "input_tokens_details", None), "cached_tokens", None
                ),
                reasoning_tokens=getattr(
                    getattr(usage, "output_tokens_details", None), "reasoning_tokens", None
                ),
                response_id=provider_id(getattr(response, "id", None)),
                provider_request_id=(
                    provider_id(getattr(response, "_request_id", None)) or request_id
                ),
                cache_write_tokens=getattr(getattr(usage, "input_tokens_details", None), "cache_write_tokens", None),
                prompt_cache_mode=payload.get("prompt_cache_options", {}).get("mode"),
                request_text_characters=sum(len(part.get("text", "")) for message in payload.get("input", [])
                    if isinstance(message, dict) and isinstance(message.get("content"), list)
                    for part in message['content'] if isinstance(part, dict) and part.get('type') == 'input_text')
                    + len(payload.get('instructions', '')) + len(json.dumps(payload.get('text', {})))
                    + len(json.dumps(payload.get('tools', []))),
                request_image_count=sum(part.get('type') == 'input_image' for message in payload.get('input', [])
                    if isinstance(message, dict) and isinstance(message.get('content'), list)
                    for part in message['content'] if isinstance(part, dict)),
            )
            self.usage_records.append(record)
            logging.getLogger(__name__).log(
                logging.INFO if outcome == "completed" else logging.WARNING,
                "Room provider request completed: %s", json.dumps(asdict(record)),
                extra={"model_usage": asdict(record)},
            )
        if not isinstance(response, Response):
            raise AnalysisFailure(
                502,
                "invalid_analysis",
                "The model returned an unexpected analysis.",
                diagnostics={"reason": "unexpected_output_type"},
            )
        return response

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.close()


def response_json(response: Response, max_bytes: int) -> object:
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        raise AnalysisFailure(
            502,
            "invalid_analysis",
            "The analysis provider returned an invalid response.",
            diagnostics={"reason": "missing_output"},
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
                "The model could not complete this room request. Review the room input.",
            )
    if getattr(response, "status", None) == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        token_limited = reason == "max_output_tokens"
        raise AnalysisFailure(
            502,
            "analysis_incomplete",
            "The model result did not finish within its output limits."
            if token_limited
            else "The model could not complete this room request.",
            diagnostics={
                "reason": "max_output_tokens" if token_limited else "incomplete_output"
            },
        )
    if getattr(response, "status", None) != "completed":
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
                    diagnostics={"reason": "unexpected_output"},
                )
            texts.extend(getattr(part, "text", None) for part in content)
        elif not isinstance(item, ResponseReasoningItem):
            raise AnalysisFailure(
                502,
                "invalid_analysis",
                "The model returned an unexpected analysis.",
                diagnostics={"reason": "unexpected_output_type"},
            )
    reason = "output_validation"
    try:
        reason = (
            "output_count"
            if len(texts) != 1
            else "output_text_type"
            if not isinstance(texts[0], str)
            else "output_too_large"
        )
        if (
            len(texts) != 1
            or not isinstance(texts[0], str)
            or len(texts[0].encode("utf-8")) > max_bytes
        ):
            raise ValueError("Unexpected output size")

        reason = "invalid_json"

        def unique_keys(pairs):
            nonlocal reason
            result = {}
            for key, value in pairs:
                if key in result:
                    reason = "duplicate_json_field"
                    raise ValueError("Duplicate JSON field")
                result[key] = value
            return result

        return json.loads(texts[0], object_pairs_hook=unique_keys)
    except (ValidationError, ValueError, RecursionError):
        raise AnalysisFailure(
            502,
            "invalid_analysis",
            "The model returned an invalid analysis. Please try again.",
            diagnostics={"reason": reason},
        ) from None


def _normalize_observations(decoded: object, image_ids: list[str]) -> object:
    """Normalize lossless representations of dimensions and evidence sets.

    JSON Schema cannot express our model validators. Normalize only this lossless
    representation; unknown fields, partial dictionaries and real values still
    pass through strict Pydantic validation unchanged.
    """
    if not isinstance(decoded, dict):
        return decoded
    normalized = deepcopy(decoded)
    manifest = normalized.get("image_ids")
    if isinstance(manifest, list) and all(isinstance(value, str) for value in manifest):
        # Only canonicalize an exact set of supplied photos. A missing or foreign
        # ID must still fail; no evidence is inferred or reassigned.
        if set(manifest) == set(image_ids):
            normalized["image_ids"] = list(image_ids)
    for kind in ("walls", "objects", "openings", "relationships"):
        entities = normalized.get(kind)
        if isinstance(entities, list):
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                if kind == "objects":
                    from roometry.vision.features import normalize_fixture_category
                    normalize_fixture_category(entity)
                span = entity.get("span")
                if isinstance(span, dict):
                    refs = span.get("source_image_ids")
                    if isinstance(refs, list) and all(isinstance(value, str) for value in refs):
                        span["source_image_ids"] = list(dict.fromkeys(refs))
                references = entity.get("source_image_ids")
                if isinstance(references, list) and all(isinstance(value, str) for value in references):
                    entity["source_image_ids"] = list(dict.fromkeys(references))

    finishes = normalized.get("finishes")
    if isinstance(finishes, dict):
        refs = finishes.get("source_image_ids")
        if isinstance(refs, list) and all(isinstance(value, str) for value in refs):
            finishes["source_image_ids"] = list(dict.fromkeys(refs))

    def unknown(value: object, keys: set[str]) -> bool:
        return (
            isinstance(value, dict)
            and set(value) == keys
            and all(item is None for item in value.values())
        )

    if unknown(
        normalized.get("estimated_dimensions"), {"width_m", "length_m", "height_m"}
    ):
        normalized["estimated_dimensions"] = None
    objects = normalized.get("objects")
    if isinstance(objects, list):
        for obj in objects:
            if isinstance(obj, dict) and unknown(
                obj.get("estimated_dimensions_m"), {"width", "depth", "height"}
            ):
                obj["estimated_dimensions_m"] = None
    return normalized


def parse_observations(
    response: Response, image_ids: list[str], max_bytes: int
) -> RoomObservations:
    decoded = response_json(response, max_bytes)
    try:
        normalized = _normalize_observations(decoded, image_ids)
        observations = RoomObservations.model_validate(normalized)
    except ValidationError as error:
        observations = recover_optional_observations(normalized)
        if observations is None:
            raise AnalysisFailure(
                502,
                "invalid_analysis",
                "The model returned inconsistent room observations. Please try again.",
                diagnostics=validation_diagnostics(error, RoomObservations),
            ) from None
    except (ValueError, RecursionError):
        raise AnalysisFailure(
            502,
            "invalid_analysis",
            "The model returned an invalid analysis. Please try again.",
            diagnostics={"reason": "invalid_observations"},
        ) from None
    if observations.image_ids != image_ids:
        raise AnalysisFailure(
            502,
            "invalid_analysis",
            "The model returned observations for unexpected photos. Please try again.",
            diagnostics={"reason": "image_evidence_mismatch"},
        )
    return observations
