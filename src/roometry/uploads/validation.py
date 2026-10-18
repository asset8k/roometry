"""Bounded still-photo decoding and fresh metadata-free JPEG output."""

import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
from typing import cast
from uuid import uuid4

import pillow_heif
from PIL import Image, ImageOps, UnidentifiedImageError

from roometry.uploads.errors import UploadFailure
from roometry.uploads.schemas import (
    PreparedImage,
    SourceMediaType,
    UploadErrorCode,
    UploadLimits,
)

FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "AVIF": "image/avif",
}
EXTENSIONS = {
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png": {"image/png"},
    ".webp": {"image/webp"},
    ".avif": {"image/avif"},
    ".heic": {"image/heic", "image/heif"},
    ".heif": {"image/heic", "image/heif"},
}


def initialize_decoders() -> None:
    pillow_heif.register_heif_opener(thumbnails=False, depth_images=False)
    Image.init()
    if not {"JPEG", "PNG", "WEBP", "HEIF", "AVIF"}.issubset(Image.OPEN):
        raise RuntimeError("Required photo decoders are unavailable.")


@dataclass(frozen=True)
class NormalizedPhoto:
    metadata: PreparedImage
    jpeg: bytes


def normalize_photo(
    data: bytes,
    filename: str,
    content_type: str | None,
    limits: UploadLimits,
    index: int,
) -> NormalizedPhoto:
    def fail(status: int, code: UploadErrorCode, message: str) -> UploadFailure:
        return UploadFailure(status, code, message, file_index=index)

    if not data:
        raise fail(422, "invalid_image", "This photo is empty.")
    if len(data) > limits.max_file_bytes:
        raise fail(413, "file_too_large", "This photo exceeds the file-size limit.")
    if (
        not filename
        or len(filename) > 255
        or any(char in filename for char in ("/", "\\", "\0"))
    ):
        raise fail(422, "invalid_image", "Use a photo with a valid filename.")
    extension = PurePath(filename).suffix.lower()
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    if extension not in EXTENSIONS or declared not in {
        "",
        "application/octet-stream",
        *limits.allowed_media_types,
    }:
        raise fail(
            415,
            "unsupported_image_type",
            "Use a JPEG, PNG, WebP, HEIC, HEIF, or AVIF photo.",
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                detected = (
                    pillow_heif.get_file_mimetype(data)
                    if image.format == "HEIF"
                    else FORMATS.get(image.format or "")
                )
                if detected not in limits.allowed_media_types:
                    raise fail(
                        415,
                        "unsupported_image_type",
                        "The photo content is not a supported still-image format.",
                    )
                family = (
                    {"image/heic", "image/heif"}
                    if detected in {"image/heic", "image/heif"}
                    else {detected}
                )
                if detected not in EXTENSIONS[extension] or (
                    declared not in {"", "application/octet-stream"}
                    and declared not in family
                ):
                    raise fail(
                        415,
                        "unsupported_image_type",
                        "The photo content does not match its file type.",
                    )
                width, height = image.size
                if (
                    min(width, height) < limits.min_edge_px
                    or max(width, height) > limits.max_edge_px
                    or width * height > limits.max_pixels
                ):
                    raise fail(
                        422,
                        "image_resolution",
                        "This photo is outside the allowed resolution limits.",
                    )
                if getattr(image, "n_frames", 1) != 1:
                    raise fail(
                        422,
                        "animated_image",
                        "Upload a single still photo rather than an animation or image sequence.",
                    )
                image.verify()
            # verify() is not a pixel decode. Reopen and load fully to detect
            # truncated streams before accepting anything into private storage.
            with Image.open(BytesIO(data)) as image:
                image.load()
                ImageOps.exif_transpose(image, in_place=True)
                image.thumbnail(
                    (limits.prepared_max_edge_px, limits.prepared_max_edge_px),
                    Image.Resampling.LANCZOS,
                )
                with (
                    image.convert("RGBA") as rgba,
                    Image.new("RGB", image.size, "white") as clean,
                ):
                    with rgba.getchannel("A") as alpha:
                        clean.paste(rgba, mask=alpha)
                    # A new image has no source EXIF/XMP/ICC/comments to inherit.
                    output = BytesIO()
                    clean.save(output, "JPEG", quality=85, exif=b"", icc_profile=None)
                    jpeg = output.getvalue()
                    prepared_width, prepared_height = clean.size
    except UploadFailure:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise fail(
            422, "image_resolution", "This photo exceeds safe decoding limits."
        ) from None
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        EOFError,
        RuntimeError,
    ):
        raise fail(
            422,
            "invalid_image",
            "This photo could not be decoded. Try another image or export it again.",
        ) from None
    if len(jpeg) > limits.prepared_max_bytes:
        raise fail(
            413,
            "prepared_image_too_large",
            "The prepared photo exceeds the image-size limit.",
        )
    return NormalizedPhoto(
        PreparedImage(
            image_id=str(uuid4()),
            original_media_type=cast(SourceMediaType, detected),
            original_bytes=len(data),
            original_width_px=width,
            original_height_px=height,
            prepared_media_type="image/jpeg",
            prepared_bytes=len(jpeg),
            prepared_width_px=prepared_width,
            prepared_height_px=prepared_height,
        ),
        jpeg,
    )
