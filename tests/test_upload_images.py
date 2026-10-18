"""Real codec checks for oriented, metadata-free private photo derivatives."""

from io import BytesIO

from PIL import Image
from PIL.PngImagePlugin import PngInfo
import pytest

from roometry.config import Settings
from roometry.uploads.errors import UploadFailure
from roometry.uploads.validation import initialize_decoders, normalize_photo


@pytest.fixture(scope="module", autouse=True)
def codecs():
    initialize_decoders()


@pytest.fixture
def limits():
    return Settings(_env_file=None, upload_min_edge_px=16, upload_prepared_max_edge_px=128).public_upload_limits()


def encoded(format="PNG", mode="RGB", size=(320, 160), **kwargs):
    output = BytesIO()
    with Image.new(mode, size) as image:
        image.save(output, format, **kwargs)
    return output.getvalue()


@pytest.mark.parametrize("format,extension,media_type", [
    ("JPEG", ".jpg", "image/jpeg"), ("PNG", ".png", "image/png"),
    ("WEBP", ".webp", "image/webp"), ("HEIF", ".heic", "image/heic"),
    ("HEIF", ".heif", "image/heif"), ("AVIF", ".avif", "image/avif"),
])
def test_real_decoders_normalize_all_advertised_formats(limits, format, extension, media_type):
    source = encoded(format)
    result = normalize_photo(source, "room" + extension, media_type, limits, 2)
    assert result.metadata.original_bytes == len(source)
    assert result.metadata.original_media_type in ({"image/heic", "image/heif"} if format == "HEIF" else {media_type})
    assert result.metadata.prepared_bytes == len(result.jpeg)
    with Image.open(BytesIO(result.jpeg)) as image:
        image.load()
        assert image.format == "JPEG" and image.mode == "RGB" and image.size == (128, 64)
        assert not image.getexif()
        assert "icc_profile" not in image.info and "xmp" not in image.info


def test_orientation_is_applied_and_exif_removed(limits):
    exif = Image.Exif()
    exif[274], exif[270] = 6, "Private room description"
    result = normalize_photo(encoded("JPEG", exif=exif), "room.jpg", "image/jpeg", limits, 0)
    assert (result.metadata.original_width_px, result.metadata.original_height_px) == (320, 160)
    assert (result.metadata.prepared_width_px, result.metadata.prepared_height_px) == (64, 128)
    assert b"Private room description" not in result.jpeg
    with Image.open(BytesIO(result.jpeg)) as image:
        assert not image.getexif()


def test_transparency_is_white_and_png_metadata_does_not_survive(limits):
    info = PngInfo()
    info.add_text("Description", "Private address")
    info.add_text("XML:com.adobe.xmp", "Private GPS XMP")
    result = normalize_photo(encoded(mode="RGBA", pnginfo=info), "room.png", "image/png", limits, 0)
    assert b"Private" not in result.jpeg
    with Image.open(BytesIO(result.jpeg)) as image:
        assert min(image.getpixel((0, 0))) >= 250


@pytest.mark.parametrize("mode,format,extension", [("L", "PNG", ".png"), ("CMYK", "JPEG", ".jpg"), ("P", "PNG", ".png")])
def test_modes_and_small_images_normalize_without_upscaling(limits, mode, format, extension):
    result = normalize_photo(encoded(format, mode, (32, 16)), "room" + extension, None, limits, 0)
    with Image.open(BytesIO(result.jpeg)) as image:
        assert image.mode == "RGB" and image.size == (32, 16)


@pytest.mark.parametrize("filename,mime,code", [
    ("room.JPG", "application/octet-stream", None), ("room.jpeg", None, None),
    ("room.png", "image/jpeg", "unsupported_image_type"),
    ("room.jpg", "image/png", "unsupported_image_type"),
    ("room.svg", "image/jpeg", "unsupported_image_type"),
    ("../room.jpg", "image/jpeg", "invalid_image"),
    ("room.jpg", "video/quicktime", "unsupported_image_type"),
])
def test_content_and_file_metadata_must_agree(limits, filename, mime, code):
    if code is None:
        assert normalize_photo(encoded("JPEG"), filename, mime, limits, 0).metadata.original_media_type == "image/jpeg"
    else:
        with pytest.raises(UploadFailure) as error:
            normalize_photo(encoded("JPEG"), filename, mime, limits, 3)
        assert error.value.response.detail[0].code == code
        assert error.value.response.detail[0].file_index == 3


@pytest.mark.parametrize("data", [b"", b"not a photo", b"\xff\xd8\xff", encoded("JPEG")[:-100], encoded("PNG")[:-20]])
def test_empty_corrupt_and_truncated_streams_fail(limits, data):
    with pytest.raises(UploadFailure) as error:
        normalize_photo(data, "room.jpg" if data.startswith(b"\xff\xd8") else "room.png", None, limits, 0)
    assert error.value.response.detail[0].code == "invalid_image"


def test_animation_is_rejected(limits):
    buffer = BytesIO()
    with Image.new("RGB", (32, 32), "red") as first, Image.new("RGB", (32, 32), "blue") as second:
        first.save(buffer, "PNG", save_all=True, append_images=[second], duration=100)
    with pytest.raises(UploadFailure) as error:
        normalize_photo(buffer.getvalue(), "room.png", "image/png", limits, 0)
    assert error.value.response.detail[0].code == "animated_image"


@pytest.mark.parametrize("changes,code", [
    ({"max_pixels": 1000}, "image_resolution"), ({"max_edge_px": 100}, "image_resolution"),
    ({"min_edge_px": 200}, "image_resolution"), ({"prepared_max_bytes": 1}, "prepared_image_too_large"),
])
def test_pixel_and_prepared_byte_limits(limits, changes, code):
    with pytest.raises(UploadFailure) as error:
        normalize_photo(encoded(), "room.png", "image/png", limits.model_copy(update=changes), 0)
    assert error.value.response.detail[0].code == code


def test_decompression_bomb_is_safe_error(limits, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 200)
    with pytest.raises(UploadFailure) as error:
        normalize_photo(encoded(size=(32, 16)), "room.png", "image/png", limits, 0)
    assert error.value.response.detail[0].code == "image_resolution"
