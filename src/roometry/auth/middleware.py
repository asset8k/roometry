"""Bound credential bodies before JSON parsing; never echo credentials in errors."""

import json

from starlette.responses import JSONResponse


class AuthBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].startswith("/api/auth/")
        ):
            return await self.app(scope, receive, send)

        async def fail(status, code, message):
            await JSONResponse(
                {"detail": [{"code": code, "message": message}]},
                status_code=status,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)

        headers = scope["headers"]
        lengths = [v for k, v in headers if k.lower() == b"content-length"]
        if len(lengths) > 1 or (lengths and not lengths[0].isdigit()):
            return await fail(400, "invalid_request", "Invalid request headers.")
        if lengths and int(lengths[0]) > 4096:
            return await fail(
                413,
                "request_too_large",
                "Keep sign-in details within the displayed limits.",
            )
        content_type = next(
            (v for k, v in headers if k.lower() == b"content-type"), b""
        )
        if content_type.split(b";", 1)[0].strip().lower() != b"application/json":
            return await fail(
                415, "unsupported_media_type", "Send sign-in details as JSON."
            )
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 4096:
                return await fail(
                    413,
                    "request_too_large",
                    "Keep sign-in details within the displayed limits.",
                )
            if not message.get("more_body", False):
                break

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result

        def finite(_value):
            raise ValueError()

        try:
            json.loads(body, object_pairs_hook=unique, parse_constant=finite)
        except (ValueError, UnicodeError, RecursionError):
            return await fail(
                422, "invalid_request", "Check the sign-in details and try again."
            )
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
