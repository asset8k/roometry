from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class AuthModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(AuthModel):
    username: Annotated[
        str, Field(min_length=3, max_length=32, pattern=r"^[a-z0-9_]+$")
    ]
    password: SecretStr

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def bounded_password(cls, value):
        if not 12 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Use a password of 12–128 characters.")
        return value


class UserResponse(AuthModel):
    id: UUID
    username: str


class AuthResponse(AuthModel):
    user: UserResponse
    expires_at: datetime


class SessionResponse(AuthModel):
    user: UserResponse | None
    expires_at: datetime | None


class LogoutRequest(AuthModel):
    pass
