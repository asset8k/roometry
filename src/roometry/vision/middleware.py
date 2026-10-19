"""Bound analysis JSON before FastAPI reads it, including chunked requests."""

import json

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from roometry.vision.errors import AnalysisFailure

ANALYSIS_PATH = "/api/rooms/analyze"
ANALYSIS_PATHS = {ANALYSIS_PATH, "/api/rooms/propose-scene"}


class AnalysisRequestLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["path"] not in ANALYSIS_PATHS
            or scope["method"] != "POST"
        ):
            await self.app(scope, receive, send)
            return
        lengths = [
            value for key, value in scope["headers"] if key.lower() == b"content-length"
        ]
        failure: AnalysisFailure | None = None
        try:
            if len(lengths) > 1 or (lengths and not lengths[0].isdigit()):
                raise ValueError
            if lengths and int(lengths[0]) > self.max_bytes:
                failure = AnalysisFailure(
                    413, "request_too_large", "The analysis request is too large."
                )
        except ValueError:
            failure = AnalysisFailure(
                400, "invalid_request", "Invalid analysis request headers."
            )
        if failure is not None:
            await JSONResponse(
                failure.response.model_dump(mode="json"),
                status_code=failure.status,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
            return
        content_type = next(
            (
                value
                for key, value in scope["headers"]
                if key.lower() == b"content-type"
            ),
            b"",
        )
        if content_type.split(b";", 1)[0].strip().lower() != b"application/json":
            failure = AnalysisFailure(
                415, "unsupported_media_type", "Send the room analysis request as JSON."
            )
        body = bytearray()
        while failure is None:
            message = await receive()
            if message["type"] == "http.disconnect":
                failure = AnalysisFailure(
                    400, "invalid_request", "The analysis request was interrupted."
                )
                break
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                failure = AnalysisFailure(
                    413, "request_too_large", "The analysis request is too large."
                )
                break
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        if failure is None:

            def unique_keys(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate JSON field")
                    result[key] = value
                return result

            try:
                json.loads(body, object_pairs_hook=unique_keys)
            except (ValueError, RecursionError):
                failure = AnalysisFailure(
                    422,
                    "invalid_request",
                    "Send a valid room analysis request as JSON.",
                )
        if failure is not None:
            await JSONResponse(
                failure.response.model_dump(mode="json"),
                status_code=failure.status,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
            return
        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay_receive, send)
