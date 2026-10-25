"""Database-backed sessions; authentication never imports or persists guest rooms."""

from datetime import UTC, datetime
from uuid import uuid4

import jwt
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from roometry.auth.errors import AuthFailure
from roometry.auth.schemas import AuthResponse, UserResponse
from roometry.auth.security import (
    decode_token,
    hash_password,
    issue_token,
    verify_password,
)
from roometry.users.model import AuthSession, User


class AuthService:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings

    def available(self):
        if self.database is None or self.settings.jwt_secret_key is None:
            raise AuthFailure(503, "auth_unavailable", "Sign-in is not configured yet.")

    @property
    def secret(self):
        return self.settings.jwt_secret_key.get_secret_value()

    def _claims(self, token):
        try:
            return decode_token(token, self.secret)
        except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
            raise AuthFailure(
                401, "invalid_session", "Your session expired. Please sign in again."
            ) from None

    def _issue(self, db, user, previous):
        now = datetime.now(UTC)
        db.execute(delete(AuthSession).where(AuthSession.expires_at <= now))
        if previous:
            try:
                _, old_hash = self._claims(previous)
                old = db.get(AuthSession, old_hash)
                if old:
                    old.revoked_at = now
            except AuthFailure:
                pass
        token, digest, expires = issue_token(
            user.id, self.secret, self.settings.access_token_expire_minutes * 60
        )
        db.add(
            AuthSession(
                token_hash=digest, user_id=user.id, created_at=now, expires_at=expires
            )
        )
        return token, AuthResponse(
            user=UserResponse(id=user.id, username=user.username), expires_at=expires
        )

    def register(self, credentials, previous=None):
        self.available()
        hashed = hash_password(credentials.password.get_secret_value())
        try:
            with self.database.sessions.begin() as db:
                user = User(
                    id=uuid4(),
                    username=credentials.username,
                    password_hash=hashed,
                    is_active=True,
                    created_at=datetime.now(UTC),
                )
                db.add(user)
                db.flush()  # Enforce the unique username and insert the parent before its session.
                return self._issue(db, user, previous)
        except IntegrityError:
            raise AuthFailure(
                409, "username_unavailable", "That username is unavailable."
            ) from None
        except SQLAlchemyError:
            raise AuthFailure(
                503,
                "auth_unavailable",
                "Sign-in is temporarily unavailable. Please try again.",
            ) from None

    def login(self, credentials, previous=None):
        self.available()
        try:
            with self.database.sessions.begin() as db:
                user = db.scalar(
                    select(User).where(User.username == credentials.username)
                )
                valid = verify_password(
                    credentials.password.get_secret_value(),
                    user.password_hash if user else None,
                )
                if not valid or not user or not user.is_active:
                    raise AuthFailure(
                        401, "invalid_credentials", "Username or password is incorrect."
                    )
                return self._issue(db, user, previous)
        except SQLAlchemyError:
            raise AuthFailure(
                503,
                "auth_unavailable",
                "Sign-in is temporarily unavailable. Please try again.",
            ) from None

    def current(self, token):
        self.available()
        user_id, digest = self._claims(token)
        try:
            with self.database.sessions() as db:
                record = db.scalar(
                    select(AuthSession).where(
                        AuthSession.token_hash == digest,
                        AuthSession.user_id == user_id,
                        AuthSession.revoked_at.is_(None),
                        AuthSession.expires_at > datetime.now(UTC),
                    )
                )
                user = db.get(User, user_id) if record else None
                if not user or not user.is_active:
                    raise AuthFailure(
                        401,
                        "invalid_session",
                        "Your session expired. Please sign in again.",
                    )
                expires = record.expires_at
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=UTC)
                return AuthResponse(
                    user=UserResponse(id=user.id, username=user.username),
                    expires_at=expires,
                )
        except SQLAlchemyError:
            raise AuthFailure(
                503,
                "auth_unavailable",
                "Sign-in is temporarily unavailable. Please try again.",
            ) from None

    def logout(self, token):
        self.available()
        try:
            _, digest = self._claims(token)
        except AuthFailure:
            return
        try:
            with self.database.sessions.begin() as db:
                record = db.get(AuthSession, digest)
                if record:
                    record.revoked_at = datetime.now(UTC)
        except SQLAlchemyError:
            raise AuthFailure(
                503, "auth_unavailable", "Couldn’t sign out. Please try again."
            ) from None
