"""Count actual multipart body bytes before parsing; independent of spooling."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from roometry.uploads.errors import UploadFailure


class UploadBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["path"] != "/api/rooms/inputs"
            or scope["method"] != "POST"
        ):
            await self.app(scope, receive, send)
            return
        failure = UploadFailure(
            413, "request_too_large", "The combined upload request is too large."
        )
        lengths = [
            value for key, value in scope["headers"] if key.lower() == b"content-length"
        ]
        try:
            if len(lengths) > 1 or (lengths and (not lengths[0].isdigit())):
                raise ValueError
            oversized = bool(lengths and int(lengths[0]) > self.max_bytes)
        except ValueError:
            invalid = UploadFailure(
                400, "invalid_multipart", "Invalid upload request headers."
            )
            await JSONResponse(
                invalid.response.model_dump(mode="json"), status_code=400
            )(scope, receive, send)
            return
        if oversized:
            await JSONResponse(
                failure.response.model_dump(mode="json"), status_code=413
            )(scope, receive, send)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Raised while the endpoint consumes its stream; the upload
                    # exception handler serializes it and the parser closes files.
                    raise failure
            return message

        await self.app(scope, limited_receive, send)
