"""Public room-input contracts, separate from private storage and scene data."""

from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from roometry.scenes.schemas import MAX_ROOM_HEIGHT_M, MAX_ROOM_SIDE_M, RoomType

MAX_IMAGES = 10
MAX_DESCRIPTION_CHARS = 2000
MAX_GOAL_TEXT_CHARS = 2000
MAX_CONSTRAINTS_CHARS = 1000
MAX_STYLE_CHARS = 100
MAX_BUDGET_AMOUNT = 1_000_000
MIB = 1024 * 1024

Goal = Literal[
    "general_analysis", "more_storage", "workspace", "more_open_space", "better_layout"
]
# Source formats; the upload decoder normalizes all accepted photos to JPEG.
SourceMediaType = Literal[
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "image/avif"
]
SourceExtension = Literal[".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif"]
UploadErrorCode = Literal[
    "invalid_multipart",
    "unexpected_field",
    "invalid_options",
    "invalid_image_count",
    "file_too_large",
    "total_files_too_large",
    "request_too_large",
    "unsupported_image_type",
    "invalid_image",
    "animated_image",
    "image_resolution",
    "prepared_image_too_large",
    "input_not_found",
    "storage_unavailable",
    "forbidden_origin",
]
OpaqueId = Annotated[
    str,
    Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    ),
]
PositiveInt = Annotated[int, Field(gt=0)]
Side = Annotated[float, Field(gt=0, le=MAX_ROOM_SIDE_M, allow_inf_nan=False)]
Height = Annotated[float, Field(gt=0, le=MAX_ROOM_HEIGHT_M, allow_inf_nan=False)]


class InputModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, revalidate_instances="always"
    )


class InputDimensions(InputModel):
    """Partial user measurements; omission never manufactures scene dimensions."""

    width_m: Side | None = None
    length_m: Side | None = None
    height_m: Height | None = None
    measurements_source: Literal["estimated", "user_confirmed"] = "estimated"

    @model_validator(mode="after")
    def confirmation_has_measurement(self) -> Self:
        if self.measurements_source == "user_confirmed" and all(
            value is None for value in (self.width_m, self.length_m, self.height_m)
        ):
            raise ValueError(
                "At least one dimension is required to confirm measurements."
            )
        return self


class InputBudget(InputModel):
    amount: Annotated[float, Field(ge=0, le=MAX_BUDGET_AMOUNT, allow_inf_nan=False)]
    # Syntax only: no exchange-rate lookup or claim that every three-letter code exists.
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class RoomInputOptions(InputModel):
    room_type: RoomType | None = None
    description: Annotated[str, Field(max_length=MAX_DESCRIPTION_CHARS)] | None = None
    goals: Annotated[list[Goal], Field(max_length=5)] = Field(
        default_factory=lambda: ["general_analysis"]
    )
    goal_text: Annotated[str, Field(max_length=MAX_GOAL_TEXT_CHARS)] | None = None
    dimensions: InputDimensions | None = None
    style: Annotated[str, Field(max_length=MAX_STYLE_CHARS)] | None = None
    budget: InputBudget | None = None
    constraints: Annotated[str, Field(max_length=MAX_CONSTRAINTS_CHARS)] | None = None

    @field_validator("description", "goal_text", "style", "constraints", mode="before")
    @classmethod
    def trim_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("goals")
    @classmethod
    def normalize_goals(cls, goals: list[Goal]) -> list[Goal]:
        if len(goals) != len(set(goals)):
            raise ValueError("Goal choices must be unique.")
        return goals or ["general_analysis"]


class UploadLimits(InputModel):
    """Public policy DTO. Upload handlers enforce the active settings."""

    min_images: Literal[1] = 1
    max_images: Annotated[int, Field(ge=1, le=MAX_IMAGES)]
    allowed_media_types: list[SourceMediaType] = Field(
        default_factory=lambda: [
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/heic",
            "image/heif",
            "image/avif",
        ]
    )
    allowed_extensions: list[SourceExtension] = Field(
        default_factory=lambda: [
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".heic",
            ".heif",
            ".avif",
        ]
    )
    max_file_bytes: PositiveInt
    max_total_file_bytes: PositiveInt
    max_body_bytes: PositiveInt
    min_edge_px: PositiveInt
    max_edge_px: PositiveInt
    max_pixels: PositiveInt
    prepared_max_edge_px: PositiveInt
    prepared_max_bytes: PositiveInt
    options_max_bytes: Literal[16384] = 16384
    ttl_seconds: PositiveInt
    max_description_chars: Literal[2000] = MAX_DESCRIPTION_CHARS
    max_goal_text_chars: Literal[2000] = MAX_GOAL_TEXT_CHARS
    max_constraints_chars: Literal[1000] = MAX_CONSTRAINTS_CHARS
    max_style_chars: Literal[100] = MAX_STYLE_CHARS
    max_budget_amount: Literal[1000000] = MAX_BUDGET_AMOUNT
    max_room_side_m: float = MAX_ROOM_SIDE_M
    max_room_height_m: float = MAX_ROOM_HEIGHT_M


class PreparedImage(InputModel):
    """Safe public metadata; originals may rotate before prepared dimensions are measured."""

    image_id: OpaqueId
    original_media_type: SourceMediaType
    original_bytes: PositiveInt
    original_width_px: PositiveInt
    original_height_px: PositiveInt
    prepared_media_type: Literal["image/jpeg"]
    prepared_bytes: PositiveInt
    prepared_width_px: PositiveInt
    prepared_height_px: PositiveInt


class PreparedRoomInput(InputModel):
    input_id: OpaqueId
    status: Literal["ready"]
    options: RoomInputOptions
    images: Annotated[list[PreparedImage], Field(min_length=1, max_length=MAX_IMAGES)]
    expires_at: AwareDatetime

    @field_validator("images")
    @classmethod
    def unique_image_ids(cls, images: list[PreparedImage]) -> list[PreparedImage]:
        if len({image.image_id for image in images}) != len(images):
            raise ValueError("Image IDs must be unique.")
        return images


class UploadIssue(InputModel):
    code: UploadErrorCode
    message: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
    file_index: Annotated[int, Field(ge=0)] | None = None
    field: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class UploadErrorResponse(InputModel):
    detail: Annotated[list[UploadIssue], Field(min_length=1, max_length=16)]
