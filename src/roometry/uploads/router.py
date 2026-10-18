"""Private temporary room inputs; no generation run or public image serving."""

import json
from typing import Any, cast

import anyio
from fastapi import APIRouter, Request, Response
from pydantic import ValidationError
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect

from roometry.config import Settings
from roometry.uploads.errors import UploadFailure
from roometry.uploads.schemas import (
    MAX_IMAGES,
    PreparedRoomInput,
    RoomInputOptions,
    UploadErrorResponse,
    UploadLimits,
)
from roometry.uploads.service import UploadService
from roometry.uploads.session import COOKIE_NAME, check_origin, session

router = APIRouter(prefix="/api/rooms", tags=["room inputs"])
ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"model": UploadErrorResponse} for code in (400, 403, 404, 413, 415, 422, 503)
}


class CompleteMultipartParser(MultiPartParser):
    """python-multipart finalize alone does not require the closing boundary."""

    complete = False
    header_bytes = 0

    def on_part_begin(self) -> None:
        self.header_bytes = 0
        super().on_part_begin()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self.header_bytes += end - start
        if self.header_bytes > 8192:
            raise MultiPartException("Multipart headers too large.")
        super().on_header_field(data, start, end)

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self.header_bytes += end - start
        if self.header_bytes > 8192:
            raise MultiPartException("Multipart headers too large.")
        super().on_header_value(data, start, end)

    def on_end(self) -> None:
        self.complete = True

    async def parse(self) -> FormData:
        result = await super().parse()
        if not self.complete:
            await result.close()
            raise MultiPartException("Incomplete multipart data.")
        return result


def parse_options(raw: str) -> RoomInputOptions:
    def unique_object(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("Duplicate option key")
            obj[key] = value
        return obj

    def invalid_constant(_value):
        raise ValueError("Nonfinite JSON number")

    try:
        data = json.loads(
            raw, object_pairs_hook=unique_object, parse_constant=invalid_constant
        )
        return RoomInputOptions.model_validate(data)
    except (ValueError, ValidationError, RecursionError):
        raise UploadFailure(
            422, "invalid_options", "Check the room options, dimensions, and budget."
        ) from None


@router.get("/input-limits", response_model=UploadLimits)
async def input_limits(request: Request) -> UploadLimits:
    return cast(Settings, request.app.state.settings).public_upload_limits()


@router.post(
    "/inputs",
    response_model=PreparedRoomInput,
    status_code=201,
    responses=ERRORS,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["images", "options"],
                        "additionalProperties": False,
                        "properties": {
                            "images": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": MAX_IMAGES,
                                "items": {"type": "string", "format": "binary"},
                            },
                            "options": {
                                "type": "string",
                                "description": "JSON-encoded RoomInputOptions",
                            },
                        },
                    }
                }
            },
        }
    },
)
async def prepare_input(request: Request, response: Response) -> PreparedRoomInput:
    settings = cast(Settings, request.app.state.settings)
    service = cast(UploadService, request.app.state.upload_service)
    check_origin(request, settings)
    if len(request.headers.get("content-type", "")) > 512:
        raise UploadFailure(400, "invalid_multipart", "Invalid multipart content type.")
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "multipart/form-data"
    ):
        raise UploadFailure(
            415,
            "unsupported_image_type",
            "Send photos and options as multipart form data.",
        )
    try:
        form = await CompleteMultipartParser(
            request.headers,
            request.stream(),
            max_files=service.limits.max_images,
            max_fields=1,
            max_part_size=service.limits.options_max_bytes,
        ).parse()
    except MultiPartException as error:
        if error.message.startswith("Too many files"):
            raise UploadFailure(
                422,
                "invalid_image_count",
                f"Choose between 1 and {service.limits.max_images} photos.",
            ) from None
        if error.message.startswith("Part exceeded"):
            raise UploadFailure(
                413, "request_too_large", "The room options exceed the allowed size."
            ) from None
        raise UploadFailure(
            400,
            "invalid_multipart",
            "The upload form is incomplete or contains unexpected fields.",
        ) from None
    except (ClientDisconnect, ValueError):
        raise UploadFailure(
            400, "invalid_multipart", "The upload was interrupted or malformed."
        ) from None
    try:
        files: list[UploadFile] = []
        raw_options: list[str] = []
        for key, value in form.multi_items():
            if key == "images" and isinstance(value, UploadFile):
                files.append(value)
            elif key == "options" and isinstance(value, str):
                raw_options.append(value)
            else:
                raise UploadFailure(
                    400,
                    "unexpected_field",
                    "Send only photos and a single room-options field.",
                )
        if not 1 <= len(files) <= service.limits.max_images:
            raise UploadFailure(
                422,
                "invalid_image_count",
                f"Choose between 1 and {service.limits.max_images} photos.",
            )
        if len(raw_options) != 1:
            raise UploadFailure(
                422, "invalid_options", "Include one room-options JSON field."
            )
        if len(raw_options[0].encode("utf-8")) > service.limits.options_max_bytes:
            raise UploadFailure(
                413, "request_too_large", "The room options exceed the allowed size."
            )
        options = parse_options(raw_options[0])
        token, owner = session(request)
        result = await service.prepare(files, options, owner)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=service.limits.ttl_seconds,
            httponly=True,
            secure=settings.app_environment == "production",
            samesite="lax",
            path="/api/rooms",
        )
        response.headers["Cache-Control"] = "no-store"
        return result
    finally:
        with anyio.CancelScope(shield=True):
            await form.close()


@router.delete("/inputs/{input_id}", status_code=204, responses=ERRORS)
async def delete_input(input_id: str, request: Request) -> Response:
    check_origin(request, cast(Settings, request.app.state.settings))
    _, owner = session(request)
    await cast(UploadService, request.app.state.upload_service).delete(input_id, owner)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
