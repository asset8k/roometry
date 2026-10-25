# Accounts — milestone step 1

Username/password registration, sign-in, session restoration and sign-out are connected to the existing account dialog. The guest workspace remains mounted and usable without an account.

## Local setup

Install the locked dependencies with `uv sync`. Set these private values in the repository-root `.env`:

```dotenv
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:5432/<database>
JWT_SECRET_KEY=<random-secret-at-least-32-bytes>
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
AUTH_COOKIE_SECURE=false
AUTH_COOKIE_SAMESITE=lax
AUTH_ATTEMPTS_PER_MINUTE=10
```

Generate a signing secret with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`; keep the result private and stable across backend restarts. Changing it invalidates existing sessions. Never commit `.env`.

Run schema migrations explicitly before using accounts:

```sh
uv run alembic upgrade head
uv run alembic check
```

Startup does not create tables or connect to the database. Missing auth configuration or an unavailable database returns a safe account error; guest uploads and health checks remain independent.

### This workspace's development PostgreSQL

Step 1 created an isolated PostgreSQL 18 cluster at `.local/postgres-auth`, an ignored directory. It uses the private Unix socket `/private/tmp/roometry-auth-pg-socket` on port 55432, with no TCP listener. The existing `.env` points to its `roometry` database using the restricted `roometry_app` role. Migrations have already been applied.

After a machine restart, start it from the repository root:

```sh
mkdir -p /private/tmp/roometry-auth-pg-socket
chmod 700 /private/tmp/roometry-auth-pg-socket
/opt/homebrew/opt/postgresql@18/bin/pg_ctl \
  -D .local/postgres-auth -l .local/postgres-auth/server.log \
  -o "-k /private/tmp/roometry-auth-pg-socket -p 55432 -h ''" start
```

This cluster uses local trust authentication inside the private socket directory, for this user's development machine only. Use a properly authenticated hosted PostgreSQL URL for deployment. No hosted database has been created or changed.

## API and session behavior

| Endpoint | Behavior |
| --- | --- |
| `POST /api/auth/register` | Username and password → user and expiry; creates a session. |
| `POST /api/auth/login` | Username and password → user and expiry; rotates the current session. |
| `GET /api/auth/me` | Restores the current user, or returns an anonymous session when no cookie exists. |
| `POST /api/auth/logout` | Empty JSON object → 204; revokes the session and clears its cookie. |

Usernames are canonical lowercase ASCII letters, digits and underscores, 3–32 characters. Passwords accept 12–128 characters and are hashed using Argon2id. Duplicate usernames are enforced transactionally by the database.

The browser receives an HttpOnly, host-only `roometry_auth` cookie scoped to `/api`. JavaScript receives the public user profile and expiry, never the JWT. JWTs require a valid signature, issuer, audience, subject and expiry; the database also checks that the corresponding session is active. Only a digest of the session identifier is stored. Logging out invalidates replay of that token. Sessions expire without an automatic refresh token.

Credential requests have a 4 KiB body limit, origin checks, sanitized errors and a configurable per-IP attempt limit. Hashing runs outside the event loop with bounded concurrency. The attempt limit is process-local; a shared production limiter belongs with the usage-limit milestone.

For HTTPS deployment, set `AUTH_COOKIE_SECURE=true` and list exact frontend `CORS_ORIGINS`. A same-origin `/api` proxy is preferred. A cross-site frontend/API arrangement requires `AUTH_COOKIE_SAMESITE=none`, secure cookies, and browser support for those cookies; this has not been deployment-tested.

## Verification and scope

`uv run pytest tests/test_auth.py -q` covers password hashing, duplicate registrations, expired/tampered tokens, disabled users, logout replay, restart restoration, request validation, origin checks, throttling and migration upgrade/downgrade. Fast isolated tests use migrated SQLite databases; browser verification uses the local PostgreSQL database.

The browser flow was checked for registration, wrong-password feedback, sign-in, sign-out, preserved room drafts, and session restoration after a backend restart and page reload. No model calls are needed.

Saved rooms, conversations, scene versions, private photo persistence, durable graph checkpoints, project ownership checks, guest-to-account imports and public sharing are **not part of step 1**. Authentication alone does not save a room. The UI identifies those unavailable features explicitly. Existing temporary upload ownership is unchanged.
